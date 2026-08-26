# Tab Tear-off（标签页拖出独立窗口）— 设计 spec

日期：2026-08-26
作者：von（⚡）
关联：MANJUCRAFT_LAUNCHER_WINDOW_SPEC / RESULT_PANEL_SPEC / DetachedApp

> 状态：**草案（未实施）**。本文定义"把标签页从主窗口拖出、变成独立 Electron
> 窗口"的交互与架构，供实施前评审。与已废弃的「点漫剧go 图标自动弹独立窗口」
> 方案**不是一回事**——见 §1.3。

---

## 1. 背景

### 1.1 需求来源

用户："tab 可以拖动交换位置，和拖出来成为一个独立窗口吗？就像浏览器里的标签页的行为"

已实现：**拖动交换位置**（commit `4868b96` + 修复 `328f2f1`）。
待实现：**拖出独立窗口**（tear-off）。

### 1.2 当前标签页架构（上下文）

- `App.jsx` 内 `const [tabs, setTabs] = useState(...)`（line ~1094）是唯一的 tab 源。
- 每个 tab 是一个对象：`{ id, type, title, icon, iconSrc, workflowId, assistantId,
  browserUrl, taskId, runId, studioKey, resultOpen, ... }`。
- tab 有 4 种 `type`：`homepage`（启动台）、`chat`（对话）、`studio`（工作台，
  需要 manifest）、`browser`（内置 webview 外链）。
- 激活 tab 用 `activeTabId`；窗口渲染逻辑按 `activeTab.type` 分支（browser / studio /
  default=chat+homepage）。

### 1.3 与已废弃方案的边界（重要）

`docs/MANJUCRAFT_LAUNCHER_WINDOW_SPEC.md` 记录过：**「点图标自动弹独立窗口」已废弃**，
原因是 `panel=studio` 独立窗口黑屏 + 用户明确反对"弹独立窗口"。

本次 tear-off 与它**本质不同**：

| 维度 | 已废弃（自动弹） | 本次（tear-off） |
|---|---|---|
| 触发 | 点图标自动弹 | **用户手动把 tab 拖出窗口** |
| 用户预期 | "我不想要多窗口" | "我主动要把这个 tab 拆出来" |
| 黑屏风险 | 独立窗口渲染完整 App（重） | 复用已验证的 detach-result 机制（轻） |

**结论**：tear-off 不复活旧方案；它复用 `createDetachedPanelWindow` 那套已经跑通的
窗口创建路径，只是把"结果面板"泛化成"任意 tab"。

---

## 2. 目标

用户把任意 tab（homepage / chat / studio / browser）从主窗口顶部标签栏拖出一定距离 →
- 该 tab **从主窗口移除**，作为**独立 Electron 窗口**继续渲染，铺满新窗口
- 新窗口复用主进程已就绪的 AG-UI bridge / Hermes 后端（**不重启后端进程**）
- 关闭新窗口时行为可预期（见 §4.3）

约束：

1. **手动触发**：仅拖拽手势触发，无自动弹出。
2. **同后端**：新窗口 `window.hermes` 走同一个 preload，aguiPort 从主进程共享。
3. **可重复拖出**：同一 tab 已 tear-off 后再拖不会产生第二个重复窗口（复用 `__detachKey`）。
4. **不破坏 tab 内其他功能**：拖动交换位置（已有）与 tear-off 手势互不干扰。

---

## 3. 设计选项（待用户定夺）

### 3.1 触发判定

- **A. 拖出 TopBar 垂直下方 X px 即触发**（推荐，简单）：`onDragEnd` 里比较
  `event.clientY > topbarBottom + 60`，超过就 tear-off，否则按普通 reorder 处理。
- B. 拖到屏幕边缘才触发：Chrome 的"拖到显示器边缘"行为，实现复杂，先不做。

### 3.2 支持哪些 tab 类型（阶段划分）

- **Phase 1（MVP）**：只支持 `browser`（webview 外链，状态最简单，`browserUrl` 一个字段即够）。
- Phase 2：`studio`（工作台，需 `workflowId` + manifest）。
- Phase 3：`chat` / `homepage`（状态最重：消息历史、session 绑定、多流并发）。

### 3.3 关闭独立窗口后 tab 的去向（关键，需定夺）

- **A. 丢弃**（最简单）：关掉就没了，用户可重新在主窗口打开。符合"临时拆出来看"的心智。
- B. **回主窗口**：关窗后 tab 自动回到主窗口标签栏（需跨窗口状态回传，复杂）。
- C. 丢进一个"已分离"列表，可手动拖回。

> 建议 Phase 1 用 **A**，后续按需升级 B。

### 3.4 跨窗口状态管理（架构核心，需定夺）

- **A. 每窗口独立 tab 列表**（推荐，Phase 1）：主窗口 `tabs` 里移除该 tab；独立窗口
  用**启动参数**自建一个单 tab 列表。两端互不实时同步。简单、可预测。
- B. 主进程统一状态仓库（`ipcMain` 存 tabs，两端订阅）：复杂，但支持"关窗回主窗口"。
- C. 独立窗口渲染 `App` 的完整副本：最重，接近旧方案，黑屏风险回归。

> 推荐 Phase 1 用 **A**，与"关闭即丢弃"（§3.3-A）配合，实现代价最低。

---

## 4. 架构（Phase 1：browser tab tear-off）

### 4.1 数据流

```
用户拖 browser tab 出 TopBar
  ↓ TabBar onDragEnd 检测 clientY 越界
  ↓ App.tearOffTab(tab)
  ↓ window.hermes.tearOffTab({ type:'browser', title, browserUrl, ... })
main.js ipcMain.handle('tear-off-tab')
  ↓ 复用 createDetachedPanelWindow 的窗口模板，改加载参数
  ↓ 新建 BrowserWindow → 加载 index.html?panel=tab&type=browser&...
  ↓ DetachedApp mode="tab" 渲染 <BrowserPanel fullscreen browserUrl=... />
  ↓ 源窗口 App 里 setTabs 移除该 tab
```

### 4.2 关键改动点

1. **TabBar.jsx**：`onDragEnd` 里判定 tear-off 触发条件，新增 `onTearOff(tab)` 回调。
2. **App.jsx**：`tearOffTab(tab)` —— `setTabs(prev => prev.filter(t => t.id !== tab.id))`
   + 调 preload IPC。注意 `closeTab` 有"至少留一个 tab"保护，tear-off 同理。
3. **preload.js**：`tearOffTab: (payload) => ipcRenderer.invoke('tear-off-tab', payload)`。
4. **main.js**：`ipcMain.handle('tear-off-tab', ...)` —— 泛化 `createDetachedPanelWindow`
   的 URL 参数（`panel=tab` + tab 序列化），保留 `__detachKey` 去重。
5. **DetachedApp.jsx**：新增 `mode="tab"` 分支，按 `type` 渲染（Phase 1 只 browser）。

### 4.3 tab 序列化（`panel=tab` 的 URL params）

Phase 1 最小集（browser）：

```
type=browser
title=<url 编码的标题>
browserUrl=<url 编码的外链地址>
```

后续 phase 追加：`workflowId`（studio）、`sessionId`/`assistantId`（chat）等。
**安全注意**：`browserUrl` 等入参在 main.js 加载前校验（仅允许 http/https，避免 `file://`
注入——已有 `will-navigate` 拦截可复用）。

### 4.4 后端复用

`window.hermes` 的 aguiPort 来自主进程启动时的 HermesRunner。独立窗口的 preload
**不重启**后端，而是通过主进程把当前 `aguiPort` 透传给新窗口（`DetachedApp` 已有
"等主窗口后端就绪"的等待逻辑，见 `src/main.jsx` 的 Bootstrap 分支）。

---

## 5. 分阶段实施

| Phase | 范围 | 状态同步策略 | 预估 |
|---|---|---|---|
| 1 | browser tab tear-off | 每窗口独立 + 关闭丢弃 | ~2-3h |
| 2 | studio tab tear-off | 同上 | ~2h |
| 3 | chat / homepage tear-off | 同上（后续再议回传） | ~4h+ |

---

## 6. 风险与未决

1. **黑屏回归风险**：旧方案 `panel=studio` 黑屏，根因未完全定位（疑为独立窗口渲染完整
   App 时 `-webkit-app-region: drag` + 主题/bridge 初始化时序）。Phase 1 用
   `mode="tab"` 只渲染单组件（BrowserPanel），避开重 App 渲染，风险低。
2. **OS drag-region 与 DnD 冲突**（已踩坑，见 commit `328f2f1` / `b161c5a`）：tear-off
   检测点在 `onDragEnd`，需确认 tab 容器保持 `no-drag`。
3. **多窗口下 `window.hermes` 单例假设**：现有代码多处假设单窗口（如 `subscribeContractEvents`
   的全局 fan-out）。多窗口后事件是否误投？需在 Phase 2+ 验证。
4. **未决**：§3.3（关窗去向）、§3.4（状态仓库）需用户拍板。

---

## 7. 决策请求（待用户）

- [ ] §3.3 关窗后 tab 去向：A（丢弃，推荐）/ B（回主窗口）/ C（可拖回列表）
- [ ] §3.4 跨窗口状态：A（每窗口独立，推荐 Phase 1）/ B（主进程仓库）/ C（完整 App 副本）
- [ ] §3.2 阶段划分：先做 browser（Phase 1）还是直接 studio？
