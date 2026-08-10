import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// 单配置同时服务 vite build 与 vitest（vitest/config 的 defineConfig 兼容 vite 自身）。
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.ts'],
    restoreMocks: true,
  },
});
