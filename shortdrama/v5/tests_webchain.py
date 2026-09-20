# -*- coding: utf-8 -*-
"""`v5/webchain.py`（P2b 创建链）自测。

**不打真 LLM、不起真 dev server、不跑真链**：
  · `webchain._chat` / `extract_brief` 打桩
  · `subprocess.Popen` / `probe_ok` / `subprocess.run` 打桩

覆盖重点是**判据与护栏**：
  · 类型包回落必须**说得出来**（不假装映射上了）
  · brief 缺字段**不猜**（带"缺哪些"重试一次，仍缺就抛）
  · dev server 复用/切换/超时三条路
  · 链产物核对**必须用 GATE_ROLES（7 个）**，用 ROLES（8 个，含 director）会永远判失败
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v5 import config, guards, webchain  # noqa: E402
from v5.media import runner  # noqa: E402

GOOD_BRIEF = {
    "topic": "纸扎铺的夜", "pack": "shortdrama", "genre": "恐怖悬疑",
    "episodes": 1, "target_duration": "120 秒，共 30 镜，每镜 4 秒快切，避免全表等长",
    "protagonist": "纸扎匠：年约四十的男性，深蓝棉袄，全片只用这一个固定人名。",
    "must_have": ["他接下十倍报酬的单子", "他照着手机相册里的活人脸画纸人",
                  "画过的活人接连失踪", "他掀开最后一个纸人的白布看见自己的脸"],
    "key_props": ["空钱盒：磨损铁皮，盒底无币——全片逐字一致"],
    "禁忌": ["画面无可读文字"], "tone": "冷色调、低照度，情绪由紧到崩",
    "结局": "他举着狼毫笔定格在纸人面前",
}


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._pp = mock.patch.object(config, "PROJECTS_DIR", self.root)
        self._pr = mock.patch.object(config, "PROJECT_ROOT", self.root)
        self._pp.start(); self._pr.start()

    def tearDown(self):
        self._pr.stop(); self._pp.stop()
        self.tmp.cleanup()


# ════════════════════════════════ 类型包 / 项目名

class TestPackAndPid(_Base):
    def test_known_packs_reads_directory(self):
        """★ 包列表**以 `v5/skills/packs/` 目录为唯一真相源**，不硬编。

        实测事故（2026-09-16）：我硬编了 3 个包，项目当天新增第 4 个
        `wool-felt-story-short` → 显式传它被**静默误拒**（回落 shortdrama、
        连说明都没有）—— 正是本项目最忌的"选了 A 实际跑 B"。
        """
        packs = webchain.known_packs()
        self.assertIn("shortdrama", packs)
        self.assertNotIn("craft", packs, "craft/ 是技法库、不是类型包")
        # 与 `/styles` 同源
        from v5 import webmap
        self.assertEqual(sorted(packs), sorted(s["code"] for s in webmap.styles()))

    def test_every_real_pack_is_accepted(self):
        """**任何一个真实存在的包都必须被接受**（防止再次硬编漏掉新包）。"""
        for p in webchain.known_packs():
            got, note = webchain.resolve_pack("", p)
            self.assertEqual(got, p, "包 %r 被误拒了" % p)
            self.assertEqual(note, "", "包 %r 被接受时不该带说明" % p)

    def test_explicit_pack_wins(self):
        pack, note = webchain.resolve_pack("whatever_code", "niulai-movie-style")
        self.assertEqual(pack, "niulai-movie-style")
        self.assertEqual(note, "")

    def test_unknown_explicit_pack_falls_back_and_says_so(self):
        """传了不存在的包名 → 回落 + **必须说明**（不能静默换包）。"""
        pack, note = webchain.resolve_pack("", "no-such-pack")
        self.assertEqual(pack, "shortdrama")
        self.assertIn("不存在", note)
        self.assertIn("shortdrama", note, "说明里要列出当前有哪些包")

    def test_keyword_mapping_is_reported(self):
        # 2026-09-16：原用 `realpeople_3d_style`（映射 3d-animation），该包铲除后改用 wool 关键词。
        pack, note = webchain.resolve_pack("realpeople_wool_felt_style")
        self.assertEqual(pack, "wool-felt-story-short")
        self.assertTrue(note, "按关键词映射**必须说明**，不能静默")

    def test_unknown_style_falls_back_and_says_so(self):
        """★ 不认识的前端风格码回落 `shortdrama` 并**说明**（v5 当前 4 个包）。"""
        pack, note = webchain.resolve_pack("realpeople_horror_film_style")
        self.assertEqual(pack, "shortdrama")
        self.assertIn("不对应任何 v5 类型包", note)

    def test_empty_style_is_silent(self):
        self.assertEqual(webchain.resolve_pack(""), ("shortdrama", ""))

    def test_slug_and_dedup(self):
        self.assertEqual(webchain._slug("Paper Crane!"), "paper-crane")
        self.assertEqual(webchain._slug("纸扎铺"), "")
        pid = webchain.new_pid("paper-crane")
        (self.root / pid).mkdir()
        pid2 = webchain.new_pid("paper-crane")
        self.assertNotEqual(pid, pid2, "**目录名即 pid** → 必须去重")
        self.assertTrue(pid2.startswith(pid))

    def test_pid_fallback_when_no_ascii(self):
        self.assertTrue(webchain.new_pid("纸扎铺").startswith("paste-"))


# ════════════════════════════════ JSON 归一化

class TestParseJson(_Base):
    def test_plain(self):
        self.assertEqual(webchain._parse_json_obj('{"a": 1}'), {"a": 1})

    def test_strips_code_fence(self):
        """契约里说了别用围栏，模型仍可能加 → 必须剥掉（本项目踩过"连围栏一起抄"）。"""
        self.assertEqual(webchain._parse_json_obj('```json\n{"a": 1}\n```'), {"a": 1})

    def test_takes_outermost_object(self):
        self.assertEqual(webchain._parse_json_obj('好的：{"a": 2} 完毕'), {"a": 2})

    def test_invalid_raises_with_excerpt(self):
        """解析失败**不许静默返回 {}**（否则"为什么没建成功"完全看不见）。"""
        with self.assertRaises(ValueError) as c:
            webchain._parse_json_obj("完全不是 JSON")
        self.assertIn("原文前", str(c.exception))


# ════════════════════════════════ brief 提炼

class TestExtractBrief(_Base):
    def test_ok_first_try(self):
        with mock.patch.object(webchain, "_chat", lambda *a, **k: json.dumps(GOOD_BRIEF, ensure_ascii=False)):
            b = webchain.extract_brief("正文" * 100, "shortdrama")
        self.assertEqual(b["topic"], "纸扎铺的夜")

    def test_retries_with_missing_list_then_succeeds(self):
        """★ 缺字段**不猜**：带"缺哪些"重试一次。"""
        calls = []

        def fake_chat(msgs, **k):
            calls.append(msgs)
            if len(calls) == 1:
                bad = dict(GOOD_BRIEF)
                bad.pop("结局")
                return json.dumps(bad, ensure_ascii=False)
            return json.dumps(GOOD_BRIEF, ensure_ascii=False)

        with mock.patch.object(webchain, "_chat", fake_chat):
            b = webchain.extract_brief("正文" * 100, "shortdrama")
        self.assertEqual(len(calls), 2)
        # 第二次的 user 消息里要**点名**缺了哪个字段
        self.assertIn("结局", json.dumps(calls[1], ensure_ascii=False))
        self.assertEqual(b["结局"], GOOD_BRIEF["结局"])

    def test_raises_after_second_failure(self):
        bad = dict(GOOD_BRIEF)
        bad.pop("tone")
        with mock.patch.object(webchain, "_chat", lambda *a, **k: json.dumps(bad, ensure_ascii=False)):
            with self.assertRaises(ValueError) as c:
                webchain.extract_brief("正文" * 100, "shortdrama")
        self.assertIn("tone", str(c.exception))

    def test_rejects_too_short(self):
        with self.assertRaises(ValueError) as c:
            webchain.extract_brief("太短", "shortdrama")
        self.assertIn("太短", str(c.exception))

    def test_spec_mentions_value_requirements(self):
        """契约要**同时规定结构与值**（只规定结构，模型会在值上自由发挥）。"""
        spec = webchain._brief_spec("shortdrama")
        for kw in ("至少", "字", "具体可拍", "不要编造"):
            self.assertIn(kw, spec)
        self.assertNotIn("```", spec, "契约里的示例**别用代码围栏包**（模型会连围栏一起抄）")


# ════════════════════════════════ 建项目

class TestCreateProject(_Base):
    def _create(self, text="正文内容" * 30, **kw):
        with mock.patch.object(webchain, "extract_brief",
                               lambda *a, **k: dict(GOOD_BRIEF)):
            return webchain.create_project(text, log=lambda *_: None, **kw)

    def test_writes_brief_and_keeps_script(self):
        r = self._create()
        root = self.root / r["pid"]
        self.assertTrue((root / "brief.json").exists())
        b = json.loads((root / "brief.json").read_text(encoding="utf-8"))
        self.assertEqual(b["pack"], "shortdrama")
        # ★ 用户原文不能丢
        self.assertTrue((root / "scriptwriter" / "scriptwriter_ep1.md").exists())
        self.assertIn("正文内容", (root / "scriptwriter" / "scriptwriter_ep1.md")
                      .read_text(encoding="utf-8"))
        # 来源说明
        note = (root / "scriptwriter" / "_source-note.md").read_text(encoding="utf-8")
        self.assertIn("用户在网页端粘贴提供", note)

    def test_pack_from_keyword_note_passthrough(self):
        # 2026-09-16：同上，3d-animation 铲除后改用 wool 关键词。
        r = self._create(style_code="realpeople_wool_felt_style")
        self.assertEqual(r["pack"], "wool-felt-story-short")
        self.assertTrue(r["note"])

    def test_explicit_name_overrides_topic(self):
        r = self._create(name="我起的名字")
        self.assertEqual(r["topic"], "我起的名字")

    def test_does_not_run_chain(self):
        """建项目**不跑创作链**（秒级 vs 十几分钟，必须分开）。"""
        with mock.patch.object(webchain, "ensure_devserver") as m:
            self._create()
        m.assert_not_called()

    def test_public_project_shape(self):
        r = self._create()
        pub = webchain.public_project(self.root / r["pid"])
        self.assertEqual(pub["id"], r["pid"], "前端读 `p.id`")
        self.assertEqual(pub["pid"], r["pid"])
        a = pub["analyze"]
        # 此刻还没有资产条目 → 角色数来自 brief，且**带 note 说明**
        self.assertEqual(a["characters"], 1)
        self.assertEqual(a["scenes"], 0)
        self.assertTrue(a["note"], "没有资产条目时要说明原因，别让人以为解析漏了")


# ════════════════════════════════ dev server（D6）

class TestMultiEpisodeBrief(_Base):
    """★★ brief 契约必须**按集数**给不同口径（2026-09-19 修的真 bug）。

    ## 事故形态（用户要"跑两集老夫子连续剧"时撞上的）

    契约里写死「`episodes`：整数，**固定填 1**」，而 `create_project` 事后把
    `episodes` 覆盖成用户选的值 ⇒ **模型按单集设计、我们偷偷改成多集**。
    实测产出的 brief：
      · `must_have` 4 条**全是第 1 集的剧情** ⇒ 第 2 集**必被忠实度门拦下**；
      · 没有 `second_character` ⇒ 第二个角色没有角色卡；
      · `禁忌` 冒出「无老赵…出现」（`must_have` 又要求老赵出场）= 自相矛盾。
    """

    def test_multi_episode_spec_uses_per_episode_wording(self):
        spec = webchain._brief_spec("laofuzi-hk-retro", "idea", 2)
        self.assertIn("每一集都能覆盖", spec, "多集必须要求「每集都能覆盖」的硬要求")
        self.assertIn("2 集", spec)
        self.assertNotIn("固定填 1", spec, "不能再把 episodes 写死成 1")
        self.assertIn("固定填 2", spec)
        self.assertIn("second_character", spec, "多集/双角色场景必须要求填第二角色")
        self.assertIn("audio_mode", spec, "audio_mode 必须要求显式填")

    def test_single_episode_spec_keeps_four_act_wording(self):
        spec = webchain._brief_spec("shortdrama", "idea", 1)
        self.assertIn("钩子/发展/转折/收尾", spec)
        self.assertNotIn("每一集都能覆盖", spec)

    def test_spec_forbids_character_banning_in_taboo(self):
        """提示词里必须**明写**不许把角色写进 `禁忌`（第二道闸门见 `_sanitize_brief`）。"""
        for ep in (1, 2):
            spec = webchain._brief_spec("shortdrama", "idea", ep)
            self.assertIn("把角色禁掉", spec)
            self.assertIn("自相矛盾", spec)

    def test_target_duration_says_per_episode_when_multi(self):
        spec = webchain._brief_spec("shortdrama", "idea", 3)
        bare = spec.replace("**", "")
        self.assertIn("每一集的时长", bare, "多集必须说明时长是**每集**的、不是合计")
        self.assertNotIn("每一集的时长", webchain._brief_spec("shortdrama", "idea", 1))

    def test_no_dangling_cross_reference(self):
        """契约里不许出现"请按下面的要求写"这类**悬空交叉引用**。

        实测（2026-09-19）：多集分支的 tail 里又列了一条 `must_have`，写着
        「请按下面的要求写（多集口径）」——**而下面已经没有了**，
        且与上面的 `mh_rule` 构成"同一字段两份说明"（本项目最忌）。
        """
        for ep in (1, 2):
            for mode in ("idea", "script"):
                spec = webchain._brief_spec("shortdrama", mode, ep)
                self.assertNotIn("按下面的要求写", spec)
                # 同一字段只允许出现一次说明
                self.assertEqual(spec.count("- `must_have`："), 1,
                                 "%s/%d 集：must_have 说明写了两份" % (mode, ep))

    def test_sanitize_drops_character_banning_taboo(self):
        """确定性兜底：把「无<片中人物>出现」从 `禁忌` 里摘掉，并**说出来**。

        不摘的后果（实测）：`禁忌` 是**静帧 QC 的 P0 判据来源** ⇒
        每一镜只要有该角色就被判硬伤 ⇒ 无限重画直到撞上限、最终带伤放行（纯烧配额）。
        """
        b = {
            "protagonist": "老夫子：圆脸、短发，明黄长衫。全片只用这一个固定人名。",
            "second_character": "老赵：光头浓须、紫红西服、粉红领带。",
            "must_have": ["老夫子 与 老赵 在 骑楼 扭打"],
            "禁忌": ["无字幕", "无老赵（光头浓须、紫红西服、粉红领带）出现", "无背景音乐"],
        }
        logs = []
        out = webchain._sanitize_brief(dict(b), log=logs.append)
        self.assertEqual(out["禁忌"], ["无字幕", "无背景音乐"],
                         "只摘'禁角色'那条，画面层硬约束要留下")
        self.assertTrue(logs and "已摘掉" in logs[0], "必须**说出来**，不能静默改契约")

    def test_sanitize_keeps_normal_taboo_untouched(self):
        b = {"protagonist": "纸扎匠：四十岁，深蓝棉袄。",
             "禁忌": ["无字幕、无烧录文字", "无第二张清晰人脸"]}
        out = webchain._sanitize_brief(dict(b), log=lambda *_: None)
        self.assertEqual(out["禁忌"], b["禁忌"], "正常禁忌不该被动")

    def test_sanitize_never_empties_required_field(self):
        """`禁忌` 是**必填字段** ⇒ 就算全被摘掉也要补一条，别把 brief 变成"缺字段"。"""
        b = {"protagonist": "老夫子：明黄长衫。", "禁忌": ["无老夫子出现"]}
        out = webchain._sanitize_brief(dict(b), log=lambda *_: None)
        self.assertTrue(out["禁忌"], "不能摘成空数组（validate 会判缺字段）")

    def test_create_project_forwards_episodes_to_spec(self):
        """`create_project(episodes=N)` 必须把 N 传到契约里（否则模型仍按单集设计）。"""
        seen = {}

        def fake_extract(text, pack, mode="script", episodes=1, log=print):
            seen["episodes"] = episodes
            return dict(GOOD_BRIEF)

        with mock.patch.object(webchain, "extract_brief", fake_extract):
            webchain.create_project("一句创意" * 10, mode="idea", episodes=2,
                                    log=lambda *_: None)
        self.assertEqual(seen.get("episodes"), 2)


class _FakeProc:
    def __init__(self, argv, **kw):
        self.argv = argv
        self.kw = kw
        self.pid = 7777
        self.returncode = None

    def poll(self):
        return None


def _probe_free_then_ready():
    """`probe_ok` 桩：**入口**那次返回 False（端口空闲），之后返回 True（新 server 就绪）。

    ⚠️ 不能恒返回 True：入口 `probe_ok()==True` 在新逻辑里表示**端口已被占**，
    会走"拒绝接管"分支（实测踩到 —— 而且本机 2024 上真有个遗留 server）。
    """
    box = {"n": 0}

    def _p(**kw):
        box["n"] += 1
        return box["n"] > 1
    return _p


class TestDevServer(_Base):
    def test_reuse_when_same_project_alive_ok(self):
        webchain._write_state({"pid": "p1", "os_pid": 7777})
        with mock.patch.object(webchain, "_alive", lambda p: True), \
             mock.patch.object(webchain, "probe_ok", lambda **k: True), \
             mock.patch.object(webchain.subprocess, "Popen") as popen:
            st = webchain.ensure_devserver("p1", log=lambda *_: None)
        self.assertTrue(st["reused"])
        popen.assert_not_called()
        self.assertEqual(st["pid"], "p1")

    def test_switch_project_stops_old_and_archives(self):
        """★ D6：换项目必须**重启** dev（项目目录编译期绑定）。"""
        webchain._write_state({"pid": "old", "os_pid": 4321})
        (self.root / ".langgraph_api").mkdir()
        stopped, archived = {}, {}
        with mock.patch.object(webchain, "_alive", lambda p: True), \
             mock.patch.object(webchain, "probe_ok", _probe_free_then_ready()), \
             mock.patch.object(webchain, "stop_devserver",
                               lambda log=None: stopped.setdefault("n", 1)), \
             mock.patch.object(webchain, "archive_api_dir",
                               lambda log=None: archived.setdefault("n", 1)), \
             mock.patch.object(webchain.subprocess, "Popen", _FakeProc):
            st = webchain.ensure_devserver("new", log=lambda *_: None)
        self.assertEqual(stopped.get("n"), 1, "换项目要先停旧 dev")
        self.assertEqual(archived.get("n"), 1, "要归档 .langgraph_api（防陈旧 run 复活）")
        self.assertFalse(st["reused"])
        self.assertEqual(webchain.read_state()["pid"], "new")

    def test_env_binds_project_and_port(self):
        with mock.patch.object(webchain, "probe_ok", _probe_free_then_ready()), \
             mock.patch.object(webchain, "archive_api_dir", lambda log=None: ""), \
             mock.patch.object(webchain.subprocess, "Popen", _FakeProc):
            webchain.ensure_devserver("pX", log=lambda *_: None)
        st = webchain.read_state()
        self.assertEqual(st["port"], 2024)
        self.assertEqual(st["pid"], "pX")

    def test_log_handle_not_leaked(self):
        """★ 日志句柄只给子进程用 —— 父进程 spawn 完必须关，否则每次切项目泄漏一个。"""
        with mock.patch.object(webchain, "probe_ok", _probe_free_then_ready()), \
             mock.patch.object(webchain, "archive_api_dir", lambda log=None: ""), \
             mock.patch.object(webchain.subprocess, "Popen", _FakeProc):
            webchain.ensure_devserver("pL", log=lambda *_: None)
        logf = self.root / ".tmp" / "web-devserver.log"
        self.assertTrue(logf.exists())
        logf.unlink()          # 句柄没关的话 Windows 上这一步会 PermissionError

    def test_archive_moves_dir(self):
        (self.root / ".langgraph_api").mkdir()
        name = webchain.archive_api_dir(log=lambda *_: None)
        self.assertTrue(name.startswith(".langgraph_api.bak-web-"))
        self.assertFalse((self.root / ".langgraph_api").exists())
        self.assertTrue((self.root / name).exists())

    def test_archive_noop_when_absent(self):
        self.assertEqual(webchain.archive_api_dir(log=lambda *_: None), "")

    def test_timeout_raises(self):
        with mock.patch.object(webchain, "probe_ok", lambda **k: False), \
             mock.patch.object(webchain, "archive_api_dir", lambda log=None: ""), \
             mock.patch.object(webchain.subprocess, "Popen", _FakeProc):
            with self.assertRaises(RuntimeError) as c:
                webchain.ensure_devserver("pY", log=lambda *_: None, wait_s=0.2)
        self.assertIn("未就绪", str(c.exception))

    def test_early_exit_raises_with_log_hint(self):
        class Dead(_FakeProc):
            def poll(self):
                return 3
        with mock.patch.object(webchain, "probe_ok", lambda **k: False), \
             mock.patch.object(webchain, "archive_api_dir", lambda log=None: ""), \
             mock.patch.object(webchain.subprocess, "Popen", Dead):
            with self.assertRaises(RuntimeError) as c:
                webchain.ensure_devserver("pZ", log=lambda *_: None, wait_s=5)
        self.assertIn("启动即退出", str(c.exception))

    def test_status_shape(self):
        st = webchain.devserver_status()
        for k in ("alive", "ok", "agent_url"):
            self.assertIn(k, st)
        self.assertIn("2024", st["agent_url"])


class TestIdeaMode(_Base):
    """★ `mode="idea"`（AI 创作）与 `mode="script"`（粘贴剧本）**是两种活**。

    | mode | 谁写剧本 | 为什么 |
    |---|---|---|
    | script | 用户（原文进 scriptwriter 产物位） | 链看到编剧产物已存在 → **沿用它** |
    | idea   | 创作链（**不写** scriptwriter 产物） | 否则两句点子会被当成整部剧本，全片毁掉 |
    """

    def test_idea_spec_asks_model_to_design(self):
        spec = webchain._brief_spec("shortdrama", "idea")
        for kw in ("创作任务", "你来设计", "不要复述"):
            self.assertIn(kw, spec)
        self.assertNotIn("以原文为准", spec)
        self.assertNotIn("```", spec)

    def test_script_spec_stays_extractive(self):
        spec = webchain._brief_spec("shortdrama", "script")
        self.assertIn("提炼", spec)
        self.assertIn("直接取自剧本", spec)

    def test_idea_accepts_short_input_script_does_not(self):
        with mock.patch.object(webchain, "_chat",
                               lambda *a, **k: json.dumps(GOOD_BRIEF, ensure_ascii=False)):
            b = webchain.extract_brief("纸扎匠给纸人点睛", "shortdrama", mode="idea")
        self.assertEqual(b["topic"], GOOD_BRIEF["topic"], "只验不抛（内容来自桩）")
        with self.assertRaises(ValueError):
            webchain.extract_brief("纸扎匠给纸人点睛", "shortdrama")   # script 模式要 ≥40 字

    def _create(self, mode, **kw):
        with mock.patch.object(webchain, "extract_brief",
                               lambda *a, **k: dict(GOOD_BRIEF)):
            return webchain.create_project("一句创意或整段剧本" * 6, mode=mode,
                                           log=lambda *_: None, **kw)

    def test_idea_mode_does_not_write_script_artifact(self):
        """★★ **不许**写 scriptwriter 产物 —— 否则链会把点子当整部剧本。"""
        r = self._create("idea")
        root = self.root / r["pid"]
        self.assertFalse((root / "scriptwriter" / "scriptwriter_ep1.md").exists(),
                         "idea 模式绝不能写编剧产物")
        # 但要留下创意来源可追溯
        self.assertTrue((root / "_source-idea.md").exists())
        self.assertIn("用户输入", (root / "_source-idea.md").read_text(encoding="utf-8"))

    def test_script_mode_writes_script_artifact(self):
        r = self._create("script")
        root = self.root / r["pid"]
        self.assertTrue((root / "scriptwriter" / "scriptwriter_ep1.md").exists())
        self.assertTrue((root / "scriptwriter" / "_source-note.md").exists())

    def test_episodes_and_ratio_written_into_brief(self):
        r = self._create("idea", episodes=3, ratio="9:16")
        b = json.loads((self.root / r["pid"] / "brief.json").read_text(encoding="utf-8"))
        self.assertEqual(b["episodes"], 3)
        self.assertEqual(b["ratio"], "9:16")

    def test_episodes_default_untouched_when_zero(self):
        r = self._create("idea")            # 不传 episodes
        b = json.loads((self.root / r["pid"] / "brief.json").read_text(encoding="utf-8"))
        self.assertEqual(b["episodes"], 1, "不传就别覆盖 LLM 给的值")


class TestDevServerAdoption(_Base):
    """★★ 端口上**已有** dev server 时怎么办（**实测发现的 D6 缺口**）。

    真实场景：2024 上有上个会话遗留的 server，而状态文件对它一无所知。
    `/ok` **不带"服务哪个项目"的信息** → 只能靠 `role_fs_root.txt` 判断。
    判得出来是别人的 → **拒绝**（不偷偷杀别人的进程）；判不出来 → 也拒绝并提示 `force`。
    """

    def _bound_file(self, project: str):
        d = self.root / ".tmp"
        d.mkdir(parents=True, exist_ok=True)
        # ⚠️  在本测试里被 patch 成  → 路径不能再加 projects/
        (d / "role_fs_root.txt").write_text(str(self.root / project), encoding="utf-8")

    def test_bound_project_reads_file(self):
        self._bound_file("village-bees")
        self.assertEqual(webchain.bound_project(), "village-bees")

    def test_bound_project_empty_when_missing(self):
        self.assertEqual(webchain.bound_project(), "")

    def test_adopts_when_bound_matches(self):
        """端口上的 server 确实是本项目 → **接管**（补记状态）后复用。"""
        self._bound_file("want")
        with mock.patch.object(webchain, "probe_ok", lambda **k: True), \
             mock.patch.object(webchain, "_pid_on_port", lambda p: 999), \
             mock.patch.object(webchain.subprocess, "Popen") as popen:
            st = webchain.ensure_devserver("want", log=lambda *_: None)
        self.assertTrue(st["reused"])
        self.assertEqual(st["source"], "adopted")
        popen.assert_not_called()
        self.assertEqual(webchain.read_state()["pid"], "want")

    def test_refuses_when_bound_is_other_project(self):
        """★ 端口上服务的是**别的、还活着的**项目 → 拒绝，绝不偷杀。

        ⚠️ 必须让那个项目**真的存在**：绑定到一个不存在的项目会被判成"僵尸"
        并**接管**（那是另一条有意的取舍 —— 见 `test_takes_over_when_bound_project_deleted`
        与 `ensure_devserver` 的说明）。
        """
        live = self.root / "someone-else"          # `PROJECTS_DIR` 被 patch 成 self.root
        live.mkdir(parents=True)
        (live / "brief.json").write_text("{}", encoding="utf-8")
        self._bound_file("someone-else")
        with mock.patch.object(webchain, "probe_ok", lambda **k: True), \
             mock.patch.object(webchain, "_pid_on_port", lambda p: 4321), \
             mock.patch.object(webchain.subprocess, "run") as run_, \
             mock.patch.object(webchain.subprocess, "Popen") as popen:
            with self.assertRaises(RuntimeError) as c:
                webchain.ensure_devserver("mine", log=lambda *_: None)
        self.assertIn("拒绝接管", str(c.exception))
        self.assertIn("force=true", str(c.exception))
        run_.assert_not_called()       # 没杀任何进程
        popen.assert_not_called()      # 也没试着抢端口

    def test_refuses_when_bound_unknown(self):
        """判不出服务哪个项目 → 也拒绝（宁可让人决定，也不瞎猜）。"""
        with mock.patch.object(webchain, "probe_ok", lambda **k: True), \
             mock.patch.object(webchain, "_pid_on_port", lambda p: 4321), \
             mock.patch.object(webchain.subprocess, "Popen") as popen:
            with self.assertRaises(RuntimeError):
                webchain.ensure_devserver("mine", log=lambda *_: None)
        popen.assert_not_called()

    def test_uses_state_pid_when_bound_file_empty(self):
        """★ `role_fs_root.txt` 还没写时（刚起完 server），用**状态文件**兜底 ——
        否则 409 会报"(判不出来)"，人无从判断该不该 force。
        """
        live = self.root / "village-bees"
        live.mkdir(parents=True)
        (live / "brief.json").write_text("{}", encoding="utf-8")
        # 状态文件说端口上那个进程（4321）服务的是 village-bees；但没有 role_fs_root.txt
        webchain._write_state({"pid": "village-bees", "os_pid": 4321})
        with mock.patch.object(webchain, "probe_ok", lambda **k: True), \
             mock.patch.object(webchain, "_pid_on_port", lambda p: 4321), \
             mock.patch.object(webchain, "bound_project", lambda: ""), \
             mock.patch.object(webchain.subprocess, "run") as run_, \
             mock.patch.object(webchain.subprocess, "Popen") as popen:
            with self.assertRaises(RuntimeError) as c:
                webchain.ensure_devserver("mine", log=lambda *_: None)
        self.assertIn("village-bees", str(c.exception), "消息里要点出**是哪个项目**占着")
        run_.assert_not_called()

    def test_force_kills_and_restarts(self):
        """`force=true` → 杀掉端口上的进程后重启。"""
        self._bound_file("someone-else")
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(webchain, "probe_ok", lambda **k: True), \
             mock.patch.object(webchain, "_pid_on_port", lambda p: 4321), \
             mock.patch.object(webchain.subprocess, "run", fake_run), \
             mock.patch.object(webchain.subprocess, "Popen", _FakeProc), \
             mock.patch.object(webchain.time, "sleep", lambda s: None), \
             mock.patch.object(webchain, "archive_api_dir", lambda log=None: ""):
            st = webchain.ensure_devserver("mine", log=lambda *_: None, force=True)
        self.assertFalse(st["reused"])
        self.assertTrue(any("/T" in c and "4321" in c for c in calls),
                        "force 时必须杀掉端口上的进程（带 /T）")

    def test_takes_over_when_bound_project_deleted(self):
        """★★ 绑定的项目**已不存在** → 那是僵尸，**可以安全接管**。

        不补这条会把端口永久占死：僵尸服务着一个被删掉的项目，
        而"拒绝接管"逻辑会让**之后每一个项目**都撞 409，人只能手工 taskkill。
        （这是我在 CDP 调试里亲手制造的局面。）
        """
        self._bound_file("a-project-i-just-deleted")     # 没有 brief.json = 已不存在
        killed = []

        def fake_run(cmd, **kw):
            killed.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(webchain, "probe_ok", lambda **k: True), \
             mock.patch.object(webchain, "_pid_on_port", lambda p: 5555), \
             mock.patch.object(webchain.subprocess, "run", fake_run), \
             mock.patch.object(webchain.subprocess, "Popen", _FakeProc), \
             mock.patch.object(webchain.time, "sleep", lambda s: None), \
             mock.patch.object(webchain, "archive_api_dir", lambda log=None: ""):
            st = webchain.ensure_devserver("brand-new", log=lambda *_: None)
        self.assertFalse(st["reused"])
        self.assertTrue(any("/T" in c and "5555" in c for c in killed),
                        "僵尸必须被清掉（带 /T）")

    def test_does_not_take_over_live_other_project(self):
        """对照：绑定的项目**仍在盘上** → 不接管（那是别人的活项目）。"""
        (self.root / "other-live").mkdir(parents=True)
        (self.root / "other-live" / "brief.json").write_text("{}", encoding="utf-8")
        self._bound_file("other-live")
        with mock.patch.object(webchain, "probe_ok", lambda **k: True), \
             mock.patch.object(webchain, "_pid_on_port", lambda p: 5555), \
             mock.patch.object(webchain.subprocess, "run") as run_, \
             mock.patch.object(webchain.subprocess, "Popen") as popen:
            with self.assertRaises(RuntimeError):
                webchain.ensure_devserver("mine", log=lambda *_: None)
        run_.assert_not_called()

    def test_health_path_free_port_starts(self):
        """端口空着（`/ok` 不通）→ 正常启动，不走接管分支。"""
        with mock.patch.object(webchain, "probe_ok", lambda **k: False), \
             mock.patch.object(webchain.subprocess, "Popen", _FakeProc), \
             mock.patch.object(webchain, "archive_api_dir", lambda log=None: ""):
            # probe_ok 恒 False → 会超时；用极小 wait_s 验证"走了启动分支"
            with self.assertRaises(RuntimeError) as c:
                webchain.ensure_devserver("fresh", log=lambda *_: None, wait_s=0.2)
        self.assertIn("未就绪", str(c.exception))


# ════════════════════════════════ 链执行

class TestExecChain(_Base):
    def _mk_project(self, pid="chainproj"):
        root = self.root / pid
        (root / "brief.json").parent.mkdir(parents=True, exist_ok=True)
        (root / "brief.json").write_text(json.dumps({"topic": "x"}), encoding="utf-8")
        # 驱动脚本要在 `PROJECT_ROOT` 下存在（`_exec_chain` 会先查它）
        drv = self.root / "scripts" / "drive_chain.py"
        drv.parent.mkdir(parents=True, exist_ok=True)
        drv.write_text("# stub", encoding="utf-8")
        return root

    def _artifacts(self, root, roles):
        for role in roles:
            rel = guards.out_path(role, 1)
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# x", encoding="utf-8")

    def test_fails_when_devserver_not_serving_project(self):
        """前置条件不满足 → **抛**（由 `runner.execute()` 统一记成 `status=failed`）。"""
        root = self._mk_project()
        with mock.patch.object(webchain, "devserver_status",
                               lambda: {"alive": False, "ok": False}):
            with self.assertRaises(RuntimeError) as c:
                runner._exec_chain(root, 1, [], lambda *_: None)
        self.assertIn("dev server", str(c.exception))
        self.assertIn("D6", str(c.exception))

    def test_ok_when_all_gate_roles_present(self):
        root = self._mk_project()
        self._artifacts(root, guards.GATE_ROLES)
        with mock.patch.object(webchain, "devserver_status",
                               lambda: {"alive": True, "ok": True}), \
             mock.patch.object(runner.subprocess, "run",
                               lambda *a, **k: mock.Mock(returncode=0)):
            r = runner._exec_chain(root, 1, [], lambda *_: None)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["stage"], "chain")

    def test_missing_role_reported_and_director_excluded(self):
        """★★ 产物核对**必须用 GATE_ROLES（7 个）**。

        用 `ROLES`（8 个，含 `director`）会**永远**报告缺 director —— supervisor 架构里
        `director` 就是 supervisor 本身、不产出角色产物（本项目已因此白跑两轮的既有事故）。
        """
        root = self._mk_project()
        self._artifacts(root, [r for r in guards.GATE_ROLES if r != "reviewer"])
        with mock.patch.object(webchain, "devserver_status",
                               lambda: {"alive": True, "ok": True}), \
             mock.patch.object(runner.subprocess, "run",
                               lambda *a, **k: mock.Mock(returncode=0)):
            r = runner._exec_chain(root, 1, [], lambda *_: None)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["missing_roles"], ["reviewer"])
        self.assertNotIn("director", r["missing_roles"], "director 不该出现在缺失清单里")

    def test_nothing_produced_lists_all_seven(self):
        root = self._mk_project()
        with mock.patch.object(webchain, "devserver_status",
                               lambda: {"alive": True, "ok": True}), \
             mock.patch.object(runner.subprocess, "run",
                               lambda *a, **k: mock.Mock(returncode=1)):
            r = runner._exec_chain(root, 1, [], lambda *_: None)
        self.assertEqual(sorted(r["missing_roles"]), sorted(guards.GATE_ROLES))
        self.assertEqual(len(r["missing_roles"]), 7, "恰好 7 个（不含 director）")

    def test_chain_kind_is_registered(self):
        self.assertIn("chain", runner.KINDS)
        self.assertIn("chain", runner._EXEC)

    def test_chain_needs_no_shots(self):
        """`chain` 不吃 shots —— 空 shots 不该被"整片渲染"那条护栏误伤。"""
        root = self._mk_project()
        shots = runner._validate(root.name, "chain", [])
        self.assertEqual(shots, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
