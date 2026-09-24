# 风格卡：《命案》/ 郑保瑞都市残酷线（Mad Fate · Urban Grim Thriller）

> 来源：2026-09-24 抽帧分析，样本为 **解说版**《命案》全片（21.5 min，960×720@24fps，
> 产物存 `tmp/mingan-0924/`：15503 张 12fps 小帧 + 646 张 2s 读图帧 + 17 张接触印相）。
> ⚠️ **样本前提**：解说话音轨与解说剪辑混在片源里 ⇒ **节奏数字是"解说版节奏"（上限）**，
> 原片实际剪速只会更慢；调色指标以 216 帧中位数计，受解说插图影响极小，可信。

## 一句话定位
郑保瑞《智齿》同源的**都市残酷文艺惊悚**：憋闷不吓人——旧街区被荧光绿/品红霓虹浸泡，
死黑压顶、重暗角、高光去饱和；贴脸特写 + 笼式构图让人物被环境困住；血腥只做暗示。

## 实测数字（风格卡的"用印"，写进契约的依据）

### 剪辑节奏（解说版上限）
- 全片 410 镜 / 1292s：**均值 3.15s、中位 1.92s**；p10=0.82 / p25=1.42 / p90=6.26
- **双峰分布**是重点：<1s 快切爆发 48 个 + >8s 长镜铺陈 25 个（最长 26.75s）
- ⇒ 语法：**长镜憋着 → 突发短切爆发**。落到本管线（单镜 4–12s 钳制）：
  铺陈用 8–12s 慢推镜，爆发用连续 4s 短镜组，**不是每镜均匀 6 秒**

### 调色（216 帧中位）
| 指标 | 实测 | 含义 |
|---|---|---|
| 抬黑 p01 | **0.0（死黑）** | 暗部黑得死，不雾不灰——跟"胶片感"反向 |
| 压白 p99 | 216（无纯白） | 高光收敛，天空压成灰白或过曝剪影 |
| 对比跨度 p95−p05 | 136–142 | 高反差 |
| 中带 R−B | **+25（暖）** | 画面只有光源是暖的（钨丝灯/烛火/钠灯） |
| 暗带 R−B | +4（中性偏冷） | 环境壳子是冷的——"暖光源泡在冷壳子里" |
| 高带饱和度 | **11%** | 高光几乎去饱和（灯芯/天空发白） |
| 中带饱和度 | 39% | 中调低饱和 |
| **暗角（角/心）** | **0.40（极重）** | 四角明显压暗，人物被黑暗包住 |
| 颗粒（拉普拉斯std） | 25.8 | 有质感，但这是解说二压，原片更低 |

### 声音（3×30s 采样，含解说旁白，仅供参考）
中位 −12 ~ −18.5 dB、动态范围 12–28 dB——音轨全程稠密零静音。
本管线落地：`audio_mode: silent`（无台词）+ **分镜「环境声」列写满音景**（雨声、低频嗡鸣、
电流声、远处犬吠）+ `PACK_BGM=1` 模型自带配乐。真实折扣：BGM 不可逐镜指挥。

## 视觉风格（读图归纳，17 张接触印相）

### 三套光照模式（按剧情段落切换）
1. **夜戏·双荧光色**：旧街巷被青绿荧光灯管 + 品红/紫红霓虹对冲浸泡（唐楼走廊、
   室内法事场面一品红打光）——这是全片辨识度最高的一招
2. **日戏·高照度残酷日光**：山坡挖坟/土场戏，正午硬光、土黄泥坡、无柔光，
   汗湿的脸直接晒给观众
3. **高潮·过曝白天空**：天台戏把天空压成纯白废纸，人物成深色剪影/半剪影，
   卫星天线锅形成"笼子"框住人

### 构图与机位
- **贴脸特写占比极高**：人脸常顶到画框，头顶被切掉，偏中心、不给你喘息空间
- **笼式构图**：窗框/栏杆/锅形天线/门框把人困在几何牢笼里——"被环境困住"的具象化
- 静态机位为主，运动只在关键拍（压迫→爆发的那一下）
- 道具有仪式感：命理罗盘、符纸、祭品、观音像——**宿命主题的实物锚点**

### 血腥 = 暗示（硬契约，也是过审策略）
苍白皮肤上一道血痕、血泊反光、拖痕、包扎布、事发后的现场——**不拍过程**。
供应商图像/视频模型会拒真血腥，暗示性处理反而更贴本片气质，双赢。

## 质感关键词（英文提示词用）
`gritty urban Hong Kong thriller, oppressive atmosphere, cyan-green fluorescent and
magenta neon lighting on rain-soaked old streets, crushed blacks, no pure white,
heavy vignette, desaturated highlights, warm practical lights in a cold shell,
tight off-center close-ups, cage framing through window frames and railings,
blood only as small red accents on pale skin, ritual talismans and offerings`

## 生成提示词模板（定妆/静帧）
```
Gritty Hong Kong crime thriller film still, oppressive claustrophobic mood.
A gaunt middle-aged fortune teller in a shabby dark jacket leans into frame in a
tight off-center close-up, head cropped by the top edge, lit by a single warm
tungsten bulb against crushed-black shadows, heavy vignette swallowing the corners.
Behind him a rain-soaked old Kowloon alley glows cyan-green from fluorescent tubes
and magenta neon. Pale skin, exhausted eyes, no pure white in the frame, high
contrast, desaturated highlights, cinematic grit.
```
负面词：`pure white highlights, flat even lighting, bright cheerful colors, wide
airy composition, center-framed portrait, clean modern streets, graphic blood, gore`

## 落地到管线：`packs/madfate-grim/` 骨架建议
- `pack.json`：`audio_mode 默认 silent`；`still-refs: true`；`still-tail: material`；
  **不启用** short-drama-hooks/爽点 craft（憋闷片跟"密度递增/钩子"判据直接对打）；
  `style-watch: true`（QC 盯住别漂成普通都市剧）
- `director/SKILL.md`：节奏双峰语法——铺陈 8–12s 慢推、爆发连排 4s 短镜；
  运镜只在关键拍
- `scenedesigner/SKILL.md`：构图三件套（贴脸特写/笼式构图/被环境挤压）；
  **环境声列按灵魂列对待**：每镜必填（雨/电流/嗡鸣/远处声），不留空
- `reviewer/SKILL.md`：新增风格轴——"画面是否黑暗压迫、有无死黑与暗角、
  血腥是否只做暗示"；漂成明亮通透 = P1
- 版权线：**只取美学，不复刻剧情与角色**（同 laofuzi-hk-retro 处理）

## 复刻可行性评估
| 维度 | 可达程度 | 说明 |
|---|---|---|
| 调色/光影（三套光照模式） | ★★★★☆ | style-block 可执行词已备好；双荧光色是模型擅长项 |
| 死黑+重暗角 | ★★★★☆ | 提示词可锁；必要时后期 ffmpeg 一行兜底 |
| 构图（贴脸/笼式） | ★★★☆☆ | 靠分镜质量 + QC 风格轴盯着；agnes 构图服从度一般 |
| 憋闷节奏 | ★★★☆☆ | 4–12s 钳制内做"长镜铺陈"可行，真长镜（20s+）做不了 |
| 音景 | ★★★☆☆ | 环境声列+BGM 可保大半；不可逐镜指挥配乐 |
| 血腥暗示 | ★★★★☆ | 暗示性处理本来就是模型安全区的舒适解 |
