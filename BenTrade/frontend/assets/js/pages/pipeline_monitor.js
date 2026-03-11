// Pipeline Monitor — page module
// Renders pipeline run list, detail, stage tracker, candidate ledger,
// event log, and artifact inspector.
(function () {
  'use strict';

  window.BenTradePages = window.BenTradePages || {};

  /* ── Constants ──────────────────────────────────────────────── */
  var STAGE_LABELS = {
    market_data: 'Market Data',
    market_model_analysis: 'Market Model',
    scanners: 'Scanners',
    candidate_selection: 'Selection',
    shared_context: 'Context',
    candidate_enrichment: 'Enrichment',
    events: 'Events',
    policy: 'Policy',
    orchestration: 'Orchestration',
    prompt_payload: 'Prompt',
    final_model_decision: 'Model Decision',
    final_response_normalization: 'Response',
  };

  var STATUS_ICONS = {
    completed: '✓',
    failed: '✗',
    running: '◎',
    pending: '○',
    skipped: '–',
  };

  var STATUS_CLASSES = {
    completed: 'pm-status-completed',
    failed: 'pm-status-failed',
    running: 'pm-status-running',
    pending: 'pm-status-pending',
    skipped: 'pm-status-skipped',
    partial_failed: 'pm-status-failed',
  };

  /* ── Helpers ────────────────────────────────────────────────── */
  var fmt = (window.BenTradeUtils && window.BenTradeUtils.format) || {};

  function esc(val) {
    if (val == null) return '';
    return String(val).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function fmtMs(ms) {
    if (ms == null) return 'N/A';
    if (ms < 1000) return ms + 'ms';
    return (ms / 1000).toFixed(2) + 's';
  }

  function fmtTime(iso) {
    if (!iso) return 'N/A';
    try {
      var d = new Date(iso);
      return d.toLocaleTimeString() + ' ' + d.toLocaleDateString();
    } catch (_) {
      return String(iso);
    }
  }

  function shortId(id) {
    if (!id) return 'N/A';
    if (id.length <= 16) return id;
    return id.slice(0, 8) + '…' + id.slice(-4);
  }

  function statusPill(status) {
    var cls = STATUS_CLASSES[status] || 'pm-status-pending';
    return '<span class="qtPill ' + cls + '">' + esc(status || 'unknown') + '</span>';
  }

  function setDisplay(el, show) {
    if (el) el.style.display = show ? '' : 'none';
  }

  /* ── Init ───────────────────────────────────────────────────── */
  window.BenTradePages.initPipelineMonitor = function initPipelineMonitor(rootEl) {
    var scope = rootEl || document.body;

    // DOM refs
    var elRunCount = scope.querySelector('#pmRunCount');
    var elDemoBtn = scope.querySelector('#pmDemoRunBtn');
    var elRefreshBtn = scope.querySelector('#pmRefreshBtn');
    var elErrorBanner = scope.querySelector('#pmErrorBanner');
    var elLoading = scope.querySelector('#pmLoading');
    var elEmpty = scope.querySelector('#pmEmpty');
    var elRunList = scope.querySelector('#pmRunList');
    var elRunRows = scope.querySelector('#pmRunRows');
    var elRunDetail = scope.querySelector('#pmRunDetail');
    var elBackBtn = scope.querySelector('#pmBackToList');
    var elRunHeaderGrid = scope.querySelector('#pmRunHeaderGrid');
    var elStageTracker = scope.querySelector('#pmStageTracker');
    var elLedgerSection = scope.querySelector('#pmLedgerSection');
    var elLedgerRows = scope.querySelector('#pmLedgerRows');
    var elEventList = scope.querySelector('#pmEventList');
    var elEventLevelFilter = scope.querySelector('#pmEventLevelFilter');
    var elEventStageFilter = scope.querySelector('#pmEventStageFilter');
    var elArtifactGrid = scope.querySelector('#pmArtifactGrid');
    var elArtifactDetail = scope.querySelector('#pmArtifactDetail');
    var elArtifactDetailTitle = scope.querySelector('#pmArtifactDetailTitle');
    var elArtifactJson = scope.querySelector('#pmArtifactJson');
    var elArtifactClose = scope.querySelector('#pmArtifactClose');

    var currentRunId = null;
    var currentRunData = null;

    /* ── API helpers ──────────────────────────────────────────── */
    function apiFetch(url) {
      return fetch(url).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
    }

    function showError(msg) {
      if (elErrorBanner) {
        elErrorBanner.textContent = msg;
        elErrorBanner.style.display = '';
      }
    }

    function clearError() {
      if (elErrorBanner) {
        elErrorBanner.textContent = '';
        elErrorBanner.style.display = 'none';
      }
    }

    /* ── Run List View ────────────────────────────────────────── */
    function showRunList() {
      setDisplay(elRunList, true);
      setDisplay(elRunDetail, false);
      currentRunId = null;
      currentRunData = null;
    }

    function loadRunList() {
      clearError();
      setDisplay(elLoading, true);
      setDisplay(elEmpty, false);
      setDisplay(elRunList, false);
      setDisplay(elRunDetail, false);

      apiFetch('/api/pipeline/runs')
        .then(function (data) {
          setDisplay(elLoading, false);
          var runs = data.runs || [];
          if (elRunCount) elRunCount.textContent = runs.length + ' run' + (runs.length !== 1 ? 's' : '');
          if (runs.length === 0) {
            setDisplay(elEmpty, true);
            return;
          }
          renderRunTable(runs);
          setDisplay(elRunList, true);
        })
        .catch(function (err) {
          setDisplay(elLoading, false);
          showError('Failed to load pipeline runs: ' + err.message);
        });
    }

    function renderRunTable(runs) {
      if (!elRunRows) return;
      var html = '';
      for (var i = 0; i < runs.length; i++) {
        var r = runs[i];
        var stagesInfo = (r.completed_stages || 0) + '/' + ((r.completed_stages || 0) + (r.failed_stages || 0) + (r.pending_stages || 0));
        html += '<tr class="pm-run-row" data-run-id="' + esc(r.run_id) + '">';
        html += '<td class="pm-cell-id" title="' + esc(r.run_id) + '">' + esc(shortId(r.run_id)) + '</td>';
        html += '<td>' + statusPill(r.status) + '</td>';
        html += '<td>' + esc(r.trigger_source || '—') + '</td>';
        html += '<td>' + esc(fmtTime(r.started_at)) + '</td>';
        html += '<td>' + esc(fmtMs(r.duration_ms)) + '</td>';
        html += '<td>' + esc(stagesInfo) + '</td>';
        html += '<td>' + (r.error_count > 0 ? '<span class="pm-error-count">' + r.error_count + '</span>' : '0') + '</td>';
        html += '<td><button class="btn pm-btn pm-btn-sm pm-inspect-btn" data-run-id="' + esc(r.run_id) + '">Inspect</button></td>';
        html += '</tr>';
      }
      elRunRows.innerHTML = html;
    }

    /* ── Run Detail View ──────────────────────────────────────── */
    function loadRunDetail(runId) {
      clearError();
      currentRunId = runId;
      setDisplay(elRunList, false);
      setDisplay(elRunDetail, true);
      setDisplay(elLoading, true);

      apiFetch('/api/pipeline/runs/' + encodeURIComponent(runId))
        .then(function (data) {
          setDisplay(elLoading, false);
          currentRunData = data;
          renderRunHeader(data);
          renderStageTracker(data.stages || []);
          renderLedger(data.ledger);
          renderEvents(data.events || []);
          renderArtifacts(data.artifacts || []);
          populateStageFilter(data.stages || []);
        })
        .catch(function (err) {
          setDisplay(elLoading, false);
          showError('Failed to load run detail: ' + err.message);
        });
    }

    /* ── Run Header ───────────────────────────────────────────── */
    function renderRunHeader(data) {
      if (!elRunHeaderGrid) return;
      var items = [
        { label: 'Run ID', value: shortId(data.run_id), title: data.run_id },
        { label: 'Status', value: statusPill(data.status), raw: true },
        { label: 'Trigger', value: data.trigger_source || 'N/A' },
        { label: 'Pipeline Version', value: data.pipeline_version || 'N/A' },
        { label: 'Started', value: fmtTime(data.started_at) },
        { label: 'Ended', value: fmtTime(data.ended_at) },
        { label: 'Duration', value: fmtMs(data.duration_ms) },
        { label: 'Errors', value: String(data.error_count || 0), cls: (data.error_count > 0 ? 'negative' : '') },
      ];
      var counters = data.candidate_counters || {};
      var counterKeys = Object.keys(counters);
      for (var k = 0; k < counterKeys.length; k++) {
        items.push({ label: counterKeys[k].replace(/_/g, ' '), value: String(counters[counterKeys[k]]) });
      }
      var html = '';
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        html += '<div class="pm-header-metric">';
        html += '<div class="metric-label">' + esc(it.label) + '</div>';
        if (it.raw) {
          html += '<div class="metric-value">' + it.value + '</div>';
        } else {
          html += '<div class="metric-value' + (it.cls ? ' ' + it.cls : '') + '"' + (it.title ? ' title="' + esc(it.title) + '"' : '') + '>' + esc(it.value) + '</div>';
        }
        html += '</div>';
      }
      elRunHeaderGrid.innerHTML = html;
    }

    /* ── Stage Tracker ────────────────────────────────────────── */
    function renderStageTracker(stages) {
      if (!elStageTracker) return;
      var html = '<div class="pm-stage-pipeline">';
      for (var i = 0; i < stages.length; i++) {
        var s = stages[i];
        var cls = STATUS_CLASSES[s.status] || 'pm-status-pending';
        var icon = STATUS_ICONS[s.status] || '○';
        var label = STAGE_LABELS[s.stage_key] || s.label || s.stage_key;
        var dur = fmtMs(s.duration_ms);
        var artCount = s.artifact_count || 0;

        html += '<div class="pm-stage-node ' + cls + '" title="' + esc(s.stage_key) + ' — ' + esc(s.status) + '">';
        html += '<div class="pm-stage-icon">' + icon + '</div>';
        html += '<div class="pm-stage-label">' + esc(label) + '</div>';
        html += '<div class="pm-stage-meta">' + esc(dur);
        if (artCount > 0) html += ' · ' + artCount + ' art';
        html += '</div>';

        // Summary counts
        var sc = s.summary_counts || {};
        var scKeys = Object.keys(sc);
        if (scKeys.length > 0) {
          html += '<div class="pm-stage-counts">';
          for (var j = 0; j < Math.min(scKeys.length, 3); j++) {
            html += '<span class="pm-stage-count-item">' + esc(scKeys[j]) + ': ' + esc(String(sc[scKeys[j]])) + '</span>';
          }
          html += '</div>';
        }

        // Error detail
        if (s.error) {
          html += '<div class="pm-stage-error" title="' + esc(JSON.stringify(s.error)) + '">';
          html += '⚠ ' + esc(s.error.message || s.error.code || 'Error');
          html += '</div>';
        }

        html += '</div>';
        if (i < stages.length - 1) {
          html += '<div class="pm-stage-connector ' + cls + '"></div>';
        }
      }
      html += '</div>';
      elStageTracker.innerHTML = html;
    }

    /* ── Candidate Ledger ─────────────────────────────────────── */
    function renderLedger(ledger) {
      if (!ledger || !ledger.ledger_rows || ledger.ledger_rows.length === 0) {
        setDisplay(elLedgerSection, false);
        return;
      }
      setDisplay(elLedgerSection, true);
      if (!elLedgerRows) return;

      var rows = ledger.ledger_rows;
      var html = '';
      for (var i = 0; i < rows.length; i++) {
        var r = rows[i];
        var usableIcon = r.downstream_usable ? '✓' : '✗';
        var usableCls = r.downstream_usable ? 'positive' : 'negative';
        html += '<tr>';
        html += '<td title="' + esc(r.candidate_id) + '">' + esc(shortId(r.candidate_id)) + '</td>';
        html += '<td><strong>' + esc(r.symbol || '—') + '</strong></td>';
        html += '<td>' + esc(r.action || '—') + '</td>';
        html += '<td>' + (r.conviction != null ? Number(r.conviction).toFixed(2) : 'N/A') + '</td>';
        html += '<td>' + esc(r.policy_outcome || '—') + '</td>';
        html += '<td>' + statusPill(r.response_status) + '</td>';
        html += '<td>' + esc(r.provider || '—') + ' / ' + esc(r.model_name || '—') + '</td>';
        html += '<td class="' + usableCls + '">' + usableIcon + '</td>';
        html += '</tr>';
      }
      elLedgerRows.innerHTML = html;
    }

    /* ── Events / Logs ────────────────────────────────────────── */
    function renderEvents(events, levelFilter, stageFilter) {
      if (!elEventList) return;
      var filtered = events;
      if (levelFilter) {
        filtered = filtered.filter(function (e) { return e.level === levelFilter; });
      }
      if (stageFilter) {
        filtered = filtered.filter(function (e) { return e.stage_key === stageFilter; });
      }

      if (filtered.length === 0) {
        elEventList.innerHTML = '<div class="pm-event-empty">No events match the current filters.</div>';
        return;
      }

      var html = '';
      for (var i = 0; i < filtered.length; i++) {
        var e = filtered[i];
        var lvlCls = 'pm-event-' + (e.level || 'info');
        html += '<div class="pm-event-row ' + lvlCls + '">';
        html += '<span class="pm-event-level">' + esc((e.level || 'info').toUpperCase()) + '</span>';
        html += '<span class="pm-event-type">' + esc(e.event_type || '') + '</span>';
        if (e.stage_key) html += '<span class="pm-event-stage">' + esc(STAGE_LABELS[e.stage_key] || e.stage_key) + '</span>';
        html += '<span class="pm-event-msg">' + esc(e.message || '') + '</span>';
        if (e.timestamp) html += '<span class="pm-event-time">' + esc(fmtTime(e.timestamp)) + '</span>';
        html += '</div>';
      }
      elEventList.innerHTML = html;
    }

    function populateStageFilter(stages) {
      if (!elEventStageFilter) return;
      var opts = '<option value="">All Stages</option>';
      for (var i = 0; i < stages.length; i++) {
        var key = stages[i].stage_key;
        opts += '<option value="' + esc(key) + '">' + esc(STAGE_LABELS[key] || key) + '</option>';
      }
      elEventStageFilter.innerHTML = opts;
    }

    /* ── Artifacts ────────────────────────────────────────────── */
    function renderArtifacts(artifacts) {
      if (!elArtifactGrid) return;
      setDisplay(elArtifactDetail, false);

      if (!artifacts || artifacts.length === 0) {
        elArtifactGrid.innerHTML = '<div class="pm-artifact-empty">No artifacts in this run.</div>';
        return;
      }

      // Group by stage
      var byStage = {};
      for (var i = 0; i < artifacts.length; i++) {
        var a = artifacts[i];
        var sk = a.stage_key || 'unknown';
        if (!byStage[sk]) byStage[sk] = [];
        byStage[sk].push(a);
      }

      var html = '';
      var stageKeys = Object.keys(byStage);
      for (var si = 0; si < stageKeys.length; si++) {
        var key = stageKeys[si];
        var group = byStage[key];
        html += '<div class="pm-artifact-group">';
        html += '<div class="pm-artifact-group-title">' + esc(STAGE_LABELS[key] || key) + ' (' + group.length + ')</div>';
        for (var j = 0; j < group.length; j++) {
          var art = group[j];
          html += '<div class="pm-artifact-chip" data-artifact-id="' + esc(art.artifact_id) + '" title="' + esc(art.artifact_type) + '">';
          html += '<span class="pm-artifact-chip-type">' + esc(art.artifact_type) + '</span>';
          if (art.artifact_key) html += '<span class="pm-artifact-chip-key">' + esc(art.artifact_key) + '</span>';
          if (art.candidate_id) html += '<span class="pm-artifact-chip-cid">cid:' + esc(shortId(art.candidate_id)) + '</span>';
          html += '</div>';
        }
        html += '</div>';
      }
      elArtifactGrid.innerHTML = html;
    }

    function loadArtifactDetail(artifactId) {
      if (!currentRunId) return;
      apiFetch('/api/pipeline/runs/' + encodeURIComponent(currentRunId) + '/artifacts/' + encodeURIComponent(artifactId))
        .then(function (data) {
          if (elArtifactDetailTitle) elArtifactDetailTitle.textContent = (data.artifact_type || '') + ' — ' + (data.artifact_key || artifactId);
          if (elArtifactJson) elArtifactJson.textContent = JSON.stringify(data, null, 2);
          setDisplay(elArtifactDetail, true);
        })
        .catch(function (err) {
          showError('Failed to load artifact: ' + err.message);
        });
    }

    /* ── Demo Run ─────────────────────────────────────────────── */
    function triggerDemoRun() {
      clearError();
      if (elDemoBtn) {
        elDemoBtn.disabled = true;
        elDemoBtn.textContent = 'Running…';
      }
      fetch('/api/pipeline/demo-run', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (elDemoBtn) {
            elDemoBtn.disabled = false;
            elDemoBtn.textContent = 'Run Demo Pipeline';
          }
          if (data.ok && data.run_id) {
            loadRunList();
          } else {
            showError('Demo run failed: ' + (data.message || 'Unknown error'));
          }
        })
        .catch(function (err) {
          if (elDemoBtn) {
            elDemoBtn.disabled = false;
            elDemoBtn.textContent = 'Run Demo Pipeline';
          }
          showError('Demo run failed: ' + err.message);
        });
    }

    /* ── Event Listeners ──────────────────────────────────────── */
    if (elRefreshBtn) elRefreshBtn.addEventListener('click', loadRunList);
    if (elDemoBtn) elDemoBtn.addEventListener('click', triggerDemoRun);
    if (elBackBtn) elBackBtn.addEventListener('click', showRunList);

    // Inspect button delegation
    if (elRunRows) {
      elRunRows.addEventListener('click', function (e) {
        var btn = e.target.closest('.pm-inspect-btn');
        if (btn) {
          var runId = btn.getAttribute('data-run-id');
          if (runId) loadRunDetail(runId);
        }
      });
    }

    // Artifact chip delegation
    if (elArtifactGrid) {
      elArtifactGrid.addEventListener('click', function (e) {
        var chip = e.target.closest('.pm-artifact-chip');
        if (chip) {
          var artId = chip.getAttribute('data-artifact-id');
          if (artId) loadArtifactDetail(artId);
        }
      });
    }

    // Artifact detail close
    if (elArtifactClose) {
      elArtifactClose.addEventListener('click', function () {
        setDisplay(elArtifactDetail, false);
      });
    }

    // Event filters
    function applyEventFilters() {
      if (!currentRunData) return;
      var lvl = elEventLevelFilter ? elEventLevelFilter.value : '';
      var stg = elEventStageFilter ? elEventStageFilter.value : '';
      renderEvents(currentRunData.events || [], lvl, stg);
    }
    if (elEventLevelFilter) elEventLevelFilter.addEventListener('change', applyEventFilters);
    if (elEventStageFilter) elEventStageFilter.addEventListener('change', applyEventFilters);

    /* ── Initial load ─────────────────────────────────────────── */
    loadRunList();
  };
})();
