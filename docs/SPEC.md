# Abcyesno — Technical Specification

## 1. 总体架构

本方案基于 **Hermes 源码 Fork** 进行二次开发，保留其 harness 核心，拆除非必要模块，并用 Electron 替代官方 desktop 前端。

为了继续复用 CopilotKit 组件，Electron Main 中保留一个轻量的 **AG-UI Runtime Bridge**，把 CopilotKit 的 SSE 请求翻译成对 Hermes Gateway 的 JSON-RPC/WebSocket 调用。

```
Electron Frontend (React + CopilotKit)
        │
        │ HTTP/SSE to /api/ag-ui/run
        ▼
Electron Main
├─ AG-UI Runtime Bridge (Node.js/Express)
│   翻译 CopilotKit ↔ Hermes Gateway
│
└─ Hermes Gateway Client (WebSocket JSON-RPC)
        │
        ▼
Hermes Fork (hermes serve)
        │
        ▼
LangGraph Agent Skills
```

**为什么保留 AG-UI Bridge**：
- 前端可以继续使用 CopilotKit 的 hooks 和状态管理。
- 不需要把 Hermes Gateway 的 JSON-RPC 协议直接暴露给前端。
- 未来如果需要切换到其他 backend，只需替换 bridge 适配器。

## 2. 代码库结构

项目根目录规划：

```
hermes-portable/
├── electron/                  # Electron 主进程和 preload
│   ├── main.js                # 启动 Hermes serve、创建窗口、bridge
│   └── preload.js             # 前端与主进程通信接口
├── src/                       # React 前端
│   ├── App.jsx
│   ├── components/
│   └── styles/
├── hermes-fork/               # Hermes 源码 Fork（从官方复制）
│   ├── agent/                 # Agent loop / runtime（保留）
│   ├── tools/                 # Tool registry & execution（保留）
│   ├── skills/                # Skill registry
│   │   └── langgraph_agents/  # 自定义 LangGraph skills
│   ├── memory/                # Memory system（保留）
│   ├── gateway/               # JSON-RPC / WebSocket gateway（保留）
│   ├── cli.py                 # CLI 入口（精简后）
│   └── pyproject.toml         # 依赖（精简后）
├── agents/                    # 外部 LangGraph Agent 项目入口（可选）
│   └── manju-craft/           # 软链接或子模块指向 Desktop/manju-craft
├── package.json               # Electron 前端依赖和构建脚本
├── docs/                      # 项目文档
│   ├── PRD.md
│   ├── SPEC.md
│   ├── ROADMAP.md
│   ├── ACCEPTANCE.md
│   ├── UI_UX_SPEC.md
│   └── ADR/
└── README.md                  # 项目入口说明
```

## 3. Hermes Fork 改造范围

### 3.1 保留模块

| 模块 | 路径 | 作用 |
|------|------|------|
| Agent Loop | `agent/` | 推理循环、上下文、工具调用调度 |
| Tool Registry | `tools/` | 工具注册、执行、审批门控 |
| Skill Registry | `skills/` | Skill 发现、加载、调用 |
| Memory | `memory/` | 短期/长期记忆 |
| Gateway | `gateway/` | JSON-RPC / WebSocket 服务 |
| Session / State | `state.db` 等 | 会话持久化 |
| Config | `config/` | 配置加载与管理 |

### 3.2 计划拆除模块

| 模块 | 路径/文件 | 拆除原因 |
|------|-----------|----------|
| WhatsApp | `skills/whatsapp/`, `whatsapp-cloud/` | IM 通道，不需要 |
| Slack | `skills/slack/`, `tools/discord_tool.py` 等 | IM 通道，不需要 |
| Messaging Gateway | `gateway/` 中 messaging 部分 | 外部消息平台接入 |
| Cron Jobs | `cron/` | 非核心 |
| 自动更新 | `update/` 相关逻辑 | 避免官方更新 |
| Telemetry / 在线服务 | 分散在 CLI 和 runtime 中 | 剥离在线依赖 |
| 官方 Desktop 构建 | `apps/desktop/` | 用我们自己的 Electron |
| Docker / Nix 部署 | `docker/`, `flake.nix` | 不需要 |

> **注意**：拆除时需要保留 CLI 加载路径，避免 import 失败导致启动崩溃。

## 4. LangGraph Skill 接口规范

每个自定义 Agent 必须实现以下接口，才能被 Hermes skill 系统加载。

### 4.1 文件结构

```
skills/langgraph_agents/manju_craft/
├── __init__.py
├── skill.py          # Skill 入口
├── manifest.json     # Skill 元信息
└── requirements.txt  # 额外依赖（可选）
```

### 4.2 `manifest.json`

```json
{
  "id": "manju-craft",
  "name": "Manju Craft",
  "description": "剪映视频生成工作流",
  "version": "1.0.0",
  "capabilities": ["video", "jianying", "workflow"],
  "entry": "skill.py"
}
```

### 4.3 `skill.py` 最小实现

```python
from typing import AsyncIterable, Any

class ManjuCraftSkill:
    id = "manju-craft"
    name = "Manju Craft"
    description = "剪映视频生成工作流"

    def __init__(self, runtime_context: Any):
        self.ctx = runtime_context
        # 加载 LangGraph graph
        from manju_craft.graph.graph import build_graph
        self.graph = build_graph()

    async def run(self, messages: list[dict], config: dict | None = None) -> AsyncIterable[dict]:
        """
        messages: [{"role": "user"|"assistant"|"tool", "content": str}]
        返回异步事件流，每个事件 dict 包含 type 字段。
        """
        config = config or {}
        async for event in self.graph.astream({"messages": messages}, config):
            yield self._normalize_event(event)

    def _normalize_event(self, raw: Any) -> dict:
        # 把 LangGraph 事件转换为前端可识别格式
        return {"type": "token", "content": str(raw)}

# Hermes skill loader 会查找 skill 类
def skill_factory(runtime_context: Any):
    return ManjuCraftSkill(runtime_context)
```

### 4.4 输出事件格式

Skill 返回的事件必须包含 `type` 字段：

```python
{"type": "token", "content": "..."}           # 流式文本
{"type": "tool_call", "name": "...", "args": {}}  # 工具调用
{"type": "tool_result", "name": "...", "output": "..."}  # 工具结果
{"type": "status", "status": "thinking"}       # 状态更新
{"type": "done"}                               # 结束
{"type": "error", "message": "..."}            # 错误
```

## 5. Electron Main 职责

`electron/main.js` 包含以下模块：

### 5.1 Hermes Launcher
- 指定 `HERMES_HOME` 到应用数据目录（如 `%USERPROFILE%/.hermes_portable_data`）。
- 使用打包的 Python 解释器运行 `hermes-fork/cli.py serve --port 0`。
- 等待 gateway ready（探测 `/api/status` 或 WebSocket）。

### 5.2 AG-UI Runtime Bridge
- 启动一个本地 Express HTTP 服务，暴露 `/api/ag-ui/run/info` 和 `/api/ag-ui/run`。
- 接收 CopilotKit 前端的 SSE 请求。
- 将请求翻译成对 Hermes Gateway 的 JSON-RPC 调用。
- 把 Hermes 返回的事件流标准化为 AG-UI 事件格式返回前端。

### 5.3 BrowserWindow 管理
- 加载 React 前端 `dist/index.html`。
- 暴露 `window.hermes` API 给前端，用于助手/会话管理、审批等非聊天类交互。

### 5.4 事件转发
- Hermes Gateway 的流式事件经过 Bridge 转发给前端 CopilotKit。
- 审批请求等需要用户交互的事件通过 IPC 弹出对话框。

### 5.5 IPC 接口（非聊天类）

聊天类交互走 CopilotKit + AG-UI Bridge，IPC 负责助手/会话管理、审批等：

```ts
window.hermes = {
  getVersion: () => Promise<string>,
  getStatus: () => Promise<object>,

  // 助手与会话管理
  listAssistants: () => Promise<Assistant[]>,
  createAssistant: (skillId: string, name: string) => Promise<Assistant>,
  deleteAssistant: (id: string) => Promise<void>,
  listSessions: (assistantId: string) => Promise<Session[]>,
  createSession: (assistantId: string) => Promise<Session>,
  deleteSession: (id: string) => Promise<void>,
  switchSession: (id: string) => Promise<void>,

  // 审批
  respondApproval: (id: string, choice: boolean) => Promise<void>,
  onApprovalRequest: (cb: (req: ApprovalRequest) => void) => void,

  // 其他
  collectSystemInfo: () => Promise<object>,
}
```

## 6. Hermes Gateway 调用方式

Electron Main 作为 JSON-RPC client 连接 `ws://127.0.0.1:<port>/gateway`。

关键方法：

| 方法 | 作用 |
|------|------|
| `session.create` | 创建新会话 |
| `prompt.submit` | 提交用户消息 |
| `approval.respond` | 响应审批请求 |
| `tool.call` | 手动调用工具 |

事件监听：

| 事件 | 作用 |
|------|------|
| `stream` | 流式文本 |
| `tool.call` | 工具调用 |
| `tool.result` | 工具结果 |
| `approval.request` | 需要用户确认 |
| `message.complete` | 消息完成 |
| `error` | 错误 |

> 具体方法名和事件格式需对照 Hermes `gateway/` 源码确认。

## 7. 前端事件处理

前端通过 CopilotKit hooks 接收 AG-UI 标准化事件并渲染：

- `token` → 追加到 assistant 消息。
- `tool_call` / `tool_result` → 显示工具卡片。
- `approval_request` → 弹出确认对话框。
- `done` → 完成当前 assistant 消息。
- `error` → 显示错误提示。

CopilotKit 负责维护消息列表和流式状态；自定义组件负责渲染样式。

## 8. 配置管理

- `HERMES_HOME`：指向便携数据目录，避免污染用户原 Hermes 配置。
- `HERMES_PORTABLE_ROOT`：指向应用根目录，用于定位 `hermes-fork/`。
- `config.yaml`：Hermes 配置（模型、API Key、启用 skill 等）。
- `agents.json`：可选的自定义 skill 注册表。

## 9. 安全设计

- 所有工具执行仍走 Hermes 审批流程。
- Electron 前端无 node 权限，通过 preload 暴露受限 API。
- API Key 由 Hermes 配置管理，不硬编码。
- LangGraph skill 在独立进程中运行（若 Hermes 支持），避免污染主进程。

## 10. 打包与便携化

### 10.1 开发阶段
- `hermes-fork/` 直接使用源码。
- 依赖本机 Python（或项目内 venv）。

### 10.2 便携阶段
- 打包 Python 环境（如 `python-embedded` 或 venv）。
- 用 PyInstaller 把 `hermes serve` 入口打成独立 exe（可选）。
- Electron Builder 打包前端 + `hermes-fork/` + Python 环境。
- 启动脚本设置环境变量并启动后端。

## 11. 错误处理

| 场景 | 处理 |
|------|------|
| Hermes serve 启动失败 | 前端显示错误日志，提供重试按钮 |
| Gateway 连接断开 | 自动重连，前端提示离线 |
| Skill 执行报错 | Hermes 返回 `error` 事件，前端显示 |
| 工具审批超时 | Hermes 自动拒绝，前端提示 |

## 12. 依赖

### 12.1 Hermes Fork 依赖
- 保留 Hermes 原有核心依赖。
- 移除 IM 相关依赖（如 `whatsapp-chat-parser`、`slack-sdk` 等）。
- 新增 `langgraph` 依赖。

### 12.2 Electron 前端依赖
- React + Vite
- **CopilotKit**（核心）：提供 chat hooks、runtime 连接、消息状态管理
- `@copilotkit/react-ui`（可选）：仅参考样式，UI 全部自定义

## 13. 关键接口待确认

1. Hermes skill loader 的具体契约（类名、工厂函数、manifest 格式）。
2. Gateway JSON-RPC 方法名与事件 schema。
3. 如何注册自定义 skill 目录到 Hermes。
4. Hermes agent loop 如何选择调用哪个 skill。

---

**下一步**：调研 Hermes `skills/` 和 `gateway/` 源码，确认上述接口细节。
