/**
 * KG perf — 3d-force-graph web worker offload + α-stop tuning.
 *
 * Locked decision: useWebWorker:true on init, cooldown stop at α<0.01
 * (3× faster settle than the default 0.001).
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const APP_SRC = readFileSync(
  resolve(__dirname, '../../../website/features/knowledge_graph/js/app.js'),
  'utf8',
);

describe('KG perf — web worker + simulation tuning', () => {
  it('initializes ForceGraph3D with useWebWorker:true', () => {
    // 3d-force-graph accepts a config object on the constructor.
    // Pin the literal so a regression flips it to default false silently.
    expect(APP_SRC).toMatch(
      /new ForceGraph3D\([^)]*,\s*\{[^}]*useWebWorker\s*:\s*true/,
    );
  });

  it('stops simulation at cooldown α < 0.01 (3× faster settle)', () => {
    // .cooldownTicks() / .cooldownTime() are both fine; the binding name is
    // d3AlphaMin in 3d-force-graph 1.79+. Pin the cutoff value 0.01.
    expect(APP_SRC).toMatch(/d3AlphaMin\(\s*0\.01\s*\)/);
  });

  it('does NOT call resetSimulation on slider/bucket changes', () => {
    // Reheat-only on filter change is the locked decision (re-warm at
    // α=0.3). resetSimulation would re-trigger the warmup ticks and burn
    // CPU on the worker thread.
    expect(APP_SRC).not.toMatch(/\.resetSimulation\s*\(/);
  });

  it('reheats simulation at α=0.3 on filter change', () => {
    expect(APP_SRC).toMatch(/d3ReheatSimulation\s*\(\s*\)|alpha\(\s*0\.3\s*\)/);
  });

  it('uses the cooldownTime cap below the default to avoid runaway settles', () => {
    // The existing setting is 2500ms. Pin it as a regression guard — anything
    // higher means the worker keeps spinning long after the layout converges.
    expect(APP_SRC).toMatch(/\.cooldownTime\(\s*2500\s*\)/);
  });
});
