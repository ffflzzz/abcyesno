# -*- coding: utf-8 -*-
"""探针：**换场那一刀该不该带上一段的末帧**（2026-10-02，先测不改系统）。

现象（`leak-upstairs-1002`，四段打包）：两处跳都正好落在换场的接缝上——
第一段楼道 → 第二段卧室（成片 4-7 秒），第三段卧室 → 第四段天台（27-28 秒）。
查送进去的图：跨场接缝上模型同时收到两张互相矛盾的空间图——
`prev`=**上一段（旧场景）的真实末帧** 和 `location`=**本段（新场景）的空镜**。
同场景接缝上末帧是对的锚；换场接缝上它是在把模型往回拽。

两臂（**同一组、同一批静帧与定妆照、同一份文字**，只差这一张图）：

    KEEP  现状：末帧照给（5 张 = 人物×2 + 场景 + 首镜静帧 + 旧场景末帧）
    DROP  改法：换场就不给末帧（空出的格子由优先级自动补给场景空镜/道具）

产物只落 tmp/seam/，**不写 projects/、不碰 video_jobs.json**。
判据两条：① 人眼对照抽帧图（每臂头 3 秒、0.5 秒/帧）；
② 视觉模型点名"新组第一镜里有没有出现旧场景的元素"，抄不出依据不算。

用法：
    python scripts/probe_seam_anchor.py --dry-run      # 只看两臂各送哪几张图
    python scripts/probe_seam_anchor.py                # 真跑（4 条视频请求）
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v5 import config                                               # noqa: E402
from v5.media import prompt as prompt_mod                           # noqa: E402
from v5.media import providers, storyboard, style, video            # noqa: E402

OLD_SCENE_ELEMENTS = {  # 每个换场接缝上"旧场景"的可辨元素（人眼与模型都按这份点名）
    "pack02": "绿漆墙裙的楼道、忽明忽暗的声控灯、铁防盗门",
    "pack04": "室内暖黄台灯、天花板水渍、铝制人字梯、剥落的墙板",
}


def data_uri_of(path_or_uri: str) -> str:
    if str(path_or_uri).startswith("data:"):
        return str(path_or_uri)
    b = Path(path_or_uri).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(b).decode()


def build_arms(root: Path, ep: int, pair: str):
    """返回 (组内镜名, 分配秒, 合计, {KEEP: (urls, roles, prompt), DROP: (...)})。"""
    rec = json.loads((root / "media" / ("ep%d" % ep) / "video_jobs.json")
                     .read_text(encoding="utf-8"))
    rec = rec.get("jobs", rec)
    shots = storyboard.parse(
        (root / "scenedesigner" / ("scenedesigner_ep%d.md" % ep)).read_text(encoding="utf-8"))
    by = {s["name"]: s for s in shots}
    st = json.load(open(root / "media" / ("ep%d" % ep) / "stills.json", encoding="utf-8"))
    items = st.get("items") if isinstance(st.get("items"), dict) else st
    names, declared = rec[pair]["shots"], rec[pair]["declared_seconds"]
    group = [by[n] for n in names]
    own = {n: (items.get(n) or {}).get("url") for n in names}
    prev = {"pack02": "pack01", "pack04": "pack03"}[pair]
    clip_dir = root / "media" / ("ep%d" % ep) / "clips"
    prev_url, src = video.seam_anchor(clip_dir, prev, {}, None)
    if not prev_url:                                    # 兜底：直接读盘上的末帧图
        p = clip_dir / (prev + ".last.jpg")
        prev_url = data_uri_of(str(p)) if p.exists() else None
        src = "末帧(盘上)" if prev_url else "无"
    sb = style.wrap(style.load(root))
    out = {}
    for arm, pu in (("KEEP", prev_url), ("DROP", None)):
        urls, roles = video.pack_ref_images(root, group, own, prev_url=pu, ep=ep)
        text = prompt_mod.build_pack_prompt(group, declared, sum(declared),
                                            style_block=sb, ref_roles=roles)
        out[arm] = (urls, roles, text)
    return names, declared, sum(declared), out, "%s→%s 锚来源=%s" % (prev, pair, src)


def submit_and_wait(arms: dict, total: int, out_dir: Path, pair: str) -> dict:
    keys = list(config.AGNES_API_KEYS) or [None]
    got = {}
    for i, (arm, (urls, _roles, text)) in enumerate(arms.items()):
        key = keys[i % len(keys)]
        r = providers.submit_video(text, mode="reference", images=urls,
                                   seconds=total, aspect_ratio=config.ASPECT_RATIO,
                                   timeout=300, key=key)
        got[arm] = (r.get("video_id"), time.time(), key)
        print("[seam] %s_%s submitted id=%s" % (pair, arm, r.get("video_id")), flush=True)
        time.sleep(int(config.video_submit_interval_per_key()))
    import httpx
    pending = dict(got)
    deadline = time.time() + 60 * 25
    while pending and time.time() < deadline:
        time.sleep(15)
        for arm, (vid, t0, key) in list(pending.items()):
            q = providers.query_video(vid, key=key)
            stt = q.get("status")
            if stt == "completed" and q.get("url"):
                dest = out_dir / ("%s_%s.mp4" % (pair, arm))
                with httpx.Client(timeout=180, trust_env=False) as c:
                    resp = c.get(q["url"])
                    resp.raise_for_status()
                    dest.write_bytes(resp.content)
                print("[seam] %s_%s done → %s（%.0fs）"
                      % (pair, arm, dest.name, time.time() - t0), flush=True)
                pending.pop(arm)
            elif stt in ("failed", "error"):
                print("[seam] %s_%s FAILED %s" % (pair, arm, str(q.get("error"))[:120]))
                pending.pop(arm)
    return {k: v for k, v in pending.items()}


def grid(path: Path, out: Path, dur: float = 3.0, step: float = 0.5, cols: int = 4) -> Path:
    from PIL import Image, ImageDraw
    tiles = []
    t = 0.0
    while t < dur:
        b = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(round(t, 2)), "-i", str(path),
                            "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                           capture_output=True).stdout
        if b:
            im = Image.open(io.BytesIO(b)).convert("RGB")
            w = 240
            im = im.resize((w, int(w * im.height / im.width)))
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, w, 20], fill=(0, 0, 0))
            d.text((5, 4), "%.1fs" % t, fill=(255, 255, 0))
            tiles.append(im)
        t = round(t + step, 2)
    if not tiles:
        raise SystemExit("抽不到帧：%s" % path)
    rows = (len(tiles) + cols - 1) // cols
    H = tiles[0].height
    sheet = Image.new("RGB", (cols * 240, rows * (H + 22)), (26, 26, 26))
    for i, im in enumerate(tiles):
        sheet.paste(im, ((i % cols) * 240, (i // cols) * (H + 22) + 20))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    return out


def ask_old_scene(sheet: Path, old: str) -> str:
    from langchain_core.messages import HumanMessage

    from v5 import llm
    q = ("这是换场后**新场景第一镜**的头 3 秒（每格标了时间戳）。"
         "旧场景的元素是：%s。\n请回答：这些旧元素有没有出现在画面里？"
         "有就**逐字抄出你看到的东西并给格子时间戳**，没有就只回答“无”。" % old)
    parts = [{"type": "text", "text": q},
             {"type": "image_url", "image_url": {"url": data_uri_of(str(sheet))}}]
    r = llm.chat_for("", 600, temperature=0).invoke([HumanMessage(content=parts)])
    return (r.content if hasattr(r, "content") else str(r)).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="leak-upstairs-1002")
    ap.add_argument("--ep", type=int, default=1)
    ap.add_argument("--pairs", default="pack02,pack04")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    root = ROOT / "projects" / a.project
    out_dir = ROOT / "tmp" / "seam"
    out_dir.mkdir(parents=True, exist_ok=True)

    for pair in [x.strip() for x in a.pairs.split(",") if x.strip()]:
        names, declared, total, arms, note = build_arms(root, a.ep, pair)
        print("\n[seam] %s = %s 分配秒 %s 合计 %ds（%s）"
              % (pair, "+".join(names), declared, total, note))
        for arm, (urls, roles, text) in arms.items():
            print("  %-4s 图 %d 张：%s｜文字 %d 字"
                  % (arm, len(urls), "、".join(r[0] for r in roles), len(text)))
        if a.dry_run:
            diff = len(arms["KEEP"][2]) - len(arms["DROP"][2])
            print("  两臂文字长度差 %d 字（末帧那句声明没了，其余逐字相同）" % diff)
            continue
        submit_and_wait(arms, total, out_dir, pair)
        for arm in ("KEEP", "DROP"):
            mp4 = out_dir / ("%s_%s.mp4" % (pair, arm))
            if not mp4.exists():
                continue
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                        "format=duration", "-of", "csv=p=0", str(mp4)],
                                       capture_output=True, text=True).stdout.strip() or 3)
            sh = grid(mp4, out_dir / ("%s_%s_grid.jpg" % (pair, arm)), dur=min(3.0, dur))
            ans = ask_old_scene(sh, OLD_SCENE_ELEMENTS.get(pair, ""))
            print("[seam] %s_%s 旧场景元素是否串进来：%s" % (pair, arm, ans[:260]), flush=True)
    print("RESULT: 换场锚探针完成（产物在 tmp/seam/）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
