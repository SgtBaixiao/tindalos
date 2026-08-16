import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// 单配置同时服务 vite build 与 vitest（vitest/config 的 defineConfig 兼容 vite 自身）。
// base './'：相对路径资源，兼容 GitHub Pages 子路径与 file:// 直接打开（单页应用无路由）。
export default defineConfig({
  plugins: [react()],
  base: './',
  // dev proxy：/api/* 转发到 serve.py（tindalos serve --port 8347），前端 dev 与后端同源。
  // 仅 vite dev 生效，不影响 build / vitest。生产由反向代理或同源部署承担同职。
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8347',
    },
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
});
