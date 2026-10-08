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
#     ② ️ **（2026-10-06 更正）** 前端**不是**天然逐步停下：`manual_steps` 默认**关**
#        （`webchain.py:116` 读 `SHORTDRAMA_WEB_MANUAL_STEPS`，出厂 `"0"`），
#        要**每个请求体自己带** `manual_steps` 才开（`POST .../devserver` 或各生成端点），
#        且它是**编译期**烘进 dev server 的 ⇒ 翻了要重启 dev server、只对下一次生成生效。
#        旧版本这里写着"前端起的 dev server 自带 =1 ⇒ 网页端天然逐步停下"，**与代码不符**。
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
| `--stills-only` | **显式预览路径**：只出静帧给人看风格。默认档（视频吃资产图）平时**不产静帧**，这条是「我就想看看静帧长什么样」时手动按的按钮，不是流程的一环 |
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
   ⚠️ **`episodes.md` 必须集集有条目**（2026-10-05 起由 `guards.post_validate` 当场判）：
   `brief.episodes > 1` 时程序按**集号**数目录里的 `### 第 N 集` 条目，缺哪几集就退回并**点名集号**。
   起因是实测第 4 轮：我给 brief 写的「场」口径（一集 9–11 场）被**全剧级**目录角色照抄，
   它给每集排一张场表 ⇒ 文档胀到一次写不完（角色契约同时明令"不许分多次 write_file 追加"）⇒
   盘上只剩 4 个集条目，而旧判据只看"文件存在且非空"、照常放话"合格"⇒
   **一个角色在自己的循环里空转 45 分钟、52 分钟零出片**。
   ⇒ 写 brief 时把「场／镜」口径**限定给分镜表**，并给目录一个能一次写完的体量
   （本项目用的「每集 150–300 字、全文 ≤7000 字、≤3 卷、不写场表」）。
   回归测试：`tests_roles.TestCatalogSlicing` 两条 + `tests_guards.TestPostValidate`。
3. **brief 字段在多集下语义不同**（`episodes > 1` 时 `_brief_gate` 会主动提示）：
   `must_have` = **每集都能覆盖**的硬要求（**不是全剧四幕**）；
   `target_duration` = **每集**时长（不是合计）；全剧起承转合与 `结局`
   写进 `plotdesigner` 目录的卷首。

**集数多时的切片注入**：全剧级目录超 `SHORTDRAMA_SLICE_THRESHOLD`（默认 4000 字符）即
**按集切片**注入（只注本集 ±1 + 本卷摘要；切片**可见**、失败默认**响亮终止**，
`SHORTDRAMA_SLICE_SOFT=1` 降级为注入全文）。⇒ `plotdesigner` 的产物必须按
「`## 第 N 卷` + `### 第 M 集`」结构写，且**集条目自包含**（禁"同上/见前文"，切片后会悬空）。

⚠️ **单集项目（`brief.episodes == 1`）不切片**：只有一集时整份目录就是本集，没有"本集 ±1"可切，
plotdesigner 自然也写不出「### 第 M 集」结构 ⇒ 旧实现会 `RuntimeError` 把整条链判死
（2026-10-03 实测 `yoga-affair-1003e`：9155 字的单集目录 → `rc=3`、8 分钟零出片）。
现在直接注入全文并打一行说明；**多集**项目仍走原路径，失败照样响亮终止。
回归测试：`tests_roles` 的 `test_single_episode_catalog_is_never_a_hard_stop`。

### ★ 多项目并行（2026-09-29 起支持，两条事实）

```bash
# 两条链各自起服、各自进度目录，同秒起跑互不打扰（实测 par-a / par-b）
PYTHONUTF8=1 .venv/Scripts/python.exe scripts/run_new_project.py <项目A> --stills-qc &
PYTHONUTF8=1 .venv/Scripts/python.exe scripts/run_new_project.py <项目B> --stills-qc &
```

1. **不需要你分配端口，也不需要你避让 2024。** `run_new_project.py` 每次起服从
   **2080-2099 自动挑一个没人听的口**（扫描起点按项目名错开），并把
   `SHORTDRAMA_V5_AGENT_URL` 对齐到那个口；项目的 `.langgraph_api` 落在
   `.dev/<项目名>/` 下（仓库根那份不再共用）。⚠️ **起服前一律不杀进程**——
   旧实现在那里 `taskkill` 占口进程，串行时清的是上个项目的残留，**并行时那就是
   另一条正在跑的链**。显式钉 `SHORTDRAMA_DEV_PORT` 则只用它、被占就响亮终止（不换口、不抢人）。
   ⇒ 前端 `webchain.py` 仍走 2024 + 共用 `RUNTIME_ROOT`（**未改**），所以"前端 + CLI 并行"目前会撞。
2. **限速与视频配额是全机共用的**：三条 key 一个池，两条链互相**排队**（变慢，不是失败）。
   pack 档本身每组串行落盘（要抽真实末帧当下一组的接续锚），所以并行条数上去后
   单轮时间按当轮实测组时报（0929 晚 7-8 分钟/组），**别拿上一次的读数当基线**。

## brief.json 规范（你的核心产出）

**写到 `projects/<项目名>/brief.json`** —— 脚本按这个路径读。
⚠️ **该路径读不到时不会报错**，而是**静默用默认类型包 `shortdrama`** ——
你写的 `pack` 会被无声忽略（与下面第 4 条要点同型）。

brief 是外部 Agent 唯一的强杠杆——它决定"拍什么"，而画质审美由类型包决定。

### 质量门与 brief 注入（⚠️ 2026-09-27 更正本节第 1 条）

1. **brief 全文注入，没有任何长度截断**。角色侧走 `roles._inline_file(root,"brief.json")`
   ＋`read_file /brief.json` 读**完整文件**，所以**你写的每一个字模型都看得见**。
   ⛔ **别再为"字数预算"删内容**——原先本节写着「brief 有 3000 字符注入预算，
   放不下先丢低价值字段、核心字段放不下报 `BriefTooLarge` 终止」。查证结论：
   `series.run()` 里那次 `validate.pack_brief()` 算出的 `text` **从未被使用**
   （AST 核对：整个函数只有赋值那一处引用），落盘的是未截断的 `data`。
   ⇒ 那个上限唯一的作用是在 brief 超过 3000 字时**中止整条链**，
   而它声称保护的注入通道并不存在。调用已删除，`pack_brief()` 函数保留（有测试覆盖，
   供将来真要做注入预算时使用）。**真要控 token，该管的是 `roles.role_input`
   拼起来的注入总量**——那里现在不截断。
   ★ 我因为信了这条假文档，在 2026-09-27 一整天里反复压缩 brief、甚至删掉过
   `资产卡格式硬要求` 整个字段——全是无谓的损耗。这条记录就是防止下一个人重犯。
2. **`FIDELITY` 门是"忠实度"门不是"质量"门**：它只校验产物有没有覆盖 `must_have`，
   **不校验 brief 本身好不好**。烂 brief 被忠实执行 = 烂成片，门照样过
   ——质量责任在外部 Agent 一侧。
3. **`audio_mode` 会真的生效**：开工前注入 `dialogue` / `scenedesigner` 的输入，
   **并且**程序按分镜对白列做**确定性**校验（`roles.enforce_deterministic_verdict`；
   调用点 `drive_chain.review_state` + `guards.reconcile_manifest`）——
   `dialogue-led` 而台词镜占比 <20% ⇒ **把 reviewer 的 `pass: true` 翻成 false**、
   打回 **`scriptwriter`**（唯一能创造台词的角色：`dialogue` 逐字搬运、`scenedesigner` 抄表，
   实测两者都拒绝替上游补写）；`silent` 却有台词同样回退。
   占比 20%~50% 只警告不阻断。违规会回退，**不会静默放行**。
   ⚠️ **brief 自己矛盾时这条拦不住**（1008 实测 `yuxuan-duanfeng-1007` ep2）：
   `audio_mode: dialogue-led` 与 `禁忌` 里一句「无人说话（全片 silent）」并存 ⇒
   三级搬运一起交出**空对白列**，重做也只是原样交回。
   写 dialogue-led 时必须把 `禁忌` 里"无声/无口型"那类条目**删掉**——
   `禁忌` 同时是**成片复核的硬伤判据**，留着它会把口型本身判成硬伤。

### 字段表

| 字段 | 必填 | 说明 |
|---|---|---|
| `topic` | ✅ | 主题名（项目标识） |
| `pack` | ✅ | 类型包名，决定审美与角色 skill 回落基准 |
| `genre` | ✅ | 类型基调（温情/悬疑/喜剧…） |
| `episodes` | ✅ | 集数 |
| `target_duration` | ✅ | **只写总时长**（如「约 130 秒」）——镜数与每镜秒数**归分镜师按剧情节拍决定**（⛔「镜数 = 目标秒数 ÷ 4」这条除法基线已于 2026-10-03 废弃，见上）。要指定节奏就写**内容**口径，例如「同一场戏尽量并成一镜 12 秒，镜内按 0-3 秒 / 3-6 秒… 排事件」。硬边界只有两条：**每场（= 一条请求）4-12 秒**（供应商区间管的是请求，不是镜）、总时长落在 85%-130%（分镜契约门）。★ 2026-10-05 单位从「镜」换成「场」：场锁死、镜自由——镜长没有地板，快切正反打写 0.5 秒一镜合法（见下「镜内多拍」节末）|
| ↑ 多集且**各集不等长**时 | ✅ | 写成**分集**形式：「第 1 集约 60 秒，第 2 集约 156 秒」。片长判据按集号取本集那条（`validate.parse_target_seconds(text, ep=…)`，`roles.role_input` 注入与 `shotcheck.duration_band` 跟着走）。⚠️ 只写一个数会误伤另一集——实测 `ice-spring-bridge-duel`：为第 2 集把 brief 改成 156 秒后，**已验收的第 1 集**被门报「分镜总时长 60s 与 brief 目标 156s 不符（38%）」钉在门外。回归测试：`tests_validate.TestDurationContract.test_per_episode_target_duration_is_read_per_episode` |
| `空间数要求` | 建议 | **≥2 个视觉上明显可区分的空间**（不同地点／不同时段／不同光色）。不写时模型倾向把全片拍在同一处换机位（brawl 实测：4 个场景名都在同一条后巷 → 成片看着像"一场戏打完"） |
| `分镜格式硬要求` | 建议 | 画面描述要**按镜内节拍写**（`0-2秒：…；2-6秒：…`，0 起/首尾相接/终于本镜时长）、每拍写**身体朝向**与**画面内**视线落点、**第一拍写全角色锚点**（`@阿劲（灰蓝旧运动外套、黑色工装裤、腰间旧电筒）`）而**后续拍用「他／对方」**（★ 后续拍重复 `@名（衣装）` 会让模型多画一个人，brawl 实测）。★ 这些要求**必须写进 brief 才生效**——只写在包 SKILL 里执行率 1/18，写进 brief 是 20/20（brawl 实测）。★ 静帧只取**第一拍**，锚点写在前面的拍里才拿得到。★ 节拍端点**可用小数**（`0-0.5秒：`，2026-10-02 起如实解析；此前 `0.5-1秒：` 会被整数正则认成**起点 5、终点 1 的倒挂拍**，门报一条与作者意图无关的假理由）。★ 一组超过 12 秒时媒体层会**等比压缩每镜秒数**，节拍时间戳跟着重标、**拍数不丢**（旧实现的时间戳会溢出到下一镜，两镜抢同一秒） |
| `protagonist` | ✅ | 主角设定（外貌逐项）+ 形象依据声明 |
| `must_have` | ✅ | 写法**取决于集数**。**单集**：四幕结构（钩子/发展/转折/收尾），每幕一个具体可拍事件。**多集（`episodes`>1）**：**每集都能覆盖**的 2–3 条硬要求，**不是全剧四幕**（写全剧四幕会让**每一集**都被 FIDELITY 门拦下）|
| `key_props` | ✅ | 关键道具：精确命名 + 外观逐项特征（跨镜一致性的锚点） |
| `禁忌` | ✅ | 硬约束——**画面硬伤判据的来源**（默认档 = 成片抽帧复核 + 资产图查字闸门；静帧 QC 只在回退档跑）|
| `on_screen_text` | 建议 | `allow`（默认）/ `forbid`。默认**只禁烧录型文字**（成行字幕条、元指令文字），**场景固有文字**（门牌 / 面板读数 / 文件抬头 / 包装标签）**放行**；`forbid` = 画面里**任何**可读文字都算硬伤（怕平台审核时用）。 |
| `tone` | ✅ | 渲染风格 + 光线色彩叙事 + 情绪弧 |
| `结局` | ✅ | 定格画面描述 |
| `audio_mode` | 建议 | `dialogue-led`（**必须有台词**，台词镜 ≥20%，低于 50% 出警告）/ `silent`（不出台词，对白列统一「（无声，环境音）」）/ `narration-led`（旁白推进剧情：旁白写进「音效」列的画外音格式，**「对白」列统一「（无声，环境音）」**）。⚠️ **brief 不写该字段时一律回落 `dialogue-led`**（`validate.audio_mode_of`）——`pack.json` 的 `default-audio-mode` **代码不消费**，想要旁白必须**在 brief 里显式写** |
| `visual-style` | 建议 | 渲染风格声明（按包定义填写） |
| `second_character` | 有配角时 | 第二角色设定，写法同 `protagonist` |
| `reference_photo` | 有照片时 | 用户照片路径（先拷到项目 `images/`） |

**内容字段的值必须按项目内容原创**——照抄示例 = 产出与需求无关。

### ⚠️ 「镜内多拍」与「每镜恰好一拍」是两条对冲的契约（2026-10-03 实测）

`shotcheck.countable` 的结构类判据里有一条 **「每镜恰好一拍（出现第二个 `N-M秒：` 即不合格）」**
（0929 加、有测试），而本文件上面「分镜格式硬要求」的示例教人写 `0-2秒：…；2-6秒：…` 多拍。
**同一个 brief 里两者都要 ⇒ 模型拿到的输入自相矛盾**：1003b 实测多拍表被这条判成 26/26 不合格，
退回清单加评审打回一起压下来，分镜角色一轮做了 111 次工具调用、撞穿角色额度、整条链零出片。

⇒ 二选一，别混着写（**判据跟着 brief 声明的单镜秒数走**，`shotcheck.countable` 里同一处算出的 `_exp`）：
- **短镜方案**（`_exp < 8`）：一拍一镜，秒数写在「时长(秒)」列——这条就是 0929 原律，行为一字不变；
- **长镜方案**（`_exp ≥ 8`，如 11 镜 × 12 秒）：**反过来强制镜内时间轴**——每镜 ≥`_exp//3` 段
  （12 秒 ⇒ ≥4 段）`0-3秒：…；3-6秒：…`，且每段必须是不同事件（位移／易手／进出画／机位变化）。
  ★ 为什么必须反过来：1003c 实测——一拍写完 12 秒，等于把节奏整个交给模型，
  成片人眼判定"节点太少、太拖沓"，11 镜里 **7 镜末段明显安静**（末 2 秒帧间差／全程 = 0.41）。
  ⚠️ 配套的一条：分镜的「运镜」列**不许写"最后 N 秒机位完全静止"**——`camera-light-physics`
  要求"静止要写到秒"，模型就把它写在镜尾，1003c 有 2 镜静止满 6 秒＝半镜是静照。
  静止段只许写在镜子的**前 3 秒**内。这条代码判不了（要判的是语义位置），写进 brief 兜住。
  ★ **`≥_exp//3` 只是程序下限，别当密度目标写给分镜师**（2026-10-06 实测
  `ice-spring-bridge-duel`）：12 秒照 4 段交，帧间差 10.04、末 2 秒／全程 0.43，
  人眼判「节奏卡顿、没有连招」；改 **8-12 段（每段 1-1.5 秒）** 后涨到 15.10 / 0.90。
  ⚠️ 但密度补上后用户仍判「招式不够漂亮」——**密度管"快"，好看要的是形状**：
  每拍要凑齐 位移 + 兵器轨迹形状（圆弧／半月／一整圈／螺旋）+ 光效形状名词
  （环形火星／月轮光尾／双色涟漪／花瓣旋涡）+ 身体线条（裙摆甩成圆盘、发丝拖弧）。
  这条已落进 `xianxia-vfx-action/scenedesigner` 的「四条硬要求」第 0 条。
  回归测试：`tests_shotcheck.TestCountable.test_long_shot_must_carry_an_internal_timeline`。

### ⛔ 「镜数 = 目标秒数 ÷ 4」这条除法基线已废弃（2026-10-03，用户决定）

**镜数与每镜秒数归分镜师按剧情节拍决定；brief 只定总时长。** 旧口径把"两分钟"自动变成
"30 镜 × 4 秒"，实测成片是一段段硬分、割裂感重（用户原话：「4-6 秒一段段硬分＝割裂感重、
节奏慢」），而节奏本该由叙事决定、不是由除法决定。

它原来散在 **8 个入口**，只改包契约没用——真正写死它的是代码：
`roles.role_input`（每次派发给分镜角色的硬指令，含"约 N 镜、4 秒一镜、快切优先"）、
`webchain.py`（前端 brief 写法指引，明令"不要写每镜 6-8 秒"）、三个 craft 技法
（`short-drama-hooks` / `-opening` / `-satisfaction` 的落地约束）、
`shortdrama` 与 `chinese-style` 的分镜契约与节拍自查条款、
`xianxia-vfx-action` 的 ÷3.5 自定基线 + reviewer 的"平均镜长 >4.5 秒即阻断"。
现在这些位置统一成：**硬边界只有两条**——每场（= 一条请求）4–12 秒（供应商区间管请求）、
总时长落在 brief 目标的 85%–130%（分镜契约门）。**保留**「台词字数 ÷ 4」——
那是把一句话说完需要几秒的口播物理，不是镜数机制。

**新口径（写进分镜角色注入块与各包契约）**：① **同一场景的连续戏优先合成一镜、用满 12 秒**
（一次生成只含一镜 ⇒ 同场戏并进去才保得住人物/光色/道具一致）；② **≥8 秒的镜必须写满
镜内时间轴**（程序按**该镜自己的秒数**查，不看 brief 有没有写镜数）；③ <8 秒的镜
**不判拍数**——「每镜恰好一拍」随除法基线一起废弃，仙侠若要，写在该包自己的契约里。

**「单镜时长」判据**：brief 若声明了镜数（`parse_shot_range` 读得出）⇒ 期望秒数 = 总时长÷镜数，
按 −50%/+34% 收；**没声明**（废弃基线后的常态）⇒ 只守供应商硬区间 4–12 秒，
不再拿"≤5s 常态"当规范。
同批修掉的另外两条误伤（1003d 实测，都是**规则写歪**而不是模型写歪）：
- **`@名（` 重复**：判的应是"**同一个角色**被 @ 两次以上"。旧实现数"一镜里 `@名（` 出现几次"，
  于是 `@苏晚（…）` + `@豆绿色瑜伽垫（180 厘米…）`（一人一道具）与"三人各 @ 一次"
  都被判成会多画人——而后者恰恰是"别把对手写成背景"要求的写法。没角色名表时退化成
  "同一个 @名 重复"，两种情况下都不再误伤不同角色。
- **10b「非宽景只能 @ 一个角色」是包内律**：它由 `xianxia-vfx-action` 在 0927 从 advisory
  升为该包阻断，不是跨包通则。现在按包生效（`shotcheck.pack_requires_single_at(root)` 读该包
  分镜契约里有没有这句话），`shortdrama` 这类项目不再被它拦。
- **镜内分段的正则**要允许 `9-12秒（结束态）：` 这种写法——只认紧挨的 `秒：` 会把 12 秒镜
  数成 3 段，导致 11 镜全部被误点"没写满时间轴"。
⚠️ 顺带修掉一处静默失效：`parse_shot_range` 旧正则只认「共 N 镜」与「N-M 镜」，
**「全片 26 镜」读成 None** ⇒ 镜数判据整条退回派生公式（与 0929「明写 15-18 镜却读不出」同型）。
现在单值写法也认；brief 里仍建议写「共 11 镜」这种最稳的格式。

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
   ——**`禁忌` 是画面硬伤判据的来源**（默认档吃它的是成片抽帧复核与资产图查字闸门；静帧 QC 只在回退档跑）。
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
| `xianxia-vfx-action` | UE5-Niagara 仙侠特效动作·高精度游戏 CG（**横屏**、**无对白**）| **silent** / narration-led / dialogue-led | `still-refs: true`、`still-tail: material`、`style-watch: true`、`script-craft: []`；自带 5 个角色契约。**主资产是镜头语法**：要压进 brief 的只剩**三条语义律**（能量色编码一致／远程攻击只有板状剑罡／尾镜接续 TAIL LOCK）——可数的条款改由 `v5/shotcheck.py` 出带镜号的退回清单，不再要求角色逐镜自查（2026-09-29 实测：双重执法让分镜角色逐镜自改、一条链白跑 2 小时）。★ 已删的三条：`兵刃接触 ≥8`（官方范例 30 秒接触 **0** 次，比参考片还严，逼下来真打镜数 12→4→1）、`每镜必须写尺度对比`（16:9 六臂探针：加尺度命令的差异 +1.05 < 同提示词噪声地板 5.30，且没加它的基线本来就有大柱小人）、`宽景必须 @场景名`（代码已无条件绑 location）。跑横屏必须**同时**设 `SHORTDRAMA_ASPECT=16:9` + `SHORTDRAMA_STILL_RATIO=16:9`。**未实测**，首跑按换包验收走一遍 |

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
（当前 **6 个**：`pixar-lighting` / `short-drama-hooks` / `short-drama-opening` /
`short-drama-satisfaction` / `micro-expression-acting` / `camera-light-physics`）。
`camera-light-physics` 是**摄影与光学写法**（运镜写速度/行程/终点/静止段、视觉风格加"光落点"、
空间尺寸写在**场景卡**不是每镜）——依据是 1002 夜的五臂探针 `scripts/probe_prompt_detail.py`
（`缓推` → 带数值与静止段的写法，末镜最后两秒帧间差 10.9 → 6.7/5.7，同文本抖动带宽只有 2.8，
且未声明运镜的对照臂停在 10.8）。它配套两条**可数检查**（`shotcheck.countable(camera_light=…)`：
运镜写了位移却没写数值/终点、视觉风格没有光落点 → 进退回清单带镜号），
⛔ **只在 brief 声明了该技法时才判**（反质量包要的是僵硬锁定机位，判了是误报），
且只出清单、不进分镜契约门。它**未全面实测**（只在 9:16 写实包的一处场景验过）。
它**没有 `pack.json`、不定义角色**。
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
| **`reference`（★ 系统默认档）** | **2026-10-07 起默认喂本镜资产图**（定妆照 ≤2 + 场景空镜 + 道具，封顶 3 张，`assets.sheets_for_shot`），提示词首段逐张点名（`prompt.pack_ref_declaration`）；设 `SHORTDRAMA_VIDEO_REF_SOURCE=stills` 回到旧行为（静帧进 `images`、作 `<Picture 1>`）。**这一档不再画静帧**（媒体链整段跳过），除非某镜一张图都没绑上才退回画那几镜 | `images`(≤5) / `audios`(≤3) | `first_frame`、`last_frame` |
| `keyframe`（回退档） | 静帧当首帧，构图被锁死 | `first_frame` / `last_frame` | `images`、`audios` |
| `pack`（2026-09-22 落管线） | **相邻同场景镜打包**成一条 ≤12s 的 reference 请求；job 与产物都是**组级**（`clips/packNN.mp4`）。★ **图序（2026-10-07 起，`video.pack_ref_images`）**：人物设定表（≤2）→ 场景空镜（**按表列无条件取**，不再只绑宽景）→ 上一组成片的**真实末帧** → 道具补空位，**静帧整条退出 pack**。理由：跨组接缝的正确来源本来就是"上一段结束画面"= 成片末帧（`seam_anchor`），静帧是**起幅**、拿它当结束画面是对模型说假话（09-28 记过），而"完全去掉静帧会让相邻两组长成两种地貌"那条 09-28 实测**前提已变**——10-05/10-06 起场景卡简称与关键词也绑得上、10-07 逐镜档两轮实跑证明"场景空镜 + 文字锚点"撑得住地貌一致。⛔ 抽不到末帧就**没有锚帧**，不再退回静帧 ⇒ 因此**串行落盘**（每组提交后等成片落盘）现在是锚帧的唯一来源，比当年更重要。提示词首段仍由 `prompt.pack_ref_declaration` 生成 | 同 `reference`（≤5 张/请求，图数不限制组大小；组上限默认 12，只受总长 ≤12 秒约束） | 同 `reference` |

分组算法在 `media/video_plan.group_shots`（同场景相邻贪心、≤12s、压缩保台词下限）；
打包 prompt 在 `media/prompt.build_pack_prompt`；旁路脚本 `scripts/pack_render.py`
保留为独立验证入口。⚠️ **2026-09-28 起两者图序故意不同步**：`pack_render.py` 停在
旧的"每镜一张静帧"，留作 A/B 对照臂（新图序的复现入口是
`scripts/probe_costume_direct.py --mode hybrid`）；**分组判据**仍要求两处同步。
★ **pack 档是串行落盘**（2026-09-28）：每组提交后立即轮询到成片落盘才提交下一组，
因为下一组的接续锚帧要**从这组成片抽真实末帧**。代价是失去跨 key 并发、整轮多
20-40 分钟；换来的是真的动作接续（旧时序下锚帧永远只能拿静帧兜底，末帧修复形同没做）。
日志里每组会打 `接续锚=末帧` / `接续锚=静帧兜底`，**看到后者成片说明有问题**。
pack 档**跳过**逐镜 clipqc（组产物多镜
合并、逐镜判据不适用）与落幅帧预生成。

回退：`SHORTDRAMA_VIDEO_MODE=keyframe`。

**★ 档位是「拼错就静默换档」的高危开关**（2026-10-04 已加响亮告警）：
`config.VIDEO_MODE` 是唯一真相源，合法值只有 4 个
（`reference` / `keyframe` / `pack` / `mixed`，见 `config.VIDEO_MODES`）。
**拼错（`packk`）不报错**，会回落到 `reference` 并打印告警。
⚠️ 打包版只读 `shortdrama/settings.env`、**不带 `.env`**，所以出厂档位那一行
`SHORTDRAMA_VIDEO_MODE=reference` 已写死在 `settings.env` 里（真实环境变量仍优先）。
换 `pack` 档要显式设 `SHORTDRAMA_VIDEO_MODE=pack`（真实 env 或改 `settings.env`），
别指望改别的地方能顺带切档。

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
  ⚠️ 这条 cap 是**实测数**不是审美偏好：1 人物镜喂 3 张会多画一个人。
  它的副作用要知道——**1 人物的宽景镜里，场景空镜和道具抢同一个第 2 格，道具必输**
  （`others = [scene_pick] + others`，2026-09-23 定的是场景优先）。duanji-gui-0930 实测
  7 镜因此拿不到道具图。**要道具进得来就得先 A/B 验证 cap 能否放宽，别直接改优先级。**
  ✅ 2026-10-06 起这条"放宽要验"有了**受控出口**：`SHORTDRAMA_REF_CAP_MULTI`
  （**只放宽不收紧**，设小会被忽略并告警；默认仍是实测值，见 `assets.ref_caps()`）。
  双人打斗镜的代价实测在 `ice-spring-bridge-duel` ep1：2 张脸占两格 ⇒
  **春灵的荆棘弯刃 5 镜零绑定、场景图也进不来**，那把刀每镜现编、结尾镜头一拉远
  桥就换成了另一处。放宽属于有代价的赌注（多一张图可能多画一个人），
  所以默认不变、按项目显式打开并**人眼看片确认**。
- **道具之间抢同一个名额时，按「brief 的 `key_props` → 本集提及镜数」排序**
  （2026-10-06，`assets.bind` 里 `others.sort`；`key_prop_names()` / `mention_counts()`）。
  旧实现 `picks = (chars + others)[:cap]` 直接按 `hits_for_shot` 的返回顺序截断，而那个顺序
  = 分镜正文里 `@` 出现的先后 ⇒ **谁先被 @ 谁进请求**。《听见灯》ep1 夜堤一场实测：
  「坐在@折叠竹椅上 … 扶住膝上@走马灯灯边」，椅子写在灯前面，标题道具整场没参考图，
  成片里那盏六角走马灯三镜画成三种形状（圆球／大红圆柱／星形平轮），返工两轮 ≈ 1 小时。
  ⚠️ 注册表里所有 prop 的 `priority` 都是 `cast._register` 写死的 8（卡上也没有可填优先级的栏位），
  所以"按 priority 排"在道具之间等于没排 —— 这就是为什么判据取盘上已有事实而不是资产卡。
  回归测试：`tests_assets.TestPropSlotOrder`（六条，含"旧病装回去"的反向对照）。
- **道具按 keywords 无条件补绑**（2026-09-30，`assets.hits_for_shot` 的 else 分支）：
  `@` 命中非空时**不再跳过** keywords 兜底，prop 类照样补进来。
  旧逻辑「@ 只要命中就不猜」的代价实测过：分镜**必然** `@角色` ⇒ 兜底永不执行 ⇒
  而道具的真实写法是「主案侧戟头（玄铁断戟、褪色旧红绳垂地）」这种**全名在括号里、
  不加 `@`** ⇒ 两集 60 镜、38 镜提到道具、**绑上图的 = 0**，且**日志全绿**。
  ⛔ **只并 prop**：character 有泛词误命中事故（noodle-night LN09 因文本含「她」
  命中「老陈」→ 绑错脸），location 有自己的宽景通道，两者都不许无条件并。
- **简称也要绑得上**（2026-10-05，`assets.hits_for_text` 的尾词兜底）：keywords 要
  **整串出现在正文里**才算命中，而分镜写道具**一律用简称**（「放大片」）——简称是全名的
  **尾巴**，`k in text` 永远为假。实测 `madfate-abc-1005` 第 1 集：核心反转道具
  「死者眼球放大片」全名出现 **0** 次、简称 **13 镜**、绑定 **0 镜**、日志全绿 ⇒
  道具没有外观锚，模型拿同一场里唯一绑上的「旧机械表」**怀表图**去顶 ⇒ 道具忽大忽小、
  两件长成一件，观众当场跳戏。现在兜底再试**全名尾词**（≥3 字），且
  ⛔ **有歧义的尾词直接丢**（`红色外套`/`蓝色外套` 共有「色外套」⇒ 谁都不绑，
  宁少绑不绑错）。修完实测该集匹配数：放大片 0→13 镜、消防外套 0→4、场记板 0→3。
- **漏绑必须自己喊出来**（2026-10-05，`assets.unbound_mentions` 接进 `validate_assets`
  第 ⑤ 项，⛔ 不另起第二处报告）：资产登记了、正文提到 ≥2 镜、却一次都没进过任何一次
  请求 ⇒ 响亮报"参考图白出了 + 多半缺简称别名"。原先那四项检查全要求分镜写了
  `@全名` 才看得见，而分镜不写 @ ⇒ 那次静默了一整轮。
  ⚠️ 资产卡的**「关键词」栏**是这件事的总开关：`shortdrama` 等 6 个包的契约要求写，
  `madfate-grim` / `half-narrated-live-action` 原先**没写**（已补）——缺了它 `cast` 会
  兜底成"只有全名"，正好把漏绑钉死。
- **`location`（场景）图只在宽景绑**：全景 / 远景 / 大全景 / 空镜才绑（构图本来就是 Wide、
  不打架，且空镜正是场景漂移的重灾区）；中近景与特写**不绑**、靠文本锚点
  （场景图自带固定机位，绑进近景会把构图拉回大 Wide）。
  ✅ **宽景镜无条件按「场景」列绑 location**（2026-09-28 修，`assets.hits_for_shot`）。
  历史陷阱（2026-09-27 实测）：旧实现只在「@ 一个都没命中」时才回退按 keywords 匹配资产名
  ⇒ 只要本镜 `@` 了角色，「场景」列的名字**永不参与匹配**，场景图一张都绑不上
  （资产已登记 `location`、场景图已生成，6 镜仍各只绑 2 张人脸，"云海双塔"画成地面庭院
  而**日志全绿**；**跨包通用**，不只本包）。当时的对策是要求分镜写 `@场景名`——
  ⚠️ **那条契约已作废、reviewer 不许再据此阻断**：能由代码确定做到的事不该反复要求模型
  （xianxia-vfx-action 为它连跑四轮创作链，其中一轮 reviewer 还编造了阻断理由）。
  回归测试：`tests_assets.test_wide_shot_binds_scene_column_without_at_mention`。
- **`【无人像】` 标记**（`storyboard.NO_HUMAN_MARKS`，2026-09-27 新增）：写在「画面描述」里，
  解析时剥掉不进提示词，该镜**不绑任何角色设定表**、`cast_counts` 记 0、
  `person_directive` **不注入人数声明**。用于"人化作能量体/无脸主体"的镜。
  ★ 为什么需要它而不是靠措辞：分镜不写 `@角色名` 时，角色补漏与 keywords 兜底**照样**
  把人脸捞回来，"锁定长相与服装形制"会压过"没有站立的人形"（本包化身镜实测两轮）。
  不写该标记 = 行为与历史一字不变。
- **同脸角色**（分身/替身/克隆）自动复用源角色参考图，不重新生成。
- **分龄变体与漏登记的新角色由代码补卡**（2026-09-30，`v5/media/variants.py`）：`cast`
  读**本集分镜的 `@名（括注）`**，派生「阿旺（14岁版）」这类变体（以基础定妆照做
  img2img 锁脸）与从没进过注册表的新角色卡；绑定层按本镜括注里的年龄段选那张表。
  ★ 为什么必须有它：多集连载里剧本会推主角年龄，而**全剧只有一张定妆照**——
  identity 那句「全片每镜必须完全一致」与分镜的「14 岁瘦高」冲突时**图赢**，
  实测三集出片第 2、3 集仍是孩童体型，而旁白在念"十六岁那年我辍学了"。
  关掉设 `SHORTDRAMA_AGE_VARIANTS=0`，行为与改造前一字不变。
- **同名多张年龄卡会被拆成各自独立的表**（2026-09-30，`cast.split_same_name_cards`）：
  上游 `xiaoman-workshop-1030` 的 `worldbuilder.md` 写了「小满（8 岁，第 1 集）」和
  「小满（13 岁，第 2 集）」两张卡，而 `_register` 按 name upsert、图片也叫 `<name>.png`
  ⇒ 后一张**覆盖**前一张，全剧只剩一张不知道几岁的脸。年龄取卡标题自述（正文里
  比较句提到的参照年龄会骗人），标题没写绝对年龄就**不猜**、只报一行。
- **一集的默认年龄段决定"不带年龄的点名绑哪张表"**（2026-09-30，`variants.default_ages`）：
  分镜契约要求只有**第一拍**写全角色锚点，后续拍写 `@小满（蓝色工装马甲）`——不带年龄。
  实测第 2 集 18 次 `@小满` 有 17 次不带年龄 ⇒ 只看本镜括注的话，拆出来的表根本用不上。
  现按"**本集只出现一个年龄段**"取默认；一集里出现两个年龄段（闪回 / 混写）**不猜**，
  只报一行，那种镜必须自己写括注。镜内括注**永远优先**于整集默认。
- 升级参考图规则后，把注册表里的 `ref_ver` 删掉即可触发重生成（不必手工删图）。
- 角色卡里**不要写人物关系**（"两件制服必须完全一致"会被画成两个穿制服的人）。

**提示词反烧字**（实现细节与事故见 `v5/media/prompt.py` 的注释）：

- **文字概念词一律删掉所在分句**；文字镜（`文字镜=是`）走 `allow_text=True` 豁免。
  **不要**用负面提法（"不要出现文字"）——负面提法会**诱发**烧字。
- **分镜自身矛盾**（要求「特写」却要显示「六格监控画面」全貌）会让景别校验反复判 P0，
  重画无法收敛——这是**分镜问题**，应在 scenedesigner 阶段拆镜。

★ **适用范围（2026-10-07 起）**：默认 `reference` 档**不画静帧**，这道 QC 只在
`keyframe` / `pack` / `mixed` 三档与 `SHORTDRAMA_VIDEO_REF_SOURCE=stills` 回退时跑。
默认档对应的保护搬到了资产图上 —— 见下面「资产图查字闸门」与 `SHORTDRAMA_SHEET_TEXT_GATE`。

**资产图查字闸门**（`v5/media/sheettext.py`，2026-10-07 新增，默认开）：视频请求改吃
资产图之后，**静帧那一步的反烧字清洗与硬伤重画被整段绕过**，所以检查搬到"真正进请求的
那批图"上。逐张问视觉模型有没有可读文字（判据复用 `qc.is_hard_issue`，按文件指纹缓存到
`media/ep{N}/sheet_text_seen.json`）；命中 ⇒ **那张图剔出请求** + 响亮报资产名与依据句，
⛔ 不阻断整集。设 `SHORTDRAMA_SHEET_TEXT_GATE=0` 时打一行"这批图没查过"的告警——
闸门失效必须是看得见的。

★ 为什么这是 `sheets` 档的**必要配套**而不是可选项（10-07 两轮实跑）：命案集的场景卡
「天台水塔间」自己带一排红字招牌 ⇒ 直接喂视频后成片 ≥4 镜背景原样出现整排汉字，
提示词末尾那句 `no on-screen text` **压不过图**；仙侠那批图逐张查过无字 ⇒ 60 秒一帧无字。
⇒ **这条路线的红利完全取决于喂出去的图干不干净。**

**资产图对账（道具 / 场景）**（2026-10-07 补，`cast.verify_asset_sheets` + `sheetcheck.judge_asset`）：
角色定妆照一直有对账，**道具与场景图此前无人过问** —— 实测代价是「青霜双鞭」被画成
一个穿白衬衫的现代男人两手举着蓝色绳圈（`xianxia-zhongzhui-1007`）。
现在出图后同样过一遍视觉模型，只抓三类明确的错：**主体是真人 / 未画出 / 多主体**；
审美差异不判，模型答不上来（JSON 不成形）也**不判** —— 判据是概率性的，判宽了每次生成都报红。
v1 **不自动重画**（角色那套会重画一次）：先响亮报出来，要重画就删 `images/<名>.png` 再跑。
开关沿用 `SHORTDRAMA_SHEET_CHECK`。

**静帧硬伤 QC**：逐镜送视觉模型审查，硬伤为「**字幕 / 烧录文字**（★ 场景固有文字
**不算**，默认放行；见下方第 6 条）/ 缺人物或道具 / 多余清晰人脸 /
血腥 / 分屏多格」，另加**景别跨档校验**。**模型只负责"看图描述"，判不判由代码定**
（`qc.is_hard_issue` 三道闸门，可在 `pack.json` 收窄词表）。
★ **空镜 / 无人像镜不判景别档位**（2026-09-30，`prompt.shot_has_no_person`）：档位
（特写/中景/全景）是**以人物为尺**的，没有人的镜没有"合格景别"。实测 `xiaoman-workshop-1030`
第 2 集 LN01 是空镜，被景别判成"不符"后**自愈重画**，重画提示词还追加"本镜必须是特写"
⇒ 模型往空镜里塞了一个人（判据把它自己的病当药方）。现在这条豁免同时管**判据**与
**重画提示词**两处，只豁免档位、其余硬伤（烧字 / 多脸 / 血腥）照判。
**`P1` 只记录、不处置**（`[media] LNxx 警告（P1，不重画）`）。
负面判定默认走**复采确认**（`QC_CONFIRM_NEGATIVE`，两次都判负面才算硬伤——误报要重画图）。
★ **例外（2026-09-27）**：「**人数 / 主体复制**」类硬伤**豁免复采**（`qc.is_count_issue`），
抓到即重画——它的成本方向相反：多画一个人会顺着 静帧→视频→整组打包素材 一路带下去。
实测：同一轮 LN02/LN06 都被首轮报出"三名女性"，复采一次翻判成"干净"就放行了。
发现硬伤会自动定向重生成（单轮最多 2 次；**跨进程累计上限**
`SHORTDRAMA_STILL_QC_MAX_REGEN`，达上限或判停的镜保留现有静帧并记 `still_residual`）。
★ **一整批硬伤镜一次调用并发画**（2026-09-30）：此前逐镜调生成入口，并发度恒为 1。
线程数走 `SHORTDRAMA_IMAGE_WORKERS`（缺省自动 = 8），**低 rpm 的 key 由 `60/图片rpm`
的闸门保护**，不靠线程数限速 ⇒ 审（`SHORTDRAMA_QC_WORKERS`，吃视觉额度）与
重画（吃图片额度）是两条独立的通道，别一起调高。

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
| `SHORTDRAMA_AGE_VARIANTS` | 1 | **多集连载**：`cast` 按本集分镜的 `@名（括注）` 补「分龄变体 / 没登记的新角色」的定妆照（判据见 `v5/media/variants.py`）。`0` = 整段跳过，行为与改造前一字不变。另两个调参 `SHORTDRAMA_VARIANTS_MIN_SHOTS`（几镜才配一张图，默认 2）、`SHORTDRAMA_VARIANTS_MAX_CARDS`（每集上限，默认 6）见 README §6 |
| `SHORTDRAMA_OPEN_CHAIN` | 0 | **人用开关**：`1` 开放全链路，`--resume-media` 放行 |
| `SHORTDRAMA_ALLOW_RESUME` | 0 | **人用开关**：`1` 放行单次内部恢复 |
| `SHORTDRAMA_REQUIRE_APPROVAL` | 0 | `1` 启用三道**阶段级**审批门（`storyboard` / `stills` / `media`；指纹绑定产物）|
| `SHORTDRAMA_APPROVE_EACH_ROLE` | 0 | `1` 启用**步级 HITL**（图运行中每次派发子代理前 interrupt；配合 `--hitl*` CLI，信道见 `v5/hitl.py`）。**默认关是硬要求**——全自动驱动脚本会挂在第一步。⚠️ **（2026-10-06 更正）** 前端那条路由**不**自带它：`webchain` 的 `manual_steps` 出厂 `"0"`，要**每个请求体自己带**才开（且翻了要重启 dev server、只对下一次生成生效）。三栏工作室 `frontend_new` 的「逐步确认」勾选框就是干这个的；⛔ **绝不要写进 `.env`**（会全局生效、把外部 agent 也挂住）|
| `SHORTDRAMA_HUMAN_IN_CHARGE` | 0 | **人工模式：判断权在人**。`1` 时两道门**降级为报告**：`media_gate` 的「必须 `reviewer.passed`」不再拦、分镜契约门只出警告（判决落 `media/ep{N}/gates.json`）。★ 由**前端路径**自己带上（`runner.start` 写子进程 env，只影响前端那次 run）；⛔ **绝不要写进 `.env`** —— 那会把外部 agent 全自动链路唯一的保护也拆掉。「7 个角色产物齐」**照旧硬拦**（与人在不在无关）|
| `AGNES_VIDEO_MAX_SHOTS` | 20 | **超过 20 镜的项目必须调大**，否则按上限**截断**（会打印醒目警告）|
| `AGNES_VIDEO_MAX_SECONDS` | 12 | 单镜秒数**上限**（供应商硬约束 `seconds ∈ [4,12]`；下限 4 固定）。★ 设成小于 12 会让超长的镜被**静默压短**——2026-09-16 曾因误设 `10` 压短 620 镜中的 9 镜 |
| `SHORTDRAMA_VIDEO_BGM` | 1 | 类型包禁忌 BGM 时设 `0` |
| `SHORTDRAMA_VIDEO_MODE` | reference | `keyframe` 为回退档；`pack` 为 12s 打包档（见上文模式表）|
| `SHORTDRAMA_VIDEO_REF_SOURCE` | **sheets** | **视频请求喂什么图**（2026-10-07 起的新默认，改的是"吃什么"不是"怎么提交"）。`sheets` = 本镜的定妆照（≤2）+ 场景空镜 + 道具图，**静帧不再是输入**（封顶 3 张：图数 > 分镜人数就多画人）；`stills` = 旧行为（只喂本镜静帧）。两轮实跑定案：`madfate-abc-1005-nostill` 15 镜、`xianxia-zhongzhui-1007-nostill` 6 镜。**红利取决于资产图干不干净**——场景卡自带可读文字时会原样进成片（命案集实测 4 镜），所以喂之前先人眼扫一遍图。判据在 `v5/media/assets.sheets_for_shot`，回归测试 `v5/tests_video_sheets.py` |
| `SHORTDRAMA_SHEET_TEXT_GATE` | 1 | 上面那条的**必要配套**：出片前用视觉模型查这批资产图有没有可读文字（按文件指纹缓存，判据复用 `qc.is_hard_issue`）。命中 ⇒ 那张图**剔出请求** + 响亮报资产名；闸门关掉 ⇒ 打一行"这批图没查过"的告警。别把它和 `SHORTDRAMA_STILL_QC` 当成一回事：静帧 QC 查的是"这一镜画得对不对"，这道查的是"我要喂出去的图会不会把字带进成片" |
| `SHORTDRAMA_VIDEO_PACK_MAX_GROUP` | 12 | `pack` 模式单组最多镜数（2026-10-05 从 5 放开；5 的依据「每镜一张静帧」已随 09-28 图序失效。同 README §6）|
| `SHORTDRAMA_VIDEO_SUBMIT_TIMEOUT` | 60 | 生视频**提交**读超时（秒）。多参考图的 pack 组建议 180（60s 会把多图提交判成失败，同 README §6）|
| `SHORTDRAMA_STILL_QC` | 1 | ⚠️ 默认档**不产静帧** ⇒ 这道开关没有对象（前端勾了会打一行告警）。`0` = 跳过静帧 QC。★ **前端路径（`v5/media/runner.start`）默认传 `0`**（人工模式：判断权在人），前端工具栏的「自动质检」开关可打开 |
| `SHORTDRAMA_CLIP_QC` | 1 | `0` = 跳过成片抽帧复核。同上，前端默认 `0`、可开关 |
| `SHORTDRAMA_SLICE_THRESHOLD` | 4000 | 按集切片注入阈值（字符）|
| `SHORTDRAMA_SLICE_SOFT` | 0 | `1` = 切片失败降级为「告警 + 注入全文」|
| `SHORTDRAMA_CHAT_VENDOR` | agnes | 文本通道厂商（`v5/vendors.py` 注册表）。旧名 `NEWDEEP_LLM_PROVIDER` 仍兼容回落 |
| `SHORTDRAMA_IMAGE_VENDOR` | agnes | 生图厂商（同上注册表）。**未注册的名字响亮报错**，不静默回退 |
| `SHORTDRAMA_VIDEO_VENDOR` | agnes | 生视频厂商（同上）。前端三个生成页面各自可选，随请求进该次 run 的**子进程 env**。⚠️ 2026-10-05 起它还可能由**本机接入档案**写入（`v5/local_services.py`，设置面板「本地出片服务」探到 ComfyUI 后设为默认）⇒ 那时**命令行与外部 agent 也会走本地**；显式设过本变量（值 ≠ `settings.env` 出厂那行）赢过档案，A/B 臂照旧有效。详见 README §6「本机出片服务」 |

> **完整清单见 [`README.md`](README.md) §6 配置**（2026-09-18 起它是环境变量的**唯一完整表**）
> —— 本节只列**会改变调用方行为**的那些。语义与理由见 `v5/config.py` 的注释。

## 产物验收

- **成片**：`projects/<项目名>/media/ep{N}/episode_final.mp4`（含音轨）
- **静帧**：`media/ep{N}/stills/*.jpg` + `stills.json`（含每镜提示词与 URL）
- **分镜**：`scenedesigner/scenedesigner_ep{N}.md`
- **渲染任务**：`media/ep{N}/video_jobs.json`（显式状态机：`pending`/`submitted`/
  `completed`/`failed`/`expired`；断点续跑依据）
- **审批记录**：`media/ep{N}/approvals.json`（三道门的 by / at / note / **产物指纹**；默认档下 stills 那道不适用）
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
★ 完成判据认的是**本轮写的**，不是"在盘"：`guards.artifact_fresh(path, since)` +
  `post_validate(..., since=)` / `drive_chain.done_roles(since=)`。**没走 `reset_from` 的调用方**
  让角色重跑时，上一轮的同名文件会让 `exists()` 照样成立 ⇒ "重跑"空转、判决从旧文件解析。
  唯一豁免是 `ep>1` 已锁定的全剧级三件套（`skipped_whole`，它们本轮根本不跑）。
  不传 `since` 时行为与改造前一字不变。
同一角色回退**超过** `SHORTDRAMA_MAX_REVISIONS`（默认 **2**）次后记 `force_passed` **强制放行**
（媒体门认 `passed or force_passed`，不会卡死）。
★ 2026-09-29 才真正接线：此前 `force_passed` **零写入点**、`revision_exhausted()` 零生产调用点，
"不会卡死"是文档超前于代码——评审反复判 fail 的真实结局是重派到撞墙钟预算、`rc=0` 静默收工不出片。
现在的计数源是**门自己**按集累计、落盘的 `review_blocks`（`scripts/run_new_project.py` 也改为
问同一道门，判据只留一份）；放行时日志会列出评审仍未消化的条目，**不许静默**。
★ **2026-09-30：执行打回的是驱动器，不是 supervisor**。上面那句"回退时 phases 被清空
→ 重跑"此前**没有执行者**——`scripts/drive_chain.py` 的收工判据只看"7 个产物在不在盘上"，
于是它拿到 `pass: false` 也照样 break，外层问门、门拦下 rc=1，盘上留下"齐全但不合格"的
产物（实测白跑 64 分钟）。现在改成：产物齐 → 读判决（`reroll_plan()`，纯函数、有测试）→
判 fail 就 `reset_from()` 清本集该角色及下游 + 旧产物进 `.rerun_backup/` → 用
`redo_message()` 只重派那一段。**重试上限沿用门那一份** `SHORTDRAMA_MAX_REVISIONS`，
不新造数字；用完仍不过 ⇒ 照旧交给门 `force_passed`。判不出回退目标时**不动盘**。
★ **2026-10-03：打回重跑时「改哪些」由清单定，「怎么交」由代码定死**。
分镜重跑的输入带程序体检的退回清单（`shotcheck_ep{N}.json`），旧措辞是「只改列出的那几镜」——
模型**照做**就变成了逐镜 `edit_file` + 每改一镜读回整表：实测一轮 **111 次工具调用**
（read 49 / edit 54 / write 8，全打在同一份 `scenedesigner_ep1.md` 上），每次调用在 LangGraph 里
算两步 ⇒ ≈222 步 > 角色递归上限 ⇒ `GraphRecursionError` → 父 run `status=error` →
`drive_chain rc=3` → **45 分钟白烧、媒体链根本没启动、零出片**。
现在契约改成「**一次 `write_file` 交整表**、⛔ 不要逐镜 `edit_file`、不要反复 `read_file` 读回自己刚写的表」，
改动**范围**仍只限清单点名的条目；角色上限 `ROLE_RECURSION_LIMIT` 同时 150 → 260 兜底
（宁可慢一轮，不要"没出口"）。回归测试：`tests_shotcheck.TestWiring.test_rerun_gets_punch_list`。

★ **2026-10-04：闸触发时必须把产物放回盘上，并把乒乓记进评审台账**
（实测 `yoga-affair-1003g`：46 分钟零出片，堵点**是新装的反空转闸自己造的**）。三条：
- **回捞**：打回的动作是"先 `reset_from()` 把旧产物挪进 `.rerun_backup/`，再起一轮重派"。
  那一轮被闸掐掉时盘上是**两头空**（新的没写出来、旧的在归档里）⇒ 启动器报
  「缺 scenedesigner、reviewer → 不进媒体链」。**装闸是为了省时间，却把唯一的出口一起掐了。**
  现在收工前 `guards.restore_stashed()` 回捞**最新一份归档**，
  ⛔ 且**盘上已有新版就绝不覆盖**（那会吃掉刚做完的工作）。
- **记账**：`review_blocks` 原先只在**有人来问门**时 +1，而自动打回发生在创作链里、
  门一次都没被问到 ⇒ 台账恒为 0 ⇒ 门第一次被问只会说"第 1/2 次拦截、还差两次"，
  同一份不合格产物要人被叫三次才放行。现在驱动器每次执行评审打回、以及闸触发收工时
  各记一次（`guards.record_review_block`）——**上限与判决仍然只在门那一处**。
- **重写计数按单轮**：跨轮累计的话，打回后的第一轮**一开局就超限**（那次重写正是打回引起的），
  合法重做被当成乒乓掐掉。跨轮的预算另有 `redo_left` / 门台账，不新造数字。
- **★ 2026-10-05：分段写盘不算「重写」**（同一第二个病，实测 `madfate-abc-1005`：21 分钟零出片）。
  计数原先数"mtime 取到几个不同的值"，而角色**一次派发里分几段写出一份长文档**
  （plotdesigner 写 24316 字的十集目录，相邻两段约 140 秒）正好是 4 个值 ⇒ 闸在角色
  **还在写第一稿**时 cancel 整条 run，盘上只剩 3/7 个产物。现在 `drive_chain.write_bursts`
  先折再数：**首见算第一次落盘**（不是重写），间隔 < 4 分钟的变化并入同一次写作，
  但**文件变短**照计（分段只会长回去，变短只能是从头重写的稿子）。
  取 4 分钟的依据是量级差：1003f 实测相邻两次**真重派**约 16 分钟。折了几次会打一行日志，不静默。
- **★★ 同日第二次误杀后，重写次数不再单独构成停机理由**（16:29 那轮被掐的是
  `scenedesigner`，分段已折掉 1 次仍数到 4，而当时盘上是 **6/7 个产物、只差 reviewer**，
  那张分镜表本身合格）。角色在**自己那一轮里**回头改稿，相邻两次落盘隔的就是"再生成一遍"
  那么久——时间窗折不掉它。⇒ 现在**两条同时成立才停**：① 独立重写超预算，
  ② 且已连续 **45 分钟没有任何角色产物进出盘**（`drive_chain.NO_PROGRESS_SECONDS`；
  新增和消失都算进展，所以合法的打回重做不会被连坐）。仍在前进时只打一行 ⚠️、不停；
  `since_new_seconds` 默认 0（忘传 ⇒ 倾向于**不停**，因为误杀的代价是零出片，漏杀的代价是晚 45 分钟）。
  ★ 同轮的另一个燃料在判据侧：`validate.parse_shot_range` 把「场内 2 镜起」读成镜数声明
  `(2,2)` ⇒ 退回清单报「单镜时长要贴近 60 秒（合格区间 **30-12** 秒）」这种**没人能满足**的条目，
  现在按命中处上下文筛掉（「约 120 秒，共 24 镜；场内 2 镜起」照样读出 `(24,24)`）。
回归测试：`tests_guards.TestExitAfterThrash`、`tests_hitl.TestThrashStop`。

★ **评审的「总时长」阻断理由要拿盘上事实核一遍**（同一条链的直接起因，`1003g` 的燃料）：
审稿写「总时长 88s < brief 硬边界 110–150s」并据此打回整张表，而那张 16 镜表的
「时长(秒)」列**实际加总 120 秒、正落在带内** —— 88 = 它只加了 8 镜×8 秒 + 2 镜×12 秒，
把 2×4 秒与 4×6 秒整个漏掉。分镜因此被重派 5 次。
⇒ `shotcheck.duration_band()` 是程序对**本集表**的确定性读数，
`filter_contradicted_blocks()` 只驳同时满足三条的那**一条**理由：
① 它在断言总时长不合格；② 程序读数在带内；③ 它引用的秒数与程序读数不是同一个数。
其余理由一律原样留下（本次是"关键接触未独占整镜"，那条是真的，照打），
表**真的**不够长时同一句理由也原样留下（反向对照有测试）。
回归测试：`tests_shotcheck.TestDurationBlocksContradicted`。

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

### 改分镜的写路径契约（2026-10-02 补齐，`v5/webwrite.py` + `v5/media/renumber.py`）

网页端（也含任何直接调 `/segments*` 的调用方）改分镜时，**代码负责三件事**，
不要再指望人自己记得：

1. **增删一镜之后素材按镜名重挂**（`renumber.remap_after_edit`）。镜名 `LNxx` 是
   `storyboard.parse` 按**行序**编的，而静帧 / 片段 / 任务表 / QC 计数**全按镜名存**
   ⇒ 在中间删一行会让后面每一镜的名字全体挪位、文件不动 ⇒ **每一镜挂上隔壁镜的画面**，
   且日志全绿、成片能出（实测过）。重挂覆盖 `stills.json`（含条目里的绝对 `path`）、
   `stills/*.jpg` + `.url` 边车、`tails.json`、`still_qc_seen.json`、
   `still_requeue_tally.json`、`video_jobs.json` + `clips/*`。
   ★ **打包档（`VIDEO_MODE=pack`）不猜组号**：组记录里的 `shots` 映射到新镜名后，
   与改动后 `video_plan.group_shots` **重算的分组逐组对账** —— 成员一致才改名保住，
   变了就 `clipqc.invalidate()` 暂存作废并计入 `invalidated_groups`。
   ⚠️ 顺序必须是**先作废后改名**（反过来会把保住的成片当坏组暂存走，实测抓到）。
2. **`ep` 必须一路传到底**。原先 `_load_shots(root)` / `_sb_path(root)` 都不带集号
   （默认第 1 集）⇒ 在第 2 集页面上点保存实际**改的是第 1 集的分镜表**、
   作废第 1 集的素材，而第 2 集一字未改、界面照样弹「已保存」。
   回归测试：`tests_webwrite.TestShotRenumber.test_write_paths_honor_episode`。
3. **前端点「新增镜头」加出来的镜自带合法时长与无声对白**
   （`_NEW_SHOT_SECONDS=6` / `SILENT_DIALOGUE=「（无声，环境音）」`），
   使 `validate.check_storyboard` 不再出「时长不是数字」「空对白」两条阻断项——
   而**前端路径**跑在人工模式（门只报不拦），空镜会真的被送去生成 = 白烧配额。
   画面描述仍是 `[[待补]]` 占位，返回体带 `needs_visual: true`，前端据此**禁止**对它生成。

`GET /projects/{pid}/progress` 现在收 **`?ep=N`**（缺省 1，`ep<1` → 400）。
不传就永远按第 1 集算 `v5.render` / `cover` / `flow` ⇒ 多集连载会串集。

## 三栏工作室 `frontend_new/`（2026-10-06 起）与导演信箱

**它是什么**：`/studio` 同源挂载的第三个前端（左=项目栏 / 中=atelier 画布 / 右=导演对话）。
`frontend/` **一字未动**，两者共用数据层（`api.ts` / `types.ts` / `lib/quality` / `lib/chainMode`），
视图层各走各的。启动台两个入口都调同一个 `ensureShortdrama()` ⇒ 一次起服、两个界面。

```bash
# 构建（产物入库：release 不会自动跑任何 shortdrama:* 构建脚本）
cd frontend_new && npm install && npm run build           # base=/studio/ 写死在 vite.config
cd atelier && MSYS_NO_PATHCONV=1 VITE_BASE=/atelier/ npm run build   # 桥在 atelier 里，必须重构建
python scripts/studio_smoke.py                            # 12 条接口验收（自建夹具，跑完删）
node ../scripts/studio_shot.mjs <项目名>                   # 无头浏览器四项（需后端在 8787）
```

### 入站信道 `v5/inbox.py`（★ 这条是新的，改链路前必读）

补的是**两个真实缺口**，不是"顺手加功能"：

1. `hitl.decide` 要求链**正挂在步级门上**，而 `manual_steps` 默认**关**
   （`webchain.py:116`，AGENTS.md 旧版那句"前端起的 dev server 自带它"**是过期的**：
   它只在前端请求体带 `manual_steps` 时才开）⇒ 链一路跑到底时人说的话无处可去。
2. `webwrite` 改分镜表**不碰链的失效机制**（phases 不动、`reviewer.passed` 照样绿）
   ⇒ 人改完，导演下一轮读到的仍是"表没被改过"。

**投递语义两种，别混（作废条件不同）**：

| 条目 | 谁能读到 | 什么时候消失 |
|---|---|---|
| `message` | 下一个派发的角色；`to` 定向时**别的角色跳过而非吃掉** | 投递一次即记 `delivered_to` |
| `edit`（画布/网页改表的台账） | 只有两个**表主**（`scenedesigner` / `reviewer`） | 分镜表被重写（指纹变了）当场剪掉 |

⛔ `message` 不广播：一条给分镜师的话被 `worldbuilder` 消费掉，**没有任何一处会报错**。
要广播就重发一次 —— 显式比隐式好。
⚠️ `edit` 不"消费一次"：改了三镜，这三镜的账在表被重写前**每一轮都必须在场**。

**注入点只有一个**：`roles.role_input` 末尾（`v5/roles.py`）。无条目时返回空串 ⇒
不开这条信道时，派发给角色的文本**一个子都没多**（`tests_inbox` 有这条零回归证明）。

**「让导演重做」是显式动作，不自动**：`POST .../director/redo` 三条出口按盘上事实选 ——
挂着 → HITL 打回；有非终态 run → **409 不制造第二写者**；否则先 `reset_from` 再起链
（⛔ 不撤销记账就重跑 = 空转，见 `guards.reset_from` 的物化守卫）。
理由：`reset_from(scenedesigner)` 会重做该角色**及全部下游**（实测 ≈95 分钟），
改一个错字也重做一遍不可接受。

### 画布双向同步的边界（★ 别照着"节点可编辑"去实现，那是错的）

- **表 → 画布**：宿主轮询 `GET .../canvas?ep=N`（后端每次重读盘），`fingerprint` 变了才
  postMessage 推给 iframe；画布侧做**保位合并**（只更新产物事实，人拖过的坐标保留）。
  ⚠️ 桥**必须走 `setNodes`**，不能走 `useCanvasStore.updateProject` ——
  `project.tsx` 把 nodes 存在组件本地 state，`:391` 那条 effect 每帧把本地 state 推回 store，
  从外面写**下一帧就被覆盖**（表现是"推进去了但画面没变"）。
- **画布 → 表**：入口是**选中**（`pixa:select` 报 `s:LNxx` → 宿主镜头检查器切到那一镜），
  编辑走 `POST /segments/{sid}` 带 `source=canvas`。
  ⛔ **不许**把节点的 `metadata.prompt` 写回「画面描述」列 —— 那是**组装后的静帧提示词**
  （含类型包风格块 / 参考图声明 / 反烧字条款），写回去等于把整列污染成机器文本，
  下一轮静帧就在错误指令上重画。
  ⚠️ 检查器初值取 `scenes[0].shots[0].content_plain`，**不是 `summary`** ——
  后者是 `_first_sentence(visual)`，拿它回填再保存会把这一镜**截成一句**。
- **桥是惰性激活的**：没收到宿主消息时一条行为都不发生 ⇒ `frontend/` 直接开 iframe 的
  旧用法不变。改 `atelier` 前先看这条，否则会把"没在用"误判成"没生效"。

### ★ 和导演**直接对话**：聊清楚了再开工（2026-10-07，`v5/director_chat.py`）

**它是什么**：右栏的对话**发给 supervisor 本人**，他回答你；你点「开工」，完整创作链
就跑在**同一段对话**上 —— 他对每一步的说明（"规格已落盘"「派 scenedesigner」
"创作链已跑完"）落在同一条时间线上，中间画布同时开始长格子。

**补的是什么缺口**：在这之前机器上**没有任何东西在跟 supervisor 说话** ——
唯一的驱动是 `drive_chain` 那个"点火器"（每次新建一次性会话、丢一句开工指令、跑完就丢）。
HITL 是唯一能往图里塞话的口子，而它的中断只挂在 **`task`（派活）**这一个动作上
（`orchestrator._interrupt_on()`）⇒ 链没跑时人说的话**无处可去**。

**为什么他不误开工**（这条路唯一的未知项，已实测）：他的提示词是「你是监制，按依赖序派活」，
**没有"只回答不做事"的模式**。按住他的办法是**在消息里说清这是对话阶段**
（`CHAT_PREFIX`）—— 实测临时项目：回了一大段正经回答、**盘上新增文件 0 个**。
⛔ **不要去改他的提示词**：那是共享的、编译期生效的东西（改了要重启 dev server）。

**"按住他"和"喂饱他"是配套的两件事**：既然禁止他调用工具，他读不到 `/brief.json`、
也读不到产物目录 ⇒ 每句话都要**附上盘上现状**（`_digest`：brief 关键字段 + 各角色落盘情况 +
镜数）。第一版没喂，他会老实说"我还没拿到 brief"、只能给通用套话。

**开工怎么接上**：`POST .../director/start` → `runner.start(kind="chain", chain_thread=<那段会话>)`
→ `drive_chain --thread <id>`（**新增的可选参数，不传=自己建新线程，CLI 与外部 agent 的
原行为一字不变**）。

**两条容易写错的地方**（都踩过，回归测试见 `v5/tests_director_chat.py`）：

1. `_pull_new` **只搬他说的，不搬我们发出去的**。两边都记 ⇒ 界面上每句话出现两次
   （一次干净、一次带 `_digest`+前缀）。用户那一侧由 `submit` 自己记。
2. `_poll` **必须无条件搬运**，不能只在"本模块发起的 run 收工了"时搬 ——
   开工走的是 `runner.start`（不经过 `submit`），本模块压根不知道有 run 在跑。
   第一版这么写的结果：角色产物全落盘了、**对话里一句话都没多**。

**三条边界**：
- 会话是**项目级**的（换集不换对话），状态在 `<项目>/.tmp/director_chat.json`；
  dev server 被 `ensure_devserver` 归档 `.langgraph_api` 之后旧线程会 404 ⇒
  自动换一条新的（本地时间线保留，不丢人看过的记录）。
- 对话**不能半路插进正在跑的生产** —— 那是上面那条"中断只挂在派活那刻"决定的。
  所以是「先说清楚 → 再点开工」，不是「边跑边聊」。
- ⛔ 别在真项目上试这条链路（会花真实文本额度）。要试就自建临时项目，跑完删。

### 对调用方的影响

- 新路由：`GET|POST .../projects/{pid}/director/{inbox,message}`、`POST .../director/redo`、
  **`GET|POST .../projects/{pid}/director/chat`**、**`POST .../projects/{pid}/director/start`**。
  `chat` 的 GET 是轮询端点（顺带把新发言搬进时间线），POST **不等回答**（提交即返回）。
- `POST /segments/{sid}` 与 `POST /episodes/{eid}/segments` 多收 `source` / `by`
  （只进台账，不影响写盘）。⚠️ 调用方传了它们就要**指望服务端 pop 掉** ——
  漏了会被列进 `skipped` 并回一条"这些列不存在"的假警告（`server.py` 已处理，别退回）。
- 实时性**仍是轮询**（后端无 SSE/WebSocket，`api.ts:366` 那句"前端不依赖长连接"照旧成立）。
  真正的延迟地板是 `drive_chain` 自己 20 秒比一次产物，前端快于它是白快。

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
