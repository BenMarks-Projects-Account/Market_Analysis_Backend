/**
 * BenTrade — Shared TradeCard building blocks.
 *
 * Preserves the original utility API (resolveTradeKey, buildTradeKey,
 * openDataWorkbenchByTrade) and adds composable rendering helpers that
 * every page can use to build trade cards with consistent markup.
 *
 * Depends on: BenTradeUtils.format, BenTradeUtils.tradeAccessor (optional)
 */
window.BenTradeTradeCard = (function(){
  'use strict';

  var fmt = window.BenTradeUtils.format;
  var dbg = window.BenTradeDebug;

  /* ================================================================
   *  EXISTING UTILITIES (preserved API)
   * ================================================================ */

  function resolveTradeKey(trade){
    return String(trade?.trade_key || trade?._trade_key || '').trim();
  }

  function buildTradeKey(trade){
    var safe = trade || {};
    var util = window.BenTradeUtils?.tradeKey;
    if(util?.tradeKey){
      return util.tradeKey({
        underlying: safe.symbol || safe.underlying || safe.underlying_symbol,
        expiration: safe.expiration,
        spread_type: safe.strategy_id || safe.spread_type || safe.strategy,
        short_strike: safe.short_strike,
        long_strike: safe.long_strike,
        dte: safe.dte,
      });
    }
    var underlying = String(safe.symbol || safe.underlying || safe.underlying_symbol || '').toUpperCase();
    var expiration = String(safe.expiration || '');
    var spreadType = String(safe.strategy_id || safe.spread_type || safe.strategy || '');
    var shortStrike = String(safe.short_strike ?? '');
    var longStrike = String(safe.long_strike ?? '');
    var dte = String(safe.dte ?? '');
    return underlying + '|' + expiration + '|' + spreadType + '|' + shortStrike + '|' + longStrike + '|' + dte;
  }

  function openDataWorkbenchByTrade(trade, options){
    var opts = (options && typeof options === 'object') ? options : {};
    var key = resolveTradeKey(trade);
    if(!key){
      if(typeof opts.onMissingTradeKey === 'function'){
        try{ opts.onMissingTradeKey(trade || {}); }catch(_err){}
      }
      return false;
    }
    var encoded = encodeURIComponent(key);
    window.location.hash = '#/admin/data-workbench?trade_key=' + encoded;
    return true;
  }

  /* ================================================================
   *  SHARED BUILDING BLOCKS
   * ================================================================ */

  /**
   * Determine CSS tone class from a numeric value.
   *   toneClass(0.25)                       → 'positive'
   *   toneClass(-0.05)                      → 'negative'
   *   toneClass(null)                       → 'neutral'
   *   toneClass(0.15, { threshold: 0.2 })   → 'negative'
   */
  function toneClass(value, opts){
    if(value === null || value === undefined) return 'neutral';
    var n = Number(value);
    if(!Number.isFinite(n)) return 'neutral';
    var threshold = (opts && opts.threshold != null) ? opts.threshold : 0;
    var invert = opts && opts.invert;
    return (invert ? (n <= threshold) : (n >= threshold)) ? 'positive' : 'negative';
  }

  /**
   * Build a metric-grid HTML string.
   * @param {Array<{label:string, value:string, cssClass?:string, dataMetric?:string}>} items
   * @returns {string} HTML
   */
  function metricGrid(items){
    if(!Array.isArray(items) || !items.length) return '';

    if(dbg && dbg.enabled){
      items.forEach(function(item, idx){
        dbg.assert(item.label, 'metricGrid item missing label', { idx: idx });
        dbg.assert(item.value !== undefined, 'metricGrid item has undefined value (use "N/A")', { idx: idx, label: item.label });
      });
    }

    var cells = items.map(function(item){
      var cls = item.cssClass || 'neutral';
      var metricAttr = item.dataMetric
        ? ' data-metric="' + fmt.escapeHtml(item.dataMetric) + '"'
        : '';
      return '<div class="metric">'
        + '<div class="metric-label"' + metricAttr + '>' + fmt.escapeHtml(item.label) + '</div>'
        + '<div class="metric-value ' + cls + '">' + item.value + '</div>'
        + '</div>';
    }).join('');

    return '<div class="metric-grid">' + cells + '</div>';
  }

  /**
   * Build a titled section wrapper.
   * @param {string} title       – section heading (e.g. "CORE METRICS")
   * @param {string} contentHtml – pre-built inner HTML
   * @param {string} [extraClass] – additional CSS class (e.g. "section-core")
   * @returns {string} HTML
   */
  function section(title, contentHtml, extraClass){
    var cls = 'section' + (extraClass ? ' ' + extraClass : '');
    return '<div class="' + cls + '">'
      + '<div class="section-title">' + fmt.escapeHtml(title) + '</div>'
      + contentHtml
      + '</div>';
  }

  /**
   * Build detail rows.
   * @param {Array<{label:string, value:string, dataMetric?:string}>} items
   * @returns {string} HTML
   */
  function detailRows(items){
    if(!Array.isArray(items) || !items.length) return '';
    return '<div class="trade-details">' + items.map(function(item){
      var metricAttr = item.dataMetric
        ? ' data-metric="' + fmt.escapeHtml(item.dataMetric) + '"'
        : '';
      return '<div class="detail-row">'
        + '<span class="detail-label"' + metricAttr + '>' + fmt.escapeHtml(item.label) + '</span>'
        + '<span class="detail-value">' + item.value + '</span>'
        + '</div>';
    }).join('') + '</div>';
  }

  /**
   * Build a pill span.
   *   pill('SPY')              → <span class="qtPill">SPY</span>
   *   pill('3 warnings','warn')→ <span class="qtPill qtPill-warn">3 warnings</span>
   *
   * @param {string} text
   * @param {string} [variant] – 'warn', 'positive', 'negative'
   * @returns {string} HTML
   */
  function pill(text, variant){
    var cls = 'qtPill' + (variant ? ' qtPill-' + variant : '');
    return '<span class="' + cls + '">' + fmt.escapeHtml(text) + '</span>';
  }

  /**
   * Render a formatted metric value, or show a "missing" hint with tooltip.
   *
   * @param {*}        value     – raw metric value
   * @param {function} formatter – e.g. (v) => fmt.pct(v,1)
   * @param {string}   reason    – tooltip text when missing
   * @returns {string} HTML
   */
  function metricValueOrMissing(value, formatter, reason){
    var n = fmt.toNumber(value);
    if(n !== null) return formatter(value);
    var why = String(reason || 'Metric unavailable');
    return '<span class="home-missing-wrap">\u2014 <span class="home-missing-hint" title="'
      + fmt.escapeHtml(why) + '">?</span></span>';
  }

  /**
   * Convert strategy slug to display title.
   *   "credit_put_spread" → "Credit Put Spread"
   */
  function formatTradeType(value){
    var text = String(value || 'trade').replaceAll('_', ' ').trim();
    return text.replace(/\b\w/g, function(ch){ return ch.toUpperCase(); });
  }

  /**
   * Reason string for missing metrics based on source type and metric name.
   * Used with metricValueOrMissing().
   */
  function metricMissingReason(sourceType, metric){
    var type = String(sourceType || '').toLowerCase();
    var key = String(metric || '').toLowerCase();
    if(type === 'stock'){
      if(key === 'ev') return 'EV not computed for equities';
      if(key === 'pop') return 'POP not computed for equities';
      if(key === 'ror') return 'RoR not computed for equities';
      return 'Not computed for equities';
    }
    return 'Missing from source payload';
  }

  /* ================================================================
   *  COPY TRADE KEY — shared clipboard helper
   * ================================================================ */

  /**
   * Copy a trade key string to the clipboard and flash a "Copied" tooltip
   * on the triggering element.
   * @param {string} tradeKey
   * @param {HTMLElement} [triggerEl] — optional button to flash feedback on
   */
  function copyTradeKey(tradeKey, triggerEl){
    var text = String(tradeKey || '').trim();
    if(!text) return;
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(text).catch(function(){});
    }else{
      // Fallback for HTTP / older browsers
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      try{ document.execCommand('copy'); }catch(_e){}
      document.body.removeChild(ta);
    }
    // Flash "Copied!" feedback
    if(triggerEl){
      var prev = triggerEl.textContent;
      triggerEl.textContent = 'Copied!';
      triggerEl.classList.add('copy-flash');
      setTimeout(function(){
        triggerEl.textContent = prev;
        triggerEl.classList.remove('copy-flash');
      }, 1200);
    }
  }

  /**
   * Build a small copy-to-clipboard button HTML snippet.
   * @param {string} tradeKey — the key value to copy
   * @returns {string} HTML button string (or '' if no key)
   */
  function copyTradeKeyButton(tradeKey){
    var text = String(tradeKey || '').trim();
    if(!text) return '';
    return '<button type="button" class="btn-copy-trade-key" data-copy-trade-key="'
      + fmt.escapeHtml(text)
      + '" title="Copy trade key to clipboard" aria-label="Copy trade key">\u2398</button>';
  }

  /* ================================================================
   *  PUBLIC API
   * ================================================================ */

  return {
    // Existing utilities
    resolveTradeKey: resolveTradeKey,
    buildTradeKey: buildTradeKey,
    openDataWorkbenchByTrade: openDataWorkbenchByTrade,
    // Copy trade key
    copyTradeKey: copyTradeKey,
    copyTradeKeyButton: copyTradeKeyButton,
    // Shared building blocks
    toneClass: toneClass,
    metricGrid: metricGrid,
    section: section,
    detailRows: detailRows,
    pill: pill,
    metricValueOrMissing: metricValueOrMissing,
    formatTradeType: formatTradeType,
    metricMissingReason: metricMissingReason,
  };
})();
