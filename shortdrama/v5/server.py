# -*- coding: utf-8 -*-
"""Web Shim —— v5 后端的 HTTP 门面（P1：只读）。

**职责只有三件**：路由、错误码、静态文件。所有形状转换在 `v5/webmap.py`。
**不复制任何判据**：门 / 锁 / 指纹 / 状态机全在 v5 内部（见 `../docs-archive-20260918/spec-web-shim.md` §4）。

## 路径前缀（★ 与前端逐字对齐，不是我自己定的）

前端 `web/assets/js/core/api.js` 的 `http()` 是：

    url = baseUrl + path
    其中 path 形如 `/v1/pixa/short-drama/projects/<pid>/progress`

而前端 Settings 里的 baseUrl 注释示例是 `http://127.0.0.1:8000/api`
→ 那样会打到 `/api/v1/pixa/short-drama/...`。
为了**消除这个配置歧义**，本服务把同一套路由**同时挂在 `/` 与 `/api` 两个前缀**下
（`_mount_all`），两个 baseUrl 写法都能工作。

## 响应信封

前端判成功用的是**字符串** `'000000'`：

    if (!r.ok || (json && json.code && json.code !== '000000')) throw ...
    return json ? (json.data !== undefined ? json.data : json) : null

故：成功走 `{"code":"000000","message":"success","data":{...}}`；
失败走 HTTP 4xx/5xx + 同一个信封（`code` 故意不为 `'000000'`）。
**唯一的响应体产出点是 `webmap.envelope()` / `webmap.error_body()`。**

## 跨域

前端 `fetch` 带 `credentials: 'include'` → **不能用 `Access-Control-Allow-Origin: *`**，
必须回显 Origin。`file://` 直开页面时 Origin 为字面串 `null`，也要放行。

## ⛔ P1 阶段：只读

所有写端点返回 **501**（带明确说明），而不是静默成功 ——
前端会如实报错，人一眼就知道"这步还没接"。（"静默降级"是本项目最贵的一类 bug。）

## 启动

    .venv/Scripts/python.exe -m v5.server --port 8787

✅ **2026-09-15 实测：本机可正常启动、可正常收发请求**（`/health` / `/styles` /
`/projects` / 分镜 detail / 静态图片全部 200，含非 ASCII 路径）。

> ⚠️ 本 spec 早先沿用了**旧架构 `api-spec.md`（已删）**的一句旧结论「受限沙箱内 uvicorn
> 不响应 socket」。**那是旧架构时期的记录、且已不成立** —— 我不该照抄未验证的假设
> （同一天我已经因为"照抄假设"栽过一次：见 §17.7 的花括号 glob 假阴性）。
> 逻辑测试仍走 `TestClient`（更快、不占端口），但**不再声称沙箱跑不了服务**。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

from . import config, webchain, webmap, webwrite
from .media import runner

#: ★ `Request` **必须能在模块层解析**（实测坑，2026-09-15）：
#: 本文件顶部有 `from __future__ import annotations` → 所有类型注解都是**字符串**，
#: 由 `typing.get_type_hints()` 按**模块 globals** 求值。若把它 import 在
#: `create_app()` 内部（局部作用域），FastAPI 就解析不出来 → 把 `request`
#: 当成**查询参数** → POST 报 `400 query.request Field required` 而不是 501。
#: 用 try 包住是为了保住「不装 web 框架也能 import 本模块」的性质。
try:
    from starlette.requests import Request
except Exception:               # pragma: no cover —— 没装时只是注解，纯函数不受影响
    Request = object

#: 允许的本地来源（带 credentials 时必须**回显** Origin，不能用 `*`）
_LOCAL_HOSTS = ("http://localhost", "http://127.0.0.1", "http://[::1]")

#: 额外放行（逗号分隔），例：`SHORTDRAMA_WEB_ALLOW_ORIGIN=https://foo.example`
_EXTRA = tuple(x.strip() for x in
               os.environ.get("SHORTDRAMA_WEB_ALLOW_ORIGIN", "").split(",") if x.strip())

#: 静态文件只服务这两棵子树（其余一律 403）
_STATIC_ROOTS = ("images", "media")

#: **仍未接线**的写路径 → 501。这里只存"这条路径将来做什么"的说明，
#: 让 501 的 message 是**准确的**而不是一句空话（前端会把它显示出来）。
#: ⚠️ 每实现一条就把它从这里删掉，否则 501 提示会误导。
_WRITE_DESC = {
    "/v1/aigc/credits/calculate": "算力预估（v5 没有计费模型）",
    "/v1/pixa/short-drama/export": "导出成片（v5 的成片就在 media/ep1/episode_final.mp4）",
    "/v1/pixa/short-drama/projects/{pid}/assets/finalize": "资产定稿（v5 无此语义）",
    "/v1/pixa/short-drama/projects/{pid}/episodes": "新建分集（v5 目前按 brief.episodes 固定）",
    "/v1/pixa/short-drama/episodes/{eid}": "删除分集",
}


def _write_hint(rel: str) -> str:
    """按路径形状给出将来用途；未登记的一律「写操作」。

    带参数的模式（`{pid}`）要先**把占位符换成哨兵再转义**，否则 `re.escape`
    会把 `{`/`}` 转义掉，替换就失效了。
    """
    path = "/" + str(rel or "").lstrip("/")
    if path in _WRITE_DESC:
        return _WRITE_DESC[path]
    for pat, desc in _WRITE_DESC.items():
        rx = re.sub(r"\{[^}]*\}", "___P___", pat)
        rx = re.escape(rx).replace("___P___", "[^/]+")
        if re.fullmatch(rx, path):
            return desc
    return "写操作"


def _resolve_pid(pid: str) -> Path:
    """`pid` → 项目根。**越界即拒**（`..` / 绝对路径 / 不存在的目录）。

    为什么用白名单而不是字符串清洗：目录名就是 pid，前端会把它拼进 URL。
    清洗规则永远会漏（URL 编码、Windows 反斜杠、UNC…）；**只认已存在的目录名**
    则没有绕过面。
    """
    if not pid or pid in (".", "..") or "/" in pid or "\\" in pid:
        raise _bad("非法的项目标识：%s" % pid)
    if pid not in webmap.list_pids():
        raise _not_found("项目不存在：%s" % pid)
    return webmap.project_root(pid)


def _resolve_eid(eid: str) -> tuple:
    """`<pid>-ep<N>` → (`<pid>`, N)，并校验项目存在。"""
    pid, ep = webmap.parse_episode_id(eid)
    if not pid or ep < 1:
        raise _bad("非法的分集标识：%s" % eid)
    _resolve_pid(pid)
    return pid, ep


#: 分镜标识：`<pid>-ep<N>-<LNxx>`（与 `webmap` 里 segment_id 的构造**同源**）
#: 注意 pid 自身含 `-`（`village-tree`）→ 必须从**右侧**贪婪切。
_SID_RE = re.compile(r"^(?P<pid>.+)-ep(?P<ep>\d+)-(?P<shot>LN\d+)$")


def _split_sid(sid: str) -> tuple:
    """`<pid>-ep<N>-<LNxx>` → (`<pid>`, N, `LNxx`)。**不校验项目存在**（供批量解析）。"""
    m = _SID_RE.match(str(sid or "").strip())
    if not m:
        raise _bad("非法的分镜标识：%s（应形如 <项目>-ep1-LN03）" % sid)
    return m.group("pid"), int(m.group("ep")), m.group("shot")


def _resolve_sid(sid: str) -> tuple:
    """解析并校验项目存在。"""
    pid, ep, shot = _split_sid(sid)
    _resolve_pid(pid)
    return pid, ep, shot


def _resolve_static(pid: str, rel: str) -> Path:
    """静态文件路径。**两重防护**：pid 白名单 + resolve 后前缀校验 + 子树白名单。"""
    root = _resolve_pid(pid)
    rel = (rel or "").lstrip("/").replace("\\", "/")
    if not rel:
        raise _bad("缺少文件路径")
    first = rel.split("/", 1)[0]
    if first not in _STATIC_ROOTS:
        raise _forbidden("只允许访问 %s 两棵子树" % "、".join(_STATIC_ROOTS))
    base = root.resolve()
    target = (base / rel).resolve()
    # `resolve()` 会展开 `..` 与符号链接 → 前缀校验才是可信的
    try:
        target.relative_to(base)
    except ValueError:
        raise _forbidden("路径越界")
    if not target.is_file():
        raise _not_found("文件不存在")
    return target


# ─────────────────────────────────────────────────────────── 错误（延迟导入 FastAPI）

def _bad(msg: str):
    from fastapi import HTTPException
    return HTTPException(status_code=400, detail=msg)


def _forbidden(msg: str):
    from fastapi import HTTPException
    return HTTPException(status_code=403, detail=msg)


def _not_found(msg: str):
    from fastapi import HTTPException
    return HTTPException(status_code=404, detail=msg)


def _server_error(msg: str):
    """外部依赖（LLM / dev server）出问题 → 502（上游故障）。"""
    from fastapi import HTTPException
    return HTTPException(status_code=502, detail=msg)


def _conflict(msg: str):
    """资源被占（端口上有别人的 dev server）→ 409，提示可用 force 接管。"""
    from fastapi import HTTPException
    return HTTPException(status_code=409, detail=msg)


def create_app(base: str | None = None, web_root: str | None = None):
    """构建 FastAPI 应用。

    **延迟导入 fastapi**：本模块被 import 时不该强制依赖 web 框架
    （`webmap` 的纯函数测试要在不装 fastapi 的环境下也能跑）。

    `base`：静态资源的**绝对前缀**（如 `http://127.0.0.1:8787`）。
    不传则取环境变量 `SHORTDRAMA_WEB_BASE`。**必须给**，否则媒体 URL 是根相对路径，
    在不同源部署下会全部 404（见 `webmap.MEDIA_BASE` 的说明）。
    **例外**：给了 `web_root`（同源托管前端）时空串才正确 —— 那时页面就长在这个
    服务上，根相对路径会被解析到同一个 origin。

    `web_root`：**要一起托管的前端静态目录**。**默认（CLI）就是本仓库的 `web/`**
    —— 前端应用代码（`index.html` + `assets/`，19 个文件）2026-09-18 已搬进本仓库，
    这样就是**同一个 origin**：
      · 不需要 CORS（同源请求浏览器不做 CORS 检查）
      · **可以不再放行 `Origin: null`**（补上那条到期的安全复查项）
      · 前端不用再手动配 `baseUrl`（`api.js` 自检同源）
      · 少养一个静态服务进程（本机沙箱里那个进程会被回收）
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse
    from . import webmap as wm

    web_root = web_root if web_root is not None else os.environ.get("SHORTDRAMA_WEB_ROOT", "")
    root_dir = Path(web_root).resolve() if web_root else None
    if root_dir is not None:
        if not (root_dir / "index.html").is_file():
            raise RuntimeError(
                "`web_root` 里没有 index.html：%s（应指向前端根目录，默认是本仓库的 web/）"
                % root_dir)
        base = ""                       # 同源 → 根相对路径才对
    else:
        base = base or os.environ.get("SHORTDRAMA_WEB_BASE", "")
    if base:
        wm.set_media_base(base)
    else:
        wm.set_media_base("")

    app = FastAPI(title="shortdrama web shim", version="0.1.0-p1",
                  docs_url="/docs", redoc_url=None)

    # ── CORS（credentials + 回显 Origin，**不能用 `*`**）──
    #
    # ★ **曾经必须放行字面串 `null`**（2026-09-15 实测漏掉、会直接卡死前端）：
    #   前端原本是设计成 **`index.html` 双击即开**（`file://`）的，
    #   而 `file://` 页面发出的 `Origin` 就是字面串 `null`。不放行 → 浏览器
    #   既不批准响应也不放行预检 → **前端一个请求都发不出去**（且报错很含糊）。
    #
    # ★★ **2026-09-17 收紧**（`--web-root` 上线后）：同源托管时**不再放行 `null`**。
    #   理由：那个注释里的复査项到期了 ——
    #     「P2 一旦接入写操作（创建项目/出片），必须重新评估」
    #   而 **P2 已经完成**（建项目 / AI 创作 / 跑创作链 / 删项目全部可写）。
    #   放行 `null` 意味着**沙箱化 iframe、本地文件**等任何拿到 `null` 的来源
    #   都能建/删项目 —— 虽然服务只绑 127.0.0.1，但没有理由继续开着。
    #   同源部署下前端由**本服务**提供，浏览器根本不需要 CORS，所以关掉零代价。
    #   ⚠️ 若你确实要双击 `index.html`（`file://`）用，显式设
    #      `SHORTDRAMA_WEB_ALLOW_NULL_ORIGIN=1` 把它开回来（**知道代价再开**）。
    allow_null = (root_dir is None) or \
        (os.environ.get("SHORTDRAMA_WEB_ALLOW_NULL_ORIGIN") == "1")
    _pats = [r"http://localhost(:\d+)?", r"http://127\.0\.0\.1(:\d+)?",
             r"http://\[::1\](:\d+)?"] + [_esc(o) for o in _EXTRA]
    if allow_null:
        _pats.append(r"null")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex="|".join(_pats),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
        # ★★ **必须开**（2026-09-15 实测踩到，症状是浏览器只报 `Failed to fetch`）：
        #   Chrome 的 **Local Network Access / Private Network Access** 会对
        #   「从本机页面访问 localhost」的请求，在预检里附加
        #   `Access-Control-Request-Private-Network: true`；
        #   Starlette 的 `CORSMiddleware` 默认 `allow_private_network=False` →
        #   直接把预检判失败 → **HTTP 400** → 浏览器一律 `Failed to fetch`（信息量为零）。
        #   实测：带该头的预检 400；不带则 200 —— 所以这是**唯一**差别。
        #   项目已有先例（2026-09-03 记录：`langgraph-api` 的默认 CORSMiddleware
        #   同样未开此项 → 预检 400）。
        allow_private_network=True,
    )

    # ── 错误 → 统一信封 ──
    #
    # ⚠️ 必须**显式注册 HTTPException 的处理器**：Starlette 按异常的 MRO 查处理器，
    #    `HTTPException` 会先命中 **FastAPI 自带的默认处理器**（返回 `{"detail":...}`），
    #    我们那个只处理 `Exception` 的函数**永远不会被调用** —— 后果是前端
    #    `api.js` 拿不到 `message`，只能显示 `HTTP 404`，我们写的中文原因全丢了。
    #    （`add_exception_handler` 是按类名覆盖的，注册在后面即生效。）
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):  # noqa: ANN001
        msg = exc.detail if isinstance(exc.detail, str) else json.dumps(
            exc.detail, ensure_ascii=False)
        return JSONResponse(status_code=exc.status_code,
                            content=wm.error_body(msg, code="E%d" % exc.status_code))

    @app.exception_handler(RequestValidationError)
    async def _param_error(request: Request, exc: RequestValidationError):  # noqa: ANN001
        """查询参数不合法（如 `page=abc`）→ 400 + 信封，而不是 FastAPI 默认的 422 裸结构。"""
        first = (exc.errors() or [{}])[0]
        loc = ".".join(str(x) for x in (first.get("loc") or []))
        return JSONResponse(status_code=400, content=wm.error_body(
            "请求参数不合法：%s %s" % (loc, first.get("msg") or ""), code="E400"))

    @app.exception_handler(Exception)
    async def _any_error(request: Request, exc: Exception):     # noqa: ANN001
        """未捕获异常 → 也走信封（否则前端只有空 body + 500，完全查不出原因）。"""
        return JSONResponse(status_code=500, content=wm.error_body(
            "%s: %s" % (type(exc).__name__, str(exc)[:300]), code="E500"))

    # ── 路由表（同一个 router 会被挂到 `/` 与 `/api` 两处）──
    from fastapi import APIRouter
    r = APIRouter()

    @r.get("/health")
    @r.get("/v1/pixa/short-drama/health")
    def health():
        return wm.envelope(wm.health())

    @r.get("/v1/pixa/short-drama/styles")
    def styles():
        return wm.envelope(wm.styles())

    @r.get("/v1/pixa/short-drama/vendors")
    def vendors_route():
        """可选**厂商档**清单 + 当前缺省（2026-09-18）。

        前端在每个生成页面上让用户选厂商用；列表**以后端为准**，
        前端不写死（否则公司机 `register("comfy")` 之后前端看不到它）。
        """
        return wm.envelope(wm.vendors())

    @r.get("/v1/pixa/short-drama/projects")
    def projects(page: int = 1, page_size: int = 10, is_demo: str | None = None):
        """项目列表。分页形状按线上给；`items` 与 `list` 同值，两个键都给以免猜错。

        ## `is_demo` 现在**真的生效**（2026-09-18 接通）

        此前这个参数是**收下即丢**，而 docstring 还写着「前端没有任何视图在调它」
        —— 那句话是**错的**，两个方向都错：
          · 前端一直在调它（`core/api.js` 的 `listProjects` / 水合，服务日志里可见）；
          · 前端「精选项目」tab 正是靠 `is_demo=true` 取数据（线上逆向实证：
            `_recon/_flows/L3_featured/step.json` 记下了 `is_demo=false` 与
            `is_demo=true` **两次**请求）。

        ## 三态（与 `webmap.project_list` **同一规则**，判据不写两份）

        | 传参 | 返回 |
        |---|---|
        | 不传（`None`）| **全量**，不过滤 —— 缺省**不静默藏数据** |
        | `true` / `1` / `yes` / `on` | 只要精选（线上「精选项目」tab）|
        | `false` / `0` / `no` / `off` / 空串 | 只要非精选（线上「我的项目」）|

        为什么缺省是"全量"而不是线上的常见默认 `false`：`webmap.project_list()`
        的缺省就是不筛（`None`），**两层必须同一条规则** —— 否则读代码的人得记住
        "这层默认全量、那层默认过滤"，迟早有一个调用点踩错。
        且"缺省就悄悄少给你几条"正是本项目最忌的**静默丢数据**，即使响应里回显了
        `is_demo` 也不该拿它当默认。

        ⚠️ 归一化**别信字符串**：FastAPI 这里收的是 `str`，写成 `bool(is_demo)` 会让
        `"false"` 因非空而判**真**，于是「我的项目」变成"只要精选" ⇒ 列表整片消失
        （本项目踩过的同型坑：字符串真值 / 把"上限"当成另一种"上限"）。
        响应里**原样回显** `is_demo`，让消费方能确认服务端真的按它筛了。
        """
        want = None if is_demo is None else (
            str(is_demo).strip().lower() in ("1", "true", "yes", "y", "on"))
        rows = wm.project_list(is_demo=want)
        page = max(1, int(page or 1))
        size = max(1, min(200, int(page_size or 10)))
        start = (page - 1) * size
        chunk = rows[start:start + size]
        # `is_demo` 原样回显（消费方可确认服务端真的按它筛了，而不是静默忽略）
        return wm.envelope({"list": chunk, "items": chunk, "total": len(rows),
                            "page": page, "page_size": size, "is_demo": want})

    @r.get("/v1/pixa/short-drama/projects/{pid}/progress")
    def progress(pid: str, include_content: str = "true"):
        root = _resolve_pid(pid)
        return wm.envelope(wm.progress(root))

    @r.get("/v1/pixa/short-drama/projects/{pid}/asset-refs")
    def asset_refs(pid: str):
        root = _resolve_pid(pid)
        return wm.envelope(wm.asset_refs(root))

    @r.get("/v1/pixa/short-drama/projects/{pid}/episodes/storyboard")
    def episodes_storyboard(pid: str):
        root = _resolve_pid(pid)
        return wm.envelope(wm.episodes_storyboard(root))

    @r.get("/v1/pixa/short-drama/episodes/{eid}/storyboard/detail")
    def storyboard_detail(eid: str):
        pid, ep = _resolve_eid(eid)
        root = webmap.project_root(pid)
        return wm.envelope(wm.storyboard_detail(root, ep))

    # ⚠️ 必须**显式**列 HEAD：FastAPI 的 `APIRoute` 不像 Starlette 的 `Route` 那样
    #    在注册 GET 时自动补 HEAD（实测 HEAD → 405）。而 `HEAD` 是
    #    `curl -I` / 部分客户端的排障与预检手段，静态资源该支持。
    @r.api_route("/media/{pid}/{rel:path}", methods=["GET", "HEAD"])
    def media(pid: str, rel: str):
        """静态文件。`FileResponse` 自带 Range 支持（`<video>` 拖动进度条需要）。"""
        f = _resolve_static(pid, rel)
        return FileResponse(f)

    # ══════════════════ P2a：生产 / 编辑（写） ══════════════════
    #
    # ★ **注册顺序很重要**：这些显式路由必须**先于**下面的 501 兜底路由注册，
    #   否则 `/{rel:path}` 会抢先匹配掉所有 POST（Starlette 按注册顺序匹配）。
    #   ⚠️ 实测踩到过：兜底路由原先写在 `include_router` 之前 → 所有写端点都返回 501。

    def _vendor_of(payload, kind: str) -> str:
        """从请求体取该能力的厂商名（2026-09-18）。

        约定键名：`image_vendor` / `video_vendor`（由前端**按生成页面**选）。
        缺省/空串 ⇒ 交给 `runner.start` 回落环境变量与 agnes ⇒ 与改造前一致。

        ⚠️ 厂商名**不做静默纠正**：未注册的值由 `runner.start` 抛 `UnknownVendor`
        （`ValueError` 子类）⇒ 下面既有的 `except ValueError → 400` 自动接住。
        "早失败一次，胜过白排一个 run"。
        """
        p = payload or {}
        return str(p.get(kind + "_vendor") or "").strip()

    def _qc_of(payload) -> dict:
        """**质检自愈开关**（2026-09-19）：`still_qc` / `clip_qc`。

        它们决定"机器要不要自己判画面不合格并重画/重拍"：
          · 不传（前端默认）⇒ `runner.start` 按**人工模式默认 = 关**处理；
          · 传 `true` / `1` ⇒ 打开（人自己选的，出事不冤）。
        ⛔ **这里不做真假换算** —— `"0"` 在 Python 里是真值，这种换算只允许有一处
           （`media/runner._qc_flag`），否则两个口径一定会漂移。
        """
        p = payload or {}
        return {"still_qc": p.get("still_qc"), "clip_qc": p.get("clip_qc")}

    def _start_media(payload, kind: str):
        """起一个媒体任务。`shots` 空 → `runner` 会拒（空 shots 等于整片渲染）。"""
        p = payload or {}
        pid = str(p.get("pid") or p.get("project_id") or "").strip()
        ep = int(p.get("ep") or p.get("episode_no") or 1)
        ids = p.get("segment_ids") or ([p["segment_id"]] if p.get("segment_id") else [])
        shots: list = []
        for s in (ids if isinstance(ids, list) else [ids]):
            _p, _e, shot = _split_sid(str(s))
            shots.append(shot)
            if not pid and _p:
                pid, ep = _p, _e
        if not pid:
            raise _bad("缺少项目标识：请带 `pid`，或带可解析的 `segment_ids`")
        _resolve_pid(pid)
        try:
            rec = runner.start(pid, kind, ep=ep, shots=shots,
                               image_vendor=_vendor_of(p, "image"),
                               video_vendor=_vendor_of(p, "video"),
                               **_qc_of(p),
                               log=print)
        except ValueError as e:          # 参数 / 护栏 / **厂商名**不通过 → 400（不是 500）
            raise _bad(str(e))
        return wm.envelope(rec)

    @r.post("/v1/pixa/short-drama/segments/batch/keyframe/generate")
    async def gen_keyframe(payload: dict | None = None):
        return _start_media(payload, "keyframe")

    @r.post("/v1/pixa/short-drama/segments/batch/video/generate")
    async def gen_video(payload: dict | None = None):
        return _start_media(payload, "video")

    @r.post("/v1/pixa/short-drama/projects/{pid}/assets/batch-generate-image")
    async def gen_assets(pid: str, payload: dict | None = None):
        """批量生成资产参考图（可续跑）。**资产图是图片** ⇒ 只看 image 厂商。"""
        _resolve_pid(pid)
        try:
            rec = runner.start(pid, "assets",
                               image_vendor=_vendor_of(payload, "image"),
                               log=print)
        except ValueError as e:
            raise _bad(str(e))
        return wm.envelope(rec)

    @r.post("/v1/pixa/short-drama/episodes/{eid}/compose")
    async def compose(eid: str, payload: dict | None = None):
        """**合成成片**（D，2026-09-19）：前端「生成最终视频」按钮的真实实现。

        为什么必须由**人**显式触发：它是唯一一条会**整片烧配额**的路径
        （静帧 → 视频 → 拼接，小时级）。所以：
          · 它是一条**独立 kind**（`runner.KINDS["episode"]`），不是"空 shots 的 video"
            —— 后者被硬护栏拒掉是对的（那是"参数漏传"的形状，两者不能混）；
          · 门与记账仍全在 `pipeline.run` 内（`media_gate` / 独占锁 / 幂等跳过），
            绕不过；静帧/视频已在盘上的部分按磁盘事实复用，**不重复烧**。
        """
        pid, ep = _resolve_eid(eid)
        _resolve_pid(pid)
        p = payload or {}
        try:
            rec = runner.start(pid, "episode", ep=ep,
                               image_vendor=_vendor_of(p, "image"),
                               video_vendor=_vendor_of(p, "video"),
                               **_qc_of(p),
                               log=print)
        except ValueError as e:
            raise _bad(str(e))
        return wm.envelope(rec)

    # ── 任务台账（轮询用）──
    @r.get("/v1/pixa/short-drama/runs")
    def runs_list(pid: str = "", limit: int = 30):
        return wm.envelope({"list": runner.list_runs(pid or None, int(limit or 30))})

    @r.get("/v1/pixa/short-drama/runs/{run_id}")
    def runs_get(run_id: str):
        rec = runner.status(run_id)
        if rec is None:
            raise _not_found("任务不存在：%s" % run_id)
        rec = dict(rec)
        rec["log_tail"] = runner.log_tail(run_id, 60)
        # ★ 步级「逐步人工确认」状态**搭车**（2026-09-18）：前端本来就在轮询这个
        #   端点（`api.js` 的 `waitRun`，3 秒一次）⇒ 不必为它多开一条轮询。
        #   必要性：链路挂在等人时 run 状态仍是 `running`，只看 run 记录**看不出
        #   "正在等人"**，界面会显示成"跑了很久还没完"，人不知道自己该点东西。
        _pid = str(rec.get("pid") or "")
        if _pid and (config.PROJECTS_DIR / _pid).is_dir():
            rec["hitl"] = wm.hitl_state(webmap.project_root(_pid))
        else:
            rec["hitl"] = {"pending": False, "manual_steps": None}
        return wm.envelope(rec)

    @r.delete("/v1/pixa/short-drama/runs/{run_id}")
    def runs_cancel(run_id: str):
        rec = runner.cancel(run_id)
        if rec is None:
            raise _not_found("任务不存在：%s" % run_id)
        return wm.envelope(rec)

    # ── 确定性编辑 ──
    def _edit(fn, *a, **kw):
        """把 `EditError` 翻成 400（**可预期的输入错误不该给 500**）。"""
        try:
            return wm.envelope(fn(*a, **kw))
        except webwrite.EditError as e:
            raise _bad(str(e))

    @r.post("/v1/pixa/short-drama/projects/{pid}/auto-sync-assets")
    def sync_assets(pid: str, payload: dict | None = None):
        """把 `images/` 里没登记的图收编进注册表（= 前端「同步资产」）。"""
        return _edit(webwrite.sync_assets, _resolve_pid(pid))

    @r.patch("/v1/pixa/short-drama/projects/{pid}/auto-sync-assets")
    def patch_auto_sync(pid: str, payload: dict | None = None):
        _resolve_pid(pid)
        val = bool((payload or {}).get("auto_sync_assets", True))
        return wm.envelope({
            "auto_sync_assets": val,
            "note": "v5 没有「自动同步」开关（同步是显式动作）。"
                    "此值仅作为前端偏好回显，**未落盘**。",
        })

    @r.post("/v1/pixa/short-drama/projects/{pid}/rename")
    def rename_project(pid: str, payload: dict | None = None):
        return _edit(webwrite.rename_project, _resolve_pid(pid),
                     (payload or {}).get("name", ""))

    @r.post("/v1/pixa/short-drama/projects/{pid}/outline")
    def update_outline(pid: str, payload: dict | None = None):
        return _edit(webwrite.update_outline, _resolve_pid(pid), payload or {})

    @r.post("/v1/pixa/short-drama/projects/{pid}/assets")
    def add_asset(pid: str, payload: dict | None = None):
        p = payload or {}
        return _edit(webwrite.add_asset, _resolve_pid(pid), p.get("kind", ""),
                     p.get("name", ""), p.get("identity", ""), p.get("keywords"))

    @r.post("/v1/pixa/short-drama/projects/{pid}/assets/{kind}/{aid}/states")
    def save_asset_state(pid: str, kind: str, aid: str, payload: dict | None = None):
        """「角色信息」抽屉的保存。

        ⚠️ v5 的资产模型比 Pavo 薄 —— `webwrite.save_asset_state` 会**如实报出**
        哪些字段生效、哪些忽略了（声音/音色、四视图、per-asset 画风 v5 都没有）。
        """
        return _edit(webwrite.save_asset_state, _resolve_pid(pid), kind, aid,
                     payload or {})

    @r.delete("/v1/pixa/short-drama/projects/{pid}/assets/{kind}/{aid}")
    def delete_asset(pid: str, kind: str, aid: str):
        return _edit(webwrite.delete_asset, _resolve_pid(pid), kind, aid)

    @r.post("/v1/pixa/short-drama/episodes/{eid}/script")
    def update_script(eid: str, payload: dict | None = None):
        pid, ep = _resolve_eid(eid)
        return _edit(webwrite.update_script, webmap.project_root(pid), ep,
                     (payload or {}).get("content", ""))

    @r.post("/v1/pixa/short-drama/episodes/{eid}/script/generate")
    async def gen_script(eid: str, payload: dict | None = None):
        """**生成剧本正文**（2026-09-19）：创作链**只跑到 scriptwriter 为止**。

        为什么单独开一条（而不是复用「生成分镜脚本」）：前端是**两段式**的 ——
        先确认「简介」，再生成「剧本正文」，正文确认之后才轮到资产/分镜。
        用整条链去等正文，会让后面 4 个角色（资产卡 / 分镜 / 评审）在"正文还没被人
        确认"时就白跑一遍 —— 每个角色都是真金白银的 LLM 调用 + 分钟级耗时。

        与「生成分镜脚本」一样先 `ensure_devserver`（项目目录是编译期绑定的）。
        """
        pid, ep = _resolve_eid(eid)
        try:
            st = webchain.ensure_devserver(pid, log=print,
                                           manual_steps=p.get("manual_steps"))
            print("[shim] dev server %s（%s）" % ("复用" if st.get("reused") else "新起", pid))
        except RuntimeError as e:
            raise _conflict(str(e)[:400])
        except Exception as e:                  # noqa: BLE001
            raise _server_error(str(e)[:400])
        p = payload or {}
        try:
            rec = runner.start(pid, "script", ep=ep,
                               image_vendor=_vendor_of(p, "image"),
                               video_vendor=_vendor_of(p, "video"),
                               **_qc_of(p),
                               log=print)
        except ValueError as e:
            raise _bad(str(e))
        return wm.envelope(rec)

    @r.post("/v1/pixa/short-drama/segments/{sid}")
    def update_segment(sid: str, payload: dict | None = None):
        pid, ep, shot = _resolve_sid(sid)
        return _edit(webwrite.update_segment, webmap.project_root(pid), ep, shot,
                     payload or {})

    @r.delete("/v1/pixa/short-drama/segments/{sid}")
    def delete_segment(sid: str):
        pid, ep, shot = _resolve_sid(sid)
        return _edit(webwrite.delete_segment, webmap.project_root(pid), ep, shot)

    @r.post("/v1/pixa/short-drama/episodes/{eid}/segments")
    def add_segment(eid: str, payload: dict | None = None):
        pid, ep = _resolve_eid(eid)
        p = payload or {}
        return _edit(webwrite.add_segment, webmap.project_root(pid), ep,
                     p.get("after", ""), p.get("fields"))

    # ══════════════════ P2b：创建链（剧本解析 + 跑创作链） ══════════════════

    @r.post("/v1/pixa/short-drama/projects/paste")
    async def create_by_paste(payload: dict | None = None):
        """剧本原文 → 建项目 + `brief.json`（LLM 提炼）+ **保留原文**。

        ⚠️ **同步返回**（LLM 提炼约 10–40 秒）：前端 `createByParse` 拿到结果就要
        跳转到项目页，异步化会让它需要轮询——先把闭环做通，慢的话前端也有自己的
        "解析中…" 提示。
        """
        p = payload or {}
        text = str(p.get("content") or "").strip()
        if not text:
            raise _bad("缺少剧本正文（`content`）")
        try:
            r = webchain.create_project(
                text,
                style_code=str(p.get("style_code") or ""),
                explicit_pack=str(p.get("pack") or ""),
                name=str(p.get("name") or ""),
                log=print)
        except ValueError as e:                 # 提炼失败/字段缺 → 400（可预期的输入问题）
            raise _bad(str(e))
        except Exception as e:                  # noqa: BLE001 —— 供应商/网络问题 → 500
            raise _server_error("剧本解析失败：%s: %s" % (type(e).__name__, str(e)[:300]))
        return wm.envelope(webchain.public_project(webmap.project_root(r["pid"])))

    @r.post("/v1/pixa/short-drama/projects/ai-generate")
    async def create_by_ai(payload: dict | None = None):
        """一句创意 → `brief.json`（**LLM 创作**）+ 建项目。

        与 `/projects/paste` 的区别（**这是两种不同的活**）：
          · `paste` —— 用户给了**完整剧本** → LLM **提炼**成 brief，并把原文放进
            scriptwriter 产物位（链会沿用用户的剧本）
          · **本端点** —— 用户只给**一句点子** → LLM **创作**出 brief（四幕事件等由模型设计），
            且**不写** scriptwriter 产物 → 链会**真的去写剧本**
            （若也写进去，两句话会被当成整部剧本，全片就毁了）
        """
        p = payload or {}
        idea = str(p.get("idea") or p.get("content") or p.get("script") or "").strip()
        if not idea:
            raise _bad("缺少创意（`idea`）")
        try:
            r = webchain.create_project(
                idea,
                style_code=str(p.get("style_code") or ""),
                explicit_pack=str(p.get("pack") or ""),
                name=str(p.get("name") or ""),
                mode="idea",
                episodes=int(p.get("episodes") or 0),
                ratio=str(p.get("ratio") or ""),
                log=print)
        except ValueError as e:
            raise _bad(str(e))
        except Exception as e:                  # noqa: BLE001
            raise _server_error("AI 创作失败：%s: %s" % (type(e).__name__, str(e)[:300]))
        return wm.envelope(webchain.public_project(webmap.project_root(r["pid"])))

    @r.post("/v1/pixa/short-drama/episodes/batch/storyboard/generate")
    async def gen_storyboard(payload: dict | None = None):
        """跑创作链产出分镜（supervisor 7 角色）。

        **D6**：先确保 dev server 服务于本项目（项目目录编译期绑定，换项目要重启）。
        链本身由 `runner` 起子进程跑（台账 / 取消 / 判死全部复用）。
        """
        p = payload or {}
        ids = p.get("episode_ids") or ([p["episode_id"]] if p.get("episode_id") else [])
        if not ids:
            raise _bad("缺少 `episode_ids`")
        pid, ep = _resolve_eid(str(ids[0]))
        try:
            st = webchain.ensure_devserver(pid, log=print,
                                           manual_steps=p.get("manual_steps"))
            print("[shim] dev server %s（%s）" % ("复用" if st.get("reused") else "新起", pid))
        except RuntimeError as e:
            # ⚠️ 分流必须与 `/devserver` 端点**一致**：端口被占/起不来是**可预期的冲突**
            #    → 409（带可操作提示），不是 502。实测踩到过：这里漏改，前端只看到 502。
            raise _conflict(str(e)[:400])
        except Exception as e:                  # noqa: BLE001
            raise _server_error("dev server 未能就绪：%s" % str(e)[:300])
        try:
            rec = runner.start(pid, "chain", ep=ep, log=print)
        except ValueError as e:
            raise _bad(str(e))
        return wm.envelope(rec)

    # ── dev server（D6）：状态查询 + 手动确保（排障用）──
    @r.get("/v1/pixa/short-drama/devserver")
    def devserver_get():
        return wm.envelope(webchain.devserver_status())

    @r.post("/v1/pixa/short-drama/devserver")
    async def devserver_ensure(payload: dict | None = None):
        p = payload or {}
        pid = str(p.get("pid") or "").strip()
        if not pid:
            raise _bad("缺少 `pid`")
        _resolve_pid(pid)
        try:
            return wm.envelope(webchain.ensure_devserver(
                pid, log=print, force=bool(p.get("force")),
                manual_steps=p.get("manual_steps")))
        except RuntimeError as e:               # 端口被别人占着等**可预期**的冲突 → 409
            raise _conflict(str(e)[:400])
        except Exception as e:                  # noqa: BLE001
            raise _server_error(str(e)[:300])

    @r.delete("/v1/pixa/short-drama/devserver")
    def devserver_stop():
        """停掉 shim 起的 dev server（释放 2024）。运维/清理用。"""
        return wm.envelope(webchain.stop_devserver(log=print))

    # ── 步级「逐步人工确认」（2026-09-18）────────────────────────────────
    #
    # 链路（`drive_chain.py`）每派一个角色**之前**挂起，用项目下
    # `.tmp/hitl/{pending,decision}.json` 两个文件等人。前端只需读写这两个文件 ——
    # **链那一侧一行都不用为前端改**（信道是既有的，见 `v5/hitl.py`）。
    @r.get("/v1/pixa/short-drama/projects/{pid}/hitl")
    def hitl_get(pid: str):
        """当前是否停在某一步等人 + 可打回的目标。"""
        return wm.envelope(wm.hitl_state(_resolve_pid(pid)))

    @r.post("/v1/pixa/short-drama/projects/{pid}/hitl")
    def hitl_post(pid: str, payload: dict | None = None):
        """提交决定：`approve`（继续）/ `redo`（打回重跑）/ `reject`（中止）。

        body：`{decision, target?, by?, note?, stamp?}`

        ⚠️ **"当前有没有挂起"的校验在 `hitl.decide` 里，不在本函数** ——
        判据只写一份，CLI（`series.py --hitl-approve`）与这里走同一道关。
        那条校验是**必需的**：没有挂起时写下的决定会一直躺在盘上，等**下一次**
        挂起时被立刻消费 ⇒ 静默跳过一次人工审核（本项目最贵的一类 bug）。

        `stamp` 由前端从 `GET /hitl` 取到后原样带回；不带则取当前挂起的戳。
        两种情况都会被链路按戳校验 —— 串步的决定**不算数**（见 `v5/hitl.py`）。
        """
        from . import hitl
        root = _resolve_pid(pid)
        p = payload or {}
        try:
            hitl.decide(root, str(p.get("decision") or ""),
                        by=str(p.get("by") or ""), note=str(p.get("note") or ""),
                        target=str(p.get("target") or ""),
                        stamp=str(p.get("stamp") or ""))
        except ValueError as e:              # 非法决定 / 无挂起 / 打回目标非法 → 400
            raise _bad(str(e))
        return wm.envelope(wm.hitl_state(root))

    # ── 删除项目：**移到可恢复的暂存区**，不真删 ──
    @r.post("/v1/pixa/short-drama/projects/batch-delete")
    async def batch_delete(payload: dict | None = None):
        """删项目。**默认拒绝**：若有任务正在跑（媒体链/创作链），必须先停。

        ⛔ 为什么必须拦（2026-09-15 实测踩到）：我删了一个正在跑创作链的项目 →
        目录被移进暂存区，**但链没停**，它随后把 `worldbuilder.md` 写回**原路径**
        → `projects/<pid>/worldbuilder/` 被**凭空重建**，成了个没有 `brief.json`
        的幽灵目录（列表里看不到、但占着名字）。
        ⇒ 决策交回调用方：**先取消**，或显式传 `force=true` 让我们替你取消。
        """
        p = payload or {}
        ids = p.get("project_ids") or ([p["project_id"]] if p.get("project_id") else [])
        if not ids:
            raise _bad("缺少 `project_ids`")
        force = bool(p.get("force"))

        active = {}
        for pid in ids:
            _resolve_pid(str(pid))
            runs = [r for r in runner.list_runs(str(pid), 20)
                    if r.get("status") not in runner.TERMINAL]
            if runs:
                active[str(pid)] = [r["run_id"] for r in runs]
        if active and not force:
            raise _conflict(
                "这些项目还有任务在跑，删目录会被任务**写回来**（变成幽灵目录）：%s。"
                "请先取消任务（DELETE /runs/{id}），或加 `force=true` 让我们代你取消后删除。"
                % json.dumps(active, ensure_ascii=False))

        cancelled = {}
        if active and force:
            for pid, rids in active.items():
                for rid in rids:
                    runner.cancel(rid, log=print)
                cancelled[pid] = rids

        dest = config.RUNTIME_ROOT / ".tmp" / "_deleted" / time.strftime("%Y%m%d-%H%M%S")
        moved, skipped = [], []
        for pid in ids:
            root = _resolve_pid(str(pid))
            dest.mkdir(parents=True, exist_ok=True)
            try:
                import shutil as _sh
                _sh.move(str(root), str(dest / str(pid)))
                moved.append(str(pid))
            except Exception as e:              # noqa: BLE001
                skipped.append("%s（%s）" % (pid, str(e)[:60]))
        return wm.envelope({
            "deleted": moved, "skipped": skipped,
            "cancelled_runs": cancelled,
            "trash": str(dest.relative_to(config.PROJECT_ROOT).as_posix()),
            "note": "已移入暂存区（**可恢复**），未真正删除",
        })

    app.include_router(r)
    # ★ 同一套路由再挂一次到 `/api` 前缀 —— 消除 baseUrl 带不带 `/api` 的歧义
    app.include_router(r, prefix="/api")

    # ── 兜底：**仍未接线**的写端点 → 501（不静默成功）──
    #
    # ★ 必须**注册在最后**（见上面的顺序说明）。用一条通配路由而不是逐个
    #   `add_api_route(模板)`：带 `{pid}` 的模板要求处理函数声明同名参数，
    #   否则 FastAPI 在 `create_app()` 时就抛；通配路由没有这个耦合，
    #   且不会因为漏登记某条写路径而让它变成 404（那更难查）。
    @app.api_route("/{rel:path}", methods=["POST", "PATCH", "PUT", "DELETE"])
    async def _write_blocked(rel: str, request: Request):
        return JSONResponse(
            status_code=501,
            content=wm.error_body(
                "尚未接线的写操作：%s /%s（%s）。"
                "创作链类（建项目 / 生成分镜）需 supervisor 图，见 ../docs-archive-20260918/spec-web-shim.md §23"
                % (request.method, rel, _write_hint(rel)),
                code="E501"))

    # ── 同源托管前端（`--web-root`）──
    #
    # ★ **必须挂在最后**：Starlette 按注册顺序匹配 → 挂在前面的 API 路由先命中，
    #   `mount("/")` 只兜住剩下的一切（前端静态文件）。
    #   `html=True` 让目录请求回落 `index.html`。
    #
    # ⚠️ 这是**有意把两棵目录合到一个 origin 上**，但**不合并仓库**：
    #   ★ 2026-09-18 起**前端应用代码就在本仓库的 `web/`**（index.html + assets/，19 个文件），
    #     所以这是"托管自己的前端"，不再是跨目录借用。
    #   （`web/` 里**只有应用代码**；抓取产物 `_recon/`、截图、素材仍在外面的
    #     `pavo-offline/` 工作目录里 —— 那些是实验资料，不该进本仓库。）
    #   真正的收益是**同源**（见 create_app 的 docstring），不是目录结构。
    if root_dir is not None:
        from fastapi.staticfiles import StaticFiles
        from starlette.responses import Response as _Resp

        # ★ 浏览器会自动请求 `/favicon.ico`。不给就是 **404 × 每次导航**，
        #   在控制台里刷成一堆红字，把真正的错淹掉（实测：同源页面上唯一的
        #   4xx 就是它，冒烟测试因此误报"有控制台 error"）。
        #   返回 204 让浏览器安静收场（前端本来也没放图标）。
        @app.get("/favicon.ico", include_in_schema=False)
        def _favicon():                       # noqa: ANN202
            return _Resp(status_code=204)

        class _NoCacheStatic(StaticFiles):
            """静态文件统一加 `Cache-Control: no-cache`。

            **为什么必须加**：`StaticFiles` 只发 `etag` / `last-modified`，
            **不发 `Cache-Control`** ⇒ 浏览器启用**启发式缓存**（约文件年龄的 10%），
            在你再次访问时**直接用旧文件、连问都不问**。
            实测反复踩到：「改了 JS/CSS，刷新还是旧界面，以为改动没生效」。

            `no-cache` **不是"不缓存"**，而是"每次先回来问一句"：
            命中 `etag` 就是 304 空响应，成本可忽略；收益是**改完刷新必定生效**。
            这是开发用 shim（同源托管本仓库 `web/`），正确性优先于那点带宽。
            """

            async def get_response(self, path, scope):     # noqa: ANN001, ANN201
                r = await super().get_response(path, scope)
                r.headers["Cache-Control"] = "no-cache"
                return r

        app.mount("/", _NoCacheStatic(directory=str(root_dir), html=True), name="web")

    return app


def _esc(s: str) -> str:
    import re
    return re.escape(s)


def main() -> int:
    ap = argparse.ArgumentParser(description="shortdrama web shim")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--web-root", default=os.environ.get("SHORTDRAMA_WEB_ROOT", ""),
                    help="要一起托管的前端静态目录。**默认就是本仓库的 `web/`**"
                         "（前端应用已搬进来）→ 通常不用传。传空串 `--web-root=` 可关掉")
    a = ap.parse_args()

    import uvicorn
    # ★ 2026-09-18：前端**应用代码**已搬进本仓库 `web/`（index.html + assets/，19 个文件）。
    #   所以这里**默认托管它** → 启动命令简化成 `python -m v5.server --port 8787`，
    #   不用再记 `--web-root ../pavo-offline`。
    #   ⚠️ 默认值只加在 **CLI** 这一层，`create_app()` 的语义**不动** ——
    #      测试要靠「不传 web_root = 旧两服务模式」来验证 `Origin: null` 的放行差异。
    web_root = a.web_root
    if web_root == "":
        cand = config.PROJECT_ROOT / "web"
        if (cand / "index.html").is_file():
            web_root = str(cand)
    root_dir = Path(web_root).resolve() if web_root else None
    # ★ 静态资源必须是**绝对 URL**：旧的两服务模式（前端 5500 / shim 8787）下
    #   根相对路径会被浏览器解析到页面 origin → 全部 404。
    #   `0.0.0.0` / `::` 不能出现在 URL 里 → 回落 127.0.0.1。
    #   **同源托管时不设**（`create_app` 里置空，根相对才是对的）。
    hostname = "127.0.0.1" if a.host in ("0.0.0.0", "::") else a.host
    base = "" if root_dir else (
        os.environ.get("SHORTDRAMA_WEB_BASE") or ("http://%s:%d" % (hostname, a.port)))
    if base:
        webmap.set_media_base(base)
    print("[shim] 项目目录：%s" % config.PROJECTS_DIR)
    if root_dir:
        print("[shim] ★ 同源托管前端：%s" % root_dir)
        print("[shim] ★ 打开这个就用：http://%s:%d/" % (hostname, a.port))
        print("[shim]   媒体走根相对路径（同源），已不再放行 `Origin: null`")
        print("[shim]   要用 file:// 双击打开的话，设 "
              "SHORTDRAMA_WEB_ALLOW_NULL_ORIGIN=1（知道代价再开）")
    else:
        print("[shim] 未托管前端（旧的两服务模式）：%s/web 不存在或 --web-root= 显式关掉了" %
              config.PROJECT_ROOT)
        print("[shim] 前端 baseUrl 请填：%s（不带 /api 也行）" % base)
    print("[shim] 探活：/health")
    uvicorn.run("v5.server:app" if a.reload else create_app(base, root_dir),
                host=a.host, port=a.port, reload=a.reload, log_level="info")
    return 0


def __getattr__(name):
    """让 `uvicorn v5.server:app` 也能用（延迟构建，避免 import 时就要 fastapi）。

    ⚠️ 模块级**不能有** `app = None` —— 那样 `app` 是已存在的属性，
    `__getattr__` 永远不触发，`from v5.server import app` 会拿到 `None`。
    """
    if name == "app":
        return create_app()
    raise AttributeError(name)


if __name__ == "__main__":
    raise SystemExit(main())
