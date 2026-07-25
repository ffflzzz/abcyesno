# manju_craft 接入样本验证（C）

> 配套：`LANGRAPH_CONTRACT_SPEC.md`、`LANGRAPH_CONTRACT_GAPS.md`。
> 目标：以 manju_craft 为第一个接入样本，验证契约能否让工作流**即插即跑、前端零改动**。

---

## 1. manju_craft 现状速写（实测）

- **目录**：`hermes-fork/skills/langgraph_agents/agents/manju_craft/`
- **入口**：`agent.py`（`build_graph()` + `build_initial_state()`）
- **图**：`graph/graph.py` — 线性 11 节点管道：
  `parse_script → generate_characters → batch_generate_keyframes → consistency_check → fix_drift → batch_generate_video → generate_tts → merge_and_concat → generate_jianying_draft → finalize`
- **状态**：`graph/state.py` `AgentState`（TypedDict），含 `status`/`total_shots`/`completed_shots` 等
- **输入**：自由文本 `input:str` → `build_initial_state` 映射为 `script`/`api_key`/`project_name` + 内部初值
- **输出**：写 `~/.manjucraft/projects/<name>/` 的 `final.mp4`、`draft_content.json`、`assets.zip`；终态经 `_summarize_state` 摘要 + `state` 字典回传
- **审批**：无 `interrupt`；`stop_requested` 是死字段
- **进度**：`AgentState.status` 内部更新，但 `graph.invoke` 同步跑完，**不对外暴露**

---

## 2. 逐层对照契约（L1-L5）

### L1 发现 Manifest — 缺失
- 现状：前端靠 `MarketPanel.jsx:4-5` 硬编码 `SKILL_HINTS` 显示"用 langgraph_agent 调用 manju_craft"。
- 契约要求：新增 `agents/manju_craft/manifest.yaml`（id/name/description/category/icon/version/entry/runtime/input_schema/output_schema/capabilities/approval_gates/progress_events）。
- 改动：纯新增文件 + adapter 读它 + 前端消费 `/info`。**前端渲染零改**。

### L2 输入 Schema — 隐含未声明
- 现状：`input` 是自由文本；`script/style/project_name` 隐含在 `build_initial_state`。
- 契约要求：manifest 声明
  ```yaml
  input_schema:
    type: object
    properties:
      script: { type: string, minLength: 1, x-ui: { control: textarea, label: 脚本 } }
      style: { type: string, enum: [写实, 二次元, 3D], default: 二次元, x-ui: { control: select, label: 风格 } }
      project_name: { type: string, x-ui: { control: text, label: 项目名(可选) } }
    required: [script]
  ```
- 改动：声明 schema（adapter 校验）；前端用通用 `ContractForm` 渲染（**新组件写一次，非 per-workflow**）。当前"文本框"行为由退化 schema 向后兼容保留。

### L3 输出渲染 — 路径裸露
- 现状：`final.mp4` 等以**绝对路径字符串**出现在 tool-result 文本，前端当文本渲染。
- 契约要求：manifest `output_schema.artifacts` 声明 3 个产物（video / file-json / file-zip）；adapter 把路径封装为 `workflow.artifact` 事件（带 `type`）。
- 改动：adapter 封装（小）+ 前端新增通用 `ArtifactCard`（写一次）。**无 per-workflow 分支**。

### L4 审批门 — 无
- 现状：无 interrupt；`stop_requested` 死字段。
- 契约要求：若需"生成前确认"，声明 `approval_gates: [{id: confirm_generate, ...}]` 并用 `interrupt()` 触发。
- 改动（可选，首版可省）：adapter 翻译 interrupt→approval；前端 `ApprovalDialog` 已具备。

### L5 进度事件 — 未暴露（最大缺口）
- 现状：`graph.invoke()` 同步；`AgentState.status` 内部更新但前端只见一个不透明 `langgraph_agent` 工具调用。
- 契约要求：`graph.astream_events()` + 每节点 `emit_progress(stage, status, completed, total)` → adapter 转 `workflow.progress`/`workflow.artifact` → 前端 `WorkflowTimeline`。
- 改动（最大）：`langgraph_runtime._invoke_graph` 改流式；manju_craft 各节点发 progress。adapter 加事件分支。前端新增 `WorkflowTimeline`（写一次）。

---

## 3. 验证结论：能否即插即跑、前端零改动？

### 3.1 渲染侧零改动 —— 今日已成立
前端渲染本就通用（markdown + `ToolCallCard` + `ApprovalDialog`），**无 per-workflow 分支**。因此 manju_craft 当前"跑起来 + 文本结果"已能在不碰前端的情况下显示。这证明了"渲染零改动"的前提。

### 3.2 契约化（声明式 + 类型化）—— 需补 adapter + manifest
要让 manju_craft 成为**合格契约样本**（产物可见、进度可见、输入表单化），缺口全在 **adapter（Hermes 侧）+ 新增 manifest**，前端只需新增**通用**组件：

| 层 | 前端是否需改 | 说明 |
|----|--------------|------|
| L1 | 否（仅换数据源） | 删硬编码 SKILL_HINTS，读 `/info` |
| L2 | 否（新增通用 ContractForm） | 一个组件服务所有工作流 |
| L3 | 否（新增通用 ArtifactCard） | 一个组件服务所有工作流 |
| L4 | 否（ApprovalDialog 已存在） | 仅携带 gate context |
| L5 | 否（新增通用 WorkflowTimeline） | 一个组件服务所有工作流 |

**结论**：manju_craft 验证了契约的"零前端改动"命题——所有差异被收敛为**数据**（manifest + 事件），前端只写**一套通用渲染器**。缺口集中在 adapter（执行侧流式化 + 产物/审批翻译），不在前端。

### 3.3 铁证测试（建议下一步做）
新增第 3 个工作流（如 `image_gen`：输入 prompt+尺寸，输出图片画廊，带"生成前确认"审批门 + 进度），**仅交付 manifest + graph**，不碰任何前端文件。若 `ContractForm`/`WorkflowTimeline`/`ArtifactCard`/`ApprovalDialog` 能正确渲染它，则契约成立、零改动被实证。

---

## 4. manju_craft manifest（契约样本，待落地）

```yaml
id: manju_craft
name: 漫剧生成
description: 根据脚本生成竖屏漫剧视频，并导出剪映草稿
category: media
icon: film
version: 1.0.0
entry: agents/manju_craft/agent.py
runtime: inprocess
input_schema:
  type: object
  properties:
    script:
      type: string
      minLength: 1
      x-ui: { control: textarea, label: 脚本, placeholder: 描述你要的漫剧情节... }
    style:
      type: string
      enum: [写实, 二次元, 3D]
      default: 二次元
      x-ui: { control: select, label: 风格 }
    project_name:
      type: string
      x-ui: { control: text, label: 项目名(可选) }
  required: [script]
output_schema:
  summary: markdown
  artifacts:
    - { id: video,    type: video, source: path, label: 成片 }
    - { id: jianying, type: file,  mime: application/json, label: 剪映草稿 }
    - { id: assets,   type: file,  mime: application/zip,  label: 素材包 }
capabilities: [video-generation, script-to-video]
approval_gates:
  - { id: confirm_generate, label: 确认开始生成, stage: pre-run, risk: medium }
progress_events: [step_started, step_progress, artifact_produced]
```

## 5. 落地 manju_craft 的最小改动清单
1. 新增 `agents/manju_craft/manifest.yaml`（上）
2. `langgraph_runtime.discover_agents()` 读 manifest（:173）
3. `langgraph_runtime._invoke_graph` 改 `astream_events`，map `status`→`workflow.progress`（:284）
4. `_summarize_state` 路径→`workflow.artifact`（:242）
5. `agui-server.js` `/info` 返 manifest（:61）、translator 加 `workflow.*`（:132-364）
6. 前端新增 `ContractForm`/`WorkflowTimeline`/`ArtifactCard`/`useContractEvents`（通用，写一次）
7. 删 `SkillPanel.jsx:4-5`/`MarketPanel.jsx:4-5` 硬编码 `SKILL_HINTS`
