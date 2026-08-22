#!/usr/bin/env python3
"""对已有分镜 mp4 重跑合成节点，产出 final.mp4（不重新生成视频）。

用法:
  hermes-fork/.venv/Scripts/python.exe scripts/merge_existing_episode.py
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKILLS = os.path.join(REPO, "hermes-fork", "skills")
AGENT = os.path.join(SKILLS, "langgraph_agents", "agents", "manjucraft_agent")

sys.path.insert(0, SKILLS)
sys.path.insert(0, AGENT)
sys.path.insert(0, os.path.join(REPO, "hermes-fork"))

PROJ = os.path.join(
    os.path.expanduser("~/.hermes_portable_data"),
    "manjucraft_agent", "projects",
    "manjucraft-series-20260822-162034", "ep000",
)

from mc_services import ffmpeg as ff

print("ffmpeg_path ->", ff.ffmpeg_path())

# 收集 videos/ 下所有 shot_*.mp4，按 shot index 排序
import re
import glob

video_dir = os.path.join(PROJ, "videos")
audio_dir = os.path.join(PROJ, "audio")

video_paths = []
audio_paths = []
for vp in sorted(glob.glob(os.path.join(video_dir, "shot_*.mp4"))):
    m = re.search(r"shot_(\d+)\.mp4$", os.path.basename(vp))
    idx = int(m.group(1)) if m else 0
    video_paths.append(vp)
    # 对应音频：audio/shot_{idx}_raw.mp3
    ap = os.path.join(audio_dir, f"shot_{idx:03d}_raw.mp3")
    audio_paths.append(ap if os.path.exists(ap) else None)

print(f"videos: {len(video_paths)}, with-audio: {sum(1 for a in audio_paths if a)}")

final_path = os.path.join(PROJ, "final.mp4")
try:
    out = ff.merge_shots(video_paths, audio_paths, final_path)
    print("MERGED ->", out, "size=", os.path.getsize(out))
    print("OK")
except Exception as exc:
    print("MERGE FAILED:", repr(exc))
    sys.exit(1)
