# -*- coding: utf-8 -*-
"""守卫层自测（纯逻辑，不调 API）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v5 import config, guards  # noqa: E402


def _m(**kw):
    return {"episode_index": 1, "phases": {}, "revision_counts": {},
            "media_loop": {}, "review": {}, **kw}


class TestOutPath(unittest.TestCase):
    def test_N_expanded(self):
        """旧架构的致命 bug：{N} 从不展开 → 物化守卫永远判失败。"""
        self.assertEqual(guards.out_path("scriptwriter", 1),
                         "scriptwriter/scriptwriter_ep1.md")
        self.assertEqual(guards.out_path("scriptwriter", 3),
                         "scriptwriter/scriptwriter_ep3.md")
        self.assertNotIn("{N}", guards.out_path("scriptwriter"))


class TestPreDispatch(unittest.TestCase):
    def test_prereq(self):
        ok, why = guards.pre_dispatch("scenedesigner", _m())
        self.assertFalse(ok)
        self.assertIn("前置未满足", why)

    def test_prereq_satisfied(self):
        m = _m(phases={"scriptwriter": "complete", "dialogue": "complete",
                       "assetdesigner": "complete"})
        self.assertTrue(guards.pre_dispatch("scenedesigner", m)[0])

    def test_revision_cap(self):
        """达上限仍给最后一次机会；超过上限 → revision_exhausted 为真。"""
        m = _m(revision_counts={"scriptwriter": config.MAX_REVISIONS_PER_PHASE})
        self.assertFalse(guards.revision_exhausted("scriptwriter", m))
        m = _m(revision_counts={"scriptwriter": config.MAX_REVISIONS_PER_PHASE + 1})
        self.assertTrue(guards.revision_exhausted("scriptwriter", m))
        self.assertFalse(guards.revision_exhausted("dialogue", _m()))

    def test_reviewer_repeat_no_longer_locked_here(self):
        """reviewer 重复派发的约束已上移到**条件边**（评审不通过才回退），
        pre_dispatch 不再管——职责单一：只管前置依赖。"""
        m = _m(phases={"scenedesigner": "complete"},
               media_loop={"reviewed": True}, review={"p0": []})
        self.assertTrue(guards.pre_dispatch("reviewer", m)[0])


class TestMediaGate(unittest.TestCase):
    def test_render_needs_storyboard(self):
        ok, _ = guards.media_gate("render", _m())
        self.assertFalse(ok)

    def test_render_ok(self):
        """8 角色全绿 + 评审通过才允许渲染（编排完整性门）。"""
        m = _m(phases={r: "complete" for r in guards.ROLES},
               review={"passed": True})
        self.assertTrue(guards.media_gate("render", m)[0])

    def test_render_blocked_when_partial(self):
        """只跑了 2/8 个角色就渲染 = 编排完整性事故，必须拦。"""
        m = _m(phases={"director": "complete", "worldbuilder": "complete"},
               review={"passed": True})
        ok, why = guards.media_gate("render", m)
        self.assertFalse(ok)
        self.assertIn("创作链未完成", why)

    def test_double_render_blocked(self):
        m = _m(phases={"scenedesigner": "complete"},
               media_loop={"rendered": True, "pending_revision": False})
        self.assertFalse(guards.media_gate("render", m)[0])

    def test_revise_needs_p0(self):
        m = _m(media_loop={"rendered": True})
        self.assertFalse(guards.media_gate("revise", m)[0])

    def test_human_in_charge_only_relaxes_review_veto(self):
        """★ 2026-09-19 人工模式（`config.HUMAN_IN_CHARGE`）：**只放开评审那一条**。

        两端都要防：
          · 放开端 —— 评审未通过也放行渲染（判断权在人，reviewer 降级为报告）；
          · **不能松开的一端** —— 「7 个角色产物齐」照旧是硬要求：少跑一个角色就渲
            = 拿半成品烧配额。这条与"人在不在"无关，人工模式也不许放开。
        """
        from unittest import mock
        full = _m(phases={r: "complete" for r in guards.ROLES}, review={})
        with mock.patch.object(config, "HUMAN_IN_CHARGE", True):
            ok, why = guards.media_gate("render", full)
            self.assertTrue(ok, why)
            partial = _m(phases={"director": "complete", "worldbuilder": "complete"},
                         review={})
            ok2, why2 = guards.media_gate("render", partial)
            self.assertFalse(ok2, "人工模式也**不许**放开「创作链未完成」这条")
            self.assertIn("创作链未完成", why2)
        # 默认（关）：评审未通过照旧拦 —— CLI / 外部 agent 行为不变
        ok3, why3 = guards.media_gate("render", full)
        self.assertFalse(ok3)
        self.assertIn("评审未通过", why3)


class TestPostValidate(unittest.TestCase):
    def test_disk_truth_wins(self):
        """账本没记，但盘上有非空产物 → 承认（不假失败）。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / "scriptwriter" / "scriptwriter_ep1.md"
            p.parent.mkdir(parents=True)
            p.write_text("剧本内容" * 10, encoding="utf-8")
            ok, why, path = guards.post_validate("scriptwriter", _m(), root)
            self.assertTrue(ok)
            self.assertEqual(path, "scriptwriter/scriptwriter_ep1.md")
            self.assertIn("对账承认", why)

    def test_plotdesigner_catalog_missing_episodes_is_rejected(self):
        """★ 目录**非空但缺集**必须判不合格（2026-10-05 实测，52 分钟零出片）。

        旧判据只看"文件存在且非空"⇒ 对着只剩 4 个集条目的 10075 字目录放话"合格"，
        于是角色在"我写完了"的反馈里重写 45 分钟，下游按集切片也切不出第 5–10 集。
        现在当场列出缺哪几集，并给出可执行的修法（一次 write_file 交全）。
        """
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "brief.json").write_text('{"topic":"t","episodes":10}', encoding="utf-8")
            p = root / "plotdesigner" / "episodes.md"
            p.parent.mkdir(parents=True)
            short = "".join("### 第 %d 集：标题\n正文一行\n" % i for i in (1, 2, 3, 4))
            p.write_text("## 第 1 卷\n" + short, encoding="utf-8")
            ok, why, path = guards.post_validate("plotdesigner", _m(), root)
            self.assertFalse(ok, "只有 4/10 集条目，不许放话合格")
            self.assertIn("全剧目录缺集", why)
            self.assertIn("缺第 5、6、7、8、9、10 集", why, "要点名到集号，角色才知道补哪几集")
            self.assertIn("一次 `write_file` 交全 10 集", why, "要给出可执行的修法，不是只说错")
            self.assertEqual(path, "plotdesigner/episodes.md")
            # 反向对照：补齐 10 集 ⇒ 必须放行（不许把这条判据做成一律拦）
            p.write_text("## 第 1 卷\n" +
                         "".join("### 第 %d 集：标题\n正文一行\n" % i for i in range(1, 11)),
                         encoding="utf-8")
            ok2, why2, _ = guards.post_validate("plotdesigner", _m(), root)
            self.assertTrue(ok2, why2)
            # 单集项目（老项目）：不判，行为与改造前一字不变
            (root / "brief.json").write_text('{"topic":"t","episodes":1}', encoding="utf-8")
            p.write_text("## 第 1 卷\n### 第 1 集：标题\n正文一行\n", encoding="utf-8")
            ok3, why3, _ = guards.post_validate("plotdesigner", _m(), root)
            self.assertTrue(ok3, why3)

    def test_missing_artifact(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ok, why, _ = guards.post_validate("scenedesigner", _m(), Path(d))
            self.assertFalse(ok)
            self.assertIn("未物化", why)

    def _sw(self, root, text="剧本内容" * 10, mtime=None):
        """落一个 scriptwriter 产物，可选把 mtime 钉到指定时刻。"""
        import os, time
        p = root / "scriptwriter" / "scriptwriter_ep1.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if mtime is not None:
            os.utime(str(p), (mtime, mtime))
        return p

    def test_since_none_keeps_disk_truth_behaviour(self):
        """不传 since → 行为与改造前一字不变（既有调用方零改动）。"""
        import tempfile, time
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._sw(root, mtime=time.time() - 86400)   # 一天前的旧文件
            ok, why, path = guards.post_validate("scriptwriter", _m(), root)
            self.assertTrue(ok, why)

    def test_stale_artifact_rejected_when_since_given(self):
        """传了 since → "在盘但上一轮留下的"不再算完成。

        病样本（2026-10-01）：review_ep1.md mtime 停在 15:33:33，两轮 reviewer
        都没碰它，exists() 照样成立 → 判 complete、判决从旧文件解析、据此
        force_passed 放行渲染。
        """
        import tempfile, time
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._sw(root, mtime=time.time() - 600)     # 10 分钟前写的
            ok, why, path = guards.post_validate("scriptwriter", _m(), root,
                                                 since=time.time())
            self.assertFalse(ok, "旧产物不该算本轮完成")
            self.assertIn("未被本轮改写", why)
            self.assertIn(".rerun_backup/", why, "判据要说清怎么修")

    def test_fresh_artifact_passes_with_since(self):
        import tempfile, time
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._sw(root, mtime=time.time())
            ok, why, path = guards.post_validate("scriptwriter", _m(), root,
                                                 since=time.time() - 5)
            self.assertTrue(ok, why)

    def test_artifact_fresh_has_one_second_tolerance(self):
        """1 秒容差是给文件系统时间粒度的，不是放宽判据。"""
        import tempfile, time
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            now = time.time()
            p = self._sw(root, mtime=now - 0.5)
            self.assertTrue(guards.artifact_fresh(p, now))
            self.assertFalse(guards.artifact_fresh(p, now + 60))
            self.assertTrue(guards.artifact_fresh(p, None), "since=None 一律放行")


class TestTokenBreaker(unittest.TestCase):
    def test_run_budget(self):
        m = _m()
        b = guards.TokenBreaker(m)
        ok, why = b.add("scriptwriter", config.TOKEN_BUDGET_RUN + 1)
        self.assertFalse(ok)
        self.assertIn("TOKEN-BREAKER", why)

    def test_role_budget(self):
        m = _m()
        b = guards.TokenBreaker(m)
        b.add("other", 10)
        ok, why = b.add("scenedesigner", config.TOKEN_BUDGET_ROLE + 1)
        self.assertFalse(ok)
        self.assertIn("scenedesigner", why)

    def test_under_budget(self):
        b = guards.TokenBreaker(_m())
        self.assertTrue(b.add("director", 1000)[0])


class TestReconcile(unittest.TestCase):
    def test_missing_everything(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ok, problems = guards.reconcile(_m(), Path(d))
            self.assertFalse(ok)
            self.assertTrue(any("阶段未完成" in p for p in problems))
            self.assertTrue(any("成片不存在" in p for p in problems))


# ─── M1（2026-09-16）：多集 —— 集级产物路径 ──────────────────────────────────
#
# 改造前的 bug：**只有 `scriptwriter` 带 `{N}`**，dialogue / scenedesigner / reviewer
# 是固定路径 ⇒ 第 2 集**直接覆盖第 1 集**，而且没有任何门会拦（每个角色都"写成功了"）。
# 这些测试锁死三件事：路径确实带了集号、**读**能回退到旧名、**写**只有一个口径。
class TestEpisodePaths(unittest.TestCase):
    def test_episode_level_roles_carry_N(self):
        """集级角色的产物路径**必须**含 `{N}`（漏一个 = 那一集的产物覆盖上一集）。"""
        for role in ("scriptwriter", "dialogue", "scenedesigner", "reviewer"):
            self.assertIn("{N}", guards.OUTPUTS[role], "%s 不是集级路径" % role)

    def test_whole_drama_roles_are_flat(self):
        """全剧级角色（一次锁定、全剧复用）**不应**带集号 —— 带了会每集重做。"""
        for role in ("director", "worldbuilder", "assetdesigner", "plotdesigner"):
            self.assertNotIn("{N}", guards.OUTPUTS[role], "%s 不该是集级路径" % role)

    def test_resolve_prefers_new_name(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "dialogue").mkdir(parents=True)
            (root / "dialogue" / "dialogue_ep2.md").write_text("新", encoding="utf-8")
            (root / "dialogue" / "dialogue.md").write_text("旧", encoding="utf-8")
            p = guards.resolve_path(root, "dialogue", 2)
            self.assertEqual(p.name, "dialogue_ep2.md")

    def test_resolve_falls_back_to_legacy(self):
        """历史项目（30 个）的产物是旧名 —— 不做回退则 `--rerender` / `--monitor` 全挂。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner.md").write_text("旧名产物",
                                                                     encoding="utf-8")
            p = guards.resolve_path(root, "scenedesigner", 1)
            self.assertEqual(p.name, "scenedesigner.md")

    def test_resolve_reads_episode_from_manifest(self):
        """不传 ep 时按 manifest 的 `episode_index` 解析（运行期真相）。"""
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "reviewer").mkdir(parents=True)
            (root / "reviewer" / "review_ep3.md").write_text("第三集", encoding="utf-8")
            (root / "reviewer" / "review_ep1.md").write_text("第一集", encoding="utf-8")
            (root / ".agent_state.json").write_text(
                json.dumps({"episode_index": 3}), encoding="utf-8")
            p = guards.resolve_path(root, "reviewer")
            self.assertEqual(p.name, "review_ep3.md")

    def test_resolve_legacy_fallback_is_ep1_only(self):
        """★ 旧名回退**只对第 1 集**生效（2026-09-16 真实验收抓到的 bug）。

        历史项目是**单集**的 → 旧名文件**就是**第 1 集。若第 2 集也回退，
        `resolve_path(root, role, 2)` 会返回第 1 集的旧文件 → **第 2 集覆盖第 1 集**，
        且绕过了集级路径的全部保护（M1 要修的 bug 从另一个入口回来了）。
        """
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            legacy = root / "scenedesigner" / "scenedesigner.md"
            legacy.write_text("历史单集", encoding="utf-8")
            self.assertEqual(guards.resolve_path(root, "scenedesigner", 1), legacy)
            p2 = guards.resolve_path(root, "scenedesigner", 2)
            self.assertNotEqual(p2, legacy, "第 2 集绝不能落到旧名文件上")
            self.assertEqual(p2.name, "scenedesigner_ep2.md")

    def test_two_episodes_do_not_overwrite(self):
        """★ M1 的**核心验收**：两集的分镜同时存在、内容不同、互不覆盖。

        这是"第 2 集覆盖第 1 集"那个 bug 的回归测试 —— 改造前两个 `out_path` 相同。
        """
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            p1 = root / guards.out_path("scenedesigner", 1)
            p2 = root / guards.out_path("scenedesigner", 2)
            self.assertNotEqual(p1, p2, "第 1/2 集的分镜路径不能相同")
            p1.write_text("第一集分镜", encoding="utf-8")
            p2.write_text("第二集分镜", encoding="utf-8")
            self.assertEqual(guards.resolve_path(root, "scenedesigner", 1)
                             .read_text(encoding="utf-8"), "第一集分镜")
            self.assertEqual(guards.resolve_path(root, "scenedesigner", 2)
                             .read_text(encoding="utf-8"), "第二集分镜")


class TestPhaseAccessors(unittest.TestCase):
    """`phases` 的读/写访问器：**兼容旧的一维结构**，新结构按集隔离。

    为什么这是"接口改动"而不是散改（见 `../docs-archive-20260918/spec-m0-checklist.md` §M0④）：
    `phases` 在**生产代码 45 处 + 测试 26 处**，散改是一次高风险重构。
    """

    def test_phase_of_legacy_1d_only_for_ep1(self):
        m = _m(phases={"scenedesigner": "complete"})
        self.assertEqual(guards.phase_of(m, "scenedesigner", 1), "complete")
        # 旧一维结构对第 2 集**无意义** —— 必须返回空（否则第 2 集会拿第 1 集的状态放行）
        self.assertEqual(guards.phase_of(m, "scenedesigner", 2), "")

    def test_phase_of_new_2d(self):
        m = _m(phases={"1": {"scenedesigner": "complete"},
                       "2": {"scenedesigner": "failed"}})
        self.assertEqual(guards.phase_of(m, "scenedesigner", 1), "complete")
        self.assertEqual(guards.phase_of(m, "scenedesigner", 2), "failed")
        self.assertEqual(guards.phase_of(m, "dialogue", 2), "")

    def test_phase_of_reads_ep_from_manifest(self):
        m = _m(episode_index=2, phases={"2": {"reviewer": "complete"}})
        self.assertEqual(guards.phase_of(m, "reviewer"), "complete")

    def test_set_phase_migrates_legacy_1d(self):
        """写新结构时把旧的一维结构**迁到第 1 集名下**（不丢旧状态）。"""
        m = _m(phases={"director": "complete"})
        guards.set_phase(m, "scenedesigner", "complete", 2)
        self.assertEqual(m["phases"].get("1"), {"director": "complete"})
        self.assertEqual(m["phases"].get("2"), {"scenedesigner": "complete"})

    def test_set_phase_never_overwrites_other_episode(self):
        m = _m()
        guards.set_phase(m, "reviewer", "complete", 1)
        guards.set_phase(m, "reviewer", "failed", 2)
        self.assertEqual(m["phases"]["1"]["reviewer"], "complete")


class TestBootEpisode(unittest.TestCase):
    """起服集号：`SHORTDRAMA_V5_EPISODE` > manifest 的 `episode_index` > 1。

    为什么需要它：角色 system prompt 里的产物路径是**编译期固化**的，集号只能在
    起服前确定（见 `guards.boot_episode`）。env 与 manifest 不一致 = 角色按第 N 集
    写盘、记账按第 1 集校验 → **全判 failed 且日志看不出原因**。
    """

    def setUp(self):
        import os
        self._old = os.environ.pop(guards.EPISODE_ENV, None)

    def tearDown(self):
        import os
        if self._old is None:
            os.environ.pop(guards.EPISODE_ENV, None)
        else:
            os.environ[guards.EPISODE_ENV] = self._old

    def test_defaults_to_1(self):
        self.assertEqual(guards.boot_episode(), 1)

    def test_env_wins(self):
        import os
        os.environ[guards.EPISODE_ENV] = "5"
        self.assertEqual(guards.boot_episode(), 5)

    def test_env_garbage_is_ignored(self):
        import os
        os.environ[guards.EPISODE_ENV] = "第5集"
        self.assertEqual(guards.boot_episode(), 1)

    def test_manifest_fallback(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".agent_state.json").write_text(
                json.dumps({"episode_index": 4}), encoding="utf-8")
            self.assertEqual(guards.boot_episode(root), 4)


class TestEpisodeIsolationM2(unittest.TestCase):
    """★ M2：`phases` / `media_loop` 按集隔离 —— **门不能拿别的集的状态放行**。

    验收负样本（spec M2）：把第 2 集的名册清空，媒体门**必须拦**。
    而改造前 `media_gate` 读的是一维 `phases` ⇒ 第 2 集拿第 1 集的 `complete` 就过了。
    """

    EP1_ALL = {r: "complete" for r in guards.GATE_ROLES}

    def _m2(self, ep2=None, rendered_ep1=True):
        return {"episode_index": 2,
                "phases": {"1": dict(self.EP1_ALL), "2": dict(ep2 or {})},
                "review": {"passed": True},
                "media_loop": {"1": {"rendered": rendered_ep1}}} if rendered_ep1 else \
               {"episode_index": 2,
                "phases": {"1": dict(self.EP1_ALL), "2": dict(ep2 or {})},
                "review": {"passed": True}}

    def test_negative_sample_ep2_incomplete_is_blocked(self):
        """★ 负样本：第 2 集只跑一半 → 必须拦（不能拿第 1 集的 complete 放行）。"""
        m = self._m2({"scriptwriter": "complete"})
        ok, why = guards.media_gate("render", m, ep=2)
        self.assertFalse(ok, "第 2 集没跑完却被放行 —— 这就是 M2 要治的串集")
        self.assertIn("未完成", why)
        self.assertIn("第 2 集", why, "拦的理由要带集号，否则排查时以为是第 1 集的问题")

    def test_ep2_complete_is_allowed(self):
        """反向：第 2 集真跑完且未渲染过 → 放行（防"改动过严"把正常流程拦死）。"""
        m = self._m2(dict(self.EP1_ALL))
        self.assertTrue(guards.media_gate("render", m, ep=2)[0])

    def test_ep1_rendered_does_not_block_ep2(self):
        """第 1 集渲过**不该**让第 2 集撞上「已渲染且无待修订」。"""
        m = {"episode_index": 2, "review": {"passed": True},
             "phases": {"1": dict(self.EP1_ALL), "2": dict(self.EP1_ALL)},
             "media_loop": {"1": {"rendered": True}}}
        self.assertTrue(guards.media_gate("render", m, ep=2)[0],
                        "ep1 的 rendered 串到了 ep2")

    def test_ep2_rendered_blocks_itself(self):
        m = {"episode_index": 2, "review": {"passed": True},
             "phases": {"1": dict(self.EP1_ALL), "2": dict(self.EP1_ALL)},
             "media_loop": {"1": {}, "2": {"rendered": True}}}
        ok, why = guards.media_gate("render", m, ep=2)
        self.assertFalse(ok)
        self.assertIn("已渲染", why)

    def test_default_ep_comes_from_manifest(self):
        m = self._m2({"scriptwriter": "complete"})
        self.assertEqual(guards.media_gate("render", m)[0],
                         guards.media_gate("render", m, ep=2)[0])

    def test_pre_dispatch_is_per_episode(self):
        m = {"episode_index": 2,
             "phases": {"1": dict(self.EP1_ALL), "2": {"scriptwriter": "complete"}}}
        ok, why = guards.pre_dispatch("dialogue", m, ep=2)
        self.assertTrue(ok, "dialogue 的前置 scriptwriter 满足")

    def test_pre_dispatch_blocked_when_this_episode_missing(self):
        m = {"episode_index": 2,
             "phases": {"1": dict(self.EP1_ALL), "2": {}}}
        ok, why = guards.pre_dispatch("dialogue", m, ep=2)
        self.assertFalse(ok, "第 2 集什么都没跑，不该因为第 1 集跑完了就放行")
        self.assertIn("前置未满足", why)

    def test_reset_from_only_touches_its_episode(self):
        m = {"episode_index": 2,
             "phases": {"1": dict(self.EP1_ALL), "2": dict(self.EP1_ALL)}}
        reset = guards.reset_from("scriptwriter", m, ep=2)
        self.assertEqual(reset, ["scriptwriter", "dialogue", "scenedesigner", "reviewer"])
        for r in reset:
            self.assertEqual(guards.phase_of(m, r, 2), "")
            self.assertEqual(guards.phase_of(m, r, 1), "complete",
                             "回退第 2 集竟清掉了第 1 集的名册")

    def test_record_phase_writes_per_episode(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / guards.out_path("scenedesigner", 2)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("| 1 | 中景 | 平视 | 固定 | 8 | 甲地 | 陈默在雪里走了一段路 | （无声，环境音） | 风声 |\n",
                         encoding="utf-8")
            m = {"episode_index": 2}
            guards.record_phase(root, m, "scenedesigner", ep=2)
            self.assertEqual(guards.phase_of(m, "scenedesigner", 2), "complete")
            self.assertEqual(guards.phase_of(m, "scenedesigner", 1), "")


class TestStrayEpisodeWarning(unittest.TestCase):
    """第 N>1 集"没物化但旧名文件在盘" = **串集的特征签名**，必须响亮告警。"""

    def test_warns_when_old_name_file_exists(self):
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner.md").write_text(
                "第 1 集的产物", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ok, why, _ = guards.post_validate(
                    "scenedesigner", {"episode_index": 2}, root, ep=2)
            self.assertFalse(ok)
            self.assertIn("串集", buf.getvalue(), "必须告警：否则只看到「产物缺失」")

    def test_no_warning_for_ep1(self):
        """第 1 集本来就允许旧名 → 不该误报。"""
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner.md").write_text(
                "第 1 集的产物", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                guards.post_validate("scenedesigner", {"episode_index": 1}, root, ep=1)
            self.assertNotIn("串集", buf.getvalue())


class TestMediaLoopAccessor(unittest.TestCase):
    """`media_loop` 的按集访问器（与 `phase_of` 同型）。"""

    def test_reads_per_episode(self):
        m = {"episode_index": 2, "media_loop": {"1": {"rendered": True}, "2": {}}}
        self.assertTrue(guards.media_loop_of(m, 1).get("rendered"))
        self.assertEqual(guards.media_loop_of(m, 2), {})

    def test_reads_legacy_1d_only_for_ep1(self):
        m = {"media_loop": {"rendered": True, "pending_revision": False}}
        self.assertTrue(guards.media_loop_of(m, 1).get("rendered"))
        self.assertEqual(guards.media_loop_of(m, 2), {}, "旧一维不该外溢到第 2 集")

    def test_set_migrates_legacy_without_wiping_other_episodes(self):
        m = {"media_loop": {"rendered": True}}
        guards.media_loop_set(m, 1)               # 迁移旧一维 → "1"
        guards.media_loop_set(m, 2)["rendered"] = True
        self.assertTrue(m["media_loop"]["1"]["rendered"], "写第 2 集竟清掉了第 1 集")
        self.assertTrue(m["media_loop"]["2"]["rendered"])


class TestWholeDramaRoleMisnamedFile(unittest.TestCase):
    """★ 真机验收抓到（2026-09-17）：全剧级角色被模型擅自加了集号后缀。

    实测产出 `worldbuilder/worldbuilder_ep1.md` 而声明路径是
    `worldbuilder/worldbuilder.md` ⇒ 物化守卫判"未物化" ⇒ 整条链白跑 20 分钟。
    与"写到项目根"同一条原则：**磁盘实况优先**，不让一次改名毁掉一轮。
    """

    def test_relocates_stray_ep_suffix(self):
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "worldbuilder").mkdir(parents=True)
            (root / "worldbuilder" / "worldbuilder_ep1.md").write_text(
                "# 剧情概要\n## 角色卡：甲\n- 姓名：甲\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ok, _why, path = guards.post_validate(
                    "worldbuilder", {"episode_index": 1}, root, ep=1)
            self.assertTrue(ok, "应把写歪名字的产物挪回声明路径，而不是判失败")
            self.assertEqual(path, "worldbuilder/worldbuilder.md")
            self.assertTrue((root / "worldbuilder" / "worldbuilder.md").exists())
            self.assertIn("挪回声明路径", buf.getvalue(), "挪动必须可见")

    def test_does_not_touch_episodic_roles(self):
        """集级角色本来就该带集号 —— 兜底不能把它们搞乱。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner_ep1.md").write_text(
                "| 1 | 中景 | 平视 | 固定 | 8 | 甲 | 陈默在雪里走了一段路 | （无声，环境音） | 风声 |\n",
                encoding="utf-8")
            ok, _why, path = guards.post_validate(
                "scenedesigner", {"episode_index": 1}, root, ep=1)
            self.assertTrue(ok)
            self.assertEqual(path, "scenedesigner/scenedesigner_ep1.md")


class TestMediaGateReviewForcePass(unittest.TestCase):
    """评审上限的**出口**（2026-09-29 人定口径：渲染被拦超过 2 次 → 写 force_passed 放行）。

    为什么必须单独测：这道保险原先是**空转的** —— `revision_exhausted()` 只有测试调用、
    `force_passed` 全仓 5 处读却零写入点，测试套件照样全绿，而线上评审反复不过没有出口，
    真实结局是重派到撞墙钟预算、`rc=0` 静默收工不出片。所以下面每条都**连落盘一起断言**。
    """

    def _m(self):
        # ★ phases 必须是**二维**（`{"1": {role: "complete"}}`）：M2 之后按集读，
        #   写成扁平一份会让门以为"本集一个角色都没跑完"，测出来的是另一条判据。
        m = {"episode_index": 1, "phases": {}, "revision_counts": {},
             "media_loop": {},
             "review": {"passed": False, "rerun": ["scenedesigner"],
                        "reasons": ["LN02：非宽景双 @ 会多画一个人"]}}
        for ep in (1, 2):
            for r in guards.GATE_ROLES:
                guards.set_phase(m, r, "complete", ep=ep)
        return m

    def test_blocks_until_cap_then_force_passes(self):
        """前 `cap` 次拦、第 `cap+1` 次放行并写 force_passed。"""
        cap = int(config.MAX_REVISIONS_PER_PHASE)
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as d:
            root = Path(d)
            m = self._m()
            for i in range(1, cap + 1):
                ok, why = guards.media_gate("render", m, ep=1, root=root)
                self.assertFalse(ok, "第 %d 次应当拦下" % i)
                self.assertIn("第 %d/%d 次拦截" % (i, cap), why)
            ok, _why = guards.media_gate("render", m, ep=1, root=root)
            self.assertTrue(ok, "超过上限应当放行")
            self.assertTrue(m["review"].get("force_passed"), "放行必须留下 force_passed")

    def test_force_pass_without_machine_block_is_not_silent(self):
        """★ 判定块解析不出来时 `reasons=[]` ⇒ 放行日志不许只剩一个空标题。

        实错（2026-09-29 huashan-duel-v4-0928 ep2）：reviewer 产物只写了散文
        `**pass = false**`、没有围栏机器块，程序读不到条目与 rerun 目标；
        放行那行明明写着「评审仍未消化的条目：」后面却一个字都不打 —— 人看到的
        就是"门自己放行了"，正是这条机制当初承诺要避免的样子。
        """
        import io
        from contextlib import redirect_stdout
        from tempfile import TemporaryDirectory

        cap = int(config.MAX_REVISIONS_PER_PHASE)
        with TemporaryDirectory() as d:
            root = Path(d)
            m = self._m()
            m["review"]["reasons"] = []
            buf = io.StringIO()
            with redirect_stdout(buf):
                for _i in range(cap + 1):
                    guards.media_gate("render", m, ep=1, root=root)
            out = buf.getvalue()
        self.assertIn("force_passed", out, out)
        self.assertIn("没有机器判定块", out, "列不出条目就必须说明为什么列不出：" + out)

    def test_counter_survives_process_restart(self):
        """★ 计数必须落盘：不落盘 = 每次重启都从 0 数 = 保险再次空转。"""
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as d:
            root = Path(d)
            m = self._m()
            guards.media_gate("render", m, ep=1, root=root)
            reloaded = guards.load_manifest(root)
            self.assertEqual(int((reloaded.get("review_blocks") or {}).get("1") or 0), 1,
                             "第一次拦截没有落盘")

    def test_passed_review_is_never_counted(self):
        """评审通过时不进这条分支 —— 别把正常渲染也累计成"被拦过"。"""
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as d:
            root = Path(d)
            m = self._m()
            m["review"]["passed"] = True
            ok, _why = guards.media_gate("render", m, ep=1, root=root)
            self.assertTrue(ok)
            self.assertEqual(m.get("review_blocks") or {}, {}, "不该被计数")

    def test_human_mode_does_not_count_or_force_pass(self):
        """人工模式（前端路径）语义不变：不拦、也**不计数**、不写 force_passed。

        计数被人工模式污染的话，下一次全自动跑会从"已经拦过 N 次"起步，
        第一脚就放行 —— 那是把全自动唯一的保护拆掉。
        """
        from unittest import mock
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as d:
            root = Path(d)
            m = self._m()
            with mock.patch.object(config, "HUMAN_IN_CHARGE", True):
                ok, _why = guards.media_gate("render", m, ep=1, root=root)
            self.assertTrue(ok)
            self.assertEqual(m.get("review_blocks") or {}, {}, "不该被计数")
            self.assertFalse(m["review"].get("force_passed"))

    def test_per_episode_counters_are_isolated(self):
        """按集隔离：第 2 集不该继承第 1 集用掉的额度（M2 同一条纪律）。"""
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as d:
            root = Path(d)
            m = self._m()
            guards.media_gate("render", m, ep=1, root=root)
            guards.media_gate("render", m, ep=1, root=root)
            ok, why = guards.media_gate("render", m, ep=2, root=root)
            self.assertFalse(ok, "第 2 集第一次渲染不该被第 1 集的额度放行")
            self.assertIn("第 1/%d 次拦截" % int(config.MAX_REVISIONS_PER_PHASE), why)


class TestGateAfterDiskReconcile(unittest.TestCase):
    """产物在盘、账本 `phases` 为空时，门必须走进"评审未通过"那条分支并**累计次数**。

    这条测试是为我自己引入的一个 bug 写的（2026-09-29）：`scripts/run_new_project.py`
    新加的"问门"调用直接喂 `load_manifest()`，而那条路径上 `phases` 是空的
    （实测 `.agent_state.json` 只有 `{"episode_index": 1}`）⇒ 门先撞上
    「创作链未完成（缺 7 个角色）」，**根本进不到评审那条分支**，
    `review_blocks` 不累计、`force_passed` 永远不写 ⇒ 出口照旧堵死。
    pipeline 早就用 `reconcile_manifest` 治过这个（其 docstring 记着 2026-09-12 同型事故）。
    """

    def _proj(self, root):
        for role in guards.GATE_ROLES:
            p = root / guards.out_path(role, 1)
            p.parent.mkdir(parents=True, exist_ok=True)
            body = ('```json\n{"pass": false, "rerun": ["scenedesigner"], '
                    '"reasons": ["LN02 双 @ 会多画一个人"]}\n```'
                    if role == "reviewer" else "# %s\n\n正文若干，非空即可。\n" % role)
            p.write_text(body, encoding="utf-8")

    def test_empty_phases_still_counts_review_blocks(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as d:
            root = Path(d)
            self._proj(root)
            guards.save_manifest(root, {"episode_index": 1})          # 账本空
            m = guards.reconcile_manifest(root, guards.load_manifest(root), ep=1)
            self.assertEqual(
                [r for r in guards.GATE_ROLES if guards.phase_of(m, r, 1) != "complete"], [],
                "reconcile 没按磁盘事实补齐 phases —— 门会误报「缺角色」")
            ok, why = guards.media_gate("render", m, ep=1, root=root)
            self.assertFalse(ok)
            self.assertNotIn("创作链未完成", why, "走错了分支：该报评审拦截，不该报缺角色")
            self.assertIn("次拦截", why, "没进评审计数分支 ⇒ force_passed 永远不会被写")
            reloaded = guards.load_manifest(root)
            self.assertEqual(int((reloaded.get("review_blocks") or {}).get("1") or 0), 1,
                             "拦截次数没落盘 ⇒ 跨进程不累计，保险空转")


class TestReviewParseWarningNamesTheEpisode(unittest.TestCase):
    """解析不出判定块时，告警必须点名**本集**那份文件。

    实错（2026-09-29 渲 huashan-duel-v4-0928 第 2 集）：告警写的是
    `out_path("reviewer")`（默认 ep=1）⇒ 日志报「review_ep1.md 解析不出判定块」，
    而 ep1 那份的 `pass: false` 块是完好的。我据此去查了一个**不存在**的解析器 bug，
    真凶是 ep2 的 reviewer 只写了散文 `**pass = false**`、没有机器判定块。
    """

    def test_warning_names_ep2_file_not_ep1(self):
        import io
        from contextlib import redirect_stdout
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as d:
            root = Path(d)
            p = root / "reviewer" / "review_ep2.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("## 终审判定\n\n**pass = false**\n\n阻断理由：LN02 双 @。\n",
                         encoding="utf-8")
            guards.save_manifest(root, {"episode_index": 2})
            buf = io.StringIO()
            with redirect_stdout(buf):
                guards.reconcile_manifest(root, guards.load_manifest(root), ep=2)
            out = buf.getvalue()
        self.assertIn("review_ep2.md", out, "没告警 = 判定被静默当成未通过：" + out)
        self.assertNotIn("review_ep1.md", out, "告警点名了别的集的文件 ⇒ 会把人引去查错的地方")

    def test_invalid_new_report_clears_previous_pass_and_forced_pass(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as d:
            root = Path(d)
            p = root / "reviewer/review_ep1.md"
            p.parent.mkdir(parents=True)
            p.write_text("新报告：不通过，但遗漏了机器判定块。", encoding="utf-8")
            m = guards.reconcile_manifest(root, {"episode_index": 1,
                "review": {"passed": True, "force_passed": True, "reasons": []}}, ep=1)
        self.assertFalse(m["review"]["passed"])
        self.assertFalse(m["review"].get("force_passed"))
        self.assertTrue(m["review"]["reasons"])


class TestExitAfterThrash(unittest.TestCase):
    """★ 反空转闸不能把出口一起掐掉（2026-10-03 `yoga-affair-1003g` 实测）。

    那条链的过程：评审以「总时长 88 秒不合格」打回（**程序自己一加是 120 秒、在带内**）
    → 驱动器 `reset_from()` 把分镜与审稿挪进 `.rerun_backup/` → 起第 4 轮重派
    → 重写计数器是**跨轮累计**的，一开局就超限 ⇒ 闸把第 4 轮当场取消
    → 盘上"新的没写、旧的在归档"，`run_new_project` 报「缺 2 个角色 → 不进媒体链」
    → 46 分钟、零出片。**装闸是为了省时间，结果它把唯一剩下的那条路也堵了。**

    两条修判据（都在盘上可验）：
      · 被打回挪走、又没重做出来的产物 ⇒ 收工前从最新一份归档**回捞**；
      · 评审驱动的打回**当场记进门台账** ⇒ 门第一次被问就能按上限 `force_passed`
        放行出片，而不是"第 1/2 次拦截、还差两次"。
    """

    def _manifest(self, ep: int = 1) -> dict:
        m = {"episode_index": ep, "phases": {}, "revision_counts": {},
             "media_loop": {},
             "review": {"passed": False, "rerun": ["scenedesigner"],
                        "reasons": ["LN07：关键接触没有独占整镜"]}}
        for r in guards.GATE_ROLES:
            guards.set_phase(m, r, "complete", ep=ep)
        return m

    def test_recorded_blocks_give_the_gate_a_one_call_exit(self):
        """驱动器打的两次回**要算进门那份台账**：门第一次被问就该放行，不是还差两次。"""
        from tempfile import TemporaryDirectory
        cap = int(config.MAX_REVISIONS_PER_PHASE)
        with TemporaryDirectory() as d:
            root = Path(d)
            m = self._manifest()
            for _ in range(cap):                      # = 驱动器执行了 cap 次评审打回
                n = guards.record_review_block(root, m, ep=1)
            self.assertEqual(n, cap)
            m2 = guards.load_manifest(root)           # 模拟"另起进程问门"
            ok, why = guards.media_gate("render", m2, ep=1, root=root)
            self.assertTrue(ok, "台账已满时门必须放行，实际说：%s" % why)
            self.assertTrue(m2["review"].get("force_passed"),
                            "放行要留下 force_passed 的痕迹")

    def test_record_review_block_is_per_episode_and_on_disk(self):
        """计数落盘 + 按集独立：第 2 集不能被第 1 集的失败拖放行。"""
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as d:
            root = Path(d)
            m = self._manifest()
            guards.record_review_block(root, m, ep=1)
            guards.record_review_block(root, m, ep=1)
            guards.record_review_block(root, m, ep=2)
            back = (guards.load_manifest(root).get("review_blocks") or {})
            self.assertEqual(int(back.get("1") or 0), 2, back)
            self.assertEqual(int(back.get("2") or 0), 1, "两集混成一格 = 连载时第 2 集白跑")

    def test_restore_stashed_puts_the_artifact_back(self):
        """打回挪走、重派又失败 ⇒ 回捞最新归档那一份，盘上重新有产物可交给门。"""
        from tempfile import TemporaryDirectory
        import time
        with TemporaryDirectory() as d:
            root = Path(d)
            rel = guards.out_path("scenedesigner", 1)
            (root / "scenedesigner").mkdir(parents=True)
            (root / rel).write_text("| 镜头号 | 时长(秒) |\n|---|---|\n| 1-1 | 12 |\n",
                                    encoding="utf-8")
            moved = guards.stash_artifacts(root, ["scenedesigner"], 1)
            self.assertTrue(moved and not (root / rel).exists(), "没挪走 = 测不到回捞")
            time.sleep(1.1)                           # 归档目录名是秒级时间戳
            (root / "scenedesigner" / "x.md").write_text("新写的", encoding="utf-8")
            back = guards.restore_stashed(root, ["scenedesigner"], 1)
            self.assertEqual(back, [rel], "没把归档那份放回盘上：%s" % back)
            self.assertIn("1-1", (root / rel).read_text(encoding="utf-8"))

    def test_restore_never_overwrites_a_newer_live_copy(self):
        """盘上已经有新版产物就**绝不**回捞覆盖 —— 那会吃掉刚做完的工作。"""
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as d:
            root = Path(d)
            rel = guards.out_path("scenedesigner", 1)
            (root / "scenedesigner").mkdir(parents=True)
            (root / rel).write_text("旧版 1-1", encoding="utf-8")
            guards.stash_artifacts(root, ["scenedesigner"], 1)
            (root / rel).write_text("新版 9-9", encoding="utf-8")
            back = guards.restore_stashed(root, ["scenedesigner"], 1)
            self.assertEqual(back, [], "盘上有新版还回捞 = 覆盖刚做的工作")
            self.assertIn("9-9", (root / rel).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
