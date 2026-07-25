# Abcyesno — 已知未知事项

本文档记录在项目初期尚未确认、需要在开发过程中逐步澄清的问题。保持更新，避免后续迷路。

## 1. Hermes Skill 加载机制

- **问题**：Hermes 如何发现 `skills/` 目录下的 skill？
- **问题**：Skill 的入口约定是什么？类名、工厂函数、manifest 格式分别是什么？
- **问题**：Skill 如何接收 runtime context（如 config、logger、工具注册器）？
- **待调研文件**：
  - `hermes-fork/skills/` 加载相关代码
  - `hermes-fork/agent/agent_init.py`
  - 现有 skill 目录结构示例

## 2. Hermes Gateway 协议

- **问题**：Gateway WebSocket 连接的 URL 路径是什么？是 `/gateway` 还是 `/api/gateway`？
- **问题**：创建 session 的 JSON-RPC 方法名是什么？参数是什么？
- **问题**：提交 prompt 的方法名是什么？是 `prompt.submit` 还是 `session.prompt`？
- **问题**：流式事件的事件名有哪些？分别对应什么场景？
- **问题**：审批请求的事件格式是什么？如何响应？
- **待调研文件**：
  - `hermes-fork/gateway/`
  - 官方 desktop 源码 `apps/desktop/src/store/gateway.ts`
  - 官方 desktop 源码 `apps/desktop/src/hermes.ts`

## 3. Hermes Agent Loop 如何路由到 Skill

- **问题**：Hermes 是根据什么决定调用哪个 skill？关键词、tool 描述、还是显式路由？
- **问题**：是否需要给每个 skill 写一个 tool schema，让 LLM 自己选？
- **问题**：用户说「帮我做视频」时，Hermes 能否自动调用 `manju-craft` skill？

## 4. 模块拆除清单

- **问题**：IM 模块具体涉及哪些文件和目录？
- **问题**：自动更新逻辑分散在哪些文件中？
- **问题**：Telemetry / analytics / sentry 调用在哪些文件中？
- **问题**：`cli.py` 中注册子命令的位置在哪里？
- **待产出**：`STRIPPING_GUIDE.md` 详细清单

## 5. AG-UI Bridge 翻译逻辑

- **问题**：Hermes Gateway 的 `stream` / `tool.call` / `approval.request` 等事件如何映射到 AG-UI 的 `token` / `tool_call` / `approval_request`？
- **问题**：CopilotKit 对 `/api/ag-ui/run/info` 返回的 schema 有什么要求？
- **问题**：CopilotKit 发送的 `messages` 格式与 Hermes session message 格式是否一致？如果不一致，如何转换？

## 6. 前端状态管理

- **问题**：切换助手/会话时，CopilotKit 上下文如何正确重置？
- **问题**：助手列表和会话列表是走 IPC 还是也通过 AG-UI runtime 获取？
- **问题**：历史消息如何加载到 CopilotKit 的消息列表中？

## 7. Python 便携化

- **问题**：使用 Windows embedded Python 还是项目内 venv？
- **问题**：Hermes 依赖体积多大？精简后大概多少？
- **问题**：PyInstaller 打包 `hermes serve` 是否可行？启动速度如何？
- **问题**：LangGraph 及其依赖（如 `langchain`）如何打包？

## 8. 配置与数据隔离

- **问题**：`HERMES_HOME` 应该指向哪里？应用目录还是用户目录？
- **问题**：便携版运行时如何避免污染用户已有的 Hermes 配置？
- **问题**：API Key 是存在 Hermes config.yaml 中，还是由 Electron 单独管理？

## 9. 审批与多模态

- **问题**：Hermes 的审批弹窗默认是 TUI 还是通过 gateway 事件？在 Electron 中如何拦截？
- **问题**：是否支持图片/文件上传？CopilotKit 和 Hermes 分别支持到什么程度？

## 10. 测试策略

- **问题**：如何在没有 Agnes API Key 的情况下做单元测试？
- **问题**：Hermes skill 是否有本地测试机制？
- **问题**：Electron 前端的端到端测试用什么工具？

---

## 更新规则

- 每解决一个问题，将对应条目移到 `DEV_LOG.md` 或 SPEC 的相关章节，并在此标注「已解决」。
- 遇到新问题，随时追加到本文档。
