# -*- coding: utf-8 -*-
"""单镜验证：用**修复后的**提示词重渲一个有台词的镜，看模型是否念出剧本台词。

背景（2026-09-12 clockmaker 事故）：
    有台词的镜被同时要求 `Audio: 搁这儿吧` 与 `Ambient only, do not speak:`，
    模型取解为噤声 + 自己即兴配音 → 全片都在说同一句兜底话，剧本没被念。
    修复见 `prompt.ambient_label`（有台词 → 只做 `Background ambience:`）。

用法：
    py scripts/probe_dialogue_shot.py clockmaker LN05 [--seconds 6]
产出：
    projects/<proj>/media/ep1/_probe/<SHOT>_fixed.mp4   （听这版）
    projects/<proj>/media/ep1/_probe/<SHOT>_fixed.prompt.txt（本次实际提示词）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from v5.media import prompt, providers, storyboard  # noqa: E402

POLL_EVERY = 5
MAX_WAIT = 900


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("shot")
    ap.add_argument("--ep", type=int, default=1)
    ap.add_argument("--seconds", type=int, default=0, help="0=用分镜的 seconds")
    ap.add_argument("--tag", default="fixed", help="产物后缀，便于同镜多版本对比")
    a = ap.parse_args()

    root = Path("projects") / a.project
    ep_dir = root / "media" / ("ep" + str(a.ep))
    shots = storyboard.parse(
        (root / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8"))
    shot = next((s for s in shots if s["name"] == a.shot), None)
    if shot is None:
        print("找不到镜:", a.shot)
        return 1
    import json
    stills = json.loads((ep_dir / "stills.json").read_text(encoding="utf-8"))
    info = stills.get(a.shot) or {}
    url = info.get("url")
    if not url:
        print("该镜没有静帧 URL:", a.shot)
        return 1

    secs = a.seconds or int(shot.get("seconds") or 6)
    vprompt = prompt.build_video_prompt(shot)
    out_dir = ep_dir / "_probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ("%s_%s.prompt.txt" % (a.shot, a.tag))).write_text(
        "has_dialogue=%s\nseconds=%s\n\n%s\n"
        % (prompt.has_dialogue(shot), secs, vprompt), encoding="utf-8")

    print("镜 %s  has_dialogue=%s  seconds=%s" % (a.shot, prompt.has_dialogue(shot), secs))
    print("提示词里含 'do not speak' ?", "do not speak" in vprompt)
    print("声音段:", [seg for seg in vprompt.split("  ")
                      if "Audio:" in seg or "ambience" in seg][:2])
    print("提交中…")
    r = None
    for attempt in range(8):
        try:
            r = providers.submit_video(vprompt, first_frame=url, seconds=secs)
            break
        except providers.QueueFullError:
            wait = 30 + 15 * attempt
            print("  队列满，%ds 后重试（%d/8）" % (wait, attempt + 1))
            time.sleep(wait)
    if r is None:
        print("队列持续满，放弃")
        return 2
    vid = r.get("video_id") or r.get("task_id")
    print("video_id =", vid)

    t0 = time.time()
    while time.time() - t0 < MAX_WAIT:
        time.sleep(POLL_EVERY)
        st = providers.query_video(vid)
        print("  [%4ds] %s %s%%" % (time.time() - t0, st.get("status"),
                                    st.get("progress")))
        if st.get("status") == "completed":
            src = st.get("url")
            dst = out_dir / ("%s_%s.mp4" % (a.shot, a.tag))
            with httpx.Client(timeout=180, follow_redirects=True) as c:
                dst.write_bytes(c.get(src).content)
            print("完成 →", dst, "%.1f MB" % (dst.stat().st_size / 1e6))
            print("源:", src)
            return 0
        if st.get("status") == "failed":
            print("失败:", st.get("error"))
            return 2
    print("轮询超时")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
