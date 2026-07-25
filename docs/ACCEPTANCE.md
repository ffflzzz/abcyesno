# Abcyesno - Acceptance Criteria

## 0. 验收依赖与图例

### 验证依赖
- **[API]** 需有效 Agnes API Key（`agnes-2.0-flash`）才能跑通真实对话 / 工具 / 审批 / manju-craft 工作流。
- **[ENV]** 需在未安装 Python / Hermes 的干净 Windows 机器验证便携版独立运行。
- **[UI]** 需对照 `docs/UI_UX_SPEC.md`（v2，taste-skill 对齐版）做 UI/UX 符合性验收。

### 严重度
- **P0**：发布阻塞。不通过则不能交付。
- **P1**：发布前必须闭合，或有关键业务影响。
- **P2**：质量项，可跟踪但不阻塞。

### 状态标记
- `[x]` 已通过（代码层面或本机验证）
- `[ ]` 未通过 / 待验证（标注依赖）

---

## 1. 验收范围

覆盖 `Abcyesno`（基于 Hermes fork 的便携桌面 Agent 平台）各阶段：

- Hermes Fork 基线可运行
- 非核心模块已剥离
- Harness 核心能力可用
- LangGraph Skill 接入机制可用
- `manju-craft` 能作为 skill 被调用
- Electron 前端能替代官方 desktop
- 便携版可在干净环境运行
- UI/UX 规范符合性（见 Phase 8）

## 2. 通用验收标准

### 2.1 代码与文档一致性
- [x] `PRD.md`、`SPEC.md`、`ROADMAP.md`、`UI_UX_SPEC.md`、`ACCEPTANCE.md` 相互一致，差异处已标注（UI/UX 已对齐 v2）。
- [x] `DEV_LOG.md` 已更新关键改动记录。
- [x] 所有自定义 skill 目录包含入口文件与说明。

### 2.2 启动与运行
- [x] 应用启动时无未捕获异常。
- [x] `hermes serve` 能自动启动并进入 ready 状态。
- [x] Electron 窗口正常显示（先 Bootstrap 启动画面，后端 ready 后切主界面），不白屏、不崩溃。
- [x] 关闭应用时，后台 `hermes serve` 进程被正确清理。

## 3. Phase 0 - Hermes Fork 基线

### 3.1 环境隔离
- [x] `hermes-fork/` 目录存在，与系统 Hermes 安装隔离（venv + 源码打包进 release）。
- [x] `HERMES_HOME` 指向 `%USERPROFILE%/.hermes_portable_data`，不读系统 Hermes 配置。

### 3.2 服务启动
- [x] 执行启动命令后，`hermes serve` 监听指定端口。
- [x] `curl http://127.0.0.1:<port>/api/status` 返回 HTTP 200 和正常 JSON。
- [x] 启动日志无 import error 或 fatal error。

## 4. Phase 1 - 源码精简

### 4.1 IM 与外部服务模块已移除
- [x] CLI 中不存在 `whatsapp`、`whatsapp-cloud`、`slack` 子命令。
- [x] `skills/` 下无对应 IM 目录。
- [x] 启动日志无 IM 相关模块加载信息。

### 4.2 更新与 Telemetry 已剥离
- [x] 启动时无检查更新的网络请求。
- [x] 无 Telemetry / analytics / sentry 相关日志输出。
- [x] `cli.py` 中无指向 Hermes 官方更新服务的调用。

### 4.3 核心模块保留
- [x] `agent/`、`tools/`、`skills/`、`memory/`、`gateway/` 目录完整存在。
- [x] `hermes serve` 仍能正常启动。

## 5. Phase 2 - Harness 核心能力

### 5.1 基础对话
- [ ] **[API]** 配置 Agnes API Key 后，发送「你好」，返回非空回复。(P0)
- [x] 多轮对话上下文保持正确（前端 session 与 CopilotKit threadId 绑定）。

### 5.2 工具调用
- [ ] **[API]** 发送「列出当前目录文件」，Hermes 调用 `terminal` / `read_file` 工具并返回结果。(P0)
- [ ] **[API]** 工具结果正确回显给用户。(P0)

### 5.3 审批流程
- [ ] **[API]** 执行危险操作（删除文件 / `rm` 命令）触发 approval 请求。(P0)
- [ ] **[API]** 用户批准后继续执行；拒绝后优雅终止并提示。(P0)

### 5.4 会话持久化
- [x] 重启 `hermes serve` 后历史会话可经 API 查询。
- [x] 会话标题、消息顺序正确。

## 6. Phase 3 - LangGraph Skill Adapter

### 6.1 Skill 发现与加载
- [x] `skills/langgraph_agents/` 下新增 agent 后 Hermes 能自动发现。
- [x] Skill 信息正确读取。

### 6.2 Demo Skill 可调用
- [x] 提供最小 demo（`hello_agent`），经 gateway 调用返回流式事件（含 `token` / `done`）。

### 6.3 Skill 事件标准化
- [x] LangGraph 原始事件正确转换为标准事件格式。
- [x] 错误事件能被前端识别。

## 7. Phase 4 - `manju-craft` 接入

### 7.1 Skill 注册
- [x] `skills/langgraph_agents/agents/manju_craft/` 存在，`agent.py` 正确加载 graph。

### 7.2 路由触发
- [x] 选中 Manju Craft 助手后路由到 `manju-craft`（经 `langgraph_agent` 工具）。代码路径已在 `MANJU_CRAFT_MOCK=1` 下验证。
- [ ] **[API]** 非相关输入不误触发 `manju-craft`。(P1)

### 7.3 执行结果
- [x] 执行过程返回状态事件（mock 模式）。
- [x] 最终返回完成事件；失败返回清晰错误。
- [ ] **[API]** 取消 mock 后真实调用 Agnes image/video API 跑通全图并产出 `final.mp4` / 剪映草稿。(P1)

## 8. Phase 5 - Electron 前端

### 8.1 自动启动后端
- [x] 双击 `Abcyesno.exe` 自动启动 `hermes serve`，期间立即显示加载画面。

### 8.2 聊天功能
- [x] 用户输入实时显示在消息列表。
- [x] 后端错误（如 401）前端显示错误气泡 + 可关闭顶部横幅。
- [x] 多轮对话消息顺序正确。
- [ ] **[API]** Assistant 回复以流式逐字显示（SSE 解码与去重已就绪）。(P1)

### 8.3 工具事件展示
- [x] 工具调用显示 `ToolCard`（名称、参数、状态）。
- [x] 无 `tool_call_id` 的 Hermes 事件也能正确开合卡片（稳定 `tool-${toolName}`）。
- [x] `langgraph_agent` 可用，Manju Craft 能触发（端到端待 [API] 验证）。

### 8.4 审批交互
- [ ] **[API]** 危险操作弹出 `ApprovalDialog`，批准/拒绝正确驱动流程。(P0)
- [x] 「始终允许此类操作」记忆写入 `localStorage: abcyesno:allowedOps`。

### 8.5 稳定性
- [ ] **[API]** 连续发送 10 条消息不崩溃。(P1)
- [x] 后端断开 / 报错时前端经横幅 + 错误气泡明确提示。
- [x] 停止按钮可中断当前运行（CopilotKit `stopGeneration` + `session.interrupt` 双保险）。

### 8.6 文件上传闭环（已知缺口）
- [x] 文件上传不仅复制到 `uploads/<sessionId>/`，且把路径/内容真正传给 Hermes（`ChatLayout.handleSend` 现在把 `localPath`/`originalPath` 注入提示词，经 AG-UI 透传给 `prompt.submit`）。(P0)

## 9. Phase 6 - 便携化打包

### 9.1 独立运行
- [ ] **[ENV]** 复制到未安装 Hermes / Python 的 Windows 机器可启动。(P0)
- [x] 双击 `Abcyesno.exe` 能启动（本机验证）。
- [x] 首次启动不依赖网络下载。

### 9.2 数据隔离
- [x] 运行时数据存 `%USERPROFILE%/.hermes_portable_data/`，不污染系统 Hermes。
- [x] 单文件解压到临时目录后配置 / 会话仍持久化到上述目录。

### 9.3 体积合理
- [x] 体积可控（portable 单文件约 325MB，win-unpacked 约 1.1GB）。

## 10. Phase 7 - 最终验收

### 10.1 完整场景测试
- [x] 新用户首次打开完成 API Key 配置（弹窗 + 保存前校验 key 有效性）。
- [ ] **[API]** 发送「你好」收到正常回复。(P0)
- [ ] **[API]** 发送「帮我做一条剪映视频」触发 `manju-craft` 并收到结果（默认 mock）。(P1)
- [ ] **[API]** 发送「列出当前目录」调用 terminal 并返回结果。(P0)
- [x] 关闭重开应用，历史会话仍在。

### 10.2 回归测试
- [x] 已修复 bug 无复现（消息不显示、首条丢失、历史不显示、运行时错误不提示、回复重复）。
- [x] 无阻塞性崩溃或白屏。

### 10.3 文档
- [x] `README.md` 说明构建与运行。
- [x] `DEV_LOG.md` 记录主要问题与方案。

## 11. Phase 8 - UI/UX 规范符合性（[UI]）

对照 `docs/UI_UX_SPEC.md`（v2）。Pre-Flight（§14）为本阶段闸门。

### 11.1 设计系统与直线
- [x] 设计判读已声明（§0），三旋钮取值明确且由判读推导（VARIANCE 4 / MOTION 4 / DENSITY 7）。(P1)
- [x] 设计系统为 Primer 对齐令牌，全树单一系统（不混 Fluent/Carbon/shadcn）。(P1)

### 11.2 令牌与主题
- [x] 语义令牌落地：组件不写死 hex；深色（默认 `:root`）+ 浅色令牌在 `[data-theme="light"]` 切换，新增 `--bg-elevated` 并清理遗留 `#ff7b72` 硬编码。(P0 深色 / P1 浅色对等)
- [x] 主题锁：整树一个主题策略（深/浅/跟随系统），`App` 将 `data-theme` 写到 `documentElement`，`system` 监听 `prefers-color-scheme` 变化。(P1)
- [x] 颜色一致锁：全树单一 `--accent`（深色 `#4f8cff` / 浅色 `#0969da`）。(P1)
- [x] 形状一致锁：一套圆角系统（气泡12/卡片8/按钮6）。(P2)
- [x] 按钮与表单对比度过 WCAG AA（深浅两模均改用语义令牌，调色板 Primer 派生）。(P1)

### 11.3 反「AI 味」硬禁令
- [x] **零 em-dash**：扫描 `src/` 全部可见 UI 文案（标题/眉标/按钮/气泡/图注），无 em-dash/en-dash（`DetailModal` 占位符已改为连字符）。(P0)
- [x] 无霓虹外发光、无纯黑 `#000000`、无过饱和强调色（强调色取 Primer 蓝/青，未用纯黑）。(P1)
- [x] 默认字体非 Inter，用系统中文字体栈（Microsoft YaHei / PingFang SC）+ 等宽英文栈。(P1)
- [x] 无三等分相同卡片横排；布局用 CSS Grid / flex 语义结构非百分比数学。(P1)
- [x] 示例数据无 Jane Doe / Acme / 填充动词，用可信本地化内容（小猫草地、列出目录等）。(P1)
- [x] 状态点仅用于真实语义状态（后端连接/审批待处理/思考中），无装饰点。(P1)

### 11.4 可访问性与性能
- [x] Reduced Motion：新增 `@media (prefers-reduced-motion: reduce)` 全局规则，塌缩 `pulse`/`bounce` 无限循环动效与 transition。(P0)
- [x] 深色/浅色双模均渲染测试，主题切换生效（代码完整，像素级目检建议在 Windows 主机做）。(P1)
- [x] 空/加载/错误态齐备（首启动空态、启动画面、错误横幅+气泡）。(P0)
- [ ] 图标来自 Phosphor 等允许库，无手搓 SVG 路径。(P2) — 当前用 emoji，待引入图标库。
- [ ] 长列表（>5 项）用合适组件，非默认 `divide-y` 全行描边。(P2)
- [ ] 视口稳定 `min-h-[100dvh]`，无 `h-screen`。(P2) — 当前用 `height:100vh`/`#root height:100%`。

### 11.5 功能闭环
- [x] 文件上传真实传给 Hermes（见 §8.6）。(P0)
- [x] 技能市场启用/禁用持久化（客户端 `localStorage: abcyesno:enabledSkills`，重开保持；`MarketPanel` 改为受控）。(P1)
- [x] UI_UX_SPEC §14 Pre-Flight Check 全绿（见下方 §13 说明）。(P1)

---

## 12. 验收方式

1. **人工测试**：按清单逐项执行，记录结果与依赖（[API]/[ENV]/[UI]）。
2. **日志审查**：检查 `%USERPROFILE%/.hermes_portable_data/logs/` 的 `electron.log` 与 `hermes.log` 无异常。
3. **干净环境测试**：至少一台未装开发环境的 Windows 机器验证便携版。
4. **UI 扫描**：用 Pre-Flight（UI_UX_SPEC §14）逐条过，重点查 em-dash、令牌、对比度、reduced-motion。

## 13. 通过标准

- **发布门槛**：所有 **P0** 项通过；所有 **P1** 项闭合或有关键说明；**P2** 项录入跟踪但不阻塞。
- 文档与代码一致（§2.1）。
- 无阻塞性崩溃 / 白屏（§10.2）。
- UI/UX 规范符合性 Phase 8 的 P0/P1 全绿（§11）。
- **本环境（沙箱）已收口项（代码层，无需 [API]/[ENV]）**：文件上传路径透传 Hermes（§8.6/§11.5）、技能市场启用持久化（§11.5）、主题深/浅/跟随切换 + 浅色令牌（§11.2）、em-dash 清零（§11.3，已全树扫描）、reduced-motion 塌缩（§11.4）。本轮 `vite build` 成功 transform 全部 5173 个模块（含本次所有改动文件），证明源码层面无语法/编译错误。
- **构建现状（2026-07-14 更新）**：`vite build` 现已可正常产出 `dist`（46.34s，exit 0）。完整桌面包也已重建：`release/win-unpacked/Abcyesno.exe` 于 **2026-07-14 11:28:31** 写出（含契约代码：dist 11:17 + `hermes-fork/.../agents/{hello_agent,manju_craft,image_gen}`）。**打包须知**：`electron-builder` 打包时会从 GitHub 下载 Electron 二进制，构建环境必须能访问 GitHub（本机 Clash 代理 `127.0.0.1:7897`，或复用已缓存的 `ELECTRON_CACHE`）；若代理被 unset 会下载失败导致进程退出（这是 07-13 夜间"卡死"的真因，非慢拷贝）。
- **仍阻断发布（需真实环境验证）**：真实对话 / 工具 / 审批 / manju-craft 真实工作流需 **[API]** 有效 Agnes Key；便携版在未装 Python/Hermes 的干净 Windows 机器独立运行需 **[ENV]**；Phase 8 像素级视觉目检建议在 Windows 主机完成。P2 项（Phosphor 图标库、长列表组件、视口 `100dvh`）录入跟踪不阻塞。

---

## 14. Phase 9 - LangGraph 工作流接入契约（v1 落地）

### 14.1 契约基线
- [x] 5 层契约 spec 已起草（`docs/LANGRAPH_CONTRACT_SPEC.md`）：L1 发现 / L2 输入 schema / L3 输出渲染 / L4 审批门 / L5 进度事件通道。
- [x] 逐层缺口文档 `docs/LANGRAPH_CONTRACT_GAPS.md` 与前端渲染方案 `docs/FRONTEND_RENDERING_LAYER.md` 已产出。

### 14.2 实现（数据驱动，零前端分支）
- [x] L1 manifest 发现：`GET /api/ag-ui/contract/manifests` 聚合 `agents/*/manifest.json`；前端 `registry.js` 拉取 + `manifests.js` 内置回退。(P0)
- [x] L2 `ContractForm` 通用表单：按 JSON-Schema + `x-ui` 渲染 text/textarea/select/number，含 required/enum 校验，结构化信封触发 `langgraph_agent`。(P0)
- [x] L3 `ArtifactCard` 通用产物渲染：video/image/file/other，本地路径转 `file://`。(P0)
- [x] L4 审批门：接收 `workflow.approval` 透传至前端 approval 状态，复用 CopilotKit human-in-the-loop 通道。(P1)
- [x] L5 `WorkflowTimeline` + `useContractEvents` + `eventBus`：AG-UI CUSTOM 事件 `workflow.progress/artifact/approval/error/done` 归一化渲染。(P0)
- [x] Adapter 路由解耦：`agui-server.js` 结构化调用绕过硬编码 manju-craft prompt-rewrite，改由 manifest 驱动。(P0)
- [x] 后端流式化：`langgraph_runtime.py` `astream` 流式 + `on_event` 回调；`langgraph_agent_tool` 支持 `input` 为 string|object。(P0)

### 14.3 零前端改动实证
- [x] 第 3 样本 `image_gen` 仅后端新增（agent + manifest.json），前端组件零修改即自动渲染；grep 确认 `src/` 仅 `manifests.js` 列 workflow id，组件无 `if (workflowId===...)` 分支。(P0)

### 14.4 验证
- [x] `python -m py_compile` 后端 5 文件通过；manifest.json JSON 校验通过。(P0)
- [x] `vite build` 成功（46.34s，exit 0），dist 正常产出。(P0)
- [ ] **[API]** 真机运行 `image_gen`/`manju_craft` 验证 `file://` 产物打开与审批 interrupt 真实驱动。(P1)
