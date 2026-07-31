# Agent 自渲染 UI 组件能力 Spec（v0.1 / MVP）

> 目标：让普通对话中的 agent 能**自主决定**在回复里插入结构化 UI 组件（表格 / 流程图 / 卡片 / 进度条等），
> 复用现有"后端结构化事件 → 前端组件渲染"通道，不引入 CopilotKit，不回退到 Markdown 嵌 JSON。

## 1. 背景与现状

| 已有能力 | 触发方式 | 局限 |
|----------|----------|------|
| `WorkflowTimeline` | 后端 emit `workflow.progress` | 仅 LangGraph workflow 运行时 |
| `ApprovalBubble` | 后端 emit `workflow.approval` | 仅 HITL 审批门 |
| `ContractForm` | workflow manifest schema | 仅选中工作流时 |
| `ThinkingTranscript` | `thinking.delta` 累积 | 通用，已实现 |

**缺口**：普通对话（非 workflow）的 agent 回复永远是纯文本 Markdown，无法生成自定义 UI。

**现有通道（可直接复用）**：
- `useAgentStream.handleCustom` 接收 AG-UI `CUSTOM` 事件（`{name, value}`）
- `name.startsWith("workflow.")` → 转发 `eventBus`（`emitContractEvent(runId, {type, payload})`）
- `agui-server.js` 已有 Hermes 事件 → AG-UI CUSTOM 的翻译层（line 522-537）
- `MessageThread.jsx` 通过 `useContractEvents(sessionId)` 订阅并渲染

## 2. 架构总览

```
Agent 决策 → 调用 render_ui tool（结构化参数）
        ↓
Hermes tool executor
        ↓
agui-server 拦截 tool 结果 → translate → send CUSTOM {name: "ui.render", value: {blockId, type, props}}
        ↓
useAgentStream.handleCustom → 识别 "ui.render" → 推入 uiBlocks[] state
        ↓
MessageThread 渲染 <GeneratedComponent type props /> 内联在对话流中
```

**关键设计原则**：
1. **工具驱动**（方案 A）：agent 通过 `render_ui` tool call 声明渲染意图，比文本嵌 JSON 稳定
2. **白名单渲染**：只渲染预注册的组件 type，绝不允许任意 JSX（防 XSS / 失控布局）
3. **内联渲染**：组件出现在对话流中（消息之间），不是独立面板
4. **通道复用**：CUSTOM 事件机制与 workflow.* 完全一致，不新增传输层

## 3. 事件协议

### 3.1 前端接收（useAgentStream → handleCustom）

新增分支：
```js
} else if (name === "ui.render") {
  const { blockId, type, props, replace } = value || {};
  if (!type || !UI_BLOCK_TYPES.has(type)) return; // 安全：未知 type 丢弃
  setUiBlocks((prev) => {
    const next = replace
      ? prev.filter((b) => b.blockId !== blockId)
      : prev;
    return [...next, { blockId, type, props }];
  });
}
```

`uiBlocks` 为 `useAgentStream` 暴露的新 state，`MessageThread` 通过 props 接收。

### 3.2 后端发出（agui-server 翻译层）

在 `agui-server.js` 的 Hermes 事件翻译 switch（line 522 区域）新增：
```js
case 'ui.render':
  send({ type: 'CUSTOM', name: 'ui.render', value: payload });
  break;
```

`payload` 形状：`{ blockId: string, type: string, props: object, replace?: boolean }`

### 3.3 后端工具（Hermes `render_ui` tool）

工具签名（结构化，强制 JSON schema）：
```
render_ui(blockId: str, type: "table"|"flowchart"|"card"|"progress", props: dict) -> {ok: bool}
```
- `blockId`：幂等键，相同 blockId 重复调用 → 更新而非新增（`replace: true`）
- 工具返回 `{ok:true}` 给模型，实际渲染走事件通道（不直接在 tool result 里塞 HTML）

## 4. 前端渲染层

### 4.1 GeneratedComponent 路由

新建 `src/components/GeneratedComponent.jsx`：
```jsx
const REGISTRY = { table: TableBlock, flowchart: FlowchartBlock, card: CardBlock, progress: ProgressBlock };
export function GeneratedComponent({ block }) {
  const Cmp = REGISTRY[block.type];
  if (!Cmp) return null;
  return <Cmp {...block.props} blockId={block.blockId} />;
}
```

### 4.2 MessageThread 内联位置

在 assistant 消息气泡之后、下一条消息之前插入：
```jsx
{messages.map((m) => (
  <Fragment key={m.id}>
    {renderMessage(m)}
    {m.role === "assistant" && m.id === lastAssistantId &&
      uiBlocks.map((b) => <GeneratedComponent key={b.blockId} block={b} />)}
  </Fragment>
))}
```
> 简化 MVP：uiBlocks 挂在**最近一条 assistant 消息**下方。后续可扩展为按 blockId 精准插入到对应消息。

### 4.3 样式约定

- 组件外层 `.ui-block` 卡片容器（圆角、轻边框、与气泡视觉区分）
- 宽度跟随对话列（不撑满右侧面板）
- 支持 `theme` 透传（跟随 IDE 亮/暗主题）

## 5. MVP 组件目录

### 5.1 `table` — 结构化表格
```ts
props: {
  columns: string[];
  rows: (string|{text:string,bold?:boolean,align?:'left'|'right'})[][];
  caption?: string;
  highlightRow?: number; // 高亮某行（如"推荐"项）
}
```

### 5.2 `flowchart` — 流程图 / 架构图
```ts
props: {
  nodes: { id: string; label: string; shape?: 'rect'|'round'|'diamond'; status?: 'done'|'active'|'pending' }[];
  edges: { from: string; to: string; label?: string }[];
  direction?: 'LR'|'TB'; // 默认 TB
}
```
> MVP 用 CSS flex/grid 线性布局（不引入 mermaid/dagre），后续可换 SVG 自动布局

### 5.3 `card` — 信息卡片
```ts
props: {
  title: string;
  icon?: string; // phosphor icon name
  body: string; // 支持简单 markdown
  actions?: { label: string; onClick?: string }[]; // MVP 只读，action 后续接 tool
  tone?: 'default'|'info'|'warn'|'success';
}
```

### 5.4 `progress` — 步骤进度条
```ts
props: {
  steps: { label: string; status: 'done'|'active'|'pending'|'error' }[];
  current?: number;
}
```
> 与现有 `WorkflowTimeline` 视觉一致，但这是**通用**组件（非 workflow 事件驱动）

### 5.5 `action` — 操作进度实时预览（文件写入 / 命令执行等）

用户截图中的效果：agent 一边执行操作（写文件/跑命令），一边向用户展示**实时内容预览 + 状态动画**。

```ts
props: {
  type: 'file_write' | 'command' | 'http_request' | 'generic';
  status: 'pending' | 'running' | 'done' | 'error';  // 带动画高亮
  target?: string;        // 文件路径 / URL / 命令摘要
  preview?: string;       // 实时内容预览（markdown 或纯文本，流式追加）
  previewLang?: string;   // 语法高亮语言标识（如 "markdown", "json", "bash"）
  detail?: string;        // 底部状态文字（如 "正在写入文件" / "HTTP 200 · 0.3s"）
  error?: string;         // status=error 时的错误信息
}
```

**行为特征**：
- `status=running` 时：左侧有脉冲动画指示器（类似 cogitating spinner），`preview` 区域内容**流式增长**（每次 render_ui 同 blockId 追加而非替换）
- `status=done` 时：动画停止，显示完成标记（✓），`detail` 显示耗时/结果摘要
- `target` 为文件路径时可点击（通过 IPC `read-file` 打开）
- `preview` 走受控 markdown 渲染（代码块带语法高亮）
- **与 tool.chunk 的关系**：`tool.chunk` 展示工具调用的原始参数/结果；`action` 组件是 agent **主动选择**向用户展示的操作过程可视化。两者可并存——chunk 在消息行内，action 作为独立 uiBlock 更醒目。

## 6. 安全模型

1. **白名单**：`UI_BLOCK_TYPES = new Set(["table","flowchart","card","progress","action"])`，未知 type 静默丢弃
2. **无任意 JSX**：`props` 只接受受控字段，渲染器自行映射，不 `dangerouslySetInnerHTML`
3. **Markdown 隔离**：`card.body` 走 `react-markdown` 受控渲染（已有限制插件），不裸 HTML
4. **尺寸上限**：`table` 行数 / `flowchart` 节点数设硬上限（如 50），防恶意大 payload 卡死渲染
5. **blockId 校验**：`^[a-zA-Z0-9_-]{1,64}$`，防注入

## 7. 不做的事（MVP 边界）

- ❌ 不实现"agent 输出 JSX/HTML 直接渲染"
- ❌ 不引入 mermaid / dagre / react-flow（MVP 用 CSS 布局）
- ❌ 不做组件间交互状态（MVP 只读展示）
- ❌ 不接入 workflow 审批门到通用组件（保持两条通道清晰）
- ❌ 不做组件持久化（会话结束即丢弃，不写数据库）

## 8. 实施步骤

1. `useAgentStream.js`：新增 `uiBlocks` state + `handleCustom` 的 `ui.render` 分支
2. `agui-server.js`：翻译层加 `ui.render` case
3. `MessageThread.jsx`：接收 `uiBlocks` prop，内联 `<GeneratedComponent>`
4. 新建 `src/components/GeneratedComponent.jsx` + 5 个子组件
5. 新建 `src/components/ui/` 下 5 个组件文件
6. Hermes 新增 `render_ui` tool（`.hermes/...` 或现有 tools 目录）+ 事件 emit
7. `index.css` 加 `.ui-block` 等样式
8. 构建 + 部署 + 真机验证

## 9. 验收

- [ ] agent 调用 `render_ui({type:"table",...})` → 对话流中渲染表格
- [ ] 相同 blockId 重复调用 → 更新而非新增
- [ ] 未知 type → 静默丢弃不报错
- [ ] 切换窗口 / 新会话 → uiBlocks 正确重置
- [ ] 组件跟随 IDE 主题（亮/暗）
- [ ] 5 个 MVP 组件均可在真实对话中由 agent 触发
- [ ] **`action` 组件**：status=running 时有脉冲动画，preview 流式追加内容；status=done 动画停止显示 ✓；文件路径可点击

## 10. 实施状态（2026-07-26）

**已落地（8 步全部完成）：**

1. ✅ `src/hooks/useAgentStream.js`
   - 新增 `uiBlocks` state（白名单 `UI_BLOCK_TYPES`、blockId 正则 `BLOCK_ID_RE`、上限 `UI_BLOCK_MAX=64`）
   - `handleCustom` 新增 `ui.render` 分支：白名单 + blockId 校验，按 blockId 幂等更新；支持 `appendPreview` 仅追加 preview 文本
   - `sendMessage` / `reset` / `setHistory` 均清空 `uiBlocks`（新轮隔离，切换会话因 `key={selectedSessionId}` 重挂自动重置）
   - 返回值暴露 `uiBlocks`
2. ✅ `electron/backend/agui-server.js`
   - `createTurnTranslator` 新增 `ui.render` case（网关事件通道，备用）
   - 新增 `activeTurnSenders` 映射 + `writeUiActiveCoord`/`clearUiActiveCoord`：每轮开始写入 `.ui_active.json` 并注册 sender，轮结束清理
   - 新增 HTTP 端点 `POST /api/ag-ui/ui-event`：`{runId, payload:{type,blockId,props,replace?,appendPreview?}}` → 中继 `CUSTOM{name:"ui.render"}` 注入当前轮 SSE 流
3. ✅ `src/components/MessageThread.jsx`：`uiBlocks` prop 接入，线程末尾渲染 `<GeneratedComponent>` 区域
4. ✅ `src/components/GeneratedComponent.jsx` + `src/components/ui/{Table,Flowchart,Card,Progress,Action}Block.jsx` + `src/components/ui/markdown.jsx`
5. ✅ `src/components/ui/` 下 5 个组件（props schema 见 §5，跟随主题 CSS 变量）
6. ✅ `hermes-fork/tools/render_ui_tool.py`：自注册 `render_ui` 工具，读 `.ui_active.json` → HTTP 桥 POST 到 `/api/ag-ui/ui-event`，纵深防御校验；返回 `{ok, delivered}`
7. ✅ `src/styles/index.css`：`.ui-block` 及全部子组件样式（暗/亮主题）
8. ✅ 构建通过（`vite build` → dist 517KB），已部署至 `release/win-unpacked/resources/app`（dist + agui-server.js + render_ui_tool.py）

**验证情况：**
- 前端构建通过；agui-server.js / render_ui_tool.py 语法 + 注册检查通过（`registry._tools` 含 `render_ui`，toolset=`ui`）
- `/api/ag-ui/ui-event` 路由 + 输入校验 smoke test 通过（missing runId→error，无活跃轮→dropped）
- **未做真机端到端验证**（需干净 Windows + 有效 Agnes API Key + 运行 Abcyesno.exe），与既有 E2E 缺口一致。真机验证项：模型调用 `render_ui` → 对话流出现对应组件；相同 blockId 重复调用更新而非新增；`.ui_active.json` 时序正确。

**数据流总览：**
```
模型调 render_ui(blockId,type,props)
  → hermes tool (render_ui_tool.py)
  → 读 .ui_active.json 取 runId
  → POST /api/ag-ui/ui-event {runId, payload}
  → agui-server 中继 CUSTOM{name:"ui.render"} 注入当前轮 SSE
  → useAgentStream.handleCustom("ui.render") 推入 uiBlocks[]
  → MessageThread 末尾 <GeneratedComponent> 按 type 白名单路由渲染
```

