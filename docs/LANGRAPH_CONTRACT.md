# LangGraph 工作流接入契约（Contract v1, DRAFT）

> 状态：草案。作者：von。目标读者：lex + 实现者。
> 关联文档：`UI_UX_SPEC.md`、`ACCEPTANCE.md`、`SPEC.md`、`ROADMAP.md`。

## 0. 目的与范围

本契约定义一套**声明式接入规范**，让任意 LangGraph 工作流无需修改前端代码即可在 Abcyesno 平台运行。它把"每个工作流特有的东西"全部变成数据（manifest + 事件流），前端只维护一套通用渲染器。

本契约是"不反复写前端"承诺成立的前置条件。当前 `langgraph-agents` skill 仅有 `hello_agent` 与 `manju_craft` 两个硬编码示例（经 `langgraph_agent` 工具直连），没有这层契约，因此每加新工作流仍需改 UI。本契约的验收标志：`hello_agent` 与 `manju_craft` 改写为头两个合规 manifest，且新增第三个工作流时前端零改动。

## 1. 三层栈角色定义

| 层 | 角色 | 在本栈中的承载 |
|---|---|---|
| 前端 | 通用渲染层：按契约渲染任意工作流的 state/events | CopilotKit / AG-UI + React（App.jsx、ChatLayout、MessageThread、ApprovalDialog、MarketPanel） |
| 后端 | 执行运行时：agent loop + tools / skills / memory / gateway | Hermes harness（`hermes-fork/`） |
| 编排 | 复杂编排层（controller）：产出可被契约加载的工作流 | LangGraph server / 图 |

关键事实：CopilotKit 与 Hermes 的现有接口**未必直接暴露**下面第 3 节的 5 层契约，因此需要在 LangGraph 与前端之间插入一层 **adapter**。

## 2. 缺失的 adapter 层

adapter 是让契约成立的最小新增组件，职责如下：

1. **manifest 发现**：扫描并暴露所有已注册工作流的 manifest，供"技能市场"目录使用。
2. **运行生命周期翻译**：把 LangGraph 的 `invoke -> stream(updates) -> result` 翻译成 AG-UI 事件流（见第 5 节），使前端能通用渲染进度与产物。
3. **审批透传**：当工作流在某步骤需要人工确认时，adapter 发出 human-in-the-loop 事件，CopilotKit 弹出 `ApprovalDialog`；用户确认/拒绝后，adapter 把结果回传 LangGraph 继续/中止。

**承载位置（建议）**：adapter 落在 Hermes 侧，复用现有两个天然承载点：
- `langgraph-agents` skill：每个工作流的 manifest 与其服务端接线在此登记。
- `electron/backend/agui-server.js`：AG-UI 事件流从此发出；adapter 的翻译逻辑在此接入，把 LangGraph 流桥接为 AG-UI 流。

> 备选：若未来 LangGraph 部署在独立服务，adapter 可独立为微服务；但契约（manifest 格式 + 事件协议）不变。

## 3. 契约的 5 层

### L1 发现与 manifest（Discovery）
- 每个工作流提供一个 `manifest.yaml`，描述身份与能力。
- 字段：`id`、`name`、`description`、`category`、`icon`、`version`、`tags`（能力标签）、`requires_approval`（布尔，是否含审批门）。
- 前端"技能市场"直接消费此清单，不硬编码任何工作流。

### L2 输入 schema（Input）
- 用 JSON-Schema（draft-07）描述工作流所需输入。
- 为方便渲染，约定字段 `x-ui` 提示控件类型：`text` / `textarea` / `number` / `select` / `multiselect` / `file` / `boolean` / `date`。
- 前端按 schema **动态生成表单**，不做每工作流定制组件。
- 文件输入：用户选择文件后，前端走现有 `upload-file` 通道取得 `localPath`，填入该字段。

### L3 输出渲染提示（Output）
- manifest 声明 `output.render`：`markdown` / `text` / `image` / `image_gallery` / `video` / `table` / `file` / `card` / `none`。
- 附带 `output.schema`（可选，table/card 用）：列定义或字段定义。
- 前端 `MessageThread` 按 `output.render` 通用渲染，并支持多产物（一次运行产出多个 artifact）。

### L4 审批门（Approval）
- manifest 声明 `approvals`：列表，每项含 `id`、`description`、`trigger`（触发该审批的步骤/条件）。
- 运行时 adapter 据此发出 human-in-the-loop 事件；前端复用现有 `ApprovalDialog`。
- 未声明审批的工作流不弹窗，零额外 UI。

### L5 统一进度事件通道（Progress / Events）
- 规定工作流必须吐标准事件流；adapter 把 LangGraph 原生事件映射成 AG-UI 事件（见第 5 节）。
- 事件类型：`run_started` / `step_started` / `step_progress` / `step_done` / `artifact` / `needs_approval` / `error` / `run_ended`。
- 前端只渲染这条时间线（新增通用 `WorkflowTimeline` 组件），不写专属进度 UI。

### 调用信封（Invocation Envelope）
- 请求：`{ manifest_id, inputs, session_id, run_id }`，inputs 经 L2 schema 校验。
- 响应：流式 AG-UI 事件；终态为 `run_ended` 携带 `result`（与 L3 渲染提示对应）。
- 现有 `langgraph_agent` 工具的调用约定应固化为上述信封，成为契约的传输实现。

## 4. Manifest 文件格式与位置

约定位置（Hermes 侧）：

```
hermes-fork/.../skills/langgraph-agents/<workflow_id>/manifest.yaml
```

最小示例（`hello_agent`）：

```yaml
id: hello_agent
name: Hello Agent
description: 最小 demo 工作流，回显输入。
category: demo
icon: sparkle
version: 1.0.0
tags: [demo, echo]
requires_approval: false
input:
  type: object
  properties:
    message:
      type: string
      title: 消息
      x-ui: textarea
  required: [message]
output:
  render: markdown
  schema: null
approvals: []
events: agui   # 走 L5 标准事件通道
```

`manju_craft`（漫剧/剪映视频工作流）应声明 `output.render: video` + `image_gallery`，并在 `approvals` 中声明发布前审批门，作为契约的"重产物 + 审批"验证样例。

## 5. 事件映射表（LangGraph -> AG-UI -> UI）

| LangGraph 原生 | adapter 翻译为 AG-UI 事件 | UI 渲染 |
|---|---|---|
| `on_chain_start` / 节点开始 | `step_started { step, label }` | WorkflowTimeline 新增节点 |
| `on_chain_stream` / 中间文本 | `step_progress { step, delta }` 或 `text_message_content` | Timeline 内联文本 / 气泡 |
| `on_tool_start` / `on_tool_end` | `step_progress { tool, status }` | Timeline 工具状态 |
| 产出文件/图/视频 | `artifact { uri, type, render }` | MessageThread 按 `output.render` 渲染 |
| 需人工确认 | `needs_approval { id, description }` | ApprovalDialog |
| 异常 | `error { message }` | 错误横幅 / 错误气泡 |
| `on_chain_end` / 运行结束 | `run_ended { result }` | 终态渲染 |

> 事件协议默认**复用 AG-UI（CopilotKit）既有事件类型**，不另造私有协议（见第 7 节默认决策 D2）。

## 6. Adapter 接口（Hermes 侧，草案）

- `GET  /langgraph/manifests` -> 返回全部 manifest（L1 发现；喂给技能市场）。
- `POST /langgraph/run` -> body 为调用信封；返回 `run_id`，并通过 AG-UI 流推送 L5 事件。
- `POST /langgraph/run/:id/approve` 与 `/reject` -> 透传 human-in-the-loop 结果回 LangGraph。
- 事件翻译实现位于 `agui-server.js` 的 adapter 桥接处；manifest 加载实现位于 `langgraph-agents` skill。

## 7. 前端通用渲染器改动点

| 现有组件 | 改动 |
|---|---|
| `MarketPanel` | 改为消费 L1 manifest 清单（已是受控组件，接 `GET /langgraph/manifests`）。 |
| 新增 `ContractForm` | 按 L2 schema 动态渲染输入表单，替代每工作流定制输入。 |
| `MessageThread` | 按 L3 `output.render` 通用渲染产物（已有 markdown/图，补 video/gallery/table/card）。 |
| `ApprovalDialog` | 复用，由 L4 / `needs_approval` 事件驱动。 |
| 新增 `WorkflowTimeline` | 渲染 L5 进度事件时间线。 |

## 8. 迁移计划

1. 起草本契约（本文档）。
2. 在 `langgraph-agents` 下为 `hello_agent` 补 `manifest.yaml`（L1-L5 全声明）。
3. 写 adapter 的 manifest 发现 + 运行翻译 + 审批透传（第 6 节）。
4. 前端加 `ContractForm` + `WorkflowTimeline`，`MarketPanel` 接 manifest 接口。
5. 把 `manju_craft` 改为合规 manifest（验证 video + 审批门）。
6. 验收：新增第三个工作流（仅放 manifest + 指向 LangGraph 部署），前端零改动即通过。

## 9. 默认决策（待 lex 确认）

- **D1 输入 schema 格式**：用 JSON-Schema draft-07 + `x-ui` 控件提示。（备选：自研轻量字段格式）
- **D2 进度事件通道**：复用 AG-UI / CopilotKit 既有事件类型，不造私有协议。（备选：自定义事件 envelope）
- **D3 adapter 位置**：Hermes 侧（`langgraph-agents` skill + `agui-server.js`）。（备选：独立微服务）

以上三项为建议默认值，lex 否决任一项则改。

## 10. 验收标准（对应 ACCEPTANCE 新增 Phase）

- 新增工作流仅需：放置合规 `manifest.yaml` + 指向 LangGraph 部署；前端**零代码改动**即可被发现、填表、看进度、看结果。
- `hello_agent`、`manju_craft` 均为合规 manifest，并经真实运行验证（需 [API] + [ENV]）。
- adapter 三个接口（manifests / run / approve）实现并测试。
- 前端 `ContractForm` + `WorkflowTimeline` 通用组件就位，无每工作流分支。
