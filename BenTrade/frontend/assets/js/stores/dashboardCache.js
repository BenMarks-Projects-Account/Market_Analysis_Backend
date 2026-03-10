/**
 * BenTrade Dashboard Cache — centralized session-level cache for dashboard payloads.
 *
 * Provides stale-while-refresh semantics:
 *   - Render cached data immediately on route mount
 *   - Background refresh keeps old data visible
 *   - Cache overwritten only on success
 *   - Failed refresh preserves previous data
 *
 * Shape per dashboard key:
 *   { data, lastUpdated, isLoaded, lastError }
 *
 * Refreshing state is transient (in-memory only).
 *
 * Storage: sessionStorage (auto-clears on tab close, no boot-modal cleanup needed).
 */
window.BenTradeDashboardCache = (function(){
  var STORAGE_KEY = 'bentrade_dashboard_cache_v1';

  /* ── In-memory primary store ── */
  var _mem = {};

  /* ── Refreshing flags (transient, never persisted) ── */
  var _refreshing = {};

  /* ── In-flight promise tracking (prevents duplicate concurrent fetches) ── */
  var _inFlight = {};

  /* ── Persistence helpers ── */
  function _loadFromStorage(){
    try{
      var raw = sessionStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    }catch(_e){
      return {};
    }
  }

  function _persist(){
    try{
      var out = {};
      var keys = Object.keys(_mem);
      for(var i = 0; i < keys.length; i++){
        var k = keys[i];
        var entry = _mem[k];
        if(entry && entry.isLoaded){
          out[k] = {
            data: entry.data,
            lastUpdated: entry.lastUpdated,
            lastError: entry.lastError || null
          };
        }
      }
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(out));
    }catch(_e){}
  }

  /* ── Bootstrap from sessionStorage ── */
  var _stored = _loadFromStorage();
  var _storedKeys = Object.keys(_stored);
  for(var _si = 0; _si < _storedKeys.length; _si++){
    var _sk = _storedKeys[_si];
    var _entry = _stored[_sk];
    if(_entry && _entry.data != null){
      _mem[_sk] = {
        data: _entry.data,
        lastUpdated: _entry.lastUpdated || null,
        isLoaded: true,
        lastError: _entry.lastError || null
      };
    }
  }

  /**
   * Get cached entry for a dashboard.
   * @param {string} key — dashboard identifier (e.g. 'newsSentiment')
   * @returns {{ data: any, lastUpdated: string|null, isLoaded: boolean, lastError: string|null } | null}
   */
  function get(key){
    return _mem[key] || null;
  }

  /**
   * Store successful data for a dashboard (overwrites previous).
   * @param {string} key
   * @param {*} data — full API response payload
   */
  function set(key, data){
    _mem[key] = {
      data: data,
      lastUpdated: new Date().toISOString(),
      isLoaded: true,
      lastError: null
    };
    _persist();
  }

  /**
   * Record a fetch error without clearing existing data.
   * @param {string} key
   * @param {string} error
   */
  function setError(key, error){
    if(!_mem[key]){
      _mem[key] = { data: null, lastUpdated: null, isLoaded: false, lastError: null };
    }
    _mem[key].lastError = String(error || '');
  }

  /**
   * Check whether a background refresh is in progress for a dashboard.
   * @param {string} key
   * @returns {boolean}
   */
  function isRefreshing(key){
    return !!_refreshing[key];
  }

  /**
   * Mark a dashboard as refreshing or not.
   * @param {string} key
   * @param {boolean} value
   */
  function setRefreshing(key, value){
    _refreshing[key] = !!value;
  }

  /**
   * Stale-while-refresh fetch wrapper.
   *
   * If cached data exists, returned promise resolves with cached data immediately
   * via the `onCached` callback, then runs `fetchFn` in background.
   *
   * On fetch success → updates cache, calls `onSuccess(data)`.
   * On fetch error → preserves cache, calls `onError(err)`.
   *
   * Deduplicates concurrent calls for the same key.
   *
   * @param {string} key           — dashboard cache key
   * @param {Function} fetchFn     — async () => data
   * @param {Object} callbacks
   * @param {Function} [callbacks.onCached]  — called immediately with cached data if available
   * @param {Function} [callbacks.onSuccess] — called with fresh data on success
   * @param {Function} [callbacks.onError]   — called with error on failure
   * @param {boolean}  [force=false]         — force refresh even if already in-flight
   * @returns {Promise<*>}
   */
  function fetchWithCache(key, fetchFn, callbacks, force){
    var cb = callbacks || {};
    var cached = get(key);

    // Render cached data immediately if available
    if(cached && cached.isLoaded && cached.data != null && typeof cb.onCached === 'function'){
      cb.onCached(cached.data);
    }

    // Reuse in-flight promise if not forced
    if(_inFlight[key] && !force){
      return _inFlight[key];
    }

    setRefreshing(key, true);

    var p = fetchFn()
      .then(function(data){
        set(key, data);
        setRefreshing(key, false);
        delete _inFlight[key];
        if(typeof cb.onSuccess === 'function') cb.onSuccess(data);
        return data;
      })
      .catch(function(err){
        setError(key, err?.message || String(err));
        setRefreshing(key, false);
        delete _inFlight[key];
        if(typeof cb.onError === 'function') cb.onError(err);
        throw err;
      });

    _inFlight[key] = p;
    return p;
  }

  /**
   * Clear one dashboard entry.
   * @param {string} key
   */
  function clear(key){
    delete _mem[key];
    delete _refreshing[key];
    delete _inFlight[key];
    _persist();
  }

  /** Clear all dashboard cache entries. */
  function clearAll(){
    _mem = {};
    _refreshing = {};
    _inFlight = {};
    try{ sessionStorage.removeItem(STORAGE_KEY); }catch(_e){}
  }

  /* ── Convenience selectors / helpers ── */

  /**
   * Get the raw data payload for a dashboard (unwrapped from entry wrapper).
   * @param {string} key
   * @returns {*|null}
   */
  function getData(key){
    var entry = _mem[key];
    return (entry && entry.isLoaded && entry.data != null) ? entry.data : null;
  }

  /**
   * Get current dashboard status string.
   * @param {string} key
   * @returns {'idle'|'loading'|'ready'|'refreshing'|'error'}
   */
  function getStatus(key){
    var entry = _mem[key];
    if(_refreshing[key]) return 'refreshing';
    if(!entry) return 'idle';
    if(entry.isLoaded && entry.data != null) return entry.lastError ? 'error' : 'ready';
    if(entry.lastError) return 'error';
    return 'loading';
  }

  /**
   * Get the last updated ISO timestamp.
   * @param {string} key
   * @returns {string|null}
   */
  function getLastUpdated(key){
    var entry = _mem[key];
    return entry ? (entry.lastUpdated || null) : null;
  }

  /**
   * Check whether any cached data exists for a dashboard.
   * @param {string} key
   * @returns {boolean}
   */
  function hasCache(key){
    var entry = _mem[key];
    return !!(entry && entry.isLoaded && entry.data != null);
  }

  /**
   * Should a full-page loading skeleton be shown?
   * True only when no cached data exists and no prior error.
   * @param {string} key
   * @returns {boolean}
   */
  function shouldShowLoader(key){
    return !hasCache(key) && !_refreshing[key] && getStatus(key) !== 'error';
  }

  /**
   * Should a lightweight refresh overlay be shown over existing data?
   * @param {string} key
   * @returns {boolean}
   */
  function shouldShowRefreshOverlay(key){
    return !!_refreshing[key] && hasCache(key);
  }

  /**
   * Should cached data be rendered?
   * @param {string} key
   * @returns {boolean}
   */
  function shouldShowCachedData(key){
    return hasCache(key);
  }

  /**
   * Validate a payload before allowing cache write.
   * @param {*} data        — proposed payload
   * @param {Array} requiredFields — list of dot-separated field paths
   * @returns {boolean}
   */
  function validatePayload(data, requiredFields){
    if(data == null || typeof data !== 'object') return false;
    if(!requiredFields || requiredFields.length === 0) return true;
    for(var i = 0; i < requiredFields.length; i++){
      var parts = requiredFields[i].split('.');
      var cur = data;
      for(var j = 0; j < parts.length; j++){
        if(cur == null || typeof cur !== 'object') return false;
        cur = cur[parts[j]];
      }
      if(cur === undefined) return false;
    }
    return true;
  }

  /**
   * Safe cache write: validates payload before overwriting.
   * If validation fails, records an error but preserves prior cache.
   *
   * @param {string} key
   * @param {*} data
   * @param {Array} [requiredFields] — dot-path fields that must exist
   * @returns {boolean} true if write succeeded
   */
  function setSafe(key, data, requiredFields){
    if(!validatePayload(data, requiredFields)){
      console.warn('[DashboardCache] Payload validation failed for "' + key + '", preserving prior cache');
      setError(key, 'Invalid payload — prior data preserved');
      return false;
    }
    set(key, data);
    return true;
  }

  /**
   * Build a status snapshot for diagnostics / logging.
   * @param {string} key
   * @returns {Object}
   */
  function getStatusSnapshot(key){
    var entry = _mem[key];
    return {
      dashboard_key: key,
      status: getStatus(key),
      has_loaded_once: hasCache(key),
      last_loaded_at: entry ? entry.lastUpdated : null,
      last_error: entry ? entry.lastError : null,
      source: _refreshing[key] ? 'network' : (hasCache(key) ? 'cache' : null)
    };
  }

  return {
    get: get,
    set: set,
    setSafe: setSafe,
    setError: setError,
    isRefreshing: isRefreshing,
    setRefreshing: setRefreshing,
    fetchWithCache: fetchWithCache,
    clear: clear,
    clearAll: clearAll,
    /* Selectors / helpers */
    getData: getData,
    getStatus: getStatus,
    getLastUpdated: getLastUpdated,
    hasCache: hasCache,
    shouldShowLoader: shouldShowLoader,
    shouldShowRefreshOverlay: shouldShowRefreshOverlay,
    shouldShowCachedData: shouldShowCachedData,
    validatePayload: validatePayload,
    getStatusSnapshot: getStatusSnapshot
  };
})();
