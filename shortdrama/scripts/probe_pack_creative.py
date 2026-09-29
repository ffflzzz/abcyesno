# -*- coding: utf-8 -*-
"""探针：pack 档加一句「核心创意/总纲」vs 不加，同一组镜、同一批参考图。

起因（2026-09-29 对账 Agnes Video 2.5 官方提示词模板指南）：官方完整公式是
「参考素材说明 + 核心创意 + 画面过程说明」，7 个范例每一个都在正文开头写一句
「X秒，X:XX 版 + 谁在哪干什么 + 风格 + 运镜」。我们的 pack 档只有素材声明和
「本片段总长 N 秒由 M 个节拍组成」——**报了结构，没报内容**。

矩阵（一次只动一个变量）：

    臂    提示词
    ----  ---------------------------------------------------------------
    base  生产 `build_pack_prompt` 原样输出
    lead  同一段文字，在素材声明之后、总长声明之前插入【核心创意】段

【核心创意】的全部文字都从**盘上现成事实**拼出来，不手写创作内容：
时长与画幅取自 job 记账与 config、人名取自 brief 的 protagonist/second_character、
场景名取自分镜「场景」列、各段小标题取自各镜画面描述的第一个节拍前缀、
题材取自 brief.genre。⇒ 若 lead 臂赢，赢的是"多给了一句总纲"，不是"我加了要求"。

参考图**两组共用同一次装配结果**（`video.pack_ref_images` + `video.seam_anchor`），
所以图不会成为变量。⛔ 不改任何生产代码、不动 projects/ 下的产物。

用法：
    PYTHONUTF8=1 SHORTDRAMA_ASPECT=16:9 python scripts/probe_pack_creative.py --dry-run
    PYTHONUTF8=1 SHORTDRAMA_ASPECT=16:9 python scripts/probe_pack_creative.py --groups pack01,pack02
产物：
    tmp/PK_<组>_<臂>.mp4            两臂成片
    tmp/pk_prompts/<组>_<臂>.txt    两臂提示词逐字留档
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v5 import config                                              # noqa: E402
from v5.media import jobs as jobs_mod                              # noqa: E402
from v5.media import prompt as prompt_mod                          # noqa: E402
from v5.media import providers, storyboard, style, video           # noqa: E402

PROJ = ROOT / "projects" / "huashan-duel-v4-0928"
EP = 1
OUT = ROOT / "tmp"
PROMPT_DIR = OUT / "pk_prompts"

_BEAT_TITLE = re.compile(r"0-\d+\s*秒[：:]\s*([^—－]{2,18}?)[—－]")

#: 臂名 → (要不要结构总纲, 要不要「两人都同帧在动」这条律)。
#: base/lead 是第一轮已渲的两角；struct/rule 是把两者拆开的新两臂。
#: base2 与 base **提示词、参考图完全相同**，唯一作用是量模型自身的生成方差——
#: 没有它，"臂间差"和"重摇一次也会有的抖动"分不开（2026-09-29：用户反馈
#: "没看出来区别"，此时必须先知道噪声有多大）。
SPECS = {"base": (False, False), "lead": (True, True),
         "struct": (True, False), "rule": (False, True),
         "base2": (False, False)}


def _brief() -> dict:
    return json.loads((PROJ / "brief.json").read_text(encoding="utf-8"))


def _person_names(b: dict) -> list[str]:
    out = []
    for k in ("protagonist", "second_character"):
        nm = str(b.get(k) or "").split("：")[0].strip()
        if nm:
            out.append(nm)
    return out


def creative_line(group: list[dict], declared: list[int], total: int,
                  b: dict, aspect: str, include_rule: bool = True,
                  include_struct: bool = True) -> str:
    """【核心创意】段——官方 2.2 要求的五要素：主体/地点/事件/风格/运镜。

    ★ `include_rule` 拆出第二条律（2026-09-29 复盘）：base/lead 两臂差的不只是
      "多一句总纲"，还多了一句「两人全程同帧且都在动」——而这两组的分镜恰恰写着
      「另一人作远景虚化剪影无动作」「两人各缩为石台直径十分之一」。不拆成
      `struct`（只结构）/ `rule`（只这条律）两个臂，就分不清赢在哪一条。
    """
    scene = (group[0].get("scene") or "").strip()
    beats = []
    for s in group:
        m = _BEAT_TITLE.search((s.get("visual") or ""))
        beats.append(m.group(1).strip() if m else "")
    beats = [x for x in beats if x]
    who = " 与 ".join("@%s" % n for n in _person_names(b)) or "两人"
    parts = []
    if include_struct:
        parts.append("【核心创意】%d 秒，%s 横版。" % (total, aspect))
        parts.append("%s 在 @%s 上，依次「%s」。"
                     % (who, scene, "、".join(beats) if beats else "连续出招与应招"))
    if include_rule:
        parts.append("两人全程同帧且都在动，各只持一柄兵刃。")
    if include_struct:
        if b.get("genre"):
            parts.append("%s。" % str(b["genre"]).strip().rstrip("。"))
        parts.append("%d 个镜头依次硬切，不是同一机位一镜到底。" % len(group))
    return "".join(parts)


def insert_lead(text: str, lead: str) -> str:
    """插在素材声明之后、总长声明之前（= 官方公式里核心创意的位
    置）。分段符与生产一致：\\n\\n。"""
    segs = text.split("\n\n")
    idx = 1 if len(segs) > 1 else 0
    segs.insert(idx, lead)
    return "\n\n".join(segs)


def cut_line(group: list[dict], declared: list[int]) -> str:
    """只加一句「这是 N 个镜头、在第几秒切」——官方 3.1 / 原则6 的写法。

    不含秒数画幅声明、不含人数律，用来把上一轮混在一起的三样东西拆开。
    """
    acc, bounds = 0, []
    for s in declared[:-1]:
        acc += s
        bounds.append(str(acc))
    return ("本片段由 %d 个镜头依次硬切构成，切点在第 %s 秒；"
            "这不是同一机位的一镜到底，每个镜头各自成画。"
            % (len(group), "、".join(bounds)))


def motion_line(b: dict) -> str:
    """只加 brief 里那条人数律（分镜的单拍描述与它相反）。"""
    who = " 与 ".join("@%s" % n for n in _person_names(b)) or "两人"
    return "%s 全程同帧且都在动：一人出招，另一人在同一镜内做出可见反应。" % who


ARMS = {"cut": cut_line, "motion": motion_line}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", default="pack01,pack02")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--compare", action="store_true",
                    help="只做对照产物（两臂 mp4 已在盘上时）")
    ap.add_argument("--measure", action="store_true",
                    help="逐对抽帧量「几人同框/几人在动/景别」，base vs base2 当噪声地板")
    ap.add_argument("--arms", default="struct,rule",
                    help="逗号分隔：%s" % ",".join(SPECS))
    ap.add_argument("--force", action="store_true", help="盘上已有的臂也重渲")
    a = ap.parse_args()
    gl = [x.strip() for x in a.groups.split(",") if x.strip()]
    if a.compare:
        return compare(gl)
    if a.measure:
        return measure(gl)
    specs = [(t, *SPECS[t]) for t in [x.strip() for x in a.arms.split(",") if x.strip()]]

    out_dir = PROJ / "media" / ("ep%d" % EP)
    clip_dir = out_dir / "clips"
    jobs = jobs_mod.load(out_dir)
    st_raw = json.loads((out_dir / "stills.json").read_text(encoding="utf-8"))
    stills = st_raw.get("stills", st_raw)
    md = (PROJ / "scenedesigner" / ("scenedesigner_ep%d.md" % EP)).read_text(encoding="utf-8")
    by = {s["name"]: s for s in storyboard.parse(md)}
    b = _brief()
    sb = style.wrap(style.load(PROJ))
    order = sorted(jobs)

    arms = []
    for gname in [x.strip() for x in a.groups.split(",") if x.strip()]:
        rec = jobs.get(gname) or {}
        names, declared = rec.get("shots") or [], rec.get("declared_seconds") or []
        total = int(rec.get("total_seconds") or sum(declared or []))
        if not names:
            print("[pk] !! 记账里没有 %s，跳过" % gname)
            continue
        group = [by[n] for n in names]
        gi = order.index(gname)
        prev_last = jobs[order[gi - 1]]["shots"][-1] if gi > 0 else None
        prev_url, prev_src = video.seam_anchor(
            clip_dir, ("pack%02d" % gi) if gi > 0 else None, stills, prev_last)
        own = {n: (stills.get(n) or {}).get("url") for n in names}
        urls, roles = video.pack_ref_images(PROJ, group, own, prev_url=prev_url)

        base = prompt_mod.build_pack_prompt(group, declared, total,
                                            style_block=sb, ref_roles=roles)
        print("[pk] %s = %s 合计%ds | images=%d 接续锚=%s"
              % (gname, "+".join(names), total, len(urls), prev_src))
        for tag, inc_struct, inc_rule in specs:
            if OUT.joinpath("PK_%s_%s.mp4" % (gname, tag)).exists() and not a.force:
                print("[pk] %s_%s 已在盘上，跳过（--force 可重跑）" % (gname, tag))
                continue
            if not (inc_struct or inc_rule):
                text, lead = base, ""
            else:
                lead = creative_line(group, declared, total, b, config.ASPECT_RATIO,
                                     include_rule=inc_rule, include_struct=inc_struct)
                text = insert_lead(base, lead)
            if lead:
                print("     %-6s %s" % (tag, lead))
            print("     %-6s 字数=%d (+%d)" % (tag, len(text), len(text) - len(base)))
            arms.append((gname, tag, text, urls, total))

    PROMPT_DIR.mkdir(parents=True, exist_ok=True)
    for gname, tag, text, urls, total in arms:
        (PROMPT_DIR / ("%s_%s.txt" % (gname, tag))).write_text(text, encoding="utf-8")
    if a.dry_run:
        print("[pk] dry-run，未提交（共 %d 臂）" % len(arms))
        return 0

    OUT.mkdir(exist_ok=True)
    pool = video.keypool.KeyPool.of()
    print("[pk] 提交配速：%d 条 key × %s" % (len(pool), pool.pacing()))
    ids = {}
    for i, (gname, tag, text, urls, total) in enumerate(arms):
        for q_try in range(config.VIDEO_QUEUE_RETRIES + 1):
            key_idx, key = pool.claim()
            try:
                r = providers.submit_video(text, mode="reference",
                                           images=[u for u in urls if u],
                                           seconds=total, key=key,
                                           aspect_ratio=config.ASPECT_RATIO)
                ids["%s_%s" % (gname, tag)] = (r.get("video_id"), time.time(), key)
                print("[pk] %s_%s submitted id=%s" % (gname, tag, r.get("video_id")))
                break
            except providers.QueueFullError:
                pool.note_rate_limited(key_idx)
                wait = min(300, 60 * (1 + q_try))
                print("[pk] %s_%s 队列满 → 换 key，%ds 后重试（%d）"
                      % (gname, tag, wait, q_try + 1))
                time.sleep(wait)
            except Exception as e:                                # noqa: BLE001
                print("[pk] %s_%s 提交失败：%s" % (gname, tag, str(e)[:120]))
                time.sleep(20)

    deadline = time.time() + 60 * 40
    pending = dict(ids)
    while pending and time.time() < deadline:
        time.sleep(15)
        for tag, (vid, _t0, key) in list(pending.items()):
            try:
                q = providers.query_video(vid, key=key)
            except Exception as e:                                # noqa: BLE001
                print("[pk] %s 查询异常：%s" % (tag, str(e)[:60]))
                continue
            st = q.get("status")
            if st == "completed" and q.get("url"):
                import httpx
                dest = OUT / ("PK_%s.mp4" % tag)
                try:
                    with httpx.Client(timeout=300, trust_env=False) as c:
                        resp = c.get(q["url"])
                        resp.raise_for_status()
                        dest.write_bytes(resp.content)
                    print("[pk] RESULT %s → %s（%.1f MB，%.0fs）"
                          % (tag, dest.name, dest.stat().st_size / 1e6,
                             time.time() - ids[tag][1]))
                    pending.pop(tag)
                except Exception as e:                            # noqa: BLE001
                    print("[pk] %s 下载失败：%s" % (tag, str(e)[:80]))
            elif st in ("failed", "error"):
                print("[pk] RESULT %s FAILED: %s" % (tag, str(q.get("error"))[:120]))
                pending.pop(tag)
    if pending:
        print("[pk] !! 仍在等待：%s" % list(pending))
        return 1
    return 0


ARM_ORDER = ("base", "base2", "struct", "rule", "lead")
ARM_DESC = {"base": "no global line", "base2": "same as base (noise floor)",
            "struct": "structure only",
            "rule": "both-move rule only", "lead": "structure + rule"}


def compare(groups: list[str]) -> int:
    """对照产物：每臂一行的接触表 + 每个非 base 臂对 base 的上下同屏视频。

    ⛔ 无声（-an）——本轮验证的是提示词对**画面**的影响，不比对音频；
    混两条 BGM 会让人误读成音频结论。
    ⛔ 不在画面上打标签——本机 ffmpeg 无 fontconfig，`drawtext` 会让整条滤镜失败
    （2026-09-29 实测 "Cannot load default config file"）。约定：**上=base，下=对比臂**，
    臂名写在接触表行首（PIL 画字不受此限）。
    """
    import subprocess

    from PIL import Image, ImageDraw

    for g in groups:
        have = [t for t in ARM_ORDER if (OUT / ("PK_%s_%s.mp4" % (g, t))).exists()]
        if not have:
            print("[cmp] !! %s 一条臂都没有" % g)
            continue
        base = OUT / ("PK_%s_base.mp4" % g)

        frames_dir = OUT / ("cmp_%s" % g)
        frames_dir.mkdir(exist_ok=True)
        rows, counts = [], []
        for t in have:
            p = OUT / ("PK_%s_%s.mp4" % (g, t))
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(p),
                            "-vf", "fps=1/2,scale=440:-1",
                            str(frames_dir / (t + "_%02d.jpg"))], capture_output=True)
            got = sorted(frames_dir.glob(t + "_*.jpg"))
            row = [Image.open(x).convert("RGB") for x in got]
            h = max((im.height for im in row), default=0)
            strip = Image.new("RGB", (440 * len(row) + 8 * (len(row) - 1), h), (18, 18, 18))
            for i, im in enumerate(row):
                strip.paste(im, (i * (440 + 8), 0))
            ImageDraw.Draw(strip).text((8, 6), "%s  %s" % (t.upper(), ARM_DESC[t]),
                                       fill=(255, 255, 0))
            rows.append(strip)
            counts.append(len(row))
        canvas = Image.new("RGB", (max(r.width for r in rows),
                                   sum(r.height + 10 for r in rows)), (12, 12, 12))
        y = 0
        for r in rows:
            canvas.paste(r, (0, y))
            y += r.height + 10
        sheet = OUT / ("CMP_%s_sheet.jpg" % g)
        canvas.save(sheet, quality=90)
        print("[cmp] %s 接触表 → %s（臂：%s，每行 %s 帧 / 间隔 2 秒）"
              % (g, sheet.name, ",".join(have), counts))

        if not base.exists():
            continue
        for t in have:
            if t == "base":
                continue
            vs = OUT / ("CMP_%s_vs_%s.mp4" % (g, t))
            r = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(base),
                 "-i", str(OUT / ("PK_%s_%s.mp4" % (g, t))),
                 "-filter_complex", "[0:v]scale=1280:-1[a];[1:v]scale=1280:-1[b];"
                                    "[a][b]vstack", "-an", str(vs)],
                capture_output=True, text=True)
            print("[cmp] %s 上=base 下=%s → %s %s"
                  % (g, t, vs.name, "OK" if r.returncode == 0
                     else "FAIL: " + (r.stderr or "")[:160]))
    return 0


MEASURE_PROMPT = """这是**同一段视频相隔约 0.6 秒的两帧**（第一张在前，第二张在后）。
只回答一个 JSON，不要任何解释文字：
{"people": 画面里出现的人物总数（含远景小人，整数）,
 "clear_faces": 清晰可辨的人脸数（整数）,
 "actors": 两帧之间发生了可见位移或姿态变化的人物数（整数，0/1/2/3…）,
 "scale": 主体人物的景别，只能取 大远景/远景/中景/近景/特写 之一,
 "quote": 支撑 actors 这个数的画面事实，一句话（必须是你亲眼看到的差异，
          例如「红衣者从左侧移到中央、白衣者手臂由垂到抬起」）}
判定纪律：站着不动、只有头发或衣摆轻微飘动的人物**不计入 actors**；
看不清是两个人的远景小点，people 照实数、clear_faces 记 0。"""


def _frame(video: Path, at: float, dest: Path) -> bool:
    import subprocess
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "%.2f" % at,
                        "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=900:-1", str(dest)], capture_output=True, text=True)
    return r.returncode == 0 and dest.exists()


def _dur(video: Path) -> float:
    import subprocess
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(video)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:  # noqa: BLE001
        return 0.0


def measure(groups: list[str], step: float = 2.0, delta: float = 0.6) -> int:
    """逐对抽帧送视觉模型，量「几人同框 / 几人在动 / 景别」，并用 base vs base2
    当噪声地板——臂间差小于重摇抖动就不算差别。"""
    import json as _json

    from langchain_core.messages import HumanMessage
    from v5.llm import chat_for

    def uri(p: Path) -> str:
        import base64
        return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()

    rows = []
    for g in groups:
        for arm in ARM_ORDER:
            v = OUT / ("PK_%s_%s.mp4" % (g, arm))
            if not v.exists():
                continue
            dur = _dur(v)
            fd = OUT / ("meas_%s_%s" % (g, arm))
            fd.mkdir(exist_ok=True)
            t0 = 0.4
            while t0 + delta < dur:
                f1, f2 = fd / ("a_%04.1f.jpg" % t0), fd / ("b_%04.1f.jpg" % t0)
                if not (_frame(v, t0, f1) and _frame(v, t0 + delta, f2)):
                    t0 += step
                    continue
                msg = HumanMessage(content=[
                    {"type": "text", "text": MEASURE_PROMPT},
                    {"type": "image_url", "image_url": {"url": uri(f1)}},
                    {"type": "image_url", "image_url": {"url": uri(f2)}}])
                d = {}
                for _try in range(3):
                    try:
                        r = chat_for("", 400, temperature=0).invoke([msg])
                        txt = r.content if hasattr(r, "content") else str(r)
                        m = re.search(r"\{.*\}", txt, re.S)
                        d = _json.loads(m.group(0)) if m else {}
                        if isinstance(d, dict) and "actors" in d:
                            break
                    except Exception as e:                        # noqa: BLE001
                        print("[ms] %s/%s t=%.1f 调用失败：%s"
                              % (g, arm, t0, str(e)[:70]))
                        time.sleep(8)
                if isinstance(d, dict) and "actors" in d:
                    rows.append({"group": g, "arm": arm, "t": round(t0, 1),
                                 "people": int(d.get("people") or 0),
                                 "clear_faces": int(d.get("clear_faces") or 0),
                                 "actors": int(d.get("actors") or 0),
                                 "scale": str(d.get("scale") or ""),
                                 "quote": str(d.get("quote") or "")[:90]})
                    print("[ms] %s %-6s t=%4.1f people=%s faces=%s actors=%s %s"
                          % (g, arm, t0, d.get("people"), d.get("clear_faces"),
                             d.get("actors"), d.get("scale")))
                t0 += step
    (OUT / "pk_measure.json").write_text(
        _json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n===== 汇总（每组每臂：帧对数 / 两人以上同框率 / ≥2人在动率 / 平均清晰人脸）=====")
    for g in groups:
        print("-- %s" % g)
        for arm in ARM_ORDER:
            sub = [r for r in rows if r["group"] == g and r["arm"] == arm]
            if not sub:
                continue
            n = len(sub)
            both = sum(1 for r in sub if r["people"] >= 2) / n
            moving = sum(1 for r in sub if r["actors"] >= 2) / n
            faces = sum(r["clear_faces"] for r in sub) / n
            from collections import Counter
            sc = Counter(r["scale"] for r in sub).most_common(2)
            print("   %-6s n=%2d 同框≥2人=%4.0f%% ≥2人在动=%4.0f%% 平均清晰脸=%.2f 景别=%s"
                  % (arm, n, both * 100, moving * 100, faces, sc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
