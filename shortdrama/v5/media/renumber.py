# -*- coding: utf-8 -*-
"""分镜增删之后，把「按镜名存的东西」重挂到新镜名。

## 为什么必须有这个模块（2026-10-02）

`storyboard.parse` 给的镜名 `LNxx` 是**按行序**编号的（不是分镜表里「镜头号」那一列 ——
那一列写 `1-1 / 1-2` 时它取到的整数是**幕号**，会重复，不能当身份）。
而媒体层的一切产物都**按镜名落盘**：

    media/epN/stills.json                键 = 镜名（值里还有绝对路径 `path`）
    media/epN/stills/LNxx.jpg            + `LNxx.jpg.url` 边车
    media/epN/tails.json / tails/        键 = 镜名（落幅帧）
    media/epN/still_qc_seen.json         键 = 镜名（`{mtime, clean}`）
    media/epN/still_requeue_tally.json   键 = 镜名
    media/epN/video_jobs.json            键 = 镜名（reference/keyframe 档）或 `packNN`（打包档）
    media/epN/clips/LNxx.mp4             + `LNxx.last.jpg`（打包档是 `packNN.mp4`）

⇒ **在中间删一行或插一行，会让后面每一镜的名字全体挪一位，而文件不会跟着动。**
实测（删 LN02）：删后 `LN03` 这条名字指向的画面已经是原来的 `LN04`，
而 `stills/LN03.jpg` 里装的还是原来 `LN03` 的图 ⇒ **从这以后每一镜都挂着隔壁镜的画面**。
最坏的是它**不报错**：`webmap.storyboard_detail` 按名字取静帧、`compose` 按名字拼片段，
日志全绿、前端有图、成片能出 —— 只有人眼看成片才发现。

前端有两个按钮直接踩这条（`v5/webwrite.delete_segment` / `add_segment`），
所以重挂必须由**代码**做，不能靠「提醒人别在中间删」。

## 打包档（`VIDEO_MODE=pack`）为什么不能照搬改名

打包档的 job 键是**组号**（`pack01…`），组由 `video_plan.group_shots` 按
「同场景相邻 + ≤12 秒」贪心分出 ⇒ 删一镜会让**后面所有组的边界**都可能变。
这里不猜，改成**按内容对账**：每条组记录里存着 `shots: [镜名…]`，
先把它映射到新镜名，再与**改动后重算出来的分组**逐组比对 ——
成员完全一致 ⇒ 只是组号变了，改名保住成片；不一致 ⇒ 这一组的内容确实不再是那段视频，
交给 `clipqc.invalidate()` 暂存作废（**不删**），重渲时覆盖。

## 顺序与「洞」的纪律（写错的后果是把对的搬成错的）

· **删**（名字整体前移）→ **从小到大**处理：每个目标位恰好是上一轮已经搬空的源位。
· **插**（名字整体后移）→ **从大到小**处理：同理。
· 源**不存在**时**必须清空目标**。否则会出现「洞」：只渲了 LN01/LN02/LN04 的集删掉
  LN02，`LN03`（没渲）搬到 `LN02` 是空操作，于是 `LN02` 留着**被删那镜**的静帧。

## 与审批门的关系

`media/epN/approvals.json` 的指纹按 `stills.json` + 分镜内容算（既有实现），
重挂会改 `stills.json` ⇒ 批文自动作废，这是**对的**，这里不碰它。
"""
from __future__ import annotations

import json
from pathlib import Path


# ───────────────────────────────────────────────────────────── 镜名对照

def ln_name(pos: int) -> str:
    """1-based 位置 → 镜名。与 `storyboard.parse` 的 `"LN%02d" % 位置` **同一口径**。"""
    return "LN%02d" % int(pos)


def shift_map(total_before: int, pos: int, op: str) -> dict:
    """算出「旧镜名 → 新镜名」的对照（`None` = 该镜的产物要丢弃）。

    · `op="delete"`：`pos` = **被删镜**的位置 ⇒ 其后的每一镜前移一位。
    · `op="insert"`：`pos` = **新插入行**的位置 ⇒ 原占该位及其后的镜后移一位。

    ⚠️ 只按位置算，不看内容 —— 调用方必须保证**一次只动一行**
    （`webwrite` 的增删正是如此）。一次动多行请分多次调用。
    """
    total_before = int(total_before)
    pos = int(pos)
    hi = total_before + (1 if op == "insert" else 0)
    if pos < 1 or pos > hi:
        raise ValueError("shift_map：位置 %d 超出 1..%d（op=%s）" % (pos, hi, op))
    out: dict = {}
    if op == "delete":
        out[ln_name(pos)] = None
        for j in range(pos + 1, total_before + 1):
            out[ln_name(j)] = ln_name(j - 1)
    elif op == "insert":
        for j in range(total_before, pos - 1, -1):
            out[ln_name(j)] = ln_name(j + 1)
    else:
        raise ValueError("shift_map：未知操作 %r" % op)
    return out


# ───────────────────────────────────────────────────────────── 磁盘原语

def _load(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:                                    # noqa: BLE001
        return {}


def _save(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _move_file(src: Path, dst: Path) -> bool:
    """搬一个文件；**目标已存在就覆盖**（目标那一份已经不属于这里了）。"""
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        try:
            dst.unlink()
        except Exception:                                # noqa: BLE001
            pass
    src.replace(dst)
    return True


def _drop(*files: Path) -> int:
    n = 0
    for f in files:
        if f.exists():
            try:
                f.unlink()
                n += 1
            except Exception:                            # noqa: BLE001
                pass
    return n


def remap_json(data: dict, mapping: dict) -> tuple:
    """按键重挂一张 json 清单，返回 `(新表, 搬了几条)`。

    ⛔ 不能「就地写新键」：目标键可能已经存在（那是别的镜的旧条目），
    而且**源不存在**时必须让目标也变空（洞）。所以先算「这次涉及哪些名字」
    （映射的键 ∪ 值），把这些键从旧表里**全部剔除**，再按映射逐条放回。
    没被涉及的键（例如 `packNN`、别的命名）原样保留。
    """
    touched = set(mapping) | {v for v in mapping.values() if v}
    out = {k: v for k, v in data.items() if k not in touched}
    inv = {v: k for k, v in mapping.items() if v}        # 新名 → 旧名
    moved = 0
    for new, old in inv.items():
        if old in data:
            out[new] = data[old]
            moved += 1
    return out, moved


def _fix_path_field(rec, mapping: dict) -> None:
    """条目里的 `path` / `local` 是**绝对路径**，集号在中间 —— 换名必须一起改。

    ⚠️ 不改的后果很隐蔽：`webmap._still_relpath` 用 `relative_to(root)` 求解，
    它会老老实实把**旧文件**的相对路径给前端，于是前端显示的还是隔壁镜的图。
    """
    if not isinstance(rec, dict):
        return
    for field in ("path", "local"):
        v = str(rec.get(field) or "")
        if not v:
            continue
        q = Path(v)
        for old, new in mapping.items():
            if new and q.stem == old:
                rec[field] = str(q.with_name(new + q.suffix))
                break


def _remap_named_files(dir_path: Path, mapping: dict, suffixes: tuple,
                       order: list) -> int:
    """把 `dir_path/<name><suffix>` 按映射搬到新名字；洞要清空目标。"""
    if not dir_path.exists():
        return 0
    n = 0
    for old in order:
        if old not in mapping:
            continue
        new = mapping[old]
        for suf in suffixes:
            src = dir_path / (old + suf)
            if new is None:                              # 被删镜的产物 ⇒ 丢弃
                n += _drop(src)
                continue
            dst = dir_path / (new + suf)
            if src.exists():
                if _move_file(src, dst):
                    n += 1
            else:
                n += _drop(dst)                          # 洞：目标那一份不可信
    return n


# ───────────────────────────────────────────────────────────── 静帧侧

def remap_stills(root: Path, ep: int, mapping: dict, order: list) -> dict:
    """`stills.json` + 图 + `.url` 边车 + `tails.json` + 两张 QC 计数表。"""
    out_dir = root / "media" / ("ep%d" % int(ep))
    rep: dict = {}

    mpath = out_dir / "stills.json"
    data = _load(mpath)
    if data:
        new_data, moved = remap_json(data, mapping)
        for rec in new_data.values():
            _fix_path_field(rec, mapping)
        if moved or len(new_data) != len(data):
            _save(mpath, new_data)
        rep["stills_json"] = moved
    rep["still_files"] = _remap_named_files(out_dir / "stills", mapping,
                                            (".jpg", ".jpg.url", ".png"), order)

    tpath = out_dir / "tails.json"
    tdata = _load(tpath)
    if tdata:
        new_t, moved_t = remap_json(tdata, mapping)
        for rec in new_t.values():
            _fix_path_field(rec, mapping)
        if moved_t or len(new_t) != len(tdata):
            _save(tpath, new_t)
        rep["tails_json"] = moved_t
    rep["tail_files"] = _remap_named_files(out_dir / "tails", mapping,
                                           (".jpg", ".jpg.url"), order)

    for fname, key in (("still_qc_seen.json", "seen_json"),
                       ("still_requeue_tally.json", "tally_json")):
        p = out_dir / fname
        d = _load(p)
        if d:
            nd, moved_d = remap_json(d, mapping)
            if moved_d or len(nd) != len(d):
                _save(p, nd)
            rep[key] = moved_d
    return rep


# ───────────────────────────────────────────────────────────── 视频侧

def _is_pack_keys(keys) -> bool:
    return any(str(k).lower().startswith("pack") for k in keys)


def remap_jobs(root: Path, ep: int, mapping: dict, order: list, shots_after: list,
               log=print) -> dict:
    """`video_jobs.json` + `clips/`。

    ⚠️ 打包档**保不住**的组走 `clipqc.invalidate()`（暂存不删），并计入
    `invalidated_groups` —— 那是「这笔钱保不住」的唯一诚实口径，必须报出来。
    """
    from . import clipqc, jobs as jobs_mod, video_plan

    out_dir = root / "media" / ("ep%d" % int(ep))
    clip_dir = out_dir / "clips"
    jb = jobs_mod.load(out_dir)
    rep: dict = {"mode": "none", "jobs_renamed": 0, "clips_moved": 0,
                 "invalidated_groups": [], "notes": []}
    if not jb:
        rep["clips_moved"] = _remap_named_files(clip_dir, mapping,
                                                (".mp4", ".last.jpg"), order)
        rep["mode"] = "shot" if rep["clips_moved"] else "none"
        return rep

    if not _is_pack_keys(jb.keys()):
        rep["mode"] = "shot"
        new_jb, moved = remap_json(jb, mapping)
        for rec in new_jb.values():
            _fix_path_field(rec, mapping)
        if moved or len(new_jb) != len(jb):
            jobs_mod.save(out_dir, new_jb)
        rep["jobs_renamed"] = moved
        rep["clips_moved"] = _remap_named_files(clip_dir, mapping,
                                                (".mp4", ".last.jpg"), order)
        # 暂存区里的坏片也一起搬 —— 那是历史，名字要还能对上
        rep["stashed_moved"] = _remap_named_files(clip_dir / ".clipqc_bad", mapping,
                                                  (".mp4", ".last.jpg"), order)
        return rep

    # ── 打包档：按内容对账，不猜组号 ──
    rep["mode"] = "pack"
    groups_after = [tuple(str(s.get("name") or "") for s in g)
                    for g, _sec in video_plan.group_project_shots(root, shots_after, ep)]
    renamed: dict = {}
    doomed: list = []
    #: 被删镜的名字集合。⚠️ **不能用 `mapping.get(m) is None` 判"这一镜被删了"** ——
    #: 没参与本次位移的名字（例如删第 3 镜时组 A 的 `LN01/LN02`）`get` 同样回 `None`，
    #: 于是**所有组**都被误判成"内容没了"⇒ 整集成片一起作废（实测被测试抓到）。
    deleted = {k for k, v in mapping.items() if v is None}
    for old_key, rec in sorted(jb.items()):
        members = [str(s) for s in ((rec or {}).get("shots") or [])]
        if not members:
            doomed.append(old_key)      # 老记录没写成员 ⇒ 无法对账，不冒认
            continue
        if set(members) & deleted:
            doomed.append(old_key)                      # 本组少了内容，视频不再成立
            continue
        want = tuple(mapping.get(m, m) for m in members)
        if want in groups_after:
            new_key = "pack%02d" % (groups_after.index(want) + 1)
            if new_key != old_key:
                renamed[old_key] = new_key
        else:
            doomed.append(old_key)                      # 组边界变了，成员对不上

    # ★ **顺序必须是"先作废、后改名"**：作废会把 `clips/packNN.mp4` 移进暂存区，
    #   而改名会把别的组搬进同一个名字下。反过来做（先改名再作废）就会
    #   把**保住的那一组的成片**当成坏组暂存走 —— 静默丢钱，正是本模块要防的事。
    if doomed:
        try:
            stashed = clipqc.invalidate(root, doomed, ep=ep, log=lambda *_: None)
            rep["invalidated_groups"] = sorted(stashed) or sorted(doomed)
        except Exception as e:                          # noqa: BLE001
            rep["notes"].append("片段组作废失败（%s: %s）—— 这些组**仍在盘上**，"
                                "重渲前请人工确认" % (type(e).__name__, str(e)[:120]))
            rep["invalidated_groups"] = sorted(doomed)

    new_jb, moved = remap_json(jb, renamed)
    #: ⚠️ 剔除作废组时**不能碰改名的目标键**：删中间那组时，后一组的新组号恰好就是
    #: 被删那组的旧组号（`pack03 → pack02`），无条件 pop 会把**刚保住的成片记录**
    #: 一起删掉（实测被测试抓到：表里只剩 pack01）。
    _inv = {v for v in renamed.values()}
    for k in doomed:
        if k not in _inv:
            new_jb.pop(k, None)
    for rec in new_jb.values():
        if isinstance(rec, dict):
            rec["shots"] = [str(mapping.get(str(s), s)) for s in (rec.get("shots") or [])]
            _fix_path_field(rec, renamed)
    if moved or len(new_jb) != len(jb):
        jobs_mod.save(out_dir, new_jb)
    rep["jobs_renamed"] = moved
    rep["clips_moved"] = _remap_named_files(clip_dir, renamed, (".mp4", ".last.jpg"),
                                            sorted(renamed, reverse=True))
    if rep["invalidated_groups"]:
        log("[renumber] ⚠️ 打包档有 %d 个片段组的成员关系变了，已暂存作废、需重渲：%s"
            % (len(rep["invalidated_groups"]), ",".join(rep["invalidated_groups"])))
    return rep


# ───────────────────────────────────────────────────────────── 总入口

def remap_after_edit(root: Path, ep: int, op: str, pos: int, log=print) -> dict:
    """改完分镜表之后调用一次：把本集所有按镜名存的产物重挂到新镜名。

    ⚠️ 调用方必须**先把分镜表落盘**再调这里 —— 总镜数是从**改动后**的表读的，
    顺序错了会整体差一位。
    """
    from . import storyboard

    # ★ 必须走 `guards.resolve_path`（新名优先、旧名回退）—— 写死 `scenedesigner_epN.md`
    #   会让**旧项目**（一维产物名 `scenedesigner.md`）解析出 0 镜，于是这里
    #   直接返回"没有可重挂的镜"，素材照旧错位（与 M1 那批串集缺陷同根）。
    from .. import guards
    md_path = guards.resolve_path(root, "scenedesigner", int(ep))
    shots_after: list = []
    try:
        if md_path.exists():
            shots_after = storyboard.parse(md_path.read_text(encoding="utf-8"))
    except Exception:                                    # noqa: BLE001
        shots_after = []
    total_after = len(shots_after)
    total_before = total_after + (1 if op == "delete" else -1)
    if total_after < 1 or total_before < 1:
        return {"ok": False, "reason": "改动后没有可重挂的镜（分镜表解析出 0 镜？）"}

    mapping = shift_map(total_before, pos, op)
    names = [ln_name(i) for i in range(1, total_before + 1)]
    order = names if op == "delete" else list(reversed(names))

    rep = {"ok": True, "op": op, "pos": pos, "ep": int(ep),
           "shots_before": total_before, "shots_after": total_after,
           "stills": remap_stills(root, ep, mapping, order),
           "jobs": remap_jobs(root, ep, mapping, order, shots_after, log=log)}

    # 名字变了但**画面内容没变**的那些镜，`_invalidate_shot` 不该波及；
    # 只有真正被动的那一镜要作废（调用方负责，见 webwrite）。
    final = root / "media" / ("ep%d" % int(ep)) / "episode_final.mp4"
    if final.exists():
        rep.setdefault("notes", []).append(
            "成片仍是**改动前**的那一版（里面还带着被删/没带新加的镜）—— "
            "重跑「生成最终视频」才会覆盖，这里不自动烧配额")
    return rep
