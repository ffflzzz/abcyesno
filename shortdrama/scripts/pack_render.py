# -*- coding: utf-8 -*-
"""12s 打包渲染（管线外验证）：把 ep 相邻分镜打包成 ≤12s 的 reference 请求。

背景（2026-09-21 用户提案 + LN01-03 实验成功）：逐镜 reference 时模型不知道
相邻镜存在 → 镜间断开。把相邻镜打包成一条 ≤12s 请求（每镜一张静帧、逐拍
<Picture i> 点名），接戏从"跨请求问题"变成"单请求内部问题"。

本脚本**不改 v5 管线**：读盘上分镜与静帧 → 分组 → 提交 → 轮询落盘。

分组规则（v2 压缩式）：
- 仅**同场景**相邻镜合并（跨场景切换是分镜语义，不交给模型即兴）；
- 声明时长之和 ≤12s 直接合并；
- 超限时**等比压缩**到 12s，但每镜不得低于 `speech_need`（台词字数/5 + 1s，
  无声镜 4s），且压幅不得超原声明 40%——否则放弃合并、该镜独立成组。

用法：
    python pack_render.py <project> --ep N [--dry-run] [--max-group 5]
产物：
    projects/<p>/media/ep{N}_pack/pack{k}_{first}-{last}.mp4
    projects/<p>/media/ep{N}_pack/packs.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

ROOT = Path(r"C:/Users/Administrator/Downloads/abcyesno-v8/abcyesno-v8/abcyesno-v8/shortdrama")
sys.path.insert(0, str(ROOT))

from v5 import config                                   # noqa: E402
from v5.media import providers                          # noqa: E402
from v5.media import style as style_mod                 # noqa: E402
from v5.media.storyboard import parse                   # noqa: E402
from v5.media.video import _wait_one                    # noqa: E402

SUBMIT_GAP_S = int(getattr(config, "VIDEO_SUBMIT_MIN_INTERVAL_S", 65))
MAX_SECONDS = 12
MIN_KEEP_RATIO = 0.6     # 压缩后每镜至少保留原声明的 60%


def clamp_sec(s: dict) -> int:
    """分镜声明时长：解析失败按 4s 兜底，硬区间 4-12。"""
    v = int(s.get("seconds") or 0) or 4
    return max(4, min(12, v))


def speech_need(s: dict) -> int:
    """镜最短可行秒数：台词语音（5 字/秒）+ 1s 余量；无声镜 4s。"""
    chars = len(re.sub(r"[^一-龥]", "", s.get("dialogue") or ""))
    need = chars / 5.0 + 1.0 if chars else 2.0
    return max(4, math.ceil(need))


def _fit(declared: list[int], mins: list[int]) -> list[int] | None:
    """把 declared 等比压到 ≤12s；保每镜 ≥mins 且 ≥60% 原声明。失败返回 None。"""
    total = sum(declared)
    if total <= MAX_SECONDS:
        return declared
    scaled = [d * MAX_SECONDS / total for d in declared]
    out = [max(m, round(x)) for x, m in zip(scaled, mins)]
    while sum(out) > MAX_SECONDS:
        idx = max(range(len(out)), key=lambda k: out[k] - mins[k])
        if out[idx] - 1 < mins[idx] or out[idx] - 1 < declared[idx] * MIN_KEEP_RATIO:
            return None
        out[idx] -= 1
    if any(o < declared[idx] * MIN_KEEP_RATIO for idx, o in enumerate(out)):
        return None
    return out if sum(out) <= MAX_SECONDS else None


def group_shots(shots: list[dict], max_group: int) -> list[tuple[list[dict], list[int]]]:
    """同场景相邻镜贪心分组，返回 (镜列表, 每镜声明秒) 。"""
    groups: list[tuple[list[dict], list[int]]] = []
    i, n = 0, len(shots)
    while i < n:
        cur = [shots[i]]
        declared = [clamp_sec(shots[i])]
        while len(cur) < max_group and i + len(cur) < n:
            nxt = shots[i + len(cur)]
            sc_cur = (cur[-1].get("scene") or "").strip()
            sc_nxt = (nxt.get("scene") or "").strip()
            if not sc_cur or sc_cur != sc_nxt:
                break
            trial_d = declared + [clamp_sec(nxt)]
            mins = [speech_need(s) for s in cur + [nxt]]
            fitted = _fit(trial_d, mins)
            if sum(trial_d) <= MAX_SECONDS:
                cur.append(nxt)
                declared = trial_d
                continue
            if fitted:
                cur.append(nxt)
                declared = fitted
                break        # 压缩组 12s 已满，不再吞镜
            break
        groups.append((cur, declared))
        i += len(cur)
    return groups


def fmt_dialogue(d: str) -> str:
    d = (d or "").strip()
    if not d or "无声" in d:
        return "无台词（环境音）"
    return d


def build_prompt(group: list[dict], declared: list[int], total: int,
                 style_block: str = "") -> str:
    """打包 prompt：参考图逐拍点名 + 时间段边界 + 逐拍完整内容。

    `style_block` = 项目风格块（与 v5.media.prompt.build_pack_prompt 同步，
    2026-09-22：替换硬编码「国风古装」句，题材污染；缺省回退通用实拍句）。
    """
    n = len(group)
    bounds, maps = [], []
    left = 0
    for i, s in enumerate(group):
        right = left + declared[i]
        maps.append("<Picture %d> 为第 %d-%d 秒节拍的画面参考" % (i + 1, left, right))
        bounds.append((left, right))
        left = right
    segs = [
        "、".join(maps)
        + "；共 %d 张参考图对应同一条 %d 秒片段的 %d 个节拍，"
          "人物、服装、道具与场景一律以对应参考图为准。" % (n, total, n),
        "本片段总长 %d 秒，由连续发生的 %d 个节拍组成，各节拍按下列时间分配自然衔接，"
        "节拍边界允许 ±1 秒弹性：" % (total, n),
    ]
    for (l, r), s in zip(bounds, group):
        scene = (s.get("scene") or "").strip()
        head = "【第 %d-%d 秒" % (l, r)
        if scene:
            head += "｜%s" % scene
        head += "｜%s·%s·%s】" % (s.get("shot_type"), s.get("angle"), s.get("camera"))
        join = (s.get("join_note") or "").strip()
        join_line = "\n转场承接：%s。" % join if join else ""
        style = (s.get("visual_style") or "").strip()
        style_line = "\n视觉风格：%s" % style if style else ""
        segs.append(
            "%s\n%s%s%s\n台词：%s\n音效：%s\n落幅：%s"
            % (head, (s.get("visual") or "").strip(), join_line, style_line,
               fmt_dialogue(s.get("dialogue")),
               (s.get("sfx") or "").strip() or "无",
               (s.get("tail") or "").strip() or "自然收在该拍动作结束处"))
    if style_block:
        segs.append(
            style_block.rstrip("。 ")
            + "。这是同一条连续素材，"
            "各节拍光线与色调随场景自然过渡，转场干脆利落，人物造型跨节拍完全一致。")
    else:
        segs.append(
            "画面风格：电影级实拍剧照质感；这是同一条连续素材，"
            "各节拍光线与色调随场景自然过渡，转场干脆利落，人物造型跨节拍完全一致。")
    segs.append("全片不得出现任何文字、字幕、水印；不得分屏；竖屏构图。")
    return "\n\n".join(segs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ep", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-group", type=int, default=5)
    a = ap.parse_args()

    root = config.PROJECTS_DIR / a.project
    sb = root / "scenedesigner" / ("scenedesigner_ep%d.md" % a.ep)
    shots = parse(sb.read_text(encoding="utf-8"))
    if not shots:
        raise SystemExit("分镜解析为空：%s" % sb)

    stills: dict = {}
    sp = root / "media" / ("ep%d" % a.ep) / "stills.json"
    if sp.exists():
        stills = json.loads(sp.read_text(encoding="utf-8"))
    missing = [s["name"] for s in shots if not (stills.get(s["name"]) or {}).get("url")]
    if missing and not a.dry_run:
        raise SystemExit("静帧缺失：%s" % missing)

    groups = group_shots(shots, a.max_group)
    out_dir = root / "media" / ("ep%d_pack" % a.ep)
    report = []
    print("[pack] 共 %d 镜 → %d 组" % (len(shots), len(groups)))
    for k, (g, declared) in enumerate(groups, 1):
        total = sum(declared)
        names = [s["name"] for s in g]
        tag = "+".join("%s(%ds)" % (nm, d) for nm, d in zip(names, declared))
        dest = out_dir / ("pack%02d_%s-%s.mp4" % (k, names[0], names[-1]))
        entry = {"pack": k, "shots": names, "declared_seconds": declared,
                 "total": total, "dest": str(dest), "status": "pending"}
        if a.dry_run:
            print("  pack%02d %s 合计%ds" % (k, tag, total))
        elif dest.exists() and dest.stat().st_size > 0:
            print("[pack%02d] 已存在跳过：%s" % (k, dest.name))
            entry["status"] = "skipped"
        else:
            urls = [(stills[s["name"]] or {}).get("url") for s in g]
            prompt = build_prompt(g, declared, total,
                                  style_block=style_mod.wrap(style_mod.load(root)))
            print("[pack%02d] %s 合计%ds prompt=%d字 images=%d 提交中…"
                  % (k, tag, total, len(prompt), len(urls)))
            r = None
            for attempt in range(1, 6):
                try:
                    r = providers.submit_video(prompt, mode="reference",
                                               images=[u for u in urls if u],
                                               seconds=total, aspect_ratio=config.ASPECT_RATIO)
                    break
                except providers.QueueFullError:
                    wait_s = 60 * attempt
                    print("[pack%02d] 队列满，%ds 后重试（第 %d/5 次）" % (k, wait_s, attempt))
                    time.sleep(wait_s)
            if r is None:
                print("[pack%02d] 队列满重试 5 次仍失败" % k)
                entry["status"] = "failed"
                report.append(entry)
                if k < len(groups):
                    time.sleep(SUBMIT_GAP_S)
                continue
            vid = r.get("video_id") or r.get("task_id")
            print("[pack%02d] submitted id=%s" % (k, vid))
            t0 = time.time()
            local = _wait_one(vid, dest, rounds=90, interval=10,
                              log=lambda m: print(m))
            if local:
                print("[pack%02d] done in %.0fs" % (k, time.time() - t0))
                entry["status"] = "ok"
            else:
                print("[pack%02d] 轮询超窗/失败" % k)
                entry["status"] = "failed"
            if k < len(groups):
                time.sleep(SUBMIT_GAP_S)   # per-key 1rpm 闸门
        report.append(entry)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "packs.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for e in report if e["status"] in ("ok", "skipped"))
    print("RESULT:", json.dumps({"status": "ok" if ok == len(report) else "partial",
                                 "ok": ok, "total_groups": len(report),
                                 "report": str(out_dir / "packs.json")},
                                ensure_ascii=False))
    return 0 if ok == len(report) else 1


if __name__ == "__main__":
    sys.exit(main())
