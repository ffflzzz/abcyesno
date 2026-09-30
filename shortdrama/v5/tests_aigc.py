# -*- coding: utf-8 -*-
"""`v5.aigc`（画布 → 后端 → 模型 的同源代理）的离线测试。

不打网络：真正出图那一步用桩替掉，只验**代理自己该做对的事**——
比例折算、张数上限、多模态提示词取文本、错误不静默。
"""
from __future__ import annotations

import unittest
from typing import Any

from . import aigc, config


class TestRatioFolding(unittest.TestCase):
    """★ 核心判据：画布发的像素串必须折到官方比例档位上。

    实测 Agnes **忽略**像素 size（画布发 `1024x1792`、后端发 `1K`+`ratio:9:16`，
    两边出图完全一样 736×1312）⇒ 照原样转发的话，画布里的"画幅"选择器是假开关。
    """

    def test_pixel_strings_fold_to_nearest_official_ratio(self):
        cases = {"1024x1792": "9:16", "1792x1024": "16:9", "1024x1024": "1:1",
                 "768x1024": "3:4", "1024x768": "4:3", "832x1248": "2:3"}
        for size, want in cases.items():
            self.assertEqual(aigc.ratio_from_size(size), want, "%s 应折成 %s" % (size, want))

    def test_already_official_ratio_passes_through(self):
        for r in aigc.IMAGE_RATIOS:
            self.assertEqual(aigc.ratio_from_size(r), r)

    def test_garbage_or_missing_falls_back_to_project_ratio(self):
        """解析不出来就回落到项目档位，**不猜**、也不发一个供应商不认的值。"""
        for bad in ("", None, "auto", "1K", "abc", "0x0", "10x", 12):
            self.assertEqual(aigc.ratio_from_size(bad), config.STILL_RATIO,
                             "%r 不该被猜成某个比例" % bad)

    def test_folded_value_is_always_in_the_official_set(self):
        for size in ("1024x1792", "2000x500", "1x9999", "3:7", "100x101"):
            self.assertIn(aigc.ratio_from_size(size), aigc.IMAGE_RATIOS)


class TestPromptExtraction(unittest.TestCase):
    def test_plain_string(self):
        self.assertEqual(aigc._prompt_text({"prompt": " 一只猫 "}), "一只猫")

    def test_multimodal_array_keeps_only_text_parts(self):
        p = [{"type": "text", "text": "猫"}, {"type": "image_url", "image_url": {"url": "x"}},
             {"type": "text", "text": "在桌上"}]
        self.assertEqual(aigc._prompt_text({"prompt": p}), "猫 在桌上")

    def test_empty_prompt_raises_not_silently_empty(self):
        """空提示词必须抛 —— 静默返回空列表会让画布显示"成功但没图"。"""
        with self.assertRaises(ValueError):
            aigc.generate_images({"prompt": "   "})


class TestGenerate(unittest.TestCase):
    """真出图那一步打桩，只验代理自己的纪律。"""

    def setUp(self):
        self.calls: list[dict[str, Any]] = []
        self._orig = aigc.providers.gen_image

        def fake(prompt, refs=None, ratio=None, timeout=120, key=None):
            self.calls.append({"prompt": prompt, "refs": refs, "ratio": ratio})
            return "", "https://example.invalid/%d.png" % len(self.calls)
        aigc.providers.gen_image = fake
        self.addCleanup(lambda: setattr(aigc.providers, "gen_image", self._orig))

    def test_ratio_reaches_the_provider(self):
        aigc.generate_images({"prompt": "猫", "size": "1024x1792"})
        self.assertEqual(self.calls[0]["ratio"], "9:16")

    def test_n_is_honoured_but_capped(self):
        aigc.generate_images({"prompt": "猫", "n": 2})
        self.assertEqual(len(self.calls), 2)
        self.calls.clear()
        urls = aigc.generate_images({"prompt": "猫", "n": 99})
        self.assertEqual(len(self.calls), aigc.MAX_IMAGES_PER_REQUEST,
                         "误触大数不该一次烧掉几十张配额")
        self.assertEqual(len(urls), aigc.MAX_IMAGES_PER_REQUEST)

    def test_garbage_n_does_not_crash(self):
        urls = aigc.generate_images({"prompt": "猫", "n": "abc"})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(urls), 1)

    def test_provider_failure_propagates(self):
        def boom(prompt, refs=None, ratio=None, timeout=120, key=None):
            raise RuntimeError("429 too many requests")
        aigc.providers.gen_image = boom
        with self.assertRaises(RuntimeError):
            aigc.generate_images({"prompt": "猫"})


class TestModelsListing(unittest.TestCase):
    def test_only_served_models_are_listed(self):
        """列了没接线的模型 ⇒ 画布按钮"能点但永远转圈"，比不列更糟。"""
        ids = [d["id"] for d in aigc.models_payload()["data"]]
        self.assertEqual(ids, [config.MODELS["image"], config.MODELS["chat"]])
        self.assertNotIn("agnes-video-new-flash", ids)


class TestResponseShape(unittest.TestCase):
    def test_openai_shape(self):
        r = aigc.image_response(["u1", "u2"])
        self.assertEqual([d["url"] for d in r["data"]], ["u1", "u2"])
        self.assertIsInstance(r["created"], int)


if __name__ == "__main__":
    unittest.main(verbosity=2)
