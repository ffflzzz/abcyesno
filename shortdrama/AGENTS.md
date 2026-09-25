# AGENTS.md — AI 短剧生成工厂 · 外部 Agent 调用规范

> 本文件面向调用本项目的**外部 Agent**（WorkBuddy / Codex / 其他编排器）。
> 你的角色是**项目经理**：解析用户需求 → 写 brief.json → 调 CLI → 验收产物。
> **本文件是仓库里唯一的 `AGENTS.md`**，只写**当前契约**。
> **第一手事实一律以 `v5/` 代码为准。** 设计依据与事故记录归 git 历史（`git log`），不在此复述。

## ⛔ 调用方准入

| 入口 | 状态 | 说明 |
|---|---|---|
| 全链路（`scripts/run_new_project.py`） | ✅ **默认可用** | 推荐路径：写 brief.json → 跑脚本 → 验收产物 |
| `--monitor` / `--watch` | ✅ 可用 | 只读观察：不写盘、不推进图 |
| `--rerender <镜号>` | ✅ **默认可用** | **单镜重渲**。媒体链唯一入口的**受限调用**（gate / 记账 / 幂等 / 限流全部照常），故无需放行。`--from still` 连静帧一起重做 |
| `--resume-media` / `--stills-only` | ⛔ **默认拒绝** | 需 `SHORTDRAMA_OPEN_CHAIN=1`（开放全链路）或 `SHORTDRAMA_ALLOW_RESUME=1`（单次恢复） |

**三道输入门**（`--resume-media` 路径强制过；`series._input_gates`）：

1. **brief 门**（`_brief_gate`）—— 缺必填字段 → `BRIEF-REJECT`
2. **分镜契约门**（`_storyboard_gate`）—— 缺列 / 镜序错乱 / 空对白 / 要求画内文字 /
   未覆盖 `must_have` / 违反 `audio_mode` / **镜内节拍不自洽**（写了 `0-2秒：` 分段的镜
   必须从 0 起、首尾相接、终于本镜时长）→ `STORYBOARD-REJECT`
3. **资产契约门**（`_assets_gate`）—— 分镜点名的角色在资产注册表里没有身份锚点 → 阻断
   （纯道具 / 空镜项目不适用）

**仍然禁止**（这些绕过一切守卫，破坏的是可复现性，不是"权限"）：
手改 `projects/<名>/.agent_state.json`、手删 `clips/*.mp4`、手喂 `scenedesigner.md` 直接渲染。

## 这个项目是什么

多智能体 AI 短剧生成工厂，实现在 **`v5/`**。
**创作链** = supervisor 架构（LLM 编排，按依赖序派发角色图）；
**媒体链** = 确定性流水线，**在图外**，唯一入口 `v5.media.pipeline.run`
（带独占锁 `media/ep{N}/.running`，陈旧锁靠心跳 mtime 接管，`SHORTDRAMA_MEDIA_LOCK=0` 可关）。

## 常用命令

```bash
# 在仓库根目录执行（本文件所在目录）

# ── 新项目全链路（推荐入口：创作链 → 评审 → 媒体链）
python scripts/run_new_project.py <项目名> [--stills-qc | --no-qc] [--chain-only] [--fresh]

# ── 多集连载（一条命令跑 N 集，**一次起服**）
python scripts/run_new_project.py <项目名> --episodes 1-3 --stills-qc
python scripts/run_new_project.py <项目名> --episodes 1-3 --chain-only   # 只创作链（不烧配额）
SHORTDRAMA_OPEN_CHAIN=1 python -m v5.series <项目名> --resume-media --episodes 1-3

# ── 媒体链（出片，唯一入口）/ 只读观察
SHORTDRAMA_OPEN_CHAIN=1 python -m v5.series <项目名> --resume-media
SHORTDRAMA_OPEN_CHAIN=1 python -m v5.series <项目名> --resume-media --stills-only
python -m v5.series <项目名> --monitor [--seconds 600]

# ── 单镜重渲（默认放行）
python -m v5.series <项目名> --rerender LN03 --note "周奶奶没出现"
python -m v5.series <项目名> --rerender LN03 --from still   # 连静帧一起重做
python -m v5.series <项目名> --rerender LN03,LN05           # 多镜（逐个排队）

# ── 审批门（**默认关**，需 SHORTDRAMA_REQUIRE_APPROVAL=1 才生效）
python -m v5.series <项目名> --approvals
python -m v5.series <项目名> --approve storyboard --by 姓名 --note 依据
python -m v5.series <项目名> --revoke stills

# ── 步级 HITL（**默认关**，需 SHORTDRAMA_APPROVE_EACH_ROLE=1 才生效）
# ⚠️ 与上面的「审批门」是**两件事**（别混）：
#   · 审批门   = 阶段**之间**的门，查产物是否合格才放行（`--approvals` 系列）
#   · 步级 HITL = 图**运行中**每次派发子代理前 interrupt，靠 resume 继续（信道具见 `v5/hitl.py`）
python -m v5.series <项目名> --hitl                        # 看当前挂起在哪一步
python -m v5.series <项目名> --hitl-approve --by 姓名 --note 依据
python -m v5.series <项目名> --hitl-reject  --note "原因"   # 拒绝 ⇒ 链路终止
python -m v5.series <项目名> --hitl-redo --target 角色 --note "原因"
#   ↑ 打回：重跑「该角色 + 它的**全部下游**」（旧产物移入 `.rerun_backup/` 后重跑）。
#     `--target` 省略 = 打回**刚产出的那个**角色（= pending 里的 `prev_role`）。
#     ① 前端网页端另有「继续 / 打回」确认条，走同一套（`GET/POST .../projects/{pid}/hitl`）；
#     ② 前端起的 dev server **自带** `SHORTDRAMA_APPROVE_EACH_ROLE=1`（见 `v5/webchain.py`）
#        ⇒ 网页端**天然逐步停下**，外部 agent 不受影响。
#     ③ ★ 打回目标里**含 `director`**（制作规格 `director/director.md`，前提是它在盘）：
#        链路第一停时人唯一能审的就是它；打回它 = **重写规格 + 全部 7 个角色重做**
#        （规格是整条链的输入）。判据见 `v5/hitl.redo_targets_of`。

# ── 「全手动」到底是什么（2026-09-19 收口）
#   创作链：每次 `task` 派发前停一次，人给「继续 / 打回」（上面那条）。
#   媒体链：每个动作（资产图 / 静帧 / 视频 / 出片）都是**人点才跑**；
#          但**质检自愈**（静帧判硬伤自动重画、成片抽帧自动重拍）默认**关**，
#          由前端「自动质检」开关打开（`SHORTDRAMA_STILL_QC` / `SHORTDRAMA_CLIP_QC`
#          随请求进子进程 env）。
#   出片：`POST /v1/pixa/short-drama/episodes/{eid}/compose`（= `runner.KINDS["episode"]`
#        → `pipeline.run` **不带 `only`** 的整片形态）。它是**唯一**允许不带 `shots`
#        的任务类型；静帧/视频已在盘上的按磁盘事实复用，不重复烧配额。

# ── ⚠️ 改前端那条链路前先看一眼：哪些东西是**两条链共用**的（2026-09-19 查证）
#   共用（改了会同时影响 CLI / 外部 agent —— 有意改就改，但要**知道**）：
#     · supervisor 的提示词：`ORCHESTRATOR_DISCIPLINE`（编排纪律，含「第 0 步：先落制作规格」）
#       + `roles.director_system_prompt`（= 类型包自己的 director SKILL）
#     · 子代理清单：**7 个角色全部派发**（`make_sync/async_subagents`，worldbuilder 在内）
#       ⇒ 第 1 集比"director 代写"时期多一次 `task`（这是刻意的：per-role 契约必须生效）
#     · `guards.PREREQ` / `GATE_ROLES` / `guards.media_gate` —— 门的**判据**（唯一一份）
#     · `hitl` 的 `pending.json` / `decision.json` 契约（`redo_targets` 现含 `director`）
#     · 每次过门写 `media/ep{N}/gates.json`（**新增文件**；CLI 路径也会写）
#   不共用（只在前端路径生效，不改默认行为）：
#     · `SHORTDRAMA_HUMAN_IN_CHARGE=1`（门降级为报告）—— 只由 `media/runner.start` 设
#     · `SHORTDRAMA_STILL_QC` / `CLIP_QC` 的**前端默认 0**（CLI 不传 ⇒ 仍是 config 默认 1）
#     · `runner.KINDS["episode"]` 与 `POST /episodes/{eid}/compose`（新路由）
#     · `progress` / `storyboard_detail` 里新增的 `v5.gates` / `render.final_url` 等字段

# ── 创作链 dev server（需在 Studio / SDK / 前端里驱动时）
# 注册 9 张图：supervisor + role_* ×7 + media_rerender
SHORTDRAMA_V5_PROJECT=<项目名> .venv/Scripts/langgraph.exe dev \
    --config v5/langgraph.json --host 127.0.0.1 --port 2024 --no-browser
```

> ⛔ **`--brief` / `--pack` 不可用**（传了进程直接退出）。
> 驱动创作链见上面的 dev server 段。

| 参数 | 说明 |
|---|---|
| `<项目名>` | 英文标识（如 `paper-crane`），产物在 `projects/<项目名>/` |
| `--ep N` | 单集（默认 1）。集号进产物路径：`*_ep{N}.md` / `media/ep{N}/` |
| `--episodes SPEC` | **按集跑 N 集**：`1-3` / `3` / `1,3,5` / `1-3,7`。`run_new_project` 上为全链路、`series` 上需配 `--resume-media`。**任一一集被门拦下即停止**（不静默跳过）|
| `--fresh` | 归档**整个项目**的产物后重跑。**与多集互斥**（脚本 `rc=2` 拒绝）|
| `--resume-media` | 断点续跑媒体链（需 `SHORTDRAMA_OPEN_CHAIN=1` 或 `SHORTDRAMA_ALLOW_RESUME=1`）|
| `--monitor` / `--watch` | 只读观察：不写盘、不推进图 |
| `--rerender` | 单镜重渲（`--from still` 连静帧；逗号分隔多镜）|
| `--approvals` / `--approve` / `--revoke` | 人工审批门（**阶段之间**；指纹绑定产物，上游一变审批自动作废。需 `SHORTDRAMA_REQUIRE_APPROVAL=1`）|
| `--hitl` / `--hitl-approve` / `--hitl-reject` / `--hitl-redo` | 步级 HITL（**图运行中**的 interrupt；需 `SHORTDRAMA_APPROVE_EACH_ROLE=1`）。`--hitl` 查挂起状态，后三个写决定（配合 `--by` / `--note` / `--target`）。**与审批门是两件事**，见上文命令段 |
| `--target` | 配合 `--hitl-redo`：打回哪个角色。**只能填本步之前已完成的角色**（填别的直接报错，不会静默）|
| `--events N` | 配合 `--monitor`：最多快照 N 次（默认 1 次，不阻塞）|
| `--bind-only` | 只写 `episode_index` 后退出（脚本用；不跑任何链路）|

### ★ 多集连载的三条硬契约（写 brief / 派活前必读）

1. **`--fresh` 与多集互斥**（脚本直接拒绝 `rc=2`）：`--fresh` 归档的是**整个项目**的产物目录，
   会把**别的集**一起挪走。它只用于"**同一集**上一轮被打断、要重跑"。
2. **全剧级产物只在第 1 集生成**：`worldbuilder.md` / `assets.md` / `episodes.md`
   是**全剧级**（一次锁定、全剧复用）；第 2..N 集**自动跳过**这三个角色。
   ⇒ 要改角色卡就**回第 1 集改**，否则跨集错位（上一集的分镜引用旧名字，
   而参考图不会跟着重画）。
3. **brief 字段在多集下语义不同**（`episodes > 1` 时 `_brief_gate` 会主动提示）：
   `must_have` = **每集都能覆盖**的硬要求（**不是全剧四幕**）；
   `target_duration` = **每集**时长（不是合计）；全剧起承转合与 `结局`
   写进 `plotdesigner` 目录的卷首。

**集数多时的切片注入**：全剧级目录超 `SHORTDRAMA_SLICE_THRESHOLD`（默认 4000 字符）即
**按集切片**注入（只注本集 ±1 + 本卷摘要；切片**可见**、失败默认**响亮终止**，
`SHORTDRAMA_SLICE_SOFT=1` 降级为注入全文）。⇒ `plotdesigner` 的产物必须按
「`## 第 N 卷` + `### 第 M 集`」结构写，且**集条目自包含**（禁"同上/见前文"，切片后会悬空）。

## brief.json 规范（你的核心产出）

**写到 `projects/<项目名>/brief.json`** —— 脚本按这个路径读。
⚠️ **该路径读不到时不会报错**，而是**静默用默认类型包 `shortdrama`** ——
你写的 `pack` 会被无声忽略（与下面第 4 条要点同型）。

brief 是外部 Agent 唯一的强杠杆——它决定"拍什么"，而画质审美由类型包决定。

### 质量门与注入预算

1. **brief 有 3000 字符注入预算**，按字段优先级智能分配：放不下时**先丢低价值字段**
   （`topic` / `genre` / `reference_photo`…）并打印省略清单；**核心字段**
   （`must_have` / `key_props` / `禁忌` / `protagonist` / `结局`）放不下则直接报
   `BriefTooLarge` 并终止——不会静默丢内容。输出始终是合法 JSON。
2. **`FIDELITY` 门是"忠实度"门不是"质量"门**：它只校验产物有没有覆盖 `must_have`，
   **不校验 brief 本身好不好**。烂 brief 被忠实执行 = 烂成片，门照样过
   ——质量责任在外部 Agent 一侧。
3. **`audio_mode` 会真的生效**：开工前注入 `dialogue` / `scenedesigner` 的输入，
   reviewer 节点按分镜对白列做**确定性**校验——`dialogue-led` 而台词镜占比 <20%
   直接判不通过并回退 scenedesigner；`silent` 却有台词同样回退。
   占比 20%~50% 只警告不阻断。违规会回退，**不会静默放行**。

### 字段表

| 字段 | 必填 | 说明 |
|---|---|---|
| `topic` | ✅ | 主题名（项目标识） |
| `pack` | ✅ | 类型包名，决定审美与角色 skill 回落基准 |
| `genre` | ✅ | 类型基调（温情/悬疑/喜剧…） |
| `episodes` | ✅ | 集数 |
| `target_duration` | ✅ | **总时长目标 + 镜数指引**。**每镜秒数不写死**——注明「按剧情节拍 **4-12s** 灵活分配（快切蒙太奇可到 2s），避免全表等长」 |
| `空间数要求` | 建议 | **≥2 个视觉上明显可区分的空间**（不同地点／不同时段／不同光色）。不写时模型倾向把全片拍在同一处换机位（brawl 实测：4 个场景名都在同一条后巷 → 成片看着像"一场戏打完"） |
| `分镜格式硬要求` | 建议 | 画面描述要**按镜内节拍写**（`0-2秒：…；2-6秒：…`，0 起/首尾相接/终于本镜时长）、每拍写**身体朝向**与**画面内**视线落点。★ 这些要求**必须写进 brief 才生效**——只写在包 SKILL 里执行率 1/18，写进 brief 是 20/20（brawl 实测） |
| `protagonist` | ✅ | 主角设定（外貌逐项）+ 形象依据声明 |
| `must_have` | ✅ | 写法**取决于集数**。**单集**：四幕结构（钩子/发展/转折/收尾），每幕一个具体可拍事件。**多集（`episodes`>1）**：**每集都能覆盖**的 2–3 条硬要求，**不是全剧四幕**（写全剧四幕会让**每一集**都被 FIDELITY 门拦下）|
| `key_props` | ✅ | 关键道具：精确命名 + 外观逐项特征（跨镜一致性的锚点） |
| `禁忌` | ✅ | 硬约束——静帧 QC 的 P0 判据来源 |
| `on_screen_text` | 建议 | `allow`（默认）/ `forbid`。默认**只禁烧录型文字**（成行字幕条、元指令文字），**场景固有文字**（门牌 / 面板读数 / 文件抬头 / 包装标签）**放行**；`forbid` = 画面里**任何**可读文字都算硬伤（怕平台审核时用）。 |
| `tone` | ✅ | 渲染风格 + 光线色彩叙事 + 情绪弧 |
| `结局` | ✅ | 定格画面描述 |
| `audio_mode` | 建议 | `dialogue-led`（**必须有台词**，台词镜 ≥20%，低于 50% 出警告）/ `silent`（不出台词，对白列统一「（无声，环境音）」）/ `narration-led`（旁白推进剧情：旁白写进「音效」列的画外音格式，**「对白」列统一「（无声，环境音）」**）。⚠️ **brief 不写该字段时一律回落 `dialogue-led`**（`validate.audio_mode_of`）——`pack.json` 的 `default-audio-mode` **代码不消费**，想要旁白必须**在 brief 里显式写** |
| `visual-style` | 建议 | 渲染风格声明（按包定义填写） |
| `second_character` | 有配角时 | 第二角色设定，写法同 `protagonist` |
| `reference_photo` | 有照片时 | 用户照片路径（先拷到项目 `images/`） |

**内容字段的值必须按项目内容原创**——照抄示例 = 产出与需求无关。

### 示例（`niulai-movie-style` 包的喜剧短片——值仅为示例，必须按你的项目内容替换）

```json
{
  "topic": "主题名",
  "pack": "niulai-movie-style",
  "genre": "喜剧",
  "audio_mode": "dialogue-led",
  "visual-style": "primitive folk CGI，2005 年国产廉价 3D 游戏过场动画；精致低模 = 失败",
  "episodes": 1,
  "target_duration": "4 分钟以上（不少于 240 秒），共 32 镜 / 284 秒。每镜时长按剧情节拍在 8-10s 内分配，避免全表等长",
  "protagonist": "陈默：粗糙多面体头部、块状黑色短发、矩形手指、黑色圆领 T 恤、深灰色直筒长裤。全片只用这一个固定人名。",
  "must_have": ["第一幕(钩子): …", "第二幕(发展): …", "第三幕(转折): …", "第四幕(收尾): …"],
  "key_props": ["旧 U 盘：磨损金属壳旧式 U 盘（块状几何、低清拉丝金属贴图、USB 口歪斜）——全片逐字一致"],
  "禁忌": ["无字幕、无烧录文字（★ 场景内固有标识——门牌 / 面板读数 / 文件抬头 / 包装标签——**可保留**）", "无第二张清晰人脸", "无背景音乐、无歌唱"],
  "tone": "反向出圈低画质喜剧 + 游戏 bug 元幽默；僵硬节奏即笑点",
  "结局": "陈默举着旧 U 盘对镜头僵硬大笑，画面定格"
}
```

### 写 brief 的六条要点

1. **`must_have` 四条硬纪律**（门算的是**字符二元组覆盖率**，
   `validate._content_bigrams`）：
   - ① **禁负面措辞**（"不得/不解释/不总结"）——产物的画面描述不会写这些词，
     那些二元组永远匹配不上，**纯粹稀释分母**。负面约束写进 `禁忌`（门不测）。
   - ② **必须用产物会字面写出的词**：**具体物件名 + 角色名**（分镜的画面描述会点名它们）。
   - ③ **禁抽象状态/意图词**（"推进笑点/未收尾"）——抽象意图写进 `tone`（门不测）。
   - ④ 用**空格**把短语分开（`每集 巴赫 与 抄谱员 面对面说话 至少 2 处`）会**提高**命中率。
2. **叙事信息走对白或画面动作，禁写「画面出现文字/刻字/屏幕内容」**——当前视频模型
   渲染不准，会被静帧 QC 判 P0。
3. **`禁忌` 按项目预判**：多角色项目加「无第二张清晰人脸」；`silent` 模式加「无口型动作」
   ——**`禁忌` 是静帧 QC 的 P0 判据来源**。
4. **道具命名精确且逐字一致**（如「深蓝色丝绒戒指盒」）——近义词漂移 = 参考图绑不上 = 穿帮。
5. **用户照片作主角时**：先拷到 `projects/<项目名>/images/`，brief 里声明形象唯一依据；
   照片会进 `assets.json` 注册表。
6. **改 brief 措辞后不必重跑创作链**：`FIDELITY` 门在**媒体阶段**才判
   ⇒ 跑 `--resume-media` 即可。

## 类型包

| 包 | 风格 | 音频模式 | 关键开关 |
|---|---|---|---|
| `shortdrama`（默认）| 写实电影感、竖屏真人质感 | silent / dialogue-led | **回落基准**：缺 `pack.json`；有 `style-block.md`（`still-tail` 走缺省 `material`）|
| `niulai-movie-style` | primitive folk CGI（故意低质的 bootleg 3D，**精致 = 失败**）| silent / dialogue-led / narration-led | `still-refs: false`、`still-tail: flat`、`style-is-criterion: true` |
| `wool-felt-story-short` | 现实世界比例的羊毛毡世界（**精致 = 正确**）| dialogue-led / silent | `still-refs: true`、`still-tail: material`、`style-watch: true`、`hard-keys-drop: [五官, 面部特征]` |
| `chinese-style-short-drama` | 真人国风短剧（BJD 瓷肌 + 华丽古风妆造）| dialogue-led / silent | `still-refs: true`、`still-tail: material`、`style-watch: true`；**不开** `style-is-criterion`。**未实测**，首跑需按换包验收走一遍 |
| `laofuzi-hk-retro` | 老夫子 IP 老港片复古风 | 按包定义 | 4 个角色（`director`/`worldbuilder`/`assetdesigner`/`reviewer`），其余回落 `shortdrama`。⚠️ 含第三方 IP，**商用需授权** |
| `half-narrated-live-action` | 半解说真人短剧（旁白推进剧情 + 对白补情绪）| **narration-led** / dialogue-led / silent | `still-refs: true`、`still-tail: material`、`style-watch: true`；启用商业三件套 `script-craft`（**不含**微表情）|
| `madfate-grim` | 命案·都市残酷惊悚（gritty 现代老城）| **narration-led** / silent / dialogue-led | `still-refs: true`、`still-tail: material`、`style-watch: true`、**不开** `style-is-criterion`；**刻意不启用**商业短剧 `script-craft` |

**`pack.json` 里真正生效的只有七项**：`still-refs` / `still-tail` /
`style-is-criterion` / `style-watch` / `visual-style` / `hard-keys*` / `script-craft`。
**其余字段（`trigger-words` / `aspect-ratio` / `shot-duration` / `default-*-model` /
`audio-modes` / `default-audio-mode` 等）后端不消费**（`name` / `display-name-zh` /
`audio-modes` 只被 `webmap` 读去给前端展示），填了不影响出片（完整字段表见 `README.md` §4）。

- **`still-tail`**：`material`（真实连续材质）/ `flat`（平涂纯色块）/ `none`，缺省 `material`。
  **写实与 3D 包必须用 `material`**；`flat` 只给反质量包用。
- **`style-is-criterion`**：风格当**硬判据**（可判 P0）。⚠️ 其注入文本写死了
  「反质量类型包」前提，**只给反质量包用**——追求真实质感的包请用 `style-watch`
  （只记不改、只许报 P1），否则判据方向相反会稳定误判。

**角色 skill 回落**：某包缺某角色的 `SKILL.md` 时自动回落到 `shortdrama` 同名角色，
这是设计内的（`niulai-movie-style` 只定义 5 个角色）。

**另有 `craft/`——不是类型包，是技法库**：`v5/skills/packs/craft/<技法名>/SKILL.md`
（当前 **5 个**：`pixar-lighting` / `short-drama-hooks` / `short-drama-opening` /
`short-drama-satisfaction` / `micro-expression-acting`）。它**没有 `pack.json`、不定义角色**。
**已接线**（`v5/media/style.py` `script_craft_of()`/`read_craft()` + `v5/roles.py` 注入）：
brief.json 的 `script-craft` 列表（项目级）> pack.json 的 `script-craft`（包级默认），
**opt-in、未声明不注入**；技法 frontmatter 的 `inject-to: [...]` 指定注入角色，
漏写=不注入任何角色并打告警；技法名无效**响亮报错**。

**导入新类型包**：在 `v5/skills/packs/<新包名>/` 建角色 `SKILL.md` + `pack.json`
+（有强审美主张时**务必**）`style-block.md`（逐镜注入，是画质的主要锁定手段）。
**不要有损提炼**——渐进披露支持大文档，细节放 `references/` 按需读。

**新包首跑必做**：① 抽查一镜的 `media/ep{N}/stills.json` → `prompt`
是否含本包风格块特征词；② 抽帧人眼看，并做新旧并排 A/B。
**"日志全绿 + 有 mp4" 不等于包生效。**

## 媒体管线

**视频生成模式 `VIDEO_MODE`（官方规定 `keyframe` 与 `reference` 互斥，同一请求不能混用）**：

| 模式 | 图怎么用 | 允许的媒体字段 | 不允许 |
|---|---|---|---|
| **`reference`（当前默认）** | 静帧进 `images`，提示词里作 `<Picture 1>` | `images`(≤5) / `audios`(≤3) | `first_frame`、`last_frame` |
| `keyframe`（回退档） | 静帧当首帧，构图被锁死 | `first_frame` / `last_frame` | `images`、`audios` |
| `pack`（2026-09-22 落管线） | **相邻同场景镜打包**成一条 ≤12s 的 reference 请求（每镜一张静帧、逐拍 `<Picture i>` 点名+时间边界）；job 与产物都是**组级**（`clips/packNN.mp4`），提交前自动生成跨组接缝静帧预检图 `seam_preview.jpg`（人眼扫，不阻断） | 同 `reference`（≤5 张/请求 ⇒ 组上限默认 5） | 同 `reference` |

分组算法在 `media/video_plan.group_shots`（同场景相邻贪心、≤12s、压缩保台词下限）；
打包 prompt 在 `media/prompt.build_pack_prompt`；旁路脚本 `scripts/pack_render.py`
保留为独立验证入口，两处判据改动必须同步。pack 档**跳过**逐镜 clipqc（组产物多镜
合并、逐镜判据不适用）与落幅帧预生成。

回退：`SHORTDRAMA_VIDEO_MODE=keyframe`。

**渲染放行条件**：媒体链**不在图内**；supervisor 图跑完后由 `series` 调
`pipeline.run(root, ep=ep)`。是否真的渲染，由收在 `pipeline.run` 内部的
`guards.media_gate("render")` 判定——它要求**被派发的 7 个角色全 `complete`
＋ 评审 `passed` 或 `force_passed`**，缺一不可（`guards.GATE_ROLES = tuple(PREREQ)`，
**不含 `director`**）。

⇒ **外部 Agent 触发媒体链的推荐方式**：单镜用 `--rerender`（默认放行）；
全量出片走 `--resume-media`（需放行）。

**媒体层依赖四份输入**，质量直接决定成片：

| 输入 | 作用 |
|---|---|
| `scenedesigner/scenedesigner_ep{N}.md` | 分镜表（**集级**）；字段越全（景别/角度/运镜/落幅）提示词越准 |
| `worldbuilder/worldbuilder.md` + `assetdesigner/assets.md` | **参考图的唯一依据**：`cast.ensure` 按卡片确定性生成角色/资产图 |
| `assets.json` | 角色/道具身份锚点（`identity`）+ `ref_ver` 参考图版本 |
| `brief.json` 的 `pack` | 决定 `style-block.md` 与参考图开关（**写错包名 = 静默退回朴素提示词**）|

**参考图绑定规则**（必须遵守，否则同一个人会在多镜里长成多个人）：

- 角色参考图 = **单格正面像**（不是四视图拼图）；四视图归档到 `images/_sheets/` 仅供人工查看。
- **参考图条数按 `assets.bind` 分档**：**纯道具镜**不限；**1 个人物**封顶 2 张（脸 + 道具）；
  **≥2 个人物**封顶 3 张且**先满足人脸**。
- **`location`（场景）图只在宽景绑**：全景 / 远景 / 大全景 / 空镜才绑（构图本来就是 Wide、
  不打架，且空镜正是场景漂移的重灾区）；中近景与特写**不绑**、靠文本锚点
  （场景图自带固定机位，绑进近景会把构图拉回大 Wide）。
- **同脸角色**（分身/替身/克隆）自动复用源角色参考图，不重新生成。
- 升级参考图规则后，把注册表里的 `ref_ver` 删掉即可触发重生成（不必手工删图）。
- 角色卡里**不要写人物关系**（"两件制服必须完全一致"会被画成两个穿制服的人）。

**提示词反烧字**（实现细节与事故见 `v5/media/prompt.py` 的注释）：

- **文字概念词一律删掉所在分句**；文字镜（`文字镜=是`）走 `allow_text=True` 豁免。
  **不要**用负面提法（"不要出现文字"）——负面提法会**诱发**烧字。
- **分镜自身矛盾**（要求「特写」却要显示「六格监控画面」全貌）会让景别校验反复判 P0，
  重画无法收敛——这是**分镜问题**，应在 scenedesigner 阶段拆镜。

**静帧硬伤 QC**：逐镜送视觉模型审查，硬伤为「**字幕 / 烧录文字**（★ 场景固有文字
**不算**，默认放行；见下方第 6 条）/ 缺人物或道具 / 多余清晰人脸 /
血腥 / 分屏多格」，另加**景别跨档校验**。**模型只负责"看图描述"，判不判由代码定**
（`qc.is_hard_issue` 三道闸门，可在 `pack.json` 收窄词表）。
**`P1` 只记录、不处置**（`[media] LNxx 警告（P1，不重画）`）。
发现硬伤会自动定向重生成（单轮最多 2 次；**跨进程累计上限**
`SHORTDRAMA_STILL_QC_MAX_REGEN`，达上限或判停的镜保留现有静帧并记 `still_residual`）。

**成片抽帧复核**：拼接前对每条 clip 抽首/中/尾三帧复核，不合格镜**同时作废 clip 与 job
状态**后重渲（`SHORTDRAMA_CLIP_QC=0` 可跳过）；**缺镜直接返回 `status=incomplete`，
不拼接、不覆盖旧片**。

> ⚠️ **QC 对"小字"概率性漏报**（实测满墙汉字仍判通过）。批量生产时不要只信 QC 回执
> ——用 `scripts/qc_sweep.py --json` 全量审查，或把静帧拼图人工扫一遍。

### 关键环境变量（写在 `.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGNES_API_KEY` | — | **必需**（生图/生视频/LLM）。语义 = key 池中**第一条**。key 文件 `.env` 允许两处：`shortdrama/.env` 优先，找不到再读仓库根 `.env`（读到即停，勿两处并存——防轮换漂移） |
| `AGNES_API_KEYS` | — | 可选：多条 key（逗号/分号/换行分隔）。配合 `SHORTDRAMA_VIDEO_KEY_ROTATE`；未设则退回单条 |
| `SHORTDRAMA_V5_PROJECT` | studio | supervisor 架构绑定的项目目录（**编译期绑定，换项目必须重启 dev**）|
| `SHORTDRAMA_V5_AGENT_URL` | http://127.0.0.1:2024 | 派发子任务的目标 Agent Protocol server（**必填**，端口须与 dev server 一致）|
| `SHORTDRAMA_V5_EPISODE` | 1 | 起服默认集号（生产用 `--episodes`，不设此变量）|
| `SHORTDRAMA_STUDIO_PACK` | shortdrama | supervisor / Studio 绑定的类型包 |
| `SHORTDRAMA_OPEN_CHAIN` | 0 | **人用开关**：`1` 开放全链路，`--resume-media` 放行 |
| `SHORTDRAMA_ALLOW_RESUME` | 0 | **人用开关**：`1` 放行单次内部恢复 |
| `SHORTDRAMA_REQUIRE_APPROVAL` | 0 | `1` 启用三道**阶段级**审批门（`storyboard` / `stills` / `media`；指纹绑定产物）|
| `SHORTDRAMA_APPROVE_EACH_ROLE` | 0 | `1` 启用**步级 HITL**（图运行中每次派发子代理前 interrupt；配合 `--hitl*` CLI，信道见 `v5/hitl.py`）。**默认关是硬要求**——全自动驱动脚本会挂在第一步。★ **前端那条路由 `v5/webchain.py` 自己带上它**（只影响它起的那个 dev server ⇒ 外部 agent 不受影响）；⛔ **绝不要写进 `.env`**（会全局生效、把外部 agent 也挂住）|
| `SHORTDRAMA_HUMAN_IN_CHARGE` | 0 | **人工模式：判断权在人**。`1` 时两道门**降级为报告**：`media_gate` 的「必须 `reviewer.passed`」不再拦、分镜契约门只出警告（判决落 `media/ep{N}/gates.json`）。★ 由**前端路径**自己带上（`runner.start` 写子进程 env，只影响前端那次 run）；⛔ **绝不要写进 `.env`** —— 那会把外部 agent 全自动链路唯一的保护也拆掉。「7 个角色产物齐」**照旧硬拦**（与人在不在无关）|
| `AGNES_VIDEO_MAX_SHOTS` | 20 | **超过 20 镜的项目必须调大**，否则按上限**截断**（会打印醒目警告）|
| `AGNES_VIDEO_MAX_SECONDS` | 12 | 单镜秒数**上限**（供应商硬约束 `seconds ∈ [4,12]`；下限 4 固定）。★ 设成小于 12 会让超长的镜被**静默压短**——2026-09-16 曾因误设 `10` 压短 620 镜中的 9 镜 |
| `SHORTDRAMA_VIDEO_BGM` | 1 | 类型包禁忌 BGM 时设 `0` |
| `SHORTDRAMA_VIDEO_MODE` | reference | `keyframe` 为回退档；`pack` 为 12s 打包档（见上文模式表）|
| `SHORTDRAMA_VIDEO_PACK_MAX_GROUP` | 5 | `pack` 模式单组最多镜数（同 README §6）|
| `SHORTDRAMA_STILL_QC` | 1 | `0` = 跳过静帧 QC。★ **前端路径（`v5/media/runner.start`）默认传 `0`**（人工模式：判断权在人），前端工具栏的「自动质检」开关可打开 |
| `SHORTDRAMA_CLIP_QC` | 1 | `0` = 跳过成片抽帧复核。同上，前端默认 `0`、可开关 |
| `SHORTDRAMA_SLICE_THRESHOLD` | 4000 | 按集切片注入阈值（字符）|
| `SHORTDRAMA_SLICE_SOFT` | 0 | `1` = 切片失败降级为「告警 + 注入全文」|
| `SHORTDRAMA_CHAT_VENDOR` | agnes | 文本通道厂商（`v5/vendors.py` 注册表）。旧名 `NEWDEEP_LLM_PROVIDER` 仍兼容回落 |
| `SHORTDRAMA_IMAGE_VENDOR` | agnes | 生图厂商（同上注册表）。**未注册的名字响亮报错**，不静默回退 |
| `SHORTDRAMA_VIDEO_VENDOR` | agnes | 生视频厂商（同上）。前端三个生成页面各自可选，随请求进该次 run 的**子进程 env** |

> **完整清单见 [`README.md`](README.md) §6 配置**（2026-09-18 起它是环境变量的**唯一完整表**）
> —— 本节只列**会改变调用方行为**的那些。语义与理由见 `v5/config.py` 的注释。

## 产物验收

- **成片**：`projects/<项目名>/media/ep{N}/episode_final.mp4`（含音轨）
- **静帧**：`media/ep{N}/stills/*.jpg` + `stills.json`（含每镜提示词与 URL）
- **分镜**：`scenedesigner/scenedesigner_ep{N}.md`
- **渲染任务**：`media/ep{N}/video_jobs.json`（显式状态机：`pending`/`submitted`/
  `completed`/`failed`/`expired`；断点续跑依据）
- **审批记录**：`media/ep{N}/approvals.json`（三道门的 by / at / note / **产物指纹**）
- **黑板**：`projects/<项目名>/.agent_state.json`（**勿手改**）。`phases` / `media_loop`
  都是**按集**的（`{"1": {...}, "2": {...}}`）

**角色产物分两类（多集下必须分清）**：

| 归属 | 文件 | 多集行为 |
|---|---|---|
| **全剧级** | `director/director.md`、`worldbuilder/worldbuilder.md`、`assetdesigner/assets.md`、`plotdesigner/episodes.md` | **只在第 1 集生成**，第 2..N 集跳过 |
| **集级** | `scriptwriter/scriptwriter_ep{N}.md`、`dialogue/dialogue_ep{N}.md`、`scenedesigner/scenedesigner_ep{N}.md`、`reviewer/review_ep{N}.md` | 每集一份，互不覆盖 |

**断点续跑**：`video_jobs.json` 里 `state=completed` 且 `clips/<镜名>.mp4` 存在的镜会被跳过；
`submitted` 会**认领原任务继续轮询**（不重复提交）；`failed`/`expired` 会重新推进。

**评审回退**：reviewer 判 `pass: false` 时先做**缺陷分级**——只给 `advisory`（建议）
而无 `reasons`（阻断理由）**视为通过**；有阻断理由才回退，目标由
`decision.resolve_target()` 算出（取 `rerun` 与 `reason_owners` 中**更上游**的那个）。
回退时该角色**及其下游**的 `phases` 被清空、旧产物移到 `.rerun_backup/<时间戳>/` 后重跑。
同一角色回退**超过** `SHORTDRAMA_MAX_REVISIONS` 次后记 `force_passed` **强制放行**
（媒体门认 `passed or force_passed`，不会卡死）。

**旧项目（旧产物名 / 一维 manifest）**：老项目的集级产物是 `dialogue.md` /
`scenedesigner.md` / `review.md`。读取走 `guards.resolve_path()`（新名优先、旧名回退）。
⇒ 老项目仍可 `--monitor` / `--resume-media`，但**不要**给它们加 `--ep 2`。

### 常用维护脚本（`scripts/`）

| 脚本 | 用途 |
|---|---|
| `qc_sweep.py` | 全量静帧 QC 审查（只报告不生成，`--json` 出结构化结果）|
| `reroll_list.py` | 按名单多轮重滚硬伤镜（`--names LN05,LN06` 或 `--from-qc <json>`）|
| `gen_all_stills.py` | 全量强制重生成静帧（**不跑 QC**，风格块改动后必须用它）|
| `concat_robust.py` | 稳健拼接（`--crop` 无黑边 / `--pad` 零损失）|

### 单镜返工

```bash
python -m v5.series <项目名> --rerender LN03 --note "周奶奶没出现"
python -m v5.series <项目名> --rerender LN03 --from still   # 连静帧一起重做
```

`--rerender` **默认放行**（无需环境变量）。若需换静帧，用 `reroll_list.py --names <镜名>`。

> **注意**：返工会**改变产物**——若开着审批门（`SHORTDRAMA_REQUIRE_APPROVAL=1`），
> 上游一变批文即自动作废，需重新 `--approve`。

## 硬性注意事项

1. **LLM 429**：创作阶段撞 Agnes 限速会自动冷却重试——冷却循环属正常，勿中断进程
2. **视频配额**：`video_quota.json` 是全局账本，勿手改
3. **push 手动**：commit 只做到本地，push 由用户执行（用户明确要求时才推）
4. **黑名单**：勿手改 `projects/<名>/.agent_state.json`；勿动 `video_quota.json`；
   类型包是审美定义，改之前先确认影响范围
5. **dev server**：**仓库根没有 `langgraph.json`**，启动必须 `--config` 指定；
   **换项目必须重启 dev**（项目与 pack 是编译期绑定），**换集不必**。
   端口须与 `SHORTDRAMA_V5_AGENT_URL` 一致，否则派发全失败。
   启动与 venv 排查以 `v5/` 代码与脚本注释为准。
6. **不要往提示词里加"不要出现文字"**：负面提法会**诱发**模型烧字。
   正确做法是正向描述（"所有表面都是平涂纯色块"），且不出现"招牌/摊位/纸张"等载体名词。
   ★ **2026-09-17 补充（这条的边界）**：本条**只为防烧录**（压模型行为）。
   **不要**顺手把它升级成"画面里不能有任何文字/数字"——那会逼分镜写**反物理**的描述
   （"电梯按钮为无字圆形色块"），而生成模型照真实世界画（面板照样带楼层数字）
   ⇒ 产出"违规" + QC 假警报（实测：elevator-layoff 的 LN05/LN06/LN10 三处报"可读数字"
   又被复采翻判干净，白耗复核轮次）。
   **场景固有文字默认放行**；确需全片无字（如怕平台审核）在 brief 写 `on_screen_text: forbid`。
   这条已由 `tests_core.py` 的回归测试锁死
7. **文档对账（2026-09-21 起）**：改了**结构类事实**（图注册 / media 模块 / craft 技法 /
   env 名单）必须同步 `README.md` 与本文件，并跑 `python scripts/check_docs.py`——
   对不上 exit 1，不许 commit。README 是权威快照；脚本只拦"机器可数"的漂移，
   说法类（接线状态、语义）靠这条纪律兜底。
