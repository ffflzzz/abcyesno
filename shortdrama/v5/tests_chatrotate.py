"""文本通道的 key 轮转（`v5/llm.py::RotatingChatOpenAI`）—— 全程离线，不碰网络。

为什么必须有（2026-09-30 实测）：`xiaoman-workshop-1030` 第 2 集起服 **20 秒**就
`status=error`，正文「已达到 API 用量上限，请在 2026-09-30 19:00 之后重试」。
逐条探测三条 key 的文本额度：第 1 条 429、第 2 条可用、第 3 条可用 —— 而文本通道
只会用一条（`config._chat_endpoint_of()` 死盯第一条带专属地址的 key）。
额度是**按 key** 算的，所以"换一条"就是出口；没有这层，整条链只能干等两小时。

★ 这些用例不打网络：父类 `ChatOpenAI.invoke` 被打桩。
"""
from __future__ import annotations

import unittest
from unittest import mock

from v5 import config, llm


class _Fake429(Exception):
    pass


def _key_of(m) -> str:
    """读模型当前用的 key。

    ⛔ 不能用 `m.api_key` —— 那只是构造别名，事后既读不到也赋不上
    （真实字段是 `openai_api_key: SecretStr`）。测试第一版就栽在这里。
    """
    k = getattr(m, "openai_api_key", None)
    return k.get_secret_value() if hasattr(k, "get_secret_value") else str(k)


def _mk(keys=("k1", "k2", "k3")):
    """把候选集换成假 key，并重置池（每个用例独立）。"""
    cands = [(k, "https://fake.test/v1") for k in keys]
    llm._CHAT_POOL = None
    llm._CHAT_CANDS = []
    return mock.patch.object(config, "chat_key_pool", return_value=cands)


class PoolTests(unittest.TestCase):
    def test_only_keys_with_own_base(self):
        """没标专属地址的 key 不许进文本候选集（那会落到国际入口）。"""
        with mock.patch.object(config, "AGNES_API_KEYS", ["a", "b"]), \
                mock.patch.object(config, "AGNES_KEY_BASE", {"b": "https://x/v1"}):
            self.assertEqual(config.chat_key_pool(), [("b", "https://x/v1")])

    def test_none_marked_falls_back_to_single(self):
        with mock.patch.object(config, "AGNES_API_KEYS", ["a", "b"]), \
                mock.patch.object(config, "AGNES_KEY_BASE", {}), \
                mock.patch.object(config, "AGNES_API_KEY", "a"):
            self.assertEqual(config.chat_key_pool(), [("a", "")])


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.model = llm.RotatingChatOpenAI(
            api_key="k1", model="m", base_url="https://fake.test/v1")

    def tearDown(self):
        # 别把假 key 的池留给后面的用例（同进程里别的套件会读 `llm.chat_pool()`）
        llm._CHAT_POOL = None
        llm._CHAT_CANDS = []

    def test_429_moves_to_next_key_and_succeeds(self):
        seen = []

        def fake(self_, inp, *a, **kw):
            seen.append(_key_of(self_))
            if _key_of(self_) == "k1":
                raise _Fake429("Error code: 429 - 已达到 API 用量上限")
            return "ok"

        with _mk(), mock.patch.object(llm.ChatOpenAI, "invoke", fake):
            out = self.model.invoke("hi")
            pool = llm.chat_pool()          # ★ 必须在 with 内取：出块后补丁失效，
                                            #   `chat_pool()` 会用**真实 key** 重建，
                                            #   断言就打在另一个对象上（我踩过一次）
        self.assertEqual(out, "ok")
        self.assertEqual(seen, ["k1", "k2"], "第一次撞 429 后必须换下一条 key")
        self.assertEqual(pool._rl[0], 1, "被拒的 key 要记账")
        # 验"冷却生效"要看行为，不能看 `earliest_free_s()` —— 那个的语义是
        # "最快还有多久放开"，另两条空闲时它就是 0（我第一版拿错判据了）。
        nxt = pool.claim_nowait()
        self.assertIsNotNone(nxt, "还有空闲 key 时不该领不到")
        self.assertNotEqual(nxt[0], 0, "刚被拒的 k1 在冷却中，不该马上又被选中")

    def test_other_errors_propagate_immediately(self):
        calls = []

        def fake(self_, inp, *a, **kw):
            calls.append(_key_of(self_))
            raise ValueError("模型名写错了")

        with _mk(), mock.patch.object(llm.ChatOpenAI, "invoke", fake):
            with self.assertRaises(ValueError):
                self.model.invoke("hi")
        self.assertEqual(calls, ["k1"], "非限流错误不许换 key 重试（那是掩盖真因）")

    def test_all_keys_cooling_fails_fast_instead_of_hanging(self):
        """全在冷却 ⇒ 抛错并说清还要等多久。⛔ 不许睡到恢复（那是把挂死换成慢挂死）。"""
        def fake(self_, inp, *a, **kw):
            raise _Fake429("Error code: 429 - rate limit")

        with _mk(), mock.patch.object(llm.ChatOpenAI, "invoke", fake), \
                mock.patch.object(config, "CHAT_COOLDOWN_SEC", 600.0):
            with self.assertRaises(Exception) as cm:
                for _ in range(6):          # 池只有 3 条，多跑几轮把全部拖进冷却
                    self.model.invoke("hi")
        self.assertIn("冷却", str(cm.exception))

    def test_async_path_rotates_too(self):
        import asyncio
        seen = []

        async def fake(self_, inp, *a, **kw):
            seen.append(_key_of(self_))
            if _key_of(self_) == "k1":
                raise _Fake429("Error code: 429")
            return "ok"

        with _mk(), mock.patch.object(llm.ChatOpenAI, "ainvoke", fake):
            out = asyncio.run(self.model.ainvoke("hi"))
        self.assertEqual(out, "ok")
        self.assertEqual(seen, ["k1", "k2"])


class CooldownParsingTests(unittest.TestCase):
    def test_vendor_stated_reset_time_is_honored(self):
        import datetime as dt
        target = dt.datetime.now() + dt.timedelta(hours=2, minutes=5)
        msg = "已达到 API 用量上限，请在 %s 之后重试" % target.strftime("%Y-%m-%d %H:%M")
        secs = llm.cooldown_seconds(_Fake429(msg))
        # 代码会加 30s 余量，所以允许区间是 [2h, 2h+400s]
        self.assertGreater(secs, 2 * 3600 - 60, "供应商给了恢复时间就该照它冷却")
        self.assertLess(secs, 2 * 3600 + 400)

    def test_no_reset_time_uses_default(self):
        with mock.patch.object(config, "CHAT_COOLDOWN_SEC", 120.0):
            self.assertEqual(llm.cooldown_seconds(_Fake429("Error code: 429")), 120.0)


class FactoryTests(unittest.TestCase):
    def test_single_key_keeps_plain_class(self):
        """候选只有一条 ⇒ 返回父类对象（错误文案与改造前逐字节一致）。"""
        with mock.patch.object(config, "chat_key_pool", return_value=[("a", "")]), \
                mock.patch.object(config, "CHAT_KEY_ROTATE", True):
            m = llm.chat_for("agnes", 64)
        self.assertIs(type(m), llm.ChatOpenAI)

    def test_rotate_off_keeps_plain_class(self):
        with _mk(("k1", "k2")), mock.patch.object(config, "CHAT_KEY_ROTATE", False):
            m = llm.chat_for("agnes", 64)
        self.assertIs(type(m), llm.ChatOpenAI)

    def test_multi_key_uses_rotating_class(self):
        with _mk(), mock.patch.object(config, "CHAT_KEY_ROTATE", True):
            m = llm.chat_for("agnes", 64)
        self.assertIsInstance(m, llm.RotatingChatOpenAI)


if __name__ == "__main__":
    unittest.main(verbosity=2)
