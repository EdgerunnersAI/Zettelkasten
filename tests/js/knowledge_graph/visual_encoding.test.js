/**
 * KG visual encoding — edge opacity, edge thickness, no-purple invariant.
 *
 * Locked decisions (R2 research, mem-vault VCS_HUtQLKTHzh71InGIU87I):
 *   D-KG-VIS  Encode connection_strength as line OPACITY + line WIDTH only.
 *             Color stays amber/gold base; community modulates HUE only
 *             within the amber spectrum (HSL 30–55).
 *   CLAUDE.md No purple/violet/lavender (HSL 250–290) anywhere.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const APP_SRC = readFileSync(
  resolve(__dirname, '../../../website/features/knowledge_graph/js/app.js'),
  'utf8',
);
const CSS_SRC = readFileSync(
  resolve(__dirname, '../../../website/features/knowledge_graph/css/style.css'),
  'utf8',
);

describe('KG visual encoding — opacity + width formulas', () => {
  // The opacity formula is `0.2 + 0.8 * connection_strength`; width is
  // `0.5 + 2.5 * connection_strength`. Both are pinned in app.js by name
  // so we can exercise them via the test-exports fence.
  let helpers;
  beforeEach(() => {
    const m = APP_SRC.match(
      /\/\* test-exports:start \*\/([\s\S]*?)\/\* test-exports:end \*\//,
    );
    if (!m) throw new Error('app.js test-exports fence missing');
    const sandbox = {};
    new Function(
      'exports',
      m[1] + '\nexports.__viz = { edgeOpacityFor, edgeWidthFor };',
    )(sandbox);
    helpers = sandbox.__viz;
  });

  it('edgeOpacityFor follows 0.2 + 0.8 * s', () => {
    expect(helpers.edgeOpacityFor(0)).toBeCloseTo(0.2, 5);
    expect(helpers.edgeOpacityFor(0.5)).toBeCloseTo(0.6, 5);
    expect(helpers.edgeOpacityFor(1)).toBeCloseTo(1.0, 5);
    // Defensive: out-of-range strengths clamp; opacity never <0.2 or >1.0.
    expect(helpers.edgeOpacityFor(-0.5)).toBeGreaterThanOrEqual(0.2);
    expect(helpers.edgeOpacityFor(2)).toBeLessThanOrEqual(1.0);
  });

  it('edgeWidthFor follows 0.5 + 2.5 * s', () => {
    expect(helpers.edgeWidthFor(0)).toBeCloseTo(0.5, 5);
    expect(helpers.edgeWidthFor(0.5)).toBeCloseTo(1.75, 5);
    expect(helpers.edgeWidthFor(1)).toBeCloseTo(3.0, 5);
    // Width is monotonically non-decreasing and never below 0.5.
    expect(helpers.edgeWidthFor(-1)).toBeGreaterThanOrEqual(0.5);
  });

  it('app.js wires .linkOpacity to edgeOpacityFor and .linkWidth to edgeWidthFor', () => {
    // Find the 3d-force-graph init block. The linkOpacity / linkWidth
    // callbacks must call the helpers; otherwise the formula is dead code.
    expect(APP_SRC).toMatch(/\.linkOpacity\(\s*[^)]*edgeOpacityFor[^)]*\)/);
    expect(APP_SRC).toMatch(/\.linkWidth\(\s*[^)]*edgeWidthFor[^)]*\)/);
  });
});

describe('KG visual encoding — no-purple color rule', () => {
  // Allowed hues: amber/gold (HSL 30–55), teal (155–190 — used by Kasten dots),
  // source-badge palette already in COLORS{}. Forbidden: 250–290 = purple.
  // We scan for `hsl(<h>...)` and `#xxxxxx` literals and reject anything in
  // the purple band.
  function hslMatchesPurple(h) { return h >= 250 && h <= 290; }

  function hexToHsl(hex) {
    const m = hex.replace('#', '').match(/^([0-9a-f]{6})$/i);
    if (!m) return null;
    const r = parseInt(m[1].slice(0, 2), 16) / 255;
    const g = parseInt(m[1].slice(2, 4), 16) / 255;
    const b = parseInt(m[1].slice(4, 6), 16) / 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    if (max === min) return { h: 0, s: 0, l: (max + min) / 2 };
    const d = max - min;
    let h;
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h = Math.round(h * 60); if (h < 0) h += 360;
    const l = (max + min) / 2;
    const s = d / (1 - Math.abs(2 * l - 1));
    return { h, s, l };
  }

  function scanForPurple(src, label) {
    // Strip block + line comments first.
    const stripped = src
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/\/\/[^\n]*/g, '');
    // 1) hsl(<h>, ..) / hsla(<h>, ..)
    const hslRe = /hsla?\(\s*([0-9.]+)/gi;
    let match;
    while ((match = hslRe.exec(stripped))) {
      const h = parseFloat(match[1]);
      expect(hslMatchesPurple(h), `${label} HSL ${h} is purple`).toBe(false);
    }
    // 2) Hex colors: extract HSL hue, reject 250..290.
    const hexRe = /#[0-9a-fA-F]{6}\b/g;
    while ((match = hexRe.exec(stripped))) {
      const hsl = hexToHsl(match[0]);
      if (!hsl) continue;
      // Pure greys (saturation 0) are fine — h is meaningless.
      if (hsl.s < 0.05) continue;
      expect(
        hslMatchesPurple(hsl.h),
        `${label} hex ${match[0]} → hue ${hsl.h} is purple`,
      ).toBe(false);
    }
    // 3) Hardcoded purple aliases.
    const aliases = ['#A78BFA', '#8B5CF6', '#7C3AED', '#6D28D9', 'rebeccapurple', 'purple', 'violet', 'lavender', 'indigo'];
    for (const a of aliases) {
      // Allow the literal token only inside obvious comment/test contexts —
      // already stripped above, so any survivor is a real style declaration.
      const re = new RegExp(`\\b${a.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')}\\b`, 'i');
      expect(stripped, `${label} contains forbidden alias "${a}"`).not.toMatch(re);
    }
  }

  it('app.js contains no purple colors', () => {
    scanForPurple(APP_SRC, 'app.js');
  });

  it('style.css contains no purple colors', () => {
    scanForPurple(CSS_SRC, 'style.css');
  });

  it('app.js declares an amber base for community modulation (HSL 30–55)', () => {
    // The community-hue helper is named getCommunityHue() and must clamp
    // its output to 30..55. The test asserts the constant pair exists.
    expect(APP_SRC).toMatch(/AMBER_HUE_MIN\s*=\s*30/);
    expect(APP_SRC).toMatch(/AMBER_HUE_MAX\s*=\s*55/);
  });

  it('community hue helper clamps within amber band', () => {
    const m = APP_SRC.match(
      /\/\* test-exports:start \*\/([\s\S]*?)\/\* test-exports:end \*\//,
    );
    const sandbox = {};
    new Function('exports', m[1] + '\nexports.f = getCommunityHue;')(sandbox);
    for (const cid of [0, 1, 7, 13, 99, 1000]) {
      const h = sandbox.f(cid);
      expect(h).toBeGreaterThanOrEqual(30);
      expect(h).toBeLessThanOrEqual(55);
    }
    // null/undefined community → falls back to a neutral amber centre.
    expect(sandbox.f(null)).toBeGreaterThanOrEqual(30);
    expect(sandbox.f(undefined)).toBeLessThanOrEqual(55);
  });
});
