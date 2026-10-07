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
#: ⚠️ 2026-09-30 首次上真链路又报了 4 条同型假矛盾，这张表随之扩到**渲染风格词**：
#:   「瓷感次表面散射肤质」「成片发丝高光」「挥出时拖一片扁平剑罡」描述的是
#:   **成片怎么渲染 / 出招时发生什么**，白底设定表结构性地不可能满足它们。
#:   留窄表（只剔这些）而不放宽判据：`左肩旧疤`、`深灰长发松束低髻` 这类是真身份项，
#:   正是这条判据要拦的（同日 ep2 就靠它重画救回了手背旧疤）。
_GLOW_WORDS = ("雷光", "电弧", "光效", "流光", "光晕", "发光", "特效", "剑罡", "罡气",
               "肤质", "次表面", "散射", "瓷感", "高光", "哑光", "通透", "反光", "质感")

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


# ─── 资产图（道具 / 场景）对账（2026-10-07 加）────────────────────────────────
# ★ 为什么必须补这一块：角色定妆照有对账（上面那一套），**道具与场景没有任何对账**。
#   实测代价（`xianxia-zhongzhui-1007`）：「青霜双鞭」画成一个**穿白衬衫的现代男人
#   两手举着蓝色绳圈** —— 图上主体的类型就错了，而它一路无人拦；
#   若不是这条路线当时不喂道具图，它会被原样送进视频请求。
#
# 判据刻意**窄**（只抓"类型错 / 什么都没画 / 多主体"这三种明确的）：
#   颜色深浅、款式细节这类审美差异**不判** —— 判宽了会把每次生成都变成一次赌。

ASK_ASSET = """下面是一张**资产参考图**（道具或场景的空镜），以及这张卡的名称与规格。
你的任务**只是报告你看见了什么**，不是评判画得好不好。

回答四格：
  subject  —— 图上主体是什么（一句话，照你看到的写）
  person   —— 图上有没有**清晰可辨的真人**（是 / 否）。半身、全身、正脸侧脸都算"是"；
              纯剪影、模糊人影不算。
  colors   —— 主体与主要配件的颜色，照你看到的写（例："深青灰的金属，握把缠绕浅褐布条"）
  notes    —— 有没有多件不相关的主体、有没有分格拼图、有没有可读文字（一句话）

⛔ 不要照抄卡片的措辞来回答 —— 卡片写"细长青霜鞭"而图上是别的，你就写你看见的。

卡片：%s（%s）
规格（逐字）：%s

只输出 JSON：{"subject": "...", "person": "是", "colors": "...", "notes": "..."}"""


def ask_asset(card: dict) -> str:
    return ASK_ASSET % (card.get("name") or "", card.get("type") or "",
                        card.get("prompt") or "")


def _reply_obj(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except Exception:                                        # noqa: BLE001
        return {}
    return d if isinstance(d, dict) else {}


def judge_asset(card: dict, reply: str) -> list[tuple[str, str]]:
    """资产图 ↔ 资产卡：返回 `[(类别, 依据)]`，空 = 没抓到明确问题。

    三类，每类都对应一次实测或明确的推理，⛔ **不判审美**（深浅/款式不算）：
      · 主体是真人  —— 卡片是道具/场景，图上却画了清晰的人（白衬衫男人那次）；
      · 未画出      —— 模型答不出主体（空白 / 纯色背景）；
      · 多主体      —— 图上出现多件不相关主体或分格拼接（会污染视频请求）。
    模型答不上来的（JSON 解析失败 / 关键字段缺失）一律**不算**问题 ——
    判据是概率性的，宁可漏一次也不要每次生成都报红。
    """
    d = _reply_obj(reply)
    if not d:
        return []
    out = []
    if str(d.get("person") or "").strip() in ("是", "有", "yes", "true"):
        out.append(("主体是真人", "图上画了清晰的人，而这张卡是%s「%s」"
                    % ("道具" if card.get("type") == "prop" else "场景",
                       card.get("name") or "")))
    # ⚠️ 只认"**模型确实答了这一格、但答的是空**"；JSON 里根本没有 subject 这个键
    #    = 它没按格式答 ⇒ 算"答不上来"，不判（与函数尾那句同一口径）。
    subj = str(d.get("subject") or "").strip()
    if "subject" in d and (not subj or subj in ("无", "空", "没有", "无主体")):
        out.append(("未画出", "模型答不出主体：%s" % (subj or "（空）")))
    notes = str(d.get("notes") or "")
    if any(k in notes for k in ("多件", "分格", "拼图", "拼接", "多个主体")):
        out.append(("多主体", notes[:80]))
    return out
