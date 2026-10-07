# -*- coding: utf-8 -*-
"""资产图查字闸门（Stage C）的回归测试。

判据来源：10-07 两轮实跑。命案集的场景卡自带一排红字 ⇒ 直接喂视频后成片 ≥4 镜有字；
仙侠集八张图干净 ⇒ 60 秒一帧无字。闸门失效必须是**看得见**的，所以这几条要钉住：
  ① 只有过了 `qc.is_hard_issue` 且句子命中文字类键的才算带字；
  ② 否定语境（"无字幕"）与别的轴上的"字"不能算（反向对照）；
  ③ 按文件指纹缓存 ⇒ 同一张图第二次**不再问模型**；
  ④ 剔图时 `urls` 与 `roles` 必须同步剔（错位会让提示词点名点到隔壁那张图）。
"""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5.media import sheettext, video  # noqa: E402


def _issue(level, desc):
    return {"level": level, "desc": desc}


class JudgeText(unittest.TestCase):
    def test_positive_hits(self):
        for desc in ("背景墙面出现成行红色文字",
                     "招牌上有可读字符",
                     "画面底部烧录字幕条"):
            hit, why = sheettext.has_text_issue([_issue("P0", desc)])
            self.assertTrue(hit, desc)
            self.assertIn(desc[:6], why)

    def test_negative_controls_not_counted(self):
        # P1 不算（只记不改的那条通道）
        self.assertFalse(sheettext.has_text_issue([_issue("P1", "背景有文字")])[0])
        # 否定语境不算（"无字幕"是 QC 的正常措辞，不是缺陷）
        self.assertFalse(sheettext.has_text_issue(
            [_issue("P0", "画面干净，无字幕、无水印")])[0])
        # 别的轴上的"字"不算
        self.assertFalse(sheettext.has_text_issue(
            [_issue("P0", "低分辨率重复贴图")])[0])
        # 空/坏结构不炸
        self.assertFalse(sheettext.has_text_issue([])[0])
        self.assertFalse(sheettext.has_text_issue([{"desc": "文字"}])[0])


class ScanAndFilter(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sheettext_"))
        (self.root / "images").mkdir(parents=True, exist_ok=True)
        (self.root / "media" / "ep1").mkdir(parents=True, exist_ok=True)
        for nm in ("天台水塔间", "验尸房"):
            (self.root / "images" / (nm + ".png")).write_bytes(b"\x89PNG fake")
        cards = [{"name": "天台水塔间", "type": "location", "ref_image": "天台水塔间.png",
                  "public_url": "https://cdn/x/1.png", "url": ""},
                 {"name": "验尸房", "type": "location", "ref_image": "验尸房.png",
                  "public_url": "https://cdn/x/2.png", "url": ""}]
        json.dump({"assets": cards},
                  io.open(self.root / "assets.json", "w", encoding="utf-8"),
                  ensure_ascii=False)

    def _stub(self, bad_names):
        calls = {"n": 0}

        def fake_review(path, **kw):
            calls["n"] += 1
            nm = Path(path).stem
            return {"issues": ([_issue("P0", "墙面出现成行红色文字")]
                               if nm in bad_names else [])}
        return fake_review, calls

    def test_caches_by_file_fingerprint(self):
        fake, calls = self._stub({"天台水塔间"})
        with mock.patch.object(sheettext.assets_mod, "auto_sync",
                               lambda r, **k: {"assets": json.loads(
                                   (self.root / "assets.json").read_text(encoding="utf-8"))["assets"]}), \
             mock.patch.object(sheettext.qc, "review", fake), \
             mock.patch.object(sheettext.assets_mod, "local_ref_path",
                               lambda r, nm: self.root / "images" / (nm + ".png")):
            bad1 = sheettext.flagged_urls(self.root, 1, log=lambda *a: None)
            self.assertEqual(bad1, {"https://cdn/x/1.png": "天台水塔间"})
            n_after_first = calls["n"]
            bad2 = sheettext.flagged_urls(self.root, 1, log=lambda *a: None)
            self.assertEqual(bad2, bad1, "缓存必须给出同一个判决")
            self.assertEqual(calls["n"], n_after_first,
                             "图没换就不该再问模型（第二次新增调用必须为 0）")

    def test_drop_flagged_keeps_roles_aligned(self):
        urls = ["u-face", "u-scene-bad", "u-prop"]
        roles = [("character", "角色「阿冷」的人物设定表"),
                 ("location", "场景「天台水塔间」的空镜"),
                 ("prop", "道具「旧机械表」")]
        kept_u, kept_r = video.drop_flagged(urls, roles, {"u-scene-bad": "天台水塔间"},
                                            "LN05", log=lambda *a: None)
        self.assertEqual(kept_u, ["u-face", "u-prop"])
        self.assertEqual([l for _k, l in kept_r],
                         ["角色「阿冷」的人物设定表", "道具「旧机械表」"],
                         "剔图必须同步剔声明，否则点名会点到隔壁那张图")

    def test_gate_off_is_loud_not_silent(self):
        lines = []
        with mock.patch.object(sheettext.config, "SHEET_TEXT_GATE", False), \
             mock.patch.object(sheettext.assets_mod, "auto_sync",
                               lambda r, **k: {"assets": json.loads(
                                   (self.root / "assets.json").read_text(encoding="utf-8"))["assets"]}), \
             mock.patch.object(sheettext.assets_mod, "local_ref_path",
                               lambda r, nm: self.root / "images" / (nm + ".png")):
            bad = sheettext.flagged_urls(self.root, 1, log=lines.append)
        self.assertEqual(bad, {}, "闸门关 = 谁都不拦")
        self.assertTrue(any("没有查过" in l for l in lines),
                        "闸门失效必须响亮，不能日志全绿：\n".join(lines))


if __name__ == "__main__":
    unittest.main(verbosity=2)
