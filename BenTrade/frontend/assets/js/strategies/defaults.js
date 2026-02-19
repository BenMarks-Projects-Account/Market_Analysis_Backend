window.BenTradeStrategyDefaults = (function(){
  // Canonical scanner symbol universe — must match backend DEFAULT_SCANNER_SYMBOLS
  const SCANNER_SYMBOLS = ['SPY', 'QQQ', 'IWM', 'DIA', 'XSP', 'RUT', 'NDX'];

  const presetsByStrategy = {
    credit_spread: {
      conservative: {
        dte_min: 14,
        dte_max: 30,
        expected_move_multiple: 1.0,
        width_min: 3,
        width_max: 5,
        distance_min: 0.03,
        distance_max: 0.08,
        symbols: SCANNER_SYMBOLS,
        min_pop: 0.65,
        min_ev_to_risk: 0.02,
        max_bid_ask_spread_pct: 1.5,
        min_open_interest: 500,
        min_volume: 50,
      },
      strict: {
        dte_min: 7,
        dte_max: 21,
        expected_move_multiple: 1.0,
        width_min: 1,
        width_max: 5,
        distance_min: 0.01,
        distance_max: 0.12,
        symbols: SCANNER_SYMBOLS,
        min_pop: 0.65,
        min_ev_to_risk: 0.02,
        max_bid_ask_spread_pct: 1.5,
        min_open_interest: 200,
        min_volume: 10,
      },
    },
  };

  // Flat defaults map (backward-compatible: returns the conservative preset for credit_spread)
  const defaultsByStrategy = {
    credit_spread: presetsByStrategy.credit_spread.conservative,
    debit_spreads: {
      dte_min: 14,
      dte_max: 45,
      width_min: 2,
      width_max: 10,
      max_debit_pct_width: 0.65,
      max_iv_rv_ratio_for_buying: 1.5,
      max_bid_ask_spread_pct: 1.5,
      min_open_interest: 200,
      min_volume: 10,
      direction: 'both',
      symbols: SCANNER_SYMBOLS,
    },
    iron_condor: {
      dte_min: 21,
      dte_max: 45,
      distance_mode: 'expected_move',
      distance_target: 1.0,
      min_sigma_distance: 1.0,
      wing_width_put: 5,
      wing_width_call: 5,
      wing_width_max: 10,
      min_ror: 0.08,
      symmetry_target: 0.5,
      min_open_interest: 200,
      min_volume: 10,
      symbols: SCANNER_SYMBOLS,
    },
    butterflies: {
      dte_min: 7,
      dte_max: 21,
      center_mode: 'spot',
      width_min: 2,
      width_max: 10,
      min_cost_efficiency: 1.2,
      min_open_interest: 150,
      min_volume: 10,
      butterfly_type: 'debit',
      option_side: 'call',
      symbols: SCANNER_SYMBOLS,
    },
    calendars: {
      near_dte_min: 7,
      near_dte_max: 14,
      far_dte_min: 30,
      far_dte_max: 60,
      dte_min: 7,
      dte_max: 60,
      moneyness: 'atm',
      prefer_term_structure: 1,
      max_bid_ask_spread_pct: 1.5,
      min_open_interest: 500,
      min_volume: 50,
      symbols: SCANNER_SYMBOLS,
    },
    income: {
      dte_min: 14,
      dte_max: 45,
      delta_min: 0.15,
      delta_max: 0.35,
      min_annualized_yield: 0.06,
      min_buffer: '',
      min_open_interest: 200,
      min_volume: 10,
      symbols: SCANNER_SYMBOLS,
    },
  };

  const whyByStrategy = {
    credit_spread: [
      '14-30 DTE and $3-$5 spreads gather enough premium for positive EV on index ETFs.',
      '3%-8% OTM distance avoids near-the-money gamma and illiquid far-OTM strikes.',
      'SPY/QQQ/IWM multi-symbol scan maximizes the candidate pool while keeping to liquid ETFs.',
    ],
    debit_spreads: [
      '14-45 DTE with 2-10 point widths keeps directional risk defined.',
      'Debit cap at 65% of width avoids overpaying for convexity.',
      'IV/RV ≤ 1.5 and practical liquidity floors improve consistency.',
    ],
    iron_condor: [
      'Expected-move distance target at 1.0 keeps neutral structures findable in normal markets.',
      '5/5 wings (max 10) and symmetry target 0.5 keep risk manageable.',
      'RoR floor 0.08 plus moderate liquidity constraints avoid empty scans.',
    ],
    butterflies: [
      '7-21 DTE and 2-10 wings target repeatable debit butterfly structures.',
      'Cost-efficiency floor at 1.2 is selective without starving candidates.',
      'Liquidity defaults (OI 150, vol 10) keep execution practical.',
    ],
    calendars: [
      'Near/far tenor pairing expresses term-structure views with controlled decay.',
      'ATM strike is educationally clean for isolating vol-time effects.',
      'Liquidity filters improve execution consistency across both expiries.',
    ],
    income: [
      '14-45 DTE and 0.15-0.35 delta supports both CSP and covered-call setups.',
      '6% annualized yield floor balances trade availability and carry quality.',
      'Buffer is auto-managed; missing explicit buffer should warn, not hard-fail.',
    ],
  };

  function normalizeStrategyId(strategyId){
    const key = String(strategyId || '').trim().toLowerCase();
    return key;
  }

  function getStrategyDefaults(strategyId, presetName){
    const key = normalizeStrategyId(strategyId);
    // If a preset is requested and we have presets for this strategy, use it
    const stratPresets = presetsByStrategy[key];
    let result;
    if(stratPresets && presetName){
      const preset = stratPresets[String(presetName).toLowerCase()];
      if(preset) result = { ...preset };
    }
    if(!result){
      const obj = defaultsByStrategy[key] || {};
      result = { ...obj };
    }
    // Override symbols with the global symbol universe store if available
    const storeSymbols = window.BenTradeSymbolUniverseStore?.getSymbols?.();
    if(Array.isArray(storeSymbols) && storeSymbols.length){
      result.symbols = storeSymbols;
    }
    return result;
  }

  function getStrategyWhy(strategyId){
    const key = normalizeStrategyId(strategyId);
    const reasons = whyByStrategy[key] || [];
    return [...reasons];
  }

  function getPresetNames(strategyId){
    const key = normalizeStrategyId(strategyId);
    const stratPresets = presetsByStrategy[key];
    return stratPresets ? Object.keys(stratPresets) : [];
  }

  return {
    getStrategyDefaults,
    getStrategyWhy,
    getPresetNames,
  };
})();
