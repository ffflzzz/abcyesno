# 浏览器自动化（Path B：纯 Playwright）技术规格

> 范围：让 Abcyesno 的 agent 能在用户机器上做浏览器自动化，且随便携 exe 分发，新电脑零配置开箱即用。
> 状态：已全量落地（2026-08-02）。7 工具 + toolset 启用 + Chromium 打包 + 引导 skill 均已就绪；§5.5 侧栏内置浏览器（route B / CDP 驱动 Electron 内置 `<webview>`）也已实现并接入。开发机 agent 可立即试用 `browser-pw`，便携 release 已手动同步相关文件。
> 替代方案：ego-lite（macOS-only 独立浏览器 app，不可嵌入 webview、不可打包、强依赖用户登录态）→ 否决；browser-use / Stagehand（重量级、引入第二套 LLM 或 Node 链）→ 不采用。Path B 仅用 Hermes 既有 LLM 直接驱动 Playwright 原子工具，最轻、最适便携。

---

## 1. 目标与非目标

### 1.1 目标
- Agent 通过一组原子工具逐步驱动网页：打开 URL、读内容、点击、填表、滚动、截图、关闭。
- 这些工具随便携版分发，在**全新 Windows 机器**上无需安装 Playwright / 下载 Chromium / 配置环境变量即可工作。
- 不引入 Node 工具链、不引入第二套 LLM、不依赖任何外部登录态。

### 1.2 非目标
- 不在应用内嵌可见浏览器窗口（可选，见 §6）。
- 不做自愈式 Web 智能体（如 browser-use 的多步推理循环）——Path B 由 Hermes 既有的 LLM 原生 function-calling 驱动。
- 不改造 Hermes 的 agent 循环本身。

---

## 2. 设计原则

| 原则 | 说明 |
|------|------|
| 复用既有运行时 | 用 Hermes 自己的 `.venv` 里的 `playwright` 包，不另装。 |
| 单 LLM 驱动 | 由 Agnes（Hermes 现有 provider）直接决定调用哪个 `pw_browser_*` 工具。 |
| 会话按 `task_id` 隔离 | 单次自动化任务用一个稳定 `task_id`，多任务互不踩踏。 |
| 零配置分发 | Chromium 随 `extraResources` 打进 `win-unpacked`，运行时通过 `PLAYWRIGHT_BROWSERS_PATH` 指过去。 |
| toolset 休眠开关 | 工具靠 `HERMES_TUI_TOOLSETS` 启用；未列出则对 agent 不可见，可安全回滚。 |

---

## 3. 已落地现状（实现基线）

| 项 | 状态 | 位置 |
|----|------|------|
| 7 个原子工具 + 注册 | ✅ 已实现并冒烟通过 | `hermes-fork/tools/pw_browser_tool.py` |
| toolset 启用 | ✅ 已启用（默认 `'hermes-cli,browser-pw'`） | `electron/backend/hermes-runner.js:272` |
| Chromium 安装（开发机） | ✅ 在 `C:\Users\Administrator\.hermes_portable_data\playwright-browsers\` | 含 `chromium-1234` / `chromium_headless_shell-1234` / `ffmpeg-1011` / `winldd-1007` |
| Chromium 打包进 release | ✅ 已配 `extraResources` 并手动同步到 `release/win-unpacked/resources/playwright-browsers`（embedded 模式下已非必需，保留作兼容/回退） | `package.json` `build.extraResources` |
| 运行时 `PLAYWRIGHT_BROWSERS_PATH` | ✅ 已设，指向 `resources/playwright-browsers`（回退 portable_data） | `hermes-runner.js` `env` 对象 |
| 引导 skill | ✅ 已建 | `hermes-fork/skills/browser_pw/SKILL.md` |
| `_resolve` 透传 `task_id`（去全局变量） | ✅ 已完成 | `hermes-fork/tools/pw_browser_tool.py` |
| 侧栏内置浏览器面板（§5.5 route B） | ✅ 已实现：Electron `remote-debugging-port` + Python `connect_over_cdp` 驱动内置 `<webview>` | `electron/main.js` + `src/components/BrowserPanel.jsx` + `pw_browser_tool.py` |
| 工具可见性门控 | ✅ 改为检查 `PW_CDP_URL`（Electron CDP 端点），不再依赖独立 Chromium 构建 | `pw_browser_tool.py` `check_pw_browser_requirements` |

冒烟结果（2026-08-02 模拟真实启动）：`HERMES_TUI_TOOLSETS="hermes-cli,browser-pw"` 下 `validate_toolset('browser-pw')=True`，enabled toolsets = `['hermes-cli','browser-pw']`，7 个 `pw_browser_*` 工具全部暴露；`navigate('https://example.com')` → 返回 `TITLE: Example Domain`，snapshot 读到正文，screenshot 落盘，close 释放。Chromium 冒烟独立通过（dev 环境 `.venv` + portable_data 构建）。

---

## 4. 工具契约（toolset `browser-pw`）

所有工具 handler 签名 `handler(args: dict, **kwargs)`，从 `kwargs["task_id"]`（回退 `args["task_id"]`，默认 `"default"`）取会话 id；返回用 `tool_result()` / `tool_error()` 序列化 JSON 字符串。`check_fn=check_pw_browser_requirements` 在 `playwright` 不可 import 或 `PW_CDP_URL` 未设置（即不在桌面应用内运行）时使工具对 agent 不可见。浏览器是 Electron 内置 Chromium 经 CDP 驱动，不再需要独立 Chromium 构建。

| 工具 | 参数 | 行为 |
|------|------|------|
| `pw_browser_navigate` | `url`(必), `task_id`, `headless`(默认 true) | 打开 URL，`wait_until="load"`，返回 `{url,title}` |
| `pw_browser_snapshot` | `task_id`, `max_chars`(默认 8000) | 返回 `body.inner_text` 截断文本 |
| `pw_browser_click` | `ref`(必), `task_id` | 按 selector 点击，`timeout=10000` |
| `pw_browser_type` | `ref`(必), `text`(必), `task_id` | `fill` 输入框 |
| `pw_browser_scroll` | `direction`(`up`/`down`), `task_id` | `mouse.wheel` ±800 |
| `pw_browser_screenshot` | `task_id` | 存 PNG 到 `$HERMES_HOME/browser-shots/`，返回路径 |
| `pw_browser_close` | `task_id` | 关闭浏览器并释放 `_SESSIONS[task_id]` |

**selector 格式（`_resolve` 支持）**：CSS 选择器、`//xpath`、`text=...`（`exact=False` 取首个）、`role=...`（ARIA 角色名）、`placeholder=...`。

---

## 5. 实施清单（交付步骤）

### 5.1 启用 toolset（必做）
`electron/backend/hermes-runner.js:272`：
```js
HERMES_TUI_TOOLSETS: process.env.HERMES_TUI_TOOLSETS || 'hermes-cli,browser-pw',
```
保留 `hermes-cli` 以维持 langgraph / shell / file 工具。

### 5.2 引导 skill（推荐）
新建 `hermes-fork/skills/browser_pw/SKILL.md`，短说明何时优先用 `pw_browser_*` 工具（用户要打开/访问/读取网页、填表、截网页图时），提升模型选工具的稳定性。注意：`browser-pw` 是 **toolset** 而非 LangGraph skill，靠 §5.1 启用即可被 agent 看见；SKILL.md 仅作行为引导。

### 5.3 打包内置 Chromium（分发零配置必做）

5.3.1 `package.json` 的 `"build"` 增加：
```json
"extraResources": [
  { "from": "build/playwright-browsers", "to": "playwright-browsers" }
]
```
构建前把开发机 `chromium-*` 与 `chromium_headless_shell-*`（及 `winldd-*`、`ffmpeg-*`）复制到 `build/playwright-browsers/`。只跑无头可仅带 `chromium_headless_shell-*`，省约 150MB。

5.3.2 `electron/backend/hermes-runner.js` 的 `env` 对象内（紧跟 `HERMES_TUI_TOOLSETS` 后）增加：
```js
PLAYWRIGHT_BROWSERS_PATH: path.join(process.resourcesPath, 'playwright-browsers'),
```
运行时 `process.resourcesPath` → `release/win-unpacked/resources`。开发环境无此目录时，`_chromium_installed()` 会回退到默认缓存与 `.hermes_portable_data/playwright-browsers`，不影响开发。

### 5.4 加固 `pw_browser_tool.py`（上线前必做）
- `_resolve()` 当前依赖模块级全局 `_CURRENT_TASK_ID`，而各 handler 收到的是 `task_id`，并发会解析错会话。改为给 `_resolve` 增加 `task_id` 实参，从 `click` / `type` 透传，去掉对全局的依赖。
- 修类型注解：`_SESSIONS: Dict[str, _Session]`（当前写成 `Dict[str, Dict[str, Any]]`，实际存 `_Session` 实例）。
- `role=` 分支 `get_by_role(s[len("role="):])` 仅支持纯角色名（如 `role=button`）；如需 `role=button name=Submit` 需解析。最小做法：在 schema 描述写清支持的 selector 格式。

### 5.5 侧栏内置浏览器（route B / CDP 驱动 Electron 内置 Chromium）— 已实现

不再用独立 Playwright 窗口，而是把浏览器**嵌进应用侧栏**：用 Electron 内置的 `<webview>`（即 Electron 自带的 Chromium）作为「浏览器」面板，Python 端用 Playwright `connect_over_cdp(PW_CDP_URL)` 连到 Electron 的 DevTools 端口，按 marker URL 精确选中该 webview 页面来驱动。用户能在侧栏实时看到 agent 操作。

实施要点：
- `electron/main.js`：在 `whenReady` 前追加 `app.commandLine.appendSwitch('remote-debugging-port', PW_CDP_PORT)`（默认 `18922`，可经 `PW_CDP_PORT` 覆盖），并写入 `process.env.PW_CDP_URL` 与 `PW_WEBVIEW_MARKER`（`data:text/html,<title>browser-pw-marker</title>`）。新增 `get-browser-info` IPC 供前端取 marker。
- `preload.js`：暴露 `getBrowserInfo()`。
- `src/components/BrowserPanel.jsx`：侧栏 `<webview src={marker} partition="pw-browser">`，header 含刷新/收起；点头部地球图标开合，agent 调用 `pw_browser_*` 时前端自动弹出。
- `pw_browser_tool.py`：`_Session` 改为 `connect_over_cdp` 连 Electron，扫 CDP targets 找 `marker` 对应的 webview page 并缓存为共享页面（多 task_id 共用同一可见页）；`pw_browser_close` 不再关闭浏览器，仅重载回 marker；`check_pw_browser_requirements` 改为检查 `PW_CDP_URL`。
- `headless` 参数已无意义（面板恒可见），schema 标注忽略。

安全/隔离：CDP 端口仅绑 `127.0.0.1`；目标选择严格按 marker 过滤，绝不选中应用主窗口页面。

---

## 6. 验证

| 层级 | 命令 / 动作 | 通过判据 |
|------|------------|----------|
| 静态编译 | `cd hermes-fork && python -m py_compile tools/pw_browser_tool.py` | 无报错 |
| 单测（开发机） | 脚本依次 `navigate→snapshot→click(真实selector)→type→screenshot→close` | 无异常，snapshot 非空 |
| 集成 | `npx electron-builder --win --dir` 后运行，问 agent"打开 example.com 并告诉我标题" | agent 调用 `pw_browser_navigate` 并返回标题；spawn 环境含 `browser-pw` |
| 真机验收 | 在**未装 Playwright、未设全局 `PLAYWRIGHT_BROWSERS_PATH`** 的干净 Windows 跑 `win-unpacked/Abcyesno.exe` | 浏览器从 `resources/playwright-browsers` 启动并可用 |

---

## 7. 验收标准

1. `HERMES_TUI_TOOLSETS` 默认含 `browser-pw`。
2. Agent 能用 7 个 `pw_browser_*` 工具端到端驱动网页。
3. 便携版内置 Chromium，干净机器零手动配置可用。
4. 无 Node、无第二套 LLM、无外部登录态依赖。

---

## 8. 已知限制与风险

- **单用户桌面**：会话按 `task_id` 隔离，`_resolve` 已透传 `task_id` 并去除全局 `_CURRENT_TASK_ID`，并发解析可靠（§5.4 已完成）。embedded 模式下所有 task 共用同一个可见浏览器面板，`pw_browser_close` 仅把面板重置回 marker。
- **CDP 端口冲突**：`remote-debugging-port` 默认 `18922`，若被占用则浏览器自动化不可用（应用其余功能不受影响），可用 `PW_CDP_PORT` 覆盖。端口仅绑 `127.0.0.1`。
- **目标选择安全**：Python 严格按 `PW_WEBVIEW_MARKER` 过滤 CDP targets，绝不选中应用主窗口页面；用户手动关闭面板 webview 后，Python 检测到页面关闭会自动重连（重新扫 marker）。
- **版本耦合（仅外部/headless 回退路径）**：若改回独立 Playwright 窗口，则 Chromium 构建版本须与 `playwright` Python 版本匹配，升级后须重下 Chromium 到 `build/playwright-browsers`。embedded 模式用的是 Electron 自带 Chromium，无此耦合。
- **体积（仅外部回退路径）**：`build/playwright-browsers` 约 702MB 随 `extraResources` 进 release，embedded 模式下已非必需，保留作兼容/回退；该目录已在 `.gitignore`。
- **selector 精度**：`text=` 默认 `exact=False` 取首个匹配；复杂页面可能误点，需在 SKILL.md 引导模型先用 `snapshot` 确认文本。

---

## 9. 回滚

改回 `'hermes-cli'` 并移除 `extraResources` 即停用，`pw_browser_tool.py` 可安全保持休眠（文件不删）。

---

## 10. 文件改动清单

| 文件 | 改动 |
|------|------|
| `electron/backend/hermes-runner.js` | `HERMES_TUI_TOOLSETS` 默认加 `browser-pw`；`env` 加 `PLAYWRIGHT_BROWSERS_PATH` |
| `package.json` | `build.extraResources` 增加 `build/playwright-browsers → playwright-browsers` |
| `hermes-fork/tools/pw_browser_tool.py` | `_resolve` 透传 `task_id`；改 `check_pw_browser_requirements` 检查 `PW_CDP_URL`；`_Session` 改 `connect_over_cdp` 驱动内置 webview（共享页面、close 重置 marker） |
| `hermes-fork/skills/browser_pw/SKILL.md` | 新建引导文件；更新为 embedded 面板模式 |
| `electron/main.js` | 追加 `remote-debugging-port` 开关 + 写入 `PW_CDP_URL`/`PW_WEBVIEW_MARKER`；`get-browser-info` IPC |
| `electron/preload.js` | 暴露 `getBrowserInfo()` |
| `src/components/BrowserPanel.jsx` | 新建：侧栏内置 `<webview>` 浏览器面板 |
| `src/App.jsx` / `src/components/ChatLayout.jsx` / `src/styles/index.css` | 接入 BrowserPanel（开关、自动弹出、样式） |
| `build/playwright-browsers/` | 构建前放入 Chromium 构建目录（embedded 模式非必需，保留兼容） |
