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

    def test_target_injected_with_shot_count_and_floor(self):
        """180 秒 → 必须同时给出「÷4 基线 ≈45 镜」「镜长由内容决定」与「85% 下限」。

        ★ 2026-09-17：除数 7 → **4**（用户定档「4 秒一镜、快切优先」）。
        ⚠️ 本测试的 brief **刻意不写镜数** —— 最初写的「共 26 镜左右」会随
        brief 一起注入分镜师的输入，让 `assertIn("26")` 从 **brief 自己**那里
        「假通过」（断言被测试数据污染 = 等于没测）。
        """
        self._brief("约 180 秒（3 分钟）")
        with mock.patch.object(config, "INLINE_UPSTREAM", True):
            s = role_input("scenedesigner", self.root, {"episode_index": 1})
        self.assertIn("片长与镜头数", s)
        self.assertIn("180", s, "目标秒数要写出来")
        self.assertIn("45", s, "180 ÷ 4 ≈ 45 镜")
        self.assertIn("4 秒", s, "快切基线（默认 4 秒）要写出来")
        self.assertIn("镜长由内容决定", s, "镜长不能被写死成固定值")
        self.assertIn("85", s, "必须给出会被门拦下的下限")
        self.assertIn("加一遍", s, "要要求它自己把时长列加一遍")

    def test_quota_cap_warns_when_fast_cut_impossible(self):
        """目标秒数 ÷ 4 超过配额上限 → 必须**显式预告**本片做不到 4 秒快切。

        物理约束：4 秒/镜 × `AGNES_VIDEO_MAX_SHOTS` ⇒ 支持快切的最长片有上限。
        超了若不预告，模型会排出**会被静默截断**的镜数（虽是半成品，却已烧完创作链）。
        """
        long_sec = (config.VIDEO_MAX_SHOTS + 1) * 4
        self._brief("约 %d 秒" % long_sec)
        with mock.patch.object(config, "INLINE_UPSTREAM", True):
            s = role_input("scenedesigner", self.root, {"episode_index": 1})
        self.assertIn("配额上限", s)
        self.assertIn("做不到 4 秒快切", s)

    def test_brief_without_target_still_ok(self):
        """brief 没写 target_duration → 不炸；景别那条仍要注入。"""
        self._brief("")
        with mock.patch.object(config, "INLINE_UPSTREAM", False):
            s = role_input("scenedesigner", self.root, {"episode_index": 1})
        self.assertNotIn("片长与镜头数", s)
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
        self.assertNotIn("片长与镜头数", s)
        self.assertNotIn("景别列写法", s)


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

    def test_long_catalog_is_sliced(self):
        from v5.roles import SLICE_THRESHOLD, _upstream_block
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
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


if __name__ == "__main__":
    unittest.main()
