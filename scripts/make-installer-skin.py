#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从品牌素材生成 NSIS 安装器皮肤资源。

产出（全部落在 build/，electron-builder 的 buildResources 目录）：
  build/installerHeader.bmp   150x57  24bit  安装页/完成页页眉右侧签名图
  build/installerSidebar.bmp  164x314 24bit  完成页左侧大图
  build/icon.png              512x512 RGBA  exe / 安装器图标源
  build/icon.ico              16..256 多尺寸  安装器窗口图标 + exe 图标

素材来源：src/assets/bach-icon.png（1024x994 白底扁平插画）
依赖：Pillow + numpy + scipy（用 anaconda 的 python 跑最省事）

跑法：
  "C:/ProgramData/anaconda3/python.exe" scripts/make-installer-skin.py
可选 --preview 额外输出 PNG 预览到 tmp/installer-skin/preview/
"""
from __future__ import annotations

import os
import sys

import numpy as np
import scipy.ndimage as ndi
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "assets", "bach-icon.png")
BUILD = os.path.join(ROOT, "build")
PREVIEW = os.path.join(ROOT, "tmp", "installer-skin", "preview")

# 品牌色（从 bach-icon.png 采样）
COL_INK = (26, 26, 26)          # 描边黑，用作主文字色
COL_SKIN = (249, 188, 148)      # 脸部粉
COL_ACCENT = (232, 154, 107)    # 强调线（脸色的深一档）
COL_MUTED = (150, 150, 150)     # 次要文字
COL_PAPER = (255, 255, 255)     # MUI 页眉/侧栏底色是纯白，图底必须一致才能无缝

FONT_CANDIDATES = [
    ("C:/Windows/Fonts/seguisb.ttf", "semibold"),
    ("C:/Windows/Fonts/segoeuib.ttf", "bold"),
    ("C:/Windows/Fonts/arialbd.ttf", "arial-bold"),
    ("C:/Windows/Fonts/msyhbd.ttc", "yahei-bold"),
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path, _name in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    raise SystemExit("找不到可用字体，检查 C:/Windows/Fonts/")


def cutout(path: str) -> Image.Image:
    """去掉纯白底，返回紧凑裁剪后的 RGBA 头像。"""
    img = Image.open(path).convert("RGB")
    a = np.asarray(img).astype(np.int16)
    lo = a.min(axis=2)
    spread = a.max(axis=2) - a.min(axis=2)
    raw_white = (lo > 243) & (spread < 14)
    # closing 会因 border_value=0 把最外圈整圈清掉（边界外被当成背景），
    # 所以再并回 raw_white —— 否则边缘种子全空，propagation 什么都扩散不出来。
    near_white = ndi.binary_closing(
        raw_white, structure=np.ones((3, 3), bool), border_value=1
    ) | raw_white
    seed = np.zeros_like(near_white)
    seed[0, :] = raw_white[0, :]
    seed[-1, :] = raw_white[-1, :]
    seed[:, 0] = raw_white[:, 0]
    seed[:, -1] = raw_white[:, -1]
    if not seed.any():
        raise SystemExit("抠图失败：图像四周没有判定为背景的白像素")
    outside = ndi.binary_propagation(seed, mask=near_white)
    # 外扩 1px：切掉抗锯齿留下的浅白边
    outside = ndi.binary_dilation(outside, iterations=1)
    alpha = np.where(outside, 0, 255).astype(np.uint8)
    rgba = np.dstack([np.asarray(img), alpha])
    out = Image.fromarray(rgba, "RGBA")
    box = out.getchannel("A").getbbox()
    if box is None:
        raise SystemExit("抠图失败：整幅图都被判成背景")
    return out.crop(box)


def square_canvas(avatar: Image.Image, pad_ratio: float = 0.07) -> Image.Image:
    """把头像放进正方形透明画布，四周留 pad_ratio 边距（图标构图用）。"""
    w, h = avatar.size
    side = int(max(w, h) / (1 - 2 * pad_ratio))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(avatar, ((side - w) // 2, (side - h) // 2), avatar)
    return canvas


def head_scaled(avatar: Image.Image, height: int, keep: float = 0.80, fade: float = 0.07) -> Image.Image:
    """保留头部、切掉肩膀，底缘做透明渐隐 —— 不套圆形遮罩，边缘就是原图的粗描边。

    keep/fade 是相对原图高度的比例：keep 之前完全保留，keep..keep+fade 渐隐到全透明。
    """
    w, h = avatar.size
    cut = int(round(h * (keep + fade)))
    head = avatar.crop((0, 0, w, cut))
    a = np.asarray(head.getchannel("A")).astype(np.float32) / 255.0
    yy = np.arange(cut, dtype=np.float32)
    y0, y1 = h * keep, float(cut)
    ramp = np.clip((y1 - yy) / max(y1 - y0, 1.0), 0.0, 1.0)
    a = a * ramp[:, None]
    head.putalpha(Image.fromarray((a * 255.0).astype(np.uint8), "L"))
    hw, hh = head.size
    return head.resize((max(1, int(round(hw * height / hh))), height), Image.LANCZOS)


def save_bmp24(img: Image.Image, path: str) -> None:
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(path, "BMP")
    with open(path, "rb") as fh:
        head = fh.read(32)
    if head[:2] != b"BM":
        raise SystemExit(f"{path} 不是 BMP")
    bpp = int.from_bytes(head[28:30], "little")
    if bpp != 24:
        raise SystemExit(f"{path} 是 {bpp}bit，NSIS 要求 24bit")


def build_header(avatar: Image.Image) -> Image.Image:
    """150x57 页眉签名：右侧头像 + 左侧品牌字。底色必须与 MUI 页眉一致。"""
    w, h = 150, 57
    img = Image.new("RGB", (w, h), COL_PAPER)
    av = head_scaled(avatar, 36)
    ax = w - 10 - av.size[0]
    img.paste(av, (ax, (h - av.size[1]) // 2), av)
    draw = ImageDraw.Draw(img)
    font = load_font(12)
    draw.text((ax - 8, h // 2), "Abcyesno", font=font, fill=COL_INK, anchor="rm")
    return img


def build_sidebar(avatar: Image.Image) -> Image.Image:
    """164x314 完成页侧栏：头像 + 强调线 + 品牌字 + 副标。"""
    w, h = 164, 314
    img = Image.new("RGB", (w, h), COL_PAPER)
    av = head_scaled(avatar, 104)
    img.paste(av, ((w - av.size[0]) // 2, 62), av)
    draw = ImageDraw.Draw(img)
    bar_w, bar_h = 40, 2
    draw.rectangle(
        ((w - bar_w) // 2, 198, (w + bar_w) // 2, 198 + bar_h - 1), fill=COL_ACCENT
    )
    font_name = load_font(20)
    draw.text((w // 2, 212), "Abcyesno", font=font_name, fill=COL_INK, anchor="ma")
    font_sub = load_font(10)
    draw.text((w // 2, 242), "Agent OS", font=font_sub, fill=COL_MUTED, anchor="ma")
    return img


def main() -> int:
    if not os.path.exists(SRC):
        raise SystemExit(f"找不到素材：{SRC}")
    os.makedirs(BUILD, exist_ok=True)
    avatar = cutout(SRC)
    print(f"抠图完成，紧凑头像 {avatar.size[0]}x{avatar.size[1]}")

    header = build_header(avatar)
    sidebar = build_sidebar(avatar)
    save_bmp24(header, os.path.join(BUILD, "installerHeader.bmp"))
    save_bmp24(sidebar, os.path.join(BUILD, "installerSidebar.bmp"))
    print("写出 build/installerHeader.bmp (150x57 24bit)")
    print("写出 build/installerSidebar.bmp (164x314 24bit)")

    icon_sq = square_canvas(avatar)
    icon_png = icon_sq.resize((512, 512), Image.LANCZOS)
    icon_png.save(os.path.join(BUILD, "icon.png"), "PNG")
    print("写出 build/icon.png (512x512 RGBA)")

    sizes = [16, 24, 32, 48, 64, 128, 256]
    icon_sq.resize((256, 256), Image.LANCZOS).save(
        os.path.join(BUILD, "icon.ico"),
        "ICO",
        sizes=[(s, s) for s in sizes],
    )
    print(f"写出 build/icon.ico ({'/'.join(str(s) for s in sizes)})")

    if "--preview" in sys.argv:
        os.makedirs(PREVIEW, exist_ok=True)
        header.save(os.path.join(PREVIEW, "header.png"))
        sidebar.save(os.path.join(PREVIEW, "sidebar.png"))
        header.resize((600, 228), Image.NEAREST).save(os.path.join(PREVIEW, "header-x4.png"))
        sidebar.resize((328, 628), Image.NEAREST).save(os.path.join(PREVIEW, "sidebar-x2.png"))
        icon_png.save(os.path.join(PREVIEW, "icon-512.png"))
        icon_png.resize((32, 32), Image.LANCZOS).resize((256, 256), Image.NEAREST).save(
            os.path.join(PREVIEW, "icon-32px-x8.png")
        )
        print(f"预览图输出到 {PREVIEW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
