# -*- coding: utf-8 -*-
"""Supervisor 架构 v2（2026-09-11 晚）：per-role 专用 graph。

v1 教训（五轮迭代实证）：AsyncSubAgent 的 input 由 LLM 自由生成——v1 要求它
构造结构化字段（role/project/…），模型给相对名/绝对路径随机、字段拼成自然
语言，三层桥接补丁打不赢"参数形态不可控"这个根因。

v2 正解：**消除不可控层**——
- 每个角色一张**专用 graph**（角色 SKILL、模型、项目目录全部**编译期固定**），
  经 langgraph.json 注册为 role_<角色名>；
- start_async_task 的 input 只剩**一段自然语言任务简报**（LLM 最擅长的形态）；
- supervisor 只需要决定顺序与并发（它的本职），不再构造路径与字段。

设计红线不变：reviewer 独立 subagent（审改分离）；失败重派一次二次停下问人
（HITL）；媒体链外挂不变；守卫层不变。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.async_subagents import AsyncSubAgent
from deepagents.middleware.subagents import CompiledSubAgent
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.profiles import (GeneralPurposeSubagentProfile, HarnessProfile,
                                 register_harness_profile)
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState

from . import config
from .llm import role_chat
from .media import rerender_agent
from .roles import FS_TOOLS, director_system_prompt, role_input, role_system_prompt
from .guards import PREREQ, boot_episode, load_manifest, out_path, record_phase

# ── 禁用 general-purpose 同步子代理（2026-09-12 实测修复）─────────────────────
# create_deep_agent 默认注入同步 `general-purpose` 子代理 → 挂 SubAgentMiddleware
# → 暴露 `task` 工具（与异步 start_async_task 并存的第二条派发通道，模型可能选错；
# 实测 supervisor 因此有 13 个工具）。官方解法即下述 profile（实测 task 消失）。
# 注：我们的模型 role_chat 是 ChatOpenAI 子类，_get_ls_params().ls_provider == "openai"。
register_harness_profile("openai", HarnessProfile(
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
))

# ── 部署配置 ──────────────────────────────────────────────────────────────────
# v5 独立部署：项目目录由 SHORTDRAMA_V5_PROJECT 指定（缺省 studio）。
_PROJECT = os.environ.get("SHORTDRAMA_V5_PROJECT", "studio")
_PACK = os.environ.get("SHORTDRAMA_STUDIO_PACK", "shortdrama")
# supervisor 与全部 role graph 的 FS 视角统一 = 项目目录本身（/brief.json 等相对路径直接可达）。
_root: Path = config.PROJECTS_DIR / _PROJECT
_root.mkdir(parents=True, exist_ok=True)

# ★ M1 加、**M3 改**：起服集号（`boot_episode`）。
#
# M1 时它是**编译期固化点**（角色 system prompt 里写死本集产物路径）⇒ 换集必须重启 dev。
# M3（2026-09-17）把产物路径改成**每次开工由 `role_input` 给定**（运行期真相），
# 这里只剩"起服时的默认值"这一个用途 ⇒ **一个 dev server 可连续跑第 1..N 集**。
_EP: int = boot_episode(_root)

# 单个角色 agent 的**步数预算**（GraphRecursionError 的上限）。
#
# 2026-09-12 实测事故（six-winters，36 镜）：这里原本硬编码 `40`。
# `reviewer` 要读 7 份上游产物（其中分镜 19.6 KB）再写评分报告 —— 步数不够 →
#     langgraph.errors.GraphRecursionError: Recursion limit of 40 reached
# 且**父 run 的 recursion_limit 管不到这个值**：`drive_chain.py` 传了 600，
# 角色节点仍按自己的 40 走 → 整条链在最后一个角色处崩掉
# （产物只到 scenedesigner，reviewer 永远缺失，媒体链被 media_gate 拦住）。
# 这正是"多角色链路的预算要按最大读写的那个角色定"——所以默认取 150，
# 可用 SHORTDRAMA_ROLE_RECURSION_LIMIT 覆盖（长分镜项目可调更大）。
ROLE_RECURSION_LIMIT = int(os.environ.get("SHORTDRAMA_ROLE_RECURSION_LIMIT", "150"))

# ── 依赖序（supervisor 的 todolist 纪律引用）────────────────────────────────
PREREQ: dict[str, list[str]] = {
    "worldbuilder": [],
    "assetdesigner": ["worldbuilder"],
    "plotdesigner": ["worldbuilder"],
    "scriptwriter": ["plotdesigner"],
    "dialogue": ["scriptwriter"],
    "scenedesigner": ["scriptwriter", "dialogue", "assetdesigner"],
    "reviewer": ["scenedesigner"],
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    "worldbuilder": "世界观与角色卡（worldbuilder.md）——最先派发",
    "assetdesigner": "资产卡（assets.md）——worldbuilder 之后，可与 plotdesigner 同轮并行派发",
    "plotdesigner": "剧情结构与分幕（episodes.md）——worldbuilder 之后，可与 assetdesigner 同轮并行派发",
    "scriptwriter": "剧本执笔（scriptwriter_ep1.md）——plotdesigner 之后",
    "dialogue": "台词清单（dialogue.md）——scriptwriter 之后",
    "scenedesigner": "分镜 13 列（scenedesigner.md）——须等剧本/台词/资产齐",
    "reviewer": "独立审片（review.md，temperature=0）——最后派发；返回 pass=true 才算创作链完成",
}

#: 媒体链唯一一个子代理（2026-09-13，spec §3.3）。**不是角色**，不参与依赖序与
#: `GATE_ROLES` —— 它是"出完成片之后，人指出某一镜有问题"才派发的**修订**动作。
MEDIA_RERENDER_DESC = (
    "单镜重渲：修**已经渲出来**的某几镜（图生视频重做 + 自动重拼成片）。"
    "**只在成片已存在、且人指出具体哪一镜有问题时才派**，不要用它跑整条链。"
    "简报必须以头行开头：`【重渲】LN03` 或 `【重渲】LN03,LN05 from_still`"
    "（`from_still` = 连静帧一起重做；只在静帧本身画错时才加）；头行后面写清问题。"
    "**耗时 3–10 分钟**（内部要等视频生成与成片复核），返回即表示已重渲完并重拼。"
)

#: 本片"可直接重渲"的镜号提示（编译期读不到分镜时留空，由图内节点自己再读一次）
MEDIA_RERENDER_NAME = rerender_agent.NODE_NAME


# ── per-role 专用图（角色 SKILL/模型/项目目录**全部编译期固定**）──────────────
def _build_role_graph(role: str, pack: str):
    """单角色图：零运行时参数——input 只剩自然语言任务简报。

    项目目录编译期绑定（2026-09-11 晚定案）：v2 运行时传 project 的方案被
    "supervisor 不传路径（纪律）× role 需要路径"的死锁击穿——回到编译期绑定，
    换项目重启 dev（15 秒），换取 input 零结构化要求。
    成功后调 guards.record_phase 记账——supervisor 架构产出与标准 manifest 兼容的
    manifest（phases），media_gate / --resume-media 直接可用。
    """
    agent = create_agent(
        model=role_chat(role, 8192),
        tools=[],
        middleware=[FilesystemMiddleware(
            backend=FilesystemBackend(root_dir=str(_root), virtual_mode=True),
            tools=FS_TOOLS)],
        system_prompt=role_system_prompt(pack, role, _EP),
        name=role,
    )

    async def node(state: MessagesState) -> dict:
        # 可观测：**落盘一行"本角色节点确实跑了"的证据**（2026-09-11 起，2026-09-17 加强）。
        #
        # ★ 为什么改成 append 一行 + 失败要响：
        #   2026-09-17 三集验收撞到一个悬案 —— ep2/ep3 的 7 个角色产物**都在盘上**
        #   （且逐级、各隔 4–5 分钟出现，形态上就是"子代理在跑"），但
        #     · `record_phase` 的自报行（`[guards] 记账`）**一次都没打印**、
        #     · `phases` 里没有本集的名册（只有 `reconcile_manifest` 补的第 1/2 集）、
        #     · `token_usage.run` 恒为 0。
        #   `print` 在 dev.log（文件句柄）上是**块缓冲**、且本处原本 `except: pass`
        #   ⇒ 两者都不可作为判据。**文件 append 是唯一可靠的证据**：
        #   跑过一次就必然多一行，跑失败也不会被静默吞掉。
        # ★★ 2026-09-18 修：**阻塞 IO 必须挪出事件循环**。
        #
        #   旧实现直接在 `async def node` 里 `os.mkdir` + `open` ⇒ 被 LangGraph 的
        #   阻塞检测拦下（`BlockingError: Blocking call to os.mkdir`），
        #   异常被 except 吞成一行警告 ⇒ **这个探针文件从来没写成功过**。
        #   ⇒ 一切依据"探针没多出行"推出的「角色节点没执行 / supervisor 代写」结论
        #     **都可能是误判** —— 2026-09-17 的"三集验收悬案"与 2026-09-18
        #     laofuzi-shop 的 rc=3 误报，形态完全一致：产物**逐级各隔 3–5 分钟**
        #     正常出现（那是真角色在跑的形态），却因为探针写不出来而被判"没执行"。
        def _write_diag() -> None:
            import time as _t
            dbg = _root.parent / ".tmp" / "role_fs_root.txt"
            dbg.parent.mkdir(parents=True, exist_ok=True)
            with dbg.open("a", encoding="utf-8") as f:
                f.write("[%s] node 执行 role=%-14s ep=%s root=%s\n"
                        % (_t.strftime("%m-%d %H:%M:%S"), role,
                           int((load_manifest(_root).get("episode_index") or 1)), _root))

        try:
            await asyncio.to_thread(_write_diag)   # 阻塞 IO 走线程，不占事件循环
        except Exception as e:  # noqa: BLE001
            # ⛔ **不静默**：这个文件是"节点跑没跑"的唯一证据，写不了必须说出来
            print("[orchestrator] ⚠️ 节点诊断写失败：%s: %s" % (type(e).__name__, e),
                  flush=True)
        # 完整开工契约（role_input）：brief 路径 / 上游产物清单 / **产物路径与列契约**
        # ——2026-09-11 深夜实测：v2 只给自然语言简报时 scenedesigner 不写 13 列，
        # 被分镜契约门拦（缺列+镜序错乱）。契约注入必须保留，简报作为补充附后。
        try:
            m = load_manifest(_root)
        except Exception:
            m = {}
        brief = state.get("brief") or ""
        # ★ M3（2026-09-17）：**集号自检已降级**。
        #   原先它是"响亮告警"：集号编译期固化 ⇒ system prompt 绑的集与 manifest 不一致
        #   就必然串集。现在产物路径**由每次开工的 `role_input` 给出**（运行期真相），
        #   system prompt 只给形状 ⇒ **一个 dev server 连续跑第 1..N 集是正常用法**，
        #   不一致不再是错误。保留一行 info 便于排障（谁在跑第几集）。
        _live_ep = int((m or {}).get("episode_index", _EP) or _EP)
        if _live_ep != _EP:
            print("[orchestrator] 本集 = 第 %d 集（起服时绑的是 %d；M3 起路径按开工契约"
                  "动态给定，两者不一致是正常的多集用法）" % (_live_ep, _EP))
        user = role_input(role, _root, m)
        # 上游就绪检查：每个角色开工时要读上游产物（开工契约），上游未落盘它就只能
        # 按自己对 brief 的理解发挥，产物必然对不上。这里在输入里明确列出**缺失的
        # 上游**，要求角色只在已有材料上工作并标注待补——温和约束，不硬拒绝
        # （保留"单独重跑某角色"的合法场景）。
        # ⚠️ 上游路径必须**按 manifest 的 live 集号**展开（不能默认 1）——否则第 2 集会
        #    拿第 1 集的上游来判"缺失/齐备"。
        missing_up = [r for r in PREREQ.get(role, [])
                      if not (_root / out_path(r, _live_ep)).exists()]
        if missing_up:
            user += ("\n\n【⚠️ 上游产物缺失（未落盘）】%s\n"
                     "处理要求：优先基于 /brief.json 与**已存在**的上游产物工作；"
                     "缺失部分**不要凭空编造细节**，在产物开头用一行标注"
                     "「待补：<缺失项>」以便下一轮补齐。" % "、".join(missing_up))
        if brief:
            user += "\n\n【任务简报（supervisor 下发；与 brief.json 冲突时以 brief.json 为准）】\n" + brief
        res = await agent.ainvoke(
            {"messages": [HumanMessage(content=user)]},
            config={"recursion_limit": ROLE_RECURSION_LIMIT})
        # 记账（复用 guards 同一套）：物化对账 → phases——supervisor 架构
        # 由此产出标准 manifest，media_gate / --resume-media 直接可用。
        try:
            m = load_manifest(_root)
            record_phase(_root, m, role)
        except Exception as e:  # noqa: BLE001
            # ★ **不再静默吞异常**（2026-09-12 实测事故）：这里原本是 `pass`，
            # 结果是 phases 没落盘、media_gate 拦住媒体链，而且**完全查不出原因**
            # （guard 的文档字符串恰好警告过同一类事故）。记账失败必须留痕。
            import traceback
            try:
                d = _root / ".tmp"
                d.mkdir(parents=True, exist_ok=True)
                with (d / "role_record_err.log").open("a", encoding="utf-8") as f:
                    f.write("[%s] %s: %s\n%s\n\n"
                            % (role, type(e).__name__, e, traceback.format_exc()))
            except Exception:  # noqa: BLE001
                pass
        return {"messages": (res.get("messages") or [])[-1:]}

    # 节点名 = **角色名**（唯一），不是固定的 `"role"`（2026-09-13 改）。
    # 依据 `langchain-dev-guide / multi-agent.md` Issue 3：
    #   「each subagent needs to be wrapped in a StateGraph with a **unique node name**,
    #     otherwise LangGraph assigns namespaces by "call order", and **reordering calls
    #     scrambles state**」
    # 当前（subagent-as-tool + `checkpointer=None`）下节点名不参与命名空间分配，
    # 所以重复名**无害** —— 但一旦要用 `get_state(subgraphs=True)` 看子代理内部状态、
    # 或在 HITL resume 时定位到具体子代理，**唯一名就是前提**。趁零成本先改掉。
    g = StateGraph(MessagesState)
    g.add_node(role, node)
    g.add_edge(START, role)
    g.add_edge(role, END)
    return g.compile()


role_worldbuilder = _build_role_graph("worldbuilder", _PACK)
role_assetdesigner = _build_role_graph("assetdesigner", _PACK)
role_plotdesigner = _build_role_graph("plotdesigner", _PACK)
role_scriptwriter = _build_role_graph("scriptwriter", _PACK)
role_dialogue = _build_role_graph("dialogue", _PACK)
role_scenedesigner = _build_role_graph("scenedesigner", _PACK)
role_reviewer = _build_role_graph("reviewer", _PACK)

# 已编译 role 图的登记表——**同步**子代理要用 `runnable` 直接内联它们。
ROLE_GRAPHS: dict[str, object] = {
    "worldbuilder": role_worldbuilder,
    "assetdesigner": role_assetdesigner,
    "plotdesigner": role_plotdesigner,
    "scriptwriter": role_scriptwriter,
    "dialogue": role_dialogue,
    "scenedesigner": role_scenedesigner,
    "reviewer": role_reviewer,
}

# ── 媒体链的唯一子代理：`media_rerender`（2026-09-13，spec §3.3）───────────────
#
# 它把"图内（director 对话）→ 图外（媒体链）"这条新通路接上：节点内调
# `pipeline.rerender()` —— 也就是 CLI `--rerender` 用的**同一个**函数。
# 于是两个入口（CLI / 对话）共用一套实现，gate / 记账 / 幂等 / 限流 / 独占锁
# 全部照常（spec §3.5：模块边界不动，只新增一条数据流）。
#
# ★ 与 7 个角色图的**一处系统性差别**：角色节点内部是"跑一个 LLM agent"，
#   而这里是**纯确定性代码**（解析简报 → 调 pipeline → 回话）。
#   理由见 `media/rerender_agent.py` 的模块 docstring：「决策交给 LLM，执行留给代码」。
media_rerender_graph = rerender_agent.build_rerender_graph(_root)


# ── supervisor：Director 主 agent ─────────────────────────────────────────────
ORCHESTRATOR_DISCIPLINE = """

【你是监制（Supervisor），不是创作者——不写任何创作产物文件
（worldbuilder/…/scenedesigner.md 一律不是你的职责）。】

调度纪律（必须遵守）：
1. 先在回复正文里按依赖序列出计划（哪几步、每步哪个角色），然后**按依赖序逐个调用
   `task(子代理名, 任务简报)`**。`task` 是**同步**调用：子代理就地跑完才返回，
   **返回即表示该角色产物已落盘**。所以——**上一次 `task` 返回之后再调下一个**。
   为什么必须按序：每个角色开工时要读上游产物（见它的开工契约），上游产物未落盘就派
   下游，下游只能按自己对 brief 的理解发挥，两边产物必然对不上。
   顺序（严格遵守）：
   - **第 0 步（派发任何角色之前）**：先把**制作规格**用 `write_file` 写到
     `/director/director.md`（片长 / 画幅 / 音频模式 / 视觉基准 —— 见你自己的 SKILL）。
     这是**你自己的**产物，不在上面那条"不许替下游角色写产物"之列。
     为什么必须在派发之前：worldbuilder 的**开工契约**把 `/director/director.md`
     列为它的上游，规格没落盘它会被告知"上游产物缺失"、只能靠 brief 猜。
   - 第 1 步：worldbuilder
   - 第 2 步：assetdesigner 与 plotdesigner——这两个互不依赖，**可以在同一条回复里
     发起两个 `task` 调用**，等两个都返回再继续
   - 第 3 步：scriptwriter
   - 第 4 步：dialogue
   - 第 5 步：scenedesigner（须等剧本 + 台词 + 资产齐）
   - 第 6 步：reviewer（最后）
2. 每次 `task` 的任务简报 = 主题、类型、主角设定、must_have 四幕、关键道具、禁忌、
   结局——逐字取自 brief.json。不要构造路径、字段或文件名（子代理已绑定项目与角色）。
3. `task` 返回 error／失败：**原样报告 ToolMessage 里的错误内容**，不要编造"已完成"，
   也不要跳过该角色往下走；同一角色连续两次失败 → 停下问用户。
4. **六步全部跑完再结束本轮回复**，报告每一步的产物路径与 reviewer 的判定。
   reviewer 返回 pass=true 才算创作链完成；否则按其 reasons **只重调被点名的那一个**角色
   （重调后仍要按依赖序把受影响的下游补齐）。
   **★ 人工打回走的是同一套动作**（2026-09-18）——见下面第 4b 条。

4b. **【人工打回】** 开启「逐步人工确认」时，每次 `task` 派发**之前**会先问用户一次
   （`interrupt_on`，信道见 `v5/hitl.py`；前端给"继续 / 打回"两个按钮）。
   如果你某次 `task` 调用的返回是一条**被拒绝**的消息（含「【人工打回】」字样）：
   · 那是**人工打回，不是派发失败** —— 不要报错、不要跳过、**不要重复派发被拒的那个**；
   · 按消息里的指示：**只重新派发被点名的那一个角色**（用 `task`），
     然后按依赖序把**它的下游重新补齐**；
   · **不要重跑它的上游**（那些用户已确认通过；重跑 = 白烧时间 + 覆盖用户认可的产物）；
   · 它的旧产物**已被移走**（进了 `.rerun_backup/`）⇒ 必须**重新写出**完整产物。
   · **例外 —— 打回的是「制作规格」**（消息里的目标是 **`director`**）：
     那不是"重派某角色"（director 是你自己、不派发）。按消息里的要求做：
     **先重写 `/director/director.md`**，然后**把 7 个角色全部重新派发**。
     规格（片长/画幅/音频模式/视觉基准）是这个片子的输入，它一变，
     下游全部作废 —— 这是用户确认过的。
   说明：`reject` 决定（中止整条链）**不会** resume，所以你不会看到它；
   你看到的任何「被拒绝」都来自**打回**。

【媒体阶段：只在"成片已存在 + 人指出具体某一镜有问题"时才用】
5. 触发条件：人看完片子说「LN03 的窗帘没全开」「LN05 没有周奶奶」这类**针对某一镜**的
   意见。派发 `task("media_rerender", 简报)`，简报**必须以头行开头**：
   `【重渲】LN03` 或 `【重渲】LN03,LN05`；头行字段**只能是镜号与 `from_still`**
   （逗号分隔），**然后换行**再写理由 —— 头行后面紧跟的其它镜号会被当成目标，
   多渲一镜就是白烧一份视频配额。
   · **必须先做一次判断：问题出在静帧还是成片？**（这一步不能省，也不能猜）
     - 画面里**多出来**东西：烧字 / 水印 / 字幕 / 多余的物件 / 动作运镜不对
       → **只重渲视频**（头行**不加** `from_still`）
     - 画面里的**人/物本身就是错的**：长相不像、衣服不对、道具缺失或错了、
       场景不对 → **头行加 `from_still`**（静帧是视频的输入，视频层改不掉）
     - 两类都像 / 拿不准 → **在回复里问人一句再派**，不要瞎猜
       （加错 = 白画一张图；不加错 = 白烧一版 3 分钟的视频。两样都比问一句贵）
   · **不要臆造镜号**：只用分镜里真实存在的 `LNxx`；不确定就先读 `/scenedesigner/scenedesigner.md`。
   · **不要用 `task` 头行外的方式暗示镜号**（例如正文里提"不像 LN05 那样"——
     出现两个镜号会被判为歧义而**拒绝执行**，那是故意的：烧错镜 = 白烧一份视频配额）。
   · **耗时 3–10 分钟**，这是正常的（内部要等视频生成 + 成片复核 + 重拼），不是卡死。
   · 返回后**如实转述**：出现 `residual` / `blocked` / `未成功` 就照原样报告，
     **不要**包装成"已修好"；`blocked` 时不要重试，先报告挡在哪道门。
6. 改**剧本/分镜**（而不是画面细节）要回到上面的角色派发，`media_rerender`
   只重渲画面，不会重跑创作链；它也**不是整片渲染入口**（整片出片走媒体链）。
"""


# ── 「方案 D」已撤销（2026-09-19）：**7 个角色一律派发，director 不再兼任世界** ──
#
# 原方案（2026-09-13 起）：`worldbuilder` 从子代理清单移除，改由 supervisor 亲自写
# `worldbuilder/worldbuilder.md`，省掉一次完整 agent loop（实测省 5:33）。
#
# ⛔ 为什么撤销（2026-09-19 实测，laofuzi-hk-retro 双集生产）：
#   ① **pack 的 per-role 覆盖被废掉一半**：产物由 director 写 ⇒ 执行的是
#      `packs/<pack>/director/SKILL.md`，而 `packs/<pack>/worldbuilder/SKILL.md`
#      永远到不了执行者。pack 机制的意义正在于 per-role 覆盖。
#   ② **同一个 system prompt 里两条相反指令**：三个包的 director SKILL 都写着
#      「不要替下游角色写产物」（laofuzi-hk-retro:66 / chinese-style:102 /
#      wool-felt:80），而 `roles.director_system_prompt` 又往同一处塞
#      「先自己把 worldbuilder 写完」。模型照**自己那份**契约交了产物：
#      标题是 director 的 `## 角色总览` **表格**，而不是 worldbuilder 契约要求的
#      `## 角色卡：<名>` 段。
#   ③ **静默失败，代价在成片**：`media/cast.parse_characters` 用
#      `^#+\s*角色卡\s*[:：]` 抽角色卡 → 表格形态**解析出 0 个角色** ⇒ 日志只报
#      「所有镜按无人物处理」，参考图不生成，一路到成片才暴露。
#   ⇒ 结论：省下的 5 分钟买来的是一个**只在成片可见**的静默故障。撤回派发。
#      第 2 集起因全剧级产物已在盘（`drive_chain.WHOLE_DRAMA`）—— 本来也不重跑。
#
# 保留这条记录是因为：它是「**提示词里两条相反指令 → 模型任选一条 → 判据静默失配**」
# 的完整样本，与 `topics/parse-robustness.md` 同型。
def make_async_subagents() -> list[AsyncSubAgent]:
    """构造 7 个角色的 AsyncSubAgent 声明。

    ★ url 必填（2026-09-12 实测定位）：
    `AsyncSubAgent.url` 缺省时走 **in-process ASGI transport**，实测在本项目的
    in-process 调用下失败——每次派发都返回
    `Failed to launch async subagent '<role>': 'NoneType' object is not callable`，
    任务从不注册（async_tasks channel 恒空）、零产物、零 trace；而弱模型会无视
    该错误 ToolMessage 继续"报告 task_id"（叠加幻觉）。
    解法：url 指向**真实的 Agent Protocol server**——本地 dev 自己就是该 server
    （`http://127.0.0.1:2024`），supervisor 经 HTTP 调 role_agent 图 → 真正的后台 run。
    生产部署时改为部署地址（env SHORTDRAMA_V5_AGENT_URL）。
    """
    url = os.environ.get("SHORTDRAMA_V5_AGENT_URL", "http://127.0.0.1:2024")
    out = [AsyncSubAgent(name=r, description=ROLE_DESCRIPTIONS[r],
                         graph_id="role_" + r, url=url)
           for r in PREREQ]
    # 媒体链的修订入口：与角色同一形态（一张注册在 langgraph.json 的图 + Agent
    # Protocol url），但**不属于依赖序**——它是"出片之后"的动作。
    out.append(AsyncSubAgent(name=MEDIA_RERENDER_NAME, description=MEDIA_RERENDER_DESC,
                             graph_id=MEDIA_RERENDER_NAME, url=url))
    return out


def make_sync_subagents() -> list[CompiledSubAgent]:
    """7 个角色声明为**同步**子代理（官方做法）。

    依据（langchain-dev-guide / `multi-agent.md` Issue 1 决策表）：
    「**主 agent 需要子 agent 的结果来决定下一步**」→ **Subagents（同步调用）**。
    我们的 8 角色是严格串行依赖链，正是这一行；异步子代理是给"独立并行子任务"用的
    （官方第 6 章通篇是"完全并行"，无串行依赖范式）。

    与异步的实质差别：
      · 同步：`task` 就地跑完 → 结果直接回到 supervisor 手里 → 它在**同一个 run 内**
        接着调下一个 → **整条链一个 run 跑完，不需要任何外部驱动**。
      · 异步：派发即返回、必须结束回合，等外部再发 run（因此**必须**有 driver）。

    源码硬约束：`runnable` 的 state schema 必须含 `messages`——role 图基于
    `MessagesState`，满足。返回值：父 agent 取 `structured_response`（非空则 JSON 化），
    否则取最后一条非空 AIMessage；**产物仍以磁盘为准**（role 自己 write_file），
    supervisor 用 FS 工具读，不依赖返回值。
    """
    return [
        CompiledSubAgent(name=r, description=ROLE_DESCRIPTIONS[r],
                         runnable=ROLE_GRAPHS[r])
        for r in PREREQ] + [
        # 媒体链修订入口（2026-09-13）：`task("media_rerender", "【重渲】LN03…")`
        CompiledSubAgent(name=MEDIA_RERENDER_NAME, description=MEDIA_RERENDER_DESC,
                         runnable=media_rerender_graph),
    ]


def _subagents():
    """子代理模式开关。

    默认 **sync**（官方做法）；设 `SHORTDRAMA_SUBAGENTS=async` 回退到旧的异步路径
    （仅作 A/B 对比与应急回退用——异步必须配一个 driver，否则链路推不完）。
    """
    mode = os.environ.get("SHORTDRAMA_SUBAGENTS", "sync").strip().lower()
    return make_async_subagents() if mode == "async" else make_sync_subagents()


def _interrupt_on() -> dict | None:
    """步级 HITL 的 `interrupt_on`（抽成函数，便于测试开关本身）。

    返回 `{"task": True}` = 每次调 `task` 派发子代理前挂起，允许全部决策类型
    （approve / edit / reject / respond，见 `HumanInTheLoopMiddleware`）。
    **默认返回 None（不开）** —— `drive_chain.py` 是全自动跑完整条链的，
    默认开启会让每条链在第一步就挂起。见 `config.APPROVE_EACH_ROLE` 与 `v5/hitl.py`。
    """
    return {"task": True} if config.APPROVE_EACH_ROLE else None


def supervisor_system_prompt(pack: str = _PACK, ep: int = _EP,
                             root: Path | None = None) -> str:
    """supervisor 的 system prompt = director 契约 + **叙事技法** + 调度纪律。

    ★ 抽成**纯函数**（2026-09-17）是为了能**直接测**它 —— 原先这段内联在
    `build_supervisor()` 里，而那个函数一调就要建 LLM 客户端，测试够不着，
    于是"M4 的 director 收不到技法"这个缺口**整整一天没被发现**（实测 jade-fish
    日志 director 注入 0 份）。**能测的契约才守得住。**
    """
    root = _root if root is None else root
    _craft = ""
    try:
        from .roles import _craft_block
        _craft = _craft_block(root, "director")
    except Exception as e:  # noqa: BLE001 —— 注入失败不该让起服失败，但**必须可见**
        print("[craft] ⚠️ director 的技法注入失败：%s" % str(e)[:160])
    return director_system_prompt(pack, ep) + _craft + ORCHESTRATOR_DISCIPLINE


def build_supervisor():
    # backend 统一：virtual_mode=True（路径穿越防护——2026-09-12 核实补上，
    # 技能明确要求；默认 False 无安全保证）。
    backend = FilesystemBackend(root_dir=str(_root), virtual_mode=True)
    return create_deep_agent(
        model=role_chat("director", 8192),
        system_prompt=supervisor_system_prompt(_PACK, _EP, _root),
        subagents=_subagents(),
        backend=backend,
        # FS 工具白名单（2026-09-12 实测修复）：不传时 FilesystemMiddleware 用默认全集
        # （含 execute / delete）→ 实测 supervisor 13 工具；传入后 13→11，只留
        # 6 文件工具 + 5 把异步遥控器（role 图本就用同一白名单，supervisor 此前漏了）。
        middleware=[FilesystemMiddleware(backend=backend, tools=FS_TOOLS)],
        # 步级 HITL（默认关）：开时每次派发子代理前 interrupt，等人 resume。
        # deepagents 会把它与 permissions 生成的 fs-interrupt 合并（用户条目优先，
        # 见 deepagents/graph.py:_merge_fs_interrupt_on）。
        interrupt_on=_interrupt_on(),
        name="director-supervisor",
    )


supervisor_graph = build_supervisor()
