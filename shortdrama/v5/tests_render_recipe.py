# -*- coding: utf-8 -*-
"""「这一版要不要重渲」的判据回归（`pipeline.needs_rerender` / `_render_recipe`）。

为什么单独一个文件（而不是加进 `tests_flow.py`）：那条判据决定**会不会再烧一遍视频配额**，
而 2026-09-30 它漏了"出片配方"这一半，代价是 huashan-duel-v4-0928 第 2 集
先出成竖屏 + 只渲前 20 镜、补对参数后又被门挡回。`tests_flow.py` 当时有别的会话在改，
不往同一个文件里写。
"""
from __future__ import annotations

import unittest
from unittest import mock

from .media import pipeline


class RecipeTests(unittest.TestCase):
    def _set(self, aspect="16:9", mode="pack", cap=40, ratio="16:9"):
        return mock.patch.multiple(pipeline.config, ASPECT_RATIO=aspect, VIDEO_MODE=mode,
                                   VIDEO_MAX_SHOTS=cap, STILL_RATIO=ratio)

    def test_recipe_covers_every_param_that_changes_the_picture(self):
        """四个参数各自变一个，配方串就必须变 —— 漏一个就等于那一类改动仍会被门挡。"""
        with self._set():
            base = pipeline._render_recipe()
        for kw in ({"aspect": "9:16"}, {"mode": "reference"}, {"cap": 20}, {"ratio": "3:4"}):
            args = dict(aspect="16:9", mode="pack", cap=40, ratio="16:9")
            args.update(kw)
            with self._set(**args):
                self.assertNotEqual(pipeline._render_recipe(), base,
                                    "参数 %s 变了但配方串没变 ⇒ 门会当作什么都没改" % list(kw))

    def test_recipe_stable_for_same_params(self):
        with self._set(), self._set():
            self.assertEqual(pipeline._render_recipe(), pipeline._render_recipe())


class NeedsRerenderTests(unittest.TestCase):
    FP = "CURRENT"
    RECIPE = "16:9|pack|40|16:9"

    def _why(self, ml, only=None):
        return pipeline.needs_rerender(ml, fp_now=self.FP, recipe=self.RECIPE, only=only)

    def test_explicit_shot_rerender_always_counts(self):
        self.assertEqual(self._why({"rendered": True, "input_fingerprint": self.FP,
                                    "recipe": self.RECIPE}, only=["LN03"]), "显式")

    def test_never_rendered_needs_no_revision(self):
        self.assertEqual(self._why({}), "")

    def test_changed_artifacts_count(self):
        self.assertEqual(self._why({"rendered": True, "input_fingerprint": "OLD",
                                    "recipe": self.RECIPE}), "产物")

    def test_plain_re_run_with_nothing_changed_is_blocked(self):
        """反向对照：什么都不变时**不许**放行整集重渲（否则每次误点都烧一遍配额）。"""
        self.assertEqual(self._why({"rendered": True, "input_fingerprint": self.FP,
                                    "recipe": self.RECIPE}), "")

    def test_recipe_change_counts_even_when_artifacts_untouched(self):
        """★ 0930 那一集的回归：分镜/静帧一个没变，只是画幅与镜数上限改对了。"""
        self.assertEqual(self._why({"rendered": True, "input_fingerprint": self.FP,
                                    "recipe": "9:16|reference|20|3:4"}), "配方")

    def test_legacy_record_without_recipe_is_NOT_a_rerender(self):
        """老项目（记录里没 `recipe` 键）不因升级代码被重烧一整集 —— 保守方向。

        取舍写进判据注释：这一类要重渲走 `--rerender <镜号>`，它等价于一次显式修订请求。
        """
        self.assertEqual(self._why({"rendered": True, "input_fingerprint": self.FP}), "")


if __name__ == "__main__":
    unittest.main()
