# manjucraft_agent — LangGraph 漫剧 Agent 规格书（v1）

> 状态：待确认（spec 阶段，未动手建代码）
> 范围：单条漫剧 **+ 多集长剧（series 模式）** 均已纳入 v1；episode 编排层设计见 §3.4。
> 目标：新建独立 LangGraph agent，**不复用 / 不修改** 现有 `manju_craft`，用 LangGraph 干净重做漫剧工作流（脚本→视频+剪映草稿），并一次性修掉旧包已确认的技术债。

---

## 1. 目标与范围

- **做什么**：输入剧情脚本 → 输出竖屏漫剧成片（mp4）+ 剪映草稿（json）+ 素材包（zip）。
- **能力边界**：
  - ✅ 单条漫剧（一次运行 = 一条视频），分镜数由 LLM 解析决定（无硬上限，受 Agnes 单次额度/时长约束）。
  - ✅ **多集长剧集（series 模式）**：一次运行 = N 集连载，跨集角色一致性由「角色圣经 `character_bible`」锁定，支持集间/集内崩溃续跑（详见 §3.4）。单条漫剧即 `total_episodes=1` 的特例，两套能力由同一张外层图承载，**无需改运行时**。
- **非目标**：不碰旧 `manju_craft` 包；不改动 LangGraph 运行时 `langgraph_runtime.py` 的通用加载逻辑（新 agent 只需符合其已有约定：导出编译好的 `graph` 即可，series 模式通过输入 `mode` 区分）。

## 2. 命名与定位

| 项 | 值 | 说明 |
|---|---|---|
| 目录 | `hermes-fork/skills/langgraph_agents/agents/manjucraft_agent/` | 全新独立包 |
| manifest `id` | `manjucraft_agent` | 运行时/契约以此为准 |
| 前端展示标签 | `manjucraft-agent`（漫剧生成 v2） | 仅展示用，可与 id 不同 |
| 后端 skill | Hermes `langgraph-agents`（与旧一致） | 经 `langgraph_agent` 工具调用 |
| 旧 `manju_craft` | **保留不动** | 本次不扩展、不删除，灰度并存 |

> 注：Python 包目录不能含连字符，故内部统一用下划线 `manjucraft_agent`；所有内部子模块加唯一前缀 `mc_`（如 `mc_graph`、`mc_series`、`mc_state`、`mc_services.*`），避免与同进程其它 agent 的顶层模块名（`graph` 等）碰撞。

## 3. LangGraph 架构（两层图：外层 series 编排 + 内层单集管线）

> 统一设计：单条漫剧 = `total_episodes=1` 的特例。两层图共用同一个 `agent.py` 导出的 `graph`（外层编排图），内层单集管线作为 **subgraph** 被外层 `run_one_episode` 节点调用。无需修改 `langgraph_runtime` 通用约定。

### 3.1 外层编排图（series orchestrator，StateGraph）

```
START
  → plan_episodes        # 拆分系列脚本 → 每集脚本 + total_episodes；决定一致性策略
  → run_one_episode     # 调内层 subgraph 产出第 current_episode 集（带 character_bible）
  → [conditional edge]  # current_episode < total_episodes ? 回 run_one_episode(current+1)
  →                    # 否则 → finalize_series
  → finalize_series     # 汇总所有集 → 系列成片包（playlist + 全集 zip）
  → END
```

- 循环用**条件边**实现（LangGraph 标准 loop 模式）：`run_one_episode` 完成后按 `current_episode` 是否到顶决定回到自身还是进 `finalize_series`。
- 外层图编译带 `MemorySaver` checkpointer（key=`series_thread_id`），保证**集间续跑**（见 §3.4）。

### 3.2 内层单集管线（subgraph，13 节点，直线流 + 3 个 interrupt 门）

```
START
  → parse_script              # 剧本解析：LLM 拆分成镜 + 角色（首集生成，续集复用 bible）
  → generate_characters      # 首集生成角色首图 ref_image；续集跳过（用 bible）
  → gate_first_frame         # [interrupt] 首帧确认（首集锁定 bible）
  → batch_generate_keyframes # 批量出分镜图
  → consistency_check        # 感知哈希一致性评分
  → gate_each_scene          # [interrupt] 分镜确认
  → fix_drift                # 漂移修正（真阻塞，见 §8.9）
  → batch_generate_video     # 批量出分镜视频
  → generate_tts             # 逐镜配音
  → merge_and_concat         # ffmpeg 合成成片
  → generate_jianying_draft  # 导出剪映草稿 + 素材 zip
  → gate_end                 # [interrupt] 成片确认
  → finalize                 # 汇总本集输出
  → END
```

- 内层图独立编译（自带 MemorySaver，key=`episode_thread_id`），被外层节点 `run_one_episode` 通过 `episode_graph.ainvoke(input_state, config)` 调用；输入 = `{episode_scripts[current_episode], character_bible, style, ...}`。
- 两层各自 checkpoint 命名空间隔离，互不干扰。

### 3.3 State schema（TypedDict，分外层/内层，修复债务）

**外层 series state**（外层图持有，跨集持久化）：

```
series_script: str
series_name: str
total_episodes: int
current_episode: int
character_bible: list[Character]   # ★ 首集锁定的角色 ref_image，跨集透传保证一致性
episode_scripts: list[str]         # plan_episodes 拆分结果
episode_results: list[EpisodeResult]  # 每集 video/jianying/zip 路径 + status
consistency_policy: str            # 'lock_bible'（默认）| 'per_episode'
style: str
api_key: str
status: str                        # 仅日志
```

**内层 episode state**（内层 subgraph 持有，单集内持久化；相对旧包新增/修正标 ★）：

```
script: str                        # = episode_scripts[current_episode]
api_key: str
project_name: str
style: str
characters: list[Character]        # 首集=生成；续集=来自 character_bible（只读）
shots: list[Shot]                  # index/description/dialogue/duration/prompt/video_prompt
shot_results: list[ShotResult]     # keyframe_*/video_*/tts_*/consistency_score/status
current_shot_index: int            # ★ 逐镜更新（旧包形同虚设）
completed_shots: int               # ★ 逐镜更新
total_shots: int
status: str                        # 仅日志，不参与控制流
final_video_path / jianying_draft_path / assets_zip_path: NotRequired[str]
steer_notes: NotRequired[str]      # ★ 审批时带回的修改意见，正式声明并在首帧/分镜节点消费
stop_requested: bool
max_retries: int
```

- `agent.py` 导出：`graph = build_series_graph().compile(checkpointer=MemorySaver())`（外层，必须带 checkpointer 才能 HITL/续跑）、`episode_graph = build_episode_graph().compile(checkpointer=MemorySaver())`、`build_initial_state(text)`、`build_initial_state_obj(obj)`、`WORKFLOW_STAGES`、`summarize_state(result)`。

### 3.4 Episode 编排层（跨集角色一致性 + 集间续跑）

**跨集角色一致性（character_bible）**
- 第 1 集走完整 `generate_characters` 生成角色首图 `ref_image`；在 `gate_first_frame`（首帧确认）批准后，这些 `ref_image` 被**锁定**写入外层 `character_bible`。
- 第 2…N 集的 `run_one_episode` 将 `character_bible` 作为内层 `characters` 输入，**跳过 `generate_characters`**，直接复用锁定参考图 → 跨集脸/风格一致。
- `consistency_policy='lock_bible'`（默认）：续集强制复用 bible；`'per_episode'`：每集重生成（一致性较弱，供风格 experimentation）。

**HITL 策略（避免 N×3 弹窗轰炸）**
- 第 1 集：3 个完整审批门（首帧/分镜/成片），首帧门批准即锁定 bible。
- 第 2…N 集：跳过首帧/分镜的"角色"审批（一致性已由 bible 担保），仅保留一个轻量 **`gate_episode_ready`**（本集脚本+分镜预览确认）；成片门可选（默认开，可配 `auto_approve_tail`）。
- 具体门集合在 manifest `approval_gates` 按 `mode` 注明（单集模式仍是原 3 门）。

**集间 / 集内崩溃续跑（两级 checkpoint）**
- 外层 `series_thread_id` + 内层 `episode_thread_id` 各自 MemorySaver。
- 崩溃重启：传入相同的 `series_thread_id` → 外层从最后一个完成的节点 checkpoint 恢复（知道跑到第几集）；该集内层也从自己的 checkpoint 恢复（知道跑到第几镜）。
- `character_bible` / `episode_results` 在外层 state，天然随 checkpoint 持久化，恢复后不丢。
- 这是 LangGraph checkpoint + 全局 state 相对"手写脚本"的核心优势（无需手动落盘/轮询）。

**入口与模式区分**
- 输入 `mode: 'single' | 'series'`（默认 `single`）。`single` 即 `total_episodes=1`，走同一外层图（loop 一次即结束），完全向后兼容。
- `series`：`build_initial_state_obj` 读取 `series_script` / `total_episodes` / `consistency_policy` 初始化外层 state。
- 两种模式共用 `graph`（外层编排图），**不改运行时加载约定**。

## 4. 审批门 HITL 设计（contract L4）

- 单集三个门 `gate_first_frame` / `gate_each_scene` / `gate_end`，节点内调用 `langgraph.types.interrupt({...})`。
- **Interrupt payload 形状**（与前端 ApprovalDialog 契约一致）：
  ```json
  { "gate_id": "first_frame", "node": "gate_first_frame",
    "label": "首帧确认", "message": "...", "allowSteer": true }
  ```
- 恢复：`interrupt()` 返回前端决策 dict → `decision=="reject"` 抛 `WorkflowRejected`（运行时转 `workflow.done{status:"rejected"}`）；带 `steerText` → 写入 `state.steer_notes` 并由 `generate_characters`/`batch_generate_keyframes` 在下轮消费（**旧包该字段未声明也未消费，本次修**）。
- **manifest `approval_gates` 如实填写**（旧包是空数组，与代码矛盾，本次修）：
  ```json
  "approval_gates": [
    {"gate_id":"first_frame","label":"首帧确认","allowSteer":true},
    {"gate_id":"each_scene","label":"分镜确认","allowSteer":true},
    {"gate_id":"end","label":"成片确认","allowSteer":false}
  ]
  ```
- **series 模式门差异**（见 §3.4）：第 1 集保留上述 3 门（首帧门批准即锁定 `character_bible`）；第 2…N 集仅保留轻量 `gate_episode_ready`。manifest 在 `approval_gates` 注明两种模式门集合，或前端按 `mode` 动态渲染。
- **input_schema 扩展**（支持 series）：在原有 `script/style/project_name` 基础上新增：
  ```json
  "mode":        {"type":"string","enum":["single","series"],"default":"single","x_ui":{"control":"select","label":"模式"}},
  "series_script":{"type":"string","x_ui":{"control":"textarea","label":"系列脚本（series 模式）"}},
  "total_episodes":{"type":"integer","minimum":1,"maximum":24,"default":3,"x_ui":{"control":"number","label":"集数"}},
  "consistency_policy":{"type":"string","enum":["lock_bible","per_episode"],"default":"lock_bible","x_ui":{"control":"select","label":"跨集一致性"}}
  ```
  `single` 模式忽略 `series_script/total_episodes`；`series` 模式忽略顶层 `script`（改用 `series_script` 拆分）。

## 5. 契约 L5 事件（workflow.*）

复用已验证的运行时事件（旧 manifest 的 `progress_events` 字段名是错的，本次订正）：

| 事件 | 触发点 | payload |
|---|---|---|
| `workflow.progress` | 每个节点开始/结束 + ★ 逐镜进度 + ★ 逐集进度 | `{stage, label, index?, total?, episode?, total_episodes?}` |
| `workflow.artifact` | 每类产物落盘 | `{kind, path}` |
| `workflow.approval` | 命中 interrupt | `{workflowRunId, gate_id, node, label, message, allowSteer, artifacts}` |
| `workflow.error` | 节点异常 | `{message}` |
| `workflow.done` | 图结束 | `{status:"done"|"rejected", artifacts}` |

- 事件经已验证通道回流：Python `on_event`（langgraph_agent_tool 的 `_make_http_emitter`）→ `POST /api/ag-ui/workflow-event` → agui-server 转 CUSTOM → 前端 `useAgentStream.handleCustom("workflow.*")`。
- **ID 一致性（关键）**：决策文件名 = `run_agent` 生成的 `uuid4()` = `workflow.approval` 里的 `workflowRunId`；前端 ApprovalDialog 必须原样回写 `/api/ag-ui/interrupt`，否则落进 1h 超时。新 agent 沿用同一机制，文档化该约束。
- HITL **必须走带 `on_event` 的路径**；无 `on_event` 的同步调用会在 interrupt 处返回 `__interrupted__` 哨兵（假完成），spec 明确禁止非前端场景无 emitter 调用。

## 6. 服务层与 Agnes 接入（修复债务）

- **API key**：沿用 `_get_agnes_credentials()`（env `AGNES_API_KEY` 或 Hermes `config.yaml`），`build_initial_state` 注入 `os.environ["AGNES_API_KEY"]`。
- **Base URL**：`AGNES_BASE_URL` 或默认 `https://apihub.agnes-ai.com/v1`；**视频轮询 URL 由 BASE_URL 派生**（旧包硬编码 `apihub.agnes-ai.com/agnesapi`，本次修）。
- **Mock：依赖注入，不做 import 期 monkey-patch**（旧包 `_apply_smoke_mocks` 全局打补丁会污染同进程其它 agent，本次修）。方案：service 函数接受 `backend` 参数或 `build_initial_state` 据 env `MANJUCRAFT_AGENT_MOCK` 选择真实/假实现，不改动模块级函数。
- 服务子模块放 `manjucraft_agent/mc_services/`（agnes_media / llm / tts / ffmpeg / jianying），唯一前缀避免碰撞。

## 7. 与 agui-server / 前端的集成（接线点清单）

旧 `manju_craft` 的接线在 `electron/backend/agui-server.js`，新 agent 需补齐/并存：

1. **委派识别**：`looksLikeVideoTask()` 关键词已含 `manjucraft`（无需改）。新增映射：`ctx.skillId === 'manjucraft-agent'`（或 mention/delegation 解析到 `manjucraft_agent`）→ `delegatedAgent = 'manjucraft_agent'`（参照现有 `:869` 对 `manju-craft` 的处理）。
2. **prompt 改写**：委派分支把 `agent_name` 改成 `"manjucraft_agent"`（参照 `:898-915`）。
3. **workflow subscriber / 协调文件**：沿用现有 `wf-<runId>` 注册 + `.wf_active_*.json` 机制（无需改，工具侧按 `workflow_run_id` 发现）。
4. **前端工作流面板**：manifest 自动被 `discoverManifests()` 收集，新 agent 会出现在「工作流」列表；结构化调用经 `langgraph_agent` 工具 + `agent_name: "manjucraft_agent"` 触发 HITL 路径。
5. **默认视频工作流**：建议将默认视频委派从 `manju_craft` 切到 `manjucraft_agent`（旧包保留可手动选）；或两者并存由用户选。
6. **series 模式路由**：`looksLikeVideoTask` 关键词增补 `系列/连载/多集/episode`（命中即建议 `mode='series'`）；前端工作流面板在 manjucraft-agent 表单暴露 `mode/series_script/total_episodes/consistency_policy` 字段，结构化调用经 `build_initial_state_obj` 透传，无需改运行时。

## 8. 相对旧 manju_craft 的取舍（修复的债务清单）

| # | 旧债 | 本次处理 |
|---|---|---|
| 1 | manifest `approval_gates:[]` 与 3 个 interrupt 矛盾 | 如实填写（并区分 single/series 门） |
| 2 | 顶层 `graph` 包名 `sys.path` hack，跨 agent 碰撞风险 | 内部模块加 `mc_` 前缀，规则化 import |
| 3 | 视频轮询 URL 硬编码域名 | 由 `BASE_URL` 派生 |
| 4 | import 期 monkey-patch mock 污染全局 | 依赖注入，env 开关选实现 |
| 5 | `steer_notes` 写入但未声明未消费（审批改 prompt 失效） | 声明 + 在首帧/分镜节点消费 |
| 6 | 逐镜进度字段形同虚设 | 节点逐镜更新 `current_shot_index`/`completed_shots` + 发进度事件 |
| 7 | 无 `on_event` 时 HITL 假完成 | spec 明确禁止；冒烟用带 emitter 的路径 |
| 8 | HITL 双 ID 通道脆弱 | 沿用已验证机制并文档化 `workflowRunId` 单一真相源 |
| 9 | `fix_drift` 软降级（写死 0.75、从不真阻塞） | 阈值可配，重算分数；低于阈值进入复审/拒绝而非静默放行 |
| 10 | `status` 语义漂移参与隐含控制 | 仅作日志，控制流靠节点/interrupt |
| 11 | 无多集/跨集能力 | 新增两层图 + `character_bible` + 两级 checkpoint（§3.4） |

## 9. 验证与冒烟方案

- **编译**：`python -m py_compile` 新包所有 `.py`。
- **加载**：`run_agent('manjucraft_agent', script)` 与 `run_agent('manjucraft_agent', input_obj={mode:'series', series_script, total_episodes:2})` 都能被 `langgraph_runtime` 发现并编译。
- **冒烟（mock，single）**：设 `MANJUCRAFT_AGENT_MOCK=1`，harness 调 `run_agent(..., on_event=auto_approve)`——`auto_approve` 收到 `workflow.approval` 自动回 `Command(resume={"decision":"approve"})`，验证全 DAG 跑通无 Interrupt 序列化错误。
- **冒烟（mock，series）**：同上但 `mode='series', total_episodes=2`，验证 `character_bible` 在第 1 集锁定、第 2 集复用（不调 `generate_characters`）、`episode_results` 累计 2 条、两级 checkpoint 可恢复。
- **冒烟（真实）**：有效 Agnes key + 不设 mock，经前端入口跑一条 6 分镜脚本（single）验证 3 次审批弹窗→确认→成片；真实消耗额度。series 真实跑需更大额度，单独评估。
- **契约校验**：`GET /api/ag-ui/contract/manifests` 返回新 agent 的 `approval_gates` 非空、`input_schema` 含 `mode/series_script/total_episodes/consistency_policy`，且与 graph 实际门一致。

## 10. 交付物文件树

```
hermes-fork/skills/langgraph_agents/agents/manjucraft_agent/
├── __init__.py
├── manifest.json                # id=manjucraft_agent，approval_gates 真实（含 series 差异），input_schema 含 mode/series_*
├── agent.py                     # 导出 graph(外层)=build_series_graph().compile(...)、episode_graph、
│                               #   build_initial_state(_obj)、WORKFLOW_STAGES、summarize_state
├── mc_state.py                  # SeriesState / EpisodeState TypedDict（含 steer_notes、character_bible）
├── mc_graph.py                  # build_episode_graph()：内层 13 节点 subgraph + 3 interrupt 门
├── mc_series.py                 # build_series_graph()：外层编排（plan_episodes/run_one_episode/finalize_series + 条件边）
├── mc_nodes/
│   ├── parse_script.py
│   ├── generate_characters.py
│   ├── approval_gate.py         # 首帧/分镜/成片 3 门 + 续集 gate_episode_ready
│   ├── batch_generate_keyframes.py
│   ├── consistency_check.py
│   ├── fix_drift.py
│   ├── batch_generate_video.py
│   ├── generate_tts.py
│   ├── merge_and_concat.py
│   ├── generate_jianying_draft.py
│   ├── finalize.py              # 内层单集汇总
│   ├── plan_episodes.py         # 外层：拆系列脚本 → episode_scripts + total_episodes
│   ├── run_one_episode.py       # 外层：调 episode_graph.ainvoke（注入 character_bible）
│   └── finalize_series.py       # 外层：汇总所有集 → 系列成片包
└── mc_services/
    ├── llm.py                   # parse_script_to_shots（支持 mock 注入）
    ├── agnes_media.py           # 生图/生视频，URL 由 BASE_URL 派生，支持 mock
    ├── tts.py
    ├── ffmpeg.py
    └── jianying.py
```

> 接线改动（最小、并存）：`electron/backend/agui-server.js` 委派分支新增 `manjucraft_agent` 映射（§7.1–7.2），`looksLikeVideoTask` 增补 series 关键词（§7.6）。旧 `manju_craft` 包与现有接线**保持不变**。
