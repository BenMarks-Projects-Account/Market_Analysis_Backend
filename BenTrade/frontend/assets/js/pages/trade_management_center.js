/**
 * BenTrade -- Trade Management Center (Prompt 10 consolidation)
 *
 * Depends on compact /api/tmc/workflows/... endpoints (Prompt 8/9).
 * Old trade-building pipeline payload assumptions are gone.
 * Active Trade section remains separate (uses /api/active-trade-pipeline).
 *
 * Section 1: Stock Opportunities  (TMC workflow endpoints)
 * Section 2: Options Opportunities (TMC workflow endpoints)
 * Section 3: Active Trade Candidates (active-trade-pipeline -- unchanged)
 */
(function () {
  'use strict';

  /* -- State --------------------------------------------------------- */
  var _pollTimer      = null;
  var _activeRunning  = false;
  /** Last loaded stock run_id from the /latest endpoint. */
  var _lastStockRunId = null;
  /** Last loaded options run_id from the /latest endpoint. */
  var _lastOptionsRunId = null;
  /** Completion-poll timer for stock workflow. */
  var _stockPollTimer  = null;
  /** Completion-poll timer for options workflow. */
  var _optionsPollTimer = null;
  /** Full refresh chain running flag. */
  var _fullRefreshRunning = false;
  /** Timestamp (ms) of last manual active-trade render from Full Refresh or Run Active.
   *  Used to prevent orchestrator poll from overwriting recent manual renders. */
  var _lastManualActiveRenderAt = 0;
  /** Flag-based guard: true while a manual refresh (Full Refresh or Run Active) is in progress.
   *  Prevents orchestrator poll from overwriting results before the refresh completes. */
  var _manualRefreshInProgress = false;
  /** Grace period (ms) after a manual render during which poll-based refreshes are suppressed.
   *  Backup guard in case flag is not cleared (belt and suspenders). */
  var _MANUAL_RENDER_GUARD_MS = 300000;

  /**
   * Stored event handler references — prevents listener stacking on re-render.
   * Each grid gets ONE delegated click handler; old handler removed before new one is added.
   */
  var _stockGridClickHandler   = null;
  var _optionsGridClickHandler = null;
  var _activeGridClickHandler  = null;

  /** Cached generated_at timestamps for periodic freshness refresh. */
  var _stockGeneratedAt  = null;
  var _optionsGeneratedAt = null;
  var _activeGeneratedAt  = null;
  /** Periodic freshness-label refresh timer. */
  var _freshnessTimer = null;

  /** Cached last-loaded response data for instant re-render on SPA navigation. */
  var _cachedStockResp   = null;
  var _cachedOptionsResp = null;
  var _cachedActiveData  = null;

  /* -- API ref -------------------------------------------------------- */
  var api = window.BenTradeApi;

  /* =================================================================
   *  SHARED HELPERS
   * ================================================================= */

  function esc(s) {
    if (s == null) return '';
    var d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function fmtPct(v) {
    if (v == null) return '--';
    return (v * 100).toFixed(1) + '%';
  }

  function fmtDollar(v) {
    if (v == null) return '--';
    return '$' + Number(v).toFixed(2);
  }

  function fmtDate(iso) {
    if (!iso) return '--';
    try { return new Date(iso).toLocaleString(); } catch (_) { return iso; }
  }

  /* -- Status vocabulary --------------------------------------------- */

  /**
   * TMC status vocabulary -- single source of truth for UI mapping.
   * Maps TMCStatus string to { css, label, isError, isEmpty }.
   */
  var TMC_STATUS_MAP = {
    completed:   { css: 'tmc-run-completed',  label: 'COMPLETED',   isError: false, isEmpty: false },
    degraded:    { css: 'tmc-run-degraded',   label: 'DEGRADED',    isError: false, isEmpty: false },
    failed:      { css: 'tmc-run-failed',      label: 'FAILED',      isError: true,  isEmpty: false },
    no_output:   { css: 'tmc-run-no-output',   label: 'NO OUTPUT',   isError: false, isEmpty: true  },
    unavailable: { css: 'tmc-run-unavailable', label: 'UNAVAILABLE', isError: true,  isEmpty: true  },
  };

  /**
   * Batch-level status vocabulary — used by the section-header badge
   * to distinguish complete vs partial pipeline runs.
   */
  var BATCH_STATUS_MAP = {
    completed: { css: 'tmc-batch-completed', label: '' },
    partial:   { css: 'tmc-batch-partial',   label: 'PARTIAL' },
  };

  function getStatusInfo(status) {
    return TMC_STATUS_MAP[status] || { css: 'tmc-run-unknown', label: (status || 'UNKNOWN').toUpperCase(), isError: false, isEmpty: true };
  }

  /** Update a status badge element with consistent styling. */
  function updateStatusBadge(el, status) {
    if (!el) return;
    var info = getStatusInfo(status);
    el.textContent = info.label;
    el.className = 'tmc-run-status ' + info.css;
  }

  /** Update a batch-status badge element. Shows nothing for "completed". */
  function updateBatchStatusBadge(el, batchStatus) {
    if (!el) return;
    var info = BATCH_STATUS_MAP[batchStatus] || { css: '', label: '' };
    el.textContent = info.label;
    el.className = 'tmc-batch-status ' + info.css;
  }

  /** Update the freshness timestamp element with "Last updated X ago". */
  function updateFreshness(el, generatedAt) {
    if (!el) return;
    if (!generatedAt) { el.textContent = ''; return; }
    try {
      var ts = new Date(generatedAt);
      var diffMs = Date.now() - ts.getTime();
      var label;
      if (diffMs < 60000) {
        label = 'just now';
      } else if (diffMs < 3600000) {
        var mins = Math.floor(diffMs / 60000);
        label = mins + ' min ago';
      } else if (diffMs < 86400000) {
        var hrs = Math.floor(diffMs / 3600000);
        label = hrs + 'h ago';
      } else {
        var days = Math.floor(diffMs / 86400000);
        label = days + 'd ago';
      }
      el.textContent = 'Updated ' + label;
      el.title = ts.toLocaleString();
    } catch (_) {
      el.textContent = '';
    }
  }

  /* -- Refreshing badge helpers (Fix 2 — stale data indicator) ------- */

  /**
   * Show a subtle "↻ Refreshing…" badge next to a section's freshness indicator.
   * @param {string} section - 'stock' | 'options' | 'active'
   */
  function _showRefreshingBadge(section) {
    var ids = {
      stock:   'tmcStockFreshness',
      options: 'tmcOptionsFreshness',
      active:  'tmcActiveTimestamp',
    };
    var parentEl = document.getElementById(ids[section]);
    if (!parentEl) return;
    // Avoid duplicates
    var existing = parentEl.parentNode.querySelector('.tmc-refreshing-badge');
    if (existing) return;
    var badge = document.createElement('span');
    badge.className = 'tmc-refreshing-badge';
    badge.id = 'tmcRefreshing-' + section;
    badge.textContent = '↻ Refreshing\u2026';
    parentEl.insertAdjacentElement('afterend', badge);
  }

  /**
   * Remove the refreshing badge for a section.
   * @param {string} section - 'stock' | 'options' | 'active'
   */
  function _removeRefreshingBadge(section) {
    var badge = document.getElementById('tmcRefreshing-' + section);
    if (badge) badge.remove();
  }

  function actionClass(action) {
    switch ((action || '').toLowerCase()) {
      case 'buy':  return 'tmc-action-buy';
      case 'hold': return 'tmc-action-hold';
      case 'pass': return 'tmc-action-pass';
      default:     return 'tmc-action-unknown';
    }
  }

  /**
   * Generate a human-readable explanation of what makes a trade profitable.
   * Shared across options cards, active trade cards, and trade ticket.
   *
   * Accepts either a TMC normalised candidate, an active trade rec, or a
   * TradeTicket model — field lookup is flexible across naming conventions.
   */
  function getTradeExplanation(trade) {
    var strategy = (trade.strategy_id || trade.strategyId || trade.strategy || '').toLowerCase();
    var symbol = trade.symbol || trade.underlying || '';
    var legs = trade.legs || [];
    var math = trade.math || {};
    var breakeven = trade.breakevens || trade.breakeven || math.breakeven;
    var shortStrike = trade.short_strike || trade.shortStrike
      || (legs.filter(function(l){ return l.side==='short'||l.side==='sell'||l.side==='sell_to_open'; })[0]||{}).strike;
    var longStrike = trade.long_strike || trade.longStrike
      || (legs.filter(function(l){ return l.side==='long'||l.side==='buy'||l.side==='buy_to_open'; })[0]||{}).strike;
    var underlyingPrice = trade.underlying_price || trade.underlyingPrice || trade.current_price;

    function $(v) { return v != null ? '$' + Number(v).toFixed(0) : '?'; }

    switch (strategy) {
      case 'put_credit_spread':
      case 'put_credit':
        return 'Profits if ' + symbol + ' stays ABOVE ' + $(shortStrike) + ' at expiration. '
          + 'Max profit if ' + symbol + ' closes above ' + $(shortStrike) + '. '
          + 'Time decay works in your favor.';

      case 'call_credit_spread':
      case 'call_credit':
        return 'Profits if ' + symbol + ' stays BELOW ' + $(shortStrike) + ' at expiration. '
          + 'Max profit if ' + symbol + ' closes below ' + $(shortStrike) + '. '
          + 'Time decay works in your favor.';

      case 'iron_condor': {
        var ps = legs.filter(function(l){ return (l.option_type||'').toLowerCase()==='put' && (l.side==='short'||l.side==='sell'||l.side==='sell_to_open'); })[0];
        var cs = legs.filter(function(l){ return (l.option_type||'').toLowerCase()==='call' && (l.side==='short'||l.side==='sell'||l.side==='sell_to_open'); })[0];
        var psStr = ps ? $(ps.strike) : '?';
        var csStr = cs ? $(cs.strike) : '?';
        return 'Profits if ' + symbol + ' stays between ' + psStr + ' and ' + csStr + ' at expiration. '
          + 'A range-bound strategy \u2014 you want low volatility and time decay.';
      }

      case 'iron_butterfly': {
        if (breakeven && Array.isArray(breakeven) && breakeven.length === 2) {
          return 'Profits if ' + symbol + ' stays between ' + $(breakeven[0]) + ' and ' + $(breakeven[1]) + '. '
            + 'Max profit if ' + symbol + ' closes exactly at ' + $(shortStrike) + '. '
            + 'Profits shrink as price moves away from center.';
        }
        return 'Profits if ' + symbol + ' stays near ' + $(shortStrike) + '. Range-bound strategy with peaked payoff at center strike.';
      }

      case 'put_debit_spread':
      case 'put_debit': {
        var be = Array.isArray(breakeven) ? breakeven[0] : breakeven;
        var parts = 'Profits if ' + symbol + ' drops' + (be != null ? ' below ' + $(be) + ' (breakeven)' : '') + '.';
        if (underlyingPrice != null) parts += ' Currently at ' + $(underlyingPrice) + '.';
        parts += ' Max profit if ' + symbol + ' closes below ' + $(longStrike) + '.';
        return parts;
      }

      case 'call_debit_spread':
      case 'call_debit': {
        var be2 = Array.isArray(breakeven) ? breakeven[0] : breakeven;
        var parts2 = 'Profits if ' + symbol + ' rises' + (be2 != null ? ' above ' + $(be2) + ' (breakeven)' : '') + '.';
        if (underlyingPrice != null) parts2 += ' Currently at ' + $(underlyingPrice) + '.';
        parts2 += ' Max profit if ' + symbol + ' closes above ' + $(longStrike) + '.';
        return parts2;
      }

      case 'butterfly_debit': {
        var centerLeg = legs.filter(function(l){ return l.side==='short'||l.side==='sell'||l.side==='sell_to_open'; })[0];
        var center = centerLeg ? $(centerLeg.strike) : '?';
        if (breakeven && Array.isArray(breakeven) && breakeven.length === 2) {
          return 'Profits if ' + symbol + ' finishes between ' + $(breakeven[0]) + ' and ' + $(breakeven[1]) + '. '
            + 'Max profit at exactly ' + center + '. '
            + 'A precision bet on where ' + symbol + ' will land.';
        }
        return 'Profits if ' + symbol + ' finishes near ' + center + '. Precision strategy with peaked payoff.';
      }

      case 'calendar_call_spread':
      case 'calendar_put_spread':
        return 'Profits from time decay differential \u2014 near-term option decays faster than far-term. '
          + 'Best if ' + symbol + ' stays near the strike price.';

      case 'diagonal_call_spread':
      case 'diagonal_put_spread':
        return 'Combines directional bias with time decay advantage. '
          + 'Near-term short option decays faster while far-term long retains value.';

      case 'equity':
      case 'equity_long':
      case 'stock_pullback_swing':
      case 'stock_mean_reversion':
      case 'stock_momentum_breakout':
      case 'stock_volatility_expansion':
        return 'Profits if ' + symbol + ' price increases' + (underlyingPrice != null ? ' from current level of ' + $(underlyingPrice) : '') + '.';

      default:
        if (strategy) return 'Strategy: ' + strategy.replace(/_/g, ' ');
        return '';
    }
  }
  // Expose for TradeTicket and other modules
  window.getTradeExplanation = getTradeExplanation;

  /**
   * Build the styled HTML block for the trade explanation.
   * Returns empty string if no explanation is available.
   *
   * For options strategies with sufficient data, renders the full
   * trade education + management guide.  Falls back to the simple
   * one-line explanation for stocks and edge cases.
   */
  function buildExplanationHtml(trade) {
    var guide = buildTradeGuide(trade);
    if (guide) return renderTradeGuide(guide);

    // Fallback: simple one-liner
    var text = getTradeExplanation(trade);
    if (!text) return '';
    return '<div style="background:rgba(0,224,195,0.05);border:1px solid rgba(0,224,195,0.15);'
      + 'border-radius:6px;padding:10px 12px;margin:8px 0;">'
      + '<div style="color:#00e0c3;font-size:0.7rem;font-weight:600;text-transform:uppercase;'
      + 'letter-spacing:0.03em;margin-bottom:4px;">HOW THIS TRADE PROFITS</div>'
      + '<div style="color:rgba(224,224,224,0.8);font-size:0.82rem;line-height:1.5;">' + esc(text) + '</div>'
      + '</div>';
  }

  /* ── Trade Education + Management Guide builders ─────────────── */

  /**
   * Route to the correct strategy guide builder based on strategy_id.
   * Returns null if no structured guide can be built (falls back to
   * the simple one-liner in buildExplanationHtml).
   */
  function buildTradeGuide(trade) {
    var strategy = (trade.strategy_id || trade.strategyId || trade.strategy || '').toLowerCase();
    switch (strategy) {
      case 'put_credit_spread':
      case 'put_credit':
      case 'call_credit_spread':
      case 'call_credit':
        return _buildCreditSpreadGuide(trade, strategy);
      case 'put_debit_spread':
      case 'put_debit':
      case 'call_debit_spread':
      case 'call_debit':
        return _buildDebitSpreadGuide(trade, strategy);
      case 'iron_condor':
        return _buildIronCondorGuide(trade);
      case 'iron_butterfly':
        return _buildIronButterflyGuide(trade);
      case 'butterfly_debit':
        return _buildButterflyGuide(trade);
      case 'calendar_call_spread':
      case 'calendar_put_spread':
        return _buildCalendarGuide(trade, strategy);
      case 'diagonal_call_spread':
      case 'diagonal_put_spread':
        return _buildDiagonalGuide(trade, strategy);
      default:
        return null;
    }
  }

  /** Extract common fields from a trade for guide builders. */
  function _guideFields(trade) {
    var legs = trade.legs || [];
    var math = trade.math || {};
    var shortLegs = legs.filter(function(l) { return l.side === 'short' || l.side === 'sell' || l.side === 'sell_to_open'; });
    var longLegs  = legs.filter(function(l) { return l.side === 'long'  || l.side === 'buy'  || l.side === 'buy_to_open'; });
    return {
      symbol: trade.symbol || trade.underlying || '???',
      legs: legs,
      shortLegs: shortLegs,
      longLegs: longLegs,
      credit: math.net_credit != null ? Number(math.net_credit) : (trade.credit != null ? Number(trade.credit) : null),
      debit: math.net_debit != null ? Number(math.net_debit) : (trade.debit != null ? Number(trade.debit) : null),
      maxProfit: math.max_profit != null ? Number(math.max_profit) : (trade.maxProfit != null ? Number(trade.maxProfit) : null),
      maxLoss: math.max_loss != null ? Number(math.max_loss) : (trade.maxLoss != null ? Number(trade.maxLoss) : null),
      width: math.width != null ? Number(math.width) : (trade.width != null ? Number(trade.width) : null),
      breakeven: trade.breakevens || trade.breakeven || math.breakeven || [],
      dte: trade.dte != null ? Number(trade.dte) : null,
      pop: math.pop != null ? Number(math.pop) : (trade.pop != null ? Number(trade.pop) : null),
      underlyingPrice: trade.underlying_price || trade.underlyingPrice || trade.current_price || null,
    };
  }

  function _$(v) { return v != null ? '$' + Number(v).toFixed(2) : '?'; }
  function _$0(v) { return v != null ? '$' + Number(v).toFixed(0) : '?'; }

  function _buildCreditSpreadGuide(trade, strategy) {
    var f = _guideFields(trade);
    var isPut = strategy.indexOf('put') >= 0;
    var direction = isPut ? 'above' : 'below';
    var dangerDirection = isPut ? 'drops toward' : 'rises toward';

    var shortStrike = null, longStrike = null;
    f.shortLegs.forEach(function(l) { shortStrike = l.strike; });
    f.longLegs.forEach(function(l) { longStrike = l.strike; });

    var credit = f.credit;
    var width = f.width || (shortStrike != null && longStrike != null ? Math.abs(shortStrike - longStrike) : null);
    var maxProfit = f.maxProfit;
    var maxLoss = f.maxLoss != null ? Math.abs(f.maxLoss) : (width != null && credit != null ? (width - credit) * 100 : null);
    if (maxProfit == null && credit != null) maxProfit = credit * 100;

    var breakeven = null;
    if (Array.isArray(f.breakeven) && f.breakeven.length > 0) breakeven = f.breakeven[0];
    else if (typeof f.breakeven === 'number') breakeven = f.breakeven;
    else if (shortStrike != null && credit != null) breakeven = isPut ? shortStrike - credit : shortStrike + credit;

    var profitTarget50 = credit != null ? credit * 0.50 : null;
    var stopLossVal = credit != null ? credit * 2.0 : null;
    var dte = f.dte || 14;
    var timeExit = Math.max(7, Math.round(dte * 0.25));

    var keyLevels = [];
    keyLevels.push({ label: 'Max profit zone', value: f.symbol + ' ' + direction + ' ' + _$0(shortStrike), color: 'green' });
    if (breakeven != null) keyLevels.push({ label: 'Breakeven', value: _$(breakeven), color: 'yellow' });
    keyLevels.push({ label: 'Max loss zone', value: f.symbol + ' ' + (isPut ? 'below' : 'above') + ' ' + _$0(longStrike), color: 'red' });
    keyLevels.push({ label: 'Short strike', value: _$0(shortStrike), color: 'white' });

    return {
      title: 'HOW THIS TRADE WORKS',
      profitLoss: 'This is an INCOME trade. '
        + (credit != null ? 'You collect ' + _$(credit) + ' per share' + (maxProfit != null ? ' (' + _$0(maxProfit) + ' per contract)' : '') + ' upfront. ' : '')
        + 'You keep this premium if ' + f.symbol + ' stays ' + direction + ' ' + _$0(shortStrike) + ' through expiration.',
      keyLevels: keyLevels,
      managementPlan: [
        {
          label: 'Take profit at 50%',
          detail: credit != null && profitTarget50 != null
            ? 'When the spread can be bought back for ' + _$(profitTarget50) + ', keeping '
              + _$(credit - profitTarget50) + ' profit (' + _$0((credit - profitTarget50) * 100)
              + ' per contract). This typically happens ' + Math.round(dte * 0.5) + '\u2013' + Math.round(dte * 0.7) + ' days in if '
              + f.symbol + ' stays ' + direction + ' the short strike.'
            : 'When the spread loses half its value, buy it back and lock in profit.',
        },
        {
          label: 'Stop loss at 2\u00D7 credit',
          detail: stopLossVal != null
            ? 'If the spread reaches ' + _$(stopLossVal) + ', close the trade. '
              + 'Your loss: ' + _$0((stopLossVal - credit) * 100) + ' per contract. '
              + (maxLoss != null ? 'This protects against the ' + _$0(maxLoss) + ' max loss.' : '')
            : 'If the spread doubles in value against you, close it to limit damage.',
        },
        {
          label: 'Close by ' + timeExit + ' DTE',
          detail: 'If the trade hasn\u2019t hit profit target or stop loss by '
            + timeExit + ' days to expiration, consider closing. '
            + 'Gamma risk accelerates in the final week \u2014 small price moves cause big P&L swings.',
        },
      ],
      watchFor: [
        { signal: f.symbol + ' ' + dangerDirection + ' ' + _$0(shortStrike), meaning: 'Your short strike is being tested. The trade is at risk.', action: 'Watch closely. If it breaks through, your stop loss should trigger.', type: 'danger' },
        { signal: 'VIX spike / market volatility increase', meaning: 'Higher IV inflates the spread value even if price hasn\u2019t moved. Paper losses may appear.', action: 'Don\u2019t panic. If the price level is still safe, IV expansion is temporary.', type: 'caution' },
        { signal: 'Earnings or major event within the DTE window', meaning: 'Binary events can cause overnight gaps through your strikes.', action: 'Consider closing before the event if the trade is profitable.', type: 'caution' },
        { signal: f.symbol + ' moving AWAY from ' + _$0(shortStrike), meaning: 'The trade is working. Time decay is accelerating your profit.', action: 'Let it run toward the 50% profit target.', type: 'positive' },
      ],
      thetaBehavior: 'Time decay works in your favor. Each day ' + f.symbol + ' stays ' + direction
        + ' ' + _$0(shortStrike) + ', the spread loses value (which is what you want \u2014 you sold it). '
        + 'Theta is slow in the first ~' + Math.round(dte * 0.3) + ' days, then accelerates. '
        + 'The fastest decay is in the final 7\u201310 days, which is also when gamma risk peaks \u2014 '
        + 'hence the ' + timeExit + ' DTE exit rule.',
    };
  }

  function _buildDebitSpreadGuide(trade, strategy) {
    var f = _guideFields(trade);
    var isPut = strategy.indexOf('put') >= 0;
    var direction = isPut ? 'down' : 'up';
    var targetDirection = isPut ? 'below' : 'above';
    var wrongDirection = isPut ? 'up' : 'down';

    var longStrike = null, shortStrike = null;
    f.longLegs.forEach(function(l) { longStrike = l.strike; });
    f.shortLegs.forEach(function(l) { shortStrike = l.strike; });

    var debit = f.debit;
    var width = f.width || (longStrike != null && shortStrike != null ? Math.abs(longStrike - shortStrike) : null);
    var maxLoss = f.maxLoss != null ? Math.abs(f.maxLoss) : (debit != null ? debit * 100 : null);
    var maxProfit = f.maxProfit || (width != null && debit != null ? (width - debit) * 100 : null);

    var breakeven = null;
    if (Array.isArray(f.breakeven) && f.breakeven.length > 0) breakeven = f.breakeven[0];
    else if (typeof f.breakeven === 'number') breakeven = f.breakeven;
    else if (longStrike != null && debit != null) breakeven = isPut ? longStrike - debit : longStrike + debit;

    var profitTarget75 = debit != null && width != null ? debit + (width - debit) * 0.75 : null;
    var dte = f.dte || 14;
    var reassessDte = Math.max(5, Math.round(dte * 0.3));

    var keyLevels = [];
    keyLevels.push({ label: 'Need price ' + direction, value: f.symbol + ' must move ' + direction, color: 'cyan' });
    if (breakeven != null) keyLevels.push({ label: 'Breakeven', value: _$(breakeven), color: 'yellow' });
    keyLevels.push({ label: 'Max profit zone', value: targetDirection + ' ' + _$0(shortStrike), color: 'green' });

    return {
      title: 'HOW THIS TRADE WORKS',
      profitLoss: 'This is a DIRECTIONAL trade. '
        + (debit != null ? 'You paid ' + _$(debit) + ' per share (' + _$0(maxLoss) + ' per contract) ' : '')
        + 'for the right to profit if ' + f.symbol + ' moves ' + direction + '. '
        + (maxProfit != null ? 'Max profit of ' + _$0(maxProfit) + ' if ' + f.symbol + ' closes ' + targetDirection + ' ' + _$0(shortStrike) + ' at expiration.' : ''),
      keyLevels: keyLevels,
      managementPlan: [
        {
          label: 'Take profit at 75%',
          detail: profitTarget75 != null
            ? 'When the spread reaches ' + _$(profitTarget75) + ' (75% of max width), sell to close. '
              + 'Profit: ' + _$0((profitTarget75 - debit) * 100) + ' per contract. '
              + 'Don\u2019t hold for max profit \u2014 it requires a perfect pin at expiration.'
            : 'When the spread reaches 75% of the width, take profit. Don\u2019t wait for max.',
        },
        {
          label: 'Cut losses early',
          detail: 'If ' + f.symbol + ' moves decisively ' + wrongDirection + ' (wrong direction), close the trade. '
            + (maxLoss != null ? 'Your max loss is the ' + _$0(maxLoss) + ' debit \u2014 don\u2019t let it all expire worthless if the thesis is clearly wrong.' : ''),
        },
        {
          label: 'Reassess at ' + reassessDte + ' DTE',
          detail: 'Directional trades lose value rapidly as expiration approaches. '
            + 'If ' + f.symbol + ' hasn\u2019t moved ' + direction + ' meaningfully by ' + reassessDte + ' DTE, close for whatever remains.',
        },
      ],
      watchFor: [
        { signal: f.symbol + ' moving ' + direction + ' toward breakeven', meaning: 'Your thesis is playing out. The trade is working.', action: 'Hold toward the 75% profit target.', type: 'positive' },
        { signal: f.symbol + ' moving ' + wrongDirection + ' (wrong direction)', meaning: 'Your directional thesis is wrong or early.', action: 'If the move is decisive, cut the loss. Don\u2019t hope.', type: 'danger' },
        { signal: 'IV crush after an event (earnings, Fed)', meaning: 'Even if price moves your way, IV dropping can reduce spread value.', action: 'Debit spreads are partially protected (long and short legs offset), but be aware.', type: 'caution' },
      ],
      thetaBehavior: 'Time decay works AGAINST this trade. Every day without ' + f.symbol
        + ' moving ' + direction + ', the spread loses value. Take the 75% profit quickly and don\u2019t hold hoping for max gain. The clock is your enemy.',
    };
  }

  function _buildIronCondorGuide(trade) {
    var f = _guideFields(trade);
    var shortPut = null, shortCall = null, longPut = null, longCall = null;
    f.legs.forEach(function(l) {
      var type = (l.option_type || '').toLowerCase();
      var isShort = l.side === 'short' || l.side === 'sell' || l.side === 'sell_to_open';
      if (type === 'put'  && isShort) shortPut  = l.strike;
      if (type === 'call' && isShort) shortCall = l.strike;
      if (type === 'put'  && !isShort) longPut  = l.strike;
      if (type === 'call' && !isShort) longCall = l.strike;
    });

    var credit = f.credit;
    var dte = f.dte || 14;
    var timeExit = Math.max(7, Math.round(dte * 0.25));

    var keyLevels = [];
    keyLevels.push({ label: 'Max profit zone', value: _$0(shortPut) + ' \u2013 ' + _$0(shortCall), color: 'green' });
    if (Array.isArray(f.breakeven) && f.breakeven.length === 2) {
      keyLevels.push({ label: 'Lower breakeven', value: _$(f.breakeven[0]), color: 'yellow' });
      keyLevels.push({ label: 'Upper breakeven', value: _$(f.breakeven[1]), color: 'yellow' });
    }
    keyLevels.push({ label: 'Put side max loss', value: 'below ' + _$0(longPut), color: 'red' });
    keyLevels.push({ label: 'Call side max loss', value: 'above ' + _$0(longCall), color: 'red' });

    return {
      title: 'HOW THIS TRADE WORKS',
      profitLoss: 'This is a NEUTRAL / RANGE trade. You collected premium by selling both a call spread above and a put spread below. '
        + 'You profit if ' + f.symbol + ' stays between ' + _$0(shortPut) + ' and ' + _$0(shortCall) + ' through expiration.'
        + (f.maxProfit != null ? ' Max profit: ' + _$0(f.maxProfit) + '.' : '')
        + (f.maxLoss != null ? ' Max loss: ' + _$0(Math.abs(f.maxLoss)) + '.' : ''),
      keyLevels: keyLevels,
      managementPlan: [
        { label: 'Take profit at 50%', detail: 'When you can buy the entire condor back for 50% of what you sold it for, close the whole position.' },
        { label: 'Stop loss at 2\u00D7 credit', detail: 'If the condor value doubles, close the whole position. Don\u2019t try to manage just one side.' },
        {
          label: 'Do NOT leg out of one side',
          detail: 'If one side is tested, close the ENTIRE condor \u2014 not just the losing side. Closing one side turns a defined-risk trade into an undefined-risk naked spread.',
        },
        { label: 'Close by ' + timeExit + ' DTE', detail: 'Gamma risk accelerates in the final week. Close before then if profit target not met.' },
      ],
      watchFor: [
        { signal: f.symbol + ' approaching either short strike', meaning: 'One side of the condor is at risk.', action: 'If price breaks through a short strike, close the full condor.', type: 'danger' },
        { signal: f.symbol + ' staying in the middle of the range', meaning: 'Perfect scenario. Both sides decay simultaneously.', action: 'Let theta do its work. Target 50% profit.', type: 'positive' },
        { signal: 'Sudden VIX spike', meaning: 'Both sides inflate \u2014 paper losses appear even if price hasn\u2019t moved.', action: 'If price is still in the range, this is usually temporary.', type: 'caution' },
      ],
      thetaBehavior: 'Time decay is your best friend in an iron condor. Both the call side and put side lose value simultaneously. '
        + 'Theta accelerates as expiration approaches, but gamma risk also increases \u2014 if ' + f.symbol
        + ' breaks out of the range late in the trade, losses mount quickly.',
    };
  }

  function _buildIronButterflyGuide(trade) {
    var f = _guideFields(trade);
    var shortStrike = null;
    f.legs.forEach(function(l) {
      if (l.side === 'short' || l.side === 'sell' || l.side === 'sell_to_open') shortStrike = l.strike;
    });

    var keyLevels = [];
    keyLevels.push({ label: 'Max profit at', value: _$0(shortStrike), color: 'green' });
    if (Array.isArray(f.breakeven) && f.breakeven.length === 2) {
      keyLevels.push({ label: 'Lower breakeven', value: _$(f.breakeven[0]), color: 'yellow' });
      keyLevels.push({ label: 'Upper breakeven', value: _$(f.breakeven[1]), color: 'yellow' });
    }

    return {
      title: 'HOW THIS TRADE WORKS',
      profitLoss: 'This is a NEUTRAL / PINNING trade. Max profit if ' + f.symbol + ' closes exactly at '
        + _$0(shortStrike) + '. Profits shrink as price moves away from center. '
        + (f.maxProfit != null ? 'Max profit: ' + _$0(f.maxProfit) + '. ' : '')
        + (f.maxLoss != null ? 'Max loss: ' + _$0(Math.abs(f.maxLoss)) + '.' : ''),
      keyLevels: keyLevels,
      managementPlan: [
        { label: 'Take profit at 25\u201350%', detail: 'Iron butterflies rarely reach max (requires exact pin). Take 25\u201350% of max and move on.' },
        { label: 'Stop loss at 2\u00D7 debit/credit', detail: 'If the position moves strongly against you, close for damage control.' },
      ],
      watchFor: [
        { signal: f.symbol + ' pinning near ' + _$0(shortStrike), meaning: 'The ideal scenario \u2014 maximum premium capture.', action: 'Watch for profit target.', type: 'positive' },
        { signal: f.symbol + ' breaking away from center', meaning: 'Profit zone is narrow. The trade loses value fast.', action: 'Be ready to close if breakeven is threatened.', type: 'danger' },
      ],
      thetaBehavior: 'Complex theta profile. Near expiration, if price is at the center strike, theta accelerates in your favor dramatically. '
        + 'If price is away from center, theta works against the position as the wings decay unevenly.',
    };
  }

  function _buildButterflyGuide(trade) {
    var f = _guideFields(trade);
    var centerStrike = null;
    f.legs.forEach(function(l) {
      if (l.side === 'short' || l.side === 'sell' || l.side === 'sell_to_open') centerStrike = l.strike;
    });

    var keyLevels = [];
    keyLevels.push({ label: 'Max profit at', value: _$0(centerStrike), color: 'green' });
    if (Array.isArray(f.breakeven) && f.breakeven.length === 2) {
      keyLevels.push({ label: 'Lower breakeven', value: _$(f.breakeven[0]), color: 'yellow' });
      keyLevels.push({ label: 'Upper breakeven', value: _$(f.breakeven[1]), color: 'yellow' });
    }

    return {
      title: 'HOW THIS TRADE WORKS',
      profitLoss: 'This is a PINNING trade. You profit most if ' + f.symbol + ' closes near '
        + _$0(centerStrike) + ' at expiration. Low cost, low probability, but high reward relative to debit paid.'
        + (f.maxProfit != null ? ' Max profit: ' + _$0(f.maxProfit) + '.' : '')
        + (f.maxLoss != null ? ' Risk: ' + _$0(Math.abs(f.maxLoss)) + ' (debit paid).' : ''),
      keyLevels: keyLevels,
      managementPlan: [
        { label: 'Take profit at 50%', detail: 'Butterflies rarely reach max profit (requires exact pin). Take 50% of max and move on \u2014 this is a win.' },
        { label: 'Stop loss at 1\u00D7 debit', detail: 'If the butterfly is clearly not going to work (price far from center with time running out), close for whatever value remains.' },
        { label: 'Reassess at 5 DTE', detail: 'If price is far from ' + _$0(centerStrike) + ' with < 5 DTE, close for salvage value rather than letting it expire worthless.' },
      ],
      watchFor: [
        { signal: f.symbol + ' near ' + _$0(centerStrike) + ' with declining IV', meaning: 'Perfect conditions \u2014 price is pinning and vol is dropping.', action: 'This is the dream scenario. Watch for 50% profit.', type: 'positive' },
        { signal: f.symbol + ' far from ' + _$0(centerStrike), meaning: 'The pin thesis isn\u2019t working.', action: 'If significant time remains, hold \u2014 stock could return. If < 5 DTE, close for salvage.', type: 'danger' },
      ],
      thetaBehavior: 'Complex theta. Early on, theta is minimal. Near expiration AND near the center strike, theta accelerates dramatically in your favor. '
        + 'But if price is away from center, theta works against you as the wings decay.',
    };
  }

  function _buildCalendarGuide(trade, strategy) {
    var f = _guideFields(trade);
    var isPut = strategy.indexOf('put') >= 0;
    var strike = null;
    if (f.legs.length > 0) strike = f.legs[0].strike;

    return {
      title: 'HOW THIS TRADE WORKS',
      profitLoss: 'This is a TIME DECAY trade. You profit from the near-term option decaying faster than the far-term option. '
        + 'Best if ' + f.symbol + ' stays near ' + _$0(strike) + '. '
        + (f.maxLoss != null ? 'Risk limited to ' + _$0(Math.abs(f.maxLoss)) + ' (net debit paid).' : ''),
      keyLevels: [
        { label: 'Sweet spot', value: f.symbol + ' near ' + _$0(strike), color: 'green' },
      ],
      managementPlan: [
        { label: 'Take profit at 25\u201350%', detail: 'Calendar spreads have modest profit potential. Take gains when the near-term option has decayed significantly.' },
        { label: 'Close before near-term expiration', detail: 'Close the entire spread before the short leg expires. Don\u2019t let it expire and leave a naked long option.' },
      ],
      watchFor: [
        { signal: f.symbol + ' staying near ' + _$0(strike), meaning: 'Ideal \u2014 both options are at ATM, but the short decays faster.', action: 'Hold and let the time differential work.', type: 'positive' },
        { signal: f.symbol + ' moving far from ' + _$0(strike), meaning: 'Both options lose extrinsic value, reducing the spread\u2019s value.', action: 'Consider closing if the move is large.', type: 'danger' },
        { signal: 'IV increase', meaning: 'Benefits the far-term option more than the near-term, expanding spread value.', action: 'This is a tailwind for calendars.', type: 'positive' },
      ],
      thetaBehavior: 'The near-term short option decays faster than the far-term long option \u2014 this differential IS your profit. '
        + 'Works best when ' + f.symbol + ' stays near the strike and implied volatility is stable or rising.',
    };
  }

  function _buildDiagonalGuide(trade, strategy) {
    var f = _guideFields(trade);
    var isPut = strategy.indexOf('put') >= 0;
    var direction = isPut ? 'down' : 'up';
    var shortStrike = null, longStrike = null;
    f.shortLegs.forEach(function(l) { shortStrike = l.strike; });
    f.longLegs.forEach(function(l) { longStrike = l.strike; });

    return {
      title: 'HOW THIS TRADE WORKS',
      profitLoss: 'This is a DIRECTIONAL + TIME DECAY trade. Combines a directional bias ' + direction
        + ' with a time decay advantage. The near-term short at ' + _$0(shortStrike)
        + ' decays faster while the far-term long at ' + _$0(longStrike) + ' retains value.'
        + (f.maxLoss != null ? ' Risk limited to ' + _$0(Math.abs(f.maxLoss)) + '.' : ''),
      keyLevels: [
        { label: 'Short strike (near-term)', value: _$0(shortStrike), color: 'yellow' },
        { label: 'Long strike (far-term)', value: _$0(longStrike), color: 'cyan' },
      ],
      managementPlan: [
        { label: 'Take profit at 25\u201350%', detail: 'Diagonals have moderate profit potential. Take gains when available.' },
        { label: 'Close before short expiration', detail: 'Close the entire spread before the near-term short expires to avoid naked long exposure.' },
        { label: 'Roll the short leg', detail: 'If the short leg expires worthless (ideal), consider selling a new near-term option against the remaining long.' },
      ],
      watchFor: [
        { signal: f.symbol + ' moving ' + direction + ' toward short strike', meaning: 'Directional thesis working and short leg captures premium.', action: 'Ideal scenario. Monitor for profit target.', type: 'positive' },
        { signal: f.symbol + ' blowing through the short strike', meaning: 'The short leg is deep ITM, reducing or eliminating the time decay edge.', action: 'Consider closing or rolling the short leg to a further strike.', type: 'danger' },
      ],
      thetaBehavior: 'The near-term short option decays faster, which is your income engine. '
        + 'The far-term long gives directional exposure and protection. Works best when the move is gradual, not explosive.',
    };
  }

  /** Render a structured guide object into HTML. */
  function renderTradeGuide(guide) {
    var html = '<div class="trade-guide">';
    html += '<div class="guide-header">' + esc(guide.title) + '</div>';
    html += '<div class="guide-profit-loss">' + esc(guide.profitLoss) + '</div>';

    // Key levels
    if (guide.keyLevels && guide.keyLevels.length > 0) {
      html += '<div class="guide-section"><div class="guide-section-title">KEY LEVELS</div>';
      guide.keyLevels.forEach(function(l) {
        var borderColor = l.color === 'green' ? '#4ade80' : l.color === 'red' ? '#f87171' : l.color === 'yellow' ? '#fbbf24' : l.color === 'cyan' ? '#22d3ee' : '#94a3b8';
        html += '<div class="guide-level" style="border-left:3px solid ' + borderColor + ';">'
          + '<span class="level-label">' + esc(l.label) + ':</span> '
          + '<span class="level-value">' + esc(l.value) + '</span></div>';
      });
      html += '</div>';
    }

    // Management plan
    if (guide.managementPlan && guide.managementPlan.length > 0) {
      html += '<div class="guide-section"><div class="guide-section-title">MANAGEMENT PLAN</div>';
      guide.managementPlan.forEach(function(m) {
        html += '<div class="guide-mgmt-item"><div class="mgmt-label">' + esc(m.label) + '</div>'
          + '<div class="mgmt-detail">' + esc(m.detail) + '</div></div>';
      });
      html += '</div>';
    }

    // What to watch for
    if (guide.watchFor && guide.watchFor.length > 0) {
      html += '<div class="guide-section"><div class="guide-section-title">WHAT TO WATCH FOR</div>';
      guide.watchFor.forEach(function(w) {
        var icon = w.type === 'positive' ? '\u2705' : w.type === 'danger' ? '\u26A0\uFE0F' : '\uD83D\uDC41\uFE0F';
        html += '<div class="guide-watch ' + esc(w.type) + '">'
          + '<div class="watch-signal">' + icon + ' ' + esc(w.signal) + '</div>'
          + '<div class="watch-meaning">' + esc(w.meaning) + '</div>'
          + '<div class="watch-action"><strong>Action:</strong> ' + esc(w.action) + '</div>'
          + '</div>';
      });
      html += '</div>';
    }

    // Theta behavior
    if (guide.thetaBehavior) {
      html += '<div class="guide-section"><div class="guide-section-title">TIME DECAY</div>'
        + '<div class="guide-theta">' + esc(guide.thetaBehavior) + '</div></div>';
    }

    html += '</div>';
    return html;
  }

  /* ── Position Sizing Display (lazy-load-on-expand) ──────── */

  /** Cache: tradeKey → { sizing, symbol, maxLoss } */
  var _sizingCache = {};

  /**
   * Build a sizing container div inside the card body.
   * Returns empty string if the trade has no max loss.
   * The container starts empty — populated on first expand via toggle listener.
   */
  function _buildSizingPlaceholder(trade, idx) {
    var maxLoss = _extractMaxLossPerContract(trade);
    if (maxLoss == null || maxLoss <= 0) return '';
    var tradeKey = trade._tradeKey || '';
    var id = 'sizing-' + idx + '-' + (trade.symbol || '').replace(/[^a-zA-Z0-9]/g, '');
    return '<div id="' + id + '" class="sizing-placeholder" '
      + 'data-symbol="' + esc(trade.symbol || '') + '" '
      + 'data-scanner-key="' + esc(trade.strategyId || trade.strategy || '') + '" '
      + 'data-max-loss="' + maxLoss + '" '
      + 'data-trade-key="' + esc(tradeKey) + '" '
      + 'data-sizing-state="idle">'
      + '</div>';
  }

  /**
   * Extract per-contract max loss from a trade object.
   * Input: trade object (normalized candidate).
   * Formula: math.max_loss or maxLoss, or (width - credit) * 100, or debit * 100
   */
  function _extractMaxLossPerContract(trade) {
    var math = trade.math || {};
    if (math.max_loss != null) return Math.abs(Number(math.max_loss));
    if (trade.maxLoss != null) return Math.abs(Number(trade.maxLoss));
    var credit = trade.credit != null ? Number(trade.credit) : null;
    var debit = trade.debit != null ? Number(trade.debit) : null;
    var width = trade.width != null ? Number(trade.width) : null;
    if (width != null && credit != null) return Math.abs(width - credit) * 100;
    if (debit != null) return Math.abs(debit) * 100;
    return null;
  }

  /**
   * Lazy-load sizing for a single card when its <details> is expanded.
   * Called from the toggle event listener. Skips if already loaded or in-flight.
   */
  function _loadSizingForCard(detailsEl) {
    var card = detailsEl.closest('.trade-card');
    if (!card) return;
    var el = card.querySelector('.sizing-placeholder');
    if (!el) return;

    var state = el.getAttribute('data-sizing-state');
    if (state === 'loaded' || state === 'loading') return;

    var symbol = el.getAttribute('data-symbol');
    var scannerKey = el.getAttribute('data-scanner-key');
    var maxLoss = parseFloat(el.getAttribute('data-max-loss'));
    var tradeKey = el.getAttribute('data-trade-key') || '';
    if (!symbol || !maxLoss || maxLoss <= 0) return;

    // Check cache first
    if (_sizingCache[tradeKey] && _sizingCache[tradeKey].sizing) {
      el.setAttribute('data-sizing-state', 'loaded');
      el.innerHTML = _renderSizingResult(_sizingCache[tradeKey].sizing, symbol, maxLoss);
      return;
    }

    if (!api || !api.getRiskSize) return;
    el.setAttribute('data-sizing-state', 'loading');
    el.innerHTML = '<div class="sizing-loading">Calculating position size\u2026</div>';

    api.getRiskSize({
      symbol: symbol,
      scanner_key: scannerKey || '',
      max_loss_per_contract: maxLoss,
      account_mode: _getAccountMode(),
    }).then(function(resp) {
      if (!resp || !resp.ok || !resp.sizing) {
        var errMsg = (resp && resp.error) || 'Sizing unavailable';
        el.innerHTML = '<div class="sizing-error">' + esc(errMsg) + '</div>';
        el.setAttribute('data-sizing-state', 'loaded');
        return;
      }
      _sizingCache[tradeKey] = { sizing: resp.sizing, symbol: symbol, maxLoss: maxLoss };
      el.setAttribute('data-sizing-state', 'loaded');
      el.innerHTML = _renderSizingResult(resp.sizing, symbol, maxLoss);
    }).catch(function(err) {
      console.warn('[TMC] Sizing fetch failed for ' + symbol, err);
      el.innerHTML = '<div class="sizing-error">Sizing unavailable</div>';
      el.setAttribute('data-sizing-state', 'idle');
    });
  }

  /** Get current account mode from the TMC UI (default "paper"). */
  function _getAccountMode() {
    var sel = document.getElementById('tmcAccountMode')
           || document.getElementById('accountModeSelect');
    if (sel && sel.value) return sel.value;
    return 'paper';
  }

  /**
   * Render the position sizing result — two-column layout.
   * Left: big contract number. Right: detail rows.
   */
  function _renderSizingResult(sizing, symbol, maxLoss) {
    if (sizing.blocked) {
      return '<div class="sizing-result sizing-blocked">'
        + '<div class="sizing-label">POSITION SIZE</div>'
        + '<div class="sizing-blocked-msg">\u26D4 ' + esc(sizing.block_reason || 'Blocked') + '</div>'
        + '</div>';
    }

    var suggested = sizing.suggested_contracts || 0;
    var totalRisk = sizing.total_risk || 0;
    var riskPct = sizing.risk_pct_of_equity || 0;
    var binding = sizing.binding_constraint || '';
    var bindingLabel = _bindingConstraintLabel(binding);
    var maxLossFmt = maxLoss ? '$' + maxLoss.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}) : '—';

    var html = '<div class="sizing-result">';
    html += '<div class="sizing-label">POSITION SIZE</div>';
    html += '<div class="sizing-columns">';

    // Left: big contract number
    html += '<div class="sizing-contracts">';
    html += '<span class="sizing-qty">' + suggested + '</span>';
    html += '<span class="sizing-unit">contract' + (suggested !== 1 ? 's' : '') + '</span>';
    html += '</div>';

    // Right: detail rows
    html += '<div class="sizing-details">';
    html += '<div class="sizing-row"><span class="sizing-row-label">Risk / contract</span><span class="sizing-row-value">' + maxLossFmt + '</span></div>';
    html += '<div class="sizing-row"><span class="sizing-row-label">Total risk</span><span class="sizing-row-value">$' + totalRisk.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}) + '</span></div>';
    html += '<div class="sizing-row"><span class="sizing-row-label">% of equity</span><span class="sizing-row-value">' + riskPct.toFixed(1) + '%</span></div>';
    html += '<div class="sizing-row"><span class="sizing-row-label">Sized by</span><span class="sizing-row-value">' + esc(bindingLabel) + '</span></div>';
    html += '</div>';

    html += '</div>'; // .sizing-columns

    // Warnings
    if (sizing.warnings && sizing.warnings.length > 0) {
      html += '<div class="sizing-warnings">';
      sizing.warnings.forEach(function(w) {
        html += '<div class="sizing-warn">\u26A0\uFE0F ' + esc(w) + '</div>';
      });
      html += '</div>';
    }

    html += '</div>';
    return html;
  }

  function _bindingConstraintLabel(binding) {
    var map = {
      per_trade: 'Per-trade limit',
      per_underlying: 'Per-underlying limit',
      total_portfolio: 'Total portfolio risk',
      account_reserve: 'Account reserve',
      directional: 'Directional concentration',
    };
    return map[binding] || binding || 'Unknown';
  }

  /** Get cached sizing for a trade key (used by execution pre-fill). */
  function _getCachedSizing(tradeKey) {
    return _sizingCache[tradeKey] || null;
  }

  /** Clear sizing cache (e.g. when account mode changes). */
  function _clearSizingCache() {
    _sizingCache = {};
    document.querySelectorAll('.sizing-placeholder').forEach(function(el) {
      el.setAttribute('data-sizing-state', 'idle');
      el.innerHTML = '';
    });
  }

  /* ── Risk State Bar ─────────────────────────────────────── */

  /** Load portfolio risk state and render the utilization bar in TMC header. */
  function _loadRiskStateBar() {
    if (!api || !api.getRiskState) return;
    api.getRiskState(_getAccountMode()).then(function(resp) {
      if (!resp || !resp.ok) return;
      _renderRiskBar(resp);
    }).catch(function(err) {
      console.warn('[TMC] Risk state fetch failed', err);
    });
  }

  /** Render the risk utilization bar into the TMC banner area. */
  function _renderRiskBar(state) {
    var container = document.getElementById('tmcRiskBar');
    if (!container) return;

    var equity = state.equity || 0;
    var committed = state.committed_risk || 0;
    var available = state.available_risk_budget || 0;
    var positions = state.open_position_count || 0;
    var utilPct = equity > 0 ? Math.min(100, (committed / equity) * 100) : 0;

    // Color: green < 50%, yellow 50-75%, red > 75%
    var barColor = utilPct < 50 ? '#10b981' : utilPct < 75 ? '#f59e0b' : '#ef4444';

    var html = '<div class="risk-bar">';
    html += '<div class="risk-bar-label">';
    html += '<span>Risk Budget</span>';
    html += '<span>' + utilPct.toFixed(0) + '% used \u00b7 ' + positions + ' position' + (positions !== 1 ? 's' : '') + '</span>';
    html += '</div>';
    html += '<div class="risk-bar-track">';
    html += '<div class="risk-bar-fill" style="width:' + utilPct.toFixed(1) + '%;background:' + barColor + ';"></div>';
    html += '</div>';
    html += '<div class="risk-bar-detail">';
    html += '<span>Committed: $' + committed.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}) + '</span>';
    html += '<span>Available: $' + available.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}) + '</span>';
    html += '<span>Equity: $' + equity.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0}) + '</span>';
    html += '</div>';
    html += '</div>';

    container.innerHTML = html;
    container.style.display = '';
  }

  /* -- DOM builders -------------------------------------------------- */

  function buildMetric(label, value) {
    return '<div class="tmc-metric"><span class="tmc-metric-label">' +
      esc(label) + '</span><span class="tmc-metric-value">' +
      esc(value) + '</span></div>';
  }

  function buildListSection(items, title, cls) {
    if (!items || items.length === 0) return '';
    var html = '<div class="' + cls + '"><div class="tmc-points-label">' +
      esc(title) + '</div><ul class="tmc-points-list">';
    items.forEach(function (item) { html += '<li>' + esc(item) + '</li>'; });
    html += '</ul></div>';
    return html;
  }

  function showEmptyGrid(grid, countEl, msg) {
    if (grid) {
      grid.innerHTML =
        '<div class="tmc-empty-state">' +
          '<div class="tmc-empty-icon">&#9678;</div>' +
          '<div class="tmc-empty-text">' + esc(msg) + '</div>' +
        '</div>';
    }
    if (countEl) countEl.textContent = '0';
  }

  /* -- Unified workflow response handler ----------------------------- */

  /**
   * Handles a TMC workflow response envelope { status, data }.
   * Returns { ok, status, data, candidates } or calls showEmpty and returns null.
   *
   * @param {object} resp       - Response from /api/tmc/workflows/.../latest
   * @param {Element} grid      - Grid element to clear/populate
   * @param {Element} countEl   - Count badge element
   * @param {Element} qualEl    - Quality badge element
   * @param {Element} statusEl  - Status badge element
   * @param {string}  label     - "stock" or "options" for messages
   * @returns {object|null}     - { status, data, candidates } or null
   */
  function handleWorkflowResponse(resp, grid, countEl, qualEl, statusEl, label) {
    var info = getStatusInfo(resp.status);
    updateStatusBadge(statusEl, resp.status);

    // Failed / unavailable
    if (info.isError) {
      showEmptyGrid(grid, countEl, 'Workflow ' + label + ': ' + info.label.toLowerCase());
      if (qualEl) qualEl.textContent = '';
      return null;
    }

    // No output yet
    if (info.isEmpty || !resp.data) {
      showEmptyGrid(grid, countEl, 'No ' + label + ' opportunities available yet');
      if (qualEl) qualEl.textContent = '';
      return null;
    }

    var data = resp.data;
    if (qualEl) qualEl.textContent = '';

    var candidates = data.candidates || [];
    if (countEl) countEl.textContent = String(candidates.length);

    if (candidates.length === 0) {
      showEmptyGrid(grid, countEl, 'No ' + label + ' candidates found');
      return null;
    }

    return { status: resp.status, data: data, candidates: candidates };
  }

  /* =================================================================
   *  NORMALIZATION LAYER
   *
   *  Small mapping helpers that absorb field-name variation between
   *  backend compact read models and the card builders.  Prevents
   *  brittle direct coupling to exact backend field names.
   * ================================================================= */

  /**
   * Normalize a raw stock candidate from the compact read model.
   *
   * Input fields (from compact stock candidate in output.json — Prompt 12C):
   *   symbol, scanner_key, scanner_name, setup_type, direction,
   *   source_scanners (list[str]),
   *   setup_quality (0-100), confidence (0-1), rank,
   *   thesis_summary (list[str]), supporting_signals (list[str]),
   *   risk_flags (list[str]), entry_context, market_regime,
   *   risk_environment, market_state_ref, vix, regime_tags, support_state,
   *   market_picture_summary { engines_available, engines_total, engine_summaries },
   *   top_metrics, review_summary,
   *   model_recommendation, model_confidence, model_score,
   *   model_review_summary, model_key_factors (list[str]),
   *   model_caution_notes (list[str])
   */
  function normalizeStockCandidate(raw) {
    // Derive action badge from direction field.
    var dir = (raw.direction || '').toLowerCase();
    var action = dir === 'long' ? 'buy' : dir === 'short' ? 'sell' : dir || null;

    return {
      symbol:          raw.symbol || null,
      action:          action,
      setupQuality:    raw.setup_quality != null ? raw.setup_quality : null,
      confidence:      raw.confidence != null ? raw.confidence : null,
      rank:            raw.rank != null ? raw.rank : null,
      rationale:       raw.review_summary || null,
      thesis:          Array.isArray(raw.thesis_summary) ? raw.thesis_summary : [],
      points:          Array.isArray(raw.supporting_signals) ? raw.supporting_signals : [],
      risks:           Array.isArray(raw.risk_flags) ? raw.risk_flags : [],
      scannerName:     raw.scanner_name || raw.scanner_key || null,
      setupType:       raw.setup_type || null,
      topMetrics:      raw.top_metrics || {},
      marketRegime:    raw.market_regime || null,
      riskEnvironment: raw.risk_environment || null,
      // Multi-scanner provenance (12C)
      sourceScanners:  Array.isArray(raw.source_scanners) ? raw.source_scanners : [],
      // Market Picture summary (12C)
      marketPictureSummary: raw.market_picture_summary || null,
      // Market state context (12C)
      marketStateRef:  raw.market_state_ref || null,
      vix:             raw.vix != null ? raw.vix : null,
      regimeTags:      Array.isArray(raw.regime_tags) ? raw.regime_tags : [],
      supportState:    raw.support_state || null,
      // Model review (12C)
      modelRecommendation: raw.model_recommendation || null,
      modelConfidence:     raw.model_confidence != null ? raw.model_confidence : null,
      modelScore:          raw.model_score != null ? raw.model_score : null,
      modelReviewSummary:  raw.model_review_summary || null,
      modelKeyFactors:     Array.isArray(raw.model_key_factors) ? raw.model_key_factors : [],
      modelCautionNotes:   Array.isArray(raw.model_caution_notes) ? raw.model_caution_notes : [],
    };
  }

  /**
   * Normalize a raw options candidate from the compact read model.
   *
   * Input fields (from OptionsOpportunityReadModel.candidates[*]):
   *   underlying | symbol, strategy_id | strategy_type | family,
   *   math.ev, math.pop, math.max_loss, math.net_credit | math.net_debit,
   *   dte, math.width, legs[], math.max_profit, math.ror, math.pop_source
   */
  function normalizeOptionsCandidate(raw) {
    var m = raw.math || {};
    var credit = m.net_credit != null ? Number(m.net_credit) : null;
    var debit  = m.net_debit  != null ? Number(m.net_debit)  : null;
    // Show credit for credit strategies, debit for debit strategies
    var premium = credit != null && credit > 0 ? credit : debit;
    var premiumLabel = credit != null && credit > 0 ? 'credit' : 'debit';
    return {
      symbol:       raw.underlying || raw.symbol || null,
      strategy:     raw.strategy_id || raw.strategy_type || raw.family_key || null,
      strategyId:   raw.strategy_id || null,
      family:       raw.family_key || null,
      ev:           m.ev != null ? Number(m.ev) : null,
      pop:          m.pop != null ? Number(m.pop) : null,
      popSource:    m.pop_source || null,
      maxLoss:      m.max_loss != null ? Number(m.max_loss) : null,
      maxProfit:    m.max_profit != null ? Number(m.max_profit) : null,
      credit:       credit,
      debit:        debit,
      premium:      premium,
      premiumLabel: premiumLabel,
      dte:          raw.dte != null ? raw.dte : null,
      width:        m.width != null ? Number(m.width) : null,
      ror:          m.ror != null ? Number(m.ror) : null,
      evPerDay:     m.ev_per_day != null ? Number(m.ev_per_day) : null,
      breakeven:    m.breakeven || [],
      legs:         Array.isArray(raw.legs) ? raw.legs : [],
      rank:         raw.rank || null,
      expiration:   raw.expiration || null,
      underlyingPrice: raw.underlying_price || null,
      candidateId:  raw.candidate_id || null,
      // Model analysis fields (populated after options model_analysis stage)
      modelRecommendation: raw.model_recommendation || null,
      modelConviction:     raw.model_conviction != null ? raw.model_conviction : null,
      modelScore:          raw.model_score != null ? raw.model_score : null,
      modelHeadline:       raw.model_headline || null,
      modelNarrative:      raw.model_narrative || null,
      modelCautionNotes:   Array.isArray(raw.model_caution_notes) ? raw.model_caution_notes : [],
      modelKeyFactors:     Array.isArray(raw.model_key_factors) ? raw.model_key_factors : [],
      modelDegraded:       !!raw.model_degraded,
      modelStructureAnalysis:      raw.model_structure_analysis || null,
      modelProbabilityAssessment:  raw.model_probability_assessment || null,
      modelGreeksAssessment:       raw.model_greeks_assessment || null,
      modelMarketAlignment:        raw.model_market_alignment || null,
      modelSuggestedAdjustment:    raw.model_suggested_adjustment || null,
      // Preserve raw for action handlers
      _raw: raw,
    };
  }

  /* =================================================================
   *  SECTION 1 -- Stock Opportunities
   *
   *  Uses the standard BenTradeStockTradeCardMapper.renderStockCard()
   *  pipeline so TMC stock cards are identical to every other stock
   *  dashboard in the app.  The TMC compact candidate is converted to
   *  the scanner-like shape that candidateToTradeShape() expects.
   * ================================================================= */

  /** Keep rendered rows for action handler lookups (same as other dashboards). */
  var _stockRenderedRows = [];
  var _stockExpandState  = {};

  function loadStockOpportunities() {
    var grid     = document.getElementById('tmcStockGrid');
    var countEl  = document.getElementById('tmcStockCount');
    var qualEl   = document.getElementById('tmcStockQuality');
    var freshEl  = document.getElementById('tmcStockFreshness');

    // Show loading state if grid has no rendered cards yet
    if (grid && !grid.querySelector('.trade-card')) {
      grid.innerHTML = '<div class="tmc-section-loading">Loading stock opportunities…</div>';
    }

    api.tmcGetLatestStock()
      .then(function (resp) {
        // Track run_id for freshness detection
        var newRunId = resp && resp.data ? resp.data.run_id : null;
        if (newRunId && newRunId !== _lastStockRunId) {
          console.log('[TMC] Stock data refreshed: run_id=' + newRunId +
            ' generated_at=' + (resp.data.generated_at || '?') +
            ' batch_status=' + (resp.data.batch_status || '?') +
            ' candidates=' + ((resp.data.candidates || []).length));
        }
        _lastStockRunId = newRunId;

        // Update batch status and freshness indicators
        var data = resp.data;
        _stockGeneratedAt = data ? data.generated_at : null;
        updateFreshness(freshEl, _stockGeneratedAt);

        var result = handleWorkflowResponse(resp, grid, countEl, qualEl, null, 'stock');
        if (!result) return;
        _cachedStockResp = resp;
        renderStockCandidates(grid, result.candidates, result.data);
        _removeRefreshingBadge('stock');
      })
      .catch(function (err) {
        console.error('[TMC] Failed to load stock opportunities:', err);
        updateFreshness(freshEl, null);
        showEmptyGrid(grid, countEl, 'Failed to load stock opportunities');
      });
  }

  /**
   * Convert a TMC compact stock candidate into the scanner-row shape
   * that BenTradeStockTradeCardMapper.candidateToTradeShape() expects.
   *
   * The standard pipeline reads: symbol, composite_score, price,
   * strategy_id, plus a flat metrics sub-object. We map from the
   * TMC compact fields.
   */
  function tmcStockToScannerShape(raw) {
    var tm = raw.top_metrics || {};
    return {
      symbol:          raw.symbol || '',
      composite_score: tm.composite_score != null ? tm.composite_score : (raw.setup_quality || null),
      price:           tm.price != null ? tm.price : null,
      rank:            raw.rank,
      trend_state:     tm.trend_state || null,
      thesis:          Array.isArray(raw.thesis_summary) ? raw.thesis_summary : [],
      confidence:      raw.confidence,
      metrics: {
        rsi:           tm.rsi != null ? tm.rsi : null,
        atr_pct:       tm.atr_pct != null ? tm.atr_pct : null,
        composite_score: tm.composite_score != null ? tm.composite_score : null,
        volume_ratio:  tm.volume_ratio != null ? tm.volume_ratio : null,
      },
      // Preserve raw for TMC-specific enrichment injection
      _tmc_raw: raw,
    };
  }

  function renderStockCandidates(grid, candidates, data) {
    if (!grid) return;
    var stockMapper = window.BenTradeStockTradeCardMapper;

    // If the standard mapper is not available, fall back to basic rendering
    if (!stockMapper || !stockMapper.renderStockCard) {
      grid.innerHTML = '';
      _stockRenderedRows = [];
      candidates.forEach(function (raw) {
        grid.appendChild(buildStockCardFallback(normalizeStockCandidate(raw), data));
      });
      return;
    }

    _stockRenderedRows = candidates.slice();
    var html = '';
    candidates.forEach(function (raw, idx) {
      var strategyId = raw.scanner_key || raw.setup_type || 'stock_opportunity';
      var scannerShape = tmcStockToScannerShape(raw);

      try {
        var cardHtml = stockMapper.renderStockCard(scannerShape, idx, strategyId, _stockExpandState);

        // Build TMC enrichment (split into collapsible body + always-visible warnings)
        var enrichment = buildTmcEnrichmentHtml(raw);

        // 1. Inject body content INSIDE the <details> collapsible (before </details>)
        if (enrichment.body) {
          cardHtml = cardHtml.replace(
            '</details>',
            enrichment.body + '</details>'
          );
        }

        // 2. Remove the "Run Model Analysis" button row and model output div from TMC cards
        cardHtml = cardHtml.replace(/<div class="run-row">.*?<\/div>/s, '');
        cardHtml = cardHtml.replace(/<div class="trade-model-output"[^>]*>.*?<\/div>/s, '');

        // 3. Inject warnings (caution, model-not-available) before the action buttons (always visible)
        if (enrichment.warnings) {
          cardHtml = cardHtml.replace(
            '<div class="trade-actions">',
            enrichment.warnings + '<div class="trade-actions">'
          );
        }

        html += cardHtml;
      } catch (cardErr) {
        console.warn('[TMC] Stock card render error for candidate ' + idx, cardErr);
        html += '<div class="trade-card" style="margin-bottom:12px;padding:10px;border:1px solid rgba(255,120,100,0.3);border-radius:10px;background:rgba(8,18,26,0.9);color:rgba(255,180,160,0.8);font-size:12px;">\u26A0 Render error for ' + esc((raw && raw.symbol) || '#' + idx) + '</div>';
      }
    });

    grid.innerHTML = html;

    // ── Wire delegated action handlers (remove old listener to prevent stacking) ──
    if (_stockGridClickHandler) {
      grid.removeEventListener('click', _stockGridClickHandler);
    }
    _stockGridClickHandler = function (e) {
      var btn = e.target.closest('[data-action]');
      if (!btn) return;
      var action   = btn.dataset.action;
      var tradeKey = btn.dataset.tradeKey || '';
      var symbol   = btn.dataset.symbol || '';
      var row      = _findStockRowByTradeKey(tradeKey);
      var scannerRow = row ? tmcStockToScannerShape(row) : null;
      var strategyId = row ? (row.scanner_key || row.setup_type || 'stock_opportunity') : '';

      if (action === 'model-analysis' && row) {
        // TMC uses dedicated final-decision prompt, NOT the per-strategy one
        runTmcFinalDecision(btn, tradeKey, row, strategyId);
      } else if (action === 'execute' && scannerRow) {
        stockMapper.executeStockTrade(btn, tradeKey, scannerRow, strategyId);
      } else if (action === 'reject' && tradeKey) {
        var cardEl = btn.closest('.trade-card');
        if (cardEl) {
          cardEl.style.opacity = '0.35';
          cardEl.style.pointerEvents = 'none';
        }
      } else if (action === 'data-workbench' && scannerRow) {
        stockMapper.openDataWorkbenchForStock(scannerRow, strategyId);
      } else if (action === 'stock-analysis') {
        stockMapper.openStockAnalysis(symbol || (row && row.symbol));
      } else if (action === 'workbench' && scannerRow) {
        stockMapper.openDataWorkbenchForStock(scannerRow, strategyId);
      }
    };
    grid.addEventListener('click', _stockGridClickHandler);

    // Wire expand state persistence
    grid.querySelectorAll('details.trade-card-collapse').forEach(function (details) {
      details.addEventListener('toggle', function () {
        var tk = details.dataset.tradeKey || '';
        if (tk) _stockExpandState[tk] = details.open;
      });
    });

    // Hydrate cached model analysis results
    if (window.BenTradeModelAnalysisStore && window.BenTradeModelAnalysisStore.hydrateContainer) {
      window.BenTradeModelAnalysisStore.hydrateContainer(grid);
    }
  }

  /**
   * Format a value that is ALREADY a 0-100 percentage.
   * Unlike fmtPct() which expects decimals, this just appends '%'.
   */
  function fmtPctDirect(v) {
    if (v == null) return '--';
    return Number(v).toFixed(1) + '%';
  }

  /** Assessment/impact color map for factor rendering. */
  var _assessColors = {
    favorable: '#00dc78', positive: '#00dc78',
    unfavorable: '#ff5a5a', negative: '#ff5a5a',
    concerning: '#ffc83c',
    neutral: '#8899aa',
  };

  /**
   * Build TMC-specific enrichment HTML to inject into the standard card.
   * Returns { body, warnings } where:
   *   - body: goes INSIDE the <details> collapsible (hidden when collapsed)
   *   - warnings: stays OUTSIDE the collapsible (visible when collapsed)
   *
   * Rendering rules:
   *   - If model analysis ran successfully → body gets MODEL REVIEW + tech analysis + factors + engine summary.
   *   - If model analysis is absent → warnings gets "MODEL ANALYSIS NOT AVAILABLE" banner.
   *   - CAUTION notes always go to warnings (visible when collapsed).
   *   - Key factors render as structured cards (factor + impact + evidence).
   *   - Confidence is displayed directly as 0-100% (not re-multiplied).
   */
  function buildTmcEnrichmentHtml(raw) {
    var bodyParts = [];    // inside collapsible
    var warningParts = []; // always visible (between header and buttons)
    var hasModelReview = !!(raw.model_review_summary || raw.model_recommendation);

    // ── Model review section (collapsible body) ──
    if (hasModelReview) {
      var recText = raw.model_recommendation
        ? esc(String(raw.model_recommendation).toUpperCase())
        : '';
      // model_confidence is already 0-100 from backend — do NOT multiply by 100
      var confText = raw.model_confidence != null
        ? 'Conf: ' + fmtPctDirect(raw.model_confidence)
        : '';
      var scoreText = raw.model_score != null
        ? 'Score: ' + Math.round(raw.model_score)
        : '';
      var headerBadges = [recText, confText, scoreText].filter(Boolean).join(' \u00B7 ');

      // Determine recommendation color
      var recColor = '#b4b4c8';
      if (recText === 'BUY' || recText === 'EXECUTE') recColor = '#00dc78';
      else if (recText === 'PASS' || recText === 'REJECT') recColor = '#ff5a5a';

      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid ' + recColor + '33;background:' + recColor + '08;">' +
          '<div class="section-title" style="margin-bottom:6px;">MODEL REVIEW' +
            (headerBadges ? ' \u2014 <span style="color:' + recColor + ';">' + headerBadges + '</span>' : '') +
          '</div>' +
          (raw.model_review_summary
            ? '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(raw.model_review_summary) + '</div>'
            : '') +
        '</div>'
      );
    }

    // ── Technical Analysis (collapsible body) ──
    var ta = raw.model_technical_analysis;
    if (ta && typeof ta === 'object') {
      var taHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(0,220,255,0.03);border-radius:6px;border:1px solid rgba(0,220,255,0.12);">';
      taHtml += '<div class="section-title" style="color:var(--accent-cyan,#00dcff);">TECHNICAL ANALYSIS</div>';
      if (ta.setup_quality_assessment) {
        taHtml += '<div style="font-size:11px;color:var(--text,#d7fbff);line-height:1.5;margin-bottom:6px;">' + esc(ta.setup_quality_assessment) + '</div>';
      }
      if (ta.key_metrics_cited && typeof ta.key_metrics_cited === 'object') {
        var mKeys = Object.keys(ta.key_metrics_cited);
        if (mKeys.length > 0) {
          taHtml += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px;">';
          mKeys.forEach(function (mk) {
            var mv = ta.key_metrics_cited[mk];
            taHtml += '<span style="font-size:10px;padding:2px 6px;background:rgba(255,255,255,0.04);border-radius:3px;border:1px solid rgba(255,255,255,0.08);"><span style="color:var(--muted);">' + esc(mk.replace(/_/g, ' ')) + ':</span> <b style="color:var(--text,#d7fbff);">' + esc(String(mv != null ? mv : '\u2014')) + '</b></span>';
          });
          taHtml += '</div>';
        }
      }
      var rows = [
        { label: 'Trend', val: ta.trend_context, icon: '\u2197' },
        { label: 'Momentum', val: ta.momentum_read, icon: '\u26A1' },
        { label: 'Volatility', val: ta.volatility_read, icon: '\u223C' },
        { label: 'Volume', val: ta.volume_read, icon: '\u25A3' },
      ].filter(function (r) { return !!r.val; });
      rows.forEach(function (r) {
        taHtml += '<div style="font-size:10px;line-height:1.4;padding:2px 0 2px 8px;border-left:2px solid rgba(0,220,255,0.25);margin-bottom:2px;"><span style="color:var(--accent-cyan,#00dcff);font-weight:600;">' + r.icon + ' ' + esc(r.label) + ':</span> <span style="color:var(--text-secondary,#bbb);">' + esc(r.val) + '</span></div>';
      });
      taHtml += '</div>';
      bodyParts.push(taHtml);
    }

    // ── Caution notes (collapsible body) ──
    var cautions = Array.isArray(raw.model_caution_notes) ? raw.model_caution_notes : [];
    if (cautions.length > 0) {
      var cautionLis = cautions.map(function (c) { return '<li style="margin-bottom:2px;">' + esc(c) + '</li>'; }).join('');
      bodyParts.push(
        '<div class="section" style="margin-bottom:6px;padding:6px 10px;border-radius:6px;border:1px solid rgba(244,200,95,0.2);background:rgba(244,200,95,0.04);">' +
          '<div class="section-title" style="color:var(--warn,#f4c85f);">CAUTION</div>' +
          '<ul style="margin:0;padding-left:16px;font-size:11px;line-height:1.5;">' + cautionLis + '</ul>' +
        '</div>'
      );
    }

    // ── Key factors (collapsible body) ──
    var factors = Array.isArray(raw.model_key_factors) ? raw.model_key_factors : [];
    if (factors.length > 0) {
      var factorsHtml = '';
      factors.forEach(function (f) {
        if (typeof f === 'string') {
          factorsHtml += '<div style="font-size:11px;color:var(--text-secondary,#bbb);line-height:1.4;padding:3px 0 3px 8px;border-left:2px solid #8899aa;margin-bottom:3px;">' + esc(f) + '</div>';
        } else if (f && typeof f === 'object') {
          var factorName = f.factor || f.name || '';
          var impact = String(f.impact || f.assessment || 'neutral').toLowerCase();
          var evidence = f.evidence || f.detail || '';
          var impColor = _assessColors[impact] || '#8899aa';
          var impLabel = impact.charAt(0).toUpperCase() + impact.slice(1);

          factorsHtml += '<div style="font-size:11px;line-height:1.4;padding:4px 0 4px 8px;border-left:2px solid ' + impColor + ';margin-bottom:4px;">';
          factorsHtml += '<div style="display:flex;align-items:center;gap:6px;">';
          factorsHtml += '<span style="color:' + impColor + ';font-weight:600;">' + esc(factorName) + '</span>';
          factorsHtml += '<span style="font-size:9px;padding:1px 5px;border-radius:3px;border:1px solid ' + impColor + '44;color:' + impColor + ';text-transform:uppercase;letter-spacing:0.3px;">' + esc(impLabel) + '</span>';
          factorsHtml += '</div>';
          if (evidence) {
            factorsHtml += '<div style="font-size:10px;color:var(--muted,#6a8da8);margin-top:2px;">' + esc(evidence) + '</div>';
          }
          factorsHtml += '</div>';
        }
      });

      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;">' +
          '<div class="section-title">KEY FACTORS</div>' +
          factorsHtml +
        '</div>'
      );
    }

    // ── Engine summary (collapsible body) ──
    if (raw.review_summary) {
      if (!hasModelReview) {
        // Model analysis absent — warning banner (always visible)
        warningParts.unshift(
          '<div style="margin-bottom:6px;padding:5px 10px;font-size:11px;font-weight:600;color:#ff8a5a;background:rgba(255,138,90,0.08);border:1px solid rgba(255,138,90,0.2);border-radius:5px;text-align:center;">' +
            '\u26A0 MODEL ANALYSIS NOT AVAILABLE \u2014 expand for engine output' +
          '</div>'
        );
      }
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:6px 10px;border-radius:6px;border:1px solid rgba(100,149,237,0.12);background:rgba(100,149,237,0.04);">' +
          '<div class="section-title">ENGINE SUMMARY</div>' +
          '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(raw.review_summary) + '</div>' +
        '</div>'
      );
    }

    return { body: bodyParts.join(''), warnings: warningParts.join('') };
  }

  /* ── TMC Final Trade Decision ──────────────────────────────────────
   *
   *  Calls the dedicated TMC final-decision endpoint which gives the
   *  model full trade setup + fresh market picture context and asks
   *  for a portfolio-manager-level decision.
   *
   *  This replaces the per-strategy runModelAnalysisForStock() used
   *  on the other stock dashboards.
   * ────────────────────────────────────────────────────────────────── */

  function runTmcFinalDecision(btn, tradeKey, rawCandidate, strategyId) {
    var modelStore = window.BenTradeModelAnalysisStore;

    if (!api || !api.tmcFinalDecision) {
      console.error('[TMC] BenTradeApi.tmcFinalDecision not available');
      return;
    }

    // Dedupe guard
    if (tradeKey && modelStore) {
      var existing = modelStore.get(tradeKey);
      if (existing && existing.status === 'running') return;
      modelStore.setRunning(tradeKey);
    }

    // Loading state
    var cardEl = btn ? btn.closest('.trade-card') : null;
    var outputEl = cardEl ? cardEl.querySelector('[data-model-output]') : null;

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="home-scan-spinner" aria-hidden="true" style="margin-right:4px;"></span>Analyzing\u2026';
    }
    if (outputEl) {
      outputEl.style.display = 'block';
      outputEl.innerHTML = '<div style="padding:8px;font-size:11px;color:var(--muted);">Running TMC final decision analysis\u2026</div>';
    }

    api.tmcFinalDecision(rawCandidate, strategyId)
      .then(function (result) {
        var analysis = (result && result.analysis) || {};

        // Store for hydration
        if (tradeKey && modelStore) {
          var bridged = {
            status: 'success',
            model_evaluation: {
              model_recommendation: analysis.decision === 'EXECUTE' ? 'BUY' : 'PASS',
              recommendation: analysis.decision || 'PASS',
              score_0_100: analysis.engine_comparison ? analysis.engine_comparison.model_score : null,
              confidence_0_1: analysis.conviction != null ? analysis.conviction / 100 : null,
              thesis: analysis.decision_summary || '',
              key_drivers: (analysis.factors_considered || []).map(function (f) {
                return { factor: f.factor || '', impact: f.assessment || 'neutral', evidence: f.detail || '' };
              }),
              risk_review: {
                primary_risks: analysis.risk_assessment ? (analysis.risk_assessment.primary_risks || []) : [],
                volatility_risk: null,
                timing_risk: null,
                data_quality_flag: null,
              },
            },
          };
          var modelUI = window.BenTradeModelAnalysis;
          var parsed = modelUI ? modelUI.parse(bridged) : bridged;
           // Attach full TMC analysis for rich rendering
          parsed._tmc_analysis = analysis;
          modelStore.setSuccess(tradeKey, parsed);
        }

        // Render
        if (outputEl) {
          outputEl.style.display = 'block';
          outputEl.innerHTML = renderTmcFinalDecisionResult(analysis);
        }

        // Reset button
        if (btn) {
          btn.disabled = false;
          var ts = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          btn.innerHTML = '\u21BB Re-run Analysis <span style="font-size:9px;color:var(--muted);margin-left:4px;">' + ts + '</span>';
        }
      })
      .catch(function (err) {
        var errMsg = (err && err.message) || 'TMC final decision analysis failed';
        console.error('[TMC] final decision error:', err);

        if (tradeKey && modelStore) {
          modelStore.setError(tradeKey, errMsg);
        }
        if (outputEl) {
          outputEl.style.display = 'block';
          outputEl.innerHTML = '<div style="padding:8px;font-size:11px;color:#ff5a5a;">\u26A0 ' + esc(errMsg) + '</div>';
        }
        if (btn) {
          btn.disabled = false;
          btn.textContent = 'Run Model Analysis';
        }
      });
  }

  /**
   * Render TMC final decision analysis into rich HTML.
   *
   * Output contract fields:
   *   decision, conviction, decision_summary, factors_considered,
   *   technical_analysis { setup_quality_assessment, key_metrics_cited,
   *     trend_context, momentum_read, volatility_read, volume_read },
   *   market_alignment, risk_assessment, what_would_change_my_mind,
   *   engine_comparison
   */
  function renderTmcFinalDecisionResult(analysis) {
    if (!analysis) return '';

    // ── Detect fallback / parse failure ──
    if (analysis._fallback) {
      return '<div style="padding:10px 0;">'
        + '<div style="padding:8px 10px;font-size:12px;color:#ff8a5a;background:rgba(255,138,90,0.08);border:1px solid rgba(255,138,90,0.2);border-radius:6px;margin-bottom:8px;">'
        + '\u26A0 <strong>MODEL ANALYSIS FAILED</strong> \u2014 ' + esc(analysis.decision_summary || 'Parse failure')
        + '</div>'
        + (analysis._raw_text_preview
          ? '<details style="margin-bottom:8px;"><summary style="font-size:10px;color:var(--muted);cursor:pointer;">Raw model output (debug)</summary>'
            + '<pre style="font-size:9px;color:var(--muted);white-space:pre-wrap;max-height:150px;overflow:auto;padding:6px;background:rgba(0,0,0,0.3);border-radius:4px;margin-top:4px;">' + esc(analysis._raw_text_preview) + '</pre></details>'
          : '')
        + '</div>';
    }

    var decision = analysis.decision || 'PASS';
    var conviction = analysis.conviction != null ? analysis.conviction : 0;
    var decColor = decision === 'EXECUTE' ? '#00dc78' : '#ff5a5a';
    var convColor = conviction >= 70 ? '#00dc78' : conviction >= 40 ? '#ffc83c' : '#ff5a5a';

    var html = '<div style="padding:10px 0;">';

    // ── Decision Header ──
    html += '<div style="display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:10px;padding:8px 10px;border-radius:6px;border:1px solid ' + decColor + '33;background:' + decColor + '08;">';
    html += '<span style="font-size:14px;font-weight:800;padding:3px 12px;border-radius:4px;border:1px solid ' + decColor + '55;color:' + decColor + ';letter-spacing:1px;text-shadow:0 0 8px ' + decColor + '44;">' + esc(decision) + '</span>';
    html += '<span style="font-size:12px;color:' + convColor + ';font-weight:700;">Conviction: ' + conviction + '%</span>';
    if (analysis.engine_comparison && analysis.engine_comparison.model_score != null) {
      var msColor = analysis.engine_comparison.model_score >= 60 ? '#00dc78' : analysis.engine_comparison.model_score >= 40 ? '#ffc83c' : '#ff5a5a';
      html += '<span style="font-size:12px;color:' + msColor + ';font-weight:700;">Score: ' + Math.round(analysis.engine_comparison.model_score) + '<span style="font-size:10px;color:var(--muted);font-weight:400;">/100</span></span>';
    }
    html += '</div>';

    // ── Decision Summary (structured) ──
    if (analysis.decision_summary) {
      html += '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;margin-bottom:10px;padding:6px 10px;border-radius:5px;border-left:3px solid ' + decColor + ';">' + esc(analysis.decision_summary) + '</div>';
    }

    // ── Technical Analysis (new detailed metrics section) ──
    var ta = analysis.technical_analysis;
    if (ta && typeof ta === 'object') {
      html += '<div style="margin-bottom:10px;padding:8px 10px;background:rgba(0,220,255,0.03);border-radius:6px;border:1px solid rgba(0,220,255,0.12);">';
      html += '<div style="font-size:10px;font-weight:700;color:var(--accent-cyan,#00dcff);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px;">Technical Analysis</div>';

      // Setup Quality Assessment
      if (ta.setup_quality_assessment) {
        html += '<div style="font-size:11px;color:var(--text,#d7fbff);line-height:1.5;margin-bottom:6px;">' + esc(ta.setup_quality_assessment) + '</div>';
      }

      // Key Metrics Cited grid
      var metricsCited = ta.key_metrics_cited;
      if (metricsCited && typeof metricsCited === 'object') {
        var mKeys = Object.keys(metricsCited);
        if (mKeys.length > 0) {
          html += '<div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(130px, 1fr));gap:4px 10px;margin-bottom:6px;">';
          mKeys.forEach(function (mk) {
            var mv = metricsCited[mk];
            var mStr = mv != null ? String(mv) : '\u2014';
            html += '<div style="font-size:10px;padding:3px 6px;background:rgba(255,255,255,0.04);border-radius:3px;border:1px solid rgba(255,255,255,0.06);">';
            html += '<span style="color:var(--muted);text-transform:uppercase;font-size:9px;">' + esc(mk.replace(/_/g, ' ')) + '</span><br>';
            html += '<span style="color:var(--text,#d7fbff);font-weight:600;">' + esc(mStr) + '</span>';
            html += '</div>';
          });
          html += '</div>';
        }
      }

      // Technical context rows (trend, momentum, volatility, volume)
      var techRows = [
        { label: 'Trend', value: ta.trend_context, icon: '\u2197' },
        { label: 'Momentum', value: ta.momentum_read, icon: '\u26A1' },
        { label: 'Volatility', value: ta.volatility_read, icon: '\u223C' },
        { label: 'Volume', value: ta.volume_read, icon: '\u25A3' },
      ].filter(function (r) { return !!r.value; });

      if (techRows.length > 0) {
        techRows.forEach(function (r) {
          html += '<div style="font-size:11px;line-height:1.4;padding:2px 0 2px 8px;border-left:2px solid rgba(0,220,255,0.25);margin-bottom:3px;">';
          html += '<span style="color:var(--accent-cyan,#00dcff);font-weight:600;">' + r.icon + ' ' + esc(r.label) + ':</span> ';
          html += '<span style="color:var(--text-secondary,#bbb);">' + esc(r.value) + '</span>';
          html += '</div>';
        });
      }

      html += '</div>';
    }

    // ── Factors Considered ──
    var factors = analysis.factors_considered || [];
    if (factors.length > 0) {
      html += '<div style="margin-bottom:10px;">';
      html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px;">Factors Considered</div>';

      // Group by category
      var groups = {};
      factors.forEach(function (f) {
        var cat = f.category || 'trade_setup';
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(f);
      });

      var catLabels = {
        trade_setup: 'Trade Setup',
        market_environment: 'Market Environment',
        risk_reward: 'Risk / Reward',
        timing: 'Timing',
        data_quality: 'Data Quality',
      };
      var assessColors = {
        favorable: '#00dc78',
        unfavorable: '#ff5a5a',
        concerning: '#ffc83c',
        neutral: '#8899aa',
      };

      Object.keys(groups).forEach(function (cat) {
        html += '<div style="margin-bottom:8px;">';
        html += '<div style="font-size:9px;font-weight:700;color:#6a8da8;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px;padding-bottom:2px;border-bottom:1px solid rgba(106,141,168,0.15);">' + esc(catLabels[cat] || cat) + '</div>';
        groups[cat].forEach(function (f) {
          var aColor = assessColors[f.assessment] || '#8899aa';
          var aLabel = (f.assessment || 'neutral').charAt(0).toUpperCase() + (f.assessment || 'neutral').slice(1);
          var wBadge = f.weight === 'high' ? '\u25CF' : f.weight === 'low' ? '\u25CB' : '\u25D0';
          html += '<div style="padding:3px 0 3px 8px;border-left:2px solid ' + aColor + ';margin-bottom:3px;">';
          html += '<div style="display:flex;gap:6px;align-items:center;font-size:11px;line-height:1.4;">';
          html += '<span style="color:' + aColor + ';font-size:8px;" title="Weight: ' + esc(f.weight || 'medium') + '">' + wBadge + '</span>';
          html += '<span style="color:var(--text,#d7fbff);font-weight:600;">' + esc(f.factor || '') + '</span>';
          html += '<span style="font-size:9px;padding:1px 4px;border-radius:2px;border:1px solid ' + aColor + '33;color:' + aColor + ';">' + esc(aLabel) + '</span>';
          html += '</div>';
          if (f.detail) {
            html += '<div style="font-size:10px;color:var(--muted);margin-top:1px;padding-left:14px;">' + esc(f.detail) + '</div>';
          }
          html += '</div>';
        });
        html += '</div>';
      });

      html += '</div>';
    }

    // ── Market Alignment ──
    if (analysis.market_alignment) {
      var ma = analysis.market_alignment;
      var maColor = ma.overall === 'aligned' ? '#00dc78' : ma.overall === 'conflicting' ? '#ff5a5a' : '#ffc83c';
      html += '<div style="margin-bottom:10px;padding:8px 10px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.12);">';
      html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px;">Market Alignment</div>';
      html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">';
      html += '<span style="font-size:10px;padding:2px 8px;border-radius:3px;border:1px solid ' + maColor + '44;color:' + maColor + ';font-weight:700;letter-spacing:0.3px;">' + esc(String(ma.overall || 'neutral').toUpperCase()) + '</span>';
      html += '</div>';
      if (ma.detail) {
        html += '<div style="font-size:11px;color:var(--text-secondary,#bbb);line-height:1.5;">' + esc(ma.detail) + '</div>';
      }
      html += '</div>';
    }

    // ── Risk Assessment ──
    if (analysis.risk_assessment) {
      var ra = analysis.risk_assessment;
      var rvColor = ra.risk_reward_verdict === 'favorable' ? '#00dc78' : ra.risk_reward_verdict === 'unfavorable' ? '#ff5a5a' : '#ffc83c';
      html += '<div style="margin-bottom:10px;padding:8px 10px;background:rgba(255,90,90,0.03);border-radius:6px;border:1px solid rgba(255,90,90,0.1);">';
      html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px;">Risk Assessment';
      html += ' <span style="font-size:9px;padding:1px 6px;border-radius:3px;border:1px solid ' + rvColor + '44;color:' + rvColor + ';margin-left:6px;font-weight:700;">' + esc(String(ra.risk_reward_verdict || 'marginal').toUpperCase()) + '</span>';
      html += '</div>';

      if (ra.biggest_concern) {
        html += '<div style="font-size:11px;color:#ffc83c;line-height:1.5;margin-bottom:5px;padding:4px 8px;background:rgba(255,200,60,0.06);border-radius:4px;border-left:3px solid #ffc83c;">\u26A0 <strong>Key concern:</strong> ' + esc(ra.biggest_concern) + '</div>';
      }

      var risks = ra.primary_risks || [];
      if (risks.length > 0) {
        html += '<ul style="margin:0;padding-left:18px;font-size:11px;color:var(--text-secondary,#bbb);line-height:1.5;">';
        risks.forEach(function (r) { html += '<li style="margin-bottom:2px;">' + esc(r) + '</li>'; });
        html += '</ul>';
      }
      html += '</div>';
    }

    // ── Engine Comparison ──
    if (analysis.engine_comparison) {
      var ec = analysis.engine_comparison;
      var agreeColor = ec.agreement === 'agree' ? '#00dc78' : ec.agreement === 'disagree' ? '#ff5a5a' : '#ffc83c';
      html += '<div style="margin-bottom:10px;padding:8px 10px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.12);">';
      html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px;">Engine vs Model</div>';
      html += '<div style="display:flex;gap:16px;align-items:center;font-size:11px;margin-bottom:4px;">';
      if (ec.engine_score != null) {
        var esColor = ec.engine_score >= 60 ? '#00dc78' : ec.engine_score >= 40 ? '#ffc83c' : '#ff5a5a';
        html += '<span style="color:var(--text-secondary,#bbb);">Engine: <b style="color:' + esColor + ';">' + Math.round(ec.engine_score) + '</b></span>';
      }
      if (ec.model_score != null) {
        var ms2Color = ec.model_score >= 60 ? '#00dc78' : ec.model_score >= 40 ? '#ffc83c' : '#ff5a5a';
        html += '<span style="color:var(--text-secondary,#bbb);">Model: <b style="color:' + ms2Color + ';">' + Math.round(ec.model_score) + '</b></span>';
      }
      html += '<span style="font-size:10px;padding:2px 8px;border-radius:3px;border:1px solid ' + agreeColor + '44;color:' + agreeColor + ';font-weight:700;">' + esc(String(ec.agreement || 'partial').toUpperCase()) + '</span>';
      html += '</div>';
      if (ec.reasoning) {
        html += '<div style="font-size:10px;color:var(--text-secondary,#bbb);line-height:1.5;padding-left:8px;border-left:2px solid ' + agreeColor + ';">' + esc(ec.reasoning) + '</div>';
      }
      html += '</div>';
    }

    // ── What Would Change My Mind ──
    if (analysis.what_would_change_my_mind) {
      html += '<div style="margin-bottom:8px;padding:6px 10px;border-radius:6px;border:1px solid rgba(255,255,255,0.06);background:rgba(255,255,255,0.02);">';
      html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:3px;">\u21BB What Would Change My Mind</div>';
      html += '<div style="font-size:11px;color:var(--text-secondary,#bbb);line-height:1.5;font-style:italic;">' + esc(analysis.what_would_change_my_mind) + '</div>';
      html += '</div>';
    }

    // ── Fallback/Parse info (debug) ──
    if (analysis._parse_method && analysis._parse_method !== 'direct') {
      html += '<div style="font-size:9px;color:var(--muted);opacity:0.6;padding-top:4px;border-top:1px solid rgba(255,255,255,0.06);">Parse method: ' + esc(analysis._parse_method) + '</div>';
    }

    html += '</div>';
    return html;
  }

  /* ================================================================
   *  _openDataWorkbenchInline — open inline Data Workbench modal
   *  for options or active trade candidates.
   * ================================================================ */
  function _openDataWorkbenchInline(row, context) {
    var sym = String(row.symbol || row.underlying || row.ticker || '?').toUpperCase();
    var modal = window.BenTradeDataWorkbenchModal;
    if (modal && typeof modal.open === 'function') {
      modal.open({
        symbol:     sym,
        normalized: row,
        rawSource:  row,
        derived:    { source: 'tmc_' + (context || 'unknown'), trade_key: row.trade_key || '' },
      });
      return;
    }
    // Fallback: inline JSON viewer
    console.log('[TMC] Data Workbench - Raw candidate:', JSON.stringify(row, null, 2));
    var overlay = document.createElement('div');
    overlay.className = 'dwb-tmc-fallback-overlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.8);z-index:5000;overflow:auto;padding:2rem;';
    overlay.innerHTML =
      '<div style="max-width:800px;margin:0 auto;background:#161b22;border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:1.5rem;">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">' +
          '<h6 style="color:#00e0c3;margin:0;">DATA WORKBENCH — ' + sym + '</h6>' +
          '<button class="dwb-tmc-fallback-close" style="background:none;border:none;color:#e0e0e0;font-size:1.5rem;cursor:pointer;">&times;</button>' +
        '</div>' +
        '<pre style="color:#e0e0e0;font-size:0.75rem;max-height:70vh;overflow:auto;white-space:pre-wrap;word-break:break-all;">' +
          _escapeHtml(JSON.stringify(row, null, 2)) +
        '</pre>' +
      '</div>';
    overlay.querySelector('.dwb-tmc-fallback-close').addEventListener('click', function () { overlay.remove(); });
    overlay.addEventListener('click', function (ev) { if (ev.target === overlay) overlay.remove(); });
    var root = (window.BenTradeOverlayRoot && window.BenTradeOverlayRoot.get)
      ? window.BenTradeOverlayRoot.get()
      : document.body;
    root.appendChild(overlay);
  }

  function _escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /** Find a raw TMC candidate by trade key for action handlers. */
  function _findStockRowByTradeKey(tradeKey) {
    if (!tradeKey) return null;
    var stockMapper = window.BenTradeStockTradeCardMapper;
    for (var i = 0; i < _stockRenderedRows.length; i++) {
      var row = _stockRenderedRows[i];
      var strategyId = row.scanner_key || row.setup_type || 'stock_opportunity';
      var rk = stockMapper
        ? stockMapper.buildStockTradeKey(row.symbol, strategyId)
        : '';
      if (rk === tradeKey) return row;
    }
    return null;
  }

  /**
   * Fallback card builder when BenTradeStockTradeCardMapper is unavailable.
   * Produces a minimal readable card — should never appear in practice.
   */
  function buildStockCardFallback(c, data) {
    var card = document.createElement('div');
    card.className = 'tmc-card tmc-stock-card';
    var symbol = c.symbol || '???';
    var action = c.action || '--';
    card.innerHTML =
      '<div class="tmc-card-header">' +
        '<div class="tmc-card-symbol">' + esc(symbol) + '</div>' +
        '<div class="tmc-card-action ' + actionClass(action) + '">' + esc(String(action).toUpperCase()) + '</div>' +
      '</div>' +
      (c.rationale ? '<div class="tmc-rationale"><div class="tmc-rationale-text">' + esc(c.rationale) + '</div></div>' : '') +
      '<div class="tmc-card-footer"><span class="tmc-scanner-badge">' + esc(c.scannerName || '--') + '</span></div>';
    return card;
  }

  /**
   * Start a completion-poll that checks /stock/latest every interval
   * until the run_id changes from the baseline, or maxAttempts is reached.
   *
   * @param {string|null} baselineRunId - run_id before the trigger
   * @param {number} intervalMs - poll interval (default 15000)
   * @param {number} maxAttempts - max polls (default 20 = ~5 min)
   */
  function _startStockCompletionPoll(baselineRunId, intervalMs, maxAttempts) {
    _stopStockCompletionPoll();
    var attempts = 0;
    intervalMs = intervalMs || 15000;
    maxAttempts = maxAttempts || 20;

    console.log('[TMC] Starting stock completion poll (baseline run_id=' +
      (baselineRunId || 'none') + ', interval=' + intervalMs + 'ms, max=' + maxAttempts + ')');

    _stockPollTimer = setInterval(function () {
      attempts++;
      if (attempts > maxAttempts) {
        console.log('[TMC] Stock completion poll exhausted (' + maxAttempts + ' attempts)');
        _stopStockCompletionPoll();
        return;
      }

      api.tmcGetLatestStock()
        .then(function (resp) {
          var newRunId = resp && resp.data ? resp.data.run_id : null;
          if (newRunId && newRunId !== baselineRunId) {
            console.log('[TMC] Stock completion poll detected new run: ' + newRunId);
            _stopStockCompletionPoll();
            // Full reload with rendering
            loadStockOpportunities();
          }
        })
        .catch(function () {
          // Ignore poll errors — will retry on next interval
        });
    }, intervalMs);
  }

  function _stopStockCompletionPoll() {
    if (_stockPollTimer) {
      clearInterval(_stockPollTimer);
      _stockPollTimer = null;
    }
  }

  function triggerStockRun() {
    var baselineRunId = _lastStockRunId;
    console.log('[TMC] Triggering stock workflow (baseline run_id=' + (baselineRunId || 'none') + ')');

    api.tmcRunStock()
      .then(function (result) {
        console.log('[TMC] Stock workflow trigger returned: status=' + result.status +
          ' run_id=' + (result.run_id || '?') + ' candidates=' + (result.candidate_count || 0));
        _stopStockCompletionPoll();
        loadStockOpportunities();
      })
      .catch(function (err) {
        console.error('[TMC] Stock workflow trigger failed:', err);
        // The workflow may still be running in the background (shielded
        // from HTTP disconnect on the backend).  Start polling to detect
        // when it completes and refresh automatically.
        _startStockCompletionPoll(baselineRunId);
        // Also try an immediate load — the trigger may have failed after
        // the workflow already finished and wrote output.json.
        loadStockOpportunities();
      });
  }

  /* =================================================================
   *  SECTION 2 -- Options Opportunities
   * ================================================================= */

  function loadOptionsOpportunities() {
    var grid     = document.getElementById('tmcOptionsGrid');
    var countEl  = document.getElementById('tmcOptionsCount');
    var qualEl   = document.getElementById('tmcOptionsQuality');
    var statusEl = document.getElementById('tmcOptionsStatus');
    var batchEl  = document.getElementById('tmcOptionsBatchStatus');
    var freshEl  = document.getElementById('tmcOptionsFreshness');

    updateStatusBadge(statusEl, null);
    if (statusEl) statusEl.textContent = 'Loading...';

    api.tmcGetLatestOptions()
      .then(function (resp) {
        var newRunId = resp && resp.data ? resp.data.run_id : null;
        if (newRunId && newRunId !== _lastOptionsRunId) {
          console.log('[TMC] Options data refreshed: run_id=' + newRunId +
            ' batch_status=' + (resp.data.batch_status || '?') +
            ' candidates=' + ((resp.data.candidates || []).length));
        }
        _lastOptionsRunId = newRunId;

        // Update batch status and freshness indicators
        var data = resp.data;
        _optionsGeneratedAt = data ? data.generated_at : null;
        updateBatchStatusBadge(batchEl, data ? data.batch_status : null);
        updateFreshness(freshEl, _optionsGeneratedAt);

        var result = handleWorkflowResponse(resp, grid, countEl, qualEl, statusEl, 'options');
        if (!result) return;
        _cachedOptionsResp = resp;
        renderOptionsCandidates(grid, result.candidates, result.data);
        _removeRefreshingBadge('options');
      })
      .catch(function (err) {
        console.error('[TMC] Failed to load options opportunities:', err);
        updateStatusBadge(statusEl, 'failed');
        updateBatchStatusBadge(batchEl, null);
        updateFreshness(freshEl, null);
        showEmptyGrid(grid, countEl, 'Failed to load options opportunities');
      });
  }

  /** Keep rendered options rows for action handler lookups. */
  var _optionsRenderedRows = [];
  var _optionsExpandState  = {};

  function renderOptionsCandidates(grid, candidates, data) {
    if (!grid) return;
    var tc = window.BenTradeTradeCard;

    _optionsRenderedRows = candidates.slice();
    var html = '';
    candidates.forEach(function (raw, idx) {
      var c = normalizeOptionsCandidate(raw);
      try {
        html += buildOptionsTradeCard(c, idx, data);
      } catch (cardErr) {
        console.warn('[TMC] Options card render error for candidate ' + idx, cardErr);
        html += '<div class="trade-card" style="margin-bottom:12px;padding:10px;border:1px solid rgba(255,120,100,0.3);border-radius:10px;background:rgba(8,18,26,0.9);color:rgba(255,180,160,0.8);font-size:12px;">\u26A0 Render error for ' + esc((raw && raw.symbol) || '#' + idx) + '</div>';
      }
    });

    grid.innerHTML = html;

    // ── Wire delegated action handlers (remove old listener to prevent stacking) ──
    if (_optionsGridClickHandler) {
      grid.removeEventListener('click', _optionsGridClickHandler);
    }
    _optionsGridClickHandler = function (e) {
      var btn = e.target.closest('[data-action]');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      var action   = btn.dataset.action;
      var tradeKey = btn.dataset.tradeKey || '';
      var row      = _findOptionsRowByTradeKey(tradeKey);

      if (action === 'execute' && row) {
        _executeOptionsTrade(btn, tradeKey, row);
      } else if (action === 'reject' && tradeKey) {
        var cardEl = btn.closest('.trade-card');
        if (cardEl) {
          cardEl.style.opacity = '0.35';
          cardEl.style.pointerEvents = 'none';
        }
      } else if (action === 'data-workbench' && row) {
        _openDataWorkbenchInline(row, 'options');
      }
    };
    grid.addEventListener('click', _optionsGridClickHandler);

    // Wire expand state persistence + lazy sizing load
    grid.querySelectorAll('details.trade-card-collapse').forEach(function (details) {
      details.addEventListener('toggle', function () {
        var tk = details.dataset.tradeKey || '';
        if (tk) _optionsExpandState[tk] = details.open;
        // Lazy-load sizing when card expands
        if (details.open) {
          _loadSizingForCard(details);
        }
      });
      // Load sizing for cards that start already expanded
      if (details.open) {
        _loadSizingForCard(details);
      }
    });

    // Load risk state bar on render
    _loadRiskStateBar();
  }

  /**
   * Build a TradeCard trade key for an options candidate.
   * Format: SYMBOL|OPTIONS|strategy_id|short_strike|long_strike|dte
   */
  function _buildOptionsTradeKey(c) {
    var sym = String(c.symbol || '').toUpperCase();
    var sid = String(c.strategyId || c.strategy || '');
    var shorts = '', longs = '';
    if (c.legs.length >= 1) {
      var sortedLegs = c.legs.slice().sort(function (a, b) { return (a.strike || 0) - (b.strike || 0); });
      var shortLegs = sortedLegs.filter(function (l) { return (l.side || '').toUpperCase() === 'SHORT'; });
      var longLegs  = sortedLegs.filter(function (l) { return (l.side || '').toUpperCase() === 'LONG'; });
      shorts = shortLegs.map(function (l) { return l.strike; }).join(',');
      longs  = longLegs.map(function (l) { return l.strike; }).join(',');
    }
    return sym + '|OPTIONS|' + sid + '|' + (shorts || 'NA') + '|' + (longs || 'NA') + '|' + (c.dte != null ? c.dte : 'NA');
  }

  function _findOptionsRowByTradeKey(tradeKey) {
    if (!tradeKey) return null;
    for (var i = 0; i < _optionsRenderedRows.length; i++) {
      var r = _optionsRenderedRows[i];
      var c = normalizeOptionsCandidate(r);
      if (_buildOptionsTradeKey(c) === tradeKey) return r;
    }
    return null;
  }

  /**
   * Build a single options candidate as a full TradeCard.
   * Uses the same HTML structure as stock TradeCards: <details> collapse,
   * header summary, expandable body sections, always-visible action footer.
   */
  function buildOptionsTradeCard(c, idx, data) {
    var tc = window.BenTradeTradeCard;
    var symbol = c.symbol || '???';
    var strategyLabel = c.strategy ? c.strategy.replace(/_/g, ' ').replace(/\b\w/g, function (ch) { return ch.toUpperCase(); }) : '--';
    var tradeKey = _buildOptionsTradeKey(c);
    c._tradeKey = tradeKey;

    // ── Score badge (model_score preferred, fallback to rank) ──
    var scoreVal = c.modelScore != null ? c.modelScore : null;
    var scoreBadge = '';
    if (scoreVal !== null) {
      scoreBadge = '<span class="trade-rank-badge" style="font-size:14px;font-weight:700;color:var(--accent-cyan);background:rgba(0,220,255,0.08);border:1px solid rgba(0,220,255,0.24);border-radius:8px;padding:3px 10px;white-space:nowrap;">Score ' + Math.round(scoreVal) + '</span>';
    } else if (c.rank) {
      scoreBadge = '<span class="trade-rank-badge" style="font-size:14px;font-weight:700;color:var(--accent-cyan);background:rgba(0,220,255,0.08);border:1px solid rgba(0,220,255,0.24);border-radius:8px;padding:3px 10px;white-space:nowrap;">#' + c.rank + '</span>';
    }

    // ── Header badges ──
    var symbolBadge = tc ? tc.pill(symbol) : '<span class="qtPill">' + esc(symbol) + '</span>';
    var dteBadge = c.dte !== null ? (tc ? tc.pill(c.dte + ' DTE') : '<span class="qtPill">' + c.dte + ' DTE</span>') : '';

    // ── Subtitle: strikes, expiration, premium ──
    var subtitleParts = [];
    if (c.legs.length >= 2) {
      var strikes = c.legs.map(function (l) { return l.strike; }).filter(function (s) { return s != null; });
      var optType = (c.legs[0].option_type || '').toUpperCase();
      subtitleParts.push(strikes.join(' / ') + ' ' + optType);
    }
    if (c.expiration) {
      subtitleParts.push(c.expiration);
    }
    if (c.premium != null) {
      subtitleParts.push(c.premiumLabel.charAt(0).toUpperCase() + c.premiumLabel.slice(1) + ': $' + Number(c.premium).toFixed(2));
    }
    var subtitleText = subtitleParts.join(' \u00B7 ');

    var tradeKeyDisplay = tradeKey
      ? '<span class="trade-key-wrap"><span class="trade-key-label">' + esc(tradeKey) + '</span>'
        + (tc ? tc.copyTradeKeyButton(tradeKey) : '') + '</span>'
      : '';

    // ── Core metrics section (expanded body) ──
    var coreItems = [
      { label: 'EV', value: fmtDollar(c.ev), cssClass: c.ev > 0 ? 'positive' : (c.ev < 0 ? 'negative' : 'neutral') },
      { label: 'POP', value: fmtPct(c.pop), cssClass: c.pop != null && c.pop >= 0.65 ? 'positive' : (c.pop != null ? 'negative' : 'neutral') },
      { label: 'RoR', value: c.ror != null ? (c.ror * 100).toFixed(0) + '%' : '--', cssClass: c.ror != null && c.ror > 0.15 ? 'positive' : 'neutral' },
      { label: 'Max Profit', value: fmtDollar(c.maxProfit), cssClass: 'positive' },
      { label: 'Max Loss', value: c.maxLoss != null ? fmtDollar(Math.abs(c.maxLoss)) : '--', cssClass: 'negative' },
      { label: 'Width', value: c.width != null ? '$' + c.width.toFixed(0) : '--', cssClass: 'neutral' },
      { label: 'EV/Day', value: fmtDollar(c.evPerDay), cssClass: c.evPerDay != null && c.evPerDay > 0 ? 'positive' : 'neutral' },
      { label: 'DTE', value: c.dte != null ? c.dte + 'd' : '--', cssClass: 'neutral' },
    ];
    var coreGridHtml = '<div class="metric-grid">' + coreItems.map(function (item) {
      return '<div class="metric"><div class="metric-label">' + esc(item.label) + '</div><div class="metric-value ' + item.cssClass + '">' + item.value + '</div></div>';
    }).join('') + '</div>';
    var coreSection = '<div class="section section-core"><div class="section-title">CORE METRICS</div>' + coreGridHtml + '</div>';

    // ── Legs detail section (expanded body) ──
    var legsSection = '';
    if (c.legs.length > 0) {
      var legsRows = '';
      c.legs.forEach(function (leg) {
        var side = (leg.side || '').toUpperCase();
        var sideClass = side === 'SHORT' ? 'tmc-leg-short' : 'tmc-leg-long';
        var strike = leg.strike != null ? String(leg.strike) : '?';
        var type = (leg.option_type || '').toUpperCase();
        var bidAsk = '';
        if (leg.bid != null && leg.ask != null) {
          bidAsk = Number(leg.bid).toFixed(2) + ' / ' + Number(leg.ask).toFixed(2);
        }
        var delta = leg.delta != null ? '\u0394 ' + Number(leg.delta).toFixed(2) : '';
        legsRows +=
          '<div class="tmc-options-leg-row">' +
            '<span class="tmc-leg-side ' + sideClass + '">' + esc(side) + '</span>' +
            '<span class="tmc-leg-strike">' + esc(strike) + ' ' + esc(type) + '</span>' +
            '<span class="tmc-leg-pricing">' + esc(bidAsk) + '</span>' +
            '<span class="tmc-leg-delta">' + esc(delta) + '</span>' +
          '</div>';
      });
      legsSection = '<div class="section"><div class="section-title">LEG DETAILS</div><div class="tmc-options-legs">' + legsRows + '</div></div>';
    }

    // ── Build enrichment sections (model review, structure, etc.) ──
    var enrichment = buildOptionsEnrichmentHtml(c);

    // ── Collapse state ──
    var isExpanded = tradeKey ? (_optionsExpandState[tradeKey] === true) : false;
    var openAttr = isExpanded ? ' open' : '';

    // ── Chevron SVG ──
    var chevronSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>';

    // ── Action buttons (always visible) ──
    var tradeKeyAttr = ' data-trade-key="' + esc(tradeKey) + '"';
    var actionsHtml = enrichment.warnings
      + '<div class="trade-actions">'
      + '<div class="actions-row">'
      + '<button type="button" class="btn btn-exec btn-action" data-action="execute"' + tradeKeyAttr + ' title="Preview and execute this options trade">Execute Trade</button>'
      + '<button type="button" class="btn btn-reject btn-action" data-action="reject"' + tradeKeyAttr + ' title="Reject this trade">Reject</button>'
      + '</div>'
      + '<div class="actions-row">'
      + '<button type="button" class="btn btn-action" data-action="data-workbench"' + tradeKeyAttr + ' title="Send to Data Workbench">Send to Data Workbench</button>'
      + '</div>'
      + '</div>';

    // ── Full card HTML ──
    return '<div class="trade-card" data-idx="' + idx + '"' + tradeKeyAttr + ' style="margin-bottom:14px;display:flex;flex-direction:column;">'
      + '<details class="trade-card-collapse"' + tradeKeyAttr + openAttr + '>'
      + '<summary class="trade-summary"><div class="trade-header trade-header-click">'
      + '<div class="trade-header-left"><span class="chev">' + chevronSvg + '</span></div>'
      + '<div class="trade-header-center">'
      + '<div class="trade-type" style="display:flex;align-items:center;gap:8px;justify-content:center;">' + symbolBadge + ' ' + dteBadge + ' ' + esc(strategyLabel) + '</div>'
      + '<div class="trade-subtitle">' + subtitleText + '</div>'
      + (tradeKeyDisplay ? '<div style="text-align:center;">' + tradeKeyDisplay + '</div>' : '')
      + '</div>'
      + '<div class="trade-header-right">' + scoreBadge + '</div>'
      + '</div></summary>'
      + '<div class="trade-body" style="flex:1 1 auto;">'
      + coreSection
      + buildExplanationHtml(c)
      + _buildSizingPlaceholder(c, idx)
      + legsSection
      + enrichment.body
      + '</div>'
      + '</details>'
      + actionsHtml
      + '</div>';
  }

  /**
   * Build options-specific enrichment HTML (expanded body sections).
   * Returns { body, warnings } matching the stock enrichment pattern.
   */
  function buildOptionsEnrichmentHtml(c) {
    var bodyParts = [];
    var warningParts = [];
    var hasModel = !!(c.modelRecommendation && !c.modelDegraded);

    // ── MODEL REVIEW section ──
    if (hasModel) {
      var recText = String(c.modelRecommendation).toUpperCase();
      var confText = c.modelConviction != null ? 'Conf: ' + fmtPctDirect(c.modelConviction) : '';
      var scoreText = c.modelScore != null ? 'Score: ' + Math.round(c.modelScore) : '';
      var headerBadges = [recText, confText, scoreText].filter(Boolean).join(' \u00B7 ');

      var recColor = '#b4b4c8';
      if (recText === 'EXECUTE') recColor = '#00dc78';
      else if (recText === 'PASS') recColor = '#ff5a5a';

      var modelBody = '';
      if (c.modelHeadline) {
        modelBody += '<div style="font-size:13px;font-weight:700;color:var(--text,#d7fbff);margin-bottom:4px;">' + esc(c.modelHeadline) + '</div>';
      }
      if (c.modelNarrative) {
        modelBody += '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(c.modelNarrative) + '</div>';
      }

      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid ' + recColor + '33;background:' + recColor + '08;">'
        + '<div class="section-title" style="margin-bottom:6px;">MODEL REVIEW'
        + (headerBadges ? ' \u2014 <span style="color:' + recColor + ';">' + headerBadges + '</span>' : '')
        + '</div>'
        + modelBody
        + '</div>'
      );
    } else if (c.modelDegraded) {
      warningParts.push(
        '<div style="margin-bottom:6px;padding:5px 10px;font-size:11px;font-weight:600;color:#ff8a5a;background:rgba(255,138,90,0.08);border:1px solid rgba(255,138,90,0.2);border-radius:5px;text-align:center;">'
        + '\u26A0 Model analysis unavailable \u2014 ranked by scanner EV only'
        + '</div>'
      );
    } else if (!c.modelRecommendation) {
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid rgba(138,138,180,0.2);background:rgba(138,138,180,0.04);">'
        + '<div class="section-title" style="color:#8a8ab4;">MODEL REVIEW</div>'
        + '<div style="font-size:12px;color:var(--muted);">Model analysis unavailable</div>'
        + '</div>'
      );
    }

    // ── STRUCTURE ANALYSIS section ──
    var sa = c.modelStructureAnalysis;
    if (sa && typeof sa === 'object') {
      var saRows = [
        { label: 'Strategy', val: sa.strategy_assessment },
        { label: 'Strike Placement', val: sa.strike_placement },
        { label: 'Width', val: sa.width_assessment },
        { label: 'DTE', val: sa.dte_assessment },
      ].filter(function (r) { return !!r.val; });

      if (saRows.length > 0) {
        var saHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(0,220,255,0.03);border-radius:6px;border:1px solid rgba(0,220,255,0.12);">';
        saHtml += '<div class="section-title" style="color:var(--accent-cyan,#00dcff);">STRUCTURE ANALYSIS</div>';
        saRows.forEach(function (r) {
          saHtml += '<div style="font-size:11px;line-height:1.4;padding:2px 0 2px 8px;border-left:2px solid rgba(0,220,255,0.25);margin-bottom:3px;">'
            + '<span style="color:var(--accent-cyan,#00dcff);font-weight:600;">' + esc(r.label) + ':</span> '
            + '<span style="color:var(--text-secondary,#bbb);">' + esc(r.val) + '</span></div>';
        });
        saHtml += '</div>';
        bodyParts.push(saHtml);
      }
    }

    // ── PROBABILITY ASSESSMENT section ──
    var pa = c.modelProbabilityAssessment;
    if (pa && typeof pa === 'object') {
      var paRows = [
        { label: 'POP Quality', val: pa.pop_quality },
        { label: 'EV Quality', val: pa.ev_quality },
        { label: 'Risk/Reward', val: pa.risk_reward },
      ].filter(function (r) { return !!r.val; });

      if (paRows.length > 0) {
        var paHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.12);">';
        paHtml += '<div class="section-title">PROBABILITY ASSESSMENT</div>';
        paRows.forEach(function (r) {
          paHtml += '<div style="font-size:11px;line-height:1.4;padding:2px 0 2px 8px;border-left:2px solid rgba(100,149,237,0.25);margin-bottom:3px;">'
            + '<span style="font-weight:600;color:var(--text,#d7fbff);">' + esc(r.label) + ':</span> '
            + '<span style="color:var(--text-secondary,#bbb);">' + esc(r.val) + '</span></div>';
        });
        paHtml += '</div>';
        bodyParts.push(paHtml);
      }
    }

    // ── GREEKS ASSESSMENT section ──
    var ga = c.modelGreeksAssessment;
    if (ga && typeof ga === 'object') {
      var gaRows = [
        { label: 'Delta', val: ga.delta_read, icon: '\u0394' },
        { label: 'Theta', val: ga.theta_read, icon: '\u0398' },
        { label: 'Vega', val: ga.vega_read, icon: '\u03BD' },
      ].filter(function (r) { return !!r.val; });

      if (gaRows.length > 0) {
        var gaHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(180,200,220,0.04);border-radius:6px;border:1px solid rgba(180,200,220,0.12);">';
        gaHtml += '<div class="section-title">GREEKS ASSESSMENT</div>';
        gaRows.forEach(function (r) {
          gaHtml += '<div style="font-size:11px;line-height:1.4;padding:2px 0 2px 8px;border-left:2px solid rgba(180,200,220,0.25);margin-bottom:3px;">'
            + '<span style="font-weight:600;color:var(--accent-cyan,#00dcff);">' + r.icon + ' ' + esc(r.label) + ':</span> '
            + '<span style="color:var(--text-secondary,#bbb);">' + esc(r.val) + '</span></div>';
        });
        gaHtml += '</div>';
        bodyParts.push(gaHtml);
      }
    }

    // ── MARKET ALIGNMENT section ──
    if (c.modelMarketAlignment) {
      var maText = String(c.modelMarketAlignment);
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.12);">'
        + '<div class="section-title">MARKET ALIGNMENT</div>'
        + '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(maText) + '</div>'
        + '</div>'
      );
    }

    // ── CAUTION section ──
    if (c.modelCautionNotes.length > 0) {
      var cautionLis = c.modelCautionNotes.map(function (note) {
        return '<li style="margin-bottom:2px;">' + esc(note) + '</li>';
      }).join('');
      bodyParts.push(
        '<div class="section" style="margin-bottom:6px;padding:6px 10px;border-radius:6px;border:1px solid rgba(244,200,95,0.2);background:rgba(244,200,95,0.04);">'
        + '<div class="section-title" style="color:var(--warn,#f4c85f);">CAUTION</div>'
        + '<ul style="margin:0;padding-left:16px;font-size:11px;line-height:1.5;">' + cautionLis + '</ul>'
        + '</div>'
      );
    }

    // ── KEY FACTORS section ──
    if (c.modelKeyFactors.length > 0) {
      var factorsHtml = '';
      c.modelKeyFactors.forEach(function (f) {
        if (typeof f === 'string') {
          factorsHtml += '<div style="font-size:11px;color:var(--text-secondary,#bbb);line-height:1.4;padding:3px 0 3px 8px;border-left:2px solid #8899aa;margin-bottom:3px;">' + esc(f) + '</div>';
        } else if (f && typeof f === 'object') {
          var factorName = f.factor || f.name || '';
          var impact = String(f.impact || f.assessment || 'neutral').toLowerCase();
          var evidence = f.evidence || f.detail || '';
          var impColor = _assessColors[impact] || '#8899aa';
          var impLabel = impact.charAt(0).toUpperCase() + impact.slice(1);

          factorsHtml += '<div style="font-size:11px;line-height:1.4;padding:4px 0 4px 8px;border-left:2px solid ' + impColor + ';margin-bottom:4px;">'
            + '<div style="display:flex;align-items:center;gap:6px;">'
            + '<span style="color:' + impColor + ';font-weight:600;">' + esc(factorName) + '</span>'
            + '<span style="font-size:9px;padding:1px 5px;border-radius:3px;border:1px solid ' + impColor + '44;color:' + impColor + ';text-transform:uppercase;letter-spacing:0.3px;">' + esc(impLabel) + '</span>'
            + '</div>'
            + (evidence ? '<div style="font-size:10px;color:var(--muted,#6a8da8);margin-top:2px;">' + esc(evidence) + '</div>' : '')
            + '</div>';
        }
      });
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;">'
        + '<div class="section-title">KEY FACTORS</div>'
        + factorsHtml
        + '</div>'
      );
    }

    // ── SUGGESTED ADJUSTMENT ──
    if (c.modelSuggestedAdjustment) {
      bodyParts.push(
        '<div class="section" style="margin-bottom:6px;padding:6px 10px;border-radius:6px;border:1px solid rgba(0,220,255,0.15);background:rgba(0,220,255,0.03);">'
        + '<div class="section-title" style="color:var(--accent-cyan,#00dcff);">SUGGESTED ADJUSTMENT</div>'
        + '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(c.modelSuggestedAdjustment) + '</div>'
        + '</div>'
      );
    }

    return { body: bodyParts.join(''), warnings: warningParts.join('') };
  }

  /**
   * Execute an options trade via the TradingService preview/submit flow.
   * Builds multi-leg order from candidate legs and opens preview modal.
   */
  function _executeOptionsTrade(btn, tradeKey, rawCandidate) {
    if (!rawCandidate || !rawCandidate.legs || rawCandidate.legs.length === 0) {
      console.warn('[TMC] Cannot execute options trade: no legs on candidate');
      if (typeof showToast === 'function') {
        showToast('Cannot execute: trade has no leg data', 'warning');
      }
      return;
    }

    // Pre-fill quantity from cached sizing result
    var cached = _getCachedSizing(tradeKey);
    if (cached && cached.sizing && !cached.sizing.blocked && cached.sizing.suggested_contracts > 0) {
      rawCandidate.quantity = cached.sizing.suggested_contracts;
    }

    // Open the TradeTicket modal — it handles normalize, validate, preview, submit
    console.log('[TMC] Raw candidate passed to modal:', JSON.stringify(rawCandidate, null, 2));
    if (window.BenTradeTradeTicket && typeof window.BenTradeTradeTicket.open === 'function') {
      window.BenTradeTradeTicket.open(rawCandidate);
    } else {
      console.error('[TMC] BenTradeTradeTicket not available');
      if (typeof showToast === 'function') {
        showToast('Execution modal not loaded. Try refreshing the page.', 'error');
      }
    }
  }

  function _startOptionsCompletionPoll(baselineRunId, intervalMs, maxAttempts) {
    _stopOptionsCompletionPoll();
    var attempts = 0;
    intervalMs = intervalMs || 15000;
    maxAttempts = maxAttempts || 20;

    _optionsPollTimer = setInterval(function () {
      attempts++;
      if (attempts > maxAttempts) {
        _stopOptionsCompletionPoll();
        return;
      }
      api.tmcGetLatestOptions()
        .then(function (resp) {
          var newRunId = resp && resp.data ? resp.data.run_id : null;
          if (newRunId && newRunId !== baselineRunId) {
            console.log('[TMC] Options completion poll detected new run: ' + newRunId);
            _stopOptionsCompletionPoll();
            loadOptionsOpportunities();
          }
        })
        .catch(function () {});
    }, intervalMs);
  }

  function _stopOptionsCompletionPoll() {
    if (_optionsPollTimer) {
      clearInterval(_optionsPollTimer);
      _optionsPollTimer = null;
    }
  }

  function triggerOptionsRun() {
    var statusEl = document.getElementById('tmcOptionsStatus');
    if (statusEl) { statusEl.textContent = 'Running...'; statusEl.className = 'tmc-run-status'; }

    var baselineRunId = _lastOptionsRunId;
    console.log('[TMC] Triggering options workflow (baseline run_id=' + (baselineRunId || 'none') + ')');

    api.tmcRunOptions()
      .then(function (result) {
        console.log('[TMC] Options workflow trigger returned: status=' + result.status +
          ' run_id=' + (result.run_id || '?'));
        updateStatusBadge(statusEl, result.status);
        _stopOptionsCompletionPoll();
        loadOptionsOpportunities();
      })
      .catch(function (err) {
        console.error('[TMC] Options workflow trigger failed:', err);
        updateStatusBadge(statusEl, 'failed');
        _startOptionsCompletionPoll(baselineRunId);
        loadOptionsOpportunities();
      });
  }

  /* =================================================================
   *  SECTION 3 -- Active Trade Candidates (unchanged -- uses
   *  /api/active-trade-pipeline, separate from TMC workflow endpoints)
   * ================================================================= */

  function recClass(recommendation) {
    switch ((recommendation || '').toUpperCase()) {
      case 'HOLD': return 'tmc-rec-hold';
      case 'REDUCE': return 'tmc-rec-reduce';
      case 'CLOSE': return 'tmc-rec-close';
      case 'URGENT_REVIEW': return 'tmc-rec-urgent';
      default: return 'tmc-rec-unknown';
    }
  }

  function urgencyLabel(urgency) {
    switch (urgency) {
      case 5: return 'CRITICAL';
      case 4: return 'HIGH';
      case 3: return 'MODERATE';
      case 2: return 'LOW';
      default: return 'NONE';
    }
  }

  function urgencyClass(urgency) {
    if (urgency >= 4) return 'tmc-urgency-high';
    if (urgency >= 3) return 'tmc-urgency-moderate';
    return 'tmc-urgency-low';
  }

  var _tmcAccountMode = 'paper';

  function _getAccountMode() {
    return _tmcAccountMode || 'paper';
  }

  function runActivePipeline() {
    if (_activeRunning) return;
    _activeRunning = true;
    _manualRefreshInProgress = true;

    var btn = document.getElementById('tmcRunActiveBtn');
    if (btn) { btn.textContent = 'Running...'; btn.disabled = true; }

    var skipModel = false;
    var cb = document.getElementById('tmcSkipModel');
    if (cb) skipModel = cb.checked;

    var accountMode = _getAccountMode();
    var url = '/api/active-trade-pipeline/run?account_mode=' + encodeURIComponent(accountMode) + '&skip_model=' + (skipModel ? 'true' : 'false');

    var startTime = Date.now();
    console.log('[TMC] Active pipeline started (standalone)', { accountMode: accountMode, skipModel: skipModel });

    fetch(url, { method: 'POST' })
      .then(function (r) {
        if (!r.ok) {
          return r.text().then(function (t) {
            var err = new Error('HTTP ' + r.status);
            err.responseText = t;
            throw err;
          });
        }
        return r.json();
      })
      .then(function (data) {
        var elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        _activeRunning = false;
        if (btn) { btn.textContent = 'Analyze Positions'; btn.disabled = false; }
        console.log('[TMC:DIAG] Active pipeline response (standalone):', {
          elapsed: elapsed + 's', ok: data.ok, keys: Object.keys(data),
          recCount: (data.recommendations || []).length,
        });
        if (data.ok === false) {
          showActiveError('Pipeline error: ' + ((data.error || {}).message || 'unknown'), data);
          _lastManualActiveRenderAt = Date.now();
          _manualRefreshInProgress = false;
          return;
        }
        renderActiveResults(data);
        _lastManualActiveRenderAt = Date.now();
        _manualRefreshInProgress = false;
      })
      .catch(function (err) {
        var elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        _activeRunning = false;
        if (btn) { btn.textContent = 'Analyze Positions'; btn.disabled = false; }
        console.error('[TMC:DIAG] Active pipeline FAILED (standalone):', err, err.responseText || '');
        showActiveError('Request failed (' + elapsed + 's): ' + err.message, null);
        _lastManualActiveRenderAt = Date.now();
        _manualRefreshInProgress = false;
      });
  }

  /**
   * Load latest active results from the GET endpoint.
   * @param {Object} [opts] - Options
   * @param {boolean} [opts.force] - If true, bypass the manual render guard
   */
  function loadLatestActiveResults(opts) {
    var force = opts && opts.force;
    // Guard: don't overwrite while a manual refresh is in progress (flag-based, primary)
    if (!force && _manualRefreshInProgress) {
      console.log('[TMC:DIAG] loadLatestActiveResults SKIPPED — manual refresh in progress (flag guard)');
      return;
    }
    // Backup time-based guard (belt and suspenders)
    if (!force && _lastManualActiveRenderAt && (Date.now() - _lastManualActiveRenderAt < _MANUAL_RENDER_GUARD_MS)) {
      console.log('[TMC:DIAG] loadLatestActiveResults SKIPPED — manual render guard active (' +
        Math.round((Date.now() - _lastManualActiveRenderAt) / 1000) + 's ago)');
      return;
    }
    console.log('[TMC:DIAG] loadLatestActiveResults — fetching GET /results', { force: !!force });
    // Show loading state if grid has no rendered cards yet
    var activeGrid = document.getElementById('tmcActiveTradeGrid');
    if (activeGrid && !activeGrid.querySelector('.trade-card')) {
      activeGrid.innerHTML = '<div class="tmc-section-loading">Loading active trades…</div>';
    }
    var _activeAbort = new AbortController();
    var _activeTimer = setTimeout(function () { _activeAbort.abort(); }, 10000);
    fetch('/api/active-trade-pipeline/results', { signal: _activeAbort.signal })
      .then(function (r) {
        clearTimeout(_activeTimer);
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        console.log('[TMC:DIAG] loadLatestActiveResults response:', { ok: data.ok, keys: Object.keys(data), recCount: (data.recommendations || []).length });
        if (data.ok === false) {
          // On initial page load this is expected — show non-alarming empty state
          var msg = (data.error || {}).message || 'No results available';
          showActiveEmpty(msg);
          return;
        }
        renderActiveResults(data);
      })
      .catch(function (err) {
        clearTimeout(_activeTimer);
        if (err.name === 'AbortError') {
          console.warn('[TMC:DIAG] loadLatestActiveResults TIMEOUT (10s)');
          showActiveEmpty('Backend busy — try again in a moment');
          return;
        }
        console.error('[TMC:DIAG] loadLatestActiveResults FAILED:', err);
        showActiveEmpty('Failed to load results: ' + err.message);
      });
  }

  /** Show a non-error empty state (e.g. "no positions", "run the pipeline first"). */
  function showActiveEmpty(msg) {
    console.log('[TMC:DIAG] showActiveEmpty called:', msg, new Error().stack.split('\n').slice(1, 3).join(' <- '));
    var grid = document.getElementById('tmcActiveTradeGrid');
    if (grid) {
      grid.innerHTML =
        '<div class="tmc-empty-state">' +
          '<div class="tmc-empty-icon">&#9673;</div>' +
          '<div class="tmc-empty-text">' + esc(msg) + '</div>' +
        '</div>';
    }
    var count = document.getElementById('tmcActiveCount');
    if (count) { count.textContent = '--'; count.className = 'tmc-count-badge tmc-count-muted'; }
    var ps = document.getElementById('tmcPortfolioSummary');
    if (ps) ps.remove();
  }

  /**
   * Show an error state with diagnostic detail (expandable).
   * Used when the pipeline returns ok:false or an HTTP error.
   */
  function showActiveError(msg, responseData) {
    console.error('[TMC:DIAG] showActiveError called:', msg, new Error().stack.split('\n').slice(1, 3).join(' <- '));
    var ps = document.getElementById('tmcPortfolioSummary');
    if (ps) ps.remove();
    var grid = document.getElementById('tmcActiveTradeGrid');
    if (grid) {
      var detailHtml = '';
      if (responseData) {
        var snippet = JSON.stringify(responseData, null, 2);
        if (snippet.length > 2000) snippet = snippet.substring(0, 2000) + '…';
        detailHtml =
          '<details style="margin-top:8px;">' +
            '<summary style="color:rgba(224,224,224,0.4); cursor:pointer; font-size:0.75rem;">Response details</summary>' +
            '<pre style="color:rgba(224,224,224,0.5); font-size:0.7rem; max-height:200px; overflow:auto; margin-top:4px; white-space:pre-wrap;">' +
              esc(snippet) + '</pre>' +
          '</details>';
      }
      grid.innerHTML =
        '<div style="background:rgba(255,23,68,0.1); border:1px solid rgba(255,23,68,0.3); border-radius:6px; padding:12px; margin:8px 0;">' +
          '<div style="color:#ff1744; font-weight:600;">Pipeline Error</div>' +
          '<div style="color:rgba(224,224,224,0.7); font-size:0.85rem; margin-top:4px;">' + esc(msg) + '</div>' +
          detailHtml +
        '</div>';
    }
    var count = document.getElementById('tmcActiveCount');
    if (count) { count.textContent = 'ERR'; count.className = 'tmc-count-badge tmc-count-error'; }
  }

  /**
   * Show an error state for the portfolio balance section.
   */
  function showBalanceError(msg, responseData) {
    console.error('[TMC:DIAG] showBalanceError called:', msg, new Error().stack.split('\n').slice(1, 3).join(' <- '));
    var grid = document.getElementById('tmcPortfolioBalanceGrid');
    var section = document.getElementById('tmcPortfolioBalanceSection');
    if (section) section.style.display = '';
    if (grid) {
      var detailHtml = '';
      if (responseData) {
        var snippet = JSON.stringify(responseData, null, 2);
        if (snippet.length > 2000) snippet = snippet.substring(0, 2000) + '…';
        detailHtml =
          '<details style="margin-top:8px;">' +
            '<summary style="color:rgba(224,224,224,0.4); cursor:pointer; font-size:0.75rem;">Response details</summary>' +
            '<pre style="color:rgba(224,224,224,0.5); font-size:0.7rem; max-height:200px; overflow:auto; margin-top:4px; white-space:pre-wrap;">' +
              esc(snippet) + '</pre>' +
          '</details>';
      }
      grid.innerHTML =
        '<div style="background:rgba(255,23,68,0.1); border:1px solid rgba(255,23,68,0.3); border-radius:6px; padding:12px;">' +
          '<div style="color:#ff1744; font-weight:600;">Rebalance Error</div>' +
          '<div style="color:rgba(224,224,224,0.7); font-size:0.85rem; margin-top:4px;">' + esc(msg) + '</div>' +
          detailHtml +
        '</div>';
    }
    var badge = document.getElementById('tmcBalanceStatus');
    if (badge) { badge.textContent = 'ERR'; badge.className = 'tmc-count-badge tmc-count-error'; }
  }

  /** Keep rendered active trade rows for action handler lookups. */
  var _activeRenderedRows = [];
  var _activeExpandState  = {};

  // ── Management display helpers ──────────────────────────────

  var _mgmtStatusStyles = {
    'AT_TARGET':  { bg: '#065f46', border: '#059669', color: '#34d399', icon: '\uD83C\uDFAF', label: 'AT TARGET' },
    'ON_TRACK':   { bg: '#064e3b', border: '#10b981', color: '#6ee7b7', icon: '\u2705', label: 'ON TRACK' },
    'NEUTRAL':    { bg: '#374151', border: '#6b7280', color: '#9ca3af', icon: '\u23F3', label: 'NEUTRAL' },
    'IN_DANGER':  { bg: '#78350f', border: '#d97706', color: '#fbbf24', icon: '\u26A0\uFE0F', label: 'IN DANGER' },
    'AT_STOP':    { bg: '#7f1d1d', border: '#dc2626', color: '#f87171', icon: '\uD83D\uDED1', label: 'AT STOP' },
    'TIME_DECAY': { bg: '#4c1d95', border: '#7c3aed', color: '#a78bfa', icon: '\u23F1\uFE0F', label: 'GAMMA ZONE' },
    'EXPIRED':    { bg: '#1f2937', border: '#4b5563', color: '#9ca3af', icon: '\uD83D\uDCC5', label: 'EXPIRED' },
  };

  function renderStatusBadge(status) {
    var s = _mgmtStatusStyles[status] || _mgmtStatusStyles['NEUTRAL'];
    return '<span class="mgmt-status-badge" style="background:' + s.bg + ';border:1px solid ' + s.border + ';color:' + s.color + ';padding:3px 10px;border-radius:4px;font-size:12px;font-weight:500;white-space:nowrap;">' + s.icon + ' ' + s.label + '</span>';
  }

  function renderPnlProgressBar(pos) {
    var profitPct = pos.profit_progress_pct || 0;
    var lossPct = pos.loss_progress_pct || 0;
    var totalPnl = pos.total_pnl;
    if (totalPnl == null) totalPnl = ((pos.position_snapshot || {}).unrealized_pnl) || 0;

    // Position marker: 0% = stop, 50% = entry, 100% = target
    var barPosition;
    if (totalPnl >= 0) {
      barPosition = 50 + (profitPct / 100 * 50);
    } else {
      barPosition = 50 - (lossPct / 100 * 50);
    }
    barPosition = Math.max(2, Math.min(98, barPosition));

    var pnlColor = totalPnl >= 0 ? '#4ade80' : '#f87171';

    // Dollar labels
    var stopDollar = '';
    var targetDollar = '';
    var maxLoss = pos.max_loss_per_unit;
    var maxProfit = pos.max_profit_per_unit;
    var qty = ((pos.position_snapshot || {}).quantity) || 1;
    var mult = pos.strategy_class === 'equity' ? 1 : 100;
    if (maxLoss != null) stopDollar = '-$' + Math.abs(maxLoss * qty * mult).toFixed(0);
    if (maxProfit != null) targetDollar = '+$' + Math.abs(maxProfit * qty * mult).toFixed(0);

    var pnlDollar = (totalPnl >= 0 ? '+' : '') + '$' + totalPnl.toFixed(0);

    return '<div class="pnl-bar-container">'
      + '<div class="pnl-bar-labels"><span class="pnl-stop-label">STOP</span><span class="pnl-entry-label">entry</span><span class="pnl-target-label">TARGET</span></div>'
      + '<div class="pnl-bar-track">'
      + '<div class="pnl-bar-stop-zone"></div>'
      + '<div class="pnl-bar-profit-zone"></div>'
      + '<div class="pnl-bar-entry-line"></div>'
      + '<div class="pnl-bar-marker" style="left:' + barPosition + '%;background:' + pnlColor + ';"></div>'
      + '</div>'
      + '<div class="pnl-bar-values">'
      + '<span style="color:#f87171;">' + esc(stopDollar) + '</span>'
      + '<span style="color:rgba(255,255,255,0.3);">$0</span>'
      + '<span style="color:rgba(255,255,255,0.7);">' + esc(pnlDollar) + '</span>'
      + '<span style="color:#4ade80;">' + esc(targetDollar) + '</span>'
      + '</div>'
      + '</div>';
  }

  function renderActionSuggestion(action) {
    if (!action || !action.message) return '';
    var urgStyles = {
      'high':   { bg: 'rgba(248,113,113,0.08)', border: '#f87171', icon: '\uD83D\uDD34' },
      'medium': { bg: 'rgba(251,191,36,0.08)',  border: '#fbbf24', icon: '\uD83D\uDFE1' },
      'low':    { bg: 'rgba(74,222,128,0.05)',   border: '#4ade80', icon: '\uD83D\uDFE2' },
    };
    var s = urgStyles[action.urgency] || urgStyles['low'];
    return '<div class="action-suggestion" style="background:' + s.bg + ';border-left:3px solid ' + s.border + ';padding:8px 12px;margin:8px 0;border-radius:0 4px 4px 0;">'
      + '<span>' + s.icon + '</span>'
      + '<span class="action-text">' + esc(action.message) + '</span>'
      + '</div>';
  }

  function renderPortfolioSummary(summary) {
    if (!summary || !summary.total_positions) return '';
    var pnlColor = summary.total_pnl >= 0 ? '#4ade80' : '#f87171';
    var pnlSign = summary.total_pnl >= 0 ? '+' : '';
    var html = '<div class="portfolio-summary">'
      + '<div class="summary-item"><span class="summary-label">Positions</span><span class="summary-value">' + summary.total_positions + '</span></div>'
      + '<div class="summary-item"><span class="summary-label">Total P&amp;L</span><span class="summary-value" style="color:' + pnlColor + ';">' + pnlSign + '$' + (summary.total_pnl || 0).toFixed(0) + '</span></div>'
      + '<div class="summary-item"><span class="summary-label">W / L</span><span class="summary-value">' + (summary.winning || 0) + ' / ' + (summary.losing || 0) + '</span></div>'
      + '<div class="summary-item"><span class="summary-label">Actions Needed</span><span class="summary-value" style="color:' + (summary.actions_needed > 0 ? '#fbbf24' : '#4ade80') + ';">' + (summary.actions_needed || 0) + '</span></div>';
    if (summary.positions_at_target > 0) {
      html += '<div class="summary-alert">\uD83C\uDFAF ' + summary.positions_at_target + ' position(s) at profit target \u2014 close to lock in gains</div>';
    }
    if (summary.positions_at_stop > 0) {
      html += '<div class="summary-alert danger">\uD83D\uDED1 ' + summary.positions_at_stop + ' position(s) at stop loss \u2014 close to limit losses</div>';
    }
    return html + '</div>';
  }

  var _mgmtUrgencyOrder = {
    'AT_STOP': 0, 'AT_TARGET': 1, 'TIME_DECAY': 2, 'IN_DANGER': 3,
    'EXPIRED': 4, 'ON_TRACK': 5, 'NEUTRAL': 6,
  };

  function sortByManagementUrgency(positions) {
    return positions.slice().sort(function (a, b) {
      var ua = _mgmtUrgencyOrder[a.management_status] != null ? _mgmtUrgencyOrder[a.management_status] : 99;
      var ub = _mgmtUrgencyOrder[b.management_status] != null ? _mgmtUrgencyOrder[b.management_status] : 99;
      if (ua !== ub) return ua - ub;
      // Tiebreaker: engine urgency, then conviction
      var eua = a.urgency || 0, eub = b.urgency || 0;
      if (eua !== eub) return eub - eua;
      return (b.conviction || 0) - (a.conviction || 0);
    });
  }

  function renderActiveResults(data) {
    console.log('[TMC:DIAG] renderActiveResults called:', {
      ok: data && data.ok, recCount: data && data.recommendations ? data.recommendations.length : 'N/A',
      keys: data ? Object.keys(data) : [],
    }, new Error().stack.split('\n').slice(1, 3).join(' <- '));
    var grid = document.getElementById('tmcActiveTradeGrid');
    var countEl = document.getElementById('tmcActiveCount');

    if (!grid) {
      console.error('[TMC:DIAG] renderActiveResults ABORT — tmcActiveTradeGrid not found!');
      return;
    }

    // Guard: pipeline returned an error envelope
    if (data && data.ok === false) {
      var errMsg = (data.error || {}).message || 'Pipeline returned an error';
      console.error('[TMC] renderActiveResults received ok:false —', errMsg);
      showActiveError(errMsg, data);
      return;
    }

    // Cache for instant re-render on SPA navigation
    _cachedActiveData = data;
    _removeRefreshingBadge('active');

    var recs = data.recommendations || [];

    if (recs.length === 0) {
      showActiveEmpty('No open positions found on ' + (_getAccountMode() || 'paper').toUpperCase() + ' account');
      return;
    }

    var sorted = recs.slice().sort(function (a, b) {
      var ua = a.urgency || 0, ub = b.urgency || 0;
      if (ua !== ub) return ub - ua;
      return (b.conviction || 0) - (a.conviction || 0);
    });

    // If management enrichment is present, prefer management-aware sort
    if (recs[0] && recs[0].management_status) {
      sorted = sortByManagementUrgency(recs);
    }

    if (countEl) {
      countEl.textContent = String(sorted.length);
      countEl.className = 'tmc-count-badge';
    }

    var tsEl = document.getElementById('tmcActiveTimestamp');
    if (tsEl) {
      _activeGeneratedAt = data.generated_at || data.timestamp || new Date().toISOString();
      updateFreshness(tsEl, _activeGeneratedAt);
    }

    _activeRenderedRows = sorted.slice();

    var html = '';
    sorted.forEach(function (rec, idx) {
      try {
        html += buildActiveTradeCard(rec, idx);
      } catch (cardErr) {
        console.warn('[TMC] Active card render error for rec ' + idx, cardErr);
        html += '<div class="trade-card" style="margin-bottom:12px;padding:10px;border:1px solid rgba(255,120,100,0.3);border-radius:10px;background:rgba(8,18,26,0.9);color:rgba(255,180,160,0.8);font-size:12px;">\u26A0 Render error for ' + esc((rec && rec.symbol) || '#' + idx) + '</div>';
      }
    });

    grid.innerHTML = html;

    // Wire delegated action handlers (remove old listener to prevent stacking)
    if (_activeGridClickHandler) {
      grid.removeEventListener('click', _activeGridClickHandler);
    }
    _activeGridClickHandler = function (e) {
      var btn = e.target.closest('[data-action]');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      var action   = btn.dataset.action;
      var tradeKey = btn.dataset.tradeKey || '';
      var row      = _findActiveRowByTradeKey(tradeKey);

      if (action === 'close-position' && row) {
        _executeActiveClose(btn, tradeKey, row);
      } else if (action === 'refresh-analysis' && row) {
        _refreshSinglePosition(btn, row);
      } else if (action === 'data-workbench' && row) {
        _openDataWorkbenchInline(row, 'active');
      }
    };
    grid.addEventListener('click', _activeGridClickHandler);

    // Wire expand state persistence
    grid.querySelectorAll('details.trade-card-collapse').forEach(function (details) {
      details.addEventListener('toggle', function () {
        var tk = details.dataset.tradeKey || '';
        if (tk) _activeExpandState[tk] = details.open;
      });
    });

    // Run meta banner
    var summary = data.summary || {};
    var acctMode = data.account_mode || _getAccountMode();
    var acctBadge = '<span class="active-account-badge badge-' + acctMode + '">' + acctMode.toUpperCase() + '</span>';

    var metaHtml =
      '<div class="tmc-active-run-meta">' +
        acctBadge +
        '<span class="tmc-meta-item">Run ' + esc((data.run_id || '').substring(0, 16)) + '</span>' +
        '<span class="tmc-meta-sep">|</span>' +
        '<span class="tmc-meta-item">' + (data.duration_ms || 0) + 'ms</span>' +
        '<span class="tmc-meta-sep">|</span>' +
        '<span class="tmc-meta-item">' + (summary.hold_count || 0) + ' hold</span>' +
        '<span class="tmc-meta-sep">|</span>' +
        '<span class="tmc-meta-item">' + (summary.reduce_count || 0) + ' reduce</span>' +
        '<span class="tmc-meta-sep">|</span>' +
        '<span class="tmc-meta-item">' + (summary.close_count || 0) + ' close</span>' +
        (summary.urgent_review_count > 0
          ? '<span class="tmc-meta-sep">|</span><span class="tmc-meta-item tmc-urgency-high">' + summary.urgent_review_count + ' urgent</span>'
          : '') +
      '</div>';

    var oldMeta = document.getElementById('tmcActiveRunMeta');
    if (oldMeta) oldMeta.remove();

    grid.insertAdjacentHTML('beforebegin',
      '<div id="tmcActiveRunMeta">' + metaHtml + '</div>'
    );

    // Portfolio management summary (from backend enrichment)
    var oldPortSummary = document.getElementById('tmcPortfolioSummary');
    if (oldPortSummary) oldPortSummary.remove();
    var portfolioSummary = data.portfolio_summary;
    if (portfolioSummary) {
      grid.insertAdjacentHTML('beforebegin',
        '<div id="tmcPortfolioSummary">' + renderPortfolioSummary(portfolioSummary) + '</div>'
      );
    }
  }

  /**
   * Build a trade key for an active trade recommendation.
   * Format: SYMBOL|ACTIVE|strategy|expiration|dte
   */
  function _buildActiveTradeKey(rec) {
    var sym = String(rec.symbol || '').toUpperCase();
    var strat = String(rec.strategy || rec.strategy_id || '');
    var exp = rec.expiration || 'NA';
    var dte = rec.dte != null ? String(rec.dte) : 'NA';
    return sym + '|ACTIVE|' + strat + '|' + exp + '|' + dte;
  }

  function _findActiveRowByTradeKey(tradeKey) {
    if (!tradeKey) return null;
    for (var i = 0; i < _activeRenderedRows.length; i++) {
      if (_buildActiveTradeKey(_activeRenderedRows[i]) === tradeKey) return _activeRenderedRows[i];
    }
    return null;
  }

  /**
   * Build a single active trade recommendation as a full TradeCard.
   * Same <details>/<summary> pattern as stock and options cards.
   */
  function buildActiveTradeCard(rec, idx) {
    var tc = window.BenTradeTradeCard;
    var symbol = rec.symbol || '???';
    var recommendation = (rec.recommendation || '--').toUpperCase();
    var conviction = rec.conviction;
    var urgency = rec.urgency || 1;
    var strategy = rec.strategy || '';
    var strategyLabel = strategy ? strategy.replace(/_/g, ' ').replace(/\b\w/g, function (ch) { return ch.toUpperCase(); }) : '--';
    var dte = rec.dte;
    var posSnap = rec.position_snapshot || {};
    var engineSummary = rec.internal_engine_summary || {};
    var engineMetrics = rec.internal_engine_metrics || {};
    var modelSummary = rec.model_summary || {};
    var tradeKey = _buildActiveTradeKey(rec);

    // ── Health score badge (replaces Score badge) ──
    var healthScore = engineSummary.trade_health_score;
    var healthColor = '#8a8ab4';
    if (healthScore != null) {
      if (healthScore >= 70) healthColor = '#00dc78';
      else if (healthScore >= 45) healthColor = '#ffc83c';
      else healthColor = '#ff5a5a';
    }
    var healthBadge = healthScore != null
      ? '<span class="trade-rank-badge" style="font-size:14px;font-weight:700;color:' + healthColor + ';background:' + healthColor + '12;border:1px solid ' + healthColor + '44;border-radius:8px;padding:3px 10px;white-space:nowrap;">Health ' + Math.round(healthScore) + '</span>'
      : '';

    // ── Recommendation badge ──
    var recColor = '#b4b4c8';
    var recPulse = '';
    if (recommendation === 'HOLD') recColor = '#00dc78';
    else if (recommendation === 'REDUCE') recColor = '#ffc83c';
    else if (recommendation === 'CLOSE') recColor = '#ff5a5a';
    else if (recommendation === 'URGENT_REVIEW') { recColor = '#ff5a5a'; recPulse = ' tmc-pulse'; }
    var recBadge = '<span class="' + recClass(recommendation) + recPulse + '" style="font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid ' + recColor + '44;color:' + recColor + ';font-weight:700;letter-spacing:0.3px;white-space:nowrap;">' + esc(recommendation.replace(/_/g, ' ')) + '</span>';

    // ── P&L display ──
    var pnlVal = posSnap.unrealized_pnl;
    var pnlPct = posSnap.unrealized_pnl_pct;
    var pnlColor = pnlVal != null ? (pnlVal >= 0 ? '#00dc78' : '#ff5a5a') : '#8a8ab4';
    var pnlText = pnlVal != null ? '$' + pnlVal.toFixed(2) : '--';
    var pnlPctText = pnlPct != null ? ' (' + (pnlPct * 100).toFixed(1) + '%)' : '';

    // ── Header pills ──
    var symbolBadge = tc ? tc.pill(symbol) : '<span class="qtPill">' + esc(symbol) + '</span>';
    var dteBadge = dte != null ? (tc ? tc.pill(dte + ' DTE') : '<span class="qtPill">' + dte + ' DTE</span>') : '';

    // ── Subtitle: strikes, expiration, P&L ──
    var subtitleParts = [];
    subtitleParts.push(strategyLabel);
    if (rec.expiration) subtitleParts.push(rec.expiration);
    subtitleParts.push('<span style="color:' + pnlColor + ';">' + pnlText + pnlPctText + '</span>');
    var subtitleText = subtitleParts.join(' \u00B7 ');

    // ── Management status badge (from enrichment) ──
    var mgmtStatus = rec.management_status;
    var mgmtBadge = mgmtStatus ? renderStatusBadge(mgmtStatus) : '';
    var mgmtAction = rec.suggested_action;

    // ── Management subtitle addition ──
    var mgmtSubtitlePart = '';
    if (mgmtStatus && rec.profit_progress_pct != null) {
      mgmtSubtitlePart = ' ' + (_mgmtStatusStyles[mgmtStatus] || _mgmtStatusStyles['NEUTRAL']).icon + ' ' + Math.round(rec.profit_progress_pct) + '% toward target';
    }

    // ── Chevron SVG ──
    var chevronSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>';

    // ── Build enrichment sections ──
    var enrichment = buildActiveEnrichmentHtml(rec);

    // ── Collapse state ──
    var isExpanded = tradeKey ? (_activeExpandState[tradeKey] === true) : false;
    var openAttr = isExpanded ? ' open' : '';

    // ── Action buttons (always visible) ──
    var tradeKeyAttr = ' data-trade-key="' + esc(tradeKey) + '"';
    var mgmtSuggestsClose = mgmtAction && mgmtAction.action === 'CLOSE';
    var isActionable = recommendation === 'CLOSE' || recommendation === 'URGENT_REVIEW' || recommendation === 'REDUCE' || mgmtSuggestsClose;
    var actionsHtml = enrichment.warnings
      + '<div class="trade-actions">'
      + '<div class="actions-row">';
    if (isActionable) {
      var closeBtnClass = (recommendation === 'CLOSE' || recommendation === 'URGENT_REVIEW') ? 'btn-danger' : 'btn-warn';
      var closeBtnLabel = recommendation === 'REDUCE' ? 'Reduce Position' : 'Close Position';
      actionsHtml += '<button type="button" class="btn ' + closeBtnClass + ' btn-action" data-action="close-position"' + tradeKeyAttr + ' title="' + esc(closeBtnLabel) + '">' + closeBtnLabel + '</button>';
    }
    actionsHtml += '<button type="button" class="btn btn-action" data-action="refresh-analysis"' + tradeKeyAttr + ' title="Re-run analysis for this position">Refresh Analysis</button>'
      + '</div>'
      + '<div class="actions-row">'
      + '<button type="button" class="btn btn-action" data-action="data-workbench"' + tradeKeyAttr + ' title="Send to Data Workbench">Send to Data Workbench</button>'
      + '</div>'
      + '</div>';

    // ── Full card HTML ──
    return '<div class="trade-card" data-idx="' + idx + '"' + tradeKeyAttr + ' style="margin-bottom:14px;display:flex;flex-direction:column;">'
      + '<details class="trade-card-collapse"' + tradeKeyAttr + openAttr + '>'
      + '<summary class="trade-summary"><div class="trade-header trade-header-click">'
      + '<div class="trade-header-left"><span class="chev">' + chevronSvg + '</span></div>'
      + '<div class="trade-header-center">'
      + '<div class="trade-type" style="display:flex;align-items:center;gap:8px;justify-content:center;flex-wrap:wrap;">' + symbolBadge + ' ' + dteBadge + ' <span style="font-size:11px;color:var(--muted);">Active Position</span> ' + recBadge + ' ' + mgmtBadge + '</div>'
      + '<div class="trade-subtitle">' + subtitleText + (mgmtSubtitlePart ? ' <span style="font-size:11px;color:rgba(255,255,255,0.5);">' + mgmtSubtitlePart + '</span>' : '') + '</div>'
      + '</div>'
      + '<div class="trade-header-right">' + healthBadge + '</div>'
      + '</div></summary>'
      + '<div class="trade-body" style="flex:1 1 auto;">'
      + enrichment.body
      + '</div>'
      + '</details>'
      + actionsHtml
      + '</div>';
  }

  /**
   * Build enrichment HTML for active trade expanded body.
   * Returns { body, warnings } matching stock/options enrichment pattern.
   */
  function buildActiveEnrichmentHtml(rec) {
    var bodyParts = [];
    var warningParts = [];
    var posSnap = rec.position_snapshot || {};
    var engineSummary = rec.internal_engine_summary || {};
    var engineMetrics = rec.internal_engine_metrics || {};
    var modelSummary = rec.model_summary || {};
    var marketAlignRaw = rec.market_alignment || {};
    var marketAlignLabel = (typeof marketAlignRaw === 'object' ? marketAlignRaw.label : marketAlignRaw) || '--';
    var marketAlignDetail = (typeof marketAlignRaw === 'object' ? marketAlignRaw.detail : marketAlignRaw) || '';
    var isDegraded = rec.is_degraded;
    var degradedReasons = rec.degraded_reasons || [];

    // ── Degradation banner ──
    if (isDegraded && degradedReasons.length > 0) {
      warningParts.push(
        '<div style="margin-bottom:6px;padding:5px 10px;font-size:11px;font-weight:600;color:#ff8a5a;background:rgba(255,138,90,0.08);border:1px solid rgba(255,138,90,0.2);border-radius:5px;text-align:center;">'
        + '\u26A0 Analysis degraded: ' + esc(degradedReasons.slice(0, 3).join(', '))
        + '</div>'
      );
    }

    // ── MANAGEMENT STATUS section (from enrichment) ──
    if (rec.management_status) {
      var mgmtHtml = '<div class="section section-management" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;">';
      // P&L progress bar
      if (rec.profit_target_value != null || rec.stop_loss_value != null) {
        mgmtHtml += renderPnlProgressBar(rec);
      }
      // Management details
      var mgmtDetails = [];
      if (rec.position_snapshot && rec.position_snapshot.avg_open_price != null) {
        var entryLabel = rec.strategy_class === 'equity' ? 'Entry' : (rec.strategy_class === 'income' ? 'Credit' : 'Debit');
        mgmtDetails.push(entryLabel + ': $' + Math.abs(rec.position_snapshot.avg_open_price).toFixed(2));
      }
      if (rec.position_snapshot && rec.position_snapshot.mark_price != null) {
        mgmtDetails.push('Current: $' + Math.abs(rec.position_snapshot.mark_price).toFixed(2));
      }
      if (rec.profit_target_value != null) {
        mgmtDetails.push('Target: $' + rec.profit_target_value.toFixed(2));
      }
      if (rec.stop_loss_value != null) {
        mgmtDetails.push('Stop: $' + rec.stop_loss_value.toFixed(2));
      }
      if (rec.days_held != null) {
        mgmtDetails.push('Held: ' + rec.days_held + ' days');
      }
      if (rec.management_policy) {
        var targetPctLabel = Math.round(rec.management_policy.profit_target_pct * 100) + '% profit target';
        mgmtDetails.push(targetPctLabel);
      }
      if (mgmtDetails.length > 0) {
        mgmtHtml += '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:6px;font-size:11px;color:rgba(255,255,255,0.55);">';
        mgmtDetails.forEach(function (d) {
          mgmtHtml += '<span style="padding:2px 6px;background:rgba(255,255,255,0.04);border-radius:3px;">' + esc(d) + '</span>';
        });
        mgmtHtml += '</div>';
      }
      // Action suggestion
      if (rec.suggested_action) {
        mgmtHtml += renderActionSuggestion(rec.suggested_action);
      }
      mgmtHtml += '</div>';
      bodyParts.push(mgmtHtml);
    }

    // ── POSITION SNAPSHOT section ──
    // Entry price sign convention: positive = credit received, negative = debit paid
    var entryVal = posSnap.avg_open_price;
    var entryText = '--';
    if (entryVal != null) {
      var absEntry = Math.abs(Number(entryVal));
      var entryTag = Number(entryVal) >= 0 ? ' (cr)' : ' (db)';
      entryText = '$' + absEntry.toFixed(2) + entryTag;
    }
    // Current price follows same convention
    var markVal = posSnap.mark_price;
    var markText = '--';
    if (markVal != null) {
      var absMark = Math.abs(Number(markVal));
      var markTag = Number(markVal) >= 0 ? ' (cr)' : ' (db)';
      markText = '$' + absMark.toFixed(2) + markTag;
    }
    // Cost basis: show absolute value
    var costVal = posSnap.cost_basis_total;
    var costText = '--';
    if (costVal != null) {
      costText = '$' + Math.abs(Number(costVal)).toFixed(2);
    }
    // Market value: show absolute value
    var mvVal = posSnap.market_value;
    var mvText = '--';
    if (mvVal != null) {
      mvText = '$' + Math.abs(Number(mvVal)).toFixed(2);
    }
    var snapItems = [
      { label: 'Entry Price', value: entryText },
      { label: 'Current Price', value: markText },
      { label: 'Unrealized P&L', value: posSnap.unrealized_pnl != null ? '$' + Number(posSnap.unrealized_pnl).toFixed(2) : '--',
        cssClass: posSnap.unrealized_pnl != null ? (posSnap.unrealized_pnl >= 0 ? 'positive' : 'negative') : 'neutral' },
      { label: 'P&L %', value: posSnap.unrealized_pnl_pct != null ? (posSnap.unrealized_pnl_pct * 100).toFixed(1) + '%' : '--',
        cssClass: posSnap.unrealized_pnl_pct != null ? (posSnap.unrealized_pnl_pct >= 0 ? 'positive' : 'negative') : 'neutral' },
      { label: 'DTE', value: rec.dte != null ? rec.dte + 'd' : '--' },
      { label: 'Expiration', value: rec.expiration || posSnap.expiration || '--' },
      { label: 'Cost Basis', value: costText },
      { label: 'Market Value', value: mvText },
    ];
    var snapGrid = '<div class="metric-grid">' + snapItems.map(function (item) {
      return '<div class="metric"><div class="metric-label">' + esc(item.label) + '</div><div class="metric-value ' + (item.cssClass || 'neutral') + '">' + item.value + '</div></div>';
    }).join('') + '</div>';

    // Per-leg details
    var legs = posSnap.legs || [];
    var legsHtml = '';
    if (legs.length > 0) {
      var legRows = '';
      legs.forEach(function (leg) {
        var legSide = (leg.side || '').toLowerCase();
        var side = (legSide === 'sell' || legSide === 'short' || legSide === 'sell_to_open' || legSide === 'sell_to_close') ? 'SHORT' : 'LONG';
        var sideClass = side === 'SHORT' ? 'tmc-leg-short' : 'tmc-leg-long';
        var strike = leg.strike != null ? String(leg.strike) : '?';
        var optType = (leg.option_type || leg.type || '').toUpperCase();
        var occSymbol = leg.symbol || '';
        var bidAsk = '';
        if (leg.bid != null && leg.ask != null) {
          bidAsk = Number(leg.bid).toFixed(2) + ' / ' + Number(leg.ask).toFixed(2);
        }
        var delta = leg.delta != null ? '\u0394 ' + Number(leg.delta).toFixed(2) : '';
        legRows +=
          '<div class="tmc-options-leg-row">' +
            '<span class="tmc-leg-side ' + sideClass + '">' + esc(side) + '</span>' +
            '<span class="tmc-leg-strike">' + esc(strike) + ' ' + esc(optType) + '</span>' +
            '<span class="tmc-leg-pricing">' + esc(bidAsk) + '</span>' +
            '<span class="tmc-leg-delta">' + esc(delta) + '</span>' +
          '</div>';
      });
      legsHtml = '<div class="tmc-options-legs" style="margin-top:6px;">' + legRows + '</div>';
    }

    // Live Greeks
    var greeks = rec.live_greeks;
    var greeksHtml = '';
    if (greeks) {
      var gItems = [
        { label: '\u0394 Trade Delta', value: greeks.trade_delta != null ? greeks.trade_delta.toFixed(2) : '--' },
        { label: '\u0398 Trade Theta', value: greeks.trade_theta != null ? '$' + greeks.trade_theta.toFixed(2) : '--' },
        { label: '\u03BD Trade Vega', value: greeks.trade_vega != null ? '$' + greeks.trade_vega.toFixed(2) : '--' },
      ];
      greeksHtml = '<div style="display:flex;gap:12px;margin-top:6px;flex-wrap:wrap;">' + gItems.map(function (g) {
        return '<span style="font-size:11px;color:var(--text-secondary,#bbb);"><span style="color:var(--accent-cyan,#00dcff);font-weight:600;">' + g.label + ':</span> ' + g.value + '</span>';
      }).join('') + '</div>';
      if (greeks.any_refreshed) {
        greeksHtml += '<div style="font-size:9px;color:var(--muted);margin-top:2px;">\u2713 Greeks refreshed from live chain data</div>';
      }
    }

    bodyParts.push(
      '<div class="section section-core"><div class="section-title">POSITION SNAPSHOT</div>'
      + snapGrid + legsHtml + greeksHtml
      + '</div>'
    );

    // ── PROFITABILITY EXPLANATION section ──
    var explanationBlock = buildExplanationHtml(rec);
    if (explanationBlock) {
      bodyParts.push(explanationBlock);
    }

    // ── HEALTH ASSESSMENT section ──
    var healthScore = engineSummary.trade_health_score;
    var healthColor = '#8a8ab4';
    if (healthScore != null) {
      if (healthScore >= 70) healthColor = '#00dc78';
      else if (healthScore >= 45) healthColor = '#ffc83c';
      else healthColor = '#ff5a5a';
    }
    var compKeys = Object.keys(engineMetrics);
    if (healthScore != null || compKeys.length > 0) {
      var healthHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid ' + healthColor + '33;background:' + healthColor + '08;">';
      healthHtml += '<div class="section-title">HEALTH ASSESSMENT';
      if (healthScore != null) {
        healthHtml += ' \u2014 <span style="color:' + healthColor + ';">' + Math.round(healthScore) + '/100</span>';
      }
      healthHtml += '</div>';

      if (compKeys.length > 0) {
        healthHtml += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:4px;">';
        compKeys.forEach(function (k) {
          var v = engineMetrics[k];
          var displayVal = v != null ? Math.round(v) : '--';
          var cColor = '#8a8ab4';
          if (v != null) {
            if (v >= 70) cColor = '#00dc78';
            else if (v >= 45) cColor = '#ffc83c';
            else cColor = '#ff5a5a';
          }
          healthHtml += '<span style="font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid ' + cColor + '33;color:' + cColor + ';background:' + cColor + '08;">'
            + esc(k.replace(/_/g, ' ')) + ': <strong>' + displayVal + '</strong></span>';
        });
        healthHtml += '</div>';
      }

      // Engine recommendation
      if (engineSummary.engine_recommendation) {
        healthHtml += '<div style="margin-top:6px;font-size:11px;color:var(--text-secondary,#bbb);">Engine: <strong>' + esc(engineSummary.engine_recommendation) + '</strong></div>';
      }

      // Risk flags
      var riskFlags = rec.internal_engine_flags || [];
      if (riskFlags.length > 0) {
        healthHtml += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px;">';
        riskFlags.forEach(function (f) {
          healthHtml += '<span class="tmc-risk-flag">' + esc(f) + '</span>';
        });
        healthHtml += '</div>';
      }

      healthHtml += '</div>';
      bodyParts.push(healthHtml);
    }

    // ── MODEL REVIEW section ──
    var hasModel = !!(modelSummary.model_available);
    if (hasModel) {
      var mRec = String(modelSummary.model_recommendation || rec.recommendation || '--').toUpperCase();
      var mConv = modelSummary.model_conviction != null ? fmtPctDirect(modelSummary.model_conviction) : '';
      var mProvider = modelSummary.provider || '';
      var mLatency = modelSummary.latency_ms != null ? modelSummary.latency_ms + 'ms' : '';
      var mHeaderBits = [mRec, mConv ? 'Conf: ' + mConv : '', mProvider, mLatency].filter(Boolean).join(' \u00B7 ');

      var mRecColor = '#b4b4c8';
      if (mRec === 'HOLD') mRecColor = '#00dc78';
      else if (mRec === 'REDUCE') mRecColor = '#ffc83c';
      else if (mRec === 'CLOSE' || mRec === 'URGENT_REVIEW' || mRec === 'URGENT REVIEW') mRecColor = '#ff5a5a';

      var modelBody = '';
      if (rec.rationale_summary) {
        modelBody += '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;margin-bottom:4px;">' + esc(rec.rationale_summary) + '</div>';
      }

      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid ' + mRecColor + '33;background:' + mRecColor + '08;">'
        + '<div class="section-title" style="margin-bottom:6px;">MODEL REVIEW'
        + (mHeaderBits ? ' \u2014 <span style="color:' + mRecColor + ';">' + mHeaderBits + '</span>' : '')
        + '</div>'
        + modelBody
        + '</div>'
      );
    } else {
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid rgba(138,138,180,0.2);background:rgba(138,138,180,0.04);">'
        + '<div class="section-title" style="color:#8a8ab4;">MODEL REVIEW</div>'
        + '<div style="font-size:12px;color:var(--muted);">Model analysis unavailable \u2014 engine-only assessment</div>'
        + '</div>'
      );
    }

    // ── SUPPORTING POINTS section ──
    var points = rec.key_supporting_points || [];
    if (points.length > 0) {
      var pointsLis = points.map(function (p) {
        return '<li style="margin-bottom:2px;">' + esc(p) + '</li>';
      }).join('');
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;">'
        + '<div class="section-title">KEY SUPPORTING POINTS</div>'
        + '<ul style="margin:0;padding-left:16px;font-size:11px;line-height:1.5;">' + pointsLis + '</ul>'
        + '</div>'
      );
    }

    // ── EVENT RISK section ──
    var eventRisk = rec.event_risk;
    if (eventRisk) {
      var erLevel = eventRisk.event_risk_level || 'unknown';
      var erDetails = eventRisk.event_details || [];
      var erColor = '#8a8ab4';
      if (erLevel === 'high' || erLevel === 'critical') erColor = '#ff5a5a';
      else if (erLevel === 'elevated') erColor = '#ffc83c';
      else if (erLevel === 'quiet') erColor = '#00dc78';

      var erHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid ' + erColor + '33;background:' + erColor + '08;">';
      erHtml += '<div class="section-title">EVENT RISK \u2014 <span style="color:' + erColor + ';">' + esc(erLevel.toUpperCase()) + '</span></div>';

      if (erDetails.length > 0) {
        erHtml += '<div style="margin-top:4px;">';
        erDetails.forEach(function (evt) {
          var evtName = (typeof evt === 'string') ? evt : (evt.event || evt.name || evt.title || JSON.stringify(evt));
          var evtDate = (typeof evt === 'object' && evt.date) ? ' (' + evt.date + ')' : '';
          erHtml += '<div style="font-size:11px;color:var(--text-secondary,#bbb);line-height:1.4;padding:2px 0 2px 8px;border-left:2px solid ' + erColor + '44;margin-bottom:3px;">'
            + esc(evtName) + esc(evtDate) + '</div>';
        });
        erHtml += '</div>';
      }
      erHtml += '</div>';
      bodyParts.push(erHtml);
    }

    // ── PORTFOLIO CONTEXT section ──
    var portCtx = rec.portfolio_context;
    if (portCtx) {
      var pcItems = [
        { label: 'Position Risk %', value: portCtx.position_risk_pct != null ? (portCtx.position_risk_pct * 100).toFixed(1) + '%' : '--' },
        { label: 'Underlying Conc.', value: portCtx.underlying_concentration_pct != null ? (portCtx.underlying_concentration_pct * 100).toFixed(1) + '%' : '--' },
        { label: 'Portfolio \u0394', value: portCtx.net_portfolio_delta != null ? portCtx.net_portfolio_delta.toFixed(2) : '--' },
        { label: 'Portfolio \u0398', value: portCtx.net_portfolio_theta != null ? '$' + portCtx.net_portfolio_theta.toFixed(2) : '--' },
        { label: 'Total Positions', value: portCtx.total_positions != null ? String(portCtx.total_positions) : '--' },
      ];

      var pcHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.12);">';
      pcHtml += '<div class="section-title">PORTFOLIO CONTEXT</div>';
      pcHtml += '<div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:4px;">';
      pcItems.forEach(function (item) {
        pcHtml += '<span style="font-size:11px;color:var(--text-secondary,#bbb);"><span style="font-weight:600;color:var(--text,#d7fbff);">' + esc(item.label) + ':</span> ' + item.value + '</span>';
      });
      pcHtml += '</div>';

      if (portCtx.is_portfolio_concentrated) {
        pcHtml += '<div style="margin-top:4px;font-size:10px;color:#ffc83c;">\u26A0 Portfolio concentrated in ' + esc(portCtx.top_concentration_symbol || '?') + '</div>';
      }
      var pfFlags = portCtx.portfolio_risk_flags || [];
      if (pfFlags.length > 0) {
        pcHtml += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;">';
        pfFlags.forEach(function (f) {
          pcHtml += '<span class="tmc-risk-flag">' + esc(f) + '</span>';
        });
        pcHtml += '</div>';
      }
      pcHtml += '</div>';
      bodyParts.push(pcHtml);
    }

    // ── MARKET ALIGNMENT section ──
    if (marketAlignDetail) {
      var maColor = marketAlignLabel === 'Aligned' ? '#00dc78' : marketAlignLabel === 'Unfavorable' ? '#ff5a5a' : '#ffc83c';
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.12);">'
        + '<div class="section-title">MARKET ALIGNMENT \u2014 <span style="color:' + maColor + ';">' + esc(marketAlignLabel.toUpperCase()) + '</span></div>'
        + '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(marketAlignDetail) + '</div>'
        + '</div>'
      );
    }

    // ── CAUTION section (key_risks) ──
    var risks = rec.key_risks || [];
    if (risks.length > 0) {
      var riskLis = risks.map(function (r) {
        return '<li style="margin-bottom:2px;">' + esc(r) + '</li>';
      }).join('');
      bodyParts.push(
        '<div class="section" style="margin-bottom:6px;padding:6px 10px;border-radius:6px;border:1px solid rgba(244,200,95,0.2);background:rgba(244,200,95,0.04);">'
        + '<div class="section-title" style="color:var(--warn,#f4c85f);">CAUTION</div>'
        + '<ul style="margin:0;padding-left:16px;font-size:11px;line-height:1.5;">' + riskLis + '</ul>'
        + '</div>'
      );
    }

    // ── SUGGESTED NEXT MOVE section ──
    var nextMove = rec.suggested_next_move || '';
    if (nextMove) {
      bodyParts.push(
        '<div class="section" style="margin-bottom:6px;padding:6px 10px;border-radius:6px;border:1px solid rgba(0,220,255,0.15);background:rgba(0,220,255,0.03);">'
        + '<div class="section-title" style="color:var(--accent-cyan,#00dcff);">SUGGESTED NEXT MOVE</div>'
        + '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(nextMove) + '</div>'
        + '</div>'
      );
    }

    // ── Model metadata footer ──
    if (modelSummary.model_available) {
      bodyParts.push(
        '<div style="margin-top:4px;font-size:9px;color:var(--muted);display:flex;gap:6px;align-items:center;">'
        + '<span>' + esc(modelSummary.provider || '') + '</span>'
        + '<span>\u00B7</span>'
        + '<span>' + esc(modelSummary.model_name || '') + '</span>'
        + (modelSummary.latency_ms != null ? '<span>\u00B7</span><span>' + modelSummary.latency_ms + 'ms</span>' : '')
        + (rec.recommendation_source ? '<span>\u00B7</span><span>via ' + esc(rec.recommendation_source) + '</span>' : '')
        + '</div>'
      );
    }

    return { body: bodyParts.join(''), warnings: warningParts.join('') };
  }

  /**
   * Handle close/reduce position via the suggested_close_order from the pipeline.
   * Uses /api/trading/preview if available, falls back to TradeTicket.
   */
  function _executeActiveClose(btn, tradeKey, rec) {
    var closeOrder = rec.suggested_close_order;
    if (!closeOrder) {
      console.warn('[TMC] No suggested_close_order for', tradeKey);
      // Fall back to TradeTicket
      _openActiveTradeTicket(rec, 'close');
      return;
    }

    if (api && api.tradingPreview) {
      btn.disabled = true;
      btn.textContent = 'Previewing\u2026';
      api.tradingPreview(closeOrder)
        .then(function (preview) {
          btn.disabled = false;
          btn.textContent = btn.textContent.indexOf('Reduce') >= 0 ? 'Reduce Position' : 'Close Position';
          if (window.BenTradeExecutionModal && window.BenTradeExecutionModal.open) {
            window.BenTradeExecutionModal.open(closeOrder, preview);
          } else {
            console.log('[TMC] Close order preview:', preview);
            alert('Preview: ' + JSON.stringify(preview, null, 2));
          }
        })
        .catch(function (err) {
          btn.disabled = false;
          btn.textContent = btn.textContent.indexOf('Reduce') >= 0 ? 'Reduce Position' : 'Close Position';
          console.error('[TMC] Close order preview failed:', err);
          alert('Preview failed: ' + (err.message || String(err)));
        });
    } else {
      _openActiveTradeTicket(rec, 'close');
    }
  }

  /**
   * Refresh analysis for a single position (re-runs the pipeline for just this symbol).
   */
  function _refreshSinglePosition(btn, rec) {
    var symbol = rec.symbol || '';
    btn.disabled = true;
    btn.textContent = 'Refreshing\u2026';

    var accountMode = _getAccountMode();
    var skipModel = false;
    var cb = document.getElementById('tmcSkipModel');
    if (cb) skipModel = cb.checked;

    var url = '/api/active-trade-pipeline/run?account_mode=' + encodeURIComponent(accountMode) + '&skip_model=' + (skipModel ? 'true' : 'false');
    fetch(url, { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        btn.disabled = false;
        btn.textContent = 'Refresh Analysis';
        if (data.ok === false) {
          alert('Refresh failed: ' + ((data.error || {}).message || 'unknown'));
          return;
        }
        renderActiveResults(data);
      })
      .catch(function (err) {
        btn.disabled = false;
        btn.textContent = 'Refresh Analysis';
        alert('Refresh failed: ' + (err.message || String(err)));
      });
  }

  /**
   * Fallback: open TradeTicket for active position actions.
   */
  function _openActiveTradeTicket(rec, action) {
    var symbol = rec.symbol || '';
    var strategy = rec.strategy || '';
    var posSnap = rec.position_snapshot || {};

    var tradeData = {
      underlying:    symbol,
      symbol:        symbol,
      strategyId:    strategy,
      strategyLabel: (strategy || '').replace(/_/g, ' '),
      quantity:      1,
      orderType:     'limit',
      tif:           'day',
      action:        action,
      recommendation: rec.recommendation,
      conviction:    rec.conviction,
      rationale:     rec.rationale_summary || '',
      nextMove:      rec.suggested_next_move || '',
      expiration:    posSnap.expiration || null,
      dte:           rec.dte || null,
    };

    if (window.TradeTicket && typeof window.TradeTicket.open === 'function') {
      window.TradeTicket.open(tradeData);
    } else {
      console.warn('[TMC] TradeTicket not available -- trade data:', tradeData);
      alert('Trade Ticket module not loaded. Trade data logged to console.');
    }
  }

  /* =================================================================
   *  FULL REFRESH -- chains Stock → Options → Active → Balance
   * ================================================================= */

  var _fullRefreshStages = [
    { label: 'Stock Scan',        step: 1 },
    { label: 'Options Scan',      step: 2 },
    { label: 'Active Trades',     step: 3 },
    { label: 'Portfolio Balance',  step: 4 },
  ];

  function _setRefreshStatus(msg, step, total) {
    var el = document.getElementById('tmcRefreshStatus');
    if (!el) return;
    if (!msg) { el.style.display = 'none'; el.textContent = ''; return; }
    el.style.display = 'inline';
    el.textContent = (step && total) ? ('(' + step + '/' + total + ') ' + msg) : msg;
  }

  function _setFullRefreshEnabled(running) {
    _fullRefreshRunning = running;
    var btn = document.getElementById('tmcFullRefreshBtn');
    if (btn) {
      btn.disabled = running;
      btn.textContent = running ? '⟳ Running…' : '⟳ Full Refresh';
    }
  }

  async function handleFullRefresh() {
    if (_fullRefreshRunning) return;
    _setFullRefreshEnabled(true);
    _manualRefreshInProgress = true;

    var skipModel = false;
    var cb = document.getElementById('tmcSkipModel');
    if (cb) skipModel = cb.checked;
    var accountMode = _getAccountMode();
    var t0 = Date.now();

    var chainResults = {};

    console.log('[TMC:DIAG] ══════════════════════════════════════════════');
    console.log('[TMC:DIAG] Full Refresh STARTED', { accountMode: accountMode, skipModel: skipModel, time: new Date().toISOString() });

    try {
      // Stages 1-3: Run in PARALLEL, display results PROGRESSIVELY as each arrives.
      // Previously Promise.allSettled blocked all display until the slowest (active trades)
      // resolved - causing stock/options to appear stuck when active trades took 2+ min.
      _setRefreshStatus('Running Stock, Options & Active Trade analysis…', 1, 2);

      console.log('[TMC:DIAG] Dispatching 3 parallel promises (stock, options, active)…');

      // -- Stock: trigger scan → fetch latest → display immediately --
      var stockP = api.tmcRunStock()
        .then(function (res) { chainResults.stockTrigger = res; return api.tmcGetLatestStock(); })
        .then(function (val) {
          chainResults.stockResults = val;
          console.log('[TMC:DIAG] Stock ARRIVED — displaying now');
          loadStockOpportunities();
          return val;
        })
        .catch(function (err) {
          console.warn('[TMC:DIAG] Stock scan REJECTED:', err);
          return null;
        });

      // -- Options: trigger scan → fetch latest → display immediately --
      var optionsP = api.tmcRunOptions()
        .then(function (res) { chainResults.optionsTrigger = res; return api.tmcGetLatestOptions(); })
        .then(function (val) {
          chainResults.optionsResults = val;
          console.log('[TMC:DIAG] Options ARRIVED — displaying now');
          loadOptionsOpportunities();
          return val;
        })
        .catch(function (err) {
          console.warn('[TMC:DIAG] Options scan REJECTED:', err);
          return null;
        });

      // -- Active trades: uses modelFetch (185s timeout matching backend model timeout) --
      var activeP = api.runActiveTradesPipeline({
          account_mode: accountMode,
          skip_model: skipModel,
        })
        .then(function (data) {
          chainResults.activeResults = data;
          if (data && data.ok === false) {
            var errMsg = (data.error || {}).message || 'Pipeline returned an error';
            console.error('[TMC:DIAG] Active trade pipeline returned ok:false —', errMsg, data.error);
            showActiveError(errMsg, data);
          } else {
            console.log('[TMC:DIAG] Active trade ARRIVED — displaying', (data.recommendations || []).length, 'recommendations');
            try {
              renderActiveResults(data);
            } catch (renderErr) {
              console.error('[TMC:DIAG] renderActiveResults THREW:', renderErr);
              showActiveError('Render error: ' + renderErr.message, data);
            }
          }
          _lastManualActiveRenderAt = Date.now();
          return data;
        })
        .catch(function (err) {
          if (err.name === 'AbortError') {
            console.error('[TMC:DIAG] Active trade TIMED OUT (185s model timeout)');
            showActiveError('Active trade analysis timed out — try "Analyze Positions" standalone', null);
          } else {
            console.error('[TMC:DIAG] Active trade promise REJECTED:', err);
            showActiveError('Request failed: ' + (err.message || String(err)), null);
          }
          _lastManualActiveRenderAt = Date.now();
          return null;
        });

      // Wait for all 3 to settle before proceeding to portfolio balance
      var results = await Promise.allSettled([stockP, optionsP, activeP]);
      var stockResult   = results[0];
      var optionsResult = results[1];
      var activeResult  = results[2];

      console.log('[TMC:DIAG] Promise.allSettled resolved:', {
        elapsed: ((Date.now() - t0) / 1000).toFixed(1) + 's',
        stock: stockResult.status,
        options: optionsResult.status,
        active: activeResult.status,
      });

      // Results already displayed progressively above.
      // Determine what we have for portfolio balance.
      var activeOk = activeResult.status === 'fulfilled' && activeResult.value && activeResult.value.ok !== false;

      // Stage 4: Portfolio Balance (needs results from stages 1-3)
      _setRefreshStatus('Building portfolio balance…', 2, 2);
      var balancePayload = {
        account_mode: accountMode,
        skip_model: true,
        // Pass pre-computed results to avoid pipeline re-runs
        active_trade_results: activeOk ? activeResult.value : { recommendations: [], ok: true, _provided: true },
        stock_results: stockResult.status === 'fulfilled' && stockResult.value ? stockResult.value.data || null : null,
        options_results: optionsResult.status === 'fulfilled' && optionsResult.value ? optionsResult.value.data || null : null,
      };
      console.log('[TMC:DIAG] Starting portfolio balance…', {
        activeOk: activeOk,
        hasStockResults: balancePayload.stock_results != null,
        hasOptionsResults: balancePayload.options_results != null,
        hasActiveResults: balancePayload.active_trade_results != null,
      });
      try {
        var balanceData = await api.tmcRunPortfolioBalance(balancePayload);
        chainResults.balanceResults = balanceData;
        console.log('[TMC:DIAG] Portfolio balance response:', {
          ok: balanceData.ok,
          hasPlan: !!(balanceData && balanceData.rebalance_plan),
          planKeys: balanceData && balanceData.rebalance_plan ? Object.keys(balanceData.rebalance_plan) : [],
          errors: balanceData.errors || [],
        });
        try {
          displayPortfolioBalance(balanceData);
          console.log('[TMC:DIAG] displayPortfolioBalance completed successfully');
        } catch (displayErr) {
          console.error('[TMC:DIAG] displayPortfolioBalance THREW:', displayErr);
          showBalanceError('Render error: ' + displayErr.message, balanceData);
        }
      } catch (balanceErr) {
        console.error('[TMC:DIAG] Portfolio balance request FAILED:', balanceErr);
        showBalanceError('Request failed: ' + (balanceErr.message || String(balanceErr)), balanceErr.payload || null);
      }

      var totalElapsed = ((Date.now() - t0) / 1000).toFixed(1);
      _setRefreshStatus('Complete (' + totalElapsed + 's)', null, null);
      setTimeout(function () { _setRefreshStatus(null); }, 4000);
      console.log('[TMC:DIAG] Full Refresh COMPLETE (' + totalElapsed + 's)');
      console.log('[TMC:DIAG] ══════════════════════════════════════════════');
    } catch (err) {
      console.error('[TMC:DIAG] Full Refresh OUTER CATCH — unexpected error:', err);
      console.error('[TMC:DIAG] This means code between Promise.allSettled and portfolio balance threw!');
      _setRefreshStatus('Failed: ' + err.message, null, null);
      setTimeout(function () { _setRefreshStatus(null); }, 8000);
      // Still try to show portfolio balance error so the section doesn't stay as default HTML
      showBalanceError('Full Refresh failed before portfolio balance could run: ' + err.message, null);
    } finally {
      _manualRefreshInProgress = false;
      _setFullRefreshEnabled(false);
    }
  }

  /* -- Portfolio Balance rendering ----------------------------------- */

  /** Store last balance result for close-button binding */
  var _lastBalanceResult = null;

  /** Standalone portfolio rebalance run (not part of Full Refresh) */
  async function runPortfolioRebalance() {
    var btn = document.getElementById('tmcRunBalanceBtn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Running…'; }
    var section = document.getElementById('tmcPortfolioBalanceSection');
    if (section) section.style.display = '';
    var t0 = Date.now();
    console.log('[TMC] Portfolio rebalance started (standalone)');
    try {
      // When running standalone (not inside Full Refresh), we don't have
      // chainResults.  Pass null — the backend fetches cached results or
      // produces a basic rebalance plan from account data alone.
      var balanceData = await api.tmcRunPortfolioBalance({
        account_mode: _getAccountMode(),
        skip_model: true,
        active_trade_results: null,
      });
      var elapsed = ((Date.now() - t0) / 1000).toFixed(1);
      console.log('[TMC] Portfolio rebalance response (standalone):', {
        elapsed: elapsed + 's', ok: balanceData.ok,
        hasPlan: !!(balanceData && balanceData.rebalance_plan),
        errors: balanceData.errors || [],
      });
      displayPortfolioBalance(balanceData);
    } catch (err) {
      var elapsed = ((Date.now() - t0) / 1000).toFixed(1);
      console.error('[TMC] Portfolio rebalance failed (standalone):', elapsed + 's', err);
      if (err.name === 'AbortError') {
        showBalanceError('Portfolio balance timed out after ' + elapsed + 's — check server logs for PB_DEBUG', null);
      } else {
        showBalanceError('Request failed (' + elapsed + 's): ' + (err.message || String(err)), err.payload || null);
      }
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '▶ Run Rebalance'; }
    }
  }

  function displayPortfolioBalance(data) {
    console.log('[TMC:DIAG] displayPortfolioBalance called:', {
      ok: data && data.ok, hasPlan: !!(data && data.rebalance_plan),
      errors: data && data.errors ? data.errors.length : 0,
    });
    var section = document.getElementById('tmcPortfolioBalanceSection');
    var grid    = document.getElementById('tmcPortfolioBalanceGrid');
    var badge   = document.getElementById('tmcBalanceStatus');
    if (!section || !grid) {
      console.error('[TMC:DIAG] displayPortfolioBalance ABORT — missing DOM elements:', { section: !!section, grid: !!grid });
      return;
    }
    section.style.display = '';

    _lastBalanceResult = data;

    var tsEl = document.getElementById('tmcBalanceTimestamp');
    if (tsEl) {
      var ts = data && (data.generated_at || data.timestamp);
      var display = ts ? new Date(ts).toLocaleTimeString() : new Date().toLocaleTimeString();
      tsEl.textContent = 'Last updated: ' + display;
    }

    // Guard: check for ok:false error envelope
    if (data && data.ok === false) {
      var errList = (data.errors && data.errors.length)
        ? data.errors.map(function(e){ return '<li>' + esc(String(e)) + '</li>'; }).join('')
        : '';
      var errHtml = errList ? '<ul style="text-align:left;margin-top:.5rem;">' + errList + '</ul>' : '';
      var errMsg = (data.error || {}).message || '';
      if (errMsg) errHtml = '<div style="margin-top:.5rem;">' + esc(errMsg) + '</div>' + errHtml;
      console.error('[TMC] displayPortfolioBalance received ok:false', data.errors || data.error);
      grid.innerHTML = '<div class="tmc-empty-state"><div class="tmc-empty-icon">&#9888;</div>' +
        '<div class="tmc-empty-text">Rebalance failed' + errHtml + '</div></div>';
      if (badge) { badge.textContent = 'ERR'; badge.className = 'tmc-count-badge tmc-count-error'; }
      return;
    }

    var plan = data && data.rebalance_plan ? data.rebalance_plan : null;
    if (!plan) {
      var errList = (data && data.errors && data.errors.length)
        ? data.errors.map(function(e){ return '<li>' + esc(String(e)) + '</li>'; }).join('')
        : '';
      var errHtml = errList ? '<ul style="text-align:left;margin-top:.5rem;">' + errList + '</ul>' : '';
      grid.innerHTML = '<div class="tmc-empty-state"><div class="tmc-empty-icon">&#9888;</div>' +
        '<div class="tmc-empty-text">No rebalance plan produced' + errHtml + '</div></div>';
      return;
    }

    var closes  = plan.close_actions  || [];
    var holds   = plan.hold_positions || [];
    var opens   = plan.open_actions   || [];
    var skips   = plan.skip_actions   || [];
    var impact  = plan.net_impact     || {};
    var postAdj = plan.post_adjustment_state || {};
    var policy  = data.risk_policy || plan.risk_policy_used || {};

    // Separate CLOSE vs REDUCE within close_actions
    var closeOnly = [];
    var reduceOnly = [];
    closes.forEach(function (a) {
      if (a.action === 'REDUCE') { reduceOnly.push(a); }
      else { closeOnly.push(a); }
    });

    var actionCount = closes.length + opens.length;
    if (badge) {
      badge.textContent = actionCount > 0 ? actionCount + ' actions' : 'balanced';
      badge.className = 'tmc-count-badge' + (actionCount > 0 ? ' tmc-count-live' : ' tmc-count-muted');
    }

    var html = '';

    // ──────────────── 1. SUMMARY BAR ────────────────
    html += '<div class="tmc-pb-summary-bar">';
    html += '<div class="tmc-pb-summary-row">';
    html += '<span class="tmc-pb-summary-item"><span class="tmc-pb-label">Equity</span> <span class="tmc-pb-val">$' + _fmtNum(data.account_equity) + '</span></span>';
    html += '<span class="tmc-pb-summary-item"><span class="tmc-pb-label">Regime</span> <span class="tmc-pb-val tmc-pb-regime">' + esc(data.regime_label || 'Unknown') + '</span></span>';
    html += '<span class="tmc-pb-summary-item"><span class="tmc-pb-label">Risk</span> <span class="tmc-pb-val">$' + _fmtNum(impact.risk_before) + ' → $' + _fmtNum(impact.risk_after_opens) + '</span></span>';
    html += '<span class="tmc-pb-summary-item"><span class="tmc-pb-label">Delta</span> <span class="tmc-pb-val">' + _fmtDec(impact.delta_before, 2) + ' → ' + _fmtDec(impact.delta_after, 2) + '</span></span>';
    html += '<span class="tmc-pb-summary-item"><span class="tmc-pb-label">Trades</span> <span class="tmc-pb-val">' + _fmtInt(impact.trades_before) + ' → ' + _fmtInt(impact.trades_after) + '</span></span>';
    html += '</div>';
    html += '<div class="tmc-pb-change-strip">';
    html += 'Close ' + _fmtInt(impact.positions_closed) + ', ';
    html += 'Reduce ' + _fmtInt(impact.positions_reduced) + ', ';
    html += 'Hold ' + _fmtInt(impact.positions_held) + ', ';
    html += 'Open ' + _fmtInt(impact.positions_opened) + ' new, ';
    html += 'Skip ' + _fmtInt(impact.positions_skipped);
    if (impact.risk_budget_remaining != null) {
      html += ' | Budget remaining: $' + _fmtNum(impact.risk_budget_remaining);
    }
    html += '</div>';
    html += '</div>';

    // ──────────────── 2. CLOSE / REDUCE ACTIONS ────────────────
    if (closeOnly.length || reduceOnly.length) {
      html += '<div class="tmc-pb-group tmc-pb-group-close">';
      html += '<h4 class="tmc-pb-group-title">Close / Reduce';
      html += ' <span class="tmc-pb-group-count">' + (closeOnly.length + reduceOnly.length) + '</span></h4>';
      closeOnly.forEach(function (a, i) {
        html += _renderCloseAction(a, i);
      });
      reduceOnly.forEach(function (a, i) {
        html += _renderCloseAction(a, closeOnly.length + i);
      });
      html += '</div>';
    }

    // ──────────────── 3. HOLD POSITIONS ────────────────
    if (holds.length) {
      html += '<div class="tmc-pb-group tmc-pb-group-hold">';
      html += '<h4 class="tmc-pb-group-title">Hold';
      html += ' <span class="tmc-pb-group-count">' + holds.length + '</span></h4>';
      html += '<div class="tmc-pb-hold-grid">';
      holds.forEach(function (pos) {
        html += '<span class="tmc-pb-hold-badge">';
        html += '<span class="tmc-pb-hold-sym">' + esc(pos.symbol) + '</span> ';
        html += '<span class="tmc-pb-hold-strat">' + esc((pos.strategy || '').replace(/_/g, ' ')) + '</span>';
        if (pos.trade_health_score != null) {
          html += ' <span class="tmc-pb-hold-health">health ' + Math.round(pos.trade_health_score) + '</span>';
        }
        html += '</span>';
      });
      html += '</div>';
      html += '</div>';
    }

    // ──────────────── 4. OPEN SUGGESTIONS ────────────────
    if (opens.length) {
      html += '<div class="tmc-pb-group tmc-pb-group-open">';
      html += '<h4 class="tmc-pb-group-title">Suggested New Trades';
      html += ' <span class="tmc-pb-group-count">' + opens.length + '</span></h4>';
      opens.forEach(function (a, i) {
        html += _renderOpenAction(a, i);
      });
      html += '</div>';
    }

    // ──────────────── 5. SKIPPED CANDIDATES (collapsible) ────────────────
    if (skips.length) {
      html += '<details class="tmc-pb-collapsible">';
      html += '<summary class="tmc-pb-collapsible-summary">' + skips.length + ' candidates skipped</summary>';
      html += '<div class="tmc-pb-skip-list">';
      skips.forEach(function (s) {
        html += '<div class="tmc-pb-skip-item">';
        html += '<span class="tmc-pb-skip-sym">' + esc(s.symbol) + '</span> ';
        html += '<span class="tmc-pb-skip-strat">' + esc((s.strategy || '').replace(/_/g, ' ')) + '</span>';
        html += '<span class="tmc-pb-skip-source">' + esc(s.source || '') + '</span>';
        html += '<span class="tmc-pb-skip-reason">' + esc(s.skip_reason) + '</span>';
        html += '</div>';
      });
      html += '</div>';
      html += '</details>';
    }

    // ──────────────── 6. RISK POLICY SUMMARY (collapsible) ────────────────
    html += '<details class="tmc-pb-collapsible">';
    html += '<summary class="tmc-pb-collapsible-summary">Risk Policy Details</summary>';
    html += '<div class="tmc-pb-policy-grid">';
    html += _policyRow('Max risk / trade', '$' + _fmtNum(policy.max_risk_per_trade));
    html += _policyRow('Max total risk', '$' + _fmtNum(policy.max_risk_total));
    html += _policyRow('Max concurrent trades', _fmtInt(policy.max_concurrent_trades));
    html += _policyRow('Regime', esc(policy.regime_label || data.regime_label || '—'));
    html += _policyRow('Regime multiplier', (policy.regime_multiplier != null ? policy.regime_multiplier + 'x' : '—'));
    html += _policyRow('Suggested max contracts', _fmtInt(policy.suggested_max_contracts));
    if (postAdj.risk_used != null) {
      html += _policyRow('Risk used (post-close)', '$' + _fmtNum(postAdj.risk_used));
      html += _policyRow('Risk budget available', '$' + _fmtNum(postAdj.risk_budget_available));
      html += _policyRow('Open slots', _fmtInt(postAdj.open_slots));
      html += _policyRow('Max risk / new trade', '$' + _fmtNum(postAdj.max_risk_per_new_trade));
    }
    html += '</div>';
    html += '</details>';

    // Balanced state — no actions
    if (closes.length === 0 && opens.length === 0 && holds.length === 0) {
      html += '<div class="tmc-empty-state"><div class="tmc-empty-icon">&#9989;</div>' +
        '<div class="tmc-empty-text">No rebalancing actions needed — all positions are healthy ' +
        'and no new candidates meet the risk policy constraints.</div></div>';
    } else if (closes.length === 0 && opens.length === 0 && holds.length > 0) {
      html += '<div class="tmc-empty-state"><div class="tmc-empty-icon">&#9989;</div>' +
        '<div class="tmc-empty-text">All ' + holds.length + ' positions healthy — no changes needed</div></div>';
    }

    grid.innerHTML = html;

    // Bind close buttons
    _bindCloseButtons(grid);
    // Bind preview buttons
    _bindPreviewButtons(grid);
  }

  /* -- Close action card ---------------------------------------------- */

  function _renderCloseAction(a, idx) {
    var isClose = a.action !== 'REDUCE';
    var typeClass = isClose ? 'close' : 'reduce';
    var labelText = isClose ? 'CLOSE' : 'REDUCE';

    var card = '<div class="tmc-balance-action-card tmc-balance-action-' + typeClass + '">';
    card += '<div class="tmc-balance-action-header">';
    card += '<span class="tmc-balance-action-label tmc-balance-label-' + typeClass + '">' + labelText + '</span>';
    card += '<span class="tmc-balance-action-symbol">' + esc(a.symbol || '—') + '</span>';
    if (a.strategy) {
      card += '<span class="tmc-balance-action-strategy">' + esc((a.strategy || '').replace(/_/g, ' ')) + '</span>';
    }
    card += '</div>';
    if (a.reason) {
      card += '<div class="tmc-balance-action-reason">' + esc(a.reason) + '</div>';
    }
    card += '<div class="tmc-balance-action-metrics">';
    card += '<span>Risk freed: $' + _fmtNum(a.risk_freed) + '</span>';
    if (a.delta_freed != null) {
      card += '<span>Delta freed: ' + _fmtDec(a.delta_freed, 4) + '</span>';
    }
    if (a.conviction != null) {
      card += '<span>Conviction: ' + Math.round(a.conviction) + '</span>';
    }
    if (a.trade_health_score != null) {
      card += '<span>Health: ' + Math.round(a.trade_health_score) + '</span>';
    }
    card += '</div>';
    // Close button — reuses executeActivePosition flow
    card += '<div class="tmc-balance-action-footer">';
    card += '<button class="btn tmc-btn tmc-btn-close tmc-pb-close-btn" '
      + 'data-close-idx="' + idx + '" '
      + 'data-symbol="' + esc(a.symbol || '') + '" '
      + 'data-strategy="' + esc(a.strategy || '') + '"'
      + '>Execute ' + labelText + '</button>';
    card += '</div>';
    card += '</div>';
    return card;
  }

  /* -- Open suggestion card ------------------------------------------- */

  function _renderOpenAction(a, idx) {
    var cand = a.candidate_data || {};
    var alignClass = a.regime_alignment === 'aligned' ? 'aligned' :
                     a.regime_alignment === 'neutral' ? 'neutral' : 'misaligned';

    var card = '<div class="tmc-balance-action-card tmc-balance-action-open">';
    card += '<div class="tmc-balance-action-header">';
    card += '<span class="tmc-balance-action-label tmc-balance-label-open">OPEN</span>';
    card += '<span class="tmc-balance-action-symbol">' + esc(a.symbol || '—') + '</span>';
    if (a.strategy) {
      card += '<span class="tmc-balance-action-strategy">' + esc((a.strategy || '').replace(/_/g, ' ')) + '</span>';
    }
    card += '<span class="tmc-pb-source-badge">' + esc(a.source || '') + '</span>';
    card += '<span class="tmc-pb-align-badge tmc-pb-align-' + alignClass + '">' + esc(a.regime_alignment || '') + '</span>';
    card += '</div>';
    card += '<div class="tmc-balance-action-metrics">';
    card += '<span>' + (a.contracts || 1) + 'x</span>';
    card += '<span>Risk: $' + _fmtNum(a.max_loss) + '</span>';
    if (a.ev != null) {
      card += '<span>EV: $' + _fmtDec(a.ev, 0) + '</span>';
    }
    if (a.ror != null) {
      card += '<span>RoR: ' + _fmtDec(a.ror * 100, 1) + '%</span>';
    }
    if (a.delta_impact != null) {
      card += '<span>Delta: ' + _fmtDec(a.delta_impact, 4) + '</span>';
    }
    card += '</div>';
    // Extra candidate detail line
    if (cand.scanner_key || cand.dte || cand.pop != null) {
      card += '<div class="tmc-balance-action-sub">';
      if (cand.scanner_key) card += '<span>' + esc(cand.scanner_key.replace(/_/g, ' ')) + '</span>';
      if (cand.dte != null)  card += '<span>DTE ' + cand.dte + '</span>';
      if (cand.pop != null)  card += '<span>PoP ' + _fmtDec(cand.pop * 100, 0) + '%</span>';
      if (cand.event_risk)   card += '<span class="tmc-pb-event-risk">' + esc(cand.event_risk) + '</span>';
      card += '</div>';
    }
    card += '<div class="tmc-balance-action-footer">';
    card += '<button class="btn tmc-btn tmc-btn-execute tmc-pb-preview-btn" '
      + 'data-preview-idx="' + idx + '" '
      + 'data-symbol="' + esc(a.symbol || '') + '" '
      + 'data-strategy="' + esc(a.strategy || '') + '"'
      + '>Preview Trade</button>';
    card += '</div>';
    card += '</div>';
    return card;
  }

  /* -- Policy row helper ---------------------------------------------- */

  function _policyRow(label, value) {
    return '<div class="tmc-pb-policy-row"><span>' + label + '</span><span>' + value + '</span></div>';
  }

  /* -- Bind close buttons to executeActivePosition -------------------- */

  function _bindCloseButtons(container) {
    var buttons = container.querySelectorAll('.tmc-pb-close-btn');
    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var idx = parseInt(btn.dataset.closeIdx, 10);
        if (!_lastBalanceResult || !_lastBalanceResult.rebalance_plan) return;
        var closes = _lastBalanceResult.rebalance_plan.close_actions || [];
        var action = closes[idx];
        if (!action) return;
        // Build a rec shape compatible with executeActivePosition
        var rec = {
          symbol: action.symbol,
          strategy: action.strategy,
          recommendation: action.action,
          conviction: action.conviction,
          rationale_summary: action.reason || '',
          suggested_next_move: '',
          dte: null,
          position_snapshot: {},
        };
        // If close_order is present, attach it
        if (action.close_order) {
          rec.close_order = action.close_order;
        }
        executeActivePosition(rec, 'close');
      });
    });
  }

  /* -- Bind preview buttons to TradeTicket ---------------------------- */

  function _bindPreviewButtons(container) {
    var buttons = container.querySelectorAll('.tmc-pb-preview-btn');
    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var idx = parseInt(btn.dataset.previewIdx, 10);
        if (!_lastBalanceResult || !_lastBalanceResult.rebalance_plan) return;
        var opens = _lastBalanceResult.rebalance_plan.open_actions || [];
        var action = opens[idx];
        if (!action) return;
        var tradeData = {
          underlying: action.symbol,
          symbol: action.symbol,
          strategyId: action.strategy,
          strategyLabel: (action.strategy || '').replace(/_/g, ' '),
          quantity: action.contracts || 1,
          orderType: 'limit',
          tif: 'day',
          action: 'execute',
          source: action.source || 'portfolio_balance',
        };
        if (window.TradeTicket && typeof window.TradeTicket.open === 'function') {
          window.TradeTicket.open(tradeData);
        } else {
          console.warn('[TMC] TradeTicket not available -- trade data:', tradeData);
          alert('Trade Ticket module not loaded. Trade data logged to console.');
        }
      });
    });
  }

  /* -- Formatting helpers --------------------------------------------- */

  function _fmtNum(v) {
    if (v == null) return '—';
    var n = Number(v);
    if (isNaN(n)) return '—';
    return n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }

  function _fmtDec(v, decimals) {
    if (v == null) return '—';
    var n = Number(v);
    if (isNaN(n)) return '—';
    return n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }

  function _fmtInt(v) {
    if (v == null) return '—';
    return String(Math.round(Number(v)));
  }

  /* -- Page init ----------------------------------------------------- */

  function initTradeManagementCenter(viewEl) {
    if (!viewEl) return;

    // Workflow trigger buttons
    var runStockBtn     = document.getElementById('tmcRunStock');
    var runOptionsBtn   = document.getElementById('tmcRunOptions');
    var refreshBtn      = document.getElementById('tmcRefreshBtn');
    var fullRefreshBtn  = document.getElementById('tmcFullRefreshBtn');

    if (runStockBtn) {
      runStockBtn.addEventListener('click', function () { triggerStockRun(); });
    }
    if (runOptionsBtn) {
      runOptionsBtn.addEventListener('click', function () { triggerOptionsRun(); });
    }
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        loadStockOpportunities();
        loadOptionsOpportunities();
      });
    }
    if (fullRefreshBtn) {
      fullRefreshBtn.addEventListener('click', function () { handleFullRefresh(); });
    }

    // Reset Model Providers (circuit breaker)
    var resetProvidersBtn = document.getElementById('tmcResetProvidersBtn');
    if (resetProvidersBtn) {
      resetProvidersBtn.addEventListener('click', function () {
        resetProvidersBtn.disabled = true;
        resetProvidersBtn.textContent = '⚡ Resetting…';
        api.resetCircuitBreaker()
          .then(function (res) {
            console.log('[TMC] Circuit breaker reset:', res);
            resetProvidersBtn.textContent = '✓ Providers Reset';
            setTimeout(function () {
              resetProvidersBtn.textContent = '⚡ Reset Providers';
              resetProvidersBtn.disabled = false;
            }, 3000);
          })
          .catch(function (err) {
            console.error('[TMC] Circuit breaker reset failed:', err);
            resetProvidersBtn.textContent = '✗ Reset Failed';
            setTimeout(function () {
              resetProvidersBtn.textContent = '⚡ Reset Providers';
              resetProvidersBtn.disabled = false;
            }, 3000);
          });
      });
    }

    // Account mode toggle
    var accountToggle = document.getElementById('tmcAccountToggle');
    if (accountToggle) {
      accountToggle.querySelectorAll('.active-account-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
          accountToggle.querySelectorAll('.active-account-btn').forEach(function (b) {
            b.classList.remove('is-active');
          });
          btn.classList.add('is-active');
          _tmcAccountMode = btn.dataset.mode || 'paper';
        });
      });
    }

    // Active trade controls
    var runActiveBtn     = document.getElementById('tmcRunActiveBtn');
    var refreshActiveBtn = document.getElementById('tmcRefreshActiveBtn');
    var runBalanceBtn    = document.getElementById('tmcRunBalanceBtn');

    if (runActiveBtn) {
      runActiveBtn.addEventListener('click', function () { runActivePipeline(); });
    }
    if (refreshActiveBtn) {
      refreshActiveBtn.addEventListener('click', function () { loadLatestActiveResults({ force: true }); });
    }
    if (runBalanceBtn) {
      runBalanceBtn.addEventListener('click', function () { runPortfolioRebalance(); });
    }

    // ── Render cached data immediately (instant re-display on SPA nav) ──
    if (_cachedStockResp) {
      try {
        var stockGrid = document.getElementById('tmcStockGrid');
        var stockCountEl = document.getElementById('tmcStockCount');
        var stockQualEl = document.getElementById('tmcStockQuality');
        var stockFreshEl = document.getElementById('tmcStockFreshness');
        updateFreshness(stockFreshEl, _stockGeneratedAt);
        var sr = handleWorkflowResponse(_cachedStockResp, stockGrid, stockCountEl, stockQualEl, null, 'stock');
        if (sr) renderStockCandidates(stockGrid, sr.candidates, sr.data);
        _showRefreshingBadge('stock');
      } catch (_e) { console.warn('[TMC] Cached stock render failed:', _e); }
    }
    if (_cachedOptionsResp) {
      try {
        var optGrid = document.getElementById('tmcOptionsGrid');
        var optCountEl = document.getElementById('tmcOptionsCount');
        var optQualEl = document.getElementById('tmcOptionsQuality');
        var optStatusEl = document.getElementById('tmcOptionsStatus');
        var optBatchEl = document.getElementById('tmcOptionsBatchStatus');
        var optFreshEl = document.getElementById('tmcOptionsFreshness');
        updateFreshness(optFreshEl, _optionsGeneratedAt);
        updateBatchStatusBadge(optBatchEl, _cachedOptionsResp.data ? _cachedOptionsResp.data.batch_status : null);
        var or = handleWorkflowResponse(_cachedOptionsResp, optGrid, optCountEl, optQualEl, optStatusEl, 'options');
        if (or) renderOptionsCandidates(optGrid, or.candidates, or.data);
        _showRefreshingBadge('options');
      } catch (_e) { console.warn('[TMC] Cached options render failed:', _e); }
    }
    if (_cachedActiveData) {
      try {
        renderActiveResults(_cachedActiveData);
        _showRefreshingBadge('active');
      } catch (_e) { console.warn('[TMC] Cached active render failed:', _e); }
    }

    // Load latest workflow outputs on page entry (background refresh)
    loadStockOpportunities();
    loadOptionsOpportunities();
    loadLatestActiveResults();
    if (_lastBalanceResult) displayPortfolioBalance(_lastBalanceResult);

    // ── Periodic freshness-label refresh (every 15s) ──
    _freshnessTimer = setInterval(function () {
      if (_stockGeneratedAt) updateFreshness(document.getElementById('tmcStockFreshness'), _stockGeneratedAt);
      if (_optionsGeneratedAt) updateFreshness(document.getElementById('tmcOptionsFreshness'), _optionsGeneratedAt);
      if (_activeGeneratedAt) updateFreshness(document.getElementById('tmcActiveTimestamp'), _activeGeneratedAt);
    }, 15000);

    // ── Orchestrator status indicator + auto-refresh ──
    var _orchLastCycle = null;
    var _orchPollTimer = setInterval(function () {
      if (!api.getOrchestratorStatus) return;
      api.getOrchestratorStatus().then(function (status) {
        _updateOrchestratorIndicator(status);
        // Auto-refresh TMC displays when a new cycle completes
        if (status.last_cycle_completed && status.last_cycle_completed !== _orchLastCycle) {
          _orchLastCycle = status.last_cycle_completed;
          // Only refresh if not currently running a manual full refresh
          if (!_fullRefreshRunning) {
            console.log('[TMC:DIAG] Orchestrator cycle complete — refreshing displays (loadLatestActiveResults will check guard)');
            loadStockOpportunities();
            loadOptionsOpportunities();
            loadLatestActiveResults();  // Guard will skip if within manual render window
          } else {
            console.log('[TMC:DIAG] Orchestrator cycle complete — SKIPPED refresh (Full Refresh running)');
          }
        }
      }).catch(function () {
        // Orchestrator may not be running — show idle
        _updateOrchestratorIndicator({ running: false, current_stage: 'idle', cycle_count: 0 });
      });
    }, 5000);

    // Cleanup handler for SPA navigation
    window.BenTradeActiveViewCleanup = function () {
      if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
      if (_orchPollTimer) { clearInterval(_orchPollTimer); _orchPollTimer = null; }
      if (_freshnessTimer) { clearInterval(_freshnessTimer); _freshnessTimer = null; }
      _stopStockCompletionPoll();
      _stopOptionsCompletionPoll();
      _activeRunning = false;
      var metaEl = document.getElementById('tmcActiveRunMeta');
      if (metaEl) metaEl.remove();
    };
  }

  /* -- Orchestrator status display ----------------------------------- */

  var _ORCH_STAGE_LABELS = {
    'idle': 'Idle',
    'stopped': 'Stopped',
    'paused': 'Paused',
    'market_intelligence': 'MI Running',
    'tmc_stock_options_active': 'Stock/Options/Active',
    'tmc_portfolio_balance': 'Portfolio Balance',
    'delay': 'Delay',
    'error_cooldown': 'Error (retrying)',
    'market_closed': 'Market Closed',
  };

  function _formatNextOpen(isoStr) {
    if (!isoStr) return '';
    try {
      var d = new Date(isoStr);
      var opts = { weekday: 'short', hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York', timeZoneName: 'short' };
      return d.toLocaleString('en-US', opts);
    } catch (e) { return ''; }
  }

  function _updateOrchestratorIndicator(status) {
    var el = document.getElementById('orchestratorStatus');
    if (!el) return;

    var running = status.running;
    var paused = status.paused;
    var stage = status.current_stage || 'idle';
    var cycle = status.cycle_count || 0;
    var marketOpen = status.market_open;

    // Market-closed display takes priority when orchestrator is running
    if (running && !paused && marketOpen === false) {
      var nextEvt = status.next_market_event || {};
      var nextLabel = nextEvt.event === 'open' ? _formatNextOpen(nextEvt.time) : '';
      var nextText = nextLabel ? ' (next open: ' + nextLabel + ')' : '';
      el.innerHTML = '<span style="color:#ffd600; font-size: 0.9em;">●</span> ' +
        '<small class="text-muted">⏸ Paused — market closed' + nextText + '</small>';
      return;
    }

    var color = running ? (paused ? '#ffd600' : '#00c853') : '#ff1744';
    var label = _ORCH_STAGE_LABELS[stage] || stage;
    var durationText = '';
    if (status.last_cycle_duration_ms) {
      var secs = Math.round(status.last_cycle_duration_ms / 1000);
      durationText = ' · Last: ' + (secs >= 60 ? Math.round(secs / 60) + 'm' : secs + 's');
    }

    el.innerHTML = '<span style="color:' + color + '; font-size: 0.9em;">●</span> ' +
      '<small class="text-muted">Cycle ' + cycle + ': ' + label + durationText + '</small>';
  }

  /* -- Expose for testing -------------------------------------------- */

  window._tmcInternals = {
    normalizeStockCandidate: normalizeStockCandidate,
    normalizeOptionsCandidate: normalizeOptionsCandidate,
    tmcStockToScannerShape: tmcStockToScannerShape,
    buildTmcEnrichmentHtml: buildTmcEnrichmentHtml,
    buildOptionsEnrichmentHtml: buildOptionsEnrichmentHtml,
    buildOptionsTradeCard: buildOptionsTradeCard,
    buildActiveTradeCard: buildActiveTradeCard,
    buildActiveEnrichmentHtml: buildActiveEnrichmentHtml,
    renderTmcFinalDecisionResult: renderTmcFinalDecisionResult,
    getStatusInfo: getStatusInfo,
    TMC_STATUS_MAP: TMC_STATUS_MAP,
  };

  /* -- Register ------------------------------------------------------ */

  window.BenTradePages = window.BenTradePages || {};
  window.BenTradePages.initTradeManagementCenter = initTradeManagementCenter;
})();
