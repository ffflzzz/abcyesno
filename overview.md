# 工作台「运行追踪」节点拓扑 + 实时 Trace 面板

## 用户诉求
在短剧制片工作台右侧新增一个缩略窗口：展示 manjucraft_agent 的 LangGraph 真实图结构，运行时用动画高亮做节点追踪（要求完整做：拓扑 + 当前节点脉冲 + 缩放/平移 + 复杂动画）。

## 做了什么

### 1. 后端暴露图结构 + 节点 trace（`langgraph_runtime.py`）
- 新增 `_extract_graph_topology(graph)`：用 `graph.get_graph()` 取**真实**节点与边（含 `finalize_episode → parse_script` 的 series 循环边），过滤 `__start__/__end__`，失败回退空拓扑。
- `_run_graph_with_hitl` 启动即发一次 `workflow.graph`：`{ nodes:[{id,label}], edges:[{from,to}], totalEpisodes }`，label 取自 agent 的 `WORKFLOW_STAGES`（稳定顺序）。
- 每个 astream 节点发 `workflow.trace`（**不去重**，series 模式可重复高亮）：`{ node, status: running|done|pending, episode }`；前一节点自动转 `done`，gate 节点发 `pending`，收尾把最后的 `running` 转 `done`。

### 2. 事件中继（`agui-server.js`）
`createTurnTranslator` 新增 `workflow.graph` / `workflow.trace` 的 CUSTOM 透传（原先 `default` 只会包成 `RAW`，前端读不到）。链路：Python `on_event` → agui-server → AG-UI CUSTOM → `useAgentStream.handleCustom` → eventBus `{type:name, payload:value}` → StudioWorkbench 订阅。

### 3. 前端组件（`src/components/WorkflowGraphPanel.jsx`，纯 SVG 无新依赖）
- 右上「运行追踪」面板：垂直 DAG 自动布局 + `ResizeObserver` 适应窗口 + 滚轮以光标为中心缩放 + 拖拽平移 + `－ / ＋ / ⤢` 按钮。
- 节点状态色：idle 灰 / **running 蓝色脉冲环**（`wf-pulse` 发光）/ done 绿 + ✓ / pending 琥珀闪烁 / error 红。边激活时 `wf-edge-active` 流动虚线；series 循环边用左侧贝塞尔弧线。
- 无障碍：`prefers-reduced-motion` 下关闭脉冲/闪烁，保留静态高亮环。
- 顶部显示「第 N/Total 集」+ LIVE 指示（仅 series 且 total>1 显示集数）。

### 4. 接入工作台（`StudioWorkbench.jsx` + `.css`）
- 新增 state：`topology / trace / traceEpisode / traceTotal`。
- 订阅 `workflow.graph`（建拓扑、清 trace）、`workflow.trace`（写 trace map + 集数）；`RUN_ERROR` 把 running 节点标 error；`workflow.done` 全部标 done；`RUN_STARTED`/`handleStart`/重置按钮 清拓扑。
- 右侧栏：有拓扑时渲染 `<WorkflowGraphPanel>`，下方仍是「任务中心」任务列表。
- CSS 追加 `.wf-*` 全套。

## 验证
- `node scripts/check-tdz.js`：clean（0 violations）
- `npx vite build`：通过（bundle `index-LUr7-8-x.js`）
- `node scripts/test-multisession/run.mjs`：**26/26 passed**
- 已同步：`dist` + `langgraph_runtime.py` + `agui-server.js` → `release/win-unpacked/...`；清 `HERMES_HOME` 缓存。

## 怎么看
完整退出 `Abcyesno.exe`（进程全退）再重启 → 进「短剧制片工作台」→ 填剧本/点「生成资产与分镜」→ 右侧出现「运行追踪」面板，节点随运行依次点亮并脉冲，series 模式显示集数。

## 已知边界
- `astream(stream_mode="updates")` 在节点产出时回报，所以节点点亮略晚于真实开始（与前步进器/进度条的既有语义一致）；如需严格「开始前」高亮可改 `stream_mode="debug"`，但会增加事件量。
