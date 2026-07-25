# Abcyesno 后续补齐规划

> 本文件依据 `UI_UX_SPEC.md`、`PRD.md`、`SPEC.md`、`ROADMAP.md`、`ACCEPTANCE.md` 以及当前运行反馈整理，按优先级排序。

## 当前状态（2026-07-13）

- 便携版构建成功：`release/Abcyesno 1.3.0.exe` / `release/win-unpacked/Abcyesno.exe`。
- 后端核心链路（Hermes fork、AG-UI bridge、gateway、session、interrupt）已修复。
- 前端基础聊天、session/assistant 管理、错误提示、代码复制已实现。
- 仍缺少大量 UI/UX 细节与功能闭环，详见下方阶段。

---

## Phase 1：消除明显断裂与安全隐患（立即执行）

目标：让用户不再看到无响应按钮和误操作风险。

- [x] **审批期间禁用输入并显示等待状态**
  - `ApprovalDialog` 弹出时，`Composer` 变为禁用，输入区显示“等待用户确认…”。
  - `ChatLayout` 顶部显示黄色审批等待横幅。
- [x] **侧边栏底部按钮接入实际功能**
  - `技能`：打开/关闭 `SkillPanel`。
  - `设置`：打开 API Key 设置弹窗。
  - `市场`：打开新 `MarketPanel`，列出已注册 skill。
- [x] **删除无法运行的死代码组件**
  - 已确认并删除 `BrowserPanel.jsx`、`ToolEvent.jsx`、`ChatBubble.jsx`、`InputBar.jsx`。
- [x] **消息重试 / 重新生成**
  - 最后一条 user 消息显示「重试」按钮。
  - 最后一条 assistant 消息显示「重新生成」按钮。

## Phase 2：UI/UX 与规范对齐

目标：让界面基本符合 `UI_UX_SPEC.md` 描述。

- [x] **模型选择器位置**
  - 模型选择器已从 `ChatHeader` 移到 `Composer` 左侧工具栏。
  - 自定义模型输入 popover 保留。
- [x] **创建助手时支持头像/图标选择**
  - `CreateAssistantModal` 增加 8 个 emoji 头像预设。
  - `Sidebar` 与 `MessageThread` 已使用 `assistant.avatar`。
- [ ] **侧边栏助手状态点按助手区分**
  - 全局连接状态保留在顶部。
  - 助手状态点区分待后续根据 skill 可用性实现。
- [x] **消息图片点击放大**
  - `MessageThread` 中 Markdown 图片点击打开 lightbox，支持 ESC/点击关闭。
- [x] **上下文菜单补全「查看详情」**
  - 新增 `DetailModal.jsx`，右键菜单可查看助手/会话详情。

## Phase 3：功能补齐

目标：把 TODO/PRD 中未完成的端到端能力跑通。

- [ ] **文件上传真正生效**
  - 当前 `uploadFile` 只是把文件复制到 `HERMES_HOME/uploads/<sessionId>/`。
  - 方案：在发送消息时把 `localPath` 作为消息附件元数据传给 AG-UI bridge；bridge 在调用 `prompt.submit` 时附带文件路径或调用 `read_file` 工具读取内容。
- [x] **API Key 校验**
  - `electron/main.js` 新增 `validate-api-key` IPC，调用 Agnes `/models` 验证 key。
  - `ApiKeyModal` 保存前验证，失败时不关闭弹窗并显示错误。
- [ ] **技能市场 / Skill 启用禁用**
  - 市场面板列出已注册 skill，支持启用/禁用，并写入助手 `config`。
  - 禁用的 skill 不在 `SkillPanel` 和路由中显示。
- [x] **设置抽屉完整化**
  - 新增 `SettingsPanel.jsx`：API Key 状态、默认模型、主题切换、打开数据目录。
  - 侧边栏 `设置` 按钮打开设置面板。
- [x] **始终允许此类操作**
  - `App.jsx` 收到 `approval-request` 时检查 `localStorage`；已允许的 operation 自动批准。
- [x] **主助手主动委托给专门 agent**
  - `agui-server.js` 在 `通用助手` 会话中检测到视频/剪映/manju 类请求时，自动在 prompt 中要求模型调用 `langgraph_agent` 委托给 `manju_craft`。
  - 委托调用会作为工具卡片显示在前端。

## Phase 4：端到端验证与验收

目标：在有效 Agnes API Key 和干净环境下跑通 `ACCEPTANCE.md`。

- [ ] 通用助手对话：「你好」能收到非空回复。
- [ ] 工具调用：「列出当前目录文件」触发 terminal/read_file。
- [ ] 审批流程：危险操作触发弹窗，批准/拒绝后流程正确。
- [ ] Manju Craft：选中助手并发送视频脚本，真实触发 `langgraph_agent` 工作流。
- [ ] 干净环境测试：复制 `release/` 到未安装 Hermes/Python 的 Windows 机器双击运行。
- [ ] 更新 `DEV_LOG.md` 与 `ACCEPTANCE.md` 最终状态。

## Phase 5：性能与体积优化（可选）

- [ ] 评估 embedded Python 或精简 `.venv` 依赖，降低 portable 单文件体积。
- [ ] 启用 asar 并正确 unpack 需要外部访问的文件。
- [ ] 优化 Vite chunk 拆分，减少首屏加载时间。

---

## 建议执行顺序

1. 先完成 **Phase 1**，消除用户最容易感知到的断裂按钮和安全隐患。
2. 再做 **Phase 2** 的模型选择器和头像，这两项改动小、视觉效果明显。
3. 然后进入 **Phase 3** 的文件上传、设置抽屉、API Key 校验等功能补齐。
4. 最后 **Phase 4** 验收，根据实测结果回修。
