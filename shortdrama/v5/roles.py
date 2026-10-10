# -*- coding: utf-8 -*-
"""角色装配层（2026-09-12 从 graph.py 抽出）。

为什么独立：supervisor 架构（orchestrator.py）只依赖这一层——FS_TOOLS /
role_system_prompt / role_input；graph.py 的其余部分（StudioState + 节点工厂 +
build + _route_after_review）属**静态链 DAG**。抽出后静态链成为孤岛，
可独立废弃而不影响 supervisor 与媒体链。
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from . import config, guards, inbox, validate
from . import mode as chain_mode          # ⚠️ 必须带别名！
from .guards import PREREQ, out_path
# ⚠️ 为什么带别名：本模块的 `role_input` 里**早就有**一个局部变量叫 `mode`
# （第 521 行 `mode = validate.audio_mode_of(...)`，指**音频模式**）。
# Python 的作用域在**编译期**决定 —— 只要函数体内出现过 `mode = ...`，
# 整个函数里的 `mode` 都是局部的 ⇒ 直接 `import mode` 会在注入点报
# `UnboundLocalError`（实测 18 个测试当场红）。
# 两义同名不是巧合，是真会踩的坑，所以别名取得**自解释**：`chain_mode`（链的模式）
# vs 局部 `mode`（音频模式）。


# 角色节点可用的文件工具（deepagents 的 FilesystemMiddleware 提供）。
# 注意：**不含 execute**——创作角色不该有跑命令的能力。
# 角色节点可用的文件工具（deepagents 的 FilesystemMiddleware 提供）。
# 注意：**不含 execute**——创作角色不该有跑命令的能力。
FS_TOOLS = ["ls", "read_file", "write_file", "edit_file", "glob", "grep"]


# ─── 提示词 ──────────────────────────────────────────────────────────────────

TOOL_NOTE = """

【本系统实际提供的工具】ls / read_file / write_file / edit_file / glob / grep。
技能文档里若提到 generate_image / generate_turnaround / ingest_reference 等
**本系统未提供**的工具，直接跳过那一步，不要反复尝试。参考图由媒体层根据你写的
角色卡/资产卡**确定性生成**——所以卡片里的外貌与用途描述就是出图提示词：
只写**外形与材质**，不要写人物关系、剧情动作、文字水印要求（这些会污染出图）。
你唯一的交付物是**用 write_file 写出的产物文件**。
只输出文本不算完成。
【交付顺序】输入资料已内联且足够时，直接调用 write_file；确有缺失才 read_file。
不要在聊天回复中输出规划草稿、逐项自查、镜数试算或产物正文，避免耗尽输出额度却没有文件。
把完整产物放进文件工具的 content 参数，语言遵守 brief，完成后只回复产物路径与一句结果。
工具报错时按错误修正，不用聊天正文替代落盘。"""


def _role_skill(pack: str, role: str) -> str:
    for cand in (config.SKILLS_DIR / "packs" / (pack or "shortdrama") / role / "SKILL.md",
                 config.SKILLS_DIR / "packs" / "shortdrama" / role / "SKILL.md"):
        if cand.exists():
            return cand.read_text(encoding="utf-8")
    return ""


def role_system_prompt(pack: str, role: str, ep: int = 1) -> str:
    """角色节点自己的 system prompt：技能文档 + 工具说明 + 产物路径**形状**契约。

    ★ M3（2026-09-17）：**路径不再写死集号**。
    为什么必须改：本函数在 `build_supervisor()` 里求值 ⇒ **编译期固化**。
    写死集号会带来两个后果：
      ① 换集必须重启 dev（运维坑，且沙箱里杀不掉持口进程）；
      ② 一个 dev server 只能服务一集 ⇒ M3 的「一次起服跑 N 集」**根本不可能**。
    现在这里只给**形状**（含 `{N}` 的模板，取自 `guards.OUTPUTS` 唯一真相源），
    **确切路径由每次开工的 `role_input` 给出**（`【本集产物路径】`）——那是运行期的真相。

    ⚠️ 为什么保留「【本角色产物路径】」这个**标题词**：多个类型包的 SKILL 文案写着
    「路径由 system prompt 的【本角色产物路径】给出」（防它们自己拼死路径）。
    改标题词会让那些指引失锚，所以标题词不动，只在正文里把权威来源指向开工契约。
    """
    from .guards import OUTPUTS
    tmpl = OUTPUTS.get(role) or out_path(role, ep)
    # ⚠️ 措辞必须**按角色分支**（2026-09-17 真机验收抓到的 bug）：
    #   原先无条件写「本角色的产物**是集级的**」——对 worldbuilder / assetdesigner /
    #   plotdesigner / director 这四个**全剧级**角色是**假话**，模型据此自己给文件名
    #   加了 `_ep1`（实测产出 `worldbuilder/worldbuilder_ep1.md`）→ 物化守卫按声明路径
    #   `worldbuilder/worldbuilder.md` 找不到 → 整条链白跑。**错误的元信息比没有更糟。**
    if "{N}" in tmpl:
        _scope = ("本角色的产物**是集级的**（每集一份），路径形如 /%s"
                  "（`{N}` = 本集集号）。" % tmpl)
    else:
        _scope = ("本角色的产物是**全剧级的**（一次锁定、全剧复用），路径**固定**为 /%s —— "
                  "**不要**给文件名加集号后缀。" % tmpl)
    skill = _role_skill(pack, role)
    if role == "assetdesigner" and "assets.contract.json" in skill:
        _scope += ("同时用第二次 write_file 写独立 /assetdesigner/assets.contract.json，"
                   "它不是 assets.md 内的代码块，两份文件都必须交付。")
    return (skill or ("你是 " + role + "。")) + TOOL_NOTE + (
        "\n\n【本角色产物路径】" + _scope
        + "**确切路径以本条消息之后的《本集产物路径》一行为准**"
          "—— 那一行是**运行期**给定的。不要自己推集号，也不要去写别的集的文件。")


# ── 「方案 D」已撤销（2026-09-19）：director **不再**兼任 worldbuilder ──────────
#
# 2026-09-13 ~ 2026-09-19 间，这里曾往 supervisor 的 system prompt 里追加一大段
# 「你同时兼任 worldbuilder —— 这是你开工的第一件事」，并把 worldbuilder 的整份
# SKILL.md 原文附上，同时把 `worldbuilder` 从子代理清单移除（省 5:33）。
#
# ⛔ 2026-09-19 实测撤销，理由（完整记录在 `orchestrator.make_async_subagents` 上方）：
#   ① pack 的 **per-role 覆盖被废掉一半** —— 产物由 director 写 ⇒ 执行的是
#      `packs/<pack>/director/SKILL.md`，`packs/<pack>/worldbuilder/SKILL.md`
#      **永远到不了执行者**（pack 机制的意义正在于 per-role 覆盖）；
#   ② **同一个 prompt 里两条相反指令**：三个包的 director SKILL 都写着
#      「不要替下游角色写产物」，本函数却要求「先自己把 worldbuilder 写完」——
#      模型照自己那份契约交了 `## 角色总览` **表格**，不是契约要求的
#      `## 角色卡：<名>` 段；
#   ③ **静默失败、代价在成片**：`cast.parse_characters` 的正则
#      `^#+\s*角色卡\s*[:：]` 对表格**解析出 0 个角色** → 日志只报「所有镜按无人物
#      处理」→ 参考图不生成 → 到成片才暴露。
#   ⇒ 「把产物交给下游那个**唯一持有正确契约**的执行者」，比省一次 agent loop 值钱。
def director_system_prompt(pack: str, ep: int = 1) -> str:
    """supervisor（= director）的 system prompt：**只含 director 自己的契约**。

    编排纪律（依赖序 / 派发）由 `orchestrator.ORCHESTRATOR_DISCIPLINE` 追加，
    见 `orchestrator.supervisor_system_prompt`。
    """
    return role_system_prompt(pack, "director", ep)


def _inline_file(root: Path, rel: str) -> str:
    """把一个产物文件读成"注入块"（带小标题）。读不到就**如实标注**，不静默留空。"""
    p = root / rel
    try:
        body = p.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return "### %s\n（读不到 —— 若你依赖它，请用 read_file 确认路径）" % rel
    if not body:
        return "### %s\n（空文件）" % rel
    return "### %s\n%s" % (rel, body)


def _brief_inline(root: Path, ep) -> str:
    """注入 brief.json —— **本集该看到的那一份**（按集写的字段在 `brief_for_ep` 里解析）。

    为什么不是直接 `_inline_file`：连载里有的集换了主角/场景/结局，而 `must_have` /
    `protagonist` / `结局` 是全剧级的一份 ⇒ 那一集的分镜师与评审看到的是**别人那集的**
    要求（实测第 5 集 `must_have` 四条全灭、三轮后 force_passed、输入门拦下、零出片）。

    ⛔ 旧行为兼容：brief 里没有按集写法时，输出与 `_inline_file(root, "brief.json")`
    **逐字相同**（同标题、同文本）——只是多走一次解析。
    """
    p = root / "brief.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- 读不动/解析不了：交给下游那道门响亮报
        return _inline_file(root, "brief.json")
    from . import validate
    resolved = validate.brief_for_ep(data, ep)
    if resolved == data:
        return _inline_file(root, "brief.json")
    return ("### brief.json（第 %s 集视图 —— 按集写的字段已解析成这一集的）\n%s"
            % (ep, json.dumps(resolved, ensure_ascii=False, indent=2)))


# ─── 按集切片注入（M5，2026-09-17）──────────────────────────────────────────
#
# 问题（spec M5 ①）：改造后 `plotdesigner/episodes.md` 是**全剧级**产物，而
# `role_input` 把上游产物**全文 inline**（`INLINE_UPSTREAM` 默认开）。
# 单集时代它只有几十行、无害；**集数一多，同一个机制就变成灾难**：
# 1,404 集的分集目录 ≈ 42 KB ⇒ **每一集的下游角色都会收到全份目录**
# ⇒ 上下文爆炸 + 注意力彻底稀释。
#
# ⚠️ 注意这是**改造引入的新问题**（老架构没有"全剧目录"这个概念）。

#: 「全剧级」产物的**切片阈值**（字符）。超过它就不再全文 inline，改为按集切片。
#: 取值依据：spec-m0-checklist §B3（4000 字符）。低于它的一律**照旧全文**——
#: 单集项目的行为**完全不变**（这是本改动最大的风险面，所以阈值要高得够用）。
#:
#: 可用 `SHORTDRAMA_SLICE_THRESHOLD` 覆盖：**给真机验证用**（2 集的目录天然很短，
#: 想验证切片就得把阈值压低）。生产上不要设它。
SLICE_THRESHOLD = 4000
SLICE_THRESHOLD_ENV = "SHORTDRAMA_SLICE_THRESHOLD"


def _brief_episodes(root: Path) -> int:
    """brief 声明的集数（读不到按 1 算 —— 切片这件事只对多集连载有意义）。"""
    try:
        return int(json.loads((root / "brief.json").read_text(encoding="utf-8"))
                   .get("episodes", 1) or 1)
    except Exception:  # noqa: BLE001 -- brief 读不动时另有门会响亮拦，这里不重复报
        return 1



def drop_solo_opponent_items(items: list, shots: list) -> list:
    """把**对手类**语义条目从「单人镜」上剔掉（判据由程序核，不靠模型自觉）。

    2026-10-08 实测两处都撞上：提示词里明明写了"无对手出场时这条不适用"，
    模型**照报**（LN18 独演镜、LN08 单人收势镜各一次）。语义判据要靠程序核验 ——
    单人镜（本镜只 @ 到一个名字）上不存在"对方没被攻击""对手被写成背景"这两件事。
    """
    import re as _re
    try:
        from . import shotcheck as _sc
        labels = [v for k, v in _sc.CODE_LABELS.items()
                  if k in ("opponent_as_background", "rock_chopping_as_beat")]
    except Exception:                                          # noqa: BLE001
        return items
    by_name = {s.get("name"): s for s in shots}
    out = []
    for it in items:
        if not any(l in it for l in labels):
            out.append(it)
            continue
        m = _re.search(r"(LN\d+)", it)
        shot = by_name.get(m.group(1)) if m else None
        if shot is not None:
            ats = set(_re.findall(r"@([一-龥A-Za-z0-9_]{1,8})",
                                  shot.get("visual") or ""))
            if len(ats) <= 1:
                continue          # 单人镜 ⇒ 这条不成立
        out.append(it)
    return out


def catalog_missing_episodes(text: str, episodes: int) -> list[int]:
    """全剧目录里**缺哪几集**（**纯函数**，可单测）。`episodes <= 1` 恒返回空。

    ★ 为什么必须有它（2026-10-05 实测 `madfate-abc-1005` 第 4 轮，52 分钟零出片）：
      我给 brief 写的「场」口径（一集 9–11 场、每场 4–12 秒）被**全剧级**的目录角色
      也照抄了，于是它给每集排一张场表 —— 文档胀到一次写不完，而它的契约同时明令
      "不要分多次 write_file 追加"：盘上只剩 **4 个集条目 / 6 卷 / 10075 字**（要 10 集），
      它就在自己的循环里重写了 45 分钟（图状态里那个子图落了 334 次，另两个角色各 12 与 18 次）。
      而 `post_validate` 原先只查"文件存在且非空" ⇒ 对它说"产物合格" ⇒ **没人告诉它缺了 6 集**。
      ⇒ 缺集必须当场点名到集号，角色才有可执行的修法；靠契约文字劝是劝不住的。

    重复标题（`### 第 1 集：…` 与 `### 第 1 集 节拍表` 并存）按**集号**去重，
    所以"多写一张节拍表"不会把缺的集补上。
    """
    n = int(episodes or 1)
    if n <= 1:
        return []
    got = {int(k) for k in (parse_catalog(text).get("episodes") or {})}
    return [i for i in range(1, n + 1) if i not in got]


def slice_threshold() -> int:
    """本次运行生效的切片阈值（见 `SLICE_THRESHOLD_ENV`）。"""
    import os
    raw = os.environ.get(SLICE_THRESHOLD_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return SLICE_THRESHOLD

#: 需要切片的产物**白名单**（角色名）。
#:
#: 为什么是白名单而不是"长了就切"：有些产物**天生就该整份读**（分镜表、资产表），
#: 切了会让下游读到半张表却以为读全了 —— 那正是「静默丢数据」。
#: 只有**按集组织**的目录型产物才可切。
SLICE_WHITELIST = ("plotdesigner",)

#: 切片失败时的行为：默认**响亮终止**（spec M0 B2 第 ④ 条）。
#: 设 `SHORTDRAMA_SLICE_SOFT=1` 可降级为"告警 + 注入全文"（应急用，会失去切片收益）。
SLICE_SOFT_ENV = "SHORTDRAMA_SLICE_SOFT"

#: 集号标题：`## 第1集：` / `### 第 1 集 xx` / `## 第１２集` 都认（全角数字先归一化）。
_EP_HEAD_RE = re.compile(r"^#{2,4}\s*第\s*([0-9]+)\s*集")
#: 卷（阶段）标题：`## 第 3 年（第 105–156 集）`。**不解析数字**（可能是汉字），
#: 只按出现顺序标记卷号 —— 少一次解析就少一处静默失败。
_VOL_HEAD_RE = re.compile(r"^#{2,4}\s*第\s*[^#\s]{1,6}\s*[年季部卷]")


def _norm_digits(s: str) -> str:
    """全角数字 → 半角（模型偶发写全角，不归一化就会"解析不出集"）。"""
    return s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def parse_catalog(text: str) -> dict:
    """把分集目录解析成「卷 + 集」结构（**纯函数，可单测**）。

    契约（由 `plotdesigner` 的 SKILL 保证，见 `packs/*/plotdesigner/SKILL.md`）：
        ## 第 3 年（第 105–156 集）      ← 卷标题，其后到第一个集标题之间是**卷摘要**
        ### 第 130 集：<标题>            ← 集条目
        <该集正文若干行>

    返回 `{"episodes": {集号: {"title","body","lines"}}, "volumes": [...], "why": str}`。
    解析不出任何集条目时 `episodes` 为空、`why` 给出原因（**调用方负责响亮处理**）。
    """
    lines = _norm_digits(text).splitlines()
    eps: dict[int, dict] = {}
    vols: list[dict] = []
    cur_ep: int | None = None
    cur_vol: dict | None = None

    def _close_ep(end: int) -> None:
        if cur_ep is not None and cur_ep in eps and eps[cur_ep].get("_open"):
            eps[cur_ep]["body"] = "\n".join(lines[eps[cur_ep]["_start"] + 1:end]).strip()
            eps[cur_ep]["lines"] = (eps[cur_ep]["_start"] + 1, end)
            eps[cur_ep].pop("_open", None)

    def _close_vol(end: int) -> None:
        if cur_vol is not None and cur_vol.get("_open"):
            # 卷摘要 = 卷标题之后、该卷第一个集标题之前的内容
            s = cur_vol["_start"] + 1
            e = cur_vol["_first_ep"] if cur_vol.get("_first_ep") is not None else end
            cur_vol["summary"] = "\n".join(lines[s:e]).strip()
            cur_vol["lines"] = (cur_vol["_start"] + 1, e)
            cur_vol.pop("_open", None)

    for i, l in enumerate(lines):
        m = _EP_HEAD_RE.match(l)
        if m:
            _close_ep(i)
            cur_ep = int(m.group(1))
            eps[cur_ep] = {"title": l.strip(), "_start": i, "_open": True}
            if cur_vol is not None and cur_vol.get("_first_ep") is None:
                cur_vol["_first_ep"] = i
            continue
        if _VOL_HEAD_RE.match(l):
            _close_ep(i)
            _close_vol(i)
            cur_vol = {"title": l.strip(), "_start": i, "_open": True}
            vols.append(cur_vol)
            continue
    _close_ep(len(lines))
    _close_vol(len(lines))

    why = ""
    if not eps:
        why = "目录里没解析出任何「## 第 N 集」条目（plotdesigner 的产物格式可能变了）"
    return {"episodes": eps, "volumes": vols, "why": why, "n_lines": len(lines)}


def slice_catalog(text: str, ep: int) -> dict:
    """从分集目录里切出「本集 + 前后各一集 + 本卷摘要」。

    返回 `{"ok", "text", "why", "diag"}`。**切片必须可见**（spec M0 B2 ③）：
    返回的文本开头就写明"这是切片、来自哪些行、目录共多少集"。
    """
    cat = parse_catalog(text)
    if cat["why"]:
        return {"ok": False, "text": "", "why": cat["why"], "diag": {}}
    eps, vols = cat["episodes"], cat["volumes"]
    if ep not in eps:
        have = sorted(eps)
        return {"ok": False, "text": "",
                "why": "目录里没有第 %d 集的条目（解析出 %d 集：%s..%s）"
                       % (ep, len(have), have[0], have[-1]),
                "diag": {"n_eps": len(have)}}

    # 本集属于哪一卷（最后一个起始行 ≤ 本集起始行的卷）
    vol = None
    for v in vols:
        if v["_start"] < eps[ep]["_start"]:
            vol = v
    parts = [
        "【分集目录·**本集切片**】以下内容**摘自** `%s`，**不是全文**："
        % (cat.get("name") or "plotdesigner/episodes.md"),
        "目录共 %d 行 / 解析出 %d 集 / %d 卷；本次注入第 %d 集及其前后各一集"
        % (cat["n_lines"], len(eps), len(vols), ep),
    ]
    if vol:
        s = vol.get("lines")
        parts.append("· **本卷**：%s（目录第 %s 行）" % (vol["title"], "%d-%d" % s if s else "?"))
        if vol.get("summary"):
            parts.append("  卷摘要：" + vol["summary"][:600])
    # 前情提要 = 前一集的条目；承接 = 后一集
    for label, e in (("前一集（**前情提要**）", ep - 1),
                     ("本集", ep),
                     ("后一集（**承接/伏笔**）", ep + 1)):
        if e not in eps:
            continue
        blk = eps[e]
        parts.append("· %s：%s" % (label, blk["title"]))
        if blk.get("body"):
            parts.append(blk["body"])
    return {"ok": True, "text": "\n".join(parts), "why": "",
            "diag": {"n_eps": len(eps), "n_vols": len(vols),
                     "lines": eps[ep].get("lines")}}


def _upstream_block(root: Path, role: str, ep: int) -> str:
    """注入**一段**上游产物：默认全文；全剧级目录超阈值时改为**按集切片**。

    ⚠️ 本函数是 M5 的**唯一落点** —— 单集项目（产物 < 阈值）走原路径，行为不变。
    """
    rel = out_path(role, ep)
    try:
        body = (root / rel).read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return "### %s\n（读不到 —— 若你依赖它，请用 read_file 确认路径）" % rel
    if not body:
        return "### %s\n（空文件）" % rel
    _thr = slice_threshold()
    if role not in SLICE_WHITELIST or len(body) <= _thr:
        return "### %s\n%s" % (rel, body)

    # —— 到了这里：全剧级产物且超阈值 ⇒ **必须**切片 ——
    # ★ 但**单集项目没得切**（2026-10-03 实测 `yoga-affair-1003e`：brief.episodes=1，
    #   plotdesigner 给这一集写了 9155 字的目录，里面**自然**没有「### 第 M 集」条目
    #   ⇒ 切片失败 ⇒ RuntimeError ⇒ 创作链 rc=3，8 分钟白跑、零出片）。
    #   切片存在的理由是**多集连载**省上下文（只注本集 ±1）；只有一集时整份目录
    #   就是本集，注入全文语义无损 —— 不该要求模型为了过切片器去编一套集编号结构。
    if _brief_episodes(root) <= 1:
        print("[slice] %s：%d 字符 > %d 阈值，但本项目 brief.episodes=1 ⇒ **不切片、注入全文**"
              "（单集没有「本集 ±1」可切；旧实现在这里硬终止，把整条链判死）"
              % (rel, len(body), _thr))
        return "### %s（**单集项目·不切片**）\n%s" % (rel, body)

    r = slice_catalog(body, ep)
    if r["ok"]:
        print("[slice] %s：%d 字符 > %d 阈值 → 按集切片（本集 ±1 + 本卷摘要，"
              "注入 %d 字符）"
              % (rel, len(body), _thr, len(r["text"])))
        return "### %s（**按集切片**）\n%s" % (rel, r["text"])
    # 切片失败：**响亮**，不静默给全文也不静默给空（spec M0 B2 ④）
    import os
    msg = ("[slice] ⚠️ %s 有 %d 字符（> %d 阈值）**必须切片**，但切不出来：%s"
           % (rel, len(body), _thr, r["why"]))
    # ★ 多集连载豁免（2026-09-26 实测，shiguan-series-0926 第 2 集被 RuntimeError 硬终止）：
    #   half-narrated 等包的 plotdesigner 契约是「只写本集」——全剧级产物 2..N 集又被
    #   跳过重生成（多集硬契约 2）→ 目录里**永远**没有 2..N 集条目，切片必然失败。
    #   这是包契约与切片契约的结构性冲突，模型再听话也做不出来；且本集剧情来源
    #   （brief 分集大纲）注入方本就有 → 降级为注入全文，语义无损。
    _episodes_total = _brief_episodes(root)
    if _episodes_total > 1 and r["why"].startswith("目录里没有第"):
        print(msg + "\n  → brief episodes=%d 但目录只有 %d 集条目（包契约=单集产物、"
              "后续集被跳过重生成）⇒ **自动降级为注入全文**（不再硬终止；"
              "本集剧情以 brief 分集大纲为准）。"
              % (_episodes_total, r["diag"].get("n_eps", "?")))
        return "### %s（**多集连载·切片不可行·注入全文**）\n%s" % (rel, body)
    if os.environ.get(SLICE_SOFT_ENV, "") == "1":
        print(msg + "\n  → %s=1 ⇒ 降级为**注入全文**（会失去切片收益，且上下文可能被挤爆）。"
              % SLICE_SOFT_ENV)
        return "### %s（**切片失败·已降级为全文**）\n%s" % (rel, body)
    print(msg + "\n  → 默认**终止**（切片契约要求「失败响亮终止」）。"
                "应急处置：设 %s=1 降级为注入全文后再跑。" % SLICE_SOFT_ENV)
    raise RuntimeError(
        "按集切片失败：%s（%s）。修法二选一：① 让 plotdesigner 按契约输出"
        "「## 第 N 卷」+「### 第 M 集」结构（见 packs/*/plotdesigner/SKILL.md）；"
        "② 设 %s=1 降级为注入全文。" % (rel, r["why"], SLICE_SOFT_ENV))


def resolve_ok(root: Path, role: str, ep: int) -> bool:
    """该角色的产物**此刻是否在盘且非空**（读走 `resolve_path`，含旧名回退）。

    为什么需要它：剧本直出模式下**预置产物要作为「只读上游」注入**，
    但它**在不在盘取决于预置有没有成功**（`mode.write_preseed` 可能因
    只读目录失败）⇒ 判据必须查盘，**不能**假设它在。
    """
    try:
        p = guards.resolve_path(root, role, ep)
        return p.exists() and p.stat().st_size > 0
    except Exception:  # noqa: BLE001 —— 查不到就当没有，不让注入这一步炸掉
        return False


def _craft_refs(root: Path) -> list:
    """本项目声明的叙事技法（**默认空 = 不注入**，见 `style.script_craft_of`）。"""
    try:
        from .media import style
        return style.script_craft_of(root)
    except Exception:  # noqa: BLE001
        return []


def _craft_roles_of(name: str, body: str) -> list:
    """一份技法声明它该注入给哪些角色（技法文件 frontmatter 的 `inject-to`）。

    ⚠️ **未声明 → 不注入给任何角色**（保守），并**打日志告警** ——
    否则"写了技法却谁都没收到"会静默发生。
    """
    m = re.search(r"^inject-to:\s*\[(.*?)\]", body or "", re.M)
    if not m:
        print("[craft] ⚠️ 技法 %r 没写 `inject-to` → 不注入给任何角色（请补上，如 `[director, plotdesigner]`）" % name)
        return []
    return [x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip()]


def _craft_block(root: Path, role: str) -> str:
    """叙事技法注入块：**按 brief/pack 的 `script-craft` opt-in，默认不注入**。

    2026-09-16 加：用户要求「**不要污染我的生产线**」——短剧那套判据（每集必有钩子 /
    密度递增 / 反派惨烈翻车）只该给**声明要它**的包，不能灌给微电影 / 音乐剧。
    """
    names = _craft_refs(root)
    if not names:
        return ""
    from .media import style
    parts, missing, skipped = [], [], []
    for n in names:
        body = style.read_craft(n)
        if not body:
            missing.append(n)
            continue
        if role not in _craft_roles_of(n, body):
            skipped.append(n)
            continue
        # 去 frontmatter 与维护性的引用块（那是给人看的，不进提示词）
        b = re.sub(r"\A---\n.*?\n---\n", "", body, flags=re.S)
        b = "\n".join(l for l in b.splitlines() if not l.startswith(">")).strip()
        parts.append("### %s\n%s" % (n, b))
    # ★ 两种"没生效"都必须**可见**（否则技法静默失效，排查无门）
    if missing:
        print("[craft] ⚠️ 声明了却读不到：%s（查 skills/packs/craft/<名>/SKILL.md）" % "、".join(missing))
    if not parts:
        return ""
    print("[craft] %s 注入 %d 份叙事技法：%s（%d 字）"
          % (role, len(parts), "、".join(x.split("### ")[-1].split("\n")[0] for x in parts),
             sum(len(p) for p in parts)))
    return ("\n\n【叙事技法·本片适用】以下是本片启用的**叙事判据**"
            "（由 brief / pack 的 `script-craft` 声明），开工前通读，按其设计剧情与分镜：\n\n"
            + "\n\n".join(parts))


def _shot_coverage_directive() -> str:
    """Use ordinary storyboard rows and the existing multi-row pack compiler."""
    mode = (config.VIDEO_MODE or config.VIDEO_MODE_DEFAULT).lower()
    if not config.SHOT_COVERAGE or mode != "pack" or config.VIDEO_PACK_MAX_GROUP < 2:
        return ""
    from .media.video_plan import PACK_MAX_SECONDS
    return (
        "【叙事镜头组织】一条视频请求可以包含多个镜头，不等于一镜到底。"
        "同一场连续戏按叙事需要写成多个分镜表行，每行一个镜头；"
        "相同「场次」编号和「场景」名称连续排列，交给现有pack管线合并。"
        "叙事场可以超过12秒，管线按台词和时长自动拆组；单镜请求合法，不按场数或固定镜数推导合格。"
        "每组总长4–%d秒，最多%d行；镜数、景别、机位、镜长由剧情与台词决定，不套固定三镜模板。"
        "每行用现有列写清主体的位置和朝向、一个主要动作、对方反应、结束时的人物与道具状态。"
        "下一镜从该结束状态接入；同侧拍摄，保持人物左右关系。"
        "镜内时间轴从0起到本行时长结束，全组切点由媒体层累加。"
        "不要将整组写成全程不切镜，连续运镜只限定在本行。"
        "本规则替代包内一次生成只含一镜及单镜至少4秒的旧口径，其它身份、道具、对白和总时长要求保留。"
        "全片首镜没有上一镜，起始人物与持物状态在画面描述写清即可，承接列可空；"
        "后续镜头（含下一组首镜）必须写上一镜末态到本镜起态。"
        "近景只对主体使用@身份锚点，对方的动作或手部位置仍用名字写清，"
        "不能为遵守@标记规则删掉接玉者或丢掉道具归属。"
        % (PACK_MAX_SECONDS, config.VIDEO_PACK_MAX_GROUP)
    )


def _container_facts(vp) -> str:
    """给 scenedesigner 的**出片容器事实**（数字全部从代码读，见函数内注释）。

    为什么写在这里而不是包 SKILL 里：这是系统事实（一次生成装得下多少、
    送几张图），不是类型包审美。四个包的 SKILL 已经证明过这类话会漂
    （"静帧取最后一拍"漂了 6 天）。
    """
    from .media import assets as _assets

    req = vp.PACK_MAX_SECONDS
    head = (
        "【出片容器事实·数字由管线代码给出，不是你该猜的东西】\n"
        "· 一条视频请求最长 %d 秒。" % req)
    if (config.VIDEO_MODE or config.VIDEO_MODE_DEFAULT).lower() == "pack":
        mg = config.VIDEO_PACK_MAX_GROUP
        body = (
            "最多 %d 个镜头并进同一条，且只有**同一场的相邻镜**会并进去"
            "（表里有「场次」列就按场次并组，没有才退回按「场景」列的地点名；"
            "跨场必切）。**镜长不设地板**——快切正反打写 1 秒、0.5 秒一镜都合法，"
            "秒数由这段戏的节拍定。\n"
            "· 一组声明秒数之和超过 %d 秒时，媒体层会把每镜秒数**等比压进 %d 秒**"
            "（台词镜不低于把台词读完的秒数、单镜压幅不超过 40%%，否则该镜不并组、"
            "独立成一条）。你写的 `0-2秒：` 节拍会跟着按比例重标，**拍数不丢**，"
            "但别指望声明的秒数原样落地。\n"
            "· 一条请求最多 %d 张参考图，槽位是**固定优先级**："
            "人物定妆照（最多 %d 张 ⇒ 三个人同镜时**第三个人没有定妆照**）→ "
            "场景空镜（按「场景」列取，**不分景别**）→ 上一组成片的**真实末帧** → "
            "道具图补剩下的空位（经常剩 0 张）。\n"
            "  ⛔ **静帧不进这条请求**（2026-10-07：画错的脸会被视频忠实继承；而「零静帧会不会让相邻两组长成两种地貌」这条旧依据，"
            "在同日的 pack 实跑里没有复现）。"
            % (mg, req, req,
               vp.REF_SLOTS, vp.PACK_REF_MAX_CHARS))
    else:
        if getattr(config, "VIDEO_REF_SOURCE", "sheets") == "sheets":
            body = (
                "当前视频档（`%s`）是**一个镜头一条请求**，一条最多 %d 秒。\n"
                "· 一条请求最多 %d 张图，全是**素材图**：人物定妆照（最多 %d 张）+ "
                "本镜「场景」列那张空镜 + 道具图。⛔ **静帧不进这条请求**。\n"
                "  封顶 %d 张是实测线不是审美：**图的张数超过本镜人数，模型就多画一个人**。\n"
                "· 镜头与镜头**互不知情**（每条独立生成）——跨镜一致只能靠文字与素材图。"
                % (config.VIDEO_MODE, req, vp.SHEET_SLOTS,
                   _assets.ref_caps()[0], vp.SHEET_SLOTS))
        else:
            body = (
                "当前视频档（`%s` + `VIDEO_REF_SOURCE=stills`）是**一个镜头一条请求**，"
                "一条最多 %d 秒。\n"
                "· 一条请求最多 %d 张参考图：本镜静帧 + 人物定妆照——"
                "镜里 1 个人物时封顶 %d 张、%d 个人物及以上封顶 %d 张（先满足人脸），"
                "剩下的位置给场景空镜（只有 全景/远景/大全景/空镜 才绑得到）与道具。\n"
                "· 镜头与镜头**互不知情**（每条独立生成）。"
                % (config.VIDEO_MODE, req, vp.REF_SLOTS,
                   *_assets.ref_caps(), _assets.ref_caps()[1]))
    return head + body + (
        "\n· ⇒ 所以：跨镜的长相、服装、道具位置、场景地貌**没有图兜底**，"
        "只能靠你把字写在每一镜上；多人镜要逐人写站位与朝向。\n"
        "· 节拍写法：`0-2秒：…；2-4秒：…` 从 0 起、首尾相接、终于本镜时长"
        "（不自洽 = 分镜契约门拦下），端点可用小数（`0-0.5秒：`）。"
        + ("**第一拍要写全身份锚点与身体朝向** —— 预览图与回退档只取第一拍，"
         "而它也最容易被当成整镜的代表画面。"
         if getattr(config, "VIDEO_REF_SOURCE", "sheets") == "sheets"
         else "**静帧取第一拍** ⇒ 身份锚点与身体朝向写在第一拍。"))


def role_input(role: str, root: Path, m: dict, reasons: list[str] | None = None) -> str:
    """喂给角色的**输入**。两种模式，见 `config.INLINE_UPSTREAM`：

      · **inline（默认，2026-09-13 起）** —— 上游产物**全文注入**。角色不必再
        read_file，一次调用就能"读完 + 想完 + 写产物"，把单步从 8–10 轮压到 2–3 轮。
      · **legacy**（`SHORTDRAMA_INLINE_UPSTREAM=0`）—— 只给**路径**，让它自己 read_file。
        旧理由「上游产物可达数十 KB，全量注入会挤掉创作空间」，在当前实际产物规模
        （~10 KB / 3–5K token）下不成立，而代价是 **10 倍轮次** —— 故改为默认 inline。

    注意：**只省 read，不省 write** —— 产物仍由角色 `write_file` 落盘
    （媒体链与守卫都以磁盘为准）。
    """
    ep = int(m.get("episode_index", 1) or 1)
    # ★ 剧本直出模式（2026-10-04）：依赖序**按项目 brief 取**，而不是用模块常量。
    # 默认模式返回值与 `guards.PREREQ` 逐字节相等 ⇒ 既有行为一字不变。
    _prereq = guards.prereq_for(root, ep)
    lines = [
        # 首行必须跟着模式变 —— 否则模式间文案自相矛盾（写成"用 read_file 读取"
        # 再附全文，模型仍会去 read，方案 B 的收益被抵消）。
        ("【开工前必读】下列文件的**全文已随本条消息给出**，不必再 read_file，直接开工："
         if config.INLINE_UPSTREAM else
         "【开工前必读】用 read_file 自己读取下列文件，不要凭空创作："),        "- /brief.json —— 本片需求（主题 / 四幕 must_have / 关键道具 / 禁忌 / 结局）",
    ]
    for r in _prereq.get(role, []):
        lines.append("- /%s —— 上游 %s 的产物" % (out_path(r, ep), r))
    # ★★ M3（2026-09-17）：**本集产物路径 = 运行期的唯一权威**。
    #
    # 为什么必须在这里给（而不是沿用 system prompt 里那条）：
    #   system prompt 在 `build_supervisor()` 求值 ⇒ **编译期固化** ⇒ 一个 dev
    #   server 只能服务一集（M3 的"一次起服跑 N 集"不可能实现），且换集必须重启。
    #   改成"每次开工注入"后：**同一次起服可以连续跑第 1..N 集**。
    #
    # 为什么放在**清单最前面**（而不是末尾）：模型对"开头几行"的注意力最高，
    # 而这一行写错 = 产物落到别的集上（静默覆盖），是本项目最贵的一类 bug。
    # 同时它显式声明「这是第 N 集」—— M4 要求的"集感知形状"（M5 直接复用）。
    lines.append(
        "\n【本集产物路径（★ 以这一行为准）】本项目当前跑的是**第 %d 集**。\n"
        "你必须用 write_file 把产物写到：%s\n"
        "（system prompt 里那条路径只给**形状**；确切的集号在这里。"
        "写到别的集的文件上会**静默覆盖**那一集，是严重事故。）"
        % (ep, "/" + out_path(role, ep)))
    lines.append("你的任务描述可能不完整，**一律以 brief.json 为准**。")
    # ★★ 2026-10-08：**全剧级角色要紧接着收到一句纠正**。
    #   上面那行「本项目当前跑的是**第 N 集**」说的是**本次运行的集号**，
    #   对全剧级产物（director / worldbuilder / assetdesigner / plotdesigner）不是产物范围。
    #   实测（`yuxuan-duanfeng-1007`，五集 brief）：plotdesigner 据此**只写了第 1 集
    #   的目录条目**（标题还自己加了「（本集）」），而后置体检每次都说"缺第 2-5 集" ⇒
    #   一条链跑了 67 分钟、目录一个字没动。错误的元信息比没有更糟（这是第二次栽在这）。
    from .guards import OUTPUTS as _OUT
    if "{N}" not in (_OUT.get(role) or out_path(role, ep)):
        lines.append(
            "⚠️ 纠正上一条：你的产物是**全剧级**的（一次锁定、全剧复用），"
            "路径里没有集号 —— ⛔ 不要加集号后缀，也**不要只写「本集」那一份**。")
        if role == "plotdesigner":
            try:
                from .guards import load_brief as _lb
                _n = int(((_lb(root) or {}).get("episodes")) or 1)
            except Exception:                              # noqa: BLE001
                _n = 1
            if _n > 1:
                lines.append(
                    "★ 本次 brief 是 **%d 集**：目录**集集要有条目**（每集一个 "
                    "`### 第 N 集：<标题>`，N 用阿拉伯数字），%d 集就写 %d 条 —— "
                    "缺哪集会被门当场退回并点名集号（实测：只写第 1 集 ⇒ 媒体链被拦、"
                    "整条链白跑）。" % (_n, _n, _n))
    if config.INLINE_UPSTREAM:
        lines.append("")
        # ★ M5：标题从"上游产物**全文**"改为"上游产物"——**全剧级长目录会按集切片**
        #   （见 `_upstream_block` / `slice_catalog`）。标题说"全文"而实际是切片，
        #   就是一处**自相矛盾**：模型会以为拿到了全部而漏掉其它集的关键信息。
        lines.append("【上游产物 —— 已读好，**不必再 read_file**，直接开工】"
                     "（标注了「按集切片」的那些是**目录摘录**，不是全文；"
                     "若你确实需要别集的内容，用 read_file 读原文件）")
        # ★ 注入的是**本集该看到的那一份** brief：按集写的字段（must_have / protagonist /
        #   结局…）在这里解析掉（`validate.brief_for_ep`）。旧行为（列表式 brief）逐字不变。
        lines.append(_brief_inline(root, ep))
        for r in _prereq.get(role, []):
            lines.append(_upstream_block(root, r, ep))
        if role == "reviewer" and (root / "assetdesigner" / "assets.contract.json").exists():
            lines.append("【磁盘事实】独立 /assetdesigner/assets.contract.json 已存在，"
                         "正文如下；不要从 assets.md 中的嵌入章节推断独立文件缺失。")
            lines.append(_inline_file(root, "assetdesigner/assets.contract.json"))
    # ★★ 剧本直出模式（2026-10-04）：两条**只在 from_script 下出现**的注入。
    #
    # 为什么必须在这里注入、而不是只写进包 SKILL：
    #   `_role_skill` 对**自带该角色 SKILL 的包**不回退（`3d-animation` 自带
    #   scriptwriter/dialogue）⇒ 只改一个包，别的包收不到。
    #   与本文件既有的「音频模式」「片长/镜数」「台词长度」三条硬指令**同一个理由、
    #   同一个位置**（那条教训见 `role_input` 台词长度注释里的事故记录）。
    #
    # ⚠️ 放在 `INLINE_UPSTREAM` **之外** —— 这两条不是"上游产物全文"，
    # 是**本模式的契约**，与是否全文注入上游无关。
    if role in {"scriptwriter", "scenedesigner", "reviewer"}:
        lines.append(
            "【动作因果与可见结果】普通递接按伸手接触、接收方握稳、交出方松手的顺序写，"
            "每一步明确持物者和所用手；只有剧情明确要求法术、抛接或掉落时才让道具离手。"
            "持物描述写清手与实物的接触方式（握住本体、托在掌心或提住既有把手），"
            "按道具实物结构选动作；手臂垂下不等于道具悬挂，不能暗示规格里没有的连接部件。"
            "相邻人物的接触动作必须与站位、身体朝向物理相容：先按人物自身左右判断，"
            "再检查镜头所见位置；并肩前行时自然牵手使用相邻内侧的空手，"
            "不能同时指定两只外侧手相牵而不说明可行姿势。切到背面后仍须检查这组关系，"
            "不要把画面左右当成人物左右。评审发现站位与手位矛盾时退回分镜，"
            "不要仅因每镜重复写了同一手位就判一致。"
            "行走、进出与追随用场景中的门槛、路径等可见地标写清起点和终点，"
            "严格对齐剧本与brief的结局：到门口、跨过门槛、走远是不同结果，不擅自升级。"
            "对白与动作必须表达同一行动：送出、收回、婉拒不能与实际交接及最终归属相反。"
            "有矛盾时由scriptwriter修正文台词，dialogue与scenedesigner不得靠改变动作偷偷圆台词。"
            "落幅必须在该镜动作中实际发生，不能仅在落幅列宣布完成。"
            "brief明确要求画面完成的关键动作也不能藏在两镜之间："
            "上一镜停在门外、下一镜起态已在门内，不等于拍出了进入；"
            "必须在可见动作段完成起点到终点的变化，评审逐一对照相邻镜末态与起态。"
            "写明从亭外跨进亭内等完整位移动作本身已包含过程，不要求额外逐脚中间姿势；"
            "只禁止用起态已完成替代关键动作，不凭空升级为更多微步骤。"
            "交接、接触或跨门等关键状态改变发生时，让其结果可见；"
            "之后的持续行走沿用已完成状态，不为验清道具另安排停步、展示或重复交接。"
            "普通持物允许自然遮挡，镜头服务剧情，不把验收过程写成额外动作；"
            "人物缩成小点的远景不能作为手部道具细节已验证的依据。"
            "同一持续动作的收尾若没有新事件、反应或叙事信息，合成一个连续镜头，"
            "不要只为重复走远或定格另起一镜；需要表达新的信息时仍正常切镜。"
            "保留镜头变化与剧情节奏，不逐只脚或逐根手指编排无关细节。"
            "评审据此核对具体动作矛盾，不另设固定镜数或逐步人工审批。"
        )
    _mode_name = chain_mode.mode_of(guards.load_brief(root))
    if _mode_name == "from_script":
        # ⚠️ ★ 2026-10-04 实测补上的**真缺口**：`prereq_for` 把 `scriptwriter`
        #   从「要派发的角色」里删掉了，于是**剧本不再出现在任何角色的上游清单里**。
        #   实测：分镜师的开工契约里 `/scriptwriter/scriptwriter_ep1.md` **不见了**
        #   —— 而它要靠剧本逐字照抄台词、按时间码排节拍。
        #   ⇒ **预置产物必须仍然作为「只读上游」注入**。
        #   为什么不能用 `PREREQ` 原样注：那会把 `plotdesigner` 也带回来，
        #   而它在本模式下**根本没有产物**（`_upstream_block` 只能返回「读不到」）。
        #   所以这里单独判：预置产物**在盘**才注入。
        for _r in chain_mode.PRESEEDED:
            if _r not in _prereq.get(role, []) and resolve_ok(root, _r, ep):
                lines.insert(1, "- /%s —— **剧本原文（系统预置，只读）**"
                                    "：本片剧情以此为唯一真相源，`%s` 不再被派发"
                             % (out_path(_r, ep), _r))
                lines.append(_upstream_block(root, _r, ep))
        if role == "worldbuilder":
            lines.append(chain_mode.EXTRACT_ONLY_HINT)
        if role == "scenedesigner":
            lines.append(chain_mode.BARE_INT_HINT)
    # 音频模式硬指令：brief 的 audio_mode 此前无人消费，导致「写 dialogue-led、
    # 交全片（无声）」反复发生。这里在**开工前**把要求写进输入，而不是事后才发现。
    mode = validate.audio_mode_of(guards.load_brief(root))
    if role in ("scriptwriter", "dialogue", "scenedesigner", "reviewer"):
        if mode == "dialogue-led":
            lines.append("【硬性要求·音频模式】brief.audio_mode = dialogue-led —— "
                         "本片**必须有台词**：dialogue 角色要产出具体台词清单；"
                         "scenedesigner 的「对白」列要逐镜填台词（无台词镜写「（无声，环境音）」），"
                         "**全片零台词 = 不合格**。")
        elif mode == "silent":
            lines.append("【硬性要求·音频模式】brief.audio_mode = silent —— "
                         "本片**不出台词**：对白列一律写「（无声，环境音）」，只保留音效。")
    # 台词**长度**硬指令（2026-09-15 实测事故：用户反馈"对白太短了，都不能表达剧情了"）。
    #
    # 为什么必须**注入**、而不是只改 shortdrama 的 scriptwriter SKILL：
    #   `_role_skill` 对**自带该角色 SKILL 的包**不回退（`3d-animation` 自带
    #   scriptwriter / dialogue SKILL）→ 只改一个包，别的包收不到。
    #   与上面「音频模式」「片长/镜数」「景别列写法」完全同一条理由、同一个位置。
    #
    # 事故数据：village-bridge（5 分钟 / 43 镜）→ 22 句台词**平均只有 7.0 字**、
    #   12 句 ≤6 字（「沉。」「开。」「推！」）。占比判据（台词镜 ≥20%）**达标**，
    #   所以门没拦；剧情却完全不靠台词。根因在契约层：
    #     · `scriptwriter` SKILL 原写「对白要**短促**有力，短剧不适合长篇独白」
    #       + 「1–3 分钟约 500–1500 字」的整片预算（5 分钟片按此写必然饿死台词）；
    #     · `dialogue` 是**逐字搬运器**（2026-09-14 修正：它改写台词曾导致
    #       "同一句两个版本"）→ **没有任何一环会把句子写长**。
    #   ⇒ 故三处齐改：契约（措辞与预算）+ 本注入（保证到达）+ 门（确定性判据）。
    #
    # ★ 2026-09-17：区间 10–28 → **10–40**，并**删掉「不得超过本镜秒数 × 5」**。
    #   用户原话：「现在的台词都很短了，差点就不知道表达什么意思了，
    #   **再限制对白发挥，会内容失真**」。
    #   机制上那条「秒数 × 5」对 scriptwriter **本来就是空指令** ——
    #   它写台词时**分镜还没排**，拿不到"本镜秒数"（dialogue 逐字搬运、
    #   scenedesigner 照抄，都不会回头改它）；真正会被它压住的是**分镜侧**：
    #   一旦镜头切到 4 秒，4×5 = **20 字上限** ⇒ **越切越短、内容被砍**。
    #   ⇒ 正确方向是**反向耦合**：**台词定信息量，镜头长度去装台词**
    #     （见下方 scenedesigner 注入的「镜长由内容决定，不是硬编码」）。
    if role in ("scriptwriter", "dialogue", "scenedesigner"):
        _dlg_tail = {
            "scriptwriter": "**台词写短了，全片就短了** —— 你是唯一能改台词的角色；"
                            "下游 dialogue 只逐字搬运、不会替你润色成句。",
            "dialogue": "**scriptwriter 才是唯一能改台词的角色**：若读到的台词明显过短，"
                        "在报告里**如实指出**，不要自行改写（你的职责是逐字搬运）。",
            "scenedesigner": "分镜「对白」列必须逐镜**照抄**剧本台词，"
                             "不得压缩、不得省略、不得只取前几个字。",
        }[role]
        lines.append(
            "【硬性要求·台词长度】台词必须**完整**、能独立承载剧情信息：每句净台词"
            "（不含「角色：」前缀与括注）**10–40 字**，**以「把这件事交代清楚」为准** ——"
            "**信息量优先于字数，不要为了简短而省略关键信息**；"
            "**禁止「沉。」「开。」「推！」这类 1–4 字残句**"
            "（要演「闷」就写简短但完整的句子，如「这块石头比我想的重多了。」）；"
            "**上限 40 字**（中文口播约 4–5 字/秒，一个镜头说不完更多）——"
            "**台词长了不要自己删：分镜师会把这一镜排长来装它**"
            "（4 秒镜约 16 字、6 秒约 24 字、10 秒约 40 字）。" + _dlg_tail)
    if role == "scriptwriter" and mode == "dialogue-led":
        target = validate.parse_target_seconds(guards.load_brief(root), ep=ep)
        if target:
            dialogue_budget = int(target * 4 * 0.75)
            lines.append("【整集时间预算】本集目标约%d秒，中文自然对白按每秒约4字估算。"
                         "整集净对白合计不超过约%d字（不含角色前缀与动作括注），给动作、停顿与回应留出时间。"
                         "说话、停顿与实际动作共同占用这段时间；先留出动作和回应的时间，再写对白。"
                         "不要通过重复解释或让下游无限拉长镜头来装超量台词。"
                         "保留清楚的意图与回应，删重复信息。人物台词表达人物意图，"
                         "不要把哪只手持物、道具不能复制等制作约束说成台词。" % (target, dialogue_budget))
    # 道具形制必须**逐字复制** brief（2026-09-22，当铺「单眼镜」事故定案）。
    #
    # 证据（dangpu-yuzhuo-0922）：brief.key_props 写「单眼镜：竹制边框单眼镜、镜片微黄、
    #   细绳挂耳后」+ protagonist 写「右眼戴竹制单眼镜（细绳挂耳后）」；assetdesigner
    #   产出的角色卡却改写成「**胸前垂挂**一支竹制…」——**佩戴方式被改写**。
    #   后果链：静帧收到矛盾指令（角色锚点说挂胸前 / 画面描述说戴右眼）→ 画出「胸前
    #   挂个圆筒、眼睛上没有镜」→ 静帧 QC 判 P0 触发重画 → **8 个镜白重画一轮仍未修好**
    #   （在错误的指令下重画）→ 达重画上限带伤放行，成片道具状态错误。
    # ⇒ 道具形制（材质/形状/颜色/**佩戴或放置方式**）以 brief 为**唯一真相源**，
    #   资产卡只做搬运，不做"合理化改写"。配套：reviewer 通用门新增第 13 条交叉校验。
    if role == "assetdesigner":
        lines.append(
            "【相连空间】同一建筑的外景、入口和内景共享材质、柱梁、门口结构及路径方向；"
            "这些固定特征在相关场景卡中一致写出，光色可随内外变化。"
            "不能只写同一地点的名字，却让独立参考图各自发明不同建筑。")
        lines.append(
            "【硬性要求·道具形制逐字复制】brief.json 的 `key_props` 里每件道具的形制描述"
            "（材质 / 形状 / 颜色 / **佩戴或放置方式**）必须**逐字复制**进资产卡条目——"
            "一字不改、不增删限定词、不省略佩戴方式。"
            "**道具的形制以 brief 为唯一真相源**：资产卡只做搬运工，不做改写与"
            "「合理化」（实测事故：brief 写「右眼戴、细绳挂耳后」被改写成「胸前垂挂」，"
            "导致静帧画成挂在胸前的圆筒、眼睛上没有镜，8 个镜白重画一轮）。")

    # 片长 / 镜头数硬指令（2026-09-14 实测事故）。
    #
    # 这条规则**原先只写在 shortdrama 包的 scenedesigner SKILL 里**
    # （`packs/shortdrama/scenedesigner/SKILL.md`：「镜头数 ≈ target_duration ÷ 7」），
    # 而 `_role_skill` 对**自带 scenedesigner SKILL 的包**（如 niulai-movie-style）
    # **不回退**到 shortdrama → **契约根本没到达分镜师**。
    #
    # 后果实测：village-scale（牛来包、目标 180 秒）交出 **21 镜 / 117 秒 = 65%**，
    # 被分镜契约门拦下整条媒体链（创作链 22 分钟白跑，好在媒体配额没白烧）；
    # 而 shortdrama 项目因 SKILL 里有这条，历次都在 96–103%（night-repair 112/120、
    # lost-and-found 173/180、paper-crane 264/330）。
    #
    # → **系统级规则不能靠 per-pack 的 SKILL 文本承载**，必须在开工前注入
    #   （与上面 audio_mode 完全同一条理由、同一个位置）。
    #
    # ★ 2026-09-17：除数 **7 → 4**（用户定档「第三档」），并**改成"基线"而非"硬指标"**。
    #   实测 mop-and-seat 三集（52 镜 / 378 秒）：**7.27 秒/镜**（商业短剧 1.5–4 秒）、
    #   台词镜内 **2.47 字/秒**（正常口播 4–5）、无声镜占 **32%** 时长，
    #   且**一个 4 秒镜都没有**（全表只有 6/8/10 三值）。
    #   根因：÷7 是**对现状的描述**（159 镜样本 7.1 秒/镜），进提示词后**自我实现** ——
    #   分镜师按 7 秒排片，产出继续"证明"7 秒是对的。
    #   ★ 同一条「长镜头」还牵出第二重慢：镜头 8 秒而台词只有 13 字
    #     → 音频由视频模型一并生成、**必须铺满整个 8 秒** → 语速被稀释到 1.6 字/秒。
    #     ⇒ **缩短镜头本身就修掉了「说话慢」**，故**不新增任何台词密度判据**
    #       （用户 2026-09-17 明确反对："再限制对白发挥，会内容失真"）。
    #   ★ 但镜长**不能写死成 20 个 4 秒** —— 用户要求：
    #     「4 秒一镜，**如果有长镜头需要 12 秒的话，让导演决定**；这些要跟剧本、分镜走」。
    #     ⇒ 改为**内容驱动**：默认 4 秒；**有台词的镜按「字数 ÷ 4」自适配**
    #       （台词定信息量，镜头去装它）；长镜需**写明理由**才允许 6–12 秒。
    #   ⚠️ 物理天花板（必须显式预告，否则排出被截断的镜数）：
    #     4 秒/镜 × `AGNES_VIDEO_MAX_SHOTS`（默认 20）⇒ 缺省**支持快切的最长片只有 80 秒**；
    #     更长的片必须退让镜长，故下方在 `fast > cap` 时单独告警。
    #   ⚠️ 同步点：`packs/shortdrama/scenedesigner/SKILL.md`、
    #     `packs/chinese-style-short-drama/scenedesigner/SKILL.md`、
    #     `packs/wool-felt-story-short/scenedesigner/SKILL.md`、`packs/craft/*`。
    #     平铺的 `packs/shortdrama/*.md` 与 `videospec/` **全是死文件**（改=空操作）。
    # ★ `SHORTDRAMA_LOOSE_STORYBOARD=1`（2026-10-02 A/B）：下面这一整段系统级
    #   「硬性要求 + 出片容器事实」**一律不注入**，节奏/镜长/景别/切镜密度交回
    #   类型包 SKILL 与分镜自己。留下来的只有"别把上游改坏"的搬运纪律
    #   （台词照抄、音频模式、产物路径、上游全文）——那些不是创作建议，
    #   放开它们两臂就不是同一部戏了，比不出东西。
    if role == "scenedesigner" and not config.LOOSE_STORYBOARD:
        # ★ 状态锚点每镜重复（2026-09-23，灯下棋验收定案）。
        #
        # 证据（dengxia-qi-0922 逐帧验收）：pack 打包按「同场景相邻 + ≤12s」切组，
        #   **组与组独立生成、互不知情**——柳娘的坐姿/站姿在组间反复跳变
        #   （LN06 站着撑伞在街上 → LN08 坐着 → LN11 又站着）、白玉佩在棋盘中央
        #   与挂在人物胸前之间漂移、油灯漂成蜡烛、棋篓漂成木盒。
        # 根因：这些**关键状态**只在部分镜的画面描述里出现——生成模型每段只看
        #   自己那几镜的文字，状态没写就自己编。
        # ⇒ 解法：**关键状态（人物姿态/位置、关键道具的位置与状态）必须每一镜的
        #   画面描述都重复**——哪怕啰嗦。状态变化时（起身/放下/拿起）**必须显式写出**
        #   变化动作，不能默认模型记得上一镜。
        lines.append(
            "【硬性要求·状态锚点每镜重复】以下关键状态必须**每一镜的画面描述都写明**，"
            "一组都不能漏：①人物**姿态与位置**（坐着对弈 / 站着 / 在棚下还是街上——"
            "对坐对话中人物必须始终写「坐在棋摊两侧」）；②关键道具（brief.key_props）的"
            "**位置与状态**（如「白玉佩压在棋盘中央缺一角系青绳」「铜座油灯在桌面一侧"
            "结着灯花」——逐字复用，不得只写道具名）；③人物**手里拿着什么 / 身上带着什么**"
            "（伞未收就一直写在手边）。状态发生变化时（起身 / 收伞 / 拿起 / 放下）"
            "**必须显式写出那个变化动作**，绝不许默认模型记得上一镜——"
            "**每一段视频是独立生成的，它不知道上一镜发生了什么。**")
        lines.append(
            "【空间动作】跨门、上下楼或进出同一空间时，用起点→终点说明走位（如门内→门外石阶），"
            "镜头左右只说明构图，不能替代出入方向。先交代人物转向目的地，再沿同一路径移动；"
            "机位在哪一侧、门槛前后与落幅所在侧必须一致。并肩牵手用两人相邻的空手，"
            "持物手不能同时牵手；下一镜延续这组手和所在侧。只写必要动作，不堆细碎口令。")
        tgt = validate.parse_target_seconds(guards.load_brief(root), ep=ep)
        if tgt:
            cap = int(getattr(config, "VIDEO_MAX_SHOTS", 0) or 0)
            # ★★ 2026-10-03 废弃「镜数 = 目标秒数 ÷ 4」这条除法基线（用户决定）。
            #   它原来同时出现在**这里**（每次派发都注入的硬指令）、`webchain.py` 的
            #   brief 写法指引、三个 craft 技法与四个包的分镜契约里 —— 只改包契约没用，
            #   真正把它写死的是这段代码。实测后果：brief 不写镜数时，130 秒必然变成
            #   30 镜 × 4 秒，成片"一段段硬分、割裂感重"，而节奏本该由叙事决定。
            #   现在代码只保留**两条真边界**：每镜 4–12 秒（供应商硬区间）与
            #   总时长落在 85%–130%（分镜契约门校验）；镜数与镜长归分镜师。
            coverage = _shot_coverage_directive()
            boundary = ("每场请求 **4–12 秒**，同组内镜头可短于4秒" if coverage else
                        "每镜 **4–12 秒**（供应商 API 的取值区间，越界会被静默改写）")
            scene_policy = coverage or (
                "· **★ 同一场景的连续戏优先合成一镜、用满 12 秒**：一次生成只包含一镜，"
                "把同一场戏里的多个动作与机位变化装进同一条 12 秒，人物/光色/道具的一致性"
                "和镜头效果都最好；⛔ 不要为了「多切镜」把一场戏拆成几条 4 秒短镜。"
            )
            lines.append(
                "【硬性要求·片长】brief.target_duration 的目标是 **%d 秒**："
                "**唯一的硬门是总时长** —— 分镜总时长必须落在目标的 **85%%–130%%** 内，"
                "低于 85%%（约 %d 秒以下）会被分镜契约门**直接拦下、媒体链不会启动**。"
                "**收工前必须自己把「时长(秒)」列加一遍**，确认总秒数达标；不够就补镜。"
                "★ **镜数与每镜秒数由你按剧情节拍决定，本系统不再给「目标秒数 ÷ 4」这类"
                "除法基线**（2026-10-03 废弃）。硬边界只有两条："
                "① %s；"
                "② 上面那条总时长。"
                "%s"
                "· **≥8 秒的镜必须写满镜内时间轴**（`0-3秒：…；3-6秒：…；6-9秒：…；"
                "9-12秒：…`，每段换一个事件：位移／物件易手／人物进出画／机位变化）——"
                "程序会按镜长检查这条，不合格会退回。"
                "· **有台词的镜**：秒数必须装得下台词 —— **≈ 台词字数 ÷ 4 秒**"
                "（中文口播约 4–5 字/秒：16 字用 4 秒、24 字用 6 秒、40 字用 10 秒）；"
                "**宁可把这一镜排长，也不要把台词砍短**。"
                "· **短镜照样合法**：一个反应、一个道具特写该 4 秒就 4 秒；"
                "**避免全表等长** —— 真实节拍有呼吸。"
                % (round(tgt), round(tgt * 0.85), boundary, scene_policy))
            # ⚠️ 配额上限：镜数超过它会被**静默截断**，必须提前说（按 4–12 秒的区间给出
            #   镜数范围，而不是替分镜师决定镜数）。
            if cap:
                lines.append(
                    "⚠️ 视频生成配额上限 `AGNES_VIDEO_MAX_SHOTS` = **%d 镜**：本片 %d 秒"
                    "按 4–12 秒排镜对应 **%d–%d 镜**。若你排的镜数超过 %d，媒体层会"
                    "**只渲前 %d 镜**（日志有警告但容易被忽略）⇒ 要么把镜排长，"
                    "要么让调用方调大该变量。"
                    % (cap, round(tgt), int(tgt // 12) + 1, int(tgt // 4) + 1, cap, cap))
        # ★ 【出片容器事实】（2026-10-02 新增）
        #
        # 为什么必须注入、且必须由代码给数：分镜师此前**不知道自己的表会被怎么装**。
        # 今天对账时逐条查证才发现，SKILL 里关于"一次生成能装多少、传几张图"的说法
        # 与代码已经各说各话（四个包的 scenedesigner 还在教"静帧取最后一拍"，
        # 代码 2026-09-26 起取第一拍 —— 见 `TestStillBeatClaimMatchesPipeline`）。
        # 「一条请求 ≤12 秒」「5 张参考图的槽位优先级」这类是**系统事实**，
        # 不是类型包的审美，按本文件既有的教训（per-pack 的 SKILL 承载系统级规则
        # 一定会漂）改由这里注入，且数字**从代码里读**，不许在文案里再抄一份。
        from .media import video_plan as _vp
        lines.append(_container_facts(_vp))
        if not tgt and _shot_coverage_directive():
            lines.append(_shot_coverage_directive())
        # 「景别」列会被 `qc.review_shot_type` **原样**送进构图校验提示词
        # （`景别：{shot_type}`）→ 括注会把判据冲淡。shortdrama 契约里本来就规定了
        # 词表（远景/全景/中景/近景/特写），但同样的"per-pack 才有的规则"问题
        # → 一并注入。
        lines.append(
            "【硬性要求·景别列写法】「景别」列**只写景别本身**"
            "（远景 / 全景 / 中景 / 近景 / 特写 之一），"
            "**不要括注主体或道具**——写「中景」而不是「中景（马德胜 + 老磅秤）」；"
            "该列会被原样送进构图校验，括注会让判据失焦。")
        # 多人镜必须**逐人写明站位**（2026-09-15 实测对照）。
        #
        # 证据（village-tractor 26 镜，同一部片内的天然对照）：
        #   · LN01/LN05 —— 3 人，画面描述写明了每人位置
        #     （「站车头正前右手拍引擎盖／扛锄头在车斗左／扛柴刀在车托右」）→ **画对**；
        #   · LN17/LN23 —— 3 人，只写「小林与阿凯在车托里」→ 静帧把主体
        #     **复制/重影**（LN17 实测 **9 张脸同框**）或**纵向分屏**，重画 3 轮无效。
        # ⇒ 这是**输入的可拍性**问题，不是提示词指令能补救的：人数指令已按人数分流、
        #   反分屏正向声明（NOSPLIT）也已在场，仍然失败。只能从契约侧要求把位置写清楚。
        lines.append(
            "【硬性要求·多人镜的站位】画面里出现**两个及以上人物**时，"
            "必须在「画面描述」里**为每个人物写明他具体在画面中的位置与朝向**"
            "（如「甲站在车头正前，右手拍引擎盖；乙在车斗左侧扛着锄头；丙在车斗右侧」）——"
            "**不要只写「甲乙都在车斗里」这类笼统说法**。"
            "静帧是单幅画面，笼统的多人描述会让它把人物**复制成多份、或把画面切成多格**"
            "（实测对照：写清站位的镜全部画对；只写「都在某处」的镜出现 9 张脸同框与纵向分屏）。")
        # 身份锚点必须**每镜重复**（2026-09-22 捕梦师二次深审定案）。
        #
        # 证据（bumengshi-0922 41 镜逐帧深审）：角色大衣颜色在锚点断链的镜**漂红**、
        # 镜内凭空**戴帽又摘帽**（LN03/LN39）、同一怪物在相邻镜**两个造型**
        # （骷髅怪 vs 银色机甲怪，LN31-33 静帧 QC 重画后跨镜分叉）、室内外
        # 中段**横跳**（写清室内外的镜全对，没写的镜模型自己换场地）。
        # ⇒ 模型**没有跨镜记忆**，锚点只写一次（或写「同上」）时，缺失的镜
        #   就是自由发挥的开口。打包渲染的组内一致靠参考图，**跨组/跨请求
        #   只能靠文字逐镜在场**。
        lines.append(
            "【硬性要求·身份锚点每镜重复】角色的**固定穿戴**（帽饰/外套颜色/随身道具）"
            "与场景的**室内外 + 昼夜**，必须写进**每一镜**的「画面描述」——"
            "**不得写「同上」、不得只在角色首次出场那镜写**。"
            "视频与生图模型没有跨镜记忆，锚点缺失的镜就是模型自由发挥的开口"
            "（实测：同一角色大衣颜色漂红、镜内凭空戴帽、同一怪物前后两个造型、"
            "室内外横跳）。每镜都写，逐镜不变，直到剧情明确要求换装/换景。")
        # 剪辑密度两条（2026-09-22，对标官方出片拍板）：正反打 + 开场钩子。
        #
        # 证据：官方同款模型示例（12s 打包 = 4s+3s+5s 三拍）在一次生成内由模型
        # 自主切换三次机位（俯拍全景→平移中景→对坐正反打）且微表情全中提示词
        # ——同事件多角度**模型做得到**，前提是分镜把镜拆出来。我方 14 镜/65s
        # （4.6s/镜、一镜一事）的成片被用户判定「剪辑密度不够、电影感弱」。
        # 开场钩子：官方开场第一句就是台词钩子；我方惯例 5s 空镜定场，浪费黄金
        # 3 秒。打包时长下限 2s/镜（pack_clamp_sec，2026-09-25 放权），反应镜写 4s 即合规。
        lines.append(
            "【硬性要求·关键拍点多角度】情绪转折、冲突爆发、情感峰值这些**关键拍点**，"
            "禁止用一个镜头拍完——必须拆成 2-3 镜从不同角度拍："
            "①说话/动作的一方给中近景；②紧接一镜切**另一方的反应**"
            "（中近景，可以无台词，表情就是内容，时长写 4 秒）；"
            "③需要时再加一镜双人关系镜头。"
            "相邻两镜的**景别或角度必须不同**，禁止同景别连切。")
        lines.append(
            "【硬性要求·开场即钩子】第 1 镜**禁止纯空镜定场**："
            "要么直接从人物动作/冲突切入，要么空镜不超过 2 秒且必须叠画外音台词。"
            "第一句台词必须出现在**前 6 秒**内，且必须是钩子句（反常宣言/悬念/冲突）。")
    # 叙事技法（**默认不注入**；按 brief/pack 的 `script-craft` opt-in）——
    # 见 `_craft_block`：这是"不污染生产线"的那道开关（短剧判据只给声明要它的包）。
    _cb = _craft_block(root, role)
    if _cb:
        lines.append(_cb)
    lines.append("产物用 write_file 写到：/%s" % out_path(role, ep))
    # ★★ 分镜**重跑**时给"退回清单"，不给"自己检查一遍"（2026-09-29）。
    #   事故：第 2 集分镜角色被要求满足十几条可数律，它就在**逐镜自查自改**上
    #   连跑 2 小时，撞满链预算（9000s）后 rc=0 静默收工，审稿根本没轮到。
    #   ⇒ 检查交回程序（`v5/shotcheck.py`）：
    #     · 有不合格 → 只列那几镜 + 逐字原文，并明令"没列出的不要动、改完就停"；
    #     · 全部合格 → 明令**不要重写**，直接结束本轮（这才是省下 2 小时的那一支）。
    if (role == "scenedesigner" and config.SHOTCHECK != "off"
            and not config.FAST and not config.LOOSE_STORYBOARD):
        try:
            from . import shotcheck
            # ★ 参数装配只有一份（`storyboard_check_args`）：派发时的退回清单与
            #   反空转闸的"已收敛"判据必须跑**同一组**判据，各自拼参数的话，
            #   新加一条判据就会只接进一处 —— 1008 那天「判据接错位置」的同型病。
            _kw = storyboard_check_args(root, ep)
            _had_table = bool(_kw)          # 表在不在盘上（**与"解析出几镜"无关**：
                                            #   解析出 0 镜的坏表也算"在"，下面那条
                                            #   "改哪些"的指令照样要给 —— 旧判据是
                                            #   `_sb.exists()`，换成 `_shots` 会把它弄丢）
            _shots = _kw.pop("shots", [])
            _md = _kw.pop("markdown", "")
            _pl: list[str] = []
            if _had_table:
                _pl = drop_solo_opponent_items(
                    shotcheck.punch_list(_shots, markdown=_md,
                                         use_judge=(config.SHOTCHECK == "full"),
                                         log=lambda *a: None, **_kw), _shots)
                if _pl:
                    # 钉在盘上：正规"打回重做"会把旧表移进 `.rerun_backup/`，
                    # 下一轮就没有表可读，只有这个文件还在。
                    shotcheck.save_punch(root, ep, _pl)
                else:
                    shotcheck.clear_punch(root, ep)
                    lines.append(
                        "\n【✅ 盘上第 %d 集分镜表**已通过程序体检**（可数判据 + 语义判据）】\n"
                        "体检通过只说明这些判据合格，不代表实际视频已验收。"
                        "没有新的创作需求或实拍问题时，**不要重写、不要逐镜自查、不要「优化」措辞**，"
                        "回一句「体检已合格，未改动」即可结束。"
                        "任务明确指出实拍问题时，只修对应内容、保留其余镜头，再交独立评审；"
                        "不能用体检通过拒绝这类修订。"
                        % ep)
            else:
                _pl = shotcheck.load_punch(root, ep)
                if _pl:
                    lines.append(
                        "\n【⚠️ 上一版分镜表被打回，以下是**程序体检**对它查出的 %d 处不合格"
                        " —— 新表必须避开这些，其余按 brief 正常创作】\n"
                        "检查由程序做，你**不必逐镜自查**。这些是上一版真实踩中的坑，"
                        "同型问题不要再写出来。" % len(_pl))
                    lines.extend("- " + x for x in _pl[:24])
            if _pl and _had_table:
                # ★★ 2026-10-03 实测事故（`yoga-affair-1003b`，26 镜）：原来这里写的是
                #   「只改列出的那几镜、没列出的一律不要动」。模型**照做了**——于是它用
                #   `edit_file` 逐镜外科式改、每改一镜再 `read_file` 整表确认。从 checkpointer
                #   取出的终态序列实测：**一轮 111 次工具调用**（read_file 49、edit_file 54、
                #   write_file 8，全打在同一个 `scenedesigner_ep1.md` 上）。
                #   每次调用在 LangGraph 里算两步（model + tool）⇒ ≈222 步 > 角色的
                #   `ROLE_RECURSION_LIMIT`(150) ⇒ `GraphRecursionError` ⇒ 父 run `status=error`
                #   ⇒ drive_chain rc=3、**整条链零出片**（45 分钟白跑）。
                #   ⇒ 09-29 那条判据防的是"逐镜自查"这件事**由模型来做**，但它没规定**怎么落盘**，
                #     结果把成本从 token 换成了步数，换了一个更致命的死法。
                #     现在把落盘方式也定死：**一次 write_file 交整表**，改完即停。
                lines.append(
                    "\n【⚠️ 盘上已有第 %d 集分镜表，程序体检发现 %d 处不合格】\n"
                    "**把改好的整张表用一次 `write_file` 交出去**，然后直接结束本轮。\n"
                    "⛔ 不要 `edit_file` 逐镜改、不要反复 `read_file` 读回自己刚写的表自查 —— "
                    "体检由程序做，改完它会再跑一遍确认，你**没有**通过体检会被告知。"
                    "本角色的步数上限是按「一次读完 + 一次写完」设计的，逐镜改会把整条链撞死"
                    "（实测 111 次工具调用 ⇒ 递归上限 ⇒ 零出片）。\n"
                    "改的**范围**仍然只限下面列出的这些条目，没列出的镜头内容原样保留。"
                    % (ep, len(_pl)))
                if any("片长" in x or "实际请求合计" in x for x in _pl):
                    # 时长不足时防止返工继续压缩；时长超标时必须允许缩短。
                    # 不能把一次欠长事故的“只许加”指令套到所有片长问题。
                    current_seconds = sum(float(s.get("seconds") or 0) for s in _shots)
                    target_seconds = float(_kw.get("target_seconds") or 0)
                    if target_seconds and current_seconds > target_seconds * 1.30:
                        lines.append("★ 当前分镜过长：允许缩短过长镜、合并或删除重复动作，"
                                     "使总时长回到目标区间。对白不得由分镜师擅自删改；"
                                     "如果对白本身装不下，明确交回scriptwriter压缩重复信息，"
                                     "不要虚报很短的镜长，也不要继续加长全片。")
                    elif target_seconds and current_seconds < target_seconds * 0.85:
                        lines.append(
                            "★ 涉及**片长**时，上面那句「原样保留」这样理解：**只许加、不许减** ——"
                            "可以补镜、可以把镜写长；⛔ 不许删镜、不许把几镜并成一镜、"
                            "不许把某镜的秒数改小。**新表总时长必须 ≥ 现在这一版**"
                            "（当前时长不足，应补足可见剧情而不是删短）。")
                    else:
                        lines.append("★ 声明时长已在目标区间：按实际pack分组时长调整，"
                                     "避免合组压缩使成片不足，不盲目增加或缩短全片。")
                lines.extend("- " + x for x in _pl[:24])
        except Exception as e:  # noqa: BLE001 -- 体检失败不能伪装成"合格"
            lines.append("\n【⚠️ 分镜体检未能执行（%s）—— 本轮请自行逐镜核对】"
                         % str(e)[:90])
    if reasons:
        lines.append("")
        lines.append("【本次是评审打回后的重跑，必须修正以下问题】")
        for x in reasons:
            lines.append("- " + str(x))
    # ★★ 人工入站（2026-10-06，工作室三栏界面 `frontend_new` 的入站半边）。
    #
    # 为什么必须注入在**这里**（而不是只写进包 SKILL、也不是只走 HITL）：
    #   · `hitl.decide` 要求链**正挂在步级门上**，而 `manual_steps` 默认关
    #     （`webchain.py:116`）⇒ 链一路跑到底时人说的话**无处可去**；
    #   · `webwrite` 改分镜表**不碰链的失效机制**（phases 不动、`reviewer.passed`
    #     照样绿）⇒ 人在画布上改完，导演下一轮读到的仍是"表没被改过"。
    #   本函数是**每次派发都经过**的唯一注入点，两条缺口一起补上。
    #
    # ⚠️ 放在**最末尾**：这是本轮最新的信息（人刚说的话 / 刚改的格子），
    # 而模型对结尾的注意力同样高；且它必须在 `reasons`（评审打回清单）之后 ——
    # 打回清单是上一轮的程序结论，人的改动是**这一轮**的新事实，后者覆盖前者。
    #
    # 无条目时 `render_block` 返回空串 ⇒ **一行都不加**，既有链路行为零变化。
    if role == "reviewer" and not config.LOOSE_STORYBOARD and _shot_coverage_directive():
        lines.append(_shot_coverage_directive())
        check_args = storyboard_check_args(root, ep)
        if check_args:
            from . import shotcheck
            findings = shotcheck.punch_list(**check_args)
            lines.append("【本轮程序体检】下面是当前实际分组与可数判据结果，"
                         "不要照抄分镜师的自报统计。已有违规应列入对应角色退回清单：\n"
                         + ("\n".join(findings) if findings else "可数判据无违规。"))
    _ib = inbox.render_block(root, role, ep)
    if _ib:
        lines.append(_ib)
    return "\n".join(lines)


# ─── 分镜音频模式守卫（2026-09-12 从 graph.py 迁入：媒体链的 storyboard gate 依赖它）──


def _audio_mode_defect(root: Path, ep: int | None = None) -> str:
    """分镜是否违反 brief 的音频模式（返回问题描述，合规则空串）。

    只做**确定性**判定，不看模型评审意见：
      · dialogue-led 但台词镜占比 < DIALOGUE_MIN_RATIO → 回退
      · silent 但有台词镜 → 回退（反向漏检同样会让成片跑偏）

    ⚠️ `ep` 要传（2026-10-08 补）：不传时 `resolve_path` 取 manifest 的
      `episode_index`，连载时那一格会随渲染推进变化 ⇒ 判的是**别一集**的表。
    """
    sb = guards.resolve_path(root, "scenedesigner", ep)   # M1：集级路径，读走兼容解析
    if not sb.exists():
        return ""
    try:
        md = sb.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""
    brief = guards.load_brief(root)
    r = validate.check_storyboard(md, None)      # 不传 brief：只取 schema/对白统计
    mode = validate.audio_mode_of(brief)
    if not r.get("scene_count"):
        return ""
    if mode == "dialogue-led":
        if r.get("dialogue_short"):
            return ("音频模式违规：brief.audio_mode=dialogue-led，但分镜仅 %d/%d 镜有台词"
                    "（下限 %d%%）——必须补足台词后重跑"
                    % (r.get("spoken_shots", 0), r.get("n_shots", 0),
                       int(validate.DIALOGUE_MIN_RATIO * 100)))
    elif mode == "silent" and r.get("spoken_shots"):
        return ("音频模式违规：brief.audio_mode=silent，但分镜有 %d 镜带台词——"
                "对白列应统一写「（无声，环境音）」" % r["spoken_shots"])
    return ""


#: 「全片没有台词」该打回谁 —— **只有 scriptwriter 能创造台词**。
#: `dialogue` 是逐字搬运器、`scenedesigner` 抄表，实测两者都明确拒绝替上游补写：
#: 1008 `yuxuan-duanfeng-1007` ep2 的 `dialogue_ep2.md` 整篇写的就是
#: 「这是上游与 brief 的矛盾，须回到 scriptwriter 解决（我无权在提取层修台词）」。
#: ⚠️ 为什么不「先去数上游有没有台词」（那样能少跑两个角色）：scriptwriter 是散文体，
#: `名字：` 形式的说明行与真台词在文本层分不开 —— 实测 86 个 (项目,集) 里，
#: `audio_mode=silent` 的项目照样数得出 33–40 行「像台词」的行。判宽了会把责任错派给
#: `scenedesigner`，reroll 交回一张同样空的对白列 = 整轮白烧。取最上游多跑两个角色，
#: 但一定会真的修。
_DIALOGUE_ORIGIN = "scriptwriter"


def deterministic_storyboard_defects(root: Path, ep: int | None = None) -> list[str]:
    """**程序数得出来**的分镜契约违规（评审判决必须带上，判据只留这一份）。

    目前只有音频模式一条。加新条目时的两条要求：
      · 纯确定性（不送模型、不读模型意见）；
      · 它在**独演镜 / 收势镜 / 空镜**上也成立 —— 否则会把合格的分镜钉在门外
        （1008 实测：按「对手类」判据判无对手的独演镜，一条链空转 1 小时 42 分零出片）。
    """
    d = _audio_mode_defect(root, ep)
    return [d] if d else []


def enforce_deterministic_verdict(dec: dict, root: Path,
                                  ep: int | None = None) -> dict:
    """把 `deterministic_storyboard_defects` 并进评审判决。无违规 ⇒ **原对象返回**（零副作用）。

    ## 为什么必须并到**判决**上，而不是只在渲染时拦（2026-10-08 实测 ep2）

    `brief.audio_mode=dialogue-led`、分镜 **0/8 镜有台词**。程序判据
    `validate.check_storyboard` 早就算出 `dialogue_short=True`，但它只接在
    `series.storyboard_gate`（**渲染时**）上。评审侧读的是 reviewer 自己的判定块，
    而 reviewer 把「audio_mode 与禁忌冲突」记进 **advisory** 后判了 `pass: true`
    ⇒ 驱动器按"链已完成"收工、界面看着成功，直到媒体链开跑才被门拦下 ——
    等待一整段渲染时间，然后零出片。

    ⇒ 一条判据要么在创作链里就生效（打回重做），要么就得承认它只会在最后炸。
      这里选前者：**能数的东西不由模型的 `pass` 定生死**。
      （与 `shotcheck.filter_contradicted_blocks` 是同一枚硬币的两面：那里驳回
        「模型条目与盘上事实矛盾」，这里补上「模型漏判的程序条目」。）

    ⚠️ 必须一起把 `pass` 翻下来：`decision.normalize_pass` 见 `pass: true` **直接返回
      True**、压根不看 `reasons` —— 只往 reasons 里加一条是无效的（旧文档那句
      「先跑确定性检查写进 reasons，本函数就不会误放行」只在 `pass` 为假时成立）。
    """
    if not isinstance(dec, dict) or not dec:
        return dec
    defects = deterministic_storyboard_defects(root, ep)
    if not defects:
        return dec
    out = dict(dec)
    reasons = list(out.get("reasons") or [])
    out["reasons"] = reasons + [d for d in defects if d not in reasons]
    # 责任角色：rerun 与 owners 都补，`resolve_target` 取两者里**更上游**的那个，
    # 所以评审若已点名 plotdesigner（更上游），照旧听它的。
    for key in ("rerun", "owners"):
        lst = list(out.get(key) or [])
        if _DIALOGUE_ORIGIN not in lst:
            lst.append(_DIALOGUE_ORIGIN)
        out[key] = lst
    if out.get("pass"):
        out["pass"] = False
        print("[verdict] !! 评审判 pass: true，但**程序数出来的分镜契约违规**覆盖了它：%s"
              % "；".join(str(x)[:90] for x in defects), flush=True)
        print("           → 按打回 `%s` 处理（它是唯一能创造台词的角色）及其下游。"
              % _DIALOGUE_ORIGIN, flush=True)
        print("           ⚠️ 若 brief 自身矛盾（`audio_mode` 与 `禁忌` 对冲），重做也会"
              "原样交回空对白列 —— 先修 brief.json 再重跑。", flush=True)
    return out


# ─── 分镜判据的参数装配（退回清单与反空转闸**共用**，2026-10-08）──────────────


def storyboard_check_args(root: Path, ep: int | None = None) -> dict:
    """本集分镜表要跑的那组判据参数。**表不在 / 读不动 ⇒ 返回 {}**。

    为什么单独抽出来：派发时的退回清单（`role_input`）与反空转闸的"这张表已经
    改合格了没有"必须用**同一组**参数。两处各自拼参数，加一条判据就会只接进一处
    —— 1008 那天「台词判据只接在渲染门上」就是同一个病的另一种形态。
    """
    if ep is None:
        ep = int(guards.load_manifest(root).get("episode_index", 1) or 1)
    sb = root / out_path("scenedesigner", ep)
    if not sb.exists():
        return {}
    try:
        md = sb.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return {}
    try:
        brief = guards.load_brief(root)
    except Exception:  # noqa: BLE001 -- 片长这条可缺，不该挡住其余判据
        brief = {}
    try:
        tgt = int(validate.parse_target_seconds(brief.get("target_duration"), ep=ep) or 0)
    except Exception:  # noqa: BLE001
        tgt = 0
    from . import shotcheck
    from .media import storyboard as _sbd
    from .media import style as _style
    parsed = _sbd.parse(md)
    _locked = validate.camera_reqs(brief)
    return {
        "shots": parsed,
        "combat": any(_style.has_combat_action(s) for s in parsed),
        "auto_groups": bool(_shot_coverage_directive()),
        "markdown": md,
        "target_seconds": tgt,
        "chars": shotcheck.character_names(root),
        # ★ brief 明写"共 15-18 镜"时按**区间**判，不只用"目标秒÷8"的派生下限
        #   （2026-09-29 实测：一条链交 12 镜 / 54 秒仍"镜数合格"= 漏检）
        "target_shots": validate.parse_shot_range(brief.get("target_duration")),
        "audio_mode": validate.audio_mode_of(brief),
        # ★ 摄影/光学与"锁定机位／方向去重"几条只在**声明了才判**
        #   （反质量包要的是僵硬锁定机位，判了就是误报）
        "camera_light": ("camera-light-physics" in _craft_refs(root)),
        # ★ 10b「非宽景只能 @ 一个角色」是**包内**律，谁写进自己的分镜契约才对谁判
        "single_at_law": shotcheck.pack_requires_single_at(root),
        "min_scenes": validate.min_scenes_per_episode(brief),
        "max_locked": _locked[0],
        "min_dirs": _locked[1],
    }


def storyboard_is_compliant(root: Path, ep: int | None = None) -> bool | None:
    """盘上本集分镜表是否**已通过可数判据**。`None` = 表不在/读不动（不据此判）。

    只跑**可数**那一层（零额度、每 20 秒轮询也跑得动）；语义判据（要送模型的那层）
    仍归评审与 `punch_list` 管。
    """
    kw = storyboard_check_args(root, ep)
    shots = kw.pop("shots", [])
    kw.pop("markdown", "")
    if not shots:
        return None
    from . import shotcheck
    try:
        return not shotcheck.countable(shots, **kw)
    except Exception:  # noqa: BLE001 -- 判据自己坏了不该把链判死，按"不知道"处理
        return None
