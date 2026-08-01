#!/usr/bin/env node
/**
 * check-tdz.js — static scan for temporal-dead-zone bugs in src/.
 *
 * Catches the class of bug that produced the runtime crash
 *   "Cannot access 'X' before initialization"
 * i.e. a `const`/`let` binding that is READ earlier in the *same synchronous
 * execution path* than the line that declares it.
 *
 * Minified production builds rename such variables (e.g. `isLast` -> `be`),
 * which makes the runtime error nearly impossible to read. Run this instead.
 *
 * Usage:  node scripts/check-tdz.js
 * Exit 1 when at least one violation is found.
 */
const fs = require("fs");
const path = require("path");
const parser = require("@babel/parser");
const traverseMod = require("@babel/traverse");
const traverse = traverseMod.default || traverseMod;

const SRC = path.join(__dirname, "..", "src");

function collectFiles(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) collectFiles(full, out);
    else if (/\.(js|jsx)$/.test(entry.name)) out.push(full);
  }
  return out;
}

/**
 * A reference is "deferred" (safe) when a function boundary sits between the
 * reference and the scope that owns the binding — the closure body may run
 * long after the declaration has been evaluated.
 */
function isDeferred(refPath, bindingScopeBlock) {
  let p = refPath.parentPath;
  while (p && p.node !== bindingScopeBlock) {
    if (p.isFunction()) return true;
    p = p.parentPath;
  }
  return false;
}

const violations = [];

for (const file of collectFiles(SRC)) {
  const code = fs.readFileSync(file, "utf8");
  let ast;
  try {
    ast = parser.parse(code, {
      sourceType: "module",
      plugins: ["jsx", "classProperties", "optionalChaining", "nullishCoalescingOperator"],
    });
  } catch (err) {
    violations.push({ file, line: 0, name: "<parse error>", detail: err.message });
    continue;
  }

  traverse(ast, {
    Scopable(scopePath) {
      const scope = scopePath.scope;
      if (scope.path !== scopePath) return; // only visit each scope once
      for (const [name, binding] of Object.entries(scope.bindings)) {
        if (binding.kind !== "const" && binding.kind !== "let") continue;
        const declStart = binding.path.node.start;
        for (const ref of binding.referencePaths) {
          if (ref.node.start >= declStart) continue;
          if (isDeferred(ref, scopePath.node)) continue;
          violations.push({
            file: path.relative(path.join(__dirname, ".."), file).replace(/\\/g, "/"),
            line: ref.node.loc.start.line,
            declLine: binding.path.node.loc.start.line,
            name,
          });
        }
      }
    },
  });
}

if (violations.length === 0) {
  console.log("TDZ scan: clean (0 violations)");
  process.exit(0);
}

console.error(`TDZ scan: ${violations.length} violation(s)\n`);
for (const v of violations) {
  if (v.detail) {
    console.error(`  ${v.file}  ${v.detail}`);
  } else {
    console.error(
      `  ${v.file}:${v.line}  reads '${v.name}' but it is declared at line ${v.declLine}`
    );
  }
}
process.exit(1);
