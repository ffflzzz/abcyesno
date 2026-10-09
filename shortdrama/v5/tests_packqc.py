import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from v5.media import packqc


class TestVerdict(unittest.TestCase):
    def test_fail_needs_current_frame_evidence(self):
        for refs in ([], ["previous"], ["current9"]):
            rep = packqc._normalize({"status": "fail", "issues": [
                {"desc": "玉佩多绳", "frames": refs}]}, {"previous", "current1"})
            self.assertEqual(rep["status"], "unknown")

    def test_supported_issue_overrides_pass(self):
        rep = packqc._normalize({"status": "pass", "issues": [
            {"desc": "玉佩多绳", "frames": ["current1"]}]}, {"current1"})
        self.assertEqual(rep["status"], "fail")

    def test_empty_or_malformed_pass_is_unknown(self):
        for raw in ("pass", {}, {"status": "pass"}):
            self.assertEqual(packqc._normalize(raw, set())["status"], "unknown")

    def test_frame_failure_never_calls_model(self):
        with mock.patch.object(packqc.clipqc, "grab", return_value=[]), \
                mock.patch.object(packqc.qc, "chat_for") as model:
            rep = packqc.review(Path("clip"), None, [], {}, Path("work"))
        self.assertEqual(rep["status"], "unknown")
        model.assert_not_called()

    def test_one_model_call_with_four_images_in_chronological_order(self):
        current = [Path("c1"), Path("c2"), Path("c3")]
        prior = [Path("p1"), Path("p2"), Path("tail")]
        response = mock.Mock(content='{"status":"pass","observations":["two people"],"issues":[]}')
        model = mock.Mock()
        model.invoke.return_value = response
        with mock.patch.object(packqc.clipqc, "grab", side_effect=[current, prior]), \
                mock.patch.object(packqc.qc, "_data_uri", side_effect=lambda p: p), \
                mock.patch.object(packqc.qc, "chat_for", return_value=model):
            rep = packqc.review(Path("clip"), Path("prev"), [], {}, Path("work"))
        model.invoke.assert_called_once()
        content = model.invoke.call_args.args[0][0].content
        urls = [item["image_url"]["url"] for item in content if item["type"] == "image_url"]
        self.assertEqual(urls, ["tail", "c1", "c2", "c3"])
        self.assertEqual(rep["status"], "pass")


class TestScopeAndCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "brief.json").write_text('{"topic":"test"}')
        self.a, self.b = self.root / "pack01.mp4", self.root / "pack02.mp4"
        self.a.write_bytes(b"first")
        self.b.write_bytes(b"second")
        self.shots = [{"name": "LN01"}, {"name": "LN02"}, {"name": "LN03"}]
        self.clips = {"LN01": str(self.a), "LN02": str(self.a), "LN03": str(self.b)}

    def test_one_call_per_physical_pack_and_cache_tracks_previous_bytes(self):
        with mock.patch.object(packqc, "review", return_value={"status": "pass",
                "issues": [], "observations": ["two people"]}) as review:
            packqc.audit(self.root, self.clips, self.shots, log=lambda *_: None)
            self.assertEqual(review.call_count, 2)
            self.assertEqual(review.call_args_list[1].args[1], self.a)
            packqc.audit(self.root, self.clips, self.shots, log=lambda *_: None)
            self.assertEqual(review.call_count, 2)
            self.a.write_bytes(b"new-tail")
            packqc.audit(self.root, self.clips, self.shots, only=["LN03"], log=lambda *_: None)
            self.assertEqual(review.call_count, 3)

    def test_only_keeps_prior_context_without_paying_for_prior_review(self):
        with mock.patch.object(packqc, "review", return_value={"status": "unknown", "issues": []}) as review:
            rep = packqc.audit(self.root, self.clips, self.shots, only=["LN03"], log=lambda *_: None)
            review.assert_called_once()
            self.assertEqual(review.call_args.args[1], self.a)
            self.assertEqual(set(rep["groups"]), {"pack02"})
            packqc.audit(self.root, self.clips, self.shots, only=["LN03"], log=lambda *_: None)
            self.assertEqual(review.call_count, 2, "unknown must be retriable, not cached as pass")

    def test_exception_is_recorded_as_unknown(self):
        with mock.patch.object(packqc, "review", side_effect=RuntimeError("unavailable")):
            rep = packqc.audit(self.root, self.clips, self.shots, log=lambda *_: None)
        self.assertTrue(all(r["status"] == "unknown" for r in rep["groups"].values()))
        self.assertEqual(json.loads((self.root / "media/ep1/packqc.json").read_text())["groups"], rep["groups"])
