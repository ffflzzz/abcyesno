# Abcyesno - UI/UX 设计规范（v2，taste-skill 对齐版）

> 本规范基于 `taste-skill`（anti-slop frontend）的方法论重写在原 `UI_UX_SPEC.md` 之上：明确「设计判读」、用三旋钮驱动所有视觉决策、采用 Primer 对齐的设计令牌、列出反「AI 味」硬禁令、补齐可访问性与性能护栏，并以 Pre-Flight Check 作为交付闸门。

## 0. Design Read（设计判读）

**一句话判读**：把这个产品读作「面向开发者 / 技术用户的 GitHub 风格桌面 Agent 客户端，深色优先、信任优先、信息密度偏高的工具型界面」，基底走 GitHub Primer 设计语言（深色令牌 + 浅色对等），克制动效。

判读依据：

- **产品类型**：桌面 Agent 客户端（Electron + React），不是营销落地页。因此 taste-skill 里「英雄区标签 / 滚动提示 / 城市天气条 / 装饰点」等落地页专属 Tell 在本产品中 N/A，但「令牌化、无纯黑纯白、无霓虹辉光、无 Inter 默认、无 em-dash、尊重 reduced-motion、真实状态、无占位假名」等核心纪律全部适用。
- **受众**：技术买家 / 开发者 / 想自助搭 Agent 的用户。审美由受众决定，不是由模型默认决定。
- **约束覆盖审美**：作为本地工具，稳定性、可读性、低干扰优先于炫技动效。

本规范所有旋钮与禁令都从这一判读推导，不靠模型默认审美。

## 1. 三旋钮（The Three Dials）

任何布局、动效、密度决策都受这三个旋钮约束。基准值 `8/6/4`，本产品按判读覆盖为：

| 旋钮 | 取值 | 含义 | 本产品取值理由 |
|------|------|------|----------------|
| `DESIGN_VARIANCE` | 1-10（对称→混沌） | 布局非对称程度 | **4**：可预测、对齐、工具型。侧边栏 + 聊天区是稳定双栏，不玩错落。 |
| `MOTION_INTENSITY` | 1-10（静态→电影） | 动效强度 | **4**：仅流体 CSS 过渡（hover/active/入场淡入）。所有 `>3` 的动效必须尊重 `prefers-reduced-motion`。 |
| `VISUAL_DENSITY` | 1-10（留白→座舱） | 信息密度 | **7**：座舱级。聊天 + 助手列表 + 工具卡片 + 状态点，间距紧凑但靠 1px 分隔线而非卡片盒子。 |

旋钮推断映射（节选自 taste-skill）：

| 信号 | VARIANCE | MOTION | DENSITY |
|------|----------|--------|---------|
| 信任优先 / 工具型 / 可访问性关键 | 3-4 | 2-3 | 4-5 |
| 开发者作品集 | 6 | 5 | 4 |
| 改版-保留 | 沿用 | +1 | 沿用 |

覆盖旋钮请走对话确认，不要改本文件。

## 2. 设计系统基底（Design System）

**选 Primer 对齐令牌系统**（GitHub 风格 devtool UI），理由：本产品现有调色板（`#0f1419` / `#e6edf3` / `#1f6feb`）本身就是 GitHub 深色画布，Primer 是官方、可访问性已做好的 devtool 设计系统。

- **诚实规则**：若判读命中某官方系统，就用官方包或官方令牌，不要手写它的 CSS、也不要只借令牌却覆盖 90%。
- **一个系统原则**：整棵树只用一个系统。不要在 Product 里混 Fluent + Carbon，也不要把 shadcn 组件塞进 Primer 应用。
- 本项目为 React + Vite（非 Next），组件全自定义，故采用「Primer 令牌值 + 自管 CSS 变量」实现，不引 `@primer/react` 全量（避免包体膨胀），但令牌命名与取值对齐 Primer，便于团队对齐心智。

## 3. 设计令牌（Design Tokens）

**令牌策略（二选一，本项目选 CSS 变量）**：用语义令牌定义颜色，在 `[data-theme="dark"]` 与 `[data-theme="light"]` 下切换取值。禁止在组件里写死 hex。

**深色（默认）令牌** 对齐 Primer Dark：

| 令牌 | 取值 | 用途 |
|------|------|------|
| `--bg-base` | `#0d1117` | 应用主背景（非纯黑） |
| `--bg-elevated` | `#161b22` | 卡片 / 气泡 / 浮层 |
| `--bg-inset` | `#010409` | 输入框 / 代码块内凹 |
| `--border` | `#21262d` | 分隔线 / 边框 |
| `--border-strong` | `#30363d` | 强调边框 / hover 边框 |
| `--text-primary` | `#e6edf3` | 主文字 |
| `--text-secondary` | `#8b949e` | 次要文字 |
| `--text-muted` | `#6e7681` | 占位 / 禁用 |
| `--accent` | `#1f6feb` | 强调色 / 主操作 / 用户气泡 |
| `--accent-hover` | `#388bfd` | 强调 hover |
| `--success` | `#238636` | 成功 / 在线 |
| `--warning` | `#d29922` | 警告 / 思考中 |
| `--error` | `#da3633` | 错误 / 离线 / 危险操作 |
| `--shadow-elevated` | `0 8px 24px rgba(1,4,9,0.6)` | 浮层阴影（内描边 + 微着色，非外发光） |

**浅色（对等）令牌** 对齐 Primer Light：

| 令牌 | 取值 |
|------|------|
| `--bg-base` | `#f6f8fa` |
| `--bg-elevated` | `#ffffff` |
| `--bg-inset` | `#eff2f5` |
| `--border` | `#d0d7de` |
| `--border-strong` | `#afb8c1` |
| `--text-primary` | `#1f2328` |
| `--text-secondary` | `#656d76` |
| `--text-muted` | `#6e7781` |
| `--accent` | `#0969da` |
| `--accent-hover` | `#0860ca` |
| `--success` | `#1a7f37` |
| `--warning` | `#9a6700` |
| `--error` | `#cf222e` |

**对比度纪律**：正文文本 WCAG AA（4.5:1）起步，大标题 AAA（7:1）目标。同一 CTA 在浅色和深色下都要跳得出来（层级对等）。**禁止纯 `#000000` 与纯 `#ffffff` 作大面积底色**，用 off-black / off-white。

## 4. 反「AI 味」禁令（AI Tells / Forbidden Patterns）

下列签名除非判读明确要求，否则禁用。这是 Pre-Flight 的硬闸门。

### 4.1 视觉 / CSS
- 默认**无霓虹 / 外发光**。用内描边或微着色阴影表达强调。
- **无纯黑 `#000000`**，用 off-black（zinc-950 / `#0d1117`）。
- **无过饱和强调色**，强调色需与中性色融合。
- **无大标题渐变文字**。
- **无自定义鼠标光标**（可访问性与性能双害）。

### 4.2 字体
- **避免 Inter 作默认字体**。本项目用系统中文字体栈（见 §7）+ 等宽英文栈，不引入 Inter。
- 不用「只会吼」的超大 H1；用字重 + 颜色控制层级，而非裸字号。
- 衬线仅用于编辑 / 出版物语境，不用于仪表盘。

### 4.3 布局 / 间距
- 不用「三等分相同卡片」横排（feature 三连卡已禁用）。改用非对称栅格、左右错位、水平滚动态。
- 不用 flex 百分比数学（`w-[calc(33%-1rem)]`），一律 CSS Grid。
- 不用数学上「完美」却尴尬留白的间距。

### 4.4 内容 / 数据（「Jane Doe」效应）
- **无通用占位名**（John Doe / Sarah Chan / 小明）。示例用可信、本地化、有质感的人名。
- **无通用头像**（Lucide user 图标 / 鸡蛋头）。用真实照片占位或明确风格的 emoji 头像（本项目用 8 个 emoji 预设）。
- **无假完美数字**（`99.99%` / `1234567`）。用有机数据（`47.2%` / `+86 138 0012`）。
- **无创业口水品牌名**（Acme / Nexus / 云帆）。示例用像真的语境化名称。
- **无填充动词**（赋能 / 无缝 / 颠覆 / 下一代）。只用具体动词。

### 4.5 外部资源 / 组件
- **不手搓 SVG 图标**。图标统一用 Phosphor / HugeIcons / Radix / Tabler 之一，全局统一 `strokeWidth`。本项目当前用 Phosphor 对齐此规则。
- 不用 div 拼假截图 / 假终端模拟产品 UI。
- 不引断裂的外链图片；用真实资源或明确占位。

### 4.6 em-dash 硬禁令
**em-dash（`-`）与 en-dash（`-`）作为分隔符一律禁用**。标题、眉标、胶囊、正文、引用、署名、图注、按钮、alt 文本里出现任意一处即判不通过。改用句号、逗号、括号、冒号或普通连字符 `-`。日期与数字范围用连字符（`2018-2026`、`40-80k`）。此规则不可协商。

### 4.7 状态点的例外
装饰性彩色状态点默认禁用。本项目**允许**状态点仅当它表达**真实语义状态**（后端连接：绿=就绪 / 黄=连接中 / 灰=未就绪；审批待处理），且每屏节用。禁止在普通列表 / 导航每项的装饰点。

## 5. 可访问性与性能护栏（A11y & Perf Guardrails）

- **Reduced Motion（强制）**：任何 `MOTION_INTENSITY > 3` 的动效必须尊重 `prefers-reduced-motion`；无限循环、视差、滚动劫持、磁吸物理在 reduced-motion 下塌缩为静态 / 即时。本项目 MOTION=4，过渡类动效需包 `@media (prefers-reduced-motion: reduce)` 关闭块。
- **深色 / 浅色双模（强制）**：从一开始为两种模式设计，默认跟随系统 `prefers-color-scheme`，并提供手动切换。禁止只发单模。两种模式下视觉层级与品牌识别都要成立。
- **Core Web Vitals**：LCP < 2.5s，INP < 200ms，CLS < 0.1。交付前跑 Lighthouse（桌面应用指首屏渲染与交互延迟）。
- **DOM 成本**：颗粒 / 噪点滤镜只放 `fixed inset-0 pointer-events-none` 伪元素，绝不放滚动容器。非首屏重组件懒加载。
- **Z-Index 节制**：不乱撒 `z-50` / `z-10`。在项目常量里定义分层刻度（如：sticky 栏 100 / 浮层 200 / 模态 300 / Toast 400），全树复用。

## 6. 布局纪律（Layout Discipline）

- **栅格优先**：布局用 CSS Grid（`grid grid-cols-[260px_1fr]`），禁止 flex 百分比数学。
- **视口稳定**：全高区用 `min-h-[100dvh]` / `100%` 伸缩，不用 `h-screen`（移动端 iOS 地址栏跳动）。
- **容器约束**：聊天内容区 `max-w-[1100px] mx-auto`，长文不顶边。
- **断点**：`sm 640 / md 768 / lg 1024 / xl 1280`。侧边栏在 `<768` 折叠为图标栏或抽屉。
- **形状一致锁**：全树一套圆角系统（气泡 12px / 卡片 8px / 按钮 6px），不混用多套。
- **颜色一致锁**：全树一个强调色，所有 CTA 用同一 `--accent`。

## 7. 字体与排版

- 中文：`"Microsoft YaHei", "PingFang SC", "Noto Sans SC", system-ui`。
- 英文 / 代码：`"JetBrains Mono", "SFMono-Regular", Consolas, monospace`（代码、数字、会话 ID 用等宽）。
- 正文 14px，小字 12px，标题 16-18px，大标题靠字重与颜色而非裸放大。
- `font-display: swap`，自托管字体，生产环境不 `<link>` Google Fonts。

## 8. 整体布局

```
┌──────────────────────────────────────────────────────────────────┐
│  ┌────────────┐  ┌──────────────────────────────────────────────┐ │
│  │            │  │ Topbar: 助手名 | 状态点 | 模型选择 | 新会话 | 设置 │ │
│  │  Sidebar   │  ├──────────────────────────────────────────────┤ │
│  │  搜索       │  │                                              │ │
│  │  + 助手     │  │              MessageThread                   │ │
│  │  助手列表    │  │  User ▸ 你好                                 │ │
│  │   状态点     │  │  Assistant ◂ 你好！有什么可以帮你？          │ │
│  │   头像/emoji │  │  ToolCard: langgraph_agent ▸ 执行中…        │ │
│  │  会话列表    │  │                                              │ │
│  │  底部:市场   │  ├──────────────────────────────────────────────┤ │
│  │       技能   │  │ Composer: 新会话 | 模型 | 上传 | 技能 | 输入 | 发送 │ │
│  │       设置   │  └──────────────────────────────────────────────┘ │
│  └────────────┘  └──────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

## 9. 组件规范

通用：所有交互元素有 hover / active / focus / disabled / loading 五态（见 §10 令牌）。图标统一 Phosphor。动效仅 `transform` + `opacity`，`will-change` 仅在真动画元素上。

### 9.1 侧边栏 `Sidebar`
- 顶部：搜索输入框 + 「+ 助手」按钮（图标，非文字堆）。
- 助手列表：emoji 头像（8 预设之一）、名称、真实语义状态点（绿/黄/灰）。选中项用 `--bg-elevated` 高亮 + 左侧 2px `--accent` 条。
- 会话列表：选中助手后展开，显示标题（自动摘要，可改）、末条预览、相对时间。
- 底部固定入口：市场 / 技能 / 设置（图标 + 文字，单列，不高亮装饰点）。
- 右键菜单：重命名 / 删除 / 详情。

### 9.2 聊天区 `ChatLayout`
- **Topbar**：左助手名 + 真实状态点；中模型选择器；右新会话 + 设置。状态点按 `hermesReady` / `gatewayConnected` 显示（绿=已连 / 黄=连接中 / 灰=未就绪）。
- **MessageThread**：用户气泡右侧 `--accent` 底白字；助手气泡左侧 `--bg-elevated` + 头像；Markdown 渲染；代码块自带复制按钮；工具调用为 `ToolCard`（见 9.7）；图片缩略图点击放大（lightbox，ESC / 点击关闭）。新消息平滑滚到底。
- **Composer**：左侧工具栏（新会话 / 模型 / 上传 / 技能），中间多行输入（`Enter` 发送、`Shift+Enter` 换行、支持拖拽与粘贴图片），右侧发送 / 停止（加载中切换）。审批弹出时禁用输入并切换 placeholder。

### 9.3 模型选择器 `ModelSelector`
- 下拉列出可用模型（名称 + 描述 + 速度/价格标签），含「自定义模型…」入口（弹窗输入任意 model id 并存入助手配置）。选择影响当前会话调用模型。

### 9.4 技能面板 `SkillPanel`
- 点「技能」弹出，列出当前助手可用技能/工具，点击插入触发指令或打开参数面板。市场面板 `MarketPanel` 的启用/禁用开关需持久化到助手配置（见 §13 待办）。

### 9.5 审批弹窗 `ApprovalDialog`
- 后端触发审批时弹出，显示：操作类型、`tool_name` / `tool_call_id`、描述、影响范围。按钮：批准 / 拒绝 / 始终允许此类操作（记忆写入 `localStorage: abcyesno:allowedOps`）。危险操作用 `--error` 描边强调。

### 9.6 设置面板 `SettingsPanel`
- API Key 状态（保存前调用 `validate-api-key` 校验，失败保持弹窗开启）、默认模型、主题切换（深/浅/跟随系统）、打开数据目录（`shell.openPath` 到 `%USERPROFILE%/.hermes_portable_data`）。

### 9.7 工具卡片 `ToolCard`
- 展示工具名、入参摘要、状态（执行中 spinner / 成功对勾 / 失败叉）。状态用令牌色，不用彩色装饰点堆叠。无 `tool_call_id` 时用稳定 `tool-${toolName}` 匹配 START/END。

### 9.8 空 / 加载 / 错误态（强制）
- 首启动无助手 / 无会话：空态插画 + 引导文案（非假截图）。
- 后端未就绪：Bootstrap 启动画面 + 「启动中…」「连接中…」状态。
- 错误：`ChatLayout` 顶部可关闭红色横幅 + 错误气泡（如 401）。无未捕获异常静默失败。

## 10. 交互状态令牌

| 状态 | 背景 | 文字 | 边框 | 表现 |
|------|------|------|------|------|
| 默认 | `--bg-elevated` | `--text-primary` | `--border` | 静态 |
| hover | `--bg-elevated` | `--text-primary` | `--border-strong` | 边框变亮 + 轻微提亮 |
| active | `--bg-inset` | `--text-primary` | `--accent` | 内凹 |
| focus | `--bg-elevated` | `--text-primary` | `--accent` | 2px 焦点环（可见，非纯装饰） |
| disabled | `--bg-base` | `--text-muted` | `--border` | 不可点 |
| loading | `--bg-elevated` | `--text-secondary` | `--border` | spinner / 停止按钮 |

CTA（发送 / 批准）：`--accent` 底 + 白字；hover `--accent-hover`。文字对比度过 WCAG AA。

## 11. CopilotKit 方案

### 11.1 为什么用
提供聊天状态管理、流式响应、工具调用、会话上下文；支持自定义 UI，不强制默认组件。与 AG-UI runtime 配合对接 Hermes 后端。

### 11.2 用法（已定）
- `App` 用 `CopilotKit` 包装，runtimeUrl 指向本地 AG-UI Bridge：`http://127.0.0.1:${aguiPort}/api/ag-ui/run`。
- 用 `useCopilotChat()` 取 `visibleMessages` / `appendMessage` / `sendMessage`，**完全自定义** `MessageThread` 渲染（不依赖 `CopilotChat` 默认 UI）。
- 切换助手时通过 `runtimeBody={{ assistantId, skillId }}` 路由到对应 Hermes skill，并重置 CopilotKit 会话上下文。
- 需用户确认的工具（terminal / browser / 危险写操作）用 `useCopilotAction` 注册审批。

## 12. 数据模型

```ts
interface Assistant {
  id: string;
  name: string;
  avatar: string;          // 8 个 emoji 预设之一，或自定义上传路径
  skillId: string;         // 关联 Hermes skill / LangGraph agent
  description: string;
  capabilities: string[];
  defaultModel: string;
  modelOverrides?: Record<string, string>; // 按会话覆盖
  config?: Record<string, any>;
}

interface Session {
  id: string;
  assistantId: string;
  title: string;           // 自动摘要，用户可改
  preview: string;
  updatedAt: number;
  messages: Message[];
}

interface Message {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  attachments?: Attachment[];
  toolCalls?: ToolCall[];
  createdAt: number;
}

interface ToolCall {
  id: string;              // 无 tool_call_id 时回退 tool-${toolName}
  name: string;
  args: Record<string, unknown>;
  status: "running" | "success" | "failed";
}
```

## 13. 待确认事项（本版已决议）

1. 是否自定义消息渲染：**是**，完全自定义（已在 §11.2 落地）。
2. 助手头像自定义上传：**是**，支持上传 + 8 emoji 预设（已实现）。
3. 模型选择器作用域：**按助手设默认，按会话可覆盖**（自定义模型入口已实现）。
4. 多模态输入：图片拖拽 / 粘贴已实现；**文件上传需真正传给 Hermes**（已知缺口，见 ACCEPTANCE §8.6）。
5. 会话标题：自动摘要 + 用户可重命名（已实现）。
6. 主题：深 / 浅 / 跟随系统三态（设置面板占位，需接 `data-theme` 切换）。

## 14. Pre-Flight Check（交付闸门）

交付任何 UI 改动前逐条过，任一项不达标即未完工：

- [ ] 设计判读已声明（§0 一句话）？
- [ ] 三旋钮取值明确且由判读推导，非静默用基准？
- [ ] 设计系统选定（Primer 对齐），全树只有一个系统？
- [ ] **零 em-dash（`-` / `-`）** 出现在任何可见文案（标题/眉标/按钮/气泡/图注）？
- [ ] 主题锁：整树一个主题策略（深/浅/跟随），不中途反色？
- [ ] 颜色一致锁：全树一个 `--accent`？
- [ ] 形状一致锁：一套圆角系统？
- [ ] 按钮对比度：每个 CTA 文字在底色上过 WCAG AA？
- [ ] 表单对比度：输入/占位/焦点环/标签均过 AA？
- [ ] 无 AI 味（§4）：无霓虹外发光、无纯黑、无过饱和、无 Inter 默认、无三等分卡、无 Jane Doe / Acme / 填充动词？
- [ ] 状态点仅用于真实语义状态（§4.7）？
- [ ] Reduced Motion：所有 `>3` 动效包 `prefers-reduced-motion`？
- [ ] 深色 / 浅色双模令牌定义且两模都测过？
- [ ] 空 / 加载 / 错误态齐备（§9.8）？
- [ ] 图标来自允许库（Phosphor），无手搓 SVG 路径？
- [ ] 长列表（>5 项）用对组件，非默认 `divide-y` 全行描边？
- [ ] 视口稳定：`min-h-[100dvh]`，无 `h-screen`？
- [ ] Core Web Vitals  plausible（LCP<2.5s / INP<200ms / CLS<0.1）？
- [ ] 无 `window.addEventListener('scroll')` 劫持？

## 15. 验收映射

UI/UX 符合性验收项见 `ACCEPTANCE.md` 的「Phase 8 - UI/UX 规范符合性」。本规范的 Pre-Flight（§14）即该阶段的通过闸门。

---

**结论**：以「GitHub Primer 对齐的深色工具型客户端」为判读，用三旋钮 + 语义令牌 + 反 AI 味禁令 + 可访问性护栏统一驱动所有界面决策，复用 CopilotKit runtime 但全自定义 UI 组件。
