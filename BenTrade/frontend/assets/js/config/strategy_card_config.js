/**
 * BenTrade — Per-strategy card configuration.
 *
 * Defines which metrics, header fields, detail rows, and formatting
 * rules each strategy type uses when rendering an OptionsTradeCard.
 *
 * Adding a new strategy = add a new entry here + its mapping aliases
 * in option_trade_card_model.js.  No new rendering branches needed.
 *
 * Depends on: nothing (pure data)
 *
 * Format types:
 *   'pct'     – decimal → percentage  (0.75 → "75.0%")
 *   'dollars' – unsigned dollar       (315  → "$315.00")
 *   'money'   – signed dollar         (-42  → "-$42.00")
 *   'num'     – plain number          (1.18 → "1.18")
 *   'score'   – score (1 dp)          (82.4 → "82.4")
 *   'int'     – integer               (8421 → "8421")
 */
window.BenTradeStrategyCardConfig = (function () {
  'use strict';

  /* ================================================================
   * Metric descriptor shape:
   *   {
   *     key:           canonical model key (used by the mapper & renderer)
   *     computedKey:   key to look up in computed / computed_metrics
   *     detailsKey:    key to look up in details (optional)
   *     rootFallbacks: array of legacy root-level keys  (optional)
   *     label:         human-readable label
   *     format:        one of the format types above
   *     toneOpts:      { threshold?, invert? }   (optional, for toneClass)
   *   }
   * ================================================================ */

  /* ── Shared metric descriptors (reused across strategies) ──────── */

  var SHARED = {
    pop:            { key: 'pop',            computedKey: 'pop',            rootFallbacks: ['p_win_used', 'pop_delta_approx', 'pop_approx', 'implied_prob_profit'],  label: 'Win Probability',   format: 'pct',     toneOpts: { threshold: 0.5 } },
    expected_value: { key: 'expected_value', computedKey: 'expected_value', rootFallbacks: ['ev_per_contract', 'ev', 'expected_value'],                             label: 'Expected Value',    format: 'money' },
    return_on_risk: { key: 'return_on_risk', computedKey: 'return_on_risk', rootFallbacks: ['ror'],                                                                 label: 'Return on Risk',    format: 'pct' },
    max_profit:     { key: 'max_profit',     computedKey: 'max_profit',     rootFallbacks: ['max_profit_per_contract'],                                             label: 'Max Profit',        format: 'dollars' },
    max_loss:       { key: 'max_loss',       computedKey: 'max_loss',       rootFallbacks: ['max_loss_per_contract'],                                               label: 'Max Loss',          format: 'dollars',  toneOpts: { invert: true } },
    kelly_fraction: { key: 'kelly_fraction', computedKey: 'kelly_fraction', rootFallbacks: [],                                                                      label: 'Kelly Fraction',    format: 'pct' },
    iv_rv_ratio:    { key: 'iv_rv_ratio',    computedKey: 'iv_rv_ratio',    detailsKey: 'iv_rv_ratio', rootFallbacks: [],                                           label: 'IV / RV Ratio',     format: 'num' },
    rank_score:     { key: 'rank_score',     computedKey: 'rank_score',     rootFallbacks: ['composite_score'],                                                     label: 'Rank Score',        format: 'score' },
    break_even:     { key: 'break_even',     computedKey: 'break_even',     detailsKey: 'break_even', rootFallbacks: ['break_even_low'],                            label: 'Break Even',        format: 'dollars' },
    expected_move:  { key: 'expected_move',  computedKey: 'expected_move',  detailsKey: 'expected_move', rootFallbacks: ['expected_move_near'],                      label: 'Expected Move',     format: 'dollars' },
    liquidity_score:{ key: 'liquidity_score',computedKey: null,             rootFallbacks: ['liquidity_score'],                                                     label: 'Liquidity',         format: 'score' },
    ev_to_risk:     { key: 'ev_to_risk',     computedKey: 'ev_to_risk',     rootFallbacks: ['ev_to_risk'],                                                          label: 'EV / Risk',         format: 'num' },
    open_interest:  { key: 'open_interest',  computedKey: 'open_interest',  rootFallbacks: [],                                                                      label: 'Open Interest',     format: 'int' },
    volume:         { key: 'volume',         computedKey: 'volume',         rootFallbacks: [],                                                                      label: 'Volume',            format: 'int' },
  };


  /* ── Strategy configs ─────────────────────────────────────────── */

  var CONFIGS = {

    /* ── Credit Spreads (put & call) ─────────────────────────────── */
    credit_spread: {
      strategyLabel: 'Credit Spread',
      headerFields: ['symbol', 'strategy_label', 'expiration', 'dte', 'short_strike', 'long_strike', 'width'],
      coreMetrics: [
        SHARED.pop,
        SHARED.expected_value,
        SHARED.return_on_risk,
        SHARED.max_profit,
        SHARED.max_loss,
        SHARED.kelly_fraction,
      ],
      detailFields: [
        SHARED.break_even,
        SHARED.iv_rv_ratio,
        SHARED.expected_move,
        SHARED.rank_score,
      ],
      requiredKeys: ['pop', 'expected_value', 'return_on_risk', 'max_profit', 'max_loss'],
    },

    put_credit_spread: { alias: 'credit_spread' },
    call_credit_spread: { alias: 'credit_spread' },

    /* ── Debit Spreads ───────────────────────────────────────────── */
    debit_spreads: {
      strategyLabel: 'Debit Spread',
      headerFields: ['symbol', 'strategy_label', 'expiration', 'dte', 'short_strike', 'long_strike', 'width'],
      coreMetrics: [
        SHARED.expected_value,
        SHARED.ev_to_risk,
        SHARED.return_on_risk,
        SHARED.max_profit,
        SHARED.max_loss,
        { key: 'conviction_score', computedKey: null, rootFallbacks: ['conviction_score'], label: 'Conviction', format: 'score' },
      ],
      detailFields: [
        SHARED.break_even,
        SHARED.iv_rv_ratio,
        SHARED.liquidity_score,
        SHARED.rank_score,
      ],
      requiredKeys: ['expected_value', 'return_on_risk', 'max_profit', 'max_loss'],
    },

    put_debit: { alias: 'debit_spreads' },
    call_debit: { alias: 'debit_spreads' },

    /* ── Iron Condor ─────────────────────────────────────────────── */
    iron_condor: {
      strategyLabel: 'Iron Condor',
      headerFields: ['symbol', 'strategy_label', 'expiration', 'dte', 'short_strike', 'long_strike'],
      coreMetrics: [
        SHARED.pop,
        SHARED.expected_value,
        SHARED.return_on_risk,
        SHARED.max_profit,
        SHARED.max_loss,
        { key: 'theta_capture',       computedKey: null, rootFallbacks: ['theta_capture'],       label: 'Theta Capture',     format: 'num' },
      ],
      detailFields: [
        { key: 'symmetry_score',      computedKey: null, rootFallbacks: ['symmetry_score'],      label: 'Symmetry',          format: 'score' },
        { key: 'expected_move_ratio', computedKey: null, rootFallbacks: ['expected_move_ratio'],  label: 'EM Ratio',          format: 'num' },
        { key: 'tail_risk_score',     computedKey: null, rootFallbacks: ['tail_risk_score'],     label: 'Tail Risk',         format: 'score', toneOpts: { invert: true } },
        SHARED.liquidity_score,
        SHARED.rank_score,
      ],
      requiredKeys: ['pop', 'expected_value', 'max_profit', 'max_loss', 'theta_capture'],
    },

    /* ── Butterflies ─────────────────────────────────────────────── */
    butterflies: {
      strategyLabel: 'Butterfly',
      headerFields: ['symbol', 'strategy_label', 'expiration', 'dte', 'short_strike', 'long_strike'],
      coreMetrics: [
        { key: 'peak_profit_at_center',      computedKey: null, rootFallbacks: ['peak_profit_at_center'],      label: 'Peak Profit',       format: 'dollars' },
        { key: 'probability_of_touch_center', computedKey: null, rootFallbacks: ['probability_of_touch_center'], label: 'Prob Touch Center',  format: 'pct' },
        { key: 'cost_efficiency',             computedKey: null, rootFallbacks: ['cost_efficiency'],             label: 'Cost Efficiency',   format: 'num' },
        SHARED.max_profit,
        SHARED.max_loss,
        SHARED.return_on_risk,
      ],
      detailFields: [
        { key: 'payoff_slope',       computedKey: null, rootFallbacks: ['payoff_slope'],       label: 'Payoff Slope',      format: 'num' },
        { key: 'gamma_peak_score',   computedKey: null, rootFallbacks: ['gamma_peak_score'],   label: 'Gamma Peak',        format: 'score' },
        SHARED.liquidity_score,
        SHARED.rank_score,
      ],
      requiredKeys: ['peak_profit_at_center', 'probability_of_touch_center', 'max_profit', 'max_loss'],
    },

    butterfly_debit: { alias: 'butterflies' },

    /* ── Calendar Spreads ────────────────────────────────────────── */
    calendars: {
      strategyLabel: 'Calendar Spread',
      headerFields: ['symbol', 'strategy_label', 'expiration', 'dte'],
      coreMetrics: [
        { key: 'iv_term_structure_score', computedKey: null, rootFallbacks: ['iv_term_structure_score'], label: 'IV Term Structure', format: 'score' },
        { key: 'vega_exposure',           computedKey: null, rootFallbacks: ['vega_exposure'],           label: 'Vega Exposure',     format: 'num' },
        { key: 'theta_structure',         computedKey: null, rootFallbacks: ['theta_structure'],         label: 'Theta Structure',   format: 'num' },
        SHARED.max_profit,
        SHARED.max_loss,
        SHARED.expected_value,
      ],
      detailFields: [
        { key: 'move_risk_score', computedKey: null, rootFallbacks: ['move_risk_score'], label: 'Move Risk', format: 'score', toneOpts: { invert: true } },
        SHARED.liquidity_score,
        SHARED.rank_score,
      ],
      requiredKeys: ['iv_term_structure_score', 'vega_exposure', 'theta_structure'],
    },

    calendar_spread:      { alias: 'calendars' },
    calendar_call_spread: { alias: 'calendars' },
    calendar_put_spread:  { alias: 'calendars' },

    /* ── Income (CSP / Covered Call) ─────────────────────────────── */
    income: {
      strategyLabel: 'Income Strategy',
      headerFields: ['symbol', 'strategy_label', 'expiration', 'dte', 'short_strike'],
      coreMetrics: [
        { key: 'annualized_yield_on_collateral', computedKey: null, rootFallbacks: ['annualized_yield_on_collateral'], label: 'Annualised Yield', format: 'pct' },
        { key: 'premium_per_day',                computedKey: null, rootFallbacks: ['premium_per_day'],                label: 'Premium / Day',    format: 'dollars' },
        { key: 'downside_buffer',                computedKey: null, rootFallbacks: ['downside_buffer'],                label: 'Downside Buffer',  format: 'pct' },
        SHARED.max_profit,
        SHARED.max_loss,
        SHARED.pop,
      ],
      detailFields: [
        { key: 'assignment_risk_score', computedKey: null, rootFallbacks: ['assignment_risk_score'], label: 'Assignment Risk', format: 'score', toneOpts: { invert: true } },
        SHARED.iv_rv_ratio,
        SHARED.liquidity_score,
        SHARED.rank_score,
      ],
      requiredKeys: ['annualized_yield_on_collateral', 'premium_per_day', 'downside_buffer', 'max_profit', 'max_loss'],
    },

    csp:          { alias: 'income' },
    covered_call: { alias: 'income' },
  };


  /* ── Fallback config for unknown strategies ────────────────────── */

  var FALLBACK = {
    strategyLabel: 'Trade',
    headerFields: ['symbol', 'strategy_label', 'expiration', 'dte', 'short_strike', 'long_strike'],
    coreMetrics: [
      SHARED.pop,
      SHARED.expected_value,
      SHARED.return_on_risk,
      SHARED.max_profit,
      SHARED.max_loss,
      SHARED.rank_score,
    ],
    detailFields: [
      SHARED.break_even,
      SHARED.iv_rv_ratio,
    ],
    requiredKeys: ['max_profit', 'max_loss'],
  };

  /* ── Public API ──────────────────────────────────────────────── */

  /**
   * Resolve the card config for a given strategy ID.
   * Follows alias chains (max 3 hops to prevent loops).
   *
   * @param {string} strategyId – e.g. 'credit_spread', 'put_debit', 'iron_condor'
   * @returns {{ strategyLabel: string, headerFields: string[], coreMetrics: object[], detailFields: object[], requiredKeys: string[] }}
   */
  function forStrategy(strategyId) {
    var id = String(strategyId || '').trim().toLowerCase();
    var cfg = CONFIGS[id];
    var hops = 0;
    while (cfg && cfg.alias && hops < 3) {
      cfg = CONFIGS[cfg.alias];
      hops++;
    }
    return cfg || FALLBACK;
  }

  return {
    CONFIGS:     CONFIGS,
    FALLBACK:    FALLBACK,
    SHARED:      SHARED,
    forStrategy: forStrategy,
  };
})();
