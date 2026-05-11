/**
 * BC-01: secret-leak invariant for browser_cache storage.
 *
 * No value persisted into `STATE_KEY` (localStorage) or `RETURN_KEY`
 * (sessionStorage) by any public `browserCache` API may contain a
 * JWT-shaped token, a refresh-token-shaped opaque blob, an email
 * address, or a UUID. The cache is meant to hold only:
 *   - tiny flags (a/h: 0|1)
 *   - a same-origin path (l/p, validated by isPath)
 *   - an empty theme string ('t': '')
 *   - timestamps (u/e: number)
 *
 * This test locks that contract across `markLoggedIn`, `markLoggedOut`,
 * `setReturnPath`, and `consumeReturnPath` — the entire public surface
 * that mutates storage. If a future change ever wires a token or PII
 * value into either storage slot, this suite fails.
 *
 * cache.js is an IIFE that attaches to `window.browserCache` on load —
 * mirrored loading pattern from BC-02 / BC-03.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const CACHE_SRC = readFileSync(
  resolve(__dirname, '../../../website/features/browser_cache/js/cache.js'),
  'utf8',
);

function loadCache() {
  delete window.browserCache;
  delete window.ZKBrowserCache;
  window.localStorage.clear();
  window.sessionStorage.clear();
  // eslint-disable-next-line no-new-func
  new Function('window', CACHE_SRC).call(window, window);
  return window.browserCache;
}

function readAll(storage) {
  const out = {};
  for (let i = 0; i < storage.length; i += 1) {
    const k = storage.key(i);
    out[k] = storage.getItem(k);
  }
  return out;
}

function snapshotAll() {
  return {
    ...readAll(window.localStorage),
    ...readAll(window.sessionStorage),
  };
}

// Forensic regexes — JWT (3-segment base64url), UUIDv4-ish, email, and
// long opaque tokens that smell like refresh tokens.
const JWT_RE = /eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/;
const JWT_LOOSE_RE = /eyJ[A-Za-z0-9_-]+\.eyJ/;
const UUID_RE = /[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/i;
const EMAIL_RE = /@/;
const LONG_TOKEN_RE = /[A-Za-z0-9_-]{40,}/; // refresh-token shaped

function assertNoSecrets(snapshot) {
  for (const [k, v] of Object.entries(snapshot)) {
    const s = String(v);
    expect(s, `key=${k} JWT-shaped leak`).not.toMatch(JWT_RE);
    expect(s, `key=${k} JWT-loose leak`).not.toMatch(JWT_LOOSE_RE);
    expect(s, `key=${k} UUID leak`).not.toMatch(UUID_RE);
    expect(s, `key=${k} email leak`).not.toMatch(EMAIL_RE);
    expect(s, `key=${k} long-token leak`).not.toMatch(LONG_TOKEN_RE);
  }
}

describe('BC-01 — no secrets in browser_cache storage', () => {
  let bc;
  beforeEach(() => {
    bc = loadCache();
  });

  it('initial load leaves storage free of secret-shaped values', () => {
    assertNoSecrets(snapshotAll());
  });

  it('markLoggedIn writes no JWT/UUID/email/long-token to storage', () => {
    bc.markLoggedIn();
    assertNoSecrets(snapshotAll());
  });

  it('markLoggedIn payload contains only the documented flags', () => {
    bc.markLoggedIn();
    const raw = window.localStorage.getItem('zk.bc.v1');
    expect(raw).not.toBeNull();
    const obj = JSON.parse(raw);
    // Only the 5 documented keys, nothing else.
    expect(Object.keys(obj).sort()).toEqual(['a', 'h', 'l', 't', 'u']);
    expect(obj.a === 0 || obj.a === 1).toBe(true);
    expect(obj.h === 0 || obj.h === 1).toBe(true);
    expect(typeof obj.l).toBe('string');
    expect(obj.l.startsWith('/')).toBe(true);
    expect(obj.t).toBe('');
    expect(typeof obj.u).toBe('number');
  });

  it('markLoggedOut leaves zero residue (no leaked keys)', () => {
    bc.markLoggedIn();
    bc.setReturnPath('/home/zettels');
    bc.markLoggedOut();
    const snap = snapshotAll();
    assertNoSecrets(snap);
    // Both storage slots must be fully cleared.
    expect(window.localStorage.getItem('zk.bc.v1')).toBeNull();
    expect(window.sessionStorage.getItem('zk.bc.return.v1')).toBeNull();
  });

  it('setReturnPath writes only the validated path + ttl', () => {
    expect(bc.setReturnPath('/home/zettels')).toBe(true);
    assertNoSecrets(snapshotAll());
    const raw = window.sessionStorage.getItem('zk.bc.return.v1');
    expect(raw).not.toBeNull();
    const obj = JSON.parse(raw);
    expect(Object.keys(obj).sort()).toEqual(['e', 'p']);
    expect(obj.p).toBe('/home/zettels');
    expect(typeof obj.e).toBe('number');
  });

  it('setReturnPath rejects a JWT-shaped argument outright', () => {
    const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.signature';
    expect(bc.setReturnPath(jwt)).toBe(false);
    assertNoSecrets(snapshotAll());
    expect(window.sessionStorage.getItem('zk.bc.return.v1')).toBeNull();
  });

  it('setReturnPath rejects an email-shaped argument', () => {
    expect(bc.setReturnPath('user@example.com')).toBe(false);
    assertNoSecrets(snapshotAll());
  });

  it('setReturnPath rejects a UUID-shaped argument', () => {
    expect(bc.setReturnPath('11111111-2222-3333-4444-555555555555')).toBe(false);
    assertNoSecrets(snapshotAll());
  });

  it('consumeReturnPath clears storage and exposes no residue', () => {
    bc.setReturnPath('/home');
    bc.consumeReturnPath();
    assertNoSecrets(snapshotAll());
    expect(window.sessionStorage.getItem('zk.bc.return.v1')).toBeNull();
  });

  it('full sequence (markLoggedIn → setReturnPath → consume → markLoggedOut) leaves no secrets at any step', () => {
    bc.markLoggedIn();
    assertNoSecrets(snapshotAll());
    bc.setReturnPath('/home/zettels');
    assertNoSecrets(snapshotAll());
    bc.consumeReturnPath();
    assertNoSecrets(snapshotAll());
    bc.markLoggedOut();
    assertNoSecrets(snapshotAll());
  });

  it('patchState with attacker-injected token fields is ignored — only documented keys land', () => {
    // Public API surface: patchState only honors allowCredentialStorage,
    // hasLoggedIn, landingPath. Token-like keys must NOT be persisted.
    bc.patchState({
      hasLoggedIn: true,
      accessToken: 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhYmMifQ.sig',
      refreshToken: 'rt_' + 'x'.repeat(64),
      email: 'attacker@example.com',
      userId: '11111111-2222-3333-4444-555555555555',
    });
    assertNoSecrets(snapshotAll());
    const raw = window.localStorage.getItem('zk.bc.v1');
    const obj = JSON.parse(raw);
    expect(Object.keys(obj).sort()).toEqual(['a', 'h', 'l', 't', 'u']);
  });
});
