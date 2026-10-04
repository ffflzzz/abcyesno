# -*- coding: utf-8 -*-
"""三臂对照脚手架：把一个母项目的**第 N 集**克隆成若干臂，只换视频档位，别的输入一字不变。

为什么必须克隆而不是原地换档重渲：`video.submit_all` 见 `video_jobs.json` 里
state=completed 且 clip 在盘就跳过提交。同一目录里先跑 reference 再跑 mixed，
mixed 会**整批复用 reference 的成片**，日志全绿、三臂其实是同一片（假对照）。

为什么三臂共用同一份静帧：档位比的是"同样的画面怎么下单"，静帧一变就变成
比两轮 LLM 方差。所以克隆**保留** stills.json + stills/*.jpg（并把条目里的
绝对 path 改指向本臂目录），**剥掉**全部渲染层记账与产物。

配套的两个 QC 开关由调用方带（见 README 段尾）：
`SHORTDRAMA_STILL_QC=0` 防某一臂把对照用的静帧就地重画掉；
`SHORTDRAMA_CLIP_QC=0` 防 reference/mixed 臂享有 pack 臂拿不到的成片自愈重拍。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 渲染层产物与记账——克隆时剥掉（母项目里可以留着，臂里必须没有）
DROP_DIRS = ("clips", ".rerun_backup", "tail_frames")
DROP_NAMES = ("video_jobs.json", "tails.json", "seam_preview.jpg",
              "still_qc_seen.json", "still_requeue_tally.json",
              "clip_qc_seen.json", "gates.json", "approvals.json",
              "episode_final.mp4", "episode_final_narr.mp4")


def clone(master: str, arm: str, ep: int) -> dict:
    src = ROOT / "projects" / master
    dst = ROOT / "projects" / arm
    if not (src / "brief.json").exists():
        raise SystemExit("母项目不存在或没有 brief.json：%s" % src)
    if dst.exists():
        raise SystemExit("臂目录已存在（要重来请先删它）：%s" % dst)

    ignore = shutil.copytree(str(src), str(dst),
                             ignore=shutil.ignore_patterns(
                                 "__pycache__", ".tmp", "*.log",
                                 "clips", ".rerun_backup"),
                             symlinks=False)
    # copytree 的返回值是目标目录（3.8+），ignore 只处理目录名——文件级剥下面
    dropped = []
    for f in sorted(dst.rglob("*")):
        if not f.is_file():
            continue
        if f.parent.name in DROP_DIRS:
            f.unlink()
            dropped.append(str(f.relative_to(dst)))
        elif f.parent.match("media/ep*") and (f.name in DROP_NAMES
                                              or f.name.startswith("episode_final")):
            f.unlink()
            dropped.append(str(f.relative_to(dst)))
    for d in list(dst.rglob("media/ep*")) + list(dst.rglob("clips")):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()

    # 静帧记录里的绝对 path 改指向本臂（url 是远端地址、三臂天然同一个，不动）
    fixed = 0
    sj = dst / "media" / ("ep%d" % ep) / "stills.json"
    if sj.exists():
        data = json.loads(sj.read_text(encoding="utf-8"))
        for name, rec in (data.items() if isinstance(data, dict) else []):
            if not isinstance(rec, dict) or not rec.get("path"):
                continue
            p = Path(str(rec["path"]))
            newp = dst / "media" / ("ep%d" % ep) / "stills" / p.name
            if newp.exists() and str(rec["path"]) != str(newp):
                rec["path"] = str(newp)
                fixed += 1
        sj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"arm": arm, "dropped": dropped, "paths_fixed": fixed,
            "stills": len(list((dst / "media" / ("ep%d" % ep) / "stills").glob("*.jpg")))
            if (dst / "media" / ("ep%d" % ep) / "stills").exists() else 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("master")
    ap.add_argument("--arms", default="reference,pack,mixed")
    ap.add_argument("--ep", type=int, default=1)
    a = ap.parse_args()
    for mode in [s.strip() for s in a.arms.split(",") if s.strip()]:
        r = clone(a.master, "%s-%s" % (a.master, mode), a.ep)
        print("[ab_arm] 臂 %s：静帧 %d 张（path 改写 %d 条），剥掉渲染层 %d 项"
              % (r["arm"], r["stills"], r["paths_fixed"], len(r["dropped"])))
        if not r["stills"]:
            print("[ab_arm] ⚠️ 该臂没有静帧——母项目先跑 --stills-only 或 --resume-media 出静帧")
    return 0


if __name__ == "__main__":
    sys.exit(main())
