/**
 * BC-04: TTL expiry contracts for browser_cache storage.
 *
 * `STATE_TTL_MS` = 180 days (cache.js:7); `RETURN_TTL_MS` = 15 minutes (cache.js:8).
 * `readState` (cache.js:128) treats `now() - state.u > STATE_TTL_MS` as expired
 * and returns null → `getState()` falls back to public defaults.
 *
 * Vitest fake timers intercept `Date.now()` deterministically; see Vitest 2.x.
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

const STATE_TTL_MS = 180 * 24 * 60 * 60 * 1000;  // 15_552_000_000
const RETURN_TTL_MS = 15 * 60 * 1000;            // 900_000

describe('BC-04 TTL expiry', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    window.localStorage.clear();
    window.sessionStorage.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('state survives at STATE_TTL_MS - 1ms', () => {
    const cache = loadCache();
    cache.markLoggedIn();  // writes h=1 + u=now()
    vi.advanceTimersByTime(STATE_TTL_MS - 1);
    // Re-load so the fresh IIFE reads localStorage with the advanced clock.
    const cache2 = loadCache();
    expect(cache2.getState().hasLoggedIn).toBe(true);
  });

  it('state expires at STATE_TTL_MS + 1ms (default returned)', () => {
    const cache = loadCache();
    cache.markLoggedIn();
    vi.advanceTimersByTime(STATE_TTL_MS + 1);
    const cache2 = loadCache();
    // Past TTL — readState() returns null → getDefaultPublicState() served.
    expect(cache2.getState().hasLoggedIn).toBe(false);
  });

  it('return-path survives at RETURN_TTL_MS - 1ms', () => {
    const cache = loadCache();
    cache.setReturnPath('/home/zettels');
    vi.advanceTimersByTime(RETURN_TTL_MS - 1);
    expect(cache.consumeReturnPath()).toBe('/home/zettels');
  });

  it('return-path expires at RETURN_TTL_MS + 1ms (null returned)', () => {
    const cache = loadCache();
    cache.setReturnPath('/home/kastens');
    vi.advanceTimersByTime(RETURN_TTL_MS + 1);
    expect(cache.consumeReturnPath()).toBeNull();
  });
});
