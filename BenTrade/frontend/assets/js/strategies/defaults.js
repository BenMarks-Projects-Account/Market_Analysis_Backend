window.BenTradeStrategyDefaults = (function(){
  const defaultsByStrategy = {
    credit_spread: {
      dte_min: 7,
      dte_max: 21,
      expected_move_multiple: 1.0,
      width_min: 1,
      width_max: 5,
      min_pop: 0.65,
      min_ev_to_risk: 0.02,
      max_bid_ask_spread_pct: 1.5,
      min_open_interest: 500,
      min_volume: 50,
    },
    debit_spreads: {
      dte_min: 14,
      dte_max: 45,
      width_min: 2,
      width_max: 10,
      max_debit_pct_width: 0.45,
      max_iv_rv_ratio_for_buying: 1.0,
      max_bid_ask_spread_pct: 1.5,
      min_open_interest: 500,
      min_volume: 50,
      direction: 'both',
    },
    iron_condor: {
      dte_min: 21,
      dte_max: 45,
      distance_mode: 'expected_move',
      distance_target: 1.1,
      min_sigma_distance: 1.1,
      wing_width_put: 5,
      wing_width_call: 5,
      wing_width_max: 10,
      min_ror: 0.12,
      symmetry_target: 0.70,
      min_open_interest: 500,
      min_volume: 50,
    },
    butterflies: {
      dte_min: 7,
      dte_max: 21,
      center_mode: 'spot',
      width_min: 2,
      width_max: 10,
      min_cost_efficiency: 2.0,
      min_open_interest: 500,
      min_volume: 50,
      butterfly_type: 'debit',
      option_side: 'call',
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
    },
    income: {
      dte_min: 14,
      dte_max: 45,
      delta_min: 0.20,
      delta_max: 0.30,
      min_annualized_yield: 0.10,
      min_buffer: '',
      min_open_interest: 500,
      min_volume: 50,
    },
  };

  const whyByStrategy = {
    credit_spread: [
      'Short DTE + 1.0 expected-move distance targets premium decay with balanced assignment risk.',
      'POP and EV/risk floors avoid low-edge setups.',
      'Liquidity gates reduce slippage and bad fills.',
    ],
    debit_spreads: [
      'Mid DTE and width controls keep directional bets defined and affordable.',
      'Max debit as % width helps preserve upside asymmetry.',
      'IV/RV preference favors buying premium when comparatively cheaper.',
    ],
    iron_condor: [
      '1.1x expected-move shorts provide a conservative neutral range.',
      'Balanced 5-10 point wings improve risk symmetry and management clarity.',
      'RoR and liquidity floors avoid low-quality premium-selling structures.',
    ],
    butterflies: [
      'Short DTE with spot-centered structures focuses on high-theta pin scenarios.',
      'Wing range keeps payoff shape interpretable and repeatable.',
      'Cost-efficiency floor favors favorable payoff asymmetry.',
    ],
    calendars: [
      'Near/far tenor pairing expresses term-structure views with controlled decay.',
      'ATM strike is educationally clean for isolating vol-time effects.',
      'Liquidity filters improve execution consistency across both expiries.',
    ],
    income: [
      '14-45 DTE and 0.20-0.30 delta target conservative income strikes.',
      '10% annualized yield floor avoids very low-carry positions.',
      'Min buffer left blank uses backend auto-buffer (expected-move/delta based).',
    ],
  };

  function normalizeStrategyId(strategyId){
    const key = String(strategyId || '').trim().toLowerCase();
    if(key === 'strategy-credit-put' || key === 'strategy-credit-call') return 'credit_spread';
    return key;
  }

  function getStrategyDefaults(strategyId){
    const key = normalizeStrategyId(strategyId);
    const obj = defaultsByStrategy[key] || {};
    return { ...obj };
  }

  function getStrategyWhy(strategyId){
    const key = normalizeStrategyId(strategyId);
    const reasons = whyByStrategy[key] || [];
    return [...reasons];
  }

  return {
    getStrategyDefaults,
    getStrategyWhy,
  };
})();
