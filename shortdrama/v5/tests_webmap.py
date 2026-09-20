# -*- coding: utf-8 -*-
"""`v5/webmap.py` 自测（**纯函数 + 临时目录**，不调任何 API、不起 socket）。

为什么一定要有这层测试：
  `webmap` 是「v5 磁盘产物 → Pavo 前端契约」的**唯一转换点**。它错了，
  前端会静默显示错数据（比报错难查得多）。这里逐条锁住**形状**：
  · 超集键（local 与线上两套）必须同时在
  · token 双向翻译必须能往返
  · 缺文件/空项目**必须不抛**（返回空结构），且**不许编**内容
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v5 import config, webmap  # noqa: E402

SB_MD = """# 分镜：纸扎铺

## 第1幕｜纸扎铺-日｜S1 / 6s

| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |
|--------|------|------|------|---------|---------|------|------|
| 1 | 全景 | 平视 | 固定 | 6 | 纸扎匠在逼仄的铺子里看着空钱盒，@纸扎匠 的指尖刚触到盒沿又停住，没有动 | 纸扎匠：这单我接了。 | 环境音 |
| 2 | 近景 | 俯视 | 缓推 | 4 | @纸扎匠 的手指挪到免提手机边缘，屏幕亮着，富人的声音从听筒传出来 | 富人：钱给你十倍。 | 手机电流声 |

## 分镜总表

| 镜头号 | 时长(秒) | 备注 |
|--------|---------|------|
| 1 | 6 | 汇总 |
| 2 | 4 | 汇总 |
"""


class _Base(unittest.TestCase):
    def setUp(self):
        # ★ `webmap.MEDIA_BASE` 是**模块级全局**：别的测试类（如 `tests_server` 的
        #   写端点用例）会把它设成绝对前缀。不在这里强制复位，本文件的相对路径断言
        #   就会**依赖测试执行顺序**（实测：全量 discover 时 4 个用例挂掉）。
        webmap.set_media_base("")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._p = mock.patch.object(config, "PROJECTS_DIR", self.root)
        self._p.start()
        self.pid = "demo-drama"
        self.proj = self.root / self.pid
        self.proj.mkdir(parents=True)

    def tearDown(self):
        webmap.set_media_base("")
        self._p.stop()
        self.tmp.cleanup()

    # ── 构造器材 ──
    def write_brief(self, **over):
        b = {"topic": "纸扎铺", "pack": "shortdrama", "genre": "恐怖悬疑",
             "episodes": 1, "target_duration": "120s", "protagonist": "纸扎匠",
             "must_have": ["接单", "画脸", "失踪", "收走"],
             "key_props": ["空钱盒"], "禁忌": ["无可读文字"],
             "tone": "冷色调", "结局": "他掀开白布"}
        b.update(over)
        (self.proj / "brief.json").write_text(
            json.dumps(b, ensure_ascii=False), encoding="utf-8")

    def write_assets(self):
        (self.proj / "assets.json").write_text(json.dumps({
            "assets": [
                {"id": "a1", "name": "纸扎匠", "type": "character", "keywords": ["纸扎匠"],
                 "priority": 9, "identity": "年约四十的男性，深蓝棉袄", "ref_image": "纸扎匠.png"},
                {"id": "a2", "name": "纸扎铺", "type": "location", "keywords": ["纸扎铺"],
                 "priority": 8, "ref_image": "纸扎铺.png"},
                {"id": "a3", "name": "空钱盒", "type": "prop", "keywords": ["钱盒"],
                 "priority": 8, "ref_image": "空钱盒.png"},
            ]}, ensure_ascii=False), encoding="utf-8")

    def write_sb(self):
        d = self.proj / "scenedesigner"
        d.mkdir(exist_ok=True)
        (d / "scenedesigner.md").write_text(SB_MD, encoding="utf-8")


class TestIdRoundtrip(_Base):
    """`<pid>-ep<N>` 的 eid 必须**可逆**（`storyboard_detail` 靠它反解 pid）。"""

    def test_roundtrip(self):
        self.assertEqual(webmap.parse_episode_id("demo-drama-ep1"), ("demo-drama", 1))
        self.assertEqual(webmap.episode_id("demo-drama", 1), "demo-drama-ep1")

    def test_pid_with_dashes(self):
        """★ pid 自身含 `-`（v5 全是这种命名：`village-tree`）→ 必须**从右侧**切。"""
        pid, ep = webmap.parse_episode_id("village-tree-ep12")
        self.assertEqual((pid, ep), ("village-tree", 12))

    def test_bad_ids(self):
        for bad in ("", "no-suffix", "x-ep0", "ep1"):
            pid, ep = webmap.parse_episode_id(bad)
            self.assertFalse(pid, "非法 eid 必须解析为空：%r" % bad)
            self.assertEqual(ep, 0)


class TestTokens(_Base):
    """token 双向翻译 —— v5 用 `@资产名`，Pavo 用 `@[名 - 基础形象](sd-asset://kind/id)`。"""

    def test_token_format_matches_pavo(self):
        t = webmap.token("scene", "纸扎铺", "355708573973520384")
        self.assertEqual(t, "@[纸扎铺 - 基础形象](sd-asset://scene/355708573973520384)")

    def test_split_display_both_separators(self):
        """线上两种写法都出现过（带空格 / 不带空格）→ 都要切得开。"""
        self.assertEqual(webmap._split_display("纸扎匠 - 基础形象"), ("纸扎匠", "基础形象"))
        self.assertEqual(webmap._split_display("纸扎铺-基础形象"), ("纸扎铺", "基础形象"))
        self.assertEqual(webmap._split_display("无分隔"), ("无分隔", ""))

    def test_rich_to_plain(self):
        rich = "@[纸扎铺 - 基础形象](sd-asset://scene/1)内有旧灯泡"
        self.assertEqual(webmap.rich_to_plain(rich), "纸扎铺 - 基础形象内有旧灯泡")

    def test_rich_to_v5_maps_and_reports(self):
        """★ 认识的要转成 `@资产名`，**不认识的原样保留并上报**（不许静默）。"""
        txt = "@[纸扎匠 - 基础形象](sd-asset://character/a1) 看向 @[陌生人 - 基础形象](sd-asset://character/zz)"
        out, unresolved = webmap.rich_to_v5(txt, ["纸扎匠"])
        self.assertIn("@纸扎匠", out)
        self.assertNotIn("sd-asset://character/a1", out)
        self.assertIn("陌生人", unresolved)
        self.assertIn("sd-asset://character/zz", out, "不认识的必须原样保留，不猜")

    def test_out_direction_uses_registry_kind_and_id(self):
        """v5 → Pavo：kind/id **必须来自注册表**（拼错 kind 会让前端回写失配）。"""
        self.write_assets()
        idx = webmap.name_index(self.proj)
        reg = webmap.registry(self.proj)
        self.assertEqual(idx["纸扎匠"], {"kind": "character", "id": "a1"})
        self.assertEqual(idx["纸扎铺"]["kind"], "scene", "location 要映射成 scene")
        self.assertEqual(idx["空钱盒"]["kind"], "prop")

        rich, tokens, unresolved = webmap._to_rich("看 @纸扎匠 与 @纸扎铺", reg, idx)
        self.assertIn("sd-asset://character/a1", rich)
        self.assertIn("sd-asset://scene/a2", rich)
        self.assertEqual(unresolved, [])
        # ★ token 数组：前端 `D.richHtml()` 吃的是它（不是字符串）
        kinds = [t["type"] for t in tokens]
        self.assertIn("ref", kinds)
        self.assertIn("text", kinds)
        refs = [t for t in tokens if t["type"] == "ref"]
        self.assertEqual({(r["kind"], r["id"], r["name"]) for r in refs},
                         {("character", "a1", "纸扎匠"), ("scene", "a2", "纸扎铺")})

    def test_adjacent_mentions_both_upgraded(self):
        """★ 真数据抓到的 bug（village-bees LN01）：两个**相邻**引用会被吞成一个。

        原文 `@阿凯站在@老周家院的青砖院中央` —— 旧实现用 `assets._AT` 那个
        字符类去抽（它不含 `@`）→ 抽成 `阿凯站在@老周家院的青砖院中央` 一整条 →
        谁也不匹配 → **token 升级整个失效**。
        """
        (self.proj / "assets.json").write_text(json.dumps({"assets": [
            {"id": "c1", "name": "阿凯", "type": "character", "priority": 9, "identity": "黑T"},
            {"id": "s1", "name": "老周家院", "type": "location", "priority": 8},
        ]}, ensure_ascii=False), encoding="utf-8")
        idx = webmap.name_index(self.proj)
        reg = webmap.registry(self.proj)
        rich, tokens, unresolved = webmap._to_rich(
            "阿凯站在@老周家院的青砖院中央，@阿凯 喘着气", reg, idx)
        self.assertIn("sd-asset://scene/s1", rich, "相邻的两个引用都要升级")
        self.assertIn("sd-asset://character/c1", rich)
        self.assertEqual(unresolved, [])
        self.assertEqual([t["id"] for t in tokens if t["type"] == "ref"], ["s1", "c1"])

    def test_longest_name_wins(self):
        """★ 长名优先：`@老周家院`（场景）不能被 `@老周`（角色）切开。"""
        (self.proj / "assets.json").write_text(json.dumps({"assets": [
            {"id": "c1", "name": "老周", "type": "character", "priority": 9, "identity": "外套"},
            {"id": "s1", "name": "老周家院", "type": "location", "priority": 8},
        ]}, ensure_ascii=False), encoding="utf-8")
        idx = webmap.name_index(self.proj)
        rich, _tk, _un = webmap._to_rich("在@老周家院里，@老周 站着",
                                         webmap.registry(self.proj), idx)
        self.assertIn("sd-asset://scene/s1", rich)
        self.assertIn("sd-asset://character/c1", rich)
        self.assertNotIn("scene/c1", rich, "不能把 @老周家院 切成 @老周 + 家院")

    def test_unresolved_out_direction_is_reported(self):
        idx = {"纸扎匠": {"kind": "character", "id": "a1"}}
        reg = {"assets": [{"id": "a1", "name": "纸扎匠", "type": "character"}]}
        rich, _tk, unresolved = webmap._to_rich("看 @不存在的东西", reg, idx)
        self.assertIn("@不存在的东西", rich)
        self.assertIn("不存在的东西", unresolved)


class TestEnvelope(_Base):
    def test_success_code_is_string_000000(self):
        """前端判的是**字符串** `'000000'`（`api.js` 的 `http()`）——数字 0 会被判失败。"""
        e = webmap.envelope({"a": 1})
        self.assertEqual(e["code"], "000000")
        self.assertIsInstance(e["code"], str)
        self.assertEqual(e["data"], {"a": 1})

    def test_error_code_is_not_success(self):
        e = webmap.error_body("炸了", code="E500")
        self.assertNotEqual(e["code"], webmap.OK_CODE)
        self.assertEqual(e["message"], "炸了")


class TestEmptyProject(_Base):
    """空项目/缺文件**必须不抛**（P1 是只读端点，一次刷新不该 500）。"""

    def test_bare_dir(self):
        r = webmap.progress(self.proj)
        self.assertEqual(r["id"], self.pid)
        self.assertEqual(r["name"], self.pid, "没有 brief 时项目名回落目录名")
        self.assertEqual(r["episodes"], [], "没有 brief 时不该凭空造分集")

    def test_no_assets_no_storyboard(self):
        self.assertEqual(webmap.asset_refs(self.proj),
                         {"characters": [], "scenes": [], "props": []})
        d = webmap.storyboard_detail(self.proj, 1)
        self.assertEqual(d["segments"], [])
        self.assertEqual(d["storyboard_phase"], webmap.PHASE_DRAFT)

    def test_dirty_json_does_not_throw(self):
        (self.proj / "brief.json").write_text("{ 这不是 JSON", encoding="utf-8")
        self.assertEqual(webmap.brief(self.proj), {})
        self.assertEqual(webmap.progress(self.proj)["id"], self.pid)

    def test_list_pids_only_real_projects(self):
        """★ **只认有 `brief.json` 的目录**。

        实测（2026-09-15）：`projects/` 下有 4 个非项目目录 ——
        `__lockout__` / `__open__`（锁相关临时目录）、`studio`（orchestrator 建的
        默认工作目录）、`.tmp`。旧判据把它们全当项目 → 前端列表多出 4 行空项目。
        """
        (self.root / ".hidden").mkdir()
        (self.root / "_lockout").mkdir()
        (self.root / "__open__").mkdir()
        (self.root / "studio").mkdir()
        (self.root / "a-file.txt").write_text("x", encoding="utf-8")
        self.assertEqual(webmap.list_pids(), [], "无 brief.json 的目录不是项目")
        self.write_brief()
        self.assertEqual(webmap.list_pids(), [self.pid])


class TestExcerpt(_Base):
    """`world_setting` 摘录：结构化简报**不能只取第一行**（实测只有 10 字）。"""

    def test_multi_line_accumulation(self):
        md = "# 标题\n\n## 小节\n- 主题：阿凯的蜂箱\n- 类型：乡村温情\n正文一\n正文二\n"
        got = webmap._excerpt(md, 600)
        self.assertNotIn("#", got, "不该带标题")
        self.assertIn("阿凯的蜂箱", got)
        self.assertIn("正文二", got, "要累加多行，不能只取第一行")
        self.assertIn("主题", got)

    def test_respects_budget(self):
        md = "\n".join("第%d行内容" % i for i in range(500))
        self.assertLessEqual(len(webmap._excerpt(md, 100)), 100)

    def test_world_setting_not_too_short(self):
        self.write_brief()
        wb = self.proj / "worldbuilder"
        wb.mkdir()
        (wb / "worldbuilder.md").write_text(
            "# 剧情概要与世界观\n- 主题：阿凯的蜂箱\n- 类型：乡村温情\n"
            "老周、小林、阿凯三人围绕三排蜂箱展开的一段乡村故事。\n", encoding="utf-8")
        o = webmap.progress(self.proj)["outline"]
        self.assertGreater(len(o["world_setting"]), 20,
                           "只取第一行会得到 10 字的无意义摘录")

    # ── 2026-09-18：字段不能混进"给下游 Agent 的指令"，也不能与剧情概要重复 ──

    def test_excerpt_skips_blockquote_instructions(self):
        """`>` 行是**写给下游 Agent 的元信息/指令**，不是世界观。

        实测 `bach-daily-ep1`：`worldbuilder.md` 开头就是
          `> 类型包 niulai-movie-style ｜ primitive_folk_cgi（反质量预设…）`
          `> 本文件是渲染风格的唯一权威：下游分镜、静帧、视频提示词、QC 判定全部以此为准。`
        摘录从头部取 ⇒ 前端「世界观设定」第一屏就是这两行，既噪声又会被误读成世界观。
        """
        md = ("# 标题\n\n> 类型包 xxx ｜ 反质量预设\n"
              "> 本文件是渲染风格的唯一权威：下游分镜以此为准。\n"
              "## 二、世界观设定\n十八世纪的莱比锡，圣托马斯教堂。\n")
        got = webmap._excerpt(md, 600)
        self.assertNotIn("唯一权威", got, "给下游 Agent 的指令不该出现在摘录里")
        self.assertNotIn("类型包", got)
        self.assertIn("圣托马斯教堂", got)

    def test_world_setting_uses_world_section_not_head_excerpt(self):
        """有「世界观设定」章节就取那一节 —— 否则会与「剧情概要」显示同一批内容。

        `worldbuilder.md` 第一节通常是「故事大纲（四幕，逐字对应 must_have）」，
        从头摘 600 字必然把它吃进来 ⇒ 前端两个字段重复。
        """
        self.write_brief()
        wb = self.proj / "worldbuilder"
        wb.mkdir()
        (wb / "worldbuilder.md").write_text(
            "# 项目锁定\n\n> 类型包 x（反质量预设）\n"
            "## 一、故事大纲（四幕，逐字对应 must_have）\n"
            "- 第一幕：阿凯在蜂箱前被蜇了\n"
            "## 二、世界观设定\n"
            "时间/地点：浙东山村，三排蜂箱散在坡地上。\n"
            "社会逻辑：蜂农靠天吃饭，收成看花期。\n"
            "## 三、视觉风格指南（唯一权威）\n"
            "色彩：暖黄\n", encoding="utf-8")
        o = webmap.progress(self.proj)["outline"]
        ws = o["world_setting"]
        self.assertIn("浙东山村", ws)
        self.assertNotIn("第一幕", ws, "不该含故事大纲那一节（会与剧情概要重复）")
        self.assertNotIn("唯一权威", ws)
        self.assertNotIn("色彩：暖黄", ws, "下一节的内容不该混进来")

    def test_world_setting_falls_back_when_no_section(self):
        """没有「世界观设定」章节时**回落摘录**，不许空着（实测有 5 个项目没有该节）。"""
        self.write_brief()
        wb = self.proj / "worldbuilder"
        wb.mkdir()
        (wb / "worldbuilder.md").write_text(
            "# 剧情概要与世界观\n- 主题：阿凯的蜂箱\n"
            "老周、小林、阿凯三人围绕三排蜂箱展开的一段乡村故事。\n", encoding="utf-8")
        o = webmap.progress(self.proj)["outline"]
        self.assertIn("蜂箱", o["world_setting"])

    def test_outline_carries_structured_summary_items(self):
        """`story_summary_items` 必须与 `story_summary` **同源**（一个是列表、一个是它的 join）。

        ★ 分开给是为了让前端把 must_have 渲染成**分条**而不是一整段；
        但两者**不许各自拼一遍** —— 那会埋下"两处判据不一致"的坑。
        """
        self.write_brief()
        b = json.loads((self.proj / "brief.json").read_text(encoding="utf-8"))
        b["must_have"] = ["第一幕：接单", "第二幕：交货"]
        (self.proj / "brief.json").write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
        o = webmap.progress(self.proj)["outline"]
        self.assertEqual(o["story_summary_items"], ["第一幕：接单", "第二幕：交货"])
        self.assertEqual(o["story_summary"], "第一幕：接单 / 第二幕：交货",
                         "字符串必须正好是列表的 join —— 一个来源")


class TestRegistryWarnings(_Base):
    """数据质量问题必须**可见**（但 shim **不过滤** —— 那会复制 auto_sync 的规则）。"""

    def test_source_photo_pseudo_assets_warned(self):
        (self.proj / "assets.json").write_text(json.dumps({"assets": [
            {"id": "a1", "name": "老周", "type": "character", "priority": 9,
             "identity": "深色外套"},
            {"id": "a2", "name": "老周.source", "type": "character", "priority": 8},
        ]}, ensure_ascii=False), encoding="utf-8")
        w = webmap.progress(self.proj)["v5"]["warnings"]
        self.assertTrue(any("source" in x for x in w), "源照片伪资产要报出来")
        # ★ 但**不能过滤** —— 过滤等于把规则复制一份，还会掩盖数据问题
        names = [c["name"] for c in webmap.asset_refs(self.proj)["characters"]]
        self.assertIn("老周.source", names, "shim 只报告、不过滤")

    def test_character_without_identity_warned(self):
        (self.proj / "assets.json").write_text(json.dumps({"assets": [
            {"id": "a1", "name": "老周", "type": "character", "priority": 9},
        ]}, ensure_ascii=False), encoding="utf-8")
        w = webmap.progress(self.proj)["v5"]["warnings"]
        self.assertTrue(any("identity" in x for x in w))

    def test_location_without_identity_not_warned(self):
        """场景/道具**本来就不需要** identity → 不该报警（否则全是噪声）。"""
        (self.proj / "assets.json").write_text(json.dumps({"assets": [
            {"id": "s1", "name": "纸扎铺", "type": "location", "priority": 8},
            {"id": "p1", "name": "空钱盒", "type": "prop", "priority": 8},
        ]}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(webmap.progress(self.proj)["v5"]["warnings"], [])


class TestSupersetKeys(_Base):
    """★ 前端读 **local** 形状、线上返回**另一套** → 两套键必须同时在。"""

    def test_progress_has_both_shapes(self):
        self.write_brief()
        r = webmap.progress(self.proj)
        for k in ("id", "project_id"):
            self.assertEqual(r[k], self.pid, "缺超集键：%s" % k)
        for k in ("name", "projectName"):
            self.assertEqual(r[k], "纸扎铺", "缺超集键：%s" % k)
        self.assertIn("planned_episode_count", r)
        self.assertIn("flow", r)

    def test_episode_has_both_shapes(self):
        self.write_brief()
        ep = webmap.progress(self.proj)["episodes"][0]
        self.assertEqual(ep["id"], ep["episode_id"])
        self.assertEqual(ep["no"], ep["episode_no"])
        self.assertEqual(ep["script"], ep["content"])
        self.assertEqual(ep["summary"], ep["episode_summary"])

    def test_episode_always_has_storyboard_key(self):
        """★ local 形状要求 `storyboard` **一定存在**（前端多处直接取 `ep.storyboard.segments`）。

        完整分镜走 detail 端点；列表只给轻量壳（阶段 + 空 segments）。
        """
        self.write_brief()
        for ep in webmap.progress(self.proj)["episodes"]:
            self.assertIn("storyboard", ep)
            self.assertIn("segments", ep["storyboard"])
            self.assertEqual(ep["storyboard"]["segments"], [])
            self.assertIn("phase", ep["storyboard"])
            self.assertIn("storyboard_phase", ep["storyboard"])

    def test_episode_storyboard_phase_reflects_disk(self):
        self.write_brief()
        self.assertEqual(webmap.progress(self.proj)["episodes"][0]["storyboard"]["phase"],
                         webmap.PHASE_DRAFT)
        self.write_sb()
        self.assertEqual(webmap.progress(self.proj)["episodes"][0]["storyboard"]["phase"],
                         webmap.PHASE_DONE)

    def test_asset_row_has_both_shapes(self):
        self.write_assets()
        refs = webmap.asset_refs(self.proj)
        ch = refs["characters"][0]
        self.assertEqual(ch["id"], ch["character_id"])
        self.assertEqual(ch["name"], ch["title"])
        self.assertEqual(ch["states"][0]["thumbnail_url"], "/media/%s/images/纸扎匠.png" % self.pid)

    def test_asset_state_has_image_alias(self):
        """★★ 形象状态**必须带 `image`** —— 这条曾漏过，且症状是"静默显示未生成"。

        实测事故（2026-09-17）：后端只给 `thumbnail_url` / `images`，
        而前端有 **9 处**在读 `state.image` ⇒ **即使磁盘上真有图，卡片也一律显示「未生成」**。
        实测证据：`lost-and-found` 的 `thumbnail_url` 是
        `/media/lost-and-found/images/周平.png`（图**确实存在**）但 `image` 缺失
        → 18 个有资产图的项目全都显示「未生成」。

        这是本项目铁律的又一例：**契约要同时规定「结构」与「值」** ——
        只给"新形状"、不给前端在读的字段，就是**静默**失配。
        """
        self.write_assets()
        for ch in webmap.asset_refs(self.proj)["characters"]:
            st = ch["states"][0]
            self.assertIn("image", st, "形象状态缺 `image`（前端读的就是它）")
            self.assertEqual(st["image"], st["thumbnail_url"],
                             "`image` 与 `thumbnail_url` 必须同源同值")
            self.assertEqual(st["image"], "/media/%s/images/%s.png" % (self.pid, ch["name"]))

    def test_asset_state_image_empty_when_no_image(self):
        """没有图时 `image` 是空串（**别名也要一致**，别一个空一个有值）。"""
        self.write_assets()
        for ch in webmap.asset_refs(self.proj)["characters"]:
            ch["states"][0].pop("image")
        # 直接用一个没有 ref_image 的注册表验证
        (self.proj / "assets.json").write_text(json.dumps({
            "assets": [{"id": "a9", "name": "无图角色", "type": "character",
                        "identity": "测试", "ref_image": ""}]}, ensure_ascii=False),
            encoding="utf-8")
        st = webmap.asset_refs(self.proj)["characters"][0]["states"][0]
        self.assertEqual(st["image"], "")
        self.assertEqual(st["thumbnail_url"], "")
        self.assertEqual(st["images"], [])

    def test_storyboard_segment_has_both_shapes(self):
        self.write_brief()
        self.write_assets()
        self.write_sb()
        d = webmap.storyboard_detail(self.proj, 1)
        for k in ("phase", "storyboard_phase"):
            self.assertEqual(d[k], webmap.PHASE_DONE, "缺超集键：%s" % k)
        s = d["segments"][0]
        self.assertEqual(s["order"], s["display_order"])
        self.assertEqual(s["duration_ms"], s["estimated_duration_ms"])
        self.assertEqual(s["video_prompt"], s["video_prompt_text"])
        self.assertEqual(s["keyframe"], s["selected_keyframe_url"])
        self.assertEqual(s["video"], s["selected_video_url"])
        self.assertEqual(s["id"], s["segment_id"])


class TestStoryboardDetail(_Base):
    def setUp(self):
        super().setUp()
        self.write_brief()
        self.write_assets()
        self.write_sb()

    def test_parse_dedupes_summary_table(self):
        """分镜文件里有第二张表（`## 分镜总表`）→ 必须仍只出 2 镜。"""
        d = webmap.storyboard_detail(self.proj, 1)
        self.assertEqual(len(d["segments"]), 2)
        self.assertEqual([s["order"] for s in d["segments"]], [1, 2])

    def test_video_prompt_is_full_text(self):
        """★ 必须给**全文**（本项目铁律：只报字数会漏掉字面量「同上」）。"""
        s = webmap.storyboard_detail(self.proj, 1)["segments"][0]
        self.assertTrue(s["video_prompt_text"].strip(),
                        "提示词不该为空 —— 装配失败是静默失败，必须能测出来")
        self.assertGreater(len(s["video_prompt_text"]), 20)

    def test_rich_and_plain_pair(self):
        """v5 的 `@资产名` → 出方向应升级成 Pavo token，且 plain 版要剥掉 token。"""
        s = webmap.storyboard_detail(self.proj, 1)["segments"][0]
        shot = s["shots"][0]
        self.assertIn("sd-asset://character/a1", shot["content_rich"])
        self.assertNotIn("sd-asset://", shot["content_plain"])
        self.assertEqual(shot["content"], shot["content_plain"])

    def test_segment_has_scenes_layer(self):
        """★★ Pavo 是**三层** `segment → scenes → shots`。

        第一版只在 segment 上放了 `shots`、漏了 `scenes` →
        前端 `storyboard.js:114` 的 `seg.scenes.reduce` **TypeError、整页崩**
        （2026-09-15 真实浏览器实测）。这条锁死那一层必须存在。
        """
        s = webmap.storyboard_detail(self.proj, 1)["segments"][0]
        self.assertIn("scenes", s, "segment 必须有 scenes 中间层")
        self.assertEqual(len(s["scenes"]), 1)
        sc = s["scenes"][0]
        self.assertIn("title", sc)
        self.assertIn("shots", sc)
        self.assertEqual(len(sc["shots"]), 1)

    def test_scene_shot_has_rich_token_array(self):
        """★ `sh.rich` 必须是 **token 数组**（前端 `D.richHtml` 对字符串会 `.map` → TypeError）。"""
        s = webmap.storyboard_detail(self.proj, 1)["segments"][0]
        sh = s["scenes"][0]["shots"][0]
        self.assertIsInstance(sh["rich"], list)
        self.assertTrue(all(isinstance(t, dict) and "type" in t for t in sh["rich"]))
        refs = [t for t in sh["rich"] if t["type"] == "ref"]
        self.assertTrue(refs, "应至少有一个 ref token（画面描述里有 @纸扎匠）")
        self.assertEqual(refs[0]["kind"], "character")
        self.assertEqual(refs[0]["name"], "纸扎匠")

    def test_scene_shot_has_duration_sec(self):
        """视图模板直接读 `sh.duration_sec`（`storyboard.js:139`）。"""
        s = webmap.storyboard_detail(self.proj, 1)["segments"][0]
        self.assertEqual(s["scenes"][0]["shots"][0]["duration_sec"], 6)

    def test_scene_linked_asset_id_resolved(self):
        """场景名能对上注册表 → 给出 linked_scene_asset_id；对不上则空串（不猜）。"""
        s = webmap.storyboard_detail(self.proj, 1)["segments"][0]
        # SB_MD 没有「场景」列 → scene 为空 → 回落 "未标注场景" → 注册表里没有 → 空
        self.assertEqual(s["scenes"][0]["linked_scene_asset_id"], "")
        # 把场景列指到已知资产上 → 应解析出来
        (self.proj / "scenedesigner" / "scenedesigner.md").write_text(
            SB_MD.replace("| 音效 |", "| 音效 | 场景 |")
                 .replace("| 环境音 |", "| 环境音 | 纸扎铺 |")
                 .replace("| 手机电流声 |", "| 手机电流声 | 纸扎铺 |"),
            encoding="utf-8")
        s2 = webmap.storyboard_detail(self.proj, 1)["segments"][0]
        self.assertEqual(s2["scenes"][0]["linked_scene_asset_id"], "a2")
        self.assertEqual(s2["scenes"][0]["title"], "纸扎铺")

    def test_v5_section_carries_real_fields(self):
        s = webmap.storyboard_detail(self.proj, 1)["segments"][0]
        self.assertEqual(s["v5"]["shot_name"], "LN01")
        self.assertEqual(s["v5"]["shot_type"], "全景")
        self.assertEqual(s["v5"]["angle"], "平视")
        self.assertIn("这单我接了", s["v5"]["dialogue"])

    def test_order_is_sequential_not_column_index(self):
        """★★ 镜序必须**连续 1..N**，不能用镜头号列的整数。

        真实事故（2026-09-15，village-bees 18 镜）：分镜把镜头号写成 `| 1-1 | 1-2 | 2-1 |`，
        而 `storyboard.parse` 的 `index` 取的是**第一个整数** = **幕号** →
        order 变成 `[1,1,1,2,2,3,…]`，左侧导轨编号重复。
        """
        multi = SB_MD.replace("| 1 | 全景", "| 1-1 | 全景").replace("| 2 | 近景", "| 2-1 | 近景")
        (self.proj / "scenedesigner" / "scenedesigner.md").write_text(multi, encoding="utf-8")
        segs = webmap.storyboard_detail(self.proj, 1)["segments"]
        self.assertEqual([s["order"] for s in segs], [1, 2], "镜序必须连续")
        self.assertEqual([s["display_order"] for s in segs], [1, 2])
        # 列里的原始整数仍可在 v5 块里查到（供排障），但**不参与排序**
        self.assertEqual([s["v5"]["col_index"] for s in segs], [1, 2])

    def test_order_matches_shot_name(self):
        """`order` 应与 `shot_name`（LN01…）的序号一致 —— 两者都是"位置"。"""
        for s in webmap.storyboard_detail(self.proj, 1)["segments"]:
            self.assertEqual(s["order"], int(s["v5"]["shot_name"][2:]),
                             "%s 的 order 与镜名不符" % s["v5"]["shot_name"])

    def test_job_state_mapping(self):
        md = self.proj / "media" / "ep1"
        (md / "clips").mkdir(parents=True)
        (md / "video_jobs.json").write_text(json.dumps({
            "LN01": {"state": "completed"}, "LN02": {"state": "failed", "error": "x"}},
            ensure_ascii=False), encoding="utf-8")
        segs = {s["v5"]["shot_name"]: s for s in
                webmap.storyboard_detail(self.proj, 1)["segments"]}
        self.assertEqual(segs["LN01"]["status"], "completed")
        self.assertEqual(segs["LN02"]["status"], "failed")
        self.assertEqual(segs["LN02"]["v5"]["job_error"], "x")

    def test_unresolved_tokens_are_surfaced(self):
        """分镜引用了注册表没有的资产 → **必须上报**，不许静默。"""
        (self.proj / "scenedesigner" / "scenedesigner.md").write_text(
            SB_MD.replace("@纸扎匠 的指尖", "@张三 的指尖"), encoding="utf-8")
        s = webmap.storyboard_detail(self.proj, 1)["segments"][0]
        self.assertIn("张三", s["v5"]["unresolved_tokens"])


class TestEpisodes(_Base):
    def test_episode_list_shape(self):
        self.write_brief(episodes=2)
        rows = webmap.episodes_storyboard(self.proj)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["episode_no"] for r in rows], [1, 2])
        self.assertEqual(rows[0]["storyboard"]["phase"], webmap.PHASE_DRAFT)
        self.assertIn("stats", rows[0])

    def test_scripts_from_disk(self):
        self.write_brief()
        d = self.proj / "scriptwriter"
        d.mkdir()
        (d / "scriptwriter_ep1.md").write_text("正文拍摄剧本", encoding="utf-8")
        ep = webmap.progress(self.proj)["episodes"][0]
        self.assertEqual(ep["script_status"], "completed")
        self.assertEqual(ep["content"], "正文拍摄剧本")


class TestOutline(_Base):
    def test_protagonist_by_id_not_identity(self):
        """★ 曾用 `a is prot` 比较对象身份 → 每次 `registry()` 都重解析 JSON，
        身份恒不等 → 所有角色都被判 supporting。必须按 id 比。"""
        self.write_assets()
        bios = webmap.progress(self.proj)["outline"]["character_biographies"]
        self.assertEqual(len(bios), 1)
        self.assertEqual(bios[0]["name"], "纸扎匠")
        self.assertEqual(bios[0]["character_role"], "protagonist",
                         "priority 最高的角色应判为主角")
        self.assertEqual(bios[0]["asset_id"], "a1")
        self.assertIn("深蓝棉袄", bios[0]["bio"])

    def test_no_llm_no_invention(self):
        """缺字段**留空**，绝不编（一次刷新调一次 LLM 也不可接受）。"""
        self.write_brief(genre="", must_have=[])
        o = webmap.progress(self.proj)["outline"]
        self.assertEqual(o["story_type"], "")
        self.assertEqual(o["story_summary"], "")
        self.assertEqual(o["target_audience"], "")

    def test_must_have_composed_into_summary(self):
        self.write_brief()
        o = webmap.progress(self.proj)["outline"]
        self.assertIn("接单", o["story_summary"])
        self.assertEqual(o["outline_status"], "draft")
        self.assertTrue(o["editable"])


class TestStyles(_Base):
    def test_three_packs_present(self):
        rows = webmap.styles()
        codes = [s["code"] for s in rows]
        self.assertIn("shortdrama", codes)
        # 2026-09-16：`3d-animation` 已随该包铲除而移除（见 .tmp/_cleaned-20260916/）。
        self.assertIn("niulai-movie-style", codes)
        self.assertNotIn("craft", codes, "craft/ 是技法库，不是类型包")

    def test_shortdrama_first(self):
        """★ `shortdrama` 必须排第一：它是 v5 的**回落基准包**，也是前端下拉的默认项。

        按字母序会默认选中 `3d-animation` → "进页面直接点开始创作"就跑了 3D 包
        （实测踩到：AI 创作出来的项目是 3D 动画风格）。
        """
        self.assertEqual(webmap.styles()[0]["code"], "shortdrama")

    def test_entry_fields(self):
        e = [s for s in webmap.styles() if s["code"] == "niulai-movie-style"][0]
        self.assertTrue(e["name"], "要有中文显示名")
        self.assertIs(e["still_refs"], False, "牛来包是 still-refs:false")
        self.assertEqual(e["audio_modes"][:2], ["silent", "dialogue-led"])

    def test_unknown_pack_falls_back_to_code(self):
        e = webmap._style_entry("no-such-pack")
        self.assertEqual(e["name"], "no-such-pack")


class TestFlow(_Base):
    def test_step_progression(self):
        self.assertEqual(webmap.current_step(self.proj), "outline", "没 brief → 第 1 步")
        self.write_brief()
        self.assertEqual(webmap.current_step(self.proj), "assets", "有 brief 无资产")
        self.write_assets()
        self.assertEqual(webmap.current_step(self.proj), "episodes", "有资产无分集")
        (self.proj / "plotdesigner").mkdir()
        (self.proj / "plotdesigner" / "episodes.md").write_text("第1集：接单", encoding="utf-8")
        self.assertEqual(webmap.current_step(self.proj), "storyboard")
        self.write_sb()
        self.assertEqual(webmap.current_step(self.proj), "render")
        md = self.proj / "media" / "ep1"
        md.mkdir(parents=True)
        (md / "episode_final.mp4").write_bytes(b"\x00")
        self.assertEqual(webmap.current_step(self.proj), "render")

    def test_flow_shape(self):
        f = webmap.flow(self.proj)
        self.assertEqual(f["current_step"], f["steps"][0]["key"])
        self.assertEqual(f["steps"][0]["state"], "active")
        self.assertFalse(f["review_passed"])

    def test_review_passed_marks_locked(self):
        self.write_brief()
        (self.proj / ".agent_state.json").write_text(
            json.dumps({"phases": {}, "review": {"passed": True}}, ensure_ascii=False),
            encoding="utf-8")
        r = webmap.progress(self.proj)
        self.assertEqual(r["outline"]["outline_status"], "locked")
        self.assertFalse(r["outline"]["editable"], "评审通过后概要应锁定")
        self.assertEqual(r["status"], "completed")


class TestRenderState(_Base):
    def test_counts_from_disk(self):
        self.write_brief()
        self.write_sb()
        md = self.proj / "media" / "ep1"
        (md / "stills").mkdir(parents=True)
        (md / "clips").mkdir(parents=True)
        for n in ("LN01", "LN02"):
            (md / "clips" / (n + ".mp4")).write_bytes(b"\x00")
        rs = webmap.progress(self.proj)["v5"]["render"]
        self.assertEqual(rs["expected_shots"], 2)
        self.assertEqual(rs["clips_ready"], 2)
        self.assertFalse(rs["final"])

    def test_estimated_seconds_not_ffprobe(self):
        """预计时长 = 各镜秒数之和（不 fork ffprobe、不起子进程）。"""
        self.write_brief()
        self.write_sb()
        ep = webmap.progress(self.proj)["episodes"][0]
        self.assertEqual(ep["duration_s"], 10.0, "6 + 4 秒")

    def test_still_relpath_uses_real_relative_path(self):
        """★ 集号在路径中间（`media/ep1/stills/LN01.jpg`）—— 拼字符串会拼错。"""
        p = self.proj / "media" / "ep1" / "stills"
        p.mkdir(parents=True)
        f = p / "LN01.jpg"
        f.write_bytes(b"\x00")
        rel = webmap._still_relpath(self.proj, {"path": str(f)})
        self.assertEqual(rel, "media/ep1/stills/LN01.jpg")
        self.assertEqual(webmap.media_url(self.pid, rel),
                         "/media/%s/media/ep1/stills/LN01.jpg" % self.pid)

    def test_still_relpath_outside_root_is_empty(self):
        """路径不在项目内 → 返回空，**绝不猜**（拼错的 URL 会稳定 404，更难查）。"""
        self.assertEqual(webmap._still_relpath(self.proj, {"path": "C:/elsewhere/a.jpg"}), "")
        self.assertEqual(webmap._still_relpath(self.proj, {}), "")


class TestProjectRow(_Base):
    def test_row_has_both_shapes(self):
        self.write_brief()
        rows = webmap.project_list()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["id"], self.pid)
        self.assertEqual(r["project_id"], self.pid)
        self.assertEqual(r["name"], r["projectName"])
        self.assertIn("flow", r)

    def test_row_shape_matches_progress(self):
        """★ 列表行与 progress 的**键集合必须一致**（除少数纯详情字段）。

        实测教训：列表原本缺 `outline` → 前端 `p.outline.story_type` 抛 TypeError、整页崩。
        形状不一致 = 消费方得记住"哪个端点给哪个键"，一定会漏。
        """
        self.write_brief()
        self.write_assets()
        row = webmap.project_list()[0]
        prog = webmap.progress(self.proj)
        for k in ("id", "name", "ratio", "style", "status", "cover", "created_at",
                  "episodes", "outline", "assets", "flow",
                  "project_id", "projectName", "planned_episode_count",
                  # 「精选项目」判据（2026-09-18 接通）：列表行与 progress 必须**同键同义**
                  "project_type", "is_demo"):
            self.assertIn(k, row, "列表行缺键：%s" % k)
            self.assertIn(k, prog, "progress 缺键：%s" % k)
        # outline 的关键子键也要在（视图直接取）
        for k in ("story_type", "story_summary", "world_setting",
                  "character_biographies", "outline_status", "editable"):
            self.assertIn(k, row["outline"])

    def test_cover_from_first_still(self):
        self.write_brief()
        p = self.proj / "media" / "ep1" / "stills"
        p.mkdir(parents=True)
        (p / "LN01.jpg").write_bytes(b"\x00")
        (self.proj / "media" / "ep1" / "stills.json").write_text(json.dumps(
            {"LN01": {"path": str(p / "LN01.jpg"), "url": "https://x/1.jpg"}},
            ensure_ascii=False), encoding="utf-8")
        self.assertEqual(webmap.project_list()[0]["cover"],
                         "/media/%s/media/ep1/stills/LN01.jpg" % self.pid)


class TestMediaBase(_Base):
    """★ 静态 URL 必须是**绝对**的：前后端不同源时，根相对路径会被解析到页面 origin
    → 全部 404（2026-09-15 真实浏览器实测：26 条 404 / 13 张坏图）。"""

    def test_default_is_root_relative(self):
        self.assertEqual(webmap.media_url("p", "images/a.png"), "/media/p/images/a.png")
        self.assertEqual(webmap.media_url("p", "/images/a.png"), "/media/p/images/a.png")

    def test_absolute_when_base_set(self):
        old = webmap.MEDIA_BASE
        try:
            webmap.set_media_base("http://127.0.0.1:8787")
            self.assertEqual(webmap.media_url("p", "images/a.png"),
                             "http://127.0.0.1:8787/media/p/images/a.png")
            # 尾斜杠要吃掉，否则会拼出 `//media`
            webmap.set_media_base("http://127.0.0.1:8787/")
            self.assertEqual(webmap.media_url("p", "images/a.png"),
                             "http://127.0.0.1:8787/media/p/images/a.png")
        finally:
            webmap.set_media_base(old)

    def test_flows_through_endpoints(self):
        """端到端：设了 base 之后，asset-refs 与分镜里的图片都该是绝对地址。"""
        old = webmap.MEDIA_BASE
        try:
            self.write_brief()
            self.write_assets()
            self.write_sb()
            p = self.proj / "media" / "ep1" / "stills"
            p.mkdir(parents=True)
            (p / "LN01.jpg").write_bytes(b"\x00")
            (self.proj / "media" / "ep1" / "stills.json").write_text(json.dumps(
                {"LN01": {"path": str(p / "LN01.jpg"), "url": "https://x/1.jpg",
                          "seconds": 6}}, ensure_ascii=False), encoding="utf-8")
            webmap.set_media_base("http://api.test:9")
            self.assertTrue(webmap.asset_refs(self.proj)["characters"][0]["states"][0]
                            ["thumbnail_url"].startswith("http://api.test:9/media/"))
            segs = webmap.storyboard_detail(self.proj, 1)["segments"]
            self.assertTrue(segs[0]["keyframe"].startswith("http://api.test:9/media/"),
                            "分镜关键帧也要绝对地址：%r" % segs[0]["keyframe"])
            self.assertIn("LN01.jpg", segs[0]["keyframe"])
        finally:
            webmap.set_media_base(old)


class TestVideoUrls(_Base):
    """★ 视频 URL 的路径正确性（**静帧对、视频全 404** 的隐蔽事故）。

    ⚠️ 写新类时**别漏 `class` 声明** —— 漏了的话这些方法会挂到**上一个类**里
    （已在 `tests_server.py` 踩过一次）。
    """

    def _shot_names(self, ep=1):
        return [s["v5"]["shot_name"]
                for s in webmap.storyboard_detail(self.proj, ep)["segments"]]

    def test_video_url_keeps_episode_segment(self):
        """★★ 视频 URL **必须带 `ep{N}/`** —— 这条曾漏过，症状极隐蔽。

        实测事故（2026-09-17，同源冒烟测试抓到）：
          `webmap` 里静帧走 `_still_relpath`（对），视频却**手拼** `media/clips/xx.mp4`
          → 少了 `ep{N}` → 前端 `<video>` 请求 `/media/<pid>/media/clips/LN01.mp4` **404**，
          而文件其实在 `media/ep1/clips/LN01.mp4`。
          **静帧全对、只有视频全 404** —— 只看静帧会以为一切正常。
        """
        self.write_sb()
        d = self.proj / "media" / "ep1" / "clips"
        d.mkdir(parents=True)
        names = self._shot_names()
        for n in names:
            (d / (n + ".mp4")).write_bytes(b"\x00")
        seg = webmap.storyboard_detail(self.proj, 1)["segments"][0]
        self.assertEqual(seg["v5"]["shot_name"], names[0])
        self.assertIn("/media/ep1/clips/", seg["video"],
                      "视频路径少了 `ep1/` → 前端必然 404：%r" % seg["video"])
        self.assertIn("/media/ep1/clips/", seg["selected_video_url"])
        self.assertTrue(seg["video"].endswith(".mp4"))

    def test_video_empty_when_clip_absent(self):
        """没有成片 → 空串（别编一个必然 404 的 URL）。"""
        self.write_sb()
        (self.proj / "media" / "ep1" / "clips").mkdir(parents=True)
        for s in webmap.storyboard_detail(self.proj, 1)["segments"]:
            self.assertEqual(s["video"], "")
            self.assertEqual(s["selected_video_url"], "")

    def test_video_url_uses_requested_episode(self):
        """要第 2 集的详情，视频路径就得是 `ep2/`（不能永远是 ep1）。"""
        self.write_sb()
        (self.proj / "scenedesigner" / "scenedesigner_ep2.md").write_text(
            SB_MD, encoding="utf-8")
        for ep in (1, 2):
            d = self.proj / "media" / ("ep%d" % ep) / "clips"
            d.mkdir(parents=True)
            for n in self._shot_names():
                (d / (n + ".mp4")).write_bytes(b"\x00")
        self.assertIn("/media/ep2/clips/",
                      webmap.storyboard_detail(self.proj, 2)["segments"][0]["video"])
        self.assertIn("/media/ep1/clips/",
                      webmap.storyboard_detail(self.proj, 1)["segments"][0]["video"])


class TestStylePreview(_Base):
    """风格预览：**真实样张** + 该包会注入的文案（用户说「不直观」→ 补的）。

    ⛔ **必须把 `SKILLS_DIR` 也 patch 到临时目录**：这些用例会造类型包，
    不 patch 就会把 `pmt*` 写进**真实的 `v5/skills/packs/`**
    （实测污染事故：`/styles` 立刻多出 3 个假包，前端风格库也跟着多 3 项）。
    `_Base.setUp` 只 patch 了 `PROJECTS_DIR`。
    """

    def setUp(self):
        super().setUp()
        self._sk = mock.patch.object(config, "SKILLS_DIR", self.root / "skills")
        self._sk.start()

    def tearDown(self):
        self._sk.stop()
        super().tearDown()

    def _mk_pack(self, name, visual_style=None, block=None):
        d = config.SKILLS_DIR / "packs" / name
        d.mkdir(parents=True, exist_ok=True)
        if visual_style is not None:
            (d / "pack.json").write_text(json.dumps(
                {"visual-style": visual_style}, ensure_ascii=False), encoding="utf-8")
        if block is not None:
            (d / "style-block.md").write_text(block, encoding="utf-8")
        return d

    def _mk_project(self, pid, pack, stills=1):
        p = self.root / pid
        (p / "media" / "ep1" / "stills").mkdir(parents=True)
        (p / "brief.json").write_text(json.dumps({"topic": "x", "pack": pack},
                                                 ensure_ascii=False), encoding="utf-8")
        for i in range(stills):
            (p / "media" / "ep1" / "stills" / ("LN%02d.jpg" % (i + 1))).write_bytes(b"\xff\xd8")
        return p

    def test_injectable_text_skips_hash_comments(self):
        """★ `#` 开头是**给维护者的注释**（不注入提示词）→ 预览不能显示它们。

        直接拿整篇 `style-block.md` 当预览，用户会看到"长度纪律""★ 光源不要硬编码"
        这类运维备注 —— 那是内部笔记，不是风格说明。
        """
        d = self._mk_pack("pmt", block=(
            "# pmt 风格块\n"
            "# 核心：这是一条注释，不该出现在预览里\n"
            "#\n"
            "# 长度纪律：超过 200 字会压掉画面内容\n"
            "\n"
            "视觉风格：真实注入的那一段文案。\n"
            "第二行也是注入内容。\n"
            "\n"
            "绝对不要：卡通、插画。\n"))
        txt = webmap._injectable_text(d)
        self.assertIn("视觉风格", txt)
        self.assertIn("第二行", txt, "同段多行要合并")
        self.assertNotIn("长度纪律", txt, "注释不该进预览")
        self.assertNotIn("不该出现在预览里", txt)
        self.assertNotIn("绝对不要", txt, "只取第一段")

    def test_visual_style_prefers_pack_json(self):
        d = self._mk_pack("pmt2", visual_style="来自 pack.json 的说明",
                          block="视觉风格：来自 style-block 的说明\n")
        e = {"pack": {"visual-style": "来自 pack.json 的说明"}}
        self.assertEqual(webmap._pack_visual_style(d, e), "来自 pack.json 的说明")

    def test_visual_style_falls_back_to_style_block(self):
        """★ `shortdrama` **没有 pack.json** → 不抽就是空的（它还是默认包）。"""
        d = self._mk_pack("pmt3", block="# 注释\n\n视觉风格：从风格块抽出来的文案。\n")
        self.assertEqual(webmap._pack_visual_style(d, {}), "视觉风格：从风格块抽出来的文案。")

    def test_sample_picks_deterministically(self):
        """样张选择必须**确定性**（同一份磁盘每次同一张，界面才不会闪）。"""
        self._mk_project("aaa-few", "pmt4", stills=2)
        self._mk_project("zzz-many", "pmt4", stills=5)
        s1 = webmap._pack_sample("pmt4")
        s2 = webmap._pack_sample("pmt4")
        self.assertEqual(s1, s2)
        self.assertEqual(s1["sample_project"], "zzz-many", "取静帧最多的那个项目")
        self.assertEqual(s1["sample_stills"], 5)
        self.assertIn("LN01", s1["sample_shot"], "同数量按名序取第一张")

    def test_sample_empty_when_no_project(self):
        """★ 没有样张就**空着**，不编（实测：`3d-animation` 一个项目都没有）。"""
        self.assertEqual(webmap._pack_sample("never-used-pack"), {})

    def test_sample_ignores_other_packs_and_hidden_dirs(self):
        self._mk_project("other-pack-proj", "another-pack", stills=9)
        self._mk_project("_hidden", "pmt5", stills=9)
        self.assertEqual(webmap._pack_sample("pmt5"), {}, "隐藏目录不算；别的包也不算")

    def test_cover_url_is_absolute_when_base_set(self):
        self._mk_project("proj-a", "pmt6", stills=1)
        old = webmap.MEDIA_BASE
        try:
            webmap.set_media_base("http://api.test:9")
            got = webmap._pack_sample("pmt6")
            self.assertTrue(got["cover_url"].startswith("http://api.test:9/media/"),
                            "外层是不同源部署 → 必须是绝对地址：%r" % got["cover_url"])
        finally:
            webmap.set_media_base(old)


class TestFrontendPackSnapshot(_Base):
    """★ **漂移检测**：前端的离线包快照必须与 `styles()` 一致。

    前端的 `web/assets/js/data/packs.js` 是 `_tools/gen_packs.py` 从
    `webmap.styles()` 生成的**快照**（离线模式下风格库显示它）。
    v5 加了包而忘了重跑生成脚本 → 用户界面上的风格库就少一个包
    （实测事故：新增第 4 个包 `wool-felt-story-short` 时后端硬编列表静默拒了它）。

    ★ 2026-09-18：前端**应用代码已搬进本仓库** `web/`，所以这里不再需要跨项目猜路径。
    """

    def _snapshot(self):
        """同仓库的 `web/assets/js/data/packs.js`。

        ⚠️ 搬进本仓库后**不再"找不到就跳过"** —— 跳过会把"文件丢了"藏起来，
        而这正是这套漂移检测要防的事。缺了就直接失败。
        """
        p = config.PROJECT_ROOT / "web" / "assets" / "js" / "data" / "packs.js"
        self.assertTrue(p.is_file(),
                        "找不到 %s —— 前端应用应在本仓库 `web/` 下（或重跑 gen_packs.py）" % p)
        return p

    def test_snapshot_exists_in_repo(self):
        """前端应用代码**应该**在 `web/` 里（搬过之后就位；缺了要能立刻看出来）。"""
        p = config.PROJECT_ROOT / "web" / "index.html"
        self.assertTrue(p.is_file(),
                        "`web/index.html` 不存在 —— 前端应用应在本仓库 `web/` 下")

    def test_web_tree_has_no_pavo_offline_dependency(self):
        """★ 应用代码搬进 `web/` 后**不该再引用 `pavo-offline` 路径**（否则等于没搬干净）。

        `_tools/` 里的验证脚本仍可能提到它（那是另一个目录的事），但
        **`web/` 下的应用代码**——尤其是 `index.html` 与 `assets/js/**`——
        不该出现对 `pavo-offline` 的路径依赖。
        """
        bad = []
        for f in (config.PROJECT_ROOT / "web").rglob("*"):
            if not f.is_file() or f.suffix.lower() not in (".html", ".js", ".css"):
                continue
            txt = f.read_text(encoding="utf-8", errors="replace")
            for ln, line in enumerate(txt.splitlines(), 1):
                # `pavo-offline:v1` 是 **localStorage 的键名**，不是路径 —— 放行
                if "pavo-offline" in line and "pavo-offline:v1" not in line:
                    bad.append("%s:%d %s" % (f.relative_to(config.PROJECT_ROOT), ln, line.strip()[:70]))
        self.assertEqual(bad, [], "web/ 里仍有对 pavo-offline 的引用：\n  " + "\n  ".join(bad))

    def test_snapshot_matches_backend(self):
        f = self._snapshot()
        txt = f.read_text(encoding="utf-8")
        m = re.search(r'"packs"\s*:\s*(\[.*\])\s*\n\}', txt, re.S)
        self.assertIsNotNone(m, "packs.js 里没找到 `packs` 数组（生成脚本改了？）")
        snap = json.loads(m.group(1))
        codes = [s["code"] for s in snap]
        live = [s["code"] for s in webmap.styles()]
        self.assertEqual(codes, live,
                         "前端包快照与后端不一致 → 请重跑 `python _tools/gen_packs.py`\n"
                         "  快照：%s\n  后端：%s" % (codes, live))

    def test_snapshot_carries_name_and_group(self):
        f = self._snapshot()
        m = re.search(r'"packs"\s*:\s*(\[.*\])\s*\n\}', f.read_text(encoding="utf-8"), re.S)
        snap = json.loads(m.group(1))
        for s in snap:
            self.assertTrue(s.get("name"), "包 %s 缺 name（前端要显示它）" % s.get("code"))
            self.assertTrue(s.get("group"), "包 %s 缺 group（前端按 group 分类）" % s.get("code"))


class TestHealth(_Base):
    def test_health_shape(self):
        h = webmap.health()
        self.assertTrue(h["ok"])
        self.assertIn("shortdrama", h["packs"])
        self.assertEqual(h["aspect_ratio"], config.ASPECT_RATIO)


if __name__ == "__main__":
    unittest.main(verbosity=2)
