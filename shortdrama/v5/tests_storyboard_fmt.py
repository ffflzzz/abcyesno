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
        self.assertIsNone(validate.parse_shot_range("约 60 秒，镜数按目标秒推"))
        self.assertIsNone(validate.parse_shot_range(""))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
