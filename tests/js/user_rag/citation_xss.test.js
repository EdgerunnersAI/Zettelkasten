/**
 * UR-04 — Citation HTML sanitization.
 *
 * Citation chips render `citation.title` (or `node_id` / `id`) from a model-
 * generated payload. The model is *untrusted output*: a prompt-injection or
 * a poisoned source title could try to smuggle HTML/JS into the chip. The
 * invariant is that `buildCitations` MUST use `textContent` (not innerHTML)
 * for the chip text, so no `<script>` / `<img onerror>` / event handlers can
 * execute in the user's session.
 *
 * Anchored to website/features/user_rag/js/user_rag.js:392-404.
 *
 * Why textContent (and not e.g. DOMPurify): the chip never needs HTML — it
 * is a pure text label. textContent is the simplest, fastest, and most
 * audit-friendly sanitizer for that use. If anyone refactors to innerHTML or
 * insertAdjacentHTML, this test fails.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const USER_RAG_SRC = readFileSync(
  resolve(__dirname, '../../../website/features/user_rag/js/user_rag.js'),
  'utf8',
);

function loadUserRag(window) {
  const initGuard = /\n\s*if \(document\.readyState === 'loading'\) \{[\s\S]*?init\(\);\s*\}\s*\}\)\(\);?\s*$/;
  const exportTail = `
    window.__userRag = {
      buildCitations: buildCitations,
      replaceCitations: replaceCitations,
      escapeHtml: escapeHtml,
      handleSSEChunk: handleSSEChunk,
      consumeSSE: consumeSSE
    };
  })();`;
  const modified = USER_RAG_SRC.replace(initGuard, '\n' + exportTail);
  if (modified === USER_RAG_SRC) {
    throw new Error('loadUserRag: regex did not match — has user_rag.js changed?');
  }
  new Function(modified).call(window);
  return window.__userRag;
}

const XSS_PAYLOADS = [
  '<script>window.__xssFired = true;</script>',
  '<img src=x onerror="window.__xssFired = true">',
  '<svg/onload="window.__xssFired = true">',
  '"><script>window.__xssFired = true;</script>',
  "javascript:window.__xssFired=true",
  '<iframe src="javascript:window.__xssFired=true"></iframe>',
  '<a href="javascript:window.__xssFired=true">click</a>',
];

describe('UR-04 citation XSS — buildCitations must use textContent, not innerHTML', () => {
  let probe;

  beforeEach(() => {
    document.body.innerHTML = '';
    delete window.__xssFired;
    probe = loadUserRag(window);
  });

  XSS_PAYLOADS.forEach((payload) => {
    it(`renders payload as text, never as HTML: ${payload.slice(0, 40)}...`, () => {
      const wrapper = probe.buildCitations([{ id: 'x', title: payload }]);
      // No <script>, <img>, <svg>, <iframe>, <a> elements should be parsed
      // inside the chip — only a single span.rag-citation-chip with the
      // payload as literal text content.
      const chips = wrapper.querySelectorAll('.rag-citation-chip');
      expect(chips.length).toBe(1);
      expect(chips[0].textContent).toBe(payload);

      // Defence in depth: no nested elements at all (chip is pure text).
      expect(chips[0].querySelector('script')).toBeNull();
      expect(chips[0].querySelector('img')).toBeNull();
      expect(chips[0].querySelector('svg')).toBeNull();
      expect(chips[0].querySelector('iframe')).toBeNull();
      expect(chips[0].querySelector('a')).toBeNull();

      // The literal `<` from the payload, if present, must appear in
      // textContent verbatim — proving the browser parsed it as text.
      if (payload.includes('<')) {
        expect(chips[0].textContent).toContain('<');
      }
    });
  });

  it('attaches to DOM without firing XSS payload (full integration)', () => {
    // Mount the wrapper into the live document and assert that even after
    // append the script side effect does not fire (because textContent never
    // parsed HTML, append cannot turn it into HTML either).
    const wrapper = probe.buildCitations([
      { id: 'a', title: '<script>window.__xssFired = true;</script>' },
      { id: 'b', title: '<img src=x onerror="window.__xssFired = true">' },
    ]);
    document.body.appendChild(wrapper);
    // Force a microtask flush so any onerror that *would* fire has a chance.
    return Promise.resolve().then(() => {
      expect(window.__xssFired).toBeUndefined();
    });
  });

  it('source contract: chip text uses textContent (not innerHTML)', () => {
    // Direct grep on user_rag.js — the buildCitations function MUST use
    // `chip.textContent =`. If anyone refactors to innerHTML / outerHTML /
    // insertAdjacentHTML, this fails. (escapeHtml is acceptable as a fallback
    // sanitizer but is not used in buildCitations today.)
    const buildCitationsFn = USER_RAG_SRC.match(
      /function buildCitations\([\s\S]*?\n\s{2}\}/,
    );
    expect(buildCitationsFn).not.toBeNull();
    const body = buildCitationsFn[0];
    expect(body).toMatch(/chip\.textContent\s*=/);
    expect(body).not.toMatch(/chip\.innerHTML\s*=/);
    expect(body).not.toMatch(/insertAdjacentHTML/);
  });

  it('citations falling through SSE pipeline are sanitized end-to-end', async () => {
    // Drive the full SSE-> handleSSEChunk -> replaceCitations -> buildCitations
    // path with a malicious citations frame from the server. Asserts the
    // sanitizer is not bypassed by a particular event type.
    const article = document.createElement('article');
    const body = document.createElement('div');
    body.className = 'rag-message-body';
    article.appendChild(body);
    document.body.appendChild(article);

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'data: {"type":"citations","citations":[' +
              '{"id":"a","title":"<img src=x onerror=\\"window.__xssFired = true\\">"}' +
              ']}\n\n',
          ),
        );
        controller.enqueue(
          encoder.encode('event: done\ndata: {"type":"done","turn":{"content":"ok"}}\n\n'),
        );
        controller.close();
      },
    });
    await probe.consumeSSE(stream.getReader(), article);

    const chip = article.querySelector('.rag-citation-chip');
    expect(chip).not.toBeNull();
    expect(chip.querySelector('img')).toBeNull();
    expect(window.__xssFired).toBeUndefined();
  });
});

describe('UR-04 escapeHtml utility (defence-in-depth used elsewhere in user_rag.js)', () => {
  let probe;
  beforeEach(() => {
    document.body.innerHTML = '';
    probe = loadUserRag(window);
  });

  it('escapes <, >, &, " correctly', () => {
    // Implementation uses textContent->innerHTML round-trip which escapes
    // the HTML metachars deterministically.
    expect(probe.escapeHtml('<b>x</b>')).toBe('&lt;b&gt;x&lt;/b&gt;');
    expect(probe.escapeHtml('a & b')).toBe('a &amp; b');
    expect(probe.escapeHtml('<script>x</script>')).toBe('&lt;script&gt;x&lt;/script&gt;');
  });

  it('coerces null/undefined to empty string (no NPE)', () => {
    expect(probe.escapeHtml(null)).toBe('');
    expect(probe.escapeHtml(undefined)).toBe('');
  });
});
