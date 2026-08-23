/**
 * Build script for the vendored wechat-claude-code bridge.
 * Bundles TypeScript source into a single CJS file consumable by
 * Electron's main process: electron/backend/wechat_bridge/dist/index.js
 *
 * Run from repo root:  node electron/backend/wechat_bridge/build.mjs
 */
import { build } from 'esbuild';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));

await build({
  entryPoints: [join(here, 'src', 'bridge.ts')],
  bundle: true,
  platform: 'node',
  target: 'node20',
  format: 'cjs',
  outfile: join(here, 'dist', 'index.js'),
  // Only referenced by the vestigial CLI setup path (headless Linux);
  // never executed inside abcyesno.
  external: ['qrcode-terminal'],
  sourcemap: false,
  legalComments: 'none',
  logLevel: 'info',
});
