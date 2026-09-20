# -*- coding: utf-8 -*-
"""`v5/server.py` 端点自测（`TestClient`，**不需要真 socket**）。

为什么不用 uvicorn：本机沙箱内 uvicorn 不响应 socket（环境限制，见
**旧架构 `api-spec.md`（已删）**）。`TestClient` 直连 ASGI app，逻辑全测得到。

## 测什么（验收判据**两端都防**）

| 方向 | 用例 |
|---|---|
| 该成功 | `/health`、`/styles`、`/progress`、`/asset-refs`、分镜、静态图 —— 200 + 信封 |
| 该失败 | 未知 pid → 404；路径越界 / 非白名单子树 → 403；写端点 → 501（**不许静默成功**） |

⚠️ 特别锁住：**成功码必须是字符串 `"000000"`** —— 前端 `api.js` 的判据是
`json.code !== '000000'`，返回数字 `0` 会被判成失败。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v5 import config, server  # noqa: E402
from v5.media import runner  # noqa: E402
from v5.tests_webmap import SB_MD  # noqa: E402  （复用同一份分镜样本）


def _client(app):
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pid = "demo-drama"
        self.proj = self.root / self.pid
        (self.proj / "scenedesigner").mkdir(parents=True)
        (self.proj / "images").mkdir()
        (self.proj / "media" / "ep1" / "stills").mkdir(parents=True)
        (self.proj / "brief.json").write_text(json.dumps(
            {"topic": "纸扎铺", "pack": "shortdrama", "genre": "恐怖悬疑",
             "episodes": 1, "must_have": ["接单"], "key_props": [], "禁忌": [],
             "tone": "冷", "结局": "定格", "protagonist": "纸扎匠"},
            ensure_ascii=False), encoding="utf-8")
        (self.proj / "assets.json").write_text(json.dumps({"assets": [
            {"id": "a1", "name": "纸扎匠", "type": "character", "priority": 9,
             "identity": "深蓝棉袄", "ref_image": "纸扎匠.png"}]}, ensure_ascii=False),
            encoding="utf-8")
        (self.proj / "scenedesigner" / "scenedesigner.md").write_text(SB_MD, encoding="utf-8")
        (self.proj / "images" / "纸扎匠.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (self.proj / "media" / "ep1" / "stills" / "LN01.jpg").write_bytes(b"\xff\xd8\xff")

        self._p = mock.patch.object(config, "PROJECTS_DIR", self.root)
        self._p.start()
        self.c = _client(server.create_app())

    def tearDown(self):
        self._p.stop()
        self.tmp.cleanup()

    # ── 工具 ──
    def get(self, path):
        r = self.c.get(path)
        body = None
        try:
            body = r.json()
        except Exception:      # noqa: BLE001 —— 二进制响应（图片）
            body = None
        return r, body

    def _ok(self, path):
        r, b = self.get(path)
        self.assertEqual(r.status_code, 200, "%s → %s %s" % (path, r.status_code, b))
        self.assertEqual(b.get("code"), "000000",
                         "%s 的成功码必须是字符串 '000000'（数字 0 会被前端判失败）" % path)
        return b["data"]

    # ── 该成功 ──
    def test_health(self):
        d = self._ok("/health")
        self.assertTrue(d["ok"])
        self.assertEqual(d["projects"], 1)

    def test_health_alias_under_api_prefix(self):
        """★ baseUrl 带不带 `/api` 都要能用（前端注释示例是 `.../api`）。"""
        d = self._ok("/api/v1/pixa/short-drama/health")
        self.assertTrue(d["ok"])

    def test_styles(self):
        d = self._ok("/v1/pixa/short-drama/styles")
        self.assertIn("shortdrama", [s["code"] for s in d])

    def test_projects_paging(self):
        d = self._ok("/v1/pixa/short-drama/projects?page=1&page_size=10&is_demo=false")
        self.assertEqual(d["total"], 1)
        self.assertEqual(len(d["list"]), 1)
        self.assertEqual(d["list"], d["items"], "list 与 items 必须同值")

    # ── `is_demo`（精选项目）2026-09-18 接通：此前是**收下即丢** ──

    def test_is_demo_true_without_manifest_is_honestly_empty(self):
        """没配精选清单 → `is_demo=true` 返回 **0 条**。

        ★ 必须是"空"，不能回落成全量：回落会让「精选项目」tab 显示成"我的项目"，
        这是**静默偏差**（用户以为那批是运营挑的）。宁可空，且前端据此说真话。
        """
        d = self._ok("/v1/pixa/short-drama/projects?is_demo=true")
        self.assertEqual(d["total"], 0)
        self.assertEqual(d["list"], [])
        self.assertIs(d["is_demo"], True, "参数必须原样回显，证明确实按它筛了")

    def test_is_demo_string_values_are_normalized(self):
        """★ **别信字符串**：`is_demo` 进到路由时是 `str`。

        若写成 `bool(is_demo)`，`"false"`（非空字符串）会被判**真** —— 于是
        「我的项目」tab 变成"只要精选"，而当时精选为空 ⇒ **列表整片消失**。
        同型坑本项目踩过（"配额上限"被当成"数据上限"）。
        """
        for s in ("false", "0", "no", "off", "False", "FALSE", ""):
            d = self._ok("/v1/pixa/short-drama/projects?is_demo=" + s)
            self.assertEqual(d["total"], 1, "is_demo=%r 应判假 → 保留我的项目" % s)
            self.assertIs(d["is_demo"], False, "is_demo=%r" % s)
        for s in ("true", "1", "yes", "on", "TRUE"):
            d = self._ok("/v1/pixa/short-drama/projects?is_demo=" + s)
            self.assertEqual(d["total"], 0, "is_demo=%r 应判真 → 精选为空" % s)
            self.assertIs(d["is_demo"], True, "is_demo=%r" % s)

    def test_projects_without_is_demo_returns_all(self):
        """★ 不传 `is_demo` = **全量**（缺省不静默藏数据）。

        锁这条，是因为"缺省值"很容易被顺手改成过滤态：一旦如此，任何忘传参数的
        调用方都会**少拿到数据却毫无察觉**。这里特意把唯一的项目标成精选 ——
        若缺省被当成 `is_demo=false`，结果会是 0 条，测试立刻抓到。
        """
        (self.root / "featured.json").write_text(
            json.dumps({"pids": [self.pid]}), encoding="utf-8")
        d = self._ok("/v1/pixa/short-drama/projects")
        self.assertEqual([r["id"] for r in d["list"]], [self.pid])
        self.assertEqual(d["total"], 1)
        self.assertIsNone(d["is_demo"], "缺省必须回显 None（=没筛），不是 False")

    def test_featured_manifest_partitions_projects(self):
        """配了精选清单 → 两批**互斥且穷尽**，且精选行的 `project_type` 变 `demo`。

        为什么锁"互斥且穷尽"：线上就是同一端点的两个取值（`is_demo=false` / `true`），
        若两边都返回同一个项目，两个 tab 会显示重复内容；若都不返回，项目会凭空消失。
        """
        other = self.root / "b-featured"
        (other / "images").mkdir(parents=True)
        (other / "brief.json").write_text(json.dumps(
            {"topic": "精选示例", "pack": "shortdrama"}, ensure_ascii=False), encoding="utf-8")
        (self.root / "featured.json").write_text(
            json.dumps({"pids": ["b-featured"]}), encoding="utf-8")

        a = self._ok("/v1/pixa/short-drama/projects")
        t = self._ok("/v1/pixa/short-drama/projects?is_demo=true")
        f = self._ok("/v1/pixa/short-drama/projects?is_demo=false")
        ids_t = [r["id"] for r in t["list"]]
        ids_f = [r["id"] for r in f["list"]]
        self.assertEqual(ids_t, ["b-featured"])
        self.assertEqual(ids_f, [self.pid])
        self.assertEqual(sorted(ids_t + ids_f), sorted(r["id"] for r in a["list"]),
                         "两批必须互斥且穷尽")
        self.assertTrue(all(r["is_demo"] for r in t["list"]))
        self.assertTrue(all(r["project_type"] == "demo" for r in t["list"]))
        self.assertTrue(all(not r["is_demo"] for r in f["list"]))

    def test_featured_manifest_broken_json_is_empty_not_error(self):
        """清单内容坏掉 → 按"空"处理，**不许 500**。

        理由：精选是**锦上添花**的展示数据。为它挂掉整个列表页，代价远大于收益。
        但只宽恕"内容坏"，不宽恕"读文件本身失败"（那是权限/磁盘问题，应当暴露）。
        """
        (self.root / "featured.json").write_text("{ not json", encoding="utf-8")
        d = self._ok("/v1/pixa/short-drama/projects?is_demo=true")
        self.assertEqual(d["total"], 0)
        self.assertEqual(self._ok("/v1/pixa/short-drama/projects?is_demo=false")["total"], 1)
        # 形状不对（不是 list）同样按空
        (self.root / "featured.json").write_text(json.dumps({"pids": "oops"}), encoding="utf-8")
        self.assertEqual(self._ok("/v1/pixa/short-drama/projects?is_demo=true")["total"], 0)

    def test_progress_superset(self):
        d = self._ok("/v1/pixa/short-drama/projects/%s/progress?include_content=true" % self.pid)
        self.assertEqual(d["id"], self.pid)
        self.assertEqual(d["project_id"], self.pid)
        self.assertEqual(d["name"], "纸扎铺")

    def test_progress_under_api_prefix(self):
        d = self._ok("/api/v1/pixa/short-drama/projects/%s/progress" % self.pid)
        self.assertEqual(d["id"], self.pid)

    def test_asset_refs(self):
        d = self._ok("/v1/pixa/short-drama/projects/%s/asset-refs" % self.pid)
        self.assertEqual(d["characters"][0]["name"], "纸扎匠")
        self.assertEqual(d["characters"][0]["states"][0]["token"],
                         "@[纸扎匠 - 基础形象](sd-asset://character/a1)")

    def test_episodes_storyboard(self):
        d = self._ok("/v1/pixa/short-drama/projects/%s/episodes/storyboard" % self.pid)
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["episode_no"], 1)

    def test_storyboard_detail_by_eid(self):
        d = self._ok("/v1/pixa/short-drama/episodes/%s-ep1/storyboard/detail" % self.pid)
        self.assertEqual(d["episode_id"], "%s-ep1" % self.pid)
        self.assertEqual(len(d["segments"]), 2)
        s = d["segments"][0]
        self.assertTrue(s["video_prompt_text"].strip(), "提示词必须给全文")
        self.assertIn("sd-asset://character/a1", s["shots"][0]["content_rich"])

    def test_static_still(self):
        r = self.c.get("/media/%s/media/ep1/stills/LN01.jpg" % self.pid)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content[:2], b"\xff\xd8", "应原样返回 JPEG 字节")

    def test_static_head_supported(self):
        """`HEAD` 要能用（`curl -I` 排障 / 部分客户端预检）。

        FastAPI 的 `APIRoute` **不会**在注册 GET 时自动补 HEAD（实测 405），
        必须显式列出 —— 与 Starlette 的 `Route` 行为不同。
        """
        r = self.c.head("/media/%s/media/ep1/stills/LN01.jpg" % self.pid)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-type", "").split(";")[0], "image/jpeg")

    def test_static_range_supported(self):
        """`<video>` 拖进度条依赖 Range。"""
        r = self.c.get("/media/%s/media/ep1/stills/LN01.jpg" % self.pid,
                       headers={"Range": "bytes=0-1"})
        self.assertIn(r.status_code, (200, 206))
        if r.status_code == 206:
            self.assertIn("content-range", {k.lower() for k in r.headers})

    def test_static_asset_image(self):
        r = self.c.get("/media/%s/images/%s" % (self.pid, "纸扎匠.png"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content[:4], b"\x89PNG")

    def test_no_project_leak_in_list(self):
        """列表里只该有自己的项目。"""
        d = self._ok("/v1/pixa/short-drama/projects")
        self.assertEqual([r["id"] for r in d["list"]], [self.pid])

    # ── 该失败 ──
    def test_unknown_pid_404(self):
        r, b = self.get("/v1/pixa/short-drama/projects/nope/progress")
        self.assertEqual(r.status_code, 404)
        self.assertNotEqual(b.get("code"), "000000", "错误响应不许带成功码")

    def test_dotdot_pid_rejected(self):
        """路径穿越必须被拒。

        ⚠️ 断言的是**安全性质**（不返回 200、不泄漏数据），不是某个具体状态码 ——
        实测 `..` 会被**客户端/中间件先归一化**：`/projects/../progress` 变成
        `/progress`，于是根本不落到 `_resolve_pid`，返回的是 **405**（路径匹配上了
        兜底路由但方法不允许）。三种结果都算拒绝；真正该测的是"拿不到数据"。
        """
        for bad in ("..", ".", "%2e%2e", "..%2f.."):
            r, b = self.get("/v1/pixa/short-drama/projects/%s/progress" % bad)
            self.assertNotEqual(r.status_code, 200, "`%s` 这种 pid 不该返回数据" % bad)
            self.assertIn(r.status_code, (400, 404, 405),
                          "`%s` → %s 不是预期的拒绝码" % (bad, r.status_code))
            self.assertTrue(b is None or b.get("data") is None,
                            "拒绝时不该带 data 体：%r" % (b,))

    def test_resolve_pid_guard_is_the_real_check(self):
        """守卫本身必须拦下 `..` / 绝对路径 / 含分隔符的 pid（不依赖客户端归一化）。"""
        from fastapi import HTTPException
        for bad in ("..", ".", "", "a/b", "a\\b", "C:"):
            with self.assertRaises(HTTPException, msg="`%r` 必须被拒" % bad):
                server._resolve_pid(bad)

    def test_bad_eid_400(self):
        r, _ = self.get("/v1/pixa/short-drama/episodes/not-an-eid/storyboard/detail")
        self.assertEqual(r.status_code, 400)

    def test_write_endpoints_501(self):
        """★ 仍未接线的写端点必须**明确报未实现**，不许静默成功。

        ⚠️ 每实现一条就要从这里**移走**（否则测试会拦下正常的实现）——
        实测踩到：`paste` / `storyboard generate` 接线后仍留在这里，测试失败。
        """
        cases = [
            ("post", "/v1/pixa/short-drama/export"),
            ("post", "/v1/aigc/credits/calculate"),
            ("post", "/v1/pixa/short-drama/projects/%s/assets/finalize" % self.pid),
        ]
        for method, path in cases:
            kw = {} if method == "delete" else {"json": {}}
            r = getattr(self.c, method)(path, **kw)
            self.assertEqual(r.status_code, 501, "%s %s 应 501" % (method.upper(), path))
            self.assertIn("尚未接线", r.json().get("message", ""))


    def test_static_guard_blocks_traversal(self):
        from fastapi import HTTPException
        cases = ["../brief.json", "brief.json", "images/../../brief.json",
                 ".agent_state.json", "", "images/../media/ep1"]
        for rel in cases:
            with self.assertRaises(HTTPException, msg="`%s` 必须被拒" % rel) as c:
                server._resolve_static(self.pid, rel)
            self.assertIn(c.exception.status_code, (400, 403, 404))

    def test_static_guard_allows_whitelisted(self):
        f = server._resolve_static(self.pid, "images/纸扎匠.png")
        self.assertTrue(f.is_file())

    def test_resolve_pid_rejects_unknown(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            server._resolve_pid("no-such-project")

    def test_write_hint_is_specific(self):
        """501 的说明应当是**准确的**（前端会把它显示给人看）。

        ⚠️ 已实现的路由要从提示表移走（否则会显示"尚未接线"误导用户）——
        所以这里抽查的是**仍未接线**的路径。
        """
        self.assertIn("算力预估", server._write_hint("/v1/aigc/credits/calculate"))
        self.assertIn("导出成片", server._write_hint("/v1/pixa/short-drama/export"))
        # 带参数的模式也要能匹配上（占位符 → 任意一段）
        self.assertIn("资产定稿",
                      server._write_hint("/v1/pixa/short-drama/projects/demo/assets/finalize"))
        self.assertIn("新建分集",
                      server._write_hint("/v1/pixa/short-drama/projects/demo/episodes"))
        self.assertIn("删除分集", server._write_hint("/v1/pixa/short-drama/episodes/demo-ep1"))
        # ★ 已实现的路径**必须不再**落在提示表里（2026-09-17 实测踩到：
        #   `/assets/{kind}/{aid}/states` 实现后仍留在表里 → 抽查"生成形象"会失败）
        self.assertEqual(
            server._write_hint("/v1/pixa/short-drama/projects/demo/assets/character/a1/states"),
            "写操作", "该端点已实现，不该再出现在 501 提示表里")
        self.assertEqual(server._write_hint("/v1/pixa/short-drama/whatever"), "写操作")


class TestCreateChain(unittest.TestCase):
    """P2b：剧本解析建项目 + 跑创作链。**不打真 LLM、不起真 dev server**。"""

    def setUp(self):
        from v5 import webchain
        self.wc = webchain
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pid = "demo-drama"
        proj = self.root / self.pid
        (proj / "scenedesigner").mkdir(parents=True)
        (proj / "media" / "ep1" / "clips").mkdir(parents=True)
        (proj / "brief.json").write_text(json.dumps(
            {"topic": "纸扎铺", "pack": "shortdrama", "genre": "悬疑", "episodes": 1,
             "must_have": ["一"], "key_props": [], "禁忌": [], "tone": "冷",
             "结局": "定格", "protagonist": "纸扎匠"}, ensure_ascii=False), encoding="utf-8")
        (proj / "scenedesigner" / "scenedesigner.md").write_text(SB_MD, encoding="utf-8")

        self._p = mock.patch.object(config, "PROJECTS_DIR", self.root)
        self._pr = mock.patch.object(config, "PROJECT_ROOT", self.root)
        self._p.start(); self._pr.start()

        class _P:
            def __init__(self, argv, **kw):
                self.pid = 888

            def wait(self, *a, **k):
                return 0

        self._popen = mock.patch.object(runner.subprocess, "Popen", _P)
        self._popen.start()
        self.c = _client(server.create_app(base="http://127.0.0.1:9999"))

    def tearDown(self):
        from v5 import webmap
        webmap.set_media_base("")
        self._popen.stop(); self._pr.stop(); self._p.stop()
        self.tmp.cleanup()

    def ok(self, r):
        self.assertEqual(r.status_code, 200, "%s %s" % (r.status_code, r.text[:300]))
        b = r.json()
        self.assertEqual(b["code"], "000000")
        return b["data"]

    # ── 剧本解析 ──
    def test_paste_creates_project(self):
        """★ 返回形状要对齐前端：`playlet-list.js:192-196` 读 `p.id` 与 `p.analyze.*`。"""
        with mock.patch.object(self.wc, "extract_brief",
                               lambda *a, **k: dict(
                                   topic="纸扎铺的夜", pack="shortdrama", genre="恐怖",
                                   episodes=1, target_duration="120 秒，共 30 镜，每镜 4 秒快切",
                                   protagonist="纸扎匠：深蓝棉袄，全片只用这一个固定人名。",
                                   must_have=["他接下十倍报酬的单子", "他照着活人脸画纸人",
                                              "画过的活人接连失踪", "他掀开白布看见自己的脸"],
                                   key_props=["空钱盒：磨损铁皮——全片逐字一致"],
                                   禁忌=["画面无可读文字"], tone="冷色调",
                                   结局="他举着笔定格")):
            d = self.ok(self.c.post("/v1/pixa/short-drama/projects/paste",
                                    json={"content": "剧本正文" * 40,
                                          "style_code": "realpeople_horror_film_style"}))
        self.assertTrue(d.get("id"), "前端读 `p.id`")
        self.assertEqual(d["id"], d["pid"])
        self.assertIn("analyze", d)
        for k in ("characters", "scenes", "props", "note"):
            self.assertIn(k, d["analyze"])
        self.assertTrue((self.root / d["pid"] / "brief.json").exists())

    def test_paste_without_content_is_400(self):
        r = self.c.post("/v1/pixa/short-drama/projects/paste", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("content", r.json().get("message", ""))

    def test_paste_bad_script_is_400_not_500(self):
        """剧本太短/提炼失败 = **可预期的输入问题** → 400（不是 500）。"""
        r = self.c.post("/v1/pixa/short-drama/projects/paste", json={"content": "太短"})
        self.assertEqual(r.status_code, 400)

    def test_paste_llm_failure_is_502(self):
        with mock.patch.object(self.wc, "extract_brief",
                               side_effect=RuntimeError("供应商挂了")):
            r = self.c.post("/v1/pixa/short-drama/projects/paste",
                            json={"content": "剧本正文" * 40})
        self.assertEqual(r.status_code, 502)
        self.assertIn("剧本解析失败", r.json().get("message", ""))

    # ── AI 创作（一句创意 → brief）──
    def test_ai_generate_creates_project(self):
        """★ `idea` 模式：返回形状与 paste 一致（前端同一段代码消费）。"""
        with mock.patch.object(self.wc, "extract_brief",
                               lambda *a, **k: dict(
                                   topic="纸扎铺的夜", pack="shortdrama", genre="恐怖",
                                   episodes=1, target_duration="120 秒，共 30 镜，每镜 4 秒快切",
                                   protagonist="纸扎匠：深蓝棉袄，全片只用这一个固定人名。",
                                   must_have=["他接下十倍报酬的单子", "他照着活人脸画纸人",
                                              "画过的活人接连失踪", "他掀开白布看见自己的脸"],
                                   key_props=["空钱盒：磨损铁皮——全片逐字一致"],
                                   禁忌=["画面无可读文字"], tone="冷色调",
                                   结局="他举着笔定格")):
            d = self.ok(self.c.post("/v1/pixa/short-drama/projects/ai-generate",
                                    json={"idea": "纸扎匠给纸人点睛后自己也变成了纸人",
                                          "style_code": "shortdrama",
                                          "ratio": "9:16", "episodes": 2}))
        self.assertTrue(d.get("id"))
        self.assertEqual(d["id"], d["pid"])
        self.assertIn("analyze", d)
        root = self.root / d["pid"]
        # ★ idea 模式**不该**写编剧产物（否则链会把点子当整部剧本）
        self.assertFalse((root / "scriptwriter" / "scriptwriter_ep1.md").exists())
        self.assertTrue((root / "_source-idea.md").exists())
        b = json.loads((root / "brief.json").read_text(encoding="utf-8"))
        self.assertEqual(b["episodes"], 2, "集数要写进 brief")

    def test_ai_generate_without_idea_is_400(self):
        r = self.c.post("/v1/pixa/short-drama/projects/ai-generate", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("idea", r.json().get("message", ""))

    def test_ai_generate_too_short_is_400(self):
        r = self.c.post("/v1/pixa/short-drama/projects/ai-generate", json={"idea": "短"})
        self.assertEqual(r.status_code, 400)

    def test_ai_generate_llm_failure_is_502(self):
        with mock.patch.object(self.wc, "extract_brief",
                               side_effect=RuntimeError("供应商挂了")):
            r = self.c.post("/v1/pixa/short-drama/projects/ai-generate",
                            json={"idea": "纸扎匠给纸人点睛后自己也变成了纸人"})
        self.assertEqual(r.status_code, 502)
        self.assertIn("AI 创作失败", r.json().get("message", ""))

    # ── 跑创作链 ──
    def test_generate_storyboard_starts_chain(self):
        with mock.patch.object(self.wc, "ensure_devserver",
                               lambda pid, log=None: {"reused": False, "pid": pid}), \
             mock.patch.object(self.wc, "devserver_status",
                               lambda: {"alive": True, "ok": True}):
            d = self.ok(self.c.post(
                "/v1/pixa/short-drama/episodes/batch/storyboard/generate",
                json={"episode_ids": ["%s-ep1" % self.pid]}))
        self.assertEqual(d["kind"], "chain")
        self.assertEqual(d["pid"], self.pid)
        self.assertEqual(d["shots"], [], "chain 不吃 shots")

    def test_generate_storyboard_requires_ids(self):
        r = self.c.post("/v1/pixa/short-drama/episodes/batch/storyboard/generate", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("episode_ids", r.json().get("message", ""))

    def test_generate_storyboard_devserver_failure_is_502(self):
        """D6 起不来且**不是可预期冲突**（如环境缺文件）→ 502。

        分流口径：`RuntimeError` = 端口被占/起不来等**可预期冲突** → 409；
        其它异常（FileNotFoundError 等）= 上游真出问题 → 502。
        """
        with mock.patch.object(self.wc, "ensure_devserver",
                               side_effect=FileNotFoundError("找不到 langgraph.exe")):
            r = self.c.post("/v1/pixa/short-drama/episodes/batch/storyboard/generate",
                            json={"episode_ids": ["%s-ep1" % self.pid]})
        self.assertEqual(r.status_code, 502)
        self.assertIn("dev server", r.json().get("message", ""))

    def test_generate_storyboard_port_conflict_is_409(self):
        """★★ 端口被占（可预期冲突）→ **409**，不是 502。

        实测踩到：`generate` 端点原先只写了 `except Exception → 502`，
        而 `/devserver` 端点已经分了 409 —— 两处**分流不一致**，前端只看到 502
        这种"服务端好像挂了"的状态，拿不到"端口被别人占着"这个可操作信息。
        """
        with mock.patch.object(self.wc, "ensure_devserver",
                               side_effect=RuntimeError("端口 2024 上已有 dev，拒绝接管")):
            r = self.c.post("/v1/pixa/short-drama/episodes/batch/storyboard/generate",
                            json={"episode_ids": ["%s-ep1" % self.pid]})
        self.assertEqual(r.status_code, 409, r.text[:200])
        self.assertIn("拒绝接管", r.json().get("message", ""))

    def test_devserver_ensure_port_conflict_is_409(self):
        """同一个分流也要在 `/devserver` 端点成立（两处须一致）。"""
        with mock.patch.object(self.wc, "ensure_devserver",
                               side_effect=RuntimeError("端口被占，拒绝接管")):
            r = self.c.post("/v1/pixa/short-drama/devserver",
                            json={"pid": self.pid})
        self.assertEqual(r.status_code, 409)

    # ── dev server 状态 ──
    def test_devserver_status_endpoint(self):
        d = self.ok(self.c.get("/v1/pixa/short-drama/devserver"))
        for k in ("alive", "ok", "agent_url"):
            self.assertIn(k, d)

    def test_devserver_ensure_requires_pid(self):
        r = self.c.post("/v1/pixa/short-drama/devserver", json={})
        self.assertEqual(r.status_code, 400)

    def test_devserver_ensure_unknown_pid_404(self):
        r = self.c.post("/v1/pixa/short-drama/devserver", json={"pid": "nope"})
        self.assertEqual(r.status_code, 404)

    # ── 删除项目：移到暂存区 ──
    def test_batch_delete_moves_to_trash(self):
        d = self.ok(self.c.post("/v1/pixa/short-drama/projects/batch-delete",
                                json={"project_ids": [self.pid]}))
        self.assertEqual(d["deleted"], [self.pid])
        self.assertFalse((self.root / self.pid).exists(), "原目录应已移走")
        trash = self.root / d["trash"]
        self.assertTrue((trash / self.pid).exists(), "★ 必须**可恢复**（移到暂存区而非删除）")
        self.assertIn("可恢复", d["note"])

    def test_batch_delete_requires_ids(self):
        r = self.c.post("/v1/pixa/short-drama/projects/batch-delete", json={})
        self.assertEqual(r.status_code, 400)

    def test_batch_delete_refuses_while_run_active(self):
        """★★ 有任务在跑时**默认拒绝删除**。

        实测踩到：我删了一个正在跑创作链的项目 → 目录被移走，**但链没停**，
        它把 `worldbuilder.md` 写回**原路径** → 凭空重建出一个没有 `brief.json`
        的幽灵目录（列表看不到、但占着名字）。
        """
        from v5.media import runner as R
        # `_alive` 必须打桩 True：假 pid 会被 `reconcile()` 判成 lost（terminal）→
        # 就不算"在跑"了（实测踩到），那样这条护栏根本测不到。
        with mock.patch.object(R, "_alive", lambda p: True):
            rec = R.start(self.pid, "assets", log=lambda *_: None)
            r = self.c.post("/v1/pixa/short-drama/projects/batch-delete",
                            json={"project_ids": [self.pid]})
        self.assertEqual(r.status_code, 409, r.text[:200])
        msg = r.json().get("message", "")
        self.assertIn("还有任务在跑", msg)
        self.assertIn(rec["run_id"], msg)
        self.assertTrue((self.root / self.pid).exists(), "拒绝时不该动目录")

    def test_batch_delete_force_cancels_then_deletes(self):
        """`force=true` → 先取消任务再删（并在返回里**列出取消了哪些**）。"""
        from v5.media import runner as R
        with mock.patch.object(R, "_alive", lambda p: True):
            rec = R.start(self.pid, "assets", log=lambda *_: None)
            with mock.patch.object(R.subprocess, "run",
                                   lambda *a, **k: mock.Mock(returncode=0, stdout="", stderr="")):
                d = self.ok(self.c.post("/v1/pixa/short-drama/projects/batch-delete",
                                        json={"project_ids": [self.pid], "force": True}))
        self.assertEqual(d["deleted"], [self.pid])
        self.assertEqual((d.get("cancelled_runs") or {}).get(self.pid), [rec["run_id"]],
                         "要在返回里说清**取消了哪些任务**")
        self.assertFalse((self.root / self.pid).exists())

    # ── 静态安全（直接测守卫，比走 HTTP 可靠：客户端可能先把 `..` 归一化）──
class TestCors(unittest.TestCase):
    """CORS 必须**回显 Origin**（前端带 `credentials:'include'`，不能用 `*`）。"""

    def setUp(self):
        self.app = server.create_app()
        self.c = _client(self.app)

    def _acao(self, origin):
        r = self.c.get("/v1/pixa/short-drama/styles", headers={"Origin": origin})
        return r.status_code, r.headers.get("access-control-allow-origin")

    def test_local_origins_echoed(self):
        for o in ("http://localhost:5500", "http://127.0.0.1:8080", "http://localhost"):
            st, acao = self._acao(o)
            self.assertEqual(st, 200)
            self.assertEqual(acao, o, "本地来源必须**回显**（不能是 `*`，因为要带凭证）")

    def test_null_origin_allowed(self):
        """★ 前端是 `index.html` 双击即开（`file://`），而 `file://` 的 Origin 是
        字面串 `null` —— 不放行则**前端一个请求都发不出去**（实测漏过一次）。"""
        st, acao = self._acao("null")
        self.assertEqual(st, 200)
        self.assertEqual(acao, "null", "`file://` 页面必须能用")

    def test_foreign_origin_not_echoed(self):
        """外部来源**不回显** → 浏览器会拦下（服务端不主动拒绝，这是 CORS 的正常形态）。"""
        st, acao = self._acao("http://evil.example")
        self.assertEqual(st, 200, "服务照常响应，由浏览器裁决")
        self.assertIsNone(acao, "外部来源不该被回显")

    def test_credentials_header_true(self):
        r = self.c.get("/v1/pixa/short-drama/styles",
                       headers={"Origin": "http://localhost:5500"})
        self.assertEqual(r.headers.get("access-control-allow-credentials"), "true")

    def test_preflight_allows_post(self):
        """预检要放行 POST —— 前端所有写动作都是 POST/PATCH。"""
        r = self.c.options("/v1/pixa/short-drama/projects/paste", headers={
            "Origin": "http://localhost:5500",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("access-control-allow-origin"),
                         "http://localhost:5500")
        self.assertIn("POST", r.headers.get("access-control-allow-methods", ""))

    def test_preflight_private_network_allowed(self):
        """★★ Chrome 的 Local Network Access：本机页面访问 localhost 时，预检会带
        `Access-Control-Request-Private-Network: true`。

        Starlette 默认 `allow_private_network=False` → **预检直接 400** →
        浏览器只报 `Failed to fetch`（信息量为零，实测就是这样栽的）。
        """
        for origin in ("null", "http://localhost:5500", "http://127.0.0.1:8080"):
            r = self.c.options("/v1/pixa/short-drama/styles", headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true"})
            self.assertEqual(r.status_code, 200,
                             "%s 的 PNA 预检被拒（%s）—— 浏览器会只报 Failed to fetch"
                             % (origin, r.status_code))
            self.assertEqual(r.headers.get("access-control-allow-private-network"), "true",
                             "缺 Access-Control-Allow-Private-Network 头")

    def test_preflight_without_pna_still_ok(self):
        """不带该头的普通预检不能因此坏掉（两端都要防）。"""
        r = self.c.options("/v1/pixa/short-drama/styles", headers={
            "Origin": "http://localhost:5500",
            "Access-Control-Request-Method": "GET"})
        self.assertEqual(r.status_code, 200)


class TestSameOriginWebRoot(unittest.TestCase):
    """`--web-root`：**同一 origin 托管前端**（前端仍在自己的目录，只是被"端出去"）。

    为什么值得做（都是实测被咬过的）：
      · 不需要 CORS（同源请求浏览器不做 CORS 检查）
      · **可以不再放行 `Origin: null`** ← 补上面那条到期的复查项
      · 前端不用手配 baseUrl（`api.js` 自检同源）
      · 少养一个静态服务进程（本机沙箱里那个会被回收）
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.web = Path(self.tmp.name)
        (self.web / "index.html").write_text("<!doctype html><h1>PAVO</h1>", encoding="utf-8")
        (self.web / "assets" / "js").mkdir(parents=True)
        (self.web / "assets" / "js" / "app.js").write_text("// app", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _app(self, **kw):
        return _client(server.create_app(**kw))

    # ── 静态托管 ──
    def test_serves_index_at_root(self):
        c = self._app(web_root=str(self.web))
        r = c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("PAVO", r.text)

    def test_serves_nested_asset(self):
        c = self._app(web_root=str(self.web))
        r = c.get("/assets/js/app.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("// app", r.text)

    def test_api_routes_win_over_static_mount(self):
        """★ API 路由必须先命中 —— `mount(\"/\")` 挂在最后，不能抢走 `/health`。"""
        c = self._app(web_root=str(self.web))
        r = c.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("code"), "000000", "命中的应是 API，不是静态目录")

    def test_api_prefix_still_works(self):
        c = self._app(web_root=str(self.web))
        self.assertEqual(c.get("/api/health").status_code, 200)

    def test_unknown_path_is_404_not_index(self):
        """未知路径不该被 `index.html` 兜住（那会让所有 404 伪装成页面）。"""
        c = self._app(web_root=str(self.web))
        self.assertEqual(c.get("/no-such-file.js").status_code, 404)

    def test_favicon_is_204_not_404(self):
        """★ 浏览器会自动请求 `/favicon.ico` —— 给 204，别 404。

        不给的话**每次导航刷一条 404 红字**，把真正的错淹掉
        （实测：同源页面上唯一的 4xx 就是它，冒烟测试因此误报「有控制台 error」）。
        """
        c = self._app(web_root=str(self.web))
        self.assertEqual(c.get("/favicon.ico").status_code, 204)

    def test_api_routes_not_shadowed_by_favicon(self):
        c = self._app(web_root=str(self.web))
        self.assertEqual(c.get("/health").json().get("code"), "000000")

    def test_missing_index_html_raises_early(self):
        """配错目录要在**启动时**报错，而不是运行期一片 404。"""
        d = Path(tempfile.mkdtemp())
        with self.assertRaises(RuntimeError) as ctx:
            server.create_app(web_root=str(d))
        self.assertIn("index.html", str(ctx.exception))

    # ── 媒体 URL 转为根相对（同源才对） ──
    def test_media_base_empty_when_same_origin(self):
        from v5 import webmap
        old = webmap.MEDIA_BASE
        try:
            server.create_app(base="http://127.0.0.1:8787", web_root=str(self.web))
            self.assertEqual(webmap.MEDIA_BASE, "",
                             "同源时必须是**根相对** —— 页面就在这个 origin 上")
            self.assertTrue(webmap.media_url("p", "media/a.jpg").startswith("/media/"))
        finally:
            webmap.set_media_base(old)

    # ── CORS 收紧 ──
    def test_null_origin_rejected_in_same_origin_mode(self):
        """★ 同源托管时**不再放行 `Origin: null`**。

        那条注释里的复查项到期了：「P2 一旦接入写操作，必须重新评估」——
        **P2 已完成**（建项目/创作/跑链/删项目全可写）。放行 `null` 意味着
        沙箱化 iframe、本地文件等任何拿到 `null` 的来源都能写。
        """
        c = self._app(web_root=str(self.web))
        r = c.get("/v1/pixa/short-drama/styles", headers={"Origin": "null"})
        self.assertIsNone(r.headers.get("access-control-allow-origin"),
                          "同源模式下不该回显 `null`")

    def test_local_origins_still_allowed_in_same_origin_mode(self):
        """本地来源照常放行（可能要开两个端口调试）。"""
        c = self._app(web_root=str(self.web))
        r = c.get("/v1/pixa/short-drama/styles", headers={"Origin": "http://127.0.0.1:5500"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), "http://127.0.0.1:5500")

    def test_null_origin_opt_in_escape_hatch(self):
        """要用 `file://` 双击打开时，显式设环境变量能把它开回来（知道代价再开）。"""
        c = self._app(web_root=str(self.web))
        with mock.patch.dict(os.environ, {"SHORTDRAMA_WEB_ALLOW_NULL_ORIGIN": "1"}):
            c2 = self._app(web_root=str(self.web))
        r = c2.get("/v1/pixa/short-drama/styles", headers={"Origin": "null"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), "null")
        # 没设变量时仍然拒绝（对照）
        r0 = c.get("/v1/pixa/short-drama/styles", headers={"Origin": "null"})
        self.assertIsNone(r0.headers.get("access-control-allow-origin"))

    def test_null_origin_still_allowed_without_web_root(self):
        """**向后兼容**：没给 `web_root`（旧的两服务模式）时 `file://` 仍要能用。"""
        c = self._app(base="http://127.0.0.1:8787")
        r = c.get("/v1/pixa/short-drama/styles", headers={"Origin": "null"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), "null")


class TestWriteEndpoints(unittest.TestCase):
    """P2a 写端点。**不烧配额**：`runner` 的 Popen 被换成假对象。"""

    PID = "demo-drama"
    EID = "demo-drama-ep1"
    SID = "demo-drama-ep1-LN01"

    def setUp(self):
        from v5.media import runner
        self.runner = runner
        runner._FakeCalls = []

        class _P:
            def __init__(self, argv, **kw):
                runner._FakeCalls.append({"argv": argv, "kw": kw})
                self.pid = 5150

            def wait(self, *a, **k):
                return 0

        self._fake = _P
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.proj = self.root / self.PID
        (self.proj / "scenedesigner").mkdir(parents=True)
        (self.proj / "images").mkdir()
        (self.proj / "media" / "ep1" / "clips").mkdir(parents=True)
        (self.proj / "brief.json").write_text(json.dumps(
            {"topic": "纸扎铺", "pack": "shortdrama", "genre": "悬疑", "episodes": 1,
             "must_have": ["接单"], "key_props": [], "禁忌": [], "tone": "冷",
             "结局": "定格", "protagonist": "纸扎匠"}, ensure_ascii=False), encoding="utf-8")
        (self.proj / "assets.json").write_text(json.dumps({"assets": [
            {"id": "a1", "name": "纸扎匠", "type": "character", "priority": 9,
             "identity": "深蓝棉袄", "ref_image": "纸扎匠.png"}]}, ensure_ascii=False),
            encoding="utf-8")
        (self.proj / "scenedesigner" / "scenedesigner.md").write_text(SB_MD, encoding="utf-8")

        self._p = mock.patch.object(config, "PROJECTS_DIR", self.root)
        self._pr = mock.patch.object(config, "PROJECT_ROOT", self.root)
        self._popen = mock.patch.object(runner.subprocess, "Popen", _P)
        self._taskkill = mock.patch.object(
            runner.subprocess, "run",
            lambda *a, **k: mock.Mock(returncode=0, stdout="", stderr=""))
        self._p.start(); self._pr.start(); self._popen.start(); self._taskkill.start()
        self.c = _client(server.create_app(base="http://127.0.0.1:9999"))

    def tearDown(self):
        # ★ `create_app(base=...)` 会改 **模块级全局** `webmap.MEDIA_BASE`
        #   → 必须复位，否则污染后面断言相对路径的用例（实测被全量 discover 抓到）。
        from v5 import webmap
        webmap.set_media_base("")
        self._taskkill.stop(); self._popen.stop(); self._pr.stop(); self._p.stop()
        self.tmp.cleanup()

    def ok(self, r):
        self.assertEqual(r.status_code, 200, "%s %s" % (r.status_code, r.text[:300]))
        b = r.json()
        self.assertEqual(b["code"], "000000")
        return b["data"]

    # ── ★ 路由顺序：显式写路由不能被 501 兜底吃掉 ──
    def test_explicit_routes_beat_catchall(self):
        """★ 兜底路由必须**注册在最后**，否则它会抢先匹配掉所有 POST。

        实测踩到过：兜底写在 `include_router` 之前 → 所有写端点都返回 501。
        """
        r = self.c.post("/v1/pixa/short-drama/projects/%s/rename" % self.PID,
                        json={"name": "新名"})
        self.assertEqual(r.status_code, 200, "rename 被兜底吃掉了？" + r.text[:200])

    def test_unimplemented_still_501(self):
        """仍未接线的（如「导出成片」）必须仍然 501，且说明里点出原因。

        ⚠️ 别再用已实现的路由测这条 —— 实测踩到过两次（`paste`、`ai-generate`
        接线后此用例失败）。
        """
        r = self.c.post("/v1/pixa/short-drama/export", json={"episode_id": "e1"})
        self.assertEqual(r.status_code, 501)
        self.assertIn("尚未接线", r.json().get("message", ""))

    # ── 媒体任务 ──
    def test_keyframe_run_starts(self):
        d = self.ok(self.c.post(
            "/v1/pixa/short-drama/segments/batch/keyframe/generate",
            json={"segment_ids": [self.SID, "demo-drama-ep1-LN02"]}))
        self.assertEqual(d["kind"], "keyframe")
        self.assertEqual(d["shots"], ["LN01", "LN02"])
        self.assertEqual(d["pid"], self.PID)
        self.assertTrue(self.runner._FakeCalls, "应 spawn 子进程")

    def test_video_run_starts(self):
        d = self.ok(self.c.post(
            "/v1/pixa/short-drama/segments/batch/video/generate",
            json={"segment_id": self.SID}))
        self.assertEqual(d["kind"], "video")
        self.assertEqual(d["shots"], ["LN01"])

    def test_media_run_requires_shots(self):
        """★★ 空 shots = 整片渲染 → 必须 400，**绝不能默认渲全片**。

        ⚠️ 必须**带 pid** 才会走到这条护栏（不带 pid 会先在"缺项目标识"处被拦）。
        这条路径单独测，因为它是本服务**最贵的一条护栏**。
        """
        for kind in ("keyframe", "video"):
            r = self.c.post(
                "/v1/pixa/short-drama/segments/batch/%s/generate" % kind,
                json={"pid": self.PID})
            self.assertEqual(r.status_code, 400, kind)
            self.assertIn("整片渲染", r.json().get("message", ""), kind)

    def test_media_run_without_pid_is_400(self):
        r = self.c.post("/v1/pixa/short-drama/segments/batch/video/generate", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("项目标识", r.json().get("message", ""))

    def test_media_run_rejects_unknown_shot(self):
        r = self.c.post("/v1/pixa/short-drama/segments/batch/video/generate",
                        json={"segment_ids": ["demo-drama-ep1-LN99"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("LN99", r.json().get("message", ""))

    def test_media_run_rejects_bad_sid(self):
        r = self.c.post("/v1/pixa/short-drama/segments/batch/keyframe/generate",
                        json={"segment_ids": ["garbage"]})
        self.assertEqual(r.status_code, 400)

    def test_assets_run(self):
        d = self.ok(self.c.post(
            "/v1/pixa/short-drama/projects/%s/assets/batch-generate-image" % self.PID,
            json={"kinds": ["character"]}))
        self.assertEqual(d["kind"], "assets")

    def test_compose_starts_episode_run(self):
        """★ D（2026-09-19）：`/episodes/{eid}/compose` = **整片出片**的真实实现。

        为什么要一条**独立 kind**（而不是"空 shots 的 video 请求"）：
        `keyframe`/`video` 的空 shots 被硬护栏拒掉（那是"参数漏传"的形状，
        见 `test_media_run_requires_shots`）；而"人显式要求整片出片"是另一回事。
        两者必须能用**类型**区分，不能靠"shots 是不是空"猜。
        这里同时锁住：路由存在、kind=episode、**不带 shots 也不再 400**。
        """
        d = self.ok(self.c.post(
            "/v1/pixa/short-drama/episodes/%s/compose" % self.EID, json={}))
        self.assertEqual(d["kind"], "episode")
        self.assertEqual(d["ep"], 1)
        self.assertEqual(d["pid"], self.PID)
        self.assertEqual(d["shots"], [], "整片出片不吃 shots")
        self.assertTrue(self.runner._FakeCalls, "应 spawn 子进程")

    def test_gen_script_route_starts_script_kind(self):
        """★ 2026-09-19 两段式：`/episodes/{eid}/script/generate` = **只跑到剧本正文**。

        前端「确认简介 → 生成剧本内容」用它。与「生成分镜脚本」的区别**只在 kind**
        （`script` → `drive_chain --until scriptwriter`），门 / 记账 / 独占锁全在同一入口。
        """
        # ⚠️ 这条路由先 `ensure_devserver`（与「生成分镜脚本」同一套 D6 逻辑），
        #    而本机**可能真有一个 dev server 在跑** ⇒ 会 409「拒绝接管别人的进程」
        #    （那是**正确**行为，不是 bug）。测试只关心"起没起对 kind"，故替掉它。
        from v5 import webchain
        with mock.patch.object(webchain, "ensure_devserver",
                               lambda pid, **kw: {"reused": True, "pid": pid}):
            d = self.ok(self.c.post(
                "/v1/pixa/short-drama/episodes/%s/script/generate" % self.EID, json={}))
        self.assertEqual(d["kind"], "script")
        self.assertEqual(d["ep"], 1)
        self.assertTrue(self.runner._FakeCalls, "应 spawn 子进程")
        # 与既有的 `/script`（**保存**正文）不是同一条：一个是生成，一个是写文本
        self.assertNotEqual(d["kind"], "keyframe")

    def test_compose_rejects_bad_eid(self):
        r = self.c.post("/v1/pixa/short-drama/episodes/garbage/compose", json={})
        self.assertIn(r.status_code, (400, 404))

    def test_qc_switches_reach_the_child_env(self):
        """★ 2026-09-19：质检自愈开关**随请求**走到子进程 env（判据只写一处）。

        · 不传 ⇒ 关（人工模式默认）；
        · 传 `true` ⇒ 开。
        真值换算只在 `runner._qc_flag`，服务器这层只做**转发**（见 `_qc_of`）。
        """
        self.ok(self.c.post(
            "/v1/pixa/short-drama/episodes/%s/compose" % self.EID, json={}))
        env = self.runner._FakeCalls[-1]["kw"]["env"]
        self.assertEqual(env.get("SHORTDRAMA_STILL_QC"), "0")
        self.assertEqual(env.get("SHORTDRAMA_CLIP_QC"), "0")

        self.ok(self.c.post(
            "/v1/pixa/short-drama/episodes/%s/compose" % self.EID,
            json={"still_qc": True, "clip_qc": 1}))
        env = self.runner._FakeCalls[-1]["kw"]["env"]
        self.assertEqual(env.get("SHORTDRAMA_STILL_QC"), "1")
        self.assertEqual(env.get("SHORTDRAMA_CLIP_QC"), "1")

        # 单镜/批量视频这条入口同样吃这两个开关
        self.ok(self.c.post(
            "/v1/pixa/short-drama/segments/batch/video/generate",
            json={"segment_id": self.SID,  "still_qc": True}))
        env = self.runner._FakeCalls[-1]["kw"]["env"]
        self.assertEqual(env.get("SHORTDRAMA_STILL_QC"), "1")
        self.assertEqual(env.get("SHORTDRAMA_CLIP_QC"), "0", "只开了静帧那一半")

    # ── runs 台账 ──
    def test_runs_list_and_get(self):
        d = self.ok(self.c.post(
            "/v1/pixa/short-drama/segments/batch/keyframe/generate",
            json={"segment_id": self.SID}))
        rid = d["run_id"]
        got = self.ok(self.c.get("/v1/pixa/short-drama/runs/%s" % rid))
        self.assertEqual(got["run_id"], rid)
        self.assertIn("log_tail", got)
        lst = self.ok(self.c.get("/v1/pixa/short-drama/runs?pid=%s" % self.PID))
        self.assertEqual([x["run_id"] for x in lst["list"]], [rid])

    def test_run_404(self):
        r = self.c.get("/v1/pixa/short-drama/runs/nope")
        self.assertEqual(r.status_code, 404)

    def test_run_cancel(self):
        d = self.ok(self.c.post(
            "/v1/pixa/short-drama/segments/batch/keyframe/generate",
            json={"segment_id": self.SID}))
        got = self.ok(self.c.delete("/v1/pixa/short-drama/runs/%s" % d["run_id"]))
        self.assertEqual(got["status"], "cancelled")

    # ── 确定性编辑 ──
    def test_rename(self):
        d = self.ok(self.c.post("/v1/pixa/short-drama/projects/%s/rename" % self.PID,
                                json={"name": "改过的名字"}))
        self.assertEqual(d["topic"], "改过的名字")
        b = json.loads((self.proj / "brief.json").read_text(encoding="utf-8"))
        self.assertEqual(b["topic"], "改过的名字")

    def test_rename_empty_is_400(self):
        r = self.c.post("/v1/pixa/short-drama/projects/%s/rename" % self.PID,
                        json={"name": "  "})
        self.assertEqual(r.status_code, 400)

    def test_update_outline(self):
        d = self.ok(self.c.post("/v1/pixa/short-drama/projects/%s/outline" % self.PID,
                                json={"genre": "温情"}))
        self.assertIn("genre", d["changed"])

    def test_update_outline_rejects_pack(self):
        """`pack` 不可编辑（改类型包会换掉整套审美）→ 400。"""
        # 2026-09-16：原来用 3d-animation；该包铲除后换一个**存在**的包名
        # （本用例只验证"不可编辑"，与具体包名无关，但必须传一个真实存在的包以免测成别的分支）。
        r = self.c.post("/v1/pixa/short-drama/projects/%s/outline" % self.PID,
                        json={"pack": "niulai-movie-style"})
        self.assertEqual(r.status_code, 400)

    def test_update_script(self):
        d = self.ok(self.c.post(
            "/v1/pixa/short-drama/episodes/%s/script" % self.EID,
            json={"content": "正文" * 80}))
        self.assertEqual(d["chars"], 160)
        self.assertTrue((self.proj / "scriptwriter" / "scriptwriter_ep1.md").exists())

    def test_update_segment_reports_invalidation(self):
        d = self.ok(self.c.post("/v1/pixa/short-drama/segments/%s" % self.SID,
                                json={"dialogue": "新台词内容更长一些"}))
        self.assertIn("dialogue", d["applied"])
        self.assertIn("invalidated", d)
        md = (self.proj / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8")
        self.assertIn("新台词内容更长一些", md)

    def test_delete_segment(self):
        d = self.ok(self.c.delete("/v1/pixa/short-drama/segments/demo-drama-ep1-LN02"))
        self.assertEqual(d["removed"], "LN02")

    def test_delete_last_segment_400(self):
        self.ok(self.c.delete("/v1/pixa/short-drama/segments/demo-drama-ep1-LN02"))
        r = self.c.delete("/v1/pixa/short-drama/segments/%s" % self.SID)
        self.assertEqual(r.status_code, 400)

    def test_add_segment(self):
        d = self.ok(self.c.post("/v1/pixa/short-drama/episodes/%s/segments" % self.EID,
                                json={"after": "LN01"}))
        self.assertTrue(d["visible"], "新镜必须能被解析出来")

    def test_assets_add_delete_and_sync(self):
        d = self.ok(self.c.post("/v1/pixa/short-drama/projects/%s/assets" % self.PID,
                                json={"kind": "prop", "name": "空钱盒"}))
        self.assertEqual(d["type"], "prop")
        d = self.ok(self.c.delete(
            "/v1/pixa/short-drama/projects/%s/assets/prop/%s" % (self.PID, "空钱盒")))
        self.assertEqual(d["removed"], ["空钱盒"])

        (self.proj / "images" / "新图.png").write_bytes(b"\x89PNG")
        d = self.ok(self.c.post(
            "/v1/pixa/short-drama/projects/%s/auto-sync-assets" % self.PID, json={}))
        self.assertIn("新图", d["added"])

    def test_add_asset_keeps_identity(self):
        """★ 新增资产时 `identity`（**会注入每一镜提示词**的外观描述）必须一起落盘。

        留空会导致模型每镜自己编长相 —— **同一个人在 18 个镜头里长成 18 个人**，
        问题要到成片才暴露（资产契约门就是为此而设）。
        """
        d = self.ok(self.c.post("/v1/pixa/short-drama/projects/%s/assets" % self.PID,
                                json={"kind": "character", "name": "测试角色甲",
                                      "identity": "四方脸、深蓝棉袄，全片只用这一个固定人名。"}))
        self.assertGreater(d["identity_chars"], 10)
        reg = json.loads((self.proj / "assets.json").read_text(encoding="utf-8"))
        hit = [a for a in reg["assets"] if a["name"] == "测试角色甲"][0]
        self.assertIn("深蓝棉袄", hit["identity"])

    def test_save_asset_state_applies_and_reports_ignored(self):
        """★★ 「角色信息」抽屉保存：**能落地的写、落不了地的如实报出来**。

        v5 的资产模型比 Pavo 薄（每个资产只有一份身份、项目级风格、无 per-asset
        音色/四视图）。所以返回里带 `applied` / `ignored` 两张清单 ——
        ⛔ **绝不假装收下**（静默忽略是本项目最贵的一类 bug）。
        """
        self.ok(self.c.post("/v1/pixa/short-drama/projects/%s/assets" % self.PID,
                            json={"kind": "character", "name": "测试角色乙"}))
        d = self.ok(self.c.post(
            "/v1/pixa/short-drama/projects/%s/assets/character/测试角色乙/states" % self.PID,
            json={"name": "测试角色丙", "description": "戴圆框眼镜，灰蓝布衫。",
                  "state_name": "基础形象", "voice_mode": "ai_auto",
                  "style_id": "realpeople_x", "model_code": "agnes-image",
                  "fourview_image": {"url": "x"}}))
        self.assertEqual(d["applied"].get("identity"), "戴圆框眼镜，灰蓝布衫。")
        self.assertEqual(d["applied"].get("name"), "测试角色丙")
        for k in ("state_name", "voice_mode", "style_id", "model_code", "fourview_image"):
            self.assertIn(k, d["ignored"], "落不了地的字段必须出现在 ignored 里：%s" % k)
        self.assertIn("一份身份", d["note"], "要说明 v5 为什么忽略这些")
        # 名字改了 → id 跟着改（前端按 id 找它）
        self.assertEqual(d["id"], "测试角色丙")
        reg = json.loads((self.proj / "assets.json").read_text(encoding="utf-8"))
        self.assertTrue(any(a["id"] == "测试角色丙" for a in reg["assets"]))

    def test_save_asset_state_unknown_asset_is_400(self):
        r = self.c.post("/v1/pixa/short-drama/projects/%s/assets/character/无此人/states"
                        % self.PID, json={"description": "x"})
        # 与 delete_asset 同一约定：EditError → 400（不是 404）
        self.assertEqual(r.status_code, 400)

    def test_patch_auto_sync_is_advisory(self):
        d = self.ok(self.c.patch(
            "/v1/pixa/short-drama/projects/%s/auto-sync-assets" % self.PID,
            json={"auto_sync_assets": False}))
        self.assertIs(d["auto_sync_assets"], False)
        self.assertIn("未落盘", d["note"])

    def test_bad_sid_is_400(self):
        r = self.c.post("/v1/pixa/short-drama/segments/garbage", json={"dialogue": "x"})
        self.assertEqual(r.status_code, 400)

    def test_unknown_pid_write_404(self):
        r = self.c.post("/v1/pixa/short-drama/projects/nope/rename", json={"name": "x"})
        self.assertEqual(r.status_code, 404)


class TestMediaBaseWiring(unittest.TestCase):
    """`create_app(base=...)` 必须把绝对前缀注入 `webmap`（否则媒体 URL 全 404）。"""

    def test_create_app_sets_base(self):
        from v5 import webmap
        old = webmap.MEDIA_BASE
        try:
            server.create_app(base="http://127.0.0.1:9999")
            self.assertEqual(webmap.MEDIA_BASE, "http://127.0.0.1:9999")
        finally:
            webmap.set_media_base(old)

    def test_no_base_leaves_default(self):
        from v5 import webmap
        old = webmap.MEDIA_BASE
        try:
            webmap.set_media_base("")
            server.create_app()
            self.assertEqual(webmap.MEDIA_BASE, "",
                             "没给 base 时不该乱设（纯函数测试依赖这个默认）")
        finally:
            webmap.set_media_base(old)


class TestHitlEndpoints(unittest.TestCase):
    """步级「逐步人工确认」的两个端点（2026-09-18）。

    前端靠它显示"链路停在某一步"，并提交 `approve`（继续）/ `redo`（打回）。

    ⚠️ **业务判据（有没有挂起 / 目标合不合法）只在 `v5/hitl.py` 一份** ——
    这里只验"HTTP 形状 + 错误码分流"，不重复验规则（本项目忌同一判据写两份）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pid = "hitl-drama"
        self.proj = self.root / self.pid
        (self.proj / "images").mkdir(parents=True)
        (self.proj / "brief.json").write_text(json.dumps(
            {"topic": "演示", "pack": "shortdrama", "episodes": 1},
            ensure_ascii=False), encoding="utf-8")
        self._p = mock.patch.object(config, "PROJECTS_DIR", self.root)
        self._p.start()
        # `PROJECT_ROOT` 也要指到临时目录：`runner` 的台账与 dev 状态文件都在它下面，
        # 否则测试会往本仓库 `.tmp/` 里写东西
        self._p2 = mock.patch.object(config, "PROJECT_ROOT", self.root)
        self._p2.start()
        self.c = _client(server.create_app())

    def tearDown(self):
        self._p2.stop()
        self._p.stop()
        self.tmp.cleanup()

    def _url(self):
        return "/v1/pixa/short-drama/projects/%s/hitl" % self.pid

    def test_no_pending_yet(self):
        r = self.c.get(self._url())
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        self.assertEqual(b["code"], "000000")
        self.assertFalse(b["data"]["pending"])
        self.assertIsNone(b["data"]["manual_steps"],
                          "不知道就报 None（不能报成 False）")

    def test_post_approve_roundtrip(self):
        from v5 import hitl
        hitl.record_pending(self.proj, thread_id="t", run_id="r",
                            done_roles=["worldbuilder"])
        r = self.c.post(self._url(), json={"decision": "approve", "by": "老王"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()["data"]
        self.assertEqual(d["decision"], "approve", "提交后状态要立刻反映出来")
        self.assertTrue(d["pending"], "决定被消费前，挂起仍在")
        self.assertEqual(d["prev_role"], "worldbuilder")
        self.assertEqual(d["next_role"], "assetdesigner")

    def test_post_redo_defaults_to_prev_role(self):
        from v5 import hitl
        hitl.record_pending(self.proj, thread_id="t", run_id="r",
                            done_roles=["worldbuilder", "assetdesigner"])
        r = self.c.post(self._url(), json={"decision": "redo", "note": "资产卡不对"})
        self.assertEqual(r.status_code, 200, r.text)
        dec = hitl.read_decision(self.proj)
        self.assertEqual(dec["decision"], "redo")
        self.assertEqual(dec["target"], "assetdesigner")
        self.assertEqual(dec["note"], "资产卡不对")

    def test_post_without_pending_is_400(self):
        """★ **不许静默成功**：没有挂起时写下的决定会被**下一次**挂起立刻消费
        ⇒ 静默跳过一次人工审核（本项目最贵的一类 bug）。"""
        r = self.c.post(self._url(), json={"decision": "approve"})
        self.assertEqual(r.status_code, 400, r.text)
        b = r.json()
        self.assertNotEqual(b["code"], "000000")
        self.assertIn("没有等待批准", b["message"])

    def test_post_illegal_redo_target_is_400(self):
        from v5 import hitl
        hitl.record_pending(self.proj, thread_id="t", run_id="r",
                            done_roles=["worldbuilder"])
        r = self.c.post(self._url(), json={"decision": "redo", "target": "reviewer"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("打回目标", r.json()["message"])

    def test_post_unknown_project_is_404(self):
        r = self.c.post("/v1/pixa/short-drama/projects/nope/hitl",
                        json={"decision": "approve"})
        self.assertEqual(r.status_code, 404)

    def test_runs_endpoint_carries_hitl(self):
        """`GET /runs/{id}` 要带上 `hitl`。

        前端本来就在轮询这个端点（`api.js` 的 `waitRun`，3 秒一次）⇒ 挂起状态
        **搭车**即可，不必让它再开一条轮询。必要性：链路等人时 run 状态仍是
        `running`，只看 run 记录**看不出"正在等人"**。
        """
        from v5 import hitl
        from v5.media import runner
        hitl.record_pending(self.proj, thread_id="t", run_id="r",
                            done_roles=["worldbuilder"])
        rec = {"run_id": "20260101-000000-hitl-drama-chain-abcdef",
               "pid": self.pid, "ep": 1, "kind": "chain", "shots": [],
               "status": "running", "os_pid": None}
        with mock.patch.object(runner, "read_ledger", return_value=dict(rec)):
            r = self.c.get("/v1/pixa/short-drama/runs/%s" % rec["run_id"])
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()["data"]
        self.assertIn("hitl", d)
        self.assertTrue(d["hitl"]["pending"])
        self.assertEqual(d["hitl"]["prev_role"], "worldbuilder")

    def test_hitl_reachable_under_api_prefix(self):
        """`baseUrl` 带 `/api` 也要能用（同 `health` 的既有约束）。"""
        r = self.c.get("/api/v1/pixa/short-drama/projects/%s/hitl" % self.pid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["code"], "000000")

    def test_not_swallowed_by_501_catchall(self):
        """★ 回归锁：显式写路由必须**先于** 501 兜底注册。

        事故形态（`server.py:418-421` 记着）：兜底路由若注册在前，
        `/{rel:path}` 会抢走所有 POST/PATCH/DELETE ⇒ 写端点全变成 501。
        """
        r = self.c.post(self._url(), json={"decision": "approve"})
        self.assertNotEqual(r.status_code, 501, "被 501 兜底抢走了：注册顺序错了")


class TestAppImportable(unittest.TestCase):
    """`uvicorn v5.server:app` 也要能用（模块级 `__getattr__` 延迟构建）。"""

    def test_module_getattr_builds_app(self):
        import importlib
        m = importlib.import_module("v5.server")
        app = getattr(m, "app")
        self.assertIsNotNone(app, "★ 模块级不能有 `app = None`，否则 __getattr__ 不触发")
        self.assertTrue(hasattr(app, "routes"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
