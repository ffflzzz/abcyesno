# Abcyesno v9 — Agent Verbose Timeline 组件规范

## 1. 目标

将当前 `MessageThread.jsx` 中静态的 `ToolCallCard` 替换为类似 **OpenAI Codex / Claude Code / WorkBuddy** 的 Agent Verbose Timeline 组件。

新组件需满足：
- 实时展示 agent 思考、工具调用、执行过程、结果/错误。
- 支持展开/折叠详情，默认展示摘要。
- 动画流畅，状态转换清晰。
- 与现有暗色主题、消息气泡、CopilotKit 事件流完全兼容。
- 不引入新的运行时依赖。

## 2. 参考风格

### 2.1 Codex / Claude Code / WorkBuddy 共同特征

| 特征 | 说明 |
|------|------|
| 步骤流/时间线 | 每个工具调用/思考步骤独占一行，按时间顺序排列 |
| 状态脉冲动效 | 当前执行中的步骤带呼吸灯或旋转 spinner；已完成步骤显示成功图标；错误步骤显示错误图标 |
| 折叠/展开 | 默认折叠参数/输出，点击后展开完整内容 |
| 实时输出 | 长时间运行的命令可展示滚动日志 |
| 文件变更 | 编辑类工具展示 `+n / -m` 行数统计 |
| 最终总结 | 全部完成后可收缩为一句摘要 |

### 2.2 本组件采用的设计

- **垂直时间线**：左侧状态图标 + 连接线，右侧内容卡片。
- **三种步骤类型**：`thought`（思考）、`tool`（工具调用）、`result`（结果/总结）。
- **四种状态**：`pending`（等待中）、`running`（执行中）、`complete`（完成）、`error`（失败）。
- **单条可展开**：点击步骤卡片展开详情；再次点击收起。
- **整组可折叠**：当一组 verbose 步骤结束后，显示「收起/展开全部」入口。

## 3. 组件架构

```
MessageThread.jsx
└── AgentVerboseTimeline ({ steps, onStepClick, onToggleCollapse })
    ├── TimelineHeader (总标题 + 收起/展开按钮)
    └── TimelineStep[]
        ├── StepIcon (状态图标)
        ├── StepConnector (与下一步的连接线)
        └── StepCard
            ├── StepHeader (类型标签 + 名称 + 状态 + 时间)
            ├── StepSummary (一句话摘要)
            └── StepDetails (可折叠的参数/输出/错误详情)
```

## 4. 数据模型

```ts
interface VerboseStep {
  id: string;                    // 唯一标识
  type: "thought" | "tool" | "result" | "system";
  status: "pending" | "running" | "complete" | "error";
  name?: string;                 // tool 名称 / thought 标题
  summary?: string;              // 一句话摘要
  details?: string | object;     // 完整参数或输出
  durationMs?: number;           // 执行耗时
  createdAt: number;             // 时间戳
  metadata?: {
    filePath?: string;           // 文件操作路径
    linesAdded?: number;         // 新增行数
    linesRemoved?: number;       // 删除行数
    command?: string;            // 终端命令
    exitCode?: number;           // 命令退出码
    errorMessage?: string;       // 错误信息
  };
}
```

## 5. 状态流转

```
用户发送消息
    │
    ▼
 assistant 开始思考
    │  → 产生 thought 步骤 (running)
    ▼
 决定调用工具
    │  → tool 步骤 (running)
    ▼
 等待后端事件
    │
    ├── TOOL_CALL_START / tool.start → tool 步骤保持 running
    ├── 中间输出 → 更新 details（实时追加）
    ├── TOOL_CALL_END / tool.complete → tool 步骤变为 complete
    │                                → 生成 result 步骤 (complete)
    └── 错误 / interrupt → tool 步骤变为 error
                              → 生成 result 步骤 (error)
```

## 6. 视觉规范

### 6.1 颜色

沿用 `docs/UI_UX_SPEC.md` 色板：

| 用途 | 色值 |
|------|------|
| 时间线容器背景 | 透明（继承消息气泡 `#161b22`） |
| 步骤卡片背景 | `#0f1419` |
| 步骤卡片边框 | `#21262d` |
| 主文字 | `#e6edf3` |
| 次要文字 | `#8b949e` |
| thought 图标 | `#8b949e` |
| tool 图标 | `#4f8cff` |
| result 图标 | `#238636` |
| error 图标 | `#da3633` |
| running 呼吸色 | `#4f8cff` |
| 连接线 | `#21262d` |

### 6.2 尺寸与间距

- 时间线左侧图标列宽度：`28px`
- 图标大小：`14px`
- 连接线宽度：`2px`
- 步骤卡片内边距：`12px`
- 步骤卡片圆角：`8px`
- 步骤之间间距：`8px`
- 步骤标题字号：`13px`
- 摘要/详情字号：`12px`
- 时间戳字号：`11px`

### 6.3 图标

使用 Unicode 字符或 CSS 动画，不引入图标库：

| 类型/状态 | 图标 |
|-----------|------|
| thought running | `🧠` + 呼吸动画 |
| thought complete | `🧠` |
| tool running | `⚙️` 旋转 |
| tool complete | `✅` |
| tool error | `❌` |
| result | `📝` |
| system | `ℹ️` |

## 7. 动画规范

### 7.1 running 呼吸动画

```css
@keyframes pulse-glow {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(79, 140, 255, 0.4); }
  50% { opacity: 0.7; box-shadow: 0 0 0 4px rgba(79, 140, 255, 0); }
}
```

### 7.2 旋转动画

```css
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
```

### 7.3 展开/折叠动画

```css
.step-details {
  max-height: 0;
  opacity: 0;
  overflow: hidden;
  transition: max-height 0.25s ease, opacity 0.2s ease;
}
.step-details.open {
  max-height: 600px;
  opacity: 1;
}
```

### 7.4 新步骤入场动画

```css
@keyframes slide-in {
  from { opacity: 0; transform: translateY(-6px); }
  to { opacity: 1; transform: translateY(0); }
}
```

## 8. 交互规范

- 点击任意步骤卡片 → 切换该步骤详情展开/折叠。
- 点击头部「收起全部」→ 所有步骤折叠为摘要。
- 点击头部「展开全部」→ 所有步骤展开详情。
- 当存在 running 步骤时，自动滚动到底部，让用户看到最新进展。
- 鼠标悬停步骤卡片 → 边框颜色变为 `#30363d`。

## 9. 与现有事件流集成

`App.jsx` / `ChatLayout.jsx` 中已有工具事件处理逻辑。新组件只负责渲染，不改动事件协议。

消息对象中 `role === "tool"` 的消息会被转换为一组 `VerboseStep`：

```js
// 伪代码
function toolMessageToSteps(toolMsg) {
  const steps = [];
  steps.push({
    id: `${toolMsg.id}-call`,
    type: "tool",
    status: mapStatus(toolMsg.status),
    name: toolMsg.toolName,
    summary: `调用 ${toolMsg.toolName}`,
    details: toolMsg.args,
    createdAt: toolMsg.createdAt,
  });
  if (toolMsg.result || toolMsg.content) {
    steps.push({
      id: `${toolMsg.id}-result`,
      type: "result",
      status: mapStatus(toolMsg.status),
      summary: summarizeResult(toolMsg.result || toolMsg.content),
      details: toolMsg.result || toolMsg.content,
      createdAt: toolMsg.createdAt,
    });
  }
  return steps;
}
```

`thought` 步骤由后端 `agent.thought` 类事件生成；如后端无明确 thought 事件，可在 assistant 消息开始生成时自动插入一个 `thought` 步骤占位。

## 10. 文件变更计划

| 文件 | 变更 |
|------|------|
| `src/components/AgentVerboseTimeline.jsx` | 新增主组件 |
| `src/components/MessageThread.jsx` | 移除 `ToolCallCard`，引入 `AgentVerboseTimeline` |
| `src/styles/index.css` | 新增 `.agent-verbose-*` 样式 |
| `docs/AGENT_VERBOSE_SPEC.md` | 本文件 |
| `docs/DEV_LOG.md` | 记录本次改造 |

## 11. 验收标准

- [ ] `npm run build` 在 `L:/abcyesno-v9` 通过，无新增 warning/error。
- [ ] 打开聊天窗口，发送任意消息，工具调用以时间线形式展示。
- [ ] running 状态的步骤有呼吸/旋转动画。
- [ ] 点击步骤可展开/折叠详情。
- [ ] 头部「展开全部/收起全部」生效。
- [ ] 错误状态的步骤显示红色图标和错误摘要。
- [ ] 暗色主题颜色符合本规范 6.1。
- [ ] 不引入新的 npm 依赖。
- [ ] 不影响现有消息气泡、用户消息、图片放大等功能。
