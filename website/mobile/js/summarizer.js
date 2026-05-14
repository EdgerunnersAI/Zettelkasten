/* ═══════════════════════════════════════════════════════════
   Mobile Summarizer — Touch-optimized, minimal JS
   ═══════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const form     = document.getElementById('summarize-form');
  const urlInput = document.getElementById('url-input');
  const srcSel   = document.getElementById('source-select');
  const submitBtn= document.getElementById('submit-btn');
  const loading  = document.getElementById('loading');
  const loadTxt  = document.getElementById('loading-text');
  const errorEl  = document.getElementById('error');
  const result   = document.getElementById('result');
  const copyBtn  = document.getElementById('copy-btn');

  const MESSAGES = [
    'Analyzing content...',
    'Extracting key insights...',
    'Building summary...',
    'Almost ready...',
    'Generating tags...',
    'Finishing up...'
  ];

  let msgIndex = 0;
  let msgTimer = null;
  let rawSummary = '';

  function showLoading() {
    msgIndex = 0;
    loadTxt.textContent = MESSAGES[0];
    loading.classList.add('active');
    result.classList.remove('active');
    errorEl.classList.remove('active');
    submitBtn.disabled = true;
    msgTimer = setInterval(() => {
      msgIndex = (msgIndex + 1) % MESSAGES.length;
      loadTxt.textContent = MESSAGES[msgIndex];
    }, 3000);
  }

  function hideLoading() {
    loading.classList.remove('active');
    submitBtn.disabled = false;
    if (msgTimer) { clearInterval(msgTimer); msgTimer = null; }
  }

  function showError(msg) {
    hideLoading();
    errorEl.textContent = msg;
    errorEl.classList.add('active');
  }

  function markdownToHTML(md) {
    if (!md) return '';
    let html = md;
    // Code blocks
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Headers
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italic
    html = html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
    // Lists
    html = html.replace(/^[\s]*[-•*]\s+(.+)$/gm, '<li>$1</li>');
    html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');
    // Paragraphs
    html = html.replace(/\n\n/g, '</p><p>');
    html = '<p>' + html + '</p>';
    html = html.replace(/<p>\s*<(h[23]|ul|pre)/g, '<$1');
    html = html.replace(/<\/(h[23]|ul|pre)>\s*<\/p>/g, '</$1>');
    return html;
  }

  function showResult(data) {
    hideLoading();

    // Badge
    const badge = document.getElementById('result-badge');
    const src = (data.source_type === 'generic' ? 'web' : data.source_type) || 'web';
    badge.textContent = src;
    badge.className = 'm-result-badge ' + src.toLowerCase();

    // Title
    document.getElementById('result-title').textContent = data.title || 'Summary';

    // Brief
    document.getElementById('result-brief').textContent = data.brief_summary || data.one_line_summary || '';

    // Tags
    const tagsEl = document.getElementById('result-tags');
    tagsEl.innerHTML = '';
    if (data.tags && data.tags.length) {
      data.tags.forEach(function (t) {
        const tag = document.createElement('span');
        tag.className = 'm-tag';
        tag.textContent = t;
        tagsEl.appendChild(tag);
      });
    }

    // Detailed summary
    rawSummary = data.summary || '';
    document.getElementById('result-detail').innerHTML = markdownToHTML(rawSummary);

    // Source link
    const srcLink = document.getElementById('source-link');
    srcLink.href = data.source_url || '#';

    result.classList.add('active');
    result.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // Copy handler
  copyBtn.addEventListener('click', function () {
    if (!rawSummary) return;
    navigator.clipboard.writeText(rawSummary).then(function () {
      copyBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Copied!';
      setTimeout(function () {
        copyBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg> Copy';
      }, 2000);
    });
  });

  // Form submit
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var url = urlInput.value.trim();
    if (!url) return;

    // Basic URL validation
    if (!/^https?:\/\/.+/i.test(url)) {
      if (/^[\w]/.test(url)) url = 'https://' + url;
      else return showError('Please enter a valid URL.');
    }
    if (url.length > 2048) return showError('URL is too long (max 2048 characters).');

    showLoading();

    if (!window.ZKAddZettel || typeof window.ZKAddZettel.add !== 'function') {
      return showError('Add Zettel API helper failed to load. Please refresh and try again.');
    }

    window.ZKAddZettel.add({
      url: url,
      clientActionId: window.ZKAddZettel.makeActionId('landing'),
      persist: true,
      surface: 'landing',
      mode: 'auto'
    })
    .then(function (data) {
      showResult(data.summary || {});
    })
    .catch(function (err) {
      showError(err.message || 'Something went wrong. Please try again.');
    });
  });
})();
