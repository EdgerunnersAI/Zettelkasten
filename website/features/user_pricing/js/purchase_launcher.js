(function () {
  'use strict';

  var RAZORPAY_SCRIPT_URL = 'https://checkout.razorpay.com/v1/checkout.js';
  var razorpayScriptPromise = null;

  function hasQuotaDetail(detail) {
    return Boolean(detail && detail.code === 'quota_exhausted' && detail.meter);
  }

  function authToken() {
    if (typeof window.getAuthToken === 'function') return window.getAuthToken();
    try {
      var raw = window.localStorage && window.localStorage.getItem('zk-auth-token');
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      return parsed && parsed.access_token ? parsed.access_token : null;
    } catch (_) {
      return null;
    }
  }

  function authHeaders() {
    var token = authToken();
    return token ? { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
  }

  async function fetchJson(path, options) {
    options = options || {};
    options.headers = Object.assign({}, options.headers || {});
    var response = await fetch(path, options);
    var payload = null;
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) {
      var detail = payload && payload.detail ? payload.detail : payload;
      var err = new Error((detail && detail.message) || 'Request failed');
      err.status = response.status;
      err.detail = detail;
      throw err;
    }
    return payload;
  }

  function loadRazorpayScript() {
    if (typeof window.Razorpay === 'function') return Promise.resolve();
    if (razorpayScriptPromise) return razorpayScriptPromise;
    razorpayScriptPromise = new Promise(function (resolve, reject) {
      var existing = document.querySelector('script[data-zk-razorpay]');
      if (existing) {
        existing.addEventListener('load', function () { resolve(); });
        existing.addEventListener('error', function () { reject(new Error('Razorpay checkout failed to load.')); });
        return;
      }
      var script = document.createElement('script');
      script.src = RAZORPAY_SCRIPT_URL;
      script.async = true;
      script.setAttribute('data-zk-razorpay', '1');
      script.onload = function () { resolve(); };
      script.onerror = function () { reject(new Error('Razorpay checkout failed to load.')); };
      document.head.appendChild(script);
    });
    return razorpayScriptPromise;
  }

  function showToast(message, kind) {
    try {
      window.dispatchEvent(new CustomEvent('zk:pricing:toast', {
        detail: { message: message, kind: kind || 'info' }
      }));
    } catch (_) { /* noop */ }
    if (kind === 'error') {
      console.error('[pricing]', message);
    } else {
      console.info('[pricing]', message);
    }
  }

  function findCatalogProduct(catalog, productId) {
    var plans = catalog && catalog.plans ? catalog.plans : {};
    for (var planId in plans) {
      var periods = plans[planId].periods || {};
      for (var periodId in periods) {
        if (periods[periodId].id === productId) return { kind: 'subscription', amount: periods[periodId].amount, product: periods[periodId] };
      }
    }
    var packs = catalog && catalog.packs ? catalog.packs : {};
    for (var group in packs) {
      for (var i = 0; i < packs[group].length; i++) {
        if (packs[group][i].id === productId) return { kind: 'pack', amount: packs[group][i].amount, product: packs[group][i] };
      }
    }
    return null;
  }

  async function ensureBillingProfile() {
    var token = authToken();
    if (!token) {
      // Don't navigate here — surface a 401 so the caller can decide whether
      // to pop an inline login modal (pricing page) or do its own redirect.
      var err = new Error('Please sign in to continue checkout.');
      err.status = 401;
      err.code = 'not_authenticated';
      throw err;
    }

    var profilePayload = await fetchJson('/api/pricing/billing-profile', { headers: authHeaders() });
    var profile = profilePayload && profilePayload.profile;
    if (profile && profile.phone) return profile;

    // Pages can install window.ZKPricing.promptForPhone to capture the phone
    // via a styled inline modal; we fall back to window.prompt only when no
    // page has installed a custom collector.
    var phone;
    if (typeof window.ZKPricing.promptForPhone === 'function') {
      phone = await window.ZKPricing.promptForPhone();
    } else {
      phone = window.prompt('Enter your phone number for secure checkout');
    }
    if (!phone) throw new Error('Phone number is required for checkout.');
    return await fetchJson('/api/pricing/billing-profile', {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify({ phone: phone })
    });
  }

  function chooseProductId(options, catalog) {
    if (options.productId) return options.productId;
    var detail = options.detail || {};
    var recommended = options.recommendedProducts || detail.recommended_products || [];
    return recommended[0] || null;
  }

  async function createCheckout(options, productId, productInfo) {
    var isChange = !!options.changeSubscription;
    var endpoint = isChange
      ? '/api/payments/subscriptions/change'
      : (productInfo.kind === 'subscription' ? '/api/payments/subscriptions' : '/api/payments/orders');
    var expectedAmount = Number.isFinite(options.expectedAmount) ? options.expectedAmount : productInfo.amount;
    var body = isChange
      ? { to_product_id: productId, expected_amount: expectedAmount }
      : {
          product_id: productId,
          source: options.source || 'unknown',
          expected_amount: expectedAmount,
          resume_token: options.detail && options.detail.resume_token ? options.detail.resume_token : null
        };
    return await fetchJson(endpoint, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(body)
    });
  }

  async function verifyPayment(payload) {
    return await fetchJson('/api/payments/orders/verify', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(payload)
    });
  }

  function openRazorpayCheckout(checkoutPayload) {
    return new Promise(function (resolve, reject) {
      if (!checkoutPayload || !checkoutPayload.key_id || (!checkoutPayload.order_id && !checkoutPayload.subscription_id)) {
        reject(new Error('Checkout configuration is incomplete.'));
        return;
      }
      var settled = false;
      var theme = checkoutPayload.theme || {};
      // Match the site's deep dark backdrop instead of Razorpay's default black —
      // gives the modal a seamless overlay over zettelkasten.in.
      if (!theme.color) theme.color = '#0d9488';
      if (!theme.backdrop_color) theme.backdrop_color = '#0a0f14';
      // For subscriptions Razorpay routes UPI through Autopay (e-mandate), which
      // only supports the `collect` sub-flow. For one-time orders we expose
      // QR + UPI ID + UPI Apps as switchable flows inside one curated block.
      // Razorpay docs:
      //   razorpay.com/docs/payments/subscriptions/upi-autopay
      //   razorpay.com/docs/payments/payment-gateway/web-integration/standard/payment-methods/upi/
      var isRecurring = !!checkoutPayload.subscription_id;
      var options = {
        key: checkoutPayload.key_id,
        amount: checkoutPayload.amount,
        currency: checkoutPayload.currency || 'INR',
        name: checkoutPayload.name || 'Zettelkasten',
        description: checkoutPayload.description || '',
        // Logo at top-left of the modal. Razorpay accepts an HTTPS URL.
        image: (checkoutPayload.image && /^https?:/.test(checkoutPayload.image))
          ? checkoutPayload.image
          : (location.origin + (checkoutPayload.image || '/artifacts/company_logo.svg')),
        // Pre-select UPI as the default payment method on open — saves the
        // user one sidebar click. Razorpay honours `prefill.method` when the
        // method is enabled on the merchant account.
        prefill: Object.assign({ method: 'upi' }, checkoutPayload.prefill || {}),
        notes: checkoutPayload.notes || {},
        theme: theme,
        // Method curation:
        //   • Subscriptions   → UPI-Autopay (collect) + recurring card mandate
        //     only; intent/qr/netbanking aren't autopay-eligible.
        //   • One-time orders → no override. Razorpay's default block already
        //     auto-selects UPI on open and renders QR + UPI ID + Apps as
        //     integrated tabs (industry-standard, fewest clicks). We only
        //     `hide` Pay-Later + EMI to keep the list to UPI/Card/Netbanking/
        //     Wallet/More-options, then `prefill.method='upi'` lands the user
        //     directly on UPI without a sidebar click.
        config: isRecurring ? {
          display: {
            blocks: {
              upi: {
                name: 'Pay with UPI Autopay',
                instruments: [{ method: 'upi', flows: ['collect'] }]
              },
              cards: {
                name: 'Pay with Card (auto-debit)',
                instruments: [{ method: 'card' }]
              }
            },
            sequence: ['block.upi', 'block.cards'],
            preferences: { show_default_blocks: false }
          }
        } : {
          display: {
            // Razorpay-recommended config post 28 Feb 2026 NPCI deprecation
            // of UPI Collect: hide the collect sub-flow explicitly so the
            // checkout doesn't render a non-functional VPA textbox. UPI QR
            // (desktop) and UPI Intent (mobile) remain. Pay-Later + EMI
            // hidden to keep the sidebar tight.
            // Razorpay docs:
            //   /docs/announcements/upi-collect-migration/standard-integration/
            hide: [
              { method: 'upi', flows: ['collect'] },
              { method: 'paylater' },
              { method: 'emi' }
            ]
          }
        },
        // Stop Razorpay from auto-retrying via SMS bank-OTP fill (faster real
        // payments) and let the modal show its own retry button on transient
        // failures so the user doesn't restart the whole flow.
        retry: { enabled: true, max_count: 3 },
        send_sms_hash: true,
        // Pre-check the "save card" box so RBI-compliant tokenised recurring
        // billing works on first try without an extra click.
        remember_customer: true,
        modal: {
          // Don't dismiss on accidental backdrop / Esc — confirm first so a
          // half-filled form isn't lost.
          backdropclose: false,
          escape: false,
          confirm_close: true,
          ondismiss: function () {
            if (settled) return;
            settled = true;
            reject(Object.assign(new Error('Checkout cancelled.'), { code: 'dismissed' }));
          }
        },
        handler: function (response) {
          if (settled) return;
          settled = true;
          resolve(response);
        }
      };
      if (checkoutPayload.subscription_id) {
        options.subscription_id = checkoutPayload.subscription_id;
        options.recurring = '1';
      } else {
        options.order_id = checkoutPayload.order_id;
      }
      var rzp;
      try {
        rzp = new window.Razorpay(options);
      } catch (err) {
        reject(err);
        return;
      }
      if (rzp && typeof rzp.on === 'function') {
        rzp.on('payment.failed', function (resp) {
          if (settled) return;
          settled = true;
          var description = resp && resp.error && resp.error.description ? resp.error.description : 'Payment failed.';
          reject(Object.assign(new Error(description), { code: 'payment_failed', source: resp }));
        });
      }
      rzp.open();
    });
  }

  async function openPurchase(options) {
    options = options || {};
    var detail = options.detail || {};
    var resumeAction = options.resumeAction || detail.resumeAction || null;

    window.dispatchEvent(new CustomEvent('zk:pricing:open', {
      detail: {
        meter: options.meter || detail.meter,
        source: options.source || 'unknown',
        recommendedProducts: options.recommendedProducts || detail.recommended_products || [],
        resumeAction: resumeAction
      }
    }));

    window.ZKPricing.pendingResume = {
      resumeAction: resumeAction,
      onResume: typeof options.onResume === 'function' ? options.onResume : null
    };

    // Reuse the catalog that pricing.js already fetched on page load if
    // available — saves one round-trip on every click.
    var catalog = (window.ZKPricing && window.ZKPricing.cachedCatalog)
      ? window.ZKPricing.cachedCatalog
      : await fetchJson('/api/pricing/catalog');
    var productId = chooseProductId(options, catalog);
    if (!productId) {
      window.location.href = '/pricing';
      return;
    }
    var productInfo = findCatalogProduct(catalog, productId);
    if (!productInfo && /^custom_(zettel|kasten|question)_\d+$/.test(productId) && Number.isFinite(options.expectedAmount)) {
      productInfo = { kind: 'pack', amount: options.expectedAmount };
    }
    if (!productInfo) throw new Error('Selected product is no longer available.');
    if (Number.isFinite(options.expectedAmount) && options.expectedAmount !== productInfo.amount) {
      throw new Error('Displayed price changed. Refresh pricing before checkout.');
    }

    await ensureBillingProfile();

    var checkoutPayload;
    try {
      checkoutPayload = await createCheckout(options, productId, productInfo);
    } catch (err) {
      showToast(err.message || 'Could not start checkout.', 'error');
      throw err;
    }

    try {
      await loadRazorpayScript();
    } catch (err) {
      showToast('Could not load payment provider. Check your connection and retry.', 'error');
      throw err;
    }

    var rzpResponse;
    try {
      rzpResponse = await openRazorpayCheckout(checkoutPayload);
    } catch (err) {
      if (err && err.code === 'dismissed') {
        showToast('Checkout cancelled.', 'info');
      } else {
        showToast(err.message || 'Payment failed.', 'error');
      }
      throw err;
    }

    var verifyResult;
    try {
      verifyResult = await verifyPayment({
        payment_id: checkoutPayload.payment_id,
        razorpay_payment_id: rzpResponse.razorpay_payment_id,
        razorpay_order_id: rzpResponse.razorpay_order_id || checkoutPayload.order_id || null,
        razorpay_subscription_id: rzpResponse.razorpay_subscription_id || checkoutPayload.subscription_id || null,
        razorpay_signature: rzpResponse.razorpay_signature
      });
    } catch (err) {
      showToast('Payment verification failed. Contact support if you were charged.', 'error');
      throw err;
    }

    showToast('Payment successful — credits applied.', 'success');
    window.dispatchEvent(new CustomEvent('zk:pricing:paid', {
      detail: {
        payment: verifyResult && verifyResult.payment,
        productId: productId,
        kind: productInfo.kind
      }
    }));

    await resumePendingPurchase();
    return verifyResult;
  }

  async function resumePendingPurchase() {
    var pending = window.ZKPricing && window.ZKPricing.pendingResume;
    if (pending && typeof pending.onResume === 'function') {
      await pending.onResume(pending.resumeAction);
      window.ZKPricing.pendingResume = null;
    }
  }

  async function fetchMySubscription() {
    var token = authToken();
    if (!token) return { subscription: null };
    try {
      return await fetchJson('/api/payments/subscriptions/me', { headers: authHeaders() });
    } catch (err) {
      if (err && err.status === 401) return { subscription: null };
      throw err;
    }
  }

  async function cancelMySubscription() {
    var token = authToken();
    if (!token) throw new Error('Please sign in to cancel.');
    try {
      var result = await fetchJson('/api/payments/subscriptions/cancel', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ cancel_at_cycle_end: false })
      });
      showToast('Subscription cancelled.', 'success');
      window.dispatchEvent(new CustomEvent('zk:pricing:cancelled', { detail: result }));
      return result;
    } catch (err) {
      showToast(err.message || 'Could not cancel subscription.', 'error');
      throw err;
    }
  }

  window.ZKPricing = window.ZKPricing || {};
  window.ZKPricing.fetchJson = fetchJson;
  window.ZKPricing.hasQuotaDetail = hasQuotaDetail;
  window.ZKPricing.openPurchase = openPurchase;
  window.ZKPricing.resumePendingPurchase = resumePendingPurchase;
  window.ZKPricing.loadRazorpayScript = loadRazorpayScript;
  window.ZKPricing.fetchMySubscription = fetchMySubscription;
  window.ZKPricing.cancelMySubscription = cancelMySubscription;
})();
