/**
 * Active Trade Pipeline — execution-focused pipeline monitor dashboard.
 *
 * Mirrors the Trade Building Pipeline UI pattern: stage graph area,
 * right-side detail panel, run header controls, status bar.
 * Only the stage names, stage semantics and result content differ.
 *
 * Prefix: atp-*   (Active Trade Pipeline)
 */
;(function () {
  'use strict';
  window.BenTradePages = window.BenTradePages || {};

  /* ── Stage visual metadata ─────────────────────────────────── */
  var STAGE_ORDER = [
    'load_positions',
    'market_context',
    'build_packets',
    'engine_analysis',
    'model_analysis',
    'normalize',
    'complete'
  ];

  var STAGE_LABELS = {
    load_positions:  'Load Positions',
    market_context:  'Market Context',
    build_packets:   'Build Packets',
    engine_analysis: 'Engine Analysis',
    model_analysis:  'Model Analysis',
    normalize:       'Normalize',
    complete:        'Complete'
  };

  /* Graph layout — single column pipeline (col 2, rows 0-6) */
  var GRAPH_LAYOUT = {
    load_positions:  { row: 0, col: 2 },
    market_context:  { row: 1, col: 2 },
    build_packets:   { row: 2, col: 2 },
    engine_analysis: { row: 3, col: 2 },
    model_analysis:  { row: 4, col: 2 },
    normalize:       { row: 5, col: 2 },
    complete:        { row: 6, col: 2 }
  };

  var STATUS_ICONS = {
    completed: '✓', running: '⟳', failed: '✕',
    pending: '○', skipped: '⊘', idle: '◇'
  };

  var STATUS_CLASSES = {
    completed: 'tbp-node-completed',
    running:   'tbp-node-running',
    failed:    'tbp-node-failed',
    pending:   'tbp-node-pending',
    skipped:   'tbp-node-skipped',
    idle:      'tbp-node-pending'
  };

  /* ── Helpers ────────────────────────────────────────────────── */
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
  function fmtMs(ms) { return ms != null ? (ms / 1000).toFixed(2) + 's' : '—'; }
  function fmtTime(iso) {
    if (!iso) return '—';
    try { return new Date(iso).toLocaleTimeString(); } catch (_) { return String(iso); }
  }
  function shortId(id) {
    if (!id) return 'N/A';
    return id.length <= 16 ? id : id.slice(0, 8) + '…' + id.slice(-4);
  }
  function fmtPct(v) { return v != null ? (v * 100).toFixed(1) + '%' : '—'; }

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
      case 5: return 'CRITICAL'; case 4: return 'HIGH'; case 3: return 'MODERATE';
      case 2: return 'LOW'; default: return 'NONE';
    }
  }
  function urgencyClass(urgency) {
    if (urgency >= 4) return 'tmc-urgency-high';
    if (urgency >= 3) return 'tmc-urgency-moderate';
    return 'tmc-urgency-low';
  }

  /* ── Init entry point ──────────────────────────────────────── */
  window.BenTradePages.initActiveTradesPipeline = function initActiveTradesPipeline(rootEl) {
    var scope = rootEl || document.body;

    // DOM refs — use same TBP class names since we share the shell
    var elRunId       = scope.querySelector('#atpRunId');
    var elRunStatus   = scope.querySelector('#atpRunStatus');
    var elRunMeta     = scope.querySelector('#atpRunMeta');
    var elBtnStart    = scope.querySelector('#atpBtnStart');
    var elRunPicker   = scope.querySelector('#atpRunPicker');
    var elGraphArea   = scope.querySelector('#atpGraphArea');
    var elGraphGrid   = scope.querySelector('#atpGraphGrid');
    var elSvg         = scope.querySelector('#atpConnectorsSvg');
    var elDetailPanel = scope.querySelector('#atpDetailPanel');
    var elDetailEmpty = scope.querySelector('#atpDetailEmpty');
    var elDetailContent = scope.querySelector('#atpDetailContent');
    var elStageDetail = scope.querySelector('#atpStageDetail');
    var elRecDetail   = scope.querySelector('#atpRecDetail');
    var elDetailIcon  = scope.querySelector('#atpDetailIcon');
    var elDetailName  = scope.querySelector('#atpDetailName');
    var elDetailStatus= scope.querySelector('#atpDetailStatus');
    var elDetailMetrics = scope.querySelector('#atpDetailMetrics');
    var elResultsSection = scope.querySelector('#atpResultsSection');
    var elResultsGrid = scope.querySelector('#atpResultsGrid');
    var elResultsTitle = scope.querySelector('#atpResultsTitle');
    var elFilterRec   = scope.querySelector('#atpFilterRec');
    var elBtnTmc      = scope.querySelector('#atpBtnTmc');
    var elStatusText  = scope.querySelector('#atpStatusText');

    var currentRunId = null;
    var currentRunData = null;
    var selectedStage = null;
    var nodeEls = {};
    var _running = false;
    var _allRecs = [];

    /* ── API helpers (matches TBP pattern) ───────────────────── */
    function apiFetch(url, opts) {
      return fetch(url, opts).then(function (r) {
        if (!r.ok) {
          return r.text().then(function (body) {
            var detail = '';
            try {
              var parsed = JSON.parse(body);
              detail = (parsed.detail && parsed.detail.message) || parsed.detail || parsed.message || '';
              if (typeof detail === 'object') detail = JSON.stringify(detail);
            } catch (_) { detail = body ? body.slice(0, 200) : ''; }
            throw new Error((opts && opts.method || 'GET') + ' ' + url + ' → ' + r.status + (detail ? ': ' + detail : ''));
          });
        }
        return r.json();
      });
    }

    function setStatus(msg) {
      if (elStatusText) elStatusText.textContent = msg;
    }

    /* ── Render the static graph skeleton (matches TBP renderGraph) ── */
    function renderGraph() {
      if (!elGraphGrid) return;

      var html = '';
      for (var i = 0; i < STAGE_ORDER.length; i++) {
        var key = STAGE_ORDER[i];
        var pos = GRAPH_LAYOUT[key];
        var label = STAGE_LABELS[key] || key;
        html += '<div class="tbp-node tbp-node-pending" '
              + 'data-stage="' + esc(key) + '" '
              + 'style="grid-row:' + (pos.row + 1) + ';grid-column:' + (pos.col + 1) + '">'
              + '<div class="tbp-node-icon">' + STATUS_ICONS.pending + '</div>'
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

      requestAnimationFrame(function () {
        requestAnimationFrame(drawConnectors);
      });
    }

    /* ── SVG connectors (linear pipeline — straight lines between consecutive nodes) ── */
    function drawConnectors() {
      if (!elSvg || !elGraphArea) return;
      var areaRect = elGraphArea.getBoundingClientRect();
      elSvg.setAttribute('width', areaRect.width);
      elSvg.setAttribute('height', areaRect.height);

      var paths = '';
      for (var i = 0; i < STAGE_ORDER.length - 1; i++) {
        var parentEl = nodeEls[STAGE_ORDER[i]];
        var childEl = nodeEls[STAGE_ORDER[i + 1]];
        if (!parentEl || !childEl) continue;

        var pRect = parentEl.getBoundingClientRect();
        var cRect = childEl.getBoundingClientRect();
        var px = pRect.left + pRect.width / 2 - areaRect.left;
        var py = pRect.top + pRect.height - areaRect.top;
        var cx = cRect.left + cRect.width / 2 - areaRect.left;
        var cy = cRect.top - areaRect.top;

        var connCls = 'tbp-conn-pending';
        var stageStatus = getStageStatus(STAGE_ORDER[i]);
        if (stageStatus === 'completed') connCls = 'tbp-conn-completed';
        else if (stageStatus === 'running') connCls = 'tbp-conn-running';
        else if (stageStatus === 'failed') connCls = 'tbp-conn-failed';

        var midY = (py + cy) / 2;
        paths += '<path class="tbp-connector ' + connCls + '" '
               + 'd="M' + px + ',' + py + ' C' + px + ',' + midY + ' ' + cx + ',' + midY + ' ' + cx + ',' + cy + '" '
               + 'fill="none" />';
      }
      elSvg.innerHTML = paths;
    }

    /* ── Derive per-stage status from run data ───────────────── */
    function getStageStatus(stageKey) {
      if (!currentRunData) return 'pending';

      // Use real per-stage data when available
      var stages = currentRunData.stages;
      if (stages && stages[stageKey]) {
        var s = (stages[stageKey].status || '').toLowerCase();
        if (s === 'completed' || s === 'running' || s === 'failed' || s === 'skipped') return s;
      }

      // Fallback for legacy results without stages dict
      var status = (currentRunData.status || '').toLowerCase();
      if (status === 'completed' || status === 'success') {
        if (stageKey === 'model_analysis' && isEngineOnly()) return 'skipped';
        return 'completed';
      }
      if (status === 'failed') return stageKey === STAGE_ORDER[0] ? 'failed' : 'pending';
      if (status === 'running') return 'running';
      return 'pending';
    }

    function isEngineOnly() {
      if (!currentRunData || !currentRunData.recommendations) return false;
      var recs = currentRunData.recommendations;
      return recs.length > 0 && recs[0].recommendation_source === 'engine_only';
    }

    /* ── Update node states from run data (matches TBP updateNodes) ── */
    function updateNodes() {
      var stages = (currentRunData && currentRunData.stages) || {};
      for (var i = 0; i < STAGE_ORDER.length; i++) {
        var key = STAGE_ORDER[i];
        var el = nodeEls[key];
        if (!el) continue;

        var status = getStageStatus(key);
        var icon = STATUS_ICONS[status] || '○';
        var cls = STATUS_CLASSES[status] || 'tbp-node-pending';

        el.className = 'tbp-node ' + cls;
        if (key === selectedStage) el.classList.add('tbp-node-selected');

        el.querySelector('.tbp-node-icon').textContent = icon;

        // Show per-stage duration or skip reason in meta
        var metaEl = el.querySelector('.tbp-node-meta');
        var stageData = stages[key];
        if (stageData && status === 'skipped') {
          metaEl.textContent = stageData.reason || 'skipped';
        } else if (stageData && stageData.duration_ms != null) {
          metaEl.textContent = fmtMs(stageData.duration_ms);
        } else {
          metaEl.textContent = '';
        }
      }
      drawConnectors();
    }

    /* ── Run header update (matches TBP updateRunHeader) ─────── */
    function updateRunHeader(data) {
      if (!data) {
        if (elRunId) elRunId.textContent = 'No run selected';
        if (elRunStatus) { elRunStatus.textContent = ''; elRunStatus.className = 'tbp-run-status'; }
        if (elRunMeta) elRunMeta.innerHTML = '';
        return;
      }
      if (elRunId) { elRunId.textContent = shortId(data.run_id); elRunId.title = data.run_id || ''; }
      if (elRunStatus) {
        var st = (data.status || 'unknown').toLowerCase();
        elRunStatus.textContent = st.toUpperCase();
        elRunStatus.className = 'tbp-run-status tbp-status-' + st;
      }
      if (elRunMeta) {
        var recs = data.recommendations || [];
        var rc = data.recommendation_counts || {};
        elRunMeta.innerHTML =
          '<span class="tbp-meta-item" title="Duration">' + esc(fmtMs(data.duration_ms)) + '</span>'
          + '<span class="tbp-meta-item" title="Started">' + esc(fmtTime(data.started_at)) + '</span>'
          + '<span class="tbp-meta-item" title="Trades reviewed">' + (data.trade_count || recs.length) + ' trades</span>'
          + (rc.URGENT_REVIEW ? '<span class="tbp-meta-item tbp-meta-error">' + rc.URGENT_REVIEW + ' urgent</span>' : '');
      }
    }

    /* ── Stage detail panel (matches TBP selectStage) ────────── */
    function selectStage(stageKey) {
      selectedStage = stageKey;
      for (var k in nodeEls) {
        nodeEls[k].classList.toggle('tbp-node-selected', k === stageKey);
      }

      if (!stageKey) { showDetailEmpty(); return; }

      if (elDetailEmpty) elDetailEmpty.style.display = 'none';
      if (elDetailContent) elDetailContent.style.display = '';
      if (elStageDetail) elStageDetail.style.display = '';
      if (elRecDetail) elRecDetail.style.display = 'none';

      var label = STAGE_LABELS[stageKey] || stageKey;
      var status = getStageStatus(stageKey);
      var statusIcon = STATUS_ICONS[status] || '◇';
      var statusCls = STATUS_CLASSES[status] || '';

      if (elDetailIcon) { elDetailIcon.textContent = statusIcon; elDetailIcon.className = 'tbp-detail-stage-icon ' + statusCls; }
      if (elDetailName) elDetailName.textContent = label;
      if (elDetailStatus) {
        elDetailStatus.textContent = status;
        elDetailStatus.className = 'tbp-detail-stage-status tbp-status-' + status;
      }

      // Metrics depends on stage/run data — use real per-stage data
      if (elDetailMetrics) {
        var html = '<div class="tbp-detail-metric"><span class="tbp-dm-label">Status</span><span class="tbp-dm-value">' + esc(status) + '</span></div>';

        var stageData = (currentRunData && currentRunData.stages) ? currentRunData.stages[stageKey] : null;
        if (stageData) {
          html += '<div class="tbp-detail-metric"><span class="tbp-dm-label">Duration</span><span class="tbp-dm-value">' + esc(fmtMs(stageData.duration_ms)) + '</span></div>';
          if (stageData.started_at) {
            html += '<div class="tbp-detail-metric"><span class="tbp-dm-label">Started</span><span class="tbp-dm-value">' + esc(fmtTime(stageData.started_at)) + '</span></div>';
          }
          if (stageData.reason) {
            html += '<div class="tbp-detail-metric"><span class="tbp-dm-label">Reason</span><span class="tbp-dm-value">' + esc(stageData.reason) + '</span></div>';
          }

          // Show stage-specific metadata
          var meta = stageData.metadata || {};
          var metaKeys = Object.keys(meta);
          if (metaKeys.length > 0) {
            html += '<div style="grid-column:1/-1;margin-top:8px;border-top:1px solid rgba(0,234,255,0.08);padding-top:6px">';
            metaKeys.forEach(function (k) {
              var val = meta[k];
              var displayVal = (val != null && typeof val === 'object') ? JSON.stringify(val) : String(val != null ? val : '—');
              html += '<div class="tbp-count-row"><span>' + esc(k.replace(/_/g, ' ')) + '</span><span>' + esc(displayVal) + '</span></div>';
            });
            html += '</div>';
          }
        } else if (currentRunData) {
          html += '<div class="tbp-detail-metric"><span class="tbp-dm-label">Duration</span><span class="tbp-dm-value">' + esc(fmtMs(currentRunData.duration_ms)) + '</span></div>';
          html += '<div class="tbp-detail-metric"><span class="tbp-dm-label">Trades</span><span class="tbp-dm-value">' + (currentRunData.trade_count || 0) + '</span></div>';
        }

        // Show summary counts for the 'complete' stage
        if (stageKey === 'complete' && currentRunData) {
          var rc = currentRunData.recommendation_counts || {};
          var rcKeys = Object.keys(rc);
          if (rcKeys.length > 0) {
            html += '<div style="grid-column:1/-1;margin-top:8px">';
            rcKeys.forEach(function (k) {
              html += '<div class="tbp-count-row"><span>' + esc(k) + '</span><span>' + esc(String(rc[k])) + '</span></div>';
            });
            html += '</div>';
          }
        }
        elDetailMetrics.innerHTML = html;
      }
    }

    function showDetailEmpty() {
      if (elDetailEmpty) elDetailEmpty.style.display = '';
      if (elDetailContent) elDetailContent.style.display = 'none';
    }

    /* ── Account mode from TMC toggle ─────────────────────────── */
    function getAccountMode() {
      var activeBtn = document.querySelector('#tmcAccountToggle .active-account-btn.is-active');
      return (activeBtn && activeBtn.getAttribute('data-mode')) || 'paper';
    }

    /* ── Close order preview → confirm → submit flow ──────────── */
    function handleClosePreview(rec) {
      var co = rec.suggested_close_order;
      if (!co || !co.ready_for_preview) return;

      var mode = getAccountMode();
      var desc = co.description || (rec.symbol + ' close');
      var costLine = '';
      if (co.estimated_cost != null) {
        costLine = '\nEstimated cost: $' + Math.abs(co.estimated_cost).toFixed(2) + ' (' + (co.price_effect || '') + ')';
      } else if (co.estimated_proceeds != null) {
        costLine = '\nEstimated proceeds: $' + co.estimated_proceeds.toFixed(2);
      }

      if (!confirm('Preview close order?\n\n' + desc + costLine + '\nAccount: ' + mode.toUpperCase())) {
        return;
      }

      var previewPayload = {
        order_type: co.order_type,
        symbol: co.symbol,
        limit_price: co.limit_price,
        price_effect: co.price_effect,
        time_in_force: co.time_in_force || 'DAY',
        mode: mode,
      };
      if (co.order_type === 'multileg' && co.legs) {
        previewPayload.legs = co.legs;
      }
      if (co.order_type === 'equity') {
        previewPayload.side = co.side;
        previewPayload.quantity = co.quantity;
      }

      // Show loading state on button
      var closeBtn = elRecDetail && elRecDetail.querySelector('[data-action="close-preview"]');
      if (closeBtn) { closeBtn.textContent = 'Previewing...'; closeBtn.disabled = true; }

      apiFetch('/api/trading/close-preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(previewPayload),
      })
        .then(function (preview) {
          if (closeBtn) { closeBtn.textContent = 'Preview Complete'; closeBtn.disabled = false; }

          if (!preview.ok) {
            alert('Preview failed: ' + (preview.tradier_preview_error || 'Unknown error'));
            if (closeBtn) closeBtn.textContent = co.action === 'REDUCE' ? 'Reduce Position' : 'Close Position';
            return;
          }

          // Show preview details and confirm submission
          var previewInfo = '';
          var tp = preview.tradier_preview;
          if (tp && tp.order) {
            var orderInfo = tp.order;
            previewInfo += '\nStatus: ' + (orderInfo.status || '—');
            if (orderInfo.commission != null) previewInfo += '\nCommission: $' + orderInfo.commission;
            if (orderInfo.cost != null) previewInfo += '\nCost: $' + orderInfo.cost;
          }

          if (!confirm('Submit this close order?\n\n' + desc + costLine + previewInfo + '\n\nAccount: ' + mode.toUpperCase())) {
            if (closeBtn) closeBtn.textContent = co.action === 'REDUCE' ? 'Reduce Position' : 'Close Position';
            return;
          }

          // Submit
          if (closeBtn) { closeBtn.textContent = 'Submitting...'; closeBtn.disabled = true; }

          var submitPayload = Object.assign({}, previewPayload);
          apiFetch('/api/trading/close-submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(submitPayload),
          })
            .then(function (result) {
              if (result.ok) {
                var statusMsg = result.dry_run ? 'DRY RUN' : result.status;
                alert('Order ' + statusMsg + '\n\n' + (result.message || '') +
                  (result.broker_order_id ? '\nOrder ID: ' + result.broker_order_id : ''));
                if (closeBtn) { closeBtn.textContent = 'Submitted'; closeBtn.disabled = true; closeBtn.classList.add('atp-btn-done'); }
              } else {
                alert('Order rejected: ' + (result.message || 'Unknown error'));
                if (closeBtn) { closeBtn.textContent = co.action === 'REDUCE' ? 'Reduce Position' : 'Close Position'; closeBtn.disabled = false; }
              }
            })
            .catch(function (err) {
              alert('Submit error: ' + err.message);
              if (closeBtn) { closeBtn.textContent = co.action === 'REDUCE' ? 'Reduce Position' : 'Close Position'; closeBtn.disabled = false; }
            });
        })
        .catch(function (err) {
          alert('Preview error: ' + err.message);
          if (closeBtn) { closeBtn.textContent = co.action === 'REDUCE' ? 'Reduce Position' : 'Close Position'; closeBtn.disabled = false; }
        });
    }

    /* ── Show recommendation in detail panel ──────────────────── */
    function showRecInDetail(rec) {
      if (elDetailEmpty) elDetailEmpty.style.display = 'none';
      if (elDetailContent) elDetailContent.style.display = '';
      if (elStageDetail) elStageDetail.style.display = 'none';
      if (elRecDetail) elRecDetail.style.display = '';

      // Deselect stage nodes
      for (var k in nodeEls) nodeEls[k].classList.remove('tbp-node-selected');
      selectedStage = null;

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
      var healthScore = engineSummary.trade_health_score;
      var riskFlags = rec.internal_engine_flags || [];
      var nextMove = rec.suggested_next_move || '';

      var h = '';

      // Header
      h += '<div class="tbp-detail-header" style="border-bottom-color:rgba(0,234,255,0.08)">'
         + '<div class="tbp-detail-stage-name" style="font-size:16px">' + esc(symbol) + '</div>'
         + '<div class="tmc-card-action ' + recClass(recommendation) + '">' + esc(recommendation) + '</div>'
         + '</div>';

      // Conviction
      var convPct = conviction != null ? Math.round(conviction * 100) : 0;
      h += '<div class="tmc-conviction-row" style="margin:10px 0">'
         + '<span class="tmc-label">Conviction</span>'
         + '<div class="tmc-conviction-bar-wrap"><div class="tmc-conviction-bar" style="width:' + convPct + '%"></div></div>'
         + '<span class="tmc-conviction-value">' + fmtPct(conviction) + '</span></div>';

      // Key metrics
      var pnlVal = posSnap.unrealized_pnl;
      var pnlText = pnlVal != null ? '$' + pnlVal.toFixed(2) : '—';
      h += '<div class="tbp-detail-metrics">';
      h += '<div class="tbp-detail-metric"><span class="tbp-dm-label">Health</span><span class="tbp-dm-value">' + (healthScore != null ? healthScore + '/100' : '—') + '</span></div>';
      h += '<div class="tbp-detail-metric"><span class="tbp-dm-label">P&L</span><span class="tbp-dm-value">' + pnlText + '</span></div>';
      h += '<div class="tbp-detail-metric"><span class="tbp-dm-label">Urgency</span><span class="tbp-dm-value ' + urgencyClass(urgency) + '">' + urgencyLabel(urgency) + '</span></div>';
      h += '</div>';

      // Engine component scores
      var compKeys = Object.keys(engineMetrics);
      if (compKeys.length > 0) {
        h += '<div class="tmc-engine-scores"><div class="tmc-rationale-label">Engine Scores</div><div class="tmc-engine-grid">';
        compKeys.forEach(function (k) {
          var v = engineMetrics[k];
          h += '<span class="tmc-engine-item">' + esc(k.replace(/_/g, ' ')) + ': <strong>' + (v != null ? Math.round(v) : '—') + '</strong></span>';
        });
        h += '</div></div>';
      }

      // Risk flags
      if (riskFlags.length > 0) {
        h += '<div class="tmc-risk-flags">';
        riskFlags.forEach(function (f) { h += '<span class="tmc-risk-flag">' + esc(f) + '</span>'; });
        h += '</div>';
      }

      // Rationale
      if (rationale) {
        h += '<div class="tmc-rationale"><div class="tmc-rationale-label">Rationale</div><div class="tmc-rationale-text">' + esc(rationale) + '</div></div>';
      }

      // Points
      if (points.length > 0) {
        h += '<div class="tmc-points"><div class="tmc-points-label">Supporting Points</div><ul class="tmc-points-list">';
        points.forEach(function (p) { h += '<li>' + esc(p) + '</li>'; });
        h += '</ul></div>';
      }

      // Risks
      if (risks.length > 0) {
        h += '<div class="tmc-risks"><div class="tmc-risks-label">Risks</div><ul class="tmc-risks-list">';
        risks.forEach(function (r) { h += '<li>' + esc(r) + '</li>'; });
        h += '</ul></div>';
      }

      // Next move
      if (nextMove) {
        h += '<div class="tmc-next-move"><div class="tmc-rationale-label">Suggested Next Move</div><div class="tmc-next-move-text">' + esc(nextMove) + '</div></div>';
      }

      // Model meta
      if (modelSummary.model_available) {
        h += '<div class="tmc-model-meta"><span class="tmc-meta-item">' + esc(modelSummary.provider || '—') + '</span><span class="tmc-meta-sep">·</span><span class="tmc-meta-item">' + esc(modelSummary.model_name || '—') + '</span></div>';
      } else {
        h += '<div class="tmc-model-meta"><span class="tmc-meta-item tmc-meta-degraded">Engine only</span></div>';
      }

      // Close / Reduce button — only for actionable recommendations
      var closeOrder = rec.suggested_close_order;
      if (closeOrder && closeOrder.ready_for_preview && recommendation !== 'HOLD') {
        var btnLabel = recommendation === 'REDUCE' ? 'Reduce Position' : 'Close Position';
        var btnClass = recommendation === 'REDUCE' ? 'atp-btn-reduce' : 'atp-btn-close';
        h += '<div class="atp-close-action">'
           + '<button class="btn atp-close-btn ' + btnClass + '" data-action="close-preview">'
           + btnLabel + '</button>';
        if (closeOrder.description) {
          h += '<div class="atp-close-desc">' + esc(closeOrder.description) + '</div>';
        }
        if (closeOrder.estimated_cost != null) {
          h += '<div class="atp-close-cost">Est. cost: $' + Math.abs(closeOrder.estimated_cost).toFixed(2) + ' (' + esc(closeOrder.price_effect || '') + ')</div>';
        } else if (closeOrder.estimated_proceeds != null) {
          h += '<div class="atp-close-cost">Est. proceeds: $' + closeOrder.estimated_proceeds.toFixed(2) + '</div>';
        }
        h += '</div>';
      }

      if (elRecDetail) {
        elRecDetail.innerHTML = h;
        // Attach close button handler via DOM query
        var closeBtn = elRecDetail.querySelector('[data-action="close-preview"]');
        if (closeBtn) {
          closeBtn.addEventListener('click', function () { handleClosePreview(rec); });
        }
      }
    }

    /* ── Results section (recommendation mini-cards below graph) ── */
    function renderResults(data) {
      _allRecs = data.recommendations || [];
      currentRunData = data;
      currentRunId = data.run_id || null;

      updateRunHeader(data);
      updateNodes();

      // Show results section
      if (elResultsSection) elResultsSection.style.display = '';

      applyFilter();
    }

    function applyFilter() {
      var filter = elFilterRec ? elFilterRec.value : '';
      var recs = _allRecs;
      if (filter) {
        recs = recs.filter(function (r) { return (r.recommendation || '').toUpperCase() === filter; });
      }

      // Sort: urgency desc, then conviction desc
      var sorted = recs.slice().sort(function (a, b) {
        var ua = a.urgency || 0, ub = b.urgency || 0;
        if (ua !== ub) return ub - ua;
        return (b.conviction || 0) - (a.conviction || 0);
      });

      if (elResultsTitle) {
        elResultsTitle.textContent = 'Recommendations (' + sorted.length + (filter ? ' ' + filter : '') + ')';
      }

      if (!elResultsGrid) return;

      if (sorted.length === 0) {
        elResultsGrid.innerHTML =
          '<div class="tmc-empty-state">'
          + '<div class="tmc-empty-icon">◉</div>'
          + '<div class="tmc-empty-text">' + (filter ? 'No ' + esc(filter) + ' recommendations.' : 'No recommendations in this run.') + '</div>'
          + '</div>';
        return;
      }

      elResultsGrid.innerHTML = '';
      sorted.forEach(function (rec) {
        var mini = buildRecMiniCard(rec);
        mini.addEventListener('click', function () { showRecInDetail(rec); });
        elResultsGrid.appendChild(mini);
      });
    }

    /* ── Recommendation mini-card (compact row card below graph) ── */
    function buildRecMiniCard(rec) {
      var symbol = rec.symbol || '???';
      var recommendation = (rec.recommendation || '—').toUpperCase();
      var conviction = rec.conviction;
      var urgency = rec.urgency || 1;
      var healthScore = (rec.internal_engine_summary || {}).trade_health_score;
      var isDegraded = rec.is_degraded;
      var hasCloseOrder = rec.suggested_close_order && rec.suggested_close_order.ready_for_preview;

      var card = document.createElement('div');
      card.className = 'atp-rec-mini-card atp-rec-border-' + recommendation.toLowerCase().replace('_', '-');

      card.innerHTML =
        '<div class="atp-mini-symbol">' + esc(symbol) + '</div>'
        + '<div class="tmc-card-action ' + recClass(recommendation) + '">' + esc(recommendation) + '</div>'
        + '<div class="atp-mini-conviction">' + fmtPct(conviction) + '</div>'
        + '<div class="atp-mini-health">' + (healthScore != null ? healthScore + '/100' : '—') + '</div>'
        + '<div class="' + urgencyClass(urgency) + '">' + esc(urgencyLabel(urgency)) + '</div>'
        + (isDegraded ? '<span class="atp-mini-degraded">⚠</span>' : '')
        + (hasCloseOrder && recommendation !== 'HOLD' ? '<span class="atp-mini-actionable" title="Close order available">⚡</span>' : '');

      return card;
    }

    /* ── Empty / loading / error states ───────────────────────── */
    function showEmpty(msg) {
      if (elResultsSection) elResultsSection.style.display = '';
      if (elResultsGrid) {
        elResultsGrid.innerHTML =
          '<div class="tmc-empty-state">'
          + '<div class="tmc-empty-icon">◉</div>'
          + '<div class="tmc-empty-text">' + esc(msg) + '</div>'
          + '</div>';
      }
    }

    function showLoading() {
      if (elResultsSection) elResultsSection.style.display = '';
      if (elResultsGrid) {
        elResultsGrid.innerHTML =
          '<div class="tmc-empty-state">'
          + '<div class="atp-loading-spinner"></div>'
          + '<div class="tmc-empty-text">Running pipeline — analysing active positions…</div>'
          + '</div>';
      }
      setStatus('Pipeline running…');
    }

    function showError(msg) {
      if (elResultsSection) elResultsSection.style.display = '';
      if (elResultsGrid) {
        elResultsGrid.innerHTML =
          '<div class="tmc-empty-state">'
          + '<div class="tmc-empty-icon" style="color:var(--danger,#ff4f66)">✕</div>'
          + '<div class="tmc-empty-text" style="color:var(--danger,#ff4f66)">' + esc(msg) + '</div>'
          + '</div>';
      }
      setStatus('Error');
    }

    /* ── Run controls ────────────────────────────────────────── */
    function doStartRun() {
      if (_running) return;
      _running = true;
      setStatus('Starting pipeline run…');
      if (elBtnStart) { elBtnStart.textContent = '⏳ Running…'; elBtnStart.disabled = true; }

      var skipModel = false;
      var cb = scope.querySelector('#atpSkipModel');
      if (cb) skipModel = cb.checked;

      // Set all nodes to pending, first stage to running
      for (var i = 0; i < STAGE_ORDER.length; i++) {
        var el = nodeEls[STAGE_ORDER[i]];
        if (el) {
          if (i === 0) {
            el.className = 'tbp-node tbp-node-running';
            el.querySelector('.tbp-node-icon').textContent = STATUS_ICONS.running;
          } else {
            el.className = 'tbp-node tbp-node-pending';
            el.querySelector('.tbp-node-icon').textContent = STATUS_ICONS.pending;
          }
        }
      }
      drawConnectors();
      showLoading();

      var url = '/api/active-trade-pipeline/run?skip_model=' + (skipModel ? 'true' : 'false');

      apiFetch(url, { method: 'POST' })
        .then(function (data) {
          _running = false;
          if (elBtnStart) { elBtnStart.textContent = '▶ Start Run'; elBtnStart.disabled = false; }

          if (data.ok === false) {
            // Mark first stage as failed, rest remain pending
            STAGE_ORDER.forEach(function (key, idx) {
              var n = nodeEls[key];
              if (n) {
                if (idx === 0) {
                  n.className = 'tbp-node tbp-node-failed';
                  n.querySelector('.tbp-node-icon').textContent = STATUS_ICONS.failed;
                } else {
                  n.className = 'tbp-node tbp-node-pending';
                  n.querySelector('.tbp-node-icon').textContent = STATUS_ICONS.pending;
                }
              }
            });
            drawConnectors();
            showError('Pipeline error: ' + ((data.error || {}).message || 'unknown'));
            return;
          }

          renderResults(data);
          loadRunList();
          setStatus('Run completed — ' + (data.trade_count || 0) + ' trades reviewed');
        })
        .catch(function (err) {
          _running = false;
          if (elBtnStart) { elBtnStart.textContent = '▶ Start Run'; elBtnStart.disabled = false; }
          STAGE_ORDER.forEach(function (key) {
            var n = nodeEls[key];
            if (n) { n.className = 'tbp-node tbp-node-failed'; n.querySelector('.tbp-node-icon').textContent = STATUS_ICONS.failed; }
          });
          drawConnectors();
          showError('Failed to run pipeline: ' + err.message);
          console.error('[ATP] Pipeline run failed:', err);
        });
    }

    /* ── Load run list for picker (matches TBP loadRunList) ──── */
    function loadRunList() {
      apiFetch('/api/active-trade-pipeline/runs')
        .then(function (data) {
          if (!elRunPicker || !data.ok || !data.runs) return;
          var html = '<option value="">— Recent Runs (' + (data.runs.length) + ') —</option>';
          data.runs.forEach(function (r) {
            var sel = r.run_id === currentRunId ? ' selected' : '';
            var statusBadge = (r.status || '').toLowerCase() === 'completed' ? '✓' : r.status === 'failed' ? '✕' : '○';
            html += '<option value="' + esc(r.run_id) + '"' + sel + '>'
                   + statusBadge + ' ' + esc(shortId(r.run_id)) + ' — ' + esc(fmtTime(r.started_at))
                   + ' — ' + (r.trade_count || 0) + ' trades</option>';
          });
          elRunPicker.innerHTML = html;
        })
        .catch(function () { /* silent */ });
    }

    /* ── Load run detail (matches TBP loadRun) ───────────────── */
    function loadRun(runId) {
      if (!runId) return;
      setStatus('Loading run ' + shortId(runId) + '…');

      apiFetch('/api/active-trade-pipeline/results/' + encodeURIComponent(runId))
        .then(function (data) {
          if (data.ok === false) {
            showError((data.error || {}).message || 'Run not found');
            return;
          }
          renderResults(data);
          if (selectedStage) selectStage(selectedStage);
          setStatus('Loaded run ' + shortId(runId));
        })
        .catch(function (err) {
          showError('Failed to load run: ' + err.message);
        });
    }

    /* ── Wire events (mirrors TBP) ───────────────────────────── */
    if (elBtnStart) elBtnStart.onclick = doStartRun;
    if (elRunPicker) elRunPicker.onchange = function () {
      var v = elRunPicker.value;
      if (v) loadRun(v);
    };
    if (elFilterRec) elFilterRec.onchange = function () { applyFilter(); };
    if (elBtnTmc) elBtnTmc.onclick = function () { window.location.hash = '#/trade-management'; };

    // Redraw connectors on resize
    var resizeTimer;
    window.addEventListener('resize', function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(drawConnectors, 150);
    });

    /* ── Bootstrap ───────────────────────────────────────────── */
    renderGraph();
    setStatus('Pipeline ready — ' + STAGE_ORDER.length + ' stages');

    // Load run list and try to show latest run
    loadRunList();
    apiFetch('/api/active-trade-pipeline/results')
      .then(function (data) {
        if (data.ok !== false) {
          renderResults(data);
        }
      })
      .catch(function () {
        setStatus('Ready — no previous runs. Click Start Run to begin.');
      });

    // Cleanup
    window.BenTradeActiveViewCleanup = function () {
      _running = false;
      currentRunId = null;
      currentRunData = null;
      _allRecs = [];
    };
  };
})();
