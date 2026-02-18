/**
 * BenTrade — Lightweight debug / assertion utilities.
 *
 * Enabled automatically on localhost / 127.0.0.1 / *.local,
 * or via   localStorage.setItem('bentrade_debug', '1')
 *
 * No dependencies — load before everything else.
 */
window.BenTradeDebug = (function(){
  'use strict';

  var enabled = false;

  function init(){
    try{
      var host = String(location.hostname || '').toLowerCase();
      enabled = host === 'localhost'
        || host === '127.0.0.1'
        || host.endsWith('.local')
        || localStorage.getItem('bentrade_debug') === '1';
    }catch(_err){
      enabled = false;
    }
  }

  /**
   * Log a soft assertion failure.  Does NOT throw.
   * @param {boolean} condition
   * @param {string}  message
   * @param {*}       [context]
   */
  function assert(condition, message, context){
    if(!enabled) return;
    if(!condition){
      console.warn('[BenTrade ASSERT]', message, context !== undefined ? context : '');
    }
  }

  /**
   * Debug-only structured log.
   * @param {string} tag      – module identifier (e.g. 'accessor', 'card')
   * @param {string} message
   * @param {*}      [data]
   */
  function log(tag, message, data){
    if(!enabled) return;
    console.debug('[BenTrade:' + tag + ']', message, data !== undefined ? data : '');
  }

  /**
   * Validate a trade object and log warnings for missing fields.
   * Call once per card render (idempotent, guards via WeakSet).
   */
  var _validated = typeof WeakSet !== 'undefined' ? new WeakSet() : null;

  function validateTradeOnce(trade, context){
    if(!enabled) return;
    if(!trade || typeof trade !== 'object') return;
    if(_validated && _validated.has(trade)) return;
    if(_validated) _validated.add(trade);

    var accessor = window.BenTradeUtils && window.BenTradeUtils.tradeAccessor;
    if(!accessor) return;

    var symbol = accessor.resolveString(trade, 'symbol');
    var strategy = accessor.resolveString(trade, 'strategy');
    var ev = accessor.resolve(trade, 'ev');
    var pop = accessor.resolve(trade, 'pop');

    if(!symbol)   log('validate', 'Trade missing symbol', { context: context, trade_key: trade.trade_key });
    if(!strategy) log('validate', 'Trade missing strategy', { context: context, trade_key: trade.trade_key });

    // Check computed vs legacy sourcing
    var hasComputed = (trade.computed && typeof trade.computed === 'object')
                   || (trade.computed_metrics && typeof trade.computed_metrics === 'object');
    if(!hasComputed && (ev !== null || pop !== null)){
      log('validate', 'Trade has metrics but no computed block — using legacy fields', { context: context, trade_key: trade.trade_key });
    }
  }

  init();

  return {
    get enabled(){ return enabled; },
    assert: assert,
    log: log,
    validateTradeOnce: validateTradeOnce,
  };
})();
