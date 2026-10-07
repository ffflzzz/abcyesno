# -*- coding: utf-8 -*-
"""角色输入组装 + **7 个角色一律派发**（方案 B；方案 D 已于 2026-09-19 撤销）的自测。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v5 import config, guards, orchestrator  # noqa: E402
from v5.roles import director_system_prompt, role_input  # noqa: E402


class TestInlineUpstream(unittest.TestCase):
    """方案 B：上游产物**全文注入** —— 省掉 `read_file` 的多轮往返。

    背景（2026-09-13）：角色 = `create_agent` + FS 工具，实测 8–10 轮 LLM 调用/角色。
    而"读几个已知文件 → 写一份已知格式文档"1 次调用就够；旧实现只给路径，
    **省的是几 KB token，付出的是 10 倍轮次**。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "brief.json").write_text('{"topic": "测试片"}', encoding="utf-8")
        (self.root / "assetdesigner").mkdir()
        (self.root / "assetdesigner" / "assets.md").write_text(
            "# 关键资产清单\n## 资产卡：钥匙\n- 名称：钥匙\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_inline_mode_embeds_content_not_just_paths(self):
        with mock.patch.object(config, "INLINE_UPSTREAM", True):
            s = role_input("scenedesigner", self.root, {"episode_index": 1})
        self.assertIn("上游产物", s)
        self.assertIn("测试片", s, "brief 的**内容**应进输入")
        self.assertIn("资产卡：钥匙", s, "上游 assetdesigner 的**内容**应进输入")
        self.assertNotIn("不要凭空创作", s, "inline 模式的首行不该再要求 read_file")

    def test_legacy_mode_only_gives_paths(self):
        """`SHORTDRAMA_INLINE_UPSTREAM=0` 必须能完整回退旧行为。"""
        with mock.patch.object(config, "INLINE_UPSTREAM", False):
            s = role_input("scenedesigner", self.root, {"episode_index": 1})
        self.assertNotIn("上游产物全文", s)
        self.assertIn("用 read_file 自己读取", s)
        self.assertNotIn("资产卡：钥匙", s, "legacy 模式不带正文")

    def test_missing_upstream_is_labelled_not_silent(self):
        """读不到的产物要**如实标注** —— 静默留空会让角色误以为上游为空。"""
        with mock.patch.object(config, "INLINE_UPSTREAM", True):
            s = role_input("dialogue", self.root, {"episode_index": 1})
        self.assertIn("读不到", s)


class TestWorldbuilderIsDispatched(unittest.TestCase):
    """★★ 2026-09-19：**方案 D 撤销** —— worldbuilder 回到子代理清单，director 不再兼任。

    为什么撤销（真事故，laofuzi-hk-retro 双集生产）：产物由 director 写 ⇒ 执行的是
    `packs/<pack>/director/SKILL.md`，而 `packs/<pack>/worldbuilder/SKILL.md`
    **永远到不了执行者**；同一 prompt 里还并存两条相反指令
    （包 SKILL 写「不要替下游角色写产物」、`director_system_prompt` 写「你先自己写完」）
    ⇒ 模型交了 director 那份的 `## 角色总览` 表格 ⇒ `cast.parse_characters` 的正则
    `^#+\\s*角色卡\\s*[:：]` **解析出 0 个角色** ⇒ 参考图不生成 ⇒ 到成片才暴露。

    这组测试锁住"**产物执行者 = 持有该角色契约的那张图**"这条不变量。
    """

    def test_worldbuilder_is_dispatched(self):
        names = [s["name"] for s in orchestrator.make_sync_subagents()]
        self.assertIn("worldbuilder", names,
                      "worldbuilder 必须被派发 —— 只有它手里有该角色的产物格式契约")
        # 只数**角色**（用 PREREQ 取交集）：`media_rerender` 不是角色 —— 它不在
        # PREREQ/GATE_ROLES 里，是"出片之后"的修订入口（2026-09-13 加入）。
        roles = [n for n in names if n in orchestrator.PREREQ]
        self.assertEqual(sorted(roles), sorted(orchestrator.PREREQ),
                         "7 个角色一个都不能少（含 worldbuilder）")
        self.assertIn("media_rerender", names, "媒体链的修订入口必须在派发清单里")
        self.assertNotIn("media_rerender", orchestrator.PREREQ,
                         "它不是角色：不该进依赖序（否则会被当成创作链的一环）")

    def test_async_list_also_has_worldbuilder(self):
        names = [s["name"] for s in orchestrator.make_async_subagents()]
        self.assertIn("worldbuilder", names)
        roles = [n for n in names if n in orchestrator.PREREQ]
        self.assertEqual(sorted(roles), sorted(orchestrator.PREREQ))

    def test_no_director_owned_constant(self):
        """`_DIRECTOR_OWNED` 必须**不存在** —— 留着它就会有人再接回去。"""
        self.assertFalse(hasattr(orchestrator, "_DIRECTOR_OWNED"),
                         "方案 D 的开关已删除；如要重新引入请先读 make_async_subagents 上方的事故记录")

    def test_worldbuilder_own_skill_reaches_the_executor(self):
        """δ 的收益断言：**per-role 覆盖真的生效**。

        撤销方案 D 之前，worldbuilder 的产物由 director 写 ⇒ 本包
        `worldbuilder/SKILL.md` 一个字都到不了执行者。现在它必须是
        `role_system_prompt(pack, "worldbuilder")` 的载体。
        """
        from v5.roles import role_system_prompt
        for pack in ("laofuzi-hk-retro", "chinese-style-short-drama",
                     "wool-felt-story-short", "niulai-movie-style", "shortdrama"):
            sp = role_system_prompt(pack, "worldbuilder", 1)
            self.assertIn("worldbuilder/worldbuilder.md", sp,
                          "%s 包的 worldbuilder 契约必须写明产物路径" % pack)
            self.assertNotIn("你是 director", sp, "别角色的 SKILL 不许串进来")

    def test_prereq_and_gate_unchanged(self):
        """产物路径与口径**保持不变** —— 媒体链 `cast` 才能零改动。"""
        from v5.guards import GATE_ROLES, PREREQ, out_path

        self.assertIn("worldbuilder", PREREQ, "assetdesigner 仍要读它")
        self.assertIn("worldbuilder", GATE_ROLES, "媒体门仍要校验它")
        self.assertEqual(out_path("worldbuilder", 1), "worldbuilder/worldbuilder.md")

    def test_director_prompt_does_not_teach_writing_worldbuilder(self):
        """★★ 反向断言：director 的 system prompt **不许**再出现"你亲自写"的授权。

        事故形态是"提示词里两条相反指令，模型任选一条"。所以这里锁的是**负样本**：
        `director_system_prompt` 只含 director 自己的契约，不含 worldbuilder 的
        SKILL 全文、也不含"兼任"。
        """
        dp = director_system_prompt("laofuzi-hk-retro", 1)
        self.assertNotIn("兼任", dp)
        self.assertNotIn("由你亲自", dp)
        self.assertNotIn("你同时兼任 worldbuilder", dp)
        # 正样本：director 自己的契约仍在（规格文档路径）
        self.assertIn("director/director.md", dp)

    def test_discipline_puts_spec_before_dispatch(self):
        """★ 2026-09-19：编排纪律里 must 有「**第 0 步：先落制作规格**」。

        为什么这条重要（会打到**所有**路径，含外部 agent）：`guards.PREREQ` 里
        `worldbuilder ← ["director"]` ⇒ worldbuilder 的**开工契约**把
        `/director/director.md` 列为上游；规格没落盘，它会收到"上游产物缺失"
        的提示，只能靠 brief 猜（人物/场景名对不上时，问题要到成片才暴露）。
        此前 worldbuilder 不被派发（方案 D），这条依赖**从未生效**过。
        """
        s = orchestrator.ORCHESTRATOR_DISCIPLINE
        self.assertIn("第 0 步", s)
        self.assertIn("director/director.md", s)
        self.assertLess(s.index("第 0 步"), s.index("第 1 步"), "第 0 步必须排在派发之前")

    def test_every_role_finds_its_own_graph(self):
        """异步派发按 `graph_id` 找图 ⇒ **7 张 role_* 图必须都在 `langgraph.json` 里**。

        方案 D 期间 `role_worldbuilder` 虽已注册但**不在派发清单**；撤销方案 D 后
        它重新可派发 ⇒ 这条注册必须仍然成立（否则异步路径会报"图不存在"）。
        """
        import json
        cfg = (Path(__file__).resolve().parents[1] / "v5" / "langgraph.json")
        graphs = json.loads(cfg.read_text(encoding="utf-8")).get("graphs") or {}
        for r in orchestrator.PREREQ:
            self.assertIn("role_" + r, graphs, "%s 的图没注册" % r)
        self.assertIn("supervisor", graphs)

    def test_goal_prompt_dispatches_worldbuilder(self):
        """目标提示（drive_chain）里"亲自写 worldbuilder"的授权必须已删除。

        ⚠️ 只查**代码行**、不查注释行：本项目刻意把事故史留在注释里（那条注释
        会引用旧文案原文），把注释也算进去就会变成"越记录越失败"的假断言。
        """
        src = (Path(__file__).resolve().parents[1] / "scripts" / "drive_chain.py")
        code = "\n".join(ln for ln in src.read_text(encoding="utf-8").splitlines()
                         if not ln.lstrip().startswith("#"))
        self.assertNotIn("由你亲自", code,
                         "旧文案会把 worldbuilder 的 per-role 契约整个换掉")
        self.assertIn("派发 worldbuilder", code)


class TestDurationDirectiveInjected(unittest.TestCase):
    """片长/镜头数硬指令必须**在开工前注入**（2026-09-14 实测事故）。

    这条规则原先**只写在 shortdrama 包的 scenedesigner SKILL 里**
    （`packs/shortdrama/scenedesigner/SKILL.md`：「镜头数 ≈ target_duration ÷ 7」），
    而 `_role_skill` 对**自带 scenedesigner SKILL 的包**（niulai-movie-style /
    3d-animation）**不回退**到 shortdrama → **契约根本没到达分镜师**。

    实测后果：`village-scale`（牛来包、brief 目标 180 秒）交出 **21 镜 / 117 秒 = 65%**，
    被分镜契约门拦下整条媒体链（创作链 22 分钟白跑）；而 shortdrama 项目因 SKILL 里
    有这条，历次都落在 96–103%（night-repair 112/120、lost-and-found 173/180）。
    → **系统级规则不能靠 per-pack 的 SKILL 文本承载**，必须在 `role_input` 里注入。

    ★ 2026-09-17：除数 7 → **4**（用户定档「4 秒一镜、快切优先」）。
    实测 mop-and-seat 三集（52 镜 / 378 秒）＝ **7.27 秒/镜**（商业短剧 1.5–4 秒），
    且台词镜内只有 **2.47 字/秒**（正常口播 4–5）—— **镜头越长，同一句话被摊得越慢**
    ⇒ 缩短镜头本身就修掉了「说话慢」。故镜数由「硬指标」改为「**基线**」，
    硬门只剩总时长 85%–130%；镜长改为**内容驱动**（默认 4 秒，台词长的镜自动排长）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _brief(self, target):
        (self.root / "brief.json").write_text(
            json.dumps({"topic": "测试片", "target_duration": target},
                       ensure_ascii=False), encoding="utf-8")

    def test_target_injects_bounds_not_a_division_baseline(self):
        """180 秒 → 注入的是**边界**（每镜 4-12 秒 + 总时长 85% 下限），⛔ 不再注入除法基线。

        ★ 2026-10-03：用户废弃「镜数 = 目标秒数 ÷ 4」（它把两分钟自动变成 30 镜 × 4 秒，
        实测一段段硬分、割裂感重），镜数与镜长归分镜师。本测试因此**反向**断言旧措辞
        不许回来 —— 否则哪天有人顺手把基线加回去，这里会静默通过。
        """
        self._brief("约 180 秒（3 分钟）")
        with mock.patch.object(config, "INLINE_UPSTREAM", True):
            s = role_input("scenedesigner", self.root, {"episode_index": 1})
        self.assertIn("片长", s)
        self.assertIn("180", s, "目标秒数要写出来")
        self.assertIn("85", s, "必须给出会被门拦下的下限")
        self.assertIn("加一遍", s, "要要求它自己把时长列加一遍")
        self.assertIn("4–12 秒", s, "供应商硬区间要写出来")
        self.assertIn("用满 12 秒", s, "同一场景并成一镜的口径要写出来")
        self.assertIn("镜内时间轴", s, "≥8 秒的镜必须写时间轴（程序会查）")
        self.assertIn("台词字数 ÷ 4", s, "口播物理保留 —— 它不是镜数机制")
        # ⛔ 被废弃的除法权威不许从任何地方爬回来
        for gone in ("镜数基线", "快切优先", "÷ 4（约", "默认 4 秒", "做不到 4 秒快切"):
            self.assertNotIn(gone, s, "「%s」已随除法基线废弃" % gone)

    def test_quota_cap_states_the_truncation_risk(self):
        """配额上限必须说清「超了会被静默截断」，但**不替分镜师决定镜数**。"""
        long_sec = (config.VIDEO_MAX_SHOTS + 1) * 4
        self._brief("约 %d 秒" % long_sec)
        with mock.patch.object(config, "INLINE_UPSTREAM", True):
            s = role_input("scenedesigner", self.root, {"episode_index": 1})
        self.assertIn("配额上限", s)
        self.assertIn("只渲前", s, "要讲清截断后果")
        self.assertNotIn("做不到 4 秒快切", s, "旧预告假定 4 秒基线，已废弃")

    def test_brief_without_target_still_ok(self):
        """brief 没写 target_duration → 不炸；景别那条仍要注入。"""
        self._brief("")
        with mock.patch.object(config, "INLINE_UPSTREAM", False):
            s = role_input("scenedesigner", self.root, {"episode_index": 1})
        self.assertNotIn("【硬性要求·片长】", s)
        self.assertIn("景别列写法", s, "景别写法与片长无关，必须始终注入")

    def test_genre_rule_and_shot_type_rule_present(self):
        """「景别」列会被 `qc.review_shot_type` 原样送进构图校验 → 必须禁括注。"""
        self._brief("约 180 秒")
        with mock.patch.object(config, "INLINE_UPSTREAM", False):
            s = role_input("scenedesigner", self.root, {"episode_index": 1})
        self.assertIn("只写景别本身", s)
        for w in ("远景", "全景", "中景", "近景", "特写"):
            self.assertIn(w, s, "规范词表要与 shortdrama 契约一致")

    def test_other_roles_not_polluted(self):
        """别把片长指令塞进别的角色的输入（避免污染 dialogue 等）。"""
        self._brief("约 180 秒")
        with mock.patch.object(config, "INLINE_UPSTREAM", False):
            s = role_input("dialogue", self.root, {"episode_index": 1})
        self.assertNotIn("【硬性要求·片长】", s)
        self.assertNotIn("景别列写法", s)


class TestContainerFactsInjected(unittest.TestCase):
    """★ 2026-10-02：分镜师必须被告知**自己的表会被怎么装**（出片容器事实）。

    为什么是真实故障而不是补充说明：今天逐条对账才发现，"一次生成能装多少秒、
    送哪几张图"这些事实**只存在于媒体层代码里**，创作链一侧一个字都没提。
    后果是可复现的：`luanzhen-xue-1001` 12 镜**零宽景** ⇒ 六组一张场景空镜都没进过
    请求；三人同镜时第三人没有定妆照（`chars[:2]` 硬卡）；分镜师按"每镜一张静帧"
    的旧假设排承接，而 2026-09-28 起一段里只有**第一镜**有图。
    这些他不知道，就只能靠"每镜重复写锚点"这条老规矩硬扛——而老规矩没告诉他为什么。

    ★ 数字**一律从代码常量读**（`video_plan.PACK_MAX_SECONDS` / `REF_SLOTS` /
    `PACK_REF_MAX_CHARS`、`assets.REF_CAP_SOLO`/`MULTI`）⇒ 最后一条测试改常量、
    不改文案，文案必须跟着变。这是本测试的存在意义：**不许在提示词里再抄一份数字**
    （同一批文档里"静帧取最后一拍"就是这么漂了 6 天的）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "brief.json").write_text(
            json.dumps({"topic": "测试片"}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _input(self):
        with mock.patch.object(config, "INLINE_UPSTREAM", False):
            return role_input("scenedesigner", self.root, {"episode_index": 1})

    def test_pack_mode_states_seconds_grouping_and_slots(self):
        from v5.media import video_plan as vp

        with mock.patch.object(config, "VIDEO_MODE", "pack"), \
                mock.patch.object(config, "VIDEO_PACK_MAX_GROUP", 5):
            s = self._input()
        self.assertIn("出片容器事实", s)
        self.assertIn("最长 %d 秒" % vp.PACK_MAX_SECONDS, s)
        self.assertIn("最多 5 个镜头并进同一条", s)
        self.assertIn("同一场的相邻镜", s,
                      "并组单位必须是**场**（2026-10-05 口径）——它决定分组形状")
        self.assertIn("镜长不设地板", s,
                      "★ 秒数地板已取消（场锁死、镜自由），注入里不许再写「每镜最短 N 秒」")
        self.assertNotIn("每镜最短", s, "旧措辞会随 PACK_MIN_SHOT_SECONDS=0 变成「每镜最短 0 秒」的假话")
        self.assertIn("最多 %d 张参考图" % vp.REF_SLOTS, s)
        self.assertIn("第三个人没有定妆照", s)
        self.assertIn("静帧不进这条请求", s)   # 2026-10-07：连"每镜一张"也不再提静帧
        self.assertIn("等比压进", s, "秒数会被压缩必须预告，否则分镜师以为声明会原样落地")
        self.assertIn("第一拍要写全身份锚点", s)

    def test_reference_mode_does_not_claim_packing(self):
        """非 pack 档没有"并组"这件事 ⇒ 不许照抄 pack 的说法。"""
        from v5.media import assets as A
        from v5.media import video_plan as vp

        with mock.patch.object(config, "VIDEO_MODE", "reference"):
            s = self._input()
        self.assertIn("出片容器事实", s)
        self.assertIn("一个镜头一条请求", s)
        self.assertNotIn("并进同一条", s, "reference 档说并组 = 对模型撒谎")
        self.assertIn("最长 %d 秒" % vp.PACK_MAX_SECONDS, s)
        self.assertIn("封顶 %d 张" % vp.SHEET_SLOTS, s,
                      "非 pack 档的封顶要说的是**素材图**的张数")
        self.assertIn("静帧不进这条请求", s)
        self.assertNotIn("先满足人脸", s,
                         "stills 档的措辞不该出现在默认档里"
                         "（⛔ 别改成比数字：REF_CAP_MULTI 与 SHEET_SLOTS 都是 3，"
                         "比数字会自己撞车）")

    def test_numbers_come_from_code_not_from_prose(self):
        """★ 反向锁：只改常量、不改文案 ⇒ 文案必须跟着变。

        写死的数字与代码漂开是本类测试要防的原病（不是新病）。
        """
        from v5.media import video_plan as vp

        with mock.patch.object(config, "VIDEO_MODE", "pack"), \
                mock.patch.object(vp, "REF_SLOTS", 4), \
                mock.patch.object(vp, "PACK_REF_MAX_CHARS", 1):
            s = self._input()
        self.assertIn("最多 4 张参考图", s, "REF_SLOTS 改了文案没跟着改 = 数字写死了")
        self.assertIn("最多 1 张 ⇒ 三个人同镜时", s,
                      "PACK_REF_MAX_CHARS 改了文案没跟着改 = 数字写死了")

    def test_other_roles_are_not_given_the_media_contract(self):
        """容器事实只给分镜师——编剧/对白看到"参考图槽位"只会分心。"""
        with mock.patch.object(config, "VIDEO_MODE", "pack"):
            for role in ("scriptwriter", "dialogue", "reviewer"):
                s = role_input(role, self.root, {"episode_index": 1})
                self.assertNotIn("出片容器事实", s, "%s 不该看到媒体层槽位" % role)


class TestPackSkillEpisodePathConsistency(unittest.TestCase):
    """类型包 SKILL 里**不得再写死集级产物路径**（M1，2026-09-16）。

    为什么这是**真实故障**而不是洁癖：角色按 SKILL 里的路径文案 `write_file`。
    写死 `scenedesigner/scenedesigner.md` ⇒ 第 2 集把第 1 集的分镜覆盖掉，
    而**每一层都报成功**（角色写成了、守卫按新名找不到 → 判「产物缺失」但
    日志里只看到"阶段未 complete"，查不出原因）。见 `guards.OUTPUTS` 上方的告警。

    所以：包里的文案必须指向**运行期注入**的路径（`system prompt 的【本角色产物路径】`
    或 `【开工前必读】` 清单），而不是自己拼死路径。
    """

    #: 集级产物的**旧**路径文案（M1 后一律不许出现在 SKILL 里）
    _FORBIDDEN = (
        "scenedesigner/scenedesigner.md",
        "reviewer/review.md",
        "dialogue/dialogue.md",
    )

    def test_no_hardcoded_legacy_output_paths(self):
        packs = config.SKILLS_DIR / "packs"
        skills = sorted(packs.rglob("SKILL.md"))
        self.assertTrue(skills, "没扫到任何 SKILL.md —— 路径不对，测试本身失效了")
        bad = []
        for f in skills:
            if "craft" in f.parts:          # 技法库不写产物路径
                continue
            body = f.read_text(encoding="utf-8")
            for pat in self._FORBIDDEN:
                if pat in body:
                    bad.append("%s 写了死路径 %s" % (f.relative_to(packs), pat))
        self.assertEqual(bad, [], "；".join(bad))

    def test_packs_with_own_role_skill_point_at_injected_path(self):
        """**自带** scenedesigner / reviewer SKILL 的包必须指向注入路径。

        因为 `_role_skill` 对自带该角色的包**不回退**到 shortdrama —— 这些包拿不到
        shortdrama 的契约，只能靠自己写清"路径由 system prompt 给出"。

        ⚠️ 只对**明确写了产物路径**的包要求（shortdrama 基线本就不提路径 ——
        它的路径由 `role_system_prompt` 注入，那是权威来源）。这里锁的是
        **"注入式表述"这个约定本身不会静默消失**。
        """
        packs = config.SKILLS_DIR / "packs"
        ok = []
        for role in ("scenedesigner", "reviewer"):
            for f in sorted(packs.glob("*/%s/SKILL.md" % role)):
                body = f.read_text(encoding="utf-8")
                if ("本角色产物路径" in body) or ("开工前必读" in body):
                    ok.append("%s/%s" % (f.parts[-3], role))
        self.assertGreaterEqual(
            len(ok), 2,
            "没有任何包用「注入式产物路径」表述了 —— 约定可能在重构里被抹掉（找到：%s）"
            % ok)

    def test_system_prompt_is_ep_agnostic(self):
        """★ M3：system prompt **只给形状**，不带具体集号。

        为什么这是硬要求：system prompt 在 `build_supervisor()` 里求值 ⇒ 编译期固化。
        写死集号 ⇒ ① 换集必须重启 dev；② 一个 dev 只能服务一集 ⇒
        M3 的「一次起服跑 N 集」**不可能**。所以确切路径必须由**运行期**的
        `role_input` 给出（见下一测试）。
        """
        from v5.roles import role_system_prompt
        for role in ("scriptwriter", "dialogue", "scenedesigner", "reviewer"):
            sp = role_system_prompt("shortdrama", role, 2)
            # ⚠️ 只能看**【本角色产物路径】那一句**：SKILL 正文会合法地**举例**提到
            #    `scriptwriter_ep1.md`（那是文档引用，不该被这条测试判违规）。
            tail = sp.split("【本角色产物路径】")[-1]
            self.assertIn("{N}", tail, "%s 的产物路径契约应给**形状**（含 {N}）" % role)
            self.assertNotIn("_ep2.md", tail, "%s 的产物路径契约不该带具体集号" % role)
            self.assertNotIn("_ep1.md", tail, "%s 的产物路径契约不该带具体集号" % role)

    def test_whole_drama_roles_are_not_called_episodic(self):
        """★ 真机验收抓到的 bug（2026-09-17）：**错误的元信息比没有更糟**。

        system prompt 原先把**所有**角色都描述成"产物是集级的"，
        于是模型给全剧级角色也加了集号 —— 实测产出 `worldbuilder/worldbuilder_ep1.md`，
        而声明路径是 `worldbuilder/worldbuilder.md` ⇒ 物化守卫判"未物化" ⇒ 整链白跑。
        """
        from v5.roles import role_system_prompt
        for role in ("worldbuilder", "assetdesigner", "plotdesigner", "director"):
            sp = role_system_prompt("shortdrama", role, 1)
            tail = sp.split("【本角色产物路径】")[-1]
            self.assertIn("全剧级", tail, "%s 应被描述为全剧级产物" % role)
            self.assertNotIn("是集级的", tail, "%s 不该被描述成集级产物" % role)
        for role in ("scriptwriter", "dialogue", "scenedesigner", "reviewer"):
            tail = role_system_prompt("shortdrama", role, 1).split("【本角色产物路径】")[-1]
            self.assertIn("是集级的", tail, "%s 是集级产物" % role)

    def test_role_input_carries_the_authoritative_episode_path(self):
        """★ M3：`role_input` 才是**本集产物路径的运行期权威**（每次开工都给）。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "brief.json").write_text('{"topic": "测试片"}', encoding="utf-8")
            for role in ("scriptwriter", "dialogue", "scenedesigner", "reviewer"):
                s = role_input(role, root, {"episode_index": 3})
                self.assertIn("本集产物路径", s, "%s 的开工契约没给本集路径" % role)
                self.assertIn("/" + guards.out_path(role, 3), s,
                              "%s 的开工契约没给第 3 集的确切路径" % role)
                self.assertIn("第 3 集", s, "%s 的开工契约没声明集号（M4 要求集感知）" % role)


class TestStillBeatClaimMatchesPipeline(unittest.TestCase):
    """包 SKILL 里「静帧取哪一拍」的说法必须与代码一致（2026-10-02 收口）。

    为什么这是真实故障而不是措辞洁癖：`prompt.content_line(beat_pick=...)` 从
    2026-09-26 起取**第一拍**（取最后一拍会让静帧提示词里一个服装词都不剩——
    brawl 实测同组三张静帧穿出两套外套），而**四个包的 scenedesigner SKILL 还在教
    「静帧取最后一拍」「朝向必须进最后一拍（静帧取它）」**。分镜师照 SKILL 写，
    身份锚点与朝向就落在静帧拿不到的那一拍上：定妆照没有服装词 → 视频继承错的衣服。
    同一个文件里甚至同时存在两种说法（shortdrama 第 101/116 行说"最后"、
    第 128 行说"第一"），模型只能挑一个听。
    """

    #: 已废弃的说法（剥掉 markdown 粗体后比对）
    _STALE = ("静帧取最后一拍", "静帧只取最后一拍",
              "朝向必须进最后一拍", "尤其最后一拍")

    def _skills(self):
        packs = config.SKILLS_DIR / "packs"
        files = sorted(packs.glob("*/scenedesigner/SKILL.md"))
        self.assertTrue(files, "没扫到 scenedesigner SKILL —— 路径不对，测试本身失效了")
        return files

    def test_no_pack_teaches_the_abandoned_last_beat(self):
        bad = []
        for f in self._skills():
            body = f.read_text(encoding="utf-8").replace("*", "")
            for pat in self._STALE:
                if pat in body:
                    bad.append("%s：%s" % (f.parts[-3], pat))
        self.assertEqual(bad, [], "SKILL 在教静帧拿不到的那一拍：" + "；".join(bad))

    def test_packs_teaching_still_consumption_point_at_first_beat(self):
        """**正向锁**：不许用"把那句话删掉"糊过上一条测试。

        凡是写了「静帧取…拍」的包，必须写成取第一拍；且这种包不得少于 4 个
        （五个自带分镜契约的包都该讲清这件事）。
        """
        covered = []
        for f in self._skills():
            body = f.read_text(encoding="utf-8").replace("*", "")
            if "静帧取" not in body and "静帧只取" not in body:
                continue
            self.assertTrue("静帧取第一拍" in body or "静帧只取第一拍" in body,
                            "%s 写了静帧取哪一拍，却没写第一拍" % f.parts[-3])
            covered.append(f.parts[-3])
        self.assertGreaterEqual(
            len(covered), 4,
            "讲清「静帧取第一拍」的包只剩 %s —— 契约不能靠删语句来合规" % covered)

    def test_pipeline_still_takes_the_first_beat(self):
        """代码侧的锚：判据变了要同时改文档，不许只改一边。"""
        from v5.media.prompt import content_line

        shot = {"visual": "0-2秒：@阿劲（灰蓝旧运动外套）抬手；2-4秒：他转身走开"}
        self.assertIn("灰蓝旧运动外套", content_line(shot, beat_pick="first"),
                      "静帧路径不再取第一拍 ⇒ 上面两条 SKILL 判据要一起重新对账")
        self.assertNotIn("灰蓝旧运动外套", content_line(shot, beat_pick="last"))


class TestLooseStoryboardSwitch(unittest.TestCase):
    """★ `SHORTDRAMA_LOOSE_STORYBOARD=1`：只放**创作约束**，不放**搬运纪律**。

    用户 1002 的要求是"除了 4-12 秒和 5 张图，其余全交回导演与分镜"。这个开关是
    为 A/B 而开（`scripts/ab_loose_storyboard.py`），不是把契约永久删掉——那几条
    硬性要求里有多条是拿事故换来的。两条边界由本测试钉住：
    ① 创作类（节奏/镜长/景别/站位/钩子/容器事实）整段消失；
    ② 搬运类（产物路径、上游全文、台词照抄、音频模式）**必须留下**——
       放开它们，两臂就不是同一部戏，A/B 失去共同口径。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "brief.json").write_text(json.dumps(
            {"topic": "测试片", "audio_mode": "dialogue-led",
             "target_duration": "约 120 秒"}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _in(self, loose):
        with mock.patch.object(config, "INLINE_UPSTREAM", False), \
                mock.patch.object(config, "LOOSE_STORYBOARD", loose):
            return role_input("scenedesigner", self.root, {"episode_index": 1})

    def test_creative_directives_gone_plumbing_kept(self):
        tight, loose = self._in(False), self._in(True)
        for tag in ("状态锚点每镜重复", "片长", "景别列写法", "多人镜的站位",
                    "身份锚点每镜重复", "关键拍点多角度", "开场即钩子", "出片容器事实"):
            self.assertIn(tag, tight, "现行档少了这条：%s" % tag)
            self.assertNotIn(tag, loose, "放开档还留着创作约束：%s" % tag)
        # 搬运纪律不受开关影响
        for keep in ("本集产物路径", "台词长度", "音频模式"):
            self.assertIn(keep, loose, "放开档把搬运纪律也删了：%s" % keep)
        self.assertNotEqual(tight, loose)

    def test_default_is_unchanged_and_other_roles_unaffected(self):
        """默认（不设环境变量）行为逐字不变；别的角色读不到这个开关。"""
        self.assertFalse(config.LOOSE_STORYBOARD, "默认必须是 0（放开是显式动作）")
        with mock.patch.object(config, "LOOSE_STORYBOARD", True), \
                mock.patch.object(config, "INLINE_UPSTREAM", False):
            s = role_input("assetdesigner", self.root, {"episode_index": 1})
        self.assertIn("道具形制逐字复制", s, "放开档不该动 assetdesigner 的纪律")


class TestDirectorReceivesCraft(unittest.TestCase):
    """★ M4 缺口（2026-09-17 修）：supervisor（= director）**必须**收到叙事技法。

    缺口是怎么漏掉的：`_craft_block` 只在 `roles.role_input()` 里调用，而
    `role_input` 的唯一调用点是 7 个子代理角色的节点工厂 —— supervisor 不走它。
    实测 jade-fish 的 dev.log：`plotdesigner 注入 3 份`、`scenedesigner 注入 1 份`、
    **director 0 份**；而 `short-drama-opening` 的 `inject-to` 明明写着
    `[director, plotdesigner]`。

    ⇒ 修法：把 supervisor 的 prompt 抽成**纯函数** `supervisor_system_prompt`
    （原先内联在 `build_supervisor()` 里，一调就要建 LLM，测试够不着 ——
    **能测的契约才守得住**），并在里面注入 director 的技法。
    """

    def _root(self, pack: str) -> Path:
        d = Path(tempfile.mkdtemp(prefix="dir_craft_"))
        self.addCleanup(shutil.rmtree, d, True)
        (d / "brief.json").write_text(
            json.dumps({"topic": "测试片", "pack": pack}), encoding="utf-8")
        return d

    def test_director_gets_craft_when_pack_enables_it(self):
        root = self._root("chinese-style-short-drama")
        sp = orchestrator.supervisor_system_prompt(
            "chinese-style-short-drama", 1, root)
        self.assertIn("叙事技法", sp, "supervisor 的 prompt 里没有技法区块")
        self.assertIn("short-drama-opening", sp,
                      "opening 的 inject-to 含 director，必须到达")

    def test_director_gets_nothing_when_pack_says_nothing(self):
        """羊毛毡包**没**声明 script-craft ⇒ 不该被短剧判据污染。"""
        root = self._root("wool-felt-story-short")
        sp = orchestrator.supervisor_system_prompt("wool-felt-story-short", 1, root)
        self.assertNotIn("叙事技法", sp)

    def test_discipline_still_present(self):
        """注入技法不能把调度纪律挤掉。"""
        root = self._root("chinese-style-short-drama")
        sp = orchestrator.supervisor_system_prompt("chinese-style-short-drama", 1, root)
        self.assertIn("调度纪律", sp)
        self.assertIn("监制", sp, "ORCHESTRATOR_DISCIPLINE 的开头就是【你是监制】")


class TestCatalogSlicing(unittest.TestCase):
    """★ M5：**按集切片注入** —— 全剧级长目录不能整份 inline。

    为什么必须做（spec M5 ①）：`plotdesigner/episodes.md` 改造后是**全剧级**产物，
    而 `role_input` 把上游产物全文 inline。1,404 集 ≈ 42 KB ⇒ **每一集的下游角色
    都会收到全份目录** ⇒ 上下文爆炸 + 注意力彻底稀释。
    ⚠️ 这是**改造引入的新问题**（老架构没有"全剧目录"）。
    """

    CAT = (
        "## 第 1 年（第 1–3 集）\n"
        "这一年巴赫刚到任，处处碰壁。\n"
        "### 第 1 集：到任\n"
        "巴赫走进教堂，发现唱诗班只剩八个人，管风琴还漏风。\n"
        "### 第 2 集：第一堂课\n"
        "学生故意把音唱错，巴赫没有发火，只是把谱子倒过来放。\n"
        "### 第 3 集：抄谱\n"
        "蜡烛烧到了谱角，他救下的却是别人的那本。\n"
        "## 第 2 年（第 4–6 集）\n"
        "手艺渐熟，麻烦也更大。\n"
        "### 第 4 集：新曲\n"
        "他熬夜写了三页，第二天被抄谱员弄丢了两页。\n"
    )

    def test_parse_catalog(self):
        from v5.roles import parse_catalog
        r = parse_catalog(self.CAT)
        self.assertEqual(sorted(r["episodes"]), [1, 2, 3, 4])
        self.assertEqual(len(r["volumes"]), 2)
        self.assertEqual(r["volumes"][0]["summary"], "这一年巴赫刚到任，处处碰壁。")
        self.assertEqual(r["episodes"][2]["body"], "学生故意把音唱错，巴赫没有发火，只是把谱子倒过来放。")

    def test_parse_catalog_tolerates_fullwidth_digits(self):
        """模型偶发写全角数字 —— 不归一化就会"解析不出集"。"""
        from v5.roles import parse_catalog
        r = parse_catalog("## 第１年\n### 第１集：开场\n正文。\n### 第２集：继续\n正文。\n")
        self.assertEqual(sorted(r["episodes"]), [1, 2])

    def test_slice_keeps_prev_self_next_and_volume(self):
        from v5.roles import slice_catalog
        r = slice_catalog(self.CAT, 2)
        self.assertTrue(r["ok"])
        self.assertIn("本集切片", r["text"])
        self.assertIn("前情提要", r["text"])
        self.assertIn("巴赫走进教堂", r["text"], "前一集的条目应作为前情提要出现")
        self.assertIn("第一堂课", r["text"])
        self.assertIn("抄谱", r["text"], "后一集应出现（承接/伏笔）")
        self.assertIn("这一年巴赫刚到任", r["text"], "本卷摘要应出现")

    def test_slice_marks_which_lines_it_came_from(self):
        """**切片必须可见**（spec M0 B2 ③）——否则又变成"静默截断"。"""
        from v5.roles import slice_catalog
        r = slice_catalog(self.CAT, 2)
        self.assertIn("目录共", r["text"])
        self.assertIn("解析出", r["text"])

    def test_slice_edges_have_no_missing_neighbours(self):
        from v5.roles import slice_catalog
        first = slice_catalog(self.CAT, 1)
        last = slice_catalog(self.CAT, 4)
        self.assertTrue(first["ok"])
        self.assertTrue(last["ok"])
        self.assertNotIn("前一集", first["text"], "第 1 集没有前一集")
        self.assertNotIn("后一集", last["text"], "最后一集没有后一集")

    def test_slice_fails_loudly_when_episode_missing(self):
        from v5.roles import slice_catalog
        r = slice_catalog(self.CAT, 99)
        self.assertFalse(r["ok"])
        self.assertIn("没有第 99 集", r["why"])

    def test_short_catalog_still_inlined_whole(self):
        """★ 单集项目的行为**完全不变** —— 这是本改动最大的风险面。"""
        from v5.roles import _upstream_block
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / guards.out_path("plotdesigner", 1)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# 分集剧情设计\n\n## 第1集：开场\n很短。\n", encoding="utf-8")
            blk = _upstream_block(root, "plotdesigner", 1)
            self.assertNotIn("按集切片", blk, "低于阈值的目录必须照旧全文")
            self.assertIn("很短。", blk)

    def test_single_episode_catalog_is_never_a_hard_stop(self):
        """★ 单集项目超阈值 ⇒ 注入全文，⛔ 不许把整条链判死。

        实错（2026-10-03 `yoga-affair-1003e`）：brief.episodes=1，plotdesigner 给这一集
        写了 9155 字的目录，里面**自然**没有「### 第 M 集」条目 ⇒ 切片失败 ⇒
        RuntimeError ⇒ 创作链 rc=3，8 分钟白跑、零出片。
        切片是为多集连载省上下文的；只有一集时整份目录就是本集，没有"本集 ±1"可切。
        """
        from v5.roles import SLICE_SOFT_ENV, SLICE_THRESHOLD, _upstream_block
        import os
        big = "这一集的详细剧情走向，按段落写，不带集编号。" * 400
        self.assertGreater(len(big), SLICE_THRESHOLD)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "brief.json").write_text('{"topic":"t","episodes":1}', encoding="utf-8")
            p = root / guards.out_path("plotdesigner", 1)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(big, encoding="utf-8")
            blk = _upstream_block(root, "plotdesigner", 1)
            self.assertIn("单集项目·不切片", blk)
            self.assertIn("不带集编号", blk, "必须真的把全文注进去，不能给空")
            # 反向对照：同样这份目录放在**多集**项目里仍然响亮终止（守卫没被顺手关掉）
            (root / "brief.json").write_text('{"topic":"t","episodes":3}', encoding="utf-8")
            old = os.environ.pop(SLICE_SOFT_ENV, None)
            try:
                with self.assertRaises(RuntimeError):
                    _upstream_block(root, "plotdesigner", 2)
            finally:
                if old is not None:
                    os.environ[SLICE_SOFT_ENV] = old

    def test_long_catalog_is_sliced(self):
        from v5.roles import SLICE_THRESHOLD, _upstream_block
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # ⚠️ 必须声明多集：切片只对多集连载存在（单集项目现在直接注入全文，
            #   见 test_single_episode_catalog_is_never_a_hard_stop）
            (root / "brief.json").write_text('{"topic":"t","episodes":4}', encoding="utf-8")
            p = root / guards.out_path("plotdesigner", 1)
            p.parent.mkdir(parents=True, exist_ok=True)
            big = self.CAT + ("### 第 5 集：填充\n" + "这句话用来把目录撑过阈值。" * 400 + "\n")
            p.write_text(big, encoding="utf-8")
            self.assertGreater(len(big), SLICE_THRESHOLD)
            blk = _upstream_block(root, "plotdesigner", 1)
            self.assertIn("按集切片", blk)
            self.assertLess(len(blk), len(big), "切片必须比全文短")

    def test_non_whitelisted_product_is_never_sliced(self):
        """分镜表这类产物**天生就该整份读** —— 切了就是"读到半张表却以为读全了"。"""
        from v5.roles import SLICE_THRESHOLD, _upstream_block
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / guards.out_path("scenedesigner", 1)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("| 1 | 中景 | 平视 | 固定 | 8 | 甲 | " + "画面" * 3000 + " | x | y |\n",
                         encoding="utf-8")
            self.assertGreater(p.stat().st_size, SLICE_THRESHOLD)
            blk = _upstream_block(root, "scenedesigner", 1)
            self.assertNotIn("按集切片", blk)

    def test_slice_failure_terminates_by_default_and_downgrades_with_env(self):
        """spec M0 B2 ④「失败响亮终止」；`SHORTDRAMA_SLICE_SOFT=1` 是应急降级开关。"""
        import os
        from v5.roles import SLICE_SOFT_ENV, SLICE_THRESHOLD, _upstream_block
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "brief.json").write_text('{"topic":"t","episodes":4}', encoding="utf-8")
            p = root / guards.out_path("plotdesigner", 1)
            p.parent.mkdir(parents=True, exist_ok=True)
            # 超阈值，但**没有**「第 N 集」结构 → 切不出来
            p.write_text("没有集标题的目录。" * 600, encoding="utf-8")
            self.assertGreater(len(p.read_text(encoding="utf-8")), SLICE_THRESHOLD)
            old = os.environ.get(SLICE_SOFT_ENV)
            try:
                os.environ.pop(SLICE_SOFT_ENV, None)
                with self.assertRaises(RuntimeError):
                    _upstream_block(root, "plotdesigner", 1)
                os.environ[SLICE_SOFT_ENV] = "1"
                blk = _upstream_block(root, "plotdesigner", 1)
                self.assertIn("切片失败", blk)
                self.assertIn("没有集标题的目录", blk, "降级模式应注入全文")
            finally:
                if old is None:
                    os.environ.pop(SLICE_SOFT_ENV, None)
                else:
                    os.environ[SLICE_SOFT_ENV] = old


    def test_catalog_missing_episodes_names_the_gaps(self):
        """★ 缺集必须**点名到集号**（2026-10-05 实测：文件非空 ≠ 目录可用）。

        第 4 轮的目录 10075 字、6 卷、只数出 4 个集条目（brief 要 10 集），
        旧判据照常放话"产物合格" ⇒ 角色带着"我写完了"的反馈重写 45 分钟、整条链零出片。
        """
        from v5.roles import catalog_missing_episodes as miss
        self.assertEqual(miss(self.CAT, 4), [], "1–4 集齐 ⇒ 不许报")
        self.assertEqual(miss(self.CAT, 10), [5, 6, 7, 8, 9, 10],
                         "缺的要一个个列出来，角色才知道补哪几集")
        self.assertEqual(miss(self.CAT, 1), [], "单集项目不判（行为与改造前一字不变）")
        self.assertEqual(miss("", 3), [1, 2, 3], "整份目录没集条目 ⇒ 全缺，也要列出来")

    def test_extra_beat_tables_do_not_count_as_episodes(self):
        """反向对照：给同一集再写一张「节拍表」**补不上缺的集**（按集号去重）。

        这正是那次事故的样子——角色把「场／节拍表」写进全剧目录，卷数膨胀、
        每集条目却没写完。判据若按「### 标题条数」数，就会把 4 集判成"合格"。
        """
        from v5.roles import catalog_missing_episodes as miss
        fat = ("## 第 1 卷\n### 第 1 集：开场\n正文\n### 第 1 集 节拍表（120 秒 / 10 场）\n"
               "场1 验尸房·凌晨\n场2 天台·夜\n"
               "## 第 2 卷\n### 第 2 集：反转\n正文\n### 第 2 集 节拍表（120 秒）\n场1 …\n"
               "## 第 3 卷\n### 第 3 集 节拍表（120 秒）\n场1 …\n")
        self.assertEqual(miss(fat, 3), [], "第 1、2、3 集都有条目 ⇒ 不报（哪怕只有一张表）")
        self.assertEqual(miss(fat, 10), [4, 5, 6, 7, 8, 9, 10],
                         "多写的节拍表不许把「缺第 4–10 集」瞒过去")


class TestCameraLightCraftReachesRoles(unittest.TestCase):
    """`camera-light-physics` 必须**真的到达**它声明的角色（1003 接入）。

    为什么单独测这条：技法是靠 `_craft_block` 在 `role_input` 里注入的，
    而"声明了却没到达"在这项目里发生过两次——
    ① supervisor 不走 role_input ⇒ director 收到了 0 份（M4，已修）；
    ② 系统级规则只写在某个包的 SKILL 里 ⇒ 自带该角色 SKILL 的包收不到
      （village-scale 因此交出 65% 片长被门拦下）。
    还有一条纪律：**没声明就必须一字不注入**（反质量包不要真实摄影的物理）。
    """

    def _root(self, craft=None):
        d = Path(tempfile.mkdtemp(prefix="cl_craft_"))
        self.addCleanup(shutil.rmtree, d, True)
        b = {"topic": "测试片", "pack": "shortdrama"}
        if craft is not None:
            b["script-craft"] = craft
        (d / "brief.json").write_text(json.dumps(b), encoding="utf-8")
        return d

    def test_declared_reaches_scenedesigner(self):
        root = self._root(["camera-light-physics"])
        s = role_input("scenedesigner", root, {"episode_index": 1})
        self.assertIn("camera-light-physics", s, "技法名都没到 ⇒ inject-to 或 opt-in 断了")
        self.assertIn("终点停", s, "运镜写法的核心（终点）没到，注入被截了？")
        self.assertIn("光落点", s, "光落点那句没到 ⇒ 第二要素等于没提")

    def test_declared_reaches_assetdesigner(self):
        """空间尺寸写在**场景卡**是这技法的关键分工——卡的角色收不到就会漂回每镜。"""
        root = self._root(["camera-light-physics"])
        s = role_input("assetdesigner", root, {"episode_index": 1})
        self.assertIn("camera-light-physics", s)

    def test_undeclared_injects_nothing(self):
        for role in ("scenedesigner", "assetdesigner", "director"):
            s = role_input(role, self._root(), {"episode_index": 1})
            self.assertNotIn("camera-light-physics", s, "%s 被没声明的技法污染了" % role)
            self.assertNotIn("光落点", s)


class TestEveryPackDeclaresVerdictBlock(unittest.TestCase):
    """每个包的 reviewer 契约都**必须**给出围栏判定块示例（1003 零成片事故）。

    病根：`guards.media_gate` 只认 `decision.parse_decision` 能解析出的
    `pass / rerun / reasons`；散文写「## 通过判定 ✅」它读不到 ⇒ 产物七份齐全、
    评审也写了通过，链仍停在「评审未通过」上。实测 `drydock-dawn-1003`
    白跑 1 小时 55 分、零成片。而漏写示例的正是**回落基准包 shortdrama**
    ——所有不自带 reviewer 的包都照它学，漏在这里代价最大。
    """

    @staticmethod
    def _has_block(text: str) -> bool:
        """契约里是否给了「机器可读判决」的围栏块示例。

        必需三件：围栏 + `pass` + `rerun`/`reasons`。`advisory` 是可选字段
        （牛来包的判定块就没写它，那份契约是完整的），⛔ 别把它列进必需项——
        那会把合法的契约判成缺陷，正是本测试要防的"假红"。
        """
        fence = chr(96) * 3
        return (fence in text and "pass:" in text
                and "rerun" in text and "reasons" in text)

    def test_all_packs_declare_it(self):
        packs = sorted(p.parent.parent.name
                       for p in (config.SKILLS_DIR / "packs").glob("*/reviewer/SKILL.md"))
        self.assertGreaterEqual(len(packs), 5, "扫到的包太少，这条测试会假绿")
        missing = []
        for pk in packs:
            body = (config.SKILLS_DIR / "packs" / pk / "reviewer" / "SKILL.md").read_text(
                encoding="utf-8")
            if not self._has_block(body):
                missing.append(pk)
        self.assertEqual([], missing,
                         "这些包的 reviewer 契约没给判定块示例，产物会读不出判决：%s" % missing)

    def test_fallback_base_pack_is_the_one_that_matters(self):
        """shortdrama 是回落基准——它必须自己带上（缺了就是全链的坑）。"""
        body = (config.SKILLS_DIR / "packs" / "shortdrama" / "reviewer" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertTrue(self._has_block(body),
                        "基准包 reviewer 缺判定块示例 ⇒ 所有回落它的包都不会写判决块")

    def test_measure_would_catch_the_old_contract(self):
        """反向对照：把旧版契约（没有判定块那一节）装回来，这条检查必须红。"""
        body = (config.SKILLS_DIR / "packs" / "shortdrama" / "reviewer" / "SKILL.md").read_text(
            encoding="utf-8")
        cut = body.split("## ★ 判定块")[0]
        self.assertTrue(cut.strip(), "切片失败（找不到那一节的开头？）")
        self.assertFalse(self._has_block(cut),
                         "删掉判定块那节后仍然算通过 = 这项检查测不到旧病")


if __name__ == "__main__":
    unittest.main()


class TestContainerFactsMatchPipeline(unittest.TestCase):
    """「出片容器事实」是**直接喂给分镜师的模型**的话 —— 它说错一句，分镜就照着错的方向写。

    2026-10-07 静帧退出视频输入后，这段里有三句会变成假的（本镜静帧 + 定妆照、
    本组第一镜静帧、静帧取第一拍）。这里逐条对账到代码，不靠人记。
    """

    def _text(self, mode=None, ref_source=None):
        import importlib
        import os
        from v5 import config
        from v5.media import video_plan as vp
        saved = dict(os.environ)
        try:
            if mode:
                os.environ["SHORTDRAMA_VIDEO_MODE"] = mode
            if ref_source:
                os.environ["SHORTDRAMA_VIDEO_REF_SOURCE"] = ref_source
            elif "SHORTDRAMA_VIDEO_REF_SOURCE" in os.environ:
                del os.environ["SHORTDRAMA_VIDEO_REF_SOURCE"]
            importlib.reload(config)
            importlib.reload(vp)
            importlib.reload(sys.modules["v5.roles"] if "v5.roles" in sys.modules
                             else importlib.import_module("v5.roles"))
            from v5 import roles
            return roles._container_facts(vp), vp
        finally:
            os.environ.clear()
            os.environ.update(saved)
            importlib.reload(config)
            importlib.reload(vp)

    def test_sheets_mode_says_no_still_and_the_real_cap(self):
        text, vp = self._text("reference")
        self.assertIn("静帧不进这条请求", text)
        self.assertNotIn("本镜静帧 + 人物定妆照", text, "旧描述还留着 = 分镜会照旧写")
        self.assertIn("图的张数超过本镜人数", text)
        self.assertIn(str(vp.SHEET_SLOTS), text)

    def test_pack_mode_drops_the_first_shot_still(self):
        text, _vp = self._text("pack")
        self.assertIn("上一组成片的**真实末帧**", text)
        self.assertNotIn("第一镜**的静帧", text)
        self.assertIn("不分景别", text, "场景现在按表列无条件取")

    def test_stills_fallback_keeps_the_old_words(self):
        text, _vp = self._text("reference", "stills")
        self.assertIn("本镜静帧 + 人物定妆照", text)
        self.assertIn("静帧取第一拍", text)
