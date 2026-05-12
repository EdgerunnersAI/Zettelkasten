/**
 * BC-05: malformed JSON + quota-exceeded + storage-unavailable recovery.
 *
 * Three failure modes the OWASP HTML5 Security Cheat Sheet (Web Storage)
 * names as mandatory hardening primitives:
 *   1. Malformed JSON in storage → safeParse returns null → default state
 *   2. setItem throws QuotaExceededError → safeSet returns false → no throw
 *   3. window.localStorage missing (Safari ITP / private mode) → default
 *
 * jsdom Storage stubs are mutable; we spy on setItem for case 2 and remove
 * the localStorage descriptor entirely for case 3.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const CACHE_SRC = readFileSync(
  resolve(__dirname, '../../../website/features/browser_cache/js/cache.js'),
  'utf8',
);

function loadCache() {
  delete window.browserCache;
  delete window.ZKBrowserCache;
  // eslint-disable-next-line no-new-func
  new Function('window', CACHE_SRC).call(window, window);
  return window.browserCache;
}

const STATE_KEY = 'zk.bc.v1';

describe('BC-05 corruption + quota + unavailable recovery', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it('malformed JSON in localStorage → default state, no throw', () => {
    window.localStorage.setItem(STATE_KEY, '{"broken":');
    expect(() => loadCache()).not.toThrow();
    const cache = loadCache();
    expect(() => cache.getState()).not.toThrow();
    expect(cache.getState().hasLoggedIn).toBe(false);  // default — anonymous
  });

  it('localStorage with non-JSON garbage → default state', () => {
    window.localStorage.setItem(STATE_KEY, 'not-even-close-to-json');
    const cache = loadCache();
    expect(cache.getState().hasLoggedIn).toBe(false);
  });

  it('QuotaExceededError on setItem → markLoggedIn does not throw', () => {
    const cache = loadCache();
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError', 'QuotaExceededError');
    });
    expect(() => cache.markLoggedIn()).not.toThrow();
    // State was never persisted — re-read returns default.
    setItemSpy.mockRestore();
    window.localStorage.clear();
    const cache2 = loadCache();
    expect(cache2.getState().hasLoggedIn).toBe(false);
  });

  it('QuotaExceededError on setReturnPath → no throw, path not stored', () => {
    const cache = loadCache();
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError', 'QuotaExceededError');
    });
    expect(() => cache.setReturnPath('/home/zettels')).not.toThrow();
    setItemSpy.mockRestore();
    // consumeReturnPath returns null because nothing was persisted.
    expect(cache.consumeReturnPath()).toBeNull();
  });

  it('localStorage missing (Safari ITP / private mode sim) → default, no throw', () => {
    const original = Object.getOwnPropertyDescriptor(window, 'localStorage');
    Object.defineProperty(window, 'localStorage', { value: undefined, configurable: true });
    try {
      expect(() => loadCache()).not.toThrow();
      const cache = loadCache();
      expect(cache.getState().hasLoggedIn).toBe(false);
    } finally {
      if (original) Object.defineProperty(window, 'localStorage', original);
    }
  });
});
