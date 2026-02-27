/**
 * BenTrade — Shared Model Analysis State Store.
 *
 * Persists model analysis results keyed by trade key (or stable identifier)
 * so they survive card re-renders, auto-refresh cycles, and navigation.
 *
 * State shape per entry:
 *   { status: 'idle'|'running'|'success'|'error',
 *     startedAt: number|null,
 *     finishedAt: number|null,
 *     result: NormalizedModelAnalysis|null,
 *     error: string|null }
 *
 * Persists to sessionStorage so results survive within a browser tab session.
 *
 * Depends on: BenTradeModelAnalysis (for parse/render)
 */
window.BenTradeModelAnalysisStore = (function(){
  'use strict';

  var STORAGE_KEY = 'bentrade_model_analysis_v1';

  /* In-memory cache — primary source */
  var _store = {};

  /* ── Persistence ── */
  function _save(){
    try{
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(_store));
    }catch(_err){
      /* quota exceeded or private mode — degrade silently */
    }
  }

  function _load(){
    try{
      var raw = sessionStorage.getItem(STORAGE_KEY);
      if(raw){
        var parsed = JSON.parse(raw);
        if(parsed && typeof parsed === 'object'){
          _store = parsed;
          /* Fix any stale "running" entries from a crashed tab */
          Object.keys(_store).forEach(function(key){
            if(_store[key] && _store[key].status === 'running'){
              _store[key].status = 'error';
              _store[key].error = 'Interrupted (page reload during analysis)';
            }
          });
        }
      }
    }catch(_err){
      _store = {};
    }
  }

  /* Load on module init */
  _load();

  /* ── Public API ── */

  /**
   * Get the analysis state for a trade key.
   * Returns the entry object or null if not found.
   */
  function get(tradeKey){
    if(!tradeKey) return null;
    return _store[tradeKey] || null;
  }

  /**
   * Get the full store (e.g. for iteration).
   */
  function getAll(){
    return _store;
  }

  /**
   * Mark a trade key as "running" (analysis in progress).
   */
  function setRunning(tradeKey){
    if(!tradeKey) return;
    _store[tradeKey] = {
      status: 'running',
      startedAt: Date.now(),
      finishedAt: null,
      result: null,
      error: null,
    };
    _save();
    _notify(tradeKey);
    console.debug('[ModelStore] setRunning', tradeKey);
  }

  /**
   * Mark a trade key as "success" with normalized result.
   * @param {string} tradeKey
   * @param {object} normalizedResult — output of BenTradeModelAnalysis.parse()
   */
  function setSuccess(tradeKey, normalizedResult){
    if(!tradeKey) return;
    var prev = _store[tradeKey] || {};
    _store[tradeKey] = {
      status: 'success',
      startedAt: prev.startedAt || null,
      finishedAt: Date.now(),
      result: normalizedResult || null,
      error: null,
    };
    _save();
    _notify(tradeKey);
    console.debug('[ModelStore] setSuccess', tradeKey, normalizedResult?.recommendation);
  }

  /**
   * Mark a trade key as "error".
   */
  function setError(tradeKey, errorMessage){
    if(!tradeKey) return;
    var prev = _store[tradeKey] || {};
    _store[tradeKey] = {
      status: 'error',
      startedAt: prev.startedAt || null,
      finishedAt: Date.now(),
      result: null,
      error: String(errorMessage || 'Unknown error'),
    };
    _save();
    _notify(tradeKey);
    console.debug('[ModelStore] setError', tradeKey, errorMessage);
  }

  /**
   * Clear analysis for a single trade key.
   */
  function clear(tradeKey){
    if(!tradeKey) return;
    delete _store[tradeKey];
    _save();
    _notify(tradeKey);
  }

  /**
   * Clear all stored analyses.
   */
  function clearAll(){
    _store = {};
    _save();
    _notify(null);
    console.debug('[ModelStore] clearAll');
  }

  /* ── Listeners (simple observer pattern) ── */
  var _listeners = [];

  /**
   * Subscribe to changes. Callback receives (tradeKey, entry).
   * Returns an unsubscribe function.
   */
  function subscribe(fn){
    if(typeof fn !== 'function') return function(){};
    _listeners.push(fn);
    return function unsubscribe(){
      _listeners = _listeners.filter(function(f){ return f !== fn; });
    };
  }

  function _notify(tradeKey){
    var entry = tradeKey ? (_store[tradeKey] || null) : null;
    _listeners.forEach(function(fn){
      try{ fn(tradeKey, entry); }catch(_err){}
    });
  }

  /**
   * Hydrate all model outputs in a DOM container.
   * Finds all [data-model-output][data-trade-key] elements and
   * renders cached results into them.
   */
  function hydrateContainer(containerEl){
    if(!containerEl) return;
    var parser = window.BenTradeModelAnalysis;
    if(!parser) return;

    var slots = containerEl.querySelectorAll('[data-model-output][data-trade-key]');
    slots.forEach(function(slot){
      var tk = slot.getAttribute('data-trade-key');
      if(!tk) return;
      var entry = _store[tk];
      if(!entry || entry.status === 'idle') return;

      if(entry.status === 'running'){
        slot.style.display = 'block';
        slot.innerHTML = parser.render(parser.parse({ status: 'running' }));
        return;
      }

      if(entry.status === 'error'){
        slot.style.display = 'block';
        slot.innerHTML = parser.render(parser.parse({ status: 'error', summary: entry.error }));
        return;
      }

      if(entry.status === 'success' && entry.result){
        slot.style.display = 'block';
        slot.innerHTML = parser.render(entry.result);
        return;
      }
    });

    /* Also update button states */
    var btns = containerEl.querySelectorAll('button[data-action="model-analysis"][data-trade-key]');
    btns.forEach(function(btn){
      var tk = btn.getAttribute('data-trade-key');
      if(!tk) return;
      var entry = _store[tk];
      if(entry && entry.status === 'running'){
        btn.disabled = true;
        btn.innerHTML = '<span class="home-scan-spinner" aria-hidden="true" style="margin-right:4px;"></span>Running\u2026';
      } else {
        btn.disabled = false;
        var finishedAt = entry && entry.finishedAt ? entry.finishedAt : null;
        if(finishedAt){
          var d = new Date(finishedAt);
          var ts = d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
          btn.innerHTML = '\u21BB Re-run Analysis <span style="font-size:9px;color:var(--muted);margin-left:4px;">' + ts + '</span>';
        } else {
          btn.textContent = 'Run Model Analysis';
        }
      }
    });
  }

  return {
    get: get,
    getAll: getAll,
    setRunning: setRunning,
    setSuccess: setSuccess,
    setError: setError,
    clear: clear,
    clearAll: clearAll,
    subscribe: subscribe,
    hydrateContainer: hydrateContainer,
  };
})();
