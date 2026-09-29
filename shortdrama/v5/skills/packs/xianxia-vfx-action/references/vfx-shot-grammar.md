# 蒸馏依据：两条 Ref2VA 提示词 → xianxia-vfx-action 的镜头语法

> ⚠️ 本文件**不注入任何提示词**，也不被 `style.load()` 读取（只有 `style-block.md` 会被
> 逐镜注入）。它是**依据档案**：判据从哪来、为什么这么定、什么时候该改。
> 要改 `scenedesigner` / `reviewer` 的判据，先读这里。

## 0. 来源

用户提供同一支 30 秒片的两条分段提示词（前 15 秒 / 后 15 秒），走的是 Ref2VA
（参考图 → 视频+音频）范式：`@image1/@image2` 定两名剑修外形、`@image3` 定场景与首帧、
第二条还额外吃 `@video1` + `@videoaudio1` 做逐帧接续。

**只取美学与镜头语法**（渲染基准 / 能量色编码 / 剑罡形态 / 运镜 / 接续 / 音景），
不复刻其剧情、角色名、具体编排与分镜时刻。本包的 demo 项目是原创同名题材。

## 1. 为什么本包的重心在「分镜」而不是「风格块」

对照实验式的读法能看出：两条提示词里**风格只有 1 行**（`Style: UE5-Niagara …`），
而**约束与语法占了全文 80%**。也就是说参考片的可复制性几乎全在
"每镜怎么写"，不在"整体什么调子"。

本管线的风格块有**长度纪律**（>200 字会压掉画面内容；现有包注入正文中文 135–351 字，
见 `shortdrama` / `chinese-style-short-drama` 的 `style-block.md` 注释）。所以：

| 内容 | 落在哪 | 为什么 |
|---|---|---|
| 渲染基准、能量色、板状剑罡的一句话形态 | `style-block.md`（逐镜注入，341 字符） | 每一镜都要重述，且必须与画幅/帧率无关 |
| 六条镜头语法、运镜库、音景词库 | `scenedesigner/SKILL.md` → 落进「画面描述」「视觉风格」「音效」三列 | 只有写进单元格才会进该镜提示词 |
| 判定与阻断 | `reviewer/SKILL.md`（链上）+ `brief.禁忌`（静帧 QC 的 P0 判据来源） | 媒体层不看 SKILL |
| 本档案 | `references/`（人按需读） | 不占注入预算 |

★ **但注意**：SKILL 的执行率不是 100%。2026-09-25 brawl 实测——同一批格式要求，
只写在包 SKILL 里执行率 **1/18**，写进 `brief.json` 是 **20/20**。所以**首次用本包的
项目必须把六条语法压成 5–8 条写进 brief 的 `分镜格式硬要求`**（见 §5）。

## 2. 逐条对照表（源文 → 规则 → 执行点）

| # | 源文里的写法 | 蒸馏出的规则 | 落在本包哪里 | 为什么这样搬 |
|---|---|---|---|---|
| 1 | `@image1 defines S1 appearance (long brown hair with bangs, gold floral hairpin with purple gem chains, white-gold embroidered sheer-sleeve gown)` | 身份 = **一句可逐字复制的锚点串** | worldbuilder「锚点串」→ scenedesigner 每镜**第一拍** | 参考片把外形钉在 `@image` 上；本管线**没有** `@image`，等价物是「参考图 + 静帧只取第一拍」⇒ 锚点必须**写在第一拍**，否则静帧提示词里一个服装词都不剩（brawl 实测同组三张静帧两套外套） |
| 2 | `wielding her violet crystal longsword whose blade glow burns dark-crimson` / `no pink or magenta glow anywhere` | **能量色编码**：一人一色，色随形走（剑芒/雷光/图腾/化身/地面反馈全同色） | director 的「能量色分配表」→ worldbuilder「能量色绑定」→ 分镜两列 → reviewer 第 5 条 | 这是参考片最强的一条辨识度来源，且**双人同帧**时两色互撞才形成"在对打"的读感。英文色名（`dark-crimson`）作为**锚词保留**：技术色名对渲染模型的指向性比中文近义色稳 |
| 3 | `Exactly two fighters in every frame; afterimages translucent only` | **人数律**：每帧恰好两人；残影一律半透明 | reviewer 第 7 条 + `brief.禁忌` | 静帧 QC 有"人数清点"（多出一个一律 P0，2026-09-26 补强）。本包天然两人，**禁忌不能照抄单主角包的"无第二张清晰人脸"**；同时要防"残影被判成分身" ⇒ 分镜里必须显式写"半透明拖尾"（见 §4 已知冲突） |
| 4 | `Every ranged attack must be a colossal complete blade-slab sword-qi … never a crescent arc, never a beam, never a disc; pillars and ring cracks only as ground feedback` | **板状剑罡形态律** + 地面反馈是"结果"不是"攻击" | 风格块一句 + scenedesigner 第 3 条 + reviewer 第 6 条（正向判、反向不写进单元格） | 这是**模型最容易自己发明**的形态（默认画月牙弧/激光束）。★ 关键处理：源文用**否定句**，本包**不在提示词里写否定**（负面提法诱发烧字/反效果，AGENTS 硬约束 6）⇒ 否定清单只留在 reviewer 判据里，分镜只写正向形 |
| 5 | `Frame-Zero State: S1 stands alone…, sword NOT in hand yet; axis 180 degrees; no motion yet` | **起始态声明**并进第 1 镜第 1 拍 | scenedesigner 第 1 条 | 模型对打斗开场会自印"已举剑对冲"的通用起手式。"剑还没到手"这个反差是整段戏的力量来源，且它随第一拍直接成为**开场静帧** |
| 6 | `At 00:02.200, …` 绝对时间码 | ⚠️ **不能照搬**。翻译成管线的 `0-3秒：` / `0-4s：`，**并且取整成整数秒** | scenedesigner「节拍语法」+ reviewer 第 2 条**阻断** | 解析正则 `storyboard._BEAT_RE` 只认 `数字-数字 + 秒\|s + 冒号`。`At 00:02.200` **无连字符**→ 整镜被当散文，**静默**失去：① 静帧第一拍切割 ② pack 档全局时间轴重映射 ③ 门的节拍自洽校验。★ **首跑实测（2026-09-26）补一刀**：源文时间轴是**小数全局轴**（`00:11.000`/`00:11.400`），模型翻译时会保留 `.5` 刻度——而正则两侧都是 `(\d+)`，`1.5-3秒：` 被切成 **`5-3秒：`** 假拍，门报出费解的「第一节拍未从 0 秒开始（起点 5 秒）」；那次 6 镜里 3 镜中招、媒体链被拦未启动（好在**零付费生成**）。⇒ 翻译必须**同时**做两件事：**重置为镜内 0 起 + 取整到整数秒** |
| 7 | `the camera shakes strongly for 0.3s with red overexposure at edges` / `camera rises from macro of S2's blade tip to extreme low angle` / `sonic-speed handheld chase` / `wide long-range tracking with lateral orbit and vertical rise` / `shot on a high-speed camera for a 0.3s orbit` | **八式运镜库**（含"不要每镜都在动"的重音纪律） | scenedesigner 第 5 条（对应「角度」+「运镜」两列） | 景别/角度/运镜是本管线**独立三列**，比散文里的运镜描述更可解析；且 `qc.review_shot_type` 会做**景别跨档校验**——所以运镜要落到列里，不能只写在描述里 |
| 8 | `TAIL LOCK: final frame keeps camera at mid-height lateral orbit with … nothing settling, nothing freezing` + `Continuing frame-exactly from the guided tail with match-cut inheritance` | **尾镜接续**——但**范围限定为"每个生成组的末镜"** | scenedesigner 第 6 条 + reviewer 第 9 条 advisory | 参考片分两段，段尾必须留未收矢量给下一段接。本管线 pack 档**也是**一次生成多镜、组间独立，同构问题；但**全片末镜要收势**（brief 的 `结局` 要一个可定格画面），不加这层限定会让每一镜都被要求"不收起" ⇒ 成片抖成一团。组间衔接管线**已有**机制（`build_pack_prompt` 把上一组末镜静帧作为 `<Picture n+1>` 附上），所以**不要**在分镜里写 match-cut 指令 |
| 9 | `S1 dissolves into a vermilion phoenix of dark-crimson flame … At 00:12.000 …` + `No feather wings … except the phoenix and lightning-tiger avatars between 00:11.000 and 00:12.000` | **化身有时间窗**（哪镜出场、哪镜收掉），且必须是**半透明能量构造体** | scenedesigner 第 6 条邻近 + reviewer 第 7 条 | 没有时间窗 ⇒ 模型让兽形常驻、与人物抢主体；"no fur" 是渲染语言一致性（有毛的白虎 = 写实材质，会把整片拉离游戏 CG 层）。⚠️ 化身在静帧 QC 里可能被"人数清点"误判为多出一个 → 属已知冲突（§4） |
| 10 | `overall_soundscape: Nobody speaks… Bright shrine bed of cloud sea wind, waterfall roar, crane cries, wind chime jingles, foley of sword-qi air cuts, blade snatches, …` + `non_diegetic_music: None` | **音效列 = 灵魂列**（foley 词库 + 动态弧），`audio_mode: silent` | pack.json 音频模式 + scenedesigner「音效列」+ reviewer 第 15 条 | 参考片的"没有音乐、没有人说话"是**风格决定**，不是省事。silent 模式下分镜「对白」列每格必须 `（无声，环境音）`——判定是 `startswith("（无声")`，写别的会触发回退 |
| 11 | `missed blades smash walkways and roofs, tile shards streaming` / `terrace craters, colonnades burst, wide deep gorge plowed` | **环境是代价**：力量感记在场景上，不记在人体（`wounds shallow grazes only`） | assetdesigner「可破坏物清单」+ director 连续性 + reviewer 第 14 条 | 场景卡没有可破坏物，分镜就只能砸空气 ⇒ 本包场景卡**四件套**里多出一条"可破坏物清单"，这是其他包没有的。同时"见血即阻"保住了 QC 的「血腥」硬伤判据不被削弱 |
| 12 | `constraints: No subtitles, watermarks, UI, modern elements` | 交给管线的画幅/文字治理 + `brief.禁忌` | 不写进风格块、不写进单元格 | 源文的否定式约束是为**平台**写的，不是为**提示词**写的。搬进提示词会反噬（见 §4 与 AGENTS 硬约束 6） |

## 3. 有意**没**搬进来的东西

- **`Style` 那一整行的字面英译**：`16:9`、`60fps` 由 API 参数决定，写进提示词只与参数
  打架（`prompt._clean_prompt` 还会把分镜自带的 `Ratio: x:y` 正则清掉）。保留的是
  "横屏宽画幅 / 高帧率运动清晰"这类**构图与运动词**。
- **具体的 6+6 镜编排与时刻表**（`00:00 / 00:02.2 / 00:03.4 / 00:05 / 00:07 / 00:11 / 00:12`）：
  那是**这一支片**的剪辑，不是可复用资产。抽出来的是它背后的两条律：
  **升级链**（起手→互搏→化身→终招）与**密度**（对撞段短镜、大招段长镜）。
- **角色名与兵器专名**（`S1/S2`、`violet crystal longsword`）：换成占位符与结构要求。
- **"no slow-motion" 的全称否定**：源文自己就允许一次 bullet-time，所以规则是
  「全片至多一次」，不是「禁慢动作」。

## 4. 已知冲突与失效模式（首跑必看）

| 冲突 | 现状 | 若真出问题的处置 |
|---|---|---|
| `cast._char_prompt` / `_asset_prompt` **硬编码**「写实短剧质感」（`cast.py:587` / `:706`），与本包"非真人实拍的高精度 CG"相反 | 缓解 = 角色卡外貌段**开头**自带渲染声明（worldbuilder 契约第 1 条）。它拼在硬编码词之前 | 静帧仍出成真人实拍 → 先 `brief.visual-style` 写死项目级期望（`style.style_watch_spec` 项目优先于包级，QC 才报得准）；再不行把 `still-refs` 改 `false` + `--rerender <全部镜号> --from still` |
| `qc.HARD_KEYS` 含「重影 / 分身 / 两个一模一样」，而本包契约**允许半透明残影**；兽形化身可能被"人数清点"数成多一个 | 靠 `qc.review` 把**本镜分镜原文**喂给 QC（"它明确要求的内容不算硬伤"）兜住 ⇒ **前提是分镜第一拍写了"半透明拖尾、非实体分身"** | 实测出现"残影被判 P0 → 反复重画" → `hard-keys-drop: ["重影"]`（**只 drop 这一条**，别连「分身/克隆」一起删） |
| 「竖屏构图」在 pack 档提示词尾句里**硬编码**（`prompt.py:1477` / `pack_render.py:167`） | 2026-09-26 已改为跟随 `config.ASPECT_RATIO`（默认 9:16 行为不变）。横屏项目仍**必须**同时设两个 env，见下 | 若忘了设 `SHORTDRAMA_STILL_RATIO`，静帧是 3:4 而视频是 16:9 —— 表现为构图被上下挤或两侧补黑 |
| 画幅**只能**用 env 表达：分镜正文自带 `Ratio: 16:9` 会被清洗 | `SHORTDRAMA_ASPECT=16:9` **且** `SHORTDRAMA_STILL_RATIO=16:9`（默认值分别是 `9:16` 与 `3:4`） | 两值不一致 ⇒ 静帧与成片画幅不符 |
| `pack.json` 的 `default-audio-mode` / `aspect-ratio` / `shot-duration` / `trigger-words` / `roles` **代码全都不消费** | 已逐条在字段自己的 `*-note` 里写明"只供人读/前端展示" | —— 音频模式**只能**在 `brief.json` 里定 |

## 4.5 ★ 首跑实测回填（2026-09-26，xianxia-duel-demo-0926）

第一轮 6 镜 30 秒的分镜**语法全对**（整数节拍、锚点串逐字、起始态"剑不在手"、
must_have 四幕 100% 覆盖、silent 对白列 6/6 合规、三个空间、板状剑罡、两色同框），
reviewer `pass`、输入门全过。烧掉 6 张静帧 + 1 组视频后，出片判 `incomplete`。
**三条真缺陷全部落在包契约上，而且都是"日志全绿、画面不对"那一类**：

| # | 症状 | 根因（不是模型的错） | 已做的修法 |
|---|---|---|---|
| 1 | 提示词明写「成片金顶塔檐铺向云海」，画面是**地面庭院**，云海双塔全程没出现；LN01 只绑了 2 张角色图、**0 张场景图** | 我在 assetdesigner 的模板里写 `## 资产卡：<名>` + 一行 `- type：location`。而 `cast._KIND_TYPE` 是**按标题关键词**判类型的（`场景卡`→location、`资产卡`→**prop**），那行 `type` 是无效装饰 → 三个空间全成 prop → `ensure` 不出环境空镜、`bind()` 宽景不绑场景图。且它**没产 `assets.contract.json`**（结构化清单才是第一来源，缺了才正则解析散文、**失配不报错**） | assetdesigner 加「标题关键词就是 type 唯一判据」节 + **两份产物**模板（`## 场景卡：` / `## 道具卡：` + contract JSON）；reviewer 第 10 条改为**场景登记门**（标题错 / 缺 JSON = 阻断）；brief 加同名硬要求 |
| 2 | 角色参考图**是对的**（白金色刺绣长裙、齐刘海、3D CG 质感），但静帧里绛雪穿成**大红**、沧月穿成**深蓝** | 我在风格块写「每名战斗者独占一种能量色：**一方暗赤**……一方深蓝白」——字面上把**人**说成了能量色，模型连布料一起染色。参考图打不过这一句 | 风格块改成**分层声明**：「能量色只绑在兵刃与光效上……两人的裙装保持各自布料本色，布料是哑光织料、只有剑与光是能量」；worldbuilder 锚点串把布料写在先、兵刃与色写在末；brief 加同名硬要求 |
| 3 | 化身镜（LN04）画成**三人持剑合影**，凤凰/白虎全无 | 我自己两条律打架：「每镜第一拍写全两名角色锚点」撞上「化作凤凰/白虎」→ 两个锚点串 + 两只兽，模型数出三个主体 | scenedesigner 加「**化身镜的例外**」：第一拍改写形态串 + 显式声明「没有站立的人形」；reviewer 第 7 条把"化身镜仍写两人锚点"列为阻断 |
| 4 | pack02（3 图）/ pack03（2 图）提交各两次都恰好卡满 60 秒报 `read operation timed out`，`attempts=0`、没拿到 `video_id`；单图的 pack01 正常完成 | `providers.submit_video` 的读超时**写死 60 秒**，而多参考图提交要等服务端把素材拉齐 | 新增 `SHORTDRAMA_VIDEO_SUBMIT_TIMEOUT`（默认 60 = 行为不变），多图组设 180。见 README §6 / AGENTS 环境变量表 |

**没被证实为缺陷的一条**：`hard-keys` 与残影/化身的对打（§4 第 2 行担心的那件事）**没有发生**——
QC 确实把残影与化身当内容读了（分镜原文喂进 QC 那道兜底起效），报的是服装与场景类
问题。所以 v0.1.0 继续**不动**硬伤词表。

**一条被推翻的预判**：我担心 `cast._char_prompt` 硬编码的「写实短剧质感」会把角色参考图
拉成真人实拍 —— 实测**没有**，参考图是干净的 3D CG 质感。`still-refs: true` 维持。

## 4.6 第二轮实测（同日，v2）：§4.5 的三条里两条成立、一条不够

第二轮成片出来了（**30.4s / 1280×720**，三组打包）。逐条复核：

| §4.5 的修法 | 第二轮结果 |
|---|---|
| 场景卡标题 + contract JSON | **登记与生成都对了**（3 张 location 图入盘），但**仍一张都没绑进静帧** —— 见下方"绑定层"那条，这是我第一轮报告里说错的地方 |
| 能量色与衣装分层 | **成立**。绛雪回到白金色裙、沧月薰衣草紫粉，暗赤／深蓝白只出现在剑与雷光上 |
| 化身镜改措辞 | **不成立，且比第一轮更退一步**：三人合影治好了，兽形整个没出现，画成两个静态对峙人像 |

### 真正的杠杆在**绑定层**，不在措辞层（两条机制，都已改代码）

1. **宽景镜的场景图绑定**——当年实测到：`assets.hits_for_shot` **只在「@ 一个都没命中」时**
   才回退按 keywords 匹配资产名 ⇒ 本包每镜都 `@` 了两个角色 → 「场景」列的名字**永不参与匹配**
   → `scene_pick` 拿不到 location → 场景图 0 张绑定。**跨包通用陷阱**（已写进 AGENTS 绑定规则）。
   对照实验：同一份分镜只加 `@场景名`，**全景镜 2 张 → 3 张**，中景仍 2 张（设计上只有宽景绑场景）。
   ⛔ **2026-09-28 起这条改由代码做**：宽景镜**无条件**按「场景」列绑 location
   （回归测试 `tests_assets.test_wide_shot_binds_scene_column_without_at_mention`）。
   ⇒ `@场景名` 那个契约要求**已作废**（2026-09-29 从 scenedesigner SKILL 删掉），
   reviewer 也不许再据此阻断。本包为它连跑过四轮创作链，其中一轮还编造了阻断理由。
2. **`【无人像】` 标记**（`storyboard.NO_HUMAN_MARKS`）。为什么必须靠代码：实测把
   `@绛雪/@沧月` 换成"那具凤凰光构／那具白虎光构"后，**角色补漏照样把两张人物设定表
   捞回来**，提示词于是同时要求「锁定该角色的长相、发型、体型与服装形制」与
   「没有站立的人形」——前者更具体，所以画成两个人。标记让该镜跳过 character 类参考图、
   `cast_counts` 记 0、`person_directive` **不注入人数声明**（不走空镜声明，因为
   `EMPTY_SCENE` 会说"环境静物"，把爆炸中的能量兽形抹平）。测试：`tests_assets.TestNoHumanMark`。

### 人数过冲：仓库定稿律在本包不成立

双人镜绑 2 张人脸 + 自动注入「画面中共有 2 个人物，每个人物只出现一次」，
**仍画成 3 人**（LN02 / LN06）。2026-09-15 那条「双人镜喂 3 张正好两个人」的定稿律，
在**打斗构图 + 双人脸**下失效。⇒ 契约改成：**首选过肩／侧后单人**（本镜只 @ 一人），
**次选复合镜正反打**（每子镜只 @ 一人），只有"必须两张脸"的定格镜才同时 @ 两人。

配套代码修复：**「人数 / 主体复制」类硬伤豁免复采翻判**（`qc.COUNT_ISSUE_KEYS` +
`qc.is_count_issue`，接线在 `pipeline` 的「③ 负面判定复采确认」）。
本轮这两镜都是**首轮 QC 明确抓到"三名女性"、复采一次翻判成"干净"就放行**——
`QC_CONFIRM_NEGATIVE` 的"误报比漏报贵"偏置对字幕类成立，对人数类方向相反
（多一个人会顺着 静帧→视频→整组素材 一路带下去）。测试：`tests_core.TestCountIssueExemptFromRecheck`。

### 第四轮（v4，12:11 出片 29.7s）：「只 @ 一人」升阻断 ⇒ 人数收敛

把 reviewer 第 10b 条从 advisory 升为**阻断** + brief 同步成硬要求之后，
`scenedesigner` 真的改了拍法：LN02 写成**复合镜正反打**（子镜 1 过肩拍 @绛雪、
子镜 2 反打 @沧月，每个子镜只 @ 一人），reviewer 逐镜查了这条并给出通过理由。
成片 12 帧抽样（每 2.5 秒一帧）**每一帧都恰好两人**；同时起始态"双手空着、掌心聚光"、
鹤群惊起、板状剑罡、双剑相交两色光环、拉远收势全部到位。
⇒ **这条律要靠 reviewer 阻断 + brief 双写才落地**，advisory 档等于没写。

⚠️ 顺手记一条**我自己犯过的检查错误**：判断"本镜是否同时 @ 两人"不能数
`'@绛雪' in visual and '@沧月' in visual` —— 复合镜的两个子镜各自 @ 一人，
整格里两个名字都在，会被误判成违规。要按**子镜**切分再数。

静帧侧的账（`still_requeue_tally`）：LN01/LN02/LN03/LN04 各重画 2 次，
LN01/LN02/LN03 达 `SHORTDRAMA_STILL_QC_MAX_REGEN` 上限仍带伤。
1C 那道豁免**确实生效**（LN02 报「主体被复制：两个几乎一模一样的 @沧月」→ 命中判据 → 重画），
但成片人数是对的 —— **静帧判据与视频产物不是一回事**，静帧只是首帧。

### 仍未解决 / 待观察

- **化身镜仍未收敛——三种写法三种症状**（这是本包目前唯一的硬缺口）：
  ① 同时 @ 两人 + 兽形 → **三人合影**；② 改措辞去 @名 → **两个静态人像**（角色补漏把人脸
  参考图捞回来了）；③ 打 `【无人像】` 摘掉人脸 → **两人腾空对撞，兽形依旧没出现**
  （静帧 QC 报「画面主体变成了两个手持法术/兵器的人」）。
  ⇒ 摘掉人脸只解决"不画人"的一半；**没有人脸锚时模型在古装语境下自己补人**。
  下一步若要攻这条，方向是 ④「人形被光体包裹的过渡态」起手（保留一个 @名 + 光壳描述），
  把兽形交给视频演化——**未实测**。
- 部分镜偏写实照片质感（`style-watch` 报 P1，不处置）。
- 起手镜「剑不在手」在 LN01 没被执行（静帧里两人都持剑）。属模型侧对"未持械"的弱响应，
  暂不加判据。

## 5. 新项目开工模板（把六条语法压进 brief）

`分镜格式硬要求` 建议照这个骨架写（本包 demo 用的就是它，六条）：

1. 每镜第一拍**逐字复制**两名在场角色的锚点串（`@名（发型发饰、衣装、兵刃与能量色）`），
   并写明兵刃**在不在手**；后续拍用「她／对方」，**不重复** `@名（衣装）`；
2. 镜内节拍用 `0-3秒：…；3-6秒：…`（从 0 起、首尾相接、终于本镜时长，**两端只能是整数**），
   **禁写** `At 00:02.200` 这类时间码，**也禁写** `0-1.5秒：` 这类小数刻度（会被切成假拍）；
3. 远程攻击只写一种形态：**三倍施放者身宽的板状剑罡**（边沿与剑尖清晰）；
   能量柱与环形地裂只作为**握剑引爆／命中瞬间的地面反馈**；
4. 每名角色**全片一种能量色**（暗赤 dark-crimson / 深蓝白 deep-blue-white），
   双人对峙镜**两个色都要出现**；
5. 每镜写**身体朝向 + 视线落点**（落在画面内的 @谁 或道具上，禁"看向镜头""瞟向画外"）；
6. 每个生成组（相邻同场景）最后一镜的「落幅」写成**仍在运动**的状态；全片最后一镜收势。

## 6. 验收：怎么确认"包生效了"

⚠️ **「日志全绿 + 有 mp4」不等于包生效**（AGENTS 明写）。新包首跑必做三件事：

1. 抽一镜看 `media/ep1/stills.json` 的 `prompt`：是否含本包特征词
   （`板状剑罡` / `dark-crimson` / `深蓝白` / `高精度游戏 CG`）——**缺词就是没生效**；
2. 抽「对白」列：silent 下是否每格 `（无声，环境音）`；
3. 成片**逐组抽帧**人眼看（不要只看拼接处），并与本档案 §2 的规则逐条对照打分；
   静帧用 `scripts/qc_sweep.py --json` 全量扫一遍（QC 对"小字"概率性漏报）。

首跑后请把**实测结论回填**到本表（哪几条律真的被执行、执行率多少），并更新
`pack.json` 里所有 `⏳ 未实测` 的标注。**没跑过的包不许装成跑过的包。**
