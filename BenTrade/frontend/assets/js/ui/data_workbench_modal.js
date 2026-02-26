/**
 * BenTrade — Data Workbench Modal.
 *
 * An inline data-inspection modal with three tabs:
 *   1. Normalized   — the mapped trade object the card renders
 *   2. Raw Source    — the unprocessed candidate payload
 *   3. Derived       — scoring inputs, outputs & mapper diagnostics
 *
 * Each tab has a "Copy JSON" button.
 *
 * Usage:
 *   BenTradeDataWorkbenchModal.open({
 *     symbol:     'SPY',
 *     normalized: { ... },      // candidateToTradeShape output
 *     rawSource:  { ... },      // original scanner candidate (or null)
 *     derived:    { ... },      // scoring / mapper summary
 *   });
 *
 *   BenTradeDataWorkbenchModal.close();
 *
 * Depends on: BenTradeUtils.format (escapeHtml)
 */
window.BenTradeDataWorkbenchModal = (function () {
  'use strict';

  var _overlayEl = null;

  /* ── Helpers ────────────────────────────────────────────────── */

  function esc(text) {
    var fn = window.BenTradeUtils && window.BenTradeUtils.format && window.BenTradeUtils.format.escapeHtml;
    if (fn) return fn(text);
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /** Pretty-print a value to indented JSON with safe fallback. */
  function prettyJSON(obj) {
    try {
      return JSON.stringify(obj, null, 2);
    } catch (_e) {
      return String(obj);
    }
  }

  /** Copy text to clipboard (same pattern as trade_card.js). */
  function copyToClipboard(text, btnEl) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(function () {});
    } else {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch (_e) {}
      document.body.removeChild(ta);
    }
    if (btnEl) {
      var prev = btnEl.textContent;
      btnEl.textContent = 'Copied!';
      btnEl.classList.add('copy-flash');
      setTimeout(function () {
        btnEl.textContent = prev;
        btnEl.classList.remove('copy-flash');
      }, 1200);
    }
  }

  /* ── Build DOM ──────────────────────────────────────────────── */

  function _ensureOverlay() {
    if (_overlayEl) return _overlayEl;
    _overlayEl = document.createElement('div');
    _overlayEl.className = 'dwb-modal-overlay';
    _overlayEl.setAttribute('role', 'dialog');
    _overlayEl.setAttribute('aria-modal', 'true');
    _overlayEl.setAttribute('aria-label', 'Data Workbench');
    document.body.appendChild(_overlayEl);

    /* Close on backdrop click */
    _overlayEl.addEventListener('click', function (e) {
      if (e.target === _overlayEl) close();
    });

    /* Close on Escape */
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && _overlayEl && _overlayEl.classList.contains('is-open')) {
        close();
      }
    });

    return _overlayEl;
  }

  /* ── Tab definitions ────────────────────────────────────────── */

  var TABS = [
    { id: 'normalized', label: 'Normalized' },
    { id: 'raw',        label: 'Raw Source' },
    { id: 'derived',    label: 'Derived / Scoring' },
  ];

  /* ── Render ─────────────────────────────────────────────────── */

  function _renderContent(opts) {
    var symbol = esc(opts.symbol || 'Trade');
    var normalized = opts.normalized || {};
    var rawSource  = opts.rawSource  || null;
    var derived    = opts.derived    || {};

    /* Build tab buttons */
    var tabBtns = TABS.map(function (t, i) {
      var active = i === 0 ? ' dwb-tab-active' : '';
      return '<button type="button" class="dwb-tab' + active + '" data-dwb-tab="' + t.id + '">'
        + esc(t.label) + '</button>';
    }).join('');

    /* Build tab panels */
    var panels = [];

    /* 1. Normalized */
    var normJSON = prettyJSON(normalized);
    panels.push(
      '<div class="dwb-panel dwb-panel-active" data-dwb-panel="normalized">'
      + '<div class="dwb-panel-toolbar"><button type="button" class="dwb-copy-btn" data-dwb-copy="normalized" title="Copy JSON">Copy JSON</button></div>'
      + '<pre class="dwb-json"><code>' + esc(normJSON) + '</code></pre>'
      + '</div>'
    );

    /* 2. Raw Source */
    var rawJSON;
    var rawNote = '';
    if (rawSource && typeof rawSource === 'object' && Object.keys(rawSource).length > 0) {
      rawJSON = prettyJSON(rawSource);
    } else {
      rawJSON = '{}';
      rawNote = '<div class="dwb-note">Raw payload not captured for this trade.</div>';
    }
    panels.push(
      '<div class="dwb-panel" data-dwb-panel="raw">'
      + '<div class="dwb-panel-toolbar"><button type="button" class="dwb-copy-btn" data-dwb-copy="raw" title="Copy JSON">Copy JSON</button></div>'
      + rawNote
      + '<pre class="dwb-json"><code>' + esc(rawJSON) + '</code></pre>'
      + '</div>'
    );

    /* 3. Derived / Scoring */
    var derivedJSON = prettyJSON(derived);
    panels.push(
      '<div class="dwb-panel" data-dwb-panel="derived">'
      + '<div class="dwb-panel-toolbar"><button type="button" class="dwb-copy-btn" data-dwb-copy="derived" title="Copy JSON">Copy JSON</button></div>'
      + '<pre class="dwb-json"><code>' + esc(derivedJSON) + '</code></pre>'
      + '</div>'
    );

    return '<div class="dwb-modal">'
      + '<div class="dwb-modal-header">'
      + '<h3 class="dwb-modal-title">Data Workbench \u2014 ' + symbol + '</h3>'
      + '<button type="button" class="dwb-modal-close" aria-label="Close" title="Close">\u00D7</button>'
      + '</div>'
      + '<div class="dwb-tabs">' + tabBtns + '</div>'
      + '<div class="dwb-body">' + panels.join('') + '</div>'
      + '</div>';
  }

  /* ── JSON cache for copy ────────────────────────────────────── */
  var _jsonCache = {};

  /* ── Open ───────────────────────────────────────────────────── */

  /**
   * Open the Data Workbench modal.
   *
   * @param {object} opts
   * @param {string} opts.symbol      — display symbol
   * @param {object} opts.normalized  — normalized trade object
   * @param {object|null} opts.rawSource — raw scanner candidate (may be null)
   * @param {object} opts.derived     — scoring / diagnostic data
   */
  function open(opts) {
    var o = opts || {};
    var overlay = _ensureOverlay();

    /* Cache JSON for copy buttons */
    _jsonCache.normalized = prettyJSON(o.normalized || {});
    _jsonCache.raw        = prettyJSON(o.rawSource || {});
    _jsonCache.derived    = prettyJSON(o.derived || {});

    overlay.innerHTML = _renderContent(o);
    overlay.classList.add('is-open');

    /* Wire close button */
    var closeBtn = overlay.querySelector('.dwb-modal-close');
    if (closeBtn) closeBtn.addEventListener('click', close);

    /* Wire tab switching */
    var tabs = overlay.querySelectorAll('[data-dwb-tab]');
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].addEventListener('click', _onTabClick);
    }

    /* Wire copy buttons */
    var copyBtns = overlay.querySelectorAll('[data-dwb-copy]');
    for (var j = 0; j < copyBtns.length; j++) {
      copyBtns[j].addEventListener('click', _onCopyClick);
    }

    /* Focus the close button for keyboard access */
    if (closeBtn) closeBtn.focus();
  }

  /* ── Close ──────────────────────────────────────────────────── */

  function close() {
    if (_overlayEl) {
      _overlayEl.classList.remove('is-open');
      _overlayEl.innerHTML = '';
    }
  }

  /* ── Tab switching ──────────────────────────────────────────── */

  function _onTabClick(e) {
    var btn = e.currentTarget;
    var tabId = btn.getAttribute('data-dwb-tab');
    if (!tabId || !_overlayEl) return;

    /* Deactivate all tabs */
    var allTabs = _overlayEl.querySelectorAll('[data-dwb-tab]');
    for (var i = 0; i < allTabs.length; i++) {
      allTabs[i].classList.remove('dwb-tab-active');
    }
    btn.classList.add('dwb-tab-active');

    /* Hide all panels, show selected */
    var allPanels = _overlayEl.querySelectorAll('[data-dwb-panel]');
    for (var j = 0; j < allPanels.length; j++) {
      var panelId = allPanels[j].getAttribute('data-dwb-panel');
      if (panelId === tabId) {
        allPanels[j].classList.add('dwb-panel-active');
      } else {
        allPanels[j].classList.remove('dwb-panel-active');
      }
    }
  }

  /* ── Copy handler ───────────────────────────────────────────── */

  function _onCopyClick(e) {
    var btn = e.currentTarget;
    var tabId = btn.getAttribute('data-dwb-copy');
    var json = _jsonCache[tabId] || '{}';
    copyToClipboard(json, btn);
  }

  /* ── Public API ─────────────────────────────────────────────── */

  return {
    open: open,
    close: close,
  };
})();
