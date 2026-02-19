/**
 * BenTrade — Centralised metric resolution for trade objects.
 *
 * Resolution order (per user spec):
 *   1. trade.computed[key]          (preferred)
 *   2. trade.computed_metrics[key]  (fallback)
 *   3. Legacy root-level keys       (last resort)
 *
 * Returns null for any missing / non-finite value — never coerces to 0.
 *
 * Depends on: BenTradeUtils.format  (must be loaded first)
 */
window.BenTradeUtils = window.BenTradeUtils || {};

window.BenTradeUtils.tradeAccessor = (function(){
  'use strict';

  var toNumber = window.BenTradeUtils.format.toNumber;

  /**
   * Central field-resolution map.
   *   computed  – key to look up inside trade.computed / trade.computed_metrics
   *   fallbacks – ordered list of legacy root-level keys
   */
  var FIELD_MAP = {
    /* identity / structure */
    symbol:           { computed: null,                    fallbacks: ['symbol'] },
    strategy:         { computed: null,                    fallbacks: ['strategy_id'] },
    expiration:       { computed: null,                    fallbacks: ['expiration'] },
    short_strike:     { computed: null,                    fallbacks: ['short_strike', 'put_short_strike', 'call_short_strike'] },
    long_strike:      { computed: null,                    fallbacks: ['long_strike', 'put_long_strike', 'call_long_strike'] },

    /* core metrics */
    ev:               { computed: 'expected_value',        fallbacks: ['ev', 'edge'] },
    pop:              { computed: 'pop',                   fallbacks: ['pop'] },
    ror:              { computed: 'return_on_risk',        fallbacks: ['return_on_risk', 'ror'] },
    max_loss:         { computed: 'max_loss',              fallbacks: ['max_loss'] },
    max_profit:       { computed: 'max_profit',            fallbacks: ['max_profit'] },
    net_credit:       { computed: 'net_credit',            fallbacks: ['net_credit', 'credit'] },
    net_debit:        { computed: 'net_debit',             fallbacks: ['net_debit', 'debit'] },
    composite:        { computed: 'trade_quality_score',   fallbacks: ['composite_score', 'trade_quality_score', 'score'] },
    rank:             { computed: 'rank_score',            fallbacks: ['rank', 'rank_score', 'score', 'composite_score', 'trade_quality_score'] },
    break_even:       { computed: 'break_even',            fallbacks: ['break_even'] },
    iv_rv_ratio:      { computed: 'iv_rv_ratio',           fallbacks: ['iv_rv_ratio'] },
    bid_ask_pct:      { computed: 'bid_ask_pct',           fallbacks: ['bid_ask_pct'] },
    underlying_price: { computed: 'underlying_price',      fallbacks: ['underlying_price', 'price'] },
  };

  /* ------------------------------------------------------------------ */

  /**
   * Resolve a numeric metric from a trade object.
   * Uses FIELD_MAP to walk computed → computed_metrics → legacy keys.
   *
   * @param {object} trade  – trade row
   * @param {string} field  – canonical field name (e.g. 'ev', 'pop', 'ror')
   * @returns {number|null}
   */
  function resolve(trade, field){
    if(!trade || typeof trade !== 'object') return null;

    var def = FIELD_MAP[field];
    if(!def){
      // Unknown field — try direct access
      if(window.BenTradeDebug && window.BenTradeDebug.enabled){
        window.BenTradeDebug.log('accessor', 'Unknown field in resolve()', field);
      }
      return toNumber(trade[field]);
    }

    // 1. trade.computed[key]
    if(def.computed){
      var comp = trade.computed;
      if(comp && typeof comp === 'object'){
        var v1 = toNumber(comp[def.computed]);
        if(v1 !== null) return v1;
      }
      // 2. trade.computed_metrics[key]
      var cm = trade.computed_metrics;
      if(cm && typeof cm === 'object'){
        var v2 = toNumber(cm[def.computed]);
        if(v2 !== null) return v2;
      }
    }

    // 3. Legacy root-level keys
    for(var i = 0; i < def.fallbacks.length; i++){
      var v3 = toNumber(trade[def.fallbacks[i]]);
      if(v3 !== null) return v3;
    }

    return null;
  }

  /**
   * Resolve a string field (symbol, strategy, expiration).
   * Returns trimmed string or null.
   */
  function resolveString(trade, field){
    if(!trade || typeof trade !== 'object') return null;
    var def = FIELD_MAP[field];
    var keys = def ? def.fallbacks : [field];
    for(var i = 0; i < keys.length; i++){
      var v = trade[keys[i]];
      if(v != null && String(v).trim()) return String(v).trim();
    }
    return null;
  }

  /**
   * Flexible metric resolution with explicit computed-key and legacy keys.
   * Backwards-compatible with the admin_data_workbench pattern:
   *   metricNumber(trade, 'max_profit', 'max_profit_per_contract', 'max_profit_per_share', 'max_profit')
   *
   * @param {object} trade
   * @param {string} computedKey – key to look up in computed / computed_metrics
   * @param {...string} legacyKeys – root-level fallback keys (rest args)
   * @returns {number|null}
   */
  function metricNumber(trade, computedKey /*, ...legacyKeys */){
    if(!trade || typeof trade !== 'object') return null;

    if(computedKey){
      var comp = trade.computed;
      if(comp && typeof comp === 'object'){
        var v1 = toNumber(comp[computedKey]);
        if(v1 !== null) return v1;
      }
      var cm = trade.computed_metrics;
      if(cm && typeof cm === 'object'){
        var v2 = toNumber(cm[computedKey]);
        if(v2 !== null) return v2;
      }
    }

    for(var i = 2; i < arguments.length; i++){
      var v3 = toNumber(trade[arguments[i]]);
      if(v3 !== null) return v3;
    }
    return null;
  }

  return {
    FIELD_MAP: FIELD_MAP,
    resolve: resolve,
    resolveString: resolveString,
    metricNumber: metricNumber,
  };
})();
