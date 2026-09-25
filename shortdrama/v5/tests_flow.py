# -*- coding: utf-8 -*-
"""媒体链与守卫的端到端自测（假模型，不调任何 API）。

验证四件事：
1. 8 个角色节点按静态边依次执行（模型无法跳过）
2. phases 逐节点落盘（守卫的记账修复）
3. 未跑满 8 角色 → 媒体节点被 media_gate 拦住
4. reviewer 判定 false → 条件边回退到指定角色；回退超限 → 强制放行
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402

from v5 import config, guards  # noqa: E402
from v5.media import scaffold  # noqa: E402

ARTIFACTS = {
    "director": "director/director.md",
    "worldbuilder": "worldbuilder/worldbuilder.md",
    "assetdesigner": "assetdesigner/assets.md",
    "plotdesigner": "plotdesigner/episodes.md",
    "scriptwriter": "scriptwriter/scriptwriter_ep1.md",
    "dialogue": "dialogue/dialogue.md",
    "scenedesigner": "scenedesigner/scenedesigner.md",
    "reviewer": "reviewer/review.md",
}


def _write_artifacts(root: Path, roles) -> None:
    for role in roles:
        p = root / ARTIFACTS[role]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("内容 " * 30, encoding="utf-8")


def _seed_passing_manifest(root: Path) -> None:
    """写一份"创作链已完成、评审已通过"的 manifest。

    为什么现在需要它（2026-09-10）：`guards.media_gate("render")` 已收进
    `pipeline.run`（媒体链唯一入口）。于是**直接调 `pipeline.run` 的测试**
    必须先交代"8 角色全 complete + 评审通过"，否则会被门拦下——
    这正是那条门应有的强制力，不是测试的负担。
    """
    m = {"phases": {r: "complete" for r in guards.ROLES},
         "review": {"passed": True}}
    guards.save_manifest(root, m)


def _fake_chat(role: str, max_tokens: int = 8192):
    """无限假模型：每个节点都返回一句文本，不写文件（产物由测试预置）。

    bind_tools 必须返回自身——create_agent 装配时会对模型调 bind_tools，
    GenericFakeChatModel 默认抛 NotImplementedError。
    """
    class _Endless(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.messages = iter(["（假模型输出）"])
            return super()._generate(messages, stop=stop, run_manager=run_manager,
                                     **kwargs)

    return _Endless(messages=iter(["（假模型输出）"]))


_REVIEW_TEXT = "```yaml\npass: true\nrerun: []\n```"


def _fake_chat_writing(role: str, max_tokens: int = 8192):
    """会**真的写产物**的假模型：第一轮发 write_file 工具调用，第二轮收工。

    用它才能测出"回退重跑后产物重新落盘 → 流程能走完"的完整闭环。
    reviewer 的产物内容取模块级 _REVIEW_TEXT（模拟模型输出的判定块）。
    """
    from langchain_core.messages import AIMessage, ToolMessage

    body = _REVIEW_TEXT if role == "reviewer" else ("内容 " * 60)

    class _Writer(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            done = any(isinstance(m, ToolMessage) for m in messages)
            if done:
                self.messages = iter(["（完成）"])
            else:
                self.messages = iter([AIMessage(content="", tool_calls=[{
                    "name": "write_file",
                    "args": {"file_path": "/" + ARTIFACTS[role],
                             "content": body},
                    "id": "call-1"}])])
            return super()._generate(messages, stop=stop, run_manager=run_manager,
                                     **kwargs)

    return _Writer(messages=iter([""]))


class TestExternalAgentLockout(unittest.TestCase):
    """外部调用方的准入契约。

    **2026-09-10 策略变更**（用户决策）：`--monitor` 只读铁律是**调试期纪律**，
    不是架构——外部 Agent 本是系统主入口，长期锁死会限制可用性。
    现改为「开放 + 三道门守输入」：
      · 默认仍锁（稳妥迁移），`--resume-media`/`--stills-only` 需放行；
      · `SHORTDRAMA_OPEN_CHAIN=1` → 开放全链路，改由 brief 门 /
        分镜契约门 / 资产契约门守住输入。
    `--media-only` 这个"完全绕过创作链"的旧旁路**仍然删除**（它连三道门都没有）。
    """

    def test_media_only_flag_is_gone(self):
        """--media-only 旧名必须不存在（它连三道输入门都绕过，不能复活）。"""
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "v5.series", "x", "--media-only"],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unrecognized arguments", r.stderr)

    def test_resume_media_refused_by_default(self):
        """默认（未开放）→ SystemExit，且必须告诉人两条放行路径。"""
        import asyncio
        from v5 import series
        with _env_cleared(series.RESUME_ENV):
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(series.run("__lockout__", resume_media=True))
            msg = str(cm.exception)
        self.assertIn("RESUME-BLOCK", msg)
        self.assertIn("--monitor", msg, "必须保留只读观察这条路")
        self.assertIn("SHORTDRAMA_OPEN_CHAIN", msg, "必须告知开放全链路的方式")

    def test_stills_only_refused_by_default(self):
        import asyncio
        from v5 import series
        with _env_cleared(series.RESUME_ENV):
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(series.run("__lockout__", stills_only=True))
            self.assertIn("RESUME-BLOCK", str(cm.exception))

    def test_open_chain_allows_and_applies_three_gates(self):
        """开放后：放行，且**必须**跑三道输入门（这是开放的代价与前提）。"""
        import asyncio
        from unittest import mock

        from v5 import config, series
        from v5.media import pipeline

        with mock.patch.object(config, "OPEN_CHAIN", True), \
                mock.patch.object(series, "_input_gates") as gates, \
                mock.patch.object(pipeline, "run", return_value={"status": "ok"}):
            out = asyncio.run(series.run("__open__", stills_only=True))
        self.assertEqual(out.get("status"), "ok")
        self.assertEqual(gates.call_count, 1,
                         "开放全链路后必须过 brief/分镜/资产三道输入门")

    def test_monitor_is_read_only(self):
        """监控模式：不推进图、不改 manifest、项目不存在也不代建。"""
        import asyncio
        import contextlib
        import io
        from v5 import series
        root = Path(tempfile.mkdtemp(prefix="mon_"))
        manifest = root / ".agent_state.json"
        manifest.write_text(json.dumps({"phases": {"director": "complete"}}),
                            encoding="utf-8")
        before = manifest.read_text(encoding="utf-8")
        real = series.config.PROJECTS_DIR
        series.config.PROJECTS_DIR = root.parent
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                out = asyncio.run(series.monitor(root.name))
        finally:
            series.config.PROJECTS_DIR = real
        self.assertEqual(manifest.read_text(encoding="utf-8"), before)
        self.assertEqual(out["status"], "monitor_done")
        self.assertIn("director", out["phases"])

    def test_monitor_stops_on_problem(self):
        """产物缺失 → 监控只报告并停止，不修复。"""
        import asyncio
        import contextlib
        import io
        from v5 import series
        root = Path(tempfile.mkdtemp(prefix="mon_"))
        (root / ".agent_state.json").write_text(
            json.dumps({"phases": {"director": "complete"}}), encoding="utf-8")
        real = series.config.PROJECTS_DIR
        series.config.PROJECTS_DIR = root.parent
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                out = asyncio.run(series.monitor(root.name))
        finally:
            series.config.PROJECTS_DIR = real
        self.assertTrue(out["problems"])
        self.assertIn("产物缺失", out["problems"][0])


class TestApprovalGates(unittest.TestCase):
    """审批门：**产物版本级**批准，不是橡皮图章（2026-09-10 人在环决策）。

    在此之前"人工验收"只是纪律（`--stills-only` + 环境变量 + 注释），
    没人能说清是谁、何时、依据哪一版批准的。本组锁住新契约：
      · 未批准 → 阻断该阶段；
      · 批准后**产物一变就自动作废** → 必须重新验收。
    """

    def _root(self):
        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        (root / "scenedesigner").mkdir(parents=True)
        (root / "scenedesigner" / "scenedesigner.md").write_text(
            "## 第1幕｜车内-夜｜S1 / 5s\n\n| 镜头 | 画面 |\n|---|---|\n| 1 | 甲 |\n",
            encoding="utf-8")
        (root / "media" / "ep1").mkdir(parents=True)
        (root / "media" / "ep1" / "stills.json").write_text("{}", encoding="utf-8")
        return d, root

    def test_unknown_gate_rejected(self):
        from v5.media import approvals

        d, root = self._root()
        with d, self.assertRaises(ValueError):
            approvals.approve(root, "whatever")

    def test_approve_then_check_passes(self):
        from v5.media import approvals

        d, root = self._root()
        with d:
            ok, _why = approvals.check(root, "stills")
            self.assertFalse(ok, "未批准时必须判否")
            approvals.approve(root, "stills", by="tester", note="看过 18 张静帧")
            ok, why = approvals.check(root, "stills")
            self.assertTrue(ok)
            self.assertIn("tester", why)

    def test_artifact_change_voids_approval(self):
        """**最关键的一条**：上游产物一变，审批自动作废。

        否则静帧被重画十轮、审批还挂着，审批就退化成橡皮图章。
        """
        from v5.media import approvals

        d, root = self._root()
        with d:
            approvals.approve(root, "stills", by="tester")
            self.assertTrue(approvals.is_approved(root, "stills"))
            # 静帧产物被改写（等价于"又重画了一轮"）
            (root / "media" / "ep1" / "stills.json").write_text(
                '{"LN01": {"url": "new"}}', encoding="utf-8")
            ok, why = approvals.check(root, "stills")
            self.assertFalse(ok, "产物变了审批必须作废")
            self.assertIn("产物已变更", why)

    def test_storyboard_gate_watches_storyboard_not_stills(self):
        """分镜门盯分镜文件：改静帧不影响分镜门的批准。"""
        from v5.media import approvals

        d, root = self._root()
        with d:
            approvals.approve(root, "storyboard", by="tester")
            (root / "media" / "ep1" / "stills.json").write_text("{\"x\":1}",
                                                               encoding="utf-8")
            self.assertTrue(approvals.is_approved(root, "storyboard"))
            (root / "scenedesigner" / "scenedesigner.md").write_text("改了分镜",
                                                                     encoding="utf-8")
            self.assertFalse(approvals.is_approved(root, "storyboard"))

    def test_revoke(self):
        from v5.media import approvals

        d, root = self._root()
        with d:
            approvals.approve(root, "media", by="tester")
            self.assertTrue(approvals.is_approved(root, "media"))
            approvals.revoke(root, "media", by="tester", note="发现缺陷")
            self.assertFalse(approvals.is_approved(root, "media"))

    def test_unattributed_is_flagged_in_summary(self):
        """不署名不阻断（工具不该替人做身份管理），但必须显眼暴露。"""
        from v5.media import approvals

        d, root = self._root()
        with d:
            approvals.approve(root, "stills")
            self.assertIn("未署名", approvals.summary(root))
            approvals.approve(root, "stills", by="someone")
            self.assertNotIn("未署名", approvals.summary(root))

    def test_approval_block_at_single_entry(self):
        """REQUIRE_APPROVAL 打开且 stills 未批准 → `pipeline.run` 直接返回 blocked。

        2026-09-10：审批检查从 `series.run` 搬进 `pipeline.run`（媒体链唯一入口）。
        以前它只写在 resume 路径里，**跑全链路（graph 路径）会绕过审批门**——
        这正是"守卫写在两个调用点之一"的典型后果。
        """
        from v5 import config
        from v5.media import pipeline

        d, root = self._root()
        _seed_passing_manifest(root)      # 先过 render 门，才能验到审批门
        with d, mock.patch.object(config, "REQUIRE_APPROVAL", True):
            r = pipeline.run(root, ep=1)
        self.assertEqual(r["status"], "blocked")
        self.assertEqual(r.get("gate"), "stills",
                         "未批准时必须止于 stills 门（不得进入渲染）")

    def test_approval_passes_when_required_and_approved(self):
        import asyncio
        from unittest import mock

        from v5 import config, series
        from v5.media import approvals, pipeline

        d, root = self._root()
        real = config.PROJECTS_DIR
        config.PROJECTS_DIR = root
        try:
            with d:
                approvals.approve(root / "p", "stills", by="tester")
                with mock.patch.object(config, "OPEN_CHAIN", True), \
                        mock.patch.object(config, "REQUIRE_APPROVAL", True), \
                        mock.patch.object(series, "_input_gates"), \
                        mock.patch.object(pipeline, "run",
                                          return_value={"status": "ok"}) as run_mock:
                    out = asyncio.run(series.run("p", resume_media=True))
                self.assertEqual(out.get("status"), "ok")
                self.assertEqual(run_mock.call_count, 1)
        finally:
            config.PROJECTS_DIR = real


class TestClipQcReauditLoop(unittest.TestCase):
    """复核重拍后**必须再复核**（2026-09-10 实测事故）。

    此前 `clipqc` 只审一轮：重拍后不复核就拼接，于是重渲的 clip 带着同样的
    硬伤直接进成片。铁证 nightshift-45 LN06——重拍后仍烧着「林宇决定亲自查看 /
    影视效果 请勿模仿」字幕 + 「28mm: 57 23487 Z0035」相机参数，却因"只审一轮"
    未被拦下。

    此测试锁死两件事：
      1. 第一轮 audit 报坏 → 重拍 → **第二轮 audit 必须再被调用**；
      2. 第二轮干净后退出循环，且不重复作废。
    """

    def _run(self, audit_seq, n_shots=1, chain_names=None, tally=None):
        """用假依赖跑 pipeline.run（不碰任何 API），返回 (result, audit_mock, invalidate_mock, concat_mock)。

        tally 用于预置"累计重拍计数"（跨进程的上限依据）。
        """
        import contextlib
        from unittest import mock

        from v5.media import pipeline, scaffold, storyboard

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner.md").write_text(
                scaffold.build("t", [{"name": "店-夜",
                                      "shots": [{"seconds": 5}] * n_shots}]),
                encoding="utf-8")
            shots = storyboard.parse(
                (root / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8"))
            names = [s["name"] for s in shots]
            pick = chain_names if chain_names is not None else names
            _seed_passing_manifest(root)   # 媒体门要求创作链已完成
            pool = {n: {"name": n, "path": "/x/%s.jpg" % n, "url": "u"} for n in names}
            if tally:
                (root / "media" / "ep1").mkdir(parents=True, exist_ok=True)
                pipeline._save_requeue_tally(root, 1, tally)

            def fake_audit(ok, ss, wd, log=print, hard_keys=None):
                return audit_seq.pop(0) if audit_seq else {}

            def fake_chain(pr, ss, st, planned, ep=1, log=print, only=None):
                return {n: str(root / "clips" / (n + ".mp4")) for n in pick}

            def fake_concat(clip_dir, out):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"fake")
                return len(pick)

            a = mock.patch.object(pipeline.clipqc, "audit", side_effect=fake_audit)
            i = mock.patch.object(pipeline.clipqc, "invalidate", return_value={})
            cc = mock.patch.object(pipeline.compose, "concat", side_effect=fake_concat)
            patches = [
                mock.patch.object(pipeline.cast, "ensure", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.style, "load", lambda *a_, **k_: ""),
                mock.patch.object(pipeline.style, "wrap", lambda *a_, **k_: ""),
                mock.patch.object(pipeline.style, "still_refs_enabled", lambda *a_, **k_: False),
                mock.patch.object(pipeline.assets, "bind", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.assets, "identity_lines", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.stills, "ensure",
                                  lambda pr, ss, **k_: pool),
                mock.patch.object(pipeline.stills, "load", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.stills, "stills_dir", lambda *a_, **k_: root),
                mock.patch.object(pipeline.qc, "review", lambda *a_, **k_: {"issues": []}),
                mock.patch.object(pipeline.qc, "review_shot_type",
                                  lambda *a_, **k_: {"ok": True}),
                mock.patch.object(pipeline.video, "submit_chain", side_effect=fake_chain),
                mock.patch.object(pipeline.compose, "duration", return_value=5.0),
                # 本类测的是 clipqc 的**复审循环**，与串行/平铺无关 —— 显式锁 keyframe。
                # 否则默认 reference 会改走 submit_all（本测试只 mock 了 submit_chain）
                # → 提交失败 → 没进 clipqc → audit 调用 0 次。
                mock.patch.object(pipeline.config, "VIDEO_MODE", "keyframe"),
                a, i, cc,
            ]
            with contextlib.ExitStack() as st:
                ms = [st.enter_context(p) for p in patches]
                with contextlib.redirect_stdout(io.StringIO()):
                    res = pipeline.run(root, ep=1, max_regen=0, stills_only=False)
            return res, ms[-3], ms[-2], ms[-1]

    def test_reaudit_after_requeue(self):
        """第一轮坏 → 重拍 → 第二轮干净：audit 必须调 2 次，invalidate 只 1 次。"""
        res, audit, inv, _cc = self._run([{"LN01": ["烧字"]}, {}])
        self.assertEqual(audit.call_count, 2, "重拍后没有再次复核——硬伤会直接进成片")
        self.assertEqual(inv.call_count, 1)
        self.assertEqual(res.get("requeued"), ["LN01"])
        self.assertFalse(res.get("residual"))

    def test_residual_reported_when_not_converged(self):
        """一直坏 → 达轮数上限后如实报告 residual，不再无限重拍。"""
        res, audit, inv, _cc = self._run([{"LN01": ["烧字"]} for _ in range(10)])
        self.assertEqual(audit.call_count, config.CLIP_QC_ROUNDS + 1)
        self.assertEqual(inv.call_count, config.CLIP_QC_ROUNDS)
        self.assertEqual(res.get("residual"), ["LN01"])

    def test_missing_clip_does_not_overwrite_final(self):
        """缺镜时**不拼接、不覆盖已成片**，且不得报 ok（2026-09-10 实测事故）。

        当时 503 队列满致 3 镜渲染失败，`compose.concat` 按目录 glob 拼接，
        5 镜残缺片覆盖了完整的 8 镜成片，还返回 status=ok —— 静默丢镜。
        宁可保留旧片 + 如实报 incomplete。
        """
        res, _a, _i, concat = self._run([], n_shots=2, chain_names=["LN01"])
        self.assertEqual(res.get("status"), "incomplete")
        self.assertEqual(res.get("missing"), ["LN02"])
        self.assertEqual(res.get("expected"), 2)
        self.assertEqual(concat.call_count, 0, "缺镜时不该拼接（会覆盖已成片）")
        # 完整时仍照常拼接
        res2, _a2, _i2, concat2 = self._run([], n_shots=2)
        self.assertEqual(res2.get("status"), "ok")
        self.assertEqual(concat2.call_count, 1)

    def test_requeue_tally_roundtrip(self):
        """累计重拍计数要能跨进程存取（它正是"跨轮次"上限的依据）。"""
        from v5.media import pipeline

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual(pipeline._load_requeue_tally(root, 1), {})
            pipeline._save_requeue_tally(root, 1, {"LN01": 2, "LN03": 1})
            self.assertEqual(pipeline._load_requeue_tally(root, 1),
                             {"LN01": 2, "LN03": 1})
            # 不同集互不干扰
            self.assertEqual(pipeline._load_requeue_tally(root, 2), {})

    def test_over_tally_keeps_clip_and_reports_residual(self):
        """累计重拍达上限的镜**不再作废**，保留 clip 并记 residual。

        否则"渲出来→被作废→重渲失败→下轮再作废"无限空转，成片永远缺镜
        （2026-09-10 实测：clips 从 8 掉到 3，成片攒不齐）。

        预置 tally 让 LN01 已达上限：即使 audit 一直报坏，也**不得**再调
        invalidate，而应把 LN01 记进 residual 并保留其 clip 参与拼接。
        """
        res, audit, inv, concat = self._run(
            [{"LN01": ["烧字"]} for _ in range(10)],
            tally={"LN01": config.CLIP_QC_MAX_REQUEUE})
        self.assertEqual(inv.call_count, 0, "达上限的镜不该再被作废（会死锁）")
        self.assertEqual(res.get("residual"), ["LN01"])
        # clip 被保留 → 不缺镜 → 正常拼接
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(concat.call_count, 1)
        self.assertEqual(audit.call_count, 1, "全达上限即无可作废镜，一轮即退")


class TestClipStashNeverLosesClips(unittest.TestCase):
    """作废必须**暂存**而非删除（2026-09-10 maskparade 实测丢片事故）。

    原实现 `unlink()` 直接删。重渲要排队（1rpm + 队列满退避），外部续跑器又有
    单轮时间上限——上限到时进程被杀，被删的 clip 还没渲出替代品 → **永久丢失**。
    实测：7 镜 → 作废 5 镜 → 只渲回 2 镜 → 下轮再审再作废 → 盘上只剩 1 镜，
    `compose.concat` 因缺镜拒绝拼接，成片永远出不来。

    契约：**任何时刻被打断，clip 数都不会比循环开始时更少**。
    """

    def _root(self, names=("LN01", "LN02")):
        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        clip_dir = root / "media" / "ep1" / "clips"
        clip_dir.mkdir(parents=True)
        for n in names:
            (clip_dir / (n + ".mp4")).write_bytes(b"OLD-" + n.encode())
        return d, root, clip_dir

    def test_invalidate_moves_instead_of_deleting(self):
        """作废后 clip 必须还在盘上（只是换了位置），且返回暂存映射。"""
        from v5.media import clipqc

        d, root, clip_dir = self._root()
        with d:
            moved = clipqc.invalidate(root, ["LN01"], ep=1, log=lambda *_: None)
            self.assertFalse((clip_dir / "LN01.mp4").exists(), "原位置应已空出")
            self.assertIn("LN01", moved, "必须返回暂存路径映射")
            self.assertTrue(Path(moved["LN01"]).exists(), "clip 不能被删除，只能暂存")
            # 未涉及的镜不受影响
            self.assertTrue((clip_dir / "LN02.mp4").exists())

    def test_stash_dir_invisible_to_concat(self):
        """暂存目录必须对 `concat` 的 `LN*.mp4` 遍历不可见，否则旧片会混进成片。"""
        from v5.media import clipqc

        d, root, clip_dir = self._root()
        with d:
            clipqc.invalidate(root, ["LN01"], ep=1, log=lambda *_: None)
            seen = sorted(p.stem for p in clip_dir.glob("LN*.mp4"))
            self.assertEqual(seen, ["LN02"], "concat 只能看到未暂存的镜")

    def test_restore_puts_clip_back_and_marks_completed(self):
        """重渲没回来 → 恢复暂存，clip 回到原位且 done() 判 True（不再重提交）。"""
        from v5.media import clipqc, jobs as jobs_mod

        d, root, clip_dir = self._root()
        with d:
            moved = clipqc.invalidate(root, ["LN01"], ep=1, log=lambda *_: None)
            back = clipqc.restore(root, moved, ep=1, log=lambda *_: None)
            self.assertEqual(back, ["LN01"])
            self.assertEqual((clip_dir / "LN01.mp4").read_bytes(), b"OLD-LN01",
                             "恢复的必须是原来那个 clip")
            jobs = jobs_mod.load(root / "media" / "ep1")
            self.assertTrue(jobs_mod.done(jobs, "LN01", clip_dir),
                            "恢复后必须标 completed，否则会重复烧配额")

    def test_discard_removes_stash_on_success(self):
        """重渲成功 → 暂存被丢弃（不留垃圾）。"""
        from v5.media import clipqc

        d, root, clip_dir = self._root()
        with d:
            moved = clipqc.invalidate(root, ["LN01"], ep=1, log=lambda *_: None)
            clipqc.discard(root, moved, log=lambda *_: None)
            self.assertFalse(Path(moved["LN01"]).exists(), "成功后暂存应被清掉")

    def test_restore_leftovers_recovers_from_kill(self):
        """关键回归：暂存后被**中断**（模拟单轮上限杀进程），下次启动必须放回。"""
        from v5.media import clipqc

        d, root, clip_dir = self._root(names=("LN01", "LN02", "LN03"))
        with d:
            # 三个镜都作废，但"只渲回 1 个"就断电 —— 这正是原实现丢片的时刻
            clipqc.invalidate(root, ["LN01", "LN02", "LN03"], ep=1, log=lambda *_: None)
            self.assertEqual(len(list(clip_dir.glob("LN*.mp4"))), 0, "都进暂存了")
            # 进程重启
            got = clipqc.restore_leftovers(root, ep=1, log=lambda *_: None)
            self.assertEqual(sorted(got), ["LN01", "LN02", "LN03"])
            self.assertEqual(len(list(clip_dir.glob("LN*.mp4"))), 3,
                             "重启后 clip 数必须回到 3 —— 绝不能比开始时少")

    def test_restore_leftovers_is_idempotent(self):
        """暂存区空时不得报错、不得改动任何东西（每次都跑它）。"""
        from v5.media import clipqc

        d, root, clip_dir = self._root()
        with d:
            self.assertEqual(clipqc.restore_leftovers(root, ep=1, log=lambda *_: None), [])
            self.assertEqual(len(list(clip_dir.glob("LN*.mp4"))), 2)

    def test_pipeline_keeps_clip_when_rerender_does_not_come_back(self):
        """集成：重渲没回 → clip 被恢复 → 仍能拼接，且该镜记入 residual。

        修复前这里会因缺镜返回 incomplete（成片永远出不来）。
        """
        import contextlib
        from unittest import mock

        from v5.media import clipqc, jobs as jobs_mod, pipeline, scaffold, storyboard

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            clip_dir = root / "media" / "ep1" / "clips"
            clip_dir.mkdir(parents=True)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner.md").write_text(
                scaffold.build("t", [{"name": "店-夜",
                                      "shots": [{"seconds": 5}, {"seconds": 5}]}]),
                encoding="utf-8")
            shots = storyboard.parse(
                (root / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8"))
            names = [s["name"] for s in shots]
            _seed_passing_manifest(root)   # 媒体门要求创作链已完成
            pool = {n: {"name": n, "path": "/x/%s.jpg" % n, "url": "u"} for n in names}
            # 两镜的 clip 都在盘上且 job=completed（模拟"已渲好一轮"）
            for n in names:
                (clip_dir / (n + ".mp4")).write_bytes(b"OLD-" + n.encode())
            jobs = {}
            for n in names:
                jobs_mod.mark(jobs, n, "completed", local=str(clip_dir / (n + ".mp4")))
            jobs_mod.save(root / "media" / "ep1", jobs)

            def fake_audit(ok, ss, wd, log=print, hard_keys=None):
                # 每轮都报两镜都坏（模拟 QC 非确定性：重渲完仍判不合格）
                return {n: ["烧字"] for n in ok}

            calls = {"n": 0}

            def fake_chain(pr, ss, st, planned, ep=1, log=print, only=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    # 首次渲染：正常返回（ok 由此建立）
                    return {n: str(clip_dir / (n + ".mp4")) for n in names}
                return {}      # 重渲：一个都没回来（队列拥堵 / 进程被上限杀掉）

            patches = [
                mock.patch.object(pipeline.cast, "ensure", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.style, "load", lambda *a_, **k_: ""),
                mock.patch.object(pipeline.style, "wrap", lambda *a_, **k_: ""),
                mock.patch.object(pipeline.style, "still_refs_enabled", lambda *a_, **k_: False),
                mock.patch.object(pipeline.assets, "bind", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.assets, "identity_lines", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.stills, "ensure", lambda pr, ss, **k_: pool),
                mock.patch.object(pipeline.stills, "load", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.stills, "stills_dir", lambda *a_, **k_: root),
                mock.patch.object(pipeline.qc, "review", lambda *a_, **k_: {"issues": []}),
                mock.patch.object(pipeline.qc, "review_shot_type",
                                  lambda *a_, **k_: {"ok": True}),
                mock.patch.object(pipeline.video, "submit_chain",
                                  side_effect=fake_chain),
                mock.patch.object(pipeline.compose, "duration", return_value=5.0),
                mock.patch.object(pipeline.clipqc, "audit", side_effect=fake_audit),
            ]
            with contextlib.ExitStack() as st:
                for p in patches:
                    st.enter_context(p)
                with contextlib.redirect_stdout(io.StringIO()):
                    res = pipeline.run(root, ep=1, max_regen=0, stills_only=False)

            self.assertEqual(res.get("status"), "ok",
                             "重渲没回来时靠恢复暂存，成片必须仍能拼出来")
            self.assertEqual(sorted(res.get("residual") or []), sorted(names))
            self.assertEqual(len(list(clip_dir.glob("LN*.mp4"))), 2,
                             "恢复后两镜都要在盘上")
            self.assertFalse((clip_dir / ".clipqc_bad").exists()
                             and list((clip_dir / ".clipqc_bad").glob("*.mp4")),
                             "恢复过的暂存区不该留残片")


class TestStillsResilienceAgainstMidRunKill(unittest.TestCase):
    """静帧跑到一半被杀 → 重启**不能**把已生成的静帧全部重烧（2026-09-13 实测现场）。

    现场（paper-crane 真跑时抓到的）：盘上 **19 张 jpg + 19 个 `.jpg.url` 边车**，
    而 `stills.json` **是空的** —— 因为 `_save` 只在**整批跑完之后**调一次。
    重启后 `cur = {}` → 幂等判据 `cur.get("url") and local.exists()` 不成立 →
    **整批重烧**（配额 + 分钟级时间全白费）。今天已经发生过一次媒体链被沙箱回收，
    所以这不是理论风险。

    对比：clip 侧没这个问题 —— `jobs_mod.save` 每次提交都落盘。
    """

    def _seed(self, root: Path):
        sd = root / "media" / "ep1" / "stills"
        sd.mkdir(parents=True)
        (sd / "LN01.jpg").write_bytes(b"JPG")
        (sd / "LN01.jpg.url").write_text("https://cdn/x.png", encoding="utf-8")
        return sd

    def test_sidecar_prevents_regeneration(self):
        """图 + url 边车都在盘 → 直接复用，一次生图调用都不该有。"""
        from v5.media import providers, stills

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._seed(root)
            with mock.patch.object(providers, "gen_image") as g:
                data = stills.ensure(root, [{"name": "LN01", "seconds": 5}], ep=1,
                                     log=lambda *_: None)
            self.assertEqual(g.call_count, 0, "盘上已有图 + 边车 → 不该重新生图")
            self.assertEqual(data["LN01"]["url"], "https://cdn/x.png")
            # 兜底之后 json 要落盘，下次走常规判据
            self.assertTrue((root / "media" / "ep1" / "stills.json").exists())

    def test_force_still_regenerates(self):
        """`force=True`（`--from still` 用它）必须照常重画 —— 兜底不许挡路。"""
        from v5.media import providers, stills

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._seed(root)
            with mock.patch.object(providers, "gen_image",
                                   return_value=("p", "https://cdn/new.png")) as g, \
                    mock.patch.object(stills, "_download"):
                data = stills.ensure(root, [{"name": "LN01", "seconds": 5}], ep=1,
                                     force=True, log=lambda *_: None)
            self.assertEqual(g.call_count, 1, "force 时必须重画")
            self.assertEqual(data["LN01"]["url"], "https://cdn/new.png")

    def test_sidecar_ignored_when_image_missing(self):
        """只有边车、图不在盘 → 不能当成已完成（否则会留下无图的镜）。"""
        from v5.media import providers, stills

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            sd = self._seed(root)
            (sd / "LN01.jpg").unlink()
            with mock.patch.object(providers, "gen_image",
                                   return_value=("p", "https://cdn/new.png")) as g, \
                    mock.patch.object(stills, "_download"):
                stills.ensure(root, [{"name": "LN01", "seconds": 5}], ep=1,
                              log=lambda *_: None)
            self.assertEqual(g.call_count, 1, "图不在盘 → 必须重画")


class TestSingleShotRerender(unittest.TestCase):
    """单镜重渲（`../docs-archive-20260918/spec-qc-modes-and-rerender.md` §3，2026-09-13）。

    **`only` 是同一入口的受限调用，不是第二个入口** —— 这组用例守的就是这句话：
    gate / 记账 / 幂等 / 限流全部照常，只有"作用域"被收窄。

    事故依据（都在 spec 里）：
      · 2026-09-10 `--resume-media` = 第二入口 → 绕过 gate 与记账 → 重复渲染烧配额；
      · 实测 6 镜 60 秒的片：视频生成 **1.6 分**，clipqc **18 分且未完** ——
        重渲若顺带跑全片复核，等于把"检查比生成贵 10 倍"再付一遍；
      · `restore_leftovers` 会拿陈旧暂存覆盖新片 → 重渲**安静地等于没做**。
    """

    def _run(self, *, n_shots=3, only=None, from_still=False, media_loop=None,
             rerender_ok=True):
        """用假依赖跑 `pipeline.run`。返回 (res, calls, disk)。

        `disk` 里是退出临时目录**之前**采到的磁盘事实（mtime 类断言必须这样取）。
        """
        import contextlib
        from unittest import mock

        from v5.media import clipqc, jobs as jobs_mod, pipeline, scaffold, storyboard

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner.md").write_text(
                scaffold.build("t", [{"name": "店-夜",
                                      "shots": [{"seconds": 5}] * n_shots}]),
                encoding="utf-8")
            names = [s["name"] for s in storyboard.parse(
                (root / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8"))]
            _seed_passing_manifest(root)          # 媒体门要求创作链已完成
            if media_loop:                        # 模拟"成片已出过一次"
                from v5.media import approvals as _ap
                ml = dict(media_loop)
                # "CURRENT" = 用**当下**的指纹种下 —— 即"输入没变"，
                # 此时媒体门的"已渲染且无待修订"会生效（这是对照组的必要条件）。
                if ml.get("input_fingerprint") == "CURRENT":
                    ml["input_fingerprint"] = _ap.fingerprint(root, "media", 1)
                m = guards.load_manifest(root)
                m["media_loop"] = ml
                guards.save_manifest(root, m)
            ep_dir = root / "media" / "ep1"
            clip_dir = ep_dir / "clips"
            clip_dir.mkdir(parents=True)
            jobs = {}
            for n in names:
                (clip_dir / (n + ".mp4")).write_bytes(b"OLD-" + n.encode())
                jobs_mod.mark(jobs, n, "completed", local=str(clip_dir / (n + ".mp4")))
            jobs_mod.save(ep_dir, jobs)

            pool = {n: {"name": n, "path": "/x/%s.jpg" % n, "url": "u"} for n in names}
            calls = {"stills": [], "review": [], "audit": [], "submitted": []}

            def fake_ensure(pr, ss, **k):
                calls["stills"].append({"names": [s["name"] for s in ss],
                                        "force": bool(k.get("force"))})
                return pool

            def fake_review(p_, shot=None, style_spec="", style_watch_spec="", **kw):
                calls["review"].append(shot["name"] if shot else "?")
                return {"issues": []}

            def fake_chain(pr, ss, st, planned, ep=1, log=print, only=None):
                """模拟 `video.submit_chain` 的**关键契约**：非目标镜绝不提交。"""
                got = {}
                for s in ss:
                    n = s["name"]
                    if only is not None and n not in set(only):
                        if (clip_dir / (n + ".mp4")).exists():
                            got[n] = str(clip_dir / (n + ".mp4"))
                        continue
                    if jobs_mod.done(jobs_mod.load(ep_dir), n, clip_dir):
                        got[n] = str(clip_dir / (n + ".mp4"))
                        continue
                    calls["submitted"].append(n)          # 真的提交了 = 烧了配额
                    if rerender_ok:
                        (clip_dir / (n + ".mp4")).write_bytes(b"NEW-" + n.encode())
                        j = jobs_mod.load(ep_dir)
                        jobs_mod.mark(j, n, "completed",
                                      local=str(clip_dir / (n + ".mp4")))
                        jobs_mod.save(ep_dir, j)
                        got[n] = str(clip_dir / (n + ".mp4"))
                return got

            def fake_audit(clips, ss, wd, log=print, workers=None, hard_keys=None):
                calls["audit"].append(sorted(clips))
                return {}

            def fake_concat(cd, o):
                o.parent.mkdir(parents=True, exist_ok=True)
                o.write_bytes(b"FILM")
                return len(sorted(cd.glob("LN*.mp4")))

            patches = [
                mock.patch.object(pipeline.cast, "ensure", lambda *a, **k: {}),
                mock.patch.object(pipeline.style, "load", lambda *a, **k: ""),
                mock.patch.object(pipeline.style, "wrap", lambda *a, **k: ""),
                mock.patch.object(pipeline.style, "still_refs_enabled",
                                  lambda *a, **k: False),
                mock.patch.object(pipeline.assets, "bind", lambda *a, **k: {}),
                mock.patch.object(pipeline.assets, "identity_lines", lambda *a, **k: {}),
                mock.patch.object(pipeline.stills, "ensure", side_effect=fake_ensure),
                mock.patch.object(pipeline.stills, "load", lambda *a, **k: {}),
                mock.patch.object(pipeline.stills, "stills_dir", lambda *a, **k: root),
                mock.patch.object(pipeline.qc, "review", side_effect=fake_review),
                mock.patch.object(pipeline.qc, "review_shot_type",
                                  lambda *a, **k: {"ok": True}),
                mock.patch.object(pipeline.video, "submit_chain", side_effect=fake_chain),
                # keyframe 档才走 submit_chain（本类只 mock 了它）；顺带免掉
                # `extract_last_frame` 的 ffmpeg 调用（假 mp4 解不出来，纯浪费）。
                mock.patch.object(pipeline.config, "VIDEO_MODE", "keyframe"),
                mock.patch.object(pipeline.video, "extract_last_frame",
                                  lambda *a, **k: None),
                mock.patch.object(pipeline.clipqc, "audit", side_effect=fake_audit),
                mock.patch.object(pipeline.compose, "duration", return_value=5.0),
                mock.patch.object(pipeline.compose, "concat", side_effect=fake_concat),
            ]
            with contextlib.ExitStack() as st:
                for p in patches:
                    st.enter_context(p)
                with contextlib.redirect_stdout(io.StringIO()):
                    res = pipeline.run(root, ep=1, max_regen=0, stills_only=False,
                                       only=only, from_still=from_still)
            disk = {
                "clips": {n: (clip_dir / (n + ".mp4")).read_bytes()
                          for n in names if (clip_dir / (n + ".mp4")).exists()},
                "n_clips": len(list(clip_dir.glob("LN*.mp4"))),
                "stash": sorted(p.stem for p in (clip_dir / ".clipqc_bad").glob("*.mp4"))
                if (clip_dir / ".clipqc_bad").exists() else [],
                "jobs": jobs_mod.load(ep_dir),
                "clips_dir": clip_dir,
            }
            return res, calls, disk

    # ── 镜号校验（风险 #6）──────────────────────────────────────────────
    def test_unknown_shot_is_rejected_before_burning_quota(self):
        """臆造镜号必须在**提交之前**被拦下（director 可能说"重渲 LN99"）。"""
        from v5.media import pipeline, scaffold

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner.md").write_text(
                scaffold.build("t", [{"name": "店-夜",
                                      "shots": [{"seconds": 5}]}]),
                encoding="utf-8")
            res = pipeline.rerender(root, ["LN99"], log=lambda *_: None)
        self.assertEqual(res.get("status"), "failed")
        self.assertIn("unknown shots", res.get("reason", ""))
        self.assertIn("LN01", res.get("known") or [])

    def test_rerender_wrapper_delegates_to_run(self):
        """CLI 与子代理的落点都必须是 `run(only=[...])` —— 不许另开入口。"""
        from unittest import mock

        from v5.media import pipeline, scaffold

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner.md").write_text(
                scaffold.build("t", [{"name": "店-夜",
                                      "shots": [{"seconds": 5}, {"seconds": 5}]}]),
                encoding="utf-8")
            with mock.patch.object(pipeline, "run",
                                   return_value={"status": "ok"}) as m:
                pipeline.rerender(root, ["LN02"], note="周奶奶没出现",
                                  from_still=True, log=lambda *_: None)
            kw = m.call_args.kwargs
            self.assertEqual(kw.get("only"), ["LN02"])
            self.assertTrue(kw.get("from_still"))

    # ── 作用域收窄（本 spec 的核心收益）──────────────────────────────────
    def test_only_narrows_scope_to_the_target_shot(self):
        """只动目标镜：静帧不重画、静帧 QC 跳过、成片复核只审它。"""
        res, calls, disk = self._run(only=["LN03"])
        self.assertEqual([c["names"] for c in calls["stills"]], [["LN03"]])
        self.assertFalse(calls["stills"][0]["force"], "未 --from still 不该重画静帧")
        self.assertEqual(calls["review"], [], "图没变还复审只会概率翻判")
        self.assertEqual(calls["audit"], [["LN03"]],
                         "重渲不该顺带付全片 clipqc 的代价（18 分钟/6 镜）")
        self.assertEqual(calls["submitted"], ["LN03"], "只有目标镜可以烧视频配额")
        self.assertEqual(res.get("status"), "ok")

    def test_from_still_redraws_still_and_rechecks_it(self):
        """`--from still`：连静帧一起重做，且重画后必须过一遍静帧 QC。"""
        _res, calls, _disk = self._run(only=["LN03"], from_still=True)
        self.assertTrue(calls["stills"][0]["force"], "from_still 必须强制重画静帧")
        self.assertEqual(calls["review"], ["LN03"], "重画过的静帧必须复审")

    def test_no_only_keeps_full_scope(self):
        """不给 `only` 时行为与改动前**完全一致**（全片作用域）。"""
        _res, calls, _disk = self._run()
        self.assertEqual(sorted(calls["stills"][0]["names"]), ["LN01", "LN02", "LN03"])
        self.assertEqual(calls["audit"], [["LN01", "LN02", "LN03"]])
        self.assertEqual(calls["submitted"], [], "全部已完成 → 不应重复提交")

    # ── 媒体门：已渲染过也要放行重渲 ─────────────────────────────────────
    def test_gate_blocks_when_nothing_changed_but_rerender_passes(self):
        """`rendered=True` 且输入指纹未变：普通调用被门挡；`only` 必须放行。

        这是 `pending_revision` 的用途 —— 媒体门是"版本感知闸门"，
        而"人看完片子说要改某一镜"本身就是一次修订请求（它不改输入，只改意图）。
        """
        res, calls, _disk = self._run(
            only=["LN03"],
            media_loop={"rendered": True, "input_fingerprint": "CURRENT"})
        self.assertEqual(res.get("status"), "ok", "已渲染后的重渲被自己的门挡住了")
        self.assertEqual(calls["submitted"], ["LN03"])

    def test_gate_still_blocks_plain_rerun_after_rendered(self):
        """反向对照：不给 `only` 时，门的"已渲染且无待修订"仍然生效。"""
        res, calls, _disk = self._run(
            media_loop={"rendered": True, "input_fingerprint": "CURRENT"})
        self.assertEqual(res.get("status"), "blocked")
        self.assertEqual(res.get("gate"), "render")
        self.assertEqual(calls["submitted"], [], "被门挡住就一镜都不许提交")

    # ── 收敛保证：绝不丢片 ───────────────────────────────────────────────
    def test_successful_rerender_replaces_clip_and_clears_stash(self):
        res, _calls, disk = self._run(only=["LN03"])
        self.assertEqual(disk["clips"]["LN03"], b"NEW-LN03")
        self.assertEqual(disk["stash"], [], "渲回来了就该丢弃暂存的旧片")
        self.assertEqual(res.get("residual") or [], [])
        self.assertEqual(disk["n_clips"], 3, "其余镜一个都不能少")

    def test_failed_rerender_restores_old_clip_and_reports_residual(self):
        """重渲没回来 → 旧 clip 放回原位 + 记 residual（宁要有瑕疵但完整）。"""
        res, _calls, disk = self._run(only=["LN03"], rerender_ok=False)
        self.assertEqual(res.get("status"), "incomplete")
        self.assertEqual(res.get("residual"), ["LN03"])
        self.assertEqual(res.get("missing"), ["LN03"])
        self.assertEqual(disk["clips"]["LN03"], b"OLD-LN03", "旧片必须回来，绝不能丢")
        self.assertEqual(disk["stash"], [], "恢复后不该留残片")
        self.assertEqual(disk["n_clips"], 3)

    def test_stale_stash_never_clobbers_a_fresh_clip(self):
        """关键回归：暂存里的旧片**不得**覆盖 clips/ 里的新片。

        `restore_leftovers` 原本无条件放回 —— 而单镜重渲**必然**经过
        "作废 → 重渲成功 → 进程在 discard 之前被杀"这个状态，
        旧行为会在下一次启动时把重渲成果悄悄回滚（重渲等于没做）。
        """
        from v5.media import clipqc

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            clip_dir = root / "media" / "ep1" / "clips"
            clip_dir.mkdir(parents=True)
            (clip_dir / "LN01.mp4").write_bytes(b"OLD-LN01")
            clipqc.invalidate(root, ["LN01"], ep=1, log=lambda *_: None)
            (clip_dir / "LN01.mp4").write_bytes(b"NEW-LN01")   # 重渲成功
            got = clipqc.restore_leftovers(root, ep=1, log=lambda *_: None)
            self.assertEqual(got, [], "新片已在盘 → 不得再放回旧片")
            self.assertEqual((clip_dir / "LN01.mp4").read_bytes(), b"NEW-LN01")
            self.assertFalse(list((clip_dir / ".clipqc_bad").glob("*.mp4")),
                             "陈旧暂存应被丢弃")


class TestDialogueVerbatimGate(unittest.TestCase):
    """第 4 道输入门：对白逐字（2026-09-14）。

    `_storyboard_gate` / `_assets_gate` 之外新增的一道，跑在 `_input_gates` 里。
    默认**只告警不拦**（存量项目的旧 dialogue 产物多是改写版，直接拦会全卡住）；
    `SHORTDRAMA_DIALOGUE_VERBATIM_STRICT=1` 才阻断 —— 沿用 `ASSET_GATE_STRICT`
    的渐进策略。
    """

    SCRIPT = "【镜1 · 3s】玻璃门推开\n- 周平：这件没湿。\n"

    @staticmethod
    def _gate(root):
        from v5 import series          # 与文件里其它用例一致：局部导入
        return series._dialogue_gate(root)

    def _root(self, dialogue: str):
        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        (root / "dialogue").mkdir(parents=True)
        (root / "scriptwriter").mkdir(parents=True)
        (root / "dialogue" / "dialogue.md").write_text(dialogue, encoding="utf-8")
        (root / "scriptwriter" / "scriptwriter_ep1.md").write_text(
            self.SCRIPT, encoding="utf-8")
        return d, root

    def test_verbatim_passes(self):
        import contextlib
        d, root = self._root("# 对白清单\n\n- 周平：这件没湿。\n")
        with d, contextlib.redirect_stdout(io.StringIO()) as out:
            self._gate(root)
            self.assertIn("逐字门通过", out.getvalue())

    def test_rewritten_warns_by_default(self):
        import contextlib
        d, root = self._root("# 对白清单\n\n- 周平：这台我先用了。\n")
        with d, contextlib.redirect_stdout(io.StringIO()) as out:
            self._gate(root)        # 默认不抛
            self.assertIn("不在剧本原文里", out.getvalue())

    def test_strict_blocks(self):
        d, root = self._root("# 对白清单\n\n- 周平：这台我先用了。\n")
        with d, mock.patch.object(config, "DIALOGUE_VERBATIM_STRICT", True):
            with self.assertRaises(SystemExit) as cm:
                self._gate(root)
            self.assertIn("DIALOGUE-REJECT", str(cm.exception))

    def test_no_dialogue_artifact_is_not_a_failure(self):
        """产物不全（如 media-only 项目）→ 本门不适用，不是失败。"""
        import contextlib
        d, root = self._root("# 对白清单\n\n- 周平：随便\n")
        (root / "scriptwriter" / "scriptwriter_ep1.md").unlink()
        with d, contextlib.redirect_stdout(io.StringIO()) as out:
            self._gate(root)
            self.assertEqual(out.getvalue(), "")


class _SandboxDenied(BaseException):
    """沙箱批量删除保护拦下 `unlink()` 时抛的**不是 `Exception` 的子类**。

    测试必须用同一种异常才测得出东西 —— `except Exception` 抓不住它是本项目
    两次真实事故（实质出片被判成失败）的根因。
    """


class TestMediaChainExclusiveLock(unittest.TestCase):
    """媒体链独占锁（spec §3.4 风险 #2/#3，2026-09-13）。

    并发会写坏 `video_jobs.json`（单写入者假设）或拼出半截成片。
    **陈旧判定只认 mtime 心跳，不认 PID** —— 见 `_acquire_lock` 的说明
    （Windows 上 `os.kill(pid, 0)` 会真的 TerminateProcess）。
    """

    def _project(self) -> tuple:
        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        (root / "scenedesigner").mkdir(parents=True)
        (root / "scenedesigner" / "scenedesigner.md").write_text(
            scaffold.build("t", [{"name": "店-夜", "shots": [{"seconds": 5}]}]),
            encoding="utf-8")
        _seed_passing_manifest(root)
        return d, root

    def test_fresh_lock_refuses_concurrent_run(self):
        """别人的锁还活着（心跳新鲜）→ 拒绝启动，且**不推进任何东西**。"""
        from unittest import mock

        from v5.media import pipeline

        d, root = self._project()
        with d:
            with pipeline.media_lock(root, ep=1, log=lambda *_: None) as first:
                self.assertTrue(first)
                # 把实现体换成哨兵：门若失效，测试会以断言失败告终，
                # 而不是真的去跑一遍媒体链（避免测试触网）。
                with mock.patch.object(pipeline, "_run_guarded",
                                       return_value={"status": "ok"}) as impl:
                    res = pipeline.run(root, ep=1, log=lambda *_: None)
                    called = impl.call_count
            self.assertEqual(res.get("status"), "blocked")
            self.assertEqual(res.get("gate"), "media-lock")
            self.assertEqual(called, 0, "被锁挡住时不得进入实现体")

    def test_stale_lock_is_taken_over(self):
        """进程被回收后留下的锁（心跳过期）→ 后来者接管，不许卡死生产。"""
        import os as _os

        from v5 import config
        from v5.media import pipeline

        d, root = self._project()
        with d:
            lp = pipeline._lock_path(root, 1)
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text("{}", encoding="utf-8")
            old = lp.stat().st_mtime - (config.MEDIA_LOCK_STALE_S + 60)
            _os.utime(lp, (old, old))
            with pipeline.media_lock(root, ep=1, log=lambda *_: None) as got:
                self.assertTrue(got, "陈旧锁必须被接管（否则项目永久卡死）")

    def test_lock_released_after_use(self):
        """正常退出必须释放锁，否则下一次调用会被自己挡住。"""
        from v5.media import pipeline

        d, root = self._project()
        with d:
            with pipeline.media_lock(root, ep=1, log=lambda *_: None):
                pass
            self.assertFalse(pipeline._lock_path(root, 1).exists())

    def test_release_survives_blocked_unlink_and_neutralizes_lock(self):
        """删除被沙箱拦下时：不许抛异常，且必须**把 mtime 归零**。

        2026-09-14 实测：被评审门拦下的那一轮把 `media/ep1/.running` 留在了盘上，
        mtime 正好是进程退出时刻 → 下次运行在 TTL（300s）内会被**自己刚跑完的锁**
        拒之门外。`os.utime` 只改元数据，不受批量删除保护影响。
        """
        import os as _os

        from unittest import mock

        from v5.media import pipeline

        d, root = self._project()
        with d:
            lp = pipeline._lock_path(root, 1)
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text("{}", encoding="utf-8")
            with mock.patch.object(Path, "unlink", side_effect=_SandboxDenied("bulk")):
                pipeline._release_lock(lp, log=lambda *_: None)   # 不得抛异常
            self.assertTrue(lp.exists())
            self.assertLess(lp.stat().st_mtime, 10,
                            "删不掉时必须把 mtime 归零，否则下次运行会被它拦住")
            # 归零后 → 立刻被判陈旧 → 能接管
            self.assertLess(_os.path.getmtime(lp) - 0, 10)

    def test_acquire_takes_over_lock_that_cannot_be_deleted(self):
        """陈旧锁**删不掉**时也必须能接管，不许出现"锁删不掉 → 媒体链永久起不来"。"""
        import os as _os

        from unittest import mock

        from v5 import config
        from v5.media import pipeline

        d, root = self._project()
        with d:
            lp = pipeline._lock_path(root, 1)
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text('{"pid": 999999, "at": 0}', encoding="utf-8")
            old = lp.stat().st_mtime - (config.MEDIA_LOCK_STALE_S + 60)
            _os.utime(lp, (old, old))
            with mock.patch.object(Path, "unlink", side_effect=_SandboxDenied("bulk")):
                self.assertTrue(pipeline._acquire_lock(lp, log=lambda *_: None),
                                "陈旧锁删不掉时放弃接管 = 项目永久卡死")
            self.assertIn(str(_os.getpid()), lp.read_text(encoding="utf-8"))


class TestSubmitOnlyScope(unittest.TestCase):
    """提交层的 `only`：非目标镜**绝不提交**，但盘中已有 clip 仍进 `done`。

    为什么单独测：reference 档走 `submit_all`（另一条提交路径），
    两条路径必须同构 —— 这正是 2026-09-13 "同一部片两种模式"漏改的教训。
    """

    def test_submit_all_skips_non_target_shots(self):
        from unittest import mock

        from v5.media import jobs as jobs_mod, providers, video

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ep_dir = root / "media" / "ep1"
            clip_dir = ep_dir / "clips"
            clip_dir.mkdir(parents=True)
            (clip_dir / "LN01.mp4").write_bytes(b"OLD")
            shots = [{"name": "LN01", "seconds": 5}, {"name": "LN02", "seconds": 5}]
            planned = [{"name": "LN01", "frame_plan": {}},
                       {"name": "LN02", "frame_plan": {}}]
            st = {n["name"]: {"url": "u"} for n in shots}
            with mock.patch.object(video.config, "VIDEO_MODE", "reference"), \
                    mock.patch.object(providers, "submit_video",
                                      return_value={"video_id": "v1"}) as sub, \
                    mock.patch.object(video.config, "VIDEO_SUBMIT_MIN_INTERVAL_S", 0):
                video.submit_all(root, shots, st, planned, ep=1, log=lambda *_: None,
                                 only=["LN02"])
            self.assertEqual(sub.call_count, 1, "只有目标镜可以提交")
            jobs = jobs_mod.load(ep_dir)
            self.assertFalse(jobs_mod.done(jobs, "LN01", clip_dir))
            self.assertIn("LN02", jobs)


class TestVideoFailureRetryPass(unittest.TestCase):
    """★ 拼接前「补渲一轮」（2026-09-14 实测 + 用户拍板"做 1"）。

    实测：`LN03`/`LN24` 首轮被供应商报 `generation failed`，而 `video.py` **对该状态零重试**
    （把它当终态）→ 24/26 → 缺镜时 `compose.concat` **按设计不拼接** → **整轮无成片**
    （还白搭 17 张静帧重画）；而补渲时**同样两镜一次就成功** ⇒ **偶发故障不是终态**。

    本组锁住三条：
      ① 首轮有镜未成 → **再提交一轮，且 `only` 精确等于未完成的那些镜**；
      ② 补渲成功才拼接（`status=ok` + `concat` 被调）；
      ③ **单镜重渲（调用方已给 `only`）不得触发补渲** —— 那是受限调用，不该扩面。
    """

    def _run(self, pass1_ok, pass2_ok, only=None, n=3):
        """返回 (result, submits, concat)。`submits` = 每次提交的 only（None=全量）。"""
        import contextlib
        from unittest import mock

        from v5.media import pipeline, scaffold, storyboard

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner.md").write_text(
                scaffold.build("t", [{"name": "村口", "shots": [{"seconds": 5}] * n}]),
                encoding="utf-8")
            shots = storyboard.parse(
                (root / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8"))
            names = [s["name"] for s in shots]
            _seed_passing_manifest(root)
            pool = {x: {"name": x, "path": "/x/%s.jpg" % x, "url": "u"} for x in names}
            (root / "media" / "ep1").mkdir(parents=True, exist_ok=True)

            submits: list = []
            polls: list = []

            def _submit(pr, ss, st_, pl, ep=1, log=print, tails=None, only=None):
                submits.append(list(only) if only else None)
                return {x: {"state": "submitted", "video_id": "v"} for x in (only or names)}

            def _poll(pr, jobs, ep=1, log=print):
                idx = len(polls)
                polls.append(idx)
                ok = pass1_ok if idx == 0 else pass2_ok
                return {x: "/x/%s.mp4" % x for x in ok}

            patches = [
                mock.patch.object(pipeline.cast, "ensure", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.style, "load", lambda *a_, **k_: ""),
                mock.patch.object(pipeline.style, "wrap", lambda *a_, **k_: ""),
                mock.patch.object(pipeline.style, "still_refs_enabled", lambda *a_, **k_: False),
                mock.patch.object(pipeline.assets, "bind", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.assets, "identity_lines", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.stills, "ensure", mock.MagicMock(return_value=pool)),
                mock.patch.object(pipeline.stills, "load", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.video, "submit_all", side_effect=_submit),
                mock.patch.object(pipeline.video, "poll_all", side_effect=_poll),
                mock.patch.object(pipeline.video, "submit_chain", side_effect=_submit),
                mock.patch.object(pipeline.compose, "concat", return_value=n),
                mock.patch.object(pipeline.config, "STILL_QC", False),
                mock.patch.object(pipeline.config, "CLIP_QC", False),
            ]
            with contextlib.ExitStack() as stk:
                ms = [stk.enter_context(p) for p in patches]
                with contextlib.redirect_stdout(io.StringIO()):
                    res = pipeline.run(root, ep=1, log=lambda *_: None, only=only)
            return res, submits, ms[11]

    def test_partial_first_pass_triggers_one_retry_with_only(self):
        """首轮 1/3 成功 → 补渲一轮，`only` 恰为未成的两镜；补渲成功后拼接。"""
        res, submits, concat = self._run(["LN01"], ["LN01", "LN02", "LN03"])
        self.assertEqual(len(submits), 2, "首轮未完成必须补渲一轮")
        self.assertEqual(submits[1], ["LN02", "LN03"], "补渲只针对未完成的镜")
        self.assertEqual(res.get("status"), "ok")
        self.assertEqual(concat.call_count, 1, "补渲凑齐后才拼接")

    def test_retry_still_incomplete_does_not_concat(self):
        """补渲后仍缺 → 如实 `incomplete`，**不拼接**（宁缺勿覆盖已成片）。"""
        res, submits, concat = self._run(["LN01"], ["LN01", "LN02"])
        self.assertEqual(len(submits), 2, "只补一轮，不无限重试")
        self.assertEqual(res.get("status"), "incomplete")
        self.assertEqual(res.get("missing"), ["LN03"])
        self.assertEqual(concat.call_count, 0)

    def test_full_first_pass_does_not_retry(self):
        """首轮全成 → 不补渲（零额外配额）。"""
        _res, submits, concat = self._run(["LN01", "LN02", "LN03"], [])
        self.assertEqual(len(submits), 1)
        self.assertEqual(concat.call_count, 1)

    def test_single_shot_rerender_does_not_expand(self):
        """★ 单镜重渲（调用方给了 `only`）**不得**触发补渲 —— 那是受限调用，不扩面。"""
        _res, submits, _concat = self._run(["LN01", "LN02", "LN03"], [], only=["LN02"])
        self.assertEqual(len(submits), 1, "重渲路径不补渲")
        self.assertEqual(submits[0], ["LN02"])


class TestStillRegenConvergence(unittest.TestCase):
    """静帧重生成必须跨进程收敛（2026-09-10 实测配额泄漏）。

    `max_regen` 只在单进程内计数，外部续跑器每重启一次就重置 → 静帧被无限重画。
    实测 4 轮驱动硬伤数 9→7→10→9 震荡，纯烧生图配额。
    补 `STILL_QC_MAX_REGEN` + 跨进程 tally（与 clip 侧对称）。
    """

    def _run(self, defects, max_regen=2, tally=None, n_shots=1):
        """跑 pipeline.run，mock 掉一切外部依赖。defects = 每轮 qc.review 返回的硬伤描述列表。

        返回 (result, ensure_mock)。
        """
        import contextlib
        from unittest import mock

        from v5.media import pipeline, scaffold, storyboard

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner.md").write_text(
                scaffold.build("t", [{"name": "店-夜",
                                      "shots": [{"seconds": 5}] * n_shots}]),
                encoding="utf-8")
            shots = storyboard.parse(
                (root / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8"))
            names = [s["name"] for s in shots]
            _seed_passing_manifest(root)   # 媒体门要求创作链已完成
            pool = {n: {"name": n, "path": "/x/%s.jpg" % n, "url": "u"} for n in names}
            (root / "media" / "ep1").mkdir(parents=True, exist_ok=True)
            if tally:
                pipeline._save_still_tally(root, 1, tally)

            def fake_review(path, shot=None, style_spec="", style_watch_spec="", **kw):
                if defects:
                    return {"issues": [{"level": "P0", "desc": defects}]}
                return {"issues": []}

            patches = [
                mock.patch.object(pipeline.cast, "ensure", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.style, "load", lambda *a_, **k_: ""),
                mock.patch.object(pipeline.style, "wrap", lambda *a_, **k_: ""),
                mock.patch.object(pipeline.style, "still_refs_enabled", lambda *a_, **k_: False),
                mock.patch.object(pipeline.assets, "bind", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.assets, "identity_lines", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.stills, "ensure",
                                  mock.MagicMock(return_value=pool)),
                mock.patch.object(pipeline.stills, "load", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.stills, "stills_dir", lambda *a_, **k_: root),
                mock.patch.object(pipeline.qc, "review", side_effect=fake_review),
                mock.patch.object(pipeline.qc, "review_shot_type",
                                  lambda *a_, **k_: {"ok": True}),
            ]
            with contextlib.ExitStack() as st:
                ms = [st.enter_context(p) for p in patches]
                with contextlib.redirect_stdout(io.StringIO()):
                    res = pipeline.run(root, ep=1, max_regen=max_regen, stills_only=True)
            return res, ms[6]

    def test_regen_capped_by_cross_process_tally(self):
        """累计重画达上限 → **不再重画**（否则跨重启无限烧配额）。

        注意基线：`stills.ensure` 也被**初次生成**调用一次，所以
        "只初次生成、零重画" 的期望值是 1（不是 0）。
        """
        from v5 import config

        res, ensure = self._run("画面出现可读字符",
                                tally={"LN01": config.STILL_QC_MAX_REGEN})
        self.assertEqual(ensure.call_count, 1,
                         "达上限时只应有初次生成，不该出现任何重画")
        self.assertEqual(res.get("still_residual"), ["LN01"])

    def test_regen_happens_below_tally_cap(self):
        """未达上限时照常重画（1 次初生成 + **至少 1 次**重画）。

        ⚠️ 2026-09-16 起不再断言 `max_regen` 次：`STILL_QC_STOP_REPEAT` 会在
        「同一镜连续两轮报**同一类**问题」时判停 —— 因为重画的强化约束只由
        **类别**决定，同类 ⇒ 提示词完全一致 ⇒ 第二次重画只是**换种子抽奖**
        （实测 felt-bach LN08/LN16：原样重画到撞上限，问题照旧，纯白做）。
        本用例的**回归意图**（"不能根本不重画"）由 `>= 2` 守住。
        """
        res, ensure = self._run("画面出现可读字符", max_regen=2)
        self.assertGreaterEqual(
            ensure.call_count, 2,
            "初次生成 1 次 + 至少 1 次重画（否则就是当初那个空转 bug）")
        self.assertIn("still_residual", res)

    def test_unclassified_defect_still_regenerates(self):
        """**回归**：硬伤不属于任何已知类别时也必须重画。

        原实现把 `stills.ensure` 放在 `if extra:` 里 → "缺少关键道具"这类
        硬伤不匹配任何类别 → 根本不重画 → bad 永远非空、循环空转到 max_regen，
        白烧一轮确认还报"仍有硬伤（达上限）"。

        ⚠️ 2026-09-16 起 `unclassified` 连续两轮会判停（那正是**纯抽奖**）——
        见 `test_same_kind_twice_stops_regenerating`。故这里只断言"至少重画一次"。
        """
        res, ensure = self._run("缺少关键道具：站牌未出现", max_regen=2)
        self.assertGreaterEqual(
            ensure.call_count, 2,
            "未归类硬伤也必须重画，否则循环空转")

    def test_still_tally_is_separate_from_clip_tally(self):
        """两个 tally 必须互不干扰（否则静帧的计数会误触 clip 的上限）。"""
        from v5.media import pipeline

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual(pipeline._load_still_tally(root, 1), {})
            pipeline._save_still_tally(root, 1, {"LN01": 2})
            pipeline._save_requeue_tally(root, 1, {"LN02": 3})
            self.assertEqual(pipeline._load_still_tally(root, 1), {"LN01": 2})
            self.assertEqual(pipeline._load_requeue_tally(root, 1), {"LN02": 3})
            # 不同集互不干扰
            self.assertEqual(pipeline._load_still_tally(root, 2), {})


class TestStillQcNarrowReview(unittest.TestCase):
    """QC 第 N(≥1) 轮**只复审上一轮被重画过的镜**，不复查图未变的镜。

    2026-09-12 实测依据（`LN12`）：原图从 14:52 起**一直没变过**，却被连审 3 次
    给出 clean / clean / **defect** → 第三次白画一张（tally=1）。该轮判的 10 镜里
    有 4 镜（LN12/LN20/LN27/LN36）属此类。

    旧行为每轮都 `_ex.map(_review_one, shots)`（全 40 镜）→ 3 轮 240 次调用，
    而真正该审的只有 69 镜（138 次），**约 42% 是复审无改动图**；更坏的是概率
    翻判会**凭空制造重画**，吃掉重画预算 → 更多镜撞上限、带硬伤放行。
    """

    def _run(self, bad_names, n_shots=3, max_regen=2):
        """跑 pipeline.run（假模型）。返回 (每个镜被复审的次数, ensure 调用次数)。"""
        import contextlib
        from unittest import mock

        from v5.media import pipeline, scaffold, storyboard

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner.md").write_text(
                scaffold.build("t", [{"name": "店-夜",
                                      "shots": [{"seconds": 5}] * n_shots}]),
                encoding="utf-8")
            shots = storyboard.parse(
                (root / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8"))
            names = [s["name"] for s in shots]
            _seed_passing_manifest(root)
            pool = {n: {"name": n, "path": "/x/%s.jpg" % n, "url": "u"} for n in names}
            (root / "media" / "ep1").mkdir(parents=True, exist_ok=True)

            seen: dict = {}

            def fake_review(path, shot=None, style_spec="", style_watch_spec="", **kw):
                who = str(path).rsplit("/", 1)[-1].replace(".jpg", "")
                seen[who] = seen.get(who, 0) + 1
                if who in bad_names:
                    return {"issues": [{"level": "P0", "desc": "画面出现可读字符"}]}
                return {"issues": []}

            patches = [
                mock.patch.object(pipeline.cast, "ensure", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.style, "load", lambda *a_, **k_: ""),
                mock.patch.object(pipeline.style, "wrap", lambda *a_, **k_: ""),
                mock.patch.object(pipeline.style, "still_refs_enabled", lambda *a_, **k_: False),
                mock.patch.object(pipeline.assets, "bind", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.assets, "identity_lines", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.stills, "ensure",
                                  mock.MagicMock(return_value=pool)),
                mock.patch.object(pipeline.stills, "load", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.stills, "stills_dir", lambda *a_, **k_: root),
                mock.patch.object(pipeline.qc, "review", side_effect=fake_review),
                mock.patch.object(pipeline.qc, "review_shot_type",
                                  lambda *a_, **k_: {"ok": True}),
            ]
            with contextlib.ExitStack() as st:
                ms = [st.enter_context(p) for p in patches]
                with contextlib.redirect_stdout(io.StringIO()):
                    res = pipeline.run(root, ep=1, max_regen=max_regen, stills_only=True)
            return seen, ms[6].call_count, res

    def test_second_round_reviews_only_regenerated(self):
        """3 镜里只有 LN01 被判硬伤 → LN02/LN03 只该被审 **1 次**（第 0 轮）。

        旧的"每轮复审全部"行为下，LN02/LN03 会被审 3 次（3 轮 × 全量）。
        """
        seen, _ensure, _res = self._run(bad_names={"LN01"}, n_shots=3, max_regen=2)
        # 2026-09-16 起负面判定会**复采一次**（`config.QC_CONFIRM_NEGATIVE`），
        # 且「同类连续两轮」会提前判停（`config.STILL_QC_STOP_REPEAT`）
        # → 不再钉死具体次数（那会随那两条机制调整而脆断），只断言核心意图。
        self.assertGreaterEqual(seen.get("LN01", 0), 2,
                                "被重画的镜每轮都要复审（收敛判定）")
        self.assertEqual(seen.get("LN02"), 1,
                         "图未变的镜不该被复审（概率翻判会凭空制造重画）")
        self.assertEqual(seen.get("LN03"), 1)

    def test_first_round_still_reviews_everything(self):
        """第 0 轮必须审全部镜——收窄只针对第 ≥1 轮。"""
        seen, _ensure, _res = self._run(bad_names={"LN01"}, n_shots=3, max_regen=2)
        self.assertEqual(sorted(seen), ["LN01", "LN02", "LN03"])

    def test_narrowing_saves_regens(self):
        """收窄后重画次数 = 初生成 1 次 + 每轮最多 1 张（只有 LN01 被判）。

        旧的"全量复审"会顺带把 LN02/LN03 的概率翻判也吃进重画。
        这里至少保证：重画次数与"被判硬伤的镜数"成正比，不被镜总数放大。
        （2026-09-16 起 `STILL_QC_STOP_REPEAT` 会提前判停 → 用 `>= 2` 断言。）
        """
        from v5 import config

        assert config.STILL_QC_MAX_REGEN >= 3, "本用例假设默认上限 ≥3"
        _seen, ensure_calls, res = self._run(bad_names={"LN01"}, n_shots=3, max_regen=2)
        self.assertGreaterEqual(ensure_calls, 2,
                                "1 次初生成 + 每轮只重画 1 张（LN01）")
        self.assertIn("still_residual", res)

    def test_same_kind_twice_stops_regenerating(self):
        """**新判据（2026-09-16）**：同一镜连续两轮报**同一类**问题 → 不再重画。

        为什么：重画的强化约束**只由类别决定** → 同类 ⇒ 提示词完全一致 ⇒
        再画一次只是**换种子抽奖**，不是修复。实测 felt-bach：LN08「两个相似老头」/
        LN16「缺风箱工」连续两轮落 `unclassified`（强化约束为空），
        原样重画到撞上限、问题照旧 —— 那两轮纯白做。

        断言：初生成 1 次 + **1 次**重画（第二轮判停），且镜进 residual、
        日志给出可解释的理由。
        """
        import contextlib
        from unittest import mock

        from v5.media import pipeline

        _seen, ensure_calls, res = self._run(bad_names={"LN01"}, n_shots=1,
                                             max_regen=2)
        self.assertEqual(ensure_calls, 2,
                         "初生成 1 次 + 同类第二次即判停（不再重画）")
        self.assertIn("still_residual", res)

    def test_kind_change_still_regenerates(self):
        """类别**变了**仍允许重画（说明上一轮强化起了作用、只是又冒出别的问题）。"""
        import contextlib
        from unittest import mock

        from v5.media import pipeline, scaffold, storyboard

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner.md").write_text(
                scaffold.build("t", [{"name": "店-夜", "shots": [{"seconds": 5}]}]),
                encoding="utf-8")
            _seed_passing_manifest(root)
            pool = {"LN01": {"name": "LN01", "path": "/x/LN01.jpg", "url": "u"}}
            (root / "media" / "ep1").mkdir(parents=True, exist_ok=True)
            seq = iter(["画面出现可读字符", "背景漂移到窗外"])   # text → drift

            def fake_review(path, shot=None, style_spec="", style_watch_spec="", **kw):
                return {"issues": [{"level": "P0", "desc": next(seq, "背景漂移到窗外")}]}

            patches = [
                mock.patch.object(pipeline.cast, "ensure", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.style, "load", lambda *a_, **k_: ""),
                mock.patch.object(pipeline.style, "wrap", lambda *a_, **k_: ""),
                mock.patch.object(pipeline.style, "still_refs_enabled",
                                  lambda *a_, **k_: False),
                mock.patch.object(pipeline.assets, "bind", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.assets, "identity_lines", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.stills, "ensure",
                                  mock.MagicMock(return_value=pool)),
                mock.patch.object(pipeline.stills, "load", lambda *a_, **k_: {}),
                mock.patch.object(pipeline.stills, "stills_dir", lambda *a_, **k_: root),
                mock.patch.object(pipeline.qc, "review", side_effect=fake_review),
                mock.patch.object(pipeline.qc, "review_shot_type",
                                  lambda *a_, **k_: {"ok": True}),
            ]
            with contextlib.ExitStack() as st:
                ms = [st.enter_context(p) for p in patches]
                with contextlib.redirect_stdout(io.StringIO()):
                    pipeline.run(root, ep=1, max_regen=2, stills_only=True)
            self.assertGreaterEqual(ms[6].call_count, 3,
                                    "类别变化（text→drift）时必须继续重画")


class TestModelUpgradeAndQcTemperature(unittest.TestCase):
    """主模型升级 agnes-3.0-flash + 评判类温度归零（2026-09-10）。

    官方文档：3.0-flash 是**纯文本**模型（输入支持文本 + 图像 URL，输出只有文本），
    所以只有 chat 升级，image/video 仍留 2.5 系（官方无 3.0 版）。
    QC 要读图 —— "输入支持图像 URL" 是它能接替 2.5 的前提。
    """

    def test_chat_model_is_3_0_flash(self):
        from v5 import config

        self.assertEqual(config.MODELS["chat"], "agnes-3.0-flash")

    def test_image_and_video_stay_on_2_5(self):
        """不能顺手把图/视频也升到 3.0——那是纯文本模型，没有 3.0 版图/视频。"""
        from v5 import config

        self.assertIn("2.5", config.MODELS["image"])
        self.assertIn("2.5", config.MODELS["video"])

    def test_chat_for_passes_temperature_through(self):
        """温度要能透传：创作角色 0.1，评判类 0。"""
        from unittest import mock

        from v5 import llm

        with mock.patch.object(llm, "ChatOpenAI") as m:
            llm.chat_for("agnes", 512, temperature=0)
            self.assertEqual(m.call_args.kwargs["temperature"], 0)
            llm.chat_for("agnes", 512)
            self.assertEqual(m.call_args.kwargs["temperature"], 0.1)

    def test_qc_calls_use_temperature_zero(self):
        """QC 两个入口都必须传 temperature=0。

        判据是概率性的：温度不为 0 时同一张图连审会给出不同结论
        （实测 LN08 判 1/1/2、LN10 判 0/1/0）→ 复核环路不收敛、白烧配额。
        """
        from unittest import mock

        from v5.media import qc

        seen: list[float] = []

        class _R:
            content = '{"issues": [], "ok": true}'

        class _FakeChat:
            def invoke(self, _msgs):
                return _R()

        def fake_chat_for(provider="", max_tokens=8192, temperature=0.1):
            seen.append(temperature)
            return _FakeChat()

        with mock.patch.object(qc, "chat_for", side_effect=fake_chat_for), \
                mock.patch.object(qc, "_data_uri", return_value="data:image/jpeg;base64,AA"):
            qc.review("/x/a.jpg")
            qc.review_shot_type("/x/a.jpg", {"shot_type": "全景"})

        self.assertEqual(seen, [0, 0], "QC 的两次调用都必须是 temperature=0")


class TestQcStyleSpecAndDuplication(unittest.TestCase):
    """静帧 QC 的两个判据缺口（2026-09-15 village-tractor 实测）。

    缺口①：P0 列表里没有「主体被复制成多份」—— LN17 **9 张脸**同框（老周×4 +
      小林×3）、LN21 多台拖拉机 + 多个老周、LN23 人物重影，全都落在缝里
      （第 3 条只管"不该出现的**陌生人**脸"，而复制出来的是**主角本人**）。
    缺口②：QC 明文「风格、氛围、构图偏好一律不判」—— 反质量包（牛来）的核心审美
      **就是**"必须粗糙、精致即失败"，于是最该守的一项**没有判据**：LN24 画成
      **写实照片级** + 三个完全陌生的演员，却全部放行、直接进成片。
    修法：pack 声明 `style-is-criterion` 后 QC 追加风格轴；**默认关闭**。
    """

    def test_duplication_is_hard(self):
        from v5.media import qc

        self.assertTrue(qc.is_hard_issue(
            {"level": "P0", "desc": "画面中出现两个一模一样的老周，人物重影严重"}))
        self.assertTrue(qc.is_hard_issue(
            {"level": "P0", "desc": "同一台拖拉机在画面里重复出现三次，像拼贴"}))

    def test_legitimate_multiplicity_not_hard(self):
        """★ 防**误判成功**：本来就该有多个同类物件不算复制。"""
        from v5.media import qc

        self.assertFalse(qc.is_hard_issue(
            {"level": "P0", "desc": "坡道上散落着一路化肥袋，袋面为纯色块"}))
        self.assertFalse(qc.is_hard_issue(
            {"level": "P1", "desc": "低分辨率贴图重复平铺"}),
            "P1 不该判硬伤（贴图重复是本片刻意风格）")

    def test_negated_duplication_not_hard(self):
        from v5.media import qc

        self.assertFalse(qc.is_hard_issue(
            {"level": "P0", "desc": "未见重复出现的人物，人物数量符合要求"}))

    def test_style_collapse_is_hard(self):
        from v5.media import qc

        self.assertTrue(qc.is_hard_issue(
            {"level": "P0", "desc": "画风违背类型包审美：画面为写实照片质感"}))

    def test_review_appends_style_spec_only_when_given(self):
        """风格轴**只在 pack 声明后**追加 —— 空 spec 时 QC 行为一字不变。"""
        from unittest import mock

        from v5.media import qc

        seen: list[str] = []

        class _R:
            content = '{"issues": []}'

        class _FakeChat:
            def invoke(self, msgs):
                seen.append(msgs[0].content[0]["text"])
                return _R()

        with mock.patch.object(qc, "chat_for", return_value=_FakeChat()), \
                mock.patch.object(qc, "_data_uri",
                                  return_value="data:image/jpeg;base64,AA"):
            qc.review("/x/a.jpg")
            qc.review("/x/a.jpg", style_spec="primitive folk CGI，极低面数")

        # 用风格轴小节的**专属标题**判定（基础提示词的"边界例外"注里也含"硬判据"，
        # 故不能用这个词做判据）
        self.assertNotIn("=== 本片审美是", seen[0])
        self.assertIn("=== 本片审美是", seen[1])
        self.assertIn("primitive folk CGI，极低面数", seen[1])
        self.assertIn("画风违背类型包审美", seen[1])

    def test_style_is_criterion_is_per_pack(self):
        """开关是 **pack 级**：牛来包 True、写实包 False（写实画面不许被判死）。"""
        import json
        import tempfile
        from pathlib import Path

        from v5.media import style

        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        with d:
            (root / "brief.json").write_text(
                json.dumps({"pack": "niulai-movie-style"}, ensure_ascii=False),
                encoding="utf-8")
            self.assertTrue(style.style_is_criterion(root))
            self.assertTrue(style.style_criterion_spec(root))
            (root / "brief.json").write_text(
                json.dumps({"pack": "shortdrama"}, ensure_ascii=False),
                encoding="utf-8")
            self.assertFalse(style.style_is_criterion(root))
            self.assertEqual(style.style_criterion_spec(root), "")


class TestStillRefRule(unittest.TestCase):
    """静帧路径也必须声明「以参考图为准」（2026-09-15 实测缺口）。

    事故：`prompt.REF_USAGE_*` **只注入在视频提示词**里（`build_video_prompt`），
    **静帧路径一个字都没有** → 模型不知道那张图是什么、该不该以它为准，
    于是**只按文字走**：实测"面部棱角清楚、线条硬朗"把脸改老改硬、
    分镜写的 `头戴@草帽` 直接给加上帽子 → 用户反馈「为什么样子都变了，
    不是和我给的照片完全一样的」。
    修法：静帧提示词**最前面**补 `STILL_REF_RULE`（复用 `REF_USAGE_ZH` + 写死冲突优先级）。
    """

    def test_declared_when_refs_bound(self):
        from v5.media import prompt, stills

        out = stills._with_ref_rule("画面内容", ["data:image/jpeg;base64,AA"])
        self.assertTrue(out.startswith(prompt.STILL_REF_RULE), out[:80])
        self.assertIn("以 <Picture 1> 为准", out)
        self.assertIn("画面内容", out, "原提示词不能被吃掉")

    def test_unchanged_without_refs(self):
        """没绑参考图 → **一字不改**（纯加法，老项目零影响）。"""
        from v5.media import stills

        self.assertEqual(stills._with_ref_rule("画面内容", []), "画面内容")
        self.assertEqual(stills._with_ref_rule("画面内容", None), "画面内容")

    def test_both_call_sites_use_it(self):
        """两个 `gen_image` 调用点都要走它 —— 漏一处，那一路仍旧跑偏。"""
        import inspect

        from v5.media import stills

        src = inspect.getsource(stills)
        self.assertEqual(src.count("_with_ref_rule("), 3,
                         "应为 1 处定义 + 2 处调用（主静帧 / 尾缀预生成）")


class TestModelProfileIsolation(unittest.TestCase):
    """模型怪癖规则必须隔离在 per-model profile 后面（2026-09-10 技术债）。

    `prompt.py` 曾从 17KB 涨到 48KB：23 张规则表、37 处「实测」注释，
    几乎全在补偿某一个具体模型的怪癖。换模型时它们会变成死代码，
    甚至变成**有害代码**——例如"按语言主体清中文"，对能读中文的模型就是破坏。

    本组测试锁两件事：
      ① 默认档（agnes 2.5）行为与历史完全一致 —— 不许回退；
      ② 传一个"乖模型"档案时，所有补偿都能被真正关掉。
    """

    def test_resolve_by_exact_name(self):
        from v5.media import model_profile as mp

        self.assertIs(mp.resolve("agnes-video-2.5-flash", kind="video"), mp.AGNES_VIDEO_25)
        self.assertIs(mp.resolve("agnes-image-2.5-flash", kind="image"), mp.AGNES_IMAGE_25)

    def test_resolve_prefix_fallback_for_future_versions(self):
        """版本号变化时按前缀兜底，不至于掉到默认档。"""
        from v5.media import model_profile as mp

        self.assertIs(mp.resolve("agnes-image-9.9-future", kind="image"),
                      mp.AGNES_IMAGE_25)
        self.assertIs(mp.resolve("agnes-video-9.9-future", kind="video"),
                      mp.AGNES_VIDEO_25)

    def test_unknown_model_falls_back_by_kind_not_capable(self):
        """未知模型**不能**掉到 CAPABLE——零补偿会让烧字/分屏直接回归。"""
        from v5.media import model_profile as mp

        self.assertIs(mp.resolve("totally-unknown", kind="video"), mp.AGNES_VIDEO_25)
        self.assertIs(mp.resolve("totally-unknown", kind="image"), mp.AGNES_IMAGE_25)
        self.assertIs(mp.resolve("", kind="image"), mp.AGNES_IMAGE_25)

    def test_capable_profile_disables_every_compensation(self):
        """CAPABLE 下 sanitize_text 必须退化成恒等变换（零补偿）。"""
        from v5.media import model_profile as mp, prompt

        raw = ("招牌挂在门上。画面里有文字水印。随后他慢慢走开。"
               "DO NOT clear facial features. Asset reuse: heavy — same mask model.")
        out = prompt.sanitize_text(raw, profile=mp.CAPABLE)
        self.assertIn("招牌", out, "burns_text=False 时不该改写招牌名词")
        self.assertIn("文字", out, "不该删文字概念分句")
        self.assertIn("随后", out, "splits_frames=False 时不该做时间改写")
        self.assertIn("DO NOT", out, "negative_induces=False 时不该删禁令")
        self.assertIn("same mask model", out, "leaks_asset_bookkeeping=False 时不该清")
        self.assertNotIn(prompt.NOSPLIT, out, "不该加反分屏声明")

    def test_default_profile_keeps_historical_behavior(self):
        """回归：不传 profile 时必须与历史行为逐条一致。"""
        from v5.media import prompt

        out = prompt.sanitize_text("招牌挂在门上。随后他慢慢走开。")
        self.assertNotIn("招牌", out, "默认档必须仍改写招牌名词")
        self.assertNotIn("随后", out, "默认档必须仍做时间改写")
        self.assertIn(prompt.NOSPLIT, out, "默认档必须仍加反分屏声明")

    def test_capable_profile_keeps_chinese_intact(self):
        """能读中文的模型不该被清中文——"补丁变有害"最典型的一处。"""
        from v5.media import model_profile as mp, prompt

        t = "Rebuild as primitive folk CGI. Lock: single driver in bus cabin, 老周 seated."
        self.assertEqual(prompt.strip_cjk(t, profile=mp.CAPABLE), t,
                         "reads_chinese=True 时 strip_cjk 必须恒等")
        self.assertNotIn("老周", prompt.strip_cjk(t, profile=mp.AGNES_IMAGE_25),
                         "2.5 档（reads_chinese=False）必须清掉中文字形")

    def test_build_video_prompt_honours_injected_profile(self):
        """组装器只认 profile，不认模型名 —— 新模型零改动接入。"""
        from v5.media import model_profile as mp, prompt

        shot = {"name": "LN01", "visual": "招牌下站着一个人。Rebuild as low poly.",
                "shot_type": "中景", "angle": "平视", "camera": "固定",
                "visual_style": "", "scene": "", "tail": "",
                "dialogue": "（无声）", "sfx": "", "text_shot": "", "join_note": ""}
        plain = prompt.build_video_prompt(shot, profile=mp.CAPABLE)
        self.assertNotIn(prompt.NOSPLIT_VIDEO, plain, "乖模型不需要反分屏声明")
        strict = prompt.build_video_prompt(shot, profile=mp.AGNES_VIDEO_25)
        self.assertIn(prompt.NOSPLIT_VIDEO, strict, "2.5 视频档需要反分屏声明")

    def test_new_model_needs_no_assembler_edit(self):
        """登记一个档案即可接入新模型 —— 组装器一行都不用改。"""
        from v5.media import model_profile as mp, prompt

        prof = mp.register(mp.ModelProfile(key="my-future-model",
                                           reads_chinese=True))
        self.addCleanup(lambda: mp._PROFILES.pop("my-future-model", None))
        self.assertIs(mp.resolve("my-future-model"), prof)

        shot = {"name": "LN01", "visual": "招牌下站着一个纸扎匠。",
                "shot_type": "中景", "angle": "平视", "camera": "固定",
                "visual_style": "", "scene": "", "tail": "",
                "dialogue": "（无声）", "sfx": "", "text_shot": "", "join_note": ""}
        out = prompt.build_still_prompt(shot, profile=prof)
        self.assertIn("招牌", out, "新档案零补偿 → 原文应保留")

    def test_describe_lists_quirks(self):
        """排查用的清单必须能一眼看出哪些模型挂了哪些怪癖。"""
        from v5.media import model_profile as mp

        d = mp.describe()
        self.assertIn("agnes-image-2.5-flash", d)
        self.assertIn("burns_text", d)
        self.assertIn("clean", d, "CAPABLE 应显示为 clean")


class TestTailFramePregen(unittest.TestCase):
    """落幅帧预生成：把"连续镜必须串行"换成"上一镜落幅图"（2026-09-10）。

    现状瓶颈：`submit_chain` 用上一镜**渲出来的真实尾帧**当下一镜 first_frame
    → 连续镜必须等前一镜渲完 → 整条链串行。bootleg99-full 32 镜里 25 镜落在
    链上，86 分钟就是这么来的；maskparade 全 18 镜都是 cut，不受影响。

    解法：静帧阶段预生成"落幅帧图"，视频阶段平铺提交
    （first=上一镜落幅图，last=本镜静帧），串行依赖消失。
    """

    HEADINGS = ("## 第1幕｜车内-夜｜S1 / 5s",
                "## 第1幕｜车内-夜｜S2 / 5s",   # 同幕同场 → continuous
                "## 第2幕｜车外-晨｜S3 / 5s")   # 换幕 → cut


    def test_flat_submit_truth_table(self):
        """平铺判据真值表（keyframe 档）—— 判据现收在 `VideoPlan.can_submit_flat`。

        2026-09-13 收敛：原 `pipeline._parallel_ok` 已并入 `VideoPlan.of()` ——
        "能不能平铺"本质上是**模式决策**的一部分（reference 下各镜无依赖、恒可平铺；
        keyframe 下取决于承接依赖是否解除），与"用什么图 / 要不要抽尾帧"是同一件事的几面。
        分散在两处判断正是当天漏改的根因，故收敛到 `media/video_plan.py` 一处。
        """
        from v5.media.video_plan import VideoPlan

        def flat(**kw):
            return VideoPlan.of("keyframe", **kw).can_submit_flat

        # 全 cut（n_need=0）：开 TAIL_PREGEN 即可平铺（旧判据在此恒为 False）
        self.assertTrue(flat(still_chain=True, tail_pregen=True, n_tails=0, n_need=0))
        # 有连续镜且落幅齐 → 平铺
        self.assertTrue(flat(still_chain=True, tail_pregen=True, n_tails=5, n_need=5))
        # 有连续镜但落幅不全 → 退回串行（安全）
        self.assertFalse(flat(still_chain=True, tail_pregen=True, n_tails=3, n_need=5))
        # 没开落幅预生成 → 串行
        self.assertFalse(flat(still_chain=True, tail_pregen=False, n_tails=0, n_need=0))
        # 显式关链式 → 永远平铺（接受连续镜重复）
        self.assertTrue(flat(still_chain=False, tail_pregen=False, n_tails=0, n_need=3))

    def _planned(self):
        from v5.media import relations

        shots = [{"name": "LN%02d" % (i + 1), "heading": h}
                 for i, h in enumerate(self.HEADINGS)]
        return shots, relations.plan_frames(shots)

    def test_relations_setup_is_as_expected(self):
        """先锁住前提：LN02 必须是 continuous，LN03 必须是 cut。"""
        _shots, planned = self._planned()
        rel = [p["frame_plan"]["relation"] for p in planned]
        self.assertEqual(rel, ["cut", "continuous", "cut"])

    def test_tail_needed_only_for_predecessors(self):
        """只有"被下一镜承接"的镜需要落幅图；末镜与 cut 后继的镜都不需要。"""
        from v5.media import stills

        _shots, planned = self._planned()
        self.assertEqual(stills.tail_needed(planned), ["LN01"])

    def test_tail_needed_empty_when_all_cut(self):
        """全 cut 的片（如 maskparade）不该生成任何落幅图——白烧配额。"""
        from v5.media import stills

        planned = [{"name": "LN%02d" % i,
                    "frame_plan": {"relation": "cut", "use_prev_last": False}}
                   for i in range(1, 4)]
        self.assertEqual(stills.tail_needed(planned), [])

    def test_tail_content_strips_wrapper(self):
        """落幅裸内容必须剥掉运镜套壳——尾帧要画面，不要运镜指令。

        注意与 `tail_line` 取向相反：那里保留原文（否则会二次套壳成
        「镜头最终停在镜头缓缓推向…」），这里剥干净。
        """
        from v5.media import prompt

        self.assertEqual(prompt.tail_content({"tail": "落幅停在老周的手上"}), "老周的手上")
        self.assertEqual(prompt.tail_content({"tail": "镜头最终停在后视镜"}), "后视镜")
        self.assertEqual(
            prompt.tail_content({"tail": "镜头缓缓推向监控屏幕，画面定格在那张脸上"}),
            "监控屏幕，画面定格在那张脸上")

    def test_build_tail_prompt_is_framed_on_tail_not_opening(self):
        """尾帧提示词必须**以落幅为主体**——与静帧路径刻意去掉落幅正相反。"""
        from v5.media import prompt

        shot = {"name": "LN01", "visual": "开场：老周坐在驾驶座上握着方向盘。",
                "tail": "落幅停在老周的手离开方向盘", "shot_type": "中景",
                "angle": "平视", "camera": "固定", "visual_style": "",
                "scene": "", "dialogue": "（无声）", "sfx": "",
                "text_shot": "", "join_note": ""}
        p = prompt.build_tail_prompt(shot)
        self.assertIn("手离开方向盘", p, "尾帧应以落幅为主体")
        self.assertNotIn("握着方向盘", p, "尾帧不该以开场构图为主体")
        self.assertIn(prompt.NOSPLIT, p, "尾帧同样要压分屏")

    def test_submit_all_uses_prev_tail_as_first_frame(self):
        """有落幅图时：连续镜 first=上一镜落幅图、last=本镜静帧。"""
        import contextlib
        from unittest import mock

        from v5.media import video

        shots, planned = self._planned()
        st = {n: {"url": "still://%s" % n} for n in ("LN01", "LN02", "LN03")}
        tails = {"LN01": {"url": "tail://LN01"}}
        seen: list[dict] = []

        def fake_submit(prompt, *, first_frame=None, last_frame=None, seconds=8, **kw):
            seen.append({"first": first_frame, "last": last_frame})
            return {"video_id": "task_%d" % len(seen)}

        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(video.providers, "submit_video",
                                   side_effect=fake_submit), \
                    mock.patch.object(video.config, "VIDEO_SUBMIT_MIN_INTERVAL_S", 0), \
                    mock.patch.object(video.config, "VIDEO_MODE", "keyframe"), \
                    contextlib.redirect_stdout(io.StringIO()):
                video.submit_all(Path(d), shots, st, planned, ep=1,
                                 log=lambda *_: None, tails=tails)

        # LN01 是 cut → 用自己的静帧、无 last
        self.assertEqual(seen[0]["first"], "still://LN01")
        self.assertIsNone(seen[0]["last"])
        # LN02 是 continuous → 承 LN01 的**落幅图**，收在自己的静帧
        self.assertEqual(seen[1]["first"], "tail://LN01")
        self.assertEqual(seen[1]["last"], "still://LN02")
        # LN03 是 cut → 回到自己的静帧
        self.assertEqual(seen[2]["first"], "still://LN03")
        self.assertIsNone(seen[2]["last"])

    def test_submit_all_without_tails_degrades_to_own_still(self):
        """没给落幅图时必须退回 own-still（不能因为缺图就不渲）。"""
        import contextlib
        from unittest import mock

        from v5.media import video

        shots, planned = self._planned()
        st = {n: {"url": "still://%s" % n} for n in ("LN01", "LN02", "LN03")}
        seen: list[dict] = []

        def fake_submit(prompt, *, first_frame=None, last_frame=None, seconds=8, **kw):
            seen.append({"first": first_frame, "last": last_frame})
            return {"video_id": "task_%d" % len(seen)}

        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(video.providers, "submit_video",
                                   side_effect=fake_submit), \
                    mock.patch.object(video.config, "VIDEO_SUBMIT_MIN_INTERVAL_S", 0), \
                    mock.patch.object(video.config, "VIDEO_MODE", "keyframe"), \
                    contextlib.redirect_stdout(io.StringIO()):
                video.submit_all(Path(d), shots, st, planned, ep=1,
                                 log=lambda *_: None)

        self.assertEqual([x["first"] for x in seen],
                         ["still://LN01", "still://LN02", "still://LN03"])
        self.assertTrue(all(x["last"] is None for x in seen))

    def test_submit_all_modes_stay_isomorphic(self):
        """两条提交路径（submit_all / submit_chain）必须用**同一个** VIDEO_MODE。

        2026-09-13 事故：`submit_all` 没传 mode，而 `providers.submit_video` 的
        默认值是 keyframe —— 一旦开了 TAIL_PREGEN 走到这条平铺路径，就会渲出与
        主路径不同的产物（keyframe 继承静帧画幅、reference 按 aspect_ratio 输出
        → 同一部片画幅漂移）。
        """
        import contextlib

        from v5.media import video

        shots = [{"name": "LN01", "visual": "a", "seconds": 6},
                 {"name": "LN02", "visual": "b", "seconds": 6}]
        planned = [{"frame_plan": {"relation": "cut"}},
                   {"frame_plan": {"relation": "continuous", "use_prev_last": True}}]
        st = {n: {"url": "still://%s" % n} for n in ("LN01", "LN02")}
        tails = {"LN01": {"url": "tail://LN01"}}

        for mode in ("reference", "keyframe"):
            seen: list[dict] = []

            def fake_submit(prompt, **kw):
                seen.append(kw)
                return {"video_id": "task_%d" % len(seen)}

            with tempfile.TemporaryDirectory() as d:
                with mock.patch.object(config, "VIDEO_MODE", mode), \
                        mock.patch.object(config, "VIDEO_SUBMIT_MIN_INTERVAL_S", 0), \
                        mock.patch.object(video.providers, "submit_video",
                                          side_effect=fake_submit), \
                        contextlib.redirect_stdout(io.StringIO()):
                    jobs = video.submit_all(Path(d), shots, st, planned, ep=1,
                                            log=lambda *_: None, tails=tails)
            self.assertEqual([k.get("mode") for k in seen], [mode, mode],
                             "平铺路径的 mode 必须跟随 VIDEO_MODE")
            if mode == "reference":
                self.assertEqual(seen[1]["images"], ["still://LN02"],
                                 "reference 下静帧进 images")
                self.assertIsNone(seen[1].get("first_frame"),
                                  "reference 下不得传 first_frame（与 API 互斥）")
                self.assertEqual(jobs["LN02"]["first_frame_kind"], "reference_still")
            else:
                self.assertEqual(seen[1]["first_frame"], "tail://LN01",
                                 "keyframe 下连续镜仍承上一镜落幅图")
                self.assertEqual(jobs["LN02"]["first_frame_kind"], "prev_tail_pregen")

    def test_tail_extraction_skipped_in_reference(self):
        """reference 下不抽尾帧 —— 抽了也没人消费，纯浪费一次视频解码。"""
        from v5.media import video
        from v5.media.video_plan import VideoPlan

        calls: list = []
        orig = video.extract_last_frame
        video.extract_last_frame = lambda p, **kw: (calls.append(p), "TAIL")[1]
        try:
            self.assertIsNone(
                video._tail_if_needed(Path("a.mp4"), VideoPlan.of("reference")))
            self.assertEqual(calls, [], "reference 下不该调用 extract_last_frame")
            self.assertEqual(
                video._tail_if_needed(Path("a.mp4"), VideoPlan.of("keyframe")), "TAIL")
            self.assertEqual(len(calls), 1, "keyframe 下必须抽（承接要用）")
        finally:
            video.extract_last_frame = orig

    def test_reference_forces_flat_submit(self):
        """reference 模式必须走平铺提交 —— 各镜之间没有可承接的依赖。

        2026-09-13：这条判据原在 `pipeline._parallel_ok` 里（它只问"链式依赖解除了吗"），
        而**这个前提在 reference 下不成立** —— reference 不允许 first_frame/last_frame。
        漏掉的代价：视频阶段串行白等（≈3 分/镜；40 镜 ≈100 分 → 平铺 ≈25 分）。
        这是 reference 切换的第三处漏改，也已收敛进 `VideoPlan`。
        """
        from v5.media.video_plan import VideoPlan

        vp = VideoPlan.of("reference")
        self.assertTrue(vp.can_submit_flat, "reference 下各镜独立，必须允许平铺")
        self.assertTrue(vp.use_images and not vp.use_keyframes, "静帧进 images")
        self.assertFalse(vp.needs_tail_extract, "reference 不承接 → 抽尾帧是白做功")
        self.assertTrue(vp.announce_picture, "reference 要求写 <Picture 1> 声明")

        # keyframe 回退档：判据不变 —— 有链式依赖时仍走串行
        kp = VideoPlan.of("keyframe", still_chain=True, tail_pregen=False)
        self.assertFalse(kp.can_submit_flat, "keyframe 且未开落幅预生成 → 仍串行")
        self.assertTrue(kp.use_keyframes and not kp.use_images)
        self.assertTrue(kp.needs_tail_extract, "keyframe 要抽尾帧给下一镜承接")

    def test_tail_pregen_default_is_off(self):
        """默认必须关闭——它是"速度换承接质量"的取舍，得由人按项目开。"""
        import os
        from v5 import config

        self.assertNotIn("SHORTDRAMA_TAIL_PREGEN", os.environ)
        self.assertFalse(config.TAIL_PREGEN)


class TestVideoQueueFullRetry(unittest.TestCase):
    """队列满（503 video_queue_full）必须退避重试，不能直接判死（2026-09-10）。

    批量重拍时 Agnes 反复返回 503；原实现当成永久失败 → 该镜直接缺席成片。
    """

    def test_retry_then_succeed(self):
        import contextlib
        import json as _json
        from unittest import mock

        from v5.media import jobs as jobs_mod, providers, video

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "media" / "ep1").mkdir(parents=True)
            (root / "media" / "ep1" / "clips").mkdir()
            shots = [{"name": "LN01", "seconds": 5, "visual": "v",
                      "shot_type": "全景", "angle": "平视", "camera": "固定"}]
            planned = [{"name": "LN01", "frame_plan": {"relation": "cut",
                                                       "use_prev_last": False}}]
            stills = {"LN01": {"url": "http://x/a.png"}}
            seq = [providers.QueueFullError("full"),
                   providers.QueueFullError("full"),
                   {"video_id": "vid1"}]

            def fake_submit(*a_, **k_):
                item = seq.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

            with contextlib.ExitStack() as st:
                st.enter_context(mock.patch.object(providers, "submit_video",
                                                   side_effect=fake_submit))
                st.enter_context(mock.patch.object(video, "_wait_one",
                                                   return_value=str(root / "c.mp4")))
                st.enter_context(mock.patch.object(video, "extract_last_frame",
                                                   return_value=None))
                st.enter_context(mock.patch.object(video.time, "sleep",
                                                   return_value=None))
                with contextlib.redirect_stdout(io.StringIO()):
                    done = video.submit_chain(root, shots, stills, planned, ep=1)
            self.assertIn("LN01", done, "队列满退避后应重试成功，而不是判死")
            self.assertEqual(seq, [])

    def test_submit_all_retries_on_queue_full(self):
        """并铺式路径（submit_all）也必须退避重试。

        2026-09-10 缺口：submit_chain 加了退避，但 submit_all 只特判 429，
        队列满走通用异常 → 立即 mark failed → 该镜缺席（LN09/LN10 实测）。
        """
        import contextlib
        import json as _json
        from unittest import mock

        from v5.media import jobs as jobs_mod, providers, video

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            out = root / "media" / "ep1"
            (out / "clips").mkdir(parents=True)
            shots = [{"name": "LN01", "seconds": 5, "visual": "v",
                      "shot_type": "全景", "angle": "平视", "camera": "固定"}]
            planned = [{"name": "LN01", "frame_plan": {"relation": "cut",
                                                       "use_prev_last": False}}]
            stills = {"LN01": {"url": "http://x/a.png"}}
            seq = [providers.QueueFullError("full"),
                   providers.QueueFullError("full"),
                   {"video_id": "vid1"}]

            def fake_submit(*a_, **k_):
                item = seq.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

            with contextlib.ExitStack() as st:
                st.enter_context(mock.patch.object(providers, "submit_video",
                                                   side_effect=fake_submit))
                st.enter_context(mock.patch.object(video.time, "sleep",
                                                   return_value=None))
                with contextlib.redirect_stdout(io.StringIO()):
                    jobs = video.submit_all(root, shots, stills, planned, ep=1)
            self.assertEqual(jobs["LN01"]["state"], "submitted",
                             "队列满退避后应提交成功，而不是 failed")
            self.assertEqual(seq, [])

    def test_submit_all_switches_key_on_queue_full(self):
        """队列满时**换 key 重试**（2026-09-22 改造）。

        依据：队列满是**每条通道各自的状态** —— 实测同一晚 pack03 撞满 2 次后由
        另一条 key 提交成功；国际池堵死时国内入口（不同 endpoint）可能立刻就过。
        旧逻辑一条 key 原地退避 60/120/180/240/300s（≈15 分钟）；现在每轮重新
        claim（换通道）+ 该 key 冷却一轮（note_rate_limited）。
        """
        import contextlib
        from unittest import mock

        from v5 import config
        from v5.media import providers, video

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            out = root / "media" / "ep1"
            (out / "clips").mkdir(parents=True)
            shots = [{"name": "LN01", "seconds": 5, "visual": "v",
                      "shot_type": "全景", "angle": "平视", "camera": "固定"}]
            planned = [{"name": "LN01", "frame_plan": {"relation": "cut",
                                                       "use_prev_last": False}}]
            stills = {"LN01": {"url": "http://x/a.png"}}
            seen_keys: list = []

            def fake_submit(*a_, **k_):
                seen_keys.append(k_.get("key"))
                if k_.get("key") == "k1":
                    raise providers.QueueFullError("full")
                return {"video_id": "vid1"}

            with contextlib.ExitStack() as st:
                st.enter_context(mock.patch.object(config, "AGNES_API_KEYS",
                                                   ["k1", "k2"]))
                st.enter_context(mock.patch.object(config, "VIDEO_KEY_ROTATE", True))
                st.enter_context(mock.patch.object(providers, "submit_video",
                                                   side_effect=fake_submit))
                st.enter_context(mock.patch.object(video.time, "sleep",
                                                   return_value=None))
                with contextlib.redirect_stdout(io.StringIO()):
                    jobs = video.submit_all(root, shots, stills, planned, ep=1)

            self.assertEqual(jobs["LN01"]["state"], "submitted",
                             "换 key 后应提交成功，而不是原地退避到放弃")
            self.assertEqual(seen_keys, ["k1", "k2"],
                             "队列满后必须换到**另一条** key（不是同一条原地等）")
            self.assertEqual(jobs["LN01"]["key"], "k2",
                             "记账里的 key 应是最终成功那条（轮询要按它查）")

    def test_submit_all_rate_limit_does_not_block_rest(self):
        """429 只能跳过本镜，不能中断整批提交。

        旧实现 `except RateLimitError: break` —— 429 一出现，后面所有镜
        连提交机会都没有（maskparade 实测 LN11 之后的镜被整体挡掉）。
        """
        import contextlib
        from unittest import mock

        from v5.media import providers, video

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "media" / "ep1" / "clips").mkdir(parents=True)
            shots = [{"name": "LN%02d" % i, "seconds": 5, "visual": "v",
                      "shot_type": "全景", "angle": "平视", "camera": "固定"}
                     for i in (1, 2, 3)]
            planned = [{"name": s["name"],
                        "frame_plan": {"relation": "cut", "use_prev_last": False}}
                       for s in shots]
            stills = {s["name"]: {"url": "http://x/%s.png" % s["name"]}
                      for s in shots}

            def fake_submit(*a_, **k_):
                # LN01 撞 429；LN02/LN03 应仍被提交
                if not fake_submit.seen:
                    fake_submit.seen = True
                    raise providers.RateLimitError("429")
                return {"video_id": "vid"}

            fake_submit.seen = False

            with contextlib.ExitStack() as st:
                st.enter_context(mock.patch.object(providers, "submit_video",
                                                   side_effect=fake_submit))
                st.enter_context(mock.patch.object(video.time, "sleep",
                                                   return_value=None))
                with contextlib.redirect_stdout(io.StringIO()):
                    jobs = video.submit_all(root, shots, stills, planned, ep=1)
            self.assertNotEqual(jobs["LN01"]["state"], "submitted")
            self.assertEqual(jobs["LN02"]["state"], "submitted",
                             "429 之后的后继镜必须仍能提交")
            self.assertEqual(jobs["LN03"]["state"], "submitted")


class TestFacelessCarrierHardKey(unittest.TestCase):
    """无脸载体长出真人五官必须算硬伤（2026-09-10 maskparade LN12 实测）。

    设定是"白瓷面具无五官"，实际画成有瞳孔/鼻梁/嘴唇的真人脸——旧 HARD_KEYS
    没有覆盖，QC 即使报"面具上出现真人五官"也不会触发重生成，直接进成片。
    """

    def test_keys_cover_faceless_violation(self):
        from v5.media import pipeline

        desc = "面具上出现了完整的人类五官，眼睛、鼻子和嘴都很清晰"
        hit = any(k in desc for k in pipeline.HARD_KEYS)
        self.assertTrue(hit, "HARD_KEYS 必须能命中'面具长出五官'类描述")

    def test_extra_constraint_is_positive_only(self):
        """强化约束不得含部件名词（会反向诱发凭空长脸）。"""
        from v5.media import pipeline

        for w in ("眼睛", "鼻子", "嘴唇", "瞳孔", "面部", "五官"):
            self.assertNotIn(w, pipeline.ANTI_FACELESS_HARD)

    def test_anti_split_is_positive_only(self):
        """反分屏强化约束也不得含分屏类概念词。"""
        from v5.media import pipeline

        for w in ("分屏", "格子", "拼接", "拼图", "两格"):
            self.assertNotIn(w, pipeline.ANTI_SPLIT_HARD)


class TestBackgroundDriftHardKey(unittest.TestCase):
    """背景/窗外场景漂移必须算硬伤（2026-09-10 maskparade LN12 实测）。

    分镜要求夜间车窗为**纯黑**，实际画成荒漠沙丘。旧 HARD_KEYS 未覆盖，
    QC 即使报"窗外出现荒漠"也不会触发重生成 → 夜戏的封闭感直接丢失，
    且与剧情矛盾（车上根本没有窗外风景）。
    """

    def test_keys_cover_background_drift(self):
        from v5.media import pipeline

        for desc in ("窗外出现了荒漠沙丘，与要求的纯黑背景不符",
                     "背景里出现街道和建筑，分镜要求纯黑",
                     "画面下方背景出现沙丘纹理"):
            hit = any(k in desc for k in pipeline.HARD_KEYS)
            self.assertTrue(hit, "HARD_KEYS 必须能命中背景漂移类描述：%r" % desc)

    def test_qc_prompt_has_drift_criterion(self):
        """QC 提示词必须显式给出第 7 条判据，否则模型不知道该报。"""
        from v5.media import qc

        self.assertIn("背景", qc.PROMPT)
        self.assertIn("纯黑", qc.PROMPT)

    def test_qc_prompt_guards_against_false_positive(self):
        """必须给出反误报边界——分镜没要求纯黑的背景不算漂移。"""
        from v5.media import qc

        self.assertIn("只", qc.PROMPT)
        self.assertIn("不算漂移", qc.PROMPT)


class TestQcRateLimitBackoff(unittest.TestCase):
    """QC 撞 429 必须**退避重试**：不许兜底成"无硬伤"，也不许把整链崩掉。

    2026-09-12 事故：为并发化加的 429 退避**漏了 `import time`**，撞 429 时
    抛 `NameError` → clockmaker 媒体链跑了 17 分钟后整条崩掉。而当时 241 例
    测试全绿——**没有一个用例走到 429 分支**。本类就是补那个洞：
    "全绿"必须真的覆盖这条路径，否则绿色是假的。
    """

    def test_module_imports_time(self):
        """直接锁死"用了 time 却没 import"这一类 bug（事故根因）。"""
        from v5.media import pipeline

        self.assertTrue(hasattr(pipeline, "time"),
                        "pipeline 用了 time.sleep 就必须 import time")

    def test_is_rate_limit_recognises_provider_message(self):
        """真限速文案要认得出；普通错误不能误判（否则白白退避 75s）。"""
        from v5.llm import is_rate_limit

        real = ("Error code: 429 - {'error': {'code': '', 'message': \"You've "
                "reached the API rate limit for free users. Upgrade to a Token "
                "Plan to unlock higher limits...\", 'type': 'AgnesAI_error'}}")
        self.assertTrue(is_rate_limit(RuntimeError(real)))
        self.assertTrue(is_rate_limit(RuntimeError("Too Many Requests")))
        self.assertFalse(is_rate_limit(ValueError("bad json")))

    def test_backoff_retries_then_succeeds(self):
        """429 两次后成功 → 共 3 次调用，退避 5s/10s，返回正常结果。"""
        from v5.media import pipeline

        sleeps: list = []
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("Error code: 429 - rate limit")
            return "ok"

        got = pipeline.call_with_backoff(fn, attempts=5, base=5.0,
                                         sleep=sleeps.append)
        self.assertEqual(got, "ok")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(sleeps, [5.0, 10.0])

    def test_non_rate_limit_raises_immediately(self):
        """非限速异常立即抛，不浪费一次退避。"""
        from v5.media import pipeline

        sleeps: list = []

        def fn():
            raise ValueError("bad json")

        with self.assertRaises(ValueError):
            pipeline.call_with_backoff(fn, attempts=5, base=5.0,
                                       sleep=sleeps.append)
        self.assertEqual(sleeps, [])

    def test_backoff_exhausted_raises_last_error(self):
        """退避耗尽仍失败 → 抛出（由上层如实记录），绝不静默算作通过。"""
        from v5.media import pipeline

        sleeps: list = []

        def fn():
            raise RuntimeError("429 rate limit")

        with self.assertRaises(RuntimeError):
            pipeline.call_with_backoff(fn, attempts=3, base=5.0,
                                       sleep=sleeps.append)
        self.assertEqual(sleeps, [5.0, 10.0], "3 次尝试只该退避 2 次")

    def test_review_shot_type_reraises_rate_limit(self):
        """景别校验撞 429 必须向上抛（交给退避层）。

        旧实现无条件 `except Exception: return ok=True`——限速被兜底成
        "校验通过"，就是**静默漏检**：景别跨档偏离的镜当成合格进成片。
        """
        from v5.media import qc

        with mock.patch.object(qc, "chat_for",
                               side_effect=RuntimeError("Error code: 429 - rate limit")), \
                mock.patch.object(qc, "_data_uri",
                                  return_value="data:image/jpeg;base64,AA"):
            with self.assertRaises(RuntimeError):
                qc.review_shot_type("/x/a.jpg", {"shot_type": "全景"})

    def test_review_shot_type_still_swallows_other_errors(self):
        """非限速异常仍兜底 ok=True——构图校验是增强，不能阻断生产。"""
        from v5.media import qc

        with mock.patch.object(qc, "chat_for",
                               side_effect=ValueError("bad json")), \
                mock.patch.object(qc, "_data_uri",
                                  return_value="data:image/jpeg;base64,AA"):
            r = qc.review_shot_type("/x/a.jpg", {"shot_type": "全景"})
        self.assertTrue(r["ok"])


class TestClipQcFrameAccumulation(unittest.TestCase):
    """`review_clip` 必须**逐帧累计**问题，不能只用最后一帧。

    2026-09-12 修正的缩进 bug：`rep` 在帧循环内赋值、却只在**循环外**使用 →
    前 4 帧的结论被静默丢弃（40 镜 × 5 帧 = 200 次调用，实际只用 40 次，
    80% 白调）。原注释"去重（同一问题在 3 帧里都出现）"说明意图本就是
    跨帧累计 + 去重。附带：5 帧全失败时旧实现 `rep` 未绑定 →
    `UnboundLocalError` 直接崩掉整个 clipqc。
    """

    def _run(self, per_frame, n_frames=5):
        """per_frame[i] = 第 i 帧 qc.review 返回的 issues；传 Exception 表示该帧失败。"""
        import contextlib

        from v5.media import clipqc, qc

        calls = {"i": 0}
        frames = [Path("/x/LN01.f%d.jpg" % i) for i in range(n_frames)]

        def fake_review(path, shot=None, style_spec=""):
            i = calls["i"]
            calls["i"] += 1
            r = per_frame[i]
            if isinstance(r, Exception):
                raise r
            return {"issues": r}

        with mock.patch.object(clipqc, "grab", lambda *a, **k: frames), \
                mock.patch.object(qc, "review", side_effect=fake_review):
            with contextlib.redirect_stdout(io.StringIO()):
                return clipqc.review_clip(Path("/x/LN01.mp4"), {}, Path("/tmp"))

    def test_accumulates_issues_across_all_frames(self):
        """第 1 帧与第 5 帧各有问题 → **两条都要报**（旧实现只报第 5 帧）。"""
        got = self._run([
            [{"level": "P0", "desc": "画面出现可读文字"}],
            [], [], [],
            [{"level": "P0", "desc": "缺少关键道具"}],
        ])
        self.assertEqual(len(got), 2, "跨帧问题必须累计，不能只看最后一帧")
        self.assertIn("画面出现可读文字", got[0])
        self.assertIn("缺少关键道具", got[1])

    def test_dedups_repeated_issue(self):
        """同一问题在多帧出现 → 去重成一条。"""
        dup = [{"level": "P0", "desc": "画面出现可读文字"}]
        self.assertEqual(len(self._run([dup] * 5)), 1)

    def test_all_frames_failing_does_not_crash(self):
        """5 帧全部异常 → 返回 []（旧实现 `rep` 未绑定 → UnboundLocalError）。"""
        self.assertEqual(self._run([RuntimeError("boom")] * 5), [])

    def test_partial_failure_still_reports(self):
        """部分帧失败 → 仍报出成功帧的问题。"""
        got = self._run([
            RuntimeError("429"), RuntimeError("429"),
            [{"level": "P0", "desc": "缺少关键道具"}],
            RuntimeError("timeout"), [],
        ])
        self.assertEqual(len(got), 1)
        self.assertIn("缺少关键道具", got[0])


class TestClipQcAuditParallel(unittest.TestCase):
    """`clipqc.audit` 必须**并发**执行。

    串行的失败模式极贵：2026-09-12 实测 LN18 的一次视觉调用挂了 **15 分钟**
    （`timeout=300` 未掐断——流式响应一直在缓慢吐字节），整个 clipqc 被那一镜
    冻住 15 分钟，成片推迟 15 分钟。
    """

    def test_audit_covers_every_clip_and_runs_in_parallel(self):
        import threading

        from v5.media import clipqc

        # audit 会过滤"盘上不存在"的路径 → 用真实临时文件
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            names = ["LN%02d" % i for i in range(1, 9)]
            clips = {}
            for n in names:
                f = tmp / (n + ".mp4")
                f.write_bytes(b"\x00")
                clips[n] = str(f)

            # barrier(4)：必须**同时**有 4 个 worker 在跑才能通过。
            # 串行实现会在此超时（BrokenBarrierError）→ 测试失败。
            barrier = threading.Barrier(4, timeout=5)
            seen: list = []

            def fake_review_clip(p, shot, work_dir, log=print, hard_keys=None):
                seen.append(p.stem)
                barrier.wait()
                return ["硬伤"] if p.stem == "LN03" else []

            with mock.patch.object(clipqc, "review_clip",
                                   side_effect=fake_review_clip):
                bad = clipqc.audit(clips, [], tmp,
                                   log=lambda *_: None, workers=4)

            self.assertEqual(sorted(seen), sorted(names), "每个 clip 都必须被审到")
            self.assertEqual(list(bad), ["LN03"])

    def test_audit_skips_missing_clip_files(self):
        """盘上不存在的 clip 不送审（`done()` 与盘上文件可能不一致）。"""
        from v5.media import clipqc

        calls: list = []
        with mock.patch.object(clipqc, "review_clip",
                               side_effect=lambda p, *a, **k: calls.append(p) or []):
            bad = clipqc.audit({}, [], Path("/tmp"), log=lambda *_: None, workers=2)
        self.assertEqual(bad, {})
        self.assertEqual(calls, [])


class TestMediaGateRoleScope(unittest.TestCase):
    """媒体门的角色口径 + 门前物化对账（2026-09-12 实测事故）。

    事故：supervisor 架构下 7 个角色产物**全部在盘**、reviewer 也 `pass: true`，
    但 `.agent_state.json` 的 `phases` 是空的 → `media_gate("render")` 拦住媒体链，
    而且**查不出原因**（后置记账被 `except: pass` 吞掉）。
    两处根因，两条锁：
      ① 口径：旧 `ROLES` 是 8 个（含 `director`），而 supervisor 架构里 `director`
         就是 supervisor 自己、**没有任何路径**会把它记为 complete → 门永远拦。
      ② 依赖：`phases` 原本只靠角色节点的后置记账；改为媒体门前**按磁盘事实对账**。
    """

    def test_gate_roles_exclude_director(self):
        """门要查的是**被派发的 7 个**角色，不含 director。"""
        self.assertEqual(len(guards.GATE_ROLES), 7, guards.GATE_ROLES)
        self.assertNotIn("director", guards.GATE_ROLES)
        self.assertEqual(set(guards.GATE_ROLES), set(guards.PREREQ))

    def test_reconcile_writes_every_gate_role(self):
        """对账必须为**全部** GATE_ROLES 写下 phases 条目（complete 或 failed）。

        这样门读到的就不再是"缺项"，而是按磁盘事实得出的裁决。
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            m = guards.reconcile_manifest(root, {})
            # ★ M2：`phases` 已二维 → 断言走 `phase_of`（**读**的唯一入口）
            for r in guards.GATE_ROLES:
                self.assertTrue(guards.phase_of(m, r, 1),
                                "对账漏了角色：" + r)
            self.assertTrue((root / ".agent_state.json").exists(), "对账必须落盘")

    def test_reconcile_recognises_artifact_on_disk(self):
        """产物在盘、账本没记 → 对账应把它记为 complete（物化对账的语义）。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / guards.out_path("worldbuilder")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# 世界观\n\n（测试用）\n", encoding="utf-8")
            m = guards.reconcile_manifest(root, {})
            self.assertEqual(guards.phase_of(m, "worldbuilder", 1), "complete")

    def test_media_gate_still_blocks_without_review_pass(self):
        """对账**不放松**评审要求：review 未 pass 仍然拦。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            m = guards.reconcile_manifest(root, {})
            ok, why = guards.media_gate("render", m)
            self.assertFalse(ok)
            self.assertIn("创作链未完成", why)   # 产物不在盘 → 先缺角色

    def test_reconcile_reads_review_verdict_from_disk(self):
        """`review` 没落盘时，从 `reviewer/review.md` 的围栏判定块补记。

        全仓库**没有任何代码**写 `m["review"]`，判定只活在 md 里 →
        media_gate 会一直报「评审未通过（无 pass: true）」，即使 reviewer 明明 pass。
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / guards.out_path("reviewer")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("评分……\n\n```yaml\npass: true\nrerun: []\nreasons: []\n```\n",
                         encoding="utf-8")
            m = guards.reconcile_manifest(root, {})
            self.assertTrue(m["review"]["passed"])
            self.assertEqual(m["review"]["rerun"], [])

    def test_reconcile_does_not_invent_review(self):
        """没有 review.md 时**不许凭空造** passed（repair-only 的同一条原则）。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            m = guards.reconcile_manifest(root, {})
            self.assertNotIn("passed", m.get("review") or {})


class TestAssetMentionAndIntegrity(unittest.TestCase):
    """`@资产名` 精确匹配 + **cast 之后**的资产完整性校验（2026-09-12）。

    问题背景：资产契约门（`series._assets_gate`）跑在 `cast` **之前**，注册表必然
    还是空的 → 只会误报；而全流程**没有任何一步**检查"分镜点名的资产是否真有图"。
    于是"资产卡写了、图没生成"会静默降级成文字锚点，问题到成片才暴露。
    """

    def _reg(self):
        return {"assets": [
            {"name": "老魏", "type": "character", "keywords": ["老魏", "司机"],
             "url": "https://x/laowei.png"},
            {"name": "出租车", "type": "prop", "keywords": ["出租车"],
             "url": "https://x/taxi.png"},
            {"name": "雪夜街道", "type": "location", "keywords": ["街道"],
             "url": "https://x/street.png"},
        ]}

    def test_resolve_mentions_is_registry_driven(self):
        """**注册表驱动、长名优先** —— 中文无词边界，`@出租车里` 必须切成 `@出租车`。

        这正是"正则切不动"的地方：`@雪夜街道空镜` 里名字到哪结束，只有拿已知
        名字去比对才知道。所以匹配器不能用纯正则，必须由注册表驱动。
        """
        from v5.media import assets

        matched, leftover = assets.resolve_mentions(
            "在@出租车里，@老魏看了@老魏的袖子。", self._reg())
        self.assertEqual(sorted(matched), ["出租车", "老魏"])
        self.assertEqual(leftover, [], "全部匹配上时不该有遗留项")

    def test_resolve_mentions_trims_at_asset_boundary(self):
        """`@雪夜街道空镜` → 只取 `雪夜街道`（长名优先切词）。"""
        from v5.media import assets

        matched, leftover = assets.resolve_mentions("@雪夜街道空镜", self._reg())
        self.assertEqual(matched, ["雪夜街道"])
        self.assertEqual(leftover, [])

    def test_exact_mention_preferred_over_keywords(self):
        """@了具体资产 → 精确命中它，不靠 keywords 子串猜。"""
        from v5.media import assets

        shot = {"visual": "出租车里，@老魏握方向盘", "dialogue": ""}
        hits, unresolved = assets.hits_for_shot(self._reg(), shot)
        self.assertEqual([h["name"] for h in hits], ["老魏"])
        self.assertEqual(unresolved, [])

    def test_unresolved_mention_is_reported_not_silent(self):
        """@ 了注册表里没有的名字 → 如实返回 unresolved（**不许静默**）。

        注意断言的是 **前缀**：中文无词边界，`@沈师傅从工作台起身` 无法确定名字
        到哪结束，所以只能把 `@` 之后的整串作为待查项报出来（由人判断）。
        """
        from v5.media import assets

        hits, unresolved = assets.hits_for_shot(
            self._reg(), {"visual": "@沈师傅从工作台起身", "dialogue": ""})
        self.assertTrue(unresolved, "未匹配的 @ 引用必须报出来")
        self.assertTrue(unresolved[0].startswith("沈师傅"), unresolved)

    def test_falls_back_to_keywords_without_mention(self):
        """没有 @ 的旧分镜 → 回退 keywords 子串匹配（向后兼容）。"""
        from v5.media import assets

        hits, _ = assets.hits_for_shot(
            self._reg(), {"visual": "出租车停在雪地里", "dialogue": ""})
        self.assertIn("出租车", [h["name"] for h in hits])

    def test_validate_flags_unbound_shot(self):
        from v5.media import assets

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "assets.json").write_text(json.dumps({"assets": [
                {"name": "老魏", "type": "character", "keywords": ["老魏"],
                 "url": "https://x/laowei.png"}]}), encoding="utf-8")
            probs = assets.validate_assets(
                root, [{"name": "LN01", "visual": "@老魏上车", "dialogue": ""}], {})
            self.assertTrue(any("没绑到任何参考图" in p for p in probs), probs)

    def test_validate_does_not_flag_location_only_shot(self):
        """只 @ 了场景的镜**不算漏绑** —— `location` 设计上就不绑参考图
        （场景图自带机位，会覆盖分镜构图）。否则纯空镜/场景镜会全是误报。"""
        from v5.media import assets

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "assets.json").write_text(json.dumps({"assets": [
                {"name": "雪夜街道", "type": "location", "keywords": ["街道"],
                 "url": "https://x/street.png"}]}), encoding="utf-8")
            probs = assets.validate_assets(
                root, [{"name": "LN02", "visual": "@雪夜街道空镜", "dialogue": ""}], {})
            self.assertEqual(probs, [])

    def test_validate_reports_missing_image_file(self):
        """注册表有条目但图不在盘 → 报出来（"有卡没图"的核心检测）。"""
        from v5.media import assets

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "assets.json").write_text(json.dumps({"assets": [
                {"name": "老魏", "type": "character", "keywords": ["老魏"]}]}),
                encoding="utf-8")
            probs = assets.validate_assets(root, [], {})
            self.assertTrue(any("参考图不在盘" in p for p in probs), probs)


class TestAssetContractJson(unittest.TestCase):
    """结构化资产清单 `assets.contract.json` 优先于对 `assets.md` 散文的正则解析。

    理由：散文解析会**静默失败**（换措辞/加列 → 正则失配 → 参考图不生成且无人知晓）。
    """

    def test_contract_preferred_and_normalised(self):
        from v5.media import cast

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "assetdesigner").mkdir(parents=True)
            (root / "assetdesigner" / "assets.contract.json").write_text(
                json.dumps({"characters": [{"name": "老魏", "appearance": "深灰旧夹克"}],
                            "assets": [{"name": "出租车", "type": "prop"}]}),
                encoding="utf-8")
            chars, items = cast._load_contract(root)
            self.assertEqual([c["name"] for c in chars], ["老魏"])
            self.assertEqual(items[0]["keywords"], ["出租车"], "缺 keywords 时用 name 兜底")

    def test_absent_contract_returns_none(self):
        from v5.media import cast

        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cast._load_contract(Path(d)), (None, None))

    def test_broken_contract_falls_back(self):
        """坏 JSON → 当作不存在（走散文兜底），不抛异常。"""
        from v5.media import cast

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "assets.contract.json").write_text("{ not json", encoding="utf-8")
            self.assertEqual(cast._load_contract(root), (None, None))


class _env_cleared:
    """临时清空某个环境变量（测试用）。"""

    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        import os
        self.old = os.environ.pop(self.name, None)
        return self

    def __exit__(self, *exc):
        import os
        if self.old is not None:
            os.environ[self.name] = self.old
        return False


class TestMultiKeySubmitGate(unittest.TestCase):
    """多 key 提交配速（2026-09-16）。

    实测依据（`scripts/probe_multikey.py`，五阶段）：同一条 key 在 60s 内第二次
    提交 → **429**；**不同** key 间隔 2s → 全部通过 ⇒ 1rpm 是 **per-key**，
    故提交段可按 key 数摊薄（40 镜 / 3 key：65s×40≈43 分 → 约 15 分）。
    这里锁死四件事：
      1. 池子**先轮完一圈再回头**用第一条（不是死盯第一条）；
      2. 轮完一圈后必须**等够该 key 的间隔**才复用；
      3. 撞 429 的 key 下一镜被跳过（不能甩回同一条窗口）；
      4. `VIDEO_KEY_ROTATE=0`（默认）只用第一条 key ⇒ 与改造前等价。
    """

    def _pool(self, keys=("a", "b", "c"), interval=65.0):
        """注入假时钟的池子 —— 闸门可确定性验证，且**不真的睡觉**。"""
        from v5.media import keypool

        clock = [0.0]
        slept: list[float] = []

        def fake_sleep(s):
            slept.append(s)
            clock[0] += s

        return (keypool.KeyPool(list(keys), interval,
                                monotonic=lambda: clock[0], sleep=fake_sleep),
                clock, slept)

    def test_rotates_before_reusing_a_key(self):
        pool, _clock, slept = self._pool()
        self.assertEqual([pool.claim()[0] for _ in range(3)], [0, 1, 2])
        self.assertEqual(slept, [], "池里有 3 条空闲 key，前 3 次提交不该等")
        self.assertEqual(pool.claim()[0], 0, "第 4 次要回头用第 1 条")
        self.assertEqual(slept, [65.0], "且必须等满该 key 的间隔")

    def test_returns_the_real_keys_in_rotation(self):
        pool, _, _ = self._pool(keys=("k1", "k2"))
        self.assertEqual([pool.claim()[1] for _ in range(2)], ["k1", "k2"])

    def test_rate_limited_key_is_skipped_next_round(self):
        pool, _clock, slept = self._pool(keys=("k1", "k2"))
        pool.claim()
        pool.note_rate_limited(0)
        self.assertEqual(pool.claim()[0], 1, "撞过 429 的 key 下一镜应被跳过")
        self.assertEqual(slept, [])

    def test_rate_limited_key_costs_an_extra_round(self):
        """429 让这条 key **多停一轮**（now + 2×interval 才复用）。

        语义写死在测试里，防止后人把 `note_rate_limited` 改成"记成此刻"
        （那样它与 `claim()` 已有的记账重复，等于**什么都没做**）。
        """
        pool, _clock, slept = self._pool(keys=("k1",), interval=10.0)
        pool.claim()                        # t=0 用掉这条 key
        pool.note_rate_limited(0)           # 被拒 → last 推后到 t=10
        pool.claim()                        # 再要它，得等到 t=20
        self.assertEqual(slept, [20.0])

    def test_rotate_off_uses_first_key_only(self):
        """默认（未开轮转）时池里只有第一条 —— 这是"行为与改造前等价"的依据。"""
        from v5.media import keypool, video

        with mock.patch.object(video.config, "AGNES_API_KEYS", ["k1", "k2", "k3"]), \
                mock.patch.object(video.config, "VIDEO_KEY_ROTATE", False):
            self.assertEqual(keypool.KeyPool.of().keys, ["k1"])
        with mock.patch.object(video.config, "AGNES_API_KEYS", ["k1", "k2", "k3"]), \
                mock.patch.object(video.config, "VIDEO_KEY_ROTATE", True):
            self.assertEqual(keypool.KeyPool.of().keys, ["k1", "k2", "k3"])

    # ── 端到端（假 submit_video，不调 API）──
    def _shots(self, n=3):
        shots = [{"name": "LN%02d" % i, "seconds": 5, "visual": "v",
                  "shot_type": "全景", "angle": "平视", "camera": "固定"}
                 for i in range(1, n + 1)]
        planned = [{"name": s["name"],
                    "frame_plan": {"relation": "cut", "use_prev_last": False}}
                   for s in shots]
        stills = {s["name"]: {"url": "http://x/%s.png" % s["name"]} for s in shots}
        return shots, planned, stills

    def _run(self, shots, planned, stills, side_effect, rotate: bool,
             keys=("k1", "k2", "k3")):
        import contextlib

        from v5.media import video

        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(video.providers, "submit_video",
                                   side_effect=side_effect), \
                    mock.patch.object(video.config, "VIDEO_MODE", "reference"), \
                    mock.patch.object(video.config, "AGNES_API_KEYS", list(keys)), \
                    mock.patch.object(video.config, "VIDEO_KEY_ROTATE", rotate), \
                    mock.patch.object(video.config, "VIDEO_SUBMIT_MIN_INTERVAL_S", 0), \
                    contextlib.redirect_stdout(io.StringIO()):
                jobs = video.submit_all(Path(d), shots, stills, planned, ep=1,
                                        log=lambda *_: None)
        return jobs

    def test_submit_all_rotates_keys_across_shots(self):
        shots, planned, stills = self._shots(3)
        used: list[str] = []

        def fake_submit(prompt, *, seconds=8, key=None, **kw):
            used.append(key)
            return {"video_id": "vid%d" % len(used)}

        jobs = self._run(shots, planned, stills, fake_submit, rotate=True)
        self.assertEqual(used, ["k1", "k2", "k3"], "三镜应各用一条不同 key")
        self.assertTrue(all(jobs[s["name"]]["state"] == "submitted" for s in shots))

    def test_submit_all_default_still_uses_one_key(self):
        shots, planned, stills = self._shots(3)
        used: list[str] = []

        def fake_submit(prompt, *, seconds=8, key=None, **kw):
            used.append(key)
            return {"video_id": "vid%d" % len(used)}

        self._run(shots, planned, stills, fake_submit, rotate=False)
        self.assertEqual(used, ["k1"] * 3, "未开轮转时行为必须与改造前一致")

    def test_submit_all_429_skips_that_shot_but_continues(self):
        """429 只跳过本镜（不中断整批），且**不再甩回同一条 key**。"""
        shots, planned, stills = self._shots(3)
        used: list[str] = []

        def fake_submit(prompt, *, seconds=8, key=None, **kw):
            used.append(key)
            if len(used) == 1:
                from v5.media import providers
                raise providers.RateLimitError("429")
            return {"video_id": "vid%d" % len(used)}

        jobs = self._run(shots, planned, stills, fake_submit, rotate=True)
        self.assertEqual(used, ["k1", "k2", "k3"])
        self.assertEqual(jobs["LN01"]["state"], "pending",
                         "429 是「稍后再来」，不是失败 —— 不能标 failed")
        self.assertEqual(jobs["LN02"]["state"], "submitted")
        self.assertEqual(jobs["LN03"]["state"], "submitted")


class TestVideo503Classification(unittest.TestCase):
    """503 必须按**错误码**分流（2026-09-16 实测缺陷）。

    `POST /v1/videos` 用不存在的 model 时，Agnes 返回的**也是 503**
    （`code=model_not_found`）。旧实现 `if status==503 or "queue_full" in text`
    把任何 503 当"队列满"⇒ 白退避 5 轮（≈20+40+60+80+100 秒）并打出**误导性的**
    「队列持续满」——失败没被如实描述。三条判据：
      1. 带明确非容量错误码 → 判死（不重试），且消息里带上原始 error；
      2. 真·队列满 → 仍可重试（这条保住 2026-09-10 的教训）；
      3. 拿不到线索（如网关 HTML）→ **按容量处理**（宁多退避，别丢镜）。
    """

    class _Resp:
        def __init__(self, status, text):
            self.status_code = status
            self.text = text

        def json(self):
            return json.loads(self.text)

    def _submit(self, status, text):
        from v5.media import providers

        resp = self._Resp(status, text)

        class _C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **k):
                return resp

        with mock.patch.object(providers.httpx, "Client", lambda **k: _C()):
            return providers.submit_video("p", mode="keyframe",
                                          first_frame="http://x/a.png")

    def test_model_not_found_503_is_fatal(self):
        from v5.media import providers

        with self.assertRaises(RuntimeError) as cm:
            self._submit(503, json.dumps({
                "error": {"code": "model_not_found",
                          "message": "No available channel for model X"}}))
        self.assertNotIsInstance(cm.exception, providers.QueueFullError)
        self.assertIn("model_not_found", str(cm.exception))

    def test_queue_full_503_is_still_retryable(self):
        from v5.media import providers

        with self.assertRaises(providers.QueueFullError):
            self._submit(503, json.dumps({
                "error": {"code": "video_queue_full", "message": "queue is full"}}))

    def test_opaque_503_stays_retryable(self):
        from v5.media import providers

        with self.assertRaises(providers.QueueFullError):
            self._submit(503, "<html><body>502 Bad Gateway</body></html>")


class TestBindEpisode(unittest.TestCase):
    """`episode_index` 的**唯一写入方** = `series.bind_episode`（M1，2026-09-16）。

    改造前它是 **5 处读、0 处写**（恒为 1）⇒ 集级产物永远落在第 1 集。
    为什么必须"唯一写入方"：集号有两个消费者 ——
      · `guards.post_validate` 按它校验产物（写盘侧）；
      · `guards.resolve_path` 按它解析读路径。
    两边口径不一致 = 角色按第 N 集写、记账按第 1 集查 → **全判 failed**，
    而且日志里只看到"阶段未 complete"，查不出原因。
    """

    def _root(self):
        return Path(tempfile.mkdtemp(prefix="bind_ep_"))

    def test_writes_episode_index(self):
        from v5 import series
        root = self._root()
        m = series.bind_episode(root, 3)
        self.assertEqual(m["episode_index"], 3)
        on_disk = json.loads((root / ".agent_state.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["episode_index"], 3, "必须立即落盘（不是只改内存）")

    def test_switching_episode_isolates_state(self):
        """★ M2：换集**不再清空全账** —— `phases` 已二维，按集隔离。

        仍然要清/重置的两项，都是"每集重新开始"的语义：
          · `revision_counts` —— 每集的回退预算独立（不清则第 1 集用光后第 2 集不再回退）
          · `review`           —— 一维，换集必须作废（第 2 集不能拿第 1 集的 pass）
        """
        from v5 import series
        root = self._root()
        series.bind_episode(root, 1)
        m = json.loads((root / ".agent_state.json").read_text(encoding="utf-8"))
        # 造一份**第 1 集的名册**（旧一维结构，模拟历史/前一轮）
        m["phases"] = {"director": "complete", "reviewer": "complete"}
        m["review"] = {"passed": True}
        m["revision_counts"] = {"scriptwriter": 3}
        m["token_usage"] = {"run": 999999, "roles": {}}
        (root / ".agent_state.json").write_text(json.dumps(m), encoding="utf-8")

        out = series.bind_episode(root, 2)
        self.assertEqual(out["episode_index"], 2)
        # ① 第 1 集的名册**保留**（可回头重渲），且迁到 "1" 名下
        self.assertEqual(guards.phase_of(out, "director", 1), "complete",
                         "换集不该丢掉第 1 集的名册")
        # ② 第 2 集自己名下**没有**任何 complete —— 门会拦（不会拿第 1 集的放行）
        self.assertEqual(guards.phase_of(out, "director", 2), "")
        self.assertEqual(guards.phase_of(out, "reviewer", 2), "")
        # ③ 每集重置的两项
        self.assertEqual(out["revision_counts"], {}, "换集必须重置回退计数（每集独立预算）")
        self.assertEqual(out["token_usage"]["run"], 0, "换集必须重置 token 账（否则几集就熔断）")
        self.assertNotIn("review", out, "换集必须作废评审结论")
        self.assertNotIn("media_loop", out, "换集必须清掉上一集的渲染状态")

    def test_same_episode_does_not_clear(self):
        """**同一集重跑**（--fresh 之后续跑）不能清 phases —— 否则每次都从头来。"""
        from v5 import series
        root = self._root()
        series.bind_episode(root, 2)
        m = json.loads((root / ".agent_state.json").read_text(encoding="utf-8"))
        m["phases"] = {"director": "complete"}
        (root / ".agent_state.json").write_text(json.dumps(m), encoding="utf-8")
        out = series.bind_episode(root, 2)
        self.assertEqual(out["phases"], {"director": "complete"})


class TestEpisodeLoop(unittest.TestCase):
    """★ M3：集循环 —— 一条命令按集跑媒体链（串行、逐集绑定、逐集过门）。

    真跑 N 集要几小时，所以这里用**假的 pipeline** 锁住编排语义：
    顺序对不对、有没有逐集绑定集号、有没有逐集过门、失败时会不会**静默跳过**。
    """

    def test_parse_episodes(self):
        from v5.series import parse_episodes
        self.assertEqual(parse_episodes("3"), [3])
        self.assertEqual(parse_episodes("1-4"), [1, 2, 3, 4])
        self.assertEqual(parse_episodes("1,3,5"), [1, 3, 5])
        self.assertEqual(parse_episodes("1-3,7"), [1, 2, 3, 7])
        self.assertEqual(parse_episodes(" 2 , 2 "), [2], "应去重")
        for bad in ("", "abc", "0"):
            with self.assertRaises(SystemExit, msg="非法输入必须响亮报错：%r" % bad):
                parse_episodes(bad)

    def _run(self, spec, statuses):
        """跑一次集循环，返回 (结果, 每次 pipeline.run 的 (ep, ep_index_snapshot))。"""
        import asyncio
        from v5 import series

        seen = []

        def fake_pipeline_run(root, **kw):
            ep = kw.get("ep")
            m = guards.load_manifest(root)
            seen.append((ep, int(m.get("episode_index", 1) or 1)))
            st = statuses.pop(0) if statuses else "ok"
            return {"status": st, "final": "media/ep%d/episode_final.mp4" % ep}

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            real = series.config.PROJECTS_DIR
            series.config.PROJECTS_DIR = root
            try:
                with mock.patch.object(series, "_input_gates") as gt, \
                        mock.patch("v5.media.pipeline.run", side_effect=fake_pipeline_run):
                    out = asyncio.run(series.run_media_episodes("proj", spec))
            finally:
                series.config.PROJECTS_DIR = real
        return out, seen, gt

    def test_binds_and_gates_every_episode(self):
        out, seen, gt = self._run("1-3", ["ok", "ok", "ok"])
        self.assertEqual([e for e, _ in seen], [1, 2, 3], "必须按集号升序、串行")
        # 每次进 pipeline 之前，manifest 的集号必须**已经**是本集
        for ep, live in seen:
            self.assertEqual(ep, live, "第 %d 集跑时 manifest 的 episode_index 不是 %d "
                                       "→ 角色会写到别的集" % (ep, ep))
        self.assertEqual(gt.call_count, 3, "每集都要过三道输入门")
        self.assertEqual(out["status"], "ok")
        self.assertEqual([r["ep"] for r in out["results"]], [1, 2, 3])

    def test_failure_stops_the_loop(self):
        """★ 任何一集被门拦下就**停止** —— 静默跳过会让「出了 1 集」看起来像「出了 2 集」。"""
        out, seen, _gt = self._run("1-3", ["blocked", "ok", "ok"])
        self.assertEqual([e for e, _ in seen], [1], "第 1 集就失败，不该继续跑第 2 集")
        self.assertEqual(out["status"], "incomplete")

    def test_single_episode_spec(self):
        out, seen, _gt = self._run("4", ["ok"])
        self.assertEqual([e for e, _ in seen], [4])

    def test_cli_requires_resume_flag(self):
        """`--episodes` 必须配合 `--resume-media`：否则就是开了媒体链的第二个入口。"""
        import io as _io
        import contextlib
        real_argv = sys.argv
        sys.argv = ["series", "somerepo", "--episodes", "1-2"]
        try:
            from v5 import series
            buf = _io.StringIO()
            with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
                series.main()
            self.assertIn("--resume-media", str(cm.exception))
        finally:
            sys.argv = real_argv


class TestSerialBriefSemantics(unittest.TestCase):
    """★ M5：连载下 `brief.must_have` 的语义变了，**不改就每集失败**。

    FIDELITY 门校验的是「**这一集**的分镜有没有覆盖 must_have」。所以多集下
    must_have 必须是"每集都能覆盖的硬要求"，而不是全剧四幕 —— 否则**每一集**都被判
    「未覆盖 must_have」，而且报错文字会把人引向「分镜写错了」这个**错误方向**。
    """

    def _gate(self, data):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            from v5.series import _brief_gate
            _brief_gate(data)
        return buf.getvalue()

    def _brief(self, **kw):
        d = {"topic": "测试", "pack": "shortdrama", "genre": "剧情",
             "episodes": 1, "target_duration": "约 120 秒",
             "protagonist": {"name": "甲", "desc": "主角"},
             "must_have": ["开场即冲突"], "key_props": ["钥匙"],
             "禁忌": "无", "tone": "温和", "结局": "和解"}
        d.update(kw)
        return d

    def test_single_episode_prints_nothing_about_serial(self):
        out = self._gate(self._brief())
        self.assertNotIn("连载模式", out, "单集项目不该被这条打扰")

    def test_serial_explains_must_have_semantics(self):
        out = self._gate(self._brief(episodes=4))
        self.assertIn("连载模式", out)
        self.assertIn("每集都能覆盖", out)

    def test_serial_warns_on_whole_drama_wording(self):
        out = self._gate(self._brief(episodes=4, must_have=["第 1 集相遇",
                                                           "大结局：两人和好"]))
        self.assertIn("全剧级", out, "出现「大结局」必须告警（它会每集都判不覆盖）")


class TestInputGatesReallyRun(unittest.TestCase):
    """★ 真机 08:34 抓到的 bug：**三道输入门从来没被任何测试真正调用过**。

    `_storyboard_gate` / `_assets_gate` 在 M1 改成用 `guards.resolve_path(...)`
    之后**没导入 `guards`** ⇒ `--resume-media` 的自检直接 `NameError`。
    真实症状极其误导：日志只有「!! 输入门未过 → 不启动媒体链」，Traceback 埋在
    `gates.log` 里；而 `--chain-only` 不走门预检 ⇒ 之前的 `--chain-only` 验收
    **根本覆盖不到这条路径**。

    ⇒ 本测试只守一条：**门里不许出现"未导入的名字"**。
    门按判据拒绝（SystemExit）是合法的，不算失败。
    """

    SB = ("# 分镜\n\n| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 场景 | 画面描述 | 对白 | 音效 |\n"
          "|---|---|---|---|---|---|---|---|---|\n"
          "| 1 | 全景 | 平视 | 固定 | 6 | 甲地 | @阿甲在空荡的铺子里看着一只空钱盒发呆 | 阿甲：这单我接了。 | 环境音 |\n")

    def _proj(self, ep: int):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "brief.json").write_text(json.dumps({
            "topic": "门自检", "pack": "shortdrama", "genre": "剧情", "episodes": 1,
            "target_duration": "约 6 秒", "protagonist": "阿甲",
            "must_have": ["开场即冲突"], "key_props": ["钱盒"],
            "禁忌": "无", "tone": "克制", "结局": "和解",
        }, ensure_ascii=False), encoding="utf-8")
        p = d / guards.out_path("scenedesigner", ep)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.SB, encoding="utf-8")
        return d

    def test_gates_have_no_undefined_names(self):
        import contextlib
        from v5 import series
        for ep in (1, 2):
            root = self._proj(ep)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    series._input_gates(root, None, ep=ep)
            except NameError as e:
                self.fail("第 %d 集：门里用了**未导入的名字** —— %s" % (ep, e))
            except SystemExit as e:
                # 门按判据拒绝是合法的；但不能是空的理由
                self.assertTrue(str(e).strip(), "门拒绝时必须给出理由")

    def test_gates_are_never_silent(self):
        """反向：门**不许静默通过** —— 必须要么打出判定依据、要么给出拒绝理由。

        为什么不写成「这个样本必然被拒」：那会把测试绑死在 `FIDELITY_THRESHOLD` /
        台词长度阈值上，阈值一调测试就假挂。这里只锁**"不静默"**这一条 ——
        而"静默跳过"正是本仓库反复付学费的那类 bug。
        """
        import contextlib
        from v5 import series
        root = self._proj(1)
        buf = io.StringIO()
        verdict = None
        try:
            with contextlib.redirect_stdout(buf):
                series._input_gates(root, None, ep=1)
            verdict = buf.getvalue().strip() or None
        except SystemExit as e:
            verdict = str(e).strip() or None
        except NameError as e:            # noqa: PERF203
            self.fail("门里用了**未导入的名字** —— %s" % e)
        self.assertTrue(verdict, "三道门一行输出、一个判定都没有 ⇒ 它根本没在跑")


class TestResumeMediaHonoursEp(unittest.TestCase):
    """★★ 真机事故（2026-09-17，三集首跑）：`--resume-media --ep N` **没有把 ep 传给
    `pipeline.run`** ⇒ 每一集都渲进 `media/ep1/`，后一集**整个覆盖前一集的媒体目录**
    （实测：ep2 的 run 把 ep1 的 `stills.json` 与 9 张静帧重画、并重拼了成片）。

    为什么没被发现：`--resume-media` 分支上我给 `_input_gates` 加了 `ep=ep`、
    **却漏了紧邻的 `pipeline.run`** —— 与 `_storyboard_gate` 漏导入 `guards` 同型：
    **主路径没有测试盯着，只靠"我改的时候记得"是守不住的。**
    """

    def _proj(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "brief.json").write_text(json.dumps({
            "topic": "ep 透传", "pack": "shortdrama", "genre": "剧情", "episodes": 3,
            "target_duration": "约 6 秒", "protagonist": "阿甲",
            "must_have": ["开场即冲突"], "key_props": ["钱盒"],
            "禁忌": "无", "tone": "克制", "结局": "和解"}), encoding="utf-8")
        return d

    def _run(self, ep):
        """跑一次 `--resume-media --ep ep`，返回 pipeline.run 收到的 kwargs。"""
        import asyncio
        from v5 import series

        seen = {}

        def fake_pipeline_run(root, **kw):
            seen.update(kw)
            seen["_root"] = str(root)
            # 用 str 而不是 %d：漏传 ep 时 kw["ep"] 是 None，%d 会抛 TypeError
            # ——那样测试虽然也会失败，但报错信息看不出"漏传 ep"这件事。
            return {"status": "ok", "final": "media/ep%s/episode_final.mp4" % kw.get("ep")}

        d = self._proj()
        real = series.config.PROJECTS_DIR
        series.config.PROJECTS_DIR = d
        old_argv = sys.argv
        sys.argv = ["series", "proj", "--resume-media", "--ep", str(ep)]
        # 用 ALLOW_RESUME（`_resume_allowed` 在**调用时**读 env；
        # 而 OPEN_CHAIN 是模块导入时读进 config 的，测试里改 env 无效）
        os.environ["SHORTDRAMA_ALLOW_RESUME"] = "1"
        try:
            with mock.patch("v5.media.pipeline.run", side_effect=fake_pipeline_run), \
                    mock.patch.object(series, "_input_gates"), \
                    contextlib.redirect_stdout(io.StringIO()):
                asyncio.run(series.run("proj", ep=ep, resume_media=True))
        finally:
            series.config.PROJECTS_DIR = real
            sys.argv = old_argv
            os.environ.pop("SHORTDRAMA_ALLOW_RESUME", None)
        return seen

    def test_ep_is_forwarded_to_pipeline(self):
        for ep in (1, 2, 3):
            seen = self._run(ep)
            self.assertEqual(seen.get("ep"), ep,
                             "第 %d 集：pipeline.run 收到的 ep 是 %r ⇒ 会渲进别的集的目录"
                             % (ep, seen.get("ep")))

    def test_ep_is_not_hardcoded_to_one(self):
        """反向：第 2 集绝不能拿到 ep=1（那就是"后一集覆盖前一集"的事故形态）。"""
        seen = self._run(2)
        self.assertNotEqual(seen.get("ep"), 1)


class TestPackMode(unittest.TestCase):
    """pack 档（2026-09-22，12s 打包法落管线）：分组 / 提交 / 展开 / 拼接识别。

    判据来源：旁路脚本 `scripts/pack_render.py` 三项目实测闭环（37 次提交零拒绝、
    捕梦师 213s 成片）。管线版与脚本版**分组与 prompt 必须逐字节一致**——
    这是"同一判据绝不写两份"的验收底线（实测对账见 git 提交记录）。
    """

    # ── 分组 ──

    def test_group_shots_merges_same_scene_and_clamps(self):
        from v5.media import video_plan

        shots = [
            {"name": "LN01", "scene": "画室", "seconds": 6},
            {"name": "LN02", "scene": "画室", "seconds": 5},
            {"name": "LN03", "scene": "夜街", "seconds": 6},   # 跨场景 → 断开
            {"name": "LN04", "scene": "夜街", "seconds": 99},  # 钳到 12；6+12=18
        ]
        groups = video_plan.group_shots(shots, 5)
        # 18s 超限 → 等比压缩 4+8（两镜都 ≥60% 原声明）→ 仍合并（与旁路脚本一致）
        self.assertEqual([["LN01", "LN02"], ["LN03", "LN04"]],
                         [[s["name"] for s in g] for g, _ in groups])
        self.assertEqual(groups[1][1], [4, 8])
        # 合计 ≤12s 硬上限
        for _, declared in groups:
            self.assertLessEqual(sum(declared), 12)

    def test_group_shots_compression_respects_speech_floor(self):
        from v5.media import video_plan

        # 6+6=12 恰好放得下；6+7=13 → 压缩，但每镜不得低于台词下限
        shots = [{"name": "LN01", "scene": "画室", "seconds": 7,
                  "dialogue": "一二三四五六七八九十" * 2},   # 20 字 → 需要 ≥5s
                 {"name": "LN02", "scene": "画室", "seconds": 7}]
        groups = video_plan.group_shots(shots, 5)
        self.assertEqual(len(groups), 1, "13s 压缩后仍应合并")
        declared = groups[0][1]
        self.assertEqual(sum(declared), 12)
        self.assertGreaterEqual(min(declared), 5, "台词镜不得压破语音下限")

    def test_group_shots_two_second_shots_and_single_floor(self):
        """★ 2026-09-25 放权：每拍下限 4→2s（Pavo 参考片实证 12s 六拍可行）。

        组内 2s 镜合法（凑成 ≥4s 的请求即可）；**单镜成组**必须补到 ≥4s——
        供应商 seconds ∈ [4,12] 管的是整条请求，独立成组的 2s 镜会直接被拒。
        """
        from v5.media import video_plan

        shots = [
            {"name": "LN01", "scene": "画室", "seconds": 6},
            {"name": "LN02", "scene": "画室", "seconds": 2},   # 组内 2s 合法
            {"name": "LN03", "scene": "夜街", "seconds": 2},   # 跨场景独立成组 → 补 4
        ]
        groups = video_plan.group_shots(shots, 5)
        self.assertEqual([[s["name"] for s in g] for g, _ in groups],
                         [["LN01", "LN02"], ["LN03"]])
        self.assertEqual(groups[0][1], [6, 2])
        self.assertEqual(groups[1][1], [4], "单镜成组必须补到供应商请求下限")
        for _, declared in groups:
            self.assertGreaterEqual(sum(declared), 4)
            self.assertLessEqual(sum(declared), 12)

    def test_pack_prompt_remaps_beats_to_global_timeline(self):
        """★ 2026-09-25：镜内节拍时间戳必须平移到 pack 全局时间轴。

        组级声明「<Picture i> 为第 X-Y 秒」是全局的；镜内 `0-2秒：` 是镜本地 0 起。
        组内第 2 镜不平移会同时收到「第 6-8 秒」边界和「0-2秒」正文——互相打架。
        压缩过的镜（_pack_fit 等比压秒）节拍也要跟着等比重标。
        """
        from v5.media.prompt import build_pack_prompt

        group = [
            {"name": "LN01", "scene": "画室", "seconds": 6, "shot_type": "中景",
             "angle": "平视", "camera": "固定",
             "visual": "0-2秒：@陈默抬手按住@画纸；2-6秒：右手把@画笔搁下。",
             "dialogue": "", "sfx": "", "tail": ""},
            {"name": "LN02", "scene": "画室", "seconds": 2, "shot_type": "近景",
             "angle": "平视", "camera": "固定",
             "visual": "0-2秒：@画笔在笔架上停稳。",
             "dialogue": "", "sfx": "", "tail": ""},
        ]
        p = build_pack_prompt(group, [6, 2], 8, style_block="")
        self.assertIn("0-2秒：@陈默抬手按住@画纸；2-6秒：右手把@画笔搁下", p)
        self.assertIn("6-8秒：@画笔在笔架上停稳", p,
                      "组内第 2 镜的节拍必须平移到全局时间轴")
        self.assertNotIn("第 6-8 秒｜画室｜近景】\n0-2秒", p,
                         "组级边界与镜内节拍时间戳不一致 = 打架")
        # 压缩重标：4s 节拍压进 2s 分配
        p2 = build_pack_prompt([dict(group[0], seconds=4)], [2], 2, style_block="")
        self.assertIn("0-1秒：@陈默抬手按住@画纸；1-2秒：右手把@画笔搁下", p2)

    def test_still_prompt_takes_last_beat_only(self):
        """★ 2026-09-25：静帧只取最后一拍——多拍序列是「时间性描述」，
        会诱发分屏（clockmaker 事故同类：18 镜 14 镜上下两格）。"""
        from v5.media import prompt

        shot = {"name": "LN01", "scene": "画室", "seconds": 6,
                "shot_type": "中景", "angle": "平视", "camera": "固定",
                "visual": ("0-2秒：@陈默抬手按住@画纸；"
                           "2-6秒：右手把@画笔搁下，指尖停在笔架上。"),
                "dialogue": "", "sfx": "", "tail": ""}
        p = prompt.build_still_prompt(shot)
        self.assertIn("指尖停在笔架上", p)
        self.assertNotIn("0-2秒", p, "静帧不得携带节拍时间戳（分屏诱因）")
        self.assertNotIn("抬手按住", p, "前一拍不得进静帧（只取最后一拍）")

    # ── 提交（pack 粒度）──

    def test_submit_packs_one_job_per_group(self):
        from unittest import mock

        from v5.media import jobs as jobs_mod, providers, video

        shots = [{"name": "LN01", "scene": "画室", "seconds": 5},
                 {"name": "LN02", "scene": "画室", "seconds": 5},
                 {"name": "LN03", "scene": "夜街", "seconds": 5}]
        planned = [{"name": s["name"], "frame_plan": {}} for s in shots]
        st = {s["name"]: {"url": "u"} for s in shots}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(video.config, "VIDEO_MODE", "pack"), \
                    mock.patch.object(providers, "submit_video",
                                      return_value={"video_id": "v1"}) as sub:
                jobs = video.submit_packs(root, shots, st, planned, ep=1,
                                          log=lambda *_: None)
            self.assertEqual(sub.call_count, 2, "同场景 2 镜打包 + 独立 1 组 = 2 次提交")
            ep_dir = root / "media" / "ep1"
            self.assertEqual(jobs["pack01"]["shots"], ["LN01", "LN02"])
            self.assertEqual(jobs["pack02"]["shots"], ["LN03"])
            self.assertEqual(jobs["pack01"]["state"], "submitted")
            self.assertEqual(sub.call_args_list[0].kwargs.get("seconds"), 10)

    def test_submit_packs_only_rerenders_containing_group(self):
        from unittest import mock

        from v5.media import jobs as jobs_mod, providers, video

        shots = [{"name": "LN01", "scene": "画室", "seconds": 5},
                 {"name": "LN02", "scene": "画室", "seconds": 5}]
        planned = [{"name": s["name"], "frame_plan": {}} for s in shots]
        st = {s["name"]: {"url": "u"} for s in shots}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ep_dir = root / "media" / "ep1"
            clip_dir = ep_dir / "clips"
            clip_dir.mkdir(parents=True)
            (clip_dir / "pack01.mp4").write_bytes(b"OLD")
            jobs_mod.save(ep_dir, {"pack01": {"state": "completed",
                                              "shots": ["LN01", "LN02"]}})
            with mock.patch.object(video.config, "VIDEO_MODE", "pack"), \
                    mock.patch.object(providers, "submit_video",
                                      return_value={"video_id": "v2"}) as sub:
                video.submit_packs(root, shots, st, planned, ep=1,
                                   log=lambda *_: None, only=["LN02"])
            self.assertEqual(sub.call_count, 1, "only 镜所在组整组重渲")
            self.assertFalse((clip_dir / "pack01.mp4").exists(),
                             "重渲前必须作废旧组产物")

    def test_expand_packs_maps_shots_to_group_clip(self):
        from v5.media import video

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ep_dir = root / "media" / "ep1"
            ep_dir.mkdir(parents=True)
            (ep_dir / "video_jobs.json").write_text(json.dumps({
                "pack01": {"state": "completed", "shots": ["LN01", "LN02"]},
                "LN09": {"state": "completed"},   # 旧逐镜记录：不得透传
            }, ensure_ascii=False), encoding="utf-8")
            out = video.expand_packs(root, 1, {"pack01": "/x/pack01.mp4",
                                               "LN09": "/x/LN09.mp4"},
                                     log=lambda *_: None)
            self.assertEqual(out, {"LN01": "/x/pack01.mp4", "LN02": "/x/pack01.mp4"})

    # ── 拼接识别 ──

    def test_concat_glob_prefers_pack_clips(self):
        from unittest import mock

        from v5.media import compose

        with tempfile.TemporaryDirectory() as d:
            clip_dir = Path(d)
            # 真 mp4 片段（假字节过不了 ffmpeg；0.5s 纯色片生成成本毫秒级）
            for nm, c in (("pack01", "red"), ("pack02", "blue")):
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                     "-i", "color=c=%s:s=64x64:d=0.5" % c,
                     "-c:v", "libx264", "-preset", "ultrafast",
                     str(clip_dir / (nm + ".mp4"))],
                    capture_output=True, timeout=60)
            seen = (sorted(clip_dir.glob("pack*.mp4"), key=lambda p: p.stem)
                    or sorted(clip_dir.glob("LN*.mp4"), key=lambda p: p.stem))
            self.assertEqual([p.name for p in seen], ["pack01.mp4", "pack02.mp4"],
                             "pack 产物按组序拼接；compose.concat 的 glob 选择逻辑同源")
            # 真函数走一遍（TRIM/XFADE 关掉走 copy 快路径，隔离编码耗时）
            with mock.patch.object(compose, "TRIM", 0.0), \
                    mock.patch.object(compose, "XFADE", 0.0):
                self.assertEqual(compose.concat(clip_dir, clip_dir / "out.mp4"), 2,
                                 "pack 模式的 clips/ 必须能被 concat 识别并拼接")
            self.assertTrue((clip_dir / "out.mp4").exists())


if __name__ == "__main__":
    unittest.main()
