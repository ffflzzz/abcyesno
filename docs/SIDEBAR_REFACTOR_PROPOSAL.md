# Sidebar 重构 + 长链任务面板（已实现）

## 实现日期
2026-07-25

## 改动概要

### 1. Sidebar 三 Tab 布局（方案 A）

**文件：** `src/components/Sidebar.jsx`（重写）

```
┌─────────────────────┐
│ 🎭 Abcyesno      ✕  │  Header（不变）
├─────────────────────┤
│ [💬 对话] [🔧 工作流] [⚡ 任务] │  ← Tab 栏（替代原搜索行）
├═════════════════════┤
│                     │
│  Tab 内容区          │  ← 按选中的 Tab 切换
│                     │
├─────────────────────┤
│ 🛒市场  ⚡技能  ⚙设置 │  Footer（不变）
└─────────────────────┘
```

| Tab | 内容 | 说明 |
|-----|------|------|
| **💬 对话** | 紧凑助手选择器 + 会话列表为主体 | 助手不再是大卡片，而是单行选择器；会话列表占主要空间 |
| **🔧 工作流** | workflow 卡片网格，每卡带「▶ 运行」按钮 | 点击运行 → 在后台创建独立任务 |
| **⚡ 任务** | 长链任务列表（进行中/已完成）+ 详情面板 | 参考 WorkBuddy/Codex 模式 |

### 2. 长链任务独立面板

**文件：** `src/components/TaskPanel.jsx`（新建）

核心设计：
- **任务与对话解耦** — 触发 workflow 后创建一个"后台任务"，主对话区继续正常聊天不受阻塞
- **任务生命周期**：`pending → running → completed / failed / stopped`
- **实时进度** — 通过 eventBus 订阅 `workflow.*` 事件更新任务状态、节点进度条、产物列表
- **持久化** — 任务列表存 localStorage（`abcyesno:tasks`），刷新不丢失
- **详情面板** — 选中任务后显示三子 Tab：进度时间线 / 产物预览 / 事件日志

数据流：
```
用户点击「▶ 运行」→ taskManager.createTask(workflowId)
  → 创建 task entry (status: pending)
  → 通过 onSend 发送 langgraph_agent 调用
  → 500ms 后 status → running
  → eventBus 收到 workflow.progress/artifact/complete
  → 更新 task.events / task.artifacts / task.status
  → TaskPanel 重渲染（进度条、状态标签、产物数）
```

### 3. App.jsx 接入

- 新增 `useTaskManager` hook 调用（在 ChatShell 内，handleSend 之后）
- `taskManager` 作为 prop 传给 `<Sidebar taskManager={taskManager} />`

### 4. CSS

**文件：** `src/styles/index.css`（追加 ~300 行）

新增样式族：
- `.sidebar-tabs` / `.sidebar-tab` — Tab 栏
- `.sidebar-body` / `.sidebar-tab-content` — 内容区
- `.switcher-*` — 紧凑助手选择器
- `.sessions-header` / `.sidebar-sessions-area` — 会话区
- `.wf-card-*` — 工作流卡片网格
- `.task-panel` / `.task-card` / `.task-detail` — 任务面板全套
- `.task-progress-bar` — 进度条

## 与原方案 A 的差异

| 原方案 | 实现 |
|--------|------|
| 2 个 Tab（对话/工作流） | **3 个 Tab**（对话/工作流/任务）— 用户要求加长链任务面板 |
| 无任务概念 | **完整任务系统** — TaskPanel + useTaskManager hook |
| Footer 不变 | Footer 不变 |
| localStorage 记忆 Tab | 已实现 |

## 修复的问题

- **P0 BUG 修复**：工作流列表不再重复渲染（旧版 manifests 平铺在 sidebar-section 里可能被其他逻辑重复渲染；新版只在 workflow tab 渲染一次）
- **P1 修复**：助手/工作流/会话不再平铺竞争空间，Tab 隔离

## 不做的事（同原方案）

- 不改变 Sidebar 宽度（260px）
- 不做拖拽排序
- 不改 Footer 结构
- 不移除右键菜单功能
