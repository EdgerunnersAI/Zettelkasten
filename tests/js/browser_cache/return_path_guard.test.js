/**
 * UA-02 / BC-02: setReturnPath / isPath guard regression lock.
 *
 * `setReturnPath` must reject every shape that could be used to redirect
 * the user off-origin or to a javascript: sink. Accept only a same-origin
 * absolute path beginning with a single '/'.
 *
 * cache.js is an IIFE that attaches to `window.browserCache` on load —
 * not an ES module. We exercise the public API via `window.browserCache`
 * after `require`-ing the script through JSDOM.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const CACHE_SRC = readFileSync(
  resolve(__dirname, '../../../website/features/browser_cache/js/cache.js'),
  'utf8',
);

function loadCache() {
  // Reset window between tests. JSDOM provides window+document by default
  // via vitest's `environment: 'jsdom'` config.
  delete window.browserCache;
  delete window.ZKBrowserCache;
  // Clear storage so previous-test state never leaks.
  window.localStorage.clear();
  window.sessionStorage.clear();
  // Execute the IIFE in the JSDOM window.
  // eslint-disable-next-line no-new-func
  new Function('window', CACHE_SRC).call(window, window);
  return window.browserCache;
}

describe('BC-02 / UA-02 — setReturnPath reject suite', () => {
  let bc;
  beforeEach(() => {
    bc = loadCache();
  });

  it('rejects http://evil (absolute URL with scheme)', () => {
    expect(bc.setReturnPath('http://evil')).toBe(false);
  });

  it('rejects https://evil', () => {
    expect(bc.setReturnPath('https://evil')).toBe(false);
  });

  it('rejects protocol-relative //evil', () => {
    expect(bc.setReturnPath('//evil')).toBe(false);
  });

  it('rejects backslash \\\\evil (Windows-style host smuggle)', () => {
    expect(bc.setReturnPath('\\\\evil')).toBe(false);
  });

  it('rejects javascript: URI', () => {
    expect(bc.setReturnPath('javascript:alert(1)')).toBe(false);
  });

  it('rejects data: URI', () => {
    expect(bc.setReturnPath('data:text/html,evil')).toBe(false);
  });

  it('rejects relative path without leading slash', () => {
    expect(bc.setReturnPath('home')).toBe(false);
  });

  it('rejects empty string', () => {
    expect(bc.setReturnPath('')).toBe(false);
  });

  it('rejects null', () => {
    expect(bc.setReturnPath(null)).toBe(false);
  });

  it('rejects undefined', () => {
    expect(bc.setReturnPath(undefined)).toBe(false);
  });

  it('rejects non-string (number)', () => {
    expect(bc.setReturnPath(42)).toBe(false);
  });

  it('rejects path longer than 128 chars', () => {
    expect(bc.setReturnPath('/' + 'a'.repeat(200))).toBe(false);
  });

  it('accepts plain /home', () => {
    expect(bc.setReturnPath('/home')).toBe(true);
  });

  it('accepts /home/zettels with query params', () => {
    // isPath only requires leading single '/', length<=128, not '//'.
    // Query strings are part of the path here.
    expect(bc.setReturnPath('/home/zettels?id=1')).toBe(true);
  });
});

describe('BC-03 — return-path round-trip', () => {
  let bc;
  beforeEach(() => {
    bc = loadCache();
  });

  it('set then consume returns the path then clears', () => {
    expect(bc.setReturnPath('/home/zettels')).toBe(true);
    expect(bc.consumeReturnPath()).toBe('/home/zettels');
    expect(bc.consumeReturnPath()).toBeNull();
  });

  it('consume on empty store returns null', () => {
    expect(bc.consumeReturnPath()).toBeNull();
  });
});
