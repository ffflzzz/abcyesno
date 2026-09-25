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
_ROW_RE = re.compile(r"^\|\s*(?:LN|S|镜|J)?\s*([\u2460-\u2473]|\d+)(?:-(\d+))?\s*\|")
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
#   · 静帧路径取**最后一拍**（单动作描述，防多拍序列诱发分屏——clockmaker
#     事故同类）；
#   · pack 视频路径把节拍时间戳重映射到整条请求的全局时间轴（prompt.py）。
# 格式两种都认（实测产物两种都有）：`0-2秒：` 与 `0-4s：`（拉丁 s）。
# ★ 必须跟冒号：`2-3秒后他转身` 这类**时长描述**不是节拍标记——
#   认错了会被静帧/pack 路径当节拍切割（误伤面大）；不满足完整格式
#   （数字-数字+秒/s+冒号）的文本一律当普通描述，安全降级。
_BEAT_RE = re.compile(r"(\d+)\s*[-–—]\s*(\d+)\s*(?:秒|s)\s*[：:]")


def split_beats(visual: str) -> list[tuple[int, int, str]]:
    """解析画面描述里的镜内节拍：`0-2秒：…；2-4秒：…` → [(0,2,文本), (2,4,文本)]。

    没有节拍标记（旧格式 / 标记不完整如缺冒号）返回空列表——安全降级为
    「无节拍镜」，原有路径原样消费。文本取标记之后到下一个标记（或结尾）。
    """
    text = visual or ""
    marks = list(_BEAT_RE.finditer(text))
    if not marks:
        return []
    beats: list[tuple[int, int, str]] = []
    for i, m in enumerate(marks):
        # 标记含尾部冒号，正文从其后开始
        body_start = m.end()
        body = text[body_start:]
        # 截到下一个标记（若有）
        if i + 1 < len(marks):
            body = body[:marks[i + 1].start() - body_start]
        body = body.strip("；;。 \n\t")
        beats.append((int(m.group(1)), int(m.group(2)), body))
    return beats


def beats_tiling_error(beats: list[tuple[int, int, str]], seconds: float) -> str:
    """校验节拍铺满整镜：从 0 起、首尾相接、终于时长列。返回错误描述（空=通过）。

    纯确定性判据：写不写节拍是自由，写了就必须自洽（时间轴是渲染层硬依赖，
    pack 提示词按它做全局重映射）。
    """
    if not beats:
        return ""
    if beats[0][0] != 0:
        return "第一节拍未从 0 秒开始（起点 %d 秒）" % beats[0][0]
    for (a, b, _), (c, d, _) in zip(beats, beats[1:]):
        if b != c:
            return "节拍 %d-%d 秒与 %d-%d 秒之间不连续（应首尾相接）" % (a, b, c, d)
        if d <= c:
            return "节拍 %d-%d 秒时长为零或为负" % (c, d)
    if beats[0][1] <= beats[0][0]:
        return "首节拍 %d-%d 秒时长为零或为负" % beats[0][:2]
    if abs(beats[-1][1] - seconds) > 0.5:
        return ("节拍只覆盖到 %d 秒，本镜时长 %s 秒——节拍必须覆盖整镜"
                % (beats[-1][1], seconds))
    return ""


def _col(headers: list[str], *keys: str) -> int | None:
    low = [h.strip().lower() for h in headers]
    for i, h in enumerate(low):
        if any(k in h for k in keys):
            return i
    return None


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

        def cell(idx: int | None) -> str:
            return cells[idx] if (idx is not None and idx < n) else ""

        visual = cell(i_visual)
        if len(visual) < 15:          # 占位/空行不入镜
            continue
        sec = 0
        if i_sec is not None and i_sec < n:
            msec = re.search(r"(\d+(?:\.\d+)?)", cells[i_sec])
            if msec:
                sec = int(float(msec.group(1)))
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
        })
    return shots
