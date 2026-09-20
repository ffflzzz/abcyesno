# -*- coding: utf-8 -*-
"""资产生产层 + 成片复核自测（纯逻辑，不调 API）。

覆盖本轮修复的四类事故：
  1. cast 解析角色卡/资产卡（图从未生成 → 一致性归零的根因）
  2. 对白清洗（裸台词进 prompt → 乱念 + 烧字幕）
  3. 英文短指令（中文负面长句 → 烧到屏幕上）
  4. 成片抽帧复核的作废逻辑（首帧合规 ≠ 成片合规）
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v5.media import cast, clipqc, prompt  # noqa: E402

WB_MD = """# 角色卡：值夜店员（主角）

## 基本信息
- 姓名：林宇（固定人名，全片统一使用，不得更改）
- 年龄：26岁

## 外貌特征（用于生图）
亚洲青年男性，二十五六岁，短寸黑发略显凌乱，肤色偏苍白，眼下有明显青黑阴影。

**服装**：灰蓝色便利店制服短袖，左胸口袋上方别着一枚白底塑料工牌。

## 背景故事
林宇，普通二本毕业，在城郊一家连锁便利店值夜班。

---

# 角色卡：监控分身（林宇的分身）

## 基本信息
- 姓名：同「林宇」（监控中的另一个林宇）

## 外貌特征（用于生图）
与主角完全一致的外貌：同样的短寸黑发、同样的灰蓝色制服，姿态僵硬。
"""

ASSETS_MD = """# 关键资产清单

## 资产卡：天花板监控屏幕
- 名称：天花板监控屏幕
- 类型：object
- 关键词：监控屏幕、六格监控、监控画面
- 用途：贯穿全片的信息源与悬念制造者，黑白低对比度画面。
- 参考图：images/天花板监控屏幕.png

## 资产卡：收银台
- 名称：收银台
- 类型：object
- 关键词：收银台、收银机、柜台
- 用途：主角值班的主要舞台与空间锚点，深色磨损台面。
- 参考图：images/收银台.png
"""


class TestParseCharacters(unittest.TestCase):
    def test_extracts_name_and_appearance(self):
        chars = cast.parse_characters(WB_MD)
        # 「同「林宇」」的引号被剥掉——名字要当文件名用（sanitize 后才能落盘）
        self.assertEqual([c["name"] for c in chars], ["林宇", "同林宇"])
        self.assertIn("短寸黑发", chars[0]["appearance"])
        self.assertIn("灰蓝色便利店制服", chars[0]["appearance"])

    def test_clean_name_strips_markdown_and_illegal_chars(self):
        """文件名 sanitize（2026-09-10《热牛奶》Errno 22 事故）：

        worldbuilder 把姓名写成 `**苏晚**` → 原实现只剥括号 → `**苏晚**.png`
        撞上 Windows 非法字符 `*` → 参考图全灭 → 人物一致性归零。
        """
        self.assertEqual(cast._clean_name("**苏晚**"), "苏晚")
        self.assertEqual(cast._clean_name('"老周"'), "老周")
        self.assertEqual(cast._clean_name("陈默/分身?"), "陈默分身")

    def test_skips_background_story(self):
        """外貌段只取长相与服装，不把背景故事/角色弧光带进生图提示词。"""
        c = cast.parse_characters(WB_MD)[0]
        self.assertNotIn("普通二本毕业", c["appearance"])

    def test_keywords_are_name_only(self):
        """角色 keyword **只保留角色名**（2026-09-13 反转）。

        旧行为给**每个**角色追加 ("主角","男主","女主","他","她","本人") —— 多角色
        项目的 keyword 表因此**完全重合**：实测 noodle-night 的「老陈」与「女孩」
        表一模一样，任何含「她」的镜两个角色同时命中；而 `bind()` 只留一张人物
        参考图（`chars[:1]`）→ 直接绑错脸。

        "分镜写「主角」而不写姓名"这个原始诉求由别处承担，所以移除是安全的：
        `assets.person_in_text`（自带泛词判据，驱动主角兜底）+
        `assets._chars_by_name`（按角色名精确补漏）。
        """
        self.assertEqual(cast._ROLE_ALIASES, (),
                         "泛词会让多角色 keyword 表重合，不要再加回来")
        c = cast.parse_characters(WB_MD)[0]
        self.assertIn("林宇", c["keywords"])
        for alias in ("主角", "男主", "女主", "他", "她", "本人"):
            self.assertNotIn(alias, c["keywords"], alias)

    def test_strips_parenthetical_from_name(self):
        chars = cast.parse_characters(WB_MD)
        self.assertNotIn("（固定人名", chars[0]["name"])

    def test_accepts_alternative_appearance_wordings(self):
        """★ 外貌段的**措辞**必须容忍多种（2026-09-14 实测事故，代价是一整条链）。

        `packs/niulai-movie-style/worldbuilder/SKILL.md` 产出的是
        「**外形与材质（出图提示词口径）：**…」且**正文写在同一行**；
        而原实现写死「外貌特征」并要求内容换行 → `ap=""` → `len(ap) < 20 → continue`
        → **两个角色全被丢弃** → `_names` 空 → 全片 26 镜注入「空镜：环境静物」
        → 实测 **5/26 镜退回写实照片级**（牛来包唯一的失败条件）+ 身份锚点全丢。

        以下用**真实产物原文**做回归。
        """
        md = (
            "# 角色卡：马德胜（村长，52 岁）\n"
            "- 外形与材质（出图提示词口径）：中年男性，圆脸，粗短眉毛，小眼睛，"
            "短黑发微乱，胡茬粗糙平面；蓝色旧中山装（色块蓝，领口与袖口磨白起毛边），"
            "身材敦实偏胖，肩宽腿短比例失真，左臂戴红色袖章（纯色红块，无字）。\n"
            "\n"
            "# 角色卡：杨小满（会计，28 岁）\n"
            "- 外形与材质（出图提示词口径）：年轻男性，瘦脸偏高，长下巴，细眉，眼大，"
            "短黑发服帖；格子衬衫扎进腰带，怀里抱着一本黑硬壳账本，瘦高身材手指细长方块状。\n"
        )
        chars = cast.parse_characters(md)
        self.assertEqual([c["name"] for c in chars], ["马德胜", "杨小满"],
                         "措辞不同不该让角色被丢弃")
        self.assertIn("蓝色旧中山装", chars[0]["appearance"])
        self.assertIn("格子衬衫", chars[1]["appearance"])

    def test_appearance_stops_at_next_heading(self):
        """外貌段不得吃到下一个卡（否则生图提示词会混入别的资产描述）。

        注意夹具要写够长：`parse_characters` 有 `len(ap) < 20 → 丢弃` 的下限
        （防「外貌段其实是空的」）。我第一版夹具只有 19 字，测试自己把它丢了。
        """
        md = ("# 角色卡：甲（主角）\n"
              "- 外形与材质：圆脸短黑发，穿深色外套，身材敦实偏胖，肩宽腿短比例失真。\n"
              "# 资产卡：老磅秤（机械体）\n"
              "- 圆形表盘画成无字色块弧段，方形钢板秤台，锈红粗壮立柱。\n")
        c = cast.parse_characters(md)[0]
        self.assertIn("圆脸短黑发", c["appearance"])
        self.assertNotIn("老磅秤", c["appearance"])

    def test_fenced_card_with_plus_marker(self):
        """★★ 模型把契约里的**示例格式连同代码围栏**一起复制（2026-09-14 第五种变体）。

        真实产物（dawn-broadcast 第三跑）：
            ```
            #+ 角色卡：老赵
            ```
            人类，男性，55 岁左右。圆脸……
        → 既不是 `# 角色卡：`、也不是字段/标题，而是**围栏后的一段普通正文** → 角色全丢。
        三处都要容忍：`#+` 装饰符、代码围栏行、以及"只有一段正文"这种形态。
        """
        md = ("## 四、角色卡（硬契约）\n\n"
              "```\n#+ 角色卡：老赵\n```\n"
              "人类，男性，55 岁左右。圆脸，脸宽大于脸长，双下巴明显，脖子短粗。"
              "蓝灰色旧夹克（表面磨损、补丁），灰色长裤裤脚略卷，左臂戴红袖章，无文字。\n\n"
              "```\n#+ 角色卡：小许\n```\n"
              "人类，男性，26 岁左右。瘦高，方下颌，脸型偏长，黑色短发块状、刘海不齐。"
              "橙红色工装马甲（两块灰色口袋），深蓝工装裤，肩挎深蓝帆布工具包。\n")
        chars = cast.parse_characters(md)
        self.assertEqual([c["name"] for c in chars], ["老赵", "小许"],
                         "`#+` 装饰符 + 代码围栏 + 正文档 都要容忍")
        self.assertIn("蓝灰色旧夹克", chars[0]["appearance"])
        self.assertIn("橙红色工装马甲", chars[1]["appearance"])
        self.assertNotIn("```", chars[0]["appearance"], "围栏标记不得进生图提示词")

    def test_appearance_split_into_fields(self):
        """★★ **外观拆成多个字段**、根本没有"外貌段"时，也要拼出来（2026-09-14 第三次实测）。

        真实产物（dawn-broadcast）：
            `# 角色卡：老赵（村长）`
            `- 物种：人类，男性，55 岁左右。`
            `- 脸型与头部：圆脸，脸宽大于脸长，额头低……`
            `- 体型：…  - 服装：…  - 标记：…  - 材质：…  - 低模风格：…  - 用途描述：…`
        前两轮我都在**加措辞**（`外貌特征` → `外形与材质`），每次都只多撑一轮；
        **这次换成换"结构假设"**：抓不到外貌段就**拼外观类字段**。
        """
        md = ("# 角色卡：老赵（村长）\n"
              "- 物种：人类，男性，55 岁左右。\n"
              "- 脸型与头部：圆脸，脸宽大于脸长，双下巴明显，脖子短粗。\n"
              "- 体型：中等偏矮，略胖，肩膀宽而圆，手臂粗短。\n"
              "- 服装：蓝灰色旧夹克，表面有磨损和补丁，内搭深色高领毛衣。\n"
              "- 标记：左臂戴一条红袖章，无文字；走路外八字。\n"
              "- 低模风格：face planes 粗糙，眼睛为简单的球体，表情僵硬但可读。\n"
              "- 用途描述：全片主角，清晨冲出门、查喇叭、与小许互相怀疑。\n")
        chars = cast.parse_characters(md)
        self.assertEqual([c["name"] for c in chars], ["老赵"], "拆字段也要认出来")
        ap = chars[0]["appearance"]
        for w in ("圆脸", "蓝灰色旧夹克", "红袖章", "中等偏矮"):
            self.assertIn(w, ap, w)
        self.assertNotIn("全片主角", ap, "用途/剧情类字段不得进生图提示词")

    def test_appearance_section_still_preferred_over_fields(self):
        """回归：**有**外貌段时仍走原路径（兜底只在抓不到时启用，不改变既有行为）。"""
        md = ("# 角色卡：林宇（固定人名）\n"
              "## 基本信息\n- 姓名：林宇\n"
              "## 外貌特征（用于生图）\n亚洲青年男性，短寸黑发略显凌乱，肤色偏苍白。\n"
              "## 背景故事\n林宇，普通二本毕业，在城郊一家连锁便利店值夜班。\n")
        c = cast.parse_characters(md)[0]
        self.assertEqual(c["name"], "林宇")
        self.assertIn("短寸黑发", c["appearance"])
        self.assertNotIn("普通二本毕业", c["appearance"], "背景故事不得被拼进来")


class TestParseAssets(unittest.TestCase):
    def test_extracts_asset_cards(self):
        items = cast.parse_assets(ASSETS_MD)
        self.assertEqual([a["name"] for a in items], ["天花板监控屏幕", "收银台"])
        self.assertEqual(items[0]["type"], "object")
        self.assertIn("六格监控", items[0]["keywords"])

    def test_ref_line_not_in_prompt(self):
        """「参考图：images/xxx.png」是产物声明，不是外观描述，不能进生图提示词。"""
        items = cast.parse_assets(ASSETS_MD)
        self.assertNotIn("images/", items[0]["prompt"])

    def test_unknown_type_falls_back_to_prop(self):
        md = ASSETS_MD.replace("类型：object", "类型：未知类型", 1)
        self.assertEqual(cast.parse_assets(md)[0]["type"], "prop")


class TestParseAssetsFormatCompat(unittest.TestCase):
    """牛来 / 3D 包的**实际产物格式**（2026-09-14 实测事故）。

    背景：这两个包的 assetdesigner 契约**没有规定 `assets.md` 的格式**，模型写成
    `### <名>（<type>）` + `- **类型**：` + 独立的「外形提示词」引用段。
    而 `parse_assets` 原本只认 `## 资产卡：` → 对真实产物**恒返回 0 条**且不报错
    → 场景资产从未进注册表 → `scene_lines` 无场景可用 → **切镜就换场景**。
    """

    MD = (
        "# 资产参考图 ｜ 某片\n\n"
        "## 道具/设施卡\n\n"
        "### 大喇叭（prop）\n\n"
        "- **名称**：大喇叭\n"
        "- **类型**：prop\n"
        "- **关键词**：大喇叭、圆锥壳体、铁锈色\n"
        "- **用途**：全片核心道具，LN01/LN05 出镜\n"
        "- **参考图**：（待媒体层生成）\n\n"
        "**外形提示词（出图 prompt）**\n\n"
        "> 原始低成本三维重建的孤立机械设施参考，浅灰纯色背景。\n"
        ">\n"
        "> 圆锥形大喇叭壳体，灰白色金属，表面铁锈色斑点。\n\n"
        "---\n\n"
        "## 场景参考（可选，供媒体层按需生成）\n\n"
        "### 村口电线杆场景（scene）\n\n"
        "- **名称**：村口电线杆\n"
        "- **类型**：scene\n"
        "- **关键词**：村口、电线杆、灰蓝天空\n"
        "- **用途**：LN01/LN04-LN09 主要场景\n\n"
        "**外形提示词（出图 prompt）**\n\n"
        "> 中国北方乡镇村口，天将亮未亮。灰白水泥电线杆，杆顶横挂铁锈色大喇叭，\n"
        "> 背景暗绿屋顶与土黄墙，深灰水泥地面。\n"
    )

    def test_reads_bullet_style_cards(self):
        items = cast.parse_assets(self.MD)
        self.assertEqual([a["name"] for a in items], ["大喇叭", "村口电线杆"],
                         "三级标题形态的资产卡必须被解析出来（原实现返回 0 条）")

    def test_scene_type_is_normalised_to_location(self):
        """`scene` 是 assetdesigner 契约的写法；注册表/绑定/锚点的口径是 `location`。"""
        got = {a["name"]: a["type"] for a in cast.parse_assets(self.MD)}
        self.assertEqual(got["村口电线杆"], "location")
        self.assertEqual(got["大喇叭"], "prop")

    def test_prompt_comes_from_appearance_section_not_usage(self):
        """「用途」写的是**出场镜次**，不是外形 —— 只有取「外形提示词」段才拿得到材质/光线。

        取错字段的代价：注册表里的场景描述会变成"LN01/LN04-LN09 主要场景"，
        而场景锚点的全部价值就是那段光色描述。
        """
        got = {a["name"]: a["prompt"] for a in cast.parse_assets(self.MD)}
        self.assertIn("铁锈色斑点", got["大喇叭"])
        self.assertNotIn("出镜", got["大喇叭"])
        self.assertIn("灰白水泥电线杆", got["村口电线杆"])

    def test_strips_reference_boilerplate(self):
        """出图样板首句（"…参考图，浅灰纯色背景，无文字、无水印。"）对锚点零信息量，
        却要占锚点限长 —— 必须剥掉，让「光源/色温/陈设」有位置。"""
        got = {a["name"]: a["prompt"] for a in cast.parse_assets(self.MD)}
        self.assertTrue(got["大喇叭"].startswith("圆锥形大喇叭壳体"),
                        "样板首句应被剥掉：%r" % got["大喇叭"][:60])
        self.assertNotIn("纯色背景", got["大喇叭"])

    MD_KIND = (
        "# 资产卡：某片（单集 3 分钟）\n\n"
        "> 本文件为阶段完成唯一判据（资产卡）。\n\n"
        "## 场景卡\n\n"
        "### 场景卡：收粮站（深夜）\n"
        "- 类型：scene｜关键词：`收粮站`、`深夜`\n"
        "- 外形/材质/结构（出图提示词，无人物/无关系/无文字）：\n"
        "  北方村镇粮食收购站室内，泥土地面；正中一台老式机械磅秤，"
        "夜里冷蓝月光从窗缝漏进，朴素冷蓝单光源加硬阴影。\n"
        "- 用途：全片唯一场景。\n\n"
        "## 道具卡\n\n"
        "### 道具卡：老磅秤\n"
        "- 类型：prop｜关键词：`老磅秤`、`磅秤`\n"
        "- 外形/材质/结构（出图提示词）：\n"
        "  一台老式机械磅秤，圆形表盘无数字，金属秤台可见铆钉硬块。\n"
    )

    def test_card_kind_title_format(self):
        """**第三种写法**：`### <卡类型>：<名>` + 同行 `类型｜关键词` + 无引用块的正文。

        village-scale 实测：文件首行是**文件标题** `# 资产卡：…（单集 3 分钟）`，
        而卡片是 `### 场景卡：收粮站（深夜）` —— 解析器原先两条都不认
        （`^#+` 把文件标题当卡片头 → 整个文件只切出 1 块）→ `assets.md` 只出 1 条
        → 场景锚点 0/27。
        """
        items = cast.parse_assets(self.MD_KIND)
        got = {a["name"]: a for a in items}
        self.assertIn("收粮站", got, "「卡类型：名」形态的标题必须被认出来")
        self.assertEqual(got["收粮站"]["type"], "location", "「场景卡」前缀要映射成 location")
        self.assertIn("深夜", got["收粮站"]["keywords"], "同行的关键词列也要取到")
        self.assertIn("冷蓝月光", got["收粮站"]["prompt"], "无引用块的正文也要能取")
        self.assertEqual(got["老磅秤"]["type"], "prop")
        self.assertFalse([k for k in got if "资产卡" in k],
                         "文件级一级标题不能被当成卡片：%s" % list(got))


class TestCleanDialogue(unittest.TestCase):
    def test_strips_speaker_and_tone(self):
        """角色名与语气标注是导演提示，不是台词——念出来就是"乱念"。"""
        self.assertEqual(prompt.clean_dialogue("**林宇**（轻声，冷笑）：三个月了……你演得真好。"),
                         "三个月了……你演得真好")
        # 省略号是台词语气的一部分，必须保留（只去尾部标点，不去省略号）
        self.assertEqual(prompt.clean_dialogue("林宇：又是最后一个钟头……"),
                         "又是最后一个钟头……")

    def test_strips_speaker_with_long_parenthetical(self):
        """括号先剥、说话人后剥。

        实测 LN08「监控分身（用他自己的声音，平静）：下班了。」——
        括号让前缀超过 12 字，先剥说话人会整个漏掉，把说话人当台词念出来。
        """
        self.assertEqual(prompt.clean_dialogue("监控分身（用他自己的声音，平静）：下班了。"),
                         "下班了")

    def test_tail_line_no_double_wrap(self):
        """落幅原文已含「镜头…」时不得再加「镜头最终停在」前缀。

        实测 LN08 拼成「镜头最终停在镜头缓缓推向监控屏幕…」。
        """
        self.assertEqual(
            prompt.tail_line({"tail": "镜头缓缓推向监控屏幕，画面定格在那张脸上"}),
            "镜头缓缓推向监控屏幕，画面定格在那张脸上")
        self.assertEqual(prompt.tail_line({"tail": "林宇揉眼睛的动作上"}),
                         "镜头最终停在林宇揉眼睛的动作上")
        # 「落幅…」开头也按**自带落幅**处理，原样用（2026-09-13）。旧实现会改写成
        # 「镜头最终停在…」，与官方范式的「落幅定格在…」写法不一致。
        self.assertEqual(prompt.tail_line({"tail": "落幅停在空钱盒与他垂下的指尖"}),
                         "落幅停在空钱盒与他垂下的指尖")
        # 裸动词开头同样算"自带落幅"（2026-09-13，noodle-night LN02 实测）：
        # 原文「落在女孩停在门外的侧脸与微张的嘴唇」曾拼成
        # 「镜头最终停在落在女孩停在门外的侧脸与微张的嘴唇」——三个"停/落"叠在一起。
        for raw in ("落在女孩停在门外的侧脸与微张的嘴唇",
                    "停在母亲的手上",
                    "收在两人中间的空隙",
                    "定在窗外渐亮的天光",
                    "定格在她脸上"):
            self.assertEqual(prompt.tail_line({"tail": raw}), raw, raw)

    def test_silent_markers_become_empty(self):
        for cell in ("（无声）", "（无声，环境音）", "无对白", "无", "-", ""):
            self.assertEqual(prompt.clean_dialogue(cell), "", cell)

    def test_truncates_long_line(self):
        out = prompt.clean_dialogue("啊" * 200)
        self.assertEqual(len(out), 70)

    def test_audio_line_uses_audio_contract(self):
        """台词必须走 `Audio:` 契约字段，不能裸拼进正文。

        实测事故：裸台词被模型当旁白念、并渲染成屏幕字幕。

        **2026-09-12 修正**：原断言 `assertIn("Ambient only", line)` 把 bug 写进了
        测试——有台词的镜同时收到 `Audio: 台词` 与 `Ambient only, do not speak:`，
        两句自相矛盾（一边叫它念、一边叫它别出声）→ 模型噤声后自己即兴配音。
        用户实测："所有镜头说的对白都是'大家好，很高兴和大家分享今天的内容'"。
        现在：有台词 → 环境声只作 `Background ambience:`，不带任何禁声指令。
        """
        line = prompt.audio_line({"dialogue": "林宇：别闹了……到底是谁。",
                                  "sfx": "冰柜嗡嗡声"})
        self.assertTrue(line.startswith("Audio: "))
        self.assertIn("别闹了", line)
        self.assertIn("冰柜嗡嗡声", line)
        self.assertIn("Background ambience", line)
        self.assertNotIn("do not speak", line, "有台词的镜绝不能带禁声指令")

    def test_silent_shot_uses_positive_sound_label(self):
        """★ 无台词镜的环境声段必须用**正向** `Sound: ` 标签（2026-09-14 A/B/C 实测）。

        旧行为是 `Ambient only, do not speak: <环境声>` —— 而**那个标签正是诱发说话的原因**：
        用户逐条听三变体（同一镜、同一首帧，只改音频段）：
          A 现状（否定式标签 + `no speech, no talking…`）→ 念「大家好 我们开始今天的训练」❌
          B 正向（`Sound: <环境声规格>` + 仅禁字幕）→ 干净环境音 ✅
          C 不写（无音频段 + 仅禁字幕）→ 干净环境音 ✅
        机制：`do not speak: X` 把**"说话"这个动作词与内容 X 摆在一起**（与 `Audio: <台词>` 同构）
        → 模型把它当"要念的内容"；否定的尾巴被忽略（音频通道不读否定）。
        """
        line = prompt.audio_line({"dialogue": "（无声，环境音）", "sfx": "冰柜嗡嗡声"})
        self.assertIn("Sound: ", line)
        self.assertIn("冰柜嗡嗡声", line)
        self.assertNotIn("Audio:", line)
        self.assertNotIn("do not speak", line, "否定式标签会诱发人声")
        self.assertNotIn("no speech", line)

    def test_no_dialogue_has_no_audio_field(self):
        self.assertEqual(prompt.audio_line({"dialogue": "（无声，环境音）", "sfx": ""}), "")
        self.assertFalse(prompt.has_dialogue({"dialogue": "（无声）"}))
        self.assertTrue(prompt.has_dialogue({"dialogue": "林宇：喂。"}))


class TestVideoDirectives(unittest.TestCase):
    def _shot(self, **kw):
        base = {"name": "LN01", "shot_type": "中景", "angle": "平视", "camera": "固定",
                "visual_style": "写实", "visual": "他站在收银台后。", "tail": "他抬头",
                "dialogue": "", "sfx": "", "text_shot": "", "join_note": ""}
        base.update(kw)
        return base

    def test_directive_is_english(self):
        p = prompt.build_video_prompt(self._shot())
        self.assertIn("no on-screen text", p)
        self.assertNotIn("不要字幕", p)

    def test_no_sub_prefix_when_dialogue(self):
        """有台词时禁字幕指令必须**前置**（尾部会被当台词尾音读出）。"""
        p = prompt.build_video_prompt(self._shot(dialogue="林宇：有人吗。"))
        self.assertTrue(p.startswith(prompt.VIDEO_NO_SUB_PREFIX))
        self.assertIn("LANGUAGE: Mandarin Chinese", p)

    def test_no_prefix_without_dialogue(self):
        p = prompt.build_video_prompt(self._shot())
        self.assertFalse(p.startswith(prompt.VIDEO_NO_SUB_PREFIX))
        self.assertNotIn("LANGUAGE:", p)

    def test_dialogue_not_in_body(self):
        """台词只出现在 Audio: 段，正文里不得复现（否则被念/被烧）。"""
        p = prompt.build_video_prompt(self._shot(dialogue="林宇：下班了。"))
        body = p.split("Audio:")[0]
        self.assertNotIn("下班了", body)

    # --- 2026-09-12 事故：有台词的镜被同时要求"念这句"和"别说话" -------------

    def test_dialogue_shot_never_carries_no_speak(self):
        """有台词的镜，完整提示词里**不得**出现禁声指令。

        实测（clockmaker）：40/40 镜的音效列都非空，原实现无条件拼
        `Ambient only, do not speak:`，于是**每一个**有台词的镜都在自相矛盾 ——
        模型取解为"噤声 + 自己即兴配音"，剧本台词一句都没被念出来
        （用户实测"所有镜头说的对白都是'大家好，很高兴和大家分享今天的内容'"）。
        """
        p = prompt.build_video_prompt(self._shot(dialogue="沈师傅：搁这儿吧。",
                                                sfx="鞋底轻触木地板"))
        self.assertIn("Audio:", p)
        self.assertIn("搁这儿吧", p)
        self.assertIn("鞋底轻触木地板", p)
        self.assertIn("Background ambience", p)
        self.assertNotIn("do not speak", p)
        self.assertNotIn("no speaking", p)

    def test_silent_shot_carries_no_speech_trigger(self):
        """★ 无台词镜的完整提示词里**不得出现任何"说话"相关的词**（2026-09-14 A/B/C 实测）。

        旧断言是 `assertIn("Ambient only, do not speak", p)` —— 那是把当时**尚未验伪**的
        做法写成了契约。三变体实听证明：**否定式标签反而是诱发源**（A 念了台词，B/C 干净）。
        """
        p = prompt.build_video_prompt(self._shot(sfx="怀表内轻机械声"))
        self.assertIn("Sound: ", p)
        self.assertIn("怀表内轻机械声", p)
        for w in ("do not speak", "no speech", "no talking", "voice-over",
                  "spoken words", "never go silent"):
            self.assertNotIn(w, p, w)
        self.assertNotIn("Audio:", p)

    def test_audio_segments_are_space_separated(self):
        """片段之间必须有空格——实测踩过 `了Background ambience:` 粘成一串。"""
        p = prompt.build_video_prompt(self._shot(dialogue="沈师傅：搁这儿吧。",
                                                sfx="鞋底轻触木地板"))
        self.assertNotIn("。Background", p)
        self.assertNotIn("吧Background", p)

    def test_segment_order_follows_official_shot_examples(self):
        """段序以**官方真实分镜实例**为准（`../docs-archive-20260918/official-shot-prompt-pattern.md`）：

            景别机位 → 画面风格 → 承接 → 内容与运镜 → 落幅

        ⚠ 这条与官方文档「提示词建议」那一节**不一致**（文档把"主体与场景"排第一）。
        2026-09-12 我曾仅凭文档改成"内容打头"，被 7 张真实实例推翻——实例是
        "景别机位打头、内容排在风格之后"，且与我们长期运行的顺序一致。
        **以实例为准**，故此处把顺序锁成实例的顺序。
        """
        p = prompt.build_video_prompt(self._shot(
            visual="沈师傅缓步走向柜台。", shot_type="中景", camera="固定",
            visual_style="室内暖黄。", dialogue="沈师傅：搁这儿吧。",
            sfx="鞋底轻触木地板", tail="他停在柜台前", join_note="承接上一镜落点"))
        idx = {"景别机位": p.find("中景"),
               "画面风格": p.find("室内暖黄"),
               "承接": p.find("承接"),
               "内容": p.find("沈师傅缓步走向柜台"),
               "落幅": p.find("他停在柜台前")}
        for name, i in idx.items():
            self.assertGreater(i, -1, "缺段: %s" % name)
        self.assertLess(idx["景别机位"], idx["画面风格"], "景别机位必须打头")
        self.assertLess(idx["画面风格"], idx["承接"], "风格在承接之前")
        self.assertLess(idx["承接"], idx["内容"], "承接在内容之前")
        self.assertLess(idx["内容"], idx["落幅"], "落幅收尾")


class TestAssetPromptNoPollution(unittest.TestCase):
    """资产图提示词必须剔除人物/文字联想（2026-09-09 实测事故）。

    事故链：「灰蓝色便利店制服」的用途写着「两件制服必须完全一致」→ 参考图
    画出**两个穿制服的人**；「玻璃门入口」写着「映出主角与分身同框的倒影」
    → 画出**两个人的倒影**。这两张图绑进 LN01 的 refs 后，静帧画出 3 个人。
    """

    def _asset(self, name, prompt):
        return {"name": name, "type": "prop", "keywords": [name], "prompt": prompt}

    def test_drops_person_and_plural_clues(self):
        p = cast._asset_prompt(self._asset(
            "灰蓝色便利店制服",
            "主角与监控分身的视觉统一基础。短袖款式，左胸口袋上方别着白底塑料工牌。"
            "第四幕同框时，两件制服必须完全一致。"))
        self.assertIn("短袖款式", p)
        self.assertIn("塑料工牌", p)
        # 只看描述段（尾缀的正向声明里合法地含"倒影人影"）
        desc = p.split("。竖版构图")[0]
        for bad in ("主角", "分身", "同框", "两件"):
            self.assertNotIn(bad, desc, bad)

    def test_drops_text_clues(self):
        p = cast._asset_prompt(self._asset(
            "天花板监控屏幕",
            "六格黑白监控画面，边缘有模糊英文与数字水印（不可读），偶有信号干扰条纹。"))
        self.assertIn("六格黑白监控画面", p)
        for bad in ("英文", "水印", "数字", "不可读"):
            self.assertNotIn(bad, p, bad)

    def test_declares_no_person(self):
        """资产图必须显式声明"画面中没有任何人"（正向声明，实测更稳）。"""
        p = cast._asset_prompt(self._asset("收银台", "深色磨损台面，边缘贴着褪色的模糊贴纸。"))
        self.assertIn("画面中没有任何人", p)

    def test_name_always_prepended(self):
        """描述被过滤空时，名称/关键词仍要撑起提示词（不能生成空 prompt）。"""
        p = cast._asset_prompt(self._asset("塑料工牌", "主角身份标识，字迹为模糊意象全片不可读。"))
        self.assertTrue(p.startswith("塑料工牌"), p)
        self.assertIn("单一主体", p)


class TestBindSingleCharacterRef(unittest.TestCase):
    """每镜最多绑 1 张人物参考图（2026-09-09 实测）。

    绑 4 张（含 2x2 拼图 + 含人形资产图）→ 静帧画出 3 个人；
    绑「人物单格 + 收银台」→ 只画出 1 个人。人物图多一张就多一个人。
    """

    def test_only_one_character_ref(self):
        import tempfile
        from v5.media import assets
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "images").mkdir()
            from PIL import Image
            # 每张图颜色不同：bind 按 data URI 去重，同色图会被误合并（测试假象）
            for n, color in (("林宇", (10, 10, 10)), ("同「林宇」", (20, 20, 20)),
                             ("收银台", (200, 200, 200))):
                Image.new("RGB", (32, 32), color).save(root / "images" / (n + ".png"))
            reg = {"assets": [
                {"id": "林宇", "name": "林宇", "type": "character", "keywords": ["林宇"],
                 "priority": 10, "ref_image": "林宇.png"},
                {"id": "同「林宇」", "name": "同「林宇」", "type": "character",
                 "keywords": ["同「林宇」"], "priority": 10, "ref_image": "同「林宇」.png"},
                {"id": "收银台", "name": "收银台", "type": "object", "keywords": ["收银台"],
                 "priority": 8, "ref_image": "收银台.png"},
            ]}
            (root / "assets.json").write_text(
                __import__("json").dumps(reg, ensure_ascii=False), encoding="utf-8")
            shots = [{"name": "LN01", "visual": "林宇站在收银台后。", "dialogue": ""}]
            out = assets.bind(root, shots)
            self.assertEqual(len(out["LN01"]), 2)   # 1 人物 + 1 道具

    def test_same_face_character_does_not_add_second_ref(self):
        """同脸角色（监控分身）与主角共用同一张参考图，不能因此多绑一张。"""
        import tempfile
        from v5.media import assets
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "images").mkdir()
            from PIL import Image
            Image.new("RGB", (32, 32), (10, 10, 10)).save(root / "images" / "林宇.png")
            # 分身复用主角图（cast.ensure 的实际行为）
            (root / "images" / "同「林宇」.png").write_bytes(
                (root / "images" / "林宇.png").read_bytes())
            reg = {"assets": [
                {"id": "林宇", "name": "林宇", "type": "character", "keywords": ["林宇", "主角"],
                 "priority": 10, "ref_image": "林宇.png"},
                {"id": "同「林宇」", "name": "同「林宇」", "type": "character",
                 "keywords": ["同「林宇」", "主角"], "priority": 10,
                 "ref_image": "同「林宇」.png"},
            ]}
            (root / "assets.json").write_text(
                __import__("json").dumps(reg, ensure_ascii=False), encoding="utf-8")
            shots = [{"name": "LN01", "visual": "林宇抬头看向监控。", "dialogue": ""}]
            out = assets.bind(root, shots)
            self.assertEqual(len(out["LN01"]), 1)

    def test_with_character_ref_total_capped_at_two(self):
        """有人物图时参考图总数封顶 2（2026-09-09 A/B 实测）。

        同一分镜同一提示词，只有参考图数量不同：
          人物 + 1 道具（2 张）→ 1 个人；
          人物 + 2 道具（3 张）→ **2 个人**（复现两次）。
        多图输入被模型读成"多主体"。纯道具图不受限（无人物图时可多绑）。
        """
        import json
        import tempfile
        from v5.media import assets
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "images").mkdir()
            from PIL import Image
            for n, c in (("林宇", (10, 10, 10)), ("收银台", (200, 200, 200)),
                         ("灰蓝色便利店制服", (30, 30, 30)), ("饮料货架", (40, 40, 40))):
                Image.new("RGB", (32, 32), c).save(root / "images" / (n + ".png"))
            reg = {"assets": [
                {"id": "林宇", "name": "林宇", "type": "character", "keywords": ["林宇"],
                 "priority": 10, "ref_image": "林宇.png"},
                {"id": "收银台", "name": "收银台", "type": "object", "keywords": ["收银台"],
                 "priority": 8, "ref_image": "收银台.png"},
                {"id": "灰蓝色便利店制服", "name": "灰蓝色便利店制服", "type": "prop",
                 "keywords": ["制服"], "priority": 8, "ref_image": "灰蓝色便利店制服.png"},
                {"id": "饮料货架", "name": "饮料货架", "type": "object", "keywords": ["货架"],
                 "priority": 8, "ref_image": "饮料货架.png"},
            ]}
            (root / "assets.json").write_text(
                json.dumps(reg, ensure_ascii=False), encoding="utf-8")
            shots = [{"name": "LN01",
                      "visual": "林宇站在收银台后，旁边是制服和货架。", "dialogue": ""}]
            out = assets.bind(root, shots)
            self.assertEqual(len(out["LN01"]), 2)   # 人物图 + 1 道具，不是 4 张


class TestBindExcludesLocation(unittest.TestCase):
    """location 类资产不得绑进 refs（2026-09-09 实测）。

    LN01 是「店内全景」，只因正文写了"透过玻璃门洒入"就命中「玻璃门入口」，
    而那张图是**店外视角**——绑进去后静帧直接画成站在店门外，机位被场景图
    覆盖。场景机位必须由分镜字段（景别/角度）决定。
    """

    def test_location_not_bound(self):
        import json
        import tempfile
        from v5.media import assets
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "images").mkdir()
            from PIL import Image
            for n, c in (("林宇", (10, 10, 10)), ("玻璃门入口", (200, 200, 200))):
                Image.new("RGB", (32, 32), c).save(root / "images" / (n + ".png"))
            reg = {"assets": [
                {"id": "林宇", "name": "林宇", "type": "character", "keywords": ["林宇"],
                 "priority": 10, "ref_image": "林宇.png"},
                {"id": "玻璃门入口", "name": "玻璃门入口", "type": "location",
                 "keywords": ["玻璃门"], "priority": 8, "ref_image": "玻璃门入口.png"},
            ]}
            (root / "assets.json").write_text(
                json.dumps(reg, ensure_ascii=False), encoding="utf-8")
            shots = [{"name": "LN01",
                      "visual": "店内全景，窗外霓虹透过玻璃门洒入，林宇靠在台后。",
                      "dialogue": ""}]
            out = assets.bind(root, shots)
            self.assertEqual(len(out["LN01"]), 1)   # 只剩人物图


class TestAutoSyncSkipsSheets(unittest.TestCase):
    def test_sheets_dir_not_registered(self):
        """images/_sheets/ 的四视图拼图不得被收编成资产（否则被绑进镜头 → 多人）。"""
        import json
        import tempfile
        from v5.media import assets
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "images" / "_sheets").mkdir(parents=True)
            from PIL import Image
            Image.new("RGB", (32, 32)).save(root / "images" / "收银台.png")
            Image.new("RGB", (32, 32)).save(root / "images" / "_sheets" / "林宇.png")
            reg = assets.auto_sync(root)
            ids = [a["id"] for a in reg.get("assets", [])]
            self.assertIn("收银台", ids)
            self.assertNotIn("林宇", ids)


class TestClipQcInvalidate(unittest.TestCase):
    def test_invalidate_clears_clip_and_job(self):
        """作废必须同时删 clip 与 job 状态。

        只删文件会留下 completed 状态（续跑重提交）；只清状态会让旧 clip
        继续参与拼接。
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            clip_dir = root / "media" / "ep1" / "clips"
            clip_dir.mkdir(parents=True)
            (clip_dir / "LN01.mp4").write_bytes(b"x")
            (clip_dir / "LN01.last.jpg").write_bytes(b"x")
            (clip_dir / "LN02.mp4").write_bytes(b"x")
            from v5.media import jobs as jobs_mod
            jobs_mod.save(root / "media" / "ep1",
                          {"LN01": {"state": "completed", "local": "x"},
                           "LN02": {"state": "completed", "local": "x"}})
            clipqc.invalidate(root, ["LN01"], ep=1, log=lambda *a: None)
            self.assertFalse((clip_dir / "LN01.mp4").exists())
            self.assertFalse((clip_dir / "LN01.last.jpg").exists())
            self.assertTrue((clip_dir / "LN02.mp4").exists())
            jobs = jobs_mod.load(root / "media" / "ep1")
            self.assertNotIn("LN01", jobs)
            self.assertIn("LN02", jobs)


class TestNoRefsPackSkipsAllImages(unittest.TestCase):
    """★ `still-refs: false` 的包（牛来/反质量）**全部资产只登记、不生图**（2026-09-14）。

    为什么必须跳过生图（三条都是既定事实）：
      · `pipeline` 在 `still-refs=false` 时**根本不调 `assets.bind`** →
        `refs_by_shot = {}` → 生出来的图**没有任何消费方**；
      · 而 `cast.ensure` 原本**无条件**生图 → 牛来包 3 角色 + N 道具
        = **每个项目白烧 3+N 次生图配额**（与 location 那次同类，但影响面更大）；
      · 牛来包自己的 `pack.json` 就写着「绑参考图必被拉回精致低模」——
        关参考图是它**刻意的设计**，不是漏配。

    但**必须登记** —— `assets.identity_lines()` 的 docstring 明写
    「pack 关闭参考图（**如牛来风格**）后，身份靠 assets.json 的 `identity`
    压成一句固定锚点逐镜贴上」→ **注册表条目是载荷路径**。
    """

    CONTRACT = json.dumps({
        "characters": [{"name": "马德胜", "appearance": "五十岁圆脸，蓝色旧中山装，袖口磨白",
                        "same_face_as": ""}],
        "assets": [
            {"name": "老磅秤", "type": "prop", "keywords": ["磅秤"],
             "prompt": "绿漆剥落的机械磅秤，圆形表盘有裂痕"},
            {"name": "收粮站", "type": "location", "keywords": ["收粮站"],
             "prompt": "砖墙收粮站内景，一盏吊灯硬阴影，水泥地"},
        ]}, ensure_ascii=False)

    def _root(self, pack="niulai-movie-style"):
        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        (root / "assetdesigner").mkdir(parents=True)
        (root / "assetdesigner" / "assets.contract.json").write_text(
            self.CONTRACT, encoding="utf-8")
        (root / "brief.json").write_text(
            json.dumps({"topic": "t", "pack": pack}, ensure_ascii=False), encoding="utf-8")
        return d, root

    def test_refs_off_generates_nothing(self):
        """生图**一次都不许调**（哨兵会让测试以断言失败告终）。

        ★ 必须同时 patch **两个**生图入口：角色走 `_turnaround`（三视图/四视图），
        道具走 `_single`。只 patch 一个的后果我第一次写这条时踩到了 ——
        未 patch 的 `_turnaround` **真的去调了生图 API**（全量测试从 4 秒变 48 秒）。
        """
        from unittest import mock

        from v5.media import assets as assets_mod

        d, root = self._root()
        with d:
            def _boom(*a, **k):
                raise AssertionError("still-refs=false 时不该调用生图：%r" % (a[:2],))

            with mock.patch.object(cast, "_single", side_effect=_boom), \
                 mock.patch.object(cast, "_turnaround", side_effect=_boom):
                made = cast.ensure(root, log=lambda *_: None)
            self.assertEqual(made.get("noimg"), 2, "角色 + 道具各登记 1 个（不生图）")
            self.assertEqual(made.get("scenes"), 1, "location 走原路径（本就不生图）")
            self.assertEqual(made.get("characters"), 0)
            self.assertEqual(made.get("assets"), 0)
            reg = {a["name"]: a for a in assets_mod.load_registry(root)["assets"]}
            self.assertEqual(sorted(reg), ["收粮站", "老磅秤", "马德胜"], "三个都要登记")
            for n in ("马德胜", "老磅秤", "收粮站"):
                self.assertEqual(reg[n]["ref_image"], "", "%s 没有图就别写 ref_image" % n)

    def test_identity_still_lands_in_registry(self):
        """**载荷路径不能断**：`identity` 必须进注册表，否则身份锚点全丢。

        牛来包的审美靠文字锚点锁身份 —— 只砍生图、不砍登记，正是为了这条。
        """
        from unittest import mock

        from v5.media import assets as assets_mod

        d, root = self._root()
        with d, mock.patch.object(cast, "_single",
                                  side_effect=AssertionError("不该生图")), \
                mock.patch.object(cast, "_turnaround",
                                  side_effect=AssertionError("不该生图")):
            cast.ensure(root, log=lambda *_: None)
            reg = {a["name"]: a for a in assets_mod.load_registry(root)["assets"]}
            self.assertIn("蓝色旧中山装", reg["马德胜"].get("identity", ""),
                          "角色 identity 必须写进注册表")
            # 跨模块闭环：identity_lines 逐镜取到锚点
            out = assets_mod.identity_lines(
                root, [{"name": "LN01", "visual": "@马德胜 站在磅秤旁", "dialogue": ""}])
            self.assertIn("蓝色旧中山装", out.get("LN01", ""),
                          "identity_lines 要能从注册表取到身份锚点")

    def test_refs_on_pack_still_generates(self):
        """回归：默认包（`still-refs` 缺省 True）**生图行为一字不变**。

        角色 → `_turnaround`；道具 → `_single`。两条都要能被调到。
        """
        from unittest import mock

        d, root = self._root(pack="shortdrama")
        with d:
            def _fake_any(root_, *a, **k):
                name = a[0] if isinstance(a[0], str) else a[0]["name"]
                cast.images_dir(root_).mkdir(parents=True, exist_ok=True)
                (cast.images_dir(root_) / (name + ".png")).write_bytes(b"PNG")
                return True

            with mock.patch.object(cast, "_single", side_effect=_fake_any) as g1, \
                 mock.patch.object(cast, "_turnaround", side_effect=_fake_any) as g2:
                made = cast.ensure(root, log=lambda *_: None)
            self.assertEqual([c.args[1]["name"] for c in g2.call_args_list], ["马德胜"],
                             "角色走 _turnaround（三视图）")
            self.assertEqual([c.args[1] for c in g1.call_args_list], ["老磅秤"],
                             "道具走 _single")
            self.assertEqual(made.get("noimg"), 0)


class TestCastCounts(unittest.TestCase):
    """「本镜出场角色数」—— 人物数量声明的来源（2026-09-15）。

    两个实测事故（都在 village-tractor 上暴露）：
      · `_chars_by_name` 的默认上限 **2**（本是**参考图绑定**的成本约束）被
        `hits_for_shot` 当成"本镜有谁"的判据 → **三人物镜永远只认出 2 个**：
        26 镜里 阿凯 几乎全程缺席 → 身份锚点漏人 + 人数少 1
        → 提示词对 3 人镜说"画面中只有一个人物"；
      · 分镜常用「三人」而**不点姓名**（LN24「三人喘着气站着」）→ 只按姓名认到
        主角 1 人 → 同上冲突 → 模型画出三个陌生人。
    """

    REG = {"assets": [
        {"id": "老周", "name": "老周", "type": "character", "keywords": ["老周"],
         "priority": 10, "identity": "老周的固定形象：瘦长脸、黑色短寸发、深色外套白色高领毛衣。"},
        {"id": "小林", "name": "小林", "type": "character", "keywords": ["小林"],
         "priority": 10, "identity": "小林的固定形象：圆脸、黑色抓绒立领夹克内衬白 T 恤。"},
        {"id": "阿凯", "name": "阿凯", "type": "character", "keywords": ["阿凯"],
         "priority": 10, "identity": "阿凯的固定形象：方下颌、黑色圆领 T 恤。"},
        {"id": "拖拉机", "name": "拖拉机", "type": "prop", "keywords": ["拖拉机"],
         "priority": 8},
    ]}

    def _root(self):
        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        (root / "assets.json").write_text(
            json.dumps(self.REG, ensure_ascii=False), encoding="utf-8")
        return d, root

    def test_three_characters_all_counted_and_anchored(self):
        """★ 上限不能再是 2：三人物镜必须算出 3，且锚点里**三个都在**。"""
        from v5.media import assets as assets_mod

        d, root = self._root()
        with d:
            shots = [{"name": "LN17",
                      "visual": "老周坐驾驶座，小林与阿凯在车托里，化肥袋与水缸挤压。",
                      "dialogue": ""}]
            self.assertEqual(assets_mod.cast_counts(root, shots)["LN17"], 3)
            anchor = assets_mod.identity_lines(root, shots)["LN17"]
            for who in ("老周", "小林", "阿凯"):
                self.assertIn(who, anchor, "身份锚点漏了 %s" % who)

    def test_numeral_fallback_when_no_name_given(self):
        """正文只写「三人」不点姓名 → 量词兜底算出 3（实测 LN24 的病）。"""
        from v5.media import assets as assets_mod

        d, root = self._root()
        with d:
            shots = [{"name": "LN24", "visual": "三人喘着气站着，没有说话。",
                      "dialogue": ""}]
            self.assertEqual(assets_mod.cast_counts(root, shots)["LN24"], 3)

    def test_single_character_shot(self):
        from v5.media import assets as assets_mod

        d, root = self._root()
        with d:
            shots = [{"name": "LN21",
                      "visual": "老周在驾驶座没反应过来，手还扶着方向盘。",
                      "dialogue": ""}]
            self.assertEqual(assets_mod.cast_counts(root, shots)["LN21"], 1)

    def test_numeral_never_lowers_name_count(self):
        """量词兜底取 `max`，只会抬到事实值，不会把 3 压回 3 以下。"""
        from v5.media import assets as assets_mod

        d, root = self._root()
        with d:
            shots = [{"name": "LN01",
                      "visual": "老周、小林、阿凯三人站在拖拉机前。", "dialogue": ""}]
            self.assertEqual(assets_mod.cast_counts(root, shots)["LN01"], 3)

    def test_cast_numeral_parses_common_forms(self):
        from v5.media import assets as assets_mod

        self.assertEqual(assets_mod.cast_numeral("两个人站在门口"), 2)
        self.assertEqual(assets_mod.cast_numeral("四个人抬着水缸"), 4)
        self.assertEqual(assets_mod.cast_numeral("3个人挤在车斗里"), 3)
        self.assertEqual(assets_mod.cast_numeral("老周一个人蹲着"), 1)
        self.assertEqual(assets_mod.cast_numeral("画面里没有人"), 0,
                         "「没有人」前面没有数词，不该被读成 1")
        self.assertEqual(assets_mod.cast_numeral("风吹过空荡的土路"), 0)


class TestEnsureKeepsAllLocations(unittest.TestCase):
    """`max_assets` 是**生图预算**，不是"资产条数上限"（2026-09-15 实测事故）。

    事故（village-tree）：`for a in items[:max_assets]`（`max_assets=12`）对**整个**
    清单切片，而 `items` 里混着**永不需要生图的 location** → 15 条（9 道具 + 6 场景）
    被切成 12 条 → **末尾 3 个场景被静默丢弃**（日志只报"场景（仅登记）3"，零告警）。
    后果：`assets.json` 缺 3 个 location → `scene_lines` 只能靠"画面描述兜底"把锚点
    猜回来；**一旦兜底失效，那 3 个场景立刻失去锚点**（场景逐镜漂移）且无人报错。
    → 与 `_chars_by_name(max_n=2)` **是同一类错误**：把「配额上限」当成「数据上限」。

    ★ 测"不许生图"必须**同时 patch `_single` 与 `_turnaround`**（只 patch 一个，
      另一个会真去调生图 API —— 全量测试从 4 秒变 48 秒）。
    """

    def _md(self, n_props: int, n_locs: int) -> str:
        out = ["## 道具/设施卡"]
        for i in range(1, n_props + 1):
            out.append("### 道具%d（prop）" % i)
            out.append("- 外形提示词（出图 prompt）：")
            out.append("> 粗糙几何块道具%d，平涂纯色块，硬边无平滑，低清重复贴图，朴素单光源。" % i)
            out.append("")
        out.append("## 场景参考")
        for i in range(1, n_locs + 1):
            out.append("### 场景%d（location）" % i)
            out.append("- 外形提示词（出图 prompt）：")
            out.append("> 场景%d 的布局：地面平涂色块、两三个粗糙几何块农舍、远处一片田野。"
                       % i)
            out.append("**光源**：单一硬方向光，硬低分辨率阴影。")
            out.append("**色温**：暖色，天空平涂灰白。")
            out.append("**陈设**：场地中央一张粗糙几何块木桌。")
            out.append("")
        return "\n".join(out)

    def _root(self, md: str, pack: str = "niulai-movie-style"):
        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        (root / "assetdesigner").mkdir(parents=True)
        (root / "assetdesigner" / "assets.md").write_text(md, encoding="utf-8")
        (root / "brief.json").write_text(
            json.dumps({"pack": pack}, ensure_ascii=False), encoding="utf-8")
        return d, root

    def _ensure(self, root):
        from unittest import mock

        def _boom(*a, **k):
            raise AssertionError("still-refs=false 时不该生图：%r" % (a[:2],))

        with mock.patch.object(cast, "_single", side_effect=_boom), \
                mock.patch.object(cast, "_turnaround", side_effect=_boom):
            return cast.ensure(root, log=lambda *_: None)

    def test_all_locations_registered_despite_budget(self):
        """★ 9 道具 + 6 场景 = 15 条 > 预算 12 → **6 个场景仍须全部登记**。"""
        from v5.media import assets as assets_mod

        d, root = self._root(self._md(9, 6))
        with d:
            made = self._ensure(root)
            reg = {a["name"]: a for a in assets_mod.load_registry(root)["assets"]}
            locs = sorted(n for n, a in reg.items() if a.get("type") == "location")
            self.assertEqual(len(locs), 6, "场景被 max_assets 截断了：%s" % locs)
            self.assertEqual(made["scenes"], 6)
            self.assertEqual(made["noimg"], 9, "9 个道具仍走不生图登记")

    def test_budget_still_caps_image_assets(self):
        """回归：预算对**真正会生图的那部分**依然生效（15 道具 → 只登记 12）。"""
        from v5.media import assets as assets_mod

        d, root = self._root(self._md(15, 2))
        with d:
            self._ensure(root)
            reg = {a["name"]: a for a in assets_mod.load_registry(root)["assets"]}
            props = [n for n, a in reg.items() if a.get("type") == "prop"]
            locs = [n for n, a in reg.items() if a.get("type") == "location"]
            self.assertEqual(len(props), 12, "生图预算该截断道具")
            self.assertEqual(len(locs), 2, "场景不受生图预算影响")


class TestSourcePhotoPlumbing(unittest.TestCase):
    """用户提供的源照片必须真的接进 `_turnaround(source=...)`（2026-09-15 实测缺口）。

    三处证据，缺一就是"用户给了照片却没用上"：
      ① `roles.TOOL_NOTE` 明写 generate_image / generate_turnaround /
         ingest_reference 等**本系统未提供** → 角色**无法**自己把照片注册进注册表；
      ② 那句"用 `ingest_reference` 保持该脸"**只存在于死文件** `worldbuilder.md`
         （`_role_skill` 从不读平铺 `.md`）；**live 契约只字未提用户照片**；
      ③ `cast.ensure` 原先**硬传 `source=""`** → `_turnaround` 的 img2img 通路全程关着。
    用户说"用我给你的人物做主角"时，照片因此**静默不生效**。
    """

    CARD = ("# 角色卡：老周\n\n## 基本信息\n- 姓名：老周\n- 年龄：52\n\n"
            "## 外貌特征（用于生图）\n瘦长脸、浓黑眉、黑色短寸发、深色外套里衬白色高领毛衣。\n")

    def _root(self, with_src: bool):
        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        (root / "worldbuilder").mkdir(parents=True)
        (root / "worldbuilder" / "worldbuilder.md").write_text(self.CARD, encoding="utf-8")
        (root / "brief.json").write_text(
            json.dumps({"pack": "shortdrama"}, ensure_ascii=False), encoding="utf-8")
        if with_src:
            (root / "images").mkdir(parents=True, exist_ok=True)
            (root / "images" / "老周.source.jpg").write_bytes(b"src")
        return d, root

    def test_source_photo_lookup(self):
        d, root = self._root(True)
        with d:
            self.assertTrue(cast._source_photo(root, "老周").endswith("老周.source.jpg"))
            self.assertEqual(cast._source_photo(root, "小林"), "", "没有就返回空串")

    def test_source_ref_uri_is_data_uri(self):
        """★ 传给接口的必须是 **data URI**，不是本地路径（否则 400 Bad Request）。"""
        from PIL import Image

        d, root = self._root(True)
        with d:
            Image.new("RGB", (64, 64), (10, 20, 30)).save(
                root / "images" / "老周.source.jpg", "JPEG")
            uri = cast._source_ref_uri(root, "老周")
            self.assertTrue(uri.startswith("data:image/jpeg;base64,"), uri[:60])
            self.assertEqual(cast._source_ref_uri(root, "小林"), "",
                             "没有源照片就该是空串（→ 纯文生图，与历史一致）")

    def _ensure_capture(self, root) -> dict:
        from unittest import mock

        seen: dict = {}

        def fake_turnaround(r, c, ratio, log=print, source=""):
            seen[c["name"]] = source
            return ""      # 返回空 → 走"降级登记"分支，测试不碰网络

        with mock.patch.object(cast, "_turnaround", side_effect=fake_turnaround), \
                mock.patch.object(cast, "_single",
                                  side_effect=AssertionError("本用例不该生资产图")):
            cast.ensure(root, log=lambda *_: None)
        return seen

    def test_turnaround_receives_data_uri(self):
        """**四视图模式**下，源照片也必须编成 data URI 再传（默认是直绑，故显式开开关）。"""
        from unittest import mock

        from PIL import Image

        from v5 import config

        d, root = self._root(True)
        with d:
            Image.new("RGB", (64, 64), (9, 9, 9)).save(
                root / "images" / "老周.source.jpg", "JPEG")
            with mock.patch.object(config, "SOURCE_TURNAROUND", True):
                seen = self._ensure_capture(root)
            self.assertTrue(str(seen.get("老周", "")).startswith("data:image/jpeg;base64,"),
                            "有源照片时必须编成 data URI 再传：%r"
                            % str(seen.get("老周"))[:70])

    def test_turnaround_source_empty_without_photo(self):
        d, root = self._root(False)
        with d:
            seen = self._ensure_capture(root)
            self.assertEqual(seen.get("老周"), "",
                             "没有源照片时行为必须与历史**一字不变**")

    def test_source_photo_direct_bind_by_default(self):
        """★★ 默认**直绑**：源照片本身成为 `<名>.png`，不走"重画四视图"那一跳。

        用户反馈「为什么样子都变了，不是和我给的照片完全一样的」→ 根因是
        **照片从头到尾没被复制过，它被重画了两次**（照片→四视图→静帧）。
        直绑把第一跳去掉，只剩"照片→静帧"。
        """
        from unittest import mock

        from PIL import Image

        from v5.media import assets as assets_mod

        d, root = self._root(True)
        with d:
            Image.new("RGB", (64, 64), (200, 30, 30)).save(
                root / "images" / "老周.source.jpg", "JPEG")
            with mock.patch.object(cast, "_turnaround",
                                   side_effect=AssertionError("直绑模式下不该生成四视图")), \
                    mock.patch.object(cast, "_single",
                                      side_effect=AssertionError("不该生资产图")):
                made = cast.ensure(root, log=lambda *_: None)
            reg = {a["name"]: a for a in assets_mod.load_registry(root)["assets"]}
            self.assertEqual(reg["老周"].get("ref_image"), "老周.png")
            self.assertTrue((root / "images" / "老周.png").exists(),
                            "源照片要被落成 <名>.png 供 bind() 绑定")
            self.assertEqual(made["characters"], 1)

    def test_turnaround_mode_still_available(self):
        """`SHORTDRAMA_SOURCE_TURNAROUND=1` → 回到四视图路径（**可回退**，不是死路）。"""
        from unittest import mock

        from PIL import Image

        from v5 import config

        d, root = self._root(True)
        with d:
            Image.new("RGB", (64, 64), (1, 2, 3)).save(
                root / "images" / "老周.source.jpg", "JPEG")
            seen = {}
            with mock.patch.object(config, "SOURCE_TURNAROUND", True), \
                    mock.patch.object(cast, "_turnaround",
                                      side_effect=lambda r, c, ratio, log=print, source="":
                                      seen.setdefault("src", source) or ""), \
                    mock.patch.object(cast, "_single",
                                      side_effect=AssertionError("不该生资产图")):
                cast.ensure(root, log=lambda *_: None)
            self.assertTrue(str(seen.get("src", "")).startswith("data:image/jpeg;base64,"),
                            "四视图模式下源照片仍要以 data URI 传入")

    def test_turnaround_failure_still_registers_identity(self):
        """★★ 生图失败必须**降级登记**（图没有、描述留着）。

        事故（village-bees）：12 次生成全 400 失败 → 原实现只在成功时 `_register`
        → 注册表里**一个角色都没有** → `identity_lines` 返回空 → **参考图与文字锚点
        双重失效** → 成片人物只是"像那么回事"，不是用户给的那个人。
        """
        from v5.media import assets as assets_mod

        d, root = self._root(False)
        with d:
            self._ensure_capture(root)          # fake_turnaround 恒返回 "" = 生成失败
            reg = {a["name"]: a for a in assets_mod.load_registry(root)["assets"]}
            self.assertIn("老周", reg, "生图失败也必须登记角色")
            self.assertIn("瘦长脸", reg["老周"].get("identity") or "",
                          "降级登记要带上文字身份锚点")
            self.assertEqual(reg["老周"].get("ref_image") or "", "",
                             "没图就别写 ref_image")

    def test_auto_sync_skips_source_photos(self):
        """源照片是**输入**、不是资产 —— 不能被 `auto_sync` 收编成伪角色。

        事故（village-bees）：`images/老周.source.jpg` 被收编 → 注册表冒出
        `老周.source` 这个伪 character（关键词 "老周.source" 不匹配任何镜）。
        """
        from unittest import mock

        from v5.media import assets as assets_mod

        d, root = self._root(True)
        with d:
            with mock.patch.object(cast, "_turnaround", return_value=""), \
                    mock.patch.object(cast, "_single", side_effect=AssertionError("不该生图")):
                cast.ensure(root, log=lambda *_: None)
            assets_mod.auto_sync(root)
            names = {a["name"] for a in assets_mod.load_registry(root)["assets"]}
            self.assertNotIn("老周.source", names, "源照片不该变成资产条目")
            self.assertIn("老周", names)


class TestCastSkipsLocationImages(unittest.TestCase):
    """★ 场景（`location`）**只登记、不生图**（2026-09-14）。

    两条依据都是**既定事实**，不是我新加的判断：
      · `bind()` **从不绑定 location** —— 场景图是**空镜内景**、自带机位，
        喂进"近景人物特写"这类镜会把构图拉回大 Wide，与分镜景别打架；
      · `validate_assets` **专门豁免** location 的"图在盘"检查。
    → 为它生图 = **每个项目白烧 N 次生图配额**（实测 paper-crane：4 个 location）。

    场景的作用改由**文本锚点**承担（`assets.scene_lines`）—— 所以注册表里
    **必须带上描述**（原先只有 `ref_image`、没有描述字段，导致锚点命中 0/49）。
    """

    CONTRACT = json.dumps({"characters": [], "assets": [
        {"name": "洗衣店内部", "type": "location", "keywords": ["洗衣店"],
         "prompt": "自助洗衣店内景空镜头，冷白荧光灯偏青冷色调，成排筒灯"},
        {"name": "旧钥匙", "type": "prop", "keywords": ["钥匙"],
         "prompt": "黄铜色扁平门钥匙，齿纹磨损"},
    ]}, ensure_ascii=False)

    def _root(self):
        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        (root / "assetdesigner").mkdir(parents=True)
        (root / "assetdesigner" / "assets.contract.json").write_text(
            self.CONTRACT, encoding="utf-8")
        return d, root

    @staticmethod
    def _fake_single(dest_writer):
        """假生图：**在盘上落一个文件**再返回 True。

        为什么要落盘：`_ref_is_current` 的判据是「图在盘 **且** ref_ver 达标」——
        只返回 True 不写文件的话，第二次运行必然重新生成，幂等用例就测不出东西。
        """
        def _fn(root, name, prompt, ratio, log=print):
            cast.images_dir(root).mkdir(parents=True, exist_ok=True)
            (cast.images_dir(root) / (name + ".png")).write_bytes(b"PNG")
            return True
        return _fn

    def test_location_registered_but_never_generated(self):
        from unittest import mock

        from v5.media import assets as assets_mod

        d, root = self._root()
        with d:                                  # ★ 断言必须在块内：块一结束临时目录就没了
            with mock.patch.object(cast, "_single",
                                   side_effect=self._fake_single(None)) as gen:
                made = cast.ensure(root, log=lambda *_: None)
            self.assertEqual([c.args[1] for c in gen.call_args_list], ["旧钥匙"],
                             "location 一次都不该调生图")
            self.assertEqual(made.get("scenes"), 1)
            reg = {a["name"]: a for a in assets_mod.load_registry(root)["assets"]}
            self.assertIn("洗衣店内部", reg, "仍要登记（scene_lines 的关键词来源）")
            self.assertEqual(reg["洗衣店内部"]["ref_image"], "",
                             "没有图就别写 ref_image")
            self.assertIn("冷白荧光灯", reg["洗衣店内部"]["prompt"],
                          "描述必须进注册表（锚点就靠它）")

    def test_scene_anchor_works_from_registry_after_cast(self):
        """跨模块闭环：`cast` 登记 → `scene_lines` 取到描述。

        这条链以前断在中间（注册表没有描述字段）→ 锚点命中 0/49。
        """
        from unittest import mock

        from v5.media import assets as assets_mod

        d, root = self._root()
        with d, mock.patch.object(cast, "_single",
                                  side_effect=self._fake_single(None)):
            cast.ensure(root, log=lambda *_: None)
            out = assets_mod.scene_lines(root, [{"name": "LN01", "scene": "洗衣店内部"}])
            self.assertIn("冷白荧光灯", out.get("LN01", ""))

    def test_second_run_is_idempotent(self):
        from unittest import mock

        d, root = self._root()
        with d, mock.patch.object(cast, "_single",
                                  side_effect=self._fake_single(None)) as gen:
            cast.ensure(root, log=lambda *_: None)
            first = gen.call_count
            made2 = cast.ensure(root, log=lambda *_: None)
            self.assertEqual(gen.call_count, first, "第二次不该重复生图")
            self.assertEqual(made2.get("scenes"), 0, "场景已在注册表 → 跳过")


class TestContractCharsKeywords(unittest.TestCase):
    """contract 的 `characters` 缺 `keywords` 时必须补 name 自身（2026-09-13 事故）。

    实测（rainy-door）：`_load_contract` 只给 `items` 补 keywords、**`chars` 漏了**
    → `_register` 取 `item["keywords"]` 抛 `KeyError`
    → `cast.ensure` 整体崩（异常被 pipeline 兜成"退化为无参考图"）
    → 周奶奶 + 全部道具**没有参考图** → 静帧 QC 两轮判硬伤
    → 视频 5/6 镜判"缺道具/动作"、成片带 5 处 residual。

    **这是"不对称保护"导致的漏洞**：同一份契约的两半，一半有兜底、另一半没有。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_contract(self, contract: dict) -> None:
        d = self.root / "assetdesigner"
        d.mkdir(parents=True, exist_ok=True)
        (d / "assets.contract.json").write_text(
            __import__("json").dumps(contract, ensure_ascii=False), encoding="utf-8")

    def test_chars_get_keywords_when_missing(self):
        self._write_contract({
            "characters": [{"name": "周奶奶", "appearance": "外貌" * 20}],
            "assets": [{"name": "旧黑伞", "type": "prop", "prompt": "描述" * 20}],
        })
        chars, items = cast._load_contract(self.root)
        self.assertEqual(chars[0]["keywords"], ["周奶奶"],
                         "characters 缺 keywords 必须补 name 自身（否则 _register 抛 KeyError）")
        self.assertEqual(items[0]["keywords"], ["旧黑伞"])

    def test_existing_keywords_are_not_overwritten(self):
        self._write_contract({
            "characters": [{"name": "陈叙", "appearance": "外貌" * 20,
                            "keywords": ["陈叙", "主角"]}],
            "assets": [],
        })
        chars, _ = cast._load_contract(self.root)
        self.assertEqual(chars[0]["keywords"], ["陈叙", "主角"],
                         "已配好的别名不能被兜底覆盖")

    def test_register_survives_missing_keywords(self):
        """`_register` 缺 keywords 时用 name 兜底 —— 不能抛。

        抛了的代价不是"少一条记录"，而是**整个 cast 静默退化**：
        上游只看到"资产生成异常"，之后所有镜都没有参考图。
        """
        cast._register(self.root, {"name": "某资产", "type": "prop"}, "某资产")
        reg = __import__("json").loads(
            (self.root / "assets.json").read_text(encoding="utf-8"))
        rec = reg["assets"][0]
        self.assertEqual(rec["keywords"], ["某资产"])
        self.assertEqual(rec["type"], "prop")


if __name__ == "__main__":
    unittest.main()
