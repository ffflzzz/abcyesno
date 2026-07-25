# Abcyesno — 术语表

本文档统一项目中使用的关键术语，避免后续开发时混淆。

## A

### AG-UI
- **含义**：Agent-User Interface 的协议/组件集合，本项目特指 `@ag-ui/*` 和 CopilotKit 使用的 SSE 运行时协议。
- **作用**：前端 CopilotKit 通过 AG-UI 协议与后端通信，接收流式事件。

### Assistant（助手）
- **含义**：前端界面中的一个可对话实体。
- **对应后端**：一个 Hermes skill 或一组 skill 的包装。
- **示例**：「Manju Craft 助手」对应 `manju-craft` skill。

## B

### Bridge（AG-UI Runtime Bridge）
- **含义**：Electron Main 中的 Node.js/Express 服务。
- **作用**：把 CopilotKit 的 AG-UI 请求翻译成对 Hermes Gateway 的 JSON-RPC 调用，并把事件流返回前端。

## C

### Capability（能力标签）
- **含义**：描述一个 skill/agent 能做什么的标签。
- **示例**：`video`、`jianying`、`workflow`、`research`。
- **作用**：Router 根据能力标签选择调用哪个 skill。

### CopilotKit
- **含义**：一个 React 库，提供聊天状态管理、流式响应、工具调用、自定义 UI 能力。
- **作用**：本项目前端使用 CopilotKit hooks，但自定义全部 UI 组件。

## H

### Harness
- **含义**：Agent 运行所需的基础设施层。
- **包含**：进程管理、工具注册与执行、审批门控、会话持久化、记忆、gateway 等。
- **本项目**：复用 Hermes 的 harness，不自建。

### Hermes Fork
- **含义**：从官方 Hermes 源码复制到本项目的代码副本。
- **位置**：`hermes-fork/`。
- **目的**：二次开发，剥离非必要模块，保留 harness。

### Hermes Gateway
- **含义**：Hermes 提供的 JSON-RPC over WebSocket 服务。
- **作用**：外部客户端（如 Electron Main）通过它调用 Hermes 的 session、prompt、tool、approval 等方法。

## L

### LangGraph Agent
- **含义**：使用 LangGraph 框架编写的状态机/工作流。
- **接入方式**：包装成 Hermes skill，或被 AG-UI Bridge 直接调用。
- **示例**：`manju-craft` 的 `graph/graph.py`。

## M

### MCP（Model Context Protocol）
- **含义**：一种标准化的 Agent/Tool 互操作协议。
- **本项目地位**：未来可能支持，当前先用 HTTP+SSE / Hermes skill 机制。

## R

### Router（路由）
- **含义**：决定用户消息应该调用哪个 skill/agent 的逻辑。
- **实现位置**：AG-UI Bridge 或 Hermes agent loop 内部。
- **方式**：关键词匹配、能力标签匹配、LLM 决策。

### Runtime
- **含义**：CopilotKit 概念，指提供 `/api/ag-ui/run` 接口的后端服务。
- **本项目**：AG-UI Bridge 就是 CopilotKit 的 runtime。

## S

### Session（会话）
- **含义**：用户与某个助手之间的一次连续对话。
- **持久化**：由 Hermes harness 的 session/state DB 管理。

### Skill
- **含义**：Hermes 的能力扩展单元，一个 skill 可以是一个工具集合、一个 agent 或一个工作流。
- **本项目**：每个 LangGraph Agent 包装成一个 Hermes skill。
- **位置**：`hermes-fork/skills/langgraph_agents/<skill-name>/`。

## T

### Tool（工具）
- **含义**：Agent 可调用的具体能力，如 `terminal`、`browser`、`read_file`。
- **来源**：Hermes 自带工具 + LangGraph skill 内部注册的工具。

## W

### Workflow
- **含义**：一个有明确步骤的工作流，通常用 LangGraph 实现。
- **与 Agent 区别**：Agent 更强调自主决策，Workflow 更强调预定义步骤；但在 UI 层面都可作为「助手」。
