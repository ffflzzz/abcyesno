// Generate the OpenRouter launcher icon (1024x1024 PNG) from OpenRouter's OWN
// brand assets — no hand-drawn approximation:
//   • tile silhouette : alpha channel of https://openrouter.ai/favicon/glyph.png
//                       (their official rounded-square app tile, 512x512)
//   • mark            : the `st-glyph` path from
//                       https://openrouter.ai/brand/v2/openrouter-dark.svg
//   • colors          : exactly what openrouter.ai uses — tile #03080A,
//                       mark #C8FF00 (their brand lime, as on
//                       https://openrouter.ai/apple-touch-icon.png)
//
// Output: src/assets/openrouter.png
//
// Usage:  node scripts/gen_openrouter_icon.cjs <path-to-glyph.png>
// (glyph.png is the only input that is not inline; it is their official tile.)
const sharp = require("sharp");
const path = require("path");
const fs = require("fs");

const S = 1024;

// ── 1. Tile from the official glyph.png alpha ─────────────────────────────
// Their tile is a superellipse, not a circular rounded rect — reusing their
// own alpha mask (upscaled) keeps the exact corner geometry.
// joinChannel (not composite/dest-in) because a greyscale PNG's alpha is 1
// everywhere; the silhouette only survives as an explicit alpha channel.
async function tileFromOfficialMask(sourcePng) {
  const alpha = await sharp(sourcePng)
    .ensureAlpha()
    .extractChannel("alpha")
    .resize(S, S, { kernel: "lanczos3" })
    .raw()
    .toBuffer();

  const rgb = Buffer.alloc(S * S * 3);
  for (let i = 0; i < S * S; i++) {
    rgb[i * 3] = 3;
    rgb[i * 3 + 1] = 8;
    rgb[i * 3 + 2] = 10;
  }

  return sharp(rgb, { raw: { width: S, height: S, channels: 3 } })
    .joinChannel(alpha, { raw: { width: S, height: S, channels: 1 } })
    .png()
    .toBuffer();
}

// ── 2. Official glyph path (extracted verbatim from openrouter-dark.svg) ──
// Tight viewBox = the path's own bounding box, so centring is exact.
const GLYPH_PATH =
  "M303.9475,17.19926c42.79734,0,77.48933,34.69327,77.48933,77.48933s-34.69199,77.48933-77.48933,77.48933" +
  "l76.86166,76.86244c9.76367,9.76313,2.84903,26.45667-10.95697,26.45667h-220.88335" +
  "c-71.32686,0-129.14889-57.82202-129.14889-129.14889S77.64197,17.19926,148.96884,17.19926h154.97866Z" +
  "M148.96884,68.85881c-42.79607,0-77.48933,34.69327-77.48933,77.48933s34.69327,77.48933,77.48933,77.48933," +
  "77.48933-34.69327,77.48933-77.48933-34.69327-77.48933-77.48933-77.48933Z";

const GB = { x: 19.81995, y: 17.19926, w: 361.61688, h: 258.29777 };
// Mark occupies the same share of the tile as in their own apple-touch-icon:
// measured from apple.png → width 75.6% of the tile, centred.
const MARK_W = Math.round(S * 0.7556);
const MARK_H = Math.round((MARK_W * GB.h) / GB.w);
const MARK_X = Math.round((S - MARK_W) / 2);
const MARK_Y = Math.round((S - MARK_H) / 2);

function glyphSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${MARK_W}" height="${MARK_H}"
     viewBox="${GB.x} ${GB.y} ${GB.w} ${GB.h}">
    <path d="${GLYPH_PATH}" fill="#C8FF00"/>
  </svg>`;
}

async function main() {
  const src = process.argv[2];
  if (!src || !fs.existsSync(src)) {
    console.error("usage: node scripts/gen_openrouter_icon.cjs <official-glyph.png>");
    process.exit(1);
  }

  // Tile painted in the site's tile color, carrying their own silhouette.
  const tile = await tileFromOfficialMask(src);

  const out = await sharp(tile)
    .composite([{ input: Buffer.from(glyphSvg()), left: MARK_X, top: MARK_Y }])
    .png({ compressionLevel: 9 })
    .toBuffer();

  const dest = path.resolve(__dirname, "../src/assets/openrouter.png");
  fs.writeFileSync(dest, out);
  const meta = await sharp(dest).metadata();
  console.log(`wrote ${dest} ${meta.width}x${meta.height} (${(out.length / 1024).toFixed(1)} kB)`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
