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
        self.assertEqual(ids, [config.MODELS["image"], config.MODELS["chat"], config.MODELS["video"]])
        self.assertNotIn("agnes-audio", ids)


class TestKeyFallback(unittest.TestCase):
    """★ 代理必须真的用池子里的多条 key。

    只传 `key=None` 的话永远用第一条 —— 那"走后端就能用上多 key"就是假话。
    """

    def setUp(self):
        self._pool = aigc.config.AGNES_API_KEYS
        self.seen: list[str] = []

    def tearDown(self):
        aigc.config.AGNES_API_KEYS = self._pool

    def test_rotates_to_next_key_on_rate_limit(self):
        aigc.config.AGNES_API_KEYS = ["k1", "k2", "k3"]
        orig = aigc.providers.gen_image

        def fake(prompt, refs=None, ratio=None, key=None, timeout=120):
            self.seen.append(key)
            if key in ("k1", "k2"):
                raise aigc.providers.RateLimitError("image 429")
            return "", "ok"
        aigc.providers.gen_image = fake
        self.addCleanup(setattr, aigc.providers, "gen_image", orig)
        _u, url = aigc.gen_with_fallback(aigc.providers.gen_image, "猫")
        self.assertEqual(url, "ok")
        self.assertEqual(self.seen, ["k1", "k2", "k3"], "应当逐条试到能用为止")

    def test_raises_after_pool_exhausted(self):
        aigc.config.AGNES_API_KEYS = ["k1", "k2"]
        orig = aigc.providers.gen_image

        def always429(prompt, refs=None, ratio=None, key=None, timeout=120):
            raise aigc.providers.RateLimitError("image 429")
        aigc.providers.gen_image = always429
        self.addCleanup(setattr, aigc.providers, "gen_image", orig)
        with self.assertRaises(aigc.providers.RateLimitError):
            aigc.gen_with_fallback(aigc.providers.gen_image, "猫")

    def test_generate_images_goes_through_the_fallback(self):
        """生图入口不许退回 `key=None`（那等于只用第一条）。"""
        aigc.config.AGNES_API_KEYS = ["k1", "k2"]
        orig = aigc.providers.gen_image
        seen: list[str | None] = []

        def fake(prompt, refs=None, ratio=None, key=None, timeout=120):
            seen.append(key)
            return "", "u"
        aigc.providers.gen_image = fake
        self.addCleanup(setattr, aigc.providers, "gen_image", orig)
        aigc.generate_images({"prompt": "猫"})
        self.assertEqual(seen, ["k1"], "必须显式传 key，而不是让 providers 兜默认")


class TestEdits(unittest.TestCase):
    def setUp(self):
        self.calls: list[dict[str, Any]] = []
        self._orig = aigc.providers.gen_image

        def fake(prompt, refs=None, ratio=None, key=None, timeout=120):
            self.calls.append({"refs": refs, "ratio": ratio})
            return "", "u"
        aigc.providers.gen_image = fake
        self.addCleanup(setattr, aigc.providers, "gen_image", self._orig)

    def test_refs_reach_the_provider(self):
        aigc.edit_images({"prompt": "改夜景", "image": ["data:image/png;base64,AAA"],
                          "size": "1024x1792"})
        self.assertEqual(self.calls[0]["refs"], ["data:image/png;base64,AAA"])
        self.assertEqual(self.calls[0]["ratio"], "9:16")

    def test_input_reference_key_is_also_accepted(self):
        """视频/图生图那侧的字段名是 `input_reference`，别只认 `image`。"""
        aigc.edit_images({"prompt": "改夜景", "input_reference": ["data:image/png;base64,B"]})
        self.assertEqual(self.calls[0]["refs"], ["data:image/png;base64,B"])

    def test_no_reference_is_an_error_not_a_text2image(self):
        """★ 没参考图时**不能**悄悄退回文生图 —— 那会出"看着成功、其实没按图改"的图。"""
        with self.assertRaises(ValueError):
            aigc.edit_images({"prompt": "改夜景"})


class TestChat(unittest.TestCase):
    def test_multimodal_content_collapses_to_text(self):
        msgs = aigc._chat_messages({"messages": [
            {"role": "user", "content": [{"type": "text", "text": "写一句"},
                                         {"type": "image_url", "image_url": {"url": "x"}}]}]})
        self.assertEqual(len(msgs), 1)
        self.assertIn("写一句", msgs[0]["content"])
        self.assertIn("1 段图片未被使用", msgs[0]["content"],
                      "丢了图片段必须**说出来**，不能静默当文本请求")

    def test_empty_messages_raise(self):
        with self.assertRaises(ValueError):
            aigc._chat_messages({"messages": []})

    def test_sse_shape(self):
        orig = aigc.chat_completion
        aigc.chat_completion = lambda p: {"id": "c1", "created": 1, "model": "m",
                                          "choices": [{"index": 0, "finish_reason": "stop",
                                                       "message": {"role": "assistant",
                                                                   "content": "你好"}}]}
        self.addCleanup(setattr, aigc, "chat_completion", orig)
        s = aigc.chat_sse({})
        self.assertTrue(s.startswith("data: "), "画布按 SSE 读，首行必须是 data:")
        self.assertTrue(s.rstrip().endswith("data: [DONE]"), "必须以 [DONE] 收尾，否则前端一直等")
        self.assertIn("你好", s)


class TestVideo(unittest.TestCase):
    def setUp(self):
        self._orig_submit = aigc.providers.submit_video
        self._orig_query = aigc.providers.query_video
        self._pool = aigc.config.AGNES_API_KEYS
        aigc.config.AGNES_API_KEYS = ["k1", "k2"]
        aigc._VIDEO_OWNER.clear()

    def tearDown(self):
        aigc.providers.submit_video = self._orig_submit
        aigc.providers.query_video = self._orig_query
        aigc.config.AGNES_API_KEYS = self._pool
        aigc._VIDEO_OWNER.clear()

    def test_reference_images_switch_mode_and_id_is_returned(self):
        seen: list[dict[str, Any]] = []

        def fake_submit(prompt, **kw):
            seen.append(kw)
            return {"video_id": "v9", "task_id": "t9"}
        aigc.providers.submit_video = fake_submit
        r = aigc.submit_video_task({"prompt": "猫走过", "seconds": "8",
                                    "input_reference": ["data:image/png;base64,Z"]})
        self.assertEqual(r["id"], "v9")
        self.assertEqual(seen[0]["mode"], "reference")
        self.assertEqual(seen[0]["seconds"], 8)

    def test_no_reference_is_rejected_not_sent_as_text_mode(self):
        """★ 供应商没有"纯文字生视频"：keyframe 要首帧、reference 要参考图。

        `providers.submit_video` 的报错原先写着"仅 text/keyframe/reference"，
        而代码里**没有 text 分支** —— 我照那句写了 `mode="text"`，实测被拒。
        文案已改回与代码一致；代理这边直接要参考图，别把内部术语抛给用户。
        """
        with self.assertRaises(ValueError) as cm:
            aigc.submit_video_task({"prompt": "猫走过"})
        self.assertIn("参考图", str(cm.exception))

    def test_poll_uses_the_same_key_that_submitted(self):
        """★ 硬约束：跨 key 查会查不到（`providers.submit_video` 文档写明）。"""
        aigc.providers.submit_video = lambda prompt, **kw: {"video_id": "v7"}
        aigc.submit_video_task({"prompt": "猫", "input_reference": ["data:image/png;base64,Z"]})
        qkey: list[str | None] = []
        aigc.providers.query_video = lambda vid, key=None, timeout=30: (
            qkey.append(key), {"status": "processing", "url": None})[1]
        aigc.video_status("v7")
        self.assertEqual(qkey, ["k1"], "查询必须回用提交那条 key")

    def test_url_means_completed(self):
        aigc.providers.query_video = lambda vid, key=None, timeout=30: {
            "status": "succeeded", "url": "https://x/y.mp4", "progress": 100}
        out = aigc.video_status("v1")
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["url"], "https://x/y.mp4")

    def test_failed_carries_message(self):
        aigc.providers.query_video = lambda vid, key=None, timeout=30: {
            "status": "failed", "error": "模型不支持该分辨率"}
        out = aigc.video_status("v1")
        self.assertEqual(out["status"], "failed")
        self.assertIn("分辨率", out["error"]["message"])

    def test_lost_mapping_retries_the_pool_instead_of_claiming_not_found(self):
        """服务重启后映射丢了 ⇒ 逐条 key 试，别直接报"任务不存在"。"""
        tried: list[str] = []

        def fake(vid, key=None, timeout=30):
            tried.append(key or "")
            if key != "k2":
                raise RuntimeError("查不到")
            return {"status": "succeeded", "url": "u"}
        aigc.providers.query_video = fake
        out = aigc.video_status("v-orphan")
        self.assertEqual(out["status"], "completed")
        self.assertEqual(tried, ["k1", "k2"])


class TestResponseShape(unittest.TestCase):
    def test_openai_shape(self):
        r = aigc.image_response(["u1", "u2"])
        self.assertEqual([d["url"] for d in r["data"]], ["u1", "u2"])
        self.assertIsInstance(r["created"], int)


if __name__ == "__main__":
    unittest.main(verbosity=2)
