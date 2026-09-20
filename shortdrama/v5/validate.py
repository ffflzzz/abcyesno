# -*- coding: utf-8 -*-
"""质量控制：brief 校验 / 智能截断 / 产物忠实度 / 分镜契约。

三件事，各自解决一个真实事故：

1. **brief 智能截断**（`pack_brief`）
   旧实现是 `json.dumps(brief)[:3000]`——对 JSON **字符串**裸截断：尾部字段
   静默丢失，且残留残缺 JSON；字段顺序不同的调用方丢的内容也不同。
   本实现按字段优先级组装，**放不下就丢低价值字段并报告**；
   **核心字段放不下则直接报错**（宁可失败也不静默丢失）。

2. **brief 完备性**（`validate_brief`）
   必填字段、must_have 条数与具体度。没有它，缺字段的 brief 会一路跑到
   渲染才暴露问题。

3. **产物忠实度 + 分镜契约**（`check_brief_fidelity` / `check_storyboard`）
   校验创作产物是否覆盖了 brief 的 must_have（中文二元组覆盖率）。
   视觉/风格类条目（字幕/光/竖屏…）跳过——文本产物不可能包含它们，
   那些由分镜与视频层负责。
"""
from __future__ import annotations

import json
import re

# ─── 1. brief 智能截断 ───────────────────────────────────────────────────────

# 注入 goal 时的字段优先级：高价值先放，放不下才轮到后面的。
BRIEF_PRIORITY = (
    "must_have", "key_props", "禁忌", "protagonist", "second_character", "结局",
    "target_duration", "visual-style", "audio_mode", "tone",
    "pack", "episodes", "genre", "topic", "reference_photo",
)

# 核心字段：丢了就等于没说清楚要拍什么，宁可报错也不能省。
CORE_FIELDS = ("must_have", "key_props", "禁忌", "protagonist", "结局")

DEFAULT_BUDGET = 3000


class BriefTooLarge(ValueError):
    """brief 超出预算，且连核心字段都装不下。

    不静默截断——调用方必须看到这个错误并精简 brief。
    """

    def __init__(self, needed: int, budget: int, lost_core: list[str],
                 dropped: list[str]):
        self.needed = needed
        self.budget = budget
        self.lost_core = lost_core
        self.dropped = dropped
        super().__init__(
            "brief 超出 %d 字符预算（核心字段需 %d）：%s 无法放入。"
            "请精简这些字段后重试（其余被省略的字段：%s）"
            % (budget, needed, "、".join(lost_core),
               "、".join(dropped) or "无"))


def pack_brief(brief: dict, budget: int = DEFAULT_BUDGET) -> tuple[str, list[str]]:
    """按优先级把 brief 组装进预算内。返回 (json 文本, 被省略的字段列表)。

    与旧实现的两点根本差别：
      - 输出**始终是合法 JSON**（旧实现会截出半截 JSON）
      - 省略了什么是**显式返回**的，且核心字段省略时**报错**

    单字段自身就超预算时：核心字段 → 报错；非核心 → 省略并报告。
    """
    if not isinstance(brief, dict):
        raise TypeError("brief 必须是 dict，收到 %s" % type(brief).__name__)

    order = [k for k in BRIEF_PRIORITY if k in brief]
    # 调用方自定义字段（不在优先级表里）排在最后，尽力保留
    order += [k for k in brief if k not in BRIEF_PRIORITY]

    kept: dict = {}
    dropped: list[str] = []
    used = 0
    needed_core = 0

    for k in order:
        seg = json.dumps({k: brief[k]}, ensure_ascii=False)
        cost = len(seg) + (1 if kept else 0)     # 逗号分隔符
        if used + cost <= budget:
            kept[k] = brief[k]
            used += cost
        else:
            dropped.append(k)
            if k in CORE_FIELDS:
                # 记录核心字段到底需要多少，用于报错信息
                needed_core += cost

    lost_core = [k for k in dropped if k in CORE_FIELDS]
    if lost_core:
        raise BriefTooLarge(used + needed_core, budget, lost_core, dropped)

    return json.dumps(kept, ensure_ascii=False), dropped


# ─── 2. brief 完备性 ─────────────────────────────────────────────────────────

BRIEF_REQUIRED = ("topic", "pack", "genre", "episodes", "target_duration",
                  "protagonist", "must_have", "key_props", "禁忌", "tone", "结局")

MIN_MUST_HAVE = 3          # 四幕结构至少 3 幕才叫有结构
MIN_MUST_HAVE_LEN = 8      # 每条至少这么长，否则视为"氛围描述"而非"可拍事件"


def validate_brief(brief: dict) -> dict:
    """brief 自身的质量门。返回 {ok, missing_fields, problems}。

    注意与 fidelity 的区别：这里查的是 **brief 写得全不全**，
    `check_brief_fidelity` 查的是 **产物有没有覆盖 brief**。两者都要有——
    忠实度门保证不了 brief 本身的质量（烂 brief 被忠实执行 = 烂成片）。
    """
    problems: list[str] = []

    missing = [k for k in BRIEF_REQUIRED if not str(brief.get(k) or "").strip()]
    if missing:
        problems.append("缺少必填字段：%s" % "、".join(missing))

    mh = brief.get("must_have") or []
    if not isinstance(mh, list):
        problems.append("must_have 必须是数组")
        mh = []
    elif len(mh) < MIN_MUST_HAVE:
        problems.append("must_have 只有 %d 条，建议至少 %d 条（四幕结构）"
                        % (len(mh), MIN_MUST_HAVE))
    thin = [i for i, x in enumerate(mh)
            if len(re.sub(r"\s", "", str(x))) < MIN_MUST_HAVE_LEN]
    if thin:
        problems.append("must_have 第 %s 条过短，需写清具体可拍的事件而非氛围描述"
                        % "、".join(str(i + 1) for i in thin))

    kp = brief.get("key_props") or []
    if isinstance(kp, list) and not kp:
        problems.append("key_props 为空——道具命名是跨镜一致性的锚点")

    # 节拍数 vs 目标时长（2026-09-14 实测新增）。
    #
    # ★ 结论：**分镜规模由 `must_have` 的节拍数驱动，不是由 `target_duration` 驱动。**
    #   四个真实项目的对照：
    #     节拍 8 → 目标 330s → 分镜 264s / 49 镜（80%）
    #     节拍 6 → 目标 180s → 分镜 173s / 25 镜（96%）
    #     节拍 6 → 目标 120s → 分镜 112s / 18 镜（93%）
    #     节拍 4 → 目标 180s → 分镜 **117s / 21 镜（65%）→ 被片长门拦下**
    #   实测「秒/节拍」稳定在 **19–33**（中位 ~29）→ 节拍数 ≈ 目标秒 ÷ 30。
    #
    # 为什么只告警不阻断：这是**经验比例**不是硬约束，而且跑到这里创作链已经结束，
    # 拦也拦不回时间。价值在于把"**brief 自己写少了**"这个真因**显式说出来** ——
    # 否则它会被误读成"分镜师不听话"，去改 SKILL 或调门限，全是白费。
    tgt = parse_target_seconds(brief.get("target_duration"))
    if tgt and mh:
        want_beats = max(MIN_MUST_HAVE, int(round(tgt / 30.0)))
        if len(mh) < want_beats:
            problems.append(
                "must_have 只有 %d 条节拍，而 %d 秒的片按实测（秒/节拍 19–33）约需 %d 条"
                "—— 节拍偏少会让分镜**排不满时长**"
                "（实测：180 秒只给 4 节拍 → 分镜 117 秒，被片长门拦下整条媒体链）"
                % (len(mh), int(round(tgt)), want_beats))

    return {"ok": not problems, "missing_fields": missing, "problems": problems}


# ─── 2.5 音频模式（brief.audio_mode）─────────────────────────────────────────
#
# 为什么需要这一节：`audio_mode` 长期是个**空转字段**——只出现在 BRIEF_PRIORITY
# 与 BRIEF_REQUIRED 里，没有任何消费点。后果实测过两次：
#   · paperface-2 / umbrella 的 brief 写 dialogue-led，分镜 19 镜 / 9 镜**全片零台词**；
#   · 用户看到成片直接问「为什么无对白？我要对白啊」。
# 也就是说，「要不要台词」这件事此前完全由 scenedesigner 自己即兴决定。
# 现在两个方向都接上：写之前（graph.role_input 注入硬指令）、写之后（本节的校验）。

# 需要台词的取值（写「每场都有台词」的硬指令 + 事后校验）
DIALOGUE_MODES = ("dialogue-led", "dialogue", "dialogue_led", "talkie", "有对白")
# 明确无台词的取值
SILENT_MODES = ("silent", "no-dialogue", "none", "无对白", "环境音")

# dialogue-led 下，台词镜占比的**硬性**下限。
# 为什么定 0.2 而不是更高：短剧里反应镜/空镜/道具特写本来就不说话，用 50% 会把
# 合法分镜（实测 bootleg99-full 32 镜 11 句 = 34%）判成违规 → 无谓重跑，而重跑
# 一次是整轮 scenedesigner 的 token 成本，且有 3 次上限后强制放行。
# 真正的失效形态是「全片零台词」（实测 paperface-2 0/19、umbrella 0/9），
# 0.2 足以拦住它，同时给正常节奏留空间。低于 0.5 的另出**警告**（见 SHORT_RATIO_WARN）。
DIALOGUE_MIN_RATIO = 0.2
SHORT_RATIO_WARN = 0.5

# ─── 台词**长度**判据（2026-09-15，用户反馈"对白太短、都不能表达剧情了"）─────────
#
# 为什么必须查长度：上面的 `DIALOGUE_MIN_RATIO` 只查「**有多少镜**有台词」，
# **完全不查每句多长** —— 于是「沉。」「开。」「推！」这类 1–4 字残句天然合法。
# 实测 village-bridge（43 镜 / 5 分钟片）：22 句台词**平均只有 7.0 字**，12 句 ≤6 字；
# 台词镜占比达标（22/43）、剧情却完全不靠台词。
#
# 根因在**契约**（两处，都不是"模型不听话"）：
#   · `packs/shortdrama/scriptwriter/SKILL.md` 明写「对白要**短促**有力，短剧不适合
#     长篇独白」，frontmatter 还写着「台词**短促**口语化」；步骤 3 的整片预算是
#     「1–3 分钟约 500–1500 字」→ 5 分钟片按这个预算，台词必然被饿死；
#   · `dialogue` 角色是**逐字搬运器**（2026-09-14 契约修正：它改写台词曾导致
#     "同一句两个版本"）→ 短句一路原样传到分镜，**没有任何一环会把句子写长**。
#
# 两端都要防（本项目铁律）：
#   · **下限** —— 太短的句子承载不了信息：「沉。」→「这块石头比我想的重多了。」；
#   · **上限** —— 太长念不完：中文口播约 **4–5 字/秒**，而镜头时长是固定的，
#     超了会挤掉画面动作（而且首帧已经定了）。故上限**与镜头秒数挂钩**，
#     取 `min(绝对上限, 秒数 × DIALOGUE_CHARS_PER_SEC)`。
DIALOGUE_MIN_CHARS = 8
#: 单句**绝对**上限。2026-09-15 40 → **45**（见 `DIALOGUE_CHARS_PER_SEC` 的标定说明）。
DIALOGUE_MAX_CHARS = 45
#: 语速（字/秒）。**fatal 档取"最快可得语速"6**，理由：这个数是用来判"**念不完**"的，
#: 取保守值（5）会把"口播偏快但确实念得完"的句子误杀 —— 实测（village-honey）
#: 38 字 / 7 秒 = 5.4 字/秒 被 5.0 判死，而新闻播报语速本就在 5–6 字/秒。
#: 常态语速 5 留作**告警**档（见 `DIALOGUE_LONG_WARN_PER_SEC`）。
DIALOGUE_CHARS_PER_SEC = 6.0
DIALOGUE_LONG_WARN_PER_SEC = 5.0

#: 「过长台词」的**占比**容忍度。
#:
#: ★ 2026-09-15 与"过短"逻辑对齐：**过短按占比、过长却按单句一票否决** ——
#:   同一份契约里两套判据形态不一致，代价实测很重：village-honey（9 镜短片）
#:   因 **2 句超长**直接把整条链拦下、**创作链已跑 50 分钟白等**。
#:   ⇒ 改成同一套：**占比 > FATAL 才阻断**，单句只告警。
DIALOGUE_LONG_FATAL_RATIO = 0.34
DIALOGUE_LONG_WARN_RATIO = 0.0

#: 「过短台词」的**占比**容忍度（超过 FATAL 阻断，超过 WARN 只告警）。
#:
#: 为什么不按"每句都必须达标"一刀切：实测**全部既有项目**的残句占比都在 50% 以上 ——
#:   six-winters 17/19、night-repair 12/13、lost-and-found 11/13、paper-crane 9/13、
#:   village-bridge 18/21、noodle-night 7/8、warm-milk 4/4。
#: 这不是某一部片的毛病，而是**契约长期写着"对白要短促"**的结果（见上方根因说明）。
#: 「一句不达标就拦」会把每一次运行都拦死 → 门形同废止。
#: 取 **0.40** 的含义：**残句不能成为常态**（四成以上就说明契约根本没被执行）。
DIALOGUE_SHORT_FATAL_RATIO = 0.40
DIALOGUE_SHORT_WARN_RATIO = 0.15

# ─── 分镜总时长 vs brief 目标（2026-09-13 补的缺口）───────────────────────────
#
# 为什么需要：`check_storyboard` **一直**在算 `seconds_total`，但**全仓库无人消费**
# （只躺在返回值里）；而 `brief.target_duration` 是**必填字段**。
# 两边数据都有、从没比过 → 一部写着"5 分钟"的 brief，分镜只排 2.4 分钟也照样放行，
# 代价是**整条媒体链跑完**（小时级）才发现片子短了一半。
#
# 这是典型的"便宜的确定性判据"，正该放在分镜契约门里（那一门的原则就是
# "按磁盘/文本事实裁决，不花一分钱、不会误判"）。
#
# 容差带为什么是 [0.85, 1.30]（2026-09-14 收紧下界：0.75 → 0.85）：
#   · 下界 0.85 —— 实测教训：paper-crane 的 brief 要 330 秒，分镜只排了 264 秒
#     （**80%**），擦着 0.75 的下界过关，成片 4.7 分钟 vs 目标 5.5 分钟。
#     0.85 会把这一例拦下（分镜漏内容不是节奏问题，而是**片长直接不对**）。
#   · 上界 1.30 —— 超目标 30% 会被 `AGNES_VIDEO_MAX_SHOTS` 截断成"半成品"，
#     必须提前说；但分镜标称时长与实测成片有 ±10% 出入（实测样本：标称 264s →
#     成片 280.8s，+6%），所以给到 1.30 而不是 1.10。
TARGET_TOL_LOW = 0.85
TARGET_TOL_HIGH = 1.30


def parse_target_seconds(text) -> float | None:
    """从 `brief.target_duration` 的自由文本里取第一个"N 秒 / N 分钟"。

    为什么是自由文本解析而不是新增数字字段：`target_duration` 是**必填字段**且
    历史上都是自然语言（如"约 25 秒，共 6 镜，每镜约 4 秒（单镜硬性 4-12 秒…）"），
    新增字段会与存量 brief 不兼容；取**第一个**时间量词恰好能命中"约 25 秒"这个
    主目标（后面那些"每镜约 4 秒 / 硬性 4-12 秒"都是次要说明）。

    解析不出 → 返回 None，调用方**只警告不阻断**（不能因为 brief 措辞不规范就拦住生产）。
    """
    m = re.search(r"(\d+(?:\.\d+)?)\s*(分钟|分|秒)", str(text or ""))
    if not m:
        return None
    v = float(m.group(1))
    return v * 60.0 if m.group(2) in ("分钟", "分") else v


def _norm_verbatim(t: str) -> str:
    """逐字比对的归一化：只去**空白与标点/引号**，不动一个字。

    为什么去标点：剧本写「收到。」、清单写 `收到`、别处写 "收到"，本质是同一句；
    但**换词**（"他说" → "他讲"）必须被判出来 —— 那才是要拦的失效。
    """
    return re.sub(r"[\s，。！？!?…、；;：:\"'“”‘’「」『』（）()《》\-—]+", "", str(t or ""))


#: 对白清单里的一行：`- 角色：台词`（中英文冒号都可）
_DIALOGUE_LINE_RE = re.compile(r"^\s*[-*+]\s*([^：:]{1,16})[：:]\s*(.+?)\s*$")
#: 表格形式的列名（实测 paper-crane 的清单就是表格：`| 出场序 | 参考镜号 | 角色 | 台词原文 | 语气 |`）
_DLG_TABLE_SAID = ("台词原文", "台词", "内容", "对白")
#: 说明/规则类 bullet 的关键词（**子串**匹配）：
#: 既用于「章节级跳过」（`## 合规自检`、`## 说明` 之后不再解析），
#: 也用于过滤「冒号前不是角色名」的条目
#: （实测：`- 不得出现第三张清晰人脸：…`、`- **人物口径**：…` 都被误收过）。
_DLG_META_PREFIX = ("说明", "备注", "注", "原则", "口径", "统计", "合计", "总计",
                    "校验", "自检", "口型", "提示", "附录", "依据",
                    "不得", "禁止", "不要", "严禁", "必须", "仅", "全片")
#: 单句台词长度上限（第二道网：说明行往往很长；台词是口语短句）
_DLG_MAX_LEN = 60
#: 占位行（不是台词，不参与逐字比对）
_DIALOGUE_PLACEHOLDERS = ("（无对白）", "(无对白)", "（无声）", "(无声)", "无对白", "无声")


def _dialogue_lines(md: str) -> list[str]:
    """从对白清单里抠出**台词**（表格与列表两种形态都支持）。

    为什么必须两种都支持（2026-09-14 实测）：真实产物是**表格**
    （`| 出场序 | 参考镜号 | 角色 | 台词原文 | 语气 |`），而第一版解析器只认
    `- 角色：台词` → **真台词一句没进**，反而把清单底部的「- 说明：…」
    「- 口型提示：…」当成台词 → 逐字率报成 0%（全错）。
    """
    out: list[str] = []
    said_idx: int | None = None
    skip_section = False
    for raw in str(md or "").splitlines():
        line = raw.strip()
        # ① 章节级跳过：清单末尾常有「## 合规自检 / ## 说明」这类**说明章节**，
        #    里面的条目长得像 `- 不得出现第三张清晰人脸：全片仅周平、江野两人。`
        #    —— 冒号前是**规则**而不是角色名，会被当成台词（实测就是这么误报的）。
        if line.startswith("#"):
            skip_section = any(k in line for k in _DLG_META_PREFIX)
            continue
        if skip_section:
            continue
        if line.startswith("|") and line.count("|") >= 3:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(not c or set(c) <= set("-: ") for c in cells):
                continue                      # |---|---| 分隔行
            head = next((i for i, c in enumerate(cells)
                         if any(k in c for k in _DLG_TABLE_SAID)), None)
            if head is not None:
                said_idx = head               # 表头行（每场会重复一次）
                continue
            if said_idx is not None and said_idx < len(cells):
                out.append(cells[said_idx])
            continue
        m = _DIALOGUE_LINE_RE.match(raw)
        if m:
            who = m.group(1).strip().strip("*` （）()")
            # **子串**匹配而非精确相等：「人物口径」「口型提示」这类前缀都含关键词，
            # 精确相等会漏掉（实测漏了「- **人物口径**：全片仅周平、江野两人。」）
            if not any(p in who for p in _DLG_META_PREFIX):
                out.append(m.group(2).strip())
    clean: list[str] = []
    for t in out:
        t = t.strip()
        if not t or t.strip("（()）") in _DIALOGUE_PLACEHOLDERS:
            continue
        if t in _DIALOGUE_PLACEHOLDERS or not _norm_verbatim(t):
            continue
        if len(t) > _DLG_MAX_LEN:
            continue                          # 说明行（台词是口语短句）
        clean.append(t)
    return clean


def check_dialogue_verbatim(dialogue_md: str, script_md: str) -> dict:
    """对白清单的每一句台词，必须**逐字**能在剧本里找到（2026-09-14 新增判据）。

    为什么要有一条**确定性**判据（实测事故，paper-crane）：
        `dialogue` 角色的旧契约写着「负责**对白润色与优化**」「**重写问题台词**」
        （产物标题都叫「对白优化报告」）→ 它会改写剧本台词。而 `scriptwriter_ep1.md`
        **本身也带镜级台词**，两边不一致 → 同一句台词两个版本 → 分镜配镜时无从取舍，
        下游配音/口型与画面对不上。同一晚连续 3 次，每次都由 supervisor 亲自比对改回。
        契约已改成"逐字提取"，但**只靠提示词不够** —— 这里补上可机械校验的那一半。

    返回 `{"checked": n, "offenders": [...], "ratio": float, "ok": bool}`；
    `ok` = 全部台词都能在剧本里原样找到（标点/空白差异不计）。
    """
    lines = _dialogue_lines(dialogue_md)
    norm_script = _norm_verbatim(script_md)
    offenders = [t for t in lines if _norm_verbatim(t) not in norm_script]
    ratio = (len(lines) - len(offenders)) / len(lines) if lines else 1.0
    return {"checked": len(lines), "offenders": offenders,
            "ratio": round(ratio, 3),
            "ok": not offenders or not norm_script}


def check_dialogue_verbatim_files(root, ep: int | None = None) -> dict | None:
    """按项目路径读两份产物再比对。任一份缺失 → `None`（不适用，不是失败）。

    ★ M2：`ep=None` 时取 manifest 的 `episode_index`（不传 = 当前集）。
    """
    from pathlib import Path as _P
    from . import guards
    root = _P(root)
    if ep is None:
        ep = int(guards.load_manifest(root).get("episode_index", 1) or 1)
    dlg = guards.resolve_path(root, "dialogue", int(ep))
    scr = root / "scriptwriter" / ("scriptwriter_ep%d.md" % ep)
    if not dlg.exists() or not scr.exists():
        return None
    try:
        return check_dialogue_verbatim(dlg.read_text(encoding="utf-8"),
                                       scr.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _duration_gap(seconds_total: float, brief: dict | None) -> dict:
    """`{"target": float|None, "ratio": float|None, "off": str}`（`off` 非空 = 偏离超标）。"""
    b = brief or {}
    target = parse_target_seconds(b.get("target_duration") or b.get("target-duration"))
    if not target or seconds_total <= 0:
        return {"target": target, "ratio": None, "off": ""}
    ratio = seconds_total / target
    if TARGET_TOL_LOW <= ratio <= TARGET_TOL_HIGH:
        return {"target": target, "ratio": round(ratio, 3), "off": ""}
    return {"target": target, "ratio": round(ratio, 3),
            "off": ("分镜总时长 %ds 与 brief 目标 %ds 不符（%.0f%%，容差 %.0f%%–%.0f%%）"
                    "—— %s"
                    % (round(seconds_total), round(target), ratio * 100,
                       TARGET_TOL_LOW * 100, TARGET_TOL_HIGH * 100,
                       "分镜漏了内容，媒体链跑完才发现片长不够"
                       if ratio < TARGET_TOL_LOW else
                       "会超出镜头数上限被截断成半成品"))}


def audio_mode_of(brief: dict | None) -> str:
    """归一化 brief 的音频模式（缺省 dialogue-led——默认要台词，别再默默无声）。"""
    b = brief or {}
    v = b.get("audio_mode") or b.get("audio-mode") or ""
    v = str(v).strip().lower()
    if v in SILENT_MODES:
        return "silent"
    if v in DIALOGUE_MODES:
        return "dialogue-led"
    return "dialogue-led" if not v else v


def dialogue_required(brief: dict | None) -> bool:
    return audio_mode_of(brief) == "dialogue-led"


#: 「画面内文字」政策的两档。
TEXT_POLICY_ALLOW = "allow"
TEXT_POLICY_FORBID = "forbid"


def on_screen_text_of(brief: dict | None) -> str:
    """归一化 brief 的「画面内文字」政策（**缺省 allow**）。

    为什么缺省放开（2026-09-17，用户判定）：
      原先全链路默认**禁止画面内任何文字**。它的**初衷**是防"烧录字幕"
      （模型把提示词/台词烤成字幕条烧在画面底部 —— 真问题，见
      `../docs-archive-20260918/v5-quality-regression.md`）。但它被泛化成"画面不能有任何文字/数字"，
      于是：分镜被迫写反物理的描述（"电梯按钮为无字圆形色块"）→ 生成模型照真实
      世界画（带楼层数字的面板）→ 产出"违规" → QC 判硬伤又翻判干净，白耗复核。
      ⇒ **防烧录（模型行为）与画面文字（画面内容）拆开**：前者永远压，后者默认放开。
    需要严格无字的项目（如怕平台审核）在 brief 里写 `on_screen_text: forbid`。
    """
    b = brief or {}
    v = b.get("on_screen_text") or b.get("on-screen-text") or ""
    v = str(v).strip().lower()
    return TEXT_POLICY_FORBID if v in ("forbid", "forbidden", "none", "off") \
        else TEXT_POLICY_ALLOW


# ─── 3. 忠实度 / 分镜契约 ────────────────────────────────────────────────────

# 视觉/风格类 must_have 条目由分镜与视频层负责，文本产物里不可能出现——跳过。
#
# ⚠️ 收录标准：只收**几乎不会出现在剧情动作里**的词。歧义词会让整条 must_have
# 被误跳过，导致 FIDELITY 门漏检——漏检比误报危害更大（门直接失效）。
# 因此这里**不收"镜头"**：「对镜头大笑」是剧情动作，不是镜头语言；
# 镜头语言类表述已由「运镜 / 构图 / 景别」覆盖。
_STYLE_MARKERS = ("字幕", "水印", "竖屏", "景别", "色调", "风格", "写实",
                  "电影感", "自然光", "构图", "运镜", "摄像机", "色彩", "音效", "声音",
                  "不要", "不能", "禁止", "无字幕", "阳光", "侧脸", "背影")

_REQUIRED_COLS = [("画面", "画面描述"), ("对白", "对白"), ("景别", "景别"),
                  ("运镜", "运镜"), ("时长", "时长"), ("镜头", "镜头号")]

# 画面描述里出现这些词 = 要求模型**渲染出画内文字**，属模型能力边界，
# 这条信息必须走对白而不是画面。
# ★ 2026-09-17 **收窄**：移除了「表针 / 指针指向 / 屏幕显示 / 显示屏上 / 刻痕」。
#   理由见 `media.qc.TEXT_RULE_ALLOW` 的历史说明 —— 这五项是**场景固有元素**
#   （仪表读数、屏幕界面、物件磨损痕迹），**不是**"要求文字承载剧情信息"。
#   留在这里会让**正常写场景**的分镜被标成"要求渲染画内文字"，
#   与「场景固有文字可保留」的新政策直接矛盾。
_TEXT_TRAPS = ("刻字", "碑文", "写着", "文字清晰", "文字可见", "刻着")


# 忠实度比对前要丢掉的**虚词/功能字**。它们不承载"拍的是什么"，却会挤进二元组分母，
# 把真实内容信号稀释掉（见 `_content_bigrams` 的实测说明）。
_FID_STOP = frozenset(
    "的了在和我他她它们是有与及也就都又还很更不没把被对从向为着过之而其如若则所因此且但或"
    "一二三四五六七八九十")

#: 忠实度阈值（must_have 覆盖率下限）。取自实测双端校准（2026-09-14，7 个项目 40 条节拍）：
#:   本片（真实覆盖，含同义改写）最低 0.357 ／ 异片（真实缺失）最高 0.172
#:   → 取中点 0.26，两端各留约 0.09 余量。
FIDELITY_THRESHOLD = 0.26


def _content_bigrams(s: str) -> set[str]:
    """忠实度比对用**内容二元组**：先按标点/空白/表格标记切段，段内去虚词后取二元组。

    为什么要去虚词（2026-09-14 实测事故）：
        原实现直接对"剥掉标点后的整串"取二元组，于是「的/了/在/和」这类虚词也进分母，
        真实内容信号被稀释。实测事故：brief 写「陷进泥坑／招手／跑来推车」，分镜写成
        「碾进浅泥坑／朝后方挥手／推车头」——**完全覆盖、只是换了词**，覆盖率却只有
        **0.267**，被 0.30 阈值判成"未覆盖 must_have"→ `STORYBOARD-REJECT` 拦下整条
        媒体链（创作链已跑完 21 分钟）。7 个项目 40 条节拍的分离度对照：
            原始 bigram    本片最低 0.267 / 异片最高 0.184（阈值 0.30 落在本片内部 → 必误判）
            内容 bigram    本片最低 0.357 / 异片最高 0.172（余量翻倍）
    为什么要切段：表格里 `|` 被剥掉后会把相邻单元格粘成一个假二元组（`景别`+`画面`
        → `别画`），凭空给覆盖率加分、让判据变松。段内取二元组从物理上消除这种假命中。
    """
    out: set[str] = set()
    for seg in re.split(r"[^\u4e00-\u9fffA-Za-z0-9]+", s or ""):
        seg = "".join(ch for ch in seg if ch not in _FID_STOP)
        if len(seg) <= 1:
            if seg:
                out.add(seg)
            continue
        out.update(seg[i:i + 2] for i in range(len(seg) - 1))
    return out


# 对白格的「无台词」写法（都要算作无台词，不能算台词镜）
# 注意：**不能把空串放进这个元组**——`"" in s` 恒为真，会让所有台词都判成无声。
_NO_LINE = ("无声", "无对白", "环境音", "无台词", "none", "silent", "-", "—")


def _has_line(cell: str) -> bool:
    """对白格是否**真的有台词**。

    为什么不能只看非空：SKILL.md 要求「无台词写（无声，环境音）」，于是对白格
    非空但可能全是无声标记。这里把括号与标点剥掉后比对语义。
    """
    t = re.sub(r"[\s（）()「」『』【】\[\]；;，,。.、]", "", cell or "")
    if not t:
        return False
    return not any(k in t for k in _NO_LINE)


#: 「角色（情绪）：台词」的前缀 —— 长度判据只该算**台词本身**。
_DLG_SPEAKER_RE = re.compile(r"^\s*[\*]*\s*[^：:\n]{1,14}\s*[\*]*\s*[：:]\s*")
#: 括注（情绪/动作提示）不算台词字数。
_DLG_PAREN_RE = re.compile(r"[（(][^）)]{0,40}[）)]")
#: 标点与空白也不算。
_DLG_PUNCT_RE = re.compile(r"[\s，。；、！？…—,.;!?\-·:：\"'「」『』【】()（）]")


def _dialogue_body(cell: str) -> str:
    """对白格的**净台词**（剥掉「角色：」前缀、括注、标点空白）—— 用于长度判据。

    注意：一个格子里可能有多个人说话（实测 village-tractor LN23 一格塞了 3 句），
    这里只剥**第一个**说话人前缀，其余原样计入长度 —— 长度判据要的是"这一镜
    到底要说多少字"，不是每句单独算。
    """
    t = _DLG_SPEAKER_RE.sub("", str(cell or ""))
    t = _DLG_PAREN_RE.sub("", t)
    return _DLG_PUNCT_RE.sub("", t)


def check_brief_fidelity(artifact_text: str, brief: dict | None = None,
                         threshold: float = FIDELITY_THRESHOLD) -> dict:
    """单个创作产物（director/plot/script/分镜…）是否覆盖 brief 的 must_have 故事要点。

    算法：把 must_have 条目与产物文本都切成**内容二元组**（`_content_bigrams`），算覆盖率；
    低于 threshold 视为该要点缺失。视觉/风格类条目跳过。

    ★ 本函数是忠实度判据的**唯一实现**（2026-09-14 收口）：`check_storyboard` 里原先
      另有一份手抄（阈值还是硬编码的 0.30），两份必然漂移 —— 实测正是那一份把
      "同义改写但完全覆盖"的分镜误判为缺失、拦下了整条媒体链。

    返回 {"coverage_missing": [...], "ok": bool}。缺项字符串带实测覆盖率，
    便于区分"擦线"与"完全没拍"。
    """
    all_tokens = _content_bigrams(artifact_text)
    missing: list[str] = []
    for item in (brief or {}).get("must_have") or []:
        item = str(item)
        if any(mk in item for mk in _STYLE_MARKERS):
            continue
        item_tokens = _content_bigrams(item)
        if not item_tokens:
            continue
        hit = sum(1 for t in item_tokens if t in all_tokens)
        cov = hit / len(item_tokens)
        if cov < threshold:
            missing.append("%s（覆盖 %.2f／阈值 %.2f）" % (item[:40], cov, threshold))
    return {"coverage_missing": missing, "ok": not missing}


def check_storyboard(md: str, brief: dict | None = None,
                     style_keywords: list | None = None) -> dict:
    """分镜表契约校验：schema / 镜序 / must_have 覆盖 / 空对白 / 画内文字 / 节奏。

    兼容现役分镜格式：镜头号为纯数字或 LN 前缀，允许多幕多张表（表头重复）。
    """
    # 表头选择：取**命中契约列最多**的表头行（2026-09-11 实测：产物把 SKILL 的
    # "列契约说明表"抄在正文前，第一个含"画面描述"的表头是说明表 → 误判缺列）。
    #
    # ★ 归一化**复用媒体链那一个**（2026-09-14 实测事故）：模型偶发漏写行首 `|`，
    #   而这里原先要求 `line.strip().startswith("|")` → 一个表头都认不出 →
    #   **所有列判"缺列"**（诊断还是错的：列都在，破的是表格语法）。
    #   同一个东西两份实现 → 必然漂移。
    from .media.storyboard import _ROW_RE as _SB_ROW_RE
    from .media.storyboard import normalize_table_line as _norm_row

    headers: list[str] = []
    best_hits = -1
    for line in md.splitlines():
        line = _norm_row(line)
        if not line.startswith("|"):
            continue
        cand = [c.strip().lower() for c in line.split("|")]
        hits = sum(1 for key, _ in _REQUIRED_COLS if any(key in c for c in cand))
        if hits > best_hits:
            headers, best_hits = cand, hits
    missing_cols = [name for key, name in _REQUIRED_COLS
                    if not any(key in c for c in headers)]

    # 数据行必须来自**主分镜表**：列数需与表头一致。否则文档里的辅助小表
    # （如"对白分布核验"只有 4 列）也会被当成数据行，污染镜序判定
    # （2026-09-10 实测：辅助表 [1,3,4,5] 拼在主表 [1,1,1,2,...] 前 → 误判"镜序错乱"）。
    _min_cols = len(headers) if headers else 6
    # ★ 镜头号行格式**与媒体链共用同一个正则**（2026-09-14 实测事故）。
    #
    # 原实现写死 `^(?:LN)?\d+` —— **不认 `S` 前缀**（`| S01 |`）；而媒体链的
    # `media/storyboard._ROW_RE` 认（`(?:LN|S)?`，那条注释还写着"旧实现只认前两种
    # → `S10` 静默丢镜"）。于是同一份牛来分镜：
    #     · **门**：解析出 **0 镜** → `seconds_total = 0` → `_duration_gap` 的
    #       `seconds_total <= 0` 分支**直接放行** → 门"通过了"它根本没看懂的分镜；
    #     · **媒体链**：正确解析 26 镜。
    # **同一个东西两份实现 → 必然漂移。** 这里改为直接复用媒体链那一个，
    # 并同样走 `normalize_table_line`（行首缺 `|` 也照样认）。
    # ★ 与 `storyboard.parse` **同一套去重规则**（按镜头号保留首次出现）。
    #   分镜文件里除了逐场主表，还有 `## 分镜总表` 与 `## 时长校验` —— 它们的行
    #   **长得和镜头行完全一样**（同样 10 列、同样 id）。不去重就会：30 行命中
    #   （10 真 + 10 总表 + 10 校验表）→ **重号 / 镜序错乱 / 总时长 140s（233%）**
    #   → 把一份**完全正确**的分镜拦下（实测连续三跑都因此作废）。
    shots: list[str] = []
    _seen_ids: set = set()
    deduped_rows = 0
    for _l in (_norm_row(x) for x in md.splitlines()):
        _m = _SB_ROW_RE.match(_l)
        if not _m or len([x.strip() for x in _l.split("|")]) < _min_cols:
            continue
        _k = (_m.group(1), _m.group(2))
        if _k in _seen_ids:
            deduped_rows += 1
            continue
        _seen_ids.add(_k)
        shots.append(_l)
    scenes: list[int] = []
    for l in shots:
        m = _SB_ROW_RE.match(l.strip())
        if m:
            scenes.append(int(m.group(1)))
    order_ok = scenes == sorted(scenes)

    # ★ 忠实度**复用 `check_brief_fidelity` 这份唯一实现**（2026-09-14 收口）：
    #   这里原先是对同一判据的第二份手抄，阈值还硬编码成 0.30，两份必然漂移 ——
    #   实测正是这一份把"同义改写但完全覆盖"的分镜判成缺失（覆盖 0.267 < 0.30），
    #   让 `STORYBOARD-REJECT` 拦下整条媒体链（创作链已跑完 21 分钟、白等）。
    missing = check_brief_fidelity(md, brief)["coverage_missing"]

    # 空对白检查：对白格为空会让模型把画面描述念成旁白（口型错乱/烧字幕）。
    dlg_idx = next((i for i, c in enumerate(headers)
                    if "对白" in c or "dialogue" in c.lower()), None)
    vis_idx = next((i for i, c in enumerate(headers)
                    if "画面" in c or "visual" in c.lower()), None)
    empty_dialog = 0
    spoken_shots = 0
    if dlg_idx is not None and vis_idx is not None:
        for l in shots:
            cells = [x.strip() for x in l.split("|")]
            dv = cells[dlg_idx] if dlg_idx < len(cells) else ""
            vv = cells[vis_idx] if vis_idx < len(cells) else ""
            if len(vv) >= 15 and (dv == "" or dv.isspace()):
                empty_dialog += 1
            if len(vv) >= 15 and _has_line(dv):
                spoken_shots += 1

    dur_idx = next((i for i, c in enumerate(headers)
                    if "时长" in c or "seconds" in c.lower()), None)
    # ★ 列**值**规范（2026-09-14 实测事故：契约原先只说"列结构与顺序不可变"，
    #   **没说值怎么写** → 模型自由发挥出**四种**镜头号写法，其中一种还重号）。
    #   这两条是**渲染层硬依赖**，且**纯确定性、零误判风险**：
    #     ① 镜头号去重（见上）：`shots` 已按镜头号保留首次出现；原始重复行数记在
    #        `deduped_rows` 里，由门**当提示打印**（可见但不阻断 —— 因为"总表/校验表"
    #        重复是**正常写法**，而"同一镜两行且第二行时长 `—`"也由"保留首次"自然修好）。
    #     ② 时长列必须是**数字**：`—` / 留空的行不是"可渲染的镜"（实测 9 行有数字、
    #        9 行是 `—`）—— 若不拦，`seconds_total` 只累加前者而 `n_shots` 计全部，
    #        两个数字来自不同的行集，后续所有比例都失真。
    missing_dur: list[str] = []
    if dur_idx is not None:
        for l in shots:
            cells = [x.strip() for x in l.split("|")]
            cell = cells[dur_idx] if dur_idx < len(cells) else ""
            if not re.search(r"\d", cell):
                missing_dur.append(cells[1] if len(cells) > 1 else "?")
    row_violations: list[str] = []
    if missing_dur:
        row_violations.append(
            "「时长(秒)」**不是数字**的镜：%s —— 时长必须是阿拉伯数字（如 `7`），"
            "不得写 `—` 或留空" % "、".join(missing_dur[:8]))
    text_dep: list[str] = []
    style_drift: list[str] = []
    seconds_total = 0.0
    durations: list[float] = []
    for l in shots:
        cells = [x.strip() for x in l.split("|")]
        shot_id = cells[1] if len(cells) > 1 else "?"
        if vis_idx is not None and vis_idx < len(cells):
            vv = cells[vis_idx]
            if any(k in vv for k in _TEXT_TRAPS):
                text_dep.append(shot_id)
            if style_keywords and not any(k in vv for k in style_keywords):
                style_drift.append(shot_id)
        if dur_idx is not None and dur_idx < len(cells):
            m = re.search(r"(\d+(?:\.\d+)?)", cells[dur_idx])
            if m:
                d = float(m.group(1))
                seconds_total += d
                durations.append(d)

    # 全表等长 = 节奏呆板（分镜偷懒）。真实节拍有呼吸：反应镜短、铺陈长。
    uniform_pacing = len(durations) >= 4 and len(set(durations)) == 1
    # 音频模式契约：brief 说 dialogue-led 就必须真的有台词。
    # 这一条是为了堵住「brief 写 dialogue-led、分镜全片（无声）」的实测事故。
    mode = audio_mode_of(brief)
    n_shot = len([1 for l in shots
                  if vis_idx is not None
                  and vis_idx < len([x.strip() for x in l.split("|")])
                  and len([x.strip() for x in l.split("|")][vis_idx]) >= 15])
    dialogue_ratio = (spoken_shots / n_shot) if n_shot else 0.0
    dialogue_short = bool(dialogue_required(brief)
                          and n_shot and dialogue_ratio < DIALOGUE_MIN_RATIO)
    # 达标但偏少：给警告，不阻断（短剧里动作镜多属正常，但值得提醒）
    dialogue_thin = bool(dialogue_required(brief) and n_shot
                         and DIALOGUE_MIN_RATIO <= dialogue_ratio < SHORT_RATIO_WARN)

    # 台词**长度**检查（2026-09-15）：只查占比会漏掉「沉。」「开。」这类残句
    # （实测 village-bridge 22 句平均 7.0 字，占比却达标）。两端都查：
    # 下限 = 承载不了信息；上限 = 念不完（与本镜秒数挂钩）。
    short_lines: list[str] = []
    long_lines: list[str] = []
    if dlg_idx is not None and vis_idx is not None:
        for l in shots:
            cells = [x.strip() for x in l.split("|")]
            dv = cells[dlg_idx] if dlg_idx < len(cells) else ""
            vv = cells[vis_idx] if vis_idx < len(cells) else ""
            if len(vv) < 15 or not _has_line(dv):
                continue
            sid = cells[1] if len(cells) > 1 else "?"
            sec = 0.0
            if dur_idx is not None and dur_idx < len(cells):
                m = re.search(r"\d+(?:\.\d+)?", cells[dur_idx])
                if m:
                    sec = float(m.group(0))
            body = _dialogue_body(dv)
            n = len(body)
            cap = int(min(DIALOGUE_MAX_CHARS,
                          sec * DIALOGUE_CHARS_PER_SEC if sec else DIALOGUE_MAX_CHARS))
            if n < DIALOGUE_MIN_CHARS:
                short_lines.append("%s「%s」(%d 字)" % (sid, body[:12], n))
            elif n > cap:
                long_lines.append("%s「%s」( %d 字 > 上限 %d)" % (sid, body[:12], n, cap))

    # 总时长 vs brief 目标（2026-09-13）：`seconds_total` 一直算却没被人用，
    # brief 那边 `target_duration` 也是必填 —— 接上这一根线，片长不对在
    # **分镜门口**就拦住，不用等整条媒体链跑完。
    dur = _duration_gap(seconds_total, brief)

    # ★ **解析不出镜头 = 不能算通过**（2026-09-14 实测，fail-closed）。
    #
    # 事故：门只认 `LN`/纯数字，不认 `S` 前缀 → 牛来分镜解析出 **0 镜** →
    # `seconds_total = 0` → `_duration_gap` 的 `seconds_total <= 0` 分支**直接放行**
    # → 门"通过了"一份它**根本没看懂**的分镜（镜序/片长/空对白/画内文字全部没查）。
    # 而媒体链解析出正确的 26 镜照常渲染。
    #
    # **判据看不见的东西不能算通过。** 只在"表头已识别、却一行数据都没解析出来"时判，
    # 所以纯空文件/无表格的产物不会误报（那种情况下方 schema_ok 也会报缺列）。
    unparsed = ""
    if headers and not shots:
        unparsed = ("分镜表头已识别（%d 列）但**解析不出任何镜头行** → 镜序/片长/空对白/"
                    "画内文字全部无从校验。检查「镜头号」列的写法"
                    "（支持 `1` / `1-1` / `LN01` / `S01`）" % len(headers))

    return {"schema_ok": not missing_cols, "missing_cols": missing_cols,
            "order_ok": order_ok, "scene_count": len(set(scenes)),
            "coverage_missing": missing, "empty_dialog": empty_dialog,
            "text_dependency": text_dep, "seconds_total": seconds_total,
            "target_seconds": dur["target"], "duration_ratio": dur["ratio"],
            "duration_off": dur["off"], "unparsed": unparsed,
            "row_violations": row_violations, "deduped_rows": deduped_rows,
            "uniform_pacing": uniform_pacing, "style_drift": style_drift,
            "audio_mode": mode, "spoken_shots": spoken_shots,
            "n_shots": n_shot, "dialogue_ratio": round(dialogue_ratio, 3),
            "dialogue_short": dialogue_short, "dialogue_thin": dialogue_thin,
            "short_lines": short_lines, "long_lines": long_lines,
            "ok": (not missing_cols and order_ok and not missing
                   and empty_dialog == 0 and not text_dep and not unparsed
                   and not row_violations)}
