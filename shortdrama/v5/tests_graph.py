# -*- coding: utf-8 -*-
"""结构化判定 / 路由 / 状态机的纯逻辑自测（不调 API）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langgraph.graph import END  # noqa: E402

from v5 import decision, guards, roles  # noqa: E402
from v5.media import jobs  # noqa: E402


class TestDecisionParse(unittest.TestCase):
    def test_yaml_block(self):
        text = """# 质量审核报告

## 评分
- 剧情逻辑：6/10

```yaml
pass: false
rerun: [scriptwriter]
reasons:
  - 第二幕缺 must_have 里的「老人递上纸人」
  - 对白 LN12 与人物设定冲突
```
"""
        d = decision.parse_decision(text)
        self.assertFalse(d["pass"])
        self.assertEqual(d["rerun"], ["scriptwriter"])
        self.assertEqual(len(d["reasons"]), 2)

    def test_json_block(self):
        text = '报告正文\n```json\n{"pass": true, "rerun": [], "reasons": []}\n```\n'
        d = decision.parse_decision(text)
        self.assertTrue(d["pass"])
        self.assertEqual(d["rerun"], [])

    def test_pass_with_rerun_takes_rerun(self):
        d = decision.parse_decision('```yaml\npass: false\nrerun: dialogue\n```')
        self.assertEqual(d["rerun"], ["dialogue"])

    def test_rerun_normalized_and_deduped(self):
        d = decision.parse_decision(
            '```yaml\npass: false\nrerun: ["/SCRIPTWRITER/", "剧本", "scriptwriter"]\n```')
        self.assertEqual(d["rerun"], ["scriptwriter"])

    def test_rerun_rejects_reviewer(self):
        """回退到 reviewer 自己 = 死循环，必须被过滤掉。"""
        d = decision.parse_decision('```yaml\npass: false\nrerun: [reviewer]\n```')
        self.assertEqual(d["rerun"], [])

    def test_rerun_ordered_upstream_first(self):
        """多角色时按图的顺序排序，最上游的在前。"""
        d = decision.parse_decision(
            '```yaml\npass: false\nrerun: [dialogue, director, scenedesigner]\n```')
        self.assertEqual(d["rerun"], ["director", "dialogue", "scenedesigner"])
        self.assertEqual(decision.route_target(d["rerun"]), "director")

    def test_no_block_returns_none(self):
        """没有结构化块 → None，调用方不得猜测。"""
        self.assertIsNone(decision.parse_decision("只有一份人看的评分报告。"))
        self.assertIsNone(decision.parse_decision(""))

    def test_string_rerun(self):
        d = decision.parse_decision('```yaml\npass: no\nrerun: scenedesigner\n```')
        self.assertFalse(d["pass"])
        self.assertEqual(d["rerun"], ["scenedesigner"])

    def test_missing_pass_defaults_from_rerun(self):
        d = decision.parse_decision('```yaml\nrerun: [dialogue]\n```')
        self.assertFalse(d["pass"])
        d2 = decision.parse_decision('```yaml\nrerun: []\n```')
        self.assertTrue(d2["pass"])

    def test_legacy_needs_revision(self):
        """旧格式（needs_revision / revision_target / issues）也要能读。"""
        text = ('```json\n{"scores": {"plot": 8}, "needs_revision": true, '
                '"revision_target": "scriptwriter", '
                '"issues": [{"level": "P0", "desc": "剧本时长与分镜不一致"}]}\n```')
        d = decision.parse_decision(text)
        self.assertFalse(d["pass"])
        self.assertEqual(d["rerun"], ["scriptwriter"])
        self.assertIn("P0", d["reasons"][0])
        self.assertTrue(d["legacy"])


class TestRerunTargetCrossCheck(unittest.TestCase):
    """回退目标的交叉校验：防「问题在上游、rerun 填下游」白跑一轮。

    事故（2026-09-10）：评审把 director 表格里 S04/S05 光列的问题，填成
    rerun=[plotdesigner]。而下游角色**没有权限修改上游的文件** → 那一轮 13 分钟
    完全白跑（director 文件纹丝未动），下一轮评审才回头填 director。
    """

    def test_without_owners_keeps_legacy_behaviour(self):
        """老产物没有 reason_owners → 完全保持旧行为（只认 rerun），零风险兼容。"""
        d = decision.parse_decision('```yaml\npass: false\nrerun: [plotdesigner]\n```')
        self.assertEqual(d["owners"], [])
        self.assertEqual(decision.resolve_target(d), "plotdesigner")

    def test_owners_upstream_overrides_downstream_rerun(self):
        """rerun 填了下游、owner 在上游 → 上溯到 owner。"""
        text = ('```yaml\npass: false\nrerun: [plotdesigner]\n'
                'reason_owners: [director]\n'
                'reasons:\n  - S04 光列与灯态矛盾\n```')
        d = decision.parse_decision(text)
        self.assertEqual(decision.route_target(d["rerun"]), "plotdesigner")
        self.assertEqual(decision.resolve_target(d), "director")

    def test_rerun_upstream_wins(self):
        """rerun 本身就比 owner 更上游 → 以 rerun 为准（不过度回退）。"""
        text = ('```yaml\npass: false\nrerun: [director]\n'
                'reason_owners: [scenedesigner]\nreasons:\n  - x\n```')
        d = decision.parse_decision(text)
        self.assertEqual(decision.resolve_target(d), "director")

    def test_owners_only(self):
        """rerun 为空但给了 owners → 仍能定出目标。"""
        text = ('```yaml\npass: false\nrerun: []\n'
                'reason_owners: [dialogue]\nreasons:\n  - x\n```')
        d = decision.parse_decision(text)
        self.assertEqual(decision.resolve_target(d), "dialogue")

    def test_owners_normalized_and_reviewer_filtered(self):
        """owners 同样做归一化；reviewer 必须被过滤（回退到自己 = 死循环）。"""
        text = ('```yaml\npass: false\nrerun: [scenedesigner]\n'
                'reason_owners: [评审, worldbuilder]\nreasons:\n  - x\n```')
        d = decision.parse_decision(text)
        self.assertEqual(d["owners"], ["worldbuilder"])
        self.assertEqual(decision.resolve_target(d), "worldbuilder")

    def test_real_incident_is_corrected(self):
        """事故原文回归（取自 closing-time 的 .rerun_backup/…/reviewer/review.md）。"""
        text = '''```yaml
pass: false
rerun: [plotdesigner]
reason_owners: [director, director]
reasons:
  - "S04/S05 光列与 S02/S07 灯态矛盾：director 表格 S04 写「冷光店内」"
  - "S05/S07 音效「玻璃门吱」与场景卡『玻璃自动门』矛盾"
```
'''
        d = decision.parse_decision(text)
        self.assertFalse(d["pass"])
        self.assertEqual(decision.route_target(d["rerun"]), "plotdesigner")
        self.assertEqual(decision.resolve_target(d), "director")
        self.assertEqual(d["owners"], ["director"])   # 与 rerun 一致地去重


class TestUnfencedDecisionBlock(unittest.TestCase):
    """判定块**漏写代码围栏**时的兜底解析（2026-09-14 实测事故）。

    真实事故：契约要求判定块写在 ```yaml 围栏里，但模型**偶发漏写** —— 22 个真实
    项目的 `reviewer/review.md` 里 **2 个一个反引号都没有**（其余 20 个正常）。
    旧实现只扫围栏 → `parse_decision` 返回 `None` → `reconcile_manifest` 的
    `if dec:` 整段被跳过 → manifest 里**根本没有 `review` 键** →
    `media_gate("render")` 报「评审未通过（无 pass: true）」→
    **创作链 20 分钟全绿、四道输入门全过，却不出片**，而 review.md 里明明写着
    `pass: true`（报告与判定自相矛盾，极难排查）。

    兜底**只增加可解析性**：围栏路径优先；裸块键不成立仍返回 `None`。
    """

    # night-repair（真实产物）末尾原文 —— 只有 `---` 加裸行，零反引号。
    _REAL = """# 质量审核报告：第1集《留言》

## 修改建议汇总
无需打回，无阻断性问题。

---
pass: true
rerun: []
reason_owners: []
reasons: []
advisory:
  - 1-1/1-2/1-3 三镜等距三连独白，建议剪辑留白（剪辑建议，不改分镜）
  - 3-5/3-6「报出号码开头」按禁忌落为台词省略具体号码，仅记录说明
"""

    def test_real_unfenced_product_is_parsed(self):
        """真实产物回归：`---` + 裸 `key: value`（无围栏）。"""
        d = decision.parse_decision(self._REAL)
        self.assertIsNotNone(d, "裸判定块必须能解析，否则评审门会误拦整片")
        self.assertTrue(d["pass"])
        self.assertEqual(d["rerun"], [])
        self.assertEqual(len(d["advisory"]), 2)
        self.assertTrue(d["unfenced"], "漏写围栏要如实标记，供调用方告警")

    def test_bare_block_reasons_are_blocking(self):
        """裸块里的 `reasons` 同样是**阻断**理由，不能被当成「解析不出」。"""
        text = ("报告正文\n\n---\npass: false\nrerun: [scenedesigner]\n"
                "reasons:\n  - 分镜 3-2 与人物设定矛盾\n")
        d = decision.parse_decision(text)
        self.assertFalse(d["pass"])
        self.assertEqual(d["rerun"], ["scenedesigner"])
        self.assertEqual(len(d["reasons"]), 1)
        self.assertFalse(decision.normalize_pass(d))

    def test_naked_one_line_json_legacy_format(self):
        """第二种变异：整个文件就是一行裸 JSON，且用 legacy 字段（birthday 实测）。"""
        text = ('{"scores": {"plot": 9}, "issues": [], "needs_revision": false,'
                ' "revision_target": null}')
        d = decision.parse_decision(text)
        self.assertIsNotNone(d)
        self.assertTrue(d["pass"])
        self.assertTrue(d.get("legacy"))
        self.assertTrue(d["unfenced"])

    def test_fenced_block_is_not_marked_unfenced(self):
        """契约路径优先，且**不得**被打上 `unfenced`（否则告警永远在响）。"""
        d = decision.parse_decision('```yaml\npass: true\nrerun: []\n```')
        self.assertTrue(d["pass"])
        self.assertFalse(d.get("unfenced", False))

    def test_prose_without_block_still_none(self):
        """正文里出现 ASCII 冒号的普通行 → 仍返回 `None`（兜底不许瞎猜）。"""
        self.assertIsNone(decision.parse_decision("报告正文\n\n结论如下：\nplot: 9\nchar: 9\n"))
        self.assertIsNone(decision.parse_decision("只有一份人看的评分报告，没有任何判定块。"))

    def test_reconcile_writes_review_from_unfenced_block(self):
        """端到端：裸判定块 → `reconcile_manifest` 写入 `review.passed` → 渲染门放行。

        这一条锁的就是事故本身：**解析不出 → manifest 没有 `review` 键 → 整片被拦**。
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "reviewer").mkdir(parents=True)
            (root / "reviewer" / "review.md").write_text(self._REAL, encoding="utf-8")
            m = {"phases": {r: "complete" for r in guards.GATE_ROLES}}
            guards.reconcile_manifest(root, m)
            self.assertTrue((m.get("review") or {}).get("passed"),
                            "裸判定块必须被 reconcile 读进 manifest.review")
            ok, why = guards.media_gate("render", m)
            self.assertTrue(ok, "评审已通过却仍被拦下：%s" % why)


class TestDefectGrading(unittest.TestCase):
    """缺陷分级：只有建议（advisory）、没有阻断理由（reasons）→ 视为通过。

    2026-09-10：`reasons` 非空会触发整条下游链重跑（一次 13–28 分钟）。
    若把"称呼不统一"这类建议写进 reasons，等于花 20 分钟换一句文案统一。
    """

    def test_blocking_reasons_still_fail(self):
        """有阻断理由 → 照常打回（尊重模型判定）。"""
        text = ('```yaml\npass: false\nrerun: [director]\n'
                'reasons:\n  - S04 光列与灯态矛盾\n```')
        d = decision.parse_decision(text)
        self.assertFalse(decision.normalize_pass(d))

    def test_advisory_only_becomes_pass(self):
        """只有建议、没有阻断理由 → 降级为通过。"""
        text = ('```yaml\npass: false\nrerun: []\n'
                'advisory:\n  - 主角称呼不统一，建议后续统一\n```')
        d = decision.parse_decision(text)
        self.assertEqual(d["reasons"], [])
        self.assertEqual(len(d["advisory"]), 1)
        self.assertTrue(decision.normalize_pass(d))

    def test_false_without_any_reason_still_fails(self):
        """说不通过却没给任何理由 → 保守起见仍打回（不误放行）。"""
        d = decision.parse_decision('```yaml\npass: false\nrerun: [director]\n```')
        self.assertFalse(decision.normalize_pass(d))

    def test_pass_true_always_passes(self):
        d = decision.parse_decision('```yaml\npass: true\nrerun: []\n```')
        self.assertTrue(decision.normalize_pass(d))

    def test_advisory_aliases(self):
        """advisory 的别名 suggestions / notes 也要能读。"""
        d = decision.parse_decision('```yaml\npass: false\nsuggestions:\n  - x\n```')
        self.assertEqual(d["advisory"], ["x"])

    def test_blocking_and_advisory_coexist(self):
        """阻断与建议同时存在 → 打回，但建议仍留档。"""
        text = ('```yaml\npass: false\nrerun: [director]\n'
                'reasons:\n  - 光列矛盾\n'
                'advisory:\n  - 称呼不统一\n```')
        d = decision.parse_decision(text)
        self.assertFalse(decision.normalize_pass(d))
        self.assertEqual(len(d["advisory"]), 1)

    def test_hard_defect_is_not_downgraded(self):
        """顺序契约：确定性缺陷先写入 reasons，normalize_pass 不得把它放行。

        锁住 roles.py 里的调用顺序——`_audio_mode_defect` 必须在
        `normalize_pass` **之前**执行，否则"仅有 advisory"的判定会误放行
        一个真正违规的分镜。
        """
        d = decision.parse_decision(
            '```yaml\npass: false\nrerun: []\nadvisory:\n  - x\n```')
        # 模拟 hard 检查注入（roles.py 的顺序）
        d["reasons"] = ["音频模式违规：dialogue-led 但分镜仅 0/8 镜有台词"]
        self.assertFalse(decision.normalize_pass(d))


class TestPhaseAccounting(unittest.TestCase):
    def test_record_phase_persists(self):
        """记账必须落盘——旧版记账只在中间件里，没被调用就丢。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / "director" / "director.md"
            p.parent.mkdir(parents=True)
            p.write_text("大纲" * 50, encoding="utf-8")
            m = {}
            guards.record_phase(root, m, "director")
            on_disk = guards.load_manifest(root)
            # ★ M2：`phases` 已是**二维**（`{ep: {role: state}}`）——
            #   断言一律走 `phase_of`（**读**的唯一入口），不再直读结构。
            self.assertEqual(guards.phase_of(on_disk, "director", 1), "complete")
            self.assertEqual(on_disk["phases"]["1"]["director"], "complete")

    def test_record_phase_failure_counts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            m = {}
            guards.record_phase(root, m, "director")
            self.assertEqual(guards.phase_of(m, "director", 1), "failed")
            self.assertEqual(m["revision_counts"]["director"], 1)

    def test_reset_from_clears_downstream(self):
        m = {"phases": {r: "complete" for r in guards.ROLES},
             "revision_counts": {}}
        reset = guards.reset_from("scriptwriter", m)
        self.assertEqual(reset, ["scriptwriter", "dialogue", "scenedesigner", "reviewer"])
        for r in reset:
            self.assertNotIn(r, m["phases"])
        # 上游不受影响
        self.assertEqual(m["phases"]["director"], "complete")
        # revision_counts 不清（防死循环计数跨回退累计）
        self.assertEqual(m["revision_counts"], {})

    def test_reset_from_stashes_old_artifacts(self):
        """旧产物必须移走——否则物化守卫看到文件还在就判 complete，重跑空转。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for role in ("scriptwriter", "dialogue"):
                p = root / guards.out_path(role)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("旧内容" * 20, encoding="utf-8")
            m = {"phases": {r: "complete" for r in guards.ROLES},
                 "revision_counts": {}}
            guards.reset_from("scriptwriter", m, root)
            self.assertFalse((root / guards.out_path("scriptwriter")).exists())
            self.assertFalse((root / guards.out_path("dialogue")).exists())
            backup = list((root / ".rerun_backup").rglob("*.md"))
            self.assertEqual(len(backup), 2)

    def test_revision_exhausted(self):
        """计数 = 已回退次数；达到上限仍给最后一次机会，**超过**才跳过。"""
        m = {"revision_counts": {"scriptwriter": 3}}
        self.assertFalse(guards.revision_exhausted("scriptwriter", m))
        m = {"revision_counts": {"scriptwriter": 4}}
        self.assertTrue(guards.revision_exhausted("scriptwriter", m))
        self.assertFalse(guards.revision_exhausted("dialogue", m))


class TestMediaGateCompleteness(unittest.TestCase):
    def _full(self, **kw):
        m = {"phases": {r: "complete" for r in guards.ROLES},
             "review": {"passed": True}, "media_loop": {}, "revision_counts": {}}
        m.update(kw)
        return m

    def test_all_eight_roles_required(self):
        """只跑 2/8 个角色就渲染 = 编排完整性事故，必须拦。"""
        m = self._full()
        m["phases"] = {"director": "complete", "worldbuilder": "complete",
                       "scenedesigner": "complete"}
        ok, why = guards.media_gate("render", m)
        self.assertFalse(ok)
        self.assertIn("创作链未完成", why)

    def test_review_must_pass(self):
        m = self._full(review={"passed": False})
        ok, why = guards.media_gate("render", m)
        self.assertFalse(ok)
        self.assertIn("评审未通过", why)

    def test_force_passed_allowed(self):
        """回退超限强制放行也要能渲染，否则整轮卡死。"""
        m = self._full(review={"passed": False, "force_passed": True})
        self.assertTrue(guards.media_gate("render", m)[0])

    def test_full_pass(self):
        self.assertTrue(guards.media_gate("render", self._full())[0])


class TestVideoJobsStateMachine(unittest.TestCase):
    def test_legal_flow(self):
        j = {}
        jobs.submitted(j, "LN01", "vid-1", seconds=8)
        self.assertEqual(j["LN01"]["state"], "submitted")
        jobs.mark(j, "LN01", "completed", local="x.mp4")
        self.assertEqual(j["LN01"]["state"], "completed")
        self.assertEqual(j["LN01"]["attempts"], 1)

    def test_illegal_transition_recorded_not_raised(self):
        """非法跃迁不抛异常（生产不能被日志格式拖垮），但要留痕。"""
        j = {"LN01": {"state": "pending", "attempts": 0}}
        jobs.mark(j, "LN01", "completed")
        self.assertEqual(j["LN01"]["state"], "completed")
        self.assertTrue(j["LN01"]["state_warnings"])

    def test_migrate_legacy(self):
        """旧格式（只有 video_id）迁移成 submitted，不会被当完成跳过。"""
        legacy = {"LN01": {"video_id": "v1", "first_frame_kind": "own_still"},
                  "LN02": {"first_frame": "data:..."}}
        out = jobs.migrate(legacy)
        self.assertEqual(out["LN01"]["state"], "submitted")
        self.assertEqual(out["LN02"]["state"], "pending")

    def test_done_requires_file(self):
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d)
            j = {"LN01": {"state": "completed", "attempts": 1}}
            self.assertFalse(jobs.done(j, "LN01", clip))     # 记录说完成但文件没了
            (clip / "LN01.mp4").write_bytes(b"x")
            self.assertTrue(jobs.done(j, "LN01", clip))

    def test_pending_names_includes_failed_and_expired(self):
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d)
            (clip / "LN04.mp4").write_bytes(b"x")
            j = {"LN01": {"state": "failed"}, "LN02": {"state": "expired"},
                 "LN03": {"state": "completed"},            # 记录完成但文件丢了
                 "LN04": {"state": "completed"}}            # 完成且文件在
            self.assertEqual(sorted(jobs.pending_names(j, clip)),
                             ["LN01", "LN02", "LN03"])

    def test_roundtrip_disk(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            j = {}
            jobs.submitted(j, "LN01", "v1")
            jobs.save(out, j)
            again = jobs.load(out)
            self.assertEqual(again["LN01"]["state"], "submitted")
            self.assertEqual(again["LN01"]["video_id"], "v1")


class TestAudioModeGate(unittest.TestCase):
    """reviewer 节点的确定性音频模式门：brief 说要台词，分镜不能全片无声。"""

    HEAD = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |\n"
            "|---|---|---|---|---|---|---|---|\n")

    def _root(self, d: str, audio_mode: str, n_line: int, n_total: int) -> Path:
        root = Path(d)
        (root / "scenedesigner").mkdir(parents=True, exist_ok=True)
        (root / "brief.json").write_text(
            json.dumps({"audio_mode": audio_mode}, ensure_ascii=False), encoding="utf-8")
        rows = []
        for i in range(1, n_total + 1):
            dlg = "「台词%d」" % i if i <= n_line else "（无声，环境音）"
            rows.append("| %d | 中景 | 平视 | 固定 | %d | 陈默在低模出租屋里做出第%d个动作 | %s | 风声 |"
                        % (i, 6 + i, i, dlg))
        (root / "scenedesigner" / "scenedesigner.md").write_text(
            self.HEAD + "\n".join(rows), encoding="utf-8")
        return root

    def test_dialogue_led_zero_lines_is_defect(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, "dialogue-led", 0, 4)
            msg = roles._audio_mode_defect(root)
            self.assertTrue(msg)
            self.assertIn("dialogue-led", msg)

    def test_dialogue_led_enough_lines_passes(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, "dialogue-led", 3, 4)
            self.assertEqual(roles._audio_mode_defect(root), "")

    def test_dialogue_led_third_ratio_passes(self):
        """实测 bootleg99-full 34% 属合法节奏，不该被拦。"""
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, "dialogue-led", 3, 10)
            self.assertEqual(roles._audio_mode_defect(root), "")

    def test_silent_mode_with_lines_is_defect(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, "silent", 4, 4)
            self.assertIn("silent", roles._audio_mode_defect(root))

    def test_missing_brief_defaults_to_dialogue_led(self):
        """没有 brief.json 时按 dialogue-led——零台词照样拦。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "scenedesigner").mkdir(parents=True)
            (root / "scenedesigner" / "scenedesigner.md").write_text(
                self.HEAD + "| 1 | 中景 | 平视 | 固定 | 8 | 陈默在低模出租屋里做出一个动作 | （无声） | 风声 |\n",
                encoding="utf-8")
            self.assertTrue(roles._audio_mode_defect(root))

    def test_no_storyboard_is_not_a_defect(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(roles._audio_mode_defect(Path(d)), "")


class TestRoleInputAudioMode(unittest.TestCase):
    """开工前把 audio_mode 写成硬指令（而不是等评审才发现）。"""

    def test_dialogue_led_instruction_injected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "brief.json").write_text(
                json.dumps({"audio_mode": "dialogue-led"}, ensure_ascii=False), encoding="utf-8")
            txt = roles.role_input("scenedesigner", root, {})
            self.assertIn("dialogue-led", txt)
            self.assertIn("必须有台词", txt)

    def test_silent_instruction_injected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "brief.json").write_text(
                json.dumps({"audio_mode": "silent"}, ensure_ascii=False), encoding="utf-8")
            txt = roles.role_input("dialogue", root, {})
            self.assertIn("silent", txt)
            self.assertIn("不出台词", txt)

    def test_other_roles_unaffected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "brief.json").write_text(
                json.dumps({"audio_mode": "dialogue-led"}, ensure_ascii=False), encoding="utf-8")
            txt = roles.role_input("director", root, {})
            self.assertNotIn("音频模式", txt)


if __name__ == "__main__":
    unittest.main()
