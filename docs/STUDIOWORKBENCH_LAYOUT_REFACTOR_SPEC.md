# 短剧制片工作台（StudioWorkbench）布局重构 Spec

> 状态：**v1.0 已拍板并实施**（2026-08-20）
> 触发：用户截图反馈「页面很混乱，很难使用，窗口那些一些巨大，一些小，缩放和排版都有问题」，明确指向「生成 / 结果 / 预览剪辑」三类页面。
> 目标：把 4 个 phase（剧本 / 资产 / 分镜 / 成片）拆成各自适配的布局，从一张大网格硬挤改成多布局分屏。

## 0. 已拍板决策（用户 2026-08-20）

- **采用方案 A**（分屏分阶段布局），非方案 B。
- **视觉风格一并重设计**：颜色/字号/留白全部重做。改为全面使用 app 主题变量（暗/亮双主题自适应），去掉原有硬编码暗色字面量；统一圆角/阴影/间距/字号层级；CTA 用 accent→紫渐变 + 白字。
- **右侧 aside 默认展开**，可折叠为 44px rail，折叠状态持久化进 `_studioCache` + localStorage。
- **phase=剧本 / 资产 时，完全隐藏 AssetLibrary + TaskPanel**（中段单列居中 `max-width:760px`）。
- **phase=分镜**：左 AssetLibrary + 中 shot 编辑器 + 右 TaskPanel（默认展开）。
- **phase=成片**：中 编辑台 + 右 TaskPanel（无 AssetLibrary）。
- **运行态 override**：`running` 时即使 phase 仍在剧本/资产也显示右侧 TaskPanel，保证实时进度可见。
- shot 卡片：`align-items:start` + 预览 `aspect-ratio:9/16; max-height:300px; object-fit:contain`；网格 `minmax(0,1.6fr) minmax(0,2fr) minmax(0,1.3fr)`。

## 1. 现状（截图佐证）

| 截图 | 现象 | 根因（CSS） |
|------|------|-------------|
| 图1 | 「神威狗」角色图被中段预览列吃满，整行 shot 卡片被撑成 hero 大图 | `.st-shot { grid-template-columns: 1fr 1fr 1fr; align-items: stretch }` + `.st-preview { flex: 1 }` → 预览图按高度 stretch 占满整行 |
| 图2 | 底部「第 0 集·第 2 镜」shot 卡的文本与模型选择器溢出窗口底部 | `.st-center { overflow: auto }` 缺 `max-height`/缺内部 scroll 容器；shot 列表无独立滚动约束 |
| ��1/图2 | 三列 grid 210px/1fr/280px 在 1280px 宽屏下中间列净宽 ≈ 790px，3 等分后每 shot 列只有 ~250px | 固定三列 + shot 内 1fr 1fr 1fr，无最小/最大宽度约束 |
| 全局 | 右侧「任务中心」和「LangGraph 节点追踪」始终占 280px，phase=剧本/成片时浪费中段 | 固定 grid 不区分 phase 视图本质 |

**核心症结**：4 个 phase 是本质不同的视图（表单 / 列表 / 编辑器 / 时间轴），却硬塞进同一张三列 grid，且 shot 预览列用 `flex:1` 让图片无界拉伸。

## 2. 重构目标

1. **每 phase 有适配自己的布局**，不再共用一张网格
2. **图片/视频预览有显式宽高比与最大尺寸**，不再无界拉伸
3. **每个长列表容器有独立 scroll** + 合理 max-height，不再溢出窗口
4. **右侧任务追踪改为可折叠 aside**，phase 切换时不让它无脑占 280px
5. **保留全部现有功能**：smart input、3×2 视图网格、HITL 审批门、shot 编辑、时间轴拖拽、导出剪映、LangGraph 节点追踪
6. **不引入新依赖**，纯 React + CSS Grid/Flex 改造

## 3. 方案对比

### 方案 B：小修 CSS（不重构结构）
- shot 预览加 `aspect-ratio: 9/16; max-height: 360px; object-fit: contain`
- shot grid 改 `minmax(0, 2fr) minmax(0, 2fr) minmax(0, 3fr)` + 所有子元素 `min-width: 0`
- 中段加 `max-height: calc(100vh - <topbar+stepper>)` 强制 scroll
- 优点：改动小，~50 行 CSS
- 缺点：4 个 phase 还是挤一起，phase=剧本/成片时右侧任务栏空占；shot 行的「预览挤在中间列」本质问题没解决

### 方案 A：分屏分阶段布局（**推荐**）
- 4 个 phase 各自占据整个工作台区域，layout 完全不同
- 右侧任务追踪抽成可折叠 aside
- 优点：每个 phase 视觉/交互都适配本职；用户认知负担低
- 缺点：~300-500 行重构（jsx + css + 状态迁移）

**建议选 A**：长期 ROI 高；现在 phase=分镜一张图就能撑爆整行，B 方案治标不治本。

## 4. 推荐方案 A 详细设计

### 4.1 顶层骨架（所有 phase 共享）

```
┌─────────────────────────────────────────────────────────┐
│ st-topbar  (品牌 + refresh + exit)         48px         │
├─────────────────────────────────────────────────────────┤
│ st-stepper  (剧本→资产→分镜→成片)         64px          │
├──────────┬──────────────────────────────────────────────┤
│          │                                              │
│ 折叠 aside │        phase 专属主区                       │
│ 任务中心   │        (phase=脚本: 单列表单)              │
│ LangGraph │        (phase=资产: 2 列列表)               │
│ 节点追踪   │        (phase=分镜: 单列 shot 卡堆叠)       │
│ 240px 可隐 │        (phase=成片: 时间轴 + 详情)         │
│          │                                              │
└──────────┴──────────────────────────────────────────────┘
```

- aside 默认展开 240px，header 有折叠按钮收起为 0
- 主区 `flex: 1; min-width: 0; overflow: auto`，自己管 scroll

### 4.2 Phase 1：剧本（智能输入 / 项目表单）

**布局**：单列居中表单，最大宽 720px

```
┌─────────────────────────────────────────────────────┐
│ st-form-card                                       │
│ ┌─ 智能输入 (textarea + 🎲随机 + ✨解析) ──┐        │
│ ├─ 项目信息 (项目名 + 模式) ────────────────┤        │
│ ├─ 内容设定 (剧本/大纲 textarea) ───────────┤        │
│ ├─ 风格 + 集数 + 跨集一致性 ────────────────┤        │
│ ├─ 生成参数 (分辨率 + 每镜秒数) ────────────┤        │
│ ├─ 固定角色 (textarea) ─────────────────────┤        │
│ └─ [生成资产与分镜 →] (主 CTA) ─────────────┘        │
└─────────────────────────────────────────────────────┘
```

- 现有 form-card 已经是 `max-width: 720px; margin: 0 auto`，只需去掉左侧 AssetLibrary / 右侧 TaskPanel 的固定占位列，改成 `grid-template-columns: 1fr`（或纯 flex）

### 4.3 Phase 2：资产（角色圣经）

**布局**：2 列

```
┌────────────────────┬─────────────────────┐
│ 角色圣经 (主区)     │ 生成操作 (次要)      │
│ - 神威狗            │                     │
│   3×2 视图网格      │ [重新生成角色]      │
│   [重生该资产]      │ [一键生成全部资产 →]│
│ - 场景1 (待生成)    │                     │
│ - 道具1 (待生成)    │ 进度提示             │
└────────────────────┴─────────────────────┘
```

- 保留现在的 AssetLibrary 3×2 视图网格（已经做对了，截图里角色图缩略图大小 OK）
- 左侧列宽 `minmax(280px, 1fr)`，右侧固定 220px

### 4.4 Phase 3：分镜（核心，痛点）

**布局**：单列 shot 卡堆叠，每张卡内部 3 列但比例改成 **2fr | 3fr | 1.4fr**（剧本更宽，预览固定尺寸）

```
┌─────────────────────────────────────────────────────┐
│ 第 1 集·镜 1                          [▶ 00:01]      │
│ ┌─ 剧本文本 ──────┬─ 预览 ─────────┬─ 操作 ──────┐ │
│ │ <textarea>      │ ┌──────────┐   │ [重生视图]  │ │
│ │ 留空则沿用解析   │ │          │   │ [生成视频]  │ │
│ │ 结果…           │ │ 预览图   │   │             │ │
│ │ </textarea>     │ │ 9:16     │   │ 模型:        │ │
│ │                │ │ max-360  │   │ [agnes...]   │ │
│ │                │ └──────────┘   │             │ │
│ └────────────────┴───────────────┴──────────────┘ │
├─────────────────────────────────────────────────────┤
│ 第 1 集·镜 2  ...                                    │
├─────────────────────────────────────────────────────┤
│ ...                                                  │
└─────────────────────────────────────────────────────┘
```

**关键 CSS**：
```css
.st-shot {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 3fr) minmax(0, 1.4fr);
  gap: 14px;
  padding: 12px;
  /* 关键：高度由内容定，不 stretch */
  align-items: start;        /* ← 改这里 */
}
.st-shot textarea { min-width: 0; }    /* 防 grid 撑大 */
.st-preview {
  aspect-ratio: 9 / 16;      /* 强制 9:16 竖屏比例 */
  max-height: 360px;         /* 限制最大高度 */
  max-width: 240px;          /* 限制最大宽度 */
  flex: 0 1 auto;            /* ← 改这里：不 flex:1 */
}
.st-preview img, .st-preview video {
  width: 100%; height: 100%;
  object-fit: contain;       /* contain 而非 cover，保留完整 */
}
.st-center-inner { overflow-y: auto; max-height: calc(100vh - 160px); }
```

### 4.5 Phase 4：成片（时间轴 / 剪辑）

**布局**：时间轴为主，详情侧栏可折叠

```
┌─────────────────────────────────────────────────────┐
│ 导出设置 (分辨率 + fps + [导出剪映工程 ↓])            │
├─────────────────────────────────────────────────────┤
│ 时间轴 (横轨，shot 按时长占宽度)                       │
│ ─────────────────────────────                       │
├──────────────────────────────┬──────────────────────┤
│ 预览画布 (16:9, max-h 480)    │ 选中片段详情 (可折叠) │
│ ▶ ⏮ ⏭ 进度条  00:00 / 00:12 │ 时长 / 转场 / 删除    │
│                              │                     │
└──────────────────────────────┴──────────────────────┘
```

- 保留现有 EditConsole 逻辑，仅调整 grid 让画布有 max-height
- 选中片段时右侧详情展开，未选时折叠为 0

### 4.6 全局 aside（任务中心 + LangGraph 追踪）

```css
.st-aside {
  width: 240px;
  border-right: 1px solid var(--border);
  background: var(--panel);
  display: flex;
  flex-direction: column;
  transition: width .2s;
}
.st-aside.collapsed { width: 0; overflow: hidden; }
```

- 折叠按钮放在 stepper 右上角：「任务追踪 ›」
- 折叠状态存 StudioWorkbench 状态（持久化进 `_studioCache`）

## 5. 兼容性 / 不破坏清单

| 必须保留 | 处理 |
|----------|------|
| 智能输入 + 🎲随机 + ✨解析 | 不动 form 内容，只换外层 grid |
| 角色圣经 + 3×2 视图网格 | 不动 |
| HITL 审批门 (ApprovalBubble) | 不动，fixed overlay |
| 4 阶段 stepper 跳转 | 不动 |
| shot 卡内编辑（提示词改写 / 重生 / 生成视频） | 改 grid 比例但不动交互 |
| 时间轴拖拽 + 转场编辑 | 不动 |
| 剪映导出 + draft JSON 预览 | 不动 |
| LangGraph 节点追踪面板 | 移进折叠 aside |
| 错误态 `st-runerror` 卡片 | 不动 |
| `_studioCache` 持久化 | 加 `asideCollapsed` 字段即可 |

## 6. 验收标准

1. 4K 截图重测：任何 phase 下，shot 预览图都不再无界放大，整页无溢出
2. 窗口宽度 1280px / 1600px / 1920px 各测一次，布局都自适应不破
3. 折叠 aside 后，工作区主列扩展，画面无溢出
4. 重新载入（_studioCache 持久化）后 phase / 折叠状态 / shot 状态 / timeline 全部恢复
5. TDZ 干净 / vite build ok / multisession 26/26 全绿
6. Robocopy dist → release/win-unpacked，cp main.js，kill+restart Abcyesno.exe 实测确认
7. 不引入新依赖（package.json 不变）
8. diff 行数 < 600（js+css 合计）

## 7. 实施步骤（建议）

1. **Phase 3 优先**（痛点核心）：
   - 改 `.st-shot` grid 比例 + `align-items: start` + 子元素 `min-width: 0`
   - 改 `.st-preview` 加 `aspect-ratio: 9/16; max-height: 360px; max-width: 240px; flex: 0 1 auto`
   - 改 `.st-media-img / video` `object-fit: contain`
   - 加 `.st-center-inner { overflow-y: auto; max-height: calc(100vh - 160px) }`
   - 验证：刷新分镜页，预览图固定尺寸，shot 卡纵向堆叠不溢出窗口
2. **Phase 1**：去掉 phase=script 时左右两侧固定列，改成单列居中（保留 AssetLibrary/TaskPanel 在脚本阶段隐藏）
3. **Phase 2**：微调 AssetLibrary 与右侧操作区的比例
4. **Phase 4**：时间轴画布加 max-height，详情侧栏加可折叠
5. **全局 aside 折叠**：抽 aside 组件，加折叠按钮 + 状态
6. **持久化**：aside 折叠状态写进 `_studioCache`
7. 跑质量门 + 截图回归

## 8. 待用户确认

- [ ] 选方案 A 还是 B？
- [ ] aside 默认展开/收起？建议默认展开
- [ ] phase=剧本/资产时，是否完全隐藏 AssetLibrary / TaskPanel？（建议是，避免拥挤）
- [ ] 颜色/视觉风格不变（仅布局），对吗？