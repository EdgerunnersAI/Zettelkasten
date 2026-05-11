/**
 * UA-04: OAuth state CSRF mismatch regression lock.
 *
 * The codebase delegates OAuth state validation to the Supabase JS SDK
 * (`auth.exchangeCodeForSession` performs PKCE/state checks internally
 * and rejects state mismatches with an error). Our regression invariant:
 *
 *   1. callback.html MUST call `exchangeCodeForSession(window.location.href)`
 *      (NOT a hand-rolled hash parser that could skip state validation).
 *   2. callback.html MUST surface SDK errors to the user — i.e. show the
 *      error block, stop the spinner, and avoid redirecting on failure.
 *   3. auth.js MUST configure `detectSessionInUrl: true` so the SDK
 *      enforces state on session restore.
 *
 * If any of these invariants flips, this test fails and forces a manual
 * review before a CSRF-bypass regression ships.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const CALLBACK_HTML = readFileSync(
  resolve(__dirname, '../../../website/features/user_auth/callback.html'),
  'utf8',
);
const AUTH_JS = readFileSync(
  resolve(__dirname, '../../../website/features/user_auth/js/auth.js'),
  'utf8',
);

describe('UA-04 OAuth state CSRF — SDK delegation invariants', () => {
  it('callback.html uses exchangeCodeForSession (SDK state check)', () => {
    expect(CALLBACK_HTML).toMatch(/exchangeCodeForSession\s*\(/);
  });

  it('callback.html does NOT hand-roll a hash parser around access_token', () => {
    // A hand-rolled `location.hash.split('access_token=')` style parser
    // would bypass Supabase's state-CSRF check. Forbid that pattern.
    expect(CALLBACK_HTML).not.toMatch(/location\.hash\s*\.\s*split\s*\(\s*['"]access_token/);
    expect(CALLBACK_HTML).not.toMatch(/hash\.match\s*\(\s*\/access_token/);
  });

  it('callback.html surfaces SDK errors to user (no silent redirect)', () => {
    // The error block must hide the spinner and reveal the error element
    // before throwing, so an attacker forging a state cannot land on /home.
    expect(CALLBACK_HTML).toMatch(/spinnerEl\.style\.display\s*=\s*['"]none/);
    expect(CALLBACK_HTML).toMatch(/errorEl\.style\.display\s*=\s*['"]block/);
    expect(CALLBACK_HTML).toMatch(/if\s*\(\s*result\.error\s*\)\s*throw\s+result\.error/);
  });

  it('auth.js enables detectSessionInUrl so SDK enforces state on restore', () => {
    expect(AUTH_JS).toMatch(/detectSessionInUrl\s*:\s*true/);
  });

  it('callback.html re-applies isSafePath after consumeReturnPath (defence in depth)', () => {
    // Even if returnTo came from a trusted store, validate the shape before
    // window.location.replace — protects against state corruption.
    expect(CALLBACK_HTML).toMatch(/isSafePath\s*\(\s*returnTo\s*\)/);
  });
});
