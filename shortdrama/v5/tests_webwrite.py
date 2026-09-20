# -*- coding: utf-8 -*-
"""`v5/media/runner.py` + `v5/webwrite.py` 自测。

**不烧任何配额**：`runner.start()` 里的 `subprocess.Popen` 被替换成假对象
（只记参数、不真起进程），所以不会真的跑媒体链。

覆盖的重点是**护栏与失效联动**——这两件事错了会烧钱或丢数据：
  · `only` 为空必须**拒绝**（空 = 整片渲染，小时级 + 大量配额）
  · 镜号必须真在分镜里（在烧配额**之前**拦下）
  · 改一镜必须**显式作废**它的静帧与成片，且返回里要说清作了什么废
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v5 import config, webwrite  # noqa: E402
from v5.media import jobs as jobs_mod, runner  # noqa: E402

SB_MD = """# 分镜：纸扎铺

## 第1幕｜纸扎铺-日｜S1 / 6s

| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 | 场景 |
|--------|------|------|------|---------|---------|------|------|------|
| 1 | 全景 | 平视 | 固定 | 6 | 纸扎匠在逼仄的铺子里看着空钱盒，@纸扎匠 的指尖刚触到盒沿又停住，没有动 | 纸扎匠：这单我接了。 | 环境音 | 纸扎铺 |
| 2 | 近景 | 俯视 | 缓推 | 4 | @纸扎匠 的手指挪到免提手机边缘，屏幕亮着，富人的声音从听筒传出来 | 富人：钱给你十倍。 | 手机电流声 | 纸扎铺 |
"""


#: 一份**真的能过分镜契约门**的分镜（8 列齐 + 台词成句 + must_have 覆盖得上）。
#: 用来说明"通过的判决也覆盖写" —— 它配套 `brief.must_have = ["纸扎匠看着空钱盒"]`。
MD_OK = """# 分镜：纸扎铺

| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |
|--------|------|------|------|---------|---------|------|------|
| 1 | 全景 | 平视 | 固定 | 6 | 纸扎匠在逼仄的铺子里看着空钱盒 | 纸扎匠：这单我接下来就去准备材料。 | 环境音 |
| 2 | 近景 | 俯视 | 缓推 | 6 | 纸扎匠的手指挪到免提手机边缘 | 富人：钱给你十倍你先别声张。 | 电流声 |
"""


class _FakePopen:
    """假 Popen：只记录，不真起进程。"""

    calls = []

    def __init__(self, argv, **kw):
        _FakePopen.calls.append({"argv": argv, "kw": kw})
        self.pid = 424242

    def wait(self, *a, **kw):
        return 0


class _Base(unittest.TestCase):
    def setUp(self):
        _FakePopen.calls = []
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._p = mock.patch.object(config, "PROJECTS_DIR", self.root)
        self._pr = mock.patch.object(config, "PROJECT_ROOT", self.root)
        self._p.start()
        self._pr.start()
        self.pid = "demo-drama"
        self.proj = self.root / self.pid
        (self.proj / "scenedesigner").mkdir(parents=True)
        (self.proj / "images").mkdir()
        (self.proj / "media" / "ep1" / "clips").mkdir(parents=True)
        (self.proj / "media" / "ep1" / "stills").mkdir(parents=True)
        (self.proj / "brief.json").write_text(json.dumps(
            {"topic": "纸扎铺", "pack": "shortdrama", "genre": "恐怖悬疑", "episodes": 1,
             "must_have": ["接单"], "key_props": [], "禁忌": [], "tone": "冷",
             "结局": "定格", "protagonist": "纸扎匠"}, ensure_ascii=False), encoding="utf-8")
        (self.proj / "assets.json").write_text(json.dumps({"assets": [
            {"id": "a1", "name": "纸扎匠", "type": "character", "priority": 9,
             "identity": "深蓝棉袄", "ref_image": "纸扎匠.png"},
            {"id": "a2", "name": "纸扎铺", "type": "location", "priority": 8}]},
            ensure_ascii=False), encoding="utf-8")
        (self.proj / "scenedesigner" / "scenedesigner.md").write_text(SB_MD, encoding="utf-8")

    def tearDown(self):
        self._pr.stop()
        self._p.stop()
        self.tmp.cleanup()

    def seed_still_and_clip(self, shot="LN01"):
        sd = self.proj / "media" / "ep1" / "stills"
        (sd / (shot + ".jpg")).write_bytes(b"\xff\xd8")
        (sd / (shot + ".jpg.url")).write_text("https://x/1.jpg", encoding="utf-8")
        (sd.parent / "stills.json").write_text(json.dumps(
            {shot: {"path": str(sd / (shot + ".jpg")), "url": "https://x/1.jpg",
                    "seconds": 6, "prompt": "p"}}, ensure_ascii=False), encoding="utf-8")
        cd = self.proj / "media" / "ep1" / "clips"
        (cd / (shot + ".mp4")).write_bytes(b"\x00" * 32)
        out = self.proj / "media" / "ep1"
        jb = jobs_mod.load(out)
        jobs_mod.mark(jb, shot, "completed", local=str(cd / (shot + ".mp4")))
        jobs_mod.save(out, jb)


# ════════════════════════════════ runner：护栏

class TestRunnerGuards(_Base):
    def test_rejects_empty_shots(self):
        """★★ **最重要的一条**：空 shots = 整片渲染（小时级 + 大量配额）→ 必须拒。"""
        for kind in ("keyframe", "video"):
            with self.assertRaises(ValueError, msg=kind) as c:
                runner.start(self.pid, kind, shots=[], log=lambda *_: None)
            self.assertIn("整片渲染", str(c.exception))

    def test_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            runner.start(self.pid, "nonsense", shots=["LN01"], log=lambda *_: None)

    def test_rejects_unknown_shot(self):
        """镜号不在分镜里 → 在**烧配额之前**拦下。"""
        with self.assertRaises(ValueError) as c:
            runner.start(self.pid, "keyframe", shots=["LN99"], log=lambda *_: None)
        self.assertIn("LN99", str(c.exception))

    def test_rejects_missing_project(self):
        with self.assertRaises(ValueError):
            runner.start("no-such", "keyframe", shots=["LN01"], log=lambda *_: None)

    def test_assets_kind_ignores_shots(self):
        with mock.patch.object(runner.subprocess, "Popen", _FakePopen):
            rec = runner.start(self.pid, "assets", shots=["LN01"], log=lambda *_: None)
        self.assertEqual(rec["shots"], [], "assets 任务不吃 shots")

    def test_episode_kind_is_the_only_shotless_one(self):
        """★ D（2026-09-19）：`episode`（整片出片）**不许**再被空 shots 护栏拦。

        护栏拒绝的是"**参数漏传**导致的整片渲染"；"人显式要求整片出片"是另一回事。
        两者必须能用**类型**区分 —— 这条同时锁住"护栏没被这次改动松掉"。
        """
        with mock.patch.object(runner.subprocess, "Popen", _FakePopen):
            rec = runner.start(self.pid, "episode", shots=[], log=lambda *_: None)
        self.assertEqual(rec["kind"], "episode")
        self.assertEqual(rec["shots"], [])
        with self.assertRaises(ValueError, msg="keyframe/video 的空 shots 仍须拒绝"):
            runner.start(self.pid, "video", shots=[], log=lambda *_: None)


class TestEpisodeExec(_Base):
    """`_exec_episode`（D）：整片出片的子进程实现。"""

    def test_calls_pipeline_without_only(self):
        """**不带 `only`** = 整片（静帧→视频→拼接）；带了就只渲那几镜。"""
        from v5.media import pipeline
        seen = {}

        def fake(root, **kw):
            seen.update(kw)
            return {"status": "ok"}

        with mock.patch.object(pipeline, "run", fake), \
             mock.patch.object(runner, "_storyboard_warnings", lambda *a, **k: ["w"]):
            res = runner._exec_episode(self.proj, 2, [], lambda *_: None)
        self.assertNotIn("only", seen, "整片出片不能带 only")
        self.assertEqual(seen.get("ep"), 2)
        self.assertEqual(res["warnings"], ["w"], "判决要随结果回到前端")


class TestScriptStageExec(_Base):
    """`_exec_script`（2026-09-19 两段式的第一段）：创作链**只跑到剧本正文**。

    前端「确认简介 → 生成剧本内容」用它。两条判据最容易错，都锁在这里：
      ① `--until scriptwriter` 必须真的传到命令行；
      ② **下游角色缺产物不算失败**（正文是链的第 4 步，后面 3 个还没跑）。
    反向也锁：完整链（`_exec_chain`）仍然要求 7 个角色齐全 —— 别把宽松传染过去。
    """

    ROLES_TO_HERE = ("worldbuilder", "assetdesigner", "plotdesigner", "scriptwriter")

    def _prep(self, roles):
        from v5 import guards, webchain
        # `_run_chain` 会检查驱动脚本存在（真跑时是仓库里的 scripts/drive_chain.py）
        d = self.root / "scripts" / "drive_chain.py"
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text("# fake driver\n", encoding="utf-8")
        for r in roles:
            p = self.proj / guards.out_path(r, 1)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# %s\n" % r, encoding="utf-8")
        return webchain

    def _run(self, kind, roles):
        from v5.media import runner as R
        webchain = self._prep(roles)
        calls = {}

        def fake_run(cmd, cwd=None, env=None):
            calls["cmd"] = list(cmd)
            calls["env"] = dict(env or {})
            return mock.Mock(returncode=0)

        with mock.patch.object(webchain, "devserver_status",
                               lambda: {"alive": True, "ok": True}), \
             mock.patch.object(R.subprocess, "run", fake_run):
            res = R._EXEC[kind](self.proj, 1, [], lambda *_: None)
        return res, calls

    def test_script_stage_passes_until_to_driver(self):
        res, calls = self._run("script", self.ROLES_TO_HERE)
        self.assertIn("--until", calls["cmd"], "没传 --until ⇒ 会一路跑到 reviewer（白烧）")
        self.assertEqual(calls["cmd"][calls["cmd"].index("--until") + 1], "scriptwriter")
        self.assertIn("--ep", calls["cmd"], "集号仍然必须传（跨集 bug 的教训）")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["until"], "scriptwriter")

    def test_script_stage_tolerates_missing_downstream(self):
        """★ 核心：只到正文 ⇒ 下游（dialogue/scenedesigner/reviewer）不在盘上也**不算失败**。"""
        res, _ = self._run("script", self.ROLES_TO_HERE)
        self.assertEqual(res["status"], "ok",
                         "按 7 个角色核对会把一次正常的阶段产出判成 failed")
        self.assertEqual(res["warnings"], [], "还没有分镜 ⇒ 不该去算分镜契约判决")

    def test_chain_stage_still_needs_all_seven(self):
        """反向：完整链的判据**不许**被上面的宽松改掉。"""
        res, calls = self._run("chain", self.ROLES_TO_HERE)
        self.assertNotIn("--until", calls["cmd"])
        self.assertEqual(res["status"], "failed")
        self.assertIn("reviewer", res["missing_roles"])


class TestStoryboardGateHumanMode(_Base):
    """★ C（2026-09-19）人工模式：**分镜契约门降级为警告**（判断权在人）。

    两端都要防：
      · 人工模式 —— 硬伤只出报告、**不阻断**（`SystemExit` 不许发生）；
      · 默认（全自动 / CLI）—— 照旧 `SystemExit` 硬拦，行为逐字节不变。
    并锁住"判决**必须落盘**"：不落盘的话，「降级为警告」在界面上等于「门消失了」。
    """

    #: 缺列（画面描述 / 对白 / 运镜 / 时长 / 角度）—— 确定性硬伤，不依赖 brief
    BROKEN = "| 镜头号 | 景别 |\n|---|---|\n| 1 | 全景 |\n"

    def _break_it(self):
        (self.proj / "scenedesigner" / "scenedesigner.md").write_text(
            self.BROKEN, encoding="utf-8")

    def test_human_mode_warns_not_blocks(self):
        from v5 import guards, series
        self._break_it()
        with mock.patch.object(config, "HUMAN_IN_CHARGE", True):
            series._storyboard_gate(self.proj, None, ep=1)      # ← 不许抛
        rep = guards.gate_report(self.proj, 1)
        self.assertTrue(rep, "判决必须落盘（否则「降级为警告」= 「删掉门」）")
        self.assertFalse(rep["blocked"], "人工模式：放行")
        self.assertEqual(rep["ep"], 1)
        self.assertTrue(any("缺列" in f for f in rep["fatal"]), rep)

    def test_default_mode_still_blocks(self):
        from v5 import guards, series
        self._break_it()
        with self.assertRaises(SystemExit) as c:
            series._storyboard_gate(self.proj, None, ep=1)
        self.assertIn("STORYBOARD-REJECT", str(c.exception))
        self.assertTrue(guards.gate_report(self.proj, 1)["blocked"],
                        "拦下了也要留证据（否则事后查不出是为什么没渲）")

    def test_clean_storyboard_overwrites_stale_report(self):
        """干净通过也要**覆盖写** —— 否则上一轮的红字会一直挂在页面上。"""
        from v5 import guards, series
        # 先制造一份"带红字"的报告
        self._break_it()
        with mock.patch.object(config, "HUMAN_IN_CHARGE", True):
            series._storyboard_gate(self.proj, None, ep=1)
        self.assertTrue(guards.gate_report(self.proj, 1)["fatal"])
        # 换成一份**真的能过门**的分镜（8 列齐 + 台词成句 + must_have 覆盖得上）
        (self.proj / "brief.json").write_text(json.dumps(
            {"topic": "纸扎铺", "pack": "shortdrama", "genre": "恐怖悬疑",
             "episodes": 1, "must_have": ["纸扎匠看着空钱盒"], "key_props": [],
             "禁忌": [], "tone": "冷", "结局": "定格", "protagonist": "纸扎匠"},
            ensure_ascii=False), encoding="utf-8")
        (self.proj / "scenedesigner" / "scenedesigner.md").write_text(
            MD_OK, encoding="utf-8")
        series._storyboard_gate(self.proj, None, ep=1)
        rep = guards.gate_report(self.proj, 1)
        self.assertEqual(rep["fatal"], [], "这一份应当干净通过")
        self.assertFalse(rep["blocked"])

    def test_gate_report_write_failure_keeps_the_verdict(self):
        """★★ 2026-09-19：**判决落盘失败，不许把判决本身吞掉**。

        `record_gate_report` 挂在**所有路径**（含 CLI / 外部 agent）的门上，而且是在
        `raise SystemExit("[STORYBOARD-REJECT] …")` **之前**调的。
        早先它直接让 `write_text` 抛（目录只读 / 磁盘满）⇒ 用户看到的是 Python
        traceback，而不是那句**最该看到**的 `[STORYBOARD-REJECT] …` 诊断。
        """
        from v5 import guards, series
        self._break_it()
        with mock.patch.object(config, "HUMAN_IN_CHARGE", False), \
             mock.patch.object(Path, "write_text",
                               side_effect=OSError("只读文件系统")):
            with self.assertRaises(SystemExit) as c:
                series.storyboard_gate(self.proj, None, ep=1)
        self.assertIn("[STORYBOARD-REJECT]", str(c.exception),
                      "落盘失败也不能换掉真正的诊断")

    def test_runner_reports_but_never_crashes_subprocess(self):
        """`_storyboard_warnings` 是"顺手出报告"，不是准入点。

        默认模式下门会 `SystemExit`；子进程**不许**因此崩（真正的准入点
        在 `pipeline.run` 的 `media_gate` 里），但判决必须带回来给前端。
        """
        self._break_it()
        logs = []
        with mock.patch.object(config, "HUMAN_IN_CHARGE", False):
            out = runner._storyboard_warnings(self.proj, 1, logs.append)
        self.assertTrue(out, "拦下了也要把判决带回来（前端要看得见）")
        self.assertTrue(any("STORYBOARD-REJECT" in x for x in logs), logs)


class TestRunnerLedger(_Base):
    def _start(self, kind="keyframe", shots=("LN01",)):
        with mock.patch.object(runner.subprocess, "Popen", _FakePopen):
            return runner.start(self.pid, kind, shots=list(shots), log=lambda *_: None)

    def test_start_spawns_module_and_writes_ledger(self):
        rec = self._start()
        self.assertEqual(rec["status"], "running")
        self.assertEqual(rec["os_pid"], 424242)
        argv = _FakePopen.calls[-1]["argv"]
        self.assertEqual(argv[1:3], ["-m", "v5.media.runner"])
        self.assertIn(rec["run_id"], argv)
        # 台账落盘
        led = json.loads(runner.ledger_path(rec["run_id"]).read_text(encoding="utf-8"))
        self.assertEqual(led["kind"], "keyframe")
        self.assertEqual(led["shots"], ["LN01"])
        # 子进程环境：PYTHONPATH 指向仓库根（否则子进程 import 不到 v5）
        env = _FakePopen.calls[-1]["kw"]["env"]
        self.assertIn(str(config.PROJECT_ROOT), env["PYTHONPATH"])

    def test_child_env_marks_human_in_charge(self):
        """★★ 2026-09-19：**前端路径 = 人工模式**，且只对它生效。

        `runner.start` 只被 `server.py`（前端）调用 ⇒ 在子进程 env 里设
        `SHORTDRAMA_HUMAN_IN_CHARGE=1` 就等于"只对前端生效"。
        两条都要锁：
          · 前端拉起的任务**必须**带上它（否则媒体门会拦下"评审未通过"的片子，
            而人工模式下判断权在人）；
          · 父进程（server / CLI / 外部 agent）的 `os.environ` **不许**被改
            —— 那会让全自动链路一起失去唯一的质量保护。
        """
        with mock.patch.object(runner.subprocess, "Popen", _FakePopen):
            with mock.patch.dict(runner.os.environ, {}, clear=False):
                runner.os.environ.pop("SHORTDRAMA_HUMAN_IN_CHARGE", None)
                runner.start(self.pid, "video", shots=["LN01"], log=lambda *_: None)
                self.assertNotIn(
                    "SHORTDRAMA_HUMAN_IN_CHARGE", runner.os.environ,
                    "开关只能跟着**路径**走（写进子进程 env），不能污染父进程")
        env = _FakePopen.calls[-1]["kw"]["env"]
        self.assertEqual(env.get("SHORTDRAMA_HUMAN_IN_CHARGE"), "1")

    def test_qc_autoheal_is_off_by_default_on_frontend_path(self):
        """★★ 2026-09-19：**质检自愈**（静帧判硬伤自动重画 / 成片抽帧复核自动重拍）
        在前端路径**默认关**。

        为什么默认关：那两条环路是"机器替人判断画面合不合格，并直接烧配额改"，
        与"判断权在人"最不一致的一处。人要它，就在前端打开开关（值随请求传下来）。
        ⛔ 必须**显式写 env**（不能只靠"不设"，因为 shim 进程可能从 `.env` 读到过 `=1`）。
        """
        with mock.patch.object(runner.subprocess, "Popen", _FakePopen):
            runner.start(self.pid, "video", shots=["LN01"], log=lambda *_: None)
        env = _FakePopen.calls[-1]["kw"]["env"]
        self.assertEqual(env.get("SHORTDRAMA_STILL_QC"), "0")
        self.assertEqual(env.get("SHORTDRAMA_CLIP_QC"), "0")

    def test_qc_switches_can_be_turned_on_and_string_zero_means_off(self):
        """打开 = 值随请求传下来；**字符串 `"0"` 必须读成"关"**（`"0"` 是真值）。"""
        with mock.patch.object(runner.subprocess, "Popen", _FakePopen):
            runner.start(self.pid, "video", shots=["LN01"],
                         still_qc=True, clip_qc="1", log=lambda *_: None)
        env = _FakePopen.calls[-1]["kw"]["env"]
        self.assertEqual(env.get("SHORTDRAMA_STILL_QC"), "1")
        self.assertEqual(env.get("SHORTDRAMA_CLIP_QC"), "1")

        with mock.patch.object(runner.subprocess, "Popen", _FakePopen):
            runner.start(self.pid, "video", shots=["LN01"],
                         still_qc="0", clip_qc=False, log=lambda *_: None)
        env = _FakePopen.calls[-1]["kw"]["env"]
        self.assertEqual(env.get("SHORTDRAMA_STILL_QC"), "0",
                         '字符串 "0" 在 Python 里是真值 —— 不许读成"开"')
        self.assertEqual(env.get("SHORTDRAMA_CLIP_QC"), "0")

    def test_log_file_created(self):
        rec = self._start()
        self.assertTrue(runner.log_path(rec["run_id"]).exists())
        self.assertIn("keyframe", runner.log_tail(rec["run_id"]))

    def test_run_id_unique_and_contains_pid(self):
        a = self._start()["run_id"]
        b = self._start()["run_id"]
        self.assertNotEqual(a, b)
        self.assertIn(self.pid, a)

    def test_reconcile_marks_lost_when_process_gone(self):
        """★ 台账说 running、进程却没了 → 必须判死。

        否则前端**永远转圈**（子进程被沙箱回收/被杀/崩溃都会走到这）。
        """
        rec = self._start()
        with mock.patch.object(runner, "_alive", return_value=False):
            got = runner.status(rec["run_id"])
        self.assertEqual(got["status"], "lost")
        self.assertIsNotNone(got["ended_at"])
        # 而且**落盘**了（下次读还是 lost）
        again = runner.read_ledger(rec["run_id"])
        self.assertEqual(again["status"], "lost")

    def test_reconcile_keeps_running_when_alive(self):
        rec = self._start()
        with mock.patch.object(runner, "_alive", return_value=True):
            self.assertEqual(runner.status(rec["run_id"])["status"], "running")

    def test_list_runs_filters_by_pid(self):
        self._start()
        self._start(kind="video")
        self.assertEqual(len(runner.list_runs(self.pid)), 2)
        self.assertEqual(runner.list_runs("other"), [])
        # 列表不带大结果
        self.assertNotIn("result", runner.list_runs(self.pid)[0])

    def test_cancel_marks_and_kills_tree(self):
        rec = self._start()
        killed = {}

        def fake_run(cmd, **kw):
            killed["cmd"] = cmd
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(runner.subprocess, "run", fake_run):
            got = runner.cancel(rec["run_id"])
        self.assertEqual(got["status"], "cancelled")
        # ★ 必须带 /T（媒体链会 spawn ffmpeg 等子进程，只杀父会留孤儿）
        self.assertIn("/T", killed["cmd"])
        self.assertIn(str(424242), killed["cmd"])

    def test_cancel_terminal_is_noop(self):
        rec = self._start()
        runner._write_ledger({**rec, "status": "ok"})
        with mock.patch.object(runner.subprocess, "run") as m:
            got = runner.cancel(rec["run_id"])
        self.assertEqual(got["status"], "ok")
        m.assert_not_called()

    def test_status_unknown_returns_none(self):
        self.assertIsNone(runner.status("nope"))


# ════════════════════════════════ webwrite

class TestRename(_Base):
    def test_rename_writes_brief_topic(self):
        r = webwrite.rename_project(self.proj, "新名字")
        self.assertEqual(r["topic"], "新名字")
        self.assertEqual(r["previous"], "纸扎铺")
        b = json.loads((self.proj / "brief.json").read_text(encoding="utf-8"))
        self.assertEqual(b["topic"], "新名字")

    def test_rename_rejects_empty_and_long(self):
        for bad in ("", "   ", "x" * 61):
            with self.assertRaises(webwrite.EditError):
                webwrite.rename_project(self.proj, bad)


class TestScript(_Base):
    def test_update_script_writes_and_flags_stale(self):
        r = webwrite.update_script(self.proj, 1, "正文" * 100)
        self.assertEqual(r["chars"], 200)
        self.assertTrue(r["stale"]["storyboard"], "剧本变了 → 分镜必须标记为过期")
        self.assertTrue((self.proj / "scriptwriter" / "scriptwriter_ep1.md").exists())
        self.assertIn("scenedesigner", (self.proj / "scriptwriter").parent.name
                      + "scenedesigner")

    def test_update_script_second_episode_path(self):
        webwrite.update_script(self.proj, 2, "第二集")
        self.assertTrue((self.proj / "scriptwriter" / "scriptwriter_ep2.md").exists())


class TestAssets(_Base):
    def test_sync_assets_picks_up_new_images(self):
        (self.proj / "images" / "新道具.png").write_bytes(b"\x89PNG")
        r = webwrite.sync_assets(self.proj)
        self.assertIn("新道具", r["added"])
        self.assertEqual(r["added_count"], 1)

    def test_sync_assets_skips_source_photos(self):
        """源照片是**输入**不是资产（`auto_sync` 既有口径，别破坏）。"""
        (self.proj / "images" / "老周.source.jpg").write_bytes(b"\xff\xd8")
        r = webwrite.sync_assets(self.proj)
        self.assertNotIn("老周.source", r["added"])

    def test_add_and_delete_asset(self):
        r = webwrite.add_asset(self.proj, "prop", "空钱盒")
        self.assertEqual(r["type"], "prop")
        reg = json.loads((self.proj / "assets.json").read_text(encoding="utf-8"))
        self.assertIn("空钱盒", [a["name"] for a in reg["assets"]])

        d = webwrite.delete_asset(self.proj, "prop", "空钱盒")
        self.assertEqual(d["removed"], ["空钱盒"])
        reg = json.loads((self.proj / "assets.json").read_text(encoding="utf-8"))
        self.assertNotIn("空钱盒", [a["name"] for a in reg["assets"]])
        # 图不删
        self.assertIn("images/ 里的图未删", d["note"])

    def test_add_asset_duplicate_rejected(self):
        with self.assertRaises(webwrite.EditError):
            webwrite.add_asset(self.proj, "character", "纸扎匠")

    def test_kind_aliases(self):
        self.assertEqual(webwrite._norm_kind("characters"), "character")
        self.assertEqual(webwrite._norm_kind("scene"), "location")
        self.assertEqual(webwrite._norm_kind("location"), "location")
        with self.assertRaises(webwrite.EditError):
            webwrite._norm_kind("weird")


class TestSegmentEdit(_Base):
    def test_update_segment_changes_right_cell(self):
        """只该动目标镜的那一格，**其余行原样**。"""
        webwrite.update_segment(self.proj, 1, "LN02", {"dialogue": "换了台词。"})
        md = (self.proj / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8")
        self.assertIn("换了台词。", md)
        # LN01 完全没动
        self.assertIn("这单我接了。", md)
        self.assertIn("指尖刚触到盒沿", md)
        # 表格结构未坏：列数与表头一致
        rows = [l for l in md.splitlines() if l.strip().startswith("|")]
        widths = {len(r.split("|")) for r in rows}
        self.assertEqual(len(widths), 1, "所有行的列数必须一致，实际 %s" % widths)

    def test_update_segment_only_touches_target(self):
        before = (self.proj / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8")
        webwrite.update_segment(self.proj, 1, "LN02", {"dialogue": "只改这一格。"})
        after = (self.proj / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8")
        b_lines = before.splitlines()
        a_lines = after.splitlines()
        self.assertEqual(len(b_lines), len(a_lines), "不该增删行")
        diff = [i for i, (x, y) in enumerate(zip(b_lines, a_lines)) if x != y]
        self.assertEqual(len(diff), 1, "只应有 1 行变化，实际 %d 行：%s" % (len(diff), diff))

    def test_header_found_even_when_visual_contains_huamian(self):
        """★★ 真实数据抓到的 bug：数据行的画面描述里**含「画面」二字**时，
        向上找表头会命中上一镜的数据行 → 报「该分镜表无此列」（而列其实在）。

        真实 `scenedesigner.md` 里"画面"是高频词，所以这不是边角情况。
        """
        md = (self.proj / "scenedesigner" / "scenedesigner.md")
        orig = md.read_text(encoding="utf-8")
        md.write_text(orig.replace("手指挪到免提手机", "画面里手指挪到免提手机"),
                      encoding="utf-8")          # LN02 自己的描述含「画面」
        r = webwrite.update_segment(self.proj, 1, "LN02", {"dialogue": "改后台词。"})
        self.assertIn("dialogue", r["applied"], "含「画面」的描述不该让找表头失败")
        new = md.read_text(encoding="utf-8")
        self.assertIn("改后台词。", new)
        # LN01 的台词仍在（没改错行）
        self.assertIn("这单我接了。", new)

    def test_header_detection_ignores_data_rows(self):
        """`_is_header_row` 必须把数据行判掉（判据与 `parse` 一致）。"""
        header = "| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |"
        data = "| 1 | 全景 | 平视 | 固定 | 6 | 画面里有个人在走动，足够十五个字以上 | 甲：台词。 | 环境音 |"
        sep = "|---|---|---|---|---|---|---|---|"
        self.assertTrue(webwrite._is_header_row(header))
        self.assertFalse(webwrite._is_header_row(data))
        self.assertFalse(webwrite._is_header_row(sep))
        self.assertFalse(webwrite._is_header_row(""))

    def test_add_segment_header_lookup_also_fixed(self):
        """`add_segment` 也走同一个表头查找 → 同样不能被含「画面」的数据行带偏。"""
        md = (self.proj / "scenedesigner" / "scenedesigner.md")
        md.write_text(md.read_text(encoding="utf-8").replace(
            "指尖刚触到盒沿", "画面里指尖刚触到盒沿"), encoding="utf-8")
        r = webwrite.add_segment(self.proj, 1, after="LN02",
                                 fields={"dialogue": "新镜台词。"})
        self.assertTrue(r["visible"])
        self.assertIn("新镜台词。", md.read_text(encoding="utf-8"))

    def test_update_segment_pipe_is_escaped(self):
        """写进单元格的 `|` 会破坏表格 → 必须替换掉。"""
        webwrite.update_segment(self.proj, 1, "LN01", {"visual": "前半|后半都够长的描述内容"})
        md = (self.proj / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8")
        self.assertNotIn("前半|后半", md)
        self.assertIn("前半／后半", md)

    def test_update_segment_reports_invalidated(self):
        """★ 改一镜必须**显式作废**静帧与成片，且返回里说清（绝不"改了却看着没变"）。"""
        self.seed_still_and_clip("LN01")
        r = webwrite.update_segment(self.proj, 1, "LN01", {"visual": "换了个完全不同的画面描述内容"})
        self.assertTrue(r["invalidated"]["still"], "静帧应被作废")
        self.assertTrue(r["invalidated"]["clip"], "成片应被暂存")
        self.assertTrue(r["invalidated"]["job"], "任务状态应回 pending")
        self.assertIn("需重新生成", r["note"])
        # 静帧文件与 json 条目都没了
        sd = self.proj / "media" / "ep1" / "stills"
        self.assertFalse((sd / "LN01.jpg").exists())
        self.assertEqual(len(json.loads((sd.parent / "stills.json").read_text(encoding="utf-8"))), 0)
        # clip 被**暂存**（不在 clips/ 了，但也没被删）—— 目录名以 `clipqc` 为准
        self.assertFalse((self.proj / "media" / "ep1" / "clips" / "LN01.mp4").exists())
        self.assertTrue(list((self.proj / "media" / "ep1" / "clips" / ".clipqc_bad")
                             .glob("LN01.mp4")), "clip 应被暂存而非删除")

    def test_update_segment_rejects_unknown_column(self):
        with self.assertRaises(webwrite.EditError):
            webwrite.update_segment(self.proj, 1, "LN01", {"nonexistent": "x"})

    def test_update_segment_rejects_unknown_shot(self):
        with self.assertRaises(webwrite.EditError):
            webwrite.update_segment(self.proj, 1, "LN99", {"dialogue": "x"})

    def test_delete_segment_removes_row(self):
        r = webwrite.delete_segment(self.proj, 1, "LN02")
        self.assertEqual(r["removed"], "LN02")
        self.assertEqual(r["remaining"], 1)
        md = (self.proj / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8")
        self.assertNotIn("钱给你十倍", md)
        self.assertIn("这单我接了", md)

    def test_delete_segment_refuses_to_empty(self):
        webwrite.delete_segment(self.proj, 1, "LN02")
        with self.assertRaises(webwrite.EditError) as c:
            webwrite.delete_segment(self.proj, 1, "LN01")
        self.assertIn("至少保留一镜", str(c.exception))

    def test_add_segment_inserts_placeholder(self):
        r = webwrite.add_segment(self.proj, 1, after="LN01")
        self.assertEqual(r["inserted_after"], "LN01")
        self.assertEqual((r["shots_before"], r["shots_after"]), (2, 3))
        self.assertTrue(r["visible"], "★ 新镜必须**能被 parse 出来**，否则等于没插")
        md = (self.proj / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8")
        self.assertIn("待补", md)
        from v5.media import storyboard
        self.assertEqual(len(storyboard.parse(md)), 3)

    def test_add_segment_at_end_when_no_after(self):
        r = webwrite.add_segment(self.proj, 1)
        self.assertTrue(r["visible"])
        from v5.media import storyboard
        self.assertEqual(len(storyboard.parse(
            (self.proj / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8"))), 3)

    def test_add_segment_with_visual_is_used(self):
        r = webwrite.add_segment(self.proj, 1, after="LN01",
                                 fields={"visual": "新加的一镜：镜头缓缓推进到门缝，外面在下雨"})
        self.assertTrue(r["visible"])
        md = (self.proj / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8")
        self.assertIn("镜头缓缓推进到门缝", md)
        self.assertNotIn("待补", md, "给了 visual 就不该再写占位")

    def test_placeholder_meets_parse_min_length(self):
        """★ 占位文本必须 ≥15 字 —— `parse` 会跳过更短的画面描述（插了等于没插）。"""
        self.assertGreaterEqual(len(webwrite.PLACEHOLDER_VISUAL.strip()), 15)

    def test_edit_without_storyboard_rejected(self):
        (self.proj / "scenedesigner" / "scenedesigner.md").unlink()
        with self.assertRaises(webwrite.EditError):
            webwrite.update_segment(self.proj, 1, "LN01", {"dialogue": "x"})


class TestOutline(_Base):
    def test_whitelist_only(self):
        r = webwrite.update_outline(self.proj, {"genre": "温情"})
        self.assertEqual(r["changed"]["genre"], "恐怖悬疑")
        b = json.loads((self.proj / "brief.json").read_text(encoding="utf-8"))
        self.assertEqual(b["genre"], "温情")

    def test_rejects_non_whitelisted(self):
        # 2026-09-16：原来用 3d-animation（该包已铲除）；换成仍然存在的包名。
        with self.assertRaises(webwrite.EditError) as c:
            webwrite.update_outline(self.proj, {"pack": "niulai-movie-style"})
        self.assertIn("不可编辑", str(c.exception))


class TestInvalidateAll(_Base):
    def test_all_shots_go_pending(self):
        self.seed_still_and_clip("LN01")
        self.seed_still_and_clip("LN02")
        r = webwrite.invalidate_all(self.proj, 1, reason="剧本改了")
        self.assertEqual(r["shots"], 2)
        self.assertEqual(len(r["stashed_clips"]), 2)
        jb = jobs_mod.load(self.proj / "media" / "ep1")
        self.assertTrue(all(v["state"] == "pending" for v in jb.values()))


def _sb_md(n_shots: int) -> str:
    """造一份 `n_shots` 镜的分镜（格式与真实产物同构，`parse` 会给出 LN01..）。"""
    rows = "\n".join(
        "| %d | 近景 | 平视 | 固定 | 4 | 老陈在楼道里收起 @黑伞 站定，第 %d 镜 | 老陈：第 %d 句。 | 环境音 |"
        % (i, i, i) for i in range(1, n_shots + 1))
    tot = "\n".join("| %d | 4 | 汇总 |" % i for i in range(1, n_shots + 1))
    return ("# 分镜：验收\n\n## 第1幕｜楼道-日｜S1 / 4s\n\n"
            "| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |\n"
            "|--------|------|------|------|---------|---------|------|------|\n"
            + rows + "\n\n## 分镜总表\n\n"
            "| 镜头号 | 时长(秒) | 备注 |\n|--------|---------|------|\n" + tot + "\n")


class TestValidateReadsRequestedEpisode(unittest.TestCase):
    """★★ `_validate` 必须**按请求的那一集**读分镜，不能回落 manifest。

    ## 事故形态（2026-09-19 实跑两集时抓到）

    `_validate` 旧签名**不收 `ep`** ⇒ 里面 `resolve_path(root,"scenedesigner")` 的
    `ep=None` 回落到 **`manifest.episode_index`**（= 最近跑过的那一集）。
    实测：跑完第 2 集后（`episode_index=2`），回到**第 1 集**的分镜页点「批量生成图片」，
    前端选的 19 个镜号被拿**第 2 集的分镜（15 镜）**校验 ⇒

        E400「镜号不在分镜里：LN16、LN17、LN18、LN19（本片共 15 镜）」

    ⇒ 报的是**看起来像"分镜写错了"的错**，真因是**串集**；而且此时该集
    **所有媒体操作都被拒**（与 payload 无关，只要 manifest 停在别的集就必现）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._p = mock.patch.object(config, "PROJECTS_DIR", self.root)
        self._pr = mock.patch.object(config, "PROJECT_ROOT", self.root)
        self._p.start()
        self._pr.start()
        self.pid = "two-ep"
        proj = self.root / self.pid
        (proj / "scenedesigner").mkdir(parents=True)
        (proj / "brief.json").write_text(json.dumps(
            {"topic": "两集验收", "pack": "shortdrama", "genre": "温情", "episodes": 2,
             "must_have": ["收伞"], "key_props": [], "禁忌": [], "tone": "静",
             "结局": "定格", "protagonist": "老陈"}, ensure_ascii=False), encoding="utf-8")
        # ep1 有 4 镜、ep2 只有 2 镜 —— 数目不同才能暴露"读错集"
        (proj / "scenedesigner" / "scenedesigner_ep1.md").write_text(_sb_md(4), encoding="utf-8")
        (proj / "scenedesigner" / "scenedesigner_ep2.md").write_text(_sb_md(2), encoding="utf-8")
        # ★ 关键前提：manifest 停在**第 2 集**（真实场景就是刚跑完 ep2）
        (proj / ".agent_state.json").write_text(
            json.dumps({"episode_index": 2}), encoding="utf-8")
        self.proj = proj

    def tearDown(self):
        self._pr.stop()
        self._p.stop()
        self.tmp.cleanup()

    def test_ep1_shot_accepted_even_when_manifest_is_on_ep2(self):
        """LN03 在第 1 集里有、第 2 集里没有 ⇒ 按 ep=1 校验必须**通过**。"""
        out = runner._validate(self.pid, "keyframe", ["LN03"], ep=1)
        self.assertEqual(out, ["LN03"])

    def test_same_shot_rejected_for_the_other_episode(self):
        """同一镜号按 ep=2 校验就该**被拒**（证明它真的按集读了）。"""
        with self.assertRaises(ValueError) as c:
            runner._validate(self.pid, "keyframe", ["LN03"], ep=2)
        self.assertIn("镜号不在分镜里", str(c.exception))

    def test_ep0_normalizes_to_ep1(self):
        """`ep` 为 0/None 时归一成 1，别让它又回落到 manifest。"""
        self.assertEqual(runner._validate(self.pid, "keyframe", ["LN03"], ep=0), ["LN03"])

    def test_pipeline_known_shots_is_per_episode(self):
        """★★ `pipeline._known_shots` 也必须**按集**读（2026-09-19 同一事故的第二个入口）。

        实测：`runner._validate` 修好之后重试，run 以 `ep=1 shots=19` 启动，
        但**流水线内部**仍打印「分镜 15 镜」——它读的是第 2 集。
        根因同型：`resolve_path(..., ep=None)` 回落 `manifest.episode_index`。
        """
        from v5.media import pipeline
        self.assertEqual(pipeline._known_shots(self.proj, 1),
                         ["LN01", "LN02", "LN03", "LN04"], "第 1 集有 4 镜")
        self.assertEqual(pipeline._known_shots(self.proj, 2),
                         ["LN01", "LN02"], "第 2 集只有 2 镜")


class TestExecChainPassesEpisode(unittest.TestCase):
    """`_exec_chain` **必须把 `--ep` 传给 `drive_chain`**（2026-09-19 修的真 bug）。

    ## 事故形态（本项目最忌的「选了 A 实际跑 B」换了个入口）

    `_exec_chain(root, ep, …)` 收了 `ep`，但旧命令行只拼了 `--timeout` ⇒
    `drive_chain` 用它自己的缺省 `--ep 1` ⇒ **在前端点第 2 集的「生成分镜」，
    实际重跑了第 1 集**（`bind_episode(root,1)` + 产物写 `*_ep1.md`）。
    而函数末尾的产物核对又按 `ep=2` 查 ⇒ 报「缺角色」——
    失败**看得见**，但**第 1 集已经被白跑一遍**（十几分钟 + 一整轮 token）。

    ⇒ 本用例锁死 argv 里必须有 `--ep <N>`，且值等于传进来的 ep。
    """

    def test_ep_is_forwarded(self):
        from v5 import webchain
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "proj2"
        root.mkdir(parents=True)
        captured = {}

        class _R:
            returncode = 0

        def fake_run(argv, **kw):
            captured["argv"] = list(argv)
            return _R()

        with mock.patch.object(webchain, "devserver_status",
                               return_value={"alive": True, "ok": True}), \
                mock.patch.object(runner.subprocess, "run", fake_run):
            runner._exec_chain(root, 2, [], lambda *a, **k: None)

        argv = captured.get("argv") or []
        self.assertIn("--ep", argv,
                      "必须显式传 --ep；否则 drive_chain 用自己的缺省 1 = 重跑第 1 集")
        self.assertEqual(argv[argv.index("--ep") + 1], "2")

    def test_ep_defaults_to_1_when_zero(self):
        from v5 import webchain
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "proj3"
        root.mkdir(parents=True)
        captured = {}

        class _R:
            returncode = 0

        def fake_run(argv, **kw):
            captured["argv"] = list(argv)
            return _R()

        with mock.patch.object(webchain, "devserver_status",
                               return_value={"alive": True, "ok": True}), \
                mock.patch.object(runner.subprocess, "run", fake_run):
            runner._exec_chain(root, 0, [], lambda *a, **k: None)
        argv = captured.get("argv") or []
        self.assertEqual(argv[argv.index("--ep") + 1], "1", "ep=0 要归一成 1，不能传 '0'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
