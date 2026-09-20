import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

/**
 * 为什么 `server.fs.allow` 要放开到仓库根：
 * 样式（`web/assets/css/*.css`）**不复制**，直接引用旧的那一份 ——
 * 复制会立刻产生「同一判据写两份」，而本项目最贵的一类 bug 就是这个
 * （改了一边忘了另一边 = 两边界面悄悄不一致）。
 * 过渡期两边共用一份 CSS：改一次，React 版和旧 web 版同时生效。
 *
 * 等 `web/` 真要退役时，把那两个 css 移进 `frontend/src/styles/` 即可 ——
 * 那时 import 路径会立刻报错，**不会静默失效**。
 */
const repoRoot = fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    fs: { allow: [repoRoot] },
    /**
     * ★★ **必须把这几条代理回后端**（2026-09-19 真浏览器实测抓到的真问题）：
     *
     *   后端返回的封面/静帧是**相对路径**（`/media/<项目>/media/ep1/stills/LN01.jpg`）。
     *   旧版页面本身就由 8787 托管，所以相对路径天然正确；
     *   而 React 版跑在 5173 上 → 相对路径被解析到 `127.0.0.1:5173/media/...`
     *   → **每一张封面都裂成占位图标**（第一次截图里 39 张卡全是破图）。
     *
     *   代理比"给每张图拼上后端域名"更好：
     *     · 与**生产同源**的形态一致（生产也是后端托管前端），不会出现
     *       "开发能跑、部署后图裂"这类只在一边暴露的偏差；
     *     · 顺带绕开 CORS —— 虽然 `v5/server.py` 已放行 localhost 任意端口，
     *       但那要依赖后端配置正确；同源则天然没有这个问题。
     */
    proxy: {
      '/v1': { target: 'http://127.0.0.1:8787', changeOrigin: true },
      '/media': { target: 'http://127.0.0.1:8787', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8787', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    emptyOutDir: true,
  },
});
