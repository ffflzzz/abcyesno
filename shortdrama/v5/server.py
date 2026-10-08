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

    `web_root`：**要一起托管的前端静态目录**。**默认（CLI）是 `frontend/dist`**
    —— React 前端的构建产物（旧的原生 JS `web/` 已于 2026-09-30 退役删除）。
    这样是**同一个 origin**：
      · 不需要 CORS（同源请求浏览器不做 CORS 检查）
      · **可以不再放行 `Origin: null`**（补上那条到期的安全复查项）
      · 前端不用再手动配 `baseUrl`（`api.ts` 自检同源）
      · 少养一个静态服务进程（本机沙箱里那个进程会被回收）
    """
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, Response
    from . import webmap as wm

    web_root = web_root if web_root is not None else os.environ.get("SHORTDRAMA_WEB_ROOT", "")
    root_dir = Path(web_root).resolve() if web_root else None
    if root_dir is not None:
        if not (root_dir / "index.html").is_file():
            raise RuntimeError(
                "`web_root` 里没有 index.html：%s（应指向前端构建产物目录，"
                "默认是 frontend/dist，需先 npm run build）" % root_dir)
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

    # ── 本机出片服务（ComfyUI 等）：一键探测 / 接入 / 撤销（2026-10-05）──────
    #
    # 为什么这五条都放在这里而不是 Electron 侧自己发请求：厂商档、接入档案、
    # 轮询窗口全在 Python 这一侧（`v5/vendors.py` + `v5/local_services.py`）。
    # Electron 只是**另一个调用方**，与短剧前端 SPA 并列 —— 在 Electron 里再实现
    # 一遍探测与登记，就是同一件事两份真相，迟早只对一边生效。
    # ⚠️ 写端点会改**本进程的环境变量**（`SHORTDRAMA_VIDEO_VENDOR`），
    #    媒体子进程继承 ⇒ 下一次生成生效；已经在跑的那一轮不受影响。
    @r.get("/v1/pixa/short-drama/local-services")
    def local_services_get():
        """面板打开时的一屏：档案、当前厂商、为什么是它。**只读**。"""
        return wm.envelope(wm.local_services_status())

    @r.post("/v1/pixa/short-drama/local-services/probe")
    async def local_services_probe(payload: dict | None = None):
        """探测本机默认端口 + 手填地址。`tried[]` 每条都带「为什么不通」。"""
        try:
            return wm.envelope(wm.local_services_probe(payload))
        except ValueError as e:
            raise _bad(str(e))

    @r.post("/v1/pixa/short-drama/local-services/inspect")
    async def local_services_inspect(payload: dict | None = None):
        """先看那张工作流认不认得出参数落点（还没有任何写入）。"""
        try:
            return wm.envelope(wm.local_services_inspect(payload))
        except ValueError as e:
            raise _bad(str(e))

    @r.post("/v1/pixa/short-drama/local-services/connect")
    async def local_services_connect(payload: dict | None = None):
        """存档案 + 登记厂商档 + 按需设为本机默认。校验全在写盘之前。"""
        try:
            return wm.envelope(wm.local_services_connect(payload))
        except ValueError as e:
            raise _bad(str(e))

    @r.post("/v1/pixa/short-drama/local-services/default")
    async def local_services_default(payload: dict | None = None):
        """只翻「是否本机默认」这个开关（接入了但不想让它当默认时用）。"""
        try:
            return wm.envelope(wm.local_services_default(payload))
        except ValueError as e:
            raise _bad(str(e))

    @r.post("/v1/pixa/short-drama/local-services/forget")
    async def local_services_forget():
        """撤销接入：删档案与工作流存档，视频厂商退回云端。"""
        return wm.envelope(wm.local_services_forget())

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
    def progress(pid: str, include_content: str = "true", ep: int = 1):
        """项目进度。**`ep` 必须能传**（2026-10-02 补）：原先这条路由没有集号通道，
        于是 `progress` 内部永远按**第 1 集**算 —— 而它给的恰恰是按集的东西：
        `v5.render`（静帧/片段完成数、逐镜任务状态）、`v5.media_loop`、`v5.gates`、
        `flow.current_step`、`cover`。多集项目在第 2 集页面上显示第 1 集的渲染进度
        = 本项目最忌的「串集」（同 M1/M2 那批缺陷一个根）。

        ⚠️ `include_content` 这个参数是**历史形状**（线上有、代码从不读）——
        保留只为不破坏调用方，别误以为它控了什么。

        缺省 `ep=1` ⇒ 老调用方行为一字不变。

        ⚠️ 校验里**不能用 `ep or 1`**：`0 or 1` 得 1 ⇒ `?ep=0` 会被当成"没传"而静默
        按第 1 集处理，正是本项目反复踩过的"字符串/数字真值"坑（本条测试抓到的）。
        """
        ep_n = int(ep)
        if ep_n < 1:
            raise _bad("集号必须 ≥ 1（收到 %r）" % ep)
        root = _resolve_pid(pid)
        return wm.envelope(wm.progress(root, ep=ep_n))

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

    @r.get("/v1/pixa/short-drama/projects/{pid}/canvas")
    def project_canvas(pid: str, request: Request, ep: int = 1):
        """把这一集**已跑完的产物**摆成画布（确定性，不调模型、不烧配额）。

        返回的就是 Infinite Atelier 的 `importProject()` 能直接吃的形状
        （`title` / `nodes` / `connections` / `viewport`，多余字段它自动忽略），
        外加 `fingerprint`（输入指纹）与 `warnings`（哪一列没数据，不静默）。

        ★ 地址前缀取**本次请求的来源**：开发期画布在 `:3000`、后端在 `:8787`，
          填根相对路径会被浏览器解析到画布自己那个源 ⇒ 每个节点都是破图（实测）。
        """
        from . import canvasout
        return wm.envelope(canvasout.build(_resolve_pid(pid), ep,
                                           base=str(request.base_url)))

    # ══════════════════ 画布应用的模型代理（同源 · OpenAI 兼容形状）══════════════════
    #
    # ★ 为什么要这层：画布直连模型的话，密钥要在浏览器里**再填一份**，而且
    #   绕过后端的密钥池轮换 / 429 冷却 / 配额记账 —— 生成的东西日志和账本里都看不见。
    #   走这里，密钥只有 `.env` 那一份；真正出图仍调 `providers.gen_image`，
    #   与媒体链**同一条路**（这里不另写一份调用逻辑）。
    from . import aigc

    @r.get("/v1/models")
    def aigc_models():
        """只列后端真能服务的模型（见 `aigc.models_payload` 的说明）。"""
        return aigc.models_payload()

    @r.post("/v1/images/generations")
    def aigc_image_generations(payload: dict):
        try:
            urls = aigc.generate_images(payload or {})
        except Exception as exc:                       # noqa: BLE001
            # 不静默返回空列表 —— 那会让画布显示"成功但没有图"
            return JSONResponse(status_code=502, content={"error": {
                "message": "生图失败：%s" % str(exc)[:300], "type": "shortdrama_proxy"}})
        return aigc.image_response(urls)

    @r.post("/v1/images/edits")
    def aigc_image_edits(payload: dict):
        """带参考图的编辑。请求体是 **JSON + data URI**，不是 multipart 上传 ——
        理由与实测结论写在 `aigc.edit_images` 的文档里。"""
        try:
            urls = aigc.edit_images(payload or {})
        except Exception as exc:                       # noqa: BLE001
            return JSONResponse(status_code=502, content={"error": {
                "message": "参考图编辑失败：%s" % str(exc)[:300], "type": "shortdrama_proxy"}})
        return aigc.image_response(urls)

    @r.post("/v1/chat/completions")
    def aigc_chat(payload: dict):
        try:
            if (payload or {}).get("stream"):
                return Response(content=aigc.chat_sse(payload),
                                media_type="text/event-stream")
            return aigc.chat_completion(payload or {})
        except Exception as exc:                       # noqa: BLE001
            return JSONResponse(status_code=502, content={"error": {
                "message": "文本生成失败：%s" % str(exc)[:300], "type": "shortdrama_proxy"}})

    @r.post("/v1/videos")
    def aigc_video_submit(payload: dict):
        try:
            return aigc.submit_video_task(payload or {})
        except Exception as exc:                       # noqa: BLE001
            return JSONResponse(status_code=502, content={"error": {
                "message": "视频提交失败：%s" % str(exc)[:300], "type": "shortdrama_proxy"}})

    @r.get("/v1/videos/{video_id}")
    def aigc_video_status(video_id: str):
        try:
            return aigc.video_status(video_id)
        except Exception as exc:                       # noqa: BLE001
            return JSONResponse(status_code=502, content={"error": {
                "message": "视频查询失败：%s" % str(exc)[:300], "type": "shortdrama_proxy"}})

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

        ⚠️ **`p` 必须在第一次读它之前就赋值**（2026-10-02 修的真实缺陷）：原先
        `p = payload or {}` 写在 `ensure_devserver(...)` **之后**，而它的参数里就用了
        `p.get("manual_steps")` —— 函数内任何一处赋值都会把 `p` 变成**局部变量**，
        于是每次进来先 `UnboundLocalError`，再被下面的 `except Exception` 兜成 **502**。
        后果是「生成剧本正文」这个按钮**点一次失败一次**（两段式向导的第一段整段断开），
        而 `tests_server.test_gen_script_route_starts_script_kind` 一直是红的。
        同一形状的坑在 `/episodes/batch/storyboard/generate` 里**没有**（那边 `p` 在最前面），
        所以这条路由是漏改的那一个，不是有意为之。
        """
        p = payload or {}
        pid, ep = _resolve_eid(eid)
        try:
            st = webchain.ensure_devserver(pid, log=print,
                                           manual_steps=p.get("manual_steps"))
            print("[shim] dev server %s（%s）" % ("复用" if st.get("reused") else "新起", pid))
        except RuntimeError as e:
            raise _conflict(str(e)[:400])
        except Exception as e:                  # noqa: BLE001
            raise _server_error(str(e)[:400])
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
        p = dict(payload or {})
        # `source` / `by` 是**记账用的元数据**，不是分镜列 —— 必须先从 patch 里取走，
        # 否则 `update_segment` 会把它们列进 `skipped` 并回一条"这些列不存在"的警告，
        # 而画布上每次编辑都弹这个警告。
        src = str(p.pop("source", "") or "web")
        by = str(p.pop("by", "") or "")
        return _edit(webwrite.update_segment, webmap.project_root(pid), ep, shot,
                     p, src, by)

    @r.delete("/v1/pixa/short-drama/segments/{sid}")
    def delete_segment(sid: str, source: str = "web", by: str = ""):
        pid, ep, shot = _resolve_sid(sid)
        return _edit(webwrite.delete_segment, webmap.project_root(pid), ep, shot,
                     source or "web", by)

    @r.post("/v1/pixa/short-drama/episodes/{eid}/segments")
    def add_segment(eid: str, payload: dict | None = None):
        pid, ep = _resolve_eid(eid)
        p = dict(payload or {})
        src = str(p.pop("source", "") or "web")
        by = str(p.pop("by", "") or "")
        return _edit(webwrite.add_segment, webmap.project_root(pid), ep,
                     p.get("after", ""), p.get("fields"), src, by)

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

    # ══════════ 导演信箱（2026-10-06，工作室三栏界面 `frontend_new` 的入站半边）══════════
    #
    # 与上面 HITL 的分工（**两条都要，不是二选一**）：
    #   · HITL   = 链**正挂在步级门上**时才有决定可下（`decide` 无 pending 直接 400），
    #              而 `manual_steps` 默认关（`webchain.py:116`）⇒ 链一路跑到底时它不可用；
    #   · 信箱    = **任何时候**都能送话进去，投递点是 `roles.role_input`
    #              （每次派发都经过），所以下一个被派发的角色会读到人说的话。
    # 信道本体见 `v5/inbox.py`（含投递规则与"为什么不能广播"的事故说明）。
    @r.get("/v1/pixa/short-drama/projects/{pid}/director/inbox")
    def director_inbox(pid: str, ep: int = 1):
        """回显对话框：信箱历史 + 未投递的 + 仍然有效的改动台账 + 当前挂起状态。

        ★ 一次调用把**右栏要渲染的全套事实**给齐 —— 前端只轮询这一个端点，
        不为 hitl / 台账各开一条轮询（`runs/{id}` 搭车 `hitl` 是同一个理由）。
        """
        from . import guards, hitl, inbox
        root = _resolve_pid(pid)
        ep = max(1, int(ep or 1))
        st = wm.hitl_state(root)
        return wm.envelope({
            "messages": inbox.history(root, ep=ep, limit=80),
            "pending": inbox.pending_messages(root, ep=ep),
            "edits": inbox.live_edits(root, ep),
            "stats": inbox.status(root, ep=ep),
            "hitl": st,
            "manual_steps": bool(webchain.manual_steps_on()),
            # `targets` = **信箱消息**能定向给谁（含 `director`，那条走 HITL 通道）
            "targets": list(inbox.KNOWN_TARGETS),
            # ★ `idle_targets` = **链没挂起时**「让导演重做」能选谁 —— 只有 7 个被派发的
            #   角色，⛔ 不含 `director`。打回 `director` 的语义是"重写制作规格 +
            #   全部 7 个角色重做"（`drive_chain.redo_message`），那是**全项目重置**，
            #   绝不能出现在一个默认会被选中的下拉里。
            #   2026-10-07 实测事故：验收脚本点了一下重做按钮，就把一个 9 月的项目
            #   8 份产物全挪进 `.rerun_backup/`、`phases` 清空。
            "idle_targets": [r for r in guards.GATE_ROLES],
        })

    @r.post("/v1/pixa/short-drama/projects/{pid}/director/message")
    def director_message(pid: str, payload: dict | None = None):
        """往信箱里投一句话。body：`{text*, ep?, by?, to?}`。

        `to` 省略或空串 = **谁下一个被派发谁收到**；填角色名 = 定向（别的角色跳过，
        ⛔ 不许被它吃掉）。非法目标 400（不静默降级成"任意" —— 那会让一条本来
        定向的话被随便一个角色消费掉，而人以为它送到了分镜师）。

        ⚠️ 本端点**不判断链是否在跑**，也不假装"已经送到导演手上"：
        返回里的 `delivered_to` 是空串，前端据此显示「排队中」而不是「已发送」。
        """
        from . import inbox
        root = _resolve_pid(pid)
        p = payload or {}
        try:
            rec = inbox.append_message(
                root, str(p.get("text") or ""), ep=int(p.get("ep") or 1),
                by=str(p.get("by") or ""), to=str(p.get("to") or ""))
        except ValueError as e:
            raise _bad(str(e))
        return wm.envelope(rec)

    @r.post("/v1/pixa/short-drama/projects/{pid}/director/redo")
    def director_redo(pid: str, payload: dict | None = None):
        """「让导演重做」—— 画布改完表之后，人**显式**点它才回退重跑。

        ★ 为什么不自动做：`reset_from(scenedesigner)` 会把该角色**及全部下游**重做
          （实测整表重派 ≈95 分钟）。改一个错字也重做一遍是不可接受的，
          所以默认只入表 + 作废那几镜的素材，重做由人决定（AGENTS.md 的口径）。

        三条出口，按盘上事实选（**判据只写这一处**）：
          1. 链**正挂在门上** → 走 `hitl.decide(redo)`：既有路径，立刻生效；
          2. 本集**有非终态 run 在跑** → **409 拒绝**，不制造第二个写者
             （并行时杀掉别人的链是本项目明令禁止的动作）；
          3. 链没在跑 → `reset_from` 清记账 + 旧产物进 `.rerun_backup/` → 起一轮
             `chain` run。⚠️ 必须先 `reset_from` 再起：驱动器按"产物在不在盘上"
             派活，不撤销记账 ⇒ 重跑是空转（1006 实测过的那条）。
        """
        from . import guards, hitl, inbox
        root = _resolve_pid(pid)
        p = payload or {}
        ep = max(1, int(p.get("ep") or 1))
        note = str(p.get("note") or "").strip()
        target = str(p.get("target") or "").strip()
        by = str(p.get("by") or "")

        # 「有没有挂起」的判据**复用 `hitl_state`**，不在这里重算 ——
        # 它是 `GET /hitl` 用的同一份，且已经处理过"pending 文件在但已过期"。
        st = wm.hitl_state(root)
        if st.get("pending"):
            tgt = target or str(st.get("prev_role") or "")
            try:
                hitl.decide(root, "redo", by=by, note=note, target=tgt,
                            stamp=str(p.get("stamp") or st.get("stamp") or ""))
            except ValueError as e:
                raise _bad(str(e))
            return wm.envelope({"channel": "hitl", "target": tgt,
                                "hitl": st,
                                "note": "已作为「打回」交给正挂起的链路"})

        if not target:
            raise _bad("当前没有挂起的步骤，必须指明打回哪个角色"
                       "（target=worldbuilder/assetdesigner/…/reviewer）")
        # ★ 链**没挂起**时不许打回 `director`。它的语义不是"重派一个角色"，而是
        #   "重写制作规格 + 全部 7 个角色重做"（`drive_chain.redo_message`），
        #   等于**全项目重置** —— 不该能在没有一次真实挂起、没有人明确看到那条
        #   说明的情况下被点掉。
        #   2026-10-07 实测：无头验收脚本误点这一下，把一个 9 月项目的 8 份产物
        #   全挪进 `.rerun_backup/` 并清空了 `phases`（靠 `restore_stashed` +
        #   `reconcile_manifest` 才回得来）。判据写在服务端，不指望前端下拉做对。
        if target == "director":
            raise _bad("链当前没有挂起，不能打回 director —— 那等于重写制作规格并"
                       "重做全部 7 个角色（全项目重置）。要这么做请开逐步确认后重跑。")
        busy = [x for x in runner.list_runs(pid, 20)
                if str(x.get("status") or "") not in runner.TERMINAL]
        if busy:
            raise _conflict("本项目有 %d 个任务正在跑（%s）⇒ 不能同时起第二条链。"
                            "先停掉它，或等它挂在步骤上再点。"
                            % (len(busy), str(busy[0].get("run_id") or "")))

        m = guards.load_manifest(root)
        try:
            moved = guards.reset_from(target, m, root=root, ep=ep)
        except Exception as e:  # noqa: BLE001 —— 目标不是角色名等
            raise _bad("回退失败（%s）：%s" % (target, str(e)[:120]))
        try:
            inbox.append_message(root, "【人工要求重做】目标角色 %s。人写的理由：%s"
                                 % (target, note or "（未填写）"), ep=ep, by=by,
                                 to=target)
        except ValueError:
            pass
        rec = runner.start(pid, "chain", ep=ep,
                           image_vendor=str(p.get("image_vendor") or ""),
                           video_vendor=str(p.get("video_vendor") or ""))
        return wm.envelope({"channel": "chain", "target": target,
                            "reset_roles": moved, "run": rec,
                            "note": "已清空 %s 及其下游的本集记账并起了新一轮创作链"
                                    % target})

    # ══════════ 空白项目（2026-10-08）：不调模型，秒级建壳 ══════════
    @r.post("/v1/pixa/short-drama/projects/blank")
    def create_blank_project(payload: dict | None = None):
        """建一个**空白**项目：只有名字 + 类型包 + 集数 + 画幅，brief 的创作字段留空。

        与 `/projects/ai-generate` 的分工：那条要**先调一次 LLM**把一句话写成完整
        brief（10–40 秒，且输入太短会被拦）；这条**一步到位**，给你一个可以立刻
        跟导演聊起来的壳。创作字段由你和他定（见 `webchain.create_blank` 的说明）。
        """
        p = payload or {}
        name = str(p.get("name") or p.get("topic") or "").strip()
        if not name:
            raise _bad("给个名字（中文就行，目录名会自动生成）")
        return wm.envelope(webchain.create_blank(
            name, style_code=str(p.get("style_code") or p.get("pack") or ""),
            episodes=int(p.get("episodes") or 1), ratio=str(p.get("ratio") or "")))

    # ══════════ 和导演对话（2026-10-07）：聊清楚了，再开工 ══════════
    #
    # 与信箱的分工：信箱是把话**投递给下一个被派发的角色**（链没跑就永远排着）；
    # 这里是真的**跟 supervisor 本人说话** —— 一段被记住的会话，他回答你，
    # 你满意了再点开工，生产就跑在**同一段对话**上（`runner.start(chain_thread=…)`）。
    # 为什么能按住他不干活：见 `v5/director_chat.py` 的实测记录（消息里说明
    # "这是对话阶段" 之后，盘上新增文件 0 个）。
    @r.get("/v1/pixa/short-drama/projects/{pid}/director/chat")
    def director_chat_get(pid: str, ep: int = 1):
        """右栏的时间线 + 他这一轮答完没有。**顺带把新发言搬进时间线**（轮询端点）。"""
        from . import director_chat
        root = _resolve_pid(pid)
        st = director_chat.poll(root)
        return wm.envelope({
            "turns": st.get("turns") or [],
            "busy": bool(st.get("busy")),
            "status": st.get("status") or "",
            "thread_id": st.get("thread_id") or "",
            "error": st.get("error") or "",
        })

    @r.post("/v1/pixa/short-drama/projects/{pid}/director/chat")
    def director_chat_post(pid: str, payload: dict | None = None):
        """说一句。**不等回答**（和这个应用其它地方一个节奏：提交 → 轮询）。"""
        from . import director_chat
        root = _resolve_pid(pid)
        p = payload or {}
        res = director_chat.submit(root, str(p.get("text") or ""), int(p.get("ep") or 1))
        if not res.get("ok"):
            raise _bad(str(res.get("error") or "发不出去"))
        return wm.envelope(res)

    @r.post("/v1/pixa/short-drama/projects/{pid}/director/start")
    def director_start(pid: str, payload: dict | None = None):
        """**开工**：接着刚才那段对话，跑完整条创作链。

        `manual_steps`：每派一个角色前停一次等人确认（翻了要重启 dev server，
        只对下一次生成生效 —— 所以交给 `ensure_devserver` 去判断要不要重启）。
        """
        from . import director_chat
        root = _resolve_pid(pid)
        p = payload or {}
        ep = max(1, int(p.get("ep") or 1))
        tid = director_chat.thread_id(root)
        if not tid:
            raise _bad("还没有跟导演说过话 —— 先说一句，或直接开一条新对话")
        try:
            webchain.ensure_devserver(pid, wait_s=120,
                                      manual_steps=bool(p.get("manual_steps", False)))
        except Exception as e:  # noqa: BLE001
            raise _bad("起不了 dev server：%s" % str(e)[:160])
        rec = runner.start(pid, "chain", ep=ep, chain_thread=tid,
                           image_vendor=str(p.get("image_vendor") or ""),
                           video_vendor=str(p.get("video_vendor") or ""))
        return wm.envelope({"thread_id": tid, "run": rec})

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

    # ── 同源托管静态前端 ──
    #
    # ★ **必须挂在最后**：Starlette 按注册顺序匹配 → 挂在前面的 API 路由先命中，
    #   `mount("/")` 只兜住剩下的一切（前端静态文件）。
    #   ⚠️ 也正因为按顺序匹配，**子路径 `/atelier` 必须挂在 `/` 之前**，
    #   否则会被根挂载整个吞掉（实测：挂反时 /atelier/ 返回的是工作台首页）。
    #   `html=True` 让目录请求回落 `index.html`。
    #
    # ⚠️ 这是**有意把几棵目录合到一个 origin 上**，但**不合并仓库**：
    #   · `/`        → `frontend/dist`（React 工作台；旧的原生 JS `web/` 已于 2026-09-30 退役）
    #   · `/atelier` → `atelier/dist`（vendored 的画布应用 Infinite Atelier）
    #   · `/studio`  → `frontend_new/dist`（三栏工作室：项目栏 + 画布 + 导演对话）
    #   真正的收益是**同源**（见 create_app 的 docstring），不是目录结构。
    _mounts: list[tuple[str, Path, str]] = []
    _atelier = config.PROJECT_ROOT / "atelier" / "dist"
    if (_atelier / "index.html").is_file():
        _mounts.append(("/atelier", _atelier, "atelier"))
    else:
        # 没构建就**说清楚**，别让人对着 404 猜是接口坏了还是地址写错了。
        # ⚠️ 挂在 `app` 上而不是 `r` 上：`r` 已经 `include_router` 过了，
        #    事后再往 `r` 上加路由**不会生效**（实测踩过一次静默 404）。
        @app.api_route("/atelier", methods=["GET"], include_in_schema=False)
        @app.api_route("/atelier/{rest:path}", methods=["GET"], include_in_schema=False)
        def _atelier_missing(rest: str = ""):        # noqa: ANN202, ARG001
            return JSONResponse(
                status_code=503,
                content=wm.error_body(
                    "画布应用没构建：找不到 %s/index.html。先跑 "
                    "cd atelier && npm install && VITE_BASE=/atelier/ npm run build"
                    % _atelier, code="E503"))

    _studio = config.PROJECT_ROOT / "frontend_new" / "dist"
    if (_studio / "index.html").is_file():
        _mounts.append(("/studio", _studio, "studio"))
    else:
        @app.api_route("/studio", methods=["GET"], include_in_schema=False)
        @app.api_route("/studio/{rest:path}", methods=["GET"], include_in_schema=False)
        def _studio_missing(rest: str = ""):         # noqa: ANN202, ARG001
            return JSONResponse(
                status_code=503,
                content=wm.error_body(
                    "三栏工作室没构建：找不到 %s/index.html。先跑 "
                    "cd frontend_new && npm install && npm run build"
                    "（构建前缀由 vite.config 的 base 定，不用设环境变量）"
                    % _studio, code="E503"))

    if root_dir is not None:
        _mounts.append(("/", root_dir, "web"))

    if _mounts:
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
            """静态文件：统一加 `Cache-Control: no-cache` + 无扩展名路径回落 index.html。

            **为什么必须加 no-cache**：`StaticFiles` 只发 `etag` / `last-modified`，
            **不发 `Cache-Control`** ⇒ 浏览器启用**启发式缓存**（约文件年龄的 10%），
            在你再次访问时**直接用旧文件、连问都不问**。
            实测反复踩到：「改了 JS/CSS，刷新还是旧界面，以为改动没生效」。
            `no-cache` **不是"不缓存"**，而是"每次先回来问一句"：
            命中 `etag` 就是 304 空响应，成本可忽略；收益是**改完刷新必定生效**。

            **为什么要 SPA 回落**：画布应用是 **history 路由**（`createBrowserRouter`），
            深链 `/atelier/canvas` 在磁盘上没有对应文件 ⇒ 原本 404（实测）。
            只回落到 `index.html` 给**没有扩展名**的路径，让前端路由自己接管；
            带扩展名的（`.js` `.png`…）照旧 404 —— 否则丢一个图片文件会被伪装成
            "首页 HTML"，那种错最难查。
            （React 工作台不受影响：它走 hash 路由。）
            """

            async def get_response(self, path, scope):     # noqa: ANN001, ANN201
                from starlette.exceptions import HTTPException as _HTTPExc
                try:
                    r = await super().get_response(path, scope)
                except _HTTPExc as exc:
                    if exc.status_code != 404 or "." in path.rsplit("/", 1)[-1]:
                        raise
                    r = await super().get_response("index.html", scope)
                r.headers["Cache-Control"] = "no-cache"
                return r

        for _path, _dir, _name in _mounts:
            app.mount(_path, _NoCacheStatic(directory=str(_dir), html=True), name=_name)

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
                    help="要一起托管的前端静态目录。**默认是本仓库的 `frontend/dist`**"
                         "（React 前端的构建产物）→ 通常不用传。传空串 `--web-root=` 可关掉")
    a = ap.parse_args()

    import uvicorn
    # ★ 2026-09-30：默认托管切到 **`frontend/dist`**（React 那套成了唯一前端）。
    #   原先默认是本仓库 `web/`（原生 JS 旧版），`web/` 已退役删除。
    #   启动命令不变：`python -m v5.server --port 8787`。
    #   ⚠️ 默认值只加在 **CLI** 这一层，`create_app()` 的语义**不动** ——
    #      测试要靠「不传 web_root = 旧两服务模式」来验证 `Origin: null` 的放行差异。
    web_root = a.web_root
    if web_root == "":
        cand = config.PROJECT_ROOT / "frontend" / "dist"
        if (cand / "index.html").is_file():
            web_root = str(cand)
        elif cand.is_dir():
            # 目录在、产物没构建 —— 别让人以为"前端起了但打不开"是后端的问题
            print("[shim] ⚠️ %s 里没有 index.html ⇒ **前端没构建**，"
                  "本次只起 API。先跑：cd frontend && npm run build" % cand)
        else:
            print("[shim] ⚠️ 找不到 %s ⇒ 本次只起 API（前端需自己起或传 --web-root）" % cand)
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
    _ad = config.PROJECT_ROOT / "atelier" / "dist" / "index.html"
    print("[shim] 画布应用：%s" % ("同源挂在 /atelier" if _ad.is_file()
                                  else "⚠️ 没构建（点画布入口会 503）→ cd atelier && "
                                       "MSYS_NO_PATHCONV=1 VITE_BASE=/atelier/ npm run build"))
    if root_dir:
        print("[shim] ★ 同源托管前端：%s" % root_dir)
        print("[shim] ★ 打开这个就用：http://%s:%d/" % (hostname, a.port))
        print("[shim]   媒体走根相对路径（同源），已不再放行 `Origin: null`")
        print("[shim]   要用 file:// 双击打开的话，设 "
              "SHORTDRAMA_WEB_ALLOW_NULL_ORIGIN=1（知道代价再开）")
    else:
        print("[shim] 未托管前端（旧的两服务模式）：默认目录 frontend/dist 没有构建产物，"
              "或 --web-root= 显式关掉了")
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
