// Generate Apple-style launcher icons at 1024x1024 from inline SVG.
const sharp = require("sharp");
const fs = require("fs");
const path = require("path");

const OUT = path.resolve(__dirname, "../src/assets");

// Apple-style squircle icon template.
// Each icon: same-size squircle container (243px rounded corners ~ iOS 14/16),
// single top->bottom same-hue gradient, pure-white foreground, no stroke/decoration.
const S = 1024;
const R = 243; // corner radius

function squircleBg(gradId, top, bottom) {
  return `
    <defs>
      <linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${top}"/>
        <stop offset="100%" stop-color="${bottom}"/>
      </linearGradient>
    </defs>
    <rect x="0" y="0" width="${S}" height="${S}" rx="${R}" fill="url(#${gradId})"/>`;
}

// scale helper: preview used 160px box; multiply by 6.4 to reach 1024
const k = 6.4;
const sx = (n) => Math.round(n * k);
const sy = (n) => Math.round(n * k);

// --- 1. 对话 (Chat) — speech bubble on blue gradient ---
const chat = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${S} ${S}" width="${S}" height="${S}">
  ${squircleBg("chat", "#34C8FF", "#007AFF")}
  <path d="M ${sx(110)} ${sy(158)} Q ${sx(110)} ${sy(138)} ${sx(130)} ${sy(138)} L ${sx(170)} ${sy(138)} Q ${sx(190)} ${sy(138)} ${sx(190)} ${sy(158)} L ${sx(190)} ${sy(188)} Q ${sx(190)} ${sy(208)} ${sx(170)} ${sy(208)} L ${sx(144)} ${sy(208)} L ${sx(130)} ${sy(224)} L ${sx(134)} ${sy(208)} L ${sx(130)} ${sy(208)} Q ${sx(110)} ${sy(208)} ${sx(110)} ${sy(188)} Z" fill="#FFFFFF"/>
</svg>`;

// --- 2. 漫剧go (Drama) — clapperboard on red gradient ---
const drama = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${S} ${S}" width="${S}" height="${S}">
  ${squircleBg("drama", "#FF6B6B", "#C20A18")}
  <g fill="#FFFFFF">
    <rect x="${sx(288)}" y="${sy(158)}" width="${sx(104)}" height="${sx(78)}" rx="${sx(8)}"/>
    <rect x="${sx(286)}" y="${sy(138)}" width="${sx(108)}" height="${sy(26)}" rx="${sx(6)}"/>
  </g>
  <g fill="url(#drama)" opacity="0.95">
    <polygon points="${sx(288)},${sy(138)} ${sx(308)},${sy(138)} ${sx(320)},${sy(164)} ${sx(300)},${sy(164)}"/>
    <polygon points="${sx(324)},${sy(138)} ${sx(344)},${sy(138)} ${sx(356)},${sy(164)} ${sx(336)},${sy(164)}"/>
    <polygon points="${sx(360)},${sy(138)} ${sx(380)},${sy(138)} ${sx(392)},${sy(164)} ${sx(372)},${sy(164)}"/>
  </g>
</svg>`;

// --- 3. 论文重写 (Paper Rewrite) — doc + pen on orange gradient ---
const paper = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${S} ${S}" width="${S}" height="${S}">
  ${squircleBg("paper", "#FFD60A", "#FF8A00")}
  <g fill="#FFFFFF">
    <path d="M ${sx(478)} ${sy(140)} L ${sx(538)} ${sy(140)} L ${sx(562)} ${sy(164)} L ${sx(562)} ${sy(220)} L ${sx(478)} ${sy(220)} Z"/>
    <rect x="${sx(490)}" y="${sy(160)}" width="${sx(50)}" height="${sx(6)}" rx="${sx(3)}"/>
    <rect x="${sx(490)}" y="${sy(174)}" width="${sx(60)}" height="${sx(6)}" rx="${sx(3)}"/>
    <rect x="${sx(490)}" y="${sy(188)}" width="${sx(40)}" height="${sx(6)}" rx="${sx(3)}"/>
  </g>
  <g transform="translate(${sx(528)} ${sy(198)}) rotate(-30)">
    <rect x="0" y="0" width="${sx(46)}" height="${sx(10)}" rx="${sx(3)}" fill="#FFFFFF"/>
    <polygon points="${sx(46)},0 ${sx(60)},${sx(5)} ${sx(46)},${sx(10)}" fill="#FFFFFF"/>
    <polygon points="0,0 ${sx(8)},${sx(5)} 0,${sx(10)}" fill="#3D3D3D"/>
    <rect x="${sx(2)}" y="${sx(2)}" width="${sx(4)}" height="${sx(6)}" rx="${sx(1)}" fill="#1a1a1a"/>
  </g>
</svg>`;

const targets = [
  { name: "app-chat.png", svg: chat },
  { name: "app-manju.png", svg: drama },
  { name: "app-paper.png", svg: paper },
];

(async () => {
  for (const t of targets) {
    const buf = Buffer.from(t.svg);
    const out = path.join(OUT, t.name);
    await sharp(buf)
      .resize(S, S, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
      .png()
      .toFile(out);
    console.log("wrote", t.name);
  }
  console.log("done");
})().catch((e) => {
  console.error("ERROR", e);
  process.exit(1);
});
