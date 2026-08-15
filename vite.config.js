import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// 编译面板：每次构建/启动前从 LangGraph agent manifest.json 生成前端契约数据
// （bundled manifests + 白名单 + 启动台应用），保证「新增 agent」纯数据驱动。
function contractCodegen() {
  return {
    name: 'contract-codegen',
    buildStart() {
      try {
        execFileSync('node', [path.resolve(__dirname, 'scripts/gen-contract.mjs')], { stdio: 'inherit' });
      } catch (e) {
        console.warn('[contract-codegen] failed:', e.message);
      }
    },
  };
}

export default defineConfig({
  plugins: [contractCodegen(), react()],
  base: './',
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    assetsInlineLimit: 0,
    sourcemap: true,
    // 桌面 app 本地加载，单 chunk 体积警告无意义；阈值提到 2000 消除噪音
    chunkSizeWarningLimit: 2000,
  },
});
