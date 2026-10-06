"""评审 fail 之后"该不该自动打回、打回谁"的判据（`scripts/drive_chain.reroll_plan`）。

为什么必须有这份测试（2026-09-30 实测，代价是一整集白跑 64 分钟）：
`xiaoman-workshop-1030` 第 1 集七个角色产物全齐、reviewer 判 `pass: false` 并写明
`rerun: [scenedesigner]`，但**没有任何一步执行打回** —— 驱动器的收工判据只看"产物
在不在盘上"，于是它 break，外层问门，门拦下 rc=1。设计意图（"supervisor 会在同一个
run 内重派上游"）写在注释里，实测不兑现 ⇒ 靠 LLM 自觉的律等于没有。

判据抽成纯函数、副作用留在循环里，就是为了让这些分支能被**当场**钉住，
而不是等真跑一小时才发现"要不要打回"判错了。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

_spec = importlib.util.spec_from_file_location(
    "drive_chain", str(_ROOT / "scripts" / "drive_chain.py"))
dc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dc)     # 模块级只 import config/guards，不需要 dev server

from v5 import decision  # noqa: E402

# 2026-09-30 真实产物（projects/xiaoman-workshop-1030/reviewer/review_ep1.md 末尾）
REAL_FAIL = """## YAML

```yaml
pass: false
rerun: [scenedesigner]
reason_owners: [scenedesigner]
reasons:
  - 镜 04 / 08 / 10 / 16 / 17 / 19 / 20 共 7 个旁白镜口播超速（34–41 字压在 5–6s 内）
advisory:
  - 场景列缺「内/外·日/夜」
```
"""

PASS_YAML = """```yaml
pass: true
rerun: []
reasons: []
```
"""


class RerollBudgetTests(unittest.TestCase):
    """预算必须读**门那份按集落盘的累计值**（我第一版按进程起算，实测会把同一集
    再打回两轮才轮到门放行 —— 一轮 25 分钟，纯重复付费）。"""

    def test_durable_blocks_shrink_budget(self):
        self.assertEqual(dc.reroll_budget(2, 0), 2)     # 没拦过：给足
        self.assertEqual(dc.reroll_budget(2, 1), 1)     # 拦过一次：只剩一次
        self.assertEqual(dc.reroll_budget(2, 2), 0)     # 到上限：直接交给门
        self.assertEqual(dc.reroll_budget(2, 5), 0)     # 越界不返回负数

    def test_missing_counter_is_tolerated(self):
        for missing in (None, ""):
            self.assertEqual(dc.reroll_budget(2, missing), 2)


class RerollPlanTests(unittest.TestCase):
    def test_fenced_yaml_with_bad_escape_still_parses(self):
        """★ 围栏在、内容 YAML 读不出来时，**行解析要兜住这个块**（2026-10-06 零出片）。

        真实事故（`ice-spring-bridge-duel` 第 3 集）：评审在双引号字符串里引用了正则
        `(\\d+)`，YAML 报 `found unknown escape character 'd'` ⇒ **整块**读不出来 ⇒
        `media_gate` 判「评审未通过」⇒ 创作链 32 分钟、七件产物齐全、两道输入门全过，
        **零出片**。判定本身一直是清楚的（`pass: false` + 两条理由）。
        """
        txt = ("## 总结\n\n```yaml\n"
               "pass: false\n"
               "rerun: [scenedesigner]\n"
               "reason_owners: [scenedesigner, scenedesigner]\n"
               "reasons:\n"
               "  - \"LN01-LN05 全部节拍时间码使用小数秒，解析正则 (\\d+) 会把 '1.5-3秒' 切成假拍\"\n"
               "  - \"LN01-LN05 全部第一拍锚点串缺能量色项\"\n"
               "advisory:\n"
               "  - \"建议改为半静态收势\"\n"
               "```\n")
        import yaml
        body = txt.split("```yaml")[1].split("```")[0]
        with self.assertRaises(Exception):
            yaml.safe_load(body)          # 前提：YAML 确实读不出来（不是我在测一个能过的块）
        dec = decision.parse_decision(txt)
        self.assertIsNotNone(dec, "YAML 读不出来时行解析必须兜住，否则媒体链被拦死")
        self.assertFalse(dec.get("pass"))
        self.assertEqual(dec.get("rerun"), ["scenedesigner"])
        self.assertEqual(len(dec.get("reasons") or []), 2)
        self.assertIn("(\\d+)", dec["reasons"][0], "理由原文要保住，别把转义吃掉")
        self.assertTrue(dec.get("lenient"), "要留下『走了兜底』的标记，供调用方如实告警")

    def test_lenient_fallback_does_not_invent_a_pass(self):
        """★ 兜底**不许把拦得住的判决读成放行**（反向对照）。

        走兜底时 `pass` 与理由必须原样带出：否则这条兜底就成了"解析器替评审放行"，
        比原来读不出来更危险。
        """
        blocked = ("```yaml\npass: false\nrerun: [scenedesigner]\n"
                   "reasons:\n  - \"引用正则 (\\d+) 时 YAML 报错的那一型\"\n```\n")
        d1 = decision.parse_decision(blocked)
        self.assertIsNotNone(d1)
        self.assertFalse(d1.get("pass"), "评审说不过，兜底不许改成过")
        self.assertEqual(d1.get("rerun"), ["scenedesigner"])
        # 块里连 `pass` 都没有 ⇒ 兜底也不该编出一个判定（仍走「读不出」）
        self.assertIsNone(decision.parse_decision(
            "```yaml\nnotes:\n  - \"没有判定键 (\\d+)\"\n```\n"))

    def test_normal_fenced_yaml_is_not_marked_lenient(self):
        """反向对照：正常能解析的块**不许**被兜底路径改写行为。"""
        dec = decision.parse_decision(REAL_FAIL)
        self.assertIsNotNone(dec)
        self.assertIsNone(dec.get("lenient"))

    def test_real_verdict_rerolls_scenedesigner(self):
        """真实那次判决喂进去：必须得出"打回 scenedesigner"，并把原因带上。"""
        dec = decision.parse_decision(REAL_FAIL)
        self.assertIsNotNone(dec, "解析器读不出这段判决 —— 后面的判据全是空转")
        self.assertEqual(dec.get("rerun"), ["scenedesigner"])
        act, tgt, note = dc.reroll_plan(dec, "", 2)
        self.assertEqual(act, "reroll")
        self.assertEqual(tgt, "scenedesigner")
        self.assertIn("口播超速", note)

    def test_pass_does_nothing(self):
        act, tgt, note = dc.reroll_plan(decision.parse_decision(PASS_YAML), "", 2)
        self.assertEqual((act, tgt, note), ("", "", ""))

    def test_until_path_never_rerolls(self):
        """`--until` 是前端两段式（跑到某角色为止），那条路径上没有评审。"""
        dec = decision.parse_decision(REAL_FAIL)
        self.assertEqual(dc.reroll_plan(dec, "scriptwriter", 2)[0], "")

    def test_budget_spent_hands_to_gate(self):
        """重试用完不再打回，但要报 `exhausted` 让外层**响亮**交给门。"""
        dec = decision.parse_decision(REAL_FAIL)
        act, tgt, _ = dc.reroll_plan(dec, "", 0)
        self.assertEqual((act, tgt), ("exhausted", "scenedesigner"))

    def test_unnamed_target_must_not_touch_disk(self):
        """目标不在 7 角色里 ⇒ `no-target`：⛔ 绝不能挪走产物却派不出重做。"""
        dec = {"pass": False, "rerun": ["某某没听过的角色"], "reasons": ["x"]}
        act, tgt, _ = dc.reroll_plan(dec, "", 2)
        self.assertEqual((act, tgt), ("no-target", ""))

    def test_upstream_owner_wins(self):
        """问题在上游、`rerun` 却填了下游时，取**更上游**的那个（下游无权改上游文件）。

        喂原始 YAML 文本而不是 dict：`parse_decision` 会把 `reason_owners` 归一成
        `owners`，而 `resolve_target` 只认 `owners` —— 直接塞 `reason_owners` 的假
        dict 会让这条交叉校验**静默失效**（判据看着测了，其实没测）。
        """
        txt = """```yaml
pass: false
rerun: [scenedesigner]
reason_owners: [plotdesigner]
reasons:
  - 大纲就缺一环，分镜无法自洽
```
"""
        dec = decision.parse_decision(txt)
        self.assertEqual(dec.get("owners"), ["plotdesigner"], "归一化没生效，本用例等于没测")
        act, tgt, _ = dc.reroll_plan(dec, "", 2)
        self.assertEqual((act, tgt), ("reroll", "plotdesigner"))

    def test_missing_verdict_is_not_a_failure(self):
        """读不到判决 ⇒ 不动盘（把"没解析出来"当成"不合格"会误删产物）。"""
        for bad in (None, {}, "", 3, ["pass: false"]):
            self.assertEqual(dc.reroll_plan(bad, "", 2)[0], "", "输入 %r 不该触发打回" % (bad,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
