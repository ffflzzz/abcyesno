"""分龄变体与新角色补卡（`v5/media/variants.py`）+ 绑定层按括注选表。

为什么这些测试必须存在（2026-09-30 实测，`shiguan-series-0926` 三集已出片）：
  ① 剧本要求主角 10→14→16 岁，但全剧只有**一张** 10 岁定妆照，且 identity 明写
    「全片每镜必须完全一致」⇒ 冲突时图赢，第 2、3 集出片仍是孩童体型；
  ② 第 3 集 `@狮艺店老板娘` 被点名 29 次，注册表 12 条里一条没有 ⇒ 她的脸每镜自由发挥。
两条都是**跑完不报错**的失效，所以判据必须被测住，不能靠日志。

★ 夹具铁律：每个用例都断言 `storyboard.parse` 解析出 ≥2 镜 —— 解析器会按列名/长度
  静默丢行，0 镜的夹具会让上面两条判据"全过"（同 `tests_*` 里那条反复踩到的坑）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from v5.media import assets, cast, storyboard, variants

WB = """# 角色卡：阿旺（主角）

- 姓名：阿旺
- 外貌特征：10 岁男孩，圆脸颊肉饱满，圆寸头，右额角碎发翘起，洗白蓝校服短袖短裤，
  白色球鞋，左膝髌骨下方陈旧淤青紫黄色。
"""

_TABLE = "\n".join([
    "| 镜头号 | 景别 | 角度 | 运镜 | 时长 | 画面描述 | 对白 | 音效 |",
    "|---|---|---|---|---|---|---|---|",
])


def row(n: int, visual: str) -> str:
    return ("| %d | 近景 | 仰拍 | 固定 | 5 | %s | （旁白·人物闭嘴） | 画外音：环境音 |"
            % (n, visual))


def shots_of(*visuals: str) -> list:
    md = _TABLE + "\n" + "\n".join(row(i + 1, v) for i, v in enumerate(visuals))
    shots = storyboard.parse(md)
    assert len(shots) == len(visuals), \
        "夹具只解析出 %d/%d 镜 —— 判据会在缺镜的表上假通过" % (len(shots), len(visuals))
    return shots


def derive(shots, base=None, known=(), spk=()):
    return variants.derive_cards(shots, base if base is not None else {"阿旺": "10 岁男孩，圆脸"},
                                 known, spk, log=lambda *_: None)


class DeriveTests(unittest.TestCase):
    def test_age_variant_when_storyboard_ages_up(self):
        """分镜把年龄推到 14 岁、卡上写 10 岁 ⇒ 派生「阿旺（14岁版）」并指向基础卡锁脸。"""
        got = derive(shots_of(
            "@阿旺（14岁，瘦高身形，蓝白校服外套）趴在下铺床沿看手机",
            "@阿旺（14岁，蓝白校服外套）抬头，视线锁定屏幕"))
        self.assertEqual([c["name"] for c in got], ["阿旺（14岁版）"])
        c = got[0]
        self.assertEqual(c["alias_of"], "阿旺")
        self.assertEqual(c["age_tag"], "14岁")
        self.assertIn("瘦高身形", c["appearance"])
        # 变体的外观必须**明说"同一个人只改年龄体型"**，否则 img2img 会重新抽一个人
        self.assertIn("与阿旺一致", c["appearance"])

    def test_same_age_as_card_derives_nothing(self):
        """年龄段与卡一致 ⇒ 什么都不做（不许无谓重烧定妆照）。"""
        got = derive(shots_of(
            "@阿旺（10岁，圆脸，洗白蓝校服）扒着祠堂门缝",
            "@阿旺（10岁，圆脸）退半步，视线仍锁门缝"))
        self.assertEqual(got, [])

    def test_new_character_gets_card_with_shorthand_alias(self):
        """没卡的人出场够多 ⇒ 补一张卡；分镜里的简称必须一起登记，否则多数镜仍绑不到图。"""
        got = derive(shots_of(
            "@狮艺店老板娘（40岁，花白短发，围裙上有旧污渍）从货架后转身",
            "@老板娘（花白短发，围裙旧污渍）把现金推过柜台"))
        self.assertEqual([c["name"] for c in got], ["狮艺店老板娘"])
        self.assertEqual(got[0]["alias_names"], ["老板娘"])
        self.assertEqual(got[0].get("alias_of", ""), "")

    def test_known_prop_is_not_turned_into_a_person(self):
        """道具被 @ 得再多也不能派生成"角色"（第一版实测把牛皮大鼓画成了人）。"""
        got = derive(shots_of(
            "@牛皮大鼓（鼓面磨痕，鼓槌搁架两侧）静置",
            "@牛皮大鼓（鼓面磨痕）入画，鼓槌在右侧"), known={"牛皮大鼓"})
        self.assertEqual(got, [])

    def test_camera_language_does_not_split_the_person(self):
        """`@阿旺侧脸近景（左脸3/4）` 是"名字+镜头术语"，不是第二个人。

        第一版把它当成独立资产 ⇒ 一集派生出 6 张假卡（真烧 6 次图）。
        """
        got = derive(shots_of(
            "@阿旺（14岁，瘦高身形）起身；切@阿旺侧脸近景（左脸3/4），嘴闭合",
            "@阿旺近侧脸（右脸3/4）；@阿旺（14岁，蓝白校服外套）视线落前方"))
        self.assertEqual([c["name"] for c in got], ["阿旺（14岁版）"])

    def test_one_shot_stranger_is_not_carded(self):
        """只出现一镜的名字不配一张定妆照（门槛数的是**镜数**）。"""
        got = derive(shots_of(
            "@阿旺（14岁，瘦高身形）抬头",
            "门口站着@陌生访客（戴斗笠）的背影，@陌生访客（戴斗笠）放下斗笠"))
        self.assertEqual([c["name"] for c in got], [])

    def test_person_registered_as_prop_still_gets_a_face(self):
        """被错登记成道具、但分镜带年龄点名的人 ⇒ 照样补卡（否则他永远没有脸）。"""
        got = derive(shots_of(
            "@阿力队长（25岁，白色狮队服束腰）抱臂站在桩阵尽头",
            "@阿力（25岁，白色狮队服束腰）下颌微抬"),
            known={"阿力队长"})
        self.assertEqual([c["name"] for c in got], ["阿力队长"])


BASE_REG = {"assets": [
    {"id": "阿旺", "name": "阿旺", "type": "character", "priority": 10,
     "keywords": ["阿旺"], "ref_image": "阿旺.png",
     "identity": "阿旺的固定形象（全片每镜必须完全一致）：10 岁男孩，圆脸"},
]}
VARIANT_REG = {"assets": list(BASE_REG["assets"]) + [
    {"id": "阿旺（14岁版）", "name": "阿旺（14岁版）", "type": "character", "priority": 10,
     "keywords": ["阿旺", "阿旺（14岁版）"], "ref_image": "阿旺（14岁版）.png",
     "alias_of": "阿旺", "age_tag": "14岁",
     "identity": "阿旺（14岁版）的固定形象（本阶段（14岁）每镜必须完全一致）：14 岁，瘦高身形"},
]}


class BindTests(unittest.TestCase):
    def _names(self, reg, visual):
        s = shots_of(visual)[0]
        hits, _ = assets.hits_for_shot(reg, s)
        return [h.get("name") for h in hits]

    def test_parenthetical_age_selects_the_variant_sheet(self):
        """本镜写 14 岁 ⇒ 绑 14 岁那张表。不绑就是拿孩童设定表画青少年（实测的病）。"""
        self.assertEqual(
            self._names(VARIANT_REG, "@阿旺（14岁，瘦高身形，蓝白校服外套）站在桩阵中央"),
            ["阿旺（14岁版）"])

    def test_bare_mention_keeps_base_sheet(self):
        """没写年龄段 ⇒ 绑基础条目，行为与改造前一字不变。"""
        self.assertEqual(
            self._names(VARIANT_REG, "@阿旺抬起头，视线牢牢锁定在手机屏幕上"), ["阿旺"])

    def test_registry_without_variants_is_untouched(self):
        """没有变体条目时（= 所有历史项目）绑定结果不得有任何变化。"""
        self.assertEqual(self._names(BASE_REG, "@阿旺（14岁，瘦高身形）抬头"), ["阿旺"])

    def test_shorthand_mention_resolves_to_full_name(self):
        """`@老板娘` 要能认到「狮艺店老板娘」——实测简称 8 次 / 全名 1 次。"""
        reg = {"assets": [
            {"id": "狮艺店老板娘", "name": "狮艺店老板娘", "type": "character",
             "alias_names": ["老板娘"], "ref_image": "狮艺店老板娘.png"}]}
        matched, leftover = assets.resolve_mentions("@老板娘 把现金推过柜台", reg)
        self.assertEqual(matched, ["狮艺店老板娘"])
        self.assertEqual(leftover, [])

    def test_alias_matching_does_not_apply_to_plain_keywords(self):
        """简称匹配**只认 `alias_names`**：历史条目的 keywords 里有 他/她/主角 这类泛词，
        拿它们做 `@` 匹配会绑错脸（village-bees 的事故口径）。"""
        reg = {"assets": [{"id": "老陈", "name": "老陈", "keywords": ["他", "主角"]}]}
        matched, _ = assets.resolve_mentions("@他 转身离开", reg)
        self.assertEqual(matched, [])


class CastWiringTests(unittest.TestCase):
    """`cast.ensure` 真的会把派生卡走一遍出图并登记（不碰网络：`_turnaround` 打桩）。"""

    def _root(self, visuals):
        d = tempfile.TemporaryDirectory()
        root = Path(d.name) / "p1"
        (root / "worldbuilder").mkdir(parents=True)
        (root / "scenedesigner").mkdir(parents=True)
        (root / "images").mkdir(parents=True)
        (root / "worldbuilder" / "worldbuilder.md").write_text(WB, encoding="utf-8")
        (root / "scenedesigner" / "scenedesigner_ep2.md").write_text(
            _TABLE + "\n" + "\n".join(row(i + 1, v) for i, v in enumerate(visuals)),
            encoding="utf-8")
        return d, root

    def test_variant_card_is_generated_and_registered_with_alias(self):
        from unittest import mock

        seen: dict = {}

        def fake_turnaround(r, c, ratio, log=print, source=""):
            seen[c["name"]] = source
            return ""          # 空 → 走降级登记分支，测试不碰网络

        d, root = self._root([
            "@阿旺（14岁，瘦高身形，蓝白校服外套）在下铺看手机",
            "@阿旺（14岁，蓝白校服外套）抬头"])
        with d, mock.patch.object(cast, "_turnaround", side_effect=fake_turnaround), \
                mock.patch.object(cast, "_single", side_effect=AssertionError("不该生资产图")):
            cast.ensure(root, log=lambda *_: None, ep=2)
            reg = json.loads((root / "assets.json").read_text(encoding="utf-8"))
        self.assertIn("阿旺（14岁版）", seen, "派生卡没进 cast 的出图循环：%s" % list(seen))
        rec = [a for a in reg["assets"] if a.get("name") == "阿旺（14岁版）"]
        self.assertEqual(len(rec), 1, rec)
        # 续跑要认得出"这是谁的几岁版"——不落盘就下次进程丢失（绑定层靠它选表）
        self.assertEqual(rec[0]["alias_of"], "阿旺")
        self.assertEqual(rec[0]["age_tag"], "14岁")
        self.assertIn("本阶段", rec[0]["identity"])

    def test_age_variants_off_is_byte_identical_to_history(self):
        """`SHORTDRAMA_AGE_VARIANTS=0` ⇒ 一条派生卡都不许有（退回改造前的开关）。"""
        from unittest import mock

        from v5 import config

        seen: list = []
        d, root = self._root([
            "@阿旺（14岁，瘦高身形，蓝白校服外套）在下铺看手机",
            "@阿旺（14岁，蓝白校服外套）抬头"])
        with d, mock.patch.object(config, "AGE_VARIANTS", False), \
                mock.patch.object(cast, "_turnaround",
                                  side_effect=lambda r, c, ratio, log=print, source="": (
                                      seen.append(c["name"]) or "")), \
                mock.patch.object(cast, "_single", side_effect=AssertionError("不该生资产图")):
            cast.ensure(root, log=lambda *_: None, ep=2)
        self.assertEqual(seen, ["阿旺"])


class SameNameAgeCardTests(unittest.TestCase):
    """同名多张年龄卡必须拆成各自独立的表（2026-09-30 实测的新缺陷）。

    上游 `xiaoman-workshop-1030` 写了「8 岁」和「13 岁」两张小满卡，而 `_register`
    按 name upsert、图片也叫 `<name>.png` ⇒ 后一张覆盖前一张，全剧只剩一张不知道
    几岁的脸。
    """

    def test_second_card_becomes_its_own_variant(self):
        from v5.media import cast

        out = cast.split_same_name_cards(
            [{"name": "小满", "heading": "# 角色卡：小满（8 岁，第 1 集）",
              "appearance": "圆脸带婴儿肥，脸颊饱满圆润"},
             {"name": "小满", "heading": "# 角色卡：小满（13 岁，第 2 集）",
              "appearance": "明显抽条，比 8 岁时下颌线更清晰"}],
            log=lambda *_: None)
        self.assertEqual([c["name"] for c in out], ["小满", "小满（13岁版）"],
                         "年龄要取标题自述 —— 13 岁卡通篇在跟 8 岁比，取第一个会取成 8 岁")
        v = out[1]
        self.assertEqual((v["alias_of"], v["age_tag"], v["alias_names"]),
                         ("小满", "13岁", ["小满"]))
        self.assertTrue(v["derived"], "拆出来的表不该占基础角色的生图预算")

    def test_age_falls_back_to_largest_in_text(self):
        """标题没写年龄时取正文里**最大**的（比较句里的参照年龄总是较小的那个）。"""
        from v5.media import variants

        self.assertEqual(variants.card_age_tag(
            {"heading": "# 角色卡：小满", "appearance": "比 8 岁时抽条，13 岁，下颌线清晰"}),
            "13岁")

    def test_second_card_without_age_is_loud_not_guessed(self):
        """没写绝对年龄就**不猜** —— 猜出来的年龄会静默锁错脸。"""
        from v5.media import cast

        lines = []
        out = cast.split_same_name_cards(
            [{"name": "小满", "heading": "# 角色卡：小满（童年）", "appearance": "圆脸带婴儿肥"},
             {"name": "小满", "heading": "# 角色卡：小满（少年）", "appearance": "抽条了，下颌线更清晰"}],
            log=lambda m: lines.append(m))
        self.assertEqual([c["name"] for c in out], ["小满", "小满"])
        self.assertIn("没写绝对年龄", " ".join(lines))

    def test_covered_ages_includes_base_card_own_age(self):
        from v5.media import cast

        cov = cast.covered_ages([
            {"name": "小满", "heading": "# 角色卡：小满（8 岁，第 1 集）",
             "appearance": "圆脸带婴儿肥"},
            {"name": "小满（13岁版）", "alias_of": "小满", "age_tag": "13岁"}])
        self.assertEqual(cov, {"小满": {"8岁", "13岁"}})

    def test_plan_needs_its_own_sheet_not_a_mention_in_the_text(self):
        """判据漏洞的反向对照：卡文本里**出现过**这个年龄 ≠ 这一集有自己的表。

        旧判据拿"13 岁卡里提到了 8 岁"就当 8 岁那集已锚定 ⇒ 不出表，全剧共用一张。
        """
        shots = shots_of("@小满（8 岁，圆脸，红色雨衣）站在巷口看着修车铺",
                         "@小满（8 岁）蹲下摸轮胎，视线落在水洼上")
        # 真实情形：注册表里活下来的是 13 岁那张卡，它的文本顺带提到 8 岁（对照用）
        need, rest = variants.plan(shots, {"小满": "13 岁，明显抽条，比 8 岁时下颌线更清晰"},
                                   ("小满",), ())
        self.assertEqual([r["why"] for r in need], ["age-variant"],
                         "8 岁那集没有自己的表 ⇒ 必须派生，不能被卡里的「8 岁」字样骗过")
        self.assertEqual(rest, [])
        # 给了 covered（拆表之后 8 岁已有自己的表）⇒ 才允许判"已锚定"
        need2, rest2 = variants.plan(shots, {"小满": "8 岁，圆脸"}, ("小满",), (),
                                     covered={"小满": {"8岁"}})
        self.assertEqual(need2, [])
        self.assertEqual([r["why"] for r in rest2], ["already-anchored"])


class EpisodeDefaultTests(unittest.TestCase):
    """**整集**的默认年龄段：拆出表之后，还得让这一集真的去用那张表。

    2026-09-30 实测 `xiaoman-workshop-1030` 第 2 集：分镜契约只在第一拍写全角色锚点，
    后续拍一律 `@小满（蓝色工装马甲）`——**不带年龄**。实测本集 `@小满` 18 次、
    **17 次不带年龄** ⇒ 只按本镜括注选表的话，这一集绝大多数镜仍绑回 8 岁孩童表，
    画面上就是小学生，而旁白在念"十三岁"。
    """

    EP = ("@阿旺（14岁，明显抽条，蓝白校服外套）站在修理铺门口",
          "@阿旺（蓝白校服外套）蹲下摸轮胎，视线落在水洼上",
          "@阿旺（蓝白校服外套）抬头看向巷口")

    def test_one_age_tag_in_the_episode_becomes_its_default(self):
        shots = shots_of(*self.EP)
        self.assertEqual(variants.default_ages(shots, ("阿旺",), (), {"阿旺"}),
                         {"阿旺": "14岁"})

    def test_ageless_mention_uses_the_episode_default(self):
        """不带年龄的点名 ⇒ 绑本集那张表（这才是"这一集用上了新表"）。"""
        s = shots_of("@阿旺（蓝白校服外套）蹲下摸轮胎")[0]
        hits, _ = assets.hits_for_shot(VARIANT_REG, s,
                                       defaults={"阿旺": "14岁"})
        self.assertEqual([h.get("name") for h in hits], ["阿旺（14岁版）"])

    def test_parenthetical_age_beats_the_default(self):
        """闪回镜自己写了 10 岁 ⇒ 括注优先，不能被整集默认盖掉。"""
        s = shots_of("@阿旺（10岁，圆脸，洗白蓝校服短袖）蹲在老樟树下想起从前")[0]
        hits, _ = assets.hits_for_shot(VARIANT_REG, s, defaults={"阿旺": "14岁"})
        self.assertEqual([h.get("name") for h in hits], ["阿旺"])

    def test_two_age_tags_in_one_episode_are_not_guessed(self):
        """本集同时出现两个年龄段 ⇒ 不设默认 + 响亮一行（猜了就会静默锁错脸）。"""
        lines: list = []
        got = variants.default_ages(shots_of(
            "@阿旺（14岁，瘦高身形，蓝白校服外套）站在祠堂门口",
            "@阿旺（10岁，圆脸，洗白蓝校服短袖）蹲在老樟树下",
            "@阿旺（16岁，肩宽，深色夹克）从巷口走过来"),
            ("阿旺",), (), {"阿旺"}, log=lines.append)
        self.assertEqual(got, {})
        self.assertIn("不设默认", " ".join(lines))

    def test_defaults_come_from_the_registry_not_a_handmade_dict(self):
        """生产路径：`assets.episode_defaults(注册表, 本集分镜)` 自己算得出默认年龄段。"""
        self.assertEqual(assets.episode_defaults(VARIANT_REG, shots_of(*self.EP)),
                         {"阿旺": "14岁"})

    def test_registry_without_variant_entries_is_byte_untouched(self):
        """没有变体条目的历史项目 ⇒ 传了默认年龄段也不许改绑定的任何东西。"""
        s = shots_of("@阿旺（蓝色外套）蹲在台阶上，视线落在自己的鞋尖")[0]
        hits, _ = assets.hits_for_shot(BASE_REG, s, defaults={"阿旺": "14岁"})
        self.assertEqual([h.get("name") for h in hits], ["阿旺"])


class MissingVariantSheetTests(unittest.TestCase):
    """年龄表没出图 ⇒ 绑定会静默回落基础卡，这一行必须喊出来。"""

    def test_missing_derived_sheet_is_loud(self):
        from unittest import mock

        d, root = CastWiringTests._root(None, [
            "@阿旺（14岁，瘦高身形，蓝白校服外套）在下铺看手机",
            "@阿旺（14岁，蓝白校服外套）抬头"])
        lines: list = []
        with d, mock.patch.object(cast, "_turnaround",
                                  side_effect=lambda *a, **kw: ""), \
                mock.patch.object(cast, "_single",
                                  side_effect=AssertionError("不该生资产图")):
            cast.ensure(root, log=lines.append, ep=2)
        text = " ".join(lines)
        self.assertIn("阿旺（14岁版）", text)
        self.assertIn("年龄表不在盘上", text,
                      "表没落地却静默回落孩童脸 = 本次要修的病，不许看不见")


if __name__ == "__main__":
    unittest.main(verbosity=2)
