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
        self.assertTrue(any("恰好一拍" in c for c in checks), checks)
        self.assertTrue(any("@名（" in c for c in checks), checks)
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

    def test_10b_two_at_mentions_in_non_wide_shot(self):
        """★ 审稿 10b 该由程序先抓（2026-09-29：它抓到了，代价是一轮分镜重派 + 20 分钟）。"""
        both = _shot("LN02", "@沈砚 蹬地前冲三步劈下，@阮青 侧身避开后横移反手格开")
        wide = dict(both, name="LN01", shot_type="全景")
        checks = {h["check"] for h in shotcheck.countable([both], 60, ["沈砚", "阮青"])}
        self.assertTrue(any("非宽景" in c for c in checks), checks)
        # 反向对照：同样的双 @ 写在**宽景**里必须放行（宽景就该两人同框）
        clean = [h for h in shotcheck.countable([wide], 60, ["沈砚", "阮青"])
                 if "非宽景" in h["check"]]
        self.assertEqual(clean, [])
        # 不传角色名 ⇒ 不判这条（不拿硬编码人名瞎判）
        self.assertEqual([h for h in shotcheck.countable([both], 60)
                          if "非宽景" in h["check"]], [])

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
                                               shotcheck.character_names(root))
                if "非宽景" in h["check"]]
        self.assertEqual(len(hits), 1, hits)


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
        self.assertIn("只改列出的那几镜", txt)
        self.assertIn("不要逐镜自查整张表", txt)
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
        self.assertNotIn("只改列出的那几镜", txt)

    def test_punch_survives_the_rerun_moving_the_table_away(self):
        """★ 打回重做会**先把旧表移进 `.rerun_backup/`** ⇒ 清单必须落在文件里。

        没有这一步，"退回清单"在真实打回路径上永远不会注入（2026-09-29 设计漏洞）。
        """
        root = self._root(_row("LN01", "两人隔着三身位对峙，谁都没有先出手"))
        with mock.patch.object(config, "SHOTCHECK", "count"):
            txt1 = roles.role_input("scenedesigner", root, {"episode_index": 1})
        self.assertIn("只改列出的那几镜", txt1)
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


if __name__ == "__main__":
    unittest.main()
