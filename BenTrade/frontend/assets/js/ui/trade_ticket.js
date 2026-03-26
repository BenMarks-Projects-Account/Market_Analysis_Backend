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
  var _step      = 'review'; // review | previewing | warning | confirmed | submitting | submitted | reconciling | done | error
  var _traceId   = null;   // end-to-end trace ID for this session
  var _submitResp = null;  // last submit response (for order ID display)
  var _lastError = null;   // last error object { message, status, detail, endpoint, bodySnippet }
  var _submitIdempotencyKey = null; // stable per-preview idempotency key (prevents duplicate orders)

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
    var execEnabled = _status && _status.tradier_execution_enabled;
    var dryRun      = _status ? _status.dry_run : true;
    var env         = _status ? _status.environment : 'unknown';
    var val         = validate(t);

    _el.innerHTML = '<div class="tt-card" onclick="event.stopPropagation()">' +
      _renderHeader(t) +
      '<div class="tt-body">' +
        _renderOrderSummary(t) +
        _renderLegs(t) +
        _renderRiskReward(t) +
        _renderPricing(t) +
        _renderPayloadPreview(t) +
        _renderSafetyPanel(execEnabled, dryRun, env) +
        _renderStatus() +
      '</div>' +
      _renderFooter(t, execEnabled, val) +
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

  /* ── Render: payload preview (dev-only accordion) ───────────── */

  function _renderPayloadPreview(t) {
    // Build the same payload the backend will receive
    var previewReq = toPreview(t, _mode);
    if (_traceId) previewReq.trace_id = _traceId;

    // Redact any sensitive fields (none in preview, but safe pattern)
    var display = JSON.stringify(previewReq, null, 2);

    var sections = '<pre class="tt-payload-json">' + display + '</pre>';

    // Show Tradier payload sent (after preview succeeds)
    if (_preview && _preview.payload_sent) {
      sections += '<div class="tt-payload-section-title">\u25B6 Tradier Payload Sent</div>' +
        '<pre class="tt-payload-json">' + JSON.stringify(_preview.payload_sent, null, 2) + '</pre>';
    }

    // Show Tradier preview response
    if (_preview && _preview.tradier_preview) {
      sections += '<div class="tt-payload-section-title">\u25B6 Tradier Preview Response</div>' +
        '<pre class="tt-payload-json tt-payload-success">' + JSON.stringify(_preview.tradier_preview, null, 2) + '</pre>';
    }

    // Show Tradier preview error
    if (_preview && _preview.tradier_preview_error) {
      sections += '<div class="tt-payload-section-title">\u25B6 Tradier Preview Error</div>' +
        '<pre class="tt-payload-json tt-payload-error">' + _preview.tradier_preview_error + '</pre>';
    }

    // Show last error payload for debugging
    if (_lastError && _lastError.bodySnippet) {
      sections += '<div class="tt-payload-section-title">\u25B6 Last Error Response</div>' +
        '<pre class="tt-payload-json tt-payload-error">' + _lastError.bodySnippet + '</pre>';
    }

    return '<details class="tt-payload-preview"' + (_step === 'error' ? ' open' : '') + '>' +
      '<summary class="tt-section-title" style="cursor:pointer;user-select:none;">' +
        '\u25C7 Payload Preview (Dev)' +
      '</summary>' +
      sections +
      ((_traceId) ? '<div class="tt-trace-id">Trace ID: ' + _traceId + '</div>' : '') +
    '</details>';
  }

  /* ── Render: safety panel ──────────────────────────────────── */

  function _renderSafetyPanel(execEnabled, dryRun, env) {
    var paperConfigured = _status ? _status.paper_configured : false;

    // Execution toggle (single flag: TRADIER_EXECUTION_ENABLED)
    var toggleCls = execEnabled ? 'tt-toggle on' : 'tt-toggle off';
    var toggleLabel = execEnabled ? 'ON' : 'OFF';
    var toggleHtml = '<button class="' + toggleCls + '" data-action="toggle-trade-cap" ' +
      'title="' + (execEnabled ? 'Disable' : 'Enable') + ' Tradier execution">' +
      '<span class="tt-toggle-track"><span class="tt-toggle-thumb"></span></span>' +
      '<span class="tt-toggle-label">' + toggleLabel + '</span>' +
    '</button>';

    // Destination label
    var destLabel = _mode === 'live' ? 'Tradier LIVE' : 'Tradier PAPER (sandbox)';

    // Warning message — simple and honest
    var warn;
    if (execEnabled) {
      warn = '<div class="tt-safety-msg tt-safety-live">Execution ENABLED \u2014 orders will be sent to ' + destLabel + '.</div>';
    } else {
      warn = '<div class="tt-safety-msg tt-safety-off">Execution DISABLED \u2014 dry run only (payload logged, not submitted).</div>';
    }

    var modeSelect = '<select class="tt-select tt-mode-select" data-field="mode">' +
      '<option value="paper"' + (_mode === 'paper' ? ' selected' : '') + '>Paper</option>' +
      '<option value="live"' + (_mode === 'live' ? ' selected' : '') + '>Live</option>' +
    '</select>';

    // Paper-not-configured hint
    var paperHint = '';
    if (_mode === 'paper' && !paperConfigured) {
      paperHint = '<div class="tt-safety-msg tt-safety-dry">' +
        'Paper credentials not configured \u2014 will use PaperBroker (simulated fills).' +
      '</div>';
    }

    return '<div class="tt-section tt-safety">' +
      '<div class="tt-section-title">Execution Safety</div>' +
      '<div class="tt-safety-row">' +
        '<span class="tt-safety-label">Tradier Execution</span>' + toggleHtml +
      '</div>' +
      '<div class="tt-safety-row">' +
        '<span class="tt-safety-label">Destination</span>' + modeSelect +
      '</div>' +
      '<div class="tt-safety-row">' +
        '<span class="tt-safety-label">Route</span>' +
        '<span class="tt-safety-value">' + destLabel + '</span>' +
      '</div>' +
      warn +
      paperHint +
    '</div>';
  }

  /* ── Render: status messages ───────────────────────────────── */

  function _renderStatus() {
    if (_step === 'previewing') {
      return '<div class="tt-status tt-status-loading"><span class="tt-spinner"></span> Previewing order\u2026</div>';
    }
    if (_step === 'submitting') {
      return '<div class="tt-status tt-status-loading"><span class="tt-spinner"></span> Submitting order to Tradier\u2026</div>';
    }
    if (_step === 'submitted' || _step === 'reconciling') {
      var reconMsg = _step === 'reconciling'
        ? '<span class="tt-spinner"></span> Reconciling with Tradier\u2026'
        : 'Order submitted \u2014 awaiting broker confirmation';
      var orderIdLine = '';
      if (_submitResp && _submitResp.broker_order_id) {
        var oid = _submitResp.broker_order_id;
        var isDryRun = _submitResp.dry_run === true || _submitResp.status === 'DRY_RUN' || oid.indexOf('dryrun-') === 0;
        var isPaperSim = oid.indexOf('paper-') === 0;
        orderIdLine = '<div class=\"tt-preview-detail\">Order ID: <strong>' + oid + '</strong>' +
          (isDryRun ? ' <span class=\"tt-badge tt-badge-dry\">[DRY RUN]</span>' : '') +
          (isPaperSim ? ' <span class=\"tt-badge tt-badge-sim\">[SIMULATOR]</span>' : '') +
        '</div>';
      }
      if (_submitResp && _submitResp.trace_id) {
        orderIdLine += '<div class="tt-preview-detail">Trace: ' + _submitResp.trace_id + '</div>';
      }
      return '<div class="tt-status tt-status-pending">' +
        '<div class="tt-preview-label">' + reconMsg + '</div>' +
        orderIdLine +
        (_submitResp && _submitResp.tradier_raw_status
          ? '<div class="tt-preview-detail">Tradier status: <strong>' + _submitResp.tradier_raw_status + '</strong></div>'
          : '') +
      '</div>';
    }
    if (_step === 'warning' && _preview) {
      var softWarns = _preview.soft_warnings || [];
      var warnHtml = '<div class="tt-status tt-status-warning">';
      warnHtml += '<div class="tt-preview-label" style="color:#ff9800;">\u26A0 Risk Warnings</div>';
      warnHtml += '<div style="background:rgba(255,152,0,0.08); border:1px solid rgba(255,152,0,0.25); border-radius:6px; padding:10px 14px; margin:8px 0;">';
      for (var wi = 0; wi < softWarns.length; wi++) {
        warnHtml += '<div style="color:rgba(224,224,224,0.85); font-size:0.85rem; margin-bottom:4px;">\u2022 ' + softWarns[wi] + '</div>';
      }
      warnHtml += '</div>';
      warnHtml += '<div style="color:rgba(224,224,224,0.5); font-size:0.8rem; margin-top:6px;">The trade preview succeeded but triggered risk warnings. You can override and continue or cancel.</div>';
      warnHtml += '</div>';
      return warnHtml;
    }
    if (_step === 'confirmed' && _preview) {
      var warns = _preview.warnings || [];
      var warnHtml = '';
      if (warns.length) {
        warnHtml = '<ul class="tt-preview-warns">' +
          warns.map(function (w) { return '<li>' + w + '</li>'; }).join('') +
        '</ul>';
      }

      // Tradier preview result
      var tradierInfo = '';
      if (_preview.tradier_preview) {
        var tp = _preview.tradier_preview;
        var tpOrder = tp.order || {};
        tradierInfo = '<div class="tt-preview-detail" style="color:#4fc3f7;">\u2714 Tradier preview confirmed</div>';
        if (tpOrder.status) {
          tradierInfo += '<div class="tt-preview-detail">Tradier status: <strong>' + tpOrder.status + '</strong></div>';
        }
        if (tpOrder.commission != null) {
          tradierInfo += '<div class="tt-preview-detail">Commission: $' + Number(tpOrder.commission).toFixed(2) + '</div>';
        }
        if (tpOrder.cost != null) {
          tradierInfo += '<div class="tt-preview-detail">Buying power effect: $' + Number(tpOrder.cost).toFixed(2) + '</div>';
        }
      } else if (_preview.tradier_preview_error) {
        tradierInfo = '<div class="tt-preview-detail" style="color:#ffa726;">\u26A0 Tradier preview: ' +
          _preview.tradier_preview_error + '</div>';
      }

      return '<div class="tt-status tt-status-preview">' +
        '<div class="tt-preview-label">Preview confirmed \u2714</div>' +
        '<div class="tt-preview-detail">Ticket: ' + _preview.ticket.id.slice(0, 8) + '\u2026</div>' +
        '<div class="tt-preview-detail">Expires: ' + new Date(_preview.expires_at).toLocaleTimeString() + '</div>' +
        (_preview.trace_id ? '<div class="tt-preview-detail">Trace: ' + _preview.trace_id + '</div>' : '') +
        tradierInfo +
        warnHtml +
      '</div>';
    }
    if (_step === 'done') {
      var doneHtml = '<div class="tt-status tt-status-success">';
      if (_submitResp) {
        var statusLabel = _submitResp.status || 'SUBMITTED';
        var isDone_DryRun = _submitResp.dry_run === true || statusLabel === 'DRY_RUN';
        var statusClass;
        if (isDone_DryRun) {
          statusClass = 'tt-tone-neutral';
          statusLabel = 'DRY RUN \u2014 NOT SUBMITTED';
        } else if (statusLabel === 'FILLED') {
          statusClass = 'tt-tone-positive';
        } else if (statusLabel === 'REJECTED') {
          statusClass = 'tt-tone-negative';
        } else {
          statusClass = '';
        }
        doneHtml += '<div class="tt-preview-label ' + statusClass + '">' + statusLabel + '</div>';
        // Destination label from backend response
        if (_submitResp.destination_label) {
          doneHtml += '<div class="tt-preview-detail">Destination: <strong>' + _submitResp.destination_label + '</strong></div>';
        }
        if (_submitResp.dev_mode_forced_paper) {
          doneHtml += '<div class="tt-preview-detail" style="color:rgba(255,200,60,0.85);">\u25B3 Development mode forced PAPER routing</div>';
        }
        if (isDone_DryRun) {
          doneHtml += '<div class="tt-preview-detail">Payload logged, no broker order placed.</div>';
        }
        if (_submitResp.broker_order_id) {
          var idLabel = isDone_DryRun ? 'Local ID' : 'Order ID';
          doneHtml += '<div class="tt-preview-detail">' + idLabel + ': <strong>' + _submitResp.broker_order_id + '</strong>';
          if (isDone_DryRun) doneHtml += ' <span class="tt-badge tt-badge-dry">[DRY RUN]</span>';
          doneHtml += '</div>';
        }
        if (_submitResp.tradier_raw_status) {
          doneHtml += '<div class="tt-preview-detail">Tradier: ' + _submitResp.tradier_raw_status + '</div>';
        }
        if (_submitResp.message) {
          doneHtml += '<div class="tt-preview-detail">' + _submitResp.message + '</div>';
        }
        if (_submitResp.trace_id) {
          doneHtml += '<div class="tt-preview-detail">Trace: ' + _submitResp.trace_id + '</div>';
        }
      } else {
        doneHtml += 'Order submitted \u2714';
      }
      doneHtml += '</div>';
      return doneHtml;
    }
    if (_step === 'error') {
      var errBox = '<div class="tt-status tt-status-error">';
      errBox += '<div class="tt-preview-label tt-tone-negative">\u274C Preview Failed</div>';
      if (_lastError) {
        errBox += '<div class="tt-error-message">' + (_lastError.message || 'Unknown error') + '</div>';
        if (_lastError.status) {
          errBox += '<div class="tt-error-detail">HTTP ' + _lastError.status + ' — ' + (_lastError.endpoint || '') + '</div>';
        }
        if (_lastError.detail && typeof _lastError.detail === 'object') {
          // Handle both object and array formats
          if (Array.isArray(_lastError.detail)) {
            // Pydantic 422 array format
            var items = _lastError.detail.map(function(e) {
              var loc = (e.loc || []).join(' \u2192 ');
              return '<li>' + (loc ? '<strong>' + loc + '</strong>: ' : '') + (e.msg || JSON.stringify(e)) + '</li>';
            }).join('');
            errBox += '<div class="tt-error-detail"><ul style="margin:4px 0;padding-left:16px;">' + items + '</ul></div>';
          } else {
            var detailMsg = _lastError.detail.message || '';
            errBox += '<div class="tt-error-detail">' + detailMsg + '</div>';
            if (_lastError.detail.failed_checks) {
              errBox += '<div class="tt-error-detail">Failed checks: ' + _lastError.detail.failed_checks.join(', ') + '</div>';
            }
            if (_lastError.detail.upstream_status) {
              errBox += '<div class="tt-error-detail">Tradier HTTP ' + _lastError.detail.upstream_status + '</div>';
            }
            if (_lastError.detail.upstream_body) {
              errBox += '<details class="tt-error-raw"><summary>Tradier response body</summary><pre>' +
                String(_lastError.detail.upstream_body).slice(0, 2000) + '</pre></details>';
            }
            if (_lastError.detail.trace_id) {
              errBox += '<div class="tt-error-detail">Trace: ' + _lastError.detail.trace_id + '</div>';
            }
          }
        } else if (_lastError.detail && _lastError.detail !== _lastError.message) {
          errBox += '<div class="tt-error-detail">' + _lastError.detail + '</div>';
        }
        if (_lastError.bodySnippet && _lastError.bodySnippet.length > 0) {
          errBox += '<details class="tt-error-raw"><summary>Raw response</summary><pre>' + _lastError.bodySnippet + '</pre></details>';
        }
      } else {
        errBox += '<div class="tt-error-message">No error details available \u2014 check browser console for stack trace.</div>';
      }
      errBox += '</div>';
      return errBox;
    }
    return '';
  }

  /* ── Render: footer ────────────────────────────────────────── */

  function _renderFooter(t, execEnabled, val) {
    var confirmLabel, confirmDisabled, confirmTitle;

    // Even if execution is ON, block if validation fails
    var actuallyValid = val.valid;

    // Diagnostic breadcrumb for footer rendering
    console.debug('[TradeTicket] _renderFooter — step=%s mode=%s execEnabled=%s valid=%s errors=%s',
      _step, _mode, execEnabled, actuallyValid, val.errors.join('; '));

    if (_step === 'review') {
      confirmLabel    = 'Preview Order';
      confirmDisabled = !actuallyValid;
      confirmTitle    = !actuallyValid ? val.errors.join(' ') : '';
    } else if (_step === 'warning') {
      // Soft risk warnings — offer override
      confirmLabel    = 'Override & Continue';
      confirmDisabled = false;
      confirmTitle    = 'Proceed despite risk warnings';
    } else if (_step === 'error') {
      // Preview failed — offer retry, never offer submit
      confirmLabel    = 'Retry Preview';
      confirmDisabled = !actuallyValid;
      confirmTitle    = !actuallyValid ? val.errors.join(' ') : 'Click to retry the preview.';
    } else if (_step === 'confirmed') {
      // Mode-specific submit button labels
      var isDevMode = _status && _status.development_mode;
      if (isDevMode && _mode === 'live') {
        confirmLabel    = 'Live Disabled (Dev Mode)';
        confirmDisabled = true;
        confirmTitle    = 'Live trading is disabled in development mode.';
      } else if (_mode === 'paper') {
        // Paper submit is ALWAYS enabled — backend controls DRY_RUN vs real.
        confirmLabel    = execEnabled ? 'Submit to Paper (Sandbox)' : 'Submit to Paper (Dry Run)';
        confirmDisabled = !actuallyValid;
        confirmTitle    = !actuallyValid ? val.errors.join(' ') : '';
      } else {
        confirmLabel    = 'Submit LIVE Order';
        confirmDisabled = !execEnabled || !actuallyValid;
        confirmTitle    = !actuallyValid
          ? val.errors.join(' ')
          : (!execEnabled ? 'Enable Tradier Execution toggle to submit.' : '');
      }
    } else {
      confirmLabel    = 'Processing\u2026';
      confirmDisabled = true;
      confirmTitle    = '';
    }

    if (_step === 'done' || _step === 'submitted' || _step === 'reconciling') {
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
        if (_step === 'review' || _step === 'error') _doPreview();
        else if (_step === 'warning') _doPreview(true);
        else if (_step === 'confirmed') _doSubmit();
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
    var isOn = _status && _status.tradier_execution_enabled;
    console.info('[TradeTicket] _toggleTradeCap — currently %s, toggling to %s', isOn ? 'ON' : 'OFF', isOn ? 'OFF' : 'ON');
    try {
      // PATCH runtime-config to toggle the single execution flag
      if (isOn) {
        await api.tradingKillSwitchOff();
      } else {
        await api.tradingKillSwitchOn();
      }
      // Refresh status from backend
      _status = await api.getTradingStatus();
      console.info('[TradeTicket] Toggle result — tradier_execution_enabled=%s dry_run=%s',
        _status && _status.tradier_execution_enabled, _status && _status.dry_run);
    } catch (err) {
      _showToast('Toggle failed: ' + (err.message || 'Unknown error'), 'error');
      console.error('[TradeTicket] Toggle trade cap error:', err);
    }
    _render();
  }

  /* ── Preview flow ──────────────────────────────────────────── */

  async function _doPreview(overrideFlag) {
    if (_loading) return;
    _step = 'previewing';
    _loading = true;
    _lastError = null;
    _render();

    try {
      var req = toPreview(_ticket, _mode);
      if (_traceId) req.trace_id = _traceId;
      if (overrideFlag) req.override = true;

      // ── Pre-request diagnostic ──────────────────────────────
      console.log('[TRADE_TICKET] preview_click', {
        trace_id: _traceId,
        destination: _mode,
        execution_enabled: _status && _status.tradier_execution_enabled,
        endpoint: 'POST /api/trading/preview',
        override: !!overrideFlag,
        payload: JSON.parse(JSON.stringify(req)),
      });

      var resp = await api.tradingPreview(req);
      _preview = resp;
      if (resp.trace_id) _traceId = resp.trace_id;

      // Check if backend returned soft warnings requiring override
      if (resp.requires_override && resp.soft_warnings && resp.soft_warnings.length > 0) {
        _step = 'warning';
        _submitIdempotencyKey = null; // reset on re-preview
        console.info('[TRADE_TICKET] preview_soft_warnings', {
          soft_warnings: resp.soft_warnings,
          checks: resp.checks,
        });
      } else {
        _step = 'confirmed';
        // Generate stable idempotency key once per successful preview
        _submitIdempotencyKey = 'idem-' + (_preview.ticket.id || _traceId) + '-' + Date.now().toString(36);
      }

      console.info('[TRADE_TICKET] preview_ok', {
        ticket_id: resp.ticket && resp.ticket.id,
        trace_id: resp.trace_id,
        checks: resp.checks,
        warnings: resp.warnings,
        soft_warnings: resp.soft_warnings || [],
        requires_override: resp.requires_override || false,
        tradier_preview: resp.tradier_preview || null,
        tradier_preview_error: resp.tradier_preview_error || null,
        payload_sent: resp.payload_sent || null,
      });
    } catch (err) {
      _step = 'error';
      _lastError = {
        message: err.message || 'Unknown error',
        status: err.status || null,
        detail: err.detail || null,
        endpoint: err.endpoint || 'POST /api/trading/preview',
        bodySnippet: err.bodySnippet || '',
        payload: err.payload || null,
      };
      console.error('[TRADE_TICKET] preview_error', _lastError);
      _showToast('Preview failed: ' + _lastError.message, 'error');
    } finally {
      _loading = false;
      _render();
    }
  }

  /* ── Submit flow ───────────────────────────────────────────── */

  async function _doSubmit() {
    if (_loading) return;
    if (!_preview) {
      console.error('[TradeTicket] _doSubmit called but _preview is null — cannot submit without a valid preview.');
      _showToast('No preview available — please preview the order first.', 'error');
      _step = 'review';
      _render();
      return;
    }
    _step = 'submitting';
    _loading = true;
    _submitResp = null;
    _render();

    try {
      // Use the stable idempotency key generated at preview-confirm time.
      // This ensures that retries or double-clicks hit the backend cache
      // instead of creating duplicate orders.
      if (!_submitIdempotencyKey) {
        _submitIdempotencyKey = 'idem-' + (_preview.ticket.id || _traceId) + '-' + Date.now().toString(36);
      }

      var payload = {
        ticket_id:          _preview.ticket.id,
        confirmation_token: _preview.confirmation_token,
        idempotency_key:    _submitIdempotencyKey,
        mode:               _mode,
        trace_id:           _traceId,
      };

      console.info('[TradeTicket] Submit payload:', {
        ticket_id: payload.ticket_id,
        mode: payload.mode,
        trace_id: payload.trace_id,
      });

      var resp = await api.tradingSubmit(payload);
      _submitResp = resp;
      var modeUsed = resp.account_mode_used ? resp.account_mode_used.toUpperCase() : _mode.toUpperCase();

      // Use backend's authoritative dry_run flag — single source of truth.
      // Fall back to order ID prefix detection for backwards compat.
      var oid = resp.broker_order_id || '';
      var isDryRun = resp.dry_run === true || resp.status === 'DRY_RUN' || oid.indexOf('dryrun-') === 0;
      var isPaperSim = oid.indexOf('paper-') === 0;
      var isTradierReal = !isDryRun && !isPaperSim;

      if (isDryRun) {
        // Dry-run: payload was logged, no broker order placed
        _step = 'done';
        _showToast('[' + modeUsed + '] DRY RUN — payload logged, not submitted', 'info');
      } else if (resp.status === 'FILLED' && isTradierReal) {
        _step = 'done';
        _showToast('[' + modeUsed + '] Order FILLED — ID: ' + oid.slice(0, 15), 'success');
      } else if (resp.status === 'REJECTED') {
        _step = 'done';
        _showToast('[' + modeUsed + '] Order REJECTED — ' + (resp.message || ''), 'error');
      } else if (isPaperSim) {
        _step = 'done';
        _showToast('[' + modeUsed + '] Paper simulated — ' + (resp.status || 'OK'), 'info');
      } else {
        // Real Tradier order: show as submitted, not yet confirmed
        _step = 'submitted';
        _showToast('[' + modeUsed + '] Order submitted — ID: ' + oid.slice(0, 15) + ' — awaiting broker update', 'info');
        _loading = false;
        _render();
        // Start reconciliation polling in background
        _doReconcile(oid);
        return;
      }
    } catch (err) {
      _step = 'error';
      _showToast('Submit failed: ' + (err.message || 'Unknown error'), 'error');
      console.error('[TradeTicket] Submit error:', err);
    } finally {
      _loading = false;
      _render();
    }
  }

  /* ── Reconciliation: poll Tradier for real order status ───── */

  async function _doReconcile(orderId) {
    if (!orderId || !api.getTradierOrderStatus) return;

    _step = 'reconciling';
    _render();

    var maxAttempts = 4;
    var delays = [2000, 3000, 5000, 8000];

    for (var i = 0; i < maxAttempts; i++) {
      await new Promise(function (resolve) { setTimeout(resolve, delays[i] || 5000); });

      try {
        var result = await api.getTradierOrderStatus(orderId);
        if (!result || !result.ok) continue;

        var order = result.order || {};
        var tradierStatus = String(order.status || '').toLowerCase();
        console.info('[TradeTicket] Reconcile attempt', i + 1, 'status:', tradierStatus);

        // Update the submit response with real Tradier status
        if (_submitResp) {
          _submitResp.tradier_raw_status = order.status || tradierStatus;
        }

        if (tradierStatus === 'filled') {
          if (_submitResp) _submitResp.status = 'FILLED';
          _step = 'done';
          _showToast('Order FILLED — confirmed by Tradier', 'success');
          _render();
          return;
        }
        if (tradierStatus === 'rejected' || tradierStatus === 'canceled' || tradierStatus === 'expired') {
          if (_submitResp) _submitResp.status = 'REJECTED';
          _step = 'done';
          _showToast('Order ' + tradierStatus.toUpperCase() + ' by Tradier', 'error');
          _render();
          return;
        }
        // pending/open/partially_filled — keep polling
        _render();
      } catch (err) {
        console.warn('[TradeTicket] Reconcile error:', err);
      }
    }

    // Exhausted retries — show final state
    _step = 'done';
    if (_submitResp && !_submitResp.tradier_raw_status) {
      _submitResp.message = 'Submitted — awaiting broker update (reconciliation timed out)';
    }
    _render();
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
    _submitResp = null;
    _lastError = null;
    _submitIdempotencyKey = null;
    // Generate trace_id on frontend when opening modal
    _traceId = 'ttk-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);

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
      console.info('[TradeTicket] Status fetched — exec_enabled=%s, dry_run=%s, dev_mode=%s, paper_configured=%s',
        _status.tradier_execution_enabled, _status.dry_run, _status.development_mode, _status.paper_configured);
      if (_status.credentials) {
        console.info('[TradeTicket] Credentials — paper_key_last4=%s, paper_acct_last4=%s, paper_base_url=%s',
          _status.credentials.paper_key_last4 || 'MISSING',
          _status.credentials.paper_acct_last4 || 'MISSING',
          _status.credentials.paper_base_url || 'MISSING');
      }
    } catch (err) {
      console.warn('[TradeTicket] Could not fetch trading status:', err);
      _status = { tradier_execution_enabled: false, dry_run: true, environment: 'unknown' };
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
    _traceId = null;
    _submitResp = null;
    _lastError = null;
    _submitIdempotencyKey = null;

    // Restore background app shell
    var shell = document.querySelector('.shell');
    if (shell) shell.classList.remove('modal-open');
  }

  /* ── Public API ────────────────────────────────────────────── */

  return { open: open, close: _close };
})();
