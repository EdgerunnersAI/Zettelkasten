(function () {
  'use strict';

  var tabButtons = Array.prototype.slice.call(document.querySelectorAll('.pricing-tab'));
  var panels = Array.prototype.slice.call(document.querySelectorAll('.pricing-panel'));
  var subscriptionGrid = document.getElementById('subscription-grid');
  var packGroups = document.getElementById('pack-groups');
  var catalog = null;
  var currentSubscription = null;            // {plan_id, period_id, status,...} or null
  var ACTIVE_SUB_STATUSES = ['active', 'authenticated', 'pending_cancel', 'grace', 'paused'];
  var PLAN_RANK = { free: 0, basic: 1, max: 2 };
  var selectedPeriods = { basic: 'monthly', max: 'monthly' };
  var selectedPlan = 'basic';
  var customMeter = 'zettel';
  var customQuantity = 10;

  function userHasActiveSub() {
    return Boolean(
      currentSubscription &&
      currentSubscription.plan_id &&
      currentSubscription.plan_id !== 'free' &&
      ACTIVE_SUB_STATUSES.indexOf(currentSubscription.status) !== -1
    );
  }

  function isCurrentPlanCard(planId) {
    if (planId === 'free') return !userHasActiveSub();
    return userHasActiveSub() && currentSubscription.plan_id === planId;
  }

  function planChangeKind(targetPlanId) {
    // 'subscribe' (no active sub), 'upgrade', 'downgrade', or 'current'.
    if (!userHasActiveSub()) return 'subscribe';
    var currentRank = PLAN_RANK[currentSubscription.plan_id];
    var targetRank = PLAN_RANK[targetPlanId];
    if (currentRank === undefined || targetRank === undefined) return 'subscribe';
    if (targetRank > currentRank) return 'upgrade';
    if (targetRank < currentRank) return 'downgrade';
    return 'current';
  }

  function titleCase(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

  function syncSlidingIndicator(container, activeSelector) {
    if (!container) return;
    var active = container.querySelector(activeSelector);
    if (!active) return;
    container.style.setProperty('--indicator-x', active.offsetLeft + 'px');
    container.style.setProperty('--indicator-y', active.offsetTop + 'px');
    container.style.setProperty('--indicator-w', active.offsetWidth + 'px');
    container.style.setProperty('--indicator-h', active.offsetHeight + 'px');
  }

  function syncAllSlidingIndicators() {
    syncSlidingIndicator(document.querySelector('.pricing-tabs'), '.pricing-tab.is-active');
    Array.prototype.slice.call(document.querySelectorAll('.period-toggle')).forEach(function (toggle) {
      syncSlidingIndicator(toggle, '.period-btn.is-active');
    });
    syncSlidingIndicator(document.querySelector('.custom-topline'), '.custom-tile.is-active');
  }

  function setActive(tabName) {
    tabButtons.forEach(function (btn) {
      var active = btn.getAttribute('data-tab') === tabName;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });

    panels.forEach(function (panel) {
      var id = panel.id === 'subscription-panel' ? 'subscription' : 'custom';
      panel.classList.toggle('is-active', id === tabName);
    });

    syncAllSlidingIndicators();
  }

  function quotaSection(plan, meter, label) {
    var quota = plan.quotas[meter] || {};
    var lines = [];
    if (quota.daily) lines.push(quota.daily + ' max per day');
    if (quota.weekly) lines.push(quota.weekly + ' max per week');
    if (quota.monthly) lines.push(quota.monthly + ' max per month');
    if (quota.total) lines.push(quota.total + ' max per user');
    return [
      '<section class="quota-section">',
      '<h3>' + label + '</h3>',
      lines.map(function (line) { return '<p>' + line + '</p>'; }).join(''),
      '</section>'
    ].join('');
  }

  function customLabels() {
    return {
      zettel: { title: 'Zettels', note: 'Capture credits' },
      kasten: { title: 'Kastens', note: 'Workspace credits' },
      question: { title: 'Questions', note: 'RAG answer credits' }
    };
  }

  function planCard(plan) {
    var periodKeys = Object.keys(plan.periods);
    var activePeriod = selectedPeriods[plan.id] || periodKeys[0];
    var period = plan.periods[activePeriod];
    var buttons = periodKeys.map(function (key) {
      return '<button type="button" class="period-btn' + (key === activePeriod ? ' is-active' : '') + '" data-plan="' + plan.id + '" data-period="' + key + '" aria-pressed="' + (key === activePeriod ? 'true' : 'false') + '">' + plan.periods[key].label + '</button>';
    }).join('');
    var cta;
    if (isCurrentPlanCard(plan.id)) {
      // User is on this plan today — show "Current Plan" + (paid only) cancel X.
      var cancelBtn = (plan.id === 'free')
        ? ''
        : '<button type="button" class="cancel-sub-btn" data-cancel-sub aria-label="Cancel Subscription!" title="Cancel Subscription!"><span aria-hidden="true">×</span></button>';
      cta = ''
        + '<div class="current-plan-row">'
        + '<span class="price-cta current">Current Plan</span>'
        + cancelBtn
        + '</div>';
    } else if (plan.id === 'free') {
      cta = '<a class="price-cta muted" href="/home">Start free</a>';
    } else {
      var changeKind = planChangeKind(plan.id);
      var label = 'Subscribe';
      var changeAttr = '';
      if (changeKind === 'upgrade') {
        label = 'Upgrade to ' + titleCase(plan.id);
        changeAttr = ' data-change-sub="1"';
      } else if (changeKind === 'downgrade') {
        label = 'Downgrade to ' + titleCase(plan.id);
        changeAttr = ' data-change-sub="1"';
      }
      cta = '<button type="button" class="price-cta" data-product="' + period.id + '" data-kind="subscription" data-amount="' + period.amount + '"' + changeAttr + '>' + label + '</button>';
    }

    return [
      '<article class="price-card' + (plan.id === selectedPlan ? ' selected' : '') + (plan.id === 'basic' ? ' featured' : '') + '" data-plan-card="' + plan.id + '">',
      '<p class="price-tier">' + plan.name + '</p>',
      '<p class="price-description">' + plan.description + '</p>',
      '<div class="period-slot">' + (periodKeys.length > 1 ? '<div class="period-toggle" role="group">' + buttons + '</div>' : '') + '</div>',
      '<div class="price-block">',
      '<p class="price-amount">' + period.display_amount + '<span>' + (period.months === 1 ? '/month' : '/' + period.months + ' months') + '</span></p>',
      period.list_amount > period.amount ? '<p class="list-price">' + period.display_list_amount + '</p>' : '<p class="list-price empty">&nbsp;</p>',
      '</div>',
      '<div class="quota-list">',
      quotaSection(plan, 'zettel', 'Zettels'),
      quotaSection(plan, 'kasten', 'Kastens'),
      quotaSection(plan, 'rag_question', 'Questions'),
      '</div>',
      cta,
      '</article>'
    ].join('');
  }

  function renderSubscriptions() {
    if (!subscriptionGrid || !catalog) return;
    subscriptionGrid.innerHTML = ['free', 'basic', 'max'].map(function (id) {
      return planCard(catalog.plans[id]);
    }).join('');
    syncAllSlidingIndicators();
  }

  function renderPacks() {
    if (!packGroups || !catalog) return;
    var labels = customLabels();
    var meterPacks = catalog.packs[customMeter] || [];
    var slider = sliderSettings(customMeter);
    customQuantity = normalizeQuantityForInput(customQuantity, slider);
    var estimate = estimatePack(meterPacks, customQuantity, slider, customMeter);

    var tiles = Object.keys(labels).map(function (meter) {
      var active = meter === customMeter;
      return [
        '<button type="button" class="custom-tile' + (active ? ' is-active' : '') + '" data-custom-meter="' + meter + '" aria-pressed="' + (active ? 'true' : 'false') + '">',
        '<span>' + labels[meter].title + '</span>',
        '<small>' + labels[meter].note + '</small>',
        '<strong>' + firstPackPrice(meter) + '</strong>',
        '</button>'
      ].join('');
    }).join('');

    packGroups.innerHTML = [
      '<section class="custom-estimator">',
      '<div class="custom-topline">',
      tiles,
      '</div>',
      '<div class="custom-control-row">',
      '<div class="custom-slider-wrap">',
      '<label class="custom-slider-label" for="custom-count-range">Number of ' + labels[customMeter].title.toLowerCase() + '</label>',
      '<input id="custom-count-range" class="custom-range" type="range" min="0" max="' + (slider.values.length - 1) + '" step="1" value="' + sliderIndexForQuantity(customQuantity, slider) + '">',
      '<div class="custom-range-labels">' + slider.labels.map(function (label, index) { return '<span style="left:' + tickPosition(index, slider) + '%">' + label + '</span>'; }).join('') + '</div>',
      '</div>',
      '<div class="custom-stepper" aria-label="Custom quantity">',
      '<button type="button" data-step-qty="-10" aria-label="Decrease quantity">-</button>',
      '<input id="custom-count-input" type="number" min="' + slider.inputMin + '" step="' + slider.inputStep + '" value="' + estimate.roundedQuantity + '">',
      '<button type="button" data-step-qty="10" aria-label="Increase quantity">+</button>',
      '</div>',
      '</div>',
      '<div class="custom-bottom">',
      '<div><span class="estimate-label">Price estimate</span><strong class="estimate-price"><span class="estimate-list">' + estimate.listDisplay + '</span>' + estimate.display + '</strong><p>' + estimate.roundedQuantity + ' ' + labels[customMeter].title.toLowerCase() + ' selected</p></div>',
      '<button type="button" class="price-cta custom-buy" data-product="' + estimate.productId + '" data-kind="pack" data-amount="' + estimate.amount + '">Buy!</button>',
      '</div>',
      '</section>'
    ].join('');
    syncAllSlidingIndicators();
  }

  function syncCustomControls() {
    var labels = customLabels();
    var meterPacks = catalog.packs[customMeter] || [];
    var slider = sliderSettings(customMeter);
    customQuantity = normalizeQuantityForInput(customQuantity, slider);
    var estimate = estimatePack(meterPacks, customQuantity, slider, customMeter);

    var sliderLabel = document.querySelector('.custom-slider-label');
    if (sliderLabel) {
      sliderLabel.textContent = 'Number of ' + labels[customMeter].title.toLowerCase();
    }

    var range = document.getElementById('custom-count-range');
    if (range) {
      range.setAttribute('max', String(slider.values.length - 1));
      range.value = String(sliderIndexForQuantity(customQuantity, slider));
    }

    var rangeLabels = document.querySelector('.custom-range-labels');
    if (rangeLabels) {
      rangeLabels.innerHTML = slider.labels.map(function (label, index) {
        return '<span style="left:' + tickPosition(index, slider) + '%">' + label + '</span>';
      }).join('');
    }

    var input = document.getElementById('custom-count-input');
    if (input) {
      input.setAttribute('min', String(slider.inputMin));
      input.setAttribute('step', String(slider.inputStep));
      input.value = String(estimate.roundedQuantity);
    }

    var estimatePrice = document.querySelector('.estimate-price');
    if (estimatePrice) {
      estimatePrice.innerHTML = '<span class="estimate-list">' + estimate.listDisplay + '</span>' + estimate.display;
    }

    var estimateText = document.querySelector('.custom-bottom p');
    if (estimateText) {
      estimateText.textContent = estimate.roundedQuantity + ' ' + labels[customMeter].title.toLowerCase() + ' selected';
    }

    var buyButton = document.querySelector('.custom-buy');
    if (buyButton) {
      buyButton.setAttribute('data-product', estimate.productId);
      buyButton.setAttribute('data-amount', estimate.amount);
    }

    syncAllSlidingIndicators();
  }

  function updateCustomMeter(meter) {
    customMeter = meter;
    customQuantity = sliderSettings(customMeter).inputMin;
    Array.prototype.slice.call(document.querySelectorAll('[data-custom-meter]')).forEach(function (button) {
      var active = button.getAttribute('data-custom-meter') === customMeter;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    syncCustomControls();
  }

  function updateSelectedPlan(planId) {
    selectedPlan = planId;
    Array.prototype.slice.call(document.querySelectorAll('[data-plan-card]')).forEach(function (card) {
      card.classList.toggle('selected', card.getAttribute('data-plan-card') === selectedPlan);
    });
  }

  function updateSubscriptionPeriod(periodBtn) {
    var planId = periodBtn.getAttribute('data-plan');
    var periodId = periodBtn.getAttribute('data-period');
    var plan = catalog && catalog.plans && catalog.plans[planId];
    var period = plan && plan.periods && plan.periods[periodId];
    var card = periodBtn.closest('[data-plan-card]');
    if (!plan || !period || !card) return;

    selectedPeriods[planId] = periodId;
    updateSelectedPlan(planId);

    Array.prototype.slice.call(card.querySelectorAll('[data-period]')).forEach(function (button) {
      var active = button === periodBtn;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });

    var priceAmount = card.querySelector('.price-amount');
    if (priceAmount) {
      priceAmount.innerHTML = period.display_amount + '<span>' + (period.months === 1 ? '/month' : '/' + period.months + ' months') + '</span>';
    }

    var listPrice = card.querySelector('.list-price');
    if (listPrice) {
      listPrice.classList.toggle('empty', !(period.list_amount > period.amount));
      listPrice.innerHTML = period.list_amount > period.amount ? period.display_list_amount : '&nbsp;';
    }

    var cta = card.querySelector('[data-product]');
    if (cta) {
      cta.setAttribute('data-product', period.id);
      cta.setAttribute('data-amount', period.amount);
    }

    syncAllSlidingIndicators();
  }

  function firstPackPrice(meter) {
    var packs = catalog && catalog.packs && catalog.packs[meter] ? catalog.packs[meter] : [];
    return packs.length ? packs[0].display_amount : '';
  }

  function sortedPacks(packs) {
    return packs.slice().sort(function (a, b) { return a.quantity - b.quantity; });
  }

  function sliderSettings(meter) {
    var configured = catalog.custom_slider_values && catalog.custom_slider_values[meter];
    var fallback = meter === 'question'
      ? [50, 100, 150, 200, 250, 300, 350]
      : [1, 5, 10, 20, 30, 40, 50];
    var values = configured && configured.length ? configured : fallback;
    return sliderFromValues(values, meter === 'question' ? 50 : 1);
  }

  function sliderFromValues(values, inputStep) {
    return {
      values: values,
      labels: values.map(function (value, index) {
        return String(value) + (index === values.length - 1 ? '+' : '');
      }),
      inputMin: values[0],
      inputMax: values[values.length - 1],
      inputStep: inputStep,
      openStep: values[0] >= 50 ? 50 : 10
    };
  }

  function tickPosition(index, slider) {
    if (slider.values.length <= 1) return 0;
    return (index / (slider.values.length - 1)) * 100;
  }

  function sliderIndexForQuantity(quantity, slider) {
    var normalized = clampQuantityToSlider(quantity, slider);
    var index = slider.values.indexOf(normalized);
    return index >= 0 ? index : slider.values.length - 1;
  }

  function quantityForSliderIndex(index, slider) {
    var safeIndex = Math.max(0, Math.min(slider.values.length - 1, Number(index) || 0));
    return slider.values[safeIndex];
  }

  function clampQuantityToSlider(value, slider) {
    var raw = Number(value) || slider.inputMin;
    for (var i = 0; i < slider.values.length; i += 1) {
      if (raw <= slider.values[i]) return slider.values[i];
    }
    return slider.values[slider.values.length - 1];
  }

  function stepQuantity(currentQuantity, direction, slider) {
    var current = normalizeQuantityForInput(currentQuantity, slider);
    if (current >= slider.inputMax) {
      var next = current + (direction > 0 ? slider.openStep : -slider.openStep);
      return normalizeQuantityForInput(Math.max(slider.inputMin, next), slider);
    }
    var currentIndex = sliderIndexForQuantity(current, slider);
    var nextIndex = currentIndex + (direction > 0 ? 1 : -1);
    return quantityForSliderIndex(nextIndex, slider);
  }

  function normalizeQuantityForInput(value, slider) {
    var raw = Number(value) || slider.inputMin;
    if (raw <= slider.inputMax) return clampQuantityToSlider(raw, slider);
    return Math.ceil(raw / slider.openStep) * slider.openStep;
  }

  function estimatePack(packs, count, slider, meter) {
    var rounded = normalizeQuantityForInput(count, slider);
    if (!packs.length) return { roundedQuantity: rounded, display: '₹0', productId: '' };
    var sorted = sortedPacks(packs);
    var exact = sorted.find(function (pack) { return pack.quantity === rounded; });
    if (exact) {
      return {
        roundedQuantity: exact.quantity,
        amount: exact.amount,
        listAmount: exact.list_amount,
        display: exact.display_amount,
        listDisplay: exact.display_list_amount,
        productId: exact.id
      };
    }
    var base = sorted.find(function (pack) { return pack.quantity === slider.inputMax; }) || sorted[sorted.length - 1];
    var amount = extendAmount(base.amount, base.quantity, rounded);
    var listAmount = extendAmount(base.list_amount, base.quantity, rounded);
    return {
      roundedQuantity: rounded,
      amount: amount,
      listAmount: listAmount,
      display: formatEstimate(amount, catalog.currency),
      listDisplay: formatEstimate(listAmount, catalog.currency),
      productId: 'custom_' + meter + '_' + rounded
    };
  }

  function extendAmount(baseAmount, baseQuantity, quantity) {
    return Math.ceil((quantity * baseAmount / baseQuantity) / 100) * 100;
  }

  function formatEstimate(amount, currency) {
    if (currency === 'INR') return '₹' + Math.round(amount / 100);
    return currency + ' ' + (amount / 100).toFixed(2);
  }

  function readAuthToken() {
    try {
      var raw = window.localStorage && window.localStorage.getItem('zk-auth-token');
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      return parsed && parsed.access_token ? parsed.access_token : null;
    } catch (_) { return null; }
  }

  // Supabase client with autoRefreshToken so a user sitting on /pricing past
  // the JWT exp doesn't silently 401 every checkout call. Mirrors the init in
  // user_home.js / user_zettels.js so the same localStorage 'zk-auth-token'
  // entry is the single shared session across pages.
  var _supabase = null;
  var _pendingPurchase = null;            // queued buy intent waiting on sign-in
  var _authListenerInstalled = false;

  async function initSupabase() {
    if (_supabase) return _supabase;
    if (typeof supabase === 'undefined' || !supabase.createClient) return null;
    try {
      var resp = await fetch('/api/auth/config');
      if (!resp.ok) return null;
      var config = await resp.json();
      if (!config.supabase_url || !config.supabase_anon_key) return null;
      _supabase = supabase.createClient(config.supabase_url, config.supabase_anon_key, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          storage: window.localStorage,
          storageKey: 'zk-auth-token',
        },
      });
      // Touch the session so a stale (but still refresh-eligible) token gets
      // exchanged before the user clicks a buy button.
      try { await _supabase.auth.getSession(); } catch (_) { /* fall through */ }

      if (!_authListenerInstalled) {
        _supabase.auth.onAuthStateChange(function (event) {
          if (event === 'SIGNED_IN' || event === 'TOKEN_REFRESHED') {
            closeLoginModal();
            refreshCurrentSubscription().then(renderSubscriptions);
            // Replay the buy intent the user clicked before being asked to sign in.
            if (_pendingPurchase) {
              var pending = _pendingPurchase;
              _pendingPurchase = null;
              window.ZKPricing.openPurchase(pending).catch(function (err) {
                if (err && err.status === 401) openLoginModal(pending);
              });
            }
          }
        });
        _authListenerInstalled = true;
      }
      return _supabase;
    } catch (_) {
      return null;
    }
  }

  function openLoginModal(pendingPurchase) {
    _pendingPurchase = pendingPurchase || null;
    var modal = document.getElementById('login-modal');
    if (!modal) {
      // Fall back to homepage modal if the inline one isn't on this page.
      window.location.href = '/?auth=login&return=' + encodeURIComponent(window.location.pathname);
      return;
    }
    modal.classList.add('open');
    document.body.style.overflow = 'hidden';
    var emailInput = document.getElementById('login-email');
    if (emailInput) { try { emailInput.focus(); } catch (_) {} }
  }

  function closeLoginModal() {
    var modal = document.getElementById('login-modal');
    if (modal) {
      modal.classList.remove('open');
      document.body.style.overflow = '';
    }
  }

  // Inline phone-capture modal — installed as window.ZKPricing.promptForPhone so
  // purchase_launcher.js picks it up instead of falling back to window.prompt.
  // Returns a Promise that resolves to the phone string or null (cancelled).
  function installPhoneModalHandler() {
    if (!window.ZKPricing) return;
    var modal = document.getElementById('phone-modal');
    if (!modal) return;
    var form = document.getElementById('phone-form');
    var input = document.getElementById('phone-input');
    var errEl = document.getElementById('phone-error');
    var cancelBtn = modal.querySelector('[data-phone-cancel]');
    var overlay = modal.querySelector('[data-phone-overlay]');

    window.ZKPricing.promptForPhone = function () {
      return new Promise(function (resolve) {
        function cleanup(result) {
          modal.classList.remove('open');
          document.body.style.overflow = '';
          form.removeEventListener('submit', onSubmit);
          if (cancelBtn) cancelBtn.removeEventListener('click', onCancel);
          if (overlay) overlay.removeEventListener('click', onCancel);
          document.removeEventListener('keydown', onKey);
          resolve(result);
        }
        function onSubmit(e) {
          e.preventDefault();
          var raw = (input.value || '').trim();
          var digitsOnly = raw.replace(/[^\d+]/g, '');
          if (digitsOnly.replace(/\+/g, '').length < 10) {
            errEl.textContent = 'Enter at least 10 digits.';
            errEl.style.display = 'block';
            return;
          }
          cleanup(digitsOnly);
        }
        function onCancel() { cleanup(null); }
        function onKey(e) { if (e.key === 'Escape') onCancel(); }

        if (errEl) { errEl.textContent = ''; errEl.style.display = 'none'; }
        if (input) input.value = '';
        form.addEventListener('submit', onSubmit);
        if (cancelBtn) cancelBtn.addEventListener('click', onCancel);
        if (overlay) overlay.addEventListener('click', onCancel);
        document.addEventListener('keydown', onKey);

        modal.classList.add('open');
        document.body.style.overflow = 'hidden';
        if (input) { try { input.focus(); } catch (_) {} }
      });
    };
  }

  async function bootSharedHeader() {
    // Hand the access token to the shared ZKHeader module so it can fetch
    // /api/me + render the user's avatar and dropdown — same as user_home
    // and user_zettels do. Without this call the header renders the empty
    // fallback glyph and the dropdown JS isn't wired.
    if (!window.ZKHeader || typeof window.ZKHeader.boot !== 'function') return;
    if (window.ZKHeader.__booted) return;
    var token = readAuthToken();
    try { await window.ZKHeader.boot(token); } catch (_) { /* non-fatal */ }
  }

  async function loadCatalog() {
    // initSupabase runs before catalog so a refreshable-but-expired access
    // token gets exchanged before refreshCurrentSubscription / first buy click.
    await initSupabase();
    var response = await fetch('/api/pricing/catalog');
    catalog = await response.json();
    // Hand the catalog to purchase_launcher so the next openPurchase call
    // skips its own redundant catalog fetch.
    if (window.ZKPricing) window.ZKPricing.cachedCatalog = catalog;
    // Warm the Razorpay SDK on a microtask so it's resident by the time the
    // user actually clicks Subscribe. preload <link> primes the network
    // cache; this primes the global window.Razorpay function.
    if (window.ZKPricing && typeof window.ZKPricing.loadRazorpayScript === 'function') {
      window.ZKPricing.loadRazorpayScript().catch(function () { /* non-fatal */ });
    }
    await Promise.all([bootSharedHeader(), refreshCurrentSubscription()]);
    renderSubscriptions();
    renderPacks();
  }

  async function refreshCurrentSubscription() {
    if (!window.ZKPricing || typeof window.ZKPricing.fetchMySubscription !== 'function') {
      currentSubscription = null;
      return;
    }
    try {
      var payload = await window.ZKPricing.fetchMySubscription();
      currentSubscription = payload && payload.subscription ? payload.subscription : null;
    } catch (_) {
      currentSubscription = null;
    }
  }

  tabButtons.forEach(function (button) {
    button.addEventListener('click', function () {
      setActive(button.getAttribute('data-tab'));
    });
  });

  document.addEventListener('click', function (event) {
    var periodBtn = event.target.closest('[data-period]');
    if (periodBtn) {
      updateSubscriptionPeriod(periodBtn);
      return;
    }

    var productBtn = event.target.closest('[data-product]');
    if (productBtn && window.ZKPricing) {
      var isChange = productBtn.getAttribute('data-change-sub') === '1';
      if (isChange) {
        var currentLabel = currentSubscription && currentSubscription.plan_id
          ? titleCase(currentSubscription.plan_id) : 'current';
        var targetCard = productBtn.closest('[data-plan-card]');
        var targetLabel = targetCard ? titleCase(targetCard.getAttribute('data-plan-card')) : 'new';
        var ok = window.confirm(
          'This will cancel your current ' + currentLabel + ' plan and start ' + targetLabel + '. ' +
          'You\'ll authorise a new UPI Autopay or card mandate. Continue?'
        );
        if (!ok) return;
      }
      var purchaseIntent = {
        productId: productBtn.getAttribute('data-product'),
        kind: productBtn.getAttribute('data-kind'),
        expectedAmount: parseInt(productBtn.getAttribute('data-amount') || '', 10),
        source: isChange ? 'pricing-page-change' : 'pricing-page',
        changeSubscription: isChange,
        onResume: function () {
          refreshCurrentSubscription().then(renderSubscriptions);
        }
      };
      window.ZKPricing.openPurchase(purchaseIntent).catch(function (err) {
        // Session expiry — pop the inline login modal and queue the buy intent
        // so it auto-resumes after the SIGNED_IN event fires. Previously this
        // either silently rejected (nothing happened) or redirected the user
        // off /pricing entirely.
        if (err && err.status === 401) openLoginModal(purchaseIntent);
      });
      return;
    }

    var cancelBtn = event.target.closest('[data-cancel-sub]');
    if (cancelBtn && window.ZKPricing && typeof window.ZKPricing.cancelMySubscription === 'function') {
      cancelBtn.disabled = true;
      var confirmed = window.confirm('Cancel your active subscription? You will keep access until the end of the current period.');
      if (!confirmed) {
        cancelBtn.disabled = false;
        return;
      }
      window.ZKPricing.cancelMySubscription()
        .then(function () { return refreshCurrentSubscription(); })
        .then(function () { renderSubscriptions(); })
        .catch(function () { /* toast already shown */ })
        .finally(function () { cancelBtn.disabled = false; });
      return;
    }

    var card = event.target.closest('[data-plan-card]');
    if (card) {
      updateSelectedPlan(card.getAttribute('data-plan-card'));
      return;
    }

    var meterBtn = event.target.closest('[data-custom-meter]');
    if (meterBtn) {
      updateCustomMeter(meterBtn.getAttribute('data-custom-meter'));
      return;
    }

    var stepBtn = event.target.closest('[data-step-qty]');
    if (stepBtn) {
      customQuantity = stepQuantity(
        customQuantity,
        parseInt(stepBtn.getAttribute('data-step-qty'), 10),
        sliderSettings(customMeter)
      );
      syncCustomControls();
      return;
    }

  });

  document.addEventListener('input', function (event) {
    if (event.target && event.target.id === 'custom-count-range') {
      customQuantity = quantityForSliderIndex(
        parseInt(event.target.value || '0', 10) || 0,
        sliderSettings(customMeter)
      );
      syncCustomControls();
    }

    if (event.target && event.target.id === 'custom-count-input') {
      customQuantity = normalizeQuantityForInput(
        parseInt(event.target.value || '10', 10) || 10,
        sliderSettings(customMeter)
      );
      syncCustomControls();
    }
  });

  window.addEventListener('resize', syncAllSlidingIndicators);

  installPhoneModalHandler();

  loadCatalog().catch(function () {
    if (subscriptionGrid) subscriptionGrid.innerHTML = '<p class="pricing-error">Pricing could not load. Please refresh.</p>';
  });
})();
