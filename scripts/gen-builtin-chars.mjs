// Generate seed/built_in.json for the global Character Library.
// Each built-in card ships with an inline SVG placeholder (dataUri) as its
// front image so the library works out-of-the-box without burning image-model
// quota. "应用至画布" later feeds `prompt` straight to Agnes.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(
  __dirname,
  "..",
  "hermes-fork",
  "skills",
  "langgraph_agents",
  "agents",
  "manjucraft_agent",
  "seed",
  "built_in.json"
);

// Palette per card — gives the placeholder grid a bit of variety.
const PALETTE = [
  ["#ff8fab", "#ffd6e0"],
  ["#7c83fd", "#b8c0ff"],
  ["#ffb085", "#ffe0c7"],
  ["#8fd9c4", "#d4f5ec"],
  ["#c08cf0", "#e7d4ff"],
  ["#f0a8c0", "#ffd9e6"],
  ["#9fb8ff", "#d4e0ff"],
  ["#ffcf8f", "#fff0d4"],
  ["#a8d8a8", "#dff5df"],
  ["#ff9f9f", "#ffd6d6"],
  ["#b0b8c4", "#e2e8f0"],
];

function svgDataUri(name, c1, c2) {
  const safe = name.replace(/[<>&]/g, "");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="400" viewBox="0 0 300 400">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${c1}"/>
      <stop offset="1" stop-color="${c2}"/>
    </linearGradient>
  </defs>
  <rect width="300" height="400" fill="url(#g)"/>
  <circle cx="150" cy="150" r="70" fill="rgba(255,255,255,0.55)"/>
  <rect x="80" y="220" width="140" height="150" rx="70" fill="rgba(255,255,255,0.45)"/>
  <text x="150" y="395" font-family="sans-serif" font-size="20" fill="rgba(0,0,0,0.55)" text-anchor="middle">${safe}</text>
</svg>`;
  return "data:image/svg+xml;utf8," + encodeURIComponent(svg);
}

const SEED = [
  {
    name: "圆妹/清新少女",
    tags: ["清新少女", "女主", "现代", "青年", "逆袭"],
    prompt:
      "清新少女角色设定图，圆脸大眼，齐肩短发，穿着浅色连衣裙，阳光自然的妆容，全身立绘，二次元写实风格，高细节，统一风格",
    style: "写实",
  },
  {
    name: "霸总/精英大佬",
    tags: ["霸总", "男主", "现代", "中年", "都市"],
    prompt:
      "精英霸道总裁角色设定图，五官立体，西装革履，气场强大，短发利落，成熟男性，全身立绘，写实风格，高细节，统一风格",
    style: "写实",
  },
  {
    name: "温柔妈妈/慈爱妇女",
    tags: ["母亲", "女主", "家庭", "中年", "治愈"],
    prompt:
      "温柔母亲角色设定图，慈爱笑容，中等长度微卷发，居家温婉服饰，亲切中年女性，全身立绘，写实风格，高细节，统一风格",
    style: "写实",
  },
  {
    name: "清冷千金/白鹤染主任",
    tags: ["千金", "女主", "现代", "青年", "豪门"],
    prompt:
      "清冷豪门千金角色设定图，气质疏离，长直发，高级定制礼服，冷白皮，高傲青年女性，全身立绘，写实风格，高细节，统一风格",
    style: "写实",
  },
  {
    name: "古风男主",
    tags: ["古风", "男主", "古代", "青年", "武侠"],
    prompt:
      "古风武侠男主角设定图，束发长袍，剑眉星目，英气青年男子，水墨古风，全身立绘，高细节，统一风格",
    style: "古风",
  },
  {
    name: "古风女主",
    tags: ["古风", "女主", "古代", "青年", "言情"],
    prompt:
      "古风言情女主角设定图，云鬓花颜，襦裙飘逸，温婉秀丽青年女子，水墨古风，全身立绘，高细节，统一风格",
    style: "古风",
  },
  {
    name: "恶毒女配/白莲花",
    tags: ["反派", "女配", "现代", "青年", "阴谋"],
    prompt:
      "伪善白莲花反派女配角设定图，表面柔弱内心算计，精致妆容，名媛服饰，青年女性，全身立绘，写实风格，高细节，统一风格",
    style: "写实",
  },
  {
    name: "正派长辈/父亲",
    tags: ["长辈", "男配", "古代", "中年", "威严"],
    prompt:
      "正派威严长辈角色设定图，中年男子，长须正冠，忠厚威严，古风服饰，全身立绘，水墨古风，高细节，统一风格",
    style: "古风",
  },
  {
    name: "偏激长辈/刻薄亲戚",
    tags: ["反派", "长辈", "现代", "中年", "偏见"],
    prompt:
      "偏激刻薄亲戚角色设定图，面容刻薄，中年女性，市井打扮，算计表情，全身立绘，写实风格，高细节，统一风格",
    style: "写实",
  },
  {
    name: "反派长辈/刻薄亲",
    tags: ["反派", "长辈", "现代", "中年", "阴冷"],
    prompt:
      "阴冷反派长辈角色设定图，眼神阴鸷，中年男性，权势打扮，压迫感，全身立绘，写实风格，高细节，统一风格",
    style: "写实",
  },
  {
    name: "生活务实老善良",
    tags: ["平民", "长辈", "现代", "中年", "邻家"],
    prompt:
      "生活务实善良长辈角色设定图，朴实笑容，中年男性，家常衣着，邻家亲切感，全身立绘，写实风格，高细节，统一风格",
    style: "写实",
  },
];

const out = SEED.map((c, i) => {
  const [c1, c2] = PALETTE[i % PALETTE.length];
  return {
    id: `builtin_${i.toString().padStart(2, "0")}`,
    name: c.name,
    tags: c.tags,
    prompt: c.prompt,
    style: c.style || "",
    frontUrl: svgDataUri(c.name, c1, c2),
    views: { 正面: svgDataUri(c.name, c1, c2) },
    source: "builtin",
    createdAt: 0,
    lastUsedAt: 0,
    useCount: 0,
  };
});

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(out, null, 2), "utf-8");
console.log(`wrote ${out.length} built-in characters -> ${OUT}`);
