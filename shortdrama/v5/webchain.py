# -*- coding: utf-8 -*-
"""Web 侧的**创作链**通道（P2b）：剧本解析建项目 + 跑 supervisor 链出分镜。

## 与 `runner.py` 的分工

- `runner.py`   —— 台账 / spawn / 取消 / 判死（**通用机制**），P2b 让它多接一个 kind
- `webchain.py` —— 创作链特有的两件事：**剧本→brief 的提炼** 与 **dev server 生命周期**

## ★ D6：项目目录是「编译期绑定」的

`v5/orchestrator.py:51-55`：

    _PROJECT = os.environ.get("SHORTDRAMA_V5_PROJECT", "studio")
    _root: Path = config.PROJECTS_DIR / _PROJECT        # 模块导入时固化

⇒ **一个 dev server 只能服务一个项目**；换项目必须**重启**（约 15 秒）。
⇒ 端口必须与 `SHORTDRAMA_V5_AGENT_URL`（默认 2024）**一致**，否则 supervisor
   经 HTTP 派发子任务会**全失败**（静默失败 —— `AGENTS.md:355`）。故这里启动时断言。

启动/重启的次序照抄 `scripts/run_new_project.py`（那是跑通过 20+ 部的脚本）：
**归档 `.langgraph_api` → 起 dev → 等 `/ok`**。

## 链怎么驱动

**复用 `scripts/drive_chain.py`**（生产驱动，已处理 SDK 调用、批次循环、
`interrupted` → `command={"resume":{"decisions":[...]}}` 的 HITL 闭环）。
**不重写** —— 重写一份 SDK 调用必然与它漂移。

## 剧本解析要不要 LLM

要。`brief.json` 的 11 个必填字段里，`must_have`（四幕具体事件）、`key_props`、
`tone`、`结局` 都**必须是有内容的创作决定**，从原文机械抽取做不到。
这就是 spec §15-Q4 记的那一步。
**但**：LLM 只负责**提炼**，缺字段**不猜**——一次带"缺哪些"的重试，仍缺就报错。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config, validate

#: ⛔ **不要硬编类型包列表** —— 它会的（2026-09-16 实测）。
#:
#: 我原先写死 `("shortdrama", "3d-animation", "niulai-movie-style")`，
#: 结果项目当天新增了第 4 个包 `wool-felt-story-short`（羊毛毡风格）后：
#: **显式传这个包会被静默误拒** → 回落 `shortdrama`，而且连一句说明都没有
#: （explicit 分支不写 note）→ 就是本项目最忌的「选了 A 实际跑 B」。
#:
#: 真相源只有一个：`v5/skills/packs/` 下的目录（`craft` 是技法库、不是包）。
#: `webmap.styles()` 也是这么读的 —— 两处必须同源。
_PACKS_EXCLUDE = ("craft",)


def known_packs() -> list:
    """当前真实存在的类型包（目录即真相源）。"""
    d = config.SKILLS_DIR / "packs"
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.is_dir() and p.name not in _PACKS_EXCLUDE
                  and not p.name.startswith((".", "_")))


#: 前端「风格库」的风格码 → v5 类型包。
#:
#: ⛔ 只映射**能明确对应的**；其余落到 `shortdrama` 并在响应里**说明**。
#:    不硬编一张大表去假装能映射 Pavo 的 20+ 风格（那会制造"选了 A 实际跑 B"的静默偏差）。
PACK_HINTS = (
    # ⚠️ `3d-animation` 的三条映射（3d / animation / cartoon）已于 2026-09-16 **随该包铲除而删除**。
    #    删掉后 `realpeople_3d_style` 这类风格码会**回落 shortdrama 并说明**（机制不变，见 resolve_pack）。
    ("niulai", "niulai-movie-style"),
    ("bootleg", "niulai-movie-style"),
    ("lowpoly", "niulai-movie-style"),
    ("wool", "wool-felt-story-short"),
    ("felt", "wool-felt-story-short"),
)

DEV_STATE = ".tmp/web-devserver.json"
# Vendored for abcyesno: the port was a literal, so a packaged copy could never
# coexist with a dev checkout's `langgraph dev` on 2024. Env-overridable now.
DEV_PORT = int(os.environ.get("SHORTDRAMA_DEV_PORT", "2024"))
_ARCHIVE = ".langgraph_api"

#: ★ 前端「逐步人工确认」的总开关：起 dev server 时打开**步级 HITL**
#: （`interrupt_on={"task": True}` ⇒ 每派一个角色**之前**挂起等人点头）。
#:
#: ## 为什么只在这里设、而且**绝不能写进 `.env`**
#:
#: `config.py:16-27` 会把 `.env` 注入**全局** `os.environ`；而 `run_new_project.py`
#: 也起自己的 dev server（`run_new_project.py:294` 调 `drive_chain.py`）——
#: 写进 `.env` 会把**外部 agent 的全自动链路**一起停住，每次都挂死在第一步
#: （`config.py:197-199` 记的正是这件事）。
#: 只挂在 webchain 起的那个 dev server 上 ⇒ 外部 agent 完全不受影响。
#:
#: ## 两条必须记住的约束
#:
#: 1. **编译期生效**：`orchestrator.py:443` 是模块级 `build_supervisor()`，
#:    `_interrupt_on()` 在其中被调 ⇒ 改它**必须重启 dev**（约 15 秒）。
#: 2. **不能由前端 shim 读自己的环境变量判断**：dev server 与 shim 是**两个进程**，
#:    shim 读到的永远是"没开"（误报）。⇒ 起 dev 时把它记进状态文件，
#:    由 `webmap.hitl_state` 如实播报（"未知"就说未知）。
MANUAL_STEPS_ENV = "SHORTDRAMA_APPROVE_EACH_ROLE"

#: 期望值。**默认关**（创作链一口气跑完）—— 2026-09-20 用户反馈"每个角色都要
#: 确认一次太烦"。前端在「生成分镜脚本」的请求体里带 `manual_steps` 才逐步停。
#: 只影响 webchain 自己起的 dev server ⇒ CLI / 外部 agent 的链路不受任何影响。
#: 因为它**编译期生效**（见上面的约束 1），运行中的 server 与本值不一致时
#: `ensure_devserver` 会重启 dev（约 6 秒），而不是静默沿用旧行为。
_manual_steps = os.environ.get("SHORTDRAMA_WEB_MANUAL_STEPS", "0") != "0"


def manual_steps_on() -> bool:
    """本模块起的 dev server 是否开启逐步人工确认（当前期望值）。"""
    return _manual_steps


def set_manual_steps(on) -> bool:
    """设置期望值。返回**是否发生变化**（变了就意味着要重启 dev 才生效）。"""
    global _manual_steps
    want = bool(on)
    changed = want != _manual_steps
    _manual_steps = want
    return changed


# ─────────────────────────────────────────────────────── 类型包

def resolve_pack(style_code: str = "", explicit: str = "") -> tuple:
    """(pack, note)。**不确定就回落 + 说明**，绝不假装映射上了。

    ⚠️ 包的合法性以 **`known_packs()`（目录）** 为准：
    显式传了一个**不存在**的包名时，必须**说明被换掉了** ——
    静默换成别的包就是"选了 A 实际跑 B"（实测踩到：硬编 3 个包把第 4 个静默拒了）。
    """
    packs = known_packs()
    exp = str(explicit or "").strip()
    if exp:
        if exp in packs:
            return exp, ""
        return "shortdrama", ("指定的类型包 %r 不存在（当前有：%s）→ 已回落 shortdrama"
                              % (exp, "/".join(packs) or "无"))
    low = str(style_code or "").strip().lower()
    if low in packs:
        return low, ""
    for needle, pack in PACK_HINTS:
        if needle in low and pack in packs:
            return pack, "前端风格码 %r 按关键词映射到类型包 %r" % (style_code, pack)
    if style_code:
        return "shortdrama", ("前端风格码 %r 不对应任何 v5 类型包（当前有：%s）→ 已用 shortdrama"
                              % (style_code, "/".join(packs) or "无"))
    return "shortdrama", ""


# ─────────────────────────────────────────────────────── 项目名

def _slug(s: str) -> str:
    """ASCII kebab slug；非 ASCII 一律丢弃。"""
    t = re.sub(r"[^a-zA-Z0-9]+", "-", str(s or "")).strip("-").lower()
    return re.sub(r"-{2,}", "-", t)[:40]


def new_pid(slug: str = "") -> str:
    """新项目目录名。**必须与已有目录不冲突**（目录名即 pid）。"""
    base = _slug(slug) or ("paste-" + time.strftime("%m%d-%H%M"))
    pid, i = base, 2
    while (config.PROJECTS_DIR / pid).exists():
        pid, i = "%s-%d" % (base, i), i + 1
    return pid


# ─────────────────────────────────────────────────────── 剧本 → brief

def _brief_spec(pack: str, mode: str = "script", episodes: int = 1) -> str:
    """给 LLM 的 brief 契约。**结构 + 值都要规定**（只规定结构，模型会在值上自由发挥）。

    ⚠️ 示例**不用代码围栏包**：模型会把围栏一起抄进产物（本项目已踩过）。

    `mode`：
      · `script` —— 用户给的是**完整剧本**，任务是**提炼**（以原文为准，不许编新情节）
      · `idea`   —— 用户只给**一句创意**，任务是**创作**（四幕事件、道具、结局都要你设计）

    ## ★★ `episodes` 必须传进来（2026-09-19 修的真 bug）

    本函数原来**不接** `episodes`，契约里写死「`episodes`：整数，**固定填 1**」，
    而 `create_project` 事后又把它覆盖成用户选的值 ⇒ **模型按单集设计，我们偷偷改成多集**。
    实测（老夫子 2 集连续剧）产出的 brief 有多严重：
      · `must_have` 4 条**全是第 1 集的剧情**（怀表）⇒ 第 2 集（凉茶）**必被忠实度门拦下**；
      · `second_character` 缺失 ⇒ 老赵没有角色卡；
      · `禁忌` 里冒出「无老赵…出现」——**把角色禁掉**，而 `must_have` 又要求他出现。
    ⇒ 现在按集数给不同口径（单集=四幕；多集=**每集都能覆盖**的硬要求）。
    """
    mh_n = getattr(validate, "MIN_MUST_HAVE", 4)
    mh_len = getattr(validate, "MIN_MUST_HAVE_LEN", 12)
    ep_n = max(1, int(episodes or 1))
    multi = ep_n > 1
    if mode == "idea":
        head = """根据下面这个**创意**，创作一份短剧 `brief.json`（**唯一产出，不要任何解释文字**）。

这是**创作任务**：用户只给了一句点子，**具体事件、关键道具、结局都要你来设计**。
不要复述用户的原话，也不要写成"用户说…"这种转述。"""
        if multi:
            # ⚠️ 这里**不要**再列一条 `must_have`：上面 `mh_rule` 已经给了完整的多集口径，
            #    重复一条还会写"请按下面的要求写"——而下面已经没有了（悬空交叉引用）。
            #    **同一字段两份说明**正是本项目最忌的「同一判据写两份」，模型会挑一份照做。
            tail = """- `topic`：**系列名**（中文 6–14 字），取自全剧核心意象 —— 不要用只属于某一集的标题。
- `second_character`：**创意里出现的第二个具名人物必须填**（写法同 `protagonist`）。
  缺了 ⇒ 该角色没有角色卡 ⇒ 静帧里同一个人长出不同的脸。只有一个角色时填 ""。
- `结局`：**全剧**最后一个定格画面，一句。"""
        else:
            tail = """- `topic`：项目名，中文 6–14 字，取自你设计的剧情核心意象（不要书名号）。
- `second_character`：**创意里出现的第二个具名人物必须填**（写法同 `protagonist`）；
  确实只有一个角色时填 ""。不要为了省事把第二个角色写成空。
- `结局`：**定格画面**描述，一句（全片最后一眼看到什么）。"""
            tail = tail.replace("{n}", str(mh_n)).replace("{l}", str(mh_len))
    else:
        head = """把下面这份短剧剧本原文提炼成 `brief.json`（**唯一产出，不要任何解释文字**）。"""
        if multi:
            tail = """- `second_character`：剧本里出现的第二个具名人物必须填（写法同 `protagonist`）；没有就填 ""。"""
        else:
            tail = """- `second_character`：剧本里出现的第二个具名人物必须填（写法同 `protagonist`）；没有就填 ""。"""

    if multi:
        mh_rule = (
            "- `must_have`：数组，**至少 %d 条**，每条不少于 %d 字。\n"
            "  ★★ **本片是 %d 集连续剧**：`must_have` 必须是「**每一集都能覆盖**」的硬要求"
            "（例如「每集 老夫子 与 老赵 至少有一次肢体冲突」「每集出现的场景不超过 3 个」），\n"
            "  ⛔ **绝不要**写只属于第 1 集的**具体剧情**（哪一集发生什么事）——\n"
            "  链路的忠实度门是**按集**判的，写死成某一集的剧情 ⇒ **其余每一集都会被拦下**。\n"
            "  各集的剧情由 `plotdesigner` 按卷/集结构另行展开，不写在这里。"
            % (max(2, min(mh_n, 3)), mh_len, ep_n))
    elif mode == "idea":
        mh_rule = (
            "- `must_have`：数组，**至少 %d 条**，**每条是一个具体可拍的事件**（钩子/发展/转折/收尾），\n"
            "  每条不少于 %d 字 —— 这是**你要设计**的内容，不是从创意里抄。"
            "要能拍出来（有动作、有场景）。" % (mh_n, mh_len))
    else:
        mh_rule = (
            "- `must_have`：数组，**至少 %d 条**，**每条是一个具体可拍的事件**（钩子/发展/转折/收尾），\n"
            "  每条不少于 %d 字，且**直接取自剧本里真实发生的动作**，不要氛围描写。"
            % (mh_n, mh_len))

    return head + """

只输出一个 JSON 对象，键**恰好**是这 13 个（缺一个都算失败）：
topic, pack, genre, episodes, audio_mode, target_duration, protagonist,
second_character, must_have, key_props, 禁忌, tone, 结局

各键的要求（**值也要遵守**，不要只满足结构）：

- `pack`：固定填 `""" + pack + """`。
- `genre`：类型基调（如「乡村 / 现实主义温情短剧」），一行。
- `episodes`：整数，**固定填 """ + str(ep_n) + """**。
- `audio_mode`：二选一 —— `dialogue-led`（有台词，默认）或 `silent`（无台词）。
  ★ **必须显式填一个**：它会真的生效（开工前注入 `dialogue`/`scenedesigner`，
  reviewer 按分镜对白列做**确定性**校验；`dialogue-led` 而台词镜占比 <20% 会被判不通过并回退）。
  拿不准就填 `dialogue-led`。
- `target_duration`：一句话，含""" + ("**每集**的" if multi else "") + """时长目标与**镜数指引**。写法示例（数值按剧情定）：
  「约 120 秒（2 分钟），共 30 镜左右，每镜 4-12 秒（**默认 4 秒快切**；有台词的镜按台词字数适配），避免全表等长」
  ⚠️ **快切是节奏基线**（目标秒数 ÷ 4 = 镜数），**不要**写「每镜 6-8 秒 / 共 17 镜」——
  那是旧口径，会把节奏拖慢一倍、并让台词因装不进短镜而被砍。
  """ + ("★ **多集**时这里写的是**每一集**的时长（不是合计）。" if multi else "") + """
- `protagonist`：主角设定。**逐项写外貌**（脸型/发型/服装，具体到颜色材质）+ 一行
  「全片只用这一个固定人名」。
- `key_props`：数组，关键道具。每条是「精确命名 + 外观逐项特征」，并注明**全片逐字一致**。
""" + mh_rule + """
- `禁忌`：数组，只写**画面层**硬约束（如「无字幕、无烧录文字」「无第二张清晰人脸」「无背景音乐」）。
  ⛔ **绝对不要**写「**无<某角色>出现**」「不出现 X」这类**把角色禁掉**的条目：
  ① 它会与 `must_have` 直接冲突（一边要求他出场、一边禁止他出场）；
  ② `禁忌` 是**静帧 QC 的 P0 判据来源** ⇒ 每一镜只要有该角色就被判硬伤、无限重画。
  要限制的是**画面特征**（字幕/人脸数量/文字），**不是"谁出场"**。
  ⚠️ 写「**无字幕**」而不是「画面无可读文字」——后者会连**场景固有标识**
  （门牌 / 面板读数 / 文件抬头 / 包装标签）一起禁掉，逼分镜写出反物理的描述，
  而生成模型照样会把它们画出来 ⇒ 产出"违规" + QC 假警报。
- `tone`：渲染风格 + 光线色彩叙事 + 情绪弧，一段。
""" + (("★ **多集**：全剧的起承转合与走向也写在这里（不要塞进 `must_have`）。\n" if multi else "")) + tail + """

⚠️ 原文/创意里没写的，按类型惯例补，但**不要编造与它矛盾的角色或情节**。
⛔ **不许自相矛盾**：`must_have` 要求出现的人物，绝不能同时出现在 `禁忌` 里。
"""



def _parse_json_obj(text: str) -> dict:
    """从模型输出里稳健地取出 JSON 对象。

    归一化的三件事（都是踩过的）：
      ① 剥 ```json 代码围栏（契约里说了别用围栏，但模型仍可能加）；
      ② 取**最外层** `{...}`（模型可能前后带说明文字）；
      ③ 解析失败**抛错并带上原文片段** —— 静默返回 {} 会让"为什么没建成功"完全看不见。
    """
    s = str(text or "").strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    try:
        return json.loads(s)
    except Exception as e:                      # noqa: BLE001
        raise ValueError("模型没返回合法 JSON（%s）。原文前 300 字：%s"
                         % (e, s[:300]))


def _chat(messages, max_tokens: int = 4096) -> str:
    """一次 LLM 调用 → 文本。温度 0（提炼是**判定类**任务，要可复现）。"""
    from . import llm
    m = llm.chat_for(max_tokens=max_tokens, temperature=0)
    r = m.invoke(messages)
    c = getattr(r, "content", r)
    if isinstance(c, list):                     # 多段 content（部分供应商）
        c = "".join(str(x.get("text") if isinstance(x, dict) else x) for x in c)
    return str(c or "")


def _sanitize_brief(brief: dict, log=print) -> dict:
    """摘掉**自相矛盾 / 会毒化静帧 QC** 的 `禁忌` 条目，并**说出来**。

    ## 为什么必须做确定性兜底（2026-09-19 实测）

    老夫子 2 集那轮，模型把**角色描述**写成了禁忌条目：
    `"无老赵（光头浓须、紫红西服、粉红领带）出现"` —— 而 `must_have` 又**要求老赵出场**。
    后果双重：
      ① 契约自相矛盾；
      ② `禁忌` 是**静帧 QC 的 P0 判据来源** ⇒ 每一镜只要有老赵就被判硬伤 ⇒
         无限定向重画直到撞上限，最终带伤放行（纯烧配额）。

    这类条目**确定性可判**（「无<片中人物>出现/出镜」），所以不靠模型自觉 ——
    提示词已经写了规矩（见 `_brief_spec`），这里是**第二道**、不依赖模型的闸门。
    """
    if not isinstance(brief, dict):
        return brief
    names: list = []
    for k in ("protagonist", "second_character", "third_character"):
        v = str(brief.get(k) or "").strip()
        if not v:
            continue
        m = re.match(r"([^\s：:，,（(、/]{1,12})", v)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    items = brief.get("禁忌") or []
    if not names or not isinstance(items, list):
        return brief
    kept, dropped = [], []
    for t in items:
        s = str(t)
        if ("出现" in s or "出镜" in s) and any(n in s for n in names):
            dropped.append(s)
        else:
            kept.append(s)
    if not dropped:
        return brief
    if not kept:
        # ⚠️ `禁忌` 是**必填字段**（`validate.BRIEF_REQUIRED`）⇒ 摘空了就补一条安全默认，
        #    否则会把一份本来能用的 brief 变成"缺字段"。
        kept = ["无字幕、无烧录文字"]
    brief["禁忌"] = kept
    log("[webchain] ⚠️ 已摘掉 %d 条**把角色禁掉**的禁忌（与 must_have 冲突，且会毒化静帧 QC）：%s"
        % (len(dropped), " / ".join(x[:48] for x in dropped)))
    return brief


def extract_brief(script_text: str, pack: str, mode: str = "script",
                  episodes: int = 1, log=print) -> dict:
    """剧本原文 / 一句创意 → brief。**缺字段不猜**：一次带"缺哪些"的重试，仍缺就抛错。

    `mode="idea"` 时是**创作**（事件由模型设计），见 `_brief_spec`。
    ★ `episodes` **必须传进来**：多集与单集的 `must_have` 口径**不同**
    （多集要「每集都能覆盖」的硬要求），见 `_brief_spec` 的说明。
    """
    spec = _brief_spec(pack, mode, episodes)
    text = str(script_text or "").strip()
    # idea 模式允许更短（一句点子），但也不能是空的
    floor = 8 if mode == "idea" else 40
    if len(text) < floor:
        raise ValueError("输入太短（%d 字）—— 至少 %d 字才能%s"
                         % (len(text), floor, "创作" if mode == "idea" else "提炼"))
    if len(text) > 20000:
        text = text[:20000]                     # 防超长；截断**说明**在下面
    lead = "故事创意：" if mode == "idea" else "剧本原文："
    msgs = [{"role": "system", "content": spec},
            {"role": "user", "content": lead + "\n" + text}]
    brief = _sanitize_brief(_parse_json_obj(_chat(msgs)), log=log)

    for attempt in range(2):
        r = validate.validate_brief(brief)
        missing = list(r.get("missing_fields") or [])
        if not missing:
            return brief
        if attempt == 1:
            raise ValueError("剧本提炼后 brief 仍缺字段：%s（问题：%s）"
                             % ("、".join(missing), "；".join(r.get("problems") or [])))
        # 带**具体缺哪些**重试（只说"再试一次"模型会重复犯错）
        msgs = msgs + [
            {"role": "assistant", "content": json.dumps(brief, ensure_ascii=False)[:2000]},
            {"role": "user", "content":
             "上面这份缺了这些必填字段：%s。请**补全后重新输出完整 JSON**（其余字段原样保留）。"
             % "、".join(missing)},
        ]
        brief = _sanitize_brief(_parse_json_obj(_chat(msgs)), log=log)
    return brief


# ─────────────────────────────────────────────────────── 建项目

def create_project(script_text: str, style_code: str = "", explicit_pack: str = "",
                   name: str = "", mode: str = "script", episodes: int = 0,
                   ratio: str = "", log=print) -> dict:
    """剧本/创意 → 建项目目录 + `brief.json`（+ 视模式决定要不要留原文）。

    ⚠️ **不跑创作链**：建项目是秒级、跑链是 10–65 分钟。分开做，前端第 1 步就能显示概要。

    ★★ **两种模式的关键差别**（2026-09-16）：

    | mode | 谁写剧本 | 为什么 |
    |---|---|---|
    | `script`（粘贴剧本） | **用户**——原文写进 `scriptwriter/scriptwriter_ep1.md` | 驱动 `done_roles()` 按 `projects/<p>/<role>/*.md` 判 → **编剧这一步已算完成** → 链会**沿用用户的剧本**，不重写（用户要的是把这份剧本拍出来） |
    | `idea`（AI 创作） | **创作链**——**不写** scriptwriter 产物 | 用户只给一句点子；若也写进去，链会把**两句话当成整部剧本**，全片就毁了 |

    两者**都**要产出 `brief.json`：它是 v5 的唯一输入契约。
    """
    pack, note = resolve_pack(style_code, explicit_pack)
    brief = extract_brief(script_text, pack, mode=mode,
                          episodes=max(1, int(episodes or 1)), log=log)
    brief["pack"] = pack                        # 包名以服务端解析为准
    if name:
        brief["topic"] = str(name).strip()
    if episodes and int(episodes) > 0:
        brief["episodes"] = int(episodes)
    if ratio:
        brief["ratio"] = str(ratio)

    pid = new_pid(_slug(brief.get("topic")) or name)
    root = config.PROJECTS_DIR / pid
    (root / "images").mkdir(parents=True, exist_ok=True)

    (root / "brief.json").write_text(
        json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")

    sw = root / "scriptwriter"
    sw.mkdir(parents=True, exist_ok=True)
    if mode == "script":
        # 用户给的原文**不能丢**，且**要放在编剧产物位**（见上面 docstring 的表格）
        (sw / "scriptwriter_ep1.md").write_text(str(script_text or ""), encoding="utf-8")
        (sw / "_source-note.md").write_text(
            "# 剧本来源\n\n本片剧本**由用户在网页端粘贴提供**（非 scriptwriter 角色生成）。\n"
            "文件 `scriptwriter_ep1.md` 即用户原文，未经改写。\n\n"
            "创建时间：%s\n风格码：%s\n类型包：%s\n"
            % (time.strftime("%Y-%m-%d %H:%M:%S"), style_code or "—", pack),
            encoding="utf-8")
    else:
        # idea 模式：**故意不写** scriptwriter 产物，让链去写（否则两句话会被当成整部剧本）
        (root / "_source-idea.md").write_text(
            "# 创意来源\n\n本片由网页端输入的一句创意**由 AI 创作**而来（brief 是创作结果）。\n"
            "剧本将由创作链的 scriptwriter 角色撰写。\n\n"
            "用户输入：\n\n> %s\n\n创建时间：%s\n类型包：%s\n"
            % (str(script_text or "").replace("\n", "\n> "),
               time.strftime("%Y-%m-%d %H:%M:%S"), pack),
            encoding="utf-8")

    log("[webchain] 已建项目 %s（pack=%s, mode=%s, 集数=%s）"
        % (pid, pack, mode, brief.get("episodes")))
    return {"pid": pid, "pack": pack, "note": note, "brief": brief, "mode": mode,
            "script_chars": len(str(script_text or "")),
            "topic": brief.get("topic")}


# ─────────────────────────────────────────────────────── dev server（D6）

def agent_url() -> str:
    return os.environ.get("SHORTDRAMA_V5_AGENT_URL", "http://127.0.0.1:%d" % DEV_PORT).rstrip("/")


def _state_path() -> Path:
    return config.RUNTIME_ROOT / DEV_STATE


def read_state() -> dict:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:                           # noqa: BLE001
        return {}


def _write_state(d: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def manual_steps_state():
    """状态文件里记的「**端口上那个 dev server** 是否开着逐步人工确认」。

    取值三态：`True` / `False` / **`None` = 不知道**（接管了别人起的 server）。
    **未知就说未知，不猜** —— 前端据此显示"未开启（或未知：这个 dev 不是本服务起的）"。

    ⛔ **不要**用 `config.APPROVE_EACH_ROLE` 代替它：那读的是**本进程（shim）**的
    环境，而开关由 **dev server 进程**读取。两个进程的 env 是分开的，
    shim 读到 False 不能说明 dev 是 False —— 会稳定误报。
    """
    v = read_state().get("manual_steps")
    return None if v is None else bool(v)


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % int(pid), "/NH"],
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15).stdout or ""
    except Exception:                           # noqa: BLE001
        return True
    return str(int(pid)) in out


def probe_ok(timeout: float = 3.0) -> bool:
    """`GET /ok` → `{"ok":true}`。**不一致的端口会让派发全失败**，故必须探。"""
    import urllib.request
    try:
        with urllib.request.urlopen(agent_url() + "/ok", timeout=timeout) as r:
            return b'"ok"' in r.read()
    except Exception:                           # noqa: BLE001
        return False


def bound_project() -> str:
    """**端口上那个 dev server 实际服务哪个项目** —— 未知返回空串。

    来源：`orchestrator._build_role_graph` 的角色节点会把 `_root` 落盘到
    `<repo>/.tmp/role_fs_root.txt`（那是它为了排障刻意写的）。
    `/ok` 本身**不带这个信息** —— 实测发现 2024 上可能有**上个会话遗留**的 server，
    而状态文件对它一无所知；这时必须能判断"它是不是我们的"，
    否则只能盲目抢端口（抢会失败）或盲目复用（可能服务的是别的项目）。

    ⚠️ 该文件是**角色节点跑起来之后**才写的 → 启动初期可能还是上一轮的值。
    所以它只作**提示**，取值要配合状态文件用（见 `ensure_devserver`）。
    """
    p = config.RUNTIME_ROOT / ".tmp" / "role_fs_root.txt"
    try:
        t = p.read_text(encoding="utf-8").strip()
    except Exception:                           # noqa: BLE001
        return ""
    return Path(t).name if t else ""


def _pid_on_port(port: int) -> int:
    """谁占着这个端口（取不到返回 0）。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20).stdout or ""
    except Exception:                           # noqa: BLE001
        return 0
    for ln in out.splitlines():
        if "LISTENING" in ln and (":%d " % int(port)) in ln:
            m = re.search(r"(\d+)\s*$", ln.strip())
            if m:
                return int(m.group(1))
    return 0


def _project_exists(pid: str) -> bool:
    """这个项目还在吗（有 `brief.json` 才算）。"""
    return bool(pid) and (config.PROJECTS_DIR / pid / "brief.json").exists()


def devserver_status() -> dict:
    st = read_state()
    st["alive"] = _alive(st.get("os_pid"))
    st["ok"] = probe_ok() if st["alive"] else False
    st["agent_url"] = agent_url()
    return st


def stop_devserver(log=print) -> dict:
    st = read_state()
    if st.get("os_pid"):
        try:
            # ★ 必须 /T：langgraph dev 会起 uvicorn 子进程，只杀父会留孤儿占端口
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(st["os_pid"]))],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        except Exception as e:                  # noqa: BLE001
            log("[webchain] 停 dev 失败：%s" % str(e)[:120])
    _write_state({})
    return {"stopped": st.get("os_pid")}


def archive_api_dir(log=print) -> str:
    """归档 `.langgraph_api`。**防陈旧 run 复活占槽位 / 按过期意图推进**（照抄 run_new_project）。"""
    d = config.RUNTIME_ROOT / _ARCHIVE
    if not d.exists():
        return ""
    dst = config.RUNTIME_ROOT / ("%s.bak-web-%s" % (_ARCHIVE, time.strftime("%m%d%H%M%S")))
    try:
        shutil.move(str(d), str(dst))
        log("[webchain] 已归档 %s → %s" % (_ARCHIVE, dst.name))
        return dst.name
    except Exception as e:                      # noqa: BLE001
        log("[webchain] 归档 .langgraph_api 失败（继续）：%s" % str(e)[:120])
        return ""


def ensure_devserver(pid: str, log=print, wait_s: float = 90.0,
                     force: bool = False, manual_steps=None) -> dict:
    """确保 dev server 正在服务 **pid 这个项目**。需要时重启（D6）。**幂等**。

    ## 三条路（**实测补出来的**，2026-09-15）

    ① 状态文件说是本项目 + 进程活着 + `/ok` 通 → **复用**
    ② 状态文件不知道，但**端口上有 server**（上个会话遗留 / 别人起的）：
       · 能用 `bound_project()` 确认是本项目 → **接管**（补记状态）后复用
       · 确认是别的项目 → **拒绝并说清**（**不偷偷杀别人的进程**）
       · 判不出来 → 也拒绝，并提示可加 `force=true` 强行接管
    ③ 端口空着 → 归档 `.langgraph_api` → 起新的

    `force=True`：允许杀掉端口上那个来历不明的进程后重启。**默认关** ——
    机器上可能有别人正在用的 server，静默杀掉是很糟的行为。
    """
    if manual_steps is not None:
        set_manual_steps(manual_steps)
    st = read_state()

    # ① 已知且健康
    if st.get("pid") == pid and _alive(st.get("os_pid")) and probe_ok():
        rec = st.get("manual_steps")
        # 开关值变了必须重启（编译期生效，见 MANUAL_STEPS_ENV 约束 1）—— 否则会
        # 出现"前端把开关拨到关、链却照样每步停"这种最难查的行为。
        # `rec is None` = 接管的是别人起的 server，不知道它的值 ⇒ 不动它
        # （静默重启别人的进程是很糟的行为，见本函数 docstring）。
        if rec is None or rec == _manual_steps:
            return {"reused": True, "pid": pid, "os_pid": st.get("os_pid"),
                    "agent_url": agent_url(), "source": "state"}
        log("[webchain] 逐步确认开关变了（%s → %s）→ 重启 dev" % (rec, _manual_steps))
        stop_devserver(log=log)

    # ② 端口上有东西
    if probe_ok():
        here = _pid_on_port(DEV_PORT)
        bound = bound_project()
        # `role_fs_root.txt` 是**角色跑起来之后**才写的 → 刚起完 server 时可能还是空的。
        # 这时若端口上那个进程**正是我们状态文件里记的那个**，就以它为准 ——
        # 否则 409 会报"(判不出来)"，人无从判断该不该 force。
        if not bound and st.get("os_pid") and int(st.get("os_pid") or 0) == int(here or 0):
            bound = str(st.get("pid") or "")
            log("[webchain] 端口上的进程＝状态文件记的那个（%s）" % bound)
        if bound == pid:
            # `manual_steps: None` = **不知道**。接管的是**别人起的** server，
            # 我们看不到它的环境变量 ⇒ 如实记"未知"，不猜（前端会照实显示）。
            _write_state({"pid": pid, "os_pid": here, "port": DEV_PORT,
                          "agent_url": agent_url(),
                          "manual_steps": None,
                          "adopted_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            log("[webchain] 接管端口上已有的 dev（pid %s，已确认在服务 %s）" % (here, pid))
            return {"reused": True, "pid": pid, "os_pid": here,
                    "agent_url": agent_url(), "source": "adopted"}
        # ★ **绑定到一个已不存在的项目 → 那是僵尸，可以安全接管**（2026-09-15 实测补）。
        #   不补这条会**把端口永久占死**：僵尸服务着一个被删掉的项目，
        #   而"拒绝接管"的逻辑会让**之后每一个项目**都撞 409，人只能手工 taskkill。
        #   （我自己就在 CDP 调试里制造了这个局面。）
        stale = bool(bound) and not _project_exists(bound)
        if stale or force:
            why = "绑定的项目 %r 已不存在（僵尸）" % bound if stale else "force=true"
            log("[webchain] %s → 杀掉端口 %d 上的 pid %s 后重启" % (why, DEV_PORT, here))
            if here:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(here)],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
                time.sleep(1.5)
        else:
            raise RuntimeError(
                "端口 %d 上已有一个 dev server（os pid %s），但它服务的是 %r 而不是 %r —— "
                "**拒绝接管**（不偷偷杀别人的进程）。"
                "若确认那个进程可以停，用 force=true 重试。"
                % (DEV_PORT, here or "?", bound or "(判不出来)", pid))

    # ③ 起新的（先停状态里记着的旧进程）
    if st.get("os_pid") and _alive(st.get("os_pid")):
        log("[webchain] 项目切换（%s → %s）→ 先停旧 dev" % (st.get("pid"), pid))
        stop_devserver(log=log)
    archive_api_dir(log=log)

    py = str(config.PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
    lg = config.PROJECT_ROOT / ".venv" / "Scripts" / "langgraph.exe"
    # Absolute: the server now runs with cwd = RUNTIME_ROOT (see below), so a
    # cwd-relative `v5/langgraph.json` would not resolve.
    cfg = str(config.PACKAGE_ROOT / "langgraph.json")
    argv = [str(lg), "dev", "--config", cfg,
            "--host", "127.0.0.1", "--port", str(DEV_PORT), "--no-browser"]
    if not lg.exists():                         # console script 缺失时用模块方式（本机有过先例）
        argv = [py, "-m", "langgraph_cli", "dev", "--config", cfg,
                "--host", "127.0.0.1", "--port", str(DEV_PORT), "--no-browser"]

    env = dict(os.environ)
    env.update({
        "SHORTDRAMA_V5_PROJECT": pid,
        "SHORTDRAMA_OPEN_CHAIN": "1",
        "SHORTDRAMA_ALLOW_RESUME": "1",
        # ★ 前端「逐步人工确认」（见 MANUAL_STEPS_ENV 的说明）：
        #   每派一个角色前挂起等人点头。**默认关**，由前端开关打开。
        #   **只在这个 dev server 上开** —— 外部 agent 的链路不受影响。
        MANUAL_STEPS_ENV: "1" if _manual_steps else "0",
        # 本机代理是单点故障（见 MEMORY）：本机 + agnes 一律直连
        "NO_PROXY": "127.0.0.1,localhost,agnes-ai.com,agnes-ai.space",
        "no_proxy": "127.0.0.1,localhost,agnes-ai.com,agnes-ai.space",
        "PYTHONIOENCODING": "utf-8",
        # UTF-8 mode. `langgraph_api/validation.py` reads its bundled openapi
        # resource with a bare `open()`, so on a zh-CN Windows (locale cp936)
        # the server dies with UnicodeDecodeError before any graph loads.
        "PYTHONUTF8": "1",
        # `langgraph.json`'s `"dependencies": [".."]` resolves against the
        # server's cwd — which is RUNTIME_ROOT below, not the code root — so
        # put the code root on the path or `import v5` fails.
        "PYTHONPATH": os.pathsep.join(
            [str(config.PROJECT_ROOT)]
            + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])),
    })
    config.RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    logdir = config.RUNTIME_ROOT / ".tmp"
    logdir.mkdir(parents=True, exist_ok=True)
    logfile = logdir / "web-devserver.log"
    # 子进程需要这个句柄，但**父进程不必一直持有** —— 不关就会每次切项目泄漏一个
    # 文件句柄（Windows 上还会让临时目录删不掉）。spawn 完立刻关。
    with logfile.open("w", encoding="utf-8") as lf:
        # cwd = RUNTIME_ROOT because `langgraph dev` has no option to relocate
        # its `.langgraph_api` checkpoint store; it lands in the cwd.
        proc = subprocess.Popen(argv, cwd=str(config.RUNTIME_ROOT), env=env,
                                stdout=lf, stderr=subprocess.STDOUT)
    log("[webchain] dev 启动（os pid %d，项目 %s，端口 %d）" % (proc.pid, pid, DEV_PORT))

    t0 = time.time()
    while time.time() - t0 < wait_s:
        if probe_ok(timeout=2):
            st = {"pid": pid, "os_pid": proc.pid, "port": DEV_PORT,
                  "agent_url": agent_url(),
                  # 我们自己起的 ⇒ 这个值是**确定的**（见 MANUAL_STEPS_ENV）
                  "manual_steps": manual_steps_on(),
                  "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "log": str(logfile)}
            _write_state(st)
            log("[webchain] dev 就绪（%.1f 秒）" % (time.time() - t0))
            return {"reused": False, **st}
        if proc.poll() is not None:
            raise RuntimeError("dev server 启动即退出（exit=%s）—— 见 %s"
                               % (proc.returncode, logfile))
        time.sleep(1.0)
    raise RuntimeError("dev server %.0f 秒内未就绪（`/ok` 无响应）—— 见 %s"
                       % (wait_s, logfile))


# ─────────────────────────────────────────────────────── 概要（第 1 步展示）

def overview(root: Path) -> dict:
    """给前端第 1 步的概要。**复用 `webmap`**（同一份映射，不写第二套）。"""
    from . import webmap
    p = webmap.progress(root)
    return {"pid": root.name, "topic": p["name"], "pack": p["v5"]["pack"],
            "style": p["style"], "outline": p["outline"],
            "episodes": [{"id": e["id"], "no": e["no"], "title": e["title"],
                          "script_status": e["script_status"]} for e in p["episodes"]]}


def public_project(root: Path) -> dict:
    """建项目端点返回的形状。

    ★ **超集**：前端 `playlet-list.js:192-196` 读 `p.id` 与 `p.analyze.*`，
    而我们的约定是 `pid` —— 两个都给。

    ⚠️ `analyze` 的计数**如实**：此刻只有 brief 里的角色（`protagonist` /
    `second_character`），**资产条目要等创作链跑出 `assetdesigner` 才有**
    → `scenes`/`props` 是 0，并用 `note` 说清，别让人以为解析漏了东西。
    """
    from . import webmap
    o = overview(root)
    b = webmap.brief(root)
    reg = webmap.registry(root)
    items = reg.get("assets") or []

    def _n(kinds) -> int:
        return sum(1 for a in items
                   if str(a.get("type") or "").strip().lower() in kinds)

    if items:
        chars, scenes, props = _n(("character",)), _n(("location", "scene")), _n(("prop",))
        note = ""
    else:
        chars = sum(1 for k in ("protagonist", "second_character") if str(b.get(k) or "").strip())
        scenes, props = 0, 0
        note = "资产条目由创作链的 assetdesigner 产出；此刻只有 brief 里的角色设定"
    o.update({
        "id": root.name,                  # 前端读 `p.id`
        "analyze": {"characters": chars, "scenes": scenes, "props": props, "note": note},
    })
    return o
