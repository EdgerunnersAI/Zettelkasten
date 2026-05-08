# User Authentication Design — Supabase Auth + Google OAuth

> **ARCHIVED CONTEXT — pre-migration spec.** Written while the app was hosted on Render.com (legacy, no longer used). Any "Render Dashboard" / "Render env var" reference below should be read as the production environment generally — those env vars now live in the DigitalOcean droplet's container env, not Render. See "Deployment Infrastructure (Canonical)" in the project root `CLAUDE.md` for the live setup.

**Date**: 2026-03-31
**Status**: Draft
**Approach**: Pure client-side Supabase Auth (Approach A)

## Overview

Add end-to-end user authentication to the Zettelkasten Summarizer website using Supabase Auth with Google OAuth. The browser-side `supabase-js` library handles the full OAuth flow (PKCE), session management, and token refresh. The FastAPI backend validates JWTs and scopes KG data per user.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Browser                                                     │
│                                                              │
│  supabase-js (CDN)                                           │
│  ├─ signInWithOAuth('google') → redirect to Google           │
│  ├─ exchangeCodeForSession()  → PKCE code exchange           │
│  ├─ onAuthStateChange()       → UI state updates             │
│  ├─ getSession()              → localStorage (0ms)           │
│  └─ auto token refresh        → background timer             │
│                                                              │
│  fetch('/api/*', { Authorization: Bearer <JWT> })            │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI Backend (Render, port 10000)                        │
│                                                              │
│  website/api/auth.py                                         │
│  ├─ get_current_user()    → JWT validation (PyJWT HS256)     │
│  ├─ get_optional_user()   → same, returns None if no auth    │
│  └─ get_or_create_user()  → links auth UUID → kg_users       │
│                                                              │
│  website/api/routes.py                                       │
│  ├─ GET  /api/me          → requires auth, returns profile   │
│  ├─ GET  /api/graph       → optional auth, user-scoped       │
│  └─ POST /api/summarize   → optional auth, user-scoped       │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  Supabase (PostgreSQL)                                       │
│                                                              │
│  auth.users                → managed by Supabase Auth         │
│  ├─ id (UUID)              → auth.uid()                       │
│  ├─ email                  → Google email                     │
│  └─ raw_user_meta_data     → {full_name, avatar_url}          │
│                                                              │
│  public.kg_users           → linked via trigger               │
│  ├─ render_user_id = auth.users.id::text                      │
│  ├─ display_name  = raw_user_meta_data->>'full_name'          │
│  └─ avatar_url    = raw_user_meta_data->>'avatar_url'         │
└─────────────────────────────────────────────────────────────┘
```

## Components

### C1: Frontend Auth Module

**File**: `website/features/user_auth/js/auth.js`

Responsibilities:
- Initialize supabase-js client with `SUPABASE_URL` and `SUPABASE_ANON_KEY` (injected via `/api/auth/config` endpoint)
- Register `onAuthStateChange` listener to update UI on sign-in/sign-out/token-refresh
- Export `signInWithGoogle()` — calls `supabase.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: origin + '/auth/callback' } })`
- Export `signOut()` — calls `supabase.auth.signOut()`
- Export `getAccessToken()` — returns current session's access_token or null
- Patch all `fetch('/api/*')` calls to include `Authorization: Bearer` header when authenticated

**supabase-js loading strategy**:
```html
<!-- In <head> of index.html -->
<link rel="preconnect" href="https://icmnskseuoteyirljswd.supabase.co">
<link rel="preconnect" href="https://accounts.google.com">
<link rel="preload" href="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2" as="script" crossorigin>
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
```

**Config injection**: Rather than hardcoding Supabase credentials in JS, a new `GET /api/auth/config` endpoint returns the public config:
```json
{
  "supabase_url": "https://icmnskseuoteyirljswd.supabase.co",
  "supabase_anon_key": "sb_publishable_..."
}
```
This keeps credentials centralized in the backend's env vars and avoids committing them to static files.

### C2: Auth Callback Page

**File**: `website/features/user_auth/callback.html`

Minimal HTML page:
- Loads supabase-js
- supabase-js auto-detects `?code=` parameter in URL
- Calls `exchangeCodeForSession()` to complete PKCE flow
- On success: redirects to `/` (or the page the user was on before login)
- On error: shows error message with retry link

### C3: Login UI

**Location**: Injected into `website/static/index.html` header

**Pre-auth state** (visible when not logged in):
```html
<button class="login-btn" onclick="signInWithGoogle()">
  <svg><!-- Google icon --></svg>
  Login
</button>
```

**Post-auth state** (visible when logged in):
```html
<div class="user-menu">
  <img class="user-avatar" src="{avatar_url}" alt="{name}" />
  <span class="user-name">{display_name}</span>
  <button class="logout-btn" onclick="signOut()">Logout</button>
</div>
```

**Styling**:
- Login button: teal accent (`--accent`), positioned in `.header` with `margin-left: auto`
- User menu: horizontal flex, avatar 32px circle, name in `--text-secondary`, logout as text button
- No purple anywhere

### C4: FastAPI Auth Dependency

**File**: `website/api/auth.py`

```python
# Pseudocode — actual implementation during execution
import jwt  # PyJWT
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)

async def get_current_user(credentials) -> dict:
    """Validate Supabase JWT. Returns decoded claims or raises 401."""
    token = credentials.credentials
    payload = jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated")
    return payload  # Contains 'sub' (UUID), 'email', 'user_metadata'

async def get_optional_user(request) -> dict | None:
    """Same as get_current_user but returns None instead of 401."""
    # Extract Bearer token from Authorization header
    # If present and valid, return claims
    # If absent or invalid, return None
```

**Environment**: Requires `SUPABASE_JWT_SECRET` (from Supabase Dashboard > Settings > API > JWT Secret).

### C5: Protected API Routes

**File**: Update `website/api/routes.py`

Changes:
1. **`GET /api/me`** (new) — requires auth, returns user profile:
   ```json
   {"id": "uuid", "email": "...", "name": "...", "avatar_url": "...", "node_count": 5}
   ```

2. **`GET /api/graph`** — add optional auth:
   - Authenticated: return user-scoped graph (kg_nodes where user_id matches)
   - Unauthenticated: return the global/default graph (backwards compatible)

3. **`POST /api/summarize`** — add optional auth:
   - Authenticated: write to user's graph in Supabase
   - Unauthenticated: write to file store only (backwards compatible)

4. **`GET /api/auth/config`** (new) — public, returns Supabase URL + anon key for client-side init

Key change in `_get_supabase()`: Replace hardcoded `"default-web-user"` with the authenticated user's auth UUID when available.

### C6: Supabase Schema

**File**: `supabase/website/user_auth/schema.sql`

```sql
-- Trigger: auto-create kg_users row when a new Supabase auth user signs up
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER SET search_path = ''
AS $$
BEGIN
  INSERT INTO public.kg_users (render_user_id, display_name, email, avatar_url)
  VALUES (
    NEW.id::text,
    NEW.raw_user_meta_data ->> 'full_name',
    NEW.email,
    COALESCE(
      NEW.raw_user_meta_data ->> 'avatar_url',
      NEW.raw_user_meta_data ->> 'picture'
    )
  )
  ON CONFLICT (render_user_id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    email = EXCLUDED.email,
    avatar_url = EXCLUDED.avatar_url,
    updated_at = now();
  RETURN NEW;
END;
$$;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
```

Also adds `TO authenticated` to existing RLS policies for explicitness.

### C7: Feature Registration

1. **`website/features/About.md`** — add `user_auth` entry
2. **`website/app.py`** — mount `/auth/` static files from `website/features/user_auth/`, add `/auth/callback` route

### C8: Settings Update

**File**: `telegram_bot/config/settings.py`

Add optional `SUPABASE_JWT_SECRET` field to Settings model. Loaded from env vars or `.env`.

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `website/features/user_auth/js/auth.js` | CREATE | Client-side auth module (supabase-js) |
| `website/features/user_auth/css/auth.css` | CREATE | Login button, user menu, callback page styles |
| `website/features/user_auth/callback.html` | CREATE | OAuth callback page for PKCE code exchange |
| `website/api/auth.py` | CREATE | FastAPI JWT validation dependency |
| `supabase/website/user_auth/schema.sql` | CREATE | Auth trigger + RLS policy updates |
| `website/static/index.html` | MODIFY | Add login button/user menu in header, load supabase-js |
| `website/static/js/app.js` | MODIFY | Import auth module, add Bearer headers to API calls |
| `website/static/css/style.css` | MODIFY | Add login button and user menu styles |
| `website/api/routes.py` | MODIFY | Add auth dependency, /api/me, /api/auth/config, user-scoping |
| `website/app.py` | MODIFY | Mount user_auth feature, add callback route |
| `website/features/About.md` | MODIFY | Add user_auth entry |
| `telegram_bot/config/settings.py` | MODIFY | Add SUPABASE_JWT_SECRET optional field |
| `ops/.env.example` | MODIFY | Add SUPABASE_JWT_SECRET |

## Performance Budget

| Step | Target | Mechanism |
|------|--------|-----------|
| supabase-js load | <200ms | CDN preload, cached after first visit |
| Google consent screen | <500ms | Preconnect to accounts.google.com |
| Google → Supabase callback | <300ms | Supabase infrastructure |
| Supabase → App redirect | <200ms | Direct redirect |
| PKCE code exchange | <300ms | supabase-js auto-exchange |
| UI update | <50ms | onAuthStateChange + DOM update |
| **Total (after user clicks Allow)** | **<1.5s** | Well under 5s target |

## Future OAuth Providers

Adding GitHub, Twitter, or other providers requires:
1. Enable provider in Supabase Dashboard (paste client ID/secret)
2. Add a button in the login UI: `signInWithOAuth({ provider: 'github' })`
3. No backend changes — same JWT validation, same user linking

The architecture is provider-agnostic by design.

## Manual Setup Required (Not Automated)

These steps must be done by the user in external dashboards:

1. **Google Cloud Console**: Create OAuth client, set redirect URI to `https://icmnskseuoteyirljswd.supabase.co/auth/v1/callback`
2. **Supabase Dashboard**: Authentication > Providers > Enable Google, paste client ID/secret
3. **Supabase Dashboard**: Authentication > URL Configuration > Add redirect URLs:
   - `https://<render-app-url>/auth/callback`
   - `http://localhost:8000/auth/callback` (for local dev)
4. **Supabase Dashboard**: Settings > API > Copy JWT Secret → set as `SUPABASE_JWT_SECRET` env var
5. **Supabase SQL Editor**: Run `supabase/website/user_auth/schema.sql`
6. **Render Dashboard**: Add `SUPABASE_JWT_SECRET` env var

## Testing Strategy

- Unit tests: JWT validation (valid, expired, malformed, missing)
- Unit tests: get_or_create_user with auth UUID
- Unit tests: /api/me returns correct profile
- Unit tests: /api/graph returns user-scoped data when authenticated
- Unit tests: /api/graph returns global data when unauthenticated
- Integration: auth.js initializes without errors
- Integration: login button visible, user menu hidden (pre-auth)
- Integration: callback page handles code exchange
