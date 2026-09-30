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


if __name__ == "__main__":
    unittest.main(verbosity=2)
