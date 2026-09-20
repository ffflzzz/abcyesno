# -*- coding: utf-8 -*-
"""质量控制自测：brief 智能截断 / 完备性 / 忠实度 / 分镜契约。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v5 import validate  # noqa: E402


def _brief(**kw) -> dict:
    base = {
        "topic": "测试片",
        "pack": "shortdrama",
        "genre": "喜剧",
        "episodes": 1,
        "target_duration": "90秒左右，共12镜",
        "protagonist": "陈默，亚洲男性，黑色圆领T恤",
        "must_have": [
            "第一幕(钩子)：陈默在出租屋醒来发现世界是低模的",
            "第二幕(发展)：他在夜市地摊接过一支旧U盘",
            "第三幕(转折)：世界突然更新，房屋悬浮穿模",
            "第四幕(收尾)：他举着旧U盘对镜头僵硬大笑",
        ],
        "key_props": ["旧U盘：磨损金属壳，USB口歪斜"],
        "禁忌": ["画面无可读文字", "无第二张清晰人脸"],
        "tone": "低画质喜剧 + bug 元幽默",
        "结局": "陈默举U盘定格",
    }
    base.update(kw)
    return base


class TestPackBrief(unittest.TestCase):
    """智能截断：替代 json.dumps(brief)[:3000] 的裸截断。"""

    def test_small_brief_kept_intact(self):
        b = _brief()
        text, dropped = validate.pack_brief(b)
        self.assertEqual(dropped, [])
        self.assertEqual(set(eval_safe(text)), set(b))     # 字段一个没丢

    def test_output_is_valid_json(self):
        """旧实现会截出半截 JSON；新实现输出必须始终可解析。"""
        b = _brief(topic="X" * 4000)          # 低价值字段超长
        text, dropped = validate.pack_brief(b)
        self.assertIn("topic", dropped)
        self.assertIsInstance(json_loads(text), dict)

    def test_low_value_dropped_before_high_value(self):
        """预算不够时，先丢 topic/genre 这类低价值字段，保住 must_have。"""
        b = _brief(topic="T" * 1500, genre="G" * 1500)
        text, dropped = validate.pack_brief(b)
        d = json_loads(text)
        self.assertIn("must_have", d)
        self.assertIn("key_props", d)
        self.assertIn("禁忌", d)
        for low in ("topic", "genre"):
            if low in dropped:
                self.assertNotIn(low, d)

    def test_core_field_overflow_raises(self):
        """核心字段放不下 → 报错，绝不静默丢失。"""
        b = _brief(must_have=["事件描述" * 200] * 4)      # must_have 巨长
        with self.assertRaises(validate.BriefTooLarge) as ctx:
            validate.pack_brief(b)
        self.assertIn("must_have", ctx.exception.lost_core)

    def test_error_message_is_actionable(self):
        b = _brief(must_have=["X" * 5000])
        try:
            validate.pack_brief(b)
        except validate.BriefTooLarge as e:
            self.assertIn("精简", str(e))
            self.assertTrue(e.needed > e.budget)
        else:
            self.fail("应当抛出 BriefTooLarge")

    def test_non_dict_rejected(self):
        with self.assertRaises(TypeError):
            validate.pack_brief("not a dict")


class TestValidateBrief(unittest.TestCase):
    def test_complete_brief_passes(self):
        r = validate.validate_brief(_brief())
        self.assertTrue(r["ok"], r["problems"])
        self.assertEqual(r["missing_fields"], [])

    def test_missing_required_detected(self):
        b = _brief()
        del b["protagonist"]
        del b["禁忌"]
        r = validate.validate_brief(b)
        self.assertFalse(r["ok"])
        self.assertIn("protagonist", r["missing_fields"])
        self.assertIn("禁忌", r["missing_fields"])

    def test_thin_must_have_flagged(self):
        """「氛围描述」而非「可拍事件」应被指出。"""
        r = validate.validate_brief(_brief(must_have=["开场", "发展", "结尾"]))
        self.assertFalse(r["ok"])
        self.assertTrue(any("具体可拍" in p for p in r["problems"]))

    def test_too_few_must_have(self):
        r = validate.validate_brief(_brief(must_have=["只有一幕的具体事件描述"]))
        self.assertTrue(any("四幕" in p for p in r["problems"]))

    def test_beat_count_vs_duration_flagged(self):
        """★ 节拍数 vs 目标时长（2026-09-14 实测新增）。

        结论：**分镜规模由 `must_have` 的节拍数驱动，不是由 `target_duration` 驱动**。
        实测对照：节拍 8→330s/49 镜；6→180s/25 镜；6→120s/18 镜；
        **4→180s 只出 21 镜 / 117 秒（65%）→ 被片长门拦下整条媒体链**。
        实测「秒/节拍」稳定 19–33，故节拍数 ≈ 目标秒 ÷ 30。
        """
        b = _brief(target_duration="约 180 秒", must_have=[
            "第一幕：马德胜半夜听见磅秤自己报出少三百斤",
            "第二幕：他与杨小满轮流守夜互相试探对方",
            "第三幕：杨小满承认用旧手机放录音掩盖算错的账",
            "第四幕：天亮时指针自己跳到三百斤两人听见这回对了",
        ])
        r = validate.validate_brief(b)
        self.assertFalse(r["ok"])
        self.assertTrue(any("节拍" in p for p in r["problems"]), r["problems"])

    def test_beat_count_sufficient_not_flagged(self):
        """6 节拍 / 180 秒（lost-and-found 的实测配比，落地 96%）→ 不该报节拍问题。"""
        b = _brief(target_duration="约 180 秒", must_have=[
            "第一幕：陈默在低模出租屋醒来发现世界不对劲",
            "第二幕：他在夜市地摊接过一支旧U盘并付了钱",
            "第三幕：世界突然更新，房屋悬浮还出现了穿模",
            "第四幕：他躲进楼道，发现墙上的门牌变成了乱码",
            "第五幕：他把U盘插进旧电脑，屏幕亮起一行错字",
            "第六幕：他举着旧U盘对镜头僵硬大笑，画面定格",
        ])
        r = validate.validate_brief(b)
        self.assertFalse(any("节拍" in p for p in r["problems"]), r["problems"])


class TestFidelity(unittest.TestCase):
    def test_covered_artifact_passes(self):
        b = _brief(must_have=["第一幕：陈默在低模出租屋醒来发现世界不对劲"])
        art = "陈默在低模出租屋的方块床上醒来，发现这个世界不对劲。"
        self.assertTrue(validate.check_brief_fidelity(art, b)["ok"])

    def test_missing_point_detected(self):
        b = _brief(must_have=["第四幕：陈默举着旧U盘对镜头僵硬大笑"])
        art = "陈默在街上走路，什么都没发生。"
        r = validate.check_brief_fidelity(art, b)
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["coverage_missing"]), 1)

    def test_style_items_skipped(self):
        """视觉/风格类条目不该拿文本产物去比对。"""
        b = _brief(must_have=["全片无字幕、竖屏 9:16"])
        self.assertTrue(validate.check_brief_fidelity("完全无关的内容", b)["ok"])

    def test_empty_brief_is_ok(self):
        self.assertTrue(validate.check_brief_fidelity("任意文本", None)["ok"])

    # ── 2026-09-14 实测回归：同义改写被误判"未覆盖"（真事故，拦下了整条媒体链）──
    #
    # 现场：brief 写「陷进泥坑／招手／跑来推车」，分镜写成「碾进浅泥坑／朝后方挥手／
    # 推车头」—— 事件完全拍到、只是换了词；而旧判据（整串二元组、阈值 0.30）只给到
    # **0.267**，于是 `STORYBOARD-REJECT` 把一部 26 镜 / 183 秒 / 逐镜对上的分镜判成
    # "没拍第 2 幕"，媒体链根本没启动（创作链已白跑 21 分钟）。
    #
    # 根因：旧口径把「的/了/在/和」这类**虚词也算进二元组分母**，真实内容信号被稀释。
    # 修法：改用 `_content_bigrams`（切段 + 去虚词），阈值 0.26
    # （实测双端校准 40 条节拍：本片最低 0.357 / 异片最高 0.172，两端各留 ~0.09 余量）。
    #
    # ★ 两端都要锁：既防**误判失败**（第 1 例），也防**误判成功**（第 2、3 例）。
    BEAT = ("老周自己先开车出村口就陷进泥坑，后轮空转甩泥；他冲后面招手，"
            "小林和阿凯跑来推车，推出来时三个人一身泥")

    def test_paraphrased_coverage_passes(self):
        """同义改写、但事件完全拍到了 → 必须算覆盖（旧判据正是在这里误判）。"""
        art = ("原始低成本三维重建：拖拉机驶出村口，后轮碾进浅泥坑，车身一沉；"
               "后轮空转，泥水向两侧飞溅。老周从驾驶座探身朝后方挥手；"
               "小林推圆钝车头、阿凯抵住车托后板，推出来后三个人一身泥。")
        r = validate.check_brief_fidelity(art, _brief(must_have=[self.BEAT]))
        self.assertTrue(r["ok"], r["coverage_missing"])

    def test_same_cast_different_event_still_missing(self):
        """同一批人物、但事件完全不同 → 必须仍判缺失（防"人名命中就算过"）。"""
        art = ("老周蹲在村口的石墩上抽烟，小林摊开记账本一笔一笔念，"
               "阿凯在旁边打瞌睡，日头很好。")
        r = validate.check_brief_fidelity(art, _brief(must_have=[self.BEAT]))
        self.assertFalse(r["ok"])

    def test_missing_item_reports_score(self):
        """缺项要带实测覆盖率 —— 便于区分"擦线"与"完全没拍"。"""
        art = "老周蹲在村口的石墩上抽烟，小林摊开记账本一笔一笔念。"
        r = validate.check_brief_fidelity(art, _brief(must_have=[self.BEAT]))
        self.assertFalse(r["ok"])
        self.assertIn("覆盖", r["coverage_missing"][0])

    def test_content_bigrams_split_and_drop_stopwords(self):
        """判据的两条口径本身：按标点切段（不跨标点造二元组）+ 去虚词。"""
        tk = validate._content_bigrams("车，的就")
        self.assertNotIn("车就", tk, "标点两侧不该粘出假二元组")
        self.assertFalse(tk & set("的了在和我"), "虚词不该进 token 集合")


class TestDialogueLineLength(unittest.TestCase):
    """台词**长度**判据（2026-09-15，用户反馈"对白太短、都不能表达剧情了"）。

    事故：`DIALOGUE_MIN_RATIO` 只查「有多少镜有台词」，**不查每句多长** →
    「沉。」「开。」「推！」这类 1–4 字残句天然合法。实测 village-bridge（5 分钟 / 43 镜）
    22 句台词**平均只有 7.0 字**、12 句 ≤6 字 —— 占比达标（22/43）、剧情完全不靠台词。

    根因在**契约**：`scriptwriter` SKILL 原写「对白要**短促**有力」+「1–3 分钟约
    500–1500 字」的整片预算；而 `dialogue` 是**逐字搬运器**（不许改写）→ 没有任何一环
    会把句子写长。修法三处齐动：契约措辞 + `roles.role_input` 注入 + 本判据。

    两端都要防：太短承载不了信息；太长念不完（上限与本镜秒数挂钩）。
    """

    HEAD = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |\n"
            "|---|---|---|---|---|---|---|---|\n")
    VIS = "老陈站在桥头拍着胸脯，向旁边两个人说话"

    def _md(self, rows):
        return self.HEAD + "\n".join(rows)

    def _row(self, sid, sec, dialogue, vis=None):
        return "| %s | 中景 | 平视 | 固定 | %s | %s | %s | 风声 |" % (
            sid, sec, vis or self.VIS, dialogue)

    def test_dialogue_body_strips_speaker_and_paren(self):
        self.assertEqual(
            validate._dialogue_body("老陈（拍胸脯，笃定）：桥塌了我拍胸脯，今天修好。"),
            "桥塌了我拍胸脯今天修好")

    def test_dialogue_body_keeps_extra_speakers(self):
        """一格多说话人时只剥第一个前缀（判据要的是"这一镜总共说多少字"）。"""
        self.assertEqual(
            validate._dialogue_body("小林：你那边装多了！ 阿凯：你装的！"),
            "你那边装多了阿凯你装的")

    def test_short_line_detected(self):
        """★ 核心：1–4 字残句必须被点名。"""
        r = validate.check_storyboard(
            self._md([self._row(1, 7, "大勇：沉。"),
                      self._row(2, 7, "老陈：桥塌了我拍胸脯，今天一定给你修好。")]),
            {"audio_mode": "dialogue-led"})
        self.assertEqual(len(r["short_lines"]), 1, r["short_lines"])
        self.assertIn("沉", r["short_lines"][0])
        self.assertEqual(r["long_lines"], [])

    def test_complete_line_passes(self):
        r = validate.check_storyboard(
            self._md([self._row(1, 7, "大勇：这块石头比我想的重多了，你们别松手。")]),
            {"audio_mode": "dialogue-led"})
        self.assertEqual(r["short_lines"], [])

    def test_long_line_capped_by_shot_duration(self):
        """念不完也算违规，且上限**与本镜秒数挂钩**（4 秒 × 6 字/秒 = 24 字）。

        语速常数取 **6**（fatal 档用"最快可得语速"）：实测 village-honey 有句
        38 字 / 7 秒 = **5.4 字/秒**，用 5.0 会**误杀**口播偏快但念得完的句子。
        """
        line = "老陈：" + "这是一句特别长的台词" * 3  # 30 字
        r = validate.check_storyboard(self._md([self._row(1, 4, line)]),
                                      {"audio_mode": "dialogue-led"})
        self.assertEqual(len(r["long_lines"]), 1, r["long_lines"])
        self.assertIn("上限 24", r["long_lines"][0])

    def test_silent_shot_never_counted(self):
        """无台词镜（（无声，环境音））不该进长度判据。"""
        r = validate.check_storyboard(
            self._md([self._row(1, 7, "（无声，环境音）")]),
            {"audio_mode": "dialogue-led"})
        self.assertEqual(r["short_lines"], [])
        self.assertEqual(r["spoken_shots"], 0)


class TestStoryboard(unittest.TestCase):
    HEAD = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |\n"
            "|---|---|---|---|---|---|---|---|\n")

    def _md(self, rows: list[str]) -> str:
        return self.HEAD + "\n".join(rows)

    def test_valid_storyboard(self):
        md = self._md([
            "| 1 | 全景 | 平视 | 固定 | 8 | 陈默在低模出租屋的方块床上醒来 | （无声，环境音） | 电流声 |",
            "| 2 | 中景 | 平视 | 推近 | 10 | 陈默接过地摊老板递来的旧U盘 | 「九块九？」 | 风声 |",
        ])
        b = _brief(must_have=["陈默在低模出租屋醒来", "陈默接过旧U盘"])
        r = validate.check_storyboard(md, b)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["scene_count"], 2)
        self.assertAlmostEqual(r["seconds_total"], 18.0)

    def test_missing_column(self):
        md = ("| 镜头号 | 景别 | 画面描述 | 对白 |\n|---|---|---|---|\n"
              "| 1 | 全景 | 陈默醒来 | （无声） |\n")
        r = validate.check_storyboard(md, None)
        self.assertFalse(r["schema_ok"])
        self.assertIn("运镜", r["missing_cols"])

    def test_s_prefix_shots_are_parsed(self):
        """★ `| S01 |` 前缀必须认（2026-09-14 实测事故：门不认 → 0 镜 → 片长 0s 放行）。

        媒体链的 `storyboard._ROW_RE` **一直认** `S`（它的注释写着"旧实现只认前两种
        → `S10` 静默丢镜、整轮 media 解析出 0 镜"），但**门**写死了 `^(?:LN)?\\d+`
        → **同一个东西两份实现，必然漂移**。牛来包分镜用 `S01…S26`，
        于是门解析出 0 镜 → `seconds_total = 0` → `_duration_gap` 的
        `seconds_total <= 0` 分支**直接放行**，而媒体链照常渲染 26 镜。
        """
        md = self._md([
            "| S01 | 全景 | 平视 | 固定 | 8 | 收粮站院内夜里马德胜提着手电筒 | （无声，环境音） | 风声 |",
            "| S02 | 中景 | 平视 | 固定 | 6 | 马德胜走进院子举手电筒照向磅秤 | 「哪个王八蛋？」 | 脚步 |",
        ])
        r = validate.check_storyboard(md, _brief(target_duration="约 90 秒"))
        self.assertEqual(r["unparsed"], "")
        self.assertEqual(r["seconds_total"], 14.0, "S 前缀的时长必须被算进去")
        self.assertEqual(r["scene_count"], 2)

    def test_unparsable_rows_are_blocked_not_passed(self):
        """**判据看不见的东西不能算通过**：表头在、数据行却一行都认不出 → 必须报 unparsed。

        否则门会"批准"任何它读不懂的输入 —— 实测就是这么放过了一份 0 秒的分镜，
        比没有门更危险。
        """
        md = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |\n"
              "|---|---|---|---|---|---|---|---|\n"
              "| 第1镜 | 全景 | 平视 | 固定 | 8 | 收粮站院内夜里马德胜提着手电筒 | （无声） | 风声 |\n")
        r = validate.check_storyboard(md, _brief(target_duration="约 90 秒"))
        self.assertTrue(r["unparsed"], "解析不出镜头必须显式报出来")
        self.assertFalse(r["ok"])
        self.assertEqual(r["seconds_total"], 0.0)

    def test_rows_without_leading_pipe_are_accepted(self):
        """★★ 行首漏 `|` 的表格：`check_storyboard` 必须与 `parse` 一样认（2026-09-14 实测）。

        真实事故（village-scale 第 3 轮）：模型漏写行首 `|` → 门把所有列判"缺列"
        （`[STORYBOARD-REJECT]`，且诊断是错的：列都在、破的是表格语法）→ 媒体链起不来。
        这里锁住"两个解析器口径一致"。
        """
        md = ("镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效\n"
              "01 | 近景 | 平视 | 固定 | 6 | 原始低成本三维重建的深夜收粮站内部场景 | 磅秤声：「少……」 | 铁针咔响\n"
              "02 | 中景 | 稍俯 | 手持感缓推 | 6 | 镜头进入收粮站锁定马德胜冲进门举手电 | （无声，环境音） | 脚步\n")
        r = validate.check_storyboard(md, _brief(target_duration="约 12 秒"))
        self.assertEqual(r["missing_cols"], [], "列都在，只是表格语法破了")
        self.assertEqual(r["seconds_total"], 12.0)
        self.assertEqual(r["n_shots"], 2)
        self.assertTrue(r["schema_ok"])
        # 注意**不**断言 `ok`：它还含 must_have 覆盖（本夹具的 must_have 讲的是陈默/U盘，
        # 与本分镜无关）→ 那是 fidelity 的事，不是本用例要锁的"表格语法容错"。

    def test_empty_dialog_detected(self):
        md = self._md([
            "| 1 | 全景 | 平视 | 固定 | 8 | 陈默在低模出租屋的方块床上醒来发现不对 |  | 电流声 |",
        ])
        r = validate.check_storyboard(md, None)
        self.assertEqual(r["empty_dialog"], 1)

    def test_jing_prefix_shot_ids(self):
        """★ `| 镜1 |` 前缀必须认（2026-09-14 实测：第四种写法）。

        牛来包契约原先**只说"列结构与顺序不可变"、没说值怎么写** → 同一个模型写出过四种
        镜头号写法（`S01` / 漏行首 `|` / `镜1` / 重号），每次都要人工排查。
        """
        md = self._md([
            "| 镜1 | 全景 | 平视 | 固定 | 7 | 灰蓝色天光未亮村口广场潮湿反光电线杆挂大喇叭 | 老赵：谁把喇叭接上的！ | 电流声 |",
            "| 镜2 | 中景 | 平视 | 固定 | 8 | 电线杆下老赵外八字双手叉腰跺脚小许跑入 | 小许：我上回查还是好的。 | 脚步 |",
        ])
        r = validate.check_storyboard(md, _brief(target_duration="约 15 秒"))
        self.assertEqual(r["unparsed"], "", "镜前缀必须能解析")
        self.assertEqual(r["row_violations"], [])
        self.assertEqual(r["seconds_total"], 15.0)
        self.assertEqual(r["n_shots"], 2)

    def test_duplicate_shot_ids_are_deduped_kept_first(self):
        """★ 重复镜头号按**首次出现**去重（2026-09-14 实测，连续三跑因此作废）。

        分镜文件里除逐场主表外，还有 `## 分镜总表` 与 `## 时长校验` —— 它们的行
        **长得和镜头行完全一样**（同 10 列、同 id）。实测 30 行命中（10 真 + 10 总表 + 10 校验表）
        → 旧实现把它们都算成镜 → **重号 / 镜序错乱 / 总时长 140s（233%）** →
        门把一份**完全正确**的分镜拦下。
        另一类是"同一镜写成两行、两行同号（第二行时长 `—`）" → 保留首行正好留下有数字的行。
        → 所以规则是**去重（保留首次）**，并把行数如实报进 `deduped_rows` 供门提示。
        """
        md = self._md([
            "| 1 | 全景 | 平视 | 固定 | 7 | 灰蓝色天光未亮村口广场潮湿反光挂大喇叭 | （无声） | 电流声 |",
            "| 2 | 中景 | 平视 | 固定 | 8 | 电线杆下老赵外八字双手叉腰跺脚小许跑入 | 小许：我上回查还是好的。 | 脚步 |",
            "| 1 | 全景 | 平视 | 固定 | 7 | 灰蓝色天光未亮村口广场潮湿反光挂大喇叭 | （无声） | 电流声 |",
        ])
        r = validate.check_storyboard(md, _brief(target_duration="约 15 秒"))
        self.assertEqual(r["n_shots"], 2, "重复的那行不该被算成第 3 镜")
        self.assertEqual(r["deduped_rows"], 1)
        self.assertEqual(r["seconds_total"], 15.0, "总时长不能把重复行算进去")
        self.assertEqual(r["row_violations"], [], "去重后不再是违规")
        self.assertNotIn("分镜总表", validate.check_storyboard(
            md, _brief(target_duration="约 15 秒"))["row_violations"])

    def test_non_numeric_duration_blocked(self):
        """★ 时长写 `—` 必须拦截（同一事故：9 行有数字、9 行是 `—`）。

        不拦的后果：`seconds_total` 只累加有数字的行，而 `n_shots` 计全部行 ——
        **两个数字来自不同的行集**，后续所有比例（台词镜占比等）都失真。
        """
        md = self._md([
            "| 1 | 全景 | 平视 | 固定 | 7 | 灰蓝色天光未亮村口广场潮湿反光电线杆挂大喇叭 | （无声） | 电流声 |",
            "| 2 | 近景 | 仰视 | 固定 | — | 老赵仰头瞪喇叭圆脸双下巴脖子短粗灰白短寸头发 | 老赵：谁把喇叭接上的！ | 喇叭声 |",
        ])
        r = validate.check_storyboard(md, None)
        self.assertTrue(any("不是数字" in v for v in r["row_violations"]), r["row_violations"])
        self.assertEqual(r["seconds_total"], 7.0, "只累加了有数字的那行")
        self.assertEqual(r["n_shots"], 2, "而镜数计了全部行 → 两个数字来自不同行集")

    def test_text_dependency_detected(self):
        md = self._md([
            "| 1 | 特写 | 平视 | 固定 | 8 | 镜头推向墓碑，碑文写着陈默之墓 | 「这是啥」 | 风声 |",
        ])
        r = validate.check_storyboard(md, None)
        self.assertEqual(len(r["text_dependency"]), 1)

    def test_uniform_pacing_flagged(self):
        md = self._md([
            "| %d | 全景 | 平视 | 固定 | 8 | 陈默在第%d个场景里继续行走 | （无声） | 风声 |" % (i, i)
            for i in range(1, 5)
        ])
        self.assertTrue(validate.check_storyboard(md, None)["uniform_pacing"])

    def test_ln_prefix_shot_ids(self):
        md = self._md([
            "| LN01 | 全景 | 平视 | 固定 | 8 | 陈默醒来 | （无声） | 电流声 |",
            "| LN02 | 中景 | 平视 | 固定 | 9 | 陈默起身 | 「嗯？」 | 电流声 |",
        ])
        r = validate.check_storyboard(md, None)
        self.assertTrue(r["order_ok"])
        self.assertEqual(r["scene_count"], 2)


class TestDurationContract(unittest.TestCase):
    """分镜总时长 vs brief 目标（2026-09-13 补的缺口）。

    `check_storyboard` **一直**在算 `seconds_total`，但全仓库无人消费；而
    `brief.target_duration` 是必填字段 —— 两边数据都有、从没比过。
    不接这根线，一部写"5 分钟"的 brief 交出 2.4 分钟的分镜也照样放行，
    代价是**整条媒体链跑完**（小时级）才发现片长不对。

    `validate` 只**报告**（`duration_off`），**拦截策略在 `series._storyboard_gate`**
    —— mechanism 与 policy 分开，`ok` 字段的语义不变（既有测试不受影响）。
    """

    HEAD = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |\n"
            "|---|---|---|---|---|---|---|---|\n")

    def _md(self, n=2, secs=8):
        return self.HEAD + "\n".join(
            "| %d | 全景 | 平视 | 固定 | %d | 陈默在第%d个场景里继续行走 | 「嗯」 | 风声 |"
            % (i, secs, i) for i in range(1, n + 1))

    def test_parse_target_seconds(self):
        f = validate.parse_target_seconds
        self.assertEqual(f("约 25 秒，共 6 镜，每镜约 4 秒"), 25.0,
                         "取第一个时间量词（后面的「每镜约 4 秒」是次要说明）")
        self.assertEqual(f("5 分钟"), 300.0)
        self.assertEqual(f("1.5 分钟"), 90.0)
        self.assertEqual(f("90秒左右，共12镜"), 90.0)
        self.assertIsNone(f("五分钟左右，看情况"), "解析不出 → None（只警告不阻断）")
        self.assertIsNone(f(None))
        self.assertIsNone(f(""))

    def test_shortfall_detected(self):
        """2 镜 8s = 16s，brief 目标 90s → 只有 18%，一定是漏了内容。"""
        r = validate.check_storyboard(self._md(n=2, secs=8),
                                      _brief(target_duration="约 90 秒"))
        self.assertEqual(r["target_seconds"], 90.0)
        self.assertEqual(r["duration_ratio"], 0.178)
        self.assertIn("不符", r["duration_off"])
        self.assertIn("漏了内容", r["duration_off"])

    def test_overshoot_detected(self):
        """超目标 30% 会被镜头数上限截断成半成品 → 也要提前说。"""
        r = validate.check_storyboard(self._md(n=2, secs=10),
                                      _brief(target_duration="约 10 秒"))
        self.assertEqual(r["duration_ratio"], 2.0)
        self.assertIn("截断", r["duration_off"])

    def test_within_tolerance_is_clean(self):
        """2 镜 8s = 16s；容差 0.85–1.30 → 目标 12.3s–18.8s 之间都该干净。"""
        for target in ("约 16 秒", "约 18 秒", "约 13 秒"):
            r = validate.check_storyboard(self._md(n=2, secs=8),
                                          _brief(target_duration=target))
            self.assertEqual(r["duration_off"], "", "容差内不该报：%s" % target)

    def test_shortfall_at_80pct_is_now_rejected(self):
        """★ 下界 0.75 → 0.85 的**实测依据**（2026-09-14）。

        paper-crane：brief 要 330 秒，分镜只排 264 秒（**80%**），擦着旧下界过关，
        成片 4.7 分钟 vs 目标 5.5 分钟。0.8 现在必须被判为偏离。
        """
        r = validate.check_storyboard(self._md(n=2, secs=8),
                                      _brief(target_duration="约 20 秒"))
        self.assertEqual(r["duration_ratio"], 0.8)
        self.assertTrue(r["duration_off"], "80% 必须被拦（旧下界 0.75 会放过它）")

    def test_unparseable_target_only_warns(self):
        """brief 措辞不规范不能拦住生产 —— 只留 ratio=None，不产生 off。"""
        r = validate.check_storyboard(self._md(), _brief(target_duration="五分钟左右"))
        self.assertIsNone(r["target_seconds"])
        self.assertIsNone(r["duration_ratio"])
        self.assertEqual(r["duration_off"], "")

    def test_ok_field_semantics_unchanged(self):
        """`ok` 不因片长偏离而翻假 —— 拦截是 `series` 门的 policy，不是 validate 的。"""
        r = validate.check_storyboard(self._md(n=2, secs=8),
                                      _brief(target_duration="约 900 秒"))
        self.assertIn("duration_off", r)
        self.assertTrue(r["duration_off"])


class TestDialogueVerbatim(unittest.TestCase):
    """对白**逐字**判据（2026-09-14 新增）。

    实测事故（paper-crane）：`dialogue` 的旧契约写着「负责对白润色与优化」
    「重写问题台词」，产物标题叫「对白优化报告」—— 它会**改写剧本台词**；
    而剧本本身也带镜级台词 → 同一句两个版本 → 分镜配镜无从取舍，
    配音/口型与画面对不上。同一晚连续 3 次。

    契约已改成"逐字提取"，**这条判据是机制那一半**：纯字符串比对，不花配额。
    """

    SCRIPT = ("# 第1集：纸鹤\n\n### 第1场\n"
              "【镜1 · 3s】玻璃门推开\n- 周平：这件没湿。\n"
              "【镜2 · 4s】江野抬头\n- 江野：烘一下就好。\n")

    def _md(self, *lines):
        return "# 对白清单：《纸鹤》第1集\n\n### 第1场\n" + "\n".join(lines) + "\n"

    def test_all_verbatim_passes(self):
        r = validate.check_dialogue_verbatim(
            self._md("- 周平：这件没湿。", "- 江野：烘一下就好。"), self.SCRIPT)
        self.assertEqual(r["checked"], 2)
        self.assertTrue(r["ok"])
        self.assertEqual(r["offenders"], [])

    def test_rewritten_line_is_caught(self):
        """★ 改写必须被抓到 —— 这正是 supervisor 那晚手工比对的那类问题。"""
        r = validate.check_dialogue_verbatim(
            self._md("- 周平：这件没湿。", "- 江野：这个我放的。"), self.SCRIPT)
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["offenders"]), 1)
        self.assertIn("我放的", r["offenders"][0])
        self.assertEqual(r["ratio"], 0.5)

    def test_punctuation_and_quotes_differences_are_tolerated(self):
        """标点/引号/空白差异不算改写（否则误杀会淹掉真问题）。"""
        r = validate.check_dialogue_verbatim(
            self._md("- 周平：「这件没湿」", "- 江野：烘一下就好"), self.SCRIPT)
        self.assertTrue(r["ok"], r)

    def test_placeholders_are_skipped(self):
        """（无对白）/（无声）/省略号不是台词，不参与比对也不拉低逐字率。"""
        r = validate.check_dialogue_verbatim(
            self._md("- 周平：这件没湿。", "- 江野：（无对白）", "- 周平：……"), self.SCRIPT)
        self.assertEqual(r["checked"], 1)
        self.assertTrue(r["ok"])

    def test_missing_files_returns_none(self):
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(validate.check_dialogue_verbatim_files(P(d)))

    def test_empty_script_does_not_false_positive(self):
        """剧本读不到内容时不能把清单全判成改写（宁漏不误杀）。"""
        r = validate.check_dialogue_verbatim(self._md("- 周平：随便什么"), "")
        self.assertTrue(r["ok"])

    # ── 真实产物形态（2026-09-14 用 paper-crane 的产物验出来的两条）──

    TABLE_MD = ("# 对白清单：《纸鹤》第1集\n\n"
                "## 逐字台词清单（按场）\n\n"
                "### 第1场（钩子）\n"
                "| 出场序 | 参考镜号 | 角色 | 台词原文 | 语气 |\n"
                "|---|---|---|---|---|\n"
                "| 1 | LN05 | 周平 | 这件没湿。 | 平静 |\n"
                "| 2 | LN06 | 江野 | 烘一下就好。 | 轻 |\n")

    def test_table_format_is_parsed(self):
        """★ 真实产物是**表格**（`| 出场序 | 参考镜号 | 角色 | 台词原文 | 语气 |`）——
        第一版解析器只认 `- 角色：台词`，导致真台词一句没进、逐字率报成 0%。"""
        r = validate.check_dialogue_verbatim(self.TABLE_MD, self.SCRIPT)
        self.assertEqual(r["checked"], 2, "表格里的台词必须被解析出来")
        self.assertTrue(r["ok"])

    def test_meta_section_is_skipped(self):
        """清单末尾的「## 合规自检」章节不是台词，不得参与比对。

        实测误报：`- 不得出现第三张清晰人脸：全片仅周平、江野两人。`
        冒号前是**规则**不是角色名，却被当成 `角色：台词`。
        """
        md = self.TABLE_MD + ("\n## 合规自检\n"
                             "- 台词总句数：**14 句**（说明口径）。\n"
                             "- 不得出现第三张清晰人脸：全片仅周平、江野两人。\n"
                             "- 每句 ≤12 字，无旁白、无画外音。\n")
        r = validate.check_dialogue_verbatim(md, self.SCRIPT)
        self.assertEqual(r["checked"], 2, "说明章节不该被计入台词")
        self.assertTrue(r["ok"])
        self.assertEqual(r["offenders"], [])


class TestAudioMode(unittest.TestCase):
    """brief.audio_mode 必须真的生效（此前是空转字段：写了 dialogue-led 也可能全片无声）。"""

    def test_default_is_dialogue_led(self):
        """缺失/未识别一律按要台词处理——别再默默无声。"""
        self.assertEqual(validate.audio_mode_of({}), "dialogue-led")
        self.assertEqual(validate.audio_mode_of({"audio_mode": "啥"}), "啥")
        self.assertTrue(validate.dialogue_required({}))

    def test_aliases(self):
        for v in ("dialogue-led", "Dialogue-Led", "dialogue", "talkie"):
            self.assertEqual(validate.audio_mode_of({"audio_mode": v}), "dialogue-led", v)
        for v in ("silent", "SILENT", "no-dialogue", "无对白"):
            self.assertEqual(validate.audio_mode_of({"audio_mode": v}), "silent", v)

    def test_has_line_distinguishes_silence_marks(self):
        for s in ("（无声）", "（无声，环境音）", "(无对白)", "无台词", "-", "", "  "):
            self.assertFalse(validate._has_line(s), repr(s))
        for s in ("「九块九？」", "陈默：又是最后一个钟头……", "下班了。"):
            self.assertTrue(validate._has_line(s), repr(s))

    def _md(self, n_line: int, n_total: int) -> str:
        head = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |\n"
                "|---|---|---|---|---|---|---|---|\n")
        rows = []
        for i in range(1, n_total + 1):
            dlg = "「台词%d」" % i if i <= n_line else "（无声，环境音）"
            rows.append("| %d | 中景 | 平视 | 固定 | %d | 陈默在低模出租屋里做出第%d个动作 | %s | 风声 |"
                        % (i, 6 + i, i, dlg))
        return head + "\n".join(rows)

    def test_dialogue_led_short_flagged(self):
        """dialogue-led 但台词镜占比过低（低于硬下限）→ dialogue_short。"""
        r = validate.check_storyboard(self._md(0, 4), {"audio_mode": "dialogue-led"})
        self.assertTrue(r["dialogue_short"])
        self.assertEqual(r["spoken_shots"], 0)
        self.assertEqual(r["n_shots"], 4)

    def test_dialogue_led_enough_passes(self):
        r = validate.check_storyboard(self._md(3, 4), {"audio_mode": "dialogue-led"})
        self.assertFalse(r["dialogue_short"])
        self.assertFalse(r["dialogue_thin"])
        self.assertEqual(r["spoken_shots"], 3)

    def test_dialogue_led_borderline_is_warn_not_block(self):
        """1/4 = 25%：过硬下限但低于建议线 → 只警告不阻断。

        实测 bootleg99-full 32 镜 11 句（34%）是合法节奏，用 50% 硬拦会无谓重跑。
        """
        r = validate.check_storyboard(self._md(1, 4), {"audio_mode": "dialogue-led"})
        self.assertFalse(r["dialogue_short"])
        self.assertTrue(r["dialogue_thin"])

    def test_silent_with_lines_flagged(self):
        """反向也要查：silent 却写了台词。"""
        md = self._md(4, 4)
        r = validate.check_storyboard(md, {"audio_mode": "silent"})
        self.assertEqual(r["audio_mode"], "silent")
        self.assertEqual(r["spoken_shots"], 4)
        self.assertFalse(r["dialogue_short"])   # silent 模式不按占比判

    def test_silent_all_silent_ok(self):
        r = validate.check_storyboard(self._md(0, 4), {"audio_mode": "silent"})
        self.assertFalse(r["dialogue_short"])
        self.assertFalse(r["dialogue_thin"])
        self.assertEqual(r["spoken_shots"], 0)

    def test_threshold_blocks_only_zero_like(self):
        """硬下限 0.2：0% 拦、34%（实测合法）放行、25% 警告。"""
        self.assertEqual(validate.DIALOGUE_MIN_RATIO, 0.2)
        self.assertTrue(validate.check_storyboard(
            self._md(0, 10), {"audio_mode": "dialogue-led"})["dialogue_short"])
        self.assertFalse(validate.check_storyboard(
            self._md(3, 10), {"audio_mode": "dialogue-led"})["dialogue_short"])


def json_loads(s: str) -> dict:
    import json
    return json.loads(s)


def eval_safe(text: str) -> dict:
    return json_loads(text)


if __name__ == "__main__":
    unittest.main()
