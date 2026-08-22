// Global (cross-project) character library for the StudioWorkbench.
//
// Storage layout (under HERMES_HOME):
//   ~/.hermes_portable_data/manjucraft_agent/character_library/
//     ├── index.json            # metadata list of every card
//     └── seed/built_in.json    # read-only built-in seed (shipped with app)
//
// This is a pure-Node module consumed directly by the main process via the
// `studio-call` IPC handler (see main.js). We deliberately keep it in Node
// rather than Python so the existing Node-side studio routes stay cohesive.

const fs = require("fs");
const path = require("path");
const os = require("os");
const crypto = require("crypto");

function hermesHome() {
  return process.env.HERMES_HOME || path.join(os.homedir(), ".hermes_portable_data");
}

function libraryDir() {
  return path.join(hermesHome(), "manjucraft_agent", "character_library");
}

function indexPath() {
  return path.join(libraryDir(), "index.json");
}

// Generated (real) character images live here, one file per card id.
function imagesDir() {
  return path.join(libraryDir(), "images");
}

// True when the card has no real artwork yet: either nothing at all or one of
// the legacy SVG gradient placeholders shipped in the original seed.
function isPlaceholderUrl(url) {
  if (!url || typeof url !== "string") return true;
  return url.startsWith("data:image/svg");
}

// Detect the actual image format from magic bytes and rename the file to a
// matching extension (downloadMedia falls back to `.bin` when the remote URL
// carries none, and the sandboxed renderer's protocol handler relies on the
// extension for its Content-Type).
function normalizeImageExt(filePath) {
  let fd;
  try {
    fd = fs.openSync(filePath, "r");
    const buf = Buffer.alloc(12);
    fs.readSync(fd, buf, 0, 12, 0);
    let ext = null;
    if (buf[0] === 0x89 && buf[1] === 0x50) ext = ".png";
    else if (buf[0] === 0xff && buf[1] === 0xd8) ext = ".jpg";
    else if (
      buf[0] === 0x52 && buf[1] === 0x49 && buf[2] === 0x46 && buf[3] === 0x46 &&
      buf[8] === 0x57 && buf[9] === 0x45 && buf[10] === 0x42 && buf[11] === 0x50
    ) ext = ".webp";
    else if (buf[0] === 0x47 && buf[1] === 0x49 && buf[2] === 0x46) ext = ".gif";
    if (!ext) return filePath;
    const cur = path.extname(filePath).toLowerCase();
    if (cur === ext) return filePath;
    const fixed = filePath.slice(0, filePath.length - cur.length) + ext;
    fs.closeSync(fd);
    fd = null;
    fs.renameSync(filePath, fixed);
    return fixed;
  } catch (_) {
    return filePath;
  } finally {
    if (fd !== null && fd !== undefined) {
      try { fs.closeSync(fd); } catch (_) {}
    }
  }
}

function seedPath() {
  // Shipped with the app: <repo>/hermes-fork/.../manjucraft_agent/seed/built_in.json
  const rel = path.join(
    "hermes-fork",
    "skills",
    "langgraph_agents",
    "agents",
    "manjucraft_agent",
    "seed",
    "built_in.json"
  );
  // Resolve relative to the app resources dir when packaged, else repo root.
  const candidates = [
    path.join(libraryDir(), "seed", "built_in.json"),
    path.join(process.resourcesPath || "", "app", rel),
    path.join(__dirname, "..", "..", rel),
    path.join(__dirname, "..", "..", "..", rel),
  ];
  for (const c of candidates) {
    if (c && fs.existsSync(c)) return c;
  }
  return null;
}

// Fallback built-in seed (kept in sync with seed/built_in.json) so the library
// still seeds even if the JSON file is missing from the package.
const FALLBACK_SEED = [
  { name: "圆妹/清新少女", tags: ["清新少女", "女主", "现代", "青年", "逆袭"], style: "写实" },
  { name: "霸总/精英大佬", tags: ["霸总", "男主", "现代", "中年", "都市"], style: "写实" },
  { name: "温柔妈妈/慈爱妇女", tags: ["母亲", "女主", "家庭", "中年", "治愈"], style: "写实" },
  { name: "清冷千金/白鹤染主任", tags: ["千金", "女主", "现代", "青年", "豪门"], style: "写实" },
  { name: "古风男主", tags: ["古风", "男主", "古代", "青年", "武侠"], style: "古风" },
  { name: "古风女主", tags: ["古风", "女主", "古代", "青年", "言情"], style: "古风" },
  { name: "恶毒女配/白莲花", tags: ["反派", "女配", "现代", "青年", "阴谋"], style: "写实" },
  { name: "正派长辈/父亲", tags: ["长辈", "男配", "古代", "中年", "威严"], style: "古风" },
  { name: "偏激长辈/刻薄亲戚", tags: ["反派", "长辈", "现代", "中年", "偏见"], style: "写实" },
  { name: "反派长辈/刻薄亲", tags: ["反派", "长辈", "现代", "中年", "阴冷"], style: "写实" },
  { name: "生活务实老善良", tags: ["平民", "长辈", "现代", "中年", "邻家"], style: "写实" },
];

function loadIndex() {
  try {
    const raw = fs.readFileSync(indexPath(), "utf-8");
    const data = JSON.parse(raw);
    return Array.isArray(data) ? data : [];
  } catch (_) {
    return [];
  }
}

function saveIndex(list) {
  fs.mkdirSync(libraryDir(), { recursive: true });
  fs.writeFileSync(indexPath(), JSON.stringify(list, null, 2), "utf-8");
}

function makeId(name) {
  const h = crypto.createHash("sha1").update(name).digest("hex").slice(0, 12);
  return `char_${h}`;
}

// Seed the library from built_in.json the first time it is accessed.
function seedIfEmpty() {
  const existing = loadIndex();
  if (existing.length > 0) return existing;
  let seed = [];
  const sp = seedPath();
  if (sp) {
    try {
      seed = JSON.parse(fs.readFileSync(sp, "utf-8"));
    } catch (_) {
      seed = [];
    }
  }
  if (!Array.isArray(seed) || seed.length === 0) {
    seed = FALLBACK_SEED.map((c, i) => ({ ...c, id: `builtin_${i.toString().padStart(2, "0")}` }));
  }
  const now = Date.now();
  const cards = seed.map((c) => ({
    id: c.id || makeId(c.name),
    name: c.name,
    tags: c.tags || [],
    prompt: c.prompt || "",
    style: c.style || "",
    frontUrl: c.frontUrl || "",
    views: c.views || {},
    source: c.source || "builtin",
    createdAt: c.createdAt || now,
    lastUsedAt: c.lastUsedAt || 0,
    useCount: c.useCount || 0,
  }));
  saveIndex(cards);
  return cards;
}

function findByName(name) {
  const list = loadIndex();
  return list.find((c) => c.name === name) || null;
}

function findByViewNamePrefix() {
  return loadIndex();
}

// Upsert a card by name. `source` defaults to "generated:<project>".
function upsertCard(input) {
  const list = seedIfEmpty();
  const name = (input && input.name) || "";
  if (!name) throw new Error("upsertCard 需要 name");
  const now = Date.now();
  const idx = list.findIndex((c) => c.name === name);
  const card = {
    id: idx >= 0 ? list[idx].id : makeId(name),
    name,
    tags: input.tags && input.tags.length ? input.tags : idx >= 0 ? list[idx].tags : [],
    prompt: input.prompt || (idx >= 0 ? list[idx].prompt : ""),
    style: input.style || (idx >= 0 ? list[idx].style : ""),
    frontUrl: input.frontUrl || (idx >= 0 ? list[idx].frontUrl : ""),
    views: input.views || (idx >= 0 ? list[idx].views : {}),
    source: input.source || `generated:${input.project || "unknown"}`,
    createdAt: idx >= 0 ? list[idx].createdAt : now,
    lastUsedAt: idx >= 0 ? list[idx].lastUsedAt : 0,
    useCount: idx >= 0 ? list[idx].useCount : 0,
  };
  if (idx >= 0) list[idx] = card;
  else list.push(card);
  saveIndex(list);
  return card;
}

function touchUsed(id) {
  const list = loadIndex();
  const card = list.find((c) => c.id === id);
  if (!card) return null;
  card.lastUsedAt = Date.now();
  card.useCount = (card.useCount || 0) + 1;
  saveIndex(list);
  return card;
}

function getCard(id) {
  const list = loadIndex();
  return list.find((c) => c.id === id) || null;
}

// Persist a freshly generated real artwork for a card. Also drops any legacy
// SVG placeholder views so the UI never mixes generated + placeholder art.
function setCardImage(id, localPath) {
  const list = loadIndex();
  const card = list.find((c) => c.id === id);
  if (!card) return null;
  card.frontUrl = localPath;
  const views = card.views || {};
  const kept = {};
  for (const [k, v] of Object.entries(views)) {
    if (!isPlaceholderUrl(v)) kept[k] = v;
  }
  card.views = kept;
  card.imageGeneratedAt = Date.now();
  saveIndex(list);
  return card;
}

function listCards() {
  return seedIfEmpty();
}

module.exports = {
  libraryDir,
  imagesDir,
  indexPath,
  loadIndex,
  saveIndex,
  seedIfEmpty,
  findByName,
  findByViewNamePrefix,
  upsertCard,
  touchUsed,
  listCards,
  makeId,
  isPlaceholderUrl,
  normalizeImageExt,
  getCard,
  setCardImage,
};
