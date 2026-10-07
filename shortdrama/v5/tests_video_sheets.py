# -*- coding: utf-8 -*-
"""视频请求改喂资产图（2026-10-07 新默认）的回归测试。

判据来自两轮实跑（`madfate-abc-1005-nostill` 15 镜、`xianxia-zhongzhui-1007-nostill`
6 镜）。这里只锁四件机器可判的事：
  ① 默认档下实发图清单里**不得出现静帧 URL**；
  ② 槽位顺序 = 角色设定表 → 场景空镜 → 道具，总数 ≤3（图数 > 人数会多画人）；
  ③ 场景名歧义时**谁都不绑**（宁少绑不绑错）；
  ④ 显式设 `SHORTDRAMA_VIDEO_REF_SOURCE=stills` 时行为与改造前一字不变。
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5.media import assets, prompt, video_plan  # noqa: E402

STILL_URL = "https://cdn.example/stills/LN01.png"


def _mk_project(root: Path):
    """一份最小可用的项目：注册表 + 图 + .url 伴侣。"""
    (root / "images").mkdir(parents=True, exist_ok=True)
    cards = [
        {"id": "a", "name": "云笳", "type": "character", "keywords": ["云笳"],
         "ref_image": "云笳.png"},
        {"id": "b", "name": "沈砚", "type": "character", "keywords": ["沈砚"],
         "ref_image": "沈砚.png"},
        {"id": "c", "name": "断剑坪·坪内", "type": "location",
         "keywords": ["断剑坪", "白玉平台", "云海"], "ref_image": "断剑坪·坪内.png"},
        {"id": "d", "name": "断剑坪·坪西石桥", "type": "location",
         "keywords": ["石桥", "桥下云海"], "ref_image": "坪西石桥.png"},
        {"id": "e", "name": "断刃·赤纹", "type": "prop",
         "keywords": ["断刃·赤纹"], "ref_image": "断刃·赤纹.png"},
    ]
    for i, a in enumerate(cards, start=1):
        url = "https://cdn.example/sheets/%d.png" % i
        (root / "images" / a["ref_image"]).write_bytes(b"\x89PNG fake")
        with io.open(root / "images" / (a["ref_image"] + ".url"), "w",
                     encoding="utf-8") as f:
            f.write(url)
        a["public_url"] = ""
        a["url"] = ""
    with io.open(root / "assets.json", "w", encoding="utf-8") as f:
        json.dump({"assets": cards}, f, ensure_ascii=False)
    return {c["name"]: "https://cdn.example/sheets/%d.png" % i
            for i, c in enumerate(cards, start=1)}


def _shot(**kw):
    s = {"name": "LN01", "visual": "@云笳（玄青劲装）持断刃·赤纹劈下", "dialogue": "",
         "scene": "断剑坪·坪内", "scene_col": "断剑坪·坪内", "shot_type": "全景",
         "seconds": 12}
    s.update(kw)
    return s


class SheetsForShot(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="sheets_"))
        self.urls = _mk_project(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_order_is_face_scene_prop_and_never_a_still(self):
        urls, roles = assets.sheets_for_shot(self.root, _shot(), ep=1)
        self.assertEqual([k for k, _l in roles], ["character", "location", "prop"],
                         "槽位顺序必须是 脸→场景→道具：%s" % roles)
        self.assertEqual(urls, [self.urls["云笳"], self.urls["断剑坪·坪内"],
                                self.urls["断刃·赤纹"]])
        self.assertNotIn(STILL_URL, urls, "本档的定义就是不含静帧")
        self.assertLessEqual(len(urls), 3, "图数封顶 3：超过分镜人数会多画一个人")

    def test_ambiguous_scene_binds_neither(self):
        # 「云海」同时是两张场景卡的关键词 ⇒ 谁都不绑，只报一行
        urls, roles = assets.sheets_for_shot(
            self.root, _shot(scene="云海", scene_col="云海"), ep=1)
        self.assertNotIn("location", [k for k, _l in roles])
        none, why = assets.scene_asset_for_shot(
            {"assets": [{"type": "location", "name": "A", "keywords": ["云海"]},
                        {"type": "location", "name": "B", "keywords": ["云海"]}]},
            _shot(scene="云海", scene_col="云海"))
        self.assertIsNone(none)
        self.assertIn("歧义", why)
        self.assertTrue(urls, "场景不绑，但人脸与道具照旧要绑上")

    def test_scene_lookup_reads_the_table_column_not_the_derived_one(self):
        # `relations.plan_frames` 会用标题覆写 `scene` —— 只读 `scene` 就会全部落空
        shot = _shot(scene="## 第 1 场：断剑坪·钟坠（云海之上）",
                     scene_col="断剑坪·坪内")
        _urls, roles = assets.sheets_for_shot(self.root, shot, ep=1)
        self.assertIn("location", [k for k, _l in roles])


class PlanAndPrompt(unittest.TestCase):
    def test_default_plan_is_sheets(self):
        self.assertTrue(video_plan.VideoPlan.of("reference").from_sheets)
        self.assertFalse(video_plan.VideoPlan.of("keyframe").from_sheets)

    def test_declaration_swapped_when_sheets(self):
        shot = _shot(_style_block="", _scene_line="")
        plain = prompt.build_video_prompt(dict(shot), None, mode="reference")
        roles = [("character", "角色「云笳」的人物设定表"),
                 ("location", "场景「断剑坪·坪内」的空镜（只锁建筑与地貌，不锁机位）")]
        with_roles = prompt.build_video_prompt(dict(shot), None, mode="reference",
                                               ref_roles=roles)
        self.assertIn(prompt.REF_USAGE_ZH, plain, "旧路径那句声明必须原样在")
        self.assertNotIn(prompt.REF_USAGE_ZH, with_roles,
                         "喂资产图时再说一次就是句假话，必须换掉")
        self.assertIn("第 1 张参考图=角色「云笳」的人物设定表", with_roles)
        self.assertIn("第 2 张参考图=场景", with_roles)


class StillsFallback(unittest.TestCase):
    """`SHORTDRAMA_VIDEO_REF_SOURCE=stills` 时行为与改造前一字不变。"""

    def test_knob_off_restores_old_behaviour(self):
        os.environ["SHORTDRAMA_VIDEO_REF_SOURCE"] = "stills"
        try:
            import importlib
            from v5 import config
            importlib.reload(config)
            importlib.reload(video_plan)
            self.assertFalse(video_plan.VideoPlan.of("reference").from_sheets)
        finally:
            del os.environ["SHORTDRAMA_VIDEO_REF_SOURCE"]
            import importlib
            from v5 import config
            importlib.reload(config)
            importlib.reload(video_plan)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class HasSheets(unittest.TestCase):
    """媒体链决定"能不能不画静帧"的判据：注册表里至少有一张能解析出 URL 的图。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="hassheets_"))
        (self.root / "images").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_empty_registry_is_false(self):
        with io.open(self.root / "assets.json", "w", encoding="utf-8") as f:
            json.dump({"assets": []}, f)
        self.assertFalse(assets.has_sheets(self.root))

    def test_registered_card_without_any_image_is_false(self):
        with io.open(self.root / "assets.json", "w", encoding="utf-8") as f:
            json.dump({"assets": [{"name": "甲", "type": "character",
                                   "ref_image": "甲.png", "public_url": "", "url": ""}]},
                      f)
        self.assertFalse(assets.has_sheets(self.root),
                         "卡登记了但图没生成 ⇒ 喂不出东西，必须退回静帧")

    def test_card_with_url_sidecar_is_true(self):
        with io.open(self.root / "images" / "甲.png.url", "w", encoding="utf-8") as f:
            f.write("https://cdn/x/甲.png")
        (self.root / "images" / "甲.png").write_bytes(b"\x89PNG fake")
        with io.open(self.root / "assets.json", "w", encoding="utf-8") as f:
            json.dump({"assets": [{"name": "甲", "type": "character",
                                   "ref_image": "甲.png", "public_url": "", "url": ""}]},
                      f)
        self.assertTrue(assets.has_sheets(self.root))


class NeedsStills(unittest.TestCase):
    """`from_sheets`（逐镜请求吃什么）与 `needs_stills`（这档离不离得开静帧）**不等价**。

    媒体链"要不要画静帧"只看后者。写错成前者的后果不是质量下降，是**整档出不了片**：
    pack 少掉「本组首镜静帧」这张场景实现，mixed 的承接镜拿到 `first=None` 直接判失败。
    """

    def test_only_mixed_and_keyframe_still_need_stills(self):
        # 2026-10-07：reference 与 pack 都不再吃静帧；只有 mixed 的 keyframe 那半边
        # 与 keyframe 档还把静帧当 first_frame。
        for mode, (sheets, needs) in {
                "reference": (True, False),
                "pack": (True, False),        # 图序：设定表→场景→上一段末帧→道具
                "mixed": (True, True),        # 承接镜走 keyframe，首帧=本镜静帧
                "keyframe": (False, True)}.items():
            p = video_plan.VideoPlan.of(mode)
            self.assertEqual((p.from_sheets, p.needs_stills), (sheets, needs), mode)

    def test_stills_knob_keeps_reference_needing_stills(self):
        import importlib
        import os
        from v5 import config
        os.environ["SHORTDRAMA_VIDEO_REF_SOURCE"] = "stills"
        try:
            importlib.reload(config)
            importlib.reload(video_plan)
            p = video_plan.VideoPlan.of("reference")
            self.assertFalse(p.from_sheets)
            self.assertTrue(p.needs_stills, "回退档必须自己把静帧要回来，否则无图可喂")
        finally:
            del os.environ["SHORTDRAMA_VIDEO_REF_SOURCE"]
            importlib.reload(config)
            importlib.reload(video_plan)


class PackDropsStills(unittest.TestCase):
    """pack 档 2026-10-07 起同样不吃静帧：图 = 设定表 + 场景空镜 + 道具 + 上一段**成片末帧**。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="packsheets_"))
        _mk_project(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_no_still_url_and_scene_comes_from_the_table_column(self):
        from v5.media import video
        group = [{"name": "LN01", "scene": "断剑坪·坪内", "scene_col": "断剑坪·坪内",
                  "visual": "@云笳（玄青劲装）持@断刃·赤纹 落下", "dialogue": "",
                  "shot_type": "中景", "seconds": 6},
                 {"name": "LN02", "scene": "断剑坪·坪内", "scene_col": "断剑坪·坪内",
                  "visual": "@沈砚 抬@青霜双鞭 相迎", "dialogue": "",
                  "shot_type": "中景", "seconds": 6}]
        urls, roles = video.pack_ref_images(self.root, group, ep=1)
        kinds = [k for k, _l in roles]
        self.assertNotIn("shot", kinds, "「本组首镜静帧」这一格必须已经没了")
        self.assertIn("location", kinds, "场景按表列无条件取（不再只绑宽景）")
        for u in urls:
            self.assertNotIn("/stills/", u, "pack 的图里不许出现静帧地址")
        self.assertLessEqual(len(urls), 5)

    def test_seam_anchor_has_no_still_fallback(self):
        import inspect
        from v5.media import video
        params = list(inspect.signature(video.seam_anchor).parameters)
        self.assertEqual(params, ["clip_dir", "prev_pname"],
                         "锚帧只有一来源（上一组成片末帧）；参数里再出现 stills 就是回退了")
