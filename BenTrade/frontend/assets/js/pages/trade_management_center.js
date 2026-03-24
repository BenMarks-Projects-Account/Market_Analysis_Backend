/**
 * BenTrade -- Trade Management Center (Prompt 10 consolidation)
 *
 * Depends on compact /api/tmc/workflows/... endpoints (Prompt 8/9).
 * Old trade-building pipeline payload assumptions are gone.
 * Active Trade section remains separate (uses /api/active-trade-pipeline).
 *
 * Section 1: Stock Opportunities  (TMC workflow endpoints)
 * Section 2: Options Opportunities (TMC workflow endpoints)
 * Section 3: Active Trade Candidates (active-trade-pipeline -- unchanged)
 */
(function () {
  'use strict';

  /* -- State --------------------------------------------------------- */
  var _pollTimer      = null;
  var _activeRunning  = false;
  /** Last loaded stock run_id from the /latest endpoint. */
  var _lastStockRunId = null;
  /** Last loaded options run_id from the /latest endpoint. */
  var _lastOptionsRunId = null;
  /** Completion-poll timer for stock workflow. */
  var _stockPollTimer  = null;
  /** Completion-poll timer for options workflow. */
  var _optionsPollTimer = null;
  /** Full refresh chain running flag. */
  var _fullRefreshRunning = false;

  /* -- API ref -------------------------------------------------------- */
  var api = window.BenTradeApi;

  /* =================================================================
   *  SHARED HELPERS
   * ================================================================= */

  function esc(s) {
    if (s == null) return '';
    var d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function fmtPct(v) {
    if (v == null) return '--';
    return (v * 100).toFixed(1) + '%';
  }

  function fmtDollar(v) {
    if (v == null) return '--';
    return '$' + Number(v).toFixed(2);
  }

  function fmtDate(iso) {
    if (!iso) return '--';
    try { return new Date(iso).toLocaleString(); } catch (_) { return iso; }
  }

  /* -- Status vocabulary --------------------------------------------- */

  /**
   * TMC status vocabulary -- single source of truth for UI mapping.
   * Maps TMCStatus string to { css, label, isError, isEmpty }.
   */
  var TMC_STATUS_MAP = {
    completed:   { css: 'tmc-run-completed',  label: 'COMPLETED',   isError: false, isEmpty: false },
    degraded:    { css: 'tmc-run-degraded',   label: 'DEGRADED',    isError: false, isEmpty: false },
    failed:      { css: 'tmc-run-failed',      label: 'FAILED',      isError: true,  isEmpty: false },
    no_output:   { css: 'tmc-run-no-output',   label: 'NO OUTPUT',   isError: false, isEmpty: true  },
    unavailable: { css: 'tmc-run-unavailable', label: 'UNAVAILABLE', isError: true,  isEmpty: true  },
  };

  /**
   * Batch-level status vocabulary — used by the section-header badge
   * to distinguish complete vs partial pipeline runs.
   */
  var BATCH_STATUS_MAP = {
    completed: { css: 'tmc-batch-completed', label: '' },
    partial:   { css: 'tmc-batch-partial',   label: 'PARTIAL' },
  };

  function getStatusInfo(status) {
    return TMC_STATUS_MAP[status] || { css: 'tmc-run-unknown', label: (status || 'UNKNOWN').toUpperCase(), isError: false, isEmpty: true };
  }

  /** Update a status badge element with consistent styling. */
  function updateStatusBadge(el, status) {
    if (!el) return;
    var info = getStatusInfo(status);
    el.textContent = info.label;
    el.className = 'tmc-run-status ' + info.css;
  }

  /** Update a batch-status badge element. Shows nothing for "completed". */
  function updateBatchStatusBadge(el, batchStatus) {
    if (!el) return;
    var info = BATCH_STATUS_MAP[batchStatus] || { css: '', label: '' };
    el.textContent = info.label;
    el.className = 'tmc-batch-status ' + info.css;
  }

  /** Update the freshness timestamp element with "Last updated X ago". */
  function updateFreshness(el, generatedAt) {
    if (!el) return;
    if (!generatedAt) { el.textContent = ''; return; }
    try {
      var ts = new Date(generatedAt);
      var diffMs = Date.now() - ts.getTime();
      var label;
      if (diffMs < 60000) {
        label = 'just now';
      } else if (diffMs < 3600000) {
        var mins = Math.floor(diffMs / 60000);
        label = mins + ' min ago';
      } else if (diffMs < 86400000) {
        var hrs = Math.floor(diffMs / 3600000);
        label = hrs + 'h ago';
      } else {
        var days = Math.floor(diffMs / 86400000);
        label = days + 'd ago';
      }
      el.textContent = 'Updated ' + label;
      el.title = ts.toLocaleString();
    } catch (_) {
      el.textContent = '';
    }
  }

  function actionClass(action) {
    switch ((action || '').toLowerCase()) {
      case 'buy':  return 'tmc-action-buy';
      case 'hold': return 'tmc-action-hold';
      case 'pass': return 'tmc-action-pass';
      default:     return 'tmc-action-unknown';
    }
  }

  /* -- DOM builders -------------------------------------------------- */

  function buildMetric(label, value) {
    return '<div class="tmc-metric"><span class="tmc-metric-label">' +
      esc(label) + '</span><span class="tmc-metric-value">' +
      esc(value) + '</span></div>';
  }

  function buildListSection(items, title, cls) {
    if (!items || items.length === 0) return '';
    var html = '<div class="' + cls + '"><div class="tmc-points-label">' +
      esc(title) + '</div><ul class="tmc-points-list">';
    items.forEach(function (item) { html += '<li>' + esc(item) + '</li>'; });
    html += '</ul></div>';
    return html;
  }

  function showEmptyGrid(grid, countEl, msg) {
    if (grid) {
      grid.innerHTML =
        '<div class="tmc-empty-state">' +
          '<div class="tmc-empty-icon">&#9678;</div>' +
          '<div class="tmc-empty-text">' + esc(msg) + '</div>' +
        '</div>';
    }
    if (countEl) countEl.textContent = '0';
  }

  /* -- Unified workflow response handler ----------------------------- */

  /**
   * Handles a TMC workflow response envelope { status, data }.
   * Returns { ok, status, data, candidates } or calls showEmpty and returns null.
   *
   * @param {object} resp       - Response from /api/tmc/workflows/.../latest
   * @param {Element} grid      - Grid element to clear/populate
   * @param {Element} countEl   - Count badge element
   * @param {Element} qualEl    - Quality badge element
   * @param {Element} statusEl  - Status badge element
   * @param {string}  label     - "stock" or "options" for messages
   * @returns {object|null}     - { status, data, candidates } or null
   */
  function handleWorkflowResponse(resp, grid, countEl, qualEl, statusEl, label) {
    var info = getStatusInfo(resp.status);
    updateStatusBadge(statusEl, resp.status);

    // Failed / unavailable
    if (info.isError) {
      showEmptyGrid(grid, countEl, 'Workflow ' + label + ': ' + info.label.toLowerCase());
      if (qualEl) qualEl.textContent = '';
      return null;
    }

    // No output yet
    if (info.isEmpty || !resp.data) {
      showEmptyGrid(grid, countEl, 'No ' + label + ' opportunities available yet');
      if (qualEl) qualEl.textContent = '';
      return null;
    }

    var data = resp.data;
    if (qualEl) qualEl.textContent = data.quality_level || '';

    var candidates = data.candidates || [];
    if (countEl) countEl.textContent = String(candidates.length);

    if (candidates.length === 0) {
      showEmptyGrid(grid, countEl, 'No ' + label + ' candidates found');
      return null;
    }

    return { status: resp.status, data: data, candidates: candidates };
  }

  /* =================================================================
   *  NORMALIZATION LAYER
   *
   *  Small mapping helpers that absorb field-name variation between
   *  backend compact read models and the card builders.  Prevents
   *  brittle direct coupling to exact backend field names.
   * ================================================================= */

  /**
   * Normalize a raw stock candidate from the compact read model.
   *
   * Input fields (from compact stock candidate in output.json — Prompt 12C):
   *   symbol, scanner_key, scanner_name, setup_type, direction,
   *   source_scanners (list[str]),
   *   setup_quality (0-100), confidence (0-1), rank,
   *   thesis_summary (list[str]), supporting_signals (list[str]),
   *   risk_flags (list[str]), entry_context, market_regime,
   *   risk_environment, market_state_ref, vix, regime_tags, support_state,
   *   market_picture_summary { engines_available, engines_total, engine_summaries },
   *   top_metrics, review_summary,
   *   model_recommendation, model_confidence, model_score,
   *   model_review_summary, model_key_factors (list[str]),
   *   model_caution_notes (list[str])
   */
  function normalizeStockCandidate(raw) {
    // Derive action badge from direction field.
    var dir = (raw.direction || '').toLowerCase();
    var action = dir === 'long' ? 'buy' : dir === 'short' ? 'sell' : dir || null;

    return {
      symbol:          raw.symbol || null,
      action:          action,
      setupQuality:    raw.setup_quality != null ? raw.setup_quality : null,
      confidence:      raw.confidence != null ? raw.confidence : null,
      rank:            raw.rank != null ? raw.rank : null,
      rationale:       raw.review_summary || null,
      thesis:          Array.isArray(raw.thesis_summary) ? raw.thesis_summary : [],
      points:          Array.isArray(raw.supporting_signals) ? raw.supporting_signals : [],
      risks:           Array.isArray(raw.risk_flags) ? raw.risk_flags : [],
      scannerName:     raw.scanner_name || raw.scanner_key || null,
      setupType:       raw.setup_type || null,
      topMetrics:      raw.top_metrics || {},
      marketRegime:    raw.market_regime || null,
      riskEnvironment: raw.risk_environment || null,
      // Multi-scanner provenance (12C)
      sourceScanners:  Array.isArray(raw.source_scanners) ? raw.source_scanners : [],
      // Market Picture summary (12C)
      marketPictureSummary: raw.market_picture_summary || null,
      // Market state context (12C)
      marketStateRef:  raw.market_state_ref || null,
      vix:             raw.vix != null ? raw.vix : null,
      regimeTags:      Array.isArray(raw.regime_tags) ? raw.regime_tags : [],
      supportState:    raw.support_state || null,
      // Model review (12C)
      modelRecommendation: raw.model_recommendation || null,
      modelConfidence:     raw.model_confidence != null ? raw.model_confidence : null,
      modelScore:          raw.model_score != null ? raw.model_score : null,
      modelReviewSummary:  raw.model_review_summary || null,
      modelKeyFactors:     Array.isArray(raw.model_key_factors) ? raw.model_key_factors : [],
      modelCautionNotes:   Array.isArray(raw.model_caution_notes) ? raw.model_caution_notes : [],
    };
  }

  /**
   * Normalize a raw options candidate from the compact read model.
   *
   * Input fields (from OptionsOpportunityReadModel.candidates[*]):
   *   underlying | symbol, strategy_id | strategy_type | family,
   *   math.ev, math.pop, math.max_loss, math.net_credit | math.net_debit,
   *   dte, math.width, legs[], math.max_profit, math.ror, math.pop_source
   */
  function normalizeOptionsCandidate(raw) {
    var m = raw.math || {};
    var credit = m.net_credit != null ? Number(m.net_credit) : null;
    var debit  = m.net_debit  != null ? Number(m.net_debit)  : null;
    // Show credit for credit strategies, debit for debit strategies
    var premium = credit != null && credit > 0 ? credit : debit;
    var premiumLabel = credit != null && credit > 0 ? 'credit' : 'debit';
    return {
      symbol:       raw.underlying || raw.symbol || null,
      strategy:     raw.strategy_id || raw.strategy_type || raw.family_key || null,
      strategyId:   raw.strategy_id || null,
      family:       raw.family_key || null,
      ev:           m.ev != null ? Number(m.ev) : null,
      pop:          m.pop != null ? Number(m.pop) : null,
      popSource:    m.pop_source || null,
      maxLoss:      m.max_loss != null ? Number(m.max_loss) : null,
      maxProfit:    m.max_profit != null ? Number(m.max_profit) : null,
      credit:       credit,
      debit:        debit,
      premium:      premium,
      premiumLabel: premiumLabel,
      dte:          raw.dte != null ? raw.dte : null,
      width:        m.width != null ? Number(m.width) : null,
      ror:          m.ror != null ? Number(m.ror) : null,
      evPerDay:     m.ev_per_day != null ? Number(m.ev_per_day) : null,
      breakeven:    m.breakeven || [],
      legs:         Array.isArray(raw.legs) ? raw.legs : [],
      rank:         raw.rank || null,
      expiration:   raw.expiration || null,
      underlyingPrice: raw.underlying_price || null,
      candidateId:  raw.candidate_id || null,
      // Model analysis fields (populated after options model_analysis stage)
      modelRecommendation: raw.model_recommendation || null,
      modelConviction:     raw.model_conviction != null ? raw.model_conviction : null,
      modelScore:          raw.model_score != null ? raw.model_score : null,
      modelHeadline:       raw.model_headline || null,
      modelNarrative:      raw.model_narrative || null,
      modelCautionNotes:   Array.isArray(raw.model_caution_notes) ? raw.model_caution_notes : [],
      modelKeyFactors:     Array.isArray(raw.model_key_factors) ? raw.model_key_factors : [],
      modelDegraded:       !!raw.model_degraded,
      modelStructureAnalysis:      raw.model_structure_analysis || null,
      modelProbabilityAssessment:  raw.model_probability_assessment || null,
      modelGreeksAssessment:       raw.model_greeks_assessment || null,
      modelMarketAlignment:        raw.model_market_alignment || null,
      modelSuggestedAdjustment:    raw.model_suggested_adjustment || null,
      // Preserve raw for action handlers
      _raw: raw,
    };
  }

  /* =================================================================
   *  SECTION 1 -- Stock Opportunities
   *
   *  Uses the standard BenTradeStockTradeCardMapper.renderStockCard()
   *  pipeline so TMC stock cards are identical to every other stock
   *  dashboard in the app.  The TMC compact candidate is converted to
   *  the scanner-like shape that candidateToTradeShape() expects.
   * ================================================================= */

  /** Keep rendered rows for action handler lookups (same as other dashboards). */
  var _stockRenderedRows = [];
  var _stockExpandState  = {};

  function loadStockOpportunities() {
    var grid     = document.getElementById('tmcStockGrid');
    var countEl  = document.getElementById('tmcStockCount');
    var qualEl   = document.getElementById('tmcStockQuality');
    var statusEl = document.getElementById('tmcStockStatus');
    var batchEl  = document.getElementById('tmcStockBatchStatus');
    var freshEl  = document.getElementById('tmcStockFreshness');

    updateStatusBadge(statusEl, null); // shows "loading"
    if (statusEl) statusEl.textContent = 'Loading...';

    api.tmcGetLatestStock()
      .then(function (resp) {
        // Track run_id for freshness detection
        var newRunId = resp && resp.data ? resp.data.run_id : null;
        if (newRunId && newRunId !== _lastStockRunId) {
          console.log('[TMC] Stock data refreshed: run_id=' + newRunId +
            ' generated_at=' + (resp.data.generated_at || '?') +
            ' batch_status=' + (resp.data.batch_status || '?') +
            ' candidates=' + ((resp.data.candidates || []).length));
        }
        _lastStockRunId = newRunId;

        // Update batch status and freshness indicators
        var data = resp.data;
        updateBatchStatusBadge(batchEl, data ? data.batch_status : null);
        updateFreshness(freshEl, data ? data.generated_at : null);

        var result = handleWorkflowResponse(resp, grid, countEl, qualEl, statusEl, 'stock');
        if (!result) return;
        renderStockCandidates(grid, result.candidates, result.data);
      })
      .catch(function (err) {
        console.error('[TMC] Failed to load stock opportunities:', err);
        updateStatusBadge(statusEl, 'failed');
        updateBatchStatusBadge(batchEl, null);
        updateFreshness(freshEl, null);
        showEmptyGrid(grid, countEl, 'Failed to load stock opportunities');
      });
  }

  /**
   * Convert a TMC compact stock candidate into the scanner-row shape
   * that BenTradeStockTradeCardMapper.candidateToTradeShape() expects.
   *
   * The standard pipeline reads: symbol, composite_score, price,
   * strategy_id, plus a flat metrics sub-object. We map from the
   * TMC compact fields.
   */
  function tmcStockToScannerShape(raw) {
    var tm = raw.top_metrics || {};
    return {
      symbol:          raw.symbol || '',
      composite_score: tm.composite_score != null ? tm.composite_score : (raw.setup_quality || null),
      price:           tm.price != null ? tm.price : null,
      rank:            raw.rank,
      trend_state:     tm.trend_state || null,
      thesis:          Array.isArray(raw.thesis_summary) ? raw.thesis_summary : [],
      confidence:      raw.confidence,
      metrics: {
        rsi:           tm.rsi != null ? tm.rsi : null,
        atr_pct:       tm.atr_pct != null ? tm.atr_pct : null,
        composite_score: tm.composite_score != null ? tm.composite_score : null,
        volume_ratio:  tm.volume_ratio != null ? tm.volume_ratio : null,
      },
      // Preserve raw for TMC-specific enrichment injection
      _tmc_raw: raw,
    };
  }

  function renderStockCandidates(grid, candidates, data) {
    if (!grid) return;
    var stockMapper = window.BenTradeStockTradeCardMapper;

    // If the standard mapper is not available, fall back to basic rendering
    if (!stockMapper || !stockMapper.renderStockCard) {
      grid.innerHTML = '';
      _stockRenderedRows = [];
      candidates.forEach(function (raw) {
        grid.appendChild(buildStockCardFallback(normalizeStockCandidate(raw), data));
      });
      return;
    }

    _stockRenderedRows = candidates.slice();
    var html = '';
    candidates.forEach(function (raw, idx) {
      var strategyId = raw.scanner_key || raw.setup_type || 'stock_opportunity';
      var scannerShape = tmcStockToScannerShape(raw);

      try {
        var cardHtml = stockMapper.renderStockCard(scannerShape, idx, strategyId, _stockExpandState);

        // Build TMC enrichment (split into collapsible body + always-visible warnings)
        var enrichment = buildTmcEnrichmentHtml(raw);

        // 1. Inject body content INSIDE the <details> collapsible (before </details>)
        if (enrichment.body) {
          cardHtml = cardHtml.replace(
            '</details>',
            enrichment.body + '</details>'
          );
        }

        // 2. Remove the "Run Model Analysis" button row and model output div from TMC cards
        cardHtml = cardHtml.replace(/<div class="run-row">.*?<\/div>/s, '');
        cardHtml = cardHtml.replace(/<div class="trade-model-output"[^>]*>.*?<\/div>/s, '');

        // 3. Inject warnings (caution, model-not-available) before the action buttons (always visible)
        if (enrichment.warnings) {
          cardHtml = cardHtml.replace(
            '<div class="trade-actions">',
            enrichment.warnings + '<div class="trade-actions">'
          );
        }

        html += cardHtml;
      } catch (cardErr) {
        console.warn('[TMC] Stock card render error for candidate ' + idx, cardErr);
        html += '<div class="trade-card" style="margin-bottom:12px;padding:10px;border:1px solid rgba(255,120,100,0.3);border-radius:10px;background:rgba(8,18,26,0.9);color:rgba(255,180,160,0.8);font-size:12px;">\u26A0 Render error for ' + esc((raw && raw.symbol) || '#' + idx) + '</div>';
      }
    });

    grid.innerHTML = html;

    // ── Wire delegated action handlers (same pattern as all stock dashboards) ──
    grid.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-action]');
      if (!btn) return;
      var action   = btn.dataset.action;
      var tradeKey = btn.dataset.tradeKey || '';
      var symbol   = btn.dataset.symbol || '';
      var row      = _findStockRowByTradeKey(tradeKey);
      var scannerRow = row ? tmcStockToScannerShape(row) : null;
      var strategyId = row ? (row.scanner_key || row.setup_type || 'stock_opportunity') : '';

      if (action === 'model-analysis' && row) {
        // TMC uses dedicated final-decision prompt, NOT the per-strategy one
        runTmcFinalDecision(btn, tradeKey, row, strategyId);
      } else if (action === 'execute' && scannerRow) {
        stockMapper.executeStockTrade(btn, tradeKey, scannerRow, strategyId);
      } else if (action === 'reject' && tradeKey) {
        var cardEl = btn.closest('.trade-card');
        if (cardEl) {
          cardEl.style.opacity = '0.35';
          cardEl.style.pointerEvents = 'none';
        }
      } else if (action === 'data-workbench' && scannerRow) {
        stockMapper.openDataWorkbenchForStock(scannerRow, strategyId);
      } else if (action === 'stock-analysis') {
        stockMapper.openStockAnalysis(symbol || (row && row.symbol));
      } else if (action === 'workbench') {
        console.log('[TMC] Testing Workbench stub for:', tradeKey);
      }
    });

    // Wire expand state persistence
    grid.querySelectorAll('details.trade-card-collapse').forEach(function (details) {
      details.addEventListener('toggle', function () {
        var tk = details.dataset.tradeKey || '';
        if (tk) _stockExpandState[tk] = details.open;
      });
    });

    // Hydrate cached model analysis results
    if (window.BenTradeModelAnalysisStore && window.BenTradeModelAnalysisStore.hydrateContainer) {
      window.BenTradeModelAnalysisStore.hydrateContainer(grid);
    }
  }

  /**
   * Format a value that is ALREADY a 0-100 percentage.
   * Unlike fmtPct() which expects decimals, this just appends '%'.
   */
  function fmtPctDirect(v) {
    if (v == null) return '--';
    return Number(v).toFixed(1) + '%';
  }

  /** Assessment/impact color map for factor rendering. */
  var _assessColors = {
    favorable: '#00dc78', positive: '#00dc78',
    unfavorable: '#ff5a5a', negative: '#ff5a5a',
    concerning: '#ffc83c',
    neutral: '#8899aa',
  };

  /**
   * Build TMC-specific enrichment HTML to inject into the standard card.
   * Returns { body, warnings } where:
   *   - body: goes INSIDE the <details> collapsible (hidden when collapsed)
   *   - warnings: stays OUTSIDE the collapsible (visible when collapsed)
   *
   * Rendering rules:
   *   - If model analysis ran successfully → body gets MODEL REVIEW + tech analysis + factors + engine summary.
   *   - If model analysis is absent → warnings gets "MODEL ANALYSIS NOT AVAILABLE" banner.
   *   - CAUTION notes always go to warnings (visible when collapsed).
   *   - Key factors render as structured cards (factor + impact + evidence).
   *   - Confidence is displayed directly as 0-100% (not re-multiplied).
   */
  function buildTmcEnrichmentHtml(raw) {
    var bodyParts = [];    // inside collapsible
    var warningParts = []; // always visible (between header and buttons)
    var hasModelReview = !!(raw.model_review_summary || raw.model_recommendation);

    // ── Model review section (collapsible body) ──
    if (hasModelReview) {
      var recText = raw.model_recommendation
        ? esc(String(raw.model_recommendation).toUpperCase())
        : '';
      // model_confidence is already 0-100 from backend — do NOT multiply by 100
      var confText = raw.model_confidence != null
        ? 'Conf: ' + fmtPctDirect(raw.model_confidence)
        : '';
      var scoreText = raw.model_score != null
        ? 'Score: ' + Math.round(raw.model_score)
        : '';
      var headerBadges = [recText, confText, scoreText].filter(Boolean).join(' \u00B7 ');

      // Determine recommendation color
      var recColor = '#b4b4c8';
      if (recText === 'BUY' || recText === 'EXECUTE') recColor = '#00dc78';
      else if (recText === 'PASS' || recText === 'REJECT') recColor = '#ff5a5a';

      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid ' + recColor + '33;background:' + recColor + '08;">' +
          '<div class="section-title" style="margin-bottom:6px;">MODEL REVIEW' +
            (headerBadges ? ' \u2014 <span style="color:' + recColor + ';">' + headerBadges + '</span>' : '') +
          '</div>' +
          (raw.model_review_summary
            ? '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(raw.model_review_summary) + '</div>'
            : '') +
        '</div>'
      );
    }

    // ── Technical Analysis (collapsible body) ──
    var ta = raw.model_technical_analysis;
    if (ta && typeof ta === 'object') {
      var taHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(0,220,255,0.03);border-radius:6px;border:1px solid rgba(0,220,255,0.12);">';
      taHtml += '<div class="section-title" style="color:var(--accent-cyan,#00dcff);">TECHNICAL ANALYSIS</div>';
      if (ta.setup_quality_assessment) {
        taHtml += '<div style="font-size:11px;color:var(--text,#d7fbff);line-height:1.5;margin-bottom:6px;">' + esc(ta.setup_quality_assessment) + '</div>';
      }
      if (ta.key_metrics_cited && typeof ta.key_metrics_cited === 'object') {
        var mKeys = Object.keys(ta.key_metrics_cited);
        if (mKeys.length > 0) {
          taHtml += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px;">';
          mKeys.forEach(function (mk) {
            var mv = ta.key_metrics_cited[mk];
            taHtml += '<span style="font-size:10px;padding:2px 6px;background:rgba(255,255,255,0.04);border-radius:3px;border:1px solid rgba(255,255,255,0.08);"><span style="color:var(--muted);">' + esc(mk.replace(/_/g, ' ')) + ':</span> <b style="color:var(--text,#d7fbff);">' + esc(String(mv != null ? mv : '\u2014')) + '</b></span>';
          });
          taHtml += '</div>';
        }
      }
      var rows = [
        { label: 'Trend', val: ta.trend_context, icon: '\u2197' },
        { label: 'Momentum', val: ta.momentum_read, icon: '\u26A1' },
        { label: 'Volatility', val: ta.volatility_read, icon: '\u223C' },
        { label: 'Volume', val: ta.volume_read, icon: '\u25A3' },
      ].filter(function (r) { return !!r.val; });
      rows.forEach(function (r) {
        taHtml += '<div style="font-size:10px;line-height:1.4;padding:2px 0 2px 8px;border-left:2px solid rgba(0,220,255,0.25);margin-bottom:2px;"><span style="color:var(--accent-cyan,#00dcff);font-weight:600;">' + r.icon + ' ' + esc(r.label) + ':</span> <span style="color:var(--text-secondary,#bbb);">' + esc(r.val) + '</span></div>';
      });
      taHtml += '</div>';
      bodyParts.push(taHtml);
    }

    // ── Caution notes (collapsible body) ──
    var cautions = Array.isArray(raw.model_caution_notes) ? raw.model_caution_notes : [];
    if (cautions.length > 0) {
      var cautionLis = cautions.map(function (c) { return '<li style="margin-bottom:2px;">' + esc(c) + '</li>'; }).join('');
      bodyParts.push(
        '<div class="section" style="margin-bottom:6px;padding:6px 10px;border-radius:6px;border:1px solid rgba(244,200,95,0.2);background:rgba(244,200,95,0.04);">' +
          '<div class="section-title" style="color:var(--warn,#f4c85f);">CAUTION</div>' +
          '<ul style="margin:0;padding-left:16px;font-size:11px;line-height:1.5;">' + cautionLis + '</ul>' +
        '</div>'
      );
    }

    // ── Key factors (collapsible body) ──
    var factors = Array.isArray(raw.model_key_factors) ? raw.model_key_factors : [];
    if (factors.length > 0) {
      var factorsHtml = '';
      factors.forEach(function (f) {
        if (typeof f === 'string') {
          factorsHtml += '<div style="font-size:11px;color:var(--text-secondary,#bbb);line-height:1.4;padding:3px 0 3px 8px;border-left:2px solid #8899aa;margin-bottom:3px;">' + esc(f) + '</div>';
        } else if (f && typeof f === 'object') {
          var factorName = f.factor || f.name || '';
          var impact = String(f.impact || f.assessment || 'neutral').toLowerCase();
          var evidence = f.evidence || f.detail || '';
          var impColor = _assessColors[impact] || '#8899aa';
          var impLabel = impact.charAt(0).toUpperCase() + impact.slice(1);

          factorsHtml += '<div style="font-size:11px;line-height:1.4;padding:4px 0 4px 8px;border-left:2px solid ' + impColor + ';margin-bottom:4px;">';
          factorsHtml += '<div style="display:flex;align-items:center;gap:6px;">';
          factorsHtml += '<span style="color:' + impColor + ';font-weight:600;">' + esc(factorName) + '</span>';
          factorsHtml += '<span style="font-size:9px;padding:1px 5px;border-radius:3px;border:1px solid ' + impColor + '44;color:' + impColor + ';text-transform:uppercase;letter-spacing:0.3px;">' + esc(impLabel) + '</span>';
          factorsHtml += '</div>';
          if (evidence) {
            factorsHtml += '<div style="font-size:10px;color:var(--muted,#6a8da8);margin-top:2px;">' + esc(evidence) + '</div>';
          }
          factorsHtml += '</div>';
        }
      });

      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;">' +
          '<div class="section-title">KEY FACTORS</div>' +
          factorsHtml +
        '</div>'
      );
    }

    // ── Engine summary (collapsible body) ──
    if (raw.review_summary) {
      if (!hasModelReview) {
        // Model analysis absent — warning banner (always visible)
        warningParts.unshift(
          '<div style="margin-bottom:6px;padding:5px 10px;font-size:11px;font-weight:600;color:#ff8a5a;background:rgba(255,138,90,0.08);border:1px solid rgba(255,138,90,0.2);border-radius:5px;text-align:center;">' +
            '\u26A0 MODEL ANALYSIS NOT AVAILABLE \u2014 expand for engine output' +
          '</div>'
        );
      }
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:6px 10px;border-radius:6px;border:1px solid rgba(100,149,237,0.12);background:rgba(100,149,237,0.04);">' +
          '<div class="section-title">ENGINE SUMMARY</div>' +
          '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(raw.review_summary) + '</div>' +
        '</div>'
      );
    }

    return { body: bodyParts.join(''), warnings: warningParts.join('') };
  }

  /* ── TMC Final Trade Decision ──────────────────────────────────────
   *
   *  Calls the dedicated TMC final-decision endpoint which gives the
   *  model full trade setup + fresh market picture context and asks
   *  for a portfolio-manager-level decision.
   *
   *  This replaces the per-strategy runModelAnalysisForStock() used
   *  on the other stock dashboards.
   * ────────────────────────────────────────────────────────────────── */

  function runTmcFinalDecision(btn, tradeKey, rawCandidate, strategyId) {
    var modelStore = window.BenTradeModelAnalysisStore;

    if (!api || !api.tmcFinalDecision) {
      console.error('[TMC] BenTradeApi.tmcFinalDecision not available');
      return;
    }

    // Dedupe guard
    if (tradeKey && modelStore) {
      var existing = modelStore.get(tradeKey);
      if (existing && existing.status === 'running') return;
      modelStore.setRunning(tradeKey);
    }

    // Loading state
    var cardEl = btn ? btn.closest('.trade-card') : null;
    var outputEl = cardEl ? cardEl.querySelector('[data-model-output]') : null;

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="home-scan-spinner" aria-hidden="true" style="margin-right:4px;"></span>Analyzing\u2026';
    }
    if (outputEl) {
      outputEl.style.display = 'block';
      outputEl.innerHTML = '<div style="padding:8px;font-size:11px;color:var(--muted);">Running TMC final decision analysis\u2026</div>';
    }

    api.tmcFinalDecision(rawCandidate, strategyId)
      .then(function (result) {
        var analysis = (result && result.analysis) || {};

        // Store for hydration
        if (tradeKey && modelStore) {
          var bridged = {
            status: 'success',
            model_evaluation: {
              model_recommendation: analysis.decision === 'EXECUTE' ? 'BUY' : 'PASS',
              recommendation: analysis.decision || 'PASS',
              score_0_100: analysis.engine_comparison ? analysis.engine_comparison.model_score : null,
              confidence_0_1: analysis.conviction != null ? analysis.conviction / 100 : null,
              thesis: analysis.decision_summary || '',
              key_drivers: (analysis.factors_considered || []).map(function (f) {
                return { factor: f.factor || '', impact: f.assessment || 'neutral', evidence: f.detail || '' };
              }),
              risk_review: {
                primary_risks: analysis.risk_assessment ? (analysis.risk_assessment.primary_risks || []) : [],
                volatility_risk: null,
                timing_risk: null,
                data_quality_flag: null,
              },
            },
          };
          var modelUI = window.BenTradeModelAnalysis;
          var parsed = modelUI ? modelUI.parse(bridged) : bridged;
           // Attach full TMC analysis for rich rendering
          parsed._tmc_analysis = analysis;
          modelStore.setSuccess(tradeKey, parsed);
        }

        // Render
        if (outputEl) {
          outputEl.style.display = 'block';
          outputEl.innerHTML = renderTmcFinalDecisionResult(analysis);
        }

        // Reset button
        if (btn) {
          btn.disabled = false;
          var ts = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          btn.innerHTML = '\u21BB Re-run Analysis <span style="font-size:9px;color:var(--muted);margin-left:4px;">' + ts + '</span>';
        }
      })
      .catch(function (err) {
        var errMsg = (err && err.message) || 'TMC final decision analysis failed';
        console.error('[TMC] final decision error:', err);

        if (tradeKey && modelStore) {
          modelStore.setError(tradeKey, errMsg);
        }
        if (outputEl) {
          outputEl.style.display = 'block';
          outputEl.innerHTML = '<div style="padding:8px;font-size:11px;color:#ff5a5a;">\u26A0 ' + esc(errMsg) + '</div>';
        }
        if (btn) {
          btn.disabled = false;
          btn.textContent = 'Run Model Analysis';
        }
      });
  }

  /**
   * Render TMC final decision analysis into rich HTML.
   *
   * Output contract fields:
   *   decision, conviction, decision_summary, factors_considered,
   *   technical_analysis { setup_quality_assessment, key_metrics_cited,
   *     trend_context, momentum_read, volatility_read, volume_read },
   *   market_alignment, risk_assessment, what_would_change_my_mind,
   *   engine_comparison
   */
  function renderTmcFinalDecisionResult(analysis) {
    if (!analysis) return '';

    // ── Detect fallback / parse failure ──
    if (analysis._fallback) {
      return '<div style="padding:10px 0;">'
        + '<div style="padding:8px 10px;font-size:12px;color:#ff8a5a;background:rgba(255,138,90,0.08);border:1px solid rgba(255,138,90,0.2);border-radius:6px;margin-bottom:8px;">'
        + '\u26A0 <strong>MODEL ANALYSIS FAILED</strong> \u2014 ' + esc(analysis.decision_summary || 'Parse failure')
        + '</div>'
        + (analysis._raw_text_preview
          ? '<details style="margin-bottom:8px;"><summary style="font-size:10px;color:var(--muted);cursor:pointer;">Raw model output (debug)</summary>'
            + '<pre style="font-size:9px;color:var(--muted);white-space:pre-wrap;max-height:150px;overflow:auto;padding:6px;background:rgba(0,0,0,0.3);border-radius:4px;margin-top:4px;">' + esc(analysis._raw_text_preview) + '</pre></details>'
          : '')
        + '</div>';
    }

    var decision = analysis.decision || 'PASS';
    var conviction = analysis.conviction != null ? analysis.conviction : 0;
    var decColor = decision === 'EXECUTE' ? '#00dc78' : '#ff5a5a';
    var convColor = conviction >= 70 ? '#00dc78' : conviction >= 40 ? '#ffc83c' : '#ff5a5a';

    var html = '<div style="padding:10px 0;">';

    // ── Decision Header ──
    html += '<div style="display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:10px;padding:8px 10px;border-radius:6px;border:1px solid ' + decColor + '33;background:' + decColor + '08;">';
    html += '<span style="font-size:14px;font-weight:800;padding:3px 12px;border-radius:4px;border:1px solid ' + decColor + '55;color:' + decColor + ';letter-spacing:1px;text-shadow:0 0 8px ' + decColor + '44;">' + esc(decision) + '</span>';
    html += '<span style="font-size:12px;color:' + convColor + ';font-weight:700;">Conviction: ' + conviction + '%</span>';
    if (analysis.engine_comparison && analysis.engine_comparison.model_score != null) {
      var msColor = analysis.engine_comparison.model_score >= 60 ? '#00dc78' : analysis.engine_comparison.model_score >= 40 ? '#ffc83c' : '#ff5a5a';
      html += '<span style="font-size:12px;color:' + msColor + ';font-weight:700;">Score: ' + Math.round(analysis.engine_comparison.model_score) + '<span style="font-size:10px;color:var(--muted);font-weight:400;">/100</span></span>';
    }
    html += '</div>';

    // ── Decision Summary (structured) ──
    if (analysis.decision_summary) {
      html += '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;margin-bottom:10px;padding:6px 10px;border-radius:5px;border-left:3px solid ' + decColor + ';">' + esc(analysis.decision_summary) + '</div>';
    }

    // ── Technical Analysis (new detailed metrics section) ──
    var ta = analysis.technical_analysis;
    if (ta && typeof ta === 'object') {
      html += '<div style="margin-bottom:10px;padding:8px 10px;background:rgba(0,220,255,0.03);border-radius:6px;border:1px solid rgba(0,220,255,0.12);">';
      html += '<div style="font-size:10px;font-weight:700;color:var(--accent-cyan,#00dcff);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px;">Technical Analysis</div>';

      // Setup Quality Assessment
      if (ta.setup_quality_assessment) {
        html += '<div style="font-size:11px;color:var(--text,#d7fbff);line-height:1.5;margin-bottom:6px;">' + esc(ta.setup_quality_assessment) + '</div>';
      }

      // Key Metrics Cited grid
      var metricsCited = ta.key_metrics_cited;
      if (metricsCited && typeof metricsCited === 'object') {
        var mKeys = Object.keys(metricsCited);
        if (mKeys.length > 0) {
          html += '<div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(130px, 1fr));gap:4px 10px;margin-bottom:6px;">';
          mKeys.forEach(function (mk) {
            var mv = metricsCited[mk];
            var mStr = mv != null ? String(mv) : '\u2014';
            html += '<div style="font-size:10px;padding:3px 6px;background:rgba(255,255,255,0.04);border-radius:3px;border:1px solid rgba(255,255,255,0.06);">';
            html += '<span style="color:var(--muted);text-transform:uppercase;font-size:9px;">' + esc(mk.replace(/_/g, ' ')) + '</span><br>';
            html += '<span style="color:var(--text,#d7fbff);font-weight:600;">' + esc(mStr) + '</span>';
            html += '</div>';
          });
          html += '</div>';
        }
      }

      // Technical context rows (trend, momentum, volatility, volume)
      var techRows = [
        { label: 'Trend', value: ta.trend_context, icon: '\u2197' },
        { label: 'Momentum', value: ta.momentum_read, icon: '\u26A1' },
        { label: 'Volatility', value: ta.volatility_read, icon: '\u223C' },
        { label: 'Volume', value: ta.volume_read, icon: '\u25A3' },
      ].filter(function (r) { return !!r.value; });

      if (techRows.length > 0) {
        techRows.forEach(function (r) {
          html += '<div style="font-size:11px;line-height:1.4;padding:2px 0 2px 8px;border-left:2px solid rgba(0,220,255,0.25);margin-bottom:3px;">';
          html += '<span style="color:var(--accent-cyan,#00dcff);font-weight:600;">' + r.icon + ' ' + esc(r.label) + ':</span> ';
          html += '<span style="color:var(--text-secondary,#bbb);">' + esc(r.value) + '</span>';
          html += '</div>';
        });
      }

      html += '</div>';
    }

    // ── Factors Considered ──
    var factors = analysis.factors_considered || [];
    if (factors.length > 0) {
      html += '<div style="margin-bottom:10px;">';
      html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px;">Factors Considered</div>';

      // Group by category
      var groups = {};
      factors.forEach(function (f) {
        var cat = f.category || 'trade_setup';
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(f);
      });

      var catLabels = {
        trade_setup: 'Trade Setup',
        market_environment: 'Market Environment',
        risk_reward: 'Risk / Reward',
        timing: 'Timing',
        data_quality: 'Data Quality',
      };
      var assessColors = {
        favorable: '#00dc78',
        unfavorable: '#ff5a5a',
        concerning: '#ffc83c',
        neutral: '#8899aa',
      };

      Object.keys(groups).forEach(function (cat) {
        html += '<div style="margin-bottom:8px;">';
        html += '<div style="font-size:9px;font-weight:700;color:#6a8da8;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px;padding-bottom:2px;border-bottom:1px solid rgba(106,141,168,0.15);">' + esc(catLabels[cat] || cat) + '</div>';
        groups[cat].forEach(function (f) {
          var aColor = assessColors[f.assessment] || '#8899aa';
          var aLabel = (f.assessment || 'neutral').charAt(0).toUpperCase() + (f.assessment || 'neutral').slice(1);
          var wBadge = f.weight === 'high' ? '\u25CF' : f.weight === 'low' ? '\u25CB' : '\u25D0';
          html += '<div style="padding:3px 0 3px 8px;border-left:2px solid ' + aColor + ';margin-bottom:3px;">';
          html += '<div style="display:flex;gap:6px;align-items:center;font-size:11px;line-height:1.4;">';
          html += '<span style="color:' + aColor + ';font-size:8px;" title="Weight: ' + esc(f.weight || 'medium') + '">' + wBadge + '</span>';
          html += '<span style="color:var(--text,#d7fbff);font-weight:600;">' + esc(f.factor || '') + '</span>';
          html += '<span style="font-size:9px;padding:1px 4px;border-radius:2px;border:1px solid ' + aColor + '33;color:' + aColor + ';">' + esc(aLabel) + '</span>';
          html += '</div>';
          if (f.detail) {
            html += '<div style="font-size:10px;color:var(--muted);margin-top:1px;padding-left:14px;">' + esc(f.detail) + '</div>';
          }
          html += '</div>';
        });
        html += '</div>';
      });

      html += '</div>';
    }

    // ── Market Alignment ──
    if (analysis.market_alignment) {
      var ma = analysis.market_alignment;
      var maColor = ma.overall === 'aligned' ? '#00dc78' : ma.overall === 'conflicting' ? '#ff5a5a' : '#ffc83c';
      html += '<div style="margin-bottom:10px;padding:8px 10px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.12);">';
      html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px;">Market Alignment</div>';
      html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">';
      html += '<span style="font-size:10px;padding:2px 8px;border-radius:3px;border:1px solid ' + maColor + '44;color:' + maColor + ';font-weight:700;letter-spacing:0.3px;">' + esc(String(ma.overall || 'neutral').toUpperCase()) + '</span>';
      html += '</div>';
      if (ma.detail) {
        html += '<div style="font-size:11px;color:var(--text-secondary,#bbb);line-height:1.5;">' + esc(ma.detail) + '</div>';
      }
      html += '</div>';
    }

    // ── Risk Assessment ──
    if (analysis.risk_assessment) {
      var ra = analysis.risk_assessment;
      var rvColor = ra.risk_reward_verdict === 'favorable' ? '#00dc78' : ra.risk_reward_verdict === 'unfavorable' ? '#ff5a5a' : '#ffc83c';
      html += '<div style="margin-bottom:10px;padding:8px 10px;background:rgba(255,90,90,0.03);border-radius:6px;border:1px solid rgba(255,90,90,0.1);">';
      html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px;">Risk Assessment';
      html += ' <span style="font-size:9px;padding:1px 6px;border-radius:3px;border:1px solid ' + rvColor + '44;color:' + rvColor + ';margin-left:6px;font-weight:700;">' + esc(String(ra.risk_reward_verdict || 'marginal').toUpperCase()) + '</span>';
      html += '</div>';

      if (ra.biggest_concern) {
        html += '<div style="font-size:11px;color:#ffc83c;line-height:1.5;margin-bottom:5px;padding:4px 8px;background:rgba(255,200,60,0.06);border-radius:4px;border-left:3px solid #ffc83c;">\u26A0 <strong>Key concern:</strong> ' + esc(ra.biggest_concern) + '</div>';
      }

      var risks = ra.primary_risks || [];
      if (risks.length > 0) {
        html += '<ul style="margin:0;padding-left:18px;font-size:11px;color:var(--text-secondary,#bbb);line-height:1.5;">';
        risks.forEach(function (r) { html += '<li style="margin-bottom:2px;">' + esc(r) + '</li>'; });
        html += '</ul>';
      }
      html += '</div>';
    }

    // ── Engine Comparison ──
    if (analysis.engine_comparison) {
      var ec = analysis.engine_comparison;
      var agreeColor = ec.agreement === 'agree' ? '#00dc78' : ec.agreement === 'disagree' ? '#ff5a5a' : '#ffc83c';
      html += '<div style="margin-bottom:10px;padding:8px 10px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.12);">';
      html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px;">Engine vs Model</div>';
      html += '<div style="display:flex;gap:16px;align-items:center;font-size:11px;margin-bottom:4px;">';
      if (ec.engine_score != null) {
        var esColor = ec.engine_score >= 60 ? '#00dc78' : ec.engine_score >= 40 ? '#ffc83c' : '#ff5a5a';
        html += '<span style="color:var(--text-secondary,#bbb);">Engine: <b style="color:' + esColor + ';">' + Math.round(ec.engine_score) + '</b></span>';
      }
      if (ec.model_score != null) {
        var ms2Color = ec.model_score >= 60 ? '#00dc78' : ec.model_score >= 40 ? '#ffc83c' : '#ff5a5a';
        html += '<span style="color:var(--text-secondary,#bbb);">Model: <b style="color:' + ms2Color + ';">' + Math.round(ec.model_score) + '</b></span>';
      }
      html += '<span style="font-size:10px;padding:2px 8px;border-radius:3px;border:1px solid ' + agreeColor + '44;color:' + agreeColor + ';font-weight:700;">' + esc(String(ec.agreement || 'partial').toUpperCase()) + '</span>';
      html += '</div>';
      if (ec.reasoning) {
        html += '<div style="font-size:10px;color:var(--text-secondary,#bbb);line-height:1.5;padding-left:8px;border-left:2px solid ' + agreeColor + ';">' + esc(ec.reasoning) + '</div>';
      }
      html += '</div>';
    }

    // ── What Would Change My Mind ──
    if (analysis.what_would_change_my_mind) {
      html += '<div style="margin-bottom:8px;padding:6px 10px;border-radius:6px;border:1px solid rgba(255,255,255,0.06);background:rgba(255,255,255,0.02);">';
      html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:3px;">\u21BB What Would Change My Mind</div>';
      html += '<div style="font-size:11px;color:var(--text-secondary,#bbb);line-height:1.5;font-style:italic;">' + esc(analysis.what_would_change_my_mind) + '</div>';
      html += '</div>';
    }

    // ── Fallback/Parse info (debug) ──
    if (analysis._parse_method && analysis._parse_method !== 'direct') {
      html += '<div style="font-size:9px;color:var(--muted);opacity:0.6;padding-top:4px;border-top:1px solid rgba(255,255,255,0.06);">Parse method: ' + esc(analysis._parse_method) + '</div>';
    }

    html += '</div>';
    return html;
  }

  /** Find a raw TMC candidate by trade key for action handlers. */
  function _findStockRowByTradeKey(tradeKey) {
    if (!tradeKey) return null;
    var stockMapper = window.BenTradeStockTradeCardMapper;
    for (var i = 0; i < _stockRenderedRows.length; i++) {
      var row = _stockRenderedRows[i];
      var strategyId = row.scanner_key || row.setup_type || 'stock_opportunity';
      var rk = stockMapper
        ? stockMapper.buildStockTradeKey(row.symbol, strategyId)
        : '';
      if (rk === tradeKey) return row;
    }
    return null;
  }

  /**
   * Fallback card builder when BenTradeStockTradeCardMapper is unavailable.
   * Produces a minimal readable card — should never appear in practice.
   */
  function buildStockCardFallback(c, data) {
    var card = document.createElement('div');
    card.className = 'tmc-card tmc-stock-card';
    var symbol = c.symbol || '???';
    var action = c.action || '--';
    card.innerHTML =
      '<div class="tmc-card-header">' +
        '<div class="tmc-card-symbol">' + esc(symbol) + '</div>' +
        '<div class="tmc-card-action ' + actionClass(action) + '">' + esc(String(action).toUpperCase()) + '</div>' +
      '</div>' +
      (c.rationale ? '<div class="tmc-rationale"><div class="tmc-rationale-text">' + esc(c.rationale) + '</div></div>' : '') +
      '<div class="tmc-card-footer"><span class="tmc-scanner-badge">' + esc(c.scannerName || '--') + '</span></div>';
    return card;
  }

  /**
   * Start a completion-poll that checks /stock/latest every interval
   * until the run_id changes from the baseline, or maxAttempts is reached.
   *
   * @param {string|null} baselineRunId - run_id before the trigger
   * @param {number} intervalMs - poll interval (default 15000)
   * @param {number} maxAttempts - max polls (default 20 = ~5 min)
   */
  function _startStockCompletionPoll(baselineRunId, intervalMs, maxAttempts) {
    _stopStockCompletionPoll();
    var attempts = 0;
    intervalMs = intervalMs || 15000;
    maxAttempts = maxAttempts || 20;

    console.log('[TMC] Starting stock completion poll (baseline run_id=' +
      (baselineRunId || 'none') + ', interval=' + intervalMs + 'ms, max=' + maxAttempts + ')');

    _stockPollTimer = setInterval(function () {
      attempts++;
      if (attempts > maxAttempts) {
        console.log('[TMC] Stock completion poll exhausted (' + maxAttempts + ' attempts)');
        _stopStockCompletionPoll();
        return;
      }

      api.tmcGetLatestStock()
        .then(function (resp) {
          var newRunId = resp && resp.data ? resp.data.run_id : null;
          if (newRunId && newRunId !== baselineRunId) {
            console.log('[TMC] Stock completion poll detected new run: ' + newRunId);
            _stopStockCompletionPoll();
            // Full reload with rendering
            loadStockOpportunities();
          }
        })
        .catch(function () {
          // Ignore poll errors — will retry on next interval
        });
    }, intervalMs);
  }

  function _stopStockCompletionPoll() {
    if (_stockPollTimer) {
      clearInterval(_stockPollTimer);
      _stockPollTimer = null;
    }
  }

  function triggerStockRun() {
    var statusEl = document.getElementById('tmcStockStatus');
    if (statusEl) { statusEl.textContent = 'Running...'; statusEl.className = 'tmc-run-status'; }

    var baselineRunId = _lastStockRunId;
    console.log('[TMC] Triggering stock workflow (baseline run_id=' + (baselineRunId || 'none') + ')');

    api.tmcRunStock()
      .then(function (result) {
        console.log('[TMC] Stock workflow trigger returned: status=' + result.status +
          ' run_id=' + (result.run_id || '?') + ' candidates=' + (result.candidate_count || 0));
        updateStatusBadge(statusEl, result.status);
        _stopStockCompletionPoll();
        loadStockOpportunities();
      })
      .catch(function (err) {
        console.error('[TMC] Stock workflow trigger failed:', err);
        updateStatusBadge(statusEl, 'failed');
        // The workflow may still be running in the background (shielded
        // from HTTP disconnect on the backend).  Start polling to detect
        // when it completes and refresh automatically.
        _startStockCompletionPoll(baselineRunId);
        // Also try an immediate load — the trigger may have failed after
        // the workflow already finished and wrote output.json.
        loadStockOpportunities();
      });
  }

  /* =================================================================
   *  SECTION 2 -- Options Opportunities
   * ================================================================= */

  function loadOptionsOpportunities() {
    var grid     = document.getElementById('tmcOptionsGrid');
    var countEl  = document.getElementById('tmcOptionsCount');
    var qualEl   = document.getElementById('tmcOptionsQuality');
    var statusEl = document.getElementById('tmcOptionsStatus');
    var batchEl  = document.getElementById('tmcOptionsBatchStatus');
    var freshEl  = document.getElementById('tmcOptionsFreshness');

    updateStatusBadge(statusEl, null);
    if (statusEl) statusEl.textContent = 'Loading...';

    api.tmcGetLatestOptions()
      .then(function (resp) {
        var newRunId = resp && resp.data ? resp.data.run_id : null;
        if (newRunId && newRunId !== _lastOptionsRunId) {
          console.log('[TMC] Options data refreshed: run_id=' + newRunId +
            ' batch_status=' + (resp.data.batch_status || '?') +
            ' candidates=' + ((resp.data.candidates || []).length));
        }
        _lastOptionsRunId = newRunId;

        // Update batch status and freshness indicators
        var data = resp.data;
        updateBatchStatusBadge(batchEl, data ? data.batch_status : null);
        updateFreshness(freshEl, data ? data.generated_at : null);

        var result = handleWorkflowResponse(resp, grid, countEl, qualEl, statusEl, 'options');
        if (!result) return;
        renderOptionsCandidates(grid, result.candidates, result.data);
      })
      .catch(function (err) {
        console.error('[TMC] Failed to load options opportunities:', err);
        updateStatusBadge(statusEl, 'failed');
        updateBatchStatusBadge(batchEl, null);
        updateFreshness(freshEl, null);
        showEmptyGrid(grid, countEl, 'Failed to load options opportunities');
      });
  }

  /** Keep rendered options rows for action handler lookups. */
  var _optionsRenderedRows = [];
  var _optionsExpandState  = {};

  function renderOptionsCandidates(grid, candidates, data) {
    if (!grid) return;
    var tc = window.BenTradeTradeCard;

    _optionsRenderedRows = candidates.slice();
    var html = '';
    candidates.forEach(function (raw, idx) {
      var c = normalizeOptionsCandidate(raw);
      try {
        html += buildOptionsTradeCard(c, idx, data);
      } catch (cardErr) {
        console.warn('[TMC] Options card render error for candidate ' + idx, cardErr);
        html += '<div class="trade-card" style="margin-bottom:12px;padding:10px;border:1px solid rgba(255,120,100,0.3);border-radius:10px;background:rgba(8,18,26,0.9);color:rgba(255,180,160,0.8);font-size:12px;">\u26A0 Render error for ' + esc((raw && raw.symbol) || '#' + idx) + '</div>';
      }
    });

    grid.innerHTML = html;

    // ── Wire delegated action handlers ──────────────────────────
    grid.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-action]');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      var action   = btn.dataset.action;
      var tradeKey = btn.dataset.tradeKey || '';
      var row      = _findOptionsRowByTradeKey(tradeKey);

      if (action === 'execute' && row) {
        _executeOptionsTrade(btn, tradeKey, row);
      } else if (action === 'reject' && tradeKey) {
        var cardEl = btn.closest('.trade-card');
        if (cardEl) {
          cardEl.style.opacity = '0.35';
          cardEl.style.pointerEvents = 'none';
        }
      } else if (action === 'data-workbench' && row) {
        window.BenTradeTradeCard.openDataWorkbenchByTrade(row);
      }
    });

    // Wire expand state persistence
    grid.querySelectorAll('details.trade-card-collapse').forEach(function (details) {
      details.addEventListener('toggle', function () {
        var tk = details.dataset.tradeKey || '';
        if (tk) _optionsExpandState[tk] = details.open;
      });
    });
  }

  /**
   * Build a TradeCard trade key for an options candidate.
   * Format: SYMBOL|OPTIONS|strategy_id|short_strike|long_strike|dte
   */
  function _buildOptionsTradeKey(c) {
    var sym = String(c.symbol || '').toUpperCase();
    var sid = String(c.strategyId || c.strategy || '');
    var shorts = '', longs = '';
    if (c.legs.length >= 1) {
      var sortedLegs = c.legs.slice().sort(function (a, b) { return (a.strike || 0) - (b.strike || 0); });
      var shortLegs = sortedLegs.filter(function (l) { return (l.side || '').toUpperCase() === 'SHORT'; });
      var longLegs  = sortedLegs.filter(function (l) { return (l.side || '').toUpperCase() === 'LONG'; });
      shorts = shortLegs.map(function (l) { return l.strike; }).join(',');
      longs  = longLegs.map(function (l) { return l.strike; }).join(',');
    }
    return sym + '|OPTIONS|' + sid + '|' + (shorts || 'NA') + '|' + (longs || 'NA') + '|' + (c.dte != null ? c.dte : 'NA');
  }

  function _findOptionsRowByTradeKey(tradeKey) {
    if (!tradeKey) return null;
    for (var i = 0; i < _optionsRenderedRows.length; i++) {
      var r = _optionsRenderedRows[i];
      var c = normalizeOptionsCandidate(r);
      if (_buildOptionsTradeKey(c) === tradeKey) return r;
    }
    return null;
  }

  /**
   * Build a single options candidate as a full TradeCard.
   * Uses the same HTML structure as stock TradeCards: <details> collapse,
   * header summary, expandable body sections, always-visible action footer.
   */
  function buildOptionsTradeCard(c, idx, data) {
    var tc = window.BenTradeTradeCard;
    var symbol = c.symbol || '???';
    var strategyLabel = c.strategy ? c.strategy.replace(/_/g, ' ').replace(/\b\w/g, function (ch) { return ch.toUpperCase(); }) : '--';
    var tradeKey = _buildOptionsTradeKey(c);

    // ── Score badge (model_score preferred, fallback to rank) ──
    var scoreVal = c.modelScore != null ? c.modelScore : null;
    var scoreBadge = '';
    if (scoreVal !== null) {
      scoreBadge = '<span class="trade-rank-badge" style="font-size:14px;font-weight:700;color:var(--accent-cyan);background:rgba(0,220,255,0.08);border:1px solid rgba(0,220,255,0.24);border-radius:8px;padding:3px 10px;white-space:nowrap;">Score ' + Math.round(scoreVal) + '</span>';
    } else if (c.rank) {
      scoreBadge = '<span class="trade-rank-badge" style="font-size:14px;font-weight:700;color:var(--accent-cyan);background:rgba(0,220,255,0.08);border:1px solid rgba(0,220,255,0.24);border-radius:8px;padding:3px 10px;white-space:nowrap;">#' + c.rank + '</span>';
    }

    // ── Header badges ──
    var symbolBadge = tc ? tc.pill(symbol) : '<span class="qtPill">' + esc(symbol) + '</span>';
    var dteBadge = c.dte !== null ? (tc ? tc.pill(c.dte + ' DTE') : '<span class="qtPill">' + c.dte + ' DTE</span>') : '';

    // ── Subtitle: strikes, expiration, premium ──
    var subtitleParts = [];
    if (c.legs.length >= 2) {
      var strikes = c.legs.map(function (l) { return l.strike; }).filter(function (s) { return s != null; });
      var optType = (c.legs[0].option_type || '').toUpperCase();
      subtitleParts.push(strikes.join(' / ') + ' ' + optType);
    }
    if (c.expiration) {
      subtitleParts.push(c.expiration);
    }
    if (c.premium != null) {
      subtitleParts.push(c.premiumLabel.charAt(0).toUpperCase() + c.premiumLabel.slice(1) + ': $' + Number(c.premium).toFixed(2));
    }
    var subtitleText = subtitleParts.join(' \u00B7 ');

    var tradeKeyDisplay = tradeKey
      ? '<span class="trade-key-wrap"><span class="trade-key-label">' + esc(tradeKey) + '</span>'
        + (tc ? tc.copyTradeKeyButton(tradeKey) : '') + '</span>'
      : '';

    // ── Core metrics section (expanded body) ──
    var coreItems = [
      { label: 'EV', value: fmtDollar(c.ev), cssClass: c.ev > 0 ? 'positive' : (c.ev < 0 ? 'negative' : 'neutral') },
      { label: 'POP', value: fmtPct(c.pop), cssClass: c.pop != null && c.pop >= 0.65 ? 'positive' : (c.pop != null ? 'negative' : 'neutral') },
      { label: 'RoR', value: c.ror != null ? (c.ror * 100).toFixed(0) + '%' : '--', cssClass: c.ror != null && c.ror > 0.15 ? 'positive' : 'neutral' },
      { label: 'Max Profit', value: fmtDollar(c.maxProfit), cssClass: 'positive' },
      { label: 'Max Loss', value: c.maxLoss != null ? fmtDollar(Math.abs(c.maxLoss)) : '--', cssClass: 'negative' },
      { label: 'Width', value: c.width != null ? '$' + c.width.toFixed(0) : '--', cssClass: 'neutral' },
      { label: 'EV/Day', value: fmtDollar(c.evPerDay), cssClass: c.evPerDay != null && c.evPerDay > 0 ? 'positive' : 'neutral' },
      { label: 'DTE', value: c.dte != null ? c.dte + 'd' : '--', cssClass: 'neutral' },
    ];
    var coreGridHtml = '<div class="metric-grid">' + coreItems.map(function (item) {
      return '<div class="metric"><div class="metric-label">' + esc(item.label) + '</div><div class="metric-value ' + item.cssClass + '">' + item.value + '</div></div>';
    }).join('') + '</div>';
    var coreSection = '<div class="section section-core"><div class="section-title">CORE METRICS</div>' + coreGridHtml + '</div>';

    // ── Legs detail section (expanded body) ──
    var legsSection = '';
    if (c.legs.length > 0) {
      var legsRows = '';
      c.legs.forEach(function (leg) {
        var side = (leg.side || '').toUpperCase();
        var sideClass = side === 'SHORT' ? 'tmc-leg-short' : 'tmc-leg-long';
        var strike = leg.strike != null ? String(leg.strike) : '?';
        var type = (leg.option_type || '').toUpperCase();
        var bidAsk = '';
        if (leg.bid != null && leg.ask != null) {
          bidAsk = Number(leg.bid).toFixed(2) + ' / ' + Number(leg.ask).toFixed(2);
        }
        var delta = leg.delta != null ? '\u0394 ' + Number(leg.delta).toFixed(2) : '';
        legsRows +=
          '<div class="tmc-options-leg-row">' +
            '<span class="tmc-leg-side ' + sideClass + '">' + esc(side) + '</span>' +
            '<span class="tmc-leg-strike">' + esc(strike) + ' ' + esc(type) + '</span>' +
            '<span class="tmc-leg-pricing">' + esc(bidAsk) + '</span>' +
            '<span class="tmc-leg-delta">' + esc(delta) + '</span>' +
          '</div>';
      });
      legsSection = '<div class="section"><div class="section-title">LEG DETAILS</div><div class="tmc-options-legs">' + legsRows + '</div></div>';
    }

    // ── Build enrichment sections (model review, structure, etc.) ──
    var enrichment = buildOptionsEnrichmentHtml(c);

    // ── Collapse state ──
    var isExpanded = tradeKey ? (_optionsExpandState[tradeKey] === true) : false;
    var openAttr = isExpanded ? ' open' : '';

    // ── Chevron SVG ──
    var chevronSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>';

    // ── Action buttons (always visible) ──
    var tradeKeyAttr = ' data-trade-key="' + esc(tradeKey) + '"';
    var actionsHtml = enrichment.warnings
      + '<div class="trade-actions">'
      + '<div class="actions-row">'
      + '<button type="button" class="btn btn-exec btn-action" data-action="execute"' + tradeKeyAttr + ' title="Preview and execute this options trade">Execute Trade</button>'
      + '<button type="button" class="btn btn-reject btn-action" data-action="reject"' + tradeKeyAttr + ' title="Reject this trade">Reject</button>'
      + '</div>'
      + '<div class="actions-row">'
      + '<button type="button" class="btn btn-action" data-action="data-workbench"' + tradeKeyAttr + ' title="Send to Data Workbench">Send to Data Workbench</button>'
      + '</div>'
      + '</div>';

    // ── Full card HTML ──
    return '<div class="trade-card" data-idx="' + idx + '"' + tradeKeyAttr + ' style="margin-bottom:14px;display:flex;flex-direction:column;">'
      + '<details class="trade-card-collapse"' + tradeKeyAttr + openAttr + '>'
      + '<summary class="trade-summary"><div class="trade-header trade-header-click">'
      + '<div class="trade-header-left"><span class="chev">' + chevronSvg + '</span></div>'
      + '<div class="trade-header-center">'
      + '<div class="trade-type" style="display:flex;align-items:center;gap:8px;justify-content:center;">' + symbolBadge + ' ' + dteBadge + ' ' + esc(strategyLabel) + '</div>'
      + '<div class="trade-subtitle">' + subtitleText + '</div>'
      + (tradeKeyDisplay ? '<div style="text-align:center;">' + tradeKeyDisplay + '</div>' : '')
      + '</div>'
      + '<div class="trade-header-right">' + scoreBadge + '</div>'
      + '</div></summary>'
      + '<div class="trade-body" style="flex:1 1 auto;">'
      + coreSection
      + legsSection
      + enrichment.body
      + '</div>'
      + '</details>'
      + actionsHtml
      + '</div>';
  }

  /**
   * Build options-specific enrichment HTML (expanded body sections).
   * Returns { body, warnings } matching the stock enrichment pattern.
   */
  function buildOptionsEnrichmentHtml(c) {
    var bodyParts = [];
    var warningParts = [];
    var hasModel = !!(c.modelRecommendation && !c.modelDegraded);

    // ── MODEL REVIEW section ──
    if (hasModel) {
      var recText = String(c.modelRecommendation).toUpperCase();
      var confText = c.modelConviction != null ? 'Conf: ' + fmtPctDirect(c.modelConviction) : '';
      var scoreText = c.modelScore != null ? 'Score: ' + Math.round(c.modelScore) : '';
      var headerBadges = [recText, confText, scoreText].filter(Boolean).join(' \u00B7 ');

      var recColor = '#b4b4c8';
      if (recText === 'EXECUTE') recColor = '#00dc78';
      else if (recText === 'PASS') recColor = '#ff5a5a';

      var modelBody = '';
      if (c.modelHeadline) {
        modelBody += '<div style="font-size:13px;font-weight:700;color:var(--text,#d7fbff);margin-bottom:4px;">' + esc(c.modelHeadline) + '</div>';
      }
      if (c.modelNarrative) {
        modelBody += '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(c.modelNarrative) + '</div>';
      }

      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid ' + recColor + '33;background:' + recColor + '08;">'
        + '<div class="section-title" style="margin-bottom:6px;">MODEL REVIEW'
        + (headerBadges ? ' \u2014 <span style="color:' + recColor + ';">' + headerBadges + '</span>' : '')
        + '</div>'
        + modelBody
        + '</div>'
      );
    } else if (c.modelDegraded) {
      warningParts.push(
        '<div style="margin-bottom:6px;padding:5px 10px;font-size:11px;font-weight:600;color:#ff8a5a;background:rgba(255,138,90,0.08);border:1px solid rgba(255,138,90,0.2);border-radius:5px;text-align:center;">'
        + '\u26A0 Model analysis unavailable \u2014 ranked by scanner EV only'
        + '</div>'
      );
    } else if (!c.modelRecommendation) {
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid rgba(138,138,180,0.2);background:rgba(138,138,180,0.04);">'
        + '<div class="section-title" style="color:#8a8ab4;">MODEL REVIEW</div>'
        + '<div style="font-size:12px;color:var(--muted);">Model analysis unavailable</div>'
        + '</div>'
      );
    }

    // ── STRUCTURE ANALYSIS section ──
    var sa = c.modelStructureAnalysis;
    if (sa && typeof sa === 'object') {
      var saRows = [
        { label: 'Strategy', val: sa.strategy_assessment },
        { label: 'Strike Placement', val: sa.strike_placement },
        { label: 'Width', val: sa.width_assessment },
        { label: 'DTE', val: sa.dte_assessment },
      ].filter(function (r) { return !!r.val; });

      if (saRows.length > 0) {
        var saHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(0,220,255,0.03);border-radius:6px;border:1px solid rgba(0,220,255,0.12);">';
        saHtml += '<div class="section-title" style="color:var(--accent-cyan,#00dcff);">STRUCTURE ANALYSIS</div>';
        saRows.forEach(function (r) {
          saHtml += '<div style="font-size:11px;line-height:1.4;padding:2px 0 2px 8px;border-left:2px solid rgba(0,220,255,0.25);margin-bottom:3px;">'
            + '<span style="color:var(--accent-cyan,#00dcff);font-weight:600;">' + esc(r.label) + ':</span> '
            + '<span style="color:var(--text-secondary,#bbb);">' + esc(r.val) + '</span></div>';
        });
        saHtml += '</div>';
        bodyParts.push(saHtml);
      }
    }

    // ── PROBABILITY ASSESSMENT section ──
    var pa = c.modelProbabilityAssessment;
    if (pa && typeof pa === 'object') {
      var paRows = [
        { label: 'POP Quality', val: pa.pop_quality },
        { label: 'EV Quality', val: pa.ev_quality },
        { label: 'Risk/Reward', val: pa.risk_reward },
      ].filter(function (r) { return !!r.val; });

      if (paRows.length > 0) {
        var paHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.12);">';
        paHtml += '<div class="section-title">PROBABILITY ASSESSMENT</div>';
        paRows.forEach(function (r) {
          paHtml += '<div style="font-size:11px;line-height:1.4;padding:2px 0 2px 8px;border-left:2px solid rgba(100,149,237,0.25);margin-bottom:3px;">'
            + '<span style="font-weight:600;color:var(--text,#d7fbff);">' + esc(r.label) + ':</span> '
            + '<span style="color:var(--text-secondary,#bbb);">' + esc(r.val) + '</span></div>';
        });
        paHtml += '</div>';
        bodyParts.push(paHtml);
      }
    }

    // ── GREEKS ASSESSMENT section ──
    var ga = c.modelGreeksAssessment;
    if (ga && typeof ga === 'object') {
      var gaRows = [
        { label: 'Delta', val: ga.delta_read, icon: '\u0394' },
        { label: 'Theta', val: ga.theta_read, icon: '\u0398' },
        { label: 'Vega', val: ga.vega_read, icon: '\u03BD' },
      ].filter(function (r) { return !!r.val; });

      if (gaRows.length > 0) {
        var gaHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(180,200,220,0.04);border-radius:6px;border:1px solid rgba(180,200,220,0.12);">';
        gaHtml += '<div class="section-title">GREEKS ASSESSMENT</div>';
        gaRows.forEach(function (r) {
          gaHtml += '<div style="font-size:11px;line-height:1.4;padding:2px 0 2px 8px;border-left:2px solid rgba(180,200,220,0.25);margin-bottom:3px;">'
            + '<span style="font-weight:600;color:var(--accent-cyan,#00dcff);">' + r.icon + ' ' + esc(r.label) + ':</span> '
            + '<span style="color:var(--text-secondary,#bbb);">' + esc(r.val) + '</span></div>';
        });
        gaHtml += '</div>';
        bodyParts.push(gaHtml);
      }
    }

    // ── MARKET ALIGNMENT section ──
    if (c.modelMarketAlignment) {
      var maText = String(c.modelMarketAlignment);
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.12);">'
        + '<div class="section-title">MARKET ALIGNMENT</div>'
        + '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(maText) + '</div>'
        + '</div>'
      );
    }

    // ── CAUTION section ──
    if (c.modelCautionNotes.length > 0) {
      var cautionLis = c.modelCautionNotes.map(function (note) {
        return '<li style="margin-bottom:2px;">' + esc(note) + '</li>';
      }).join('');
      bodyParts.push(
        '<div class="section" style="margin-bottom:6px;padding:6px 10px;border-radius:6px;border:1px solid rgba(244,200,95,0.2);background:rgba(244,200,95,0.04);">'
        + '<div class="section-title" style="color:var(--warn,#f4c85f);">CAUTION</div>'
        + '<ul style="margin:0;padding-left:16px;font-size:11px;line-height:1.5;">' + cautionLis + '</ul>'
        + '</div>'
      );
    }

    // ── KEY FACTORS section ──
    if (c.modelKeyFactors.length > 0) {
      var factorsHtml = '';
      c.modelKeyFactors.forEach(function (f) {
        if (typeof f === 'string') {
          factorsHtml += '<div style="font-size:11px;color:var(--text-secondary,#bbb);line-height:1.4;padding:3px 0 3px 8px;border-left:2px solid #8899aa;margin-bottom:3px;">' + esc(f) + '</div>';
        } else if (f && typeof f === 'object') {
          var factorName = f.factor || f.name || '';
          var impact = String(f.impact || f.assessment || 'neutral').toLowerCase();
          var evidence = f.evidence || f.detail || '';
          var impColor = _assessColors[impact] || '#8899aa';
          var impLabel = impact.charAt(0).toUpperCase() + impact.slice(1);

          factorsHtml += '<div style="font-size:11px;line-height:1.4;padding:4px 0 4px 8px;border-left:2px solid ' + impColor + ';margin-bottom:4px;">'
            + '<div style="display:flex;align-items:center;gap:6px;">'
            + '<span style="color:' + impColor + ';font-weight:600;">' + esc(factorName) + '</span>'
            + '<span style="font-size:9px;padding:1px 5px;border-radius:3px;border:1px solid ' + impColor + '44;color:' + impColor + ';text-transform:uppercase;letter-spacing:0.3px;">' + esc(impLabel) + '</span>'
            + '</div>'
            + (evidence ? '<div style="font-size:10px;color:var(--muted,#6a8da8);margin-top:2px;">' + esc(evidence) + '</div>' : '')
            + '</div>';
        }
      });
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;">'
        + '<div class="section-title">KEY FACTORS</div>'
        + factorsHtml
        + '</div>'
      );
    }

    // ── SUGGESTED ADJUSTMENT ──
    if (c.modelSuggestedAdjustment) {
      bodyParts.push(
        '<div class="section" style="margin-bottom:6px;padding:6px 10px;border-radius:6px;border:1px solid rgba(0,220,255,0.15);background:rgba(0,220,255,0.03);">'
        + '<div class="section-title" style="color:var(--accent-cyan,#00dcff);">SUGGESTED ADJUSTMENT</div>'
        + '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(c.modelSuggestedAdjustment) + '</div>'
        + '</div>'
      );
    }

    return { body: bodyParts.join(''), warnings: warningParts.join('') };
  }

  /**
   * Execute an options trade via the TradingService preview/submit flow.
   * Builds multi-leg order from candidate legs and opens preview modal.
   */
  function _executeOptionsTrade(btn, tradeKey, rawCandidate) {
    var c = normalizeOptionsCandidate(rawCandidate);
    if (!c.legs || c.legs.length === 0) {
      console.warn('[TMC] Cannot execute options trade: no legs on candidate');
      return;
    }

    // Build order legs for Tradier multi-leg order
    var orderLegs = [];
    c.legs.forEach(function (leg) {
      var side = (leg.side || '').toUpperCase();
      var optionType = (leg.option_type || '').toLowerCase();
      // Build OCC symbol: SYMBOL + YYMMDD + P/C + 8-digit strike
      var occSymbol = '';
      if (c.symbol && c.expiration && leg.strike != null && optionType) {
        var parts = String(c.expiration).split('-');
        if (parts.length === 3) {
          var yy = parts[0].slice(-2);
          var mm = parts[1];
          var dd = parts[2];
          var pc = optionType.charAt(0).toUpperCase();
          var strikeInt = Math.round(Number(leg.strike) * 1000);
          var strikeStr = String(strikeInt);
          while (strikeStr.length < 8) strikeStr = '0' + strikeStr;
          occSymbol = c.symbol.toUpperCase() + yy + mm + dd + pc + strikeStr;
        }
      }
      orderLegs.push({
        option_symbol: occSymbol,
        side: side === 'SHORT' ? 'sell_to_open' : 'buy_to_open',
        quantity: 1,
        strike: leg.strike,
        option_type: optionType,
      });
    });

    var orderPayload = {
      class: 'multileg',
      symbol: c.symbol,
      type: 'market',
      duration: 'day',
      legs: orderLegs,
      // Preview metadata
      _meta: {
        strategy_id: c.strategyId || c.strategy,
        trade_key: tradeKey,
        source: 'tmc_options',
      },
    };

    // Use TradingService preview if available
    if (api && api.tradingPreview) {
      btn.disabled = true;
      btn.textContent = 'Previewing\u2026';
      api.tradingPreview(orderPayload)
        .then(function (preview) {
          btn.disabled = false;
          btn.textContent = 'Execute Trade';
          // Open execution modal with preview data
          if (window.BenTradeExecutionModal && window.BenTradeExecutionModal.open) {
            window.BenTradeExecutionModal.open(orderPayload, preview);
          } else {
            console.log('[TMC] Options trade preview:', preview);
            alert('Preview: ' + JSON.stringify(preview, null, 2));
          }
        })
        .catch(function (err) {
          btn.disabled = false;
          btn.textContent = 'Execute Trade';
          console.error('[TMC] Options trade preview failed:', err);
        });
    } else {
      console.log('[TMC] TradingService not available. Order payload:', orderPayload);
    }
  }

  function _startOptionsCompletionPoll(baselineRunId, intervalMs, maxAttempts) {
    _stopOptionsCompletionPoll();
    var attempts = 0;
    intervalMs = intervalMs || 15000;
    maxAttempts = maxAttempts || 20;

    _optionsPollTimer = setInterval(function () {
      attempts++;
      if (attempts > maxAttempts) {
        _stopOptionsCompletionPoll();
        return;
      }
      api.tmcGetLatestOptions()
        .then(function (resp) {
          var newRunId = resp && resp.data ? resp.data.run_id : null;
          if (newRunId && newRunId !== baselineRunId) {
            console.log('[TMC] Options completion poll detected new run: ' + newRunId);
            _stopOptionsCompletionPoll();
            loadOptionsOpportunities();
          }
        })
        .catch(function () {});
    }, intervalMs);
  }

  function _stopOptionsCompletionPoll() {
    if (_optionsPollTimer) {
      clearInterval(_optionsPollTimer);
      _optionsPollTimer = null;
    }
  }

  function triggerOptionsRun() {
    var statusEl = document.getElementById('tmcOptionsStatus');
    if (statusEl) { statusEl.textContent = 'Running...'; statusEl.className = 'tmc-run-status'; }

    var baselineRunId = _lastOptionsRunId;
    console.log('[TMC] Triggering options workflow (baseline run_id=' + (baselineRunId || 'none') + ')');

    api.tmcRunOptions()
      .then(function (result) {
        console.log('[TMC] Options workflow trigger returned: status=' + result.status +
          ' run_id=' + (result.run_id || '?'));
        updateStatusBadge(statusEl, result.status);
        _stopOptionsCompletionPoll();
        loadOptionsOpportunities();
      })
      .catch(function (err) {
        console.error('[TMC] Options workflow trigger failed:', err);
        updateStatusBadge(statusEl, 'failed');
        _startOptionsCompletionPoll(baselineRunId);
        loadOptionsOpportunities();
      });
  }

  /* =================================================================
   *  SECTION 3 -- Active Trade Candidates (unchanged -- uses
   *  /api/active-trade-pipeline, separate from TMC workflow endpoints)
   * ================================================================= */

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

  var _tmcAccountMode = 'paper';

  function _getAccountMode() {
    return _tmcAccountMode || 'paper';
  }

  function runActivePipeline() {
    if (_activeRunning) return;
    _activeRunning = true;

    var btn = document.getElementById('tmcRunActiveBtn');
    if (btn) { btn.textContent = 'Running...'; btn.disabled = true; }

    var skipModel = false;
    var cb = document.getElementById('tmcSkipModel');
    if (cb) skipModel = cb.checked;

    var accountMode = _getAccountMode();
    var url = '/api/active-trade-pipeline/run?account_mode=' + encodeURIComponent(accountMode) + '&skip_model=' + (skipModel ? 'true' : 'false');

    fetch(url, { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        _activeRunning = false;
        if (btn) { btn.textContent = 'Analyse Positions'; btn.disabled = false; }
        if (data.ok === false) {
          showActiveEmpty('Pipeline error: ' + ((data.error || {}).message || 'unknown'));
          return;
        }
        renderActiveResults(data);
      })
      .catch(function (err) {
        _activeRunning = false;
        if (btn) { btn.textContent = 'Analyse Positions'; btn.disabled = false; }
        console.error('[TMC] Active pipeline failed:', err);
        showActiveEmpty('Failed to run pipeline: ' + err.message);
      });
  }

  function loadLatestActiveResults() {
    fetch('/api/active-trade-pipeline/results')
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
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
          '<div class="tmc-empty-icon">&#9673;</div>' +
          '<div class="tmc-empty-text">' + esc(msg) + '</div>' +
        '</div>';
    }
    var count = document.getElementById('tmcActiveCount');
    if (count) { count.textContent = '--'; count.className = 'tmc-count-badge tmc-count-muted'; }
  }

  /** Keep rendered active trade rows for action handler lookups. */
  var _activeRenderedRows = [];
  var _activeExpandState  = {};

  function renderActiveResults(data) {
    var recs = data.recommendations || [];
    var grid = document.getElementById('tmcActiveTradeGrid');
    var countEl = document.getElementById('tmcActiveCount');

    if (!grid) return;

    if (recs.length === 0) {
      showActiveEmpty('No open positions found on ' + (_getAccountMode() || 'paper').toUpperCase() + ' account');
      return;
    }

    var sorted = recs.slice().sort(function (a, b) {
      var ua = a.urgency || 0, ub = b.urgency || 0;
      if (ua !== ub) return ub - ua;
      return (b.conviction || 0) - (a.conviction || 0);
    });

    if (countEl) {
      countEl.textContent = String(sorted.length);
      countEl.className = 'tmc-count-badge';
    }

    _activeRenderedRows = sorted.slice();

    var html = '';
    sorted.forEach(function (rec, idx) {
      try {
        html += buildActiveTradeCard(rec, idx);
      } catch (cardErr) {
        console.warn('[TMC] Active card render error for rec ' + idx, cardErr);
        html += '<div class="trade-card" style="margin-bottom:12px;padding:10px;border:1px solid rgba(255,120,100,0.3);border-radius:10px;background:rgba(8,18,26,0.9);color:rgba(255,180,160,0.8);font-size:12px;">\u26A0 Render error for ' + esc((rec && rec.symbol) || '#' + idx) + '</div>';
      }
    });

    grid.innerHTML = html;

    // Wire delegated action handlers
    grid.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-action]');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      var action   = btn.dataset.action;
      var tradeKey = btn.dataset.tradeKey || '';
      var row      = _findActiveRowByTradeKey(tradeKey);

      if (action === 'close-position' && row) {
        _executeActiveClose(btn, tradeKey, row);
      } else if (action === 'refresh-analysis' && row) {
        _refreshSinglePosition(btn, row);
      } else if (action === 'data-workbench' && row) {
        window.BenTradeTradeCard.openDataWorkbenchByTrade(row);
      }
    });

    // Wire expand state persistence
    grid.querySelectorAll('details.trade-card-collapse').forEach(function (details) {
      details.addEventListener('toggle', function () {
        var tk = details.dataset.tradeKey || '';
        if (tk) _activeExpandState[tk] = details.open;
      });
    });

    // Run meta banner
    var summary = data.summary || {};
    var acctMode = data.account_mode || _getAccountMode();
    var acctBadge = '<span class="active-account-badge badge-' + acctMode + '">' + acctMode.toUpperCase() + '</span>';

    var metaHtml =
      '<div class="tmc-active-run-meta">' +
        acctBadge +
        '<span class="tmc-meta-item">Run ' + esc((data.run_id || '').substring(0, 16)) + '</span>' +
        '<span class="tmc-meta-sep">|</span>' +
        '<span class="tmc-meta-item">' + (data.duration_ms || 0) + 'ms</span>' +
        '<span class="tmc-meta-sep">|</span>' +
        '<span class="tmc-meta-item">' + (summary.hold_count || 0) + ' hold</span>' +
        '<span class="tmc-meta-sep">|</span>' +
        '<span class="tmc-meta-item">' + (summary.reduce_count || 0) + ' reduce</span>' +
        '<span class="tmc-meta-sep">|</span>' +
        '<span class="tmc-meta-item">' + (summary.close_count || 0) + ' close</span>' +
        (summary.urgent_review_count > 0
          ? '<span class="tmc-meta-sep">|</span><span class="tmc-meta-item tmc-urgency-high">' + summary.urgent_review_count + ' urgent</span>'
          : '') +
      '</div>';

    var oldMeta = document.getElementById('tmcActiveRunMeta');
    if (oldMeta) oldMeta.remove();

    grid.insertAdjacentHTML('beforebegin',
      '<div id="tmcActiveRunMeta">' + metaHtml + '</div>'
    );
  }

  /**
   * Build a trade key for an active trade recommendation.
   * Format: SYMBOL|ACTIVE|strategy|expiration|dte
   */
  function _buildActiveTradeKey(rec) {
    var sym = String(rec.symbol || '').toUpperCase();
    var strat = String(rec.strategy || rec.strategy_id || '');
    var exp = rec.expiration || 'NA';
    var dte = rec.dte != null ? String(rec.dte) : 'NA';
    return sym + '|ACTIVE|' + strat + '|' + exp + '|' + dte;
  }

  function _findActiveRowByTradeKey(tradeKey) {
    if (!tradeKey) return null;
    for (var i = 0; i < _activeRenderedRows.length; i++) {
      if (_buildActiveTradeKey(_activeRenderedRows[i]) === tradeKey) return _activeRenderedRows[i];
    }
    return null;
  }

  /**
   * Build a single active trade recommendation as a full TradeCard.
   * Same <details>/<summary> pattern as stock and options cards.
   */
  function buildActiveTradeCard(rec, idx) {
    var tc = window.BenTradeTradeCard;
    var symbol = rec.symbol || '???';
    var recommendation = (rec.recommendation || '--').toUpperCase();
    var conviction = rec.conviction;
    var urgency = rec.urgency || 1;
    var strategy = rec.strategy || '';
    var strategyLabel = strategy ? strategy.replace(/_/g, ' ').replace(/\b\w/g, function (ch) { return ch.toUpperCase(); }) : '--';
    var dte = rec.dte;
    var posSnap = rec.position_snapshot || {};
    var engineSummary = rec.internal_engine_summary || {};
    var engineMetrics = rec.internal_engine_metrics || {};
    var modelSummary = rec.model_summary || {};
    var tradeKey = _buildActiveTradeKey(rec);

    // ── Health score badge (replaces Score badge) ──
    var healthScore = engineSummary.trade_health_score;
    var healthColor = '#8a8ab4';
    if (healthScore != null) {
      if (healthScore >= 70) healthColor = '#00dc78';
      else if (healthScore >= 45) healthColor = '#ffc83c';
      else healthColor = '#ff5a5a';
    }
    var healthBadge = healthScore != null
      ? '<span class="trade-rank-badge" style="font-size:14px;font-weight:700;color:' + healthColor + ';background:' + healthColor + '12;border:1px solid ' + healthColor + '44;border-radius:8px;padding:3px 10px;white-space:nowrap;">Health ' + Math.round(healthScore) + '</span>'
      : '';

    // ── Recommendation badge ──
    var recColor = '#b4b4c8';
    var recPulse = '';
    if (recommendation === 'HOLD') recColor = '#00dc78';
    else if (recommendation === 'REDUCE') recColor = '#ffc83c';
    else if (recommendation === 'CLOSE') recColor = '#ff5a5a';
    else if (recommendation === 'URGENT_REVIEW') { recColor = '#ff5a5a'; recPulse = ' tmc-pulse'; }
    var recBadge = '<span class="' + recClass(recommendation) + recPulse + '" style="font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid ' + recColor + '44;color:' + recColor + ';font-weight:700;letter-spacing:0.3px;white-space:nowrap;">' + esc(recommendation.replace(/_/g, ' ')) + '</span>';

    // ── P&L display ──
    var pnlVal = posSnap.unrealized_pnl;
    var pnlPct = posSnap.unrealized_pnl_pct;
    var pnlColor = pnlVal != null ? (pnlVal >= 0 ? '#00dc78' : '#ff5a5a') : '#8a8ab4';
    var pnlText = pnlVal != null ? '$' + pnlVal.toFixed(2) : '--';
    var pnlPctText = pnlPct != null ? ' (' + (pnlPct * 100).toFixed(1) + '%)' : '';

    // ── Header pills ──
    var symbolBadge = tc ? tc.pill(symbol) : '<span class="qtPill">' + esc(symbol) + '</span>';
    var dteBadge = dte != null ? (tc ? tc.pill(dte + ' DTE') : '<span class="qtPill">' + dte + ' DTE</span>') : '';

    // ── Subtitle: strikes, expiration, P&L ──
    var subtitleParts = [];
    subtitleParts.push(strategyLabel);
    if (rec.expiration) subtitleParts.push(rec.expiration);
    subtitleParts.push('<span style="color:' + pnlColor + ';">' + pnlText + pnlPctText + '</span>');
    var subtitleText = subtitleParts.join(' \u00B7 ');

    // ── Chevron SVG ──
    var chevronSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>';

    // ── Build enrichment sections ──
    var enrichment = buildActiveEnrichmentHtml(rec);

    // ── Collapse state ──
    var isExpanded = tradeKey ? (_activeExpandState[tradeKey] === true) : false;
    var openAttr = isExpanded ? ' open' : '';

    // ── Action buttons (always visible) ──
    var tradeKeyAttr = ' data-trade-key="' + esc(tradeKey) + '"';
    var isActionable = recommendation === 'CLOSE' || recommendation === 'URGENT_REVIEW' || recommendation === 'REDUCE';
    var actionsHtml = enrichment.warnings
      + '<div class="trade-actions">'
      + '<div class="actions-row">';
    if (isActionable) {
      var closeBtnClass = (recommendation === 'CLOSE' || recommendation === 'URGENT_REVIEW') ? 'btn-danger' : 'btn-warn';
      var closeBtnLabel = recommendation === 'REDUCE' ? 'Reduce Position' : 'Close Position';
      actionsHtml += '<button type="button" class="btn ' + closeBtnClass + ' btn-action" data-action="close-position"' + tradeKeyAttr + ' title="' + esc(closeBtnLabel) + '">' + closeBtnLabel + '</button>';
    }
    actionsHtml += '<button type="button" class="btn btn-action" data-action="refresh-analysis"' + tradeKeyAttr + ' title="Re-run analysis for this position">Refresh Analysis</button>'
      + '</div>'
      + '<div class="actions-row">'
      + '<button type="button" class="btn btn-action" data-action="data-workbench"' + tradeKeyAttr + ' title="Send to Data Workbench">Send to Data Workbench</button>'
      + '</div>'
      + '</div>';

    // ── Full card HTML ──
    return '<div class="trade-card" data-idx="' + idx + '"' + tradeKeyAttr + ' style="margin-bottom:14px;display:flex;flex-direction:column;">'
      + '<details class="trade-card-collapse"' + tradeKeyAttr + openAttr + '>'
      + '<summary class="trade-summary"><div class="trade-header trade-header-click">'
      + '<div class="trade-header-left"><span class="chev">' + chevronSvg + '</span></div>'
      + '<div class="trade-header-center">'
      + '<div class="trade-type" style="display:flex;align-items:center;gap:8px;justify-content:center;flex-wrap:wrap;">' + symbolBadge + ' ' + dteBadge + ' <span style="font-size:11px;color:var(--muted);">Active Position</span> ' + recBadge + '</div>'
      + '<div class="trade-subtitle">' + subtitleText + '</div>'
      + '</div>'
      + '<div class="trade-header-right">' + healthBadge + '</div>'
      + '</div></summary>'
      + '<div class="trade-body" style="flex:1 1 auto;">'
      + enrichment.body
      + '</div>'
      + '</details>'
      + actionsHtml
      + '</div>';
  }

  /**
   * Build enrichment HTML for active trade expanded body.
   * Returns { body, warnings } matching stock/options enrichment pattern.
   */
  function buildActiveEnrichmentHtml(rec) {
    var bodyParts = [];
    var warningParts = [];
    var posSnap = rec.position_snapshot || {};
    var engineSummary = rec.internal_engine_summary || {};
    var engineMetrics = rec.internal_engine_metrics || {};
    var modelSummary = rec.model_summary || {};
    var marketAlignRaw = rec.market_alignment || {};
    var marketAlignLabel = (typeof marketAlignRaw === 'object' ? marketAlignRaw.label : marketAlignRaw) || '--';
    var marketAlignDetail = (typeof marketAlignRaw === 'object' ? marketAlignRaw.detail : marketAlignRaw) || '';
    var isDegraded = rec.is_degraded;
    var degradedReasons = rec.degraded_reasons || [];

    // ── Degradation banner ──
    if (isDegraded && degradedReasons.length > 0) {
      warningParts.push(
        '<div style="margin-bottom:6px;padding:5px 10px;font-size:11px;font-weight:600;color:#ff8a5a;background:rgba(255,138,90,0.08);border:1px solid rgba(255,138,90,0.2);border-radius:5px;text-align:center;">'
        + '\u26A0 Analysis degraded: ' + esc(degradedReasons.slice(0, 3).join(', '))
        + '</div>'
      );
    }

    // ── POSITION SNAPSHOT section ──
    var snapItems = [
      { label: 'Entry Price', value: posSnap.avg_open_price != null ? '$' + Number(posSnap.avg_open_price).toFixed(2) : '--' },
      { label: 'Current Price', value: posSnap.mark_price != null ? '$' + Number(posSnap.mark_price).toFixed(2) : '--' },
      { label: 'Unrealized P&L', value: posSnap.unrealized_pnl != null ? '$' + Number(posSnap.unrealized_pnl).toFixed(2) : '--',
        cssClass: posSnap.unrealized_pnl != null ? (posSnap.unrealized_pnl >= 0 ? 'positive' : 'negative') : 'neutral' },
      { label: 'P&L %', value: posSnap.unrealized_pnl_pct != null ? (posSnap.unrealized_pnl_pct * 100).toFixed(1) + '%' : '--',
        cssClass: posSnap.unrealized_pnl_pct != null ? (posSnap.unrealized_pnl_pct >= 0 ? 'positive' : 'negative') : 'neutral' },
      { label: 'DTE', value: rec.dte != null ? rec.dte + 'd' : '--' },
      { label: 'Expiration', value: rec.expiration || posSnap.expiration || '--' },
      { label: 'Cost Basis', value: posSnap.cost_basis_total != null ? '$' + Number(posSnap.cost_basis_total).toFixed(2) : '--' },
      { label: 'Market Value', value: posSnap.market_value != null ? '$' + Number(posSnap.market_value).toFixed(2) : '--' },
    ];
    var snapGrid = '<div class="metric-grid">' + snapItems.map(function (item) {
      return '<div class="metric"><div class="metric-label">' + esc(item.label) + '</div><div class="metric-value ' + (item.cssClass || 'neutral') + '">' + item.value + '</div></div>';
    }).join('') + '</div>';

    // Per-leg details
    var legs = posSnap.legs || [];
    var legsHtml = '';
    if (legs.length > 0) {
      var legRows = '';
      legs.forEach(function (leg) {
        var qty = leg.quantity || leg.qty || 0;
        var side = qty < 0 ? 'SHORT' : 'LONG';
        var sideClass = side === 'SHORT' ? 'tmc-leg-short' : 'tmc-leg-long';
        var strike = leg.strike != null ? String(leg.strike) : '?';
        var optType = (leg.option_type || leg.type || '').toUpperCase();
        var occSymbol = leg.symbol || '';
        var bidAsk = '';
        if (leg.bid != null && leg.ask != null) {
          bidAsk = Number(leg.bid).toFixed(2) + ' / ' + Number(leg.ask).toFixed(2);
        }
        var delta = leg.delta != null ? '\u0394 ' + Number(leg.delta).toFixed(2) : '';
        legRows +=
          '<div class="tmc-options-leg-row">' +
            '<span class="tmc-leg-side ' + sideClass + '">' + esc(side) + '</span>' +
            '<span class="tmc-leg-strike">' + esc(strike) + ' ' + esc(optType) + '</span>' +
            '<span class="tmc-leg-pricing">' + esc(bidAsk) + '</span>' +
            '<span class="tmc-leg-delta">' + esc(delta) + '</span>' +
          '</div>';
      });
      legsHtml = '<div class="tmc-options-legs" style="margin-top:6px;">' + legRows + '</div>';
    }

    // Live Greeks
    var greeks = rec.live_greeks;
    var greeksHtml = '';
    if (greeks) {
      var gItems = [
        { label: '\u0394 Trade Delta', value: greeks.trade_delta != null ? greeks.trade_delta.toFixed(2) : '--' },
        { label: '\u0398 Trade Theta', value: greeks.trade_theta != null ? '$' + greeks.trade_theta.toFixed(2) : '--' },
        { label: '\u03BD Trade Vega', value: greeks.trade_vega != null ? '$' + greeks.trade_vega.toFixed(2) : '--' },
      ];
      greeksHtml = '<div style="display:flex;gap:12px;margin-top:6px;flex-wrap:wrap;">' + gItems.map(function (g) {
        return '<span style="font-size:11px;color:var(--text-secondary,#bbb);"><span style="color:var(--accent-cyan,#00dcff);font-weight:600;">' + g.label + ':</span> ' + g.value + '</span>';
      }).join('') + '</div>';
      if (greeks.any_refreshed) {
        greeksHtml += '<div style="font-size:9px;color:var(--muted);margin-top:2px;">\u2713 Greeks refreshed from live chain data</div>';
      }
    }

    bodyParts.push(
      '<div class="section section-core"><div class="section-title">POSITION SNAPSHOT</div>'
      + snapGrid + legsHtml + greeksHtml
      + '</div>'
    );

    // ── HEALTH ASSESSMENT section ──
    var healthScore = engineSummary.trade_health_score;
    var healthColor = '#8a8ab4';
    if (healthScore != null) {
      if (healthScore >= 70) healthColor = '#00dc78';
      else if (healthScore >= 45) healthColor = '#ffc83c';
      else healthColor = '#ff5a5a';
    }
    var compKeys = Object.keys(engineMetrics);
    if (healthScore != null || compKeys.length > 0) {
      var healthHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid ' + healthColor + '33;background:' + healthColor + '08;">';
      healthHtml += '<div class="section-title">HEALTH ASSESSMENT';
      if (healthScore != null) {
        healthHtml += ' \u2014 <span style="color:' + healthColor + ';">' + Math.round(healthScore) + '/100</span>';
      }
      healthHtml += '</div>';

      if (compKeys.length > 0) {
        healthHtml += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:4px;">';
        compKeys.forEach(function (k) {
          var v = engineMetrics[k];
          var displayVal = v != null ? Math.round(v) : '--';
          var cColor = '#8a8ab4';
          if (v != null) {
            if (v >= 70) cColor = '#00dc78';
            else if (v >= 45) cColor = '#ffc83c';
            else cColor = '#ff5a5a';
          }
          healthHtml += '<span style="font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid ' + cColor + '33;color:' + cColor + ';background:' + cColor + '08;">'
            + esc(k.replace(/_/g, ' ')) + ': <strong>' + displayVal + '</strong></span>';
        });
        healthHtml += '</div>';
      }

      // Engine recommendation
      if (engineSummary.engine_recommendation) {
        healthHtml += '<div style="margin-top:6px;font-size:11px;color:var(--text-secondary,#bbb);">Engine: <strong>' + esc(engineSummary.engine_recommendation) + '</strong></div>';
      }

      // Risk flags
      var riskFlags = rec.internal_engine_flags || [];
      if (riskFlags.length > 0) {
        healthHtml += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px;">';
        riskFlags.forEach(function (f) {
          healthHtml += '<span class="tmc-risk-flag">' + esc(f) + '</span>';
        });
        healthHtml += '</div>';
      }

      healthHtml += '</div>';
      bodyParts.push(healthHtml);
    }

    // ── MODEL REVIEW section ──
    var hasModel = !!(modelSummary.model_available);
    if (hasModel) {
      var mRec = String(modelSummary.model_recommendation || rec.recommendation || '--').toUpperCase();
      var mConv = modelSummary.model_conviction != null ? fmtPctDirect(modelSummary.model_conviction) : '';
      var mProvider = modelSummary.provider || '';
      var mLatency = modelSummary.latency_ms != null ? modelSummary.latency_ms + 'ms' : '';
      var mHeaderBits = [mRec, mConv ? 'Conf: ' + mConv : '', mProvider, mLatency].filter(Boolean).join(' \u00B7 ');

      var mRecColor = '#b4b4c8';
      if (mRec === 'HOLD') mRecColor = '#00dc78';
      else if (mRec === 'REDUCE') mRecColor = '#ffc83c';
      else if (mRec === 'CLOSE' || mRec === 'URGENT_REVIEW' || mRec === 'URGENT REVIEW') mRecColor = '#ff5a5a';

      var modelBody = '';
      if (rec.rationale_summary) {
        modelBody += '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;margin-bottom:4px;">' + esc(rec.rationale_summary) + '</div>';
      }

      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid ' + mRecColor + '33;background:' + mRecColor + '08;">'
        + '<div class="section-title" style="margin-bottom:6px;">MODEL REVIEW'
        + (mHeaderBits ? ' \u2014 <span style="color:' + mRecColor + ';">' + mHeaderBits + '</span>' : '')
        + '</div>'
        + modelBody
        + '</div>'
      );
    } else {
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid rgba(138,138,180,0.2);background:rgba(138,138,180,0.04);">'
        + '<div class="section-title" style="color:#8a8ab4;">MODEL REVIEW</div>'
        + '<div style="font-size:12px;color:var(--muted);">Model analysis unavailable \u2014 engine-only assessment</div>'
        + '</div>'
      );
    }

    // ── SUPPORTING POINTS section ──
    var points = rec.key_supporting_points || [];
    if (points.length > 0) {
      var pointsLis = points.map(function (p) {
        return '<li style="margin-bottom:2px;">' + esc(p) + '</li>';
      }).join('');
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;">'
        + '<div class="section-title">KEY SUPPORTING POINTS</div>'
        + '<ul style="margin:0;padding-left:16px;font-size:11px;line-height:1.5;">' + pointsLis + '</ul>'
        + '</div>'
      );
    }

    // ── EVENT RISK section ──
    var eventRisk = rec.event_risk;
    if (eventRisk) {
      var erLevel = eventRisk.event_risk_level || 'unknown';
      var erDetails = eventRisk.event_details || [];
      var erColor = '#8a8ab4';
      if (erLevel === 'high' || erLevel === 'critical') erColor = '#ff5a5a';
      else if (erLevel === 'elevated') erColor = '#ffc83c';
      else if (erLevel === 'quiet') erColor = '#00dc78';

      var erHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;border-radius:6px;border:1px solid ' + erColor + '33;background:' + erColor + '08;">';
      erHtml += '<div class="section-title">EVENT RISK \u2014 <span style="color:' + erColor + ';">' + esc(erLevel.toUpperCase()) + '</span></div>';

      if (erDetails.length > 0) {
        erHtml += '<div style="margin-top:4px;">';
        erDetails.forEach(function (evt) {
          var evtName = (typeof evt === 'string') ? evt : (evt.event || evt.name || evt.title || JSON.stringify(evt));
          var evtDate = (typeof evt === 'object' && evt.date) ? ' (' + evt.date + ')' : '';
          erHtml += '<div style="font-size:11px;color:var(--text-secondary,#bbb);line-height:1.4;padding:2px 0 2px 8px;border-left:2px solid ' + erColor + '44;margin-bottom:3px;">'
            + esc(evtName) + esc(evtDate) + '</div>';
        });
        erHtml += '</div>';
      }
      erHtml += '</div>';
      bodyParts.push(erHtml);
    }

    // ── PORTFOLIO CONTEXT section ──
    var portCtx = rec.portfolio_context;
    if (portCtx) {
      var pcItems = [
        { label: 'Position Risk %', value: portCtx.position_risk_pct != null ? (portCtx.position_risk_pct * 100).toFixed(1) + '%' : '--' },
        { label: 'Underlying Conc.', value: portCtx.underlying_concentration_pct != null ? (portCtx.underlying_concentration_pct * 100).toFixed(1) + '%' : '--' },
        { label: 'Portfolio \u0394', value: portCtx.net_portfolio_delta != null ? portCtx.net_portfolio_delta.toFixed(2) : '--' },
        { label: 'Portfolio \u0398', value: portCtx.net_portfolio_theta != null ? '$' + portCtx.net_portfolio_theta.toFixed(2) : '--' },
        { label: 'Total Positions', value: portCtx.total_positions != null ? String(portCtx.total_positions) : '--' },
      ];

      var pcHtml = '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.12);">';
      pcHtml += '<div class="section-title">PORTFOLIO CONTEXT</div>';
      pcHtml += '<div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:4px;">';
      pcItems.forEach(function (item) {
        pcHtml += '<span style="font-size:11px;color:var(--text-secondary,#bbb);"><span style="font-weight:600;color:var(--text,#d7fbff);">' + esc(item.label) + ':</span> ' + item.value + '</span>';
      });
      pcHtml += '</div>';

      if (portCtx.is_portfolio_concentrated) {
        pcHtml += '<div style="margin-top:4px;font-size:10px;color:#ffc83c;">\u26A0 Portfolio concentrated in ' + esc(portCtx.top_concentration_symbol || '?') + '</div>';
      }
      var pfFlags = portCtx.portfolio_risk_flags || [];
      if (pfFlags.length > 0) {
        pcHtml += '<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;">';
        pfFlags.forEach(function (f) {
          pcHtml += '<span class="tmc-risk-flag">' + esc(f) + '</span>';
        });
        pcHtml += '</div>';
      }
      pcHtml += '</div>';
      bodyParts.push(pcHtml);
    }

    // ── MARKET ALIGNMENT section ──
    if (marketAlignDetail) {
      var maColor = marketAlignLabel === 'Aligned' ? '#00dc78' : marketAlignLabel === 'Unfavorable' ? '#ff5a5a' : '#ffc83c';
      bodyParts.push(
        '<div class="section" style="margin-bottom:8px;padding:8px 10px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.12);">'
        + '<div class="section-title">MARKET ALIGNMENT \u2014 <span style="color:' + maColor + ';">' + esc(marketAlignLabel.toUpperCase()) + '</span></div>'
        + '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(marketAlignDetail) + '</div>'
        + '</div>'
      );
    }

    // ── CAUTION section (key_risks) ──
    var risks = rec.key_risks || [];
    if (risks.length > 0) {
      var riskLis = risks.map(function (r) {
        return '<li style="margin-bottom:2px;">' + esc(r) + '</li>';
      }).join('');
      bodyParts.push(
        '<div class="section" style="margin-bottom:6px;padding:6px 10px;border-radius:6px;border:1px solid rgba(244,200,95,0.2);background:rgba(244,200,95,0.04);">'
        + '<div class="section-title" style="color:var(--warn,#f4c85f);">CAUTION</div>'
        + '<ul style="margin:0;padding-left:16px;font-size:11px;line-height:1.5;">' + riskLis + '</ul>'
        + '</div>'
      );
    }

    // ── SUGGESTED NEXT MOVE section ──
    var nextMove = rec.suggested_next_move || '';
    if (nextMove) {
      bodyParts.push(
        '<div class="section" style="margin-bottom:6px;padding:6px 10px;border-radius:6px;border:1px solid rgba(0,220,255,0.15);background:rgba(0,220,255,0.03);">'
        + '<div class="section-title" style="color:var(--accent-cyan,#00dcff);">SUGGESTED NEXT MOVE</div>'
        + '<div style="font-size:12px;color:var(--text,#d7fbff);line-height:1.6;">' + esc(nextMove) + '</div>'
        + '</div>'
      );
    }

    // ── Model metadata footer ──
    if (modelSummary.model_available) {
      bodyParts.push(
        '<div style="margin-top:4px;font-size:9px;color:var(--muted);display:flex;gap:6px;align-items:center;">'
        + '<span>' + esc(modelSummary.provider || '') + '</span>'
        + '<span>\u00B7</span>'
        + '<span>' + esc(modelSummary.model_name || '') + '</span>'
        + (modelSummary.latency_ms != null ? '<span>\u00B7</span><span>' + modelSummary.latency_ms + 'ms</span>' : '')
        + (rec.recommendation_source ? '<span>\u00B7</span><span>via ' + esc(rec.recommendation_source) + '</span>' : '')
        + '</div>'
      );
    }

    return { body: bodyParts.join(''), warnings: warningParts.join('') };
  }

  /**
   * Handle close/reduce position via the suggested_close_order from the pipeline.
   * Uses /api/trading/preview if available, falls back to TradeTicket.
   */
  function _executeActiveClose(btn, tradeKey, rec) {
    var closeOrder = rec.suggested_close_order;
    if (!closeOrder) {
      console.warn('[TMC] No suggested_close_order for', tradeKey);
      // Fall back to TradeTicket
      _openActiveTradeTicket(rec, 'close');
      return;
    }

    if (api && api.tradingPreview) {
      btn.disabled = true;
      btn.textContent = 'Previewing\u2026';
      api.tradingPreview(closeOrder)
        .then(function (preview) {
          btn.disabled = false;
          btn.textContent = btn.textContent.indexOf('Reduce') >= 0 ? 'Reduce Position' : 'Close Position';
          if (window.BenTradeExecutionModal && window.BenTradeExecutionModal.open) {
            window.BenTradeExecutionModal.open(closeOrder, preview);
          } else {
            console.log('[TMC] Close order preview:', preview);
            alert('Preview: ' + JSON.stringify(preview, null, 2));
          }
        })
        .catch(function (err) {
          btn.disabled = false;
          btn.textContent = btn.textContent.indexOf('Reduce') >= 0 ? 'Reduce Position' : 'Close Position';
          console.error('[TMC] Close order preview failed:', err);
          alert('Preview failed: ' + (err.message || String(err)));
        });
    } else {
      _openActiveTradeTicket(rec, 'close');
    }
  }

  /**
   * Refresh analysis for a single position (re-runs the pipeline for just this symbol).
   */
  function _refreshSinglePosition(btn, rec) {
    var symbol = rec.symbol || '';
    btn.disabled = true;
    btn.textContent = 'Refreshing\u2026';

    var accountMode = _getAccountMode();
    var skipModel = false;
    var cb = document.getElementById('tmcSkipModel');
    if (cb) skipModel = cb.checked;

    var url = '/api/active-trade-pipeline/run?account_mode=' + encodeURIComponent(accountMode) + '&skip_model=' + (skipModel ? 'true' : 'false');
    fetch(url, { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        btn.disabled = false;
        btn.textContent = 'Refresh Analysis';
        if (data.ok === false) {
          alert('Refresh failed: ' + ((data.error || {}).message || 'unknown'));
          return;
        }
        renderActiveResults(data);
      })
      .catch(function (err) {
        btn.disabled = false;
        btn.textContent = 'Refresh Analysis';
        alert('Refresh failed: ' + (err.message || String(err)));
      });
  }

  /**
   * Fallback: open TradeTicket for active position actions.
   */
  function _openActiveTradeTicket(rec, action) {
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
      console.warn('[TMC] TradeTicket not available -- trade data:', tradeData);
      alert('Trade Ticket module not loaded. Trade data logged to console.');
    }
  }

  /* =================================================================
   *  FULL REFRESH -- chains Stock → Options → Active → Balance
   * ================================================================= */

  var _fullRefreshStages = [
    { label: 'Stock Scan',        step: 1 },
    { label: 'Options Scan',      step: 2 },
    { label: 'Active Trades',     step: 3 },
    { label: 'Portfolio Balance',  step: 4 },
  ];

  function _setRefreshStatus(msg, step, total) {
    var el = document.getElementById('tmcRefreshStatus');
    if (!el) return;
    if (!msg) { el.style.display = 'none'; el.textContent = ''; return; }
    el.style.display = 'inline';
    el.textContent = (step && total) ? ('(' + step + '/' + total + ') ' + msg) : msg;
  }

  function _setFullRefreshEnabled(running) {
    _fullRefreshRunning = running;
    var btn = document.getElementById('tmcFullRefreshBtn');
    if (btn) {
      btn.disabled = running;
      btn.textContent = running ? '⟳ Running…' : '⟳ Full Refresh';
    }
  }

  function handleFullRefresh() {
    if (_fullRefreshRunning) return;
    _setFullRefreshEnabled(true);
    var total = _fullRefreshStages.length;

    var skipModel = false;
    var cb = document.getElementById('tmcSkipModel');
    if (cb) skipModel = cb.checked;
    var accountMode = _getAccountMode();

    var chainResults = {};

    // Stage 1: Stock Scan
    _setRefreshStatus('Running stock scan…', 1, total);
    api.tmcRunStock()
      .then(function (res) {
        chainResults.stockTrigger = res;
        return api.tmcGetLatestStock();
      })
      .then(function (resp) {
        chainResults.stockResults = resp;
        // Refresh the stock section UI
        loadStockOpportunities();

        // Stage 2: Options Scan
        _setRefreshStatus('Running options scan…', 2, total);
        return api.tmcRunOptions();
      })
      .then(function (res) {
        chainResults.optionsTrigger = res;
        return api.tmcGetLatestOptions();
      })
      .then(function (resp) {
        chainResults.optionsResults = resp;
        loadOptionsOpportunities();

        // Stage 3: Active Trades
        _setRefreshStatus('Analysing active trades…', 3, total);
        var url = '/api/active-trade-pipeline/run?account_mode=' +
          encodeURIComponent(accountMode) + '&skip_model=' + (skipModel ? 'true' : 'false');
        return fetch(url, { method: 'POST' }).then(function (r) { return r.json(); });
      })
      .then(function (activeData) {
        chainResults.activeResults = activeData;
        renderActiveResults(activeData);

        // Stage 4: Portfolio Balance
        _setRefreshStatus('Building portfolio balance…', 4, total);
        return api.tmcRunPortfolioBalance({
          account_mode: accountMode,
          skip_model: skipModel,
          active_trade_results: activeData,
        });
      })
      .then(function (balanceData) {
        chainResults.balanceResults = balanceData;
        displayPortfolioBalance(balanceData);
        _setRefreshStatus('Complete', null, null);
        setTimeout(function () { _setRefreshStatus(null); }, 4000);
        _setFullRefreshEnabled(false);
        console.log('[TMC] Full Refresh complete', chainResults);
      })
      .catch(function (err) {
        console.error('[TMC] Full Refresh failed:', err);
        _setRefreshStatus('Failed: ' + err.message, null, null);
        setTimeout(function () { _setRefreshStatus(null); }, 8000);
        _setFullRefreshEnabled(false);
      });
  }

  /* -- Portfolio Balance rendering ----------------------------------- */

  /** Store last balance result for close-button binding */
  var _lastBalanceResult = null;

  function displayPortfolioBalance(data) {
    var section = document.getElementById('tmcPortfolioBalanceSection');
    var grid    = document.getElementById('tmcPortfolioBalanceGrid');
    var badge   = document.getElementById('tmcBalanceStatus');
    if (!section || !grid) return;
    section.style.display = '';

    _lastBalanceResult = data;

    var plan = data && data.rebalance_plan ? data.rebalance_plan : null;
    if (!plan) {
      grid.innerHTML = '<div class="tmc-empty-state"><div class="tmc-empty-icon">&#9888;</div>' +
        '<div class="tmc-empty-text">No balance data returned</div></div>';
      return;
    }

    var closes  = plan.close_actions  || [];
    var holds   = plan.hold_positions || [];
    var opens   = plan.open_actions   || [];
    var skips   = plan.skip_actions   || [];
    var impact  = plan.net_impact     || {};
    var postAdj = plan.post_adjustment_state || {};
    var policy  = data.risk_policy || plan.risk_policy_used || {};

    // Separate CLOSE vs REDUCE within close_actions
    var closeOnly = [];
    var reduceOnly = [];
    closes.forEach(function (a) {
      if (a.action === 'REDUCE') { reduceOnly.push(a); }
      else { closeOnly.push(a); }
    });

    var actionCount = closes.length + opens.length;
    if (badge) {
      badge.textContent = actionCount > 0 ? actionCount + ' actions' : 'balanced';
      badge.className = 'tmc-count-badge' + (actionCount > 0 ? ' tmc-count-live' : ' tmc-count-muted');
    }

    var html = '';

    // ──────────────── 1. SUMMARY BAR ────────────────
    html += '<div class="tmc-pb-summary-bar">';
    html += '<div class="tmc-pb-summary-row">';
    html += '<span class="tmc-pb-summary-item"><span class="tmc-pb-label">Equity</span> <span class="tmc-pb-val">$' + _fmtNum(data.account_equity) + '</span></span>';
    html += '<span class="tmc-pb-summary-item"><span class="tmc-pb-label">Regime</span> <span class="tmc-pb-val tmc-pb-regime">' + esc(data.regime_label || 'Unknown') + '</span></span>';
    html += '<span class="tmc-pb-summary-item"><span class="tmc-pb-label">Risk</span> <span class="tmc-pb-val">$' + _fmtNum(impact.risk_before) + ' → $' + _fmtNum(impact.risk_after_opens) + '</span></span>';
    html += '<span class="tmc-pb-summary-item"><span class="tmc-pb-label">Delta</span> <span class="tmc-pb-val">' + _fmtDec(impact.delta_before, 2) + ' → ' + _fmtDec(impact.delta_after, 2) + '</span></span>';
    html += '<span class="tmc-pb-summary-item"><span class="tmc-pb-label">Trades</span> <span class="tmc-pb-val">' + _fmtInt(impact.trades_before) + ' → ' + _fmtInt(impact.trades_after) + '</span></span>';
    html += '</div>';
    html += '<div class="tmc-pb-change-strip">';
    html += 'Close ' + _fmtInt(impact.positions_closed) + ', ';
    html += 'Reduce ' + _fmtInt(impact.positions_reduced) + ', ';
    html += 'Hold ' + _fmtInt(impact.positions_held) + ', ';
    html += 'Open ' + _fmtInt(impact.positions_opened) + ' new, ';
    html += 'Skip ' + _fmtInt(impact.positions_skipped);
    if (impact.risk_budget_remaining != null) {
      html += ' | Budget remaining: $' + _fmtNum(impact.risk_budget_remaining);
    }
    html += '</div>';
    html += '</div>';

    // ──────────────── 2. CLOSE / REDUCE ACTIONS ────────────────
    if (closeOnly.length || reduceOnly.length) {
      html += '<div class="tmc-pb-group tmc-pb-group-close">';
      html += '<h4 class="tmc-pb-group-title">Close / Reduce';
      html += ' <span class="tmc-pb-group-count">' + (closeOnly.length + reduceOnly.length) + '</span></h4>';
      closeOnly.forEach(function (a, i) {
        html += _renderCloseAction(a, i);
      });
      reduceOnly.forEach(function (a, i) {
        html += _renderCloseAction(a, closeOnly.length + i);
      });
      html += '</div>';
    }

    // ──────────────── 3. HOLD POSITIONS ────────────────
    if (holds.length) {
      html += '<div class="tmc-pb-group tmc-pb-group-hold">';
      html += '<h4 class="tmc-pb-group-title">Hold';
      html += ' <span class="tmc-pb-group-count">' + holds.length + '</span></h4>';
      html += '<div class="tmc-pb-hold-grid">';
      holds.forEach(function (pos) {
        html += '<span class="tmc-pb-hold-badge">';
        html += '<span class="tmc-pb-hold-sym">' + esc(pos.symbol) + '</span> ';
        html += '<span class="tmc-pb-hold-strat">' + esc((pos.strategy || '').replace(/_/g, ' ')) + '</span>';
        if (pos.trade_health_score != null) {
          html += ' <span class="tmc-pb-hold-health">health ' + Math.round(pos.trade_health_score) + '</span>';
        }
        html += '</span>';
      });
      html += '</div>';
      html += '</div>';
    }

    // ──────────────── 4. OPEN SUGGESTIONS ────────────────
    if (opens.length) {
      html += '<div class="tmc-pb-group tmc-pb-group-open">';
      html += '<h4 class="tmc-pb-group-title">Suggested New Trades';
      html += ' <span class="tmc-pb-group-count">' + opens.length + '</span></h4>';
      opens.forEach(function (a, i) {
        html += _renderOpenAction(a, i);
      });
      html += '</div>';
    }

    // ──────────────── 5. SKIPPED CANDIDATES (collapsible) ────────────────
    if (skips.length) {
      html += '<details class="tmc-pb-collapsible">';
      html += '<summary class="tmc-pb-collapsible-summary">' + skips.length + ' candidates skipped</summary>';
      html += '<div class="tmc-pb-skip-list">';
      skips.forEach(function (s) {
        html += '<div class="tmc-pb-skip-item">';
        html += '<span class="tmc-pb-skip-sym">' + esc(s.symbol) + '</span> ';
        html += '<span class="tmc-pb-skip-strat">' + esc((s.strategy || '').replace(/_/g, ' ')) + '</span>';
        html += '<span class="tmc-pb-skip-source">' + esc(s.source || '') + '</span>';
        html += '<span class="tmc-pb-skip-reason">' + esc(s.skip_reason) + '</span>';
        html += '</div>';
      });
      html += '</div>';
      html += '</details>';
    }

    // ──────────────── 6. RISK POLICY SUMMARY (collapsible) ────────────────
    html += '<details class="tmc-pb-collapsible">';
    html += '<summary class="tmc-pb-collapsible-summary">Risk Policy Details</summary>';
    html += '<div class="tmc-pb-policy-grid">';
    html += _policyRow('Max risk / trade', '$' + _fmtNum(policy.max_risk_per_trade));
    html += _policyRow('Max total risk', '$' + _fmtNum(policy.max_risk_total));
    html += _policyRow('Max concurrent trades', _fmtInt(policy.max_concurrent_trades));
    html += _policyRow('Regime', esc(policy.regime_label || data.regime_label || '—'));
    html += _policyRow('Regime multiplier', (policy.regime_multiplier != null ? policy.regime_multiplier + 'x' : '—'));
    html += _policyRow('Suggested max contracts', _fmtInt(policy.suggested_max_contracts));
    if (postAdj.risk_used != null) {
      html += _policyRow('Risk used (post-close)', '$' + _fmtNum(postAdj.risk_used));
      html += _policyRow('Risk budget available', '$' + _fmtNum(postAdj.risk_budget_available));
      html += _policyRow('Open slots', _fmtInt(postAdj.open_slots));
      html += _policyRow('Max risk / new trade', '$' + _fmtNum(postAdj.max_risk_per_new_trade));
    }
    html += '</div>';
    html += '</details>';

    // Balanced state — no actions
    if (closes.length === 0 && opens.length === 0 && holds.length === 0) {
      html += '<div class="tmc-empty-state"><div class="tmc-empty-icon">&#9989;</div>' +
        '<div class="tmc-empty-text">Portfolio is balanced — no actions suggested</div></div>';
    }

    grid.innerHTML = html;

    // Bind close buttons
    _bindCloseButtons(grid);
    // Bind preview buttons
    _bindPreviewButtons(grid);
  }

  /* -- Close action card ---------------------------------------------- */

  function _renderCloseAction(a, idx) {
    var isClose = a.action !== 'REDUCE';
    var typeClass = isClose ? 'close' : 'reduce';
    var labelText = isClose ? 'CLOSE' : 'REDUCE';

    var card = '<div class="tmc-balance-action-card tmc-balance-action-' + typeClass + '">';
    card += '<div class="tmc-balance-action-header">';
    card += '<span class="tmc-balance-action-label tmc-balance-label-' + typeClass + '">' + labelText + '</span>';
    card += '<span class="tmc-balance-action-symbol">' + esc(a.symbol || '—') + '</span>';
    if (a.strategy) {
      card += '<span class="tmc-balance-action-strategy">' + esc((a.strategy || '').replace(/_/g, ' ')) + '</span>';
    }
    card += '</div>';
    if (a.reason) {
      card += '<div class="tmc-balance-action-reason">' + esc(a.reason) + '</div>';
    }
    card += '<div class="tmc-balance-action-metrics">';
    card += '<span>Risk freed: $' + _fmtNum(a.risk_freed) + '</span>';
    if (a.delta_freed != null) {
      card += '<span>Delta freed: ' + _fmtDec(a.delta_freed, 4) + '</span>';
    }
    if (a.conviction != null) {
      card += '<span>Conviction: ' + Math.round(a.conviction) + '</span>';
    }
    if (a.trade_health_score != null) {
      card += '<span>Health: ' + Math.round(a.trade_health_score) + '</span>';
    }
    card += '</div>';
    // Close button — reuses executeActivePosition flow
    card += '<div class="tmc-balance-action-footer">';
    card += '<button class="btn tmc-btn tmc-btn-close tmc-pb-close-btn" '
      + 'data-close-idx="' + idx + '" '
      + 'data-symbol="' + esc(a.symbol || '') + '" '
      + 'data-strategy="' + esc(a.strategy || '') + '"'
      + '>Execute ' + labelText + '</button>';
    card += '</div>';
    card += '</div>';
    return card;
  }

  /* -- Open suggestion card ------------------------------------------- */

  function _renderOpenAction(a, idx) {
    var cand = a.candidate_data || {};
    var alignClass = a.regime_alignment === 'aligned' ? 'aligned' :
                     a.regime_alignment === 'neutral' ? 'neutral' : 'misaligned';

    var card = '<div class="tmc-balance-action-card tmc-balance-action-open">';
    card += '<div class="tmc-balance-action-header">';
    card += '<span class="tmc-balance-action-label tmc-balance-label-open">OPEN</span>';
    card += '<span class="tmc-balance-action-symbol">' + esc(a.symbol || '—') + '</span>';
    if (a.strategy) {
      card += '<span class="tmc-balance-action-strategy">' + esc((a.strategy || '').replace(/_/g, ' ')) + '</span>';
    }
    card += '<span class="tmc-pb-source-badge">' + esc(a.source || '') + '</span>';
    card += '<span class="tmc-pb-align-badge tmc-pb-align-' + alignClass + '">' + esc(a.regime_alignment || '') + '</span>';
    card += '</div>';
    card += '<div class="tmc-balance-action-metrics">';
    card += '<span>' + (a.contracts || 1) + 'x</span>';
    card += '<span>Risk: $' + _fmtNum(a.max_loss) + '</span>';
    if (a.ev != null) {
      card += '<span>EV: $' + _fmtDec(a.ev, 0) + '</span>';
    }
    if (a.ror != null) {
      card += '<span>RoR: ' + _fmtDec(a.ror * 100, 1) + '%</span>';
    }
    if (a.delta_impact != null) {
      card += '<span>Delta: ' + _fmtDec(a.delta_impact, 4) + '</span>';
    }
    card += '</div>';
    // Extra candidate detail line
    if (cand.scanner_key || cand.dte || cand.pop != null) {
      card += '<div class="tmc-balance-action-sub">';
      if (cand.scanner_key) card += '<span>' + esc(cand.scanner_key.replace(/_/g, ' ')) + '</span>';
      if (cand.dte != null)  card += '<span>DTE ' + cand.dte + '</span>';
      if (cand.pop != null)  card += '<span>PoP ' + _fmtDec(cand.pop * 100, 0) + '%</span>';
      if (cand.event_risk)   card += '<span class="tmc-pb-event-risk">' + esc(cand.event_risk) + '</span>';
      card += '</div>';
    }
    card += '<div class="tmc-balance-action-footer">';
    card += '<button class="btn tmc-btn tmc-btn-execute tmc-pb-preview-btn" '
      + 'data-preview-idx="' + idx + '" '
      + 'data-symbol="' + esc(a.symbol || '') + '" '
      + 'data-strategy="' + esc(a.strategy || '') + '"'
      + '>Preview Trade</button>';
    card += '</div>';
    card += '</div>';
    return card;
  }

  /* -- Policy row helper ---------------------------------------------- */

  function _policyRow(label, value) {
    return '<div class="tmc-pb-policy-row"><span>' + label + '</span><span>' + value + '</span></div>';
  }

  /* -- Bind close buttons to executeActivePosition -------------------- */

  function _bindCloseButtons(container) {
    var buttons = container.querySelectorAll('.tmc-pb-close-btn');
    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var idx = parseInt(btn.dataset.closeIdx, 10);
        if (!_lastBalanceResult || !_lastBalanceResult.rebalance_plan) return;
        var closes = _lastBalanceResult.rebalance_plan.close_actions || [];
        var action = closes[idx];
        if (!action) return;
        // Build a rec shape compatible with executeActivePosition
        var rec = {
          symbol: action.symbol,
          strategy: action.strategy,
          recommendation: action.action,
          conviction: action.conviction,
          rationale_summary: action.reason || '',
          suggested_next_move: '',
          dte: null,
          position_snapshot: {},
        };
        // If close_order is present, attach it
        if (action.close_order) {
          rec.close_order = action.close_order;
        }
        executeActivePosition(rec, 'close');
      });
    });
  }

  /* -- Bind preview buttons to TradeTicket ---------------------------- */

  function _bindPreviewButtons(container) {
    var buttons = container.querySelectorAll('.tmc-pb-preview-btn');
    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var idx = parseInt(btn.dataset.previewIdx, 10);
        if (!_lastBalanceResult || !_lastBalanceResult.rebalance_plan) return;
        var opens = _lastBalanceResult.rebalance_plan.open_actions || [];
        var action = opens[idx];
        if (!action) return;
        var tradeData = {
          underlying: action.symbol,
          symbol: action.symbol,
          strategyId: action.strategy,
          strategyLabel: (action.strategy || '').replace(/_/g, ' '),
          quantity: action.contracts || 1,
          orderType: 'limit',
          tif: 'day',
          action: 'execute',
          source: action.source || 'portfolio_balance',
        };
        if (window.TradeTicket && typeof window.TradeTicket.open === 'function') {
          window.TradeTicket.open(tradeData);
        } else {
          console.warn('[TMC] TradeTicket not available -- trade data:', tradeData);
          alert('Trade Ticket module not loaded. Trade data logged to console.');
        }
      });
    });
  }

  /* -- Formatting helpers --------------------------------------------- */

  function _fmtNum(v) {
    if (v == null) return '—';
    var n = Number(v);
    if (isNaN(n)) return '—';
    return n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }

  function _fmtDec(v, decimals) {
    if (v == null) return '—';
    var n = Number(v);
    if (isNaN(n)) return '—';
    return n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }

  function _fmtInt(v) {
    if (v == null) return '—';
    return String(Math.round(Number(v)));
  }

  /* -- Page init ----------------------------------------------------- */

  function initTradeManagementCenter(viewEl) {
    if (!viewEl) return;

    // Workflow trigger buttons
    var runStockBtn     = document.getElementById('tmcRunStock');
    var runOptionsBtn   = document.getElementById('tmcRunOptions');
    var refreshBtn      = document.getElementById('tmcRefreshBtn');
    var fullRefreshBtn  = document.getElementById('tmcFullRefreshBtn');

    if (runStockBtn) {
      runStockBtn.addEventListener('click', function () { triggerStockRun(); });
    }
    if (runOptionsBtn) {
      runOptionsBtn.addEventListener('click', function () { triggerOptionsRun(); });
    }
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        loadStockOpportunities();
        loadOptionsOpportunities();
      });
    }
    if (fullRefreshBtn) {
      fullRefreshBtn.addEventListener('click', function () { handleFullRefresh(); });
    }

    // Account mode toggle
    var accountToggle = document.getElementById('tmcAccountToggle');
    if (accountToggle) {
      accountToggle.querySelectorAll('.active-account-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
          accountToggle.querySelectorAll('.active-account-btn').forEach(function (b) {
            b.classList.remove('is-active');
          });
          btn.classList.add('is-active');
          _tmcAccountMode = btn.dataset.mode || 'paper';
        });
      });
    }

    // Active trade controls
    var runActiveBtn     = document.getElementById('tmcRunActiveBtn');
    var refreshActiveBtn = document.getElementById('tmcRefreshActiveBtn');

    if (runActiveBtn) {
      runActiveBtn.addEventListener('click', function () { runActivePipeline(); });
    }
    if (refreshActiveBtn) {
      refreshActiveBtn.addEventListener('click', function () { loadLatestActiveResults(); });
    }

    // Load latest workflow outputs on page entry
    loadStockOpportunities();
    loadOptionsOpportunities();

    // Cleanup handler for SPA navigation
    window.BenTradeActiveViewCleanup = function () {
      if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
      _stopStockCompletionPoll();
      _stopOptionsCompletionPoll();
      _activeRunning = false;
      var metaEl = document.getElementById('tmcActiveRunMeta');
      if (metaEl) metaEl.remove();
    };
  }

  /* -- Expose for testing -------------------------------------------- */

  window._tmcInternals = {
    normalizeStockCandidate: normalizeStockCandidate,
    normalizeOptionsCandidate: normalizeOptionsCandidate,
    tmcStockToScannerShape: tmcStockToScannerShape,
    buildTmcEnrichmentHtml: buildTmcEnrichmentHtml,
    buildOptionsEnrichmentHtml: buildOptionsEnrichmentHtml,
    buildOptionsTradeCard: buildOptionsTradeCard,
    buildActiveTradeCard: buildActiveTradeCard,
    buildActiveEnrichmentHtml: buildActiveEnrichmentHtml,
    renderTmcFinalDecisionResult: renderTmcFinalDecisionResult,
    getStatusInfo: getStatusInfo,
    TMC_STATUS_MAP: TMC_STATUS_MAP,
  };

  /* -- Register ------------------------------------------------------ */

  window.BenTradePages = window.BenTradePages || {};
  window.BenTradePages.initTradeManagementCenter = initTradeManagementCenter;
})();
