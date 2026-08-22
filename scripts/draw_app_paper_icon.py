# Draw src/assets/app-paper.png — flat launcher icon for paper_rewriter_agent.
# Style: indigo->violet gradient rounded square + white open book + pen.
from PIL import Image, ImageDraw
import math, os

S = 2048  # supersample canvas
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

C1 = (79, 70, 229)    # #4f46e5 indigo
C2 = (124, 58, 237)   # #7c3aed violet
R = int(S * 0.225)    # corner radius ~ matches app grid squircle feel

def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

# --- gradient rounded-square background ---
grad = Image.new("RGBA", (S, S), (0, 0, 0, 0))
gd = ImageDraw.Draw(grad)
for y in range(S):
    gd.line([(0, y), (S, y)], fill=lerp(C1, C2, y / S) + (255,))
mask = Image.new("L", (S, S), 0)
md = ImageDraw.Draw(mask)
md.rounded_rectangle([0, 0, S - 1, S - 1], radius=R, fill=255)
img.paste(grad, (0, 0), mask)

WHITE = (255, 255, 255, 255)
W_SOFT = (238, 236, 255, 255)  # slightly tinted page white

cx, cy = S / 2, S / 2 - S * 0.03
bw = S * 0.30          # book HALF-width per side
bh = S * 0.36          # book height
gap = S * 0.035        # center spine gap
page_drop = S * 0.055  # how much the outer edge sits lower than the spine top curve

# --- open book: two mirrored pages with a gentle top curve ---
for side in (-1, 1):
    x0 = cx + side * gap            # spine edge
    x1 = cx + side * bw             # outer edge
    pts_top = []
    N = 48
    for i in range(N + 1):
        t = i / N                   # 0 at spine -> 1 at outer edge
        x = x0 + (x1 - x0) * t
        # top edge dips from spine peak down to outer edge
        y = cy - bh / 2 + page_drop * (t ** 1.6)
        pts_top.append((x, y))
    pts_bottom = []
    for i in range(N + 1):
        t = i / N
        x = x0 + (x1 - x0) * t
        # bottom edge mirrors with a slight upward bow toward outer corner
        y = cy + bh / 2 - page_drop * 0.55 * (t ** 2.0)
        pts_bottom.append((x, y))
    d.polygon(pts_top + pts_bottom[::-1], fill=W_SOFT)
    # subtle page fold lines on each side
    for k in (1, 2):
        off = S * 0.045 * k
        fx0 = cx + side * (gap + off)
        fx1 = cx + side * (bw - S * 0.06)
        fy = cy + bh * 0.28
        d.line([(fx0, fy), (fx1, fy)], fill=(203, 199, 240, 160), width=int(S * 0.008))

# --- spine shadow between pages ---
d.line([(cx, cy - bh / 2 - S*0.01), (cx, cy + bh / 2 - S * 0.02)],
       fill=(180, 174, 226, 200), width=int(S * 0.010))

# --- fountain pen nib overlapping lower-right of the book ---
pen_len = S * 0.30
pen_w = S * 0.115
ang = math.radians(-38)
px, py = cx + S * 0.17, cy + S * 0.26  # nib tip position

ux, uy = math.cos(ang), math.sin(ang)      # along pen (tip -> tail)
vx, vy = -uy, ux                           # perpendicular

def p(t, w):
    return (px + ux * t + vx * w, py + uy * t + vy * w)

nib_l = pen_len * 0.42
tail_l = pen_len
body_pts = [
    p(0, 0),                                  # tip
    p(nib_l * 0.55, -pen_w * 0.55),           # flaring to body width
    p(tail_l, -pen_w * 0.62),
    p(tail_l + S*0.03, 0),
    p(tail_l, pen_w * 0.62),
    p(nib_l * 0.55, pen_w * 0.55),
]
d.polygon(body_pts, fill=(49, 46, 129, 235))  # deep indigo body
# nib highlight slit
slit_end = p(nib_l * 0.9, 0)
d.line([(px, py), slit_end], fill=WHITE, width=int(S * 0.012))
# tail band
band_c = p(tail_l * 0.86, 0)
bw2a = p(tail_l * 0.86, -pen_w * 0.66)
bw2b = p(tail_l * 0.86, pen_w * 0.66)
d.line([bw2a, bw2b], fill=WHITE, width=int(S * 0.016))

out = os.path.join(os.path.dirname(__file__), "..", "src", "assets", "app-paper.png")
img.resize((512, 512), Image.LANCZOS).save(out, "PNG")
print("saved", out)
