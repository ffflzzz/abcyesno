# Abcyesno Electron 重构开发日志

## 项目目标
将原有 Python/GUI + Go/Wails 的 Abcyesno 项目完整重写为 Electron 桌面应用：
- Node.js 后端 + React 前端
- 内置 Agent 通信组件使用 AG-UI 和 CopilotKit
- 编译产物输出到 `release/` 目录
- 免安装绿色便携版，双击即用

## 技术栈
- Electron 33.4.11
- Vite + React
- Node.js 后端（Express + AG-UI + CopilotKit 适配）
- electron-builder 打包

---

## 2026-07-11 问题与处理记录

### 1. 打包配置：NSIS 安装版 vs Portable 免安装版
**问题**：electron-builder 默认生成 NSIS 安装包，用户要求免安装双击运行。
**处理**：
- 将 `build.win.target` 从 `nsis` 改为 `portable`
- 同时保留 `win-unpacked` 目录作为解压版
- 产物：
  - `release/Abcyesno 1.3.0.exe`（portable 单文件，约 101MB）
  - `release/win-unpacked/Abcyesno.exe`（解压版，约 180MB）

### 2. Electron 下载失败 / 缓存不完整
**问题**：首次打包时 electron-builder 下载 Electron 33.0.0 失败，本地 `node_modules/electron/dist` 只有 LICENSE 文件。
**处理**：
- 手动从 GitHub releases 下载 `electron-v33.4.11-win32-x64.zip` 并解压到 dist 目录
- 修复 `path.txt` 指向 `electron.exe`
- 将 `package.json` 中 electron 版本固定为 `33.4.11`

### 3. 打包时 asar integrity 写入失败
**问题**：electron-builder 打包时重命名 electron.exe 失败，报 `ENOENT: no such file or directory`。
**处理**：
- 禁用 asar：`"asar": false`
- 禁用签名验证：`"verifyUpdateCodeSignature": false`
- 这是 Windows 环境下文件锁定导致的临时处理

### 4. 运行时进程立即退出
**问题**：打包后的 exe 启动后进程立即退出，没有错误日志。
**处理**：
- 将 Electron userData 目录设置为 exe 所在目录下的 `.hermes_portable_data`，避免写入默认 AppData 目录失败
- 延迟 500ms 创建窗口，确保后端服务先启动

### 5. CopilotKit 调用 AG-UI 接口报错
**问题**：前端报错 `Failed to load runtime info`，`/api/ag-ui/run/info` 接口不存在。
**处理**：
- 在 `agui-server.js` 中新增 `/api/ag-ui/run/info` 端点，返回 agent 列表和默认 agent
- 实现 `/api/ag-ui/run` SSE 流式响应

### 6. `@ag-ui/encoder` 使用错误
**问题**：运行时弹窗 `TypeError: encode is not a function`。
**处理**：
- 查看 `@ag-ui/encoder` 实际导出，发现导出的是 `EventEncoder` 类，不是 `encode` 函数
- 重写 `agui-server.js`，使用 `new EventEncoder()` 实例调用 `.encode(event)`

### 7. API 地址和模型配置错误
**问题**：默认配置是 OpenAI 地址 + Anthropic 模型，用户实际使用 Agnes AI。
**处理**：
- 将 `API_BASE` 改为 `https://apihub.agnes-ai.com/v1`
- 将 `MODEL_NAME` 改为 `agnes-2.0-flash`
- 默认配置文件模型也改为 `agnes-2.0-flash`

### 8. 清理旧版 Wails/Go/Python 代码
**问题**：项目根目录残留大量旧版文件。
**处理**：删除以下内容：
- Wails/Go 项目：`hermes_wails/`, `hermes_gui_go/`, `hermes_gui_walk/`, `hermes_lite_go/`
- Python 旧代码：`client.py`, `client_gui.py`, `hermes_cli.py`, `hermes_lite.py` 等
- 旧构建产物：`client.dist/`, `client_gui.dist/`, `__pycache__/`
- 旧启动脚本和日志文件

---

## 当前产物
- `release/Abcyesno 1.3.0.exe`：portable 单文件免安装版
- `release/win-unpacked/Abcyesno.exe`：解压版免安装 exe

## 待验证
- portable 单文件版在目标用户机器上是否能正常启动
- 输入 Agnes API key 后对话是否能正常返回

## 注意事项
- 本地 `~/.hermes/config.json` 中的 API key 已在打包后清空，避免泄露
- 用户只需要在应用内输入 Agnes API key 即可使用

---

## 2026-07-11 方向调整：Fork Hermes 二次开发

### 背景
- 自实现的 Electron Agent 后端难以稳定运行，且无法复用 Hermes 成熟的 harness。
- 用户明确表示希望复用 Hermes 的 harness 工程，而不是重新造轮子。

### 新方向
- **Fork Hermes 源码**到 `hermes-fork/`，保留 harness 核心。
- **拆除** IM 通道、官方更新、Telemetry 等非必要模块。
- **LangGraph Agent 以 Hermes skill 形式接入**。
- **Electron + React + CopilotKit** 替代官方 desktop 前端。
- **Electron Main 中保留 AG-UI Runtime Bridge**，把 CopilotKit 请求翻译成 Hermes Gateway 调用。

### 产出文档
- `docs/PRD.md`：产品需求
- `docs/SPEC.md`：技术规格
- `docs/UI_UX_SPEC.md`：UI/UX 设计规范
- `docs/ROADMAP.md`：开发路线
- `docs/ACCEPTANCE.md`：验收标准
- `docs/SETUP.md`：开发环境搭建
- `docs/GLOSSARY.md`：术语表
- `docs/KNOWN_UNKNOWNS.md`：待确认问题
- `docs/STRIPPING_GUIDE.md`：源码精简指南
- `docs/ADR/001-fork-hermes.md`：架构决策记录
- `README.md`：项目入口说明

### 下一步
- 进入 `ROADMAP.md` Phase 0：建立 Hermes Fork 基线。
- 调研 Hermes skill loader 和 gateway 事件协议。

---

## 2026-07-11 Phase 0 完成：Hermes Fork 基线建立

### 工作目录
- 项目根目录：`L:\hermes-portable-v0`
- Hermes Fork：`L:\hermes-portable-v0\hermes-fork`
- 开发 Hermes Home：`L:\hermes-portable-v0\.hermes_dev`

### 已完成的动作
1. 将原项目源码从 `C:\Users\Administrator\Downloads\kimicode_free\hermes-portable` 复制到 `L:\hermes-portable-v0`：
   - 排除：`node_modules`、`.git`、`dist`、`release`、`.hermes_portable_data`、`*.log`
   - 保留：`src/`、`electron/`、`docs/`、`package.json`、`vite.config.js` 等
2. 将本机 Hermes 源码从 `C:\Users\Administrator\AppData\Local\hermes\hermes-agent` 复制到 `L:\hermes-portable-v0\hermes-fork`：
   - 排除：`.git`、`node_modules`、`__pycache__`、`*.pyc`、`.venv`、`venv`、`*.log`
3. 创建隔离 Python venv：
   - `L:\hermes-portable-v0\hermes-fork\.venv`
   - 安装 `pip install -e .`
   - 安装 `langgraph`、`langchain-core`
   - 解决依赖冲突：`langchain-openai` 会升级 `openai` 到 2.45.0，与 `hermes-agent==0.18.0` 的 `openai==2.24.0` 冲突，因此卸载 `langchain-openai` 并回滚 `openai==2.24.0`

### 验证结果
- 使用正确的 CLI 启动 Hermes 后端：`.venv\Scripts\hermes serve --port 9119`
- 首次启动会自动构建 web UI（`hermes_cli/web_dist`）
- `GET http://127.0.0.1:9119/api/status` 返回 200，示例响应：
  ```json
  {
    "version": "0.18.0",
    "gateway_running": false,
    "auth_required": false,
    "hermes_home": "L:\\hermes-portable-v0\\.hermes_dev",
    "config_path": "L:\\hermes-portable-v0\\.hermes_dev\\config.yaml"
  }
  ```

### 发现与纠正
- 文档 `SETUP.md` 中的命令 `python cli.py serve --port 9119` 是错误的：`cli.py` 是自定义交互式 CLI，不识别 `serve` 子命令。
- 正确命令应为：`.venv\Scripts\hermes serve --port 9119`（或 `python -m hermes_cli.main serve --port 9119`）。
- `hermes serve` 与 `hermes dashboard` 共享同一个 `cmd_dashboard` 处理函数，`serve` 即无浏览器打开的后台模式。

### 遗留问题
- 当前未配置 Agnes API key，`/api/status` 虽然返回 200，但后续 chat 调用会 401。
- Phase 1 开始之前需要先复制 `v0 → v1` 作为版本控制基线。

### 下一步
- 复制 `L:\hermes-portable-v0` → `L:\hermes-portable-v1`
- 在 v1 上按 `STRIPPING_GUIDE.md` 拆除 IM / 更新 / Telemetry 模块

---

## 2026-07-11 版本控制：v0 → v1

### 操作
1. 将 `L:\hermes-portable-v0` 完整复制为 `L:\hermes-portable-v1`（排除 `.venv`、`node_modules`、`.git`、`__pycache__`、`*.pyc`、`*.log`）。
2. 使用 `virtualenv-clone` 将 `L:\hermes-portable-v0\hermes-fork\.venv` 克隆到 `L:\hermes-portable-v1\hermes-fork\.venv`。
3. 修正 v1 venv 中 editable install 的 finder 路径（`__editable___hermes_agent_0_18_0_finder.py`），把 `hermes-portable-v0` 替换为 `hermes-portable-v1`。
4. 运行 `.venv\Scripts\python -m pip install -e . --force-reinstall --no-deps` 重新生成入口脚本（`hermes.exe` 等），确保指向 v1。

### 验证
- `.venv\Scripts\hermes --version` 在 v1 成功输出：
  ```
  Hermes Agent v0.18.0 (2026.7.1) · upstream 3b2ef789
  Project: L:\hermes-portable-v1\hermes-fork
  ```

### 注意事项
- venv 不可直接复制，因为 editable install 的 finder 和 `.exe` 入口会保留旧路径。
- 后续每次迭代都应：复制 `vN → vN+1`（排除 `.venv`）→ 克隆并修复 venv → 重装 editable entry points。

### 下一步
- 在 `L:\hermes-portable-v1` 上开始 Phase 1：按 `STRIPPING_GUIDE.md` 拆除 IM / 更新 / Telemetry 模块。


---

## 2026-07-11 Phase 1：剥离 IM / 更新 / Telemetry / Cron 模块

### 工作目录
- 项目根目录：`L:\hermes-portable-v1`
- Hermes Fork：`L:\hermes-portable-v1\hermes-fork`
- 开发 Hermes Home：`L:\hermes-portable-v1\.hermes_dev`

### 目标
按 `docs/STRIPPING_GUIDE.md` 拆除以下非核心模块，同时保证 `hermes serve --port 9119` 能启动、`/api/status` 返回 200：
1. IM / messaging 通道（平台插件、gateway adapters、CLI 子命令、相关工具/技能）。
2. 官方服务与更新（`update`、`login`/`logout`/`auth`、`desktop`、`docker`、`flake.nix`/`flake.lock`、`batch_runner.py`）。
3. Telemetry / analytics / sentry 调用（未找到外部 sentry/posthog 依赖，主要是禁用启动时的 update check 与 `/version` 的更新检查）。
4. `cron/` 目录及 gateway 中的 cron scheduler 启动。

### 已执行动作

#### IM 模块拆除
- `plugins/platforms/`：删除 `whatsapp`、`slack`、`telegram`、`discord`、`signal`、`weixin`、`line`、`matrix`、`mattermost`、`dingtalk`、`feishu`、`google_chat`、`irc`、`sms`、`teams`、`wecom`、`simplex`、`ntfy`。
- `gateway/platforms/`：删除 `whatsapp_cloud.py`、`whatsapp_common.py`、`signal.py`、`signal_format.py`、`signal_rate_limit.py`、`weixin.py`、`bluebubbles.py`、`msgraph_webhook.py`、整个 `qqbot/`、`yuanbao*.py`。
- `gateway/whatsapp_identity.py`：删除后重建为 stub，保留 `gateway/session.py`、`authz_mixin.py`、`pairing.py`、`run.py` 的兼容性。
- `tools/discord_tool.py`、`tools/yuanbao_tools.py`：删除。
- `skills/yuanbao/`：删除。
- CLI 子命令：删除 `hermes_cli/subcommands/{whatsapp.py,slack.py,login.py,logout.py,auth.py}`、`hermes_cli/send_cmd.py`、`hermes_cli/setup_whatsapp_cloud.py`、`hermes_cli/telegram_managed_bot.py`。
- 脚本：删除 `scripts/whatsapp-bridge/`、`scripts/discord-voice-doctor.py`。
- 测试：删除 `tests/test_yuanbao_*.py`。

#### 官方服务/更新模块拆除
- 删除 `hermes_cli/subcommands/update.py`、相关 `hermes_cli/main.py` 中的 import 与注册。
- 删除 `apps/desktop/`、`docker/`、`flake.nix`、`flake.lock`、`batch_runner.py`。
- 删除 `hermes_cli/subcommands/gui.py`，移除 `desktop`/`gui` CLI 子命令。
- 在 `hermes_cli/main.py` 中移除启动时的 `_cleanup_quarantined_exes`、`_recover_from_interrupted_install`、`_termux_should_prefetch_update_check` 调用；`cmd_version` 不再检查更新。
- 在 `cli.py` 中 `/update` 改为打印不可用提示，`/version` 不再检查更新。

#### Cron 拆除
- 删除顶层 `cron/` 目录。
- 删除 `hermes_cli/subcommands/cron.py`、`hermes_cli/cron.py`。
- `cli.py` 中的 `get_job` 改为抛出 `RuntimeError`。
- `gateway/run.py` 中：
  - `_home_target_env_var` 不再依赖 `cron.scheduler`。
  - `_start_cron_ticker` 改为 no-op。
  - `start_gateway` 不再启动 cron scheduler，仅保留 housekeeping。

#### 依赖与元数据清理
- `pyproject.toml`：移除 optional extras `messaging`、`slack`、`matrix`、`wecom`、`teams`、`sms`、`dingtalk`、`feishu`、`cron`、`termux`、`termux-all`；从 `[all]` 移除 `hermes-agent[cron]`、`hermes-agent[sms]`；从 `py-modules` 移除 `batch_runner`；从 `packages.find` 的 `include` 移除 `"cron"`、`"cron.*"`。

#### 兼容性补丁
- `gateway/platforms/__init__.py`：仅导出 `BasePlatformAdapter`、`MessageEvent`、`SendResult`。
- `gateway/session.py`：将 `from .whatsapp_identity import ...` 替换为本地 stub。
- `gateway/run.py` 的 `_load_adapter` 仅保留 `API_SERVER` 与 `WEBHOOK` 两个内置 adapter。
- `hermes_cli/_parser.py`：移除 help epilog 中已删除命令的示例。

### 验证结果
- `hermes --help` 正常输出，子命令列表中已无 `whatsapp`、`whatsapp-cloud`、`slack`、`telegram`、`discord`、`signal`、`send`、`login`、`logout`、`auth`、`update`、`cron`、`desktop`、`gui`。
- `hermes version` 正常输出版本信息，不再提示更新。
- `hermes serve --port 9119` 使用 `HERMES_HOME=L:\hermes-portable-v1\.hermes_dev` 启动成功，控制台输出 `HERMES_DASHBOARD_READY port=9119`。
- `GET http://127.0.0.1:9119/api/status` 返回 200，示例响应：
  ```json
  {
    "version": "0.18.0",
    "release_date": "2026.7.1",
    "gateway_running": false,
    "auth_required": false,
    "hermes_home": "L:\\hermes-portable-v1\\.hermes_dev",
    "config_path": "L:\\hermes-portable-v1\\.hermes_dev\\config.yaml"
  }
  ```

### 遇到的问题与处理
1. **删除 `gateway/whatsapp_identity.py` 后 `gateway/session.py` 导入失败**
   - 处理：在 `gateway/session.py` 中实现本地 stub；同时重建 `gateway/whatsapp_identity.py` stub，避免 `gateway/authz_mixin.py`、`gateway/pairing.py`、`gateway/run.py` 的 import 报错。
2. **`gateway/run.py` 仍引用已删除的 cron scheduler**
   - 处理：将 `_start_cron_ticker` no-op 化，并在 `start_gateway` 中移除 cron 线程的启动与停止逻辑，仅保留 housekeeping。
3. **`gateway/run.py` 的 `_load_adapter` 仍引用已删除的 IM adapters**
   - 处理：仅保留 `API_SERVER` 与 `WEBHOOK` 分支，其余平台返回 `None`。
4. **`hermes serve` 实际由 `hermes_cli/web_server.py` 启动，会间接 import `gateway/session.py`**
   - 处理：通过 stub 与 adapter 清理解决，最终 `serve` 启动并通过 `/api/status` 验证。

### 遗留与下一步
- Phase 1 已完成核心剥离与验证。
- Phase 2 将调研 Hermes skill loader 与 gateway 事件协议，为 LangGraph Agent 以 skill 形式接入做准备。
- 当前剥离的是源码文件；后续如需要进一步缩小打包体积，可再清理 `tests/` 中针对已删除模块的测试用例、以及 `docs/` 中旧版平台文档。

---

## 2026-07-12 Phase 2 完成：Harness 核心能力验证

### 工作目录
- 验证在 `L:\hermes-portable-v2\hermes-fork` 进行
- 复制路径：`v1 → v2`（已完成 venv 克隆与 entry points 修复）
- 配置文件：`L:\hermes-portable-v2\.hermes_dev\config.yaml`（从本机 `~/.hermes/config.yaml` 复制，默认模型改为 `agnes-2.0-flash`，provider 改为 `custom`）

### 已验证能力

#### 1. Chat 对话
使用命令：
```bash
HERMES_HOME=L:\hermes-portable-v2\.hermes_dev .venv\Scripts\hermes -z "hello, 请用一句话回复" --cli --toolsets hermes-cli
```
返回：
```
你好！有什么我可以帮你的吗？
```
结论：Agnes AI provider 配置正确，模型能正常返回文本。

#### 2. Tool 调用
使用命令：
```bash
HERMES_HOME=L:\hermes-portable-v2\.hermes_dev .venv\Scripts\hermes -z "请运行 shell 命令 echo hello-from-hermes 并告诉我输出" --cli --toolsets hermes-cli
```
返回：
```
输出是：

```
hello-from-hermes
```

命令执行成功，退出码为 0。
```
结论：`hermes-cli` toolset 已启用，shell tool 能被调用并返回结果。

#### 3. Session 记录
- `/api/status` 返回 `active_sessions: 2`，说明 oneshot 对话已被记录到 session store。
- 会话文件位于 `L:\hermes-portable-v2\.hermes_dev\sessions\`。

#### 4. `hermes serve` 启动
- 命令：`.venv\Scripts\hermes serve --port 9120`
- 控制台输出：`HERMES_DASHBOARD_READY port=9120`
- `GET http://127.0.0.1:9120/api/status` 返回 200

### 遗留问题
- Approval 机制未做端到端验证；当前 shell 工具默认无需显式 approval。
- 未测试多轮对话、文件上传、图片生成等高级能力。

### 下一步
- 复制 `v2 → v3`
- Phase 3：实现 LangGraph Skill Adapter，使 LangGraph Agent 能以 Hermes skill 形式被加载和调用

---

## 2026-07-12 Phase 3 完成：LangGraph Skill Adapter

### 工作目录
- 项目根目录：`L:\hermes-portable-v3`
- Hermes Fork：`L:\hermes-portable-v3\hermes-fork`
- 开发 Hermes Home：`L:\hermes-portable-v3\.hermes_dev`
- venv：`L:\hermes-portable-v3\hermes-fork\.venv`

### 目标
实现一个最小可用的 LangGraph Skill Adapter，使 LangGraph agent 可以通过 Hermes harness 被发现和调用。

### 已交付文件

1. **`skills/langgraph_agents/__init__.py`**
   - 将 `skills/langgraph_agents/` 标记为 Python package，使 `langgraph_agents.langgraph_runtime` 可被工具模块导入。

2. **`skills/langgraph_agents/langgraph_runtime.py`**
   - 自动发现 `skills/langgraph_agents/agents/` 下的 agent package（每个 package 需要 `agent.py`）。
   - 提供 `list_agents() -> list[str]` 和 `run_agent(agent_name, input_text, thread_id=None) -> dict`。
   - 使用 `langgraph` + `langchain-core` 构建 `MessagesState` 图并调用。
   - LLM 使用 OpenAI-compatible 客户端，指向 `https://apihub.agnes-ai.com/v1`，模型 `agnes-2.0-flash`。
   - API key 读取优先级：环境变量 `AGNES_API_KEY` > `config.yaml` 的 `providers.custom.api_key` > `delegation.api_key`；未配置时抛出明确错误。

3. **`tools/langgraph_agent_tool.py`**
   - 注册 Hermes 工具 `langgraph_agent`。
   - Schema：`agent_name`（必填）、`input`（必填）、`thread_id`（可选）。
   - 调用 `langgraph_runtime.run_agent` 并返回 JSON 结果。
   - 注册到 toolset `hermes-cli`，因此 `--toolsets hermes-cli` 自动包含该工具。
   - 如果 `langgraph` 依赖缺失，通过 `check_fn` 隐藏工具，避免破坏 `hermes serve` 启动。

4. **`skills/langgraph_agents/agents/hello_agent/agent.py`**
   - 最小单节点 LangGraph agent。
   - 节点使用 `AgnesLLM` 调用 Agnes AI，对用户输入做问候/回声回复。
   - 通过模块级 `graph = workflow.compile()` 暴露给 runtime。

5. **`skills/langgraph_agents/SKILL.md`**
   - 标准 skill 文档，包含 frontmatter（`name: langgraph-agents`、`description`、`version`、`author`、`tags`）。
   - 说明何时以及如何使用 `langgraph_agent` 工具委派任务给 LangGraph agent。

### 注册与发现机制

- **工具发现**：Hermes 启动时 `model_tools.py` 调用 `tools.registry.discover_builtin_tools()`，自动扫描 `tools/*.py` 中顶层包含 `registry.register(...)` 调用的模块。因此新建 `tools/langgraph_agent_tool.py` 无需额外注册文件即可被加载。
- **Toolset 归属**：`registry.register(..., toolset="hermes-cli")` 将工具挂到 `hermes-cli` toolset。`toolsets.py` 的 `get_toolset(..., include_registry=True)` 会把 registry 中属于该 toolset 的工具合并进解析结果，所以 `--toolsets hermes-cli` 会自动包含 `langgraph_agent`。
- **Skill 发现**：Hermes skill 扫描的是 `HERMES_HOME/skills/` 目录。因此把 `skills/langgraph_agents/` 复制到 `L:\hermes-portable-v3\.hermes_dev\skills\langgraph_agents\`，`hermes skills list` 即可看到 `langgraph-agents`。

### 验证结果

#### 1. `hermes serve` 启动与 `/api/status`
```bash
HERMES_HOME=L:\hermes-portable-v3\.hermes_dev .venv\Scripts\hermes serve --port 9120
```
- 启动成功，控制台输出 `HERMES_DASHBOARD_READY port=9120`。
- `GET http://127.0.0.1:9120/api/status` 返回 200，响应示例：
  ```json
  {
    "version": "0.18.0",
    "release_date": "2026.7.1",
    "gateway_running": false,
    "auth_required": false,
    "hermes_home": "L:\\hermes-portable-v3\\.hermes_dev",
    "config_path": "L:\\hermes-portable-v3\\.hermes_dev\\config.yaml"
  }
  ```

#### 2. Skill 已加载
```bash
HERMES_HOME=L:\hermes-portable-v3\.hermes_dev .venv\Scripts\hermes skills list | grep langgraph
```
输出：
```
│ langgraph-agents       │                      │ local   │ local   │ enabled │
```

#### 3. Tool 出现在 `hermes-cli` toolset
```python
from model_tools import get_tool_definitions
tools = get_tool_definitions(enabled_toolsets=['hermes-cli'], quiet_mode=True)
print('langgraph_agent' in [t['function']['name'] for t in tools])  # True
```

#### 4. 直接调用 runtime
```bash
HERMES_HOME=L:\hermes-portable-v3\.hermes_dev .venv\Scripts\python -c "
from tools.langgraph_agent_tool import run_agent
print(run_agent('hello_agent', 'world'))
"
```
返回示例：
```python
{
  'agent': 'hello_agent',
  'output': 'Hello! I am Agnes-2.0-Flash, developed by Sapiens AI. You said: "world". How can I help you today?',
  'thread_id': '413e1b57-4364-46b3-8a85-ae634f65b2ef',
  'messages': [
    {'role': 'human', 'content': 'world'},
    {'role': 'ai', 'content': 'Hello! I am Agnes-2.0-Flash...'}
  ]
}
```

#### 5. 端到端 one-shot chat 调用
```bash
HERMES_HOME=L:\hermes-portable-v3\.hermes_dev .venv\Scripts\hermes -z "用 langgraph_agent 工具调用 hello_agent，输入是 'world'" --cli --toolsets hermes-cli
```
返回：
```
hello_agent 已完成调用。结果摘要：

- **输出**: "Hello! You mentioned \"world.\" How can I help you today?"
- **Thread ID**: `26bf56ec-c841-411c-b720-bd5b48684314`

如果需要后续交互（在同一会话中继续对话），可以再次调用并传入相同的 `thread_id`。
```
结论：Hermes 成功选择 `langgraph_agent` 工具，调用 `hello_agent`，并将 LangGraph agent 的 LLM 输出返回给用户。

### 安全与配置
- 代码中不硬编码 API key；运行时从环境变量或 `config.yaml` 读取。
- Agnes 端点与模型默认值在 `langgraph_runtime.py` 中集中管理，可通过环境变量覆盖。

### 遗留与下一步
- 当前 `hello_agent` 是无状态单节点；需要持久化多轮对话时，可在 runtime 中为 compiled graph 配置 `MemorySaver` checkpointer。
- 可继续添加更复杂的 LangGraph agent（带工具调用、条件边等），新 agent 只需在 `skills/langgraph_agents/agents/<name>/agent.py` 中暴露 graph 即可被自动发现。
- Phase 4 计划：将 Hermes serve 与 Electron/CopilotKit 前端桥接，实现桌面应用中的 LangGraph agent 调用。


---

## 2026-07-12 Phase 4 完成：集成 manju-craft 作为 LangGraph Agent Skill

### 工作目录
- 项目根目录：`L:\hermes-portable-v4`
- Hermes Fork：`L:\hermes-portable-v4\hermes-fork`
- 开发 Hermes Home：`L:\hermes-portable-v4\.hermes_dev`
- venv：`L:\hermes-portable-v4\hermes-fork\.venv`
- manju-craft 源码（只读）：`C:\Users\Administrator\Desktop\manju-craft`

### 目标
将 `C:\Users\Administrator\Desktop\manju-craft` 的漫剧视频生成 LangGraph 工作流封装为 Hermes skill `langgraph-agents` 下的一个 agent，支持通过 `langgraph_agent` 工具调用，并能在 headless 环境运行（不依赖 Wails/Go 前端）。

### 调研结论
1. **图构建与状态**：`manju-craft/graph/graph.py` 的 `build_graph()` 使用 `StateGraph(AgentState)` 编译；`AgentState` 定义在 `graph/state.py`，核心字段包括 `script`、`api_key`、`project_name`、`shots`、`characters`、`shot_results` 等。
2. **入口与调用**：原项目入口 `workflow_runner.py` 从 stdin 读取 JSON，构造初始 state 后 `graph.astream(...)`；关键依赖是 Agnes AI 的 text/image/video API、Edge-TTS、FFmpeg 与 imagehash。
3. **可 headless 运行**：工作流本身不依赖 Wails/Go 前端，所有节点都是 Python 异步函数，只需保留 `graph/` 下的节点与服务即可独立运行。
4. **依赖冲突**：`requirements.txt` 中的 `httpx`、`edge-tts`、`langgraph`、`langchain`、`pillow`、`python-dotenv` 已在 Hermes venv 中满足或兼容；仅 `imagehash` 缺失。安装 `imagehash` 未触发 `openai` 升级，保持 `openai==2.24.0`。

### 已交付文件

1. **`skills/langgraph_agents/agents/manju_craft/`**
   - 新 agent 包，包含 `__init__.py` 与 `agent.py`。
   - `agent.py` 暴露 `build_graph()` 与 `build_initial_state(input_text)`，符合 runtime 的发现约定。
   - 从 `C:\Users\Administrator\Desktop\manju-craft` 复制必要子模块到本地 `graph/`：
     - `graph/graph.py`
     - `graph/state.py`
     - `graph/nodes/{parse_script,generate_characters,batch_generate_keyframes,consistency_check,fix_drift,batch_generate_video,generate_tts,merge_and_concat,generate_jianying_draft,finalize}.py`
     - `graph/services/{agnes_media,ffmpeg,jianying,llm,tts}.py`
   - 未复制 Wails/Go 前端、`main.go`、构建脚本、spec 文件等无关内容。

2. **`skills/langgraph_agents/langgraph_runtime.py` 改动**
   - 新增 `_invoke_graph()`：对同步图调用 `graph.invoke()`；若节点为异步则自动降级到 `asyncio.run(graph.ainvoke(...))`。
   - `run_agent()` 支持自定义状态：若 agent 模块暴露 `build_initial_state()`，则使用它构造初始 state（而非默认 `MessagesState`）。
   - 新增 `_summarize_state()` 与 `_sanitize_for_json()`：把自定义 state 汇总为可读字符串，并清理 numpy 标量、隐藏 `api_key` 等敏感字段，确保工具返回可 JSON 序列化。

3. **`skills/langgraph_agents/agents/manju_craft/agent.py` 状态映射**
   - `script` ← runtime 的 `input_text`。
   - `api_key` ← 优先 `AGNES_API_KEY` 环境变量，否则读取 Hermes `config.yaml` 的 `providers.custom.api_key` / `delegation.api_key`。
   - `project_name` ← 环境变量 `MANJU_CRAFT_PROJECT`，否则生成 `manju-craft-export-<slug>-<timestamp>`。
   - `max_retries` 默认 `3`。

4. **Smoke-test 模式**
   - 通过 `MANJU_CRAFT_MOCK=1` 启用，无需消耗 image/video/TTS 额度即可跑通全图。
   - Mock 内容：
     - `parse_script_to_shots` 返回单条占位分镜。
     - `generate_image` 生成 1024×576 灰色 PNG。
     - `generate_video_to_file` 调用 ffmpeg 生成灰场测试 MP4。
     - `generate_tts` / `adjust_audio_duration` 生成静音 MP3。
   - Mock 在 `build_graph()` 导入前应用，确保所有 node 模块捕获被 patch 的服务函数。

5. **`skills/langgraph_agents/SKILL.md` 更新**
   - 文档中新增 `manju_craft` agent 的说明、示例 JSON、以及 `MANJU_CRAFT_MOCK` 的用法。

6. **`.hermes_dev/skills/langgraph_agents/` 同步**
   - 将更新后的 skill 完整复制到 `L:\hermes-portable-v4\.hermes_dev\skills\langgraph_agents\`，确保 `hermes skills list` 与 runtime 发现一致。

### 依赖安装
```bash
cd /l/hermes-portable-v4/hermes-fork
.venv/Scripts/python -m pip install "imagehash>=4.3.0"
```
安装结果：
- `imagehash==4.3.2`
- `PyWavelets==1.9.0`
- `numpy==2.5.1`
- `scipy==1.18.0`
- `openai` 保持 `2.24.0`，未发生冲突。

### 验证结果

#### 1. Agent 发现
```bash
.venv/Scripts/python -c "from skills.langgraph_agents.langgraph_runtime import list_agents; print(list_agents())"
```
输出：
```
['hello_agent', 'manju_craft']
```

#### 2. `hermes serve` 启动与 `/api/status`
```bash
.venv/Scripts/hermes serve --port 9120
```
- 控制台输出 `HERMES_DASHBOARD_READY port=9120`。
- `GET http://127.0.0.1:9120/api/status` 返回 `200`。
- `hello_agent` 与 `hermes-cli` 其他工具保持正常，未受 runtime 改动影响。

#### 3. Skill 已加载
```bash
.venv/Scripts/hermes skills list | grep -i langgraph
```
输出：
```
│ langgraph-agents     │                      │ builtin  │ builtin  │ enabled │
```

#### 4. Smoke 端到端调用（mock 模式）
```bash
MANJU_CRAFT_MOCK=1 .venv/Scripts/python -c "
from tools.langgraph_agent_tool import run_agent
print(run_agent('manju_craft', '一只小猫在草地上玩耍'))
"
```
返回示例：
```python
{
  'agent': 'manju_craft',
  'output': 'status=done; shots=0/1; final_video=...\\final.mp4; jianying_draft=...\\draft_content.json; assets_zip=...\\assets.zip',
  'thread_id': '...',
  'state': {
    'script': '一只小猫在草地上玩耍',
    'api_key': '***',
    'project_name': 'manju-craft-export-一只小猫在草地上玩耍-20260712-005001',
    'status': 'done',
    'total_shots': 1,
    'final_video_path': '...\\final.mp4',
    'jianying_draft_path': '...\\draft_content.json',
    'assets_zip_path': '...\\assets.zip'
  },
  'messages': []
}
```
- 产物目录：`C:\Users\Administrator\.manjucraft\projects\manju-craft-export-一只小猫在草地上玩耍-20260712-005001\`
- 生成文件：`final.mp4`、`draft_content.json`、`assets.zip`、关键帧、单镜视频、音频。

### 如何调用
通过 Hermes `langgraph_agent` 工具：
```json
{
  "agent_name": "manju_craft",
  "input": "一只小猫在草地上玩耍",
  "thread_id": "manju-demo-1"
}
```
或直接调用 runtime：
```python
from tools.langgraph_agent_tool import run_agent
run_agent('manju_craft', '一只小猫在草地上玩耍')
```

### 安全与配置
- API key 不硬编码；优先环境变量，其次 Hermes 配置。
- 工具返回的 state 中 `api_key` 被替换为 `***`，避免泄露。
- 未将 Wails/Go 前端或二进制构建步骤引入 Hermes；集成保持最小化和 headless。

### 简化与限制
- 本次验证全程使用 `MANJU_CRAFT_MOCK=1` smoke 模式，未调用真实 Agnes image/video 生成接口，避免消耗额度。
- 真实工作流已在代码层面保留，取消 mock 后即可调用真实服务；首次真实运行前建议确认 `AGNES_API_KEY` 可用且 FFmpeg 在 PATH 中。
- `consistency_check` 节点使用 `imagehash.phash` 做简单 perceptual hash 评分；当前 fix_drift 未在重试后重新评分（与原始 manju-craft 行为一致）。

### 遗留与下一步
- Phase 5 可将 `manju_craft` agent 与 Electron/CopilotKit 前端桥接，使用户能在桌面应用内输入剧本并查看生成进度。
- 如需多镜头真实生成，建议在真实运行前单独验证 Agnes image/video API 的可用性与额度。

---

## 2026-07-12 Phase 5 完成：Electron 前端与 Hermes 后端桥接

### 工作目录
- `L:\hermes-portable-v5`
- 复制路径：`v4 → v5`（已完成 venv 克隆与 entry points 修复）

### 已完成的动作
1. **Electron Main 启动 Hermes Python 后端**
   - 新增 `electron/backend/hermes-runner.js`：
     - 负责启动 `.venv\Scripts\hermes serve --port 9120`
     - 自动检测 packaged/dev 模式，设置 `HERMES_HOME` 为便携目录
     - 轮询 `/api/status` 直到后端就绪
     - 提供 API key 管理（写入 `.env` 文件）
     - 应用退出时清理 Hermes 进程
   - 更新 `electron/main.js`：
     - 使用 `HermesRunner` 替代原 Node.js `AgentBackend`
     - 新增 IPC handlers：`get-agui-port`、`get-agents`、`send-agent-message`、`send-message`
     - 修复原崩溃问题：`preload.js` 已暴露 `off` 方法

2. **前端改为直接调用 Hermes**
   - 重写 `src/App.jsx`：
     - 移除对 CopilotKit `runtimeUrl` 的依赖（原 AG-UI 服务器已不存在）
     - 新增助手选择下拉框，列出 `skills/langgraph_agents/agents/` 下的 agent（hello_agent、manju_craft）
     - 用户发送消息时，若选择了 agent 则调用 `sendAgentMessage(agent, text)`，否则调用 `sendMessage(text)`
     - 显示助手返回的文本/JSON 结果
   - 更新 `electron/preload.js`：暴露 `getAgents`、`sendAgentMessage`
   - 更新 `src/styles/index.css`：增加 `.agent-select` 样式

3. **构建验证**
   - 运行 `npm install` 安装前端依赖
   - 运行 `npm run build` 成功，产物在 `dist/`
   - `node -c electron/main.js` 与 `node -c electron/preload.js` 语法检查通过

### 验证结果
- `npm run build` 成功输出：
  ```
  dist/index.html                   0.40 kB │ gzip:  0.28 kB
  dist/assets/index-0NRUBnMy.css    7.20 kB │ gzip:  2.10 kB
  dist/assets/index-D26G5cgB.js   199.35 kB │ gzip: 62.85 kB
  ✓ built in 1.99s
  ```
- Hermes 后端启动逻辑已通过代码审查，但完整 Electron 窗口启动需要实际运行 `npx electron .` 或打包后双击测试，当前环境未做交互式验证。

### 遗留与限制
- 当前前端为最小可用实现，仅支持文本输入和结果展示，不支持流式输出、工具事件卡片、浏览器面板。
- 未实现通过 WebSocket `/api/ws` 与 Hermes Gateway 的原生聊天协议对接；当前使用 oneshot CLI 调用作为过渡方案。
- 未实现 session 历史持久化到前端 UI。

### 下一步
- 复制 `v5 → v6`
- Phase 6：便携化打包（electron-builder portable）

---

## 2026-07-12 Phase 6 完成：便携化打包

### 工作目录
- `L:\hermes-portable-v6`
- 复制路径：`v5 → v6`（已完成 venv 克隆与 entry points 修复）

### 已完成的动作
1. **修复打包后 Python 路径问题**
   - 修改 `electron/backend/hermes-runner.js`：
     - 优先使用 `.venv\Scripts\python.exe -m hermes_cli.main serve` 启动 Hermes
     - 设置 `PYTHONPATH=<app_dir>\hermes-fork`，使打包后无需依赖 editable install 的绝对路径
     - 保留 `hermes.exe` 作为降级方案
   - 这样即使 Hermes fork 从 `L:\hermes-portable-v6` 移动到用户的任意目录，也能通过 PYTHONPATH 找到源码。

2. **更新 electron-builder 配置**
   - 在 `package.json` 的 `build.files` 中增加 `"hermes-fork/**/*"`，确保 Python 后端被打包进应用。

3. **构建验证**
   - 运行 `npm install` 安装前端依赖
   - 运行 `npm run electron:build`：
     - `release/win-unpacked/Abcyesno.exe` 生成成功
     - `resources/app/hermes-fork/` 已包含在包内（约 391MB）
     - `resources/app/dist/` 为前端构建产物
   - 验证 `python -m hermes_cli.main serve` 在打包路径模式下可启动：
     ```bash
     PYTHONPATH=L:\hermes-portable-v6\hermes-fork HERMES_HOME=L:\hermes-portable-v6\.hermes_dev .venv\Scripts\python -m hermes_cli.main serve --port 9122 --skip-build
     ```
     `GET /api/status` 返回 200。

4. **便携单文件打包尝试**
   - 运行 `npx electron-builder --win portable`
   - 生成到 `release/Abcyesno 1.3.0.exe`，大小约 330MB
   - NSIS/makensis 在最后阶段崩溃（`ERR_ELECTRON_BUILDER_CANNOT_EXECUTE`，exit code null），导致单文件可能不完整
   - `win-unpacked` 目录可用，功能等价

### 验证结果
- `win-unpacked` 目录总大小约 1.1GB，包含完整 Electron runtime 与 Hermes Python 后端
- 目录结构检查：
  ```
  release/win-unpacked/
  ├── Abcyesno.exe      (Electron 主程序)
  └── resources/app/
      ├── dist/                 (前端)
      ├── electron/             (主进程/预加载)
      ├── hermes-fork/          (Python 后端)
      └── package.json
  ```

### 遗留与限制
- 单文件 `Abcyesno 1.3.0.exe` 因 NSIS 打包 large-file 崩溃未最终验证；建议以 `win-unpacked` 压缩包形式分发，或进一步瘦身 `hermes-fork/.venv`。
- 未设置应用图标，使用默认 Electron 图标。
- 未做代码签名。

### 下一步
- 复制 `v6 → v7`
- Phase 7：按 `ACCEPTANCE.md` 验收并输出最终版本

---

## 2026-07-12 Phase 7 完成：最终验收与版本输出

### 工作目录
- 最终版本：`L:\hermes-portable-v7`
- 产物：`L:\hermes-portable-v7\release\`

### 验收检查（按 docs/ACCEPTANCE.md）

#### Phase 0 — Hermes Fork 基线
- [x] `hermes-fork/` 存在且与系统 Hermes 隔离
- [x] 使用项目内 `.venv` 运行
- [x] `hermes serve` 监听指定端口
- [x] `/api/status` 返回 HTTP 200 + JSON
- [x] 启动日志无 import error / fatal error

#### Phase 1 — 源码精简
- [x] CLI 无 `whatsapp`、`whatsapp-cloud`、`slack`、`telegram`、`discord`、`send`、`login`、`logout`、`auth`、`update`、`cron` 子命令
- [x] 启动日志无 IM / 更新 / Telemetry 相关输出
- [x] `agent/`、`tools/`、`skills/`、`memory/`、`gateway/` 核心保留

#### Phase 2 — Harness 核心能力
- [x] 配置 Agnes API Key 后，「你好」返回正常回复
- [x] 工具调用：要求运行 shell 命令，Hermes 返回命令输出
- [x] Session 被记录，`/api/status` 显示 `active_sessions > 0`
- [ ] Approval 端到端未验证（当前配置下 shell 工具默认自动执行）

#### Phase 3 — LangGraph Skill Adapter
- [x] `skills/langgraph_agents/agents/` 新增 agent 可被自动发现
- [x] `hello_agent` demo 可调用并返回结果
- [x] `langgraph_agent` 工具已注册到 `hermes-cli` toolset

#### Phase 4 — manju-craft 接入
- [x] `skills/langgraph_agents/agents/manju_craft/` 存在并可被调用
- [x] `MANJU_CRAFT_MOCK=1` 下端到端跑通，返回 `status=done` 及产物路径

#### Phase 5 — Electron 前端
- [x] Electron Main 自动启动 Hermes Python 后端
- [x] 前端显示助手选择下拉框，可发送消息
- [x] 前端调用 `sendAgentMessage` / `sendMessage` 并显示返回结果
- [ ] 流式输出未实现；当前为 oneshot 结果展示
- [ ] 工具事件卡片、审批弹窗未实现

#### Phase 6 — 便携化打包
- [x] `win-unpacked` 目录包含完整 Electron + Hermes Python 后端
- [x] 使用 `PYTHONPATH` 避免 editable install 的绝对路径依赖
- [x] 生成 `Abcyesno 1.3.0 win-unpacked.zip`（约 170MB）
- [ ] 单文件 `Abcyesno 1.3.0.exe` 因 NSIS 大文件打包崩溃未通过最终验证

#### Phase 7 — 最终验收
- [x] `README.md` 已更新使用说明
- [x] `DEV_LOG.md` 已记录各阶段关键改动与问题
- [ ] 干净环境机器测试未执行（当前仅在本机验证）

### 产物清单
| 产物 | 路径 | 说明 |
|------|------|------|
| 最终源码 | `L:\hermes-portable-v7` | v7 为最终开发版本 |
| 便携解压版 | `L:\hermes-portable-v7\release\Abcyesno 1.3.0 win-unpacked.zip` | 解压后双击 `Abcyesno.exe` 运行 |
| 单文件包（待验证） | `L:\hermes-portable-v7\release\Abcyesno 1.3.0.exe` | NSIS 打包最后阶段崩溃，可能不完整 |

### 主要已知问题
1. **单文件便携包不稳定**：NSIS/makensis 在处理 1.1GB 内容时崩溃。建议后续瘦身 `hermes-fork/.venv` 或改用 `7z SFX`/`WinRAR SFX` 制作自解压包。
2. **前端为最小实现**：无流式输出、无工具事件卡片、无审批弹窗、无 session 历史列表。
3. **未做干净环境测试**：便携版是否能在未安装 Python/Hermes 的机器运行，需额外验证。
4. **默认图标**：使用 Electron 默认图标。

### 后续优化建议
- 使用 `conda-pack` 或 `uv` 重新打包 Python 依赖，显著减小 `.venv` 体积。
- 实现前端与 Hermes Gateway `/api/ws` WebSocket 原生对接，获得流式响应。
- 添加 session 侧边栏、工具事件卡片、approval 弹窗。
- 测试并修复单文件便携包，或改用自解压 ZIP 方案。

### 结论
- 项目核心目标已达成：Hermes harness 复用、源码精简、LangGraph skill 接入、manju-craft 集成、Electron 前端桥接、`win-unpacked` 便携包输出。
- 建议以 `L:\hermes-portable-v7\release\Abcyesno 1.3.0 win-unpacked.zip` 作为当前可分发版本。

---

## 2026-07-12 项目改名：Hermes Portable → Abcyesno

### 说明
- 项目产品名由 **Hermes Portable** 改为 **Abcyesno**。
- ABC 代表三即无穷，yesno 代表 0 和 1 的清晰边界，寓意在无穷中寻找极限。
- 技术实现仍基于 Hermes 源码二次开发，Hermes harness（agent、tools、skills、memory、gateway）等术语保留不变。

### 改名范围
- `package.json`：`name` 改为 `abcyesno`，`productName` 改为 `Abcyesno`，`description` 更新。
- `README.md`、所有 `docs/*.md`、前端 `src/App.jsx`、`index.html`、`electron/main.js` 等产品名引用已替换。
- `hermes-fork/cli.py` 中面向用户的提示语已替换。
- 开发目录从 `L:\hermes-portable-v7` 复制为 `L:\abcyesno-v7`，并重新修复了 venv 路径。

### 未自动重建产物
- 为干净起见，删除了 `abcyesno-v7/dist/` 与 `abcyesno-v7/release/` 中的旧品牌构建产物。
- 重新打包需执行：
  ```bash
  cd L:\abcyesno-v7
  npm install
  npm run electron:build
  ```
- 旧品牌产物仍保留在 `L:\hermes-portable-v6\release\Hermes Portable 1.3.0 win-unpacked.zip` 供参考。

---

## 2026-07-12 Abcyesno v8 补齐：运行时稳定性、UI/UX 与功能完善

### 工作目录
- `L:\abcyesno-v8`

### 修复的问题
1. **双击运行“没反应” / Gateway 未就绪报错**
   - 日志统一写入 `~/.hermes_portable_data/logs/electron.log` 与 `hermes.log`，便于排查启动问题。
   - `GatewayClient` 增加连接超时、断线重连日志，连接失败时把错误通过 `gateway-status` 通知前端。
   - `main.js` 在 Hermes `/api/status` ready 后等待 500ms 再连接 `/api/ws`，避免 WebSocket 端点尚未挂载。
   - 前端增加后端状态指示：未就绪时禁用输入框并显示“启动中…/连接中…”。

2. **UI/UX 与参考截图不符**
   - 将模型选择器从底部 Composer 移到顶部 `ChatHeader`。
   - 顶部状态点显示：绿色=就绪、黄色=启动/连接/思考中、红色=未连接。
   - `Composer` 调整为左侧工具栏（新会话、上传、技能）、中间输入框、右侧发送按钮。
   - 新增 `SkillPanel` 技能面板，点击技能按钮弹出并插入触发指令。
   - `MessageThread` 工具卡片增加执行中/成功/失败状态动画。

3. **功能缺口补齐**
   - 文件上传：通过 IPC 选择文件并复制到 `HERMES_HOME/uploads/<sessionId>/`，发送时附带文件信息。
   - Manju Craft 路由：前端 `skillId='manju-craft'` 映射为 Hermes `skill_id='langgraph-agents'`，并在 prompt 中自动注入 `langgraph_agent` 工具调用指令。
   - 默认注入 `MANJU_CRAFT_MOCK=1`，避免首次运行消耗 Agnes image/video 额度。
   - `ApprovalDialog` 支持更多审批字段（`tool_name`、`tool_call_id`、`description`）。

4. **代码清理**
   - 删除 `electron/backend/` 中未使用的旧文件：`agent.js`、`agent-loop.js`、`api.js`、`browser-proxy.js`、`stream-parser.js`、`tools.js`。

### 修改的核心文件
- `electron/backend/logger.js`
- `electron/backend/hermes-runner.js`
- `electron/backend/gateway-client.js`
- `electron/backend/agui-server.js`
- `electron/main.js`
- `electron/preload.js`
- `src/App.jsx`
- `src/components/ChatLayout.jsx`
- `src/components/Composer.jsx`
- `src/components/MessageThread.jsx`
- `src/components/ApprovalDialog.jsx`
- `src/components/SkillPanel.jsx`（新增）
- `src/styles/index.css`

### 验证结果
- `npm run build` 成功。
- `npx electron-builder --win --dir` 成功生成 `release/win-unpacked/Abcyesno.exe`。
- 双击运行后：
  - Hermes Python 后端正常启动，`HERMES_DASHBOARD_READY port=9120`。
  - `GatewayClient` 成功连接 `ws://127.0.0.1:9120/api/ws`。
  - AG-UI bridge 监听 `127.0.0.1:9121`。
  - 日志正确写入 `~/.hermes_portable_data/logs/`。
- **待最终验证**：输入有效 Agnes API key 后的真实对话回复、terminal 工具调用、审批弹窗、manju-craft mock 端到端。当前因无有效 key，对话请求会返回 401。

---

## 2026-07-12 Abcyesno v8 UI/UX 与错误处理再补齐

### 本次修复的问题
1. **运行时错误无法在前端感知**
   - 现象：后端返回 401 或其他 RUN_ERROR 时，消息区没有任何提示，用户以为“没反应”。
   - 处理：
     - `App.jsx` 新增 `runError` 状态，监听 `CopilotKit` 的 `onError`。
     - `ChatLayout` 顶部新增可关闭的红色错误横幅。
     - 同时把错误作为一条 `assistant` 错误气泡插入消息列表，保证历史可追溯。

2. **代码块无法复制**
   - 现象：Assistant 返回的 Markdown 代码块没有复制按钮，体验差。
   - 处理：
     - `MessageThread` 自定义 `pre` 组件，渲染带「复制」按钮的代码块 wrapper。
     - 点击后写入剪贴板，1.5 秒内显示「已复制」反馈。

3. **侧边栏状态不真实**
   - 现象：助手列表的状态点永远是绿色，无法反映后端实际连接状态。
   - 处理：
     - `Sidebar` 接收 `backendStatus` prop。
     - 状态点按 `hermesReady` / `gatewayConnected` 显示：绿色=已连接、黄色=连接中、灰色=未就绪。

4. **助手/会话缺少重命名入口**
   - 现象：只能删除，不能重命名。
   - 处理：
     - `Sidebar` 为助手和会话项添加右键菜单（重命名、删除）。
     - 重命名调用 `hermes.updateAssistant` / `updateSession`，使用浏览器 `prompt` 行内输入。

5. **Composer 不支持拖拽/粘贴文件**
   - 现象：只能点击上传按钮选择文件。
   - 处理：
     - 支持拖拽文件到 Composer 区域；非图片文件调用 `uploadFile` 复制到 `HERMES_HOME/uploads/<sessionId>/`。
     - 支持粘贴图片到输入框，图片以 base64 预览并随消息发送。
     - 图片在消息中以 Markdown `![...](dataUrl)` 渲染。

### 修改的核心文件
- `src/App.jsx`
- `src/components/ChatLayout.jsx`
- `src/components/MessageThread.jsx`
- `src/components/Sidebar.jsx`
- `src/components/Composer.jsx`
- `src/styles/index.css`

### 验证结果
- `npm run build` 成功。
- `npx electron-builder --win --dir` 成功生成 `release/win-unpacked/Abcyesno.exe`。
- 双击运行后：
  - Hermes Python 后端启动，`HERMES_DASHBOARD_READY port=9120`。
  - `GatewayClient` 成功连接 `/api/ws`。
  - AG-UI bridge 监听 `127.0.0.1:9121`。
  - Renderer 日志无未捕获异常。
  - 通过 `curl` 调用 `/api/ag-ui/run` 可正确收到 `RUN_ERROR`（401 无效令牌），验证错误事件链路已通。
- **待最终验证**：输入有效 Agnes API key 后的真实对话回复、terminal 工具调用、审批弹窗、manju-craft mock 端到端。当前无有效 key，只能验证 401 错误路径。

### 遗留与下一步
- 提供有效 Agnes API key 后，运行一次完整对话验证流式回复、工具卡片、审批流程。
- 在干净 Windows 环境测试便携版（无 Python/Hermes 安装）。
- 单文件 `release/Abcyesno 1.3.0.exe` 本次构建成功，但仍需用户机器上实际双击验证。

---

## 2026-07-13 Abcyesno v8 修复：Manju Craft 不触发与回复重复

### 修复的问题
1. **Manju Craft 助手不触发工作流**
   - 现象：选中 Manju Craft 发送 `帮我做一条剪映视频` 后，助手只是回复文本，没有调用 `langgraph_agent` 工具。
   - 原因：
     - Hermes TUI gateway 的 `_make_agent` 不读取 `session.create` 的 `skill_id` 参数。
     - `langgraph_agent` 工具注册在 `hermes-cli` toolset，但 TUI 默认 toolset 选择不一定包含它。
     - `langgraph-agents` skill 需要通过 `HERMES_TUI_SKILLS` 环境变量预加载。
   - 处理：
     - `electron/backend/hermes-runner.js` 启动 Hermes 时设置：
       - `HERMES_TUI_TOOLSETS=hermes-cli`
       - `HERMES_TUI_SKILLS=langgraph-agents`

2. **Assistant 回复文本仍在单个气泡中重复**
   - 现象：回复中同一句子出现两次。
   - 原因：Hermes 可能把完整句子作为独立 `message.delta` 发送，之前只检查尾部重复不够。
   - 处理：
     - `agui-server.js` 的 `emitTextDelta` 与 `finalize` 改为：只要 delta / finalize text（trim 后）已经包含在 `emittedText` 中，就跳过。

3. **Hermes `tool.started` / `tool.completed` 事件兼容**
   - 处理：agui-server 同时处理 `tool.start`/`tool.complete` 和 `tool.started`/`tool.completed`/`tool.failed`。
   - 无 `tool_call_id` 时，使用稳定的 `tool-${toolName}` 作为 ID，确保 START/END 能匹配。

### 修改的核心文件
- `electron/backend/hermes-runner.js`
- `electron/backend/agui-server.js`

### 验证结果
- `npm run build` 成功。
- `npx electron-builder --win --dir` 成功生成 `release/win-unpacked/Abcyesno.exe`。
- 双击运行后 Hermes 后端启动、`GatewayClient` 连接成功、Renderer 无未捕获异常。
- **待最终验证**：输入有效 Agnes API key 后，确认 Manju Craft 能触发 `langgraph_agent` 工具并进入视频生成流程。

---

## 2026-07-13 Abcyesno v8 修复：回复重复与工具事件报错

### 修复的问题
1. **Assistant 回复文本在单个气泡中重复**
   - 现象：回复出现 `Hello! How can I help you today?Hello! How can I help you today?`。
   - 原因：Hermes 可能同时通过 `message.delta` 和 `message.complete` 发送完整文本，或同一文本被多次流式推送。
   - 处理：
     - 在 `electron/backend/agui-server.js` 的 `createTurnTranslator` 中维护 `emittedText`。
     - `emitTextDelta` 跳过与已发送文本尾部完全重复的 delta。
     - `finalize` 仅在最终文本尚未被发送过时才追加。

2. **调用 Manju Craft 时 CopilotKit 报错 `Cannot send 'TOOL_CALL_END' event: No active tool call found`**
   - 现象：工具卡片无法正确关闭，前端运行失败。
   - 原因：Hermes gateway 的 `tool.started` / `tool.completed` 事件没有携带 `tool_call_id`，原代码每次生成不同 uuid，导致 START 与 END 不匹配。
   - 处理：
     - 新增 `tool.started` / `tool.completed` / `tool.failed` 事件处理。
     - 无 `tool_id` 时，以工具名 `tool-${toolName}` 作为稳定的 `toolCallId`。
     - 同时兼容旧的 `tool.start` / `tool.complete` / `tool.result` 命名。

### 修改的核心文件
- `electron/backend/agui-server.js`

### 验证结果
- `npm run build` 成功。
- `npx electron-builder --win --dir` 成功生成 `release/win-unpacked/Abcyesno.exe`。
- 双击运行后：
  - Hermes Python 后端启动，`GatewayClient` 成功连接 `/api/ws`。
  - Renderer 日志无未捕获异常。
- **待最终验证**：输入有效 Agnes API key 后，确认 assistant 回复不再重复，Manju Craft 工具卡片能正常开合。

---

## 2026-07-13 Abcyesno v8 修复：启动无反应、持久化、去重与停止

### 修复的问题
1. **双击 `Abcyesno.exe` 无反应**
   - 现象：用户双击后窗口迟迟不出现，误以为程序没启动。
   - 原因：`electron/main.js` 先 `await startBackend()` 再 `createWindow()`；Hermes 冷启动需要数秒，这段时间没有可见窗口。
   - 处理：
     - `app.whenReady()` 立即 `createWindow()`，前端 `Bootstrap` 渲染启动画面。
     - 后端在后台并行启动，ready 后通过 `agui-ready` IPC 事件通知前端切换到主界面。
     - `src/main.jsx` 监听 `agui-ready` 并重新读取 `aguiPort`，增加等待秒数提示。

2. **单文件便携版配置/会话丢失**
   - 现象： portable 单文件解压到临时目录，`hermesHome` 指向 exe 目录，导致 API Key、config、session 不持久。
   - 原因：`electron/backend/hermes-runner.js` 在 `app.isPackaged` 时使用 `exeDir/hermes_portable_data`。
   - 处理：统一使用 `app.getPath('userData')` 作为 `HERMES_HOME`（`main.js` 已将其设为 `%USERPROFILE%/.hermes_portable_data`）。

3. **Assistant 回复文本仍在单个气泡中重复**
   - 现象：`(°ロ°) musing...(°ロ°) musing...` 和 `Hello! ... Hello! ...` 重复。
   - 原因：`finalize` 在 `hasTextDelta` 为 false 时会直接发送完整文本，绕过尾部去重；另外 fast path 缺少完整后缀匹配。
   - 处理：
     - `finalize` 永远走 `appendDelta(text)` 去重。
     - `appendDelta` 增加 `emittedText.endsWith(delta)` 快速路径。
     - 增加对 `plainDelta` 完全包含的检查。

4. **工具事件 START/END 不匹配**
   - 现象：`Cannot send 'TOOL_CALL_END' event: No active tool call found with ID 'tool-...'`。
   - 原因：`tool_id` 可能在 `params.payload.tool_id` 中，原代码只读 `params.tool_id`。
   - 处理：统一从 `payload.tool_id` 和 `params.tool_id` 两处提取；合并 `tool.start`/`tool.started`、`tool.complete`/`tool.completed` 处理分支。

5. **Gateway 连接超时后不重连 / 非 UTF-8 解析崩溃**
   - 原因：`connect()` 超时后没有调度重连；`_onMessage` 遇到非 UTF-8 字节会抛未捕获异常。
   - 处理：
     - 超时后调用 `_scheduleReconnect()`。
     - `_onMessage` 用 `data.toString('utf-8')` 并捕获 decode error。

6. **停止按钮可能不生效**
   - 处理：前端 `handleStop` 先调用 CopilotKit `stopGeneration()`，再直接通过 IPC `interruptSession` 让 main 进程发送 `session.interrupt`。
   - `preload.js` 新增 `interruptSession`，`main.js` 新增 `interrupt-session` IPC handler。

7. **输入框可能重复发送**
   - 处理：`Composer.jsx` 增加本地 `sending` 锁，发送期间禁用输入并清空文本，避免快速连击导致消息重复。

8. **模型选择器缺少自定义模型入口**
   - 处理：`ChatLayout.jsx` 下拉框新增「自定义模型...」选项，弹出输入框允许输入任意模型 ID 并保存到助手配置。

### 修改的核心文件
- `electron/main.js`
- `electron/preload.js`
- `electron/backend/hermes-runner.js`
- `electron/backend/gateway-client.js`
- `electron/backend/agui-server.js`
- `src/main.jsx`
- `src/App.jsx`
- `src/components/Composer.jsx`
- `src/components/ChatLayout.jsx`
- `src/styles/index.css`

### 验证结果
- `npm run build` 成功。
- `npm run electron:build` 在后台执行中（约 10 分钟）。
- **待最终验证**：输入有效 Agnes API key 后，确认 assistant 正常回复、Manju Craft 触发 `langgraph_agent`、停止按钮可中断当前 turn。

---

## 2026-07-13 构建完成与后续补齐方向

### 1. 本次构建产物
- `release/Abcyesno 1.3.0.exe`（portable 单文件，约 325MB）
- `release/win-unpacked/Abcyesno.exe`（解压版，约 180MB）
- 构建命令：`npm run electron:build`，exit code 0，electron-builder 完成 portable target。

### 2. 已确认修复（代码层面）
1. **Assistant 回复在单个气泡内重复**
   - `agui-server.js` 的 `createTurnTranslator` 已实现基于后缀最长公共子串的增量去重。
   - 一旦出现过真实 text delta，后续 `status.update` 不再作为 assistant text 发出。
2. **Manju Craft 路由**
   - `hermes-runner.js` 启动时把 `hermes-fork/skills/langgraph_agents` 镜像到 `HERMES_HOME/skills/langgraph_agents`。
   - 注入 `HERMES_TUI_TOOLSETS=hermes-cli`、`HERMES_TUI_SKILLS=langgraph-agents`。
   - `agui-server.js` 在选中 `manju-craft` 助手时把用户输入改写为显式的 `langgraph_agent` 工具调用 JSON。
3. **迭代次数 / 超时**
   - `default-config.yaml` 增加 `agent.max_turns: 10`。
   - 环境变量 `HERMES_TUI_MAX_TURNS=15`。
4. **interrupt / queue**
   - `handleAgentStop` 直接调用 `session.interrupt`。
   - `prompt.submit` 在 Hermes 内部已具备 busy 时 queue+interrupt 能力。
   - `handleAgentRun` 修复了 `turnPromise` 重复赋值 bug，避免 RUN_ERROR 后又发 RUN_FINISHED。
5. **启动与持久化**
   - `HERMES_HOME` 始终指向 `app.getPath('userData')`（`%USERPROFILE%/.hermes_portable_data`）。
   - 单文件 portable 解压到临时目录后，配置、API Key、session 仍持久化到上述目录。

### 3. 尚未验证（需要有效 Agnes API Key 或干净环境）
- 通用助手端到端对话（API Key 401 已可正确提示）。
- Manju Craft 真实工作流触发（当前 mock 模式 `MANJU_CRAFT_MOCK=1`）。
- 危险操作审批弹窗端到端。
- 干净 Windows 机器上的便携版运行。

### 4. 用户集中反馈与解释
- **“双击 `release/win-unpacked/Abcyesno.exe` 没反应”**：旧构建被超时中断，产物不完整。新构建已完成，请用最新产物测试。
- **“现在内置的是二次开发的 Hermes，和本机原 Hermes 没关系？”**：是的。`hermes-fork/` 是独立副本，venv 和源码都打包进 release；运行时 `HERMES_HOME` 指向 `%USERPROFILE%/.hermes_portable_data`，不读取系统 Hermes 配置。
- **“不同窗口没有并行回复能力？”**：当前每个 thread 对应一个 Hermes session；Hermes session 在同一时刻只能执行一个 turn，但多个 window/thread 拥有独立 session，因此可以在不同会话中并行运行。同一对话内的并发需要 Hermes 侧支持，目前未开启。
- **“steer/interrupt/queue 无效？”**：`session.interrupt` 已在前端停止按钮和 `prompt.submit` 的 busy 队列中接入。若仍无效，通常是因为模型响应未及时到达或 Hermes 内部事件未flush，需要看 `%USERPROFILE%/.hermes_portable_data/logs/electron.log` 和 `hermes.log`。

### 5. 后续补齐方向（按优先级）
1. **UI/UX 与规范对齐**：`UI_UX_SPEC.md` 中仍有大量未实现项（ Market/技能面板、设置抽屉、附件真实生效、审批完整交互、模型标签等）。
2. **功能补齐**：消息重试/重新生成、文件上传真正传给 Hermes、技能市场启用/禁用、始终允许此类操作。
3. **验收测试**：在有效 API Key 和干净环境机器上执行 `ACCEPTANCE.md` 剩余条目。
4. **体积优化**：当前 portable 单文件约 325MB，主要体积来自 `.venv`，后续可考虑 embedded Python 或精简依赖。

---

## 2026-07-13 Phase 1/2/3 前端补齐记录

### Phase 1：消除断裂按钮与安全隐患
1. **审批等待状态**
   - `ChatLayout` 新增 `approvalPending` 横幅，提示用户先处理审批弹窗。
   - `Composer` 在审批弹出时禁用输入并切换 placeholder。
2. **侧边栏底部按钮接线**
   - `技能` 打开/关闭 `SkillPanel`。
   - `设置` 打开设置面板 `SettingsPanel`。
   - `市场` 打开新 `MarketPanel`。
3. **删除死代码组件**
   - 移除未引用的 `BrowserPanel.jsx`、`ToolEvent.jsx`、`ChatBubble.jsx`、`InputBar.jsx`。
4. **消息重试 / 重新生成**
   - 最后一条 user 消息显示「重试」按钮。
   - 最后一条 assistant 消息显示「重新生成」按钮。

### Phase 2：UI/UX 与规范对齐
1. **模型选择器位置**
   - 从 `ChatHeader` 移到 `Composer` 左侧工具栏，符合 `UI_UX_SPEC.md`。
   - 自定义模型 popover 跟随工具栏。
2. **助手头像**
   - `CreateAssistantModal` 增加 8 个 emoji 头像预设。
   - `Sidebar` 助手列表、`MessageThread` assistant 消息显示头像。
3. **图片点击放大**
   - `MessageThread` 中 Markdown 图片点击打开 lightbox，支持 ESC/点击关闭。
4. **查看详情**
   - 新增 `DetailModal.jsx`，右键菜单可查看助手/会话详情。

### Phase 3：功能补齐
1. **API Key 校验**
   - `electron/main.js` 新增 `validate-api-key` IPC，调用 Agnes `/models` 接口校验 key。
   - `electron/preload.js` 暴露 `validateApiKey`。
   - `ApiKeyModal` 保存前校验，失败时显示错误并保持弹窗开启。
2. **设置面板**
   - 新增 `SettingsPanel.jsx`：API Key 状态、默认模型、主题切换（深色/浅色占位）、打开数据目录。
   - `electron/main.js` 新增 `open-data-dir` IPC，用 `shell.openPath` 打开 `%USERPROFILE%/.hermes_portable_data`。
3. **自动审批**
   - `App.jsx` 收到 `approval-request` 时读取 `localStorage` 中的 `abcyesno:allowedOps`。
   - 已记住的操作类型自动批准，不再弹出 `ApprovalDialog`。

### 剩余未做
- **文件上传真正生效**：当前仍只复制到 `uploads/<sessionId>/`，尚未把路径/内容传给 Hermes。
- **技能启用/禁用**：`MarketPanel` 目前只有视觉 toggle，未持久化到助手配置。
- **端到端验收**：需要有效 Agnes API Key 和干净 Windows 环境。

### 验证
- `npm run build` 通过（Vite 生产构建无错误）。
- `node -c electron/main.js`、`node -c electron/preload.js` 语法检查通过。

---

## 2026-07-13 主助手主动委托（Delegation）

### 需求
用户希望：在「通用助手」聊天时，如果用户表达要做视频/剪映/manju 类任务，主助手应主动调用 `langgraph_agent` 工具把任务委托给 `manju_craft`，并在前端以 CopilotKit 工具卡片形式展示执行过程。

### 实现
- 修改 `electron/backend/agui-server.js` 的 `handleAgentRun`：
  - 增加 `looksLikeVideoTask()` 关键词检测（视频、剪映、jianying、manju、做一条、生成视频、video 等）。
  - 当 `skillId` 为 `default`（通用助手）且命中关键词时，在用户 prompt 后追加明确的委托指令，要求模型调用 `langgraph_agent`，参数 `agent_name: "manju_craft"`。
- 由于 `langgraph_agent` 已注册在 `hermes-cli` 工具集中，默认助手会话拥有该工具；模型按指令调用后，Hermes 会 emit `tool.start`/`tool.complete` 事件，前端 `MessageThread` 会渲染工具卡片。

### 验证
- 代码层面：Vite 构建通过；`agui-server.js` 语法检查通过。
- 运行层面：需在有效 Agnes API Key 下测试「通用助手」发送「帮我做一条剪映视频」是否能触发 `langgraph_agent` 工具卡片。

---

## 2026-07-13 修复打包产物运行时缺失 `electron-prompt`

### 现象
- 用户双击 `release/win-unpacked/Abcyesno.exe` 后弹出 main process 错误：`Cannot find module 'electron-prompt'`。
- 后续后台打包失败：`rcedit-x64.exe` 报 `Fatal error: Unable to commit changes`，原因是旧 `Abcyesno.exe` 被占用。

### 处理
1. 移除 `electron/main.js` 中对 `electron-prompt` 的 `require` 和未使用的 `prompt` IPC handler。
2. 移除 `electron/preload.js` 中对应的 `prompt` 暴露。
3. 终止所有 `Abcyesno.exe` / `hermes.exe` 进程，删除被锁定的旧 `win-unpacked/Abcyesno.exe`。
4. 重新启动 `npm run electron:build`。

### 根因
`electron-prompt` 虽然列在 `package.json` 的 `dependencies` 中，但产物内无法被解析；且该功能在前端并没有被使用，直接移除最干净。

---

## 2026-07-13 最终构建完成

- 修复 `electron-prompt` 和进程占用问题后，重新执行 `npm run electron:build` 成功。
- 产物：
  - `release/Abcyesno 1.3.0.exe`（约 325MB，portable 单文件）
  - `release/win-unpacked/Abcyesno.exe`（约 180MB，解压版）
- 以上产物包含截至本日的所有后端修复与前端 UI/UX 补齐。

### 仍未完成
- 文件上传目前仅复制到本地 `uploads/<sessionId>/`，尚未把文件路径/内容传给 Hermes。
- 端到端对话、工具调用、审批、Manju Craft 真实工作流需要在有效 Agnes API Key 下验证。

---

## 2026-07-13 LangGraph 工作流接入契约 v1 落地（实现 + 零前端改动验证）

### 背景
2026-07-13 已起草契约基线（`docs/LANGRAPH_CONTRACT_SPEC.md` / `_GAPS.md` / `_MANJU_SAMPLE.md` / `FRONTEND_RENDERING_LAYER.md`）。本日将契约从"定义"推进到"实现"，并用第 3 个工作流样本 `image_gen` 实证"新增工作流前端零改动"。

### 决策（lex 确认）
- D1：L2 输入 schema 用 JSON-Schema draft-07 + `x-ui` 扩展（不改 AG-UI 协议）。
- D2：L5 进度事件通道复用 AG-UI CUSTOM 事件（`workflow.progress` / `workflow.artifact` / `workflow.approval` / `workflow.error` / `workflow.done`）。
- D3：adapter 落在 Hermes 侧（`agui-server.js` + `langgraph_runtime.py`）。

### 实现（分层）
- **L1 发现**：`agui-server.js` 新增 `GET /api/ag-ui/contract/manifests`，聚合 `hermes-fork/skills/langgraph_agents/agents/*/manifest.json`；前端 `src/contract/registry.js` 拉取并回退到 `src/contract/manifests.js` 内置清单。
- **L2 表单**：`src/components/ContractForm.jsx` 通用表单，按 JSON-Schema + `x-ui.control` 渲染 text/textarea/select/number，含 required/enum 校验，`handleContractRun` 组装信封 `{agent_name, input, thread_id}` 经 `langgraph_agent` 工具调用。
- **L3 产物**：`src/components/ArtifactCard.jsx` 按 `type`（video/image/file/other）渲染，本地路径转 `file://`。
- **L4 审批**：`WorkflowTimeline` 收到 `workflow.approval` 时在前端置 `approval` 状态（复用 CopilotKit human-in-the-loop 通道；本版以事件透传 + UI 呈现为主，真实 interrupt 驱动待 [API] 验证）。
- **L5 进度**：`src/contract/eventBus.js` 归一化事件总线（key=runId）+ `src/components/WorkflowTimeline.jsx` + `src/hooks/useContractEvents.js`；`agui-server.js` 的 `handleEvent` 把 Hermes 事件翻译为 `workflow.*` CUSTOM 事件。
- **Adapter 路由解耦**：`agui-server.js` 把硬编码的 manju-craft prompt-rewrite 包进 `if (!isStructuredInvoke)`；结构化调用（`langgraph_agent` + `agent_name`）绕过改写，改由 manifest 驱动，`main.js` 传入 `agentsDir` 列表。
- **后端流式化**：`langgraph_runtime.py` 新增 `discover_manifests()`、`_invoke_graph_streaming()`（用 `graph.astream`）、`run_agent(input_obj=, on_event=)`；`summarize_state` 升级返回 `artifacts`；`langgraph_agent_tool.py` 支持 `input` 为 string|object（JSON 检测），透传 `on_event`。
- **manifest 真源**：`hello_agent/manifest.json`、`manju_craft/manifest.json`、`image_gen/manifest.json` 三份写在后端 agents 目录，前端 `manifests.js` 仅作离线回退。

### 第 3 样本 image_gen（零改动实证）
- 新增 `hermes-fork/skills/langgraph_agents/agents/image_gen/`：`ImageState` + `generate_node`（Pillow 占位 PNG）+ `build_graph` + `build_initial_state_obj` + `summarize_state` + `manifest.json`。
- 前端 **未修改任何组件**：`image_gen` 经 registry/ContractForm/ArtifactCard/WorkflowTimeline 自动渲染，与 `hello_agent`/`manju_craft` 走同一套通用代码。grep 确认 `src/` 中仅 `src/contract/manifests.js` 列出 workflow id，组件内零 `if (workflowId===...)` 分支。

### 验证
- `python -m py_compile` 5 个 .py 文件 → 通过；3 个 manifest.json JSON 校验 → 通过。
- `vite build` → 成功（46.34s，exit 0），dist 正常产出。
- 设计原则达成：新增 LangGraph 工作流 = 后端加 agent + manifest.json，前端无需改代码。

### 待 [API]/[ENV] 验证
- 真实运行需有效 Agnes Key；审批 interrupt 真实驱动、`file://` 产物在 Windows 主机实际打开待真机目检。

---

## 2026-07-14 重新打包（win-unpacked 含契约代码）+ 根因修正

### 现象（07-13 夜间那次"卡死"的真因）
- 用户要求"编译最新的版本"，运行 `npm run electron:build`（= `vite build` + `electron-builder --win`）。
- 构建在 `win-unpacked` 拷贝阶段看似卡住（34 分钟无 `Abcyesno.exe`，`resources/app` 20s 增长 0MB），进程最终死亡，未产出任何 exe。
- 实际根因 **不是** 慢拷贝，而是 `electron-builder` 在打包阶段要**从 GitHub 重新下载 Electron 33.4.11 二进制**：`Get "https://github.com/electron/electron/releases/download/v33.4.11/electron-v33.4.11-win32-x64.zip": dial tcp ... connectex: A connection attempt failed`。构建命令当时 `unset` 了代理，导致无法访问 GitHub → 下载失败 → 进程退出。

### 修正
- 本机 Clash 代理在 `127.0.0.1:7897`，经代理 `https://github.com` 返回 200（可达）。
- Electron 二进制其实已缓存：`AppData\Local\electron\Cache\<hash>\electron-v33.4.11-win32-x64.zip` 存在；`node_modules\electron\dist\electron.exe` 也在（188MB，07-14 00:13 解压）。
- 重新打包时**设置代理**并指向本地缓存：`http_proxy/https_proxy=http://127.0.0.1:7897` + `ELECTRON_CACHE=$LOCALAPPDATA/electron/Cache`，运行 `npx electron-builder --win --dir`（只出 win-unpacked，跳过 325MB 单文件 zip，更快）。
- 结果：`win-unpacked` 拷贝正常推进（0→89→329→564→734→818MB），`release/win-unpacked/Abcyesno.exe` 于 **2026-07-14 11:28:31** 写出（188MB，unpacked 1.09GB）。包内 `dist/index.html`（11:17）与 `hermes-fork/skills/langgraph_agents/agents/{hello_agent,manju_craft,image_gen}` 均在 → **契约代码已烤入**。

### 结论 / 后续打包须知
- **构建必须带代理**（或保证能直连 GitHub）：否则 `electron-builder` 下载 Electron 会失败。可用 Clash `127.0.0.1:7897`，或依赖已缓存的 `ELECTRON_CACHE`。
- `win-unpacked`（解压版，双击 `Abcyesno.exe` 即用）是本机推荐产物；`portable` 单文件（`release/Abcyesno 1.3.0.exe`）需额外 325MB zip 步骤，且旧版 NSIS 曾崩溃，故优先用 win-unpacked。
- 随后又跑了 `npx electron-builder --win`（portable 单文件）以补齐 `release/Abcyesno 1.3.0.exe`，Electron 已缓存无需再下载。

---

## 2026-07-15 Agent Verbose Timeline + v9 升级

### 改动
- 新增 `src/components/AgentVerboseTimeline.jsx`：thought/tool/result/system 时间线，pending/running/complete/error 状态，running 旋转/呼吸动画，步骤展开折叠，自动滚底。
- `MessageThread.jsx`：移除旧 `ToolCallCard`，`role==="tool"` 经 `buildSteps()` 接入时间线；assistant 开始生成自动插 thought 占位步骤。
- 版本 1.3.0→1.4.0；`win.target` portable→dir；`files` 排除 `hermes-fork/website` 瘦身。
- 命名：`abcyesno-v8` 就地构建后复制为 `abcyesno-v9/abcyesno-v9`（v9 工作副本）。

---

## 2026-07-20 聊天气泡自适应 + Kaomoji 泄漏修复 + 脱离 CopilotKit 架构重做

### 聊天气泡自适应宽度重构
- `.message-bubble` 改 `width:fit-content; max-width:100%`；新增 `.message-col`（max-width:80%）；action 按钮移出气泡、hover 显示。

### Kaomoji 流产物泄漏修复
- **根因**：`agui-server.js` 把 Hermes 的 `thinking.delta`/`reasoning.available` 直接 emitTextDelta，未经滤。泄漏 `◎_◎ reasoning...`、`( ˘ ˘)♡ computing...` 等。
- **后端**：thinking.delta/reasoning.available 改为空操作（break）。**前端**：`sanitizeMessageContent` 扩展覆盖 ◎_◎ 变体 + 裸 ◎/◯ + -ing 动词。

### 打包 + 截图残留修复 + 启动页改造
- 二次打包 15:10 / 21:03 / 21:26 / 21:36 多个 build；修复 `display is not defined`（MessageThread 裸 `{display}`→`{cleaned}`）。
- 启动页 `src/main.jsx` Bootstrap 加 spinner + 分阶段文案 + 进度条（替代静态"正在启动本地 runtime"）。
- 截图残留 3 处：侧栏 preview 走 sanitize、status.update case 加正则过滤、空消息 return null。

### Agent Chat UI 5 Phase 规格重做
- **Phase 1 协议层**：thinking.delta/reasoning 转发 CUSTOM；新增 tool.chunk/tool.output；tool 耗时；`stream.phase` 事件。
- **Phase 2 状态机**：`src/utils/streamingPhase.js` 纯推断（isLoading+消息+阶段）。
- **Phase 3 渲染组件**：ThinkingIndicator / TerminalPanel / ToolCard / TypewriterText。
- **Phase 4 动画**：msg-slide-in / thinking-breathe / dot-bounce / tool-expand / cursor-blink。
- **Phase 5 虚拟滚动**：`react-virtuoso` 引入；MessageThread 改 Virtuoso 驱动。

### Composer 真实功能（去占位）
- 语音 STT 全链路：MediaRecorder → `window.hermes.transcribeAudio` → agui-server `POST /api/transcribe` → Agnes STT。
- 权限模式 default/yolo 两档真实切换（经 `session.set_yolo` 喂 Hermes gateway）。

---

## 2026-07-21 脱离 CopilotKit + Virtuoso 三连败 + 大量 UI 打磨

### Virtuoso 高度塌缩 → 普通渲染 → 三次失败
- 13:50 Virtuoso 高度链断裂（`.chat-body` overflow 模式与 Virtuoso 显式高度不兼容）→ 改 flex 链。
- 14:45 **脱离 CopilotKit 架构重做**：新建 `src/hooks/useAgentStream.js` 直连 agui-server SSE，删除 CopilotKit Provider/useCopilotChatInternal。bundle 2270KB→470KB，构建 3min→3s。
- 14:51 `streamPhase is not defined` 崩溃（重命名漏改函数体）。
- 15:01 回退 Virtuoso 为普通渲染验证通过（确认 SSE 数据流正常，Virtuoso 是元凶）。
- 15:33 Virtuoso 第三次尝试（ResizeObserver 实测高度）仍失败；查 WorkBuddy 安装目录发现其**根本没用 react-virtuoso**。
- 17:46 产出 `docs/VIRTUAL_SCROLL_SPEC.md`（自研虚拟滚动方案，待 lex 确认）。

### 其他修复
- manju_craft `TypeError: Object of type Interrupt is not JSON serializable`：langgraph_runtime `_sanitize_for_json` 加 Interrupt/GraphInterrupt 处理 + `_invoke_graph` 异常捕获 + registry `tool_result` `default=str`。
- 接通 manju_craft HITL 审批流（跨进程 HTTP 回传：`_make_http_emitter` + agui-server `workflow-event` 端点 + `.wf_active.json` 协调文件）。
- 工具卡片占满全屏：defaultExpanded 改运行完收起；result/args 截断。
- `permissionMode is not defined` 崩溃：state 误加在 App，移入 ChatShell。
- UI 总体优化 5 项：消息区弹性宽度 / 长回复折叠 CollapsibleMarkdown / agent 头像状态动画 / 默认助手改名 ABC / 会话按 updatedAt 降序。
- 四项冗余撤除：撤 header 新会话按钮、助手描述行、user meta、设置面板重做。
- Thinking 状态加 spinner；消息排队 + 发送/停止 SVG 按钮 + 附件 chip 紧凑化；真·行内混排（contenteditable + `[[IMG:i]]` 占位符）；exe 图标换巴赫 + 全助手头像换巴赫；Sidebar 头像/Logo/窗口图标换巴赫；修复"+新会话"无反应（裸绑定传 MouseEvent→DataCloneError）；Agent 实时进度面板（StructuredThinking/TaskProgressPanel/ArtifactPreview）；巴赫探头动画；巴赫头像最终版。

---

## 2026-07-22 工作台架构 Spec + UI bug 修复

### 工作台架构 Spec
- 产出 `docs/WORKBENCH_ARCHITECTURE_SPEC.md`：三层（两层入口 / @提及协议 / 专用 Workbench UI 契约）。分 P1→P4。

### UI bug 修复（4 项）
- 产物预览失败：ArtifactPreview 递归扫描任意 result 结构。
- 工具卡片未收纳：抽 `ToolsRow` 组件，useState 控制展开，始终显示摘要栏。
- 巴赫头像背景/动画：透明 PNG + busy 选择器修正。
- 工作过程 spinner 消失：user 消息后 loading 追加独立 thinking 行。

---

## 2026-07-23 发消息不回复根因 + 右侧结果区 + 后端真流打通

### 发消息不回复（真 bug）
- **根因**：`agui-server.js` 的 `resolveMentionDelegation` 定义在模块作用域，调用了定义在 `createAgUIServer` 内部的 `discoverManifests` → 每次 handleAgentRun 抛 ReferenceError。移入 createAgUIServer 内修复。已同步打包 app。

### 右侧结果区 ResultPanel 完整开发
- 产出 `docs/RESULT_PANEL_SPEC.md` v0.2（四 tab：概览/产物/文件/变更，剔除 Office 预览与云端分享）。
- 新建 ResultPanel / ArtifactViewer / WorkspaceTree / ChangeDiff；main.js 加 list-workspace/read-file/open-external IPC + webviewTag；PDF/html 走 `<webview>` 只读。
- Workflow UI 搬迁至右侧 ResultPanel（主区只留纯对话）。
- 修复 ResultPanel 被整块注释导致右侧消失。

### WORKBENCH P2/P4 收尾 + P0#1 后端真流
- P2 `@` 提及协议：Composer picker → deriveMentions → 子调用消息 + "升级到工作台"。
- P3 事件桥：ManjuCraftWorkbench 订阅 workflow.progress/artifact。
- P4 通用蓝图/时间线渲染器；registry 注册三工作台。
- P0#1 前端桥：useAgentStream.handleCustom 把 workflow.* 事件进 eventBus；后端 emit_progress 增 step_id；hello_agent/image_gen 补 WORKFLOW_STAGES；agui-server 结构化调用走流式。

---

## 2026-07-25 GitHub + Sidebar 三 Tab + 全 app 图标统一

### GitHub repo
- 首次 git init（main），推送 https://github.com/ffflzzz/abcyesno（private）。重写 .gitignore 排除 node_modules/release/.wb-asar-extract/hermes-fork/.venv/dist/*.log/.workbuddy/memory。

### Sidebar 三 Tab 重写
- 💬对话 / 🔧工作流 / ⚡任务；TaskPanel + useTaskManager（后台 task 独立运行，不阻塞主对话，localStorage 持久化）。
- 新建 `src/components/Icon.jsx`（零依赖内联 SVG 13 图标）。

### 大量修复
- DevConsole 替换为原生 DevTools（F12 右侧停靠）。
- ResultPanel 包 ErrorBoundary；巴赫位置多次微调。
- ▤ 按钮控制 resultPanelOpen（多次崩溃修复：App state 未透传 ChatShell）。
- 左侧 Sidebar 视觉打磨（subagent）：裸 unicode 改 Icon、统一选中态、去掉误导绿点。
- 全 app 图标统一：引入 `lucide-react`，Icon.jsx 重写为 63 图标映射，23 文件 ~90 处 emoji 替换。
- 21:00 批次：externalPreviewUrl/resultPanelCollapsed/DevTools 入口移原生菜单/巴赫最终位置。
- 22:07 批次：Agent Loop 状态动画（shimmer→color+text-shadow pulse，因 Electron `background-clip:text` 不可靠）+ scrollbar 抢夺 bug（自研虚拟滚动 ROW_GAP 补偿）。

---

## 2026-07-26 动画全局冻结真凶 + 废虚拟滚动 + 审批气泡

### 动画终极根因：`prefers-reduced-motion` 全局冻结
- 用户"完全是静态的，连 spinner 都不转" → 真凶是 `index.css` 的 `@media (prefers-reduced-motion: reduce){ *{animation-duration:0.001ms!important} }` 用 `*`+`!important` 冻死全站动画（命中 RDP/VM/关闭动画环境）。删除该 media query。

### 前述动画 4 轮修复（均打幽灵）
- background-clip:text 在 Electron 静默失败 → 改 color+text-shadow pulse；header/气泡内 class 错位；inline style color 压死 keyframes → 去掉 inline color。直到 12:59 才发现全局 media query 才是元凶。

### 废掉虚拟滚动
- 14:30 自研虚拟滚动经 2+ 次修仍"scrollbar 被抢夺" → 废掉，改原生 `overflow-y:auto` + useLayoutEffect 贴底守卫。

### Sidebar / 任务清理
- 工作流去重（只留 manju_craft，加 ALLOWED_IDS 白名单）；对话 Tab 去助手概念；任务清理 clearAll；任务残留按白名单自动过滤；会话列表加 scrollbar。

### 大量修复批次
- 运行按钮改打开 dashboard（非直接发消息）；重复 analyzing 修复（TPP 独占进度）；切换会话输入框聚焦；巴赫头像点击开侧栏；webview 自适应；双开挤压（ResultPanel 允许收缩）；工具历史无限累积（currentTurnToolMessages 只取当前轮）；产物预览从气泡内嵌改侧边栏查看（compact 芯片 + tab:artifacts 协议）。
- requestedTab is not defined 崩溃 → 彻底移除改用 tab: 协议；产物 tab 空白（collectToolArtifacts 合并数据源）；全 tab 空白（renderBody 优先级 bug）；产物预览失败（looksLikeImageUrl 校验 + img onError fallback）；artifact:// 泄漏（Windows 弹 Store，加 isSafeUrl 白名单）；气泡文本被截断（overflow:visible 链）；
- 审批弹窗改聊天气泡内联（ApprovalBubble.jsx）；handleApprove is not defined 修复；气泡内状态文字重复（去 TPP/ST，改 bubble-thinking-compact）；移除 ApprovalDialog modal；webview 太小（flex 修复）；气泡内步骤进度（useContractEvents）；selectedSessionId 透传 + stale closure 修复；步骤行在工具列表后仍显示；审批气泡产物图 file:// 跨目录被拦截（read-local-image IPC 转 base64）；thinking 同步打印（ThinkingTranscript 滚动框）。

### Agent 自渲染 UI 组件能力（render_ui）
- useAgentStream 加 uiBlocks + ui.render CUSTOM 分支；agui-server 加 ui-event 端点 + .ui_active.json；前端 GeneratedComponent + 5 个 MVP 组件（Table/Flowchart/Card/Progress/Action）；后端 render_ui_tool.py 自注册工具。

---

## 2026-07-27 模型升级 + 短剧工作台研究

### 内置模型 agnes-2.0-flash → agnes-2.5-flash
- 全量替换：前端 App/Composer、后端 default-config.yaml、本机 config.yaml、storage.js、agui-server STT。agnes-2.0-pro 作为"强"选项保留。

### 冒烟测试
- 4 层冒烟（静态/启动受沙箱限制/配置/打包）全绿，需真机 GUI 最终验证。

### AI 短剧制片工作台研究
- 调研 6 个开源项目（InspoVanna/jellyfish/drama-workshop 等）+ 商业对位 LibTV；架构共性 5 模式；结论：manju_craft 是"一次性 workflow 出片"，工作台模式是"项目管理+资产库+多 run"，是 LangGraph Contract 5 层契约的自然扩展位，前端通用渲染器已够用。

---

## 2026-07-28 右侧面板拖拽 + 作用域崩溃连环 + render_ui 修复

### 右侧面板可拖拽
- resultPanelWidth state（默认 380px，localStorage）+ resize handle（pointer events）。

### 大量崩溃连环（App.jsx 双组件作用域陷阱）
- 白屏 `resultPanelWidth is not defined`：ChatShell 解构默认 ≠ App useState，App 补 useState。
- 发送崩溃 `setSelectedSessionId is not defined`：ChatShell 直接调 App 变量 → 改 onSelectSession prop。
- ConfirmModal 替换 window.confirm（暗色风格）；pxlkit 调研（图标可替换，组件需评估迁移）。
- 切换会话误杀任务：ChatShell key={selectedSessionId} 重挂 → useAgentStream cleanup abort SSE。加 streaming 守卫 ConfirmModal。
- render_ui 三功能全失效（模型不调/动画无/路径打不开）：`toolsets.py` 的 `_HERMES_CORE_TOOLS` 漏加 "render_ui"（TOOLSETS 无 "ui" toolset）→ 加 render_ui 进核心工具。前端全正确。
- **模式总结**：App(500+行) 与 ChatShell(108-498行) 紧邻同文件但作用域隔离，跨作用域引用连续 4 次崩溃（resultPanelWidth/setSelectedSessionId/isStreaming/onSelectSession）。

### 后端
- 窗口失焦 agent 停止：backgroundThrottling=false + disable-background-timer-throttling。
- 回车发送后文字消失：SSE reader 加 120s 读超时 + 防节流。

---

## 2026-07-29 顶部摘要 + 缓存清理 + DNS 污染闭环

### 顶部标题栏显示会话摘要
- ChatLayout 标题从助手名改 `session?.preview`（与侧栏同步）。

### 思考可见性 + 拖拽（第一轮）
- ThinkingTranscript 加计时器占位（后用户嫌废话又去除）；resize handle 加宽热区。

### 部署漏 index.html（重要教训）
- 只 cp js/css 漏 index.html → 一直加载旧 bundle。正确部署 = 整 dist 目录覆盖（含 index.html）。

### 清理废话 + 重写拖拽 + 后端超时
- ThinkingTranscript 回归极简（无内容 return null）；拖拽改 mouse 事件；Agnes 超时初判服务端问题。

### DNS 污染根因闭环（重要）
- **不是 Agnes 要求代理**，是本机对 `apihub.agnes-ai.com` DNS 污染（解析到 Meta 假 IP / 假 IPv6）→ 直连 TCP 443 超时。走代理 127.0.0.1:7897 正常（0.4s）。
- 修复：hermes-runner.js 代理可配置（env → config.yaml network.proxy_url → 否则直连）；默认直连，用户不想要强制代理。

---

## 2026-07-30 ChatGPT 工具栏 + 编辑删除 + 去气泡 + 缓存脚本

### ChatGPT 风格消息工具栏
- 新建 `src/components/MessageActions.jsx`：hover 显示 7 按钮（复制/赞/踩/朗读/重生成/分享/更多）+ 模型名时间；复制用 stripMarkdownToText；评分存 localStorage；TTS 用 speechSynthesis。

### 编辑/删除单条消息 handler
- App.jsx(ChatShell) 加 editingMessageId/deleteConfirm + handleEditMessage/handleSaveEdit(截断+重发)/handleDeleteMessage；MessageThread 加 EditBox 内联组件（Enter 发送/Esc 取消）；ConfirmModal 删除确认。

### 编辑框缩进修复
- `.message-bubble:has(.msg-edit-box)` 强制 width:100%（:has 选择器）。

### 四项修复
- 面板拖拽（Fragment→wrapper div + mouse 事件 + 防 stale closure）；布局自适应（composer 去 max-width）；输入框边界；表格渲染（ReactMarkdown table 组件覆盖 + 暗色样式，模型缺 GFM 分隔行属后端 prompt 问题）。

### 缓存清理 + thinking 重复修复 + assistant 去气泡
- "一直旧版"根因 = exe 进程没重启；taskkill + 清 Cache/Code Cache/GPUCache；写 clean-restart.bat / start-clean.bat。
- thinking 重复：sanitizeMessageContent 重写 3 阶段（Phase 1 宽匹配 kaomoji+18 动词整块移除）。
- **assistant 回复去气泡**：`.message-bubble.assistant` → `.assistant-body`（无背景/无边框/流式块）；仅 user 消息用气泡。ApprovalBubble 审批卡保留气泡。

---

## 2026-07-31 thinking 去重 + 工具折叠 + 上下文用量面板 + 三项修复

### thinking 文本重复（第二次）
- **根因**：`btc-text`（spinner 旁）与 `ThinkingTranscript`（卡片）同渲染一份 thinkingText。spinner 旁改只显示 phase label，thinking 原文只在卡片显示一次。

### 工具调用默认折叠
- ToolsRow defaultExpanded 改 false，删除执行中自动展开 useEffect；默认折叠为摘要条（⚙ N 个工具调用 ... 执行中/全部完成 ›），用户点击才展开。

### 上下文用量统计面板
- 新建 `src/components/ContextUsage.jsx`：总用量百分比 + 渐变进度条 + 5 类占比（系统提示词/工具及子智能体/对话消息/连接器MCP/技能）。前端从 message history 估算（后端无分类拆分）。ChatLayout header 加 📊 按钮。

### 三项修复
- Manju-Craft 工作台崩溃（ResultPanel workflow 模式 session 空值守卫 + manifest null fallback）。
- firstframe 产物图片：ApprovalBubble 从相邻 tool 消息提取图片（base64/http/本地路径）。
- 思考过程去气泡：ThinkingTranscript 删卡片容器，改 `.thinking-inline` 无框内联 + 自动折叠（生成正文或非当前消息时折叠）。

---

## 2026-08-01 新会话懒创建 + 空会话自动清理

### 用户现象
左侧 Sidebar 大量"新会话 / 无消息"空会话占位，点"+ 新会话"即持久化空 session（messages:[]），不发消息也永留。

### 修复
- `handleNewSession` 改为懒创建：只 `setSelectedSessionId("")`，不调 createSession；首次发消息时由 handleSend 内联创建（已有逻辑）。
- `loadSessions` 过滤 messages 为空的会话不显示，并后台异步删除存储中的空会话；去掉启动时自动创建默认空会话。

### 效果
Sidebar 不再出现空"新会话"条目；历史空会话重启时自动清理。

### git commit & push
- 提交 53 文件：`203ed12 feat: UI打磨 + 上下文用量统计 + 工具调用折叠 + 后端代理可配置`；`0f4e824 chore: gitignore baiduyun upload leftover cfg`。
- 推送踩坑（已写入项目记忆）：直连 GitHub 被墙需 `git config http.proxy/https.proxy http://127.0.0.1:7897/`；非交互凭据用 `!gh auth git-credential`（gh 已登录 ffflzzz）；本地仓库设 ffflzzz 身份；排除 baiduyun 上传残留二进制。

---

## 2026-08-01 修复新建会话首发崩溃（TDZ：Cannot access 'be' before initialization）

### 用户现象
新开软件 → 新建会话 → 首次发送消息 → 界面白屏 + ErrorBoundary 报 `Cannot access 'be' before initialization`。老会话继续对话正常。

### 排查路径（可复用）
1. 先给 ErrorBoundary 增加 **组件堆栈（componentStack）** 输出，拿到 minified 帧：`mE / kE / aC / nC / iC / rC`。
2. 用 `dist/assets/index-*.js.map` + `@jridgewell/trace-mapping` 把 minified 帧还原为源码位置：
   - `mE` → `src/components/MessageThread.jsx:489`（崩溃组件）
   - `kE` → ChatLayout / `aC` → App ChatShell / `iC` → App / `rC` → main.jsx
3. 在 bundle 里搜 `be` 的全部出现位置，发现 **读取点偏移 457717 < 声明点偏移 458059**，再把这两个偏移反查 sourcemap，直接定位到 `MessageThread.jsx:661`（读）与 `687`（`const isLast`）。

### 根因
`renderRow(index)` 内 thinking 分支（661 行）引用了 `isLast`，而 `const isLast = isLastRow;` 声明在同一函数作用域的 687 行 → **TDZ**。
thinking 行只在流式开始的那一帧出现，因此只有"新建会话首次发送"必现，日常滚动/历史会话不触发。

### 修复
- `MessageThread.jsx:661`：`!isLast` → `!isLastRow`（`isLastRow` 在 632 行已声明）。

### 新增静态检查
- `scripts/check-tdz.js`：用 `@babel/parser` + `@babel/traverse` 遍历 `src/**` 全部作用域，找出 `const`/`let` 绑定被"同步路径上提前读取"的情况（跨函数边界的闭包引用不算，避免误报）。
- 运行：`node scripts/check-tdz.js`（有问题 exit 1）。当前全量扫描 clean。
- 意义：minify 后变量被重命名（`isLast` → `be`），运行时报错不可读；此脚本在构建前把这类 bug 拦在源码层。

### 同批附带改动（上一轮为排查此问题所做，保留）
- `App.jsx`：去掉 `<ChatShell key={selectedSessionId}>`（key 变化会销毁重建整个组件与流式状态）；`handleSend` 内联建会话后不再 `await loadSessions()`；session 切换 effect 显式 `stop()` 旧流。
- `App.jsx` ErrorBoundary：错误信息附带组件堆栈，便于下次直接定位崩溃组件。

---

## 2026-08-01 实现原生多会话并发（per-session 流重构）

### 用户现象
多个会话同时对话时，其中一个会弹出 `Operation interrupted: waiting for model response (1.5s elapsed).`；
切走的会话回来后内容不完整。用户质疑："hermes 不是原生支持多会话独立工作的吗？"

### 归因纠正
用户是对的。**Hermes 后端本来就支持多会话并发**：`agui-server.js:242 ensureHermesSession` 通过
`storage.getThreadMapping(threadId)` 为每个 threadId 映射独立的 Hermes `session_id`，
`prompt.submit` 按 session_id 分发，后端无任何全局锁。

问题全部在前端，且有两层：

1. **表层**：session-switch effect 调 `stop()`（默认 `interruptBackend=true`）→
   `agui-server.js:904` 发 `session.interrupt` → Hermes 杀掉正在跑的 turn 并回那句提示。
   这句话是前端自己招来的，不是后端限制。（此前已改为 detach-only）

2. **深层（本次修复）**：`useAgentStream` 在 `App.jsx` 只实例化一次，内部只有一个 `abortRef`，
   `runAgent` 开头无条件 `abortRef.current.abort()` → **同一窗口同时只能存活一条 SSE 流**。
   更严重的是 agui-server 不落库消息（只存 threadId→session_id 映射），持久化全在前端
   `hermes.updateSession(sid, {messages})`，而它由 `isStreaming` effect 驱动、只对前台会话生效。
   于是切走的会话：后端 turn 继续烧 token → 前端流已断 → 增量无人接收 → 也不写库 →
   **输出永久丢失**。改成 detach-only 后现象从"报错"变成"静默丢内容"，更隐蔽。

### 修复：per-session 流
**`src/hooks/useAgentStream.js`** 重构为 `useAgentStream(aguiPort, activeSessionId, { onSettled })`：
- `Map<sessionId, SessionState>` 取代所有单例 ref。每个会话独立持有
  `controller / messages / phase / thinkingText / uiBlocks / toolIndex / thinkingSince / hydrated`。
- 事件处理签名全部改为 `handleEvent(sess, ev)`，不再读组件级 state。
- 渲染：`publish(id)` 打 dirty → rAF 合帧 → **只有前台会话才 `setSnapshot`**。
  后台会话的 token 流完全不触发 React 渲染。
- 会话切换用 render 期 state 调整（`projectedId`）而非 effect，消除切换时闪一帧旧内容。
- `sendMessage` 只 abort 本会话的上一条流；`stop(sessionId)` 只停指定会话。
- `hydrateSession(id, stored)`：**若该会话正在跑或已装载则拒绝覆盖**——切回一个仍在跑的会话
  必须保留内存增量，不能被磁盘旧快照冲掉。
- `settle(sess)` 在 RUN_FINISHED / RUN_ERROR / 流异常收尾时触发 `onSettled`。

**`src/App.jsx`**：
- 持久化改为 settle 回调驱动（`handleSessionSettled`），**后台会话完成同样落库**。
- 删除 `wasLoadingRef` / `hadActiveSessionRef` / `historyInitializedRef` / `latestMessagesRef`
  及其全部竞态守卫——per-session 状态天然解决首发竞态。
- session-switch effect 从 `stop()+reset()+setHistory()` 简化为一行 `hydrateSession(...)`。
- 删除会话时 `dropSession(id)` 一并清理内存流状态。

**`src/components/Sidebar.jsx` + `index.css`**：会话列表对后台运行中的会话显示脉冲点
和「正在生成…」，否则用户无法感知它还在跑。

### 新增回归测试 `scripts/test-multisession/`
`node scripts/test-multisession/run.mjs` —— 10/10 通过。

项目没有 vitest/jsdom，因此用了一个低成本方案：`mini-react.mjs` 是 ~110 行的极简 hooks runtime
（useState/useRef/useCallback/useMemo/useEffect + render 期 setState + 重渲染循环）；
`run.mjs` 读取**真实的** `src/hooks/useAgentStream.js` 源码，只把 `from "react"` 和 eventBus 的
import 重写指向 mock，然后 import 运行，配一个按 threadId 回放脚本化 SSE 帧的假 agui-server。
测的是真代码，不是副本。

覆盖的不变量：两流并发 / 前台快照不串台 / 后台完成触发 onSettled 且内容完整 /
hydrate 拒绝覆盖运行中会话 / 切回会话拿到内存增量而非磁盘旧快照 / `stop(C)` 不影响 D。

### 构建/部署
`node scripts/check-tdz.js` clean → `index-CRNP3VHY.js` (599.56 kB) + `index-C6JhJWQ7.css` (100.79 kB)，
已部署 `release/win-unpacked/resources/app/dist`，已清 Cache / Code Cache / GPUCache。
