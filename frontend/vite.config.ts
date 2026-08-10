import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// 单配置同时服务 vite build 与 vitest（vitest/config 的 defineConfig 兼容 vite 自身）。
// base './'：相对路径资源，兼容 GitHub Pages 子路径与 file:// 直接打开（单页应用无路由）。
export default defineConfig({
  plugins: [react()],
  base: './',
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.ts'],
    restoreMocks: true,
  },
});
