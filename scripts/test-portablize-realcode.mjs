// scripts/test-portablize-realcode.mjs
//
// Exercises the ACTUAL electron/backend/hermes-runner.js _portablizeVenv()
// (not a replica) against a copy of the real release venv, with
// process.resourcesPath pointed at the release resources dir so that
// _resolveBasePython() candidate #1 (the bundled Python) is selected — exactly
// what happens on a packaged app launch.
//
// Proves the shipped code rewrites the venv to the bundled Python + the
// editable finder to the app's hermes-fork, and that the venv then imports
// hermes_cli from the app's hermes-fork using the bundled (non-dev) Python.

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

const RELEASE = path.join(root, 'release', 'win-unpacked', 'resources');
const RELEASE_APP = path.join(RELEASE, 'app');
const REAL_VENV = path.join(RELEASE_APP, 'hermes-fork', '.venv');
const REAL_FORK = path.join(RELEASE_APP, 'hermes-fork');
const BUNDLED_PY = path.join(RELEASE, 'runtime', 'python', 'python.exe');

function fail(m) { console.error('\nFAIL:', m); process.exit(1); }
function norm(p) { return p.replace(/\\/g, '/'); }

if (!fs.existsSync(REAL_VENV)) fail('real release venv missing: ' + REAL_VENV);
if (!fs.existsSync(BUNDLED_PY)) fail('bundled python missing in release: ' + BUNDLED_PY);

const T = fs.mkdtempSync(path.join(os.tmpdir(), 'portreal-'));
const TMP_VENV = path.join(T, 'venv');
console.log('Temp venv copy:', TMP_VENV);
fs.cpSync(REAL_VENV, TMP_VENV, { recursive: true });

// Build a temp module dir: copy hermes-runner.js + logger.js, rewrite the two
// path constants to the temp venv + real fork, then require the real code.
const MOD = path.join(T, 'mod');
fs.mkdirSync(MOD, { recursive: true });
let runner = fs.readFileSync(path.join(root, 'electron/backend/hermes-runner.js'), 'utf-8');
const logger = fs.readFileSync(path.join(root, 'electron/backend/logger.js'), 'utf-8');
runner = runner.replace(/const HERMES_FORK = [^;]+;/, `const HERMES_FORK = ${JSON.stringify(REAL_FORK)};`);
runner = runner.replace(/const HERMES_VENV = [^;]+;/, `const HERMES_VENV = ${JSON.stringify(TMP_VENV)};`);
fs.writeFileSync(path.join(MOD, 'hermes-runner.cjs'), runner);
fs.writeFileSync(path.join(MOD, 'logger.js'), logger);

// Make _resolveBasePython() candidate #1 resolve to the bundled Python exactly
// as Electron would at runtime.
try { process.resourcesPath = RELEASE; } catch (_) { /* read-only on some runtimes */ }
console.log('process.resourcesPath =', process.resourcesPath);

const require = createRequire(path.join(MOD, 'x.js'));
const { HermesRunner } = require(path.join(MOD, 'hermes-runner.cjs'));

const app = { getPath: () => path.join(T, 'hermes_home') };
const inst = new HermesRunner({ app });
inst._portablizeVenv();

// ── verify the rewrite ──
const cfg = fs.readFileSync(path.join(TMP_VENV, 'pyvenv.cfg'), 'utf-8');
console.log('--- rewritten pyvenv.cfg ---\n' + cfg);
if (!cfg.includes('home = ' + path.dirname(BUNDLED_PY))) {
  fail('pyvenv.cfg home not rewritten to bundled Python:\n' + cfg);
}

const sp = path.join(TMP_VENV, 'Lib', 'site-packages');
const finder = fs.readdirSync(sp).find((f) => f.startsWith('__editable__') && f.endsWith('_finder.py'));
const ftxt = fs.readFileSync(path.join(sp, finder), 'utf-8');
if (!ftxt.includes(REAL_FORK.replace(/\\/g, '\\\\'))) fail('finder not rewritten to real hermes-fork');

// ── launch temp venv python, import hermes_cli → must resolve under REAL_FORK ──
const py = path.join(TMP_VENV, 'Scripts', 'python.exe');
const r = spawnSync(py, ['-c', 'import hermes_cli as h; print("IMPORT_OK", h.__file__)'], { encoding: 'utf-8' });
process.stdout.write(r.stdout || '');
process.stderr.write(r.stderr || '');
if (r.status !== 0) fail('hermes_cli import failed (status ' + r.status + ')');
if (!norm(r.stdout).includes(norm(REAL_FORK))) fail('hermes_cli not resolved under real fork');
if (norm(r.stdout).includes(norm('C:/Users/Administrator/.workbuddy'))) fail('still using dev-machine Python path!');

console.log('\nPASS: REAL hermes-runner.js _portablizeVenv() works end-to-end with bundled Python.');
try { fs.rmSync(T, { recursive: true, force: true }); } catch (_) {}
