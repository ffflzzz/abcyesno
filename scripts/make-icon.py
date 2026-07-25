from PIL import Image, ImageDraw, ImageFont
import os

size = 256
img = Image.new('RGBA', (size, size), (15, 20, 25, 255))
draw = ImageDraw.Draw(img)

# Gradient background
for y in range(size):
    r = int(15 + (79 - 15) * y / size)
    g = int(20 + (140 - 20) * y / size)
    b = int(25 + (255 - 25) * y / size)
    draw.line([(0, y), (size, y)], fill=(r, g, b, 255))

# Rounded rectangle card
card = (40, 50, 216, 206)
draw.rounded_rectangle(card, radius=30, fill=(22, 27, 34, 230), outline=(79, 140, 255, 255), width=4)

# Draw letter A
try:
    font = ImageFont.truetype("arial.ttf", 130)
except Exception:
    font = ImageFont.load_default()
bbox = draw.textbbox((0, 0), "A", font=font)
text_w = bbox[2] - bbox[0]
text_h = bbox[3] - bbox[1]
x = (size - text_w) // 2
y = (size - text_h) // 2 - 10
draw.text((x, y), "A", font=font, fill=(230, 237, 243, 255))

os.makedirs("build", exist_ok=True)
# Save multiple sizes in ico
img.save("build/icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
print("build/icon.ico created")
