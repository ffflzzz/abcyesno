# 待办面板（Todos Panel）设计规格

> 状态：草案 · 2026-09-09
> 目标形态：**消息流内嵌气泡**，跟随上下文逐步划线，对齐 Hermes TUI 的观感。

---

## 1. 背景与问题

Hermes 后端自带 `todo` 工具（`hermes-fork/tools/todo_tool.py`），agent 规划多步任务时会写入待办列表，TUI 在 `agent/display.py:466/1341` 专门渲染（完成项划线）。abcyesno 侧三处缺失导致「打印不出来」：

| 层 | 现状 |
|---|---|
| 后端能力 | ✅ 有（`todo` 工具，已注册进 coding toolset） |
| 传输契约 | ⚠️ 仅作为**普通工具调用**透传（`TOOL_CALL_START/ARGS`），无待办语义事件 |
| 前端 UI | ❌ `src/` 与 `agui-server.js` 中 `todo` 零引用，只有通用工具卡片 |
| 模型触发 | ❌ 近期 `agent.log` 中 `todo` 出现 0 次；`agent/system_prompt.py` 零处提及 |

---

## 2. 数据源

`todo` 工具契约（源自 `tools/todo_tool.py`）：

- 单一工具名：`todo`
- 写：`args.todos = [{ id, content, status }]`，另有 `merge: bool`
- 读：省略 `todos` 参数
- 状态机：`pending` → `in_progress` → `completed` / `cancelled`
- 写操作返回**写后的完整列表**（`merge=false` 为整体替换，`merge=true` 为按 `id` 合并）

**关键结论**：每次调用携带的是**快照**，前端应按「最新快照整体渲染」，不做增量合并（后端已负责 merge 语义）。

---

## 3. 数据契约：`todo.update` 事件

### 3.1 事件格式

由 `agui-server.js` 在识别到 `todo` 工具调用时发出（AG-UI `CUSTOM`）：

```jsonc
{
  "type": "CUSTOM",
  "name": "todo.update",
  "value": {
    "ts": 1757500000000,
    "items": [
      { "id": "1", "content": "分析现有项目结构", "status": "completed" },
      { "id": "2", "content": "创建 brief.json", "status": "in_progress" },
      { "id": "3", "content": "生成分镜表", "status": "pending" }
    ]
  }
}
```

- `items` 按工具传入顺序，**最多保留 20 条**（超出截断，防止提示词注入撑爆 UI）
- `content` 截断 200 字符；`status` 归一化为四态之一，非法值按 `pending`
- 缺 `id` 时以数组下标补 `id`，保证 React key 稳定

### 3.2 传输层改造点

`electron/backend/agui-server.js` → `createTurnTranslator.handleEvent` 的 `tool.start` / `tool.started` 分支：

1. 取出 `args`（兼容两种形态：`payload.args` 存在则用它，否则整个 payload；再兼容 `arguments` / `parameters` 包装键）
2. 定位 `args.todos`，非数组则丢弃
3. 归一化后 `send({ type: 'CUSTOM', name: 'todo.update', value: { ts, items } })`
4. **容错**：解析失败静默跳过（不得影响主文本流）；不重复发送相同快照（按 `JSON.stringify(items)` 与上次比对去重）

### 3.3 前端订阅路径

`useAgentStream.handleCustom` 目前仅把 `workflow.*` 转发进 `contractEvents`（`useAgentStream.js:633`）。新增分支：

```js
} else if (name === "todo.update") {
  emitContractEvent(sess.id, { type: "todo.update", payload: value });
  publish(sess.id);
}
```

组件侧沿用既有模式：`useContractEvents(sessionId)` → 取**最后一条** `todo.update` 作为当前快照。

---

## 4. UI 形态（消息流内嵌气泡）

### 4.1 位置与生命周期

- 渲染在**助手消息流中**，位于「内联思考 / workflow 进度」区域附近，作为一条特殊气泡
- **同一轮 turn 内原地更新**：`todo` 每次调用更新同一条气泡（取最新快照），**不逐次插入新气泡**——避免刷屏，与 TUI 的「活块」一致
- 新的一轮（新的 `message.start`）开始时，上一轮气泡**冻结为历史**，新一轮重新起一条
- 一轮结束后气泡保持最终态（全部划线 + 计数）

### 4.2 视觉规范

```
┌──────────────────────────────────────────┐
│ 📋 待办  2/5                     [收起]  │
│ ✅ 分析现有项目结构                       │  ← completed：灰色 + line-through
│ ◐ 创建 brief.json                        │  ← in_progress：accent 色 + 轻微呼吸
│ ○ 生成分镜表                              │  ← pending：muted
│ ○ 角色图与场景图                          │
│ – 导出剪映草稿                            │  ← cancelled：淡删除线
└──────────────────────────────────────────┘
```

- 状态图标：`pending ○` / `in_progress ◐` / `completed ✅` / `cancelled –`
- 完成态：`text-decoration: line-through` + `opacity: .6`
- 顶部计数：`已完成/总数`（`completed` 计入分子，`cancelled` 不计入总数分母）
- 超过 6 条时默认折叠为「📋 待办 2/8（展开）」，点击展开
- 深色/浅色主题均使用既有 token（`--panel`、`--muted`、`--accent`、`--radius-*`），不写死颜色

### 4.3 组件与文件

- 新增 `src/components/TodoBubble.jsx`（纯展示，输入 `items`，无 I/O）
- `src/components/MessageThread.jsx`：从 `contractEvents` 计算 `todos` 并渲染
- `src/styles/index.css`：`.todo-bubble*` 系列样式

---

## 5. 微信侧文案

复用 `progress-tracker` 的既有门控（去重 45s / 节流 20s / 单轮 6 条），新增 `todo.update` 分支：

| 场景 | 文案 |
|---|---|
| 首次收到计划（N≥2） | `📋 计划 N 步：1) 分析项目结构 2) 创建 brief…`（内容截断，最多列前 3 条） |
| 有进展（完成数增加） | `✅ 2/5 分析现有项目结构 已完成` |
| 全部完成 | `✅ 计划 5 步全部完成` |

- 去重键：`todo:${completed}/${total}`，避免同一计数重复播报
- 全部完成属于**非紧急**，仍走节流窗口，不抢最终答案额度
- 单步计划（N=1）不推送（无信息量）

---

## 6. 测试点

### 6.1 单元（桥，`npm run test:bridge`）

1. `todo.update` 首次 → 输出 `📋 计划 N 步：…`
2. 完成数增加 → 输出 `✅ x/N …`
3. 相同计数重复到达 → 去重（第二次返回 null）
4. 全部完成 → `✅ 计划 N 步全部完成`
5. 单步计划 → 不推送
6. `items` 非法（非数组 / 空 / 字段缺失）→ 安全返回 null，不抛

### 6.2 手工验证

1. 在应用内发起多步任务（如「先 A 再 B 最后 C」），确认模型调用 `todo` 后气泡出现
2. 观察逐步划线：完成一步即有一条被划掉，计数递增
3. 新开一轮对话：上一轮气泡冻结，新一轮重新起一条
4. 微信侧收到 `📋 计划 N 步` 与 `✅ x/N`，且不刷屏
5. 关闭/重开会话后历史气泡保持最终态

### 6.3 回归门

`node scripts/check-tdz.js` · `npx vite build` · `scripts/test-multisession/run.mjs`（38/38）· `npm run test:bridge`

---

## 7. 分阶段实施

| 阶段 | 内容 | 依赖 |
|---|---|---|
| **P1** | agui-server 发 `todo.update` + 前端内嵌气泡 + 样式 | 无 |
| **P2** | 微信桥文案（`progress-tracker` 分支 + 单测） | P1 的事件格式 |
| **P3** | 提示词引导（默认人设/系统提示加入「多步任务先写 todos」）+ 实测模型是否触发 | P1/P2 完成，需真实任务验证 |

**P3 说明**：当前模型不会自发使用 `todo`。P1/P2 落地后若仍无待办产生，需在 `hermes-fork/hermes_cli/default_soul.py` 或 config 的 `system_prompt` 中加入显式规则。此项涉及后端人设改动，单独评估。

---

## 8. 未决问题

1. **气泡归属**：快照属于「当前 turn」还是「整个会话」？当前设计按 turn 冻结；若用户希望会话级常驻（如侧边常显最新计划），需另做面板（不在本次范围）。
2. **merge 模式**：后端 `merge=true` 时前端收到的是合并后全量，无需特殊处理；若未来出现「仅增量」载荷，需补合并逻辑。
3. **长内容**：单条 `content` 超 200 字符截断后，是否需要 hover 全文？当前设计 `title` 属性兜底。
