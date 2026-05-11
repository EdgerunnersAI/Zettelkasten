# Browser Cache

This folder owns the small browser-side cache used by the public landing and auth callback flows to remember non-secret UX hints.

## What This Folder Owns

- A versioned `localStorage` record at `zk.bc.v1` for compact, non-sensitive state:
  - `a`: `1` or `0` for `allowCredentialStorage`
  - `h`: `1` or `0` for `hasLoggedIn`
  - `l`: a same-origin path hint, defaulting to `/home`
  - `t`: a reserved theme placeholder, currently always blank
  - `u`: update time in epoch milliseconds
- A versioned `sessionStorage` record at `zk.bc.return.v1` for short-lived auth return paths:
  - `p`: return path
  - `e`: expiry time in epoch milliseconds
- A frozen browser API exposed as both `window.browserCache` and `window.ZKBrowserCache`.
- Cleanup logic for stale or invalid cache records.

## What It Does Not Own

- Authentication, Supabase session validation, token refresh, cookies, or authorization decisions.
- User profile data, graph data, zettels, kastens, or any server-side persistence.
- Static route registration; `website/app.py` mounts this folder at `/browser-cache/js`.
- The auth UI and callback page that consume this cache.

## Key Files

- `js/cache.js` - Self-contained browser script. Defines storage keys, payload caps, state normalization, return-path helpers, cleanup, and the public API.
- `About.md` - This folder-scoped developer note.

There are no subfolders besides `js/`.

## Entry Points And Public Interfaces

- Static asset URL: `/browser-cache/js/cache.js`, mounted from `website/app.py`.
- Landing page include: `website/static/index.html` loads this script before `/auth/js/auth.js`.
- Auth callback include: `website/features/user_auth/callback.html` loads this script before callback handling code.
- Public API methods:
  - `getState()`
  - `patchState(partial)`
  - `setReturnPath(path)`
  - `consumeReturnPath()`
  - `cleanup()`
  - `markLoggedIn()`
  - `markLoggedOut()`
  - `isLoggedInHint()`
  - `getLandingPath()`
  - `setLandingPath(path)`
  - `getThemePlaceholder()`

Consumers must treat every value as a hint. The cache is not security truth.

## Representative Runtime Flows

- Page load:
  - `cache.js` runs immediately, removes expired or invalid cache entries, and exposes `window.browserCache` plus `window.ZKBrowserCache`.
- Returning visitor on `/`:
  - `website/features/user_auth/js/auth.js` calls `getState()` when deciding whether to patch hints and redirect an authenticated landing-page session to `/home`.
- OAuth start:
  - `auth.js` calls `setReturnPath('/home')` before redirecting to Supabase OAuth.
- OAuth callback:
  - `callback.html` patches `hasLoggedIn`, `allowCredentialStorage`, and `landingPath`.
  - It then calls `consumeReturnPath()` when available, falls back to the raw storage key or legacy `auth_return_to`, validates the path, and redirects.
- Email sign-in/sign-out:
  - Successful email sign-in patches logged-in hints.
  - Sign-out patches `hasLoggedIn` and `allowCredentialStorage` to false; default empty state removes the localStorage record.

## Dependencies And External Contracts

- Browser APIs: `window`, `localStorage`, `sessionStorage`, `JSON`, and `Date.now()`.
- Web Storage can throw or be unavailable; all storage reads, writes, and removals are wrapped in safe helpers.
- `localStorage` payloads are capped at 256 JSON characters.
- `sessionStorage` return-path payloads are capped at 96 JSON characters.
- State records expire after 180 days when they include an old `u` timestamp.
- Return-path records expire after 15 minutes.
- Paths must be strings, begin with `/`, be at most 128 characters, and not begin with `//`.

## How To Extend Safely

- Keep the stored schema compact and versioned. If a new persisted field is needed, add it to normalization, public-state conversion, writing, and cleanup together.
- Store only UX hints. Never store passwords, tokens, API keys, cookies, provider profile payloads, email addresses, or authorization state.
- Preserve optional-consumer behavior. `auth.js` has fallbacks for missing `window.browserCache`, so new consumers should also degrade gracefully.
- Preserve path validation for any redirect-related value.
- Keep payloads below the existing caps or deliberately update the caps and this doc together.
- If storage key names or public method names change, update `auth.js`, `callback.html`, and the static HTML includes in the same change.

## Testing And Debugging Notes

- There is no dedicated browser-cache test file in the current test tree.
- Use `rg -n "zk\\.bc|window\\.browserCache|browser-cache/js" website tests` to find current consumers.
- For route-level smoke coverage, exercise `GET /browser-cache/js/cache.js` through `website.app.create_app()` or a running dev server.
- In DevTools, inspect `localStorage["zk.bc.v1"]` and `sessionStorage["zk.bc.return.v1"]`; both should contain compact JSON only.
- To debug redirect issues, check whether `consumeReturnPath()` returns a path once and then removes `zk.bc.return.v1`.
- A route smoke check attempted during this doc update timed out while importing the full FastAPI app in this environment, so the mounted path claim is verified from `website/app.py`, not from a completed runtime request.

## Invariants, Gotchas, And Known Risks

- This cache must remain non-secret and non-authoritative.
- `getState()` returns default public state when localStorage is missing, blocked, stale, invalid, or empty.
- `patchState()` removes the localStorage record when the next state is the default logged-out `/home` state.
- `markLoggedOut()` removes both storage keys.
- The callback page still has a legacy fallback to `auth_return_to`.
- `safeSet()` measures `JSON.stringify(data).length`, not encoded byte length.
- The path validator allows same-origin path strings only; it does not parse route existence.

## Related Docs

- `website/features/About.md`
- `website/features/user_auth/js/auth.js`
- `website/features/user_auth/callback.html`
- `website/app.py`
- MDN Web Storage API: https://developer.mozilla.org/en-US/docs/Web/API/Web_Storage_API
- OWASP HTML5 Security Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html
