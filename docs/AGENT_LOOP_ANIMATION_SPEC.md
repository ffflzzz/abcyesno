# Agent Loop 状态动画 Spec v0.1

> **目标**：定义 Abcyesno agent 运行全生命周期的状态动画体系——每个阶段有明确的视觉反馈、文字流光/脉冲等动态效果，任务完成后动画静止为终态。
> **确认后动手**：本文档仅供 lex 审批，不直接改代码。

---

## 1. 现状诊断

### 1.1 已有但未生效 / 有缺陷的动画

| 问题 | 位置 | 影响 |
|---|---|---|
| `pulse-soft` 未定义 | `.tool-summary-status.running` (index.css:2648) 引用不存在的 keyframes | "执行中…"标签**完全静态** |
| `pulse` 重复定义 | 638行(纯opacity) vs 3802行(scale+opacity)，后者覆盖前者 | `header-status-dot` 等拿到错误版本 |
| `thinking-shimmer` 已写好 | index.css:2575 渐变扫描 | 可能因浏览器兼容或选择器优先级未生效 |
| 头像三态动画已写好 | `agent-breathe/ring/spin/typing` (index.css:860-896) | 依赖 `.agent-avatar` 正确加 class |

### 1.2 用户截图反映的核心缺失

- **截图1**："正在思考…" → 只有蓝色圆圈 spinner + 静态文字，**无流光**
- **截图2**：langgraph_agent "执行中…" → warning 色标签 + 静态文字，**无脉冲/流动**

### 1.3 状态源（已确认）

```
useAgentStream.phase:
  idle → thinking → tool_executing → text_generating → idle
                                    ↘ idle (RUN_FINISHED/RUN_ERROR)

isStreaming = phase !== "idle"
```

状态从 `useAgentStream.js` → `App.jsx` → `ChatLayout.jsx`(isLoading/status/streamPhase) → `MessageThread.jsx`(loading/phaseLabel)。

---

## 2. 动画状态机（6 态）

| # | 状态名 | phase 值 | 触发条件 | 视觉关键词 |
|---|---|---|---|---|
| S0 | **空闲** | `idle` | 无活动 | 全部静止，头像正常 |
| S1 | **思考中** | `thinking` | RUN_STARTED，模型推理 | 头像呼吸 + 文字流光 + 圈旋转 |
| S2 | **执行工具** | `tool_executing` | TOOL_CALL_START | 头像转环 + 标签脉冲 + 卡片展开动效 |
| S3 | **生成回复** | `text_generating` | TOOL_CALL_END 后出文本 | 头像弹跳 + 打字光标闪烁 |
| S4 | **完成** | `idle` + isStreaming=false | RUN_FINISHED | 所有动画→静止，图标变✓ |
| S5 | **出错** | `idle` + error | RUN_ERROR | 脉冲红闪→静止红色 |

**状态流转图**：

```
S0(空闲) ──发送消息──→ S1(思考中) ──工具调用──→ S2(执行工具)
  ↑                           │                      │
  │                    直接回复(无工具)               │ TOOL_CALL_END
  │                           ↓                      ↓
  └──────────────── S3(生成回复) ←───────────────────┘
                              │
                         RUN_FINISHED
                              ↓
                          S4(完成)
                              │
                         RUN_ERROR
                              ↓
                          S5(出错)
```

---

## 3. 逐态动画规格

### 3.0 S0 — 空闲（基准态）

| 元素 | 表现 |
|---|---|
| 头像 | 正常大小，无动画 |
| 状态栏（header） | 绿点"就绪"，静止 |
| 输入区巴赫 | `bach-idle`（轻微摇晃） |
| 无任何运行中指示器 | — |

### 3.1 S1 — 思考中（thinking）

**核心体验：AI 在"想"，安静但有生命感。**

| 元素 | 当前状态 | 目标动画 | 规格 |
|---|---|---|---|
| **头像** | `agent-breathe` 已有 | ✅ 保留，微调 | `scale(1→1.06)` 1.8s ease-in-out infinite；外环 `agent-ring` box-shadow 扩散 2s |
| **思考圈 spinner** | `thinking-spin` + `spinner-glow` 已有 | ✅ 保留 | 0.8s linear 旋转 + 1.6s 外发光脉冲 |
| **"正在思考…"文字** | `thinking-shimmer` 已写但可能未生效 | 🔧 **重点修复** | 见下方 3.1a |
| **三点省略号** | `dot-bounce` 已有 | ✅ 保留 | 错位弹跳 1.2s |
| **header 状态栏** | "思考中…" + 点 | 加流光 | 文字同 thinking-shimmer |
| **气泡容器** | 无 | 加微妙脉动 | `box-shadow` 微弱呼吸 2.4s |

#### 3.1a 文字流光（thinking-shimmer）修复规格

```css
/* 目标效果：文字从左到右扫过一道高光，循环 */
.thinking-text,
.think-phase-label,
.header-status-text.thinking {
  background: linear-gradient(
    90deg,
    var(--text-muted) 0%,
    var(--text-primary) 40%,
    var(--accent) 50%,
    var(--text-primary) 60%,
    var(--text-muted) 100%
  );
  background-size: 200% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: thinking-shimmer 2s linear infinite;
}

@keyframes thinking-shimmer {
  0% { background-position: 100% 0; }
  100% { background-position: -100% 0; }
}

/* 不支持 background-clip:text 的降级 */
@supports not (-webkit-background-clip: text) {
  .thinking-text { animation: thinking-breathe-fallback 2s ease-in-out infinite; }
}
```

**关键修复点**：
1. 确保 `var(--text-muted)` / `var(--text-primary)` / `var(--accent)` 在 CSS 变量中已定义
2. `color: transparent` 必须在 `background-clip: text` 之后（某些引擎顺序敏感）
3. `background-size: 200%` 是扫光前提

### 3.2 S2 — 执行工具（tool_executing）

**核心体验：AI 在"干活"，节奏加快，有进度感。**

| 元素 | 当前状态 | 目标动画 | 规格 |
|---|---|---|---|
| **头像** | `agent-spin`（转环）已有 | ✅ 保留 | 外环 0.9s linear 无限旋转，warning 色 |
| **"执行中…"标签** | 引用不存在的 `pulse-soft`，**完全静态** | 🔧 **重点新增** | 见下方 3.2a |
| **ToolCard 边框** | warning 色，无动画 | 加流动光边框 | `border-color` 循环渐变或左侧进度条 |
| **ToolCard 展开** | `tool-expand` 已有 | ✅ 保留 | height transition 0.25s |
| **TerminalPanel 流式输出** | `terminal-line-in` + cursor-blink | ✅ 保留 | 逐行淡入 + 光标闪烁 |
| **header 状态栏** | "执行工具…" | 同执行中标签风格 | 脉冲 warning 色 |

#### 3.2a "执行中…"标签脉冲+流光规格

```css
/* 方案：标签背景色渐变循环 + 文字微闪烁 */
.tool-summary-status.running {
  background: var(--warning-subtle, rgba(234,179,8,0.15));
  color: var(--warning, #eab308);
  border: 1px solid rgba(234,179,8,0.3);
  /* 修复：替换悬挂的 pulse-soft */
  animation: status-running-pulse 1.5s ease-in-out infinite;
  position: relative;
  overflow: hidden;
}

/* 左侧流动进度条 */
.tool-summary-status.running::before {
  content: '';
  position: absolute;
  left: -100%;
  top: 0;
  width: 100%;
  height: 100%;
  background: linear-gradient(
    90deg,
    transparent,
    rgba(234,179,8,0.25),
    transparent
  );
  animation: status-sweep 1.8s ease-in-out infinite;
}

@keyframes status-running-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.65; }
}

@keyframes status-sweep {
  0% { left: -100%; }
  100% { left: 200%; }
}
```

### 3.3 S3 — 生成回复（text_generating）

**核心体验：AI 在"说话"，轻快、有输出感。**

| 元素 | 当前状态 | 目标动画 | 规格 |
|---|---|---|---|
| **头像** | `agent-typing` 弹跳已有 | ✅ 保留 | translateY 0→-4px 0.7s |
| **打字光标** | `cursor-blink` 已有 | ✅ 保留 | 1s steps(1) |
| **TypewriterText 逐字** | 已有 | ✅ 保留 | 字符逐个出现 |
| **气泡** | 无 | 微弱生长感 | `min-height` 平滑过渡 |

### 3.4 S4 — 完成（静止终态）

**所有动画停止，显示结果。**

| 元素 | 表现 |
|---|---|
| 头像 | 恢复正常，无 class |
| 状态栏 | 绿点"就绪" |
| 工具聚合栏 | "全部完成" + ✓ 图标 + success 色，**静止** |
| ToolCard | 各卡片 `status="complete"`，✓ 图标，可折叠 |
| 思考指示器 | 移除（DOM 卸载） |
| 过渡 | 从 S1/S2/S3 到 S4 用 300ms ease-out 渐隐动画到终态 |

**"全部完成"标签规格**：
```css
.tool-summary-status.complete {
  background: var(--success-subtle, rgba(34,197,94,0.12));
  color: var(--success, #22c55e);
  border: 1px solid rgba(34,197,94,0.25);
  /* 故意无 animation —— 完成态静止 */
}
```

### 3.5 S5 — 出错

| 元素 | 表现 |
|---|---|
| 状态栏 | 红"出错" |
| 错误消息 | 红色气泡，带 shake 动画（0.5s，仅播一次） |
| 头像 | 无特殊动画（避免过度干扰） |

```css
@keyframes error-shake {
  0%, 100% { translateX(0); }
  20% { translateX(-4px); }
  40% { translateX(4px); }
  60% { translateX(-3px); }
  80% { translateX(3px); }
}
.error-bubble { animation: error-shake 0.5s ease-out; } /* 只播一次 */
```

---

## 4. 组件级动画映射表

| 组件 | S1 思考 | S2 执行工具 | S3 生成回复 | S4 完成 | S5 出错 |
|---|---|---|---|---|---|
| **AgentAvatar** | breathe+ring | spin(环) | typing+bounce | normal | normal |
| **HeaderStatusBar** | 流光文字"思考中…" | 脉冲"执行工具…" | "生成回复…" | "就绪"绿 | "出错"红 |
| **ThinkingIndicator** | shimmer+spinner+dots | 隐藏 | 隐藏 | — | — |
| **StructuredThinking** | shimmer phase label | 隐藏 | 隐藏 | — | — |
| **ToolsRow 聚合栏** | 隐藏 | pulse+sweep"执行中…" | 隐藏 | 静止"全部完成"✓ | — |
| **ToolCard** | — | sweep光边+running态 | — | ✓ complete | error |
| **TerminalPanel** | — | line-in+cursor | — | 静止 | — |
| **TypewriterText** | — | — | cursor-blink | 静止全文 | — |
| **MessageBubble** | 微弱脉动 | — | 生长过渡 | 正常 | shake |

---

## 5. CSS 变量依赖

动画层需要以下变量（检查是否已存在，不存在则需补）：

```css
:root {
  /* 已有（确认即可） */
  --accent: #6c8aff;           /* 主色调蓝 */
  --text-primary: #e4e4e7;     /* 主文字 */
  --text-muted: #a1a1aa;       /* 弱化文字 */
  --success: #22c55e;          /* 成功绿 */
  --warning: #eab308;          /* 警告黄 */
  --error: #ef4444;            /* 错误红 */

  /* 需新增（半透明背景用） */
  --success-subtle: rgba(34,197,94,0.12);
  --warning-subtle: rgba(234,179,8,0.15);
  --error-subtle: rgba(239,68,68,0.12);
}
```

---

## 6. 技术债务清理清单

在实现新动画前，必须先修：

| # | 债务 | 操作 |
|---|---|---|
| D1 | `pulse-soft` 未定义 (line 2648) | 删除引用 or 定义 keyframes |
| D2 | `pulse` 重复定义 (638 vs 3802) | 重命名其中一个（如 `pulse-scale`） |
| D3 | `thinking-shimmer` 可能未生效 | 检查 CSS 变量 + `color:transparent` 顺序 |
| D4 | 动画停止机制缺失 | 完成/出错时确保 `animation: none` 被设置 |

---

## 7. 实现优先级

| P0（本次做） | 用户明确要求的 |
|---|---|
| P0-1 | **思考文字流光**（shimmer 修复+增强） |
| P0-2 | **"执行中…"标签脉冲+扫光**（修 pulse-soft 悬挂） |
| P0-3 | **完成态静止**（确保动画停止，不继续跑） |

| P1（后续优化） | 锦上添花 |
|---|---|
| P1-1 | 气泡容器微弱脉动（S1） |
| P1-2 | ToolCard 流动光边框（S2） |
| P1-3 | 出错 shake 动画（S5） |
| P1-4 | S2→S3→S4 状态过渡渐隐（300ms） |

| P2（暂不做） | 备忘 |
|---|---|
| P2-1 | 巴赫 busy 态联动（composer-bach-busy 已有但未接入 isStreaming） |
| P2-2 | 进度条百分比可视化（需后端传 progress 事件） |

---

## 8. 验收标准

1. 发送消息后，"正在思考…"文字有**从左到右的流光循环**效果
2. 进入工具调用后，"执行中…"标签有**脉冲 + 左侧扫光条**
3. 任务完成后，**所有动画立即静止**，显示"全部完成"✓（success 色）
4. 反复多轮对话不泄漏动画（无多余 spinner/脉动残留）
5. Dark theme 下流光可见且不刺眼（亮度对比度 ≥ 3:1）

---

*待 lex 确认后按 P0 顺序实施。*
