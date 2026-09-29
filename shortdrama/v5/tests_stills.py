# -*- coding: utf-8 -*-
"""并发生图：跨 key 池 + 串行/并发一致性。

为什么要有这份测试（2026-09-29）：静帧从"一张一张发"改成"3 条 key 并发发"，
省的是一小时里 18 分钟的墙钟，**风险是并发把提示词或清单弄坏**——而这两样一旦
悄悄不对，要到成片逐帧看才发现（本项目最忌的"失败不可见"）。所以这里锁三件事：

1. 并发与串行**产出逐字相同**（同 prompt、同条目、同清单条数）；
2. 缺图必须**响亮**（失败镜不进清单、并打印还差几镜），不许"完成 N/N"骗人；
3. 429 只惩罚**撞限的那条 key**，其他 key 照常顶上。

闸门一律用假时钟或超大 rpm，**不在测试里真 sleep**。
"""
from __future__ import annotations

import hashlib
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


def _shot(i: int) -> dict:
    # 「画面描述」长度必须 ≥15 字：storyboard 解析器会把更短的当占位符丢行
    # （测试夹具的老坑——解析出 0 镜时所有判据都会"全过"）。
    return {
        "index": i, "name": "LN%02d" % i, "heading": "第1集", "line": i,
        "visual": "0-4秒：@沈砚 侧身左移半步拔剑横端、剑尖斜指对方出画处右肋、暗赤雷光炸开一掌宽 第%d" % i,
        "dialogue": "（无声，环境音）" if i % 3 else "最后一壶，喝完就动手",
        "seconds": 4, "shot_type": "中景", "angle": "平视", "camera": "缓推",
        "scene": "崖顶石台", "visual_style": "黄昏逆光", "tail": "剑尖前点",
        "text_shot": "否", "join_note": "承接上一镜落点", "sfx": "风声",
    }


class _FakeImage:
    """替身生图：url 只由 prompt 决定（与 key 无关）⇒ 串行/并发可逐字比对。"""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, prompt, refs=None, ratio=None, timeout=120, key=None):
        fp = hashlib.md5(prompt.encode("utf-8")).hexdigest()[:8]
        with self.lock:
            self.calls.append((fp, key))
        return "fake.jpg", "https://cdn/%s" % fp


class TestImagePool(unittest.TestCase):
    def test_gate_is_60_over_image_rpm_per_key(self):
        """三条 80rpm 的 key ⇒ 每条闸门 0.75 秒，**不是**视频那边的 12 秒。

        图片额度与视频额度在供应商侧是分开的；拿视频闸门管生图，
        等于把 80rpm 的通道当 5rpm 用（并发 3 条也白搭）。
        """
        from v5 import config
        from v5.media import keypool
        with mock.patch.object(config, "image_pool_keys", return_value=["a", "b", "c"]), \
                mock.patch.object(config, "AGNES_KEY_IMAGE_RPM",
                                  {"a": 80, "b": 80, "c": 80}):
            p = keypool.KeyPool.image_pool()
            self.assertEqual(len(p), 3)
            self.assertEqual([round(x, 2) for x in p._intervals], [0.75, 0.75, 0.75])

    def test_no_image_rpm_marks_falls_back_to_single_key(self):
        """没标图片 rpm ⇒ 池里只有池第一条 ⇒ 并发自动退化成改造前的串行行为。"""
        from v5 import config
        from v5.media import keypool
        with mock.patch.object(config, "AGNES_API_KEYS", ["only"]), \
                mock.patch.object(config, "AGNES_API_KEY", "only"), \
                mock.patch.object(config, "AGNES_KEY_IMAGE_RPM", {}):
            p = keypool.KeyPool.image_pool()
            self.assertEqual(len(p), 1)
            self.assertEqual(p.keys, ["only"])

    def test_workers_default_follows_pool_and_caps(self):
        from v5 import config
        for n, want in ((1, 1), (3, 3), (9, config.IMAGE_WORKERS_CAP)):
            with mock.patch.object(config, "image_pool_keys",
                                   return_value=["k%d" % i for i in range(n)]), \
                    mock.patch.dict("os.environ", {}, clear=True):
                self.assertEqual(config.image_workers(), want, "%d 条 key" % n)

    def test_workers_one_means_serial(self):
        """`SHORTDRAMA_IMAGE_WORKERS=1` 是回退开关：整条路退回逐张发。"""
        from v5 import config
        with mock.patch.object(config, "image_pool_keys", return_value=["a", "b", "c"]), \
                mock.patch.dict("os.environ", {"SHORTDRAMA_IMAGE_WORKERS": "1"}):
            self.assertEqual(config.image_workers(), 1)

    def test_workers_never_exceed_available_keys(self):
        """显式写 8 但只有 2 条 key ⇒ 压回 2（同一条 key 自我限速不是提速）。"""
        from v5 import config
        with mock.patch.object(config, "image_pool_keys", return_value=["a", "b"]), \
                mock.patch.dict("os.environ", {"SHORTDRAMA_IMAGE_WORKERS": "8"}):
            self.assertEqual(config.image_workers(), 2)


class TestStillsParallelAndSerialAgree(unittest.TestCase):
    def _run(self, workers: int, shots, fake, root: Path):
        from v5 import config
        from v5.media import providers, stills
        with mock.patch.object(providers, "gen_image", fake), \
                mock.patch.object(stills, "_download",
                                  lambda url, p: Path(p).write_bytes(b"JPG")), \
                mock.patch.object(config, "image_pool_keys",
                                  return_value=["k1", "k2", "k3"]), \
                mock.patch.object(config, "AGNES_KEY_IMAGE_RPM",
                                  {"k1": 6000, "k2": 6000, "k3": 6000}), \
                mock.patch.object(config, "image_workers", return_value=workers):
            return stills.ensure(root, shots, log=lambda *_: None)

    def test_serial_and_parallel_produce_identical_manifest(self):
        """★ 核心：并发不能改变产出。同 prompt、同 url、同条数，逐字比。"""
        shots = [_shot(i) for i in range(1, 7)]
        with TemporaryDirectory() as d1, TemporaryDirectory() as d2:
            serial = self._run(1, shots, _FakeImage(), Path(d1))
            par = self._run(3, shots, _FakeImage(), Path(d2))
        self.assertEqual(len(serial), 6, "串行必须真产出 6 条（0 条会让断言全绿）")
        self.assertEqual(len(par), 6)
        self.assertEqual(sorted(serial), sorted(par))
        for name in serial:
            self.assertEqual(serial[name]["url"], par[name]["url"], name)
            self.assertEqual(serial[name]["prompt"], par[name]["prompt"],
                             "%s 的提示词在并发下变了" % name)

    def test_parallel_actually_spreads_across_keys(self):
        """并发真的用上了多条 key（不是 3 个线程挤同一条）。"""
        fake = _FakeImage()
        with TemporaryDirectory() as d:
            out = self._run(3, [_shot(i) for i in range(1, 7)], fake, Path(d))
        self.assertEqual(len(out), 6)
        used = {k for _fp, k in fake.calls if k}
        self.assertGreaterEqual(len(used), 2, "6 张只打在 1 条 key 上 = 并发没生效")

    def test_failed_shot_is_loud_and_others_survive(self):
        """★ 缺镜不许伪装成成功：失败的那镜**不进清单**，其余照常。

        同型事故：`gen_all_stills.py` 对 6 镜返回 504 却打印「完成 32/32」。
        """
        shots = [_shot(i) for i in range(1, 7)]

        # 第 3 镜永远失败（按正文里的镜尾标记命中），其余正常返回 url
        def boom(prompt, refs=None, ratio=None, timeout=120, key=None):
            if "第3" in prompt:
                raise RuntimeError("read operation timed out")
            return "f.jpg", "https://cdn/" + hashlib.md5(prompt.encode()).hexdigest()[:8]

        with TemporaryDirectory() as d:
            out = self._run(3, shots, boom, Path(d))
        self.assertEqual(len(out), 5, "6 镜里 1 镜失败 ⇒ 清单必须只有 5 条，不能伪 6")
        self.assertNotIn("LN03", out)

    def test_rate_limit_retries_on_another_key_and_succeeds(self):
        """撞 429 的那条 key 停一轮，别的 key 顶上 ⇒ 该镜仍要画出来。"""
        shots = [_shot(i) for i in range(1, 5)]
        state = {"n": 0}

        def flaky(prompt, refs=None, ratio=None, timeout=120, key=None):
            from v5.media import providers
            state["n"] += 1
            if state["n"] == 1:
                raise providers.RateLimitError("image 429")
            return "f.jpg", "https://cdn/" + hashlib.md5(prompt.encode()).hexdigest()[:8]

        with TemporaryDirectory() as d:
            out = self._run(3, shots, flaky, Path(d))
        self.assertEqual(len(out), 4, "首次 429 之后必须重试成功，不能丢镜")
        self.assertEqual(state["n"], 5, "4 镜 + 1 次 429 重试 = 5 次调用，多一次就是重复烧图")


if __name__ == "__main__":
    unittest.main(verbosity=2)
