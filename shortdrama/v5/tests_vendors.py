# -*- coding: utf-8 -*-
"""厂商档（vendors）与厂商分发层（providers）的**离线**单测。

不打网络、不依赖 `.env`、不依赖真项目目录。

覆盖四条**最贵的**判据：
  1. **缺省 = agnes**，且行为与改造前逐字节一致（等价性由 `tests_flow` 的 503 分类用例
     与本次独立探针共同背书；这里再钉住"缺省不引入 impl"）。
  2. **未注册厂商 ⇒ 响亮报错，绝不静默回退 agnes** ——
     静默回退 = 「你以为在跑 A，其实跑的是 B」，本项目最忌的「失败不可见」。
  3. **配置档真的改请求**（base / size / 比例字段 / 参考图上限）——
     这是「OpenAI 兼容类 = 改一份配置」这句承诺的唯一证据。
  4. **比例字段允许为空**（`ratio_key=None` / `aspect_key=None`）——
     否则 OpenAI 原生档会把 `size` 撞成重复键、后写覆盖前写（**静默**丢配置）。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .media import jobs
from .media import providers
from . import vendors

#: 本次测试注册的厂商名（tearDown 清理，避免污染其它用例）
_REGISTERED: list[str] = []


def _register(name: str, spec: dict) -> dict:
    vendors.register(name, spec, replace=True)
    _REGISTERED.append(name)
    return spec


class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


class _CapturingClient:
    """替身 httpx.Client：把请求**原样**记下来，不联网。"""

    last: dict = {}

    def __init__(self, **kw):
        self._kw = kw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None):
        type(self).last = {"url": url, "headers": headers, "body": json, "kw": self._kw}
        return _Resp({"data": [{"url": "http://cdn/x.jpg"}],
                      "video_id": "v1", "task_id": "t1"})

    def get(self, url, params=None, headers=None):
        type(self).last = {"url": url, "params": params, "headers": headers}
        return _Resp({"status": "completed", "metadata": {"url": "http://cdn/v.mp4"}})


class VendorBase(unittest.TestCase):
    #: 测试要隔离的环境变量：三条链的选择变量 + 文本通道的旧名 + deepseek 的模型名
    ENVS = (vendors.ENV_KEY["chat"], vendors.ENV_KEY["image"], vendors.ENV_KEY["video"],
            vendors.ENV_LEGACY["chat"], "DEEPSEEK_MODEL")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.ENVS}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        while _REGISTERED:
            vendors._REGISTRY.pop(_REGISTERED.pop(), None)

    def _capture(self, fn, **kw):
        _CapturingClient.last = {}
        with mock.patch.object(providers.httpx, "Client", _CapturingClient):
            getattr(providers, fn)(**kw)
        return dict(_CapturingClient.last)


class TestDefaults(VendorBase):
    """判据 1：缺省即现状。"""

    def test_default_vendor_is_agnes(self):
        self.assertEqual(vendors.current("image"), "agnes")
        self.assertEqual(vendors.current("video"), "agnes")

    def test_agnes_has_no_impl_module(self):
        """agnes 走 providers 内置实现 ⇒ impl_for 必须是 None。

        若这里返回了模块，说明有人给 agnes 挂了实现 ⇒ 缺省路径不再等于改造前行为。
        """
        self.assertIsNone(vendors.impl_for("image"))
        self.assertIsNone(vendors.impl_for("video"))

    def test_blank_env_falls_back_to_agnes(self):
        os.environ[vendors.ENV_KEY["video"]] = "   "
        self.assertEqual(vendors.current("video"), "agnes")

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            vendors.current("audio")

    def test_default_request_targets_agnes_base(self):
        from . import config
        got = self._capture("gen_image", prompt="P")
        self.assertTrue(got["url"].startswith(config.AGNES_BASE), got["url"])
        self.assertTrue(got["url"].endswith("/v1/images/generations"), got["url"])
        self.assertEqual(got["body"]["size"], "1K")
        self.assertIn("ratio", got["body"])


class TestUnknownVendorLoud(VendorBase):
    """判据 2：未注册厂商必须**响亮**报错，不得静默回退。"""

    def test_unregistered_raises_not_fallback(self):
        os.environ[vendors.ENV_KEY["video"]] = "comfy"
        with self.assertRaises(vendors.UnknownVendor) as cm:
            vendors.spec_for("video")
        # 消息必须**可据以行动**：说出已注册的有哪些 + 指到交接单
        msg = str(cm.exception)
        self.assertIn("comfy", msg)
        self.assertIn("agnes", msg)
        self.assertIn("公司机接入ComfyUI-交接单", msg)

    def test_provider_call_raises_too(self):
        """不是只在 `spec_for` 报错 —— 真正调用 providers 也得报错。"""
        os.environ[vendors.ENV_KEY["video"]] = "comfy"
        with self.assertRaises(vendors.UnknownVendor):
            providers.submit_video("p", mode="keyframe", first_frame="http://x/a.png")

    def test_describe_flags_unregistered(self):
        os.environ[vendors.ENV_KEY["image"]] = "nope"
        self.assertIn("未注册", vendors.describe())


class TestRegister(VendorBase):
    """注册语义：重复登记必须报错（静默覆盖会让"注册了却没生效"不可见）。"""

    def test_duplicate_raises(self):
        _register("dup", {"label": "x"})
        with self.assertRaises(ValueError):
            vendors.register("dup", {"label": "y"})

    def test_replace_allows_overwrite(self):
        _register("dup2", {"label": "x"})
        vendors.register("dup2", {"label": "y"}, replace=True)
        self.assertEqual(vendors.get("dup2")["label"], "y")

    def test_empty_name_rejected(self):
        with self.assertRaises(ValueError):
            vendors.register("  ", {})


class TestBaseResolution(VendorBase):
    """`base_for` 三级优先级：base_env > base_from_config > base_default。"""

    def test_env_wins(self):
        _register("b1", {"base_env": "SHORTDRAMA_B1_BASE",
                         "base_default": "https://fallback"})
        os.environ["SHORTDRAMA_B1_BASE"] = "https://from-env"
        os.environ[vendors.ENV_KEY["image"]] = "b1"
        self.addCleanup(os.environ.pop, "SHORTDRAMA_B1_BASE", None)
        self.assertEqual(vendors.base_for("image"), "https://from-env")

    def test_env_empty_falls_to_default(self):
        _register("b2", {"base_env": "SHORTDRAMA_B2_BASE",
                         "base_default": "https://fallback"})
        os.environ.pop("SHORTDRAMA_B2_BASE", None)
        os.environ[vendors.ENV_KEY["image"]] = "b2"
        self.assertEqual(vendors.base_for("image"), "https://fallback")

    def test_agnes_reads_config_live(self):
        """agnes 走 `base_from_config` ⇒ 必须**调用时**读，不能 import 期固化。"""
        from . import config
        with mock.patch.object(config, "AGNES_BASE", "https://patched.example"):
            self.assertEqual(vendors.base_for("image"), "https://patched.example")


class TestConfigDrivenVendor(VendorBase):
    """判据 3：注册一个配置档 ⇒ 请求**真的**随之改变（这一类厂商零代码）。"""

    def _oai_spec(self, **over):
        spec = {"label": "聚合平台", "impl": None,
                "base_env": "SHORTDRAMA_OAI_BASE",
                "image_path": "/v1/images/generations", "image_size": "1024x1024",
                "ratio_key": None, "ref_max": 2,
                "video_path": "/v1/videos", "video_size": "720P", "aspect_key": None,
                "seconds_min": 4, "query_path": "/agnesapi",
                "query_model_key": "model_name"}
        spec.update(over)
        return spec

    def test_base_size_refmax_apply(self):
        _register("oai", self._oai_spec())
        os.environ["SHORTDRAMA_OAI_BASE"] = "https://api.example.com"
        self.addCleanup(os.environ.pop, "SHORTDRAMA_OAI_BASE", None)
        os.environ[vendors.ENV_KEY["image"]] = "oai"

        got = self._capture("gen_image", prompt="P", refs=["r1", "r2", "r3"])
        self.assertEqual(got["url"], "https://api.example.com/v1/images/generations")
        self.assertEqual(got["body"]["size"], "1024x1024")
        # ref_max=2 ⇒ 参考图被截到 2 张
        self.assertEqual(got["body"]["extra_body"]["image"], ["r1", "r2"])

    def test_empty_ratio_key_omits_field(self):
        """判据 4：`ratio_key=None` ⇒ **不写**该字段。

        若仍写 `ratio`，OpenAI 原生档会多出一个它不认识的字段；
        若把它设成 `"size"`，则会与 size 撞键、**静默**覆盖掉配置里的尺寸。
        """
        _register("oai2", self._oai_spec())
        os.environ["SHORTDRAMA_OAI_BASE"] = "https://api.example.com"
        self.addCleanup(os.environ.pop, "SHORTDRAMA_OAI_BASE", None)
        os.environ[vendors.ENV_KEY["image"]] = "oai2"
        got = self._capture("gen_image", prompt="P", ratio="16:9")
        self.assertNotIn("ratio", got["body"])
        self.assertEqual(got["body"]["size"], "1024x1024")

    def test_video_empty_aspect_key_and_seconds_min(self):
        _register("oai3", self._oai_spec(seconds_min=5))
        os.environ["SHORTDRAMA_OAI_BASE"] = "https://api.example.com"
        self.addCleanup(os.environ.pop, "SHORTDRAMA_OAI_BASE", None)
        os.environ[vendors.ENV_KEY["video"]] = "oai3"
        got = self._capture("submit_video", prompt="P", mode="keyframe",
                            first_frame="f", seconds=2)
        self.assertNotIn("aspect_ratio", got["body"])
        # 下限取厂商档 seconds_min=5（agas 是 4）⇒ 传 2 被抬到 5
        self.assertEqual(got["body"]["seconds"], "5")

    def test_query_uses_vendor_path(self):
        _register("oai4", self._oai_spec(query_path="/tasks"))
        os.environ["SHORTDRAMA_OAI_BASE"] = "https://api.example.com"
        self.addCleanup(os.environ.pop, "SHORTDRAMA_OAI_BASE", None)
        os.environ[vendors.ENV_KEY["video"]] = "oai4"
        got = self._capture("query_video", video_id="v")
        self.assertEqual(got["url"], "https://api.example.com/tasks")
        self.assertEqual(got["params"]["video_id"], "v")


class _FakeImpl:
    """假的自有协议实现模块（证明 `impl` 委派通路可用，且**不碰内置 httpx**）。"""

    calls: list = []

    @staticmethod
    def submit_video(prompt, **kw):
        _FakeImpl.calls.append((prompt, kw))
        return {"video_id": "fake-1", "task_id": "fake-1"}

    @staticmethod
    def query_video(video_id, **kw):
        return {"status": "completed", "url": "http://fake/v.mp4"}


class TestImplDelegation(VendorBase):
    """自有协议厂商：`impl` 非空 ⇒ 委派给它，**不**走内置实现。"""

    def test_impl_takes_over(self):
        _register("mine", {"label": "自有协议", "impl": _FakeImpl})
        os.environ[vendors.ENV_KEY["video"]] = "mine"
        _FakeImpl.calls = []
        with mock.patch.object(providers.httpx, "Client",
                               side_effect=AssertionError("不该碰 httpx")):
            r = providers.submit_video("hello", mode="keyframe",
                                       first_frame="f", seconds=6)
        self.assertEqual(r["video_id"], "fake-1")
        self.assertEqual(_FakeImpl.calls[0][0], "hello")
        self.assertEqual(_FakeImpl.calls[0][1]["seconds"], 6)

    def test_query_delegates(self):
        _register("mine2", {"label": "自有协议", "impl": _FakeImpl})
        os.environ[vendors.ENV_KEY["video"]] = "mine2"
        with mock.patch.object(providers.httpx, "Client",
                               side_effect=AssertionError("不该碰 httpx")):
            r = providers.query_video("fake-1")
        self.assertEqual(r["url"], "http://fake/v.mp4")


class TestProvenance(VendorBase):
    """产地记录：能判断"这份是谁产的"（只写不读，不参与任何判据）。"""

    def test_jobs_mark_records_video_vendor(self):
        j = {}
        jobs.mark(j, "LN01", "submitted", video_id="v1")
        self.assertEqual(j["LN01"]["video_vendor"], "agnes")

    def test_jobs_mark_follows_current_vendor(self):
        """重渲换了厂商 ⇒ 记录要反映**现在的产出方**（覆盖写，不是 setdefault）。"""
        _register("other", {"label": "别家"})
        j = {}
        jobs.mark(j, "LN01", "submitted", video_id="v1")
        os.environ[vendors.ENV_KEY["video"]] = "other"
        jobs.mark(j, "LN01", "completed", local="x")
        self.assertEqual(j["LN01"]["video_vendor"], "other")

    def test_unknown_sentinel_is_not_empty(self):
        """`UNKNOWN` 与"字段缺失"必须可区分 —— 不把"查不出"伪装成"没有"。"""
        self.assertTrue(vendors.UNKNOWN)
        self.assertNotEqual(vendors.UNKNOWN, "agnes")


class TestChatLane(VendorBase):
    """文本通道**已并入同一机制**（2026-09-18 统一）。

    本轮的核心目的：**去掉「未注册就静默回退 agnes」** ——
    旧行为会让"以为在跑别家、其实一直是 agnes"完全不可见。
    """

    def test_default_is_agnes_with_v1_suffix(self):
        """缺省 = agnes；`base_suffix=/v1` 是文本通道特有的（媒体侧路径里自带）。"""
        from . import config
        p = vendors.chat_spec()
        self.assertEqual(p["base_url"], config.AGNES_BASE + "/v1")
        self.assertEqual(p["model"], config.MODELS["chat"])
        self.assertEqual(p["api_key"], config.AGNES_API_KEY)

    def test_values_are_read_live(self):
        """一律**调用时**读 config —— 否则又成了第二份真相源。"""
        from . import config
        with mock.patch.object(config, "AGNES_BASE", "https://x.example"), \
                mock.patch.object(config, "MODELS", {"chat": "m-test", "image": "i", "video": "v"}):
            p = vendors.chat_spec()
        self.assertEqual(p["base_url"], "https://x.example/v1")
        self.assertEqual(p["model"], "m-test")

    def test_unknown_vendor_raises_not_fallback(self):
        """★ 本轮的**目的**：旧实现此处会静默回退 agnes。"""
        os.environ[vendors.ENV_KEY["chat"]] = "not-a-real-vendor"
        with self.assertRaises(vendors.UnknownVendor):
            vendors.chat_spec()

    def test_legacy_env_still_works(self):
        """旧名 `NEWDEEP_LLM_PROVIDER` 兼容 —— 不破已有 `.env`。"""
        os.environ[vendors.ENV_LEGACY["chat"]] = "deepseek"
        os.environ["DEEPSEEK_MODEL"] = "deepseek-chat"
        self.assertEqual(vendors.current("chat"), "deepseek")
        self.assertEqual(vendors.chat_spec()["model"], "deepseek-chat")

    def test_new_env_wins_over_legacy(self):
        os.environ[vendors.ENV_LEGACY["chat"]] = "deepseek"
        os.environ[vendors.ENV_KEY["chat"]] = "agnes"
        self.assertEqual(vendors.current("chat"), "agnes")

    def test_registered_but_empty_model_errors_with_hint(self):
        """已注册但模型名为空 ⇒ `VendorConfigError`，且**指名要填哪个变量**。"""
        os.environ[vendors.ENV_KEY["chat"]] = "deepseek"
        with self.assertRaises(vendors.VendorConfigError) as cm:
            vendors.chat_spec()
        self.assertIn("DEEPSEEK_MODEL", str(cm.exception))

    def test_vendor_without_chat_section_raises(self):
        """只有媒体能力的厂商被选进文本通道 ⇒ 也是响亮报错。"""
        _register("mediaonly", {"label": "只有媒体能力"})
        os.environ[vendors.ENV_KEY["chat"]] = "mediaonly"
        with self.assertRaises(vendors.UnknownVendor) as cm:
            vendors.chat_spec()
        self.assertIn("chat", str(cm.exception))

    def test_chat_for_builds_client_from_vendor_spec(self):
        """端到端：`chat_for()` 拿到的客户端与厂商档一致（不发网络）。"""
        from . import config, llm
        m = llm.chat_for()
        self.assertEqual(m.model_name, config.MODELS["chat"])
        self.assertEqual(m.openai_api_base, config.AGNES_BASE + "/v1")

    def test_role_override_still_works(self):
        """`ROLE_PROVIDER` per-role 覆盖是统一前就有的能力，不能破。"""
        from . import config, llm
        os.environ["DEEPSEEK_MODEL"] = "deepseek-chat"
        config.ROLE_PROVIDER["reviewer"] = "deepseek"
        try:
            self.assertEqual(llm.role_chat("reviewer").model_name, "deepseek-chat")
            self.assertEqual(llm.role_chat("director").model_name, config.MODELS["chat"])
        finally:
            config.ROLE_PROVIDER.pop("reviewer", None)

    def test_describe_covers_three_lanes(self):
        d = vendors.describe()
        for k in ("chat=", "image=", "video="):
            self.assertIn(k, d)


if __name__ == "__main__":
    unittest.main()
