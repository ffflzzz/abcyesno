"""探针（只读，不接进任何链路）：成片侧的语义审能不能用。

对法：
  · 单镜审 —— 每条 clip 抽 25%/55%/90% 三帧，问"这一镜有没有在演它写的那件事"
  · 整集审 —— 按镜号拼一张故事板，问"连起来讲的是什么 / 哪两镜断了 / 哪镜没在演"

模型只许报"看得见的"，并且必须**逐字抄**分镜原文；抄不出的一律作废（代码去分镜里核）。
跑完给三个数：耗时、抓到几条、误报（核不到引用）几条。

用法：
  python scripts/probe_storyboard_review.py --project <项目目录> [--eps 1,2]
        [--workers 4] [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage          # noqa: E402
from PIL import Image, ImageDraw                          # noqa: E402

from v5 import llm                                        # noqa: E402
from v5.media import storyboard                           # noqa: E402

FRACS = (0.25, 0.55, 0.90)
PUNCT = "「」『』“”\"'（）()、，。．：:；;！!？?—–-…· \t\r\n【】"

# ── 人工逐帧看过的确证项（用来算召回）────────────────────────────────────────
GROUND_TRUTH = {
    "1": {
        "LN10": "静帧里她手上并没有腰牌，成片凭空多出一块牌，牌面还刻着一列像字的纹样",
        "LN05": "同一镜内她的服装从浅青广袖变成墨黑窄袖，房间也换了",
        "LN04": "袖型与角色卡的「窄袖」不符（广袖），颜色偏浅青",
    },
    "2": {
        "LN01": "她多了一件卡上没有的浅色披衫",
        "LN02": "同一角色下一镜变成肘上短袖，与「窄袖襦裙」不符",
        "LN03": "腰牌牌面出现一列刻字（可读文字）",
    },
}

PROMPT_SHOT = """你是短剧**成片**质检。下面是**同一个镜头**按时间先后的三帧（25%、55%、90%），以及这一镜的分镜原文。

只回答一件事：**这一镜有没有在演它写的那件事。**

规则（违反任何一条，你这条就不作数）：
- 只报**画面里看得见**的问题，不许推测画外、不许推测下一镜。
- 每条问题必须给 `quote`：**从下面分镜原文里逐字抄**被违背的那一段（10~40 字）。抄不出来就别报这条。
- 每条问题必须给 `seen`：你在画面里看到了什么（≤30 字）。
- 要报：人物该在不在、道具该在不在、动作有没有发生、服装颜色与袖型对不对、发饰对不对、画面里有没有可读文字、人数对不对。
- 一律不报：景别、光线、构图、风格、审美、演技好坏。

只输出 JSON，不要解释：
{"verdict":"ok|bad","items":[{"kind":"缺道具|道具凭空出现|动作没发生|服装不符|人数不符|可读文字|说话人不在场|其他","quote":"逐字抄的分镜原文","seen":"画面里看到了什么"}]}

分镜原文（画面描述）：
{visual}

分镜原文（落幅）：
{tail}
"""

PROMPT_EP = """这是一集竖屏短剧的**故事板**，格子按镜号顺序排列，每格取自该镜中段，格上标了镜号。

回答三件事：
1. `summary`：用一句话说清这一集在讲什么。看不出来就老实写"看不出来"，并写 `summary_ok:false`。
2. `breaks`：哪两个**相邻镜**之间接不上（人数、站位、服装、道具、空间突然变了）。每条给 `between`（形如 "LN05→LN06"）和 `seen`（≤30 字，说看见什么变了）。
3. `not_acting`：哪一镜没在演分镜写给它的事。每条给 `shot`、`seen`，以及 `quote`——**从下面分镜清单里逐字抄**被违背的那一段（10~40 字），抄不出就别报。

只报看得见的。只输出 JSON：
{"summary":"...","summary_ok":true,"breaks":[{"between":"LN01→LN02","seen":"..."}],"not_acting":[{"shot":"LN05","quote":"...","seen":"..."}]}

分镜清单：
{board}
"""


def norm(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch not in PUNCT)


def data_uri(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


def grab(video: Path, frac: float, out: Path) -> Path | None:
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(video)], capture_output=True,
                             text=True).stdout.strip() or 0)
    if not d:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{d * frac:.2f}", "-i", str(video),
                    "-frames:v", "1", "-q:v", "3", str(out)], capture_output=True)
    return out if out.exists() else None


def ask(imgs: list[Path], text: str, tries: int = 3) -> str:
    parts = [{"type": "text", "text": text}]
    for p in imgs:
        parts.append({"type": "image_url", "image_url": {"url": data_uri(p)}})
    msg = HumanMessage(content=parts)
    last = None
    for i in range(tries):
        try:
            r = llm.chat_for("", 1200, temperature=0).invoke([msg])
            return r.content if hasattr(r, "content") else str(r)
        except Exception as e:                                  # noqa: BLE001
            last = e
            if llm.is_rate_limit(e):
                time.sleep(20 * (i + 1))
                continue
            time.sleep(3)
    raise RuntimeError(f"视觉调用失败：{str(last)[:120]}")


def parse_json(txt: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", txt, re.S)
    cand = m.group(1) if m else (txt[txt.find("{"): txt.rfind("}") + 1] if "{" in txt else "")
    try:
        return json.loads(cand) if cand else {}
    except Exception:                                           # noqa: BLE001
        return {}


def pool_of(sh: dict) -> str:
    return norm(" ".join(str(sh.get(k) or "") for k in
                         ("visual", "tail", "join_note", "dialogue", "scene", "shot_type")))


def board_text(shots: list[dict]) -> str:
    out = []
    for s in shots:
        out.append("%s｜%s｜%s｜画面：%s｜落幅：%s" % (
            s["name"], s.get("shot_type") or "", (s.get("scene") or "")[:18],
            (s.get("visual") or "")[:180], (s.get("tail") or "")[:80]))
    return "\n".join(out)


def contact_sheet(frames: list[tuple[str, Path]], out: Path, cols: int = 5, tile_w: int = 240):
    th = int(tile_w * 16 / 9)
    ims = []
    for _, p in frames:
        im = Image.open(p).convert("RGB")
        w, h = im.size
        target = h * 9 / 16
        if w > target:                                    # 不是 9:16 就居中裁
            im = im.crop((int((w - target) / 2), 0, int((w + target) / 2), h))
        ims.append(im.resize((tile_w, th), Image.LANCZOS))
    rows = (len(ims) + cols - 1) // cols
    S = Image.new("RGB", (cols * tile_w, rows * (th + 20)), "white")
    dr = ImageDraw.Draw(S)
    for i, im in enumerate(ims):
        r, c = divmod(i, cols)
        x, y = c * tile_w, r * (th + 20)
        S.paste(im, (x, y + 20))
        dr.text((x + 4, y + 4), frames[i][0], fill="black")
    out.parent.mkdir(parents=True, exist_ok=True)
    S.save(out, quality=88)
    return out


def review_shot(ep, sh, clip, work):
    imgs = [f for f in (grab(clip, fr, work / f"{sh['name']}_{i}.jpg")
                        for i, fr in enumerate(FRACS)) if f]
    if not imgs:
        return {"shot": sh["name"], "error": "抽不到帧"}
    txt = ask(imgs, PROMPT_SHOT.replace("{visual}", (sh.get("visual") or "")[:1600])
              .replace("{tail}", (sh.get("tail") or "")[:400]))
    d = parse_json(txt)
    pool = pool_of(sh)
    kept, dropped = [], []
    for it in (d.get("items") or []):
        q = norm(str(it.get("quote") or ""))
        if not q or q not in pool:
            dropped.append({"quote": str(it.get("quote") or "")[:60], "seen": str(it.get("seen") or "")[:60],
                            "why": "引用在分镜里核不到" if q else "没给引用"})
        elif not str(it.get("seen") or "").strip():
            dropped.append({"quote": q[:60], "why": "没描述看到什么"})
        else:
            kept.append(it)
    return {"shot": sh["name"], "verdict": d.get("verdict") or "?", "kept": kept,
            "dropped": dropped, "raw_head": txt[:160]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--eps", default="1,2")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.project)
    out = root / "probe_review"
    out.mkdir(exist_ok=True)
    t0 = time.time()
    result = {"project": root.name, "shots": {}, "episode": {}, "calls": 0}

    for ep in [e.strip() for e in args.eps.split(",") if e.strip()]:
        md = root / f"scenedesigner/scenedesigner_ep{ep}.md"
        if not md.exists():
            print(f"[skip] 没有 {md}")
            continue
        shots = storyboard.parse(md.read_text(encoding="utf-8"))
        if args.limit:
            shots = shots[:args.limit]
        by_name = {s["name"]: s for s in shots}
        mid = []
        jobs = []
        for s in shots:
            clip = root / f"media/ep{ep}/clips/{s['name']}.mp4"
            if not clip.exists():
                continue
            jobs.append((s, clip))
        print(f"== ep{ep}: {len(shots)} 镜 / 有 clip {len(jobs)} 条")
        if args.dry_run:
            for s, clip in jobs:
                f = grab(clip, 0.55, out / f"e{ep}_{s['name']}_mid.jpg")
                if f:
                    mid.append((s["name"], f))
            if mid:
                print("   拼版:", contact_sheet(mid, out / f"e{ep}_board.jpg"))
            continue

        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(review_shot, ep, s, c, out / f"frames_e{ep}"): s["name"] for s, c in jobs}
            for fu in cf.as_completed(futs):
                name = futs[fu]
                try:
                    r = fu.result()
                except Exception as e:                          # noqa: BLE001
                    r = {"shot": name, "error": str(e)[:160]}
                result["shots"][f"{ep}:{name}"] = r
                if not r.get("error"):
                    result["calls"] += 1
                n = len(r.get("kept") or [])
                print(f"   {name} {r.get('verdict','?')} 抓到{n} 丢弃{len(r.get('dropped') or [])}"
                      + (f"  ERR {r['error']}" if r.get("error") else ""))
                f = grab(root / f"media/ep{ep}/clips/{name}.mp4", 0.55, out / f"e{ep}_{name}_mid.jpg")
                if f:
                    mid.append((name, f))
        # 整集审
        if mid:
            mid.sort(key=lambda x: x[0])
            sheet = contact_sheet(mid, out / f"e{ep}_board.jpg")
            txt = ask([sheet], PROMPT_EP.replace("{board}", board_text(shots)))
            d = parse_json(txt)
            kept_b, kept_n, dropped = [], [], []
            for b in (d.get("breaks") or []):
                mm = re.findall(r"LN\d+", str(b.get("between") or ""))
                if len(mm) == 2 and mm[0] in by_name and mm[1] in by_name:
                    kept_b.append(b)
                else:
                    dropped.append({"kind": "break", "raw": str(b)[:80], "why": "镜号不存在"})
            for it in (d.get("not_acting") or []):
                sh = by_name.get(str(it.get("shot") or ""))
                q = norm(str(it.get("quote") or ""))
                if sh and q and q in pool_of(sh):
                    kept_n.append(it)
                else:
                    dropped.append({"kind": "not_acting", "shot": it.get("shot"),
                                    "quote": str(it.get("quote") or "")[:60],
                                    "why": "镜号不存在" if not sh else "引用核不到"})
            result["episode"][ep] = {"summary": d.get("summary"), "summary_ok": d.get("summary_ok"),
                                     "breaks": kept_b, "not_acting": kept_n, "dropped": dropped}
            result["calls"] += 1
            print(f"   整集审：断点 {len(kept_b)} / 没在演 {len(kept_n)} / 丢弃 {len(dropped)}")
            print(f"   summary: {str(d.get('summary'))[:120]}")

    # 对账：人工确证项抓到没有
    hits = {}
    for ep, items in GROUND_TRUTH.items():
        for name, desc in items.items():
            got = [i for i in (result["shots"].get(f"{ep}:{name}", {}) or {}).get("kept") or []]
            got += [i for i in result["episode"].get(ep, {}).get("not_acting") or []
                    if i.get("shot") == name]
            hits[f"ep{ep} {name}"] = {"确证问题": desc, "探针报了吗": bool(got),
                                      "内容": [str(i.get("kind") or i.get("quote") or "")[:70] + "｜" +
                                               str(i.get("seen") or "")[:70] for i in got]}
    result["ground_truth"] = hits
    result["seconds"] = round(time.time() - t0, 1)
    (out / "probe_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    n_hit = sum(1 for v in hits.values() if v["探针报了吗"])
    print(f"\n耗时 {result['seconds']}s ｜ 模型调用 {result['calls']} 次 ｜ "
          f"人工确证 {len(hits)} 项，探针抓到 {n_hit} 项")
    for k, v in hits.items():
        print(f"  {'✅' if v['探针报了吗'] else '❌'} {k} —— {v['确证问题'][:46]}")
    print("明细：", out / "probe_result.json")


if __name__ == "__main__":
    main()
