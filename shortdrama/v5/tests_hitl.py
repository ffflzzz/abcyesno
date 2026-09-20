# -*- coding: utf-8 -*-
"""步级 HITL 自测（纯文件操作 + 开关，不调任何 API）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v5 import config, hitl  # noqa: E402
from v5.guards import PREREQ  # noqa: E402


class TestHitlChannel(unittest.TestCase):
    """`pending.json` / `decision.json` 两个文件构成的信道。

    为什么是文件而不是交互式 input：链路跑在后台（无终端），批准动作由另一个
    进程（CLI）发起 —— 文件是两个进程间最简单可靠的信道。见 `v5/hitl.py`。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_pending_marks_next_role(self):
        hitl.record_pending(self.root, thread_id="t-1", run_id="r-1",
                            done_roles=["worldbuilder", "assetdesigner"])
        pd = hitl.read_pending(self.root)
        self.assertEqual(pd["thread_id"], "t-1")
        self.assertEqual(pd["state"], "pending")
        self.assertEqual(pd["next_role"], list(PREREQ)[2],
                         "下一个待派发 = PREREQ 顺序里第一个未完成的")
        self.assertIn("已完成 2/7", hitl.status(self.root))

    def test_decision_roundtrip_and_single_consume(self):
        self.assertIsNone(hitl.read_decision(self.root), "没有决定文件时返回 None")
        # ★ 2026-09-18：`decide` 现在**要求真有挂起**（防"没有挂起时写下的决定，
        #   被下一次挂起立刻消费" = 静默跳过一次人工审核），故先造一个挂起。
        hitl.record_pending(self.root, thread_id="t-1", run_id="r-1",
                            done_roles=["worldbuilder"])
        hitl.decide(self.root, "approve", by="老王", note="本步可以")
        d = hitl.read_decision(self.root)
        self.assertEqual(d["decision"], "approve")
        self.assertEqual(d["by"], "老王")
        hitl.clear_decision(self.root)      # 一次决定只消费一次
        self.assertIsNone(hitl.read_decision(self.root))

    def test_illegal_decision_rejected_at_write(self):
        with self.assertRaises(ValueError):
            hitl.decide(self.root, "maybe")

    def test_dirty_decision_file_does_not_crash(self):
        """脏文件（非法值 / 坏 JSON）不能让链路崩 —— 只当"还没决定"。"""
        hitl.decision_path(self.root).write_text('{"decision": "???"}', encoding="utf-8")
        self.assertIsNone(hitl.read_decision(self.root))
        hitl.decision_path(self.root).write_text("not json at all", encoding="utf-8")
        self.assertIsNone(hitl.read_decision(self.root))

    def test_clear_decision_is_idempotent(self):
        hitl.clear_decision(self.root)      # 没有文件时也不该抛
        hitl.record_pending(self.root, thread_id="t-1", run_id="r-1")
        hitl.decide(self.root, "reject", note="不行")
        hitl.clear_decision(self.root)
        hitl.clear_decision(self.root)

    def test_status_without_pending(self):
        self.assertIn("无待批准", hitl.status(self.root))

    def test_clear_pending_removes_the_hang(self):
        """★ 决定被消费时要**撤掉挂起标记**（2026-09-18 验收实测的坑）。

        不清的话：从"决定被消费"到"下次挂起"之间（链路执行期，分钟级），
        前端一直读到过期的 pending ⇒ 弹出一条"等你确认"，而用户点了**没反应**
        （戳校验会正确忽略它 —— 安全，但莫名其妙）。
        """
        hitl.record_pending(self.root, thread_id="t", run_id="r",
                            done_roles=["worldbuilder"])
        self.assertTrue(hitl.read_pending(self.root))
        hitl.clear_pending(self.root)
        self.assertIsNone(hitl.read_pending(self.root), "执行期不该再有待批")
        hitl.clear_pending(self.root)        # 幂等：没有文件时也不该抛


class TestDecideGuards(unittest.TestCase):
    """`decide` 的三条校验（2026-09-18 新增）—— 都是防「静默生效」，不是防权限。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_decide_without_pending_is_refused(self):
        """★ 最重要的一条：没有挂起时写下的决定会**在下一次挂起时被立刻消费**
        ⇒ 静默跳过一次人工审核。必须拒绝。"""
        # 故意**不**造挂起
        with self.assertRaises(ValueError) as cm:
            hitl.decide(self.root, "approve", by="老王")
        self.assertIn("没有等待批准", str(cm.exception))
        self.assertIsNone(hitl.read_decision(self.root), "被拒时**不该**留下文件")

    def test_redo_requires_valid_target(self):
        hitl.record_pending(self.root, thread_id="t", run_id="r",
                            done_roles=["worldbuilder", "assetdesigner"])
        # 打回一个**没做过**的角色 = 打回不存在的产物 → 拒绝
        with self.assertRaises(ValueError) as cm:
            hitl.decide(self.root, "redo", target="reviewer")
        self.assertIn("打回目标", str(cm.exception))
        self.assertIsNone(hitl.read_decision(self.root))

    def test_redo_defaults_to_prev_role(self):
        """不传 target 时，默认打回**刚产出的那个**（= done 里依赖序最靠后的）。"""
        hitl.record_pending(self.root, thread_id="t", run_id="r",
                            done_roles=["worldbuilder", "assetdesigner"])
        hitl.decide(self.root, "redo", note="资产卡不对")
        d = hitl.read_decision(self.root)
        self.assertEqual(d["decision"], "redo")
        self.assertEqual(d["target"], "assetdesigner")
        self.assertEqual(d["note"], "资产卡不对")

    def test_redo_with_explicit_upstream_target(self):
        """也可以打回更上游的（用户想回到大纲那一层）。"""
        hitl.record_pending(self.root, thread_id="t", run_id="r",
                            done_roles=["worldbuilder", "assetdesigner", "plotdesigner"])
        hitl.decide(self.root, "redo", target="worldbuilder")
        self.assertEqual(hitl.read_decision(self.root)["target"], "worldbuilder")

    def test_illegal_decision_still_rejected(self):
        hitl.record_pending(self.root, thread_id="t", run_id="r")
        with self.assertRaises(ValueError):
            hitl.decide(self.root, "maybe")


class TestStampBoundToPending(unittest.TestCase):
    """决定必须**带上本次挂起的戳**才被认账（防"上一次的残留决定"串到这一步）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stamp_mismatch_is_ignored(self):
        hitl.record_pending(self.root, thread_id="t", run_id="r",
                            done_roles=["worldbuilder"])
        self.assertTrue(hitl.read_pending(self.root)["stamp"], "挂起要带戳")
        hitl.decide(self.root, "approve")
        # 链路拿着**另一个**戳来读 ⇒ 不算数（那决定属于上一次挂起）
        self.assertIsNone(hitl.read_decision(self.root, stamp="deadbeef"))
        # 不校验戳时仍能读到（供 `status()` 如实报出"有个失效的决定"）
        self.assertIsNotNone(hitl.read_decision(self.root))

    def test_stamp_recorded_on_decision(self):
        hitl.record_pending(self.root, thread_id="t", run_id="r")
        stamp = hitl.read_pending(self.root)["stamp"]
        hitl.decide(self.root, "approve")
        self.assertEqual(hitl.read_decision(self.root)["stamp"], stamp)
        self.assertIsNotNone(hitl.read_decision(self.root, stamp=stamp))

    def test_new_pending_gets_new_stamp(self):
        """再次挂起必须换戳 —— 否则"上一步的决定"会命中下一步。"""
        hitl.record_pending(self.root, thread_id="t", run_id="r1")
        s1 = hitl.read_pending(self.root)["stamp"]
        hitl.record_pending(self.root, thread_id="t", run_id="r2")
        s2 = hitl.read_pending(self.root)["stamp"]
        self.assertNotEqual(s1, s2, "每次挂起都要换戳（即使 run_id 相同）")

    def test_status_reports_stale_decision(self):
        """戳不对时必须**说出来**（不能假装没看见 —— 那会让人一直干等）。"""
        hitl.record_pending(self.root, thread_id="t", run_id="r")
        hitl.decide(self.root, "approve")
        hitl.record_pending(self.root, thread_id="t", run_id="r2")   # 又往前走了一步
        self.assertIn("已失效", hitl.status(self.root))


class TestPrevRole(unittest.TestCase):
    """`prev_role` = "用户刚看到的那个产物"属于谁。**顺序必须按 `PREREQ`。**"""

    def test_uses_dependency_order_not_input_order(self):
        # 故意把列表顺序打乱：语义上的"上一步"由**依赖序**决定，不是传入顺序
        self.assertEqual(hitl.prev_role_of(["scriptwriter", "worldbuilder"]),
                         "scriptwriter")
        self.assertEqual(hitl.prev_role_of(["assetdesigner", "worldbuilder"]),
                         "assetdesigner")

    def test_locked_to_prereq_not_roles(self):
        """★ 回归锁：`guards.ROLES`（8 个、`director` 排第一）**不是**本函数的依据。

        两者顺序不同，混用会算错"上一步是谁" ⇒ 打回打错人 ⇒ 整轮白跑。
        """
        from v5.guards import PREREQ as G_PREREQ, ROLES as G_ROLES
        self.assertEqual(len(G_PREREQ), 7, "被派发的角色是 7 个（不含 director）")
        self.assertEqual(len(G_ROLES), 8, "ROLES 含 director")
        self.assertEqual(G_ROLES[0], "director", "ROLES 里 director 排第一")
        self.assertNotIn("director", G_PREREQ)
        # 即使有人把 director 塞进来，也**不会**被当成可打回的目标
        self.assertEqual(hitl.prev_role_of(["director", "worldbuilder"]), "worldbuilder")

    def test_empty_means_director(self):
        """★★ 2026-09-19：什么都没完成时，人刚看到的是 **`director/director.md`**。

        链路第一停发生在「派发 worldbuilder 之前」，那一刻唯一的产物就是制作规格。
        旧实现返回空串 ⇒ 确认条显示"刚产出：—"、且 `decide` 的 `redo` 默认值
        （取自 `prev_role`）为空 ⇒ **规格写错了却挑不出可打回的对象**。
        """
        self.assertEqual(hitl.prev_role_of([]), hitl.DIRECTOR)
        self.assertEqual(hitl.prev_role_of(None), hitl.DIRECTOR)

    def test_pending_carries_prev_role(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            hitl.record_pending(root, thread_id="t", run_id="r",
                                done_roles=["worldbuilder", "assetdesigner",
                                            "plotdesigner"])
            pd = hitl.read_pending(root)
            self.assertEqual(pd["prev_role"], "plotdesigner")
            self.assertEqual(pd["next_role"], "scriptwriter",
                             "下一个待派发 = 依赖序里第一个未完成的")


class TestDirectorAsRedoTarget(unittest.TestCase):
    """★★ 2026-09-19：**制作规格也能被打回**。

    为什么必要：第一停时人唯一能审的就是 `director/director.md`（片长/画幅/音频模式/
    视觉基准）。此前它既不能确认也不能打回 ⇒ 规格错了只能一路往下走，
    到 reviewer 之后才发现 —— 那时打回任何单个角色都救不回来（它们照规格写的）。

    判据是**文件在不在盘**（director 不是被派发的角色，`phases["director"]` 永远不存在）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _spec(self, write=True):
        p = self.root / "director"
        p.mkdir(exist_ok=True)
        if write:
            (p / "director.md").write_text("# 制作规格\n目标时长 90 秒\n", encoding="utf-8")

    def test_targets_include_director_only_when_spec_on_disk(self):
        self.assertEqual(hitl.redo_targets_of(self.root, []), [],
                         "规格文档还没落盘 ⇒ **不给**这个选项（不假装它已完成）")
        self._spec(True)
        self.assertEqual(hitl.redo_targets_of(self.root, []), [hitl.DIRECTOR])
        self.assertEqual(hitl.redo_targets_of(self.root, ["worldbuilder"]),
                         [hitl.DIRECTOR, "worldbuilder"], "director 排最前（依赖序）")

    def test_pending_carries_redo_targets(self):
        self._spec(True)
        hitl.record_pending(self.root, thread_id="t", run_id="r", done_roles=[])
        pd = hitl.read_pending(self.root)
        self.assertEqual(pd["redo_targets"], [hitl.DIRECTOR])
        self.assertEqual(pd["prev_role"], hitl.DIRECTOR)

    def test_decide_accepts_director(self):
        self._spec(True)
        hitl.record_pending(self.root, thread_id="t", run_id="r", done_roles=[])
        hitl.decide(self.root, "redo", target=hitl.DIRECTOR, note="片长定错了")
        self.assertEqual(hitl.read_decision(self.root)["target"], hitl.DIRECTOR)

    def test_decide_defaults_to_director(self):
        """不带 target 时默认打回 `prev_role` —— 第一停就是 director。"""
        self._spec(True)
        hitl.record_pending(self.root, thread_id="t", run_id="r", done_roles=[])
        hitl.decide(self.root, "redo", note="规格不对")
        self.assertEqual(hitl.read_decision(self.root)["target"], hitl.DIRECTOR)

    def test_decide_rejects_director_when_spec_absent(self):
        """规格文档不在盘 ⇒ 它不是可打回目标（拒绝要说得出原因）。"""
        hitl.record_pending(self.root, thread_id="t", run_id="r", done_roles=[])
        with self.assertRaises(ValueError) as c:
            hitl.decide(self.root, "redo", target=hitl.DIRECTOR, note="x")
        self.assertIn("无效", str(c.exception))

    def test_stale_pending_without_field_still_works(self):
        """旧 `pending.json`（没有 `redo_targets` 字段）⇒ 回落 `done_roles`，行为不变。"""
        self._spec(True)
        hitl.record_pending(self.root, thread_id="t", run_id="r",
                            done_roles=["worldbuilder"])
        pd = hitl.read_pending(self.root)
        pd.pop("redo_targets")                       # 模拟改造前写下的文件
        hitl.pending_path(self.root).write_text(json.dumps(pd, ensure_ascii=False),
                                                encoding="utf-8")
        hitl.decide(self.root, "redo", target="worldbuilder")
        self.assertEqual(hitl.read_decision(self.root)["target"], "worldbuilder")
        with self.assertRaises(ValueError):
            hitl.decide(self.root, "redo", target=hitl.DIRECTOR)   # 该文件里没有它


class TestHitlState(unittest.TestCase):
    """`webmap.hitl_state` —— 前端 `GET /projects/{pid}/hitl` 读的那份状态。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.proj = self.root / "demo-project"
        self.proj.mkdir(parents=True)
        # `hitl_state` 会读 dev server 状态文件（`config.PROJECT_ROOT/.tmp/...`）
        # ⇒ 指到临时目录，免得读到本机真实状态（测试不该依赖环境）
        self._p = mock.patch.object(config, "PROJECT_ROOT", self.root)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self.tmp.cleanup()

    def test_no_pending(self):
        from v5 import webmap
        st = webmap.hitl_state(self.proj)
        self.assertFalse(st["pending"])
        self.assertEqual(st["redo_targets"], [])
        self.assertIsNone(st["manual_steps"],
                          "没有 dev 状态文件 ⇒ **不知道**（`None`），不能报成 False")

    def test_pending_shape(self):
        from v5 import webmap
        hitl.record_pending(self.proj, thread_id="t", run_id="r",
                            done_roles=["worldbuilder", "assetdesigner"])
        st = webmap.hitl_state(self.proj)
        self.assertTrue(st["pending"])
        self.assertEqual(st["prev_role"], "assetdesigner", "刚产出的是资产卡")
        self.assertEqual(st["next_role"], "plotdesigner")
        self.assertEqual(st["redo_targets"], ["worldbuilder", "assetdesigner"],
                         "可打回 = 已完成的角色（依赖序）")
        self.assertEqual(st["decision"], "", "还没人做决定")
        self.assertEqual(st["stale_decision"], "")
        self.assertTrue(st["stamp"])

    def test_first_stop_offers_director_as_target(self):
        """★ 2026-09-19 第一停：`prev_role` = 制作规格，且**它可以被打回**。

        第一停发生在「派发 worldbuilder 之前」，人刚看到的就是 `director/director.md`
        （片长/画幅/音频模式/视觉基准）。此前这里 `prev_role` 是空串、
        `redo_targets` 也是空 —— 规格写错了人却挑不出可打回的对象。
        """
        from v5 import webmap
        spec = self.proj / "director" / "director.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("# 制作规格\n目标时长 90 秒\n", encoding="utf-8")
        hitl.record_pending(self.proj, thread_id="t", run_id="r", done_roles=[])
        st = webmap.hitl_state(self.proj)
        self.assertEqual(st["prev_role"], hitl.DIRECTOR, "刚产出的是制作规格")
        self.assertEqual(st["next_role"], "worldbuilder")
        self.assertEqual(st["redo_targets"], [hitl.DIRECTOR])

    def test_first_stop_without_spec_has_no_target_but_says_so(self):
        """规格文档不在盘 ⇒ **不给**可打回目标（不假装），但"刚产出"仍如实说是它。"""
        from v5 import webmap
        hitl.record_pending(self.proj, thread_id="t", run_id="r", done_roles=[])
        st = webmap.hitl_state(self.proj)
        self.assertEqual(st["redo_targets"], [])
        self.assertEqual(st["prev_role"], hitl.DIRECTOR)

    def test_stale_decision_is_surfaced_not_hidden(self):
        """戳不对的决定**不算当前决定**，但必须**如实报出来**。

        藏着不说 ⇒ 用户以为"我批过了"，链路却在干等（本项目忌"静默"）。
        """
        from v5 import webmap
        hitl.record_pending(self.proj, thread_id="t", run_id="r",
                            done_roles=["worldbuilder"])
        hitl.decide(self.proj, "approve")
        hitl.record_pending(self.proj, thread_id="t", run_id="r2")   # 又前进一步
        st = webmap.hitl_state(self.proj)
        self.assertEqual(st["decision"], "", "失效的决定不算数")
        self.assertEqual(st["stale_decision"], "approve", "但要报出来")

    def test_valid_decision_is_not_marked_stale(self):
        from v5 import webmap
        hitl.record_pending(self.proj, thread_id="t", run_id="r",
                            done_roles=["worldbuilder"])
        hitl.decide(self.proj, "approve", by="老王")
        st = webmap.hitl_state(self.proj)
        self.assertEqual(st["decision"], "approve")
        self.assertEqual(st["stale_decision"], "")

    def test_manual_steps_comes_from_dev_state_file(self):
        """★ `manual_steps` 必须取 **dev 状态文件**，不能取本进程的环境变量。

        开关由 **dev server 进程**在编译期读（`orchestrator.py:443`），而本函数跑在
        **shim 进程**里 —— 读 `config.APPROVE_EACH_ROLE` **必然误报**（会稳定说"没开"）。
        """
        from v5 import webmap
        d = self.root / ".tmp"
        d.mkdir(parents=True)
        (d / "web-devserver.json").write_text('{"manual_steps": true}', encoding="utf-8")
        self.assertTrue(webmap.hitl_state(self.proj)["manual_steps"])

    def test_progress_carries_hitl(self):
        """`progress()` 的 `v5` 块里也要有 `hitl`（同一函数，判据不写两份）。"""
        from v5 import webmap
        (self.proj / "brief.json").write_text(
            '{"topic":"演示","pack":"shortdrama","episodes":1}', encoding="utf-8")
        hitl.record_pending(self.proj, thread_id="t", run_id="r",
                            done_roles=["worldbuilder"])
        self.assertTrue(webmap.progress(self.proj)["v5"]["hitl"]["pending"])


class TestRedoFlow(unittest.TestCase):
    """打回的**动作序列** —— 即 `drive_chain` 收到 `redo` 后做的那三句。

    为什么在这里照抄一遍：`drive_chain.py` 要在有 dev server 时才跑得起来（单测够不着），
    而这段动作**会动用户的产物**（移进 `.rerun_backup/`）—— 风险最高的一段，
    必须有个能随时跑的回归锁。顺序与 `drive_chain` 的 redo 分支**逐句对应**。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "demo"
        for role, rel in (("worldbuilder", "worldbuilder/worldbuilder.md"),
                          ("assetdesigner", "assetdesigner/assets.md"),
                          ("plotdesigner", "plotdesigner/episodes.md")):
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# %s 的产物\n" % role, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_redo_moves_downstream_products_and_clears_phases(self):
        from v5 import guards

        guards.save_manifest(self.root, {"episode_index": 1, "phases": {"1": {
            "worldbuilder": "complete", "assetdesigner": "complete",
            "plotdesigner": "complete", "scriptwriter": "complete"}}})

        # ── ↓↓↓ 与 `scripts/drive_chain.py` 的 redo 分支逐句一致 ↓↓↓ ──
        m = guards.load_manifest(self.root)
        reset = guards.reset_from("assetdesigner", m, self.root, 1)
        guards.save_manifest(self.root, m)
        # ── ↑↑↑ ──

        self.assertEqual(reset, ["assetdesigner", "plotdesigner", "scriptwriter",
                                 "dialogue", "scenedesigner", "reviewer"],
                         "打回必须**连下游一起**清 —— 否则基于旧内容写的下游产物全作废，"
                         "等于没打回")

        # 旧产物**必须挪走**：`done_roles()` 是按"文件在不在"判进度的
        # （`drive_chain.py:93-105`）⇒ 不挪就会被判"这步已完成" ⇒ **重跑空转**
        self.assertFalse((self.root / "assetdesigner/assets.md").exists())
        self.assertFalse((self.root / "plotdesigner/episodes.md").exists())
        moved = list((self.root / ".rerun_backup").rglob("assets.md"))
        self.assertTrue(moved, "旧产物要进 .rerun_backup/（可人工回捞）")

        # **上游不动**：用户已经确认通过的东西不能被覆盖
        self.assertTrue((self.root / "worldbuilder/worldbuilder.md").exists(),
                        "上游产物必须原样保留（用户已认可）")
        m2 = guards.load_manifest(self.root)
        self.assertNotIn("assetdesigner", m2["phases"]["1"])
        self.assertEqual(m2["phases"]["1"].get("worldbuilder"), "complete",
                         "上游的记账必须保留")

    def test_redo_from_most_upstream_resets_everything(self):
        """打回第一个角色（worldbuilder）⇒ 除它自己外全清 —— 全片重做。"""
        from v5 import guards
        guards.save_manifest(self.root, {"episode_index": 1, "phases": {"1": {
            "worldbuilder": "complete", "assetdesigner": "complete"}}})
        m = guards.load_manifest(self.root)
        reset = guards.reset_from("worldbuilder", m, self.root, 1)
        guards.save_manifest(self.root, m)
        self.assertEqual(reset[0], "worldbuilder")
        self.assertIn("assetdesigner", reset, "assetdesigner 在 worldbuilder **下游**，要一起清")
        self.assertIn("reviewer", reset)
        self.assertFalse((self.root / "worldbuilder/worldbuilder.md").exists())
        self.assertFalse((self.root / "assetdesigner/assets.md").exists())

    def test_redo_director_resets_spec_and_every_role(self):
        """★★ 2026-09-19：打回**制作规格** ⇒ 规格文档 + **全部 7 个角色**一起重做。

        `reset_from("director")` 的依据是 `guards.ROLES[0:]`（director 排第一）——
        所以它返回 **8 项**（director + 7 角色），而不是"没有下游、只挪一个文件"。
        这一条同时锁住"规格一变，下游全部作废"这个语义。
        """
        from v5 import guards
        spec = self.root / "director" / "director.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("# 制作规格\n目标时长 90 秒\n", encoding="utf-8")
        guards.save_manifest(self.root, {"episode_index": 1, "phases": {"1": {
            "worldbuilder": "complete", "assetdesigner": "complete"}}})

        m = guards.load_manifest(self.root)
        reset = guards.reset_from(hitl.DIRECTOR, m, self.root, 1)
        guards.save_manifest(self.root, m)

        self.assertEqual(len(reset), 8, reset)
        self.assertEqual(reset[0], hitl.DIRECTOR)
        for r in ("worldbuilder", "assetdesigner", "plotdesigner", "reviewer"):
            self.assertIn(r, reset, "规格变了 ⇒ 所有角色产物都作废")
        self.assertFalse(spec.exists(), "规格文档必须挪走（否则重写变成'文件还在'⇒ 空转）")
        self.assertFalse((self.root / "worldbuilder/worldbuilder.md").exists())
        self.assertTrue(list((self.root / ".rerun_backup").rglob("director.md")),
                        "旧规格要进 .rerun_backup/（可人工回捞）")

    def test_redo_message_semantics_differ_for_director(self):
        """给导演的那条消息：打回角色 = 只重派它；打回规格 = 重写规格 + 整链重做。"""
        import importlib.util
        drv = Path(__file__).resolve().parents[1] / "scripts" / "drive_chain.py"
        spec = importlib.util.spec_from_file_location("_drive_chain_probe", drv)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        role_msg = mod.redo_message("scenedesigner", "第 3 镜机位不对")
        self.assertIn("只重新派发 `scenedesigner`", role_msg)
        self.assertIn("不要重跑它的**上游**", role_msg)

        spec_msg = mod.redo_message("director", "片长定成 90 秒了，要 120 秒")
        self.assertIn("先重写 `/director/director.md`", spec_msg)
        self.assertIn("重新派发全部 7 个角色", spec_msg)
        self.assertIn("120 秒", spec_msg, "用户写的原因要原样带过去")
        self.assertNotIn("只重新派发 `director`", spec_msg,
                         "director 不是被派发的角色 —— 不许让导演去 task(\"director\")")


class TestDriveChainUntil(unittest.TestCase):
    """`--until` 的达成判据（2026-09-19 前端两段式的骨架）。

    `drive_chain.reached(got, until)`：**目标角色 + 它的全部上游**都在盘上才算达成。
    ⛔ 只判"目标在不在"是错的：上游缺失也会算达成 ⇒ 下游产物基于不存在的上游写出来
    （等于假产物），而前端会拿它当"剧本正文已就绪"。
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        drv = Path(__file__).resolve().parents[1] / "scripts" / "drive_chain.py"
        spec = importlib.util.spec_from_file_location("_dc_until_probe", drv)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def test_full_chain_semantics_unchanged(self):
        m = self.m
        self.assertFalse(m.reached([], ""))
        self.assertFalse(m.reached(list(m.ROLES[:6]), ""), "少一个都不算跑完")
        self.assertTrue(m.reached(list(m.ROLES), ""))

    def test_until_needs_target_and_upstream(self):
        m = self.m
        up_to = list(m.ROLES[:m.ROLES.index("scriptwriter") + 1])
        self.assertTrue(m.reached(up_to, "scriptwriter"))
        self.assertTrue(m.reached(list(m.ROLES), "scriptwriter"), "全齐当然算")
        self.assertFalse(m.reached(m.ROLES[:3], "scriptwriter"), "上游没齐")
        self.assertFalse(m.reached(["scriptwriter"], "scriptwriter"),
                         "★ 只有它自己 ⇒ **不算达成**（否则就是假产物）")

    def test_unknown_until_is_not_reached(self):
        self.assertFalse(self.m.reached(list(self.m.ROLES), "不存在的角色"))


class TestInterruptOnSwitch(unittest.TestCase):
    """`interrupt_on` 的开关 —— **默认必须关**。

    `scripts/drive_chain.py` 是全自动跑完整条链的；默认开启会让每条链在第一步
    就挂起等人（今天实测：empty-flat 的创作链就是脚本无人值守跑完的）。
    """

    def test_default_off_and_explicit_on(self):
        from v5 import orchestrator

        with mock.patch.object(config, "APPROVE_EACH_ROLE", False):
            self.assertIsNone(orchestrator._interrupt_on(),
                              "默认关：否则全自动链路会挂住")
        with mock.patch.object(config, "APPROVE_EACH_ROLE", True):
            self.assertEqual(orchestrator._interrupt_on(), {"task": True},
                             "开时拦 `task` —— 每次派发子代理前挂起")


if __name__ == "__main__":
    unittest.main()
