# -*- coding: utf-8 -*-
"""`media_rerender` 子代理的自测（假 runner，不碰 API、不碰媒体链）。

这组用例守三件事：

1. **镜号解析 fail-closed**（spec §3.4 风险 #6）：解析不出/有歧义 → **什么都不做**。
   猜一个镜号去烧视频配额，比不动坏得多。
2. **`from_still` 只认显式标记**：裸「静帧」是**原因描述**（"静帧里窗帘没开"），
   不是"连静帧一起重做"的指令；误开的代价是白画一张图。
3. **回话必须诚实**：`blocked` / `incomplete` / `residual` 要出现在给 director 的文字里
   —— 否则弱模型会顺着"已重渲"的语气编出"修好了"（项目里已有同类幻觉记录）。
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import HumanMessage  # noqa: E402

from v5.media import rerender_agent, scaffold  # noqa: E402
from v5.media.rerender_agent import format_result, parse_instruction  # noqa: E402

KNOWN = ["LN01", "LN02", "LN03", "LN04", "LN05", "LN06"]


class TestParseInstruction(unittest.TestCase):
    def test_header_form(self):
        p = parse_instruction("【重渲】LN03\n周奶奶没出现", KNOWN)
        self.assertEqual(p["shots"], ["LN03"])
        self.assertFalse(p["from_still"])
        self.assertEqual(p["problem"], "")

    def test_header_with_multiple_shots_and_still(self):
        p = parse_instruction("【重渲】LN03,LN05 from_still\n窗帘没全开", KNOWN)
        self.assertEqual(p["shots"], ["LN03", "LN05"])
        self.assertTrue(p["from_still"])

    def test_escaped_newline_does_not_swallow_later_shots(self):
        """★ 实测（2026-09-13 探针）：模型有时把换行写成**转义 `\\n`** ——
        于是"头行"实际上吃掉了整段理由，理由里提到的别的镜号会被误当成重渲目标
        （重渲错的镜 = 白烧一份视频配额）。解析必须**按语法单元截断**。
        """
        p = parse_instruction(
            "【重渲】LN02,LN04\\n用户原话：LN02 和 LN04 都有水印，不像 LN06 那样干净",
            KNOWN)
        self.assertEqual(p["shots"], ["LN02", "LN04"])
        self.assertNotIn("LN06", p["shots"], "理由里提及的镜号不是目标")

    def test_header_stops_at_first_non_target_token(self):
        p = parse_instruction("【重渲】LN03 然后 顺便看看 LN05", KNOWN)
        self.assertEqual(p["shots"], ["LN03"], "「然后」之后的不算目标")

    def test_ascii_header_and_lowercase_shot(self):
        p = parse_instruction("[rerender] ln3\nbad frame", KNOWN)
        self.assertEqual(p["shots"], ["LN03"])

    def test_prose_with_exactly_one_shot_is_accepted(self):
        p = parse_instruction("LN03 的窗帘没开，请重做", KNOWN)
        self.assertEqual(p["shots"], ["LN03"])
        self.assertEqual(p["problem"], "")

    def test_prose_with_two_shots_is_refused(self):
        """★ 歧义必须拒绝：解释性提及（"不像 LN05 那样"）会把无辜的镜也重渲掉。"""
        p = parse_instruction("LN03 的窗帘没开，不像 LN05 那样", KNOWN)
        self.assertEqual(p["shots"], [])
        self.assertIn("2 个不同镜号", p["problem"])
        self.assertIn("LN03", p["problem"])

    def test_invented_shot_is_refused(self):
        """臆造镜号 → 不算镜号 → 拒绝（分镜里没有 LN99）。"""
        p = parse_instruction("【重渲】LN99\n随便改改", KNOWN)
        self.assertEqual(p["shots"], [])
        self.assertTrue(p["problem"])

    def test_no_shot_at_all_is_refused_with_teaching_message(self):
        p = parse_instruction("把窗帘改成全开", KNOWN)
        self.assertEqual(p["shots"], [])
        self.assertIn("头行", p["problem"])
        self.assertIn("LN01", p["problem"], "拒绝时必须把可用镜号回给 director")

    def test_chinese_nth_maps_through_storyboard(self):
        """「第3镜」→ 分镜里第 3 个镜号。**必须靠分镜映射**，映射不出就不给。"""
        self.assertEqual(parse_instruction("重做第3镜", KNOWN)["shots"], ["LN03"])
        self.assertEqual(parse_instruction("重做第3镜", [])["shots"], [],
                         "没有分镜就不许猜")

    def test_bare_still_word_is_not_a_marker(self):
        """裸「静帧」是原因描述，不是指令 —— 误开的代价是白画一张图。"""
        self.assertFalse(parse_instruction("【重渲】LN03\n静帧里窗帘没开", KNOWN)["from_still"])
        self.assertTrue(parse_instruction("【重渲】LN03\n连静帧一起重做", KNOWN)["from_still"])
        self.assertTrue(rerender_agent.has_still_marker("--from still"))

    def test_note_is_carried_through(self):
        p = parse_instruction("【重渲】LN03\n周奶奶不该出现", KNOWN)
        self.assertIn("周奶奶", p["note"])


class TestFormatResult(unittest.TestCase):
    def test_ok(self):
        s = format_result(["LN03"], {"status": "ok", "shots": 6, "seconds": 28.2,
                                     "final": "/x/final.mp4", "clips": 6}, False)
        self.assertIn("完成", s)
        self.assertIn("LN03", s)
        self.assertIn("/x/final.mp4", s)

    def test_ok_without_still_warns_that_still_was_not_touched(self):
        """★ "静帧没改"是**静默前提**，必须说破。

        director 对"问题在静帧层还是成片层"的判断**不稳**（三次探测里那类请求
        都派发失败）。若不提示，人看到"✅ 完成"会以为人物长相也修了 ——
        而"人物画错"这类问题**只在视频层重渲是修不掉的**，于是白等一轮再来一次。
        """
        s = format_result(["LN03"], {"status": "ok", "shots": 6}, False)
        self.assertIn("静帧**未改动**", s)
        self.assertIn("from_still", s)
        s2 = format_result(["LN03"], {"status": "ok", "shots": 6}, True)
        self.assertIn("一起重做", s2)
        self.assertNotIn("未改动", s2)

    def test_blocked_reports_gate_and_never_claims_success(self):
        s = format_result(["LN03"], {"status": "blocked", "gate": "media-lock",
                                     "reason": "已有媒体链在运行"}, False)
        self.assertIn("未执行", s)
        self.assertIn("media-lock", s)
        self.assertIn("已有媒体链在运行", s)

    def test_incomplete_and_residual_are_surfaced(self):
        s = format_result(["LN03"], {"status": "incomplete", "missing": ["LN03"],
                                     "residual": ["LN03"], "reason": ""}, False)
        self.assertIn("未成功", s)
        self.assertIn("带伤出厂", s)
        self.assertNotIn("✅", s)


class TestRerenderGraph(unittest.TestCase):
    """节点行为：解析 → 调 runner → 回一段**给 director 看**的话。"""

    def _graph(self, calls: list):
        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        (root / "scenedesigner").mkdir(parents=True)
        (root / "scenedesigner" / "scenedesigner.md").write_text(
            scaffold.build("t", [{"name": "店-夜",
                                  "shots": [{"seconds": 5}] * 3}]),
            encoding="utf-8")

        def fake_runner(project_root, names, note="", from_still=False):
            calls.append({"root": project_root, "names": list(names),
                          "note": note, "from_still": from_still})
            return {"status": "ok", "shots": 3, "seconds": 12.0, "clips": 3,
                    "final": "/x/f.mp4", "requeued": [], "residual": []}

        g = rerender_agent.build_rerender_graph(root, runner=fake_runner)
        return d, g

    def _call(self, g, text):
        out = asyncio.run(g.ainvoke({"messages": [HumanMessage(content=text)]}))
        return str(out["messages"][-1].content)

    def test_valid_instruction_reaches_the_pipeline(self):
        calls: list = []
        d, g = self._graph(calls)
        with d:
            msg = self._call(g, "【重渲】LN02\n第二镜的人脸飘了")
        self.assertEqual(len(calls), 1, "合法指令必须落到 pipeline")
        self.assertEqual(calls[0]["names"], ["LN02"])
        self.assertFalse(calls[0]["from_still"])
        self.assertIn("完成", msg)

    def test_ambiguous_instruction_never_touches_the_pipeline(self):
        calls: list = []
        d, g = self._graph(calls)
        with d:
            msg = self._call(g, "LN01 不对，也不像 LN03 那样")
        self.assertEqual(calls, [], "歧义时一镜都不许提交（烧错镜 = 白烧配额）")
        self.assertIn("未执行", msg)

    def test_from_still_is_forwarded(self):
        calls: list = []
        d, g = self._graph(calls)
        with d:
            self._call(g, "【重渲】LN03 from_still\n人物长相不对")
        self.assertTrue(calls[0]["from_still"])

    def test_node_name_is_unique(self):
        """节点名必须唯一（LangGraph 的命名空间按调用序分配，重名会串状态）。"""
        calls: list = []
        d, g = self._graph(calls)
        with d:
            self.assertIn(rerender_agent.NODE_NAME, g.get_graph().nodes)


if __name__ == "__main__":
    unittest.main()
