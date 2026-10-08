# -*- coding: utf-8 -*-
"""Storyboard parsing: 8-column markdown → shots.

格式（脚手架与旧管线一致，保证分镜文件可迁移）：
    ## S1 / 6s
    | 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |
    |--------|------|-----|------|---------|---------|------|------|
    | 1 | 全景 | 平视 | 固定 | 6 | ... | ... | ... |

解析是确定性的（不是让模型再读一遍）——平台纪律。
"""
from __future__ import annotations

import re

# 镜头号列格式（2026-09-10 扩充）：四种写法都见过，全部必须认。
#   裸数字 `| 1 |`   —— 脚手架默认
#   集-镜 `| 1-1 |`  —— nightshift-45
#   S 前缀 `| S10 |` —— maskparade（分镜师跟着小节标题 `## S10 / 6s` 写）
#   **镜前缀 `| 镜7 |`** —— dawn-broadcast（2026-09-14）；不认它会让**整张表解析不出**
#   旧实现只认前两种 → `S10` 静默丢镜、整轮 media 解析出 0 镜、创作链白跑。
_ROW_RE = re.compile(r"^\|\s*(?:LN|S|镜|J|L)?\s*([\u2460-\u2473]|\d+)(?:-(\d+))?\s*\|")
# ★ 2026-09-23：兼容圈号镜号（①②③…⑳，half-narrated 舞狮项目实测产物）——
#   消费处统一转成阿拉伯数字，下游 int() 不会崩。
_SEP_RE = re.compile(r"^\|[\s:\-|]+\|$")


def normalize_table_line(line: str, min_pipes: int = 2) -> str:
    """把**缺前导/尾随竖线**的 pipe-table 行补齐成标准 markdown 表格行。

    ## 为什么需要（2026-09-14 实测，两个解析器同一天各中一次）

    模型写 pipe table 时会**偶发漏掉行首的 `|`**，例如真实产物：
        `镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效`
        `01 | 近景 | 平视 | 固定，末尾极轻右摇到门口 | 6 | 原始低成本三维重建的…`
    而 `parse` 与 `validate.check_storyboard` 都要求 `line.startswith("|")` →
    **一个表头、一行数据都认不出** →
      · `validate.check_storyboard`：所有列判"缺列" →
        `[STORYBOARD-REJECT] 分镜缺列：画面描述、对白、景别、运镜、时长、镜头号`
        （**诊断还是错的** —— 列都在，破的是表格语法）；
      · `parse`：解析出 0 镜。
    这与「reviewer 漏写代码围栏」是同一种病：**解析器只认一种写法**。
    所以归一化放在这里当**唯一真相源**，两个解析器都调它。

    `min_pipes` 默认 2：少于这个数说明不像表格行（正文里偶发的单个 `|` 不该被当成表），
    原样返回 → 调用方按"非表格行"跳过。
    """
    s = (line or "").strip()
    if not s or s.startswith("|"):
        return s
    if s.count("|") < min_pipes:
        return s
    return "| " + s + " |"
HEADER_KEYS = ("镜头号", "景别", "角度", "运镜", "时长", "画面描述", "对白", "音效")


# ─── 镜内节拍（2026-09-25：分镜智能体的节奏设计权下放）──────────────────────
#
# 背景：Pavo 参考片实证（同 agnes 模型）——一条 12s 生成里塞 6 个逐秒节拍
# （2s/拍），模型精确执行且身份一致。旧管线每镜下限 4s（pack_clamp_sec），
# 一条 12s 请求最多 3 拍，密度差 2-3 倍。现在把「拍怎么切」交给 scenedesigner
# 按剧本节奏设计：画面描述列写镜内节拍，格式：
#
#     0-2秒：@婆婆右手捏着一炷香凑近火盆；2-4秒：右手把香插回盆里。
#
# 契约（门在 validate.check_storyboard 校验，见 beat_violations）：
#   · 节拍必须从 0 开始、首尾相接、覆盖整镜时长（时长列数字）；
#   · 不写节拍的镜（旧格式）完全合法——向后兼容，零影响。
# 消费：
#   · 静帧路径取**第一拍**（2026-09-26 brawl 实测后由"最后一拍"改定：取最后一拍会让
#     静帧提示词里一个服装词都不剩，同组静帧穿出两套外套；见 `prompt.content_line`）；
#   · pack 视频路径把节拍时间戳重映射到整条请求的全局时间轴（prompt.py）。
# 格式两种都认（实测产物两种都有）：`0-2秒：` 与 `0-4s：`（拉丁 s）。
# ★ 必须跟冒号：`2-3秒后他转身` 这类**时长描述**不是节拍标记——
#   认错了会被静帧/pack 路径当节拍切割（误伤面大）；不满足完整格式
#   （数字-数字+秒/s+冒号）的文本一律当普通描述，安全降级。
# ★ **端点允许小数**（`0-0.5秒：`）。旧正则是纯 `(\d+)`，遇到小数标记不是"认不出"
#   而是**认错**：`0.5-1秒：` 里的前导 `0` 被丢掉，匹配成起点 5、终点 1 的**倒挂拍**。
#   实测后果两处：① `beats_tiling_error` 报「第一节拍未从 0 秒开始（起点 5 秒）」
#   ——一条与作者意图无关的假理由把整张分镜表拦下；② 侥幸过了门的，
#   `_remap_beats` 会按这个倒挂轴做全局重映射。现在小数按浮点如实解析，
#   整数端点仍返回 int（**未写小数的项目输出逐字不变**）。
_BEAT_RE = re.compile(
    r"(\d+(?:\.\d+)?|\.\d+)\s*[-–—]\s*(\d+(?:\.\d+)?|\.\d+)\s*(?:秒|s)\s*[：:]")

#: 浮点比较容差：节拍是"人写的秒数"，0.3+0.3+0.4 这类累加噪声不该被判成不连续。
_BEAT_EPS = 0.01


def beat_label(x) -> str:
    """节拍端点的人读/机读写法：整数不带小数点（`2`），小数保留到百分位（`0.75`）。

    提示词与门的报错文案都用它——用 `%d` 会把小数直接截成错的整数。
    """
    v = round(float(x), 2)
    return "%g" % v


def _beat_num(raw: str):
    v = float(raw)
    return int(v) if v.is_integer() else round(v, 2)


def split_beats(visual: str) -> list[tuple[float, float, str]]:
    """解析画面描述里的镜内节拍：`0-2秒：…；2-4秒：…` → [(0,2,文本), (2,4,文本)]。

    端点可以是小数（`0-0.5秒：`）。整数端点返回 int、小数返回 float。

    没有节拍标记（旧格式 / 标记不完整如缺冒号）返回空列表——安全降级为
    「无节拍镜」，原有路径原样消费。文本取标记之后到下一个标记（或结尾）。
    """
    text = visual or ""
    marks = list(_BEAT_RE.finditer(text))
    if not marks:
        return []
    beats: list[tuple[float, float, str]] = []
    for i, m in enumerate(marks):
        # 标记含尾部冒号，正文从其后开始
        body_start = m.end()
        body = text[body_start:]
        # 截到下一个标记（若有）
        if i + 1 < len(marks):
            body = body[:marks[i + 1].start() - body_start]
        body = body.strip("；;。 \n\t")
        beats.append((_beat_num(m.group(1)), _beat_num(m.group(2)), body))
    return beats


def beat_prefix(visual: str) -> str:
    """第一个节拍标记**之前**的那段正文（没有标记就是整段）。

    ★ 为什么单独取它（2026-10-02 实测）：分镜契约要求「第一拍写全角色锚点」，
    而作者很自然地把它写成标记前的一句总起。`luanzhen-xue-1001` LN01 的真实产物：

        （闯入发难·凌厉）@周娘子身着绛紫绸衫，右手戴@周娘子的护甲，赤金簪与金耳坠，
        左侧两名模糊随从剪影紧随其后　**0-2s：**@周娘子自画面左侧快步走入…

    `split_beats` 按定义只取标记**之后**的文本 ⇒ 这一整句锚点（绛紫绸衫／赤金簪／
    金耳坠／随从剪影）**既进不了静帧提示词**（`content_line(beat_pick="first")` 取
    `_beats[0][2]`），**也进不了 pack 视频提示词**（`_remap_beats` 只回节拍）。
    实测 LN01/LN02 的 `stills.json` 里这四个词**一个字都不在**，而日志全绿、
    门全过 —— 身份锚点是历次漂移的头号来源（见记忆「定妆照成了新的单点故障」），
    丢在这里等于契约白写。调用方负责把它带回去。
    """
    text = visual or ""
    m = _BEAT_RE.search(text)
    if not m:
        return text.strip()
    return text[:m.start()].strip(" ；;\n\t")


def beats_tiling_error(beats: list[tuple[float, float, str]], seconds: float) -> str:
    """校验节拍铺满整镜：从 0 起、首尾相接、终于时长列。返回错误描述（空=通过）。

    纯确定性判据：写不写节拍是自由，写了就必须自洽（时间轴是渲染层硬依赖，
    pack 提示词按它做全局重映射）。
    """
    if not beats:
        return ""
    if abs(float(beats[0][0])) > _BEAT_EPS:
        return "第一节拍未从 0 秒开始（起点 %s 秒）" % beat_label(beats[0][0])
    for (a, b, _), (c, d, _) in zip(beats, beats[1:]):
        if abs(float(b) - float(c)) > _BEAT_EPS:
            return ("节拍 %s-%s 秒与 %s-%s 秒之间不连续（应首尾相接）"
                    % (beat_label(a), beat_label(b), beat_label(c), beat_label(d)))
        if float(d) <= float(c):
            return "节拍 %s-%s 秒时长为零或为负" % (beat_label(c), beat_label(d))
    if float(beats[0][1]) <= float(beats[0][0]):
        return ("首节拍 %s-%s 秒时长为零或为负"
                % (beat_label(beats[0][0]), beat_label(beats[0][1])))
    if abs(float(beats[-1][1]) - float(seconds)) > 0.5:
        return ("节拍只覆盖到 %s 秒，本镜时长 %s 秒——节拍必须覆盖整镜"
                % (beat_label(beats[-1][1]), seconds))
    return ""


def _col(headers: list[str], *keys: str) -> int | None:
    low = [h.strip().lower() for h in headers]
    for i, h in enumerate(low):
        if any(k in h for k in keys):
            return i
    return None


def _act_num(raw: str) -> int:
    """「场次」单元格 → 场号整数。认 `3`／`场3`／`第3场`／`场 3 验尸房·凌晨`。

    解析不出就是 0（= 这一集没用场次列），**不猜**：猜错场号会让打包档把
    两段不同的戏并成一条请求，而那正是这条改动要解决的问题。
    """
    s = (raw or "").strip()
    if not s:
        return 0
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else 0


#: 「本镜不绑角色参考图」标记（2026-09-27，xianxia-vfx-action 化身镜实测新增）。
#: 写在「画面描述」单元格任意位置，解析时**从正文里剥掉**并置 `shot["no_human"]=True`。
#: 为什么需要它（而不是靠措辞）：仙侠包的「人化作兽形能量体」那一镜，分镜已经不写
#: `@角色名` 了，`bind()` 仍会经 keywords 兜底 + 角色补漏把**两张人物设定表**捞回来，
#: 提示词于是同时要求"锁定该角色的长相与服装形制"和"没有站立的人形"——
#: 前者更具体，模型照后者画成两个静态人像。参考图是绑定层的事实，只能由绑定层关。
#: 生效点：`assets.bind`（跳过 character 类）、`assets.cast_counts`（记 0 人）、
#: `prompt.person_directive`（**不注入任何人数声明**——空镜声明会说"环境静物"，
#: 与"能量构造体在爆炸"直接冲突，所以这一镜的人数措辞交给分镜正文自己写）。
NO_HUMAN_MARKS = ("【无人像】",)


def parse(md: str) -> list[dict]:
    shots: list[dict] = []
    headers: list[str] = []
    heading = ""
    _seen_ids: set = set()          # 按镜头号去重（见下方说明）
    for lineno, raw in enumerate(md.splitlines()):
        line = raw.strip()
        if line.startswith("#"):
            heading = line
            continue
        # ★ 先补前导/尾随竖线（见 `normalize_table_line`）：模型偶发漏写行首 `|`，
        #   只认标准写法会让**整张表**解析不出（实测：26 镜全丢）。
        line = normalize_table_line(line)
        if not line.startswith("|"):
            continue
        if _SEP_RE.match(line):
            continue
        cells = [c.strip() for c in line.split("|")]
        # ★ 崩坏行检测（2026-09-18 实测；只为**可见性**，不改变解析结果）。
        #
        # 模型在长输出后期会把「表头前若干列的列名」与「数据后若干列的内容」挤成
        # **同一行**：该行含多个列名、却又不以镜头号开头。旧行为把它静默吞掉
        # ⇒ **该镜消失、且全流程零报错**（实测：一部 30 镜的片因此丢了最后 8 镜
        #   含定格结局，总时长仍落在合格区间内，评审与三道输入门全部放行）。
        #
        # ★ 判据必须**独立于下面的表头分支**：挂在 `_col(cells,"画面")` 之下会漏检
        #   —— 视觉风格列写「同上」的崩坏行不含「画面」二字，仍会静默跳过
        #   （实测 8 行只报出 5 行）。这里改成独立判断，覆盖全部崩坏行。
        # ★ 不会对正常表头误报：表头只有短列名，不存在 >=20 字的内容单元格；
        #   每场重复表头（本仓库的既有合法形态）同样只有短列名。
        if not _ROW_RE.match(line):
            _hit = [k for k in HEADER_KEYS if any(k in c for c in cells)]
            if len(_hit) >= 3 and any(len(c) >= 20 for c in cells):
                print("[storyboard] !! 第 %d 行疑似「表头与数据混排」（命中列名：%s）"
                      " -> 该行被当作表头丢弃，对应镜头会**静默消失**，"
                      "请检查分镜产物的表格结构（表头是否被逐场重复、数据行是否缺列）"
                      % (lineno + 1, "/".join(_hit[:6])))
        # 表头判定用**结构**而不是关键词：数据行以「| 数字 |」开头，表头不是。
        # （占位符里出现"对白/音效"等词曾让数据行被误判成表头——措辞不可靠。）
        if _col(cells, "画面") is not None and not _ROW_RE.match(line):
            headers = cells
            continue
        m = _ROW_RE.match(line)
        if not m or not headers:
            continue
        # ★ **按镜头号去重，保留首次出现**（2026-09-14 实测三次事故）。
        #
        # 分镜文件里**不止一张表**：主分镜表之外还常有
        #   · `## 分镜总表`（把 10 镜再汇总一遍，**id 与主表完全相同**）
        #   · `## 时长校验`（`| 镜头号 | 时长(秒) |` 两列汇总，id 也相同）
        #   · `## 每秒五象限面板`（**牛来包契约要求**的，5 列表、行是 `| 0s |` —— 这个
        #     本来就不匹配 `_ROW_RE`，无害）
        # 前两种的每一行都长得像镜头行 → 旧实现把它们也算成镜：
        # 实测 30 行命中（10 真 + 10 总表 + 10 校验表）→ **重号 / 镜序错乱 / 总时长 140s（233%）**
        # → 分镜契约门把一份**完全正确**的分镜拦下（连续三跑都因此作废）。
        # 另一类：同一镜被写成两行、两行同号（第二行时长写 `—`）→ 保留首行正好留下有数字的那行。
        # **保留首次**的理由：主分镜表在前、汇总/校验表在后，且两者时长一致（实测）。
        _key = (m.group(1), m.group(2))
        if _key in _seen_ids:
            continue
        _seen_ids.add(_key)
        n = len(cells)
        i_visual = _col(headers, "画面", "visual") or 0
        i_dlg = _col(headers, "对白", "dialogue")
        i_sec = _col(headers, "时长", "秒")
        i_shot = _col(headers, "景别")
        i_ang = _col(headers, "角度")
        i_cam = _col(headers, "运镜")
        # 可选列：分镜可给「场景/视觉风格/落幅/文字镜/承接」，
        # 缺列时下面统一取空串（向后兼容旧分镜）
        i_scene = _col(headers, "场景", "scene")
        i_style = _col(headers, "视觉风格", "风格", "style")
        i_tail = _col(headers, "落幅", "收尾", "tail")
        i_text = _col(headers, "文字镜", "text_shot")
        i_join = _col(headers, "承接", "join")
        i_sfx = _col(headers, "音效", "sfx")
        #: 「场次」列（2026-10-05 新增）：场 = 一次生成 = 一条 ≤12 秒请求的容器。
        #: ★ 只认「场次/场号/act/scene_no」，**不认单字「场」**——`_col` 是子串匹配，
        #:   「场」会命中「场景」，于是地点列被当成场号（同型的键名漂移本仓库犯过多次）。
        i_act = _col(headers, "场次", "场号", "scene_no", "act")

        def cell(idx: int | None) -> str:
            return cells[idx] if (idx is not None and idx < n) else ""

        visual = cell(i_visual)
        if len(visual) < 15:          # 占位/空行不入镜
            continue
        #: 「无人像」标记：从正文剥掉（它不是画面内容，不该进提示词），另置标志位。
        no_human = any(mk in visual for mk in NO_HUMAN_MARKS)
        if no_human:
            for mk in NO_HUMAN_MARKS:
                visual = visual.replace(mk, "")
            visual = visual.strip()
        sec = 0
        if i_sec is not None and i_sec < n:
            msec = re.search(r"(\d+(?:\.\d+)?)", cells[i_sec])
            if msec:
                #: ★ 2026-10-05：小秒数**必须留住**。旧写法 `int(float(...))` 把 0.5 秒
                #: 截成 0，于是快切镜在分组时被 `pack_clamp_sec` 的"或 4"兜成 4 秒 ——
                #: 分镜声明的节拍边界与实际下单秒数脱节。整数照旧给 int（不动既有消费方）。
                f = float(msec.group(1))
                sec = int(f) if f == int(f) else round(f, 2)
        _shot_num = m.group(1)
        if "①" <= _shot_num <= "⑳":      # 圈号镜号 ①②③… → 阿拉伯数字
            _shot_num = str(ord(_shot_num) - ord("①") + 1)
        shots.append({
            "index": int(_shot_num),
            "name": "LN%02d" % (len(shots) + 1),
            "heading": heading,
            #: 该行在原文 `splitlines()` 里的下标（0 基）。
            #: 供**回写编辑**用（`v5/webwrite.py` 按行改单元格）——
            #: 让"哪些行算镜头"这件事只有本函数一处判据，别处不再重实现。
            "line": lineno,
            "visual": visual,
            "no_human": no_human,
            "dialogue": cell(i_dlg),
            "seconds": sec,
            "shot_type": cell(i_shot),
            "angle": cell(i_ang),
            "camera": cell(i_cam),
            "scene": cell(i_scene),
            "visual_style": cell(i_style),
            "tail": cell(i_tail),
            "text_shot": cell(i_text),
            "join_note": cell(i_join),
            "sfx": cell(i_sfx),
            #: 场号（整数，缺列或解析不出 = 0）。见 `i_act` 的注释。
            "act": _act_num(cell(i_act)),
            "act_label": cell(i_act),
        })
    return shots


def repair_beat_continuity(md: str) -> tuple[str, list[str]]:
    """把**镜内节拍的首尾断裂**按算术改成"首尾相接"（只动起点数字，内容一字不改）。

    ★ 为什么由代码做（2026-10-08 实测 `yuxuan-duanfeng-1007` 第 2 集）：
      分镜要写「12 秒 / 8-12 段」，镜1 第三段写成 `2.5-4.5秒：`（上一段终点是 3）。
      这是一处**纯算术**笔误，而时间轴是渲染层硬依赖（pack 提示词按节拍做全局
      重映射，断链会让两镜抢同一秒）。**打回两轮都没修**：第一轮原样交回，
      第二轮改完仍然是 2.5 ⇒ 让模型反复做加法不是契约，是空转。
      同一条原则已在别处落地（AGENTS.md：「能由代码确定做到的事不该反复要求模型」）。

    ⛔ 只修一种形状，其余一律原样交回给门判：
      · 第 i≥1 段的起点 ≠ 上一段的终点，**且**改成上一段终点后仍满足 起点 < 本段终点；
      · 不动第一段的起点（"必须从 0 起"是作者的决定，推不出来）；
      · 不动任何**终点**（终点=作者给的节拍长度，动它等于改戏）；
      · 会让本段塌缩（起点 ≥ 终点）的**不修** —— 那要挑一段来牺牲，属语义判断；
      · 只动「画面描述」那一列（表头认不出 ⇒ 整表不动）。

    返回 `(新文本, 变更说明)`；没有可修的断裂时原样返回（调用方可据此零改动）。
    """
    lines = md.splitlines(keepends=True)
    headers: list[str] = []
    vis_idx: int | None = None
    changes: list[str] = []
    out: list[str] = []
    for raw in lines:
        line = normalize_table_line(raw.strip())
        if not line.startswith("|") or _SEP_RE.match(line):
            out.append(raw)
            continue
        cells = [c.strip() for c in line.split("|")]
        if not headers:
            if _col(cells, "画面") is None:
                out.append(raw)          # 不是表头，也不修（下一行再试）
                continue
            headers = cells
            vis_idx = _col(headers, "画面")
            out.append(raw)
            continue
        # 数据行
        if vis_idx is None or vis_idx >= len(cells) or len(cells) < 3:
            out.append(raw)
            continue
        cell = cells[vis_idx]
        new_cell, item = _repair_cell_beats(cell, cells[1] if len(cells) > 1 else "?")
        if item and cell in raw:
            # ★ 只在原文里**就地替换那一段单元格**：整行重建会把模型的列间空格、
            #   崩坏行的格数一起"修"掉 —— 那不是本次变更要碰的东西。
            out.append(raw.replace(cell, new_cell, 1))
            changes.append(item)
        else:
            out.append(raw)
    return "".join(out), changes


def _repair_cell_beats(cell: str, shot_id: str) -> tuple[str, str]:
    """单个「画面描述」单元格的节拍起点重排。返回 `(新单元格, 变更说明)`（无变更则原串+空串）。"""
    marks = list(_BEAT_RE.finditer(cell))
    if len(marks) < 2:
        return cell, ""
    fixes: list[tuple[int, int, str]] = []     # (start_off, end_off, 新起点文本)
    notes: list[str] = []
    for i in range(1, len(marks)):
        prev_end = _beat_num(marks[i - 1].group(2))
        m = marks[i]
        start = _beat_num(m.group(1))
        end = _beat_num(m.group(2))
        if abs(float(start) - float(prev_end)) <= _BEAT_EPS:
            continue                          # 本来就相接
        if float(prev_end) >= float(end) - _BEAT_EPS:
            break                             # 会塌缩 ⇒ 后面的段一并交给门判，不猜
        fixes.append((m.start(1), m.end(1), beat_label(prev_end)))
        notes.append("%s 第%d段起点 %s→%s 秒" % (shot_id, i + 1,
                                                beat_label(start), beat_label(prev_end)))
    if not fixes:
        return cell, ""
    new = cell
    for a, b, txt in reversed(fixes):
        new = new[:a] + txt + new[b:]
    return new, "；".join(notes)
