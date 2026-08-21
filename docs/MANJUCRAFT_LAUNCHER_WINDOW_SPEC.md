# 「漫剧go」启动台入口 — 设计 spec（已废弃独立窗口方案）

日期：2026-08-21
作者：von（⚡）
关联：MANJUCRAFT_AGENT_SPEC / RESULT_PANEL_SPEC

> ⚠️ **本 spec 描述的「独立 Electron 窗口」方案已废弃。**
> 实测 `panel=studio` 独立窗口黑屏不可用，用户明确反对弹独立窗口。
> 现改为：**点「漫剧go」图标 → 在当前窗口标签栏「新增一个 tab」承载工作台**
> （`launcher.openMode: "newTab"` + `App.openAppAsNewTab`）。
> 独立窗口相关代码（`createAppWindow` / `open-app-window` IPC / `resolveManifestName` /
> preload `openAppWindow` / main.jsx `panel=studio` / DetachedApp `mode="studio"`）已全部回退。

---

## 1. 问题（原始动机，保留备查）

启动台（`src/components/Launcher.jsx`）目前是紧凑 app-grid，点 "漫剧go" 图标 →
`homepageApps[].onClick` →
`openApp({ type:"studio", workflowId:"manjucraft_agent" })` →
**替换当前 Launcher tab 成一个 studio tab**，里面塞 `ChatLayout` + `ResultPanel(StudioWorkbench)`。

痛点：

- `ChatLayout` 主列 max-width 760/980 像素 + `result-panel` 默认折叠 → 任务进度、产物面板被
  挤压成窄竖条
- 想看到完整 WorkflowGraphPanel + 分镜图 + TTS/剪辑时间线，必须手动展开
  ResultPanel、调宽度、调 detail tab → 反复来回
- 用户在做长任务时，希望把它"摊到大屏另一窗口"看着跑，而不是塞在原窗口的 tab 里

## 2. 目标

点启动台图标 **「漫剧go」** → **直接拉起一个独立 Electron 窗口**，里面渲染完整的
`App`(ChatLayout + StudioWorkbench) —— 但只属于这个 manifest，自然铺满新窗口的宽高。

约束：

1. **手动触发**：不抢焦点，不在任务开始时自动弹。点图标才弹。
2. **同一个后端**：窗口复用主窗口已起好的 AG-UI bridge + Hermes gateway（不重起后端进程）。
3. **不破坏 launcher app 的「对话」入口**：那个继续走"替换当前 Launcher tab"。
4. **可重复触发**：点同一图标第二次聚焦已有窗口（同一 Chrome tab 去重 UX）。
5. **可关闭**：用户关掉放大窗口不会影响主窗口的任何状态。

## 3. 设计选项（已征求用户意见，已选定）

- 形态：**A. 独立 Electron 窗口**（基于现有 `detachResultPanel` 机制镜像）
- 触发：**手动点图标触发**（不做自动弹出，不加 launcher badge）

## 4. 架构

### 4.1 复用现有 detach 模式

参考：`electron/main.js::createDetachedPanelWindow` 与
`src/DetachedApp.jsx`。

```
「漫剧go」icon
  ↓ (window.hermes.openAppWindow)
main.js → ipcMain.handle('open-app-window')
  ↓ BrowserWindow 加载
index.html?panel=studio&workflow=manjucraft_agent
  ↓ main.jsx isStudioPanel() 分支
Bootstrap or DetachedApp → 完整 App(workflowId=manjucraft_agent, ...)
```

### 4.2 改动清单

| 文件 | 改动 |
|---|---|
| `electron/main.js` | 镜像 `createDetachedPanelWindow` 加 `createAppWindow({ workflowId })`；注册新 IPC handler `open-app-window`；窗口 title `'Abcyesno · {manifest.name}'`、`min-width:1024 min-height:720`、默认 `width:1280 height:860`；re-use 去重 key = `app={workflowId}` |
| `electron/preload.js` | 在 `contextBridge.exposeInMainWorld('hermes', ...)` 加 `openAppWindow: (opts) => ipcRenderer.invoke('open-app-window', opts \|\| {})` |
| `src/main.jsx` | `isDetachedPanel()` 扩为 `parseBootMode()`：返回 `'main' \| 'studio'`；当 `studio` 时返回 `<DetachedApp mode="studio" workflowId=... />` |
| `src/DetachedApp.jsx` | 新增 `mode === 'studio'` 分支：渲染**完整 App** 而非裸 ResultPanel；Bootstrap 等同主窗口；从 URL 取 `workflowId` 注入 `selectedWorkflowId` 初始值；自动选中"进行中"任务（`TaskPanel` 选第一条 `running`），无则停在 StudioWorkbench 表单页 |
| `src/App.jsx` | 检测 `window.location.search` 含有 `panel=studio` 时 → `setSelectedWorkflowId(workflowId)` + 强制 `setResultPanelOpen(true)` + 强制 `setResultPanelCollapsed(false)`；在 `homepageApps` 里把"漫剧go"（来源是 `launcherApps`）的 `onClick` 改成调用 `window.hermes.openAppWindow({ workflowId })` 而非 `openApp(...)`；保持"对话"入口不变 |
| `electron/main.js` `restoreSession`/App 关闭 | 窗口关闭时只清自身 `allWindows`，不动主窗口的 selectedWorkflowId / task panel state |

### 4.3 行为细节

**「漫剧go」图标点击流程**

1. `homepageApps[manjucraft_agent].onClick` → `window.hermes.openAppWindow({ workflowId:"manjucraft_agent" })`
2. 主进程 IPC handler `open-app-window`：
   - 遍历 `allWindows`：命中 `__appWindowKey === app={workflowId}` → `restore()` + `focus()` + `{ reused: true }` 返回
   - 否则 `createAppWindow({ workflowId })` → 设 `__appWindowKey` → `{ reused: false }`
3. 主窗口状态完全不动，launcher 图标不被替换为 tab（区别于"对话"入口）
4. 新窗口加载 `index.html?panel=studio&workflow=manjucraft_agent`
5. `main.jsx::parseBootMode()` 识别 `panel=studio` → 渲染 `<DetachedApp mode="studio" workflowId=... />`
6. `DetachedApp` 等同 main.jsx 的 `Bootstrap`：等 `aguiPort`，调 `initContract(aguiPort)`
7. 然后渲染 `<App aguiPort={port} initialWorkflowId={workflowId} studioEntry />`
8. `App` 收到 `initialWorkflowId`：useEffect 强制 `setSelectedWorkflowId(workflowId)` + 同步打开 ResultPanel；并订阅 `taskManager.tasks` 找到第一条 `status==='running'` 的 → `taskManager.onSelectTask(id)`

**窗口关闭 / 重开**

- 关闭新窗口 → `__appWindowKey` 随之回收（`allWindows.delete(win)` 时不存 ref） → 下次可重新打开
- 主窗口的 tab/result panel / task panel state 完全不受影响
- 如果用户希望"关闭=退出整个应用"，目前默认 NO（独立窗口关闭不影响主窗口）

**误差 / 鲁棒性**

- 同一图标被双击：第二次 IPC handler 直接命中现有窗口 → focus
- 主窗口后端没起好，新窗口同时启动：`DetachedApp` 走 polling 复用主窗口进程，与现有 detached-result 一致；不会出现两个 hermes 进程
- manifest 不存在或 workflowId 写错：App 收到非法 ID 会 fall through 到空表单页；不抛错

### 4.4 与现有机制的对齐

- 复用 `allWindows`、`wireDevToolsHotkey`、`render-process-gone`、`console-message`
- 复用 `path.join(__dirname, '..', 'dist', 'index.html')` 作为加载目标
- 复用 `mainWindow.__detachKey` 同样的 `__appWindowKey`（不冲突，存的是不同 string）
- 复用 `<App>` 的 selectedWorkflowId / ResultPanel 状态机；现有 ChatShell 不动
- 不修改 `manifest.json` —— 「漫剧go」的 launcher 行为由前端 `homepageApps` 在
  App.jsx 中根据 "此 key 来自 launcherApps" 的属性判定，或新增 manifest 字段
  `launcher.openMode: 'tab' | 'window'`，默认 `'tab'`；manjucraft_agent 显式声明
  `'window'`，未来其他 agent 想用 tab 还是窗口完全 data-driven

**决定**：用 manifest 字段方式扩展（更符合"compile panel / 配置驱动"约定），字段名
`launcher.openMode`，枚举 `'tab' | 'window'`，默认 `'tab'`。`launcherApps` codegen
把这个字段带过来，`gen-contract.mjs::buildLauncherApps` 写入；`homepageApps` 在
App.jsx 根据 `openMode` 派发 onClick。

## 5. UI / 交互细节

### 5.1 启动台图标

不变，仍然是 `Launcher.jsx` 的彩色圆角方形 + 「漫剧go」文字。

- 不加任何 "运行中" badge（用户在 FAQ 里不愿自动抢焦点）
- 不改 hover 态

### 5.2 新窗口

- `title`：`Abcyesno · {manifest.name}`（例：`Abcyesno · 短剧制片工作台`）
- `icon`：复用 `electron/bach-icon.png`
- `backgroundColor`：`#0f1419`（与启动页 spin 颜色一致）
- 默认尺寸 `1280×860`，最小 `1024×720`
- 加载完 ready-to-show 时 `.center()` 并 `.show()`

### 5.3 App 内表现

- `studioEntry` 时：ResultPanel **不折叠**、自动选中 workflowId；如果该 workflow 已有
  `running` 任务，自动选中第一条；无任务则停在 StudioWorkbench 的 script phase
- 由于打开方式变了，**主窗口里不会冒出新 tab**，原 launcher tab 保持原状

## 6. 验收

1. **TDZ clean**：build 前 `node scripts/check-tdz.js`
2. **vite build**：无 TS / 无 lint warn
3. **多会话回归**：`node scripts/test-multisession/run.mjs` 26/26
4. **手动启动 + 关闭 Abcyesno.exe 重新打开**
5. **集成测试**：
   - 主窗口点启动台「漫剧go」→ 新 Electron 窗口出现
   - 新窗口内显示 StudioWorkbench 的表单页
   - 用户在主窗口发聊天触发 manjucraft 任务 → 主窗口 / 新窗口 都能看见任务进度
   - 关掉新窗口 → 主窗口 state 完全不变
   - 再点一次「漫剧go」→ IPC handler reuse 路径命中 → 已有窗口 focus 而不是开新窗口
6. **dev DevTools**：`console-message` 走到 `log('app-window', ...)` 通道
7. **commit + push**：commit message 形如
   `feat(launcher): pop manjucraft into independent window via open-app-window IPC`

## 7. 后续可能的衍生（不在本次范围）

- launcher 图标 "运行中" badge（运行中 + 任务数 badge）+ click-through 自动 focus 已开窗口
- 多窗口并行：未来如果加多个 manifest 弹窗，键加 sessionId / tab 区分即可
- 窗口位置记忆：electron `electron-store` 或 `app.getPath('userData')/windowState.json`
  持久化窗口 bounds
