:

# ADR 001: 采用 Fork Hermes 二次开发方案

## 状态

- **日期**：2026-07-11
- **状态**：已采纳
- **决策者**：项目所有者 + 开发助手

## 背景

`hermes-portable` 项目最初是一个独立的 Electron + React + Node.js 应用，内置了一个自实现的 Agent 后端。该实现：

- 无法复用 Hermes 官方成熟的 harness（进程管理、工具系统、审批、记忆、gateway）。
- 需要自己维护模型调用、工具执行、会话状态，开发成本高且不稳定。
- 与真正的 Hermes Agent 能力差距大。

用户的核心诉求是：

1. 做一个真正的 Hermes 便携版。
2. 能够方便地接入自定义 LangGraph Agent（如 `manju-craft`）。
3. 不想重复造轮子，希望复用 Hermes 的 harness 工程。

## 决策

**采用 Fork Hermes 源码 + 二次开发方案：**

- 将 Hermes 源码复制到 `hermes-fork/`。
- 拆除 IM 通道、官方更新、Telemetry 等非必要模块。
- 保留 harness 核心：agent loop、tools、skills、memory、gateway、session。
- 用 Hermes 本身作为中央 Agent 调度器。
- LangGraph Agent 以 Hermes skill 形式接入。
- Electron + React 替代官方 desktop 作为前端壳。
- Electron Main 中保留 AG-UI Runtime Bridge，让前端继续使用 CopilotKit。

## 备选方案

### 方案 A：完全自研 Orchestrator + 直接调用 Agnes API
- **优点**：完全可控，不受 Hermes 更新影响。
- **缺点**：需要重新实现 harness 的全部能力，超出团队当前投入。
- **结论**：否决。用户明确表示不想重复造轮子。

### 方案 B：薄层 Orchestrator + 调用 `hermes serve`
- **优点**：比方案 A 轻量，前端和 backend 解耦。
- **缺点**：仍然要跨进程对接 Hermes JSON-RPC/WS，且 Hermes 本身的 skill 系统没有被充分利用。
- **结论**：否决。不如直接让 Hermes 当调度器，深度复用 skill 系统。

### 方案 C：Fork Hermes 并二次开发（本方案）
- **优点**：
  - 最大程度复用 Hermes harness。
  - LangGraph Agent 天然以 skill 形式接入。
  - 可以拆除不需要的模块，控制产品边界。
- **缺点**：
  - 需要维护一个 Hermes fork。
  - 官方更新后合并成本较高。
  - 需要理解 Hermes 内部模块结构。
- **结论**：采纳。与用户需求最匹配。

## 影响

### 代码库
- 新增 `hermes-fork/` 目录，存放 Hermes 源码副本。
- 新增 `docs/` 目录存放 PRD、SPEC、ROADMAP、ACCEPTANCE 等文档。
- 现有 `electron/backend/` 自实现代码将逐步废弃，替换为 AG-UI Bridge 和 Hermes Gateway Client。

### 开发流程
- 开发前需要先启动 Hermes serve。
- 新增 skill 需要理解 Hermes skill loader 机制。
- 需要定期 review Hermes 官方更新，评估是否合并。

### 部署形态
- 最终产物是 Electron + Hermes fork + Python 环境的便携包。
- 用户无需单独安装 Hermes 官方包。

## 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| Hermes 模块耦合紧，拆除时容易破坏启动 | 小步删除，每次删除后跑 `hermes serve` 验证 |
| 官方更新后合并困难 | 用 git 管理 fork，记录所有 patch；只合并安全更新 |
| Skill loader 接口复杂 | 优先调研源码，用最小 demo 验证 |
| Python 便携化体积大 | 使用 embedded Python 或精简 venv |

## 后续行动

1. 按 `ROADMAP.md` Phase 0 复制 Hermes 源码并验证启动。
2. 按 `STRIPPING_GUIDE.md` 逐步移除非核心模块。
3. 调研 Hermes skill loader 和 gateway 事件协议。
