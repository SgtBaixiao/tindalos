/**
 * components.test.ts —— 交付物完整性（验收 #4）：
 * 五种节点组件 + drawer + legend + 进度带 + 暖墨深色模式（theme.css 令牌）存在。
 */
import { existsSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const NODE_FILES = [
  'src/components/nodes/ActNode.tsx',
  'src/components/nodes/SceneNode.tsx',
  'src/components/nodes/EventNode.tsx',
  'src/components/nodes/NpcNode.tsx',
  'src/components/nodes/ClueNode.tsx',
];

describe('组件交付物存在', () => {
  it('五种自定义节点组件文件存在', () => {
    for (const f of NODE_FILES) {
      expect(existsSync(f), `缺失 ${f}`).toBe(true);
    }
  });

  it('drawer / legend / 进度带 / 入口存在', () => {
    for (const f of [
      'src/components/NodeDrawer.tsx',
      'src/components/Legend.tsx',
      'src/components/ProgressBand.tsx',
      'src/App.tsx',
      'src/main.tsx',
      'src/store/useGraphStore.ts',
    ]) {
      expect(existsSync(f), `缺失 ${f}`).toBe(true);
    }
  });

  it('public 数据存在（campaign.json + progress.jsonl）', () => {
    expect(existsSync('public/campaign.json')).toBe(true);
    expect(existsSync('public/progress.jsonl')).toBe(true);
  });
});

describe('theme.css 令牌体系（验收 #4 / 设计调研 §2.1+§3.2）', () => {
  const css = readFileSync('src/theme.css', 'utf-8');

  it('继承 house-style 核心色令牌', () => {
    for (const token of [
      '--t-paper: #faf6ef',
      '--t-orange: #e8620c',
      '--t-rule-red: #d64545',
      '--t-verdigris: #6b7a55',
      '--t-inkblue: #3e4a5a',
      '--t-oldpaper: #ede0c2',
      '--t-sepia-ink: #4a3b28',
    ]) {
      expect(css.toLowerCase(), `缺少令牌 ${token}`).toContain(token);
    }
  });

  it('暖墨深色板存在（base #1E1A15 + [data-theme=dark]）', () => {
    const hasDarkSelector =
      css.includes('[data-theme="dark"]') || css.includes("[data-theme='dark']");
    expect(hasDarkSelector, '缺少 [data-theme=dark] 选择器').toBe(true);
    expect(css.toLowerCase()).toContain('#1e1a15');
  });

  it('--xy-* 变量覆盖存在', () => {
    for (const v of [
      '--xy-node-background-color-default',
      '--xy-edge-stroke-default',
      '--xy-minimap-background-color-default',
      '--xy-controls-button-background-color-default',
    ]) {
      expect(css, `缺少 ${v}`).toContain(v);
    }
  });

  it('纸纹噪点 data-uri 存在（feTurbulence）', () => {
    expect(css).toContain('feTurbulence');
    expect(css).toContain('--t-paper-texture');
  });
});
