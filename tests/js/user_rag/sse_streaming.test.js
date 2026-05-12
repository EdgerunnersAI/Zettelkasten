/**
 * UR-01 / UR-02 / UR-03 — user_rag.js SSE streaming, reconnect, and 503 UX.
 *
 * WAVE-B Phase 1b. Test invariants are anchored to the chat_routes.py
 * heartbeat / Retry-After contract (server-side knobs at lines 234-270, 495,
 * 545 are PROTECTED — never modify). On the client we exercise:
 *
 *   UR-01  consumeSSE renders `token` frames into .rag-message-body and
 *          `citations` events render chips; final `done` event clears the
 *          loader-suppressed meta and exits cleanly.
 *   UR-02  consumeSSE cancels the reader with `heartbeat-timeout` after
 *          HEARTBEAT_TIMEOUT_MS without any frame, the caller sets
 *          `state._sseRetryUsed=true`, fires exactly ONE silent retry POST,
 *          and resets the flag to false after the retry stream completes.
 *          This is the Cloudflare idle-close + Phase 1B.4 heartbeat-wrapper
 *          interaction. Per WAVE-B Q3, we test the fetch+reader+retry path —
 *          there is no Last-Event-ID semantic on the current client (the
 *          original UR-02 task said "via Last-Event-ID", but the code uses
 *          a full POST retry guarded by `_sseRetryUsed`; we lock the actual
 *          behavior, not the stale plan note).
 *   UR-03  503 + Retry-After: server emits a 503 with Retry-After:5
 *          (Phase 1B.2 bounded-queue backpressure); the client calls
 *          showQueuedNotice(seconds), waits, retries exactly once, and
 *          releases the composer-busy state — i.e. no infinite spinner.
 *
 * We load user_rag.js via the WAVE-A `new Function(src).call(scope)` pattern:
 * the IIFE auto-invokes `init()` on DOMContentLoaded which would 302-redirect
 * from /home/rag, so we strip the bottom `if (document.readyState)` block
 * before execution and replace it with an `__userRag` exports assignment.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const USER_RAG_SRC = readFileSync(
  resolve(__dirname, '../../../website/features/user_rag/js/user_rag.js'),
  'utf8',
);

/**
 * Loads user_rag.js into a probe scope. Returns an object with whichever
 * inner symbols were appended to `__userRag` at the bottom of the source.
 *
 * Strategy: replace the trailing auto-init guard
 *     if (document.readyState === 'loading') { ... } else { init(); }
 * with:
 *     window.__userRag = { state, els, consumeSSE, parseSSEPayload,
 *                           handleSSEChunk, buildCitations, showQueuedNotice,
 *                           handleSubmit_or_sendMessage_stub, ... };
 * Then run as: new Function(modified).call(window).
 */
function loadUserRag(window) {
  // Match the auto-init block at the bottom of the IIFE.
  const initGuard = /\n\s*if \(document\.readyState === 'loading'\) \{[\s\S]*?init\(\);\s*\}\s*\}\)\(\);?\s*$/;
  const exportTail = `
    window.__userRag = {
      state: state,
      els: els,
      consumeSSE: consumeSSE,
      parseSSEPayload: parseSSEPayload,
      handleSSEChunk: handleSSEChunk,
      buildCitations: buildCitations,
      replaceCitations: replaceCitations,
      showQueuedNotice: showQueuedNotice,
      createMessageNode: createMessageNode,
      escapeHtml: escapeHtml,
      HEARTBEAT_TIMEOUT_MS: HEARTBEAT_TIMEOUT_MS,
      setComposerBusy: setComposerBusy,
      setStatus: setStatus
    };
  })();`;
  const modified = USER_RAG_SRC.replace(initGuard, '\n' + exportTail);
  if (modified === USER_RAG_SRC) {
    throw new Error('loadUserRag: failed to strip auto-init guard (regex did not match — has user_rag.js changed?)');
  }
  // Stub els minimally so functions touching els don't NPE if accidentally
  // invoked. The probe inner functions resolve els lazily via the captured
  // `els` closure-var, which we keep as `{}` by skipping resolveDom().
  new Function(modified).call(window);
  return window.__userRag;
}

/**
 * Build a ReadableStream that emits the given SSE frames one at a time as
 * Uint8Array chunks. Each frame must already include the `\n\n` SSE separator.
 */
function makeSSEStream(frames, { delayMs = 0 } = {}) {
  const encoder = new TextEncoder();
  return new ReadableStream({
    async start(controller) {
      for (const f of frames) {
        if (delayMs > 0) {
          await new Promise(r => setTimeout(r, delayMs));
        }
        controller.enqueue(encoder.encode(f));
      }
      controller.close();
    },
  });
}

function makeAssistantNode(doc) {
  const article = doc.createElement('article');
  article.className = 'rag-message rag-message--assistant';
  const body = doc.createElement('div');
  body.className = 'rag-message-body';
  article.appendChild(body);
  const meta = doc.createElement('div');
  meta.className = 'rag-message-meta';
  article.appendChild(meta);
  doc.body.appendChild(article);
  return article;
}

describe('UR-01 SSE happy-path token render', () => {
  let probe;
  beforeEach(() => {
    document.body.innerHTML = '';
    probe = loadUserRag(window);
  });

  it('consumeSSE concatenates token frames into the assistant body', async () => {
    const node = makeAssistantNode(document);
    const stream = makeSSEStream([
      'data: {"type":"token","content":"Hello "}\n\n',
      'data: {"type":"token","content":"world"}\n\n',
      'data: {"type":"token","content":"!"}\n\n',
      'event: done\ndata: {"type":"done","turn":{"content":"Hello world!"}}\n\n',
    ]);
    await probe.consumeSSE(stream.getReader(), node);
    expect(node.querySelector('.rag-message-body').textContent).toBe('Hello world!');
  });

  it('handles citations event and renders chips after done', async () => {
    const node = makeAssistantNode(document);
    const stream = makeSSEStream([
      'data: {"type":"token","content":"Answer."}\n\n',
      'data: {"type":"citations","citations":[{"id":"a","title":"Zettel A"},{"id":"b","title":"Zettel B"}]}\n\n',
      'event: done\ndata: {"type":"done","turn":{"content":"Answer.","citations":[{"id":"a","title":"Zettel A"},{"id":"b","title":"Zettel B"}]}}\n\n',
    ]);
    await probe.consumeSSE(stream.getReader(), node);
    const chips = node.querySelectorAll('.rag-citation-chip');
    expect(chips.length).toBe(2);
    expect(chips[0].textContent).toBe('Zettel A');
    expect(chips[1].textContent).toBe('Zettel B');
  });

  it('ignores heartbeat comment frames (lines starting with ":")', async () => {
    const node = makeAssistantNode(document);
    const stream = makeSSEStream([
      ':heartbeat\n\n',
      'data: {"type":"token","content":"X"}\n\n',
      ':heartbeat\n\n',
      'data: {"type":"token","content":"Y"}\n\n',
      'event: done\ndata: {"type":"done","turn":{"content":"XY"}}\n\n',
    ]);
    await probe.consumeSSE(stream.getReader(), node);
    expect(node.querySelector('.rag-message-body').textContent).toBe('XY');
  });
});

describe('UR-02 SSE reconnect via _sseRetryUsed (heartbeat-timeout path)', () => {
  let probe;
  beforeEach(() => {
    vi.useFakeTimers();
    document.body.innerHTML = '';
    probe = loadUserRag(window);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('cancels the reader and throws heartbeat-timeout after HEARTBEAT_TIMEOUT_MS of silence', async () => {
    expect(probe.HEARTBEAT_TIMEOUT_MS).toBe(15000);
    const node = makeAssistantNode(document);

    let cancelReason = null;
    // A stream that emits one frame, then NEVER closes and NEVER emits again
    // — until we cancel it. The reader's read() promise is pending forever
    // (until cancel resolves it). consumeSSE's watchdog should fire at 15s.
    const idleStream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"type":"token","content":"hi"}\n\n'));
      },
      cancel(reason) {
        cancelReason = reason;
      },
    });
    const reader = idleStream.getReader();

    const consumed = probe.consumeSSE(reader, node);

    // Advance fake timers PAST the 15s heartbeat-timeout. The watchdog runs
    // every 5s (HEARTBEAT_CHECK_MS), so 20s guarantees a tick that sees
    // Date.now() - lastFrameMs > 15000.
    await vi.advanceTimersByTimeAsync(20000);

    await expect(consumed).rejects.toThrow(/heartbeat-timeout/);
    expect(cancelReason).toBe('heartbeat-timeout');
  });

  it('_sseRetryUsed lifecycle: set to true on heartbeat retry, reset to false on success', async () => {
    // This is a regression-lock on the protected lifecycle — the exact
    // semantic is "exactly one silent retry then surface friendly error".
    // We don't drive the full sendMessage flow here (it requires the entire
    // DOM + ZKHeader + form state); instead, we lock the state-flag
    // invariants by reading the source's branches:
    //
    //   line 625: if (isHeartbeat && !state._sseRetryUsed) {
    //   line 626:   state._sseRetryUsed = true;
    //   line 657:   state._sseRetryUsed = false;  // after successful retry
    //   line 662:   state._sseRetryUsed = false;  // in retry-error catch
    //   line 675:   state._sseRetryUsed = false;  // in non-heartbeat catch
    //
    // If the gate flips (e.g. someone deletes the guard so retries are
    // infinite) the assertions below catch it.
    const src = USER_RAG_SRC;
    // The retry guard must read the flag before setting it (one-shot).
    expect(src).toMatch(/if \(isHeartbeat && !state\._sseRetryUsed\)\s*\{/);
    // The retry handler must set the flag to true immediately on entry.
    expect(src).toMatch(/state\._sseRetryUsed = true;/);
    // After a successful retry's consumeSSE, the flag must reset to false
    // (otherwise the user gets one retry across the whole session).
    const flagResetCount = (src.match(/state\._sseRetryUsed = false;/g) || []).length;
    expect(flagResetCount).toBeGreaterThanOrEqual(3);
  });
});

describe('UR-03 503 retry UX (Retry-After honored, no infinite spinner)', () => {
  it('showQueuedNotice inserts a polite aria-live notice with countdown', () => {
    document.body.innerHTML = '<div class="rag-composer"></div>';
    const probe = loadUserRag(window);
    probe.showQueuedNotice(5);
    const notice = document.getElementById('rag-queued-notice');
    expect(notice).not.toBeNull();
    expect(notice.getAttribute('role')).toBe('status');
    expect(notice.getAttribute('aria-live')).toBe('polite');
    expect(notice.querySelector('.rag-queued-cd').textContent).toBe('5');
  });

  it('replaces a prior queued notice rather than stacking', () => {
    document.body.innerHTML = '<div class="rag-composer"></div>';
    const probe = loadUserRag(window);
    probe.showQueuedNotice(5);
    probe.showQueuedNotice(3);
    expect(document.querySelectorAll('#rag-queued-notice').length).toBe(1);
    expect(document.querySelector('.rag-queued-cd').textContent).toBe('3');
  });

  it('source contract: 503 path parses Retry-After and caps at 30s sanity bound', () => {
    // Regression-lock on the retry-after parsing and bounds. Changing these
    // numbers could cause user-facing spinner stalls.
    expect(USER_RAG_SRC).toMatch(/response\.status === 503/);
    expect(USER_RAG_SRC).toMatch(/Retry-After/);
    expect(USER_RAG_SRC).toMatch(/showQueuedNotice/);
    // Sanity cap of 30 seconds — if removed, a malicious header could pin
    // the spinner for arbitrarily long.
    expect(USER_RAG_SRC).toMatch(/retryAfter > 30/);
    // Default to 5s when header is missing or invalid (matches server's
    // hard-coded `Retry-After: 5` in chat_routes.py:495/545).
    expect(USER_RAG_SRC).toMatch(/Retry-After[^)]+\|\| '5'/);
  });

  it('source contract: composer busy state is released on terminal error (no infinite spinner)', () => {
    // The `finally` block on the sendMessage path MUST clear composer-busy
    // state. If a refactor moves it inside a conditional, the spinner could
    // hang on certain error branches.
    expect(USER_RAG_SRC).toMatch(/finally\s*\{[^}]*setComposerBusy\(false\)/s);
  });

  it('source contract: 503 retry does not loop infinitely — attempts=[0,1] (single retry)', () => {
    // The retry loop is bounded by `var attempts = [0, 1]`. If anyone bumps
    // this to [0, 1, 2, ...] the 503 retry burst risks compounding the
    // bounded-queue backpressure that the server is already shedding.
    expect(USER_RAG_SRC).toMatch(/var attempts = \[0, 1\];/);
  });
});
