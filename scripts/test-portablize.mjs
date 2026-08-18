// scripts/test-portablize.mjs
//
// Non-destructive regression test for the venv portability fix.
//
// It replicates the EXACT rewrite algorithm used by
// electron/backend/hermes-runner.js _portablizeVenv() against a temp copy of
// the release venv + hermes-fork + bundled Python, then launches the temp venv
// Python to prove that:
//   1. the venv boots off the BUNDLED Python (not the dev machine's path), and
//   2. the editable-install finder resolves hermes_agent from the app's
//      hermes-fork (not the dev machine's source dir).
//
// This is the scenario a brand-new machine hits: the dev Python path
// (C:\Users\Administrator\.workbuddy\...) does not exist there.
//
// Usage:  node scripts/test-portablize.mjs

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

const RELEASE_APP = path.join(root, 'release', 'win-unpacked', 'resources', 'app');
const SRC_FORK = path.join(RELEASE_APP, 'hermes-fork');
const SRC_VENV = path.join(SRC_FORK, '.venv');
const BUNDLED = path.join(root, 'build', 'runtime', 'python');

function fail(msg) {
  console.error('\nFAIL:', msg);
  process.exit(1);
}
function norm(p) { return p.replace(/\\/g, '/'); }

if (!fs.existsSync(SRC_VENV)) fail('release venv missing: ' + SRC_VENV);
if (!fs.existsSync(BUNDLED)) fail('bundled python missing: ' + BUNDLED);

const T = fs.mkdtempSync(path.join(os.tmpdir(), 'portablize-'));
const T_FORK = path.join(T, 'app', 'hermes-fork');
const T_VENV = path.join(T_FORK, '.venv');
const RUNTIME = path.join(T, 'runtime', 'python');
const HERMES_FORK = T_FORK;

console.log('Temp workspace:', T);
console.log('Copying hermes-fork (incl. .venv) and bundled python...');
fs.cpSync(SRC_FORK, T_FORK, { recursive: true });
fs.cpSync(BUNDLED, RUNTIME, { recursive: true });

// ── replicate hermes-runner.js _portablizeVenv() ──────────────────────────
function longestCommonPrefix(strs) {
  if (!strs.length) return '';
  let prefix = strs[0];
  for (const s of strs) {
    let i = 0;
    const max = Math.min(prefix.length, s.length);
    while (i < max && prefix[i] === s[i]) i++;
    prefix = prefix.slice(0, i);
    if (!prefix) break;
  }
  while (prefix.length && /[\\/]/.test(prefix[prefix.length - 1])) prefix = prefix.slice(0, -1);
  return prefix;
}

// (1) pyvenv.cfg → point at the bundled base Python.
const baseExe = path.join(RUNTIME, 'python.exe');
const baseDir = path.dirname(baseExe);
const cfgPath = path.join(T_VENV, 'pyvenv.cfg');
{
  let txt = fs.readFileSync(cfgPath, 'utf-8');
  txt = txt.replace(/^home\s*=\s*.+$/m, `home = ${baseDir}`);
  txt = txt.replace(/^executable\s*=\s*.+$/m, `executable = ${baseExe}`);
  fs.writeFileSync(cfgPath, txt, 'utf-8');
  console.log('  pyvenv.cfg home ->', baseDir);
}

// (2) editable-install finder → rewrite source prefix to HERMES_FORK.
const sp = path.join(T_VENV, 'Lib', 'site-packages');
const HERMES_FORK_ESCAPED = HERMES_FORK.replace(/\\/g, '\\\\');
const finderFiles = fs.readdirSync(sp).filter((f) => f.startsWith('__editable__') && f.endsWith('_finder.py'));
for (const f of finderFiles) {
  const fp = path.join(sp, f);
  let txt = fs.readFileSync(fp, 'utf-8');
  const paths = [...txt.matchAll(/'([^']+)'/g)].map((m) => m[1]).filter((p) => /^[A-Za-z]:[\\/]/.test(p));
  const prefix = longestCommonPrefix(paths);
  if (!prefix || !/hermes-fork$/i.test(prefix) || prefix === HERMES_FORK_ESCAPED) continue;
  if (txt.includes(prefix)) {
    txt = txt.split(prefix).join(HERMES_FORK_ESCAPED);
    fs.writeFileSync(fp, txt, 'utf-8');
    console.log('  finder', f, 'prefix ->', HERMES_FORK_ESCAPED);
  }
}

// ── launch the temp venv Python and verify ────────────────────────────────
const py = path.join(T_VENV, 'Scripts', 'python.exe');

console.log('\n[1] import hermes_cli (must resolve under temp hermes-fork, not dev path)');
const r1 = spawnSync(py, ['-c', 'import hermes_cli as h; print("IMPORT_OK", h.__file__)'], { encoding: 'utf-8' });
process.stdout.write(r1.stdout || '');
process.stderr.write(r1.stderr || '');
if (r1.status !== 0) fail('hermes_cli import failed (status ' + r1.status + ')');
if (!r1.stdout.includes('IMPORT_OK')) fail('hermes_cli did not import');
if (!norm(r1.stdout).includes(norm(T_FORK))) fail('hermes_cli resolved outside temp hermes-fork');
if (norm(r1.stdout).includes(norm('C:/Users/Administrator/.workbuddy'))) {
  fail('hermes_cli still resolved via dev-machine Python path!');
}

console.log('\n[2] hermes_cli.main --help (proves bundled Python drives the venv; this is the real entry the app runs)');
const r2 = spawnSync(py, ['-m', 'hermes_cli.main', '--help'], { encoding: 'utf-8' });
process.stdout.write(r2.stdout ? r2.stdout.slice(0, 400) + '\n' : '');
process.stderr.write(r2.stderr ? r2.stderr.slice(0, 400) : '');
if (r2.status !== 0) fail('hermes_cli.main --help failed (status ' + r2.status + ')');

console.log('\nPASS: venv portability rewrite works with the bundled Python.');

// cleanup
try { fs.rmSync(T, { recursive: true, force: true }); } catch (_) {}
