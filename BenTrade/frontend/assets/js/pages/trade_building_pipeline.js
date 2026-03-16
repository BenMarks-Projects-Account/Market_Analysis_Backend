/**
 * Trade Building Pipeline — execution-focused DAG workflow dashboard.
 *
 * Renders the 13 canonical pipeline stages as a dependency-aware graph,
 * provides run controls (start / pause / resume / cancel), a clickable
 * stage-detail panel, and polling for live execution feel.
 *
 * Prefix: tbp-*   (Trade Building Pipeline)
 */
;(function () {
  'use strict';
  window.BenTradePages = window.BenTradePages || {};

  /* ── Stage visual metadata ─────────────────────────────────── */
  var STAGE_LABELS = {
    market_data:                  'Market Data',
    market_model_analysis:        'Model Analysis',
    stock_scanners:               'Stock Scanners',
    options_scanners:             'Options Scanners',
    candidate_selection:          'Selection',
    shared_context:               'Shared Context',
    candidate_enrichment:         'Enrichment',
    events:                       'Events',
    policy:                       'Policy',
    orchestration:                'Orchestration',
    prompt_payload:               'Prompt Payload',
    final_model_decision:         'Model Decision',
    final_response_normalization: 'Normalization',
  };

  var STATUS_ICONS = {
    completed: '✓', running: '⟳', failed: '✕',
    pending: '○', skipped: '⊘', cancelled: '⊗',
  };

  var STATUS_CLASSES = {
    completed: 'tbp-node-completed',
    running:   'tbp-node-running',
    failed:    'tbp-node-failed',
    pending:   'tbp-node-pending',
    skipped:   'tbp-node-skipped',
    cancelled: 'tbp-node-cancelled',
  };

  /* ── DAG layout — row/col placement for the 12 stages ──────
   *
   * The graph uses a 6-column × 9-row grid.  Each stage is placed
   * at a specific (row, col) coordinate.  Connectors are drawn as
   * SVG paths between node centres.
   *
   * Layout (cols 0-4, rows 0-9):
   *   Row 0:  market_data (col 0)  stock_scanners (col 2)  options_scanners (col 4)   ← Wave 0 (parallel)
   *   Row 1:  market_model_analysis (col 0)                candidate_selection (col 4)
   *   Row 2:  shared_context (col 0)
   *   Row 3:              candidate_enrichment (col 2, centre)
   *   Row 4:  policy (col 0)                               events (col 4)
   *   Row 5:              orchestration (col 2, centre)
   *   Row 6:              prompt_payload (col 2, centre)
   *   Row 7:              final_model_decision (col 2, centre)
   *   Row 8:              final_response_normalization (col 2, centre)
   */
  var GRAPH_LAYOUT = {
    market_data:                  { row: 0, col: 0 },
    stock_scanners:               { row: 0, col: 2 },
    options_scanners:             { row: 0, col: 4 },
    market_model_analysis:        { row: 1, col: 0 },
    candidate_selection:          { row: 1, col: 4 },
    shared_context:               { row: 2, col: 0 },
    candidate_enrichment:         { row: 3, col: 2 },
    policy:                       { row: 4, col: 0 },
    events:                       { row: 4, col: 4 },
    orchestration:                { row: 5, col: 2 },
    prompt_payload:               { row: 6, col: 2 },
    final_model_decision:         { row: 7, col: 2 },
    final_response_normalization: { row: 8, col: 2 },
  };

  var GRAPH_ROWS = 9;
  var GRAPH_COLS = 5; // 0..4

  var POLL_INTERVAL_MS = 2000;

  /* ── Helpers ────────────────────────────────────────────────── */
  function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
  function fmtMs(ms) { return ms != null ? (ms / 1000).toFixed(2) + 's' : '—'; }
  function fmtTime(iso) {
    if (!iso) return '—';
    try { var d = new Date(iso); return d.toLocaleTimeString(); } catch (_) { return String(iso); }
  }
  function shortId(id) {
    if (!id) return 'N/A';
    return id.length <= 16 ? id : id.slice(0, 8) + '…' + id.slice(-4);
  }

  /* ── Init entry point ──────────────────────────────────────── */
  window.BenTradePages.initTradeBuildingPipeline = function initTradeBuildingPipeline(rootEl) {
    var scope = rootEl || document.body;

    // DOM refs
    var elRunId       = scope.querySelector('#tbpRunId');
    var elRunStatus   = scope.querySelector('#tbpRunStatus');
    var elRunMeta     = scope.querySelector('#tbpRunMeta');
    var elBtnStart    = scope.querySelector('#tbpBtnStart');
    var elBtnPause    = scope.querySelector('#tbpBtnPause');
    var elBtnResume   = scope.querySelector('#tbpBtnResume');
    var elBtnCancel   = scope.querySelector('#tbpBtnCancel');
    var elRunPicker   = scope.querySelector('#tbpRunPicker');
    var elGraphArea   = scope.querySelector('#tbpGraphArea');
    var elGraphGrid   = scope.querySelector('#tbpGraphGrid');
    var elGraphLoad   = scope.querySelector('#tbpGraphLoading');
    var elSvg         = scope.querySelector('#tbpConnectorsSvg');
    var elDetailPanel = scope.querySelector('#tbpDetailPanel');
    var elDetailEmpty = scope.querySelector('#tbpDetailEmpty');
    var elDetailContent = scope.querySelector('#tbpDetailContent');
    var elDetailIcon  = scope.querySelector('#tbpDetailIcon');
    var elDetailName  = scope.querySelector('#tbpDetailName');
    var elDetailStatus= scope.querySelector('#tbpDetailStatus');
    var elDetailMetrics= scope.querySelector('#tbpDetailMetrics');
    var elDetailDeps  = scope.querySelector('#tbpDetailDeps');
    var elDetailCounts= scope.querySelector('#tbpDetailCounts');
    var elDetailCountsSection = scope.querySelector('#tbpDetailCountsSection');
    var elDetailError = scope.querySelector('#tbpDetailError');
    var elDetailErrorSection = scope.querySelector('#tbpDetailErrorSection');
    var elDetailArtifacts = scope.querySelector('#tbpDetailArtifacts');
    var elDetailArtifactsSection = scope.querySelector('#tbpDetailArtifactsSection');
    var elCandidateProgress = scope.querySelector('#tbpCandidateProgress');
    var elCandidateProgressSection = scope.querySelector('#tbpCandidateProgressSection');
    var elStatusText  = scope.querySelector('#tbpStatusText');

    var depMap = {};            // stage → [dependencies]
    var stageOrder = [];        // canonical order
    var stageLabelsMap = {};    // stage → human label
    var currentRunId = null;
    var currentRunData = null;
    var selectedStage = null;
    var pollTimer = null;
    var nodeEls = {};           // stage → DOM node element

    /* ── API helpers ─────────────────────────────────────────── */

    function apiFetch(url, opts) {
      return fetch(url, opts).then(function (r) {
        if (!r.ok) {
          // Try to extract backend error detail before rejecting
          return r.text().then(function (body) {
            var detail = '';
            try {
              var parsed = JSON.parse(body);
              detail = parsed.detail && parsed.detail.message
                ? parsed.detail.message
                : parsed.detail || parsed.message || '';
              if (typeof detail === 'object') detail = JSON.stringify(detail);
            } catch (_) { detail = body ? body.slice(0, 200) : ''; }
            var method = (opts && opts.method) || 'GET';
            throw new Error(method + ' ' + url + ' → ' + r.status + (detail ? ': ' + detail : ''));
          });
        }
        return r.json();
      });
    }

    function setStatus(msg) {
      if (elStatusText) elStatusText.textContent = msg;
    }

    /* ── Load dependency map (called once) ───────────────────── */
    function loadDependencyMap() {
      return apiFetch('/api/pipeline/dependency-map').then(function (data) {
        depMap = data.dependency_map || {};
        stageOrder = data.stage_order || [];
        stageLabelsMap = data.stage_labels || {};
        return data;
      });
    }

    /* ── Render the static graph skeleton ────────────────────── */
    function renderGraph() {
      if (elGraphLoad) elGraphLoad.style.display = 'none';
      if (!elGraphGrid) return;

      var html = '';
      for (var i = 0; i < stageOrder.length; i++) {
        var key = stageOrder[i];
        var pos = GRAPH_LAYOUT[key] || { row: i, col: 2 };
        var label = stageLabelsMap[key] || STAGE_LABELS[key] || key;
        html += '<div class="tbp-node tbp-node-pending" '
              + 'data-stage="' + esc(key) + '" '
              + 'style="grid-row:' + (pos.row + 1) + ';grid-column:' + (pos.col + 1) + '">'
              + '<div class="tbp-node-icon">' + (STATUS_ICONS.pending) + '</div>'
              + '<div class="tbp-node-label">' + esc(label) + '</div>'
              + '<div class="tbp-node-meta"></div>'
              + '</div>';
      }
      elGraphGrid.innerHTML = html;

      // Cache node elements
      nodeEls = {};
      var nodes = elGraphGrid.querySelectorAll('.tbp-node');
      for (var n = 0; n < nodes.length; n++) {
        nodeEls[nodes[n].getAttribute('data-stage')] = nodes[n];
      }

      // Click handler
      elGraphGrid.onclick = function (e) {
        var node = e.target.closest('.tbp-node');
        if (node) selectStage(node.getAttribute('data-stage'));
      };

      // Draw connectors after layout settles
      requestAnimationFrame(function () {
        requestAnimationFrame(drawConnectors);
      });
    }

    /* ── SVG connectors between dependency nodes ─────────────── */
    function drawConnectors() {
      if (!elSvg || !elGraphArea) return;
      var areaRect = elGraphArea.getBoundingClientRect();
      elSvg.setAttribute('width', areaRect.width);
      elSvg.setAttribute('height', areaRect.height);

      var paths = '';
      var keys = Object.keys(depMap);
      for (var i = 0; i < keys.length; i++) {
        var child = keys[i];
        var parents = depMap[child] || [];
        var childEl = nodeEls[child];
        if (!childEl) continue;
        var cRect = childEl.getBoundingClientRect();
        var cx = cRect.left + cRect.width / 2 - areaRect.left;
        var cy = cRect.top - areaRect.top;

        for (var j = 0; j < parents.length; j++) {
          var parentEl = nodeEls[parents[j]];
          if (!parentEl) continue;
          var pRect = parentEl.getBoundingClientRect();
          var px = pRect.left + pRect.width / 2 - areaRect.left;
          var py = pRect.top + pRect.height - areaRect.top;

          // Determine the status class for the connector
          var connCls = 'tbp-conn-pending';
          if (currentRunData) {
            var stages = currentRunData.stages || [];
            var parentStage = stages.find(function (s) { return s.stage_key === parents[j]; });
            if (parentStage && parentStage.status === 'completed') connCls = 'tbp-conn-completed';
            else if (parentStage && parentStage.status === 'failed') connCls = 'tbp-conn-failed';
            else if (parentStage && parentStage.status === 'running') connCls = 'tbp-conn-running';
          }

          // Bezier curve from parent bottom to child top
          var midY = (py + cy) / 2;
          paths += '<path class="tbp-connector ' + connCls + '" '
                 + 'd="M' + px + ',' + py + ' C' + px + ',' + midY + ' ' + cx + ',' + midY + ' ' + cx + ',' + cy + '" '
                 + 'fill="none" />';
        }
      }
      elSvg.innerHTML = paths;
    }

    /* ── Update node states from run data ─────────────────────── */
    function updateNodes(runData) {
      var stages = runData ? runData.stages || [] : [];
      var stageMap = {};
      for (var i = 0; i < stages.length; i++) {
        stageMap[stages[i].stage_key] = stages[i];
      }

      for (var key in nodeEls) {
        var el = nodeEls[key];
        var s = stageMap[key];
        var status = s ? s.status : 'pending';
        var icon = STATUS_ICONS[status] || '○';
        var cls = STATUS_CLASSES[status] || 'tbp-node-pending';

        // Replace all status classes
        el.className = 'tbp-node ' + cls;
        if (key === selectedStage) el.classList.add('tbp-node-selected');

        el.querySelector('.tbp-node-icon').textContent = icon;
        var meta = el.querySelector('.tbp-node-meta');
        if (key === 'final_model_decision' && status === 'running' && runData && runData.candidate_progress) {
          var cp = runData.candidate_progress;
          meta.textContent = (cp.completed_count || 0) + '/' + (((cp.completed_count || 0) + (cp.remaining_count || 0))) + ' candidates';
        } else if (s && s.duration_ms != null) {
          meta.textContent = fmtMs(s.duration_ms);
        } else if (status === 'running') {
          meta.textContent = '…';
        } else {
          meta.textContent = '';
        }
      }

      drawConnectors();
    }

    /* ── Run header update ───────────────────────────────────── */
    function updateRunHeader(runData) {
      if (!runData) {
        if (elRunId) elRunId.textContent = 'No run selected';
        if (elRunStatus) { elRunStatus.textContent = ''; elRunStatus.className = 'tbp-run-status'; }
        if (elRunMeta) elRunMeta.innerHTML = '';
        return;
      }
      if (elRunId) elRunId.textContent = shortId(runData.run_id);
      if (elRunId) elRunId.title = runData.run_id || '';
      if (elRunStatus) {
        elRunStatus.textContent = runData.status || 'unknown';
        elRunStatus.className = 'tbp-run-status tbp-status-' + (runData.status || 'unknown');
      }
      if (elRunMeta) {
        var stages = runData.stages || [];
        var completed = stages.filter(function (s) { return s.status === 'completed'; }).length;
        var failed = stages.filter(function (s) { return s.status === 'failed'; }).length;
        elRunMeta.innerHTML =
          '<span class="tbp-meta-item" title="Duration">' + esc(fmtMs(runData.duration_ms)) + '</span>'
          + '<span class="tbp-meta-item" title="Started">' + esc(fmtTime(runData.started_at)) + '</span>'
          + '<span class="tbp-meta-item" title="Stages completed">' + completed + '/' + stages.length + ' stages</span>'
          + (failed > 0 ? '<span class="tbp-meta-item tbp-meta-error">' + failed + ' failed</span>' : '');
      }
    }

    /* ── Stage detail panel ──────────────────────────────────── */
    function selectStage(stageKey) {
      selectedStage = stageKey;
      // Highlight node
      for (var k in nodeEls) {
        nodeEls[k].classList.toggle('tbp-node-selected', k === stageKey);
      }

      if (!stageKey) { showDetailEmpty(); return; }

      // Find run-time stage data if a run is loaded
      var stage = null;
      if (currentRunData) {
        var stages = currentRunData.stages || [];
        for (var i = 0; i < stages.length; i++) {
          if (stages[i].stage_key === stageKey) { stage = stages[i]; break; }
        }
      }

      // Always show the panel — structural info is available even without a run
      if (elDetailEmpty) elDetailEmpty.style.display = 'none';
      if (elDetailContent) elDetailContent.style.display = '';

      var label = stageLabelsMap[stageKey] || STAGE_LABELS[stageKey] || stageKey;
      var status = stage ? stage.status : 'idle';
      var statusIcon = STATUS_ICONS[status] || '◇';
      var statusCls = STATUS_CLASSES[status] || '';

      if (elDetailIcon) { elDetailIcon.textContent = statusIcon; elDetailIcon.className = 'tbp-detail-stage-icon ' + statusCls; }
      if (elDetailName) elDetailName.textContent = label;
      if (elDetailStatus) {
        elDetailStatus.textContent = stage ? status : 'idle';
        elDetailStatus.className = 'tbp-detail-stage-status' + (stage ? ' tbp-status-' + status : '');
      }

      // Metrics — show runtime data if available, otherwise show placeholders
      if (elDetailMetrics) {
        elDetailMetrics.innerHTML =
          '<div class="tbp-detail-metric"><span class="tbp-dm-label">Duration</span><span class="tbp-dm-value">' + esc(stage ? fmtMs(stage.duration_ms) : '—') + '</span></div>'
          + '<div class="tbp-detail-metric"><span class="tbp-dm-label">Artifacts</span><span class="tbp-dm-value">' + (stage ? (stage.artifact_count || 0) : '—') + '</span></div>'
          + '<div class="tbp-detail-metric"><span class="tbp-dm-label">Log Events</span><span class="tbp-dm-value">' + (stage ? (stage.log_event_count || 0) : '—') + '</span></div>';
      }

      // Dependencies — always available from structural data
      var deps = depMap[stageKey] || [];
      if (elDetailDeps) {
        if (deps.length === 0) {
          elDetailDeps.innerHTML = '<span class="tbp-dep-tag tbp-dep-root">Root (no dependencies)</span>';
        } else {
          elDetailDeps.innerHTML = deps.map(function (d) {
            return '<span class="tbp-dep-tag">' + esc(stageLabelsMap[d] || STAGE_LABELS[d] || d) + '</span>';
          }).join('');
        }
      }

      // Summary counts (run-time only)
      var sc = stage ? (stage.summary_counts || {}) : {};
      var scKeys = Object.keys(sc);
      if (elDetailCountsSection) elDetailCountsSection.style.display = scKeys.length > 0 ? '' : 'none';
      if (elDetailCounts && scKeys.length > 0) {
        elDetailCounts.innerHTML = scKeys.map(function (k) {
          return '<div class="tbp-count-row"><span>' + esc(k) + '</span><span>' + esc(String(sc[k])) + '</span></div>';
        }).join('');
      }

      // Error (run-time only)
      var hasError = stage && stage.error;
      if (elDetailErrorSection) elDetailErrorSection.style.display = hasError ? '' : 'none';
      if (elDetailError && hasError) {
        var errMsg = stage.error.message || stage.error.code || JSON.stringify(stage.error);
        elDetailError.textContent = errMsg;
      }

      // Candidate progress (Step 14 only)
      renderCandidateProgress(stageKey, stage);

      // Artifacts (run-time only)
      var arts = stage ? (stage.artifact_refs || []) : [];
      if (elDetailArtifactsSection) elDetailArtifactsSection.style.display = arts.length > 0 ? '' : 'none';
      if (elDetailArtifacts && arts.length > 0) {
        elDetailArtifacts.innerHTML = arts.map(function (aid) {
          return '<div class="tbp-artifact-ref" data-aid="' + esc(aid) + '">' + esc(shortId(aid)) + '</div>';
        }).join('');
      }
    }

    /* ── Candidate progress rendering (Step 14) ────────────── */
    function renderCandidateProgress(stageKey, stage) {
      if (!elCandidateProgressSection) return;
      // Only show for final_model_decision
      if (stageKey !== 'final_model_decision') {
        elCandidateProgressSection.style.display = 'none';
        return;
      }

      var cp = currentRunData ? currentRunData.candidate_progress : null;
      var stageStatus = stage ? stage.status : 'idle';

      // Derive per-candidate history from events
      var candidateEvents = deriveCandidateEvents();

      // Show section if stage is running with progress, or completed with event history
      if (!cp && candidateEvents.length === 0) {
        elCandidateProgressSection.style.display = 'none';
        return;
      }

      elCandidateProgressSection.style.display = '';
      var html = '';

      // Live progress header (when running)
      if (cp && stageStatus === 'running') {
        var total = (cp.completed_count || 0) + (cp.remaining_count || 0);
        var pct = total > 0 ? Math.round(((cp.completed_count || 0) / total) * 100) : 0;
        html += '<div class="tbp-cp-summary">'
              + '<div class="tbp-cp-bar-track"><div class="tbp-cp-bar-fill" style="width:' + pct + '%"></div></div>'
              + '<div class="tbp-cp-stats">'
              + '<span>' + (cp.completed_count || 0) + ' / ' + total + ' candidates</span>'
              + '<span>' + (cp.remaining_count || 0) + ' remaining</span>'
              + '</div>'
              + '</div>';
        if (cp.candidate_id) {
          var statusCls = cp.candidate_status === 'completed' ? 'tbp-cp-ok'
            : cp.candidate_status === 'failed' ? 'tbp-cp-fail' : 'tbp-cp-deg';
          html += '<div class="tbp-cp-current">'
                + '<span class="tbp-cp-current-label">Last processed:</span> '
                + '<span class="tbp-cp-sym">' + esc(cp.symbol || cp.candidate_id) + '</span> '
                + '<span class="tbp-cp-badge ' + statusCls + '">' + esc(cp.candidate_status || '') + '</span>'
                + (cp.elapsed_ms != null ? ' <span class="tbp-cp-time">' + fmtMs(cp.elapsed_ms) + '</span>' : '')
                + '</div>';
        }
      } else if (stageStatus === 'completed' || stageStatus === 'failed') {
        // Completed — show final tally from summary_counts
        var sc = stage ? (stage.summary_counts || {}) : {};
        var total = (sc.total_completed || 0) + (sc.total_failed || 0) + (sc.total_skipped || 0);
        if (total > 0) {
          html += '<div class="tbp-cp-summary tbp-cp-done">'
                + '<div class="tbp-cp-stats">'
                + '<span>Completed: ' + (sc.total_completed || 0) + '</span>'
                + (sc.total_failed ? '<span class="tbp-cp-fail">Failed: ' + sc.total_failed + '</span>' : '')
                + (sc.total_skipped ? '<span>Skipped: ' + sc.total_skipped + '</span>' : '')
                + '</div></div>';
        }
      }

      // Per-candidate event log
      if (candidateEvents.length > 0) {
        html += '<div class="tbp-cp-log">';
        for (var i = 0; i < candidateEvents.length; i++) {
          var ce = candidateEvents[i];
          var rowCls = ce.status === 'completed' ? 'tbp-cp-ok'
            : ce.status === 'failed' ? 'tbp-cp-fail'
            : ce.status === 'degraded' ? 'tbp-cp-deg'
            : ce.status === 'started' ? 'tbp-cp-active' : '';
          html += '<div class="tbp-cp-log-row ' + rowCls + '">'
                + '<span class="tbp-cp-pos">' + ce.position + '</span>'
                + '<span class="tbp-cp-sym">' + esc(ce.symbol || ce.candidate_id) + '</span>'
                + '<span class="tbp-cp-badge ' + rowCls + '">' + esc(ce.status) + '</span>';
          if (ce.elapsed_ms != null) {
            html += '<span class="tbp-cp-time">' + fmtMs(ce.elapsed_ms) + '</span>';
          }
          html += '</div>';
        }
        html += '</div>';
      }

      if (elCandidateProgress) elCandidateProgress.innerHTML = html;
    }

    function deriveCandidateEvents() {
      if (!currentRunData || !currentRunData.events) return [];
      var events = currentRunData.events;
      var started = {};   // candidate_id → event
      var completed = {};  // candidate_id → event
      var order = [];      // ordered candidate_ids

      for (var i = 0; i < events.length; i++) {
        var e = events[i];
        var et = e.event_type || '';
        var meta = e.metadata || {};
        var cid = meta.candidate_id;
        if (!cid) continue;
        if (et === 'candidate_execution_started') {
          started[cid] = meta;
          if (order.indexOf(cid) === -1) order.push(cid);
        } else if (et === 'candidate_execution_completed') {
          completed[cid] = meta;
          if (order.indexOf(cid) === -1) order.push(cid);
        }
      }

      var result = [];
      for (var j = 0; j < order.length; j++) {
        var id = order[j];
        var c = completed[id];
        var s = started[id];
        result.push({
          candidate_id: id,
          symbol: (c && c.symbol) || (s && s.symbol) || id,
          position: (c && c.queue_position) || (s && s.queue_position) || (j + 1),
          status: c ? (c.candidate_status || 'completed') : 'started',
          elapsed_ms: c ? c.elapsed_ms : null,
        });
      }
      return result;
    }

    function showDetailEmpty() {
      if (elDetailEmpty) elDetailEmpty.style.display = '';
      if (elDetailContent) elDetailContent.style.display = 'none';
    }

    /* ── Load run detail ─────────────────────────────────────── */
    function loadRun(runId) {
      if (!runId) return;
      currentRunId = runId;
      setStatus('Loading run ' + shortId(runId) + '…');

      apiFetch('/api/pipeline/runs/' + encodeURIComponent(runId))
        .then(function (data) {
          currentRunData = data;
          updateRunHeader(data);
          updateNodes(data);
          if (selectedStage) selectStage(selectedStage);
          setStatus('Loaded run ' + shortId(runId));
        })
        .catch(function (err) {
          setStatus('Error loading run: ' + err.message);
        });
    }

    /* ── Load run list for picker ────────────────────────────── */
    function loadRunList() {
      apiFetch('/api/pipeline/runs').then(function (data) {
        var runs = data.runs || [];
        if (!elRunPicker) return;
        var html = '<option value="">— Recent Runs (' + runs.length + ') —</option>';
        for (var i = 0; i < runs.length; i++) {
          var r = runs[i];
          var sel = r.run_id === currentRunId ? ' selected' : '';
          var statusBadge = r.status === 'completed' ? '✓' : r.status === 'failed' ? '✕' : '○';
          html += '<option value="' + esc(r.run_id) + '"' + sel + '>'
                + statusBadge + ' ' + esc(shortId(r.run_id)) + ' — ' + esc(fmtTime(r.started_at))
                + '</option>';
        }
        elRunPicker.innerHTML = html;
      }).catch(function () { /* silent */ });
    }

    /* ── Run controls ────────────────────────────────────────── */
    function doStartRun() {
      setStatus('Starting pipeline run…');
      elBtnStart.disabled = true;

      apiFetch('/api/pipeline/runs/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trigger_source: 'trade-building-pipeline', scope: { mode: 'full' } }),
      })
        .then(function (data) {
          if (data.ok && data.run_id) {
            setStatus('Run started: ' + shortId(data.run_id));
            currentRunId = data.run_id;
            loadRun(data.run_id);
            loadRunList();
            startPolling();
          } else if (!data.ok && data.run_id) {
            // A run is already in progress — poll it instead.
            setStatus('Run already in progress: ' + shortId(data.run_id));
            currentRunId = data.run_id;
            loadRun(data.run_id);
            startPolling();
            elBtnStart.disabled = false;
          } else {
            setStatus('Run response: ' + (data.status || 'unknown'));
            if (data.run_id) { loadRun(data.run_id); loadRunList(); }
            elBtnStart.disabled = false;
          }
        })
        .catch(function (err) {
          elBtnStart.disabled = false;
          setStatus('Start failed: ' + err.message);
        });
    }

    function doStubControl(action) {
      if (!currentRunId) return;
      apiFetch('/api/pipeline/runs/' + encodeURIComponent(currentRunId) + '/' + action, { method: 'POST' })
        .then(function (data) {
          if (!data.implemented) {
            setStatus(action.charAt(0).toUpperCase() + action.slice(1) + ': ' + (data.message || 'not implemented'));
          }
        })
        .catch(function (err) {
          setStatus(action + ' error: ' + err.message);
        });
    }

    /* ── Polling for live updates ────────────────────────────── */
    var pollInFlight = false;

    function startPolling() {
      stopPolling();
      pollInFlight = false;
      pollTimer = setInterval(function () {
        if (!currentRunId || pollInFlight) return;
        pollInFlight = true;
        apiFetch('/api/pipeline/runs/' + encodeURIComponent(currentRunId))
          .then(function (data) {
            currentRunData = data;
            updateRunHeader(data);
            updateNodes(data);
            if (selectedStage) selectStage(selectedStage);
            // Stop polling if terminal state
            if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
              stopPolling();
              elBtnStart.disabled = false;
              loadRunList();
              setStatus('Run ' + shortId(currentRunId) + ' ' + data.status);
            }
          })
          .catch(function () { /* silent */ })
          .then(function () { pollInFlight = false; });
      }, POLL_INTERVAL_MS);
    }

    function stopPolling() {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      pollInFlight = false;
    }

    /* ── Wire events ─────────────────────────────────────────── */
    if (elBtnStart) elBtnStart.onclick = doStartRun;
    if (elBtnPause) elBtnPause.onclick = function () { doStubControl('pause'); };
    if (elBtnResume) elBtnResume.onclick = function () { doStubControl('resume'); };
    if (elBtnCancel) elBtnCancel.onclick = function () { doStubControl('cancel'); };
    if (elRunPicker) elRunPicker.onchange = function () {
      var v = elRunPicker.value;
      if (v) loadRun(v);
    };

    // Redraw connectors on window resize
    var resizeTimer;
    window.addEventListener('resize', function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(drawConnectors, 150);
    });

    /* ── Bootstrap ───────────────────────────────────────────── */
    // Step 1: Load dependency map and render structural graph.
    // This must succeed for the page to be usable.
    loadDependencyMap()
      .then(function () {
        renderGraph();
        setStatus('Graph loaded — ' + stageOrder.length + ' stages');
      })
      .catch(function (err) {
        setStatus('Graph load error: ' + err.message);
        if (elGraphLoad) elGraphLoad.textContent = 'Failed to load dependency map: ' + err.message;
      })
      .then(function () {
        // Step 2: Independently load run list and auto-select latest run.
        // Graph is already rendered; run loading failures don't break the graph.
        return loadRunList();
      })
      .then(function () {
        return apiFetch('/api/pipeline/runs');
      })
      .then(function (data) {
        var runs = data.runs || [];
        if (runs.length > 0) {
          loadRun(runs[0].run_id);
        } else {
          setStatus('Ready — no previous runs. Click Start Run to begin.');
        }
      })
      .catch(function (err) {
        // Run list failed — graph is still functional
        setStatus('Run list unavailable: ' + err.message);
      });

    // Cleanup when navigating away
    window.BenTradeActiveViewCleanup = function () {
      stopPolling();
    };
  };
})();
