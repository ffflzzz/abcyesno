# -*- coding: utf-8 -*-
"""Deterministic concat of rendered clips (ffmpeg)."""
from __future__ import annotations

import subprocess
from pathlib import Path


def concat(clip_dir: Path, out: Path) -> int:
    clips = sorted(clip_dir.glob("LN*.mp4"), key=lambda p: p.stem)
    if not clips:
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    if len(clips) == 1:
        out.write_bytes(clips[0].read_bytes())
        return 1
    lst = clip_dir / "_concat.txt"
    lst.write_text("\n".join("file '" + p.name + "'" for p in clips), encoding="utf-8")
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c", "copy", str(out)], capture_output=True)
    try:
        lst.unlink()
    except BaseException:  # noqa: BLE001
        # ★ **必须 catch `BaseException` 而不只是 `Exception`**（2026-09-14 实测两次）：
        # 沙箱的批量删除保护会**拦下删除并抛非 Exception 的异常**（实测把
        # `clips/_concat.txt` 的清理拦掉）→ 异常一路冒到顶层 → **已经跑完 25 镜、
        # 已经拼完成片**的那一轮在 `print("RESULT:")` **之前**结束
        # → 编排层据此判定"整条链失败"（实质成功被记成失败）。
        # 这里只是删一个临时清单文件：**清理失败绝不能影响成片**，留着也无害
        # （`clips/` 的遍历是 `glob("LN*.mp4")`，`_concat.txt` 不会命中）。
        pass
    return len(clips) if r.returncode == 0 else 0


def duration(path: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, timeout=30)
        return float(r.stdout.decode().strip())
    except Exception:  # noqa: BLE001
        return 0.0
