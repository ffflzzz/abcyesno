# 短剧制片工作台 · 定制 UI 规格书（Spec）

> 状态：草案（待评审，未实现）
> 作者：von / lex
> 关联文档：`LANGRAPH_CONTRACT_SPEC.md`、`FRONTEND_RENDERING_LAYER.md`
> 约束红线：**普通 workflow 仍走通用渲染器，零前端改动**。本工作台是「可选增强视图」，由 manifest 显式声明开启，不开启时系统行为完全不变。

---

## 1. 目标与范围

### 1.1 要解决的问题
用户截图中的「AI 短剧制片工作台」有 4 个特征是现有通用渲染器（`ContractForm` + `ArtifactCard` + `WorkflowTimeline`）表达不出的：

1. **三段式阶段条**（剧本 → 资产 → 分镜 → 成片），段间有依赖与准备态判断。
2. **三栏资产库**（角色 / 场景 / 道具），是跨 run 持久化的实体，分镜通过引用它们保证一致性。
3. **每镜三列同屏编辑器**（左：剧本文本 / 中：拍摄法+模型参数 / 右：视频预览+生成进度），每镜独立可生成。
4. **资产与分镜分离编排**：资产先生成、分镜后引用，不是一次性跑完。

### 1.2 不在本 spec 范围（v1）
- LibTV 式**节点画布 / 拖拽 DAG**（无限画布 + 连线）——那是另一种范式，本 spec 不做。
- 多人协同编辑、实时光标。
- 模型层一致性技术（LoRA / IP-Adapter / 参考图进化）——属 `agent.py` 职责，UI 只负责呈现与引用。
- **完整 NLE（多轨 / 关键帧 / 贴纸 / 抠像 / 调色）**——不在前端重做，导出剪映工程后在剪映里精修。
- **轻量剪辑台（EditConsole）属于范围**：单视频轨 shot 条拖拽排序 + 每镜时长 + 转场（无/淡入/淡出）+「导出剪映工程」。定位是「最后一程的粗排」，重活交给剪映。

### 1.3 成功标准
- 一个声明了 `studio` 块的 manifest，能在前端渲染出完整工作台，无需改任何现有组件逻辑。
- 一个不声明 `studio` 的 manifest（如 `manju_craft`、`image_gen`），渲染行为与今天完全一致。
- 新增「短剧类 workflow」= 写 manifest（含 `studio` 块）+ 写 `agent.py`，前端零新增代码（除本 spec 引入的通用 Studio 组件外）。

---

## 2. 触发机制：manifest 扩展

在现有 manifest 结构**顶层**新增一个可选对象 `studio`。缺省 = 走通用渲染器。

```json
{
  "id": "manju_studio",
  "name": "短剧制片工作台",
  "input_schema": { "...": "同通用结构，描述顶层项目参数" },
  "output_schema": { "...": "同通用结构，描述最终产物" },
  "studio": {
    "phases": [
      { "id": "script",   "label": "剧本",   "component": "ContractForm" },
      { "id": "assets",   "label": "资产",   "component": "AssetLibrary" },
      { "id": "storyboard","label": "分镜",  "component": "StoryboardEditor" },
      { "id": "export",   "label": "成片",   "component": "EditConsole" }
    ],
    "asset_types": [
      { "id": "character", "label": "角色", "generator": "manju_assets", "param": "character_desc" },
      { "id": "scene",     "label": "场景", "generator": "manju_assets", "param": "scene_desc" },
      { "id": "prop",      "label": "道具", "generator": "manju_assets", "param": "prop_desc" }
    ]
  }
}
```

- `agui-server.js` 的 `GET /api/ag-ui/contract/manifests` 已聚合所有 manifest，无需改聚合逻辑，只是多读一个字段。
- 前端 `App.jsx` 在选中 assistant 时读 `manifest.studio`：有 → 挂载 `<StudioView>`；无 → 走现有 `ContractForm` + `WorkflowTimeline` 路径。**这是唯一一处前端分支**，且是「有 studio 块才进，否则原路」的单点判断。

---

## 3. 整体布局（StudioView）

```
┌──────────────────────────────────────────────────────────────┐
│  PhaseStepper:  剧本 ✓ → 资产 → 分镜 → 成片                    │  ← 顶部阶段条
├──────────┬───────────────────────────────────────┬───────────┤
│          │                                        │           │
│ AssetLib │         Center Pane（随 phase 切换）      │ TaskPanel │
│ (仅 assets │   script:    ContractForm             │ (复用现有  │
│  阶段可见) │   assets:   AssetGrid                  │  任务进度) │
│          │   storyboard:StoryboardEditor(三列)      │           │
│ 角色/场景 │   export:   EditConsole(剪辑台+导出)      │           │
│ /道具 tabs│                                        │           │
│          │                                        │           │
└──────────┴───────────────────────────────────────┴───────────┘
```

- **左栏 `AssetLibrary`**：仅在 `assets` phase 可见；其他 phase 收起（或常驻窄条）。
- **中栏 Center Pane**：按当前 phase 渲染对应组件。
- **右栏 `TaskPanel`**：**完全复用现有** `TaskPanel.jsx`（按 run 渲染任务卡片 + 进度），不新写。

---

## 4. 数据模型

### 4.1 前端 store（StudioStore，新增轻量状态）
工作台需要跨 run 持久化的实体，通用渲染器没有这个概念。新增一个局部 store（不侵入 `useAgentStream`）：

```js
studio = {
  project:   { id, name, script, style, episodeCount, resolution, secPerShot },
  assets:    { character: [Asset], scene: [Asset], prop: [Asset] },  // Asset = {id, type, label, paths:[...], refRunId}
  episodes:  [ { id, index, title, shots: [Shot] } ],
  shots:     [ { id, episodeId, index, script, prompt, camera, model, videoPath, status } ],
  timeline:  [ { shotId, durationSec, transition } ],   // 成片页编排顺序（可拖拽重排）
  export:    { draftFolder, fps, width, height },        // 剪辑台导出参数
  phase:     "script" | "assets" | "storyboard" | "export",
  currentRunId: null
}
```

- 持久化：写入 `HERMES_HOME` 下的 `studio_<projectId>.json`（复用 `Storage` 机制），会话重载可恢复。
- 与普通 workflow 的 `session` 隔离，互不影响。

### 4.2 后端 → 前端事件（扩展 progress_events）
在现有 `step_started / step_progress / artifact_produced` 基础上，studio 类 workflow 可额外 emit 这些自定义 CUSTOM 事件（前端 `eventBus` 已按 runId 路由，直接订阅即可）：

| 事件 | payload | 前端动作 |
|---|---|---|
| `asset_produced` | `{ assetType, assetId, label, paths[] }` | 写入 `studio.assets[assetType]` → `AssetLibrary` 新增卡片 |
| `shot_created` | `{ shotId, episodeId, index, script }` | 初始化 `studio.shots[]` 一行 |
| `shot_updated` | `{ shotId, prompt?, camera?, model? }` | 更新对应 shot 中列字段 |
| `shot_video_ready` | `{ shotId, videoPath }` | 更新右列预览 + 状态 |
| `episode_ready` | `{ episodeId, videoPath }` | `EpisodeGallery` 新增成片 |

- 这些事件由 `manju_studio` 的 `agent.py` 在对应阶段 `emit`；前端只消费、不关心来源。
- 普通 workflow 不 emit 这些事件，行为不变。

---

## 5. 组件清单：复用 vs 新增

| 组件 | 状态 | 说明 |
|---|---|---|
| `PhaseStepper` | **新增** | 顶部阶段条，读 `manifest.studio.phases`，控制 `studio.phase` 切换 + 前置依赖判断（前段未完成禁跳） |
| `AssetLibrary` | **新增** | 左栏，按 `asset_types` 渲染 tabs；每 tab 一个 `AssetGrid`；「生成」按钮调对应 `generator` manifest（走现有 run 机制） |
| `AssetCard` | **新/复用** | 复用 `ArtifactCard` 样式，简化为图片网格卡 |
| `StoryboardEditor` | **新增（核心定制件）** | 每镜三列：左 `textarea`(script) / 中 表单(prompt,camera,model) / 右 视频预览 + 生成按钮 + 进度；行 = 一个 shot，独立可生成 |
| `EpisodeGallery` | **新增** | `ArtifactCard` 网格，每集一个视频卡，复用现有播放器（作为 `EditConsole` 导出后的结果视图） |
| `EditConsole` | **新增（成片页核心）** | 轻量剪辑台：单视频轨 shot 条（HTML5 拖拽排序）+ 每镜时长输入 + 转场下拉（无/淡入/淡出）+ 项目参数（分辨率/fps）+ 「导出剪映工程」按钮；导出时前端构建 `draft_content.json` + `draft_meta.json` 并写入工程文件夹 |
| `ContractForm` | **复用** | `script` phase 直接挂载，零改动 |
| `TaskPanel` | **复用** | 右栏任务进度，零改动 |
| `ArtifactCard` | **复用** | 资产卡 / 成片卡样式来源，零改动 |

**结论**：新增 5 个组件（`PhaseStepper` / `AssetLibrary` / `StoryboardEditor` / `EpisodeGallery` / `EditConsole`），全部是「studio 模式专用」，平时不挂载。现有 4 个通用组件零改动。

---

## 6. 后端契约（agent.py 职责）

`manju_studio/agent.py` 相对 `manju_craft/agent.py` 的变化：

1. **阶段编排**：按 `phases` 顺序执行，每段 emit 对应事件：
   - 资产段：调用 `manju_assets`（子 workflow 或内部函数）生成角色/场景/道具三视图 → emit `asset_produced`。
   - 分镜段：按 `episodeCount` 拆集 → 每集拆 shot → emit `shot_created` → 逐镜生成 → emit `shot_video_ready`。
   - 成片段：合成每集视频 → emit `episode_ready`。
2. **引用一致性**：分镜 prompt 中注入 `studio.assets` 的参考图路径（即上次讨论的「资产库=另一个 workflow、主 workflow 引用其产物」路径，落地到事件层）。
3. **循环 fan-out**：N 集 / M 镜并行，通过多次 `emit` 推进度，前端按 `shotId` 回填，UI 不阻塞。

### 6.4 剪辑台导出契约（复用 manju_craft 的草稿构建器）
**已确认 manju_craft 内部已有可复用模块**（读 `hermes-fork/skills/langgraph_agents/agents/manju_craft/graph/`）：
- `services/jianying.py::build_draft_and_zip(shot_results, shots, project_dir, resolution, fps)` — 现成草稿构建器，内部已处理：微秒 timerange（`_us()`）、视频/音频/字幕三轨、素材 `shutil.copy2` 进 `assets/`、输出 `draft_content.json` + `assets.zip`。schema 标记 `draft_version:"10.8"`。
- `nodes/batch_generate_video.py` + `nodes/batch_generate_keyframes.py` — 单镜生成节点（studio 逐镜生成直接复用）。
- `nodes/generate_characters.py` — 角色参考图（即资产库雏形）。

**集成方式（推荐）**：
- `manju_studio` 的 agent.py **import 复用**以上节点与 `build_draft_and_zip`，把它当「零件库」编排多集，最后调一次 `build_draft_and_zip` 出工程。
- `EditConsole` 导出 = 把前端 `studio.timeline`（顺序+时长+转场）传给后端，后端调同一个 `build_draft_and_zip` → **与 manju_craft 产物格式 100% 一致，剪映通开**。
- **不要前端手搓 draft JSON**（原型里的客户端 JSON 仅为演示）；真接入时走 IPC 调后端 builder，避免两份格式漂移。
- 写盘由后端 `build_draft_and_zip` 完成（已含 copy2），无需前端 fs。

**共存关系**：
- UI：skill 面板里 `manju_craft`（快出单视频）与 `manju_studio`（项目工作台）并列，用户按需选。
- 产物：两者都产 `draft_content.json`，剪映打开方式相同。
- **前置条件（已实现 / 已核实）**：
- **图片上传到 Hermes 已打通**（验证链：`useAgentStream.js` `splitInlineImages`→`images` 字段 → `agui-server.js` `attachTurnImages`→`image.attach_bytes`→session `attached_images`→`prompt.submit` 转 native `image_url`；`main.js` `read-local-image` 读盘为 dataURL）。资产参考图走此通道即可进 keyframe 生成，跨镜一致性**无上传阻碍**。
- **仍需配置（非代码 gap）**：Agnes 须显式声明 `image_input_mode: native`（`config.yaml` / `default-config.yaml`），否则 `decide_image_input_mode` 在 auto 下静默降级为 text，模型看不到像素（见 `FRONTEND_RENDERING_LAYER` 视觉验证小节）。
- **通用文件 `upload-file` IPC**（`main.js:564`）会把任意文件 copy 到 `HERMES_HOME/uploads/<sessionId>/` 并返回 `localPath`，供 agent 按路径读取；非图片资产（如参考视频/脚本）走此通道。

> 后端只负责「按契约 emit 事件 + 产出文件」，不关心前端怎么画。前端只消费事件。这是本 spec 解耦的核心。

---

## 7. 分阶段实现建议

### MVP（建议第一步）
- `PhaseStepper` + `ContractForm`(script) + `EpisodeGallery` + 复用 `TaskPanel`。
- 资产库 v1：用 `manju_assets` workflow 生成，结果以 `AssetCard` 网格展示（暂不支持分镜内 @mention 引用，仅展示）。
- 分镜编辑器 v1：简化为「每集一个视频卡 + 进度」，不做三列逐镜编辑。
- 验证「manifest 声明 studio → 渲染工作台，不声明 → 原路」这条红线。

### v2（富交互）
- `StoryboardEditor` 三列逐镜编辑 + 单镜独立生成 + `shot_*` 事件回填。
- 分镜内引用资产（`@mention` 或下拉选参考图）。
- `AssetLibrary` 支持编辑/删除/重生成。
- `EditConsole` 剪辑台：shot 条拖拽排序 + 每镜时长 + 转场 + 导出剪映工程（前端构建 draft JSON + 拷素材）。

### v3（可选）
- 阶段条 + 资产库持久化到 `HERMES_HOME`，跨会话恢复。
- 批量「一键派 N 集」按钮（循环 `createTask`）。

---

## 8. 开放问题（待 lex 拍板）

1. **资产引用方式**：分镜里怎么「绑」角色？选项 A 下拉选已有资产卡；选项 B `@mention` 文本语法（需 parser）。MVP 建议 A。
2. **子 workflow 调用形态**：`manju_assets` 是独立 manifest（用户手动先跑）还是被 `manju_studio` 内部自动调用？建议 v1 独立、v2 内部自动。
3. **StudioView 入口**：在 SkillPanel 里点开，还是聊天界面里自动切换？建议 SkillPanel 内嵌。
4. **是否要阶段持久化**（v3）先不做，确认 MVP 不阻塞。

---

## 9. 一句话总结

本 spec 把「截图里的富工作台」拆成 **5 个新增专用组件（含 `EditConsole` 轻量剪辑台）+ 1 个 manifest 可选块 + 一组 studio 专属事件**，全部挂在现有契约之上，**不触动通用渲染器的零改动承诺**。普通 workflow 完全不受影响；声明了 `studio` 的 workflow 自动获得工作台视图。剪辑台导出复用 `manju_craft` 已有的剪映草稿能力，只是把「黑盒自动出片」升级为「用户可粗排后再导出工程」。
