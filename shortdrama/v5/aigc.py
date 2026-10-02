# -*- coding: utf-8 -*-
"""画布应用 → 本后端 → 模型服务 的**同源代理**。

为什么要这一层（而不是让画布直连模型）：
  画布自己直连的话，密钥要在浏览器里**再填一份**，而且绕过后端的
  密钥池轮换 / 429 冷却 / 配额记账 —— 生成的东西在日志和账本里都看不见。
  走这层之后：画布只跟同源后端说话，密钥只有 `.env` 那一份，
  `providers` 里那套多 key 与限速**照旧生效**（本模块不另写一份调用逻辑）。

对齐的是**协议形状**（OpenAI 兼容的 `{data:[{url}]}`），不是把供应商的
参数体系换掉 —— 生图仍然走 `providers.gen_image`，与媒体链同一条路。
"""
from __future__ import annotations

import json
import math
import time
from typing import Any

from . import config
from .media import providers

#: 生图官方支持的比例档位（2026-09-18 实测记录在 `config.py` 的注释里；
#: 比前端可选的 `RATIO_CHOICES` 多 2:3 与 3:2）
IMAGE_RATIOS = ("1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9")

#: 一次请求最多出几张。画布的"张数"选择器能填到 4，再高就是误触烧配额。
MAX_IMAGES_PER_REQUEST = 4


def _parse_ratio(value: str) -> float | None:
    """`"9:16"` / `"1024x1792"` → 宽高比浮点数；解析不出来给 None。"""
    s = str(value or "").strip().lower().replace("×", "x")
    for sep in (":", "x", "/"):
        if sep in s:
            a, _, b = s.partition(sep)
            try:
                w, h = float(a), float(b)
            except ValueError:
                return None
            return w / h if h else None
    return None


def ratio_from_size(size: Any, default: str | None = None) -> str:
    """把画布发来的 `size` 折到**官方比例档位**上。

    ★ 为什么必须折：实测 Agnes **忽略**像素串 size（画布发 `1024x1792`、
    后端发 `1K`+`ratio:9:16`，两边出图像素完全一样 736×1312）——
    照原样转发，用户在画布里选画幅就是**假开关**。
    折完之后，画布的比例选择器真的能改变出图。
    """
    d = default or config.STILL_RATIO
    if isinstance(size, str):
        exact = size.strip()
        if exact in IMAGE_RATIOS:
            return exact
        r = _parse_ratio(exact)
        if r and r > 0:
            # 按对数距离取最近档 —— 竖横屏之间的差距要能分辨，
            # 而 1:1 两侧要对称（用线性差会在 1:1 附近偏一边）
            return min(IMAGE_RATIOS, key=lambda x: abs(math.log(_parse_ratio(x) / r)))
    return d


def _prompt_text(payload: dict) -> str:
    """画布的 `prompt` 可能是字符串，也可能是多模态内容数组（取其中的文本段）。"""
    p = payload.get("prompt")
    if isinstance(p, str):
        return p.strip()
    if isinstance(p, list):
        parts = [c.get("text", "") for c in p if isinstance(c, dict) and c.get("type") == "text"]
        return " ".join(x for x in parts if x).strip()
    return ""


def _count(payload: dict) -> int:
    """要几张。解析不出来按 1 张，上限 `MAX_IMAGES_PER_REQUEST`（误触大数不该一次烧光配额）。"""
    try:
        n = int(payload.get("n") or 1)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, MAX_IMAGES_PER_REQUEST))


def generate_images(payload: dict) -> list[str]:
    """OpenAI 形状的请求 → 图片 URL 列表。真正出图仍走 `providers.gen_image`。

    抛 `providers.RateLimitError` / `RuntimeError` 由调用方转成 HTTP 错误 ——
    这里不吞异常：静默返回空列表会让画布显示"生成成功但没有图"。
    """
    prompt = _prompt_text(payload)
    if not prompt:
        raise ValueError("prompt 为空")
    ratio = ratio_from_size(payload.get("size"))
    refs = _refs_of(payload) or None
    urls: list[str] = []
    for _ in range(_count(payload)):
        _unused, url = gen_with_fallback(providers.gen_image, prompt, refs=refs, ratio=ratio)
        urls.append(url)
    return urls


def image_response(urls: list[str]) -> dict:
    """OpenAI 的 `/images/generations` 响应形状（画布按 `data[0].url` 取图）。"""
    return {"created": int(time.time()),
            "data": [{"url": u, "revised_prompt": ""} for u in urls]}


# ─────────────────────────────────── 密钥选用与限速兜底

def _pool() -> list[str]:
    return [k for k in (config.AGNES_API_KEYS or [config.AGNES_API_KEY]) if k]


def gen_with_fallback(fn, *args, **kw):
    """按池子里的 key 依次试；**撞限速就换下一条**，全用完才把错误抛出去。

    ★ 为什么代理要自己做这件事：媒体链那边是调用方显式传 key 来轮换的，
      `providers` 自己不换。代理若只传 `key=None`，就永远用池子第一条 ——
      那等于把"走后端就能用上多 key"这个卖点变成假话（本仓库最忌文档超前于代码）。
    """
    keys = _pool()
    last: Exception | None = None
    for k in keys:
        try:
            return fn(*args, key=k, **kw)
        except providers.RateLimitError as exc:
            last = exc
            continue
    raise last or RuntimeError("没有可用的 key")


# ─────────────────────────────────── 参考图改图

def _refs_of(payload: dict) -> list[str]:
    refs = (payload.get("image") or payload.get("reference_images")
            or payload.get("refs") or payload.get("input_reference") or [])
    if isinstance(refs, str):
        refs = [refs]
    return [r.strip() for r in refs if isinstance(r, str) and r.strip()]


def edit_images(payload: dict) -> list[str]:
    """带参考图的编辑。

    ⚠️ 请求形状是 **JSON + data URI**，不是 OpenAI 原生的 multipart 上传 ——
    本仓库的 venv **没装** multipart 解析库（`python-multipart`），FastAPI 收不了
    表单式文件上传；与其为省一个依赖自己手写 multipart 解析器（边界情况多），
    不如把 vendored 画布那侧改成发 data URI。
    ★ 实测供应商**收 data URI**：拿 1.4MB 静帧转 base64 塞进 `extra_body.image`，
    返回 200 且结果 URL 落在 `images/i2i/` 路径下（说明确实按图生图处理，不是当文生图糊弄）。
    """
    prompt = _prompt_text(payload)
    if not prompt:
        raise ValueError("prompt 为空")
    refs = _refs_of(payload)
    if not refs:
        raise ValueError("没有参考图 —— 编辑至少要一张（否则该走 /images/generations）")
    ratio = ratio_from_size(payload.get("size"))
    urls: list[str] = []
    for _ in range(_count(payload)):
        _unused, url = gen_with_fallback(providers.gen_image, prompt, refs=refs, ratio=ratio)
        urls.append(url)
    return urls


# ─────────────────────────────────── 文本

def _chat_messages(payload: dict) -> list[dict]:
    """把 OpenAI 的 messages 压成纯文本对话。

    ⚠️ 多模态内容数组里的**图片段被丢掉**：文本通道是语言模型，喂图进来只会报错，
    而画布的"看图说话"应该走生图/视觉那条路。丢掉的量记在响应里，不静默。
    """
    out, dropped = [], 0
    for m in payload.get("messages") or []:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, list):
            dropped += sum(1 for p in c if isinstance(p, dict) and p.get("type") != "text")
            c = " ".join(str(p.get("text", "")) for p in c
                         if isinstance(p, dict) and p.get("type") == "text").strip()
        c = str(c or "").strip()
        if c:
            out.append({"role": str(m.get("role") or "user"), "content": c})
    if not out:
        raise ValueError("messages 为空")
    if dropped:
        out[0]["content"] = "（注：本次请求里 %d 段图片未被使用）\n%s" % (dropped, out[0]["content"])
    return out


def chat_completion(payload: dict) -> dict:
    """完整回答。文本通道的**多 key 轮换在 `llm.chat_for` 里已经做了**，这里不重复实现。"""
    from . import llm
    try:
        max_tokens = int(payload.get("max_tokens") or 4096)
    except (TypeError, ValueError):
        max_tokens = 4096
    try:
        temp = float(payload.get("temperature"))
    except (TypeError, ValueError):
        temp = 0.1                                    # 创作类默认留多样性；评判类由调用方传 0
    r = llm.chat_for(max_tokens=max_tokens, temperature=temp).invoke(_chat_messages(payload))
    text = r.content if isinstance(r.content, str) else \
        " ".join(str(p.get("text", "")) for p in r.content if isinstance(p, dict))
    model = str(payload.get("model") or config.MODELS.get("chat") or "")
    return {"id": "chatcmpl-shortdrama-%d" % int(time.time()),
            "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def chat_sse(payload: dict) -> str:
    """按 SSE 格式吐给画布（它是流式读法）。

    ⚠️ 这是**一次性**的完整回答切成两个块，不是逐 token 流 —— 想要真流式得把
    `llm` 的 `astream` 接进来，而那会要求路由改成 async 生成器；先把功能接通。
    """
    d = chat_completion(payload)
    text = d["choices"][0]["message"]["content"]

    def _chunk(delta: dict, finish: str | None) -> str:
        body = {"id": d["id"], "object": "chat.completion.chunk", "created": d["created"],
                "model": d["model"],
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return "data: %s\n\n" % json.dumps(body, ensure_ascii=False)

    return _chunk({"role": "assistant", "content": text}, None) \
        + _chunk({}, "stop") + "data: [DONE]\n\n"


# ─────────────────────────────────── 视频

#: video_id → 提交时用的 key。**查询必须用同一条 key**（`providers.submit_video`
#: 的文档写死了这条：跨 key 查会查不到），所以提交结果要记下来。
_VIDEO_OWNER: dict[str, str] = {}
_VIDEO_OWNER_MAX = 200


def _remember_key(video_id: str, key: str) -> None:
    _VIDEO_OWNER[video_id] = key
    while len(_VIDEO_OWNER) > _VIDEO_OWNER_MAX:          # 防长跑进程无限涨
        _VIDEO_OWNER.pop(next(iter(_VIDEO_OWNER)))


def submit_video_task(payload: dict) -> dict:
    """画布的视频提交 → `providers.submit_video`。

    有参考图走 `reference` 档、没有就走 `text` 档（`keyframe` 要首帧，画布给不出）。
    秒数由 `providers` 双向钳到 [4, VIDEO_MAX_SECONDS] —— 官方硬约束，超了会 400。
    """
    prompt = _prompt_text(payload)
    if not prompt:
        raise ValueError("prompt 为空")
    refs = _refs_of(payload)
    if not refs:
        # 供应商只有 keyframe / reference 两种模式，**都要求带素材**（没有纯文字生视频这条路）。
        # 本项目本来就是静帧先行 ⇒ 画布上正确的用法是把某一镜的静帧挂成参考图再生成。
        raise ValueError("生成视频需要至少一张参考图：把某一镜的静帧挂到本节点上再生成"
                         "（供应商的 keyframe 要首帧、reference 要参考图，没有纯文字生视频）")
    try:
        seconds = int(float(payload.get("seconds") or 6))
    except (TypeError, ValueError):
        seconds = 6
    last: Exception | None = None
    for k in _pool():
        try:
            r = providers.submit_video(prompt, seconds=seconds,
                                       mode="reference", images=refs, key=k)
            vid = str(r.get("video_id") or r.get("task_id") or "")
            if not vid:
                raise RuntimeError("供应商没回 video_id")
            _remember_key(vid, k)
            return {"id": vid, "object": "video", "status": "queued", "progress": 0,
                    "model": str(payload.get("model") or config.MODELS.get("video") or "")}
        except providers.RateLimitError as exc:
            last = exc
            continue
    raise last or RuntimeError("没有可用的 key")


def video_status(video_id: str) -> dict:
    """查进度 → OpenAI 形状。画布见到 `url` 就直接取视频，不需要 `/content`。"""
    key = _VIDEO_OWNER.get(video_id)
    if key is None:
        # 服务重启过 ⇒ 映射丢了。按池里的 key 逐个试，**别一上来就报"任务不存在"**
        for k in _pool():
            try:
                d = providers.query_video(video_id, key=k)
            except Exception:                            # noqa: BLE001
                continue
            _remember_key(video_id, k)
            return _video_shape(video_id, d)
        raise RuntimeError("查不到这个视频任务（服务可能重启过）")
    return _video_shape(video_id, providers.query_video(video_id, key=key))


def _video_shape(video_id: str, d: dict) -> dict:
    st = str(d.get("status") or "").lower()
    out = {"id": video_id, "object": "video", "status": "in_progress",
           "progress": d.get("progress") or 0}
    if d.get("url"):
        out["status"] = "completed"
        out["url"] = d["url"]
        out["progress"] = 100
    elif st in ("failed", "error", "cancelled"):
        out["status"] = "failed"
        out["error"] = {"message": str(d.get("error") or "生成失败")}
    return out


def models_payload() -> dict:
    """OpenAI 形状的 `/v1/models`，让画布的模型下拉里能看到真实可用的模型。

    列的是**后端真能服务**的四条通道（生图 / 参考图编辑 / 文本 / 生视频）。
    音频没有 —— 代理不服务它，列出来只会让按钮"能点但永远转圈"。
    """
    ids = [m for m in (config.MODELS.get("image"), config.MODELS.get("chat"),
                       config.MODELS.get("video")) if m]
    return {"object": "list",
            "data": [{"id": m, "object": "model", "created": int(time.time()),
                      "owned_by": "shortdrama"} for m in ids]}
