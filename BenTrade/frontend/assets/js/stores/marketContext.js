/**
 * Shared Market Context Store — single source of truth for real-time
 * market metrics used by both Home and News & Sentiment dashboards.
 *
 * Normalizes all market metrics into a consistent shape:
 *   {
 *     value:          number|null,  // current_value for display
 *     previous_close: number|null,  // prior session close
 *     source:         string,       // "tradier"|"finnhub"|"fred"
 *     freshness:      string,       // "intraday"|"delayed"|"eod"
 *     is_intraday:    boolean,
 *     fetched_at:     string|null,  // ISO timestamp
 *     observation_date: string|null // YYYY-MM-DD for EOD
 *   }
 *
 * Two consumers:
 *   - Home dashboard (macro tiles)
 *   - News & Sentiment dashboard (macro stress card)
 *
 * VIX Canary: Compares VIX values from different rendering paths
 * to detect freshness mismatches.
 */
window.BenTradeMarketContext = (function() {
  'use strict';

  var _context = null;      // latest normalized context
  var _listeners = [];      // onChange callbacks
  var _fetchPromise = null;  // dedup concurrent fetches

  // ── Normalize a flat macro response from /api/stock/macro ─────
  function normalizeFromFlatMacro(macro) {
    if (!macro || typeof macro !== 'object') return null;
    var freshness = macro._freshness || {};

    function _buildMetric(key, value) {
      var f = freshness[key] || {};
      return {
        value: (value != null && value !== '') ? Number(value) : null,
        previous_close: f.previous_close != null ? Number(f.previous_close) : null,
        source: f.source || 'unknown',
        freshness: f.freshness || (f.is_intraday ? 'intraday' : 'eod'),
        is_intraday: !!f.is_intraday,
        fetched_at: f.fetched_at || null,
        observation_date: f.observation_date || null,
        source_timestamp: f.source_timestamp || null,
      };
    }

    return {
      vix: _buildMetric('vix', macro.vix),
      ten_year_yield: _buildMetric('ten_year_yield', macro.ten_year_yield),
      two_year_yield: _buildMetric('two_year_yield', macro.two_year_yield),
      fed_funds_rate: _buildMetric('fed_funds_rate', macro.fed_funds_rate),
      oil_wti: _buildMetric('oil_wti', macro.oil_wti),
      usd_index: _buildMetric('usd_index', macro.usd_index),
      cpi_yoy: _buildMetric('cpi_yoy', macro.cpi_yoy),
      yield_curve_spread: macro.yield_curve_spread,
      _generated_at: macro._generated_at || null,
      _normalized_at: new Date().toISOString(),
    };
  }

  // ── Normalize N&S macro_context into same shape ───────────────
  function normalizeFromNsMacro(macroCtx) {
    if (!macroCtx || typeof macroCtx !== 'object') return null;
    var freshness = macroCtx._freshness || {};

    // N&S uses slightly different field names (us_10y_yield vs ten_year_yield)
    function _buildMetric(nsKey, ctxKey, value) {
      var f = freshness[nsKey] || freshness[ctxKey] || {};
      return {
        value: (value != null && value !== '') ? Number(value) : null,
        previous_close: f.previous_close != null ? Number(f.previous_close) : null,
        source: f.source || 'unknown',
        freshness: f.freshness || (f.is_intraday ? 'intraday' : 'eod'),
        is_intraday: !!f.is_intraday,
        fetched_at: f.fetched_at || null,
        observation_date: f.observation_date || null,
        source_timestamp: f.source_timestamp || null,
      };
    }

    return {
      vix: _buildMetric('vix', 'vix', macroCtx.vix),
      ten_year_yield: _buildMetric('us_10y_yield', 'ten_year_yield', macroCtx.us_10y_yield),
      two_year_yield: _buildMetric('us_2y_yield', 'two_year_yield', macroCtx.us_2y_yield),
      fed_funds_rate: _buildMetric('fed_funds_rate', 'fed_funds_rate', macroCtx.fed_funds_rate),
      oil_wti: _buildMetric('oil_wti', 'oil_wti', macroCtx.oil_wti),
      usd_index: _buildMetric('usd_index', 'usd_index', macroCtx.usd_index),
      cpi_yoy: _buildMetric('cpi_yoy', 'cpi_yoy', null),
      yield_curve_spread: macroCtx.yield_curve_spread,
      stress_level: macroCtx.stress_level,
      _generated_at: macroCtx.as_of || null,
      _normalized_at: new Date().toISOString(),
    };
  }

  // ── Core: update context and notify listeners ─────────────────
  function setContext(normalized) {
    if (!normalized) return;
    _context = normalized;
    console.log('[MARKET_CONTEXT] refresh_success', {
      vix: normalized.vix ? normalized.vix.value : null,
      vix_source: normalized.vix ? normalized.vix.source : null,
      vix_freshness: normalized.vix ? normalized.vix.freshness : null,
      generated_at: normalized._generated_at,
    });
    _listeners.forEach(function(fn) {
      try { fn(normalized); } catch(e) { console.warn('[MARKET_CONTEXT] listener error', e); }
    });
  }

  // ── Fetch from /api/stock/macro (canonical endpoint) ──────────
  function fetchAndUpdate() {
    if (_fetchPromise) return _fetchPromise;

    console.log('[MARKET_CONTEXT] metric_fetch_start');
    var api = window.BenTradeApi;
    if (!api || !api.getMacroIndicators) {
      console.warn('[MARKET_CONTEXT] refresh_failure reason=no_api');
      return Promise.resolve(null);
    }

    _fetchPromise = api.getMacroIndicators()
      .then(function(macro) {
        var normalized = normalizeFromFlatMacro(macro);
        if (normalized) setContext(normalized);
        _fetchPromise = null;
        return normalized;
      })
      .catch(function(err) {
        console.warn('[MARKET_CONTEXT] refresh_failure', err);
        _fetchPromise = null;
        return null;
      });

    return _fetchPromise;
  }

  // ── VIX canary: compare card value vs chart value ─────────────
  function vixCanaryCheck(chartValue, cardValue) {
    if (chartValue == null || cardValue == null) return;
    var chart = Number(chartValue);
    var card = Number(cardValue);
    if (isNaN(chart) || isNaN(card) || chart === 0) return;

    var divergence = Math.abs(chart - card);
    var divergencePct = (divergence / card) * 100;

    console.log('[MARKET_CONTEXT] vix_chart_value=' + chart.toFixed(2));
    console.log('[MARKET_CONTEXT] vix_card_value=' + card.toFixed(2));

    // Flag if chart and card diverge by more than 10%
    if (divergencePct > 10) {
      console.warn(
        '[MARKET_CONTEXT] vix_freshness_mismatch chart=' + chart.toFixed(2) +
        ' card=' + card.toFixed(2) + ' divergence_pct=' + divergencePct.toFixed(1) +
        '% — chart and card may be using different freshness layers'
      );
    }
  }

  // ── Freshness badge HTML helper ───────────────────────────────
  function freshnessTag(metric) {
    if (!metric) return '';
    var f = metric.freshness || (metric.is_intraday ? 'intraday' : 'eod');
    if (f === 'intraday') {
      return '<span class="mc-freshness-tag mc-freshness-live" title="Intraday (' + (metric.source || '') + ')">live</span>';
    }
    var title = metric.observation_date ? 'EOD (' + metric.observation_date + ')' : 'End-of-day';
    if (f === 'delayed') {
      return '<span class="mc-freshness-tag mc-freshness-delayed" title="' + title + '">delayed</span>';
    }
    return '<span class="mc-freshness-tag mc-freshness-eod" title="' + title + '">eod</span>';
  }

  // ── Public API ────────────────────────────────────────────────
  return {
    /** Get the current cached context (may be null if not yet loaded) */
    getContext: function() { return _context; },

    /** Normalize data from /api/stock/macro response */
    normalizeFromFlatMacro: normalizeFromFlatMacro,

    /** Normalize data from /api/news-sentiment macro_context response */
    normalizeFromNsMacro: normalizeFromNsMacro,

    /** Update the shared context from already-normalized data */
    setContext: setContext,

    /** Fetch from /api/stock/macro and update the shared context */
    fetchAndUpdate: fetchAndUpdate,

    /** Register a listener for context changes */
    onChange: function(fn) {
      if (typeof fn === 'function') _listeners.push(fn);
    },

    /** Remove a listener */
    offChange: function(fn) {
      _listeners = _listeners.filter(function(f) { return f !== fn; });
    },

    /** VIX canary: compare chart vs card values for freshness audit */
    vixCanaryCheck: vixCanaryCheck,

    /** Generate a freshness badge HTML string for a metric */
    freshnessTag: freshnessTag,
  };
})();
