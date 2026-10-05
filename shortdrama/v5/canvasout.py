# -*- coding: utf-8 -*-
"""把一集**已跑完的产物**摆成 Infinite Atelier 的画布（确定性，零模型调用、零配额）。

输入全是盘上已有事实：

| 来源 | 变成画布里的 | 连线 |
|---|---|---|
| `assets.json` + `images/<名>.png` | 资产定妆照节点 | → 提示词里点名了它的那些静帧 |
| `media/ep{N}/stills.json` | 静帧节点（每镜一格） | → 它所属的 pack 组 |
| `media/ep{N}/video_jobs.json` | 组视频节点 | → 成片 |
| `media/ep{N}/episode_final.mp4` | 成片节点 | — |

★ 图片/视频**只填地址**（`<base>/media/<项目>/<相对路径>`），不搬字节。
  Atelier 的节点把 `metadata.content` 原样交给 `<img src>` / `<video src>`
  （`components/canvas/canvas-node.tsx`），没有本地库键时不做任何解析
  ⇒ 画布能直接显示已生成的静帧与片段。

⚠️ 组与镜的对应**只认 `video_jobs.json` 的 `shots` 数组**。
  不要用 `webmap._render_state` 数成片 —— 它按 `clips/LN*.mp4` 匹配，
  而 pack 档产物叫 `packNN.mp4`，恒为 0。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import config

#: 静帧与片段都是竖屏 9:16
_PORTRAIT = (216, 384)
#: 定妆照（三视图横图）
_SHEET = (300, 200)
_ASSET_X = 0
#: 一条横带里第一格的横坐标（左边留出资产栏 + 一段连线走廊，
#: 只留 260px 的话资产那束线会挤成一坨）
_BAND_X0 = 760
#: 带内相邻格 / 相邻两条带的间距
_STEP_X = 256
_STEP_Y = 440
#: 一条带最多几格。pack 组本身 ≤5 镜（`SHORTDRAMA_VIDEO_PACK_MAX_GROUP`），
#: 这个上限只为兜住"没打包的镜"和被手工调大的组，别让一行无限长。
_MAX_PER_BAND = 6
#: 打开时"装进视野"用的**画布可视区宽**：1440 的窗口减去那条「画布元素」侧栏（约 260）。
#: 按整窗宽算会把图撑到右侧出框（实测片段列被切掉）。
_FIT_W = 1180
#: 打开缩放的下限 —— 再小就只是色块，读不出画面
_MIN_SCALE = 0.30


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:                            # noqa: BLE001
        raise RuntimeError("%s 解析失败：%s" % (path, exc)) from exc


def _media_url(pid: str, rel: str, base: str = "") -> str:
    """静态地址。`v5/server.py` 的 `/media/{pid}/{rel}` 只放行 images、media 两棵子树。

    ⚠️ `base` **不能省成习惯性的相对路径**：画布与后端不同源时（开发期 Atelier 在
    `localhost:3000`、后端在 `:8787`），根相对地址会被浏览器解析到**页面所在源**
    ⇒ 每个节点都是破图（实测）。所以由调用方按**请求来源**传绝对前缀。
    """
    return "%s/media/%s/%s" % (base.rstrip("/"), pid, rel)


def _node(nid: str, ntype: str, title: str, x: int, y: int, size: tuple[int, int],
          meta: dict[str, Any]) -> dict[str, Any]:
    w, h = size
    return {"id": nid, "type": ntype, "title": title,
            "position": {"x": x, "y": y}, "width": w, "height": h,
            "metadata": meta}


#: 「这一类没数据」的三条说法。**只在这里写一遍** —— `build()` 与 `node_sources()`
#: 共用，否则列表页给的理由会和真打开画布时看到的不一致。
_WARN_NO_STILLS = "第 %d 集没有静帧登记表（stills.json）⇒ 画布里没有逐镜格"
_WARN_NO_JOBS = "第 %d 集没有渲染任务表（video_jobs.json）⇒ 画布里只有静帧，没有片段组"
_WARN_NO_FINAL = "第 %d 集还没有 episode_final.mp4 ⇒ 画布右侧无收尾节点"
_WARN_NO_ASSET_IMG = "资产「%s」的定妆照不在盘上（%s）"
_WARN_GROUP_SHOT = "组 %s 点名了 %s，但静帧登记表里没有它"


def _read_inputs(root: Path, ep: int) -> tuple[Path, dict, dict, list]:
    """读这一集画布的**四类输入**（缺文件给空，不报错；解析失败才响亮终止）。"""
    ep_dir = root / "media" / ("ep%d" % ep)
    stills = _read_json(ep_dir / "stills.json") or {}
    jobs = _read_json(ep_dir / "video_jobs.json") or {}
    assets = (_read_json(root / "assets.json") or {}).get("assets") or []
    return ep_dir, stills, jobs, assets


def _assets_on_disk(assets: list, img_dir: Path, warns: list[str]) -> list[tuple[str, dict, Path]]:
    """资产 → **定妆照真在盘上**的那些（画布左栏一格 = 这里一条）。

    ⛔ 不看 `assets.json` 里登记了几条：名字为空、或 `ref_image` 指不到真文件的
    都不会变成节点（`build()` 就是按这个条件跳过的）。缺文件的进 `warns`。
    """
    out: list[tuple[str, dict, Path]] = []
    for a in assets:
        name = (a.get("name") or "").strip()
        if not name:
            continue
        f = img_dir / (a.get("ref_image") or "")
        if not f.is_file():
            warns.append(_WARN_NO_ASSET_IMG % (name, a.get("ref_image") or "ref_image 为空"))
            continue
        out.append((name, a, f))
    return out


def node_sources(root: Path, ep: int = 1) -> dict[str, Any]:
    """这一集的画布**会有几格** —— 给列表端点决定「这个入口能不能点」。

    ★ 为什么要单独出这个读数：入口原先按**分镜表镜数**（`shots`）放行，而画布的格子
      来自静帧登记表 / 渲染任务表 / 定妆照 / 成片 —— 两者可以完全无关。
      实测打包应用里唯一那颗可点的按钮（13 镜）指向一张**零格**画布，
      画布应用只能报「接口返回里没有节点」并停在列表页。
    ★ 判据必须与 `build()` 同源（共用 `_read_inputs` / `_bands_of` / `_assets_on_disk`）：
      数出来的格数、以及**理由的原文与顺序**，都要和真打开画布时看到的逐字一致 ——
      否则列表页说"这一集没静帧"、点进去却报另一件事，又是一种"看着正常其实没通"。
      回归测试 `tests_canvasout.TestNodeSources` 逐种形状对账这两者。
    """
    ep_dir, stills, jobs, assets = _read_inputs(root, ep)
    warns: list[str] = []
    if not stills:
        warns.append(_WARN_NO_STILLS % ep)
    if not jobs:
        warns.append(_WARN_NO_JOBS % ep)
    # 走一遍带（与 build() 同序）：一条带尾挂一个组节点，`pk=None` 的散镜带没有
    bands = _bands_of(sorted(stills), jobs)
    packs = 0
    for _, pk in bands:
        if not pk:
            continue
        packs += 1
        for s in (jobs[pk].get("shots") or []):
            if s not in stills:
                warns.append(_WARN_GROUP_SHOT % (pk, s))
    placed = _assets_on_disk(assets, root / "images", warns)
    final = (ep_dir / "episode_final.mp4").is_file()
    if not final:
        warns.append(_WARN_NO_FINAL % ep)
    n_stills, n_assets = len(stills), len(placed)
    return {"stills": n_stills, "packs": packs, "assets": n_assets, "final": final,
            "nodes": n_stills + packs + n_assets + (1 if final else 0),
            "warnings": warns}


def fingerprint(root: Path, ep: int) -> str:
    """**输入指纹**：产物内容 + 集号 + 会改变画布形态的生成参数。

    只记文本不够 —— 画幅/视频档/组大小换了，同一份文本会摆出完全不同的图。
    """
    h = hashlib.sha1()
    ep_dir = root / "media" / ("ep%d" % ep)
    for name in ("stills.json", "video_jobs.json"):
        f = ep_dir / name
        h.update(("%s|" % name).encode())
        h.update(f.read_bytes() if f.is_file() else b"<missing>")
    a = _read_json(root / "assets.json")
    h.update(("assets|%d|" % len((a or {}).get("assets") or [])).encode())
    # ★ 档位读 `config.VIDEO_MODE`（**已校验过的值**）而不是裸 `os.environ.get`：
    #   缓存键必须等于实际渲染用的档位。若这里读原始 env 而渲染读 config，
    #   一个拼错的档位名（config 回落 reference）就会算出**同一把钥匙但不同产物**。
    h.update(("ep=%d|aspect=%s|mode=%s|packmax=%s" % (
        ep,
        os.environ.get("SHORTDRAMA_ASPECT", "9:16"),
        config.VIDEO_MODE,
        os.environ.get("SHORTDRAMA_VIDEO_PACK_MAX_GROUP", "5"))).encode())
    return h.hexdigest()[:16]


def _bands_of(order: list[str], jobs: dict[str, Any]) -> list[tuple[list[str], str | None]]:
    """把镜切成横带：**一个 pack 组一条带**（带尾挂那条带的片段节点）。

    pack 组本来就是"相邻同场景贪心"分出来的（`media/video_plan.group_shots`），
    所以一条带 ≈ 一场戏 —— 这就是按组摆而不是按列摆的理由。
    竖排一根柱子在 30 镜的项目上是 16 屏高（实测），不可用。
    没进任何组的镜（只跑了静帧、没出片）单独成带，不丢。
    """
    in_stills = set(order)
    used: set[str] = set()
    out: list[tuple[list[str], str | None]] = []
    for pk in sorted(jobs):
        shots = [s for s in (jobs[pk].get("shots") or []) if s in in_stills]
        used.update(shots)
        chunks = [shots[i:i + _MAX_PER_BAND] for i in range(0, len(shots), _MAX_PER_BAND)] or []
        for ci, chunk in enumerate(chunks):
            # 组节点只挂在该组**最后一条带**的尾部，多带的组不会重复出现节点
            out.append((chunk, pk if ci == len(chunks) - 1 else None))
    left = [s for s in order if s not in used]
    for i in range(0, len(left), _MAX_PER_BAND):
        out.append((left[i:i + _MAX_PER_BAND], None))
    return out


def build(root: Path, ep: int = 1, base: str = "") -> dict[str, Any]:
    """返回一份可直接喂给 Atelier `importProject()` 的对象（多余字段它会自动忽略）。

    缺文件**不静默**：哪一类没数据就进 `warnings`，让人看得见"这集没出静帧"。
    """
    pid = root.name
    ep_dir, stills, jobs, assets = _read_inputs(root, ep)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    warns: list[str] = []

    if not stills:
        warns.append(_WARN_NO_STILLS % ep)
    if not jobs:
        warns.append(_WARN_NO_JOBS % ep)

    order = sorted(stills)
    bands = _bands_of(order, jobs)
    #: 镜 → 它所在那条带的纵坐标（资产归位与连线都读这个）
    shot_y = {s: i * _STEP_Y for i, (shots, _) in enumerate(bands) for s in shots}
    shot_band = {s: i for i, (shots, _) in enumerate(bands) for s in shots}

    # ── 横带：逐镜静帧，带尾挂该带的片段组 ──
    for bi, (shots, pk) in enumerate(bands):
        for j, shot in enumerate(shots):
            rec = stills[shot] or {}
            nodes.append(_node("s:%s" % shot, "image", "%s · %ss" % (shot, rec.get("seconds", "?")),
                               _BAND_X0 + j * _STEP_X, bi * _STEP_Y, _PORTRAIT,
                               {"content": _media_url(pid, "media/ep%d/stills/%s.jpg" % (ep, shot), base),
                                "prompt": rec.get("prompt") or "", "status": "success",
                                "seconds": str(rec.get("seconds") or "")}))
        if not pk:
            continue
        rec = jobs[pk] or {}
        local = Path(rec.get("local") or "")
        state = rec.get("state") or "?"
        nodes.append(_node("p:%s" % pk, "video", "%s · %s · %s镜" % (pk, state, len(rec.get("shots") or [])),
                           _BAND_X0 + len(shots) * _STEP_X + 40, bi * _STEP_Y, _PORTRAIT,
                           {"content": _media_url(pid, "media/ep%d/clips/%s" % (ep, local.name or "%s.mp4" % pk), base),
                            "prompt": " / ".join(rec.get("shots") or []),
                            "status": "success" if state == "completed" else "error",
                            "errorDetails": rec.get("error") or ""}))
        for s in shots:
            edges.append({"id": "s:%s>p:%s" % (s, pk), "fromNodeId": "s:%s" % s, "toNodeId": "p:%s" % pk})
        for s in (jobs[pk].get("shots") or []):
            if s not in stills:
                warns.append(_WARN_GROUP_SHOT % (pk, s))

    # ── 左栏：资产定妆照排成一条图例，按「首次出场的那条带」排序 ──
    # 为什么不是"放在它平均出现的高度"：13 条带时那样会把所有资产挤到中下部，
    # 且同一场戏的资产 y 完全相同 ⇒ 互相盖住（实测两个都在 y=3373）。
    # 「哪些资产真能变成一格」在 `_assets_on_disk()` 里定，与 `node_sources()` 同一份。
    rows: list[tuple[int, str, dict[str, Any]]] = []
    for name, a, f in _assets_on_disk(assets, root / "images", warns):
        # 资产 → 本镜：提示词里**原样出现**资产名才算（保守匹配，宁可少连不可连错）
        hits = [s for s in order if name in ((stills[s] or {}).get("prompt") or "")]
        first = min([shot_y[s] for s in hits if s in shot_y], default=10 ** 9)
        rows.append((first, name, {"file": f.name, "hits": hits, "a": a, "seen": {}}))
    rows.sort(key=lambda r: (r[0], r[1]))
    for i, (_, name, info) in enumerate(rows):
        nodes.append(_node("a:%s" % name, "image", "%s（%s）" % (name, (info["a"].get("type") or "asset")),
                           _ASSET_X, i * (_SHEET[1] + 40), _SHEET,
                           {"content": _media_url(pid, "images/%s" % info["file"], base),
                            "prompt": info["a"].get("prompt") or "", "status": "success"}))
        for s in info["hits"]:
            b = shot_band.get(s)
            if b is not None and b not in info["seen"]:
                info["seen"][b] = s
        # 连线粒度 = **场次**：同一场戏里逐镜再连一遍不携带信息（实测「侯府正门长阶」
        # 一条资产就拉出 30 根线，因为它每镜都在）。每条带只连它的首镜。
        for s in info["seen"].values():
            edges.append({"id": "a:%s>s:%s" % (name, s), "fromNodeId": "a:%s" % name,
                          "toNodeId": "s:%s" % s})
    n_placed = len(rows)

    # ── 收尾：成片放在所有带的右侧、纵向居中 ──
    final = ep_dir / "episode_final.mp4"
    widest = max([len(shots) for shots, _ in bands] or [1])
    if final.is_file():
        nodes.append(_node("f:final", "video", "第 %d 集成片" % ep,
                           _BAND_X0 + (widest + 1) * _STEP_X + 40,
                           max(0, (len(bands) - 1) * _STEP_Y // 2), _PORTRAIT,
                           {"content": _media_url(pid, "media/ep%d/episode_final.mp4" % ep, base),
                            "status": "success"}))
        for pk in sorted(jobs):
            if any(p == pk for _, p in bands):
                edges.append({"id": "p:%s>f:final" % pk, "fromNodeId": "p:%s" % pk, "toNodeId": "f:final"})
    else:
        warns.append(_WARN_NO_FINAL % ep)

    xs = [n["position"]["x"] + n["width"] for n in nodes] or [0]
    ys_all = [n["position"]["y"] + n["height"] for n in nodes] or [0]
    w, h = max(xs) - _ASSET_X, max(ys_all)
    #: 打开时**按宽度**适配、纵向留可读下限。
    #: 按高度适配会把 30 镜的项目压到 0.15（实测）—— 形状看得见但格子全糊成一片，
    #: 而"看清每一格"才是这张图存在的理由；纵向靠小地图与滚动，不靠缩小。
    k = min(1.0, max(_MIN_SCALE, round(_FIT_W / max(w, 1), 3)))

    canvas: dict[str, Any] = {
        "title": "%s · 第 %d 集" % (pid, ep),
        "nodes": nodes,
        "connections": edges,
        #: 起点让开画布应用自己那条「画布元素」侧栏（约 260px）——
        #: 不让开的话，缩放 0.85 时整条资产栏正好压在它下面（实测看不见资产格）。
        "viewport": {"x": 280, "y": 64, "k": k},
        "backgroundMode": "lines",
        "showImageInfo": False,
        "fingerprint": fingerprint(root, ep),
        "stats": {"assets": n_placed, "stills": len(order), "packs": len(jobs),
                  "bands": len(bands), "final": final.is_file(), "edges": len(edges),
                  "width": w, "height": h, "scale": k},
        "warnings": warns,
    }
    return canvas
