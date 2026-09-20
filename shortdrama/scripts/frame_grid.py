# -*- coding: utf-8 -*-
"""A/B 逐镜并排总览图：**左 A / 右 B**，一行一镜。

用途（两个真实场景）：
  · **改提示词 / 换风格包的前后对照** —— spec M4 的验收判据就是这么定的：
    改前抽帧归档 → 改后按**同组镜中点**重抽 → 拼「左旧 / 右新」。
    理由：**指标不敏感、眼睛敏感**。实测改 `still-refs` 时 QC 残留镜数只从
    10 变 11（指标几乎没动），而核心缺陷已经解决 ⇒ 这类改动只能靠人眼看。
  · 两个项目 / 两轮的同一镜对照（如开 / 关 `script-craft` 各跑一集）。

用法：
    py scripts/frame_grid.py <A> <B> [输出png] [--shots LN01,LN03] [--n 3] [--mid] [--max 12] [--ep N]

`<A>` / `<B>` 可以是：
  · 一个**项目目录**   → 自动取 `media/ep{N}/clips`（`--ep N` 集号，默认 1）
  · 一个 **clips 目录** → 取其中 `*.mp4`
  · 一个**单个 mp4**   → 一行

默认每镜每侧抽 **3 帧**（首/中/尾，直接复用 `clipqc.grab` 的既有抽帧口径，
**不另写一份抽帧逻辑**）；`--mid` 只要**中点**一帧（镜多时扫一眼最省）。

⚠️ 只画**两侧都存在的镜号**（交集），并显式打印"只在一侧有"的镜 —— 静默丢镜
   会让人误以为"两边一样多"。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from v5.media import clipqc  # noqa: E402

W = 260          # 每帧缩略宽
PAD = 8
LABEL_W = 96     # 左侧镜号标签宽度
HEADER_H = 34


def _arg(flag: str, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def resolve_clips(p: Path, ep: int = 1) -> dict:
    """把用户给的路径解析成 `{镜号: mp4路径}`。

    解析不出来就**响亮报错**（不静默返回空 —— 空图会让人以为"两边都没问题"）。
    """
    if p.is_file() and p.suffix.lower() == ".mp4":
        return {p.stem: p}
    cands = [p / "media" / ("ep%d" % ep) / "clips", p / "clips", p]
    for d in cands:
        if d.is_dir():
            got = {q.stem: q for q in sorted(d.glob("*.mp4")) if q.stat().st_size > 0}
            if got:
                return got
    raise SystemExit("在 %s 下找不到任何 mp4（试过：%s）"
                     % (p, "、".join(str(c) for c in cands)))


def shot_frames(clip: Path, tmp: Path, n: int, mid_only: bool) -> list:
    """抽帧并缩放到统一宽度。`mid_only` 时只留中点那一帧。"""
    want = 3 if mid_only else max(1, n)   # grab 的 n=3 才包含中点（首/中/尾）
    try:
        files = clipqc.grab(clip, tmp, n=want)
    except Exception as e:  # noqa: BLE001
        print("  !! %s 抽帧失败：%s" % (clip.name, str(e)[:80]))
        return []
    if mid_only and len(files) >= 2:
        files = [files[len(files) // 2]]
    out = []
    for f in files:
        try:
            im = Image.open(f).convert("RGB")
        except Exception:  # noqa: BLE001
            continue
        out.append(im.resize((W, max(1, int(im.height * W / im.width)))))
    return out


def main() -> int:
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    for flag in ("--shots", "--n", "--max", "--ep"):     # 别把这些 flag 的取值当位置参数
        v = _arg(flag)
        if v is not None and v in pos:
            pos.remove(v)
    if len(pos) < 2:
        print(__doc__)
        return 2
    A, B = Path(pos[0]), Path(pos[1])
    OUT = Path(pos[2]) if len(pos) > 2 else Path(".tmp") / "ab_overview.png"
    ep = int(_arg("--ep", "1"))
    n = int(_arg("--n", "3"))
    mid = "--mid" in sys.argv
    max_rows = int(_arg("--max", "12"))
    only = [s.strip() for s in (_arg("--shots", "") or "").split(",") if s.strip()]

    ca, cb = resolve_clips(A, ep), resolve_clips(B, ep)
    common = sorted(set(ca) & set(cb))
    if only:
        common = [s for s in common if s in only]
    if not common:
        raise SystemExit("两侧没有**共同的镜号**（A=%d 条、B=%d 条）→ 无法并排对照"
                         % (len(ca), len(cb)))
    dropped = sorted((set(ca) | set(cb)) - set(common))
    if dropped:
        print("  注：以下镜**只在一侧存在**，未画进图：%s" % "、".join(dropped))
    if len(common) > max_rows:
        print("  只画前 %d 镜（共有 %d）—— 用 --max 调大，或用 --shots 指定"
              % (max_rows, len(common)))
        common = common[:max_rows]

    tmp = Path(tempfile.mkdtemp(prefix="abgrid_"))
    try:
        rows = []
        for name in common:
            fa = shot_frames(ca[name], tmp / ("a_" + name), n, mid)
            fb = shot_frames(cb[name], tmp / ("b_" + name), n, mid)
            if fa or fb:
                rows.append((name, fa, fb))
        if not rows:
            raise SystemExit("一行都没画出来 —— 抽帧全失败（检查 ffmpeg 是否可用）")

        ncols_a = max(len(r[1]) for r in rows)
        ncols_b = max(len(r[2]) for r in rows)
        row_h = max((im.height for _, fa, fb in rows for im in (fa + fb)), default=200)
        canvas = Image.new(
            "RGB",
            (LABEL_W + (ncols_a + ncols_b) * (W + PAD) + PAD,
             HEADER_H + len(rows) * (row_h + PAD) + PAD),
            (24, 24, 28))
        dr = ImageDraw.Draw(canvas)
        dr.text((PAD, PAD), "A = %s" % A.name, fill=(255, 210, 120))
        dr.text((LABEL_W + ncols_a * (W + PAD), PAD), "B = %s" % B.name,
                fill=(140, 220, 255))
        for i, (name, fa, fb) in enumerate(rows):
            y = HEADER_H + i * (row_h + PAD)
            dr.text((PAD, y + row_h // 2), name, fill=(235, 235, 235))
            x = LABEL_W
            for im in fa:
                canvas.paste(im, (x, y))
                x += W + PAD
            x = LABEL_W + ncols_a * (W + PAD)
            for im in fb:
                canvas.paste(im, (x, y))
                x += W + PAD
        OUT.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(OUT)
        print("  已写 %s（%d 行；每行左 %d 帧 / 右 %d 帧；%.0f×%.0f）"
              % (OUT, len(rows), ncols_a, ncols_b, canvas.width, canvas.height))
        print("  行序（上→下）：%s" % " / ".join(n_ for n_, _, _ in rows))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
