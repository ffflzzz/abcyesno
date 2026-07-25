# Abcyesno — Development Roadmap

## 总体策略

按「先让 Hermes fork 能跑 → 拆除非核心模块 → 接入自定义 skill → 替换前端 → 打包便携」的顺序推进。每个阶段都有明确验收点，避免一次性改动过大导致无法调试。

---

## Phase 0：项目初始化与基线建立

**目标**：把 Hermes 源码复制到当前项目，并验证能启动 `hermes serve`。

### 任务清单
1. 将 `C:\Users\Administrator\AppData\Local\hermes\hermes-agent\` 复制到 `hermes-portable/hermes-fork/`。
2. 在 `hermes-fork/` 下创建独立的 Python venv（与系统隔离）。
3. 安装 Hermes 核心依赖（`pip install -e .`）。
4. 运行 `python hermes-fork/cli.py serve --port 9119`，确认 gateway 启动。
5. 用浏览器或 curl 访问 `http://127.0.0.1:9119/api/status`，确认返回正常。

### 验收标准
- `hermes serve` 能在项目目录下成功启动。
- `/api/status` 返回 200。
- 无启动阶段 import error。

---

## Phase 1：Hermes 源码精简

**目标**：拆除 IM 通道、官方更新、Telemetry 等非必要模块，保留 harness 核心。

### 任务清单
1. **识别并列出非核心模块**：
   - IM：`skills/whatsapp/`, `skills/whatsapp-cloud/`, `skills/slack/`, `tools/discord_tool.py`
   - 更新：搜索 `update` 相关调用和后台任务
   - Telemetry：搜索 `analytics`, `telemetry`, `sentry` 等
   - 官方 Desktop：`apps/desktop/`
   - Docker/Nix：`docker/`, `flake.nix`
2. **安全移除**：
   - 删除目录/文件。
   - 修改 `cli.py` 中对应的子命令注册，避免引用已删除模块。
   - 修改 `pyproject.toml` / `requirements`，移除相关依赖。
3. **运行冒烟测试**：
   - `hermes serve` 仍能启动。
   - `hermes chat -q "hello"` 能返回结果（使用 Agnes API）。

### 验收标准
- `hermes serve` 启动后无 IM 相关模块加载。
- CLI 中无 whatsapp/slack 等子命令。
- 原生 chat 流程能跑通一次完整对话。

---

## Phase 2：Hermes Harness 核心能力验证

**目标**：确认精简后的 Hermes 仍具备 agent loop、tool、approval、session 等核心能力。

### 任务清单
1. 使用 Agnes API Key 配置 Hermes。
2. 测试一次带工具调用的对话（如「查一下当前目录有什么文件」）。
3. 测试审批流程：执行 terminal 命令时是否弹出/返回 approval 请求。
4. 验证 session 历史是否被正确保存。
5. 验证 memory 是否工作（多轮对话上下文保持）。

### 验收标准
- Agent 能调用 terminal / browser / file 工具。
- 危险操作触发 approval。
- 会话能持久化，关闭后重新打开可继续。

---

## Phase 3：LangGraph Skill Adapter 实现

**目标**：建立一套机制，能把任意 LangGraph graph 注册为 Hermes skill。

### 任务清单
1. 调研 Hermes skill loader 机制：
   - 读取 `skills/` 目录的方式。
   - manifest 或入口点约定。
   - skill 类/工厂函数签名。
2. 创建 `hermes-fork/skills/langgraph_agents/__init__.py` 和基础 adapter。
3. 编写一个最小 LangGraph skill demo（如 echo skill）。
4. 在 Hermes 中注册并调用该 demo skill。
5. 将 `manju-craft` 的 graph 接入该 adapter。

### 验收标准
- Hermes 能发现并加载 demo LangGraph skill。
- 通过 gateway 调用该 skill，能返回流式事件。
- `manju-craft` skill 能被调用并产生输出。

---

## Phase 4：接入 `manju-craft`

**目标**：让 `manju-craft` 作为 Hermes skill 被调用，完成第一个端到端 demo。

### 任务清单
1. 在 `hermes-fork/skills/langgraph_agents/` 下创建 `manju_craft/`。
2. `manju_craft/skill.py` 中加载 `C:\Users\Administrator\Desktop\manju-craft\graph\graph.py`。
3. 实现事件标准化：把 LangGraph 事件转换为前端可识别的 `token/tool_call/tool_result/done/error`。
4. 配置 skill 路由：让用户输入「做视频」等关键词时调用 `manju-craft`。
5. 运行一次完整对话：用户输入 → Hermes 路由 → manju-craft skill → 返回结果。

### 验收标准
- 用户在 Hermes chat 中发送相关指令，能触发 `manju-craft`。
- `manju-craft` 返回的事件能在后端日志中观察到。
- 对话结果正确。

---

## Phase 5：Electron 前端替代官方 Desktop

**目标**：用我们自己的 React/Electron 前端连接 Hermes gateway，替代官方 desktop。

### 任务清单
1. 精简当前 `src/` 和 `electron/`，移除自实现 backend（`agent-loop.js`, `api.js`, `tools.js` 等）。
2. `electron/main.js` 改为：
   - 启动 `hermes serve`。
   - 连接 gateway WebSocket。
   - 创建 BrowserWindow 加载前端。
3. `electron/preload.js` 暴露：
   - `sendPrompt(text)`
   - `onEvent(channel, cb)`
   - `respondApproval(id, choice)`
4. 前端实现聊天界面：
   - 用户消息列表。
   - 流式 assistant 消息。
   - 工具事件展示。
   - 审批弹窗。
5. 测试前端与 Hermes gateway 的完整链路。

### 验收标准
- Electron 应用启动时自动拉起 `hermes serve`。
- 前端发送消息，Hermes 返回流式回复。
- 工具调用和审批能在前端显示并交互。

---

## Phase 6：便携化打包

**目标**：把整个应用打包成可复制的便携版。

### 任务清单
1. 调研 Python 便携方案：
   - Windows embedded Python
   - 项目内 venv + 相对路径启动
   - PyInstaller 打包 `hermes serve`
2. 选择方案并集成到 Electron Builder。
3. 打包 `hermes-fork/` + Python 环境 + Electron 前端。
4. 编写启动脚本，自动设置 `HERMES_HOME` 和 `PATH`。
5. 在干净 Windows 环境测试双击运行。

### 验收标准
- 复制 `release/` 文件夹到新 Windows 机器，双击 exe 能启动。
- 无需预先安装 Hermes 或 Python。
- 能完成一次完整对话。

---

## Phase 7：验收与文档

**目标**：完成所有验收测试，整理文档。

### 任务清单
1. 按 `ACCEPTANCE.md` 执行全部测试用例。
2. 修复验收过程中发现的问题。
3. 更新 `DEV_LOG.md` 记录关键改动。
4. 输出最终便携版到 `release/`。

### 验收标准
- `ACCEPTANCE.md` 中所有条目通过。
- 无 blocker 级别 bug。
- 文档与代码一致。

---

## 时间估算（粗略）

| 阶段 | 预估时间 |
|------|----------|
| Phase 0 | 0.5 ~ 1 天 |
| Phase 1 | 1 ~ 2 天 |
| Phase 2 | 1 ~ 2 天 |
| Phase 3 | 2 ~ 3 天 |
| Phase 4 | 2 ~ 3 天 |
| Phase 5 | 2 ~ 3 天 |
| Phase 6 | 3 ~ 5 天 |
| Phase 7 | 1 ~ 2 天 |

**总计**：约 2 ~ 3 周（按全职投入估算）。

---

## 风险与应对

| 风险 | 应对 |
|------|------|
| Hermes 模块耦合紧，拆除时容易破坏启动 | 小步删除，每次删除后跑 `hermes serve` 验证 |
| Skill loader 接口复杂 | 优先调研源码，必要时用最小 demo 验证 |
| Gateway 事件协议文档不全 | 用官方 desktop 源码和实际抓包确认 |
| Python 便携化体积过大 | 使用 embedded Python + 精简依赖 |
| 打包后路径问题 | 启动脚本用相对路径，设置 `HERMES_HOME` |
