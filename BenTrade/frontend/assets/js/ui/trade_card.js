window.BenTradeTradeCard = (function(){
  function buildTradeKey(trade){
    const safe = trade || {};
    const underlying = String(safe.underlying || safe.underlying_symbol || '').toUpperCase();
    const expiration = String(safe.expiration || '');
    const spreadType = String(safe.spread_type || '');
    const shortStrike = String(safe.short_strike ?? '');
    const longStrike = String(safe.long_strike ?? '');
    const dte = String(safe.dte ?? '');
    return `${underlying}|${expiration}|${spreadType}|${shortStrike}|${longStrike}|${dte}`;
  }

  return {
    buildTradeKey,
  };
})();
