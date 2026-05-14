/* Zettelkasten Summarizer — Client-side logic */

(function () {
  'use strict';

  const form = document.getElementById('summarize-form');
  const urlInput = document.getElementById('url-input');
  const submitBtn = document.getElementById('submit-btn');
  const errorMsg = document.getElementById('error-message');

  // Source dropdown
  const dropdownToggle = document.getElementById('dropdown-toggle');
  const dropdownLabel = document.getElementById('dropdown-label');
  const dropdownMenu = document.getElementById('dropdown-menu');
  const dropdownItems = dropdownMenu.querySelectorAll('.dropdown-item');

  const inputSection = document.getElementById('input-section');
  const loadingSection = document.getElementById('loading-section');
  const resultSection = document.getElementById('result-section');
  const errorSection = document.getElementById('error-section');

  const loadingText = document.getElementById('loading-text');
  const loadingUrl = document.getElementById('loading-url');

  const resultSource = document.getElementById('result-source');
  const resultTitle = document.getElementById('result-title');
  const resultOneliner = document.getElementById('result-oneliner');
  const resultTags = document.getElementById('result-tags');
  const resultBrief = document.getElementById('result-brief');
  const resultDetailed = document.getElementById('result-detailed');
  const resultLink = document.getElementById('result-link');

  const copyBtn = document.getElementById('copy-btn');
  const kgOpenBtn = document.getElementById('kg-open-btn');
  const tryAnotherBtn = document.getElementById('try-another-btn');
  const errorRetryBtn = document.getElementById('error-retry-btn');
  const errorDetail = document.getElementById('error-detail');

  // Loading messages that cycle
  const loadingMessages = [
    'Detecting source type...',
    'Resolving redirects...',
    'Extracting content...',
    'Analyzing with Gemini AI...',
    'Building summary...',
    'Almost there...',
  ];

  let loadingInterval = null;

  function showSection(section) {
    [inputSection, loadingSection, resultSection, errorSection].forEach(function (s) {
      s.classList.add('hidden');
    });
    section.classList.remove('hidden');
  }

  function startLoading(url) {
    showSection(loadingSection);
    loadingUrl.textContent = url;
    var idx = 0;
    loadingText.textContent = loadingMessages[0];
    loadingInterval = setInterval(function () {
      idx = Math.min(idx + 1, loadingMessages.length - 1);
      loadingText.textContent = loadingMessages[idx];
    }, 3000);
  }

  function stopLoading() {
    if (loadingInterval) {
      clearInterval(loadingInterval);
      loadingInterval = null;
    }
  }

  function validateUrl(url) {
    if (!url) return 'Please enter a URL';
    if (url.length > 2048) return 'URL is too long (max 2048 characters)';
    if (!url.match(/^https?:\/\/.+/)) return 'URL must start with http:// or https://';
    return null;
  }

  // Simple Markdown to HTML converter for summaries
  function markdownToHtml(text) {
    if (!text) return '';

    var html = text
      // Escape HTML entities first
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      // Code blocks (before other rules)
      .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
      // Inline code
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      // Headers
      .replace(/^### (.+)$/gm, '<h3>$1</h3>')
      .replace(/^## (.+)$/gm, '<h2>$1</h2>')
      // Bold
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      // Italic
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')
      // Bullet points (• or - or *)
      .replace(/^[•\-\*] (.+)$/gm, '<li>$1</li>')
      // Wrap consecutive <li> in <ul>
      .replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>')
      // Line breaks for remaining text
      .replace(/\n\n/g, '</p><p>')
      .replace(/\n/g, '<br>');

    // Wrap in paragraph if not starting with a block element
    if (!html.match(/^<[hup]/)) {
      html = '<p>' + html + '</p>';
    }

    return html;
  }

  function renderTags(tags) {
    resultTags.innerHTML = '';
    tags
      .filter(function (tag) {
        // Filter out noisy/redundant tags
        if (tag.startsWith('source/')) return false;
        if (tag.startsWith('status/')) return false;
        if (tag.startsWith('difficulty/')) return false;
        return true;
      })
      .forEach(function (tag) {
        var el = document.createElement('span');
        el.className = 'tag';
        var category = tag.split('/')[0];
        if (['domain', 'type', 'keyword'].indexOf(category) !== -1) {
          el.classList.add('tag-' + category);
        }
        el.textContent = tag;
        resultTags.appendChild(el);
      });
  }

  function showResult(data) {
    stopLoading();
    showSection(resultSection);

    // Source badge
    resultSource.textContent = data.source_type;
    resultSource.className = 'source-badge ' + data.source_type;

    // Title & one-liner
    resultTitle.textContent = data.title || 'Untitled';
    resultOneliner.textContent = data.one_line_summary || '';
    resultOneliner.style.display = data.one_line_summary ? '' : 'none';

    // Tags
    renderTags(data.tags || []);

    // Summaries
    resultBrief.innerHTML = markdownToHtml(data.brief_summary);
    resultDetailed.innerHTML = markdownToHtml(data.summary);

    // Source link
    resultLink.href = data.source_url;
    resultLink.textContent = 'View original \u2197';

    // Knowledge Graph button
    if (data.node_id) {
      kgOpenBtn.href = '/knowledge-graph?node=' + encodeURIComponent(data.node_id);
      kgOpenBtn.classList.remove('hidden');
    } else {
      kgOpenBtn.classList.add('hidden');
    }
  }

  function showError(message) {
    stopLoading();
    showSection(errorSection);
    errorDetail.textContent = message;
  }

  function reset() {
    showSection(inputSection);
    urlInput.value = '';
    urlInput.focus();
    errorMsg.textContent = '';
    document.querySelector('.input-wrapper').classList.remove('error');
    // Reset dropdown
    dropdownLabel.textContent = 'Menu';
    dropdownToggle.className = 'dropdown-toggle';
    dropdownItems.forEach(function (i) { i.classList.remove('active'); });
  }

  // Source dropdown logic
  dropdownToggle.addEventListener('click', function () {
    var isOpen = dropdownMenu.classList.contains('open');
    dropdownMenu.classList.toggle('open');
    dropdownToggle.classList.toggle('open');
    if (!isOpen) {
      // Close on outside click
      setTimeout(function () {
        document.addEventListener('click', closeDropdown);
      }, 0);
    }
  });

  function closeDropdown(e) {
    if (!dropdownToggle.contains(e.target) && !dropdownMenu.contains(e.target)) {
      dropdownMenu.classList.remove('open');
      dropdownToggle.classList.remove('open');
      document.removeEventListener('click', closeDropdown);
    }
  }

  dropdownItems.forEach(function (item) {
    item.addEventListener('click', function () {
      var value = item.getAttribute('data-value');
      var label = value ? item.textContent : 'Menu';

      // Update label
      dropdownLabel.textContent = label;

      // Update toggle color class
      dropdownToggle.className = 'dropdown-toggle selected';
      if (value) {
        dropdownToggle.classList.add('src-' + value);
      } else {
        dropdownToggle.classList.remove('selected');
        dropdownToggle.className = 'dropdown-toggle';
      }

      // Mark active item
      dropdownItems.forEach(function (i) { i.classList.remove('active'); });
      item.classList.add('active');

      // Close
      dropdownMenu.classList.remove('open');
      dropdownToggle.classList.remove('open');
      document.removeEventListener('click', closeDropdown);

      urlInput.focus();
    });
  });

  // Submit handler
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var url = urlInput.value.trim();

    // Validate
    var err = validateUrl(url);
    if (err) {
      errorMsg.textContent = err;
      document.querySelector('.input-wrapper').classList.add('error');
      return;
    }

    errorMsg.textContent = '';
    document.querySelector('.input-wrapper').classList.remove('error');
    submitBtn.disabled = true;

    startLoading(url);

    var authToken = typeof getAuthToken === 'function' ? getAuthToken() : null;

    if (!window.ZKAddZettel || typeof window.ZKAddZettel.add !== 'function') {
      stopLoading();
      submitBtn.disabled = false;
      showError('Add Zettel API helper failed to load. Please refresh and try again.');
      return;
    }

    window.ZKAddZettel.add({
      url: url,
      token: authToken,
      clientActionId: window.ZKAddZettel.makeActionId('landing'),
      persist: true,
      surface: 'landing',
      mode: 'auto'
    })
      .then(function (data) {
        var summary = data.summary || {};
        summary.node_id = data.node_id;
        summary.workspace_zettel_id = data.workspace_zettel_id;
        summary.persistence = data.persistence;
        showResult(summary);
      })
      .catch(function (err) {
        showError(err.message || 'An unexpected error occurred. Please try again.');
      })
      .finally(function () {
        submitBtn.disabled = false;
      });
  });

  // Clear error on input
  urlInput.addEventListener('input', function () {
    errorMsg.textContent = '';
    document.querySelector('.input-wrapper').classList.remove('error');
  });

  // Copy button
  copyBtn.addEventListener('click', function () {
    var text = resultDetailed.innerText;
    navigator.clipboard.writeText(text).then(function () {
      copyBtn.classList.add('copied');
      document.querySelector('.copy-text').textContent = 'Copied!';
      setTimeout(function () {
        copyBtn.classList.remove('copied');
        document.querySelector('.copy-text').textContent = 'Copy';
      }, 2000);
    });
  });

  // Try another
  tryAnotherBtn.addEventListener('click', reset);
  errorRetryBtn.addEventListener('click', reset);

  // Focus input on load
  urlInput.focus();
})();
