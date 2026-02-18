window.BenTradeTradeCard = (function(){
  function resolveTradeKey(trade){
    return String(trade?.trade_key || trade?._trade_key || '').trim();
  }

  function buildTradeKey(trade){
    const safe = trade || {};
    const util = window.BenTradeUtils?.tradeKey;
    if(util?.tradeKey){
      return util.tradeKey({
        underlying: safe.underlying || safe.underlying_symbol || safe.symbol,
        expiration: safe.expiration,
        spread_type: safe.spread_type || safe.strategy,
        short_strike: safe.short_strike,
        long_strike: safe.long_strike,
        dte: safe.dte,
      });
    }
    const underlying = String(safe.underlying || safe.underlying_symbol || '').toUpperCase();
    const expiration = String(safe.expiration || '');
    const spreadType = String(safe.spread_type || safe.strategy || '');
    const shortStrike = String(safe.short_strike ?? '');
    const longStrike = String(safe.long_strike ?? '');
    const dte = String(safe.dte ?? '');
    return `${underlying}|${expiration}|${spreadType}|${shortStrike}|${longStrike}|${dte}`;
  }

  function openDataWorkbenchByTrade(trade, options){
    const opts = (options && typeof options === 'object') ? options : {};
    const key = resolveTradeKey(trade);
    if(!key){
      if(typeof opts.onMissingTradeKey === 'function'){
        try{ opts.onMissingTradeKey(trade || {}); }catch(_err){}
      }
      return false;
    }

    const encoded = encodeURIComponent(key);
    window.location.hash = `#/admin/data-workbench?trade_key=${encoded}`;
    return true;
  }

  return {
    resolveTradeKey,
    buildTradeKey,
    openDataWorkbenchByTrade,
  };
})();
