import { expect, it } from 'vitest';
import { writeFileSync } from 'node:fs';
import { performance } from 'node:perf_hooks';
import { applyNodeChanges, type NodeChangeLike } from '../src/store/useGraphStore';
import type { GraphNode } from '../src/lib/types';
it('compares frozen legacy batch algorithm with current implementation', () => {
  const legacy = (changes: NodeChangeLike[], nodes: GraphNode[]) => {
    let next = nodes;
    for (const c of changes)
      if (c.type === 'position' && c.position)
        next = next.map((n) => (n.id === c.id ? { ...n, position: c.position! } : n));
    return next;
  };
  const results = [];
  for (const count of [100, 1000, 5000]) {
    const nodes: GraphNode[] = Array.from({ length: count }, (_, i) => ({
      id: String(i),
      type: 'event',
      position: { x: 0, y: 0 },
      data: { title: 'fixed' },
    }));
    const changes: NodeChangeLike[] = Array.from({ length: Math.min(count, 200) }, (_, i) => ({
      type: 'position',
      id: String(i),
      position: { x: 10, y: 10 },
    }));
    for (const [name, fn] of [
      ['legacy', legacy],
      ['refactored', applyNodeChanges],
    ] as const) {
      const times = [];
      for (let i = 0; i < 30; i++) {
        const start = performance.now();
        const next = fn(changes, nodes);
        times.push(performance.now() - start);
        expect(next[0].position.x).toBe(10);
      }
      times.sort((a, b) => a - b);
      results.push({
        nodes: count,
        changes: changes.length,
        name,
        p50_ms: times[14],
        p95_ms: times[27],
      });
    }
  }
  if (process.env.TINDALOS_WRITE_BENCHMARK === '1')
    writeFileSync(
      '../benchmarks/frontend-batch.json',
      JSON.stringify({ node: process.version, repetitions: 30, results }, null, 2),
    );
  console.log(JSON.stringify(results));
});
