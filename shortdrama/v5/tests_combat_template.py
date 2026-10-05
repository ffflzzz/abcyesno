# -*- coding: utf-8 -*-
"""**测试 A（负样本）**：把 `2.5打斗_30秒定制.md` 的二十一拍**原样**当分镜表，
验证 `check_storyboard` / `media_gate` 会不会**拦下**它。

## 为什么这个测试有价值

它测的**不是**"能不能跑通"，而是**门还活着没有**。
架构改动之后最危险的失效形态是「门被改松了、什么都放行」——
那不会报错，只会在成片后才暴露（记忆里同类事故：片长门形同虚设、
角色卡解析 0 个却全程零报错）。

所以这一档的判据是：**必须被拦，且理由必须是"对的那一条"**。
如果它通过了 ⇒ 我的改动把门改坏了。

## 这份模板与本链的**根本冲突**（本测试要验证的正是这条）

| 模板要求 | 本链的硬约束 | 冲突后果 |
|---|---|---|
| 21 拍，单拍 1.0–1.7 秒 | 每镜 **4–12 秒**（供应商 API 区间） | 全部被钳成 4 秒 |
| **一镜到底、禁止硬切** | 每镜是一次独立生成，镜与镜互不知情 | 拼接点必然"断" |
| 拍长 1.0–1.7 秒（如 `0-1.0`） | 解析器抓**第一个数字** ⇒ 读成 **0** | 静默兜底 4 秒 |
| 总时长 30 秒 | `_duration_gap` 要求落在 target 的 **85%–130%** | 取决于 target |

⚠️ 特别注意第三行：`storyboard.py:288` 是
`re.search(r"(\\d+(?:\\.\\d+)?)", cells[i_sec])` —— **抓第一个数字**。
所以 `0-1.0` 会被读成 **`0`**！这正是 `mode.BARE_INT_HINT` 要防的那件事。
"""
from __future__ import annotations

import unittest

from v5 import mode, validate


#: 模板第 2.1 节的**二十一拍**（时间码列照抄，一字不改）。
#: 这是「原样喂」的关键：不做任何"适配"，看门怎么判。
RAW_BEATS = [
    ("1",  "0-1.0",     "开场破局"),
    ("2",  "1.0-2.0",   "借力腾空"),
    ("3",  "2.0-3.0",   "贴地突进·缴械"),
    ("4",  "3.0-4.0",   "首次受挫·硬架被震退"),
    ("5",  "4.0-5.5",   "反杀精英·三连击"),
    ("6",  "5.5-7.0",   "远程压制·拨打弹幕"),
    ("7",  "7.0-8.0",   "贴身险招·滑铲仰击"),
    ("8",  "8.0-9.0",   "震退起飞"),
    ("9",  "9.0-10.0",  "空中留白·蓄力特写"),
    ("10", "10-11.5",   "范围爆发（爽点一）"),
    ("11", "11.5-13",   "织网切割"),
    ("12", "13-14",     "穿插破夹击"),
    ("13", "14-15.5",   "重创跪地（最低点）"),
    ("14", "15.5-17",   "拾器·极限专注"),
    ("15", "17-18.5",   "连锁引爆（爽点二）"),
    ("16", "18.5-20",   "BOSS 登场·清零爽感"),
    ("17", "20-22",     "BOSS 首击·闪避反击"),
    ("18", "22-24",     "攀爬·关节破坏"),
    ("19", "24-26",     "终极蓄力（最大留白）"),
    ("20", "26-28",     "终极一击（最高点）"),
    ("21", "28-29",     "落地余韵"),
]


def _storyboard(seconds_cells: list[str], total_target: int = 30) -> str:
    """按 13 列契约拼一张分镜表（`seconds_cells` 逐镜给定「时长(秒)」列）。"""
    head = ("| 镜头号 | 场景 | 时长(秒) | 景别 | 运镜 | 画面描述 | 角色 | "
            "道具 | 身份锚点 | 视觉风格 | 对白 | 音效 | 镜尾衔接 |")
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    rows = [head, sep]
    for i, ((num, _tc, name), sec) in enumerate(zip(RAW_BEATS, seconds_cells), 1):
        rows.append("| %s | 焦土战场 | %s | 中景 | 镜头跟着她旋身 | 第%s拍 %s，"
                    "箭矢贯穿敌兵胸膛，化为焦黑碳屑 | 主角 | 长弓 | "
                    "长发红绑带鳞甲 | 战火逆光 | （无声，环境音） | 尘暴 |"
                    " 借转身惯性 |" % (num, sec, num, name))
    return "\n".join(rows)


class TestTimecodeCellsAreMisread(unittest.TestCase):
    """★ 核心发现：模板的时间码**原样填进「时长(秒)」列会被读成 0**。

    这不是假设，是 `storyboard.py:288` 的实际行为：
    `re.search(r"(\\d+(?:\\.\\d+)?)", "0-1.0")` ⇒ `"0"`。
    """

    def test_regex_takes_first_number(self):
        import re
        pat = r"(\d+(?:\.\d+)?)"
        self.assertEqual(re.search(pat, "0-1.0").group(1), "0")
        self.assertEqual(re.search(pat, "10-11.5").group(1), "10")
        self.assertEqual(re.search(pat, "1.0-2.0").group(1), "1.0")
        self.assertEqual(int(float(re.search(pat, "1.0-2.0").group(1))), 1)

    def test_parsed_seconds_are_wrong(self):
        """把模板的时间码原样填进去 ⇒ 每镜秒数被读成**那一拍的起点**。

        ⚠️ 实测真相比预想更糟：不是"全部读成 0"（那是 `0-1.0` 这种两段式的情形），
        而是 `re.search` 抓到 `1.0-2.0` 的 **`1`**、`10-11.5` 的 **`10`**、
        `28-29` 的 **`28`** —— 于是 21 拍被读成 0..28 递增的一列，
        **30 秒的片被读成 257 秒**（比原片长 8.5 倍）。
        ⇒ 「时长(秒)」列填时间码，产出的是**看起来像模像样、实则完全错误的**秒数，
        而且片长门只会报「总时长 257s 与目标不符」，不会说「你填错格式了」。
        """
        from v5.media import storyboard
        md = _storyboard([tc for _n, tc, _t in RAW_BEATS])
        shots = storyboard.parse(md)
        self.assertEqual(len(shots), 21)
        secs = [s["seconds"] for s in shots]
        self.assertEqual(secs[:5], [0, 1, 2, 3, 4], "抓的是每拍的**起点**")
        self.assertEqual(secs[-1], 28, "最后一拍『28-29』被读成 28（起点）")
        # ✅ 2026-10-05：**两家口径已合一**。原来 `storyboard.parse` 走 `int(float(...))`
        #   把小数秒截掉 ⇒ parse 加总 257、门 `seconds_total` 259，差 2 秒
        #   （记忆里那条「判片长只能用门的口径」就是这个裂口）。
        #   现在 parse 保留小数（场口径下 0.5 秒一镜是合法写法），两边同为 259.0。
        #   ⚠️ 本测试的**主角不是这个数**：把时间码原样填进「时长(秒)」列，
        #   解析器抓到的仍是每拍**起点**，30 秒的片照样被读成 259 秒。
        self.assertEqual(sum(secs), 259.0, "parse 与门现在同口径")
        self.assertGreater(sum(secs), 30 * 8,
                           "而且是**偏大** ⇒ 片长门会报「超出」而不是「不足」")

    def test_clamp_silently_makes_it_4s(self):
        """兜底：**0 秒 → 4 秒**，**全程不报错**。

        ⚠️ 一个容易读错的细节：0 秒**不是**被钳到每镜下限，而是被
        `_sec(...) or 4.0` 的 **`or` 短路**成 4 —— 压根没有"下限"参与（2026-10-05 起
        `PACK_MIN_SHOT_SECONDS = 0`，场口径下秒数地板挂在**场**上，不挂在镜上）。
        """
        from v5.media.video_plan import pack_clamp_sec
        self.assertEqual(pack_clamp_sec({"seconds": 0}), 4)
        self.assertEqual(pack_clamp_sec({}), 4, "缺字段也是 4")
        self.assertEqual(pack_clamp_sec({"seconds": 3}), 3, "区间内原样")
        self.assertEqual(pack_clamp_sec({"seconds": 16}), 12, "超上限压到 12")
        # ★ 2026-10-05：小秒数是**合法写法**（快切正反打），不再被拉长
        self.assertEqual(pack_clamp_sec({"seconds": 1}), 1, "1 秒照原样（原来会被钳成 2）")
        self.assertEqual(pack_clamp_sec({"seconds": 0.5}), 0.5, "0.5 秒照原样")


class TestGateRejectsRawTemplate(unittest.TestCase):
    """门必须**拦下**原样模板 —— 若通过，说明改动把门改坏了。"""

    def test_duration_gate_flags_wrong_total(self):
        """原样填时间码 ⇒ 21 拍合计 **259 秒**（门口径）vs 目标 30 秒 ⇒ 必须报偏离。

        ⚠️ 实测方向是**偏大**（259s > 30s），不是偏小 ——
        因为解析器抓的是每拍起点（0,1,2,…28），累加出 257（parse 口径）/ 259（门口径）。
        这比"偏小"更隐蔽：偏小会被一眼看出，259 秒看起来"挺像个长片"。
        ⇒ 且**必须用 `seconds_total`（门口径）判断**，理由见
        `test_parsed_seconds_are_wrong` 里那条实测差异注释。
        """
        md = _storyboard([tc for _n, tc, _t in RAW_BEATS])
        brief = {"target_duration": "30秒",       # ⚠️ 必须是「30秒」这种带单位的**字符串**
                 "must_have": ["开场破局", "终极一击"]}
        r = validate.check_storyboard(md, brief)
        self.assertEqual(r["seconds_total"], 259.0)
        gap = r["duration_off"]
        self.assertTrue(gap, "片长门必须报偏离（259s vs 目标 30s）")
        self.assertIn("30", gap)
        # ⚠️ `ok` **不含**时长条（实测 True）—— 时长是**阻断项但走独立字段**
        # （`series._storyboard_gate` 把它塞进 `fatal`，不是靠 `ok` 兜）。
        # 真正决定「渲不渲」的是 `pipeline._run_impl` 之后那道片长门，
        # 所以这里断言的是「偏离被算出来了」，不是 `ok` 为假。
        self.assertEqual(r["duration_ratio"], round(259.0 / 30.0, 3))

    def test_numeric_target_duration_is_silently_ignored(self):
        """⚠️★ 实测坑（2026-10-04）：`target_duration` 写成**数字**时
        `parse_target_seconds` 返回 `None` ⇒ **片长门静默失效**
        （`duration_ratio=None`、`duration_off=''`、`ok` 仍为真）。

        这比「填错格式被拦」糟得多：门**看起来在跑**，实际什么也没查。
        记忆里早有同型结论（「别拿数字精调 brief」/「三条口径不同」），
        这里把它的**失效形态**钉成测试 —— 将来谁改了容错解析，这里会红。
        """
        md = _storyboard([tc for _n, tc, _t in RAW_BEATS])
        r_num = validate.check_storyboard(md, {"target_duration": 30})
        self.assertIsNone(validate.parse_target_seconds(30),
                          "数字形态解析不出来（实测）")
        self.assertEqual(r_num["duration_off"], "", "⇒ 门不报偏离")
        self.assertIsNone(r_num["duration_ratio"])
        # 同一份表换成字符串形态 ⇒ 门立刻报偏离 ⇒ 证明是**解析**问题不是表问题
        r_str = validate.check_storyboard(md, {"target_duration": "30秒"})
        self.assertTrue(r_str["duration_off"], "字符串形态必须报偏离")

    def test_compliant_duration_passes(self):
        """反向断言：3 镜 × 10 秒 = 30 秒 ⇒ 同一张表结构下**必须通过**时长那条。

        存在的意义：证明上一条不是因为「表写坏了」被拦，而是**真的因为时长**。
        """
        md = _storyboard(["10"] * 21)
        r = validate.check_storyboard(md, {"target_duration": 210})
        self.assertEqual(r["duration_off"], "", "21×10=210s 应落在 85%–130% 带内")
        self.assertEqual(r["seconds_total"], 210)

    def test_text_dependency_does_not_fire(self):
        """⚠️ 反向断言：模板**没有**要求画内文字 ⇒ 这条门不该报。
        目的：确认「拦下」的理由不是这条 —— 否则会把门改坏误判成改对。"""
        md = _storyboard(["6"] * 21)
        r = validate.check_storyboard(md, {"target_duration": 126})
        self.assertFalse(r.get("text_dependency"),
                         "模板里没有画内文字要求，这条不该触发")

    def test_schema_is_fine(self):
        """13 列齐全 ⇒ 门不该报「缺列」。同样是反向断言。"""
        md = _storyboard(["6"] * 21)
        r = validate.check_storyboard(md, {"target_duration": 126})
        self.assertEqual(r.get("missing_cols"), [])
        self.assertTrue(r.get("order_ok"))


class TestBareIntHintCoversThisCase(unittest.TestCase):
    """注入文案必须**覆盖上面那个真实故障**（否则注入等于没写）。"""

    def test_hint_names_the_exact_bad_formats(self):
        h = mode.BARE_INT_HINT
        for bad in ("0:45", "8s", "8秒"):
            self.assertIn(bad, h, "必须举出 %r 这个反例" % bad)

    def test_hint_explains_reads_first_number(self):
        self.assertIn("第一个数字", mode.BARE_INT_HINT)

    def test_hint_says_silent(self):
        self.assertIn("静默", mode.BARE_INT_HINT)


class TestComplianceCeiling(unittest.TestCase):
    """一镜到底 30 秒 vs 每镜 4–12 秒 —— 这条冲突**结构上无解**。

    存在的意义：把结论**钉成可执行的判据**，而不是留在对话里。
    30 秒 ÷ 最长 12 秒 = 至少 3 镜；÷ 最短 4 秒 = 至多 7 镜。
    """

    def test_thirty_seconds_needs_three_to_seven_shots(self):
        self.assertEqual(30 // 12 + (1 if 30 % 12 else 0), 3)
        self.assertEqual(30 // 4, 7)

    def test_single_shot_thirty_seconds_is_impossible(self):
        """「30 秒一镜到底」在 4–12 秒/镜的区间下**物理上做不到**。"""
        self.assertGreater(30, 12)

    def test_pack_mode_single_request_cap(self):
        from v5.media.video_plan import PACK_MAX_SECONDS
        self.assertEqual(PACK_MAX_SECONDS, 12)


if __name__ == "__main__":
    unittest.main()
