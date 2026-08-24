// Generate Apple-style launcher icons at 1024x1024 from inline SVG.
// Foreground is designed DIRECTLY in 1024-space (no scale math), so each
// element is properly centered inside the squircle.
const sharp = require("sharp");
const path = require("path");

const OUT = path.resolve(__dirname, "../src/assets");
const S = 1024;
const R = 243; // iOS-style squircle corner radius

// Background squircle + same-hue vertical gradient.  Used by all three icons
// so they share the same container language.
function bg(id, top, bottom) {
  return `
    <defs>
      <linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${top}"/>
        <stop offset="100%" stop-color="${bottom}"/>
      </linearGradient>
    </defs>
    <rect width="${S}" height="${S}" rx="${R}" fill="url(#${id})"/>`;
}

// ── 1. 对话 (Chat) — speech bubble on blue gradient ────────────────────────
// Bubble body: rounded rect 580×380, centered horizontally, vertically
// shifted slightly up so the tail fits below.
// Tail: triangle from the bottom of the body, pointing down-left.
const chat = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${S} ${S}" width="${S}" height="${S}">
  ${bg("g", "#34C8FF", "#007AFF")}
  <path d="M 222 400
           Q 222 320 302 320
           L 722 320
           Q 802 320 802 400
           L 802 620
           Q 802 700 722 700
           L 462 700
           L 332 808
           L 380 700
           L 302 700
           Q 222 700 222 620 Z"
        fill="#FFFFFF"/>
</svg>`;

// ── 2. 漫剧go (Drama) — clapperboard on red gradient ──────────────────────
// Body: 664×320 at y=460–780
// Hinge (clapper): 664×180 at y=280–460
// Diagonal stripes: 5 parallelograms slanting down-right across the hinge,
// clipped to the hinge rect via <clipPath>.
const drama = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${S} ${S}" width="${S}" height="${S}">
  ${bg("g", "#FF6B6B", "#C20A18")}
  <defs>
    <clipPath id="hinge">
      <rect x="180" y="280" width="664" height="180" rx="24"/>
    </clipPath>
  </defs>
  <g fill="#FFFFFF">
    <rect x="180" y="460" width="664" height="320" rx="32"/>
    <rect x="180" y="280" width="664" height="180" rx="24"/>
  </g>
  <g fill="url(#g)" clip-path="url(#hinge)">
    <polygon points="180,280 280,280 340,460 240,460"/>
    <polygon points="321,280 421,280 481,460 381,460"/>
    <polygon points="462,280 562,280 622,460 522,460"/>
    <polygon points="603,280 703,280 763,460 663,460"/>
    <polygon points="744,280 844,280 904,460 804,460"/>
  </g>
</svg>`;

// ── 3. 论文重写 (Paper Rewrite) — document + pen on orange gradient ──────
// Document: pentagon with top-right corner cut (folded-paper silhouette)
// Text lines: 3 rounded rects
// Pen: white body + dark eraser + black clip, rotated -30°, bottom-right
const paper = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${S} ${S}" width="${S}" height="${S}">
  ${bg("g", "#FFD60A", "#FF8A00")}
  <g fill="#FFFFFF">
    <path d="M 240 240
             L 620 240
             L 760 380
             L 760 790
             L 240 790 Z"/>
  </g>
  <g fill="#FF8A00">
    <rect x="290" y="440" width="380" height="32" rx="16"/>
    <rect x="290" y="500" width="420" height="32" rx="16"/>
    <rect x="290" y="560" width="320" height="32" rx="16"/>
  </g>
  <g transform="translate(620 700) rotate(-30)">
    <rect x="0" y="0" width="280" height="60" rx="20" fill="#FFFFFF"/>
    <polygon points="280,0 360,30 280,60" fill="#FFFFFF"/>
    <polygon points="0,0 50,30 0,60" fill="#3D3D3D"/>
    <rect x="20" y="10" width="16" height="40" rx="4" fill="#1a1a1a"/>
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
