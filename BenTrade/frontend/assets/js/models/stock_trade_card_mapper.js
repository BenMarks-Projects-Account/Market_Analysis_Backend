/**
 * BenTrade — Stock Trade Card Mapper (shared utility).
 *
 * Converts a raw stock strategy candidate into the flat trade shape
 * that BenTradeOptionTradeCardModel.map() and renderFullCard() expect.
 *
 * ALL four stock strategy dashboards call these functions instead of
 * duplicating their own candidateToTradeShape / buildDerivedData.
 *
 * Trade key format:  SYMBOL|STOCK|strategy_id|NA|NA|NA
 *
 * Depends on:
 *   - BenTradeUtils.format          (toNumber)
 *   - BenTradeOptionTradeCardModel  (map)
 *   - BenTradeTradeCard             (renderFullCard)
 */
window.BenTradeStockTradeCardMapper = (function () {
  'use strict';

  /* ================================================================
   *  buildStockTradeKey
   *
   *  Canonical stock trade key:  SYMBOL|STOCK|strategy_id|NA|NA|NA
   *
   *  Input fields: symbol (string), strategyId (string)
   *  Formula:      UPPER(symbol) + '|STOCK|' + strategyId + '|NA|NA|NA'
   * ================================================================ */

  function buildStockTradeKey(symbol, strategyId) {
    var sym = String(symbol || '').trim().toUpperCase();
    var sid = String(strategyId || '').trim();
    if (!sym || !sid) return '';
    return sym + '|STOCK|' + sid + '|NA|NA|NA';
  }

  /* ================================================================
   *  candidateToTradeShape
   *
   *  Flattens a backend stock-strategy candidate object into the
   *  root-level shape that the 4-tier metric resolver inside
   *  BenTradeOptionTradeCardModel.map() can traverse via rootFallbacks.
   *
   *  The flattening is generic — every key on `row` and every key in
   *  `row.metrics` is copied to root level.  Strategy-specific field
   *  selection is handled by strategy_card_config.js, NOT here.
   *
   *  @param {object} row         – raw candidate from the scanner API
   *  @param {string} strategyId  – e.g. 'stock_pullback_swing'
   *  @returns {object} flat trade object ready for the mapper
   * ================================================================ */

  function candidateToTradeShape(row, strategyId) {
    if (!row || typeof row !== 'object') {
      return { symbol: '', strategy_id: strategyId, trade_key: '' };
    }

    var symbol  = String(row.symbol || '').toUpperCase();
    var metrics = (row.metrics && typeof row.metrics === 'object') ? row.metrics : {};

    /* Core identity fields */
    var shape = {
      symbol:           symbol,
      strategy_id:      strategyId,
      trade_key:        row.trade_key || buildStockTradeKey(symbol, strategyId),
      price:            row.price != null ? row.price : null,
      underlying_price: row.price != null ? row.price : null,
      composite_score:  row.composite_score != null ? row.composite_score : null,
      rank_score:       row.composite_score != null ? row.composite_score : null,
      _scanner_candidate: row,
    };

    /* Flatten root-level fields (scores, states, etc.) */
    var skipRoot = { symbol: 1, metrics: 1, thesis: 1, data_source: 1, confidence: 1, score_breakdown: 1 };
    var rootKeys = Object.keys(row);
    for (var i = 0; i < rootKeys.length; i++) {
      var k = rootKeys[i];
      if (!skipRoot[k] && shape[k] === undefined) {
        shape[k] = row[k];
      }
    }

    /* Flatten metrics sub-object to root (rootFallbacks resolution) */
    var metricKeys = Object.keys(metrics);
    for (var j = 0; j < metricKeys.length; j++) {
      var mk = metricKeys[j];
      if (shape[mk] === undefined) {
        shape[mk] = metrics[mk];
      }
    }

    return shape;
  }

  /* ================================================================
   *  buildDerivedData
   *
   *  Builds the diagnostic/derived payload used by Data Workbench.
   *  Generic across all stock strategies.
   *
   *  @param {object} rawRow      – original candidate from API
   *  @param {object} model       – mapper model (from .map())
   *  @param {string} strategyId  – strategy identifier
   *  @returns {object}
   * ================================================================ */

  function buildDerivedData(rawRow, model, strategyId) {
    var metrics = (rawRow && rawRow.metrics && typeof rawRow.metrics === 'object') ? rawRow.metrics : {};

    /* Collect all *_score fields from root level as scoring_outputs */
    var scoringOutputs = {};
    if (rawRow && typeof rawRow === 'object') {
      var keys = Object.keys(rawRow);
      for (var i = 0; i < keys.length; i++) {
        var k = keys[i];
        if (k === 'composite_score' || (k.indexOf('_score') !== -1 && typeof rawRow[k] === 'number')) {
          scoringOutputs[k] = rawRow[k];
        }
      }
    }

    return {
      source: strategyId,
      scoring_inputs: _shallowCopy(metrics),
      scoring_outputs: scoringOutputs,
      mapper_diagnostics: model ? {
        missingKeys:   model.missingKeys || [],
        hasAllRequired: model.hasAllRequired,
        coreResolved:  _filterMetricKeys(model.coreMetrics, true),
        coreMissing:   _filterMetricKeys(model.coreMetrics, false),
      } : null,
      thesis:      (rawRow && Array.isArray(rawRow.thesis)) ? rawRow.thesis : [],
      data_source: (rawRow && rawRow.data_source) || null,
    };
  }

  /* ================================================================
   *  renderStockCard
   *
   *  Renders a single stock candidate as a full TradeCard, identical
   *  to the options card layout, plus an appended Stock Analysis button.
   *
   *  Uses renderFullCard() from BenTradeTradeCard as the canonical
   *  renderer, then injects the stock-specific action row.
   *
   *  @param {object} row          – raw candidate from scanner API
   *  @param {number} idx          – card index
   *  @param {string} strategyId   – e.g. 'stock_pullback_swing'
   *  @param {object} [expandState]– { tradeKey: boolean }
   *  @returns {string} HTML
   * ================================================================ */

  function renderStockCard(row, idx, strategyId, expandState) {
    var cardMod = window.BenTradeTradeCard;
    var tradeObj = candidateToTradeShape(row, strategyId);
    var tk = tradeObj.trade_key || '';
    var esc = (window.BenTradeUtils && window.BenTradeUtils.format && window.BenTradeUtils.format.escapeHtml)
      ? window.BenTradeUtils.format.escapeHtml
      : function (v) { return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); };

    if (!cardMod || !cardMod.renderFullCard) {
      /* Minimal fallback if renderFullCard is unavailable */
      return '<div class="trade-card" data-idx="' + idx + '" style="margin-bottom:12px;padding:14px;border:1px solid rgba(0,234,255,0.15);border-radius:10px;background:rgba(8,18,26,0.9);">'
        + '<div style="font-weight:700;font-size:14px;color:rgba(215,251,255,0.95);">#' + (row.rank || idx + 1) + ' ' + esc(row.symbol) + '</div>'
        + '<div style="font-size:12px;color:rgba(190,236,244,0.65);margin-top:4px;">Score: ' + _fmtFallback(row.composite_score, 1) + ' | $' + _fmtFallback(row.price, 2) + '</div>'
        + '</div>';
    }

    /* Stock Analysis extra button injected after Data Workbench row */
    var stockAnalysisHtml = '<div class="actions-row">'
      + '<button type="button" class="btn btn-action" data-action="stock-analysis"'
      + ' data-trade-key="' + esc(tk) + '"'
      + ' data-symbol="' + esc(tradeObj.symbol) + '"'
      + ' title="Open in Stock Analysis">Open in Stock Analysis</button>'
      + '</div>';

    return cardMod.renderFullCard(tradeObj, idx, {
      strategyHint: strategyId,
      expandState: expandState || {},
      extraActionsHtml: stockAnalysisHtml,
    });
  }

  /* ================================================================
   *  openDataWorkbenchForStock
   *
   *  Opens the Data Workbench modal for a stock candidate.
   *  Shared across all stock strategy dashboards.
   *
   *  @param {object} rawRow      – original candidate from API
   *  @param {string} strategyId  – strategy identifier
   * ================================================================ */

  function openDataWorkbenchForStock(rawRow, strategyId) {
    var tradeObj = candidateToTradeShape(rawRow, strategyId);
    var mapper   = window.BenTradeOptionTradeCardModel;
    var model    = (mapper && typeof mapper.map === 'function') ? mapper.map(tradeObj, strategyId) : null;
    var derived  = buildDerivedData(rawRow, model, strategyId);
    var modal    = window.BenTradeDataWorkbenchModal;
    if (modal && modal.open) {
      modal.open({
        symbol:     tradeObj.symbol || '',
        normalized: tradeObj,
        rawSource:  rawRow,
        derived:    derived,
      });
    } else {
      console.warn('[StockTradeCardMapper] Data Workbench modal not available');
    }
  }

  /* ================================================================
   *  openStockAnalysis  —  navigate to stock analysis page.
   * ================================================================ */

  function openStockAnalysis(symbol) {
    var s = String(symbol || '').trim().toUpperCase();
    if (!s) return;
    localStorage.setItem('bentrade_selected_symbol', s);
    location.hash = '#stock-analysis';
  }

  /* ── Private helpers ─────────────────────────────────────────── */

  function _shallowCopy(obj) {
    if (!obj || typeof obj !== 'object') return {};
    var out = {};
    var keys = Object.keys(obj);
    for (var i = 0; i < keys.length; i++) {
      out[keys[i]] = obj[keys[i]];
    }
    return out;
  }

  function _filterMetricKeys(metrics, resolved) {
    if (!Array.isArray(metrics)) return [];
    var out = [];
    for (var i = 0; i < metrics.length; i++) {
      var hasVal = metrics[i].value !== null;
      if (resolved ? hasVal : !hasVal) {
        out.push(metrics[i].key);
      }
    }
    return out;
  }

  function _fmtFallback(v, d) {
    return v != null ? Number(v).toFixed(d || 0) : '—';
  }


  /* ================================================================
   *  runModelAnalysisForStock
   *
   *  Shared handler for the "Run Model Analysis" button on stock
   *  strategy TradeCards.  Follows the same flow as the options
   *  analysis handler in strategy_dashboard_shell.js:
   *
   *    1. Check cache — if already running, dedupe.
   *    2. Mark running in BenTradeModelAnalysisStore.
   *    3. Update button + output slot to loading state.
   *    4. POST /api/model/analyze_stock_strategy with the candidate.
   *    5. Adapt response to BenTradeModelAnalysis.parse() shape.
   *    6. Store success/error in BenTradeModelAnalysisStore.
   *    7. Render into the output slot.
   *
   *  @param {HTMLElement} btn           — the clicked button
   *  @param {string}      tradeKey      — canonical trade key
   *  @param {object}      rawCandidate  — full candidate from scanner API
   *  @param {string}      strategyId    — e.g. 'stock_pullback_swing'
   * ================================================================ */

  function runModelAnalysisForStock(btn, tradeKey, rawCandidate, strategyId) {
    var api = window.BenTradeApi;
    var modelUI = window.BenTradeModelAnalysis;
    var modelStore = window.BenTradeModelAnalysisStore;

    if (!api || !api.modelAnalyzeStockStrategy) {
      console.error('[StockTradeCardMapper] BenTradeApi.modelAnalyzeStockStrategy not available');
      return;
    }

    /* ── 1. Dedupe guard ── */
    if (tradeKey && modelStore) {
      var existing = modelStore.get(tradeKey);
      if (existing && existing.status === 'running') {
        console.debug('[StockTradeCardMapper] dedupe guard — already running for', tradeKey);
        return;
      }
    }

    /* ── 2. Mark running ── */
    if (tradeKey && modelStore) {
      modelStore.setRunning(tradeKey);
    }

    /* ── 3. Update UI to loading state ── */
    var cardEl = btn ? btn.closest('.trade-card') : null;
    var outputEl = cardEl ? cardEl.querySelector('[data-model-output]') : null;

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="home-scan-spinner" aria-hidden="true" style="margin-right:4px;"></span>Running\u2026';
    }

    if (outputEl && modelUI) {
      outputEl.style.display = 'block';
      outputEl.innerHTML = modelUI.render(modelUI.parse({ status: 'running' }));
    }

    /* ── 4. Call the API ── */
    api.modelAnalyzeStockStrategy(strategyId, rawCandidate)
      .then(function (result) {
        var analysis = (result && result.analysis) || {};

        /* ── 5. Adapt to BenTradeModelAnalysis.parse() shape ──
         *
         * The stock strategy analysis returns:
         *   { recommendation, score, confidence, summary, key_drivers, risk_review,
         *     engine_vs_model, data_quality, timestamp }
         *
         * BenTradeModelAnalysis.parse() expects a model_evaluation-like object.
         * We bridge by mapping fields to the expected shape:
         *   model_recommendation → recommendation (BUY/PASS)
         *   score_0_100          → score
         *   confidence_0_1       → confidence / 100
         *   thesis               → summary
         *   key_drivers          → key_drivers (already structured)
         *   risk_review          → risk_review
         */
        var bridged = {
          status: 'success',
          model_evaluation: {
            model_recommendation: analysis.recommendation || 'PASS',
            recommendation: analysis.recommendation || 'PASS',
            score_0_100: analysis.score != null ? analysis.score : null,
            confidence_0_1: analysis.confidence != null ? analysis.confidence / 100 : null,
            thesis: analysis.summary || '',
            key_drivers: analysis.key_drivers || [],
            risk_review: {
              primary_risks: (analysis.risk_review && analysis.risk_review.primary_risks) || [],
              volatility_risk: (analysis.risk_review && analysis.risk_review.volatility_risk) || null,
              timing_risk: (analysis.risk_review && analysis.risk_review.timing_risk) || null,
              data_quality_flag: (analysis.risk_review && analysis.risk_review.data_quality_flag) || null,
            },
            data_quality_flags: (analysis.data_quality && analysis.data_quality.warnings) || [],
            missing_data: [],
          },
          /* Engine vs Model comparison → engine_calculations for the renderer */
          engine_calculations: analysis.engine_vs_model ? {
            engine_score: analysis.engine_vs_model.engine_score,
            model_score: analysis.engine_vs_model.model_score,
          } : null,
        };

        var parsed = modelUI ? modelUI.parse(bridged) : bridged;

        /* Inject engine-vs-model comparison into parsed result for custom rendering */
        if (analysis.engine_vs_model) {
          parsed._engine_vs_model = analysis.engine_vs_model;
        }

        /* ── 6. Store ── */
        if (tradeKey && modelStore) {
          modelStore.setSuccess(tradeKey, parsed);
        }

        /* ── 7. Render ── */
        if (outputEl && modelUI) {
          outputEl.style.display = 'block';
          var html = modelUI.render(parsed);

          /* Append engine-vs-model comparison section if present */
          if (analysis.engine_vs_model) {
            html += _renderEngineVsModelSection(analysis.engine_vs_model);
          }

          outputEl.innerHTML = html;
        }

        /* Reset button */
        if (btn) {
          btn.disabled = false;
          var ts = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          btn.innerHTML = '\u21BB Re-run Analysis <span style="font-size:9px;color:var(--muted);margin-left:4px;">' + ts + '</span>';
        }
      })
      .catch(function (err) {
        var errMsg = (err && err.message) || 'Model analysis failed';
        console.error('[StockTradeCardMapper] model analysis error:', err);

        /* Store error */
        if (tradeKey && modelStore) {
          modelStore.setError(tradeKey, errMsg);
        }

        /* Render error */
        if (outputEl && modelUI) {
          outputEl.style.display = 'block';
          outputEl.innerHTML = modelUI.render(modelUI.parse({ status: 'error', summary: errMsg }));
        }

        /* Reset button */
        if (btn) {
          btn.disabled = false;
          btn.textContent = 'Run Model Analysis';
        }
      });
  }


  /* ── Engine vs Model comparison section renderer ─────────────── */

  function _renderEngineVsModelSection(evm) {
    if (!evm || typeof evm !== 'object') return '';

    var esc = (window.BenTradeUtils && window.BenTradeUtils.format && window.BenTradeUtils.format.escapeHtml)
      ? window.BenTradeUtils.format.escapeHtml
      : function (v) { return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); };

    var agreeColor = {
      'agree': '#00dc78',
      'disagree': '#ff5a5a',
      'mixed': '#ffc83c',
    };
    var color = agreeColor[evm.agreement] || '#b4b4c8';

    var html = '<div style="margin-top:8px;padding:6px 8px;background:rgba(100,149,237,0.04);border-radius:6px;border:1px solid rgba(100,149,237,0.15);">';
    html += '<div style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px;">Engine vs Model</div>';

    /* Score comparison row */
    html += '<div style="display:flex;gap:16px;font-size:11px;margin-bottom:4px;">';
    if (evm.engine_score != null) {
      html += '<span style="color:var(--text-secondary,#bbb);">Engine: <b>' + Number(evm.engine_score).toFixed(1) + '</b></span>';
    }
    if (evm.model_score != null) {
      html += '<span style="color:var(--text-secondary,#bbb);">Model: <b>' + Number(evm.model_score).toFixed(1) + '</b></span>';
    }
    html += '<span style="font-size:10px;padding:1px 6px;border-radius:3px;border:1px solid ' + color + '44;color:' + color + ';font-weight:600;">' + esc(String(evm.agreement || 'mixed').toUpperCase()) + '</span>';
    html += '</div>';

    /* Notes */
    var notes = evm.notes || [];
    if (notes.length) {
      for (var i = 0; i < notes.length; i++) {
        html += '<div style="font-size:10px;color:var(--text-secondary,#bbb);line-height:1.4;padding-left:8px;border-left:2px solid ' + color + ';margin-bottom:2px;">' + esc(notes[i]) + '</div>';
      }
    }

    html += '</div>';
    return html;
  }


  /* ================================================================
   *  executeStockTrade
   *
   *  Opens the stock execution modal for a candidate.
   *  Called from the 4 stock strategy dashboards when the user
   *  clicks "Execute Trade".
   *
   *  @param {HTMLElement} btn          – the clicked button
   *  @param {string}      tradeKey     – canonical trade key
   *  @param {object}      rawCandidate – full candidate from scanner API
   *  @param {string}      strategyId   – e.g. 'stock_pullback_swing'
   * ================================================================ */

  function executeStockTrade(btn, tradeKey, rawCandidate, strategyId) {
    var modal = window.BenTradeStockExecuteModal;
    if (!modal || !modal.open) {
      console.error('[StockTradeCardMapper] BenTradeStockExecuteModal not available');
      return;
    }
    modal.open(rawCandidate, strategyId, tradeKey);
  }


  /* ── Public API ──────────────────────────────────────────────── */

  return {
    buildStockTradeKey:           buildStockTradeKey,
    candidateToTradeShape:        candidateToTradeShape,
    buildDerivedData:             buildDerivedData,
    renderStockCard:              renderStockCard,
    openDataWorkbenchForStock:    openDataWorkbenchForStock,
    openStockAnalysis:            openStockAnalysis,
    runModelAnalysisForStock:     runModelAnalysisForStock,
    executeStockTrade:            executeStockTrade,
  };
})();
