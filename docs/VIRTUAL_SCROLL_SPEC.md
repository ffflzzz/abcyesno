# 自研虚拟滚动（Virtual Scroll）开发规范

> 目标：替换 react-virtuoso，为 Abcyesno 消息列表提供一个**零依赖、可预期、能调试**的虚拟滚动实现。
> 背景：react-virtuoso 在本项目 Electron 生产环境三次集成均失败（容器高度测量为 0 导致全空白），根因是该库高度求解依赖复杂的 flex 链 + 初始化时序，黑盒不可控。自研方案用 200 行代码换取完全可控的行为。

---

## 1. 设计目标与非目标

### 目标
- 只渲染视口内的消息行 + 上下缓冲，长会话（1000+ 条）滚动流畅（60fps）。
- 新消息到达时自动滚到底（除非用户上滚超过阈值）。
- 上滚超阈值时右下角显示"↓ 回到底部"按钮。
- 行高**可变**（消息长短不一、工具卡片可展开/收起）。
- 与现有 `useAgentStream` / `MessageThread` 数据结构零改动对接。

### 非目标
- 不做横向虚拟化。
- 不做无限加载分页（历史消息一次性全量在内存，只是不全渲染）。
- 不做行高缓存持久化（刷新后重新测量，可接受）。
- 不追求 Virtuoso 的全部特性（grid、分组吸附 header 等）。

---

## 2. 核心原理

虚拟滚动的本质：**只把"应该可见的行"挂到 DOM 上，其余用上下两个占位高度撑开滚动条**。

```
┌─────────────────────────┐
│   topSpacer (上占位)     │  ← 高度 = 所有在视口上方的行的总高
├─────────────────────────┤
│  可见行 i                │  ← 真实 DOM
│  可见行 i+1              │
│  ...（视口内 + 缓冲）     │
│  可见行 j                │
├─────────────────────────┤
│   bottomSpacer (下占位)  │  ← 高度 = 所有在视口下方的行的总高
└─────────────────────────┘
```

容器 `overflow-y: auto`，内容总高 = topSpacer + 可见行 + bottomSpacer，滚动条自然正确。滚动时根据 scrollTop 计算当前应渲染的行区间 `[startIndex, endIndex]`，重新渲染。

**关键难点：行高可变**。无法预先知道每行多高，所以采用**预估 + 实测修正**：
- 初始给每行一个预估值（如 80px）。
- 行渲染后用 ResizeObserver 实测真实高度，写入 `heightMap`。
- 行位置 = 前面所有行高之和（用前缀和加速查询）。

---

## 3. 数据结构

### 3.1 行模型（Row）
沿用现有 `MessageThread` 的 `renderGrouped` 输出，三种行：

```ts
type Row =
  | { type: "message"; data: Message }        // 普通消息（user/assistant）
  | { type: "tools"; items: Message[] }        // 工具调用组（ToolCard 列表）
  | { type: "thinking" };                      // 合成 thinking 指示行
```

### 3.2 高度表（HeightMap）
```ts
// 每个 row index 的实测高度（px）。未测过的用预估值。
heightMap: Map<number, number>
```
- 写入时机：行的 DOM 挂载后，用 ResizeObserver 监听该行高度变化（展开工具卡片、图片加载、打字机逐字都会改变高度）。
- 修正时机：某行实测高度变化时，重算它之后所有行的偏移。

### 3.3 前缀和（Prefix Offsets）
```ts
// offsets[i] = 第 i 行顶部距内容顶部的距离
offsets: number[]   // 长度 = rows.length + 1，offsets[0] = 0
```
- `offsets[i+1] = offsets[i] + rowHeight(i)`。
- `rowHeight(i) = heightMap.get(i) ?? estimatedHeight(i)`。
- 任何一行的实测高度更新后，从该行起增量重算后缀（O(n)，n 为总行数；1000 行内可忽略）。

---

## 4. 核心算法

### 4.1 可见区间计算
```ts
function findVisibleRange(scrollTop, viewportHeight, offsets, overscan): [start, end]
```
- 二分查找 `offsets` 找第一个底部 > scrollTop 的行 → start。
- 二分查找第一个顶部 > scrollTop + viewportHeight 的行 → end。
- 上下各加 overscan（行数，如上方 5 行、下方 3 行），clamp 到 `[0, rows.length-1]`。

### 4.2 占位高度
```ts
topSpacer = offsets[start]
bottomSpacer = offsets[rows.length] - offsets[end + 1]
```

### 4.3 滚动到底
```ts
function scrollToBottom(smooth):
  container.scrollTo({ top: offsets[rows.length], behavior: smooth ? "smooth" : "auto" })
```

### 4.4 atBottom 判定
```ts
atBottom = (scrollHeight - scrollTop - clientHeight) < threshold   // threshold = 200px
```

---

## 5. 组件设计

### 5.1 新 Hook：`useVirtualRows`
文件：`src/hooks/useVirtualRows.js`

```ts
function useVirtualRows({
  rows: Row[],
  estimatedHeight?: (row, index) => number,   // 默认按行类型给：message 80 / tools 120 / thinking 48
  overscanTop?: number,                        // 默认 5（行）
  overscanBottom?: number,                     // 默认 3（行）
  atBottomThreshold?: number,                  // 默认 200（px）
}): {
  containerRef,        // 挂到外层滚动容器
  virtualItems: [{ index, row, offsetTop }],  // 当前要渲染的行 + 各自的 top 偏移
  topSpacer, bottomSpacer,
  atBottom,
  scrollToBottom: (smooth?) => void,
  measureRow: (index, el) => void,            // 行渲染后调用，注册实测高度
}
```

职责：
- 维护 heightMap / offsets。
- 监听容器 scroll（rAF 节流）→ 算可见区间 → setState 触发重渲染。
- ResizeObserver 监听容器尺寸（窗口 resize）→ 重算可见区间。
- 暴露 measureRow 给每行的 ref 回调，实测高度写入 heightMap 并修正 offsets。

### 5.2 改造 `MessageThread`
- 删除 react-virtuoso 相关全部代码（import、virtuosoRef、containerHeight、ResizeObserver-for-container）。
- 引入 `useVirtualRows`。
- 渲染结构：

```jsx
<div className="vs-container" ref={containerRef} onScroll={...}>
  <div style={{ height: topSpacer }} />
  {virtualItems.map(({ index, row, offsetTop }) => (
    <div
      key={rowKey(row, index)}
      ref={(el) => measureRow(index, el)}
      data-row-index={index}
    >
      {renderRow(index)}
    </div>
  ))}
  <div style={{ height: bottomSpacer }} />
  {!atBottom && <button className="scroll-bottom-btn" onClick={scrollToBottom}>↓ 回到底部</button>}
</div>
```

注意：行**不需要**绝对定位。用最简单的"占位 + 正常文档流"即可（见 §2 原理图），避免 transform 定位带来的复杂度。

### 5.3 行 key 策略
- `message` 行：`row.data.id`。
- `tools` 行：`tools-${第一个 tool 消息 id}`。
- `thinking` 行：固定 `"thinking"`。
- 稳定 key 保证 React 复用 DOM，打字机/工具展开状态不丢。

---

## 6. 动态高度处理（重点）

这是自研方案最容易踩坑的地方，明确规则：

1. **初始预估**：未测量的行用 `estimatedHeight`。宁可估大不可估小（估小会导致滚动条跳动）。
2. **实测修正**：行挂载后 `measureRow` 用 `el.getBoundingClientRect().height` 记录；用 ResizeObserver 监听该行，高度变化时更新。
3. **修正策略**：某行高度从 H1 变 H2（差值 Δ），则该行之后所有行偏移 += Δ。直接重算整个 offsets 数组（1000 行内是微秒级）。
4. **视口锚定**：如果高度变化发生在视口**上方**的行，需要同步调整 scrollTop += Δ，否则用户会看到内容"跳"。视口内及下方的行变化不调整。
5. **打字机**：最后一条流式消息高度持续增长。由于它在最底部且 atBottom=true，followOutput 逻辑会持续滚到底，天然处理。
6. **工具卡片展开/收起**：高度变化大（几十→几百 px）。展开时该行在视口内，按规则 4 不调整 scrollTop（内容向下推，自然）。

---

## 7. 自动滚底与"回到底部"

- **followOutput**：当 `atBottom === true` 时，rows 变化（新消息 / 高度重算）后自动 `scrollToBottom(smooth)`。
- **冻结**：用户上滚使 `atBottom === false` 时，新消息到达**不**自动滚底，仅显示"↓ 回到底部"按钮。
- **点击按钮**：`scrollToBottom(smooth)`，按钮消失（atBottom 变 true）。
- **发新消息**：用户自己发消息时强制 `scrollToBottom` 并设 atBottom=true（用户意图明确）。

---

## 8. 性能要求

| 指标 | 目标 | 手段 |
|------|------|------|
| 滚动帧率 | 60fps | scroll 事件用 requestAnimationFrame 节流，每帧最多一次 setState |
| 首次渲染行数 | ≤ 视口行数 + 8 | overscanTop 5 + overscanBottom 3 |
| 高度重算复杂度 | O(n) 但 n≤1000 可忽略 | 前缀和数组，增量更新 |
| 内存 | 不随行数线性增长 DOM | 只有可见行进 DOM |

避免：
- 每行都挂 ResizeObserver（1000 行 = 1000 个 observer）。**改为**：只给可见行挂，行滚出视口时 disconnect。或共用一个 ResizeObserver 实例 observe 多个行元素（推荐，浏览器原生支持一个 RO observe 多元素）。
- scroll 回调里直接读写 DOM 导致强制同步布局（layout thrashing）。读写分离：回调里只读 scrollTop，setState 后在 render 里写。

---

## 9. 边界情况

| 场景 | 处理 |
|------|------|
| rows 为空 | MessageThread 顶层已 return null（ChatLayout 显示 welcome），不进入虚拟滚动 |
| 只有 1-2 条短消息（不足一屏） | topSpacer=0、bottomSpacer=0，可见区间=全部行，退化为普通渲染，正确 |
| 窗口 resize | ResizeObserver 监听容器 → 重算可见区间 |
| 切换会话（rows 全换） | 重置 heightMap / offsets / scrollTop=0（或滚到底），atBottom=true |
| 消息内容含图片（异步加载撑高） | 行的 ResizeObserver 捕获高度变化 → 修正 offsets |
| 打字机流式增高 | 行高持续变化 → 持续修正 + followOutput 滚底 |
| 工具卡片在视口上方展开 | 按 §6 规则 4 调整 scrollTop 锚定 |

---

## 10. 实施步骤

1. **新建** `src/hooks/useVirtualRows.js`：实现 §5.1 的 hook（约 150 行）。
2. **改造** `src/components/MessageThread.jsx`：移除 react-virtuoso，接入 useVirtualRows（净删约 30 行，新增约 20 行）。
3. **CSS**：`.vs-container { flex:1; min-height:0; overflow-y:auto; position:relative; }`，行包裹层 `will-change: transform` 可不加（用文档流方案不需要）。
4. **验证清单**：
   - [ ] 100 条历史消息滚动流畅，无白屏/闪烁
   - [ ] 新消息自动滚底
   - [ ] 上滚 300px 出现"↓ 回到底部"，点按钮滚底
   - [ ] 展开视口中间的工具卡片，内容下推不跳动
   - [ ] 打字机输出时持续滚底
   - [ ] 切换会话后列表正确重置
5. **卸载** react-virtuoso 依赖（可选，保留无妨）。

---

## 11. 验收标准

- 1000 条消息滚动 60fps，DOM 中同时存在的消息行 ≤ 视口行数 + 8。
- 所有 §9 边界情况通过验证清单。
- 不再出现"全空白"——因为容器高度不依赖任何 CSS 高度链求解，只依赖 `overflow-y:auto` 容器自身（这是浏览器布局的基本行为，不会失败）。

---

## 12. 为什么这次不会失败（与 Virtuoso 方案对比）

| | react-virtuoso（失败） | 自研（本 spec） |
|---|---|---|
| 容器高度来源 | 要求父容器有确定高度（flex 链 + height:100%），Electron 生产环境解析为 0 | 容器自身 `overflow-y:auto`，浏览器天然给它高度，不依赖求解 |
| 行高 | 黑盒测量，失败时无日志 | 显式 ResizeObserver + heightMap，每步可 console.log |
| 时序 | mount 时高度未稳定即测量，失败后不自愈 | 首帧先用预估高度渲染占位，RO 实测后修正，渐进收敛 |
| 代码量 | 引入 470KB→依赖黑盒 | ~200 行，完全可读可调试 |
