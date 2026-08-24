// One-shot script: replace hard-coded border-radius values in
// src/styles/index.css with --radius-* token references.
//
// Visual-hierarchy mapping (numbers <50 → a token; round/percent values
// stay literal so circles stay circles):
//
//   px value bucket          token
//   ------------------------ ----------------------
//   2px                      --radius-xs
//   4px, 5px                 --radius-xs
//   6px, 8px, 10px           --radius-sm
//   12px, 13px, 14px         --radius-md
//   16px                     --radius-lg
//   50% or 999/9999px        --radius-pill
//
// Handles three syntactic forms:
//   border-radius:           4px;
//   border-radius:           4px 8px;
//   border-radius:           4px 8px 12px 16px;
//   border-top-left-radius:  4px;
//   (etc for top/bottom/left/right)
//
// Idempotent: if a `border-radius: var(--radius-X)` already exists it's left
// alone. Re-running never double-applies.

const fs = require("fs");
const path = require("path");

const TARGET = path.resolve(__dirname, "..", "src", "styles", "index.css");
const css = fs.readFileSync(TARGET, "utf8");

const NUM_TOKEN = (() => {
  const map = new Map();
  const set = (px, tok) => {
    for (const n of px) map.set(n, tok);
  };
  set([2], "xs");
  set([3], "xs");
  set([4, 5], "xs");
  set([6, 7, 8, 9, 10], "sm");
  set([12, 13, 14], "md");
  set([16, 18], "lg");
  return map;
})();

// Matcher for "border-radius" + optional side qualifier. Captures only the
// value half so we can rewrite values without disturbing the LHS.
const RADIUS_PROPERTY_RE = /\b(border(?:-top(?:-(?:left|right))?|-bottom(?:-(?:left|right))?|-left|-right)?-radius)\s*:\s*([^;{}]+)(?=[;}])/g;

function translateValue(raw) {
  const trimmed = raw.trim();
  // Already tokenized — leave alone.
  if (/\bvar\(--radius-/.test(trimmed)) return raw;
  // Pct / calc / pre-existing var — leave alone.
  if (/%/.test(trimmed) || /calc\(/.test(trimmed)) return raw;

  // 999 / 9999 → pill token.
  if (/^999(?:9)?(?:px)?$/.test(trimmed.replace(/\s+/g, ""))) return "var(--radius-pill)";

  // Each token is space-separated. Translate each numeric one independently.
  const parts = trimmed.split(/\s+/).map((p) => {
    const m = p.match(/^(\d+)px$/);
    if (!m) return p; // preserve literals like 50% as-is (caller handles)
    const n = Number(m[1]);
    const tok = NUM_TOKEN.get(n);
    if (!tok) return p;
    return `var(--radius-${tok})`;
  });

  // If user used the literal "0" or "0px" we leave it.
  if (parts.every((p) => /^0(px)?$/.test(p))) return raw;

  // 50% / auto stays as-is.
  return parts.join(" ");
}

let replaced = 0;
const next = css.replace(RADIUS_PROPERTY_RE, (full, prop, val) => {
  const newVal = translateValue(val);
  if (newVal !== val) replaced += 1;
  return `${prop}: ${newVal}`;
});

fs.writeFileSync(TARGET, next);
console.log(`radius tokens applied: ${replaced} lines updated in ${path.relative(process.cwd(), TARGET)}`);
