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

    def test_workers_default_is_cap_regardless_of_key_count(self):
        """**2026-09-30 改的契约**：自动档恒为 `IMAGE_WORKERS_CAP`，不再等于 key 数。

        旧判据（自动档 = key 数、封顶 4）的直觉是从**视频**通道搬的（1rpm、同 key
        60 秒内二次提交必 429）。图片通道标称 2K 档 80rpm，"同一条 key 多张在途"
        就是它的额定工况 —— 按 key 数封顶等于把 80rpm 的通道钉在并发 3。
        """
        from v5 import config
        for n in (1, 3, 9):
            with mock.patch.object(config, "image_pool_keys",
                                   return_value=["k%d" % i for i in range(n)]), \
                    mock.patch.dict("os.environ", {}, clear=True):
                self.assertEqual(config.image_workers(), config.IMAGE_WORKERS_CAP,
                                 "%d 条 key 时自动档该是 %d（不看 key 数）"
                                 % (n, config.IMAGE_WORKERS_CAP))

    def test_workers_one_means_serial(self):
        """`SHORTDRAMA_IMAGE_WORKERS=1` 是回退开关：整条路退回逐张发。"""
        from v5 import config
        with mock.patch.object(config, "image_pool_keys", return_value=["a", "b", "c"]), \
                mock.patch.dict("os.environ", {"SHORTDRAMA_IMAGE_WORKERS": "1"}):
            self.assertEqual(config.image_workers(), 1)

    def test_explicit_value_is_not_clamped_to_key_count(self):
        """显式写 12、只有 2 条 key ⇒ **采纳 12**（旧行为是压回 2）。

        保护低 rpm key 的是**闸门**不是线程数，见下一条。
        """
        from v5 import config
        with mock.patch.object(config, "image_pool_keys", return_value=["a", "b"]), \
                mock.patch.dict("os.environ", {"SHORTDRAMA_IMAGE_WORKERS": "12"}):
            self.assertEqual(config.image_workers(), 12)

    def test_low_rpm_key_is_protected_by_its_gate_not_by_thread_count(self):
        """★ 上一条为什么安全：1rpm 的 key（4K 档）闸门 = 60 秒，线程再多也在
        `claim()` 上排队，撞不出 429 风暴。这条把"保护在闸门"这件事钉住——
        否则放开线程数上限就等于放开限速。
        """
        from v5 import config
        from v5.media import keypool
        with mock.patch.object(config, "image_pool_keys", return_value=["slow"]), \
                mock.patch.object(config, "AGNES_KEY_IMAGE_RPM", {"slow": 1}):
            p = keypool.KeyPool.image_pool()
            self.assertEqual([round(x) for x in p._intervals], [60.0])


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

    def test_workers_above_key_count_draws_every_shot(self):
        """★ 并发度 > key 数（1 条 key 发 8 张）必须**一张不丢、一张不重**。

        这是放开 `image_workers()` 后新增的工况：旧代码不可能出现（线程数被压在
        key 数以下）。8 张都挤在一条 key 上排队提交，若 `claim()` 的记账或清单
        合并有并发缺陷，最先丢的就是这种配置。
        """
        from v5 import config
        from v5.media import providers, stills
        fake = _FakeImage()
        with TemporaryDirectory() as d:
            with mock.patch.object(providers, "gen_image", fake), \
                    mock.patch.object(stills, "_download",
                                      lambda url, p: Path(p).write_bytes(b"JPG")), \
                    mock.patch.object(config, "image_pool_keys", return_value=["only"]), \
                    mock.patch.object(config, "AGNES_KEY_IMAGE_RPM", {"only": 6000}), \
                    mock.patch.object(config, "image_workers", return_value=8):
                out = stills.ensure(Path(d), [_shot(i) for i in range(1, 9)],
                                    log=lambda *_: None)
        self.assertEqual(len(out), 8, "8 镜必须产出 8 条")
        self.assertEqual(len(fake.calls), 8, "调用次数 = 镜数（多一次就是重复烧图）")
        self.assertEqual({k for _fp, k in fake.calls}, {"only"})

    def test_per_shot_qc_extra_lands_only_on_its_own_prompt(self):
        """★ `_qc_extra` 逐镜生效：**A 镜的定向补充语不得混进 B 镜**。

        为什么单独锁这条：QC 重画一批多镜时，每镜的硬伤类别不同（缺字 / 分屏 /
        未归类），补充语靠镜对象自带的 `_qc_extra` 传。批量入口只有一个全局
        `extra`——若有人图省事把整批用同一份 `extra`，缺字镜会被追加反分屏句、
        而未归类镜会被别人的句子污染，且**日志全绿**。
        """
        from v5 import config
        from v5.media import providers, stills
        prompts: dict = {}

        def rec(prompt, refs=None, ratio=None, timeout=120, key=None):
            fp = hashlib.md5(prompt.encode("utf-8")).hexdigest()[:8]
            for i in (1, 2, 3):
                if prompt.endswith("补充语LN%02d" % i):
                    prompts["LN%02d" % i] = prompt
            return "f.jpg", "https://cdn/" + fp

        shots = []
        for i in (1, 2, 3):
            s = _shot(i)
            s["_qc_extra"] = "补充语LN%02d" % i
            shots.append(s)
        with TemporaryDirectory() as d:
            with mock.patch.object(providers, "gen_image", rec), \
                    mock.patch.object(stills, "_download",
                                      lambda url, p: Path(p).write_bytes(b"JPG")), \
                    mock.patch.object(config, "image_pool_keys", return_value=["k1"]), \
                    mock.patch.object(config, "AGNES_KEY_IMAGE_RPM", {"k1": 6000}), \
                    mock.patch.object(config, "image_workers", return_value=3):
                out = stills.ensure(Path(d), shots, log=lambda *_: None)
        self.assertEqual(len(out), 3, "3 镜必须真产出 3 条（0 条会让断言全绿）")
        self.assertEqual(sorted(prompts), ["LN01", "LN02", "LN03"], "三镜各自的补充语没被记录")
        for name, p in prompts.items():
            for other in ("LN01", "LN02", "LN03"):
                if other != name:
                    self.assertNotIn("补充语%s" % other, p,
                                     "%s 的提示词串进了 %s 的补充语" % (name, other))


if __name__ == "__main__":
    unittest.main(verbosity=2)
