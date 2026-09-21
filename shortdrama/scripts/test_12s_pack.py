# -*- coding: utf-8 -*-
"""12 秒打包提交实验：LN01-03（4s+4s+4s）合成**一条** 12s reference 请求。

背景（2026-09-21，用户提案）：现在每镜单独提交 reference，模型不知道相邻镜
的存在 → 镜间断开（mixed 的落幅承接也因此落空）。本实验把同一场景、时间
连续的三个分镜打包成一次 API 调用，让模型在**单次生成内部**自己处理节拍
间的转场与连续性——接戏从"跨请求问题"变成"单请求内部问题"。

对照组：media/ep1/（mixed 版逐镜）与 media/ep1_ref/（reference 旧版）的
LN01/LN02/LN03 三条 4s 片段。

用法：
    py scripts/test_12s_pack.py            # 提交 + 轮询 + 抽帧
产物：
    projects/guofeng-test-0921/media/packtest/LN01-03_12s.mp4
    projects/guofeng-test-0921/media/packtest/frames/{f0,f4,f8,f12}.jpg
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config                                   # noqa: E402
from v5.media import providers                          # noqa: E402
from v5.media.storyboard import parse                   # noqa: E402
from v5.media.video import _wait_one                    # noqa: E402

PROJECT = "guofeng-test-0921"
SHOTS = ("LN01", "LN02", "LN03")
SECONDS = 12
OUT_DIR = config.PROJECTS_DIR / PROJECT / "media" / "packtest"

# 硬闸门：本实验只验证"打包提交"这一件事，不碰 v5 主链。
if int(getattr(config, "VIDEO_MAX_SECONDS", 12)) < SECONDS:
    raise SystemExit("VIDEO_MAX_SECONDS=%d < 12，请先确认 API 上限" % config.VIDEO_MAX_SECONDS)


def build_prompt(shots: list[dict]) -> str:
    """三拍打包 prompt：参考图逐拍点名 + 节拍边界硬声明。"""
    segs = [
        "以 <Picture 1> 为第 0-4 秒节拍的画面参考，<Picture 2> 为第 4-8 秒节拍的画面参考，"
        "<Picture 3> 为第 8-12 秒节拍的画面参考；三张参考图对应同一条 12 秒片段的三个节拍，"
        "人物、服装、道具与场景一律以对应参考图为准。",
        "本片段总长 12 秒，由同一场景里连续发生的三个节拍组成，各节拍严格占 4 秒，"
        "节拍之间按下列说明转场：",
    ]
    for i, s in enumerate(shots):
        left, right = i * 4, (i + 1) * 4
        dialogue = (s.get("dialogue") or "").strip()
        dlg = dialogue if dialogue and "无声" not in dialogue else "无台词（环境音）"
        join = (s.get("join_note") or "").strip()
        join_line = f"转场承接：{join}。" if join else ""
        segs.append(
            "【第 %d-%d 秒｜%s·%s·%s】%s\n%s\n台词：%s\n音效：%s\n落幅：%s"
            % (left, right, s.get("shot_type"), s.get("angle"), s.get("camera"),
               join_line, (s.get("visual") or "").strip(), dlg,
               (s.get("sfx") or "").strip(), (s.get("tail") or "").strip()))
    segs.append(
        "画面风格：电影级国风古装剧照质感。第 0-4 秒与第 8-12 秒是画室实景"
        "（粗铜烛台暖橘烛光约 2400K 与窗外冷月白光约 7000K 对切，85mm/50mm 焦段）；"
        "第 4-8 秒是画中世界（冷月白光约 7500K 柔和漫射，85mm）——节拍之间色调随空间切换，"
        "转场要干脆利落、像同一个摄制组拍出来的一条连续素材。",
        )
    segs.append("全片不得出现任何文字、字幕、水印；不得分屏；竖屏构图。")
    return "\n\n".join(segs)


def main() -> int:
    root = config.PROJECTS_DIR / PROJECT
    shots = {s["name"]: s for s in parse(
        (root / "scenedesigner" / "scenedesigner_ep1.md").read_text(encoding="utf-8"))}
    picked = [shots[n] for n in SHOTS]
    stills = json.loads((root / "media" / "ep1" / "stills.json").read_text(encoding="utf-8"))
    urls = [(stills[n] or {}).get("url") for n in SHOTS]
    if not all(urls):
        raise SystemExit("静帧缺失：%s" % SHOTS)
    prompt = build_prompt(picked)
    print("[pack] prompt %d 字，images=%d，seconds=%d" % (len(prompt), len(urls), SECONDS))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / ("_".join(SHOTS) + "_12s.mp4")
    if dest.exists() and dest.stat().st_size > 0:
        print("[pack] 已存在：%s（跳过提交）" % dest)
    else:
        r = providers.submit_video(prompt, mode="reference", images=list(urls),
                                   seconds=SECONDS, aspect_ratio=config.ASPECT_RATIO)
        vid = r.get("video_id") or r.get("task_id")
        print("[pack] submitted id=%s" % vid)
        t0 = time.time()
        local = _wait_one(vid, dest, rounds=60, interval=10,
                          log=lambda m: print(m))
        if not local:
            raise SystemExit("[pack] 轮询超窗/失败")
        print("[pack] done in %.0fs" % (time.time() - t0))

    # 抽帧：0 / 4 / 8 / 11.9s —— 三个节拍起点 + 结尾
    import subprocess
    fdir = OUT_DIR / "frames"
    fdir.mkdir(exist_ok=True)
    for t, tag in ((0, "f0"), (4, "f4"), (8, "f8"), (11.9, "f12")):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(dest),
                        "-frames:v", "1", "-q:v", "2", str(fdir / (tag + ".jpg"))],
                       capture_output=True, timeout=60)
    print("RESULT:", json.dumps({"status": "ok", "final": str(dest),
                                 "frames": str(fdir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
