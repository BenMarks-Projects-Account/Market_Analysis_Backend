// Scanner Review — page module (Prompt 14)
// Renders V2 routing overview, family verification cards, per-run
// scanner diagnostics, and candidate drill-in for the Scanner Review
// dashboard at #/admin/scanner-review.
(function () {
  'use strict';

  window.BenTradePages = window.BenTradePages || {};

  /* ── Constants ──────────────────────────────────────────────── */
  var FAMILY_LABELS = {
    vertical_spreads: 'Vertical Spreads',
    iron_condors: 'Iron Condors',
    butterflies: 'Butterflies',
    calendars: 'Calendars',
    stock: 'Stock Strategies',
    other: 'Other',
  };

  var PATH_LABELS = {
    v2: 'V2',
    legacy: 'Legacy',
    unknown: '?',
  };

  var STATUS_CLASSES = {
    completed: 'sr-status-completed',
    failed: 'sr-status-failed',
    running: 'sr-status-running',
    pending: 'sr-status-pending',
    skipped: 'sr-status-skipped',
  };

  /* ── Helpers ────────────────────────────────────────────────── */
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
    var cls = STATUS_CLASSES[status] || 'sr-status-pending';
    return '<span class="qtPill ' + cls + '">' + esc(status || 'unknown') + '</span>';
  }

  function pathPill(path) {
    var cls = path === 'v2' ? 'sr-path-v2' : (path === 'legacy' ? 'sr-path-legacy' : 'sr-path-unknown');
    var label = PATH_LABELS[path] || path || '?';
    return '<span class="qtPill ' + cls + '">' + esc(label) + '</span>';
  }

  function setDisplay(el, show) {
    if (el) el.style.display = show ? '' : 'none';
  }

  function apiFetch(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function fmtNum(n) {
    if (n == null) return '—';
    return typeof n === 'number' ? n.toLocaleString() : String(n);
  }

  function fmtPct(n) {
    if (n == null) return '—';
    return (n * 100).toFixed(1) + '%';
  }

  /* ── Init ───────────────────────────────────────────────────── */
  window.BenTradePages.initScannerReview = function initScannerReview(rootEl) {
    var scope = rootEl || document.body;

    // DOM refs
    var elSubtitle = scope.querySelector('#srSubtitle');
    var elRefreshBtn = scope.querySelector('#srRefreshBtn');
    var elErrorBanner = scope.querySelector('#srErrorBanner');
    var elLoading = scope.querySelector('#srLoading');
    var elRoutingGrid = scope.querySelector('#srRoutingGrid');
    var elFamilyGrid = scope.querySelector('#srFamilyGrid');
    var elRunSelector = scope.querySelector('#srRunSelector');
    var elRunSummary = scope.querySelector('#srRunSummary');
    var elRunHeaderGrid = scope.querySelector('#srRunHeaderGrid');
    var elScannerFamilies = scope.querySelector('#srScannerFamilies');
    var elScannerTableWrap = scope.querySelector('#srScannerTableWrap');
    var elScannerRows = scope.querySelector('#srScannerRows');
    var elCandidateSection = scope.querySelector('#srCandidateSection');
    var elCandidateSectionTitle = scope.querySelector('#srCandidateSectionTitle');
    var elCandidateFamilyFilter = scope.querySelector('#srCandidateFamilyFilter');
    var elCandidateBackBtn = scope.querySelector('#srCandidateBackBtn');
    var elCandidateRows = scope.querySelector('#srCandidateRows');
    var elCandidateCount = scope.querySelector('#srCandidateCount');
    var elDetailPanel = scope.querySelector('#srDetailPanel');
    var elDetailTitle = scope.querySelector('#srDetailTitle');
    var elDetailCloseBtn = scope.querySelector('#srDetailCloseBtn');
    var elDetailMetrics = scope.querySelector('#srDetailMetrics');
    var elDetailTrace = scope.querySelector('#srDetailTrace');
    var elDetailRawToggle = scope.querySelector('#srDetailRawToggle');
    var elDetailJson = scope.querySelector('#srDetailJson');

    var currentRunId = null;
    var currentRunData = null;
    var currentCandidates = [];

    /* ── Error handling ──────────────────────────────────────── */
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

    /* ── Load all data ───────────────────────────────────────── */
    function loadAll() {
      clearError();
      setDisplay(elLoading, true);

      Promise.all([
        apiFetch('/api/scanner-review/routing'),
        apiFetch('/api/pipeline/runs'),
      ])
        .then(function (results) {
          setDisplay(elLoading, false);
          renderRoutingOverview(results[0]);
          renderFamilyVerification(results[0].family_verification || {});
          populateRunSelector(results[1].runs || []);
        })
        .catch(function (err) {
          setDisplay(elLoading, false);
          showError('Failed to load scanner review data: ' + err.message);
        });
    }

    /* ── Section 1: Routing Overview ─────────────────────────── */
    function renderRoutingOverview(data) {
      if (!elRoutingGrid) return;

      var model = data.routing_model || 'unknown';
      var v2Families = data.v2_families || [];
      var overrides = data.overrides_active || {};
      var legacyForced = data.legacy_forced_keys || [];
      var registry = data.pipeline_registry || {};

      var html = '';

      // Routing model card
      html += '<div class="sr-routing-card">';
      html += '<div class="sr-routing-card-label">Routing Model</div>';
      html += '<div class="sr-routing-card-value">' + pathPill(model) + '</div>';
      html += '</div>';

      // V2 families card
      html += '<div class="sr-routing-card">';
      html += '<div class="sr-routing-card-label">V2 Families</div>';
      html += '<div class="sr-routing-card-value">' + esc(v2Families.length ? v2Families.join(', ') : 'None') + '</div>';
      html += '</div>';

      // Overrides card
      var overrideCount = Object.keys(overrides).length;
      html += '<div class="sr-routing-card">';
      html += '<div class="sr-routing-card-label">Active Overrides</div>';
      html += '<div class="sr-routing-card-value">' + esc(String(overrideCount)) + '</div>';
      if (overrideCount > 0) {
        html += '<div class="sr-routing-card-detail">';
        for (var k in overrides) {
          html += '<div>' + esc(k) + ' → ' + esc(String(overrides[k])) + '</div>';
        }
        html += '</div>';
      }
      html += '</div>';

      // Legacy forced keys
      html += '<div class="sr-routing-card">';
      html += '<div class="sr-routing-card-label">Legacy Forced Keys</div>';
      html += '<div class="sr-routing-card-value">' + esc(legacyForced.length ? legacyForced.join(', ') : 'None') + '</div>';
      html += '</div>';

      // Pipeline registry
      var regKeys = Object.keys(registry);
      if (regKeys.length > 0) {
        html += '<div class="sr-routing-card sr-routing-card-wide">';
        html += '<div class="sr-routing-card-label">Scanner Key → Execution Path</div>';
        html += '<div class="sr-routing-card-detail sr-key-routing">';
        var keyRouting = data.scanner_key_routing || {};
        for (var sk in keyRouting) {
          html += '<div class="sr-key-route-row">';
          html += '<span class="sr-key-name">' + esc(sk) + '</span>';
          html += pathPill(keyRouting[sk]);
          html += '</div>';
        }
        html += '</div>';
        html += '</div>';
      }

      elRoutingGrid.innerHTML = html;
    }

    /* ── Section 2: Family Verification ──────────────────────── */
    function renderFamilyVerification(familyData) {
      if (!elFamilyGrid) return;

      if (!familyData || typeof familyData !== 'object') {
        elFamilyGrid.innerHTML = '<div class="sr-empty">No family verification data available.</div>';
        return;
      }

      var families = familyData.families || familyData;
      var keys = Object.keys(families);

      if (keys.length === 0) {
        elFamilyGrid.innerHTML = '<div class="sr-empty">No V2 families registered.</div>';
        return;
      }

      var html = '';
      for (var i = 0; i < keys.length; i++) {
        var fk = keys[i];
        var fam = families[fk];
        if (!fam || typeof fam !== 'object') continue;

        var label = FAMILY_LABELS[fk] || fk;
        var scanners = fam.scanners || fam.scanner_keys || [];
        var ready = fam.ready !== false;
        var statusCls = ready ? 'sr-family-ready' : 'sr-family-not-ready';

        html += '<div class="sr-family-card ' + statusCls + '">';
        html += '<div class="sr-family-card-header">';
        html += '<span class="sr-family-card-name">' + esc(label) + '</span>';
        html += '<span class="qtPill ' + (ready ? 'sr-status-completed' : 'sr-status-pending') + '">' + (ready ? 'Ready' : 'Not Ready') + '</span>';
        html += '</div>';
        html += '<div class="sr-family-card-scanners">';
        for (var j = 0; j < scanners.length; j++) {
          html += '<span class="sr-scanner-chip">' + esc(scanners[j]) + '</span>';
        }
        html += '</div>';

        // Extra details if available
        if (fam.test_count != null) {
          html += '<div class="sr-family-card-meta">' + esc(String(fam.test_count)) + ' tests</div>';
        }
        if (fam.notes) {
          html += '<div class="sr-family-card-meta">' + esc(fam.notes) + '</div>';
        }

        html += '</div>';
      }

      elFamilyGrid.innerHTML = html;
    }

    /* ── Section 3: Per-run selector ─────────────────────────── */
    function populateRunSelector(runs) {
      if (!elRunSelector) return;

      var html = '<option value="">Select a pipeline run…</option>';
      for (var i = 0; i < runs.length; i++) {
        var r = runs[i];
        var label = shortId(r.run_id) + ' — ' + (r.status || '?') + ' — ' + fmtTime(r.started_at);
        html += '<option value="' + esc(r.run_id) + '">' + esc(label) + '</option>';
      }
      elRunSelector.innerHTML = html;
    }

    function loadRunScannerSummary(runId) {
      if (!runId) {
        setDisplay(elRunSummary, false);
        setDisplay(elScannerFamilies, false);
        setDisplay(elScannerTableWrap, false);
        setDisplay(elCandidateSection, false);
        currentRunId = null;
        currentRunData = null;
        return;
      }

      currentRunId = runId;
      clearError();
      setDisplay(elLoading, true);

      apiFetch('/api/scanner-review/runs/' + encodeURIComponent(runId) + '/scanner-summary')
        .then(function (data) {
          setDisplay(elLoading, false);
          if (!data.available) {
            showError(data.message || 'No scanner data for this run.');
            setDisplay(elRunSummary, false);
            setDisplay(elScannerFamilies, false);
            setDisplay(elScannerTableWrap, false);
            return;
          }
          currentRunData = data;
          renderRunSummaryHeader(data);
          renderScannerFamilyCards(data.family_groups || {});
          renderScannerTable(data.scanner_summaries || {});
          setDisplay(elRunSummary, true);
          setDisplay(elScannerFamilies, true);
          setDisplay(elScannerTableWrap, true);
        })
        .catch(function (err) {
          setDisplay(elLoading, false);
          showError('Failed to load run scanner summary: ' + err.message);
        });
    }

    /* ── Run summary header ──────────────────────────────────── */
    function renderRunSummaryHeader(data) {
      if (!elRunHeaderGrid) return;

      var items = [
        { label: 'Stage Status', value: statusPill(data.stage_status), raw: true },
        { label: 'Scanners Run', value: String(data.total_run || 0) },
        { label: 'Total Candidates', value: String(data.total_candidates || 0) },
        { label: 'Usable Candidates', value: String(data.total_usable_candidates || 0) },
        { label: 'Completed', value: String(data.completed_count || 0) },
        { label: 'Failed', value: String(data.failed_count || 0), cls: data.failed_count > 0 ? 'negative' : '' },
        { label: 'Duration', value: fmtMs(data.elapsed_ms) },
        { label: 'Generated', value: fmtTime(data.generated_at) },
      ];

      var routingSummary = data.routing_summary || {};
      if (routingSummary.v2_count != null) {
        items.push({ label: 'V2 Scanners', value: String(routingSummary.v2_count || 0) });
      }
      if (routingSummary.legacy_count != null) {
        items.push({ label: 'Legacy Scanners', value: String(routingSummary.legacy_count || 0) });
      }

      var liveness = data.liveness_snapshot;
      if (liveness) {
        var timedOut = (liveness.timed_out || []).length;
        var capHit = (liveness.cap_hit || []).length;
        if (timedOut > 0) {
          items.push({ label: 'Timed Out', value: String(timedOut), cls: 'negative' });
        }
        if (capHit > 0) {
          items.push({ label: 'Cap Hit', value: String(capHit), cls: 'warning' });
        }
      }

      var html = '';
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        html += '<div class="sr-header-metric">';
        html += '<div class="metric-label">' + esc(it.label) + '</div>';
        if (it.raw) {
          html += '<div class="metric-value">' + it.value + '</div>';
        } else {
          html += '<div class="metric-value' + (it.cls ? ' ' + it.cls : '') + '">' + esc(it.value) + '</div>';
        }
        html += '</div>';
      }
      elRunHeaderGrid.innerHTML = html;
    }

    /* ── Scanner family group cards ──────────────────────────── */
    function renderScannerFamilyCards(familyGroups) {
      if (!elScannerFamilies) return;

      var keys = Object.keys(familyGroups);
      if (keys.length === 0) {
        elScannerFamilies.innerHTML = '<div class="sr-empty">No scanner family data.</div>';
        return;
      }

      var html = '<div class="sr-scanner-family-grid">';
      for (var i = 0; i < keys.length; i++) {
        var fk = keys[i];
        var fam = familyGroups[fk];
        var label = FAMILY_LABELS[fk] || fk;
        var scanners = fam.scanners || [];
        var paths = fam.execution_paths || [];

        html += '<div class="sr-scanner-family-card">';
        html += '<div class="sr-sfam-header">';
        html += '<span class="sr-sfam-name">' + esc(label) + '</span>';
        for (var p = 0; p < paths.length; p++) {
          html += pathPill(paths[p]);
        }
        html += '</div>';
        html += '<div class="sr-sfam-metrics">';
        html += '<div class="sr-sfam-metric"><span class="metric-label">Candidates</span><span class="metric-value">' + esc(String(fam.total_candidates || 0)) + '</span></div>';
        html += '<div class="sr-sfam-metric"><span class="metric-label">Usable</span><span class="metric-value">' + esc(String(fam.total_usable || 0)) + '</span></div>';
        html += '<div class="sr-sfam-metric"><span class="metric-label">Scanners</span><span class="metric-value">' + esc(String(scanners.length)) + '</span></div>';
        html += '</div>';
        html += '<div class="sr-sfam-scanners">';
        for (var j = 0; j < scanners.length; j++) {
          html += '<span class="sr-scanner-chip">' + esc(scanners[j]) + '</span>';
        }
        html += '</div>';
        html += '</div>';
      }
      html += '</div>';

      elScannerFamilies.innerHTML = html;
    }

    /* ── Per-scanner diagnostics table ────────────────────────── */
    function renderScannerTable(scannerSummaries) {
      if (!elScannerRows) return;

      var keys = Object.keys(scannerSummaries);
      if (keys.length === 0) {
        elScannerRows.innerHTML = '<tr><td colspan="7" class="sr-empty-cell">No scanners found.</td></tr>';
        return;
      }

      var html = '';
      for (var i = 0; i < keys.length; i++) {
        var sk = keys[i];
        var s = scannerSummaries[sk];
        html += '<tr class="sr-scanner-row" data-scanner-key="' + esc(sk) + '">';
        html += '<td class="sr-cell-key">' + esc(sk) + '</td>';
        html += '<td>' + pathPill(s.execution_path || 'unknown') + '</td>';
        html += '<td>' + statusPill(s.status) + '</td>';
        html += '<td>' + esc(String(s.candidate_count || 0)) + '</td>';
        html += '<td>' + esc(String(s.usable_candidate_count || 0)) + '</td>';
        html += '<td>' + esc(fmtMs(s.elapsed_ms)) + '</td>';
        html += '<td>';
        html += '<button class="btn sr-btn sr-btn-sm sr-drill-btn" data-scanner-key="' + esc(sk) + '">Drill In</button>';
        if (s.diagnostics) {
          html += ' <button class="btn sr-btn sr-btn-sm sr-diag-btn" data-scanner-key="' + esc(sk) + '">Diag</button>';
        }
        html += '</td>';
        html += '</tr>';

        // Diagnostics expansion row (hidden by default)
        if (s.diagnostics) {
          html += '<tr class="sr-diag-row" id="srDiag_' + esc(sk) + '" style="display:none;">';
          html += '<td colspan="7">';
          html += renderDiagnosticsInline(s.diagnostics);
          html += '</td>';
          html += '</tr>';
        }
      }
      elScannerRows.innerHTML = html;
    }

    /* ── Inline diagnostics rendering ────────────────────────── */
    function renderDiagnosticsInline(diag) {
      if (!diag) return '';

      var html = '<div class="sr-diag-inline">';

      // Stage counts
      var sc = diag.stage_counts;
      if (sc && sc.length > 0) {
        html += '<div class="sr-diag-block">';
        html += '<div class="sr-diag-block-title">Filter Stage Counts</div>';
        html += '<div class="sr-diag-stage-bar">';
        for (var i = 0; i < sc.length; i++) {
          var stage = sc[i];
          var label = stage.stage || stage.name || ('Stage ' + i);
          var count = stage.remaining != null ? stage.remaining : (stage.count != null ? stage.count : '?');
          html += '<div class="sr-diag-stage-step">';
          html += '<span class="sr-diag-stage-label">' + esc(label) + '</span>';
          html += '<span class="sr-diag-stage-count">' + esc(String(count)) + '</span>';
          html += '</div>';
          if (i < sc.length - 1) html += '<span class="sr-diag-arrow">→</span>';
        }
        html += '</div>';
        html += '</div>';
      }

      // Rejection reason counts
      var rr = diag.rejection_reason_counts;
      if (rr && typeof rr === 'object') {
        var rrKeys = Object.keys(rr);
        if (rrKeys.length > 0) {
          html += '<div class="sr-diag-block">';
          html += '<div class="sr-diag-block-title">Rejection Reasons</div>';
          html += '<div class="sr-diag-reasons">';
          // Sort by count descending
          rrKeys.sort(function (a, b) { return (rr[b] || 0) - (rr[a] || 0); });
          for (var j = 0; j < rrKeys.length; j++) {
            html += '<div class="sr-diag-reason-row">';
            html += '<span class="sr-diag-reason-code">' + esc(rrKeys[j]) + '</span>';
            html += '<span class="sr-diag-reason-count">' + esc(String(rr[rrKeys[j]])) + '</span>';
            html += '</div>';
          }
          html += '</div>';
          html += '</div>';
        }
      }

      // Data quality counts
      var dq = diag.data_quality_counts;
      if (dq && typeof dq === 'object') {
        var dqKeys = Object.keys(dq);
        if (dqKeys.length > 0) {
          html += '<div class="sr-diag-block">';
          html += '<div class="sr-diag-block-title">Data Quality</div>';
          html += '<div class="sr-diag-reasons">';
          for (var d = 0; d < dqKeys.length; d++) {
            html += '<div class="sr-diag-reason-row">';
            html += '<span class="sr-diag-reason-code">' + esc(dqKeys[d]) + '</span>';
            html += '<span class="sr-diag-reason-count">' + esc(String(dq[dqKeys[d]])) + '</span>';
            html += '</div>';
          }
          html += '</div>';
          html += '</div>';
        }
      }

      // Candidate / accepted counts
      if (diag.candidate_count != null || diag.accepted_count != null) {
        html += '<div class="sr-diag-block">';
        html += '<div class="sr-diag-block-title">Funnel</div>';
        html += '<span class="sr-diag-funnel">Candidates: ' + esc(String(diag.candidate_count || 0));
        html += ' → Accepted: ' + esc(String(diag.accepted_count || 0)) + '</span>';
        html += '</div>';
      }

      html += '</div>';
      return html;
    }

    /* ── Section 4: Candidate drill-in ───────────────────────── */
    function loadCandidates(scannerKey) {
      clearError();
      setDisplay(elLoading, true);

      var url = '/api/scanner-review/runs/' + encodeURIComponent(currentRunId) + '/candidates';
      if (scannerKey) url += '?scanner_key=' + encodeURIComponent(scannerKey);

      apiFetch(url)
        .then(function (data) {
          setDisplay(elLoading, false);
          currentCandidates = data.candidates || [];
          if (elCandidateSectionTitle) {
            elCandidateSectionTitle.textContent = scannerKey
              ? 'Candidates — ' + scannerKey
              : 'All Candidates';
          }
          renderCandidateTable(currentCandidates);
          setDisplay(elCandidateSection, true);
        })
        .catch(function (err) {
          setDisplay(elLoading, false);
          showError('Failed to load candidates: ' + err.message);
        });
    }

    function renderCandidateTable(candidates) {
      if (!elCandidateRows) return;

      if (candidates.length === 0) {
        elCandidateRows.innerHTML = '<tr><td colspan="10" class="sr-empty-cell">No candidates found.</td></tr>';
        if (elCandidateCount) elCandidateCount.textContent = '0 candidates';
        return;
      }

      var html = '';
      for (var i = 0; i < candidates.length; i++) {
        var c = candidates[i];
        html += '<tr class="sr-candidate-row" data-idx="' + i + '">';
        html += '<td><strong>' + esc(c.symbol || c.underlying || '—') + '</strong></td>';
        html += '<td>' + esc(c.strategy || c.strategy_type || '—') + '</td>';
        html += '<td>' + esc(c.scanner_key || '—') + '</td>';
        html += '<td>' + statusPill(c.status || c.decision || '—') + '</td>';
        html += '<td>' + esc(c.credit != null ? '$' + Number(c.credit).toFixed(2) : '—') + '</td>';
        html += '<td>' + esc(c.width != null ? '$' + Number(c.width).toFixed(0) : '—') + '</td>';
        html += '<td>' + esc(c.ev != null ? '$' + Number(c.ev).toFixed(2) : '—') + '</td>';
        html += '<td>' + esc(c.delta != null ? Number(c.delta).toFixed(3) : '—') + '</td>';
        html += '<td>' + esc(c.dte != null ? String(c.dte) : '—') + '</td>';
        html += '<td><button class="btn sr-btn sr-btn-sm sr-detail-btn" data-idx="' + i + '">Detail</button></td>';
        html += '</tr>';
      }
      elCandidateRows.innerHTML = html;

      if (elCandidateCount) {
        elCandidateCount.textContent = candidates.length + ' candidate' + (candidates.length !== 1 ? 's' : '');
      }
    }

    /* ── Detail panel ────────────────────────────────────────── */
    function showCandidateDetail(idx) {
      var c = currentCandidates[idx];
      if (!c) return;

      if (elDetailTitle) {
        elDetailTitle.textContent = (c.symbol || c.underlying || 'Candidate') + ' — ' + (c.strategy || c.scanner_key || 'Detail');
      }

      // Metrics
      if (elDetailMetrics) {
        var metrics = [
          { label: 'Symbol', value: c.symbol || c.underlying },
          { label: 'Strategy', value: c.strategy || c.strategy_type },
          { label: 'Scanner', value: c.scanner_key },
          { label: 'Status', value: c.status || c.decision, raw: statusPill(c.status || c.decision) },
          { label: 'Credit', value: c.credit != null ? '$' + Number(c.credit).toFixed(2) : null },
          { label: 'Width', value: c.width != null ? '$' + Number(c.width).toFixed(0) : null },
          { label: 'EV', value: c.ev != null ? '$' + Number(c.ev).toFixed(2) : null },
          { label: 'Delta', value: c.delta != null ? Number(c.delta).toFixed(3) : null },
          { label: 'DTE', value: c.dte },
          { label: 'P(OTM)', value: c.prob_otm != null ? fmtPct(c.prob_otm) : null },
          { label: 'IV Rank', value: c.iv_rank != null ? fmtPct(c.iv_rank) : null },
          { label: 'Open Interest', value: c.open_interest != null ? fmtNum(c.open_interest) : null },
        ];

        var html = '';
        for (var i = 0; i < metrics.length; i++) {
          var m = metrics[i];
          var val = m.raw || esc(m.value != null ? String(m.value) : '—');
          html += '<div class="sr-detail-metric">';
          html += '<span class="metric-label">' + esc(m.label) + '</span>';
          html += '<span class="metric-value">' + val + '</span>';
          html += '</div>';
        }
        elDetailMetrics.innerHTML = html;
      }

      // Filter trace / rejection info
      if (elDetailTrace) {
        var trace = c.filter_trace || c.rejection_reason || c.warn_reasons;
        if (trace) {
          var traceHtml = '<div class="sr-diag-block-title">Filter / Rejection Info</div>';
          if (typeof trace === 'string') {
            traceHtml += '<div class="sr-diag-reason-row"><span class="sr-diag-reason-code">' + esc(trace) + '</span></div>';
          } else if (Array.isArray(trace)) {
            for (var t = 0; t < trace.length; t++) {
              traceHtml += '<div class="sr-diag-reason-row"><span class="sr-diag-reason-code">' + esc(String(trace[t])) + '</span></div>';
            }
          } else if (typeof trace === 'object') {
            traceHtml += '<pre class="sr-trace-json">' + esc(JSON.stringify(trace, null, 2)) + '</pre>';
          }
          elDetailTrace.innerHTML = traceHtml;
        } else {
          elDetailTrace.innerHTML = '<div class="sr-empty">No filter trace data.</div>';
        }
      }

      // Raw JSON toggle
      if (elDetailJson) {
        elDetailJson.textContent = JSON.stringify(c, null, 2);
        elDetailJson.style.display = 'none';
      }
      if (elDetailRawToggle) {
        elDetailRawToggle.textContent = 'Show Raw JSON';
      }

      setDisplay(elDetailPanel, true);
    }

    /* ── Event bindings ──────────────────────────────────────── */

    // Refresh button
    if (elRefreshBtn) {
      elRefreshBtn.addEventListener('click', function () {
        loadAll();
        if (currentRunId) loadRunScannerSummary(currentRunId);
      });
    }

    // Run selector
    if (elRunSelector) {
      elRunSelector.addEventListener('change', function () {
        var runId = elRunSelector.value;
        setDisplay(elCandidateSection, false);
        setDisplay(elDetailPanel, false);
        loadRunScannerSummary(runId);
      });
    }

    // Candidate back button
    if (elCandidateBackBtn) {
      elCandidateBackBtn.addEventListener('click', function () {
        setDisplay(elCandidateSection, false);
        setDisplay(elDetailPanel, false);
      });
    }

    // Detail close button
    if (elDetailCloseBtn) {
      elDetailCloseBtn.addEventListener('click', function () {
        setDisplay(elDetailPanel, false);
      });
    }

    // Raw JSON toggle
    if (elDetailRawToggle) {
      elDetailRawToggle.addEventListener('click', function () {
        if (elDetailJson) {
          var showing = elDetailJson.style.display !== 'none';
          elDetailJson.style.display = showing ? 'none' : '';
          elDetailRawToggle.textContent = showing ? 'Show Raw JSON' : 'Hide Raw JSON';
        }
      });
    }

    // Delegated click handlers on scanner table
    if (elScannerRows) {
      elScannerRows.addEventListener('click', function (e) {
        var btn = e.target.closest('.sr-drill-btn');
        if (btn) {
          var sk = btn.getAttribute('data-scanner-key');
          if (sk && currentRunId) loadCandidates(sk);
          return;
        }

        var diagBtn = e.target.closest('.sr-diag-btn');
        if (diagBtn) {
          var dk = diagBtn.getAttribute('data-scanner-key');
          var diagRow = scope.querySelector('#srDiag_' + dk);
          if (diagRow) {
            var vis = diagRow.style.display !== 'none';
            diagRow.style.display = vis ? 'none' : '';
            diagBtn.textContent = vis ? 'Diag' : 'Hide';
          }
          return;
        }
      });
    }

    // Delegated click handlers on candidate table
    if (elCandidateRows) {
      elCandidateRows.addEventListener('click', function (e) {
        var btn = e.target.closest('.sr-detail-btn');
        if (btn) {
          var idx = parseInt(btn.getAttribute('data-idx'), 10);
          if (!isNaN(idx)) showCandidateDetail(idx);
        }
      });
    }

    // Family filter on candidates
    if (elCandidateFamilyFilter) {
      elCandidateFamilyFilter.addEventListener('change', function () {
        var family = elCandidateFamilyFilter.value;
        if (!family) {
          renderCandidateTable(currentCandidates);
        } else {
          var filtered = currentCandidates.filter(function (c) {
            return (c.family === family || c.scanner_family === family);
          });
          renderCandidateTable(filtered);
        }
      });
    }

    /* ── Initial load ────────────────────────────────────────── */
    loadAll();
  };
})();
