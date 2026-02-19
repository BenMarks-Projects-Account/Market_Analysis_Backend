/**
 * BenTrade — Global Symbol Universe Store
 *
 * Manages the user-modifiable symbol list used by all scanners.
 * Persisted in localStorage; defaults to the canonical SCANNER_SYMBOLS
 * from BenTradeStrategyDefaults if nothing is stored.
 *
 * Exposed as  window.BenTradeSymbolUniverseStore
 *
 * API:
 *   getSymbols()           → string[]  (current list, always uppercase)
 *   addSymbol(sym)         → boolean   (true if added, false if dup/invalid)
 *   removeSymbol(sym)      → boolean   (true if removed)
 *   resetToDefaults()      → void
 *   subscribe(listener)    → unsubscribe function
 */
window.BenTradeSymbolUniverseStore = (function(){
  'use strict';

  const STORAGE_KEY = 'bentrade_symbol_universe_v1';

  /* Canonical defaults from BenTradeStrategyDefaults */
  const FALLBACK = ['SPY', 'QQQ', 'IWM', 'DIA', 'XSP', 'RUT', 'NDX'];

  function _getDefaults(){
    try{
      const df = window.BenTradeStrategyDefaults?.getStrategyDefaults?.('credit_spread');
      if(df?.symbols && Array.isArray(df.symbols) && df.symbols.length) return df.symbols.map(s => String(s).toUpperCase());
    }catch(_e){}
    return FALLBACK.slice();
  }

  /** Basic ticker validation: 1-6 uppercase letters (or ^-prefixed index) */
  function isValidTicker(sym){
    return /^[A-Z\^]{1,6}$/.test(sym);
  }

  let _symbols = [];
  const _listeners = new Set();

  function _load(){
    try{
      const raw = localStorage.getItem(STORAGE_KEY);
      if(raw){
        const arr = JSON.parse(raw);
        if(Array.isArray(arr) && arr.length){
          _symbols = arr.map(s => String(s).toUpperCase()).filter(Boolean);
          return;
        }
      }
    }catch(_e){}
    _symbols = _getDefaults();
  }

  function _save(){
    try{
      localStorage.setItem(STORAGE_KEY, JSON.stringify(_symbols));
    }catch(_e){}
  }

  function _notify(){
    _listeners.forEach(fn => { try{ fn(_symbols.slice()); }catch(_e){} });
  }

  /* ── Public API ── */

  function getSymbols(){
    return _symbols.slice();
  }

  function addSymbol(sym){
    const normalized = String(sym || '').trim().toUpperCase();
    if(!normalized || !isValidTicker(normalized)) return false;
    if(_symbols.includes(normalized)) return false;
    _symbols.push(normalized);
    _save();
    _notify();
    return true;
  }

  function removeSymbol(sym){
    const normalized = String(sym || '').trim().toUpperCase();
    const idx = _symbols.indexOf(normalized);
    if(idx === -1) return false;
    _symbols.splice(idx, 1);
    _save();
    _notify();
    return true;
  }

  function resetToDefaults(){
    _symbols = _getDefaults();
    _save();
    _notify();
  }

  function subscribe(listener){
    if(typeof listener !== 'function') return function(){};
    _listeners.add(listener);
    return function unsubscribe(){
      _listeners.delete(listener);
    };
  }

  /* Init */
  _load();

  return {
    getSymbols,
    addSymbol,
    removeSymbol,
    resetToDefaults,
    subscribe,
    isValidTicker,
  };
})();
