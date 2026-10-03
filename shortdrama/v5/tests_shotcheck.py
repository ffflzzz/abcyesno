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

from . import config, roles, shotcheck

# 列名必须与真实分镜表**一字不差**：`storyboard.parse` 按列名取字段，
# 少一列就解析出 0 镜 —— 那样测试会"全过"却什么都没测到（本项目最忌的假绿）。
SB_HEAD = ("# 分镜\n\n| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 场景 | 视觉风格 | "
           "画面描述 | 落幅 | 对白 | 音效 | 文字镜 | 承接 |\n"
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")


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


class TestCountable(unittest.TestCase):
    def test_structural_laws_fire(self):
        shots = [
            _shot("LN01", "0-3秒：蹬地前冲；3-6秒：横移两步"),        # 两拍
            _shot("LN02", "@裴烛（黑衣）出剑、@谢潮生（白衣）格开、@裴烛（黑衣）再压上"),  # @名（ x3
            _shot("LN03", "两人对峙"),                                # 位移动词不足
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
        shots = [_shot("LN%02d" % i,
                       "@裴烛 蹬地前冲劈出板状剑罡，@谢潮生 侧身避开后横移反手格开")
                 for i in range(1, 33)]
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
        shots = [_shot("LN%02d" % i,
                       "@裴烛 蹬地前冲劈出板状剑罡，@谢潮生 侧身避开后横移反手格开",
                       seconds=4) for i in range(1, 18)]
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
        root = self._root(_row("LN01", "两人隔着三身位对峙，谁都没有先出手"))
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
        rows = "".join(_row("LN%02d" % i,
                            "@裴烛 蹬地前冲劈出板状剑罡，@谢潮生 侧身避开后横移反手格开",
                            seconds=4) for i in range(1, 33))
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
        root = self._root(_row("LN01", "两人隔着三身位对峙，谁都没有先出手"))
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
        good = "".join(_row("LN%02d" % i,
                            "@裴烛 蹬地前冲劈出板状剑罡，对方侧身避开后横移反手格开",
                            seconds=4) for i in range(1, 33))
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


if __name__ == "__main__":
    unittest.main()
