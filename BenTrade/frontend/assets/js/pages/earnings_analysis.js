/**
 * Earnings Analysis dashboard.
 * Consumes the EVA proxy at /api/eva/* and renders a sortable table of
 * upcoming earnings for the universe, with a side panel showing the
 * full feature snapshot for the selected event.
 *
 * Setup score formula (heuristic v1) — see _computeSetupScore() below.
 *
 * Init:  window.BenTradePages.initEarningsAnalysis(rootEl)
 */
(function () {
  'use strict';

  window.BenTradePages = window.BenTradePages || {};

  // ── Shared rendering helpers (also used by On Demand) ────────────
  var EAShared = window.BenTradeEAShared = window.BenTradeEAShared || {};

  function _esc(s) {
    if (s == null) return '';
    var d = document.createElement('span');
    d.textContent = String(s);
    return d.innerHTML;
  }
  function _num(v, dp) {
    if (v == null || v === '' || (typeof v === 'number' && !isFinite(v))) return '—';
    var n = Number(v);
    if (!isFinite(n)) return '—';
    return n.toFixed(dp == null ? 2 : dp);
  }
  function _pct(v, dp) {
    if (v == null || v === '') return '—';
    var n = Number(v);
    if (!isFinite(n)) return '—';
    return n.toFixed(dp == null ? 2 : dp) + '%';
  }
  function _fmtDate(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: '2-digit' });
  }
  function _businessDaysUntil(iso) {
    if (!iso) return null;
    var target = new Date(iso);
    if (isNaN(target.getTime())) return null;
    var today = new Date();
    today.setHours(0, 0, 0, 0);
    target.setHours(0, 0, 0, 0);
    var ms = target - today;
    if (ms < 0) return 0;
    var calDays = Math.floor(ms / (24 * 3600 * 1000));
    // Approximate trading days (~5/7 of calendar)
    return Math.max(0, Math.round(calDays * 5 / 7));
  }
  function _clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  // EVA returns several percentage-style fields as decimals (e.g. 0.0273
  // for 2.73%). The dashboard's chips/formatters assume percent units, so
  // we normalize known decimal-encoded fields once at ingest. Heuristic:
  // if |v| <= 1, treat as decimal and scale to percent. Anything > 1 is
  // assumed already in percent units.
  var _PCT_DECIMAL_FIELDS = [
    'implied_move_pct',
    'realized_move_1q', 'realized_move_2q', 'realized_move_4q', 'realized_move_8q',
    'realized_move_avg_4q', 'realized_move_avg_8q', 'realized_move_stddev_8q',
  ];
  function _scaleIfDecimal(v) {
    if (v == null) return null;
    var n = Number(v);
    if (!isFinite(n)) return null;
    if (Math.abs(n) <= 1) return n * 100;
    return n;
  }
  function _normalizeSnapshot(snap) {
    if (!snap || typeof snap !== 'object') return snap;
    // Idempotent guard: once normalized, return as-is. The |n|<=1 heuristic
    // in _scaleIfDecimal is NOT self-idempotent (a 0.45% value scaled once
    // to 0.45 is still <=1 and would scale again to 45). Without this flag,
    // calling _normalizeSnapshot twice on the same object inflates percent
    // fields by another factor of 100 \u2014 the ×10000 display bug.
    if (snap.__pct_normalized) return snap;
    var out = {};
    Object.keys(snap).forEach(function (k) { out[k] = snap[k]; });
    _PCT_DECIMAL_FIELDS.forEach(function (k) {
      if (out[k] != null) out[k] = _scaleIfDecimal(out[k]);
    });
    out.__pct_normalized = true;
    return out;
  }

  /**
   * Setup Score (heuristic v1, 0-100). Tunable; document changes here.
   *   vol_edge (40)   — implied vs historical realized
   *   iv_rank_52w (20)
   *   term_structure_slope (15)  positive slope = event premium
   *   options_liquidity_score (15)
   *   realization_consistency (10) — tighter stddev = more reliable baseline
   * Final score is rebased to /100 by the populated weight subset, so
   * partial-data events still rank meaningfully.
   */
  function _computeSetupScore(snap) {
    if (!snap) return null;
    var score = 0, weight = 0;

    var vol_edge = _vol_edge(snap);
    if (vol_edge != null) {
      // +20% edge → ~30 of 40; -20% edge → ~10 of 40
      score += 25 + _clamp(vol_edge * 0.5, -15, 15);
      weight += 40;
    }
    if (snap.iv_rank_52w != null) {
      score += Number(snap.iv_rank_52w) * 0.2;
      weight += 20;
    }
    if (snap.term_structure_slope != null) {
      score += _clamp(Number(snap.term_structure_slope) * 75, 0, 15);
      weight += 15;
    }
    if (snap.options_liquidity_score != null) {
      score += Number(snap.options_liquidity_score) * 0.15;
      weight += 15;
    }
    if (snap.realized_move_stddev_8q != null && snap.realized_move_avg_8q) {
      var cv = Number(snap.realized_move_stddev_8q) / Number(snap.realized_move_avg_8q);
      if (isFinite(cv)) {
        score += _clamp((1 - cv) * 10, 0, 10);
        weight += 10;
      }
    }
    if (weight <= 0) return null;
    return _clamp((score / weight) * 100, 0, 100);
  }

  function _vol_edge(snap) {
    if (!snap) return null;
    var im = Number(snap.implied_move_pct);
    var rm = Number(snap.realized_move_avg_8q);
    if (!isFinite(im) || im === 0 || !isFinite(rm)) return null;
    return (1 - rm / im) * 100;
  }

  function _impliedMoveChip(v) {
    var n = Number(v);
    if (!isFinite(n)) return '<span class="ea-chip ea-chip-gray">—</span>';
    var cls = 'ea-chip-cyan';
    if (n < 3) cls = 'ea-chip-gray';
    else if (n > 10) cls = 'ea-chip-red';
    else if (n > 6) cls = 'ea-chip-yellow';
    return '<span class="ea-chip ' + cls + '">' + n.toFixed(2) + '%</span>';
  }
  function _volEdgeChip(v) {
    if (v == null) return '<span class="ea-chip ea-chip-gray">—</span>';
    var sign = v >= 0 ? '+' : '';
    var cls = v >= 0 ? 'ea-chip-green' : 'ea-chip-red';
    return '<span class="ea-chip ' + cls + '">' + sign + v.toFixed(1) + '%</span>';
  }
  function _ivVsHvChip(v) {
    if (v == null) return '<span class="ea-chip ea-chip-gray">—</span>';
    var n = Number(v);
    if (!isFinite(n)) return '<span class="ea-chip ea-chip-gray">—</span>';
    var cls = n > 1.2 ? 'ea-chip-green' : (n < 0.85 ? 'ea-chip-red' : 'ea-chip-cyan');
    return '<span class="ea-chip ' + cls + '">' + n.toFixed(2) + '</span>';
  }
  function _termSlopeChip(v) {
    if (v == null) return '<span class="ea-chip ea-chip-gray">—</span>';
    var n = Number(v);
    if (!isFinite(n)) return '<span class="ea-chip ea-chip-gray">—</span>';
    var cls = n > 0 ? 'ea-chip-green' : (n < -0.02 ? 'ea-chip-red' : 'ea-chip-cyan');
    return '<span class="ea-chip ' + cls + '">' + (n * 100).toFixed(2) + '%</span>';
  }
  function _setupScoreChip(v) {
    if (v == null) return '<span class="ea-chip ea-chip-gray">—</span>';
    var cls = 'ea-chip-gray';
    if (v >= 80) cls = 'ea-chip-bright-green';
    else if (v >= 60) cls = 'ea-chip-green';
    else if (v >= 40) cls = 'ea-chip-yellow';
    return '<span class="ea-chip ' + cls + '">' + Math.round(v) + '</span>';
  }
  function _tradeableIcon(v) {
    if (v === true) return '<span class="ea-tradeable-yes" title="Passes baseline filter">✓</span>';
    if (v === false) return '<span class="ea-tradeable-no" title="Fails baseline filter">✕</span>';
    return '<span class="ea-chip ea-chip-gray">—</span>';
  }

  EAShared.helpers = {
    esc: _esc, num: _num, pct: _pct, fmtDate: _fmtDate,
    businessDaysUntil: _businessDaysUntil,
    computeSetupScore: _computeSetupScore, volEdge: _vol_edge,
    impliedMoveChip: _impliedMoveChip, volEdgeChip: _volEdgeChip,
    ivVsHvChip: _ivVsHvChip, termSlopeChip: _termSlopeChip,
    setupScoreChip: _setupScoreChip, tradeableIcon: _tradeableIcon,
    normalizeSnapshot: _normalizeSnapshot,
  };

  /** Build side-panel HTML for a given snapshot + ticker profile + event. */
  EAShared.renderSidePanel = function (ctx) {
    // ctx: { event, ticker_profile, snapshot, snapshots_all, history }
    // Idempotent normalization \u2014 _normalizeSnapshot tags its output with
    // __pct_normalized so re-entry is a no-op. Safe whether the caller is
    // the main dashboard (already normalized via _flatten) or OnDemand
    // (passes raw snapshots).
    var snap = _normalizeSnapshot(ctx.snapshot || {});
    var ev = ctx.event || {};
    var prof = ctx.ticker_profile || {};
    var ve = _vol_edge(snap);
    var setup = _computeSetupScore(snap);

    var overview = '<div class="ea-stat-grid">' +
      _stat('Implied Move', _impliedMoveChip(snap.implied_move_pct), 'implied_move_pct') +
      _stat('Hist Avg (8Q)', _pct(snap.realized_move_avg_8q), 'realized_move_avg_8q') +
      _stat('Vol Edge', _volEdgeChip(ve), 'vol_edge') +
      _stat('Setup Score', _setupScoreChip(setup), 'setup_score') +
      _stat('IV Rank 52W', snap.iv_rank_52w != null ? Math.round(snap.iv_rank_52w) : '—', 'iv_rank_52w') +
      _stat('IV vs HV 30d', _ivVsHvChip(snap.iv_vs_hv_30d), 'iv_vs_hv_30d') +
      _stat('Term Slope', _termSlopeChip(snap.term_structure_slope), 'term_structure_slope') +
      _stat('Liquidity', snap.options_liquidity_score != null ? Math.round(snap.options_liquidity_score) : '—', 'options_liquidity_score') +
      _stat('Tradeable', _tradeableIcon(snap.passes_baseline_filter), 'passes_baseline_filter') +
      _stat('Days Out', _businessDaysUntil(ev.earnings_date) != null ? _businessDaysUntil(ev.earnings_date) : '—', 'days_out') +
      '</div>';

    // Each tab is wrapped so a throw in one renderer cannot blank the entire
    // side panel. If a tab fails, the others still render and the failed tab
    // shows an inline error \u2014 critical because Overview/History/Macro must
    // remain functional even if Timeline data is malformed.
    function _safeTab(name, fn) {
      try { return fn(); }
      catch (err) {
        console.error('[EarningsAnalysis] tab \"' + name + '\" render failed', err);
        return '<div class=\"ea-state ea-state-error\">' + _esc(name) +
          ' tab failed to render: ' + _esc(err && err.message || String(err)) + '</div>';
      }
    }

    var hist     = _safeTab('history',  function () { return _renderHistoryFromSnapshot(snap, ctx.history); });
    var timeline = _safeTab('timeline', function () { return _renderTimelineFromSnapshots(ctx.snapshots_all, ctx.event); });
    var macro    = _safeTab('macro',    function () { return _renderMacroFromSnapshot(snap); });

    var raw = '<pre class=\"ea-raw-json\">' + _esc(JSON.stringify(snap, null, 2)) + '</pre>';

    return {
      title: (ev.ticker || prof.ticker || '?') + ' — ' + (prof.company_name || '') +
             ' · ' + _fmtDate(ev.earnings_date) + (ev.timing ? ' (' + _esc(ev.timing) + ')' : ''),
      tabs: { overview: overview, history: hist, timeline: timeline, macro: macro, raw: raw }
    };
  };

  function _stat(label, valHtml, metricKey) {
    var attr = metricKey ? ' data-metric="' + _esc(metricKey) + '"' : '';
    return '<div class="ea-stat"><div class="ea-stat-label"' + attr + '>' + _esc(label) +
      '</div><div class="ea-stat-value">' + valHtml + '</div></div>';
  }

  // History tab — derived from per-quarter realized_move_Nq fields in the
  // latest snapshot (no separate API call). Falls back to ctx.history
  // (legacy shape from /tickers/{t}.earnings_history) if present.
  function _renderHistoryFromSnapshot(snap, legacyHistory) {
    snap = snap || {};
    var quarters = [
      { label: '1Q', val: snap.realized_move_1q },
      { label: '2Q', val: snap.realized_move_2q },
      { label: '4Q', val: snap.realized_move_4q },
      { label: '8Q', val: snap.realized_move_8q },
    ].filter(function (q) { return q.val != null; });

    if (!quarters.length && legacyHistory && legacyHistory.length) {
      var html = '<table class="ea-mini-table"><thead><tr>' +
        '<th>Date</th><th>EPS Surp%</th><th>Realized %</th><th>Implied %</th><th>R/I</th>' +
        '</tr></thead><tbody>';
      legacyHistory.forEach(function (r) {
        var ratio = (r.realized_move_pct != null && r.implied_move_pct) ?
          (Number(r.realized_move_pct) / Number(r.implied_move_pct)).toFixed(2) : '—';
        html += '<tr>' +
          '<td>' + _esc(r.earnings_date || '') + '</td>' +
          '<td>' + (r.eps_surprise_pct != null ? _pct(r.eps_surprise_pct) : '—') + '</td>' +
          '<td>' + (r.realized_move_pct != null ? _pct(r.realized_move_pct) : '—') + '</td>' +
          '<td>' + (r.implied_move_pct != null ? _pct(r.implied_move_pct) : '—') + '</td>' +
          '<td>' + ratio + '</td>' +
          '</tr>';
      });
      return html + '</tbody></table>';
    }

    if (!quarters.length) {
      return '<div class="ea-state">No earnings history available for this ticker.</div>';
    }

    var rowsHtml = quarters.map(function (q) {
      return '<tr><td>' + _esc(q.label) + '</td><td class="ea-num">' + _pct(q.val) + '</td></tr>';
    }).join('');
    var table = '<table class="ea-mini-table"><thead><tr>' +
      '<th>Quarters Ago</th><th class="ea-num">Realized Move</th>' +
      '</tr></thead><tbody>' + rowsHtml + '</tbody></table>';

    var summary = '<div class="ea-stat-grid" style="margin-top:12px;">' +
      _stat('Avg 4Q', _pct(snap.realized_move_avg_4q), 'realized_move_avg_4q') +
      _stat('Avg 8Q', _pct(snap.realized_move_avg_8q), 'realized_move_avg_8q') +
      _stat('Stddev 8Q', _pct(snap.realized_move_stddev_8q), 'realized_move_stddev_8q') +
      _stat('Clean Prints', snap.clean_prints_available != null ? Math.round(snap.clean_prints_available) : '—', 'clean_prints_available') +
      _stat('R/I Ratio 8Q', snap.realized_implied_ratio_8q != null ? Number(snap.realized_implied_ratio_8q).toFixed(2) : '—', 'realized_implied_ratio_8q') +
      '</div>';

    var warning = '';
    if (snap.clean_prints_available != null && Number(snap.clean_prints_available) < 4) {
      warning = '<div class="ea-banner ea-banner-warn" style="margin-top:12px;">Limited history — only ' +
        Math.round(Number(snap.clean_prints_available)) + ' quarters of clean data.</div>';
    }
    return table + summary + warning;
  }

  // Timeline tab — list of snapshot rows from /events/{id}/features.
  // Sorts closest-to-earnings first.
  function _renderTimelineFromSnapshots(snaps, ev) {
    // Diagnostic — keep in code; helps debug future Timeline regressions.
    try {
      console.log('[EA Timeline]', {
        snapshotsLength: (snaps && snaps.length) || 0,
        eventId: ev && (ev.event_id || ev.id),
        ticker: ev && ev.ticker,
        earningsDate: ev && ev.earnings_date,
        sampleKeys: snaps && snaps[0] ? Object.keys(snaps[0]).slice(0, 12) : null,
      });
    } catch (_) {}

    if (!snaps || !snaps.length) {
      // Improved fallback: distinguish "too early" from "still processing".
      var days = ev && ev.earnings_date ? _businessDaysUntil(ev.earnings_date) : null;
      var ctxLine = (days != null && days > 7)
        ? 'T-7 chain capture begins ~7 trading days before earnings (currently T-' + days + ').'
        : 'Snapshots may still be processing. Check again shortly.';
      return '<div class="ea-state">No snapshots available yet for this event.<br>' +
        '<span style="opacity:0.75;font-size:0.9em;">' + _esc(ctxLine) + '</span></div>';
    }
    snaps = snaps.slice().sort(function (a, b) {
      return String(b.snapshot_date || '').localeCompare(String(a.snapshot_date || ''));
    });
    var evDate = ev && ev.earnings_date ? new Date(ev.earnings_date) : null;
    function _daysOut(s) {
      // /events/{id}/features uses days_to_earnings; some payloads use days_to_event.
      var d2e = (s.days_to_earnings != null) ? s.days_to_earnings
              : (s.days_to_event != null)    ? s.days_to_event
              : null;
      if (d2e != null) return 'T-' + d2e;
      if (!evDate || !s.snapshot_date) return '—';
      var d = new Date(s.snapshot_date);
      if (isNaN(d.getTime())) return '—';
      var n = Math.round((evDate - d) / (24 * 3600 * 1000));
      return n >= 0 ? 'T-' + n : 'T+' + Math.abs(n);
    }
    var html = '<table class="ea-mini-table"><thead><tr>' +
      '<th>Snapshot Date</th><th>Days Out</th><th class="ea-num">Implied %</th>' +
      '<th class="ea-num">ATM IV</th><th class="ea-num">Underlying</th><th class="ea-num">Term Slope</th>' +
      '</tr></thead><tbody>';
    snaps.forEach(function (s) {
      // /events/{id}/features returns ATM IV as `atm_iv_ours`; the inline
      // latest_snapshot projection from /events/upcoming renames it to `atm_iv`.
      // Accept either so the Timeline works regardless of source.
      var atmRaw = (s.atm_iv_ours != null) ? s.atm_iv_ours : s.atm_iv;
      var atm = (atmRaw != null && isFinite(Number(atmRaw))) ? (Number(atmRaw) * 100).toFixed(1) + '%' : '—';
      var term = (s.term_structure_slope != null && isFinite(Number(s.term_structure_slope))) ?
        ((Number(s.term_structure_slope) * 100).toFixed(1) + '%') : '—';
      html += '<tr>' +
        '<td>' + _esc(s.snapshot_date || '') + '</td>' +
        '<td>' + _daysOut(s) + '</td>' +
        '<td class="ea-num">' + (s.implied_move_pct != null ? _pct(s.implied_move_pct) : '—') + '</td>' +
        '<td class="ea-num">' + atm + '</td>' +
        '<td class="ea-num">' + (s.underlying_price != null ? '$' + _num(s.underlying_price) : '—') + '</td>' +
        '<td class="ea-num">' + term + '</td>' +
        '</tr>';
    });
    return html + '</tbody></table>';
  }

  // Macro tab — macro context fields from the latest snapshot.
  function _renderMacroFromSnapshot(snap) {
    snap = snap || {};
    function _fmt(v, dp) {
      if (v == null) return '<span title="Not yet populated — requires 30+ trading days of forward history.">—</span>';
      var n = Number(v);
      if (!isFinite(n)) return '—';
      return n.toFixed(dp == null ? 2 : dp);
    }
    var grid = '<div class="ea-stat-grid">' +
      _stat('VIX Level', _fmt(snap.vix_level), 'vix_level') +
      _stat('VIX Term Slope', _fmt(snap.vix_term_slope), 'vix_term_slope') +
      _stat('SPY IV Rank', _fmt(snap.spy_iv_rank, 0), 'spy_iv_rank') +
      _stat('Sector ETF IV Rank', _fmt(snap.sector_etf_iv_rank, 0), 'sector_etf_iv_rank') +
      '</div>';
    var vix = Number(snap.vix_level);
    var note = '';
    if (isFinite(vix)) {
      var msg;
      if (vix < 15) msg = 'Low-volatility regime. Implied vols are compressed across the market.';
      else if (vix < 20) msg = 'Normal volatility regime.';
      else if (vix < 30) msg = 'Elevated volatility. Event premium may be amplified.';
      else msg = 'High volatility regime. Use caution interpreting individual-name implied moves.';
      note = '<div class="ea-banner ea-banner-info" style="margin-top:12px;">' + _esc(msg) + '</div>';
    }
    return grid + note;
  }

  // ── Earnings Analysis page controller ────────────────────────────
  window.BenTradePages.initEarningsAnalysis = function initEarningsAnalysis(rootEl) {
    var scope = rootEl || document;
    var COLUMNS = [
      { key: 'ticker',                  label: 'Ticker',     numeric: false },
      { key: 'company_name',            label: 'Company',    numeric: false },
      { key: 'sector',                  label: 'Sector',     numeric: false },
      { key: 'earnings_date',           label: 'Date',       numeric: false },
      { key: 'timing',                  label: 'Timing',     numeric: false },
      { key: 'days_out',                label: 'Days Out',   numeric: true },
      { key: 'implied_move_pct',        label: 'Implied %',  numeric: true, metric: 'implied_move_pct' },
      { key: 'realized_move_avg_8q',    label: 'Hist Avg',   numeric: true, metric: 'realized_move_avg_8q' },
      { key: 'vol_edge',                label: 'Vol Edge',   numeric: true, metric: 'vol_edge' },
      { key: 'iv_rank_52w',             label: 'IV Rank',    numeric: true, metric: 'iv_rank_52w' },
      { key: 'iv_vs_hv_30d',            label: 'IV/HV',      numeric: true, metric: 'iv_vs_hv_30d' },
      { key: 'term_structure_slope',    label: 'Term',       numeric: true, metric: 'term_structure_slope' },
      { key: 'passes_baseline_filter',  label: 'Tradeable',  numeric: false, metric: 'passes_baseline_filter' },
      { key: 'setup_score',             label: 'Setup',      numeric: true, metric: 'setup_score' },
    ];

    var state = {
      rows: [],          // flattened combined rows
      profileByTicker: {},
      sortKey: 'earnings_date',
      sortAsc: true,
      selectedKey: null,
      // null = collapsed (no tab open). On first row select we auto-open Overview.
      selectedTab: null,
      selectedCtx: null,
      pollTimer: null,
    };

    var elTable    = scope.querySelector('#ea-table');
    var elThead    = scope.querySelector('#ea-thead-row');
    var elTbody    = scope.querySelector('#ea-tbody');
    var elSector   = scope.querySelector('#ea-sector');
    var elWindow   = scope.querySelector('#ea-window');
    var elTradeable= scope.querySelector('#ea-tradeable-only');
    var elMinScore = scope.querySelector('#ea-min-score');
    var elMinScoreVal = scope.querySelector('#ea-min-score-val');
    var elSearch   = scope.querySelector('#ea-search');
    var elRefresh  = scope.querySelector('#ea-refresh');
    var elCount    = scope.querySelector('#ea-result-count');
    var elUpdated  = scope.querySelector('#ea-last-updated');
    var elTabs     = scope.querySelector('#ea-tabs');
    var elSideTitle= scope.querySelector('#ea-side-title');
    var elSideBody = scope.querySelector('#ea-side-body');
    var elContentPanel = scope.querySelector('#ea-content-panel');

    function _setActionButtonsEnabled(enabled) {
      Array.prototype.forEach.call(elTabs.querySelectorAll('.ea-action-btn'), function (b) {
        b.disabled = !enabled;
      });
    }
    function _setActiveActionButton(tab) {
      Array.prototype.forEach.call(elTabs.querySelectorAll('.ea-action-btn'), function (b) {
        b.classList.toggle('active', b.getAttribute('data-tab') === tab);
      });
    }
    function _showContentPanel(show) {
      if (elContentPanel) elContentPanel.style.display = show ? '' : 'none';
    }

    _renderHeader();

    function _renderHeader() {
      elThead.innerHTML = COLUMNS.map(function (c) {
        var sorted = state.sortKey === c.key ? ' sorted' : '';
        var arrow = state.sortKey === c.key ? (state.sortAsc ? '▲' : '▼') : '↕';
        var metricAttr = c.metric ? ' data-metric="' + c.metric + '"' : '';
        return '<th class="' + (c.numeric ? 'ea-num' : '') + sorted + '" data-col="' + c.key + '"' + metricAttr + '>' +
          _esc(c.label) + ' <span class="ea-sort-arrow">' + arrow + '</span></th>';
      }).join('');
      Array.prototype.forEach.call(elThead.querySelectorAll('th'), function (th) {
        th.addEventListener('click', function () {
          var key = th.getAttribute('data-col');
          if (state.sortKey === key) state.sortAsc = !state.sortAsc;
          else { state.sortKey = key; state.sortAsc = true; }
          _renderHeader();
          _renderRows();
        });
      });
      _bindTooltips(elThead);
    }

    function _renderSkeleton() {
      var html = '';
      for (var i = 0; i < 6; i++) {
        html += '<tr class="ea-skeleton-row">';
        for (var j = 0; j < COLUMNS.length; j++) html += '<td></td>';
        html += '</tr>';
      }
      elTbody.innerHTML = html;
    }

    function _filtered() {
      var sec = elSector.value || '';
      var tradeOnly = !!elTradeable.checked;
      var minScore = Number(elMinScore.value) || 0;
      var q = (elSearch.value || '').trim().toLowerCase();
      return state.rows.filter(function (r) {
        if (sec && r.sector !== sec) return false;
        if (tradeOnly && r.passes_baseline_filter !== true) return false;
        if (minScore > 0 && (r.setup_score == null || r.setup_score < minScore)) return false;
        if (q) {
          var hay = ((r.ticker || '') + ' ' + (r.company_name || '')).toLowerCase();
          if (hay.indexOf(q) === -1) return false;
        }
        return true;
      });
    }

    function _sorted(list) {
      var k = state.sortKey, asc = state.sortAsc ? 1 : -1;
      return list.slice().sort(function (a, b) {
        var av = a[k], bv = b[k];
        if (av == null && bv == null) return 0;
        if (av == null) return 1;   // nulls last
        if (bv == null) return -1;
        if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * asc;
        return String(av).localeCompare(String(bv)) * asc;
      });
    }

    function _renderRows() {
      var rows = _sorted(_filtered());
      elCount.textContent = rows.length + ' of ' + state.rows.length;
      if (!rows.length) {
        elTbody.innerHTML = '<tr><td colspan="' + COLUMNS.length + '" class="ea-state">' +
          (state.rows.length ? 'No events match the current filters.' :
           'No upcoming earnings in the selected window. Try extending to 30 days.') + '</td></tr>';
        return;
      }
      elTbody.innerHTML = rows.map(function (r) {
        var sel = state.selectedKey === r.__key ? ' ea-row-selected' : '';
        return '<tr class="ea-row' + sel + '" data-key="' + _esc(r.__key) + '">' +
          '<td><span class="ea-ticker">' + _esc(r.ticker) + '</span></td>' +
          '<td class="ea-company">' + _esc((r.company_name || '').slice(0, 30)) + '</td>' +
          '<td>' + _esc(r.sector || '—') + '</td>' +
          '<td>' + _fmtDate(r.earnings_date) + '</td>' +
          '<td>' + _esc(r.timing || '—') + '</td>' +
          '<td class="ea-num">' + (r.days_out != null ? r.days_out : '—') + '</td>' +
          '<td class="ea-num">' + _impliedMoveChip(r.implied_move_pct) + '</td>' +
          '<td class="ea-num">' + _pct(r.realized_move_avg_8q) + '</td>' +
          '<td class="ea-num">' + _volEdgeChip(r.vol_edge) + '</td>' +
          '<td class="ea-num">' + (r.iv_rank_52w != null ? Math.round(r.iv_rank_52w) : '—') + '</td>' +
          '<td class="ea-num">' + _ivVsHvChip(r.iv_vs_hv_30d) + '</td>' +
          '<td class="ea-num">' + _termSlopeChip(r.term_structure_slope) + '</td>' +
          '<td>' + _tradeableIcon(r.passes_baseline_filter) + '</td>' +
          '<td class="ea-num">' + _setupScoreChip(r.setup_score) + '</td>' +
        '</tr>';
      }).join('');
      // Per-row click binding handled once via delegation below (see _bindRowClickDelegation).
      _bindTooltips(elTbody);
    }

    // Bind a single delegated click listener. We bind on document in the
    // capture phase so it cannot be intercepted by any parent stopPropagation
    // and is unaffected by tbody being re-rendered. Scoped by closest match
    // inside `scope` so it only handles this dashboard's rows.
    var _rowClickBound = false;
    function _bindRowClickDelegation() {
      if (_rowClickBound) return;
      _rowClickBound = true;
      var handler = function (e) {
        var tr = e.target && e.target.closest ? e.target.closest('tr.ea-row') : null;
        if (!tr) return;
        // Only handle clicks inside our scope (so this doesn't fire for other dashboards).
        if (scope.contains && !scope.contains(tr)) return;
        var key = tr.getAttribute('data-key');
        console.log('[EarningsAnalysis] row click', { key: key, rowsLen: state.rows.length });
        var row = state.rows.find(function (x) { return String(x.__key) === String(key); });
        if (row) _selectRow(row);
        else console.warn('[EarningsAnalysis] row click: no row found for key', key);
      };
      document.addEventListener('click', handler, true); // capture phase
      // Also attach on tbody as a fallback in case document-level binding is blocked.
      if (elTbody) elTbody.addEventListener('click', handler);
    }

    function _populateSectors() {
      var seen = {};
      state.rows.forEach(function (r) { if (r.sector) seen[r.sector] = true; });
      var keys = Object.keys(seen).sort();
      elSector.innerHTML = '<option value="">All sectors</option>' +
        keys.map(function (s) { return '<option value="' + _esc(s) + '">' + _esc(s) + '</option>'; }).join('');
    }

    function _selectRow(row) {
      var isNewRow = state.selectedKey !== row.__key;
      state.selectedKey = row.__key;
      _renderRows();
      // Auto-open Overview on first row click; preserve the active tab when
      // the user clicks a different row while a tab is already open.
      if (isNewRow && !state.selectedTab) state.selectedTab = 'overview';
      _setActionButtonsEnabled(true);
      _setActiveActionButton(state.selectedTab);
      elSideTitle.textContent = (row.ticker || '?') + ' — loading…';
      if (state.selectedTab) {
        _showContentPanel(true);
        elSideBody.innerHTML = '<div class="ea-state">Loading event details…</div>';
      }

      var eid = row.__event_id;
      var ticker = row.ticker;
      var pTicker = window.BenTradeApi.getEvaTicker(ticker).catch(function (err) {
        console.error('[EarningsAnalysis] getEvaTicker failed', ticker, err);
        return null;
      });
      if (!eid) {
        console.warn('[EarningsAnalysis] no event_id resolved for row — Timeline fetch skipped',
          { ticker: ticker, rowKeys: Object.keys(row || {}), event: row.__event });
      }
      var pFeatures = eid ?
        window.BenTradeApi.getEvaEventFeatures(eid).catch(function (err) {
          console.error('[EarningsAnalysis] getEvaEventFeatures failed', eid, err);
          return null;
        }) :
        Promise.resolve(null);

      Promise.all([pTicker, pFeatures]).then(function (parts) {
        var profile = (parts[0] && (parts[0].ticker || parts[0])) || {};
        var featuresPayload = parts[1] || {};
        var snaps = featuresPayload.features || featuresPayload.snapshots || (Array.isArray(featuresPayload) ? featuresPayload : []);
        // Normalize each timeline snapshot the same way we normalize the
        // inline latest_snapshot from /events/upcoming.
        snaps = (snaps || []).map(_normalizeSnapshot);
        var latest = _normalizeSnapshot(_pickLatestSnapshot(snaps)) || row.__snapshot;
        var history = (parts[0] && (parts[0].earnings_history || parts[0].history)) || [];
        var ctx = {
          event: row.__event,
          ticker_profile: profile.profile || profile,
          snapshot: latest,
          snapshots_all: snaps,
          history: history,
        };
        state.selectedCtx = ctx;
        // Wrap render+display so any throw inside renderSidePanel or
        // _renderSideTab still leaves a usable error message in the panel
        // rather than silently failing after the loading state.
        try {
          var rendered = EAShared.renderSidePanel(ctx);
          elSideTitle.textContent = rendered.title;
          if (state.selectedTab) _renderSideTab(rendered, state.selectedTab);
        } catch (err) {
          console.error('[EarningsAnalysis] renderSidePanel threw', err, { ctx: ctx });
          elSideTitle.textContent = (row.ticker || '?') + ' — render error';
          elSideBody.innerHTML = '<div class="ea-state ea-state-error">Side panel render failed: ' +
            _esc(err && err.message || String(err)) + '</div>';
        }
      }).catch(function (err) {
        console.error('[EarningsAnalysis] side panel data fetch failed', err);
        elSideTitle.textContent = (row.ticker || '?') + ' — load error';
        elSideBody.innerHTML = '<div class="ea-state ea-state-error">Failed to load: ' + _esc(err && err.message || String(err)) + '</div>';
      });
    }

    function _renderSideTab(rendered, tab) {
      _setActiveActionButton(tab);
      _showContentPanel(true);
      elSideBody.innerHTML = rendered.tabs[tab] || '';
      _bindTooltips(elSideBody);
    }

    // ── Model Analysis tab ───────────────────────────────────────
    function _renderModelAnalysisLoading() {
      elSideBody.innerHTML =
        '<div class="ea-analysis-loading">' +
          '<div class="ea-analysis-spinner"></div>' +
          '<div>Analyzing\u2026 (typically 10\u201330 seconds)</div>' +
          '<div class="ea-analysis-loading-detail">Routing through model_router</div>' +
        '</div>';
    }
    function _formatAnalysisTime(iso) {
      if (!iso) return '';
      try { return new Date(iso).toLocaleString(); } catch (_) { return iso; }
    }
    function _buildAnalysisHtml(result, eventId) {
      var sections = result.structured_sections || null;
      // v2.0: Premium prompt is independent of local analysis — always show.
      var premiumBtn = '<button class="ea-action-btn" data-ea-premium-prompt="' + _esc(eventId) + '" title="Generate a copyable prompt for Claude Pro / ChatGPT Plus">Premium Model Prompt</button>';
      var cachedBanner = result.cached
        ? '<div class="ea-analysis-cached">' +
            '<span>Cached \u00b7 ' + _esc(_formatAnalysisTime(result.created_at)) + '</span>' +
            '<span class="ea-analysis-actions">' +
              '<button class="ea-action-btn" data-ea-reanalyze="' + _esc(eventId) + '">Re-analyze</button>' +
              premiumBtn +
            '</span>' +
          '</div>'
        : '<div class="ea-analysis-cached">' +
            '<span></span>' +
            '<span class="ea-analysis-actions">' + premiumBtn + '</span>' +
          '</div>';
      var sectionsHtml;
      if (sections && sections.setup_quality && sections.directional_thesis) {
        sectionsHtml =
          '<h3>Setup Quality</h3><p>' + _esc(sections.setup_quality) + '</p>' +
          '<h3>Directional Thesis</h3>' +
          '<p class="ea-thesis">' + _esc(sections.directional_thesis) + '</p>' +
          '<div class="ea-trade-tiers">' +
            '<div class="ea-trade-tier ea-tier-conservative">' +
              '<h4>Conservative</h4>' +
              '<pre>' + _esc(sections.conservative_trade || '(missing)') + '</pre>' +
            '</div>' +
            '<div class="ea-trade-tier ea-tier-medium">' +
              '<h4>Medium</h4>' +
              '<pre>' + _esc(sections.medium_trade || '(missing)') + '</pre>' +
            '</div>' +
            '<div class="ea-trade-tier ea-tier-aggressive">' +
              '<h4>Aggressive</h4>' +
              '<pre>' + _esc(sections.aggressive_trade || '(missing)') + '</pre>' +
            '</div>' +
          '</div>' +
          '<h3>Risks</h3><p>' + _esc(sections.risks || '').replace(/\n/g, '<br>') + '</p>' +
          '<h3>Confidence</h3><p>' + _esc(sections.confidence || '') + '</p>';
      } else {
        sectionsHtml = '<pre class="ea-analysis-raw">' + _esc(result.response_text || '(no response)') + '</pre>';
      }
      var provider = result.model_provider || 'unknown';
      var mode = result.execution_mode || 'unknown';
      var model = result.model_used || 'unknown';
      var promptV = result.prompt_version || '?';
      var tokens = (result.tokens_input || result.tokens_output)
        ? ' \u00b7 Tokens: ' + (result.tokens_input || '?') + ' in / ' + (result.tokens_output || '?') + ' out'
        : '';
      return '<div class="ea-analysis-container">' +
        cachedBanner +
        '<div class="ea-analysis-content">' + sectionsHtml + '</div>' +
        '<div class="ea-analysis-meta">' +
          _esc(provider) + ' \u00b7 ' + _esc(mode) + ' \u00b7 ' + _esc(model) +
          ' \u00b7 prompt v' + _esc(promptV) + tokens +
        '</div>' +
        '</div>';
    }
    function _buildAnalysisErrorHtml(message, eventId) {
      return '<div class="ea-analysis-error">' +
        '<h3>Analysis Failed</h3>' +
        '<p>' + _esc(message) + '</p>' +
        '<button class="ea-action-btn" data-ea-reanalyze="' + _esc(eventId) + '">Retry</button>' +
        '</div>';
    }
    function _bindReanalyzeButtons() {
      Array.prototype.forEach.call(elSideBody.querySelectorAll('[data-ea-reanalyze]'), function (b) {
        b.addEventListener('click', function () {
          var eid = b.getAttribute('data-ea-reanalyze');
          _renderModelAnalysis(eid, true);
        });
      });
      Array.prototype.forEach.call(elSideBody.querySelectorAll('[data-ea-premium-prompt]'), function (b) {
        b.addEventListener('click', function () {
          var eid = b.getAttribute('data-ea-premium-prompt');
          _showPremiumPromptModal(eid, b);
        });
      });
    }

    // ── Premium model prompt modal ───────────────────────────────
    // Mirrors Company Evaluator's research-prompt modal pattern: opens an
    // overlay, fetches the prompt, supports copy-to-clipboard, and lets the
    // user paste the premium model's response back to be persisted.
    function _showPremiumPromptModal(eventId, sourceBtn) {
      // Disable trigger to avoid double-clicks while the request is in-flight.
      if (sourceBtn) {
        sourceBtn.disabled = true;
        sourceBtn.dataset._origText = sourceBtn.textContent;
        sourceBtn.textContent = 'Generating\u2026';
      }
      window.BenTradeApi.getEvaPremiumPrompt(eventId).then(function (resp) {
        _renderPremiumPromptModal(resp || {}, eventId);
      }).catch(function (err) {
        var msg = (err && err.message) || String(err);
        alert('Failed to generate premium prompt: ' + msg);
      }).finally(function () {
        if (sourceBtn) {
          sourceBtn.disabled = false;
          if (sourceBtn.dataset._origText) {
            sourceBtn.textContent = sourceBtn.dataset._origText;
            delete sourceBtn.dataset._origText;
          }
        }
      });
    }

    function _closePremiumPromptModal() {
      var existing = document.querySelector('.ea-premium-modal-overlay');
      if (existing) existing.remove();
      document.removeEventListener('keydown', _onPremiumModalEsc);
    }

    function _onPremiumModalEsc(e) {
      if (e.key === 'Escape') _closePremiumPromptModal();
    }

    function _renderPremiumPromptModal(resp, eventId) {
      _closePremiumPromptModal();
      var ticker = resp.ticker || 'event ' + eventId;
      var promptText = resp.prompt_text || '';
      var charCount = resp.char_count || promptText.length;
      var meta = charCount.toLocaleString() + ' characters';
      if (resp.local_analysis_age_seconds != null) {
        meta += ' \u00b7 local analysis ' + _formatPromptAge(resp.local_analysis_age_seconds);
      }

      var overlay = document.createElement('div');
      overlay.className = 'ea-premium-modal-overlay';
      overlay.innerHTML =
        '<div class="ea-premium-modal" role="dialog" aria-modal="true" aria-label="Premium Model Prompt">' +
          '<div class="ea-premium-modal-header">' +
            '<h3>Premium Model Prompt \u2014 ' + _esc(ticker) + '</h3>' +
            '<button type="button" class="ea-modal-close" aria-label="Close">\u00d7</button>' +
          '</div>' +
          '<div class="ea-premium-modal-body">' +
            '<p class="ea-premium-instructions">' +
              'Copy the prompt below and paste it into Claude Pro, ChatGPT Plus, or another premium model. ' +
              'This prompt requests a full senior-trader analysis with view decomposition, up to 8 trade ' +
              "recommendations, and educational framing \u2014 independent of the local model's analysis. " +
              'Paste the response back below to save it for reference.' +
            '</p>' +
            '<div class="ea-premium-prompt-actions">' +
              '<button type="button" class="ea-action-btn" data-ea-copy-prompt>Copy Prompt to Clipboard</button>' +
              '<span class="ea-premium-meta">' + _esc(meta) + '</span>' +
              '<span class="ea-copy-status" data-ea-copy-status></span>' +
            '</div>' +
            '<textarea class="ea-premium-prompt-text" data-ea-prompt-text readonly></textarea>' +
            '<hr class="ea-premium-divider">' +
            '<h4>Save Premium Model Response (optional)</h4>' +
            '<p class="ea-premium-instructions">' +
              "Paste the premium model's response here to save it alongside the local analysis for future reference." +
            '</p>' +
            '<textarea class="ea-premium-response-text" data-ea-response-text ' +
              'placeholder="Paste premium model\u2019s response here\u2026"></textarea>' +
            '<div class="ea-premium-prompt-actions">' +
              '<button type="button" class="ea-action-btn" data-ea-save-response>Save Response</button>' +
              '<span class="ea-save-status" data-ea-save-status></span>' +
            '</div>' +
          '</div>' +
        '</div>';
      document.body.appendChild(overlay);

      // Set textarea value via property (not innerHTML attribute) so any
      // characters \u2014 including angle brackets / quotes \u2014 round-trip
      // unchanged when the user copies it back out.
      var promptArea = overlay.querySelector('[data-ea-prompt-text]');
      promptArea.value = promptText;

      // Attempt to prefill the response area if a previous one was saved.
      window.BenTradeApi.getEvaPremiumResponse(eventId).then(function (saved) {
        if (saved && saved.response_text) {
          var ta = overlay.querySelector('[data-ea-response-text]');
          if (ta) ta.value = saved.response_text;
        }
      }).catch(function () { /* 404 is the normal first-run case */ });

      overlay.querySelector('.ea-modal-close').addEventListener('click', _closePremiumPromptModal);
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) _closePremiumPromptModal();
      });
      document.addEventListener('keydown', _onPremiumModalEsc);

      var copyBtn = overlay.querySelector('[data-ea-copy-prompt]');
      var copyStatus = overlay.querySelector('[data-ea-copy-status]');
      copyBtn.addEventListener('click', function () {
        _copyTextToClipboard(promptText).then(function (ok) {
          if (ok) {
            copyStatus.textContent = '\u2713 Copied';
            copyStatus.classList.add('ea-status-ok');
            copyStatus.classList.remove('ea-status-err');
            setTimeout(function () {
              copyStatus.textContent = '';
              copyStatus.classList.remove('ea-status-ok');
            }, 2000);
          } else {
            copyStatus.textContent = 'Copy failed \u2014 select and copy manually';
            copyStatus.classList.add('ea-status-err');
            copyStatus.classList.remove('ea-status-ok');
            promptArea.focus();
            promptArea.select();
          }
        });
      });

      var saveBtn = overlay.querySelector('[data-ea-save-response]');
      var saveStatus = overlay.querySelector('[data-ea-save-status]');
      saveBtn.addEventListener('click', function () {
        var ta = overlay.querySelector('[data-ea-response-text]');
        var text = (ta.value || '').trim();
        if (!text) {
          saveStatus.textContent = 'Nothing to save';
          saveStatus.classList.add('ea-status-err');
          saveStatus.classList.remove('ea-status-ok');
          return;
        }
        saveBtn.disabled = true;
        saveStatus.textContent = 'Saving\u2026';
        saveStatus.classList.remove('ea-status-ok', 'ea-status-err');
        window.BenTradeApi.saveEvaPremiumResponse(eventId, text).then(function (r) {
          saveStatus.textContent = '\u2713 Saved \u00b7 ' + ((r && r.char_count) || text.length).toLocaleString() + ' chars';
          saveStatus.classList.add('ea-status-ok');
        }).catch(function (err) {
          saveStatus.textContent = 'Save failed: ' + ((err && err.message) || String(err));
          saveStatus.classList.add('ea-status-err');
        }).finally(function () {
          saveBtn.disabled = false;
        });
      });
    }

    function _formatPromptAge(seconds) {
      if (seconds == null) return '';
      if (seconds < 60) return 'generated ' + seconds + 's ago';
      if (seconds < 3600) return 'generated ' + Math.round(seconds / 60) + 'm ago';
      if (seconds < 86400) return 'generated ' + Math.round(seconds / 3600) + 'h ago';
      return 'generated ' + Math.round(seconds / 86400) + 'd ago';
    }

    function _copyTextToClipboard(text) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text).then(function () { return true; }).catch(function () {
          return _fallbackCopy(text);
        });
      }
      return Promise.resolve(_fallbackCopy(text));
    }

    function _fallbackCopy(text) {
      try {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        var ok = document.execCommand('copy');
        document.body.removeChild(ta);
        return ok;
      } catch (_) {
        return false;
      }
    }
    function _renderModelAnalysis(eventId, forceRefresh) {
      _setActiveActionButton('model_analysis');
      _showContentPanel(true);
      _renderModelAnalysisLoading();
      window.BenTradeApi.analyzeEvaEvent(eventId, !!forceRefresh).then(function (result) {
        // Guard against stale responses if the user switched rows mid-flight.
        if (state.selectedTab !== 'model_analysis') return;
        if (result && result.error_message) {
          elSideBody.innerHTML = _buildAnalysisErrorHtml(result.error_message, eventId);
        } else {
          elSideBody.innerHTML = _buildAnalysisHtml(result || {}, eventId);
        }
        _bindReanalyzeButtons();
      }).catch(function (err) {
        if (state.selectedTab !== 'model_analysis') return;
        var msg = (err && err.message) || String(err);
        elSideBody.innerHTML = _buildAnalysisErrorHtml(msg, eventId);
        _bindReanalyzeButtons();
      });
    }

    Array.prototype.forEach.call(scope.querySelectorAll('#ea-tabs .ea-action-btn'), function (b) {
      b.addEventListener('click', function () {
        if (b.disabled) return;
        var tab = b.getAttribute('data-tab');
        // Toggle: clicking the active tab again collapses the content panel.
        if (state.selectedTab === tab) {
          state.selectedTab = null;
          _setActiveActionButton(null);
          _showContentPanel(false);
          return;
        }
        state.selectedTab = tab;
        if (tab === 'model_analysis') {
          // Model Analysis is sourced separately from the side-panel tabs \u2014
          // it calls /api/eva/events/{id}/analyze through the model router.
          var row = state.rows.find(function (x) { return String(x.__key) === String(state.selectedKey); });
          var eid = row && row.__event_id;
          if (!eid) {
            _setActiveActionButton('model_analysis');
            _showContentPanel(true);
            elSideBody.innerHTML = '<div class="ea-state ea-state-error">Cannot analyze: no event_id resolved for this row.</div>';
            return;
          }
          _renderModelAnalysis(eid, false);
          return;
        }
        if (state.selectedCtx) {
          _renderSideTab(EAShared.renderSidePanel(state.selectedCtx), tab);
        } else {
          _setActiveActionButton(tab);
          _showContentPanel(true);
          elSideBody.innerHTML = '<div class="ea-state">Select a row above to view event details.</div>';
        }
      });
    });

    function _pickLatestSnapshot(snaps) {
      if (!Array.isArray(snaps) || !snaps.length) return null;
      var sorted = snaps.slice().sort(function (a, b) {
        return String(b.snapshot_date || '').localeCompare(String(a.snapshot_date || ''));
      });
      return sorted[0];
    }

    function _bindTooltips(container) {
      try { window.BenTradeUI && window.BenTradeUI.Tooltip && window.BenTradeUI.Tooltip.bindMetricsInContainer && window.BenTradeUI.Tooltip.bindMetricsInContainer(container); } catch (_) {}
      try { window.attachMetricTooltips && window.attachMetricTooltips(container); } catch (_) {}
    }

    function _flatten(eventsPayload, profilesByTicker) {
      var events = (eventsPayload && (eventsPayload.events || eventsPayload.upcoming || eventsPayload)) || [];
      if (!Array.isArray(events)) events = [];
      return events.map(function (item) {
        var ev = item.event || item;
        var snap = _normalizeSnapshot(item.latest_snapshot || item.snapshot || item.features || {});
        var prof = profilesByTicker[ev.ticker] || item.ticker_profile || {};
        var ve = _vol_edge(snap);
        var setup = _computeSetupScore(snap);
        // Defensive: EVA upcoming has been observed to return the event id under
        // either `event_id` or `id`, sometimes nested in `item`. Try all locations
        // so the Timeline tab fetch always has an id to call /events/{id}/features.
        var resolvedEid = ev.event_id || ev.id || item.event_id || item.id || null;
        return {
          __key: (resolvedEid || (ev.ticker + '|' + ev.earnings_date)),
          __event_id: resolvedEid,
          __event: ev,
          __snapshot: snap,
          ticker: ev.ticker,
          company_name: prof.company_name || ev.company_name || '',
          sector: prof.sector || ev.sector || '',
          earnings_date: ev.earnings_date,
          timing: ev.timing,
          days_out: _businessDaysUntil(ev.earnings_date),
          implied_move_pct: snap.implied_move_pct != null ? Number(snap.implied_move_pct) : null,
          realized_move_avg_8q: snap.realized_move_avg_8q != null ? Number(snap.realized_move_avg_8q) : null,
          vol_edge: ve,
          iv_rank_52w: snap.iv_rank_52w != null ? Number(snap.iv_rank_52w) : null,
          iv_vs_hv_30d: snap.iv_vs_hv_30d != null ? Number(snap.iv_vs_hv_30d) : null,
          term_structure_slope: snap.term_structure_slope != null ? Number(snap.term_structure_slope) : null,
          passes_baseline_filter: snap.passes_baseline_filter,
          setup_score: setup,
        };
      });
    }

    function _load(force) {
      var days = elWindow.value || '14';
      var cacheKey = 'eva:upcoming:' + days;
      _renderSkeleton();
      try {
        window.BenTradeEarningsAnalysisCache.fetchWithCache(
          cacheKey,
          function () {
            return window.BenTradeApi.getEvaUpcomingEvents(days);
          },
          {
            ttlMs: 5 * 60 * 1000,
            force: !!force,
            onCached: function (data) { _hydrate(data); },
            onSuccess: function (data) {
              elUpdated.textContent = 'Updated ' + new Date().toLocaleTimeString();
              _hydrate(data);
            },
            onError: function (err) {
              console.error('[EarningsAnalysis] getEvaUpcomingEvents failed', err);
              var msg = (err && err.status === 503) ?
                'Could not reach Earnings Vol Analyzer. Check that the service is running on 192.168.1.143:8200.' :
                'Failed to load events: ' + (err && err.message ? err.message : err);
              elTbody.innerHTML = '<tr><td colspan="' + COLUMNS.length + '" class="ea-state ea-state-error">' + _esc(msg) + '</td></tr>';
            }
          }
        );
      } catch (err) {
        console.error('[EarningsAnalysis] _load threw synchronously', err);
        elTbody.innerHTML = '<tr><td colspan="' + COLUMNS.length + '" class="ea-state ea-state-error">' +
          _esc('Internal error: ' + (err && err.message ? err.message : err)) + '</td></tr>';
      }
    }

    function _hydrate(payload) {
      // If event payload doesn't carry profile fields, do a lightweight one-shot
      // ticker lookup batch only for tickers we don't yet know.
      state.rows = _flatten(payload, state.profileByTicker);
      _populateSectors();
      _renderRows();
      _maybeFetchProfiles();
    }

    function _maybeFetchProfiles() {
      var missing = {};
      state.rows.forEach(function (r) {
        if ((!r.company_name || !r.sector) && r.ticker && !state.profileByTicker[r.ticker]) missing[r.ticker] = true;
      });
      var tickers = Object.keys(missing).slice(0, 20); // cap to be polite
      if (!tickers.length) return;
      Promise.all(tickers.map(function (t) {
        return window.BenTradeApi.getEvaTicker(t)
          .then(function (resp) { return { t: t, p: (resp && (resp.profile || resp)) || {} }; })
          .catch(function (err) {
            console.error('[EarningsAnalysis] getEvaTicker (profile backfill) failed', t, err);
            return { t: t, p: {} };
          });
      })).then(function (parts) {
        parts.forEach(function (x) { state.profileByTicker[x.t] = x.p; });
        // Re-flatten with new profile data without re-fetching events
        state.rows = state.rows.map(function (r) {
          var p = state.profileByTicker[r.ticker] || {};
          return Object.assign({}, r, {
            company_name: r.company_name || p.company_name || '',
            sector: r.sector || p.sector || '',
          });
        });
        _populateSectors();
        _renderRows();
      });
    }

    // ── Bindings ──
    _bindRowClickDelegation();
    elWindow.addEventListener('change', function () { _load(true); });
    elTradeable.addEventListener('change', _renderRows);
    elMinScore.addEventListener('input', function () {
      elMinScoreVal.textContent = elMinScore.value;
      _renderRows();
    });
    elSector.addEventListener('change', _renderRows);
    elSearch.addEventListener('input', _renderRows);
    elRefresh.addEventListener('click', function () {
      try { window.BenTradeEarningsAnalysisCache.clearAll && window.BenTradeEarningsAnalysisCache.clearAll(); } catch (_) {}
      _load(true);
    });

    // ── Connection toggle (mirrors Company Evaluator pattern) ──
    var _connRadios = scope.querySelectorAll('input[name="ea-conn-mode"]');
    var _connUrlEl  = scope.querySelector('#ea-conn-url');

    function _setConnRadioState(mode) {
      Array.prototype.forEach.call(_connRadios, function (r) { r.checked = (r.value === mode); });
    }
    function _showConnUrl(url, healthy) {
      if (!_connUrlEl) return;
      var dot = healthy ? '\u25CF' : '\u25CB';
      var color = healthy ? '#00c853' : '#ff1744';
      _connUrlEl.innerHTML = '<span style="color:' + color + ';">' + dot + '</span> ' + _esc(url);
      _connUrlEl.title = healthy ? 'Connected' : 'Cannot reach EVA at ' + url;
    }
    function _showConnWarning(url) {
      if (!_connUrlEl) return;
      _connUrlEl.innerHTML = '<span style="color:#ff1744;">\u25CB</span> ' + _esc(url) +
        ' <span style="color:#ff9800; font-size:0.68rem;">\u2014 not reachable</span>';
    }
    async function _checkEvaHealth(url) {
      try {
        var res = await fetch('/api/eva/status');
        if (!res.ok) { _showConnWarning(url); return; }
        var data = await res.json();
        if (data.service_healthy) _showConnUrl(url, true); else _showConnWarning(url);
      } catch (_e) { _showConnWarning(url); }
    }
    async function _loadConnectionState() {
      try {
        var res = await fetch('/api/eva/connection');
        if (!res.ok) return;
        var data = await res.json();
        _setConnRadioState(data.mode);
        _showConnUrl(data.url, null);
        _checkEvaHealth(data.url);
      } catch (_e) { /* ignore */ }
    }
    async function _switchConnectionMode(mode) {
      Array.prototype.forEach.call(_connRadios, function (r) { r.disabled = true; });
      try {
        var res = await fetch('/api/eva/connection', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: mode }),
        });
        if (!res.ok) {
          var err = await res.json().catch(function () { return {}; });
          alert('Failed to switch EVA mode: ' + (err.detail || 'unknown error'));
          _loadConnectionState();
          return;
        }
        var data = await res.json();
        _setConnRadioState(data.mode);
        _showConnUrl(data.url, null);
        // Cache invalidation: clear EVA cache so we refetch from new URL
        try { window.BenTradeEarningsAnalysisCache.clearAll && window.BenTradeEarningsAnalysisCache.clearAll(); } catch (_) {}
        await _checkEvaHealth(data.url);
        _load(true);
      } catch (e) {
        alert('Failed to switch connection: ' + e.message);
        _loadConnectionState();
      } finally {
        Array.prototype.forEach.call(_connRadios, function (r) { r.disabled = false; });
      }
    }
    Array.prototype.forEach.call(_connRadios, function (radio) {
      radio.addEventListener('change', function () {
        if (this.checked) _switchConnectionMode(this.value);
      });
    });
    _loadConnectionState();

    _load(false);

    return function cleanup() {
      if (state.pollTimer) clearInterval(state.pollTimer);
    };
  };
})();
