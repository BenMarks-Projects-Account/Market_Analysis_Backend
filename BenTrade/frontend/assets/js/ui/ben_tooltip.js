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
 * Depends on: BenTradeOverlayRoot (overlayRoot.js)
 */
window.BenTradeBenTooltip = (function () {
  'use strict';

  /* ── Tooltip content registry ────────────────────────────────── */

  var TIPS = {};

  /* ── Market Regime components ── */

  TIPS['regime_trend'] = {
    title: 'Trend Strength',
    body: 'Measures directional market bias using moving-average alignment (EMA20, EMA50, SMA200). Strong upward alignment signals bullish regime support, while weak or inverted structure increases downside and mean-reversion risk.',
    impact: 'Higher trend strength favors premium-selling strategies and directional trades with the trend.',
  };

  TIPS['regime_volatility'] = {
    title: 'Volatility Environment',
    body: 'Evaluates implied volatility level relative to normal conditions (primarily via VIX). Elevated volatility increases option premiums and risk, while low volatility compresses pricing but often supports trend persistence.',
    impact: 'Higher volatility favors premium selling; very low volatility may favor debit structures or directional plays.',
  };

  TIPS['regime_breadth'] = {
    title: 'Market Breadth',
    body: 'Tracks how many sectors or components participate in the market move. Broad participation signals healthy institutional support, while narrow leadership increases fragility and reversal risk.',
    impact: 'Strong breadth improves confidence in trend continuation and premium strategies.',
  };

  TIPS['regime_rates'] = {
    title: 'Interest Rate Pressure',
    body: 'Monitors the 10-year Treasury yield as a proxy for financial conditions. Rising yields can pressure equities (especially growth), while stable or falling rates generally support risk assets.',
    impact: 'Stable or falling rates support bullish structures; rapidly rising rates increase regime risk.',
  };

  TIPS['regime_momentum'] = {
    title: 'Momentum Quality',
    body: 'Uses RSI positioning to evaluate whether price movement is sustainably trending or becoming stretched. Mid-range RSI typically indicates healthy continuation, while extremes increase reversal probability.',
    impact: 'Healthy momentum supports trend trades; overbought/oversold conditions increase mean-reversion risk.',
  };

  /* ── Strategy chips ── */

  TIPS['put_credit_spread'] = {
    title: 'Put Credit Spread',
    body: 'A bullish defined-risk premium strategy that sells an out-of-the-money put while buying a further OTM put for protection. Profits when price stays above the short strike and volatility contracts.',
    conditions: [
      'Bullish to neutral trend',
      'Elevated implied volatility',
      'Stable or rising market',
    ],
  };

  TIPS['covered_call'] = {
    title: 'Covered Call',
    body: 'Owns shares while selling an out-of-the-money call to generate income. Caps upside but provides partial downside cushion through collected premium.',
    conditions: [
      'Neutral to moderately bullish market',
      'Elevated implied volatility',
      'Low expectation of explosive upside',
    ],
  };

  TIPS['call_debit'] = {
    title: 'Call Debit Spread',
    body: 'A defined-risk bullish strategy that buys a call and sells a higher-strike call. Requires upward price movement to profit and benefits from directional momentum.',
    conditions: [
      'Strong bullish trend',
      'Lower or rising volatility',
      'Momentum expansion phases',
    ],
  };

  TIPS['short_gamma'] = {
    title: 'Short Gamma Exposure',
    body: 'Represents strategies that benefit from price stability but are harmed by large directional moves. Short gamma positions collect premium but carry tail risk during volatility expansion.',
    conditions: [
      'Range-bound markets',
      'Declining volatility',
      'High liquidity environments',
    ],
    risk: 'Vulnerable to sharp breakouts and volatility spikes.',
  };

  TIPS['debit_butterfly'] = {
    title: 'Debit Butterfly',
    body: 'A low-cost, defined-risk neutral strategy that profits if price pins near a target level at expiration. Requires precise price location and typically underperforms in strong trends.',
    conditions: [
      'Low volatility',
      'Range-bound markets',
      'Event pinning scenarios',
    ],
    risk: 'Low probability of max profit; sensitive to directional drift.',
  };

  /* Additional strategy chips that may appear dynamically */

  TIPS['iron_condor'] = {
    title: 'Iron Condor',
    body: 'A neutral premium-selling strategy combining a put credit spread and a call credit spread. Profits when the underlying stays within a defined range through expiration.',
    conditions: [
      'Range-bound markets',
      'Elevated implied volatility',
      'Low momentum / mean-reverting conditions',
    ],
  };

  TIPS['calendar_spread'] = {
    title: 'Calendar Spread',
    body: 'Sells a near-term option and buys a longer-dated option at the same strike. Profits from time decay and potential IV expansion in the back month.',
    conditions: [
      'Low near-term volatility',
      'Stable underlying price',
      'Positive term structure',
    ],
  };

  TIPS['call_credit_spread'] = {
    title: 'Call Credit Spread',
    body: 'A bearish defined-risk premium strategy that sells an OTM call while buying a further OTM call. Profits when price stays below the short strike.',
    conditions: [
      'Bearish to neutral trend',
      'Elevated implied volatility',
      'Resistance overhead or negative momentum',
    ],
  };

  TIPS['put_debit'] = {
    title: 'Put Debit Spread',
    body: 'A defined-risk bearish strategy that buys a put and sells a lower-strike put. Requires downward price movement to profit.',
    conditions: [
      'Bearish trend or breakdown',
      'Lower or rising volatility',
      'Negative momentum',
    ],
  };

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
