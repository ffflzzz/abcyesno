# -*- coding: utf-8 -*-
"""本机 / 局域网的出片服务：一键探测 + 接入档案（2026-10-05）。

## 为什么需要这一层

`v5/vendors.py` 的厂商档是**写死在源码里的两条**（agnes / deepseek）。而用户的 GPU
机器上跑着什么（ComfyUI 在哪、装的是 H3 的哪批节点、那张工作流长什么样）
只有**运行时**才知道 ⇒ 必须有一份「盘上的接入档案」，探到就登记、并能撤销。

## 三条设计决定（都是被这个仓库的旧坑逼出来的）

1. **档案放在 `config.RUNTIME_ROOT` 下，不放安装目录。**
   打包版的 `SHORTDRAMA_RUNTIME` 指向用户可写目录，而安装树按
   `shortdrama-runner.js` 的注释是**当只读对待**的（要能装进 Program Files）。
   往 `settings.env` 写默认值在只读安装下会当场失败，所以这里根本不碰它。
2. **「设为默认」仍然只改一个环境变量，不新增第二套选择机制。**
   厂商选择的真相源始终只有 `SHORTDRAMA_VIDEO_VENDOR`（`vendors.current()`）。
   本模块在 `vendors` import 期把档案里的默认值**写进这个变量**，
   于是 CLI / shim / 媒体子进程三条路径吃到的是同一个东西，没有第二份真相。
3. **人显式设的环境变量永远赢过档案。**
   A/B 靠单变量切换（`SHORTDRAMA_VIDEO_VENDOR=agnes python -m v5.series …`），
   那是这个项目的命根子。打包版 `settings.env` 会注入出厂的 `agnes`，
   所以「env 里有值」不等于「人显式设的」⇒ 判据是**它是否不等于出厂那一行的值**。
   每次生效都打一行说明（谁赢了、为什么），不静默换档。

## 探测的形状（返回给前端/设置面板）

    {"address": "http://127.0.0.1:8188", "reachable": True, "kind": "comfyui",
     "comfyui_version": "0.3.x", "devices": [{"name": "NVIDIA GeForce RTX 3060",
                                              "vram_gb": 12.0}],
     "h3_nodes": ["MiniMaxH3Sampler", ...], "model_files": [...],
     "reason": ""            # 探不到时**必须**有一句人话，不许空着让人猜
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import config

#: 档案目录（RUNTIME_ROOT 下，随打包版走到用户可写目录）
_DIR_NAME = "local_media"
_PROFILE_FILE = "profile.json"
_WORKFLOW_FILE = "comfyui_workflow.json"

#: 厂商名与实现模块名（`v5/media/vendors/comfyui.py`）
VENDOR = "comfyui"

#: 探测的默认候选地址（本机默认端口；跨网/跨机器由用户在设置面板手填）
DEFAULT_ADDRESS = "http://127.0.0.1:8188"

#: 认「这是 H3」用的节点名/文件名关键词（小写匹配）。认不出来不代表接不了，
#: 只影响面板上那行「这台机装了 H3」的可信度，所以列出来给人自己看。
_H3_HINTS = ("minimax", "h3", "clipproj")

#: 工作流里这些字段的常见叫法 —— 用来**猜** mapping，猜不中就要求人指。
_ROLE_FIELDS: dict[str, tuple[str, ...]] = {
    "prompt": ("text", "prompt", "value", "positive"),
    "image": ("image", "images", "first_frame", "input_image"),
    "width": ("width", "w", "image_width"),
    "height": ("height", "h", "image_height"),
    "frames": ("length", "frames", "num_frames", "video_length", "frame_count"),
}

#: 本地出片比云端慢得多，轮询窗口默认放宽（云端是 60 轮 × 10 秒 ≈ 10 分钟）。
DEFAULT_POLL_ROUNDS = 360
DEFAULT_POLL_INTERVAL = 10

#: 一次请求**必须**能送进去的参数。缺任何一个都不许接入 ——
#: 少一个字段等于把工作流里**上一次的残留值**当成本次参数发出去
#: （画面与提示词无关而日志全绿，是本项目最贵的一类 bug）。
REQUIRED_ROLES = ("prompt", "image", "frames")


# ─── 档案读写 ────────────────────────────────────────────────────────────────

def dir_path() -> Path:
    return Path(config.RUNTIME_ROOT) / _DIR_NAME


def profile_path() -> Path:
    return dir_path() / _PROFILE_FILE


def workflow_path() -> Path:
    return dir_path() / _WORKFLOW_FILE


def load_profile() -> dict:
    """读接入档案。文件不在 / 读坏了 ⇒ `{}`（**并打一行**，坏档案不能装成没接）。"""
    p = profile_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print("[local] 接入档案读不出来（%s）：%s ⇒ 视为未接入，请在设置面板重新接入"
              % (str(e)[:80], p), flush=True)
        return {}
    return data if isinstance(data, dict) else {}


def _save_profile(profile: dict) -> Path:
    d = dir_path()
    d.mkdir(parents=True, exist_ok=True)
    p = profile_path()
    p.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def settings_env_value(name: str) -> str:
    """出厂 `settings.env` 里某个键的值（没有 ⇒ 空串）。

    为什么要读它：打包版会把它整份注入子进程 env，于是「档案设的默认」与
    「出厂写的 agnes」在 Python 里长得一模一样。只有拿出厂值比一下，
    才分得出**人真的显式设过**（那样必须让人赢，A/B 才做得了）。
    """
    f = Path(config.PROJECT_ROOT) / "settings.env"
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return ""


# ─── 探测 ────────────────────────────────────────────────────────────────────

def _candidate_addresses(extra: list | None = None) -> list[str]:
    """本机默认端口 + 用户手填的地址（去重、保持顺序）。"""
    out: list[str] = [DEFAULT_ADDRESS]
    for a in (extra or []):
        s = str(a or "").strip()
        if s and s not in out:
            out.append(s)
    prof = load_profile()
    pa = str(prof.get("address") or "").strip()
    if pa and pa not in out:
        out.append(pa)
    return out


def _norm(address: str) -> str:
    """补 `http://`、去尾斜杠。用户手填时多半只写 `127.0.0.1:8188`。"""
    s = str(address or "").strip().rstrip("/")
    if not s:
        return ""
    if "://" not in s:
        s = "http://" + s
    return s


def probe_one(address: str, timeout: float = 3.0) -> dict:
    """探一个地址。返回里 **`reachable=False` 时必须带一句 `reason`**。

    为什么盯着这句：面板上「什么都没探到」和「ComfyUI 开着但版本太旧不认识」
    是两件完全不同的事，混成一句「未检测到本地服务」会让人去重装 ComfyUI。
    """
    base = _norm(address)
    out = {"address": base, "reachable": False, "kind": "", "reason": "",
           "comfyui_version": "", "devices": [], "h3_nodes": [], "model_files": []}
    if not base:
        out["reason"] = "地址是空的"
        return out
    try:
        import httpx
        with httpx.Client(timeout=timeout, trust_env=False) as c:
            r = c.get(base + "/system_stats")
            if r.status_code >= 400:
                out["reason"] = ("%s 有服务在听，但 /system_stats 返回 HTTP %d"
                                 " ⇒ 多半不是 ComfyUI，或版本太旧" % (base, r.status_code))
                return out
            data = r.json() or {}
            system = data.get("system") or {}
            if not isinstance(system, dict) or not system:
                out["reason"] = ("%s 回答了 /system_stats 但没有 system 字段"
                                 " ⇒ 不是 ComfyUI" % base)
                return out
            out["reachable"] = True
            out["kind"] = "comfyui"
            out["comfyui_version"] = str(system.get("comfyui_version") or "")
            for d in (data.get("devices") or []):
                if not isinstance(d, dict):
                    continue
                total = d.get("vram_total") or d.get("torch_vram_total") or 0
                out["devices"].append({
                    "name": str(d.get("name") or ""),
                    "type": str(d.get("type") or ""),
                    "vram_gb": round(float(total or 0) / (1024 ** 3), 1),
                })
            # 节点与模型文件：/object_info 可能好几 MB，探测是一次性的点击，值。
            try:
                ri = c.get(base + "/object_info", timeout=max(timeout, 10.0))
                nodes = ri.json() if ri.status_code < 400 else {}
                out["h3_nodes"], out["model_files"] = _scan_nodes(nodes)
            except Exception as e:  # noqa: BLE001
                out["reason"] = ("ComfyUI 在线，但 /object_info 取失败（%s）"
                                 " ⇒ 接入时的工作流参数可能要手指" % str(e)[:60])
    except Exception as e:  # noqa: BLE001
        out["reason"] = "连不上 %s（%s）" % (base, _explain_conn_error(e))
    return out


def _explain_conn_error(e: Exception) -> str:
    """把 httpx 的异常翻成一句人话（跨网机器最容易撞的三种）。"""
    name = type(e).__name__.lower()
    text = str(e)
    if "connecterror" in name or "connection refused" in text.lower():
        return "端口上没东西在听 ⇒ 那边 ComfyUI 没开，或端口填错了"
    if "timeout" in name or "timeout" in text.lower():
        return "握手超时 ⇒ 跨网/VPN 不通，或那台机器不允许这个端口进来"
    if "ssl" in name or "ssl" in text.lower():
        return "TLS 错 ⇒ 那边可能是 https，试试在地址前面写 https://"
    return text[:120]


def _scan_nodes(nodes: Any) -> tuple[list[str], list[str]]:
    """从 `/object_info` 里挑出像 H3 的节点名，以及加载器里的模型文件名。

    只列**名字**，不判「够不够格跑」——判据在人那边：面板把这两列摊开，
    他一眼看见 `MiniMaxH3...` 就知道接得成，看不见就知道要去装节点。
    """
    hits: set[str] = set()
    files: set[str] = set()
    if not isinstance(nodes, dict):
        return [], []
    for name, info in nodes.items():
        low = str(name).lower()
        if any(h in low for h in _H3_HINTS):
            hits.add(str(name))
        if not isinstance(info, dict):
            continue
        required = ((info.get("input") or {}).get("required")) or {}
        optional = ((info.get("input") or {}).get("optional")) or {}
        for sec in (required, optional):
            if not isinstance(sec, dict):
                continue
            for _field, shape in sec.items():
                if isinstance(shape, (list, tuple)) and shape:
                    first = shape[0]
                    if isinstance(first, (list, tuple)):
                        for v in first:
                            v = str(v)
                            if v.lower().endswith((".safetensors", ".ckpt", ".pth", ".gguf")):
                                if any(h in v.lower() for h in _H3_HINTS):
                                    files.add(v)
    return sorted(hits)[:40], sorted(files)[:40]


def probe(addresses: list | None = None, timeout: float = 3.0) -> dict:
    """探测候选地址，返回 `{"found": [...], "tried": [...], "default": "..."}`。

    `found` 只放**确认可达**的；`tried` 把每一个候选的 `reason` 原样带回去
    （探不到时人要能看到「为什么没探到」，这是本面板唯一能自证清白的地方）。
    """
    tried = [probe_one(a, timeout=timeout) for a in _candidate_addresses(addresses)]
    found = [t for t in tried if t["reachable"]]
    prof = load_profile()
    return {"found": found, "tried": tried,
            "connected": bool(prof.get("address")),
            "profile": _profile_view(prof)}


# ─── 接入（把工作流与 mapping 存成档案，并登记厂商档）───────────────────────

def parse_workflow(workflow: Any) -> dict:
    """把工作流入参归一成 `{节点号: {class_type, inputs}}`。

    两种来源都要能吃：ComfyUI 界面导出的 **API 格式**（就是这一层），
    以及用户在面板里贴的整份 JSON 文本（顶层可能还包着 `{"nodes": …}` ——
    那不是 API 格式，直接拒掉并说明怎么导出，免得存一张跑不通的图）。

    公开给 `webmap` 用（面板上「先看一眼这张图认不认得出参数落点」那一步，
    接不接还没决定 ⇒ 不能只有私有的 `connect` 能校验）。
    """
    if isinstance(workflow, str):
        try:
            workflow = json.loads(workflow)
        except Exception as e:  # noqa: BLE001
            raise ValueError("工作流 JSON 解析失败：%s" % str(e)[:120])
    if not isinstance(workflow, dict) or not workflow:
        raise ValueError("工作流是空的（要的是 ComfyUI 导出的 API 格式那张图）")
    if "nodes" in workflow and "links" in workflow:
        raise ValueError(
            "这份是 ComfyUI 界面格式的图，程序要的是 API 格式："
            "菜单 Workflow → Export (API)，再把那个 .json 交进来")
    bad = [k for k, v in workflow.items()
           if not (isinstance(v, dict) and isinstance(v.get("inputs"), dict))]
    if bad:
        raise ValueError(
            "这份工作流里有 %d 个条目不是 {inputs: …} 的形状（例如 %s）"
            " ⇒ 它不是 API 格式" % (len(bad), str(bad[:3])))
    return workflow


def suggest_mapping(graph: dict) -> dict:
    """按字段名**猜** mapping，猜不中的列进 `missing` 指名要人来指。

    ★ 为什么要猜而不是要求人手填全部：面板上要「快捷接入」，
      而五个角色里通常四个能靠字段名认出来（`text` / `image` / `width` / `height`）。
      但认出来的每一项都带上「是哪个节点的哪个字段」给人回显，
      ⛔ 绝不把猜中的当确定的偷偷用下去。
    """
    found: dict[str, dict] = {}
    missing: list[str] = []
    for role, names in _ROLE_FIELDS.items():
        hit = None
        for node, entry in sorted(graph.items()):
            inputs = entry.get("inputs") or {}
            for fname in names:
                if fname in inputs:
                    # 连线进来的字段（值是 [节点号, 端口]）不能由我们写死覆盖
                    if isinstance(inputs[fname], (list, tuple)):
                        continue
                    hit = {"node": str(node), "field": fname,
                           "class_type": str(entry.get("class_type") or ""),
                           "current": inputs[fname]}
                    break
            if hit:
                break
        if hit:
            found[role] = hit
        else:
            missing.append(role)
    return {"mapping": found, "missing": missing}


def connect(address: str, workflow: Any, mapping: dict | None = None,
            fps: int = 24, align: str = "4n+1", resolutions: dict | None = None,
            seconds_min: float = 4, seconds_max: float = 12,
            headers: dict | None = None, set_default: bool = True,
            poll_rounds: int = DEFAULT_POLL_ROUNDS,
            poll_interval: int = DEFAULT_POLL_INTERVAL) -> dict:
    """把工作流 + 参数落点存成接入档案，登记厂商档，并按需设为默认视频厂商。

    校验在**写盘之前**全过一遍（图能解析、地址真能连、mapping 指的节点字段
    真的存在）—— 存一份跑不通的档案，坏的是后面每一次出片，而它报出来的地方
    离根因很远。
    """
    from . import vendors

    base = _norm(address)
    if not base:
        raise ValueError("地址是空的")
    graph = parse_workflow(workflow)
    check = probe_one(base)
    if not check["reachable"]:
        raise ValueError(check["reason"] or "ComfyUI 不可达")

    sug = suggest_mapping(graph)
    mapping = dict(sug["mapping"]) | dict(mapping or {})
    # 帧数网格写在 frames 这一项里（`comfyui.submit_video` 从那里读）。
    # ★ 为什么存进 mapping 而不是档顶层：提交侧读的是 `mapping["frames"]`，
    #   顶层再存一份就是两个真相源 —— 改一处不动另一处，出片按没人预期的网格走。
    if "frames" in mapping:
        mapping["frames"]["align"] = str(align or "none")
    required = [r for r in REQUIRED_ROLES if r not in mapping]
    if required:
        raise ValueError(
            "这几项参数认不出该写进哪个节点：%s ⇒ 请在面板里指一下（工作流里"
            "它的字段名可能不叫 %s）" % ("、".join(required),
                                        " / ".join(_ROLE_FIELDS[required[0]])))
    # 逐个确认节点与字段真的在图上（写不进去的 mapping 到提交时才炸，那时
    # 已经在出片中途了）—— 借实现模块里的同一个校验函数，判据不写两份。
    from .media.vendors import comfyui as _impl
    _impl._mapping_of({"mapping": mapping})
    for role in ("prompt", "width", "height", "frames"):
        if role in mapping:
            _impl._set_field(graph, mapping, role, _dry_value(role))

    wf = workflow_path()
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")

    profile = {
        "vendor": VENDOR,
        "address": base,
        "workflow_path": str(wf),
        "mapping": mapping,
        "fps": int(fps),
        "resolutions": dict(resolutions or _DEFAULT_RESOLUTIONS),
        "seconds_min": float(seconds_min),
        "seconds_max": float(seconds_max),
        "headers": dict(headers or {}),
        "poll_rounds": int(poll_rounds),
        "poll_interval": int(poll_interval),
        "default": bool(set_default),
        "detected": {"comfyui_version": check.get("comfyui_version"),
                     "devices": check.get("devices"),
                     "h3_nodes": check.get("h3_nodes"),
                     "model_files": check.get("model_files")},
    }
    _save_profile(profile)
    vendors.register(VENDOR, spec_of(profile), replace=True)
    if set_default:
        _apply_env(profile)
    return {"ok": True, "profile": _profile_view(profile),
            "mapping_suggested": sug, "check": check,
            "notes": _notes(profile)}


#: 默认像素表：竖屏 9:16 与横屏 16:9 各一档，尺寸按 12G 级显卡能稳跑的量给。
#: ★ 这是**待真机校准的初值**，不是实测数：真机第一次出片后要按显存/耗时改。
_DEFAULT_RESOLUTIONS = {"9:16": [480, 864], "16:9": [864, 480], "1:1": [640, 640]}


def _dry_value(role: str) -> Any:
    return {"prompt": "验证用", "width": 8, "height": 8, "frames": 9}.get(role, 0)


def _notes(profile: dict) -> list[str]:
    """接入时要**当场说给人听**的降级（不是等出片时才在日志里看见）。"""
    out = []
    out.append("图片（静帧）仍走 agnes：本地只接了视频。面板选到 ComfyUI 生图会直接报错。")
    out.append("reference / pack 档的身份靠多张参考图，本机工作流只有 1 个图位，"
               "多余的图会被丢弃（提交时逐条打印）。这类项目建议视频档切回 agnes。")
    out.append("秒数→帧数按 %d fps 与 %s 网格换算，与云端按秒收费的口径不同。"
               % (int(profile.get("fps") or 24),
                  str((profile.get("mapping") or {}).get("frames", {}).get("align") or "none")))
    return out


def _profile_view(profile: dict) -> dict:
    """给面板看的档案（去掉绝对路径以外的内部字段，保留来源与降级说明）。"""
    if not profile:
        return {}
    return {
        "vendor": profile.get("vendor"),
        "address": profile.get("address"),
        "default": bool(profile.get("default")),
        "fps": profile.get("fps"),
        "resolutions": profile.get("resolutions"),
        "seconds_min": profile.get("seconds_min"),
        "seconds_max": profile.get("seconds_max"),
        "mapping": profile.get("mapping"),
        "detected": profile.get("detected"),
        "workflow_exists": bool(Path(str(profile.get("workflow_path") or "")).is_file()),
    }


def spec_of(profile: dict) -> dict:
    """档案 → `vendors` 注册表里那条档（字段含义见 `v5/vendors.py` 的 AGNES 档）。"""
    return {
        "label": "ComfyUI 本地·只支持视频",
        "impl": "comfyui",
        "auth": "none",
        "address": profile.get("address"),
        "workflow_path": profile.get("workflow_path"),
        "mapping": profile.get("mapping") or {},
        "fps": profile.get("fps") or 24,
        "resolutions": profile.get("resolutions") or dict(_DEFAULT_RESOLUTIONS),
        "headers": profile.get("headers") or {},
        "seconds_min": profile.get("seconds_min") or 4,
        "seconds_max": profile.get("seconds_max") or 12,
        "poll_rounds": profile.get("poll_rounds") or DEFAULT_POLL_ROUNDS,
        "poll_interval": profile.get("poll_interval") or DEFAULT_POLL_INTERVAL,
        # 云端 Agnes 的产物是可下载 URL；本机的也一样（`/view?filename=`）。
        "output_delivery": "url",
        # 工作流自己存的产物没有音轨可保证 —— 报告由 compose 前的探测给，
        # 这里只声明「厂商不承诺」，不参与任何判据。
        "native_audio": False,
        # 能力声明：**只接视频**。静帧仍走云端 agnes（用户定的四条之一），
        # 前端据此不把它列进图片下拉；`webmap._caps` 读的就是这一项。
        "capabilities": {"image": False, "video": True},
    }


# ─── 默认档的生效与撤销 ──────────────────────────────────────────────────────

def _env_key() -> str:
    from . import vendors
    return vendors.ENV_KEY["video"]


def decide(profile: dict) -> tuple[str, str]:
    """**纯函数**：档案说「我是默认」时，到底该不该改环境变量、为什么。

    返回 `(动作, 说明)`，动作 ∈ {"apply", "keep"}。
    分成两步是有原因的：`status()` 是只读端点，绝不能因为「顺便算一下来源」
    就把进程环境改了 —— 那会让一次刷新面板变成一次换档。
    """
    key = _env_key()
    vendor = str(profile.get("vendor") or VENDOR)
    cur = (os.environ.get(key) or "").strip()
    factory = settings_env_value(key)
    if cur and cur != factory and cur != vendor:
        return "keep", ("视频厂商沿用显式环境变量的 %s（档案里的 %s 未覆盖；"
                        "想用本地出片请显式设 %s=%s）" % (cur, vendor, key, vendor))
    return "apply", "视频厂商 = %s（来源：本机接入档案 %s）" % (vendor, profile.get("address"))


def _apply_env(profile: dict, reason_log: bool = True) -> str:
    """按 `decide()` 的结果落一次环境变量，返回那句来源说明。"""
    action, msg = decide(profile)
    if action == "apply":
        os.environ[_env_key()] = str(profile.get("vendor") or VENDOR)
    if reason_log:
        print("[local] %s%s" % ("⚠ " if action == "keep" else "", msg), flush=True)
    return msg


def apply_at_import() -> str:
    """`v5/vendors.py` 在 import 期调这里：登记档案里的厂商，并按需设默认。

    没有档案 ⇒ 什么都不做，返回空串（行为与接入本功能前**一字不变**）。
    """
    profile = load_profile()
    if not profile.get("address"):
        return ""
    from . import vendors
    try:
        vendors.register(VENDOR, spec_of(profile), replace=True)
    except Exception as e:  # noqa: BLE001 —— 档案坏了不能拖垮整个 import
        print("[local] ⚠ 接入档案登记失败（%s）⇒ 本次仍按云端出片" % str(e)[:100],
              flush=True)
        return ""
    if profile.get("default"):
        return _apply_env(profile)
    return "已登记 %s（未设为默认）" % VENDOR


def revert_env_if_mine(why: str = "") -> str:
    """当前 env 指向本厂商时把它退回出厂厂商。

    ⚠️ 只在 env **确实是 comfyui** 时改：人显式设成别家（或 A/B 的对照臂设成
    agnes）时，无条件退回会把他的显式值一起抹掉 —— 那是覆盖调用方的意图。
    """
    from . import vendors
    if (os.environ.get(_env_key()) or "").strip().lower() != VENDOR:
        return "视频厂商当前是 %s，不是 %s ⇒ 未改动" % (vendors.current("video"), VENDOR)
    os.environ[_env_key()] = vendors.DEFAULT
    msg = "视频厂商已退回 %s%s" % (vendors.DEFAULT, ("（%s）" % why if why else ""))
    print("[local] " + msg, flush=True)
    return msg


def set_default(flag: bool) -> dict:
    """改「是否本机默认」。没有档案 ⇒ 报错，不静默成功。"""
    from . import vendors
    profile = load_profile()
    if not profile.get("address"):
        raise ValueError("还没有接入档案 ⇒ 请先探测并接入一次")
    profile["default"] = bool(flag)
    _save_profile(profile)
    vendors.register(VENDOR, spec_of(profile), replace=True)
    source = _apply_env(profile) if flag else revert_env_if_mine("撤销本机默认")
    return {"ok": True, "default": bool(flag), "source": source,
            "profile": _profile_view(profile)}


def disconnect() -> dict:
    """撤销接入：清 env、删厂商档、删档案与工作流存档。

    ⛔ 不会去动 `settings.env`（出厂值不是我们写的，也不该由我们改）。
    """
    profile = load_profile()
    revert_env_if_mine("已断开本机出片服务")
    from . import vendors
    vendors.unregister(VENDOR)
    removed = []
    for p in (profile_path(), workflow_path()):
        try:
            if p.is_file():
                p.unlink()
                removed.append(p.name)
        except Exception as e:  # noqa: BLE001
            print("[local] ⚠ 删不掉 %s（%s）" % (p.name, str(e)[:60]), flush=True)
    msg = "已断开本机出片服务，视频厂商回到 %s" % vendors.current("video")
    return {"ok": True, "message": msg, "removed": removed,
            "profile": _profile_view(profile)}


def status() -> dict:
    """面板打开时的一屏：档案、当前厂商、为什么是它。**只读，不改任何东西。**"""
    from . import vendors
    profile = load_profile()
    return {
        "profile": _profile_view(profile),
        "current_video_vendor": vendors.current("video"),
        "current_image_vendor": vendors.current("image"),
        "registered": VENDOR in vendors.names(),
        "source_note": ("" if not profile.get("address") else decide(profile)[1]),
        "notes": _notes(profile) if profile else [],
    }
