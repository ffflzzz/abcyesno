# -*- coding: utf-8 -*-
"""Provider adapters: image (stills) + video (image-to-video).

新架构的关键：静帧是**生产输入**（首帧），不是一次性质检产物。
两个能力因此成为硬要求：
  1) 生图必须能带参考图（人物/道具身份）→ 身份通过静帧带进视频
  2) 生视频必须能吃 first_frame/last_frame（图生视频）→ 构图由分镜决定

只依赖 httpx（LangChain 不管媒体协议）。

★ **所有对 agnes 的 httpx 客户端都用 `trust_env=False`（直连，不走环境代理）**。
2026-09-13 事故：本文件与 cast/stills/video 共 7 处客户端默认读 `HTTP_PROXY`，
而沙箱注入了 `HTTP_PROXY=http://127.0.0.1:13431`（透明代理）。API 调用侥幸没被挡
（`NO_PROXY=agnes-ai.com` 只豁免了 API 域名），但**输出 CDN 是另一个域名
`platform-outputs.agnes-ai.space`** —— 它不在豁免列表里，于是下载视频时走代理
→ `httpx.ProxyError: 502 Bad Gateway` → **整条媒体链崩掉**（46 镜项目已跑完的
40 张静帧 + 7 个视频全废）。
agnes 是国内可直连的服务，代理只会引入这种偶发故障，故彻底断开环境代理，
不再依赖"记得把新域名加进 NO_PROXY"这种脆弱约定。
"""
from __future__ import annotations

import httpx

from .. import config
from .. import vendors


class RateLimitError(Exception):
    """Provider 429."""


class QueueFullError(Exception):
    """Provider 队列满（HTTP 503 / video_queue_full）——**瞬时**，退避后可重试。

    与 429 的区别：429 是配额/限速（要等更久），队列满是"当前排队太挤"，
    通常几十秒到两分钟就能进。实测（2026-09-10）：批量重拍时反复 503，
    原实现当成永久失败 → 该镜直接缺席成片。
    """


def _auth(key: str | None = None) -> dict:
    """鉴权头。**多 key 的唯一注入点**（2026-09-16）。

    `key` 缺省 = `config.AGNES_API_KEY`（即 key 池第一条）→ **单 key 行为逐字节不变**。
    收敛成一处的理由：本项目历史上就是因为"同一件事写了两份"漂移出过事故
    （`qc.HARD_KEYS` 两份实现少了 10 个词）；三个调用点各写一次 Bearer 就是三个
    漏改点。
    """
    return {"Authorization": "Bearer " + (key or config.AGNES_API_KEY),
            "Content-Type": "application/json"}


def _bearer(key: str | None = None) -> dict:
    """只要 Authorization 的端点（查询不需要 Content-Type）。"""
    return {"Authorization": "Bearer " + (key or config.AGNES_API_KEY)}


#: 503 响应体里属于"容量类"（可退避重试）的关键词。
_CAPACITY_HINTS = ("queue_full", "queue is full", "overload", "busy", "capacity",
                   "rate_limit", "too many")


def _is_capacity_503(r) -> bool:
    """503 是"排队太挤"（可重试）还是"路由/配置错误"（重试无用）？

    背景（2026-09-16 实测）：`POST /v1/videos` 用**不存在的 model** 时，
    Agnes 返回的**也是 503**，体为
    `{"error":{"code":"model_not_found","message":"No available channel for model ..."}}`。
    旧实现 `if status==503 or "queue_full" in text:` 把**任何** 503 当队列满 ⇒
    模型名写错会白退避 `VIDEO_QUEUE_RETRIES` 轮（≈20+40+60+80+100 秒）并打出
    **误导性的**「队列持续满」—— 属于"失败不可见"那一类，本项目最忌讳。

    判据：带明确 `error.code` 且**不是**容量类 ⇒ 判死；拿不到线索时**按容量处理**
    （保守方向：宁可多退避几次，也别把真·队列满判死 —— 那会直接丢镜）。
    """
    try:
        d = r.json() or {}
    except Exception:  # noqa: BLE001
        return True
    err = d.get("error")
    code = msg = ""
    if isinstance(err, dict):
        code = str(err.get("code") or "")
        msg = str(err.get("message") or "")
    elif err:
        msg = str(err)
    blob = (code + " " + msg + " " + r.text[:200]).lower()
    if any(h in blob for h in _CAPACITY_HINTS):
        return True
    return not code          # 有明确错误码又不是容量类 → 不可重试


# ─── Image ───────────────────────────────────────────────────────────────────

def gen_image(prompt: str, refs: list[str] | None = None, ratio: str | None = None,
              timeout: int = 120, key: str | None = None) -> tuple[str, str]:
    """Generate a still. Returns (local_path_hint, public_url).

    refs: 参考图 URL/路径列表（人物三视图、资产图）——身份靠它们锁定。
    实际写盘由调用方完成（它知道项目目录）；这里只返回 url。
    key: 用哪条 key（缺省=池第一条）。多 key 并行生图时由调用方指定。

    ★ 2026-09-18：本函数是**厂商分发层**。厂商档 `impl` 非空 ⇒ 委派给该模块；
    否则走内置实现 `_builtin_gen_image`（= 改造前的原逻辑，**逐字保留**）。
    """
    impl = vendors.impl_for("image")
    if impl is not None:
        return impl.gen_image(prompt, refs=refs, ratio=ratio,
                              timeout=timeout, key=key)
    return _builtin_gen_image(prompt, refs=refs, ratio=ratio,
                              timeout=timeout, key=key)


def _builtin_gen_image(prompt: str, refs: list[str] | None = None,
                       ratio: str | None = None, timeout: int = 120,
                       key: str | None = None) -> tuple[str, str]:
    """内置（agnes 风格）生图 —— 改造前的原逻辑。

    唯一变化：厂商特有取值改为读 `vendors.spec_for("image")`
    （agnes 档的值与原字面量**逐字一致** ⇒ 缺省路径输出不变）。
    """
    spec = vendors.spec_for("image")
    body = {"model": config.MODELS["image"], "prompt": prompt,
            "size": config.IMAGE_SIZE or spec["image_size"]}
    # 比例字段名**允许为空**：OpenAI 原生生图用 `size` 表达尺寸、**没有**独立的 ratio 字段
    # （若强行设 `ratio_key="size"`，会与上面的 `size` 撞键、后者把它覆盖掉）。
    # agnes 档的 `ratio_key="ratio"` ⇒ 键序与原字面量一致：model, prompt, size, ratio, n。
    _rk = spec.get("ratio_key")
    if _rk:
        body[_rk] = ratio or config.STILL_RATIO
    body["n"] = 1
    if refs:
        # 参考图沿用 `extra_body.image`（Agnes 的扩展写法，多数 OpenAI 兼容聚合平台亦兼容）。
        # **参考图约定不同**的厂商请走 impl 模块 —— 见 vendors.py 的说明。
        body["extra_body"] = {"image": list(refs)[:spec["ref_max"]],
                              "response_format": "url"}
    with httpx.Client(timeout=timeout, trust_env=False) as c:  # 直连，见文件头
        r = c.post(vendors.base_for("image", key) + spec["image_path"],
                   headers=_auth(key), json=body)
        if r.status_code == 429:
            raise RateLimitError("image 429")
        r.raise_for_status()
        data = r.json().get("data") or [{}]
        url = data[0].get("url", "")
    if not url:
        raise RuntimeError("image api returned no url")
    return "", url


# ─── Video ───────────────────────────────────────────────────────────────────

def submit_video(prompt: str, *, first_frame: str | None = None,
                 last_frame: str | None = None, seconds: int = 10,
                 aspect_ratio: str | None = None, timeout: int = 60,
                 mode: str = "keyframe",
                 images: list[str] | None = None,
                 audios: list[str] | None = None,
                 key: str | None = None) -> dict:
    """★ 2026-09-18：**厂商分发层**。档里 `impl` 非空 ⇒ 委派给该模块；
    否则走内置实现 `_builtin_submit_video`（= 改造前的原逻辑，**逐字保留**）。

    厂商档需实现**同签名**函数（只接视频的厂商可以只实现本函数与 `query_video`）。
    """
    impl = vendors.impl_for("video")
    if impl is not None:
        return impl.submit_video(prompt, first_frame=first_frame,
                                 last_frame=last_frame, seconds=seconds,
                                 aspect_ratio=aspect_ratio, timeout=timeout,
                                 mode=mode, images=images, audios=audios, key=key)
    return _builtin_submit_video(
        prompt, first_frame=first_frame, last_frame=last_frame, seconds=seconds,
        aspect_ratio=aspect_ratio, timeout=timeout, mode=mode, images=images,
        audios=audios, key=key)


def _builtin_submit_video(prompt: str, *, first_frame: str | None = None,
                          last_frame: str | None = None, seconds: int = 10,
                          aspect_ratio: str | None = None, timeout: int = 60,
                          mode: str = "keyframe",
                          images: list[str] | None = None,
                          audios: list[str] | None = None,
                          key: str | None = None) -> dict:
    """内置（agnes 风格）图生视频 —— 改造前 `submit_video` 的原逻辑。

    图生视频：first_frame / last_frame 是**静帧 URL**（生产输入）。

    默认 `mode="keyframe"`（静帧先行架构的做法：身份在静帧阶段用参考图锁好，
    视频阶段只吃关键帧）。**两条硬规则（官方文档，非我们的取舍）**：
      · `keyframe` 必需 first_frame/last_frame 至少一个，**不允许** images/audios/videos
      · `reference` 必需 images/audios 至少一类，**不允许** first_frame/last_frame
    即两者**互斥**，同一请求无法混用。

    2026-09-13 新增 `mode`/`images`/`audios` 参数（默认值保持原行为不变）：
    官方示例（`@角色/@场景` 引用素材）走的是 **reference**，我们需要能对照实测，
    而不是靠"文档说 reference 可能重构图"这种定性描述下结论。
    用 reference 时注意：**没有首帧锁定**，构图只能靠 prompt 文本描述；
    换来的是可用 `audios`（音频参考，keyframe 下拿不到）。

    `key`（2026-09-16）：用哪条 key 提交（缺省=池第一条）。**注意 `query_video`
    必须用同一条 key** —— 除非探测证明 `video_id` 是账号维度（见
    `scripts/probe_multikey.py` 阶段 B），否则跨 key 查询会查不到。
    """
    mode = (mode or "keyframe").strip().lower()
    if mode == "keyframe":
        if not (first_frame or last_frame):
            raise ValueError("keyframe 模式需要 first_frame 和/或 last_frame")
    elif mode == "reference":
        if not (images or audios):
            raise ValueError("reference 模式需要 images 和/或 audios 至少一类非空")
    else:
        raise ValueError("不支持的 mode：%r（仅 text/keyframe/reference）" % mode)

    spec = vendors.spec_for("video")
    body = {"model": config.MODELS["video"], "prompt": prompt, "mode": mode,
            "size": spec["video_size"]}
    # 比例字段名同样**允许为空**（理由同生图：避免与 `size` 撞键）。
    _ak = spec.get("aspect_key")
    if _ak:
        body[_ak] = aspect_ratio or config.ASPECT_RATIO
    # API 硬约束 seconds ∈ [4, 12]（2026-09-10 实测 400：分镜写 3s 的镜被拒，
    # 整镜缺席 → 缺镜不拼接）。上限下限都要钳——短镜（快切节奏）同样会 400。
    # 下限取厂商档 `seconds_min`（agnes=4），上限取 config.VIDEO_MAX_SECONDS。
    body["seconds"] = str(max(spec["seconds_min"],
                              min(int(seconds), config.VIDEO_MAX_SECONDS)))
    if mode == "keyframe":
        if first_frame:
            body["first_frame"] = first_frame
        if last_frame:
            body["last_frame"] = last_frame
    else:   # reference
        if images:
            body["images"] = list(images)
        if audios:
            body["audios"] = list(audios)
    with httpx.Client(timeout=timeout, trust_env=False) as c:  # 直连，见文件头
        r = c.post(vendors.base_for("video", key) + spec["video_path"],
                   headers=_auth(key), json=body)
        if r.status_code == 429:
            raise RateLimitError("video 429")
        if r.status_code == 503 or "queue_full" in r.text:
            # 503 两种来源要分开（见 `_is_capacity_503`）：容量类才退避重试，
            # 路由/配置类（如 model_not_found）直接判死，别打出误导性的"队列满"。
            if r.status_code == 503 and not _is_capacity_503(r):
                raise RuntimeError("video submit http 503（非容量类，不重试）: %s"
                                   % r.text[:140])
            raise QueueFullError("video queue full: %s" % r.text[:100])
        if r.status_code >= 400:
            raise RuntimeError("video submit http %d: %s" % (r.status_code, r.text[:140]))
        d = r.json()
    return {"video_id": d.get("video_id"), "task_id": d.get("task_id") or d.get("id")}


def query_video(video_id: str, timeout: int = 30, key: str | None = None) -> dict:
    """★ 2026-09-18：**厂商分发层**（`impl` 非空则委派，否则走内置实现）。"""
    impl = vendors.impl_for("video")
    if impl is not None:
        return impl.query_video(video_id, timeout=timeout, key=key)
    return _builtin_query_video(video_id, timeout=timeout, key=key)


def _builtin_query_video(video_id: str, timeout: int = 30,
                         key: str | None = None) -> dict:
    """内置（agnes 风格）查询 —— 改造前 `query_video` 的原逻辑。"""
    spec = vendors.spec_for("video")
    with httpx.Client(timeout=timeout, trust_env=False) as c:  # 直连，见文件头
        r = c.get(vendors.base_for("video", key) + spec["query_path"],
                  params={"video_id": video_id,
                          spec["query_model_key"]: config.MODELS["video"]},
                  headers=_bearer(key))
        if r.status_code == 429:
            raise RateLimitError("video query 429")
        r.raise_for_status()
        d = r.json()
    meta = d.get("metadata") or {}
    err = d.get("error")
    return {"status": d.get("status"), "progress": d.get("progress"),
            "url": meta.get("url") or d.get("video_url") or d.get("url"),
            "error": (err.get("message") if isinstance(err, dict) else err)}
