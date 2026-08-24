# UI 圆角重构方案（Radius Token Refactor）

> Status: 候选方案，等用户拍板
> Owner: UI Designer / lex
> Date: 2026-08-24
> Scope: 全局 `border-radius` 收敛 + 关键硬角落重塑

## 背景

用户反馈（截图）："全局 UI 都一点圆角都没有，太生硬了"。

`src/styles/index.css` 一个文件管了 80+ 处 `border-radius`，数值是
`2 / 4 / 5 / 6 / 8 / 10 / 12 / 13 / 14 / 16 / 50%` 一通乱炖，没有任何 token 化分
级。最戳的几个硬角落：

- 顶部浏览器风格 tab bar：tab 完全方形
- 侧边栏 header（Abcyesno 标题区）
- 「对话 / 任务」切换器
- 「+ 新会话」按钮（0px）
- 底部 chip（默认权限 / 工作空间 / 模型选择）

同时 `--border-radius` token 系统不存在，主题切换时无法联动。

## 设计原则

1. **少即是多**：可用半径只给 4 档（xs / sm / md / lg）+ 1 档全圆（pill），任何零散
   数值都映射到这一档。
2. **半径承载等级**：圆角越大 = 元素越"独立 / 浮起"（容器、卡片）。按钮、输入框
   内同一档，不要为了"好看"超出分级乱加。
3. **不破坏既有功能**：本次纯视觉收敛，DOM 结构、active 判定、TabBar API、tab
   高度（40px）全部保留。仅重写 radius 与必要 padding。
4. **主题联动**：token 进 `:root` / `[data-theme=light]`，未来 dark/light 切换全
   自动跟随。

## 三套方案并列

### 方案 A — 保守统一（最小动作）

只引入 radius token，不改容器形状：

```css
:root {
  --radius-xs: 4px;    /* 极端紧凑：tab-close、checkbox */
  --radius-sm: 8px;    /* 控件：button、input、chip */
  --radius-md: 12px;   /* 卡片：textarea、modal、bubble、context-menu */
  --radius-lg: 16px;   /* 大容器：session-item、message-thread card */
  --radius-pill: 9999px;
}
```

替换映射（节选）：

| 选择器 | 现状 | 改后 |
|---|---|---|
| `.tabbar-tab-close` | 4px | xs |
| `.tabbar-add` | 6px | sm |
| `.sidebar-search input` | 6px | sm |
| `.add-btn`（+ 新会话） | 6px | sm |
| `.session-item` | 8px | sm 或 lg（见下） |
| `.workflow-item` | 8px | sm |
| `.header-icon`（对话/任务） | 6px | sm |
| `.composer-btn` | 8px | sm |
| `.composer textarea` | 12px | md |
| `.composer-send` | 12px | md |
| `.modal` | 12px | md |
| `.context-menu` | 8px | md |
| `.message-bubble`（user） | 14px | md |
| `.context-usage-modal` | 14px | md |
| `.hint` | 16px | lg |
| `.image-chip` | 13px | sm（chip 不是 banner） |
| `.assistant-avatar`、`.status-dot` | 50% | pill |
| `.btc-progress-bar` | 2px | xs |

预估改动量：~50 个 CSS 行级替换。**不改任何 DOM 结构、不改容器 padding、不改布局**。
视觉差异：与现状几乎一致，唯一区别是把 6 / 8 / 13 这种"中间值"统一到最近一档，
所有按钮、输入框、卡片之间半径互相一致。

### 方案 B — 浏览器风格 tab 重塑 + token 统一（推荐）

在 A 的基础上，把浏览器风格 tab bar 改成 **Chrome / Arc 风 active tab**：

```css
.tabbar {
  height: 40px;
  background: var(--panel);
  /* tabbar 自身保持方角，作为标签后面的"墙纸" */
}

.tabbar-tabs {
  display: flex;
  gap: 4px;          /* tab 间留 4px 缝隙，凸显独立感 */
  padding: 6px 0 0;  /* 顶部留 6px 让 active tab "探出" 来 */
  align-items: flex-end;  /* active tab 下沉到 tabbar 底部 */
}

.tabbar-tab {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 12px;
  border-radius: 10px 10px 0 0;   /* 浏览器标签的"上圆下直" */
  background: var(--panel-2);   /* 默认就是稍暗的"未激活" */
  color: var(--muted);
}

.tabbar-tab.active {
  background: var(--bg);  /* active tab 与主区底色融合 */
  color: var(--text);
  /* 去掉 inset 底线阴影 */
  box-shadow: none;
}

.tabbar-tab-close {
  border-radius: var(--radius-xs);  /* 4px */
}

.tabbar-add {
  width: 28px;
  height: 28px;
  margin: 0 0 0 4px;
  align-self: flex-end;       /* 与 tab 顶对齐 */
  margin-bottom: 4px;
  border-radius: var(--radius-sm);
}
```

预期效果（mockup 看 [MOCKUPS.md]）：

- active tab 顶部 10px 圆角，与主区背景融合（让"页面高于标签"的层级立刻读出来）
- inactive tab 用 `--panel-2` 略灰底，不再是 0 信息
- + 按钮变小、下沉，与 tab 行对齐
- tab 高度 40 → 容器不变，内部 padding 6+34=40 仍然完整

其余 token 化与方案 A 一致。

### 方案 C — macOS Aqua 化（最大胆）

最大幅度改：

- tab 全部 pill 化（`radius-pill`），上方留 8px 容器 + 8px 间距
- 所有 `.composer-btn` 变 pill
- 所有 input / textarea 变 `radius-lg: 16px`
- modal / context-menu / approval-detail 升到 `--radius-lg: 16px`
- message-bubble（user）变 `radius-md: 14px` 但保留「右下角小三角」4px
- session-item hover 时再加 4px 形变动画

视觉最柔，但有几个副作用：

1. 信息密度下降（同一屏看到的会话数变少）
2. 与现有「简洁克制」的审美有偏离（你之前的偏好是侧边栏时间用相对日期、图
   片 chip 化、Launcher 极简），过度圆润会显得「过度包装」
3. 改的范围最大，需要交互测试更多

不推荐作为第一轮。

## 推荐路径

**先 B、再视反馈决定要不要继续朝 A 的反向（左）走**：

1. **方案 B 全量落地**（~80 个 css 行级 + tab DOM padding 微调）
2. 跑质量门 + 部署 + 重启给你看
3. 你觉得"还差点"再选 A 或 C 二次调整

## 不动范围（明确写出避免拉扯）

- icon、logo、tab 关闭按钮 hover 红色 — 颜色不动
- 头像 50% 圆形 — pill 不动
- message-bubble `border-bottom-right-radius: 5px`（用户消息右下小尖角）— 这是设计
  意图，保留
- workflow.list 内的 active 状态左边 2px accent 条 — 视觉锚点，保留
- StudioWorkbench 的 `--st-*` token — 独立体系，本次不动，留作第二轮

## 验证

- `node scripts/check-tdz.js`
- `npx vite build`
- `node scripts/test-multisession/run.mjs`（应继续 38/38 全过）
- 视觉对照：截图 vs mockup，差异点逐项列出

## 落地文件

- `src/styles/index.css` — token 块 + 全量 radius 替换
- `src/components/TabBar.jsx`（**方案 B 才改**）— 调整 `.tabbar-tabs` padding/align
- `src/components/StudioWorkbench.css` — 仅检查是否有冲突样式需要同步

## 进度 checklist

- [ ] 用户在 AskUserQuestion 中选择方案
- [ ] 落地 CSS（按选中方案）
- [ ] 质量门通过
- [ ] 部署 + 重启
- [ ] 截图对比验证
- [ ] git commit & push
- [ ] memory/2026-08-24.md 追加记录
