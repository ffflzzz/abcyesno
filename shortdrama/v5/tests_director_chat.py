# -*- coding: utf-8 -*-
"""`v5/director_chat.py`（和导演对话）+ `runner` 那条"接着聊过的那段对话开工"的接线自测。

**全程离线**：不连 dev server、不调模型。用假 client 打桩 `_client`，
所以这套测试不花一分钱额度，也不会碰任何真项目。

## 为什么值得钉住

这条路上有两个"只有真跑才暴露"的坑，都已经踩过：

  1. **重复发言**：`_pull_new` 第一版把人说的和机器说的**都**搬进时间线，
     而我们自己已经记过用户那一侧 ⇒ 界面上每句话出现两次（一次干净、一次带前缀）。
  2. **开工进度搬不进来**：`_poll` 第一版只在"本模块发起的 run 收工了"时才搬运，
     而 `director/start` 走的是 `runner.start`（不经过 `submit`）⇒
     角色产物全落盘了、对话里却一句话都没多。
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v5 import director_chat  # noqa: E402
from v5.media import runner  # noqa: E402

_client_orig = director_chat._client


class _FakeClient:
    """能返回一份可控的 thread state。按调用次序吐出预设的几帧。"""

    def __init__(self, frames):
        self.frames = list(frames)
        self.calls = 0

        outer = self

        class _Threads:
            async def get_state(self, tid):        # noqa: ARG002
                outer.calls += 1
                return {"values": {"messages": outer.frames[min(outer.calls - 1,
                                                                 len(outer.frames) - 1)]}}

        self.threads = _Threads()


def _ai(t):
    return {"type": "ai", "content": t}


def _human(t):
    return {"type": "human", "content": t}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "demo"
        (self.root / "scenedesigner").mkdir(parents=True)
        (self.root / "brief.json").write_text(json.dumps(
            {"topic": "试探片", "pack": "shortdrama", "genre": "测试", "episodes": 1,
             "target_duration": "约 60 秒", "protagonist": "甲：测试用",
             "must_have": ["甲说一句话"], "key_props": [], "禁忌": [],
             "tone": "冷", "结局": "定格"}, ensure_ascii=False), encoding="utf-8")
        # ★★ 2026-10-08：**RUNTIME_ROOT 必须打桩**（同 `tests_webchain` 里那条教训）。
        #   不打桩的话，任何一次 `ensure_devserver` 都会写**真实**的
        #   `.tmp/web-devserver.json`、还会真去 spawn `langgraph dev` ——
        #   实测把共享状态文件写成了一条 `{"pid":"demo"}` 的假现场。
        from v5 import config as _cfg
        self._rt = mock.patch.object(_cfg, "RUNTIME_ROOT", self.root)
        self._rt.start()

    def tearDown(self):
        try:
            self._rt.stop()
        except Exception:  # noqa: BLE001
            pass
        self.tmp.cleanup()


class TestState(_Base):
    def test_empty_by_default_and_reading_creates_nothing(self):
        self.assertEqual([], director_chat.turns(self.root))
        self.assertEqual("", director_chat.thread_id(self.root))
        self.assertFalse(director_chat.path_of(self.root).exists())

    def test_ask_with_empty_text_is_refused_without_touching_the_network(self):
        r = director_chat.submit(self.root, "   ", ep=1)
        self.assertFalse(r["ok"])
        self.assertFalse(director_chat.path_of(self.root).exists())

    def test_reset_forgets_the_thread_but_keeps_the_transcript(self):
        obj = {"thread_id": "t-1", "run_id": "r-1", "seen": 4,
               "turns": [{"role": "director", "text": "你好", "ep": 1}]}
        director_chat._write(self.root, obj)
        director_chat.reset(self.root)
        after = director_chat._read(self.root)
        self.assertEqual("", after["thread_id"])
        self.assertEqual(1, len(after["turns"]))      # ⛔ 不删人看过的记录


class TestPullNew(_Base):
    """时间线搬运：只搬他说的，不搬我们发出去的。"""

    def test_only_assistant_messages_are_pulled(self):
        obj = director_chat._read(self.root)
        obj.update({"thread_id": "t-1", "run_id": "r-1", "seen": 0})
        director_chat._write(self.root, obj)
        fake = _FakeClient([[_human("（带前缀的原始消息）"), _ai("我打算用 3 个空间")]])
        director_chat._client = lambda url: fake            # type: ignore[assignment]
        try:
            added = asyncio.run(director_chat._pull_new(self.root, "t-1", "http://x", 1))
        finally:
            director_chat._client = _client_orig
        self.assertEqual(1, added)
        ts = director_chat.turns(self.root)
        self.assertEqual(1, len(ts))
        self.assertEqual("director", ts[0]["role"])
        self.assertIn("3 个空间", ts[0]["text"])

    def test_second_poll_does_not_duplicate(self):
        obj = director_chat._read(self.root)
        obj.update({"thread_id": "t-1", "run_id": "r-1", "seen": 0})
        director_chat._write(self.root, obj)
        fake = _FakeClient([[_ai("第一句")]])
        director_chat._client = lambda url: fake            # type: ignore[assignment]
        try:
            asyncio.run(director_chat._pull_new(self.root, "t-1", "http://x", 1))
            added2 = asyncio.run(director_chat._pull_new(self.root, "t-1", "http://x", 1))
        finally:
            director_chat._client = _client_orig
        self.assertEqual(0, added2, "同一帧搬第二遍 = 界面上出现重影")
        self.assertEqual(1, len(director_chat.turns(self.root)))

    def test_production_narration_lands_in_the_same_timeline(self):
        """开工后的"规格已落盘／派 scenedesigner"必须进得来 —— 这就是进度可见。"""
        obj = director_chat._read(self.root)
        obj.update({"thread_id": "t-1", "run_id": "r-1", "seen": 0})
        director_chat._write(self.root, obj)
        fake = _FakeClient([[_ai("先落规格"), _ai("规格已落盘。开始派发。"),
                             _ai("Worldbuilder 已落盘。")]])
        director_chat._client = lambda url: fake            # type: ignore[assignment]
        try:
            added = asyncio.run(director_chat._pull_new(self.root, "t-1", "http://x", 1))
        finally:
            director_chat._client = _client_orig
        self.assertEqual(3, added)
        # ⚠️ 这里要**子串**匹配：`assertIn` 对列表是**精确相等**，
        #    第一条会把"规格已落盘。开始派发。"整句拿去比对而假红（刚踩过）。
        texts = [t["text"] for t in director_chat.turns(self.root)]
        self.assertTrue(any("规格已落盘" in t for t in texts), texts)


class TestChainVerdict(_Base):
    """★ 开工失败必须在对话里**响亮**，不能只躺在 `.tmp/web-runs/*.log` 里。

    实测 `paste-1008-2204`（2026-10-08）：点开工 → 界面弹「已开工」→ 导演回一句反问
    → 然后什么都没发生。真相是那一轮 21 秒、7 个角色零产物、`status=failed`，
    而界面上从头到尾没说这一轮失败了。
    """

    _REC = {"run_id": "r-chain-1", "kind": "chain", "status": "failed",
            "started_at": "2026-10-08 22:14:16", "ended_at": "2026-10-08 22:14:37",
            "result": {"reason": "创作链未产出这些角色的契约产物：worldbuilder"}}

    def _runs(self, rec):
        return lambda pid=None, limit=30: [dict(rec)]

    def test_failed_chain_lands_in_the_transcript(self):
        with mock.patch.object(runner, "list_runs", self._runs(self._REC)):
            director_chat._chain_verdict(self.root, 1)
        sys_t = [t["text"] for t in director_chat.turns(self.root) if t["role"] == "system"]
        self.assertEqual(1, len(sys_t), sys_t)
        self.assertIn("failed", sys_t[0])
        self.assertIn("worldbuilder", sys_t[0], "理由必须原样带出来，不然等于没说")

    def test_reported_only_once_per_run(self):
        with mock.patch.object(runner, "list_runs", self._runs(self._REC)):
            director_chat._chain_verdict(self.root, 1)
            director_chat._chain_verdict(self.root, 1)      # 前端每 3 秒就轮一次
        self.assertEqual(1, len([t for t in director_chat.turns(self.root)
                                 if t["role"] == "system"]),
                         "同一个 run 报两遍 = 刷屏")

    def test_a_run_that_is_still_going_reports_nothing(self):
        """⛔ 前提不存在时不许报 —— 否则链正在跑就被判了死刑。"""
        live = dict(self._REC, status="running", result={})
        with mock.patch.object(runner, "list_runs", self._runs(live)):
            director_chat._chain_verdict(self.root, 1)
        self.assertEqual([], [t for t in director_chat.turns(self.root)
                              if t["role"] == "system"])


class TestChatPhaseContract(_Base):
    """对话阶段那段律的**形状**：不派活、不写创作产物，但**必须能落 `brief.json`**。

    实测代价（`paste-1008-2204`）：旧律连 `brief.json` 都不许写 ⇒ 用户在聊天里把整条
    故事口述完、导演两次说"最终版如下"，盘上仍是出厂那份空 brief ⇒ 开工时他读到空 brief
    就停下来反问，21 秒零产物。"聊清楚"没有出口 = 这条道白搭。
    """

    def test_brief_write_allowed_and_dispatch_still_banned(self):
        p = director_chat.CHAT_PREFIX
        self.assertIn("brief.json", p)
        self.assertIn("write_file", p, "得告诉他用什么写 —— 只说'落盘'他未必动手")
        self.assertIn("不要派发任何子代理", p)
        for gone in ("worldbuilder", "分镜"):
            self.assertIn(gone, p, "创作产物仍然要点名禁止")

    def test_no_leftover_blanket_ban(self):
        """反向对照：旧那句抄回来，这条道就又变成"聊完即蒸发"。"""
        p = director_chat.CHAT_PREFIX
        self.assertNotIn("不要调用任何工具", p)
        self.assertNotIn("不要写任何文件", p)


class TestDigest(_Base):
    """对话时禁止他用工具 ⇒ 必须把盘上事实喂给他，否则只能空谈。"""

    def test_digest_carries_brief_and_role_status(self):
        d = director_chat._digest(self.root, 1)
        self.assertIn("试探片", d)          # brief 的主题
        self.assertIn("甲：测试用", d)      # 主角
        self.assertIn("定格", d)            # 结局
        self.assertIn("scenedesigner", d)   # 角色清单（此刻还没落盘）
        self.assertIn("还没有", d)

    def test_digest_marks_what_is_already_on_disk(self):
        (self.root / "worldbuilder").mkdir()
        (self.root / "worldbuilder" / "worldbuilder.md").write_text("世界设定内容",
                                                                   encoding="utf-8")
        d = director_chat._digest(self.root, 1)
        self.assertIn("worldbuilder", d)
        self.assertIn("已落盘", d)


class TestTypedGoAhead(_Base):
    """打字即开工（2026-10-08 用户："为什么要发送和开工这样机械分开？"）。

    ⛔ 只认**短句**：长句里出现"开工"多半是在布置任务（"开工前先把空间定下来"），
      那不该触发一路跑下去。两侧都要钉：该触发的触发、**不该触发的一个都不许**。
    """

    def setUp(self):
        super().setUp()
        # ★★ 2026-10-08：**必须把 `ensure_devserver` 打桩**。
        #   `_submit` 第一件事就是起 dev server —— 不打桩它会**真的 spawn 一个
        #   `langgraph dev`**（实测：留下了一个活着的进程占着 2024 端口，还把
        #   `.tmp/web-devserver.json` 写成了 `{"pid":"demo"}` 这条**假现场**）。
        #   单测不许起真进程，这是硬规矩。
        from v5 import webchain as _wc
        self._dev = mock.patch.object(_wc, "ensure_devserver",
                                      lambda pid, **k: {"reused": True, "pid": pid,
                                                        "agent_url": "http://127.0.0.1:9"})
        self._dev.start()

    def tearDown(self):
        self._dev.stop()
        super().tearDown()

    def test_short_go_words_hit(self):
        for t in ("开工", "开工！", "开始吧", "开拍", "动手吧", " start ", "Go"):
            self.assertTrue(director_chat.looks_like_go(t), t)

    def test_instructions_mentioning_go_do_not_hit(self):
        for t in ("开工前先把三个空间定下来", "我们先聊清楚再开工，你觉得呢？",
                  "我不太确定要不要开工", "开工之后能改吗？"):
            self.assertFalse(director_chat.looks_like_go(t), t)

    def test_empty_and_long_are_not_go(self):
        self.assertFalse(director_chat.looks_like_go(""))
        self.assertFalse(director_chat.looks_like_go("   "))
        self.assertFalse(director_chat.looks_like_go("开工" * 20))

    def test_submit_with_go_word_starts_the_chain_not_a_chat_run(self):
        """★ 走的是 `runner.start`（驱动器），不是自己建一个 run ——
        否则步级确认 / 打回 / 反空转那些闭环一个都不在。"""
        started, notes = [], []

        def _fake_start(pid, kind, ep=1, **kw):
            started.append((pid, kind, kw.get("chain_thread")))
            return {"run_id": "r-x", "status": "running"}

        with mock.patch.object(director_chat, "_ensure_thread",
                               lambda root, url: asyncio.sleep(0, result=("t-1", True))),              mock.patch.object(director_chat, "_live_chain_run", lambda root: None),              mock.patch.object(director_chat, "_digest", lambda root, ep: ""):
            from v5.media import runner as _r
            orig = _r.start
            _r.start = _fake_start                     # type: ignore[assignment]
            try:
                r = asyncio.run(director_chat._submit(self.root, "开工", 1))
            finally:
                _r.start = orig                        # type: ignore[assignment]
        self.assertTrue(r.get("ok"))
        self.assertTrue(r.get("started"), r)
        self.assertEqual(("demo", "chain", "t-1"), started[0] if started else None)
        self.assertIn("开工", [t["text"] for t in director_chat.turns(self.root)])

    def test_submit_with_go_word_is_refused_when_a_chain_is_already_running(self):
        with mock.patch.object(director_chat, "_ensure_thread",
                               lambda root, url: asyncio.sleep(0, result=("t-1", True))),              mock.patch.object(director_chat, "_live_chain_run",
                               lambda root: {"run_id": "2026xxxx-live"}),              mock.patch.object(director_chat, "_digest", lambda root, ep: ""):
            r = asyncio.run(director_chat._submit(self.root, "开工", 1))
        self.assertFalse(r.get("ok"))
        self.assertIn("已经有一条链在跑", r.get("error") or "")



class TestConcurrentWrites(_Base):
    """并发写（2026-10-07 用户界面上直接弹的那个 WinError 32）。

    场景是真的：前端每 3 秒轮询一次 `GET .../director/chat`，每轮都可能
    `_pull_new` → 重写状态文件；FastAPI 的同步处理器跑在**线程池**里 ⇒ 两次轮询
    会真的并发。第一版两个写者共用 `director_chat.json.tmp` 这个**同一个**临时名，
    `os.replace` 直接 `[WinError 32] 另一个程序正在使用此文件`。
    """

    def test_temp_name_differs_on_every_write(self):
        """机制层：临时名带 pid + 序号 ⇒ 不同写者永不撞名。"""
        import os as _os
        seen = []
        orig = director_chat.os.replace

        def spy(a, b):
            seen.append(str(a))
            return orig(a, b)

        director_chat.os.replace = spy
        try:
            director_chat._write(self.root, {"turns": []})
            director_chat._write(self.root, {"turns": []})
        finally:
            director_chat.os.replace = orig
        self.assertEqual(2, len(seen))
        self.assertNotEqual(seen[0], seen[1], "两次写用了同一个临时名 = 会 WinError 32")

    def test_parallel_appends_lose_nothing_and_raise_nothing(self):
        import threading
        errs = []

        def work(i):
            try:
                director_chat._append(self.root, "director", "第%d条" % i)
            except Exception as e:  # noqa: BLE001
                errs.append(repr(e))

        ts = [threading.Thread(target=work, args=(i,)) for i in range(24)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual([], errs, "并发写抛异常了：%s" % errs[:3])
        got = [t["text"] for t in director_chat.turns(self.root)]
        self.assertEqual(24, len(got), "有写者被别的写者覆盖掉了（丢更新）")
        # ⚠️ 用**集合**比，别用 sorted() —— 字符串序把「第10条」排在「第2条」前面，
        #    拿排序后的列表去比会假红（刚踩过）。
        self.assertEqual({"第%d条" % i for i in range(24)}, set(got))

    def test_readers_never_see_a_broken_file_while_writers_run(self):
        """**这条才是复现真实形态的**：写者不断替换文件，读者同时在读。

        ⚠️ 教训：我一度用"8 个线程狂打 HTTP GET"来验修复，2468 次零错误 —— 但那次
        POST 因为 2024 端口被别人的 dev server 占着而失败了，**根本没有对话、
        写路径一次都没走到**。绿灯是假的。读数干净不等于这条路被走过。
        """
        import threading
        stop = [False]
        errs = []

        def writer(i):
            n = 0
            while not stop[0]:
                try:
                    director_chat._append(self.root, "director", "w%d-%d" % (i, n))
                    n += 1
                except Exception as e:  # noqa: BLE001
                    errs.append("写:" + repr(e))

        def reader():
            while not stop[0]:
                try:
                    director_chat.turns(self.root)     # 半截 JSON 会在这里炸
                except Exception as e:  # noqa: BLE001
                    errs.append("读:" + repr(e))

        ws = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        rs = [threading.Thread(target=reader) for _ in range(4)]
        for t in ws + rs:
            t.start()
        import time as _t
        _t.sleep(1.2)
        stop[0] = True
        for t in ws + rs:
            t.join()
        self.assertEqual([], errs[:4], "读写互踩：%s" % errs[:4])
        self.assertTrue(len(director_chat.turns(self.root)) > 0)

    def test_no_temp_files_left_behind(self):
        for i in range(3):
            director_chat._append(self.root, "user", "x%d" % i)
        left = [p.name for p in director_chat.path_of(self.root).parent.iterdir()
                if ".tmp" in p.name or p.name.endswith(".lock")]
        self.assertEqual([], left, "临时文件/锁没清干净：%s" % left)


class TestRunnerThreadPlumbing(_Base):
    """`runner` 那条："接着聊过的那段对话开工"。**默认不许变**。"""

    def test_exec_chain_defaults_to_no_thread(self):
        """⛔ 不传 rec 时必须退化成原行为（让 drive_chain 自己新建对话）。"""
        seen = {}

        def _fake_run_chain(root, ep, shots, log, until="", thread=""):
            seen["thread"] = thread
            return {"status": "ok"}

        orig = runner._run_chain
        runner._run_chain = _fake_run_chain               # type: ignore[assignment]
        try:
            for rec in (None, {}, {"chain_thread": ""}):
                seen.clear()
                runner._exec_chain(self.root, 1, [], print, rec)
                assert seen["thread"] == "", "rec=%r 时不该带线程" % (rec,)
        finally:
            runner._run_chain = orig
        self.assertEqual("", seen["thread"])

    def test_exec_chain_passes_thread_when_record_has_one(self):
        seen = {}

        def _fake_run_chain(root, ep, shots, log, until="", thread=""):
            seen["thread"] = thread
            return {"status": "ok"}

        orig = runner._run_chain
        runner._run_chain = _fake_run_chain               # type: ignore[assignment]
        try:
            runner._exec_chain(self.root, 1, [], print, {"chain_thread": "t-9"})
        finally:
            runner._run_chain = orig
        self.assertEqual("t-9", seen["thread"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
