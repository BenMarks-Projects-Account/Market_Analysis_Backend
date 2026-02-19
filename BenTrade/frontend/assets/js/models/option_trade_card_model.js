/**
 * BenTrade — Options trade → card view-model mapper.
 *
 * Converts a raw API trade object into a clean OptionTradeCardModel
 * that the card renderer consumes.  The card NEVER reads raw JSON
 * keys directly — all access goes through this mapper.
 *
 * Resolution order (4-tier):
 *   1. trade.computed[computedKey]
 *   2. trade.computed_metrics[computedKey]
 *   3. trade.details[detailsKey || computedKey]
 *   4. trade[rootFallback]   (legacy root-level keys)
 *
 * Missing values → null in model → "N/A" in card → debug-logged.
 *
 * Depends on:
 *   - BenTradeUtils.format          (toNumber, formatting fns)
 *   - BenTradeStrategyCardConfig    (forStrategy, metric descriptors)
 */
window.BenTradeOptionTradeCardModel = (function () {
  'use strict';

  var fmt    = window.BenTradeUtils.format;
  var cfgMod = window.BenTradeStrategyCardConfig;

  /* ── Debug flag ──────────────────────────────────────────────── */

  function _isDebug() {
    if (window.BENTRADE_DEBUG_TRADES) return true;
    try { return new URLSearchParams(window.location.search).get('debug_trades') === '1'; } catch (_) { return false; }
  }

  /* ── 4-tier metric resolver ────────────────────────────────── */

  /**
   * Resolve a numeric value from a trade object using a metric descriptor.
   *
   * @param {object} trade          – raw API trade object
   * @param {object} metricDesc     – { computedKey, detailsKey?, rootFallbacks? }
   * @returns {number|null}
   */
  function _resolveMetric(trade, metricDesc) {
    var computedKey  = metricDesc.computedKey || null;
    var detailsKey   = metricDesc.detailsKey || null;
    var fallbacks    = metricDesc.rootFallbacks || [];

    // 1. computed
    if (computedKey) {
      var comp = trade.computed;
      if (comp && typeof comp === 'object') {
        var v1 = fmt.toNumber(comp[computedKey]);
        if (v1 !== null) return v1;
      }
      // 2. computed_metrics
      var cm = trade.computed_metrics;
      if (cm && typeof cm === 'object') {
        var v2 = fmt.toNumber(cm[computedKey]);
        if (v2 !== null) return v2;
      }
    }

    // 3. details
    var dk = detailsKey || computedKey;
    if (dk) {
      var det = trade.details;
      if (det && typeof det === 'object') {
        var v3 = fmt.toNumber(det[dk]);
        if (v3 !== null) return v3;
      }
    }

    // 4. root fallbacks
    for (var i = 0; i < fallbacks.length; i++) {
      var v4 = fmt.toNumber(trade[fallbacks[i]]);
      if (v4 !== null) return v4;
    }

    // 5. last resort: try key itself at root
    var v5 = fmt.toNumber(trade[metricDesc.key]);
    if (v5 !== null) return v5;

    return null;
  }

  /* ── Formatting dispatch ──────────────────────────────────── */

  /**
   * Format a numeric value according to the format type string.
   * Returns a display string, or 'N/A' for null values.
   */
  function _formatValue(value, formatType) {
    if (value === null || value === undefined) return 'N/A';
    switch (formatType) {
      case 'pct':     return fmt.pct(value, 1);
      case 'dollars': return fmt.dollars(value, 2);
      case 'money':   return fmt.money(value);
      case 'score':   return fmt.formatScore(value, 1);
      case 'int':     return fmt.num(value, 0);
      case 'num':     return fmt.num(value, 2);
      default:        return fmt.num(value, 2);
    }
  }

  /* ── Identity / header field resolution ──────────────────── */

  function _resolveHeader(trade) {
    var pills = (trade.pills && typeof trade.pills === 'object') ? trade.pills : {};
    var strategyId = String(trade.strategy_id || trade.strategy || trade.spread_type || '').toLowerCase();
    var tradeKey = String(trade.trade_key || trade.trade_id || '');

    return {
      /* core identity */
      tradeKey:        tradeKey,
      tradeId:         String(trade.trade_id || trade.trade_key || ''),
      symbol:          String(trade.symbol || trade.underlying || trade.underlying_symbol || '').toUpperCase(),
      strategyId:      strategyId,
      strategyLabel:   pills.strategy_label || _formatTradeType(strategyId),
      expiration:      String(trade.expiration || ''),
      dte:             fmt.toNumber(trade.dte),

      /* strikes */
      shortStrike:     fmt.toNumber(trade.short_strike),
      longStrike:      fmt.toNumber(trade.long_strike),
      width:           fmt.toNumber(trade.width),
      underlyingPrice: fmt.toNumber(trade.underlying_price || trade.price),

      /* pricing – strategy-aware net credit vs debit */
      netCredit:       fmt.toNumber(trade.net_credit),
      netDebit:        fmt.toNumber(trade.net_debit),
    };
  }

  /* ── Legs derivation ─────────────────────────────────────── */

  /**
   * Derive structured legs from the trade payload.
   * Real leg arrays are rare in current data; we synthesise from
   * strike / strategy fields when possible.
   */
  function _resolveLegs(trade, header) {
    /* 1. Use explicit legs array if provided */
    if (Array.isArray(trade.legs) && trade.legs.length) {
      return trade.legs;
    }

    var sid = header.strategyId;
    var legs = [];

    /* 2. Iron condor – 4-leg composite */
    if (sid === 'iron_condor') {
      var ps = fmt.toNumber(trade.put_short_strike);
      var pl = fmt.toNumber(trade.put_long_strike);
      var cs = fmt.toNumber(trade.call_short_strike);
      var cl = fmt.toNumber(trade.call_long_strike);
      if (ps !== null) legs.push({ strike: ps, side: 'sell', callput: 'put', qty: 1 });
      if (pl !== null) legs.push({ strike: pl, side: 'buy',  callput: 'put', qty: 1 });
      if (cs !== null) legs.push({ strike: cs, side: 'sell', callput: 'call', qty: 1 });
      if (cl !== null) legs.push({ strike: cl, side: 'buy',  callput: 'call', qty: 1 });
      return legs.length ? legs : null;
    }

    /* 3. Butterfly – 3-leg composite */
    if (sid === 'butterfly_debit' || sid === 'butterflies') {
      var center = fmt.toNumber(trade.center_strike || trade.short_strike);
      var lower  = fmt.toNumber(trade.lower_strike);
      var upper  = fmt.toNumber(trade.upper_strike);
      if (center !== null) legs.push({ strike: center, side: 'sell', callput: 'put', qty: 2 });
      if (lower  !== null) legs.push({ strike: lower,  side: 'buy',  callput: 'put', qty: 1 });
      if (upper  !== null) legs.push({ strike: upper,  side: 'buy',  callput: 'put', qty: 1 });
      return legs.length ? legs : null;
    }

    /* 4. Simple 2-leg spread (credit / debit) */
    if (header.shortStrike !== null || header.longStrike !== null) {
      var isCredit = sid.indexOf('credit') !== -1 || sid === 'csp';
      var cp = sid.indexOf('call') !== -1 ? 'call' : 'put';
      if (header.shortStrike !== null) legs.push({ strike: header.shortStrike, side: isCredit ? 'sell' : 'buy',  callput: cp, qty: 1 });
      if (header.longStrike  !== null) legs.push({ strike: header.longStrike,  side: isCredit ? 'buy'  : 'sell', callput: cp, qty: 1 });
      return legs;
    }

    return null; /* no fabrication — null over incorrect */
  }

  /* ── Source metadata ────────────────────────────────────────── */

  function _resolveSourceMeta(trade) {
    return {
      reportFile:   trade._source_report_file || trade.report_file || trade._report_file || null,
      reportId:     trade._source_report_id   || trade.report_id   || null,
      generatedAt:  trade._source_generated_at || trade.generated_at || null,
      reportName:   trade._source_report_name  || trade.report_name  || null,
    };
  }

  function _formatTradeType(val) {
    return String(val || 'trade').replaceAll('_', ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  /* ── Build metric items array ─────────────────────────────── */

  /**
   * @param {object}   trade       – raw trade
   * @param {object[]} descriptors – array of metric descriptors from config
   * @returns {{ items: object[], missing: string[] }}
   */
  function _buildMetricItems(trade, descriptors) {
    var items = [];
    var missing = [];

    for (var i = 0; i < descriptors.length; i++) {
      var desc = descriptors[i];
      var value = _resolveMetric(trade, desc);
      var display = _formatValue(value, desc.format);
      var tc = window.BenTradeTradeCard;
      var tone = tc ? tc.toneClass(value, desc.toneOpts) : (value !== null && value >= 0 ? 'positive' : (value !== null ? 'negative' : 'neutral'));

      items.push({
        key:        desc.key,
        label:      desc.label,
        value:      value,
        display:    display,
        tone:       tone,
        dataMetric: desc.key,
      });

      if (value === null) {
        missing.push(desc.key);
      }
    }

    return { items: items, missing: missing };
  }

  /* ── Helpers ───────────────────────────────────────────────── */

  /** Find a metric item in an items array by key. */
  function _findItem(items, key) {
    for (var i = 0; i < items.length; i++) {
      if (items[i].key === key) return items[i];
    }
    return null;
  }

  /* ── Pills ────────────────────────────────────────────────── */

  function _buildPills(trade, header) {
    var pills = [];
    if (header.symbol) pills.push({ text: header.symbol, variant: null });
    if (header.dte !== null) pills.push({ text: header.dte + ' DTE', variant: null });

    var tradeOi = (trade.pills && trade.pills.oi) || (trade.computed && trade.computed.open_interest) || trade.open_interest;
    var tradeVol = (trade.pills && trade.pills.vol) || (trade.computed && trade.computed.volume) || trade.volume;
    if (fmt.toNumber(tradeOi) !== null) pills.push({ text: 'OI ' + fmt.num(tradeOi, 0), variant: null });
    if (fmt.toNumber(tradeVol) !== null) pills.push({ text: 'Vol ' + fmt.num(tradeVol, 0), variant: null });

    var regime = (trade.details && trade.details.market_regime) || trade.market_regime;
    if (regime) pills.push({ text: String(regime), variant: 'warn' });

    return pills;
  }

  /* ================================================================
   *  mapOptionTradeToCardModel  —  THE main entry point
   * ================================================================ */

  /**
   * Map a raw API trade object to a clean card view-model.
   *
   * @param {object} rawTrade    – trade from the API (post strip_legacy_fields)
   * @param {string} [strategyHint] – optional strategy ID override
   * @returns {OptionTradeCardModel}
   */
  function mapOptionTradeToCardModel(rawTrade, strategyHint) {
    var trade = (rawTrade && typeof rawTrade === 'object') ? rawTrade : {};
    var header = _resolveHeader(trade);
    var effectiveStrategy = strategyHint || header.strategyId;
    var config = cfgMod.forStrategy(effectiveStrategy);

    // Derive legs
    var legs = _resolveLegs(trade, header);

    // Source metadata
    var source = _resolveSourceMeta(trade);

    // Strategy-aware net credit / debit label
    var isDebit = effectiveStrategy.indexOf('debit') !== -1;
    var netPremium = isDebit ? header.netDebit : header.netCredit;
    var netPremiumLabel = isDebit ? 'Net Debit' : 'Net Credit';
    // Fallback: if the primary premium is null, try the other side
    if (netPremium === null) {
      netPremium = isDebit ? header.netCredit : header.netDebit;
      if (netPremium !== null) netPremiumLabel = isDebit ? 'Net Credit' : 'Net Debit';
    }

    // Build core metrics
    var coreResult = _buildMetricItems(trade, config.coreMetrics || []);

    // Client-side ev_to_risk fallback: if the backend did not provide it,
    // compute from expectedValue / |maxLoss| so the card always shows it.
    var evItem  = _findItem(coreResult.items, 'expected_value');
    var mlItem  = _findItem(coreResult.items, 'max_loss');
    var evrItem = _findItem(coreResult.items, 'ev_to_risk');
    if (evrItem && evrItem.value === null && evItem && evItem.value !== null && mlItem && mlItem.value !== null && Math.abs(mlItem.value) > 0) {
      evrItem.value   = evItem.value / Math.abs(mlItem.value);
      evrItem.display = _formatValue(evrItem.value, 'num');
      evrItem.tone    = (window.BenTradeTradeCard ? window.BenTradeTradeCard.toneClass(evrItem.value, null) : (evrItem.value >= 0 ? 'positive' : 'negative'));
      // Remove from missing list
      var misIdx = coreResult.missing.indexOf('ev_to_risk');
      if (misIdx !== -1) coreResult.missing.splice(misIdx, 1);
    }

    // Build detail fields
    var detailResult = _buildMetricItems(trade, config.detailFields || []);

    // Also check ev_to_risk in detail fields
    var evrDetail = _findItem(detailResult.items, 'ev_to_risk');
    if (evrDetail && evrDetail.value === null) {
      var evVal = evItem ? evItem.value : null;
      var mlVal = mlItem ? mlItem.value : null;
      // Try resolving from the just-built core metrics if not found yet
      if (evVal === null) { var evR = _resolveMetric(trade, cfgMod.SHARED.expected_value); if (evR !== null) evVal = evR; }
      if (mlVal === null) { var mlR = _resolveMetric(trade, cfgMod.SHARED.max_loss);       if (mlR !== null) mlVal = mlR; }
      if (evVal !== null && mlVal !== null && Math.abs(mlVal) > 0) {
        evrDetail.value   = evVal / Math.abs(mlVal);
        evrDetail.display = _formatValue(evrDetail.value, 'num');
        evrDetail.tone    = (window.BenTradeTradeCard ? window.BenTradeTradeCard.toneClass(evrDetail.value, null) : (evrDetail.value >= 0 ? 'positive' : 'negative'));
        var dMisIdx = detailResult.missing.indexOf('ev_to_risk');
        if (dMisIdx !== -1) detailResult.missing.splice(dMisIdx, 1);
      }
    }

    // Build pills
    var pills = _buildPills(trade, header);

    // Aggregate missing keys
    var allMissing = coreResult.missing.concat(detailResult.missing);

    // Check required keys
    var missingRequired = [];
    var requiredKeys = config.requiredKeys || [];
    for (var i = 0; i < requiredKeys.length; i++) {
      if (allMissing.indexOf(requiredKeys[i]) !== -1) {
        missingRequired.push(requiredKeys[i]);
      }
    }

    // Debug-log missing keys per strategy
    if (_isDebug() && allMissing.length > 0) {
      console.warn(
        '[DEBUG_TRADES:MODEL_MAPPER] ' + header.symbol + ' ' + effectiveStrategy +
        ' missing ' + allMissing.length + ' keys: ' + allMissing.join(', ') +
        (missingRequired.length ? '  ⚠ REQUIRED missing: ' + missingRequired.join(', ') : '')
      );
    }

    return {
      /* ── identity (never reach into _raw for these) ──────── */
      tradeKey:        header.tradeKey,
      tradeId:         header.tradeId,
      symbol:          header.symbol,
      strategyId:      effectiveStrategy,
      strategyLabel:   config.strategyLabel || header.strategyLabel,
      expiration:      header.expiration,
      dte:             header.dte,
      legs:            legs,
      width:           header.width,
      netPremium:      netPremium,
      netPremiumLabel: netPremiumLabel,
      underlyingPrice: header.underlyingPrice,
      shortStrike:     header.shortStrike,
      longStrike:      header.longStrike,

      /* ── source metadata ─────────────────────────────────── */
      source:          source,

      /* ── header (backward-compat — callers may still use) ── */
      header:          header,

      /* ── metric blocks (pre-resolved, pre-formatted) ─────── */
      coreMetrics:     coreResult.items,
      detailFields:    detailResult.items,
      pills:           pills,

      /* ── diagnostics ─────────────────────────────────────── */
      missingKeys:     allMissing,
      missingRequired: missingRequired,
      hasAllRequired:  missingRequired.length === 0,

      /* ── pass-through for edge cases only ────────────────── */
      _raw:            trade,
    };
  }

  /* ================================================================
   *  buildTradeActionPayload  —  identity bundle for action buttons
   * ================================================================ */

  /**
   * Build a clean payload object containing all identity fields
   * needed by action handlers (execute, workbench, reject, etc.).
   * Consumers should NEVER reach into _raw; this payload is the
   * single source of truth for actions.
   *
   * @param {object} model – the OptionTradeCardModel returned by map()
   * @returns {object}
   */
  function buildTradeActionPayload(model) {
    if (!model || typeof model !== 'object') return {};
    return {
      tradeKey:        model.tradeKey        || '',
      tradeId:         model.tradeId         || '',
      symbol:          model.symbol          || '',
      strategyId:      model.strategyId      || '',
      strategyLabel:   model.strategyLabel   || '',
      expiration:      model.expiration      || '',
      dte:             model.dte,
      legs:            model.legs            || null,
      width:           model.width,
      netPremium:      model.netPremium,
      netPremiumLabel: model.netPremiumLabel  || '',
      underlyingPrice: model.underlyingPrice,
      shortStrike:     model.shortStrike,
      longStrike:      model.longStrike,
      source:          model.source          || {},
    };
  }

  /* ── Public API ───────────────────────────────────────────── */

  return {
    map:                     mapOptionTradeToCardModel,
    buildTradeActionPayload: buildTradeActionPayload,
    resolveMetric:           _resolveMetric,
    formatValue:             _formatValue,
  };
})();
