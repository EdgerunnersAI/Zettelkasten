(function () {
  'use strict';

  var _supabaseClient = null;
  var _session = null;
  var _token = '';
  var _allNodes = [];
  var _activeSource = 'all';
  var _activeTags = new Set();
  var _activeSort = 'newest';

  var _deleteConfirmId = null;
  var _deleteConfirmTimer = null;
  var _pendingDelete = null;
  var _bodyLockCount = 0;

  var avatarBtn;
  var avatarWrap;
  var avatarDropdown;
  var avatarImg;
  var avatarFallback;
  var menuSignout;
  var menuNexus;

  var statTotal;
  var statSources;
  var statLatest;

  var searchInput;
  var listEl;
  var emptyEl;

  var filtersWrap;
  var filtersBtn;
  var filtersBtnText;
  var filtersMenu;
  var filterParentSource;
  var filterParentTags;
  var submenuSource;
  var submenuTags;
  var filtersClear;
  var sortWrap;
  var sortBtn;
  var sortBtnText;
  var sortMenu;

  var addWrap;
  var addBtn;
  var addDropdown;
  var addForm;
  var addUrlInput;
  var addSubmitBtn;
  var addError;

  var summaryOverlay;
  var summaryBackdrop;
  var summaryClose;
  var summarySource;
  var summaryDate;
  var summaryTitle;
  var summaryText;
  var summaryTags;

  var undoToast;
  var undoText;
  var undoBtn;
  var undoTime;

  function resolveDom() {
    avatarBtn = document.getElementById('avatar-btn');
    avatarWrap = document.getElementById('avatar-wrap');
    avatarDropdown = document.getElementById('avatar-dropdown');
    avatarImg = document.getElementById('avatar-img');
    avatarFallback = document.getElementById('avatar-fallback');
    menuSignout = document.getElementById('menu-signout');
    menuNexus = document.getElementById('menu-nexus');

    statTotal = document.getElementById('stat-total');
    statSources = document.getElementById('stat-sources');
    statLatest = document.getElementById('stat-latest');

    searchInput = document.getElementById('zettels-search');
    listEl = document.getElementById('zettels-list');
    emptyEl = document.getElementById('zettels-empty');

    filtersWrap = document.getElementById('filters-wrap');
    filtersBtn = document.getElementById('filters-btn');
    filtersBtnText = document.getElementById('filters-btn-text');
    filtersMenu = document.getElementById('filters-menu');
    filterParentSource = document.getElementById('filter-parent-source');
    filterParentTags = document.getElementById('filter-parent-tags');
    submenuSource = document.getElementById('submenu-source');
    submenuTags = document.getElementById('submenu-tags');
    filtersClear = document.getElementById('filters-clear');
    sortWrap = document.getElementById('sort-wrap');
    sortBtn = document.getElementById('sort-btn');
    sortBtnText = document.getElementById('sort-btn-text');
    sortMenu = document.getElementById('sort-menu');

    addWrap = document.getElementById('add-zettel-wrap');
    addBtn = document.getElementById('add-zettel-btn');
    addDropdown = document.getElementById('add-zettel-dropdown');
    addForm = document.getElementById('add-zettel-form');
    addUrlInput = document.getElementById('add-url-input');
    addSubmitBtn = document.getElementById('add-submit-btn');
    addError = document.getElementById('add-error');

    summaryOverlay = document.getElementById('summary-overlay');
    summaryBackdrop = document.getElementById('summary-backdrop');
    summaryClose = document.getElementById('summary-close');
    summarySource = document.getElementById('summary-source');
    summaryDate = document.getElementById('summary-date');
    summaryTitle = document.getElementById('summary-title');
    summaryText = document.getElementById('summary-text');
    summaryTags = document.getElementById('summary-tags');

    undoToast = document.getElementById('undo-toast');
    undoText = document.getElementById('undo-text');
    undoBtn = document.getElementById('undo-btn');
    undoTime = document.getElementById('undo-time');
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

  async function initSupabase() {
    try {
      var resp = await fetch('/api/auth/config');
      var config = await resp.json();
      if (!config.supabase_url || !config.supabase_anon_key) return null;
      return supabase.createClient(config.supabase_url, config.supabase_anon_key, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          storage: window.localStorage,
          storageKey: 'zk-auth-token',
        },
      });
    } catch (err) {
      console.error('[user_zettels] Supabase init failed:', err);
      return null;
    }
  }

  async function init() {
    resolveDom();
    _supabaseClient = await initSupabase();
    if (!_supabaseClient) {
      window.location.href = '/';
      return;
    }

    var sessionResult = await _supabaseClient.auth.getSession();
    _session = sessionResult.data.session;
    _token = _session ? _session.access_token : '';

    if (!_token) {
      window.location.href = '/';
      return;
    }

    var profile = await fetchProfile(_token);
    if (!profile) {
      window.location.href = '/';
      return;
    }

    // Avatar lifecycle owned by shared ZKHeader (preload + retry + fallback)
    if (window.ZKHeader && typeof window.ZKHeader.boot === 'function') {
      await window.ZKHeader.boot(_token, { profile: profile });
    }
    bindEvents();
    await loadZettels();
  }

  async function fetchProfile(token) {
    try {
      var resp = await fetch('/api/me', {
        headers: { Authorization: 'Bearer ' + token }
      });
      if (!resp.ok) return null;
      return await resp.json();
    } catch (err) {
      console.error('[user_zettels] Profile fetch failed:', err);
      return null;
    }
  }

  async function loadZettels() {
    try {
      var resp = await fetch('/api/graph?view=my', {
        headers: { Authorization: 'Bearer ' + _token }
      });
      var graph = await resp.json();
      var nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
      _allNodes = nodes.map(normalizeNode);
    } catch (err) {
      console.error('[user_zettels] Failed to load zettels:', err);
      _allNodes = [];
    }

    rebuildFilterMenus();
    updateStats(_allNodes);
    applyFilters();
  }

  function normalizeNode(node) {
    var source = normalizeSource(node.group || node.source_type || 'web');
    var summaryParts = extractSummaryParts(node.summary || '');
    if (node.description) {
      var descriptionParts = extractSummaryParts(node.description || '');
      if (!summaryParts.brief || summaryParts.brief === summaryParts.detailed) {
        summaryParts.brief = descriptionParts.brief || summaryParts.brief;
      }
      if (!summaryParts.detailed || summaryParts.detailed === summaryParts.brief) {
        summaryParts.detailed = descriptionParts.detailed || summaryParts.detailed;
      }
      if (!summaryParts.detailedStructured && descriptionParts.detailedStructured) {
        summaryParts.detailedStructured = descriptionParts.detailedStructured;
      }
    }
    var cleanTags = (Array.isArray(node.tags) ? node.tags : [])
      .map(normalizeTag)
      .filter(Boolean);

    return {
      id: node.id || createLocalNodeId(node.name || node.title || 'zettel'),
      title: (node.name || node.title || 'Untitled').trim(),
      summary: summaryParts.brief,
      briefSummary: summaryParts.brief,
      detailedSummary: summaryParts.detailed,
      detailedStructured: summaryParts.detailedStructured || null,
      tags: uniqueStrings(cleanTags),
      normalizedTags: uniqueStrings(cleanTags.map(function (tag) { return tag.toLowerCase(); })),
      url: (node.url || '').trim(),
      date: normalizeCaptureDate(node.date || node.node_date || node.captured_at || node.created_at || ''),
      source: source,
      sourceLabel: sourceLabel(source),
      summaryLength: summaryParts.detailed.length || summaryParts.brief.length
    };
  }

  function normalizeSource(value) {
    var raw = (value || 'web').toString().trim().toLowerCase();
    if (raw === 'generic' || raw === 'hackernews') return 'web';
    if (raw === 'news' || raw === 'newsletter') return 'newsletter';
    if (raw === 'yt') return 'youtube';
    if (raw === 'gh') return 'github';
    if (raw === 'rd') return 'reddit';
    if (raw === 'ss') return 'substack';
    if (raw === 'md') return 'medium';
    return raw || 'web';
  }

  function sourceLabel(source) {
    if (source === 'substack') return 'Newsletter';
    if (source === 'web') return 'Web';
    return titleCase(source);
  }

  function normalizeTag(tag) {
    var clean = (tag || '').toString().trim();
    if (!clean) return '';
    var slashIndex = clean.indexOf('/');
    if (slashIndex >= 0) clean = clean.slice(slashIndex + 1);
    return clean.trim();
  }

  function rebuildFilterMenus() {
    renderSourceFilterMenu();
    renderTagFilterMenu();
    updateFiltersButtonText();
    syncSortMenuOptions();
  }

  function renderSourceFilterMenu() {
    if (!submenuSource) return;

    submenuSource.innerHTML = '';
    submenuSource.appendChild(createSourceOption('all', 'All Sources'));

    uniqueSources(_allNodes).forEach(function (src) {
      submenuSource.appendChild(createSourceOption(src, sourceLabel(src)));
    });
  }

  function renderTagFilterMenu() {
    if (!submenuTags) return;
    submenuTags.innerHTML = '';

    var tags = uniqueTags(_allNodes);
    if (!tags.length) {
      var empty = document.createElement('div');
      empty.className = 'zettels-filter-sub-option';
      empty.textContent = 'No tags yet';
      empty.style.cursor = 'default';
      empty.style.opacity = '0.65';
      submenuTags.appendChild(empty);
      return;
    }

    tags.forEach(function (tag) {
      submenuTags.appendChild(createTagOption(tag));
    });
  }

  function createSourceOption(value, label) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'zettels-filter-sub-option' + (_activeSource === value ? ' selected' : '');
    btn.textContent = label;
    btn.setAttribute('role', 'menuitemradio');
    btn.setAttribute('aria-checked', _activeSource === value ? 'true' : 'false');
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      _activeSource = value;
      clearDeleteConfirmState();
      renderSourceFilterMenu();
      updateFiltersButtonText();
      applyFilters();
    });
    return btn;
  }

  function createTagOption(tag) {
    var normalized = tag.toLowerCase();
    var isSelected = _activeTags.has(normalized);

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'zettels-tag-option' + (isSelected ? ' selected' : '');
    btn.setAttribute('role', 'menuitemcheckbox');
    btn.setAttribute('aria-checked', isSelected ? 'true' : 'false');
    btn.innerHTML =
      '<span class="zettels-tag-check" aria-hidden="true"></span>' +
      '<span>#' + escapeHtml(tag) + '</span>';

    btn.addEventListener('click', function (e) {
      e.preventDefault();
      if (_activeTags.has(normalized)) _activeTags.delete(normalized);
      else _activeTags.add(normalized);
      clearDeleteConfirmState();
      renderTagFilterMenu();
      updateFiltersButtonText();
      applyFilters();
    });

    return btn;
  }

  function uniqueSources(nodes) {
    var seen = {};
    var list = [];
    nodes.forEach(function (node) {
      if (node.source && !seen[node.source]) {
        seen[node.source] = true;
        list.push(node.source);
      }
    });
    list.sort(function (a, b) { return sourceLabel(a).localeCompare(sourceLabel(b)); });
    return list;
  }

  function uniqueTags(nodes) {
    var seen = {};
    var list = [];
    nodes.forEach(function (node) {
      (node.tags || []).forEach(function (tag) {
        var normalized = tag.toLowerCase();
        if (normalized && !seen[normalized]) {
          seen[normalized] = true;
          list.push(tag);
        }
      });
    });
    list.sort(function (a, b) { return a.localeCompare(b); });
    return list;
  }

  function updateFiltersButtonText() {
    if (!filtersBtnText) return;
    var parts = [];
    if (_activeSource !== 'all') {
      parts.push('Source: ' + sourceLabel(_activeSource));
    }
    if (_activeTags.size > 0) {
      parts.push('Tags: ' + String(_activeTags.size));
    }
    filtersBtnText.textContent = parts.length ? parts.join(' | ') : 'All Filters';
  }

  function updateSortButtonText() {
    if (!sortBtnText || !sortMenu) return;
    var option = sortMenu.querySelector('.sort-option[data-sort="' + _activeSort + '"]');
    sortBtnText.textContent = option ? option.textContent : 'Newest First';
  }

  function syncSortMenuOptions() {
    if (!sortMenu) return;
    var options = sortMenu.querySelectorAll('.sort-option');
    options.forEach(function (opt) {
      var selected = opt.getAttribute('data-sort') === _activeSort;
      opt.classList.toggle('selected', selected);
      opt.setAttribute('aria-checked', selected ? 'true' : 'false');
    });
    updateSortButtonText();
  }

  function updateStats(nodes) {
    var total = nodes.length;
    var sources = {};
    var latest = '';

    nodes.forEach(function (node) {
      sources[node.source] = true;
      if ((node.date || '') > latest) latest = node.date;
    });

    if (statTotal) statTotal.textContent = String(total);
    if (statSources) statSources.textContent = String(Object.keys(sources).length);
    if (statLatest) statLatest.textContent = latest ? formatDate(latest) : '-';
  }

  function applyFilters(options) {
    var opts = options || {};
    var restoreId = opts.restoreId || '';
    var query = ((searchInput && searchInput.value) || '').trim().toLowerCase();
    var sortMode = _activeSort || 'newest';

    var filtered = _allNodes.filter(function (node) {
      if (_activeSource !== 'all' && node.source !== _activeSource) return false;

      if (_activeTags.size) {
        var matchesAllTags = true;
        _activeTags.forEach(function (selectedTag) {
          if (node.normalizedTags.indexOf(selectedTag) === -1) matchesAllTags = false;
        });
        if (!matchesAllTags) return false;
      }

      if (!query) return true;
      var haystack = [
        node.title,
        node.briefSummary,
        node.detailedSummary,
        node.source,
        node.sourceLabel,
        (node.tags || []).join(' ')
      ].join(' ').toLowerCase();
      return haystack.indexOf(query) !== -1;
    });

    filtered.sort(function (a, b) {
      if (sortMode === 'oldest') return (a.date || '').localeCompare(b.date || '');
      if (sortMode === 'title') return a.title.localeCompare(b.title);
      if (sortMode === 'summary_long') return b.summaryLength - a.summaryLength;
      if (sortMode === 'summary_short') return a.summaryLength - b.summaryLength;
      return (b.date || '').localeCompare(a.date || '');
    });

    renderList(filtered, { restoreId: restoreId });
  }

  function renderList(nodes, options) {
    if (!listEl || !emptyEl) return;

    var opts = options || {};
    listEl.innerHTML = '';

    if (!nodes.length) {
      emptyEl.classList.remove('hidden');
      return;
    }

    emptyEl.classList.add('hidden');

    nodes.forEach(function (node, idx) {
      var card = createCard(node, idx, opts.restoreId === node.id);
      listEl.appendChild(card);
    });
  }

  function createCard(node, idx, shouldRestoreAnimate) {
    var card = document.createElement('article');
    card.className = 'zettels-card' + (shouldRestoreAnimate ? ' is-restoring' : '');
    card.style.animationDelay = String(idx * 0.03) + 's';
    card.tabIndex = 0;
    card.setAttribute('role', 'link');
    var safeUrl = toSafeHttpUrl(node.url);
    card.setAttribute('aria-label', safeUrl ? 'Open ' + node.title : node.title);
    card.dataset.nodeId = node.id;

    var dateBadge = node.date
      ? '<span class="home-card-date">' + escapeHtml(formatDate(node.date)) + '</span>'
      : '';

    var safeKgId = encodeURIComponent(node.id || '');
    var goToBtnHtml = safeUrl
      ? '<a class="home-card-goto-btn" href="' + escapeHtml(safeUrl) + '" target="_blank" rel="noopener noreferrer" aria-label="Open original source">' +
          '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
            '<path d="M14 4h6v6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>' +
            '<path d="M20 4L12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>' +
            '<path d="M19 13.5V19a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>' +
          '</svg>' +
          '<span class="tooltip">Open original source</span>' +
        '</a>'
      : '';

    var kgBtnHtml = '<a class="home-card-kg-btn" href="/knowledge-graph?node=' + safeKgId + '" aria-label="View in Knowledge Graph">' +
        '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
          // Three outer nodes
          '<circle cx="12" cy="4.8" r="2.2" stroke="currentColor" stroke-width="2"></circle>' +
          '<circle cx="5.2" cy="18.4" r="2.2" stroke="currentColor" stroke-width="2"></circle>' +
          '<circle cx="18.8" cy="18.4" r="2.2" stroke="currentColor" stroke-width="2"></circle>' +
          // Central hub
          '<circle cx="12" cy="12" r="1.4" stroke="currentColor" stroke-width="2"></circle>' +
          // Spokes from hub to each outer node
          '<line x1="12" y1="10.6" x2="12" y2="7"  stroke="currentColor" stroke-width="2" stroke-linecap="round"></line>' +
          '<line x1="10.9" y1="13"  x2="6.7" y2="16.7" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line>' +
          '<line x1="13.1" y1="13"  x2="17.3" y2="16.7" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line>' +
        '</svg>' +
        '<span class="tooltip">View in Knowledge Graph</span>' +
      '</a>';

    card.innerHTML =
      '<h2 class="zettels-card-title">' + escapeHtml(node.title) + '</h2>' +
      '<p class="zettels-card-summary">' + escapeHtml(truncate(node.briefSummary, 240)) + '</p>' +
      '<div class="zettels-card-meta">' +
        dateBadge +
        '<span class="home-card-source ' + node.source + '">' + escapeHtml(node.sourceLabel) + '</span>' +
        '<div class="zettels-card-actions">' +
          goToBtnHtml +
          kgBtnHtml +
          '<button class="zettels-delete-btn" type="button" aria-label="Delete zettel">' +
            '<img class="icon-trash icon-trash-img" src="/artifacts/icon-trash-bootstrap.svg" alt="" aria-hidden="true" />' +
            '<svg class="icon-check" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
              '<path d="M5 12.5L9.2 16.7L19 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>' +
            '</svg>' +
            '<span class="tooltip">Delete</span>' +
          '</button>' +
          '<span class="zettels-delete-cloud">Are you sure?</span>' +
        '</div>' +
      '</div>';

    card.addEventListener('click', function (e) {
      // Buttons stop propagation themselves — but defensively short-circuit.
      var goToBtn = e.target.closest('.home-card-goto-btn');
      if (goToBtn) {
        e.stopPropagation();
        return; // anchor handles navigation natively
      }

      var kgBtn = e.target.closest('.home-card-kg-btn');
      if (kgBtn) {
        e.stopPropagation();
        return;
      }

      var deleteBtn = e.target.closest('.zettels-delete-btn');
      if (deleteBtn) {
        e.preventDefault();
        e.stopPropagation();
        handleDeleteClick(node, card, deleteBtn);
        return;
      }

      // Card body click → open summary modal (reversed from prior behavior).
      e.preventDefault();
      openSummary(node);
    });

    card.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      // Don't trigger when focus is on a child button/link.
      if (e.target !== card) return;
      e.preventDefault();
      openSummary(node);
    });

    return card;
  }

  function handleDeleteClick(node, cardEl, buttonEl) {
    if (_pendingDelete && _pendingDelete.node.id !== node.id) return;

    if (_deleteConfirmId !== node.id) {
      clearDeleteConfirmState();
      _deleteConfirmId = node.id;
      cardEl.classList.add('delete-confirm');
      if (buttonEl) buttonEl.classList.add('confirm');
      _deleteConfirmTimer = window.setTimeout(clearDeleteConfirmState, 2600);
      return;
    }

    clearDeleteConfirmState();
    startDeleteFlow(node, cardEl);
  }

  function clearDeleteConfirmState() {
    _deleteConfirmId = null;
    if (_deleteConfirmTimer) {
      window.clearTimeout(_deleteConfirmTimer);
      _deleteConfirmTimer = null;
    }

    var current = listEl ? listEl.querySelector('.zettels-card.delete-confirm') : null;
    if (current) current.classList.remove('delete-confirm');
    if (listEl) {
      var btn = listEl.querySelector('.zettels-delete-btn.confirm');
      if (btn) btn.classList.remove('confirm');
    }
  }

  function startDeleteFlow(node, cardEl) {
    if (_pendingDelete) return;

    var sourceIndex = findNodeIndexById(node.id);
    if (sourceIndex < 0) return;

    _allNodes.splice(sourceIndex, 1);
    rebuildFilterMenus();
    updateStats(_allNodes);

    cardEl.classList.add('is-removing');
    window.setTimeout(function () {
      cardEl.classList.add('is-collapsed');
    }, 680);

    window.setTimeout(function () {
      applyFilters();
    }, 820);

    _pendingDelete = {
      node: node,
      sourceIndex: sourceIndex,
      expiresAt: Date.now() + 5000,
      timeoutId: null,
      intervalId: null
    };

    showUndoToast('Zettel deleted.');

    _pendingDelete.intervalId = window.setInterval(function () {
      if (!_pendingDelete) return;
      var secondsLeft = Math.max(0, Math.ceil((_pendingDelete.expiresAt - Date.now()) / 1000));
      if (undoTime) undoTime.textContent = secondsLeft + 's';
    }, 200);

    _pendingDelete.timeoutId = window.setTimeout(function () {
      finalizePendingDelete();
    }, 5000);
  }

  function showUndoToast(message) {
    if (!undoToast) return;
    if (undoText) undoText.textContent = message;
    if (undoBtn) undoBtn.classList.remove('hidden');
    if (undoTime) undoTime.classList.remove('hidden');
    if (undoTime) undoTime.textContent = '5s';
    undoToast.classList.remove('hidden');
  }

  function hideUndoToast() {
    if (!undoToast) return;
    undoToast.classList.add('hidden');
  }

  function handleUndoDelete() {
    if (!_pendingDelete) return;
    var pending = _pendingDelete;
    clearPendingDeleteTimers();
    _pendingDelete = null;
    hideUndoToast();

    restoreDeletedNode(pending.node, pending.sourceIndex);
  }

  async function finalizePendingDelete() {
    if (!_pendingDelete) return;
    var pending = _pendingDelete;
    clearPendingDeleteTimers();
    _pendingDelete = null;
    hideUndoToast();

    try {
      var resp = await fetch('/api/zettels/' + encodeURIComponent(pending.node.id), {
        method: 'DELETE',
        headers: {
          Authorization: 'Bearer ' + _token
        }
      });

      if (!resp.ok) {
        var detail = 'Delete failed';
        try {
          var body = await resp.json();
          detail = body.detail || detail;
        } catch (parseErr) {
          void parseErr;
        }
        throw new Error(detail);
      }
    } catch (err) {
      console.error('[user_zettels] Delete failed:', err);
      restoreDeletedNode(pending.node, pending.sourceIndex);
      showTransientToast('Delete failed. Restored.');
    }
  }

  function restoreDeletedNode(node, sourceIndex) {
    var insertAt = Math.max(0, Math.min(sourceIndex, _allNodes.length));
    _allNodes.splice(insertAt, 0, node);
    rebuildFilterMenus();
    updateStats(_allNodes);
    applyFilters({ restoreId: node.id });
  }

  function clearPendingDeleteTimers() {
    if (!_pendingDelete) return;
    if (_pendingDelete.timeoutId) window.clearTimeout(_pendingDelete.timeoutId);
    if (_pendingDelete.intervalId) window.clearInterval(_pendingDelete.intervalId);
  }

  function showTransientToast(message) {
    if (!undoToast) return;
    if (undoText) undoText.textContent = message;
    if (undoBtn) undoBtn.classList.add('hidden');
    if (undoTime) undoTime.classList.add('hidden');
    undoToast.classList.remove('hidden');
    window.setTimeout(function () {
      undoToast.classList.add('hidden');
      if (undoBtn) undoBtn.classList.remove('hidden');
      if (undoTime) undoTime.classList.remove('hidden');
    }, 2200);
  }

  async function addZettel(url, existingPricingActionId) {
    var pricingActionId = existingPricingActionId || ('zettel:' + Date.now() + ':' + Math.random().toString(36).slice(2));
    if (addError) addError.textContent = '';
    if (addSubmitBtn) addSubmitBtn.disabled = true;
    clearDeleteConfirmState();

    var dropdownRect = addDropdown ? addDropdown.getBoundingClientRect() : null;
    var targetSlot = createInsertionSlot();
    var spacer = targetSlot.spacer;
    var skeleton = null;

    await sleep(500);

    skeleton = createSkeletonCard();
    if (spacer && spacer.parentNode) {
      spacer.parentNode.replaceChild(skeleton, spacer);
    } else if (listEl) {
      listEl.insertBefore(skeleton, listEl.firstChild);
    }

    var apiPromise = window.ZKAddZettel.add({
      url: url,
      token: _token,
      clientActionId: pricingActionId,
      persist: true,
      surface: 'zettels',
      mode: 'auto'
    });

    if (addUrlInput) addUrlInput.value = '';

    if (dropdownRect && addDropdown && skeleton) {
      await shatterElement(addDropdown, skeleton.getBoundingClientRect(), skeleton);
    } else if (addDropdown) {
      addDropdown.classList.remove('open');
    }

    try {
      var envelope = await apiPromise;
      var result = envelope.summary || {};
      result.node_id = envelope.node_id;
      result.workspace_zettel_id = envelope.workspace_zettel_id;
      result.persistence = envelope.persistence;
      var newNode = normalizeNode({
        id: result.node_id || buildNodeId(result.title || 'untitled', result.source_type || 'web'),
        name: result.title || 'Untitled',
        summary: result.summary || result.brief_summary || '',
        description: result.brief_summary || '',
        tags: Array.isArray(result.tags) ? result.tags : [],
        url: result.source_url || url,
        group: result.source_type || 'web',
        date: result.captured_at || new Date().toISOString().slice(0, 10)
      });

      upsertNodeAtTop(newNode);
      rebuildFilterMenus();
      updateStats(_allNodes);

      var canInsertAtTop = shouldRenderNewNodeAtTop(newNode);
      if (canInsertAtTop && skeleton && skeleton.parentNode) {
        var newCard = createCard(newNode, 0, false);
        newCard.classList.add('is-new');
        skeleton.parentNode.replaceChild(newCard, skeleton);
        emptyEl.classList.add('hidden');
        window.setTimeout(function () {
          newCard.classList.remove('is-new');
        }, 450);
      } else {
        if (skeleton && skeleton.parentNode) skeleton.parentNode.removeChild(skeleton);
        if (spacer && spacer.parentNode) spacer.parentNode.removeChild(spacer);
        applyFilters();
      }
    } catch (err) {
      var quotaDetail = err && err.detail && err.detail.code === 'quota_exhausted' ? err.detail : null;
      if (quotaDetail && window.ZKPricing) {
        await window.ZKPricing.openPurchase({
          detail: quotaDetail,
          source: 'my-zettels:add-zettel',
          resumeAction: { type: 'add_zettel', url: url, clientActionId: pricingActionId },
          onResume: function () { return addZettel(url, pricingActionId); }
        });
        return;
      }
      console.error('[user_zettels] Add failed:', err);
      if (skeleton && skeleton.parentNode) skeleton.parentNode.removeChild(skeleton);
      if (spacer && spacer.parentNode) spacer.parentNode.removeChild(spacer);
      applyFilters();

      if (addError) addError.textContent = err.message || 'Failed to add zettel';
      if (addDropdown) addDropdown.classList.add('open');
      if (addUrlInput) addUrlInput.focus();
    } finally {
      if (addSubmitBtn) addSubmitBtn.disabled = false;
    }
  }

  function createInsertionSlot() {
    if (emptyEl) emptyEl.classList.add('hidden');
    if (!listEl) return { spacer: null };

    var cardHeight = measureCardHeight();

    var spacer = document.createElement('div');
    spacer.className = 'zettels-card-spacer';
    spacer.style.height = '0';
    spacer.style.overflow = 'hidden';
    spacer.style.transition = 'height 1.2s cubic-bezier(0.25, 0.1, 0.25, 1)';
    spacer.style.border = 'none';
    spacer.style.background = 'transparent';
    spacer.style.padding = '0';
    spacer.style.margin = '0';

    listEl.insertBefore(spacer, listEl.firstChild);
    requestAnimationFrame(function () {
      spacer.style.height = Math.round(cardHeight * 0.9) + 'px';
    });

    return { spacer: spacer };
  }

  function measureCardHeight() {
    if (!listEl) return 104;
    var firstCard = listEl.querySelector('.zettels-card');
    if (firstCard) return firstCard.getBoundingClientRect().height || 104;

    var measure = createSkeletonCard();
    measure.style.visibility = 'hidden';
    measure.style.position = 'absolute';
    listEl.appendChild(measure);
    var height = measure.getBoundingClientRect().height || 104;
    listEl.removeChild(measure);
    return height;
  }

  function createSkeletonCard() {
    var skeleton = document.createElement('article');
    skeleton.className = 'zettels-card zettels-card-skeleton';
    skeleton.style.opacity = '0';
    skeleton.style.transition = 'opacity 0.4s ease';
    skeleton.innerHTML =
      '<div class="skeleton-line skeleton-title"></div>' +
      '<div class="skeleton-line skeleton-body"></div>' +
      '<div class="zettels-card-meta">' +
        '<div class="skeleton-line skeleton-date"></div>' +
        '<div class="skeleton-line skeleton-source"></div>' +
      '</div>';
    return skeleton;
  }

  function shouldRenderNewNodeAtTop(node) {
    var query = ((searchInput && searchInput.value) || '').trim().toLowerCase();
    var sortMode = _activeSort || 'newest';
    if (sortMode !== 'newest') return false;
    if (_activeSource !== 'all' && node.source !== _activeSource) return false;

    if (_activeTags.size) {
      var matches = true;
      _activeTags.forEach(function (tag) {
        if (node.normalizedTags.indexOf(tag) === -1) matches = false;
      });
      if (!matches) return false;
    }

    if (!query) return true;
    var haystack = [
      node.title,
      node.briefSummary,
      node.detailedSummary,
      node.source,
      node.sourceLabel,
      (node.tags || []).join(' ')
    ].join(' ').toLowerCase();
    return haystack.indexOf(query) !== -1;
  }

  function upsertNodeAtTop(node) {
    var idx = findNodeIndexById(node.id);
    if (idx >= 0) _allNodes.splice(idx, 1);
    _allNodes.unshift(node);
  }

  function buildNodeId(title, sourceType) {
    var source = normalizeSource(sourceType);
    var prefixMap = {
      youtube: 'yt',
      reddit: 'rd',
      github: 'gh',
      substack: 'ss',
      newsletter: 'ss',
      medium: 'md',
      web: 'web'
    };
    var prefix = prefixMap[source] || 'web';
    var slug = slugify(title || 'untitled', 24);
    return prefix + '-' + slug;
  }

  function slugify(text, maxLen) {
    var slug = (text || '')
      .toString()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');
    return (slug || 'untitled').slice(0, maxLen || 24).replace(/-+$/g, '');
  }

  function shatterElement(sourceEl, targetRect, revealEl) {
    return new Promise(function (resolve) {
      var rect = sourceEl.getBoundingClientRect();
      var container = document.createElement('div');
      container.style.cssText = 'position:fixed;inset:0;z-index:600;pointer-events:none;overflow:hidden;';
      document.body.appendChild(container);

      var cols = 10;
      var rows = 4;
      var shardW = rect.width / cols;
      var shardH = rect.height / rows;
      var shards = [];
      var colors = [
        'hsla(172, 66%, 50%, 0.7)',
        'hsla(172, 50%, 40%, 0.6)',
        'hsla(172, 40%, 35%, 0.5)',
        'hsla(190, 50%, 30%, 0.5)',
        'hsla(210, 30%, 25%, 0.5)',
        'hsla(172, 66%, 50%, 0.4)'
      ];

      var tCols = 10;
      var tRows = 4;
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

          var idx = r * cols + c;
          var tR = Math.floor(idx / tCols) % tRows;
          var tC = idx % tCols;

          shards.push({
            el: shard,
            explodeX: startX + (Math.random() - 0.5) * 280,
            explodeY: startY + (Math.random() - 0.5) * 180 - 60,
            explodeRot: (Math.random() - 0.5) * 300,
            targetX: targetRect.left + tC * tShardW,
            targetY: targetRect.top + tR * tShardH,
            targetW: tShardW,
            targetH: tShardH
          });
        }
      }

      sourceEl.classList.remove('open');

      requestAnimationFrame(function () {
        shards.forEach(function (shardState) {
          shardState.el.style.left = shardState.explodeX + 'px';
          shardState.el.style.top = shardState.explodeY + 'px';
          shardState.el.style.transform = 'rotate(' + shardState.explodeRot + 'deg) scale(' + (0.6 + Math.random() * 0.6) + ')';
          shardState.el.style.opacity = '0.85';
        });
      });

      setTimeout(function () {
        shards.forEach(function (shardState, idx) {
          var delay = (idx % 5) * 0.02;
          shardState.el.style.transition = 'all 0.55s cubic-bezier(0.34, 1.56, 0.64, 1) ' + delay + 's';
          shardState.el.style.left = shardState.targetX + 'px';
          shardState.el.style.top = shardState.targetY + 'px';
          shardState.el.style.width = shardState.targetW + 'px';
          shardState.el.style.height = shardState.targetH + 'px';
          shardState.el.style.transform = 'rotate(0deg) scale(1)';
          shardState.el.style.opacity = '0.7';
          shardState.el.style.borderRadius = '1px';
        });
        if (revealEl) {
          setTimeout(function () {
            revealEl.style.opacity = '1';
          }, 300);
        }
      }, 600);

      setTimeout(function () {
        shards.forEach(function (shardState) {
          shardState.el.style.transition = 'all 0.35s ease-out';
          shardState.el.style.opacity = '0';
          shardState.el.style.transform = 'scale(0.95)';
        });
      }, 1200);

      setTimeout(function () {
        container.remove();
        resolve();
      }, 1550);
    });
  }

  function openSummary(node) {
    if (!summaryOverlay || !summarySource || !summaryDate || !summaryTitle || !summaryText || !summaryTags) return;

    // Match card visual pattern exactly: date pill (mono) THEN source pill.
    summarySource.className = 'home-card-source ' + node.source;
    summarySource.textContent = node.sourceLabel;
    summaryDate.className = 'home-card-date';
    summaryDate.textContent = node.date ? formatDate(node.date) : '';
    if (!node.date) summaryDate.style.display = 'none';
    else summaryDate.style.display = '';
    summaryTitle.textContent = node.title;
    renderDualSummary(summaryText, {
      brief: node.briefSummary || '',
      detailed: node.detailedSummary || '',
      detailedStructured: node.detailedStructured || null
    });

    summaryTags.innerHTML = '';
    (node.tags || []).forEach(function (tag) {
      var el = document.createElement('span');
      el.className = 'zettels-tag';
      el.textContent = '#' + tag;
      summaryTags.appendChild(el);
    });

    summaryOverlay.classList.remove('hidden');
    setBodyScrollLocked(true);
  }

  function closeSummary() {
    if (!summaryOverlay) return;
    summaryOverlay.classList.add('hidden');
    setBodyScrollLocked(false);
  }

  function openFiltersMenu() {
    if (!filtersWrap || !filtersBtn) return;
    closeSortMenu();
    filtersWrap.classList.add('open');
    filtersBtn.setAttribute('aria-expanded', 'true');
    openSubmenu('source');
  }

  function closeFiltersMenu() {
    if (!filtersWrap || !filtersBtn) return;
    filtersWrap.classList.remove('open');
    filtersBtn.setAttribute('aria-expanded', 'false');
    closeSubmenus();
  }

  function toggleFiltersMenu() {
    if (!filtersWrap) return;
    if (filtersWrap.classList.contains('open')) closeFiltersMenu();
    else openFiltersMenu();
  }

  function openSortMenu() {
    if (!sortWrap || !sortBtn) return;
    closeFiltersMenu();
    sortWrap.classList.add('open');
    sortBtn.setAttribute('aria-expanded', 'true');
  }

  function closeSortMenu() {
    if (!sortWrap || !sortBtn) return;
    sortWrap.classList.remove('open');
    sortBtn.setAttribute('aria-expanded', 'false');
  }

  function toggleSortMenu() {
    if (!sortWrap) return;
    if (sortWrap.classList.contains('open')) closeSortMenu();
    else openSortMenu();
  }

  function openSubmenu(name) {
    closeSubmenus();

    if (name === 'source' && submenuSource) {
      submenuSource.classList.add('open');
      if (filterParentSource) filterParentSource.classList.add('active');
    }

    if (name === 'tags' && submenuTags) {
      submenuTags.classList.add('open');
      if (filterParentTags) filterParentTags.classList.add('active');
    }
  }

  function closeSubmenus() {
    if (submenuSource) submenuSource.classList.remove('open');
    if (submenuTags) submenuTags.classList.remove('open');
    if (filterParentSource) filterParentSource.classList.remove('active');
    if (filterParentTags) filterParentTags.classList.remove('active');
  }

  function bindEvents() {
    if (avatarBtn && avatarDropdown && !avatarBtn.dataset.zkBound) {
      avatarBtn.dataset.zkBound = '1';
      avatarBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        avatarDropdown.classList.toggle('open');
      });
    }

    if (filtersBtn) {
      filtersBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        toggleFiltersMenu();
      });
    }

    if (sortBtn) {
      sortBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        toggleSortMenu();
      });
    }

    if (filterParentSource) {
      filterParentSource.addEventListener('mouseenter', function () { openSubmenu('source'); });
      filterParentSource.addEventListener('focus', function () { openSubmenu('source'); });
      filterParentSource.addEventListener('click', function (e) {
        e.preventDefault();
        openSubmenu('source');
      });
    }

    if (filterParentTags) {
      filterParentTags.addEventListener('mouseenter', function () { openSubmenu('tags'); });
      filterParentTags.addEventListener('focus', function () { openSubmenu('tags'); });
      filterParentTags.addEventListener('click', function (e) {
        e.preventDefault();
        openSubmenu('tags');
      });
    }

    if (filtersClear) {
      filtersClear.addEventListener('click', function () {
        _activeSource = 'all';
        _activeTags.clear();
        clearDeleteConfirmState();
        rebuildFilterMenus();
        applyFilters();
      });
    }

    if (sortMenu) {
      sortMenu.addEventListener('click', function (e) {
        var option = e.target.closest('.sort-option');
        if (!option) return;
        e.preventDefault();
        var nextSort = option.getAttribute('data-sort') || 'newest';
        if (_activeSort === nextSort) {
          closeSortMenu();
          return;
        }
        _activeSort = nextSort;
        syncSortMenuOptions();
        clearDeleteConfirmState();
        applyFilters();
        closeSortMenu();
      });
    }

    if (addBtn) {
      addBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        if (!addDropdown) return;
        addDropdown.classList.toggle('open');
        if (addDropdown.classList.contains('open') && addUrlInput) addUrlInput.focus();
      });
    }

    if (addForm) {
      addForm.addEventListener('submit', function (e) {
        e.preventDefault();
        var url = addUrlInput ? addUrlInput.value.trim() : '';
        if (!url) return;
        addZettel(url);
      });
    }

    if (searchInput) searchInput.addEventListener('input', applyFilters);

    if (menuSignout) {
      menuSignout.addEventListener('click', async function () {
        try {
          if (_supabaseClient) await _supabaseClient.auth.signOut();
        } finally {
          window.location.href = '/';
        }
      });
    }

    if (menuNexus) {
      menuNexus.addEventListener('click', function () {
        if (avatarDropdown) avatarDropdown.classList.remove('open');
      });
    }

    if (undoBtn) undoBtn.addEventListener('click', handleUndoDelete);
    if (summaryClose) summaryClose.addEventListener('click', closeSummary);
    if (summaryBackdrop) summaryBackdrop.addEventListener('click', closeSummary);

    document.addEventListener('click', function (e) {
      if (avatarDropdown && avatarWrap && !avatarWrap.contains(e.target)) {
        avatarDropdown.classList.remove('open');
      }
      if (filtersWrap && !filtersWrap.contains(e.target)) {
        closeFiltersMenu();
      }
      if (sortWrap && !sortWrap.contains(e.target)) {
        closeSortMenu();
      }
      if (addDropdown && addWrap && !addWrap.contains(e.target)) {
        addDropdown.classList.remove('open');
      }
    });

    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      closeSummary();
      clearDeleteConfirmState();
      if (avatarDropdown) avatarDropdown.classList.remove('open');
      closeFiltersMenu();
      closeSortMenu();
      if (addDropdown) addDropdown.classList.remove('open');
    });
  }

  function findNodeIndexById(nodeId) {
    for (var i = 0; i < _allNodes.length; i++) {
      if (_allNodes[i].id === nodeId) return i;
    }
    return -1;
  }

  function uniqueStrings(values) {
    var seen = {};
    var out = [];
    values.forEach(function (value) {
      if (!value) return;
      if (!seen[value]) {
        seen[value] = true;
        out.push(value);
      }
    });
    return out;
  }

  function createLocalNodeId(seed) {
    return 'local-' + slugify(seed || 'zettel', 20) + '-' + String(Date.now()).slice(-6);
  }

  function renderDualSummary(container, parts) {
    container.innerHTML = '';
    var brief = (parts && parts.brief) ? String(parts.brief).trim() : '';
    var detailed = (parts && parts.detailed) ? String(parts.detailed).trim() : '';
    var structured = (parts && isStructuredDetailed(parts.detailedStructured))
      ? parts.detailedStructured
      : null;
    var hasBrief = brief && brief !== 'No summary available for this zettel.';
    var hasDetailed = structured
      ? true
      : (detailed && detailed !== brief && detailed !== 'No summary available for this zettel.');

    if (!hasBrief && !hasDetailed) {
      container.textContent = 'No summary available for this zettel.';
      return;
    }

    if (hasBrief) {
      var briefWrap = document.createElement('div');
      briefWrap.className = 'zettels-summary-section zettels-summary-brief';
      var briefHeading = document.createElement('h3');
      briefHeading.className = 'zettels-summary-section-heading';
      briefHeading.textContent = 'Brief';
      var briefBody = document.createElement('p');
      briefBody.className = 'zettels-summary-section-body';
      briefBody.textContent = brief;
      briefWrap.appendChild(briefHeading);
      briefWrap.appendChild(briefBody);
      container.appendChild(briefWrap);
    }

    if (hasDetailed) {
      if (hasBrief) {
        var divider = document.createElement('hr');
        divider.className = 'zettels-summary-divider';
        container.appendChild(divider);
      }
      var detailedWrap = document.createElement('div');
      detailedWrap.className = 'zettels-summary-section zettels-summary-detailed';
      var detailedHeading = document.createElement('h3');
      detailedHeading.className = 'zettels-summary-section-heading';
      detailedHeading.textContent = 'Detailed';
      detailedWrap.appendChild(detailedHeading);
      if (structured) {
        renderStructuredDetailed(detailedWrap, structured);
      } else {
        renderMarkdownLite(detailedWrap, detailed);
      }
      container.appendChild(detailedWrap);
    }
  }

  function isStructuredDetailed(value) {
    if (!Array.isArray(value) || value.length === 0) return false;
    for (var i = 0; i < value.length; i++) {
      var section = value[i];
      if (!section || typeof section !== 'object' || Array.isArray(section)) return false;
      if (!('heading' in section || 'bullets' in section || 'sub_sections' in section || 'subSections' in section)) {
        return false;
      }
    }
    return true;
  }

  // Coerce any shape (array / single dict / stringified JSON / Python repr) into
  // a normalized structured-sections array, or null if nothing usable. Central
  // funnel used by extractSummaryParts so every render path gets the same
  // defensive recovery regardless of how the summary was persisted.
  function coerceStructuredDetailed(value) {
    if (value == null) return null;
    if (isStructuredDetailed(value)) return value;
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      var wrapped = [value];
      if (isStructuredDetailed(wrapped)) return wrapped;
    }
    if (typeof value !== 'string') return null;
    var trimmed = value.trim();
    if (!trimmed) return null;
    if (trimmed.charAt(0) === '[' || trimmed.charAt(0) === '{') {
      var attempts = [trimmed];
      if (trimmed.indexOf("'") !== -1) attempts.push(trimmed.replace(/'/g, '"'));
      for (var i = 0; i < attempts.length; i++) {
        try {
          var parsed = JSON.parse(attempts[i]);
          if (isStructuredDetailed(parsed)) return parsed;
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            var wrappedParsed = [parsed];
            if (isStructuredDetailed(wrappedParsed)) return wrappedParsed;
          }
        } catch (err) { void err; }
      }
    }
    // Markdown-string fallback: parse "## heading\n- bullet" form into
    // structured sections so the JSON-bullet expander + raw-schema heading
    // renamer kick in. Without this, rows that stored detailed_summary as a
    // plain markdown blob (older writer path, Steve Jobs row etc.) render as
    // literal text with raw "thesis" headings and {timestamp, title, bullets}
    // JSON leaking through as bullets.
    if (trimmed.indexOf('## ') !== -1 || trimmed.indexOf('### ') !== -1) {
      var sections = parseMarkdownToSections(trimmed);
      if (sections && sections.length) return sections;
    }
    return null;
  }

  function parseMarkdownToSections(markdown) {
    var lines = String(markdown || '').split(/\r?\n/);
    var sections = [];
    var current = null;
    var subHeading = null;
    function ensureCurrent() {
      if (!current) { current = { heading: 'Overview', bullets: [], sub_sections: {} }; sections.push(current); }
      return current;
    }
    function pushBullet(text) {
      var c = ensureCurrent();
      if (subHeading) {
        if (!c.sub_sections[subHeading]) c.sub_sections[subHeading] = [];
        c.sub_sections[subHeading].push(text);
      } else {
        c.bullets.push(text);
      }
    }
    for (var i = 0; i < lines.length; i++) {
      var raw = lines[i];
      var line = raw.replace(/\s+$/, '');
      if (!line.trim()) continue;
      var h2 = line.match(/^##\s+(.*)$/);
      var h3 = line.match(/^###\s+(.*)$/);
      var bullet = line.match(/^\s*[-*]\s+(.*)$/);
      if (h2) {
        current = { heading: h2[1].trim(), bullets: [], sub_sections: {} };
        sections.push(current);
        subHeading = null;
        continue;
      }
      if (h3) {
        subHeading = h3[1].trim();
        ensureCurrent();
        continue;
      }
      if (bullet) {
        pushBullet(bullet[1].trim());
        continue;
      }
      // Plain paragraph line → treat as a bullet in current section
      pushBullet(line.trim());
    }
    return sections.length ? sections : null;
  }

  // Map raw pipeline schema keys (youtube/newsletter/github/reddit) to
  // human-facing labels. Drop fields that should never render as their own
  // section (``format`` becomes a bullet inside Overview, never standalone).
  var RAW_HEADING_MAP = {
    'thesis': 'Core argument',
    'core_argument': 'Core argument',
    'issue_thesis': 'Core argument',
    'chapters_or_segments': 'Chapter walkthrough',
    'chapter_walkthrough': 'Chapter walkthrough',
    'demonstrations': 'Demonstrations',
    'closing_takeaway': 'Closing remarks',
    'closing_remarks': 'Closing remarks',
    'publication_identity': 'Publication identity',
    'sections': 'Sections',
    'conclusions_or_recommendations': 'Conclusions & recommendations',
    'cta': 'Call to action',
    'stance': 'Stance',
    'overview': 'Overview',
    'format': 'Format',
    'format_and_speakers': 'Format and speakers'
  };

  function prettyHeading(raw) {
    if (!raw) return raw;
    var key = String(raw).trim().toLowerCase();
    if (RAW_HEADING_MAP.hasOwnProperty(key)) return RAW_HEADING_MAP[key];
    // Title-case snake_case / lowercase fallbacks so "chapter_walkthrough"
    // style keys never leak through to the UI as-is.
    return String(raw)
      .replace(/_+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .replace(/^\w/, function (c) { return c.toUpperCase(); });
  }

  // Strip leading timestamp prefix ("00:00 — Title", "1h23m Title",
  // "[12:34] Title") from chapter headings. Per product decision timestamps
  // are never rendered in detailed summaries — too error-prone, low reward.
  function stripTimestampPrefix(label) {
    if (!label) return label;
    return String(label)
      .replace(/^\s*\[?\d{1,2}(?::\d{2}){1,2}\]?\s*[—\-:]\s*/, '')
      .replace(/^\s*\[?\d{1,2}(?::\d{2}){1,2}\]?\s+/, '')
      .replace(/^\s*\d{4}\s*[—\-]\s*/, '')  // year prefixes ("1852 — title")
      .trim();
  }

  // Chapter bullets sometimes arrive as stringified JSON objects
  // ({"timestamp":"...", "title":"...", "bullets":[...]}). Normalize them to
  // proper sub-sections so the renderer never shows a JSON blob.
  function expandChapterJsonBullets(section) {
    var bullets = Array.isArray(section.bullets) ? section.bullets : [];
    if (!bullets.length) return section;
    var subs = {};
    var leftover = [];
    bullets.forEach(function (b) {
      if (typeof b !== 'string') { leftover.push(b); return; }
      var t = b.trim();
      if (t.charAt(0) !== '{') { leftover.push(b); return; }
      try {
        var parsed = JSON.parse(t);
        if (parsed && typeof parsed === 'object') {
          var title = stripTimestampPrefix(String(parsed.title || '').trim());
          if (!title) { leftover.push(b); return; }
          var sub = Array.isArray(parsed.bullets) ? parsed.bullets.filter(Boolean).map(String) : [];
          if (!sub.length && parsed.summary) sub = [String(parsed.summary)];
          var key = title;
          var idx = 2;
          while (Object.prototype.hasOwnProperty.call(subs, key)) { key = title + ' (' + idx + ')'; idx += 1; }
          subs[key] = sub;
          return;
        }
      } catch (e) { void e; }
      leftover.push(b);
    });
    if (!Object.keys(subs).length) return section;
    var merged = {};
    if (section.sub_sections && typeof section.sub_sections === 'object') {
      Object.keys(section.sub_sections).forEach(function (k) { merged[k] = section.sub_sections[k]; });
    }
    Object.keys(subs).forEach(function (k) { merged[k] = subs[k]; });
    return {
      heading: section.heading,
      bullets: leftover,
      sub_sections: merged
    };
  }

  function normalizeRawSchemaSections(sections) {
    if (!Array.isArray(sections)) return sections;
    var out = [];
    sections.forEach(function (section) {
      if (!section || typeof section !== 'object') return;
      // Skip sections that should collapse (format is a tag, not a section)
      var rawKey = String(section.heading || '').trim().toLowerCase();
      if (rawKey === 'format') return;
      var working = expandChapterJsonBullets(section);
      var prettySubs = {};
      var subs = working.sub_sections && typeof working.sub_sections === 'object' ? working.sub_sections : {};
      Object.keys(subs).forEach(function (sk) {
        var cleanSk = stripTimestampPrefix(prettyHeading(sk));
        prettySubs[cleanSk || sk] = subs[sk];
      });
      out.push({
        heading: prettyHeading(working.heading || ''),
        bullets: Array.isArray(working.bullets) ? working.bullets : [],
        sub_sections: prettySubs
      });
    });
    return out;
  }

  // Build a chevron SVG used in the collapsible H1 button.
  function buildChevronSpan() {
    var span = document.createElement('span');
    span.className = 'zettels-summary-h2-chevron';
    span.setAttribute('aria-hidden', 'true');
    span.innerHTML = '<svg viewBox="0 0 24 24" fill="none">' +
      '<path d="M6 9L12 15L18 9" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round"></path></svg>';
    return span;
  }

  // Toggle a collapsible H1 section. Reads/writes ``aria-expanded`` on the
  // heading and ``data-collapsed`` on the paired panel. Sets max-height on
  // expansion to the panel's natural scrollHeight so the CSS transition
  // animates from 0 -> measured-height -> auto.
  function setSectionExpanded(headingEl, panelEl, expanded) {
    headingEl.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    if (expanded) {
      panelEl.removeAttribute('data-collapsed');
      // Measure target height, then animate. Using requestAnimationFrame
      // so the browser registers the starting 0px state before changing.
      panelEl.style.maxHeight = '0px';
      requestAnimationFrame(function () {
        var target = panelEl.scrollHeight;
        panelEl.style.maxHeight = target + 'px';
      });
      // After the transition completes, drop the inline max-height so
      // dynamic content (image loads, etc.) reflows naturally.
      var clearMax = function () {
        panelEl.style.maxHeight = '';
        panelEl.removeEventListener('transitionend', clearMax);
      };
      panelEl.addEventListener('transitionend', clearMax);
    } else {
      // Capture current height, then transition to 0.
      var current = panelEl.scrollHeight;
      panelEl.style.maxHeight = current + 'px';
      requestAnimationFrame(function () {
        panelEl.setAttribute('data-collapsed', 'true');
        panelEl.style.maxHeight = '0px';
      });
    }
  }

  function attachToggle(headingEl, panelEl) {
    headingEl.setAttribute('role', 'button');
    headingEl.setAttribute('tabindex', '0');
    headingEl.setAttribute('aria-expanded', 'true');
    var sectionId = 'zk-sec-' + Math.random().toString(36).slice(2, 9);
    panelEl.id = sectionId;
    headingEl.setAttribute('aria-controls', sectionId);

    var handler = function (event) {
      var expanded = headingEl.getAttribute('aria-expanded') === 'true';
      setSectionExpanded(headingEl, panelEl, !expanded);
      event.preventDefault();
    };
    headingEl.addEventListener('click', handler);
    headingEl.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
        handler(event);
      }
    });
  }

  function renderStructuredDetailed(container, sections) {
    sections = normalizeRawSchemaSections(sections) || sections;
    var firstSectionRendered = false;
    sections.forEach(function (section) {
      if (!section || typeof section !== 'object') return;
      var heading = section.heading == null ? '' : String(section.heading).trim();

      var bullets = Array.isArray(section.bullets) ? section.bullets : [];
      var subs = section.sub_sections || section.subSections;
      var hasSubs = subs && typeof subs === 'object' && !Array.isArray(subs)
        && Object.keys(subs).length > 0;

      // If there is no heading, render bullets+subs flat (legacy content
      // without an anchor heading — never collapsible).
      if (!heading) {
        appendSectionBody(container, bullets, subs, hasSubs);
        return;
      }

      var h4 = document.createElement('h4');
      h4.className = 'zettels-summary-h2';
      var labelSpan = document.createElement('span');
      labelSpan.className = 'zettels-summary-h2-label';
      labelSpan.textContent = heading;
      h4.appendChild(labelSpan);
      h4.appendChild(buildChevronSpan());
      container.appendChild(h4);

      var panel = document.createElement('div');
      panel.className = 'zettels-summary-panel';
      appendSectionBody(panel, bullets, subs, hasSubs);
      container.appendChild(panel);

      attachToggle(h4, panel);
      // Default state: first section expanded, all others collapsed.
      // Apply collapse SYNCHRONOUSLY at render time — setting aria-expanded
      // and data-collapsed directly avoids the rAF race where the modal
      // opens with measurements still pending and sections appear expanded.
      if (firstSectionRendered) {
        h4.setAttribute('aria-expanded', 'false');
        panel.setAttribute('data-collapsed', 'true');
        panel.style.maxHeight = '0px';
      }
      firstSectionRendered = true;
    });
  }

  function appendSectionBody(parent, bullets, subs, hasSubs) {
    if (bullets && bullets.length) {
      var ul = document.createElement('ul');
      ul.className = 'zettels-summary-list';
      bullets.forEach(function (bullet) {
        var text = bullet == null ? '' : String(bullet).trim();
        if (!text) return;
        var li = document.createElement('li');
        li.className = 'zettels-summary-list-item';
        li.textContent = text;
        ul.appendChild(li);
      });
      if (ul.childNodes.length) parent.appendChild(ul);
    }
    if (hasSubs) {
      Object.keys(subs).forEach(function (subHeading) {
        var subBullets = subs[subHeading];
        if (!Array.isArray(subBullets) || !subBullets.length) return;
        var h5 = document.createElement('h5');
        h5.className = 'zettels-summary-h3';
        h5.textContent = String(subHeading || '').trim();
        parent.appendChild(h5);

        var subUl = document.createElement('ul');
        subUl.className = 'zettels-summary-list';
        subBullets.forEach(function (bullet) {
          var text = bullet == null ? '' : String(bullet).trim();
          if (!text) return;
          var li = document.createElement('li');
          li.className = 'zettels-summary-list-item';
          li.textContent = text;
          subUl.appendChild(li);
        });
        if (subUl.childNodes.length) parent.appendChild(subUl);
      });
    }
  }

  function renderMarkdownLite(container, markdown) {
    var lines = String(markdown || '').split(/\r?\n/);
    var paraBuf = [];
    var listStack = null;

    function flushPara() {
      if (!paraBuf.length) return;
      var joined = paraBuf.join(' ').trim();
      if (joined) {
        var p = document.createElement('p');
        p.className = 'zettels-summary-para';
        p.textContent = joined;
        container.appendChild(p);
      }
      paraBuf = [];
    }
    function closeList() { listStack = null; }

    for (var i = 0; i < lines.length; i++) {
      var trimmed = lines[i].replace(/\s+$/, '');
      if (!trimmed.trim()) { flushPara(); closeList(); continue; }
      var h3 = trimmed.match(/^###\s+(.*)$/);
      var h2 = trimmed.match(/^##\s+(.*)$/);
      var bullet = trimmed.match(/^\s*[-*]\s+(.*)$/);
      if (h2 || h3) {
        flushPara(); closeList();
        var heading = document.createElement(h2 ? 'h4' : 'h5');
        heading.className = h2 ? 'zettels-summary-h2' : 'zettels-summary-h3';
        heading.textContent = (h2 ? h2[1] : h3[1]).trim();
        container.appendChild(heading);
        continue;
      }
      if (bullet) {
        flushPara();
        if (!listStack) {
          listStack = { el: document.createElement('ul') };
          listStack.el.className = 'zettels-summary-list';
          container.appendChild(listStack.el);
        }
        var li = document.createElement('li');
        li.className = 'zettels-summary-list-item';
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

  function extractSummaryParts(rawSummary) {
    // Accept already-parsed objects in case the caller hands us the node
    // payload directly (defensive — today the API stringifies everything,
    // but this keeps the renderer correct if that ever changes).
    var isPlainObject = rawSummary && typeof rawSummary === 'object' && !Array.isArray(rawSummary);
    var rawInput = rawSummary == null ? '' : (typeof rawSummary === 'string' ? rawSummary : '');
    var rawText = normalizeSummaryText(rawInput);
    var parsed = isPlainObject ? rawSummary : tryParseSummaryObject(rawInput);

    if (parsed) {
      var rawDetailed = parsed.detailed_summary != null ? parsed.detailed_summary : parsed.detailedSummary;
      // coerceStructuredDetailed centralizes every recovery path: native array,
      // single dict (wrap → array), stringified JSON, Python repr (single→double
      // quote swap). Anything that can't be salvaged falls through as a flat
      // string for the markdown renderer — no bad data ever reaches the DOM.
      var structuredDetailed = coerceStructuredDetailed(rawDetailed);

      var briefFromParsed = normalizeSummaryText(
        parsed.brief_summary || parsed.briefSummary || parsed.one_line_summary || parsed.summary || ''
      );
      var detailedFromParsed = structuredDetailed
        ? ''
        : normalizeSummaryText(rawDetailed || parsed.summary || '');

      var resolvedBrief = briefFromParsed || detailedFromParsed;
      var resolvedDetailed = detailedFromParsed || briefFromParsed;
      if (resolvedBrief || resolvedDetailed || structuredDetailed) {
        return {
          brief: resolvedBrief || 'No summary available for this zettel.',
          detailed: resolvedDetailed || resolvedBrief || 'No summary available for this zettel.',
          detailedStructured: structuredDetailed
        };
      }
    }

    var fallback = rawText || 'No summary available for this zettel.';
    return { brief: fallback, detailed: fallback, detailedStructured: null };
  }

  function tryParseSummaryObject(rawText) {
    var cleaned = String(rawText || '')
      .replace(/\r\n/g, '\n')
      .trim();
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
    var pattern = new RegExp('"' + fieldName + '"\\s*:\\s*"((?:\\\\.|[^"\\\\])*)"', 'i');
    var match = text.match(pattern);
    if (!match || !match[1]) return '';
    return normalizeSummaryText(match[1]);
  }

  function normalizeSummaryText(value, options) {
    var opts = options || {};
    // Non-string inputs (array / plain object) must never fall through to
    // String(value) — that yields "[object Object],[object Object]" garbage
    // which renders as a literal paragraph. Coerce them to empty so the
    // caller's fallback chain (brief or placeholder) kicks in.
    if (value != null && typeof value === 'object') return '';
    var text = String(value || '')
      .replace(/\r\n/g, '\n')
      .replace(/\\n/g, '\n')
      .replace(/\\r/g, '\r')
      .replace(/\\t/g, '\t')
      .trim();
    if (!opts.preserveEscapedQuotes) {
      text = text.replace(/\\"/g, '"');
    }
    // Defensive: if anything upstream slipped past and produced an
    // "[object Object]" string (JS's default Array/Object toString), strip
    // it rather than render it as user-facing text.
    if (/^(\[object Object\](,\s*)?)+$/.test(text)) return '';
    return text;
  }

  function truncate(value, limit) {
    if (!value || value.length <= limit) return value;
    return value.slice(0, limit - 3).trim() + '...';
  }

  function formatDate(value) {
    var parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return new Intl.DateTimeFormat('en-US', {
      year: 'numeric',
      month: 'short',
      day: '2-digit'
    }).format(parsed);
  }

  function normalizeCaptureDate(value) {
    var raw = String(value || '').trim();
    if (!raw) return '';
    var parsed = new Date(raw);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toISOString().slice(0, 10);
    }
    var match = raw.match(/^(\d{4}-\d{2}-\d{2})/);
    return match ? match[1] : '';
  }

  function titleCase(value) {
    return (value || '')
      .replace(/[_-]/g, ' ')
      .replace(/\b\w/g, function (ch) { return ch.toUpperCase(); });
  }

  function escapeHtml(value) {
    var div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
  }

  function sleep(ms) {
    return new Promise(function (resolve) {
      window.setTimeout(resolve, ms);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

