/* ============================================
   Knowledge Graph — Lightweight 3D Viewer
   ============================================
   Performance targets:
   - < 2s full load (graph visible + interactive)
   - Single shared SphereGeometry for all nodes
   - MeshBasicMaterial (no lighting = flat circle at every angle)
   - Cached materials to avoid GC churn
   - Minimal draw calls: 1 mesh + 1 sprite per node
   ============================================ */

/* test-exports:start */
// Pure helpers — extracted so vitest can exercise them without booting
// THREE / 3d-force-graph (CDN globals not available under jsdom). The
// fence markers are load-bearing — `tests/js/knowledge_graph/*.test.js`
// regex-extracts everything between them. Edit with care.
//
// WAVE-C 1c locked decisions (mem-vault VCS_HUtQLKTHzh71InGIU87I):
//   D-KG-3  Default render threshold ≥ 0.7 (Strong-only) on first load.
//   D-KG-4  Buckets: Strong ≥ 0.70, Medium 0.50–0.70, Weak 0.30–0.50.
//           Slider: 0.30–0.85 step 0.05, 250 ms debounce, re-warm at α=0.3.
//   D-KG-VIS Encode connection_strength as line OPACITY + line WIDTH only.
//            Color stays amber/gold; community modulates HUE within 30–55.
const STRENGTH_BUCKETS = {
  strong: { min: 0.7, label: 'Strong' },
  medium: { min: 0.5, label: 'Medium' },
  weak:   { min: 0.3, label: 'Weak'   }
};
const DEFAULT_MIN_STRENGTH = 0.7;
const SLIDER_MIN = 0.3;
const SLIDER_MAX = 0.85;
const SLIDER_STEP = 0.05;
const SLIDER_DEBOUNCE_MS = 250;
const AMBER_HUE_MIN = 30;
const AMBER_HUE_MAX = 55;

function snapToBucket(name) {
  const b = STRENGTH_BUCKETS[name];
  return b ? b.min : DEFAULT_MIN_STRENGTH;
}
function bucketForStrength(s) {
  const v = Number(s);
  if (!Number.isFinite(v) || v < STRENGTH_BUCKETS.weak.min) return null;
  if (v >= STRENGTH_BUCKETS.strong.min) return 'strong';
  if (v >= STRENGTH_BUCKETS.medium.min) return 'medium';
  return 'weak';
}
function cullLinksByStrength(links, threshold) {
  if (!Array.isArray(links)) return [];
  const t = Number(threshold) || 0;
  return links.filter(function (l) {
    // Missing connection_strength → coerce to 0. At threshold 0 all links
    // pass through; any threshold > 0 culls links lacking a strength field.
    const raw = l && l.connection_strength;
    const s = (raw === null || raw === undefined) ? 0 : Number(raw);
    if (!Number.isFinite(s)) return false;
    return s >= t;
  });
}
function debounce(fn, ms) {
  let h = null;
  return function () {
    const args = arguments, self = this;
    if (h) clearTimeout(h);
    h = setTimeout(function () { h = null; fn.apply(self, args); }, ms);
  };
}
function _clamp(x, lo, hi) { return x < lo ? lo : (x > hi ? hi : x); }
function edgeOpacityFor(s) {
  const v = _clamp(Number(s) || 0, 0, 1);
  return 0.2 + 0.8 * v;
}
function edgeWidthFor(s) {
  const v = _clamp(Number(s) || 0, 0, 1);
  return 0.5 + 2.5 * v;
}
function getCommunityHue(communityId) {
  // Spread integer ids deterministically across the amber band [30..55] so
  // distinct communities are visually distinguishable while keeping the
  // CLAUDE.md "no purple, amber on /knowledge-graph" rule intact.
  if (communityId === null || communityId === undefined) {
    return (AMBER_HUE_MIN + AMBER_HUE_MAX) / 2;
  }
  const span = AMBER_HUE_MAX - AMBER_HUE_MIN;
  const idx = Math.abs(parseInt(communityId, 10) || 0);
  return AMBER_HUE_MIN + (idx * 7 % (span + 1));
}
/* test-exports:end */

(function () {
  'use strict';

  // ---- Node colours by source. Adding a new source = add one row here. ----
  const COLORS = {
    youtube:    '#E05565',
    reddit:     '#E09040',
    github:     '#56C8D8',
    substack:   '#60A5FA',
    newsletter: '#60A5FA',  // matches .zettels-source-badge.newsletter HSL(205,40,68)
    medium:     '#4ADE80',
    web:        '#94A3B8'
  };
  const COLORS_INT = {
    youtube:    0xE05565,
    reddit:     0xE09040,
    github:     0x56C8D8,
    substack:   0x60A5FA,
    newsletter: 0x60A5FA,
    medium:     0x4ADE80,
    web:        0x94A3B8
  };
  const SOURCE_LABEL = {
    youtube: 'YouTube',
    reddit: 'Reddit',
    github: 'GitHub',
    substack: 'Substack',
    newsletter: 'Newsletter',
    medium: 'Medium',
    web: 'Web'
  };

  function escapeHtml(str) {
    const el = document.createElement('span');
    el.textContent = str;
    return el.innerHTML;
  }

  function showOverlay(id, text) {
    const o = document.getElementById(id);
    if (!o) return;
    if (text) {
      const t = o.querySelector('.kg-overlay-text');
      if (t) t.textContent = text;
    }
    o.classList.remove('hidden');
  }
  function hideOverlay(id) { const o = document.getElementById(id); if (o) o.classList.add('hidden'); }

  // Defensive brief-summary extractor.
  //
  // Production data ships `node.summary` as a JSON-stringified envelope:
  //   { "mini_title": "", "brief_summary": "…", "detailed_summary": [...], "closing_remarks": "…" }
  // but several legacy rows ship plain strings, and a small fraction of the
  // envelope is malformed. This function NEVER returns a value that starts
  // with "{" — it always degrades to a human-readable string.
  function extractBriefFromSummary(raw) {
    const text = String(raw == null ? '' : raw).trim();
    if (!text) return '';

    // Plain string (legacy) — return as-is, capped to 800 chars to keep panel tidy.
    if (text.charAt(0) !== '{') {
      return text.length > 800 ? text.slice(0, 800).trimEnd() + '…' : text;
    }

    // Try to parse the envelope. If it fails OR yields no usable text,
    // fall back to a stripped-of-braces best-effort excerpt.
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === 'object') {
        const candidates = [
          parsed.brief_summary,
          parsed.briefSummary,
          parsed.summary,
        ];
        for (const c of candidates) {
          if (typeof c === 'string' && c.trim()) return c.trim();
        }
        // Try first non-empty bullet of detailed_summary[0].bullets.
        const detailed = Array.isArray(parsed.detailed_summary) ? parsed.detailed_summary : [];
        for (const section of detailed) {
          const bullets = Array.isArray(section?.bullets) ? section.bullets : [];
          for (const b of bullets) {
            if (typeof b === 'string' && b.trim()) return b.trim();
          }
        }
        // Last resort: closing_remarks.
        if (typeof parsed.closing_remarks === 'string' && parsed.closing_remarks.trim()) {
          return parsed.closing_remarks.trim();
        }
      }
    } catch (_err) { /* fall through */ }

    // Could not parse and could not find a clean field — strip braces+keys
    // from the raw text and return the first 240 chars so the user sees
    // SOMETHING readable instead of a JSON dump.
    const stripped = text
      .replace(/[{}\[\]"]/g, ' ')
      .replace(/\b\w+_summary\b\s*:?/g, ' ')
      .replace(/\bmini_title\b\s*:?/g, ' ')
      .replace(/\bdetailed_summary\b\s*:?/g, ' ')
      .replace(/\bclosing_remarks\b\s*:?/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    return stripped.length > 240 ? stripped.slice(0, 240).trimEnd() + '…' : stripped;
  }

  function toSafeHttpUrl(rawUrl) {
    const value = String(rawUrl || '').trim();
    if (!value) return '';
    try {
      const parsed = new URL(value, window.location.origin);
      const protocol = parsed.protocol.toLowerCase();
      if (protocol !== 'http:' && protocol !== 'https:') return '';
      return parsed.href;
    } catch (err) {
      void err;
      return '';
    }
  }

  // ---- DOM refs ----
  const container = document.getElementById('graph-container');
  const searchInput = document.getElementById('search-input');
  const filterBtn = document.getElementById('filter-btn');
  const filterDropdown = document.getElementById('filter-dropdown');
  const sidePanel = document.getElementById('side-panel');
  const panelClose = document.getElementById('panel-close');
  const statsEl = document.getElementById('graph-stats');

  // ---- URL params ----
  const urlParams = new URLSearchParams(window.location.search);
  const spotlightId = urlParams.get('node'); // e.g. ?node=yt-attention

  // ---- State ----
  let graphData = { nodes: [], links: [] };
  let fullData = { nodes: [], links: [] };
  let graph = null;
  let selectedNode = null;
  let panelHideTimer = null;
  let highlightNodes = new Set();
  let hoverNode = null;
  let activeSources = new Set();      // populated after first /api/graph response
  let activeKastens = new Set();      // populated when user picks any
  let activeTags = new Set();         // populated when user picks any tag
  let kastenMembership = new Map();   // sandboxId -> Set<nodeId>; lazy-loaded
  let kastenList = [];                // [{id, name, member_count}, ...]
  let knownSources = new Set();       // union of COLORS keys + groups present in data
  let userOwnedIds = new Set();       // node IDs owned by logged-in user (Personal scope)
  let currentView = 'global'; // 'global' or 'my'
  let isLoggedIn = false;
  let authToken = null;
  // WAVE-C 1c: edge-strength threshold (D-KG-3 default 0.7 = Strong-only).
  let minStrength = DEFAULT_MIN_STRENGTH;
  let activeBucket = 'strong'; // exclusive bucket selection by default
  const longDateFormatter = new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric'
  });

  // ---- Auth helpers ----
  function getStoredAuthToken() {
    // Production stores the JWT under `zk-auth-token` (set by user_auth feature).
    // The Supabase-style `sb-<projectRef>-auth-token` keys are checked as a
    // fallback for any future migration. Without the `zk-auth-token` branch
    // logged-in users would see Personal greyed and Add-to-Kasten as a no-op.
    try {
      const direct = localStorage.getItem('zk-auth-token');
      if (direct) {
        const parsed = JSON.parse(direct);
        if (parsed && parsed.access_token) return parsed.access_token;
      }
    } catch (e) { /* ignore */ }
    try {
      const keys = Object.keys(localStorage);
      const sbKey = keys.find(k => k.startsWith('sb-') && k.endsWith('-auth-token'));
      if (sbKey) {
        const data = JSON.parse(localStorage.getItem(sbKey));
        if (data && data.access_token) return data.access_token;
      }
    } catch (e) { /* ignore */ }
    return null;
  }

  function authHeaders() {
    const token = getStoredAuthToken();
    return token ? { 'Authorization': 'Bearer ' + token } : {};
  }

  // ---- View toggle (always visible; Personal greys out when logged out) ----
  const viewToggle = document.getElementById('view-toggle');
  const STORAGE_KEY_VIEW = 'kg.view';

  function setViewBtns(view) {
    if (!viewToggle) return;
    viewToggle.querySelectorAll('.kg-view-btn').forEach(b => {
      const isActive = b.dataset.view === view;
      b.classList.toggle('active', isActive);
      b.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
  }

  function setPersonalEnabled(enabled) {
    const personalBtn = viewToggle?.querySelector('[data-view="my"]');
    if (!personalBtn) return;
    if (enabled) {
      personalBtn.removeAttribute('aria-disabled');
      personalBtn.removeAttribute('title');
    } else {
      personalBtn.setAttribute('aria-disabled', 'true');
      personalBtn.setAttribute('title', 'Sign in to switch to Personal');
    }
  }

  function openLoginModalFromKG() {
    // The header partial owns the login button; clicking it opens the modal.
    const btn = document.querySelector('.home-login-btn, [data-open-login], #header-login-btn');
    if (btn) { btn.click(); return; }
    // Direct modal toggle if it's already in the DOM.
    const modal = document.getElementById('login-modal');
    if (modal) { modal.classList.add('open'); return; }
    // Last-resort: bounce to landing page (where the modal lives) with a
    // return-to so the user lands back on KG after sign-in. The KG header
    // partial does NOT carry the login modal, so without this fallback the
    // greyed Personal/Kastens click would silently no-op.
    const ret = encodeURIComponent(location.pathname + location.search + location.hash);
    location.href = '/?auth=login&return=' + ret;
  }

  // Restore persisted view (only auto-restore "my" if we end up confirming login).
  const savedView = localStorage.getItem(STORAGE_KEY_VIEW);
  if (savedView === 'my' || savedView === 'global') {
    currentView = savedView === 'my' ? 'global' : savedView; // tentatively global; flip after login confirm
  }
  setViewBtns(currentView);

  // Check auth status via API.
  authToken = getStoredAuthToken();
  if (authToken) {
    fetch('/api/me', { headers: { 'Authorization': 'Bearer ' + authToken } })
      .then(r => r.ok ? r.json() : Promise.reject('not logged in'))
      .then(() => {
        isLoggedIn = true;
        setPersonalEnabled(true);
        loadKastens();
        loadUserOwnedIds();
        refreshOpenPanelAddBtn();
        if (savedView === 'my') {
          currentView = 'my';
          setViewBtns('my');
          loadGraphData();
        }
      })
      .catch(() => { isLoggedIn = false; authToken = null; setPersonalEnabled(false); });
  } else {
    setPersonalEnabled(false);
  }

  // Fetch the set of node IDs the user owns (their Personal scope).
  // Used in Global view to gate the Add-to-Kasten button: a node is only
  // addable when it itself is user-owned OR shares a link with a user-owned
  // node — i.e. the user has earned visibility into it through their graph.
  function loadUserOwnedIds() {
    fetch('/api/graph?view=my', { headers: authHeaders() })
      .then(r => r.ok ? r.json() : Promise.reject('user-graph'))
      .then(data => {
        userOwnedIds = new Set((data.nodes || []).map(n => n.id));
        refreshOpenPanelAddBtn();
      })
      .catch(() => { /* leave empty; gating will treat all nodes as not-addable */ });
  }

  // Decide whether the Add-to-Kasten button is enabled / disabled / login.
  // Called from openPanel() each time a node is selected.
  function computeAddBtnState(node) {
    if (!isLoggedIn) return 'login';
    if (currentView === 'my') return 'enabled';
    if (userOwnedIds.has(node.id)) return 'enabled';
    // Global view: addable iff at least one link of node touches a user-owned node.
    const links = (fullData && fullData.links) ? fullData.links : [];
    for (let i = 0; i < links.length; i++) {
      const l = links[i];
      const s = typeof l.source === 'object' ? l.source.id : l.source;
      const t = typeof l.target === 'object' ? l.target.id : l.target;
      if (s === node.id && userOwnedIds.has(t)) return 'enabled';
      if (t === node.id && userOwnedIds.has(s)) return 'enabled';
    }
    return 'unlinked';
  }

  // Apply Add-to-Kasten button state for the currently-open panel node.
  // Extracted so it can be re-run when isLoggedIn / userOwnedIds change
  // asynchronously after the panel is already open (avoids a race where the
  // panel was opened before /api/me resolved and stays in 'login' state).
  function _applyAddBtnState(node) {
    const addBtn = document.getElementById('panel-add-kasten');
    if (!addBtn || !node) return;
    const state = computeAddBtnState(node);
    addBtn.classList.toggle('disabled-soft', state !== 'enabled');
    if (state === 'login') {
      addBtn.setAttribute('aria-disabled', 'true');
      addBtn.title = 'Sign in to add to a Kasten';
    } else if (state === 'unlinked') {
      addBtn.setAttribute('aria-disabled', 'true');
      addBtn.title = 'Connect this to one of your zettels to add it to a Kasten';
    } else {
      addBtn.removeAttribute('aria-disabled');
      addBtn.title = 'Add to a Kasten';
    }
    addBtn.onclick = () => {
      const liveState = computeAddBtnState(node); // recompute at click time
      if (liveState === 'login') { openLoginModalFromKG(); return; }
      if (liveState === 'unlinked') { showToast(addBtn.title); return; }
      if (window.kgKastenModal) {
        window.kgKastenModal.open(node, kastenList, authHeaders, () => loadKastens());
      }
    };
  }

  // If the panel is currently open, re-apply its Add-to-Kasten state. Called
  // after auth confirms or userOwnedIds populates so the button flips from
  // "Sign in" → enabled / unlinked the moment the data lands.
  function refreshOpenPanelAddBtn() {
    if (!_currentPanelNodeId) return;
    const node = (graphData.nodes || []).find(n => n.id === _currentPanelNodeId);
    if (node) _applyAddBtnState(node);
  }

  // Lightweight transient toast — used for the "Connect this to your zettels"
  // hint when Add-to-Kasten is blocked by the connection gate. Mirrors the
  // toast shape kasten_modal.js uses on success so styling is consistent.
  function showToast(text) {
    let t = document.querySelector('.kg-toast');
    if (!t) { t = document.createElement('div'); t.className = 'kg-toast'; document.body.appendChild(t); }
    t.textContent = text;
    requestAnimationFrame(() => t.classList.add('visible'));
    setTimeout(() => t.classList.remove('visible'), 2400);
    setTimeout(() => { if (t.parentNode) t.parentNode.removeChild(t); }, 2900);
  }

  if (viewToggle) {
    viewToggle.addEventListener('click', (e) => {
      const btn = e.target.closest('.kg-view-btn');
      if (!btn) return;
      const newView = btn.dataset.view;
      // Greyed Personal → open login modal.
      if (newView === 'my' && !isLoggedIn) {
        openLoginModalFromKG();
        return;
      }
      if (newView === currentView) return;
      currentView = newView;
      localStorage.setItem(STORAGE_KEY_VIEW, newView);
      setViewBtns(newView);
      // Clear any kasten selections when leaving Personal — they no longer make sense in Global.
      if (newView === 'global') {
        activeKastens.clear();
      }
      renderKastensSection();
      loadGraphData();
    });
  }

  // ---- Smart label shortening ----
  const SEP = ' \u2014 '; // " — "
  const LEAD_FILLER = new Set(['the', 'a', 'an', 'my', 'why', 'how', 'what']);
  const TAIL_FILLER = new Set([
    'of', 'at', 'is', 'for', 'in', 'the', 'a', 'an', 'that', 'with',
    'and', 'or', 'to', 'by', 'on', 'as', 'after', 'before', 'from'
  ]);

  function _wrapTitle(name, softMax) {
    if (name.length <= softMax) return name;
    // Prefer breaking at the LAST whitespace BEFORE softMax (avoids overlong first line).
    const beforeIdx = name.lastIndexOf(' ', softMax);
    if (beforeIdx > 0) {
      return name.slice(0, beforeIdx) + '\n' + name.slice(beforeIdx + 1);
    }
    // Fallback: first whitespace AFTER softMax — never break mid-word.
    const afterIdx = name.indexOf(' ', softMax);
    if (afterIdx === -1) return name;
    return name.slice(0, afterIdx) + '\n' + name.slice(afterIdx + 1);
  }

  function getShortLabel(node) {
    const name = node.name || '';
    const sepIdx = name.indexOf(SEP);
    if (node.group === 'github') {
      return sepIdx > -1 ? name.slice(0, sepIdx) : _smartTruncate(name, 28);
    }
    if ((node.group === 'reddit' || node.group === 'substack' || node.group === 'medium' || node.group === 'newsletter') && sepIdx > -1) {
      return _smartTruncate(name.slice(sepIdx + SEP.length), 28);
    }
    const topicPart = sepIdx > -1 ? name.slice(0, sepIdx) : name;
    return _smartTruncate(topicPart, 28);
  }

  function _smartTruncate(str, maxChars) {
    const s = (str || '').trim();
    if (s.length <= maxChars) return s;
    // Strip leading filler.
    const words = s.split(/\s+/);
    while (words.length > 1 && LEAD_FILLER.has(words[0].toLowerCase())) words.shift();
    // Build word-by-word until we'd exceed maxChars.
    let out = '';
    for (const w of words) {
      if ((out + ' ' + w).trim().length > maxChars) break;
      out = (out + ' ' + w).trim();
    }
    if (!out) out = words[0].slice(0, Math.max(8, maxChars - 1));
    // Drop trailing filler.
    const parts = out.split(' ');
    while (parts.length > 1 && TAIL_FILLER.has(parts[parts.length - 1].toLowerCase())) parts.pop();
    return parts.join(' ') + (parts.join(' ').length < s.length ? '…' : '');
  }

  // ---- Node degree (connection count) for sizing ----
  function computeDegrees(data) {
    const deg = {};
    data.nodes.forEach(n => { deg[n.id] = 0; });
    data.links.forEach(l => {
      const s = typeof l.source === 'object' ? l.source.id : l.source;
      const t = typeof l.target === 'object' ? l.target.id : l.target;
      deg[s] = (deg[s] || 0) + 1;
      deg[t] = (deg[t] || 0) + 1;
    });
    return deg;
  }

  // ---- Shared geometry — ONE allocation for every node ----
  const _sphereGeo = new THREE.SphereGeometry(1, 48, 48);
  const _matCache = {};

  // Ring sprite texture (billboard — always faces camera)
  (function () {
    var s = 256, c = document.createElement('canvas');
    c.width = s; c.height = s;
    var ctx = c.getContext('2d'), h = s / 2;
    ctx.clearRect(0, 0, s, s);
    ctx.beginPath();
    ctx.arc(h, h, h * 0.94, 0, Math.PI * 2);
    ctx.arc(h, h, h * 0.76, 0, Math.PI * 2, true);
    ctx.closePath();
    ctx.fillStyle = '#ffffff';
    ctx.fill();
    _matCache['_ringTex'] = new THREE.CanvasTexture(c);
  })();

  function getSphereMat(hexColor, dim) {
    const key = hexColor + (dim ? '_d' : '');
    if (!_matCache[key]) {
      _matCache[key] = new THREE.MeshBasicMaterial({
        color: hexColor,
        transparent: dim,
        opacity: dim ? 0.12 : 1.0,
        depthWrite: !dim
      });
    }
    return _matCache[key];
  }

  let nodeDegrees = {};
  const _activeNodeIds = new Set();
  let _maxPagerank = 0;

  // ---- Load data ----
  function loadGraphData() {
    showOverlay('overlay-loading');
    hideOverlay('overlay-empty');
    hideOverlay('overlay-error');
    // Build /api/graph URL with `view` and `min_strength` (D-KG-6: the
    // server uses min_strength as part of its 30s cache key, so passing it
    // pre-filters payload AND keeps cache-key alignment with the client cull).
    const params = new URLSearchParams();
    if (currentView === 'my') params.set('view', 'my');
    params.set('min_strength', String(minStrength));
    const qs = params.toString();
    const apiUrl = '/api/graph' + (qs ? ('?' + qs) : '');
    fetch(apiUrl, { headers: authHeaders() })
      .then(function (r) { return r.ok ? r.json() : Promise.reject('api'); })
      .catch(function () { return fetch('/kg/content/graph.json').then(function (r) { return r.json(); }); })
      .then(data => {
        fullData = data;
        fullData.nodes = (fullData.nodes || []).map(node => {
          node.group = normalizeGroup(node.group);
          return node;
        });
        graphData = JSON.parse(JSON.stringify(data));
        graphData.nodes = (graphData.nodes || []).map(node => {
          node.group = normalizeGroup(node.group);
          return node;
        });
        nodeDegrees = computeDegrees(fullData);
        _maxPagerank = Math.max(...(fullData.nodes || []).map(n => n.pagerank || 0), 0.001);
        // Seed source filter from union of known + observed groups.
        const observed = new Set((fullData.nodes || []).map(n => normalizeGroup(n.group)));
        knownSources = new Set([...Object.keys(COLORS), ...observed]);
        // First load: enable all sources by default.
        if (activeSources.size === 0) {
          knownSources.forEach(s => activeSources.add(s));
        }
        renderSourceSection();
        renderTagsSection();
        if (graph) {
          // Re-apply active filters
          applyFilters();
        } else {
          initGraph();
          updateStats();
        }
        hideOverlay('overlay-loading');
        // Deep-link: ?node=<id> focuses + opens a node after init.
        try {
          const params = new URLSearchParams(window.location.search);
          const focusId = params.get('node');
          if (focusId) {
            // Allow the force-graph layout to settle before focusing so the
            // camera fly-to has a meaningful target position.
            setTimeout(function () {
              const target = (graphData.nodes || []).find(n => n.id === focusId);
              if (target) handleNodeClick(target);
            }, 1200);
          }
        } catch (e) { /* non-fatal */ }
      })
      .catch(err => {
        console.error('Failed to load graph data:', err);
        hideOverlay('overlay-loading');
        showOverlay('overlay-error', 'Could not load graph data.');
        if (statsEl) statsEl.textContent = 'Failed to load data';
      });
  }

  // Initial load
  loadGraphData();

  // ---- In-place node visual update (avoids full rebuild flicker) ----
  function _updateNodeVisual(node) {
    const obj = node.__threeObj;
    if (!obj || !obj.children) return;

    const color = COLORS_INT[node.group] || 0x888888;
    const isSelected = selectedNode && selectedNode.id === node.id;
    const isHovered = hoverNode && hoverNode.id === node.id;
    const isHighlighted = highlightNodes.size === 0 || highlightNodes.has(node.id);
    const isActive = isSelected || isHovered;
    const dim = !isHighlighted;

    const deg = nodeDegrees[node.id] || 1;
    const isSpotlight = spotlightId && spotlightId === node.id;
    let baseRadius;
    if (node.pagerank !== undefined && _maxPagerank > 0) {
      baseRadius = 2 + (node.pagerank / _maxPagerank) * 4;
    } else {
      baseRadius = Math.min(2 + deg * 0.3, 5);
    }
    const radius = isActive ? baseRadius + 1 : (isSpotlight ? baseRadius + 0.5 : baseRadius);

    for (let i = 0; i < obj.children.length; i++) {
      const child = obj.children[i];

      // Update sphere mesh scale + material
      if (child.isMesh) {
        child.scale.setScalar(radius);
        child.material = getSphereMat(color, dim);
      }

      // Update text label
      if (child.__isLabel) {
        const label = isActive ? _wrapTitle(node.name || '', 32) : getShortLabel(node);
        if (child.text !== label) child.text = label;
        child.position.set(0, -(radius + 3), 0);

        if (isActive) {
          child.color = '#ffffff';
          child.fontWeight = '700';
          child.fontSize = 120;
          child.textHeight = 2.4;
          child.backgroundColor = 'rgba(8, 12, 24, 0.92)';
          child.strokeColor = 'rgba(0, 0, 0, 0.6)';
          child.strokeWidth = 0.04;
          child.padding = 1.4;
          child.borderWidth = 0.08;
          child.borderColor = 'rgba(255, 255, 255, 0.18)';
          child.borderRadius = 0.8;
        } else {
          child.color = isHighlighted ? 'rgba(210, 216, 228, 0.78)' : 'rgba(200, 208, 220, 0.06)';
          child.fontWeight = '600';
          child.backgroundColor = false;
          child.strokeWidth = 0;
          child.padding = 0;
          child.borderWidth = 0;
        }
      }
    }
  }

  // Batch update all visible nodes (used for search/filter highlight changes)
  function _refreshAllNodeVisuals() {
    graphData.nodes.forEach(n => _updateNodeVisual(n));
  }

  // ---- 3D Graph ----
  function initGraph() {
    // useWebWorker:true offloads the d3 force layout to a Web Worker so the
    // main thread stays free for camera + label updates. ~3× smoother on
    // large graphs (>500 nodes).
    graph = new ForceGraph3D(container, { useWebWorker: true })
      .graphData(graphData)
      .backgroundColor('#06060f')
      .showNavInfo(false)

      // ---- Node: degree-scaled sphere + short text label ----
      .nodeThreeObject(node => {
        const group = new THREE.Group();
        const color = COLORS_INT[node.group] || 0x888888;

        const isSelected = selectedNode && selectedNode.id === node.id;
        const isHovered = hoverNode && hoverNode.id === node.id;
        const isHighlighted = highlightNodes.size === 0 || highlightNodes.has(node.id);
        const isActive = isSelected || isHovered;
        const dim = !isHighlighted;

        // PageRank-based radius (if available), fallback to degree-based
        const deg = nodeDegrees[node.id] || 1;
        const isSpotlight = spotlightId && spotlightId === node.id;
        let baseRadius;
        if (node.pagerank !== undefined && _maxPagerank > 0) {
          baseRadius = 2 + (node.pagerank / _maxPagerank) * 4;
        } else {
          baseRadius = Math.min(2 + deg * 0.3, 5);
        }
        const radius = isActive ? baseRadius + 1 : (isSpotlight ? baseRadius + 0.5 : baseRadius);
        const mesh = new THREE.Mesh(_sphereGeo, getSphereMat(color, dim));
        mesh.scale.setScalar(radius);
        group.add(mesh);

        // Spotlight ring (billboarded mesh — never picks up scale-stuck bug)
        if (isSpotlight) {
          const ringMat = new THREE.MeshBasicMaterial({
            color: color,
            transparent: true,
            opacity: 0.5,
            depthWrite: false,
            side: THREE.DoubleSide
          });
          const ring = new THREE.Mesh(new THREE.RingGeometry(radius * 1.4, radius * 1.7, 48), ringMat);
          ring.__isRing = true;
          ring.__nodeRadius = radius;
          group.add(ring);
        }

        // Text label — high-DPI canvas (fontSize 120 px) scaled down to
        // textHeight world-units gives crisp edges at any zoom. strokeWidth
        // is a fraction of fontSize in SpriteText — keep it ≤ 0.06 or
        // adjacent letters' strokes merge and the title looks garbled.
        const label = isActive ? _wrapTitle(node.name || '', 32) : getShortLabel(node);
        const sprite = new SpriteText(label);
        sprite.fontFace = 'Inter, -apple-system, sans-serif';
        sprite.fontWeight = '600';
        sprite.fontSize = isActive ? 120 : 90;
        sprite.textHeight = isActive ? 2.4 : 1.8;
        sprite.__isLabel = true;

        if (isActive) {
          sprite.color = '#ffffff';
          sprite.fontWeight = '700';
          sprite.backgroundColor = 'rgba(8, 12, 24, 0.92)';
          sprite.strokeColor = 'rgba(0, 0, 0, 0.6)';
          sprite.strokeWidth = 0.04;
          sprite.padding = 1.4;
          sprite.borderWidth = 0.08;
          sprite.borderColor = 'rgba(255, 255, 255, 0.18)';
          sprite.borderRadius = 0.8;
        } else {
          sprite.color = isHighlighted ? 'rgba(210, 216, 228, 0.78)' : 'rgba(200, 208, 220, 0.06)';
          sprite.backgroundColor = false;
          sprite.padding = 0;
        }
        sprite.position.set(0, -(radius + 3), 0);
        group.add(sprite);

        return group;
      })
      .nodeThreeObjectExtend(false)

      // ---- Link rendering (colors kept as-is) ----
      .linkColor(link => {
        const src = typeof link.source === 'object' ? link.source : null;
        if (src && hoverNode && (src.id === hoverNode.id || (typeof link.target === 'object' && link.target.id === hoverNode.id))) {
          return COLORS[src.group] || 'rgba(160, 180, 240, 0.8)';
        }
        return 'rgba(100, 130, 200, 0.25)';
      })
      .linkWidth(link => {
        const src = typeof link.source === 'object' ? link.source : null;
        const tgt = typeof link.target === 'object' ? link.target : null;
        const base = edgeWidthFor(link.connection_strength);
        if (hoverNode && ((src && src.id === hoverNode.id) || (tgt && tgt.id === hoverNode.id))) {
          return base + 1.0; // boost on hover, still respects strength baseline
        }
        return base;
      })
      .linkOpacity(link => edgeOpacityFor(link && link.connection_strength))
      // d3AlphaMin: stop the simulation when α drops below 0.01 (default
      // 0.001). 3× faster settle, no visible quality loss after initial
      // layout. Keeps web-worker idle sooner.
      .d3AlphaMin(0.01)
      .linkCurvature(0.15)
      .linkCurveRotation(0.4)
      // Particles — 1 per link, fast travel speed
      .linkDirectionalParticles(1)
      .linkDirectionalParticleWidth(1.0)
      .linkDirectionalParticleSpeed(0.008)
      .linkDirectionalParticleColor(link => {
        const src = typeof link.source === 'object' ? link.source : null;
        return src ? (COLORS[src.group] || '#4466aa') : '#4466aa';
      })

      // ---- Interactions ----
      .nodeLabel(() => '')  // disable default HTML tooltip (prevents double-text)
      .onNodeClick(handleNodeClick)
      .onBackgroundClick(handleBackgroundClick)
      .onNodeHover(node => {
        const prevHover = hoverNode;
        hoverNode = node || null;
        container.style.cursor = node ? 'pointer' : 'default';
        if (prevHover && prevHover !== node) _updateNodeVisual(prevHover);
        if (node) {
          _activeNodeIds.add(node.id);
          _updateNodeVisual(node);
        } else if (prevHover) {
          _activeNodeIds.delete(prevHover.id);
        }
      })

      // ---- Physics — fast convergence ----
      .d3AlphaDecay(0.025)
      .d3VelocityDecay(0.35)
      .warmupTicks(100)
      .cooldownTime(2500);

    // ---- HiDPI sharpness ----
    graph.renderer().setPixelRatio(Math.min(window.devicePixelRatio, 2));

    // ---- Minimal scene — no fog, no starfield, no point lights ----
    graph.scene().add(new THREE.AmbientLight(0xffffff, 1));

    // Force layout — wider spread, collision avoidance
    graph.d3Force('charge').strength(-200).distanceMax(400);
    graph.d3Force('link').distance(90);

    const d3 = window.d3 || null;
    if (d3 && d3.forceCenter) {
      graph.d3Force('center', d3.forceCenter(0, 0, 0).strength(0.05));
    }

    // ---- Camera ----
    const controls = graph.controls();
    controls.autoRotate = false;
    controls.autoRotateSpeed = 0.1;
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 80;
    controls.maxDistance = 600;

    const stopOrbit = () => { controls.autoRotate = false; };
    container.addEventListener('mousedown', stopOrbit);
    container.addEventListener('touchstart', stopOrbit);

    // Zoom: if spotlight node, fly to it; otherwise fit all
    if (spotlightId) {
      setTimeout(() => {
        const sNode = graphData.nodes.find(n => n.id === spotlightId);
        if (sNode) {
          handleNodeClick(sNode);
        } else {
          graph.zoomToFit(1200, 50);
        }
      }, 2200);
    } else {
      setTimeout(() => graph.zoomToFit(1200, 50), 1800);
    }

    window.addEventListener('resize', () => {
      graph.width(window.innerWidth).height(window.innerHeight);
    });

    // ---- Text scale clamping — cap label size when camera is close ----
    var _v3 = new THREE.Vector3();
    var MAX_LABEL_FRAC = 0.025;

    function clampLabelScales() {
      requestAnimationFrame(clampLabelScales);
      var cam = graph.camera();
      if (!cam) return;
      graphData.nodes.forEach(function (node) {
        var obj = node.__threeObj;
        if (!obj || !obj.children) return;
        var nodeIsActive = (selectedNode && selectedNode.id === node.id) ||
                           (hoverNode && hoverNode.id === node.id);
        for (var i = 0; i < obj.children.length; i++) {
          var child = obj.children[i];
          if (child.__isRing) {
            child.lookAt(cam.position);
          }
          if (!child.__isLabel) continue;
          // Hide the in-canvas SpriteText for the active node — the HTML
          // overlay below renders that title with crisp browser text.
          child.visible = !nodeIsActive;
          if (child.__origSy === undefined) {
            child.__origSy = child.scale.y;
            child.__origSx = child.scale.x;
          }
          obj.getWorldPosition(_v3);
          var dist = cam.position.distanceTo(_v3);
          var maxH = dist * MAX_LABEL_FRAC;
          if (child.__origSy > maxH && maxH > 0) {
            var r = maxH / child.__origSy;
            child.scale.set(child.__origSx * r, child.__origSy * r, 1);
          } else {
            child.scale.set(child.__origSx, child.__origSy, 1);
          }
        }
      });
      _updateActiveLabel();
    }

    // HTML title overlay for selected / hovered node — projects world coords
    // to screen each frame and parks the label just below the node.
    var _activeLabelEl = document.getElementById('kg-active-label');
    var _lastActiveLabelText = '';
    function _updateActiveLabel() {
      if (!_activeLabelEl) return;
      var active = selectedNode || hoverNode;
      if (!active || active.x === undefined) {
        if (!_activeLabelEl.classList.contains('hidden')) {
          _activeLabelEl.classList.add('hidden');
        }
        return;
      }
      var deg = nodeDegrees[active.id] || 1;
      var baseRadius = (active.pagerank !== undefined && _maxPagerank > 0)
        ? 2 + (active.pagerank / _maxPagerank) * 4
        : Math.min(2 + deg * 0.3, 5);
      var anchor = new THREE.Vector3(active.x || 0, (active.y || 0) - (baseRadius + 4.5), active.z || 0);
      var screen = anchor.clone().project(graph.camera());
      // Behind camera → hide.
      if (screen.z > 1) {
        if (!_activeLabelEl.classList.contains('hidden')) {
          _activeLabelEl.classList.add('hidden');
        }
        return;
      }
      var rect = container.getBoundingClientRect();
      var x = (screen.x * 0.5 + 0.5) * rect.width + rect.left;
      var y = (-screen.y * 0.5 + 0.5) * rect.height + rect.top;
      _activeLabelEl.style.transform = 'translate(-50%, 0) translate3d(' + Math.round(x) + 'px, ' + Math.round(y) + 'px, 0)';
      var name = active.name || '';
      if (name !== _lastActiveLabelText) {
        _activeLabelEl.textContent = name;
        _lastActiveLabelText = name;
      }
      if (_activeLabelEl.classList.contains('hidden')) {
        _activeLabelEl.classList.remove('hidden');
      }
    }
    requestAnimationFrame(clampLabelScales);
  }

  // ---- Node click → centre node, fly to it, then open panel ----
  let _panelOpenTimer = null;

  function handleNodeClick(node) {
    _activeNodeIds.clear();
    if (node) _activeNodeIds.add(node.id);
    const prevSelected = selectedNode;
    selectedNode = node;
    graph.controls().autoRotate = false;
    if (prevSelected && prevSelected !== node) _updateNodeVisual(prevSelected);
    _updateNodeVisual(node);

    if (_panelOpenTimer) { clearTimeout(_panelOpenTimer); _panelOpenTimer = null; }

    // If panel is already open, swap content INSTANTLY so connection-clicks feel snappy.
    const panelAlreadyOpen = sidePanel.classList.contains('visible');
    if (panelAlreadyOpen) openPanel(node);

    // Fly camera.
    const cam = graph.camera();
    const nx = node.x || 0, ny = node.y || 0, nz = node.z || 0;
    const dx = cam.position.x - nx;
    const dy = cam.position.y - ny;
    const dz = cam.position.z - nz;
    const len = Math.sqrt(dx*dx + dy*dy + dz*dz) || 1;
    const targetDist = 90;
    graph.cameraPosition({
      x: nx + (dx/len)*targetDist,
      y: ny + (dy/len)*targetDist,
      z: nz + (dz/len)*targetDist
    }, node, 1000);

    // Open panel after camera centres (only if not already open).
    if (!panelAlreadyOpen) {
      _panelOpenTimer = setTimeout(() => { openPanel(node); _panelOpenTimer = null; }, 700);
    }
  }

  function handleBackgroundClick() {
    if (_panelOpenTimer) { clearTimeout(_panelOpenTimer); _panelOpenTimer = null; }
    closePanel();
    selectedNode = null;
    _activeNodeIds.clear();
    highlightNodes.clear();
    _refreshAllNodeVisuals();
  }

  // ---- Side Panel ----
  let _currentPanelNodeId = null;

  function openPanel(node) {
    const badge = document.getElementById('panel-badge');
    const title = document.getElementById('panel-title');
    const date = document.getElementById('panel-date');
    const summary = document.getElementById('panel-summary');
    const tags = document.getElementById('panel-tags');
    const connections = document.getElementById('panel-connections');
    const link = document.getElementById('panel-link');
    const addBtn = document.getElementById('panel-add-kasten');

    const nodeGroup = normalizeGroup(node.group);
    badge.textContent = (SOURCE_LABEL[nodeGroup] || nodeGroup).toUpperCase();
    badge.className = 'kg-panel-badge ' + nodeGroup;
    title.textContent = node.name || '';

    // Empty-date hide (P0 #3).
    const formatted = formatDate(node.date);
    if (formatted && formatted !== 'Invalid Date' && formatted !== '') {
      date.textContent = formatted;
      date.classList.remove('hidden');
    } else {
      date.textContent = '';
      date.classList.add('hidden');
    }

    summary.textContent = extractBriefFromSummary(node.summary);

    const safeLink = toSafeHttpUrl(node.url);
    if (safeLink) {
      link.href = safeLink;
      link.removeAttribute('aria-disabled');
      link.tabIndex = 0;
      link.rel = 'noopener noreferrer';
      link.target = '_blank';
    } else {
      link.href = '#';
      link.setAttribute('aria-disabled', 'true');
      link.tabIndex = -1;
      link.rel = '';
      link.target = '';
    }

    // Add-to-Kasten button — three states:
    //   1. Logged out → greyed; click opens login modal directly.
    //   2. Logged in + node addable → enabled; click opens Kasten modal.
    //   3. Logged in + node NOT addable (Global scope, no link to any
    //      user-owned node) → greyed; click shows a transient hint toast.
    //
    // "Addable" means: the user owns the node OR at least one link of the
    // node connects to a node the user owns. This implements the rule
    // "If a single Node connected to their own Zettel, then the user can
    // add that particular Kasten from Global to their own Kasten."
    if (addBtn) {
      _applyAddBtnState(node);
    }

    tags.innerHTML = (Array.isArray(node.tags) ? node.tags : []).map(
      t => '<span class="kg-tag">' + escapeHtml(t) + '</span>'
    ).join('');

    const nodeLinks = graphData.links.filter(
      l => l.source === node || l.target === node ||
           l.source?.id === node.id || l.target?.id === node.id
    );
    const connectedNodes = nodeLinks.map(l => {
      const other = (l.source === node || l.source?.id === node.id) ? l.target : l.source;
      return { node: other, relation: l.relation };
    });

    connections.innerHTML = connectedNodes.map(c => `
      <div class="kg-connection" data-id="${escapeHtml(c.node.id || c.node)}">
        <span class="kg-connection-dot" style="background: ${COLORS[c.node.group] || '#888'}"></span>
        <span class="kg-connection-name">${escapeHtml(c.node.name || c.node)}</span>
        <span class="kg-connection-relation">${escapeHtml(c.relation || '')}</span>
      </div>
    `).join('');
    connections.querySelectorAll('.kg-connection').forEach(el => {
      el.addEventListener('click', () => {
        const targetId = el.dataset.id;
        const targetNode = graphData.nodes.find(n => n.id === targetId);
        if (targetNode && targetNode.id !== _currentPanelNodeId) handleNodeClick(targetNode);
      });
    });

    _currentPanelNodeId = node.id;
    sidePanel.dataset.nodeId = node.id;

    if (panelHideTimer) { clearTimeout(panelHideTimer); panelHideTimer = null; }
    sidePanel.classList.remove('hidden');
    requestAnimationFrame(() => sidePanel.classList.add('visible'));
  }

  function closePanel() {
    sidePanel.classList.remove('visible');
    if (panelHideTimer) clearTimeout(panelHideTimer);
    _currentPanelNodeId = null;
    sidePanel.dataset.nodeId = '';
    panelHideTimer = setTimeout(() => { sidePanel.classList.add('hidden'); panelHideTimer = null; }, 350);
  }

  function formatDate(dateStr) {
    const d = new Date(dateStr);
    return Number.isNaN(d.getTime()) ? String(dateStr || '') : longDateFormatter.format(d);
  }

  // ---- Search ----
  const searchClear = document.getElementById('search-clear');
  const searchCount = document.getElementById('search-count');
  let _searchDebounce = null;

  function _applySearch(query) {
    highlightNodes.clear();
    selectedNode = null;
    let matchCount = 0;
    if (query.length > 0) {
      graphData.nodes.forEach(node => {
        const nodeTags = Array.isArray(node.tags) ? node.tags : [];
        const nodeSummary = extractBriefFromSummary(node.summary);
        const match = (node.name || '').toLowerCase().includes(query) ||
                      nodeTags.some(t => String(t).toLowerCase().includes(query)) ||
                      nodeSummary.toLowerCase().includes(query);
        if (match) { highlightNodes.add(node.id); matchCount++; }
      });
    }
    // Count badge.
    if (searchCount) {
      if (query.length === 0) {
        searchCount.classList.add('hidden');
        searchCount.textContent = '';
      } else {
        searchCount.classList.remove('hidden');
        searchCount.textContent = matchCount === 0 ? '0' : String(matchCount);
      }
    }
    // Clear button.
    if (searchClear) {
      searchClear.classList.toggle('hidden', query.length === 0);
    }
    _refreshAllNodeVisuals();
    // Auto-frame matched nodes.
    if (matchCount === 1) {
      const only = graphData.nodes.find(n => highlightNodes.has(n.id));
      if (only) {
        const cam = graph.camera();
        const nx = only.x || 0, ny = only.y || 0, nz = only.z || 0;
        const dx = cam.position.x - nx, dy = cam.position.y - ny, dz = cam.position.z - nz;
        const len = Math.sqrt(dx*dx + dy*dy + dz*dz) || 1;
        const targetDist = 100;
        graph.cameraPosition({
          x: nx + (dx/len)*targetDist,
          y: ny + (dy/len)*targetDist,
          z: nz + (dz/len)*targetDist
        }, only, 800);
      }
    } else if (matchCount > 1) {
      graph.zoomToFit(800, 80, n => highlightNodes.has(n.id));
    }
  }

  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      const query = e.target.value.toLowerCase().trim();
      if (_searchDebounce) clearTimeout(_searchDebounce);
      _searchDebounce = setTimeout(() => _applySearch(query), 250);
    });
  }
  if (searchClear) {
    searchClear.addEventListener('click', () => {
      if (!searchInput) return;
      searchInput.value = '';
      _applySearch('');
      searchInput.focus();
    });
  }

  // ---- Reset view ----
  const resetViewBtn = document.getElementById('reset-view-btn');
  if (resetViewBtn) {
    resetViewBtn.addEventListener('click', () => {
      if (!graph) return;
      // Clear highlights so all nodes are visible during the fit.
      if (searchInput) searchInput.value = '';
      _applySearch('');
      selectedNode = null;
      highlightNodes.clear();
      _refreshAllNodeVisuals();
      closePanel();
      graph.zoomToFit(800, 60);
    });
  }

  // ---- Filter ----
  if (filterBtn && filterDropdown) {
    filterBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      filterDropdown.classList.toggle('hidden');
      filterBtn.classList.toggle('active');
    });
  }

  document.addEventListener('click', (e) => {
    if (filterDropdown && filterBtn && !filterDropdown.contains(e.target) && e.target !== filterBtn) {
      filterDropdown.classList.add('hidden');
      filterBtn.classList.remove('active');
    }
  });

  function renderSourceSection() {
    const body = document.getElementById('filter-source-body');
    if (!body) return;
    body.innerHTML = '';
    [...knownSources].sort().forEach(src => {
      const id = 'flt-src-' + src;
      const lbl = document.createElement('label');
      lbl.className = 'kg-filter-item';
      const checked = activeSources.has(src);
      if (!checked) lbl.classList.add('unchecked');
      lbl.innerHTML =
        '<input type="checkbox" id="' + id + '" value="' + src + '"' + (checked ? ' checked' : '') + '>' +
        '<span class="kg-filter-dot" style="background:' + (COLORS[src] || '#888') + '"></span>' +
        '<span>' + (SOURCE_LABEL[src] || src) + '</span>';
      lbl.addEventListener('click', (e) => {
        // Click anywhere on the label toggles the checkbox; we manage state ourselves.
        e.preventDefault();
        if (activeSources.has(src)) activeSources.delete(src); else activeSources.add(src);
        lbl.classList.toggle('unchecked', !activeSources.has(src));
        const cb = lbl.querySelector('input');
        if (cb) cb.checked = activeSources.has(src);
        applyFilters();
      });
      body.appendChild(lbl);
    });
  }

  function renderTagsSection() {
    const body = document.getElementById('filter-tags-body');
    if (!body) return;
    body.innerHTML = '';
    const allTags = new Set();
    (fullData.nodes || []).forEach(n => {
      (Array.isArray(n.tags) ? n.tags : []).forEach(t => {
        if (t && typeof t === 'string') allTags.add(t);
      });
    });
    const tags = [...allTags].sort((a, b) => a.localeCompare(b));
    if (tags.length === 0) {
      const p = document.createElement('p');
      p.className = 'kg-filter-empty';
      p.textContent = 'No tags available.';
      body.appendChild(p);
      return;
    }
    tags.forEach(tag => {
      const id = 'flt-tag-' + tag.replace(/[^a-z0-9_-]/gi, '_');
      const lbl = document.createElement('label');
      lbl.className = 'kg-filter-item';
      const checked = activeTags.has(tag);
      // Default = no tag filter (size 0). Visual treatment: when activeTags is empty,
      // every tag is "in scope" so don't strikethrough; once user picks any, the
      // unselected ones get the line-through-when-unchecked treatment.
      if (activeTags.size > 0 && !checked) lbl.classList.add('unchecked');
      lbl.innerHTML =
        '<input type="checkbox" id="' + id + '" value="' + escapeHtml(tag) + '"' + (checked ? ' checked' : '') + '>' +
        '<span class="kg-filter-dot" style="background:#14b8a6"></span>' +
        '<span>' + escapeHtml(tag) + '</span>';
      lbl.addEventListener('click', (e) => {
        e.preventDefault();
        if (activeTags.has(tag)) activeTags.delete(tag); else activeTags.add(tag);
        // Re-render to refresh strikethrough across siblings (since "no filter
        // active" → "filter active" flips the visual treatment of all items).
        renderTagsSection();
        applyFilters();
      });
      body.appendChild(lbl);
    });
  }

  document.querySelectorAll('.kg-filter-section-header').forEach(h => {
    const toggle = () => {
      const section = h.parentElement;
      section.classList.toggle('collapsed');
      h.setAttribute('aria-expanded', section.classList.contains('collapsed') ? 'false' : 'true');
    };
    h.addEventListener('click', toggle);
    h.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        toggle();
      }
    });
  });

  // ---- Side-panel collapsible sections (Summary / Tags / Connected Notes) ----
  (function wirePanelSections() {
    const STORAGE_PREFIX = 'kg.panel.collapsed.';
    document.querySelectorAll('.kg-panel-section[data-panel-section]').forEach(section => {
      const key = section.dataset.panelSection;
      const header = section.querySelector('.kg-panel-section-header');
      if (!header) return;
      const storageKey = STORAGE_PREFIX + key;
      // Restore persisted state (default expanded).
      try {
        if (localStorage.getItem(storageKey) === '1') {
          section.classList.add('collapsed');
          header.setAttribute('aria-expanded', 'false');
        } else {
          header.setAttribute('aria-expanded', 'true');
        }
      } catch (_e) {
        header.setAttribute('aria-expanded', 'true');
      }
      const toggle = () => {
        section.classList.toggle('collapsed');
        const collapsed = section.classList.contains('collapsed');
        header.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        try { localStorage.setItem(storageKey, collapsed ? '1' : '0'); } catch (_e) { /* ignore */ }
      };
      header.addEventListener('click', toggle);
      header.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          toggle();
        }
      });
    });
  })();

  function applyFilters() {
    // Build the set of node IDs allowed by the Kastens axis.
    let kastenAllowedIds = null; // null = no kasten filter active = allow all
    if (activeKastens.size > 0) {
      kastenAllowedIds = new Set();
      activeKastens.forEach(kid => {
        const memb = kastenMembership.get(kid);
        if (memb) memb.forEach(nid => kastenAllowedIds.add(nid));
      });
    }
    const filteredNodes = fullData.nodes.filter(n => {
      const src = normalizeGroup(n.group);
      if (!activeSources.has(src)) return false;
      if (kastenAllowedIds && !kastenAllowedIds.has(n.id)) return false;
      if (activeTags.size > 0) {
        const tags = Array.isArray(n.tags) ? n.tags : [];
        const hit = tags.some(t => activeTags.has(t));
        if (!hit) return false;
      }
      return true;
    });
    const nodeIds = new Set(filteredNodes.map(n => n.id));
    let filteredLinks = fullData.links.filter(l => {
      const src = typeof l.source === 'object' ? l.source.id : l.source;
      const tgt = typeof l.target === 'object' ? l.target.id : l.target;
      return nodeIds.has(src) && nodeIds.has(tgt);
    });
    // Client-side strength cull (D-KG-3/4). The server pre-filters using
    // ?min_strength= for the same threshold so this is normally a no-op,
    // but it guards against stale payloads after a slider change while
    // the next /api/graph fetch is in flight.
    filteredLinks = cullLinksByStrength(filteredLinks, minStrength);
    graphData = { nodes: filteredNodes, links: filteredLinks };
    nodeDegrees = computeDegrees(graphData);
    if (graph) {
      graph.graphData(graphData);
      // Re-warm at α=0.3 (NOT resetSimulation — that would re-trigger
      // warmupTicks and burn worker CPU). 0.3 is enough kinetic energy
      // for the layout to absorb new/removed links without restarting.
      if (typeof graph.d3ReheatSimulation === 'function') {
        graph.d3ReheatSimulation();
      }
    }
    updateStats();
    closePanel();
    selectedNode = null;
    highlightNodes.clear();

    // Empty-state overlay (P1 #14).
    const emptyOverlay = document.getElementById('overlay-empty');
    if (emptyOverlay) emptyOverlay.classList.toggle('hidden', filteredNodes.length > 0);

    if (filteredNodes.length > 0) setTimeout(() => graph && graph.zoomToFit(800, 60), 800);
  }

  // ---- Stats ----
  function updateStats() {
    const n = graphData.nodes.length;
    const l = graphData.links.length;
    statsEl.textContent = `${n} notes \u00B7 ${l} connections`;
  }

  // ---- Close panel button ----
  if (panelClose) {
    panelClose.addEventListener('click', () => {
      if (_panelOpenTimer) { clearTimeout(_panelOpenTimer); _panelOpenTimer = null; }
      closePanel();
      selectedNode = null;
      highlightNodes.clear();
      _refreshAllNodeVisuals();
    });
  }

  // ---- Keyboard: Escape ----
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    // 1) Close kasten modal first if open.
    const km = document.getElementById('kasten-modal');
    if (km && !km.classList.contains('hidden')) {
      window.kgKastenModal && window.kgKastenModal.close();
      return;
    }
    // 2) Close filter dropdown if open.
    if (filterDropdown && !filterDropdown.classList.contains('hidden')) {
      filterDropdown.classList.add('hidden');
      filterBtn && filterBtn.classList.remove('active');
      return;
    }
    // 3) Otherwise close panel + clear search/highlights (existing behaviour).
    if (_panelOpenTimer) { clearTimeout(_panelOpenTimer); _panelOpenTimer = null; }
    closePanel();
    selectedNode = null;
    _activeNodeIds.clear();
    highlightNodes.clear();
    hoverNode = null;
    if (searchInput) { searchInput.value = ''; _applySearch(''); }
    _refreshAllNodeVisuals();
  });

  function normalizeGroup(group) {
    var normalized = (group || '').toString().trim().toLowerCase();
    if (normalized === 'generic') return 'web';
    return normalized || 'web';
  }

  // ---- Kastens filter section ----
  function renderKastensSection() {
    const body = document.getElementById('filter-kastens-body');
    if (!body) return;
    body.innerHTML = '';
    const sectionEl = body.closest('.kg-filter-section');
    // Greyed when view is Global (Kastens are personal scope).
    // Whole section is hover-greyed with the tooltip "Sign in to switch"
    // (mirrors the greyed Personal segment in the toggle).
    if (currentView === 'global') {
      if (sectionEl) {
        sectionEl.classList.add('disabled-scope');
        sectionEl.setAttribute('title', 'Sign in to switch');
        sectionEl.setAttribute('aria-disabled', 'true');
        // Capture-phase click handler: any click anywhere in the section
        // opens login (logged-out) or switches view to Personal (logged-in).
        sectionEl.onclick = (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (!isLoggedIn) { openLoginModalFromKG(); return; }
          currentView = 'my';
          localStorage.setItem(STORAGE_KEY_VIEW, 'my');
          setViewBtns('my');
          loadGraphData();
          setTimeout(renderKastensSection, 0);
        };
      }
      // Body content: a single non-interactive hint (the section's onclick
      // captures the gesture for both logged-in and logged-out users).
      const hint = document.createElement('p');
      hint.className = 'kg-filter-empty';
      hint.textContent = 'Sign in to switch';
      body.appendChild(hint);
      return;
    }
    if (sectionEl) {
      sectionEl.classList.remove('disabled-scope');
      sectionEl.removeAttribute('title');
      sectionEl.removeAttribute('aria-disabled');
      sectionEl.onclick = null;
    }
    if (!isLoggedIn) {
      const link = document.createElement('a');
      link.className = 'kg-filter-cta-link';
      link.textContent = 'Sign in to filter by Kasten';
      link.href = '#';
      link.addEventListener('click', (e) => { e.preventDefault(); openLoginModalFromKG(); });
      body.appendChild(link);
      return;
    }
    if (kastenList.length === 0) {
      const link = document.createElement('a');
      link.className = 'kg-filter-cta-link';
      link.textContent = 'No Kastens yet — Create one →';
      link.href = '/home/kastens';
      body.appendChild(link);
      return;
    }
    kastenList.forEach(k => {
      const id = 'flt-kst-' + k.id;
      const lbl = document.createElement('label');
      lbl.className = 'kg-filter-item';
      const checked = activeKastens.has(k.id);
      if (!checked) lbl.classList.add('unchecked');
      lbl.innerHTML =
        '<input type="checkbox" id="' + id + '" value="' + k.id + '"' + (checked ? ' checked' : '') + '>' +
        '<span class="kg-filter-dot" style="background:' + (k.color || '#14b8a6') + '"></span>' +
        '<span>' + escapeHtml(k.name) + '</span>';
      lbl.addEventListener('click', async (e) => {
        e.preventDefault();
        if (activeKastens.has(k.id)) {
          activeKastens.delete(k.id);
        } else {
          activeKastens.add(k.id);
          // Lazy-load membership on first selection.
          if (!kastenMembership.has(k.id)) {
            try {
              const resp = await fetch('/api/rag/sandboxes/' + encodeURIComponent(k.id) + '/members?limit=1000', { headers: authHeaders() });
              if (resp.ok) {
                const data = await resp.json();
                const ids = new Set((data.members || []).map(m => m.node_id));
                kastenMembership.set(k.id, ids);
              } else {
                kastenMembership.set(k.id, new Set());
              }
            } catch (_e) {
              kastenMembership.set(k.id, new Set());
            }
          }
        }
        lbl.classList.toggle('unchecked', !activeKastens.has(k.id));
        const cb = lbl.querySelector('input');
        if (cb) cb.checked = activeKastens.has(k.id);
        applyFilters();
      });
      body.appendChild(lbl);
    });
  }

  function loadKastens() {
    if (!isLoggedIn) { renderKastensSection(); return; }
    fetch('/api/rag/sandboxes', { headers: authHeaders() })
      .then(r => r.ok ? r.json() : Promise.reject('not ok'))
      .then(data => {
        kastenList = (data.sandboxes || []).map(s => ({ id: s.id, name: s.name, color: s.color, member_count: s.member_count }));
        renderKastensSection();
      })
      .catch(() => {
        kastenList = [];
        renderKastensSection();
      });
  }

  const overlayEmptyReset = document.getElementById('overlay-empty-reset');
  if (overlayEmptyReset) {
    overlayEmptyReset.addEventListener('click', () => {
      activeSources = new Set([...knownSources]);
      activeKastens.clear();
      activeTags.clear();
      renderSourceSection();
      renderTagsSection();
      renderKastensSection(); // safe — defined in Task 7.2; defaults to empty if not yet loaded
      applyFilters();
    });
  }

  renderKastensSection();

  // ---- Strength bucket / slider wiring (WAVE-C 1c) ----
  // Buckets are exclusive — clicking one snaps the slider + threshold to
  // that bucket's lower bound. Slider gives fine control inside [0.30,0.85].
  // Both paths re-fetch /api/graph (cache-key-aligned) AND re-cull client-
  // side immediately for snappy UX.
  const strengthSlider = document.getElementById('strength-slider');
  const strengthValue = document.getElementById('strength-value');
  const strengthControls = document.getElementById('strength-controls');

  function _syncStrengthUI() {
    if (strengthSlider) strengthSlider.value = String(minStrength);
    if (strengthValue) strengthValue.textContent = minStrength.toFixed(2);
    if (strengthControls) {
      strengthControls.querySelectorAll('[data-bucket]').forEach(b => {
        b.classList.toggle('active', b.dataset.bucket === activeBucket);
        b.setAttribute('aria-pressed', b.dataset.bucket === activeBucket ? 'true' : 'false');
      });
    }
  }

  function _onStrengthChange(opts) {
    opts = opts || {};
    // Re-cull immediately for instant feedback.
    if (graph) applyFilters();
    // Refresh from server with new cache key (matches D-KG-6).
    loadGraphData();
    if (opts.snapBucket) activeBucket = bucketForStrength(minStrength) || activeBucket;
    _syncStrengthUI();
  }

  const _debouncedStrengthChange = debounce(function () {
    _onStrengthChange({ snapBucket: true });
  }, SLIDER_DEBOUNCE_MS);

  if (strengthSlider) {
    strengthSlider.min = String(SLIDER_MIN);
    strengthSlider.max = String(SLIDER_MAX);
    strengthSlider.step = String(SLIDER_STEP);
    strengthSlider.value = String(minStrength);
    strengthSlider.addEventListener('input', function (e) {
      const v = parseFloat(e.target.value);
      if (Number.isFinite(v)) {
        minStrength = v;
        if (strengthValue) strengthValue.textContent = v.toFixed(2);
        _debouncedStrengthChange();
      }
    });
  }

  if (strengthControls) {
    strengthControls.querySelectorAll('[data-bucket]').forEach(btn => {
      btn.addEventListener('click', function () {
        const b = btn.dataset.bucket;
        if (!STRENGTH_BUCKETS[b]) return;
        activeBucket = b;
        minStrength = snapToBucket(b);
        _syncStrengthUI();
        _onStrengthChange({ snapBucket: false });
      });
    });
  }

  _syncStrengthUI();

  const overlayRetry = document.getElementById('overlay-error-retry');
  if (overlayRetry) overlayRetry.addEventListener('click', loadGraphData);

})();
