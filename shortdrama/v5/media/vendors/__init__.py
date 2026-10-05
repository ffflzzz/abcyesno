# -*- coding: utf-8 -*-
"""自有协议厂商的实现模块（`vendors.IMPL_PACKAGE` 指向这里）。

与「OpenAI 兼容类 = 在 `v5/vendors.py` 加一条 dict」相对：这类厂商的请求形状
不是 `POST {base}/v1/videos` + 轮询 `?video_id=`，而是各家自己的接口，
必须写代码。模块要实现的函数签名与 `v5/media/providers.py` 同名函数**逐字一致**
（分发点在 `providers.gen_image` / `submit_video` / `query_video`）。

本包在 2026-10-05 之前**是空的** —— 注册表里只有内置的 agnes 档。
也就是说这里的第一个模块（`comfyui.py`）是这套厂商接缝**第一次被第二个实现验证**，
它的形状从来没有在真机上跑过；改动前先看 `v5/tests_local_services.py`
（那里用本机假 ComfyUI 把整条请求路径真跑一遍）。
"""
from __future__ import annotations
