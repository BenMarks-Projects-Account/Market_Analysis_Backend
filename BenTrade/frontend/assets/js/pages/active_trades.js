/**
 * BenTrade — Active Trades Dashboard (Institutional-Grade Position Management)
 *
 * Sections:
 *   1. Portfolio Risk Bar      — aggregate risk metrics
 *   2. Position Control Bar    — sort, filter, view toggle
 *   3. Position Cards Grid     — collapsible cards with header P&L
 *   4. Card Snapshot Layout    — aligned metric rows (Row 1 + Row 2)
 *   5. Action Bar              — Run Model Analysis · Execute Trade · Close Position
 *   6. Secondary Actions       — Show Legs · Simulate Close
 *   7. Expandable Analytics    — Position Breakdown, PnL Sim, Model Analysis, Notes
 *   8. Professional Enhancements — PnL heat bar, risk flags, alerts placeholder
 *
 * Data logic (Section 9):
 *   avg_entry_per_share, cost_basis_total, market_value_total, unrealized_pl_total, pl_pct.
 *   Values match broker calculations. Batch-fetched quotes via backend.
 */
window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initActiveTrades = function initActiveTrades(rootEl) {
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const api = window.BenTradeApi;
  const sourceHealthUi = window.BenTradeSourceHealth;
  const tradeKeyUtil = window.BenTradeUtils?.tradeKey;

  /* ── DOM refs ── */
  const listEl            = scope.querySelector('#activeList');
  const errorEl           = scope.querySelector('#activeError');
  const refreshBtn        = scope.querySelector('#activeRefreshBtn');
  const autoRefreshEl     = scope.querySelector('#activeAutoRefresh');
  const underlyingFilterEl= scope.querySelector('#activeUnderlyingFilter');
  const statusFilterEl    = scope.querySelector('#activeStatusFilter');
  const searchEl          = scope.querySelector('#activeSearch');
  const liveBadgeEl       = scope.querySelector('#activeLiveBadge');
  const sortSelectEl      = scope.querySelector('#atSortSelect');
  const viewToggleEl      = scope.querySelector('#atViewToggle');

  const modalEl           = scope.querySelector('#activeCloseModal');
  const modalBodyEl       = scope.querySelector('#activeModalBody');
  const modalCloseBtn     = scope.querySelector('#activeCloseModalBtn');
  const accountToggleEl   = scope.querySelector('#activeAccountToggle');

  const closeConfirmModal = scope.querySelector('#activeCloseConfirmModal');
  const closeConfirmBody  = scope.querySelector('#activeCloseConfirmBody');
  const closeConfirmDismiss = scope.querySelector('#activeCloseConfirmDismiss');
  const toastEl           = scope.querySelector('#activeToast');
  const expandAllBtn      = scope.querySelector('#atExpandAllBtn');

  /* Risk bar metric elements */
  const riskEls = {
    positions:    scope.querySelector('#atRiskPositions .at-risk-value'),
    exposure:     scope.querySelector('#atRiskExposure .at-risk-value'),
    unrealizedPnl:scope.querySelector('#atRiskUnrealizedPnl .at-risk-value'),
    dailyPnl:     scope.querySelector('#atRiskDailyPnl .at-risk-value'),
    winner:       scope.querySelector('#atRiskWinner .at-risk-value'),
    loser:        scope.querySelector('#atRiskLoser .at-risk-value'),
    capitalAtRisk:scope.querySelector('#atRiskCapitalAtRisk .at-risk-value'),
  };

  if (!listEl || !refreshBtn) return;

  /* ── State ── */
  let autoTimer = null;
  let trades = [];
  let payload = null;
  let accountMode = 'live';
  let viewMode = 'expanded';  // "expanded" | "compact"
  const expandedCards = new Set();
  let monitorData = {};  // keyed by symbol → monitor_result

  /* ── Format helpers ── */
  const _fmt = window.BenTradeUtils.format;
  const toNumber = _fmt.toNumber;
  const fmtDollars = _fmt.dollars;
  const fmtPct = _fmt.signedPct;
  const fmtMoney = _fmt.money;

  function fmtTotal(v) {
    var n = toNumber(v);
    if (n === null) return 'N/A';
    var abs = Math.abs(n);
    var s = abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (n < 0 ? '-$' : '$') + s;
  }

  function fmtSignedTotal(v) {
    var n = toNumber(v);
    if (n === null) return 'N/A';
    var abs = Math.abs(n);
    var s = abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (n >= 0 ? '+$' : '-$') + s;
  }

  function pnlClass(v) {
    var n = toNumber(v);
    if (n === null) return '';
    return n >= 0 ? 'positive' : 'negative';
  }

  function showToast(msg, type) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.className = 'active-toast ' + (type === 'error' ? 'toast-error' : 'toast-success');
    toastEl.style.display = 'block';
    setTimeout(function () { toastEl.style.display = 'none'; }, 4000);
  }

  /* ── Stable key ── */
  function stableKey(trade, idx) {
    if (tradeKeyUtil?.tradeKey) {
      return tradeKeyUtil.tradeKey({
        underlying: trade?.symbol,
        expiration: trade?.expiration,
        spread_type: trade?.strategy_id || trade?.spread_type || trade?.strategy,
        short_strike: trade?.short_strike,
        long_strike: trade?.long_strike,
        dte: trade?.dte,
      });
    }
    return String(trade?.trade_key || trade?.trade_id || idx);
  }

  /* ═══════════════════════════════════════════════════════════════
   * SECTION 1 — Portfolio Risk Bar
   * ═══════════════════════════════════════════════════════════════ */
  function updateRiskBar(filtered) {
    if (!riskEls.positions) return;
    var totalPositions = filtered.length;
    var totalExposure = 0;
    var totalPnl = 0;
    var totalDailyPnl = 0;
    var bestPnl = null;
    var bestSym = '';
    var worstPnl = null;
    var worstSym = '';
    var capitalAtRisk = 0;

    filtered.forEach(function (t) {
      var mv = toNumber(t.market_value) || toNumber(t.cost_basis_total) || 0;
      totalExposure += Math.abs(mv);
      var pnl = toNumber(t.unrealized_pnl) || 0;
      totalPnl += pnl;
      var dc = toNumber(t.day_change);
      if (dc !== null) totalDailyPnl += dc * Math.abs(toNumber(t.quantity) || 1);
      capitalAtRisk += Math.abs(toNumber(t.cost_basis_total) || 0);

      if (bestPnl === null || pnl > bestPnl) { bestPnl = pnl; bestSym = t.symbol; }
      if (worstPnl === null || pnl < worstPnl) { worstPnl = pnl; worstSym = t.symbol; }
    });

    riskEls.positions.textContent = totalPositions;
    riskEls.exposure.textContent = fmtTotal(totalExposure);
    riskEls.exposure.className = 'at-risk-value';

    riskEls.unrealizedPnl.textContent = fmtSignedTotal(totalPnl);
    riskEls.unrealizedPnl.className = 'at-risk-value ' + pnlClass(totalPnl);

    riskEls.dailyPnl.textContent = totalDailyPnl !== 0 ? fmtSignedTotal(totalDailyPnl) : '—';
    riskEls.dailyPnl.className = 'at-risk-value ' + pnlClass(totalDailyPnl);

    if (bestPnl !== null) {
      riskEls.winner.textContent = bestSym + ' ' + fmtSignedTotal(bestPnl);
      riskEls.winner.className = 'at-risk-value positive';
    } else {
      riskEls.winner.textContent = '—';
      riskEls.winner.className = 'at-risk-value';
    }

    if (worstPnl !== null) {
      riskEls.loser.textContent = worstSym + ' ' + fmtSignedTotal(worstPnl);
      riskEls.loser.className = 'at-risk-value negative';
    } else {
      riskEls.loser.textContent = '—';
      riskEls.loser.className = 'at-risk-value';
    }

    riskEls.capitalAtRisk.textContent = fmtTotal(capitalAtRisk);
    riskEls.capitalAtRisk.className = 'at-risk-value';
  }

  /* ═══════════════════════════════════════════════════════════════
   * SECTION 2 — Filtering & Sorting
   * ═══════════════════════════════════════════════════════════════ */
  function hydrateUnderlyingFilter(allTrades) {
    var current = underlyingFilterEl ? underlyingFilterEl.value || 'ALL' : 'ALL';
    var symbols = [...new Set((allTrades || []).map(function(t){ return String(t.symbol||'').toUpperCase(); }).filter(Boolean))].sort();
    if (!underlyingFilterEl) return;
    underlyingFilterEl.innerHTML = '<option value="ALL">All symbols</option>';
    symbols.forEach(function(s) {
      var o = document.createElement('option'); o.value = s; o.textContent = s;
      underlyingFilterEl.appendChild(o);
    });
    underlyingFilterEl.value = symbols.includes(current) ? current : 'ALL';
  }

  function filterTrades() {
    var sym = underlyingFilterEl ? (underlyingFilterEl.value || 'ALL').toUpperCase() : 'ALL';
    var status = statusFilterEl ? (statusFilterEl.value || 'ALL').toUpperCase() : 'ALL';
    var search = searchEl ? (searchEl.value || '').trim().toLowerCase() : '';

    return (trades || []).filter(function (t) {
      var s = String(t.symbol || '').toUpperCase();
      var st = String(t.status || '').toUpperCase();
      var strategy = String(t.strategy || '').toLowerCase();
      if (sym !== 'ALL' && s !== sym) return false;
      if (status !== 'ALL' && st !== status) return false;
      if (search && !(s.toLowerCase().includes(search) || strategy.includes(search))) return false;
      return true;
    });
  }

  function sortTrades(list) {
    var mode = sortSelectEl ? sortSelectEl.value : 'symbol_asc';
    var sorted = list.slice();
    switch (mode) {
      case 'pnl_asc':
        sorted.sort(function(a,b){ return (toNumber(a.unrealized_pnl)||0) - (toNumber(b.unrealized_pnl)||0); });
        break;
      case 'pnl_desc':
        sorted.sort(function(a,b){ return (toNumber(b.unrealized_pnl)||0) - (toNumber(a.unrealized_pnl)||0); });
        break;
      case 'exposure_desc':
        sorted.sort(function(a,b){
          return Math.abs(toNumber(b.market_value)||toNumber(b.cost_basis_total)||0) -
                 Math.abs(toNumber(a.market_value)||toNumber(a.cost_basis_total)||0);
        });
        break;
      case 'symbol_asc':
        sorted.sort(function(a,b){ return (a.symbol||'').localeCompare(b.symbol||''); });
        break;
      case 'strategy_asc':
        sorted.sort(function(a,b){ return (a.strategy||'').localeCompare(b.strategy||''); });
        break;
    }
    return sorted;
  }

  function getFilteredSorted() {
    return sortTrades(filterTrades());
  }

  /* ═══════════════════════════════════════════════════════════════
   * SECTION 3–8 — Position Cards
   * ═══════════════════════════════════════════════════════════════ */

  function buildPnlHeatBar(trade) {
    // PnL heat indicator: bar showing position relative to entry price
    var entry = toNumber(trade.avg_open_price);
    var current = toNumber(trade.mark_price);
    if (entry === null || current === null || entry === 0) return '';
    var ratio = ((current - entry) / entry) * 100;
    var clamped = Math.max(-10, Math.min(10, ratio));
    var pct = ((clamped + 10) / 20) * 100; // 0-100 scale, 50% = breakeven
    var color = ratio >= 0 ? 'rgba(126,247,184,0.8)' : 'rgba(255,79,102,0.8)';
    return '<div class="at-heat-bar">' +
      '<div class="at-heat-track">' +
        '<div class="at-heat-center"></div>' +
        '<div class="at-heat-fill" style="left:' + Math.min(pct, 50) + '%;width:' + Math.abs(pct - 50) + '%;background:' + color + ';"></div>' +
      '</div>' +
    '</div>';
  }

  function buildRiskFlags(trade) {
    var flags = [];
    var pnl = toNumber(trade.unrealized_pnl) || 0;
    var exposure = Math.abs(toNumber(trade.market_value) || toNumber(trade.cost_basis_total) || 0);
    var pnlPct = toNumber(trade.unrealized_pnl_pct) || 0;
    if (pnl < -100) flags.push('<span class="at-risk-flag at-risk-flag-loss" title="Large Unrealized Loss">⚠︎ LOSS</span>');
    if (exposure > 5000) flags.push('<span class="at-risk-flag at-risk-flag-exposure" title="High Exposure">▴ HIGH EXP</span>');
    if (pnlPct < -0.05) flags.push('<span class="at-risk-flag at-risk-flag-drawdown" title="Significant Drawdown">↓ DRAWDOWN</span>');
    return flags.join(' ');
  }

  function renderCards() {
    var filtered = getFilteredSorted();
    updateRiskBar(filtered);
    renderStats(filtered.length);

    if (!filtered.length) {
      listEl.innerHTML =
        '<div class="active-empty-tron">' +
          '<div class="active-empty-title">NO OPEN POSITIONS</div>' +
          '<div class="active-empty-sub">Quantum lane is clear. New positions will appear here.</div>' +
        '</div>';
      return;
    }

    var isCompact = viewMode === 'compact';
    var gridClass = isCompact ? 'at-card-grid at-compact' : 'at-card-grid';
    listEl.className = gridClass;

    listEl.innerHTML = filtered.map(function (trade, idx) {
      var key = stableKey(trade, idx);
      var isOpen = expandedCards.has(key);
      var pnlCl = pnlClass(trade.unrealized_pnl);
      var qty = trade.quantity != null ? trade.quantity : 'N/A';
      var posType = Number(trade.quantity || 0) < 0 ? 'Short' : 'Long';
      var badgeClass = accountMode === 'paper' ? 'badge-paper' : 'badge-live';
      var badgeLabel = accountMode === 'paper' ? 'PAPER' : 'LIVE';
      var strategy = trade.strategy || 'single';
      var costBasis = trade.cost_basis_total != null ? Number(trade.cost_basis_total) : null;
      var marketValue = trade.market_value != null ? Number(trade.market_value) : null;
      var dayChange = toNumber(trade.day_change);
      var dayChangePct = toNumber(trade.day_change_pct);
      var dayChangeStr = dayChange !== null ? fmtMoney(dayChange) : '—';
      var dayChangePctStr = dayChangePct !== null ? _fmt.signedPct(dayChangePct / 100) : '';
      var riskFlags = buildRiskFlags(trade);
      var heatBar = buildPnlHeatBar(trade);
      var asOf = payload?.as_of ? new Date(payload.as_of).toLocaleTimeString() : '—';
      var exposure = Math.abs(marketValue || costBasis || 0);

      /* Monitor chip data */
      var sym = (trade.symbol || '').toUpperCase();
      var mon = monitorData[sym];
      var monChip = '';
      if (mon) {
        var monCls = 'at-monitor-chip at-monitor-' + (mon.status || 'watch').toLowerCase();
        monChip = '<span class="' + monCls + '">' + (mon.status || '?') + ' ' + (mon.score_0_100 != null ? mon.score_0_100 : '?') + '</span>';
      }

      /* ══════════════════════════════════════════════════════════════
       * BenTrade Trade Card — Active Position Variant
       *
       *   .trade-card.at-v2
       *     ├── HEADER (.at-card-header — clickable)
       *     │     main: chev+symbol | strategy·status·dir/qty | P&L
       *     │     meta: account · rec · risk flags · monitor · AI
       *     ├── SUMMARY STRIP (.at-summary-strip — always visible)
       *     │     heat bar + compact P&L / Mark / Exposure
       *     ├── COLLAPSIBLE BODY (.at-card-body)
       *     │     .trade-body (metrics + details)
       *     │     analytics panels
       *     └── FOOTER (.at-card-footer — always visible, bottom-anchored)
       *           run-row · execute+close · show-legs+sim+monitor
       * ══════════════════════════════════════════════════════════════ */
      var _tc = window.BenTradeTradeCard;

      var tradeStatus = String(trade.status || 'OPEN').toUpperCase();
      var statusCls = 'at-status-' + tradeStatus.toLowerCase();
      var recAction = (mon && mon.recommended_action && mon.recommended_action.action)
        ? String(mon.recommended_action.action).toUpperCase()
        : 'HOLD';
      var recCls = 'at-rec-' + recAction.toLowerCase();
      var strategyLabel = String(strategy || 'single').replace(/_/g, ' ');
      strategyLabel = strategyLabel.charAt(0).toUpperCase() + strategyLabel.slice(1);

      var aiStatus = '';
      if (window.BenTradeModelAnalysisStore?.get) {
        var cached = window.BenTradeModelAnalysisStore.get(key);
        if (cached && cached.status === 'success') aiStatus = 'ok';
        else if (cached && cached.status === 'error') aiStatus = 'error';
      }

      var html = '<div class="trade-card at-v2' + (isOpen ? ' at-expanded' : '') + '" data-trade-key="' + key + '">';

      /* ─── 1. HEADER ─── */
      html += '<div class="at-card-header" data-toggle-key="' + key + '">';

      /* Main row: Identity | Info | P&L */
      html += '<div class="at-hdr-main">';
      html += '<div class="at-hdr-identity">';
      html += '<span class="chev"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg></span>';
      html += '<span class="at-symbol-badge">' + (trade.symbol || 'N/A') + '</span>';
      html += '</div>';
      html += '<div class="at-hdr-info">';
      html += '<span class="at-hdr-strategy">' + strategyLabel + '</span>';
      html += '<span class="at-hdr-status ' + statusCls + '">' + tradeStatus + '</span>';
      html += '<span class="at-hdr-pos">' + posType + ' <span class="at-hdr-qty">\u00d7' + Math.abs(qty) + '</span></span>';
      html += '</div>';
      html += '<div class="at-hdr-pnl">';
      html += '<span class="at-pnl-readout ' + (pnlCl || 'neutral') + '">' + fmtSignedTotal(trade.unrealized_pnl) + '</span>';
      html += '</div>';
      html += '</div>'; /* .at-hdr-main */

      /* Meta row: subdued context tags */
      html += '<div class="at-hdr-meta">';
      html += '<span class="at-meta-tag ' + badgeClass + '">' + badgeLabel + '</span>';
      html += '<span class="at-meta-tag ' + recCls + '">' + recAction + '</span>';
      if (monChip) html += monChip;
      if (riskFlags) html += riskFlags;
      if (aiStatus === 'ok') html += '<span class="at-ai-dot at-ai-ok" title="AI analysis available"></span>';
      else if (aiStatus === 'error') html += '<span class="at-ai-dot at-ai-err" title="AI analysis failed"></span>';
      html += '</div>'; /* .at-hdr-meta */

      html += '</div>'; /* .at-card-header */

      /* ─── 2. SUMMARY STRIP (compact, always visible) ─── */
      html += '<div class="at-summary-strip">';
      html += heatBar;
      html += '<div class="at-strip-data">';
      html += '<span class="at-strip-item"><span class="at-strip-lbl">P&L</span><span class="at-strip-val ' + (pnlCl || 'neutral') + '">' + fmtSignedTotal(trade.unrealized_pnl) + '</span></span>';
      html += '<span class="at-strip-sep">\u00b7</span>';
      html += '<span class="at-strip-item"><span class="at-strip-lbl">Mark</span><span class="at-strip-val">' + fmtDollars(trade.mark_price) + '</span></span>';
      html += '<span class="at-strip-sep">\u00b7</span>';
      html += '<span class="at-strip-item"><span class="at-strip-lbl">Exp</span><span class="at-strip-val">' + fmtTotal(exposure) + '</span></span>';
      html += '</div>';
      html += '</div>';

      /* ─── 3. COLLAPSIBLE BODY ─── */
      html += '<div class="at-card-body' + (isOpen ? '' : ' at-collapsed') + '" data-body-key="' + key + '">';

      /* Trade body: sections mirroring standard BenTrade card family */
      html += '<div class="trade-body">';

      /* Section: POSITION METRICS */
      var coreMetrics = [
        { label: 'Current Price',  value: fmtDollars(trade.mark_price),         cssClass: 'neutral',           dataMetric: 'current_price' },
        { label: 'Unrealized P&L', value: fmtSignedTotal(trade.unrealized_pnl), cssClass: pnlCl || 'neutral',  dataMetric: 'unrealized_pnl' },
        { label: 'Position Value', value: fmtTotal(marketValue),                cssClass: 'neutral',           dataMetric: 'position_value' },
        { label: 'Cost Basis',     value: fmtTotal(costBasis),                  cssClass: 'neutral',           dataMetric: 'cost_basis' },
        { label: 'Day Change',     value: dayChangeStr + (dayChangePctStr ? ' <small>' + dayChangePctStr + '</small>' : ''), cssClass: pnlClass(dayChange) || 'neutral', dataMetric: 'day_change' },
        { label: 'Exposure',       value: fmtTotal(exposure),                   cssClass: 'neutral',           dataMetric: 'exposure' },
      ];
      html += _tc.section('POSITION METRICS', _tc.metricGrid(coreMetrics), 'section-core');

      /* Section: POSITION DETAILS */
      var detailItems = [
        { label: 'Avg Entry Price', value: fmtDollars(trade.avg_open_price) || '\u2014', dataMetric: 'avg_entry' },
        { label: 'Last Update',     value: asOf,                                          dataMetric: 'last_update' },
      ];
      html += _tc.section('POSITION DETAILS', _tc.detailRows(detailItems), 'section-details');

      html += '</div>'; /* .trade-body */

      /* Analytics panels */
      html += '<div class="at-analytics" data-analytics-key="' + key + '">';
      html += '<div class="at-panel at-panel-legs" data-panel="legs-' + key + '" style="display:none;"><div class="at-panel-title">Position Breakdown</div><div class="at-panel-content">' + buildLegsTable(trade) + '</div></div>';
      html += '<div class="at-panel at-panel-sim" data-panel="sim-' + key + '" style="display:none;"><div class="at-panel-title">PnL Simulation</div><div class="at-panel-content" data-sim-content="' + key + '"></div></div>';
      html += '<div class="at-panel at-panel-model" data-panel="model-' + key + '" style="display:none;"></div>';
      html += '<div class="at-panel at-panel-monitor" data-panel="monitor-' + key + '" style="display:none;"><div class="at-panel-title">Position Monitor</div><div class="at-panel-content" data-monitor-content="' + key + '">' + buildMonitorPanel(trade) + '</div></div>';
      html += '<div class="at-panel at-panel-notes" data-panel="notes-' + key + '" style="display:none;"><div class="at-panel-title">Trade Notes</div><div class="at-panel-content"><textarea class="at-notes-input" placeholder="Add notes about this position\u2026" rows="3"></textarea></div></div>';
      html += '</div>'; /* .at-analytics */

      html += '</div>'; /* .at-card-body */

      /* ─── 4. FOOTER (always visible — bottom-anchored action zone) ─── */
      html += '<div class="at-card-footer">';
      html += '<div class="at-footer-run"><button class="btn btn-run btn-action" data-action="model-analysis" data-trade-key="' + key + '">Run Model Analysis</button></div>';
      html += '<div class="trade-model-output" data-model-output data-trade-key="' + key + '" style="display:none;"></div>';
      html += '<div class="at-footer-actions">';
      html += '<button class="btn btn-exec btn-action" data-action="execute-trade" data-trade-key="' + key + '">Execute Trade</button>';
      html += '<button class="btn btn-reject btn-action" data-action="close-position" data-trade-key="' + key + '">Close Position</button>';
      html += '</div>';
      html += '<div class="at-footer-tools">';
      html += '<button class="btn at-btn-tool" data-action="show-legs" data-trade-key="' + key + '">Show Legs</button>';
      html += '<button class="btn at-btn-tool" data-action="simulate-close" data-trade-key="' + key + '">Simulate Close</button>';
      html += '<button class="btn at-btn-tool" data-action="show-monitor" data-trade-key="' + key + '">Monitor</button>';
      html += '</div>';
      html += '</div>'; /* .at-card-footer */

      html += '</div>'; /* .trade-card */

      return html;
    }).join('');

    wireCardEvents(filtered);

    if (window.attachMetricTooltips) window.attachMetricTooltips(listEl);
    if (window.BenTradeModelAnalysisStore?.hydrateContainer) window.BenTradeModelAnalysisStore.hydrateContainer(listEl);
  }

  /* ── Build Monitor Panel content ── */
  function buildMonitorPanel(trade) {
    var sym = (trade.symbol || '').toUpperCase();
    var mon = monitorData[sym];
    if (!mon) {
      return '<div class="at-no-data">No monitor data yet. Click <strong>Monitor</strong> or wait for refresh.</div>';
    }

    var html = '';

    /* Status + Score header */
    var statusCls = 'at-mon-status at-mon-status-' + (mon.status || 'watch').toLowerCase();
    html += '<div class="at-mon-header">';
    html += '<span class="' + statusCls + '">' + (mon.status || '?') + '</span>';
    html += '<span class="at-mon-score">Score: <strong>' + (mon.score_0_100 != null ? mon.score_0_100 : '?') + '</strong> / 100</span>';
    html += '</div>';

    /* Recommended action */
    if (mon.recommended_action) {
      html += '<div class="at-mon-action">';
      html += '<span class="at-mon-action-label">Recommended:</span> ';
      html += '<span class="at-mon-action-value">' + (mon.recommended_action.action || '—') + '</span>';
      if (mon.recommended_action.reason_short) {
        html += ' <span class="at-mon-action-reason">— ' + mon.recommended_action.reason_short + '</span>';
      }
      html += '</div>';
    }

    /* Breakdown bars */
    var bd = mon.breakdown || {};
    var factors = [
      { key: 'regime_alignment', label: 'Regime Alignment', max: 25 },
      { key: 'trend_strength',   label: 'Trend Strength',   max: 25 },
      { key: 'drawdown_risk',    label: 'Drawdown Risk',     max: 25 },
      { key: 'volatility_risk',  label: 'Volatility Risk',   max: 15 },
      { key: 'time_in_trade',    label: 'Time in Trade',     max: 10 },
    ];
    html += '<div class="at-mon-breakdown">';
    html += '<div class="at-mon-section-title">Score Breakdown</div>';
    factors.forEach(function (f) {
      var val = bd[f.key] != null ? Number(bd[f.key]) : 0;
      var pct = f.max > 0 ? Math.round((val / f.max) * 100) : 0;
      var barColor = pct >= 70 ? 'rgba(126,247,184,0.8)' : pct >= 40 ? 'rgba(0,234,255,0.7)' : pct >= 20 ? 'rgba(255,193,7,0.7)' : 'rgba(255,79,102,0.7)';
      html += '<div class="at-mon-factor">';
      html += '<div class="at-mon-factor-head">';
      html += '<span class="at-mon-factor-label">' + f.label + '</span>';
      html += '<span class="at-mon-factor-val">' + val.toFixed(1) + ' / ' + f.max + '</span>';
      html += '</div>';
      html += '<div class="at-mon-factor-track"><div class="at-mon-factor-fill" style="width:' + pct + '%;background:' + barColor + ';"></div></div>';
      html += '</div>';
    });
    html += '</div>';

    /* Triggers */
    var triggers = mon.triggers || [];
    if (triggers.length > 0) {
      html += '<div class="at-mon-triggers">';
      html += '<div class="at-mon-section-title">Triggers</div>';
      triggers.forEach(function (t) {
        var lvlCls = 'at-mon-trigger-' + (t.level || 'info').toLowerCase();
        var icon = t.hit ? '●' : '○';
        html += '<div class="at-mon-trigger ' + lvlCls + (t.hit ? ' at-mon-trigger-hit' : '') + '">';
        html += '<span class="at-mon-trigger-icon">' + icon + '</span> ';
        html += '<span class="at-mon-trigger-level">' + (t.level || 'INFO') + '</span> ';
        html += '<span class="at-mon-trigger-msg">' + (t.message || t.id || '—') + '</span>';
        html += '</div>';
      });
      html += '</div>';
    }

    /* Last evaluated */
    if (mon.last_evaluated_ts) {
      var evalDate = new Date(mon.last_evaluated_ts * 1000);
      html += '<div class="at-mon-evaluated">Last evaluated: ' + evalDate.toLocaleTimeString() + '</div>';
    }

    /* Narrative section (initially empty, filled on demand) */
    html += '<div class="at-mon-narrative-section" data-mon-narrative="' + sym + '">';
    html += '<button class="btn at-btn-secondary at-mon-narrative-btn" data-action="run-monitor-narrative" data-symbol="' + sym + '">Run Monitor Analysis</button>';
    html += '<div class="at-mon-narrative-output" data-mon-narrative-output="' + sym + '"></div>';
    html += '</div>';

    return html;
  }

  function buildLegsTable(trade) {
    var legs = trade.legs;
    if (!legs || !legs.length) return '<div class="at-no-data">No leg data available</div>';
    var html = '<table class="at-legs-table"><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th></tr></thead><tbody>';
    legs.forEach(function (leg) {
      html += '<tr>';
      html += '<td>' + (leg.symbol || '—') + '</td>';
      html += '<td>' + (leg.side || '—') + '</td>';
      html += '<td>' + (leg.qty || '—') + '</td>';
      html += '<td>' + fmtDollars(leg.price) + '</td>';
      html += '</tr>';
    });
    html += '</tbody></table>';
    return html;
  }

  /* ═══════════════════════════════════════════════════════════════
   * Event Wiring
   * ═══════════════════════════════════════════════════════════════ */
  function wireCardEvents(filtered) {
    /* Header collapse/expand toggle */
    listEl.querySelectorAll('[data-toggle-key]').forEach(function (hdr) {
      hdr.addEventListener('click', function (e) {
        if (e.target.closest('button') || e.target.closest('a')) return;
        var key = hdr.getAttribute('data-toggle-key');
        var card = hdr.closest('.trade-card');
        var body = listEl.querySelector('[data-body-key="' + key + '"]');
        if (!body) return;
        body.classList.toggle('at-collapsed');
        var isNowOpen = !body.classList.contains('at-collapsed');
        if (card) card.classList.toggle('at-expanded', isNowOpen);
        if (isNowOpen) {
          expandedCards.add(key);
        } else {
          expandedCards.delete(key);
        }
      });
    });

    /* Primary actions */
    listEl.querySelectorAll('[data-action="model-analysis"]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-trade-key');
        var trade = findTrade(filtered, key);
        if (trade) runModelAnalysis(trade, key);
      });
    });
    listEl.querySelectorAll('[data-action="execute-trade"]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-trade-key');
        var trade = findTrade(filtered, key);
        console.info('[ACTIVE_TRADE] execute_trade_click', { symbol: trade?.symbol, strategy: trade?.strategy, key: key });
        if (!trade) {
          console.warn('[ACTIVE_TRADE] action_failure', { action: 'execute-trade', reason: 'trade not found', key: key });
          showToast('Could not find trade data', 'error');
          return;
        }
        openExecuteTrade(trade);
      });
    });
    listEl.querySelectorAll('[data-action="close-position"]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-trade-key');
        var trade = findTrade(filtered, key);
        if (trade) openCloseConfirmation(trade);
      });
    });

    /* Secondary actions */
    listEl.querySelectorAll('[data-action="show-legs"]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-trade-key');
        var trade = findTrade(filtered, key);
        console.info('[ACTIVE_TRADE] show_legs_click', { symbol: trade?.symbol, strategy: trade?.strategy, key: key });
        var panel = listEl.querySelector('[data-panel="legs-' + key + '"]');
        if (!panel) {
          console.warn('[ACTIVE_TRADE] action_failure', { action: 'show-legs', reason: 'panel not found', key: key });
          showToast('Legs panel not available', 'error');
          return;
        }
        /* Expand the card body first so the panel is visible */
        expandCardBody(key);
        togglePanel(panel);
        console.info('[ACTIVE_TRADE] action_success', { action: 'show-legs', key: key, visible: panel.style.display !== 'none' });
      });
    });
    listEl.querySelectorAll('[data-action="simulate-close"]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-trade-key');
        var trade = findTrade(filtered, key);
        if (trade) openSimulateClose(trade);
      });
    });
    listEl.querySelectorAll('[data-action="show-monitor"]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-trade-key');
        var trade = findTrade(filtered, key);
        console.info('[ACTIVE_TRADE] monitor_click', { symbol: trade?.symbol, strategy: trade?.strategy, key: key });
        var panel = listEl.querySelector('[data-panel="monitor-' + key + '"]');
        if (!panel) {
          console.warn('[ACTIVE_TRADE] action_failure', { action: 'show-monitor', reason: 'panel not found', key: key });
          showToast('Monitor panel not available', 'error');
          return;
        }
        /* Expand the card body first so the panel is visible */
        expandCardBody(key);
        togglePanel(panel);
        console.info('[ACTIVE_TRADE] action_success', { action: 'show-monitor', key: key, visible: panel.style.display !== 'none' });
      });
    });

    /* Monitor narrative buttons */
    listEl.querySelectorAll('[data-action="run-monitor-narrative"]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var sym = btn.getAttribute('data-symbol');
        if (!sym) return;
        runMonitorNarrative(sym, btn);
      });
    });
  }

  function findTrade(list, key) {
    return list.find(function (t, i) { return stableKey(t, i) === key; });
  }

  /**
   * Expand the card body for a given trade key.
   * Required when footer buttons (outside .at-card-body) need to reveal
   * panels that live inside the collapsible body.
   */
  function expandCardBody(key) {
    var body = listEl.querySelector('[data-body-key="' + key + '"]');
    if (!body || !body.classList.contains('at-collapsed')) return;
    body.classList.remove('at-collapsed');
    var card = body.closest('.trade-card');
    if (card) card.classList.add('at-expanded');
    expandedCards.add(key);
  }

  function togglePanel(panel) {
    if (panel.style.display === 'none') {
      panel.style.display = 'block';
      panel.classList.add('at-panel-open');
    } else {
      panel.style.display = 'none';
      panel.classList.remove('at-panel-open');
    }
  }

  /* ── Model Analysis ── */
  function runModelAnalysis(trade, tradeKey) {
    var outputEl = listEl.querySelector('.trade-model-output[data-trade-key="' + tradeKey + '"]');
    var panelEl = listEl.querySelector('[data-panel="model-' + tradeKey + '"]');
    if (!panelEl) return;

    /* Show loading in model panel */
    panelEl.style.display = 'block';
    panelEl.classList.add('at-panel-open');
    panelEl.innerHTML = '<div class="at-ai-loading"><span class="at-ai-spinner"></span> Running model analysis…</div>';
    if (outputEl) {
      outputEl.style.display = 'none';
      outputEl.innerHTML = '';
    }

    /* Update AI status indicator in header */
    var headerEl = listEl.querySelector('.at-card-header[data-toggle-key="' + tradeKey + '"]');
    var existingStatus = headerEl ? headerEl.querySelector('.at-ai-status') : null;
    if (existingStatus) existingStatus.outerHTML = '<span class="at-ai-status at-ai-running" title="Running…">AI ⟳</span>';

    /* Expand card body if collapsed so user sees result */
    var bodyEl = listEl.querySelector('[data-body-key="' + tradeKey + '"]');
    var cardEl = panelEl.closest('.trade-card');
    if (bodyEl && bodyEl.classList.contains('at-collapsed')) {
      bodyEl.classList.remove('at-collapsed');
      if (cardEl) cardEl.classList.add('at-expanded');
      expandedCards.add(tradeKey);
    }

    /* Build raw position payload — NO monitor scores or triggers */
    var positionPayload = {
      symbol: trade.symbol, strategy: trade.strategy || 'single',
      quantity: trade.quantity, avg_open_price: trade.avg_open_price,
      mark_price: trade.mark_price, cost_basis_total: trade.cost_basis_total,
      market_value: trade.market_value, unrealized_pnl: trade.unrealized_pnl,
      unrealized_pnl_pct: trade.unrealized_pnl_pct,
      day_change: trade.day_change, status: trade.status,
    };

    if (!api.analyzeActiveTrade) {
      panelEl.innerHTML = '<div class="at-ai-error">Active trade analysis not available.</div>';
      return;
    }

    api.analyzeActiveTrade(trade.symbol, positionPayload, accountMode)
      .then(function (result) {
        if (!result || !result.ok) {
          var errMsg = (result && result.error) ? result.error.message : 'Unknown error';
          var errKind = (result && result.error && result.error.kind) ? ' (' + result.error.kind + ')' : '';
          var errHtml = '<div class="at-ai-error">' +
            '<span class="at-ai-error-icon">✗</span> ' + escapeHtml(errMsg) + escapeHtml(errKind) +
            '<button class="btn at-btn-secondary at-ai-retry" data-action="model-analysis" data-trade-key="' + tradeKey + '" style="margin-left:10px;">Retry</button>' +
            '</div>';
          panelEl.innerHTML = errHtml;
          _updateAiStatusBadge(headerEl, 'error');
          return;
        }
        var a = result.analysis || {};
        var rendered = renderActiveTradeAnalysis(a, result.context_used, tradeKey);
        panelEl.innerHTML = '<div class="at-panel-title">Active Trade Analysis</div><div class="at-panel-content">' + rendered + '</div>';
        _updateAiStatusBadge(headerEl, 'ok');
        if (window.BenTradeModelAnalysisStore?.set) {
          window.BenTradeModelAnalysisStore.set(tradeKey, { status: 'success', result: result });
        }
      })
      .catch(function (err) {
        var errHtml = '<div class="at-ai-error">' +
          '<span class="at-ai-error-icon">✗</span> ' + escapeHtml(err.message || String(err)) +
          '<button class="btn at-btn-secondary at-ai-retry" data-action="model-analysis" data-trade-key="' + tradeKey + '" style="margin-left:10px;">Retry</button>' +
          '</div>';
        panelEl.innerHTML = errHtml;
        _updateAiStatusBadge(headerEl, 'error');
      });
  }

  function _updateAiStatusBadge(headerEl, status) {
    if (!headerEl) return;
    var existing = headerEl.querySelector('.at-ai-status');
    var newBadge = '';
    if (status === 'ok') newBadge = '<span class="at-ai-status at-ai-ok" title="AI analysis available">AI ✓</span>';
    else if (status === 'error') newBadge = '<span class="at-ai-status at-ai-err" title="AI analysis failed">AI ✗</span>';
    else if (status === 'running') newBadge = '<span class="at-ai-status at-ai-running" title="Running…">AI ⟳</span>';
    if (existing) existing.outerHTML = newBadge;
    else if (newBadge) {
      var chevron = headerEl.querySelector('.at-hdr-chevron');
      if (chevron) chevron.insertAdjacentHTML('beforebegin', newBadge);
    }
  }

  /* ── Render structured Active Trade Analysis result ── */
  function renderActiveTradeAnalysis(analysis, ctx, tradeKey) {
    var stance = analysis.stance || analysis.suggested_action || 'HOLD';
    var conf = analysis.confidence != null ? analysis.confidence : 0;
    // Handle old 0–1 scale: if confidence is a float ≤1, convert to 0–100
    if (typeof conf === 'number' && conf > 0 && conf <= 1) conf = Math.round(conf * 100);
    var thesisStatus = analysis.thesis_status || 'INTACT';
    var headline = analysis.headline || analysis.one_sentence_summary || '';
    var summary = analysis.summary || analysis.one_sentence_summary || '';
    var risks = analysis.key_risks || analysis.risk_flags || [];
    var supports = analysis.key_supports || [];
    var ts = analysis.technical_state || {};
    var ap = analysis.action_plan || {};
    var memo = analysis.memo || {};
    var pc = analysis.position_context || {};

    /* Stance color class */
    var stanceCls = 'at-ai-stance-watch';
    if (stance === 'HOLD') stanceCls = 'at-ai-stance-hold';
    else if (stance === 'ADD') stanceCls = 'at-ai-stance-add';
    else if (stance === 'REDUCE') stanceCls = 'at-ai-stance-reduce';
    else if (stance === 'EXIT' || stance === 'CLOSE') stanceCls = 'at-ai-stance-exit';

    /* Thesis status color class */
    var thesisCls = 'at-ai-thesis-intact';
    if (thesisStatus === 'WEAKENING') thesisCls = 'at-ai-thesis-weakening';
    else if (thesisStatus === 'BROKEN') thesisCls = 'at-ai-thesis-broken';

    /* Urgency color class */
    var urgency = (ap.urgency || 'LOW').toUpperCase();
    var urgencyCls = 'at-ai-urgency-low';
    if (urgency === 'MEDIUM') urgencyCls = 'at-ai-urgency-medium';
    else if (urgency === 'HIGH') urgencyCls = 'at-ai-urgency-high';

    /* Confidence color class */
    var confCls = 'at-ai-conf-low';
    if (conf >= 70) confCls = 'at-ai-conf-high';
    else if (conf >= 40) confCls = 'at-ai-conf-mid';

    var html = '<div class="at-ai-card at-ai-memo">';

    /* ── 1. Header: headline + badges ── */
    html += '<div class="at-ai-memo-header">';
    if (headline) {
      html += '<div class="at-ai-headline">' + escapeHtml(headline) + '</div>';
    }
    html += '<div class="at-ai-badges">';
    html += '<span class="at-ai-action-badge ' + stanceCls + '">' + escapeHtml(stance) + '</span>';
    html += '<span class="at-ai-badge ' + confCls + '">Confidence ' + conf + '%</span>';
    html += '<span class="at-ai-badge ' + thesisCls + '">Thesis ' + escapeHtml(thesisStatus) + '</span>';
    html += '</div></div>';

    /* ── 2. Executive Summary ── */
    if (summary) {
      html += '<div class="at-ai-summary">' + escapeHtml(summary) + '</div>';
    }

    /* ── 3. Position Snapshot ── */
    if (pc.entry_price != null || pc.current_price != null || pc.pnl_dollar != null) {
      html += '<div class="at-ai-section">';
      html += '<div class="at-ai-section-title">Position Snapshot</div>';
      html += '<div class="at-ai-snapshot-grid">';
      if (pc.entry_price != null) html += '<div class="at-ai-snap"><span class="at-ai-snap-label">Entry</span><span class="at-ai-snap-val">$' + Number(pc.entry_price).toFixed(2) + '</span></div>';
      if (pc.current_price != null) html += '<div class="at-ai-snap"><span class="at-ai-snap-label">Current</span><span class="at-ai-snap-val">$' + Number(pc.current_price).toFixed(2) + '</span></div>';
      if (pc.pnl_dollar != null) {
        var pnlCls = pc.pnl_dollar >= 0 ? 'at-ai-pnl-pos' : 'at-ai-pnl-neg';
        html += '<div class="at-ai-snap"><span class="at-ai-snap-label">P&amp;L $</span><span class="at-ai-snap-val ' + pnlCls + '">' + (pc.pnl_dollar >= 0 ? '+' : '') + Number(pc.pnl_dollar).toFixed(2) + '</span></div>';
      }
      if (pc.pnl_percent != null) {
        var pctCls = pc.pnl_percent >= 0 ? 'at-ai-pnl-pos' : 'at-ai-pnl-neg';
        html += '<div class="at-ai-snap"><span class="at-ai-snap-label">P&amp;L %</span><span class="at-ai-snap-val ' + pctCls + '">' + (pc.pnl_percent >= 0 ? '+' : '') + Number(pc.pnl_percent).toFixed(2) + '%</span></div>';
      }
      if (pc.score != null) html += '<div class="at-ai-snap"><span class="at-ai-snap-label">Score</span><span class="at-ai-snap-val">' + pc.score + '</span></div>';
      html += '</div></div>';
    }

    /* ── 4. Key Risks ── */
    if (risks.length) {
      html += '<div class="at-ai-section">';
      html += '<div class="at-ai-section-title">Key Risks</div>';
      html += '<ul class="at-ai-risks">';
      risks.forEach(function (r) { html += '<li>⚠ ' + escapeHtml(r) + '</li>'; });
      html += '</ul></div>';
    }

    /* ── 5. Key Supports ── */
    if (supports.length) {
      html += '<div class="at-ai-section">';
      html += '<div class="at-ai-section-title">Key Supports</div>';
      html += '<ul class="at-ai-supports">';
      supports.forEach(function (s) { html += '<li>✓ ' + escapeHtml(s) + '</li>'; });
      html += '</ul></div>';
    }

    /* ── 6. Technical State ── */
    if (ts.trend_assessment || ts.drawdown_assessment || ts.price_vs_sma20 || ts.price_vs_sma50) {
      html += '<div class="at-ai-section">';
      html += '<div class="at-ai-section-title">Technical State</div>';
      html += '<div class="at-ai-tech-grid">';
      if (ts.price_vs_sma20) html += '<div class="at-ai-tech-row"><span class="at-ai-tech-label">Price vs SMA20</span><span class="at-ai-tech-val">' + escapeHtml(ts.price_vs_sma20) + '</span></div>';
      if (ts.price_vs_sma50) html += '<div class="at-ai-tech-row"><span class="at-ai-tech-label">Price vs SMA50</span><span class="at-ai-tech-val">' + escapeHtml(ts.price_vs_sma50) + '</span></div>';
      if (ts.trend_assessment) html += '<div class="at-ai-tech-row at-ai-tech-wide"><span class="at-ai-tech-label">Trend</span><span class="at-ai-tech-val">' + escapeHtml(ts.trend_assessment) + '</span></div>';
      if (ts.drawdown_assessment) html += '<div class="at-ai-tech-row at-ai-tech-wide"><span class="at-ai-tech-label">Drawdown</span><span class="at-ai-tech-val">' + escapeHtml(ts.drawdown_assessment) + '</span></div>';
      html += '</div></div>';
    }

    /* ── 7. Action Plan ── */
    if (ap.primary_action || ap.next_step || ap.risk_trigger || ap.upside_trigger) {
      html += '<div class="at-ai-section">';
      html += '<div class="at-ai-section-title">Action Plan';
      html += ' <span class="at-ai-badge at-ai-badge-sm ' + urgencyCls + '">' + escapeHtml(urgency) + '</span>';
      html += '</div>';
      html += '<div class="at-ai-action-grid">';
      if (ap.primary_action) html += '<div class="at-ai-action-row"><span class="at-ai-action-label">Action</span><span class="at-ai-action-val">' + escapeHtml(ap.primary_action) + '</span></div>';
      if (ap.next_step) html += '<div class="at-ai-action-row"><span class="at-ai-action-label">Next Step</span><span class="at-ai-action-val">' + escapeHtml(ap.next_step) + '</span></div>';
      if (ap.risk_trigger) html += '<div class="at-ai-action-row"><span class="at-ai-action-label">Risk Trigger</span><span class="at-ai-action-val at-ai-trigger-risk">' + escapeHtml(ap.risk_trigger) + '</span></div>';
      if (ap.upside_trigger) html += '<div class="at-ai-action-row"><span class="at-ai-action-label">Upside Trigger</span><span class="at-ai-action-val at-ai-trigger-upside">' + escapeHtml(ap.upside_trigger) + '</span></div>';
      html += '</div></div>';
    }

    /* ── 8. Analyst Memo ── */
    if (memo.thesis_check || memo.what_changed || memo.decision) {
      html += '<div class="at-ai-section at-ai-memo-section">';
      html += '<div class="at-ai-section-title">Analyst Memo</div>';
      if (memo.thesis_check) html += '<div class="at-ai-memo-row"><span class="at-ai-memo-label">Thesis Check</span><span class="at-ai-memo-val">' + escapeHtml(memo.thesis_check) + '</span></div>';
      if (memo.what_changed) html += '<div class="at-ai-memo-row"><span class="at-ai-memo-label">What Changed</span><span class="at-ai-memo-val">' + escapeHtml(memo.what_changed) + '</span></div>';
      if (memo.decision) html += '<div class="at-ai-memo-row"><span class="at-ai-memo-label">Decision</span><span class="at-ai-memo-val at-ai-memo-decision">' + escapeHtml(memo.decision) + '</span></div>';
      html += '</div>';
    }

    /* ── Data Context (collapsible) ── */
    if (ctx) {
      html += '<details class="at-ai-context">';
      html += '<summary>Data context used</summary>';
      html += '<div class="at-ai-context-body">';
      html += 'Regime: ' + escapeHtml(String(ctx.regime || 'N/A'));
      html += ' (score: ' + (ctx.regime_score != null ? ctx.regime_score : 'N/A') + ')';
      html += ' · SMA20: ' + (ctx.sma20 != null ? '$' + Number(ctx.sma20).toFixed(2) : 'N/A');
      html += ' · SMA50: ' + (ctx.sma50 != null ? '$' + Number(ctx.sma50).toFixed(2) : 'N/A');
      html += ' · RSI14: ' + (ctx.rsi14 != null ? Number(ctx.rsi14).toFixed(1) : 'N/A');
      html += '</div></details>';
    }

    html += '</div>'; /* .at-ai-card */
    return html;
  }

  /* ── Simulate Close ── */
  function openSimulateClose(trade) {
    if (!modalEl || !modalBodyEl) return;
    var mark = trade?.mark_price;
    var quantity = Number(trade?.quantity || 0);
    var multiplier = trade?.option_type ? 100 : 1;

    if (mark === null || mark === undefined || Number.isNaN(Number(mark))) {
      modalBodyEl.innerHTML = '<div class="active-modal-note">Mark unavailable</div>';
    } else {
      var estClose = Number(mark) * quantity * multiplier;
      var pnl = (trade?.unrealized_pnl !== null && trade?.unrealized_pnl !== undefined)
        ? Number(trade.unrealized_pnl) : null;
      modalBodyEl.innerHTML =
        '<div class="active-modal-row"><span>Trade</span><strong>' + trade.symbol + ' • ' + (trade.strategy||'single') + '</strong></div>' +
        '<div class="active-modal-row"><span>Est. Close Value</span><strong>' + fmtTotal(estClose) + '</strong></div>' +
        '<div class="active-modal-row"><span>Est. P&L if Closed</span><strong>' + (pnl !== null ? fmtSignedTotal(pnl) : 'N/A') + '</strong></div>';
    }
    if (window.attachMetricTooltips) window.attachMetricTooltips(modalBodyEl);
    modalEl.style.display = 'flex';
  }

  /* ── Close Position ── */
  function openCloseConfirmation(trade) {
    if (!closeConfirmModal || !closeConfirmBody) return;
    var sym = trade.symbol || 'N/A';
    var qty = Math.abs(Number(trade.quantity || 0));
    var side = Number(trade.quantity || 0) < 0 ? 'Short' : 'Long';
    var modeLabel = accountMode === 'paper' ? 'PAPER' : 'LIVE';
    var markStr = trade.mark_price != null ? fmtDollars(trade.mark_price) : 'market';

    closeConfirmBody.innerHTML =
      '<div class="active-modal-row"><span>Symbol</span><strong>' + sym + '</strong></div>' +
      '<div class="active-modal-row"><span>Direction</span><strong>' + side + ' → Close</strong></div>' +
      '<div class="active-modal-row"><span>Quantity</span><strong>' + qty + ' shares</strong></div>' +
      '<div class="active-modal-row"><span>Order Type</span><strong>Market</strong></div>' +
      '<div class="active-modal-row"><span>Last Price</span><strong>' + markStr + '</strong></div>' +
      '<div class="active-modal-row"><span>Account</span><strong>' + modeLabel + '</strong></div>' +
      '<div style="margin-top:14px;display:flex;gap:10px;">' +
        '<button class="btn btn-exec" id="activeCloseConfirmBtn" style="flex:1;">Confirm Close</button>' +
        '<button class="btn btn-reject" id="activeCloseCancelBtn" style="flex:1;">Cancel</button>' +
      '</div>';

    closeConfirmModal.style.display = 'flex';

    var confirmBtn = closeConfirmBody.querySelector('#activeCloseConfirmBtn');
    var cancelBtn = closeConfirmBody.querySelector('#activeCloseCancelBtn');
    cancelBtn.addEventListener('click', function () { closeConfirmModal.style.display = 'none'; });

    confirmBtn.addEventListener('click', async function () {
      confirmBtn.disabled = true;
      confirmBtn.textContent = 'Submitting…';
      try {
        var result = await api.closePosition({
          symbol: sym, quantity: qty,
          side: side.toLowerCase() === 'short' ? 'sell' : 'buy',
          account_mode: accountMode,
        });
        closeConfirmModal.style.display = 'none';
        if (result?.ok) {
          showToast('Closed ' + qty + ' shares of ' + sym, 'success');
          setTimeout(function () { refresh(); }, 1500);
        } else {
          showToast((result?.error?.message || 'Close order failed'), 'error');
        }
      } catch (err) {
        closeConfirmModal.style.display = 'none';
        showToast('Close order failed: ' + (err.message || err), 'error');
      }
    });
  }

  /* ── Execute Trade ── */
  function openExecuteTrade(trade) {
    if (window.BenTradeTradeTicket?.open) {
      console.info('[ACTIVE_TRADE] execute_trade opening trade ticket', { symbol: trade.symbol, strategy: trade.strategy });
      window.BenTradeTradeTicket.open({
        symbol: trade.symbol, underlying: trade.symbol,
        strategy: trade.strategy || 'single', strategy_id: trade.strategy_id || 'single',
        quantity: 1, trade_key: stableKey(trade, 0), account_mode: accountMode,
      }).then(function () {
        console.info('[ACTIVE_TRADE] action_success', { action: 'execute-trade', symbol: trade.symbol });
      }).catch(function (err) {
        console.error('[ACTIVE_TRADE] action_failure', { action: 'execute-trade', symbol: trade.symbol, error: err?.message || err });
        showToast('Execute Trade failed: ' + (err?.message || 'Unknown error'), 'error');
      });
      return;
    }
    if (window.BenTradeStockExecution?.open) {
      console.info('[ACTIVE_TRADE] execute_trade opening stock execution', { symbol: trade.symbol });
      window.BenTradeStockExecution.open({
        symbol: trade.symbol, side: 'buy', quantity: 1, account_mode: accountMode,
      });
      return;
    }
    console.warn('[ACTIVE_TRADE] action_failure', { action: 'execute-trade', reason: 'no trade ticket or stock execution module available' });
    showToast('Execute Trade modal not available — trade ticket module not loaded', 'error');
  }

  /* ═══════════════════════════════════════════════════════════════
   * Monitor — Fetch & Narrative
   * ═══════════════════════════════════════════════════════════════ */

  /**
   * Fetch monitor results for all open positions and re-render cards
   * to update status chips and panel contents.
   */
  async function fetchMonitor() {
    if (!api.getMonitorResults) return;
    try {
      var res = await api.getMonitorResults(accountMode);
      if (res && res.monitor_results) {
        monitorData = res.monitor_results;
        console.log('[active-trades] monitor updated', Object.keys(monitorData).length, 'symbols');
        renderCards();
      }
    } catch (err) {
      console.warn('[active-trades] monitor fetch failed', err);
      // Non-critical — don't block the dashboard
    }
  }

  /**
   * Request an LLM-powered narrative for a single position's monitor result.
   * @param {string} sym — uppercase symbol
   * @param {HTMLElement} btn — the button clicked (for loading state)
   */
  async function runMonitorNarrative(sym, btn) {
    if (!api.getMonitorNarrative) {
      showToast('Monitor narrative API not available', 'error');
      return;
    }
    var mon = monitorData[sym];
    if (!mon) {
      showToast('No monitor data for ' + sym + '. Run a refresh first.', 'error');
      return;
    }

    /* Find the matching trade to send position context */
    var trade = (trades || []).find(function (t) {
      return (t.symbol || '').toUpperCase() === sym;
    });
    var position = trade ? {
      symbol: trade.symbol,
      quantity: trade.quantity,
      avg_open_price: trade.avg_open_price,
      mark_price: trade.mark_price,
      cost_basis_total: trade.cost_basis_total,
      market_value: trade.market_value,
      unrealized_pnl: trade.unrealized_pnl,
      unrealized_pnl_pct: trade.unrealized_pnl_pct,
      strategy: trade.strategy,
    } : { symbol: sym };

    /* Update UI to loading state */
    var outputEl = listEl.querySelector('[data-mon-narrative-output="' + sym + '"]');
    if (outputEl) outputEl.innerHTML = '<div class="at-loading">Generating narrative…</div>';
    if (btn) { btn.disabled = true; btn.textContent = 'Analyzing…'; }

    try {
      var result = await api.getMonitorNarrative(sym, position, mon);
      if (outputEl) {
        if (result && result.structured) {
          outputEl.innerHTML = renderMonitorMemo(result.structured);
        } else if (result && result.narrative) {
          outputEl.innerHTML = '<div class="at-mon-narrative-text">' + escapeHtml(stripThinkTags(result.narrative)) + '</div>';
        } else if (result && result.error) {
          var kind = (result.error.kind || '');
          outputEl.innerHTML = '<div class="at-error-inline">' + escapeHtml(result.error.message || 'Model analysis failed') + '</div>';
        } else {
          outputEl.innerHTML = '<div class="at-no-data">No narrative returned.</div>';
        }
      }
    } catch (err) {
      if (outputEl) {
        outputEl.innerHTML = '<div class="at-error-inline">' + escapeHtml(err.message || String(err)) + '</div>';
      }
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Run Monitor Analysis'; }
    }
  }

  /** Render a structured monitor memo object into styled HTML. */
  function renderMonitorMemo(memo) {
    if (!memo) return '<div class="at-no-data">No analysis available.</div>';
    var label = memo.label || 'WATCH';
    var conf = memo.confidence != null ? memo.confidence : 0;
    var summary = memo.summary || '';
    var thesis = memo.thesis_check || '';
    var risks = memo.key_risks || [];
    var action = memo.action || '';

    var labelCls = 'at-ai-stance-watch';
    if (label === 'HOLD') labelCls = 'at-ai-stance-hold';
    else if (label === 'ADD') labelCls = 'at-ai-stance-add';
    else if (label === 'REDUCE') labelCls = 'at-ai-stance-reduce';
    else if (label === 'EXIT') labelCls = 'at-ai-stance-exit';

    var confCls = 'at-ai-conf-low';
    if (conf >= 70) confCls = 'at-ai-conf-high';
    else if (conf >= 40) confCls = 'at-ai-conf-mid';

    var html = '<div class="at-ai-card at-ai-memo at-mon-memo">';
    html += '<div class="at-ai-memo-header">';
    html += '<div class="at-ai-badges">';
    html += '<span class="at-ai-action-badge ' + labelCls + '">' + escapeHtml(label) + '</span>';
    html += '<span class="at-ai-badge ' + confCls + '">Confidence ' + conf + '%</span>';
    html += '</div></div>';

    if (summary) {
      html += '<div class="at-ai-section"><div class="at-ai-section-title">Summary</div>';
      html += '<div class="at-ai-summary">' + escapeHtml(summary) + '</div></div>';
    }
    if (thesis) {
      html += '<div class="at-ai-section"><div class="at-ai-section-title">Thesis Check</div>';
      html += '<div class="at-ai-summary">' + escapeHtml(thesis) + '</div></div>';
    }
    if (risks.length) {
      html += '<div class="at-ai-section"><div class="at-ai-section-title">Key Risks</div>';
      html += '<ul class="at-ai-risks">';
      risks.forEach(function (r) { html += '<li>⚠ ' + escapeHtml(r) + '</li>'; });
      html += '</ul></div>';
    }
    if (action) {
      html += '<div class="at-ai-section"><div class="at-ai-section-title">Recommended Action</div>';
      html += '<div class="at-ai-summary">' + escapeHtml(action) + '</div></div>';
    }
    html += '</div>';
    return html;
  }

  /** Strip <think>...</think> and similar reasoning tags from text (frontend safety net). */
  function stripThinkTags(text) {
    if (!text) return '';
    text = text.replace(/<think>[\s\S]*?<\/think>/gi, '');
    text = text.replace(/<think>[\s\S]*$/gi, '');
    text = text.replace(/<scratchpad>[\s\S]*?<\/scratchpad>/gi, '');
    text = text.replace(/<\/?(?:think|scratchpad|reasoning|thought|internal)>/gi, '');
    return text.trim();
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  /* ═══════════════════════════════════════════════════════════════
   * Misc UI helpers
   * ═══════════════════════════════════════════════════════════════ */
  function setLiveBadge(asOf) {
    if (!liveBadgeEl) return;
    if (!asOf) {
      liveBadgeEl.textContent = 'STALE';
      liveBadgeEl.classList.remove('is-live');
      return;
    }
    var ageMs = Date.now() - new Date(asOf).getTime();
    var isLive = Number.isFinite(ageMs) && ageMs >= 0 && ageMs <= 90000;
    liveBadgeEl.textContent = isLive ? 'LIVE' : 'STALE';
    liveBadgeEl.classList.toggle('is-live', isLive);
  }

  function renderSourceHealth(sh) {
    if (sourceHealthUi?.renderFromSnapshot) sourceHealthUi.renderFromSnapshot(sh || {});
  }

  function renderStats(count) {
    var statsEl = document.getElementById('reportStatsGrid');
    if (!statsEl) return;
    var ml = accountMode === 'paper' ? 'Paper' : 'Live';
    statsEl.innerHTML =
      '<div class="statTile"><div class="statLabel">Dashboard</div><div class="statValue">Active Trades</div></div>' +
      '<div class="statTile"><div class="statLabel" data-metric="trade_source">Source</div><div class="statValue">Tradier</div></div>' +
      '<div class="statTile"><div class="statLabel" data-metric="open_trades">Open</div><div class="statValue">' + count + '</div></div>' +
      '<div class="statTile"><div class="statLabel" data-metric="trade_mode">Account</div><div class="statValue">' + ml + '</div></div>';
  }

  function setError(text) {
    if (!errorEl) return;
    if (!text) { errorEl.style.display = 'none'; errorEl.textContent = ''; return; }
    errorEl.style.display = 'block'; errorEl.textContent = text;
  }

  /* ═══════════════════════════════════════════════════════════════
   * Refresh
   * ═══════════════════════════════════════════════════════════════ */
  var _cache = window.BenTradeDashboardCache;
  var CACHE_KEY = 'activeTrades';

  function setRefreshState(refreshing) {
    if (refreshing) {
      refreshBtn.disabled = true;
      refreshBtn.classList.add('btn-refreshing');
      refreshBtn.innerHTML = '<span class="btn-spinner"></span>Refreshing\u2026';
    } else {
      refreshBtn.disabled = false;
      refreshBtn.classList.remove('btn-refreshing');
      refreshBtn.innerHTML = 'Refresh';
    }
  }

  async function refresh() {
    try {
      setError('');
      setRefreshState(true);
      payload = await api.getActiveTrades(accountMode);

      if (payload?.error) {
        var em = typeof payload.error === 'object'
          ? (payload.error.message || JSON.stringify(payload.error))
          : String(payload.error);
        setError(em);
      }

      trades = Array.isArray(payload?.active_trades) ? payload.active_trades : [];
      console.log('[active-trades] mode=' + accountMode + ' trades=' + trades.length);
      hydrateUnderlyingFilter(trades);
      renderSourceHealth(payload?.source_health || {});
      setLiveBadge(payload?.as_of);
      renderCards();

      if (_cache) _cache.set(CACHE_KEY, payload);

      /* Kick off monitor evaluation in background (non-blocking) */
      if (trades.length > 0) {
        fetchMonitor();
      }
    } catch (err) {
      console.error('[active-trades] refresh failed', err);
      var errText = 'Failed to load active trades';
      if (err && typeof err === 'object') {
        var status = err.status ? ' (' + err.status + ')' : '';
        var detail = err.detail || err.message || '';
        var body = err.bodySnippet || '';
        var um = err.payload?.error?.message || err.payload?.error?.upstream_body_snippet || '';
        var us = err.payload?.error?.upstream_status;
        if (um) errText = 'Tradier' + (us ? ' ' + us : '') + status + ': ' + um;
        else if (detail) errText = detail + status;
        else if (body) errText = 'Error' + status + ': ' + body.slice(0, 200);
        else errText = 'Failed to load active trades' + status;
      }
      setError(errText);
      trades = [];
      renderCards();
    } finally {
      setRefreshState(false);
    }
  }

  /* ═══════════════════════════════════════════════════════════════
   * Event Binding
   * ═══════════════════════════════════════════════════════════════ */
  function setupAutoRefresh() {
    if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
    if (autoRefreshEl?.checked) autoTimer = setInterval(refresh, 30000);
  }

  refreshBtn.addEventListener('click', refresh);

  /* Delegated handler for dynamically-injected retry buttons */
  listEl.addEventListener('click', function (e) {
    var retryBtn = e.target.closest('.at-ai-retry[data-action="model-analysis"]');
    if (!retryBtn) return;
    var key = retryBtn.getAttribute('data-trade-key');
    if (!key) return;
    var idx = trades.findIndex(function (t, i) { return stableKey(t, i) === key; });
    if (idx === -1) return;
    runModelAnalysis(trades[idx], key);
  });

  if (underlyingFilterEl) underlyingFilterEl.addEventListener('change', renderCards);
  if (statusFilterEl) statusFilterEl.addEventListener('change', renderCards);
  if (searchEl) searchEl.addEventListener('input', renderCards);
  if (sortSelectEl) sortSelectEl.addEventListener('change', renderCards);
  if (autoRefreshEl) autoRefreshEl.addEventListener('change', setupAutoRefresh);

  /* View toggle */
  if (viewToggleEl) {
    viewToggleEl.querySelectorAll('.at-view-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var v = btn.getAttribute('data-view');
        if (!v || v === viewMode) return;
        viewMode = v;
        viewToggleEl.querySelectorAll('.at-view-btn').forEach(function (b) {
          b.classList.toggle('is-active', b.getAttribute('data-view') === viewMode);
        });
        renderCards();
      });
    });
  }

  /* Expand / Collapse All */
  if (expandAllBtn) {
    expandAllBtn.addEventListener('click', function () {
      var bodies = listEl.querySelectorAll('.at-card-body');
      var anyCollapsed = false;
      bodies.forEach(function (b) { if (b.classList.contains('at-collapsed')) anyCollapsed = true; });
      bodies.forEach(function (b) {
        var key = b.getAttribute('data-body-key');
        var card = b.closest('.trade-card');
        if (anyCollapsed) {
          b.classList.remove('at-collapsed');
          if (card) card.classList.add('at-expanded');
          if (key) expandedCards.add(key);
        } else {
          b.classList.add('at-collapsed');
          if (card) card.classList.remove('at-expanded');
          if (key) expandedCards.delete(key);
        }
      });
      expandAllBtn.textContent = anyCollapsed ? '▲ Collapse All' : '▼ Expand All';
    });
  }

  /* Modal dismiss */
  if (modalCloseBtn && modalEl) {
    modalCloseBtn.addEventListener('click', function () { modalEl.style.display = 'none'; });
    modalEl.addEventListener('click', function (e) { if (e.target === modalEl) modalEl.style.display = 'none'; });
  }
  if (closeConfirmDismiss && closeConfirmModal) {
    closeConfirmDismiss.addEventListener('click', function () { closeConfirmModal.style.display = 'none'; });
    closeConfirmModal.addEventListener('click', function (e) { if (e.target === closeConfirmModal) closeConfirmModal.style.display = 'none'; });
  }

  /* Account toggle */
  if (accountToggleEl) {
    accountToggleEl.querySelectorAll('.active-account-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var mode = btn.getAttribute('data-mode');
        if (!mode || mode === accountMode) return;
        accountMode = mode;
        accountToggleEl.querySelectorAll('.active-account-btn').forEach(function (b) {
          b.classList.toggle('is-active', b.getAttribute('data-mode') === accountMode);
        });
        console.log('[active-trades] account toggle →', accountMode);
        refresh();
      });
    });
  }

  /* ── Boot ── */
  // Render cached data immediately if available
  var _cached = _cache && _cache.get(CACHE_KEY);
  if (_cached && _cached.isLoaded && _cached.data) {
    payload = _cached.data;
    trades = Array.isArray(payload?.active_trades) ? payload.active_trades : [];
    hydrateUnderlyingFilter(trades);
    renderSourceHealth(payload?.source_health || {});
    setLiveBadge(payload?.as_of);
    renderCards();
  }
  // Always fetch fresh data in background
  refresh();
};
