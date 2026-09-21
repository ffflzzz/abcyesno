# -*- coding: utf-8 -*-
"""驱动 supervisor 创作链（**不用 runs.stream**）。

为什么不用 `scripts/diag_orch.py` 的流式方式（2026-09-12 实测）：
    `client.runs.stream(thread_id, run_id)` 在本机 dev server 上稳定 404
    （`NotFoundError: Thread or assistant not found`），而同一时刻 dev.log 里
    该 thread 的 supervisor run **实际在后台执行并成功**。即"流式端点不可用，
    但 run 是好的"。故改为：创建 run → 轮询 run 状态 + 轮询项目产物落盘。

用法：
    py scripts/drive_chain.py <项目> [--url ...] [--timeout 7200] [--ep N]
                                 [--hitl-timeout 0]

`--hitl-timeout`：等待**人工确认**的独立上限（秒；**0 = 无限等**，默认）。
  它与 `--timeout`（链本身的总预算）**互不叠加** —— 人思考的时间会从 `t0` 里
  抵消掉，否则全手动模式下"隔天回来"必然被判超时（见 `_wait_decision`）。

★ M3（2026-09-17）：`--ep N` 指定本集。**完成判据必须按本集的产物路径** ——
   改造前用 `(root / role).glob("*.md")`，第 2 集会被第 1 集的 `scriptwriter_ep1.md`
   满足 ⇒ 判"全跑完"、直接进媒体链（而第 2 集其实一个产物都没写）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config
from v5.guards import (PREREQ, load_manifest, out_path,  # noqa: E402
                       reset_from, resolve_path, save_manifest)

# ★ 完成判据必须用**被派发的 7 个角色**（`PREREQ` 的键），**不含 `director`**。
# 2026-09-12 实测教训：原判据写死 8 个（含 director），而 supervisor 架构里
# `director` 就是 supervisor 自己、不产出角色产物 → 判据**永远不成立**
# → 白多跑了两轮（每轮 20+ 分钟）。口径必须与 `guards.GATE_ROLES` 一致。
ROLES = list(PREREQ)


def reached(got, until: str = "") -> bool:
    """`until` 之前的角色（含它自己）是否都已产出；`until` 空 = 全部 7 个。

    ★ 2026-09-19（前端**两段式**）：`--until scriptwriter` = "只要剧本正文"。
      判据是**该角色**及**它之前的全部角色**都在盘上 —— 只判"until 在不在"的话，
      上游缺失也会算达成（下游产物会基于不存在的上游写出来，等于假产物）。
    """
    have = {str(x) for x in (got or [])}
    if not until:
        return all(r in have for r in ROLES)
    if until not in ROLES:
        return False                     # 调用方应已校验；这里不猜
    return all(r in have for r in ROLES[:ROLES.index(until) + 1])


def _arg(flag: str, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


async def _pending_actions(c, tid: str) -> list:
    """本次挂起的**待批动作**列表（空列表 = 没挂起）。**唯一可信的中断判据**。

    ## 为什么不看 run 状态（2026-09-18 实测，决定性）

    `runs.get()` 对一个**已中断**的 run 报的是 **`success`**，不是 `interrupted`。
    实测状态序列就是 `running → success`；而同一刻 `threads.get_state(tid)` 是

        next  = ['HumanInTheLoopMiddleware.after_model']
        tasks[0].interrupts[0].value.action_requests = [{'name': 'task', 'args': {...}}]

    ⇒ 旧判据 `if status == "interrupted"` **永不成立**，于是：
      · 图停住了等人，驱动器却以为"这轮跑完了"，**再发一个新 run**；
      · 新 human 消息把挂起的 `task` 全部顶掉（ToolMessage 里写着
        `was cancelled - another message came in before it could be completed`）；
      · 于是**每一轮都重复同一件事**，产物永远停在 worldbuilder。
    这也解释了为什么"步级 HITL 的 CLI 从来没真正生效过" —— 那段代码是照文档写的，
    但**没有对着真服务验证过状态到底怎么报**。本函数是补上的验证结论。

    ## 返回值同时决定"要发几个决定"

    中间件会校验 `len(decisions) == 挂起的工具调用数`（`human_in_the_loop.py:459-464`，
    不匹配直接 `ValueError`）。而导演**会在同一条回复里派两个角色**
    （assetdesigner + plotdesigner，纪律第 2 步就是这么要求的）⇒ 挂起 2 个动作。
    所以 resume 时必须**按实际数量**给决定，不能写死 1 个。
    """
    try:
        st = await c.threads.get_state(tid)
    except Exception:            # noqa: BLE001 —— 查不到就当"没挂起"（保守，不误判挂起）
        return []
    out: list = []
    for t in (st.get("tasks") or []):
        for itr in (t.get("interrupts") or []):
            out.extend((itr.get("value") or {}).get("action_requests") or [])
    return out


async def _wait_decision(root: Path, project: str, tid: str, rid: str,
                         got: list, hitl_timeout: float,
                         interval: float = 5.0) -> dict | None:
    """图已 `interrupt` 挂起 → 记录待批准并轮询等外部决定。

    返回**决定字典**（`hitl.read_decision` 的形态：`decision` / `target` / `by` /
    `note` / `stamp`）；超时返回 `None`。
    信道是项目下的 `pending.json` / `decision.json`（见 `v5/hitl.py` 的说明：
    链路在后台跑、没有终端，所以用文件跟人工侧通信）。

    ## 两处 2026-09-18 的改动（都为前端「逐步人工确认」服务）

    1. **不再吃链的总预算**。旧实现是 `while time.time() - t0 < timeout`，用的是
       **链启动时**的 `t0` ⇒ **人思考的时间在消耗链的超时**（`runner.py:348` 只给
       5400 秒）——全手动下这是**必然**发生的误判（隔天回来必被判超时）。
       现在用独立预算 `hitl_timeout`（**0 = 无限等**）；等待时长由 `main()`
       加回 `t0` 抵消。
    2. **按 `stamp` 校验决定**（见 `v5/hitl.py` 的说明）。戳不匹配 = 决定是给
       **上一次挂起**的 ⇒ 不算数，且**必须响亮告警** —— 否则用户会一直以为
       自己在等链路，而链路在等一个永远不会来的（有效）决定。
    """
    from v5 import hitl

    hitl.record_pending(root, thread_id=tid, run_id=rid, done_roles=got)
    _pd = hitl.read_pending(root) or {}
    stamp = str(_pd.get("stamp") or "")     # 本次挂起的唯一标识
    print("[drive] ⏸ 已挂起等待人工确认：%s" % hitl.status(root), flush=True)
    print("[drive]   继续：py -m v5.series %s --hitl-approve --by 你的名字" % project,
          flush=True)
    print("[drive]   打回：py -m v5.series %s --hitl-redo --target <角色> --note 原因"
          % project, flush=True)
    print("[drive]   中止：py -m v5.series %s --hitl-reject --note 原因" % project,
          flush=True)
    print("[drive]   （等待%s）" % ("上限 %.0f 秒" % hitl_timeout
                                 if hitl_timeout else "不设上限，直到人来决定"),
          flush=True)
    warned = False
    waited = 0.0
    while True:
        await asyncio.sleep(interval)
        waited += interval
        d = hitl.read_decision(root, stamp=stamp)
        if d:
            hitl.clear_decision(root)      # 一次决定只消费一次
            # ★ 同时撤掉挂起标记（2026-09-18 验收实测的坑）：`pending.json` 只在
            #   **下次挂起时**才被覆盖 ⇒ 链路执行的那几分钟里前端会读到**过期的**
            #   pending、弹出一条"等你确认"，而用户点了没反应（戳校验会正确忽略它，
            #   安全但莫名其妙）。执行期理应"没有待批"。
            hitl.clear_pending(root)
            print("[drive] ▶ 收到决定：%s%s（by %s）"
                  % (d["decision"],
                     (" → 打回 " + str(d.get("target"))) if d.get("target") else "",
                     d.get("by") or "—"), flush=True)
            return d
        if not warned and hitl.read_decision(root):
            # 有文件、但戳不匹配 ⇒ 它是给**上一次挂起**的，已失效。
            # 必须说出来：否则人以为"我批过了"，链路却在干等（本项目忌"静默"）。
            print("[drive] ⚠️ 磁盘上有一个**已失效**的决定（属于上一次挂起，戳 %s ≠ %s）"
                  "—— 已忽略，请**重新确认**这一步。"
                  % ((hitl.read_decision(root) or {}).get("stamp") or "无", stamp or "无"),
                  flush=True)
            warned = True
        if hitl_timeout and waited > hitl_timeout:
            print("[drive] 等待人工确认超时（%.0f 秒）→ 终止" % hitl_timeout, flush=True)
            return None
    return None


def redo_message(tgt: str, note: str) -> str:
    """把「打回」翻成一条发给导演的消息（**纯函数**，便于单测）。

    两种情况语义不同，必须分开写（2026-09-19）：
      · 普通角色：**只重派它 + 补齐下游**（上游是人已确认过的，不许重跑）；
      · `director`（制作规格 `/director/director.md`）：director **不是被派发的角色**
        （supervisor 自己写），所以这不是"重派一个角色"，而是
        **重写规格 + 整链重做** —— 规格（片长/画幅/音频模式/视觉基准）一变，
        下游全部作废，没有任何"上游"可留。
    """
    if tgt == "director":
        return ("【人工打回】**制作规格**不合格，用户要求重做。"
                "用户写的原因：%s\n"
                "处理要求：\n"
                "1. **先重写 `/director/director.md`**（用 write_file）——"
                "这份规格是整条链的输入，用户已确认**下游全部作废**；\n"
                "2. 然后按依赖序**重新派发全部 7 个角色**（用 `task`）："
                "worldbuilder → assetdesigner ∥ plotdesigner → scriptwriter → "
                "dialogue → scenedesigner → reviewer；\n"
                "3. **不要重复派发刚被拒的那一步**（本次挂起的动作全部作废）；\n"
                "4. 旧产物已全部移走（`.rerun_backup/`），必须**重新写出**完整产物。"
                % note)
    return ("【人工打回】用户看过 `%s` 的产物后**不满意**，要求重做。"
            "用户写的原因：%s\n"
            "处理要求：**只重新派发 `%s`**（用 `task`），然后按依赖序把它的"
            "**下游重新补齐**。\n"
            "· 不要重跑它的**上游**（那些用户已确认通过）；\n"
            "· **不要重复派发刚被拒的那一步**（本次挂起的动作全部作废）；\n"
            "· 它的旧产物已被移走，必须**重新写出**完整产物。"
            % (tgt, note, tgt))


async def main() -> int:
    from langgraph_sdk import get_client

    project = next((a for a in sys.argv[1:] if not a.startswith("--")), "")
    if not project:
        print("用法: py scripts/drive_chain.py <项目>")
        return 1
    # `--url` wins; else the documented env var; else 2024. The env fallback
    # matters: the packaged app probes a free port for `langgraph dev`, so a
    # bare 2024 literal dispatches to whatever unrelated server holds it.
    url = (_arg("--url", "") or os.environ.get("SHORTDRAMA_V5_AGENT_URL", "")
           or "http://127.0.0.1:2024")
    timeout = float(_arg("--timeout", "7200"))
    ep = int(_arg("--ep", "1"))
    # ★ 2026-09-19：**跑到某个角色为止**（`--until scriptwriter` = 只产出剧本正文）。
    #   为什么需要：前端是**两段式**的 —— 先确认「简介」再生成「剧本正文」，
    #   而生成正文只需要链的前半段（worldbuilder → assetdesigner∥plotdesigner → scriptwriter）。
    #   不传 = 跑完整条链（原行为，逐字节不变）。
    until = str(_arg("--until", "") or "").strip()
    if until and until not in ROLES:
        print("[drive] --until 只能是 %s 之一（收到 %r）" % ("、".join(ROLES), until))
        return 1
    # ★ 等待**人工确认**的独立预算（秒）。0 = **无限等**（默认）。
    #   只与步级 HITL 有关；没开那个开关时这一行完全不参与（`_wait_decision` 不被调用）。
    hitl_timeout = float(_arg("--hitl-timeout", "0"))
    # Must come from config, not a cwd-relative "projects": the packaged app
    # spawns this with cwd = the (possibly read-only) install tree and
    # SHORTDRAMA_PROJECTS pointing elsewhere, so a literal would split one
    # project across two roots and the HITL channel would never meet the shim.
    root = config.PROJECTS_DIR / project

    # 新链一律从"没有待批"开始。默认（自动模式）下 `record_pending` 永不被调用，
    # 上一次逐步确认留下的 `pending.json` 就**没有任何机会被覆盖**，前端会永远挂着
    # 一条过期的「等你确认」—— 用户点了没反应（戳校验正确地忽略了他）。
    from v5 import hitl as _hitl
    _hitl.clear_pending(root)

    # 本集绑定：`role_input` 每次开工都从 manifest 读 `episode_index` 决定产物路径，
    # 所以**必须在这里就把集号写对**（否则角色全写到上一集去）。
    from v5.series import bind_episode
    bind_episode(root, ep)
    print("[drive] 本集 = 第 %d 集（产物形如 %s）" % (ep, out_path("scenedesigner", ep)),
          flush=True)

    c = get_client(url=url)
    th = await c.threads.create()
    tid = th["thread_id"]
    print("[drive] thread = %s" % tid, flush=True)

    def done_roles() -> list:
        """产物侧进展（不依赖 server 状态）。

        ★ M3：判据必须是**本集**的产物文件（含 `_ep{N}`）。用 `glob("*.md")` 会让
        第 2 集被第 1 集的产物满足 → 判"全跑完"→ 白跑一轮媒体链。
        读走 `resolve_path`：第 1 集的历史项目产物是旧名，也要认。
        """
        got = []
        for r in ROLES:
            p = resolve_path(root, r, ep)
            if p.exists() and p.stat().st_size > 0:
                got.append(r)
        return got

    # ★ supervisor 是**按批次**推进的（一次 run 只做一个批次）——
    # 2026-09-12 实测：一个 run 跑 31 分钟只产出 worldbuilder。
    # 所以必须**循环发 run**，并且目标提示要求它一次把剩余角色推完
    # （同一 run 内多批次，受 recursion_limit 约束）。
    # ★ 同步子代理：一次 run 就应该把整条链跑完，所以目标提示直接要求"跑完整条链"。
    # 驱动器的循环退化为兜底（真跑完就一轮结束）。
    # ★ M5 配套（2026-09-17）：**全剧级产物要在第 1 集锁定，第 2..N 集不重跑**。
    #
    # 为什么必须这样（serial-smoke 实测）：每个角色的产物路径分两类 ——
    #   · **集级**（`scriptwriter_ep{N}.md` / `dialogue_ep{N}.md` / …）：每集重跑，正确
    #   · **全剧级**（`worldbuilder.md` / `assets.md` / `episodes.md`）：**一次锁定、全剧复用**
    # 但链路每集都会把 7 个角色全部重跑 ⇒ ep2 会把全剧级产物**重新生成一遍**（实测
    # `worldbuilder.md` 在 ep2 的 07:34 被覆盖）。后果：
    #   ① 浪费（每集多花 4 个角色的大模型调用）；
    #   ② **跨集错位**：角色卡/资产名一变，ep1 的分镜就引用着旧名字，而参考图
    #      （`cast.ensure` 幂等）不会跟着重画 ⇒ 文字与图对不上 ⇒ 静帧 QC 残留暴增。
    # ⇒ 第 2 集起，只要**全剧级产物已在盘**，就在目标提示里明确**跳过**这三步。
    #   （`done_roles()` 判"全剧级文件在盘"照样成立，所以完成判据不受影响。）
    WHOLE_DRAMA = ("worldbuilder", "assetdesigner", "plotdesigner")
    _have_whole = all(resolve_path(root, r, 1).exists() for r in WHOLE_DRAMA)
    if ep > 1 and _have_whole:
        _plan = ("本集**用 `task` 依次派发**这 4 个角色："
                 "scriptwriter → dialogue → scenedesigner → reviewer。"
                 "上游的全剧级产物直接读盘上的现成文件。")
        print("[drive] 第 %d 集：全剧级产物已在盘 → 目标提示将要求**跳过** %s"
              % (ep, "、".join(WHOLE_DRAMA)), flush=True)
    else:
        # ★ 2026-09-19：worldbuilder **回到派发**（方案 D 撤销，理由见
        #   `v5/orchestrator.make_async_subagents` 上方与 `v5/roles.director_system_prompt`）。
        #   旧文案写的是「开工第一件事：**由你亲自**写出 worldbuilder 的产物」——
        #   它与这里下面那条 `_DISPATCH`（「不要自己用 write_file 写任何创作产物」）
        #   **直接打架**，模型两头都能选。实测选中的是"自己写"，于是 worldbuilder
        #   的 per-role SKILL 完全没上场，交出的角色卡是 director 的表格形态，
        #   媒体链解析出 0 个角色 → 到成片才暴露。
        _plan = ("开工第一件事：**用 `task` 派发 worldbuilder**（剧情概要 + 世界观 + "
                 "角色卡），等它返回再往下。\n"
                 "**其余全部用 `task` 依次派发**：assetdesigner → plotdesigner → "
                 "scriptwriter → dialogue → scenedesigner → reviewer。\n"
                 "为什么这一步也必须是 `task`：每个角色手里握着自己那份"
                 "`packs/<包>/<角色>/SKILL.md` 契约（产物格式是硬契约，媒体链按它解析），"
                 "由你代写会**换掉契约**——实测后果是角色卡被写成表格、解析出 0 个角色。")
    # ★★★ 纪律行**必须在最前、且必须是祈使 + 禁止**（2026-09-17 血泪事故）：
    #   我 M3 改写的版本把派发要求写成了"…再按依赖序派发 A + B；之后才是 C → D → …"
    #   —— 派发对象只在 A/B 处点了名，后面四个角色变成"之后才是…"的**裸序列**。
    #   结果模型**一个 `task` 都没派发，自己把 4 个角色全写了**：
    #     · `role_fs_root.txt` 一行没多、`[guards] 记账` 一次没打印、`phases[ep]` 无本集名册、
    #       `token_usage.run` 恒为 0 ⇒ **角色节点一次都没执行**；
    #     · 而产物**照样齐全、门全过、reviewer 也 pass** ⇒ 这件事被**掩盖了整整两集**
    #       （见本文件末尾的"代写探针"，就是为它加的）。
    #   ⇒ 派发要求写成**最前面的祈使句 + 明确禁止代写**，缺一不可。
    _DISPATCH = ("**纪律（最重要，先读这条）**：每一个角色都必须用 "
                 "`task(角色名, 任务简报)` **派发**出去执行 —— "
                 "**不要自己用 `write_file` 写任何创作产物**"
                 "（剧本、台词、分镜、评审报告都不是你的职责，你只负责调度与汇报）。\n")
    # ★ 2026-09-19：`--until` 时**同时约束导演**（提示词是主要手段，`reached()` 是兜底）——
    #   否则它会一路派发到 reviewer，白烧后面 4 个角色的调用。
    _STOP = (("\n**★ 本轮只跑到 `%s` 为止**：它的产物落盘后**立刻结束回复**，"
              "**不要**派发它的下游角色（下游由下一轮负责）。" % until) if until else "")
    _TAIL = ("最后报告这一步产物的路径。" if until
             else "最后报告每一步产物路径与 reviewer 的判定。")
    GOAL = ("本项目当前要产出的是**第 %d 集**。\n%s按依赖序把本集需要的创作链"
            "**在本轮内跑完**：%s%s\n"
            "`task` 是**同步**调用：上一个返回之后再调下一个，不要中途停下等待。\n"
            "%s"
            % (ep, _DISPATCH, _plan, _STOP, _TAIL))
    t0 = time.time()
    round_no = 0
    # ★★ **"静默代写"探针**（2026-09-17 加，为它付了两集的学费）
    #
    # 事故：supervisor **一个 `task` 都没派发**、自己把 4 个角色的产物全写了。
    # 表现是「产物齐全 + 门全过 + reviewer 也 pass」——**从产物侧完全看不出来**；
    # 只有 `role_input` 里的东西（上游注入 / craft / 按集切片 / `record_phase` 记账 /
    # `TokenBreaker`）**全部静默失效**。真正能戳破它的只有角色节点自己的落盘痕迹：
    # `projects/.tmp/role_fs_root.txt`（`node` 的第一句就 append 一行）。
    # ⇒ 开跑前记一次行数、收工时再记一次；**产物齐全却没多行** = 疑似代写，响亮告警。
    _diag = config.PROJECTS_DIR / ".tmp" / "role_fs_root.txt"

    def _diag_lines() -> int:
        try:
            return len(_diag.read_text(encoding="utf-8", errors="replace").splitlines())
        except Exception:  # noqa: BLE001
            return -1

    _diag0 = _diag_lines()
    resume_cmd = None          # 非 None → 本轮发 resume（步级 HITL），否则发新 input
    aborted = ""               # 非空 = **人工**结束（中止/打回失败/等超时）→ rc=4
    pending_n = 0              # 本次挂起的**待批动作数**（resume 要按它给决定，见 _pending_actions）
    # ★ status 必须在循环外初始化（2026-09-21 实测）：产物全齐时第一轮就 break，
    #   从未进过循环体 ⇒ 536 行 `if status in ("error","timeout")` 读到未初始化变量
    #   → UnboundLocalError → 重跑**已完成的集**1 秒炸 rc=1（被误报"重跑必须加 --fresh"）。
    #   空串语义 = "未跑任何 run"，error/timeout 判定自然不命中。
    status = ""
    while time.time() - t0 < timeout:
        got = done_roles()
        if reached(got, until):
            # 2026-09-19：`--until` 时"够"= 达成到该角色为止（不是 7 个都齐）
            print("[drive] %s %s"
                  % ("全部角色完成" if not until else "已达成 --until %s" % until, got),
                  flush=True)
            break
        round_no += 1
        # recursion_limit 必须够大：**同步**子代理在父 run 内联跑，
        # 6 个角色各自的步数都计入父 run 的预算（旧值 120 在异步下够用，
        # 同步下会被撑爆）。可用 SHORTDRAMA_RECURSION_LIMIT 覆盖。
        cfg = {"recursion_limit": int(
            os.environ.get("SHORTDRAMA_RECURSION_LIMIT", "600"))}
        if resume_cmd is None:
            run = await c.runs.create(
                tid, "supervisor",
                input={"messages": [{"role": "user", "content": GOAL}]}, config=cfg)
            tag = ""
        else:
            # 步级 HITL：把人工决定交回**同一个 thread**（`Command(resume=...)`）。
            run = await c.runs.create(tid, "supervisor", command=resume_cmd,
                                      config=cfg)
            tag = "【resume】"
            resume_cmd = None
        rid = run["run_id"]
        print("[drive] 第 %d 轮 run=%s%s（已有 %s）" % (round_no, rid, tag, got),
              flush=True)
        status = "?"
        poll_err = 0          # 连续轮询失败次数
        dead = False
        while time.time() - t0 < timeout:
            await asyncio.sleep(20)
            try:
                r = await c.runs.get(tid, rid)
                status = r.get("status") or "?"
                poll_err = 0
            except Exception as e:  # noqa: BLE001
                status = "poll-err:%s" % str(e)[:50]
                poll_err += 1
            _now = done_roles()
            print("[%5ds] r%d run=%-12s 产物=%s"
                  % (time.time() - t0, round_no, status, _now), flush=True)
            # ★ 2026-09-19：`--until` 已达成 ⇒ **不等这一轮 run 结束**
            #   （它可能还在往下游派发）。取消它 + 撤掉已挂起的待批，然后收工。
            #   ⛔ 取消失败必须**响亮**：否则人会以为链停了，而它还在后台烧调用。
            if until and reached(_now, until):
                print("[drive] ✅ 已达成 --until %s ⇒ 取消当前 run 并收工"
                      "（免得它继续往下游派发）" % until, flush=True)
                try:
                    await c.runs.cancel(tid, rid)
                except Exception as e:      # noqa: BLE001
                    print("[drive] ⚠️ 取消 run 失败（%s: %s）—— 链可能仍在后台继续，"
                          "请查 dev 日志 / Studio" % (type(e).__name__, str(e)[:120]),
                          flush=True)
                from v5 import hitl as _hitl
                if _hitl.read_pending(root):
                    _hitl.clear_pending(root)
                    print("[drive]   已撤掉挂起的待批（本轮目标已达成，那一步作废）",
                          flush=True)
                break
            if status in ("success", "error", "timeout", "interrupted"):
                break
            # ★ dev server 死了要**立刻发现**（2026-09-15 实测）：
            #   当晚 dev server 在 t≈402s 退出后，驱动器拿到的全是
            #   `poll-err:All connection attempts failed`，但它不认这个信号，
            #   于是一路空转到 `--timeout 5400`（90 分钟）才收工 ——
            #   期间没有任何产物、没有任何告警。这违反本项目的
            #   「失败必须可见 / 静默空转是最贵的一类 bug」。
            #   6 次 × 20s = 2 分钟纯连接失败，判定为"服务已不在"。
            if poll_err >= 6:
                dead = True
                break
        if dead:
            print("[drive] !! 连续 %d 次轮询失败（%s）—— dev server 很可能已退出，"
                  "提前终止，不把 %ds 超时白等掉。"
                  % (poll_err, status, int(timeout)), flush=True)
            break

        # ★★★ 2026-09-18 **关键修复**：`success` 也可能意味着「**停住了等人**」。
        #
        #   实测（决定性，见 `_pending_actions` 的文档）：中断时 `runs.get()` 报的
        #   就是 `success`，状态序列是 `running → success`；而此刻 thread 是
        #   `next=['HumanInTheLoopMiddleware.after_model']` + `interrupts` 非空。
        #
        #   旧代码把 `success` 直接当"这轮跑完" ⇒ 又发一个新 run ⇒ 新 human 消息把
        #   挂起的 `task` 全顶掉（ToolMessage: `was cancelled - another message came
        #   in before it could be completed`）⇒ **每一轮原地打转、产物永远停在同一处**。
        #   这正是"步级 HITL 的 CLI 从来没真正生效过"的根因。
        #
        #   判据改成问 **thread 状态**（`next` + `tasks[].interrupts`），那才可信。
        if status == "success":
            _acts = await _pending_actions(c, tid)
            if _acts:
                status = "interrupted"
                pending_n = len(_acts)
                print("[drive] ⏸ 检测到挂起（runs.get 报 success，但 thread 停在 "
                      "HumanInTheLoopMiddleware；待批动作 %d 个）"
                      % pending_n, flush=True)

        if status == "interrupted":
            # 步级 HITL（`SHORTDRAMA_APPROVE_EACH_ROLE=1`）：等人给了决定再 resume。
            # ⚠️ 旧实现把 `interrupted` 与 `success` 同等对待（break 出内层后直接发
            # 新 run）—— 那样会**丢掉挂起的 thread 状态**、从头重跑一遍。
            _wait_t0 = time.time()
            dec = await _wait_decision(root, project, tid, rid,
                                       done_roles(), hitl_timeout)
            # ★ **等人不算链的时间**（2026-09-18）：把这段补回 `t0`。
            #   不补的话，人思考 10 分钟就等于从链的 90 分钟预算里扣掉 10 分钟
            #   —— 全手动模式下这是**必然**发生的误判，不是边缘情况。
            t0 += time.time() - _wait_t0
            if dec is None:
                aborted = "等待人工确认超时"
                break
            _kind = str(dec.get("decision") or "").strip().lower()
            if _kind == "reject":
                aborted = "人工中止（reject）"
                print("[drive] 人工中止 → 终止", flush=True)
                break
            if _kind == "redo":
                # ⏪ **打回**：重跑"目标角色 + 它的全部下游"。
                #
                # 两个动作缺一不可（都是实测换来的判据）：
                #   ① `reset_from` 清**本集** phases + 把旧产物移到 `.rerun_backup/`
                #      —— 产物**必须挪走**：`done_roles()` 是按"文件在不在"判进度的
                #      （本文件 `:93-105`）⇒ 不挪就判"这步已完成" ⇒ **重跑空转**。
                #   ② 把决定翻成一条**带消息的 `reject`** resume 回同一 thread。
                #      实测中间件对 `reject` 会**跳过工具执行**并把 `message` 交给模型
                #      （`langchain/agents/middleware/human_in_the_loop.py:337-354`）
                #      ⇒ 我们不需要自己造中断机制。
                tgt = str(dec.get("target") or "")
                try:
                    _m = load_manifest(root)
                    _reset = reset_from(tgt, _m, root, ep)
                    save_manifest(root, _m)
                except Exception as e:      # noqa: BLE001
                    # 打回失败**不能**带着半截状态继续（那会产出对不上的产物）
                    print("[drive] !! 打回失败（%s: %s）→ 终止；请查 .rerun_backup/ 与 manifest"
                          % (type(e).__name__, str(e)[:200]), flush=True)
                    aborted = "打回失败：%s" % str(e)[:120]
                    break
                print("[drive] ⏪ 打回 %s：已清空本集进度（%s），旧产物移至 .rerun_backup/"
                      " → %s"
                      % (tgt, "、".join(_reset) or "—",
                         "请求导演**重写制作规格 + 整链重做**" if tgt == "director"
                         else "请求导演只重派 %s 并补齐下游" % tgt), flush=True)
                _note = str(dec.get("note") or "（未写原因）")
                _msg = redo_message(tgt, _note)
                # ⚠️ 决定个数**必须等于**挂起的动作数（中间件校验，见 `_pending_actions`）：
                #    导演在同一条回复里派 assetdesigner + plotdesigner 时会挂起 **2** 个，
                #    只给 1 个决定会 `ValueError: Number of human decisions (1) does not
                #    match number of hanging tool calls (2)`。两个都要拒（我们改派 target）。
                resume_cmd = {"resume": {"decisions": [
                    {"type": "reject", "message": _msg}
                    for _ in range(pending_n or 1)]}}
                continue
            # ⚠️ 同上：决定个数必须与挂起的动作数一致，不能写死 1 个。
            resume_cmd = {"resume": {"decisions": [
                {"type": "approve"} for _ in range(pending_n or 1)]}}
            continue
        if status in ("error", "timeout"):
            print("[drive] 本轮终止 status=%s" % status, flush=True)
            break
    _got = done_roles()

    # ★★ 2026-09-18：**人工结束**（中止 / 打回失败 / 等待超时）⇒ 专用退出码 `4`。
    #
    # 为什么不复用 0：中止也会 `break` 出来，落到 `return 0`，而
    # `runner._exec_chain` 接着按"角色产物缺没缺"判 ⇒ 报
    # `failed: 创作链未产出这些角色的契约产物：…`
    # ⇒ **把"人主动中止"说成"链路坏了"**，把排查方向带偏（也是"失败必须可见"
    # 的另一面：**成功/失败/人停**三态不能混成两态）。
    # 调用方据此记成 `cancelled`（**不是** failed，更**不是** ok）。
    if aborted:
        print("[drive] !! 创作链因**人工操作**结束：%s ⇒ rc=4"
              "（既不是'成功'，也不是'链路坏了'）" % aborted, flush=True)
        return 4

    # ★★ 2026-09-18：**run 自身失败（error/timeout）⇒ 一律非 0 退出**（旧实现返回 0）。
    #
    # 事故（laofuzi-shop 第 2 次跑，56 分钟，见 2026-09-18 记忆）：
    #   账号级 **API 速率限制（429，`You've reached the API rate limit for free users`）**
    #   让 supervisor 调不动模型 → 它**自己 `write_file` 代写了 7 个角色产物**
    #   → 产物凑齐 ⇒ 旧实现走到这里 `return 0` 判「创作链成功」。
    #   于是**失败被推迟到 reviewer 判定才暴露**，而那时盘上已是一份**假产物**。
    #   代价：56 分钟 + 一整轮返工。
    # ⇒ **失败必须当场可见**，不能靠下游兜 —— 这是本项目「静默空转是最贵的一类 bug」的又一实例。
    if status in ("error", "timeout"):
        print("[drive] !! 创作链 run 以 **status=%s** 结束 ⇒ 判定失败"
              "（旧实现在此处返回 0，把失败推给下游）" % status, flush=True)
        print("[drive]    最常见原因：**账号级 API 速率限制（429）/ 请求超时**。\n"
              "        查 `projects/<名>/dev.log` 里的 `OpenAIRateLimitError` / "
              "`OpenAITimeoutError`；\n"
              "        若确为限流，先确认**没有并行任务在抢同一账号额度**"
              "（免费 tier 的 rate limit 是**账号级共享**的）。\n"
              "        重跑必须加 `--fresh`：盘上多半是半程态或**代写产物**。", flush=True)
        return 3

    if reached(_got, until) and _diag_lines() <= _diag0:   # `--until` 时=达成到该角色
        # 产物齐全但探针没多出行 —— 见上面 `_diag` 的说明。
        #
        # ★★ 2026-09-18：**加交叉验证**（血案：当天 rc=3 误报，把正常链判死）。
        #   事故：`orchestrator` 的探针写入在 `async def node` 里调**阻塞的
        #   `os.mkdir`**，被 LangGraph 拦下（`BlockingError: Blocking call to os.mkdir`）
        #   ⇒ **探针从来没写成功过** ⇒ 此处把"探针坏了"读成了"角色没执行"，
        #   而那条链其实是完全正常的：`run=success`、7 个产物 `08:15 → 08:33`
        #   **逐级间隔 3–5 分钟**（真角色在跑的形态）。
        #   ⇒ **判据看不见的东西，既不能算通过、也不能算失败。**
        #   交叉验证手段：**产物时间跨度** —— 7 个角色逐个跑出来至少要十几分钟，
        #   supervisor 代写则会在极短时间内批量落盘。
        _ts = []
        for _r in ROLES:
            _p = config.PROJECTS_DIR / project / out_path(_r, ep)
            if _p.exists():
                _ts.append(_p.stat().st_mtime)
        _span = (max(_ts) - min(_ts)) / 60.0 if len(_ts) >= 3 else 0.0

        print("[drive] ⚠️⚠️ **产物齐全，但探针没有多出行**（`%s`）\n"
              "        · 可能是 supervisor **代写**产物（角色契约未生效：上游产物注入 / "
              "按集切片 / craft 技法 / `record_phase` 记账 / token 熔断全部静默失效）；\n"
              "        · 也**可能是探针自己写失败**（2026-09-18 前它由 async 节点里的"
              "阻塞 `os.mkdir` 引起，**已修**）。\n"
              "        交叉验证：产物时间跨度 = **%.1f 分钟**" % (_diag, _span), flush=True)
        if _span < 5.0:
            print("[drive]    ⇒ 跨度 < 5 分钟 ⇒ **判定为代写**：产物不可信，不得进入"
                  "媒体链；**判定失败（rc=3）**，重跑必须加 `--fresh`。", flush=True)
            return 3
        print("[drive]    ⇒ 跨度 ≥ 5 分钟（产物**逐级**出现）⇒ **形态上是真角色在跑**"
              "⇒ **不判代写、不阻断**。\n"
              "        若要彻底确认，请查 dev.log 有无「节点诊断写失败」；"
              "探针修好后本判据即恢复可靠。", flush=True)

    print("[drive] 结束 产物=%s" % _got, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
