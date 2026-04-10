/**
 * BenTrade — Trade Ticket Normalization Adapter
 *
 * Converts raw trade card data (from buildTradeActionPayload or _raw)
 * into a clean TradeTicketModel that the Trade Ticket modal consumes.
 *
 * Shape:
 *   underlying        – symbol string  (e.g. "SPY")
 *   strategyId        – canonical strategy id (e.g. "put_credit_spread")
 *   strategyLabel     – display label (e.g. "Put Credit Spread")
 *   quantity          – number of contracts (default 1)
 *   orderType         – "limit" | "market"
 *   tif               – "day" | "gtc"
 *   limitPrice        – per-spread limit price (number | null)
 *   maxProfit         – per-contract max profit (number | null)
 *   maxLoss           – per-contract max loss (number | null)
 *   breakevens        – array of breakeven prices (number[] | null)
 *   pop               – probability of profit 0-1 (number | null)
 *   ev                – expected value per contract (number | null)
 *   ror               – return on risk ratio (number | null)
 *   expiration        – expiration date string
 *   dte               – days to expiration
 *   underlyingPrice   – current underlying price
 *   width             – spread width in points
 *   shortStrike       – short leg strike
 *   longStrike        – long leg strike
 *   netPremium        – per-spread net credit or debit
 *   netPremiumLabel   – "Net Credit" | "Net Debit"
 *   priceEffect       – "CREDIT" | "DEBIT"
 *   midPrice          – spread mid price (number | null)
 *   naturalPrice      – natural price (bid/ask derived, number | null)
 *   ivRank            – IV rank 0-100 (number | null)
 *   iv                – implied volatility (number | null)
 *   legs              – array of leg objects (see below)
 *
 * Leg shape:
 *   side              – "buy_to_open" | "sell_to_open" | "buy_to_close" | "sell_to_close"
 *   optionSymbol      – OCC symbol string or ""
 *   expiration        – expiration date string
 *   strike            – strike price number
 *   right             – "call" | "put"
 *   quantity          – leg quantity
 *   bid               – leg bid (number | null)
 *   ask               – leg ask (number | null)
 *   mid               – leg mid (number | null)
 *
 * Depends on:
 *   - BenTradeUtils.format (toNumber)
 */
window.BenTradeTradeTicketModel = (function () {
  'use strict';

  var toNum = window.BenTradeUtils.format.toNumber;

  /* ── Side mapping ──────────────────────────────────────────── */

  var SIDE_MAP = {
    sell:          'sell_to_open',
    buy:           'buy_to_open',
    short:         'sell_to_open',
    long:          'buy_to_open',
    sell_to_open:  'sell_to_open',
    buy_to_open:   'buy_to_open',
    sell_to_close: 'sell_to_close',
    buy_to_close:  'buy_to_close',
  };

  function _normSide(raw) {
    if (!raw) return 'buy_to_open';
    return SIDE_MAP[String(raw).toLowerCase()] || 'buy_to_open';
  }

  /* ── Strategy → price effect ───────────────────────────────── */

  function _priceEffect(strategyId) {
    if (!strategyId) return 'CREDIT';
    var s = String(strategyId).toLowerCase();
    if (s.indexOf('debit') !== -1) return 'DEBIT';
    if (s === 'call_debit' || s === 'put_debit') return 'DEBIT';
    return 'CREDIT';
  }

  /* ── OCC symbol builder ──────────────────────────────────────── */

  /**
   * Build an OCC symbol from components when the backend didn't provide one.
   * OCC format: ROOT(1-6 chars) + YYMMDD + P/C + 8-digit strike (strike * 1000, zero-padded).
   * Returns empty string if any component is missing.
   *
   * Input fields: symbol (underlying), expiration (YYYY-MM-DD), strike (number), callput (put|call)
   * Formula: OCC = SYMBOL + YYMMDD + P/C + sprintf("%08d", strike * 1000)
   */
  function _buildOccSymbol(symbol, expiration, strike, callput) {
    if (!symbol || !expiration || strike == null || !callput) return '';
    var sym = String(symbol).toUpperCase().replace(/[^A-Z]/g, '');
    if (!sym || sym.length > 6) return '';
    var parts = String(expiration).split('-');
    if (parts.length !== 3) return '';
    var yy = parts[0].slice(-2);
    var mm = parts[1];
    var dd = parts[2];
    var pc = String(callput).charAt(0).toUpperCase();
    if (pc !== 'P' && pc !== 'C') return '';
    var strikeInt = Math.round(Number(strike) * 1000);
    if (isNaN(strikeInt) || strikeInt <= 0) return '';
    var strikeStr = String(strikeInt);
    while (strikeStr.length < 8) strikeStr = '0' + strikeStr;
    return sym + yy + mm + dd + pc + strikeStr;
  }

  /* ── Resolve legs ──────────────────────────────────────────── */

  function _normalizeLegs(rawLegs, header) {
    if (!Array.isArray(rawLegs) || rawLegs.length === 0) {
      // Synthesize 2-leg spread from header if possible
      if (header.shortStrike == null && header.longStrike == null) return [];
      var sid = String(header.strategyId || '').toLowerCase();
      var cp = sid.indexOf('call') !== -1 ? 'call' : 'put';
      var isCredit = _priceEffect(sid) === 'CREDIT';
      var legs = [];
      if (header.shortStrike != null) {
        legs.push({
          side:          isCredit ? 'sell_to_open' : 'buy_to_open',
          optionSymbol:  _buildOccSymbol(header.symbol, header.expiration, header.shortStrike, cp),
          expiration:    header.expiration || '',
          strike:        header.shortStrike,
          right:         cp,
          quantity:      1,
          bid: null, ask: null, mid: null,
        });
      }
      if (header.longStrike != null) {
        legs.push({
          side:          isCredit ? 'buy_to_open' : 'sell_to_open',
          optionSymbol:  _buildOccSymbol(header.symbol, header.expiration, header.longStrike, cp),
          expiration:    header.expiration || '',
          strike:        header.longStrike,
          right:         cp,
          quantity:      1,
          bid: null, ask: null, mid: null,
        });
      }
      return legs;
    }

    return rawLegs.map(function (leg) {
      var occ = String(leg.occ_symbol || leg.option_symbol || leg.optionSymbol || '');

      // Build OCC from components when not pre-built
      if (!occ) {
        var cp = String(leg.callput || leg.right || leg.option_type || '').toLowerCase();
        var exp = String(leg.expiration || header.expiration || '');
        occ = _buildOccSymbol(header.symbol, exp, toNum(leg.strike), cp);
      }

      return {
        side:          _normSide(leg.side),
        optionSymbol:  occ,
        expiration:    String(leg.expiration || header.expiration || ''),
        strike:        toNum(leg.strike) || 0,
        right:         String(leg.callput || leg.right || leg.option_type || 'put').toLowerCase(),
        quantity:      toNum(leg.qty || leg.quantity) || 1,
        bid:           toNum(leg.bid),
        ask:           toNum(leg.ask),
        mid:           toNum(leg.mid),
      };
    });
  }

  /* ── Resolve metric from raw trade (4-tier) ────────────────── */

  function _dig(raw, keys) {
    for (var i = 0; i < keys.length; i++) {
      var v = null;
      // check computed, computed_metrics, math, details, then root
      if (raw.computed && raw.computed[keys[i]] != null)         v = toNum(raw.computed[keys[i]]);
      if (v != null) return v;
      if (raw.computed_metrics && raw.computed_metrics[keys[i]] != null) v = toNum(raw.computed_metrics[keys[i]]);
      if (v != null) return v;
      if (raw.math && raw.math[keys[i]] != null)                 v = toNum(raw.math[keys[i]]);
      if (v != null) return v;
      if (raw.details && raw.details[keys[i]] != null)           v = toNum(raw.details[keys[i]]);
      if (v != null) return v;
      v = toNum(raw[keys[i]]);
      if (v != null) return v;
    }
    return null;
  }

  /* ================================================================
   *  normalizeForTicket  —  THE main entry point
   * ================================================================
   *
   * Accepts EITHER:
   *   (a) A full trade card model (from option_trade_card_model.map())
   *   (b) A buildTradeActionPayload() result
   *   (c) A raw API trade object
   *
   * Returns a clean TradeTicketModel.
   */
  function normalizeForTicket(input) {
    if (!input || typeof input !== 'object') return _empty();
    console.log('[TradeTicket] Input to normalize:', JSON.stringify(input, null, 2));

    // If input has _raw, use it for deep metric resolution
    var raw = (input._raw && typeof input._raw === 'object') ? input._raw : input;

    // Resolve identity
    var symbol        = String(input.symbol || raw.symbol || raw.underlying || '').toUpperCase();
    var strategyId    = String(input.strategyId || input.strategy_id || raw.strategy_id || raw.strategy || '').toLowerCase();
    var strategyLabel = String(input.strategyLabel || input.strategy_label || _fmtStrategy(strategyId));
    var expiration    = String(input.expiration || raw.expiration || '');
    var dte           = toNum(input.dte || raw.dte);
    var shortStrike   = toNum(input.shortStrike != null ? input.shortStrike : raw.short_strike);
    var longStrike    = toNum(input.longStrike != null ? input.longStrike : raw.long_strike);

    // Derive strikes from legs when header strikes are missing
    // Input fields: legs[].strike, legs[].side
    if (shortStrike == null || longStrike == null) {
      var srcLegs = input.legs || raw.legs;
      if (Array.isArray(srcLegs) && srcLegs.length >= 2) {
        for (var li = 0; li < srcLegs.length; li++) {
          var leg = srcLegs[li];
          var side = String(leg.side || '').toLowerCase();
          var st = toNum(leg.strike);
          if (st != null && st > 0) {
            if (side === 'sell_to_open' || side === 'sell' || side === 'short') {
              if (shortStrike == null) shortStrike = st;
            } else if (side === 'buy_to_open' || side === 'buy' || side === 'long') {
              if (longStrike == null) longStrike = st;
            }
          }
        }
      }
    }
    var width         = toNum(input.width || raw.width) || (shortStrike != null && longStrike != null ? Math.abs(shortStrike - longStrike) : null);
    var underlyingPrice = toNum(input.underlyingPrice != null ? input.underlyingPrice : raw.underlying_price);

    // Net premium
    var netPremium      = toNum(input.netPremium);
    var netPremiumLabel = input.netPremiumLabel || null;
    if (netPremium == null) {
      netPremium = _dig(raw, ['net_credit']);
      if (netPremium != null) { netPremiumLabel = 'Net Credit'; }
      else {
        netPremium = _dig(raw, ['net_debit']);
        if (netPremium != null) netPremiumLabel = 'Net Debit';
      }
    }
    if (!netPremiumLabel) netPremiumLabel = _priceEffect(strategyId) === 'CREDIT' ? 'Net Credit' : 'Net Debit';

    // Resolve legs
    var rawLegs = input.legs || raw.legs || null;
    var header = { strategyId: strategyId, expiration: expiration, shortStrike: shortStrike, longStrike: longStrike, symbol: symbol };
    var legs = _normalizeLegs(rawLegs, header);

    // Risk / reward metrics
    var maxProfit  = _dig(raw, ['max_profit', 'max_profit_per_contract']);
    var maxLoss    = _dig(raw, ['max_loss', 'max_loss_per_contract']);
    var pop        = _dig(raw, ['pop', 'probability_of_profit']);
    var ev         = _dig(raw, ['expected_value', 'ev']);
    var ror        = _dig(raw, ['return_on_risk', 'ror', 'ev_to_risk']);

    // Breakevens — resolve from multiple sources
    // Input fields: breakevens, breakeven, computed.breakevens, details.breakevens,
    //               details.break_even, break_even (root)
    var breakevens = null;
    var be = raw.breakevens || raw.breakeven
      || (raw.math && (raw.math.breakevens || raw.math.breakeven))
      || (raw.computed && raw.computed.breakevens)
      || (raw.details && (raw.details.breakevens || raw.details.break_even))
      || raw.break_even;
    if (Array.isArray(be)) breakevens = be.map(function (v) { return toNum(v); }).filter(function (v) { return v != null; });
    else if (be != null) { var bv = toNum(be); if (bv != null) breakevens = [bv]; }

    // Pricing context — resolve from pricing sub-dict first, then root
    // Input fields: pricing.spread_mid, spread_mid, mid_price, mid (for mid)
    //               pricing.spread_natural, spread_natural, natural_price, natural (for natural)
    // Note: use explicit != null checks instead of || to handle 0 correctly.
    var pricingSub = (raw.pricing && typeof raw.pricing === 'object') ? raw.pricing : {};
    var midPrice     = toNum(pricingSub.spread_mid);
    if (midPrice == null) midPrice = _dig(raw, ['spread_mid', 'mid_price', 'mid']);
    var naturalPrice = toNum(pricingSub.spread_natural);
    if (naturalPrice == null) naturalPrice = _dig(raw, ['spread_natural', 'natural_price', 'natural']);

    // IV / ranking
    var iv     = _dig(raw, ['iv', 'implied_volatility']);
    var ivRank = _dig(raw, ['iv_rank', 'iv_percentile']);

    // Limit price: default to net premium (which is per-spread mid-based)
    var limitPrice = netPremium != null ? Math.abs(netPremium) : (midPrice != null ? Math.abs(midPrice) : null);

    // Compute spread-level pricing from leg bid/ask when not already set
    var isCredit = _priceEffect(strategyId) === 'CREDIT';
    if (midPrice == null && legs && legs.length >= 2) {
      var sellLeg = legs.find(function(l) { return l.side === 'sell_to_open'; });
      var buyLeg  = legs.find(function(l) { return l.side === 'buy_to_open'; });
      if (sellLeg && buyLeg) {
        var sellMid = (sellLeg.bid != null && sellLeg.ask != null) ? (sellLeg.bid + sellLeg.ask) / 2 : null;
        var buyMid  = (buyLeg.bid != null && buyLeg.ask != null) ? (buyLeg.bid + buyLeg.ask) / 2 : null;
        if (sellMid != null && buyMid != null) {
          if (isCredit) {
            midPrice     = sellMid - buyMid;
            naturalPrice = sellLeg.bid - buyLeg.ask;
          } else {
            midPrice     = buyMid - sellMid;
            naturalPrice = buyLeg.ask - sellLeg.bid;
          }
        }
      }
      if (limitPrice == null && midPrice != null) {
        limitPrice = Math.round(Math.abs(midPrice) * 100) / 100;
      }
    }

    // Execution readiness — backend may flag this when legs lack OCC
    var executionInvalid = !!(raw.execution_invalid);
    var executionInvalidReason = raw.execution_invalid_reason || null;

    var _result = {
      underlying:      symbol,
      strategyId:      strategyId,
      strategyLabel:   strategyLabel,
      quantity:        Math.max(1, parseInt(raw.quantity, 10) || 1),
      orderType:       'limit',
      tif:             'day',
      limitPrice:      limitPrice,
      maxProfit:       maxProfit,
      maxLoss:         maxLoss,
      breakevens:      breakevens,
      pop:             pop,
      ev:              ev,
      ror:             ror,
      expiration:      expiration,
      dte:             dte,
      underlyingPrice: underlyingPrice,
      width:           width,
      shortStrike:     shortStrike,
      longStrike:      longStrike,
      netPremium:      netPremium,
      netPremiumLabel: netPremiumLabel,
      priceEffect:     _priceEffect(strategyId),
      midPrice:        midPrice,
      naturalPrice:    naturalPrice,
      iv:              iv,
      ivRank:          ivRank,
      legs:            legs,
      executionInvalid:       executionInvalid,
      executionInvalidReason: executionInvalidReason,
    };
    console.log('[TradeTicket] Output from normalize:', JSON.stringify(_result, null, 2));
    return _result;
  }

  function _empty() {
    return {
      underlying: '', strategyId: '', strategyLabel: '', quantity: 1,
      orderType: 'limit', tif: 'day', limitPrice: null,
      maxProfit: null, maxLoss: null, breakevens: null, pop: null, ev: null, ror: null,
      expiration: '', dte: null, underlyingPrice: null, width: null,
      shortStrike: null, longStrike: null, netPremium: null, netPremiumLabel: '',
      priceEffect: 'CREDIT', midPrice: null, naturalPrice: null,
      iv: null, ivRank: null, legs: [],
      executionInvalid: false, executionInvalidReason: null,
    };
  }

  function _fmtStrategy(val) {
    return String(val || 'trade').replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  /* ── Client-side validation ────────────────────────────────── */

  /**
   * Validate a TradeTicketModel before submission.
   * Returns { valid: boolean, errors: string[], warnings: string[] }
   *
   * Blocking errors (prevent execution):
   *   - Symbol / strategy / legs missing
   *   - Limit price invalid
   *   - execution_invalid flag from backend (e.g. missing OCC)
   *   - Any leg missing OCC symbol
   *
   * Warnings (non-blocking):
   *   - Max loss unavailable
   *   - Expiration missing
   *   - Breakeven unavailable
   *   - Mid / natural price missing
   */
  function validate(ticket) {
    var errors = [];
    var warnings = [];

    if (!ticket.underlying)            errors.push('Symbol is required.');
    if (!ticket.strategyId)            errors.push('Strategy is required.');
    if (ticket.quantity < 1)           errors.push('Quantity must be at least 1.');
    if (ticket.legs.length === 0)      errors.push('At least one leg is required for options.');

    if (ticket.orderType === 'limit') {
      if (ticket.limitPrice == null || ticket.limitPrice <= 0) {
        errors.push('Limit price must be a positive number.');
      }
    }

    // Execution-invalid flag from backend (e.g. missing OCC symbols)
    if (ticket.executionInvalid) {
      errors.push(ticket.executionInvalidReason || 'Trade flagged as execution-invalid by backend.');
    }

    // OCC symbol check on every leg
    var occMissing = 0;
    for (var i = 0; i < ticket.legs.length; i++) {
      var leg = ticket.legs[i];
      if (!leg.optionSymbol || !String(leg.optionSymbol).trim()) {
        occMissing++;
      }
    }
    if (occMissing > 0) {
      errors.push(occMissing + ' leg(s) missing OCC symbol — cannot execute.');
    }
    // Legs validation — all strategies require a legs array
    if (!Array.isArray(ticket.legs) || ticket.legs.length < 2) {
      errors.push('Trade requires at least 2 legs (got ' + (Array.isArray(ticket.legs) ? ticket.legs.length : 0) + ').');
    }
    if (ticket.maxLoss == null || ticket.maxLoss === 0) {
      warnings.push('Max loss is unavailable — proceed with caution.');
    }

    if (!ticket.expiration) {
      warnings.push('Expiration is missing.');
    } else {
      // Block execution of expired options
      var expParts = String(ticket.expiration).split('-');
      if (expParts.length === 3) {
        var expDate = new Date(Number(expParts[0]), Number(expParts[1]) - 1, Number(expParts[2]));
        // Set to end of expiration day (market close)
        expDate.setHours(23, 59, 59, 999);
        if (expDate < new Date()) {
          errors.push('Options expired on ' + ticket.expiration + ' — cannot execute.');
        }
      }
    }

    if (!ticket.breakevens || ticket.breakevens.length === 0) {
      warnings.push('Breakeven not computed — verify trade manually.');
    }

    if (ticket.midPrice == null) {
      warnings.push('Spread mid price unavailable.');
    }

    if (ticket.naturalPrice == null) {
      warnings.push('Natural price unavailable — fill quality uncertain.');
    }

    // Log validation result
    if (typeof console !== 'undefined' && console.info) {
      console.info(
        '[ExecutionValidator] trade=' + ticket.underlying + ' ' + ticket.strategyId +
        ' valid=' + (errors.length === 0) +
        ' errors=' + errors.length +
        ' warnings=[' + warnings.join('; ') + ']'
      );
    }

    return { valid: errors.length === 0, errors: errors, warnings: warnings };
  }

  /* ── Build backend preview request ─────────────────────────── */

  /**
   * Convert TradeTicketModel → TradingPreviewRequest payload
   * for POST /api/trading/preview
   */
  function toPreviewRequest(ticket, mode) {
    // Map strategyId to backend strategy enum
    var strategyMap = {
      put_credit_spread: 'put_credit',
      put_credit:        'put_credit',
      call_credit_spread: 'call_credit',
      call_credit:       'call_credit',
      put_debit_spread:  'put_debit',
      put_debit:         'put_debit',
      call_debit_spread: 'call_debit',
      call_debit:        'call_debit',
      iron_condor:       'iron_condor',
      iron_butterfly:        'iron_butterfly',
      butterfly_debit:   'butterfly_debit',
      butterflies:       'butterfly_debit',
      calendar_call_spread:  'calendar_call',
      calendar_put_spread:   'calendar_put',
      diagonal_call_spread:  'diagonal_call',
      diagonal_put_spread:   'diagonal_put',
    };
    var strategy = strategyMap[ticket.strategyId] || 'put_credit';

    // Build legs array from ticket.legs for ALL strategies
    var legsPayload = [];
    if (Array.isArray(ticket.legs) && ticket.legs.length >= 2) {
      for (var i = 0; i < ticket.legs.length; i++) {
        var leg = ticket.legs[i];
        var side = String(leg.side || '').toUpperCase().replace(/ /g, '_');
        if (side === 'SELL') side = 'SELL_TO_OPEN';
        if (side === 'BUY')  side = 'BUY_TO_OPEN';
        var legObj = {
          strike:      leg.strike,
          side:        side,
          option_type: String(leg.right || leg.optionType || leg.callput || 'put').toLowerCase(),
          quantity:    leg.quantity || 1,
        };
        // Pass through exact OCC symbol from option chain when available
        if (leg.optionSymbol) legObj.option_symbol = leg.optionSymbol;
        // Per-leg expiration for calendar/diagonal strategies
        if (leg.expiration && leg.expiration !== ticket.expiration) {
          legObj.expiration = leg.expiration;
        }
        legsPayload.push(legObj);
      }
    }

    // Guard: every strategy must have a legs array
    if (legsPayload.length < 2) {
      throw new Error(
        'Trade requires at least 2 legs (got ' + legsPayload.length +
        ', strategy=' + strategy + ')'
      );
    }

    var payload = {
      symbol:        ticket.underlying,
      strategy:      strategy,
      expiration:    ticket.expiration,
      legs:          legsPayload,
      quantity:      ticket.quantity,
      limit_price:   (ticket.limitPrice != null && ticket.limitPrice > 0) ? ticket.limitPrice : (function() {
        throw new Error('Limit price is missing or zero — cannot submit preview without a valid price.');
      })(),
      time_in_force: ticket.tif.toUpperCase(),
      mode:          mode || 'paper',
    };

    // Also send short_strike/long_strike for 2-leg strategies (backward compat)
    if (legsPayload.length === 2 && ticket.shortStrike && ticket.longStrike) {
      payload.short_strike = ticket.shortStrike;
      payload.long_strike  = ticket.longStrike;
    }

    if (typeof console !== 'undefined' && console.log) {
      var optionSymbols = legsPayload.map(function(l) { return l.option_symbol || '(none)'; });
      console.log('Order option symbols:', optionSymbols);
      console.log('[TradeTicket] preview payload:', JSON.stringify(payload));
    }

    return payload;
  }

  return {
    normalize:        normalizeForTicket,
    validate:         validate,
    toPreviewRequest: toPreviewRequest,
  };
})();
