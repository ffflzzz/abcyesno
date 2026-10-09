# -*- coding: utf-8 -*-
"""`v5/media/sheetcheck.py` 的离线单测（不打网络）。

夹具用的是 **2026-09-29 xianxia-60s4-0929 的真实角色卡原文**，
"图上所见"用的是 `images/沈砚.png` 的**真实画面**（酒红发 / 完整长剑 / 铜护腕）——
也就是这条判据要拦的那个病样本。反向对照（观察与卡片一致 ⇒ 不判）同批写，
否则"永远不报错"和"判据正确"分不开。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock

from .media import sheetcheck as sc

CARD = ("@沈砚（暗赤色织金窄袖劲装配黑色长裤短靴、玄黑高马尾铜制山形发冠、"
        "左腕一串旧铜钱、双手共持一柄断剑自中段折断、剑身残留暗赤雷光）")
# ⇒ 可对账 **4 项**。第 5 项是光效：白底设定表上没有雷光是**对的**，被 `items_of` 剔掉。

# 图上真实画出来的样子（对照 images/沈砚.png 的真人像核对过）
SEEN_WRONG = json.dumps({"items": [
    {"item": "暗赤色织金窄袖劲装配黑色长裤短靴", "seen": "是",
     "detail": "暗红色织金窄袖上衣，配黑色长裤与短靴"},
    {"item": "玄黑高马尾铜制山形发冠", "seen": "是",
     "detail": "头发是酒红色高马尾，头顶金色山形发冠"},
    {"item": "左腕一串旧铜钱", "seen": "否",
     "detail": "左腕戴着一只铜质护腕，没有铜钱"},
    {"item": "双手共持一柄断剑自中段折断", "seen": "是",
     "detail": "双手共持一柄完整长剑，剑身未断"},
]}, ensure_ascii=False)

SEEN_RIGHT = json.dumps({"items": [
    {"item": "暗赤色织金窄袖劲装配黑色长裤短靴", "seen": "是",
     "detail": "暗红织金窄袖劲装，黑色长裤短靴"},
    {"item": "玄黑高马尾铜制山形发冠", "seen": "是",
     "detail": "玄黑色高马尾，发冠是铜色山形"},
    {"item": "左腕一串旧铜钱", "seen": "是",
     "detail": "左腕缠着一串旧铜钱"},
    {"item": "双手共持一柄断剑自中段折断", "seen": "是",
     "detail": "双手共持一柄剑身自中段折断的断剑"},
]}, ensure_ascii=False)


class TestItemsOf(unittest.TestCase):
    def test_splits_to_visual_items(self):
        items = sc.items_of(CARD)
        # ★ 夹具必须真的解析出条目 —— 0 条会让下面所有判据"全过"却什么都没测到
        self.assertEqual(len(items), 4, items)
        self.assertTrue(any("马尾" in i for i in items), items)
        self.assertTrue(any("断剑" in i for i in items), items)
        self.assertFalse(any("雷光" in i for i in items), "光效项必须被剔掉：%s" % items)

    def test_non_visual_items_dropped(self):
        """身高/体态/气质这类**像素核不了**的条目不进对账（留着只会造假矛盾）。"""
        self.assertEqual(sc.items_of("约一米八、身形挺拔、气质冷冽"), [])
        self.assertEqual(sc.items_of("眼神锐利"), ["眼神锐利"], "眼是可见特征，该留")

    def test_glow_items_are_not_checked(self):
        """雷光/电弧**不该**在白底设定表上 —— 要求它有 = 每次必判矛盾的假伤。"""
        card = "@沈砚（玄黑高马尾铜制山形发冠、剑身残留暗赤雷光、剑尖拖短促电弧）"
        self.assertEqual(sc.items_of(card), ["玄黑高马尾铜制山形发冠"], sc.items_of(card))

    def test_parenthetical_item_is_not_cut_in_two(self):
        """真卡写法：括号里还有 `、` —— 括号内不许切（切了模型只能复述半截）。"""
        card = ("@沈砚（双手共持一柄断剑（全片只有一把剑、不是双手各持一把）"
                "—— 玄铁质地修长长剑自中段折断、左腕缠一串旧铜钱）")
        items = sc.items_of(card)
        self.assertEqual(len(items), 2, items)
        self.assertIn("不是双手各持一把", items[0])
        self.assertIn("自中段折断", items[0])
        self.assertEqual(items[1], "左腕缠一串旧铜钱")


class TestJudge(unittest.TestCase):
    def test_real_incident_gets_flagged(self):
        """病样本：卡片`玄黑高马尾`+`断剑`，图上酒红发+完整长剑 ⇒ 必须抓到。"""
        r = sc.judge(CARD, SEEN_WRONG)
        kinds = {(m[2], m[0]) for m in r["mismatch"]}
        self.assertIn(("颜色矛盾", "玄黑高马尾铜制山形发冠"), kinds, r["mismatch"])
        self.assertIn(("兵刃形态矛盾", "双手共持一柄断剑自中段折断"), kinds, r["mismatch"])
        self.assertIn(("未画出", "左腕一串旧铜钱"), kinds, r["mismatch"])

    def test_matching_sheet_passes(self):
        """反向对照：观察与卡片一致 ⇒ 一条都不许判。"""
        r = sc.judge(CARD, SEEN_RIGHT)
        self.assertEqual(r["mismatch"], [], r["mismatch"])
        self.assertEqual(r["unchecked"], [], "四项都答了就不该有未判")

    def test_synonym_in_same_color_family_passes(self):
        """近义同族不算矛盾（卡片`暗赤` vs 图上`酒红` 都是红族）。"""
        reply = json.dumps({"items": [
            {"item": "暗赤色织金窄袖劲装配黑色长裤短靴", "seen": "是",
             "detail": "酒红色窄袖上衣配黑裤黑靴"}]}, ensure_ascii=False)
        self.assertEqual(sc.judge(CARD, reply)["mismatch"], [])

    def test_fabricated_item_not_counted(self):
        """模型抄不出卡片原文的条目 ⇒ 不算矛盾；卡片里真有的项仍记为"未判"。"""
        reply = json.dumps({"items": [
            {"item": "披风上绣着金色巨龙", "seen": "否", "detail": "没有"}]},
            ensure_ascii=False)
        r = sc.judge(CARD, reply)
        self.assertEqual(r["mismatch"], [], r["mismatch"])
        self.assertNotIn("披风上绣着金色巨龙", json.dumps(r, ensure_ascii=False))
        self.assertEqual(len(r["unchecked"]), 4, r["unchecked"])

    def test_unanswered_items_are_reported_not_blocked(self):
        """只答一项 ⇒ 其余进 unchecked（判据看不见的不算通过，也不算失败）。"""
        reply = json.dumps({"items": [
            {"item": "左腕一串旧铜钱", "seen": "是", "detail": "一串旧铜钱"}]},
            ensure_ascii=False)
        r = sc.judge(CARD, reply)
        self.assertEqual(r["mismatch"], [])
        self.assertEqual(len(r["unchecked"]), 3, r["unchecked"])

    def test_garbage_reply_is_not_a_pass_nor_a_crash(self):
        r = sc.judge(CARD, "这张图很好看，符合角色设定。")
        self.assertEqual(r["mismatch"], [])
        self.assertEqual(len(r["unchecked"]), 4, "解析不出条目必须全列成未判")

    def test_missing_item_flagged(self):
        reply = json.dumps({"items": [
            {"item": "左腕一串旧铜钱", "seen": "否", "detail": ""}]}, ensure_ascii=False)
        m = sc.judge(CARD, reply)["mismatch"]
        self.assertEqual([x[2] for x in m], ["未画出"], m)


class TestColors(unittest.TestCase):
    def test_compound_color_yields_both_families(self):
        self.assertEqual(sc.colors("深蓝白雷光"), {"蓝", "白"})
        self.assertEqual(sc.colors("玄黑高马尾"), {"黑"})
        self.assertEqual(sc.colors("剑身折断"), set())

    def test_copper_is_a_material_not_a_color(self):
        """`铜制发冠` 画成金色不该算矛盾 ⇒ 铜不进颜色族。"""
        self.assertNotIn("金", sc.colors("铜制山形发冠"))


class TestAskText(unittest.TestCase):
    def test_lists_card_items_verbatim(self):
        t = sc.ask_text(CARD)
        for it in sc.items_of(CARD):
            self.assertIn(it, t)


class TestVerifyWiring(unittest.TestCase):
    """`cast.verify_character_sheets` 的接线：**有界**是这里唯一要守的事。

    生图与登记全部 mock 掉（不打网络）；视觉回复用 `ask=` 注入。
    """

    def _proj(self, tmp):
        from pathlib import Path

        from PIL import Image

        root = Path(tmp)
        (root / "images").mkdir(parents=True, exist_ok=True)
        # 必须是**真实尺寸量级**的图：`verify` 会先按字节数挡掉截断/占位文件
        # （拿读不出内容的图问模型 = 假矛盾 + 白烧一次重画），所以这里塞噪声让它过闸。
        import os

        Image.frombytes("RGB", (96, 96), bytes(os.urandom(96 * 96 * 3))).save(
            root / "images" / "沈砚.png")
        return root

    def test_same_picture_and_card_do_not_reroll_residual_on_restart(self):
        from .media import cast
        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            chars = [{"name": "沈砚", "appearance": CARD}]
            ask = mock.Mock(return_value=SEEN_WRONG)
            with mock.patch.object(cast, "_turnaround", return_value=True) as draw, \
                    mock.patch.object(cast, "_register"):
                first = cast.verify_character_sheets(root, chars, ask=ask, log=lambda *_: None)
                paid = ask.call_count
                again = cast.verify_character_sheets(root, chars, ask=ask, log=lambda *_: None)
                self.assertEqual(ask.call_count, paid)
                self.assertEqual(draw.call_count, 1)
                self.assertEqual(again["residual"], first["residual"])
                self.assertEqual(again["cached"], 1)
                (root / "images/沈砚.png").write_bytes((root / "images/沈砚.png").read_bytes() + b"changed")
                cast.verify_character_sheets(root, chars, ask=ask, log=lambda *_: None)
                self.assertGreater(ask.call_count, paid)
                before = ask.call_count
                chars[0]["appearance"] += "；腰带为银色"
                cast.verify_character_sheets(root, chars, ask=ask, log=lambda *_: None)
                self.assertGreater(ask.call_count, before)

    def test_unavailable_vision_is_not_cached_as_a_verdict(self):
        from .media import cast
        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            chars = [{"name": "沈砚", "appearance": CARD}]
            ask = mock.Mock(side_effect=RuntimeError("unavailable"))
            for _ in range(2):
                rep = cast.verify_character_sheets(root, chars, ask=ask, log=lambda *_: None)
                self.assertEqual(rep["unknown"], 1)
            self.assertEqual(ask.call_count, 2)

    def test_no_answers_are_unknown_and_retriable(self):
        from .media import cast
        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            chars = [{"name": "沈砚", "appearance": CARD}]
            ask = mock.Mock(return_value="无法辨认")
            for _ in range(2):
                rep = cast.verify_character_sheets(root, chars, ask=ask, log=lambda *_: None)
                self.assertEqual(rep["unknown"], 1)
            self.assertEqual(ask.call_count, 2)

    def test_redraws_once_and_passes(self):
        from .media import cast

        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            seen = [SEEN_WRONG, SEEN_RIGHT]
            calls = []

            def fake_ask(path, app):
                out = seen[min(len(calls), len(seen) - 1)]
                calls.append(1)
                return out

            with mock.patch.object(cast, "_turnaround", return_value="x") as tur, \
                    mock.patch.object(cast, "_register") as reg:
                r = cast.verify_character_sheets(root, [{"name": "沈砚", "appearance": CARD}],
                                                 log=lambda *a: None, ask=fake_ask)
            self.assertEqual(tur.call_count, 1, "矛盾必须触发一次重画")
            self.assertEqual(r["residual"], [], r)
            self.assertEqual(r["redrawn"], 1, r)
            self.assertTrue(reg.called, "重画成功后必须重新登记参考图")

    def test_still_bad_after_one_redo_is_residual_not_a_loop(self):
        """★ 红线：视觉判据是概率性的，重画**只能一次**，否则就是没有出口的循环。"""
        from .media import cast

        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            n = {"ask": 0}

            def always_wrong(path, app):
                n["ask"] += 1
                return SEEN_WRONG

            with mock.patch.object(cast, "_turnaround", return_value="x") as tur:
                r = cast.verify_character_sheets(root, [{"name": "沈砚", "appearance": CARD}],
                                                 log=lambda *a: None, ask=always_wrong)
            self.assertEqual(tur.call_count, 1, "重画次数必须等于上限，不许循环")
            self.assertEqual(r["residual"], ["沈砚"], r)
            self.assertEqual(n["ask"], 2, "核对两次：重画前 + 重画后")

    def test_switch_off_means_no_calls_at_all(self):
        from . import config
        from .media import cast

        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            with mock.patch.object(config, "SHEET_CHECK", False):
                r = cast.verify_character_sheets(
                    root, [{"name": "沈砚", "appearance": CARD}],
                    log=lambda *a: None, ask=lambda p, a: self.fail("关了就不该调用视觉模型"))
            self.assertEqual(r["checked"], 0, r)

    def test_card_without_visual_items_is_not_judged(self):
        """⚠️ "没判"必须与"通过"分开——否则空卡片会伪装成已对账。"""
        from .media import cast

        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            r = cast.verify_character_sheets(
                root, [{"name": "沈砚", "appearance": "约一米八、身形挺拔"}],
                log=lambda *a: None, ask=lambda p, a: self.fail("无可对账项 ⇒ 不该问模型"))
            self.assertEqual(r["checked"], 0, r)
            self.assertEqual(r["residual"], [], r)

    def test_truncated_image_is_not_judged_and_never_asked(self):
        """写坏/占位的参考图问模型 ⇒ 模型一律答"图上没有" ⇒ 假矛盾 + 白烧一次重画。"""
        from pathlib import Path

        from .media import cast

        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)
            Path(root / "images" / "沈砚.png").write_bytes(b"PNG")     # 3 字节的桩
            r = cast.verify_character_sheets(
                root, [{"name": "沈砚", "appearance": CARD}],
                log=lambda *a: None, ask=lambda p, a: self.fail("图不可读 ⇒ 不该问模型"))
            self.assertEqual(r["checked"], 0, r)
            self.assertEqual(r["residual"], [], r)

    def test_model_failure_does_not_kill_the_chain(self):
        """对账是**附加**环节：视觉调用炸了（没 key / 超时）必须降级为"不判"，不能掀翻 cast。"""
        from .media import cast

        with tempfile.TemporaryDirectory() as td:
            root = self._proj(td)

            def boom(path, app):
                raise RuntimeError("no api key")

            r = cast.verify_character_sheets(root, [{"name": "沈砚", "appearance": CARD}],
                                             log=lambda *a: None, ask=boom)
            self.assertEqual(r["checked"], 1, "确实尝试过核对")
            self.assertEqual(r["residual"], [], r)


    def test_render_style_words_are_not_checked(self):
        """★ 首跑真链路（2026-09-30 xianxia-60s5-0930）报出的 4 条假矛盾。

        「瓷感次表面散射肤质」「成片发丝高光」「扁平剑罡」说的是**成片怎么渲染**、
        **出招时发生什么** —— 白底设定表结构性地不可能画出来，要求它有 = 必判矛盾 +
        白烧一次重画。而同一张卡上的真身份项（旧疤、发髻）必须**留下**，
        否则这条判据就被自己削平了。
        """
        card = ("@柳无咎（灰白交领道袍、深灰长发松束低髻、"
                "瓷感次表面散射肤质、成片发丝高光、挥出时拖一片扁平剑罡）")
        items = sc.items_of(card)
        self.assertEqual(items, ["灰白交领道袍", "深灰长发松束低髻"], items)

    def test_real_identity_items_survive_the_widened_skip_list(self):
        """反向对照：扩表之后，真该拦的两项仍在表内（不然这轮 ep2 的旧疤就漏了）。"""
        card = "@姜屹（左肩一道浅旧疤、眉骨一抹干涸血痕、瓷感肤质）"
        items = sc.items_of(card)
        self.assertEqual(len(items), 2, items)
        self.assertTrue(any("旧疤" in i for i in items), items)

    def test_temporary_pose_does_not_trigger_identity_redraw(self):
        card = '粉色刺绣长裙、站姿带一点前倾的亲近感、左腕铜镯'
        self.assertEqual(sc.items_of(card), ['粉色刺绣长裙', '左腕铜镯'])


if __name__ == "__main__":
    unittest.main()


from v5.media import sheetcheck  # noqa: E402


class AssetSheetJudgement(unittest.TestCase):
    """道具 / 场景图对账（2026-10-07 补）。三向夹具：
    应过 / 应红（白衬衫男人那一类）/ **前提不存在也要 False**（模型答不上来时不许报红）。"""

    def _reply(self, subject="半截宽刃剑", person="否", colors="深青灰的金属", notes="单件"):
        return json.dumps({"subject": subject, "person": person,
                           "colors": colors, "notes": notes}, ensure_ascii=False)

    def test_clean_prop_passes(self):
        card = {"name": "断刃·赤纹", "type": "prop", "prompt": "半截宽刃剑，暗赤雷纹"}
        self.assertEqual(sheetcheck.judge_asset(card, self._reply()), [])

    def test_prop_drawn_as_a_person_is_flagged(self):
        card = {"name": "青霜双鞭", "type": "prop", "prompt": "细长青霜鞭，白霜、末端铜环"}
        got = sheetcheck.judge_asset(
            card, self._reply(subject="一个穿白衬衫的现代男人，两手举着蓝色绳圈",
                              person="是", colors="白衬衫、蓝色"))
        kinds = [k for k, _w in got]
        self.assertIn("主体是真人", kinds, got)

    def test_multi_subject_and_blank_are_flagged(self):
        card = {"name": "验尸房", "type": "location", "prompt": "冷绿日光灯的旧验尸房"}
        self.assertIn("多主体", [k for k, _ in sheetcheck.judge_asset(
            card, self._reply(subject="房间", notes="图上分了三格，多件不相关主体"))])
        self.assertIn("未画出", [k for k, _ in sheetcheck.judge_asset(
            card, self._reply(subject="", notes="纯白背景"))])

    def test_unparseable_reply_is_not_a_red(self):
        card = {"name": "霜铃", "type": "prop", "prompt": "青白铜铃"}
        for bad in ("", "模型今天不想说话", "{不是 JSON}", json.dumps({"foo": 1})):
            self.assertEqual(sheetcheck.judge_asset(card, bad), [],
                             "答不上来一律不判 —— 判据是概率性的，别每次生成都报红")

    def test_negated_collage_observations_are_not_multi_subject(self):
        card = {'name':'白玉佩','type':'prop'}
        for notes in ('单一场景连续空间，无分格拼图，无可读文字',
                      '没有分格，没有可读文字，远处单一主塔轮廓清晰可见',
                      '画面仅包含单一主体，无其他配件，无分格，无可见文字'):
            self.assertEqual(sheetcheck.judge_asset(card,self._reply(notes=notes)), [])
        for notes in ('无分格，但有多个主体', '没有分格却有多件不相关主体',
                      '图上存在拼接，其他部分没有文字'):
            self.assertIn('多主体', [k for k,_ in sheetcheck.judge_asset(card,self._reply(notes=notes))])
