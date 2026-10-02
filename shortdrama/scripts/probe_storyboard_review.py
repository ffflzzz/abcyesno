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
KINDS = {"缺道具", "道具凭空出现", "动作没发生", "服装不符", "发饰不符",
         "人数不符", "说话人不在场", "可读文字", "镜内漂移"}

# ── 回归样本：上一轮**人工逐帧**确证过的，用来量这版探针准不准 ──────────────
#   must_catch：真问题，探针必须报出来（`keys` = 报出来时句子里应出现的词）
#   must_not：上一轮探针的**瞎报**，这一轮必须不再出现
MUST_CATCH = {
    "1:LN10": ("腰牌静帧里没有、成片凭空多出，牌面刻着一列字", ("刻字", "文字", "錾纹", "凭空", "牌")),
    "1:LN05": ("同一镜内她服装从浅青广袖变墨黑窄袖、房间也换", ("变", "袖", "颜色", "衣")),
    "1:LN04": ("袖型与「窄袖」不符（广袖），颜色偏浅青", ("袖",)),
    "2:LN01": ("她多了一件卡上没有的浅色披衫", ("披", "外", "衫", "袖", "衣")),
    "2:LN02": ("同一角色下一镜变肘上短袖，与「窄袖襦裙」不符", ("袖",)),
    "2:LN03": ("腰牌牌面出现一列刻字（可读文字）", ("字", "錾")),
    "2:LN08": ("分镜写「两人只有剪影」，画面里人脸清清楚楚", ("剪影",)),
}
MUST_NOT = {
    "1:LN06": "光线冷暖/暖边（上一轮瞎报）",
    "2:LN02": "背景雨幕虚化程度（上一轮瞎报）",
}

PROMPT_SHOT = """你是短剧**成片**质检。下面是**同一个镜头**按时间先后的三帧（25%、55%、90%），以及这一镜的分镜原文。

回答两件事：
A. **这一镜有没有在演它写的那件事。** 该有的人在不在、该有道具在不在、动作有没有真的发生、服装发饰对不对、画面里有没有可读文字、人数对不对。
B. **有没有"不该变的东西"在三帧之间变了**：同一个人的服装颜色/袖型、发饰、所在的房间。
   ⚠️ 因剧情动作造成的变化**不算**（衣服被她解下、东西被他放下、人转身走开 —— 这些都是本该发生的）。

每条问题给三个字段：
- `kind`：从这几个里选 —— 缺道具 / 道具凭空出现 / 动作没发生 / 服装不符 / 发饰不符 / 人数不符 / 说话人不在场 / 可读文字 / 镜内漂移
- `quote`：**从下面分镜原文里逐字抄**被违背的那一段（10~40 字）
- `seen`：你在画面里看到了什么（≤30 字，写看得见的东西）

以下这些**不属于你的活**，不要写进 items（写了我也会当噪声滤掉）：光线明暗、色温冷暖、背景虚化程度、构图、景别、审美、演技。

只输出 JSON，不要解释：
{"verdict":"ok|bad","items":[{"kind":"...","quote":"...","seen":"..."}],
 "drift":{"changed":true,"what":"≤30字，说清什么变了；没变就留空"}}

分镜原文（画面描述）：
{visual}

分镜原文（落幅）：
{tail}
"""

PROMPT_EP = """这是一集竖屏短剧的**故事板**，格子按镜号顺序排列，每格取自该镜中段，格上标了镜号。

回答两件事：
1. `summary`：用一句话说清这一集在讲什么。
2. `breaks`：哪两个**相邻镜**之间接不上 —— 人数、左右站位、服装、手里拿的东西、所在的房间突然变了。
   每条给 `between`（形如 "LN05→LN06"）和 `seen`（≤30 字，说清**看得见**的变了什么）。
   只报人物/道具/空间的可辨变化；光线、氛围、构图、剪辑节奏不算。

只输出 JSON：
{"summary":"...","breaks":[{"between":"LN05→LN06","seen":"..."}]}

这一集的分镜清单（用来对照格子里该有什么）：
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


def ask(imgs: list[Path], text: str, tries: int = 3, max_tokens: int = 1200) -> str:
    parts = [{"type": "text", "text": text}]
    for p in imgs:
        parts.append({"type": "image_url", "image_url": {"url": data_uri(p)}})
    msg = HumanMessage(content=parts)
    last = None
    for i in range(tries):
        try:
            r = llm.chat_for("", max_tokens, temperature=0).invoke([msg])
            return r.content if hasattr(r, "content") else str(r)
        except Exception as e:                                  # noqa: BLE001
            last = e
            if llm.is_rate_limit(e):
                time.sleep(20 * (i + 1))
                continue
            time.sleep(3)
    raise RuntimeError(f"视觉调用失败：{str(last)[:120]}")


def ask_json(imgs: list[Path], text: str):
    """拿不回合法 JSON 就**响亮报错** —— 静默当成"没查出问题"是最坏的方向。"""
    txt = ""
    for i in range(2):
        txt = ask(imgs, text, max_tokens=1400 + 1400 * i)
        d = parse_json(txt)
        if d:
            return d, txt
    raise RuntimeError(f"模型输出解析不出 JSON（不静默放行）：{txt[:140]}")


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
    d, txt = ask_json(imgs, PROMPT_SHOT.replace("{visual}", (sh.get("visual") or "")[:1600])
                      .replace("{tail}", (sh.get("tail") or "")[:400]))
    pool = pool_of(sh)
    kept, dropped = [], []
    for it in (d.get("items") or []):
        kind = str(it.get("kind") or "").strip()
        q = norm(str(it.get("quote") or ""))
        seen = str(it.get("seen") or "").strip()
        if kind not in KINDS:
            dropped.append({"kind": kind, "quote": q[:60], "seen": seen[:60],
                            "why": "类别不在白名单（光线/构图/抠字面一律不认）"})
        elif not q or q not in pool:
            dropped.append({"kind": kind, "quote": str(it.get("quote") or "")[:60],
                            "seen": seen[:60], "why": "引用在分镜里核不到"})
        elif not seen:
            dropped.append({"kind": kind, "quote": q[:60], "why": "没描述看到什么"})
        else:
            kept.append(it)
    dr = d.get("drift") or {}
    drift = {"changed": bool(dr.get("changed")), "what": str(dr.get("what") or "").strip()}
    if drift["changed"] and not drift["what"]:
        drift["changed"] = False
    return {"shot": sh["name"], "verdict": d.get("verdict") or "?", "kept": kept,
            "drift": drift, "dropped": dropped, "raw_head": txt[:160]}


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
        # 整集审：每 7 格一张（相邻两张共用首镜，跨张的接缝也看得见）
        if mid:
            mid.sort(key=lambda x: x[0])
            chunks = [mid[i:i + 7] for i in range(0, len(mid), 6)]
            kept_b, dropped = [], []
            for ci, ch in enumerate(chunks, 1):
                sheet = contact_sheet(ch, out / f"e{ep}_board{ci}.jpg", cols=4, tile_w=380)
                d, _t = ask_json([sheet], PROMPT_EP.replace("{board}", board_text(shots)))
                result["calls"] += 1
                if d.get("summary"):
                    result["episode"].setdefault(ep, {})["summary"] = d.get("summary")
                    result["episode"][ep]["summary_ok"] = d.get("summary_ok")
                for b in (d.get("breaks") or []):
                    mm = re.findall(r"LN\d+", str(b.get("between") or ""))
                    if len(mm) == 2 and mm[0] in by_name and mm[1] in by_name:
                        kept_b.append(b)
                    else:
                        dropped.append({"kind": "break", "raw": str(b)[:80],
                                        "why": "镜号不存在或不成对"})
            result["episode"].setdefault(ep, {})["breaks"] = kept_b
            result["episode"][ep]["dropped"] = dropped
            print(f"   整集审：{len(chunks)} 张拼版 ｜ 断点 {len(kept_b)} ｜ 丢弃 {len(dropped)}")
            print(f"   summary: {str(result['episode'][ep].get('summary'))[:120]}")

    def said(key):
        v = result["shots"].get(key) or {}
        bits = [str(i.get("kind") or "") + str(i.get("quote") or "") + str(i.get("seen") or "")
                for i in (v.get("kept") or [])]
        dr = v.get("drift") or {}
        if dr.get("changed"):
            bits.append("镜内漂移" + str(dr.get("what") or ""))
        return " ".join(bits)

    catch, leak = {}, {}
    for key, (desc, keys) in MUST_CATCH.items():
        txt = said(key)
        catch[key] = {"真问题": desc, "报了吗": bool(txt) and any(k in txt for k in keys),
                      "探针原话": txt[:160]}
    for key, desc in MUST_NOT.items():
        txt = said(key)
        bad = [w for w in ("光线", "暖边", "色温", "冷暖", "虚化", "构图", "景别") if w in txt]
        leak[key] = {"该消失的瞎报": desc, "还在报": bool(bad), "命中词": bad, "探针原话": txt[:160]}
    result["must_catch"] = catch
    result["must_not"] = leak
    result["seconds"] = round(time.time() - t0, 1)
    (out / "probe_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    hit = sum(1 for v in catch.values() if v["报了吗"])
    fp = sum(1 for v in leak.values() if v["还在报"])
    n_items = sum(len(v.get("kept") or []) for v in result["shots"].values())
    n_drop = sum(len(v.get("dropped") or []) for v in result["shots"].values())
    print(f"\n耗时 {result['seconds']}s ｜ 模型调用 {result['calls']} 次 ｜ "
          f"留下 {n_items} 条 / 代码挡掉 {n_drop} 条")
    print(f"真问题 {hit}/{len(catch)} 抓到 ｜ 上一轮的瞎报残留 {fp}/{len(leak)}（要 0）")
    for k, v in catch.items():
        print(f"  {'✅' if v['报了吗'] else '❌'} {k} —— {v['真问题'][:44]}"
              + (f"｜它说：{v['探针原话'][:60]}" if v["报了吗"] else ""))
    for k, v in leak.items():
        print(f"  {'❌还在报' if v['还在报'] else '✅已消失'} {k} —— {v['该消失的瞎报']}"
              + (f"｜命中 {v['命中词']}" if v["还在报"] else ""))
    print("明细：", out / "probe_result.json")


if __name__ == "__main__":
    main()
