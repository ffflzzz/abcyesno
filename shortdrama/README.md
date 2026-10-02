# shortdrama — 多智能体 AI 短剧生产系统

把一句话主题，变成一集成片。创作链由 8 个角色 Agent 串行完成，媒体链走**静态画面先行**管线（静帧即生产首帧 → 图生视频 → 拼接）。

> 外部 Agent 调用规范见 **[AGENTS.md](AGENTS.md)**（写 brief.json → 调 CLI → 验收产物）。本文件说明系统本身。

## 1. 两条链路

```
创作链（supervisor 架构 · 2026-09-12 起唯一创作链）
  supervisor 图（director 主 agent + AsyncSubAgentMiddleware）
    ├─ start_async_task 派发 → 每个角色跑在独立 thread（经 Agent Protocol）
    └─ 依赖序：worldbuilder → assetdesigner ∥ plotdesigner → scriptwriter
               → dialogue → scenedesigner → reviewer
    reviewer 判定：pass=true → 创作链完成
                   fail    → 按 reasons 定向重派被点名角色
                              ↓
媒体链（**图外**：`v5.media.pipeline.run` 独立入口，任何调用方都绕不过守卫）
  分镜 → 镜间关系规划 → 逐镜静帧（= 视频的参考图 `<Picture 1>`）→ 静帧硬伤 QC
       → 图生视频（**reference 模式**，2026-09-13 起）→ 成片抽帧复核 → 拼接
```

**媒体链为什么不在图里**：它是**确定性流水线**而非 agent，做成节点会让 `ainvoke` 阻塞 30–60 分钟，"图跑完了吗"变得含糊；更关键的是，守卫写在节点里时 `--resume-media` 那条入口会**整个绕过** `media_gate` 与渲染记账（实测：8 角色没跑完也能渲染）。现在 `guards.media_gate("render")` 收在 `pipeline.run` 内部——**任何调用方都绕不过**。

每个角色内部仍是一个独立 agent（各自的 SKILL / 文件工具 / 模型），**输入只给路径**（brief 与上游产物路径），让它自己 `read_file`——不把全文塞进上下文。

**为什么创作链用 supervisor 而不是静态 DAG**：早期"模型自由派活"有两个死结——并行派出全部 8 个、或只跑 2/8 就进媒体链；静态 DAG 解决了顺序但把编排写死在代码里（改流程要改图）。
supervisor 用**纪律**（依赖序 + 单轮 check + 失败两次停下，见 `v5/orchestrator.py`
的 `ORCHESTRATOR_DISCIPLINE`）+ **真实独立 thread**（`async_tasks` channel 可查证）达到同等约束，
且流程可对话式调整。

## 2. 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows；Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
# 复现既有环境请用锁文件（117 包精确版本，重建/排障时以此为准）：
# pip install -r requirements.lock.txt

# 必需凭据说在 .env（仓库根，已 gitignore）
echo "AGNES_API_KEY=sk-..." > .env
# 可选：多条 key 走「提交轮转配速」——实测 1rpm 是 per-key，故提交段可按 key 数摊薄
echo "AGNES_API_KEYS=sk-...,sk-...,sk-..." >> .env
echo "SHORTDRAMA_VIDEO_KEY_ROTATE=1" >> .env
```

跑一个项目：

```bash
# ✅ 全流程（创作链 + 媒体链）——推荐路径

# ✅ 只读旁监控（可选）：不写盘、不推进图
python -m v5.series <项目名> --monitor

# ⛔ 默认拒绝：跳过创作链直接跑媒体（需 OPEN_CHAIN 或 ALLOW_RESUME，放行后过三道输入门）
python -m v5.series <项目名> --resume-media
python -m v5.series <项目名> --resume-media --stills-only
```

### 多集连载（`--episodes`）——一条命令跑 N 集，**一次起服**

```bash
# 全链路：第 1..3 集（dev server 只起一次 ⇒ 不再逐集重启）
python scripts/run_new_project.py <项目> --episodes 1-3 --stills-qc
# 只跑创作链（不烧图像/视频配额，最快的形态验证）
python scripts/run_new_project.py <项目> --episodes 1-3 --chain-only --stills-qc
# 只跑媒体链（创作链已就绪；逐集过门 → pipeline.run(ep)）
python -m v5.series <项目> --resume-media --episodes 1-3
# 单集（老写法，仍完全支持）
python scripts/run_new_project.py <项目> --ep 2 --stills-qc
```

`--episodes` 支持 `1-4` / `3` / `1,3,5` / `1-3,7`。**任何一集被门拦下即停止**（不静默跳过——
否则"出了 3 集"会看起来像"出了 4 集"）。

三条必须知道的语义：

| 项 | 规则 |
|---|---|
| **产物按集隔离** | 集级产物落 `*_ep{N}.md` / `media/ep{N}/`；**全剧级**产物（`worldbuilder.md` / `assets.md` / `episodes.md`）**只在第 1 集生成，第 2..N 集自动跳过**（否则每集重写会让上一集的分镜引用旧角色卡，而参考图不会跟着重画 ⇒ 跨集错位） |
| **状态按集隔离** | `phases` / `media_loop` 都是 `{ep: {...}}`；门**不会**拿第 1 集的 `complete` 放行第 2 集。换集只重置 `revision_counts` / `token_usage`（每集是一次新运行） |
| **`--fresh` 与多集互斥** | 脚本**直接拒绝**（`rc=2`）：`--fresh` 归档整个项目目录，会挪走**别的集**的产物 |

**多集 brief 的字段语义与单集不同**（`episodes > 1` 时门会主动提示）：`must_have` 必须是
「**每集都能覆盖**的硬要求」（如"每集至少一个笑点"），**不是全剧四幕**——FIDELITY 门按
**单集分镜**校验覆盖率；`target_duration` 是**每集**时长（不是合计）；全剧起承转合与结局
写进 `plotdesigner` 目录的卷首。

**长剧（集数多）才有的一条**：全剧级目录超 **`SHORTDRAMA_SLICE_THRESHOLD`（默认 4000 字符）**
即**按集切片注入**（只注本集 ±1 + 本卷摘要，切片可见；切不出来默认**响亮终止**，
`SHORTDRAMA_SLICE_SOFT=1` 降级为注入全文）。⇒ `plotdesigner` 必须按「卷 + 集条目」结构产出，
且**集条目自包含**（禁"同上"，切片后会悬空）。

### ⛔ 外部调用方准入（2026-09-10 起，取代原「调试期铁律」）

**策略：开放 + 三道门守输入。** 全链路默认可用；`--resume-media` 需显式放行，放行后输入由三道门把守：

| 入口 | 状态 | 说明 |
|---|---|---|
| 全链路（`scripts/run_new_project.py`） | ✅ 默认可用 | 推荐路径：写 brief.json → 调脚本 → 验收产物 |
| `--monitor` / `--watch` | ✅ 可用 | 只读观察，不写盘、不推进图，发现问题只报告 |
| `--rerender <镜号>` | ✅ 默认可用 | **单镜重渲**（`--from still` 连静帧一起重做；逗号分隔多镜）。媒体链唯一入口的**受限调用**——gate / 记账 / 幂等 / 限流全部照常 |
| `--resume-media` / `--stills-only` | ⛔ 默认拒绝 | 需 `SHORTDRAMA_OPEN_CHAIN=1` 或 `SHORTDRAMA_ALLOW_RESUME=1` |

**三道输入门**（`series._input_gates`）：

1. **brief 门** —— 缺必填字段 → `BRIEF-REJECT`
2. **分镜契约门** —— 缺列 / 镜序错乱 / 空对白 / 要求画内文字 / 未覆盖 `must_have` / 违反 `audio_mode` → `STORYBOARD-REJECT`
3. **资产契约门** —— 分镜点名的角色在注册表里没有身份锚点 → 阻断

**仍然禁止**：手改 `.agent_state.json`、手删 `clips/*.mp4`、手喂 `scenedesigner.md` 直接渲染——这些绕过一切守卫，破坏的是可复现性。

**为什么改为开放**：只读监控是**调试期纪律**而非架构。外部 Agent 本是系统主入口，长期锁死会限制可用性——门槛从"不许调"换成"输入必须过关"。默认仍锁（稳妥迁移），由人按项目放开 `SHORTDRAMA_OPEN_CHAIN=1`。

**耗时参考**（32 镜 / 4.9 分钟成片实测）：静帧 32 张 ≈ 5 分钟；视频串行链 ≈ 86 分钟（约 2.7 min/镜，受供应商 1rpm 闸门约束）；拼接 < 1 分钟。

**多 key 提速（2026-09-16 实测）**：视频提交的墙钟瓶颈是**每 key 60s 的提交闸门**，不是渲染本身——实测同一条 key 在 60s 内第二次提交返回 **429**，而**不同 key 间隔 2s 都通过**（⇒ 1rpm 是 per-key，非 per-account；被拒的 key 过窗后立即恢复，不是配额耗尽）。因此开启 `SHORTDRAMA_VIDEO_KEY_ROTATE=1` + 多条 `AGNES_API_KEYS` 后，`submit_all` 的**提交段**从 `65s × N` 摊薄到约 `65s × N / key数`（40 镜 / 3 key：约 43 分 → 约 15 分）。
**不要按 key 数反推总时长**：轮转只摊薄提交节流，**不加快单镜渲染本身**，总时长降幅小于 key 数倍。复现探测：`python scripts/probe_multikey.py`（前 4 阶段零配额；`--spend` / `--control` 才真提交，成本 ≤ 数个 4 秒视频）。
另：`video_id` 是**账号维度**（任意 key 都能 `query_video`），故续跑轮询不依赖提交时用的是哪条 key。

**断点续跑**：`video_jobs.json` 是显式状态机——`completed` 且成片在盘才跳过，`submitted` 会认领原任务继续轮询，`failed` / `expired` 会重新推进。中断后重跑同一命令即可续上。

## 3. 只读监控模式（`--monitor`）

外部 Agent 与人都可以用这个模式观察运行中的项目。它**严格只读**——2026-09-10 起它不再是"唯一允许的动作"，而是**可选**的观察方式（生产期间用它观察仍最安全）：

```bash
python -m v5.series <项目名> --monitor                 # 一次快照（默认）
python -m v5.series <项目名> --monitor --seconds 600   # 持续观察 10 分钟
python -m v5.series <项目名> --monitor --events 12     # 最多 12 次快照
```

输出示例：

```
[MONITOR] 只读观察（不写盘、不推进图）project=paperface-2 pack=shortdrama
[MONITOR #1] phases 3/8 complete | review=未评审
```

**它不做什么**：不 `ainvoke` / 不 `astream`（那会真的推进图）、不写文件、不改 `.agent_state.json`、不删 clip、不代建项目目录、不代跑角色。

**它做什么**：读 manifest 与产物 mtime，报告 `phases N/8`、评审结论；发现异常（角色 failed / 记为 complete 但产物缺失 / 评审不通过）就**打印问题并停止**，把决定权交回给人。

> 排查问题的正确姿势：监控 → 发现问题 → 停下来报告。**不要"顺手修一下"**，那会让排查失去可复现性。

## 4. 类型包

类型包决定审美与工艺，位于 `v5/skills/packs/<包名>/`。

| 包 | 风格 | 备注 |
|---|---|---|
| `shortdrama`（默认） | 写实电影感、竖屏真人质感 | 回落基准：缺 `pack.json`；**有** `style-block.md`（静帧尾缀缺省档 `material`） |
| `niulai-movie-style` | primitive folk CGI（故意低质的 bootleg 3D，**精致 = 失败**） | `still-refs: false`：不绑参考图，改由 `style-block.md` 逐镜锁定审美；`still-tail: flat`、`style-is-criterion: true` |
| `wool-felt-story-short` | 现实世界比例的羊毛毡世界（**精致 = 正确**） | `still-refs: true`、`still-tail: material`、`style-watch: true`（只记不改）、`hard-keys-drop: [五官, 面部特征]` |
| `chinese-style-short-drama` | 真人国风短剧（BJD 瓷肌 + 华丽古风妆造） | 移植自 MiniMax Design v1.2.6；`still-refs: true`、`still-tail: material`、`style-watch: true`、**不开** `style-is-criterion`；另有 `references/`（源 skill 全文 + 表情/眨眼速查）。**未实测** |
| `half-narrated-live-action` | 半解说真人短剧（旁白推进剧情，对白补情绪） | `still-refs: true`、`still-tail: material`、`style-watch: true`；`audio_mode` 主张 `narration-led`（**需在 brief 显式声明**）；`script-craft` = 开场法则 / 钩子 / 爽点（**不启用**微表情）|
| `madfate-grim` | 命案·都市残酷惊悚（港产残酷犯罪剧质感） | `still-refs: true`、`still-tail: material`、`style-watch: true`、**不开** `style-is-criterion`；**刻意不启用**商业短剧 `script-craft`（「憋闷」与「每集必有钩子」对打）；风格卡 `styles/madfate-grim.md` |
| `laofuzi-hk-retro` | 老夫子 IP 老港片复古风 | 4 个角色（director / worldbuilder / assetdesigner / reviewer），其余回落 `shortdrama`；风格卡 `styles/laofuzi-ai-remake.md`。⚠️ 含第三方 IP，商用需授权 |
| `xianxia-vfx-action` | UE5-Niagara 仙侠特效动作·高精度游戏 CG（**横屏**、**无对白**） | 2026-09-26 从用户提供的两条 Ref2VA 提示词蒸馏；自带 5 个角色（director / worldbuilder / assetdesigner / scenedesigner / reviewer），其余回落 `shortdrama`；`still-refs: true`、`still-tail: material`、`style-watch: true`、`script-craft: []`；**主资产是镜头语法不是氛围词**（要压进 brief 的是**三条语义律**：能量色一致／远程攻击只有板状剑罡／尾镜接续；可数的条款 2026-09-29 起由 `v5/shotcheck.py` 出带镜号退回清单，不再要求角色逐镜自查。`At 00:02.200` 式时间码必须翻译成 `0-3秒：`），依据档案 `.../references/vfx-shot-grammar.md`。**未实测** |

**静帧尾缀 `still-tail`**（pack.json 字段，2026-09-12 新增）：每张静帧提示词的收尾约束，
`material`=「表面为真实连续的材质」/ `flat`=「表面只是平涂纯色块」/ `none`=不注入；
它同时决定硬伤重画时的反烧字强化句。**写实与 3D 包必须 `material`**，否则会与风格块的
"真实材质"对打（noodle-night 事故）；只有本就要平涂的反质量包才用 `flat`。缺省 = `material`。

**`pack.json` 字段表**（2026-09-16 按代码校准）：

| 字段 | 取值 | 谁在读 / 作用 |
|---|---|---|
| `still-refs` | bool，缺省 `true` | `style.still_refs_enabled` → 决定 `cast.ensure` 是否生图、`assets.bind` 是否逐镜绑参考图。`false` 时身份改由 `identity_lines` 文字锚点承担 |
| `still-tail` | `material` / `flat` / `none`，缺省 `material` | 静帧收尾约束档位 + 硬伤重画的反烧字强化句 |
| `style-is-criterion` | bool，缺省 `false` | 风格当**硬判据**：QC 追加该节后"画风被拉向写实/精致"可判 **P0**。它的注入文本写死了「反质量类型包」前提，**只给反质量包用**（追求真实质感的包用它判据方向相反，会稳定误判） |
| `style-watch` | bool，缺省 `false` | 风格**只记不改**轴（2026-09-16 新增）：把 `visual-style` 作为「期望形态」注入 QC，但**只许报 P1** —— 静帧 QC 不重画、clipqc 只打印。**它只让漂移可见，不自动纠正** |
| `visual-style` | str | 上两条风格轴的**判据文本**，也是前端风格预览的说明。用 `style-watch`/`style-is-criterion` 时必须写，否则该轴为空 = 不判 |
| `script-craft` | list[str] | 技法库的**包级默认**注入清单（项目 `brief.json` 的同名字段可覆盖）。见下方 craft 段 |
| `hard-keys` / `hard-keys-add` / `hard-keys-drop` | list[str] | **硬伤关键词表**的 pack 级覆盖（2026-09-16 新增）。全局表里的 `缺`/`五官`/`面部特征` 是**子串**，与「要求五官清晰可读」的包天然抵触。三者都没写 = 用全局 `qc.HARD_KEYS`。⚠️ **闸门本身不能删**（它拦的是「模型标了 P0 但描述不指向真问题」的误报），只能收窄词表 |
| `name` / `version` / `source` / `display-name-zh` / `roles` / `trigger-words` / `audio-modes` / `default-audio-mode` / `default-video-model` / `shot-duration` / `aspect-ratio` | — | **元数据 / 前端展示**：`webmap` 只读 `display-name-zh`（回落 `name`）与 `audio-modes`；其余**后端不消费**（`v5` 里 0 处读取）。⚠️ 改 `aspect-ratio` **不会**改画幅（画幅由 `SHORTDRAMA_ASPECT` 控制）；改 `default-audio-mode` **不会**改音频模式（模式只认 `brief.audio_mode`，缺省回落 `dialogue-led`）|

**`craft/` 不是类型包，是技法技能库**：`skills/packs/craft/<技法名>/SKILL.md` 提供可复用的具体手法。它**无 `pack.json`、不定义角色**。当前 **5 个**：`pixar-lighting` / `short-drama-hooks` / `short-drama-opening` / `short-drama-satisfaction` / `micro-expression-acting`。**已接线**（2026-09-16 起，`v5/media/style.py` 的 `script_craft_of()`/`read_craft()` + `v5/roles.py` 注入）：brief.json 的 `script-craft` 列表（项目级，显式覆盖）> pack.json 的 `script-craft`（包级默认），**opt-in、未声明不注入**；每份技法 frontmatter 用 `inject-to: [角色, …]` 声明注入给谁（漏写=不注入任何角色并打告警）；技法名写错/文件缺失**响亮报错**，不静默。

**角色 skill 回落**：某包缺某角色的 `SKILL.md` 时自动回落到 `shortdrama` 同名角色。例如 `niulai-movie-style` 只定义 5 个角色，其余 3 个走 shortdrama —— 这是设计内的。

**导入新包**：建角色 `SKILL.md` + `pack.json`；若该包有强审美主张，**务必加 `style-block.md`**（逐镜注入，是画质的主要锁定手段）。细节放 `references/` 按需读，不要有损提炼。

### 音频模式（`brief.audio_mode`）

| 取值 | 行为 |
|---|---|
| `dialogue-led`（默认） | **必须有台词**：台词镜占比 ≥20%；20%~50% 出警告；<20% 判违规 |
| `silent` | **不出台词**：对白列统一「（无声，环境音）」，靠音效叙事；出现台词判违规 |
| `narration-led` | **旁白推进剧情**：旁白写进分镜「音效」列的画外音格式（`画外音（音色）：台词`），由视频模型**直接烘焙成音轨**；旁白镜「对白」列写「（无声，环境音）」、画面描述标注**人物闭嘴**。⚠️ 旁白文本**绝不进「对白」列**（会被当角色台词念 + 驱动口型）。独立 TTS 音轨（`v5/media/narration.py`）是**烘焙失败时的兜底**，默认关（见 §6 `SHORTDRAMA_NARRATION_TTS`）|
| 缺失 / 未识别 | 按 `dialogue-led` 处理（默认要台词，不再默默无声）。⚠️ `pack.json` 的 `default-audio-mode` **代码不消费** —— 想要 `narration-led` 必须在 **brief.json 显式写** `audio_mode` |

生效点有三处，缺一不可：

1. **开工前注入**（`graph.role_input`）——给 `dialogue` / `scenedesigner` 的输入里写死硬性要求；
2. **事后确定性校验**（`graph._audio_mode_defect`，reviewer 节点调用）——违规直接判不通过并回退 `scenedesigner`；
3. **续跑路径同判据**（`series._storyboard_gate`）——`STORYBOARD-REJECT` 里包含该条。

> 历史教训：`audio_mode` 曾是**空转字段**（只在字段名白名单里，无人消费）。实测 paperface-2（0/19）与 umbrella（0/9）brief 写着 `dialogue-led` 却全片无声，成片被用户当场退回。
> （两个项目的产物均已不在 `projects/` 下。）

## 5. 产物布局

```
projects/<项目名>/
├── brief.json                  # 需求（外部 Agent 产出）
├── assets.json                 # 资产注册表：角色/道具身份锚点
├── images/                     # 参考图
│
│   ── 全剧级（一次锁定，全剧复用；第 2..N 集**不再重跑**）──
├── director/director.md
├── worldbuilder/worldbuilder.md
├── assetdesigner/assets.md
├── plotdesigner/episodes.md    # 分集目录（卷 + 集条目，供按集切片注入）
│
│   ── 集级（每集一份：`{N}` = 集号）──
├── scriptwriter/scriptwriter_ep{N}.md
├── dialogue/dialogue_ep{N}.md
├── scenedesigner/scenedesigner_ep{N}.md   # 分镜表（媒体链输入）
├── reviewer/review_ep{N}.md
│
└── media/ep{N}/
    ├── stills/                 # 静帧（= 生产首帧）+ stills.json
    ├── clips/                  # 每镜成片（断点续跑依据）
    ├── video_jobs.json         # 每镜 video_id 与首帧来源
    ├── approvals.json          # 三道审批门记录（by / at / note / 产物指纹）
    └── episode_final.mp4       # 成片
```

`projects/<项目名>/.agent_state.json` 是黑板状态机（阶段 / 产物 / 修订 / token 账本），**勿手改**。
`phases` 与 `media_loop` 都是**按集**的（`{"1": {...}, "2": {...}}`）；旧项目的**一维**结构仍可读
（只对第 1 集成立）。老项目盘上的产物是**旧名**（`scenedesigner.md` / `review.md`）——
读取一律走 `guards.resolve_path()`（新名优先、**旧名回退，且只对第 1 集**）。

## 6. 配置

环境变量写在 `.env`（仓库根）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGNES_API_KEY` | — | **必需**（生图 / 生视频 / LLM）。语义 = key 池中**第一条**。key 文件 `.env` 允许两处：`shortdrama/.env` 优先，找不到再读仓库根 `.env`（读到即停，勿两处并存——防轮换漂移） |
| `AGNES_API_KEYS` | — | 可选：多条 key（逗号/分号/换行分隔，去重有序）。**每条可带自己的地址与限速**：`key@base#video_rpm[:image_rpm]`（如 `cpk-xxx@https://api.agnes-ai.cn/v1#5:80` = 国内入口：视频 5rpm、图片 80rpm；尾部 /v1 自动剥）。标了 image_rpm 的 key 会被静帧/资产图自动优先使用（`config.image_pool_keys()` 组成生图专用池，闸门 `60/rpm` 按 key 独立计时；不占视频配额）。未标的走全局 `AGNES_BASE` 与全局闸门。配合 `SHORTDRAMA_VIDEO_KEY_ROTATE` 使用；未设则退回单条 `AGNES_API_KEY` |
| `AGNES_VIDEO_MAX_SHOTS` | 20 | **超过 20 镜的项目必须调大**，否则静默截断 |
| `AGNES_VIDEO_MAX_SECONDS` | 12 | 单镜秒数**上限**（供应商硬约束 `seconds ∈ [4,12]`；下限 4 固定）。★ **别调小**——设小于 12 会让超长的镜被**静默压短**（与分镜契约「单镜 4-12 秒」也不一致）|
| `SHORTDRAMA_OPEN_CHAIN` | 0 | **人用开关**：设 `1` 开放全链路（`--resume-media` 放行，输入过三道门） |
| `SHORTDRAMA_ALLOW_RESUME` | 0 | **人用开关**：设 `1` 放行 `--resume-media`（单次内部恢复） |
| `SHORTDRAMA_REQUIRE_APPROVAL` | 0 | 设 `1` 启用三道审批门（storyboard / stills / media） |
| `SHORTDRAMA_CHAT_KEY_ROTATE` | 1 | **文本通道换 key**（2026-09-30）。撞 429 就领下一条 key 重试，被拒的那条按供应商给的恢复时间冷却（文案里没有时间点时用 `..._COOLDOWN_SEC`）。此前文本**永远只用池里第一条**，那条额度到顶就整条链 20 秒内 `status=error` 停摆，而实测另两条的文本额度是好的（额度按 key 算，非账号级）。候选集只收**标了专属地址**的 key（`config.chat_key_pool()`）；一条都没标 ⇒ 只有一个候选 ⇒ 行为与改造前逐字节一致。设 `0` 强制退回单 key |
| `SHORTDRAMA_CHAT_COOLDOWN_SEC` | 120 | 文本 key 撞 429 后的默认冷却秒数（供应商文案里给了"请在 X 之后重试"时**以它为准**，最长记 8 小时） |
| `SHORTDRAMA_VIDEO_BGM` | 1 | 类型包禁忌 BGM 时设 `0` |
| `SHORTDRAMA_STILL_CHAIN` | 1 | `0` = 并铺式（快，但连续镜画面重复） |
| `SHORTDRAMA_TAIL_PREGEN` | 0 | 落幅帧预生成 → 视频可平铺。**代价**：预生成落幅 ≠ 实际尾帧，承接由"事实"降为"预期"；无连续镜则零收益 |
| `SHORTDRAMA_STILL_RATIO` | 3:4 | 静帧比例 |
| `SHORTDRAMA_DIALOGUE_LANG` | Mandarin Chinese | 台词烘焙语言声明（纯**英文**语言名，如 `Cantonese`——含中文字形会被烧上屏幕，见 prompt.py LANG_SUFFIX 注释）。粤语项目设 `Cantonese` |
| `SHORTDRAMA_STILL_QC` | 1 | `0` = 跳过静帧 QC |
| `SHORTDRAMA_STILL_QC_MAX_REGEN` | 3 | 单镜累计重画上限（跨进程） |
| `SHORTDRAMA_SHEET_CHECK` | 1 | **定妆照 ↔ 角色卡 对账**。`0` = 不核（回到"图错了也没人发现"的历史行为）。判据：视觉模型逐项报"图上这一项长什么样"，**判定归代码**（颜色族不相交／明确没画／断剑画成完整）；抄不出卡片原文的条目一律不算。理由：0929 成片 18 镜继承了错图（卡片`玄黑高马尾`、图上酒红发），而内容指纹只保证"卡片改了会重画"、reviewer 只作文本对文本 |
| `SHORTDRAMA_SHEET_CHECK_MAX_REGEN` | 1 | 对账不过时重画几次（**硬上限**）。到点即保留现有图并响亮记 `sheet_residual`，不循环——视觉判据是概率性的 |
| `SHORTDRAMA_QC_STOP_REPEAT` | 1 | `1` = 同一镜**连续两轮报同一类**问题时不再重画（直接记 `still_residual`）。理由：重画的强化约束只由"类别"决定 ⇒ 同类 ⇒ 提示词完全一致 ⇒ 再画一次只是**换种子抽奖**。设 `0` 回到"一路重画到上限" |
| `SHORTDRAMA_QC_CONFIRM_NEG` | 1 | `1` = 只对**判负面**的镜复采一次判定，两次都判负面才算硬伤（正面判定不复采）。理由：QC 概率性翻判（实测同图连审会给不同结论），而翻判成本全落在"误报→多一次重画"这一侧。复采失败时保留原判 |
| `SHORTDRAMA_CLIP_QC_ROUNDS` | 2 | 成片复核重拍轮数 |
| `SHORTDRAMA_VIDEO_QUEUE_RETRIES` | 5 | 队列满（503）退避重试次数。**非容量类的 503**（如 `model_not_found`）直接判死，不退避 |
| `SHORTDRAMA_VIDEO_SUBMIT_MIN_INTERVAL_S` | 65 | 供应商 1rpm 平铺闸门（**单条 key** 的最小提交间隔） |
| `SHORTDRAMA_VIDEO_KEY_ROTATE` | 0 | `1` = 各镜**轮流用不同 key** 提交 → 提交段按 key 数摊薄（需 `AGNES_API_KEYS` 多于 1 条）。只摊薄提交节流，不加快渲染 |
| `SHORTDRAMA_VIDEO_SUBMIT_MIN_INTERVAL_PER_KEY_S` | 同上一行 | 覆盖 per-key 间隔；未设时**实时**跟随 `SHORTDRAMA_VIDEO_SUBMIT_MIN_INTERVAL_S` |
| `SHORTDRAMA_IMAGE_WORKERS` | 0（自动 = **8**） | **并发生图**线程数：`0` = 自动 = `IMAGE_WORKERS_CAP`（当前 8）；`1` = 逐张串行（= 改造前行为，回退开关）；`>1` = 显式指定，**原样采纳**。★ **2026-09-30 起不再压到"可用 key 数"**：旧判据的直觉是从视频通道搬来的（那边 1rpm、同 key 60 秒内二次提交必 429），而图片通道 2K 档标称 80rpm，"一条 key 挂多张在途"就是它的额定工况。**低 rpm 的 key 由闸门保护，不由线程数保护**——闸门按 key 独立计时 = `60/图片rpm`（80rpm → 0.75s/条；4K 档 1rpm → 60s），线程再多也会在 `claim()` 上排队，撞不出 429 风暴。作用于**静帧首轮**、**QC 硬伤重画**（2026-09-30 起整批一次调用，此前逐镜调 ⇒ 并发恒为 1）、**定妆照四视图**——它们之间没有依赖（跨镜传的是文字「承接上一镜落点」，不是上一张图）。⛔ **不影响两处真串行依赖**：`keyframe`/`mixed` 档的首尾帧链、pack 档**视频组之间**抽真实末帧当接续锚（2026-09-28 特意串行）。真正的上界是 `min(本值, 这一批的镜数)`，设得比批数大是空转 |
| `SHORTDRAMA_MAX_REVISIONS` | 2 | 渲染被**媒体门**因"评审未通过"拦下的次数上限，**超过**即记 `review.force_passed` 放行并响亮列缺陷（2026-09-29 由 3 改为 2，用户口径「超 2 次就放行」）。★ 计数是门自己按集累计的 `review_blocks`（落盘、跨进程）；⚠️ **不是** `revision_counts` —— 真实链路上它常年为 `null`（要角色自调 `record_phase(count=True)` 才累加，而实际重派由调度器 LLM 发起，不经过 `guards.reset_from()`） |
| `SHORTDRAMA_TOKEN_BUDGET_RUN` | 3000000 | 整轮 token 熔断预算 |
| `SHORTDRAMA_CHAT_VENDOR` | agnes | 文本通道厂商（`v5/vendors.py`）。旧名 `NEWDEEP_LLM_PROVIDER` 仍兼容回落 |
| `SHORTDRAMA_V5_PROJECT` | studio | supervisor 架构绑定的项目目录 |
| `SHORTDRAMA_V5_AGENT_URL` | http://127.0.0.1:2024 | supervisor 派发子任务的目标 Agent Protocol server（**必填**，端口须与 dev server 一致） |
| `SHORTDRAMA_STUDIO_PACK` | shortdrama | supervisor / Studio 绑定的类型包 |
| `SHORTDRAMA_V5_EPISODE` | 1 | **本项目跑第几集**（多集连载）。M3 起产物路径由**每次开工注入**（`role_input` 的【本集产物路径】），故**一个 dev server 可连续跑 N 集**（`run_new_project.py --episodes 1-4`）；本变量只作起服时的默认值/调试固定用。取值优先级：env > manifest 的 `episode_index` > 1 |
| `SHORTDRAMA_SLICE_THRESHOLD` | 4000 | **按集切片注入**的阈值（字符）：全剧级目录（`plotdesigner/episodes.md`）超过它就只注入「本集 ±1 + 本卷摘要」而非全文。**给真机验证用**，生产别设 |
| `SHORTDRAMA_SLICE_SOFT` | 0 | 切片失败时的降级开关：默认（0）**响亮终止**；设 1 则「告警 + 注入全文」（应急用） |
| `SHORTDRAMA_VIDEO_MODE` | reference | 静帧当**参考图**（各镜独立，可用 `audios`）vs `keyframe`（静帧当**首帧**，支持镜间承接但拿不到 `audios`）vs `pack`（**12s 打包**：相邻同场景镜合成一条 ≤12s 的 reference 请求，接戏变单请求内部问题；提交前自动生成跨组接缝静帧预检图 `seam_preview.jpg`）。前三者官方互斥关系同上 |
| `SHORTDRAMA_NARRATION_TTS` | 0 | `1` = 启用**独立旁白音轨**（`v5/media/narration.py`：按真实时间轴 edge-tts 逐句生成 + ffmpeg amix 混入成片）。**默认关**——agnes 画外音烘焙已验证，再混 TTS 会**双声重叠**（实测）。仅 `audio_mode=narration-led` 时生效；失败不挡链（保留原声成片）|
| `SHORTDRAMA_NARRATION_VOICE` | zh-CN-YunxiNeural | 旁白音色（edge-tts 音色名，低沉男声）|
| `SHORTDRAMA_NARRATION_RATE` | +0% | 旁白语速（edge-tts 语法，如 `+10%`）|
| `SHORTDRAMA_VIDEO_PACK_MAX_GROUP` | 5 | `pack` 模式下单组最多吞几个镜（分组算法见 `media/video_plan.group_shots`；与旁路脚本 `scripts/pack_render.py --max-group` 同义） |
| `SHORTDRAMA_LOOSE_STORYBOARD` | 0 | **分镜放开模式**（2026-10-02，为 A/B 而开）。`1` = 不再向 `scenedesigner` 注入任何系统级「硬性要求」与「出片容器事实」（状态锚点/片长与镜头数/景别列写法/多人站位/身份锚点/关键拍点多角度/开场钩子/容器事实/程序体检），节奏与切镜密度交回类型包 SKILL 与分镜自己。**保留**：供应商硬墙（一次生成 4–12 秒、一次最多 5 张参考图，本就在媒体层）、三道输入门与片长 85%–130%（交付底线，关掉两臂就没有共同口径）、以及"别把上游改坏"的搬运纪律（产物路径、台词照抄、音频模式）。对照实验见 `scripts/ab_loose_storyboard.py`；默认 `0` 行为逐字不变 |
| `SHORTDRAMA_SHOTCHECK` | full | 渲前**分镜契约体检**档位（`v5/shotcheck.py`，跑在创作链派发分镜角色时）：`full` = 可数判据 + 裁判模型语义判据；`count` = 只跑可数判据（零文本调用）；`off` = 整条关掉。不合格的镜按**镜号 + 逐字原文**写成退回清单（`scenedesigner/shotcheck_ep{N}.json`）注入分镜角色，让它只改点名的那几镜 |
| `SHORTDRAMA_VIDEO_SUBMIT_TIMEOUT` | 60 | 生视频**提交**的读超时（秒）。2026-09-26 实测：pack 档多参考图组（3–4 张 16:9 静帧）提交时服务端要先拉齐素材，60 秒谈不完 —— 同轮里单图组正常完成、多图组两次都恰好卡满 60s 报 `read operation timed out` 且 `attempts=0`（没拿到 `video_id`）。**多图组建议 180**；默认 60 = 历史行为不变 |
| `SHORTDRAMA_PACK_BGM` | 1 | `pack` 档提交是否带「全程 BGM+环境音、禁止静音段」指令（官方同款模型实测可原生执行）；brief 明确禁 BGM 的项目设 0 关掉 |
| `SHORTDRAMA_IMAGE_VENDOR` | agnes | 生图厂商（`v5/vendors.py` 注册表）。未注册的名字**响亮报错**，不静默回退 |
| `SHORTDRAMA_VIDEO_VENDOR` | agnes | 生视频厂商（同上）。前端三个生成页面各自可选，随请求进该次 run 的子进程 env |
| `SHORTDRAMA_CAST_ENSURE` | 1 | `0` = 跳过参考图确定性生成（复用已有 `images/`）|
| `SHORTDRAMA_MEDIA_LOCK` | 1 | 媒体链独占锁（`0` = 关）。陈旧锁靠**心跳 mtime** 接管（TTL 见 `SHORTDRAMA_MEDIA_LOCK_TTL`，默认 300s）——**不用 PID 探活**（Windows 上会误杀）|
| `SHORTDRAMA_APPROVE_EACH_ROLE` | 0 | `1` = 启用**步级 HITL**（图运行中每次派发子代理前 interrupt），配合 `--hitl*` CLI。**默认关是硬要求**——全自动驱动脚本会挂在第一步 |
| `SHORTDRAMA_FAST` | 0 | `1` = 只关**成片抽帧复核**（静帧 QC 保留）。理由：clipqc 要抽 5 帧再判，**检查比生成贵约 10 倍**（实测 6 镜 60 秒的片：生成 1.6 分 / clipqc 18 分未完）|
| `SHORTDRAMA_CLIP_QC` | 1 | `0` = 跳过成片抽帧复核 |
| `SHORTDRAMA_CLIP_QC_MAX_REQUEUE` | 3 | 单镜**累计**重拍上限（跨进程）。到顶**保留 clip** 并记 `residual`——宁要"有瑕疵但完整"的成片，不要"永远缺镜"的空转 |
| `SHORTDRAMA_QC_RETRY_ATTEMPTS` | 5 | QC 撞限速（429）的退避重试次数。**只能退避、不能兜底**（把 429 当"无硬伤"会静默漏检）|
| `SHORTDRAMA_QC_RETRY_BASE_S` | 5 | 退避基数（秒）；实际等待 = base × 2^i |
| `SHORTDRAMA_TOKEN_BUDGET_ROLE` | 800000 | 单角色 token 熔断预算 |
| `SHORTDRAMA_STILL_FIRST` | 1 | 静帧先行：预演静帧即**生产首帧**（`0` = 退回"文生视频 + 参考图"旧模式）|
| `SHORTDRAMA_SOURCE_TURNAROUND` | 0 | 有**用户源照片**时：默认**直绑源照片**（只剩"照片→静帧"一跳，最像）；设 `1` 先生成四视图再绑正面单格（实测四视图**四个视角的脸彼此不一致**，反而丢相似度）|
| `SHORTDRAMA_KEEP_IDENTITY_WITH_PHOTO` | 0 | 有源照片时是否**仍注入那 ~220 字文本身份锚点**。默认不注入——实测同照片同镜，提示词 545→75 字后**相似度大幅提升**（那段文字里的"面部棱角清楚"等形容与照片打架）|
| `SHORTDRAMA_INLINE_UPSTREAM` | 1 | 上游产物**直接注入**下游角色的输入（`0` = 只给路径、让角色自己 `read_file`——实测后者要 8–10 轮 LLM 调用/角色）|
| `SHORTDRAMA_LEAN_PROMPT` | 0 | 视频提示词走**精简形态**（删反分屏前置 / reference 用途声明 / 类型包风格块；保留内容四段 + 台词 + 环境声 + 禁字幕）|
| `SHORTDRAMA_ASSET_GATE` | 0 | 资产完整性缺失时**硬拦**（`return blocked`）。默认只强告警不拦——存量项目多有部分缺图，直接拦会把它们全卡住 |
| `SHORTDRAMA_AGE_VARIANTS` | 1 | **多集连载的分龄变体 / 新角色补卡**：`cast` 按本集分镜的 `@名（括注）` 派生「阿旺（14岁版）」这类卡（以基础定妆照做 img2img 锁脸）与从没进过注册表的新角色卡。同一开关还管两条配套：同名多张年龄卡**拆成各自独立的表**（否则后一张覆盖前一张，全剧只剩一张不知道几岁的脸）、以及**本集默认年龄段**（后续拍的 `@名` 不带年龄时按它选表，本集出现两个年龄段则不猜、只报一行）。设 `0` ⇒ 整段跳过，行为与改造前一字不变（修的两个实测缺口见 `v5/media/variants.py` 文件头）|
| `SHORTDRAMA_VARIANTS_MIN_SHOTS` | 2 | 一个人在本集**出现在几镜**才值得给他一张定妆照。数的是镜数，不是 `@` 次数（同一镜里 `@阿旺` 可连写 5 次）|
| `SHORTDRAMA_VARIANTS_MAX_CARDS` | 6 | 每集最多派生几张（生图配额敏感）。超出按出场镜数取前 N，被弃的会打日志 |
| `SHORTDRAMA_DIALOGUE_VERBATIM_STRICT` | 0 | 对白**逐字门**在 `_input_gates` 里阻断。默认只警告——存量项目的旧 `dialogue` 产物多为改写版 |
| `SHORTDRAMA_CHAIN_TIMEOUT` | 9000 | `run_new_project.py` 传给创作链的整体超时秒数（它据此给 `scripts/drive_chain.py` 传 `--timeout`）。⚠️ `run_new_project.py` **自身不解析** `--timeout`，直接传它会被静默忽略 |
| `SHORTDRAMA_DEV_PORT` | 2024 | dev server 端口。**语义按入口分**：`run_new_project.py` 在**不设它时**从 2080-2099 自动挑空闲口（多项目并行的前提，扫描起点按项目名错开，`SHORTDRAMA_V5_AGENT_URL` 自动对齐；**设了就只用那一个，被占即响亮终止，绝不换口也不杀占口进程**）；`webchain.py` 仍固定用它 |
| `SHORTDRAMA_IMAGE_SIZE` | （空） | 生图尺寸覆盖；空 = 不传该参数，用供应商默认 |
| `SHORTDRAMA_QC_WORKERS` | 3 | 静帧 QC / clipqc 并发 worker 数。**调高会撞供应商限速** |
| `SHORTDRAMA_RECURSION_LIMIT` | 600 | `drive_chain.py` 全链递归上限 |
| `SHORTDRAMA_ROLE_RECURSION_LIMIT` | 150 | supervisor 下各角色图的递归上限（长分镜项目可调大） |
| `SHORTDRAMA_RUNTIME` | （空） | 运行时根目录覆盖（`v5/config.py` 的 `RUNTIME_ROOT`；空 = 项目根） |
| `SHORTDRAMA_SOURCE_SHEET` | auto | 四宫格设定表：`auto` = 有源照片即走 / `0` 强制关（源照片直绑）/ `1` 强制开 |
| `SHORTDRAMA_SUBAGENTS` | sync | supervisor 派发子代理模式；`async` 回退旧异步路径 |
| `SHORTDRAMA_WEB_ALLOW_NULL_ORIGIN` | 0 | `1` = web server 放行 `Origin: null` 请求（**知道代价再开**） |
| `SHORTDRAMA_WEB_ALLOW_ORIGIN` | （空） | 额外放行的 CORS Origin，逗号分隔 |
| `SHORTDRAMA_WEB_BASE` | （自动） | 媒体 URL 对外基础地址；不传用 `http://<host>:<port>` |
| `SHORTDRAMA_WEB_MANUAL_STEPS` | 0 | `1` = webchain 全手动步进（每步等人确认） |
| `SHORTDRAMA_WEB_ROOT` | `frontend/dist` | 要同源托管的前端静态目录（server `--web-root` 的 env 默认值）。没构建时**只起 API 并响亮提示**去 `npm run build` |
| `SHORTDRAMA_COMPOSE_FIT` | crop | 拼接规格统一方式：`crop` 等比放大居中裁切（竖屏推荐）/ `pad` 等比缩小补黑边 / `off` 不统一 |
| `SHORTDRAMA_COMPOSE_TRIM` | 0.15 | 拼接时每镜首尾各剪秒数（掐头去尾）；`0` = 关。取小值防切 dialogue-led 台词 |
| `SHORTDRAMA_COMPOSE_XFADE` | 0.3 | 相邻镜 xfade 叠化秒数（音频 acrossfade 同步交叉）；`0` = 关 |

> **本表是环境变量的唯一完整清单**（2026-09-18 起；由 `scripts/check_docs.py` 强制对账：
> 代码在用的 `SHORTDRAMA_*` 必须入表或入下方运维项，表内名字必须真实存在）。
> 表外还有少量纯运维项（`SHORTDRAMA_PROJECTS` / `SHORTDRAMA_MEDIA_LOCK_TTL` / `SHORTDRAMA_VIDEO_PROVIDER`＝死配置 等），语义与理由见 `v5/config.py` 的注释。

> ⚠️ 请勿把真实密钥提交进仓库；`.env` 已在 `.gitignore` 中。

## 7. 维护脚本（`scripts/`）

| 脚本 | 用途 |
|---|---|
| `check_docs.py` | **文档对账**（改结构后必跑，挂质量门）：数代码真实值（图数 / media 模块数 / craft 技法 / env 名单）对比 README+AGENTS 声明，漂移 exit 1；`--self-test` 验证检测器自身 |
| `qc_sweep.py` | 全量静帧 QC 审查（只报告不生成，`--json` 出结构化结果） |
| `reroll_list.py` | 按名单多轮重滚硬伤镜（`--names LN05,LN06` 或 `--from-qc <json>`） |
| `gen_all_stills.py` | 全量强制重生成静帧（**不跑 QC**；风格块改动后必须用它） |
| `concat_robust.py` | 稳健拼接（`--crop` 无黑边 / `--pad` 零损失） |

## 8. 测试

```bash
# 全量（推荐）
python -m unittest discover -s v5 -p 'tests_*.py' -t .
```

**1177 个用例 / 27 个测试文件**（2026-09-30 深夜实测计数），纯离线（不打网络）：`tests_flow`(188)、`tests_server`(112)、`tests_core`(80)、`tests_webmap`(81)、`tests_cast`(72)、`tests_validate`(68)、`tests_webchain`(64)、`tests_webwrite`(61)、`tests_graph`(53)、`tests_assets`(50)、`tests_guards`(57)、`tests_hitl`(42)、`tests_roles`(35)、`tests_vendors`(34)、`tests_variants`(26)、`tests_rerender_agent`(25)、`tests_sheetcheck`(22)、`tests_aigc`(13)、`tests_chatrotate`(11)、`tests_canvasout`(11)、`tests_sheet`(12)、`tests_stills`(12)、`tests_shotcheck`(16)、`tests_storyboard_fmt`(6)、`tests_reroll`(9)、`tests_devport`(8)、`tests_render_recipe`(8)。

> ⚠️ 这一行的数字**会随每次加测试而过期**（2026-09-30 就发现它从 973/16 落后到 1163/26）。
> 要准数就现跑：`python -m unittest discover -s v5 -p 'tests_*.py' -t .`。
> `scripts/check_docs.py` 只拦"机器可数"的结构漂移，**拦不到这里**。

值得留意的回归保护：

- `tests_core.py` 锁死两条反烧字铁律：提示词中不得出现「文字/字符/字幕」，也不得出现「招牌/摊位/纸张」等载体名词——**负面提法会诱发模型烧字**，这是实测结论，改动提示词时勿违反。
- `tests_flow.py::TestStillRegenConvergence` / `TestStillQcNarrowReview` 锁死**静帧 QC 的重画纪律**：未归类硬伤也必须重画（不能空转）、跨进程累计上限、只复审上一轮重画过的镜；2026-09-16 另加两条锁「**同类连续两轮判停**」与「类别变了仍重画」。
- `tests_validate.py` 锁死 brief 注入契约与音频模式契约：输出必须是合法 JSON、核心字段放不下必须报错、低价值字段先被丢弃；`dialogue-led` 零台词/占比过低必须被判违规，`silent` 出现台词同样违规，`（无声）` 类标记不得被算作台词。
- `tests_graph.py` / `tests_flow.py` 锁死编排契约：**被派发的 7 个角色**（`guards.GATE_ROLES`，不含 `director`）必须全绿才允许渲染、评审不通过按条件边回退、回退超限强制放行不死循环、`video_jobs.json` 状态机不把失败任务当完成跳过；`audio_mode` 违规必须回退 `scenedesigner`，开工前必须把模式写进 `dialogue` / `scenedesigner` 的输入。
- `tests_flow.py::TestExternalAgentLockout`（6 例）锁死**准入契约**：`--media-only` 不存在、`--resume-media` / `--stills-only` 无放行环境变量时拒绝、`--monitor` 严格只读（不改 manifest、不代建项目、发现问题只报告）。
- `tests_flow.py::TestMediaGateAtSingleEntry`（6 例）+ `TestMediaIsOutsideGraph`（3 例）锁死**媒体链唯一入口**：8 角色未齐 / 评审未过被拦、`force_passed` 放行、`stills_only` 不受 render 门约束、`rendered` 记账落盘、**输入指纹变则自动解除闩锁**；并防 `MEDIA_NODE` 旁路复活。
- `tests_flow.py::TestApprovalGates`（8 例）锁死三道审批门：**上游产物一变，批文自动作废**。
- `tests_canvasout.py`（11 例）锁死**画布导出的三条判据**：节点地址必须是**绝对**的（写成根相对时，画布与后端不同源会每格破图，实测）、资产连线**按场次带连一次**而不是逐镜连（一条"每镜都在"的场景资产曾拉出 30 根线）、缺 `stills.json` / `video_jobs.json` / 成片时**必须进 `warnings`** 且不留假节点。两条"旧病"装回去都验过会红。

## 9. 已知局限

- **视觉 QC 对"小字"概率性漏报**（实测满墙汉字仍判通过；2026-09-16 又确认一例：分镜明确禁止音符，静帧谱面仍画出可读五线谱、QC 放行）。批量生产时不要只信 QC 回执，配合 `qc_sweep.py` 或人工拼图扫查。这也是 `style-watch` **只记不改**、以及 AGENTS.md 要求"新包首跑必须抽帧"的原因——**QC 只能守住它注意到的东西**。
- ★ **「复采翻判」会给硬伤开一条放行通道**（2026-09-17，已在两个项目上见到）：静帧 QC 的
  **复采（二次采样）** 判定与首判不一致时，`首次判 P0 → 复采翻判"干净" → **不重画**`。
  实测两例：`jade-fish` LN15（首判"分屏/多格"→ 翻判干净 → 出厂）、
  `felt-bach-serial` LN07/LN09（首判"谱面可读五线谱/音符 = 可读字符，P0"→ 翻判干净 → 出厂，
  而该包禁忌第 1 条明确禁止任何可读字符）。⇒ 复采本意是"减小误报"，实际也**放过了真硬伤**。
  **人眼抽帧仍是唯一可靠手段。**
- **`location` 类资产的参考图只在宽景绑**（2026-09-23 起：全景 / 远景 / 大全景 / 空镜；中近景与特写不绑 —— 场景图自带固定机位，绑进近景会把构图拉回大 Wide）。⇒ **中近景的环境材质与比例仍只由 `style-block.md` 的文字承担**；而角色参考图是"浅灰底、干净的拟真手办"，会把整张画面往**拟真**方向带。实测 wool-felt 项目出现「角色是毛毡、环境却是真木/真金属/真油画」，与本包"所有表面都是羊毛毡"的风格块要求不符 —— **这是中近景仍存在的风险，别指望场景图解决它**。
- **keyframe 输出高度不统一**（同一批出现过 704×960 / 992 / 1024 / 1056 / 1088 五种）。`compose.concat` 的 `-c copy` 直接拼会导致画面尺寸跳动，成片请用 `concat_robust.py`。
- **FIDELITY 门是"忠实度"门不是"质量"门**：它校验产物有没有覆盖 brief 的 must_have，**不校验 brief 本身好不好**。烂 brief 被忠实执行 = 烂成片，门照样过——质量责任仍在调用方。
- 连续镜串行等待是主要耗时来源。`cut` 镜本身无依赖，但默认的串行链仍按序推进；开 `SHORTDRAMA_TAIL_PREGEN=1` 可解除串行依赖、让视频整批平铺（**代价**：承接从"上一镜真实尾帧"降级为"预生成落幅图"，视频运动的随机偏差会计入成片）。

## 10. 目录导览

```
v5/
├── orchestrator.py         # **supervisor 架构**（director 主 agent + 7 角色图 · 唯一创作链）
├── roles.py                # 角色装配层（SKILL 装配 / 开工契约 / FS_TOOLS 白名单 / 音频守卫）
├── series.py               # **媒体链入口**（--resume-media / --monitor / 审批门 / resume 闸门）
├── langgraph.json          # 图注册（9 图：supervisor + role_* ×7 + media_rerender）
├── decision.py             # reviewer 结构化判定解析 / 回退目标路由
├── guards.py               # 记账 / 物化对账 / TokenBreaker / media_gate
├── validate.py             # brief 智能截断 / brief 完备性 / 产物忠实度 / 分镜契约
├── config.py  llm.py       # 配置与 LLM 供应商
├── media/                  # 静态画面先行管线（25 个模块，不含 __init__）
│   ├── pipeline.py         #   **媒体链唯一入口**（media_gate + 审批门 + 记账都收在这里）
│   ├── jobs.py             #   video_jobs 显式状态机
│   ├── storyboard.py       #   分镜解析
│   ├── relations.py        #   镜间关系（continuous / match / cut）
│   ├── style.py            #   类型包风格块
│   ├── cast.py             #   参考图确定性预生成（角色单格正面像 / 资产图）
│   ├── assets.py           #   资产绑定 / 文本身份锚点
│   ├── variants.py         #   从本集分镜的 `@名（括注）` 派生分龄变体卡与漏登记的新角色卡
│   ├── stills.py  qc.py    #   静帧生成 / 硬伤 QC
│   ├── video.py  clipqc.py #   图生视频 / 成片抽帧复核
│   ├── compose.py          #   ffmpeg 拼接
│   ├── approvals.py        #   三道审批门 + 产物指纹 + version-aware 闩锁
│   ├── model_profile.py    #   模型怪癖档案（按模型归档，组装器零改动）
│   ├── prompt.py           #   六段式提示词组装 + 反烧字清洗
│   └── providers.py  scaffold.py
├── canvasout.py            # **画布导出**：按盘上事实摆成 Infinite Atelier 的画布（确定性、零配额）
├── aigc.py                 # **画布模型代理**：OpenAI 形状 → 后端密钥池转发（比例折算在这）
└── skills/packs/<包名>/    # 类型包：角色 SKILL.md + pack.json + style-block.md
scripts/                    # 运维脚本（人用）
projects/                   # 运行时产出（已 gitignore）
frontend/                   # React 工作台源码（`npm run build` → `frontend/dist`，由 `/` 同源托管）
atelier/                    # **vendored 的画布应用**（Infinite Atelier，React+antd+tailwind）
│                             #   `MSYS_NO_PATHCONV=1 VITE_BASE=/atelier/ npm run build`
│                             #   → `atelier/dist`，由 `/atelier` 同源挂载（dist 与 node_modules 不入库）
docs/pavo/                  # 外部平台对接的取证材料（端点全集 / 模型档位 / 直调客户端）
AGENTS.md                   # 外部 Agent 调用规范（权威）
```

**前端两棵树**：`frontend/`（工作台）与 `atelier/`（画布）各自构建、由 `v5/server.py` 挂到
同一个 origin 下（`/` 与 `/atelier`）。旧的原生 JS `web/` 已于 2026-09-30 退役删除。
⚠️ 在 Git Bash 里构建 `atelier/` **必须带 `MSYS_NO_PATHCONV=1`** —— 否则 `/atelier/`
会被 MSYS 当成 Unix 路径改写成 `/Program Files/Git/atelier/`，产物里的资源地址全部 404（实测黑屏）。

### 画布的模型请求走同源代理（`v5/aigc.py`）

画布**不直连**模型。它按 OpenAI 形状请求同源后端，后端用 `.env` 的密钥池转发：

| 路由 | 状态 |
|---|---|
| `POST /v1/images/generations` | ✅ 已通（真出图） |
| `POST /v1/images/edits`（带参考图） | ✅ 已通（请求体是 **JSON + data URI**，见下） |
| `POST /v1/chat/completions` | ✅ 已通（`stream:true` 回 SSE；是**一次性完整回答切成两块**，不是逐 token） |
| `POST /v1/videos` + `GET /v1/videos/{id}` | ✅ 已通（**必须挂一张参考图**，见下） |
| `GET /v1/models` | ✅ 列后端真能服务的三条（生图 / 文本 / 生视频） |
| 音频 | ⛔ 不列也不接（列出来只会让按钮"能点但永远转圈"） |

★ **为什么要这层**：直连要在浏览器里再填一份密钥，且绕过 `providers` 的密钥池与限速。
走代理后密钥只有 `.env` 那一份，出图出片仍调 `providers.gen_image` / `submit_video`
（与媒体链同一条路，不另写一份调用逻辑）。
`atelier/` 的默认渠道已改成同源地址 + 占位密钥（它的"就绪"判据要求密钥非空，代理不看这个值），
⇒ 开箱不用配置。
⚠️ 但**别说成"配额记账照旧生效"**：`video_quota.json` 在本仓库**不存在、`v5/` 也没有任何代码写它**
（AGENTS.md 把它列进黑名单属于文档超前于代码）。真正继承到的只有密钥与限速两件事。

★ **多 key 轮换是代理自己做的**（`gen_with_fallback`）：媒体链的轮换由调用方显式传 key，
`providers` 自己不换 ⇒ 代理若只传 `key=None` 就永远用第一条，"用上多 key"会是假话。
实测：撞 429 逐条试到能用、全部用完才抛。

★ **参考图走 data URI 而不是 multipart**：venv **没装** `python-multipart`，FastAPI 收不了
表单式文件上传；与其为省一个依赖手写 multipart 解析器，不如把 vendored 画布那侧改成发
data URI（只改 `atelier/src/services/api/{image,video}.ts` 各一处）。实测供应商**收 data URI**：
真静帧转 base64 塞进 `extra_body.image` 返回 200，结果 URL 落在 `images/i2i/` 下
（说明确实按图生图处理，不是当文生图糊弄）。蒙版**明确报错不支持** —— 后端生图接口没有
mask 参数，静默丢掉会出「看起来按蒙版改了、其实没有」的图。

★ **生视频必须有参考图**：供应商只有 `keyframe`（要首帧）与 `reference`（要参考图）两档，
**没有纯文字生视频**。⚠️ `providers.submit_video` 的报错原先写着「仅 text/keyframe/reference」
而代码里**没有 text 分支** —— 照那句写 `mode="text"` 实测被拒，文案已改回与代码一致。
本项目本来静帧先行 ⇒ 画布上的正确用法是**把某一镜的静帧挂成参考图再生成视频**。

★ **查询必须回用提交时那条 key**（跨 key 查会查不到，`providers` 文档写死）。代理用进程内
映射记 `video_id → key`；服务重启后映射丢了 ⇒ 按池里的 key 逐条试，不直接报"任务不存在"。

★ **代理顺手修掉了"假开关"**：实测 Agnes **忽略**像素串 `size`（画布发 `1024x1792`、
后端发 `1K`+`ratio:"9:16"`，两边出图完全一样 736×1312）。`aigc.ratio_from_size` 把任意
`size` 折到官方比例档位（`config.py` 记着的 8 档）再发出去 —— 实测画布选竖屏得
736×1312、选横屏得 1312×736，比例选择器**真的**生效了。

**依赖**：ffmpeg / ffprobe 需在 PATH 中（拼接与帧抽取用）。

## 11. Studio / dev server（supervisor 的唯一入口）

```bash
SHORTDRAMA_V5_PROJECT=<项目名> .venv/Scripts/langgraph.exe dev \
    --config v5/langgraph.json --host 127.0.0.1 --port 2024 --no-browser
```

- 注册 **9 张图**：`supervisor` + `role_*` ×7 + `media_rerender`（角色图由 supervisor 经 Agent
  Protocol 调用，故 `AsyncSubAgent.url` 必填，默认指向本 dev：`http://127.0.0.1:2024`）。
- **换项目必须重启**（`SHORTDRAMA_V5_PROJECT` 是起服时绑定的）；**换集不必**——
  集级产物路径由**每次开工注入**（`roles.role_input` 的【本集产物路径】），
  所以一个 dev server 可以连续跑第 1..N 集（`run_new_project.py --episodes 1-3`）。
  `SHORTDRAMA_V5_EPISODE` 只作起服默认值/调试固定用。
- 驱动方式：Studio UI、Python SDK（`langgraph_sdk.get_client`）或前端。
  **对话式推进**——派发后 supervisor 结束回合，你每轮说"继续"，它 check 一次再推进。
- 诊断：`python scripts/diag_orch.py <项目> --url http://127.0.0.1:2024`
  （流式打印事件 + `async_tasks` channel——派发是否真实注册的第一手证据）。

> 媒体链不需要 dev：`python -m v5.series <项目> --resume-media` 独立运行。

