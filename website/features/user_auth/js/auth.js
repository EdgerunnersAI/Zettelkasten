/**
 * Zettelkasten Auth Module
 *
 * Landing-page auth behavior:
 * - Keeps browser storage minimal (no token persistence in custom cache)
 * - Remembers landing preference (/home) and whether user opted to keep browser login
 * - Redirects authenticated users from / to /home consistently
 * - Hydrates profile data from /api/me so avatar rendering is stable
 */

(function () {
  'use strict';

  var DEFAULT_AVATAR = '/artifacts/avatars/avatar_00.svg';

  var _supabaseClient = null;
  var _currentSession = null;

  var loginBtn, loginArrow, providerGrid, userMenu, userAvatar, userName;
  var loginModal, modalOverlay, modalClose, loginForm, loginEmail, loginPassword;
  var loginError, oauthGoogle, modalProviders;
  var AUTH_PROVIDERS = ['google', 'github', 'apple', 'twitter', 'facebook', 'twitch'];

  function resolveDOM() {
    loginBtn = document.getElementById('login-btn');
    loginArrow = document.getElementById('login-arrow');
    providerGrid = document.getElementById('provider-grid');
    userMenu = document.getElementById('user-menu');
    userAvatar = document.getElementById('user-avatar');
    userName = document.getElementById('user-name');
    loginModal = document.getElementById('login-modal');
    modalOverlay = document.getElementById('modal-overlay');
    modalClose = document.getElementById('modal-close');
    loginForm = document.getElementById('login-form');
    loginEmail = document.getElementById('login-email');
    loginPassword = document.getElementById('login-password');
    loginError = document.getElementById('login-error');
    oauthGoogle = document.getElementById('oauth-google');
    modalProviders = document.querySelectorAll('.modal-provider-btn');
  }

  function isLandingPage() {
    return window.location.pathname === '/';
  }

  function isKnownProvider(value) {
    return AUTH_PROVIDERS.indexOf(value) !== -1;
  }

  function getCacheState() {
    if (!window.browserCache || typeof window.browserCache.getState !== 'function') {
      return {
        allowCredentialStorage: false,
        hasLoggedIn: false,
        landingPath: '/home',
        theme: '',
        updatedAt: 0,
      };
    }
    return window.browserCache.getState();
  }

  function patchCacheState(partial) {
    if (!window.browserCache || typeof window.browserCache.patchState !== 'function') return;
    window.browserCache.patchState(partial);
  }

  function setReturnPath(path) {
    if (window.browserCache && typeof window.browserCache.setReturnPath === 'function') {
      window.browserCache.setReturnPath(path);
      return;
    }
    try {
      sessionStorage.setItem('auth_return_to', path);
    } catch (_err) {
      // noop
    }
  }

  function buildFallbackAvatar(label) {
    var text = (label || 'User').trim().charAt(0).toUpperCase() || 'U';
    var bg = '#102a31';
    var fg = '#d7f7f2';
    var svg =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" role="img" aria-label="' +
      escapeHtml(text) +
      '">' +
      '<rect width="96" height="96" rx="48" fill="' + bg + '"/>' +
      '<circle cx="48" cy="38" r="18" fill="' + fg + '" fill-opacity="0.92"/>' +
      '<path d="M20 84c4.5-17 16.7-25 28-25s23.5 8 28 25" fill="' + fg + '" fill-opacity="0.92"/>' +
      '<text x="48" y="56" text-anchor="middle" font-family="Inter, Arial, sans-serif" font-size="32" font-weight="700" fill="' +
      bg +
      '">' +
      escapeHtml(text) +
      '</text>' +
      '</svg>';
    return 'data:image/svg+xml;charset=UTF-8,' + encodeURIComponent(svg);
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function normalizeAvatarUrl(avatar) {
    if (!avatar || typeof avatar !== 'string') return DEFAULT_AVATAR;

    var trimmed = avatar.trim();
    if (!trimmed) return DEFAULT_AVATAR;

    if (trimmed.indexOf('data:image/') === 0 || trimmed.indexOf('blob:') === 0) {
      return trimmed;
    }

    try {
      var resolved = new URL(trimmed, window.location.origin);
      if (resolved.protocol === 'https:' || resolved.protocol === 'http:') {
        return resolved.href;
      }
    } catch (_err) {
      // fall through to default avatar
    }

    return DEFAULT_AVATAR;
  }

  function applyAvatar(avatar, altText) {
    if (!userAvatar) return;
    var resolved = normalizeAvatarUrl(avatar);
    var fallback = buildFallbackAvatar(altText);
    var fellBack = false;

    userAvatar.alt = altText || 'User';
    userAvatar.decoding = 'async';
    userAvatar.loading = 'eager';
    userAvatar.referrerPolicy = 'no-referrer';

    userAvatar.onerror = function () {
      if (fellBack) return;
      fellBack = true;
      userAvatar.onerror = null;
      userAvatar.src = fallback;
    };

    userAvatar.onload = function () {
      if (fellBack) return;
      userAvatar.onerror = null;
    };

    userAvatar.src = resolved;
  }

  async function fetchProfile(session) {
    if (!session || !session.access_token) return null;
    try {
      var resp = await fetch('/api/me', {
        headers: { Authorization: 'Bearer ' + session.access_token },
      });
      if (!resp.ok) return null;
      return await resp.json();
    } catch (_err) {
      return null;
    }
  }

  async function maybeRedirectAuthenticated(session) {
    if (!session || !session.user || !isLandingPage()) return;
    // Prevent redirect loop: if /home just sent us back, don't bounce again
    var lastRedirect = parseInt(sessionStorage.getItem('zk-auth-redirect') || '0', 10);
    if (Date.now() - lastRedirect < 5000) {
      console.warn('[auth] Redirect loop detected, staying on landing page');
      return;
    }
    sessionStorage.setItem('zk-auth-redirect', String(Date.now()));
    var state = getCacheState();
    if (!state.hasLoggedIn) {
      patchCacheState({
        hasLoggedIn: true,
        allowCredentialStorage: true,
        landingPath: '/home',
      });
    }
    window.location.replace('/home');
  }

  function buildUserName(session, profile) {
    if (profile && profile.name) return profile.name;
    var meta = session && session.user ? (session.user.user_metadata || {}) : {};
    return meta.full_name || (profile && profile.email) || (session && session.user && session.user.email) || 'User';
  }

  function buildAvatar(session, profile) {
    if (profile && profile.avatar_url) return profile.avatar_url;
    var meta = session && session.user ? (session.user.user_metadata || {}) : {};
    return meta.avatar_url || meta.picture || DEFAULT_AVATAR;
  }

  function updateUI(session, profile) {
    if (!loginBtn || !userMenu) return;

    if (session && session.user) {
      loginBtn.style.display = 'none';
      if (loginArrow) loginArrow.style.display = 'none';
      userMenu.style.display = 'flex';

      var displayName = buildUserName(session, profile);
      if (userName) {
        userName.textContent = displayName;
      }
      applyAvatar(buildAvatar(session, profile), displayName);
      return;
    }

    loginBtn.style.display = 'flex';
    if (loginArrow) loginArrow.style.display = 'flex';
    userMenu.style.display = 'none';
  }

  async function handleSession(eventName, session) {
    _currentSession = session;
    updateUI(session, null);

    if (!session || !session.user) {
      if (eventName === 'SIGNED_OUT') {
        patchCacheState({ hasLoggedIn: false, allowCredentialStorage: false });
      }
      return;
    }

    var profile = await fetchProfile(session);
    updateUI(session, profile);

    patchCacheState({
      hasLoggedIn: true,
      allowCredentialStorage: true,
      landingPath: '/home',
    });

    if (eventName === 'SIGNED_IN') {
      if (loginModal) closeModal();
      if (isLandingPage()) {
        sessionStorage.setItem('zk-auth-redirect', String(Date.now()));
        sessionStorage.removeItem('zk-home-redirect');
        window.location.replace('/home');
      }
      return;
    }

    await maybeRedirectAuthenticated(session);
  }

  function createSupabaseClient(config) {
    return supabase.createClient(config.supabase_url, config.supabase_anon_key, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
        storage: window.localStorage,
        storageKey: 'zk-auth-token',
      },
    });
  }

  async function init() {
    try {
      var resp = await fetch('/api/auth/config');
      var config = await resp.json();

      if (!config.supabase_url || !config.supabase_anon_key) {
        if (loginBtn) loginBtn.style.display = 'none';
        return;
      }

      _supabaseClient = createSupabaseClient(config);

      _supabaseClient.auth.onAuthStateChange(function (event, session) {
        handleSession(event, session);
      });

      // Resolve the public ready Promise so pricing.js (and any other peer)
      // can grab the client now instead of constructing a duplicate.
      if (window.ZKAuth && typeof window.ZKAuth.__signalReady === 'function') {
        window.ZKAuth.__signalReady();
      }

      var result = await _supabaseClient.auth.getSession();
      await handleSession('RESTORE', result.data.session);

      bindEvents();
    } catch (err) {
      console.error('[auth] Init failed:', err);
    }
  }

  function bindEvents() {
    if (loginBtn) {
      loginBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        openModal();
      });
    }

    if (loginArrow) {
      loginArrow.addEventListener('click', function (e) {
        e.stopPropagation();
        if (providerGrid) providerGrid.classList.toggle('open');
      });
    }

    document.addEventListener('click', function (e) {
      if (providerGrid && !providerGrid.contains(e.target) && e.target !== loginArrow) {
        providerGrid.classList.remove('open');
      }
    });

    var gridItems = document.querySelectorAll('.provider-item');
    gridItems.forEach(function (item) {
      item.addEventListener('click', function () {
        var provider = item.getAttribute('data-provider');
        if (!isKnownProvider(provider)) return;
        providerGrid.classList.remove('open');
        signInWithProvider(provider);
      });
    });

    if (modalClose) modalClose.addEventListener('click', closeModal);
    if (modalOverlay) modalOverlay.addEventListener('click', closeModal);

    if (loginForm) {
      loginForm.addEventListener('submit', function (e) {
        e.preventDefault();
        signInWithEmail();
      });
    }

    if (oauthGoogle) {
      oauthGoogle.addEventListener('click', function () {
        signInWithProvider('google');
      });
    }

    if (modalProviders) {
      modalProviders.forEach(function (btn) {
        btn.addEventListener('click', function () {
          var provider = btn.getAttribute('data-provider');
          if (!isKnownProvider(provider)) return;
          signInWithProvider(provider);
        });
      });
    }

    var logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
      logoutBtn.addEventListener('click', function () {
        signOut();
      });
    }
  }

  function openModal() {
    if (!loginModal) return;
    loginModal.classList.add('open');
    document.body.style.overflow = 'hidden';
    if (loginEmail) loginEmail.focus();
  }

  function closeModal() {
    if (!loginModal) return;
    loginModal.classList.remove('open');
    document.body.style.overflow = '';
    if (loginError) {
      loginError.textContent = '';
      loginError.style.display = 'none';
    }
  }

  async function signInWithProvider(provider) {
    if (!_supabaseClient) return;

    setReturnPath('/home');
    closeModal();

    var result = await _supabaseClient.auth.signInWithOAuth({
      provider: provider,
      options: {
        redirectTo: window.location.origin + '/auth/callback',
      },
    });

    if (result.error) {
      showError('OAuth sign-in failed: ' + result.error.message);
    }
  }

  async function signInWithEmail() {
    if (!_supabaseClient || !loginEmail || !loginPassword) return;

    var email = loginEmail.value.trim();
    var password = loginPassword.value;

    if (!email || !password) {
      showError('Please enter both email and password.');
      return;
    }

    var result = await _supabaseClient.auth.signInWithPassword({ email: email, password: password });

    if (!result.error) {
      patchCacheState({ hasLoggedIn: true, allowCredentialStorage: true, landingPath: '/home' });
      if (isLandingPage()) window.location.replace('/home');
      return;
    }

    if (result.error.message.toLowerCase().indexOf('invalid login') !== -1) {
      var signup = await _supabaseClient.auth.signUp({ email: email, password: password });
      if (signup.error) {
        showError(signup.error.message);
      } else if (signup.data.user && !signup.data.session) {
        showError('Check your email to confirm your account.');
      }
      return;
    }

    showError(result.error.message);
  }

  function showError(msg) {
    if (!loginError) return;
    loginError.textContent = msg;
    loginError.style.display = 'block';
  }

  async function signOut() {
    if (!_supabaseClient) return;
    await _supabaseClient.auth.signOut();
    _currentSession = null;
    updateUI(null, null);
    patchCacheState({ hasLoggedIn: false, allowCredentialStorage: false });
  }

  window.getAuthToken = function () {
    return _currentSession ? _currentSession.access_token : null;
  };

  window.signInWithGoogle = function () { signInWithProvider('google'); };
  window.signOut = signOut;

  // Expose the Supabase client (and a Promise that resolves to it after init)
  // so peer scripts on the same page — e.g. pricing.js — can reuse this
  // singleton instead of calling supabase.createClient again. Sharing avoids
  // the "Multiple GoTrueClient instances detected" warning, halves the
  // auth/config fetches on page load, and ensures both modules see the same
  // session refreshes.
  var _readyResolve;
  window.ZKAuth = window.ZKAuth || {};
  window.ZKAuth.ready = new Promise(function (resolve) { _readyResolve = resolve; });
  window.ZKAuth.getClient = function () { return _supabaseClient; };
  window.ZKAuth.__signalReady = function () {
    if (_readyResolve) { _readyResolve(_supabaseClient); _readyResolve = null; }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      resolveDOM();
      init();
    });
  } else {
    resolveDOM();
    init();
  }
})();
