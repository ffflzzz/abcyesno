# -*- coding: utf-8 -*-
"""四宫格设定表 + 逐张点名角色（2026-09-19）自测：**纯逻辑 + 临时目录，不联网**。

锁定四件事（每件都是当晚实测换来的行为）：
1. `still_ref_rule(None)` 必须**逐字等于历史常量**（默认路径零变化）；
2. 给了名字时必须**逐张点名角色**（官方多图合成结构）；
3. `sheet.ensure` 对手供表 `images/<名>.sheet.<ext>` **优先且零生成**；
4. 源照片过小/读不出 ⇒ **不生成、返回 None**（让调用方回落直绑；也保证单测不联网）。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image  # noqa: E402

from v5.media import prompt, sheet  # noqa: E402


class RefRuleTests(unittest.TestCase):
    def test_default_rule_unchanged(self):
        """不传名字 ⇒ 与历史常量一字不差（老路径行为不变）。"""
        self.assertEqual(prompt.still_ref_rule(None), prompt.STILL_REF_RULE)
        self.assertEqual(prompt.still_ref_rule([]), prompt.STILL_REF_RULE)

    def test_named_roles_are_spelled_out(self):
        """给了名字 ⇒ 逐张点名角色，且说明"不得画成另一个人"。"""
        rule = prompt.still_ref_rule(["老夫子", "老赵"])
        self.assertIn("第 1 张参考图=角色「老夫子」", rule)
        self.assertIn("第 2 张参考图=角色「老赵」", rule)
        self.assertIn("不得画成另一个人", rule)
        self.assertIn("其余参考图是道具/场景参考", rule)

    def test_blank_names_fall_back(self):
        """名单里全是空串 ⇒ 回落历史常量（别产出"第 1 张参考图=角色「」"这种垃圾）。"""
        self.assertEqual(prompt.still_ref_rule(["", "  "]), prompt.STILL_REF_RULE)


class SheetEnsureTests(unittest.TestCase):
    def _root(self, tw=512, th=512):
        d = tempfile.TemporaryDirectory()
        root = Path(d.name) / "p1"
        (root / "images").mkdir(parents=True)
        Image.new("RGB", (tw, th), (10, 20, 30)).save(
            root / "images" / "老周.source.jpg", "JPEG")
        return d, root

    def test_manual_sheet_wins_and_costs_nothing(self):
        """手供表优先：直接落成 `<名>.png`，**一次生成都不花**。"""
        d, root = self._root()
        with d:
            Image.new("RGB", (800, 600), (200, 200, 200)).save(
                root / "images" / "老周.sheet.jpg", "JPEG")
            with mock.patch.object(sheet.providers, "gen_image",
                                   side_effect=AssertionError("手供表不该触发生成")):
                out = sheet.ensure(root, "老周", root / "images" / "老周.source.jpg",
                                   log=lambda *_: None)
            self.assertEqual(Path(out).name, "老周.png", "手供表要落成 <名>.png 供 bind()")
            self.assertTrue((root / "images" / "老周.png").exists())
            self.assertTrue(sheet.sheet_path(root).exists(), "人工查看位也要有")

    def test_tiny_source_skips_without_network(self):
        """源照片过小 ⇒ 返回 None 且**不联网**（回落直绑由 cast 负责）。"""
        d, root = self._root(tw=64, th=64)
        with d:
            with mock.patch.object(sheet.providers, "gen_image",
                                   side_effect=AssertionError("过小源照片不该生成")):
                out = sheet.ensure(root, "老周", root / "images" / "老周.source.jpg",
                                   log=lambda *_: None)
            self.assertIsNone(out)

    def test_unreadable_source_skips(self):
        """源照片损坏 ⇒ 返回 None，不抛（不得阻断整条链路）。"""
        d = tempfile.TemporaryDirectory()
        with d:
            root = Path(d.name) / "p2"
            (root / "images").mkdir(parents=True)
            (root / "images" / "老周.source.jpg").write_bytes(b"not-an-image")
            out = sheet.ensure(root, "老周", root / "images" / "老周.source.jpg",
                               log=lambda *_: None)
            self.assertIsNone(out)

    def test_piece_paths_live_under_sheets_dir(self):
        """四格图片必须落在 `images/_sheets/`（该目录不被 auto_sync 收编成资产）。"""
        d, root = self._root()
        with d:
            p = sheet.piece_path(root, "老周", "face")
            self.assertEqual(p.parent.name, "_sheets")
            self.assertEqual(p.name, "老周_face.png")


class ConfigSwitchTests(unittest.TestCase):
    def test_auto_by_default_and_explicit_off(self):
        """默认 auto（True）；显式 0 才关 —— 用户要求"放了照片就该走新流程"。"""
        import importlib

        from v5 import config

        self.assertTrue(config.SOURCE_SHEET, "默认必须是 auto（有源照片即启用）")
        old = __import__("os").environ.get("SHORTDRAMA_SOURCE_SHEET")
        try:
            __import__("os").environ["SHORTDRAMA_SOURCE_SHEET"] = "0"
            importlib.reload(config)
            self.assertFalse(config.SOURCE_SHEET, "显式 0 必须能关掉")
        finally:
            if old is None:
                __import__("os").environ.pop("SHORTDRAMA_SOURCE_SHEET", None)
            else:
                __import__("os").environ["SHORTDRAMA_SOURCE_SHEET"] = old
            importlib.reload(config)


if __name__ == "__main__":
    unittest.main(verbosity=2)
