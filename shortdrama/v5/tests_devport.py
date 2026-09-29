# -*- coding: utf-8 -*-
"""多项目并行的前提：起服端口**自动挑空闲的、起服前一个进程都不杀**。

病根（2026-09-29 实测）：所有项目共用 2024，而 `dev server` 编译期绑着自己的项目。
旧代码起服前调 `ensure_port_free()` → `release_port()` → `taskkill` 掉占口进程。
串行时清的是上一个项目留下的陈旧 server，是对的；**并行时占口的那条就是另一条在跑的链**
—— 等于一边并行一边互相拆台。改成换口之后，那个失效面连同"陈旧监听骗过 `wait_ok()`"
一起消失（`AUTO_PORTS` 刻意避开 2024）。

跑法：`python -m unittest v5.tests_devport -v`
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_new_project_under_test", _ROOT / "scripts" / "run_new_project.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPickDevPort(unittest.TestCase):
    def setUp(self):
        self.m = _load_runner()
        self.m.DEV_PORT_PINNED = False
        self.m.DEV_PORT = 2024
        self.m.RUN_PORT = 2024
        self.killed = []
        # ★ 起服路径上一律不许杀进程：谁被调用就直接失败，而不是悄悄绕过去
        self.m._kill_tree = lambda pid: self.killed.append(pid) or "不该被调用"

    def _occupy(self, busy):
        self.m.port_free = lambda port=None: port not in busy

    def test_auto_pick_skips_occupied_ports(self):
        busy = set(self.m.AUTO_PORTS) - {2091}
        self._occupy(busy)
        self.assertEqual(self.m.pick_dev_port("any-project"), 2091)
        self.assertEqual(self.m.RUN_PORT, 2091)          # 派发目标跟着它走
        self.assertEqual(self.killed, [])

    def test_two_projects_started_together_do_not_collide(self):
        self._occupy(set())                              # 全空闲 = 只看错开的扫描起点
        a = self.m.pick_dev_port("par-a-0929")
        self._occupy(set())
        b = self.m.pick_dev_port("par-b-0929")
        self.assertNotEqual(a, b, "同名以外的项目在同秒起跑时必须拿到不同端口")
        self.assertEqual(self.killed, [])

    def test_pinned_port_is_honored_and_never_grabbed(self):
        self.m.DEV_PORT_PINNED = True
        self.m.DEV_PORT = 2024
        self._occupy({2024})
        self.assertIsNone(self.m.pick_dev_port("par-a-0929"))   # 响亮失败
        self.assertEqual(self.m.RUN_PORT, 2024)                 # 不偷偷换口
        self.assertEqual(self.killed, [], "显式端口被占也不许杀别人")

    def test_pinned_free_port_is_used(self):
        self.m.DEV_PORT_PINNED = True
        self.m.DEV_PORT = 2024
        self._occupy({2080})                             # 自动段被占也不影响显式口
        self.assertEqual(self.m.pick_dev_port("par-a-0929"), 2024)

    def test_exhausted_range_returns_none(self):
        self._occupy(set(self.m.AUTO_PORTS))
        self.assertIsNone(self.m.pick_dev_port("par-a-0929"))
        self.assertEqual(self.killed, [])

    def test_auto_range_avoids_the_default_port(self):
        # 前端 webchain.py 与手工 `langgraph dev` 都默认粘在 2024；自动段撞它就等于
        # 把"陈旧监听骗 wait_ok"那个老病又请回来。
        self.assertNotIn(2024, self.m.AUTO_PORTS)


class TestDispatchTargetFollowsPort(unittest.TestCase):
    """换端口后派发目标必须跟着变 —— 否则 config 默认的 2024 会让派发静默打错地方。"""

    def test_agent_url_uses_run_port(self):
        m = _load_runner()
        m.RUN_PORT = 2087
        env = m._env("some-project", qc_mode="full")
        self.assertEqual(env["SHORTDRAMA_V5_AGENT_URL"], "http://127.0.0.1:2087")

    def test_default_helpers_follow_run_port(self):
        m = _load_runner()
        m.RUN_PORT = 2093
        seen = []
        m.port_free = lambda port=None: seen.append(m._p(port)) or True
        m.port_free()
        self.assertEqual(seen, [2093], "无参调用必须解析成本次真正用的端口，不是常量 2024")


if __name__ == "__main__":
    unittest.main(verbosity=2)
