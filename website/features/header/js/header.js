/* Shared header behavior — single source of truth for back button, avatar dropdown,
 * AND avatar image loading. Pages include the rendered header markup via the
 * <!--ZK_HEADER--> server-side placeholder, then call ZKHeader.boot(getToken).
 *
 * Robustness guarantees (defense in depth):
 *   1. If this script never runs, CSS keeps the fallback (user glyph) visible — never a broken <img>.
 *   2. If /api/me fails, we fall back to localStorage, then to a random avatar, then to initial.
 *   3. Avatar URL is PRE-LOADED with new Image() before being committed to the visible <img>.
 *      A failed preload never touches the DOM — broken images can never render.
 *   4. On preload failure, we retry once with a cache-bust query string, then commit initial-letter fallback.
 *   5. All page-local avatar logic has been removed; home-picker uses ZKHeader.setAvatarById(id) to update.
 */
(function () {
  'use strict';

  var AVATAR_COUNT = 60;
  var AVATAR_PATH_RE = /\/artifacts\/avatars\/avatar_\d+\.svg/;
  var CACHE_KEY_PREFIX = 'zk-avatar-url-';

  // ── DOM refs (resolved in init) ───────────────────────────────────
  var refs = {};

  function resolveRefs() {
    refs.backBtn      = document.querySelector('[data-zk-back]');
    refs.avatarBtn    = document.getElementById('avatar-btn');
    refs.avatarImg    = document.getElementById('avatar-img');
    refs.avatarFb     = document.getElementById('avatar-fallback');
    refs.avatarDrop   = document.getElementById('avatar-dropdown');
    refs.avatarWrap   = document.getElementById('avatar-wrap');
  }

  function avatarUrlFor(id) {
    var safeId = Math.max(0, Math.min(AVATAR_COUNT - 1, parseInt(id, 10) || 0));
    return '/artifacts/avatars/avatar_' + String(safeId).padStart(2, '0') + '.svg';
  }

  function idFromUrl(url) {
    if (!url) return null;
    var m = String(url).match(/avatar_(\d+)\.svg/);
    return m ? parseInt(m[1], 10) : null;
  }

  function cacheKey(profileId) {
    return CACHE_KEY_PREFIX + (profileId || 'anon');
  }

  function readCached(profileId) {
    try {
      var v = localStorage.getItem(cacheKey(profileId));
      return (v && AVATAR_PATH_RE.test(v)) ? v : null;
    } catch (_) { return null; }
  }

  function writeCached(profileId, url) {
    try { localStorage.setItem(cacheKey(profileId), url); } catch (_) {}
  }

  /** Preload a URL through new Image(); only resolve once the browser confirms it's valid. */
  function preload(url, timeoutMs) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      var done = false;
      var timer = setTimeout(function () {
        if (done) return;
        done = true;
        reject(new Error('preload-timeout'));
      }, timeoutMs || 5000);
      img.onload = function () {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve(url);
      };
      img.onerror = function () {
        if (done) return;
        done = true;
        clearTimeout(timer);
        reject(new Error('preload-error'));
      };
      img.src = url;
    });
  }

  /** Render a proven-good URL into the <img>. */
  function commitImage(url) {
    if (!refs.avatarImg) return;
    refs.avatarImg.onload = null;
    refs.avatarImg.onerror = null;
    refs.avatarImg.removeAttribute('hidden');
    refs.avatarImg.src = url;
    refs.avatarImg.classList.add('loaded');
    refs.avatarImg.classList.remove('hidden');
    if (refs.avatarFb) refs.avatarFb.classList.add('hidden');
  }

  /** Terminal fallback: show user initial (or generic glyph if no name). */
  function commitInitial(profile) {
    if (!refs.avatarFb) return;
    var seed = (profile && (profile.name || profile.email || profile.display_name)) || '';
    var initial = seed ? seed.trim().charAt(0).toUpperCase() : '';
    if (initial) {
      refs.avatarFb.textContent = initial;
    }
    // Keep the generic glyph if we have no initial — do nothing, SVG stays.
    if (refs.avatarImg) {
      refs.avatarImg.classList.remove('loaded');
      refs.avatarImg.setAttribute('hidden', '');
    }
    refs.avatarFb.classList.remove('hidden');
  }

  /** Pick the URL we'll attempt: server > localStorage > random. Persist random to server + cache. */
  async function resolveAvatarUrl(profile, getToken) {
    var profileId = (profile && profile.id) || null;
    var serverUrl = profile && profile.avatar_url;
    if (serverUrl && AVATAR_PATH_RE.test(serverUrl)) {
      writeCached(profileId, serverUrl);
      return { url: serverUrl, source: 'server' };
    }
    var cached = readCached(profileId);
    if (cached) return { url: cached, source: 'cache' };

    // Assign a deterministic-ish random avatar
    var randomId = Math.floor(Math.random() * AVATAR_COUNT);
    var url = avatarUrlFor(randomId);
    writeCached(profileId, url);
    // Fire-and-forget persist to server (don't block avatar render on this)
    if (getToken) {
      try {
        var token = typeof getToken === 'function' ? await getToken() : getToken;
        if (token) {
          fetch('/api/me/avatar', {
            method: 'PUT',
            headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
            body: JSON.stringify({ avatar_id: randomId })
          }).catch(function () { /* non-blocking */ });
        }
      } catch (_) { /* non-blocking */ }
    }
    return { url: url, source: 'random', id: randomId };
  }

  /** Main avatar lifecycle: try URL → preload → commit. On failure, retry cache-busted; else initial. */
  async function loadAvatar(profile, getToken) {
    if (!refs.avatarImg) return;
    try {
      var picked = await resolveAvatarUrl(profile || {}, getToken);
      try {
        await preload(picked.url);
        commitImage(picked.url);
        return;
      } catch (err) {
        // Retry once with cache-bust (handles stale 304/CORS edge cases)
        try {
          var bustUrl = picked.url + '?v=' + Date.now();
          await preload(bustUrl);
          commitImage(picked.url); // commit the clean URL now that we've proven it's reachable
          return;
        } catch (err2) {
          console.warn('[ZKHeader] avatar preload failed twice for', picked.url, err2 && err2.message);
          // If we were trying a cached/server URL, try one more time with a fresh random
          if (picked.source !== 'random') {
            try {
              var rid = Math.floor(Math.random() * AVATAR_COUNT);
              var rurl = avatarUrlFor(rid);
              await preload(rurl);
              writeCached((profile && profile.id) || null, rurl);
              commitImage(rurl);
              return;
            } catch (_) { /* fall through */ }
          }
        }
      }
    } catch (err) {
      console.warn('[ZKHeader] avatar resolution failed', err && err.message);
    }
    // Terminal fallback
    commitInitial(profile || {});
  }

  /** Fetch /api/me with a bearer token, tolerant of network / 401. */
  async function fetchProfile(token) {
    if (!token) return null;
    try {
      var resp = await fetch('/api/me', {
        headers: { 'Authorization': 'Bearer ' + token },
        cache: 'no-store'
      });
      if (!resp.ok) return null;
      return await resp.json();
    } catch (_) { return null; }
  }

  function bindBackButton() {
    if (!refs.backBtn || refs.backBtn.dataset.zkBound) return;
    refs.backBtn.dataset.zkBound = '1';
    refs.backBtn.addEventListener('click', function () {
      if (window.history.length > 1) window.history.back();
      else window.location.href = '/home';
    });
  }

  function bindAvatarDropdown() {
    if (!refs.avatarBtn || !refs.avatarDrop || refs.avatarBtn.dataset.zkBound) return;
    refs.avatarBtn.dataset.zkBound = '1';

    // m-8: every close path MUST also reset aria-expanded — screen readers
    // otherwise announce the menu as still expanded after Escape / focus loss.
    function closeDropdown() {
      if (!refs.avatarDrop.classList.contains('open')) return;
      refs.avatarDrop.classList.remove('open');
      refs.avatarBtn.setAttribute('aria-expanded', 'false');
    }

    // WCAG 2.2 4.1.2 / WAI-ARIA 1.2 menubutton: aria-expanded mirrors .open state.
    refs.avatarBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      var nowOpen = refs.avatarDrop.classList.toggle('open');
      refs.avatarBtn.setAttribute('aria-expanded', nowOpen ? 'true' : 'false');
    });
    document.addEventListener('click', function (e) {
      if (refs.avatarWrap && !refs.avatarWrap.contains(e.target)) {
        closeDropdown();
      }
    });
    // m-8: Escape key closes the dropdown from anywhere.
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeDropdown();
    });
    // m-8: focus leaving the avatar wrap closes the dropdown (focusout
    // bubbles, unlike blur). Schedule on microtask so the relatedTarget
    // check sees the post-focus state — without that delay, clicking a
    // dropdown menu item would close before the click handler fires.
    if (refs.avatarWrap) {
      refs.avatarWrap.addEventListener('focusout', function (e) {
        var next = e.relatedTarget;
        if (!next || !refs.avatarWrap.contains(next)) closeDropdown();
      });
    }
  }

  function initBasics() {
    resolveRefs();
    bindBackButton();
    bindAvatarDropdown();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBasics);
  } else {
    initBasics();
  }

  // ── Public API ──────────────────────────────────────────────────────
  window.ZKHeader = {
    /**
     * Boot avatar loading for the current page.
     * @param {Function|string} getToken - () => Promise<string>|string  or raw bearer string
     * @param {Object} [options]
     * @param {Object} [options.profile] - pre-fetched profile (skips /api/me call)
     */
    boot: async function (getToken, options) {
      options = options || {};
      if (!refs.avatarImg) resolveRefs();
      var profile = options.profile || null;
      if (!profile && getToken) {
        try {
          var token = typeof getToken === 'function' ? await getToken() : getToken;
          profile = await fetchProfile(token);
        } catch (_) { profile = null; }
      }
      await loadAvatar(profile, getToken);
      // iter-03 §UI: surface a stable signal harness/automation can read to
      // verify every authed page actually called boot() (the missing-boot bug
      // is invisible in the DOM otherwise — partial renders, img stays blank).
      try { window.ZKHeader.__booted = true; } catch (_) { /* no-op */ }
      return profile;
    },

    /**
     * Force-update the visible avatar to a specific ID (used by the home picker).
     * Does its own preload; never breaks the display on failure.
     */
    setAvatarById: async function (avatarId, getToken, profileId) {
      var url = avatarUrlFor(avatarId);
      try {
        await preload(url);
        writeCached(profileId || null, url);
        commitImage(url);
      } catch (_) {
        console.warn('[ZKHeader] setAvatarById preload failed for id', avatarId);
      }
      // Persist (non-blocking)
      if (getToken) {
        try {
          var token = typeof getToken === 'function' ? await getToken() : getToken;
          if (token) {
            fetch('/api/me/avatar', {
              method: 'PUT',
              headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
              body: JSON.stringify({ avatar_id: avatarId })
            }).catch(function () {});
          }
        } catch (_) {}
      }
    },

    /** Bind a sign-out handler. Keeps Supabase auth out of the shared header. */
    onSignOut: function (handler) {
      var btn = document.getElementById('menu-signout');
      if (!btn || typeof handler !== 'function' || btn.dataset.zkSignoutBound) return;
      btn.dataset.zkSignoutBound = '1';
      btn.addEventListener('click', function () {
        try { handler(); } catch (e) { console.error('[ZKHeader] signOut handler threw', e); }
      });
    },

    /** Expose lightweight helpers for pages that need them (e.g. home picker). */
    _internal: { avatarUrlFor: avatarUrlFor, idFromUrl: idFromUrl }
  };
})();
