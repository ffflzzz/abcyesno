# -*- coding: utf-8 -*-
"""Web 侧的控制面写入：**确定性编辑**（不调 LLM、不起图、不烧配额）。

与 `runner.py` 的分工：
  · `runner`   —— 长任务（生图/生视频），spawn 子进程 + 台账
  · `webwrite` —— 即时写入（改剧本/改分镜/改资产/改名/同步），同步返回

## 三条纪律

1. **不新增第二判据**：分镜行由 `storyboard.parse()` 的 `line` 字段定位
   （那是"哪些行算镜头"的唯一判据）；资产走 `assets.auto_sync` / 注册表原语。
2. **失效必须显式且可见**：改一镜会**作废该镜的静帧与成片**（v5 没有细粒度失效），
   返回里必须写明作废了什么 —— 绝不能"改了但看着没变"。
3. **绝不碰黑名单**：不写 `.agent_state.json`、不动 `video_quota.json`。
   状态变更一律走 v5 自己的原语（`jobs.mark` / `clipqc.invalidate`）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config
from .media import assets, clipqc, jobs as jobs_mod, storyboard

#: 新增镜头时的占位画面描述。
#:
#: ⛔ **必须 ≥15 字**：`storyboard.parse` 会把画面描述短于 15 字的数据行**当作占位跳过**
#:   （见其 `if len(visual) < 15: continue`）。所以 `[[待补]]`（6 字）这种占位
#:   **插进去等于没插** —— 分镜镜数不变、前端那个新镜根本不出现（实测被测试抓到）。
#:   这里写成自解释的长占位：既能被解析出来（新人能看见它），又明确标着「待补」。
PLACEHOLDER_VISUAL = "[[待补]] 本镜画面描述：请填写不少于 15 字的具体内容后再生成"

#: 可在前端编辑的分镜列 → 表头关键词（与 `storyboard.parse` 的识列口径**一致**）
EDITABLE_COLS = {
    "visual": ("画面", "visual"),
    "dialogue": ("对白", "dialogue"),
    "seconds": ("时长", "秒"),
    "shot_type": ("景别",),
    "angle": ("角度",),
    "camera": ("运镜",),
    "scene": ("场景", "scene"),
    "visual_style": ("视觉风格", "风格", "style"),
    "tail": ("落幅", "收尾", "tail"),
    "sfx": ("音效", "sfx"),
}


class EditError(ValueError):
    """可预期的编辑错误（调用方转 400，不要把栈暴露给用户）。"""


# ─────────────────────────────────────────────────────────── 项目

def rename_project(root: Path, name: str) -> dict:
    """改项目名 = 改 `brief.topic`（**唯一真相源**；`projects/` 目录名不动）。"""
    name = str(name or "").strip()
    if not name:
        raise EditError("项目名不能为空")
    if len(name) > 60:
        raise EditError("项目名过长（≤60 字）")
    p = root / "brief.json"
    if not p.exists():
        raise EditError("项目缺 brief.json")
    b = json.loads(p.read_text(encoding="utf-8"))
    old = b.get("topic")
    b["topic"] = name
    p.write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"topic": name, "previous": old}


def update_outline(root: Path, patch: dict) -> dict:
    """改概要 = 改 brief 的对应字段（**不调 LLM**，只接受白名单键）。"""
    allow = {"genre": "genre", "target_duration": "target_duration",
             "tone": "tone", "结局": "结局", "story_summary": "story_summary",
             "ratio": "ratio"}
    p = root / "brief.json"
    if not p.exists():
        raise EditError("项目缺 brief.json")
    b = json.loads(p.read_text(encoding="utf-8"))
    changed = {}
    for k, v in (patch or {}).items():
        key = allow.get(k)
        if not key:
            raise EditError("概要字段不可编辑：%r（可编辑：%s）"
                            % (k, "、".join(sorted(allow))))
        val = str(v or "")
        if key == "ratio" and val not in config.RATIO_CHOICES:
            # 画幅会直接进图像/视频接口参数，写错 = 整批静帧与成片重做，
            # 所以这里**响亮拒绝**，不能像文本字段那样照收。
            raise EditError("画幅只能是：%s（收到 %r）"
                            % ("、".join(config.RATIO_CHOICES), val))
        changed[key] = b.get(key)
        b[key] = val
    p.write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"changed": changed}


# ─────────────────────────────────────────────────────────── 剧本

def script_path(root: Path, ep: int) -> Path:
    return root / "scriptwriter" / ("scriptwriter_ep%d.md" % ep)


def update_script(root: Path, ep: int, content: str) -> dict:
    """写某一集的剧本正文。

    ⚠️ **会作废下游**：分镜是从剧本派生的 → 返回里明确提示"分镜需重新生成"。
    这里**不自动重跑分镜**（那是创作链，烧 LLM 时间）—— 由人决定。
    """
    p = script_path(root, ep)
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    p.write_text(str(content or ""), encoding="utf-8")
    return {"path": str(p.relative_to(root).as_posix()),
            "chars": len(str(content or "")), "previous_chars": len(old),
            "stale": {"storyboard": True},
            "note": "剧本已更新；分镜由剧本派生，需重新生成（本步不自动跑创作链）"}


# ─────────────────────────────────────────────────────────── 资产

def _registry_path(root: Path) -> Path:
    return root / assets.REGISTRY_NAME


def sync_assets(root: Path) -> dict:
    """把 `images/` 里没登记的图收编进注册表（= 前端的「同步资产」）。

    直接复用 `assets.auto_sync` —— 它已经处理了「跳过 `*.source.*`」「只扫根层不递归」
    等实测过的细节，**不重写**。

    返回里**同时给分类计数**（`characters/scenes/props`）：前端 `wizard.js:257` 要
    「已识别 N 角色 / N 场景 / N 道具」这句提示，缺了就会显示 `undefined`。
    """
    before = {a.get("name") for a in (assets.load_registry(root).get("assets") or [])}
    reg = assets.auto_sync(root)
    items = reg.get("assets") or []
    after = {a.get("name") for a in items}

    def _count(kinds) -> int:
        return sum(1 for a in items
                   if str(a.get("type") or "").strip().lower() in kinds)

    return {
        "total": len(after),
        "added": sorted(after - before),
        "added_count": len(after - before),
        # 前端要的分类计数（`kind` 口径与 `_norm_kind` 一致）
        "characters": _count(("character",)),
        "scenes": _count(("location", "scene")),
        "props": _count(("prop",)),
    }


def add_asset(root: Path, kind: str, name: str, identity: str = "",
              keywords=None) -> dict:
    """新增资产条目（**只登记，不出图**；出图走 `runner` 的 assets 任务）。

    `identity` 是**真正会注入每一镜提示词**的身份描述 —— 前端「角色信息」抽屉里
    那个"描述"框就落在这里（2026-09-17）。空着会导致模型每镜自己编长相，
    **同一个人在 18 个镜头里长成 18 个人**，问题要到成片才暴露（见资产契约门）。
    """
    name = str(name or "").strip().lstrip("@")
    if not name:
        raise EditError("资产名不能为空")
    k = _norm_kind(kind)
    reg = assets.load_registry(root)
    items = reg.setdefault("assets", [])
    if any(str(a.get("name")) == name for a in items):
        raise EditError("资产已存在：%s" % name)
    kw = [str(x).strip().lstrip("@") for x in (keywords or []) if str(x).strip()] or [name]
    items.append({"id": name, "name": name, "type": k, "keywords": kw,
                  "priority": 8, "public_url": "", "url": "", "ref_image": "",
                  "identity": str(identity or "").strip()})
    _save_registry(root, reg)
    return {"name": name, "type": k, "total": len(items),
            "identity_chars": len(str(identity or "").strip())}


#: 「角色信息」抽屉里 v5 **能**落地的字段 → 注册表的键。
#: 其余字段**原样进 `ignored`**（绝不假装收下 —— 本项目铁律：静默忽略是最贵的一类 bug）。
_STATE_WRITABLE = {
    "name": "name",              # 重命名
    "description": "identity",   # 唯一会注入提示词的身份描述
    "identity": "identity",
}


def save_asset_state(root: Path, kind: str, aid: str, body: dict) -> dict:
    """保存「角色信息」抽屉（前端 `Api.saveAssetState` 的落点）。

    ⚠️ **v5 的资产模型比 Pavo 薄**，这一层的差距必须**如实报出来**：
      · v5 每个资产只有**一份身份**（`identity` + 参考图）；Pavo 有「形象/states」多层
        → `state_name` 收下但**不产生新形象**（v5 恒为「基础形象」）
      · **没有** per-asset 音色（声音/音色描述）→ 音频口径在 `brief.audio_mode`（项目级）
      · **没有**四视图概念（v5 一张参考图）→ `fourview_image` 忽略
      · **没有** per-asset 画风（风格是**项目级**的 `brief.pack`）→ `style_id` 忽略
      · `model_code` 也忽略（模型由 v5 的配置决定）
    ⇒ 所以返回里带 `applied` / `ignored` 两张清单，前端可以**如实**告诉用户
      "哪些生效了、哪些 v5 不支持"。
    """
    body = body or {}
    reg = assets.load_registry(root)
    items = reg.get("assets") or []
    hit = [a for a in items if str(a.get("id")) == aid or str(a.get("name")) == aid]
    if not hit:
        raise EditError("资产不存在：%s" % aid)
    k = _norm_kind(kind)
    a = hit[0]

    applied, ignored = {}, []
    for src, dst in _STATE_WRITABLE.items():
        if src not in body:
            continue
        v = str(body.get(src) or "").strip()
        if v and str(a.get(dst) or "") != v:
            a[dst] = v
            applied[dst] = v
    # 名字变了 → id 也跟（id 就是名字；否则前端按 id 找不到它）
    if applied.get("name"):
        a["id"] = applied["name"]
        if not (a.get("keywords") or []):
            a["keywords"] = [applied["name"]]

    for key in body:
        if key not in _STATE_WRITABLE:
            ignored.append(key)
    _save_registry(root, reg)
    return {
        "id": str(a.get("id") or aid),
        "name": str(a.get("name") or ""),
        "type": k,
        "applied": applied,
        "ignored": sorted(ignored),
        "note": ("v5 每个资产只有**一份身份**（identity + 参考图）："
                 "`state_name` 不产生新形象；声音/音色、四视图、per-asset 画风"
                 "与模型 v5 都没有对应语义，已忽略。"
                 "风格是**项目级**的（`brief.pack`），音频口径在 `brief.audio_mode`。"),
    }


def delete_asset(root: Path, kind: str, aid: str) -> dict:
    """删除资产条目。

    ⚠️ **只摘注册表条目，不删 `images/` 里的图**（图可能被别处引用；误删不可逆）。
    """
    reg = assets.load_registry(root)
    items = reg.get("assets") or []
    hit = [a for a in items if str(a.get("id")) == aid or str(a.get("name")) == aid]
    if not hit:
        raise EditError("资产不存在：%s" % aid)
    k = _norm_kind(kind)
    keep = [a for a in items if a not in hit]
    if len(keep) == len(items):
        keep = [a for a in items if str(a.get("name")) != aid]
    reg["assets"] = keep
    _save_registry(root, reg)
    return {"removed": [str(a.get("name")) for a in hit], "type": k,
            "total": len(keep),
            "note": "仅摘除注册表条目；images/ 里的图未删（可能被别处引用）"}


def _norm_kind(kind: str) -> str:
    k = str(kind or "").strip().lower()
    if k in ("character", "characters"):
        return "character"
    if k in ("scene", "scenes", "location"):
        return "location"
    if k in ("prop", "props"):
        return "prop"
    raise EditError("未知资产类型：%r（character / scene / prop）" % kind)


def _save_registry(root: Path, reg: dict) -> None:
    _registry_path(root).write_text(
        json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────── 分镜

def _sb_path(root: Path, ep: int = 1) -> Path:
    """分镜表路径（**集级**，M1 起）。

    ⚠️ 这里必须走 `guards.resolve_path`（新名优先、旧名回退）—— 前端编辑的是
    **本集**的分镜；写死旧名会让第 2 集的编辑落到第 1 集的文件上（静默串集）。
    """
    from . import guards
    return guards.resolve_path(root, "scenedesigner", int(ep))


def _load_shots(root: Path, ep: int = 1) -> tuple:
    p = _sb_path(root, ep)
    if not p.exists():
        raise EditError("项目还没有分镜（scenedesigner/scenedesigner_ep%d.md 不存在）" % int(ep))
    md = p.read_text(encoding="utf-8")
    shots = storyboard.parse(md)
    if not shots:
        raise EditError("分镜解析出 0 镜 —— 文件格式可能有问题，拒绝就地编辑")
    return md, shots


def _find_shot(shots: list, shot: str) -> dict:
    s = str(shot or "").strip()
    for x in shots:
        if x["name"] == s:
            return x
    raise EditError("镜号不存在：%s（本片 %s）"
                    % (s, "、".join(x["name"] for x in shots[:12])))


def _col_index(header_cells: list, keys) -> int | None:
    low = [c.strip().lower() for c in header_cells]
    for i, h in enumerate(low):
        if any(k.lower() in h for k in keys):
            return i
    return None


def _split_row(line: str) -> list:
    """markdown 表格行 → 单元格列表（保留首尾空串，便于原样拼回）。"""
    return line.split("|")


def _join_row(cells: list) -> str:
    return "|".join(cells)


def _is_header_row(line: str) -> bool:
    """这一行是不是分镜表的**表头**？

    ⛔ **不能用 `"画面" in line` 判断**（2026-09-15 真实数据抓到的 bug）：
    数据行的画面描述里**本身就可能含「画面」二字**（实测 fixture 写了
    "第 1 镜的画面描述内容"），于是向上找表头时会命中**上一镜的数据行** →
    按列改写全部落空、报「该分镜表无此列」，而表里其实有那一列。
    真实的 `scenedesigner.md` 里"画面"是高频词，所以这不是边角情况。

    判据**直接复用 `storyboard.parse` 自己的表头规则**（含「画面」列、且**不**匹配
    数据行正则、且不是分隔行）——同一件事不写第二份判据。
    """
    s = (line or "").strip()
    if not s.startswith("|") or storyboard._SEP_RE.match(s):   # noqa: SLF001
        return False
    cells = [c.strip() for c in s.split("|")]
    return (storyboard._col(cells, "画面") is not None      # noqa: SLF001
            and not storyboard._ROW_RE.match(s))            # noqa: SLF001


def _find_header_line(lines: list, before: int) -> int | None:
    """从 `before` 往上找**最近的**表头行。"""
    for j in range(int(before) - 1, -1, -1):
        if _is_header_row(lines[j]):
            return j
    return None


def update_segment(root: Path, ep: int, shot: str, patch: dict) -> dict:
    """按**列名**改分镜表的一行。返回里写明**作废了什么**。

    ⚠️ v5 没有细粒度失效：改一镜的文字也会让该镜的静帧/成片**不再可信**
    （提示词里含画面描述与对白）→ 一律作废静帧 + 暂存 clip + job 置 pending，
    并在返回里如实列出。**绝不"改了却看起来没变"**。
    """
    md, shots = _load_shots(root)
    s = _find_shot(shots, shot)
    lines = md.splitlines()
    idx = int(s["line"])
    if idx >= len(lines) or not lines[idx].strip().startswith("|"):
        raise EditError("定位分镜行失败（镜 %s 在第 %d 行）" % (shot, idx + 1))

    # 找表头：从该行往上找最近的**表头行**（判据见 `_is_header_row`）
    head_i = _find_header_line(lines, idx)
    if head_i is None:
        raise EditError("找不到分镜表头（无法安全按列改写）")
    headers = _split_row(lines[head_i])

    cells = _split_row(lines[idx])
    applied, skipped = {}, []
    for k, v in (patch or {}).items():
        keys = EDITABLE_COLS.get(k)
        if not keys:
            skipped.append(k)
            continue
        ci = _col_index(headers, keys)
        if ci is None:
            skipped.append("%s（该分镜表无此列）" % k)
            continue
        while len(cells) <= ci:
            cells.append("")
        applied[k] = str(v or "")
        cells[ci] = " %s " % str(v or "").replace("|", "／")   # `|` 会破坏表格
    if not applied:
        raise EditError("没有可应用的字段（被跳过：%s）" % "、".join(skipped) or "空 patch")

    lines[idx] = _join_row(cells)
    _sb_path(root).write_text("\n".join(lines) + ("\n" if md.endswith("\n") else ""),
                              encoding="utf-8")

    invalidated = _invalidate_shot(root, ep, shot)
    return {"shot": shot, "applied": applied, "skipped": skipped,
            "invalidated": invalidated,
            "note": "分镜已改；该镜的静帧与成片已作废，需重新生成"}


def delete_segment(root: Path, ep: int, shot: str) -> dict:
    """删除分镜的一行，并作废该镜的产物。**暂存 clip 而非删除**（宁要有瑕疵但完整）。"""
    md, shots = _load_shots(root)
    if len(shots) <= 1:
        raise EditError("至少保留一镜（拒绝删空分镜）")
    s = _find_shot(shots, shot)
    lines = md.splitlines()
    idx = int(s["line"])
    lines.pop(idx)
    _sb_path(root).write_text("\n".join(lines) + ("\n" if md.endswith("\n") else ""),
                              encoding="utf-8")
    invalidated = _invalidate_shot(root, ep, shot)
    return {"removed": shot, "remaining": len(shots) - 1, "invalidated": invalidated,
            "note": "分镜行已删除；成片已从 clips/ 暂存走，重新渲染可覆盖"}


def add_segment(root: Path, ep: int, after: str = "", fields: dict | None = None) -> dict:
    """在某镜之后插入一镜（默认带占位画面描述，需人补内容）。

    ⛔ **两个必须同时满足的条件**，否则这行不会被认作镜头（"插了等于没插"）：
      ① **镜头号列必须有数字** —— `storyboard.parse` 的 `_ROW_RE` 要求首格是 `\\d+`
         或 `\\d+-\\d+`。第一版我把占位文本写进了镜头号列 → 整行被忽略。
      ② **画面描述必须 ≥15 字** —— `parse` 把更短的当占位跳过。
    """
    md, shots = _load_shots(root)
    lines = md.splitlines()
    if after:
        idx = int(_find_shot(shots, after)["line"])
    else:
        idx = int(shots[-1]["line"])
    n_cells = len(_split_row(lines[idx]))
    vals = [""] * n_cells

    head_i = _find_header_line(lines, idx)
    headers = _split_row(lines[head_i]) if head_i is not None else []

    def put(key: str, value) -> bool:
        ci = _col_index(headers, EDITABLE_COLS[key]) if headers else None
        if ci is None:
            return False
        while len(vals) <= ci:
            vals.append("")
        vals[ci] = " %s " % str(value).replace("|", "／")
        return True

    # ① 镜头号：给一个**唯一的整数**（比现有最大镜头号大 1）
    new_no = max([int(s.get("index") or 0) for s in shots] or [0]) + 1
    if n_cells > 1:
        vals[1] = " %d " % new_no

    f = dict(fields or {})
    # ② 画面描述：给了且够长就用，否则用占位
    vis = str(f.pop("visual", "") or "").strip()
    put("visual", vis if len(vis) >= 15 else PLACEHOLDER_VISUAL)
    # ③ 其余字段
    for k, v in f.items():
        put(k, v)

    newline = _join_row(vals)
    lines.insert(idx + 1, newline)
    _sb_path(root).write_text("\n".join(lines) + ("\n" if md.endswith("\n") else ""),
                              encoding="utf-8")

    from .media import storyboard as _sb
    n_after = len(_sb.parse(_sb_path(root, ep).read_text(encoding="utf-8")))
    return {"inserted_after": after or shots[-1]["name"],
            "new_shot_index": idx + 1, "shot_no": new_no,
            "shots_before": len(shots), "shots_after": n_after,
            "visible": n_after > len(shots),
            "note": "已插入新镜（画面描述为占位）；**请补上 ≥15 字的画面描述后再生成**"}


# ─────────────────────────────────────────────────────────── 失效联动

def _invalidate_shot(root: Path, ep: int, shot: str) -> dict:
    """作废某镜的可信产物。**复用 v5 既有原语，不自造**。

    · 静帧：从 `stills.json` 摘掉该镜条目（含 `.jpg.url` 边车）—— 图不再与分镜一致
    · 成片：`clipqc.invalidate()` **暂存**（不是删除），重渲会自动覆盖
    · 任务：`jobs.mark(..., 'pending')`（合法跃迁）
    """
    from .media import stills as stills_mod

    out: dict = {"still": False, "clip": False, "job": False}

    # 1) 静帧
    sd = stills_mod.stills_dir(root, ep)
    data = stills_mod.load(sd)
    if shot in data:
        data.pop(shot, None)
        stills_mod._save(sd, data)                       # noqa: SLF001（同包内原语）
        for name in (shot + ".jpg", shot + ".jpg.url"):
            f = sd / name
            if f.exists():
                f.unlink()
        out["still"] = True

    # 2) 成片 clip（暂存）
    try:
        stashed = clipqc.invalidate(root, [shot], ep=ep, log=lambda *_: None)
        out["clip"] = bool(stashed)
    except Exception as e:                              # noqa: BLE001
        out["clip_error"] = str(e)[:120]

    # 3) 任务状态
    #
    # ⚠️ **必须无条件 mark**（不能写 `if shot in jb`）：上一步 `clipqc.invalidate`
    #   会把该镜的 job 条目 **`pop` 掉**（它的 docstring 写着"清空 job 状态使其可重渲"），
    #    所以那时 `shot not in jb` —— 带条件判断会让 `invalidated["job"]` 永远为 False
    #    （实测被测试抓到）。`jobs.mark` 自己会 `setdefault` 创建条目，无条件调用即可。
    try:
        out_dir = root / "media" / ("ep" + str(ep))
        jb = jobs_mod.load(out_dir)
        jobs_mod.mark(jb, shot, "pending", error="分镜被编辑，待重生成")
        jobs_mod.save(out_dir, jb)
        out["job"] = True
    except Exception as e:                              # noqa: BLE001
        out["job_error"] = str(e)[:120]

    return out


def invalidate_all(root: Path, ep: int, reason: str) -> dict:
    """整片作废（改剧本后用）：把该集所有 job 置 pending 并暂存全部 clip。"""
    md, shots = _load_shots(root)
    names = [s["name"] for s in shots]
    stashed = {}
    try:
        stashed = clipqc.invalidate(root, names, ep=ep, log=lambda *_: None)
    except Exception as e:                              # noqa: BLE001
        return {"error": str(e)[:150]}
    out_dir = root / "media" / ("ep" + str(ep))
    jb = jobs_mod.load(out_dir)
    for n in names:
        if n in jb:
            jobs_mod.mark(jb, n, "pending", error=reason)
    jobs_mod.save(out_dir, jb)
    return {"shots": len(names), "stashed_clips": sorted(stashed), "reason": reason}
