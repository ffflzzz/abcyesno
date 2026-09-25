## v1.4.5

### 修复（thinking 框全程可见 + agent 提速）
- **thinking 框运行中缺失（真根因）**：reasoning.delta 从未写入 agent 时间线（补丁静默失败）——网关实测 588 个 delta 正常流入、SSE 转发 355 个正常，缺失纯在 React 层。修复后：推理开始几秒内即弹出深度推理框并实时滚动
- **过程流运行时崩溃**：AgentProcessStream 引用未定义的 loading（vite 不查未定义标识符）→ 传参修复
- **新一轮 thinking 串进旧回合 thinking 框**：被中断的回合残留 currentAssistantId，RUN_STARTED 现在显式清空
- **审批点击永远失败（agent 执行慢的主因）**：approval.respond 缺 session_id → 每次点批准都报 session not found，工具干等 300s 审批超时（实测一轮 2×300s 空转）。修复后点批准立即生效
- **回合结束布局**：保留时间线分段完成态（推理条/工具条/回复行按真实时序排列），回复不再夹在工具条之间
- **已完成工具段收纳节奏**：完成后保留 2s 展示再收纳，消除闪烁

