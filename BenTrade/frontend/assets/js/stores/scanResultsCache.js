/**
 * Scan Results Cache — shared sessionStorage-backed store for scanner payloads.
 *
 * Both the Stock Scanner page and the Home dashboard read/write through this
 * store so that results survive SPA navigation AND tab-scoped browser sessions.
 *
 * Exposed as  window.BenTradeScanResultsCache
 *
 * Usage:
 *   BenTradeScanResultsCache.save('stockScanner', payload, { filterLevel: 'balanced' });
 *   const cached = BenTradeScanResultsCache.load('stockScanner');  // null if stale/missing
 *   BenTradeScanResultsCache.clear('stockScanner');
 */
window.BenTradeScanResultsCache = (function(){
  'use strict';

  /** Maximum age (ms) before cached results are considered stale. 6 hours. */
  const DEFAULT_TTL_MS = 6 * 60 * 60 * 1000;

  /**
   * Build a stable sessionStorage key for a given scanner id.
   * @param {string} scannerId
   * @returns {string}
   */
  function storageKey(scannerId){
    return 'bentrade.scanResults.' + String(scannerId || 'unknown') + '.v1';
  }

  /**
   * Save scan results to sessionStorage.
   *
   * @param {string} scannerId    — e.g. 'stockScanner'
   * @param {object} payload      — full API response / normalized payload
   * @param {object} [config]     — scanner config snapshot (e.g. { filterLevel, symbols })
   */
  function save(scannerId, payload, config){
    if(!payload || typeof payload !== 'object') return;
    const entry = {
      payload: payload,
      timestamp: new Date().toISOString(),
      config: (config && typeof config === 'object') ? config : {},
    };
    try{
      sessionStorage.setItem(storageKey(scannerId), JSON.stringify(entry));
    }catch(err){
      console.warn('ScanResultsCache: failed to persist', scannerId, err);
    }
  }

  /**
   * Load cached scan results from sessionStorage.
   * Returns null if missing or stale (> ttlMs).
   *
   * @param {string} scannerId
   * @param {number} [ttlMs]  — override TTL (default: 6 h)
   * @returns {{ payload: object, timestamp: string, config: object } | null}
   */
  function load(scannerId, ttlMs){
    const ttl = (typeof ttlMs === 'number' && ttlMs > 0) ? ttlMs : DEFAULT_TTL_MS;
    try{
      const raw = sessionStorage.getItem(storageKey(scannerId));
      if(!raw) return null;
      const entry = JSON.parse(raw);
      if(!entry || typeof entry !== 'object' || !entry.timestamp) return null;
      const ageMs = Date.now() - new Date(entry.timestamp).getTime();
      if(ageMs > ttl){
        // Stale — clean up
        sessionStorage.removeItem(storageKey(scannerId));
        return null;
      }
      return entry;
    }catch(err){
      console.warn('ScanResultsCache: failed to load', scannerId, err);
      return null;
    }
  }

  /**
   * Clear cached results for a scanner.
   * @param {string} scannerId
   */
  function clear(scannerId){
    try{
      sessionStorage.removeItem(storageKey(scannerId));
    }catch(_err){
      // ignore
    }
  }

  /**
   * Get the ISO timestamp of the last cached run, or null.
   * @param {string} scannerId
   * @returns {string|null}
   */
  function getTimestamp(scannerId){
    const entry = load(scannerId);
    return entry ? entry.timestamp : null;
  }

  /**
   * Human-readable "Last run" string (e.g. "Today 2:34 PM" or "N/A").
   * @param {string} scannerId
   * @returns {string}
   */
  function formatTimestamp(scannerId){
    const ts = getTimestamp(scannerId);
    if(!ts) return 'N/A';
    try{
      const d = new Date(ts);
      if(Number.isNaN(d.getTime())) return 'N/A';
      const now = new Date();
      const isToday = d.toDateString() === now.toDateString();
      const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      return isToday ? ('Today ' + timeStr) : d.toLocaleDateString() + ' ' + timeStr;
    }catch(_err){
      return 'N/A';
    }
  }

  return {
    save: save,
    load: load,
    clear: clear,
    getTimestamp: getTimestamp,
    formatTimestamp: formatTimestamp,
    DEFAULT_TTL_MS: DEFAULT_TTL_MS,
    /** Exposed for testing. */
    _storageKey: storageKey,
  };
})();
