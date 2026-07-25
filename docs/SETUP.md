# Abcyesno — 开发环境搭建指南

## 1. 环境要求

- **操作系统**：Windows 10/11（开发及目标运行环境）
- **Node.js**：≥ 20（建议 20 LTS 或 22 LTS）
- **Python**：≥ 3.10（用于运行 Hermes fork）
- **Git**：用于管理 Hermes fork 和子模块

## 2. 项目结构准备

```bash
# 进入项目根目录
cd C:\Users\Administrator\Downloads\kimicode_free\hermes-portable

# 安装 Node.js 依赖
npm install
```

## 3. Hermes Fork 准备

### 3.1 复制源码

```bash
# 从本机已安装的 Hermes 复制源码（后续可能改为 git submodule）
robocopy "C:\Users\Administrator\AppData\Local\hermes\hermes-agent" hermes-fork /E /XD .git node_modules __pycache__
```

### 3.2 创建隔离 Python 环境

```bash
cd hermes-fork
python -m venv .venv
.venv\Scripts\activate

# 安装 Hermes 核心依赖
pip install -e .

# 安装 LangGraph
pip install langgraph
```

> 后续精简依赖后，应维护一个最小化的 `requirements.txt`。

### 3.3 验证 Hermes serve 能启动

```bash
python -m hermes_cli.main serve --port 9119
# 或者使用 venv 入口脚本
# .venv\Scripts\hermes serve --port 9119
```

另开一个终端：

```bash
curl http://127.0.0.1:9119/api/status
```

返回 200 即表示成功。

## 4. 配置 Agnes API Key

Hermes 使用 `~/.hermes/config.yaml` 或环境变量配置 API Key。

开发阶段，在启动前设置：

```bash
set HERMES_HOME=%USERPROFILE%\.hermes_portable_dev
```

然后在 `%HERMES_HOME%\config.yaml` 中配置 provider 和 API Key。

> 具体配置格式参考 Hermes 官方文档或 `cli-config.yaml.example`。

## 5. Electron 前端开发

### 5.1 开发模式

```bash
# 项目根目录
npm run dev
```

这会同时启动 Vite dev server 和 Electron。

### 5.2 生产构建

```bash
npm run build
npx electron-builder --win --dir
```

产物在 `release/win-unpacked/`。

## 6. 调试技巧

### 6.1 查看 Hermes 日志

Hermes 日志默认在 `%HERMES_HOME%\logs\` 下。

### 6.2 查看 Electron 主进程日志

Electron Main 与 Hermes 子进程日志写入：

- `%USERPROFILE%\.hermes_portable_data\logs\electron.log`
- `%USERPROFILE%\.hermes_portable_data\logs\hermes.log`

### 6.3 连接 Hermes Gateway

使用浏览器 DevTools 或 wscat 连接：

```bash
npx wscat -c ws://127.0.0.1:9119/gateway
```

## 7. 添加新 Skill

1. 在 `hermes-fork/skills/langgraph_agents/` 下新建目录。
2. 编写 `manifest.json` 和 `skill.py`。
3. 重启 Hermes serve 或触发 skill reload。
4. 在 Electron 前端刷新助手列表。

## 8. 常见问题

### Q1: Hermes serve 启动时报 import error
- 检查是否删除了被 CLI 引用的模块。
- 查看 `cli.py` 中是否仍 import 了已删除的子命令。

### Q2: 前端无法连接 AG-UI Bridge
- 检查 Electron Main 是否成功启动 AG-UI runtime。
- 检查 `aguiPort` 是否正确传递给前端。

### Q3: CopilotKit 收不到流式事件
- 检查 AG-UI Bridge 是否正确转发 SSE。
- 检查 Hermes Gateway 事件是否被正确翻译。

## 9. 下一步

按 `ROADMAP.md` 的 Phase 0 ~ Phase 7 逐步推进。
