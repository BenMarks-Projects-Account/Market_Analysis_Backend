/**
 * BenTrade — Trade Management Center
 *
 * Candidate review + execution hub.
 * Loads pipeline run data (Step 15 ledger + response artifacts),
 * renders candidate cards with recommendation details, and wires
 * Execute Trade buttons to the Trade Ticket modal.
 */
(function () {
  'use strict';

  /* ── State ─────────────────────────────────────────────────── */

  var currentRunId = null;
  var loadedCandidates = [];
  var artifactIndex = {};
  var _pollTimer = null;
  var _activeRunning = false;

  /* ── API helpers ───────────────────────────────────────────── */

  function apiFetch(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  /* ── Formatting helpers ────────────────────────────────────── */

  function fmtPct(v) {
    if (v == null) return '—';
    return (Number(v) * 100).toFixed(0) + '%';
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    try {
      var d = new Date(iso);
      return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch (_) { return iso; }
  }

  function esc(s) {
    if (s == null) return '';
    var d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function statusClass(status) {
    switch (status) {
      case 'ready': return 'tmc-status-ready';
      case 'ready_degraded': return 'tmc-status-degraded';
      case 'failed': return 'tmc-status-failed';
      case 'skipped': return 'tmc-status-skipped';
      default: return 'tmc-status-unknown';
    }
  }

  function actionClass(action) {
    switch (action) {
      case 'buy': return 'tmc-action-buy';
      case 'hold': return 'tmc-action-hold';
      case 'pass': return 'tmc-action-pass';
      default: return 'tmc-action-unknown';
    }
  }

  /* ── Load run list ─────────────────────────────────────────── */

  function loadRuns() {
    var picker = document.getElementById('tmcRunPicker');
    if (!picker) return;

    apiFetch('/api/pipeline/runs')
      .then(function (data) {
        var runs = data.runs || [];
        picker.innerHTML = '<option value="">— Select Run —</option>';
        runs.forEach(function (r) {
          var opt = document.createElement('option');
          opt.value = r.run_id || '';
          var label = (r.run_id || '').substring(0, 12) + '… — ' + (r.status || '?') + ' — ' + fmtDate(r.started_at);
          opt.textContent = label;
          picker.appendChild(opt);
        });
        // Auto-select if there is a current run
        if (currentRunId) picker.value = currentRunId;
      })
      .catch(function (err) {
        console.error('[TMC] Failed to load runs:', err);
      });
  }

  /* ── Load run detail + candidates ──────────────────────────── */

  function loadRunDetail(runId) {
    if (!runId) {
      clearCandidates();
      return;
    }

    currentRunId = runId;
    var statusEl = document.getElementById('tmcRunStatus');
    var metaEl = document.getElementById('tmcRunMeta');
    if (statusEl) statusEl.textContent = 'Loading…';
    if (metaEl) metaEl.textContent = '';

    apiFetch('/api/pipeline/runs/' + encodeURIComponent(runId))
      .then(function (detail) {
        // Update run status
        if (statusEl) {
          statusEl.textContent = (detail.status || 'unknown').toUpperCase();
          statusEl.className = 'tmc-run-status tmc-run-' + (detail.status || 'unknown');
        }
        if (metaEl) {
          var parts = [];
          if (detail.started_at) parts.push(fmtDate(detail.started_at));
          if (detail.duration_ms != null) parts.push(Math.round(detail.duration_ms / 1000) + 's');
          metaEl.textContent = parts.join(' · ');
        }

        // Build artifact index for this run
        artifactIndex = {};
        (detail.artifacts || []).forEach(function (a) {
          artifactIndex[a.artifact_key || ''] = a;
        });

        // Extract candidate ledger
        var ledger = detail.ledger;
        if (!ledger) {
          showEmpty('No candidate ledger found in this run');
          return;
        }

        var rows = ledger.ledger_rows || [];
        if (rows.length === 0) {
          showEmpty('No candidates in ledger');
          return;
        }

        // Load full response data for each candidate
        loadCandidateResponses(runId, rows);
      })
      .catch(function (err) {
        console.error('[TMC] Failed to load run detail:', err);
        if (statusEl) statusEl.textContent = 'ERROR';
        showEmpty('Failed to load run: ' + err.message);
      });
  }

  function loadCandidateResponses(runId, ledgerRows) {
    // Find response artifact IDs from the artifact index
    var fetches = ledgerRows.map(function (row) {
      var artKey = 'response_' + (row.candidate_id || '');
      var artInfo = artifactIndex[artKey];
      if (!artInfo || !artInfo.artifact_id) {
        return Promise.resolve({ row: row, detail: null });
      }
      return apiFetch(
        '/api/pipeline/runs/' + encodeURIComponent(runId) +
        '/artifacts/' + encodeURIComponent(artInfo.artifact_id)
      ).then(function (artData) {
        return { row: row, detail: artData.data || artData };
      }).catch(function () {
        return { row: row, detail: null };
      });
    });

    Promise.all(fetches).then(function (results) {
      loadedCandidates = results;
      renderCandidates(results);
    });
  }

  /* ── Render candidates ─────────────────────────────────────── */

  function clearCandidates() {
    currentRunId = null;
    loadedCandidates = [];
    artifactIndex = {};
    var grid = document.getElementById('tmcCandidateGrid');
    if (grid) {
      grid.innerHTML = '';
      showEmpty('Select a pipeline run to view candidates');
    }
    var count = document.getElementById('tmcCandidateCount');
    if (count) count.textContent = '0';
  }

  function showEmpty(msg) {
    var grid = document.getElementById('tmcCandidateGrid');
    if (!grid) return;
    grid.innerHTML =
      '<div class="tmc-empty-state">' +
        '<div class="tmc-empty-icon">◎</div>' +
        '<div class="tmc-empty-text">' + esc(msg) + '</div>' +
      '</div>';
    var count = document.getElementById('tmcCandidateCount');
    if (count) count.textContent = '0';
  }

  function renderCandidates(results) {
    var grid = document.getElementById('tmcCandidateGrid');
    var countEl = document.getElementById('tmcCandidateCount');
    if (!grid) return;

    // Sort: buy first, then hold, then pass; within bucket by conviction desc
    var sorted = results.slice().sort(function (a, b) {
      var pa = actionPriority(a), pb = actionPriority(b);
      if (pa !== pb) return pa - pb;
      return (convictionOf(b) || 0) - (convictionOf(a) || 0);
    });

    if (countEl) countEl.textContent = String(sorted.length);
    grid.innerHTML = '';

    sorted.forEach(function (item, idx) {
      grid.appendChild(buildCandidateCard(item, idx));
    });
  }

  function actionPriority(item) {
    var action = (item.row && item.row.action) || 'unknown';
    switch (action) {
      case 'buy': return 1;
      case 'hold': return 2;
      case 'pass': return 3;
      default: return 4;
    }
  }

  function convictionOf(item) {
    return item.row && item.row.conviction;
  }

  /* ── Build candidate card ──────────────────────────────────── */

  function buildCandidateCard(item, idx) {
    var row = item.row || {};
    var detail = item.detail || {};
    var rec = detail.recommendation_summary || {};
    var policy = detail.policy_summary || {};
    var exec = detail.execution_summary || {};
    var identity = detail.candidate_identity || {};
    var quality = detail.quality_summary || {};

    var symbol = row.symbol || identity.symbol || '???';
    var action = row.action || rec.action || '—';
    var conviction = row.conviction != null ? row.conviction : rec.conviction;
    var status = row.response_status || detail.response_status || 'unknown';
    var policyOutcome = row.policy_outcome || policy.overall_outcome || '—';
    var rationale = rec.rationale_summary || '';
    var points = rec.key_supporting_points || [];
    var risks = rec.key_risks || [];
    var provider = row.provider || exec.provider || '—';
    var modelName = row.model_name || exec.model_name || '—';
    var latency = exec.latency_ms;
    var eventSens = rec.event_sensitivity || '—';
    var portfolioFit = rec.portfolio_fit || '—';
    var sizing = rec.sizing_guidance || '—';
    var scannerKey = row.scanner_key || identity.scanner_key || '';
    var strategyType = identity.strategy_type || '';

    var card = document.createElement('div');
    card.className = 'tmc-card';
    card.dataset.candidateIdx = idx;

    // Header
    var header =
      '<div class="tmc-card-header">' +
        '<div class="tmc-card-symbol">' + esc(symbol) + '</div>' +
        '<div class="tmc-card-action ' + actionClass(action) + '">' + esc(action.toUpperCase()) + '</div>' +
        '<div class="tmc-card-status ' + statusClass(status) + '">' + esc(status) + '</div>' +
      '</div>';

    // Conviction bar
    var convPct = conviction != null ? Math.round(conviction * 100) : 0;
    var convBar =
      '<div class="tmc-conviction-row">' +
        '<span class="tmc-label">Conviction</span>' +
        '<div class="tmc-conviction-bar-wrap">' +
          '<div class="tmc-conviction-bar" style="width:' + convPct + '%"></div>' +
        '</div>' +
        '<span class="tmc-conviction-value">' + fmtPct(conviction) + '</span>' +
      '</div>';

    // Metrics grid
    var metrics =
      '<div class="tmc-metrics">' +
        '<div class="tmc-metric"><span class="tmc-metric-label">Policy</span><span class="tmc-metric-value">' + esc(policyOutcome) + '</span></div>' +
        '<div class="tmc-metric"><span class="tmc-metric-label">Event Risk</span><span class="tmc-metric-value">' + esc(eventSens) + '</span></div>' +
        '<div class="tmc-metric"><span class="tmc-metric-label">Portfolio Fit</span><span class="tmc-metric-value">' + esc(portfolioFit) + '</span></div>' +
        '<div class="tmc-metric"><span class="tmc-metric-label">Sizing</span><span class="tmc-metric-value">' + esc(sizing) + '</span></div>' +
      '</div>';

    // Rationale
    var rationaleHtml = '';
    if (rationale) {
      rationaleHtml =
        '<div class="tmc-rationale">' +
          '<div class="tmc-rationale-label">Rationale</div>' +
          '<div class="tmc-rationale-text">' + esc(rationale) + '</div>' +
        '</div>';
    }

    // Supporting points + risks
    var pointsHtml = '';
    if (points.length > 0) {
      pointsHtml = '<div class="tmc-points"><div class="tmc-points-label">Supporting Points</div><ul class="tmc-points-list">';
      points.forEach(function (p) { pointsHtml += '<li>' + esc(p) + '</li>'; });
      pointsHtml += '</ul></div>';
    }

    var risksHtml = '';
    if (risks.length > 0) {
      risksHtml = '<div class="tmc-risks"><div class="tmc-risks-label">Risks</div><ul class="tmc-risks-list">';
      risks.forEach(function (r) { risksHtml += '<li>' + esc(r) + '</li>'; });
      risksHtml += '</ul></div>';
    }

    // Model metadata
    var modelMeta =
      '<div class="tmc-model-meta">' +
        '<span class="tmc-meta-item">' + esc(provider) + '</span>' +
        '<span class="tmc-meta-sep">·</span>' +
        '<span class="tmc-meta-item">' + esc(modelName) + '</span>' +
        (latency != null ? '<span class="tmc-meta-sep">·</span><span class="tmc-meta-item">' + latency + 'ms</span>' : '') +
      '</div>';

    // Footer with Execute Trade button
    var canExecute = action === 'buy' && status === 'ready';
    var footer =
      '<div class="tmc-card-footer">' +
        '<button class="btn tmc-btn-execute' + (canExecute ? '' : ' tmc-btn-disabled') + '" ' +
          'data-candidate-idx="' + idx + '"' +
          (canExecute ? '' : ' disabled') +
          ' title="' + (canExecute ? 'Open trade ticket for execution' : 'Only BUY + ready candidates can be executed') + '">' +
          '⚡ Execute Trade' +
        '</button>' +
        '<span class="tmc-scanner-badge">' + esc(scannerKey || strategyType || '—') + '</span>' +
      '</div>';

    card.innerHTML = header + convBar + metrics + rationaleHtml + pointsHtml + risksHtml + modelMeta + footer;

    // Wire execute button
    var btn = card.querySelector('.tmc-btn-execute');
    if (btn && canExecute) {
      btn.addEventListener('click', function () {
        executeCandidate(item);
      });
    }

    return card;
  }

  /* ── Execute candidate → Trade Ticket ──────────────────────── */

  function executeCandidate(item) {
    var row = item.row || {};
    var detail = item.detail || {};
    var identity = detail.candidate_identity || {};
    var rec = detail.recommendation_summary || {};

    // Adapt pipeline candidate data to trade ticket format.
    // The trade ticket model normalizer can synthesize legs from header strikes.
    var tradeData = {
      underlying:    row.symbol || identity.symbol || '',
      symbol:        row.symbol || identity.symbol || '',
      strategyId:    identity.strategy_type || row.scanner_key || '',
      strategyLabel: (identity.strategy_type || row.scanner_key || '').replace(/_/g, ' '),
      quantity:      1,
      orderType:     'limit',
      tif:           'day',
      limitPrice:    null,
      maxProfit:     null,
      maxLoss:       null,
      pop:           null,
      ev:            null,
      ror:           null,
      conviction:    row.conviction,
      rationale:     rec.rationale_summary || '',
      // Pipeline doesn't carry detailed strike/expiry — trade ticket will
      // prompt for these or they can be sourced from candidate data
      expiration:    null,
      dte:           null,
      shortStrike:   null,
      longStrike:    null,
      width:         null,
      netPremium:    null,
      legs:          [],
    };

    // Open trade ticket if available
    if (window.BenTradeTradeTicket && typeof window.BenTradeTradeTicket.open === 'function') {
      window.BenTradeTradeTicket.open(tradeData, { source: 'trade_management_center' });
    } else {
      console.warn('[TMC] Trade Ticket module not available');
      alert('Trade Ticket module is not loaded. Cannot execute trade.');
    }
  }

  /* ── Latest run shortcut ───────────────────────────────────── */

  function loadLatestRun() {
    apiFetch('/api/pipeline/runs')
      .then(function (data) {
        var runs = data.runs || [];
        if (runs.length === 0) {
          showEmpty('No pipeline runs found');
          return;
        }
        var latest = runs[0];
        var picker = document.getElementById('tmcRunPicker');
        if (picker) picker.value = latest.run_id || '';
        loadRunDetail(latest.run_id);
      })
      .catch(function (err) {
        console.error('[TMC] Failed to load latest run:', err);
      });
  }

  /* ═══════════════════════════════════════════════════════════════
   *  Active Trade Pipeline — Section 2
   * ═══════════════════════════════════════════════════════════════ */

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

  function runActivePipeline() {
    if (_activeRunning) return;
    _activeRunning = true;

    var btn = document.getElementById('tmcRunActiveBtn');
    if (btn) { btn.textContent = '⏳ Running…'; btn.disabled = true; }

    var skipModel = false;
    var cb = document.getElementById('tmcSkipModel');
    if (cb) skipModel = cb.checked;

    var url = '/api/active-trade-pipeline/run?skip_model=' + (skipModel ? 'true' : 'false');

    fetch(url, { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        _activeRunning = false;
        if (btn) { btn.textContent = '▶ Analyse Positions'; btn.disabled = false; }
        if (data.ok === false) {
          showActiveEmpty('Pipeline error: ' + ((data.error || {}).message || 'unknown'));
          return;
        }
        renderActiveResults(data);
      })
      .catch(function (err) {
        _activeRunning = false;
        if (btn) { btn.textContent = '▶ Analyse Positions'; btn.disabled = false; }
        console.error('[TMC] Active pipeline failed:', err);
        showActiveEmpty('Failed to run pipeline: ' + err.message);
      });
  }

  function loadLatestActiveResults() {
    apiFetch('/api/active-trade-pipeline/results')
      .then(function (data) {
        if (data.ok === false) {
          showActiveEmpty((data.error || {}).message || 'No results available');
          return;
        }
        renderActiveResults(data);
      })
      .catch(function (err) {
        console.error('[TMC] Failed to load active results:', err);
        showActiveEmpty('Failed to load results');
      });
  }

  function showActiveEmpty(msg) {
    var grid = document.getElementById('tmcActiveTradeGrid');
    if (grid) {
      grid.innerHTML =
        '<div class="tmc-empty-state">' +
          '<div class="tmc-empty-icon">◉</div>' +
          '<div class="tmc-empty-text">' + esc(msg) + '</div>' +
        '</div>';
    }
    var count = document.getElementById('tmcActiveCount');
    if (count) { count.textContent = '—'; count.className = 'tmc-count-badge tmc-count-muted'; }
  }

  function renderActiveResults(data) {
    var recs = data.recommendations || [];
    var grid = document.getElementById('tmcActiveTradeGrid');
    var countEl = document.getElementById('tmcActiveCount');

    if (!grid) return;

    if (recs.length === 0) {
      showActiveEmpty('No active trades found');
      return;
    }

    // Sort: urgent first, then by urgency desc, then conviction desc
    var sorted = recs.slice().sort(function (a, b) {
      var ua = a.urgency || 0, ub = b.urgency || 0;
      if (ua !== ub) return ub - ua;
      return (b.conviction || 0) - (a.conviction || 0);
    });

    if (countEl) {
      countEl.textContent = String(sorted.length);
      countEl.className = 'tmc-count-badge';
    }

    grid.innerHTML = '';
    sorted.forEach(function (rec) {
      grid.appendChild(buildActiveTradeCard(rec));
    });

    // Show run metadata
    var summary = data.summary || {};
    var metaHtml =
      '<div class="tmc-active-run-meta">' +
        '<span class="tmc-meta-item">Run ' + esc((data.run_id || '').substring(0, 16)) + '</span>' +
        '<span class="tmc-meta-sep">·</span>' +
        '<span class="tmc-meta-item">' + (data.duration_ms || 0) + 'ms</span>' +
        '<span class="tmc-meta-sep">·</span>' +
        '<span class="tmc-meta-item">' + (summary.hold_count || 0) + ' hold</span>' +
        '<span class="tmc-meta-sep">·</span>' +
        '<span class="tmc-meta-item">' + (summary.reduce_count || 0) + ' reduce</span>' +
        '<span class="tmc-meta-sep">·</span>' +
        '<span class="tmc-meta-item">' + (summary.close_count || 0) + ' close</span>' +
        (summary.urgent_review_count > 0
          ? '<span class="tmc-meta-sep">·</span><span class="tmc-meta-item tmc-urgency-high">' + summary.urgent_review_count + ' urgent</span>'
          : '') +
      '</div>';

    grid.insertAdjacentHTML('beforebegin',
      '<div id="tmcActiveRunMeta">' + metaHtml + '</div>'
    );
  }

  function buildActiveTradeCard(rec) {
    var symbol = rec.symbol || '???';
    var recommendation = (rec.recommendation || '—').toUpperCase();
    var conviction = rec.conviction;
    var urgency = rec.urgency || 1;
    var rationale = rec.rationale_summary || '';
    var points = rec.key_supporting_points || [];
    var risks = rec.key_risks || [];
    var engineSummary = rec.internal_engine_summary || {};
    var engineMetrics = rec.internal_engine_metrics || {};
    var modelSummary = rec.model_summary || {};
    var posSnap = rec.position_snapshot || {};
    var strategy = rec.strategy || '';
    var dte = rec.dte;
    var marketAlign = rec.market_alignment || '—';
    var portfolioFit = rec.portfolio_fit || '—';
    var eventSens = rec.event_sensitivity || '—';
    var nextMove = rec.suggested_next_move || '';
    var healthScore = engineSummary.trade_health_score;
    var riskFlags = rec.internal_engine_flags || [];
    var isDegraded = rec.is_degraded;
    var degradedReasons = rec.degraded_reasons || [];

    var card = document.createElement('div');
    card.className = 'tmc-card tmc-active-card';

    // Header
    var header =
      '<div class="tmc-card-header">' +
        '<div class="tmc-card-symbol">' + esc(symbol) + '</div>' +
        '<div class="tmc-card-action ' + recClass(recommendation) + '">' + esc(recommendation) + '</div>' +
        '<div class="tmc-card-status ' + urgencyClass(urgency) + '">' + esc(urgencyLabel(urgency)) + '</div>' +
      '</div>';

    // Conviction bar
    var convPct = conviction != null ? Math.round(conviction * 100) : 0;
    var convBar =
      '<div class="tmc-conviction-row">' +
        '<span class="tmc-label">Conviction</span>' +
        '<div class="tmc-conviction-bar-wrap">' +
          '<div class="tmc-conviction-bar" style="width:' + convPct + '%"></div>' +
        '</div>' +
        '<span class="tmc-conviction-value">' + fmtPct(conviction) + '</span>' +
      '</div>';

    // P&L snapshot
    var pnlVal = posSnap.unrealized_pnl;
    var pnlPct = posSnap.unrealized_pnl_pct;
    var pnlClass = pnlVal != null ? (pnlVal >= 0 ? 'tmc-pnl-positive' : 'tmc-pnl-negative') : '';
    var pnlText = pnlVal != null ? '$' + pnlVal.toFixed(2) : '—';
    var pnlPctText = pnlPct != null ? '(' + (pnlPct * 100).toFixed(1) + '%)' : '';

    // Metrics grid — active trade version
    var metrics =
      '<div class="tmc-metrics">' +
        '<div class="tmc-metric"><span class="tmc-metric-label">P&L</span><span class="tmc-metric-value ' + pnlClass + '">' + pnlText + ' ' + pnlPctText + '</span></div>' +
        '<div class="tmc-metric"><span class="tmc-metric-label">Health</span><span class="tmc-metric-value">' + (healthScore != null ? healthScore + '/100' : '—') + '</span></div>' +
        '<div class="tmc-metric"><span class="tmc-metric-label">DTE</span><span class="tmc-metric-value">' + (dte != null ? dte + 'd' : '—') + '</span></div>' +
        '<div class="tmc-metric"><span class="tmc-metric-label">Market</span><span class="tmc-metric-value">' + esc(marketAlign) + '</span></div>' +
      '</div>';

    // Engine component scores
    var engineHtml = '';
    var compKeys = Object.keys(engineMetrics);
    if (compKeys.length > 0) {
      engineHtml = '<div class="tmc-engine-scores"><div class="tmc-points-label">Engine Scores</div><div class="tmc-engine-grid">';
      compKeys.forEach(function (k) {
        var v = engineMetrics[k];
        var displayVal = v != null ? Math.round(v) : '—';
        engineHtml += '<span class="tmc-engine-item">' + esc(k.replace(/_/g, ' ')) + ': <strong>' + displayVal + '</strong></span>';
      });
      engineHtml += '</div></div>';
    }

    // Risk flags
    var flagsHtml = '';
    if (riskFlags.length > 0) {
      flagsHtml = '<div class="tmc-risk-flags">';
      riskFlags.forEach(function (f) {
        flagsHtml += '<span class="tmc-risk-flag">' + esc(f) + '</span>';
      });
      flagsHtml += '</div>';
    }

    // Rationale
    var rationaleHtml = '';
    if (rationale) {
      rationaleHtml =
        '<div class="tmc-rationale">' +
          '<div class="tmc-rationale-label">Rationale</div>' +
          '<div class="tmc-rationale-text">' + esc(rationale) + '</div>' +
        '</div>';
    }

    // Supporting points + risks
    var pointsHtml = '';
    if (points.length > 0) {
      pointsHtml = '<div class="tmc-points"><div class="tmc-points-label">Supporting Points</div><ul class="tmc-points-list">';
      points.forEach(function (p) { pointsHtml += '<li>' + esc(p) + '</li>'; });
      pointsHtml += '</ul></div>';
    }

    var risksHtml = '';
    if (risks.length > 0) {
      risksHtml = '<div class="tmc-risks"><div class="tmc-risks-label">Risks</div><ul class="tmc-risks-list">';
      risks.forEach(function (r) { risksHtml += '<li>' + esc(r) + '</li>'; });
      risksHtml += '</ul></div>';
    }

    // Next move
    var nextMoveHtml = '';
    if (nextMove) {
      nextMoveHtml =
        '<div class="tmc-next-move">' +
          '<div class="tmc-points-label">Suggested Next Move</div>' +
          '<div class="tmc-next-move-text">' + esc(nextMove) + '</div>' +
        '</div>';
    }

    // Model metadata
    var modelMeta = '';
    if (modelSummary.model_available) {
      modelMeta =
        '<div class="tmc-model-meta">' +
          '<span class="tmc-meta-item">' + esc(modelSummary.provider || '—') + '</span>' +
          '<span class="tmc-meta-sep">·</span>' +
          '<span class="tmc-meta-item">' + esc(modelSummary.model_name || '—') + '</span>' +
          (modelSummary.latency_ms != null ? '<span class="tmc-meta-sep">·</span><span class="tmc-meta-item">' + modelSummary.latency_ms + 'ms</span>' : '') +
        '</div>';
    } else {
      modelMeta = '<div class="tmc-model-meta"><span class="tmc-meta-item tmc-meta-degraded">Engine only (model unavailable)</span></div>';
    }

    // Degraded indicator
    var degradedHtml = '';
    if (isDegraded && degradedReasons.length > 0) {
      degradedHtml =
        '<div class="tmc-degraded-notice">' +
          '<span class="tmc-degraded-icon">⚠</span> Degraded: ' + esc(degradedReasons.slice(0, 3).join(', ')) +
        '</div>';
    }

    // Footer with action buttons
    var isClose = recommendation === 'CLOSE' || recommendation === 'URGENT_REVIEW';
    var isReduce = recommendation === 'REDUCE';
    var footer =
      '<div class="tmc-card-footer">' +
        '<button class="btn tmc-btn-execute" ' +
          'data-symbol="' + esc(symbol) + '" ' +
          'data-action="execute" ' +
          'title="Open trade ticket for adjustment">' +
          '⚡ Execute' +
        '</button>' +
        '<button class="btn tmc-btn-close' + (isClose || isReduce ? '' : ' tmc-btn-disabled') + '" ' +
          'data-symbol="' + esc(symbol) + '" ' +
          'data-action="close" ' +
          (isClose || isReduce ? '' : 'disabled ') +
          'title="' + (isClose || isReduce ? 'Close or reduce this position' : 'Position not flagged for closing') + '">' +
          '✕ Close' +
        '</button>' +
        '<span class="tmc-scanner-badge">' + esc(strategy || '—') + '</span>' +
        (rec.recommendation_source ? '<span class="tmc-source-badge">via ' + esc(rec.recommendation_source) + '</span>' : '') +
      '</div>';

    card.innerHTML = header + convBar + metrics + engineHtml + flagsHtml +
      rationaleHtml + pointsHtml + risksHtml + nextMoveHtml +
      modelMeta + degradedHtml + footer;

    // Wire action buttons
    var execBtn = card.querySelector('[data-action="execute"]');
    if (execBtn) {
      execBtn.addEventListener('click', function () {
        executeActivePosition(rec, 'execute');
      });
    }
    var closeBtn = card.querySelector('[data-action="close"]');
    if (closeBtn && !closeBtn.disabled) {
      closeBtn.addEventListener('click', function () {
        executeActivePosition(rec, 'close');
      });
    }

    return card;
  }

  /* ── Execute active position → Trade Ticket ─────────────── */

  function executeActivePosition(rec, action) {
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
      console.warn('[TMC] TradeTicket not available — trade data:', tradeData);
      alert('Trade Ticket module not loaded. Trade data logged to console.');
    }
  }

  /* ── Page init ─────────────────────────────────────────────── */

  function initTradeManagementCenter(viewEl) {
    if (!viewEl) return;

    // Wire controls
    var picker = document.getElementById('tmcRunPicker');
    var refreshBtn = document.getElementById('tmcRefreshBtn');
    var latestBtn = document.getElementById('tmcLatestBtn');

    if (picker) {
      picker.addEventListener('change', function () {
        loadRunDetail(picker.value);
      });
    }
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        loadRuns();
      });
    }
    if (latestBtn) {
      latestBtn.addEventListener('click', function () {
        loadLatestRun();
      });
    }

    // Active trade pipeline controls
    var runActiveBtn = document.getElementById('tmcRunActiveBtn');
    var refreshActiveBtn = document.getElementById('tmcRefreshActiveBtn');

    if (runActiveBtn) {
      runActiveBtn.addEventListener('click', function () {
        runActivePipeline();
      });
    }
    if (refreshActiveBtn) {
      refreshActiveBtn.addEventListener('click', function () {
        loadLatestActiveResults();
      });
    }

    // Initial load
    loadRuns();

    // Cleanup
    window.BenTradeActiveViewCleanup = function () {
      if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
      currentRunId = null;
      loadedCandidates = [];
      artifactIndex = {};
      _activeRunning = false;
      var metaEl = document.getElementById('tmcActiveRunMeta');
      if (metaEl) metaEl.remove();
    };
  }

  /* ── Register ──────────────────────────────────────────────── */

  window.BenTradePages = window.BenTradePages || {};
  window.BenTradePages.initTradeManagementCenter = initTradeManagementCenter;
})();
