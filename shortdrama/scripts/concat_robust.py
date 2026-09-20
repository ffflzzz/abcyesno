# -*- coding: utf-8 -*-
"""稳健拼接：先把所有片段统一到同一画布，再拼接。

为什么不能用 compose.concat 的 -c copy（实测）：
  keyframe 模式的输出尺寸**跟随首帧比例**，但 Agnes 对不同静帧给出的
  实际高度并不统一（实测同一批：704×960 / 704×992 / 704×1024）。
  `-c copy` 拼接不同分辨率的片段，播放器会拉伸/黑边/尺寸跳动——成片可见硬伤。

策略：以出现次数最多的尺寸为基准画布。
  --pad  （默认）等比缩放后补黑边，零画面损失，但多数镜会出现侧边黑条。
  --crop 等比放大到填满画布后居中裁切，无黑边，损失约 6% 边缘画面。
竖屏短剧观感优先，推荐 --crop。

用法：python scripts/concat_robust.py [project] [--crop|--pad]
"""
import collections
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config  # noqa: E402


def probe(path: Path) -> tuple[int, int, float]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60)
    nums = [x for x in r.stdout.replace("\n", ",").split(",") if x.strip()]
    w, h = int(float(nums[0])), int(float(nums[1]))
    dur = float(nums[2]) if len(nums) > 2 else 0.0
    return w, h, dur


def main() -> None:
    # ⚠️ 原默认值 `bootleg99-full` 已随 2026-09-14 的老架构清理删除；请显式传项目名。
    project = next((a for a in sys.argv[1:] if not a.startswith("--")),
                   "<请显式传项目名>")
    crop = "--crop" in sys.argv
    root = config.PROJECTS_DIR / project
    ep_dir = root / "media" / "ep1"
    clip_dir = ep_dir / "clips"
    clips = sorted(clip_dir.glob("LN*.mp4"), key=lambda p: p.stem)
    if not clips:
        print("没有片段")
        return

    sizes = []
    for c in clips:
        try:
            w, h, d = probe(c)
            sizes.append((w, h, d))
            print("  %-6s %dx%d %.2fs" % (c.stem, w, h, d))
        except Exception as e:  # noqa: BLE001
            print("  %-6s probe FAILED: %s" % (c.stem, str(e)[:60]))
            sizes.append((0, 0, 0.0))

    valid = [s for s in sizes if s[0]]
    if not valid:
        print("无可用片段")
        return
    cnt = collections.Counter((w, h) for w, h, _ in valid)
    tw, th = cnt.most_common(1)[0][0]
    print("基准画布 %dx%d（%d/%d 镜原生一致）" % (tw, th, cnt[(tw, th)], len(valid)))

    norm_dir = ep_dir / ("_norm_crop" if crop else "_norm")
    norm_dir.mkdir(exist_ok=True)
    parts = []
    for c, (w, h, _) in zip(clips, sizes):
        if not w:
            continue
        out = norm_dir / c.name
        if w == tw and h == th:
            vf = "null"
        elif crop:
            # 等比放大到刚好覆盖画布，再居中裁切（无黑边）
            vf = ("scale=%d:%d:force_original_aspect_ratio=increase,"
                  "crop=%d:%d" % (tw, th, tw, th))
        else:
            # 等比缩小到画布内，再补边（不裁切画面）
            vf = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
                  "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black" % (tw, th, tw, th))
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(c),
             "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
             "-ar", "44100", "-ac", "2", str(out)],
            capture_output=True, timeout=600)
        if out.exists():
            parts.append(out)

    lst = norm_dir / "_concat.txt"
    lst.write_text("\n".join("file '" + p.name + "'" for p in parts), encoding="utf-8")
    final = ep_dir / "episode_final.mp4"
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-c", "copy", str(final)], capture_output=True, timeout=900)
    if r.returncode != 0:
        print("concat 失败: %s" % r.stderr.decode()[:200])
        return
    w, h, d = probe(final)
    print("=== 成片 ===")
    print("  片段 %d  |  %dx%d  |  %.1f 秒（%.2f 分钟）"
          % (len(parts), w, h, d, d / 60))
    print("  %s" % final)


if __name__ == "__main__":
    main()
