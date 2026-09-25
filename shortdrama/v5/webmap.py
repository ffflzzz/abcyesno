# -*- coding: utf-8 -*-
"""Web 映射层：v5 磁盘产物 → Pavo 前端契约。

**纯函数 + 只读**：不写盘、不发网络、不起服务。这样它可以脱离 HTTP 单测
（不需要 socket —— 本机沙箱里 uvicorn 起不来，见旧架构 `api-spec.md` 的先例，该文件已删）。

## 为什么单独一个模块

`server.py` 只负责「路由 + 错误码」，所有形状转换都在这里 —— **同一件事只有一个
地方可写**（本项目一贯纪律：判据/转换绝不写两份）。

## ★ 为什么发「超集」（两套键同时给）

前端 `views/wizard.js` 读的是 **local 形状**（`p.id` / `ep.no` / `s.video_prompt`），
而线上后端返回的是**另一套**（`project_id` / `episode_no` / `video_prompt_text`）。

一手证据（2026-09-15 实地勘察）：
  · `pavo-offline/assets/js/core/store.js` 的 `createProject()` → local 形状
  · `pavo-offline/_recon/_api/*.json` → 线上真实响应，字段名另一套
  · `pavo-offline/assets/js/views/wizard.js:22` `const p = S.getProject(...)` → 视图读 local

⇒ 两套键都给：前端无论读哪套都能跑，**且不必赌哪一套是"对的"**。
代价只是响应体略大（本项目量级可忽略）。

## 数据来源（全部只读）

| 端点 | 来源 |
|---|---|
| `/projects` | `projects/` 目录 + 各项目 `brief.json` |
| `/projects/{pid}/progress` | `brief.json` + `.agent_state.json` + `plotdesigner`/`scriptwriter` 产物 |
| `/projects/{pid}/asset-refs` | `assets.json` 注册表 |
| `/projects/{pid}/episodes/storyboard` | 同上（分集列表） |
| `/episodes/{eid}/storyboard/detail` | `scenedesigner/scenedesigner.md` + `stills.json` + `video_jobs.json` |
| `/styles` | `v5/skills/packs/*/pack.json` |
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config
from .media import assets, cast, jobs as jobs_mod, prompt, relations, stills, storyboard, style, video_plan
# ⚠️ 模块另起别名：本文件里 `vendors()` 是**对外的映射函数**（对应 `GET /vendors`），
#    与模块同名会把模块名遮住 ⇒ 函数体里就取不到 `vendors.names()` 了。
from . import vendors as vendors_mod

#: 前端 `core/api.js` 的 `http()` 认这个成功码（**字符串**，不是数字）：
#:     if (!r.ok || (json && json.code && json.code !== '000000')) throw ...
#: 注意 `json.code` 为 undefined 时该判据**短路放行** —— 所以返回裸 JSON 也能过，
#: 但我们统一走信封，保证只有一处产出响应体。
OK_CODE = "000000"

#: Pavo 的 token 形态：`@[显示名](sd-asset://<type>/<ref_id>)`
#: ⚠️ 与 v5 的 `@资产名` 契约**不同**（v5 的 `assets.resolve_mentions` 是按注册表名
#: 做长名优先匹配），故两个方向都要翻译，见 `token()` / `rich_to_v5()`。
_TOKEN_RE = re.compile(r"@\[([^\]]*)\]\(sd-asset://([^/)]+)/([^)]+)\)")

#: 默认形象名（Pavo 里每个资产的第一个 state 都叫这个）
BASE_STATE = "基础形象"

#: 分镜阶段 → 展示用
PHASE_DONE = "composed"
PHASE_DRAFT = "draft"


# ─────────────────────────────────────────────────────────── 响应信封

def envelope(data, message: str = "success") -> dict:
    """包成前端认的信封。**唯一的响应体产出点**。"""
    return {"code": OK_CODE, "message": message, "data": data}


def error_body(message: str, code: str = "ERROR") -> dict:
    """错误响应体。

    ⚠️ 这里的 `code` 会命中前端的错误分支（!= '000000'）—— 这是**有意的**：
    前端在 `!r.ok` 时也会抛，我们两者都给，错误信息才带得回去。
    """
    return {"code": code, "message": message, "data": None}


# ─────────────────────────────────────────────────────────── token 双向翻译

def token(kind: str, name: str, ref_id: str, state: str = BASE_STATE) -> str:
    """v5 资产 → Pavo token。`kind ∈ character|scene|prop`。"""
    return "@[%s - %s](sd-asset://%s/%s)" % (name, state, kind, ref_id)


def _kind_of(atype: str) -> str:
    """v5 资产类型 → Pavo 的 kind 段。"""
    a = str(atype or "").strip().lower()
    if a == "character":
        return "character"
    if a in assets.LOCATION_TYPES:      # ("location", "scene")
        return "scene"
    return "prop"


def _split_display(display: str) -> tuple:
    """`纸扎匠 - 基础形象` → (`纸扎匠`, `基础形象`)。

    线上实测两种分隔写法都有（`"纸扎匠 - 基础形象"` / `"纸扎铺-基础形象"`），都用同一个
    正则按「第一个 `-`」切开；切不开时整体当资产名、state 为空。
    """
    s = str(display or "").strip()
    m = re.match(r"^\s*(.*?)\s*-\s*(.*?)\s*$", s)
    if m and m.group(1):
        return m.group(1), m.group(2)
    return s, ""


def rich_to_plain(text: str) -> str:
    """`content_rich` → `content_plain`：token 换成它的显示名。

    线上是**成对**给的（`content_rich` 带 token、`content_plain` 是纯文本），
    `content_plain` 正是喂给 v5 的那一版。
    """
    return _TOKEN_RE.sub(lambda m: m.group(1), str(text or ""))


def rich_to_v5(text: str, names: list) -> tuple:
    """Pavo 富文本 → v5 的 `@资产名` 形式。

    返回 `(转换后文本, 未解析列表)`。

    **未解析必须上报、不许静默**（本项目铁律：静默丢数据是最贵的一类 bug）：
    拿不到对应资产名的 token 会原样保留在文本里，并把**待匹配的基名**
    收进 `unresolved` —— 报基名而不是全名，是为了与出方向（`_to_rich` 报
    `@xxx` 里的裸名）**口径一致**：两处都报"注册表里缺哪个名字"。
    """
    known = sorted({str(n) for n in (names or []) if n}, key=len, reverse=True)
    unresolved: list = []

    def _sub(m):
        display, _kind, _rid = m.group(1), m.group(2), m.group(3)
        base, _state = _split_display(display)
        for n in known:
            if base == n or base.startswith(n):
                return "@" + n
        unresolved.append(base)
        return m.group(0)          # 原样保留，不猜

    return _TOKEN_RE.sub(_sub, str(text or "")), unresolved


# ─────────────────────────────────────────────────────────── 只读取数（全部容错）

def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:           # noqa: BLE001 —— 缺文件/脏 JSON 一律回落默认值
        return default


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:           # noqa: BLE001
        return ""


def project_root(pid: str) -> Path:
    """`pid` → 项目根目录。**必须做越界校验**（调用方负责，见 `server.py`）。"""
    return config.PROJECTS_DIR / pid


def list_pids() -> list:
    """全部项目名（目录名即 pid）。

    ★ **只认有 `brief.json` 的目录**（2026-09-15 实测修正）。
    `projects/` 下并不只有项目 —— 实测多出 4 个非项目目录：
      · `__lockout__` / `__open__` —— 锁相关的临时目录（各含一个 `media/`）
      · `studio` —— `orchestrator` 在 import 时 `mkdir()` 出来的默认工作目录
      · `.tmp` —— 隐藏目录
    旧实现把它们全当项目 → 前端列表多出 4 行**空项目**。

    判据选「有 brief.json」而不是"排除这几个名字"：`brief.json` 正是 v5 自己
    定义项目的凭据（`run_new_project.py` 第一个写它，各角色 `read_file /brief.json`），
    以后新增临时目录**不必再改这里**。
    """
    root = config.PROJECTS_DIR
    if not root.exists():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if not p.is_dir() or p.name.startswith(".") or p.name.startswith("_"):
            continue
        if not (p / "brief.json").exists():
            continue
        out.append(p.name)
    return out


def brief(root: Path) -> dict:
    return _read_json(root / "brief.json", {}) or {}


def manifest(root: Path) -> dict:
    """`.agent_state.json`（黑板）。**只读，绝不写**。"""
    return _read_json(root / ".agent_state.json", {}) or {}


def media_dir(root: Path, ep: int = 1) -> Path:
    return root / "media" / ("ep" + str(ep))


def shots(root: Path, ep: int = 1) -> list:
    """分镜表 → shots（复用生产解析器，不自己写一份）。

    ⚠️ 必须走 `guards.resolve_path`（集级路径 + 旧名回退）—— 写死 `scenedesigner.md`
    会让前端在**每一集**都显示第 1 集的分镜（静默串集，2026-09-16 M1 修）。
    """
    from . import guards
    p = guards.resolve_path(root, "scenedesigner", int(ep))
    if not p.exists():
        return []
    try:
        return storyboard.parse(p.read_text(encoding="utf-8"))
    except Exception:           # noqa: BLE001
        return []


def stills_map(root: Path, ep: int = 1) -> dict:
    try:
        return stills.load(stills.stills_dir(root, ep)) or {}
    except Exception:           # noqa: BLE001
        return {}


def jobs_map(root: Path, ep: int = 1) -> dict:
    try:
        return jobs_mod.load(media_dir(root, ep)) or {}
    except Exception:           # noqa: BLE001
        return {}


def registry(root: Path) -> dict:
    try:
        return assets.load_registry(root) or {}
    except Exception:           # noqa: BLE001
        return {}


# ─────────────────────────────────────────────────────────── 步骤推导

#: 与前端的步骤 key 对齐（`wizard.js` 的 STEPS：script / assets / episodes）
STEPS = ("outline", "assets", "episodes", "storyboard", "render")


def current_step(root: Path, ep: int = 1) -> str:
    """当前流程步骤。

    ★ **不新增状态文件**（spec §16「不做」第 2 条）：全部由既有产物推导。
    每一条都是一个**磁盘事实**，不是谁的自我报告。
    """
    if not brief(root):
        return "outline"
    if not (registry(root).get("assets") or []):
        return "assets"
    if not (root / "plotdesigner" / "episodes.md").exists():
        return "episodes"
    if not shots(root, ep):
        return "storyboard"
    if not (media_dir(root, ep) / "episode_final.mp4").exists():
        return "render"
    return "render"


def _review_passed(root: Path) -> bool:
    """评审是否通过（`media_gate` 的放行条件之一）。"""
    rev = manifest(root).get("review") or {}
    return bool(rev.get("passed") or rev.get("force_passed"))


def flow(root: Path, ep: int = 1) -> dict:
    """给前端的流程状态。字段名沿用线上（`flow.current_step`）。"""
    step = current_step(root, ep)
    os_ = list(STEPS)
    idx = os_.index(step) if step in os_ else 0
    return {
        "current_step": step,
        "allowed_actions": [],
        "blocked_actions": [],
        "recommended_actions": [],
        # 便利字段（超集）：前端还没用，但排障/调试直接可读
        "steps": [{"key": k, "state": ("done" if i < idx else "active" if i == idx else "todo")}
                  for i, k in enumerate(os_)],
        "review_passed": _review_passed(root),
    }


# ─────────────────────────────────────────────────────────── /projects

def _episode_count(root: Path) -> int:
    """本项目有几集。

    ★ **没有 `brief.json` 就返回 0**（而不是默认 1）：那种目录根本不是 v5 项目
    （真项目都由 `run_new_project.py` 写出 brief）。返回 1 会**凭空造出一个分集行** ——
    违反本项目一贯的「缺就留空，绝不编」。实测：这一条被 `test_bare_dir` 抓到。
    """
    b = brief(root)
    if not b:
        return 0
    try:
        n = int(b.get("episodes") or 1)
    except Exception:           # noqa: BLE001
        n = 1
    return max(1, n)


def episode_id(pid: str, ep: int) -> str:
    """稳定的 eid。**可逆**：`storyboard_detail()` 靠它反解出 (pid, ep)。

    为什么不学线上用雪花 id：v5 的产物是目录，`ep` 是目录名的组成部分，
    自造一个不透明的 id 只会多一层「id → 目录」的查表，且无法手工核对。
    """
    return "%s-ep%d" % (pid, ep)


def parse_episode_id(eid: str) -> tuple:
    """`<pid>-ep<N>` → (`<pid>`, N)。**任何不合法都返回 `("", 0)`**。

    ⛔ ep < 1 也算不合法（`x-ep0` 曾返回 `("x", 0)` —— "半有效"状态会让调用方
    只查 pid 就以为通过了）。契约是：要么两个都有效，要么两个都空。

    ⚠️ pid 自身可能含 `-`（如 `village-tree`），故必须**从右侧**切。
    """
    m = re.match(r"^(.*)-ep(\d+)$", str(eid or "").strip())
    if not m:
        return "", 0
    n = int(m.group(2))
    if n < 1:
        return "", 0
    return m.group(1), n


def _cover(root: Path, ep: int = 1) -> str:
    """封面：第一张静帧的 URL（前端 `playlet-list.js:122` 的回落路径）。"""
    st = stills_map(root, ep)
    for _name, info in sorted(st.items()):
        rel = _still_relpath(root, info)
        if rel:
            return media_url(root.name, rel)
    return ""


def _still_relpath(root: Path, info: dict) -> str:
    """`stills.json` 条目 → 相对项目根的路径（如 `media/ep1/stills/LN01.jpg`）。

    ⛔ **不能靠拼字符串**：`stills.json` 里存的是**绝对路径**
    （`.../projects/<pid>/media/ep1/stills/LN01.jpg`），集号在路径中间。
    按 `Path.relative_to(root)` 求解，拿不到（老产物/换过目录）就返回空 ——
    **绝不猜一个看起来对的路径**（拼错的 URL 会让前端稳定 404，比空更难查）。
    """
    p = str((info or {}).get("path") or "")
    if not p:
        return ""
    try:
        return Path(p).resolve().relative_to(root.resolve()).as_posix()
    except Exception:           # noqa: BLE001
        return ""


#: 静态资源的**绝对前缀**（如 `http://127.0.0.1:8787`），由 `server.py` 启动时注入。
#:
#: ★★ 为什么必须是**绝对** URL（2026-09-15 真实浏览器实测）：
#:   若返回**根相对**路径（`/media/{pid}/…`），浏览器会把它解析到**页面所在的 origin**。
#:   前后端同源时看似没事；但本方案的常态就是**不同源**（前端静态服务 5500 / shim 8787）
#:   → 所有媒体请求都打到了前端静态服务 → **26 条 404、13 张坏图**。
#:   而线上后端返回的本来就是绝对 URL（`https://cos-aigc-…/x.jpg`），
#:   所以绝对化**更贴契约**，不是权宜之计。
MEDIA_BASE = ""


def set_media_base(base: str) -> None:
    """由 `server.py` 在启动时调用（也可用 `SHORTDRAMA_WEB_BASE` 覆盖）。"""
    global MEDIA_BASE
    MEDIA_BASE = str(base or "").rstrip("/")


def media_url(pid: str, relpath: str) -> str:
    """项目内相对路径 → 可直接放进 `<img src>` 的 URL。

    `MEDIA_BASE` 为空时回落根相对路径（仅适合同源部署 / 纯函数测试）。
    """
    return "%s/media/%s/%s" % (MEDIA_BASE, pid, str(relpath or "").lstrip("/"))


def _project_row(root: Path, is_demo: bool = False) -> dict:
    pid = root.name
    b = brief(root)
    pack = str(b.get("pack") or "shortdrama")
    st = _style_entry(pack)
    n_ep = _episode_count(root)
    row = {
        # ── local 形状（视图在读）──
        "id": pid,
        "name": str(b.get("topic") or pid),
        "ratio": str(b.get("ratio") or config.ASPECT_RATIO),
        "style": {"style_id": pack, "code": pack, "name": st["name"], "cover_url": ""},
        "status": "completed" if _review_passed(root) else "draft",
        "cover": _cover(root),
        "created_at": _mtime_str(root),
        "episodes": [_episode_row(root, n, b) for n in range(1, n_ep + 1)],
        # ★ `outline` **每个返回项目的端点都要有**。
        #   实测教训（2026-09-15）：列表端点原本只给 episodes、没有 outline →
        #   前端 `p.outline.story_type` 直接抛 TypeError → **整页崩**。
        #   形状不一致 = 消费方必须记住"哪个端点给哪个键"，一定会漏。
        "outline": _outline(root, b),
        "assets": asset_refs(root),
        "flow": flow(root),
        # ── 线上形状（超集）──
        "project_id": pid,
        "projectName": str(b.get("topic") or pid),
        # ★ 「精选项目」= 线上 `GET /projects?is_demo=true` 的那一批（2026-09-18 接通）。
        #   判据来自**根级清单文件**（见 `featured_pids`），不是项目自身的属性。
        #   `project_type` 跟着走：线上这个字段就是 `user` / `demo` 两值。
        "project_type": "demo" if is_demo else "user",
        "is_demo": bool(is_demo),
        "source_type": "paste_text",
        "planned_episode_count": n_ep,
        "asset_locked": False,
        "assets_finalized": bool(registry(root).get("assets") or []),
        "auto_sync_assets": True,
        "readonly": False,
    }
    return row


def _mtime_str(root: Path) -> str:
    """项目「创建时间」= 目录 mtime（不新增状态文件，只能取文件系统事实）。"""
    import time
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(root.stat().st_mtime))
    except Exception:           # noqa: BLE001
        return ""


#: 精选项目清单文件名（放在 `projects/` 根下）。
#: 结构：`{"pids": ["<项目名>", ...]}`。
FEATURED_FILE = "featured.json"


def featured_pids() -> set:
    """读「精选项目」清单 → 项目名集合。

    ## 为什么是**根级清单文件**，而不是项目目录里的标记文件

    「是不是精选」是**运营决定**，不是项目自身的属性。加/取消精选不该去动项目目录
    —— 那里是创作产物，有产物契约（`brief.json` / `assets.json` / …）和独占锁，
    往里插一个"标记文件"会污染 `list_pids()` 的判据面。
    一个文件一处看全，日后要换成别的来源（数据库 / 远端）也只改这一个函数。

    ## 为什么文件不存在**不报错**

    不存在 = **未配置** = 精选为空。这是有意的：宁可诚实地说"没有精选项目"，
    也不要凭空编几个出来。前端据此显示"后端暂无精选项目"，而不是假装离线。

    ⚠️ 解析失败（坏 JSON / 形状不对）同样按"空"处理，但**不吞掉**其他异常类型
    —— 只宽恕坏内容，不宽恕"读文件本身失败"（那通常是权限/磁盘问题，应当暴露）。
    """
    p = config.PROJECTS_DIR / FEATURED_FILE
    if not p.exists():
        return set()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return set()
    if isinstance(d, dict):
        d = d.get("pids")
    if not isinstance(d, list):
        return set()
    return {str(x).strip() for x in d if str(x).strip()}


def project_list(is_demo: bool | None = None) -> list:
    """`GET /projects?page=&page_size=&is_demo=`。

    ⚠️ 分页**在 server 层做**（这里返回全量）—— 项目数是个位到几十，
    且前端 `listProjects(page, pageSize)` 只是把参数透传，线上也是全量再切片。

    `is_demo` 三态：
      · `None`（缺省）—— **全量**，不过滤。给"不关心精选与否"的调用方用，
        也保证老调用方行为不变。
      · `True`  —— 只要精选（线上点「精选项目」tab 就是这一支）。
      · `False` —— 只要非精选（线上「我的项目」）。
    两态与线上一致：`is_demo` 为布尔时，返回集合**互斥且穷尽**。
    """
    feats = featured_pids()
    rows = [_project_row(project_root(pid), pid in feats) for pid in list_pids()]
    if is_demo is None:
        return rows
    return [r for r in rows if bool(r.get("is_demo")) == bool(is_demo)]


# ─────────────────────────────────────────────────────────── /progress

def _script_path(root: Path, ep: int) -> Path:
    return root / "scriptwriter" / ("scriptwriter_ep%d.md" % ep)


def _episode_title(root: Path, ep: int, b: dict) -> str:
    """集标题：优先 `plotdesigner/episodes.md` 里写的，拿不到回落「第 N 集」。

    解析刻意保守 —— 取不到就回落，**不去猜结构**（猜错的标题比没有标题更糟）。
    """
    md = _read_text(root / "plotdesigner" / "episodes.md")
    if md:
        m = re.search(r"第\s*%d\s*集[^\n]*?[：:]\s*(.+)" % ep, md)
        if m:
            t = m.group(1).strip().strip("*#| ")
            if t:
                return t[:60]
    return "第 %d 集" % ep


def _episode_row(root: Path, ep: int, b: dict) -> dict:
    """单集（**超集**：local 的 `no`/`script` 与线上的 `episode_no`/`content` 都给）。"""
    pid = root.name
    eid = episode_id(pid, ep)
    script = _read_text(_script_path(root, ep))
    md = media_dir(root, ep)
    sb = md / "episode_final.mp4"
    sh = shots(root, ep)
    return {
        # local 形状
        "id": eid,
        "no": ep,
        "title": _episode_title(root, ep, b),
        "summary": "",
        "script_status": "completed" if script else "draft",
        "script": script,
        # ★ local 形状要求 `storyboard` 这个键**一定存在**（前端 `ep.storyboard.segments`
        #   在多处被直接取用，如 `playlet-list.js:140` 的封面回落）。
        #   这里只给**轻量壳**（阶段 + 空 segments）：完整分镜走
        #   `GET /episodes/{eid}/storyboard/detail` —— 列表接口不该变重。
        "storyboard": {
            "episode_id": eid, "episode_no": ep,
            "title": _episode_title(root, ep, b),
            "phase": PHASE_DONE if sh else PHASE_DRAFT,
            "storyboard_phase": PHASE_DONE if sh else PHASE_DRAFT,
            "ratio": str(b.get("ratio") or config.ASPECT_RATIO),
            "segments": [],
        },
        # 线上形状
        "episode_id": eid,
        "episode_no": ep,
        "episode_summary": "",
        "content": script,
        # 便利字段（终端用户直接可读，便于排障）
        "duration_s": _estimated_seconds(root, ep),
        "has_final": sb.exists(),
        "shots": len(sh),
    }


def _estimated_seconds(root: Path, ep: int) -> float:
    """本集**预计**时长 = 各镜秒数之和。

    ⛔ 名字刻意不叫 `final_seconds`：它不是成片时长。
    `compose.duration()` 才是真值，但那要 ffprobe 起子进程 ——
    **静态端点不该起子进程**（一次列表刷新就 fork 一次 ffprobe 不可接受）。
    故这里给的是**确定性、零成本**的估计值；真时长由 `progress.v5.render.final`
    的存在性 + 成片文件本身提供给前端（前端可以用 `<video>` 自己读）。
    """
    sh = shots(root, ep)
    try:
        return round(sum(float(s.get("seconds") or 0) for s in sh), 1)
    except Exception:           # noqa: BLE001
        return 0.0


def _outline(root: Path, b: dict) -> dict:
    """概要。**全部确定性拼装，不调 LLM**。

    为什么不调 LLM 做摘要（本 spec 初版曾这么计划）：
      ① 静态端点的成本必须是 0（一次页面刷新就调一次 LLM 不可接受）；
      ② 非确定性内容不适合当契约字段（每次刷新摘要都变，前端无法稳定展示）；
      ③ brief 里本来就有这些字段 —— 它们就是被角色读去写剧情的那份输入。
    缺失的字段**留空串**，绝不编。
    """
    must = b.get("must_have") or []
    if not isinstance(must, list):
        must = [str(must)]
    # ★ **两个字段都给**（结构 + 值），消费方按能读到的那个渲染：
    #   · `story_summary_items` —— must_have 的**原样列表**。有它前端才能把
    #     "第一幕(钩子)：… / 第二幕：… / …" 渲染成**分条**，而不是一整段糊在一起。
    #     （2026-09-18：正是这一坨让步骤一的「剧情概要」读不下去。）
    #   · `story_summary` —— 兼容既有的"一坨"写法（视图/测试/外部调用都在读它）。
    #     ⚠️ 两者**不许各自拼一遍**：字符串永远是列表的 join，一个来源。
    items = [str(x).strip() for x in must if str(x).strip()]
    summary = str(b.get("story_summary") or "").strip()
    if not summary and items:
        summary = " / ".join(items)

    bios = []
    reg = registry(root)
    # ⛔ 主角判定必须**按 id 比较**，不能用 `a is prot`：
    #    `registry()` 每次调用都重新读盘解析 JSON → 返回的是**新对象**，
    #    身份比较恒为 False（我第一版就这么写错过）。
    prot_id = ""
    try:
        prot = assets.protagonist(root, reg) or {}
        prot_id = str(prot.get("id") or prot.get("name") or "")
    except Exception:           # noqa: BLE001
        prot_id = ""

    for a in (reg.get("assets") or []):
        if _kind_of(a.get("type")) != "character":
            continue
        aid = str(a.get("id") or a.get("name") or "")
        bios.append({
            "name": str(a.get("name") or ""),
            "bio": str(a.get("identity") or a.get("ref_line") or "").strip(),
            # 线上用 protagonist / supporting；v5 的主角由 priority + 注册表序决定
            "character_role": ("protagonist" if (prot_id and aid == prot_id) else "supporting"),
            # 便利字段：v5 侧的资产 id，便于前端做反向定位
            "asset_id": aid,
        })

    world = _read_text(root / "worldbuilder" / "worldbuilder.md")
    passed = _review_passed(root)
    return {
        "story_type": str(b.get("genre") or ""),
        "target_audience": str(b.get("target_audience") or ""),
        "one_line_story": str(b.get("topic") or ""),
        "story_summary": summary,
        # 与 `story_summary` 同源的结构化版本（见上面 items 的说明）：有它前端才分得开
        # "真的有一段概要" 与 "只有 must_have 硬要求" —— 后者该分条列，不该糊成一段。
        "story_summary_items": items,
        # 「世界观设定」优先按节取；没有该节再回落摘录（见 `_world_section`）
        "world_setting": _world_section(world, 600),
        "character_biographies": bios,
        "outline_status": "locked" if passed else "draft",
        "editable": not passed,
        # 便利字段：v5 brief 里前端还没有对应位置、但展示价值高的
        "target_duration": str(b.get("target_duration") or ""),
        "tone": str(b.get("tone") or ""),
        "ending": str(b.get("结局") or ""),
        "taboos": list(b.get("禁忌") or []),
        "key_props": list(b.get("key_props") or []),
    }


def _excerpt(md: str, limit: int = 600) -> str:
    """取正文摘录（跳过标题、表格、**块引用**行），拼到预算为止。

    ⛔ **不能只取"第一行非标题"**（2026-09-15 实测）：`worldbuilder.md` 是
    **结构化简报**而不是散文，首行非标题恰好是 `- 主题：阿凯的蜂箱`（10 字）——
    于是 `world_setting` 只有 10 个字，前端等于没内容。
    逐行累加到预算，既确定性又能拿到成段内容。

    ⛔ **`>` 行必须跳过**（2026-09-18 实测 `bach-daily-ep1`）：`worldbuilder.md`
    开头那段块引用是**写给下游 Agent 看的元信息与指令**，不是世界观 ——
      · `> 类型包 niulai-movie-style ｜ primitive_folk_cgi（反质量预设…）`
      · `> 本文件是渲染风格的唯一权威：下游分镜、静帧、视频提示词、QC 判定全部以此为准。`
    它们会排在最前面（摘录从头部取）⇒ 前端「世界观设定」第一屏就是这两行，
    既是噪声又会被误读成"这部片的世界观"。**指令与内容不能混在一个字段里。**
    """
    picked: list = []
    total = 0
    for line in str(md or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("|") or s.startswith(">"):
            continue
        picked.append(re.sub(r"^[-*]\s*", "", s))
        total += len(s)
        if total >= limit:
            break
    return "\n".join(picked)[:limit]


#: 「世界观设定」章节标题（`## 世界观设定` / `### 世界观设定（荒诞版）`…）。
#: 判据只认章节名里的**世界观**两个字 —— 实测 `worldbuilder.md` 的编号花样很多
#: （`二、` / `二.` / 无编号），编号不能进判据。
RE_WORLD_SECTION = re.compile(r"^(#{2,3})\s*[^\n]*世界观[^\n]*$", re.M)


def _world_section(md: str, limit: int = 600) -> str:
    """`world_setting` 的取值：优先取「世界观设定」**那一节**，取不到再回落摘录。

    为什么要按节取、而不是继续"从头摘 N 字"（2026-09-18 对照真实产物）：
      ① 头部块引用是给下游 Agent 的**指令**（见 `_excerpt` 的说明）⇒ 噪声 + 误导；
      ② `worldbuilder.md` 的第一节通常是「故事大纲（四幕，逐字对应 must_have）」，
         摘录必然把它吃进来 ⇒ 前端「剧情概要」与「世界观设定」**显示同一批内容**。
    按节取一次解决这两件事。

    ⚠️ 取不到该节时**回落到 `_excerpt`**：宁可内容糙一点，也不要字段空着
    （实测有 5 个项目没有这一节：`empty-flat` / `felt-bach-serial` / `jade-fish` /
      `felt-frog` / `lost-and-found`）。回落是**兜底**，不是"找不到就算了"。
    """
    s = str(md or "")
    m = RE_WORLD_SECTION.search(s)
    if not m:
        return _excerpt(s, limit)
    level = len(m.group(1))
    body = s[m.end():]
    # 切到下一个**同级或更高级**的标题为止（`## 三、视觉风格指南` 属于同级 → 停）
    stop = re.search(r"^#{1,%d}\s" % level, body, re.M)
    if stop:
        body = body[:stop.start()]
    got = _excerpt(body, limit)
    return got or _excerpt(s, limit)


def _render_state(root: Path, ep: int = 1) -> dict:
    """渲染进度（给前端出片页）。全部来自既有产物，不新增状态。

    ★ M2：`media_loop` 走 `guards.media_loop_of(root 的 manifest, ep)` ——
    一维直读会让**每一集**的出片页都显示第 1 集的渲染状态（串集）。
    """
    from . import guards
    jb = jobs_map(root, ep)
    st = stills_map(root, ep)
    sh = shots(root, ep)
    clip_dir = media_dir(root, ep) / "clips"
    done_clips = len(list(clip_dir.glob("LN*.mp4"))) if clip_dir.exists() else 0
    ml = guards.media_loop_of(guards.load_manifest(root), ep)
    _final = media_dir(root, ep) / "episode_final.mp4"
    return {
        "expected_shots": len(sh),
        "stills_ready": sum(1 for v in st.values() if (v or {}).get("url")),
        "clips_ready": done_clips,
        "rendered": bool(ml.get("rendered")),
        "final": _final.exists(),
        # ★ 2026-09-19（D）：成片的**可播 URL**（此前只给 bool，前端拿不到片子）。
        #   没有成片 → 空串（**不编造一个 404 的地址**：`<video src="">` 会静默
        #   显示黑框，看起来像"加载失败"）。
        "final_url": media_url(root.name, "media/ep%d/episode_final.mp4" % ep)
                     if _final.exists() else "",
        "final_bytes": (_final.stat().st_size if _final.exists() else 0),
        "jobs": {k: {"state": (v or {}).get("state") or "pending",
                     "error": str((v or {}).get("error") or "")[:200]}
                 for k, v in jb.items()},
    }


def _registry_warnings(root: Path) -> list:
    """注册表里"看起来不该在"的条目 → **只报告，绝不过滤**。

    实测（2026-09-15，`village-bees`）：注册表里有 `老周.source` / `小林.source` /
    `阿凯.source` 三条**源照片伪资产**（`identity` 为空）。它们来自 `assets.auto_sync`
    修好之前的版本 —— 那个 bug 已修（现在显式跳过 `*.source.*`，理由：「源照片是**输入**、
    不是资产」），但**旧项目盘上的数据还在**。

    ⛔ 为什么不在 shim 里过滤掉：那会把 `auto_sync` 的规则**复制一份**（本项目铁律：
    同一判据绝不写两份），而且会**掩盖真实的数据问题** —— 前端看不到、人也就不会去清。
    正确做法是报出来（与本项目「失败必须可见」一致）。
    """
    out: list = []
    for a in (registry(root).get("assets") or []):
        nm = str(a.get("name") or "").strip()
        if not nm:
            out.append("注册表有条目**没有名字** → 会在资产列表里显示成空白卡")
            continue
        if ".source" in nm:
            out.append("注册表条目「%s」像**源照片伪资产**（旧 `auto_sync` bug 的残留）——"
                       "源照片是输入、不是资产，建议从 assets.json 清掉" % nm)
            continue
        # 只对**角色**要求身份锚点：场景/道具本来就不需要 identity
        if _kind_of(a.get("type")) == "character" \
                and not str(a.get("identity") or a.get("ref_line") or "").strip():
            out.append("角色「%s」没有身份锚点（identity）——多人物同框时容易串脸" % nm)
    return out


# ─────────────────────────────────────── /hitl（步级「逐步人工确认」，2026-09-18）

def hitl_state(root: Path) -> dict:
    """`GET /projects/{pid}/hitl` —— 前端「逐步人工确认」的状态。

    ## 全部取**磁盘事实 / 状态文件事实**，没有一条是从本进程环境推的

    尤其是 `manual_steps`：那个开关由 **dev server 进程**在编译期读
    （`orchestrator.py:443` 的模块级 `build_supervisor()`），而本函数跑在
    **shim 进程**里 —— 读自己的 `config.APPROVE_EACH_ROLE` **必然误报**
    （两个进程的 env 是分开的）。故它取自 dev server 状态文件；**不知道就说
    `None`**，不猜。

    字段：
      · `pending`        —— 当前有没有挂起（有 = 链路在等人）
      · `stamp`          —— 本次挂起的标识；前端 POST 决定时**原样带回**，防串步
      · `next_role`      —— 下一步要派谁（"点继续就会跑它"）
      · `prev_role`      —— **用户刚看到的那个产物**属于谁（= 打回的默认目标）
      · `redo_targets`   —— 允许打回的目标（= 已在盘的 `director` 规格文档 + 已完成角色，
        顺序为依赖序，供前端下拉）。**取自挂起时落盘的那一份**，不在这里重算。
      · `decision`       —— 磁盘上**当前有效**的决定（有 = 已拍板、等链路消费）
      · `stale_decision` —— 磁盘上有个**已失效**的决定（戳不匹配）。非空时要**告诉用户**
        "你的批准不生效、请重新确认"，否则他会一直干等
      · `manual_steps`   —— 端口上那个 dev server 是否开着逐步确认（`None` = 不知道）
    """
    from . import hitl, webchain
    pd = hitl.read_pending(root) or {}
    stamp = str(pd.get("stamp") or "")
    dc = hitl.read_decision(root, stamp=stamp) or {}
    done = [str(x) for x in (pd.get("done_roles") or [])]
    stale = ""
    if pd and not dc:
        s = hitl.read_decision(root)          # 有文件、只是戳不对 ⇒ 已失效
        if s:
            stale = str(s.get("decision") or "")
    return {
        "pending": bool(pd),
        "stamp": stamp,
        "thread_id": str(pd.get("thread_id") or ""),
        "run_id": str(pd.get("run_id") or ""),
        "next_role": str(pd.get("next_role") or ""),
        "prev_role": str(pd.get("prev_role") or ""),
        "done_roles": done,
        # ★ 2026-09-19：**读挂起时落盘的那一份**（含 director），不再自己重算。
        #   旧 pending 文件没有该字段 ⇒ 回落 `done`（与改造前一致）。
        #   判据只有一份：`hitl.redo_targets_of`。
        "redo_targets": [str(x) for x in (pd.get("redo_targets") or done)],
        "at": str(pd.get("at") or ""),
        "decision": str(dc.get("decision") or ""),
        "stale_decision": stale,
        "manual_steps": webchain.manual_steps_state(),
    }


def progress(root: Path, ep: int = 1) -> dict:
    """`GET /projects/{pid}/progress?include_content=true`。"""
    pid = root.name
    b = brief(root)
    n_ep = _episode_count(root)
    pack = str(b.get("pack") or "shortdrama")
    entry = _style_entry(pack)
    m = manifest(root)
    phases = m.get("phases") or {}
    # ★ M2：`phases` 升维后（`{ep: {role: state}}`），前端/排障真正要看的是
    #   **本集**的名册 ⇒ 额外给一份扁平的本集视图（`phases_ep`），
    #   而 `phases` 原样保留（二维，排障用）。
    from . import guards as _g
    phases_ep = {r: _g.phase_of(m, r, ep) for r in _g.ROLES}
    is_demo = pid in featured_pids()
    return {
        # ── local 形状（视图在读）──
        "id": pid,
        "name": str(b.get("topic") or pid),
        "ratio": str(b.get("ratio") or config.ASPECT_RATIO),
        "style": {"style_id": pack, "code": pack, "name": entry["name"], "cover_url": ""},
        "source_type": "paste_text",
        # ★ 与 `_project_row` **同键同义**（`test_row_shape_matches_progress` 锁这条不变式）。
        #   单项目详情其实用不到"是不是精选"，但**形状必须对称** ——
        #   消费方不该记住"哪个端点给哪个键"，那种记忆一定会漏。
        "project_type": "demo" if is_demo else "user",
        "is_demo": is_demo,
        "status": "completed" if _review_passed(root) else "draft",
        "asset_locked": False,
        "assets_finalized": bool(registry(root).get("assets") or []),
        "auto_sync_assets": True,
        "readonly": False,
        "planned_episode_count": n_ep,
        "cover": _cover(root, ep),
        "created_at": _mtime_str(root),
        "outline": _outline(root, b),
        "episodes": [_episode_row(root, n, b) for n in range(1, n_ep + 1)],
        "assets": asset_refs(root),
        "flow": flow(root, ep),
        # ── 线上形状（超集）──
        "project_id": pid,
        "projectName": str(b.get("topic") or pid),
        "assets_pending_publish_count": 0,
        "latest_task": None,
        "active_tasks": [],
        # ── v5 侧真实进展（前端还没有对应位置，但排障直接用）──
        "v5": {
            "pack": pack,
            "phases": phases,
            "phases_ep": phases_ep,
            "review": m.get("review") or {},
            "media_loop": _g.media_loop_of(m, ep),
            "render": _render_state(root, ep),
            #: 数据质量警告（**只报告、不过滤**）：如源照片伪资产、角色缺身份锚点
            "warnings": _registry_warnings(root),
            #: 步级「逐步人工确认」状态（2026-09-18）：链路是否正停在某一步等人。
            #: 与 `GET /projects/{pid}/hitl` **同一个函数**（判据不写两份）。
            "hitl": hitl_state(root),
            #: ★ 2026-09-19：**最近一次分镜契约门的判决**（`media/ep{N}/gates.json`）。
            #: 人工模式下门只报不拦，这份报告就是它**唯一的可见落点**
            #: —— 没这一条，"降级为警告"与"删掉门"在界面上看不出区别。
            #: 取不到 → `{}`（**绝不假报"通过"**）。
            "gates": _g.gate_report(root, ep),
        },
    }


# ─────────────────────────────────────────────────────────── /asset-refs

def asset_refs(root: Path) -> dict:
    """`GET /projects/{pid}/asset-refs`。

    映射规则（**有损，必须显式**）：
      · v5 每个资产只有**一份身份**（`identity` + 参考图）；
        Pavo 有「形象/states」多层（如 `纸扎铺` + `纸扎铺夜间`）。
      · 故这里把 v5 资产映射成**单个 base state**（`基础形象`）。
        **不会**凭空造出第二层形象 —— 前端若要「添加形象」，那是写操作，
        需在 P3 决定它在 v5 侧落到哪里（很可能是另一条 asset 或 brief 字段）。
    """
    pid = root.name
    reg = registry(root)
    out = {"characters": [], "scenes": [], "props": []}
    buckets = {"character": out["characters"], "scene": out["scenes"], "prop": out["props"]}
    ids = {"character": "character_id", "scene": "scene_id", "prop": "prop_id"}

    items = sorted(reg.get("assets") or [],
                   key=lambda a: -int(a.get("priority") or 8))
    for a in items:
        kind = _kind_of(a.get("type"))
        name = str(a.get("name") or "")
        if not name:
            continue                       # 无名条目不该出现在契约里
        aid = str(a.get("id") or name)
        imgs = _asset_images(root, a)
        st = {
            "asset_type": kind,
            "ref_id": aid,
            "display_name": "%s - %s" % (name, BASE_STATE),
            "token": token(kind, name, aid),
            "thumbnail_url": imgs[0] if imgs else "",
            "state_name": BASE_STATE,
            "is_default": True,
            # 便利字段
            "images": imgs,
            # ★★ **`image` 这个别名是必须的**（2026-09-17 实测事故）：
            #   前端（`wizard.js` 卡片缩略图、`asset-drawer.js` 主图、`store.js` 的
            #   引用 token 共 **9 处**）读的都是 `state.image`，而这里原先只给了
            #   `thumbnail_url` / `images` ⇒ **即使磁盘上真有图，前端也一律显示「未生成」**。
            #   实测：`lost-and-found` 的 `thumbnail_url` 是
            #   `/media/lost-and-found/images/周平.png`（图**确实存在**），
            #   但 `image` 键不存在 → 前端 18 个有图的项目全都显示「未生成」。
            #   这正是本项目那条铁律：**契约要同时规定「结构」与「值」** ——
            #   只给"新形状"而不给前端在读的"旧字段"，是**静默**失配。
            "image": imgs[0] if imgs else "",
            "identity": str(a.get("identity") or a.get("ref_line") or "").strip(),
            "ref_ver": a.get("ref_ver"),
        }
        row = {
            # local 形状（视图在读）
            "id": aid,
            "name": name,
            "sort_order": len(buckets[kind]) + 1,
            "states": [st],
            # 线上形状（超集）：角色 character_id / 场景 scene_id / 道具 prop_id
            ids[kind]: aid,
            "title": name,
            # 便利字段
            "type": kind,
            "keywords": list(a.get("keywords") or []),
            "priority": a.get("priority"),
        }
        buckets[kind].append(row)
    return out


def _asset_images(root: Path, a: dict) -> list:
    """资产的参考图 → shim 静态 URL 列表（可多张：`ref_images` 优先，回落 `ref_image`）。"""
    refs = a.get("ref_images") or []
    if not isinstance(refs, list):
        refs = []
    single = str(a.get("ref_image") or "").strip()
    names = ([single] if single else []) + [str(r) for r in refs if r and str(r) != single]
    out = []
    for n in names:
        n = n.strip().lstrip("/")
        if not n:
            continue
        # 注册表里的 ref_image 是 `images/` 下的文件名；带目录的按原样拼
        rel = n if "/" in n else ("images/" + n)
        out.append(media_url(root.name, rel))
    return out


# ─────────────────────────────────────────────────────────── /episodes/storyboard

def episodes_storyboard(root: Path) -> list:
    """`GET /projects/{pid}/episodes/storyboard`（分集列表 + 每集的轻量分镜统计）。"""
    b = brief(root)
    out = []
    for n in range(1, _episode_count(root) + 1):
        row = _episode_row(root, n, b)
        sh = shots(root, n)
        st = stills_map(root, n)
        jb = jobs_map(root, n)
        row.update({
            "storyboard": {
                "episode_id": row["id"],
                "episode_no": n,
                "title": row["title"],
                # local（视图读 `storyboard.phase`）/ 线上（`storyboard_phase`）都给
                "phase": PHASE_DONE if sh else PHASE_DRAFT,
                "storyboard_phase": PHASE_DONE if sh else PHASE_DRAFT,
                "ratio": str(b.get("ratio") or config.ASPECT_RATIO),
                # 轻量列表**不回传 segments**（前端只为判断"有没有分镜"）——
                # 完整分镜走 `GET /episodes/{eid}/storyboard/detail`，避免列表接口变重
                "segments": [],
            },
            "stats": {
                "segments": len(sh),
                "shots": len(sh),
                "duration_ms": int(sum(int(s.get("seconds") or 0) for s in sh) * 1000),
                "keyframes": sum(1 for v in st.values() if (v or {}).get("url")),
                "videos": sum(1 for v in jb.values()
                              if (v or {}).get("state") == "completed"),
            },
        })
        out.append(row)
    return out


# ─────────────────────────────────────────────────────────── /storyboard/detail

def _prompt_ctx(root: Path, ep: int = 1):
    """装配"本镜提示词"的上下文。

    ⛔ **这是 `pipeline._run_impl` 注入链的镜像**（`v5/media/pipeline.py:536-660`）。
    必须**逐字照抄顺序**，本项目有一条铁律：

        写脚本拼提示词**必须复用生产注入链**
        （`storyboard.parse` → `resolve_styles` → `_names` → `_style_block`
          → `_scene_line` → `join_segs`），手拼已翻车两次。

    步骤（与 pipeline 完全一致）：
      1. 角色名表 ← `worldbuilder.md`（`prompt._has_person` 靠它判"本镜有真人"）
      2. 风格块 ← `style.wrap(style.load(root))`
      3. 参考图绑定 / 文本身份锚点（由 pack 的 `still-refs` 分流）
      4. 场景锚点 ← `assets.scene_lines`
      5. 出场人数 ← `assets.cast_counts`
      6. 镜间关系 ← `relations.plan_frames`
      7. `prompt.build_video_prompt(shot, plan, mode=...)`

    ⚠️ **已知风险（记在 spec §15-Q8）**：镜像链一旦与 pipeline 漂移就会产出
    与实际渲染**不一致**的提示词（前端展示 A、后端渲 B）。正解是把它从 pipeline
    抽成一个公共函数让两边都调 —— 那要动 `pipeline.py`，**留待 P2 做**（并配一致性测试）。
    在那之前，这里**只读**、不改任何状态，最坏后果是"展示的提示词不准"，不影响出片。
    """
    sh = shots(root, ep)
    if not sh:
        return [], {}

    # 1) 角色名表
    char_names: list = []
    try:
        wb = root / "worldbuilder" / "worldbuilder.md"
        if wb.exists():
            char_names = [c["name"] for c in cast.parse_characters(_read_text(wb))]
    except Exception:           # noqa: BLE001
        char_names = []
    if char_names:
        sh = [{**s, "_names": char_names} for s in sh]

    # 2) 风格块
    try:
        block = style.wrap(style.load(root))
    except Exception:           # noqa: BLE001
        block = ""
    if block:
        sh = [{**s, "_style_block": block} for s in sh]

    # 3) 参考图 / 文本身份锚点
    try:
        if style.still_refs_enabled(root):
            pass                # 参考图只影响生图，不进提示词文本
        else:
            idl = assets.identity_lines(root, sh)
            if idl:
                sh = [{**s, "_identity_line": idl.get(s["name"], "")} for s in sh]
    except Exception:           # noqa: BLE001
        pass

    # 4) 场景锚点
    try:
        sl = assets.scene_lines(root, sh)
        if sl:
            sh = [{**s, "_scene_line": sl.get(s["name"], "")} for s in sh]
    except Exception:           # noqa: BLE001
        pass

    # 5) 出场人数
    try:
        cn = assets.cast_counts(root, sh)
        if cn:
            sh = [{**s, "_cast_n": cn.get(s["name"], 0)} for s in sh]
    except Exception:           # noqa: BLE001
        pass

    # 6) 镜间关系
    try:
        planned = relations.plan_frames(sh)
    except Exception:           # noqa: BLE001
        planned = []

    # 7) 模式（唯一决策点）
    try:
        mode = video_plan.VideoPlan.of().mode
    except Exception:           # noqa: BLE001
        mode = config.VIDEO_MODE
    return sh, {"planned": planned, "mode": mode, "ratio": str(brief(root).get("ratio")
                                                              or config.ASPECT_RATIO)}


def storyboard_detail(root: Path, ep: int = 1) -> dict:
    """`GET /episodes/{eid}/storyboard/detail`。

    ★ `video_prompt_text` **必须给全文**（本项目铁律：中间产物要打印全文给人看，
    只报字数会漏掉字面量「同上」这类 bug）。前端也是整段展示的。
    """
    from . import guards              # 局部导入（本模块顶层不引 guards，见 _still_relpath 同一惯例）
    pid = root.name
    b = brief(root)
    sh, ctx = _prompt_ctx(root, ep)
    planned = ctx.get("planned") or []
    mode = ctx.get("mode")
    plan_by_name = {str(p.get("name")): p for p in planned}

    st = stills_map(root, ep)
    jb = jobs_map(root, ep)
    clip_dir = media_dir(root, ep) / "clips"
    # 注册表只读一次（下面每镜都要用；循环里读会让 18 镜变成 18 次 JSON 解析）
    reg = registry(root)
    index = name_index(root)

    segments = []
    for i, s in enumerate(sh):
        name = str(s.get("name") or "")
        plan = plan_by_name.get(name) or {}
        vp = ""
        try:
            vp = prompt.build_video_prompt(s, plan, mode=mode) if mode else ""
        except Exception as e:  # noqa: BLE001
            vp = ""
            _ = e               # 提示词装配失败不该让整个端点挂掉
        info = st.get(name) or {}
        j = jb.get(name) or {}
        rel = _still_relpath(root, info)
        clip = clip_dir / (name + ".mp4")
        # ★ 视频的相对路径**必须也走同一个真相源**（`media_dir(root, ep)`）。
        #   ⛔ 原先这里手拼 `"media/clips/" + clip.name` —— **少了 `ep{N}`**，
        #   于是前端 `<video>` 请求 `/media/<pid>/media/clips/LN01.mp4` → **404**，
        #   而文件其实好好地躺在 `media/ep1/clips/LN01.mp4`。
        #   症状极隐蔽：静帧（走 `_still_relpath`）全对，只有视频全 404
        #   （2026-09-17 同源冒烟测试抓到：3 条 `/media/**/media/clips/*.mp4` 404）。
        clip_rel = (clip.relative_to(root).as_posix() if clip.exists() else "")
        visual = str(s.get("visual") or "")
        scene_title = str(s.get("scene") or "").lstrip("@") or "未标注场景"

        # token 化的富文本（Pavo 的 content_rich 字符串 + 前端要的 token 数组）
        rich, tokens, unresolved = _to_rich(visual, reg, index)
        plain = rich_to_plain(rich)
        # ⛔ **镜序必须用位置，不能用 `s["index"]`**（2026-09-15 真实数据实测）：
        #   `storyboard.parse` 的 `index` 是**镜头号列的第一个整数** —— 分镜写
        #   `| 1-1 | 1-2 | 2-1 |` 时它是**幕号**，于是 order 变成
        #   `[1,1,1,2,2,3,…]`（实测 village-bees 18 镜全是这样，左侧导轨编号重复）。
        #   真正的全局镜序由 `parse` 生成的 `name`（`LN01…LN18`）承载，即**位置**。
        order = i + 1
        secs = int(s.get("seconds") or 0)
        sid = "%s-ep%d-%s" % (pid, ep, name)
        # 本镜场景 → 注册表里的场景资产 id（拿不到就空串，**不猜**）
        scene_aid = str((index.get(scene_title) or {}).get("id") or "")
        scene_kind = str((index.get(scene_title) or {}).get("kind") or "scene")
        # ★ Pavo 的结构是 **三层**：`segment → scenes → shots`。
        #   我第一版只在 segment 上放了 `shots`，漏了中间 `scenes` →
        #   前端 `storyboard.js:114` 的 `seg.scenes.reduce` 直接 TypeError、**整页崩**
        #   （2026-09-15 真实浏览器实测）。一个镜次 = 一个 scene = 一个 shot。
        shot_obj = {
            "shot_id": sid + "-1",
            "display_order": 1,
            "title": _seg_title(s),
            # 视图模板直接读 `sh.duration_sec`（`storyboard.js:139`）
            "duration_sec": secs,
            "duration_ms": secs * 1000,
            # ★ `rich` 必须是 **token 数组**：`D.richHtml()` 对字符串会走 `.map` → TypeError。
            #   而 `D.parseRich()` 也用不了（它的 id 段要求纯数字，v5 的 id 是资产名）。
            "rich": tokens,
            "content_rich": rich,
            "content_plain": plain,
            "content": plain,
        }
        scene_obj = {
            "scene_id": sid + "-sc1",
            "display_order": 1,
            "title": scene_title,
            "linked_scene_asset_id": scene_aid,
            "content_rich": rich,
            "content_plain": plain,
            # 线上成对给 `asset_refs`（关系表）；本地形状视图暂未读，给了便于后续接
            "asset_refs": ([{"relation_type": scene_kind, "scene_asset_id": scene_aid,
                             "name": scene_title, "image_url": "", "sort_order": 0}]
                           if scene_aid else []),
            "shots": [shot_obj],
        }
        seg = {
            # ── local 形状（视图在读）──
            "id": sid,
            "order": order,
            "title": _seg_title(s),
            "summary": _first_sentence(visual),
            "video_prompt": vp,
            "duration_ms": secs * 1000,
            "keyframe": media_url(pid, rel) if rel else "",
            "video": media_url(pid, clip_rel) if clip_rel else "",
            "status": _seg_status(j),
            "visual_style": str(s.get("visual_style") or ""),
            "scene": scene_title,
            "scenes": [scene_obj],
            # segment 级 `shots` 保留为**同一份**列表的便利别名
            # （线上把 shots 放在 scene 下；两个都给，消费方读哪套都行）
            "shots": [shot_obj],
            # ── 线上形状（超集）──
            "segment_id": sid,
            "display_order": order,
            "visual_style_text": str(s.get("visual_style") or ""),
            "video_prompt_text": vp,
            "estimated_duration_ms": secs * 1000,
            "selected_keyframe_url": media_url(pid, rel) if rel else "",
            "selected_video_url": media_url(pid, clip_rel) if clip_rel else "",
            "selected_keyframe_id": name if rel else "",
            "selected_video_id": name if clip.exists() else "",
            # ── v5 侧真实字段（前端还没有位置，排障/后续功能直接用）──
            "v5": {
                "shot_name": name,
                # 镜头号列的整数部分。⚠️ 分镜写 `1-3` 时它是**幕号**、不是镜序 ——
                # 仅供排障参考，**别拿它当排序依据**（镜序用 `order`/`shot_name`）。
                "col_index": s.get("index"),
                "shot_type": str(s.get("shot_type") or ""),
                "angle": str(s.get("angle") or ""),
                "camera": str(s.get("camera") or ""),
                "tail": str(s.get("tail") or ""),
                "dialogue": str(s.get("dialogue") or ""),
                "sfx": str(s.get("sfx") or ""),
                "text_shot": str(s.get("text_shot") or ""),
                "join_note": str(s.get("join_note") or ""),
                "relation": plan.get("relation"),
                "heading": str(s.get("heading") or ""),
                "still_prompt": str(info.get("prompt") or ""),
                "job_state": j.get("state"),
                "job_error": str(j.get("error") or ""),
                "unresolved_tokens": unresolved,
            },
        }
        segments.append(seg)

    return {
        "episode_id": episode_id(pid, ep),
        "episode_no": ep,
        "title": _episode_title(root, ep, b),
        # local（视图读 storyboard.phase）/ 线上（storyboard_phase）都给
        "phase": PHASE_DONE if segments else PHASE_DRAFT,
        "storyboard_phase": PHASE_DONE if segments else PHASE_DRAFT,
        "ratio": ctx.get("ratio") or config.ASPECT_RATIO,
        "segments": segments,
        # ★ C（2026-09-19）：**最近一次分镜契约门的判决**，随分镜一起给。
        #   为什么搭在这个端点上：人工模式下门只报不拦，那条"红字"必须能**跟着
        #   分镜一起出现在页面上**（否则"降级为警告"等于"删掉门"）。
        #   取不到 → `{}`（前端据此**什么都不显示**，绝不假报"通过"）。
        "gates": guards.gate_report(root, ep),
    }


def _seg_status(j: dict) -> str:
    """任务状态 → 前端三色显示态（映射表见 spec §7.4）。"""
    s = str((j or {}).get("state") or "").strip().lower()
    if s == "completed":
        return "completed"
    if s == "submitted":
        return "generating"
    if s in ("failed", "expired"):
        return "failed"
    return "pending"


def _seg_title(s: dict) -> str:
    """分镜标题：「景别·场景」合成；都缺时用画面描述首句。"""
    parts = [str(s.get("shot_type") or "").strip(),
             str(s.get("scene") or "").lstrip("@").strip()]
    t = "·".join(p for p in parts if p)
    return t or _first_sentence(str(s.get("visual") or ""))[:20]


def _first_sentence(text: str) -> str:
    for sep in ("。", "；", "，", "\n"):
        i = str(text or "").find(sep)
        if i > 0:
            return text[:i].strip()
    return str(text or "").strip()


def name_index(root: Path) -> dict:
    """`{资产名: {"kind": character|scene|prop, "id": <ref_id>}}`。

    供 token 双向翻译用。**必须来自注册表**（不猜、不占位）——
    拼一个 kind/id 是错的 token 比没有 token 更糟：前端会拿它去回写分镜，
    而 v5 侧匹配不上 → 参考图绑定静默失败（`resolve_mentions` 的教训）。
    """
    idx = {}
    for a in (registry(root).get("assets") or []):
        nm = str(a.get("name") or "")
        if nm:
            idx[nm] = {"kind": _kind_of(a.get("type")),
                       "id": str(a.get("id") or nm)}
    return idx


def _to_rich(text: str, reg: dict, index: dict) -> tuple:
    """把 `@资产名` 升级成 Pavo 富文本。返回 `(rich_str, tokens, unresolved)`。

    ## 为什么同时给「字符串」和「token 数组」

    前端 `views/storyboard.js` 读的是 **`sh.rich` 数组**（交给 `D.richHtml()` 渲染成
    `@chip`）；而 `content_rich` 在线上契约里是**字符串**。
    两者都要，且**都由服务端产出**：

    ⛔ 不能让前端用 `D.parseRich()` 去解析——它的正则是
    `sd-asset:\\/\\/([a-zA-Z]+)\\/(\\d+)`，**id 段必须是纯数字**，
    而 v5 的资产 id 就是资产名（`sd-asset://character/老周`）→ **解析不出任何 ref**，
    还会让 `rich` 变成 `[{type:'text', value: 整串}]`（chip 全丢）。
    ⇒ 也**不能**把字符串直接塞给 `richHtml`：`richHtml` 对字符串会走 `.map` → **TypeError**
    （2026-09-15 真实浏览器实测：分镜页整页崩在 `seg.scenes.reduce`，其后就是这一处）。

    ## 必须注册表驱动、长名优先

    不能拿一个 `@([^\\s…]+)` 的字符类去抽（`assets._AT` 那种）——它的字符类**不含 `@`**，
    两个相邻引用会被吞成一个。实测原文：
        `@阿凯站在@老周家院的青砖院中央`
    → 抽成 `阿凯站在@老周家院的青砖院中央` 一整条，谁也不匹配、token 升级**整个失效**。
    而 `assets.at_mentions` 的 docstring 早就写了：「真正做匹配必须用
    `resolve_mentions`（**注册表驱动、长名优先**）」。

    这里用「长名优先的交替式正则」实现同一语义（Python 的交替按书写顺序取首个命中，
    故把名字按长度降序排列即可保证 `@老周家院` 先于 `@老周` 命中）。
    未解析项**复用 `assets.resolve_mentions()`** —— 那是本项目「有引用无资产必须出声」
    的**唯一判据来源**，自己再写一份必然漂移。
    """
    raw = str(text or "")
    if not raw:
        return "", [], []
    names = sorted([n for n in (index or {}) if n], key=len, reverse=True)
    if not names:
        return raw, [{"type": "text", "value": raw}], []

    pat = re.compile("@(" + "|".join(re.escape(n) for n in names) + ")")
    parts: list = []          # rich 字符串片段
    tokens: list = []         # 前端要的 token 数组
    pos = 0
    for m in pat.finditer(raw):
        if m.start() > pos:
            seg = raw[pos:m.start()]
            parts.append(seg)
            tokens.append({"type": "text", "value": seg})
        nm = m.group(1)
        e = index[nm]
        parts.append("@[%s - %s](sd-asset://%s/%s)" % (nm, BASE_STATE, e["kind"], e["id"]))
        tokens.append({"type": "ref", "kind": e["kind"], "id": str(e["id"]), "name": nm})
        pos = m.end()
    if pos < len(raw):
        tail = raw[pos:]
        parts.append(tail)
        tokens.append({"type": "text", "value": tail})

    try:
        _matched, leftover = assets.resolve_mentions(raw, reg)
    except Exception:           # noqa: BLE001 —— 上报失败不该让端点挂掉
        leftover = []
    return "".join(parts), tokens, list(leftover)


# ─────────────────────────────────────────────────────────── /styles

def _packs_dir() -> Path:
    return config.SKILLS_DIR / "packs"


#: 展示名回落表。**只在 `pack.json` 缺 `display-name-zh` 时用**。
#:
#: 为什么需要（2026-09-15 实测）：`shortdrama` 是**回落基准包**，它**本来就没有
#: `pack.json`**（见 `AGENTS.md` 的类型包表：「缺 `pack.json`」即回落到它）→
#: `_style_entry` 只能拿包名当显示名 → 前端风格下拉里出现一行英文 `shortdrama`。
#: ⚠️ 这里**不是真相源**，只是展示名；包一旦补了 `pack.json`，以 pack.json 为准。
_PACK_DISPLAY = {"shortdrama": "写实电影感"}


def _style_entry(pack: str) -> dict:
    """pack 名 → 前端风格条目。**优先读 pack.json**，不硬编码能力字段。"""
    name = _PACK_DISPLAY.get(pack, pack)
    info: dict = {}
    try:
        p = _packs_dir() / pack / "pack.json"
        if p.exists():
            info = json.loads(p.read_text(encoding="utf-8")) or {}
            name = str(info.get("display-name-zh") or info.get("name") or name)
    except Exception:           # noqa: BLE001
        pass
    return {"code": pack, "name": name, "group": "短剧引擎", "pack": info}


def _injectable_text(pack_dir: Path) -> str:
    """从 `style-block.md` 抽出**真正会被注入**的那段文案（风格预览用）。

    ⛔ 别拿整篇当预览：`style-block.md` 里 `#` 开头的是**给维护者看的注释**
    （"长度纪律""★ 光源不要在这里硬编码"…），不注入提示词。
    取**第一段非注释、非空行**。
    """
    f = pack_dir / "style-block.md"
    if not f.exists():
        return ""
    para: list = []
    for raw in f.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s.startswith("#"):
            if para:
                break
            continue
        if not s:
            if para:
                break
            continue
        para.append(s)
    return " ".join(para).strip()


def _pack_visual_style(pack_dir: Path, entry: dict) -> str:
    """包的一句风格说明：优先 `pack.json` 的 `visual-style`，否则从 style-block 抽。

    ⚠️ `shortdrama` **没有 pack.json**（只有 style-block.md）→ 不抽就是**空的**，
    而它恰好是默认包 / 回落基准包（实测：前端预览里它一片空白）。
    """
    v = str((entry.get("pack") or {}).get("visual-style") or "").strip()
    return v or _injectable_text(pack_dir)


def _pack_sample(pack: str) -> dict:
    """为某个类型包找一张**真实样张**（前端风格库的缩略图）。

    选择是**确定性**的（同一份磁盘状态每次给同一张，界面才不会闪）：
      ① 该包的项目里**静帧最多**的 → ② 同数量按项目名 → ③ 该项目静帧名序第一张。

    ⛔ 没有样张就返回 `{}`，**不编** —— 前端要如实显示「暂无样张」
    （实测：`3d-animation` 一个项目都没有）。
    """
    root = config.PROJECTS_DIR
    if not root.exists():
        return {}
    best = None                              # (key, pid, first_still, count, topic)
    for p in sorted(root.iterdir()):
        if not p.is_dir() or p.name.startswith((".", "_")):
            continue
        b, md = p / "brief.json", p / "media"
        if not b.exists() or not md.exists():
            continue
        try:
            bj = json.loads(b.read_text(encoding="utf-8"))
        except Exception:                    # noqa: BLE001 —— brief 坏掉就当不属于该包
            continue
        if str(bj.get("pack") or "") != pack:
            continue
        stills = sorted(f for f in md.glob("ep*/stills/*.jpg") if f.is_file())
        if not stills:
            continue
        key = (-len(stills), p.name)
        if best is None or key < best[0]:
            best = (key, p.name, stills[0], len(stills), str(bj.get("topic") or ""))
    if best is None:
        return {}
    _k, pid, f, n, topic = best
    return {
        "cover_url": media_url(pid, f.relative_to(root / pid).as_posix()),
        "sample_project": pid,
        # 中文片名（给人看的；pid 是目录名，写「样张来自《paper-crane》」不如《纸鹤》）
        "sample_topic": topic or pid,
        "sample_shot": f.stem,
        "sample_stills": n,
    }


def styles() -> list:
    """`GET /styles` —— v5 的类型包（**只有 3 个**）。

    ⛔ **不要假装能映射 Pavo 的 20+ 风格**（spec §15-Q3 待你拍板）：
      Pavo 的 `STYLE_CATALOG` 是按 2D/3D/真人/自定义 分组的**中文风格名**，
      而 v5 只有 3 个**类型包**。`realpeople_cinematic_style` 之类**不对应任何 v5 包**，
      硬编一张映射表只会制造"选了 A 实际跑 B"的静默偏差。
    这里**只发 v5 真实拥有的**，并附上每条的能力说明，让前端如实展示。

    `brief.pack` 是唯一真相源（`pack-conventions.md`）—— `brief.pack` 写错 = 静默退回朴素提示词。
    """
    out = []
    d = _packs_dir()
    if not d.exists():
        return out
    for p in sorted(d.iterdir()):
        if not p.is_dir() or p.name == "craft":      # craft/ 是技法库、不是类型包
            continue
        e = _style_entry(p.name)
        info = e.get("pack") or {}
        out.append({
            "code": p.name,
            "name": e["name"],
            "group": e["group"],
            # 便利字段：让前端能如实展示每个包的能力/限制
            "audio_modes": list(info.get("audio-modes") or []),
            "still_refs": info.get("still-refs"),
            # 一句风格说明（前端预览要显示它）。`shortdrama` 无 pack.json → 从 style-block 抽
            "visual_style": _pack_visual_style(p, e)[:220],
            "has_style_block": (p / "style-block.md").exists(),
        })
        # ★ 真实样张（前端缩略图）—— 没样张就空着，前端如实显示「暂无样张」
        out[-1].update(_pack_sample(p.name))
    # ★ **把 `shortdrama` 排到第一位**：它是 v5 的**回落基准包**（缺 pack.json 时就是它），
    #   也是默认。前端下拉通常默认选第一项 —— 按字母序会默认选中 `3d-animation`，
    #   于是"进页面直接点开始创作"就会跑 3D 包（实测踩到：AI 创作出来的项目是 3D 风格）。
    out.sort(key=lambda s: (0 if s["code"] == "shortdrama" else 1, s["code"]))
    return out


def vendors() -> dict:
    """`GET /vendors` —— 可选**厂商档**清单 + 当前缺省（2026-09-18）。

    为什么必须有这个接口：前端要在**每个生成页面**上让用户选厂商
    （分镜页的批量生图 / 生视频、资产页的生成参考图）。而"有哪些厂商"由**后端**
    决定（环境变量 + `v5.media.vendors.register()`）⇒ 前端**不能自己写死一份列表**。
    写死必然走样 —— 与 `styles()` 是同一条教训（v5 当天加第 4 个包时，
    后端硬编的列表就把它静默拒了）。

    返回形状（`envelope` 会再包一层）：
        {"current": {"image": "agnes", "video": "agnes"},
         "current_ok": {"image": true, "video": true},
         "items": [{"code": "agnes", "name": "...", "builtin": true,
                    "native_audio": true, "output_delivery": "url"}, ...]}

    · `builtin=True` ⇒ 走 `providers` 内置实现（档里 `impl` 为 None）
    · `current_ok=False` ⇒ 环境变量指向了一个**未注册**的厂商名
      ⇒ 前端要**如实显示**，不要假装它可用（那是"失败不可见"）
    """
    items = []
    for code in vendors_mod.names():
        spec = vendors_mod.get(code)
        label = spec.get("label") or code
        # 下拉里要短名：中文标签常写成「Agnes（默认，内置实现）」，
        # 整串塞进胶囊控件会过宽 ⇒ 取「（」之前的部分，取不到就用 code。
        short = label.split("（")[0].split("(")[0].strip() or code
        items.append({
            "code": code,
            "name": label,
            "short": short,
            "builtin": spec.get("impl") is None,
            "output_delivery": spec.get("output_delivery") or "url",
            "native_audio": bool(spec.get("native_audio")),
        })
    cur = {k: vendors_mod.current(k) for k in ("image", "video")}
    registered = set(vendors_mod.names())
    return {
        "current": cur,
        "current_ok": {k: (v in registered) for k, v in cur.items()},
        "items": items,
    }


# ─────────────────────────────────────────────────────────── 自检

def health() -> dict:
    return {
        "ok": True,
        "projects_dir": str(config.PROJECTS_DIR),
        "projects": len(list_pids()),
        "packs": [s["code"] for s in styles()],
        "video_mode": config.VIDEO_MODE,
        "aspect_ratio": config.ASPECT_RATIO,
        # 前端画幅选择器的候选（单一来源在后端，见 `config.RATIO_CHOICES`）；
        # `ratio` 是项目当前值（缺省 = 全局缺省），用于回显选中项。
        "ratio_choices": list(config.RATIO_CHOICES),
        "ratio_default": config.ASPECT_RATIO,
    }
