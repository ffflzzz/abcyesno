# Abcyesno 工作台架构变更 Spec（v0 草案）

> 状态：v0 草案（待 lex 确认后转 v1 基线）
> 范围：重新划分"通用对话入口"与"专用工作流入口"，新增 `@` 提及调用协议，定义专用 Workbench UI 契约。
> 配套文档：`LANGRAPH_CONTRACT_SPEC.md`（数据契约基线，本 Spec 在其之上叠加**入口与路由**层）。

---

## 0. 问题陈述

当前所有"助手 / 工作流"（ABC、Manju Craft、Image Gen）共用同一个聊天界面（`Composer` + `MessageThread`）。这导致两个问题：

1. **交互混乱**：进入 Manju Craft 后，界面和主 agent 完全一样，用户无法区分"我在和通用助手聊天"还是"我在操作一个视频生成工作流"。Manju Craft 需要的不是聊天，而是**节点画布 / 蓝图 / 进度时间线**。
2. **调用割裂**：想在主对话里顺手用一下 Manju Craft，必须切到侧边栏单独会话，上下文断了。

核心命题（lex 明确）：
> 主 agent 是**通用对话表面**，专用 agent/workflow 是**定制工作区**。两者必须分开；且主对话应能直接 `@` 调用任意工作流，结果回同一流。

---

## 1. 两层入口模型

```
┌────────────────────────────────────────────────────────────────┐
│  侧边栏（实体目录）                                              │
│   ├─ ABC 通用助手          → 入口类型 = CHAT                     │
│   ├─ Manju Craft 漫剧       → 入口类型 = WORKBENCH (ui: node-graph)│
│   └─ Image Gen 文生图       → 入口类型 = WORKBENCH (ui: chat)     │
└───────────────┬──────────────────────────┬──────────────────────┘
                │ 点击 CHAT 型              │ 点击 WORKBENCH 型
                ▼                          ▼
┌─────────────────────────┐   ┌──────────────────────────────────┐
│ 工作台主对话 (ChatView)   │   │ 专用工作区 (WorkbenchView)         │
│ • 通用对话 + @ 提及       │   │ • 该 workflow 的定制 UI            │
│ • @Manju_Craft 调用工作流 │   │ • 节点画布 / 蓝图 / 时间线          │
│ • 结果回同一聊天流         │   │ • 复用同一事件流驱动               │
└─────────────────────────┘   └──────────────────────────────────┘
```

**关键区分**：入口类型由 manifest/assistant 的 `ui` 字段决定（见 §3），不是由"是 agent 还是 workflow"决定。ABC 是 `ui: chat`；Manju Craft 是 `ui: node-graph`；Image Gen 因输入输出简单，可先归为 `chat` 或轻量 `form`。

---

## 2. 工作台主对话（ChatView）+ `@` 提及协议

### 2.1 体验

- 主对话即现在的 `Composer` + `MessageThread`，但 **Composer 支持 `@` 触发提及选择**。
- 输入 `@` → 弹出"可调用实体"选择器（来自 §3 的目录）→ 选中 `Manju_Craft` → 插入 `@Manju_Craft` token。
- 发送后，主 agent 把 `@Manju_Craft <后续文本>` 解析为一次**子调用**：在**当前会话流**内，以目标 workflow 执行，结果（文本 + 产物 + 进度）作为一条**内层消息**渲染（左缩进、淡色边、标注 `@Manju_Craft`）。
- 用户可在主对话继续打字，子调用在后台跑（复用现有排队机制）；子调用可交互（human-in-the-loop 审批弹窗浮在对应内层消息上）。

### 2.2 数据流（复用现有，不改后端契约）

- `@` 解析发生在前端：`Composer` 发出时把消息包成 `{ role:"user", content, mentions:["manju_craft"] }`。
- 主 agent（`useAgentStream` + `agui-server`）收到后，对带 `mentions` 的消息，将对应文本转交目标 workflow 的 `langgraph_agent` 调用（**已有能力**，现在只是加"按 mention 路由"）。
- 子调用的进度事件（`step_started` / `artifact_produced` 等，已有 `progress_events` 字段）驱动内层消息的 `TaskProgressPanel` + `ArtifactPreview`（现有组件，零改动）。

### 2.3 提及选择器数据来源

```js
// 合并 assistants + manifests 为"可调用实体目录"
const mentionables = [
  ...assistants.map(a => ({ id:a.id, name:a.name, kind:"assistant" })),
  ...manifests.map(m => ({ id:m.id, name:m.name, kind:"workflow" })),
];
```

---

## 3. 专用 Workbench UI 契约（manifest.ui）

### 3.1 新增字段

在 `src/contract/manifests.js` 每个 manifest 增加 `ui` 字段：

```js
{
  id: "manju_craft",
  // ...existing fields...
  ui: {
    type: "node-graph",        // chat | form | node-graph | blueprint | timeline | custom
    component: "ManjuCraftWorkbench", // 对应 src/workbenches/ 下注册的组件
    title: "漫剧工作台",
  },
}
```

`ui.type` 决定渲染策略：
| type | 前端行为 |
|------|---------|
| `chat` | 走通用 `ChatView`（Composer + MessageThread） |
| `form` | 走现有 `ContractForm`（参数表单 + 结果） |
| `node-graph` | 加载 `component` 指定的专用节点画布 |
| `blueprint` | 加载蓝图编辑器（类 UE Blueprint） |
| `timeline` | 加载时间线/步骤视图 |
| `custom` | 加载任意注册的 React 组件 |

### 3.2 组件注册表

新增 `src/workbenches/registry.js`：

```js
import ManjuCraftWorkbench from "./ManjuCraftWorkbench.jsx";

const WORKBENCHES = {
  ManjuCraftWorkbench,
  // 未来每个专用工作流在此注册一个组件
};

export function getWorkbench(componentName) {
  return WORKBENCHES[componentName] || null;
}
```

**契约要点**：专用组件是**注册式**而非分支式。前端 `ChatShell` 看到 `ui.type !== "chat" && ui.type !== "form"` 时，从 registry 取 `ui.component` 渲染，**不按 workflow id 写 if 分支**。新增工作流 = 写一个组件 + 注册 + manifest 加一行。

**复用策略（lex 2026-07-22 确认）**：首版 Manju Craft 组件**手写**以验证交互质量，但内部将节点画布容器、产物面板、进度高亮逻辑抽成可复用基类/子组件。后期视频生产类 workflow（分镜→生图→剪辑→导出）结构高度相似，直接继承/复用该骨架，仅替换节点定义与产物布局——手写不等于每次从零写。

### 3.3 专用组件如何消费事件流

专用组件**复用现有 CUSTOM 事件**（`stream.phase` / `thinking.delta` / `tool.chunk` / `step_started` / `artifact_produced`），通过同一个 `useAgentStream` hook 订阅。专用组件只是把这些事件渲染成"节点高亮 / 连线动画 / 时间线进度"而非聊天气泡。

例如 `ManjuCraftWorkbench`：
- 顶部：脚本输入 + 风格选择 + "生成"按钮（替代 Composer）
- 中部：节点画布（脚本解析 → 分镜 → 生图 → 剪辑 → 导出），每个节点随 `step_started`/`step_progress` 高亮
- 右侧：产物面板（分镜图、风格确认图、成片），随 `artifact_produced` 实时填充，用户点图确认风格即发 `approval_gates` 响应

---

## 4. 路由与状态管理

### 4.1 App 状态调整

现有 `App.jsx` 已有：
- `selectedAssistantId` / `assistants`（聊天助手）
- `selectedWorkflowId` / `manifests`（契约）
- `ChatShell` 按 `selectedWorkflowId` 切 `ContractForm` 或 `Composer`

变更：统一为**单一"当前入口"概念**：

```js
const [activeEntry, setActiveEntry] = useState(null);
// activeEntry = { id, kind: "assistant" | "workflow", uiType }
```

- 侧边栏点击 assistant（ui: chat）→ `activeEntry = {id, kind:"assistant", uiType:"chat"}` → `ChatView`
- 侧边栏点击 workflow（ui: node-graph）→ `activeEntry = {id, kind:"workflow", uiType:"node-graph"}` → `WorkbenchView`
- 主对话内 `@` 调用 → **不切换** activeEntry，仅在该会话流插入内层子调用消息

### 4.2 ChatShell 改造

`ChatShell` 渲染逻辑（伪代码）：

```
if (activeEntry.uiType === "chat")        → <ChatView />   （Composer + MessageThread + @ 提及）
else if (activeEntry.uiType === "form")   → <ContractForm />
else                                       → getWorkbench(manifest.ui.component)  // 专用工作区
```

### 4.3 会话归属

- 每个 entry 有独立会话列表（现有 `loadSessions(assistantId)` 已按 id 隔离）。
- 专用 Workbench 的会话存同一套 session 机制，只是渲染层不同。
- `@` 子调用归属主 agent 的当前会话，不另建会话。

---

## 5. 与现有代码的关系

| 现有资产 | 在本 Spec 中的角色 |
|---------|------------------|
| `src/contract/manifests.js` | 加 `ui` 字段；`listManifests()` 不变 |
| `src/components/ContractForm.jsx` | 即 `ui: form` 的渲染器（已是雏形） |
| `src/components/Composer.jsx` | 加 `@` 提及触发 + 提及选择器 |
| `src/hooks/useAgentStream.js` | `@` 子调用与专用 Workbench 共用，零改动 |
| `TaskProgressPanel` / `ArtifactPreview` | 专用组件内部复用，零改动 |
| `ChatShell` | 按 `activeEntry.uiType` 路由三类视图 |
| `electron/backend/agui-server.js` | 按 `mentions` 路由到目标 workflow（现有 `langgraph_agent` 调用加路由层） |

---

## 6. 验收标准

1. 侧边栏点击 ABC → 通用聊天；点击 Manju Craft → 进入节点画布工作台（非聊天）。
2. 主对话输入 `@` 弹出实体选择器；选中 `@Manju_Craft` 发送后，结果作为内层消息出现在同一会话流，不切换视图。
3. 新增一个专用工作流（如 `blueprint` 型）：仅写 1 个 React 组件 + 注册 + manifest 加 `ui` 字段，**前端路由代码零改动**。
4. 专用工作区内的进度、产物、审批交互，均来自现有 CUSTOM 事件流，无新增后端协议。

---

## 7. 实施阶段（建议）

- **P1（入口拆分）**：manifest 加 `ui` 字段；`activeEntry` 状态；`ChatShell` 路由三类视图。先把 Manju Craft 设为 `ui: form`（复用 ContractForm）跑通"点击进入非聊天界面"。
- **P2（`@` 提及）**：Composer 加 `@` 选择器 + `mentions` 输出；agui-server 按 mention 路由到 `langgraph_agent`；内层子调用消息渲染。
- **P3（专用 Workbench 组件，手写首版）**：实现 `ManjuCraftWorkbench`（节点画布 + 产物面板），内部抽可复用骨架（`WorkbenchCanvas` / `ArtifactPanel` / 进度高亮 hook）；`src/workbenches/registry.js`；替换 P1 的 `form` 为 `node-graph`。
- **P4（更多类型）**：`blueprint` / `timeline` 通用渲染器，作为后续 workflow 的即用基线。

---

## 8. 已决决策（lex 2026-07-22 确认）

1. **`@` 子调用结果允许"升级"为独立会话**。
   - 内层消息提供一个「在 Manju Craft 工作台打开」按钮，点击后把该次调用**拎出**成一个独立的专用 Workbench 会话（节点画布界面），之后在该界面继续迭代，与主对话脱钩。
   - 默认不自动跳走——用户主动点才升级。复用现有 `loadSessions` + 专用 Workbench 视图。

2. **侧边栏分"聊天 / 工作流"两组**。
   - 聊天组：`ui.type === "chat"` 的助手 + 其会话（ABC、以及 `@` 升级来的独立会话）。点击进 ChatView。
   - 工作流组：`ui.type !== "chat"` 的实体（Manju Craft / Image Gen）+ 各自专用会话。点击进 WorkbenchView（专用 UI）。
   - 分组标题用图标（💬 / 🔧），不占空间。示意见 §4.4。

3. **专用 Workbench 组件首版手写**，但骨架设计为可复用。
   - Manju Craft 首版手写 `ManjuCraftWorkbench.jsx`（节点画布 + 产物面板），以验证交互质量。
   - 组件内部将**节点画布容器、产物面板、进度高亮逻辑**抽成可复用基类/子组件；后期视频生产类 workflow（分镜→生图→剪辑→导出）结构高度相似，直接继承/复用该骨架，仅替换节点定义与产物布局。
   - 声明式通用渲染器（从 manifest 图描述自动生成）列为**后续可选优化**，不在首版范围；首版先用手写打样，再把共性沉淀。

### 4.4 侧边栏分组示意

```
┌─────────────────────────────┐
│  ⊞ Abcyesno          ⌕ 🔍    │
├─────────────────────────────┤
│  ＋ 新会话                    │
├─────────────────────────────┤
│  💬 聊天                      │  ← 组1：聊天助手 (ui: chat)
│  ├─ ABC 通用助手  14:35       │
│  └─ 漫剧讨论(升级) 13:20       │  ← @ 升级来的独立会话
│                              │
│  🔧 工作流                    │  ← 组2：工作流 (ui≠chat)
│  ├─ 🎬 Manju Craft  13:20     │  → 节点画布 Workbench
│  └─ 🖼 Image Gen    12:10     │  → 轻量表单 Workbench
├─────────────────────────────┤
│  ⚙ 设置   👤 账号   📦 技能   │
└─────────────────────────────┘
```
