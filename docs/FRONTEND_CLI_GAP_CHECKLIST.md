# Hermes CLI 有、Electron 前端缺失的 UI 清单

> 比对基准：Hermes 后端的权威契约是 `ui-tui/src/gatewayTypes.ts` + `tui_gateway/server.py`（WebSocket/JSON-RPC 事件流）。
> 前端消费者在 `src/hooks/useAgentStream.js`，只处理了 `RUN_*` / `TEXT_MESSAGE_*` / `TOOL_CALL_*` / 部分 `CUSTOM`（stream.phase、thinking.delta、tool.chunk、workflow.*、ui.render）。
> 分级：P0=会卡死/不可用，P1=重要信息丢失，P2=高级但常用，P3=锦上添花/CLI 专有。

---

## P0 — 缺失的「阻塞式等待用户」交互（触发即永久挂起）

后端把"等用户输入"统一成阻塞原语：`_block()` 发 `{x}.request` 事件并挂起线程，前端必须弹窗回 `{x}.respond` 唤醒。前端只实现了 `clarify` 和 `approval` 两类，**以下三类完全没有**：

| 缺口 | 后端机制 | 后果 |
|------|----------|------|
| **sudo 密码请求** | `sudo.request` / `sudo.respond`（server.py:3730/9792） | agent 需提权时前端无弹窗 → 线程死等 |
| **密钥/环境变量请求** | `secret.request{env_var}` / `secret.respond`（server.py:3737/9797） | 工具要秘值时前端无输入框 → 死等 |
| **终端读取请求** | `terminal.read.request` / `terminal.read.respond`（server.py:3662/9786） | 桌面 GUI read_terminal 工具无回传 → 死等 |

> 注：超时由后端兜底（约 30–120s 后给空答案），但会破坏体验且答案错误。

---

## P1 — 流式事件有发、前端没渲染（信息丢失）

| 缺口 | 后端事件 | 现状 |
|------|----------|------|
| **深度推理流 (reasoning)** | `reasoning.delta` / `reasoning.available` | 前端只处理 `thinking.delta`（浅层思考），**reasoning 不单独渲染** |
| **通用通知横幅** | `notification.show` / `notification.clear` | 前端只有 error 横幅，无 info/warn/sticky 通知层 |
| **状态行更新** | `status.update{kind,text}`（如 compacting） | 前端无状态行组件 |
| **工具进度 / 生成中** | `tool.progress` / `tool.generating` | 前端只消费 `tool.chunk` 终端输出，无独立进度指示 |
| **工具内联 diff** | `tool.complete.inline_diff` | ToolCard 显示 result 文本，未渲染 inline_diff（diff 仅在 ResultPanel「变更」页） |
| **子 agent 实时镜像** | `subagent.spawn_requested/start/thinking/tool/progress/complete` | 完全缺失，多 agent 委派时前端看不到 |
| **MOA 多模型聚合** | `moa.reference` / `moa.aggregating` | 完全缺失 |
| **后台任务完成** | `background.complete` | 缺失（`prompt.background` 启动后无回调 UI） |
| **评审摘要** | `review.summary` | 缺失 |

---

## P1 — 成本 / 账单（前端只有估算）

| 缺口 | 后端机制 | 现状 |
|------|----------|------|
| **真实 token / cost** | `message.complete.usage` + `session.usage`（cost_usd/input/output/cache_*） | 前端 `ContextUsage` 是**字符估算**（注释明示非真实值），未接后端 usage |
| **上下文分解** | `session.context_breakdown` | 缺失 |
| **账单 / 信用额** | `credits.view` / `billing.state/charge` | 缺失 |
| **账单设备流验证** | `billing.step_up.verification{verification_url,user_code}` | 缺失（CLI 弹出验证页） |
| **费用护栏错误** | `BillingErrorPayload{remainingUsd,...}` | 缺失 |

---

## P2 — 会话高级操作（CLI 有，前端无）

| 缺口 | 后端 RPC | 现状 |
|------|----------|------|
| **通用 steer（等待态注入补充指令）** | `session.steer` | 仅 ApprovalBubble 内有 steer 输入，无全局「生成中补充指令」入口 |
| **撤销 / 压缩上下文** | `session.undo` / `session.compress` | 缺失（压缩提示也无） |
| **分支会话** | `session.branch` | 缺失 |
| **会话历史** | `session.history` | 缺失 |
| **cwd / 项目树** | `session.cwd.set` / `projects.tree` | 缺失 |

---

## P2 — 终端 / 进程（前端无）

| 缺口 | 后端机制 | 现状 |
|------|----------|------|
| **终端输出流标签页** | `agent.terminal.output`（实时推送） | 前端无终端 tab |
| **终端尺寸通知** | `terminal.resize` | 缺失 |
| **细粒度停命令** | `process.stop` / `process.list` / `process.kill` | 前端只有整轮 Stop，**不能单独停某个长命令** |

---

## P2 — 模型 / 工具集 / MCP 管理

| 缺口 | 后端机制 | 现状 |
|------|----------|------|
| **完整模型选择器** | `model.options`（按 provider 分组 + 认证态 + is_current） | 前端 Composer 只有简单下拉选 `agnes-2.5-flash`/自定义 ID |
| **保存 key / 断开** | `model.save_key` / `model.disconnect` | 缺失 |
| **工具配置 UI** | `tools.list` / `tools.show` / `tools.configure` | 缺失 |
| **工具集 / MCP 重载** | `toolsets.list` / `reload.mcp` / `reload.env` | 缺失 |
| **浏览器进度事件** | `browser.progress` | BrowserPanel 有，但未接该事件流 |

---

## P3 — 语音 / 附件高级

| 缺口 | 后端机制 | 现状 |
|------|----------|------|
| **TTS 响应朗读** | `voice.tts` / `voice.status` | 前端 Composer 有 STT 录音输入，但**无响应语音播报** |
| **完整 voice mode** | `voice.toggle` / `voice.record` / `voice.transcript` | 缺失 |
| **clipboard.paste RPC** | `clipboard.paste` | 前端用本地粘贴处理，未走后端 token 估算 |
| **附件 RPC** | `image.attach` / `file.attach` / `pdf.attach` / `image.detach` | 前端本地还原，未走网关 attach（结果一致但绕过了后端估算/持久化） |

---

## P3 — 斜杠命令系统

| 缺口 | 后端机制 | 现状 |
|------|----------|------|
| **命令目录 + 自动补全** | `commands.catalog` / `complete.slash` / `complete.path` | 前端 Composer 无 `/` 菜单 |
| **命令解析/分发** | `command.resolve` / `command.dispatch` / `slash.exec` | 缺失（技能运行走 SkillPanel，非统一 slash 管线） |

---

## P3 — CLI 专有 novelty（一般不需要，但确属「CLI 有前端无」）

| 缺口 | 说明 |
|------|------|
| **Pet 头像系统** | `pet.*` 全套（生成/孵化/画廊/选择），CLI/TUI 大型特性 |
| **插件中心** | `plugins.list` / `plugins.manage` |
| **学习图谱** | `learning.frames/detail/delete/edit` |
| **回滚检查点** | `rollback.list/restore/diff` |
| **handoff 交接** | `handoff.request/state/fail` |
| **agents.list / insights** | 缺失 |
| **config 显示开关** | `show_cost/show_reasoning/streaming/inline_diffs/tui_statusbar` 由后端驱动，前端用自己写死的默认值 |
| **全屏 prompt_toolkit 输入** | 前端用 Composer 等价，无需补 |
| **设置向导 / 数字键会话选择器** | 前端有 ApiKeyModal + Sidebar 等价，无需补 |

> 注：项目已**有意剥离 cron**（`docs/STRIPPING_GUIDE.md`），故 `cron.manage` 不算缺口。

---

## 已做（对照确认，避免重复）

✅ approval 门（modal + 内联气泡） ✅ clarify 追问 ✅ 浏览器自动化 ✅ 工具结果卡 ✅ 图片显示 + Lightbox ✅ 流式 token + 思考流 ✅ 技能/工作流选择 + ContractForm + 任务面板 ✅ 消息编辑/删除/重生成/重试/停止 ✅ 语音输入(STT) ✅ @提及 ✅ 模型/权限选择器 ✅ 结果面板(产物/文件/变更diff) ✅ 多会话并发 ✅ 上下文用量估算面板

---

## 建议优先补的顺序

1. **P0 三件套**（sudo / secret / terminal.read 阻塞请求）—— 否则特定工具会卡死，属于正确性 bug。
2. **reasoning.delta + notification + status.update + tool.progress** —— 低成本、高感知的"信息补全"。
3. **真实 token/cost** —— 替换 ContextUsage 估算，接 `message.complete.usage`。
4. **subagent.* / moa.*** —— 多 agent 场景可视化。
5. 其余 P2/P3 按产品优先级排期。
