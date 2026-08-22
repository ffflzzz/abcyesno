// 把 chat 图标的 bbox 围绕"中心"放大，扩展其内容占比到 ~92%
const fs = require("fs");
const path = require("path");
const { PNG } = require("C:/Users/Administrator/.workbuddy/binaries/node/workspace/node_modules/pngjs");

const TARGET = 1024;
const f = "C:/Users/Administrator/Downloads/abcyesno-v8/abcyesno-v8/abcyesno-v8/src/assets/app-chat.png";
const png = PNG.sync.read(fs.readFileSync(f));

// 1) 找 bbox
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
const bbox = { minX, minY, maxX, maxY, w: maxX - minX + 1, h: maxY - minY + 1 };
console.log("original bbox:", bbox);

// 2) pad bbox 到正方形：以更长边为边长
const side = Math.max(bbox.w, bbox.h);
const padX = Math.floor((side - bbox.w) / 2);
const padY = Math.floor((side - bbox.h) / 2);
const srcX0 = bbox.minX - padX;
const srcY0 = bbox.minY - padY;
console.log(`square side=${side}, source crop (${srcX0},${srcY0}) ${side}x${side}`);

// 3) scale 到 ~92% canvas
const TARGET_RATIO = 0.92;
const scale = TARGET_RATIO * TARGET / side;
const newSize = Math.round(side * scale);
const off = Math.floor((TARGET - newSize) / 2);
console.log(`scale=${scale.toFixed(3)} → ${newSize}x${newSize}, padding(${off},${off})`);

const out = new PNG({ width: TARGET, height: TARGET });
for (let y = 0; y < newSize; y++) {
  for (let x = 0; x < newSize; x++) {
    const sx = Math.floor(srcX0 + x / scale);
    const sy = Math.floor(srcY0 + y / scale);
    if (sx < 0 || sx >= png.width || sy < 0 || sy >= png.height) continue;
    const sOff = (sy * png.width + sx) * 4;
    const dOff = ((y + off) * TARGET + (x + off)) * 4;
    out.data[dOff]   = png.data[sOff];
    out.data[dOff+1] = png.data[sOff+1];
    out.data[dOff+2] = png.data[sOff+2];
    out.data[dOff+3] = png.data[sOff+3];
  }
}

fs.writeFileSync(f, PNG.sync.write(out));
console.log("✓ wrote");
