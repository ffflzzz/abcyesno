// 把 ImageGen 输出右下角的"云图沃克"水印涂成透明
const fs = require("fs");
const path = require("path");
const { PNG } = require("C:/Users/Administrator/.workbuddy/binaries/node/workspace/node_modules/pngjs");

const ROOT = "C:/Users/Administrator/Downloads/abcyesno-v8/abcyesno-v8/abcyesno-v8";
const files = [
  path.join(ROOT, "gen/A_flat_1024x1024_PNG_app_launc_2026-08-22T04-37-20.png"),
  path.join(ROOT, "src/assets/app-manju.png"),
];

function findWatermarkBounds(data, W, H) {
  const x0 = W - 250, y0 = H - 130, x1 = W, y1 = H;
  let minX = x1, minY = y1, maxX = -1, maxY = -1;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      if (data[(y * W + x) * 4 + 3] > 30) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < 0) return null;
  return {
    x0: Math.max(0, minX - 4), y0: Math.max(0, minY - 4),
    x1: Math.min(W - 1, maxX + 4), y1: Math.min(H - 1, maxY + 4),
  };
}

for (const f of files) {
  console.log("processing " + path.basename(f));
  const buf = fs.readFileSync(f);
  const png = PNG.sync.read(buf);
  const W = png.width, H = png.height;
  console.log("  " + W + "x" + H);
  const box = findWatermarkBounds(png.data, W, H);
  if (!box) {
    console.log("  no watermark");
    continue;
  }
  console.log(`  watermark @ x=[${box.x0}..${box.x1}] y=[${box.y0}..${box.y1}] (${box.x1-box.x0+1}x${box.y1-box.y0+1})`);
  for (let y = box.y0; y <= box.y1; y++) {
    for (let x = box.x0; x <= box.x1; x++) {
      const off = (y * W + x) * 4;
      png.data[off] = 0; png.data[off+1] = 0; png.data[off+2] = 0; png.data[off+3] = 0;
    }
  }
  fs.writeFileSync(f, PNG.sync.write(png));
  console.log("  ✓ saved " + path.basename(f));
}
