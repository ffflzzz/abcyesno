// scripts/setup-shortdrama-venv.mjs
//
// Builds the standalone Python environment for the vendored shortdrama app at
// shortdrama/.venv, from shortdrama/requirements.lock.txt.
//
// Why a second venv instead of reusing hermes-fork/.venv: the two trees cannot
// agree on an interpreter or a dependency set. shortdrama pins openai 3.5 /
// starlette 1.6 / deepagents / langchain / langgraph-cli, while the Hermes venv
// is pinned to openai 2.24 / starlette 1.3 and has none of the last three. The
// lock file is installed verbatim so behaviour matches the source repo.
//
// v5/webchain.py looks for the interpreter at <shortdrama>/.venv/Scripts/
// {python.exe,langgraph.exe} (it computes PROJECT_ROOT as the parent of the v5
// package), so the location is not a convention we chose — it is what the
// vendored code expects.
//
// Usage:  node scripts/setup-shortdrama-venv.mjs [--force]
//
// Safe to re-run: skips when the venv already has the lock file's packages.

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');
const appDir = path.join(root, 'shortdrama');
const venvDir = path.join(appDir, '.venv');
const lock = path.join(appDir, 'requirements.lock.txt');
const scriptsDir = path.join(venvDir, 'Scripts');
const pyExe = path.join(scriptsDir, 'python.exe');
const lgExe = path.join(scriptsDir, 'langgraph.exe');

// Must be the same distribution electron-builder ships under
// resources/runtime/python (scripts/bundle-python.mjs), because the packaged
// app rewrites pyvenv.cfg to point at it on first launch.
const baseCandidates = [
  'C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe',
  path.join(root, 'build', 'runtime', 'python', 'python.exe'),
  process.env.PORTABLE_PYTHON_EXE || '',
].filter(Boolean);

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { stdio: 'inherit', ...opts });
  if (r.error) throw r.error;
  if (r.status !== 0) {
    throw new Error(`${path.basename(cmd)} ${args[0] || ''} failed with code ${r.status}`);
  }
}

function looksComplete() {
  if (!fs.existsSync(pyExe) || !fs.existsSync(lgExe)) return false;
  // Probe the import that no other component of this stack provides.
  const r = spawnSync(pyExe, ['-c', 'import v5.server, deepagents, langgraph_cli'], {
    cwd: appDir,
    encoding: 'utf-8',
  });
  return r.status === 0;
}

function main() {
  const force = process.argv.includes('--force');
  if (!force && looksComplete()) {
    console.log('[shortdrama-venv] already complete, nothing to do (use --force to rebuild)');
    return;
  }
  if (!fs.existsSync(lock)) {
    throw new Error(`missing ${path.relative(root, lock)} — shortdrama/ not vendored?`);
  }

  const base = baseCandidates.find((p) => fs.existsSync(p));
  if (!base) {
    throw new Error(
      'No managed CPython 3.13 found. Run `node scripts/bundle-python.mjs` first, ' +
      'or set PORTABLE_PYTHON_EXE.'
    );
  }
  console.log(`[shortdrama-venv] base interpreter: ${base}`);

  if (force && fs.existsSync(venvDir)) {
    console.log('[shortdrama-venv] removing existing venv (--force)');
    fs.rmSync(venvDir, { recursive: true, force: true });
  }

  run(base, ['-m', 'venv', venvDir]);
  console.log('[shortdrama-venv] installing from requirements.lock.txt (a few minutes)');
  run(pyExe, ['-m', 'pip', 'install', '--quiet', '--no-warn-script-location',
    '-r', lock]);

  if (!looksComplete()) {
    throw new Error('venv built but `import v5.server, deepagents, langgraph_cli` still fails');
  }
  const v = spawnSync(pyExe, ['-V'], { encoding: 'utf-8' });
  const lg = spawnSync(lgExe, ['--version'], { encoding: 'utf-8' });
  console.log(`[shortdrama-venv] done — ${(v.stdout || '').trim()} / ${(lg.stdout || '').trim()}`);
}

main();
