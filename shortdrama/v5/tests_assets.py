# -*- coding: utf-8 -*-
"""资产层自测（纯逻辑 + 临时目录，不调 API）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from v5.media import assets  # noqa: E402


def _mk(root: Path, name: str, w: int = 64, h: int = 64, pad: int = 0) -> None:
    """造满足视频尺寸下限、像素内容可区分的 PNG（不依赖 PIL）。

    pad 保留调用契约，但用于像素颜色；仅靠尾部填充区分的 1x1 同色图
    经输入尺寸规范化后会完全相同，无法验证多资产绑定。
    """
    import struct,zlib
    w,h=max(256,w),max(256,h)
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    pixel=bytes((pad%256,(pad//256)%256,128))
    png=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))
         +chunk(b'IDAT',zlib.compress((b'\x00'+pixel*w)*h))+chunk(b'IEND',b''))
    (root / "images").mkdir(parents=True, exist_ok=True)
    (root / "images" / name).write_bytes(png)


class TestAssets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _mk(self.root, "主角.front.png", pad=1)
        _mk(self.root, "木牌.png", pad=2)
        (self.root / "assets.json").write_text(
            '{"assets": ['
            '{"id":"prot","name":"主角","type":"character","keywords":["主角","纸扎匠"],'
            '"priority":10,"public_url":"","url":"","ref_image":"主角.front.png"},'
            '{"id":"plaque","name":"祖训木牌","type":"prop","keywords":["木牌"],'
            '"priority":5,"public_url":"","url":"","ref_image":"木牌.png"}'
            ']}', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_bind_by_keyword(self):
        shots = [{"name": "LN01", "visual": "纸扎匠低头看着木牌", "dialogue": ""}]
        b = assets.bind(self.root, shots)
        self.assertIn("LN01", b)
        self.assertEqual(len(b["LN01"]), 2)          # 角色 + 道具
        self.assertTrue(all(u.startswith("data:") for u in b["LN01"]))

    def test_character_first(self):
        shots = [{"name": "LN01", "visual": "木牌与纸扎匠", "dialogue": ""}]
        b = assets.bind(self.root, shots)
        self.assertEqual(len(b["LN01"]), 2)

    def test_protagonist_fallback(self):
        """主角没被关键词命中（agent 常换叫法）→ 强制绑主角肖像。"""
        shots = [{"name": "LN01", "visual": "他低头盘账，手在抖", "dialogue": ""}]
        b = assets.bind(self.root, shots)
        self.assertIn("LN01", b)
        self.assertTrue(len(b["LN01"]) >= 1)

    def test_public_url_passthrough(self):
        """已登记的 public URL 直接透传（不内联成 data URI）。"""
        reg = {"assets": [{"id": "x", "name": "X", "type": "prop", "keywords": ["X"],
                           "public_url": "https://example.com/x.png", "priority": 8}]}
        (self.root / "assets.json").write_text(
            __import__("json").dumps(reg, ensure_ascii=False), encoding="utf-8")
        shots = [{"name": "LN01", "visual": "画面里有 X", "dialogue": ""}]
        b = assets.bind(self.root, shots)
        self.assertIn("https://example.com/x.png", b["LN01"])

    def test_no_match_no_refs(self):
        """关键词不命中且**无角色资产** → 不绑（避免误导模型）。

        注意：images/ 里的图会被 auto_sync 自动登记，所以这里必须清空 images/，
        否则主角兜底会照常绑上（那是期望行为，见 test_protagonist_fallback）。
        """
        for f in (self.root / "images").iterdir():
            f.unlink()
        (self.root / "assets.json").write_text(
            '{"assets":[{"id":"p","name":"木牌","type":"prop","keywords":["木牌"],'
            '"public_url":"","url":"","ref_image":"木牌.png","priority":5}]}',
            encoding="utf-8")
        shots = [{"name": "LN01", "visual": "空镜头：风吹过街道", "dialogue": ""}]
        b = assets.bind(self.root, shots)
        self.assertNotIn("LN01", b)


class TestSceneAnchor(unittest.TestCase):
    """★ 「场景」列接线（2026-09-14 补的断线）。

    实测事故（paper-crane）：分镜 41/49 镜的「场景」列写着「洗衣店内部」，
    assetdesigner 也登记了 location 资产（描述完全正确），cast 还花配额生成了
    4 张场景参考图 —— 但**一道都没接上**：场景列写的是裸名（无 `@`）→ 走不了精确
    匹配；keywords 兜底只在 `hits` 为空时跑，而每镜都有 `@角色`/`@道具` → 兜底也不跑；
    提示词里也没有场景段。→ 场景锚点彻底丢失，成片漂成暖光室内。

    这组用例锁死三根线：**提示词锚点 / 参考图绑定 / 描述可用性（去空镜措辞 + 限长）**。
    """

    REG = ('{"assets": ['
           '{"id":"loc","name":"洗衣店内部","type":"location",'
           '"keywords":["洗衣店","自助洗衣店"],'
           '"prompt":"自助洗衣店内景空镜头，冷白荧光灯偏青冷色调，天花板成排筒灯，'
           '一侧洗衣机与烘干机阵列，整体空店氛围"},'
           '{"id":"p","name":"周平","type":"character","keywords":["周平"],'
           '"ref_image":"周平.png"}]}')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "assets.json").write_text(self.REG, encoding="utf-8")

    def test_scene_lines_injects_location_description(self):
        out = assets.scene_lines(self.root, [{"name": "LN01", "scene": "洗衣店内部"}])
        self.assertIn("LN01", out)
        self.assertIn("洗衣店内部", out["LN01"])
        self.assertIn("冷白荧光灯偏青冷色调", out["LN01"], "必须带上描述才有锚点价值")

    def test_scene_lines_strips_empty_shot_wording(self):
        """描述是按"空镜参考图"写的，注进**有人物**的镜里会诱导画成空店。"""
        out = assets.scene_lines(self.root, [{"name": "LN01", "scene": "洗衣店内部"}])
        for w in ("空镜头", "空镜", "空店"):
            self.assertNotIn(w, out["LN01"], "空镜措辞必须剥掉：%s" % w)

    def test_scene_lines_caps_length(self):
        """这段会进每一镜的提示词 —— 必须限长（LEAN 的价值就是短）。"""
        out = assets.scene_lines(self.root, [{"name": "LN01", "scene": "洗衣店内部"}])
        body = out["LN01"].split("：", 1)[-1]
        self.assertLessEqual(len(body), assets.SCENE_ANCHOR_MAX)
        self.assertTrue(body, "不能整段被截空")

    def test_scene_lines_falls_back_to_keywords(self):
        """分镜写的场景名与资产名不完全相等时，走 keywords 子串兜底。"""
        out = assets.scene_lines(self.root, [{"name": "LN01", "scene": "洗衣店内景"}])
        self.assertIn("LN01", out)

    def test_unknown_scene_is_not_injected(self):
        """宁可这一镜没有锚点，也不注入一个错的场景。"""
        out = assets.scene_lines(self.root, [{"name": "LN01", "scene": "便利店"}])
        self.assertEqual(out, {})

    def test_description_falls_back_to_assetdesigner_contract(self):
        """★ 真实数据教训：注册表的 location 条目**没有描述**，描述只在
        `assetdesigner/assets.contract.json` 的 `prompt` 里。

        实测（paper-crane）第一版只读注册表 → 命中 **0/49**；
        而注册表与契约**同名条目的合并**也要小心（早期用 `seen` 去重，
        契约里那条被整条跳过，等于白修）。
        """
        # 注册表：有 location 但**无 prompt**
        (self.root / "assets.json").write_text(
            '{"assets": [{"id":"loc","name":"洗衣店内部","type":"location",'
            '"keywords":["洗衣店"],"ref_image":"x.png"}]}', encoding="utf-8")
        (self.root / "assetdesigner").mkdir(parents=True, exist_ok=True)
        (self.root / "assetdesigner" / "assets.contract.json").write_text(
            '{"assets": [{"name":"洗衣店内部","type":"location",'
            '"keywords":["洗衣店"],'
            '"prompt":"自助洗衣店内景空镜头，冷白荧光灯偏青冷色调，成排筒灯"}]}',
            encoding="utf-8")
        out = assets.scene_lines(self.root, [{"name": "LN01", "scene": "洗衣店内部"}])
        self.assertIn("LN01", out, "描述在契约里也必须能取到")
        self.assertIn("冷白荧光灯", out["LN01"])

    def test_scene_is_text_anchor_not_image_ref(self):
        """★ 场景走**文本锚点**，**不进图片参考** —— 锁死这个决定。

        为什么不做图片绑定（我一度以为那才是修复，被既有设计挡回来了）：
          场景参考图是**空镜内景**，自带机位与景别。喂进"近景 @周平 手部特写"
          这类镜，等于同时告诉模型"用一个空荡的全景"，与分镜要求的景别打架
          （`qc.review_shot_type` 会把这类判成景别不符）。
        → 场景的作用由 `scene_lines()` + `prompt.scene_line()` 的**描述文本**承担：
          **信息（光线/色温/陈设）进来了，机位没进来。**
        """
        reg = json.loads(self.REG)
        shot = {"name": "LN01", "scene": "洗衣店内部", "visual": "@周平站在烘干机前"}
        hits, _un = assets.hits_for_shot(reg, shot)
        self.assertNotIn("洗衣店内部", [h.get("name") for h in hits],
                         "场景不进图片参考（避免空镜机位覆盖分镜构图）")
        # 但文本锚点必须给到 —— 那才是场景信息的入口
        anchor = assets.scene_lines(self.root, [shot])
        self.assertIn("洗衣店内部", anchor.get("LN01", ""))

    def test_scene_column_does_not_leak_into_image_refs(self):
        """场景列不得让它绑上图片；角色仍照常绑。"""
        reg = json.loads(self.REG)
        hits, _un = assets.hits_for_shot(
            reg, {"name": "LN01", "scene": "洗衣店内部", "visual": "@周平"})
        names = [h.get("name") for h in hits]
        self.assertIn("周平", names, "角色照常绑")
        self.assertNotIn("洗衣店内部", names)

    def test_wide_shot_binds_scene_column_without_at_mention(self):
        """★ 宽景镜**无条件**绑「场景」列（2026-09-28：把确定性事实从契约挪进代码）。

        旧行为：`hits_for_shot` 只在「@ 一个都没命中」时才回退 keywords 匹配 ⇒
        只要本镜 @ 了角色，「场景」列里的名字**永不参与匹配**，宽景镜一张场景图都
        绑不上、场景自由发挥而**日志全绿**（v2 实测："云海双塔"画成地面庭院）。
        上一版对策是要求分镜写 `@场景名`，代价是 v4 连跑四轮为这一条被阻断/返工，
        其中一轮 reviewer 还**编造**了阻断理由（实测 9 个宽景镜全部已写 @场景名）。
        ⇒ 「场景」列是分镜契约必填列，直接按它绑，不再依赖模型自觉。
        中近景仍不绑（场景空镜自带机位，会把构图拉回大 Wide）——那条政策没变。
        """
        reg = json.loads(self.REG)
        wide = {"name": "LN01", "scene": "洗衣店内部", "shot_type": "全景",
                "visual": "@周平 抬头看向吊扇"}
        names = [h.get("name") for h in assets.hits_for_shot(reg, wide)[0]]
        self.assertIn("洗衣店内部", names,
                      "宽景镜即使只 @ 了角色，也必须绑上「场景」列的 location")
        self.assertIn("周平", names, "角色不能被场景挤掉")
        near = dict(wide, name="LN02", shot_type="近景")
        names2 = [h.get("name") for h in assets.hits_for_shot(reg, near)[0]]
        self.assertNotIn("洗衣店内部", names2,
                         "近景仍不绑场景图（2026-09-23 收窄的政策不许被这次改动带回去）")


class TestCharFallback(unittest.TestCase):
    """`@` 只命中场景/道具时，未 `@` 的角色仍要绑对脸（2026-09-13）。

    事故：`hits_for_shot` 的 `@` 精确匹配**只要有命中**（哪怕命中的是场景或
    道具）就会跳过关键词兜底 —— 于是"没被 @ 的角色"彻底丢失，随后 `bind()`
    的主角兜底拿主角去顶替它。实测 noodle-night 12 镜里 4 镜
    （LN02/07/09/11）绑错脸：镜里是「女孩」，绑的是「老陈」的参考图。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _mk(self.root, "老陈.png", pad=1)
        _mk(self.root, "女孩.png", pad=2)
        _mk(self.root, "店面.png", pad=3)
        (self.root / "assets.json").write_text(
            '{"assets": ['
            '{"id":"a","name":"老陈","type":"character","keywords":["老陈"],'
            '"public_url":"","url":"","ref_image":"老陈.png","priority":8},'
            '{"id":"b","name":"女孩","type":"character","keywords":["女孩"],'
            '"public_url":"","url":"","ref_image":"女孩.png","priority":8},'
            '{"id":"c","name":"店面","type":"location","keywords":["店面"],'
            '"public_url":"","url":"","ref_image":"店面.png","priority":8}'
            ']}', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _chars(self, shot):
        reg = assets.auto_sync(self.root)
        hits, _ = assets.hits_for_shot(reg, shot)
        return [h["name"] for h in hits if h.get("type") == "character"]

    def test_unmentioned_char_hits_by_name(self):
        """只 @ 了场景、角色没 @ → 仍要命中该角色（不能丢给主角兜底）。"""
        self.assertEqual(
            self._chars({"name": "LN01", "visual": "女孩站在@店面门口",
                         "dialogue": "女孩：还能吃吗？"}),
            ["女孩"])

    def test_generic_pronoun_does_not_hit(self):
        """「她/他」不该让任何角色命中（旧 keyword 表含这些泛词）。"""
        self.assertEqual(
            self._chars({"name": "LN02", "visual": "她低头整理课本", "dialogue": ""}),
            [])

    def test_ordering_matches_keyword_fallback(self):
        """排序与 `hits_for_text` 一致（priority 降序）—— 两条路径不得给出不同答案。

        双人镜里 `bind()` 只留 `chars[:1]`，所以顺序即"绑谁的脸"。二者同 priority
        时保持注册表序（主角在前的约定）。
        """
        self.assertEqual(
            self._chars({"name": "LN03", "visual": "女孩抬头看向老陈", "dialogue": ""}),
            ["老陈", "女孩"],
            "同 priority 时应与 hits_for_text 同序（注册表序），而不是按文本出现顺序")
        # 两边都没 @ 也要按 priority：给「女孩」提权后它才该排前
        reg = assets.auto_sync(self.root)
        for a in reg["assets"]:
            if a["name"] == "女孩":
                a["priority"] = 99
        hits, _ = assets.hits_for_shot(
            reg, {"name": "LN04", "visual": "女孩抬头看向老陈", "dialogue": ""})
        self.assertEqual([h["name"] for h in hits if h["type"] == "character"],
                         ["女孩", "老陈"], "priority 高的角色排前")

    def test_join_note_does_not_decide_who(self):
        """`join_note` 里的角色名属于**上一镜**，不能决定本镜绑谁的脸。

        实测 noodle-night LN09：join_note 写「承接上一镜老陈擦灶台的落点」，
        而本镜其实是「女孩」—— 曾因此绑成老陈。判定本镜人物时排除 join_note。
        """
        self.assertEqual(
            self._chars({"name": "LN09", "visual": "女孩起身收拾",
                         "dialogue": "女孩：走了。",
                         "join_note": "承接上一镜老陈擦灶台的落点"}),
            ["女孩"])

    def test_two_characters_bind_both_faces(self):
        """双人镜必须绑**两个**角色的参考图（2026-09-13 改，对齐官方示例）。

        旧规则 `chars[:1]` 只绑一张 → 第二个角色对模型"不存在"。
        实测代价（rainy-door LN02）：分镜要求「周奶奶从楼梯口走下并站定」，
        画面里没有她，被 clipqc 判「缺少本镜应有的人物」。

        官方示例（2026-09-13 核实）：**每个镜头都传 2 个角色的图**，正文用
        `@林小夏-基础形象` / `@李晓雅-基础形象` 指代，同屏两人画得正确。

        注意与旧 A/B 的区别：那次证的是「**单人镜**喂 3 张会多画一个人」，
        不是"多给人物图不行" —— 图的数量超过分镜要求的人数才会多画。
        """
        reg = assets.auto_sync(self.root)
        by = {a["name"]: a for a in reg["assets"]}
        chen = assets._safe_ref_urls(by["老陈"], self.root)
        girl = assets._safe_ref_urls(by["女孩"], self.root)
        self.assertTrue(chen and girl, "测试前置：两张参考图都应可解析")
        b = assets.bind(self.root, [{"name": "LN01",
                                     "visual": "女孩抬头看向老陈", "dialogue": ""}])
        urls = b.get("LN01") or []
        self.assertIn(chen[0], urls, "双人镜应绑老陈的图")
        self.assertIn(girl[0], urls, "双人镜应绑女孩的图")

    def test_single_character_shot_still_caps_at_two(self):
        """单人镜仍封顶 2（旧 A/B：1 人物 + 2 道具 = 3 张 → 多画 1 个人）。

        新规则"双人镜给 3 张"不能外溢到单人镜 —— 否则重回旧事故。
        """
        shots = [{"name": "LN02", "visual": "女孩站在@店面门口", "dialogue": ""}]
        b = assets.bind(self.root, shots)
        self.assertLessEqual(len(b.get("LN02") or []), 2, "单人镜不该超过 2 张")

    def test_bind_picks_right_face(self):
        """端到端：`bind()` 必须绑「女孩」的图，而不是主角「老陈」的。"""
        reg = assets.auto_sync(self.root)
        by = {a["name"]: a for a in reg["assets"]}
        girl = assets._safe_ref_urls(by["女孩"], self.root)
        chen = assets._safe_ref_urls(by["老陈"], self.root)
        self.assertTrue(girl and chen, "测试前置：两张参考图都应可解析")
        b = assets.bind(self.root, [{"name": "LN01",
                                     "visual": "女孩站在@店面门口", "dialogue": ""}])
        urls = b.get("LN01") or []
        self.assertIn(girl[0], urls, "应绑女孩的参考图")
        self.assertNotIn(chen[0], urls, "不该绑主角老陈的参考图")


class TestSceneAnchorFallback(unittest.TestCase):
    """「场景」列缺失时的**画面描述兜底** + 失败告警（2026-09-14）。

    用户报告："经常切镜之后，场景就变了"。定位结果：牛来包 / 3D 包的 scenedesigner
    契约历史上只有 8 列、**没有「场景」列** → 精确匹配与 keywords 兜底都无从谈起
    → 锚点恒为 0 → 每镜的光源/陈设由模型自由发挥。
    实测 dawn-broadcast 的存量分镜：兜底把 **0/10 镜救回 9/10 镜**，且逐镜归属正确。

    判据是**打分取最强者**而不是"唯一命中" —— 场景关键词里混着通用环境元素
    （"土黄墙"会同时出现在两个场景的描述里），"唯一命中"会把 7/10 镜误判成歧义。
    """

    REG = ('{"assets": ['
           '{"id":"a","name":"村口电线杆","type":"location",'
           '"keywords":["村口电线杆","村口","电线杆","大喇叭"],'
           '"prompt":"乡镇村口，灰蓝黎明前的天空，灰白水泥电线杆上挂着铁锈色大喇叭"},'
           '{"id":"b","name":"老赵家屋前","type":"location",'
           '"keywords":["老赵家屋前","老赵家","土黄墙","灰木门"],'
           '"prompt":"一户农家屋前，土黄墙与灰黑木门，深灰水泥地面"},'
           '{"id":"p","name":"老赵","type":"character","keywords":["老赵"]}]}')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "assets.json").write_text(self.REG, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_falls_back_to_visual_description(self):
        """无「场景」列 → 从画面描述认场景（存量分镜不必重跑创作链）。"""
        shots = [{"name": "LN01",
                  "visual": "灰蓝色黎明前天空，村口广场空无一人。灰白水泥电线杆竖立画面正中，"
                            "杆顶横担下挂着一只铁锈色大喇叭"}]
        out = assets.scene_lines(self.root, shots)
        self.assertIn("LN01", out)
        self.assertIn("村口电线杆", out["LN01"])

    def test_picks_strongest_not_ambiguous(self):
        """两场景的关键词都有命中时，取**更强者**、不判歧义（实测教训）。"""
        shots = [{"name": "LN02",
                  "visual": "老赵家屋前，土黄色墙面，一扇灰旧木门半开。远处村口的水泥电线杆"}]
        out = assets.scene_lines(self.root, shots)
        # 「老赵家屋前」整串出现(2) + "老赵家"(1) = 3；「村口电线杆」只命中关键词 = 2
        self.assertIn("老赵家屋前", out["LN02"])

    def test_tie_is_skipped_and_warned(self):
        """**并列最强**才判歧义 —— 宁可这一镜没有锚点，也不注入一个错的。"""
        (self.root / "assets.json").write_text(
            '{"assets":['
            '{"id":"a","name":"甲场景","type":"location","keywords":["甲场景"],'
            '"prompt":"甲场景的室内陈设与冷白光，桌面与墙面可见"},'
            '{"id":"b","name":"乙场景","type":"location","keywords":["乙场景"],'
            '"prompt":"乙场景的室内陈设与暖黄光，窗边与地面可见"}]}',
            encoding="utf-8")
        got: list = []
        out = assets.scene_lines(self.root, [{"name": "LN01",
                                              "visual": "镜头从甲场景切到乙场景"}],
                                 log=got.append)
        self.assertNotIn("LN01", out)
        self.assertTrue(any("歧义" in m for m in got), "歧义必须进告警：%r" % got)

    def test_weak_single_keyword_is_not_enough(self):
        """单个通用词不足以定场景（门槛见 `assets._SCENE_MIN_SCORE`）。"""
        shots = [{"name": "LN01", "visual": "镜头停在土黄墙的一角，光线昏暗"}]
        out = assets.scene_lines(self.root, shots)
        self.assertNotIn("LN01", out)

    def test_warns_when_no_location_in_registry(self):
        """注册表没有 location → **必须告警**（原先完全静默，只剩"切镜换场景"这个末端症状）。"""
        (self.root / "assets.json").write_text(
            '{"assets":[{"id":"p","name":"老赵","type":"character","keywords":["老赵"]}]}',
            encoding="utf-8")
        got: list = []
        out = assets.scene_lines(self.root, [{"name": "LN01", "visual": "村口"}],
                                 log=got.append)
        self.assertEqual(out, {})
        self.assertTrue(any("location" in m for m in got),
                        "必须提示注册表缺 location：%r" % got)


class TestSceneAnchorKeepsKeySegments(unittest.TestCase):
    """场景锚点必须保住「光源 / 色温 / 陈设」（2026-09-15 实测：场景逐镜漂移）。

    事故（village-tree）：旧实现把 **302–369 字**的场景描述**从头截断到 80 字**，
    而源描述的结构是「布局 + `**光源**：…` + `**色温**：…` + `**陈设**：…`」→
    **后三段整段被砍掉**，注入提示词的只剩"布局/色块"（实测每个场景丢 ~80%）。
    对照同片的**人物身份锚点 722 字、无截断** → 场景与身份的信息投入差 **8 倍**；
    用户反馈正是「人物一致性已经可以……就是场景变来变去的」。
    修法：按**语义段**挑选（光源 → 色温 → 陈设 → 布局），而不再从头截断。
    """

    DESC = ("村口只有一棵老槐树（粗糙多边形树干、树冠由2至3个粗叶块复用、向南歪斜三十度、"
            "树皮深褐开裂），树下是一小块不规则空地，地面是平涂田土色块加几道平涂裂缝，"
            "空地下方有一条窄村道往南延伸，背景是稀疏村庄体量。"
            "**光源**：一个清早顶面偏前的单一硬方向光（太阳低角、来自画面侧前上方），"
            "加极弱环境补光，硬低分辨率阴影、平脸局部过曝。"
            "**色温**：暖色晨光，整体略过曝、不过度去饱和，天空平涂灰白带一点暖。"
            "**陈设**：老槐树为画面主锚点居中，树下空地与往南窄村道为主要陈设。")

    def test_key_segments_survive(self):
        from v5.media import assets as assets_mod

        out = assets_mod._scene_anchor_text(self.DESC)
        for k in ("光源", "色温", "陈设"):
            self.assertIn(k, out, "丢了跨镜一致性的载体「%s」：\n%s" % (k, out))
        self.assertIn("暖色晨光", out, "色温的内容要真的进来")

    def test_old_head_truncation_would_have_lost_them(self):
        """反证：旧的"从头截断 80 字"确实拿不到光源 —— 防止改回去。"""
        self.assertNotIn("光源", self.DESC[:80])

    def test_within_budget(self):
        from v5.media import assets as assets_mod

        self.assertLessEqual(len(assets_mod._scene_anchor_text(self.DESC)),
                             assets_mod.SCENE_ANCHOR_MAX + 40)

    def test_legacy_unlabeled_desc_falls_back(self):
        """旧产物没有 `**光源**：` 标签 → 回落简单截断（行为与历史一致）。"""
        from v5.media import assets as assets_mod

        legacy = "砖墙收粮站内景，一盏吊灯硬阴影，水泥地，麻袋堆在墙角。" * 8
        out = assets_mod._scene_anchor_text(legacy)
        self.assertTrue(out)
        self.assertLessEqual(len(out), assets_mod.SCENE_ANCHOR_MAX)


class TestCharNameInsideAssetName(unittest.TestCase):
    """★ 场所/道具名里含角色名时，不该把那个角色算作在场（2026-09-15 实测事故）。

    事故（village-bees LN01）：画面描述 `@阿凯站在@老周家院的青砖院中央` ——
    **场所名 `老周家院` 里含角色名 `老周`**，而 `_chars_by_name` 用的是**裸子串**判据
    → 认为"老周也在场" → 老周的照片也被绑进这一镜（人物图上限 2）
    → `<Picture 1>` 落到老周 → **静帧把主角画成了老周**（分镜要的是阿凯）。
    乡土题材里 `老周家院` / `阿凯家院` 这类命名极其自然 ——
    **不能靠"名字别这么起"规避，必须改判据。**
    """

    REG = {"assets": [
        {"name": "老周", "type": "character", "keywords": ["老周"], "priority": 10},
        {"name": "阿凯", "type": "character", "keywords": ["阿凯"], "priority": 10},
        {"name": "老周家院", "type": "location", "keywords": ["老周家院"], "priority": 8},
        {"name": "蜂箱", "type": "prop", "keywords": ["蜂箱"], "priority": 8},
    ]}

    def _names(self, text, reg=None):
        from v5.media import assets as assets_mod

        return [a["name"] for a in assets_mod._chars_by_name(reg or self.REG, text)]

    def test_name_only_inside_location_not_matched(self):
        self.assertEqual(self._names("@阿凯站在@老周家院的青砖院中央，喘着气"), ["阿凯"])

    def test_name_also_standalone_still_matched(self):
        """老周**另有一处独立出现**时必须照常命中 —— 别把功能一并砍掉。"""
        got = self._names("@阿凯站在@老周家院的院中央；@老周披着外套出来")
        self.assertIn("老周", got)
        self.assertIn("阿凯", got)

    def test_prop_name_containing_char_name(self):
        reg = {"assets": self.REG["assets"] + [
            {"name": "阿凯的蜂箱", "type": "prop", "keywords": ["蜂箱"], "priority": 8}]}
        self.assertEqual(self._names("@阿凯的蜂箱摆在三排木箱上", reg), [],
                         "只出现在道具名里 → 不该判为角色在场")

    def test_real_project_regression(self):
        """真实产物回归：village-bees 的 LN01 只该命中阿凯。"""
        import json
        from pathlib import Path

        from v5.media import assets as assets_mod, storyboard

        d = Path(__file__).resolve().parents[1] / "projects" / "village-bees"
        sb = d / "scenedesigner" / "scenedesigner.md"
        regf = d / "assets.json"
        if not (sb.exists() and regf.exists()):
            self.skipTest("village-bees 产物不在盘上")
        reg = json.loads(regf.read_text(encoding="utf-8"))
        ln01 = [s for s in storyboard.parse(sb.read_text(encoding="utf-8"))
                if s["name"] == "LN01"][0]
        got = [a["name"] for a in assets_mod._chars_by_name(
            reg, assets_mod._char_text(ln01))]
        self.assertEqual(got, ["阿凯"], "LN01 是阿凯的镜，不该命中老周：%s" % got)


class TestIdentityAnchorSkippedWithSourcePhoto(unittest.TestCase):
    """有源照片时**不再注入**文字身份锚点（2026-09-15 实测）。

    证据（用户反馈「差太远了」）：同一张照片、同一镜，提示词 **545 字 → 75 字**
    （只留"以照片为准 + 机位景别 + 画面内容"）之后，出图相似度**大幅提升** ——
    545 字里最大的一块正是**每个角色约 220 字的外貌描述**，里面还带着
    「面部棱角清楚、面部线条硬朗」这类**与照片打架**的形容词。
    ⇒ 有照片时那段文字是**净损失**。
    """

    def _root(self, with_photo: bool = True):
        import json
        import tempfile
        from pathlib import Path

        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        (root / "images").mkdir(parents=True, exist_ok=True)
        if with_photo:
            (root / "images" / "老周.source.jpg").write_bytes(b"x")
        reg = {"assets": [
            {"id": "老周", "name": "老周", "type": "character", "keywords": ["老周"],
             "priority": 10,
             "identity": "老周的固定形象（全片每镜必须完全一致）：瘦长脸、浓眉。"},
            {"id": "小林", "name": "小林", "type": "character", "keywords": ["小林"],
             "priority": 10,
             "identity": "小林的固定形象（全片每镜必须完全一致）：圆脸。"},
        ]}
        (root / "assets.json").write_text(
            json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return d, root

    SHOTS = [{"name": "LN01", "visual": "@老周站在院中", "dialogue": ""}]

    def test_anchor_dropped_for_photo_bound_character(self):
        from v5.media import assets as assets_mod

        d, root = self._root(True)
        with d:
            got = assets_mod.identity_lines(root, self.SHOTS).get("LN01", "")
            self.assertNotIn("老周的固定形象", got, "有源照片 → 不该再注入文字锚点：%r" % got)

    def test_switch_restores_anchor(self):
        from unittest import mock

        from v5 import config
        from v5.media import assets as assets_mod

        d, root = self._root(True)
        with d, mock.patch.object(config, "KEEP_IDENTITY_WITH_PHOTO", True):
            got = assets_mod.identity_lines(root, self.SHOTS).get("LN01", "")
            self.assertIn("老周的固定形象", got, "开关打开时应恢复注入：%r" % got)

    def test_no_photo_keeps_anchor(self):
        """没有源照片 → 行为与历史**一字不变**（牛来包与无照片项目零影响）。"""
        from v5.media import assets as assets_mod

        d, root = self._root(False)
        with d:
            got = assets_mod.identity_lines(root, self.SHOTS).get("LN01", "")
            self.assertIn("老周的固定形象", got)

    def test_cast_count_unaffected(self):
        """**人数声明不受影响** —— 过滤只发生在锚点写入处，不动"本镜有谁"的判据。"""
        from v5.media import assets as assets_mod

        d, root = self._root(True)
        with d:
            self.assertEqual(assets_mod.cast_counts(root, self.SHOTS).get("LN01"), 1)


class TestRefCountCapWithCharacters(unittest.TestCase):
    """参考图张数的定稿规则（2026-09-15）：**人脸优先**，每张脸各一张（最多 3 张）。

    **一次被我推翻的中间结论**：我曾把上限压到 2，依据是"LN02 单样本看起来主角脸变淡"。
    同一天 `_probe-3faces-LN03.jpg` 实测 **3 张人脸同喂 → 三个人都对得上、不混脸、不复制**
    → 直接反驳"第 3 张会稀释"。按"**不留没证据的改动**"原则撤回。
    ⇒ 最终规则：纯道具镜不受限 / **1 个人物封顶 2**（2026-09-09 A/B：再多会多画一个人）/
      **≥2 个人物封顶 3 且先满足人脸**（3 人镜 = 3 张脸，不再出现"第三个人没参考图"）。
    """

    def _root(self):
        import json
        import tempfile
        from pathlib import Path

        from PIL import Image

        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        (root / "images").mkdir(parents=True, exist_ok=True)
        # ⚠️ 各图**必须内容不同**：`bind()` 会按 URL 去重（同脸角色共用一张图是有意设计），
        #    占位图若都是同一块纯色 → data URI 相同 → 被去重成 1 张（我第一次就踩了）。
        for i, n in enumerate(("老周", "小林", "阿凯", "记账本"), start=1):
            Image.new("RGB", (32, 32), (i * 40, i * 20, i * 10)).save(
                root / "images" / (n + ".png"), "PNG")
        reg = {"assets": [
            {"id": n, "name": n, "type": "character", "keywords": [n],
             "priority": 10, "ref_image": n + ".png", "public_url": ""}
            for n in ("老周", "小林", "阿凯")] + [
            {"id": "记账本", "name": "记账本", "type": "prop", "keywords": ["记账本"],
             "priority": 8, "ref_image": "记账本.png", "public_url": ""},
        ]}
        (root / "assets.json").write_text(
            json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return d, root

    def _refs(self, visual: str):
        from v5.media import assets as assets_mod

        d, root = self._root()
        with d:
            shots = [{"name": "LN01", "visual": visual, "dialogue": ""}]
            return assets_mod.bind(root, shots).get("LN01") or []

    def test_three_person_shot_binds_three_faces(self):
        """★ 3 人镜**三张脸都绑**（不再出现"第三个人拿不到参考图"）。"""
        got = self._refs("@老周 站在 @小林 对面，@阿凯 抱着 @记账本 在门口")
        self.assertEqual(len(got), 3, "3 人镜应绑 3 张（人脸优先）：%d 张" % len(got))

    def test_single_person_shot_caps_at_two(self):
        """1 个人物 → 封顶 2（1 脸 + 1 道具）——2026-09-09 A/B：再多会多画一个人。"""
        got = self._refs("@老周 站在院中，手里拿着 @记账本")
        self.assertEqual(len(got), 2, "1 个人物时封顶 2")

    def test_two_person_shot_includes_both_faces(self):
        got = self._refs("@老周 站在 @小林 对面，中间放着 @记账本")
        self.assertEqual(len(got), 3, "2 人 + 1 道具 = 3 张（两张脸都在）")


class TestNoHumanMark(unittest.TestCase):
    """★ 「无人像」标记（2026-09-27，xianxia-vfx-action 化身镜实测新增）。

    要治的事故：仙侠包「人化作兽形能量体」那一镜，分镜**已经不写** `@角色名` 了，
    `bind()` 仍会经 keywords 兜底 + 角色补漏把两张人物设定表捞回来，提示词于是同时
    要求"锁定该角色的长相与服装形制"与"没有站立的人形"——前者更具体，
    模型就画成两个静态人像（实测：兽形完全没出现）。
    ⇒ 关参考图必须在**绑定层**，不能靠措辞。
    """

    def test_parse_strips_mark_and_sets_flag(self):
        from v5.media import storyboard
        md = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |\n"
              "|---|---|---|---|---|---|---|---|\n"
              "| 1 | 全景 | 俯视 | 环绕 | 6 | 【无人像】0-3秒：一具暗赤焰羽凤凰光构自"
              "左侧腾空，与一头深蓝白雷光白虎在半空撞开；3-6秒：连锁爆炸撕开成片屋脊 | "
              "（无声，环境音） | 雷爆 | \n")
        s = storyboard.parse(md)[0]
        self.assertTrue(s["no_human"], "标记应被解析成标志位")
        self.assertNotIn("无人像", s["visual"], "标记不能留在正文（会进提示词）")
        self.assertEqual(len(storyboard.split_beats(s["visual"])), 2,
                         "剥掉标记后节拍仍要正常切分")

    def test_bind_skips_character_sheets(self):
        """同一段正文，加标记前绑 2 张脸、加标记后绑 0 张。"""
        import tempfile
        from pathlib import Path

        from PIL import Image

        from v5.media import assets as assets_mod
        from v5.media import storyboard

        d = tempfile.TemporaryDirectory()
        root = Path(d.name)
        (root / "images").mkdir(parents=True, exist_ok=True)
        for i, n in enumerate(("绛雪", "沧月"), start=1):
            Image.new("RGB", (32, 32), (i * 90, i * 30, i * 10)).save(
                root / "images" / (n + ".png"), "PNG")
        import json
        reg = {"assets": [
            {"id": n, "name": n, "type": "character", "keywords": [n],
             "priority": 10, "ref_image": n + ".png", "public_url": ""}
            for n in ("绛雪", "沧月")]}
        (root / "assets.json").write_text(json.dumps(reg, ensure_ascii=False),
                                          encoding="utf-8")
        md_tmpl = ("| 镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 画面描述 | 对白 | 音效 |\n"
                   "|---|---|---|---|---|---|---|---|\n"
                   "| 1 | 全景 | 俯视 | 环绕 | 6 | %s | （无声，环境音） | 雷爆 |\n")
        # 真实事故形态：分镜**不写 @名**，但正文里出现了角色名（"绛雪化作…"）——
        # `_chars_by_name` 的角色补漏照样把两张人物设定表绑上，于是"锁定长相与服装
        # 形制"压过"没有站立的人形"，化身镜画成两个静态人像。
        body = "0-3秒：绛雪化作一具暗赤焰羽凤凰光构自左侧腾空，与沧月化作的一头深蓝白" \
               "雷光白虎在半空撞开；3-6秒：连锁爆炸撕开成片屋脊"
        with d:
            plain = storyboard.parse(md_tmpl % body)[0]
            marked = storyboard.parse(md_tmpl % ("【无人像】" + body))[0]
            self.assertEqual(len(assets_mod.bind(root, [plain]).get("LN01") or []), 2,
                             "前提：不写 @名 时角色补漏仍绑 2 张人物图（这正是事故来源）")
            self.assertEqual(assets_mod.bind(root, [marked]).get("LN01") or [], [],
                             "带标记的镜必须一张人物图都不绑")
            self.assertEqual(assets_mod.cast_counts(root, [marked])["LN01"], 0,
                             "带标记的镜人数记 0")

    def test_person_directive_silent_for_no_human(self):
        """不注入人数声明：既不能说"共有 2 个人物"，也不能说"空镜·环境静物"。"""
        from v5.media import prompt as P
        self.assertEqual(P.person_directive({"no_human": True, "_cast_n": 0}), "",
                         "空镜声明会说'环境静物'，会把能量化身的动感抹平")
        self.assertIn("2 个人物", P.person_directive({"_cast_n": 2}),
                      "未带标记的多人镜照旧")


class PropKeywordAlwaysMergedTests(unittest.TestCase):
    """★ 道具的 keywords 命中**无条件并入**（2026-09-30，`hits_for_shot` 的 else 分支）。

    事故（duanji-gui-0930 两集 60 镜）：旧逻辑是「`@` 只要命中任何资产就跳过 keywords
    兜底」，而分镜**必然** @ 角色 ⇒ 兜底永不执行 ⇒ 道具只能靠 `@玄铁断戟` 全名点名。
    但 scenedesigner 写道具一律简写（「断戟」「碎玉」「茶盏」），全名一次没出现过。
    实测：**38 镜正文提到道具、绑到道具图的镜数 = 0**，三件资产的参考图从头到尾没进过
    任何一次请求，且**全程日志全绿** —— 没有一道门、一行警告看得见这件事。

    道具图的价值不是"把东西画出来"，是**跨镜同一件**（戟刃那处旧崩缺、戟头那条褪色旧红绳）；
    绑不上 = `key_props` 逐字一致整层契约落空。
    """

    REG = json.dumps({"assets": [
        {"name": "裴惊寒", "type": "character", "keywords": ["裴惊寒"],
         "prompt": "玄色交领窄袖劲装"},
        {"name": "老陈", "type": "character", "keywords": ["她", "老陈"],
         "prompt": "灰色工装"},
        {"name": "侯府正堂花厅", "type": "location", "keywords": ["花厅"],
         "prompt": "红绸幔长案"},
        {"name": "玄铁断戟", "type": "prop",
         "keywords": ["断戟", "长戟", "玄铁戟", "旧红绳"],
         "prompt": "玄铁哑光黑长戟、戟刃一处旧崩缺"},
    ]})

    def test_disease_sample_prop_bound_when_character_is_at_mentioned(self):
        """病样本：@ 了角色、道具只写简称 ⇒ 必须照样绑上道具图。"""
        reg = json.loads(self.REG)
        shot = {"name": "LN08", "scene": "侯府正堂花厅", "shot_type": "近景",
                "visual": "@裴惊寒（玄色交领窄袖劲装）双手横握断戟自上砸下"}
        names = [h.get("name") for h in assets.hits_for_shot(reg, shot)[0]]
        self.assertIn("玄铁断戟", names,
                      "道具只以简称出现时也必须绑上参考图（旧行为：0/38 镜）")
        self.assertIn("裴惊寒", names, "角色不能被道具挤掉")

    def test_negative_control_no_prop_when_not_mentioned(self):
        """反向对照：正文不提任何道具 ⇒ 一张道具图都不许多绑。

        这条是改动的主要风险面（兜底从"偶尔跑"变成"总是跑"）。
        实测基线：duanji-gui-0930 第 1 集 10 镜、第 2 集 12 镜完全不提道具。
        """
        reg = json.loads(self.REG)
        shot = {"name": "LN02", "scene": "侯府正堂花厅", "shot_type": "全景",
                "visual": "@裴惊寒 站在长案前，目光落在门口"}
        names = [h.get("name") for h in assets.hits_for_shot(reg, shot)[0]]
        self.assertNotIn("玄铁断戟", names, "没提道具却绑上 = 平白多一件道具")

    def test_character_keywords_are_never_merged_unconditionally(self):
        """⛔ 只并 prop：character 走无条件并会把别人的脸绑进来。

        2026-09-13 实测 noodle-night LN09：keyword 表含泛词「她」，镜里其实是
        「女孩」，因文本含「她」命中「老陈」→ 绑错脸。所以 prop 的口子不能顺手开给角色。
        """
        reg = json.loads(self.REG)
        shot = {"name": "LN09", "scene": "侯府正堂花厅", "shot_type": "近景",
                "visual": "@裴惊寒 看着她说：把戟放下"}
        names = [h.get("name") for h in assets.hits_for_shot(reg, shot)[0]]
        self.assertNotIn("老陈", names, "角色必须由 @ 或名字命中，不能靠泛词无条件并进来")

    def test_guessed_prop_does_not_pollute_unresolved(self):
        """补绑的道具**不许**混进 `unresolved`。

        那个通道的语义是"@ 了但注册表查无此资产"。把补绑项塞进去会让日志反着说
        （"引用了不存在的资产：玄铁断戟"），把一条正常路径报成事故。
        """
        reg = json.loads(self.REG)
        shot = {"name": "LN10", "scene": "侯府正堂花厅", "shot_type": "近景",
                "visual": "@裴惊寒 松开长戟，退半步"}
        hits, unresolved = assets.hits_for_shot(reg, shot)
        self.assertIn("玄铁断戟", [h.get("name") for h in hits], "补绑照常生效")
        self.assertEqual(unresolved or [], [],
                         "unresolved 只装真正的坏 @，补绑不是事故")

    def test_full_name_in_parentheses_without_at_still_binds(self):
        """★ 真实形态：全名写在**括号描述里、不加 `@`** ⇒ 照样要绑。

        这条是被实测逼出来的：我一度给补绑加了「全名没出现在正文才算」的条件
        （为了保住 `tests_flow` 那条旧断言），结果 duanji-gui-0930 第 1 集
        从 17/20 掉回 7/20 —— 因为分镜的真实写法就是
        「主案侧戟头（玄铁断戟、褪色旧红绳垂地）」「腰间细红绳挂半枚碎玉」，
        全名在括号里、没有 `@`。**那个条件把整条修复废掉了。**
        """
        reg = json.loads(self.REG)
        shot = {"name": "LN11", "scene": "侯府正堂花厅", "shot_type": "近景",
                "visual": "@裴惊寒 侧身面向主案侧戟头（玄铁断戟、褪色旧红绳垂地）"}
        names = [h.get("name") for h in assets.hits_for_shot(reg, shot)[0]]
        self.assertIn("玄铁断戟", names,
                      "全名在括号里、没加 @ —— 这正是要救的形态，不能漏")

    def test_old_behaviour_would_still_be_caught(self):
        """把旧病装回去，证明上面几条**会红** —— 否则断言是空的。"""
        reg = json.loads(self.REG)
        shot = {"name": "LN08", "scene": "侯府正堂花厅", "shot_type": "近景",
                "visual": "@裴惊寒（玄色交领窄袖劲装）双手横握断戟自上砸下"}
        # 旧行为复刻：@ 命中非空 ⇒ 完全不看 keywords
        matched, _ = assets.resolve_mentions(shot["visual"], reg)
        by_name = {str(a.get("name") or ""): a for a in reg["assets"]}
        old = [by_name[n] for n in matched if n in by_name]
        self.assertTrue(old, "前提：旧逻辑下 @ 已命中，兜底不会执行")
        self.assertNotIn("玄铁断戟", [h.get("name") for h in old],
                         "旧逻辑必须绑不到道具 —— 这条断言不成立就说明测试是空的")




class TestPropAliasTailBinding(unittest.TestCase):
    """★ 简称也要绑得上参考图（2026-10-05 实测 `madfate-abc-1005` 第 1 集）。

    那件核心反转道具「死者眼球放大片」：卡里 `keywords` 只有全名（该包的资产契约
    根本没要求写关键词栏），而分镜 13 镜写的都是简称「放大片」⇒ `k in text` 永远为假
    ⇒ **参考图一次都没进过请求，日志全绿**。道具没有外观锚，模型就拿同一场里
    唯一绑上的「旧机械表」那张怀表图去顶 ⇒ 道具忽大忽小、两件长成一件，观众跳戏。
    """

    REG = {"assets": [
        {"name": "死者眼球放大片", "type": "prop", "priority": 8,
         "keywords": ["死者眼球放大片"]},
        {"name": "旧机械表", "type": "prop", "priority": 8,
         "keywords": ["旧机械表"]},
    ]}

    def test_short_form_binds_by_name_tail(self):
        hits = assets.hits_for_text(self.REG, "她右手拈起圆形放大片，悬在死者眼球上方")
        self.assertEqual([h["name"] for h in hits], ["死者眼球放大片"],
                         "简称「放大片」是全名的尾巴 ⇒ 尾词兜底要把它绑上")

    def test_reverse_control_absent_prop_stays_out(self):
        """反向对照：正文没提这件道具 ⇒ 不许凭空调进来（兜底不是无条件绑）。"""
        self.assertEqual(assets.hits_for_text(self.REG, "脏绿旧日光灯管频闪，台面空着"), [])

    def test_ambiguous_tail_is_dropped(self):
        """两件道具共有同一个尾词 ⇒ 谁都不绑，别拿"少绑"去换"绑错"。"""
        reg = {"assets": [
            {"name": "红色外套", "type": "prop", "priority": 8, "keywords": ["红色外套"]},
            {"name": "蓝色外套", "type": "prop", "priority": 8, "keywords": ["蓝色外套"]},
        ]}
        self.assertEqual(assets.hits_for_text(reg, "那件色外套挂在门后钉子上"), [],
                         "「色外套」同时属于两件 ⇒ 有歧义，不许猜")
        self.assertEqual([h["name"] for h in assets.hits_for_text(reg, "红色外套挂在钉子上")],
                         ["红色外套"], "写得清的时候照旧绑")

    def test_unbound_mentions_reports_the_silent_case(self):
        """「提到 N 镜却零绑定」必须被算出来（这就是那次没人报的那一条）。"""
        shots = [{"name": "LN01", "visual": "她把放大片举到右眼前", "dialogue": ""},
                 {"name": "LN02", "visual": "放大片玻璃面反出脏绿光", "dialogue": ""},
                 {"name": "LN03", "visual": "天台水塔，无人", "dialogue": ""}]
        self.assertEqual(assets.unbound_mentions(self.REG, shots, {}),
                         [("死者眼球放大片", 2)])
        self.assertEqual(assets.unbound_mentions(self.REG, shots,
                                                 {"LN01": ["死者眼球放大片"]}), [],
                         "只要进过一次真实请求就不该再报")

    def test_validate_assets_carries_the_new_problem_line(self):
        """接线：新判据要走 `validate_assets` 这**同一道**检查，不另起一处报告。"""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "assetdesigner").mkdir()
            (root / "assetdesigner" / "assets.md").write_text(
                "## 资产卡：死者眼球放大片\n"
                "- 类型：prop\n- 关键词：死者眼球放大片\n"
                "- 用途：一片圆形玻璃放大片，边缘磨花\n- 参考图：images/死者眼球放大片.png\n",
                encoding="utf-8")
            (root / "images").mkdir()
            (root / "images" / "死者眼球放大片.png").write_bytes(b"\x89PNG fake")
            shots = [{"name": "LN01", "visual": "她把放大片举到右眼前", "dialogue": ""},
                     {"name": "LN02", "visual": "放大片贴着死者眼球", "dialogue": ""}]
            probs = assets.validate_assets(root, shots, {}, names_out={})
            self.assertTrue(any("一次都没进过任何一次请求" in p for p in probs), probs)
            probs2 = assets.validate_assets(root, shots, {},
                                            names_out={"LN01": ["死者眼球放大片"]})
            self.assertFalse(any("一次都没进过任何一次请求" in p for p in probs2), probs2)


class TestPropSlotOrder(unittest.TestCase):
    """★ 道具抢同一个参考图名额时，谁该留下（2026-10-06）。

    实测事故《听见灯》ep1 夜堤那一场：分镜写「坐在@折叠竹椅上 … 扶住膝上@走马灯灯边」，
    椅子在灯**前面**被 @，而双人镜 cap=3 只剩一个道具位 ⇒ 标题道具整场没参考图，
    成片里那盏六角走马灯三镜画成三种形状，返工两轮约 1 小时。
    注册表里所有 prop 的 priority 都是 `cast._register` 写死的 8，所以"按 priority 排"
    在道具之间等于没排 —— 判据必须是盘上已有的**事实**，⛔ 不是模型自觉。
    """

    def _mk_root(self, key_props=None, with_brief=True):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        for i, n in enumerate(("沈知夏.png", "老钟.png", "走马灯.png",
                               "折叠竹椅.png", "牛皮纸订单.png", "半片竹篾.png",
                               "秦淮河边夜堤.png")):
            _mk(root, n, pad=i + 1)
        assets_json = [
            {"id": "a", "name": "沈知夏", "type": "character", "keywords": ["沈知夏"],
             "priority": 10, "public_url": "", "url": "", "ref_image": "沈知夏.png"},
            {"id": "b", "name": "老钟", "type": "character", "keywords": ["老钟"],
             "priority": 10, "public_url": "", "url": "", "ref_image": "老钟.png"},
            {"id": "c", "name": "走马灯", "type": "prop", "keywords": ["走马灯"],
             "priority": 8, "public_url": "", "url": "", "ref_image": "走马灯.png"},
            {"id": "d", "name": "折叠竹椅", "type": "prop", "keywords": ["折叠竹椅", "竹椅"],
             "priority": 8, "public_url": "", "url": "", "ref_image": "折叠竹椅.png"},
            {"id": "e", "name": "牛皮纸订单", "type": "prop", "keywords": ["牛皮纸订单"],
             "priority": 8, "public_url": "", "url": "", "ref_image": "牛皮纸订单.png"},
            {"id": "f", "name": "半片竹篾", "type": "prop", "keywords": ["半片竹篾"],
             "priority": 8, "public_url": "", "url": "", "ref_image": "半片竹篾.png"},
            {"id": "g", "name": "秦淮河边夜堤", "type": "location",
             "keywords": ["秦淮河", "夜堤"], "priority": 8,
             "public_url": "", "url": "", "ref_image": "秦淮河边夜堤.png"},
        ]
        (root / "assets.json").write_text(
            json.dumps({"assets": assets_json}, ensure_ascii=False), encoding="utf-8")
        if with_brief:
            (root / "brief.json").write_text(
                json.dumps({"key_props": key_props or ["走马灯：六角竹骨宫灯、米白绢面"]},
                           ensure_ascii=False), encoding="utf-8")
        return tmp, root

    #: 争议镜：椅子先被 @（旧实现就因此赢），两个人物 ⇒ cap 3 ⇒ 只剩一个道具位
    HOT = {"name": "LN23", "shot_type": "近景", "scene": "秦淮河边夜堤",
           "visual": "@老钟（黑色旧棉袄）坐在@折叠竹椅上，左手扶住膝上@走马灯灯边，"
                     "@沈知夏（靛蓝围裙）站在他侧前",
           "dialogue": "老钟：你外头风大。"}

    def _episode(self, lamp_times=9, chair_times=2):
        shots = [dict(self.HOT)]
        for i in range(lamp_times):
            shots.append({"name": "L%d" % i, "shot_type": "近景", "scene": "秦淮河边夜堤",
                          "visual": "@沈知夏捧着@走马灯", "dialogue": ""})
        for i in range(chair_times):
            shots.append({"name": "C%d" % i, "shot_type": "近景", "scene": "秦淮河边夜堤",
                          "visual": "@老钟坐在@折叠竹椅上", "dialogue": ""})
        return shots

    def test_fixture_reproduces_the_losing_order(self):
        """反向对照：夹具里椅子确实**排在灯前面**，且灯的提及镜数确实更高 ——
        否则下面那条断言可能只是"顺序碰巧对"，测不到东西。"""
        t = self.HOT["visual"]
        self.assertLess(t.index("@折叠竹椅"), t.index("@走马灯"))
        tmp, root = self._mk_root()
        self.addCleanup(tmp.cleanup)
        cnt = assets.mention_counts(assets.load_registry(root), self._episode())
        self.assertGreater(cnt["走马灯"], cnt["折叠竹椅"])
        self.assertEqual(cnt["走马灯"], 10)     # 争议镜 + 9 段填充
        # ★ 旧病装回去：`hits_for_shot` 返回的顺序**就是**改造前 `picks` 的顺序
        #   （旧实现 `picks = (chars + others)[:cap]`，没有排序那一步）。
        #   实测它给出椅子 ⇒ 上面那条"灯赢"的断言在改造前必红，不是顺序碰巧对。
        hits, _ = assets.hits_for_shot(assets.load_registry(root), self.HOT)
        pre_fix = [h["name"] for h in hits
                   if h.get("type") == "character"] + \
                  [h["name"] for h in hits
                   if h.get("type") not in ("character", "location")]
        self.assertEqual(pre_fix[:3], ["沈知夏", "老钟", "折叠竹椅"])

    def test_prop_slot_goes_to_the_lamp_not_the_chair(self):
        tmp, root = self._mk_root()
        self.addCleanup(tmp.cleanup)
        nm: dict = {}
        assets.bind(root, self._episode(), names_out=nm)
        self.assertEqual(nm["LN23"], ["沈知夏", "老钟", "走马灯"],
                         "双人镜只剩一个道具位时，关键道具必须压过先被 @ 的椅子")

    def test_key_prop_beats_mention_count(self):
        """「半片竹篾：…全片只出现一次」这类**镜数少但被 brief 点名**的道具也要赢。"""
        tmp, root = self._mk_root(key_props=["半片竹篾：约二十二厘米青竹篾、断口斜茬"])
        self.addCleanup(tmp.cleanup)
        shots = [{"name": "LN01", "shot_type": "近景", "scene": "沈记灯铺后屋作坊",
                  "visual": "@沈知夏手里是@牛皮纸订单与那半片@半片竹篾", "dialogue": ""}]
        for i in range(5):
            shots.append({"name": "P%d" % i, "shot_type": "近景",
                          "visual": "@沈知夏翻看@牛皮纸订单", "dialogue": ""})
        nm: dict = {}
        assets.bind(root, shots, names_out=nm)
        self.assertEqual(nm["LN01"], ["沈知夏", "半片竹篾"])

    def test_single_prop_shot_unchanged(self):
        """只有一个道具入镜 ⇒ 排序不参与，行为与改造前一字不变。"""
        tmp, root = self._mk_root()
        self.addCleanup(tmp.cleanup)
        nm: dict = {}
        assets.bind(root, [{"name": "LN01", "shot_type": "近景",
                            "visual": "@沈知夏坐在@折叠竹椅上", "dialogue": ""}], names_out=nm)
        self.assertEqual(nm["LN01"], ["沈知夏", "折叠竹椅"])

    def test_scene_still_wins_the_solo_slot(self):
        """⚠️ 2026-09-23 的「场景优先」不许被这次改动顺带翻掉：
        1 人物宽景镜 cap=2 ⇒ 第 2 格仍是场景空镜，道具进不来。"""
        tmp, root = self._mk_root()
        self.addCleanup(tmp.cleanup)
        nm: dict = {}
        assets.bind(root, [{"name": "LN01", "shot_type": "全景", "scene": "秦淮河边夜堤",
                            "visual": "@沈知夏站在堤上，膝上是@走马灯与@折叠竹椅",
                            "dialogue": ""}], names_out=nm)
        self.assertEqual(nm["LN01"], ["沈知夏", "秦淮河边夜堤"])

    def test_no_brief_falls_back_to_counts(self):
        """老项目没有 `key_props` ⇒ 只按本集提及镜数排，不报错、结论不变。"""
        tmp, root = self._mk_root(with_brief=False)
        self.addCleanup(tmp.cleanup)
        self.assertEqual(assets.key_prop_names(root), set())
        nm: dict = {}
        assets.bind(root, self._episode(), names_out=nm)
        self.assertEqual(nm["LN23"], ["沈知夏", "老钟", "走马灯"])


class TestRefCapEnv(unittest.TestCase):
    """★ 多人镜的参考图封顶：默认仍是实测值 3，放宽要显式开、收紧一律不认。

    病（2026-10-06 实测 `ice-spring-bridge-duel` 第 1 集）：双人打斗镜 2 张脸占掉
    两格，只剩 1 个道具位 ⇒ 春灵的荆棘弯刃 **5 镜零绑定**、场景图也进不来，
    结尾镜头一拉远桥就换成了另一个地方。
    """

    def setUp(self):
        import os
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._saved = {k: os.environ.pop(k, None) for k in
                       (assets.REF_CAP_MULTI_ENV, assets.REF_CAP_SOLO_ENV)}
        # ⚠️ 每张图的内容必须**互不相同**：`bind()` 按 URL 去重，而 `_mk` 写的是
        #    同一张 1x1 PNG 加 pad 字节 —— pad 一样 ⇒ data URI 一样 ⇒ 五张图被去重成一张。
        for i, f in enumerate(("甲.png", "乙.png", "剑.png", "刀.png", "桥.png"), start=1):
            _mk(self.root, f, pad=i)
        (self.root / "assets.json").write_text(json.dumps({"assets": [
            {"id": "c1", "name": "甲", "type": "character", "keywords": ["甲"],
             "priority": 10, "ref_image": "甲.png"},
            {"id": "c2", "name": "乙", "type": "character", "keywords": ["乙"],
             "priority": 10, "ref_image": "乙.png"},
            {"id": "p1", "name": "紫晶剑", "type": "prop", "keywords": ["紫晶剑", "剑"],
             "priority": 8, "ref_image": "剑.png"},
            {"id": "p2", "name": "荆棘弯刃", "type": "prop", "keywords": ["荆棘弯刃", "刀"],
             "priority": 8, "ref_image": "刀.png"},
            {"id": "l1", "name": "石拱桥", "type": "location", "keywords": ["石拱桥", "桥"],
             "priority": 8, "ref_image": "桥.png"},
        ]}, ensure_ascii=False), encoding="utf-8")
        (self.root / "brief.json").write_text('{"topic":"t","episodes":1}', encoding="utf-8")
        # 「场景」列要有值：宽景镜的 location 是**无条件按场景列绑**的（2026-09-28 修），
        # 只靠正文提到桥名不会进 `hits`（正文补漏只并 prop）。
        self.shots = [{"name": "LN01", "shot_type": "全景", "scene": "石拱桥",
                       "visual": "@甲 @乙 各持剑与刀在石拱桥上对峙", "dialogue": ""}]

    def tearDown(self):
        import os
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _bound(self):
        names: dict = {}
        assets.bind(self.root, self.shots, names_out=names)
        return names.get("LN01") or []

    def test_default_keeps_the_measured_three(self):
        got = self._bound()
        self.assertEqual(len(got), 3, "默认档不许变：2 张脸 + 1 格（实测值）")
        self.assertEqual(got[:2], ["甲", "乙"], "先满足人脸")
        self.assertNotIn("荆棘弯刃", got, "默认档下第二件兵器进不来 —— 这正是要放宽的理由")

    def test_env_widens_and_the_second_weapon_gets_in(self):
        import os
        os.environ[assets.REF_CAP_MULTI_ENV] = "5"
        got = self._bound()
        self.assertEqual(len(got), 5, "放宽到 5 ⇒ 2 脸 + 场景 + 两把兵器都进得来")
        self.assertIn("荆棘弯刃", got)
        self.assertIn("石拱桥", got)

    def test_tightening_is_refused(self):
        """反向对照：设成比实测更小的数**不许生效**（没有 A/B 依据的收紧）。"""
        import os
        os.environ[assets.REF_CAP_MULTI_ENV] = "1"
        self.assertEqual(assets.ref_caps()[1], assets.REF_CAP_MULTI)
        self.assertEqual(len(self._bound()), 3)

    def test_solo_cap_untouched_by_the_multi_env(self):
        """单变量：放宽多人封顶不该顺手改掉单人镜的那个数。"""
        import os
        os.environ[assets.REF_CAP_MULTI_ENV] = "5"
        solo, multi = assets.ref_caps()
        self.assertEqual((solo, multi), (assets.REF_CAP_SOLO, 5))


class TestSceneColumnAlias(unittest.TestCase):
    """「场景」列写的是**同一处地方的另一种说法**时，也要绑到那张场景图。

    2026-10-06 实测（`ice-spring-bridge-duel` 第 1 集）：注册表里的场景叫
    「秋宫山谷·石拱桥与桥下溪流」，分镜「场景」列写「秋宫山谷·石拱桥桥面」
    （第 3 镜还写成「…桥面及桥上方空域」）⇒ 旧实现只做**精确同名**查表，
    5 镜一张场景图都没绑上，静帧把"石拱桥上的决斗"画成开满花的山坡。
    资产卡的 `关键词` 栏里本来就写着「石拱桥」，只是这条通道没用过它。
    """

    REG = json.dumps({"assets": [
        {"id": "桥", "name": "秋宫山谷·石拱桥与桥下溪流", "type": "location",
         "keywords": ["石拱桥", "秋宫山谷", "桥下溪流", "木栏杆"],
         "ref_image": "秋宫山谷·石拱桥与桥下溪流.png", "url": "https://x/bridge.png"},
        {"id": "冰仙", "name": "冰仙", "type": "character", "keywords": ["冰仙"],
         "ref_image": "冰仙.png", "url": "https://x/bing.png"},
        {"id": "春灵", "name": "春灵", "type": "character", "keywords": ["春灵"],
         "ref_image": "春灵.png", "url": "https://x/chun.png"},
    ]})

    def _names(self, scene, shot_type="全景"):
        reg = json.loads(self.REG)
        shot = {"name": "LN01", "scene": scene, "shot_type": shot_type,
                "visual": "@冰仙 与 @春灵 在桥面对砍"}
        return [h.get("name") for h in assets.hits_for_shot(reg, shot)[0]]

    def test_scene_column_shares_a_keyword_with_the_asset(self):
        got = self._names("秋宫山谷·石拱桥桥面")
        self.assertIn("秋宫山谷·石拱桥与桥下溪流", got,
                      "「桥面」这种写法也要认回同一处场景")
        self.assertIn("冰仙", got, "角色不能被场景挤掉")

    def test_exact_name_still_works(self):
        """反向对照：加别名通道**不许**弄坏精确同名那条路。"""
        self.assertIn("秋宫山谷·石拱桥与桥下溪流",
                      self._names("秋宫山谷·石拱桥与桥下溪流"))

    def test_near_shot_still_binds_nothing(self):
        """近景不绑场景图的政策（2026-09-23）不因别名通道而放宽。"""
        self.assertNotIn("秋宫山谷·石拱桥与桥下溪流",
                         self._names("秋宫山谷·石拱桥桥面", shot_type="近景"))

    def test_ambiguous_alias_binds_nothing(self):
        """★ 两个场景同时命中 ⇒ 谁都不绑（拿错地方的图比不绑更糟）。"""
        reg = json.loads(self.REG)
        reg["assets"].append({
            "id": "亭", "name": "秋宫山谷·临溪亭", "type": "location",
            "keywords": ["临溪亭", "石拱桥"],     # 与桥共有「石拱桥」这个词
            "ref_image": "亭.png", "url": "https://x/pavilion.png"})
        got = [h.get("name") for h in
               assets.hits_for_shot(reg, {"name": "LN02", "scene": "秋宫山谷·石拱桥桥面",
                                          "shot_type": "全景", "visual": "@冰仙"})[0]]
        self.assertNotIn("秋宫山谷·临溪亭", got)
        self.assertNotIn("秋宫山谷·石拱桥与桥下溪流", got,
                         "两处都命中时不猜 —— 宁可不绑")


if __name__ == "__main__":
    unittest.main()
