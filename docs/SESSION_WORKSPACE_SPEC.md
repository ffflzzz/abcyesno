# 会话工作空间（Session Workspace）设计 Spec

> 2026-08-22 · 目标：让每个「对话」会话可绑定一个本地文件夹，agent 的文件读写 / 命令执行默认在该目录内进行——对齐 WorkBuddy 的 workspace 选择器体验。

## 一、可行性结论（已核实源码）

**Python 侧零改动。** Hermes 网关原生支持整套机制：

| 能力 | 位置 | 说明 |
|---|---|---|
| `session.create { cwd }` | `hermes-fork/tui_gateway/server.py` L4926-5020 | 创建会话时指定工作目录，L5020 `_register_session_cwd()` 注册 override |
| `session.cwd.set { session_id, cwd }` | 同文件 L5665 | 对**已有**会话换目录（要求非 running 状态） |
| 文件工具相对路径解析 | `tools/file_tools.py` L105 `_resolve_path` → L207-223 `_registered_task_cwd_override` | 相对路径自动落到注册的 cwd |
| 终端工具 cwd | `tools/code_execution_tool.py` L676 | `overrides.get("cwd") or config["cwd"]` |

Node 桥现状：`electron/backend/agui-server.js` `ensureHermesSession()`（L307-347）的 `createParams` 只传 `close_on_disconnect/model/skill_id`，未透传 cwd —— 这是唯一需要补的桥接点。

Electron 已有 `select-file` IPC（`electron/main.js` L1120-1134），复制改 `properties: ['openDirectory']` 即得文件夹选择器。

## 二、数据模型

- `storage.js` 的 session 是纯 JSON 存储，`updateSession(id, patch)`（L157-170）为通用合并 —— 直接给 session 记录新增 `workspaceDir: string | null` 字段，**无需改 schema**。
- 最近使用列表（recentWorkspaces，上限 8 个）存前端 localStorage 即可，不入后端。

## 三、UI 设计（复刻 WorkBuddy 交互）

入口：Composer 底部 pill 行（现有「默认权限」「agnes-2.5-flash」旁）加「📁 工作空间」pill + `.composer-popover` 弹层：

```
┌──────────────────────────────┐
│ 🔍 搜索/显示当前绑定文件夹      │
│ ✅ abcyesno-v8               │
│ ＋ 打开本地文件夹…            │  ← window.hermes.selectDirectory()
│ 📂 最近：proj-a / proj-b …   │
│ ⃠ 不使用工作空间              │
└──────────────────────────────┘
```

- 未绑定时 pill 显示「工作空间」灰字；绑定后显示文件夹名（取 basename）。
- 绑定状态为 **per-session**，跟随会话持久化（storage），切会话自动切换显示。
- 新会话默认「不使用工作空间」，由用户手动选择（v1 不做全局默认）。

## 四、数据流

1. 用户点击「打开本地文件夹…」→ `window.hermes.selectDirectory()` → 返回绝对路径。
2. 前端调 `POST /api/session/workspace`（新增）→ `storage.updateSession(sessionId, { workspaceDir })`。
3. 发消息时 `useAgentStream.js sendMessage` 的 `forwardedProps` 附带 `workspaceDir`。
4. `agui-server.js handleAgentRun`：
   - `resolveRunContext()` 读出 `forwardedProps.workspaceDir`；
   - `ensureHermesSession()` 三分支：
     - 新建 Hermes 会话 → `createParams.cwd = workspaceDir`；
     - 已有映射且 `workspaceDir` 与上次不同 → `client.request('session.cwd.set', {...})`；
     - 为空 → 不动（保持 HERMES_HOME 兜底行为）。
5. agent 执行 read/write/terminal 工具时，相对路径自动解析到该目录。

## 五、边界与安全说明

- **v1 只做"相对路径基准"，不做沙箱**：Hermes 工具本身允许绝对路径访问文件夹之外的位置，cwd 仅决定相对路径落点。后续可在 system prompt 注入约束（"只在工作空间内操作"）或加工具层白名单，另立任务。
- `session.cwd.set` 要求会话非 running：running 中点绑定则先本地保存，下次 run 生效（或提示"运行结束后生效"）。实现上简单处理为：run 开始前 diff 再 set，天然规避 busy 错误。
- 中文路径 / 空格 / 盘符：路径仅作为 JSON-RPC 参数与 env 传递，无 URL 编码问题（区别于 renderer 内 `file://` 子资源，不经过 `abcyesno-local://` 协议）。

## 六、改动清单（文件级）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `electron/main.js` | 新增 `ipcMain.handle('select-directory')`（openDirectory，~10 行） |
| 2 | `electron/preload.js` | 暴露 `window.hermes.selectDirectory(options)` |
| 3 | `electron/backend/agui-server.js` | ① `resolveRunContext` 读 `workspaceDir`；② `ensureHermesSession` 补 createParams.cwd / cwd.set 分支；③ 新增 `POST /api/session/workspace` |
| 4 | `src/hooks/useAgentStream.js` | `sendMessage` payload `forwardedProps.workspaceDir` 透传 |
| 5 | `src/components/Composer.jsx` | 工作空间 pill + popover（复刻权限模式 popover 模式，L708-737 参考） |
| 6 | `src/App.jsx` | per-session workspace 状态管理（走 permissionMode 同款通路） |

预计总量 ~200 行，全部在 Node/前端层。

## 七、验收

1. 质量门照常：`node scripts/check-tdz.js` → `npx vite build` → `node scripts/test-multisession/run.mjs`（37/37，workspaceDir 默认 undefined 时行为零变化）→ dist 镜像 + 杀进程重启 → commit & push。
2. 手工测试 prompt（绑定某文件夹后）：
   > 「在我当前的工作空间里创建 notes/hello.txt，内容写 today，然后执行 dir 列出根目录文件」
   预期：文件落在所选文件夹内；`dir` 输出的是该文件夹内容。
3. 边界用例：切换会话后 pill 显示各自绑定；解绑后再发消息回落 HERMES_HOME；running 中改绑不报错。
