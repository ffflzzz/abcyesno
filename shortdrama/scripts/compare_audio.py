# -*- coding: utf-8 -*-
"""比对两段音轨：是不是同一句话。用归一化互相关 + RMS/silence 特征。"""
import subprocess
import sys

import numpy as np


def pcm(path: str, sr: int = 16000) -> np.ndarray:
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
         "-f", "s16le", "-"],
        capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float64) / 32768.0


def feats(x: np.ndarray, sr: int = 16000) -> str:
    rms = float(np.sqrt((x ** 2).mean())) if len(x) else 0.0
    # 帧能量 → 非静音占比（-40dB 阈值）
    n = sr // 10
    if len(x) >= n:
        fr = x[: len(x) // n * n].reshape(-1, n)
        e = np.sqrt((fr ** 2).mean(axis=1))
        loud = float((e > 10 ** (-40 / 20)).mean())
    else:
        loud = 0.0
    return "dur=%.2fs rms=%.4f 非静音帧=%.0f%%" % (len(x) / sr, rms, loud * 100)


def best_corr(a: np.ndarray, b: np.ndarray, sr: int = 16000, span: float = 4.0):
    """在 ±span 秒内找最大归一化互相关（绝对值）。同句 → 接近 1。"""
    if not len(a) or not len(b):
        return 0.0, 0.0
    a = a - a.mean()
    b = b - b.mean()
    best, lag = 0.0, 0
    step = sr // 20                      # 50ms 步长
    for d in range(-int(span * sr), int(span * sr) + 1, step):
        if d >= 0:
            x, y = b[d:], a[: len(b) - d]
        else:
            x, y = b[: len(b) + d], a[-d:]
        m = min(len(x), len(y))
        if m < sr:                       # 重叠不足 1s 不算
            continue
        x, y = x[:m], y[:m]
        den = np.sqrt((x ** 2).sum() * (y ** 2).sum())
        if den <= 0:
            continue
        c = abs(float((x * y).sum() / den))
        if c > best:
            best, lag = c, d / sr
    return best, lag


if __name__ == "__main__":
    a = pcm(sys.argv[1])
    b = pcm(sys.argv[2])
    print("A =", sys.argv[1], "|", feats(a))
    print("B =", sys.argv[2], "|", feats(b))
    c, lag = best_corr(a, b)
    print("最大归一化互相关 = %.3f (lag %.2fs)   [>0.5 基本是同一段音频]" % (c, lag))
