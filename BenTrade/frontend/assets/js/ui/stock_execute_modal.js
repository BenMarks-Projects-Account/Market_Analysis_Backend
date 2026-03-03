/**
 * BenTrade — Stock Execution Modal
 *
 * Equity order preview/confirm/submit modal for stock strategy TradeCards.
 * Mirrors the options Trade Ticket UX pattern (review → confirm → result).
 *
 * Flow:
 *   1. open(candidate, strategyId) → show order preview
 *   2. User adjusts qty / order_type → Confirm → POST /api/stocks/execute
 *   3. Display submitted/filled/error result
 *
 * Reuses .tt-* CSS classes from the options Trade Ticket for consistent styling.
 *
 * Depends on:
 *   - BenTradeApi.stockExecute()
 *   - BenTradeApi.getStockExecutionStatus()
 *   - BenTradeOverlayRoot.get()
 *   - BenTradeUtils.format      (money, escapeHtml)
 */
window.BenTradeStockExecuteModal = (function () {
  'use strict';

  var api = window.BenTradeApi;
  var fmt = (window.BenTradeUtils && window.BenTradeUtils.format) || {};
  var fmtMoney = fmt.money || function (v) { return v != null ? '$' + Number(v).toFixed(2) : '—'; };
  var esc = fmt.escapeHtml || function (v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  };

  /* ── State ──────────────────────────────────────────────────── */

  var _el        = null;   // modal overlay element
  var _candidate = null;   // raw candidate object
  var _strategyId = '';
  var _tradeKey  = '';
  var _step      = 'review'; // review | submitting | done | error
  var _loading   = false;
  var _result    = null;

  /* Execution parameters (editable in modal) */
  var _qty       = 10;
  var _orderType = 'market';
  var _limitPrice = null;
  var _mode      = 'paper';
  var _execStatus = null;   // cached /execute/status response

  /* ── Lifecycle ──────────────────────────────────────────────── */

  /**
   * Open the stock execution modal for a candidate.
   * @param {object} candidate  – raw scanner candidate row
   * @param {string} strategyId – e.g. 'stock_pullback_swing'
   * @param {string} tradeKey   – canonical trade key
   */
  function open(candidate, strategyId, tradeKey) {
    _candidate  = candidate || {};
    _strategyId = String(strategyId || '');
    _tradeKey   = String(tradeKey || '');
    _step       = 'review';
    _loading    = false;
    _result     = null;
    _qty        = 10;
    _orderType  = 'market';
    _limitPrice = null;
    _mode       = 'paper';
    _execStatus = null;

    _ensureEl();

    /* Fetch execution status (non-blocking) */
    if (api && api.getStockExecutionStatus) {
      api.getStockExecutionStatus()
        .then(function (s) { _execStatus = s; _render(); })
        .catch(function () {});
    }

    _render();
    _el.style.display = 'flex';
  }

  function close() {
    if (_el) _el.style.display = 'none';
    _candidate = null;
  }

  /* ── DOM setup ──────────────────────────────────────────────── */

  function _ensureEl() {
    if (_el) return;
    _el = document.createElement('div');
    _el.className = 'tt-overlay';
    _el.style.display = 'none';
    _el.setAttribute('role', 'dialog');
    _el.setAttribute('aria-modal', 'true');
    _el.setAttribute('aria-label', 'Stock Order');

    _el.addEventListener('click', function (e) {
      if (e.target === _el) close();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && _el && _el.style.display !== 'none') close();
    });

    var root = (window.BenTradeOverlayRoot && window.BenTradeOverlayRoot.get)
      ? window.BenTradeOverlayRoot.get()
      : document.body;
    root.appendChild(_el);
  }

  /* ── Render ─────────────────────────────────────────────────── */

  function _render() {
    if (!_el) return;
    var c = _candidate || {};
    var sym = String(c.symbol || '').toUpperCase();
    var price = c.price != null ? Number(c.price) : null;
    var score = c.composite_score != null ? Number(c.composite_score) : null;
    var estCost = (_orderType === 'limit' && _limitPrice) ? _limitPrice * _qty
                : price != null ? price * _qty : null;

    var html = '<div class="tt-card" style="width:min(520px,94vw);">';

    /* ── Header ── */
    html += '<div class="tt-header">';
    html += '<div class="tt-header-title">Stock Order</div>';
    html += '<div class="tt-header-sub">';
    html += '<span class="tt-header-symbol" style="font-size:18px;font-weight:700;">' + esc(sym) + '</span>';
    html += '<span class="tt-header-sep"> · </span>';
    html += '<span class="tt-header-strategy">' + esc(_fmtStrategy(_strategyId)) + '</span>';
    html += '</div>';
    html += '<button class="tt-close-btn" onclick="BenTradeStockExecuteModal.close()" title="Close" '
          + 'style="position:absolute;top:12px;right:14px;background:none;border:none;color:rgba(190,236,244,0.5);font-size:18px;cursor:pointer;">✕</button>';
    html += '</div>';

    /* ── Body ── */
    html += '<div class="tt-body" style="padding:16px 20px;overflow-y:auto;max-height:60vh;">';

    if (_step === 'review') {
      html += _renderReviewStep(sym, price, score, estCost);
    } else if (_step === 'submitting') {
      html += _renderSubmitting();
    } else if (_step === 'done' && _result) {
      html += _renderResult();
    } else if (_step === 'error') {
      html += _renderError();
    }

    html += '</div>';

    /* ── Footer ── */
    html += '<div class="tt-footer" style="padding:12px 20px;border-top:1px solid rgba(0,234,255,0.12);">';
    html += _renderFooter();
    html += '</div>';

    html += '</div>';
    _el.innerHTML = html;

    /* Wire up interactive elements after render */
    _wireInputs();
  }

  /* ── Review step ────────────────────────────────────────────── */

  function _renderReviewStep(sym, price, score, estCost) {
    var h = '';

    /* Mode badge */
    var modeColor = _mode === 'live' ? '#ff5a5a' : '#00eaff';
    var modeLabel = _mode === 'live' ? 'LIVE' : 'PAPER';
    h += '<div style="text-align:center;margin-bottom:12px;">';
    h += '<span style="display:inline-block;padding:3px 12px;border-radius:20px;font-size:10px;'
       + 'font-weight:700;letter-spacing:0.1em;text-transform:uppercase;'
       + 'border:1px solid ' + modeColor + '44;color:' + modeColor + ';background:' + modeColor + '11;">'
       + modeLabel + ' MODE</span>';
    h += '</div>';

    /* Order details grid */
    h += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px 16px;margin-bottom:14px;">';
    h += _detailRow('Side', 'BUY (Long)');
    h += _detailRow('Last Price', price != null ? fmtMoney(price) : '—');
    h += _detailRow('Score', score != null ? Number(score).toFixed(1) : '—');
    h += _detailRow('Trade Key', '<span style="font-size:9px;word-break:break-all;">' + esc(_tradeKey) + '</span>');
    h += '</div>';

    /* Editable fields */
    h += '<div style="border-top:1px solid rgba(0,234,255,0.08);padding-top:12px;">';

    /* Quantity */
    h += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">';
    h += '<label style="font-size:11px;color:rgba(190,236,244,0.6);min-width:80px;">Quantity</label>';
    h += '<input id="seQty" type="number" min="1" max="500" value="' + _qty + '" '
       + 'style="width:80px;padding:5px 8px;border-radius:6px;border:1px solid rgba(0,234,255,0.2);'
       + 'background:rgba(8,18,26,0.9);color:rgba(215,251,255,0.92);font-size:13px;text-align:center;" />';
    h += '</div>';

    /* Order Type */
    h += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">';
    h += '<label style="font-size:11px;color:rgba(190,236,244,0.6);min-width:80px;">Order Type</label>';
    h += '<select id="seOrderType" style="padding:5px 8px;border-radius:6px;border:1px solid rgba(0,234,255,0.2);'
       + 'background:rgba(8,18,26,0.9);color:rgba(215,251,255,0.92);font-size:12px;">';
    h += '<option value="market"' + (_orderType === 'market' ? ' selected' : '') + '>Market</option>';
    h += '<option value="limit"' + (_orderType === 'limit' ? ' selected' : '') + '>Limit</option>';
    h += '</select>';
    h += '</div>';

    /* Limit Price (conditional) */
    if (_orderType === 'limit') {
      h += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">';
      h += '<label style="font-size:11px;color:rgba(190,236,244,0.6);min-width:80px;">Limit Price</label>';
      h += '<input id="seLimitPrice" type="number" step="0.01" min="0.01" '
         + 'value="' + (_limitPrice || (price || '')) + '" '
         + 'style="width:100px;padding:5px 8px;border-radius:6px;border:1px solid rgba(0,234,255,0.2);'
         + 'background:rgba(8,18,26,0.9);color:rgba(215,251,255,0.92);font-size:13px;text-align:center;" />';
      h += '</div>';
    }

    /* Account mode toggle */
    h += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">';
    h += '<label style="font-size:11px;color:rgba(190,236,244,0.6);min-width:80px;">Account</label>';
    h += '<select id="seMode" style="padding:5px 8px;border-radius:6px;border:1px solid rgba(0,234,255,0.2);'
       + 'background:rgba(8,18,26,0.9);color:rgba(215,251,255,0.92);font-size:12px;">';
    h += '<option value="paper"' + (_mode === 'paper' ? ' selected' : '') + '>◈ Paper</option>';
    h += '<option value="live"' + (_mode === 'live' ? ' selected' : '') + '>◆ Live</option>';
    h += '</select>';
    h += '</div>';

    h += '</div>';

    /* Estimated cost */
    h += '<div style="margin-top:12px;padding:10px 14px;border-radius:8px;border:1px solid rgba(0,234,255,0.12);background:rgba(0,234,255,0.03);">';
    h += '<div style="display:flex;justify-content:space-between;align-items:center;">';
    h += '<span style="font-size:11px;color:rgba(190,236,244,0.55);">Estimated Cost</span>';
    h += '<span style="font-size:16px;font-weight:700;color:rgba(215,251,255,0.92);">' + (estCost != null ? fmtMoney(estCost) : '—') + '</span>';
    h += '</div>';
    h += '<div style="font-size:10px;color:rgba(190,236,244,0.4);margin-top:2px;">'
       + _qty + ' shares × ' + ((_orderType === 'limit' && _limitPrice) ? fmtMoney(_limitPrice) : (price != null ? fmtMoney(price) : '—'))
       + '</div>';
    h += '</div>';

    /* Live mode warning */
    if (_mode === 'live') {
      h += '<div style="margin-top:10px;padding:8px 12px;border-radius:6px;border:1px solid rgba(255,90,90,0.3);background:rgba(255,90,90,0.06);">';
      h += '<div style="font-size:11px;color:#ff5a5a;font-weight:600;">⚠ LIVE ORDER</div>';
      h += '<div style="font-size:10px;color:rgba(255,120,120,0.8);margin-top:2px;">This will route a real order to your Tradier brokerage account. Confirm carefully.</div>';
      h += '</div>';
    }

    return h;
  }

  /* ── Submitting step ────────────────────────────────────────── */

  function _renderSubmitting() {
    return '<div style="text-align:center;padding:40px 0;">'
      + '<div class="home-scan-spinner" style="width:28px;height:28px;margin:0 auto 12px;"></div>'
      + '<div style="font-size:13px;color:rgba(190,236,244,0.7);">Submitting order…</div>'
      + '</div>';
  }

  /* ── Result step ────────────────────────────────────────────── */

  function _renderResult() {
    var r = _result || {};
    var st = r.status || 'unknown';
    var isSuccess = (st === 'submitted' || st === 'filled');
    var statusColor = st === 'filled' ? '#00dc78'
                    : st === 'submitted' ? '#00bfff'
                    : st === 'rejected' ? '#ff5a5a'
                    : '#ffc83c';
    var statusIcon  = isSuccess ? '✔' : '✖';

    /* Display label — never say FILLED unless broker actually confirmed it */
    var statusLabel = st === 'filled' ? 'FILLED'
                    : st === 'submitted' ? 'ORDER SUBMITTED'
                    : st.toUpperCase();

    var h = '<div style="text-align:center;padding:20px 0;">';

    /* Status badge */
    h += '<div style="font-size:32px;margin-bottom:8px;">' + statusIcon + '</div>';
    h += '<div style="font-size:16px;font-weight:700;color:' + statusColor + ';text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">'
       + esc(statusLabel) + '</div>';
    h += '<div style="font-size:12px;color:rgba(190,236,244,0.6);margin-bottom:16px;">' + esc(r.message || '') + '</div>';

    /* Simulator warning */
    if (r.broker === 'paper-simulator') {
      h += '<div style="margin-bottom:12px;padding:6px 10px;border-radius:6px;border:1px solid rgba(255,200,60,0.25);background:rgba(255,200,60,0.06);">'
         + '<div style="font-size:10px;color:rgba(255,200,60,0.9);">⚠ Local simulation — no Tradier paper credentials configured. Order was NOT sent to Tradier.</div>'
         + '</div>';
    }

    /* Details */
    h += '<div style="text-align:left;max-width:380px;margin:0 auto;">';
    h += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 12px;">';
    h += _detailRow('Symbol', r.symbol || '—');
    h += _detailRow('Qty', r.qty || '—');
    h += _detailRow('Order Type', r.order_type || '—');
    h += _detailRow('Account', (r.account_mode || _mode).toUpperCase());
    h += _detailRow('Broker', r.broker || '—');
    if (r.order_id) h += _detailRow('Order ID', '<span style="font-size:9px;word-break:break-all;">' + esc(r.order_id) + '</span>');
    if (r.limit_price != null) h += _detailRow('Limit Price', fmtMoney(r.limit_price));
    h += '</div>';

    /* Warnings */
    if (r.warnings && r.warnings.length) {
      h += '<div style="margin-top:10px;">';
      for (var i = 0; i < r.warnings.length; i++) {
        h += '<div style="font-size:10px;color:rgba(255,200,60,0.8);padding:2px 0;">⚠ ' + esc(r.warnings[i]) + '</div>';
      }
      h += '</div>';
    }

    /* Tradier dashboard link for real orders */
    if (r.broker === 'tradier' && r.order_id && isSuccess) {
      var dashUrl = (r.account_mode === 'live')
        ? 'https://dash.tradier.com/account/orders'
        : 'https://dash.tradier.com/account/orders';
      h += '<div style="margin-top:10px;text-align:center;">'
         + '<a href="' + dashUrl + '" target="_blank" rel="noopener" '
         + 'style="font-size:11px;color:rgba(0,234,255,0.7);text-decoration:underline;">'
         + 'View in Tradier Dashboard →</a></div>';
    }

    h += '</div></div>';
    return h;
  }

  /* ── Error step ─────────────────────────────────────────────── */

  function _renderError() {
    var msg = (_result && _result.message) || 'Order submission failed';
    return '<div style="text-align:center;padding:30px 0;">'
      + '<div style="font-size:28px;margin-bottom:8px;">✖</div>'
      + '<div style="font-size:14px;font-weight:600;color:#ff5a5a;margin-bottom:6px;">Order Failed</div>'
      + '<div style="font-size:12px;color:rgba(255,120,120,0.8);max-width:400px;margin:0 auto;">' + esc(msg) + '</div>'
      + '</div>';
  }

  /* ── Footer ─────────────────────────────────────────────────── */

  function _renderFooter() {
    if (_step === 'submitting') {
      return '<div style="text-align:center;font-size:11px;color:rgba(190,236,244,0.4);">Processing…</div>';
    }

    if (_step === 'done' || _step === 'error') {
      return '<div style="display:flex;justify-content:center;">'
        + '<button onclick="BenTradeStockExecuteModal.close()" class="btn" '
        + 'style="padding:8px 24px;border-radius:8px;border:1px solid rgba(0,234,255,0.3);'
        + 'background:rgba(0,234,255,0.08);color:rgba(0,234,255,0.9);font-size:12px;cursor:pointer;">Close</button>'
        + '</div>';
    }

    /* Review step */
    var h = '<div style="display:flex;justify-content:space-between;align-items:center;">';
    h += '<button onclick="BenTradeStockExecuteModal.close()" class="btn" '
       + 'style="padding:8px 18px;border-radius:8px;border:1px solid rgba(190,236,244,0.15);'
       + 'background:none;color:rgba(190,236,244,0.6);font-size:12px;cursor:pointer;">Cancel</button>';

    var confirmLabel = _mode === 'live' ? '◆ Confirm LIVE Order' : '◈ Confirm Paper Order';
    var confirmStyle = _mode === 'live'
      ? 'background:rgba(255,90,90,0.15);border:1px solid rgba(255,90,90,0.4);color:#ff5a5a;'
      : 'background:rgba(0,234,255,0.12);border:1px solid rgba(0,234,255,0.35);color:rgba(0,234,255,0.95);';

    h += '<button id="seConfirmBtn" class="btn" '
       + 'style="padding:8px 22px;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;' + confirmStyle + '">'
       + confirmLabel + '</button>';
    h += '</div>';
    return h;
  }

  /* ── Interactive wiring ────────────────────────────────────── */

  function _wireInputs() {
    if (!_el || _step !== 'review') return;

    var qtyInput = _el.querySelector('#seQty');
    var typeSelect = _el.querySelector('#seOrderType');
    var limitInput = _el.querySelector('#seLimitPrice');
    var modeSelect = _el.querySelector('#seMode');
    var confirmBtn = _el.querySelector('#seConfirmBtn');

    if (qtyInput) {
      qtyInput.addEventListener('input', function () {
        _qty = Math.max(1, Math.min(500, parseInt(this.value, 10) || 1));
        _render();
      });
    }

    if (typeSelect) {
      typeSelect.addEventListener('change', function () {
        _orderType = this.value;
        if (_orderType === 'market') _limitPrice = null;
        else if (_candidate && _candidate.price) _limitPrice = Number(_candidate.price);
        _render();
      });
    }

    if (limitInput) {
      limitInput.addEventListener('input', function () {
        _limitPrice = parseFloat(this.value) || null;
        _render();
      });
    }

    if (modeSelect) {
      modeSelect.addEventListener('change', function () {
        _mode = this.value;
        _render();
      });
    }

    if (confirmBtn) {
      confirmBtn.addEventListener('click', function () {
        _submit();
      });
    }
  }

  /* ── Submit flow ────────────────────────────────────────────── */

  function _submit() {
    if (_loading) return;
    _loading = true;
    _step = 'submitting';
    _render();

    var c = _candidate || {};
    var payload = {
      trade_key:      _tradeKey,
      symbol:         String(c.symbol || '').toUpperCase(),
      strategy_id:    _strategyId,
      trade_type:     'stock_long',
      qty:            _qty,
      order_type:     _orderType,
      limit_price:    _orderType === 'limit' ? _limitPrice : null,
      time_in_force:  'day',
      account_mode:   _mode,
      price_reference: c.price != null ? Number(c.price) : null,
      as_of:          new Date().toISOString(),
      engine:         c.composite_score != null ? { composite_score: c.composite_score } : null,
      metrics:        (c.metrics && typeof c.metrics === 'object') ? c.metrics : null,
      client_request_id: _uuid(),
      confirm_live:   _mode === 'live',
    };

    api.stockExecute(payload)
      .then(function (resp) {
        _loading = false;
        _result = resp;
        _step = 'done';
        _render();

        /* Store execution in session for card state */
        _cacheExecution(_tradeKey, resp);
      })
      .catch(function (err) {
        _loading = false;
        _result = { message: (err && err.message) || 'Execution failed', status: 'error' };
        _step = 'error';
        _render();
      });
  }

  /* ── Helpers ────────────────────────────────────────────────── */

  function _detailRow(label, value) {
    return '<div style="font-size:11px;color:rgba(190,236,244,0.5);">' + esc(label) + '</div>'
      + '<div style="font-size:12px;color:rgba(215,251,255,0.9);font-weight:500;">' + (value || '—') + '</div>';
  }

  function _fmtStrategy(id) {
    return String(id || '').replace(/^stock_/, '').replace(/_/g, ' ')
      .replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  function _uuid() {
    return 'se-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
  }

  /* ── Session-level execution cache ─────────────────────────── */

  var _EXEC_CACHE_KEY = 'bentrade_stock_executions';

  function _cacheExecution(tradeKey, result) {
    if (!tradeKey) return;
    try {
      var raw = sessionStorage.getItem(_EXEC_CACHE_KEY);
      var cache = raw ? JSON.parse(raw) : {};
      cache[tradeKey] = {
        status:   result.status,
        order_id: result.order_id,
        broker:   result.broker,
        mode:     result.account_mode || _mode,
        ts:       new Date().toISOString(),
      };
      sessionStorage.setItem(_EXEC_CACHE_KEY, JSON.stringify(cache));
    } catch (_e) {}
  }

  /**
   * Get cached execution for a trade key (if any).
   * Used by card renderer to show SUBMITTED / FILLED badges.
   */
  function getExecution(tradeKey) {
    try {
      var raw = sessionStorage.getItem(_EXEC_CACHE_KEY);
      if (!raw) return null;
      var cache = JSON.parse(raw);
      return cache[tradeKey] || null;
    } catch (_e) { return null; }
  }

  /* ── Public API ─────────────────────────────────────────────── */

  return {
    open:          open,
    close:         close,
    getExecution:  getExecution,
  };
})();
