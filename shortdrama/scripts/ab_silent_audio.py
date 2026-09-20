# -*- coding: utf-8 -*-
"""A/B 测试：静默镜的「音频否定词」是否会招来人声。

**完全独立于媒体链** —— 不经过 pipeline、不写项目产物，结果落 `.tmp/ab_silent/`。

单一变量：**只有提示词不同**，用的是同一个镜的**同一张静帧**。

  A（现状）   = build_video_prompt(mode="reference")  完整 788 字，
                含 `Ambient only, do not speak:` + `no speech/talking/voice-over/spoken words`
  B（官方式）  = 只留「景别机位 + 本镜风格句 + 画面内容 + 落幅」
                对齐官方示例的静默镜提示词（官方静默镜**一个音频词都没有**）

判据：听两个成片的音轨 —— A 有没有"大家好…"、B 有没有。

用法：
    py scripts/ab_silent_audio.py <项目> <镜名>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config                                   # noqa: E402
from v5.media import prompt as pm, providers, storyboard, style, video  # noqa: E402

PROJ = sys.argv[1] if len(sys.argv) > 1 else "morning-stall"
NAME = sys.argv[2] if len(sys.argv) > 2 else "LN01"

root = Path("projects") / PROJ
out = Path(".tmp/ab_silent")
out.mkdir(parents=True, exist_ok=True)

shots = storyboard.parse((root / "scenedesigner/scenedesigner.md").read_text(encoding="utf-8"))
s = next((x for x in shots if x["name"] == NAME), None)
if s is None:
    print("!! 找不到镜 %s，可选：" % NAME, [x["name"] for x in shots])
    raise SystemExit(1)

stills = json.loads((root / "media/ep1/stills.json").read_text(encoding="utf-8"))
img_url = stills[NAME]["url"]
print("[ab] 项目=%s 镜=%s 秒数=%s" % (PROJ, NAME, s.get("seconds")))
print("[ab] 静帧 = %s" % img_url[:80])

s2 = {**s, "_style_block": style.wrap(style.load(root))}

# ── A：现状（完整）────────────────────────────────────────────────────────
pa = pm.build_video_prompt(s2, mode="reference")

# ── B：官方式最小（只留四段内容，删掉整层指令）──────────────────────────────
segs = [pm.camera_line(s), pm.style_line(s), pm.content_line(s),
        pm.tail_line(s, english=False)]
pb = pm._join_segs([x for x in segs if x])

(out / "prompt_A.txt").write_text(pa, encoding="utf-8")
(out / "prompt_B.txt").write_text(pb, encoding="utf-8")

print()
print("[ab] A（现状） %d 字  含音频否定词=%s"
      % (len(pa), ("do not speak" in pa) or ("no speech" in pa)))
print("[ab] B（最小） %d 字  含音频否定词=%s"
      % (len(pb), ("do not speak" in pb) or ("no speech" in pb)))
print("[ab] A 含反分屏/禁字幕/reference 声明/风格块 = %s / %s / %s / %s"
      % ("single continuous" in pa, "no subtitles" in pa,
         "<Picture 1>" in pa, len(s2["_style_block"]) > 100))
print()

for tag, p in (("A", pa), ("B", pb)):
    dest = out / ("%s_%s.mp4" % (NAME, tag))
    if dest.exists():
        print("[ab] %s 已存在，跳过" % tag)
        continue
    print("[ab] ── 提交变体 %s ──" % tag, flush=True)
    r = None
    for retry in range(config.VIDEO_QUEUE_RETRIES + 1):
        try:
            r = providers.submit_video(p, mode="reference", images=[img_url],
                                       seconds=s.get("seconds") or 8)
            break
        except providers.RateLimitError:
            # 供应商 1rpm：媒体链若同时在跑，闸门会被它占住 —— 必须**等待重试**，
            # 不能直接放弃（2026-09-13 实测：两条链并行时 A/B 双双撞 429）。
            wait = 90
            print("     429 限流（闸门被占），%ds 后重试（%d/%d）"
                  % (wait, retry + 1, config.VIDEO_QUEUE_RETRIES + 1), flush=True)
            time.sleep(wait)
        except providers.QueueFullError:
            wait = min(120, 20 * (retry + 1))
            print("     队列满，%ds 后重试" % wait, flush=True)
            time.sleep(wait)
        except Exception as e:  # noqa: BLE001
            print("     ❌ %s: %s" % (type(e).__name__, str(e)[:150]))
            r = None
            break
    if not r:
        print("[ab] %s 提交失败" % tag, flush=True)
        continue
    vid = r.get("video_id") or r.get("task_id")
    print("     video_id = %s，轮询中…" % vid, flush=True)
    ok = video._wait_one(vid, dest, rounds=60, log=lambda *a, **k: None)
    print("[ab] %s → %s" % (tag, ok or "失败"), flush=True)
    if tag == "A" and not dest.exists():
        pass
    time.sleep(config.VIDEO_SUBMIT_MIN_INTERVAL_S)   # 供应商 1rpm 闸门

print()
print("[ab] 结果目录：%s" % out.resolve())
for f in sorted(out.glob("*.mp4")):
    print("     %s  %.1f MB" % (f.name, f.stat().st_size / 1024 / 1024))
