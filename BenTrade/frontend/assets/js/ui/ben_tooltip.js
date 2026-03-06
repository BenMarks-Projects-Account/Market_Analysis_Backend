/**
 * BenTrade — Rich Tooltip (BenTooltip).
 *
 * A reusable, fullscreen-aware tooltip for multi-line rich content.
 * Used for Market Regime components, strategy chips, and any element
 * that needs a hover/focus popup with title + body text.
 *
 * Mount: Uses BenTradeOverlayRoot.get() so it stays visible in
 *        browser fullscreen mode.
 *
 * Content source: BenTradeTooltipDictionary.allRich() (tooltip_dictionary.js).
 * Runtime entries can still be added via BenTradeBenTooltip.register().
 *
 * Usage:
 *   // Attach via data attributes:
 *   <span data-ben-tip="trend">Trend</span>
 *
 *   // Or programmatically:
 *   BenTradeBenTooltip.bind(element, 'trend');
 *
 *   // Auto-bind all [data-ben-tip] under a root:
 *   BenTradeBenTooltip.bindAll(containerEl);
 *
 * Depends on: BenTradeOverlayRoot (overlayRoot.js), BenTradeTooltipDictionary (tooltip_dictionary.js)
 */
window.BenTradeBenTooltip = (function () {
  'use strict';

  /* ── Tooltip content registry ────────────────────────────────── */
  /* Seeded from centralized dictionary; runtime additions via register() */

  var TIPS = (window.BenTradeTooltipDictionary)
    ? Object.assign({}, window.BenTradeTooltipDictionary.allRich())
    : {};

  /* ── Inline TIPS definitions removed ─────────────────────────
   * All content now lives in tooltip_dictionary.js and is seeded
   * into TIPS above via BenTradeTooltipDictionary.allRich().
   * Runtime additions still work via register(key, tip).
   * ──────────────────────────────────────────────────────────── */

  /* ── State ─────────────────────────────────────────────────── */

  var _el = null;         // the singleton tooltip DOM element
  var _active = null;     // currently-hovered target element
  var _observer = null;   // MutationObserver for auto-binding
  var _hideTimer = null;  // delay before hiding (prevents flicker)

  var TOUCH = (typeof window !== 'undefined')
    ? (('ontouchstart' in window) || (navigator.maxTouchPoints > 0))
    : false;

  /* ── Helpers ────────────────────────────────────────────────── */

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /**
   * Normalize a strategy id from chip text (e.g. "put_credit_spread" or
   * "Put Credit Spread") to a registry key.  Returns the key if found,
   * otherwise null.
   */
  function resolveKey(raw) {
    if (!raw) return null;
    var key = String(raw).trim().toLowerCase().replace(/[\s-]+/g, '_');
    if (TIPS[key]) return key;
    // Try with regime_ prefix
    if (TIPS['regime_' + key]) return 'regime_' + key;
    return null;
  }

  /* ── DOM construction ───────────────────────────────────────── */

  function ensureEl() {
    if (_el) return _el;
    _el = document.createElement('div');
    _el.className = 'ben-tip';
    _el.id = 'btBenTooltip';
    _el.setAttribute('role', 'tooltip');
    _el.setAttribute('aria-hidden', 'true');
    // Mount inside overlay-root (fullscreen-safe)
    var root = (window.BenTradeOverlayRoot && window.BenTradeOverlayRoot.get)
      ? window.BenTradeOverlayRoot.get()
      : document.body;
    root.appendChild(_el);
    return _el;
  }

  function buildHtml(tip) {
    var out = [];
    out.push('<div class="ben-tip-title">' + esc(tip.title) + '</div>');
    // Support 'lines' array for bullet-formatted dynamic content
    if (tip.lines && tip.lines.length) {
      out.push('<ul class="ben-tip-lines">');
      tip.lines.forEach(function (line) {
        out.push('<li>' + esc(line) + '</li>');
      });
      out.push('</ul>');
    }
    if (tip.body) {
      out.push('<div class="ben-tip-body">' + esc(tip.body) + '</div>');
    }
    if (tip.impact) {
      out.push('<div class="ben-tip-impact"><span class="ben-tip-impact-label">Impact to Regime:</span> ' + esc(tip.impact) + '</div>');
    }
    if (tip.conditions && tip.conditions.length) {
      out.push('<div class="ben-tip-conditions-label">Best Conditions:</div>');
      out.push('<ul class="ben-tip-conditions">');
      tip.conditions.forEach(function (c) {
        out.push('<li>' + esc(c) + '</li>');
      });
      out.push('</ul>');
    }
    if (tip.risk) {
      out.push('<div class="ben-tip-risk"><span class="ben-tip-risk-label">Risk Note:</span> ' + esc(tip.risk) + '</div>');
    }
    return out.join('');
  }

  /* ── Positioning ────────────────────────────────────────────── */

  function position(target) {
    if (!_el || !target) return;
    var rect = target.getBoundingClientRect();
    var gap = 10;

    // Make el visible but transparent for measurement
    _el.style.left = '0px';
    _el.style.top = '0px';
    var ttRect = _el.getBoundingClientRect();

    var left = rect.left + (rect.width / 2) - (ttRect.width / 2);
    var top = rect.bottom + gap;

    // Clamp horizontally
    if (left + ttRect.width > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - ttRect.width - 8);
    }
    if (left < 8) left = 8;

    // Flip above if below viewport
    if (top + ttRect.height > window.innerHeight - 8) {
      top = rect.top - ttRect.height - gap;
    }
    if (top < 8) top = Math.max(8, rect.bottom + gap);

    _el.style.left = Math.round(left) + 'px';
    _el.style.top = Math.round(top) + 'px';
  }

  /* ── Show / Hide ────────────────────────────────────────────── */

  function show(target, tipKey) {
    var entry = TIPS[tipKey];
    if (!entry) return;
    // Support dynamic tooltip builders (function → tip object)
    var tip = (typeof entry === 'function') ? entry() : entry;
    if (!tip) return;

    if (_hideTimer) { clearTimeout(_hideTimer); _hideTimer = null; }

    var el = ensureEl();
    el.innerHTML = buildHtml(tip);
    el.classList.add('is-open');
    el.setAttribute('aria-hidden', 'false');
    target.setAttribute('aria-describedby', 'btBenTooltip');
    _active = target;
    position(target);

    if (window.__BEN_DEBUG_OVERLAYS) {
      console.debug('[BenTrade:overlay] BenTooltip show', tipKey,
        'parent:', el.parentElement && (el.parentElement.id || el.parentElement.tagName),
        'fullscreenElement:', document.fullscreenElement && document.fullscreenElement.className);
    }
  }

  function hide() {
    _hideTimer = setTimeout(function () {
      if (!_el) return;
      _el.classList.remove('is-open');
      _el.setAttribute('aria-hidden', 'true');
      // Move off-screen so hidden tooltip cannot block pointer events
      _el.style.left = '-9999px';
      _el.style.top = '-9999px';
      _el.innerHTML = '';
      if (_active) {
        _active.removeAttribute('aria-describedby');
      }
      _active = null;
      _hideTimer = null;
    }, 80);
  }

  function hideImmediate() {
    if (_hideTimer) { clearTimeout(_hideTimer); _hideTimer = null; }
    if (!_el) return;
    _el.classList.remove('is-open');
    _el.setAttribute('aria-hidden', 'true');
    // Move off-screen so hidden tooltip cannot block pointer events
    _el.style.left = '-9999px';
    _el.style.top = '-9999px';
    _el.innerHTML = '';
    if (_active) {
      _active.removeAttribute('aria-describedby');
    }
    _active = null;
  }

  /* ── Binding ────────────────────────────────────────────────── */

  function isBound(el) {
    return el && el.dataset && el.dataset.benTipBound === '1';
  }

  function bind(el, tipKey) {
    if (!el || isBound(el)) return;
    var key = tipKey || resolveKey(el.getAttribute('data-ben-tip') || el.textContent);
    if (!key) return;

    el.dataset.benTipBound = '1';
    el.dataset.benTipKey = key;

    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
    if (!el.getAttribute('aria-label')) {
      var tip = TIPS[key];
      if (tip) el.setAttribute('aria-label', tip.title);
    }

    el.addEventListener('mouseenter', function () { show(el, key); });
    el.addEventListener('mouseleave', function () { hide(); });
    el.addEventListener('focus', function () { show(el, key); });
    el.addEventListener('blur', function () { hide(); });
    el.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') hideImmediate();
    });

    // Touch: tap to toggle
    if (TOUCH) {
      el.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (_active === el && _el && _el.classList.contains('is-open')) {
          hideImmediate();
        } else {
          show(el, key);
        }
      });
    }
  }

  function bindAll(rootEl) {
    var root = rootEl || document;
    var els = root.querySelectorAll('[data-ben-tip]');
    for (var i = 0; i < els.length; i++) {
      bind(els[i]);
    }
  }

  /* ── Auto-bind via MutationObserver ─────────────────────────── */

  function startObserver() {
    if (_observer) return;
    _observer = new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        var added = mutations[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var node = added[j];
          if (!(node instanceof Element)) continue;
          if (node.hasAttribute && node.hasAttribute('data-ben-tip')) {
            bind(node);
          }
          bindAll(node);
        }
      }
    });
    _observer.observe(document.body, { childList: true, subtree: true });
  }

  /* ── Global listeners ───────────────────────────────────────── */

  // Reposition on scroll/resize
  window.addEventListener('scroll', function () {
    if (_active && _el && _el.classList.contains('is-open')) position(_active);
  }, true);

  window.addEventListener('resize', function () {
    if (_active && _el && _el.classList.contains('is-open')) position(_active);
  });

  // Dismiss on outside click
  document.addEventListener('click', function (e) {
    if (!_el || !_active) return;
    var target = e.target;
    if (!(target instanceof Element)) return;
    if (_el.contains(target)) return;
    if (target.closest('[data-ben-tip]')) return;
    hideImmediate();
  });

  // Dismiss on Escape
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') hideImmediate();
  });

  // Start observer immediately
  startObserver();

  /* ── Public API ─────────────────────────────────────────────── */

  return {
    bind: bind,
    bindAll: bindAll,
    hide: hideImmediate,
    /** Register or override a tooltip entry at runtime. */
    register: function (key, tip) { TIPS[key] = tip; },
    /** Resolve a chip label/id to a registry key (or null). */
    resolveKey: resolveKey,
  };
})();
