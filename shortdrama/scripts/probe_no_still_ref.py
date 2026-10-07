# -*- coding: utf-8 -*-
"""A/B 旁路：**不给静帧**，只喂资产图（角色设定表 / 场景空镜 / 道具图）+ 文字。

对准的病：默认逐镜 reference 档一条请求只喂 1 张图 = 本镜静帧
（`video.py:216`），而 2026-09-28 华山 v2 实测「静帧画错的身份会被视频忠实继承」。
pack 档当时把修法落进了自己的图序，**逐镜档没落**。本脚本验的就是：
把静帧从视频请求里整个拿掉，只给设定表，成片是变好还是变坏。

单变量纪律（除图以外一字不动）：
  · 分镜、秒数、画幅、模型、提示词组装函数 全部沿用生产路径；
  · 提示词只把开头的「以 <Picture 1> 中的角色、服装与场景为参考」换成逐张点名声明，
    因为此时 Picture 1 不再静帧，那句原话会说谎；
  · 先 freeze 落盘（图清单 + 最终提示词），提交用的就是这份冻结数据 ⇒ 可复现、可审计。

基线不重渲：`projects/madfate-abc-1005-reference/media/ep1/clips/` 是 10-05 渲的
同分镜、同静帧来源、同提示词的 15 条成片，直接当对照臂。

用法（仓库根执行，PYTHONUTF8=1）：
    .venv/Scripts/python.exe scripts/probe_no_still_ref.py --phase freeze
    .venv/Scripts/python.exe scripts/probe_no_still_ref.py --phase probe     # 只提 LN01
    .venv/Scripts/python.exe scripts/probe_no_still_ref.py --phase rest      # 余下 14 镜
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "projects" / "madfate-abc-1005-reference"   # 输入来源（只读）
DST = ROOT / "projects" / "madfate-abc-1005-nostill"    # 本臂自己的目录（新写）
EP = 1

# 只拷输入产物，绝不拷 media（否则把基线成片一起复制进来，幂等记账会复用上一臂 ⇒ 假对照）
COPY_DIRS = ["scenedesigner", "worldbuilder", "assetdesigner", "scriptwriter",
             "dialogue", "plotdesigner", "director", "reviewer", "images"]
COPY_FILES = ["brief.json", "assets.json"]

MANIFEST = "frozen_inputs.json"
PROMPTS = "video_prompts.json"

#: 本臂一条请求最多几张图。⛔ 不放开到接口上限 5：10-06 桥上决斗在**视频通道**实测
#: 「图的张数 > 分镜要求的人数 ⇒ 多画一个人」（cap=5 画三人、cap=3 全对）。
#: 本集每镜 1 人，3 张（脸 + 场景 + 道具）已经是风险档，写在清单里供人眼验收。
ARM_MAX_IMAGES = 3
#: 人物设定表张数（本集无双人镜，1 足够；留出场景与道具的位置）。
ARM_MAX_CHARS = 2
#: 是否单独喂道具图。**定妆照或场景卡里已经画了同一件兵器**的包（仙侠这类）应当关掉：
#: 10-06 桥上决斗实测「定妆照已带兵器 ⇒ 单独道具图是重复供给」，多一张图就多一次
#: "把图读成多主体"的机会。由 `--no-props` 置 False。
ARM_PROPS = True


def setup_dir(log=print) -> None:
    """建本臂目录：输入从基线拷，media 从零开始。"""
    DST.mkdir(parents=True, exist_ok=True)
    for f in COPY_FILES:
        s = SRC / f
        if s.exists():
            shutil.copy2(s, DST / f)
    for d in COPY_DIRS:
        s, t = SRC / d, DST / d
        if s.exists():
            if t.exists():
                shutil.rmtree(t)
            shutil.copytree(s, t)
    (DST / "media" / ("ep%d" % EP) / "clips").mkdir(parents=True, exist_ok=True)
    log("[arm] 目录就绪：%s（media 为空，基线在 %s）" % (DST.name, SRC.name))


def scene_location_url(assets_mod, reg, scene_col, log=print):
    """按本镜「场景」列在注册表里找那张场景空镜，返回 (url, 名字) 或 (None, 原因)。

    为什么要自己找：`bind()` 只在**宽景**绑 location（构图理由，见 assets.py:1420），
    而这条理由在"没有静帧"的臂上不成立 —— 本臂的构图全靠文字，场景图只当地貌锚，
    与 pack 档的「第 N 张只用于锁定场景内的建筑与地貌」同一用法。
    ⚠️ 歧义不猜：两处都可能匹配 ⇒ 只报一行，宁缺。
    """
    key = (scene_col or "").strip().lstrip("@").split("（")[0].strip()
    if not key:
        return None, "本镜无场景列"
    cands = []
    for a in reg.get("assets", []):
        if a.get("type") != "location":
            continue
        nm = str(a.get("name") or "").strip()
        if not nm:
            continue
        if nm == key or nm in key or (len(key) >= 3 and key in nm):
            cands.append(a)
    if not cands:
        # 名字没对上再试关键词（分镜常写简称，如「云海」而卡名是「断剑坪·坪内」）
        for a in reg.get("assets", []):
            if a.get("type") != "location":
                continue
            if any(key == str(k).strip() or (len(key) >= 2 and key in str(k))
                   for k in (a.get("keywords") or [])):
                cands.append(a)
    if not cands:
        return None, "注册表无匹配场景卡(%s)" % key
    if len(cands) > 1:
        return None, "场景名歧义(%s→%s)" % (key, "/".join(c["name"] for c in cands))
    us = assets_mod._safe_ref_urls(cands[0], DST)
    return (us[0] if us else None), cands[0]["name"]


def build_inputs(log=print):
    """按生产路径把 15 镜的提示词与「只含资产图」的图清单算出来并冻结落盘。"""
    from v5 import config
    from v5.media import assets, cast, prompt as prompt_mod, relations
    from v5.media import storyboard as sbo
    from v5.media import style as style_mod

    md = (DST / "scenedesigner" / ("scenedesigner_ep%d.md" % EP)).read_text(encoding="utf-8")
    shots = relations.plan_frames(sbo.parse(md))
    shots = prompt_mod.resolve_styles(shots)

    # 注入与生产同构：角色名表 / 风格块 / 场景锚点 / 出场人数
    names = []
    wb = DST / "worldbuilder" / "worldbuilder.md"
    if wb.exists():
        names = [c["name"] for c in cast.parse_characters(wb.read_text(encoding="utf-8"))]
    block = style_mod.wrap(style_mod.load(DST))
    sl = assets.scene_lines(DST, shots, log=log) or {}
    cn = assets.cast_counts(DST, shots) or {}
    # 身份文字锚点：生产只在**关闭参考图**的包上注入 —— 本包开着，照基线不注入
    idl = {}
    if not style_mod.still_refs_enabled(DST):
        idl = assets.identity_lines(DST, shots, ep=EP) or {}
    shots = [{**s,
              "_names": names,
              "_style_block": block,
              "_identity_line": idl.get(s["name"], ""),
              "_scene_line": sl.get(s["name"], ""),
              "_cast_n": cn.get(s["name"], 0)} for s in shots]

    # ★ 一次 bind 同时拿 URL 与类型/名字 —— **不能调两次**：绑图跨进程会换
    #   （本项目已知未根因的病），两次调用会让「清单上的图」≠「实际提交的图」。
    named: dict = {}
    typed: dict = {}
    bound = assets.bind(DST, shots, names_out=named, types_out=typed, ep=EP)
    reg = assets.auto_sync(DST)

    rows = []
    for s in shots:
        nm = s["name"]
        triples = list(zip(bound.get(nm) or [], named.get(nm) or [], typed.get(nm) or []))
        chars = [(u, l) for u, l, k in triples if k == "character"]
        props = [(u, l) for u, l, k in triples if k not in ("character", "location")]
        # ★ 用**表列原值** `scene_col`：`relations.plan_frames` 会用标题派生的场景名
        #   覆写 `scene`（本集标题是「第 1 场：断剑坪·钟坠（云海之上）」，覆写后
        #   与资产卡名「断剑坪·坪内」对不上 ⇒ 六镜全部拿不到场景图，实测踩过）。
        loc_url, loc_info = scene_location_url(
            assets, reg, s.get("scene_col") or s.get("scene") or "", log)

        # 本臂图序：人物设定表（≤2，双人戏两张脸）→ 场景空镜 → 道具，封顶 `ARM_MAX_IMAGES`。
        # 不放开到 5：10-10-06 桥上决斗在**视频通道**实测「图数 > 分镜人数 ⇒ 多画一个人」
        # （cap=5 画三人、cap=3 全对）。本集每镜 1 人，3 张已是人数+2，属已知风险档。
        picks, roles = [], []
        for u, l in chars[:ARM_MAX_CHARS]:
            if u:
                picks.append(u); roles.append(("character", "角色「%s」的人物设定表" % l))
        if loc_url and len(picks) < ARM_MAX_IMAGES:
            picks.append(loc_url)
            roles.append(("location", "场景「%s」的空镜（只锁建筑与地貌，不锁机位）" % loc_info))
        for u, l in (props if ARM_PROPS else []):
            if len(picks) < ARM_MAX_IMAGES and u and u not in picks:
                picks.append(u); roles.append(("prop", "道具「%s」" % l))
        rows.append({"name": nm, "seconds": float(s.get("seconds") or 8),
                     # 两个都记：`scene` 被关系层按标题覆写过，`scene_col` 才是表列原值
                     "scene_col": s.get("scene_col") or "", "scene_derived": s.get("scene") or "",
                     "scene_lookup": loc_info,
                     "images": picks, "roles": roles,
                     "prompt_uses_still": False,
                     "all_bound": [{"url": u, "name": l, "kind": k} for u, l, k in triples]})

    # 提示词：与基线同一函数同一模式，只替换开头那句素材用途声明
    for s, r in zip(shots, rows):
        base = prompt_mod.build_video_prompt(s, None, mode="reference")
        decl = prompt_mod.pack_ref_declaration(r["roles"])
        if any(k == "prop" for k, _l in r["roles"]):
            decl += " 道具的形制、颜色与细节一律以对应参考图为准。"
        old_decl = prompt_mod.REF_USAGE_ZH
        new = base.replace(old_decl, decl)
        r["prompt"] = new
        r["decl_swapped"] = (new != base)
        r["same_len_minus_decl"] = len(new) - len(base)

    # ★★ 本臂的**定义**就是"没有静帧"，所以这条必须当场验，不能靠我相信：
    #    把基线那 15 张静帧的 URL 拿来，逐条比对实发图清单，命中即终止。
    still_fp = SRC / "media" / ("ep%d" % EP) / "stills.json"
    still_urls = set()
    if still_fp.exists():
        for v in json.loads(still_fp.read_text(encoding="utf-8")).values():
            if isinstance(v, dict) and v.get("url"):
                still_urls.add(v["url"])
    leaked = [r["name"] for r in rows if set(r["images"]) & still_urls]
    empty = [r["name"] for r in rows if not r["images"]]
    log("[arm] 静帧 URL 共 %d 条；本臂实发图里是否混入静帧：%s"
        % (len(still_urls), "泄漏 %s" % leaked if leaked else "没有（0 条）"))
    if leaked:
        raise SystemExit("[arm] !! 本臂定义被破坏（图清单里出现了静帧），不提交")

    (DST / "media" / ("ep%d" % EP) / MANIFEST).write_text(
        json.dumps({"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "aspect_ratio": config.ASPECT_RATIO,
                    "video_mode": config.VIDEO_MODE,
                    "video_model": config.MODELS.get("video"),
                    "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    (DST / "media" / ("ep%d" % EP) / PROMPTS).write_text(
        json.dumps({r["name"]: r["prompt"] for r in rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    log("[arm] 冻结完成：%d 镜；图张数分布 %s"
        % (len(rows), {n: sum(1 for r in rows if len(r["images"]) == n)
                       for n in sorted({len(r["images"]) for r in rows})}))
    log("[arm] 声明替换是否生效（必须全 True）：%s"
        % all(r["decl_swapped"] for r in rows))
    if empty:
        log("[arm] !! 无图可发的镜（这些镜只能靠文字，会被跳过）：%s" % ",".join(empty))
    no_scene = [r["name"] for r in rows
                if not any(k == "location" for k, _l in r["roles"])]
    if no_scene:
        log("[arm] 没拿到场景图的镜：%s" % ",".join(no_scene))
    for r in rows:
        log("[arm]   %s  %s" % (r["name"],
                                 " + ".join("%s(%s)" % (k, l) for k, l in r["roles"])
                                 or "无图"))
    log("[arm] 清单：%s" % (DST / "media" / ("ep%d" % EP) / MANIFEST))
    return rows


def load_frozen():
    p = DST / ("media/ep%d/" % EP) / MANIFEST
    return json.loads(p.read_text(encoding="utf-8"))


def submit_and_wait(rows, todo, log=print, per_call_wait: bool = True):
    """提交 + 轮询落盘。key 与配速沿用生产的 keypool。"""
    from v5 import config
    from v5.media import keypool, providers, video

    clip_dir = DST / "media" / ("ep%d" % EP) / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    pool = keypool.KeyPool.of()
    log("[arm] 提交配速：%d 条 key × %s；提交读超时 %ds"
        % (len(pool), pool.pacing(), config.VIDEO_SUBMIT_TIMEOUT))
    got = {}
    ids = {}
    for r in rows:
        if r["name"] not in todo:
            continue
        for q in range(config.VIDEO_QUEUE_RETRIES + 1):
            idx, key = pool.claim()
            try:
                res = providers.submit_video(r["prompt"], mode="reference",
                                             images=r["images"],
                                             seconds=int(r["seconds"]),
                                             aspect_ratio=config.ASPECT_RATIO,
                                             key=key, timeout=180)
                vid = res.get("video_id") or res.get("task_id")
                ids[r["name"]] = (vid, key)
                log("[arm] %s submitted（images=%d %s，%ds，%s）"
                    % (r["name"], len(r["images"]),
                       "、".join(k for k, _l in r["roles"]), r["seconds"],
                       pool.label(idx)))
                break
            except providers.QueueFullError:
                pool.note_rate_limited(idx)
                w = 20 * (q + 1)
                log("[arm] %s 队列满，%ds 后重试（%d/%d）"
                    % (r["name"], w, q + 1, config.VIDEO_QUEUE_RETRIES + 1))
                time.sleep(w)
            except providers.RateLimitError:
                pool.note_rate_limited(idx)
                log("[arm] %s 429（%s 拉黑），换 key 重试" % (r["name"], pool.label(idx)))
                time.sleep(6)
            except Exception as e:  # noqa: BLE001
                log("[arm] %s 提交失败：%s" % (r["name"], str(e)[:160]))
                break
        if per_call_wait:
            time.sleep(config.VIDEO_SUBMIT_MIN_INTERVAL_S)
    # 轮询
    for name, (vid, key) in list(ids.items()):
        dest = clip_dir / (name + ".mp4")
        local = video._wait_one(vid, dest, log=log, key=key)
        if local:
            got[name] = local
            ids.pop(name, None)
    log("[arm] 本轮落盘 %d/%d" % (len(got), len(todo)))
    return got


def main() -> int:
    global SRC, DST, EP
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=["freeze", "probe", "rest"])
    ap.add_argument("--src", default="madfate-abc-1005-reference",
                    help="输入来源项目（分镜/资产卡/资产图从它读）")
    ap.add_argument("--dst", default="",
                    help="本臂自己的项目目录名（默认 <src>-nostill）")
    ap.add_argument("--ep", type=int, default=1)
    ap.add_argument("--cap", type=int, default=3,
                    help="一条请求最多几张图。仙侠这类**定妆照已带兵器**的包给 3 但关道具；"
                         "默认 3（1 人镜 = 脸 + 场景 + 道具）")
    ap.add_argument("--no-props", action="store_true",
                    help="不单独喂道具图（定妆照/场景卡里已经画了同一件兵器时，"
                         "再给一张是重复供给 —— 10-06 桥上决斗实测）")
    args = ap.parse_args()

    SRC = ROOT / "projects" / args.src
    DST = ROOT / "projects" / (args.dst or (args.src + "-nostill"))
    EP = args.ep
    globals()["ARM_MAX_IMAGES"] = args.cap
    globals()["ARM_PROPS"] = not args.no_props

    log = lambda m: (print(m, flush=True))
    log("[arm] src=%s dst=%s ep=%d cap=%d props=%s"
        % (SRC.name, DST.name, EP, ARM_MAX_IMAGES, ARM_PROPS))
    setup_dir(log)
    rows = build_inputs(log) if args.phase == "freeze" else load_frozen()["rows"]

    if args.phase == "freeze":
        return 0
    if args.phase == "probe":
        names = ["LN01"]
    else:
        names = [r["name"] for r in rows if r["name"] != "LN01"]
    got = submit_and_wait(rows, set(names), log=log)
    for n, p in sorted(got.items()):
        log("[arm] OK %s -> %s" % (n, p))
    miss = [n for n in names if n not in got]
    if miss:
        log("[arm] !! 未落盘：%s" % ",".join(miss))
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
