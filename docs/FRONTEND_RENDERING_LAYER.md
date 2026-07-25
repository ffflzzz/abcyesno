# 前端通用渲染层方案（D）

> 配套：`LANGRAPH_CONTRACT_SPEC.md`（契约基线）、`LANGRAPH_CONTRACT_GAPS.md`、`LANGRAPH_MANJU_SAMPLE.md`。
> 目标：设计如何把**任意** LangGraph 的 state/events 渲染成**统一 UI**，使新增工作流前端零改动。

---

## 0. 设计原则（铁律）

1. **数据驱动，不分支**：前端**绝不**出现 `if (workflowId === 'manju_craft')`。所有渲染由 `manifest` + `workflow.*` 事件驱动。
2. **通用组件写一次**：`ContractForm` / `WorkflowTimeline` / `ArtifactCard` / `ApprovalGate` 是**全工作流共用**组件，不复制、不特化。
3. **复用既有通用渲染**：markdown（`MessageThread.jsx:79`）、`ToolCallCard`（`:106`）、`ApprovalDialog`（`App.jsx:411`）已通用，直接复用，不重建。
4. **零改动判据**：新增第 N 个工作流 = 新增一个 `manifest.yaml` + 指向其 graph；前端代码**不新增、不修改**任何分支。

---

## 1. 渲染层架构

```
                manifest (L1/L2/L3/L4)         workflow.* 事件 (L5)
                        │                              │
                        ▼                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │                  ContractEventBus (runId 维度)              │
   │   归一化：manifest + workflow.progress/artifact/approval     │
   └───────────┬───────────────┬───────────────┬───────────────┘
               │               │               │
        ┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐
        │ ContractForm│ │WorkflowTimeline│ │ ArtifactCard │
        │ (L2 输入)   │ │ (L5 进度)    │ │ (L3 产物)   │
        └─────────────┘ └─────────────┘ └─────────────┘
               │               │               │
               └───────┬───────┴───────┬───────┘
                       ▼               ▼
                 ChatLayout/MessageThread   ApprovalDialog(+gate)
                 （现有通用容器，复用）      （现有，仅携带 gate context）
```

---

## 2. 通用组件规格

### 2.1 `ContractForm`（L2 输入）
- **输入**：`manifest.input_schema`（JSON-Schema draft-07 + `x-ui`）。
- **行为**：按 `x-ui.control` 渲染字段（text/textarea/select/number/slider/file/multi）；客户端按 schema 做 required/类型/enum 校验；提交产出结构化 `input` 对象。
- **挂载**：用户从技能市场/侧栏选中某工作流时，Composer 区域切换为该工作流的 `ContractForm`（取代纯文本框）。自由文本工作流用退化 schema（单个 textarea），**向后兼容**当前行为。
- **落点**：新增 `src/components/ContractForm.jsx`；`ChatLayout.jsx` 的 Composer 区按"当前选中 skill 是否有 manifest"切换。
- **零改动**：一个组件服务所有工作流，无 per-workflow 分支。

### 2.2 `WorkflowTimeline`（L5 进度）
- **输入**：`workflow.progress` / `workflow.artifact` / `workflow.error` / `workflow.done` 事件流（按 `runId` 归集）。
- **渲染**：竖向时间线。节点 = 一个阶段（stage + status 标签 + 进度条 `completed/total` + message）；产物节点内嵌 `ArtifactCard`；error 节点红点。
- **数据**：不关心具体阶段名（parsing/keyframe_gen/... 都是数据），只渲染事件携带的字段。
- **落点**：新增 `src/components/WorkflowTimeline.jsx`；作为一条 assistant 消息内的区块渲染（接入 `MessageThread.jsx` 的通用消息体，与 `ToolCallCard` 平级）。
- **零改动**：阶段名/数量由事件决定，组件不写死。

### 2.3 `ArtifactCard` / `ArtifactGallery`（L3 产物）
- **输入**：`output_schema.artifacts[]` 描述符（`type`/`source`/`label`/`mime`）+ 运行期 `workflow.artifact` 事件给出的实际 `path`/`url`。
- **渲染**：按 `type` 分发——
  - `video` → `<video controls>` 播放器（本地路径走 `file://` 或 IPC 读取）
  - `image` → 单图；`image-gallery` → 网格 + 灯箱
  - `file` → 下载卡（显示 label + mime + 大小）
  - `table` → 表格；`markdown`/`text` → 现有 markdown 渲染
  - `custom-card` → 预留扩展点（极少数工作流用，仍数据驱动）
- **落点**：新增 `src/components/ArtifactCard.jsx`；被 `WorkflowTimeline` 产物节点与终态消息共同复用。
- **零改动**：switch on `type`，无 per-workflow 分支。

### 2.4 `ApprovalGate`（L4 审批，复用现有）
- **现状**：`ApprovalDialog`（`App.jsx:411`）+ `approval-banner`（`ChatLayout.jsx:124`）+ `hermes.respondApproval`（`App.jsx:655`）已通用可用。
- **改动**：仅让 `workflow.approval` 事件携带 `gate_id`/`label`/`context`，透传给现有 `ApprovalDialog` 显示；确认走既有 `approval.respond`。**不新建审批 UI**。
- **零改动**：审批 UI 已通用，仅接线 gate context。

---

## 3. 事件接入：`useContractEvents`

- **职责**：订阅 AG-UI SSE 的 `CUSTOM` 事件（name=`workflow.*`），归一化后写入 `ContractEventBus`（按 `runId`/`threadId` 分桶）。
- **实现**：轻量封装 `@ag-ui/client`（已装 `^0.0.57`）的 HttpAgent/SSE 订阅，或在现有 `agui-server.js` 的 SSE 消费侧解析 CUSTOM 事件。与 CopilotKit 消息流解耦，避免污染 `useCopilotChatInternal` 的 messages。
- **落点**：新增 `src/hooks/useContractEvents.js` + 简易 `eventBus`（可用 `useSyncExternalStore` 或 context）。
- **消费方**：`WorkflowTimeline`、`ArtifactCard`、`ApprovalGate` 订阅 bus。

---

## 4. 与现有代码的接线点

| 现有文件 | 改动 | 说明 |
|----------|------|------|
| `App.jsx:680-698` CopilotKit provider | 增 `properties.manifest` 透传（可选） | 让子组件拿当前 skill 的 manifest |
| `ChatLayout.jsx:46` handleUpload / `:65` handleSend | Composer 区按 manifest 切换为 `ContractForm` | 输入侧接管 |
| `MessageThread.jsx:79,106` | 消息体内新增 `WorkflowTimeline` / `ArtifactCard` 区块（与 `ToolCallCard` 平级） | 进度/产物渲染 |
| `SkillPanel.jsx:4-5` / `MarketPanel.jsx:4-5` | 删硬编码 `SKILL_HINTS`，改消费 `/info` 的 manifest | 发现层去硬编码 |
| `App.jsx:411` ApprovalDialog | 接收 `workflow.approval` 的 gate context | 审批接线 |

> 注意：上述"改动"是**新增通用能力 + 删硬编码**，不是新增 per-workflow 分支。删掉的 `SKILL_HINTS` 恰恰是违反契约的硬编码。

---

## 5. 数据流端到端（以 manju_craft 为例）

```
用户选「漫剧生成」
  → ChatLayout 切到 ContractForm（script 文本框 + style 下拉）
用户填 script 提交
  → ContractForm 校验 → envelope {agent_name, thread_id, input:{script,style}}
  → AG-UI SSE RUN_STARTED
  → workflow.progress(parsing)        → WorkflowTimeline 节点1
  → workflow.progress(keyframe_gen, 2/8) → 节点2 + 进度条
  → workflow.artifact(video, path)    → 节点3 内嵌 ArtifactCard(<video>)
  → [若声明] workflow.approval(confirm_publish) → ApprovalDialog（复用）
  → TEXT_MESSAGE_CONTENT(摘要)         → markdown 渲染
  → RUN_FINISHED
```
全程**无任何 `manju_craft` 专属 UI 代码**。加 `image_gen` 工作流时，仅换 manifest（不同 input/output/进度文案），组件零改。

---

## 6. 验收（前端侧）

- [ ] `ContractForm` 按任意 manifest 渲染表单，校验通过才提交
- [ ] `WorkflowTimeline` 按任意 `workflow.*` 事件渲染时间线，不依赖阶段名
- [ ] `ArtifactCard` 按 `type` 渲染视频/图/文件/表
- [ ] `ApprovalDialog` 能显示 workflow 声明的 gate context
- [ ] 全树 `grep "workflowId ==="` / 技能名硬编码 = 0
- [ ] 新增第 3 个工作流，前端零改动即可完整渲染（铁证）

---

## 7. 与 UI_UX_SPEC 的对齐
- 令牌/主题/reduced-motion 已按 `UI_UX_SPEC.md` 收口（见 `ACCEPTANCE.md` §11），通用组件沿用同一套语义令牌，不引入新色板。
- 图标：`ArtifactCard`/`WorkflowTimeline` 图标走 Phosphor（UI_UX_SPEC §11.4 P2），不手搓 SVG。
