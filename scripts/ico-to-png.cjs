// 把 excalidraw.ico 解析 + 取最大图层 + 转成 1024x1024 透明 PNG，居中 pad
// .ico 文件：6 字节 header + N×16 字节目录 + 数据（PNG 或 BMP）
const fs = require("fs");
const path = require("path");
const { PNG } = require("C:/Users/Administrator/.workbuddy/binaries/node/workspace/node_modules/pngjs");

const ROOT = "C:/Users/Administrator/Downloads/abcyesno-v8/abcyesno-v8/abcyesno-v8";
const icoPath = path.join(ROOT, "src/assets/excalidraw.ico");
const outPath = path.join(ROOT, "src/assets/excalidraw.png");

const buf = fs.readFileSync(icoPath);
const reserved = buf.readUInt16LE(0);
const type = buf.readUInt16LE(2);
const count = buf.readUInt16LE(4);
console.log(`ico header: reserved=${reserved}, type=${type}, count=${count}`);

// 列目录
const entries = [];
for (let i = 0; i < count; i++) {
  const off = 6 + i * 16;
  const w = buf.readUInt8(off) || 256;  // 0 = 256
  const h = buf.readUInt8(off+1) || 256; // 0 = 256
  const palette = buf.readUInt8(off+2);
  const resv = buf.readUInt8(off+3);
  const planes = buf.readUInt16LE(off+4);
  const bpp = buf.readUInt16LE(off+6);
  const size = buf.readUInt32LE(off+8);
  const datOff = buf.readUInt32LE(off+12);
  entries.push({ w, h, palette, planes, bpp, size, datOff });
}

// 找最大的图层（按 w*h 排）
entries.sort((a, b) => (b.w * b.h) - (a.w * a.h));
console.log("top entries:", entries.slice(0, 5).map(e => `${e.w}x${e.h} bpp=${e.bpp} size=${e.size}`));

const best = entries[0];
const data = buf.slice(best.datOff, best.datOff + best.size);
console.log(`selected: ${best.w}x${best.h}, bpp=${best.bpp}, size=${best.size}`);

// 检查数据前 8 字节判断是不是 PNG
const isPng = data[0] === 0x89 && data[1] === 0x50 && data[2] === 0x4E && data[3] === 0x47;
console.log("data is PNG?", isPng);

let srcPng;
if (isPng) {
  srcPng = PNG.sync.read(data);
} else {
  // BMP DIB - 解 BPP 格式
  // 跳过：除非真的不是 PNG；这种情况下没装 sharp/jimp 比较复杂，先尝试 pngjs 报错回退到 OS 命令 imagemagick
  throw new Error(`unsupported non-PNG ICO entry, bpp=${best.bpp}. Use ImageMagick or sharp.`);
}
console.log(`decoded: ${srcPng.width}x${srcPng.height}`);

// 检查 alpha 通道：通常 ICO 里的 PNG 是 BGRA 或 RGBA
console.log("first pixel: RGBA =", srcPng.data[0], srcPng.data[1], srcPng.data[2], srcPng.data[3]);

// 居中 pad 到 1024×1024（取 bbox of opaque pixel）
function findBBox(png, alphaThreshold = 20) {
  let minX = png.width, minY = png.height, maxX = -1, maxY = -1;
  for (let y = 0; y < png.height; y++) {
    for (let x = 0; x < png.width; x++) {
      if (png.data[(y * png.width + x) * 4 + 3] > alphaThreshold) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }
  return maxX >= 0 ? { minX, minY, maxX, maxY, w: maxX - minX + 1, h: maxY - minY + 1 } : null;
}

const bbox = findBBox(srcPng);
if (!bbox) {
  console.log("no opaque pixels in source — aborting");
  process.exit(1);
}
console.log(`bbox of opaque (raw): ${bbox.w}x${bbox.h} at (${bbox.minX},${bbox.minY})`);

// ICO 通常有白色背景。透明化白色后重新算 bbox，让图标真正居中
function isBg(r, g, b) {
  if (r >= 230 && g >= 230 && b >= 230) return true;
  if (Math.abs(r-g) < 8 && Math.abs(g-b) < 8 && r >= 195) return true;
  return false;
}
let cleared = 0;
for (let i = 0; i < srcPng.data.length; i += 4) {
  if (srcPng.data[i+3] > 0 && isBg(srcPng.data[i], srcPng.data[i+1], srcPng.data[i+2])) {
    srcPng.data[i + 3] = 0;
    cleared++;
  }
}
console.log(`transparentized bg: ${cleared}`);

const bbox2 = findBBox(srcPng);
if (!bbox2) {
  console.log("no opaque after bg removal — aborting");
  process.exit(1);
}
console.log(`bbox of icon: ${bbox2.w}x${bbox2.h} at (${bbox2.minX},${bbox2.minY})`);

const TARGET = 1024;
// 让缩放后的图标占画布的 ~88%（留 ~6% 透明 padding on each side，跟另两个图标一致）
const scale = Math.min((TARGET * 0.88) / bbox2.w, (TARGET * 0.88) / bbox2.h);
const newW = Math.round(bbox2.w * scale);
const newH = Math.round(bbox2.h * scale);
const offX = Math.floor((TARGET - newW) / 2);
const offY = Math.floor((TARGET - newH) / 2);
console.log(`scale=${scale.toFixed(3)} → ${newW}x${newH}, padding (${offX},${offY})`);

const out = new PNG({ width: TARGET, height: TARGET });
for (let y = 0; y < newH; y++) {
  for (let x = 0; x < newW; x++) {
    const sx = Math.floor(bbox2.minX + x / scale);
    const sy = Math.floor(bbox2.minY + y / scale);
    const sOff = (sy * srcPng.width + sx) * 4;
    const dOff = ((y + offY) * TARGET + (x + offX)) * 4;
    out.data[dOff]   = srcPng.data[sOff];
    out.data[dOff+1] = srcPng.data[sOff + 1];
    out.data[dOff+2] = srcPng.data[sOff + 2];
    out.data[dOff+3] = srcPng.data[sOff + 3];
  }
}

fs.writeFileSync(outPath, PNG.sync.write(out));
console.log("✓ wrote " + outPath);
