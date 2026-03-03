/**
 * BenTrade — Stock Strategy Registry
 *
 * Centralized registry of all stock-based strategy dashboards.
 * Future scanners plug into this registry for routing, navigation,
 * and card configuration.
 *
 * Status lifecycle: scaffold → beta → active → deprecated
 */
window.BenTradeStockStrategies = (function () {

  const strategies = [
    {
      id:          'stock_pullback_swing',
      name:        'Pullback Swing',
      route:       'stocks/pullback-swing',
      endpoint:    '/api/stocks/pullback-swing',
      description: 'Short-term dip buys in trending stocks — buy pullbacks to key moving averages.',
      icon:        '◇',
      status:      'beta',
    },
    {
      id:          'stock_momentum_breakout',
      name:        'Momentum Breakout',
      route:       'stocks/momentum-breakout',
      endpoint:    '/api/stocks/momentum-breakout',
      description: 'Breakout entries on volume expansion through resistance levels.',
      icon:        '△',
      status:      'beta',
    },
    {
      id:          'stock_mean_reversion',
      name:        'Mean Reversion',
      route:       'stocks/mean-reversion',
      endpoint:    '/api/stocks/mean-reversion',
      description: 'Bounce plays on oversold names reverting to mean — RSI / Bollinger extremes.',
      icon:        '⟲',
      status:      'beta',
    },
    {
      id:          'stock_volatility_expansion',
      name:        'Volatility Expansion',
      route:       'stocks/volatility-expansion',
      endpoint:    '/api/stocks/volatility-expansion',
      description: 'Entries when implied volatility spikes signal directional opportunity.',
      icon:        '◈',
      status:      'beta',
    },
  ];

  /** Return a shallow copy of the full registry. */
  function getAll () {
    return strategies.map(function (s) { return Object.assign({}, s); });
  }

  /** Look up a single strategy by its canonical id. */
  function getById (id) {
    var match = strategies.find(function (s) { return s.id === id; });
    return match ? Object.assign({}, match) : null;
  }

  /** Return only strategies with the given status. */
  function getByStatus (status) {
    return strategies
      .filter(function (s) { return s.status === status; })
      .map(function (s) { return Object.assign({}, s); });
  }

  return {
    getAll:      getAll,
    getById:     getById,
    getByStatus: getByStatus,
    /** Raw list (read-only reference). */
    list:        strategies,
  };

})();
