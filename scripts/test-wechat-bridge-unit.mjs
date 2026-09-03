/**
 * wechat_bridge 单测运行器（node:test）
 *
 * 桥是 TS 源码、由 esbuild 打成单文件 CJS 给 Electron 用，仓库里本来没有能
 * 直接跑 src/tests/*.test.ts 的入口（node --test 不认 TS）。这里用同一个
 * esbuild 把测试文件连同它 import 的 src 模块打成 ESM，再交给 node --test。
 *
 * 注意与 scripts/test-wechat-bridge.mjs（宿主冒烟测试）区分：那个测的是
 * wechat-bridge-runner.js 的加载与分发守卫，这个测的是 src 下的纯逻辑模块。
 *
 * 用法：node scripts/test-wechat-bridge-unit.mjs
 */
import { build } from 'esbuild';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { readdirSync, rmSync, mkdirSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const bridgeDir = join(repoRoot, 'electron', 'backend', 'wechat_bridge');
const testsDir = join(bridgeDir, 'src', 'tests');
const outDir = join(bridgeDir, '.tmp-tests');

const testFiles = readdirSync(testsDir).filter((f) => f.endsWith('.test.ts'));
if (testFiles.length === 0) {
  console.error(`No *.test.ts found in ${testsDir}`);
  process.exit(1);
}

rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });

await build({
  entryPoints: testFiles.map((f) => join(testsDir, f)),
  outdir: outDir,
  bundle: true,
  platform: 'node',
  target: 'node20',
  format: 'esm',
  outExtension: { '.js': '.mjs' },
  sourcemap: false,
  legalComments: 'none',
  logLevel: 'warning',
});

// 隔离数据目录：被测模块里 logger 会真的往 DATA_DIR/logs 写文件，别污染
// 真实 HERMES_HOME / ~/.wechat-claude-code。
const dataDir = mkdtempSync(join(tmpdir(), 'wcc-unit-'));

// 显式传文件列表而不是目录：不同 Node 版本对 `node --test <dir>` 的 glob
// 支持不一致（22.22 上会把目录当模块去 require）。
const builtFiles = readdirSync(outDir)
  .filter((f) => f.endsWith('.mjs'))
  .map((f) => join(outDir, f));

const res = spawnSync(process.execPath, ['--test', ...builtFiles], {
  stdio: 'inherit',
  // cwd 用 bridge 根目录：部分用例按 bridge 相对路径读 fixtures
  // （如 src/tests/fixtures/tool-noise/real-cases.json）。
  cwd: bridgeDir,
  env: { ...process.env, WCC_DATA_DIR: dataDir },
});

const status = res.status ?? 1;
rmSync(dataDir, { recursive: true, force: true });
process.exit(status);
