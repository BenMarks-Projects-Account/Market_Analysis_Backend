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

    // Initial load
    loadRuns();

    // Cleanup
    window.BenTradeActiveViewCleanup = function () {
      if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
      currentRunId = null;
      loadedCandidates = [];
      artifactIndex = {};
    };
  }

  /* ── Register ──────────────────────────────────────────────── */

  window.BenTradePages = window.BenTradePages || {};
  window.BenTradePages.initTradeManagementCenter = initTradeManagementCenter;
})();
