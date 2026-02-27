/**
 * BenTrade — Trade Ticket Modal
 *
 * Production-grade trade ticket that replaces the old stub execution modal.
 * Features:
 *   - Full trade details (order summary, legs, risk/reward, pricing, execution safety)
 *   - Preview → Confirm → Submit workflow via backend /api/trading/ endpoints
 *   - Feature-flag gated: tradeCapabilityEnabled controls live order submission
 *   - Fullscreen-safe: mounts inside overlay root
 *   - Client-side validation before enabling Confirm
 *
 * Depends on:
 *   - BenTradeTradeTicketModel  (normalize, validate, toPreviewRequest)
 *   - BenTradeApi               (getTradingStatus, tradingPreview, tradingSubmit)
 *   - BenTradeOverlayRoot       (get)
 *   - BenTradeUtils.format      (money, pct, num, toNumber)
 */
window.BenTradeTradeTicket = (function () {
  'use strict';

  var fmt        = window.BenTradeUtils.format;
  var toNum      = fmt.toNumber;
  var fmtMoney   = fmt.money;
  var fmtPct     = fmt.pct;
  var fmtNum     = fmt.num;
  var normalize  = window.BenTradeTradeTicketModel.normalize;
  var validate   = window.BenTradeTradeTicketModel.validate;
  var toPreview  = window.BenTradeTradeTicketModel.toPreviewRequest;
  var api        = window.BenTradeApi;

  /* ── State ─────────────────────────────────────────────────── */

  var _el        = null;   // modal root element
  var _ticket    = null;   // current TradeTicketModel
  var _status    = null;   // cached trading status from backend
  var _preview   = null;   // preview response from backend
  var _mode      = 'paper'; // paper | live
  var _loading   = false;
  var _step      = 'review'; // review | previewing | confirmed | submitting | done | error

  /* ── Helpers ───────────────────────────────────────────────── */

  function _dash(val) {
    return (val != null && val !== '') ? val : '\u2014';
  }

  function _money(val) {
    return val != null ? fmtMoney(val) : '\u2014';
  }

  function _pct(val, dec) {
    return val != null ? fmtPct(val, dec || 1) : '\u2014';
  }

  function _num(val, dec) {
    return val != null ? fmtNum(val, dec || 2) : '\u2014';
  }

  function _uuid() {
    return 'ttk-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
  }

  function _fmtStrategy(val) {
    return String(val || '').replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  /* ── DOM creation ──────────────────────────────────────────── */

  function _ensureEl() {
    if (_el) return _el;

    _el = document.createElement('div');
    _el.id = 'tradeTicketOverlay';
    _el.className = 'tt-overlay';
    _el.style.display = 'none';
    _el.setAttribute('role', 'dialog');
    _el.setAttribute('aria-modal', 'true');
    _el.setAttribute('aria-label', 'Trade Ticket');

    // Click backdrop to close
    _el.addEventListener('click', function (e) {
      if (e.target === _el) _close();
    });

    // ESC to close
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && _el && _el.style.display !== 'none') {
        _close();
      }
    });

    // Mount in overlay root
    var root = (window.BenTradeOverlayRoot && window.BenTradeOverlayRoot.get)
      ? window.BenTradeOverlayRoot.get()
      : document.body;
    root.appendChild(_el);

    return _el;
  }

  /* ── Render: main layout ───────────────────────────────────── */

  function _render() {
    if (!_ticket || !_el) return;
    var t = _ticket;
    var tradeCapOn = _status && _status.trade_capability_enabled;
    var dryRun     = _status ? _status.dry_run : true;
    var env        = _status ? _status.environment : 'unknown';
    var val        = validate(t);

    _el.innerHTML = '<div class="tt-card" onclick="event.stopPropagation()">' +
      _renderHeader(t) +
      '<div class="tt-body">' +
        _renderOrderSummary(t) +
        _renderLegs(t) +
        _renderRiskReward(t) +
        _renderPricing(t) +
        _renderSafetyPanel(tradeCapOn, dryRun, env) +
        _renderStatus() +
      '</div>' +
      _renderFooter(t, tradeCapOn, val) +
    '</div>';

    _bindEvents();
  }

  /* ── Render: header ────────────────────────────────────────── */

  function _renderHeader(t) {
    return '<div class="tt-header">' +
      '<div class="tt-header-title">TRADE TICKET</div>' +
      '<div class="tt-header-sub">' +
        '<span class="tt-header-symbol">' + _dash(t.underlying) + '</span>' +
        '<span class="tt-header-sep">\u00B7</span>' +
        '<span class="tt-header-strategy">' + _dash(t.strategyLabel) + '</span>' +
      '</div>' +
      '<button class="tt-close-btn" data-action="close" aria-label="Close">&times;</button>' +
    '</div>';
  }

  /* ── Render: order summary ─────────────────────────────────── */

  function _renderOrderSummary(t) {
    var action = t.priceEffect === 'CREDIT' ? 'SELL (Credit)' : 'BUY (Debit)';

    return '<div class="tt-section">' +
      '<div class="tt-section-title">Order Summary</div>' +
      '<div class="tt-grid">' +
        _row('Underlying', t.underlying) +
        _row('Strategy', t.strategyLabel) +
        _row('Action', action) +
        _row('Quantity', '<input type="number" class="tt-input tt-qty" data-field="quantity" min="1" max="100" value="' + t.quantity + '" />') +
        _row('Order Type',
          '<select class="tt-select" data-field="orderType">' +
            '<option value="limit"' + (t.orderType === 'limit' ? ' selected' : '') + '>LIMIT</option>' +
            '<option value="market"' + (t.orderType === 'market' ? ' selected' : '') + '>MARKET</option>' +
          '</select>') +
        _row('Time in Force',
          '<select class="tt-select" data-field="tif">' +
            '<option value="day"' + (t.tif === 'day' ? ' selected' : '') + '>DAY</option>' +
            '<option value="gtc"' + (t.tif === 'gtc' ? ' selected' : '') + '>GTC</option>' +
          '</select>') +
        _row('Limit Price', t.orderType === 'limit'
          ? '<input type="number" class="tt-input tt-limit" data-field="limitPrice" step="0.01" min="0.01" value="' + (t.limitPrice != null ? t.limitPrice.toFixed(2) : '') + '" />'
          : '\u2014') +
        _row('Expiration', _dash(t.expiration) + (t.dte != null ? ' (' + t.dte + ' DTE)' : '')) +
      '</div>' +
    '</div>';
  }

  /* ── Render: legs table ────────────────────────────────────── */

  function _renderLegs(t) {
    if (!t.legs || t.legs.length === 0) return '';

    var rows = '';
    for (var i = 0; i < t.legs.length; i++) {
      var leg = t.legs[i];
      var sideLabel = String(leg.side || '').replace(/_/g, ' ').toUpperCase();
      var occOk = !!(leg.optionSymbol && String(leg.optionSymbol).trim());
      var occClass = occOk ? 'tt-occ' : 'tt-occ tt-occ-missing';
      var occDisplay = occOk ? leg.optionSymbol : '\u26A0 MISSING';
      rows += '<tr>' +
        '<td>' + sideLabel + '</td>' +
        '<td>' + String(leg.right || '').toUpperCase() + '</td>' +
        '<td>' + _dash(leg.expiration) + '</td>' +
        '<td>' + _num(leg.strike, 0) + '</td>' +
        '<td>' + (leg.quantity || 1) + '</td>' +
        '<td>' + _money(leg.mid) + '</td>' +
        '<td class="' + occClass + '">' + occDisplay + '</td>' +
      '</tr>';
    }

    return '<div class="tt-section">' +
      '<div class="tt-section-title">Legs</div>' +
      '<div class="tt-table-wrap">' +
      '<table class="tt-legs-table">' +
        '<thead><tr>' +
          '<th>Side</th><th>Type</th><th>Exp</th><th>Strike</th><th>Qty</th><th>Mid</th><th>OCC</th>' +
        '</tr></thead>' +
        '<tbody>' + rows + '</tbody>' +
      '</table>' +
      '</div>' +
    '</div>';
  }

  /* ── Render: risk & reward ─────────────────────────────────── */

  function _renderRiskReward(t) {
    var beStr = '\u2014';
    if (t.breakevens && t.breakevens.length) {
      beStr = t.breakevens.map(function (b) { return _num(b, 2); }).join(', ');
    }

    return '<div class="tt-section">' +
      '<div class="tt-section-title">Risk &amp; Reward</div>' +
      '<div class="tt-grid">' +
        _row('Max Profit', _money(t.maxProfit), 'positive') +
        _row('Max Loss', _money(t.maxLoss), 'negative') +
        _row('Breakeven(s)', beStr) +
        _row('P(Profit)', _pct(t.pop, 1)) +
        _row('Expected Value', _money(t.ev)) +
        _row('Return on Risk', _num(t.ror, 2)) +
      '</div>' +
    '</div>';
  }

  /* ── Render: pricing context ───────────────────────────────── */

  function _renderPricing(t) {
    var slipWarn = '';
    if (t.limitPrice != null && t.midPrice != null) {
      var diff = Math.abs(t.limitPrice - t.midPrice);
      if (diff > 0.10) {
        slipWarn = '<div class="tt-slippage-warn">Limit is $' + diff.toFixed(2) + ' from mid \u2014 expect slippage or non-fill.</div>';
      }
    }

    return '<div class="tt-section">' +
      '<div class="tt-section-title">Pricing Context</div>' +
      '<div class="tt-grid">' +
        _row('Mid Price', _money(t.midPrice)) +
        _row('Natural Price', _money(t.naturalPrice)) +
        _row('Your Limit', _money(t.limitPrice)) +
        _row('Underlying', _money(t.underlyingPrice)) +
        _row(t.netPremiumLabel || 'Net Premium', _money(t.netPremium)) +
      '</div>' +
      slipWarn +
    '</div>';
  }

  /* ── Render: safety panel ──────────────────────────────────── */

  function _renderSafetyPanel(tradeCapOn, dryRun, env) {
    var tradingLiveEnabled = _status ? _status.trading_live_enabled : false;
    var enableLiveTrading  = _status ? _status.enable_live_trading : false;
    var paperConfigured    = _status ? _status.paper_configured    : false;

    // Trade capability toggle
    var toggleCls = tradeCapOn ? 'tt-toggle on' : 'tt-toggle off';
    var toggleLabel = tradeCapOn ? 'ON' : 'OFF';
    var toggleHtml = '<button class="' + toggleCls + '" data-action="toggle-trade-cap" ' +
      'title="' + (tradeCapOn ? 'Disable' : 'Enable') + ' trade capability">' +
      '<span class="tt-toggle-track"><span class="tt-toggle-thumb"></span></span>' +
      '<span class="tt-toggle-label">' + toggleLabel + '</span>' +
    '</button>';

    // Warning message based on state
    var warn;
    if (tradeCapOn && !dryRun) {
      warn  = '<div class="tt-safety-msg tt-safety-live">This will send a live order to Tradier (' + env + ').</div>';
    } else if (tradeCapOn && dryRun) {
      warn  = '<div class="tt-safety-msg tt-safety-dry">Trade capability ON but dry-run mode — order will be logged, not submitted.</div>';
    } else {
      warn  = '<div class="tt-safety-msg tt-safety-off">Trade capability is disabled. This is a dry run preview only.</div>';
    }

    // Live execution gate warning
    var liveGateHtml = '';
    if (_mode === 'live' && !tradingLiveEnabled) {
      liveGateHtml = '<div class="tt-safety-msg tt-safety-live">' +
        '\u26A0 TRADING_LIVE_ENABLED is OFF — live execution will be blocked by the backend.' +
      '</div>';
    }
    if (_mode === 'live' && tradeCapOn && !enableLiveTrading) {
      liveGateHtml += '<div class="tt-safety-msg tt-safety-live">' +
        '\u26A0 ENABLE_LIVE_TRADING env var is OFF — live orders will be rejected at submission.' +
      '</div>';
    }

    var modeSelect = '<select class="tt-select tt-mode-select" data-field="mode">' +
      '<option value="paper"' + (_mode === 'paper' ? ' selected' : '') + '>Paper</option>' +
      '<option value="live"' + (_mode === 'live' ? ' selected' : '') + '>Live</option>' +
    '</select>';

    // Paper-not-configured hint
    var paperHint = '';
    if (_mode === 'paper' && !paperConfigured) {
      paperHint = '<div class="tt-safety-msg tt-safety-dry">' +
        'Paper credentials not configured — will use PaperBroker (simulated fills).' +
      '</div>';
    }

    return '<div class="tt-section tt-safety">' +
      '<div class="tt-section-title">Execution Safety</div>' +
      '<div class="tt-safety-row">' +
        '<span class="tt-safety-label">Trade Capability</span>' + toggleHtml +
      '</div>' +
      '<div class="tt-safety-row">' +
        '<span class="tt-safety-label">Account Mode</span>' + modeSelect +
      '</div>' +
      warn +
      liveGateHtml +
      paperHint +
    '</div>';
  }

  /* ── Render: status messages ───────────────────────────────── */

  function _renderStatus() {
    if (_step === 'previewing') {
      return '<div class="tt-status tt-status-loading"><span class="tt-spinner"></span> Previewing order\u2026</div>';
    }
    if (_step === 'submitting') {
      return '<div class="tt-status tt-status-loading"><span class="tt-spinner"></span> Submitting order\u2026</div>';
    }
    if (_step === 'confirmed' && _preview) {
      var warns = _preview.warnings || [];
      var warnHtml = '';
      if (warns.length) {
        warnHtml = '<ul class="tt-preview-warns">' +
          warns.map(function (w) { return '<li>' + w + '</li>'; }).join('') +
        '</ul>';
      }
      return '<div class="tt-status tt-status-preview">' +
        '<div class="tt-preview-label">Preview confirmed \u2714</div>' +
        '<div class="tt-preview-detail">Ticket: ' + _preview.ticket.id.slice(0, 8) + '\u2026</div>' +
        '<div class="tt-preview-detail">Expires: ' + new Date(_preview.expires_at).toLocaleTimeString() + '</div>' +
        warnHtml +
      '</div>';
    }
    if (_step === 'done') {
      return '<div class="tt-status tt-status-success">Order submitted successfully! \u2714</div>';
    }
    if (_step === 'error') {
      return ''; // error is shown via toast
    }
    return '';
  }

  /* ── Render: footer ────────────────────────────────────────── */

  function _renderFooter(t, tradeCapOn, val) {
    var confirmLabel, confirmDisabled, confirmTitle;

    // Even if trade capability is ON, block execution if validation fails
    var actuallyValid = val.valid;

    if (_step === 'review') {
      confirmLabel    = 'Preview Order';
      confirmDisabled = !actuallyValid;
      confirmTitle    = !actuallyValid ? val.errors.join(' ') : '';
    } else if (_step === 'confirmed' || _step === 'error') {
      confirmLabel    = 'Confirm Trade';
      confirmDisabled = !tradeCapOn || !actuallyValid;
      confirmTitle    = !actuallyValid
        ? val.errors.join(' ')
        : (!tradeCapOn ? 'Live trading is disabled.' : '');
    } else {
      confirmLabel    = 'Processing\u2026';
      confirmDisabled = true;
      confirmTitle    = '';
    }

    if (_step === 'done') {
      return '<div class="tt-footer">' +
        '<button class="tt-btn tt-btn-cancel" data-action="close">Close</button>' +
      '</div>';
    }

    var warnList = '';
    var val2 = validate(t);

    // Show blocking errors as red items
    if (val2.errors.length) {
      warnList += '<div class="tt-footer-errors">' +
        val2.errors.map(function (e) { return '<span class="tt-footer-error">\u274C ' + e + '</span>'; }).join('') +
      '</div>';
    }

    // Show warnings as amber items
    if (val2.warnings.length) {
      warnList += '<div class="tt-footer-warns">' +
        val2.warnings.map(function (w) { return '<span class="tt-footer-warn">\u26A0 ' + w + '</span>'; }).join('') +
      '</div>';
    }

    return '<div class="tt-footer">' +
      warnList +
      '<div class="tt-footer-actions">' +
        '<button class="tt-btn tt-btn-cancel" data-action="close">Cancel</button>' +
        '<button class="tt-btn tt-btn-confirm" data-action="confirm"' +
          (confirmDisabled ? ' disabled' : '') +
          (confirmTitle ? ' title="' + confirmTitle + '"' : '') +
        '>' + confirmLabel + '</button>' +
      '</div>' +
    '</div>';
  }

  /* ── Grid row helper ───────────────────────────────────────── */

  function _row(label, value, tone) {
    var cls = 'tt-val' + (tone ? ' tt-tone-' + tone : '');
    return '<div class="tt-row">' +
      '<span class="tt-label">' + label + '</span>' +
      '<span class="' + cls + '">' + (value != null ? value : '\u2014') + '</span>' +
    '</div>';
  }

  /* ── Event binding ─────────────────────────────────────────── */

  function _bindEvents() {
    if (!_el) return;

    // Close button
    _el.querySelectorAll('[data-action="close"]').forEach(function (btn) {
      btn.addEventListener('click', _close);
    });

    // Confirm / preview
    var confirmBtn = _el.querySelector('[data-action="confirm"]');
    if (confirmBtn) {
      confirmBtn.addEventListener('click', function () {
        if (_step === 'review') _doPreview();
        else if (_step === 'confirmed' || _step === 'error') _doSubmit();
      });
    }

    // Field inputs
    var qtyInput = _el.querySelector('[data-field="quantity"]');
    if (qtyInput) {
      qtyInput.addEventListener('change', function () {
        _ticket.quantity = Math.max(1, parseInt(this.value, 10) || 1);
      });
    }

    var limitInput = _el.querySelector('[data-field="limitPrice"]');
    if (limitInput) {
      limitInput.addEventListener('change', function () {
        _ticket.limitPrice = parseFloat(this.value) || null;
      });
    }

    var orderTypeSelect = _el.querySelector('[data-field="orderType"]');
    if (orderTypeSelect) {
      orderTypeSelect.addEventListener('change', function () {
        _ticket.orderType = this.value;
        _render();
      });
    }

    var tifSelect = _el.querySelector('[data-field="tif"]');
    if (tifSelect) {
      tifSelect.addEventListener('change', function () {
        _ticket.tif = this.value;
      });
    }

    var modeSelect = _el.querySelector('[data-field="mode"]');
    if (modeSelect) {
      modeSelect.addEventListener('change', function () {
        _mode = this.value;
        _render();
      });
    }

    // Trade capability toggle
    var toggleBtn = _el.querySelector('[data-action="toggle-trade-cap"]');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', _toggleTradeCap);
    }
  }

  /* ── Trade capability toggle ─────────────────────────────────── */

  async function _toggleTradeCap() {
    if (_loading) return;
    var isOn = _status && _status.trade_capability_enabled;
    try {
      // kill-switch/on  → enables runtime, kill-switch/off → disables runtime
      if (isOn) {
        await api.tradingKillSwitchOff();
      } else {
        await api.tradingKillSwitchOn();
      }
      // Refresh status from backend
      _status = await api.getTradingStatus();
    } catch (err) {
      _showToast('Toggle failed: ' + (err.message || 'Unknown error'), 'error');
      console.error('[TradeTicket] Toggle trade cap error:', err);
    }
    _render();
  }

  /* ── Preview flow ──────────────────────────────────────────── */

  async function _doPreview() {
    if (_loading) return;
    _step = 'previewing';
    _loading = true;
    _render();

    try {
      var req = toPreview(_ticket, _mode);
      var resp = await api.tradingPreview(req);
      _preview = resp;
      _step = 'confirmed';
    } catch (err) {
      _step = 'error';
      _showToast('Preview failed: ' + (err.message || 'Unknown error'), 'error');
      console.error('[TradeTicket] Preview error:', err);
    } finally {
      _loading = false;
      _render();
    }
  }

  /* ── Submit flow ───────────────────────────────────────────── */

  async function _doSubmit() {
    if (_loading || !_preview) return;
    _step = 'submitting';
    _loading = true;
    _render();

    try {
      var payload = {
        ticket_id:          _preview.ticket.id,
        confirmation_token: _preview.confirmation_token,
        idempotency_key:    _uuid(),
        mode:               _mode,
      };
      var resp = await api.tradingSubmit(payload);
      _step = 'done';
      var modeUsed = resp.account_mode_used ? resp.account_mode_used.toUpperCase() : _mode.toUpperCase();
      _showToast('[' + modeUsed + '] Order ' + (resp.status || 'ACCEPTED') + ' — ID: ' + (resp.broker_order_id || '').slice(0, 12), 'success');
    } catch (err) {
      _step = 'error';
      _showToast('Submit failed: ' + (err.message || 'Unknown error'), 'error');
      console.error('[TradeTicket] Submit error:', err);
    } finally {
      _loading = false;
      _render();
    }
  }

  /* ── Toast helper ──────────────────────────────────────────── */

  function _showToast(message, type) {
    var toast = document.createElement('div');
    toast.className = 'tt-toast tt-toast-' + (type || 'info');
    toast.textContent = message;

    var root = (window.BenTradeOverlayRoot && window.BenTradeOverlayRoot.get)
      ? window.BenTradeOverlayRoot.get()
      : document.body;
    root.appendChild(toast);

    setTimeout(function () {
      toast.classList.add('tt-toast-fade');
      setTimeout(function () { toast.remove(); }, 400);
    }, 4000);
  }

  /* ── Open / close ──────────────────────────────────────────── */

  /**
   * Open the trade ticket modal.
   *
   * @param {object} tradeData  – raw trade, action payload, or card model
   * @param {object} [options]  – { rawTrade?, regimeData? }
   */
  async function open(tradeData, options) {
    var opts = options || {};
    var el = _ensureEl();

    // Normalize to ticket model
    // If both tradeData and opts.rawTrade exist, prefer rawTrade for deep fields
    var source = opts.rawTrade || tradeData;
    _ticket = normalize(source);

    // If tradeData was an action payload with extra fields, merge them
    if (tradeData && tradeData !== source) {
      if (tradeData.symbol && !_ticket.underlying) _ticket.underlying = tradeData.symbol;
      if (tradeData.strategyId && !_ticket.strategyId) _ticket.strategyId = tradeData.strategyId;
      if (tradeData.strategyLabel && !_ticket.strategyLabel) _ticket.strategyLabel = tradeData.strategyLabel;
    }

    _preview = null;
    _step    = 'review';
    _loading = false;
    _mode    = 'paper';

    // ── Diagnostic breadcrumb: log missing OCC / pricing at open ──
    if (typeof console !== 'undefined' && console.debug) {
      var _diagLegs = _ticket.legs || [];
      var _diagOccMissing = 0;
      for (var _d = 0; _d < _diagLegs.length; _d++) {
        if (!_diagLegs[_d].optionSymbol || !String(_diagLegs[_d].optionSymbol).trim()) _diagOccMissing++;
      }
      if (_diagOccMissing > 0 || _ticket.midPrice == null || _ticket.naturalPrice == null) {
        console.debug(
          '[BenTrade:TradeTicket] DIAGNOSTIC open() — ',
          'underlying=' + _ticket.underlying,
          'strategy=' + _ticket.strategyId,
          'legsCount=' + _diagLegs.length,
          'occMissing=' + _diagOccMissing,
          'midPrice=' + _ticket.midPrice,
          'naturalPrice=' + _ticket.naturalPrice,
          'breakevens=' + JSON.stringify(_ticket.breakevens),
          'sourceKeys=' + (source ? Object.keys(source).join(',') : 'null')
        );
      }
    }

    // Fetch trading status from backend
    _status = null;
    try {
      _status = await api.getTradingStatus();
    } catch (err) {
      console.warn('[TradeTicket] Could not fetch trading status:', err);
      _status = { trade_capability_enabled: false, dry_run: true, environment: 'unknown' };
    }

    _render();
    el.style.display = 'flex';

    // Dim + disable the background app shell
    var shell = document.querySelector('.shell');
    if (shell) shell.classList.add('modal-open');

    if (window.__BEN_DEBUG_OVERLAYS) {
      console.debug('[BenTrade:overlay] Trade Ticket opened',
        'parent:', el.parentElement?.id || el.parentElement?.tagName,
        'fullscreenElement:', document.fullscreenElement?.className || null,
        'ticket:', _ticket);
    }
  }

  function _close() {
    if (_el) {
      _el.style.display = 'none';
      _el.innerHTML = '';
    }
    _ticket  = null;
    _preview = null;
    _step    = 'review';
    _loading = false;

    // Restore background app shell
    var shell = document.querySelector('.shell');
    if (shell) shell.classList.remove('modal-open');
  }

  /* ── Public API ────────────────────────────────────────────── */

  return { open: open, close: _close };
})();
