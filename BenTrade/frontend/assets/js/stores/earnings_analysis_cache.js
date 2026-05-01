/**
 * Earnings Analysis cache store.
 *
 * Mirrors window.BenTradeDashboardCache pattern but scoped to EVA
 * payloads so we can have per-key TTLs (5 min for upcoming events list,
 * 5 min for ticker latest-features) without colliding with the main
 * dashboard cache.
 */
window.BenTradeEarningsAnalysisCache = (function () {
  'use strict';

  var DEFAULT_TTL_MS = 5 * 60 * 1000;
  var _store = {};      // key -> { data, expiresAt, lastUpdated }
  var _inFlight = {};

  function _now() { return Date.now(); }

  function _isEmpty(data) {
    if (data == null) return true;
    if (Array.isArray(data)) return data.length === 0;
    if (typeof data === 'object') {
      // Treat container objects with empty array payloads (events/upcoming/items)
      // as empty so we don't sit on a useless cached result.
      var arrKey = ['events', 'upcoming', 'items', 'results', 'data']
        .find(function (k) { return Array.isArray(data[k]); });
      if (arrKey && data[arrKey].length === 0) return true;
      return Object.keys(data).length === 0;
    }
    return false;
  }

  function get(key) {
    var entry = _store[key];
    if (!entry) return null;
    // Empty payloads are not useful — treat as a miss.
    if (_isEmpty(entry.data)) return null;
    if (entry.expiresAt && entry.expiresAt < _now()) {
      // Stale but keep the data so callers can render-then-refresh
      entry.stale = true;
    }
    return entry;
  }

  function set(key, data, ttlMs) {
    var ttl = (typeof ttlMs === 'number' && ttlMs > 0) ? ttlMs : DEFAULT_TTL_MS;
    _store[key] = {
      data: data,
      lastUpdated: new Date().toISOString(),
      expiresAt: _now() + ttl,
      stale: false,
    };
    return _store[key];
  }

  function clear(key) {
    if (key) {
      delete _store[key];
      delete _inFlight[key];
    } else {
      _store = {};
      _inFlight = {};
    }
  }

  /**
   * Fetch with stale-while-revalidate semantics.
   * @param {string} key
   * @param {Function} fetchFn  () => Promise<data>
   * @param {Object} opts       { ttlMs, force, onCached, onSuccess, onError }
   */
  function fetchWithCache(key, fetchFn, opts) {
    opts = opts || {};
    var cached = get(key);
    var fresh = cached && !cached.stale;

    if (cached && typeof opts.onCached === 'function') {
      try { opts.onCached(cached.data, !!cached.stale); } catch (_) {}
    }

    if (fresh && !opts.force) {
      if (typeof opts.onSuccess === 'function') {
        try { opts.onSuccess(cached.data, true); } catch (_) {}
      }
      return Promise.resolve(cached.data);
    }

    if (_inFlight[key] && !opts.force) {
      return _inFlight[key];
    }

    var p = fetchFn()
      .then(function (data) {
        set(key, data, opts.ttlMs);
        delete _inFlight[key];
        if (typeof opts.onSuccess === 'function') {
          try { opts.onSuccess(data, false); } catch (_) {}
        }
        return data;
      })
      .catch(function (err) {
        delete _inFlight[key];
        if (typeof opts.onError === 'function') {
          try { opts.onError(err); } catch (_) {}
        }
        throw err;
      });

    _inFlight[key] = p;
    return p;
  }

  return { get: get, set: set, clear: clear, clearAll: function () { clear(); }, fetchWithCache: fetchWithCache };
})();
