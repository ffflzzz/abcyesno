# 风格卡：老夫子 AI 重制 / 加拍（HK 老夫子 Douyin-Remake Style）

> 来源：2026-09-17 抽帧分析 3 个抖音片段（24 帧，存于 `style-analysis/A|B|C/`）。
> 项目背景：抖音爆火的「老夫子 AI 重制 + 加拍」——用 AI 视频生成复刻老夫子漫画/老港片
> 的人物造型与市井场景，并"加拍"漫画里没有的新桥段。
> 2026-09-17 已用本项目 ImageGen 实测定妆首帧验证：**造型可 1:1 复刻**（见 `style-analysis/testgen/`）。

## 一句话定位
老夫子漫画 IPs 的真人化 AI 复刻：经典三件套造型（黄衫黑马甲+清朝帽+圆框眼镜）+
60-80 年代香港骑楼街/唐楼天台市井场景 + 无厘头肢体喜剧 + 粤语口语腔硬字幕。

## 角色造型（一致性核心，已验证可复刻）

| 角色 | 造型要点 | 复刻实测 |
|---|---|---|
| **老夫子** | 明黄色长衫 + 黑色唐装马甲（缀 3 颗白色圆球扣）+ 清朝红顶凉帽 + 金丝圆框眼镜 + 小八字胡；夜盗/潜入戏加黑面罩 | ★★★★★ 试生成已 1:1 |
| **老赵** | 光头 + 浓八字胡 + 深紫红西服 + 白衬衫 + 粉红领带 + 黑皮鞋；趾高气扬抱臂 | ★★★★★ 试生成已 1:1 |
| 配角 | 80 年代碎花短袖衬衫 + 灰西裤 + 蓬松黑发（可换大番薯：矮胖光头一撮毛） | 未测 |

⚠️ 版权提示：老夫子 IP（王泽家族）与老港片素材均有版权。个人二创玩梗是平台灰色地带，
**接进商用生产管线前必须评估授权**；建议角色命名/造型做"神似改形"规避。

## 视觉风格

### 色调（两种模式，对应"重制"与"加拍"两条产物线）
- **重制风（A/B 段）**：老胶片质感——阴天灰青天空 + 暖钨丝灯室内（黄墙、绿百叶窗、红木地板），
  低饱和、胶片颗粒、35mm 老电影感
- **加拍风（C 段）**：高饱和怀旧数码调——黄昏粉橙天空、夜景深蓝 + 暖黄街灯，
  画面干净、浅景深（注：C 段招牌乱码是 AI 生成的已知破绽 → 招牌一律远景虚化）

### 场景锚点
- 骑楼老街（满街繁体中文招牌、电线杆、老式街灯）——招牌做虚化背景，避免 AI 乱码穿帮
- 老唐楼天台（晾衣绳、水塔、斑驳水泥墙、远处密集旧楼）
- 旧式客厅（吊扇、绿百叶窗、红木家具、西洋画框）

### 质感关键词（英文提示词用）
`1980s Hong Kong cinema, retro film look, warm tungsten lighting / overcast grey sky,
vintage Kowloon tenement rooftop, tong lau arcade street with blurred Chinese signboards,
slapstick comedy, 35mm film grain (重制风) / clean digital remaster, saturated nostalgic grade (加拍风)`

## 镜头语言（从三段实测归纳）
- 双人中景对手戏为主：一人站高位抱臂（老赵压制），一人矮位躬身（老夫子吃亏），高低错位
- 低角度仰拍强化大佬压迫感；面部大特写给情绪笑点（瞪眼、冷汗、张嘴惊呼）
- 追逐/挨打戏：肩上跟拍 + 快速横移；动作笑点拍下半身局部（悬空双脚、擦地快爬）
- 加拍新桥段遵循同一语法：**每镜 3-6 秒一个包袱**——先给威胁/冲突，再给夸张受挫反应

## 叙事/台词
- 台词风格：粤语口语直译腔（"后生仔，你搞什么啊""有胆就跳下来""哎呀死啦死啦"）
- 老夫子式反差：自作聪明 → 倒霉收场（"听说你好聪明，没人能骗你哦"→"现在才想起，我上当了！"）
- 硬字幕：白字黑边居中底部（后期加，勿让模型生成）

## 生成提示词模板（定妆首帧，已实测出图）
```
1980s Hong Kong comedy film still, retro cinematic color grading. A bald man with a
thick mustache, wearing a maroon suit, white shirt and pink tie, stands with arms
crossed and a smug grin on a vintage Kowloon tong lau arcade street at dusk,
pink-orange sky, blurred traditional Chinese shopfront signboards in the background,
shallow depth of field, 35mm film look. In the left foreground, a shorter crafty old
man in a bright yellow robe, black vest with three white pom-pom buttons, and a
Qing-dynasty red-topped hat with round wire glasses, bowing comically.
```
负面词：`modern clothing, modern cars, neon LED signs, English signage, readable text, blurry face, extra fingers, watermark`

## 「重制 + 加拍」两条产物线怎么做

### 线 1：加拍新桥段（本项目管线可直接做 ✅）
1. 定妆首帧（模板已验证）→ 存为角色参考图
2. 分镜脚本（老夫子式包袱：机灵→翻车）→ 每镜文生图首帧（复用定妆图保一致性，图生图）
3. 图生视频逐镜生成（agnes-video-2.5，单镜 4-12s）→ 前镜尾帧续接
4. 后期：粤语腔配音 + 硬字幕 + 胶片颗粒滤镜统一质感

### 线 2：重制老片段（当前管线**只能近似** ⚠️）
- 理想做法是**视频风格迁移**（video-to-video restyle），本项目没有该能力
- 可行近似：抽关键帧 → ImageGen 图生图风格化 → 图生视频回帧——**时间一致性差**，
  人物会在帧间跳变；且用老片帧做输入还有素材版权问题
- 结论：**优先做"加拍"线**（原创桥段 + 复刻造型场景），这才是系统强项；
  "重制"线除非接入真正的视频风格迁移模型，否则不建议承诺

## 复刻可行性评估（对照 C 段实测）
| 维度 | 可达程度 | 说明 |
|---|---|---|
| 造型/戏服 | ★★★★★ | 本项目 ImageGen 实测 1:1 |
| 场景氛围（骑楼街/天台） | ★★★★☆ | 氛围到位；招牌乱码靠虚化规避 |
| 色调/年代感 | ★★★★☆ | 两种模式均可稳定复现 |
| 镜头语言/喜剧节奏 | ★★★☆☆ | 靠分镜质量；肢体喜剧细节是 agnes-video 弱项 |
| 招牌/字幕 | ★★☆☆☆ | 生成必乱码 → 远景虚化 + 后期字幕 |
| 视频风格迁移（重制线） | ★★☆☆☆ | 无原生能力，帧级近似会跳变 |
