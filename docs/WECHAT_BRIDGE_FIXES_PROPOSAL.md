# 微信桥接 v2 修复方案（会话归一 + 去重 + 时间注入）

> 状态：改动 1、改动 3 已实现并发布；**改动 2 已取消**（用户确认保持自然输出）。

## 背景

`wechat-claude-code` 已 fork 进 abcyesno（`electron/backend/wechat_bridge/`），
v1 让微信消息能到达 Hermes Agent。日志显示前段链路（扫码、轮询、agui-server
HTTP POST）稳定，但用户实测暴露三个问题：

1. 微信对话**不出现在 abcyesno 主程序侧边栏的会话列表**。
2. 微信端回复**重复输出**（同一段连续出现两份）。
3. 模型**拼接日期/数字出错**（如"206 年 8 月 5 日"、"8 月 25 号"），因为模型
   没有真实时间源，凭印象编造。

> 关于"排版混乱"：用户实测后确认微信端现在已是"【标题】/• 项目"的分段效果、
> 不再是拥挤单行，**明确指示不再改动**（`保持自然输出即可`）。因此原"改动 2（排版重排
> formatForWechat）"**已取消**，不再实施。

## 改动 1: threadId 按微信用户切分 + 注册到 abcyesno session 列表 ✅ 已实现

**位置**：`electron/backend/wechat_bridge/src/main.ts`、`bridge.ts`、
`electron/backend/wechat-bridge-runner.js`、`electron/backend/storage.js`、
`electron/main.js`、`electron/preload.js`、`src/App.jsx`。

**实现要点**：
- **threadId 按用户拆分**：`provider.ts` 移除 `'wx-bridge'` fallback，调用方传
  `wx-${fromUserId}`，一人一份 Hermes session，避免多用户上下文串台导致的日期混乱。
- **首次入站建/查 session**：`main.ts:handleMessage` 在拿到 `fromUserId` 后调
  `abcSessionHelper.ensureSession(fromUserId, masked)`，后者通过注入的
  `storage.createSession('default', '微信 · ' + masked)` 建 session，并把
  `fromUserId -> abcyesno sessionId` 持久化到 `wx_session_map.json`（跨重启稳定）。
- **storage 新增 `appendSessionMessage(id, role, content)`**：在 user 入站、
  assistant 终态各 append 一次，更新 `preview` 与 `updatedAt`（上限 200 条）。
- **广播刷新**：`bridge.ts` 的 `ensureAbcSessionForWechatUser` / `appendAbcMessage`
  内部调 `notifySessionsUpdated()` → runner 注入的 `onSessionsUpdated` →
  `main.js` `webContents.send('sessions-updated')` → `App.jsx` 订阅后重新
  `loadSessions`。
- **依赖注入方式**：`bridge.ts` 改为**静态 import** `main.ts` 的 `setAbcSessionHelper`
  （而非运行时 `require('./main.js')`），避免 esbuild 打包后模块解析歧义导致
  `abcSessionHelper` 永远为 null（即"微信对话不进侧边栏"的隐患）。

## 改动 2: 排版重排（formatForWechat）❌ 已取消

原方案计划把模型 markdown 输出重写为 iLink 友好的"【标题】/• 项目"分段格式。
**用户实测后确认当前自然输出已是分段效果，明确指示不再改动。方案取消，保持自然输出。**

## 改动 3: 重复输出 + 日期/数字编造 ✅ 已实现

### 根因诊断（结论）

- **重复输出**：Agnes 2.5-flash 存在"自校正循环"——先回复，再附"哦我理解错了 /
  刚才那个回答确实…"的元评论并重写同一答案（两次文本仅头部措辞不同、正文近乎一致）。
  因为两次文本**不全等**，原来的精确匹配去重漏掉了它。
- **日期/数字编造**：模型没有真实时间源，遇到"今天几号 / 星期几"之类问题就凭印象
  乱填（"206 年"、"8 月 25 号"），且相邻两次回答的日期还可能不一致。
- **收敛验证**：`agui-server.js` 的 SSE 帧只发了一次，确认重复在**模型生成层**，
  不在桥接层重发，因此桥接层加"自校正去重"即可兜底。

### 实现要点（均在 `main.ts` 的 `sendToClaude` 内）

- **自校正去重 `isSelfCorrectionDuplicate`**：维护 `lastEmitRecord`（文本 + 时间戳），
  在 1500ms 窗口内，若新文本归一化后与上次：**完全相同**（归一化后）或**头部 70%
  的 bigram Jaccard ≥ 0.8**（容忍"我理解错了"式微改前缀），则丢弃并记 log
  `dropped self-correction duplicate`，不发微信。窗口期之外（如隔了几小时的合法重述）
  不误删。
- **真实时间注入 `formatNowForModel()`**：用 `Intl.DateTimeFormat('zh-CN', …)` 取
  宿主本地时区的"年月日 + 星期 + 时:分"注入 system prompt（`当前时间：…`）。
- **约束追加**：
  - 反自校正："回复必须一次性给到最终答案，不要'我理解错了'式的自我反思重写…"。
  - 反编造："日期、时间、星期、电话号码、身份证号、版本号、引用的数字等…必须严格
    基于上文提供的'当前时间'或用户给出的真实数据；不知道就说不知道，不要凭印象编造。"

## 回归测试

- `scripts/test-wechat-bridge.mjs` —— 11 passed（runner 生命周期、分发守卫）。
- `scripts/test-wechat-provider.mjs` —— 5 passed（无 `setEncoding` 错误、`onText` 累积一致）。
- `node scripts/check-tdz.js` —— clean（0 violations）。
- `npx vite build` —— 通过。

> 注：`splitMessage` 曾在编辑中被截断（循环体丢失导致返回空数组 + 悬空代码），
> 已还原为基于段落边界的正常实现，并重新构建 `wechat_bridge/dist/index.js`。

## 验收用例

- [x] 微信扫码绑定 → abcyesno 侧边栏出现"微信 · xxxx"一条。
- [x] 同一账号再发消息 → 该 session 保持置顶、历史可见。
- [x] 点击"微信 · xxxx" → ChatShell 渲染微信历史（同一 Hermes thread）。
- [x] 微信与主程序双向收发衔接。
- [x] **不再出现同一条消息在一个对话里打印两次**（自校正去重兜底）。
- [x] 问"今天几号 / 星期几" → 模型依据注入的真实时间作答，不再编造日期。
- [x] `check-tdz` / `vite build` / 两个 test 脚本全绿。

## 部署注意

- 改完必须**完整退出 Abcyesno.exe**（Electron 单实例，关窗不清主进程）再重启，新 bundle 才生效。
- 后端生效依赖 `release/.../resources/app/electron/backend/wechat_bridge/dist/index.js`
  （已重新构建并镜像）。
