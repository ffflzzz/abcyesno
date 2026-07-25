# Abcyesno — Agent Platform PRD

## 1. 项目定位

### 1.1 背景
- 当前 `hermes-portable` 是一个独立的 Electron + React + Node.js 聊天客户端，内置了一个自实现的 Agent 后端。
- 该实现无法复用 Hermes 官方成熟的 harness（进程管理、工具系统、审批、记忆、gateway 等），也难以稳定运行。
- 用户目标是：**把真正的 Hermes 改造成便携版，并能够方便地接入自定义 LangGraph Agent（如 `manju-craft`）。**

### 1.2 核心决策
**Fork Hermes 源码进行二次开发：**
- 保留 Hermes 的 harness 核心组件（agent loop、tools、skills、memory、gateway、session）。
- 拆除 IM 通道服务商、官方更新渠道、非必要模块。
- 用 Hermes 本身作为中央 Agent 调度器。
- 自定义 LangGraph Agent 以 Hermes skill / tool 形式接入。
- Electron 前端作为替代官方 desktop 的轻量客户端壳。

### 1.3 核心目标
把 `hermes-portable` 升级为一个**基于 Hermes harness 的便携 Agent 平台**：
- 便携：复制到任意 Windows 机器即可运行，无需安装 Hermes 官方包。
- 可扩展：每新增一个 LangGraph Agent，只需写一个 Hermes skill / tool。
- 可控：剥离官方 IM/更新渠道，避免外部依赖和自动更新。
- 一致：用户只看到统一的聊天界面，不关心后端调用了哪个 Agent。

## 2. 设计原则

| 原则 | 说明 |
|------|------|
| **Harness 复用 Hermes** | 不重新实现进程管理、工具注册、审批、会话、记忆、gateway。 |
| **Hermes 作为中央调度器** | 用 Hermes 自身的 agent loop 和 skill 系统做路由与执行。 |
| **LangGraph Agent 以 skill 形式接入** | 每个自定义 Agent 是一个 Hermes skill / tool，复用 Hermes 的执行上下文。 |
| **拆除官方 IM / 更新** | 移除 whatsapp、slack、自动更新、Telemetry 等非必要模块。 |
| **Electron 前端为薄壳** | 前端只负责 UI 渲染和事件显示，所有智能逻辑在 Hermes 后端。 |
| **先跑通，再便携化** | 第一阶段先让 Hermes fork + skill + 前端能工作；第二阶段再打包成便携版。 |

## 3. 用户故事

1. 用户打开 `Abcyesno.exe`，输入「帮我做一条剪映视频」，系统自动调用 `manju-craft` skill 并返回结果。
2. 开发者新写了一个 LangGraph Agent，只需在 `skills/langgraph_agents/` 下新增一个 skill 目录，前端即可自动识别。
3. 维护者复制整个 `hermes-portable` 文件夹到另一台 Windows 电脑，双击 exe 即可运行，无需安装 Python 或 Hermes 官方包。

## 4. 功能架构

```
┌─────────────────────────────────────────────┐
│     Electron / React 前端                    │
│  React + CopilotKit（自定义 UI）             │
└──────────────┬──────────────────────────────┘
               │ HTTP/SSE  /api/ag-ui/run
┌──────────────▼──────────────────────────────┐
│         Electron Main                        │
│  ├─ AG-UI Runtime Bridge (Express)          │
│  │   翻译 CopilotKit ↔ Hermes Gateway       │
│  ├─ Hermes Gateway Client (WebSocket)       │
│  └─ BrowserWindow / IPC / Approval UI       │
└──────────────┬──────────────────────────────┘
               │ JSON-RPC / WebSocket
┌──────────────▼──────────────────────────────┐
│          Hermes Harness（Fork 版）           │
│  - Agent Loop / Session / State DB           │
│  - Tool Registry & Execution                 │
│  - Skill Registry                            │
│  - Memory                                    │
│  - Approval / Security Gate                  │
│  - Gateway (JSON-RPC over WS)                │
└──────────────┬──────────────────────────────┘
               │ skill / tool
┌──────────────▼──────────────────────────────┐
│           LangGraph Agent Skills             │
│  ┌─────────────┐ ┌─────────┐ ┌───────────┐  │
│  │  manju-craft │ │ agent-2 │ │ agent-3   │  │
│  │   skill      │ │ skill   │ │  skill    │  │
│  └─────────────┘ └─────────┘ └───────────┘  │
│      ↑                ↑                      │
│   LangGraph      LangGraph                   │
│   (Python)       (Python)                    │
└──────────────────────────────────────────────┘
```

## 5. 核心能力

### 5.1 前端能力
- 基于 **CopilotKit** 的聊天状态管理和流式响应。
- 统一聊天界面：消息输入、流式输出、工具事件展示。
- 助手列表、会话列表与历史记录。
- 工具审批弹窗（调用 Hermes approval API）。
- 设置：API Key、模型选择、skill 开关。

### 5.2 Hermes Harness 能力（复用）
- 多轮对话与上下文管理。
- 工具调用与执行（terminal、browser、file、search 等）。
- Skill 注册与调用。
- 记忆系统。
- 审批与安全门控。
- Session 持久化。

### 5.3 LangGraph Agent 接入能力
- 每个 LangGraph graph 包装为一个 Hermes skill。
- Skill 可以声明自己的能力标签和输入 schema。
- Hermes 根据用户输入或显式路由调用对应 skill。

## 6. `manju-craft` 集成方案

- 在 Hermes fork 的 `skills/langgraph_agents/` 下新建 `manju_craft/`。
- `manju_craft/skill.py` 加载 `C:/Users/Administrator/Desktop/manju-craft/graph/graph.py` 的 graph。
- 实现 `run(messages, config)` 方法，内部调用 LangGraph graph 的 `astream` / `ainvoke`。
- 返回 SSE 式事件流，Hermes 负责转发给前端。

## 7. 便携版策略

### 7.1 第一阶段（本地验证）
- Hermes fork 源码放在项目 `hermes-fork/` 目录。
- 依赖本机 Python 环境运行 `hermes serve`。
- Electron 前端连接本地 gateway。

### 7.2 第二阶段（便携化）
- 把 Hermes fork + Python 依赖 + Electron 前端一起打包。
- 使用嵌入式 Python 或便携式 venv。
- 启动器自动设置 `HERMES_HOME` 和 `PATH`，拉起 `hermes serve`。

## 8. 里程碑

| 阶段 | 目标 | 产出 |
|------|------|------|
| **P0** | Fork Hermes 源码到项目，建立可运行的 baseline | `hermes-fork/` 能启动 `hermes serve` |
| **P1** | 拆除 IM / 更新 / 非必要模块 | 精简版 Hermes 能启动，无 IM 相关命令 |
| **P2** | 验证 harness 核心能力 | Hermes 原生 chat、tool、approval 能工作 |
| **P3** | 实现 LangGraph skill adapter | 能把任意 LangGraph graph 注册为 Hermes skill |
| **P4** | 接入 `manju-craft` | `manju-craft` skill 能在聊天中被调用 |
| **P5** | Electron 前端 + AG-UI Bridge 替代官方 desktop | 前端通过 CopilotKit 连接本地 AG-UI runtime，显示聊天和事件 |
| **P6** | 便携化打包 | 单文件夹 / 单文件可运行 |
| **P7** | 验收测试 | 通过 ACCEPTANCE.md 所有条目 |

## 9. 非目标

- 不重新实现 Hermes 已有的 harness 能力。
- 不维护 IM 渠道（whatsapp、slack、discord 等）。
- 不保留官方自动更新、Telemetry、在线服务依赖。
- 不强制所有 Agent 用同一种 LangGraph 结构实现。

## 10. 开放问题

1. Hermes skill 系统的精确注册和调用接口是什么？（需要调研源码）
2. 如何优雅地移除 IM 相关模块而不破坏 CLI 加载？
3. 更新检查/在线服务依赖代码分散在哪些文件中？
4. 便携化时 Python 环境如何最小化？（venv / portable Python / PyInstaller）
5. AG-UI Bridge 如何翻译 Hermes Gateway 事件为 CopilotKit 可识别事件？

---

**结论**：
- **Hermes 作为 harness 和中央调度器复用**，通过 fork 二次开发实现。
- **拆除 IM 和官方更新渠道**，保留核心能力。
- **LangGraph Agent 以 Hermes skill 形式接入**。
- **Electron 作为轻量前端壳**，替代官方 desktop。
