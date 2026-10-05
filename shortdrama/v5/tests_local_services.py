# -*- coding: utf-8 -*-
"""本机 / 局域网出片服务（ComfyUI 接入）的**离线**测试 —— 用一台本机假 ComfyUI 把
整条请求路径真跑一遍。

## 为什么必须有这台假机器

`v5/media/vendors/comfyui.py` 是这个仓库**第一个真正跑起来的非内置厂商**
（`v5/media/vendors/` 这个子包在 2026-10-05 之前是空的）。它第一次接触真 ComfyUI
是在用户的 GPU 机器上，而那台机器：

  · 不在同一局域网（开发机 1660 6GB 跑不动 H3，只能拿安装包过去试）；
  · 出错的代价是一整轮出片（小时级、且云端额度已经烧掉一半）。

所以「ComfyUI 会怎么回答」必须在**这台机器上**先验一遍。假机器按真实形状回答：
`/system_stats` `/object_info` `/upload/image` `/prompt` `/queue`
`/history/<id>` `/view?filename=`。真机不符时，差异会显在「假机器按 X 回答、
真机按 Y 回答」这一层，而不是藏在网络抖动里。

## 测试盯的四类病（都是本项目栽过的形状）

1. **失败不可见** —— 探不到必须给一句人话（连不上 / 不是 ComfyUI / 版本太旧），
   不许回一个空列表让人以为「我这台机没装」。
2. **静默换档 / 静默丢参数** —— mapping 里少一个图位、reference 档多张图被丢、
   秒数被钳制，三种都必须**出声**。
3. **两份真相源** —— 厂商选择只能由 `SHORTDRAMA_VIDEO_VENDOR` 决定；
   接入档案只是它的**写入者之一**，且**人显式设的环境变量必须赢过档案**
   （A/B 靠单变量切换，那是这个项目的命根子）。
4. **夹具解析出 0 条却全绿** —— 每个断言都数得出条数（mapping 认到 5 项、
   假机器返回 1 个设备 3 个 H3 节点），不是 `assertTrue(items)` 那种空转。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest import mock

import httpx

from . import config, local_services, vendors
from .media import jobs as jobs_mod
from .media import providers
from .media.vendors import comfyui

#: 每个用例都要还原的全局状态（注册表 + 环境变量）
_ENV_KEYS = ("SHORTDRAMA_VIDEO_VENDOR", "SHORTDRAMA_IMAGE_VENDOR")


# ─── 假 ComfyUI ─────────────────────────────────────────────────────────────

def _api_graph() -> dict:
    """一份 **API 格式**的工作流（ComfyUI 菜单 Workflow → Export (API) 的形状）。

    字段名故意用 H3 那批节点的真实叫法（`text` / `image` / `width` / `height`
    / `length`），因为 `suggest_mapping` 认的就是这些名字。
    """
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": "残留旧图.png"}},
        "2": {"class_type": "MiniMaxH3Fl2vaSampler",
              "inputs": {"text": "残留提示词", "width": 512, "height": 512,
                         "length": 33}},
        "9": {"class_type": "VHS_VideoCombine", "inputs": {"video": ["2", 0]}},
    }


class _FakeState:
    """假机器的可控状态（测试用它制造「还没跑完」「任务丢了」等分支）。"""

    def __init__(self, kind="comfyui"):
        self.kind = kind                     # comfyui | not_comfyui
        self.history_ready = False           # False ⇒ /history 回空（还在排队）
        self.in_queue = True                 # 与 history_ready 组合出三种状态
        self.uploaded = []                   # 收到过几张图
        self.prompts = []                    # 收到过哪些 /prompt 的图
        self.last_prompt_id = "pid-fixed-1"
        self.reject_next = False             # 下一次 /prompt 返 400

    # —— 输出文件（`/view` 给的就是这串字节）
    OUTPUT_BYTES = b"FAKE-MP4-BYTES-0123456789"


class _Handler(BaseHTTPRequestHandler):
    state: _FakeState = _FakeState()      # 类属性，由用例赋值

    def log_message(self, *_a):           # 静音
        pass

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        st = type(self).state
        if st.kind == "not_comfyui":
            self._json({"service": "something-else"})
            return
        if u.path == "/system_stats":
            self._json({
                "system": {"os": "Windows", "comfyui_version": "0.3.40",
                           "python_version": "3.12.4"},
                "devices": [{"name": "NVIDIA GeForce RTX 3060", "type": "cuda",
                             "vram_total": 12 * 1024 ** 3,
                             "vram_free": 9 * 1024 ** 3}],
            })
            return
        if u.path == "/object_info":
            self._json({
                "MiniMaxH3Fl2vaSampler": {
                    "input": {"required": {"text": ["STRING"],
                                           "width": ["INT"], "height": ["INT"],
                                           "length": ["INT"]},
                              "optional": {"image": ["IMAGE"]}}},
                "CheckpointLoaderSimple": {
                    "input": {"required": {"ckpt_name": [["minimax_h3_v1.safetensors",
                                                          "other.safetensors"]]}}},
                "VHS_VideoCombine": {"input": {"required": {"videos": ["VIDEO"]}}},
            })
            return
        if u.path == "/queue":
            running = [[1, st.last_prompt_id, {}, [], None]] if (
                st.in_queue and not st.history_ready) else []
            self._json({"queue_running": running, "queue_pending": []})
            return
        if u.path.startswith("/history/"):
            pid = u.path.split("/history/", 1)[1]
            if not st.history_ready or pid != st.last_prompt_id:
                self._json({})
                return
            self._json({pid: {"outputs": {"9": {"videos": [
                            {"filename": "h3_clip.mp4", "subfolder": "",
                             "type": "output"}]}},
                        "status": {"completed": True, "status_str": "success"}}})
            return
        if u.path == "/view":
            q = parse_qs(u.query)
            body = st.OUTPUT_BYTES
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json({"error": "no such path"}, code=404)

    def do_POST(self):
        u = urlparse(self.path)
        st = type(self).state
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if u.path == "/upload/image":
            st.uploaded.append(raw)
            self._json({"name": "ref.png", "subfolder": "", "type": "input"})
            return
        if u.path == "/prompt":
            if st.reject_next:
                st.reject_next = False
                self._json({"error": {"type": "invalid_prompt",
                                      "message": "节点 2 缺少必需输入"},
                            "node_errors": {"2": {"errors": ["missing"]}}}, code=400)
                return
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:  # noqa: BLE001
                self._json({"error": {"message": "body 不是 JSON"}}, code=400)
                return
            st.prompts.append(payload.get("prompt") or {})
            self._json({"prompt_id": st.last_prompt_id, "number": 1,
                        "node_errors": {}})
            return
        self._json({"error": "no such path"}, code=404)


class _Server:
    """随机端口的本机假 ComfyUI（127.0.0.1，不碰真网络）。"""

    def __init__(self, kind="comfyui"):
        self.state = _FakeState(kind)
        _Handler.state = self.state
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def address(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)


# ─── 用例底座：临时 RUNTIME_ROOT + 还原全局状态 ─────────────────────────────

class _Isolated(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in _ENV_KEYS}
        self._registry = dict(vendors._REGISTRY)
        self._tmp = tempfile.mkdtemp(prefix="abfixture_localmedia_")
        self._cfg = mock.patch.object(config, "RUNTIME_ROOT", Path(self._tmp))
        self._cfg.start()
        # 每个用例的默认起点：settings.env 写着出厂 agnes（打包版就是这个形状）
        self._patch_factory = mock.patch.object(
            local_services, "settings_env_value",
            lambda name: "agnes" if name == "SHORTDRAMA_VIDEO_VENDOR" else "")
        self._patch_factory.start()

    def tearDown(self):
        self._patch_factory.stop()
        self._cfg.stop()
        vendors._REGISTRY.clear()
        vendors._REGISTRY.update(self._registry)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)


# ─── 1. 探测 ────────────────────────────────────────────────────────────────

class TestProbe(_Isolated):
    def test_finds_comfyui_with_device_and_h3_nodes(self):
        srv = _Server()
        self.addCleanup(srv.stop)
        got = local_services.probe_one(srv.address)
        self.assertTrue(got["reachable"], got["reason"])
        self.assertEqual(got["kind"], "comfyui")
        self.assertEqual(got["comfyui_version"], "0.3.40")
        # ★ 数得出条数：1 个设备（12.0 GB）、1 个 H3 节点、1 个 H3 模型文件
        self.assertEqual(len(got["devices"]), 1)
        self.assertEqual(got["devices"][0]["vram_gb"], 12.0)
        self.assertEqual(got["h3_nodes"], ["MiniMaxH3Fl2vaSampler"], got["h3_nodes"])
        self.assertEqual(got["model_files"], ["minimax_h3_v1.safetensors"],
                         got["model_files"])

    def test_dead_port_says_why_not_an_empty_result(self):
        """阴性对照：端口上没东西 ⇒ 必须给一句**人话**，不许回空列表。

        探不到时用户只能看到这句 reason。写成「未检测到本地服务」就会逼人重装
        ComfyUI，而真凶常常是「没开 / 端口填错 / 跨网不通」。
        """
        free = _Server()
        port = free.port
        free.stop()
        got = local_services.probe_one("http://127.0.0.1:%d" % port)
        self.assertFalse(got["reachable"])
        self.assertTrue(got["reason"].strip(), "reason 不能是空串")
        self.assertIn("连不上", got["reason"])

    def test_other_http_service_is_not_called_comfyui(self):
        srv = _Server(kind="not_comfyui")
        self.addCleanup(srv.stop)
        got = local_services.probe_one(srv.address)
        self.assertFalse(got["reachable"])
        self.assertNotEqual(got["kind"], "comfyui")
        self.assertIn("不是 ComfyUI", got["reason"])

    def test_probe_lists_tried_addresses_for_the_panel(self):
        """面板要能看到「我试了哪几个、各为什么不通」。"""
        srv = _Server()
        self.addCleanup(srv.stop)
        out = local_services.probe(addresses=[srv.address, "http://127.0.0.1:1"])
        self.assertEqual(len(out["found"]), 1)
        # 候选 = 本机默认端口 8188 + 手填的两条
        self.assertEqual(len(out["tried"]), 3, out["tried"])
        self.assertTrue(all(t["reason"] for t in out["tried"] if not t["reachable"]))


# ─── 2. 接入 ────────────────────────────────────────────────────────────────

class TestConnect(_Isolated):
    def test_mapping_is_recognized_from_the_graph(self):
        """自动认参数落点：五个角色全部认出来（不是「认到一些」）。"""
        sug = local_services.suggest_mapping(_api_graph())
        self.assertEqual(sorted(sug["mapping"]),
                         ["frames", "height", "image", "prompt", "width"])
        self.assertEqual(sug["missing"], [])
        self.assertEqual(sug["mapping"]["frames"]["node"], "2")
        self.assertEqual(sug["mapping"]["frames"]["field"], "length")

    def test_connect_registers_vendor_and_persists_profile(self):
        srv = _Server()
        self.addCleanup(srv.stop)
        res = local_services.connect(srv.address, _api_graph())
        self.assertTrue(res["ok"])
        # 注册表里真有一条，且指向自实现模块（不是内置 None）
        self.assertIn(local_services.VENDOR, vendors.names())
        self.assertIsNotNone(vendors.get(local_services.VENDOR).get("impl"))
        self.assertTrue(local_services.profile_path().is_file())
        # 默认档切到本地：分发点必须真的走到那个模块（`vendors.impl_for`）
        self.assertEqual(vendors.current("video"), local_services.VENDOR)
        self.assertIs(vendors.impl_for("video"), comfyui)

    def test_gui_format_workflow_is_refused_with_the_right_instruction(self):
        srv = _Server()
        self.addCleanup(srv.stop)
        gui = {"nodes": [{"id": 1}], "links": []}
        with self.assertRaises(ValueError) as cm:
            local_services.connect(srv.address, gui)
        self.assertIn("Export (API)", str(cm.exception))
        # 被拒的接入**不能留下档案**（留了下次就当真接过了）
        self.assertFalse(local_services.profile_path().is_file())

    def test_unreachable_address_never_writes_a_profile(self):
        free = _Server()
        port = free.port
        free.stop()
        with self.assertRaises(ValueError):
            local_services.connect("http://127.0.0.1:%d" % port, _api_graph())
        self.assertFalse(local_services.profile_path().is_file())


# ─── 3. 提交 / 轮询 / 取回 ───────────────────────────────────────────────────

class TestSubmitAndQuery(_Isolated):
    def _connect(self, srv, **kw):
        with mock.patch("builtins.print", lambda *a, **k: None):
            local_services.connect(srv.address, _api_graph(), **kw)

    def _local_still(self) -> str:
        p = Path(self._tmp) / "still.png"
        p.write_bytes(b"PNG-FAKE")
        return str(p)

    def test_still_is_pushed_to_comfyui_instead_of_being_fetched_by_it(self):
        """★ 静帧在云端，由我们把字节推给 ComfyUI —— 不要求 GPU 机出网。

        这里用假机器的 `/view` 当「云端静帧 URL」，验的是**我们这侧下载后再上传**
        这一段：GPU 机不需要能打到图片 CDN（跨网、公司内网、离线机都打不到）。
        """
        srv = _Server()
        self.addCleanup(srv.stop)
        self._connect(srv)
        out = providers.submit_video("测试提示词", first_frame=srv.address + "/view?filename=s.png",
                                     seconds=6, mode="keyframe")
        self.assertEqual(out["video_id"], srv.state.last_prompt_id)
        self.assertEqual(len(srv.state.uploaded), 1,
                         "应当有 1 张图被推给 ComfyUI 的 /upload/image")
        graph = srv.state.prompts[-1]
        self.assertEqual(graph["2"]["inputs"]["text"], "测试提示词")
        self.assertEqual(graph["1"]["inputs"]["image"], "ref.png")

    def test_local_still_path_is_read_from_disk(self):
        srv = _Server()
        self.addCleanup(srv.stop)
        self._connect(srv)
        providers.submit_video("p", first_frame=self._local_still(), seconds=4)
        self.assertEqual(len(srv.state.uploaded), 1)

    def test_seconds_land_on_the_frame_grid_the_workflow_needs(self):
        """秒数 → 帧数：6 秒 @24fps = 144 帧，收到 4n+1 网格上是 145。"""
        srv = _Server()
        self.addCleanup(srv.stop)
        self._connect(srv)
        providers.submit_video("p", first_frame=self._local_still(), seconds=6)
        self.assertEqual(srv.state.prompts[-1]["2"]["inputs"]["length"], 145)

    def test_clamping_is_announced_not_silent(self):
        """★ 旧病装回去（2026-09-16：误设上限把 620 镜里 9 镜**静默**压短）。

        这里给 30 秒、本机上限 12 ⇒ 必须压，但必须**出声**。
        只断言「压了」不够 —— 静默压短同样压了，区别全在那一行日志。
        """
        srv = _Server()
        self.addCleanup(srv.stop)
        self._connect(srv, seconds_min=4, seconds_max=12)
        buf = []
        with mock.patch("builtins.print", lambda *a, **k: buf.append(str(a))):
            providers.submit_video("p", first_frame=self._local_still(), seconds=30)
        text = " ".join(buf)
        self.assertIn("超过本机上限", text)
        self.assertEqual(srv.state.prompts[-1]["2"]["inputs"]["length"],
                         comfyui.seconds_to_frames(12, 24, "4n+1"))

    def test_reference_images_beyond_the_one_slot_are_announced(self):
        """pack / reference 档给 3 张图，本机只有 1 个图位 ⇒ 丢 2 张必须说出来。"""
        srv = _Server()
        self.addCleanup(srv.stop)
        self._connect(srv)
        still = self._local_still()
        buf = []
        with mock.patch("builtins.print", lambda *a, **k: buf.append(str(a))):
            providers.submit_video("p", mode="reference",
                                   images=[still, still, still])
        self.assertIn("已丢弃", " ".join(buf))
        self.assertEqual(len(srv.state.uploaded), 1)

    def test_query_three_states_processing_completed_lost(self):
        srv = _Server()
        self.addCleanup(srv.stop)
        self._connect(srv)
        pid = providers.submit_video("p", first_frame=self._local_still())["video_id"]
        # ① 还在队列里、history 空 ⇒ processing（不许报 failed，那是误杀）
        self.assertEqual(providers.query_video(pid)["status"], "processing")
        # ② 跑完了 ⇒ completed + 一个能 GET 到字节的 url
        srv.state.history_ready = True
        q = providers.query_video(pid)
        self.assertEqual(q["status"], "completed")
        self.assertTrue(q["url"].endswith("type=output"))
        with httpx.Client(trust_env=False) as c:
            body = c.get(q["url"]).content
        self.assertEqual(body, _FakeState.OUTPUT_BYTES)
        # ③ history 与 queue 都没有 ⇒ 判 failed（不能报 processing）
        #    报 processing 会把「ComfyUI 中途重启洗了队列」伪装成「还在慢慢跑」,
        #    一路轮询到超时才死，白等半小时。
        srv.state.history_ready = False
        srv.state.in_queue = False
        self.assertEqual(providers.query_video(pid)["status"], "failed")

    def test_submit_rejection_is_raised_with_the_server_message(self):
        srv = _Server()
        self.addCleanup(srv.stop)
        self._connect(srv)
        srv.state.reject_next = True
        with self.assertRaises(RuntimeError) as cm:
            providers.submit_video("p", first_frame=self._local_still())
        self.assertIn("节点 2 缺少必需输入", str(cm.exception))

    def test_image_capability_is_refused_not_attributeerror(self):
        """选了它生图 ⇒ 要给「把图片厂商切回 agnes」，不是裸 AttributeError。

        ⚠️ 这里**直接调实现模块**，不走 `providers.gen_image` —— 后者按
        `SHORTDRAMA_IMAGE_VENDOR` 分发，而接入只动视频档 ⇒ 走 `providers` 会打到
        内置的 agnes 实现，那就是**一次真联网、真烧额度**的测试。
        （我第一版就是这么写的，跑出来才发现它在替我生图。）
        """
        srv = _Server()
        self.addCleanup(srv.stop)
        self._connect(srv)
        with self.assertRaises(ValueError) as cm:
            comfyui.gen_image("画一张")
        self.assertIn("SHORTDRAMA_IMAGE_VENDOR", str(cm.exception))

    def test_connect_switches_video_only_and_leaves_image_on_cloud(self):
        """「只视频走本地，静帧仍云端」这条决定在 env 层的形状。"""
        srv = _Server()
        self.addCleanup(srv.stop)
        self._connect(srv)
        self.assertEqual(vendors.current("video"), local_services.VENDOR)
        self.assertEqual(vendors.current("image"), vendors.DEFAULT)
        st = local_services.status()
        self.assertEqual(st["current_image_vendor"], vendors.DEFAULT)
        self.assertTrue(any("静帧" in n for n in st["notes"]), st["notes"])


# ─── 4. 默认档的归属（两份真相源的战场）────────────────────────────────────

class TestDefaultPrecedence(_Isolated):
    def test_explicit_env_wins_over_the_profile(self):
        """★ A/B 的命根子：人显式设过环境变量，档案不得覆盖。"""
        os.environ["SHORTDRAMA_VIDEO_VENDOR"] = "agnes"
        profile = {"vendor": local_services.VENDOR, "address": "http://x",
                   "default": True}
        action, msg = local_services.decide(profile)
        # settings.env 出厂值就是 agnes ⇒ 与它相同的值**不能**当成「人显式设的」，
        # 否则打包版永远覆盖不掉出厂档，点「接入」等于没点。
        self.assertEqual(action, "apply", msg)
        os.environ["SHORTDRAMA_VIDEO_VENDOR"] = "deepseek"
        action, msg = local_services.decide(profile)
        self.assertEqual(action, "keep")
        self.assertIn("deepseek", msg)

    def test_apply_at_import_registers_and_switches_without_a_second_source(self):
        srv = _Server()
        self.addCleanup(srv.stop)
        local_services.connect(srv.address, _api_graph())
        os.environ["SHORTDRAMA_VIDEO_VENDOR"] = "agnes"
        with mock.patch("builtins.print", lambda *a, **k: None):
            note = local_services.apply_at_import()
        self.assertIn(local_services.VENDOR, vendors.names())
        self.assertEqual(vendors.current("video"), local_services.VENDOR)
        self.assertIn("本机接入档案", note)

    def test_disconnect_restores_cloud_and_removes_files(self):
        srv = _Server()
        self.addCleanup(srv.stop)
        local_services.connect(srv.address, _api_graph())
        os.environ.pop("SHORTDRAMA_VIDEO_VENDOR", None)
        local_services.apply_at_import()
        with mock.patch("builtins.print", lambda *a, **k: None):
            out = local_services.disconnect()
        self.assertEqual(out["message"].count(local_services.VENDOR), 0)
        self.assertEqual(vendors.current("video"), vendors.DEFAULT)
        self.assertNotIn(local_services.VENDOR, vendors.names())
        self.assertFalse(local_services.profile_path().exists())

    def test_builtin_agnes_can_never_be_unregistered(self):
        with self.assertRaises(ValueError):
            vendors.unregister(vendors.DEFAULT)


class TestVendorOriginGuard(_Isolated):
    """★ 换厂商后，旧厂商产的片段**不许静默复用** —— 混血片这一类病的封口。

    旧判据只有「state=completed 且文件在盘」两维，`video_jobs.json` 里那一维
    `video_vendor`（2026-09-18 就在写）**从来没人读**。接了本机 ComfyUI 之后它才
    开始咬人：云端跑完 10 镜、切本地续跑，那 10 个云端片段会被原样拼进
    「名义上由本机 H3 产的」成片里，日志全绿。
    """

    def setUp(self):
        super().setUp()
        jobs_mod.reset_stale_origin()
        self.addCleanup(jobs_mod.reset_stale_origin)
        self.clip_dir = Path(self._tmp) / "clips"
        self.clip_dir.mkdir(parents=True, exist_ok=True)
        (self.clip_dir / "LN01.mp4").write_bytes(b"CLIP")

    def _jobs(self, produced_by):
        rec = {"state": "completed", "video_id": "v1"}
        if produced_by is not None:
            rec["video_vendor"] = produced_by
        return {"LN01": rec}

    def test_different_origin_is_not_done_and_is_named_in_the_log(self):
        jobs = self._jobs("agnes")
        os.environ["SHORTDRAMA_VIDEO_VENDOR"] = "comfyui"
        buf = []
        with mock.patch("builtins.print", lambda *a, **k: buf.append(str(a))):
            self.assertFalse(jobs_mod.done(jobs, "LN01", self.clip_dir))
        text = " ".join(buf)
        self.assertIn("重渲", text)
        self.assertIn("agnes", text)
        self.assertEqual(jobs_mod.stale_origin_report(), {"LN01": "agnes"})

    def test_same_origin_is_done(self):
        jobs = self._jobs("comfyui")
        os.environ["SHORTDRAMA_VIDEO_VENDOR"] = "comfyui"
        self.assertTrue(jobs_mod.done(jobs, "LN01", self.clip_dir))
        self.assertEqual(jobs_mod.stale_origin_report(), {})

    def test_record_without_origin_is_reused_not_reburned(self):
        """2026-09-18 之前的旧 jobs 没有产地字段 ⇒ 按盘上事实复用。

        产地不可考 ≠ 产地不同。把「查不到」当成「要重做」，等于拿一次字段缺失
        去重烧别人已经跑好的整集 —— 那是另一种「失败不可见」的反面：**成功被吞掉**。
        """
        jobs = self._jobs(None)
        os.environ["SHORTDRAMA_VIDEO_VENDOR"] = "comfyui"
        self.assertTrue(jobs_mod.done(jobs, "LN01", self.clip_dir))

    def test_missing_clip_still_wins_over_the_origin_check(self):
        """文件不在 ⇒ 与产地无关，先判未完成（旧行为一字不变）。"""
        (self.clip_dir / "LN01.mp4").unlink()
        os.environ["SHORTDRAMA_VIDEO_VENDOR"] = "agnes"
        self.assertFalse(jobs_mod.done(self._jobs("agnes"), "LN01", self.clip_dir))


class TestRoutes(_Isolated):
    """`v5/server.py` 上那五条路由的真实收发（`TestClient`，不占端口）。

    为什么要单测这一层：Electron 设置面板是**跨进程**打这些接口的，
    端点形状错了在本机 Python 测试里不会露头（`webmap` 单测只测转换函数）。
    """

    def setUp(self):
        super().setUp()
        from . import server
        self.app = server.create_app()
        from fastapi.testclient import TestClient
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def _get(self, path):
        r = self.client.get("/v1/pixa/short-drama" + path)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["code"], "000000", body)
        return body["data"]

    def _post(self, path, payload=None, expect=200):
        r = self.client.post("/v1/pixa/short-drama" + path, json=payload or {})
        self.assertEqual(r.status_code, expect, r.text)
        return r.json()

    def test_status_lists_image_still_on_cloud(self):
        d = self._get("/local-services")
        self.assertEqual(d["current_image_vendor"], vendors.DEFAULT)
        self.assertEqual(d["env_key"], "SHORTDRAMA_VIDEO_VENDOR")
        self.assertEqual(d["default_address"], local_services.DEFAULT_ADDRESS)

    def test_probe_over_http_reports_the_fake_machine_and_the_reasons(self):
        srv = _Server()
        self.addCleanup(srv.stop)
        body = self._post("/local-services/probe",
                          {"addresses": [srv.address, "http://127.0.0.1:1"]})
        self.assertEqual(body["code"], "000000")
        d = body["data"]
        self.assertEqual(len(d["found"]), 1)
        self.assertEqual(d["found"][0]["devices"][0]["vram_gb"], 12.0)
        # 不通的那几条必须各带一句 reason（面板要摊开给人看，不能合成一句「没探到」）
        # 候选 = 本机默认端口 8188（没开）+ 假机器 + 一个死端口 ⇒ 2 条不通
        dead = [t for t in d["tried"] if not t["reachable"]]
        self.assertEqual(len(d["tried"]), 3, d["tried"])
        self.assertEqual(len(dead), 2)
        self.assertTrue(all("连不上" in t["reason"] for t in dead),
                        [t["reason"] for t in dead])

    def test_inspect_returns_the_suggested_mapping_and_names_the_required(self):
        body = self._post("/local-services/inspect", {"workflow": _api_graph()})
        d = body["data"]
        self.assertEqual(sorted(d["mapping"]),
                         ["frames", "height", "image", "prompt", "width"])
        self.assertEqual(d["missing"], [])
        self.assertEqual(sorted(d["required"]), ["frames", "image", "prompt"])

    def test_inspect_bad_workflow_is_a_400_with_an_instruction(self):
        """形状不对 ⇒ 400 + 「怎么导出」，不是 500 也不是静默成功。"""
        body = self._post("/local-services/inspect",
                          {"workflow": {"nodes": [], "links": []}}, expect=400)
        self.assertIn("Export (API)", json.dumps(body, ensure_ascii=False))

    def test_connect_then_forget_round_trip(self):
        srv = _Server()
        self.addCleanup(srv.stop)
        with mock.patch("builtins.print", lambda *a, **k: None):
            body = self._post("/local-services/connect",
                              {"address": srv.address, "workflow": _api_graph()})
        self.assertEqual(body["code"], "000000", body)
        st = body["data"]["status"]
        self.assertEqual(st["current_video_vendor"], local_services.VENDOR)
        # 厂商清单里必须看得见它，且**不**出现在图片候选里
        vendors_body = self._get("/vendors")
        codes_video = [i["code"] for i in vendors_body["video_items"]]
        codes_image = [i["code"] for i in vendors_body["image_items"]]
        self.assertIn(local_services.VENDOR, codes_video)
        self.assertNotIn(local_services.VENDOR, codes_image)
        with mock.patch("builtins.print", lambda *a, **k: None):
            out = self._post("/local-services/forget")
        self.assertEqual(out["data"]["status"]["current_video_vendor"], vendors.DEFAULT)
        self.assertFalse(local_services.profile_path().exists())

    def test_default_switch_off_restores_cloud_without_forgetting_the_profile(self):
        srv = _Server()
        self.addCleanup(srv.stop)
        with mock.patch("builtins.print", lambda *a, **k: None):
            self._post("/local-services/connect",
                       {"address": srv.address, "workflow": _api_graph()})
            off = self._post("/local-services/default", {"on": False})
        self.assertEqual(off["data"]["status"]["current_video_vendor"], vendors.DEFAULT)
        # 档案还在（只是不当默认），面板据此才能再点一次「设为本机默认」
        self.assertTrue(local_services.profile_path().is_file())
        with mock.patch("builtins.print", lambda *a, **k: None):
            on = self._post("/local-services/default", {"on": True})
        self.assertEqual(on["data"]["status"]["current_video_vendor"],
                         local_services.VENDOR)


if __name__ == "__main__":
    unittest.main(verbosity=2)
