// scripts/bundle-python.mjs
//
// Copies the managed, full Python distribution into build/runtime/python so it
// ships inside the portable app (electron-builder extraResources →
// resources/runtime/python). The venv's pyvenv.cfg is rewritten at runtime
// (electron/backend/hermes-runner.js _portablizeVenv) to use this Python, which
// lets Hermes start on any machine without the dev machine's Python path.
//
// Usage:  node scripts/bundle-python.mjs
//
// Safe to run repeatedly: skips the copy when the target already looks complete.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

// Source: the managed full CPython (must match the venv's version = 3.13.14).
const managedRoots = [
  'C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12',
  process.env.PORTABLE_PYTHON_SRC || '',
].filter(Boolean);

const dest = path.join(root, 'build', 'runtime', 'python');
const destExe = path.join(dest, 'python.exe');

function findSource() {
  for (const r of managedRoots) {
    if (r && fs.existsSync(path.join(r, 'python.exe')) && fs.existsSync(path.join(r, 'Lib'))) {
      return r;
    }
  }
  return '';
}

function run() {
  const src = findSource();
  if (!src) {
    console.error('[bundle-python] No managed Python found. Set PORTABLE_PYTHON_SRC.');
    process.exit(1);
  }

  // Idempotency: if target python.exe exists and the tree is non-trivial, skip.
  if (fs.existsSync(destExe)) {
    const libDir = path.join(dest, 'Lib');
    if (fs.existsSync(libDir)) {
      console.log(`[bundle-python] Already present at ${dest} — skipping copy.`);
      console.log(`[bundle-python] Source was: ${src}`);
      return;
    }
  }

  console.log(`[bundle-python] Copying ${src} → ${dest}`);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.cpSync(src, dest, { recursive: true });
  console.log(`[bundle-python] Done. Size: ${(fs.statSync(dest).size / 1e6).toFixed(0)}MB (dir)`);
}

run();
