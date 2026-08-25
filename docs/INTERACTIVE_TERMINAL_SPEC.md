# Abcyesno — 交互式终端（TUI / PowerShell）技术规范 (Spec)

> 状态：待评审（确认后进入实现）
> 关联：`src/components/TerminalPanel.jsx`（现有只读终端面板）、`src/components/ToolCard.jsx`（工具卡片）、`src/hooks/useAgentStream.js`（`tool.chunk` 流）、`hermes-fork/tools/terminal_tool.py`（`pty` 参数 + `process_registry`）、`electron/preload.js`（`gatewayRequest` 通用回传通道）
> 决策：终端渲染走 **xterm.js**；输入回传复用 **gateway 通用通道**；后端复用 Hermes 现有 **PTY + process 工具**能力，不新写执行器

---

## 1. 背景与目标

### 1.1 现状（已核实）
当前 ChatLayout 里 agent 的终端能力是「执行 + 只读渲染」，缺交互：

| 能力 | 现状 | 证据 |
|---|---|---|
| agent 执行 shell / PowerShell 命令 | ✅ 有 | `terminal_tool.py`（Popen / 多后端 / 后台任务） |
| agent 以 PTY 启动 TUI 程序 | ✅ 后端有 | `terminal_tool.py:2056` `pty: bool = False`；`process_registry.py:710` Windows 走 `winpty.PtyProcess`、Unix 走 `ptyprocess`；`_pty_reader_loop` 持续读输出 |
| 向运行中进程写输入 | ✅ 后端有 | `process_registry.py:1480` `write_stdin(session_id, data)` → `session._pty.write()` |
| 终端输出流式渲染 | ⚠️ 只读文本 | `useAgentStream.js:503` `tool.chunk` → `ToolCard` → `TerminalPanel`（逐行文本，无 ANSI） |
| 用户在 UI 里实时输入（键盘） | ❌ 无 | 无 xterm.js / ANSI 解析 / 输入回传 |

缺口根因：
1. **TerminalPanel 无 ANSI 转义解析**——TUI 程序（vim / htop / tmux / PowerShell 彩色输出）渲染成原始转义码乱码；
2. **无键盘输入通道**——用户无法向运行中的 PTY 喂键；
3. **无会话管理 UI**——PTY 会话的生命周期（启动 / resize / close）只存在于 agent 侧。

### 1.2 目标
1. agent 启动的 TUI / PowerShell 交互进程，在 ChatLayout 内以**真实终端**呈现（ANSI 色彩、光标、清屏正确）；
2. 用户可**直接敲键盘**与运行中进程交互（vim 编辑、菜单选择、PowerShell 交互提示符）；
3. 终端会话**生命周期可视**（启动/运行/结束），可手动关闭；
4. **复用现有后端能力**（PTY + process 工具 + gateway 通道），不引入新的执行引擎。

### 1.3 范围决策
- 渲染引擎：**xterm.js**（MIT，Electron 生态标准，ANSI/Unicode/IME 支持成熟），不使用自研 ANSI 解析器；
- 输入通道：复用 `preload.gatewayRequest` 通用回传（`terminal.read.respond` 同款机制），新增 `process.write` / `process.resize` 方法名；
- 输出通道：优先复用 `tool.chunk` 事件流；若 Hermes 有独立 `process.output` 事件，实现前抓包确认后二选一（见 §7 未知项）；
- 会话归属：以 Hermes `process_registry` 的 `session_id` 为唯一键，前端按 key 映射终端面板；
- 不新写后端执行器；不在渲染层引入 Node 依赖（xterm.js 纯前端包）。

---

## 2. 设计决策与理由

| 决策点 | 选择 | 理由 |
|---|---|---|
| 渲染引擎 | xterm.js | ANSI/光标/清屏/IME 全支持，免自研；MIT 协议；Web 端成熟组件，构建产物可进 vite |
| 输入回传通道 | gateway 通用通道（`process.write`） | `preload.gatewayRequest(method, params)` 已是现成 IPC → agui-server → Hermes 网关通道（`terminal.read.respond` 同款），零新增基础设施 |
| 输出事件源 | `tool.chunk`（优先） | Hermes 工具执行期间输出已按 chunk 流式转发到前端（`agui-server.js:755`），无需改 Hermes 事件面 |
| 后端执行器 | 复用 terminal_tool + process_registry | PTY、`write_stdin`、进程 registry 均已存在，只补"前端→网关"的透传与映射 |
| 面板形态 | 消息流内嵌「终端工具卡片」 | 与现有 ToolCard 视觉语言一致；用户偏好应用内 tab，不新开独立弹窗 |
| 多会话 | 每 PTY 会话一个 xterm 实例，按 session_id 索引 | 支持 agent 同时跑多个交互进程（如一个 vim + 一个 PowerShell），互不串扰 |

**为什么不用 web 终端服务（ttyd / Wetty）**：它们把终端放到独立 HTTP/WebSocket 服务，需要新增常驻进程与端口、跨进程会话管理，与现有"agui-server 单进程托管"架构冲突；xterm.js 直接消费现有 SSE 事件流，链路最短。

**为什么不扩展现有 TerminalPanel**：TerminalPanel 是纯 `<div>` 逐行渲染，补 ANSI 解析等于自研半个终端（光标定位、清屏、着色、滚动回退、IME、复制粘贴），成本与风险远高于直接引入 xterm.js。

---

## 3. 总体架构

```
┌─ React 前端 (ChatLayout / MessageThread) ────────────────────────┐
│                                                                  │
│   TerminalToolCard (新)                                          │
│   ├─ <XTerm> 实例  ──► onData(key)                               │
│   │      ▲              │                                        │
│   │      │ write(chunk) ▼                                        │
│   │   useTerminalSession(sessionId)                              │
│   │   ├─ 订阅 useAgentStream 的 tool.chunk / tool_end 事件流      │
│   │   └─ 调用 window.hermes.gatewayRequest("process.write",…)    │
│   └───────────────────────────────────────────────               │
└──────────────┬───────────────────────────────────────────────────┘
               │ ① preload.gatewayRequest (IPC 'gateway-request')
               ▼
┌─ electron/main.js ───────────────────────────────────────────────┐
│   gateway-request ──► agui-server /api/gateway 透传               │
└──────────────┬───────────────────────────────────────────────────┘
               │ ② Hermes AG-UI 网关方法调用 (process.write / resize)
               ▼
┌─ Hermes 后端 ────────────────────────────────────────────────────┐
│   terminal_tool(pty=True) ──► process_registry.spawn_local       │
│   ├─ Windows: winpty.PtyProcess (session._pty)                   │
│   └─ write_stdin(session_id, data) → pty.write(keys)             │
│         ▲                                                        │
│   pty 输出 │ _pty_reader_loop                                     │
│         └──► 工具执行期 chunk 输出                                │
└──────────────┬───────────────────────────────────────────────────┘
               │ ③ AG-UI CUSTOM 事件 (tool.chunk / process.output?)
               ▼
   agui-server.js:755 转发 → SSE → useAgentStream → TerminalToolCard
```

三条链路（与现有架构完全对称）：
1. **输出**：PTY 读循环 → 工具 chunk → AG-UI 事件 → SSE → `useAgentStream` → xterm `write()`（现有 `tool.chunk` 通道，前端只把"发给 ToolCard 的 chunks"同时喂给 xterm）；
2. **输入**：xterm `onData` → `gatewayRequest("process.write", { session_id, data })` → agui-server 网关 → Hermes `process_registry.write_stdin`；
3. **控制**：`process.resize`（窗口尺寸变化）、`process.close`（用户手动关闭）走同一条 gateway 通道。

---

## 4. 关键接口定义

### 4.1 前端 → 后端（gateway 方法，复用 `preload.gatewayRequest`）

```ts
// 已存在，无需改动签名；method 传入新方法名即可
window.hermes.gatewayRequest(method: string, params: Record<string, any>, timeout?: number): Promise<any>

// 新增方法名（Hermes 侧若已有同语义方法，直接用；否则在 acp_adapter 补薄包装）
gatewayRequest("process.write",  { session_id: string, data: string })   // 键盘输入（UTF-8 文本）
gatewayRequest("process.resize", { session_id: string, cols: number, rows: number }) // 终端尺寸
gatewayRequest("process.close",  { session_id: string })                 // 用户手动关闭会话
```

> 备注：Hermes 现有 `process` 工具族（poll / wait / close / write）语义一致，实现时优先检查网关侧是否已暴露同名方法，避免重复包装（见 §7-未知项①）。

### 4.2 后端 → 前端（AG-UI 事件，经 agui-server 转发）

```ts
// 复用现有事件（useAgentStream 已处理）
{ type: "CUSTOM", name: "tool.chunk", value: { toolCallId: string, chunk: string } }

// 若 Hermes 对交互进程有独立输出事件，则扩展 agui-server 转发（待验证，见 §7-未知项②）
{ type: "CUSTOM", name: "process.output", value: { session_id: string, chunk: string } }
```

前端映射规则：`tool.chunk` 按 `toolCallId` 关联到所属工具调用；若该工具调用是 `terminal` 且携带 `pty=true`，chunk 同时喂给对应 `TerminalToolCard` 的 xterm 实例。

### 4.3 前端组件划分

| 组件 | 职责 |
|---|---|
| `TerminalToolCard`（新） | 替代普通 ToolCard 渲染 `terminal` 工具的展开体：xterm 容器 + 会话状态条（运行中/已结束 + 关闭按钮）；折叠时仍显示摘要（工具名 + 首行输出预览） |
| `useTerminalSession`（新 hook） | 持有 xterm 实例生命周期：`open()` / `write(data)` / `onData→gatewayRequest` / `resize` 节流 / `dispose`；订阅 useAgentStream 事件源 |
| `MessageThread#ToolsRow`（改） | 识别 `toolName === "terminal"`（或带 `pty:true`）的工具消息，改渲染 `TerminalToolCard` |
| `useAgentStream`（改） | 将 `tool.chunk` 的原始流按 `toolCallId` 分发给终端会话订阅者（新增一个轻量事件订阅接口，不影响现有 ToolCard 路径） |

### 4.4 后端改动面（最小）

| 文件 | 改动 |
|---|---|
| `hermes-fork/acp_adapter/*`（或 Hermes 网关） | 确认/补齐 `process.write` / `process.resize` / `process.close` 网关方法（若已存在则零改动） |
| `electron/backend/agui-server.js` | 若 Hermes 有独立 `process.output` 事件，增加 CUSTOM 转发（1 个 case）；若复用 `tool.chunk` 则零改动 |
| `electron/preload.js` | 零改动（`gatewayRequest` 已是通用通道） |

---

## 5. 前端交互设计

### 5.1 形态
消息流内嵌卡片（与 ToolCard 一致的外壳：图标 + 工具名 + 状态），展开后是完整 xterm 终端：

```
┌──────────────────────────────────────────────┐
│ ⚙ terminal        pty · 运行中        [关闭] │  ← 会话状态条
├──────────────────────────────────────────────┤
│  $ powershell                                 │
│  Windows PowerShell                          │
│  Copyright (C) Microsoft Corporation.         │
│                                              │
│  PS C:\Users\lex> _                          │  ← xterm 实例（可交互）
│                                              │
└──────────────────────────────────────────────┘
```

### 5.2 交互规则
- **键盘**：xterm `onData` 直接回传（不经过 Composer），每键即发（对 PTY 是必须的，不能聚合）；
- **resize**：xterm `onResize`（fit 插件）节流 200ms 后 `process.resize`；
- **折叠/展开**：折叠保留会话运行（进程不死），只收起 xterm 视图；展开恢复；
- **关闭**：用户点「关闭」→ 先 `process.close` 优雅结束，进程退出后卡片落为「已结束」态（与 ToolCard 的 interrupted/completed 状态机一致）；
- **会话结束**：agent 的 `tool_end` 到达 → 卡片标记「已结束」，xterm 置为只读（仍可滚动回看）；
- **多会话**：同一轮内多个 PTY 会话各自一张卡，互不干扰。

### 5.3 与现有组件的边界
- Composer / MessageActions / TTS 等**全部不动**；
- 普通工具（file / web 等）仍走原 ToolCard 路径，只有 `terminal` 工具升级为 TerminalToolCard；
- 竖轨 / 结果区 tab 不新增入口（终端属于"对话过程"，留在消息流内）。

---

## 6. 分阶段实施

### M1 — 只读 ANSI 渲染（纯前端，无后端改动）
- 引入 xterm.js + `@xterm/addon-fit`（vite 正常打包）；
- 新增 `TerminalToolCard`：渲染 `terminal` 工具消息，`tool.chunk` 流喂 xterm（ANSI 正确显示）；
- 现状收益：**TUI / PowerShell 彩色输出立刻可读**（vim 打开文件、htop、PSReadLine 着色），即使还不能输入。
- 验证：让 agent 跑 `powershell` 输出彩色文本 / `vim`（只读观察渲染不乱码）。

### M2 — 键盘输入回传
- 新增 `useTerminalSession`：`onData → gatewayRequest("process.write")`；
- agui-server / Hermes 网关侧确认并接通 `process.write` 方法；
- 现状收益：**完整交互**——vim 编辑、菜单选择、PowerShell 交互提示符全部可用。
- 验证：用户在终端里输入 `dir` / 编辑文件并保存，agent 侧 `process(action="read"/"wait")` 拿到结果。

### M3 — 会话控制与打磨
- `process.resize`（fit 插件联动）、`process.close`（手动关闭）、多会话并列；
- 折叠保活、结束态只读、错误态（winpty 启动失败）提示；
- 复制粘贴、终端内文本选择（xterm 原生支持）。

---

## 7. 风险与未知

| # | 未知项 | 影响 | 处置 |
|---|---|---|---|
| ① | Hermes 网关是否已暴露 `process.write` / `resize` / `close` 方法 | M2 工作量（零改动 vs 补薄包装） | 实现 M1 后抓包 `gatewayRequest` 探活，先试 `process(action="write")` |
| ② | 交互进程输出是走 `tool.chunk` 还是独立 `process.output` 事件 | M1 事件接线方式 | 用一个 `pty=True` 的 `terminal` 调用实测，抓 SSE 事件名 |
| ③ | `winpty.PtyProcess` 在本机 Windows 的可用性（依赖 `winpty` 库是否随 venv 安装） | TUI 程序能否启动 | M2 前用 `scripts/` 下探针脚本本地验证 `pty=True` 启动交互命令 |
| ④ | `terminal_tool` 描述写的是 "Linux environment" | 工具对 Windows 的适配说明缺失 | 若 M2 验证 Windows 可用，顺手更新工具描述（PR 级改动） |
| ⑤ | 大输出量下 SSE + xterm 的背压 | 快速滚动的 TUI（如 `yes`）可能卡渲染 | xterm 自带行缓冲 + 滚动回退；必要时对 write 做 16ms 帧合并 |
| ⑥ | `tool_end` 后 Hermes 是否保留 PTY 会话 | 「折叠保活」是否成立 | M3 时验证；若进程随 tool_end 终止，则交互窗口只限工具执行期（可接受） |

---

## 8. 验收标准

1. 让 agent 执行 `terminal` 工具并 `pty=True` 启动 `powershell`，ChatLayout 中出现可交互 xterm 面板；
2. 用户可直接在面板内输入命令（如 `Write-Host "ok" -ForegroundColor Green`），彩色输出正确显示，进程收到输入并响应；
3. 启动 `vim`（或 `notepad` 类文本界面）可编辑并保存，ANSI/光标/清屏无乱码；
4. 面板折叠时进程继续运行，展开恢复；点关闭后进程结束、卡片进入「已结束」态；
5. 普通工具（file/web）的 ToolCard 渲染与行为完全不变；
6. 质量门：TDZ clean → vite build 通过 → multisession 测试全绿 → robocopy 部署 release → 缓存清理 → commit & push。

---

## 9. 不做的事（Non-goals）

- 不做通用终端 tab / 独立终端窗口（用户倾向应用内消息流，且与竖轨体系冲突）；
- 不引入 ttyd / Wetty / SSH 网关等外部终端服务；
- 不做前端自研 ANSI 解析器（xterm.js 全覆盖）；
- 不在 M1/M2 处理 agent 与用户同时竞争输入的并发策略（先到先得，M3 视需要再加锁）。
