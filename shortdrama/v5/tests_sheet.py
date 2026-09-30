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

    def test_types_route_prop_and_location_away_from_character_wording(self):
        """★ `types` 分流：道具/场景**不许套"人物设定表"措辞**（2026-09-21 `1fee820`）。

        画中人来 ep1 实测：残旧仕女图与白玉平安扣被声明成"角色的人设表（头肩像三视图）"，
        模型拿着一幅仕女画被告知"这是一个人" ⇒ 道具外观彻底漂移（同一幅画 LN01 白描 /
        LN08 水墨山水）。所以道具改说"定妆照"、场景改说"环境参考"。

        原先这里断言的是「其余参考图是道具/场景参考」—— 那句话在分流那次改动里**已被删掉**，
        测试没同步，于是本模块长期带一条假红灯（2026-09-30 查实：断言引用的句子在代码里
        从未存在过，`git log -S` 在 prompt.py 上只能追到分流那次的删除）。
        """
        rule = prompt.still_ref_rule(["老夫子", "画卷", "云海双塔"],
                                     types=["character", "prop", "location"])
        self.assertIn("第 1 张参考图=角色「老夫子」的人物设定表", rule)
        self.assertIn("第 2 张参考图=道具「画卷」的定妆照", rule)
        self.assertIn("第 3 张参考图=场景「云海双塔」的环境参考", rule)
        # 反向对照：道具那一张不许出现"人物设定表/三视图"字样（那正是漂移的成因）
        prop_seg = rule.split("第 2 张参考图=", 1)[1].split("第 3 张参考图=", 1)[0]
        self.assertNotIn("人物设定表", prop_seg)
        self.assertNotIn("三视图", prop_seg)

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


class MalePromptContentPolicyTests(unittest.TestCase):
    """男性体态声明**不许含解剖学裸词**（2026-09-30 实测的 400 事故）。

    病样本：`_MALE` 原文含「绝对不要出现**乳房**或女性胸部隆起」。Agnes 内容审核对
    它回 `400 content_policy_violation`，于是**每一个用真照片做男性的项目**，四宫格
    设定表的「正视」格必挂 → `sheet.ensure` 返回 None → `cast` 静默回落"直绑源照片"
    （日志只有一行 ⚠️，成片照样出，所以 0923 那次没人发现侧/背格也一起没了）。

    单变量对照（同一条参考图、同一格、只改这一处）：400 → 200。
    触发是**组合**：短描述（"黑色抓绒外套"）带着原词能出图，600 字古风妆造叠上去必拒。

    所以这个测试两头都要锁：① 禁词不许回来；② 反女性化的**意图**不许被整段删掉
    （只为了过 ① 而删光 `_MALE`，会把"清秀男被画成女性"那个旧坑放回来）。
    """

    #: 供应商审核判定区的词（出现即 400，见上面的事故记录）
    BANNED = ("乳房", "女性胸部隆起")
    #: 删掉禁词后**必须仍然在**的反女性化表述
    MUST_KEEP = ("胸部完全平坦", "不要女性胸型", "不要细腰翘臀", "腰臀比为男性", "喉结")

    def _male_prompts(self):
        # 给一段造型文本 ⇒ 走 look 分支（真实项目都走这条；空文本走历史黑T恤分支）
        costume = "玄色交领窄袖劲装，外罩灰褐半臂长氅，腰束旧黑革带，额上束一条细黑编绳抹额。"
        return sheet._prompts(costume)

    def test_banned_words_absent_from_all_four_cells(self):
        for idx, p in enumerate(self._male_prompts()):
            for w in self.BANNED:
                self.assertNotIn(w, p, "第 %d 格（脸/正/侧/背）含审核禁词 %r" % (idx, w))

    def test_anti_feminization_intent_survives(self):
        """正视格仍要显式声明男性骨架/喉结/腰臀比 —— 那是它存在的理由。"""
        front = self._male_prompts()[1]
        for w in self.MUST_KEEP:
            self.assertIn(w, front, "反女性化表述 %r 被删掉了（会放回旧坑）" % w)

    def test_disease_sample_would_still_be_caught(self):
        """把旧病装回去，证明本测试**会红** —— 否则这三条断言是空的。"""
        old = ("这**是成年男性**：男性骨架与体态——肩膀较宽、**胸部完全平坦、绝对不要出现"
               "乳房或女性胸部隆起**、腰臀比为男性、颈部可见喉结。")
        with self.assertRaises(AssertionError):
            for w in self.BANNED:
                self.assertNotIn(w, old)


if __name__ == "__main__":
    unittest.main(verbosity=2)
