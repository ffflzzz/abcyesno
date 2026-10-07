# -*- coding: utf-8 -*-
"""`v5.canvasout` 的判据测试（离线、不联网、不烧配额）。

夹具是**手写的最小项目目录**，但每条断言都对着一个真实踩过的病：
根相对地址导致破图、资产逐镜连线拉成 30 根、缺文件时静默少一列。
按本仓库纪律（见 AGENTS.md「测试夹具必须断言解析出 N 条」），
每个用例都断言**具体条数**，不写"跑通了就行"。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from . import canvasout


def _mk(root: Path, shots=(1, 2, 3, 4), packs=((1, 2), (3, 4)), *,
        with_final=True, with_assets=True, prompt_of=None):
    """造一个最小可渲染项目：静帧 + 打包任务 + 定妆照。"""
    ep = root / "media" / "ep1"
    (ep / "stills").mkdir(parents=True, exist_ok=True)
    (ep / "clips").mkdir(parents=True, exist_ok=True)
    (root / "images").mkdir(parents=True, exist_ok=True)

    stills = {}
    for n in shots:
        stills["LN%02d" % n] = {
            "path": str(ep / "stills" / ("LN%02d.jpg" % n)),
            "url": "https://example.invalid/%d.png" % n,
            "seconds": 6,
            "prompt": (prompt_of or (lambda i: "画面：阿明拿着旧铜铃站在老宅天井"))(n),
        }
    (ep / "stills.json").write_text(json.dumps(stills, ensure_ascii=False), encoding="utf-8")

    if packs:
        jobs = {}
        for gi, group in enumerate(packs, 1):
            jobs["pack%02d" % gi] = {
                "state": "completed",
                "shots": ["LN%02d" % n for n in group],
                "local": str(ep / "clips" / ("pack%02d.mp4" % gi)),
                "total_seconds": 6 * len(group),
            }
            (ep / "clips" / ("pack%02d.mp4" % gi)).write_bytes(b"\x00")
        (ep / "video_jobs.json").write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")

    if with_final:
        (ep / "episode_final.mp4").write_bytes(b"\x00")

    if with_assets:
        (root / "images" / "阿明.png").write_bytes(b"\x00")
        (root / "images" / "旧铜铃.png").write_bytes(b"\x00")
        (root / "assets.json").write_text(json.dumps({"assets": [
            {"name": "阿明", "type": "character", "ref_image": "阿明.png", "prompt": "主角"},
            {"name": "旧铜铃", "type": "prop", "ref_image": "旧铜铃.png", "prompt": "道具"},
        ]}, ensure_ascii=False), encoding="utf-8")


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "demo-canvas"
        self.root.mkdir(parents=True)

    def build(self, **kw):
        return canvasout.build(self.root, 1, base="http://127.0.0.1:8791/", **kw)

    @staticmethod
    def _of(canvas, prefix):
        return [n for n in canvas["nodes"] if n["id"].startswith(prefix)]

    @staticmethod
    def _kinds(canvas):
        out = {"资产": 0, "镜": 0, "组": 0}
        for e in canvas["connections"]:
            f = e["fromNodeId"]
            out["资产" if f.startswith("a:") else "镜" if f.startswith("s:") else "组"] += 1
        return out


class TestShape(_Case):
    def test_four_columns_and_counts(self):
        _mk(self.root)
        c = self.build()
        self.assertEqual(len(self._of(c, "s:")), 4, "4 镜应有 4 个静帧节点")
        self.assertEqual(len(self._of(c, "p:")), 2, "2 个 pack 组应有 2 个片段节点")
        self.assertEqual(len(self._of(c, "a:")), 2, "2 个资产应有 2 个定妆照节点")
        self.assertEqual(len(self._of(c, "f:")), 1)
        self.assertEqual(c["stats"]["bands"], 2, "一条带 = 一个 pack 组")
        # 镜→组 4 条、组→成片 2 条（成片存在）
        self.assertEqual(self._kinds(c)["镜"], 4)
        self.assertEqual(self._kinds(c)["组"], 2)

    def test_media_urls_are_absolute(self):
        """★ 病样本：地址写成根相对 `/media/...` 时，画布与后端不同源 ⇒ 每格破图。"""
        _mk(self.root)
        c = self.build()
        for n in c["nodes"]:
            self.assertTrue(n["metadata"]["content"].startswith("http://127.0.0.1:8791/media/"),
                            "节点 %s 的地址必须是**绝对**的，实测相对地址会解析到画布那一侧 ⇒ 破图：%s"
                            % (n["id"], n["metadata"]["content"]))

    def test_asset_links_once_per_band_not_per_shot(self):
        """★ 病样本：一条资产每镜连一根线，30 镜就 30 根（实测「侯府正门长阶」）。"""
        _mk(self.root, shots=(1, 2, 3, 4), packs=((1, 2), (3, 4)),
            prompt_of=lambda i: "全程出现 阿明 与 旧铜铃")
        c = self.build()
        self.assertEqual(self._kinds(c)["资产"], 4,
                         "2 条资产 × 2 条带 = 4 根线；出现条数说明又退回逐镜连了")

    def test_open_viewport_fits_width_with_readable_floor(self):
        _mk(self.root)
        c = self.build()
        k = c["viewport"]["k"]
        self.assertTrue(canvasout._MIN_SCALE <= k <= 1.0, "打开缩放要落在可读区间：%s" % k)
        self.assertGreaterEqual(c["viewport"]["x"], 260,
                                "视口起点要让开画布应用那条元素栏，否则资产栏被压住")


class TestMissingIsLoud(_Case):
    def test_no_jobs_reports_and_has_no_video_nodes(self):
        """只跑了静帧、没出片 ⇒ 必须报，不能画成"这集没有片段"的假象。"""
        _mk(self.root, packs=None, with_final=False)
        c = self.build()
        self.assertEqual(self._of(c, "p:"), [])
        self.assertEqual(len(self._of(c, "s:")), 4)
        self.assertTrue(any("video_jobs.json" in w for w in c["warnings"]), c["warnings"])
        self.assertTrue(any("episode_final.mp4" in w for w in c["warnings"]), c["warnings"])

    def test_pack_naming_a_missing_shot_warns(self):
        _mk(self.root)
        f = self.root / "media" / "ep1" / "video_jobs.json"
        j = json.loads(f.read_text(encoding="utf-8"))
        j["pack01"]["shots"].append("LN99")
        f.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
        c = self.build()
        self.assertTrue(any("LN99" in w for w in c["warnings"]),
                        "组点名了登记表里没有的镜 ⇒ 必须响亮，不能少画一格当没事")

    def test_asset_without_image_warns(self):
        _mk(self.root)
        f = self.root / "assets.json"
        a = json.loads(f.read_text(encoding="utf-8"))
        a["assets"].append({"name": "没图的人", "type": "character", "ref_image": "", "prompt": ""})
        f.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        c = self.build()
        self.assertEqual(len(self._of(c, "a:")), 2, "没图的资产不该凭空造一个空节点")
        self.assertTrue(any("没图的人" in w for w in c["warnings"]), c["warnings"])

    def test_empty_episode_yields_no_nodes_and_warns(self):
        (self.root / "media" / "ep1").mkdir(parents=True)
        c = self.build()
        self.assertEqual(c["nodes"], [])
        self.assertEqual(c["connections"], [])
        self.assertTrue(any("stills.json" in w for w in c["warnings"]), c["warnings"])
        # 默认档（视频吃资产图）不产静帧是**按设计**，文案要同时给出文件名与理由，
        # 否则看的人以为坏了去补画静帧。
        self.assertTrue(any("按设计不产静帧" in w for w in c["warnings"]),
                        c["warnings"])


class TestUnpackedShots(_Case):
    def test_leftover_shots_still_get_a_band(self):
        """有静帧没进任何组（只跑了静帧那一段）⇒ 不能丢格。"""
        _mk(self.root, shots=(1, 2, 3, 4), packs=((1, 2),))
        c = self.build()
        self.assertEqual(len(self._of(c, "s:")), 4, "LN03/LN04 没进组也要摆出来")
        self.assertEqual(c["stats"]["bands"], 2, "1 条组带 + 1 条未打包带")


class TestFingerprint(_Case):
    def test_fingerprint_changes_with_products_and_with_params(self):
        _mk(self.root)
        a = canvasout.fingerprint(self.root, 1)
        self.assertEqual(canvasout.fingerprint(self.root, 1), a, "同样输入要稳定")

        s = self.root / "media" / "ep1" / "stills.json"
        d = json.loads(s.read_text(encoding="utf-8"))
        d["LN01"]["seconds"] = 10
        s.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        self.assertNotEqual(canvasout.fingerprint(self.root, 1), a, "产物内容变了 ⇒ 指纹必须变")

        _mk(self.root)                                     # 复原内容
        self.assertEqual(canvasout.fingerprint(self.root, 1), a, "复原后指纹应回到原值")
        os.environ["SHORTDRAMA_VIDEO_PACK_MAX_GROUP"] = "3"
        try:
            self.assertNotEqual(canvasout.fingerprint(self.root, 1), a,
                                "生成参数（组大小）变了，同一份文本会摆出不同的图 ⇒ 必须进指纹")
        finally:
            os.environ.pop("SHORTDRAMA_VIDEO_PACK_MAX_GROUP", None)

    def test_fingerprint_changes_per_episode(self):
        _mk(self.root)
        self.assertNotEqual(canvasout.fingerprint(self.root, 1),
                            canvasout.fingerprint(self.root, 2),
                            "多集连载下每集指纹必须不同，否则第 2 集能被第 1 集的画布冒充")


class TestNodeSources(_Case):
    """入口读数 `node_sources()` —— 必须和真摆出来的画布逐字对得上。

    它存在的理由：画布入口原先按 `shots`（分镜表镜数）放行，而画布的格子来自
    静帧登记表 / 渲染任务表 / 定妆照 / 成片，两者可以完全无关。
    """

    BASE = "http://127.0.0.1:8791/"

    def _pair(self, root):
        return (canvasout.node_sources(root, 1),
                canvasout.build(root, 1, base=self.BASE))

    def test_counts_and_reasons_match_build_for_every_shape(self):
        # 每种形状各用**一个新目录**：复用同一份盘会让"没有成片"的用例
        # 读到上一个用例留下的 mp4，测的还是相等、但测的不是它声称的那件事。
        shapes = [
            dict(),                                            # 四类齐全
            dict(packs=()),                                    # 只跑了静帧
            dict(with_final=False),                            # 没出片
            dict(with_assets=False),                           # 没有定妆照
            dict(shots=(), packs=(), with_final=False, with_assets=False),   # 什么都没有
            dict(shots=(1, 2), packs=((3, 4),)),               # 组点名的镜全不在静帧表里
        ]
        for i, kw in enumerate(shapes):
            with tempfile.TemporaryDirectory() as td:
                r = Path(td) / "demo-canvas"
                r.mkdir()
                _mk(r, **kw)
                s, c = self._pair(r)
                self.assertEqual(s["nodes"], len(c["nodes"]),
                                 "第 %d 种形状格数对不上（%s）：读数 %d ≠ 真节点 %d"
                                 % (i, kw, s["nodes"], len(c["nodes"])))
                self.assertEqual(s["warnings"], c["warnings"],
                                 "第 %d 种形状的理由对不上（%s）" % (i, kw))

    def test_storyboard_only_episode_reads_zero_nodes(self):
        """★ 真实事故形状：分镜表 13 镜、盘上一格产物都没有。

        安装包数据目录里那颗唯一可点的按钮就是这个形状（`paste-0920-1949`）：
        点进去只能看到画布应用报「接口返回里没有节点」并停在列表页。
        读数必须自己说 0，入口才有依据拦下来。
        """
        _mk(self.root, shots=(), packs=(), with_final=False, with_assets=False)
        s = canvasout.node_sources(self.root, 1)
        self.assertEqual(s["nodes"], 0, "盘上什么都没有 ⇒ 必须读成 0 格")
        self.assertEqual(len(s["warnings"]), 3,
                         "缺哪三类就要说三条，不许合并成一句「没有内容」：%s" % s["warnings"])
        self.assertTrue(any("stills.json" in w for w in s["warnings"]),
                        "静帧缺失要指名道姓：%s" % s["warnings"])

    def test_group_of_only_missing_shots_adds_no_node(self):
        """组点名的镜全不在静帧表里 ⇒ 不摆组节点，读数也不许把它算进格数。

        ⚠️ 这条防的是"拿 `len(video_jobs)` 当组数"（`build()` 的 `stats.packs`
        就是这个口径，它会多算）—— 多算出来的那一格会让入口显示"有内容"，
        而点进去少一格。
        """
        _mk(self.root, shots=(1, 2), packs=((3, 4), (1, 2)))
        s, c = self._pair(self.root)
        self.assertEqual(s["packs"], 1, "只有 pack02 的镜真在静帧表里")
        self.assertEqual(c["stats"]["packs"], 2, "stats 仍按任务表条数报（本用例的前提）")
        self.assertEqual(s["nodes"], len(c["nodes"]))
        self.assertEqual(s["warnings"], c["warnings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
