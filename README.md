# Abcyesno

基于 [Hermes](https://github.com/) 源码二次开发的**便携桌面 Agent 平台**（Electron + Vite + React 前端 + Hermes Python 后端）。内置「短剧制片工作台」（`manjucraft_agent` 漫剧工作流），可把剧本一键生产成角色图、分镜、成片视频与剪映草稿。

> 当前版本：**v1.4.5**（Release 提供免安装 `win-unpacked` 压缩包，nsis 安装包已停用）

## 项目目标

- 复用 Hermes 的 harness（agent loop、tools、skills、memory、gateway、cron、后台自我改进）。
- 拆除 IM 通道（仅保留微信收发桥）、官方更新、Telemetry 等非必要模块。
- 让 LangGraph Agent（如短剧制片工作台 `manjucraft_agent`）以 Hermes skill 形式接入。
- 用 Electron + 全自研 React 前端替代官方 desktop 前端（基于 AG-UI 协议直连 SSE，**不依赖 CopilotKit**）。
- 最终打包成免安装的桌面应用（`win-unpacked` 文件夹， Releases 直接下载解压即用）。

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
| [docs/诊断-skill沉淀与cron功能.md](docs/诊断-skill沉淀与cron功能.md) | Skill 沉淀 / Cron 现状诊断与排期 |
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
│    · 审批/澄清/sudo 请求转发渲染层（session_id 全链路）                     │
│    · CDP 调试端口 18922 (dev)                                              │
└───────────┬──────────────────────────────────────┬─────────────────────────┘
            │  IPC / preload.js                     │  HTTP + SSE (aguiPort)
            ▼                                       ▼
┌──────────────────────────────────┐   ┌──────────────────────────────────────────┐
│       RENDERER  (React + Vite)    │   │        AG-UI BRIDGE  (Node)               │
│       src/                        │   │     electron/backend/agui-server.js        │
│  App → ChatLayout → MessageThread │   │  POST /api/ag-ui/run        → 返回 SSE 流 │
│  Composer / Sidebar / IconRail    │   │  POST /api/ag-ui/workflow-event (Python→) │
│  MessageThread（agent 时间线）    │   │  GET  /api/ag-ui/contract/manifests       │
│  AgentProcessStream（过程流）     │   │  按 manifest 解析 → 转发 Hermes :9120     │
│  useAgentStream (SSE 状态机)      │   │  下发 AG-UI 事件：                         │
│  BrowserPanel（内置浏览器/CDP）   │   │   RUN_STARTED / TEXT_MESSAGE_* /           │
│  contract/eventBus (按 threadId)  │   │   TOOL_CALL_* / CUSTOM(reasoning.*等)     │
└──────────────────────────────────┘   └───────────────┬──────────────────────────┘
                                                       │  HTTP (9120)
                                                       ▼
                          ┌────────────────────────────────────────────────────┐
                          │            HERMES FORK BACKEND  (Python)             │
                          │            hermes_cli.main serve :9120               │
                          │  · Agent harness: loop / tools / skills / memory     │
                          │  · 后台自我改进：回合结束沉淀 skill / memory          │
                          │  · cron 定时任务系统（cronjob 工具）                  │
                          │  · LLM provider: agnes.js → apihub.agnes-ai.com/v1   │
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
- **存储全链路原子化**：`storage.js` 所有写操作经互斥锁（per-file promise 链）+ tmp+rename 原子替换 + 撕裂恢复，防止并发写坏 sessions.json。

---

## 一条消息的完整旅程

以「在短剧制片工作台提交一段剧本、触发生产」为例：

| # | 环节 | 动作 |
|---|------|------|
| 1 | **输入** | 用户在 `Composer` 输入文本/结构化表单 → `MessageThread` 调用 `useAgentStream.run(text, { threadId, assistantId })` |
| 2 | **发起** | `useAgentStream` 向 `http://127.0.0.1:{aguiPort}/api/ag-ui/run` 发 `POST`（带消息 + `threadId` + agent manifest id），并打开该 run 的 **SSE 流** |
| 3 | **路由** | `agui-server` 解析 manifest，把请求转发给 Hermes 后端 `:9120`，触发对应 agent（普通对话 agent，或 `langgraph_agent` 工具进入工作台流水线） |
| 4 | **推理** | Hermes 跑 agent loop：经 `agnes.js` 调用 Agnes LLM 生成文本/决定工具调用；`TEXT_MESSAGE_*`、`TOOL_CALL_*`、`reasoning.delta`（深度推理）经桥实时回传 SSE |
| 5 | **编排** | 当调用 `langgraph_agent` 工具时，`langgraph_runtime.py` 执行 `manjucraft_agent` 流水线节点：`parse_script → generate_characters → batch_generate_keyframes → consistency_check → fix_drift → batch_generate_video → generate_tts → merge_and_concat → generate_jianying_draft` |
| 6 | **落盘** | 节点产物（角色 PNG、分镜图、shot MP4、`final.mp4`、剪映草稿）写入 `~/.hermes_portable_data/manjucraft_agent/projects/<项目名>/ep000/` |
| 7 | **回传** | Python 把 `workflow.*` 事件（progress / artifact / approval / graph / trace / done / error）`POST` 到 `agui-server` 的 `/api/ag-ui/workflow-event` |
| 8 | **中继** | `agui-server` 把每条 `workflow.*` 包装成 AG-UI `CUSTOM{name:"workflow.*"}` 注入对应 run 的 SSE 流 |
| 9 | **分发** | `useAgentStream` 状态机消费 SSE：`TEXT_MESSAGE_CONTENT` → 时间线正文段；`TOOL_CALL_*` → 工具段；`reasoning.delta` → 推理段；`CUSTOM workflow.*` → `emitContractEvent(threadId, …)` → `contract/eventBus` 按 threadId 广播 |
| 10 | **渲染** | **agent 时间线**按感知→推理→行动→回复的真实顺序渲染（运行中逐行打印，结束后分段收纳）；`StudioWorkbench.ingestArtifact` / `WorkflowTimeline` / `WorkflowGraphPanel` 就地更新；媒体 URL 经 `toLoadableSrc` 转成 `abcyesno-local://` 显示 |

> 多会话并发：后端按 `threadId` 维护独立 session、无锁并发；前端 `useAgentStream` 用「每会话一个流」的 `Map`，切换会话视图不断流。

---

## 核心特性（v1.4.5）

### Agent 过程时间线
- **真实时序渲染**：按 感知 → 推理 → 行动 → 回复 的实际发生顺序分段展示，多轮任务阅读顺序与 agent 行为一致。
- **thinking 全程可见**：深度推理框在回答前弹出、流式滚动；运行结束自动收纳为折叠条，可随时展开回看；reasoning 随消息持久化（重启不丢）。
- **逐行过程流**：运行中推理/工具/回复逐行打印，自动滚底、上滚暂停；每轮 thinking 相互独立（中断不串台）。
- **工具段收纳节奏**：执行中展开明细（✓/✗、参数去噪、耗时），完成后保留 2 秒再自动收纳；回合结束整段收起。

### 内置浏览器
- Electron `<webview>` 常驻面板，agent（`pw_browser_*` 工具经 CDP）与用户共用同一页面，实时观看 agent 操作网页。
- 手动浏览：地址栏输入网址；宽度拖拽调节 + 窗口夹紧（不裁切）；仅 agent 调用浏览器工具时自动弹出。

### 审批与 HITL
- 审批气泡从小 Bach 头顶冒出（与报错气泡同款设计），支持批准/拒绝/带意见批准/记住选择；点击立即生效。
- `clarify` 交互式提问、工作流 HITL 审批门、失效审批明确提示。

### 其他
- **多会话并发**：每会话独立流，切走不断流；会话列表相对时间、异步标题摘要。
- **微信桥**：微信收发的会话由后端独占写入，前端只读防覆盖。
- **图片体验**：输入框图片 chip 内嵌缩略图预览；消息内图片走 `abcyesno-local://`。
- **/goal 目标模式**：循环执行直至目标达成（judge 评估 + 心跳防误断）。
- **skill 自动沉淀**：回合结束后台 review 自动 patch SKILL.md / 写记忆（详见 docs/诊断文档）。

---

## 核心组件一览

### Electron 主进程 / 桥

| 组件 | 文件 | 职责 |
|------|------|------|
| 主进程 | `electron/main.js` | 应用生命周期、单实例锁、`abcyesno-local://` 协议、托管 `agui-server`、拉起 `hermes-runner`、审批/澄清请求转发（session_id 全链路） |
| 后端运行器 | `electron/backend/hermes-runner.js` | 便携 Python 拉起 `hermes_cli.main serve :9120`、代理与健康检查 |
| AG-UI 桥 | `electron/backend/agui-server.js` | 前端↔后端唯一契约：`/api/ag-ui/run`(SSE)、`/workflow-event`、`/contract/manifests`；reasoning/thinking/tool 事件翻译 |
| 会话存储 | `electron/backend/storage.js` | 会话/工件 JSON 存储互斥锁 + 原子写 + 撕裂恢复 |
| LLM Provider | `electron/backend/agnes.js` | 封装 Agnes API（`apihub.agnes-ai.com/v1`）与本地媒体工具 |
| 预加载 | `electron/preload.js` | 渲染层 ↔ 主进程 IPC 桥 |

### Hermes 后端（Python）

| 组件 | 文件 | 职责 |
|------|------|------|
| Agent harness | `hermes-fork/`（agent / tools / skills / memory / gateway / cron） | agent loop、工具、技能、记忆、网关、定时任务，监听 `:9120` |
| 后台自我改进 | `hermes-fork/agent/background_review.py` | 回合结束回放对话，自动 patch SKILL.md / 写 memory |
| LangGraph 运行时 | `hermes-fork/skills/langgraph_agents/langgraph_runtime.py` | 编排流水线、发射 `workflow.*` 事件、HITL 审批门 |
| 漫剧 Agent | `hermes-fork/skills/langgraph_agents/agents/manjucraft_agent` | 短剧生产流水线（剧本→角色→分镜→视频→配音→拼接→剪映草稿） |

### 渲染层（React）

| 组件 | 文件 | 职责 |
|------|------|------|
| 根应用 | `src/App.jsx` | 接收 `aguiPort`、路由、审批请求接收、会话标题异步生成 |
| 流状态机 | `src/hooks/useAgentStream.js` | 手写 AG-UI SSE 状态机（每会话一流 + agent 时间线聚合） |
| 对话时间线 | `src/components/MessageThread.jsx` | agent 时间线渲染：`AgentProcessStream`（运行中过程流）/ 推理框 / 工具段 / 正文行；虚拟滚动 |
| 输入区 | `src/components/Composer.jsx` | 输入、图片 chip 缩略图、审批气泡锚点、技能/模型/权限菜单 |
| 内置浏览器 | `src/components/BrowserPanel.jsx` | `<webview>` 面板（agent CDP 共驱）、宽度拖拽、autosize |
| 运行监控 | `src/components/AgentRunMonitor.jsx` | 运行中 run 的状态监控 |
| 会话侧栏 | `src/components/Sidebar.jsx` | 会话列表（相对时间、异步标题）+ 竖轨入口 |
| 契约事件总线 | `src/contract/eventBus.js` | 按 `threadId` 广播 `workflow.*` 事件给各工作台 |
| 短剧制片工作台 | `src/workbenches/StudioWorkbench.jsx` | 剧本→资产→分镜→成片；`ingestArtifact` 消费工件 |
| 本地媒体转换 | `src/utils/mediaSrc.js` | `toLoadableSrc` → `abcyesno-local://` |

---

## 快速开始

### 下载安装包（推荐，外部用户）

到 **Releases** 页下载 `Abcyesno-1.4.5-win-unpacked.rar`，解压后直接运行 `Abcyesno.exe`（免安装，无需安装步骤）。首次启动输入 Agnes API Key 即自动重启后端。

> nsis 安装包已停用，Release 只提供 `win-unpacked` 压缩包（仓库只含源码，压缩包不入库）。

### 从源码构建

```bash
# 1. 安装依赖
npm install

# 2. 准备便携 Python + Playwright（分发/运行所需，详见 docs/SETUP.md）
#    便携 Python 经 scripts/bundle-python.mjs 拷到 build/runtime/python
#    npx playwright install chromium

# 3. 构建前端 + 打包
npm run build          # vite build → dist/
npm run electron:build # electron-builder --win dir → release/win-unpacked

# 4. 运行（开发热更新）
npm run dev
```

产物：`release/win-unpacked/`（免安装文件夹）。

### 默认环境

- Provider：Agnes `custom` + `agnes-2.5-flash`，`https://apihub.agnes-ai.com/v1`
- 代理：默认走 `http://127.0.0.1:7897/`（Agnes API）；`HERMES_HOME=%USERPROFILE%/.hermes_portable_data`（与系统 Hermes 隔离）
- `model.max_tokens: 8192`（reasoning 模型必须大，否则 content 空）

### 质量门（每次改动后必须全过）

```bash
node scripts/check-tdz.js              # TDZ 扫描
node scripts/test-multisession/run.mjs # 多会话回归（38 用例）
npx vite build                         # 前端构建
```

## 已知限制 / 规划中

- **Skill 自动沉淀**：后端机制已默认开启（每 10 次工具迭代评估一次），但前端暂无"已沉淀"提示与技能管理页（见 docs/诊断文档，已排期）。
- **Cron 定时任务前端面板**：后端 cron 系统完整，前端管理面板未实现（已排期）。
- **agent 时间线持久化**：时间线仅存内存，应用重启后的历史会话回退合并布局；持久化已列入规划。

## 开发路线

详见 [docs/ROADMAP.md](docs/ROADMAP.md)。
