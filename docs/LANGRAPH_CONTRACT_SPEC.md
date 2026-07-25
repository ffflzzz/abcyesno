# Abcyesno LangGraph 工作流接入契约 Spec（v1 基线）

> 状态：v1 基线（platform interface baseline）。取代 `docs/LANGRAPH_CONTRACT.md` 草案。
> 范围：规定任意 LangGraph 工作流接入 Abcyesno 前端的统一契约。这是平台"不反复写前端"承诺的接口基线。
> 配套文档：`LANGRAPH_CONTRACT_GAPS.md`（逐层缺口）、`LANGRAPH_MANJU_SAMPLE.md`（manju_craft 接入样本）、`FRONTEND_RENDERING_LAYER.md`（前端通用渲染层方案）。

---

## 0. 范围与目标

### 0.1 为什么需要这份契约
Abcyesno 的双重定位（lex 明确）：
1. 验证对 agent 工程的理解；
2. 作为 lex 自用平台：未来大量"问题专属"的 LangGraph agent / workflow 都跑在这套前端上，**不再反复写前端**。

"不反复写前端"成立的前提是平台定义**一套统一的 LangGraph 工作流接入契约**。没有这层契约，每加一个新工作流仍要定制 UI。本 Spec 即该契约的权威基线。

### 0.2 核心命题
> 一个工作流要"即插即跑、前端零改动"，当且仅当它满足：其**全部差异**（输入输出、进度、审批、产物）都能被描述为**数据**（manifest + 事件流），前端只写一套**通用渲染器**消费这些数据。

### 0.3 验收铁证（定义即闭环）
新增第 N 个工作流时，若前端代码**零改动**（仅新增一个 manifest + 指向其 graph），则契约成立。manju_craft 为第一个参考样本，hello_agent 为最小对照。

---

## 1. 三层栈与 adapter 定位

```
┌─────────────────────────────────────────────────────────────┐
│ CopilotKit 前端  = 通用渲染层                                  │
│   消费 manifest + 进度事件 + 产物描述，渲染统一 UI             │
│   不每工作流改代码                                             │
└───────────────┬─────────────────────────────────────────────┘
                │ AG-UI 协议 (SSE)
┌───────────────▼─────────────────────────────────────────────┐
│ ADAPTER（缺的这一层，落在 Hermes 侧）                          │
│   ① manifest 发现 + 注入 /info                                │
│   ② LangGraph 运行生命周期 invoke→astream→result              │
│      翻译成 AG-UI 事件流（进度/产物/审批）                     │
│   ③ 审批透传 human-in-the-loop（interrupt → approval.request） │
│   承载点：electron/backend/agui-server.js + hermes-fork        │
│            skills/langgraph_agents/langgraph_runtime.py       │
└───────────────┬─────────────────────────────────────────────┘
                │ Hermes gateway (JSON-RPC over WebSocket)
┌───────────────▼─────────────────────────────────────────────┐
│ Hermes 后端  = 执行运行时                                      │
│   loop + tools/skills/memory/gateway                          │
│   承载 langgraph_agent 工具 + langgraph-agents skill          │
└───────────────┬─────────────────────────────────────────────┘
                │ 进程内 import graph / 或 LangGraph Platform URL
┌───────────────▼─────────────────────────────────────────────┐
│ LangGraph  = 复杂编排层（controller）                          │
│   产出可被契约加载的工作流（agent.py: build_graph/build_initial_state）│
└─────────────────────────────────────────────────────────────┘
```

**事实缺口**：CopilotKit / Hermes 现有接口**未直接暴露**下面 5 层契约。需要 adapter 桥接。adapter 不另起服务，落在 Hermes 侧（现有 `langgraph-agents` skill 与 `agui-server.js` 即天然承载点）。

---

## 2. 契约总览（5 层 + 信封）

| 层 | 名称 | 作用 | 数据形态 | 前端消费 |
|----|------|------|----------|----------|
| L1 | 发现 Manifest | 让前端"知道有哪些工作流、各长什么样" | `manifest.yaml/json` | 技能市场 / 表单 / 时间线标题 |
| L2 | 输入 Schema | 让前端"动态生成输入表单"，不写组件 | JSON-Schema + `x-ui` | `ContractForm` |
| L3 | 输出渲染 | 让前端"通用渲染产物"（视频/图/文件/表） | `output_schema.artifacts[]` | `ArtifactCard` |
| L4 | 审批门 | 让前端"通用弹审批"，不每工作流特制 | `approval_gates[]` + 事件 | `ApprovalDialog` |
| L5 | 进度事件通道 | 让前端"通用渲染时间线"，不写专属进度 UI | 标准事件流 | `WorkflowTimeline` |
| — | 调用信封 | 统一 input→事件流→result 的传输格式 | 见 §8 | adapter 翻译 |

**铁律**：前端**绝不**出现 `if (workflowId === 'xxx')` 这类分支。所有渲染由 manifest + 事件**数据驱动**。

---

## 3. L1 发现 Manifest

每个工作流在其目录暴露一个 `manifest.yaml`（或 `manifest.json`）。adapter 的 `discover_agents()` 扫描并返回给前端 `/api/ag-ui/run/info`。

### 3.1 字段规范
```yaml
id: manju_craft                 # 唯一，kebab-case，匹配 langgraph_agent 的 agent_name
name: 漫剧生成                   # 展示名
description: 根据脚本生成竖屏漫剧视频（剪映草稿）  # 一句人类可读
category: media                 # general | media | research | dev | office
icon: film                      # Phosphor 图标名（见 UI_UX_SPEC §11.4 P2）
version: 1.0.0                  # 语义化版本
entry: agents/manju_craft/agent.py   # adapter 如何加载（相对 skills/langgraph_agents/）
runtime: inprocess              # inprocess | langgraph-platform-url
input_schema:  { ... }          # 见 §4
output_schema: { ... }          # 见 §5
capabilities: [video-generation, script-to-video]   # 路由/搜索/市场筛选
approval_gates: [ ... ]         # 见 §6，可空
progress_events: [step_started, step_progress, artifact_produced]  # 声明支持的 L5 事件
```

### 3.2 发现协议
- adapter 在 `langgraph_runtime.discover_agents()` 中除扫描 `agent.py` 外，额外读取同目录 `manifest.yaml`，合并为 `AgentManifest`。
- `agui-server.js` 的 `GET /api/ag-ui/run/info` 现仅返回 agent 列表元数据；**改为返回完整 manifest 列表**（id/name/description/input_schema/output_schema/approval_gates 等）。
- 前端 `SkillPanel`/`MarketPanel` 改为消费 `/info` 的 manifest，删除硬编码 `SKILL_HINTS`。

---

## 4. L2 输入 Schema

用 **JSON-Schema draft-07** 描述输入，附加 `x-ui` 控件提示（不发明新格式，最大化复用校验生态）。

### 4.1 控件映射（x-ui.control）
| control | 渲染 | 绑定 JSON 类型 |
|---------|------|----------------|
| `text` | 单行输入 | string |
| `textarea` | 多行输入 | string |
| `file` | 文件选择（走现有 upload-file IPC） | string(path) |
| `select` | 下拉（enum） | string |
| `number` | 数字输入 | number |
| `slider` | 滑块（min/max/step） | number |
| `multi` | 多选（enum 数组） | array |

### 4.2 示例（manju_craft）
```yaml
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
```
> 注：自由文本工作流（hello_agent）的 schema 退化为 `{ type: object, properties: { input: { type: string, x-ui: { control: textarea } } }, required: [input] }`，前端用 `ContractForm` 渲染一个文本框，**向后兼容**当前"输入即提示词"行为。

### 4.3 校验责任
adapter 在调用 `langgraph_agent` 前按 schema 校验（required / 类型 / enum），失败返回结构化 `workflow.error` 而非含糊文本。

---

## 5. L3 输出渲染

工作流产出通过 `output_schema.artifacts[]` 声明，前端通用渲染。

### 5.1 产物描述符
```yaml
output_schema:
  summary: markdown            # 终态文本摘要的渲染方式
  artifacts:
    - { id: video,       type: video, source: path, label: 成片 }
    - { id: jianying,    type: file,  mime: application/json, label: 剪映草稿 }
    - { id: assets,      type: file,  mime: application/zip,  label: 素材包 }
```
`type` 取值：`text | markdown | image | image-gallery | video | audio | file | table | custom-card`。
`source` 取值：`path`（本地绝对路径，前端经 `file://` 或 IPC 读取）| `url` | `inline`。

### 5.2 前端渲染（数据驱动）
`ArtifactCard` 按 `type` 分发：video→`<video>` 播放器、image-gallery→网格灯箱、file→下载卡（带 mime/label）、table→表格。
> 现状缺口：当前产物只是 tool-result 文本里的**绝对路径字符串**，前端仅当文本渲染。契约要求 adapter 把路径封装为带 `type` 的产物事件（见 §7），前端据此渲染，而非裸露路径。

---

## 6. L4 审批门

工作流可声明式标出哪些步骤前需人确认（生成前、外部发布前、破坏性操作前）。

### 6.1 声明
```yaml
approval_gates:
  - { id: confirm_generate, label: 确认开始生成, stage: pre-run, risk: medium }
  - { id: confirm_publish,  label: 确认发布到平台, stage: post-run, risk: high }
```

### 6.2 触发协议（二选一）
- **A（推荐，LangGraph 原生）**：工作流用 `interrupt(gate_context)` 挂起；adapter 捕获 `Command(resume)` 信号，翻译为 Hermes 既有 `approval.request` → 前端 `ApprovalDialog` → `approval.respond` → `Command(resume)`。
- **B（轻量）**：工作流调用标准 `request_approval(gate_id, context)`，adapter 拦截并转 `approval.request`。

> 现状缺口：Hermes 已有通用 `approval.request`/`approval.respond` + 前端 `ApprovalDialog`，但**仅用于危险 shell 命令**，未接到工作流步骤。manju_craft 的 `stop_requested` 是死字段。契约把审批门标准化为工作流可声明、adapter 可翻译的能力。

---

## 7. L5 进度事件通道（最关键）

规定工作流必须吐**标准事件流**。前端只渲染这条时间线，不写专属进度 UI。正好接现有 AG-UI 流。

### 7.1 事件类型（contract 标准集）
| 事件 | payload | 前端渲染 |
|------|---------|----------|
| `workflow.progress` | `{ stage, status, completed, total, message }` | 时间线节点 + 进度条 |
| `workflow.artifact` | `{ artifact_id, type, path, label }` | 时间线节点 + 产物卡 |
| `workflow.approval` | `{ gate_id, label, context }` | 时间线节点 + 弹窗联动 |
| `workflow.error` | `{ stage, message }` | 时间线红点 + 错误横幅 |
| `workflow.done` | `{ summary }` | 时间线完成态 |

### 7.2 承载方式（AG-UI）
上述事件以 **AG-UI `CUSTOM` 事件**（`name` + `value`）经 SSE 下发。adapter 的 `createTurnTranslator` 增加分支，把 LangGraph 流 / Hermes 事件翻译为这些 `CUSTOM` 事件。前端新增 `useContractEvents` 监听，喂给 `WorkflowTimeline`。

### 7.3 工作流侧要求
- `graph.invoke` 改为 `graph.astream_events()`（或 `astream` + 自定义 `on_custom_event`）。
- 每个节点用 `emit_progress(stage, status, completed, total)` 标准回调发 `workflow.progress`。
- 产物落盘后发 `workflow.artifact`。

> 现状缺口：当前 `langgraph_runtime._invoke_graph` 用 `graph.invoke()` **同步跑完才返回**，无回调、无流式。manju_craft 内部虽有 `AgentState.status` 更新（parsing/keyframe_gen/.../done），但**完全未对外暴露**。需改 `langgraph_runtime.py` 为 `astream_events` 并把 `status` 映射为 `workflow.progress`。

---

## 8. 调用信封（input → 事件流 → result）

### 8.1 发起（前端 → adapter）
```json
{
  "agent_name": "manju_craft",
  "thread_id": "manju-demo-1",
  "input": { "script": "一只小猫在草地上玩耍", "style": "二次元" }
}
```
adapter 校验 `input` 符合 `input_schema` 后，转成 `langgraph_agent` 工具调用（现有信封 `{agent_name, input, thread_id}` 保留，`input` 从自由文本升级为对象）。

### 8.2 流（adapter → 前端，AG-UI SSE）
```
RUN_STARTED
TEXT_MESSAGE_START ...
  (workflow.progress: parsing)
  (workflow.progress: keyframe_gen, completed 2/8)
  (workflow.artifact: video)
  (workflow.approval: confirm_publish)   # 若声明
TEXT_MESSAGE_CONTENT ...
TEXT_MESSAGE_END
RUN_FINISHED
```
括号内为 `CUSTOM` 事件，夹在 AG-UI 文本流中。

### 8.3 结果（adapter → 前端）
终态摘要走 `TEXT_MESSAGE_CONTENT`（`output_schema.summary`）；产物走 `workflow.artifact` 事件 + `ArtifactCard` 渲染。不再把路径裸塞进 tool-result 文本。

---

## 9. Adapter 规范（Hermes 侧职责）

| 职责 | 当前位置 | 改动 |
|------|----------|------|
| manifest 发现 + /info 返回完整 manifest | `langgraph_runtime.discover_agents()` + `agui-server.js:61` | 读 `manifest.yaml`，扩展 `/info` 响应 |
| 输入校验 + 信封翻译 | `agui-server.js` prompt 改写（:489-517） | 用 `input_schema` 校验，结构化注入 |
| 进度流翻译 | `agui-server.js createTurnTranslator`（:132-364） | 加 `workflow.*` CUSTOM 事件分支 |
| 审批透传 | `main.js:131` approval 桥 | 接 `interrupt/request_approval` → `approval.request` |
| 产物封装 | `langgraph_runtime._summarize_state`（:242） | 路径 → 带 type 的 `workflow.artifact` |
| 流式执行 | `langgraph_runtime._invoke_graph`（:284） | `graph.invoke` → `graph.astream_events` |

> 前端改动：仅**新增**通用组件 `ContractForm`/`WorkflowTimeline`/`ArtifactCard` + `useContractEvents`，**不改任何现有 per-workflow 分支**（因为当前也没有，渲染本就通用）。

---

## 10. 版本与兼容

- 契约版本随本 Spec：`contract: 1`。manifest 带 `version` 字段。
- 破坏性变更（改 L1-L5 字段语义）需 `contract` 主版本 +1，前端按 `contract` 字段选择渲染策略。
- 工作流 `version` 与契约版本解耦。

---

## 11. 合规检查表（workflow 是否契约就绪）

- [ ] 有 `manifest.yaml`（L1 全字段）
- [ ] `input_schema` 覆盖真实输入，required 正确（L2）
- [ ] `output_schema.artifacts` 覆盖全部产物（L3）
- [ ] 若需人确认，声明 `approval_gates` 且能被 `interrupt` 触发（L4）
- [ ] 改用 `astream_events` 并发 `workflow.progress`/`artifact`（L5）
- [ ] 信封符合 §8（input 为对象，产物走事件非裸路径）
- [ ] 前端**零改动**即可跑（铁证）

---

## 附录 A：manju_craft manifest 示例
见 `LANGRAPH_MANJU_SAMPLE.md`。

## 附录 B：hello_agent manifest 示例
```yaml
id: hello_agent
name: 问候助手
description: 最小 LangGraph 示例，验证契约最小接口
category: general
icon: chat
version: 1.0.0
entry: agents/hello_agent/agent.py
runtime: inprocess
input_schema:
  type: object
  properties:
    input: { type: string, x-ui: { control: textarea, label: 输入 } }
  required: [input]
output_schema:
  summary: markdown
  artifacts: []
capabilities: [chat]
approval_gates: []
progress_events: []
```
