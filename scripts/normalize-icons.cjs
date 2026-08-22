// 把 ImageGen 输出的 1024x1024 PNG 归一化：背景透明 + 居中 pad 到 1024x1024
// 这里"背景"包括：
//   1) 纯白背景（RGB 都 >= 230）
//   2) 图标外的柔和阴影/光晕（明亮灰色 + RGB 接近 + >= 200）
const fs = require("fs");
const path = require("path");
const { PNG } = require("C:/Users/Administrator/.workbuddy/binaries/node/workspace/node_modules/pngjs");

const ROOT = "C:/Users/Administrator/Downloads/abcyesno-v8/abcyesno-v8/abcyesno-v8";
const TARGET = 1024;

function isBackground(r, g, b) {
  // 1) 全白
  if (r >= 230 && g >= 230 && b >= 230) return true;
  // 2) 明亮灰色（柔和阴影/光晕）：R≈G≈B 且 >= 195
  if (Math.abs(r - g) < 8 && Math.abs(g - b) < 8 && r >= 195) return true;
  return false;
}

function transparentizeBackground(png) {
  let cleared = 0;
  for (let i = 0; i < png.data.length; i += 4) {
    if (png.data[i + 3] > 0 && isBackground(png.data[i], png.data[i+1], png.data[i+2])) {
      png.data[i + 3] = 0;
      cleared++;
    }
  }
  return cleared;
}

function findBBox(png) {
  let minX = png.width, minY = png.height, maxX = -1, maxY = -1;
  for (let y = 0; y < png.height; y++) {
    for (let x = 0; x < png.width; x++) {
      if (png.data[(y * png.width + x) * 4 + 3] > 20) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }
  return maxX >= 0 ? { minX, minY, maxX, maxY, w: maxX - minX + 1, h: maxY - minY + 1 } : null;
}

function recenter(png, bbox) {
  const W = png.width;
  const out = new PNG({ width: TARGET, height: TARGET });
  // 强制统一缩放：以 bbox 较短边为基准 scale 到 ~0.88 * TARGET，长边可能 > TARGET
  // 但 CSS object-fit: contain 会自动等比缩放显示，所以源头可以略大；只要统一即可。
  const SHORT_TARGET_RATIO = 0.88; // 较短边目标占比
  const scale = SHORT_TARGET_RATIO * TARGET / Math.min(bbox.w, bbox.h);
  const newW = Math.round(bbox.w * scale);
  const newH = Math.round(bbox.h * scale);
  // 边界安全：clip 到 TARGET 内
  const offX = Math.floor((TARGET - newW) / 2);
  const offY = Math.floor((TARGET - newH) / 2);
  console.log(`  scale=${scale.toFixed(3)} (shorter-edge=0.88) → ${newW}x${newH}, padding(${offX},${offY})`);
  for (let y = 0; y < newH; y++) {
    for (let x = 0; x < newW; x++) {
      const sx = Math.floor(bbox.minX + x / scale);
      const sy = Math.floor(bbox.minY + y / scale);
      const sOff = (sy * W + sx) * 4;
      const dOff = ((y + offY) * TARGET + (x + offX)) * 4;
      out.data[dOff]   = png.data[sOff];
      out.data[dOff+1] = png.data[sOff + 1];
      out.data[dOff+2] = png.data[sOff + 2];
      out.data[dOff+3] = png.data[sOff + 3];
    }
  }
  return out;
}

function process(inFile, outFile) {
  console.log("processing " + path.basename(inFile));
  const png = PNG.sync.read(fs.readFileSync(inFile));
  const cleared = transparentizeBackground(png);
  console.log(`  background cleared: ${cleared} pixels`);
  const bbox = findBBox(png);
  if (!bbox) {
    console.log("  no content bbox — abort");
    return;
  }
  console.log(`  content bbox: ${bbox.w}x${bbox.h} at (${bbox.minX},${bbox.minY})`);
  const out = recenter(png, bbox);
  fs.writeFileSync(outFile, PNG.sync.write(out));
  console.log("  ✓ wrote " + outFile);
}

process(
  path.join(ROOT, "gen/A_flat_1024x1024_PNG_app_launc_2026-08-22T04-37-20.png"),
  path.join(ROOT, "src/assets/app-chat.png")
);
process(
  path.join(ROOT, "src/assets/app-manju.png"),
  path.join(ROOT, "src/assets/app-manju.png")
);
console.log("done");
