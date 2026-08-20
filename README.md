# Abcyesno

基于 [Hermes](https://github.com/) 源码二次开发的**便携桌面 Agent 平台**（Electron + Vite + React 前端 + Hermes Python 后端）。内置「短剧制片工作台」（`manjucraft_agent` 漫剧工作流），可把剧本一键生产成角色图、分镜、成片视频与剪映草稿。

## 项目目标

- 复用 Hermes 的 harness（agent loop、tools、skills、memory、gateway）。
- 拆除 IM 通道、官方更新、Telemetry 等非必要模块。
- 让 LangGraph Agent（如短剧制片工作台 `manjucraft_agent`）以 Hermes skill 形式接入。
- 用 Electron + 全自研 React 前端替代官方 desktop 前端（基于 AG-UI 协议直连 SSE，**不依赖 CopilotKit**）。
- 最终打包成即装即用的桌面应用（nsis 安装包 + 免安装 `win-unpacked` 文件夹）。

## 文档导航

| 文档 | 说明 |
|------|------|
| [docs/PRD.md](docs/PRD.md) | 产品需求与设计目标 |
| [docs/SPEC.md](docs/SPEC.md) | 技术规格与架构 |
| [docs/WORKBENCH_ARCHITECTURE_SPEC.md](docs/WORKBENCH_ARCHITECTURE_SPEC.md) | 工作台组件化架构 |
| [docs/SHORTDRAMA_STUDIO_SPEC.md](docs/SHORTDRAMA_STUDIO_SPEC.md) | 短剧制片工作台规格 |
| [docs/STUDIOWORKBENCH_LAYOUT_REFACTOR_SPEC.md](docs/STUDIOWORKBENCH_LAYOUT_REFACTOR_SPEC.md) | 工作台布局重构方案 |
| [docs/LANGRAPH_CONTRACT_SPEC.md](docs/LANGRAPH_CONTRACT_SPEC.md) | LangGraph Agent 契约 |
| [docs/MANJUCRAFT_AGENT_SPEC.md](docs/MANJUCRAFT_AGENT_SPEC.md) | 漫剧 Agent 流水线 |
| [docs/UI_UX_SPEC.md](docs/UI_UX_SPEC.md) | 前端 UI/UX 设计规范 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 开发路线与里程碑 |
| [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) | 验收标准 |
| [docs/SETUP.md](docs/SETUP.md) | 开发环境搭建 |
| [docs/STRIPPING_GUIDE.md](docs/STRIPPING_GUIDE.md) | Hermes 源码精简指南 |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | 术语表 |
| [docs/ADR/001-fork-hermes.md](docs/ADR/001-fork-hermes.md) | 架构决策：Fork Hermes |

---

## 完整架构

四层：Electron 外壳 → React 渲染层 → AG-UI 桥 → Hermes 后端。渲染层与后端**不直接通信**，全部经由运行在 Electron 主进程内的 Node 桥 `agui-server` 转发，保证沙箱 renderer 永远不会直连 Python 后端或外部 LLM。

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           ELECTRON SHELL  (主进程)                          │
│   electron/main.js                                                          │
│    · 应用生命周期 / 单实例锁                                                │
│    · 注册 abcyesno-local:// 特权协议 (protocol.handle) → 本地图片/视频      │
│    · hermes-runner.js：拉起并托管 Python 后端、便携 Python 解析            │
│    · 宿主 agui-server.js (Node 桥, 动态 localhost 端口 → 渲染层 aguiPort)   │
│    · CDP 调试端口 18922 (dev)                                              │
└───────────┬──────────────────────────────────────┬─────────────────────────┘
            │  IPC / preload.js                     │  HTTP + SSE (aguiPort)
            ▼                                       ▼
┌──────────────────────────────────┐   ┌──────────────────────────────────────────┐
│       RENDERER  (React + Vite)    │   │        AG-UI BRIDGE  (Node)               │
│       src/                        │   │     electron/backend/agui-server.js        │
│  App → ChatLayout → MessageThread │   │  POST /api/ag-ui/run        → 返回 SSE 流 │
│  Composer / Sidebar / ToolCard    │   │  POST /api/ag-ui/workflow-event (Python→) │
│  StudioWorkbench (短剧制片)       │   │  GET  /api/ag-ui/contract/manifests       │
│  useAgentStream (SSE 状态机)      │   │  按 manifest 解析 → 转发 Hermes :9120     │
│  contract/eventBus (按 threadId)  │   │  下发 AG-UI 事件：                         │
│  utils/mediaSrc (toLoadableSrc)   │   │   RUN_STARTED / TEXT_MESSAGE_* /           │
│                                    │   │   TOOL_CALL_* / CUSTOM(workflow.*,…)      │
└──────────────────────────────────┘   └───────────────┬──────────────────────────┘
                                                       │  HTTP (9120)
                                                       ▼
                          ┌────────────────────────────────────────────────────┐
                          │            HERMES FORK BACKEND  (Python)             │
                          │            hermes_cli.main serve :9120               │
                          │  · Agent harness: loop / tools / skills / memory     │
                          │  · LLM provider: electron/backend/agnes.js           │
                          │       → https://apihub.agnes-ai.com/v1               │
                          │  · skills/langgraph_agents/                          │
                          │      langgraph_runtime.py  (编排 + 发 workflow.*)     │
                          │      agents/manjucraft_agent (短剧生产流水线)         │
                          │  · 工件落盘 ~/.hermes_portable_data/.../ep000/        │
                          └────────────────────────────────────────────────────┘
```

### 关键约束

- **渲染层只认 `aguiPort`**：`agui-server` 在启动时自选一个 localhost 端口，通过 `App({ aguiPort })` 注入渲染层；渲染层所有请求都打到这个端口，再由桥转发到 `:9120` 的 Hermes 后端。
- **本地媒体走特权协议**：Electron 沙箱 renderer 是 opaque origin，禁止跨目录 `file://` 子资源。所有本地图片/视频必须经 `abcyesno-local://`（3 斜杠 + 剥盘符冒号 + 分段编码），由 `main.js` 的 `protocol.handle` 还原路径并以显式 MIME 返回。
- **Hermes 后端单端口**：`:9120` 同时承载 agent 运行与 gateway；前端健康检查此端口判断后端就绪。

---

## 一条消息的完整旅程

以「在短剧制片工作台提交一段剧本、触发生产」为例：

| # | 环节 | 动作 |
|---|------|------|
| 1 | **输入** | 用户在 `Composer` 输入文本/结构化表单 → `MessageThread` 调用 `useAgentStream.run(text, { threadId, assistantId })` |
| 2 | **发起** | `useAgentStream` 向 `http://127.0.0.1:{aguiPort}/api/ag-ui/run` 发 `POST`（带消息 + `threadId` + agent manifest id），并打开该 run 的 **SSE 流** |
| 3 | **路由** | `agui-server` 解析 manifest，把请求转发给 Hermes 后端 `:9120`，触发对应 agent（普通对话 agent，或 `langgraph_agent` 工具进入工作台流水线） |
| 4 | **推理** | Hermes 跑 agent loop：经 `agnes.js` 调用 Agnes LLM（`apihub.agnes-ai.com/v1/chat/completions`）生成文本/决定工具调用；`TEXT_MESSAGE_*` 与 `TOOL_CALL_*` 经桥实时回传 SSE |
| 5 | **编排** | 当调用 `langgraph_agent` 工具时，`langgraph_runtime.py` 执行 `manjucraft_agent` 流水线节点：`parse_script → generate_characters → batch_generate_keyframes → consistency_check → fix_drift → batch_generate_video → generate_tts → merge_and_concat → generate_jianying_draft` |
| 6 | **落盘** | 节点产物（角色 PNG、分镜图、shot MP4、`final.mp4`、剪映草稿）写入 `~/.hermes_portable_data/manjucraft_agent/projects/<项目名>/ep000/` |
| 7 | **回传** | Python 把 `workflow.*` 事件（progress / artifact / approval / graph / trace / done / error）`POST` 到 `agui-server` 的 `/api/ag-ui/workflow-event` |
| 8 | **中继** | `agui-server` 把每条 `workflow.*` 包装成 AG-UI `CUSTOM{name:"workflow.*"}` 注入对应 run 的 SSE 流 |
| 9 | **分发** | `useAgentStream` 状态机消费 SSE：`TEXT_MESSAGE_CONTENT` → 流式文本气泡；`TOOL_CALL_*` → `ToolCard`；`CUSTOM workflow.*` → `emitContractEvent(threadId, …)` → `contract/eventBus` 按 threadId 广播 |
| 10 | **渲染** | `StudioWorkbench.ingestArtifact` / `WorkflowTimeline` / `WorkflowGraphPanel` 收到事件后就地更新；媒体 URL 经 `toLoadableSrc` 转成 `abcyesno-local://`，由 `main.js` 的 `protocol.handle` 取文件返回，图片/视频在沙箱内正常显示 |

> 多会话并发：后端按 `threadId` 维护独立 session、无锁并发；前端 `useAgentStream` 用「每会话一个流」的 `Map`，切换会话视图不断流。

---

## 核心组件一览

### Electron 主进程 / 桥

| 组件 | 文件 | 职责 |
|------|------|------|
| 主进程 | `electron/main.js` | 应用生命周期、单实例锁、`abcyesno-local://` 协议（`protocol.handle` 还原中文/空格路径并以 MIME 返回）、托管 `agui-server`、拉起 `hermes-runner` |
| 后端运行器 | `electron/backend/hermes-runner.js` | 用便携 Python 拉起 `hermes_cli.main serve :9120`；解析基础解释器、处理代理（`network.proxy_url` / DIRECT）、健康检查 |
| AG-UI 桥 | `electron/backend/agui-server.js` | 前端↔后端唯一契约：`/api/ag-ui/run`(SSE) `/api/ag-ui/workflow-event`(Python 回传) `/api/ag-ui/contract/manifests`；把 workflow.* 中继成 CUSTOM 事件 |
| LLM Provider | `electron/backend/agnes.js` | 封装 Agnes API（`apihub.agnes-ai.com/v1`），含 `toLocalPath/fileToDataUri/downloadMedia` 等本地媒体工具 |
| 预加载 | `electron/preload.js` | 渲染层 ↔ 主进程 IPC 桥 |

### Hermes 后端（Python）

| 组件 | 文件 | 职责 |
|------|------|------|
| Agent harness | `hermes-fork/`（agent / tools / skills / memory / gateway） | agent loop、工具、技能、记忆、网关，监听 `:9120` |
| LangGraph 运行时 | `hermes-fork/skills/langgraph_agents/langgraph_runtime.py` | 编排流水线、发射 `workflow.*` 事件、HITL 审批门 |
| 漫剧 Agent | `hermes-fork/skills/langgraph_agents/agents/manjucraft_agent` | 短剧生产流水线（剧本→角色→分镜→视频→配音→拼接→剪映草稿） |

### 渲染层（React）

| 组件 | 文件 | 职责 |
|------|------|------|
| 根应用 | `src/App.jsx` | 接收 `aguiPort`、路由、助手选择、会话标题异步生成 |
| 启动引导 | `src/components/Launcher.jsx` | 等待后端/桥就绪（拿到 `aguiPort`）再渲染主界面 |
| 流状态机 | `src/hooks/useAgentStream.js` | **手写** AG-UI SSE 状态机（每会话一流），完全绕开 CopilotKit |
| 契约事件总线 | `src/contract/eventBus.js` | 按 `threadId` 规范化并广播 `workflow.*` 事件给各工作台 |
| Manifest | `src/contract/manifests.generated.js` | 构建期由 agent manifest 自动生成（id/name/schema/ui） |
| 对话界面 | `src/components/`（`ChatLayout` `MessageThread` `Composer` `ToolCard` `ArtifactCard` `WorkflowTimeline` `WorkflowGraphPanel` `ApprovalDialog` `Sidebar` `SkillPanel` …） | 通用聊天 + 工具/工件/时间轴/审批/技能面板 |
| 短剧制片工作台 | `src/workbenches/StudioWorkbench.jsx`（+`.css`） | 剧本→资产→分镜→成片 四阶段自适应布局；`ingestArtifact` 消费工件 |
| 工作台注册表 | `src/workbenches/registry.js` | 按 manifest `ui.component` 零分支解析渲染（`Blueprint`/`Timeline`/`Studio`） |
| 本地媒体转换 | `src/utils/mediaSrc.js` | `toLoadableSrc`（裸路径/远程 URL → `abcyesno-local://`）、`originalPathOf`、`isRemoteMediaUrl` |

---

## 快速开始

### 下载安装包（推荐，外部用户）

到 **Releases** 页下载 `Abcyesno-Setup-1.4.0.exe` 安装，或下载 `win-unpacked` 文件夹直接运行 `Abcyesno.exe`（免安装）。首次启动输入 Agnes API Key 即自动重启后端。

> 安装包发布在 GitHub Releases，不入库（仓库只含源码）。仓库公开可克隆，但需自行构建才能跑（见下）。

### 从源码构建

```bash
# 1. 安装依赖
npm install

# 2. 准备便携 Python + Playwright（分发/运行所需，详见 docs/SETUP.md）
#    便携 Python 经 scripts/bundle-python.mjs 拷到 build/runtime/python
#    npx playwright install chromium

# 3. 构建前端 + 打包
npm run build          # vite build → dist/
npm run electron:build # electron-builder → release/ (dir + nsis)

# 4. 运行（开发热更新）
npm run dev
```

产物：`release/win-unpacked/`（免安装文件夹）与 `release/Abcyesno-Setup-1.4.0.exe`（nsis 安装包，~614MB）。

### 默认环境

- Provider：Agnes `custom` + `agnes-2.5-flash`，`https://apihub.agnes-ai.com/v1`
- 代理：默认走 `http://127.0.0.1:7897/`（Agnes API）；`HERMES_HOME=%USERPROFILE%/.hermes_portable_data`（与系统 Hermes 隔离）
- `model.max_tokens: 8192`（reasoning 模型必须大，否则 content 空）

## 当前状态

- **分发形态**：`[dir, nsis]` 并存 —— `win-unpacked` 免安装文件夹 + `Abcyesno-Setup-*.exe` 安装包（装到 `%LOCALAPPDATA%`，非单文件便携）。
- **短剧制片工作台**：剧本→资产→分镜→成片 四阶段自适应布局，视觉重设计（主题自适应）。
- **本地媒体预览**：沙箱 renderer 经 `abcyesno-local://` 加载本地图片/视频（含中文路径），角色图、分镜图、成片视频均可正常显示与播放。
- **核心链路**：Electron 主进程内 `agui-server` 桥 + `useAgentStream` 手写 SSE 状态机，全自研 React（已脱离 CopilotKit）。
- 已弃用：`v5` 旧单文件便携 exe（首次自解压 ~2GB 到临时 guid 目录，遇 >260 字符路径 + Defender 扫 7 万文件会静默卡死）。

### 已落地的前端特性

- **图片交互**：输入框与对话气泡中的图片以文件名芯片显示，悬停才弹缩略图。
- **会话列表**：标题回退到首条消息、相对时间、未命名会话隐藏副标题。
- **会话标题**：新会话首轮结束后由后端 `/api/session-title` 异步生成 ≤12 字总结标题（用户重命名优先）。
- **结果面板脱离**：可将结果区脱离为独立窗口（多窗口并行查看）。
- **短剧制片工作台**：统一视频生产前端（`StudioWorkbench`），覆盖角色图、分镜、成片、剪映草稿导出；提交真实结构化输入走 HITL 审批门。

## 开发路线

详见 [docs/ROADMAP.md](docs/ROADMAP.md)。
