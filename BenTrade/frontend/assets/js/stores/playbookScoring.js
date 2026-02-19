/**
 * Playbook-Weighted Scoring for Opportunity Engine
 *
 * Applies regime-playbook penalties to raw scanner scores so the OE
 * prefers trades that match the current market regime playbook.
 *
 * This module DOES NOT mutate the original trade score.  It produces an
 * `adjustedScore` used only for OE selection ordering.
 *
 * Penalty rules (applied as percent of base score):
 *   - Avoid       → -40%  (base * 0.60)   — strongest penalty, overrides others
 *   - Not Primary → -15%  (base * 0.85)
 *   - Not Secondary (but not primary either) → -10%  (base * 0.90)
 *   - Primary match → no penalty (1.00)
 *   - Secondary match → no penalty (1.00)
 *
 * Tie-breaking (within 0.1 of adjusted score):
 *   1) Primary strategies first
 *   2) Secondary strategies second
 *   3) Higher liquidity score
 *   4) Higher return-on-risk
 *
 * Exposed as  window.BenTradePlaybookScoring
 */
window.BenTradePlaybookScoring = (function(){
  'use strict';

  /* ── Penalty multipliers ── */
  const PENALTY_AVOID           = 0.60;   // -40%
  const PENALTY_NOT_PRIMARY     = 0.85;   // -15%
  const PENALTY_NOT_SECONDARY   = 0.90;   // -10%
  const PENALTY_NONE            = 1.00;

  /* ── Tie threshold ── */
  const TIE_EPSILON = 0.1;

  /* ── Lane priority (lower = better) ── */
  const LANE_PRIMARY   = 0;
  const LANE_SECONDARY = 1;
  const LANE_NEUTRAL   = 2;
  const LANE_AVOID     = 3;

  /* ──────────────────── Strategy-ID normalization ──────────────────── */

  /**
   * Canonicalize a strategy string for comparison.
   * Lowercases + strips whitespace/underscores/hyphens to a minimal token.
   */
  function _canon(s){
    return String(s || '').toLowerCase().replace(/[\s_-]+/g, '');
  }

  /**
   * Map of canonical scanner strategy → set of canonical playbook aliases.
   * This bridges the gap between scanner-produced IDs (e.g. "credit_spread",
   * "put_credit_spread") and playbook IDs (e.g. "put_credit_spread",
   * "credit_spreads_wider").
   */
  const ALIAS_MAP = {
    /* Scanner ID → playbook IDs it should match against */
    putcreditspread:   ['putcreditspread', 'creditspread', 'creditspreads', 'creditspreadwiderdistance', 'creditspreadswider', 'shortputspreadsnearspot'],
    callcreditspread:  ['callcreditspread', 'creditspread', 'creditspreads', 'creditspreadwiderdistance', 'creditspreadswider'],
    creditspread:      ['creditspread', 'creditspreads', 'putcreditspread', 'callcreditspread', 'creditspreadwiderdistance', 'creditspreadswider'],
    ironcondor:        ['ironcondor', 'ironcondorwide', 'ironcondortight'],
    debitspreads:      ['debitspreads', 'calldebit', 'putdebit', 'debitspread', 'aggressivedirectionaldebitspreads'],
    butterflies:       ['butterflies', 'butterfly', 'debitbutterfly'],
    income:            ['income', 'coveredcall', 'cashsecuredputfarotm', 'cspfarotm'],
    calendars:         ['calendars', 'calendar'],
    calendar:          ['calendar', 'calendars'],
    stockbuy:          ['stockbuy'],
  };

  /**
   * Check whether a trade strategy matches any strategy in a playbook lane.
   * Tries exact match first, then alias expansion.
   *
   * @param {string} tradeStrategy — the scanner/trade strategy ID
   * @param {Set<string>} laneSet  — set of canonical playbook strategy IDs
   * @returns {boolean}
   */
  function _strategyMatchesLane(tradeStrategy, laneSet){
    const canon = _canon(tradeStrategy);
    if(laneSet.has(canon)) return true;

    /* Try aliases: if the trade's canonical ID has known aliases,
       check if any of those appear in the lane set. */
    const aliases = ALIAS_MAP[canon];
    if(aliases){
      for(let i = 0; i < aliases.length; i++){
        if(laneSet.has(aliases[i])) return true;
      }
    }

    /* Reverse check: if any alias group in the map contains our canon
       AND the lane set contains keys from that same group.  This handles
       the case where the playbook uses an alias we didn't list above. */
    for(const key in ALIAS_MAP){
      const group = ALIAS_MAP[key];
      if(group.indexOf(canon) !== -1){
        for(let i = 0; i < group.length; i++){
          if(laneSet.has(group[i])) return true;
        }
      }
    }

    /* Substring fallback: if canon includes or is included by any lane entry */
    laneSet.forEach(function(entry){
      /* already returned above if exact / alias matched */
    });

    return false;
  }

  /* ──────────────────── Playbook normalization ──────────────────── */

  /**
   * Extract canonical sets { primary, secondary, avoid } from either:
   *   a) Regime's `suggested_playbook` → { primary: [string], avoid: [string] }
   *   b) Enriched playbook → { primary: [{strategy}], secondary: [{strategy}], avoid: [{strategy}] }
   *
   * The enriched playbook is preferred when both are available because it
   * includes the "secondary" lane which the regime playbook omits.
   *
   * @param {object|null} enrichedPlaybook — from /api/playbook → data.playbook.playbook
   * @param {object|null} regimePayload    — from /api/regime → data.regime
   * @returns {{ primary: Set<string>, secondary: Set<string>, avoid: Set<string> }}
   */
  function normalizePlaybook(enrichedPlaybook, regimePayload){
    var primary = new Set();
    var secondary = new Set();
    var avoid = new Set();

    /* Prefer enriched playbook (has all 3 lanes) */
    var ep = (enrichedPlaybook && typeof enrichedPlaybook === 'object') ? enrichedPlaybook : null;
    /* ep can be { playbook: { primary: [...], secondary: [...], avoid: [...] } }
       or directly { primary: [...], secondary: [...], avoid: [...] } */
    var pb = null;
    if(ep){
      pb = (ep.playbook && typeof ep.playbook === 'object') ? ep.playbook : ep;
    }

    if(pb){
      _addToSet(primary, pb.primary);
      _addToSet(secondary, pb.secondary);
      _addToSet(avoid, pb.avoid);
    }

    /* If enriched wasn't available / empty, fall back to regime payload */
    if(primary.size === 0 && avoid.size === 0){
      var regime = (regimePayload && typeof regimePayload === 'object') ? regimePayload : {};
      var sp = (regime.suggested_playbook && typeof regime.suggested_playbook === 'object')
        ? regime.suggested_playbook : {};
      _addToSet(primary, sp.primary);
      _addToSet(avoid, sp.avoid);
      /* Regime playbook has no secondary — leave set empty */
    }

    return { primary: primary, secondary: secondary, avoid: avoid };
  }

  function _addToSet(set, items){
    if(!Array.isArray(items)) return;
    for(var i = 0; i < items.length; i++){
      var item = items[i];
      var strategy = (typeof item === 'string') ? item : (item && item.strategy ? String(item.strategy) : null);
      if(strategy){
        set.add(_canon(strategy));
      }
    }
  }

  /* ──────────────────── Core scoring ──────────────────── */

  /**
   * Compute the playbook-adjusted score for a single trade.
   *
   * @param {object} trade    — normalized opportunity (must have .score and .strategy)
   * @param {object} playbook — result of normalizePlaybook()
   * @returns {{ baseScore: number, adjustedScore: number, multiplier: number, lane: string, reasons: string[] }}
   */
  function computeAdjustedScore(trade, playbook){
    var baseScore = Number(trade?.score) || 0;
    var strategy = String(trade?.strategy || '');
    var reasons = [];

    var pb = playbook || { primary: new Set(), secondary: new Set(), avoid: new Set() };

    /* Determine lane membership */
    var inPrimary   = _strategyMatchesLane(strategy, pb.primary);
    var inSecondary = _strategyMatchesLane(strategy, pb.secondary);
    var inAvoid     = _strategyMatchesLane(strategy, pb.avoid);

    var multiplier = PENALTY_NONE;
    var lane = 'neutral';

    /* Avoid overrides everything — strongest penalty */
    if(inAvoid){
      multiplier = PENALTY_AVOID;
      lane = 'avoid';
      reasons.push('Avoid strategy: -40%');
    } else if(inPrimary){
      multiplier = PENALTY_NONE;
      lane = 'primary';
      reasons.push('Primary strategy');
    } else if(inSecondary){
      multiplier = PENALTY_NONE;
      lane = 'secondary';
      reasons.push('Secondary strategy');
    } else {
      /* Not in any known lane — apply missing-primary penalty */
      if(pb.primary.size > 0 || pb.secondary.size > 0){
        multiplier = PENALTY_NOT_PRIMARY;
        lane = 'neutral';
        reasons.push('Not in playbook: -15%');
      }
      /* If no playbook data at all, no penalty */
    }

    var adjustedScore = Math.max(0, Math.min(100, baseScore * multiplier));

    return {
      baseScore:     baseScore,
      adjustedScore: Math.round(adjustedScore * 10) / 10,  // 1 decimal
      multiplier:    multiplier,
      lane:          lane,
      reasons:       reasons,
    };
  }

  /* ──────────────────── Sorting ──────────────────── */

  /**
   * Lane priority for tie-breaking (lower = preferred).
   */
  function _lanePriority(lane){
    switch(lane){
      case 'primary':   return LANE_PRIMARY;
      case 'secondary': return LANE_SECONDARY;
      case 'avoid':     return LANE_AVOID;
      default:          return LANE_NEUTRAL;
    }
  }

  /**
   * Sort opportunities by adjusted score descending, with tie-breaking.
   *
   * This function does NOT mutate the input array.  Returns a new sorted
   * array of objects enriched with `_pb` (playbook scoring metadata).
   *
   * @param {object[]} opportunities  — normalized OE opportunities
   * @param {object}   playbook       — result of normalizePlaybook()
   * @returns {object[]}
   */
  function sortByPlaybook(opportunities, playbook){
    if(!Array.isArray(opportunities) || !opportunities.length) return [];

    var toNumber = (window.BenTradeUtils && window.BenTradeUtils.format)
      ? window.BenTradeUtils.format.toNumber
      : function(v){ var n = Number(v); return Number.isFinite(n) ? n : null; };

    /* Compute adjusted score for every opportunity */
    var annotated = opportunities.map(function(opp){
      var scoring = computeAdjustedScore(opp, playbook);
      /* Non-destructive: copy the opportunity and attach _pb metadata */
      var result = Object.assign({}, opp);
      result._pb = scoring;
      return result;
    });

    /* Sort */
    annotated.sort(function(a, b){
      var adjA = a._pb.adjustedScore;
      var adjB = b._pb.adjustedScore;

      /* Primary sort: adjusted score descending */
      var diff = adjB - adjA;
      if(Math.abs(diff) > TIE_EPSILON) return diff > 0 ? 1 : -1;

      /* ── Tie-breaking (within 0.1 of each other) ── */

      /* 1. Lane priority (primary > secondary > neutral > avoid) */
      var laneDiff = _lanePriority(a._pb.lane) - _lanePriority(b._pb.lane);
      if(laneDiff !== 0) return laneDiff;

      /* 2. Base score (higher base wins if same lane + ~same adjusted) */
      var baseDiff = (b._pb.baseScore || 0) - (a._pb.baseScore || 0);
      if(Math.abs(baseDiff) > 0.01) return baseDiff > 0 ? 1 : -1;

      /* 3. Liquidity score (higher = better) */
      var liqA = (toNumber(a.key_metrics?.liquidity) || 0);
      var liqB = (toNumber(b.key_metrics?.liquidity) || 0);
      if(liqB !== liqA) return liqB - liqA;

      /* 4. Return-on-risk (higher = better) */
      var rorA = (toNumber(a.ror) || 0);
      var rorB = (toNumber(b.ror) || 0);
      return rorB - rorA;
    });

    return annotated;
  }

  /* ──────────────────── Convenience ──────────────────── */

  /**
   * One-line reason string suitable for a tooltip / subtitle.
   * e.g. "Adjusted: 78.0% → 66.3% (Avoid strategy: -40%)"
   *       "Primary strategy (no adjustment)"
   */
  function reasonSummary(pbMeta){
    if(!pbMeta) return '';
    if(pbMeta.multiplier === PENALTY_NONE){
      return pbMeta.lane === 'primary' ? 'Primary strategy'
           : pbMeta.lane === 'secondary' ? 'Secondary strategy'
           : '';
    }
    var pctChange = Math.round((1 - pbMeta.multiplier) * 100);
    return pbMeta.baseScore.toFixed(1) + '% \u2192 ' + pbMeta.adjustedScore.toFixed(1) + '% (' + (pbMeta.reasons[0] || ('-' + pctChange + '%')) + ')';
  }

  /* ──────────────────── Public API ──────────────────── */

  return {
    /* Pure functions — safe to unit-test */
    normalizePlaybook:      normalizePlaybook,
    computeAdjustedScore:   computeAdjustedScore,
    sortByPlaybook:         sortByPlaybook,
    reasonSummary:          reasonSummary,

    /* Constants (exposed for testing) */
    PENALTY_AVOID:          PENALTY_AVOID,
    PENALTY_NOT_PRIMARY:    PENALTY_NOT_PRIMARY,
    PENALTY_NOT_SECONDARY:  PENALTY_NOT_SECONDARY,
    PENALTY_NONE:           PENALTY_NONE,
    TIE_EPSILON:            TIE_EPSILON,
  };
})();
