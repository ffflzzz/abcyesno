# -*- coding: utf-8 -*-
"""角色装配层（2026-09-12 从 graph.py 抽出）。

为什么独立：supervisor 架构（orchestrator.py）只依赖这一层——FS_TOOLS /
role_system_prompt / role_input；graph.py 的其余部分（StudioState + 节点工厂 +
build + _route_after_review）属**静态链 DAG**。抽出后静态链成为孤岛，
可独立废弃而不影响 supervisor 与媒体链。
"""
from __future__ import annotations

from pathlib import Path
import re

from . import config, guards, validate
from .guards import PREREQ, out_path


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
只输出文本不算完成。"""


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
    return (_role_skill(pack, role) or ("你是 " + role + "。")) + TOOL_NOTE + (
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
    lines = [
        # 首行必须跟着模式变 —— 否则模式间文案自相矛盾（写成"用 read_file 读取"
        # 再附全文，模型仍会去 read，方案 B 的收益被抵消）。
        ("【开工前必读】下列文件的**全文已随本条消息给出**，不必再 read_file，直接开工："
         if config.INLINE_UPSTREAM else
         "【开工前必读】用 read_file 自己读取下列文件，不要凭空创作："),        "- /brief.json —— 本片需求（主题 / 四幕 must_have / 关键道具 / 禁忌 / 结局）",
    ]
    for r in PREREQ.get(role, []):
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
    if config.INLINE_UPSTREAM:
        lines.append("")
        # ★ M5：标题从"上游产物**全文**"改为"上游产物"——**全剧级长目录会按集切片**
        #   （见 `_upstream_block` / `slice_catalog`）。标题说"全文"而实际是切片，
        #   就是一处**自相矛盾**：模型会以为拿到了全部而漏掉其它集的关键信息。
        lines.append("【上游产物 —— 已读好，**不必再 read_file**，直接开工】"
                     "（标注了「按集切片」的那些是**目录摘录**，不是全文；"
                     "若你确实需要别集的内容，用 read_file 读原文件）")
        lines.append(_inline_file(root, "brief.json"))
        for r in PREREQ.get(role, []):
            lines.append(_upstream_block(root, r, ep))
    # 音频模式硬指令：brief 的 audio_mode 此前无人消费，导致「写 dialogue-led、
    # 交全片（无声）」反复发生。这里在**开工前**把要求写进输入，而不是事后才发现。
    mode = validate.audio_mode_of(guards.load_brief(root))
    if role in ("dialogue", "scenedesigner"):
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
    #     4 秒/镜 × `AGNES_VIDEO_MAX_SHOTS`（现 60）⇒ **支持快切的最长片只有 240 秒**；
    #     更长的片必须退让镜长，故下方在 `fast > cap` 时单独告警。
    #   ⚠️ 同步点：`packs/shortdrama/scenedesigner/SKILL.md`、
    #     `packs/chinese-style-short-drama/scenedesigner/SKILL.md`、
    #     `packs/wool-felt-story-short/scenedesigner/SKILL.md`、`packs/craft/*`。
    #     平铺的 `packs/shortdrama/*.md` 与 `videospec/` **全是死文件**（改=空操作）。
    if role == "scenedesigner":
        tgt = validate.parse_target_seconds(guards.load_brief(root))
        if tgt:
            cap = int(getattr(config, "VIDEO_MAX_SHOTS", 0) or 0)
            fast = tgt / 4.0                       # 「4 秒一镜」的快切基线
            lines.append(
                "【硬性要求·片长与镜头数】brief.target_duration 的目标是 **%d 秒**："
                "**镜数基线 ≈ 目标秒数 ÷ 4（约 %d 镜）—— 基线是「4 秒一镜」、快切优先。**"
                "★ **镜数不是硬指标，唯一的硬门是「总时长」**：分镜总时长必须落在目标的"
                " **85%%–130%%** 内 —— 低于 85%%（约 %d 秒以下）会被分镜契约门"
                "**直接拦下、媒体链不会启动**。**收工前必须自己把「时长(秒)」列加一遍**，"
                "确认总秒数达标；不够就补镜，不要交。"
                "**镜长由内容决定，不是硬编码**："
                "⚠️ **节奏以本条为准**：上游「视觉风格指南」若出现「避免快切 / 跳剪」"
                "一类措辞，那说的是**剪辑手法**，**不改变镜头长度** —— "
                "本片镜头仍按 4 秒基线排。"
                "· **默认 4 秒**（快切优先，这是全片的节奏基线）；"
                "· **有台词的镜**：秒数必须装得下台词 —— **≈ 台词字数 ÷ 4 秒**"
                "（中文口播约 4–5 字/秒：16 字用 4 秒、24 字用 6 秒、40 字用 10 秒）；"
                "**宁可把这一镜排长，也不要把台词砍短**；"
                "· **长镜（6–12 秒）**只在这三种情况下用：**一个连续动作或一段完整台词必须"
                "一口气演完**、**情绪停顿需要留白**、**场景交代** —— 并**在「运镜」或"
                "「画面描述」里写明为什么这一镜要长**。"
                "**避免全表等长** —— 真实节拍有呼吸：反应镜短（4–5 秒）、铺陈与台词镜长。"
                % (round(tgt), int(round(fast)), round(tgt * 0.85)))
            # ⚠️ 配额上限会**压住快切** —— 必须说出来，不能让它排出会被静默截断的镜数。
            if cap and fast > cap:
                lines.append(
                    "⚠️ 本片目标 %d 秒 ÷ 4 = %d 镜，**超过视频生成配额上限 %d 镜** ⇒ "
                    "实际只有 %d 镜可用、单镜平均 **%.1f 秒**（**本片做不到 4 秒快切**）。"
                    "请按这个更长的单镜节奏排片（单镜仍**不得超过 12 秒**），"
                    "用**更长的镜头承载内容**，不要排出会被截断的镜数。"
                    % (round(tgt), int(round(fast)), cap, cap, tgt / cap))
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
        # 3 秒。打包时长下限 4s/镜（pack_clamp_sec），反应镜写 4s 即合规。
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
    if reasons:
        lines.append("")
        lines.append("【本次是评审打回后的重跑，必须修正以下问题】")
        for x in reasons:
            lines.append("- " + str(x))
    return "\n".join(lines)


# ─── 分镜音频模式守卫（2026-09-12 从 graph.py 迁入：媒体链的 storyboard gate 依赖它）──


def _audio_mode_defect(root: Path) -> str:
    """分镜是否违反 brief 的音频模式（返回问题描述，合规则空串）。

    只做**确定性**判定，不看模型评审意见：
      · dialogue-led 但台词镜占比 < DIALOGUE_MIN_RATIO → 回退
      · silent 但有台词镜 → 回退（反向漏检同样会让成片跑偏）
    """
    sb = guards.resolve_path(root, "scenedesigner")   # M1：集级路径，读走兼容解析
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
