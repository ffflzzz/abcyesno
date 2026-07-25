# LangGraph 工作流接入契约 — 逐层缺口分析（B）

> 配套：`LANGRAPH_CONTRACT_SPEC.md`（契约基线）。本文基于实测代码逐层标出契约当前缺口：**缺什么、改哪**。
> 调研事实来源：CopilotKit 前端集成、`electron/backend/agui-server.js`、Hermes `langgraph-agents` skill / `langgraph_runtime.py` / `langgraph_agent_tool.py`、manju_craft 工作流。

---

## 0. 现状一句话

前端渲染**本就通用**（markdown + 通用 `ToolCallCard` + 已有 `ApprovalDialog`），**没有 per-workflow 分支**。所以"加工作流前端零改动"的**渲染侧前提已具备**。真正缺的是 **adapter 层**：把 LangGraph 工作流的"输入/产物/进度/审批"翻译成前端能通用消费的数据。当前这些要么不存在（进度流、产物类型化、审批门），要么靠 `agui-server.js` 硬编码 prompt 改写（输入路由）。

---

## L1 发现 Manifest — 缺口

| 项 | 现状 | 缺什么 | 改哪（file:line） |
|----|------|--------|-------------------|
| 前端目录数据 | 硬编码 `SKILL_HINTS` | 无机器可读 manifest | `SkillPanel.jsx:4-5`、`MarketPanel.jsx:4-5` |
| 后端发现 | `discover_agents()` 仅扫 `agent.py` | 不读 `manifest.yaml` | `hermes-fork/skills/langgraph_agents/langgraph_runtime.py:173` |
| /info 端点 | 返回 agent 列表元数据 | 不返回完整 manifest（input/output/approval） | `electron/backend/agui-server.js:61` (`GET /api/ag-ui/run/info`) |
| 技能↔Hermes 映射 | 写死 `manju-craft → langgraph-agents` | 应由 manifest 的 `id`/`entry` 驱动 | `agui-server.js:112` |

**工作量**：中。后端加 manifest 读取 + 扩展 `/info`；前端把 `SKILL_HINTS` 换成消费 `/info`。

---

## L2 输入 Schema — 缺口

| 项 | 现状 | 缺什么 | 改哪（file:line） |
|----|------|--------|-------------------|
| 工具信封 | `{agent_name, input, thread_id}`，`input` 为自由文本 | 无结构化/校验 | `hermes-fork/tools/langgraph_agent_tool.py:96-114` |
| manju_craft 输入 | `build_initial_state(input_text)` 把自由文本映射为 `AgentState` | 不暴露字段 schema（script/style/project_name 隐含） | `manju_craft/agent.py:208` |
| 路由/改写 | 命中 `manju-craft` 或 `looksLikeVideoTask` 时注入 `langgraph_agent` 调用 | 硬编码 prompt 模板，非 manifest 驱动 | `agui-server.js:482,489-517` |
| 前端表单 | 仅通用 Composer 文本框 | 无按 schema 动态生成的 `ContractForm` | 需新增（见 FRONTEND_RENDERING_LAYER.md） |

**工作量**：中。补 manifest `input_schema` + adapter 校验；新增 `ContractForm`（通用，写一次）。

---

## L3 输出渲染 — 缺口

| 项 | 现状 | 缺什么 | 改哪（file:line） |
|----|------|--------|-------------------|
| 产物落地 | 写 `~/.manjucraft/projects/<name>/`（final.mp4 / draft_content.json / assets.zip） | OK | `manju_craft/nodes/merge_and_concat.py:26,31`、`generate_jianying_draft.py:26` |
| 产物回传 | 仅绝对路径字符串塞进 tool-result 文本 | 无 `type` 化产物描述 | `langgraph_runtime.py:242` (`_summarize_state`)、`:284` (`_invoke_graph`) |
| 前端渲染 | 通用 markdown + `ToolCallCard`，无视频/文件卡 | 路径当文本显示，无播放器/下载卡 | `MessageThread.jsx:79,106`；`@copilotkit/react-ui` 已装未用 |
| 产物通道 | 无专用 artifact 事件 | 需 `workflow.artifact` CUSTOM 事件 | adapter `createTurnTranslator` (`agui-server.js:132-364`) |

**工作量**：中。adapter 把路径封装为带 `type` 的 `workflow.artifact`；前端新增通用 `ArtifactCard`（写一次）。

---

## L4 审批门 — 缺口

| 项 | 现状 | 缺什么 | 改哪（file:line） |
|----|------|--------|-------------------|
| 工作流审批 | 全工作流无 `interrupt()`/`Command(resume)` | 无步骤级 human-in-the-loop | `grep` 确认 langgraph_agents skill 内 0 命中 |
| manju_craft | `stop_requested` 字段定义但**无任何节点读取**（死字段） | 未接线 | `manju_craft/graph/state.py`（`stop_requested`）、各 node 未读 |
| Hermes 通用审批 | 已有 `approval.request`/`approval.respond` + `ApprovalDialog` | 仅用于危险 shell 命令，未接到工作流步骤 | `main.js:131`、`App.jsx:411`（ApprovalDialog）、`App.jsx:655`（respond） |
| 契约化触发 | 无 `request_approval(gate_id)` 标准入口 | 需 adapter 把 `interrupt`/`request_approval` 转 `approval.request` | adapter（见 SPEC §9） |

**工作量**：中。adapter 翻译 interrupt→approval；前端 `ApprovalDialog` 已具备，仅需携带 gate context。

---

## L5 进度事件通道 — 缺口（最关键）

| 项 | 现状 | 缺什么 | 改哪（file:line） |
|----|------|--------|-------------------|
| 执行方式 | `graph.invoke()` 同步跑完才返回 | 无流式、无回调 | `langgraph_runtime.py:284` (`_invoke_graph`) |
| 内部状态 | `AgentState.status` 有更新（parsing/keyframe_gen/consistency_check/merging/done） | 完全未对外暴露 | `manju_craft/graph/state.py:73`；各 node 更新 `status` |
| 计数器 | `total_shots/completed_shots/current_shot_index` 可支撑时间线 | 未流式 | `state.py` |
| 前端可见 | 仅一个不透明 `langgraph_agent` 工具调用（TOOL_CALL_START…END） | 无 `workflow.progress` 事件 | `agui-server.js` translator 映射 `tool.start/complete` 为单个 TOOL_CALL |
| 事件通道 | AG-UI 已支持 CUSTOM 事件；translator 未用 | 需加 `workflow.*` 分支 | `agui-server.js:132-364` (`createTurnTranslator`)；前端需新增 `useContractEvents` |

**工作量**：高（执行侧改动最大）。`graph.invoke` → `graph.astream_events`；每节点 `emit_progress`；adapter 翻译为 `workflow.progress`/`artifact`。

---

## Adapter 层 — 缺口汇总（缺的这一层）

adapter 落在 Hermes 侧，两个承载点：

1. **`electron/backend/agui-server.js`**（AG-UI ↔ Hermes gateway 桥）
   - `:61` `/info` 返回完整 manifest
   - `:132-364` `createTurnTranslator` 增加 `workflow.*` CUSTOM 事件分支
   - `:482,489-517` prompt 改写 → 改由 manifest `input_schema` 驱动的结构化注入
   - `:112` 技能映射 → 由 manifest `id`/`entry` 驱动

2. **`hermes-fork/skills/langgraph_agents/langgraph_runtime.py`**（工作流执行）
   - `:173` `discover_agents()` 读 `manifest.yaml`
   - `:284` `_invoke_graph` `invoke` → `astream_events`（流式进度）
   - `:242` `_summarize_state` 路径 → 带 `type` 的 `workflow.artifact`
   - 新增：捕获 `interrupt()` → 转 `approval.request`

3. **`hermes-fork/tools/langgraph_agent_tool.py`**（工具信封）
   - `:96-114` schema `input` 从自由文本升级为对象（符合 `input_schema`）

**前端改动总量**：仅**新增**通用组件 `ContractForm` / `WorkflowTimeline` / `ArtifactCard` + `useContractEvents`。**不修改任何现有 per-workflow 逻辑**（当前也没有）。这正是"零改动"可成立的根。

---

## 缺口优先级（建议落地顺序）

| 顺序 | 缺口 | 理由 |
|------|------|------|
| 1 | L1 manifest + /info | 无 manifest 其他层无法被发现；改动小、收益大 |
| 2 | L2 输入 schema + ContractForm | 让"表单"声明式，直接消除硬编码 SKILL_HINTS |
| 3 | L3 产物封装 + ArtifactCard | 视频/文件可见，manju_craft 价值立刻可感 |
| 4 | L5 进度流（astream_events） | 改动最大但体验提升最显著 |
| 5 | L4 审批门 | 按需；manju_craft 首版可先无审批门 |
