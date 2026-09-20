# -*- coding: utf-8 -*-
"""v5 核心逻辑自测（纯逻辑，不调 API）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v5.media import prompt, relations, scaffold, storyboard  # noqa: E402


def _acts():
    return [
        {"name": "纸扎铺-日", "shots": [{"seconds": 8}, {"seconds": 6}, {"seconds": 7}]},
        {"name": "纸扎铺-夜", "shots": [{"seconds": 5}, {"seconds": 6}]},
        {"name": "烧纸空地-夜", "shots": [{"seconds": 8}, {"seconds": 6}]},
    ]


class TestScaffoldParseRoundtrip(unittest.TestCase):
    def test_roundtrip(self):
        md = scaffold.build("借脸", _acts(), "写实电影感恐怖")
        shots = storyboard.parse(md)
        self.assertEqual(len(shots), 7)
        self.assertEqual(shots[0]["name"], "LN01")     # 不是 LN00
        self.assertEqual(shots[0]["seconds"], 8)
        # 反烧字不再写进正文（违反「正面描述」规范），改由组装器统一尾缀
        self.assertNotIn("不得出现任何可读文字", shots[0]["visual"])
        self.assertEqual(shots[0]["visual_style"], "写实电影感恐怖")
        self.assertEqual(shots[0]["text_shot"], "否")

    def test_placeholder_not_mistaken_for_header(self):
        """占位符含「对白/音效」等表头词，解析仍要认出数据行。"""
        md = scaffold.build("t", _acts())
        self.assertEqual(len(storyboard.parse(md)), 7)


class TestShotIdFormats(unittest.TestCase):
    """镜头号格式兼容（2026-09-10 实测缺口）。

    事故：maskparade 的分镜师按小节标题 `## S10 / 6s` 的写法，把镜头号列
    也写成 `| S10 |`。旧 _ROW_RE 只认裸数字与 `1-1` → **静默解析出 0 镜**，
    media 直接 failed，整轮创作链白跑（token 已烧 54 万）。

    分镜师写 `S10` 是合理的人类可读格式（与小节标题一致），解析器不该只认
    一种写法。这里锁定三种已见格式都必须解析成功。
    """

    HDR = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |\n"
           "|--------|------|------|------|----------|----------|------|------|\n")

    def _md(self, ids: list[str]) -> str:
        rows = "".join(
            "| %s | 中景 | 平视 | 固定 | 6 | 这是一段足够长的画面描述文本用 | （无声） | 环境声 |\n"
            % i for i in ids)
        return self.HDR + rows

    def test_bare_number_ids(self):
        """格式一：裸数字 `| 1 |`（脚手架默认）。"""
        shots = storyboard.parse(self._md(["1", "2", "3"]))
        self.assertEqual([s["index"] for s in shots], [1, 2, 3])

    def test_act_prefixed_ids(self):
        """格式二：集-镜 `| 1-1 |`（nightshift-45 在用）。"""
        shots = storyboard.parse(self._md(["1-1", "1-2", "2-1"]))
        self.assertEqual(len(shots), 3)
        self.assertEqual(shots[0]["index"], 1)
        self.assertEqual(
            [s["name"] for s in shots], ["LN01", "LN02", "LN03"])

    def test_s_prefixed_ids(self):
        """格式三：`| S10 |`（maskparade 实际产出，此前静默丢镜）。"""
        shots = storyboard.parse(self._md(["S1", "S2", "S10"]))
        self.assertEqual([s["index"] for s in shots], [1, 2, 10])
        self.assertEqual(len(shots), 3)

    def test_rows_without_leading_pipe(self):
        """★★ 行首漏写 `|` 的表格也要认（2026-09-14 实测事故）。

        模型写 pipe table 时会偶发漏掉行首的 `|`，真实产物（village-scale 第 3 轮）：
            `镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效`
            `01 | 近景 | 平视 | 固定 | 6 | 原始低成本三维重建的…`
        而 `parse` 要求 `line.startswith("|")` → **26 镜全丢**；同一个洞也让
        `validate.check_storyboard` 把**所有列**判成"缺列"（诊断还是错的：列都在，
        破的是表格语法）→ `[STORYBOARD-REJECT]` → 整条媒体链起不来。
        这与「reviewer 漏写代码围栏」同一种病：解析器只认一种写法。

        以下用**真实产物的前两行原文**做回归。
        """
        hdr = "镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效\n"
        rows = ("01 | 近景 | 平视 | 固定 | 6 | 原始低成本三维重建的深夜收粮站内部场景 | 磅秤声：「少……」 | 铁针咔响\n"
                "02 | 中景 | 稍俯 | 手持感缓推 | 6 | 镜头进入收粮站锁定马德胜冲进门举手电 | （无声，环境音） | 脚步\n")
        shots = storyboard.parse("## S01 / 6s\n" + hdr + rows)
        self.assertEqual([s["index"] for s in shots], [1, 2], "行首缺 | 也要认")
        self.assertEqual([s["name"] for s in shots], ["LN01", "LN02"])
        self.assertEqual(sum(s["seconds"] for s in shots), 12)
        self.assertEqual(shots[0]["shot_type"], "近景")
        self.assertIn("少", shots[0]["dialogue"])

    def test_plain_prose_with_one_pipe_is_not_a_row(self):
        """反例：正文里偶发的单个 `|` 不该被当成表格行。"""
        shots = storyboard.parse("这是一句正文，里面有个竖线 | 仅此而已，不是表格。\n")
        self.assertEqual(shots, [])


class TestNoSplitIsPositiveOnly(unittest.TestCase):
    """反分屏声明必须是**纯正向**（2026-09-10 maskparade 事故）。

    事故：18 镜里 14 镜被 QC 判「上下两格分屏」，重画 2 轮后仍 12 镜。
    旧 NOSPLIT 写的是「单一连续画面…**不要分屏、不要上下两格、不要拼接、
    不要拼图、不要多视角排列**」——后半句全是负面提法。

    这与本项目已反复验证的铁律冲突：**负面提法会在提示词里引入该概念，
    模型因此把它画出来**（同"越压越烧字"）。所以这里锁定：
    提示词里不得出现任何分屏类概念词。
    """

    CONCEPT_WORDS = ("分屏", "两格", "拼接", "拼图", "多视角", "上下两")

    def test_nosplit_has_no_concept_words(self):
        for w in self.CONCEPT_WORDS:
            self.assertNotIn(w, prompt.NOSPLIT, "NOSPLIT 不得含概念词 %s" % w)

    def test_still_prompt_free_of_split_concepts(self):
        """整条静帧提示词里都不能有分屏概念词。"""
        shot = {"name": "LN01", "visual": "老周坐在驾驶座上，方块头转向后视镜。",
                "shot_type": "中景", "angle": "平拍", "camera": "固定",
                "visual_style": "", "scene": "", "tail": "", "dialogue": "（无声）",
                "sfx": "", "text_shot": "", "join_note": ""}
        p = prompt.build_still_prompt(shot)
        for w in self.CONCEPT_WORDS:
            self.assertNotIn(w, p)

    def test_video_prompt_has_positive_nosplit(self):
        """视频提示词也要带反分屏（2026-09-10 实测：静帧修好后视频又分屏）。

        clipqc 判 LN03/LN04/LN05/LN06 全为"水平切成上下两格"，而静帧是干净的
        ——视频提示词此前完全没有反分屏约束。
        """
        shot = {"name": "LN01", "visual": "老周坐在驾驶座上，方块头转向后视镜。",
                "shot_type": "中景", "angle": "平拍", "camera": "固定",
                "visual_style": "", "scene": "", "tail": "", "dialogue": "（无声）",
                "sfx": "", "text_shot": "", "join_note": ""}
        plan = {"name": "LN01", "frame_plan": {"relation": "cut",
                                               "use_prev_last": False}}
        p = prompt.build_video_prompt(shot, plan)
        self.assertIn("single continuous shot", p)
        for w in self.CONCEPT_WORDS:
            self.assertNotIn(w, p)

    def test_video_nosplit_has_no_concept_words(self):
        for w in self.CONCEPT_WORDS:
            self.assertNotIn(w, prompt.NOSPLIT_VIDEO)


class TestEnglishTimeWords(unittest.TestCase):
    """英文分镜的时间性描述也要改写成状态（2026-09-10 补）。

    类型包的分镜正文可能**整段是英文**（maskparade 实测），而 _TIME_REPL 是
    中文词表，够不到 → "slowly rotating" 这类过程描述原样进提示词 →
    模型把"过程"画成前后两格。这里锁定英文表生效。
    """

    def test_slowly_rotating_becomes_state(self):
        out = prompt.sanitize_text("The driver slowly rotating toward the rear.")
        self.assertNotIn("slowly", out.lower())
        self.assertNotIn("rotating", out.lower())
        self.assertIn("rotated", out.lower())

    def test_chinese_still_works(self):
        out = prompt.sanitize_text("老周缓缓转向后视镜。")
        self.assertNotIn("缓缓", out)


class TestRatioDeclarationStripped(unittest.TestCase):
    """分镜正文自带的比例声明必须清掉（2026-09-10）。

    真实画幅由 API 参数下发。正文里写死一个与之不符的比例（分镜写
    `Ratio: 16:9`、实际静帧 3:4）会让模型自相矛盾，可能用"画两格、
    每格一个比例"来同时满足——这正是分屏的一个诱因。
    """

    def test_ratio_colon_form_removed(self):
        out = prompt.sanitize_text("车厢内景。Ratio: 16:9. 灯光很冷。")
        self.assertNotIn("16:9", out)

    def test_aspect_ratio_form_removed(self):
        out = prompt.sanitize_text("bus interior. Aspect ratio: 9:16. cold light.")
        self.assertNotIn("9:16", out)

    def test_scene_text_survives(self):
        out = prompt.sanitize_text("车厢内景。Ratio: 16:9. 灯光很冷。")
        self.assertIn("灯光很冷", out)


class TestVideoPromptCjkPurge(unittest.TestCase):
    """英文提示词里混入的中文字形必须清除（2026-09-10 maskparade 事故）。

    事故：clipqc 判 LN01「衬衫口袋处有可读中文文字（疑似"老周"）」。查提示词
    发现每镜都带 24-50 个中文字进视频提示词（景别/机位/声音/角色名）。
    对文生视频模型，中文字形只有一种表达方式——烧成屏幕文字。**要它别画字，
    就别给字。**

    两件事必须同时成立：
      ① 英文主体提示词里的中文被清（含"老周"这类角色名）；
      ② **中文主体**提示词的正文原样保留（那是内容，不是污染）。
    """

    def _en_shot(self):
        # 模拟类型包输出的英文分镜 + 中文元数据列漏进来
        return {"name": "LN01",
                "visual": "Rebuild as primitive folk CGI. Lock: single driver in "
                          "bus cabin, white shirt collar as simple triangle block.",
                "shot_type": "中景", "angle": "平视", "camera": "固定",
                "visual_style": "", "scene": "", "tail": "",
                "dialogue": "（无声）", "sfx": "引擎低频持续轰鸣",
                "text_shot": "", "join_note": ""}

    def test_chinese_name_removed_from_english_prompt(self):
        s = self._en_shot()
        s["visual"] += " (老周)"
        p = prompt.build_video_prompt(s)
        self.assertNotIn("老周", p, "角色名中文字形会被烧到画面上")
        self.assertIn("white shirt collar", p)

    def test_camera_translated_not_dropped(self):
        """景别/机位要**译成英文**而不是丢掉——丢了等于丢指令。"""
        p = prompt.build_video_prompt(self._en_shot())
        self.assertNotIn("中景", p)
        self.assertIn("medium shot", p)
        self.assertIn("eye-level camera", p)
        self.assertIn("fixed camera", p)

    def test_ambient_sound_cleared_from_english_prompt(self):
        """英文提示词里的中文环境声要清掉。

        环境声若清空后什么都不剩，连 `Ambient only` 标签一起省掉
        （没有内容可标的空标签是噪音）。
        """
        p = prompt.build_video_prompt(self._en_shot())
        self.assertNotIn("引擎低频", p)
        self.assertNotIn("Ambient only: ", p)   # 不得出现空标签

    def test_chinese_project_body_preserved(self):
        """中文项目：正文是唯一场景信息来源，**不能**清掉。"""
        shot = {"name": "LN01",
                "visual": "写实电影感恐怖。纸扎匠坐在柜台后搓着钱盒。",
                "shot_type": "中景", "angle": "平视", "camera": "固定",
                "visual_style": "", "scene": "", "tail": "",
                "dialogue": "（无声）", "sfx": "纸张摩擦声",
                "text_shot": "", "join_note": ""}
        p = prompt.build_video_prompt(shot)
        self.assertIn("纸扎匠坐在柜台后搓着钱盒", p)
        self.assertIn("中景", p)
        self.assertIn("纸张摩擦声", p, "中文项目的环境声也要保留原文")

    def test_cjk_ratio_discriminates(self):
        self.assertGreater(prompt._cjk_ratio("纸扎匠坐在柜台后搓着钱盒"), 0.9)
        self.assertLess(
            prompt._cjk_ratio("Rebuild as primitive folk CGI. Lock: driver seated."),
            0.05)

    def test_dialogue_keeps_chinese_but_ambient_does_not(self):
        """台词必须保留中文字形（TTS 要念），环境声则清掉——两者策略不同。"""
        s = self._en_shot()
        s["dialogue"] = "老周：终点站了，就你一个。"
        p = prompt.build_video_prompt(s)
        self.assertIn("终点站了", p, "台词清掉就没内容可念了")
        self.assertNotIn("引擎低频", p)

    def test_still_prompt_also_purges_cjk(self):
        """静帧路径同样要清中文字形（2026-09-10）。

        实测 LN05 的 `报站器` 在成图里表现为橙色 LED 屏上的方块字；
        分镜正文是英文，中文只是元数据漏入。
        注意 `NOSPLIT`（中文，压分屏有效）必须**保留**——它在 strip 之后才拼上。
        """
        p = prompt.build_still_prompt(self._en_shot())
        self.assertNotIn("中景", p)
        self.assertNotIn("平视机位", p)
        self.assertIn("medium shot", p)
        # NOSPLIT 是刻意保留的中文正向声明
        self.assertIn(prompt.NOSPLIT, p)


class TestNegativeClausesDropped(unittest.TestCase):
    """负面禁令必须整段删除（2026-09-10 maskparade LN16 事故）。

    分镜写 `DO NOT: smooth mask appearance, clear facial features on mask`
    → 模型**照着把面具画成带完整真人五官的脸**（眼睛/鼻子/嘴唇/牙齿）。

    这是同一条规律的第三次现形：
      ① 写"不要文字"   → 烧字
      ② 写"不要分屏"   → 分屏
      ③ 写"不要清晰五官" → 长出清晰五官
    规律：**负面提法把概念写进提示词，概念出现即被渲染。**
    唯一有效解法是让概念消失，改用正向描述。
    """

    def test_do_not_clause_removed(self):
        out = prompt.sanitize_text(
            "Rebuild as primitive folk CGI. DO NOT: clear facial features on mask.")
        self.assertNotIn("facial features", out)
        self.assertNotIn("DO NOT", out)
        self.assertIn("primitive folk CGI", out, "正向描述必须留下")

    def test_chinese_negative_removed(self):
        out = prompt.sanitize_text("低模块状风格。不要出现清晰五官。表面是纯色块。")
        self.assertNotIn("清晰五官", out)
        self.assertIn("纯色块", out)

    def test_positive_description_survives(self):
        """正向描述不得被误删——它是禁令的替代品。"""
        out = prompt.sanitize_text(
            "primitive folk CGI. The mask surface is a continuous flat colour block.")
        self.assertIn("continuous flat colour block", out)

    def test_bare_not_in_positive_sentence_not_over_dropped(self):
        """`not` 单独出现（非禁令句式）不应触发删除。"""
        out = prompt.sanitize_text("The driver is not in frame, only the empty seat.")
        self.assertIn("empty seat", out)

    def test_lN16_real_prompt_has_no_facial_features(self):
        """用真实分镜的 LN16 正向锁死：提示词里不得出现 facial features。"""
        shot = {"name": "LN16", "shot_type": "特写", "angle": "平视", "camera": "固定",
                "visual": "Rebuild as primitive folk CGI. Lock: close-up of driver's "
                          "block face, a white ceramic mask edge appearing at "
                          "cheek/jaw area. At jaw/cheek area: white multi-faceted "
                          "ceramic mask edge partially visible (flat diffuse, two "
                          "eye grooves, jagged edges). Ratio: 16:9. Text: none. "
                          "DO NOT: smooth mask appearance, clear facial features "
                          "on mask.",
                "visual_style": "", "scene": "", "tail": "",
                "dialogue": "（无声）", "sfx": "", "text_shot": "", "join_note": ""}
        p = prompt.build_still_prompt(shot)
        self.assertNotIn("facial features", p)
        self.assertIn("ceramic mask edge", p, "面具的正向描述必须留下")


class TestAssetBookkeepingDropped(unittest.TestCase):
    """资产复用记账必须整段删除（2026-09-10 maskparade 实测）。

    LN15 分镜写 `orange glowing rectangle (same asset as S5, reused)`
    → 模型把**场景编号 S5 当字面文字**画在 LED 屏上（看起来像 `$5`）。
    LN02 同病（`same asset as S1`）。LN16 写 `same face model`
    → 与"面具长出真人脸"直接相关（提什么长什么）。

    18 镜全量扫描共 8 类形态，逐类锁回归。
    """

    def test_same_asset_as_scene_ref_removed(self):
        t = "orange glowing rectangle (same asset as S5, reused), screen showing"
        out = prompt.drop_asset_bookkeeping(t)
        self.assertNotIn("S5", out)
        self.assertNotIn("same asset", out.lower())
        self.assertIn("orange glowing rectangle", out)   # 正向描述保留

    def test_same_x_model_list_removed_including_face(self):
        """`same mask model, same face model` 必须整串吃光。"""
        t = ("Materials: diffuse_only. Texture: very_low. "
             "Asset reuse: heavy — same mask model, same face model. "
             "Background: sparse dark interior colour blocks.")
        out = prompt.drop_asset_bookkeeping(t)
        self.assertNotIn("face model", out.lower())
        self.assertNotIn("mask model", out.lower())
        self.assertIn("Background:", out, "后续字段不能被吃掉")
        self.assertIn("Materials:", out)

    def test_asset_reuse_section_removed(self):
        t = ("Texture: very_low, obvious tiling. "
             "Asset reuse: `asset_reuse=heavy` — one seat model reused 6+ times "
             "down the aisle, one window frame model repeated. "
             "Background: `background_detail=sparse` — no street details.")
        out = prompt.drop_asset_bookkeeping(t)
        self.assertNotIn("reuse", out.lower())
        self.assertIn("Background:", out)
        self.assertIn("obvious tiling", out)

    def test_bare_reused_paren_removed(self):
        t = "Seats all empty (reused dark blue-green blocks, same asset throughout)."
        out = prompt.drop_asset_bookkeeping(t)
        self.assertNotIn("reused", out.lower())
        self.assertNotIn("same asset", out.lower())

    def test_reuse_verb_phrase_removed(self):
        t = ("`texture_repetition=obvious` on seat fabric "
             "(reuse same dark blue-green block texture across all seats), "
             "UV stretching visible at seat edges.")
        out = prompt.drop_asset_bookkeeping(t)
        self.assertNotIn("reuse", out.lower())
        self.assertIn("UV stretching visible", out)

    def test_see_assets_md_removed(self):
        t = "white ceramic mask edge — see assets.md `白色瓷面具`, placed neatly."
        out = prompt.drop_asset_bookkeeping(t)
        self.assertNotIn("assets.md", out.lower())
        self.assertIn("placed neatly", out)

    def test_orphan_scene_ref_after_paren_strip_removed(self):
        """括号删掉后剩下的 `fabric S1)` / `rectangle S5,` 必须收拾干净。"""
        t = "Texture: very_low, obvious tiling on seat fabric S1). Background: sparse."
        out = prompt.drop_asset_bookkeeping(t)
        self.assertNotIn("S1", out)
        self.assertNotIn(" )", out)
        self.assertIn("seat fabric", out)

    def test_normal_words_containing_s_digit_kept(self):
        """不能误伤 `USB3` / `MP4` 这类正常词里的字符。"""
        t = "Shot on a MP4 body, USB3 port visible on the panel."
        out = prompt.drop_asset_bookkeeping(t)
        self.assertIn("MP4", out)
        self.assertIn("USB3", out)

    def test_sanitize_text_pipeline_clears_all(self):
        """端到端：sanitize_text 也要清掉资产记账（不只是单独函数）。"""
        raw = ("这是一张单幅完整照片。视觉风格：primitive folk CGI。"
               "Materials: diffuse_only. Texture: very_low. "
               "Asset reuse: heavy — same mask model, same face model. "
               "Background: sparse.")
        out = prompt.sanitize_text(raw)
        self.assertNotIn("reuse", out.lower())
        self.assertNotIn("face model", out.lower())


class TestNoDialogueAfterParenStrip(unittest.TestCase):
    """`无（只有…提示音）` 这类占位台词必须判空（2026-09-10 maskparade）。

    实测：LN17/LN18 的台词列写 `无（只有报站器最后一次极轻的提示音）`。
    原实现先判 `_NO_DIALOGUE` 再剥括号——判的时候还没剥，匹配不上；
    剥完剩 `无` 却不再判 → 模型把"无"这个字念出来。
    """

    def test_placeholder_with_parenthetical_is_empty(self):
        self.assertEqual(
            prompt.clean_dialogue("无（只有报站器最后一次极轻的提示音）"), "")

    def test_placeholder_bare_is_empty(self):
        self.assertEqual(prompt.clean_dialogue("无"), "")

    def test_real_line_survives(self):
        self.assertEqual(prompt.clean_dialogue("老周：终点站了。"), "终点站了")


class TestSceneAnchorInPrompt(unittest.TestCase):
    """「场景」列必须进提示词（2026-09-14 补的断线）。

    实测事故（paper-crane）：分镜 41/49 镜的「场景」列写着「洗衣店内部」，而提示词里
    **一个「洗衣」字都没有** —— `build_still_prompt` 的段列表没有场景段，`scene` 唯一
    使用点 `join_line()` 对 cut 镜头又直接返回空。于是模型只能靠逐镜自由写的
    「视觉风格」列定光线，成片中段漂成暖光室内（书架/吊灯/木桌）。

    这组用例锁死**段序**：既有段的相对次序是"官方实例"换来的（见 commit
    `fix(prompt): 段序改回官方真实分镜实例的顺序`），插入场景段**不许打乱它**。
    """

    SHOT = {
        "name": "LN01", "index": 1, "seconds": 8,
        "shot_type": "全景", "angle": "平视", "camera": "缓推",
        "scene": "洗衣店内部",
        "visual_style": "暖橙街灯与冷白筒灯的冷暖对比，电影级写实质感，50mm焦段",
        "visual": "@周平站在@自助烘干机前，手指停在舱门边",
        "dialogue": "（无声）", "sfx": "荧光灯电流声",
        "_scene_line": "场景「洗衣店内部」：冷白荧光灯偏青冷色调，天花板成排筒灯",
    }

    def test_still_prompt_carries_scene_anchor(self):
        p = prompt.build_still_prompt(self.SHOT)
        self.assertIn("冷白荧光灯偏青冷色调", p, "场景描述必须进提示词（它是锚点）")
        self.assertIn("洗衣店内部", p)

    def test_scene_segment_sits_between_camera_and_style(self):
        p = prompt.build_still_prompt(self.SHOT)
        self.assertLess(p.index("平视"), p.index("冷白荧光灯"), "机位 → 场景")
        self.assertLess(p.index("冷白荧光灯"), p.index("暖橙街灯"), "场景 → 视觉风格")
        self.assertLess(p.index("暖橙街灯"), p.index("周平"), "视觉风格 → 画面内容（次序不变）")

    def test_falls_back_to_bare_scene_name(self):
        """注册表取不到描述时，至少把场景名带上（裸名兜底）。"""
        s = {k: v for k, v in self.SHOT.items() if k != "_scene_line"}
        self.assertIn("场景：洗衣店内部", prompt.build_still_prompt(s))

    def test_no_scene_no_segment(self):
        s = {k: v for k, v in self.SHOT.items()
             if k not in ("_scene_line", "scene")}
        self.assertNotIn("场景：", prompt.build_still_prompt(s))

    def test_video_prompt_carries_scene_anchor_too(self):
        p = prompt.build_video_prompt(self.SHOT, mode="reference")
        self.assertIn("冷白荧光灯偏青冷色调", p, "视频提示词也要有场景锚点")


class TestPromptAssembly(unittest.TestCase):
    """六段式组装器：分镜字段 → 生产提示词。"""

    def _shot(self, **kw):
        base = {"name": "LN01", "visual": "写实电影感恐怖。纸扎匠坐在柜台后搓着钱盒。",
                "shot_type": "中景", "angle": "平视", "camera": "固定",
                "visual_style": "", "scene": "", "tail": "", "dialogue": "（无声）",
                "sfx": "纸张摩擦声", "text_shot": "", "join_note": ""}
        base.update(kw)
        return base

    def test_camera_and_style_included(self):
        """景别/角度/运镜必须进提示词——旧实现只用 visual 一列，全丢了。"""
        p = prompt.build_video_prompt(self._shot())
        self.assertIn("中景", p)
        self.assertIn("平视机位", p)
        self.assertIn("固定镜头", p)

    def test_global_tail_appended(self):
        """全局尾缀必须是**英文短指令**，且**按有无台词分路**。

        实测事故 1：中文负面长句会被模型当成可显示内容烧到屏幕上
        （nightshift-45 的 42s 帧烧着「滴格林宇 / l'ryoym hmnell」）。
        英文短句不含中文字形，模型无法把它当字幕渲染。
        实测事故 2（2026-09-11《热牛奶》）：无台词镜也带 "never go silent"，
        模型把它理解成"必须有人声"→ 图生视频无剧本可念，每个无台词镜都
        即兴配音，成片每镜都有重复含混的人声。

        ★★ 2026-09-14 修正（A/B/C 三变体实测，用户逐条听）：当时对无台词镜的处置是
        "**明确禁人声**"（`Ambient only, do not speak:` + `…no speech, no talking…`）——
        **那一招也被证伪了**，反而正是它**诱发**说话：
          A 现状  → 用户实听：念「大家好 我们开始今天的训练」❌
          B 正向  `Sound: <环境声规格>` + **只禁字幕** → 干净环境音 ✅
          C 不写  无音频段 + 只禁字幕 → 干净环境音 ✅
        机制：`do not speak: X` 里 **"speak" 与内容 X 挨在一起**（与 `Audio: <台词>` 同构）
        → 模型当成"要念的内容"；否定尾巴被忽略。
        **⇒ 无台词镜改为"只正向描述环境声、完全不提说话"。**
        """
        p = prompt.build_video_prompt(self._shot())
        self.assertIn("no on-screen text", p)
        # 无台词镜：只用 `Sound:` 正向标签；不得再出现任何"说话"相关的词
        self.assertIn("Sound: ", p, "环境声段要用正向标签")
        self.assertNotIn("do not speak", p, "否定式标签会诱发模型说话")
        self.assertNotIn("no speech", p)
        self.assertNotIn("no talking", p)
        self.assertNotIn("never go silent", p)
        self.assertNotIn("不要字幕", p)
        # 有台词镜：保留 never go silent + 普通话台词声明
        p2 = prompt.build_video_prompt(
            self._shot(dialogue="苏晚（气声）：爸？"))
        self.assertIn("never go silent", p2)
        self.assertIn("spoken dialogue", p2)
        self.assertNotIn("no speech,", p2)

    def test_join_line_for_continuous(self):
        plan = {"frame_plan": {"relation": "continuous"}, "prev_tail_note": "指尖停在盒边"}
        p = prompt.build_video_prompt(self._shot(), plan)
        self.assertIn("承接上一镜", p)
        self.assertIn("指尖停在盒边", p)

    def test_no_join_line_for_cut(self):
        plan = {"frame_plan": {"relation": "cut"}}
        p = prompt.build_video_prompt(self._shot(), plan)
        self.assertNotIn("承接上一镜", p)

    def test_tail_line(self):
        p = prompt.build_video_prompt(self._shot(tail="空钱盒与他垂下的指尖"))
        self.assertIn("镜头最终停在", p)
        self.assertIn("空钱盒", p)

    def test_text_shot_keeps_text(self):
        """文字镜（黑场字幕）不该被反烧字压掉。"""
        s = self._shot(visual="黑场中白色打字机字幕逐字浮现：阴宅清点任务。",
                       text_shot="是")
        p = prompt.build_video_prompt(s)
        self.assertIn("On-screen text only", p)
        self.assertNotIn("no subtitles", p)

    def test_still_prompt_has_no_audio(self):
        """静帧没有声音，不该带 BGM/环境音约束。"""
        p = prompt.build_still_prompt(self._shot())
        self.assertNotIn("BGM", p)
        self.assertNotIn("环境声", p)
        self.assertNotIn("不要背景音乐", p)

    def test_still_prompt_has_no_text_word(self):
        """反烧字：静帧提示词里**不能出现"文字/字符/字幕"**。

        实测教训（2026-09-09）：负面提法（"不得出现任何可读文字"）会在提示词里
        引入文字概念，模型反而据此渲染出带字的招牌——越压越烧。正确解药是
        正向描述，按 pack 选档（见 `prompt.STILL_TAIL_PRESETS`）：
        反质量档说"平涂纯色块"，写实档说"真实连续的材质"。
        此测试锁死这条认知，防止后人把负面约束加回去。
        """
        p = prompt.build_still_prompt(self._shot())
        for bad in ("文字", "字符", "字幕", "字形", "笔画"):
            self.assertNotIn(bad, p)

    def test_still_prompt_drops_text_clause(self):
        """反烧字：分镜原文里的文字概念分句必须被删掉。

        实测事故（2026-09-09 nightshift-45 LN02）：分镜写「边缘有模糊英文与
        数字水印不可读」，`_TEXT_WORDS` 定义了却从未被调用（死代码），该分句
        原样进提示词 → 模型在墙上烧出乱码英文水印。
        锁死：既不能出现「英文/水印/可读」等词，也不能把同句的场景信息一起丢。
        """
        s = self._shot(visual="天花板角落监控屏幕特写，六格黑白监控画面，"
                              "边缘有模糊英文与数字水印不可读。前五格空旷。")
        p = prompt.build_still_prompt(s)
        for bad in ("英文", "水印", "不可读", "可读"):
            self.assertNotIn(bad, p)
        self.assertIn("六格黑白监控画面", p)   # 场景信息必须留下
        self.assertIn("前五格空旷", p)

    def test_text_shot_keeps_text_clause(self):
        """文字镜豁免：故意要可读文字的镜不该被删分句。"""
        s = self._shot(visual="黑场中白色打字机字幕逐字浮现：阴宅清点任务。",
                       text_shot="是")
        p = prompt.build_still_prompt(s)
        self.assertIn("字幕", p)

    def test_still_prompt_has_no_carrier_word(self):
        """反烧字：静帧提示词里不出现招牌类载体名词。

        载体词（招牌/摊位/板子/墙面/纸张）会被模型当成"画面里该有的东西"，
        据此摆出店铺场景并自动补招牌。铁证：LN29（夕阳全景）、LN32（U盘特写）
        分镜原文完全没提店铺，仍因风格块里的载体词而烧字。
        """
        p = prompt.build_still_prompt(self._shot())
        for bad in ("招牌", "摊位", "门面", "布条", "纸张", "贴纸", "海报"):
            self.assertNotIn(bad, p)

    def test_placeholder_not_leaked(self):
        """脚手架占位符不得进提示词。"""
        p = prompt.build_video_prompt(self._shot(visual="【镜1 待填】", tail="待填"))
        self.assertNotIn("待填", p)

    # ─── 静帧尾缀按 pack 分档（2026-09-12 事故）─────────────────────────────

    def test_still_tail_presets_are_distinct(self):
        """写实档**绝不能**含"平涂"，反质量档才含。

        事故（noodle-night，pack=shortdrama）：尾缀只有一档"平涂色块"文本，且
        `global_tail` 参数**全仓无人传值** → 写实短剧的每张静帧同时收到
          「表面渲染：场景里所有表面都是真实材质的自然呈现」（style-block 第 3 段）
          「画面里所有表面都只是平涂纯色块或低分辨率重复贴图」（尾缀）
        两句直接对打，等于逐镜指令模型把真实材质降级成廉价平涂。
        """
        mat = prompt.STILL_TAIL_PRESETS["material"]
        self.assertNotIn("平涂", mat)
        self.assertIn("真实", mat)
        self.assertIn("平涂", prompt.STILL_TAIL_PRESETS["flat"])
        self.assertEqual(prompt.STILL_TAIL_PRESETS["none"], "")

    def test_still_tail_default_is_material(self):
        """未注入 `_still_tail` 时取**安全默认**（material），不是遗留的 flat。

        风险不对称：误用 material 最多少一句防烧字声明（风格块与
        sign_replacements 仍在）；误用 flat 会让写实/3D 画面被要求"表面是
        平涂色块"，直接崩坏审美。未知档位也必须回落 material，不能静默变空。
        """
        self.assertEqual(prompt.still_tail({}), prompt.STILL_TAIL_PRESETS["material"])
        self.assertEqual(prompt.still_tail({"_still_tail": "flat"}),
                         prompt.STILL_TAIL_PRESETS["flat"])
        self.assertEqual(prompt.still_tail({"_still_tail": "none"}), "")
        self.assertEqual(prompt.still_tail({"_still_tail": "??"}),
                         prompt.STILL_TAIL_PRESETS["material"])

    def test_still_prompt_uses_pack_tail(self):
        """端到端：material 档静帧不含"平涂色块"，flat 档含。"""
        s = self._shot()
        mat = prompt.build_still_prompt({**s, "_still_tail": "material"})
        self.assertNotIn("平涂纯色块", mat)
        flat = prompt.build_still_prompt({**s, "_still_tail": "flat"})
        self.assertIn("平涂纯色块", flat)

    def test_negative_clause_prefix_not_orphaned(self):
        """负面禁令整段删除后不得留下孤儿修饰词。

        实测（2026-09-12 noodle-night）：`绝对不要：卡通…柔光。` 只从"不要"
        起删，提示词里于是留下悬空的「绝对」——残句形如
        `…真实光学瑕疵。绝对 表面渲染：场景里所有表面都是真实材质…`。
        锁死：修饰词与被删的禁令一起消失，且**禁令后面的正文必须留下**。
        """
        t = prompt._drop_negative_clauses(
            "画面保留噪点。绝对不要：卡通、插画。表面渲染：真实材质。")
        self.assertNotIn("绝对", t)
        self.assertNotIn("卡通", t)
        self.assertIn("表面渲染", t)     # 后一句是正向正文，不能被连坐删掉
        self.assertIn("画面保留噪点", t)


class TestPersonDirective(unittest.TestCase):
    """人物数量声明：**按人数**分流（2026-09-15 修「有人就注单人」）。

    事故（village-tractor，牛来包 26 镜）：`build_still_prompt` 原先只要
    `_has_person` 为真就追加 SINGLE_PERSON（"画面中只有一个人物"）——
    **分镜明确要 3 个人的镜也收到"只能有 1 个人"**（LN17「老周坐驾驶座，
    小林与阿凯在车托里」），与画面内容要求出现的角色名直接冲突 →
    模型把同一人物**复制满画布**同时满足两边：
      LN17 实测 **9 张脸**（老周×4 + 小林×3）、LN21 多台拖拉机、LN23 人物重影。
    """

    def _shot(self, **kw):
        base = {"name": "LN01", "visual": "老周坐在驾驶座上。", "shot_type": "中景",
                "angle": "平视", "camera": "固定", "visual_style": "", "scene": "",
                "tail": "", "dialogue": "（无声）", "sfx": "", "text_shot": "",
                "join_note": ""}
        base.update(kw)
        return base

    def test_multi_person_shot_is_not_declared_single(self):
        """★ 核心回归：3 人镜的提示词**绝不能**出现「只有一个人物」。"""
        p = prompt.build_still_prompt(
            self._shot(visual="老周坐驾驶座，小林与阿凯在车托里。", _cast_n=3))
        self.assertNotIn("只有一个人物", p)
        self.assertIn("共有 3 个人物", p)
        self.assertIn("每个人物只出现一次", p)

    def test_single_person_shot_stays_single(self):
        p = prompt.build_still_prompt(self._shot(_cast_n=1))
        self.assertIn("只有一个人物", p)
        self.assertNotIn("共有", p)

    def test_empty_scene_shot_declares_empty(self):
        p = prompt.build_still_prompt(
            self._shot(visual="空荡的土路，一只水缸歪在车斗里。", _cast_n=0))
        self.assertIn("空镜", p)
        self.assertNotIn("只有一个人物", p)

    def test_falls_back_to_boolean_when_count_missing(self):
        """`_cast_n` 缺失（老分镜 / 单测）→ 回落旧行为，既有调用方不受影响。"""
        self.assertIn("只有一个人物",
                      prompt.person_directive(self._shot(_names=["老周"])))
        self.assertIn("空镜", prompt.person_directive(
            self._shot(visual="空荡的街道，风吹过。")))


class TestIdentityAnchorTidy(unittest.TestCase):
    """身份锚点的**源头整备**（2026-09-15）。

    实测（village-tractor）：锚点含「（记账本，字迹一律不得画成可辨认字符）」，
    `_drop_negative_clauses` 从「不得」删到下一个「。」→ **连右括号一起吃掉**
    （括号变成 **5 开 4 闭**）；`_drop_text_clauses` 再按「字迹」（在 `_TEXT_WORDS`
    里）删掉半句 → 语义碎裂。而锚点正是**无参考图时的唯一身份来源**。
    """

    ANCHOR = ("小林的固定形象（全片每镜必须完全一致）：圆脸、黑色顺直短发；"
              "身穿黑色抓绒立领夹克，内衬白色圆领 T 恤（胸前为纯色块、无任何字样或 logo）；"
              "怀里常抱硬壳笔记本（记账本，字迹一律不得画成可辨认字符）。 "
              "- 材质：皮肤平涂色块、低清贴图；夹克抓绒质感粗糙；笔记本硬壳朴素单色。")

    def test_negative_clause_removed_and_no_orphan_paren(self):
        out = prompt._tidy_anchor(self.ANCHOR)
        self.assertNotIn("不得", out)
        self.assertEqual(out.count("（"), 0, out)
        self.assertEqual(out.count("）"), 0, out)

    def test_keeps_real_information(self):
        """只摘否定子句 —— **不丢「记账本」这类真信息**。"""
        out = prompt._tidy_anchor(self.ANCHOR)
        self.assertIn("记账本", out)
        self.assertIn("黑色抓绒立领夹克", out)
        self.assertIn("材质上", out, "「- 材质：」小标题要压平成可读措辞")

    def test_identity_line_applies_tidy(self):
        got = prompt.identity_line({"name": "LN01", "_identity_line": self.ANCHOR})
        self.assertNotIn("不得", got)
        self.assertNotIn("（", got)

    def test_prompt_final_has_no_negatives_from_anchor(self):
        """整链终点的兜底：锚点里的否定句不许活到最终提示词。"""
        shot = {"name": "LN01", "visual": "老周站在车头前。", "shot_type": "中景",
                "angle": "平视", "camera": "固定", "visual_style": "", "scene": "",
                "tail": "", "dialogue": "（无声）", "sfx": "", "text_shot": "",
                "join_note": "", "_cast_n": 2, "_identity_line": self.ANCHOR}
        p = prompt.build_still_prompt(shot)
        self.assertNotIn("不得画成", p)


class TestRelations(unittest.TestCase):
    def test_relations_by_act_and_scene(self):
        shots = storyboard.parse(scaffold.build("借脸", _acts()))
        planned = relations.plan_frames(shots)
        rels = [p["frame_plan"]["relation"] for p in planned]
        self.assertEqual(rels, ["cut", "continuous", "continuous",
                                "cut", "continuous", "cut", "continuous"])

    def test_first_frame_source(self):
        shots = storyboard.parse(scaffold.build("借脸", _acts()))
        planned = relations.plan_frames(shots)
        self.assertFalse(planned[0]["frame_plan"]["use_prev_last"])   # 首镜：自己的静帧
        self.assertTrue(planned[1]["frame_plan"]["use_prev_last"])    # 连续：承接上镜
        self.assertFalse(planned[3]["frame_plan"]["use_prev_last"])   # 换幕：切断


class TestShotTypePromptTemplate(unittest.TestCase):
    """景别校验的提示词模板必须可 format（2026-09-09 端到端跑崩的回归锁）。

    模板里嵌了示例 JSON，`{` `}` 必须转义成 `{{` `}}`，否则
    `SHOT_TYPE_PROMPT.format(...)` 会抛 `KeyError: '"ok"'`——静帧全部生成完
    之后才在 QC 环节崩掉，浪费一整轮生图配额。
    """

    def test_template_formats(self):
        from v5.media import qc
        txt = qc.SHOT_TYPE_PROMPT.format(shot_type="全景", angle="平视")
        self.assertIn("全景", txt)
        self.assertIn("平视", txt)
        self.assertIn('{"ok": true', txt)   # 转义后仍渲染出可读的 JSON 示例

    def test_empty_shot_type_does_not_crash(self):
        from v5.media import qc
        txt = qc.SHOT_TYPE_PROMPT.format(shot_type="未写", angle="未写")
        self.assertIn("未写", txt)


if __name__ == "__main__":
    unittest.main()
