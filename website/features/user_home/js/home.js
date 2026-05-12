/**
 * Home Page — Post-Login Dashboard
 *
 * Loads user profile, displays zettel vault, handles avatar menu,
 * and provides "Add Zettel" functionality.
 */

(function () {
  'use strict';

  var AVATAR_COUNT = 60;
  var _supabaseClient = null;
  var _currentSession = null;
  var _currentAvatarId = null;
  var _bodyLockCount = 0;

  // ── DOM refs ──────────────────────────────────────────────────────

  var avatarBtn, avatarImg, avatarFallback, avatarDropdown, avatarWrap;
  var cardGrid, emptyState, zettelCount, userDisplayName;
  var addZettelDropdown, addZettelForm, addUrlInput;
  var addSubmitBtn, addError, addLoading;
  var avatarModal, avatarModalOverlay, avatarModalClose, avatarGrid;
  var menuProfile, menuNexus, menuSignout;

  function resolveDOM() {
    // D-2 namespace: home owns its own duplicate avatar markup; header.html
    // ships #avatar-btn/#avatar-dropdown/#menu-signout — renamed here so a
    // future shell-injection of header on /home cannot silent-collide.
    avatarBtn = document.getElementById('home-avatar-btn');
    avatarImg = document.getElementById('home-avatar-img');
    avatarFallback = document.getElementById('home-avatar-fallback');
    avatarDropdown = document.getElementById('home-avatar-dropdown');
    avatarWrap = document.getElementById('home-avatar-wrap');
    cardGrid = document.getElementById('card-grid');
    emptyState = document.getElementById('empty-state');
    zettelCount = document.getElementById('zettel-count');
    userDisplayName = document.getElementById('user-display-name');
    addZettelDropdown = document.getElementById('add-zettel-dropdown');
    addZettelForm = document.getElementById('add-zettel-form');
    addUrlInput = document.getElementById('add-url-input');
    addSubmitBtn = document.getElementById('add-submit-btn');
    addError = document.getElementById('add-error');
    addLoading = document.getElementById('add-loading');
    avatarModal = document.getElementById('avatar-modal');
    avatarModalOverlay = document.getElementById('avatar-modal-overlay');
    avatarModalClose = document.getElementById('avatar-modal-close');
    avatarGrid = document.getElementById('avatar-grid');
    menuProfile = document.getElementById('menu-profile');
    menuNexus = document.getElementById('menu-nexus');
    menuSignout = document.getElementById('home-menu-signout');
  }

  function setBodyScrollLocked(locked) {
    if (locked) {
      _bodyLockCount += 1;
    } else {
      _bodyLockCount = Math.max(0, _bodyLockCount - 1);
    }
    document.body.style.overflow = _bodyLockCount > 0 ? 'hidden' : '';
  }

  function toSafeHttpUrl(rawUrl) {
    var value = String(rawUrl || '').trim();
    if (!value) return '';
    try {
      var parsed = new URL(value, window.location.origin);
      var protocol = parsed.protocol.toLowerCase();
      if (protocol !== 'http:' && protocol !== 'https:') return '';
      return parsed.href;
    } catch (err) {
      void err;
      return '';
    }
  }

  // ── Init ──────────────────────────────────────────────────────────

  async function init() {
    resolveDOM();

    try {
      // Init Supabase client
      var resp = await fetch('/api/auth/config');
      var config = await resp.json();
      if (config.supabase_url && config.supabase_anon_key) {
        _supabaseClient = supabase.createClient(config.supabase_url, config.supabase_anon_key, {
          auth: {
            persistSession: true,
            autoRefreshToken: true,
            storage: window.localStorage,
            storageKey: 'zk-auth-token',
          },
        });
        var sessionResult = await _supabaseClient.auth.getSession();
        _currentSession = sessionResult.data.session;
      }
    } catch (e) {
      console.error('[home] Supabase init failed:', e);
    }

    // Auth guard — redirect if not logged in (with loop protection)
    var token = _currentSession ? _currentSession.access_token : null;
    if (!token) {
      var lastRedirect = parseInt(sessionStorage.getItem('zk-home-redirect') || '0', 10);
      if (Date.now() - lastRedirect < 5000) {
        console.warn('[home] Redirect loop detected, staying on page');
        return;
      }
      sessionStorage.setItem('zk-home-redirect', String(Date.now()));
      window.location.href = '/';
      return;
    }
    sessionStorage.removeItem('zk-home-redirect');

    // Load user profile — stay on page even if fetch fails
    var profile = await fetchProfile(token);
    if (!profile) {
      var user = _currentSession.user || {};
      var meta = user.user_metadata || {};
      profile = {
        name: meta.full_name || user.email || 'User',
        email: user.email || '',
        avatar_url: meta.avatar_url || meta.picture || ''
      };
      console.warn('[home] Profile fetch failed, using session data');
    }

    // Set display name
    var displayName = profile.name || profile.email || 'User';
    if (userDisplayName) {
      userDisplayName.textContent = displayName.split(' ')[0];
    }

    // Bind events FIRST so UI stays interactive even if downstream calls fail
    bindEvents(token);

    // Set avatar — delegates to shared ZKHeader (non-fatal if it throws)
    try {
      if (window.ZKHeader && typeof window.ZKHeader.boot === 'function') {
        await window.ZKHeader.boot(token, { profile: profile });
        var _idMatch = profile && profile.avatar_url && profile.avatar_url.match(/avatar_(\d+)\.svg/);
        if (_idMatch) _currentAvatarId = parseInt(_idMatch[1], 10);
      } else {
        console.error('[home] ZKHeader missing — avatar will use CSS fallback only');
      }
    } catch (e) {
      console.error('[home] ZKHeader.boot failed:', e);
    }

    // Load zettels + kastens (non-fatal if they error)
    try {
      await loadZettels(token);
    } catch (e) {
      console.error('[home] loadZettels failed:', e);
    }
    try {
      loadKastens(token);
    } catch (e) {
      console.error('[home] loadKastens failed:', e);
    }
  }

  // ── Profile ───────────────────────────────────────────────────────

  async function fetchProfile(token) {
    try {
      var resp = await fetch('/api/me', {
        headers: { 'Authorization': 'Bearer ' + token }
      });
      if (resp.status === 401) return null;
      return await resp.json();
    } catch (e) {
      console.error('[home] Profile fetch failed:', e);
      return null;
    }
  }

  // ── Avatar ────────────────────────────────────────────────────────
  // All avatar load/fallback/preload logic now lives in the shared ZKHeader module
  // (website/features/header/js/header.js). These wrappers keep the local picker-grid
  // callsite working without duplicating lifecycle code.

  async function updateAvatar(avatarId, token) {
    _currentAvatarId = avatarId;
    if (window.ZKHeader && typeof window.ZKHeader.setAvatarById === 'function') {
      await window.ZKHeader.setAvatarById(avatarId, token, null);
    }
  }

  // ── Zettels ───────────────────────────────────────────────────────

  async function loadKastens(token) {
    try {
      var resp = await fetch('/api/rag/sandboxes', {
        headers: { 'Authorization': 'Bearer ' + token }
      });
      if (!resp.ok) return;
      var data = await resp.json();
      var sandboxes = data.sandboxes || [];
      var totalMembers = sandboxes.reduce(function (acc, s) {
        return acc + (s.member_count || 0);
      }, 0);
      var elCount = document.getElementById('kastens-count');
      var elTotal = document.getElementById('kastens-total');
      var elMembers = document.getElementById('kastens-members');
      if (elCount) elCount.textContent = sandboxes.length;
      if (elTotal) elTotal.textContent = sandboxes.length;
      if (elMembers) elMembers.textContent = totalMembers;

      // Sort by last_used_at desc (fallback updated_at, then created_at)
      sandboxes.sort(function (a, b) {
        var ak = a.last_used_at || a.updated_at || a.created_at || '';
        var bk = b.last_used_at || b.updated_at || b.created_at || '';
        return bk.localeCompare(ak);
      });
      renderKastenCards(sandboxes.slice(0, 3), sandboxes.length);
    } catch (e) {
      console.warn('[home] Kastens load failed:', e);
      renderKastenCards([], 0);
    }
  }

  function renderKastenCards(previewKastens, totalCount) {
    var grid = document.getElementById('kasten-grid');
    var emptyEl = document.getElementById('kasten-empty-state');
    var preview = document.getElementById('kasten-preview');
    if (!grid || !emptyEl || !preview) return;

    var fade = preview.querySelector('.home-card-fade');

    if (totalCount === 0) {
      grid.innerHTML = '';
      emptyEl.classList.remove('hidden');
      if (fade) fade.style.display = 'none';
      return;
    }

    emptyEl.classList.add('hidden');
    grid.innerHTML = '';
    if (fade) fade.style.display = previewKastens.length > 0 ? '' : 'none';

    previewKastens.forEach(function (k, i) {
      var card = document.createElement('a');
      card.className = 'home-card home-kasten-card';
      card.href = '/home/rag?sandbox=' + encodeURIComponent(k.id);
      card.style.animationDelay = (i * 0.08) + 's';

      var members = k.member_count || 0;
      var quality = (k.default_quality || 'fast').toLowerCase();
      var qualityLabel = quality === 'high' ? 'Strong' : 'Fast';
      var desc = (k.description || '').trim();

      card.innerHTML =
        '<h3 class="home-card-title">' + escapeHtml(k.name || 'Untitled') + '</h3>' +
        (desc
          ? '<p class="home-kasten-desc">' + escapeHtml(desc) + '</p>'
          : '') +
        '<div class="home-card-meta">' +
          '<span class="home-card-date">' + members + ' zettel' + (members === 1 ? '' : 's') + '</span>' +
          '<span class="home-card-source">' + escapeHtml(qualityLabel) + '</span>' +
        '</div>';

      grid.appendChild(card);
    });
  }

  async function loadZettels(token) {
    try {
      var resp = await fetch('/api/graph?view=my', {
        headers: { 'Authorization': 'Bearer ' + token }
      });
      var data = await resp.json();
      var nodes = data.nodes || [];
      var links = data.links || [];

      // Sort by date descending
      nodes.sort(function (a, b) {
        return (b.date || '').localeCompare(a.date || '');
      });

      // Update KG stats panel
      var kgNodeCount = document.getElementById('kg-node-count');
      var kgLinkCount = document.getElementById('kg-link-count');
      if (kgNodeCount) kgNodeCount.textContent = nodes.length;
      if (kgLinkCount) kgLinkCount.textContent = links.length;

      // Show only latest 3 zettels in the preview
      renderCards(nodes.slice(0, 3), nodes.length);
    } catch (e) {
      console.error('[home] Zettels load failed:', e);
      renderCards([], 0);
    }
  }

  function renderCards(previewNodes, totalCount) {
    if (!cardGrid || !emptyState || !zettelCount) return;

    zettelCount.textContent = totalCount;

    if (totalCount === 0) {
      cardGrid.innerHTML = '';
      emptyState.classList.remove('hidden');
      // Hide fade when empty
      var fade = document.querySelector('.home-card-fade');
      if (fade) fade.style.display = 'none';
      return;
    }

    emptyState.classList.add('hidden');
    cardGrid.innerHTML = '';

    // Show fade only when there are cards
    var fade = document.querySelector('.home-card-fade');
    if (fade) fade.style.display = previewNodes.length > 0 ? '' : 'none';

    previewNodes.forEach(function (node, i) {
      var card = document.createElement('a');
      card.className = 'home-card';
      var safeUrl = toSafeHttpUrl(node.url);
      card.href = safeUrl || '#';
      card.target = safeUrl ? '_blank' : '';
      card.rel = safeUrl ? 'noopener noreferrer' : '';
      card.style.animationDelay = (i * 0.08) + 's';

      var sourceClass = (node.group || 'web').toLowerCase();

      card.innerHTML =
        '<h3 class="home-card-title">' + escapeHtml(node.name || 'Untitled') + '</h3>' +
        '<div class="home-card-meta">' +
          (node.date ? '<span class="home-card-date">' + escapeHtml(node.date) + '</span>' : '') +
          '<span class="home-card-source ' + sourceClass + '">' + escapeHtml(node.group || 'web') + '</span>' +
          '<button class="home-card-summary-btn" data-node-idx="' + i + '" type="button" title="Summary" aria-label="View summary">' +
            '<img src="/artifacts/icon-summary.svg" alt="Summary" />' +
            '<span class="tooltip">Summary</span>' +
          '</button>' +
        '</div>';

      // Intercept card clicks — if summary button was clicked, show popup instead of navigating
      card.addEventListener('click', function (e) {
        var btn = e.target.closest('.home-card-summary-btn');
        if (btn) {
          e.preventDefault();
          e.stopPropagation();
          openSummaryPopup(node);
        }
      });

      cardGrid.appendChild(card);
    });
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function extractSummaryParts(rawSummary) {
    var rawText = normalizeSummaryText(rawSummary || '');
    var parsed = tryParseSummaryObject(rawText);

    if (parsed) {
      var briefFromParsed = normalizeSummaryText(
        parsed.brief_summary || parsed.briefSummary || parsed.one_line_summary || parsed.summary || ''
      );
      var detailedFromParsed = normalizeSummaryText(
        parsed.detailed_summary || parsed.detailedSummary || parsed.summary || ''
      );

      var resolvedBrief = briefFromParsed || detailedFromParsed;
      var resolvedDetailed = detailedFromParsed || briefFromParsed;
      if (resolvedBrief || resolvedDetailed) {
        return {
          brief: resolvedBrief || 'No summary available for this zettel.',
          detailed: resolvedDetailed || resolvedBrief || 'No summary available for this zettel.'
        };
      }
    }

    var fallback = rawText || 'No summary available for this zettel.';
    return { brief: fallback, detailed: fallback };
  }

  function renderDualSummary(container, parts) {
    container.innerHTML = '';
    var brief = (parts && parts.brief) ? String(parts.brief).trim() : '';
    var detailed = (parts && parts.detailed) ? String(parts.detailed).trim() : '';
    var hasBrief = brief && brief !== 'No summary available for this zettel.';
    var hasDetailed = detailed && detailed !== brief && detailed !== 'No summary available for this zettel.';

    if (!hasBrief && !hasDetailed) {
      container.textContent = 'No summary available for this zettel.';
      return;
    }

    if (hasBrief) {
      var briefWrap = document.createElement('div');
      briefWrap.className = 'home-summary-section home-summary-brief';
      var briefHeading = document.createElement('h3');
      briefHeading.className = 'home-summary-section-heading';
      briefHeading.textContent = 'Brief';
      var briefBody = document.createElement('p');
      briefBody.className = 'home-summary-section-body';
      briefBody.textContent = brief;
      briefWrap.appendChild(briefHeading);
      briefWrap.appendChild(briefBody);
      container.appendChild(briefWrap);
    }

    if (hasDetailed) {
      if (hasBrief) {
        var divider = document.createElement('hr');
        divider.className = 'home-summary-divider';
        container.appendChild(divider);
      }
      var detailedWrap = document.createElement('div');
      detailedWrap.className = 'home-summary-section home-summary-detailed';
      var detailedHeading = document.createElement('h3');
      detailedHeading.className = 'home-summary-section-heading';
      detailedHeading.textContent = 'Detailed';
      detailedWrap.appendChild(detailedHeading);
      renderMarkdownLite(detailedWrap, detailed);
      container.appendChild(detailedWrap);
    }
  }

  function renderMarkdownLite(container, markdown) {
    var lines = String(markdown || '').split(/\r?\n/);
    var paraBuf = [];
    var listStack = null; // { el: <ul>, level: number }

    function flushPara() {
      if (!paraBuf.length) return;
      var joined = paraBuf.join(' ').trim();
      if (joined) {
        var p = document.createElement('p');
        p.className = 'home-summary-para';
        p.textContent = joined;
        container.appendChild(p);
      }
      paraBuf = [];
    }
    function closeList() {
      listStack = null;
    }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var trimmed = line.replace(/\s+$/, '');
      if (!trimmed.trim()) {
        flushPara();
        closeList();
        continue;
      }
      var h3 = trimmed.match(/^###\s+(.*)$/);
      var h2 = trimmed.match(/^##\s+(.*)$/);
      var bullet = trimmed.match(/^\s*[-*]\s+(.*)$/);
      if (h2 || h3) {
        flushPara();
        closeList();
        var heading = document.createElement(h2 ? 'h4' : 'h5');
        heading.className = h2 ? 'home-summary-h2' : 'home-summary-h3';
        heading.textContent = (h2 ? h2[1] : h3[1]).trim();
        container.appendChild(heading);
        continue;
      }
      if (bullet) {
        flushPara();
        if (!listStack) {
          listStack = { el: document.createElement('ul') };
          listStack.el.className = 'home-summary-list';
          container.appendChild(listStack.el);
        }
        var li = document.createElement('li');
        li.className = 'home-summary-list-item';
        li.textContent = bullet[1].trim();
        listStack.el.appendChild(li);
        continue;
      }
      closeList();
      paraBuf.push(trimmed.trim());
    }
    flushPara();
    closeList();
  }

  function tryParseSummaryObject(rawText) {
    var cleaned = normalizeSummaryText(rawText || '');
    if (!cleaned) return null;

    cleaned = cleaned
      .replace(/^```(?:json)?/i, '')
      .replace(/```$/i, '')
      .replace(/^json\s*/i, '')
      .trim();

    var candidates = [cleaned];
    var start = cleaned.indexOf('{');
    var end = cleaned.lastIndexOf('}');
    if (start !== -1 && end > start) {
      candidates.push(cleaned.slice(start, end + 1));
    }

    for (var i = 0; i < candidates.length; i++) {
      var candidate = candidates[i].trim();
      if (!candidate) continue;
      try {
        var parsed = JSON.parse(candidate);
        if (parsed && typeof parsed === 'object') return parsed;
        if (typeof parsed === 'string') {
          var nested = JSON.parse(parsed);
          if (nested && typeof nested === 'object') return nested;
        }
      } catch (err) {
        void err;
      }
    }

    var regexBrief = extractSummaryFieldByRegex(cleaned, 'brief_summary');
    var regexDetailed = extractSummaryFieldByRegex(cleaned, 'detailed_summary');
    if (regexBrief || regexDetailed) {
      return {
        brief_summary: regexBrief,
        detailed_summary: regexDetailed
      };
    }

    return null;
  }

  function extractSummaryFieldByRegex(text, fieldName) {
    var pattern = new RegExp('"' + fieldName + '"\\s*:\\s*"([\\s\\S]*?)"\\s*(?:,|})', 'i');
    var match = text.match(pattern);
    if (!match || !match[1]) return '';
    return normalizeSummaryText(match[1]);
  }

  function normalizeSummaryText(value) {
    return String(value || '')
      .replace(/\r\n/g, '\n')
      .replace(/\\n/g, '\n')
      .replace(/\\r/g, '\r')
      .replace(/\\t/g, '\t')
      .replace(/\\"/g, '"')
      .trim();
  }

  // ── My Zettels badge (UX-8) ──────────────────────────────────────
  // The header badge was set once at render time and drifted from the
  // authoritative count returned by /api/graph?view=my as the user added
  // zettels. We now refetch on add success and on stale interaction.

  var _badgeUpdatedAt = 0;
  var BADGE_TTL_MS = 60 * 1000;
  var _badgeRefreshing = false;
  var _badgeListenersBound = false;

  async function refreshMyZettelsBadge(token) {
    if (_badgeRefreshing) return;
    _badgeRefreshing = true;
    try {
      var resp = await fetch('/api/graph?view=my', {
        credentials: 'include',
        headers: { 'Authorization': 'Bearer ' + token }
      });
      if (!resp.ok) return;
      var data = await resp.json();
      var nodes = data.nodes || [];
      var links = data.links || [];
      var badge = document.getElementById('zettel-count');
      if (badge) badge.textContent = nodes.length;
      var kgN = document.getElementById('kg-node-count');
      var kgL = document.getElementById('kg-link-count');
      if (kgN) kgN.textContent = nodes.length;
      if (kgL) kgL.textContent = links.length;
      _badgeUpdatedAt = Date.now();
    } catch (e) {
      console.warn('[home] badge refresh failed', e);
    } finally {
      _badgeRefreshing = false;
    }
  }

  function bindBadgeFreshness(token) {
    if (_badgeListenersBound) return;
    _badgeListenersBound = true;
    _badgeUpdatedAt = Date.now();
    function maybeRefresh() {
      if (Date.now() - _badgeUpdatedAt > BADGE_TTL_MS) {
        refreshMyZettelsBadge(token);
      }
    }
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') maybeRefresh();
    });
    var vault = document.getElementById('home-vault');
    if (vault) vault.addEventListener('click', maybeRefresh);
  }

  // ── Add Zettel ────────────────────────────────────────────────────

  // ── Glass Shatter Animation ─────────────────────────────────────

  function shatterElement(sourceEl, targetRect, revealEl) {
    return new Promise(function (resolve) {
      var rect = sourceEl.getBoundingClientRect();
      var container = document.createElement('div');
      container.style.cssText = 'position:fixed;inset:0;z-index:600;pointer-events:none;overflow:hidden;';
      document.body.appendChild(container);

      var cols = 10, rows = 4;
      var shardW = rect.width / cols;
      var shardH = rect.height / rows;
      var shards = [];
      var colors = [
        'hsla(172, 66%, 50%, 0.7)',
        'hsla(172, 50%, 40%, 0.6)',
        'hsla(172, 40%, 35%, 0.5)',
        'hsla(190, 50%, 30%, 0.5)',
        'hsla(210, 30%, 25%, 0.5)',
        'hsla(172, 66%, 50%, 0.4)',
      ];

      // Target card grid dimensions
      var tCols = 10, tRows = 4;
      var tShardW = targetRect.width / tCols;
      var tShardH = targetRect.height / tRows;

      for (var r = 0; r < rows; r++) {
        for (var c = 0; c < cols; c++) {
          var shard = document.createElement('div');
          var startX = rect.left + c * shardW;
          var startY = rect.top + r * shardH;

          shard.style.cssText =
            'position:fixed;' +
            'left:' + startX + 'px;top:' + startY + 'px;' +
            'width:' + shardW + 'px;height:' + shardH + 'px;' +
            'background:' + colors[Math.floor(Math.random() * colors.length)] + ';' +
            'border:0.5px solid hsla(172, 66%, 50%, 0.12);' +
            'border-radius:' + (Math.random() * 2) + 'px;' +
            'transition:all 0.55s cubic-bezier(0.25, 0.46, 0.45, 0.94);' +
            'box-shadow:0 0 3px hsla(172, 66%, 50%, 0.15);' +
            'opacity:1;';

          container.appendChild(shard);

          // Calculate where this shard should land in the target card grid
          var idx = r * cols + c;
          var tR = Math.floor(idx / tCols) % tRows;
          var tC = idx % tCols;

          shards.push({
            el: shard,
            startX: startX,
            startY: startY,
            explodeX: startX + (Math.random() - 0.5) * 280,
            explodeY: startY + (Math.random() - 0.5) * 180 - 60,
            explodeRot: (Math.random() - 0.5) * 300,
            // Exact grid position on the target card
            targetX: targetRect.left + tC * tShardW,
            targetY: targetRect.top + tR * tShardH,
            targetW: tShardW,
            targetH: tShardH,
          });
        }
      }

      // Hide dropdown
      sourceEl.classList.remove('open');

      // Phase 1: Explode outward
      requestAnimationFrame(function () {
        shards.forEach(function (s) {
          s.el.style.left = s.explodeX + 'px';
          s.el.style.top = s.explodeY + 'px';
          s.el.style.transform = 'rotate(' + s.explodeRot + 'deg) scale(' + (0.6 + Math.random() * 0.6) + ')';
          s.el.style.opacity = '0.85';
        });
      });

      // Phase 2: Assemble into card shape (600ms) — shards snap into a tight grid
      setTimeout(function () {
        shards.forEach(function (s, i) {
          var delay = (i % 5) * 0.02;
          s.el.style.transition = 'all 0.55s cubic-bezier(0.34, 1.56, 0.64, 1) ' + delay + 's';
          s.el.style.left = s.targetX + 'px';
          s.el.style.top = s.targetY + 'px';
          s.el.style.width = s.targetW + 'px';
          s.el.style.height = s.targetH + 'px';
          s.el.style.transform = 'rotate(0deg) scale(1)';
          s.el.style.opacity = '0.7';
          s.el.style.borderRadius = '1px';
        });
        // Start revealing skeleton underneath as shards settle
        if (revealEl) {
          setTimeout(function () {
            revealEl.style.opacity = '1';
          }, 300);
        }
      }, 600);

      // Phase 3: Dissolve shards — skeleton is already visible beneath (1150ms)
      setTimeout(function () {
        shards.forEach(function (s) {
          s.el.style.transition = 'all 0.35s ease-out';
          s.el.style.opacity = '0';
          s.el.style.transform = 'scale(0.95)';
        });
      }, 1200);

      // Phase 4: Cleanup (1600ms total)
      setTimeout(function () {
        container.remove();
        resolve();
      }, 1550);
    });
  }

  async function addZettel(url, token, existingPricingActionId) {
    var pricingActionId = existingPricingActionId || ('zettel:' + Date.now() + ':' + Math.random().toString(36).slice(2));
    if (addError) addError.textContent = '';
    // UX-2: immediate progress feedback — disable + spinner label + busy attr.
    var addWrapEl = document.getElementById('add-zettel-wrap');
    var _origSubmitLabel = null;
    if (addSubmitBtn) {
      addSubmitBtn.disabled = true;
      _origSubmitLabel = addSubmitBtn.textContent;
      addSubmitBtn.innerHTML = '<span class="btn-inline-spinner" aria-hidden="true"></span>Summarizing…';
      addSubmitBtn.setAttribute('aria-busy', 'true');
    }
    if (addWrapEl) addWrapEl.setAttribute('data-busy', 'true');
    // UX-2 bonus: clarifying message after 30s if still in flight.
    var _slowMsgTimer = setTimeout(function () {
      if (addError && addWrapEl && addWrapEl.getAttribute('data-busy') === 'true') {
        addError.textContent = 'Still working… large pages can take up to 60 s.';
      }
    }, 30000);
    function _restoreAddButton() {
      clearTimeout(_slowMsgTimer);
      if (addSubmitBtn) {
        addSubmitBtn.disabled = false;
        addSubmitBtn.textContent = _origSubmitLabel || 'Add';
        addSubmitBtn.removeAttribute('aria-busy');
      }
      if (addWrapEl) addWrapEl.removeAttribute('data-busy');
    }

    // Capture dropdown rect before hiding
    var dropdownRect = addZettelDropdown ? addZettelDropdown.getBoundingClientRect() : null;

    // Step 0: Insert an invisible spacer at top and animate cards sliding down
    if (emptyState) emptyState.classList.add('hidden');
    var fade = document.querySelector('.home-card-fade');
    if (fade) fade.style.display = '';

    // Measure exact skeleton height by briefly inserting a hidden one
    var measureSkeleton = document.createElement('div');
    measureSkeleton.className = 'home-card home-card-skeleton';
    measureSkeleton.style.cssText = 'visibility:hidden;position:absolute;';
    measureSkeleton.innerHTML =
      '<div class="skeleton-line skeleton-title"></div>' +
      '<div class="home-card-meta"><div class="skeleton-line skeleton-date"></div><div class="skeleton-line skeleton-source"></div></div>';
    if (cardGrid) {
      cardGrid.appendChild(measureSkeleton);
      var cardH = measureSkeleton.offsetHeight;
      cardGrid.removeChild(measureSkeleton);
    } else {
      var cardH = 72;
    }

    var spacer = document.createElement('div');
    spacer.className = 'home-card-spacer';
    spacer.style.cssText = 'height:0;overflow:hidden;transition:height 0.65s cubic-bezier(0.22, 0.61, 0.36, 1);border:none;background:none;padding:0;margin:0;';

    if (cardGrid) {
      cardGrid.insertBefore(spacer, cardGrid.firstChild);
      while (cardGrid.children.length > 4) {
        cardGrid.removeChild(cardGrid.lastChild);
      }
      // Expand to exact skeleton card height
      requestAnimationFrame(function () {
        spacer.style.height = Math.round(cardH * 0.85) + 'px';
      });
    }

    // Start shatter after cards begin sliding down
    await new Promise(function (r) { setTimeout(r, 300); });

    var targetRect = spacer.getBoundingClientRect();

    // Start API call immediately (runs in parallel with animation)
    var apiPromise = fetch('/api/summarize', {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + token,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ url: url, client_action_id: pricingActionId })
    });

    // Create skeleton now — it'll be revealed seamlessly during shatter
    var skeleton = document.createElement('div');
    skeleton.className = 'home-card home-card-skeleton';
    skeleton.style.opacity = '0';
    skeleton.style.transition = 'opacity 0.4s ease';
    skeleton.innerHTML =
      '<div class="skeleton-line skeleton-title"></div>' +
      '<div class="home-card-meta">' +
        '<div class="skeleton-line skeleton-date"></div>' +
        '<div class="skeleton-line skeleton-source"></div>' +
      '</div>';

    // Replace spacer with hidden skeleton before shatter starts
    if (cardGrid && spacer.parentNode) {
      cardGrid.replaceChild(skeleton, spacer);
      while (cardGrid.children.length > 3) {
        cardGrid.removeChild(cardGrid.lastChild);
      }
    }

    // Run shatter — shards assemble on top of the skeleton, then skeleton fades in
    if (dropdownRect && addZettelDropdown) {
      await shatterElement(addZettelDropdown, skeleton.getBoundingClientRect(), skeleton);
    } else {
      if (addZettelDropdown) addZettelDropdown.classList.remove('open');
      skeleton.style.opacity = '1';
    }

    // Clear form
    if (addUrlInput) addUrlInput.value = '';

    try {
      var resp = await apiPromise;

      if (!resp.ok) {
        var errMsg = 'Failed to process URL (HTTP ' + resp.status + ')';
        var quotaDetail = null;
        try {
          var err = await resp.json();
          quotaDetail = err.detail && err.detail.code === 'quota_exhausted' ? err.detail : null;
          errMsg = (err.detail && err.detail.message) || err.detail || errMsg;
        } catch (_parseErr) {
          var rawText = '';
          try { rawText = await resp.text(); } catch (_) {}
          if (rawText) errMsg = rawText.slice(0, 200);
        }
        if (quotaDetail && window.ZKPricing) {
          await window.ZKPricing.openPurchase({
            detail: quotaDetail,
            source: 'home:add-zettel',
            resumeAction: { type: 'add_zettel', url: url, clientActionId: pricingActionId },
            onResume: function () { return addZettel(url, token, pricingActionId); }
          });
          return;
        }
        throw new Error(errMsg);
      }

      var result = await resp.json();

      // Morph skeleton into real card
      var today = new Date().toISOString().slice(0, 10);
      var sourceType = (result.source_type || 'web').toLowerCase();
      var newNode = {
        name: result.title || 'Untitled',
        date: today,
        group: sourceType,
        url: result.source_url || url,
        summary: JSON.stringify({
          brief_summary: result.brief_summary || '',
          detailed_summary: result.summary || result.brief_summary || ''
        }),
        tags: result.tags || []
      };

      var realCard = document.createElement('a');
      realCard.className = 'home-card home-card-new';
      var safeNewUrl = toSafeHttpUrl(newNode.url);
      realCard.href = safeNewUrl || '#';
      realCard.target = safeNewUrl ? '_blank' : '';
      realCard.rel = safeNewUrl ? 'noopener noreferrer' : '';

      realCard.innerHTML =
        '<h3 class="home-card-title">' + escapeHtml(newNode.name) + '</h3>' +
        '<div class="home-card-meta">' +
          '<span class="home-card-date">' + escapeHtml(newNode.date) + '</span>' +
          '<span class="home-card-source ' + sourceType + '">' + escapeHtml(newNode.group) + '</span>' +
          '<button class="home-card-summary-btn" type="button" title="Summary" aria-label="View summary">' +
            '<img src="/artifacts/icon-summary.svg" alt="Summary" />' +
            '<span class="tooltip">Summary</span>' +
          '</button>' +
        '</div>';

      realCard.addEventListener('click', function (e) {
        var btn = e.target.closest('.home-card-summary-btn');
        if (btn) {
          e.preventDefault();
          e.stopPropagation();
          openSummaryPopup(newNode);
        }
      });

      if (cardGrid && skeleton.parentNode) {
        cardGrid.replaceChild(realCard, skeleton);
      }

      var count = parseInt(zettelCount.textContent || '0', 10) + 1;
      zettelCount.textContent = count;
      // UX-2: clear any slow-message that may have appeared in-flight.
      if (addError) addError.textContent = '';
      // UX-8: refresh the My Zettels badge authoritatively from /api/graph.
      refreshMyZettelsBadge(token);
    } catch (e) {
      if (skeleton.parentNode) skeleton.parentNode.removeChild(skeleton);
      if (addError) addError.textContent = e.message;
      if (addZettelDropdown) addZettelDropdown.classList.add('open');
    } finally {
      _restoreAddButton();
    }
  }

  // ── Summary Popup ────────────────────────────────────────────────

  function openSummaryPopup(node) {
    var loader = document.getElementById('summary-loader');
    var overlay = document.getElementById('summary-overlay');
    var title = document.getElementById('summary-title');
    var meta = document.getElementById('summary-meta');
    var text = document.getElementById('summary-text');
    var tags = document.getElementById('summary-tags');
    if (!overlay) return;

    // Prepare popup content while loader plays
    title.textContent = node.name || 'Untitled';

    var sourceClass = (node.group || 'web').toLowerCase();
    // Card-parity layout: date pill (mono) THEN source pill.
    meta.innerHTML =
      (node.date ? '<span class="home-card-date">' + escapeHtml(node.date) + '</span>' : '') +
      '<span class="home-card-source ' + sourceClass + '">' + escapeHtml(node.group || 'web') + '</span>';

    var summaryParts = extractSummaryParts(node.summary || node.description || '');
    renderDualSummary(text, summaryParts);

    tags.innerHTML = '';
    var nodeTags = node.tags || [];
    nodeTags.forEach(function (tag) {
      var el = document.createElement('span');
      el.className = 'home-summary-tag';
      el.textContent = '#' + tag;
      tags.appendChild(el);
    });

    // Show loader animation first
    setBodyScrollLocked(true);
    if (loader) {
      loader.classList.add('active');
      setTimeout(function () {
        loader.classList.remove('active');
        overlay.classList.add('open');
      }, 1500);
    } else {
      overlay.classList.add('open');
    }
  }

  function closeSummaryPopup() {
    var overlay = document.getElementById('summary-overlay');
    if (overlay) overlay.classList.remove('open');
    setBodyScrollLocked(false);
  }

  // ── Avatar Picker Modal ──────────────────────────────────────────

  function openAvatarPicker(token) {
    if (!avatarModal || !avatarGrid) return;

    // Populate grid
    avatarGrid.innerHTML = '';
    for (var i = 0; i < AVATAR_COUNT; i++) {
      var btn = document.createElement('button');
      btn.className = 'home-avatar-option' + (i === _currentAvatarId ? ' selected' : '');
      btn.innerHTML = '<img src="/artifacts/avatars/avatar_' + String(i).padStart(2, '0') + '.svg" alt="Avatar ' + i + '" />';
      btn.setAttribute('data-avatar-id', i);

      btn.addEventListener('click', (function (id) {
        return function () {
          updateAvatar(id, token);
          // Update selection
          var all = avatarGrid.querySelectorAll('.home-avatar-option');
          all.forEach(function (el) { el.classList.remove('selected'); });
          this.classList.add('selected');
          // Close modal after short delay
          setTimeout(function () { closeAvatarPicker(); }, 300);
        };
      })(i));

      avatarGrid.appendChild(btn);
    }

    avatarModal.classList.add('open');
    setBodyScrollLocked(true);
  }

  function closeAvatarPicker() {
    if (!avatarModal) return;
    avatarModal.classList.remove('open');
    setBodyScrollLocked(false);
  }

  // ── Events ────────────────────────────────────────────────────────

  function bindEvents(token) {
    window.__homeBindEventsCount = (window.__homeBindEventsCount || 0) + 1;
    console.log('[home] bindEvents start, count=' + window.__homeBindEventsCount, { hasBtn: !!avatarBtn, hasDrop: !!avatarDropdown, hasWrap: !!avatarWrap });
    window.__homeBindEventsRan = true;
    // Avatar dropdown toggle — IDs are home-namespaced (D-2) so header.js binds
    // only header's #avatar-btn; the dataset.zkBound guard remains for re-init safety.
    if (avatarBtn && !avatarBtn.dataset.zkBound) {
      avatarBtn.dataset.zkBound = '1';
      avatarBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        avatarDropdown.classList.toggle('open');
        avatarBtn.setAttribute('aria-expanded', avatarDropdown.classList.contains('open') ? 'true' : 'false');
      });
    }

    // Close dropdown on outside click
    document.addEventListener('click', function (e) {
      if (avatarDropdown && avatarWrap && !avatarWrap.contains(e.target)) {
        avatarDropdown.classList.remove('open');
        if (avatarBtn) avatarBtn.setAttribute('aria-expanded', 'false');
      }
      var addWrap = document.getElementById('add-zettel-wrap');
      if (addZettelDropdown && addWrap && !addWrap.contains(e.target)) {
        addZettelDropdown.classList.remove('open');
      }
    });

    // Profile menu item → open avatar picker
    if (menuProfile) {
      menuProfile.addEventListener('click', function (e) {
        e.preventDefault();
        avatarDropdown.classList.remove('open');
        openAvatarPicker(token);
      });
    }

    if (menuNexus) {
      menuNexus.addEventListener('click', function () {
        if (avatarDropdown) avatarDropdown.classList.remove('open');
      });
    }

    // Sign out
    if (menuSignout) {
      menuSignout.addEventListener('click', async function () {
        if (_supabaseClient) {
          await _supabaseClient.auth.signOut();
        }
        window.location.href = '/';
      });
    }

    // Add Zettel toggle — bound via event delegation on document.body so the
    // handler survives async re-renders (e.g. avatar/auth state landing after
    // initial paint replaces the toolbar DOM and would orphan a direct
    // getElementById listener). Filter by data-action so the listener only
    // ever fires on the real Add button.
    document.body.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-action="add-zettel"]');
      if (!btn) return;
      e.stopPropagation();
      var dropdown = document.getElementById('add-zettel-dropdown');
      if (!dropdown) return;
      dropdown.classList.toggle('open');
      var urlInput = document.getElementById('add-url-input');
      if (dropdown.classList.contains('open') && urlInput) {
        urlInput.focus();
      }
    });

    // Add Zettel form submit
    if (addZettelForm) {
      addZettelForm.addEventListener('submit', function (e) {
        e.preventDefault();
        var url = addUrlInput ? addUrlInput.value.trim() : '';
        if (url) addZettel(url, token);
      });
    }

    // Summary popup close
    var summaryClose = document.getElementById('summary-close');
    var summaryBackdrop = document.getElementById('summary-backdrop');
    if (summaryClose) summaryClose.addEventListener('click', closeSummaryPopup);
    if (summaryBackdrop) summaryBackdrop.addEventListener('click', closeSummaryPopup);

    // Avatar modal close
    if (avatarModalClose) avatarModalClose.addEventListener('click', closeAvatarPicker);
    if (avatarModalOverlay) avatarModalOverlay.addEventListener('click', closeAvatarPicker);

    // Escape key closes modals
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        closeSummaryPopup();
        closeAvatarPicker();
        if (avatarDropdown) avatarDropdown.classList.remove('open');
        // m-8: keep aria-expanded in sync with the visible state so AT users
        // hear the dropdown collapse when Escape closes it.
        if (avatarBtn) avatarBtn.setAttribute('aria-expanded', 'false');
        if (addZettelDropdown) {
          addZettelDropdown.classList.remove('open');
        }
      }
    });

    // Create Kasten modal
    setupCreateKastenModal(token);

    // UX-8: refresh badge on stale interaction (60 s TTL).
    bindBadgeFreshness(token);
  }

  // ── Create Kasten Modal ───────────────────────────────────────────

  var _createKastenNodes = [];
  var _createKastenNodesLoaded = false;
  var _createKastenNodesFetchedAt = 0;
  var _createKastenSelectedIds = new Set();
  var _createKastenInflight = null;
  var ALL_KASTEN_SOURCES = ['youtube', 'github', 'reddit', 'substack', 'medium', 'twitter', 'web', 'generic'];
  var KASTEN_CHOOSER_TTL_MS = 5000;

  function setupCreateKastenModal(token) {
    var overlay = document.getElementById('create-kasten-overlay');
    var form = document.getElementById('create-kasten-form');
    var nameInput = document.getElementById('kasten-name');
    var descInput = document.getElementById('kasten-desc');
    var errEl = document.getElementById('create-kasten-error');
    var submit = document.getElementById('create-kasten-submit');
    var sourcePanel = document.getElementById('kasten-scope-source-panel');
    var specificPanel = document.getElementById('kasten-scope-specific-panel');
    var zettelSearch = document.getElementById('kasten-zettel-search');
    if (!overlay || !form) return;

    function openModal() {
      // UX-5: paint the modal SHELL synchronously before doing any data work.
      // Prior code's chooser-build / graph-fetch happened on the same tick
      // and pushed first-paint to ~12 s; now everything async runs after rAF.
      errEl.textContent = '';
      form.reset();
      _createKastenSelectedIds = new Set();
      if (sourcePanel) sourcePanel.classList.add('hidden');
      if (specificPanel) specificPanel.classList.add('hidden');
      overlay.classList.remove('hidden');
      setBodyScrollLocked(true);
      // Show a placeholder in the (still-hidden) chooser so when the user
      // toggles the Specific radio there's visible feedback immediately.
      var listEl = document.getElementById('kasten-zettel-list');
      if (listEl && (!_createKastenNodesLoaded || _createKastenNodes.length === 0)) {
        listEl.innerHTML = '<div class="create-kasten-zettel-loading"><span class="btn-inline-spinner" aria-hidden="true"></span>Loading zettels…</div>';
      }
      setTimeout(function () { nameInput && nameInput.focus(); }, 30);
      // Defer the network fetch to the next frame so the modal paints first.
      requestAnimationFrame(function () {
        var ageMs = Date.now() - _createKastenNodesFetchedAt;
        if (!_createKastenNodesLoaded || ageMs > KASTEN_CHOOSER_TTL_MS) {
          loadCreateKastenNodes(token).then(function () {
            if (specificPanel && !specificPanel.classList.contains('hidden')) {
              renderCreateKastenZettelList(zettelSearch ? zettelSearch.value : '');
            }
          });
        }
      });
    }
    function closeModal() {
      overlay.classList.add('hidden');
      setBodyScrollLocked(false);
    }

    // Bind Create Kasten via event delegation so the click survives any
    // async toolbar re-render (the direct getElementById listener went stale
    // when auth resolved after initial paint and replaced the button node).
    document.body.addEventListener('click', function (e) {
      if (e.target.closest('[data-action="create-kasten"]')) openModal();
    });
    overlay.addEventListener('click', function (e) {
      if (e.target.hasAttribute('data-close-kasten')) closeModal();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !overlay.classList.contains('hidden')) closeModal();
    });

    // Scope radio toggle
    form.querySelectorAll('input[name="kasten-scope"]').forEach(function (r) {
      r.addEventListener('change', async function () {
        var v = r.value;
        if (sourcePanel) sourcePanel.classList.toggle('hidden', v !== 'source');
        if (specificPanel) specificPanel.classList.toggle('hidden', v !== 'specific');
        if (v === 'specific') {
          if (!_createKastenNodesLoaded) {
            await loadCreateKastenNodes(token);
          }
          renderCreateKastenZettelList(zettelSearch ? zettelSearch.value : '');
        }
      });
    });

    if (zettelSearch) {
      zettelSearch.addEventListener('input', function () {
        renderCreateKastenZettelList(zettelSearch.value || '');
      });
    }

    // UX-3: explicit Refresh button in the chooser header.
    var refreshBtn = document.getElementById('kasten-chooser-refresh');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', async function () {
        refreshBtn.disabled = true;
        try {
          await loadCreateKastenNodes(token, { force: true });
          renderCreateKastenZettelList(zettelSearch ? zettelSearch.value : '');
        } finally {
          refreshBtn.disabled = false;
        }
      });
    }

    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      errEl.textContent = '';
      var name = (nameInput.value || '').trim();
      if (!name) { errEl.textContent = 'Name is required'; return; }
      if (name.length > 80) { errEl.textContent = 'Name must be 80 characters or fewer'; return; }
      var quality = (form.querySelector('input[name="kasten-quality"]:checked') || {}).value || 'fast';
      var desc = (descInput.value || '').trim();
      var scope = (form.querySelector('input[name="kasten-scope"]:checked') || {}).value || 'all';
      var pricingActionId = form.getAttribute('data-pricing-action-id') || ('kasten:' + Date.now() + ':' + Math.random().toString(36).slice(2));
      form.setAttribute('data-pricing-action-id', pricingActionId);

      var pickedSources = [];
      if (scope === 'source') {
        form.querySelectorAll('input[name="kasten-source"]:checked').forEach(function (c) { pickedSources.push(c.value); });
        if (!pickedSources.length) { errEl.textContent = 'Select at least one source type'; return; }
      }
      var pickedNodeIds = [];
      if (scope === 'specific') {
        pickedNodeIds = Array.from(_createKastenSelectedIds);
        if (!pickedNodeIds.length) { errEl.textContent = 'Select at least one zettel'; return; }
      }

      // UX-6: prevent re-submit while busy + spinner glyph + data-busy.
      if (submit.disabled) return;
      submit.disabled = true;
      submit.setAttribute('aria-busy', 'true');
      submit.innerHTML = '<span class="btn-inline-spinner" aria-hidden="true"></span>Creating Kasten…';
      form.setAttribute('data-busy', 'true');
      try {
        var resp = await fetch('/api/rag/sandboxes', {
          method: 'POST',
          headers: {
            'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ name: name, description: desc || null, default_quality: quality, client_action_id: pricingActionId })
        });
        if (!resp.ok) {
          var detail = '';
          var raw = '';
          try { raw = await resp.text(); } catch(_) {}
          try { var j = JSON.parse(raw); detail = (j && (j.detail || j.error)) || ''; } catch (_) {}
          console.error('[create-kasten] failed', resp.status, raw);
          if (detail && detail.code === 'quota_exhausted' && window.ZKPricing) {
            await window.ZKPricing.openPurchase({
              detail: detail,
              source: 'home:create-kasten',
              resumeAction: { type: 'create_kasten', name: name, description: desc, scope: scope, clientActionId: pricingActionId },
              onResume: function () {
                form.setAttribute('data-pricing-action-id', pricingActionId);
                form.requestSubmit();
              }
            });
            return;
          }
          if (resp.status === 409) errEl.textContent = 'A kasten with that name already exists';
          else if (resp.status === 401) errEl.textContent = 'Please sign in again';
          else errEl.textContent = (detail && detail.message) || detail || ('Create failed (' + resp.status + ')');
          return;
        }
        var created = await resp.json();
        var sandboxId = created && created.sandbox && created.sandbox.id;

        if (sandboxId) {
          var memberBody = null;
          if (scope === 'all') memberBody = { source_types: ALL_KASTEN_SOURCES, added_via: 'bulk_source' };
          else if (scope === 'source') memberBody = { source_types: pickedSources, added_via: 'bulk_source' };
          else if (scope === 'specific') memberBody = { node_ids: pickedNodeIds, added_via: 'manual' };
          if (memberBody) {
            try {
              var addResp = await fetch('/api/rag/sandboxes/' + encodeURIComponent(sandboxId) + '/members', {
                method: 'POST',
                headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' },
                body: JSON.stringify(memberBody)
              });
              if (!addResp.ok) {
                var addRaw = '';
                try { addRaw = await addResp.text(); } catch(_) {}
                console.warn('[create-kasten] add members failed', addResp.status, addRaw);
              }
            } catch (addErr) {
              console.warn('[create-kasten] add members network error', addErr);
            }
          }
        }

        closeModal();
        await loadKastens(token);
      } catch (err) {
        console.error('[home] Create kasten failed:', err);
        errEl.textContent = 'Network error. Please try again.';
      } finally {
        submit.disabled = false;
        submit.removeAttribute('aria-busy');
        submit.textContent = 'Create';
        form.removeAttribute('data-busy');
        form.removeAttribute('data-pricing-action-id');
      }
    });
  }

  async function loadCreateKastenNodes(token, opts) {
    opts = opts || {};
    // Reuse an in-flight request so concurrent callers (open + radio toggle)
    // don't double-fetch.
    if (_createKastenInflight && !opts.force) return _createKastenInflight;
    _createKastenInflight = (async function () {
      try {
        // UX-3: source the chooser from /api/graph?view=my so newly-added
        // zettels are always present (graph is the canonical user view).
        var resp = await fetch('/api/graph?view=my', {
          credentials: 'include',
          headers: { 'Authorization': 'Bearer ' + token }
        });
        if (!resp.ok) {
          console.warn('[create-kasten] load graph failed', resp.status);
          _createKastenNodes = [];
        } else {
          var data = await resp.json();
          var nodes = data.nodes || [];
          // Normalize to the shape renderCreateKastenZettelList expects:
          // {id, name, source_type, summary}. Graph uses `group`.
          _createKastenNodes = nodes.map(function (n) {
            return {
              id: n.id,
              name: n.name || n.title || n.id,
              source_type: n.group || n.source_type || 'web',
              summary: n.summary || n.description || ''
            };
          });
        }
      } catch (e) {
        console.warn('[create-kasten] load graph err', e);
        _createKastenNodes = [];
      }
      _createKastenNodesLoaded = true;
      _createKastenNodesFetchedAt = Date.now();
    })();
    try {
      await _createKastenInflight;
    } finally {
      _createKastenInflight = null;
    }
  }

  function renderCreateKastenZettelList(query) {
    var list = document.getElementById('kasten-zettel-list');
    if (!list) return;
    var q = (query || '').trim().toLowerCase();
    var filtered = _createKastenNodes.filter(function (n) {
      if (!q) return true;
      var hay = ((n.name || '') + ' ' + (n.summary || '') + ' ' + (n.source_type || '')).toLowerCase();
      return hay.indexOf(q) !== -1;
    }).slice(0, 200);

    if (!filtered.length) {
      list.innerHTML = '<div class="create-kasten-zettel-empty">No zettels match.</div>';
      return;
    }
    list.innerHTML = '';
    filtered.forEach(function (n) {
      var row = document.createElement('label');
      row.className = 'create-kasten-zettel-item';
      var checked = _createKastenSelectedIds.has(n.id) ? 'checked' : '';
      row.innerHTML =
        '<input type="checkbox" data-node-id="' + escapeHtml(n.id) + '" ' + checked + ' />' +
        '<div class="create-kasten-zettel-body">' +
          '<div class="create-kasten-zettel-title">' + escapeHtml(n.name || n.id) + '</div>' +
          '<div class="create-kasten-zettel-meta">' + escapeHtml(n.source_type || 'web') + '</div>' +
        '</div>';
      var cb = row.querySelector('input');
      cb.addEventListener('change', function () {
        if (cb.checked) _createKastenSelectedIds.add(n.id);
        else _createKastenSelectedIds.delete(n.id);
      });
      list.appendChild(row);
    });
  }

  // ── Start ─────────────────────────────────────────────────────────

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
