# -*- coding: utf-8 -*-
"""`v5/shotcheck.py` 的离线单测（**不打网络**：模型用假对象注入）。

守的四条最贵的判据：
  1. 语义判定的**阻断权在程序**：引不出逐字原文的一律降级为提醒
     （2026-09-28 审稿角色编造阻断理由、白烧一轮创作链的直接教训）；
  2. 类别白名单：模型自创类别不算；
  3. 解析失败**不算通过**（判不了要响亮，不能伪装成"没问题"）；
  4. 接线：分镜**重跑**时输入里必须出现"只改这几镜 / 已合格就别重写"，
     开关能关掉，且关掉后行为与历史一致。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from . import config, roles, shotcheck, validate

# 列名必须与真实分镜表**一字不差**：`storyboard.parse` 按列名取字段，
# 少一列就解析出 0 镜 —— 那样测试会"全过"却什么都没测到（本项目最忌的假绿）。
SB_HEAD = ("# 分镜\n\n| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 场景 | 视觉风格 | "
           "画面描述 | 落幅 | 对白 | 音效 | 文字镜 | 承接 |\n"
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")


#: 夹具用的四条**互不相同**的动作（表级"整行重复"判据按相似度 ≥0.9 判，
#: 同句模板只换一个词仍会被判成互相抄袭 —— 那是夹具的锅）
_ACTS = ["@裴烛 蹬地前冲劈出板状剑罡，@谢潮生 侧身避开后横移反手格开",
         "@谢潮生 踏碎台面石阶腾起半空，@裴烛 抬剑上撩把剑罡弹向云海",
         "@裴烛 收剑换肩撞进半步，@谢潮生 双臂交叉架住后退三寸",
         "@谢潮生 反手甩出半圈剑光，@裴烛 低头躲过随后踢起一地碎石"]


def _row(name, visual, seconds=3, dialogue="裴烛：接住。", join="承接上镜"):
    # ⚠️ `storyboard.parse` 会把**画面描述少于 15 字**的行当占位符丢掉
    #   （`if len(visual) < 15: continue`）。测试里如果图省事写"两人对峙"，
    #   整张表会解析成 0 镜 —— 测试"全过"却什么都没测到，正是本项目最忌的假绿。
    #   所以这里直接拦住，别让它悄悄失效。
    assert len(visual) >= 15, "画面描述要 ≥15 字，否则 parse 会丢掉这一镜（测了个空）"
    return ("| %s | 中景 | 平视 | 固定 | %d | 石台 | 冷白晨光 | %s | 收在格开 | "
            "%s | 风声 | 否 | %s |\n" % (name, seconds, visual, dialogue, join))


class _Msg:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """按镜号返回预置回复（不回网络）。"""

    def __init__(self, by_name: dict, default: str = '{"violations":[]}'):
        self.by_name, self.default, self.calls = by_name, default, []

    def invoke(self, msgs):
        head = msgs[1]["content"] if isinstance(msgs[1], dict) else msgs[1][1]
        self.calls.append(head)
        for k, v in self.by_name.items():
            if k in head:
                return _Msg(v)
        return _Msg(self.default)


def _shot(name, visual, **kw):
    s = {"name": name, "shot_type": "中景", "angle": "平视", "camera": "固定",
         "seconds": 3, "scene": "石台", "visual": visual,
         "dialogue": "裴烛：接住。", "sfx": "风", "join_note": "承接上镜"}
    s.update(kw)
    return s


class TestQuoteGate(unittest.TestCase):
    def test_real_quote_blocks(self):
        v = _shot("LN02", "@谢潮生 在画面右端作远景虚化剪影无动作，@裴烛 蹬地前冲")
        llm = _FakeLLM({"LN02": json.dumps({"violations": [{
            "code": "opponent_as_background",
            "quote": "@谢潮生 在画面右端作远景虚化剪影无动作",
            "why": "对手被写成不动背景"}]}, ensure_ascii=False)})
        r = shotcheck.judge_shot(v, llm=llm)
        self.assertEqual(len(r["violations"]), 1)
        self.assertEqual(r["violations"][0]["code"], "opponent_as_background")
        self.assertEqual(r["unverifiable"], [])

    def test_fabricated_quote_is_downgraded(self):
        """★ 核心：编出来的原文**不得**当阻断。"""
        v = _shot("LN03", "@裴烛 劈出暗赤板状剑罡，@谢潮生 横剑格开")
        llm = _FakeLLM({"LN03": json.dumps({"violations": [{
            "code": "rock_chopping_as_beat",
            "quote": "@谢潮生 被劈飞的碎石砸中倒地",   # 表里没有这句
            "why": "砍石头"}]}, ensure_ascii=False)})
        r = shotcheck.judge_shot(v, llm=llm)
        self.assertEqual(r["violations"], [], "引不出原文就不该算阻断")
        self.assertEqual(len(r["unverifiable"]), 1)
        self.assertIn("引不出逐字原文", r["unverifiable"][0]["reason"])

    def test_unknown_code_rejected(self):
        v = _shot("LN04", "@裴烛 蹬地前冲三步")
        llm = _FakeLLM({"LN04": '{"violations":[{"code":"plot_hole","quote":"蹬地前冲","why":"x"}]}'})
        r = shotcheck.judge_shot(v, llm=llm)
        self.assertEqual(r["violations"], [])
        self.assertIn("白名单", r["unverifiable"][0]["reason"])

    def test_unparsable_reply_is_an_error_not_a_pass(self):
        v = _shot("LN05", "@裴烛 旋身横扫")
        r = shotcheck.judge_shot(v, llm=_FakeLLM({"LN05": "这一镜我觉得还行。"}))
        self.assertTrue(r["error"], "解析失败必须留下痕迹")
        res = shotcheck.check([v, _shot("LN06", "@裴烛 蹬地扑上，@谢潮生 侧身避开")],
                              use_judge=True, log=lambda *a: None,
                              llm=_FakeLLM({"LN05": "这一镜我觉得还行。"}))
        self.assertTrue(res["blocking"], "判不了的镜不能被当成通过")
        self.assertEqual(len(res["errors"]), 1)


    def test_single_beat_shot_is_not_judged_for_movement_verbs(self):
        """⛔ 一拍镜（收势/定格/短切）不判「位移动词 ≥2」—— 那条要求它满足不了。

        2026-10-08 实测：一条 5 集仙侠的第 1 集收尾镜因此被退回，同一条链被另一条
        误判一起拖着把分镜重写了 6 次、35 分钟零推进，最后被反空转闸掐掉。
        """
        shots = [_shot("LN19", "她收剑立定，马尾与衣摆慢慢落回"),
                 _shot("LN20", "剑尖滴下最后一滴水")]
        names = [h["names"] for h in shotcheck.countable(shots, 120)
                 if "位移动词" in h["check"]]
        self.assertEqual(names, [], "一拍镜不该被这条判到：%s" % names)


class TestCountable(unittest.TestCase):
    def test_structural_laws_fire(self):
        shots = [
            _shot("LN01", "0-3秒：蹬地前冲；3-6秒：横移两步"),        # 两拍
            _shot("LN02", "@裴烛（黑衣）出剑、@谢潮生（白衣）格开、@裴烛（黑衣）再压上"),  # @名（ x3
            _shot("LN03", "0-3秒：两人各自侧身相对；3-6秒：剑势都收着没出手"),  # 两拍且位移动词不足
            _shot("LN04", "@裴烛 劈向崖壁，崖壁崩落", join_note=""),   # 无承接 + 砍环境
        ]
        checks = {h["check"] for h in shotcheck.countable(shots, 120)}
        self.assertTrue(any("4–12 秒" in c for c in checks), checks)   # LN01 写了 3 秒 = 越界
        self.assertNotIn("恰好一拍", "".join(checks),
                         "「每镜恰好一拍」已随 ÷4 除法基线一起废弃（2026-10-03）")
        self.assertTrue(any("同一角色" in c for c in checks), checks)
        self.assertTrue(any("位移动词" in c for c in checks), checks)
        self.assertTrue(any("承接" in c for c in checks), checks)
        self.assertTrue(any("片长" in c for c in checks), checks)

    def test_good_table_passes(self):
        shots = [_shot("LN%02d" % i, _ACTS[(i - 1) % 4] + "，收势落在第 %d 处台阶" % i) for i in range(1, 33)]
        for s in shots:
            s["seconds"] = 4
        hard = shotcheck.countable(shots, 120)
        self.assertEqual([h["check"] for h in hard], [], hard)

    def test_long_shot_plan_follows_brief_not_the_old_5s_norm(self):
        """★ 11 镜 × 12 秒的长镜方案不许被"≤5s 常态"整批判死（2026-10-03 实错）。

        旧实现把 0929 那批短镜项目的节奏写成了硬判据 ⇒ 长镜 brief 一交进来，
        退回清单会逼模型把每一镜改短 —— **判据反过来扼杀 brief 要的东西**。
        现在期望单镜秒数从 brief 推（132 秒 ÷ 11 镜 = 12 秒）。
        """
        def _long(i):
            return _shot("LN%02d" % i,
                         "@裴烛 蹬地前冲劈出板状剑罡，对方侧身避开后横移反手格开",
                         seconds=12)
        plan = [_long(i) for i in range(1, 12)]
        hits = [h for h in shotcheck.countable(plan, 132, target_shots=(11, 11))
                if "单镜时长" in h["check"]]
        self.assertEqual(hits, [], hits)
        # 反向对照 1：brief 没声明镜数 ⇒ **不再拿"≤5s 常态"当规范**（那是 ÷4 时代的节奏），
        # 只守供应商硬区间 4–12 秒：12 秒放行，3 秒点名。
        self.assertEqual([h for h in shotcheck.countable([_long(1)], 12)
                          if "4–12 秒" in h["check"]], [], "12 秒在硬区间内，不该被点名")
        band = [h for h in shotcheck.countable([_long(1), _long(2) | {"seconds": 3}], 15)
                if "4–12 秒" in h["check"]]
        self.assertTrue(band and "LN02" in band[0]["name"], band)
        # 反向对照 2：brief 要 12 秒、表里混进 4 秒的镜 ⇒ 点名那一镜
        mixed = [_long(1), _long(2) | {"seconds": 4}]
        off = [h for h in shotcheck.countable(mixed, 24, target_shots=(2, 2))
               if "单镜时长" in h["check"]]
        self.assertTrue(off and "LN02" in off[0]["name"], off)

    def test_long_shot_must_carry_an_internal_timeline(self):
        """★ 12 秒镜只写一拍 = 把节奏整个交给模型 ⇒ 成片"拖、节点少"（1003c 人眼判定）。

        判据要跟着 brief 的单镜秒数走：长镜**强制**镜内时间轴（≥4 段），
        短镜仍守 0929 那条"恰好一拍"。两半都要测到，否则等于没装守卫。
        """
        four = ("0-3秒：@裴烛 蹬地前冲三步劈下，对方侧身避开；3-6秒：他横移半步反手格开，"
                "剑罡擦着柱身炸开；6-9秒：@裴烛 借势退到石台边，左手按住崖壁稳住身形；"
                "9-12秒：他抬手把断剑插进沙里，镜头停在他手背上")
        one = "@裴烛 蹬地前冲三步劈下，对方侧身避开后横移反手格开，剑罡擦着柱身炸开"
        long_plan = [_shot("LN%02d" % i, one, seconds=12) for i in range(1, 12)]
        hits = [h for h in shotcheck.countable(long_plan, 132, target_shots=(11, 11))
                if "镜内时间轴" in h["check"]]
        self.assertTrue(hits and "11 镜" in hits[0]["detail"], hits)
        # 正向：同样这批镜改成 4 段覆盖满 12 秒 ⇒ 这条不再判（其余判据不受影响）
        fixed = [_shot("LN%02d" % i, four, seconds=12) for i in range(1, 12)]
        self.assertEqual([h for h in shotcheck.countable(fixed, 132, target_shots=(11, 11))
                          if "镜内时间轴" in h["check"]], [])
        # 反向对照：短镜方案（brief 没声明镜数）仍守"恰好一拍"，长镜的放宽不许漏到这里
        # 反向对照：4 秒短镜写两段**不再被判** —— 「每镜恰好一拍」随除法基线一起废弃，
        # 时间轴只强制到 ≥8 秒的镜（仙侠包若要一拍一镜，写在该包自己的契约里）。
        short = [_shot("LN01", "0-2秒：@裴烛 蹬地前冲；2-4秒：对方侧身避开后横移", seconds=4)]
        self.assertEqual([h for h in shotcheck.countable(short, 60)
                          if "镜内时间轴" in h["check"] or "恰好一拍" in h["check"]], [], short)

    def test_10b_two_at_mentions_in_non_wide_shot(self):
        """★ 审稿 10b 该由程序先抓（2026-09-29：它抓到了，代价是一轮分镜重派 + 20 分钟）。

        ⚠️ 2026-10-03 收窄：这条是 `xianxia-vfx-action` 的**包内**律（0927 升为阻断），
        当通则用在都市情感剧上时，"双人近景各 @ 一次"这种正确写法全被误伤
        （`yoga-affair-1003d`：评审据此判停，48 分钟零出片）⇒ 只有声明了它的包才判。
        """
        both = _shot("LN02", "@沈砚 蹬地前冲三步劈下，@阮青 侧身避开后横移反手格开")
        wide = dict(both, name="LN01", shot_type="全景")
        checks = {h["check"] for h in shotcheck.countable(
            [both], 60, ["沈砚", "阮青"], single_at_law=True)}
        self.assertTrue(any("非宽景" in c for c in checks), checks)
        # 反向对照：同样的双 @ 写在**宽景**里必须放行（宽景就该两人同框）
        clean = [h for h in shotcheck.countable([wide], 60, ["沈砚", "阮青"],
                                               single_at_law=True)
                 if "非宽景" in h["check"]]
        self.assertEqual(clean, [])
        # 不传角色名 ⇒ 不判这条（不拿硬编码人名瞎判）
        self.assertEqual([h for h in shotcheck.countable([both], 60, single_at_law=True)
                          if "非宽景" in h["check"]], [])
        # ★ 包没写这条律 ⇒ 整条不判（这才是 shortdrama 这类项目的正确行为）
        self.assertEqual([h for h in shotcheck.countable([both], 60, ["沈砚", "阮青"])
                          if "非宽景" in h["check"]], [],
                         "10b 是包内律，没声明就不许判")

    def test_shot_floor_follows_target_not_a_hardcoded_25(self):
        """60 秒 / 17 镜的片子不许被"写死 25 镜"误判（2026-09-29 实错）。"""
        shots = [_shot("LN%02d" % i, _ACTS[(i - 1) % 4], seconds=4)
                 for i in range(1, 18)]
        hard = shotcheck.countable(shots, 60)
        self.assertEqual([h["check"] for h in hard if "片长" in h["check"]], [], hard)

    def test_contact_count_no_longer_blocks(self):
        """★「兵刃接触 ≥8 镜」这条**已删**（2026-09-29）。

        依据：官方范例逐帧全量看完是 30 秒一镜到底、贴身互搏 3-4 秒、金属相碰**零次**，
        我们却把接触数当阻断判据 ⇒ 比参考片还严；三项目实测越逼接触、真打镜数越少
        （12→4→1）。夹具必须**零接触词**且**真的数出 15 镜**，否则"没有这条判据"是假绿。
        """
        shots = [_shot("LN%02d" % i,
                       "@裴烛 蹬地前冲三步劈下，@谢潮生 侧身避开后横移两步",
                       seconds=4) for i in range(1, 16)]
        self.assertEqual(len(shots), 15, "夹具没凑够镜数 ⇒ 这条测试会假绿")
        self.assertFalse(any(w in s["visual"] for s in shots for w in shotcheck.CONTACT),
                         "夹具里混进了接触词，测不到'零接触不再被拦'")
        checks = [h["check"] for h in shotcheck.countable(shots, 60)]
        self.assertEqual([c for c in checks if "接触" in c], [], checks)

    def test_character_names_fall_back_to_worldbuilder(self):
        """★ 注册表是**媒体链**才写的 —— 体检跑在创作链阶段时它还不存在。

        实错（2026-09-29）：`character_names` 只读 `assets.json` ⇒ 返回空 ⇒
        10b 判据静默不判，而我已经据此报告"程序能抓到审稿那条"。
        """
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name)
        (root / "worldbuilder").mkdir()
        # ⚠️ 外貌段必须 **≥20 字**：`cast.parse_characters` 会把短于 20 字的外貌
        #   当占位符丢弃（实测事故写在它的注释里），fixture 太短会"测了个空"。
        (root / "worldbuilder" / "worldbuilder.md").write_text(
            "# 角色卡：沈砚\n\n## 基本信息\n- 姓名：沈砚\n\n"
            "## 外貌特征（用于生图）\n男性，三十岁上下，身形挺拔，玄黑长发束成高马尾，"
            "肩宽、喉结明显、下颌线硬朗。\n\n"
            "# 角色卡：阮青\n\n## 基本信息\n- 姓名：阮青\n\n"
            "## 外貌特征（用于生图）\n女性，二十多岁，身形修长、腰线明确，"
            "霜白长发高挽成髻，银质云纹发簪横插。\n", encoding="utf-8")
        self.assertFalse((root / "assets.json").exists())
        self.assertEqual(sorted(shotcheck.character_names(root)), ["沈砚", "阮青"])
        # 拿到名字之后，10b 才真的判得动
        shots = [_shot("LN02", "@沈砚 蹬地前冲劈下，@阮青 横剑格开")]
        hits = [h for h in shotcheck.countable(shots, 60,
                                               shotcheck.character_names(root),
                                               single_at_law=True)
                if "非宽景" in h["check"]]
        self.assertEqual(len(hits), 1, hits)

    def test_anchor_repeat_counts_people_not_props(self):
        """★「同一角色被 @ 两次」才是病；「不同角色各 @ 一次」和「道具带括注」都不是。

        1003d 实错：旧实现数的是"一镜里 `@名（` 出现几次"，于是
        `@苏晚（…）` + `@豆绿色瑜伽垫（180 厘米…）` 这种**一人一道具**的正确写法
        也被判成"会多画人"，评审据此把一条链判停。
        """
        people = ["苏晚", "周彦", "何芷"]
        ok = _shot("LN02", "@苏晚（奶白上衣）跪在@豆绿色瑜伽垫（180厘米长）上，"
                          "@周彦（藏青衬衫）伸手，@何芷（豆沙色卫衣）站在门口")
        bad = _shot("LN03", "@苏晚（奶白上衣）跪在垫上；@苏晚（奶白上衣）后仰；@周彦（藏青衬衫）按住她肩")
        hits_ok = [h for h in shotcheck.countable([ok], 60, people) if "同一角色" in h["check"]]
        hits_bad = [h for h in shotcheck.countable([bad], 60, people) if "同一角色" in h["check"]]
        self.assertEqual(hits_ok, [], "三人各 @ 一次 + 一个道具，不该判")
        self.assertEqual(len(hits_bad), 1, hits_bad)


class TestWiring(unittest.TestCase):
    """分镜重跑时，输入里必须带"退回清单 / 已合格就别重写"。"""

    def setUp(self):
        # Keep the nominally compliant 4s fixture from being compressed to 96s.
        patcher = mock.patch.object(config, "VIDEO_PACK_MAX_GROUP", 3)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _root(self, table_rows: str):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "brief.json").write_text(json.dumps(
            {"topic": "接线测试", "pack": "shortdrama", "episodes": 1,
             "target_duration": "约 120 秒"}, ensure_ascii=False), encoding="utf-8")
        (d / "scenedesigner").mkdir()
        (d / "scenedesigner" / "scenedesigner_ep1.md").write_text(
            SB_HEAD + table_rows, encoding="utf-8")
        return d

    def test_rerun_gets_punch_list(self):
        root = self._root(_row("LN01",
                              "0-3秒：甲挥剑刺向乙；3-6秒：乙持刀格挡，两人保持原处"))
        with mock.patch.object(config, "SHOTCHECK", "count"):
            txt = roles.role_input("scenedesigner", root, {"episode_index": 1})
        self.assertIn("用一次 `write_file` 交出去", txt)
        self.assertIn("不要 `edit_file` 逐镜改", txt)
        self.assertIn("不要反复 `read_file`", txt)
        self.assertNotIn("只改列出的那几镜", txt,
                         "旧措辞会把模型逼成逐镜 edit_file + 反复读整表："
                         "实测 111 次工具调用撞穿 ROLE_RECURSION_LIMIT ⇒ 整条链零出片")
        self.assertIn("位移动词", txt, "该镜的具体判据要能看见")

    def test_clean_table_says_do_not_rewrite(self):
        rows = "".join(_row("LN%02d" % i, _ACTS[(i - 1) % 4] + "，收势落在第 %d 处台阶" % i, seconds=4)
                       for i in range(1, 33))
        root = self._root(rows)
        with mock.patch.object(config, "SHOTCHECK", "count"):
            txt = roles.role_input("scenedesigner", root, {"episode_index": 1})
        self.assertIn("已通过程序体检", txt)
        self.assertIn("不要重写", txt)

    def test_switch_off_restores_old_behaviour(self):
        root = self._root(_row("LN01", "两人隔着三身位对峙，谁都没有先出手"))
        with mock.patch.object(config, "SHOTCHECK", "off"):
            txt = roles.role_input("scenedesigner", root, {"episode_index": 1})
        self.assertNotIn("程序体检", txt)

    def test_first_dispatch_has_no_punch_list(self):
        """盘上还没有分镜表 = 第一次派发 ⇒ 不许出现退回清单（那时无从检查）。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "brief.json").write_text('{"topic": "首轮", "pack": "shortdrama"}',
                                      encoding="utf-8")
        with mock.patch.object(config, "SHOTCHECK", "count"):
            txt = roles.role_input("scenedesigner", d, {"episode_index": 1})
        self.assertNotIn("用一次 `write_file` 交出去", txt)

    def test_punch_survives_the_rerun_moving_the_table_away(self):
        """★ 打回重做会**先把旧表移进 `.rerun_backup/`** ⇒ 清单必须落在文件里。

        没有这一步，"退回清单"在真实打回路径上永远不会注入（2026-09-29 设计漏洞）。
        """
        root = self._root(_row("LN01",
                              "0-3秒：甲挥剑刺向乙；3-6秒：乙持刀格挡，两人保持原处"))
        with mock.patch.object(config, "SHOTCHECK", "count"):
            txt1 = roles.role_input("scenedesigner", root, {"episode_index": 1})
        self.assertIn("用一次 `write_file` 交出去", txt1)
        pf = root / "scenedesigner" / "shotcheck_ep1.json"
        self.assertTrue(pf.exists(), "体检结果必须钉在盘上")
        # 模拟打回：表被移走，清单要还在
        sb = root / "scenedesigner" / "scenedesigner_ep1.md"
        backup = root / ".rerun_backup" / "t"
        backup.mkdir(parents=True)
        sb.rename(backup / "scenedesigner_ep1.md")
        with mock.patch.object(config, "SHOTCHECK", "count"):
            txt2 = roles.role_input("scenedesigner", root, {"episode_index": 1})
        self.assertIn("上一版分镜表被打回", txt2)
        self.assertIn("位移动词", txt2)
        # 合格表要清掉清单，否则过期结论会一直喂给角色
        # （fixture 的 brief 目标 120 秒 ⇒ 要 32 镜 × 4 秒才落在 85%-130% 里）
        good = "".join(_row("LN%02d" % i, _ACTS[(i - 1) % 4] + "，收势落在第 %d 处台阶" % i, seconds=4)
                       for i in range(1, 33))
        sb.write_text(SB_HEAD + good, encoding="utf-8")
        with mock.patch.object(config, "SHOTCHECK", "count"):
            txt3 = roles.role_input("scenedesigner", root, {"episode_index": 1})
        self.assertIn("已通过程序体检", txt3)
        self.assertFalse(pf.exists(), "合格后清单必须删除")
        sb.unlink()
        with mock.patch.object(config, "SHOTCHECK", "count"):
            self.assertNotIn("上一版分镜表被打回",
                             roles.role_input("scenedesigner", root, {"episode_index": 1}))


    def test_dialogue_density_rule_only_judges_dialogue_led(self):
        """★「台词镜 ≥50%」只该管 `dialogue-led`。

        实错（2026-09-30 起新链前查到）：这条原先**无条件**生效 ⇒ `silent` 与
        `narration-led` 的项目每镜都不合格——它们的对白列按本包契约统一写
        「（无声，环境音）」（旁白写在**音效**列）。后果不是报错而是白烧一轮重派：
        退回清单会要求分镜"把台词补到一半以上"，而那正好违反它自己的音频模式契约。
        """
        shots = [_shot("LN%02d" % i,
                       "@裴烛 蹬地前冲三步劈下，@谢潮生 侧身避开后横移两步",
                       dialogue="（无声，环境音）", seconds=4) for i in range(1, 16)]
        self.assertEqual(len(shots), 15, "夹具没凑够镜数 ⇒ 这条测试会假绿")
        self.assertTrue(all("（无声" in s["dialogue"] for s in shots))
        for mode in ("silent", "narration-led"):
            checks = [h["check"] for h in shotcheck.countable(shots, 60, audio_mode=mode)]
            self.assertEqual([c for c in checks if "台词镜" in c], [],
                             "%s 模式不该判台词密度：%s" % (mode, checks))
        # 反向对照：同一张表在 dialogue-led 下**必须**判出来（否则这条判据被悄悄废掉）
        checks = [h["check"] for h in shotcheck.countable(shots, 60,
                                                          audio_mode="dialogue-led")]
        self.assertTrue(any("台词镜" in c for c in checks), checks)


def _cl_row(name, camera, style_txt):
    """可配「运镜」「视觉风格」两列的行（其余与 `_row` 同）。"""
    return ("| %s | 中景 | 平视 | %s | 4 | 石台 | %s | 裴烛单手把对方手腕格开、"
            "脚下石板擦出一道痕 | 收在格开 | 裴烛：接住。 | 风声 | 否 | 承接上镜 |\n"
            % (name, camera, style_txt))


class TestCameraLightContract(unittest.TestCase):
    """`camera-light-physics` 的两条可数判据（1003 五臂探针之后接入）。

    探针的账：同一条 12 秒素材、画面描述一字不动，只把「运镜」列从 `缓推`
    换成带速度/行程/终点/静止段的写法，末镜最后两秒的帧间差就从 10.9 掉到
    6.7 与 5.7（同文本两次的抖动带宽 2.8），而**没声明运镜**的那一臂停在 10.8
    ⇒ 有效的是那句写法，不是运气。守的四件事：
      ① 没声明技法 ⇒ 两条**一条不判**（反质量包要的是僵硬锁定机位，判了是误报）；
      ② 旧产物装回来必须**会红**（`缓推` + 无光落点，就是 leak-upstairs 的真实形状）；
      ③ 写清楚了就必须**不报**（否则分镜会被推着往列里灌水）；
      ④ `固定` 与「同上」两种合法写法不许报。
    """

    def _shots(self, *rows):
        from .media import storyboard
        got = storyboard.parse(SB_HEAD + "".join(rows))
        self.assertEqual(len(got), len(rows), "解析出的镜数必须等于行数（否则测了个空）")
        return got

    def test_off_by_default_history_unchanged(self):
        shots = self._shots(_cl_row("1", "缓推", "冷白晨光"))
        checks = [h["check"] for h in shotcheck.countable(shots)]
        self.assertEqual([c for c in checks if "运镜" in c or "光落点" in c], [],
                         "没声明技法就多判了，这会污染所有现役项目：%s" % checks)

    def test_old_wording_is_caught(self):
        hits = {h["check"]: h["name"] for h in
                shotcheck.countable(self._shots(_cl_row("1", "缓推", "冷白晨光")),
                                    camera_light=True)}
        cam = [k for k in hits if "运镜" in k]
        lit = [k for k in hits if "光落点" in k]
        self.assertEqual(len(cam), 1, "只写「缓推」必须被判：%s" % list(hits))
        self.assertEqual(len(lit), 1, "没有光落点必须被判：%s" % list(hits))
        self.assertEqual(hits[cam[0]], "LN01", "退回清单必须带镜号")

    def test_spec_writes_silence_both(self):
        shots = self._shots(_cl_row(
            "1",
            "缓慢向前推近，推进速度0.3m/s，行程0.5米，终点停在她手边，全程保持近景",
            "冷白晨光，光从左上方压下来，颧骨一道高光边，绒面吃光、边缘不发亮"))
        checks = [h["check"] for h in shotcheck.countable(shots, camera_light=True)]
        self.assertEqual([c for c in checks if "运镜" in c or "光落点" in c], [], checks)

    def test_static_camera_not_flagged(self):
        # 「固定」没有位移 ⇒ 不许要求它写速度（包契约本来就允许固定机位）
        shots = self._shots(_cl_row("1", "固定", "冷白晨光，颧骨一道高光边"))
        checks = [h["check"] for h in shotcheck.countable(shots, camera_light=True)]
        self.assertEqual([c for c in checks if "运镜" in c], [], checks)

    def test_inherited_style_exempt(self):
        # 同场景不同镜允许写「同上」（包契约明写），判了就是逼模型逐镜重述
        shots = self._shots(_cl_row("1", "固定", "同上"))
        checks = [h["check"] for h in shotcheck.countable(shots, camera_light=True)]
        self.assertEqual([c for c in checks if "光落点" in c], [], checks)

    def test_punch_list_carries_the_flag(self):
        """`punch_list` 必须把这个开关透下去——角色拿的是那份清单，不是 countable。"""
        shots = self._shots(_cl_row("1", "缓推", "冷白晨光"))
        on = shotcheck.punch_list(shots, use_judge=False, camera_light=True)
        off = shotcheck.punch_list(shots, use_judge=False)
        self.assertTrue(any("运镜" in x for x in on), on)
        self.assertFalse(any("运镜" in x for x in off), off)


from tempfile import TemporaryDirectory


class TestDurationBlocksContradicted(unittest.TestCase):
    """评审的**总时长**阻断理由要拿盘上事实核一遍（2026-10-03 `yoga-affair-1003g`）。

    那条链的燃料就是一句假理由：审稿写「总时长 88s < brief 硬边界 110–150s」，
    而那张 16 镜表的「时长(秒)」列**实际加总 120 秒、正落在带内** ——
    88 = 它只加了 8 镜×8 秒 + 2 镜×12 秒，把 2×4 秒与 4×6 秒整个漏掉。
    驱动器照这条打回 ⇒ 分镜被重派 5 次、46 分钟零出片。

    ⛔ 但驳回必须**窄**：只驳"断言总时长不合格、而程序数出来在带内、且它引用的秒数
    与程序读数不是同一个数"这一类；同一条判决里的别的理由一律原样留下，
    表真的不够长时更要原样留下（反向对照在下面）。
    """

    _HDR = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 场景 | 视觉风格 |"
            " 画面描述 | 落幅 | 对白 | 音效 | 文字镜 | 承接 |")

    def _mk(self, root: Path, secs, target: str = "约 130 秒") -> None:
        (root / "scenedesigner").mkdir(parents=True, exist_ok=True)
        (root / "brief.json").write_text(
            json.dumps({"topic": "核时长", "pack": "shortdrama",
                        "target_duration": target}, ensure_ascii=False),
            encoding="utf-8")
        rows = [self._HDR, "|---|" * 13]
        for i, s in enumerate(secs, 1):
            rows.append("| 1-%d | 中景 | 平视 | 机位固定 | %d | 瑜伽私教室 | 暖橙斜射光 |"
                        " 0-3秒：@苏晚（白色运动上衣）侧对镜头；3-%d秒：@周彦（藏青衬衫）"
                        "手抬到肩 | 落幅 | （无声，环境音） | 环境音 | 否 | — |"
                        % (i, s, max(4, s)))
        (root / "scenedesigner" / "scenedesigner_ep1.md").write_text(
            "\n".join(rows) + "\n", encoding="utf-8")

    _BOGUS = ("总时长 88s < brief 硬边界 110–150s：需并镜/扩长补到 110–150s，"
              "勿只加短镜凑数")
    _REAL = "LN07：关键接触「从背后压上肩」未独占整镜（违反分镜硬要求⑮）"

    def test_program_reads_the_table_and_band(self):
        """夹具必须**真的**被解析出 N 镜（否则所有判据都在 0 条上"全过"）。"""
        from v5.media import storyboard as sb
        with TemporaryDirectory() as d:
            root = Path(d)
            self._mk(root, [12] * 10)
            shots = sb.parse((root / "scenedesigner" / "scenedesigner_ep1.md")
                             .read_text(encoding="utf-8"))
            self.assertEqual(len(shots), 10, "夹具解析出 %d 镜" % len(shots))
            band = shotcheck.duration_band(root, 1)
            self.assertEqual(band["total"], 120, band)
            self.assertTrue(band["ok"], band)

    def test_false_duration_block_is_dropped_but_the_real_one_survives(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            self._mk(root, [12] * 8 + [6] * 4)          # 8×12 + 4×6 = 120 秒，带内
            kept, dropped = shotcheck.filter_contradicted_blocks(
                root, 1, [self._BOGUS, self._REAL])
            self.assertEqual(kept, [self._REAL], "把真理由一起驳了 = 评审彻底失效：" + str(kept))
            self.assertEqual(len(dropped), 1, dropped)
            self.assertIn("程序读数", dropped[0], "驳回必须留下可复核的读数：" + dropped[0])
            self.assertIn("120", dropped[0])

    def test_true_shortfall_is_never_dropped(self):
        """★ 反向对照：表**真的**只有 60 秒时，同一句理由必须原样留下。"""
        with TemporaryDirectory() as d:
            root = Path(d)
            self._mk(root, [6] * 10)                    # 60 秒 < 下限 110
            kept, dropped = shotcheck.filter_contradicted_blocks(
                root, 1, [self._BOGUS, self._REAL])
            self.assertEqual(dropped, [], "表不够长却驳回了 = 把保护拆掉")
            self.assertEqual(len(kept), 2, kept)

    def test_unreadable_table_keeps_everything(self):
        """读不出表 / brief 没写目标秒数 ⇒ 一律"未知"，绝不据此放行。"""
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "brief.json").write_text(
                json.dumps({"topic": "没目标", "pack": "shortdrama"},
                           ensure_ascii=False), encoding="utf-8")
            self.assertIsNone(shotcheck.duration_band(root, 1))
            kept, dropped = shotcheck.filter_contradicted_blocks(
                root, 1, [self._BOGUS, self._REAL])
            self.assertEqual(dropped, [])
            self.assertEqual(len(kept), 2, kept)


class TestSceneLaws(unittest.TestCase):
    """★ 2026-10-05：场 = 一次生成 = 一条 ≤12 秒的请求。口径是「场锁死、镜自由」——
    每场几镜、每镜几秒由分镜师按节拍定，程序只查场这一层。
    """

    _SCENE_KEYS = ("每场", "同一场", "场数")

    def _scene_hits(self, shots, target):
        return [h for h in shotcheck.countable(shots, target)
                if any(k in h["check"] for k in self._SCENE_KEYS)]

    def test_no_act_column_means_no_scene_checks(self):
        """没写「场次」列的项目一条场级判据都不许出（旧项目行为一字不变）。"""
        shots = [_shot("LN01", "@裴烛 蹬地前冲劈出剑罡，对方侧身避开后横移格开", seconds=9),
                 _shot("LN02", "@裴烛 反手压上，对方退两步撞开纸伞", seconds=9)]
        self.assertEqual(self._scene_hits(shots, 18), [],
                         "没有场号却出场级判据 = 误伤所有老项目")

    def test_scene_over_12s_and_one_shot_scene_are_flagged(self):
        shots = [
            _shot("LN01", "@裴烛 蹬地前冲劈出剑罡，对方侧身避开后横移格开",
                  seconds=8, act=1),
            _shot("LN02", "@裴烛 反手压上，对方退两步撞开纸伞，两人换位",
                  seconds=8, act=1),      # 场1 = 16 秒 > 一条请求上限
            _shot("LN03", "@谢潮生 独立一镜撑起长枪，枪缨抖开",
                  seconds=6, act=2),      # 场2 只 1 镜
        ]
        hits = self._scene_hits(shots, 22)
        names = [h["check"] for h in hits]
        self.assertTrue(any("≤12" in c for c in names), hits)
        self.assertTrue(any("≥2 镜" in c for c in names), hits)
        over = [h for h in hits if "≤12" in h["check"]][0]
        self.assertIn("场1=16", over["detail"], over["detail"])

    def test_half_second_shots_are_not_flagged_when_scenes_are_used(self):
        """★ 2026-10-05：有场次列 ⇒ 秒数地板挂在**场**上，不挂在镜上。

        快切正反打（0.5 秒一镜）以前会被"每镜必须 4–12 秒"整批点名退回，
        而供应商那条 [4,12] 管的是一条请求（= 一场）的时长。
        """
        shots = []
        for i in range(1, 9):        # 场1、场2 各 4 镜 × 1.5 秒 = 6 秒（快切正反打）
            shots.append(_shot("LN%02d" % i,
                               "@裴烛 蹬地前冲劈出剑罡，对方侧身避开后横移格开",
                               seconds=1.5, act=1 + (i - 1) // 4))
        hits = shotcheck.countable(shots, 12)
        self.assertEqual([h for h in hits if "4–12 秒" in h["check"]], [],
                         "有场次列时不该再有每镜 4 秒地板：%s" % hits)
        self.assertEqual([h for h in hits if "每场" in h["check"]], [], hits)

    def test_scene_shorter_than_request_floor_is_flagged(self):
        """反向对照：场总长 2 秒（低于请求级 4 秒）必须点名——那条请求会被接口拒。"""
        shots = [_shot("LN01", "@裴烛 蹬地前冲劈出剑罡，对方侧身避开", seconds=1, act=1),
                 _shot("LN02", "@裴烛 反手压上，对方退两步", seconds=1, act=1)]
        hits = [h for h in shotcheck.countable(shots, 2) if "≥4" in h["check"]]
        self.assertTrue(hits, hits)
        self.assertIn("场1=2", hits[0]["detail"], hits[0]["detail"])

    def test_scene_split_across_table_is_flagged(self):
        """场号回头（场1、场2、又场1）必须点名——同场拆开 = 打包档切两条。"""
        shots = [
            _shot("LN01", "@裴烛 蹬地前冲劈出剑罡，对方侧身避开", seconds=5, act=1),
            _shot("LN02", "@谢潮生 横移两步撑起长枪，枪缨抖开", seconds=5, act=2),
            _shot("LN03", "@裴烛 反手压上，对方退两步撞开纸伞", seconds=5, act=1),
        ]
        hits = [h for h in self._scene_hits(shots, 15) if "连写" in h["check"]]
        self.assertTrue(hits, hits)
        self.assertIn("LN03", hits[0]["detail"], hits[0]["detail"])

    def test_compliant_scene_table_passes(self):
        """反向对照：3 场 × 每场 2 镜 × 每镜 5 秒 = 30 秒 ⇒ 场级一条都不该出。"""
        shots = []
        for a in (1, 2, 3):
            shots.append(_shot("LN%02d" % (2 * a - 1),
                               "@裴烛 蹬地前冲劈出板状剑罡，对方侧身避开后横移格开",
                               seconds=5, act=a))
            shots.append(_shot("LN%02d" % (2 * a),
                               "@裴烛 反手压上，对方退两步撞开纸伞，两人换位",
                               seconds=5, act=a))
        self.assertEqual(self._scene_hits(shots, 30), [], self._scene_hits(shots, 30))


class TestProductionScope(unittest.TestCase):
    def test_noncombat_does_not_require_dodging_or_combat_movement(self):
        shots = [_shot("LN01", "0-4秒：她开口提出同行；4-8秒：对方面向她回答",
                       seconds=8, dialogue="甲：我想和你一起下山，我们一起走吧。")]
        checks = [h["check"] for h in shotcheck.countable(shots, combat=False)]
        self.assertFalse(any("应招" in c or "位移动词" in c for c in checks))
        battle = [h["check"] for h in shotcheck.countable(shots, combat=True)]
        self.assertTrue(any("应招" in c for c in battle))

    def test_two_story_scenes_can_have_six_single_shot_requests(self):
        actions = ["她伸手递出玉佩，对方接住后握稳", "她松手收回衣袖，对方把玉放在右手",
                   "她转身朝门走去，对方停在立柱旁", "她在石阶前回头，对方提出想一起走",
                   "她伸出空着的左手，对方以右手牵住", "两人并肩迈下石阶，仍牵手走向云海"]
        shots = [_shot("LN%02d" % (i+1), text, seconds=10, act=1 if i<3 else 2,
                       dialogue="甲：我不是要你一个人离开，把这枚玉交给你，是想和你一起走下山去。")
                 for i, text in enumerate(actions)]
        with mock.patch.object(config, "VIDEO_PACK_MAX_GROUP", 6):
            hits = shotcheck.countable(shots, 60, combat=False, auto_groups=True)
        self.assertFalse(any("每场" in h["check"] or "场数" in h["check"] for h in hits), hits)
        self.assertFalse(any("片长与镜数" in h["check"] or "实际请求" in h["check"] for h in hits), hits)

    def test_compressed_actual_duration_is_checked(self):
        shots = [_shot("LN%02d" % (i+1), "玉石门廊晨光中人物沿着石阶走出", seconds=4, act=1,
                       dialogue="（无声，环境音）") for i in range(15)]
        with mock.patch.object(config, "VIDEO_PACK_MAX_GROUP", 6):
            hits = shotcheck.countable(shots, 60, audio_mode="silent", combat=False, auto_groups=True)
        self.assertTrue(any("实际请求合计" in h["check"] for h in hits), hits)

    def test_role_checks_derive_scope_from_actual_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "brief.json").write_text('{"target_duration":"约12秒","audio_mode":"dialogue-led"}', encoding="utf-8")
            folder = root / "scenedesigner"
            folder.mkdir()
            path = folder / "scenedesigner_ep1.md"
            path.write_text(SB_HEAD + _row("LN01", "她空手向前一步，开口询问对方是否同行", seconds=12), encoding="utf-8")
            with mock.patch.object(config, "VIDEO_MODE", "pack"), mock.patch.object(config, "SHOT_COVERAGE", True):
                self.assertFalse(roles.storyboard_check_args(root, 1)["combat"])
                self.assertTrue(roles.storyboard_check_args(root, 1)["auto_groups"])
                path.write_text(SB_HEAD + _row("LN01", "她挥剑刺向对方肩侧，对方格挡随即后退", seconds=12), encoding="utf-8")
                self.assertTrue(roles.storyboard_check_args(root, 1)["combat"])


if __name__ == "__main__":
    unittest.main()


class TestTableLevelDefects(unittest.TestCase):
    """表级两条（2026-10-08）：两镜成段重复 / 表写断了。

    起因：一条 5 集仙侠的表被模型重写 5 遍，**每遍后半段都在抄自己开头**，
    而当时没有任何判据抓这两件事 ⇒ 每轮打回的理由都是别的条目 ⇒ 迭代到上限收工。
    """

    def test_duplicate_prose_is_flagged_but_anchor_reuse_is_not(self):
        _body = ("右脚前撑成低桩、左腿由高起处沿一条直线弹起、脚尖先过对方肩线、"
                 "青碧火星沿直线拖成剑芒弧、肩背线条压到极低、落地时单腿前落踩住台面石缝")
        dup = [{"name": "LN01", "visual": "@顾青猗（青碧短打）" + _body},
               {"name": "LN02", "visual": "@岑墨（黯金劲装）" + _body}]
        self.assertEqual([("LN01", "LN02")], shotcheck.duplicate_prose(dup))
        ok = [{"name": "LN01", "visual": "@顾青猗（青碧短打）右脚前撑成低桩、左腿弹起"},
              {"name": "LN02", "visual": "@顾青猗（青碧短打）横移半步、枪尖斜指、肩线压低"}]
        self.assertEqual([], shotcheck.duplicate_prose(ok),
                         "锚点复述是契约要求，不能被判成重复")

    def test_truncated_table_is_flagged(self):
        head = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 场景 | 视觉风格 | 画面描述 | 落幅 | 对白 | 音效 | 文字镜 | 承接 |\n"
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        good = head + "| 1 | 近景 | 平视 | 推 | 6 | 云海 | 风格 | 内容 | 落幅 | （无声，环境音） | 声 | 否 | 开场镜 |"
        self.assertEqual("", shotcheck.table_truncated(good))
        cut = head + "| 1 | 近景 | 平视 | 推 | 6 | 云海 | 风格 | 内容 | 落幅 | （无声，环境音） | 声 | 否 | 镜头10落幅："
        self.assertIn("写断", shotcheck.table_truncated(cut))


class TestPerEpisodeVisualReqs(unittest.TestCase):
    """三条「brief 声明了才判」的视觉要求（2026-10-08 用户看片：场景太少、运镜太少）。

    实测依据：第 1 集 22/22 镜同一个「场景」、第 2 集 9/9 同一个，
    且第 2 集 9 镜里 3 镜的「运镜」列只有「定住／仅 0.1 秒微震」。
    """

    def _rows(self, n, scenes=None, cameras=None):
        scenes = scenes or ["石台"]
        cameras = cameras or ["缓推 1 米、终点停在她手边"]
        return [_shot("LN%02d" % i,
                      "@裴烛 蹬地前冲三步劈下，@谢潮生 侧身避开后横移两步、手腕被格开",
                      seconds=4, scene=scenes[i % len(scenes)],
                      camera=cameras[i % len(cameras)])
                for i in range(1, n + 1)]

    def _hits(self, shots, **kw):
        return [h["check"] for h in shotcheck.countable(shots, len(shots) * 4, **kw)]

    def test_single_scene_fires_when_declared(self):
        hits = self._hits(self._rows(9), min_scenes=3)
        one = [c for c in hits if "空间" in c]
        self.assertEqual(len(one), 1, hits)
        d = [h for h in shotcheck.countable(self._rows(9), 36, min_scenes=3)
             if "空间" in h["check"]][0]
        self.assertIn("石台 9 镜", d["detail"], "退回清单必须报**镜数分布**，否则角色不知道差在哪")

    def test_three_scenes_pass(self):
        """反向对照：三个可辨空间 ⇒ 不判。"""
        hits = self._hits(self._rows(9, scenes=["石台", "丹房", "断桥"]), min_scenes=3)
        self.assertEqual([c for c in hits if "空间" in c], [], hits)

    def test_undeclared_never_judges(self):
        """前提不存在：brief 没声明 ⇒ 一集一个空间也不报（行为与改造前一字不变）。"""
        self.assertEqual([c for c in self._hits(self._rows(9)) if "空间" in c], [])

    def test_locked_cameras_capped_only_when_declared(self):
        shots = self._rows(9)
        for i, s in enumerate(shots):        # 前 3 镜"定住且无位移"，后 6 镜有位移
            s["camera"] = ("贴地定住、仅 0.1 秒微震" if i < 3
                           else "缓推 1 米、终点停在她手边")
        hits = [h for h in shotcheck.countable(shots, 36, max_locked=2) if "定住" in h["check"]]
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0]["name"], "LN01、LN02、LN03", "必须**列全**被点名的镜")
        self.assertEqual([c for c in self._hits(shots, max_locked=-1) if "定住" in c], [],
                         "没声明就不许判")

    def test_locked_word_with_movement_is_not_flagged(self):
        """「跟移 2 米后定住」有位移方向 ⇒ 不算锁定镜。"""
        shots = self._rows(9, cameras=["跟移 2 米后定住、0.2 秒微震"])
        self.assertEqual([c for c in self._hits(shots, max_locked=0) if "定住" in c], [])

    def test_direction_diversity(self):
        two = self._rows(9, cameras=["缓推 1 米", "拉远 2 米"])
        self.assertEqual(len([c for c in self._hits(two, min_dirs=4) if "方向去重" in c]), 1)
        four = self._rows(9, cameras=["缓推 1 米", "拉远 2 米", "环绕半圈", "横移两步"])
        self.assertEqual([c for c in self._hits(four, min_dirs=4) if "方向去重" in c], [])


class TestBriefVisualFields(unittest.TestCase):
    """两个新 brief 字段的读法：认散文里的数，读不到就**不判**。"""

    def test_min_scenes(self):
        self.assertEqual(validate.min_scenes_per_episode({"每集空间数下限": "至少 3 个"}), 3)
        self.assertEqual(validate.min_scenes_per_episode({}), 0)
        self.assertEqual(validate.min_scenes_per_episode({"每集空间数下限": 1}), 0,
                         "1 个空间就是现状本身，判它没有意义")

    def test_camera_reqs(self):
        self.assertEqual(validate.camera_reqs({"锁定机位上限": "≤2 镜", "运镜方向下限": "4 种"}),
                         (2, 4))
        self.assertEqual(validate.camera_reqs({}), (-1, 0),
                         "没写 = -1（不判），不是 0（一镜都不许）")
        self.assertEqual(validate.camera_reqs({"锁定机位上限": 0}), (0, 0))


class TestDurationAdvice(unittest.TestCase):
    """片长不合格时，退回清单得说"**每镜写几秒**"，不能只报总数（1008 第 3 集）。"""

    def _rows(self, n, sec):
        return [_shot("LN%02d" % i,
                      "@裴烛 蹬地前冲三步劈下，@谢潮生 侧身避开后横移两步、手腕被格开",
                      seconds=sec) for i in range(1, n + 1)]

    def _detail(self, shots, target):
        hits = [h for h in shotcheck.countable(shots, target) if "片长" in h["check"]]
        return hits[0]["detail"] if hits else ""

    def test_over_length_says_seconds_per_shot(self):
        d = self._detail(self._rows(10, 12), 72)        # 10 镜 × 12 秒 = 120 秒
        self.assertIn("每镜平均 ≤ 9.7 秒", d, d)        # 72×1.35 ÷ 10 = 9.72
        self.assertIn("别只减镜数", d, d)

    def test_under_length_says_the_other_direction(self):
        d = self._detail(self._rows(10, 4), 72)         # 10 镜 × 4 秒 = 40 秒
        self.assertIn("每镜平均 ≥ 6.1 秒", d, d)         # 72×0.85 ÷ 10 = 6.12

    def test_in_band_gets_no_advice(self):
        """反向对照：合格的表不多嘴（旧 detail 一字不变）。"""
        d = self._detail(self._rows(10, 8), 72)
        self.assertEqual(d, "", d)


class TestStoryboardComplianceProbe(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(config, "VIDEO_PACK_MAX_GROUP", 3)
        patcher.start()
        self.addCleanup(patcher.stop)
    """`roles.storyboard_is_compliant` —— 反空转闸的"这张表已经合格了"判据。

    必须是**零额度**（只跑可数那层）且与退回清单**同一组参数**，
    否则驱动器每 20 秒轮询一次就会去送模型、或者两处判据各说一套。
    """

    HDR = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 场景 | 视觉风格 | 画面描述 "
           "| 落幅 | 对白 | 音效 | 文字镜 | 承接 |")
    SEP = "|---|" * 13
    ACTS = ("@裴烛 蹬地前冲三步劈下，@谢潮生 侧身避开后横移两步",
            "@谢潮生 反手格开，@裴烛 拧身跃起落下，石板被踩碎",
            "@裴烛 甩剑划出整圈光弧，@谢潮生 仰身后倒滑步躲开",
            "@谢潮生 压上逼退两步，@裴烛 旋身横移把剑尖挑向他手腕")

    def _root(self, d, extra=None):
        root = Path(d)
        (root / "scenedesigner").mkdir(parents=True, exist_ok=True)
        b = {"topic": "t", "pack": "shortdrama", "genre": "悬疑", "episodes": 1,
             "target_duration": "约 120 秒", "protagonist": "裴烛",
             "must_have": ["甲乙在崖顶交手三次"], "key_props": ["旧刀"],
             "禁忌": ["无可读文字"], "tone": "冷", "结局": "定格"}
        b.update(extra or {})
        (root / "brief.json").write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
        rows = [self.HDR, self.SEP]
        for i in range(1, 33):
            rows.append("| LN%02d | 中景 | 平视 | 固定 | 4 | 崖顶 | 逆光高光在剑脊 | %s，"
                        "收势落在第 %d 处台阶 | 剑尖前点 | 裴烛：接住。 | 风声 | 否 | 承接上一镜 |"
                        % (i, self.ACTS[(i - 1) % 4], i))
        (root / "scenedesigner" / "scenedesigner_ep1.md").write_text(
            "\n".join(rows) + "\n", encoding="utf-8")
        return root

    def test_compliant_table_returns_true(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIs(roles.storyboard_is_compliant(self._root(d), 1), True)

    def test_declared_requirements_make_it_false(self):
        """同一张表，brief 一声明三条 ⇒ 立刻不合格（单场景 + 32 镜全"固定"）。"""
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, extra={"每集空间数下限": 3, "锁定机位上限": 2,
                                        "运镜方向下限": 4})
            self.assertIs(roles.storyboard_is_compliant(root, 1), False)
            kw = roles.storyboard_check_args(root, 1)
            self.assertEqual((kw["min_scenes"], kw["max_locked"], kw["min_dirs"]), (3, 2, 4),
                             "参数没从 brief 传进来 ⇒ 闸与退回清单就会各说一套")
            self.assertEqual(kw["target_seconds"], 120)

    def test_no_table_is_not_a_verdict(self):
        """表不在 ⇒ None（**不许**当成"已合格"，也不许当成"没合格"）。"""
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d)
            (root / "scenedesigner" / "scenedesigner_ep1.md").unlink()
            self.assertIsNone(roles.storyboard_is_compliant(root, 1))
            self.assertEqual(roles.storyboard_check_args(root, 1), {})
