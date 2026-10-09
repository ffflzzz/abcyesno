"""Identity cache and production binding regression tests; no external API."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from v5.media import assets


class IdentityRefs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "images").mkdir()
        self.a = {"name": "甲", "type": "character", "keywords": ["甲"],
                  "ref_image": "甲.png", "public_url": "https://old.example/portrait.png"}
        self.prop = {"name": "玉", "type": "prop", "keywords": ["玉"], "ref_image": "玉.png"}
        (self.root / "assets.json").write_text(json.dumps({"assets": [self.a, self.prop]}), encoding="utf-8")
        self.brief("玉：乳白色，初始在甲右手，交接后在乙手中")
        Image.new("RGB", (300, 400), "red").save(self.root / "images/甲.png")
        Image.new("RGB", (300, 400), "white").save(self.root / "images/玉.png")
        buf = io.BytesIO()
        Image.new("RGB", (300, 400), "blue").save(buf, "PNG")
        self.raw = buf.getvalue()
        self.shots = [{"name": "LN01", "visual": "@甲 递给乙 @玉", "dialogue": "", "shot_type": "近景"}]

    def brief(self, prop):
        (self.root / "brief.json").write_text(json.dumps({"key_props": [prop]}), encoding="utf-8")

    def prepare(self):
        response = Mock(content=self.raw)
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.get.return_value = response
        with patch("v5.media.providers.gen_image", return_value=("", "https://new.example/edit")) as gen, patch("httpx.Client", return_value=client):
            result = assets.prepare_identity_refs(self.root, self.shots, log=lambda _: None)
        return result, gen.call_count

    def test_prepare_reuses_cache_and_binding_ignores_old_url(self):
        before = (self.root / "images/甲.png").read_bytes()
        prop_before = (self.root / "images/玉.png").read_bytes()
        result, calls = self.prepare()
        self.assertEqual((result["created"], calls), (1, 1))
        cached = assets._clean_identity_path(self.root, self.a)
        self.assertEqual(assets._safe_ref_urls(self.a, self.root), [assets._data_uri(cached)])
        self.assertEqual(assets._resolve_one(self.a, self.root), assets._data_uri(cached))
        self.assertIn(assets._data_uri(cached), assets.bind(self.root, self.shots)["LN01"])
        result, calls = self.prepare()
        self.assertEqual((result["reused"], calls), (1, 0))
        self.assertEqual((self.root / "images/甲.png").read_bytes(), before)
        self.assertEqual((self.root / "images/玉.png").read_bytes(), prop_before)
        self.assertEqual(len(assets.auto_sync(self.root)["assets"]), 2)

    def test_changed_source_or_prop_invalidates_cache(self):
        self.prepare()
        Image.new("RGB", (300, 400), "green").save(self.root / "images/甲.png")
        self.assertIsNone(assets._clean_identity_path(self.root, self.a))
        self.prepare()
        self.brief("玉：红色，初始在甲右手，交接后在乙手中")
        self.assertIsNone(assets._clean_identity_path(self.root, self.a))

    def test_static_accessories_are_not_cleaned(self):
        self.brief("玉：固定发饰，全片佩戴")
        result, calls = self.prepare()
        self.assertEqual((result["created"], calls), (0, 0))

    def test_identity_text_drops_ownership_but_keeps_appearance(self):
        text = "甲的固定形象：黑发，白色长袍；右手托一枚玉，无兵器。"
        cleaned = assets.identity_without_scene_props(self.root, text)
        self.assertIn("黑发，白色长袍", cleaned)
        self.assertNotIn("右手托", cleaned)
        self.assertIn("无兵器", cleaned)

    def test_storyboard_can_declare_transfer_when_brief_only_describes_shape(self):
        self.brief("玉：乳白色实心圆片")
        (self.root / "scenedesigner").mkdir()
        (self.root / "scenedesigner/scenedesigner_ep1.md").write_text("| @甲 把玉递给乙 |", encoding="utf-8")
        result, calls = self.prepare()
        self.assertEqual((result["created"], calls), (1, 1))

    def test_binding_never_calls_model_and_failed_edit_keeps_original(self):
        with patch("v5.media.providers.gen_image", side_effect=RuntimeError("unavailable")) as gen:
            assets._safe_ref_urls(self.a, self.root)
            self.assertEqual(gen.call_count, 0)
            result = assets.prepare_identity_refs(self.root, self.shots, log=lambda _: None)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(gen.call_count, 1)
            self.assertIsNone(assets._clean_identity_path(self.root, self.a))

    def test_unselected_characters_and_no_human_do_not_generate(self):
        self.shots = [{"name": "LN02", "visual": "@玉", "no_human": True}]
        result, calls = self.prepare()
        self.assertEqual((result["created"], calls), (0, 0))

    def test_corrupt_cache_is_not_bound(self):
        self.prepare()
        cache = assets._clean_identity_path(self.root, self.a)
        cache.write_bytes(b"broken")
        self.assertIsNone(assets._clean_identity_path(self.root, self.a))


if __name__ == "__main__":
    unittest.main()
