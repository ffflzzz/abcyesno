#!/usr/bin/env node
/**
 * check-undef.js — 跨作用域引用错误扫描（no-undef 的收窄版）。
 *
 * 与 check-tdz.js 互补，但抓的是**不同**的一类错误：
 *   check-tdz.js  → "Cannot access 'X' before initialization"（先读后声明，同一同步路径）
 *   check-undef.js → "X is not defined"（引用了一个在本作用域根本不可见的绑定）
 *
 * 背景（2026-09-16 微信端事故）：
 *   `suppressUntilMessageStart` 声明在 `new Promise(executor)` 内部，却被 executor
 *   之外的 markQueued() 引用。executor 内的 `let` 是函数级绑定，外层函数取不到，
 *   命中低频分支（忙时会话排队）时抛 ReferenceError。
 *   这类错误 check-tdz.js 抓不到（不是"先读后声明"），vite build 也抓不到
 *   （构建期不做跨作用域解析），只能等运行时炸——且往往炸在少走的路径上。
 *
 * 启发式（为压低误报而收窄）：
 *   标识符被引用、未解析到任何绑定，**且该名字在本文件内出现过声明**
 *   → 作者以为它可见，高置信度作用域/拼写错误。
 *   名字在本文件完全没出现过的引用一律跳过（那是真正的全局对象：process、
 *   console、Buffer、window……逐个维护白名单既费事又容易漏）。
 *
 * 覆盖范围比 check-tdz.js 更宽：electron/ 后端（CommonJS）与 scripts/ 也在内，
 * 因为上面那类错误同样会出现在非前端代码里。
 *
 * Usage:  node scripts/check-undef.js
 * Exit 1 when at least one suspicious reference is found.
 */
const fs = require("fs");
const path = require("path");
const parser = require("@babel/parser");
const traverseMod = require("@babel/traverse");
const traverse = traverseMod.default || traverseMod;

const ROOT = path.join(__dirname, "..");
const TARGETS = ["src", "electron", "scripts"];
// 构建产物、依赖、临时/测试夹具目录一律不扫
const SKIP = /(node_modules|\.tmp-tests|\/tests\/|\/dist\/|\/release\/|__pycache__)/;

function collect(dir, out = []) {
  let entries;
  try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (_) { return out; }
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (SKIP.test(full.replace(/\\/g, "/"))) continue;
    if (e.isDirectory()) collect(full, out);
    else if (/\.(js|jsx|mjs|cjs)$/.test(e.name)) out.push(full);
  }
  return out;
}

const hits = [];
let scanned = 0;
const files = TARGETS.flatMap((t) => collect(path.join(ROOT, t)));

for (const file of files) {
  const code = fs.readFileSync(file, "utf8");
  let ast;
  try {
    ast = parser.parse(code, {
      sourceType: "unambiguous",
      plugins: ["jsx", "classProperties", "optionalChaining", "nullishCoalescingOperator", "topLevelAwait"],
    });
  } catch (_) {
    continue; // 解析不了的文件（非标准语法）交给 node --check 报错，这里跳过
  }
  scanned++;

  const declaredHere = new Set();
  traverse(ast, {
    Scopable(p) {
      for (const name of Object.keys(p.scope.bindings)) declaredHere.add(name);
    },
  });

  traverse(ast, {
    ReferencedIdentifier(p) {
      const name = p.node.name;
      if (p.scope.getBinding(name)) return;        // 正常解析，跳过
      if (!declaredHere.has(name)) return;         // 疑似真全局，跳过
      // 非计算成员的属性名、对象字面量键名不是变量引用
      if (p.parentPath.isMemberExpression() && p.parentPath.node.property === p.node && !p.parentPath.node.computed) return;
      if (p.parentPath.isObjectProperty({ key: p.node }) && !p.parentPath.node.computed) return;
      hits.push({
        file: path.relative(ROOT, file).replace(/\\/g, "/"),
        line: p.node.loc.start.line,
        name,
        kind: "read",
      });
    },
    // 盲区补测：`x = 1`（operator 为 "=" 的纯写入）在 babel 里 **不算**
    // referenced identifier，于是 ReferencedIdentifier 不会触发。但严格模式下
    // 对未声明的标识符直写同样抛 ReferenceError（本次 markQueued 里就有一处
    // `suppressUntilMessageStart = true;`）。复合赋值 `x += 1` 是读+写，已被
    // 上面的引用检测覆盖，这里只补纯写入。
    AssignmentExpression(p) {
      if (p.node.operator !== "=") return;
      const left = p.node.left;
      if (!left || left.type !== "Identifier") return; // 解构赋值左侧不是单个标识符
      const name = left.name;
      if (p.scope.getBinding(name)) return;
      if (!declaredHere.has(name)) return;
      hits.push({
        file: path.relative(ROOT, file).replace(/\\/g, "/"),
        line: left.loc.start.line,
        name,
        kind: "write",
      });
    },
  });
}

if (hits.length === 0) {
  console.log(`undef scan: clean (0 suspicious refs in ${scanned} files)`);
  process.exit(0);
}

console.error(`undef scan: ${hits.length} suspicious ref(s) — 引用了一个在本作用域不可见的绑定\n`);
for (const h of hits) {
  console.error(`  ${h.file}:${h.line}  '${h.name}' (${h.kind})`);
}
console.error("\n提示：多数情况是变量声明在更内层的作用域（如 Promise executor、try 块），");
console.error("      或 props 未随组件下传。把声明提升到共同的外层作用域即可。");
process.exit(1);
