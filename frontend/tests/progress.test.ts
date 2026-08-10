/**
 * progress.test.ts —— progress.jsonl 解析与打字机工具。
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { KP_STAGES, parseProgressEvents, typewriter } from '../src/lib/progress';

describe('parseProgressEvents', () => {
  it('解析 public/progress.jsonl（7 条样例事件）且顺序保持', () => {
    const raw = readFileSync('public/progress.jsonl', 'utf-8');
    const events = parseProgressEvents(raw);
    expect(events.length).toBe(7);
    expect(events[0].agent).toBe('kp');
    expect(events[0].step).toBe('读取模组');
    expect(events[2].agent).toBe('npc');
    expect(events[2].npc).toBe('顾长歌');
    expect(events[events.length - 1].step).toBe('校对付印');
  });

  it('KP 主控步骤时间线齐全（读取模组/拟定幕结构/写作分幕/校对付印）', () => {
    const raw = readFileSync('public/progress.jsonl', 'utf-8');
    const steps = parseProgressEvents(raw)
      .filter((e) => e.agent === 'kp')
      .map((e) => e.step);
    expect(KP_STAGES.length).toBe(4);
    expect(steps.join('·')).toContain('拟定幕结构');
    expect(steps.join('·')).toContain('校对付印');
  });

  it('跳过空行、注释行与坏 JSON 行', () => {
    const raw = [
      '# 注释行',
      '',
      '   ',
      '{not json',
      '{"agent":"kp","step":"A","text":"ok"}',
      '{"step":"B"}', // 缺 text
      '',
    ].join('\n');
    const events = parseProgressEvents(raw);
    expect(events).toHaveLength(1);
    expect(events[0].step).toBe('A');
  });

  it('agent 缺省回落为 kp；npc 字段透传', () => {
    const events = parseProgressEvents('{"step":"S","text":"T","npc":"苏晚晴"}');
    expect(events[0].agent).toBe('kp');
    expect(events[0].npc).toBe('苏晚晴');
  });
});

describe('typewriter', () => {
  it('按字符数截取（打字机滚动用）', () => {
    expect(typewriter('雾从港口漫上来的那个夜晚…', 4)).toBe('雾从港口');
    expect(typewriter('abc', 0)).toBe('');
    expect(typewriter('abc', 99)).toBe('abc');
    expect(typewriter('abc', -1)).toBe('');
  });
});
