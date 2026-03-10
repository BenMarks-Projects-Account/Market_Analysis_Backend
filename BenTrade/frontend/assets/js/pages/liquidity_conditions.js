window.BenTradePages = window.BenTradePages || {};

/**
 * Liquidity & Financial Conditions dashboard controller.
 *
 * UI-only shell — static mock data, no backend wiring yet.
 * Uses BenTradeDashboardCache for session persistence across route changes.
 */
window.BenTradePages.initLiquidityConditions = function initLiquidityConditions(rootEl) {
  var CACHE_KEY = 'liquidityConditions';
  var _cache = window.BenTradeDashboardCache;
  var _destroyed = false;

  if (_cache && _cache.hasCache(CACHE_KEY)) {
    console.log('[BenTrade][Liquidity] cache_rehydrate route_entry');
  } else {
    console.log('[BenTrade][Liquidity] no_cache — showing static shell');
  }

  return function cleanupLiquidityConditions() {
    _destroyed = true;
    console.log('[BenTrade][Liquidity] cleanup — DOM detached, cache preserved');
  };
};
