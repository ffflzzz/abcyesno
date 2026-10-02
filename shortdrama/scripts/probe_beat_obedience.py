# -*- coding: utf-8 -*-
"""探针：**逐拍点名**——提示词里写的每一拍，画面上到底有没有出现。

要回答的是用户那句"之前不是测过模型能逐秒安排画面吗？"——那条已经成立，本次不再测。
本次测的是**下一层**：把节拍排得更密，画面会不会**多演出**那么多拍。
上一轮（`scripts/probe_beat_density.py`）用"帧间像素差"量，读数被同提示词重跑的噪声
地板吃掉（HALF−PROD 0.48 vs 地板 6.08）——那把尺子只看画面动没动，看不见
"你叫抬的那只手抬没抬"。所以这次换成**语义判定**：把成片按 0.25 秒抽帧，连同这一镜的
节拍清单交给视觉模型，让它逐拍回答"出现没有 + 抄出画面依据"。

四臂（同一场戏、同一批参考图、同一总秒数，只差时间切分）：

    PROD    盘上分镜原文，5 拍（0.45 拍/秒）
    PROSE   同一批子句、**没有时间戳**          反向臂（只问有没有演到，不问第几秒）
    HALF    同一批子句、每句一个 0.33–0.5 秒时间戳（29 拍）
    REPEAT  与 PROD 同一份文本重跑              噪声地板（也含"判定本身稳不稳"）

判据（⛔ 抄不出原文的"命中"不算命中）：
· 每条命中必须给出**依据帧的时间戳**并**抄出该帧里看到的东西**；
· 依据帧要落在这一拍的窗口内（±0.5 秒），窗外一律降级为"不计"并写明原因；
· **按镜分次判**（一次调用只带这一镜的帧）——整片 47 张一起送既超单次上限，
  也只会换来一个"大概都演了"；
· 每臂判**两轮**，两轮不一致的拍单独列出——同一张图连审会翻判是本项目记过的病。

用法：
    python scripts/probe_beat_obedience.py --dry-run     # 只看每臂几拍、几镜、怎么切窗口
    python scripts/probe_beat_obedience.py               # 真跑（吃视觉额度）
产物：tmp/obedience/<臂>_round<N>.json + 终端对照表。**不写 projects/。**
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROBE = ROOT / "scripts" / "probe_beat_density.py"
FRAME_STEP = 0.25          # 抽帧步长（秒）：要分辨 0.33 秒的拍，至少得比它细
TOL = 0.5                  # 依据帧落在拍窗口内允许的容差（秒）

JUDGE = """你在做**逐拍核对**。下面是一段视频里**某一个镜头**按 {step} 秒抽出的帧，
每帧前面标了它的时间戳；后面是这个镜头的节拍清单（编号是**全局拍号**，请原样回填）。

对每一条节拍，回答它有没有在画面里出现：
- 命中：给出**依据帧的时间戳**，并**抄出你在该帧里看到的东西**（谁、做了什么、在画面
  哪里）。抄不出来就不要判命中。
- 未命中：hit=false，quote 留空。
- 依据帧要落在该拍声明的时间窗内（前后允许 {tol} 秒）。窗外判命中无效。

只输出 JSON 数组，每项形如：
{{"beat": 7, "hit": true, "frame": "4.25", "quote": "周娘子侧身站在绣屏前，右手指向屏面"}}

节拍清单：
{beats}
"""


def data_uri(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


def grab_frames(clip: Path, out: Path, lo: float, hi: float) -> list[tuple[float, Path]]:
    out.mkdir(parents=True, exist_ok=True)
    got: list[tuple[float, Path]] = []
    t = lo
    while t < hi - 1e-6:
        p = out / ("f%05.2f.jpg" % t)
        if not p.exists():
            r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(clip),
                                "-frames:v", "1", "-vf", "scale=336:-1", "-q:v", "3",
                                str(p)], capture_output=True)
            if r.returncode != 0 or not p.exists():
                t = round(t + FRAME_STEP, 2)
                continue
        got.append((t, p))
        t = round(t + FRAME_STEP, 2)
    return got


def beats_of(arm: str, project: str, ep: int):
    """复用密度探针的分组与改写 ⇒ 节拍清单与当时送给模型的**逐字相同**。

    返回 `(beats, bounds)`：
      beats  = [{"no","shot","lo","hi","win","text"}]，`no` 是全局拍号
      bounds = [(镜名, 起秒, 止秒)]，按组内顺序
    """
    spec = importlib.util.spec_from_file_location("pbd", PROBE)
    pbd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pbd)
    from v5.media import storyboard

    root = ROOT / "projects" / project
    md = root / "scenedesigner" / ("scenedesigner_ep%d.md" % ep)
    by = {s["name"]: s for s in storyboard.parse(md.read_text(encoding="utf-8"))}
    rec = json.loads((root / "media" / ("ep%d" % ep) / "video_jobs.json")
                     .read_text(encoding="utf-8"))
    rec = rec.get("jobs", rec)
    names = rec["pack01"]["shots"]
    declared = rec["pack01"]["declared_seconds"]
    group = [by[n] for n in names]
    new = pbd.rewrite(group, declared, arm)

    beats: list[dict] = []
    bounds: list[tuple[str, float, float]] = []
    left = 0.0
    for nm, s, span in zip(names, new, declared):
        bounds.append((nm, left, left + span))
        if arm == "PROSE":
            # 无时间戳臂：`split_beats` 拿不到东西（它本来就没有标记），
            # 清单改用**子句**，窗口=全片任意时刻（只问"有没有演到"）。
            items = [(-1.0, 1e6, c) for c in pbd._clauses(s.get("visual") or "")]
        else:
            items = [(left + float(a), left + float(b), body)
                     for a, b, body in storyboard.split_beats(s.get("visual") or "")]
        for a, b, body in items:
            beats.append({"no": len(beats) + 1, "shot": nm,
                          "lo": a - TOL, "hi": b + TOL,
                          "win": "全片任意时刻" if arm == "PROSE"
                          else "%.2f-%.2f" % (a, b),
                          "text": body})
        left += span
    return beats, bounds


def judge(beats: list[dict], frames: list[tuple[float, Path]]) -> list[dict]:
    from langchain_core.messages import HumanMessage

    from v5 import llm

    listing = "\n".join("%d）[%s] %s" % (b["no"], b["win"], b["text"]) for b in beats)
    parts: list[dict] = [{"type": "text",
                          "text": "帧清单：" + "、".join("%.2fs" % t for t, _ in frames)}]
    for t, p in frames:
        parts.append({"type": "text", "text": "以下是 %.2f 秒的帧：" % t})
        parts.append({"type": "image_url", "image_url": {"url": data_uri(p)}})
    parts.append({"type": "text",
                  "text": JUDGE.format(step=FRAME_STEP, tol=TOL, beats=listing)})
    r = llm.chat_for("", 3000, temperature=0).invoke([HumanMessage(content=parts)])
    txt = r.content if hasattr(r, "content") else str(r)
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        raise RuntimeError("判定不是 JSON 数组：" + txt[:200])
    return json.loads(m.group(0))


def score(beats: list[dict], verdicts: list[dict], frame_ts: list[float]):
    """程序核验：抄不出依据、或依据帧不在拍窗口内 ⇒ 不算命中。"""
    by_no: dict[int, dict] = {}
    for v in verdicts:
        try:
            by_no[int(v.get("beat"))] = v
        except (TypeError, ValueError):
            continue
    hits, rejected = 0, []
    for b in beats:
        v = by_no.get(b["no"])
        if not v or not v.get("hit"):
            rejected.append((b["no"], b["win"], b["text"][:26], "判未命中"))
            continue
        quote = str(v.get("quote") or "").strip()
        if len(quote) < 4:
            rejected.append((b["no"], b["win"], b["text"][:26], "命中但抄不出依据"))
            continue
        try:
            ft = float(v.get("frame"))
        except (TypeError, ValueError):
            rejected.append((b["no"], b["win"], b["text"][:26], "没给依据帧时间戳"))
            continue
        if not frame_ts or min(abs(x - ft) for x in frame_ts) > FRAME_STEP:
            rejected.append((b["no"], b["win"], b["text"][:26],
                             "依据帧 %.2fs 不是抽出来的帧" % ft))
            continue
        if not (b["lo"] <= ft <= b["hi"]):
            rejected.append((b["no"], b["win"], b["text"][:26],
                             "依据帧 %.2fs 不在窗口 %s 内" % (ft, b["win"])))
            continue
        hits += 1
    return hits, rejected


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="luanzhen-xue-1001")
    ap.add_argument("--ep", type=int, default=1)
    ap.add_argument("--arms", default="PROD,PROSE,HALF,REPEAT")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    out = ROOT / "tmp" / "obedience"
    out.mkdir(parents=True, exist_ok=True)
    plan = {}
    for arm in [x.strip().upper() for x in a.arms.split(",") if x.strip()]:
        clip = ROOT / "tmp" / ("BD_%s_%s.mp4" % (a.project, arm))
        if not clip.exists():
            print("[ob] !! 缺成片 %s（先跑 probe_beat_density.py）" % clip.name)
            continue
        beats, bounds = beats_of(arm, a.project, a.ep)
        plan[arm] = (clip, beats, bounds)
        print("[ob] %-7s 声明 %2d 拍 / %d 镜：%s"
              % (arm, len(beats), len(bounds),
                 " ".join("%s[%.0f-%.0fs]" % (n, lo, hi) for n, lo, hi in bounds)))
    if a.dry_run:
        for arm, (_c, beats, _b) in plan.items():
            print("\n--- %s 清单 ---" % arm)
            for x in beats[:6]:
                print("  %d）[%s] %s" % (x["no"], x["win"], x["text"][:44]))
            if len(beats) > 6:
                print("  …共 %d 拍" % len(beats))
        return 0

    rows = []
    for arm, (clip, beats, bounds) in plan.items():
        per_round = []
        for rnd in range(1, a.rounds + 1):
            hits, rej, raw = 0, [], []
            for nm, lo, hi in bounds:
                sub = [x for x in beats if x["shot"] == nm]
                if not sub:
                    continue
                frames = grab_frames(clip, out / ("frames_%s_%s" % (arm, nm)), lo, hi)
                print("[ob] %s 第%d轮 %s [%.0f-%.0fs] 帧%d张 待判%d拍"
                      % (arm, rnd, nm, lo, hi, len(frames), len(sub)), flush=True)
                vs = judge(sub, frames)
                h, r = score(sub, vs, [t for t, _ in frames])
                hits += h
                rej += r
                raw += vs
            per_round.append((hits, rej))
            (out / ("%s_round%d.json" % (arm, rnd))).write_text(
                json.dumps({"hits": hits, "declared": len(beats),
                            "rejected": rej, "verdicts": raw},
                           ensure_ascii=False, indent=1), encoding="utf-8")
            print("[ob] %s 第%d轮：命中 %d/%d" % (arm, rnd, hits, len(beats)), flush=True)
        (h1, r1), (h2, r2) = per_round[0], per_round[1]
        disagree = len({x[0] for x in r1} ^ {x[0] for x in r2})
        rows.append((arm, len(beats), h1, h2, disagree, r1, r2))

    print("\n| 臂 | 声明拍数 | 命中·第1轮 | 命中·第2轮 | 两轮判定不一致 |")
    print("|---|---|---|---|---|")
    for arm, n, h1, h2, dis, _r1, _r2 in rows:
        print("| %s | %d | %d | %d | %d 拍 |" % (arm, n, h1, h2, dis))
    for arm, _n, _h1, _h2, _d, r1, r2 in rows:
        both = {x[0] for x in r1} & {x[0] for x in r2}
        print("\n[%s] 两轮都不算命中的拍（%d 条）：" % (arm, len(both)))
        for no, win, txt, why in r1:
            if no in both:
                print("   %d）[%s] %s ← %s" % (no, win, txt, why))
    print("RESULT: 逐拍点名完成（%d 臂 × %d 轮）" % (len(rows), a.rounds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
