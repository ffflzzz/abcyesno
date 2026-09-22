# -*- coding: utf-8 -*-
"""厂商档（vendor registry）—— **数据，不是类**。

## 为什么是 dict 而不是 Protocol / 抽象基类

旧架构有 `VideoProvider` Protocol + `PROVIDERS` 注册表，它是
「**只有一个供应商时提前抽象**」的产物 —— 接口形状全凭想象、**从未被第二个实现验证过**，
已随旧架构整体删除（`v5/` 里零残留）。
⇒ 这里只做**最薄的接缝**：一份厂商档数据 + 一个选择开关。

接一家新厂商的成本（这是本模块存在的全部理由）：

| 厂商类型 | 动作 |
|---|---|
| OpenAI 兼容（聚合平台类）| **加一条 dict** —— 见 `register()` |
| 自有协议（Kling / 即梦 / ComfyUI 类）| **加一个模块** `v5/media/vendors/<名>.py` —— 见 `impl_for()` |
| 只是换型号 | 改 `.env` 里的 `AGNES_*_MODEL`，**与厂商无关** |

## ★ 为什么这些取值是**函数**而不是模块级常量

与 `config.video_submit_interval_per_key()` 同一个理由：
常量会在 **import 期固化**，而运行期/测试会改 `config` ⇒ 变成**第二份真相源**
（本项目为此出过事故：全局闸门被 patch 成 0，per-key 闸门却还是 65，单测凭空 sleep 65 秒）。
故：**一律调用时实时读** `config` / `os.environ`。

## 两条硬约定（调用方依赖，勿改）

1. **缺省厂商 = `agnes`**，且 agnes **不引入任何 impl 模块**（走 `providers.py` 里的内置实现）
   ⇒ 不设环境变量时，行为与改造前**逐字节一致**。
2. **未注册的厂商名 ⇒ 响亮报错**（`UnknownVendor`），**绝不静默回退 agnes**。
   静默回退 = 「你以为在跑 A，其实跑的是 B」—— 本项目最忌的「失败不可见」。
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

from . import config

#: 自有协议厂商的实现模块放在这个子包下（默认前缀）。
#: 需要放别处的厂商，可在档里给**完整点分路径**（`impl` 含 "." 即按全路径导入）。
IMPL_PACKAGE = "v5.media.vendors"

#: 选择一个厂商的环境变量（按能力分开选；三条链可各选各的）。
ENV_KEY = {
    "chat": "SHORTDRAMA_CHAT_VENDOR",
    "image": "SHORTDRAMA_IMAGE_VENDOR",
    "video": "SHORTDRAMA_VIDEO_VENDOR",
}

#: **旧选择变量**（2026-09-18 统一前，文本通道只有它）。主变量未设时回落。
#: 保留是为了不让已有 `.env` 失效 —— 但新配置应写 `SHORTDRAMA_CHAT_VENDOR`。
ENV_LEGACY = {
    "chat": "NEWDEEP_LLM_PROVIDER",
}

DEFAULT = "agnes"

#: 产地记录里"查不出来源"时用的显式占位。
#:
#: 为什么不用空串/不写这个字段：**缺字段与"未知"是两件事** ——
#: 缺字段读作"这份产物没记录"（可能是忘了写）；`UNKNOWN` 读作
#: "确实查不出来"（例如复用盘上静帧、而它的来源已不可考）。
#: 本项目最忌把"不知道"伪装成"没有"。
UNKNOWN = "unknown"


class UnknownVendor(ValueError):
    """选了一个没注册的厂商名。

    **故意抛异常而不是回退**：回退会让"换厂商没生效"变得不可见 ——
    你会以为在测新厂商，其实一直在用 agnes。

    ★ 继承 `ValueError` 是**有意的**：`v5/server.py` 的 handler 已用
    `except ValueError → 400` 接住参数类错误 ⇒ 前端传了未注册的厂商名会
    **立刻拿到 400**，而不是等子进程跑起来才在日志里报（早失败一次胜过白排一个 run）。
    """


class VendorConfigError(ValueError):
    """厂商已注册，但**配置不全**（如文本模型名为空）。

    为什么单独一个类：这类错误的**修法不同** —— 不是"名字写错了"，
    而是"某个环境变量还没填"。报错消息里会**指名道姓**告诉你填哪个变量。
    实测背景：旧实现允许模型名为空，构造出一个 `model=""` 的客户端，
    报的错离根因很远。
    """


# ─── 内置档：agnes ───────────────────────────────────────────────────────────
#
# 取值与 `providers.py` 改造前**逐字对应**（就是为了保证缺省路径零变化）：
#   image: POST {base}/v1/images/generations，body{model,prompt,size:"1K",ratio,n:1}
#          + extra_body{image:[...≤5], response_format:"url"}；429→RateLimitError
#   video: POST {base}/v1/videos，body{model,prompt,mode,size:"720P",aspect_ratio,seconds}
#          查询 GET {base}/agnesapi?video_id=&model_name=
#   seconds 由 `providers` 双向钳到 [4, config.VIDEO_MAX_SECONDS]（配置里写明 API 硬约束 4–12）
#
# 详见 `../docs-archive-20260918/换模型操作清单-spec.md` §4（新视觉模型必须满足的语义契约）。
AGNES = {
    "label": "Agnes（默认，内置实现）",
    "impl": None,                       # None ⇒ 走 providers.py 的内置实现
    "auth": "bearer",                   # 鉴权方式
    "base_from_config": "AGNES_BASE",   # 调用时读 config.AGNES_BASE（保持旧 env 行为）

    # 生图
    "image_path": "/v1/images/generations",
    "image_size": "1K",
    "ratio_key": "ratio",               # 比例字段叫什么名（默认值取 config.STILL_RATIO）
    "ref_max": 5,                       # 参考图上限（官方：images ≤5）

    # 生视频
    "video_path": "/v1/videos",
    "video_size": "720P",
    "aspect_key": "aspect_ratio",       # 比例字段名（默认值取 config.ASPECT_RATIO）
    "seconds_min": 4,                   # 与 config.VIDEO_MAX_SECONDS 构成钳制区间
    "query_path": "/agnesapi",
    "query_model_key": "model_name",    # 查询必须带模型名（官方要求）

    # 能力声明（供人读 / 日后收敛用；**目前不参与任何判据**）
    "native_audio": True,               # 盘上实测：Agnes 视频自带音轨
    "output_delivery": "url",           # 产物以可下载 URL 返回

    # ─── 文本 / 推理通道（`v5/llm.py::chat_for` 用）──────────────────────────
    # 2026-09-18 统一：文本通道原先走 `config.LLM_PROVIDERS`（另一套注册表），
    # 现并入本模块 ⇒ 三条链一个机制、一种错误风格。
    # `base_suffix` 是**文本通道特有**的：OpenAI 兼容端点在 `/v1/chat/completions`，
    # 而媒体侧是直接 `base + image_path`（路径里自带 `/v1`），故这里要单独补 `/v1`。
    "chat": {
        "base_from_config": "AGNES_BASE", "base_suffix": "/v1",
        "key_from_config": "AGNES_API_KEY",
        "model_from_config": "MODELS.chat",
    },
}

#: DeepSeek（备用档）。**当前不可用**：`DEEPSEEK_MODEL` 在 `.env` 里是空的
#: （原模型名 2026-09-10 过期后清空）。选了它会**响亮报错**并指名要填哪个变量 ——
#: 这是有意为之：旧实现在模型名为空时会构造出一个 `model=""` 的客户端，
#: 报的错离根因很远（见 `../docs-archive-20260918/多厂商接入-spec.md` §1.1）。
DEEPSEEK = {
    "label": "DeepSeek（备用；需先填 DEEPSEEK_MODEL）",
    "impl": None,
    "auth": "bearer",
    "chat": {
        "base_env": "DEEPSEEK_BASE", "base_default": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
    },
}

#: 注册表。加一家 = 往这里加一条（或运行期调 `register()`）。
_REGISTRY: dict[str, dict] = {
    DEFAULT: AGNES,
    "deepseek": DEEPSEEK,
}


def register(name: str, spec: dict, *, replace: bool = False) -> dict:
    """登记一个厂商档。返回该档（便于链式使用）。

    `replace=False`（默认）时重复登记同名会报错 —— 静默覆盖会让
    "我明明注册了却没生效"变成不可见问题。
    """
    key = str(name or "").strip().lower()
    if not key:
        raise ValueError("厂商名不能为空")
    if key in _REGISTRY and not replace:
        raise ValueError("厂商 %r 已注册（要覆盖请传 replace=True）" % key)
    _REGISTRY[key] = dict(spec or {})
    return _REGISTRY[key]


def names() -> list[str]:
    """已注册的厂商名（排查/日志用）。"""
    return sorted(_REGISTRY)


def get(name: str) -> dict:
    """取厂商档。未注册 ⇒ `UnknownVendor`（**不静默回退**）。"""
    key = str(name or "").strip().lower()
    if key not in _REGISTRY:
        raise UnknownVendor(
            "厂商 %r 未注册。已注册：%s\n"
            "  · OpenAI 兼容类 ⇒ 在 v5/media/vendors.py 加一条 register(名字, {...})\n"
            "  · 自有协议类   ⇒ 加模块 v5/media/vendors/%s.py（实现 gen_image/"
            "submit_video/query_video）\n"
            "  · 接公司内网 ComfyUI 的步骤见 ../docs-archive-20260918/公司机接入ComfyUI-交接单.md"
            % (key, ", ".join(names()) or "（空）", key)
        )
    return _REGISTRY[key]


def current(kind: str) -> str:
    """当前选中的厂商名。

    `kind` ∈ {"chat", "image", "video"}。读对应的 `SHORTDRAMA_*_VENDOR`；
    文本通道还会**回落旧变量** `NEWDEEP_LLM_PROVIDER`（统一前的唯一入口）。
    全空 ⇒ `agnes`。
    """
    k = str(kind or "").strip().lower()
    env = ENV_KEY.get(k)
    if env is None:
        raise ValueError("未知能力 %r（仅 %s）" % (kind, " / ".join(sorted(ENV_KEY))))
    v = (os.environ.get(env) or "").strip()
    if not v:
        legacy = ENV_LEGACY.get(k)
        if legacy:
            v = (os.environ.get(legacy) or "").strip()
    return v.lower() or DEFAULT


def spec_for(kind: str) -> dict:
    """当前厂商的档（未注册 ⇒ 抛 `UnknownVendor`）。"""
    return get(current(kind))


def _config_get(path: str):
    """按点路径取 `config` 里的值（如 `"MODELS.chat"`）；取不到 ⇒ None。"""
    cur = config
    for part in str(path or "").split("."):
        if not part:
            continue
        cur = cur.get(part) if isinstance(cur, dict) else getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def _pick(sec: dict, prefix: str) -> str:
    """三级取值：`<prefix>_env`（环境变量名）> `<prefix>_from_config`（config 点路径）
    > `<prefix>_default`。

    一律**调用时**解析 —— 理由同 `config.video_submit_interval_per_key()`：
    import 期固化成常量会变成**第二份真相源**（测试 patch 了 config 却不生效）。
    """
    env_name = sec.get(prefix + "_env")
    if env_name:
        v = (os.environ.get(env_name) or "").strip()
        if v:
            return v
    cfg_path = sec.get(prefix + "_from_config")
    if cfg_path is not None:
        v = _config_get(cfg_path)
        if v is not None:
            return str(v)
    return str(sec.get(prefix + "_default") or "")


def base_for(kind: str, key: str | None = None) -> str:
    """当前厂商的**媒体**服务地址（`base_suffix` 补在末尾，文本通道才用得上）。

    `key` 给了、且该 key 在 `config.AGNES_KEY_BASE` 里配了**专属地址**
    （`AGNES_API_KEYS` 的 `key@base` 语法，2026-09-22 国内/国际混用）→ 用它；
    否则走全局（`base_from_config` → `config.AGNES_BASE`）——**默认行为零变化**。
    """
    own = _own_base_for(key)
    if own:
        return own
    spec = spec_for(kind)
    return _pick(spec, "base") + str(spec.get("base_suffix") or "")


def _own_base_for(key: str | None) -> str:
    """该 key 的专属地址（没配/厂商非 agnes/非媒体通道 → 空串）。"""
    k = (key or "").strip()
    if not k:
        return ""
    try:
        spec = spec_for("video")
    except Exception:  # noqa: BLE001 —— 厂商未注册等场景不在这里响亮，交给正常路径
        return ""
    if not spec.get("base_from_config") == "AGNES_BASE":
        return ""                     # 只有内置 agnes 档支持 key@base（别的厂商未接线）
    return _config_get_base(k)


def _config_get_base(key: str) -> str:
    """读 `config.AGNES_KEY_BASE[key]`（**调用时**解析，测试可 patch）。"""
    m = getattr(config, "AGNES_KEY_BASE", None) or {}
    return str(m.get(key, "") or "")


def chat_spec(name: str = "") -> dict:
    """文本通道的 `{base_url, api_key, model}` —— `v5/llm.py::chat_for` 的**唯一取值入口**。

    `name` 空 ⇒ 用当前选中的厂商。

    两种失败都**响亮**（这是本次统一的目的）：
      · 厂商未注册 ⇒ `UnknownVendor`
      · 已注册但**文本模型名为空** ⇒ `VendorConfigError`，且**指名要填哪个变量**

    旧行为（统一前）是"取不到就**静默回退 agnes**" —— 最坏情况是
    「以为在跑别家、其实一直是 agnes」，正是本项目最忌的「失败不可见」，故刻意改掉。
    """
    vname = (name or "").strip().lower() or current("chat")
    spec = get(vname)                        # 未注册 ⇒ UnknownVendor
    sec = spec.get("chat")
    if not isinstance(sec, dict):
        usable = [n for n in names() if isinstance((get(n) or {}).get("chat"), dict)]
        raise UnknownVendor(
            "厂商 %r 没有「文本」能力（档里缺 `chat` 段）。有文本能力的：%s"
            % (vname, ", ".join(usable) or "（无）"))
    model = _pick(sec, "model")
    if not model:
        hint = (sec.get("model_env") or sec.get("model_from_config") or "对应的模型名配置")
        raise VendorConfigError(
            "厂商 %r 的**文本模型名为空** ⇒ 请设置 %s，或把 SHORTDRAMA_CHAT_VENDOR 换成别家"
            % (vname, hint))
    return {"base_url": _pick(sec, "base") + str(sec.get("base_suffix") or ""),
            "api_key": _pick(sec, "key"),
            "model": model}


def impl_for(kind: str):
    """当前厂商的**实现模块**；内置厂商（agnes）返回 `None`。

    `None` 的含义是"用 `providers.py` 里的内置实现" —— 那条路径是零补偿的，
    不是"没有实现"。判断依据是档里的 `impl` 字段。

    模块需实现与 `providers` 同签名的 `gen_image` / `submit_video` / `query_video`
    （需要哪些由调用方决定；只需图片的厂商可以只实现 `gen_image`）。
    """
    spec = spec_for(kind)
    mod_name = spec.get("impl")
    if not mod_name:
        return None
    if not isinstance(mod_name, str):
        return mod_name                     # 已直接给了模块对象（测试/内嵌用）
    full = mod_name if "." in mod_name else (IMPL_PACKAGE + "." + mod_name)
    return importlib.import_module(full)


def impl_dir() -> Path:
    """自有协议厂商实现模块的落点（给"从这里开始写"的提示用）。"""
    return Path(__file__).resolve().parent / "vendors"


def describe(kind: str = "") -> str:
    """一行清单（日志/排查用）：当前选了谁、有哪些可选。

    形如 `[vendor] chat=agnes（内置）· image=agnes（内置）· video=agnes（内置）· 已注册[agnes,deepseek]`
    """
    buf = []
    for k in (["chat", "image", "video"] if not kind else [kind]):
        name = current(k)
        try:
            who = "内置" if get(name).get("impl") is None else "自实现"
            buf.append("%s=%s（%s）" % (k, name, who))
        except UnknownVendor:
            buf.append("%s=%s（★未注册！）" % (k, name))
    return "[vendor] " + " · ".join(buf) + " · 已注册[%s]" % ",".join(names())
