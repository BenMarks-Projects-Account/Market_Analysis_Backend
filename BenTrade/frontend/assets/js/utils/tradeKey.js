window.BenTradeUtils = window.BenTradeUtils || {};

window.BenTradeUtils.tradeKey = (function buildTradeKeyUtils(){
  function normalizeStrike(value){
    if(value === null || value === undefined || value === ''){
      return 'NA';
    }
    const n = Number(value);
    if(Number.isNaN(n)){
      return String(value).trim() || 'NA';
    }
    if(Number.isInteger(n)){
      return String(n);
    }
    return String(n).replace(/\.0+$/, '').replace(/(\.\d*?[1-9])0+$/, '$1');
  }

  function tradeKey(input){
    const source = input || {};
    const underlying = String(source.underlying ?? source.symbol ?? 'NA').trim().toUpperCase() || 'NA';
    const expiration = String(source.expiration ?? source.exp ?? 'NA').trim() || 'NA';
    const spreadType = String(source.spread_type ?? source.strategy ?? 'NA').trim().toLowerCase() || 'NA';
    const shortStrike = normalizeStrike(source.short_strike ?? source.shortStrike ?? source.short);
    const longStrike = normalizeStrike(source.long_strike ?? source.longStrike ?? source.long);
    const dte = source.dte === null || source.dte === undefined || source.dte === ''
      ? 'NA'
      : String(source.dte).trim() || 'NA';

    return `${underlying}|${expiration}|${spreadType}|${shortStrike}|${longStrike}|${dte}`;
  }

  return {
    normalizeStrike,
    tradeKey,
  };
})();
