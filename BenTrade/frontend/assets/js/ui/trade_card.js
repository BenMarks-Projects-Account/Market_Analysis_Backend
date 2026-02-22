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
   *  renderFullCard — Canonical trade card renderer.
   *
   *  Produces the exact same collapsible trade card HTML used on
   *  scanner pages.  Both scanner dashboards and Opportunity Engine
   *  call this single function — never duplicate this rendering.
   *
   *  @param {object} rawTrade      — raw API trade object
   *  @param {number} idx           — card index (used in data-idx)
   *  @param {object} [opts]
   *  @param {string}  [opts.strategyHint]  — strategy ID hint for mapper
   *  @param {object}  [opts.expandState]   — { tradeKey: boolean }
   *  @param {boolean} [opts.debugTrades]   — show debug warnings
   *  @param {string}  [opts.modelStatus]   — 'running' → "Running…" label
   *  @param {number|null} [opts.rankOverride] — fallback rank when trade
   *                                             has no rank_score field
   *  @returns {string} HTML
   * ================================================================ */
  function renderFullCard(rawTrade, idx, opts){
    var o = opts || {};
    var mapper = window.BenTradeOptionTradeCardModel;
    if(!mapper){
      return '<div class="trade-card" data-idx="' + idx + '" style="margin-bottom:14px;display:flex;flex-direction:column;">'
        + '<div class="trade-body" style="padding:8px;font-size:12px;color:var(--muted);">Mapper unavailable</div></div>';
    }

    var fmtLib = window.BenTradeUtils.format;
    var esc = fmtLib.escapeHtml || function(v){ return String(v == null ? '' : v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); };
    var fmtNum = fmtLib.num;
    var expandState = o.expandState || {};
    var debugTrades = !!o.debugTrades;

    /* 1. Map through canonical mapper */
    var model = mapper.map(rawTrade, o.strategyHint || '');
    var h = model.header;

    /* 2. Rank badge */
    var rankDesc = { key: 'rank_score', computedKey: 'rank_score', rootFallbacks: ['composite_score'] };
    var rankVal = mapper.resolveMetric(rawTrade, rankDesc);
    if(rankVal === null && o.rankOverride != null) rankVal = o.rankOverride;
    var rankBadge = rankVal !== null
      ? '<span class="trade-rank-badge" style="font-size:14px;font-weight:700;color:var(--accent-cyan);background:rgba(0,220,255,0.08);border:1px solid rgba(0,220,255,0.24);border-radius:8px;padding:3px 10px;white-space:nowrap;">Score ' + fmtLib.formatScore(rankVal, 1) + '</span>'
      : '';

    /* 3. Header badges */
    var symbolBadge = model.symbol ? pill(model.symbol) : '';
    var dteBadge    = model.dte !== null ? pill(model.dte + ' DTE') : '';

    var strikes = [
      model.shortStrike !== null ? 'Short ' + model.shortStrike : null,
      model.longStrike  !== null ? 'Long ' + model.longStrike   : null,
      model.width       !== null ? 'Width ' + model.width        : null,
    ].filter(Boolean).join(' \u00B7 ');

    var premiumText = model.netPremium !== null
      ? model.netPremiumLabel + ': $' + fmtNum(model.netPremium, 2)
      : '';

    /* 3a. Subtitle parts — build from available data */
    var subtitleParts = [];
    if(h.expiration) subtitleParts.push(esc(h.expiration) + (model.dte !== null ? ' (' + model.dte + ' DTE)' : ''));
    if(strikes) subtitleParts.push(esc(strikes));
    if(premiumText) subtitleParts.push(esc(premiumText));
    /* Fallback for equity / stock trades: show underlying price + trend */
    if(!subtitleParts.length && model.underlyingPrice !== null){
      subtitleParts.push('$' + fmtNum(model.underlyingPrice, 2));
    }
    if(!subtitleParts.length && !h.expiration && !strikes){
      /* Stock / no-leg trade — pull trend from raw data */
      var rawTrend = rawTrade.trend || (rawTrade.computed && rawTrade.computed.trend) || '';
      if(rawTrend) subtitleParts.push(esc(String(rawTrend)));
    }
    var subtitleText = subtitleParts.join(' \u00B7 ');

    var tradeKeyDisplay = model.tradeKey
      ? '<span class="trade-key-wrap"><span class="trade-key-label" style="font-size:10px;color:rgba(230,251,255,0.5);font-family:monospace;word-break:break-all;">'
        + esc(model.tradeKey) + '</span>' + copyTradeKeyButton(model.tradeKey) + '</span>'
      : '';

    /* 4. Core metrics (only resolved values) */
    var resolvedCore = model.coreMetrics.filter(function(m){ return m.value !== null; });
    var coreGridItems = resolvedCore.map(function(m){
      return { label: m.label, value: m.display, cssClass: m.tone, dataMetric: m.dataMetric };
    });
    var coreHtml = resolvedCore.length > 0
      ? section('CORE METRICS', metricGrid(coreGridItems), 'section-core')
      : '';

    /* 5. Detail fields (only resolved values) */
    var detailHtml = '';
    var resolvedDetails = model.detailFields.filter(function(m){ return m.value !== null; });
    if(resolvedDetails.length > 0){
      var detailItems = resolvedDetails.map(function(m){
        return { label: m.label, value: m.display, dataMetric: m.dataMetric };
      });
      detailHtml = section('TRADE DETAILS', detailRows(detailItems), 'section-details');
    }

    /* 6. Action buttons — 3 rows, identical to scanner pages */
    var tradeKeyAttr = model.tradeKey ? ' data-trade-key="' + esc(model.tradeKey) + '"' : '';
    var modelBtnLabel = (o.modelStatus === 'running') ? 'Running\u2026' : 'Run Model Analysis';
    var actionsHtml = '<div class="trade-actions">'
      + '<div class="run-row"><button type="button" class="btn btn-run btn-action" data-action="model-analysis"' + tradeKeyAttr + ' title="Run model analysis on this trade">' + modelBtnLabel + '</button></div>'
      + '<div class="trade-model-output" data-model-output' + tradeKeyAttr + ' style="display:none;"></div>'
      + '<div class="actions-row"><button type="button" class="btn btn-exec btn-action" data-action="execute"' + tradeKeyAttr + ' title="Open execution modal">Execute Trade</button>'
      + '<button type="button" class="btn btn-reject btn-action" data-action="reject"' + tradeKeyAttr + ' title="Reject this trade">Reject</button></div>'
      + '<div class="actions-row"><button type="button" class="btn btn-action" data-action="workbench"' + tradeKeyAttr + ' title="Send to Testing Workbench">Send to Testing Workbench</button>'
      + '<button type="button" class="btn btn-action" data-action="data-workbench"' + tradeKeyAttr + ' title="Send to Data Workbench">Send to Data Workbench</button></div>'
      + '</div>';

    /* 7. Debug warnings */
    var warnHtml = '';
    if(debugTrades && model.missingKeys.length > 0){
      warnHtml = '<div class="trade-debug-warn" style="font-size:10px;color:#ffbb33;margin-top:4px;opacity:0.8;">Missing: ' + esc(model.missingKeys.join(', ')) + '</div>';
    }

    /* 8. Collapse state */
    var isExpanded = model.tradeKey ? (expandState[model.tradeKey] === true) : false;
    var openAttr = isExpanded ? ' open' : '';

    /* Chevron SVG */
    var chevronSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>';

    /* 9. Full card HTML */
    return '<div class="trade-card" data-idx="' + idx + '"' + tradeKeyAttr + ' style="margin-bottom:14px;display:flex;flex-direction:column;">'
      + '<details class="trade-card-collapse"' + tradeKeyAttr + openAttr + '>'
      + '<summary class="trade-summary"><div class="trade-header trade-header-click">'
      + '<div class="trade-header-left"><span class="chev">' + chevronSvg + '</span></div>'
      + '<div class="trade-header-center">'
      + '<div class="trade-type" style="display:flex;align-items:center;gap:8px;justify-content:center;">' + symbolBadge + ' ' + dteBadge + ' ' + esc(model.strategyLabel) + '</div>'
      + '<div class="trade-subtitle">' + subtitleText + '</div>'
      + (tradeKeyDisplay ? '<div style="text-align:center;">' + tradeKeyDisplay + '</div>' : '')
      + '</div>'
      + '<div class="trade-header-right">' + rankBadge + '</div>'
      + '</div></summary>'
      + '<div class="trade-body" style="flex:1 1 auto;">' + coreHtml + detailHtml + warnHtml + '</div>'
      + '</details>'
      + actionsHtml
      + '</div>';
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
    // Canonical card renderer
    renderFullCard: renderFullCard,
  };
})();
