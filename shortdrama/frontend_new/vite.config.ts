import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * ★ `base: '/studio/'` 是**必须的**，不是可选美化。
 *
 * 后端把这份构建挂在 `/studio` 子路径上（`v5/server.py` 的 `_mounts`，
 * 与 `/atelier` 同一个先例）。不给 base 的话产物里是 `/assets/index-xxx.js`
 * 这种根相对引用 → 浏览器去**站点根**要 JS → 拿到的是旧工作台 `frontend/dist`
 * 的 `index.html`（因为根挂载兜底一切）→ 白屏且控制台只有一串 404，
 * 完全看不出是 base 的问题。
 */
export default defineConfig({
  plugins: [react()],
  base: '/studio/',
  server: {
    /**
     * ⚠️ **5174，不是 5173**。旧工作台 `frontend/vite.config.ts` 钉的是
     * 5173 + `strictPort: true` ⇒ 两个 dev 同时开会直接起不来。
     */
    port: 5174,
    strictPort: true,
    /**
     * 与旧工作台同理由：后端返回的封面/静帧是**相对路径**，dev 必须代理回 8787
     * 才不会全部裂图（详见 `frontend/vite.config.ts` 里那段实测记录）。
     *
     * ⚠️ 8787 是启动台**优先**端口，实际可能被让到 8788+（`findFreePort`）。
     * dev 模式下若后端不在这张口，把下面 target 改成实际端口，或直接用
     * 同源地址 `/studio/`（由 8787 托管的构建产物）看。
     */
    proxy: {
      '/v1': { target: 'http://127.0.0.1:8787', changeOrigin: true },
      '/media': { target: 'http://127.0.0.1:8787', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8787', changeOrigin: true },
      '/atelier': { target: 'http://127.0.0.1:8787', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    emptyOutDir: true,
  },
});
