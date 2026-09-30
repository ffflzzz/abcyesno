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


def generate_images(payload: dict) -> list[str]:
    """OpenAI 形状的请求 → 图片 URL 列表。真正出图仍走 `providers.gen_image`。

    抛 `providers.RateLimitError` / `RuntimeError` 由调用方转成 HTTP 错误 ——
    这里不吞异常：静默返回空列表会让画布显示"生成成功但没有图"。
    """
    prompt = _prompt_text(payload)
    if not prompt:
        raise ValueError("prompt 为空")
    ratio = ratio_from_size(payload.get("size"))
    try:
        n = int(payload.get("n") or 1)
    except (TypeError, ValueError):
        n = 1
    n = max(1, min(n, MAX_IMAGES_PER_REQUEST))
    refs = payload.get("reference_images") or payload.get("image") or None
    if isinstance(refs, str):
        refs = [refs]
    urls: list[str] = []
    for _ in range(n):
        _unused, url = providers.gen_image(prompt, refs=refs, ratio=ratio)
        urls.append(url)
    return urls


def image_response(urls: list[str]) -> dict:
    """OpenAI 的 `/images/generations` 响应形状（画布按 `data[0].url` 取图）。"""
    return {"created": int(time.time()),
            "data": [{"url": u, "revised_prompt": ""} for u in urls]}


def models_payload() -> dict:
    """OpenAI 形状的 `/v1/models`，让画布的模型下拉里能看到真实可用的模型。

    ⚠️ 只列**后端真能服务**的（生图 + 文本）。列了生视频会让画布的
    视频按钮变成"能点但永远转圈"——那比直接没有更糟。
    """
    ids = [m for m in (config.MODELS.get("image"), config.MODELS.get("chat")) if m]
    return {"object": "list",
            "data": [{"id": m, "object": "model", "created": int(time.time()),
                      "owned_by": "shortdrama"} for m in ids]}
