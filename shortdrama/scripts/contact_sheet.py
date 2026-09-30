# -*- coding: utf-8 -*-
"""把一集的静帧拼成一张长图，供人眼扫（AGENTS 建议的"别只信 QC 回执"那条的落地工具）。

用法：python scripts/contact_sheet.py <project> --ep=N [--mark=LN05,LN07] [--cols=4]

`--mark=` 传硬伤镜号（比如 `qc_sweep.py` 报出来的那份名单），这些格会描红框 +
标 ❌ —— 人扫的时候第一眼就该看它们。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from v5 import config  # noqa: E402


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    project = args[0] if args else "<请显式传项目名>"
    ep = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--ep=")), "1")
    cols = int(next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--cols=")), "4"))
    mark = {x.strip() for x in next(
        (a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--mark=")), "").replace(",", " ").split()
        if x.strip()}
    root = config.PROJECTS_DIR / project
    sd = root / "media" / ("ep" + str(ep)) / "stills"
    files = sorted(p for p in sd.glob("LN*.jpg"))
    if not files:
        raise SystemExit("[sheet] ⛔ %s 里没有静帧" % sd)

    cw, ch, pad, cap = 360, 640, 10, 30
    rows = -(-len(files) // cols)
    sheet = Image.new("RGB", (cols * (cw + pad) + pad,
                              rows * (ch + cap + pad) + pad), (18, 18, 18))
    dr = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    for i, p in enumerate(files):
        name = p.stem
        r, c = divmod(i, cols)
        x = pad + c * (cw + pad)
        y = pad + r * (ch + cap + pad)
        im = Image.open(p)
        im.thumbnail((cw, ch))
        sheet.paste(im, (x + (cw - im.width) // 2, y + (ch - im.height) // 2))
        bad = name in mark
        dr.rectangle([x, y, x + cw, y + ch], outline=(220, 60, 60) if bad else (70, 70, 70),
                     width=4 if bad else 1)
        dr.text((x + 4, y + ch + 4), "%s %s" % (name, "❌" if bad else ""),
                fill=(255, 120, 120) if bad else (230, 230, 230), font=font)
    out = root / "media" / ("ep" + str(ep)) / ("contact_sheet_ep%s.jpg" % ep)
    sheet.save(out, quality=88)
    print("[sheet] %d 镜 → %s（描红 %d 镜）" % (len(files), out, len(mark & {p.stem for p in files})))


if __name__ == "__main__":
    main()
