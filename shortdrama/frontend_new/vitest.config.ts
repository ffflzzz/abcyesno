import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

/**
 * 验证层：迁移到 React 后，「桩 window → 断言 HTML 字符串」那套（旧 web/ 的主力手段）
 * 整体失效，这是迁 React 最大的一笔代价 —— 所以这里必须**同时**给出替代方案，
 * 而不是迁移完留一句"以后补测试"。
 *
 * 替代手段（对应旧手段的两层）：
 *   · `@testing-library/react` + jsdom —— 断言**渲染出来的 DOM**（≈ 旧的 HTML 断言）
 *   · 直接 import 真模块（`src/api.ts` / `src/store.ts`）—— 断言**行为**，
 *     而不是桩掉它（旧 skill 记录过：桩住 Api 就测不出"改造点在 api.js 里"的错）
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
