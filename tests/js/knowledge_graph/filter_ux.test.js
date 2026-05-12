/**
 * KG filter UX — bucket toggle, numeric slider, debounce, threshold culling.
 *
 * Locked decisions (WAVE-C 1c, mem-vault VCS_HUtQLKTHzh71InGIU87I):
 *   D-KG-3  Default render threshold ≥ 0.7 (Strong-only) on first load.
 *   D-KG-4  Discrete buckets (Strong/Medium/Weak) + numeric slider 0.30–0.85.
 *   D-KG-6  `?min_strength=` query param flows to /api/graph for cache key.
 *
 * Static rules verify the source markers exist; runtime rules build a tiny
 * harness around the filter helpers extracted from app.js.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const APP_SRC = readFileSync(
  resolve(__dirname, '../../../website/features/knowledge_graph/js/app.js'),
  'utf8',
);
const HTML_SRC = readFileSync(
  resolve(__dirname, '../../../website/features/knowledge_graph/index.html'),
  'utf8',
);

describe('KG filter UX — static source rules', () => {
  it('declares STRENGTH_BUCKETS with Strong/Medium/Weak boundaries', () => {
    // Boundaries pinned by D-KG-3/4: Strong ≥ 0.70, Medium 0.50–0.70, Weak 0.30–0.50.
    expect(APP_SRC).toMatch(/STRENGTH_BUCKETS\s*=/);
    expect(APP_SRC).toMatch(/strong[^,]*0\.7/i);
    expect(APP_SRC).toMatch(/medium[^,]*0\.5/i);
    expect(APP_SRC).toMatch(/weak[^,]*0\.3/i);
  });

  it('declares default min strength = 0.7 (D-KG-3 Strong-only first load)', () => {
    expect(APP_SRC).toMatch(/DEFAULT_MIN_STRENGTH\s*=\s*0\.7/);
  });

  it('declares slider range 0.30–0.85 step 0.05', () => {
    // Both the slider element and the JS clamp must use 0.30..0.85.
    expect(APP_SRC).toMatch(/SLIDER_MIN\s*=\s*0\.3/);
    expect(APP_SRC).toMatch(/SLIDER_MAX\s*=\s*0\.85/);
    expect(APP_SRC).toMatch(/SLIDER_STEP\s*=\s*0\.05/);
  });

  it('uses 250ms debounce on slider changes', () => {
    // Pin the debounce literal so a future refactor can't silently change it.
    expect(APP_SRC).toMatch(/(SLIDER_DEBOUNCE_MS\s*=\s*250|setTimeout\([^,]+,\s*250\))/);
  });

  it('passes ?min_strength to /api/graph for cache-key alignment (D-KG-6)', () => {
    expect(APP_SRC).toMatch(/min_strength/);
    expect(APP_SRC).toMatch(/\/api\/graph[^']*['"`+]/);
  });

  it('re-warms simulation at α=0.3 instead of full restart on filter change', () => {
    // Per locked decision: re-warm, do NOT call .resetSimulation() / restart.
    expect(APP_SRC).toMatch(/d3ReheatSimulation|alpha\(0\.3\)|reheat[\s\S]{0,40}0\.3/i);
    expect(APP_SRC).not.toMatch(/resetSimulation\s*\(/);
  });

  it('index.html exposes a strength control container in the top bar', () => {
    expect(HTML_SRC).toMatch(/id="strength-controls"/);
    expect(HTML_SRC).toMatch(/id="strength-slider"/);
    expect(HTML_SRC).toMatch(/data-bucket="strong"/);
    expect(HTML_SRC).toMatch(/data-bucket="medium"/);
    expect(HTML_SRC).toMatch(/data-bucket="weak"/);
  });

  it('strength slider element pins min/max/step', () => {
    expect(HTML_SRC).toMatch(/min="0\.3(0)?"/);
    expect(HTML_SRC).toMatch(/max="0\.85"/);
    expect(HTML_SRC).toMatch(/step="0\.05"/);
  });
});

describe('KG filter UX — runtime threshold culling', () => {
  // Lift the pure helpers from app.js so we can exercise them without
  // booting Three.js / 3d-force-graph (CDN globals). The helpers are
  // exported via a window.__kgFilterTest hatch when KG_TEST=1 is set in
  // the harness — see _exposeForTests() in app.js.
  let helpers;

  beforeEach(() => {
    document.body.innerHTML = '';
    // Strip the CDN-dependent IIFE entry and just eval the helper block.
    // The helpers section is fenced by /* test-exports:start */ ..
    // /* test-exports:end */ markers so we can yank it cleanly.
    const m = APP_SRC.match(
      /\/\* test-exports:start \*\/([\s\S]*?)\/\* test-exports:end \*\//,
    );
    if (!m) throw new Error('app.js test-exports fence missing');
    const sandbox = {};
    new Function('exports', m[1] + '\nexports.__all = { STRENGTH_BUCKETS, DEFAULT_MIN_STRENGTH, SLIDER_MIN, SLIDER_MAX, SLIDER_STEP, snapToBucket, bucketForStrength, cullLinksByStrength, debounce };')(sandbox);
    helpers = sandbox.__all;
  });

  it('cullLinksByStrength drops links below the threshold', () => {
    const links = [
      { source: 'a', target: 'b', connection_strength: 0.85 },
      { source: 'b', target: 'c', connection_strength: 0.55 },
      { source: 'c', target: 'd', connection_strength: 0.31 },
      { source: 'd', target: 'e' }, // missing → treated as 0 → culled
    ];
    expect(helpers.cullLinksByStrength(links, 0.7).length).toBe(1);
    expect(helpers.cullLinksByStrength(links, 0.5).length).toBe(2);
    expect(helpers.cullLinksByStrength(links, 0.3).length).toBe(3);
    expect(helpers.cullLinksByStrength(links, 0).length).toBe(4);
  });

  it('bucketForStrength labels Strong ≥ 0.7, Medium 0.5–0.7, Weak 0.3–0.5', () => {
    expect(helpers.bucketForStrength(0.9)).toBe('strong');
    expect(helpers.bucketForStrength(0.7)).toBe('strong');
    expect(helpers.bucketForStrength(0.69)).toBe('medium');
    expect(helpers.bucketForStrength(0.5)).toBe('medium');
    expect(helpers.bucketForStrength(0.49)).toBe('weak');
    expect(helpers.bucketForStrength(0.3)).toBe('weak');
    expect(helpers.bucketForStrength(0.29)).toBe(null);
  });

  it('snapToBucket returns the bucket lower-bound', () => {
    expect(helpers.snapToBucket('strong')).toBe(0.7);
    expect(helpers.snapToBucket('medium')).toBe(0.5);
    expect(helpers.snapToBucket('weak')).toBe(0.3);
  });

  it('debounce coalesces rapid calls into one trailing invocation @ 250ms', () => {
    vi.useFakeTimers();
    try {
      const spy = vi.fn();
      const d = helpers.debounce(spy, 250);
      d(1); d(2); d(3);
      vi.advanceTimersByTime(249);
      expect(spy).not.toHaveBeenCalled();
      vi.advanceTimersByTime(2);
      expect(spy).toHaveBeenCalledTimes(1);
      expect(spy).toHaveBeenCalledWith(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it('default min strength constant equals 0.7 (D-KG-3)', () => {
    expect(helpers.DEFAULT_MIN_STRENGTH).toBe(0.7);
  });

  it('slider range constants match the locked spec', () => {
    expect(helpers.SLIDER_MIN).toBe(0.3);
    expect(helpers.SLIDER_MAX).toBe(0.85);
    expect(helpers.SLIDER_STEP).toBe(0.05);
  });
});
