# -*- coding: utf-8 -*-
"""分镜表**读得懂读不懂**的回归（2026-09-29 两处漏检）。

为什么单独一个文件：这两条都是"判据看不见输入却照样放行"那一类——
一次实测某轮分镜把镜号写成 `L01`，整表解析出 **0 镜**，而契约门与量表在 0 条上
全部"通过"；同日晚间另一条链交出 12 镜 / 54 秒，brief 明写 15-18 镜，
但镜数下限只有派生公式的 7 镜，于是照样"合格"。

夹具纪律：每个 parse 测试都先断言**解析出的条数**，否则 0 条会让所有断言空转。
"""
from __future__ import annotations

import unittest

HDR = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 场景 | 视觉风格 | 画面描述 "
       "| 落幅 | 对白 | 音效 | 文字镜 | 承接 |")
SEP = "|---|" * 13


def md(prefix: str, n: int) -> str:
    rows = []
    for i in range(1, n + 1):
        rows.append("| %s%02d | 中景 | 平视 | 缓推 | 4 | 崖顶石台 | 黄昏逆光 | "
                    "0-4秒：@沈砚 侧身左移半步再拔剑横端至胸前、剑尖斜指对方出画处右肋、"
                    "断剑剑刃与沧浪剑刃相抵迸出一簇电弧 | 剑尖前点 | 最后一壶，喝完就动手 | "
                    "风声 | 否 | 承接上一镜落点 |" % (prefix, i))
    return "# 分镜脚本\n\n" + HDR + "\n" + SEP + "\n" + "\n".join(rows) + "\n"


class TestShotNumberPrefix(unittest.TestCase):
    def test_L_prefix_rows_parse(self):
        """`L01` 必须能读出来（读不出 = 整表消失）。"""
        from v5.media import storyboard
        shots = storyboard.parse(md("L", 12))
        self.assertEqual(len(shots), 12, "L 前缀镜号解析不出来")
        self.assertEqual(shots[0]["seconds"], 4)

    def test_existing_prefixes_still_parse(self):
        """加 L 不许弄坏已有的四种写法。"""
        from v5.media import storyboard
        for pfx in ("", "LN", "S", "镜"):
            self.assertEqual(len(storyboard.parse(md(pfx, 6))), 6, "前缀 %r 回归" % pfx)

    def test_gate_still_fails_closed_on_zero_rows(self):
        """表头在、一行都不认 ⇒ 契约门必须响亮阻断（fail-closed 不能被兼容掉）。"""
        from v5 import validate
        bad = "# 分镜\n\n" + HDR + "\n" + SEP + "\n| X01 | 中景 | 平视 | 缓推 | 4 | 台 | 光 | " \
              "0-4秒：@沈砚 侧身左移半步再拔剑横端至胸前、剑尖斜指对方 | 落 | 台词 | 风声 | 否 | 接 |\n"
        r = validate.check_storyboard(bad, {"target_duration": "约 60 秒，共 12-14 镜",
                                            "audio_mode": "dialogue-led"})
        self.assertTrue(r["unparsed"], "解析不出镜头时门必须报 unparsed，而不是放行空表")


class TestShotCountWindow(unittest.TestCase):
    def test_parse_shot_range(self):
        from v5 import validate
        self.assertEqual(validate.parse_shot_range("约 60 秒，共 15-18 镜，快切"), (15, 18))
        self.assertEqual(validate.parse_shot_range("共 32 镜"), (32, 32))
        # ★ 单值写法（1003 实测：上一版 brief 写「全片 26 镜」被旧正则静默读成 None，
        #   于是镜数判据退回派生公式 —— 与 0929「明写 15-18 镜却读不出」同型失效）
        self.assertEqual(validate.parse_shot_range("约 132 秒，全片 11 镜 / 每镜 12 秒"), (11, 11))
        self.assertEqual(validate.parse_shot_range("26 镜 / 约 130 秒"), (26, 26))
        self.assertEqual(validate.parse_shot_range("全片 12-16 个镜头"), (12, 16))
        self.assertIsNone(validate.parse_shot_range("约 60 秒，镜数按目标秒推"))
        self.assertIsNone(validate.parse_shot_range(""))

    def test_scene_unit_brief_never_yields_a_shot_count(self):
        """★ 2026-10-05「场」口径的 brief 里那些 `N 镜` **不是**镜数声明，一个都不许读出来。

        实测把 `madfate-abc-1005` 的真 brief 喂进来：裸支把「场内 2 镜起」的那个 **2**
        读成 `(2, 2)` ⇒ 链内体检对 24 镜全表报「单镜时长要贴近 60 秒
        （合格区间 **30-12** 秒）」——区间倒挂、没有合法表能满足，
        而这份清单会在每次打回重跑时注入给分镜角色（角色改不动 ⇒ 工头反复重派）。
        """
        from v5 import validate
        real = ("约 120 秒（每集）。★ 结构单位是**场**，不是镜：一集拆成 9–11 场，"
                "**每场总长 4–12 秒**（4 秒是一条请求发得出去的最低限、12 秒是上限；"
                "一场 = 一次生成），场内 2 镜起、**镜数不设上限**。"
                "**每镜几秒完全由你按剧情节拍决定，本项目不设镜长地板**"
                "——快切正反打写 1 秒甚至 0.5 秒一镜都是合法写法。")
        self.assertIsNone(validate.parse_shot_range(real),
                          "「9–11 场」是场数、「2 镜起」是场内下限，都不许当镜数读出来")
        self.assertIsNone(validate.parse_shot_range("每场 ≥2 镜"), "≥ 前缀 = 下限，不是总数")
        self.assertIsNone(validate.parse_shot_range("场内 2 镜起"), "「起」后缀 = 下限")
        self.assertIsNone(validate.parse_shot_range("每场 4-8 镜"))
        # 反向对照：真声明必须**照样读得出**（不许把筛上下文做成一律不读）
        self.assertEqual(validate.parse_shot_range("约 120 秒，共 24 镜；场内 2 镜起"), (24, 24),
                         "同句里既有声明又有下限 ⇒ 读声明那一个")
        self.assertEqual(validate.parse_shot_range("24 镜 / 11 场"), (24, 24))

    def test_countable_uses_brief_range_not_only_formula(self):
        """brief 明写 15-18 镜时，12 镜必须不合格；16 镜必须合格。"""
        from v5 import shotcheck
        from v5.media import storyboard
        twelve = storyboard.parse(md("LN", 12))
        self.assertEqual(len(twelve), 12)
        bad = [h for h in shotcheck.countable(twelve, 60, None, (15, 18))
               if h["check"].startswith("片长与镜数")]
        self.assertTrue(bad, "12 镜 / brief 要 15-18 镜，必须被拦")
        self.assertIn("15-18", bad[0]["detail"], "退回信息要告诉角色按哪个区间判")
        sixteen = storyboard.parse(md("LN", 16))
        ok = [h for h in shotcheck.countable(sixteen, 66, None, (15, 18))
              if h["check"].startswith("片长与镜数")]
        self.assertFalse(ok, "16 镜 / 64 秒不该被这条判不合格")

    def test_formula_floor_kept_when_brief_has_no_range(self):
        """brief 没写区间时，仍按目标秒推下限（不写死、也不误杀短片）。"""
        from v5 import shotcheck
        from v5.media import storyboard
        six = storyboard.parse(md("LN", 6))
        bad = [h for h in shotcheck.countable(six, 60, None, None)
               if h["check"].startswith("片长与镜数")]
        self.assertTrue(bad, "6 镜 / 24 秒远低于 60 秒目标，该拦")


class TestSceneColumn(unittest.TestCase):
    """★ 2026-10-05：分镜表新增可选「场次」列 —— 场 = 一次生成 = 一条 ≤12 秒请求。

    没有这一列时"场"在盘上根本不存在（madfate-abc-1005 实测：剧本 4 场，
    分镜只剩 2 个地点值，分组与场无关）。
    """

    HDR_ACT = ("| 镜头号 | 场次 | 景别 | 角度 | 运镜 | 时长(秒) | 场景 | 视觉风格 "
               "| 画面描述 | 落幅 | 对白 | 音效 | 文字镜 | 承接 |")
    SEP_ACT = "|---|" * 14

    @classmethod
    def _md(cls, cells):
        """cells = [(场次单元格原文, 场景单元格原文)]；镜头号一律纯数字（占第 1 列）。"""
        rows = []
        for i, (act_cell, loc) in enumerate(cells, 1):
            rows.append("| %d | %s | 中景 | 平视 | 缓推 | 5 | %s | 夜 | "
                        "0-5秒：@沈砚 侧身左移半步再拔剑横端至胸前 | 剑尖前点 | "
                        "（无声，环境音）| 风声 | 否 | 承接上一镜落点 |"
                        % (i, act_cell, loc))
        return ("# 分镜脚本\n\n" + cls.HDR_ACT + "\n" + cls.SEP_ACT + "\n"
                + "\n".join(rows) + "\n")

    def test_scene_column_parses_to_int(self):
        from v5.media import storyboard
        shots = storyboard.parse(self._md([
            ("1", "场1 验尸房·凌晨"), ("场2", "验尸房"),
            ("3", "天台"), ("第4场", "天台")]))
        self.assertEqual(len(shots), 4, "四行必须都读出来")
        self.assertEqual([s["act"] for s in shots], [1, 2, 3, 4],
                         "四种写法（裸数字／场N／第N场）都要取出场号")

    def test_scene_cell_with_words_still_yields_number(self):
        from v5.media import storyboard
        shots = storyboard.parse(self._md([("场 7 验尸房·凌晨", "验尸房")]))
        self.assertEqual(len(shots), 1)
        self.assertEqual(shots[0]["act"], 7, "「场 7 验尸房·凌晨」要取到 7")

    def test_scene_column_absent_means_zero_not_guessed(self):
        """⛔「场」是「场景」的子串 —— 只认旧表头时 act 必须全 0，不许拿地点当场号。"""
        from v5.media import storyboard
        shots = storyboard.parse(md("", 3))
        self.assertEqual(len(shots), 3, "旧表头三行仍要全读出来")
        self.assertEqual([s["act"] for s in shots], [0, 0, 0],
                         "没有场次列却猜出场号 = 会把两段戏误并进一条请求")


    def test_fractional_seconds_are_not_truncated(self):
        """★ 2026-10-05：`时长(秒)` 写 0.5 必须读成 0.5。

        旧写法 `int(float(...))` 把 0.5 截成 0，随后 `pack_clamp_sec` 的"或 4"又把它
        兜成 4 秒 ⇒ 快切镜在分组阶段被悄悄拉长，分镜声明与实际下单脱节。
        """
        from v5.media import storyboard
        rows = ["| %d | 场1 后巷 | 近景 | 平视 | 手持 | %s | 后巷 | 夜 | "
                "0-0.5秒：@沈砚 侧头、0.5-1秒：@沈砚 拔剑半寸 | 剑柄出鞘 | "
                "（无声，环境音）| 风声 | 否 | 承接上一镜落点 |"
                % (i, sec) for i, sec in enumerate(["0.5", "0.5", "1.5", "5", "12"], 1)]
        md_txt = ("# 分镜脚本\n\n" + self.HDR_ACT + "\n" + self.SEP_ACT + "\n"
                  + "\n".join(rows) + "\n")
        shots = storyboard.parse(md_txt)
        self.assertEqual(len(shots), 5, "五行都要读出来")
        self.assertEqual([s["seconds"] for s in shots], [0.5, 0.5, 1.5, 5, 12],
                         "小数秒必须原样保留，整数照旧是 int")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestBeatContinuityRepair(unittest.TestCase):
    """镜内节拍的**算术断裂**由代码重排起点（2026-10-08 实测，打回两轮没修）。

    真实形状取自 `yuxuan-duanfeng-1007` 第 2 集镜1（12 秒 8 段）：
    第三段写成 `2.5-4.5秒：`，而上一段终点是 3 ⇒ 分镜契约门判「不连续」拦下整集。
    """

    ROW12 = ("| 1 | 全景 | 背后跟拍 | 跟移 | 12 | 雨竹林 | 斜雨冷青灰 | "
             "0-1.5秒：她踏阶而上、马尾甩出小弧；1.5-3秒：她蹬地跨上第三级石阶、水花向左溅；"
             "2.5-4.5秒：岑墨持枪追近两级、枪身横于胸前；4.5-6.5秒：枪尖擦过她右臂、青碧光起；"
             "6.5-8.5秒：她回旋踢起、腿风扫开雨帘；8.5-10.5秒：枪尾砸地、石屑弹开；"
             "10.5-12秒：她借势后撤半步、剑出鞘半寸 | 剑尖前点 | 追这么远？ | 雨声 | 否 | 开场 |")

    def _doc(self, row: str) -> str:
        return "# 分镜脚本\n\n" + HDR + "\n" + SEP + "\n" + row + "\n"

    def test_real_shape_is_repaired_and_gate_would_pass(self):
        from v5 import validate
        from v5.media import storyboard
        md = self._doc(self.ROW12)
        self.assertEqual(len(storyboard.parse(md)), 1, "夹具本身须解析出 1 镜")
        self.assertTrue(validate.check_storyboard(md, None)["beat_violations"],
                        "夹具没复现出断裂 = 本用例等于没测")
        fixed, ch = storyboard.repair_beat_continuity(md)
        self.assertEqual(len(ch), 1, ch)
        self.assertIn("2.5→3", ch[0])
        self.assertIn("2.5-4.5秒", md)          # 改前确实是这样
        self.assertIn("3-4.5秒", fixed)
        self.assertFalse(validate.check_storyboard(fixed, None)["beat_violations"],
                         "修完仍报不连续 = 修错了地方")
        # 只改数字：把**整个数字**（含小数点）抹掉后，两份文本必须逐字相同
        import re as _re
        strip = lambda s: _re.sub(r"\d+\.\d+|\.\d+|\d", "", s)
        self.assertEqual(strip(md), strip(fixed))

    def test_contiguous_timeline_untouched(self):
        """反向对照：本来就首尾相接 ⇒ 原文**逐字返回**、零变更。"""
        from v5.media import storyboard
        md = self._doc(self.ROW12.replace("2.5-4.5秒", "3-4.5秒"))
        fixed, ch = storyboard.repair_beat_continuity(md)
        self.assertEqual(ch, [])
        self.assertEqual(fixed, md)

    def test_collapse_is_not_repaired(self):
        """会塌缩（起点 ≥ 终点）的不修 —— 那要挑一段牺牲，属语义判断，交给门拦。"""
        from v5 import validate
        from v5.media import storyboard
        md = self._doc("| 1 | 全景 | 平视 | 固定 | 8 | 竹林 | 冷光 | "
                       "0-5秒：她拔剑；2-4秒：他举枪；4-8秒：两人交错 | 剑前点 | 让开 | "
                       "风声 | 否 | 开场 |")
        fixed, ch = storyboard.repair_beat_continuity(md)
        self.assertEqual(ch, [], "5-4 会塌缩，不该动手")
        self.assertEqual(fixed, md)
        self.assertTrue(validate.check_storyboard(md, None)["beat_violations"])

    def test_first_beat_start_is_never_moved(self):
        """「必须从 0 起」是作者的决定，推不出来 ⇒ 不许自动改成 0。"""
        from v5.media import storyboard
        md = self._doc("| 1 | 全景 | 平视 | 固定 | 10 | 竹林 | 冷光 | "
                       "5-8秒：她拔剑；8-10秒：他举枪 | 剑前点 | 让开 | 风声 | 否 | 开场 |")
        fixed, ch = storyboard.repair_beat_continuity(md)
        self.assertEqual(ch, [])
        self.assertIn("5-8秒", fixed)

    def test_other_columns_are_never_touched(self):
        """只动「画面描述」列：对白里出现同样的节拍写法也不许改。"""
        from v5.media import storyboard
        md = self._doc("| 1 | 全景 | 平视 | 固定 | 12 | 竹林 | 冷光 | "
                       "0-1.5秒：她拔剑；1.5-3秒：她跨步；2.5-4.5秒：他举枪 | 剑前点 | "
                       "2.5-4.5秒：那句台词原样 | 风声 | 否 | 开场 |")
        fixed, ch = storyboard.repair_beat_continuity(md)
        self.assertEqual(len(ch), 1, ch)
        self.assertIn("2.5-4.5秒：那句台词原样", fixed, "对白列被改了")

    def test_table_without_visual_column_is_left_alone(self):
        """认不出表头 ⇒ 整表不动（不猜列）。"""
        from v5.media import storyboard
        body = ("| 镜 | 描述 |\n|---|---|\n"
                "| 1 | 0-1.5秒：a；1.5-3秒：b；2.5-4.5秒：c |\n")
        fixed, ch = storyboard.repair_beat_continuity(body)
        self.assertEqual(ch, [])
        self.assertEqual(fixed, body)

    def test_gate_writes_the_repair_back_and_off_flag_restores_the_block(self):
        """接线：门读表**之前**重排并写回盘；`BEAT_AUTOFIX=0` 时照旧响亮拦下。"""
        import tempfile
        from pathlib import Path
        from unittest import mock
        from v5 import config, series
        from v5.media import storyboard
        md = self._doc(self.ROW12)
        for flag, expect_repaired in ((True, True), (False, False)):
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                (root / "scenedesigner").mkdir(parents=True)
                f = root / "scenedesigner" / "scenedesigner_ep1.md"
                f.write_text(md, encoding="utf-8")
                with mock.patch.object(config, "BEAT_AUTOFIX", flag):
                    try:
                        series.storyboard_gate(root, {"target_duration": "约 12 秒"}, ep=1)
                        raised = ""
                    except SystemExit as e:
                        raised = str(e.code)
                wrote_back = ("3-4.5秒" in f.read_text(encoding="utf-8"))
                self.assertEqual(wrote_back, expect_repaired, "flag=%s" % flag)
                if flag:
                    self.assertNotIn("节拍", raised, "重排后不该再为节拍拦下：%s" % raised)
                else:
                    self.assertIn("节拍不自洽", raised,
                                  "关掉自愈必须由门拦下（行为与改造前一致）")
