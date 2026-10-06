# -*- coding: utf-8 -*-
"""`v5/inbox.py`（导演信箱 + 画布改动台账）自测。

## 这条信道为什么会存在

`hitl.decide` 要求链**正挂在步级门上**，而 `manual_steps` 默认关 ⇒ 链跑起来时人
说的话无处可去；`webwrite` 改分镜表**不碰链的失效机制** ⇒ 人在画布上改完，导演
下一轮读到的仍是"表没被改过"。本模块补这两条，所以测试要钉住的是**投递语义**，
不是"文件写没写成功"。

## 判据形状（三向夹具，缺一种就是假绿灯）

| 方向 | 用例 |
|---|---|
| 应送达 | 无定向消息 → 下一个派发的角色拿到；`edit` → 两个表主都拿到 |
| 应拦住 | 定向给 reviewer 的消息**不许**被 scenedesigner 吃掉 |
| 前提不存在 | 空信箱 → `render_block` 返回**空串**（一行都不加，既有链路零变化）|

★ 另有两条**反向对照**（把旧病装回去证明会红）：
  · `..._is_not_consumed_by_wrong_role` —— 若实现退化成"谁先派发谁消费"，这条红；
  · `test_webwrite_edit_...` —— 若 `webwrite` 退化成不记账（改造前的行为），这条红。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v5 import inbox, roles, webwrite  # noqa: E402

SB_MD = """# 分镜：纸扎铺

| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 | 场景 |
|--------|------|------|------|---------|---------|------|------|------|
| 1 | 全景 | 平视 | 固定 | 6 | 纸扎匠在逼仄的铺子里看着空钱盒，@纸扎匠 的指尖刚触到盒沿又停住 | 纸扎匠：这单我接了。 | 环境音 | 纸扎铺 |
| 2 | 近景 | 俯视 | 缓推 | 4 | @纸扎匠 的手指挪到免提手机边缘，屏幕亮着 | 富人：钱给你十倍。 | 手机电流声 | 纸扎铺 |
"""

BRIEF = {"topic": "纸扎铺", "pack": "shortdrama", "genre": "悬疑",
         "episodes": 1, "must_have": ["纸扎匠看着空钱盒"], "key_props": [],
         "禁忌": [], "tone": "冷", "结局": "定格", "protagonist": "纸扎匠"}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "demo"
        (self.root / "scenedesigner").mkdir(parents=True)
        (self.root / "brief.json").write_text(
            json.dumps(BRIEF, ensure_ascii=False), encoding="utf-8")
        self.sb = self.root / "scenedesigner" / "scenedesigner_ep1.md"
        self.sb.write_text(SB_MD, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()


class TestInboxBasics(_Base):
    def test_empty_inbox_injects_not_a_single_line(self):
        """★ 前提不存在也要是"零注入"，不是"注入一句标题"。

        否则每一次派发都多一段废话，且旧项目升级后行为就变了。
        """
        self.assertEqual("", inbox.render_block(self.root, "scenedesigner", 1))
        self.assertEqual("", inbox.render_block(self.root, "reviewer", 1))

    def test_message_reaches_next_dispatch_and_only_once(self):
        inbox.append_message(self.root, "LN02 的手机要换成老式转盘电话", ep=1, by="lex")
        b = inbox.render_block(self.root, "dialogue", 1)
        self.assertIn("老式转盘电话", b)
        self.assertIn("lex", b)
        # 消费一次：下一次派发不该再看到
        self.assertEqual("", inbox.render_block(self.root, "scenedesigner", 1))

    def test_directed_message_is_not_consumed_by_the_wrong_role(self):
        """★ 反向对照：退化成"谁先派发谁消费"时这条必须红。

        定向消息被别的角色吃掉 = 人以为话送到了分镜师，实际被世界架构师消费了，
        而**没有任何一处会报错**。
        """
        inbox.append_message(self.root, "评审请注意：结局镜不许有旁白", ep=1,
                             to="reviewer")
        self.assertEqual("", inbox.render_block(self.root, "scenedesigner", 1))
        # 没被吃掉 ⇒ 仍然排着队
        self.assertEqual(1, len(inbox.pending_messages(self.root, ep=1)))
        b = inbox.render_block(self.root, "reviewer", 1)
        self.assertIn("结局镜不许有旁白", b)
        self.assertEqual(0, len(inbox.pending_messages(self.root, ep=1)))

    def test_unknown_target_is_a_loud_error_not_a_silent_broadcast(self):
        """非法 `to` 必须**响亮报错**。静默降级成空串 = 变成广播，语义完全反过来。"""
        with self.assertRaises(ValueError):
            inbox.append_message(self.root, "hi", ep=1, to="scene_designer")
        with self.assertRaises(ValueError):
            inbox.append(self.root, "nonsense-kind", "hi", ep=1)
        with self.assertRaises(ValueError):
            inbox.append_message(self.root, "   ", ep=1)

    def test_reading_an_inbox_creates_no_directory(self):
        """读端点不许在盘上留东西。

        实测：`GET .../director/inbox` 原先经 `dir_of()` 顺带 mkdir，
        对 67 个项目扫一遍就在每个下面留一个空 `.tmp/inbox/`。
        """
        self.assertFalse((self.root / inbox.DIR_NAME).exists())
        inbox.items(self.root)
        inbox.history(self.root, ep=1)
        inbox.render_block(self.root, "reviewer", 1)
        self.assertFalse((self.root / inbox.DIR_NAME).exists())
        # 写一次才建目录
        inbox.append_message(self.root, "现在才写", ep=1)
        self.assertTrue((self.root / inbox.DIR_NAME).exists())

    def test_corrupt_inbox_file_degrades_to_empty_not_crash(self):
        """半截 JSON 不许把创作链弄死 —— `render_block` 必须返回空串。"""
        inbox.append_message(self.root, "先写一条", ep=1)
        inbox.path_of(self.root).write_text('{"seq": 1, "items": [', encoding="utf-8")
        self.assertEqual("", inbox.render_block(self.root, "reviewer", 1))
        self.assertEqual([], inbox.items(self.root))


class TestEditLedger(_Base):
    def test_ledger_reaches_both_table_owners(self):
        inbox.append_edit(self.root, "LN01", "visual", "旧描述", "新描述", ep=1)
        for who in ("scenedesigner", "reviewer"):
            b = inbox.render_block(self.root, who, 1)
            self.assertIn("LN01", b, who)
            self.assertIn("新描述", b, who)
            self.assertIn("人工编辑", b, who)

    def test_ledger_never_reaches_non_table_roles(self):
        """世界架构师拿到"人改了 LN01 的画面描述"只会去改世界文档 —— 不该给它。"""
        inbox.append_edit(self.root, "LN01", "visual", "旧", "新", ep=1)
        for who in ("worldbuilder", "assetdesigner", "plotdesigner",
                    "scriptwriter", "dialogue"):
            self.assertEqual("", inbox.render_block(self.root, who, 1), who)

    def test_ledger_prunes_once_the_storyboard_is_rewritten(self):
        """表被分镜师重写 ⇒ 人改的那几行**已经不在盘上了** ⇒ 台账必须作废。

        不剪的话，下一轮 scenedesigner 会去"修正"一个已经不存在的差异。
        """
        inbox.append_edit(self.root, "LN01", "visual", "旧描述", "新描述", ep=1)
        self.assertEqual(1, len(inbox.live_edits(self.root, 1)))
        self.sb.write_text(SB_MD.replace("纸扎匠在逼仄的铺子里", "分镜师整表重写后的内容"),
                           encoding="utf-8")
        self.assertEqual(0, len(inbox.live_edits(self.root, 1)))
        self.assertEqual(1, inbox.prune_stale_edits(self.root, 1))
        self.assertEqual("", inbox.render_block(self.root, "scenedesigner", 1))

    def test_ledger_survives_across_dispatches_until_table_changes(self):
        """`edit` **不是**消费一次就消失 —— 表主每一轮都该看到（与 message 相反）。"""
        inbox.append_edit(self.root, "LN01", "visual", "旧", "新", ep=1)
        self.assertIn("LN01", inbox.render_block(self.root, "scenedesigner", 1))
        self.assertIn("LN01", inbox.render_block(self.root, "reviewer", 1))
        self.assertIn("LN01", inbox.render_block(self.root, "scenedesigner", 1))

    def test_delete_ledger_names_the_position_not_the_shifted_name(self):
        """删一镜后**每一镜的名字全体前移** ⇒ 只报镜名会指向隔壁那一镜。"""
        rec = inbox.append_edit(self.root, "LN02", "", "", "", ep=1,
                                action="delete", position=2)
        self.assertIn("第 2 位", rec["text"])
        self.assertIn("删除时它叫 LN02", rec["text"])

    def test_missing_storyboard_yields_no_fingerprint_and_no_prune(self):
        """表读不到时 `live_edits` 不该把账全剪掉（那会静默丢人的改动记录）。"""
        inbox.append_edit(self.root, "LN01", "visual", "旧", "新", ep=1)
        self.sb.unlink()
        self.assertEqual("", inbox.storyboard_fingerprint(self.root, 1))
        self.assertEqual(1, len(inbox.live_edits(self.root, 1)))


class TestWebwriteRecordsLedger(_Base):
    """★ 反向对照：`webwrite` 退化成改造前的"只改表不记账"，这一组必须红。"""

    def test_update_segment_records_one_edit_per_changed_field(self):
        out = webwrite.update_segment(self.root, 1, "LN01",
                                      {"visual": "纸扎匠把空钱盒翻过来抖了抖"},
                                      source="canvas", by="lex")
        self.assertIn("visual", out["applied"])
        self.assertEqual(1, out["logged"])
        edits = inbox.live_edits(self.root, 1)
        self.assertEqual(1, len(edits))
        self.assertEqual("canvas", edits[0]["meta"]["source"])
        self.assertEqual("LN01", edits[0]["meta"]["shot"])
        self.assertIn("画布", edits[0]["text"])
        self.assertIn("纸扎匠把空钱盒翻过来抖了抖", edits[0]["text"])

    def test_writing_the_same_value_back_records_nothing(self):
        """人拖了一下又拖回原样 ⇒ 不该留一条假账（导演会去"尊重"一个没发生的改动）。"""
        same = webwrite.update_segment(self.root, 1, "LN01",
                                       {"seconds": "6"}, source="canvas")
        self.assertEqual(0, same["logged"])
        self.assertEqual(0, len(inbox.live_edits(self.root, 1)))

    def test_two_fields_one_call_record_two_rows(self):
        webwrite.update_segment(self.root, 1, "LN02",
                                {"visual": "@纸扎匠 伸手把手机屏幕按灭，指节发白",
                                 "seconds": "8"}, source="web")
        self.assertEqual(2, len(inbox.live_edits(self.root, 1)))

    def test_ledger_recorded_after_the_write_so_the_fp_matches(self):
        """指纹必须是**改完之后**的 —— 用改之前的，这条账下一轮就被自己剪掉了。"""
        webwrite.update_segment(self.root, 1, "LN01",
                                {"visual": "纸扎匠把钱盒举到灯下看盒底"}, source="canvas")
        self.assertEqual(inbox.storyboard_fingerprint(self.root, 1),
                         inbox.live_edits(self.root, 1)[0]["sb_fp"])
        self.assertEqual(1, len(inbox.live_edits(self.root, 1)))

    def test_delete_segment_records_a_ledger_row(self):
        webwrite.delete_segment(self.root, 1, "LN02", source="canvas")
        edits = inbox.live_edits(self.root, 1)
        self.assertEqual(1, len(edits))
        self.assertEqual("delete", edits[0]["meta"]["action"])


class TestRoleInputInjection(_Base):
    """端到端：信箱真的进了 `roles.role_input`（那是每次派发都经过的唯一注入点）。"""

    def _input(self, role):
        return roles.role_input(role, self.root, {"episode_index": 1}, None)

    def test_inbox_text_appears_in_dispatch_input(self):
        inbox.append_message(self.root, "第三镜改成从门外拍进来", ep=1, by="lex")
        self.assertIn("第三镜改成从门外拍进来", self._input("scenedesigner"))

    def test_no_inbox_means_input_identical_to_before(self):
        """★ 零回归证明：不开这条信道时，注入文本里**一个子都没多**。"""
        self.assertNotIn("人工入站", self._input("scenedesigner"))
        self.assertNotIn("人工编辑", self._input("reviewer"))

    def test_canvas_edit_is_visible_in_dispatch_input(self):
        webwrite.update_segment(self.root, 1, "LN01",
                                {"visual": "纸扎匠把钱盒压在柜台最下层"}, source="canvas")
        self.assertIn("LN01", self._input("reviewer"))


class TestMessageCaps(_Base):
    def test_long_message_is_clipped_and_flagged(self):
        rec = inbox.append_message(self.root, "长" * (inbox.MAX_TEXT + 500), ep=1)
        self.assertTrue(rec["clipped"])
        self.assertEqual(inbox.MAX_TEXT, len(rec["text"]))

    def test_pending_messages_are_capped_per_dispatch(self):
        """刷屏不许挤掉上游产物 —— `role_input` 拼起来的总量才是真 token 预算。"""
        for i in range(inbox.MAX_PENDING_MESSAGES + 6):
            inbox.append_message(self.root, "第%d条" % i, ep=1)
        b = inbox.render_block(self.root, "reviewer", 1)
        self.assertEqual(inbox.MAX_PENDING_MESSAGES, b.count("[message "))


class TestRoutes(_Base):
    """HTTP 面：`/director/*` 三条 + `source`/`by` 不许漏进分镜列。

    用 `TestClient` 直连 ASGI app（本机沙箱里 uvicorn 不响应 socket，同 `tests_server`）。
    """

    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from v5 import config, server
        self._p = mock.patch.object(config, "PROJECTS_DIR", self.root.parent)
        self._p.start()
        self.pid = self.root.name
        self.c = TestClient(server.create_app(), raise_server_exceptions=False)

    def tearDown(self):
        self._p.stop()
        super().tearDown()

    def _url(self, tail):
        return "/v1/pixa/short-drama/projects/%s%s" % (self.pid, tail)

    def test_post_message_then_get_inbox_shows_it_queued(self):
        r = self.c.post(self._url("/director/message"),
                        json={"text": "LN01 的钱盒要漆成朱红", "ep": 1, "by": "lex"})
        self.assertEqual(200, r.status_code, r.text)
        rec = r.json()["data"]
        self.assertEqual("", rec["delivered_to"])     # 还没派发 ⇒ 排队中，不是"已送达"
        g = self.c.get(self._url("/director/inbox") + "?ep=1")
        self.assertEqual(200, g.status_code, g.text)
        d = g.json()["data"]
        self.assertEqual(1, d["stats"]["pending"])
        self.assertIn("朱红", d["messages"][0]["text"])
        self.assertIn("hitl", d)                      # 右栏一次调用拿全套事实

    def test_illegal_target_is_400_not_silently_broadcast(self):
        r = self.c.post(self._url("/director/message"),
                        json={"text": "hi", "to": "scene_desinger"})
        self.assertEqual(400, r.status_code, r.text)

    def test_empty_text_is_400(self):
        r = self.c.post(self._url("/director/message"), json={"text": "  "})
        self.assertEqual(400, r.status_code, r.text)

    def test_canvas_source_edit_does_not_leak_metadata_into_skipped(self):
        """★ 反向对照：若 `server` 忘了 pop `source`/`by`，它们会进 `skipped`
        并回一条"这些列不存在"的警告 —— 画布每次编辑都弹假警告。"""
        sid = "%s-ep1-LN01" % self.pid
        r = self.c.post("/v1/pixa/short-drama/segments/" + sid,
                        json={"visual": "纸扎匠把钱盒举到灯下看盒底",
                              "source": "canvas", "by": "lex"})
        self.assertEqual(200, r.status_code, r.text)
        d = r.json()["data"]
        self.assertEqual([], d["skipped"])
        self.assertNotIn("warning", d)
        self.assertEqual(1, d["logged"])
        self.assertEqual(1, len(inbox.live_edits(self.root, 1)))

    def test_redo_without_pending_and_without_target_is_400(self):
        """没挂起又不说打回谁 ⇒ 不能猜。猜错 = 白重跑一个角色。"""
        r = self.c.post(self._url("/director/redo"), json={"note": "重来"})
        self.assertEqual(400, r.status_code, r.text)
        self.assertIn("target", r.text)



if __name__ == "__main__":
    unittest.main(verbosity=2)
