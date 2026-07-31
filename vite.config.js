import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
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
