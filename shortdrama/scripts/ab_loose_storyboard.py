# -*- coding: utf-8 -*-
"""A/B：**把创作约束从分镜师身上摘掉，会交出什么**（2026-10-02）。

用户的要求：除「一次生成 4–12 秒」「一次最多 5 张参考图」这两条供应商硬墙之外，
节奏/镜长/景别/切镜密度全部交回类型包 SKILL 与分镜自己，**不限制也不建议**。

本脚本把这件事做成**一次可比的对读**，而不是永久删契约——那些硬性要求里有多条是
拿事故换来的（锚点断链漂红、道具名漂移一张图没绑、图比人多会多画一个人）。

    A 臂（tight） 现行：`role_input` 注入 8 条硬性要求 + 出片容器事实 + 程序体检
    B 臂（loose） `SHORTDRAMA_LOOSE_STORYBOARD=1`：上面这些**一条都不给**

两臂**只差这一个开关**：同一个项目、同一份上游（世界观/剧本/对白/brief 全文照注）、
同一个包的分镜 SKILL、同一个模型。搬运纪律（产物路径、台词照抄、音频模式）两臂都留
——放开它们就不是同一部戏了，比不出东西。

用法：
    python scripts/ab_loose_storyboard.py --project luanzhen-xue-1001 --ep 1
产物（全部落 tmp/AB/，**不写 projects/**）：
    tmp/AB/<项目>_ep<N>_<臂>.md   两臂的分镜表原文
    终端打印两臂的镜数/时长/密度/宽景/台词对照
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v5 import config, llm, roles                                        # noqa: E402
from v5.media import storyboard, video_plan                              # noqa: E402

WIDE = ("全景", "远景", "大全景", "空镜")

#: 「（无声，环境音）」「无台词」这类是**无声标记**，不算有台词的镜。
SILENT = re.compile(r"^\s*[（(]?\s*(无台词|无声|无对白|无)\s*[^)）]*[)）]?\s*$")


def has_speech(shot: dict) -> bool:
    d = (shot.get("dialogue") or "").strip()
    return bool(d) and not SILENT.match(d) and len(d) > 4


def gen(root: Path, ep: int, loose: bool, max_tokens: int = 16000) -> str:
    """跑一次分镜：system = 包 SKILL，user = role_input（按开关决定注入什么）。"""
    pack = (json.loads((root / "brief.json").read_text(encoding="utf-8"))
            .get("pack") or "shortdrama")
    sys_p = roles.role_system_prompt(pack, "scenedesigner", ep)
    old = config.LOOSE_STORYBOARD
    config.LOOSE_STORYBOARD = loose
    try:
        usr = roles.role_input("scenedesigner", root, {"episode_index": ep})
        msg = llm.role_chat("scenedesigner", max_tokens).invoke(
            [("system", sys_p), ("user", usr)])
    finally:
        config.LOOSE_STORYBOARD = old
    return msg.content if hasattr(msg, "content") else str(msg)


def stats(md: str) -> dict:
    shots = storyboard.parse(md)
    secs = [video_plan.pack_clamp_sec(s) for s in shots]
    beats = [len(storyboard.split_beats(s.get("visual") or "")) for s in shots]
    dial = [s for s in shots if has_speech(s)]
    chars = sum(len("".join(c for c in (s.get("dialogue") or "") if "\u4e00" <= c <= "鿿"))
                for s in shots)
    return {"shots": len(shots), "sec_total": sum(secs),
            "sec_min": min(secs) if secs else 0, "sec_max": max(secs) if secs else 0,
            "sec_med": statistics.median(secs) if secs else 0,
            "uniform": len(set(secs)) == 1 and len(secs) >= 4,
            "beats": sum(beats),
            "beat_per_sec": (sum(beats) / sum(secs)) if secs and sum(secs) else 0,
            "wide": sum(1 for s in shots if any(w in (s.get("shot_type") or "") for w in WIDE)),
            "dial_shots": len(dial), "dial_chars": chars,
            "scenes": len({(s.get("scene") or "").strip() for s in shots if s.get("scene")}),
            "no_beat_shots": sum(1 for b in beats if b == 0),
            "rows": shots}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="luanzhen-xue-1001")
    ap.add_argument("--ep", type=int, default=1)
    ap.add_argument("--arms", default="tight,loose")
    ap.add_argument("--out", default="tmp/AB")
    a = ap.parse_args()

    root = ROOT / "projects" / a.project
    if not (root / "brief.json").exists():
        raise SystemExit("项目不存在：%s" % root)
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)

    res = {}
    for arm in [x.strip() for x in a.arms.split(",") if x.strip()]:
        loose = arm == "loose"
        print("[ab] %s 臂生成中（loose=%s）…" % (arm, loose), flush=True)
        md = gen(root, a.ep, loose)
        p = out / ("%s_ep%d_%s.md" % (a.project, a.ep, arm))
        p.write_text(md, encoding="utf-8")
        st = stats(md)
        res[arm] = st
        print("[ab] %s → %s（解析出 %d 镜）" % (arm, p.relative_to(ROOT), st["shots"]),
              flush=True)
        if st["shots"] == 0:
            print("[ab] !! 一镜都没解析出来 —— 这一臂的产物不可比，看原文：", flush=True)
            print(md[:600])

    keys = [("shots", "镜数"), ("sec_total", "总秒数"), ("sec_min", "最短镜"),
            ("sec_max", "最长镜"), ("sec_med", "中位镜长"), ("beats", "节拍数"),
            ("beat_per_sec", "拍/秒"), ("wide", "宽景镜数"), ("scenes", "场景数"),
            ("dial_shots", "有台词镜"), ("dial_chars", "台词总字"),
            ("no_beat_shots", "无节拍镜")]
    print("\n| 指标 | A 现行（有约束） | B 放开（无约束） |")
    print("|---|---|---|")
    for k, lab in keys:
        f = lambda v: ("%.2f" % v) if isinstance(v, float) else str(v)
        print("| %s | %s | %s |" % (lab, f(res.get("tight", {}).get(k, "—")),
                                    f(res.get("loose", {}).get(k, "—"))))
    if "tight" in res and "loose" in res:
        t, l = res["tight"], res["loose"]
        print("\n[ab] 每镜秒数  A=%s  B=%s" % (
            [video_plan.pack_clamp_sec(s) for s in t["rows"]][:20],
            [video_plan.pack_clamp_sec(s) for s in l["rows"]][:20]))
        gt = video_plan.group_shots(t["rows"])
        gl = video_plan.group_shots(l["rows"])
        print("[ab] 分组（≤12s/≤%d 镜） A=%d 组 %s  B=%d 组 %s"
              % (config.VIDEO_PACK_MAX_GROUP, len(gt),
                 [len(g) for g, _ in gt], len(gl), [len(g) for g, _ in gl]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
