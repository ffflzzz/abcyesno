# -*- coding: utf-8 -*-
"""定妆照 ↔ 角色卡 对账的**纯判据**（不打网络，可离线测）。

为什么需要这一层（2026-09-29 xianxia-60s4-0929 成片实测）：
角色卡写 `@沈砚（…玄黑高马尾…双手共持一柄断剑…左腕一串旧铜钱…）`，
而 `images/沈砚.png` 画的是**酒红发 + 一柄完整长剑 + 铜护腕**——成片 18 镜忠实继承了这张错图。
链上**没有任何一环**校验"画出来的 = 卡片写的"：
  · 内容指纹（`cast`）只解决"卡片改了 → 旧图作废"，卡片没改时它一律放行；
  · reviewer 判的是**文本对文本**（分镜锚点串 vs 角色卡），它看不到图。
⇒ 定妆照是全片身份的权威来源，权威本身却没有对账环节。这条补上。

分工（照本仓库既有的"语义交模型、判定归程序"纪律）：
  · 模型**只报观察**：逐项回答"图上这一项是什么样"，并要求**逐字复制卡片那一项**；
  · **判不判由代码定**：颜色族不相交 / 明确"没画" 才算矛盾；抄不出原文的条目一律不算。
★ 为什么不让模型直接判"符不符合角色卡"：它会把"暗赤"和"酒红"判成一致（同族近义），
  也会为了迎合提示词复述卡片——那是 2026-09-28 reviewer 编造阻断理由的同一个病。
"""
from __future__ import annotations

import json
import re

#: 颜色族（`铜` 故意不收：它是材质，"铜制发冠"画成金色不该算矛盾）
COLOR_FAMILIES = {
    "黑": ("玄黑", "墨黑", "漆黑", "乌黑", "黑"),
    "白": ("霜白", "月白", "素白", "银白", "乳白", "白"),
    "红": ("暗赤", "绯红", "朱红", "赤", "红", "酒红"),
    "蓝": ("深蓝白", "深蓝", "藏蓝", "蔚蓝", "蓝"),
    "青": ("青碧", "青灰", "青", "碧", "翠"),
    "紫": ("紫",),
    "金": ("鎏金", "金黄", "金"),
    "银": ("素银", "银灰", "银"),
    "灰": ("灰",),
    "棕": ("棕", "褐", "赭"),
    "粉": ("粉",),
    "绿": ("绿",),
    "黄": ("黄",),
    "橙": ("橙",),
}

#: 只有描述**长相/着装/兵刃**的条目可对账（"眼神冷"这种没法从像素核）
_VISUAL_NOUNS = ("发", "马尾", "髻", "鬓", "刘海", "头", "脸", "眉", "眼", "肤",
                 "袍", "裙", "衣", "衫", "装", "袖", "裤", "靴", "鞋", "带",
                 "冠", "簪", "链", "镯", "腕", "疤", "纹",
                 "剑", "刀", "枪", "戟", "扇", "壶", "盒", "环", "盾")

#: 数量类硬项（"一柄/双手共持/断"画成两柄/完整 = 矛盾，与颜色无关）
_COUNT_MARKS = (("断", ("完整", "未断", "整柄")),)

#: **光效类条目不对账**（2026-09-29 真卡实测）：定妆照是白底服装表，
#: 「剑身残留暗赤雷光」「剑尖拖短促电弧」这类能量表现由**视频层**画，
#: 要求表上有 = 每次必判矛盾 ⇒ 白烧一次重画。
_GLOW_WORDS = ("雷光", "电弧", "光效", "流光", "光晕", "发光", "特效")

ASK_PROMPT = """下面是一张**人物设定参考图**（白底服装表），以及这个角色卡片上逐条写明的外形项。
你的任务**只是逐项报告图上有没有、长什么样**，不是评判画得好不好。

对每一项回答三格：
  item   —— **逐字复制**卡片里那一项（不要改写，抄不出来就别编）
  seen   —— 只回答"**这一项在图上存不存在**"：
            "是" = 图上画了这一项（哪怕细节与卡片有出入）
            "否" = 图上**根本没有**这一项（例：卡片写铜钱串，手腕上什么饰品都没有）
  detail —— 图上这一项**实际长什么样**，尤其**颜色照你看到的写**
            （例："头发是酒红色"、"发冠是金色"、"双手共持一柄完整长剑"）

★ 程度与款式的出入（袖子偏宽、衣摆偏长、颜色深浅）**写进 detail，seen 仍填 "是"**——
  判不判由程序按 detail 定，你不要替它下结论。
⛔ 不要照抄卡片措辞来回答 detail——卡片写"玄黑"而图上是红的，你就得写红的。

卡片条目（逐字）：
%s

只输出 JSON：{"items": [{"item": "...", "seen": "是", "detail": "..."}]}"""


def _split_top(s: str) -> list[str]:
    """按 、／，／； 切，但**括号内部不切**。

    实测瑕疵（2026-09-29 真卡）：角色卡写「双手共持一柄断剑（全片只有一把剑、
    不是双手各持一把）—— 玄铁质地修长长剑自中段折断」，无脑按 `、` 切会把一条
    要求切成两个半截碎片，模型只能逐字复述碎片 ⇒ 判据在比对自己的切分错误。
    """
    out, buf, depth = [], [], 0
    for ch in s:
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        if depth == 0 and ch in "、，,；;":
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf))
    return out


def items_of(appearance: str) -> list[str]:
    """把角色卡外貌段切成可对账条目（剥掉 `@名（…）` 的壳，括号内不切）。"""
    s = (appearance or "").strip()
    s = re.sub(r"^@[^（(]*[（(]", "", s)
    s = re.sub(r"[)）]\s*$", "", s)
    out = []
    for part in _split_top(s):
        p = part.strip().strip("。.")
        if len(p) >= 3 and any(n in p for n in _VISUAL_NOUNS):
            out.append(p)
    # 光效条目剔掉（见 `_GLOW_WORDS`）：白底设定表上没有雷光是**对的**
    return [p for p in out if not any(g in p for g in _GLOW_WORDS)]


def colors(text: str) -> set[str]:
    """颜色族集合。同一字命中多族（"深蓝白"→蓝+白）是故意的：只要不相交才算矛盾。"""
    got = set()
    for fam, words in COLOR_FAMILIES.items():
        for w in words:
            if w in (text or ""):
                got.add(fam)
                break
    return got


def parse_reply(text: str) -> list[dict]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:                                        # noqa: BLE001
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [x for x in (items or []) if isinstance(x, dict)]


def judge(appearance: str, reply: str) -> dict:
    """返回 `{"mismatch": [(项, 图上所见, 类别)], "unchecked": [项…]}`。

    类别：`颜色矛盾` / `未画出` / `兵刃形态矛盾`。
    ★ 逐字对不上卡片条目的一律丢进 `unchecked`（模型编造的条目不算阻断）——
      这是 2026-09-28「审稿角色编造阻断理由」那条教训的直接移植。
    """
    cards = items_of(appearance)
    by_norm = {_norm(c): c for c in cards}
    mismatch, hit = [], set()
    for row in parse_reply(reply):
        raw = str(row.get("item") or "").strip()
        key = _norm(raw)
        if key not in by_norm:
            continue
        card = by_norm[key]
        hit.add(key)
        seen = str(row.get("seen") or "").strip()
        detail = str(row.get("detail") or "")
        if seen == "否":
            mismatch.append((card, detail or "图上未画出", "未画出"))
            continue
        cc, dc = colors(card), colors(detail)
        if cc and dc and not (cc & dc):
            mismatch.append((card, detail, "颜色矛盾"))
            continue
        for mark, wrongs in _COUNT_MARKS:
            if mark in card and any(w in detail for w in wrongs):
                mismatch.append((card, detail, "兵刃形态矛盾"))
    unchecked = [c for c in cards if _norm(c) not in hit]
    return {"mismatch": mismatch, "unchecked": unchecked}


def _norm(s: str) -> str:
    return re.sub(r"[\s，。、；;：:（）()「」“”\"']", "", s or "")


def ask_text(appearance: str) -> str:
    cards = items_of(appearance)
    return ASK_PROMPT % "\n".join("- %s" % c for c in cards)
