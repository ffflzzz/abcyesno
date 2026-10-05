# -*- coding: utf-8 -*-
"""ComfyUI（自有协议厂商）—— 媒体链的视频实现，2026-10-05 接入。

## 为什么不是「加一条厂商档 dict」就完事

`v5/vendors.py` 的 dict 档管的是 **OpenAI 兼容类**（`POST {base}/v1/videos`
+ `GET {base}/agnesapi?video_id=`）。ComfyUI 的提交形状是「提交一张**工作流图**」：
`POST /prompt` 收的是 `{prompt: {节点号: {class_type, inputs}}}}`，
产物要从 `GET /history/<prompt_id>` 的**输出节点**里翻出来，再按
`GET /view?filename=…` 取字节。三件事没有一件对得上 dict 档的字段 ⇒ 只能是 impl 模块。

## 依赖的 ComfyUI 公开接口（全部只认这些）

| 用途 | 请求 | 用到的返回字段 |
|---|---|---|
| 健康/机型 | `GET /system_stats` | `system.comfyui_version`、`devices[]`（name/type/vram_total） |
| 认节点 | `GET /object_info` | 顶层键 = 类名（用来找 H3 那批节点） |
| 送参考图 | `POST /upload/image`（multipart） | `name` / `subfolder` / `type` |
| 提交 | `POST /prompt` `{prompt, client_id}` | 200 ⇒ `prompt_id`；400 ⇒ `error` + `node_errors` |
| 排队状态 | `GET /queue` | `queue_running` / `queue_pending` |
| 取产物 | `GET /history/<prompt_id>` | `outputs{节点: {videos/images/gifs: [{filename, subfolder, type}]}}`、`status` |

⚠️ **本模块第一次跑真机是在用户的 GPU 机器上，不在这台开发机上**（本机显卡跑不动
H3，那台机又跨网不可达）。所以这里的每一条形状都由 `v5/tests_local_services.py`
的**本机假 ComfyUI** 按上表逐字实现来背书，真机不符时以那份假实现为对照改。
输出节点各家自定义节点写法不一（`SaveVideo` 给 `videos`、`SaveAnimatedWEBP` 给
`images`），因此产物按 `videos → gifs → images` 的优先级**扫全部输出节点**，
不写死节点号。

## 参数怎么进图（mapping）

工作流是用户那台机器上的一张图，提示词/首帧/宽高/帧数落在哪个节点的哪个字段
**探不出来**（`/object_info` 只给类名和输入名，不给这张图里的连线关系）。
所以接入时把 mapping 连同 workflow 一起存档，形状见 `v5/local_services.py`：

    mapping = {"prompt": {"node": "12", "field": "text"}, "image": {...}, ...}

缺哪个就**响亮报错并指名是哪个角色的哪个节点**，绝不静默少填一个字段
（少填 = 用工作流里上一次的残留值出片，画面与提示词无关而日志全绿 —— 本项目最忌）。
"""
from __future__ import annotations

import copy
import json
import mimetypes
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from ... import vendors as _vendors_mod

#: 厂商名（注册表里的 code）。`local_services.py` 用同一个常量登记。
VENDOR = "comfyui"

#: 产物按这个顺序在输出节点里找（视频类节点各家用的键不统一）。
_OUTPUT_KEYS = ("videos", "gifs", "images")

#: 图/帧数换算与钳制需要的默认值（档里没写时用它）。
_DEFAULT_FPS = 24
_DEFAULT_ALIGN = "none"

_client_id = uuid.uuid4().hex[:16]


def _log(msg: str) -> None:
    print("[comfyui] %s" % msg, flush=True)


class ComfyConfigError(ValueError):
    """已接入 ComfyUI，但**存档不全 / mapping 指不到节点**。

    单独一个类：这类错误的修法不是「重试」，而是「重新接入一次」。
    报错消息里会指名缺的是哪一项（与 `vendors.VendorConfigError` 同风格）。
    """


def cfg() -> dict:
    """当前视频厂商档（必须就是本厂商，否则是被误路由）。"""
    spec = _vendors_mod.spec_for("video")
    if spec.get("impl") is None:
        raise ComfyConfigError(
            "comfyui 的实现被调到了非 comfyui 的厂商档（impl=None）")
    return spec


def _address(spec: dict) -> str:
    """服务地址，去掉尾斜杠。空 ⇒ 响亮报错（说明档案里没写地址）。"""
    a = str(spec.get("address") or "").strip().rstrip("/")
    if not a:
        raise ComfyConfigError(
            "ComfyUI 档里没有服务地址 ⇒ 请在设置面板「本地出片服务」重新探测并接入")
    return a


def _headers(spec: dict) -> dict:
    """可选鉴权头（走公网穿透时才有；本机 ComfyUI 无鉴权，留空）。"""
    h = spec.get("headers")
    return dict(h) if isinstance(h, dict) else {}


def _client(address: str, spec: dict, timeout: float) -> httpx.Client:
    """带 `base_url` 的客户端 —— 本模块一律用相对路径打 ComfyUI。

    本地服务一律 `trust_env=False`（与 `providers.py` 同一条纪律）：
    这台机可能挂着系统代理（Clash / v2rayN），走代理打 127.0.0.1 会
    TLS/CONNECT 中断，报出来的错长得像「ComfyUI 没开」。
    """
    return httpx.Client(base_url=address, timeout=timeout, headers=_headers(spec),
                        trust_env=False)


# ─── 取素材：静帧仍在云端，由我们把字节推给 ComfyUI ─────────────────────────

def _read_media(src: str, timeout: float) -> tuple[str, bytes]:
    """把「http(s) URL 或本地路径」读成 (文件名, 字节)。

    为什么不直接把 URL 丢给 ComfyUI 去取：那样要求**那台 GPU 机能出网**打到
    图片 CDN，而打包版给用户的机器未必能（跨网、公司内网、离线机都有）。
    我们这侧本来就有那张图的 URL，下载后 POST 给 ComfyUI 的 `/upload/image`
    是**唯一不依赖对端出网**的做法。
    """
    s = str(src or "").strip()
    if not s:
        raise ComfyConfigError("参考图为空串")
    if s.lower().startswith(("http://", "https://")):
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=True) as c:
            r = c.get(s)
            r.raise_for_status()
            name = Path(str(r.url.path)).name or "ref.jpg"
            return (name or "ref.jpg"), r.content
    p = Path(s)
    if not p.is_file():
        raise ComfyConfigError("参考图文件不存在：%s" % s[:160])
    return p.name, p.read_bytes()


def _upload_image(client: httpx.Client, name: str, data: bytes) -> str:
    """`POST /upload/image` ⇒ 图进 ComfyUI 的 `input/`，返回工作流里该填的名字。"""
    mime = mimetypes.guess_type(name)[0] or "image/png"
    files = {"image": (name, data, mime)}
    form = {"type": "input", "overwrite": "true"}
    r = client.post("/upload/image", files=files, data=form)
    if r.status_code >= 400:
        raise RuntimeError("ComfyUI 收图失败 HTTP %d: %s"
                          % (r.status_code, r.text[:200]))
    d = r.json()
    got = str(d.get("name") or "").strip()
    if not got:
        raise RuntimeError("ComfyUI 收图返回里没有 name: %s" % r.text[:200])
    sub = str(d.get("subfolder") or "").strip().strip("/\\")
    return "%s/%s" % (sub, got) if sub else got


# ─── 秒数 → 帧数 / 画幅 → 像素 ──────────────────────────────────────────────

def _align_frames(raw: int, align: str) -> int:
    """把帧数收到工作流要求的网格上（H3 这类模型常见 4n+1）。

    取**最近**的合法值（不是无条件往下），并保证 ≥ 网格起点，
    否则 6 秒 @24fps 会被压成 2 帧这种根本不是视频的东西。
    """
    a = str(align or "none").strip().lower()
    if a in ("", "none", "off"):
        return max(1, int(raw))
    if "n+1" in a:
        try:
            step = int(a.replace("n+1", "").strip() or 4)
        except ValueError:
            step = 4
        step = max(2, step)
        k = max(1, round((raw - 1) / step))
        return k * step + 1
    try:
        step = int(a.lstrip("=n") or 1)
    except ValueError:
        step = 1
    step = max(1, step)
    return max(step, round(raw / step) * step)


def seconds_to_frames(seconds: float, fps: int, align: str) -> int:
    return _align_frames(int(round(max(0.04, float(seconds)) * fps)), align)


def frames_to_seconds(frames: int, fps: int) -> float:
    return round(int(frames) / float(fps or _DEFAULT_FPS), 2)


def _mapping_of(spec: dict) -> dict:
    m = spec.get("mapping")
    if not isinstance(m, dict) or not m:
        raise ComfyConfigError(
            "ComfyUI 档里没有 mapping（提示词/首帧/宽高/帧数落在哪个节点的哪个字段）"
            "⇒ 请在设置面板重新接入一次，并把那张工作流交进来")
    return m


def _set_field(graph: dict, mapping: dict, role: str, value: Any,
               required: bool = True) -> bool:
    """把 `value` 写进 `mapping[role]` 指向的那个节点字段。

    返回是否写了。`required=True` 时写不到就报错 ——
    ★ 这里绝不能「写不到就跳过」：跳过等于把工作流里**上一次残留的值**当成
    本次参数发出去，出片与提示词无关而日志全绿（本项目最忌的失败不可见）。
    """
    hit = mapping.get(role)
    if not isinstance(hit, dict):
        if required:
            raise ComfyConfigError(
                "mapping 里缺「%s」这一项 ⇒ 无法把参数送进工作流"
                "（需要 %s 里有 {node, field}）"
                % (role, json.dumps(sorted(mapping), ensure_ascii=False)))
        return False
    node, field = str(hit.get("node") or ""), str(hit.get("field") or "")
    entry = graph.get(node)
    if not isinstance(entry, dict) or not isinstance(entry.get("inputs"), dict):
        raise ComfyConfigError(
            "mapping 的「%s」指向节点 %r，但工作流里没有这个节点"
            "（它的 class_type 是否被这张图导出掉了？）" % (role, node))
    if field not in entry["inputs"]:
        raise ComfyConfigError(
            "mapping 的「%s」指向节点 %s 的字段 %r，但该节点（class_type=%s）"
            "的输入里没有这个字段。现有输入：%s"
            % (role, node, field, entry.get("class_type"),
               json.dumps(sorted(entry["inputs"]), ensure_ascii=False)))
    entry["inputs"][field] = value
    return True


def _resolution(spec: dict, aspect_ratio: str | None) -> tuple[int, int, str]:
    """画幅字符串 → 具体像素。表里没有就取**最接近**的，并打印说明。

    为什么必须打印：本地工作流吃的是宽高像素，而云端吃的是 `9:16` 这种比例名。
    「按最接近的做了」是人必须知道的降级，不是可以悄悄发生的事。
    """
    table = spec.get("resolutions") or {}
    want = str(aspect_ratio or spec.get("aspect_ratio") or "9:16").strip()
    if not isinstance(table, dict) or not table:
        raise ComfyConfigError(
            "ComfyUI 档里没有 resolutions 表（画幅 → 像素）⇒ 请重新接入")
    if want in table:
        pair = table[want]
    else:
        def _val(k: str) -> float:
            try:
                a, b = str(k).split(":")
                return float(a) / float(b)
            except Exception:  # noqa: BLE001
                return 0.0

        def _ratio(v) -> float:
            try:
                return float(v[0]) / float(v[1])
            except Exception:  # noqa: BLE001
                return 0.0

        best_key = max(table, key=lambda k: -abs(_val(k) - _val(want)))
        pair = table[best_key]
        _log("画幅 %s 不在本机的像素表里，按最接近的 %s 出（%s）"
             % (want, best_key, pair))
    try:
        w, h = int(pair[0]), int(pair[1])
    except Exception as e:  # noqa: BLE001
        raise ComfyConfigError("resolutions 里 %s 的值不是 [宽, 高]：%r（%s）"
                               % (want, pair, str(e)[:60]))
    return w, h, want


def clamp_seconds(requested: float, spec: dict) -> float:
    """把请求秒数钳进本机工作流的区间，并**打印钳位动作**。

    下限/上限都取档里的值（`seconds_min` / `seconds_max`）。
    ★ 为什么盯着这个数：2026-09-16 曾因 `AGNES_VIDEO_MAX_SECONDS` 误设 10，
    620 镜里 9 镜被**静默**压短，日志全绿。这里反过来做：动了就出声。
    """
    lo = float(spec.get("seconds_min") or 4)
    hi = float(spec.get("seconds_max") or 12)
    v = float(requested)
    if v < lo:
        _log("秒数 %.1f 低于本机下限 %.1f，已抬到 %.1f" % (v, lo, lo))
        return lo
    if v > hi:
        _log("秒数 %.1f 超过本机上限 %.1f，已压到 %.1f" % (v, hi, hi))
        return hi
    return v


# ─── 三个对外函数（签名与 providers.py 的同名函数逐字一致）─────────────────

def gen_image(prompt: str, refs: list | None = None, ratio: str | None = None,
              timeout: int = 120, key: str | None = None) -> tuple[str, str]:
    """本厂商**没有**接图片能力 —— 当用户选了它生图时响亮拒绝。

    故意留这个函数而不是「不实现」：`providers.gen_image` 的分发会
    `impl.gen_image(...)`，缺函数会得到一个 `AttributeError`，
    看不出是「这厂商不支持图片」还是「代码坏了」。
    """
    raise ComfyConfigError(
        "ComfyUI 本地档只接了「视频」，没有生图实现 ⇒ 生图请把图片厂商切回 agnes"
        "（设置面板「本地出片服务」只改视频档，或 SHORTDRAMA_IMAGE_VENDOR=agnes）")


def submit_video(prompt: str, *, first_frame: str | None = None,
                 last_frame: str | None = None, seconds: int = 10,
                 aspect_ratio: str | None = None, timeout: int = 60,
                 mode: str = "keyframe", images: list | None = None,
                 audios: list | None = None, key: str | None = None) -> dict:
    """提交一张工作流，返回 `{"video_id": <prompt_id>, "task_id": <prompt_id>}`。

    素材约定（2026-10-05 与用户定的四条之一：只视频走本地，静帧仍云端）：
      · `keyframe` ⇒ `first_frame` 当首帧；`last_frame` 只有工作流里有
        「尾帧」位才送得进去，没有就打印丢弃说明（云端是原生支持首尾帧的）。
      · `reference` ⇒ 只吃 `images[0]`。**其余参考图被丢弃并打印** ——
        本机的角色设定表 / 场景空镜 / 上一组末帧没有对应入口，
        这与 pack 档「身份靠设定表锁」的假设冲突，所以提示里会建议换回云端。
    """
    spec = cfg()
    address = _address(spec)
    mapping = _mapping_of(spec)
    wf_path = str(spec.get("workflow_path") or "").strip()
    if not wf_path:
        raise ComfyConfigError("ComfyUI 档里没有 workflow_path ⇒ 请重新接入那张工作流")
    wf_file = Path(wf_path)
    if not wf_file.is_file():
        raise ComfyConfigError("工作流存档不见了：%s ⇒ 请重新接入"
                               % str(wf_file)[:160])
    graph = json.loads(wf_file.read_text(encoding="utf-8"))
    if not isinstance(graph, dict) or not graph:
        raise ComfyConfigError("工作流存档不是一张图（应为 {节点号: {...}}）：%s"
                               % wf_file.name)
    graph = copy.deepcopy(graph)

    mode = (mode or "keyframe").strip().lower()
    src = first_frame
    if mode == "reference":
        pool = [u for u in (images or []) if u]
        if not pool:
            raise ComfyConfigError("reference 档需要至少一张参考图，收到 0 张")
        src = pool[0]
        if len(pool) > 1:
            _log("★ reference 收到 %d 张参考图，本机工作流只有 1 个图位："
                 "只送第 1 张，其余 %d 张（设定表/场景空镜/前组末帧）已丢弃。"
                 "pack 档的身份锁定依赖那些图 ⇒ 这类项目建议把视频档切回 agnes"
                 % (len(pool), len(pool) - 1))
    elif mode == "keyframe":
        src = first_frame or last_frame
        if last_frame and "last_image" not in mapping:
            _log("★ keyframe 档给了尾帧，但本机工作流没有尾帧位（mapping 缺 "
                 "last_image）⇒ 尾帧已丢弃，只按首帧出")
    else:
        raise ComfyConfigError("本厂商只接了 keyframe / reference，收到 mode=%r" % mode)
    if not src:
        raise ComfyConfigError(
            "本地视频必须有驱动图（keyframe 要 first_frame、reference 要 images），"
            "本次两样都没有 ⇒ 这条请求在云端同样会被拒，先查静帧是否已生成")

    secs = clamp_seconds(float(seconds), spec)
    fps = int(spec.get("fps") or _DEFAULT_FPS)
    align = str((mapping.get("frames") or {}).get("align") or _DEFAULT_ALIGN)
    frames = seconds_to_frames(secs, fps, align)
    real = frames_to_seconds(frames, fps)
    if abs(real - secs) > 0.25:
        _log("帧数网格把 %.1f 秒调成 %.1f 秒（%d 帧 @ %d fps，对齐 %s）"
             % (secs, real, frames, fps, align))
    width, height, ratio_used = _resolution(spec, aspect_ratio)

    # 1) 先把驱动图推给 ComfyUI（它不需要出网就能拿到云端的静帧）
    image_name = ""
    if "image" not in mapping and "last_image" not in mapping:
        _log("★ mapping 里没有任何图位（image / last_image 都没有）⇒ 本次的静帧"
             "进不了工作流，出片只按提示词走，身份锁定全丢。"
             "这通常是接入时那张图被改过，请在设置面板重新接入一次")
    with _client(address, spec, max(float(timeout) or 60.0, 120.0)) as c:
        if "image" in mapping:
            name, data = _read_media(src, timeout=60)
            image_name = _upload_image(c, name, data)
            _set_field(graph, mapping, "image", image_name)
        if "last_image" in mapping and last_frame:
            name, data = _read_media(last_frame, timeout=60)
            _set_field(graph, mapping, "last_image", _upload_image(c, name, data))
    if audios:
        _log("★ 收到 %d 条音频参考，本机工作流没有音频输入位 ⇒ 已丢弃"
             % len([a for a in audios if a]))

    # 2) 参数写进图
    _set_field(graph, mapping, "prompt", prompt)
    _set_field(graph, mapping, "width", width)
    _set_field(graph, mapping, "height", height)
    _set_field(graph, mapping, "frames", frames)
    if isinstance(mapping.get("seed"), dict):
        _set_field(graph, mapping, "seed", int(mapping["seed"].get("value", 0)))

    body = {"prompt": graph, "client_id": _client_id}
    with _client(address, spec, float(timeout) or 60.0) as c:
        r = c.post("/prompt", json=body)
    if r.status_code >= 400:
        detail = r.text[:300]
        try:
            err = r.json().get("error") or {}
            if isinstance(err, dict) and err.get("message"):
                detail = "%s | %s" % (str(err.get("message"))[:160], detail)
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError("ComfyUI 提交失败 HTTP %d: %s" % (r.status_code, detail))
    d = r.json()
    pid = str(d.get("prompt_id") or "").strip()
    if not pid:
        raise RuntimeError("ComfyUI 提交成功但没有 prompt_id: %s" % r.text[:200])
    _log("已提交：%s（%dx%d，%d 帧 ≈ %.1f 秒，画幅 %s，图 %s）"
         % (pid[:12], width, height, frames, real, ratio_used, image_name or "-"))
    return {"video_id": pid, "task_id": pid, "seconds": real}


def query_video(video_id: str, timeout: int = 30, key: str | None = None) -> dict:
    """查一个 prompt 的状态，完成时给出可下载的 `url`。

    状态映射（`_wait_one` 只认 completed / failed / error 三态，其余按进行中）：
      · `/history/<id>` 里有 ⇒ 看 `status`：error ⇒ failed；有产物 ⇒ completed；
        完成却没产物 ⇒ failed（写明「输出节点里翻不到文件」）。
      · 不在 history 但在 `/queue` ⇒ processing。
      · 两处都没有 ⇒ failed 并说明可能是 ComfyUI 重启把队列洗了
        （**不能报 processing**：那样会一路轮询到超时，把「服务重启」伪装成「跑得慢」）。
    """
    spec = cfg()
    address = _address(spec)
    pid = str(video_id or "").strip()
    if not pid:
        return {"status": "failed", "progress": None, "url": None,
                "error": "video_id 是空串"}
    with _client(address, spec, float(timeout) or 30.0) as c:
        r = c.get("/history/%s" % pid)
        if r.status_code >= 400:
            return {"status": "processing", "progress": None, "url": None,
                    "error": "history HTTP %d" % r.status_code}
        hist = r.json() or {}
        entry = hist.get(pid) or {}
        if not entry:
            qr = c.get("/queue")
            q = qr.json() if qr.status_code < 400 else {}
            inq = []
            for side in ("queue_running", "queue_pending"):
                for item in (q.get(side) or []):
                    try:
                        inq.append(str(item[1]))
                    except Exception:  # noqa: BLE001
                        continue
            if pid in inq:
                return {"status": "processing", "progress": None, "url": None,
                        "error": None}
            return {"status": "failed", "progress": None, "url": None,
                    "error": "ComfyUI 既不认这个任务、也不在队列里"
                             "（多半是 ComfyUI 中途重启洗了队列）"}

    status = entry.get("status") or {}
    if isinstance(status, dict) and str(status.get("status_str", "")).lower() == "error":
        return {"status": "failed", "progress": None, "url": None,
                "error": "ComfyUI 执行报错：%s"
                         % json.dumps(status, ensure_ascii=False)[:240]}
    found = _pick_output(entry.get("outputs") or {})
    if not found:
        return {"status": "failed", "progress": None, "url": None,
                "error": "任务完成但输出节点里翻不到图片/视频文件"
                         "（工作流的保存节点可能被删掉，或产物写到了别的节点）"}
    fn, sub, typ = found
    url = "%s/view?filename=%s&subfolder=%s&type=%s" % (
        address, quote(fn, safe=""), quote(sub, safe=""), quote(typ, safe=""))
    return {"status": "completed", "progress": None, "url": url, "error": None}


def _pick_output(outputs: dict) -> tuple[str, str, str] | None:
    """从 history 的 outputs 里挑一个产物文件，返回 (filename, subfolder, type)。

    先扫 `videos` 再 `gifs` 再 `images`（见文件头：各家保存节点用的键不统一），
    同一键内取**最后一个**（多数节点是覆盖写，最后一份是本次的）。
    """
    for wanted in _OUTPUT_KEYS:
        for node in (outputs or {}).values():
            items = (node or {}).get(wanted) or []
            if not items:
                continue
            last = items[-1] if isinstance(items[-1], dict) else {}
            fn = str(last.get("filename") or "").strip()
            if fn:
                return fn, str(last.get("subfolder") or ""), str(
                    last.get("type") or "output")
    return None
