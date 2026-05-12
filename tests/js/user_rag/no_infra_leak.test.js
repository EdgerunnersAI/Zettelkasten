/**
 * UR-06 — No infrastructure leak in the RAG chat DOM.
 *
 * CLAUDE.md hard rule (feedback_no_infra_disclosure): "Never expose model
 * name, tokens, latency, scores, query_class etc. in user-facing UI."
 *
 * This is enforced two ways in user_rag.js:
 *
 *   1. STATIC: the `done` handler at line 802-806 explicitly clears the
 *      .rag-message-meta element's textContent — even though the CSS hides
 *      it, the rule is "don't write the data in the first place" so a future
 *      CSS regression cannot leak.
 *   2. RUNTIME: when a `done` event arrives with model/tier/scores/query_class
 *      fields inside `turn`, those strings must NEVER appear anywhere in the
 *      assistant message DOM.
 *
 * Anchored to website/features/user_rag/js/user_rag.js:789-822.
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
      consumeSSE: consumeSSE,
      handleSSEChunk: handleSSEChunk,
      replaceCitations: replaceCitations
    };
  })();`;
  const modified = USER_RAG_SRC.replace(initGuard, '\n' + exportTail);
  if (modified === USER_RAG_SRC) {
    throw new Error('loadUserRag: regex did not match — has user_rag.js changed?');
  }
  new Function(modified).call(window);
  return window.__userRag;
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

// The forbidden-tokens list. If any of these strings appears in the rendered
// DOM after a streaming turn completes, the test fails. Tokens drawn from
// CLAUDE.md, model registry, and feedback memo `no_infra_disclosure`.
const FORBIDDEN_DOM_TOKENS = [
  'gemini-2.5-flash',
  'gemini-2.5-flash-lite',
  'bge-large',
  'cross-encoder',
  'rerank_score',
  'similarity_score',
  'query_class',
  'tokens_used',
  'latency_ms',
  'critic_score',
  'embedding_model',
  'model_tier',
];

describe('UR-06 no infra leak — static source rules', () => {
  it('done handler explicitly clears .rag-message-meta textContent', () => {
    // The deliberate clear is the safety belt — even if a future meta
    // payload contains scores, this line wipes them before the user sees.
    expect(USER_RAG_SRC).toMatch(
      /assistantNode\.querySelector\('\.rag-message-meta'\)\.textContent = '';/,
    );
  });

  it('user_rag.js does NOT contain user-facing model-name strings', () => {
    // The file should not bake model names into rendered text. Comments
    // mentioning models are allowed (we strip /* ... */ and // ... before
    // checking), but actual string literals like "'gemini-2.5-flash'"
    // would suggest the UI displays the model — forbidden.
    const stripped = USER_RAG_SRC
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/\/\/[^\n]*/g, '');
    const modelNamePatterns = [
      /['"`]gemini-2\.5-(?:flash|pro)/i,
      /['"`]claude-(?:sonnet|opus|haiku)/i,
      /['"`]bge-large/i,
    ];
    for (const pat of modelNamePatterns) {
      expect(stripped, `model name string found via ${pat}`).not.toMatch(pat);
    }
  });

  it('user_rag.js does NOT render score / query_class fields into the DOM', () => {
    // Search for any pattern like turn.scores, turn.query_class,
    // turn.rerank_score etc. that flows into textContent/innerHTML. The
    // `turn` object IS allowed to contain those fields (server may send
    // them for future debug-panel support), but they must never be written
    // to visible DOM.
    const renderSinks = [
      /textContent\s*=\s*[^;]*turn\.(?:scores|query_class|rerank_score|similarity_score|tokens_used|latency_ms|model|critic_score)/,
      /innerHTML\s*=\s*[^;]*turn\.(?:scores|query_class|rerank_score|similarity_score|tokens_used|latency_ms|model|critic_score)/,
    ];
    for (const pat of renderSinks) {
      expect(USER_RAG_SRC, `forbidden DOM render sink ${pat}`).not.toMatch(pat);
    }
  });

  it('handleSSEChunk done branch only renders turn.content + sanitized citations + verdict badge', () => {
    // The intended render surface of `done` is: turn.content,
    // turn.citations (rendered as chips), turn.critic_verdict (badge).
    // No other fields. Lock the structure.
    const doneBranch = USER_RAG_SRC.match(
      /if \(payload\.type === 'done'\) \{[\s\S]*?return;\s*\}/,
    );
    expect(doneBranch).not.toBeNull();
    const body = doneBranch[0];
    expect(body).toMatch(/turn\.content/);
    expect(body).toMatch(/turn\.citations/);
    // critic_verdict is rendered as an emoji badge, not the raw score.
    expect(body).toMatch(/turn\.critic_verdict/);
    // Forbidden fields must not appear in the done branch body.
    expect(body).not.toMatch(/turn\.scores/);
    expect(body).not.toMatch(/turn\.query_class/);
    expect(body).not.toMatch(/turn\.tokens_used/);
    expect(body).not.toMatch(/turn\.latency_ms/);
    expect(body).not.toMatch(/turn\.model[^_]/);
  });
});

describe('UR-06 no infra leak — runtime DOM scan after streamed done', () => {
  let probe;
  beforeEach(() => {
    document.body.innerHTML = '';
    probe = loadUserRag(window);
  });

  it('strips meta when turn payload contains model name', async () => {
    // Server is misbehaving — including model in turn. The client must
    // still scrub it. Even if a future server change starts sending model
    // metadata, the client is the last-line defence.
    const node = makeAssistantNode(document);
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(c) {
        c.enqueue(encoder.encode('data: {"type":"token","content":"clean answer"}\n\n'));
        c.enqueue(encoder.encode(
          'event: done\ndata: {"type":"done","turn":{' +
            '"content":"clean answer",' +
            '"model":"gemini-2.5-flash",' +
            '"tokens_used":1234,' +
            '"latency_ms":456,' +
            '"query_class":"multi_hop",' +
            '"rerank_score":0.87,' +
            '"similarity_score":0.91,' +
            '"critic_score":0.82' +
            '}}\n\n',
        ));
        c.close();
      },
    });
    await probe.consumeSSE(stream.getReader(), node);

    // The user-visible body should ONLY be "clean answer". The meta div is
    // explicitly cleared. Critically — none of the forbidden tokens appear
    // anywhere in the article subtree.
    const body = node.querySelector('.rag-message-body');
    const meta = node.querySelector('.rag-message-meta');
    expect(body.textContent).toBe('clean answer');
    expect(meta.textContent).toBe('');

    const fullDomText = node.textContent || '';
    for (const tok of FORBIDDEN_DOM_TOKENS) {
      expect(fullDomText, `infra leak: token "${tok}" rendered in DOM`).not.toContain(tok);
    }
    // Also scan attributes (data-* or aria-label could leak too).
    const allEls = node.querySelectorAll('*');
    allEls.forEach((el) => {
      for (const attr of el.getAttributeNames()) {
        const val = el.getAttribute(attr) || '';
        for (const tok of FORBIDDEN_DOM_TOKENS) {
          expect(val, `infra leak in attr ${attr}=${val}`).not.toContain(tok);
        }
      }
    });
  });

  it('renders critic_verdict badge (allowed) without exposing critic_score (forbidden)', async () => {
    const node = makeAssistantNode(document);
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(c) {
        c.enqueue(encoder.encode(
          'event: done\ndata: {"type":"done","turn":{' +
            '"content":"answer",' +
            '"critic_verdict":"green",' +
            '"critic_score":0.95' +
            '}}\n\n',
        ));
        c.close();
      },
    });
    await probe.consumeSSE(stream.getReader(), node);

    // The verdict badge is OK; the numeric score is NOT.
    const text = node.textContent || '';
    expect(text).not.toContain('0.95');
    expect(text).not.toContain('critic_score');
  });
});
