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

    def tearDown(self):
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
