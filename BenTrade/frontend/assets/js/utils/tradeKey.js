window.BenTradeUtils = window.BenTradeUtils || {};

window.BenTradeUtils.tradeKey = (function buildTradeKeyUtils(){
  const STRATEGY_ALIASES = {
    credit_put_spread: 'put_credit',
    put_credit: 'put_credit',
    credit_call_spread: 'call_credit',
    call_credit: 'call_credit',
    debit_put_spread: 'put_debit',
    put_debit: 'put_debit',
    debit_call_spread: 'call_debit',
    call_debit: 'call_debit',
    cash_secured_put: 'csp',
    csp: 'csp',
    covered_call: 'covered_call',
    debit_call_butterfly: 'butterfly_debit',
    debit_put_butterfly: 'butterfly_debit',
    debit_butterfly: 'butterfly_debit',
    butterfly_debit: 'butterfly_debit',
    butterflies: 'butterfly_debit',
    iron_condor: 'iron_condor',
    calendar_spread: 'calendar_spread',
    calendar_call_spread: 'calendar_call_spread',
    calendar_put_spread: 'calendar_put_spread',
    single: 'single',
    long_call: 'long_call',
    long_put: 'long_put',
  };

  function canonicalStrategy(strategy){
    const key = String(strategy || '').trim().toLowerCase();
    if(!key) return 'NA';
    return STRATEGY_ALIASES[key] || key;
  }

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
    const spreadType = canonicalStrategy(source.spread_type ?? source.strategy ?? 'NA');
    const shortStrike = normalizeStrike(source.short_strike ?? source.shortStrike ?? source.short);
    const longStrike = normalizeStrike(source.long_strike ?? source.longStrike ?? source.long);
    const dte = source.dte === null || source.dte === undefined || source.dte === ''
      ? 'NA'
      : String(source.dte).trim() || 'NA';

    return `${underlying}|${expiration}|${spreadType}|${shortStrike}|${longStrike}|${dte}`;
  }

  function canonicalTradeKey(value){
    const raw = String(value || '').trim();
    if(!raw) return '';
    const parts = raw.split('|');
    if(parts.length !== 6) return raw;
    return tradeKey({
      underlying: parts[0],
      expiration: parts[1],
      spread_type: parts[2],
      short_strike: parts[3],
      long_strike: parts[4],
      dte: parts[5],
    });
  }

  return {
    canonicalStrategy,
    canonicalTradeKey,
    normalizeStrike,
    tradeKey,
  };
})();
