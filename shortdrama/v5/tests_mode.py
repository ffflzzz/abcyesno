# -*- coding: utf-8 -*-
"""`v5.mode`（剧本直出模式）的自测。

## 这些测试在钉什么

**钉「默认路径没被改坏」** —— 6 个测试全部围绕一条：`full` 模式的行为必须与
`guards.PREREQ` 逐字节相等。因为全仓有 3 处测试硬编码了「7」
（`tests_flow.py:2951` / `tests_hitl.py:199` / `tests_webchain.py:736`），
它们一个字都不用改 ⇒ 「没改坏」有了客观判据，而不是靠人说了算。

**钉 from_script 的四条边界** —— 派工名单、门名单、预置产物、未知模式回落。
"""
from __future__ import annotations

import json
import unittest

from . import guards, mode
from .mode import write_preseed


def _b(**kw) -> dict:
    b = {"mode": "full"}
    b.update(kw)
    return b


class TestDefaultUnchanged(unittest.TestCase):
    """★ 默认路径必须与改动前**逐字节一致** —— 这一组是整个改动的安全底座。"""

    def test_full_roles_equal_prereq_keys(self):
        self.assertEqual(set(mode.roles_of(_b())),
                         set(guards.PREREQ),
                         "full 模式的派工名单必须等于 PREREQ 的全部键")

    def test_full_order_is_prereq_order(self):
        self.assertEqual(list(mode.roles_of(_b())),
                         list(guards.PREREQ),
                         "顺序也要一致 —— hitl.prev_role_of 按顺序判「上一个是谁」")

    def test_full_is_still_seven(self):
        """与 tests_flow:2951 / tests_hitl:199 的硬编码断言同值。"""
        self.assertEqual(len(mode.roles_of(_b())), 7)
        self.assertEqual(len(mode.gate_roles_of(_b())), 7)

    def test_no_brief_is_full(self):
        """brief 读不到（老项目 / media-only）⇒ 一律 full，不得崩。"""
        for b in (None, {}, [], "", {"mode": ""}):
            self.assertEqual(mode.mode_of(b), "full", repr(b))
            self.assertEqual(mode.roles_of(b), tuple(guards.PREREQ), repr(b))

    def test_full_preseed_is_empty(self):
        self.assertEqual(mode.preseed(_b()), {},
                         "full 模式绝不能预置任何产物 —— 那会覆盖真实角色产物")

    def test_full_no_extract_hint(self):
        """full 模式的 worldbuilder 必须继续自由创作，不能被抽取指令污染。"""
        self.assertNotIn("剧本直出", mode.EXTRACT_ONLY_HINT[:0] or "")
        self.assertEqual(mode.roles_of(_b())[0], "worldbuilder")
        self.assertIn("worldbuilder", mode.roles_of(_b()))


class TestUnknownModeFallsBackLoudly(unittest.TestCase):
    """未知模式**不拦住链**（长任务起不来是最贵的事），但必须**响亮**。"""

    def test_falls_back_to_full(self):
        self.assertEqual(mode.mode_of({"mode": "form_script"}), "full",
                         "拼错 ⇒ 按 full 处理，绝不抛异常")

    def test_warns_loudly(self):
        w = mode.warn_unknown({"mode": "form_script"})
        self.assertIn("form_script", w)
        self.assertIn("full", w)
        self.assertIn("剧本直出", w)

    def test_no_warn_for_legal(self):
        for m in mode.MODES:
            self.assertEqual(mode.warn_unknown({"mode": m}), "", m)
        self.assertEqual(mode.warn_unknown(None), "")
        self.assertEqual(mode.warn_unknown({}), "")


class TestFromScriptRoles(unittest.TestCase):
    """from_script：省掉两次 LLM 调用，其余照旧。"""

    def setUp(self):
        self.b = _b(mode="from_script", script="第1场 焦土战场……")

    def test_skips_plot_and_scriptwriter(self):
        got = set(mode.roles_of(self.b))
        self.assertNotIn("plotdesigner", got)
        self.assertNotIn("scriptwriter", got)
        self.assertEqual(len(got), 5, got)

    def test_keeps_the_liveness_critical_three(self):
        """worldbuilder / assetdesigner / scenedesigner / reviewer 一个都不能少。"""
        got = set(mode.roles_of(self.b))
        for r in ("worldbuilder", "assetdesigner", "scenedesigner", "reviewer"):
            self.assertIn(r, got, r)
        self.assertEqual(mode.roles_of(self.b)[-1], "reviewer",
                         "reviewer 必须是最后一个 —— 它是唯一在烧配额前拦人的")

    def test_order_is_subsequence_of_prereq(self):
        """顺序必须是 PREREQ 的**子序列**（不能重排）——
        hitl.prev_role_of / pending 的「下一个该派谁」按顺序判。"""
        full = list(guards.PREREQ)
        got = list(mode.roles_of(self.b))
        it = iter(full)
        self.assertTrue(all(any(x == y for y in it) for x in got), got)

    def test_gate_roles_includes_preseeded(self):
        """⚠️ 门**照查** scriptwriter —— 查它是为了「产物被人删了能被拦」，
        不是为了让门放行。⇒ 派工 5 个、门查 6 个。"""
        self.assertIn("scriptwriter", mode.gate_roles_of(self.b))
        self.assertEqual(len(mode.roles_of(self.b)), 5)
        self.assertEqual(len(mode.gate_roles_of(self.b)), 6,
                         "门比派工多一个 = scriptwriter（产物由 preseed 满足）")

    def test_mode_arg_overrides_brief(self):
        """显式传 mode 时以它为准（调用方按项目取，不必先构造 brief）。"""
        self.assertEqual(len(mode.roles_of(_b(), mode="from_script")), 5)
        self.assertEqual(len(mode.roles_of(_b(mode="from_script"), mode="full")), 7)


class TestPreseed(unittest.TestCase):
    """预置产物：台词落盘、剧本逐字不动。"""

    def test_preseed_scriptwriter(self):
        s = "第1场 焦土战场\n女弓手拉满弓，箭矢贯穿敌兵胸膛。"
        p = mode.preseed(_b(mode="from_script", script=s))
        self.assertIn("scriptwriter", p)
        self.assertEqual(p["scriptwriter"], s, "剧本必须**逐字**落盘 —— 台词的唯一真相源")

    def test_preseed_empty_when_full(self):
        self.assertEqual(mode.preseed(_b(script="随便")), {})

    def test_preseed_empty_when_no_script(self):
        """⚠️ `mode=from_script` 但 `script` 为空 ⇒ **不预置**。
        预置一个空文件会让物化守卫判「产物为空」⇒ 反而判 failed。"""
        p = mode.preseed({"mode": "from_script"})
        self.assertEqual(p, {})

    def test_stripped_script(self):
        p = mode.preseed(_b(mode="from_script", script="\n\n  第1场 焦土  \n\n"))
        self.assertEqual(p["scriptwriter"].strip(), "第1场 焦土")


class TestHints(unittest.TestCase):
    """注入文案本身也要被测 —— 它们是「唯一能到达分镜师」的通道。"""

    def test_bare_int_hint_forbids_timecode(self):
        h = mode.BARE_INT_HINT
        self.assertIn("裸整数", h)
        self.assertIn("0:45", h, "必须**举出**这个反例 —— 抽象说「不要填时间码」模型会忽略")

    def test_bare_int_hint_explains_silent_failure(self):
        """必须说清「不报错」—— 否则模型判断「报错了我就知道不对」。"""
        self.assertIn("不报错", mode.BARE_INT_HINT)

    def test_bare_int_hint_states_real_bounds(self):
        self.assertIn("4–12", mode.BARE_INT_HINT)
        self.assertIn("85%", mode.BARE_INT_HINT)

    def test_extract_hint_forbids_card_drift(self):
        """⚠️ 必须钉住「标题写成别的 ⇒ 解析出 0 个角色」——
        这是记忆里已发生的静默事故（方案 D 撤销）。"""
        h = mode.EXTRACT_ONLY_HINT
        self.assertIn("## 角色卡", h)
        self.assertIn("0 个角色", h)

    def test_extract_hint_forbids_inventing_world(self):
        self.assertIn("不要", h := mode.EXTRACT_ONLY_HINT)
        self.assertIn("世界观", h)


class TestScriptOf(unittest.TestCase):
    def test_reads_script_key(self):
        self.assertEqual(mode.script_of({"script": "  剧本  "}), "剧本")
        self.assertEqual(mode.script_of({}), "")
        self.assertEqual(mode.script_of(None), "")
        self.assertTrue(mode.has_script({"script": "x"}))
        self.assertFalse(mode.has_script({}))


class TestScriptReachesTheRolesThatNeedIt(unittest.TestCase):
    """★★ 2026-10-04 实测补上的**真缺口**：预置产物必须仍作为「只读上游」注入。

    ## 缺口是怎么发现的（不是想出来的，是跑出来的）

    `prereq_for` 把 `scriptwriter` 从「要派发的角色」里删掉了 ——
    这是**对的**（它不再被派发）。但 `role_input` 的上游清单是**按 `PREREQ` 生成的**
    ⇒ 剧本从清单里**一起消失**了。实测打印：

        分镜师读到剧本: False | 清单含 scriptwriter 路径: False

    而分镜师**必须**读剧本（逐字照抄台词、按时间码排节拍）⇒ 这是**静默的能力缺失**：
    不报错、门也过，只是分镜师在凭 brief 的四句话瞎编。

    ⇒ 修法：`PRESEEDED` 里的角色**在盘就注入**，尽管它不在 `prereq` 里。
    ⚠️ 不能用 `PREREQ` 原样注入 —— 那会把 `plotdesigner` 带回来，
    而它本模式**根本没有产物**（`_upstream_block` 只能返回「读不到」）。
    """

    def _root(self):
        import tempfile
        from pathlib import Path
        r = Path(tempfile.mkdtemp())
        (r / "brief.json").write_text(
            json.dumps({"mode": "from_script", "script": "【幕一】\n女弓手拉满弓。",
                        "protagonist": "女弓手", "must_have": ["a", "b", "c", "d"],
                        "key_props": ["长弓"], "禁忌": "无血", "结局": "定格",
                        "target_duration": "30秒"}),
            encoding="utf-8")
        return r

    def test_preseed_written_then_reaches_scenedesigner(self):
        from v5 import roles as roles_mod
        root = self._root()
        written = write_preseed(root, 1)
        self.assertEqual(written, ["scriptwriter"])
        s = roles_mod.role_input("scenedesigner", root, {"episode_index": 1})
        self.assertIn("剧本原文", s, "清单里必须有这一行（人也要看得懂）")
        self.assertIn("只读", s)
        self.assertIn("【幕一】", s, "★ 剧本正文必须真的注入（这是缺口的正解）")

    def test_reaches_dialogue_too(self):
        from v5 import roles as roles_mod
        root = self._root()
        write_preseed(root, 1)
        s = roles_mod.role_input("dialogue", root, {"episode_index": 1})
        self.assertIn("【幕一】", s)

    def test_plotdesigner_never_injected(self):
        """反向断言：它没有产物，注进去只会得到「读不到」。"""
        from v5 import roles as roles_mod
        root = self._root()
        write_preseed(root, 1)
        s = roles_mod.role_input("scenedesigner", root, {"episode_index": 1})
        self.assertNotIn("/plotdesigner/", s)

    def test_full_mode_injects_nothing_extra(self):
        """full 模式下这段逻辑完全不触发（默认路径零变化）。"""
        from v5 import roles as roles_mod
        import tempfile
        from pathlib import Path
        r = Path(tempfile.mkdtemp())
        (r / "brief.json").write_text(json.dumps({"mode": "full"}), encoding="utf-8")
        s = roles_mod.role_input("scenedesigner", r, {"episode_index": 1})
        self.assertNotIn("剧本原文", s)

    def test_not_injected_when_preseed_failed(self):
        """⚠️ 预置**没落盘**时不得注入 —— 否则会告诉模型「去读一个不存在的文件」。
        这正是 `resolve_ok` 存在的理由（`write_preseed` 可能因只读目录失败）。"""
        from v5 import roles as roles_mod
        root = self._root()          # 故意**不**调 write_preseed
        s = roles_mod.role_input("scenedesigner", root, {"episode_index": 1})
        self.assertNotIn("剧本原文", s)


if __name__ == "__main__":
    unittest.main()
