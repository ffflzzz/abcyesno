# 右侧结果区 功能 Spec（Abcyesno）

> 版本：v0.2（草稿，剔除 Office 预览与云端分享）
> 关联文档：`PRD.md`、`UI_UX_SPEC.md`、`LANGRAPH_CONTRACT_SPEC.md`、`FRONTEND_RENDERING_LAYER.md`
> 范围声明：本文覆盖"右侧结果区"的**预览 / 验收 / 产物管理**类功能。**浏览器自动化（Playwright agent 驱动）、Office 预览、云端分享本期均不做**，详见 §8 非目标。

---

## 1. 目标与原则

- **G1 不离场验收**：用户在不离开当前任务对话的前提下，查看、预览、验收 agent 产出（文件 / 文档 / 网页 / 变更）。
- **G2 复用契约层**：新增工作流仍走 L1–L5 契约，产物自动进结果区，**零前端改动**。
- **G3 预览只读**：所有预览行为无副作用（不写远端状态、不提交、不爬取），与"自动化"划清安全边界。
- **G4 跨产物统一入口**：网页、图片、视频、PDF、Markdown 在结果区有统一查看路径（Office 预览与云端分享本期不做，见 §8）。

---

## 2. 信息架构

右侧常驻 dock 面板（默认宽度 ~420px，可折叠 / 最大化 / 外开新窗口）。

```
┌─ ResultPanel (dock 容器) ──────────────┐
│ [折叠]  [最大化]  [外开新窗]            │
│ ┌─ Tabs ──────────────────────────────┐ │
│ │ 概览 │ 产物 │ 文件 │ 变更 │         │ │
│ └──────────────────────────────────────┘ │
│ ┌─ 内容区（随 tab 切换）────────────┐ │
│ │                                       │ │
│ └──────────────────────────────────────┘ │
│ [刷新]                                  │
└───────────────────────────────────────────┘
```

| Tab | 名称 | 一句话职责 |
|---|---|---|
| T1 | 概览 Overview | 一屏速览：工作空间文件树 + 网页预览 + 变更摘要 |
| T2 | 产物 Artifacts | 任务生成的可交付物，按类型预览 |
| T3 | 文件 Files | 当前工作空间完整文件树 + 内容查看 |
| T4 | 变更 Changes | 本次任务的文件改动 diff，验收前确认 |

---

## 3. T1 概览（Overview）

### 3.1 工作空间文件树
- 渲染 `HERMES_HOME` 下的文件树（默认根 = `HERMES_HOME`；可切到项目根 `abcyesno-v8/`）。
- 点击文件 → 在 T3 打开内容查看。
- 支持展开/折叠、搜索过滤。

### 3.2 网页预览（**只读**）
- 内嵌 Electron `<webview>`，`src` 指向：
  - 本地 Web 应用：`http://127.0.0.1:<aguiPort>` 或 ad-hoc 本地服务；
  - 生成的静态 HTML：`file://<dist>/index.html` 或产物路径。
- 顶部控件：**地址栏**（只读展示当前 URL，不含输入提交）、**刷新**。
- **不做**：地址栏输入跳转、agent 自动导航、点击/表单填写（即无副作用）。
- webview 须 `contextIsolation` + 独立 partition，禁止访问主应用 DOM/IPC。

### 3.3 变更摘要
- 展示本次任务改动文件数 + 各文件 +/- 行数概要，点击跳 T4。

---

## 4. T2 产物（核心）

### 4.1 来源（统一汇聚）
| 来源 | 通道 | 现有基础 |
|---|---|---|
| 契约工作流产物 | `contract/eventBus.js` 的 `emitContractEvent(runId, {type:'artifact', ...})` | `eventBus.js` 已存在；需确认触发点（见 §7.3） |
| 上传/生成文件 | `HERMES_HOME/uploads/<sessionId>/` | `upload-file` IPC 已写这里 |
| 前端构建产物 | `dist/` | 现有 |
| 会话内联产物 | session messages 中的 artifact 标记 | 待定义 |

### 4.2 卡片列表
- 复用 `src/components/ArtifactCard.jsx` 渲染每个产物（标题、类型图标、时间、来源 workflow）。
- 列表按时间倒序；支持按类型筛选。

### 4.3 按类型预览（分发器 `ArtifactPreview.jsx`）
| 类型 | 渲染方式 |
|---|---|
| HTML / 网页原型 | `<webview>`（同 §3.2 只读策略） |
| Markdown | `react-markdown` + `remark-gfm`（与 `MessageThread` 同套） |
| PDF | Chromium 原生 `<webview>` 渲染（无需额外依赖） |
| 图片（png/jpg/gif/webp） | `<img>` |
| 视频（mp4/webm） | `<video controls>` |

> 注：Office（docx / xlsx / pptx）预览与云端分享本期不做，见 §8。

### 4.4 下载 / 另存
- 产物支持"下载到本地"（`shell.showItemInFolder` / `dialog.showSaveDialog`）。

---

## 5. T3 文件（Workspace Files）

### 5.1 文件树
- 新增 IPC `list-workspace({root?, path?})` → 返回目录树（名称、类型、mtime）。
- 复用 `window.hermes` 调用约定（main 进程 `ipcMain.handle`）。

### 5.2 内容查看
- 新增 IPC `read-file({path})` → 返回文本/二进制元信息。
- 文本类（代码/md/json/py）按扩展名着色（可复用任意高亮库或 Monaco read-only）。
- 二进制/大型文件 → 提示用系统程序打开。

### 5.3 已开文件切换
- 顶部 tab 栏展示当前打开的文件，点击切换，不离开任务。

---

## 6. T4 变更（Changes）

### 6.1 改动文件列表
- 任务开始后快照工作空间，结束/实时计算差异。
- 列表展示：文件路径、状态（新增/修改/删除）、+/- 行数。

### 6.2 行级 diff
- 选中文件 → 展示具体 diff。
- 渲染方案二选一：
  - **A. Monaco Editor diff 模式**：体验好，但增加包体（~数 MB）。
  - **B. 轻量 diff 库**（如 `diff` + 自绘）：体积小，适合便携版。
  - 建议：便携版先走 B，后续评估 A。

### 6.3 验收前确认
- 变更视图是"接受结果前最后一道检查"，代码/脚本类任务优先在此确认。

---

## 7. 数据与 IPC 契约

### 7.1 新增 IPC（main.js 实现）
| 方法 | 入参 | 返回 |
|---|---|---|
| `list-workspace` | `{root?, path?}` | 目录树 JSON |
| `read-file` | `{path}` | 文本内容 / 元信息 |

### 7.2 可复用现有 IPC
- `open-data-dir`、`select-file`、`upload-file`（见 `electron/main.js`）。

### 7.3 Artifact 汇聚触发点（待补，重要）
当前 `contract/eventBus.js` 提供 `emitContractEvent`，但**无 workflow.* 产物的调用方**（前轮已确认：`src/` 内仅定义、无 `emitContractEvent` 调用）。
- 若走 CopilotKit：`agui-server.js` 的 CUSTOM 事件分支需 `emitContractEvent`（经 IPC 或共享模块）。
- 若走自建 `useAgentStream.js`：其 `handleCustom` 需加 `workflow.artifact` → `emitContractEvent` 分支（前轮已论证此路径存在，一行级改动）。
- **本 spec 假设产物最终经 `eventBus` 汇入**，具体触发点由接入契约时定。

---

## 8. 非目标（本期不做）

- ❌ **浏览器自动化 / Playwright agent 驱动**：不内置"agent 主动导航、点击、填表、执行 JS"的浏览器。网页预览仅保留**只读渲染**。（理由：安全边界、体积、稳定性，见对话记录。）
- ❌ **Office 预览（docx / xlsx / pptx）**：本期不做。docx→html 转换、xlsx/pptx 渲染均无成熟轻量方案；如需再评估。
- ❌ **分享到云端**（我的云端网盘 / 腾讯文档 / ima / 乐享）：本期不做，依赖对应连接器启用。
- ❌ 实时协同编辑（多人同时改）。
- ❌ 产物版本历史 / 回滚。

---

## 9. 组件映射（现有 `src/`）

| 新增 / 复用 | 文件 | 说明 |
|---|---|---|
| 新增 | `src/components/ResultPanel.jsx` | dock 容器 + tabs + 刷新 |
| 新增 | `src/components/ArtifactPreview.jsx` | 按类型分发预览（§4.3） |
| 新增 | `src/components/WorkspaceTree.jsx` | 文件树（§5.1） |
| 新增 | `src/components/ChangeDiff.jsx` | diff 视图（§6） |
| 复用 | `src/components/ArtifactCard.jsx` | 产物卡片 |
| 复用 | `src/contract/eventBus.js` + `useContractEvents.js` | 产物事件源 |
| 复用 | `ChatLayout.jsx` / `App.jsx` | 挂载 `<webview>` 与 `ResultPanel` |
| 主进程改 | `electron/main.js` | 加 §7.1 IPC |
| 无需改 | `electron/backend/hermes-runner.js` | 后端无关 |

挂载点建议：`App.jsx` 的 `ChatShell` 右侧并行渲染 `<ResultPanel>`，与 `ChatLayout` 互不阻塞。

---

## 10. 验收标准（Acceptance）

- [ ] AC1：任务生成 Markdown / 图片 / HTML 产物后，T2 自动出现对应卡片，点击可正确预览。
- [ ] AC2：T3 能列出 `HERMES_HOME` 文件树，点击文本文件可读内容。
- [ ] AC3：T4 对代码类任务展示改动文件列表 + 行级 diff。
- [ ] AC4：网页预览 `<webview>` 仅渲染、无地址栏提交、无 agent 导航；partition 隔离，不可访问主应用 IPC。
- [ ] AC5：新增 workflow（manifest + LangGraph agent）后，其产物自动进 T2，**前端零改动**（契约 v1 前提闭环）。
- [ ] AC6：面板可折叠 / 最大化 / 外开新窗，不阻塞对话。

---

## 11. 开放问题

1. **diff 方案**：Monaco（体验好、包大）vs 轻量库（便携优先）？便携版倾向轻量。
2. **webview 隔离粒度**：何时允许 `file://`、何时仅 `http://127.0.0.1`？需防 `file://` 越权读盘。
3. **产物持久化**：会话关闭后产物是否保留在 `HERMES_HOME`？与 session 生命周期如何绑定？
4. **变更基线**：diff 的"前"快照在何时打（任务开始 / 首次写文件）？
