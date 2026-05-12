# BC-04 / BC-05 — TTL expiry + malformed-JSON / quota-exceeded recovery

Scope (quoted from `docs/research/full_modular_test_plans/browser_cache.md`):

> **BC-04 P2** — TTL expiry: state expires at 180d, return at 15m (time-mocked)
> **BC-05 P2** — Malformed JSON / quota-exceeded recovery (private mode, Safari ITP)

Verified module facts (from `website/features/browser_cache/js/cache.js`):

| Constant | Value | Line |
|---|---|---|
| `STATE_TTL_MS` | `180 * 24 * 60 * 60 * 1000` = 15 552 000 000 ms | 7 |
| `RETURN_TTL_MS` | `15 * 60 * 1000` = 900 000 ms | 8 |
| `MAX_STATE_BYTES` | 256 | 9 |
| `MAX_RETURN_BYTES` | 96 | 10 |
| `safeParse` | wraps `JSON.parse` in try/catch → returns `null` on error | 38-45 |
| `safeSet` | catches `setItem` throws → returns `false` | 56-66 |
| `storageAvailable` | probes via `setItem('__zk__','x') + removeItem` | 16-22 |

These tests are jsdom-only (no browser needed) per the spec's `--live` policy.

## Executive recommendation

Both tests use **Vitest 2.x fake timers** + a hand-rolled Storage mock fixture. Zero new dependencies. ~80 LoC total for both tests.

- **BC-04**: `vi.useFakeTimers({ shouldAdvanceTime: false })` + `vi.setSystemTime(epoch)` to pin a baseline, write a state, advance by `STATE_TTL_MS - 1` → assert still readable, advance by `+2` → assert `getState()` returns the default (expired). Mirror for `RETURN_TTL_MS` against sessionStorage.
- **BC-05**: Three sub-cases per industry consensus —
  1. **Malformed JSON**: pre-seed `localStorage.setItem(STATE_KEY, '{"broken":}')`, assert `getState()` returns default (clone, not stale corruption), no uncaught exception.
  2. **Quota exceeded**: monkey-patch `Storage.prototype.setItem` to throw `DOMException('QuotaExceededError', 'QuotaExceededError')`, call `markLoggedIn()` → assert it does NOT throw, returns `false`, and the public API stays stable.
  3. **Storage unavailable** (private mode / Safari ITP simulation): delete `window.localStorage` for the test, assert `getState()` returns default without throwing. Defer the actual Safari 7-day ITP cap to BC-07 (cross-browser matrix is the only place it can be tested faithfully).

## Why this pattern (industry citations)

1. **Vitest fake timers are the 2024-2026 jsdom standard** for TTL expiry, replacing Sinon's legacy `useFakeTimers()` outside Jest 28+. See [Vitest fake timers API (2024+)](https://vitest.dev/api/vi.html#vi-usefaketimers). The `shouldAdvanceTime: false` flag prevents async tasks from running on each tick and is the recommended setting for monotonic-clock TTL tests per the Vitest team's 2024 release notes.
2. **`setItem` throwing `QuotaExceededError`** is the canonical browser failure mode under storage pressure or private mode; see [MDN Storage quotas + private browsing (2024)](https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria). The OWASP HTML5 Security Cheat Sheet (Web Storage section) explicitly calls out "always wrap setItem in try/catch" as a hardening primitive — already implemented in `safeSet`; the test pins that behaviour.
3. **Safari ITP 7-day localStorage cap**: documented by Apple at [WebKit blog: Full Third-Party Cookie Blocking and More (2020+, still current)](https://webkit.org/blog/10218/full-third-party-cookie-blocking-and-more/) and reaffirmed in [Apple Privacy Policy 2024](https://support.apple.com/en-us/HT212247). This is **not jsdom-testable** — defer to BC-07 (cross-browser matrix). BC-05 stops at "storage unavailable returns default", which IS jsdom-testable.
4. **Pre-emptive `safeParse` → default on corruption** is a 2024-2026 OWASP-recommended pattern; see [OWASP WSTG §4.11.12 Testing Browser Storage (v4.2, 2024)](https://owasp.org/www-project-web-security-testing-guide/v42/4-Web_Application_Security_Testing/11-Client-side_Testing/12-Testing_Browser_Storage). Restoring to clean default protects against XSS-staged corruption attacks.

## Test sketches (paste-ready, ≤30 lines each)

### BC-04 — TTL expiry (`tests/js/browser_cache/ttl_expiry.test.js`)

```javascript
import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
import { mountWindowStorage } from './_helpers';
// _helpers.js: provides mountWindowStorage() that replaces window.localStorage
// and window.sessionStorage with in-memory Map-backed stubs. Used by BC-01/02/03/06.

describe('BC-04 TTL expiry', () => {
  let api;
  beforeEach(async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    mountWindowStorage();
    // Re-import cache.js so module-level `now()` reads the fake clock.
    vi.resetModules();
    api = (await import('@/website/features/browser_cache/js/cache.js')).default;
  });
  afterEach(() => vi.useRealTimers());

  it('state expires at 180d (15_552_000_000 ms)', () => {
    api.markLoggedIn('user-1');
    vi.advanceTimersByTime(180 * 24 * 60 * 60 * 1000 - 1);
    expect(api.getState().loggedIn).toBe(true);
    vi.advanceTimersByTime(2);
    expect(api.getState().loggedIn).toBe(false);  // default
  });

  it('return-path expires at 15m', () => {
    api.setReturnPath('/home/zettels');
    vi.advanceTimersByTime(15 * 60 * 1000 - 1);
    expect(api.consumeReturnPath()).toBe('/home/zettels');
    api.setReturnPath('/home/kastens');
    vi.advanceTimersByTime(15 * 60 * 1000 + 1);
    expect(api.consumeReturnPath()).toBeNull();
  });
});
```

### BC-05 — corruption + quota recovery (`tests/js/browser_cache/corruption_recovery.test.js`)

```javascript
import { beforeEach, describe, it, expect, vi } from 'vitest';
import { mountWindowStorage } from './_helpers';

describe('BC-05 malformed JSON + quota + unavailable', () => {
  beforeEach(() => { mountWindowStorage(); vi.resetModules(); });

  it('malformed JSON → default state, no throw', async () => {
    window.localStorage.setItem('zk.bc.v1', '{"broken":');
    const api = (await import('@/website/features/browser_cache/js/cache.js')).default;
    expect(() => api.getState()).not.toThrow();
    expect(api.getState().loggedIn).toBe(false);  // default, not undefined
  });

  it('QuotaExceededError on setItem → markLoggedIn returns false, no throw', async () => {
    const api = (await import('@/website/features/browser_cache/js/cache.js')).default;
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError', 'QuotaExceededError');
    });
    expect(() => api.markLoggedIn('user-1')).not.toThrow();
    expect(api.getState().loggedIn).toBe(false);  // never persisted
  });

  it('localStorage missing (Safari ITP / private mode) → default, no throw', async () => {
    Object.defineProperty(window, 'localStorage', { value: undefined, configurable: true });
    const api = (await import('@/website/features/browser_cache/js/cache.js')).default;
    expect(() => api.getState()).not.toThrow();
    expect(api.getState().loggedIn).toBe(false);
  });
});
```

## Comparison matrix (fake-clock approaches)

| Approach | Dep weight | Determinism | jsdom-safe | Async-safe | Verdict |
|---|---|---|---|---|---|
| **Vitest `vi.useFakeTimers()`** | 0 (native) | High | ✓ | ✓ (with `shouldAdvanceTime:false`) | **Recommended** |
| Sinon `useFakeTimers` | +1 dep | High | ✓ | ✓ | Use only if Jest project |
| `mockdate` | +1 dep | Medium (`Date.now` only) | ✓ | ✗ | Skip — drift on setTimeout |
| Hand-rolled `Date.now = () => X` | 0 | Low (leaks across tests) | ✓ | ✗ | Anti-pattern |

## Pragmatic minor modifications for OUR case

- **Dynamic-data scale**: BC-04/05 don't gate the scaling story (they're per-browser state, not server). 180-day state TTL is fine for any zettel volume.
- **Cloudflare path note**: localStorage is not cached by Cloudflare; query-string busting (`?v=YYYYMMDD<letter>`) on script tags is what handles the *deploy-time* cache, NOT the storage-TTL. The two systems are orthogonal — confirm no test conflates them.
- **WAVE-A leftovers**: BC-04 and BC-05 are jsdom-only and complete the module to 7/7 — no `--live` needed. Total estimated effort: ~45 min for both files including `_helpers.js` consolidation.

## Citations

- [Vitest — Fake Timers API (2024+)](https://vitest.dev/api/vi.html#vi-usefaketimers)
- [Vitest 2.0 release notes (2024) — `shouldAdvanceTime` flag](https://vitest.dev/blog/vitest-2)
- [MDN — Storage quotas and eviction criteria (2024)](https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria)
- [MDN — DOMException: QuotaExceededError (2024)](https://developer.mozilla.org/en-US/docs/Web/API/DOMException#quotaexceedederror)
- [OWASP WSTG §4.11.12 — Testing Browser Storage (v4.2, 2024)](https://owasp.org/www-project-web-security-testing-guide/v42/4-Web_Application_Security_Testing/11-Client-side_Testing/12-Testing_Browser_Storage)
- [OWASP HTML5 Security Cheat Sheet — Web Storage (2024)](https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html#local-storage)
- [WebKit Blog — Full Third-Party Cookie Blocking + ITP 7-day localStorage cap (2020, still current)](https://webkit.org/blog/10218/full-third-party-cookie-blocking-and-more/)
- [OWASP Session Management Cheat Sheet (2024)](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
