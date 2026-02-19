/**
 * Global Scanner Filter Profiles
 *
 * Canonical per-strategy numeric configs for four strictness levels:
 *   strict → conservative → balanced (default) → wide
 *
 * Every key matches the exact parameter name used by the corresponding
 * backend strategy plugin and frontend request payload.  Units follow
 * the Filter Parameter Inventory (docs/scanners/filter-parameter-inventory.md):
 *   - Decimals (0–1) for pop, ev_to_risk, delta, distance, debit_pct, etc.
 *   - Dollars ($) for widths, credits, debit caps.
 *   - "Percent points" for max_bid_ask_spread_pct (1.5 = 1.5%).
 *   - Counts for open interest / volume.
 *   - Days for DTE fields.
 *
 * Exposed as  window.BenTradeScannerProfiles
 *
 * Usage:
 *   const cfg = BenTradeScannerProfiles.getProfile('credit_spread', 'balanced');
 *   // → { dte_min: 7, dte_max: 45, min_pop: 0.60, ... }
 *
 * Integration:
 *   The scanner orchestrator merges getProfile(strategyId, level) into
 *   each scanner's request payload before calling the backend.  Because
 *   strategy_service.py uses setdefault(), explicit payload keys always win.
 */
window.BenTradeScannerProfiles = (function () {
  'use strict';

  /* ── Level metadata ────────────────────────────────────────── */

  const LEVELS = ['strict', 'conservative', 'balanced', 'wide'];
  const DEFAULT_LEVEL = 'balanced';

  const LEVEL_LABELS = {
    strict:       'Strict',
    conservative: 'Conservative',
    balanced:     'Balanced',
    wide:         'Wide',
  };

  const LEVEL_DESCRIPTIONS = {
    strict:       'Tightest filters — highest quality, fewest results',
    conservative: 'High-quality filters with moderate candidate flow',
    balanced:     'Default  — reasonable volume, still conservative for early use',
    wide:         'Most permissive — maximum candidate discovery',
  };

  /* ── Per-strategy profiles ─────────────────────────────────── */

  const profiles = {

    /* ─── Credit Spread ─────────────────────────────────────── */
    credit_spread: {
      strict: {
        dte_min: 14,
        dte_max: 30,
        expected_move_multiple: 1.2,
        width_min: 3,
        width_max: 5,
        distance_min: 0.03,
        distance_max: 0.08,
        min_pop: 0.70,
        min_ev_to_risk: 0.03,
        max_bid_ask_spread_pct: 1.0,
        min_open_interest: 1000,
        min_volume: 100,
      },
      conservative: {
        dte_min: 14,
        dte_max: 30,
        expected_move_multiple: 1.0,
        width_min: 3,
        width_max: 5,
        distance_min: 0.03,
        distance_max: 0.08,
        min_pop: 0.65,
        min_ev_to_risk: 0.02,
        max_bid_ask_spread_pct: 1.5,
        min_open_interest: 500,
        min_volume: 50,
      },
      balanced: {
        dte_min: 7,
        dte_max: 45,
        expected_move_multiple: 1.0,
        width_min: 1,
        width_max: 5,
        distance_min: 0.01,
        distance_max: 0.12,
        min_pop: 0.60,
        min_ev_to_risk: 0.02,
        max_bid_ask_spread_pct: 1.5,
        min_open_interest: 300,
        min_volume: 20,
      },
      wide: {
        dte_min: 3,
        dte_max: 60,
        expected_move_multiple: 0.8,
        width_min: 1,
        width_max: 10,
        distance_min: 0.01,
        distance_max: 0.15,
        min_pop: 0.50,
        min_ev_to_risk: 0.01,
        max_bid_ask_spread_pct: 2.5,
        min_open_interest: 100,
        min_volume: 10,
      },
    },

    /* ─── Debit Spreads ─────────────────────────────────────── */
    debit_spreads: {
      strict: {
        dte_min: 21,
        dte_max: 45,
        width_min: 2,
        width_max: 5,
        direction: 'both',
        max_debit_pct_width: 0.35,
        max_iv_rv_ratio_for_buying: 0.8,
        max_bid_ask_spread_pct: 1.0,
        min_open_interest: 1000,
        min_volume: 100,
      },
      conservative: {
        dte_min: 14,
        dte_max: 45,
        width_min: 2,
        width_max: 10,
        direction: 'both',
        max_debit_pct_width: 0.45,
        max_iv_rv_ratio_for_buying: 1.0,
        max_bid_ask_spread_pct: 1.5,
        min_open_interest: 500,
        min_volume: 50,
      },
      balanced: {
        dte_min: 14,
        dte_max: 45,
        width_min: 2,
        width_max: 10,
        direction: 'both',
        max_debit_pct_width: 0.55,
        max_iv_rv_ratio_for_buying: 1.3,
        max_bid_ask_spread_pct: 1.5,
        min_open_interest: 300,
        min_volume: 20,
      },
      wide: {
        dte_min: 7,
        dte_max: 60,
        width_min: 1,
        width_max: 15,
        direction: 'both',
        max_debit_pct_width: 0.70,
        max_iv_rv_ratio_for_buying: 1.8,
        max_bid_ask_spread_pct: 2.5,
        min_open_interest: 100,
        min_volume: 10,
      },
    },

    /* ─── Iron Condor ───────────────────────────────────────── */
    iron_condor: {
      strict: {
        dte_min: 21,
        dte_max: 45,
        distance_mode: 'expected_move',
        distance_target: 1.2,
        wing_width_put: 5,
        wing_width_call: 5,
        wing_width_max: 10,
        allow_skewed: false,
        min_sigma_distance: 1.2,
        symmetry_target: 0.80,
        min_ror: 0.15,
        min_credit: 0.15,
        min_open_interest: 1000,
        min_volume: 100,
      },
      conservative: {
        dte_min: 21,
        dte_max: 45,
        distance_mode: 'expected_move',
        distance_target: 1.1,
        wing_width_put: 5,
        wing_width_call: 5,
        wing_width_max: 10,
        allow_skewed: false,
        min_sigma_distance: 1.1,
        symmetry_target: 0.70,
        min_ror: 0.12,
        min_credit: 0.10,
        min_open_interest: 500,
        min_volume: 50,
      },
      balanced: {
        dte_min: 14,
        dte_max: 45,
        distance_mode: 'expected_move',
        distance_target: 1.0,
        wing_width_put: 5,
        wing_width_call: 5,
        wing_width_max: 10,
        allow_skewed: false,
        min_sigma_distance: 1.0,
        symmetry_target: 0.55,
        min_ror: 0.08,
        min_credit: 0.10,
        min_open_interest: 300,
        min_volume: 20,
      },
      wide: {
        dte_min: 7,
        dte_max: 60,
        distance_mode: 'expected_move',
        distance_target: 0.9,
        wing_width_put: 5,
        wing_width_call: 5,
        wing_width_max: 15,
        allow_skewed: true,
        min_sigma_distance: 0.8,
        symmetry_target: 0.40,
        min_ror: 0.05,
        min_credit: 0.05,
        min_open_interest: 100,
        min_volume: 10,
      },
    },

    /* ─── Butterflies ───────────────────────────────────────── */
    butterflies: {
      strict: {
        dte_min: 7,
        dte_max: 21,
        center_mode: 'spot',
        width_min: 2,
        width_max: 5,
        butterfly_type: 'debit',
        option_side: 'call',
        min_cost_efficiency: 3.0,
        min_open_interest: 1000,
        min_volume: 100,
      },
      conservative: {
        dte_min: 7,
        dte_max: 21,
        center_mode: 'spot',
        width_min: 2,
        width_max: 10,
        butterfly_type: 'debit',
        option_side: 'call',
        min_cost_efficiency: 2.0,
        min_open_interest: 500,
        min_volume: 50,
      },
      balanced: {
        dte_min: 7,
        dte_max: 30,
        center_mode: 'spot',
        width_min: 2,
        width_max: 10,
        butterfly_type: 'debit',
        option_side: 'both',
        min_cost_efficiency: 1.5,
        min_open_interest: 200,
        min_volume: 10,
      },
      wide: {
        dte_min: 3,
        dte_max: 45,
        center_mode: 'spot',
        width_min: 1,
        width_max: 15,
        butterfly_type: 'both',
        option_side: 'both',
        min_cost_efficiency: 1.0,
        min_open_interest: 50,
        min_volume: 5,
      },
    },

    /* ─── Calendars ─────────────────────────────────────────── */
    calendars: {
      strict: {
        near_dte_min: 7,
        near_dte_max: 14,
        far_dte_min: 30,
        far_dte_max: 45,
        dte_min: 7,
        dte_max: 45,
        moneyness: 'atm',
        prefer_term_structure: 1,
        allow_event_risk: false,
        max_bid_ask_spread_pct: 1.0,
        min_open_interest: 1000,
        min_volume: 100,
      },
      conservative: {
        near_dte_min: 7,
        near_dte_max: 14,
        far_dte_min: 30,
        far_dte_max: 60,
        dte_min: 7,
        dte_max: 60,
        moneyness: 'atm',
        prefer_term_structure: 1,
        allow_event_risk: false,
        max_bid_ask_spread_pct: 1.5,
        min_open_interest: 500,
        min_volume: 50,
      },
      balanced: {
        near_dte_min: 7,
        near_dte_max: 21,
        far_dte_min: 28,
        far_dte_max: 60,
        dte_min: 7,
        dte_max: 60,
        moneyness: 'atm',
        prefer_term_structure: 1,
        allow_event_risk: false,
        max_bid_ask_spread_pct: 1.5,
        min_open_interest: 300,
        min_volume: 20,
      },
      wide: {
        near_dte_min: 5,
        near_dte_max: 30,
        far_dte_min: 21,
        far_dte_max: 90,
        dte_min: 5,
        dte_max: 90,
        moneyness: 'atm',
        prefer_term_structure: 1,
        allow_event_risk: true,
        max_bid_ask_spread_pct: 2.5,
        min_open_interest: 100,
        min_volume: 5,
      },
    },

    /* ─── Income (CSP / Covered Call) ───────────────────────── */
    income: {
      strict: {
        dte_min: 14,
        dte_max: 30,
        delta_min: 0.20,
        delta_max: 0.28,
        min_annualized_yield: 0.12,
        min_buffer: '',
        min_open_interest: 1000,
        min_volume: 100,
      },
      conservative: {
        dte_min: 14,
        dte_max: 45,
        delta_min: 0.20,
        delta_max: 0.30,
        min_annualized_yield: 0.10,
        min_buffer: '',
        min_open_interest: 500,
        min_volume: 50,
      },
      balanced: {
        dte_min: 14,
        dte_max: 45,
        delta_min: 0.15,
        delta_max: 0.35,
        min_annualized_yield: 0.08,
        min_buffer: '',
        min_open_interest: 300,
        min_volume: 20,
      },
      wide: {
        dte_min: 7,
        dte_max: 60,
        delta_min: 0.10,
        delta_max: 0.40,
        min_annualized_yield: 0.04,
        min_buffer: '',
        min_open_interest: 100,
        min_volume: 5,
      },
    },
  };

  /* ── Public helpers ────────────────────────────────────────── */

  /**
   * Return a shallow-copied config object for a given strategy + level.
   *
   * @param {string} strategyId  — canonical strategy id (e.g. 'credit_spread')
   * @param {string} [level]     — 'strict' | 'conservative' | 'balanced' | 'wide'
   * @returns {object|null} config object with parameter keys, or null if unknown strategy
   */
  function getProfile(strategyId, level) {
    const sid = String(strategyId || '').trim().toLowerCase();
    const lvl = String(level || DEFAULT_LEVEL).trim().toLowerCase();
    const strategy = profiles[sid];
    if (!strategy) return null;
    const cfg = strategy[lvl] || strategy[DEFAULT_LEVEL];
    return cfg ? Object.assign({}, cfg) : null;
  }

  /**
   * Return profile configs for ALL strategies at a given level.
   * Useful for the orchestrator to merge into each scanner request.
   *
   * @param {string} [level] — 'strict' | 'conservative' | 'balanced' | 'wide'
   * @returns {object} { credit_spread: {...}, debit_spreads: {...}, ... }
   */
  function getAllProfiles(level) {
    const lvl = String(level || DEFAULT_LEVEL).trim().toLowerCase();
    const result = {};
    for (const sid of Object.keys(profiles)) {
      result[sid] = getProfile(sid, lvl);
    }
    return result;
  }

  /**
   * Return the list of strategy IDs that have profiles defined.
   * @returns {string[]}
   */
  function strategyIds() {
    return Object.keys(profiles);
  }

  return {
    LEVELS,
    DEFAULT_LEVEL,
    LEVEL_LABELS,
    LEVEL_DESCRIPTIONS,
    getProfile,
    getAllProfiles,
    strategyIds,
  };
})();
