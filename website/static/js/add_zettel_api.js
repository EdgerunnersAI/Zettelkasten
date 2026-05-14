(function () {
  'use strict';

  function makeActionId(surface) {
    return 'zettel:' + (surface || 'unknown') + ':' + Date.now() + ':' + Math.random().toString(36).slice(2);
  }

  function cleanProblemDetail(body, fallback) {
    if (!body || typeof body !== 'object') return fallback;
    if (body.detail && typeof body.detail === 'object') return body.detail.message || body.detail.detail || fallback;
    return body.detail || body.title || body.message || fallback;
  }

  async function parseResponse(response) {
    var contentType = (response.headers.get('content-type') || '').toLowerCase();
    if (contentType.indexOf('application/json') !== -1 || contentType.indexOf('application/problem+json') !== -1) {
      return response.json();
    }
    var text = await response.text();
    var message = text ? text.slice(0, 220) : 'Server returned an empty non-JSON response.';
    throw new Error('Server returned non-JSON response (HTTP ' + response.status + '): ' + message);
  }

  function sleep(ms) {
    return new Promise(function (resolve) { window.setTimeout(resolve, ms); });
  }

  async function fetchStatus(statusUrl, headers) {
    var response = await fetch(statusUrl, { headers: headers });
    var body = await parseResponse(response);
    if (!response.ok) {
      var error = new Error(cleanProblemDetail(body, 'Status check failed with status ' + response.status));
      error.status = response.status;
      error.detail = body && (body.detail || body.error || body);
      error.problem = body;
      throw error;
    }
    return body;
  }

  async function pollAccepted(body, headers) {
    if (!body || body.status !== 'accepted' || !body.status_url) return body;
    var attempts = 10;
    for (var i = 0; i < attempts; i += 1) {
      await sleep(i < 2 ? 1200 : 3000);
      var next = await fetchStatus(body.status_url, headers);
      if (next && next.status !== 'accepted') return next;
    }
    var error = new Error('Summary is still processing. Please check My Zettels in a moment.');
    error.status = 202;
    error.detail = body;
    error.problem = body;
    throw error;
  }

  async function add(options) {
    var opts = options || {};
    var token = opts.token || '';
    var headers = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = 'Bearer ' + token;

    var response = await fetch('/api/zettels/add', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({
        url: opts.url,
        client_action_id: opts.clientActionId || makeActionId(opts.surface),
        persist: opts.persist !== false,
        surface: opts.surface || 'landing',
        mode: opts.mode || 'auto'
      })
    });

    var body = await parseResponse(response);
    if (!response.ok) {
      var error = new Error(cleanProblemDetail(body, 'Request failed with status ' + response.status));
      error.status = response.status;
      error.detail = body && (body.detail || body.error || body);
      error.problem = body;
      throw error;
    }
    return pollAccepted(body, headers);
  }

  window.ZKAddZettel = {
    add: add,
    makeActionId: makeActionId,
    _parseResponse: parseResponse
  };
})();
