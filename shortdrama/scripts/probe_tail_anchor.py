# -*- coding: utf-8 -*-
"""落幅帧能不能顶替「上一组成片的真实末帧」当接续锚？—— 出并排图给人眼判。

为什么需要它：pack 档现在每组**等上一组渲完**只为抽它的真实末帧（`video.seam_anchor`），
13 组串行 ≈ 65 分钟。若改用「静帧阶段预生成的落幅帧」当锚，等待消失、可全平铺，
但承接从**事实**降级为**预期**（`prompt.build_tail_prompt` 的取舍注释）。
值不值得换，取决于落幅帧和真实末帧到底差多少 —— 只能看图判。

用法（在仓库根执行，会**烧生图额度**，不烧视频）：
    PYTHONUTF8=1 .venv/Scripts/python.exe scripts/probe_tail_anchor.py <项目名> [--ep N] [--out 目录]

产出：`<out>/tail_vs_real.jpg` —— 每行左=落幅帧（按分镜「落幅」列现画）、
右=该组成片的真实末帧。⚠️ **只测"像不像"**，测不到视频模型拿它当锚之后的接续效果。
"""
from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config                                        # noqa: E402
from v5.media import (                                       # noqa: E402
    assets, jobs as jobs_mod, keypool, prompt as prompt_mod, providers,
    relations, stills, storyboard, style, video,
)


def _injected_shots(root: Path, ep: int) -> tuple[list, dict, dict, dict, dict]:
    """复刻生产静帧阶段的逐镜注入（漏一条，落幅帧就比生产的还差，比对失真）。"""
    md = next((root / "scenedesigner").glob("scenedesigner_ep%d.md" % ep),
              next((root / "scenedesigner").glob("*.md"), None))
    if md is None:
        raise SystemExit("找不到分镜表（scenedesigner_ep%d.md）" % ep)
    shots = storyboard.parse(md.read_text(encoding="utf-8"))
    block = style.wrap(style.load(root))
    if block:
        shots = [{**s, "_style_block": block} for s in shots]
    sl = assets.scene_lines(root, shots, log=lambda *a: None)
    if sl:
        shots = [{**s, "_scene_line": sl.get(s["name"], "")} for s in shots]
    cn = assets.cast_counts(root, shots)
    if cn:
        shots = [{**s, "_cast_n": cn.get(s["name"], 0)} for s in shots]
    names: dict = {}
    types: dict = {}
    refs = assets.bind(root, shots, names_out=names, types_out=types)
    return shots, refs, names, types, {p.get("name"): p for p in relations.plan_frames(shots)}


def _tail_image(root: Path, shot: dict, plan: dict, refs: list, names: list,
                types: list, out_dir: Path):
    """一张落幅帧 = 与生产同一套提示词组装 + 同一份参考图。存到 out_dir（不碰生产清单）。"""
    p = prompt_mod.build_tail_prompt(shot, plan.get(shot["name"]))
    prompt = stills._with_ref_rule(p, refs, names, types)
    pool = _POOL
    for _try in range(3):
        idx, key = pool.claim()
        try:
            _, url = providers.gen_image(prompt, refs=refs or None,
                                         ratio=config.STILL_RATIO, key=key)
            if url:
                dest = out_dir / (shot["name"] + ".tail.jpg")
                stills._download(url, dest)
                return dest, prompt
        except providers.RateLimitError as e:
            pool.note_rate_limited(idx)
            print("  %s 限流（%s）→ 换 key" % (shot["name"], str(e)[:40]))
        except Exception as e:  # noqa: BLE001
            print("  %s 生图失败：%s" % (shot["name"], str(e)[:90]))
    return None, prompt


_POOL = None
_LOCK = threading.Lock()


def main() -> int:
    global _POOL
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--ep", type=int, default=1)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    root = Path("projects") / a.project
    if not root.exists():
        print("项目不存在：%s" % root)
        return 2
    out_dir = Path(a.out or "tmp/tail_probe/%s_ep%d" % (a.project, a.ep))
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = jobs_mod.load(root / "media" / ("ep%d" % a.ep))
    packs = sorted(k for k in jobs if k.startswith("pack"))
    clip_dir = root / "media" / ("ep%d" % a.ep) / "clips"
    # 锚帧用在**下一组**上 ⇒ 只有"有后继"的组需要比对（末组没人接）
    need = [(k, jobs[k].get("shots") or []) for k in packs[:-1]]
    need = [(k, s) for k, s in need if s]
    if not need:
        print("video_jobs.json 里没有 pack 记录（这个项目没走过 pack 档？）")
        return 2
    last_shots = [{"pack": k, "shot": s[-1]} for k, s in need]
    print("待比对 %d 组：%s" % (
        len(last_shots), " ".join("%s→%s" % (r["pack"], r["shot"]) for r in last_shots)))

    shots, refs, rnames, rtypes, plan = _injected_shots(root, a.ep)
    by_name = {s["name"]: s for s in shots}
    missing = [r["shot"] for r in last_shots if r["shot"] not in by_name]
    if missing:
        print("⚠️ 分镜表里找不到这些镜号（分镜在出片后改过？）：%s" % missing)
    todo = [r for r in last_shots if r["shot"] in by_name]

    _POOL = keypool.KeyPool.image_pool()
    print("生图池：%d 条 key，并发=%d，画幅=%s"
          % (len(_POOL), config.image_workers(), config.STILL_RATIO))
    _check_aspect(clip_dir, packs[0])

    def _one(r):
        n = r["shot"]
        s = by_name[n]
        return r, _tail_image(root, s, plan, (refs or {}).get(n) or [],
                              (rnames or {}).get(n), (rtypes or {}).get(n), out_dir)

    with ThreadPoolExecutor(max_workers=max(1, min(config.image_workers(), len(todo) or 1))) as ex:
        got = list(ex.map(_one, todo))

    rows = []
    fails = []
    for r, (dest, _prompt) in got:
        real = clip_dir / (r["pack"] + ".last.jpg")
        video.extract_last_frame(clip_dir / (r["pack"] + ".mp4"))   # 顺带落盘 .last.jpg
        rows.append((r["pack"], r["shot"], dest, real if real.exists() else None))
        if not dest or not real.exists():
            fails.append(r["pack"])
    _sheet(rows, out_dir / "tail_vs_real.jpg")
    print("\n拼接图：%s" % (out_dir / "tail_vs_real.jpg"))
    if fails:
        print("⚠️ %d 组没出齐（缺落幅帧或缺真实末帧）：%s" % (len(fails), fails))
    return 0


def _check_aspect(clip_dir: Path, first_pack: str) -> None:
    """**画幅自证**：成片实际分辨率 vs 本次生图用的 `STILL_RATIO`。

    当初踩过：换档没带对画幅 env，两条臂长成不同比例，比对结论整条作废。
    """
    import json as _json
    import subprocess
    clip = clip_dir / (first_pack + ".mp4")
    if not clip.exists():
        return
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "json", str(clip)],
                           capture_output=True, text=True, timeout=60)
        st = (_json.loads(r.stdout).get("streams") or [{}])[0]
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
    except Exception as e:  # noqa: BLE001
        print("⚠️ 画幅自证没跑成（%s）——比对前自己看一眼两张图比例是否一致" % str(e)[:60])
        return
    if w and h:
        print("成片画幅 = %d:%d ｜ 本次生图画幅参数 = %s" % (w, h, config.STILL_RATIO))


def _sheet(rows, dest: Path) -> None:
    from PIL import Image, ImageDraw
    H, W = 300, 200
    GAP, LBL = 10, 250
    canvas = Image.new("RGB", (LBL + W * 2 + GAP, len(rows) * (H + 8) + 34), (24, 24, 24))
    d = ImageDraw.Draw(canvas)
    d.text((LBL + 20, 8), "左：按分镜「落幅」列现画的落幅帧", fill=(255, 220, 120))
    d.text((LBL + W + GAP + 20, 8), "右：该组成片的真实末帧", fill=(150, 230, 150))
    y = 34
    for pack, shot, tail_p, real_p in rows:
        d.text((6, y + H // 2 - 18), "%s 末镜 %s" % (pack, shot), fill=(220, 220, 220))
        x = LBL
        for p in (tail_p, real_p):
            try:
                im = Image.open(p).convert("RGB")
                im.thumbnail((W, H))
                canvas.paste(im, (x, y))
            except Exception:  # noqa: BLE001
                d.rectangle([x, y, x + W, y + H], outline=(120, 60, 60))
                d.text((x + 10, y + H // 2), "缺", fill=(255, 120, 120))
            x += W + GAP
        y += H + 8
    canvas.save(dest, "JPEG", quality=88)


if __name__ == "__main__":
    raise SystemExit(main())
