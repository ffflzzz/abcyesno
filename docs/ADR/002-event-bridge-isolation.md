# ADR 002 — LangGraph 事件桥：重连/缓冲 vs 子进程隔离

- 状态：已接受（缓冲 + 重试退避）；子进程隔离评估后暂缓
- 日期：2026-08-15

## 背景

LangGraph agent 以「tool delegation + 同进程后台线程 + 事件桥」接入：
1. 前端/模型触发 `langgraph_agent` 工具 → Python 侧在**同进程 daemon 线程**上跑图；
2. 图执行期间通过 HTTP POST 把 `workflow.*` 事件发到 Node `agui-server`；
3. Node 按 `wfRunId` 中继到对应 SSE 流。

风险：事件桥是 fire-and-forget，订阅者未注册 / SSE 断开的瞬态窗口会把事件
**静默丢弃**（尤其 `workflow.started` 早到 → 后台 run 未置 keepOpen → SSE 提前
关闭 → 后续事件全丢）。

## 选项

### A. 同进程后台线程 + 事件桥缓冲/重试（已采纳）
- Node 侧：`pendingEventBuffer` 按 runId 暂存无订阅者事件，订阅注册时回放；
  TTL + 上限兜底防泄漏。
- Python 侧：`emit()` 加 0.5/1/2s 退避重试（仅失败路径阻塞）。
- 成本：约 40 行，零部署/进程模型变化。

### B. 隔离到子进程（暂缓）
- 每个 workflow run 用 `subprocess`/`multiprocessing` 起独立进程跑图。
- 优点：崩溃隔离（图 panic 不拖垮 Hermes）、内存隔离、可用独立 GPU/配额。
- 代价：
  1. 事件桥仍需跨进程（HTTP/管道），**不消除反而放大**缓冲/重连需求；
  2. LangGraph 图 + `interrupt()` HITL 依赖文件控制通道（`workflow_hitl/*.json`），
     跨进程后需额外 IPC 或共享盘，`_ACTIVE_RUNS` 去重锁也要换跨进程原语；
  3. 每个 run 都要加载 agent 模块/OpenAI client，冷启动开销 + 进程数膨胀；
  4. 打包（Electron `extraResources` + 内置 Chromium 已很重）再叠加多进程，
     复杂度与排障成本显著上升。
- 结论：当前规模（单生产 agent，串行/低并发）下，A 的崩溃风险可接受。若未来
  出现「图内 segfault 频繁」「需强内存/CPU 配额」「并发多 agent」任一信号，再
  演进到 B，且事件桥缓冲层可原样复用。

## 决策

采用 **A**。事件桥缓冲/重试是 B 方案下也必需的基础设施，先落地它，子进程隔离
留作规模化后的增量演进。
