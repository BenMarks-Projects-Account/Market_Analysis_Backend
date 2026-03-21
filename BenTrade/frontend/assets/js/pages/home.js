window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initHome = function initHome(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const api = window.BenTradeApi;

  /*
   * Do NOT add selectors for Source Health, Session Stats, or Strategy Leaderboard here.
   * Those are GLOBAL-ONLY panels rendered in the global right info bar (index.html / sessionStats.js).
   */
  const regimeStripEl = scope.querySelector('#homeRegimeStrip');
  const regimeBlocksEl = scope.querySelector('#homeRegimeBlocks');
  const regimeInsightsEl = scope.querySelector('#homeRegimeInsights');
  // playbookChipsEl removed — regime right-side now shows model analysis directly
  const scanPresetEl = scope.querySelector('#homeScanPreset');           // null — OE removed from home
  const runQueueBtnEl = scope.querySelector('#homeRunQueueBtn');         // null — OE removed from home
  const stopQueueBtnEl = scope.querySelector('#homeStopQueueBtn');      // null — removed
  const queueProgressEl = scope.querySelector('#homeQueueProgress');    // null — OE removed from home
  const queueCurrentEl = scope.querySelector('#homeQueueCurrent');      // null — OE removed from home
  const queueCountEl = scope.querySelector('#homeQueueCount');          // null — OE removed from home
  const queueSpinnerEl = scope.querySelector('#homeQueueSpinner');      // null — OE removed from home
  const queueLogEl = scope.querySelector('#homeQueueLog');              // null — OE removed from home
  const scanStatusEl = scope.querySelector('#homeScanStatus');          // null — OE removed from home
  const scanErrorEl = scope.querySelector('#homeScanError');            // null — OE removed from home
  const sectorContextEl = scope.querySelector('#homeSectorContext');
  const indexTilesEl = scope.querySelector('#homeIndexTiles');
  const scoreboardCardsEl = scope.querySelector('#homeScoreboardCards');
  const spyChartEl = scope.querySelector('#homeSpyChart');
  const sectorBarsEl = scope.querySelector('#homeSectorBars');
  const scannerOpportunitiesEl = scope.querySelector('#homeScannerOpportunities'); // null — OE removed
  const symbolUniverseEl = scope.querySelector('#homeSymbolUniverse');  // null — OE removed from home
  const riskTilesEl = scope.querySelector('#homeRiskTiles');
  const macroTilesEl = scope.querySelector('#homeMacroTiles');
  const strategyPlaybookEl = scope.querySelector('#homeStrategyPlaybook');
  const refreshBtnEl = scope.querySelector('#homeRefreshBtn');
  const refreshingBadgeEl = scope.querySelector('#homeRefreshingBadge');
  const lastUpdatedEl = scope.querySelector('#homeLastUpdated');
  const vixChartEl = scope.querySelector('#homeVixChart');
  const diaChartEl = scope.querySelector('#homeDiaChart');
  const qqqChartEl = scope.querySelector('#homeQqqChart');
  const iwmChartEl = scope.querySelector('#homeIwmChart');
  const mdyChartEl = scope.querySelector('#homeMdyChart');
  const errorEl = scope.querySelector('#homeError');
  // regimeModelBtnEl removed — model analysis auto-runs on every refresh
  const regimeComparisonEl = scope.querySelector('#homeRegimeComparisonTable');
  const regimeModelOutputEl = scope.querySelector('#homeRegimeModelOutput');
  const activeTradesCountEl = scope.querySelector('#homeActiveTradesCount');
  const equityCurveEl = scope.querySelector('#homeEquityCurve');
  const equityCurveEmptyEl = scope.querySelector('#homeEquityCurveEmpty');
  const clearScanResultsBtnEl = scope.querySelector('#homeClearScanResultsBtn'); // null — OE removed
  const scanLastRunEl = scope.querySelector('#homeScanLastRun');                 // null — OE removed

  /* ── Stock Engine DOM references (removed from home layout) ── */
  const stockEngineRunBtnEl = scope.querySelector('#homeStockEngineRunBtn');       // null
  const stockEngineLastUpdatedEl = scope.querySelector('#homeStockEngineLastUpdated'); // null
  const stockEngineWarningEl = scope.querySelector('#homeStockEngineWarning');     // null
  const stockEngineLoadingEl = scope.querySelector('#homeStockEngineLoading');     // null
  const stockEngineErrorEl = scope.querySelector('#homeStockEngineError');         // null
  const stockEngineCandidatesEl = scope.querySelector('#homeStockEngineCandidates'); // null

  /* ── Strategy Playbooks — new subsection refs ── */
  const stockStrategyPlaybookEl = scope.querySelector('#homeStockStrategyPlaybook');
  const optionsStrategyPlaybookEl = scope.querySelector('#homeOptionsStrategyPlaybook');

  /* ── Market Picture History refs ── */
  const mpHistoryEmptyEl = scope.querySelector('#homeMPHistoryEmpty');
  const mpHistoryChartEl = scope.querySelector('#homeMPHistoryChart');
  const mpHistorySvgEl = scope.querySelector('#homeMPHistorySvg');
  const mpHistoryLegendEl = scope.querySelector('#homeMPHistoryLegend');

  /* ── Regime proxy charts ref ── */
  const regimeProxiesEl = scope.querySelector('#homeRegimeProxies');

  /* ── Contextual Chat button ── */
  const regimeChatBtnEl = scope.querySelector('#homeRegimeChatBtn');
  if (regimeChatBtnEl) {
    regimeChatBtnEl.addEventListener('click', _onRegimeChatClick);
    // Start disabled — enabled once regime data arrives
    regimeChatBtnEl.disabled = true;
    regimeChatBtnEl.title = 'Waiting for regime data…';
  }

  /* Guard: only require elements that are actually in the new layout */
  if(!regimeStripEl || !indexTilesEl || !spyChartEl || !sectorBarsEl || !riskTilesEl || !macroTilesEl || !refreshBtnEl || !refreshingBadgeEl || !lastUpdatedEl || !vixChartEl || !errorEl){
    return;
  }

  let latestOpportunities = [];
  const _modelStore = window.BenTradeModelAnalysisStore;
  const _modelUI = window.BenTradeModelAnalysis;
  const devLoggedCards = new Set();

  /* ── Scan Results Cache helpers (shared sessionStorage) ── */
  const _scanCache = window.BenTradeScanResultsCache;
  const SCAN_CACHE_ID = 'stockScanner';

  function updateHomeScanCacheUI(){
    if(_scanCache){
      const hasCached = _scanCache.load(SCAN_CACHE_ID) !== null;
      if(clearScanResultsBtnEl){
        clearScanResultsBtnEl.style.display = hasCached ? 'inline-block' : 'none';
      }
      if(scanLastRunEl){
        const ts = _scanCache.formatTimestamp(SCAN_CACHE_ID);
        scanLastRunEl.textContent = ts !== 'N/A' ? 'Last run: ' + ts : '';
      }
    } else {
      if(clearScanResultsBtnEl) clearScanResultsBtnEl.style.display = 'none';
      if(scanLastRunEl) scanLastRunEl.textContent = '';
    }
  }

  function clearHomeScanResults(){
    if(_scanCache) _scanCache.clear(SCAN_CACHE_ID);
    // Also clear orchestrator in-memory results
    const orchestrator = window.BenTradeScannerOrchestrator;
    if(orchestrator?.clearResults) orchestrator.clearResults();
    // Clear the home cache opportunities so next render shows empty
    const snap = cacheStore?.getSnapshot?.();
    if(snap && typeof snap === 'object'){
      const data = (snap.data && typeof snap.data === 'object') ? { ...snap.data } : {};
      data.opportunities = [];
      cacheStore.setSnapshot({ ...snap, data });
    }
    latestOpportunities = [];
    if(scannerOpportunitiesEl) scannerOpportunitiesEl.innerHTML = '';
    renderScannerOpportunities([]);
    updateHomeScanCacheUI();
    setScanStatus('');
    setScanError('');
    console.debug('Home: cleared scan results cache');
  }

  /* ═════════════════════════════════════════════════════════════
     Stock Engine — runs all stock scanners via /api/stocks/engine,
     displays the top 9 candidates (server-side ranked).
     ═════════════════════════════════════════════════════════════ */
  const STOCK_ENGINE_CACHE_ID = 'stockEngine';
  let _stockEngineRunning = false;
  let _stockEngineExpandState = {};
  let _stockEngineRenderedRows = [];

  /**
   * Render a single stock candidate as a stock trade card.
   * Delegates to BenTradeStockTradeCardMapper.renderStockCard
   * (same card used on individual stock strategy pages).
   */
  function renderStockEngineCards(candidates){
    if(!stockEngineCandidatesEl) return;
    const mapper = window.BenTradeStockTradeCardMapper;
    if(!mapper){
      stockEngineCandidatesEl.innerHTML = '<div class="home-opp-empty"><div class="home-opp-empty-text">Stock card mapper unavailable.</div></div>';
      return;
    }

    if(!candidates || !candidates.length){
      stockEngineCandidatesEl.innerHTML = `
        <div class="home-opp-empty">
          <div class="home-opp-empty-icon" aria-hidden="true">◈</div>
          <div class="home-opp-empty-text">No stock opportunities yet — run a stock scan.</div>
          <button type="button" class="btn qtButton home-run-scan-btn" data-action="trigger-stock-scan">Run Stock Scan</button>
        </div>
      `;
      const triggerBtn = stockEngineCandidatesEl.querySelector('[data-action="trigger-stock-scan"]');
      if(triggerBtn) triggerBtn.addEventListener('click', () => runStockEngineScan());
      return;
    }

    _stockEngineRenderedRows = candidates;
    let html = `<div class="home-opp-count stock-note">${candidates.length} Stock Pick${candidates.length !== 1 ? 's' : ''}</div>`;
    const renderErrors = [];

    candidates.forEach(function(row, idx){
      try{
        const strategyId = String(row.strategy_id || 'stock_idea');
        html += mapper.renderStockCard(row, idx, strategyId, _stockEngineExpandState);
      }catch(cardErr){
        renderErrors.push({ idx, symbol: row?.symbol, error: cardErr.message });
        const esc = window.BenTradeUtils?.format?.escapeHtml || ((s) => String(s || ''));
        html += '<div class="trade-card" style="margin-bottom:12px;padding:10px;border:1px solid rgba(255,120,100,0.3);border-radius:10px;background:rgba(8,18,26,0.9);color:rgba(255,180,160,0.8);font-size:12px;">\u26A0 Render error for ' + esc(row?.symbol || '#' + idx) + '</div>';
      }
    });

    if(renderErrors.length){
      console.warn('[StockEngine] Card render errors:', renderErrors);
    }

    stockEngineCandidatesEl.innerHTML = html;

    /* Wire expand state persistence */
    stockEngineCandidatesEl.querySelectorAll('details.trade-card-collapse').forEach(function(details){
      details.addEventListener('toggle', function(){
        var tk = details.dataset.tradeKey || '';
        if(tk) _stockEngineExpandState[tk] = details.open;
      });
    });

    /* Wire action delegation */
    stockEngineCandidatesEl.addEventListener('click', function(e){
      var btn = e.target.closest('[data-action]');
      if(!btn) return;
      var action   = btn.dataset.action;
      var tradeKey = btn.dataset.tradeKey || '';
      var symbol   = btn.dataset.symbol || '';
      var row      = _findStockEngineRow(tradeKey);

      if(action === 'stock-analysis'){
        if(mapper.openStockAnalysis) mapper.openStockAnalysis(symbol || (row && row.symbol));
      } else if(action === 'data-workbench' && row){
        if(mapper.openDataWorkbenchForStock) mapper.openDataWorkbenchForStock(row, row.strategy_id || 'stock_idea');
      } else if(action === 'execute' && row){
        if(mapper.executeStockTrade) mapper.executeStockTrade(btn, tradeKey, row, row.strategy_id || 'stock_idea');
      } else if(action === 'model-analysis' && row){
        if(mapper.runModelAnalysisForStock) mapper.runModelAnalysisForStock(btn, tradeKey, row, row.strategy_id || 'stock_idea');
      }
    });

    /* Tooltips and model hydration */
    if(window.attachMetricTooltips) window.attachMetricTooltips(stockEngineCandidatesEl);
    if(window.BenTradeModelAnalysisStore?.hydrateContainer) window.BenTradeModelAnalysisStore.hydrateContainer(stockEngineCandidatesEl);
  }

  function _findStockEngineRow(tradeKey){
    if(!tradeKey) return null;
    const mapper = window.BenTradeStockTradeCardMapper;
    for(var i = 0; i < _stockEngineRenderedRows.length; i++){
      var row = _stockEngineRenderedRows[i];
      var rk = row.trade_key || (mapper ? mapper.buildStockTradeKey(row.symbol, row.strategy_id || 'stock_idea') : '');
      if(rk === tradeKey) return row;
    }
    return null;
  }

  function setStockEngineError(msg){
    if(!stockEngineErrorEl) return;
    stockEngineErrorEl.textContent = String(msg || '');
    stockEngineErrorEl.style.display = msg ? 'block' : 'none';
  }

  function setStockEngineWarning(msg){
    if(!stockEngineWarningEl) return;
    stockEngineWarningEl.textContent = String(msg || '');
    stockEngineWarningEl.style.display = msg ? 'inline' : 'none';
  }

  function setStockEngineLastUpdated(iso){
    if(!stockEngineLastUpdatedEl) return;
    const parsed = iso ? new Date(iso) : null;
    const text = parsed && !Number.isNaN(parsed.getTime()) ? parsed.toLocaleTimeString() : '--';
    stockEngineLastUpdatedEl.textContent = 'Last updated: ' + text;
  }

  function saveStockEngineCache(payload){
    if(_scanCache) _scanCache.save(STOCK_ENGINE_CACHE_ID, payload, { endpoint: '/api/stocks/engine' });
  }

  function loadStockEngineCache(){
    if(!_scanCache) return null;
    var entry = _scanCache.load(STOCK_ENGINE_CACHE_ID);
    return entry ? entry.payload : null;
  }

  /**
   * Run the Stock Engine scan — calls backend /api/stocks/engine.
   * The backend runs all 4 stock scanners concurrently and returns
   * the top 9 candidates pre-ranked server-side.
   */
  async function runStockEngineScan(){
    if(_stockEngineRunning) return;
    if(!api?.getStockEngine){
      setStockEngineError('Stock Engine API not available');
      return;
    }

    _stockEngineRunning = true;
    if(stockEngineRunBtnEl){
      stockEngineRunBtnEl.disabled = true;
      stockEngineRunBtnEl.textContent = '⟳ Scanning…';
    }
    if(stockEngineLoadingEl) stockEngineLoadingEl.style.display = 'flex';
    setStockEngineError('');
    setStockEngineWarning('');

    try{
      const payload = await api.getStockEngine();
      const candidates = Array.isArray(payload?.candidates) ? payload.candidates : [];
      const warnings = Array.isArray(payload?.warnings) ? payload.warnings : [];
      const scanners = Array.isArray(payload?.scanners) ? payload.scanners : [];

      /* ── Console diagnostic: per-scanner breakdown ── */
      const scannerSummary = scanners.map(s =>
        `${s.strategy_id}: ${s.candidates_count}c, max=${s.max_composite_score ?? '?'}, status=${s.status}`
      ).join(' | ');
      const topScores = candidates.slice(0, 15).map(c =>
        `${c.symbol}@${(c.composite_score ?? 0).toFixed?.(1) ?? c.composite_score}(${c.strategy_id})`
      ).join(', ');
      console.info(
        '[StockEngine] Aggregation: total=' + (payload?.total_candidates ?? '?')
        + ' top_n=' + candidates.length
        + ' | Scanners: ' + scannerSummary
        + ' | Top: ' + topScores
      );

      renderStockEngineCards(candidates);
      setStockEngineLastUpdated(payload?.as_of || new Date().toISOString());
      saveStockEngineCache(payload);

      /* ── Scanner breakdown warning if any scanner failed/skipped ── */
      const failedScanners = scanners.filter(s => s.status !== 'ok');
      if(failedScanners.length > 0){
        const failMsg = failedScanners.map(s => s.strategy_id.replace('stock_','') + '(' + s.status + ')').join(', ');
        setStockEngineWarning('Scanner issues: ' + failMsg);
        console.warn('[StockEngine] Failed/skipped scanners:', failedScanners);
      } else if(warnings.length){
        setStockEngineWarning(warnings.length + ' scanner warning' + (warnings.length !== 1 ? 's' : '') + ': ' + warnings[0]);
        console.warn('[StockEngine] Warnings:', warnings);
      }
    }catch(err){
      console.error('[StockEngine] Scan error:', err);
      setStockEngineError('Stock scan failed: ' + String(err?.message || err || 'unknown error'));
    }finally{
      _stockEngineRunning = false;
      if(stockEngineRunBtnEl){
        stockEngineRunBtnEl.disabled = false;
        stockEngineRunBtnEl.textContent = 'Run Stock Scan';
      }
      if(stockEngineLoadingEl) stockEngineLoadingEl.style.display = 'none';
    }
  }

  /* Boot: restore stock engine from session cache if available */
  (function bootStockEngine(){
    const cached = loadStockEngineCache();
    if(cached && Array.isArray(cached.candidates) && cached.candidates.length > 0){
      renderStockEngineCards(cached.candidates);
      setStockEngineLastUpdated(cached.as_of || null);
      if(Array.isArray(cached.warnings) && cached.warnings.length){
        setStockEngineWarning(cached.warnings.length + ' scanner warning(s)');
      }
    } else {
      renderStockEngineCards([]);  // show empty state
    }
  })();

  /* Wire Stock Engine run button */
  if(stockEngineRunBtnEl){
    stockEngineRunBtnEl.addEventListener('click', function(){
      runStockEngineScan().catch(function(err){
        setStockEngineError(String(err?.message || err || 'Stock scan failed'));
      });
    });
  }

  /* ── OE card state (mirrors scanner shell's _expandState + currentTrades) ── */
  const _oeExpandState = {};
  let _oeTradesForActions = [];   // parallel array to top[] – raw scannerTrade objects
  let _oeTopIdeas = [];           // normalized ideas for action handlers
  const _mapper = window.BenTradeOptionTradeCardModel;

  /* ── Symbol Universe Selector (home scan queue) ── */
  let _homeSymbolSelector = null;
  if(symbolUniverseEl && window.BenTradeSymbolUniverseSelector){
    _homeSymbolSelector = window.BenTradeSymbolUniverseSelector.mount(symbolUniverseEl, {
      showFilter: true,
      onChange: () => {},  // passive — applied on next queue run
    });
  }

  /* ── Market Regime Model Analysis state ── */
  let _latestRegimePayload = null;
  let _latestPlaybookPayload = null;
  let _latestRegimeModelResult = null;  // cached model analysis API result for persistence
  let _regimeModelInflight = null;   // Promise | null — guards duplicate clicks

  function setScanError(text){
    if(!scanErrorEl) return;
    if(!text){
      scanErrorEl.style.display = 'none';
      scanErrorEl.textContent = '';
      return;
    }
    scanErrorEl.style.display = 'block';
    scanErrorEl.textContent = String(text);
  }

  function setScanStatus(text, isBusy = false){
    if(!scanStatusEl) return;
    if(!text){
      scanStatusEl.style.display = 'none';
      scanStatusEl.innerHTML = '';
      return;
    }
    scanStatusEl.style.display = 'block';
    scanStatusEl.innerHTML = isBusy
      ? `<span class="home-scan-status"><span class="home-scan-spinner" aria-hidden="true"></span><span>${String(text)}</span></span>`
      : String(text);
  }

  /* ── Market Regime Model Analysis ──────────────────────────────── */

  /**
   * Render the Engine vs Model comparison table.
   * @param {object} result – full API response from /api/model/analyze_regime
   */
  function _renderRegimeComparisonTable(result){
    if(!regimeComparisonEl) return;
    const engine = result?.engine_summary;
    const model  = result?.model_summary;
    const comp   = result?.comparison;
    const trace  = result?.regime_comparison_trace;
    if(!engine || !model || !comp){
      regimeComparisonEl.style.display = 'none';
      return;
    }
    const dc = comp.disagreement_count || 0;
    const deltas = comp.deltas || {};
    const badgeCls = dc === 0 ? 'regime-comparison-badge--agree' : 'regime-comparison-badge--disagree';
    const badgeText = dc === 0 ? 'Full Agreement' : `${dc} Disagreement${dc > 1 ? 's' : ''}`;

    // Truncation warning
    const isTruncated = trace?.truncated === true;
    const allModelNull = !model.risk_regime_label && !model.trend_label && !model.vol_regime_label && model.confidence == null;
    const truncationHtml = (isTruncated || allModelNull)
      ? `<div style="padding:6px 10px;background:rgba(200,80,80,0.15);border:1px solid rgba(200,80,80,0.3);border-radius:6px;margin-bottom:8px;font-size:12px;color:#e0a0a0;">
           ⚠ Model response ${isTruncated ? 'was truncated (token limit)' : 'returned empty fields'}. Results may be incomplete.${isTruncated ? ' Consider increasing max_tokens.' : ''}
         </div>`
      : '';
    // Helper – return Δ cell content
    function deltaCell(key){
      const d = deltas[key];
      if(!d) return '<td>—</td>';
      if(d.match) return `<td class="regime-delta-match">✓ Match</td>`;
      const detail = d.detail ? ` (${_esc(d.detail)})` : '';
      return `<td class="regime-delta-mismatch">✗ Mismatch${detail}</td>`;
    }

    // Confidence display helper
    function fmtConf(v){ return v != null ? `${(v * 100).toFixed(0)}%` : '—'; }

    // Rows: Risk, Trend, Volatility, Confidence + block assessments
    const rows = [
      { label: 'Risk Regime', eVal: engine.risk_regime_label, mVal: model.risk_regime_label, key: 'risk' },
      { label: 'Trend',       eVal: engine.trend_label,        mVal: model.trend_label,        key: 'trend' },
      { label: 'Volatility',  eVal: engine.vol_regime_label,   mVal: model.vol_regime_label,   key: 'vol' },
      { label: 'Confidence',  eVal: fmtConf(engine.confidence), mVal: fmtConf(model.confidence), key: 'confidence' },
    ];
    // Block assessment rows when present
    if(engine.structural_label || model.structural_assessment){
      rows.push({ label: 'Structural', eVal: engine.structural_label, mVal: model.structural_assessment, key: 'structural' });
    }
    if(engine.tape_label || model.tape_assessment){
      rows.push({ label: 'Tape', eVal: engine.tape_label, mVal: model.tape_assessment, key: 'tape' });
    }
    if(engine.tactical_label || model.tactical_assessment){
      rows.push({ label: 'Tactical', eVal: engine.tactical_label, mVal: model.tactical_assessment, key: 'tactical' });
    }
    const rowsHtml = rows.map(r =>
      `<tr><td>${_esc(r.label)}</td><td>${_esc(r.eVal || '—')}</td><td>${_esc(r.mVal || '—')}</td>${deltaCell(r.key)}</tr>`
    ).join('');

    // Drivers (side by side)
    const eDrv = Array.isArray(engine.key_drivers) ? engine.key_drivers : [];
    const mDrv = Array.isArray(model.key_drivers) ? model.key_drivers : [];
    const drvHtml = (eDrv.length || mDrv.length)
      ? `<div class="regime-comparison-drivers">
           <div><div class="regime-comparison-drivers-col-title">Engine Drivers</div>${eDrv.length ? '<ol style="margin:0;padding-left:16px;">' + eDrv.map(d => `<li>${_esc(String(d))}</li>`).join('') + '</ol>' : '<span style="opacity:0.5;">—</span>'}</div>
           <div><div class="regime-comparison-drivers-col-title">Model Drivers</div>${mDrv.length ? '<ol style="margin:0;padding-left:16px;">' + mDrv.map(d => `<li>${_esc(String(d))}</li>`).join('') + '</ol>' : '<span style="opacity:0.5;">—</span>'}</div>
         </div>`
      : '';

    // Trace (collapsed)
    let traceHtml = '';
    if(trace){
      const tLines = [
        `Input mode: ${_esc(trace.input_mode || '?')}`,
        `Raw input keys: ${(trace.raw_input_keys || []).length}`,
        `Disagreements: ${trace.disagreement_count ?? '?'}`,
        `Finish reason: ${_esc(trace.finish_reason || 'ok')}`,
      ];
      if(trace.timestamps){
        if(trace.timestamps.engine_ts) tLines.push(`Engine ts: ${_esc(trace.timestamps.engine_ts)}`);
        if(trace.timestamps.model_ts) tLines.push(`Model ts: ${_esc(trace.timestamps.model_ts)}`);
      }
      traceHtml = `<details class="regime-comparison-trace"><summary style="cursor:pointer;font-size:11px;font-weight:600;color:var(--accent,#00eaff);">Comparison Trace</summary><ul style="margin:4px 0 0;padding-left:16px;">${tLines.map(l => `<li>${l}</li>`).join('')}</ul></details>`;
    }

    regimeComparisonEl.innerHTML = `
      <div class="regime-comparison-wrapper">
        <details open>
          <summary class="regime-comparison-header">
            <span class="regime-comparison-title">Market Regime: Engine vs Model</span>
            <span class="regime-comparison-badge ${badgeCls}">${badgeText}</span>
            <button class="regime-model-rerun-btn" type="button" title="Rerun model analysis">⟳ Rerun</button>
          </summary>
          <div class="regime-comparison-body">
            ${truncationHtml}
            <table class="regime-comparison-table">
              <thead><tr><th>Metric</th><th>Engine</th><th>Model</th><th>Δ</th></tr></thead>
              <tbody>${rowsHtml}</tbody>
            </table>
            ${drvHtml}
            ${traceHtml}
          </div>
        </details>
      </div>`;
    regimeComparisonEl.style.display = 'block';
    // Wire up rerun button
    const rerunBtn = regimeComparisonEl.querySelector('.regime-model-rerun-btn');
    if(rerunBtn){
      rerunBtn.addEventListener('click', function(e){
        e.preventDefault();
        e.stopPropagation();
        runRegimeModelAnalysis().catch(function(){});
      });
    }
    console.debug('[REGIME_COMPARISON] rendered', {
      disagreement_count: dc, deltas, isTruncated, allModelNull,
      model_summary: model,
      engine_summary: engine,
    });
  }

  function _renderRegimeModelOutput(analysis){
    if(!regimeModelOutputEl) return;
    if(!analysis){
      regimeModelOutputEl.style.display = 'none';
      regimeModelOutputEl.innerHTML = '';
      return;
    }

    console.debug('[REGIME_MODEL_OUTPUT] analysis keys:', Object.keys(analysis),
      'has_exec_summary:', !!analysis.executive_summary,
      'has_breakdown:', !!analysis.regime_breakdown,
      'has_trace:', !!analysis._trace);

    const sections = [];

    // Executive summary
    if(analysis.executive_summary){
      sections.push(`<div class="regime-model-section"><div class="regime-model-section-title">Executive Summary</div><div class="regime-model-section-body">${_esc(analysis.executive_summary)}</div></div>`);
    }

    // Regime breakdown by component (extended with block assessments)
    if(analysis.regime_breakdown && typeof analysis.regime_breakdown === 'object'){
      const breakdownKeys = ['structural', 'tape', 'tactical', 'trend', 'volatility', 'breadth', 'rates', 'momentum'];
      const lines = breakdownKeys
        .filter((k) => analysis.regime_breakdown[k])
        .map((k) => `<li><strong>${k.charAt(0).toUpperCase() + k.slice(1)}:</strong> ${_esc(String(analysis.regime_breakdown[k]))}</li>`)
        .join('');
      if(lines){
        sections.push(`<div class="regime-model-section"><div class="regime-model-section-title">Regime Breakdown</div><ul class="regime-model-list">${lines}</ul></div>`);
      }
    }

    // Model what-works / what-to-avoid
    const mWhatWorks = Array.isArray(analysis.what_works) ? analysis.what_works : [];
    const mWhatAvoid = Array.isArray(analysis.what_to_avoid) ? analysis.what_to_avoid : [];
    if(mWhatWorks.length || mWhatAvoid.length){
      let wwHtml = '<div class="regime-model-section"><div class="regime-model-section-title">Model Strategy Guidance</div>';
      if(mWhatWorks.length){
        wwHtml += `<div style="margin-bottom:6px;"><strong style="color:var(--green,#7ef7b8);">What Works:</strong> ${mWhatWorks.map((w) => _esc(String(w))).join(' · ')}</div>`;
      }
      if(mWhatAvoid.length){
        wwHtml += `<div><strong style="color:var(--red,#c85050);">Avoid:</strong> ${mWhatAvoid.map((w) => _esc(String(w))).join(' · ')}</div>`;
      }
      wwHtml += '</div>';
      sections.push(wwHtml);
    }

    // Primary fit
    if(analysis.primary_fit){
      sections.push(`<div class="regime-model-section"><div class="regime-model-section-title">Why Primary Strategies Fit</div><div class="regime-model-section-body">${_esc(analysis.primary_fit)}</div></div>`);
    }

    // Avoid rationale
    if(analysis.avoid_rationale){
      sections.push(`<div class="regime-model-section"><div class="regime-model-section-title">Why Avoid Strategies Are Riskier</div><div class="regime-model-section-body">${_esc(analysis.avoid_rationale)}</div></div>`);
    }

    // Change triggers
    const triggers = Array.isArray(analysis.change_triggers) ? analysis.change_triggers : [];
    if(triggers.length){
      const triggerLines = triggers.map((t) => `<li>${_esc(String(t))}</li>`).join('');
      sections.push(`<div class="regime-model-section"><div class="regime-model-section-title">What Would Change My Mind</div><ul class="regime-model-list">${triggerLines}</ul></div>`);
    }

    // Confidence + caveats
    if(analysis.confidence_caveats){
      const confPct = (analysis.confidence != null) ? ` (${(analysis.confidence * 100).toFixed(0)}%)` : '';
      sections.push(`<div class="regime-model-section"><div class="regime-model-section-title">Confidence &amp; Caveats${confPct}</div><div class="regime-model-section-body">${_esc(analysis.confidence_caveats)}</div></div>`);
    }

    // Raw inputs cross-check / transparency block
    if(analysis.raw_inputs_used && typeof analysis.raw_inputs_used === 'object'){
      const riu = analysis.raw_inputs_used;
      const entries = Object.entries(riu).filter(([k]) => k !== 'missing');
      const missing = Array.isArray(riu.missing) ? riu.missing : [];
      let riuHtml = '<div class="regime-model-section"><div class="regime-model-section-title">Derived by Model from Raw Inputs</div>';
      if(entries.length){
        const items = entries.map(([k, v]) => `<li><strong>${_esc(k)}:</strong> ${_esc(String(v))}</li>`).join('');
        riuHtml += `<ul class="regime-model-list">${items}</ul>`;
      }
      if(missing.length){
        riuHtml += `<div class="regime-model-section-body" style="margin-top:0.3em;color:var(--text-muted,#888);">Missing inputs: ${missing.map((m) => _esc(String(m))).join(', ')}</div>`;
      }
      riuHtml += '</div>';
      sections.push(riuHtml);
    }

    // Trace metadata (collapsed)
    if(analysis._trace && typeof analysis._trace === 'object'){
      const t = analysis._trace;
      const traceLines = [
        `Input mode: ${_esc(String(t.model_regime_input_mode || 'unknown'))}`,
        `Included fields: ${t.included_fields_count ?? '?'}`,
        `Excluded derived fields: ${t.excluded_fields_count ?? '?'}`,
      ];
      if(Array.isArray(t.missing_raw_fields) && t.missing_raw_fields.length){
        traceLines.push(`Missing raw: ${t.missing_raw_fields.map((f) => _esc(String(f))).join(', ')}`);
      }
      const traceHtml = traceLines.map((l) => `<li>${l}</li>`).join('');
      sections.push(`<div class="regime-model-section"><details><summary class="regime-model-section-title" style="cursor:pointer;">Trace / Debug</summary><ul class="regime-model-list" style="font-size:0.85em;opacity:0.75;">${traceHtml}</ul></details></div>`);
    }

    regimeModelOutputEl.innerHTML = `<details class="regime-model-details" open><summary class="regime-model-summary">Model Analysis Output</summary><div class="regime-model-body">${sections.join('')}</div></details>`;
    regimeModelOutputEl.style.display = 'block';
  }

  function _esc(text){
    const el = document.createElement('span');
    el.textContent = String(text || '');
    return el.innerHTML;
  }

  async function runRegimeModelAnalysis({ _isRetry = false } = {}){
    if(_regimeModelInflight){
      return; // ignore duplicate clicks while in-flight
    }
    if(!_latestRegimePayload || !_latestRegimePayload.regime_label){
      _renderRegimeModelError('No regime data available. Load the dashboard first.');
      return;
    }

    // Show loading state
    if(regimeModelOutputEl){
      regimeModelOutputEl.style.display = 'block';
      regimeModelOutputEl.innerHTML = '<div class="regime-model-loading"><span class="home-scan-spinner" aria-hidden="true"></span> Running model analysis\u2026</div>';
    }

    const promise = api.modelAnalyzeRegime(_latestRegimePayload, _latestPlaybookPayload);
    _regimeModelInflight = promise;

    try{
      const result = await promise;
      if(_regimeModelInflight !== promise) return; // stale
      // Detect all-null model labels — indicates cold-start / incomplete response
      const ms = result?.model_summary;
      const allModelNull = ms && !ms.risk_regime_label && !ms.trend_label && !ms.vol_regime_label && ms.confidence == null;
      if(allModelNull && !_isRetry){
        console.warn('[REGIME_MODEL] All model labels null on first attempt — auto-retrying in 4s');
        _regimeModelInflight = null;
        await new Promise(function(r){ setTimeout(r, 4000); });
        return runRegimeModelAnalysis({ _isRetry: true });
      }
      _latestRegimeModelResult = result;
      // Persist result into home cache for SPA re-mount restoration
      _persistRegimeModelResult(result);
      _renderRegimeComparisonTable(result);
      _renderRegimeModelOutput(result?.analysis || result);
    }catch(err){
      if(_regimeModelInflight !== promise) return;
      _renderRegimeModelError(err?.message || 'Model analysis failed');
    }finally{
      if(_regimeModelInflight === promise){
        _regimeModelInflight = null;
      }
    }
  }

  function _renderRegimeModelError(message){
    if(!regimeModelOutputEl) return;
    regimeModelOutputEl.style.display = 'block';
    regimeModelOutputEl.innerHTML = `<div class="regime-model-error">${_esc(message)}<button class="regime-model-rerun-btn" type="button" title="Retry model analysis" style="margin-left:10px;">⟳ Retry</button></div>`;
    const retryBtn = regimeModelOutputEl.querySelector('.regime-model-rerun-btn');
    if(retryBtn){
      retryBtn.addEventListener('click', function(e){
        e.preventDefault();
        runRegimeModelAnalysis().catch(function(){});
      });
    }
  }

  /** Persist regime model analysis result into home cache snapshot. */
  function _persistRegimeModelResult(result){
    const cacheStore = window.BenTradeHomeCacheStore;
    if(!cacheStore) return;
    const snap = cacheStore.getSnapshot();
    if(!snap || !snap.data) return;
    snap.data.regimeModelResult = result;
    // Re-set to persist (triggers localStorage write)
    cacheStore.setSnapshot(snap);
  }

  /** Restore cached regime model analysis from snapshot data. */
  function _restoreRegimeModelResult(data){
    const result = data?.regimeModelResult;
    if(!result) return;
    _latestRegimeModelResult = result;
    _renderRegimeComparisonTable(result);
    _renderRegimeModelOutput(result?.analysis || result);
  }

  /* ── End Regime Model Analysis ─────────────────────────────────── */

  /* ── Contextual Chat: Market Regime context builder ───────────── */

  /**
   * Build a curated context contract for the Market Regime panel.
   * Returns the reusable context contract shape consumed by BenTradeChat.open().
   *
   * CROSS-REF: Server-side mirror lives in
   *   contextual_chat_service.build_market_regime_context()
   * Both must produce the same context_payload field set.
   * Frontend is authoritative (has full dashboard state);
   * server-side is a fallback / validation reference.
   */
  function _buildRegimeChatContext() {
    var regime = _latestRegimePayload || {};
    var modelResult = _latestRegimeModelResult || {};
    var modelSummary = modelResult.model_summary || {};
    var comparison = modelResult.comparison || {};
    var components = regime.components || {};
    var blocks = regime.blocks || {};

    var structural = blocks.structural || {};
    var tape = blocks.tape || {};
    var tactical = blocks.tactical || {};
    var playbook = regime.suggested_playbook || _latestPlaybookPayload || {};

    var payload = {
      regime_label: regime.regime_label || null,
      regime_score: regime.regime_score != null ? regime.regime_score : null,
      confidence: regime.confidence != null ? regime.confidence : null,
      interpretation: regime.interpretation || null,
      structural_block: {
        label: structural.label || null,
        summary: structural.summary || null,
      },
      tape_block: {
        label: tape.label || null,
        summary: tape.summary || null,
      },
      tactical_block: {
        label: tactical.label || null,
        summary: tactical.summary || null,
      },
      key_drivers: regime.key_drivers || null,
      what_works: playbook.primary || playbook.what_works || null,
      what_to_avoid: playbook.avoid || playbook.what_to_avoid || null,
      change_triggers: regime.change_triggers || null,
      as_of: regime.as_of || null,
    };

    // Include model analysis agreement/disagreement if available
    if (comparison.disagreement_count != null) {
      payload.model_agreement = {
        disagreement_count: comparison.disagreement_count,
        model_risk: modelSummary.risk_regime_label || null,
        model_trend: modelSummary.trend_label || null,
        model_vol: modelSummary.vol_regime_label || null,
        model_confidence: modelSummary.confidence != null ? modelSummary.confidence : null,
      };
    }

    var label = payload.regime_label || 'Unknown';
    var score = payload.regime_score != null ? payload.regime_score : '?';
    var conf = payload.confidence != null ? (payload.confidence * 100).toFixed(0) + '%' : '?';

    return {
      context_type: 'market_regime',
      context_title: 'Market Regime',
      context_summary: 'Regime: ' + label + ' (score ' + score + ', confidence ' + conf + ')',
      context_payload: payload,
      source_panel: 'home.regime',
      generated_at: new Date().toISOString(),
    };
  }

  /** Enable/disable the chat button based on regime data readiness. */
  function _updateChatBtnState() {
    if (!regimeChatBtnEl) return;
    var ready = !!_latestRegimePayload && !!_latestRegimePayload.regime_label;
    regimeChatBtnEl.disabled = !ready;
    regimeChatBtnEl.title = ready
      ? 'Discuss this regime with AI'
      : 'Waiting for regime data…';
  }

  function _onRegimeChatClick() {
    if (!_latestRegimePayload || !_latestRegimePayload.regime_label) {
      console.warn('[REGIME_CHAT] No regime data available yet.');
      // Brief visual feedback instead of silent failure
      if (regimeChatBtnEl) {
        regimeChatBtnEl.classList.add('bt-btn-shake');
        setTimeout(function () { regimeChatBtnEl.classList.remove('bt-btn-shake'); }, 500);
      }
      return;
    }
    var ctx = _buildRegimeChatContext();
    if (window.BenTradeChat) {
      window.BenTradeChat.open(ctx);
    } else {
      console.error('[REGIME_CHAT] BenTradeChat module not loaded.');
    }
  }

  /* ── End Contextual Chat ──────────────────────────────────────── */

  const INDEX_SYMBOLS = ['SPY', 'QQQ', 'IWM', 'DIA', 'IWB', 'MDY'];
  const INDEX_META = {
    SPY: { name: 'S&P 500', descriptor: 'Large-cap benchmark', index: 'S&P 500' },
    DIA: { name: 'Dow Jones', descriptor: 'Blue-chip price leadership', index: 'DJIA' },
    QQQ: { name: 'Nasdaq Composite', descriptor: 'Growth-heavy market barometer', index: 'Nasdaq' },
    IWM: { name: 'Russell 2000', descriptor: 'Small-cap risk appetite gauge', index: 'Russell 2000' },
    IWB: { name: 'Russell 1000', descriptor: 'Large/mid-cap breadth proxy', index: 'Russell 1000' },
    MDY: { name: 'S&P MidCap 400', descriptor: 'Mid-cap domestic cycle read', index: 'MidCap 400' },
  };
  const SECTOR_SYMBOLS = ['XLF', 'XLK', 'XLE', 'XLY', 'XLP', 'XLV', 'XLI', 'XLB', 'XLRE', 'XLU', 'XLC'];
  const SECTOR_META = {
    XLF: { name: 'Financials', description: 'Banks, insurers, and diversified financial services firms' },
    XLK: { name: 'Technology', description: 'Software, semiconductors, hardware, and IT services' },
    XLE: { name: 'Energy', description: 'Oil, gas, exploration, production, and energy equipment' },
    XLY: { name: 'Consumer Discretionary', description: 'Retail, autos, media, and optional consumer spending' },
    XLP: { name: 'Consumer Staples', description: 'Everyday household goods, food, and beverage producers' },
    XLV: { name: 'Health Care', description: 'Pharma, biotech, medical devices, and health providers' },
    XLI: { name: 'Industrials', description: 'Aerospace, machinery, transportation, and business services' },
    XLB: { name: 'Materials', description: 'Chemicals, metals, mining, and construction materials' },
    XLRE: { name: 'Real Estate', description: 'REITs and diversified real estate management firms' },
    XLU: { name: 'Utilities', description: 'Electric, gas, and water utility providers' },
    XLC: { name: 'Communication Services', description: 'Telecom, media, entertainment, and interactive platforms' },
  };
  const STRATEGY_SOURCES = [
    { id: 'credit_spread', label: 'Credit Spread', route: '#/credit-spread' },
    { id: 'debit_spreads', label: 'Debit Spreads', route: '#/debit-spreads' },
    { id: 'iron_condor', label: 'Iron Condor', route: '#/iron-condor' },
    { id: 'butterflies', label: 'Butterflies', route: '#/butterflies' },
  ];
  const PLAYBOOK_ROUTES = {
    put_credit_spread: '#/credit-spread',
    covered_call: '#/income',
    call_debit: '#/debit-spreads',
    iron_condor: '#/strategy-iron-condor',
    put_debit: '#/debit-spreads',
    csp_far_otm: '#/income',
    calendar: '#/calendar',
    hedges: '#/portfolio-risk',
    short_put_spreads_near_spot: '#/credit-spread',
    iron_condor_tight: '#/strategy-iron-condor',
    credit_spreads_wider: '#/credit-spread',
    butterflies: '#/butterflies',
    aggressive_directional_debit_spreads: '#/debit-spreads',
    aggressive_short_calls: '#/income',
  };

  function setError(text){
    if(!text){
      errorEl.style.display = 'none';
      errorEl.textContent = '';
      return;
    }
    errorEl.style.display = 'block';
    errorEl.textContent = String(text);
  }

  /* ── shared module delegates ── */
  const _fmtLib = window.BenTradeUtils.format;
  const _accessor = window.BenTradeUtils.tradeAccessor;
  const _card    = window.BenTradeTradeCard;
  const toNumber = _fmtLib.toNumber;
  const fmt      = _fmtLib.num;
  const fmtSigned = _fmtLib.signed;
  const fmtPct   = _fmtLib.signedPct;
  const toPctString = _fmtLib.pct;
  const metricMissingReason = _card.metricMissingReason;

  function normalizeSymbol(value){
    return String(value || '').trim().toUpperCase();
  }

  function isLikelyOptionsStrategy(value){
    const text = String(value || '').toLowerCase();
    if(!text) return false;
    return text.includes('credit')
      || text.includes('debit')
      || text.includes('condor')
      || text.includes('butter')
      || text.includes('calendar')
      || text.includes('spread')
      || text.includes('covered_call')
      || text.includes('csp');
  }

  function isDevInstrumentationEnabled(){
    try{
      const host = String(location.hostname || '').toLowerCase();
      const localHost = host === 'localhost' || host === '127.0.0.1' || host.endsWith('.local');
      if(localHost) return true;
      return localStorage.getItem('bentrade_debug_home_metrics') === '1';
    }catch(_err){
      return false;
    }
  }

  function logOpportunityInstrumentationOnce(idea, idx){
    if(!isDevInstrumentationEnabled()) return;
    const key = opportunityKey(idea, idx);
    if(devLoggedCards.has(key)) return;
    devLoggedCards.add(key);

    const trade = (idea?.trade && typeof idea.trade === 'object') ? idea.trade : {};
    const comp = (trade?.computed && typeof trade.computed === 'object') ? trade.computed : {};
    const fields = {
      pop: comp?.pop,
      ev: comp?.expected_value,
      return_on_risk: comp?.return_on_risk ?? trade?.return_on_risk,
      max_profit: comp?.max_profit,
      max_loss: comp?.max_loss,
    };
    console.debug('[HomeMetrics] card_source', {
      symbol: idea?.symbol,
      strategy: idea?.strategy,
      source_feed: idea?.source_feed || 'latest analysis_*.json trades',
      source: idea?.source,
      sourceType: idea?.sourceType,
      fields,
      normalized: {
        ev: idea?.ev,
        pop: idea?.pop,
        ror: idea?.ror,
      },
    });
  }

  function normalizeTradeIdea(row, source){
    const symbol = normalizeSymbol(row?.symbol);
    const score = _fmtLib.normalizeScore(row?.composite_score ?? row?.trade_quality_score ?? row?.score) ?? 0;
    const comp = (row?.computed && typeof row.computed === 'object') ? row.computed : {};
    const ev = toNumber(comp?.expected_value ?? row?.ev ?? row?.edge);
    const pop = toNumber(comp?.pop ?? row?.pop);
    const ror = toNumber(comp?.return_on_risk ?? row?.return_on_risk ?? row?.ror);
    const strategy = String(row?.strategy_id || row?.type || row?.recommended_strategy || source?.label || 'idea');
    const recommendation = String(row?.model_evaluation?.recommendation || row?.recommendation || 'N/A');

    return {
      symbol: symbol || 'N/A',
      strategy,
      score,
      ev,
      pop,
      ror,
      recommendation,
      route: source?.route || '#/credit-spread',
      source: source?.label || 'Unknown',
      trade: row,
    };
  }

  function computeRor(raw){
    const comp = (raw?.computed && typeof raw.computed === 'object') ? raw.computed : {};
    const direct = toNumber(comp?.return_on_risk ?? raw?.return_on_risk ?? raw?.ror);
    if(direct !== null) return direct;
    const maxProfit = toNumber(comp?.max_profit ?? raw?.max_profit);
    const maxLoss = toNumber(comp?.max_loss ?? raw?.max_loss);
    if(maxProfit !== null && maxLoss !== null && maxLoss > 0){
      return maxProfit / maxLoss;
    }
    return null;
  }

  function normalizeOpportunity(candidate, sourceType){
    const row = candidate && typeof candidate === 'object' ? candidate : {};
    const raw = row?.trade && typeof row.trade === 'object'
      ? row.trade
      : (row?.raw && typeof row.raw === 'object' ? row.raw : row);

    const inferredSource = String(sourceType || row?.sourceType || row?.type || '').toLowerCase();
    const symbol = normalizeSymbol(row?.symbol || raw?.symbol) || 'N/A';
    const strategy = String(row?.strategy || raw?.strategy_id || raw?.type || raw?.recommended_strategy || 'idea');
    const strategySuggestsOptions = isLikelyOptionsStrategy(strategy);
    const isStock = !strategySuggestsOptions && (inferredSource === 'stock' || String(row?.source || '').toLowerCase().includes('stock scanner'));
    const rank = _fmtLib.normalizeScore(row?.rank ?? row?.score ?? row?.rank_score ?? raw?.rank_score ?? raw?.composite_score ?? raw?.trade_quality_score) ?? 0;

    let ev = null;
    let pop = null;
    let ror = null;
    const notes = [];

    if(isStock){
      ev = null;
      pop = null;
      ror = null;
      notes.push('Not computed for equities ideas yet.');
    }else{
      // Prefer per-contract EV from computed (unified with scanner), then key_metrics
      const comp = (raw?.computed && typeof raw.computed === 'object') ? raw.computed : {};
      ev = toNumber(comp?.expected_value ?? row?.key_metrics?.ev_to_risk ?? row?.key_metrics?.ev ?? row?.ev);
      if(ev === null){
        ev = toNumber(raw?.ev ?? row?.edge ?? row?.expected_value);
      }

      pop = toNumber(comp?.pop ?? row?.key_metrics?.pop ?? row?.pop);
      if(pop === null){
        pop = toNumber(row?.pop ?? raw?.pop);
      }

      if(pop !== null && pop > 1.0){
        pop = pop / 100.0;
      }

      ror = computeRor(raw);
      if(ror === null){
        ror = computeRor(row);
      }
      if(ror === null){
        ror = toNumber(row?.key_metrics?.ror ?? row?.key_metrics?.return_on_risk);
      }
      if(ror !== null && ror > 1.0){
        ror = ror / 100.0;
      }
    }

    const modelPayload = row?.model && typeof row.model === 'object'
      ? row.model
      : (raw?.model_evaluation && typeof raw.model_evaluation === 'object' ? raw.model_evaluation : null);

    const model = modelPayload
      ? {
        status: 'available',
        recommendation: String(modelPayload?.recommendation || 'UNKNOWN').toUpperCase(),
        confidence: toNumber(modelPayload?.confidence),
        summary: String(modelPayload?.summary || '').trim(),
      }
      : {
        status: 'not_run',
        recommendation: 'Not run',
        confidence: null,
        summary: '',
      };

    const price = toNumber(raw?.price ?? row?.key_metrics?.price);
    const rsi14 = toNumber(raw?.metrics?.rsi14 ?? row?.key_metrics?.rsi14 ?? raw?.signals?.rsi_14 ?? raw?.rsi14);
    const ema20 = toNumber(raw?.metrics?.ema20 ?? row?.key_metrics?.ema20 ?? raw?.ema20);
    const ivrv = toNumber(raw?.metrics?.iv_rv_ratio ?? row?.key_metrics?.iv_rv_ratio ?? raw?.signals?.iv_rv_ratio ?? raw?.iv_rv_ratio);
    const trendRaw = String(raw?.trend || row?.key_metrics?.trend || raw?.signals?.trend || '').trim().toLowerCase();
    const trend = trendRaw || ((price !== null && ema20 !== null) ? (price >= ema20 ? 'up' : 'down') : null);
    const bidAskSpreadPct = toNumber(raw?.bid_ask_spread_pct ?? row?.key_metrics?.bid_ask_spread_pct);
    const volume = toNumber(raw?.volume ?? row?.key_metrics?.volume);
    const openInterest = toNumber(raw?.open_interest ?? row?.key_metrics?.open_interest);
    let liquidity = null;
    if(bidAskSpreadPct !== null){
      liquidity = Math.max(0, Math.min(100, 100 - (bidAskSpreadPct * 100)));
    } else if(volume !== null || openInterest !== null){
      liquidity = Math.max(0, Math.min(100, ((volume || 0) / 1000) * 40 + ((openInterest || 0) / 3000) * 60));
    }
    let ivrvFlag = null;
    if(ivrv !== null){
      if(ivrv > 1.2) ivrvFlag = 'rich';
      else if(ivrv < 0.9) ivrvFlag = 'cheap';
      else ivrvFlag = 'balanced';
    }

    return {
      symbol,
      strategy,
      rank,
      ev,
      pop,
      ror,
      model,
      why: Array.isArray(row?.why) ? row.why : [],
      key_metrics: {
        price,
        rsi14,
        ema20,
        trend,
        iv_rv_ratio: ivrv,
        iv_rv_flag: ivrvFlag,
        liquidity,
      },
      route: row?.route || row?.actions?.open_route || '#/credit-spread',
      source: row?.source || (isStock ? 'Stock Scanner' : 'Strategy'),
      source_feed: row?.source_feed || (isStock ? 'stock scanner' : 'latest analysis_*.json trades'),
      trade: raw,
      trade_payload: isStock ? null : {
        ...raw,
        symbol: String(raw?.symbol || symbol || '').toUpperCase(),
        strategy_id: String(raw?.strategy_id || strategy || ''),
      },
      equity_payload: isStock ? {
        symbol,
        idea: { ...raw, symbol },
      } : null,
      notes,
      sourceType: isStock ? 'stock' : 'options',
      actions: row?.actions || {},
    };
  }

  const escapeHtml = _fmtLib.escapeHtml;

  /**
   * toScannerTrade — Adapter: converts an Opportunity Engine idea into
   * the raw trade shape expected by BenTradeOptionTradeCardModel.map().
   * The mapper reads from .computed, .details, root-level keys, etc.
   * We shallow-copy to avoid mutating the source idea.
   *
   * For stock scanner candidates the raw object has a completely different
   * shape (no computed/details/legs/strikes).  We bridge it here so that
   * the 4-tier metric resolver in the card model picks up stock-specific
   * metrics just like option trades.
   */
  function toScannerTrade(idea){
    const raw = idea.trade && typeof idea.trade === 'object' ? { ...idea.trade } : {};
    if(!raw.symbol)      raw.symbol      = String(idea.symbol || '');
    if(!raw.strategy_id) raw.strategy_id = String(idea.strategy || raw.spread_type || raw.strategy || '');

    /* ── Stock candidate bridge ── */
    const isStock = idea.sourceType === 'stock' || raw.type === 'stock_buy';
    if(isStock){
      raw.strategy_id = raw.strategy_id || 'stock_buy';
      raw.trade_key   = raw.trade_key || raw.idea_key || `${raw.symbol}|STOCK|stock_scanner`;
      raw.underlying_price = raw.underlying_price ?? raw.price ?? null;
      if(!raw.trend) raw.trend = raw.trend || idea.trend || '';

      /* Surface stock scores into 'computed' so the 4-tier resolver
         finds them at tier-1 (same as option trades). */
      const m = raw.metrics && typeof raw.metrics === 'object' ? raw.metrics : {};
      raw.computed = Object.assign({}, raw.computed || {}, {
        rank_score:       raw.composite_score ?? null,
        trend_score:      raw.trend_score ?? null,
        momentum_score:   raw.momentum_score ?? null,
        volatility_score: raw.volatility_score ?? null,
        pullback_score:   raw.pullback_score ?? null,
        catalyst_score:   raw.catalyst_score ?? null,
        rsi14:            m.rsi14 ?? null,
        ema20:            m.ema20 ?? null,
        sma50:            m.sma50 ?? null,
        iv_rv_ratio:      m.iv_rv_ratio ?? null,
      });
    }

    return raw;
  }

  function opportunityKey(idea, idx){
    const symbol = normalizeSymbol(idea?.symbol || idea?.trade?.symbol || idea?.trade?.underlying || 'N/A');
    const strategy = String(idea?.strategy || idea?.trade?.strategy_id || idea?.trade?.spread_type || idea?.trade?.strategy || 'idea');
    const source = String(idea?.sourceType || idea?.source || 'unknown');
    return `${symbol}|${strategy}|${source}|${Number.isFinite(idx) ? idx : 0}`;
  }

  function formatModelSummary(model){
    if(!model || model.status === 'not_run') return 'Not run';
    if(model.status === 'running') return 'Running...';
    if(model.status === 'error'){
      const summary = String(model.summary || '').trim();
      return summary ? `Error • ${summary}` : 'Error • Model analysis failed';
    }
    const rec = String(model.model_recommendation || model.recommendation || 'UNKNOWN').toUpperCase();
    const confVal = toNumber(model.confidence_0_1 ?? model.confidence);
    const confText = confVal === null ? '' : ` (${(confVal * 100).toFixed(0)}%)`;
    const scoreText = toNumber(model.score_0_100) !== null ? ` [${model.score_0_100}/100]` : '';
    const summary = String(model.thesis || model.summary || '').trim();
    if(summary){
      return `${rec}${confText}${scoreText} • ${summary}`;
    }
    return `${rec}${confText}${scoreText}`;
  }

  /**
   * Render trade model analysis output as inline HTML for a card.
   * Delegates to the shared BenTradeModelAnalysis renderer for pixel-identical
   * output across Home + Scanner dashboards.
   * @param {object} model – model_evaluation dict or raw result
   * @param {number|null} compositeScore – unused (kept for call-site compat)
   * @returns {string} HTML
   */
  function _renderTradeModelOutput(model, compositeScore){
    if(!model) return '';
    if(_modelUI){
      const parsed = _modelUI.parse(model);
      return _modelUI.render(parsed);
    }
    /* Fallback if shared module not loaded */
    const esc = escapeHtml;
    const rec = String(model.recommendation || 'UNKNOWN').toUpperCase();
    return `<div style="font-size:12px;padding:8px;color:var(--text-secondary,#ccc);">${esc(rec)} — ${esc(String(model.summary || model.thesis || ''))}</div>`;
  }

  function routeForOpportunity(idea){
    if(!idea || idea.sourceType === 'stock') return '#/stock-analysis';
    const strategy = String(idea?.strategy || idea?.trade?.spread_type || idea?.trade?.strategy || '').toLowerCase();
    if(strategy.includes('credit_put')) return '#/credit-spread';
    if(strategy.includes('credit_call')) return '#/credit-spread';
    if(strategy.includes('credit_spread')) return '#/credit-spread';
    if(strategy.includes('iron_condor')) return '#/strategy-iron-condor';
    if(strategy.includes('debit')) return '#/debit-spreads';
    if(strategy.includes('butter')) return '#/butterflies';
    if(strategy.includes('calendar')) return '#/calendar';
    if(strategy.includes('income') || strategy.includes('covered_call')) return '#/income';
    const fromActions = String(idea?.actions?.open_route || idea?.route || '#/credit-spread');
    return fromActions.startsWith('#') ? fromActions : '#/credit-spread';
  }

  function persistSelectedOpportunity(idea){
    const symbol = String(idea?.symbol || '').toUpperCase();
    if(symbol){
      localStorage.setItem('bentrade_selected_symbol', symbol);
    }
    const candidateMinimal = {
      symbol,
      strategy: String(idea?.strategy || ''),
      sourceType: String(idea?.sourceType || ''),
      route: routeForOpportunity(idea),
      rank: toNumber(idea?.rank),
      trade: idea?.trade_payload || idea?.trade || null,
      equity: idea?.equity_payload || null,
    };
    localStorage.setItem('bentrade_selected_candidate', JSON.stringify(candidateMinimal));
  }

  function openAnalysisForOpportunity(idea){
    if(!idea) return;
    persistSelectedOpportunity(idea);
    location.hash = routeForOpportunity(idea);
  }

  function sendToWorkbenchForOpportunity(idea, destination = '#/trade-testing'){
    if(!idea) return;
    const strategy = String(idea?.trade?.spread_type || idea?.trade?.strategy || idea.strategy || 'put_credit_spread');
    const payload = {
      from: 'home_dashboard',
      ts: new Date().toISOString(),
      input: {
        symbol: String(idea?.symbol || ''),
        strategy,
        expiration: idea?.trade?.expiration || 'NA',
        short_strike: idea?.trade?.short_strike ?? null,
        long_strike: idea?.trade?.long_strike ?? null,
        contractsMultiplier: 100,
      },
      trade_key: `${String(idea?.symbol || 'N/A')}|NA|${strategy}|NA|NA|NA`,
      note: `Home opportunity ${String(idea?.source || 'Unknown')} rank ${fmt(idea?.rank, 1)}`,
    };
    localStorage.setItem('bentrade_workbench_handoff_v1', JSON.stringify(payload));
    location.hash = destination;
  }

  function buildExecutionTradeFromIdea(idea){
    const src = (idea?.trade_payload && typeof idea.trade_payload === 'object')
      ? idea.trade_payload
      : ((idea?.trade && typeof idea.trade === 'object') ? idea.trade : {});
    const symbol = String(src?.symbol || idea?.symbol || '').toUpperCase();
    const strategy = String(src?.strategy_id || idea?.strategy || '');
    return {
      ...src,
      symbol,
      strategy_id: strategy,
    };
  }

  function openExecuteForOpportunity(idea){
    if(!idea || idea.sourceType === 'stock'){
      return;
    }
    const trade = buildExecutionTradeFromIdea(idea);
    if(typeof window.executeTrade === 'function'){
      window.executeTrade(trade);
      return;
    }
    window.BenTradeExecutionModal?.open?.(trade || {}, { primaryLabel: 'Execute (off)' });
  }

  function strategyIdFromValue(value){
    const text = String(value || '').toLowerCase();
    if(!text) return null;
    if(text.includes('credit') || text.includes('put_spread') || text.includes('call_spread')) return 'credit_spread';
    if(text.includes('debit')) return 'debit_spreads';
    if(text.includes('iron_condor') || text.includes('condor')) return 'iron_condor';
    if(text.includes('butter') || text.includes('fly')) return 'butterflies';
    return null;
  }

  function hasUsableTradePayload(value){
    return !!(value && typeof value === 'object' && (value.short_strike !== undefined || value.long_strike !== undefined || value.expiration || value.contracts || value.snapshot));
  }

  function getModelSourceFromSession(){
    const sessionSource = window.BenTradeSessionState?.getCurrentReportFile?.();
    if(sessionSource) return String(sessionSource);
    if(window.currentReportFile) return String(window.currentReportFile);
    return null;
  }

  async function resolveModelSourceFile(idea){
    const direct = String(idea?.report_file || idea?.trade?.report_file || idea?.trade?._source_report_file || '').trim();
    if(direct){
      console.info('[MODEL_TRACE] resolveModelSourceFile → direct:', direct);
      return direct;
    }

    const sessionSource = getModelSourceFromSession();
    if(sessionSource){
      console.info('[MODEL_TRACE] resolveModelSourceFile → session:', sessionSource);
      return sessionSource;
    }

    const strategyId = String(idea?.strategy_id || strategyIdFromValue(idea?.strategy || idea?.trade?.spread_type || idea?.trade?.strategy) || '').trim();
    if(strategyId && api?.listStrategyReports){
      try{
        const files = await api.listStrategyReports(strategyId);
        const candidate = Array.isArray(files) && files.length ? String(files[0] || '').trim() : '';
        if(candidate){
          console.info('[MODEL_TRACE] resolveModelSourceFile → listReports:', candidate);
          return candidate;
        }
      }catch(_err){
        console.warn('[MODEL_TRACE] resolveModelSourceFile → listReports error:', _err);
      }
    }

    /* Fallback: generate a synthetic source identifier so the backend can
       tag its output file.  The "source" param is an output label, not an
       input dependency — the model evaluates the trade payload directly. */
    const sym = String(idea?.symbol || idea?.trade?.underlying || idea?.trade?.symbol || 'unknown').toUpperCase();
    const strat = strategyId || 'unknown';
    const synthetic = `home_${strat}_${sym}`.replace(/[^a-zA-Z0-9_]/g, '_');
    console.info('[MODEL_TRACE] resolveModelSourceFile → synthetic fallback:', synthetic);
    return synthetic;
  }

  function findMatchingOpportunityForModel(idea){
    const symbol = normalizeSymbol(idea?.symbol || idea?.trade?.underlying || idea?.trade?.symbol || '');
    if(!symbol) return null;
    const strategyText = String(idea?.strategy || idea?.trade?.spread_type || idea?.trade?.strategy || '').toLowerCase();
    const normalizedIdeas = Array.isArray(latestOpportunities)
      ? latestOpportunities.map((row) => normalizeOpportunity(row, row?.sourceType)).filter((row) => row && row.sourceType === 'options')
      : [];

    const strict = normalizedIdeas.find((row) => {
      const sameSymbol = normalizeSymbol(row?.symbol) === symbol;
      const sameStrategy = String(row?.strategy || '').toLowerCase() === strategyText;
      return sameSymbol && sameStrategy;
    });
    if(strict) return strict;

    const loose = normalizedIdeas.find((row) => normalizeSymbol(row?.symbol) === symbol);
    return loose || null;
  }

  function resolveIdeaForModel(idea){
    if(idea?.sourceType === 'stock') return idea;
    if(hasUsableTradePayload(idea?.trade_payload || idea?.trade)){
      return idea;
    }
    return findMatchingOpportunityForModel(idea) || idea;
  }

  /* ── Dedupe guard for Home model analysis (single-flight per opKey) ── */
  const _homeModelInFlight = new Set();

  async function runModelForOpportunity(idea, onModel, originTag = 'home_opportunities'){
    const _tag = `[MODEL_TRACE:home] runModelForOpportunity`;

    if(!idea){
      console.warn(_tag, 'called with null idea');
      if(typeof onModel === 'function') onModel({ status: 'error', recommendation: 'ERROR', confidence: null, summary: 'No trade selected.' });
      return false;
    }

    if(idea.sourceType === 'stock'){
      console.info(_tag, 'stock idea — skipping (not an options trade)');
      if(typeof onModel === 'function') onModel({ status: 'error', recommendation: 'N/A', confidence: null, summary: 'Model analysis is not available for stock ideas.' });
      return false;
    }

    /* Dedupe guard — only one request per opportunity at a time */
    const opKey = idea._opKey || opportunityKey(idea, -1);
    if(_homeModelInFlight.has(opKey)){
      console.info(_tag, 'dedupe guard — already in-flight for', opKey);
      return false;
    }
    _homeModelInFlight.add(opKey);
    console.info(_tag, 'start', { opKey, originTag, symbol: idea?.symbol, strategy: idea?.strategy });

    const resolvedIdea = resolveIdeaForModel(idea);
    let sourceFile;
    try{
      sourceFile = await resolveModelSourceFile(resolvedIdea);
    }catch(sfErr){
      console.warn(_tag, 'resolveModelSourceFile threw:', sfErr);
      sourceFile = null;
    }
    if(!sourceFile){
      const nextModel = {
        status: 'error',
        recommendation: 'ERROR',
        confidence: null,
        summary: 'No report source available for model analysis.',
      };
      if(typeof onModel === 'function') onModel(nextModel);
      _homeModelInFlight.delete(opKey);
      return false;
    }

    const tradePayload = {
      ...(resolvedIdea?.trade_payload && typeof resolvedIdea.trade_payload === 'object' ? resolvedIdea.trade_payload : {}),
      ...(resolvedIdea?.trade && typeof resolvedIdea.trade === 'object' ? resolvedIdea.trade : {}),
      symbol: String(resolvedIdea?.trade?.symbol || resolvedIdea?.symbol || '').toUpperCase(),
      strategy_id: String(resolvedIdea?.trade?.strategy_id || resolvedIdea?.strategy || ''),
      home_origin: String(originTag || 'home_opportunities'),
    };
    if(typeof onModel === 'function'){
      onModel({ status: 'running', recommendation: 'RUNNING', confidence: null, summary: 'Running...' });
    }

    console.info(_tag, 'calling api.modelAnalyze', { source: sourceFile, symbol: tradePayload.symbol, strategy_id: tradePayload.strategy_id });
    try{
      const result = await api.modelAnalyze(tradePayload, sourceFile);
      console.info(_tag, 'response OK', { recommendation: result?.evaluated_trade?.model_evaluation?.recommendation });
      const me = result?.evaluated_trade?.model_evaluation || {};
      const engineCalc = result?.evaluated_trade?.engine_calculations || null;
      const nextModel = {
        status: 'available',
        ...me,
        recommendation: String(me?.recommendation || 'NEUTRAL').toUpperCase(),
        confidence: toNumber(me?.confidence),
        summary: String(me?.summary || '').trim(),
        engine_calculations: engineCalc,
      };
      if(typeof onModel === 'function') onModel(nextModel);
      return true;
    }catch(err){
      console.warn(_tag, 'api.modelAnalyze error:', err?.detail || err?.message || err);
      const nextModel = {
        status: 'error',
        recommendation: 'ERROR',
        confidence: null,
        summary: String(err?.detail || err?.message || err || 'Model analysis failed'),
      };
      if(typeof onModel === 'function') onModel(nextModel);
      return false;
    }finally{
      _homeModelInFlight.delete(opKey);
    }
  }

  const metricValueOrMissing = _card.metricValueOrMissing;

  /* renderSourceHealth — REMOVED: Source Health is global-only (index.html / source_health.js) */

  function renderChart(svgEl, history, options){
    if(!svgEl) return;
    const rows = Array.isArray(history) ? history : [];
    const points = rows.map((row) => toNumber(row?.close)).filter((v) => v !== null);
    if(!points.length){
      svgEl.innerHTML = '';
      return;
    }

    /* ── Parse dates (if present) ── */
    const dates = rows.map((row) => {
      if(!row?.date) return null;
      const d = new Date(row.date + 'T00:00:00');
      return isNaN(d.getTime()) ? null : d;
    });
    const hasDates = dates.length === points.length && dates[0] !== null && dates[dates.length - 1] !== null;

    const width = 800;
    const height = 220;
    const margin = { top: 12, right: 12, bottom: hasDates ? 36 : 22, left: 52 };
    const plotW = width - margin.left - margin.right;
    const plotH = height - margin.top - margin.bottom;
    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = Math.max(max - min, 0.0001);

    const yFor = (value) => margin.top + (1 - ((value - min) / span)) * plotH;

    /* ── X scale ── */
    let xFor;
    if(hasDates){
      const t0 = dates[0].getTime();
      const t1 = dates[dates.length - 1].getTime();
      const tSpan = Math.max(t1 - t0, 1);
      xFor = (index) => margin.left + ((dates[index].getTime() - t0) / tSpan) * plotW;
    } else {
      xFor = (index) => margin.left + (index / Math.max(points.length - 1, 1)) * plotW;
    }

    const path = points.map((value, index) => `${index === 0 ? 'M' : 'L'} ${xFor(index).toFixed(2)} ${yFor(value).toFixed(2)}`).join(' ');

    /* ── Y ticks / grid ── */
    const yTicks = Array.from({ length: 4 }, (_, idx) => {
      const ratio = idx / 3;
      const value = max - (span * ratio);
      return { value, y: yFor(value) };
    });

    const yGrid = yTicks.map((tick) => `<line x1="${margin.left}" y1="${tick.y.toFixed(2)}" x2="${(width - margin.right).toFixed(2)}" y2="${tick.y.toFixed(2)}" stroke="rgba(0,234,255,0.12)" stroke-width="1" shape-rendering="crispEdges"></line>`).join('');
    const yLabels = yTicks.map((tick) => `<text x="${(margin.left - 8).toFixed(2)}" y="${(tick.y + 3).toFixed(2)}" text-anchor="end" fill="rgba(215,251,255,0.85)" font-size="10" font-family="var(--font-body)">${Number(tick.value).toFixed(2)}</text>`).join('');

    /* ── X ticks (weekly, only when dates are available) ── */
    let xGrid = '';
    let xLabels = '';
    if(hasDates){
      const t0 = dates[0].getTime();
      const t1 = dates[dates.length - 1].getTime();
      const tSpan = Math.max(t1 - t0, 1);
      const xPixel = (ms) => margin.left + ((ms - t0) / tSpan) * plotW;

      /* Find first Monday on or after the start date */
      const start = new Date(dates[0]);
      const dayOfWeek = start.getDay();          // 0=Sun … 6=Sat
      const daysToMon = dayOfWeek === 0 ? 1 : (dayOfWeek <= 1 ? (1 - dayOfWeek) : (8 - dayOfWeek));
      const firstMon = new Date(start);
      firstMon.setDate(firstMon.getDate() + daysToMon);

      /* Determine tick interval — keep ~6-12 visible labels */
      const totalWeeks = Math.round((t1 - t0) / (7 * 86400000));
      let weekStep = 1;
      if(totalWeeks > 36) weekStep = 4;
      else if(totalWeeks > 18) weekStep = 2;

      const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      const tickLines = [];
      const tickLabels = [];
      let cursor = new Date(firstMon);
      while(cursor.getTime() <= t1){
        const px = xPixel(cursor.getTime());
        if(px >= margin.left && px <= width - margin.right){
          const yBottom = height - margin.bottom;
          tickLines.push(`<line x1="${px.toFixed(2)}" y1="${margin.top}" x2="${px.toFixed(2)}" y2="${yBottom.toFixed(2)}" stroke="rgba(0,234,255,0.08)" stroke-width="1" shape-rendering="crispEdges"></line>`);
          tickLabels.push(`<text x="${px.toFixed(2)}" y="${(yBottom + 14).toFixed(2)}" text-anchor="middle" fill="rgba(215,251,255,0.7)" font-size="9" font-family="var(--font-body)">${monthNames[cursor.getMonth()]} ${cursor.getDate()}</text>`);
        }
        cursor.setDate(cursor.getDate() + 7 * weekStep);
      }
      xGrid = tickLines.join('');
      xLabels = tickLabels.join('');
    }

    svgEl.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svgEl.innerHTML = `
      ${yGrid}
      ${xGrid}
      <line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${(height - margin.bottom).toFixed(2)}" stroke="rgba(0,234,255,0.45)" stroke-width="1" shape-rendering="crispEdges"></line>
      <line x1="${margin.left}" y1="${(height - margin.bottom).toFixed(2)}" x2="${(width - margin.right).toFixed(2)}" y2="${(height - margin.bottom).toFixed(2)}" stroke="rgba(0,234,255,0.45)" stroke-width="1" shape-rendering="crispEdges"></line>
      ${yLabels}
      ${xLabels}
      <path d="${path}" fill="none" stroke="${options?.stroke || 'rgba(0,234,255,0.95)'}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"></path>
    `;
  }

  /* ── Compact proxy mini-chart renderer ──────────────────────── */

  /**
   * Render a single compact mini-chart SVG for a broad-market proxy.
   *
   * @param {SVGElement} svgEl — target SVG element
   * @param {Array<{date:string, close:number}>} history — daily close data
   * @param {Object} opts — { symbol, changePct, stroke }
   */
  function renderMiniChart(svgEl, history, opts){
    const rows = Array.isArray(history) ? history : [];
    const points = rows.map(r => toNumber(r?.close)).filter(v => v !== null);
    if(!points.length){
      svgEl.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="rgba(147,167,182,0.6)" font-size="11">No data</text>';
      return;
    }

    const width = 320;
    const height = 120;
    const margin = { top: 24, right: 10, bottom: 20, left: 38 };
    const plotW = width - margin.left - margin.right;
    const plotH = height - margin.top - margin.bottom;
    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = Math.max(max - min, 0.0001);

    const yFor = v => margin.top + (1 - ((v - min) / span)) * plotH;

    /* Parse dates for day-boundary detection */
    const dates = rows.map(r => {
      if(!r?.date) return null;
      const raw = String(r.date);
      const d = raw.includes('T') ? new Date(raw) : new Date(raw + 'T00:00:00');
      return isNaN(d.getTime()) ? null : d;
    });
    const hasDates = dates.length === points.length && dates[0] !== null;

    /* X scale — INDEX-based (trading-session progression).
       Eliminates weekend/non-trading gaps by spacing points evenly. */
    const xFor = i => margin.left + (i / Math.max(points.length - 1, 1)) * plotW;

    /* Build Catmull-Rom smooth curve through data points.
       Tension divisor /10 keeps curves close to actual data while
       eliminating jagged noise (previous /6 was too wavy). */
    const pts = points.map((v, i) => ({ x: xFor(i), y: yFor(v) }));
    let linePath;
    if(pts.length <= 2){
      linePath = pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    } else {
      linePath = `M ${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}`;
      for(let i = 0; i < pts.length - 1; i++){
        const p0 = pts[Math.max(i - 1, 0)];
        const p1 = pts[i];
        const p2 = pts[i + 1];
        const p3 = pts[Math.min(i + 2, pts.length - 1)];
        const cp1x = p1.x + (p2.x - p0.x) / 10;
        const cp1y = p1.y + (p2.y - p0.y) / 10;
        const cp2x = p2.x - (p3.x - p1.x) / 10;
        const cp2y = p2.y - (p3.y - p1.y) / 10;
        linePath += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)}, ${cp2x.toFixed(1)} ${cp2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
      }
    }
    const fillPath = linePath + ` L ${pts[pts.length - 1].x.toFixed(1)} ${(height - margin.bottom).toFixed(1)} L ${pts[0].x.toFixed(1)} ${(height - margin.bottom).toFixed(1)} Z`;

    /* Gradient fill under the line — color driven by positive/negative move.
       Positive (+) → green (matching yield curve bullish tone).
       Negative (−) → burnt-red gradient.
       Neutral/unknown → default cyan.
       ID must be alphanumeric only — spaces/dashes break url(#id) refs. */
    const safeId = (opts?.symbol || '').replace(/[^A-Za-z0-9]/g, '') || Math.random().toString(36).slice(2, 8);
    const gradientId = 'proxyGrad_' + safeId;
    const glowId = 'proxyGlow_' + safeId;
    const changePctVal = opts?.changePct;
    const isPositive = changePctVal != null && changePctVal >= 0;
    const isNegative = changePctVal != null && changePctVal < 0;
    /* Green gradient stops (positive) */
    const gradTop    = isPositive ? 'rgb(126,247,184)' : isNegative ? 'rgb(200,80,80)' : 'rgb(0,220,245)';
    const gradMid    = isPositive ? 'rgb(100,220,155)' : isNegative ? 'rgb(180,60,60)' : 'rgb(0,200,235)';
    const gradBottom = isPositive ? 'rgb(80,195,130)'  : isNegative ? 'rgb(160,46,46)' : 'rgb(0,180,220)';
    const gradientDef = `
      <defs>
        <linearGradient id="${gradientId}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${gradTop}" stop-opacity="0.30"/>
          <stop offset="50%" stop-color="${gradMid}" stop-opacity="0.12"/>
          <stop offset="100%" stop-color="${gradBottom}" stop-opacity="0.02"/>
        </linearGradient>
        <filter id="${glowId}" x="-4%" y="-4%" width="108%" height="108%">
          <feGaussianBlur in="SourceGraphic" stdDeviation="0.5" result="blur"/>
          <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>`;

    /* Y grid — 3 horizontal lines, subtler */
    const yTicks = [0, 0.5, 1].map(r => {
      const v = max - span * r;
      return { v, y: yFor(v) };
    });
    const yGrid = yTicks.map(t => `<line x1="${margin.left}" y1="${t.y.toFixed(1)}" x2="${(width - margin.right)}" y2="${t.y.toFixed(1)}" stroke="rgba(0,234,255,0.06)" stroke-width="0.5" shape-rendering="crispEdges"/>`).join('');
    const yLabels = yTicks.map(t => `<text x="${margin.left - 4}" y="${(t.y + 3).toFixed(1)}" text-anchor="end" fill="rgba(215,251,255,0.50)" font-size="7.5" font-family="var(--font-body)">${t.v.toFixed(1)}</text>`).join('');

    /* X labels + subtle day separators using INDEX-based positioning */
    let xLabels = '';
    let dayGridLines = '';
    if(hasDates){
      const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      const first = dates[0];
      const last = dates[dates.length - 1];
      const daySpan = (last.getTime() - first.getTime()) / 86400000;
      const yBase = height - margin.bottom + 12;
      const fmtDate = (d) => {
        return daySpan <= 30
          ? `${months[d.getMonth()]} ${d.getDate()}`
          : `${months[d.getMonth()]} ${d.getFullYear().toString().slice(2)}`;
      };
      xLabels = `
        <text x="${margin.left}" y="${yBase}" fill="rgba(215,251,255,0.45)" font-size="7.5" font-family="var(--font-body)">${fmtDate(first)}</text>
        <text x="${(width - margin.right)}" y="${yBase}" text-anchor="end" fill="rgba(215,251,255,0.45)" font-size="7.5" font-family="var(--font-body)">${fmtDate(last)}</text>`;

      /* Day separators — very subtle, only at actual day transitions */
      if(daySpan > 1){
        const seenDays = new Set();
        seenDays.add(dates[0].toDateString());
        for(let di = 1; di < dates.length; di++){
          const ds = dates[di].toDateString();
          if(!seenDays.has(ds)){
            seenDays.add(ds);
            const x = xFor(di).toFixed(1);
            dayGridLines += `<line x1="${x}" y1="${margin.top}" x2="${x}" y2="${(height - margin.bottom)}" stroke="rgba(0,234,255,0.05)" stroke-width="0.5" shape-rendering="crispEdges"/>`;
          }
        }
      }
    }

    /* Symbol label + return annotation */
    const symbol = _esc(opts?.symbol || '');
    const changePct = opts?.changePct;
    const returnStr = changePct != null ? (changePct >= 0 ? '+' : '') + (changePct * 100).toFixed(1) + '%' : '';
    const isNeg = changePct != null && changePct < 0;
    /* Burnt-red gradient text for negative returns, green for positive */
    const returnColor = changePct != null
      ? (changePct >= 0 ? 'rgba(126,247,184,0.9)' : 'rgba(200,80,80,0.95)')
      : 'rgba(147,167,182,0.6)';
    /* Stroke also follows positive/negative direction */
    const stroke = opts?.stroke || (isPositive ? 'rgba(126,247,184,0.80)' : isNegative ? 'rgba(200,80,80,0.80)' : 'rgba(0,234,255,0.80)');

    /* Replace entire SVG element so <defs> gradient IDs register properly.
       Setting innerHTML on an existing SVG doesn't register defs in all
       browsers, causing fill="url(#…)" to fall back to black. */
    const svgMarkup = `<svg class="home-regime-proxy-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      ${gradientDef}
      ${yGrid}
      ${dayGridLines}
      <text x="${margin.left}" y="14" fill="rgba(215,251,255,0.9)" font-size="11" font-weight="600" font-family="var(--font-body)">${symbol}</text>
      <text x="${width - margin.right}" y="14" text-anchor="end" fill="${returnColor}" font-size="10" font-weight="500" font-family="var(--font-body)">${returnStr}</text>
      <path d="${fillPath}" fill="url(#${gradientId})" />
      <path d="${linePath}" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" filter="url(#${glowId})"/>
      ${yLabels}
      ${xLabels}
    </svg>`;
    svgEl.outerHTML = svgMarkup;
  }

  /**
   * Fetch and render macro proxy charts + yield curve in the regime panel.
   * Fire-and-forget — called during snapshot render.
   */
  function loadAndRenderRegimeProxies(macro){
    if(!regimeProxiesEl) return;
    api.getRegimeProxies().then(function(resp){
      const proxies = resp?.proxies || {};
      const symbols = ['VTI', 'VXUS', 'EFA', 'BND', 'TLT', 'UUP', 'HYG', 'LQD'];
      const labels = {
        VTI: 'Total US Market',
        VXUS: 'International Equity',
        EFA: 'Developed Intl',
        BND: 'US Bond Aggregate',
        TLT: '20+ Yr Treasury',
        UUP: 'US Dollar Index',
        HYG: 'High-Yield Credit',
        LQD: 'IG Corporate Bonds',
      };
      if(!Object.keys(proxies).length){
        regimeProxiesEl.innerHTML = '<div class="stock-note" style="padding:8px;">Proxy chart data unavailable.</div>';
        return;
      }
      let html = '';
      symbols.forEach(function(sym){
        const entry = proxies[sym];
        if(!entry) return;
        const barTag = (entry.bar_size === '1h' || entry.bar_size === '15min') ? '1h' : 'D';
        const yahooUrl = 'https://finance.yahoo.com/quote/' + encodeURIComponent(sym);
        html += `<a class="home-chart-link home-chart-link--proxy" href="${yahooUrl}" target="_blank" rel="noopener noreferrer" title="Open ${_esc(sym)} on Yahoo Finance">
          <div class="home-regime-proxy-card">
          <span class="home-proxy-bar-tag">${barTag}</span>
          <svg class="home-regime-proxy-svg" viewBox="0 0 320 120" preserveAspectRatio="none"></svg>
        </div></a>`;
      });
      // Append yield curve chart as the last tile in the grid
      html += _buildYieldCurveChartCard(macro || {});
      regimeProxiesEl.innerHTML = html;
      const svgs = regimeProxiesEl.querySelectorAll('.home-regime-proxy-svg');
      let idx = 0;
      symbols.forEach(function(sym){
        const entry = proxies[sym];
        if(!entry || !svgs[idx]) return;
        renderMiniChart(svgs[idx], entry.history || [], {
          symbol: sym + ' — ' + (labels[sym] || ''),
          changePct: entry.change_pct,
        });
        idx++;
      });
    }).catch(function(err){
      console.warn('[RegimeProxies] fetch failed:', err?.message || err);
      if(regimeProxiesEl){
        regimeProxiesEl.innerHTML = '<div class="stock-note" style="padding:8px;">Proxy charts unavailable.</div>';
      }
    });
  }

  /* ── Dynamic Regime Summary Tooltip Builder ─────────────────── */

  const FACTOR_NAMES = {
    trend:      'Trend & Structure',
    volatility: 'Volatility & Options Tone',
    breadth:    'Breadth & Participation',
    rates:      'Rates & Macro Pressure',
    momentum:   'Momentum',
  };

  function _volInterpretation(vix){
    if(vix === null || vix === undefined) return 'unavailable';
    if(vix < 16)  return 'low / compressed';
    if(vix <= 22) return 'normal';
    return 'elevated / riskier';
  }

  function _ratesInterpretation(tenYear){
    if(tenYear === null || tenYear === undefined) return 'unavailable';
    if(tenYear > 4.8)  return 'tightening — pressure on equities';
    if(tenYear > 4.2) return 'mildly restrictive';
    return 'supportive';
  }

  /**
   * Synthesize a concise market-picture read from the 5-factor regime data + macro.
   *
   * Inputs:
   *   regimeState:  'RISK_ON' | 'NEUTRAL' | 'RISK_OFF'
   *   regimeScore:  0-100 composite
   *   components:   { trend:{score,signals}, volatility:{score,signals}, ... }
   *   vix:          number | null
   *   tenYear:      number | null
   *   macro:        flat macro object (yield_curve_spread, oil_wti, etc.)
   *
   * Returns: { envLabel, envSummary, driverLine, toneChips: [{label,tone}] }
   *   envLabel    — short environment phrase (e.g. "Constructive risk-on tape")
   *   envSummary  — one-sentence market read
   *   driverLine  — what's driving the current regime
   *   toneChips   — compact signal chips for supporting metadata
   */
  function _synthesizeMarketPicture(regimeState, regimeScore, components, vix, tenYear, macro){
    const comps = components || {};
    const score = (k) => { const v = toNumber(comps[k]?.score); return v !== null ? Math.max(0, Math.min(100, v)) : 50; };
    const trendScore = score('trend');
    const volScore = score('volatility');
    const breadthScore = score('breadth');
    const ratesScore = score('rates');
    const momentumScore = score('momentum');

    /* ── Environment label ── */
    let envLabel;
    if(regimeState === 'RISK_ON'){
      envLabel = regimeScore >= 80 ? 'Strong risk-on tape' : 'Constructive risk-on tape';
    } else if(regimeState === 'RISK_OFF'){
      envLabel = regimeScore < 25 ? 'Broad risk-off conditions' : 'Risk-off bias';
    } else {
      envLabel = regimeScore >= 55 ? 'Mixed-to-constructive environment' : 'Mixed / range-bound environment';
    }

    /* ── Sort factors for driver / weakness identification ── */
    const factors = [
      { key: 'trend', score: trendScore },
      { key: 'volatility', score: volScore },
      { key: 'breadth', score: breadthScore },
      { key: 'rates', score: ratesScore },
      { key: 'momentum', score: momentumScore },
    ].sort((a, b) => b.score - a.score);
    const top = factors[0];
    const weak = factors[factors.length - 1];

    /* ── Environment summary ── */
    let envSummary;
    if(regimeState === 'RISK_ON'){
      envSummary = `Market structure supports risk assets. ${FACTOR_NAMES[top.key]} anchors the read` +
        (weak.score < 55 ? ` while ${FACTOR_NAMES[weak.key]} is the main watch item.` : '.');
    } else if(regimeState === 'RISK_OFF'){
      envSummary = `Conditions less supportive for risk. ${FACTOR_NAMES[weak.key]} is the primary pressure point` +
        (top.score > 60 ? ` though ${FACTOR_NAMES[top.key]} provides some offset.` : '.');
    } else {
      envSummary = `Directional edge is limited. ` +
        `${FACTOR_NAMES[top.key]} offers strength but ${FACTOR_NAMES[weak.key]} limits confidence.`;
    }

    /* ── Driver line ── */
    const driverLine = `Led by ${FACTOR_NAMES[top.key]} (${Math.round(top.score)})` +
      (weak.score < 55 ? ` · Watch ${FACTOR_NAMES[weak.key]} (${Math.round(weak.score)})` : '');

    /* ── Tone chips — derived from existing data ── */
    const chips = [];

    // Volatility tone
    const volInterp = _volInterpretation(vix);
    chips.push({ label: `Vol: ${volInterp}`, tone: vix !== null && vix < 18 ? 'bullish' : (vix !== null && vix > 25 ? 'riskoff' : 'neutral') });

    // Breadth tone
    const breadthTone = breadthScore >= 65 ? 'bullish' : (breadthScore < 40 ? 'riskoff' : 'neutral');
    const breadthLabel = breadthScore >= 65 ? 'broad' : (breadthScore < 40 ? 'narrow' : 'selective');
    chips.push({ label: `Breadth: ${breadthLabel}`, tone: breadthTone });

    // Rates / macro tone
    const ratesInterp = _ratesInterpretation(tenYear);
    chips.push({ label: `Rates: ${ratesInterp}`, tone: ratesScore >= 60 ? 'bullish' : (ratesScore < 40 ? 'riskoff' : 'neutral') });

    // Yield curve if available
    const ycSpread = toNumber(macro?.yield_curve_spread);
    if(ycSpread !== null){
      const ycLabel = ycSpread < 0 ? 'inverted' : (ycSpread < 0.25 ? 'flat' : 'normal');
      chips.push({ label: `Curve: ${ycLabel}`, tone: ycSpread < 0 ? 'riskoff' : (ycSpread < 0.25 ? 'neutral' : 'bullish') });
    }

    // Momentum tone
    const momTone = momentumScore >= 60 ? 'bullish' : (momentumScore < 40 ? 'riskoff' : 'neutral');
    chips.push({ label: `Momentum: ${momentumScore >= 60 ? 'favorable' : (momentumScore < 40 ? 'fading' : 'mixed')}`, tone: momTone });

    return { envLabel, envSummary, driverLine, toneChips: chips };
  }

  /**
   * Derive tone chips from the three-block engine output.
   * Each block produces one chip with its label and a tone class.
   */
  function _buildBlockToneChips(blocks) {
    const BLOCK_META = [
      ['structural', 'Structure'],
      ['tape',       'Tape'],
      ['tactical',   'Tactical'],
    ];
    const chips = [];
    for (const [key, title] of BLOCK_META) {
      const b = blocks[key];
      if (!b) continue;
      const score = toNumber(b.score);
      const label = b.label || '—';
      let tone = 'neutral';
      if (score !== null) {
        tone = score >= 60 ? 'bullish' : (score < 40 ? 'riskoff' : 'neutral');
      }
      chips.push({ label: `${title}: ${label}`, tone });
    }
    return chips;
  }

  /**
   * Build a dynamic regime tooltip from live data.
   *
   * Inputs:
   *   regimeState: 'RISK_ON' | 'NEUTRAL' | 'RISK_OFF'
   *   regimeScore: 0-100 total score
   *   components:  { trend: {score, signals}, volatility: {score, signals}, ... }
   *   vix:         number | null
   *   tenYear:     number | null
   *
   * Returns: { title, lines[] } for BenTooltip buildHtml.
   */
  function _buildRegimeSummaryTip(regimeState, regimeScore, components, vix, tenYear){
    const factors = ['trend', 'volatility', 'breadth', 'rates', 'momentum'];
    const scored = factors.map((k) => {
      const item = (components || {})[k] || {};
      const score = toNumber(item?.score);
      const signals = Array.isArray(item?.signals) ? item.signals : [];
      return { key: k, score: score !== null ? Math.max(0, Math.min(100, score)) : 0, signals };
    });
    const sorted = scored.slice().sort((a, b) => b.score - a.score);
    const top1 = sorted[0];
    const weak = sorted[sorted.length - 1];

    const volInterp = _volInterpretation(vix);
    const ratesInterp = _ratesInterpretation(tenYear);
    const breadthItem = scored.find((s) => s.key === 'breadth');
    const breadthFact = (breadthItem && breadthItem.signals.length) ? breadthItem.signals[0] : 'participation data unavailable';

    const lines = [];

    if(regimeState === 'RISK_ON'){
      lines.push('Market picture supports risk assets across trend, breadth, and positioning.');
      lines.push(`Lead factors: ${FACTOR_NAMES[top1.key]} (${Math.round(top1.score)}).`);
      if(weak.score < 60) lines.push(`Watch: ${FACTOR_NAMES[weak.key]} (${Math.round(weak.score)}).`);
    } else if(regimeState === 'RISK_OFF'){
      lines.push('Market picture is less supportive — multiple factors under pressure.');
      lines.push(`Pressure: ${FACTOR_NAMES[weak.key]} (${Math.round(weak.score)}).`);
    } else {
      lines.push('Market picture is mixed — limited directional conviction.');
      lines.push(`Strength: ${FACTOR_NAMES[top1.key]} (${Math.round(top1.score)}) · Drag: ${FACTOR_NAMES[weak.key]} (${Math.round(weak.score)}).`);
    }
    lines.push(`Vol tone: VIX ${vix != null ? fmt(vix) : '—'} (${volInterp})`);
    lines.push(`Rates: 10Y ${tenYear != null ? fmt(tenYear, 2) + '%' : '—'} (${ratesInterp})`);
    lines.push(`Breadth: ${breadthFact}`);

    const labelMap = { RISK_ON: 'Risk-On', RISK_OFF: 'Risk-Off', NEUTRAL: 'Neutral' };
    return {
      title: `Market Picture — ${labelMap[regimeState] || 'Regime'} (${fmt(regimeScore, 1)}/100)`,
      lines,
    };
  }

  /**
   * Register the dynamic regime_summary tooltip so BenTradeBenTooltip
   * can call it as a function each time the tooltip is shown.
   */
  function _registerRegimeSummaryTooltip(regimeState, regimeScore, components, vix, tenYear){
    if(!window.BenTradeBenTooltip?.register) return;
    window.BenTradeBenTooltip.register('regime_summary', function(){
      return _buildRegimeSummaryTip(regimeState, regimeScore, components, vix, tenYear);
    });
  }

  /* ── Regime label mapping (5-tier) ── */
  const REGIME_LABEL_MAP = {
    RISK_ON:          'Risk-On',
    RISK_ON_CAUTIOUS: 'Risk-On Cautious',
    NEUTRAL:          'Neutral',
    RISK_OFF_CAUTION: 'Risk-Off Caution',
    RISK_OFF:         'Risk-Off',
  };

  function _regimeTone(label){
    if(label === 'RISK_ON' || label === 'RISK_ON_CAUTIOUS') return 'bullish';
    if(label === 'RISK_OFF' || label === 'RISK_OFF_CAUTION') return 'riskoff';
    return 'neutral';
  }

  function _blockTone(score){
    if(score == null) return 'neutral';
    if(score >= 60) return 'bullish';
    if(score < 40) return 'riskoff';
    return 'neutral';
  }

  function renderRegime(regimePayload, spySummary, macro, indexSummaries){
    const vix = toNumber(macro?.vix ?? spySummary?.options_context?.vix);
    const tenYear = toNumber(macro?.ten_year_yield);
    const regimeScore = toNumber(regimePayload?.regime_score) ?? 50;
    const confidence = toNumber(regimePayload?.confidence);
    const interpretation = regimePayload?.interpretation || '';
    const regimeLabelRaw = String(regimePayload?.regime_label || 'NEUTRAL').toUpperCase();
    const regimeLabelText = REGIME_LABEL_MAP[regimeLabelRaw] || 'Neutral';
    const tone = _regimeTone(regimeLabelRaw);

    // Three blocks from new engine (may be absent for legacy payloads)
    const blocks = regimePayload?.blocks || {};
    const hasBlocks = !!(blocks.structural || blocks.tape || blocks.tactical);
    const agreement = regimePayload?.agreement || {};

    /* ── Fallback: synthesize environment summary from legacy components when blocks unavailable ── */
    const mp = hasBlocks
      ? { envLabel: interpretation, envSummary: '' }
      : _synthesizeMarketPicture(regimeLabelRaw, regimeScore, regimePayload?.components || {}, vix, tenYear, macro || {});

    /* ── Confidence badge ── */
    const confStr = confidence != null ? `${(confidence * 100).toFixed(0)}%` : '';
    const confBadge = confStr ? `<span class="home-regime-confidence">Conf: ${confStr}</span>` : '';

    /* ── Agreement indicator ── */
    const agreementBadge = hasBlocks
      ? (agreement.blocks_aligned
        ? '<span class="home-regime-agreement aligned">Blocks Aligned</span>'
        : `<span class="home-regime-agreement divergent">Divergent (spread ${agreement.max_spread || '?'})</span>`)
      : '';

    /* ── Hero strip: regime pill + env summary + confidence (full-width, no right-side clutter) ── */
    const envLabel = hasBlocks ? interpretation : mp.envLabel;
    const envSummary = hasBlocks ? '' : mp.envSummary;
    regimeStripEl.innerHTML = `
      <div class="home-regime-hero">
        <div class="home-regime-hero-left">
          <div class="home-regime-pill ${tone}" data-ben-tip="regime_summary">
            <span class="home-regime-pill-label">${_esc(regimeLabelText)}</span>
            <span class="home-regime-pill-score">${fmt(regimeScore, 0)}</span>
          </div>
          <div class="home-regime-env">
            <div class="home-regime-env-label">${_esc(envLabel)}</div>
            ${envSummary ? `<div class="home-regime-env-summary stock-note">${_esc(envSummary)}</div>` : ''}
            <div class="home-regime-meta-row">${confBadge}${agreementBadge}</div>
          </div>
        </div>
      </div>
    `;

    /* ── Register dynamic regime-summary tooltip ── */
    _registerRegimeSummaryTooltip(regimeLabelRaw, regimeScore, regimePayload?.components || {}, vix, tenYear);

    /* ── Three-block status cards ── */
    if(regimeBlocksEl){
      if(hasBlocks){
        const BLOCK_TITLES = { structural: 'Structural', tape: 'Tape', tactical: 'Tactical' };
        const BLOCK_ICONS = {
          structural: '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><rect x="1" y="8" width="3" height="5" rx="0.5" fill="rgba(0,220,245,0.7)"/><rect x="5.5" y="4" width="3" height="9" rx="0.5" fill="rgba(0,220,245,0.85)"/><rect x="10" y="1" width="3" height="12" rx="0.5" fill="rgba(0,220,245,1)"/></svg>',
          tape: '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><polyline points="1,10 4,6 7,8 10,3 13,5" stroke="rgba(0,220,245,0.9)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/><circle cx="10" cy="3" r="1.2" fill="rgba(0,220,245,0.9)"/></svg>',
          tactical: '<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="7" cy="7" r="5" stroke="rgba(0,220,245,0.7)" stroke-width="1.2" fill="none"/><circle cx="7" cy="7" r="2" stroke="rgba(0,220,245,0.9)" stroke-width="1" fill="none"/><circle cx="7" cy="7" r="0.8" fill="rgba(0,220,245,1)"/></svg>',
        };
        regimeBlocksEl.innerHTML = ['structural', 'tape', 'tactical'].map((bk) => {
          const b = blocks[bk] || {};
          const bScore = toNumber(b.score) ?? 0;
          const bLabel = b.label || '—';
          const bConf = b.confidence != null ? `${(b.confidence * 100).toFixed(0)}%` : '';
          const bTone = _blockTone(bScore);
          const bSignals = Array.isArray(b.key_signals) ? b.key_signals.slice(0, 3) : [];
          return `
            <div class="home-regime-block-card ${bTone}">
              <div class="home-regime-block-header">
                <span class="home-regime-block-icon">${BLOCK_ICONS[bk]}</span>
                <span class="home-regime-block-title">${BLOCK_TITLES[bk]}</span>
                <span class="home-regime-block-label">${_esc(bLabel)}</span>
              </div>
              <div class="home-regime-block-score-row">
                <div class="home-regime-block-bar"><div class="home-regime-block-fill" style="width:${Math.max(2, Math.round(bScore))}%;"></div></div>
                <span class="home-regime-block-score">${Math.round(bScore)}</span>
                ${bConf ? `<span class="home-regime-block-conf">${bConf}</span>` : ''}
              </div>
              ${bSignals.length ? `<div class="home-regime-block-signals">${bSignals.map((s) => `<span class="home-regime-block-signal">${_esc(String(s))}</span>`).join('')}</div>` : ''}
            </div>`;
        }).join('');
        regimeBlocksEl.style.display = '';
      } else {
        regimeBlocksEl.innerHTML = '';
        regimeBlocksEl.style.display = 'none';
      }
    }

    /* ── Decision insights: what works / avoid / triggers / drivers ── */
    if(regimeInsightsEl){
      const whatWorks = Array.isArray(regimePayload?.what_works) ? regimePayload.what_works : [];
      const whatAvoid = Array.isArray(regimePayload?.what_to_avoid) ? regimePayload.what_to_avoid : [];
      const triggers = Array.isArray(regimePayload?.change_triggers) ? regimePayload.change_triggers : [];
      const drivers = Array.isArray(regimePayload?.key_drivers) ? regimePayload.key_drivers : [];
      const hasInsights = whatWorks.length || whatAvoid.length || triggers.length || drivers.length;

      if(hasInsights){
        let insightHtml = '<div class="home-regime-insights-grid">';
        if(whatWorks.length){
          insightHtml += `<div class="home-regime-insight-col"><div class="home-regime-insight-title bullish">What Works</div><ul class="home-regime-insight-list">${whatWorks.map((w) => `<li>${_esc(String(w))}</li>`).join('')}</ul></div>`;
        }
        if(whatAvoid.length){
          insightHtml += `<div class="home-regime-insight-col"><div class="home-regime-insight-title riskoff">What to Avoid</div><ul class="home-regime-insight-list">${whatAvoid.map((w) => `<li>${_esc(String(w))}</li>`).join('')}</ul></div>`;
        }
        if(drivers.length){
          insightHtml += `<div class="home-regime-insight-col"><div class="home-regime-insight-title">Key Drivers</div><ul class="home-regime-insight-list">${drivers.map((d) => `<li>${_esc(String(d))}</li>`).join('')}</ul></div>`;
        }
        if(triggers.length){
          insightHtml += `<div class="home-regime-insight-col"><div class="home-regime-insight-title">Change Triggers</div><ul class="home-regime-insight-list">${triggers.map((t) => `<li>${_esc(String(t))}</li>`).join('')}</ul></div>`;
        }
        insightHtml += '</div>';
        regimeInsightsEl.innerHTML = insightHtml;
        regimeInsightsEl.style.display = '';
      } else {
        regimeInsightsEl.innerHTML = '';
        regimeInsightsEl.style.display = 'none';
      }
    }

  }

  /**
   * Render a real yield-curve SVG chart inside a proxy-card-style tile.
   * Plots available tenor points (FF, 2Y, 10Y, 30Y) on maturity × yield axes.
   * Returns the HTML string for insertion into the proxy grid.
   */
  function _buildYieldCurveChartCard(macro){
    const ff = toNumber(macro?.fed_funds_rate);
    const twoY = toNumber(macro?.two_year_yield);
    const tenY = toNumber(macro?.ten_year_yield);
    const thirtyY = toNumber(macro?.thirty_year_yield);

    const tenors = [];
    if(ff !== null) tenors.push({ label: 'FF', maturity: 0.08, yield: ff });
    if(twoY !== null) tenors.push({ label: '2Y', maturity: 2, yield: twoY });
    if(tenY !== null) tenors.push({ label: '10Y', maturity: 10, yield: tenY });
    if(thirtyY !== null) tenors.push({ label: '30Y', maturity: 30, yield: thirtyY });

    if(tenors.length < 2){
      return `<div class="home-regime-proxy-card"><div class="stock-note" style="padding:16px 8px;text-align:center;">Yield curve data unavailable</div></div>`;
    }

    // Determine shape classification using standard 10Y-2Y spread
    // Fallback priority: 10Y-2Y (standard) → 10Y-FF → 30Y-2Y
    let spread = null;
    let spreadLabel = '';
    if(tenY !== null && twoY !== null){
      spread = tenY - twoY;
      spreadLabel = '10Y-2Y';
    } else if(tenY !== null && ff !== null){
      spread = tenY - ff;
      spreadLabel = '10Y-FF';
    } else if(thirtyY !== null && twoY !== null){
      spread = thirtyY - twoY;
      spreadLabel = '30Y-2Y';
    }
    let shape = 'Unknown', shapeTone = 'neutral';
    if(spread !== null){
      if(spread < -0.25){ shape = 'Inverted'; shapeTone = 'riskoff'; }
      else if(spread < 0){ shape = 'Shallow Inversion'; shapeTone = 'caution'; }
      else if(spread < 0.25){ shape = 'Flat'; shapeTone = 'neutral'; }
      else if(spread < 1.0){ shape = 'Normal'; shapeTone = 'bullish'; }
      else { shape = 'Steep'; shapeTone = 'bullish'; }
    }

    // SVG chart dimensions
    const W = 320, H = 120;
    const m = { top: 26, right: 12, bottom: 22, left: 36 };
    const pW = W - m.left - m.right;
    const pH = H - m.top - m.bottom;

    const yields = tenors.map(t => t.yield);
    const yMin = Math.min(...yields) - 0.3;
    const yMax = Math.max(...yields) + 0.3;
    const ySpan = Math.max(yMax - yMin, 0.01);
    // x-axis: use log-ish scale so short tenors are more spread out
    const maxMat = tenors[tenors.length - 1].maturity;
    const xMax = Math.max(maxMat * 1.05, 11);
    const logX = mat => Math.log(Math.max(mat, 0.04) + 1);
    const logMax = logX(xMax);
    const xFor = mat => m.left + (logX(mat) / logMax) * pW;
    const yFor = y => m.top + (1 - ((y - yMin) / ySpan)) * pH;

    // Gradient + stroke colors by tone
    const toneColor = { bullish: 'rgba(126,247,184,', riskoff: 'rgba(200,80,80,', caution: 'rgba(255,199,88,', neutral: 'rgba(0,234,255,' };
    const base = toneColor[shapeTone] || toneColor.neutral;
    const strokeColor = base + '0.9)';

    // Build smooth curve via Catmull-Rom interpolation
    const pts = tenors.map(t => ({ x: xFor(t.maturity), y: yFor(t.yield) }));
    let linePath;
    if(pts.length <= 2){
      linePath = pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    } else {
      // Catmull-Rom to cubic Bezier for smooth curve
      linePath = `M ${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}`;
      for(let i = 0; i < pts.length - 1; i++){
        const p0 = pts[Math.max(i - 1, 0)];
        const p1 = pts[i];
        const p2 = pts[i + 1];
        const p3 = pts[Math.min(i + 2, pts.length - 1)];
        const cp1x = p1.x + (p2.x - p0.x) / 6;
        const cp1y = p1.y + (p2.y - p0.y) / 6;
        const cp2x = p2.x - (p3.x - p1.x) / 6;
        const cp2y = p2.y - (p3.y - p1.y) / 6;
        linePath += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)}, ${cp2x.toFixed(1)} ${cp2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
      }
    }
    const fillPath = linePath + ` L ${pts[pts.length - 1].x.toFixed(1)} ${(H - m.bottom).toFixed(1)} L ${pts[0].x.toFixed(1)} ${(H - m.bottom).toFixed(1)} Z`;

    // Y grid lines
    const gridCount = 3;
    let yGrid = '', yLabels = '';
    for(let i = 0; i <= gridCount; i++){
      const v = yMin + (ySpan * i / gridCount);
      const y = yFor(v);
      yGrid += `<line x1="${m.left}" y1="${y.toFixed(1)}" x2="${(W - m.right)}" y2="${y.toFixed(1)}" stroke="rgba(0,234,255,0.08)" stroke-width="0.5" shape-rendering="crispEdges"/>`;
      yLabels += `<text x="${m.left - 4}" y="${(y + 3).toFixed(1)}" text-anchor="end" fill="rgba(215,251,255,0.55)" font-size="8" font-family="var(--font-body)">${v.toFixed(1)}%</text>`;
    }

    // Data point dots + labels
    const dots = pts.map((p, i) =>
      `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3" fill="${strokeColor}" stroke="rgba(0,0,0,0.4)" stroke-width="0.5"/>` +
      `<text x="${p.x.toFixed(1)}" y="${(H - m.bottom + 13).toFixed(1)}" text-anchor="middle" fill="rgba(215,251,255,0.65)" font-size="8" font-family="var(--font-body)">${tenors[i].label}</text>` +
      `<text x="${p.x.toFixed(1)}" y="${(p.y - 6).toFixed(1)}" text-anchor="middle" fill="rgba(215,251,255,0.80)" font-size="7.5" font-weight="600" font-family="var(--font-body)">${tenors[i].yield.toFixed(2)}%</text>`
    ).join('');

    const gradId = 'ycGrad_' + Math.random().toString(36).slice(2, 6);

    const svg = `
      <svg class="home-regime-proxy-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <defs>
          <linearGradient id="${gradId}" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${base}0.18)"/>
            <stop offset="100%" stop-color="${base}0.01)"/>
          </linearGradient>
        </defs>
        ${yGrid}
        <text x="${m.left}" y="14" fill="rgba(215,251,255,0.9)" font-size="11" font-weight="600" font-family="var(--font-body)">Yield Curve</text>
        <text x="${W - m.right}" y="14" text-anchor="end" fill="${strokeColor}" font-size="9.5" font-weight="500" font-family="var(--font-body)">${shape}${spread !== null ? ' (' + (spread >= 0 ? '+' : '') + (spread * 100).toFixed(0) + 'bp ' + spreadLabel + ')' : ''}</text>
        <path d="${fillPath}" fill="url(#${gradId})" />
        <path d="${linePath}" fill="none" stroke="${strokeColor}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
        ${dots}
        ${yLabels}
      </svg>`;

    return `<div class="home-regime-proxy-card">${svg}</div>`;
  }

  /* ── Scoreboard: engine vs model score cards ── */

  function _scoreColor(score){
    if(score == null) return '#888';
    if(score >= 70) return '#7ef7b8';
    if(score >= 50) return '#ffc758';
    return '#c85050';
  }

  /** CSS class for score pill background (replaces inline style colors). */
  function _scoreClass(score){
    if(score == null) return 'home-score-na';
    if(score >= 70) return 'home-score-green';
    if(score >= 50) return 'home-score-amber';
    return 'home-score-red';
  }

  /**
   * Clean a summary string for safe, readable display.
   *
   * Recovery layers (defense-in-depth):
   *   1. Backend _build_plaintext_fallback() (model_analysis.py) extracts
   *      score/summary from raw JSON when LLM returns unparsed JSON text.
   *   2. This function (frontend) catches any remaining JSON-dump summaries
   *      persisted before the backend fix (2026-03-18) and extracts the
   *      .summary field.  Safe to remove the JSON branch below once all
   *      model_scores_latest.json entries have been re-generated.
   *
   * - Escapes HTML entities
   * - Strips markdown artifacts (**, ##, `, etc.)
   * - Collapses whitespace
   * - If the string is a JSON object, extracts the "summary" field from within
   * - Strips raw JSON-like object dumps that remain after extraction
   * Returns null if empty after cleaning.
   */
  function _cleanSummary(raw){
    if(!raw || typeof raw !== 'string') return null;
    let text = String(raw).trim();
    // If the whole summary is a JSON object, try to extract the "summary" field
    if(text.charAt(0) === '{'){
      try{
        var parsed = JSON.parse(text);
        if(parsed && typeof parsed === 'object'){
          // Priority: summary → executive_summary → description → tone+label fallback
          var extracted = parsed.summary || parsed.executive_summary || parsed.description;
          if(typeof extracted === 'string' && extracted.trim()){
            text = extracted.trim();
          } else {
            // Build a readable fallback from available structured fields
            var parts = [];
            if(parsed.tone) parts.push(String(parsed.tone));
            if(parsed.label) parts.push('(' + String(parsed.label) + ')');
            if(parsed.headline_drivers && Array.isArray(parsed.headline_drivers)){
              parts.push('— ' + parsed.headline_drivers.slice(0, 3).join(', '));
            }
            text = parts.length ? parts.join(' ') : '';
          }
        }
      }catch(_e){
        // JSON.parse failed — try regex extraction from malformed JSON
        var summaryMatch = text.match(/"summary"\s*:\s*"((?:[^"\\]|\\.)*)"/);
        if(summaryMatch && summaryMatch[1]){
          text = summaryMatch[1].trim();
        } else {
          // Can't extract anything useful — show a clean message
          text = 'Model analysis completed — re-run recommended for full display.';
        }
      }
    }
    // Strip markdown heading markers
    text = text.replace(/^#{1,6}\s+/gm, '');
    // Strip markdown bold/italic/code
    text = text.replace(/\*{1,2}([^*]+)\*{1,2}/g, '$1');
    text = text.replace(/`([^`]+)`/g, '$1');
    // Strip any remaining raw JSON-like blocks — use greedy nested brace matching
    text = text.replace(/\{(?:[^{}]|\{[^{}]*\})*\}/g, '');
    text = text.replace(/\[[^\]]{10,}\]/g, '');
    // Collapse whitespace
    text = text.replace(/\s+/g, ' ').trim();
    if(!text) return null;
    return _esc(text);
  }

  /**
   * Try to extract a numeric score from a raw JSON model summary string.
   * Returns a number (0-100) or null.
   */
  function _extractScoreFromSummary(raw){
    if(!raw || typeof raw !== 'string') return null;
    var trimmed = raw.trim();
    if(trimmed.charAt(0) !== '{') return null;
    try{
      var parsed = JSON.parse(trimmed);
      if(parsed && typeof parsed === 'object' && parsed.score != null){
        var val = Number(parsed.score);
        if(!isNaN(val) && val >= 0 && val <= 100) return val;
      }
    }catch(_e){
      // JSON.parse failed — try regex extraction from malformed JSON
      var scoreMatch = trimmed.match(/"score"\s*:\s*([\d.]+)/);
      if(scoreMatch){
        var val = Number(scoreMatch[1]);
        if(!isNaN(val) && val >= 0 && val <= 100) return val;
      }
    }
    return null;
  }

  function _fmtScore(val){
    if(val == null) return '—';
    return Number(val).toFixed(1);
  }

  /**
   * Build a human-readable model freshness badge.
   * Returns { text, cssClass } for the stale/missing indicator.
   */
  function _modelFreshnessBadge(eng){
    if(eng.model_score == null){
      return { text: 'Not available', cssClass: 'home-model-badge-na' };
    }
    if(eng.model_fresh === false){
      // Stale — show how old
      var capturedAt = eng.model_captured_at;
      var ageText = '';
      if(capturedAt){
        try{
          var ageMs = Date.now() - new Date(capturedAt).getTime();
          var ageHours = Math.floor(ageMs / (1000 * 60 * 60));
          if(ageHours >= 24){
            ageText = Math.floor(ageHours / 24) + 'd ago';
          } else if(ageHours >= 1){
            ageText = ageHours + 'h ago';
          } else {
            ageText = Math.max(1, Math.floor(ageMs / (1000 * 60))) + 'm ago';
          }
        }catch(_e){}
      }
      return { text: 'Stale' + (ageText ? ' (' + ageText + ')' : ''), cssClass: 'home-model-badge-stale' };
    }
    return { text: '', cssClass: '' };
  }

  function renderScoreboard(scoreboardPayload){
    if(!scoreboardCardsEl) return;
    const sb = (scoreboardPayload && typeof scoreboardPayload === 'object') ? scoreboardPayload : {};
    const engines = Array.isArray(sb.engines) ? sb.engines : [];
    const composite = sb.composite || {};
    const modelStatus = sb.model_status || null;
    const generatedAt = sb.generated_at || null;

    if(!sb.ok || !engines.length){
      scoreboardCardsEl.innerHTML = '<div class="stock-note" style="padding:12px;">Engine scoreboard data unavailable — run a market picture workflow to populate.</div>';
      return;
    }

    // Model scores are now provided by the backend from the durable store — no sessionStorage hydration needed

    // Build engine cards with paired engine vs model layout
    let html = '<div class="home-engine-cards-grid">';
    engines.forEach(function(eng){
      // Recover model_score from raw JSON summary if the backend didn't extract it
      if(eng.model_score == null && eng.model_summary){
        var recovered = _extractScoreFromSummary(eng.model_summary);
        if(recovered !== null) eng.model_score = recovered;
      }
      const eScore = _fmtScore(eng.engine_score);
      const mScore = _fmtScore(eng.model_score);
      const eScoreCls = _scoreClass(eng.engine_score);
      const mScoreCls = _scoreClass(eng.model_score);
      const eLabel = eng.engine_label || '';
      const eSummary = _cleanSummary(eng.engine_summary) || 'No engine summary available.';
      const mSummary = _cleanSummary(eng.model_summary);
      // Degraded badge: only shown when engine_status is not ok/missing.
      // Includes the specific reason when available so the warning is traceable.
      let statusBadge = '';
      if(eng.status !== 'ok' && eng.status !== 'missing'){
        const reasons = Array.isArray(eng.degraded_reasons) && eng.degraded_reasons.length
          ? eng.degraded_reasons.map(function(r){ return _esc(r.replace(/_/g, ' ')); }).join(', ')
          : _esc(eng.status);
        statusBadge = `<span class="qtPill qtPill-warn" style="font-size:10px;margin-left:6px;" title="${reasons}">${_esc(eng.status)}</span>`;
      }
      const freshBadge = _modelFreshnessBadge(eng);
      const modelBadgeHtml = freshBadge.text ? `<span class="home-model-freshness-badge ${freshBadge.cssClass}">${freshBadge.text}</span>` : '';

      // Model status indicator — honest display of model health
      const mStatusCls = eng.model_score == null ? 'home-engine-score-na' : (eng.model_fresh === false ? 'home-engine-score-stale' : '');

      html += `
        <div class="stock-card home-engine-card">
          <div class="home-engine-card-header">
            <span class="home-engine-card-name">${_esc(eng.name || eng.key)}${statusBadge}</span>
          </div>
          ${eLabel ? `<div class="home-engine-card-label">${_esc(eLabel)}</div>` : ''}
          <div class="home-engine-scores-row">
            <div class="home-engine-score-box">
              <span class="home-engine-score-tag">Engine</span>
              <span class="home-engine-score-pill ${eScoreCls}">${eScore}</span>
            </div>
            <div class="home-engine-score-box">
              <span class="home-engine-score-tag">Model</span>
              <span class="home-engine-score-pill ${mScoreCls} ${mStatusCls}">${mScore}</span>
              ${modelBadgeHtml}
            </div>
          </div>
          <div class="home-engine-summaries">
            <div class="home-engine-summary-section">
              <span class="home-engine-summary-tag">Engine</span>
              <div class="home-engine-summary-text stock-note home-engine-summary-clamp">${eSummary}</div>
            </div>
            <div class="home-engine-summary-section">
              <span class="home-engine-summary-tag">Model</span>
              <div class="home-engine-summary-text stock-note ${mSummary ? 'home-engine-summary-clamp' : 'home-engine-summary-na'}">${mSummary || (eng.model_score != null ? 'Score recorded — summary pending next model run.' : 'Not yet analyzed')}</div>
            </div>
          </div>
        </div>
      `;
    });
    html += '</div>';

    // Composite overview row
    const cState = _esc(composite.market_state || '—');
    const cSupport = _esc(composite.support_state || '—');
    const cStability = _esc(composite.stability_state || '—');
    const cConf = composite.confidence != null ? (composite.confidence * 100).toFixed(0) + '%' : '—';
    const cSummary = _cleanSummary(composite.summary);

    // Model interpretation pill: the workflow-level LLM interpretation is
    // separate from per-engine model scores.  Only show a warning when the
    // interpretation genuinely failed AND no per-engine model scores exist.
    const hasAnyModelScore = engines.some(function(e){ return e.model_score != null; });
    let modelPillHtml = '';
    if(modelStatus === 'failed' && !hasAnyModelScore){
      modelPillHtml = '<span class="qtPill home-composite-pill qtPill-warn">Interpretation: unavailable</span>';
    } else if(modelStatus === 'skipped'){
      modelPillHtml = '<span class="qtPill home-composite-pill">Interpretation: skipped</span>';
    }
    // When per-engine model scores exist, the interpretation failure is
    // inconsequential — per-engine models provide the real analysis.

    html += `
      <div class="home-scoreboard-composite">
        <div class="home-composite-pills">
          <span class="qtPill home-composite-pill">State: ${cState}</span>
          <span class="qtPill home-composite-pill">Support: ${cSupport}</span>
          <span class="qtPill home-composite-pill">Stability: ${cStability}</span>
          <span class="qtPill home-composite-pill">Confidence: ${cConf}</span>
          ${modelPillHtml}
        </div>
        ${cSummary ? `<div class="home-composite-summary stock-note">${cSummary}</div>` : ''}
        ${generatedAt ? `<div class="home-composite-timestamp stock-note">Generated: ${new Date(generatedAt).toLocaleString()}</div>` : ''}
      </div>
    `;

    scoreboardCardsEl.innerHTML = html;
  }

  /* ── Correction / drawdown state classification ── */
  function classifyDrawdownState(last, high52w){
    if(last === null || high52w === null || high52w <= 0) return null;
    const drawdownPct = ((last - high52w) / high52w) * 100;
    let label, tone;
    if(drawdownPct >= -4.9){
      label = 'Near High'; tone = 'bullish';
    } else if(drawdownPct >= -9.9){
      label = 'Pullback'; tone = 'neutral';
    } else if(drawdownPct >= -19.9){
      label = 'Correction'; tone = 'riskoff';
    } else {
      label = 'Bear Market'; tone = 'bearish';
    }
    return { drawdownPct, label, tone };
  }

  function renderIndexes(indexSummaries){
    indexTilesEl.innerHTML = INDEX_SYMBOLS.map((symbol) => {
      const payload = indexSummaries[symbol] || {};
      const price = payload?.price || {};
      const meta = INDEX_META[symbol] || { name: symbol, descriptor: symbol, index: symbol };
      const last = toNumber(price.last);
      const pct = toNumber(price.change_pct);
      const high52w = toNumber(price.high_52w);

      const drawdown = classifyDrawdownState(last, high52w);
      let drawdownHtml = '';
      if(drawdown){
        const ddPctStr = Math.abs(drawdown.drawdownPct).toFixed(1);
        drawdownHtml = `
          <div class="home-index-drawdown ${drawdown.tone}">
            <span class="home-index-dd-pct">${ddPctStr}% below 52wk high</span>
            <span class="home-index-dd-label">${drawdown.label}</span>
          </div>
        `;
      }

      return `
        <div class="statTile home-index-tile">
          <div class="home-index-header">
            <div class="statLabel home-index-name">${_esc(meta.name)}</div>
            <div class="home-index-etf stock-note">${symbol}</div>
          </div>
          <div class="statValue">${fmt(last)}</div>
          <div class="home-index-change ${pct !== null ? (pct >= 0 ? 'positive' : 'negative') : ''}">${fmtPct(pct)}</div>
          <div class="home-index-descriptor stock-note">${_esc(meta.descriptor)}</div>
          ${drawdownHtml}
        </div>
      `;
    }).join('');
  }

  function renderSectors(sectorSummaries, regimePayload){
    const rows = SECTOR_SYMBOLS.map((symbol) => {
      const pct = toNumber(sectorSummaries[symbol]?.price?.change_pct) ?? 0;
      const meta = SECTOR_META[symbol] || { name: symbol, description: symbol };
      return { symbol, pct, meta };
    });
    const maxAbs = Math.max(...rows.map((row) => Math.abs(row.pct)), 0.01);

    sectorBarsEl.innerHTML = rows.map((row) => {
      const width = Math.max(4, Math.round((Math.abs(row.pct) / maxAbs) * 100));
      const positive = row.pct >= 0;
      const label = `${row.symbol} — ${row.meta.name}`;
      const tooltip = `${row.symbol}: ${row.meta.description}`;
      return `
        <div class="home-sector-row">
          <div class="home-sector-label" title="${tooltip}">${label}</div>
          <div class="home-sector-track">
            <div class="home-sector-fill ${positive ? 'positive' : 'negative'}" style="width:${width}%;"></div>
          </div>
          <div class="home-sector-pct">${fmtPct(row.pct, 2)}</div>
        </div>
      `;
    }).join('');

    /* ── Sector context metadata chips ── */
    if(sectorContextEl){
      const chips = _buildSectorContextChips(rows, regimePayload);
      sectorContextEl.innerHTML = chips.length
        ? `<div class="home-sector-context-chips">${chips.map(c => `<span class="home-sector-ctx-chip ${c.tone}">${_esc(c.label)}</span>`).join('')}</div>`
        : '';
    }
  }

  /**
   * Derive concise sector influence/metadata chips for the expanded sector panel.
   */
  function _buildSectorContextChips(rows, regimePayload){
    const chips = [];
    const sorted = rows.slice().sort((a, b) => b.pct - a.pct);
    const positiveCount = rows.filter(r => r.pct > 0).length;

    // Breadth influence
    const breadthPct = Math.round((positiveCount / Math.max(rows.length, 1)) * 100);
    chips.push({
      label: `Breadth: ${breadthPct}% advancing`,
      tone: breadthPct >= 64 ? 'bullish' : (breadthPct <= 36 ? 'riskoff' : 'neutral'),
    });

    // Leadership concentration: top 2 sectors' share of total positive motion
    const totalAbsMove = rows.reduce((s, r) => s + Math.abs(r.pct), 0);
    if(totalAbsMove > 0 && sorted.length >= 2){
      const top2Share = (Math.abs(sorted[0].pct) + Math.abs(sorted[1].pct)) / totalAbsMove;
      if(top2Share > 0.45){
        chips.push({ label: `Concentrated: ${sorted[0].symbol} + ${sorted[1].symbol}`, tone: 'neutral' });
      } else {
        chips.push({ label: 'Leadership: Distributed', tone: 'bullish' });
      }
    }

    // Defensive vs Cyclical tilt
    const DEFENSIVE = ['XLP', 'XLU', 'XLV', 'XLRE'];
    const CYCLICAL = ['XLK', 'XLY', 'XLF', 'XLI', 'XLE', 'XLB', 'XLC'];
    const defAvg = DEFENSIVE.reduce((s, sym) => {
      const r = rows.find(x => x.symbol === sym);
      return s + (r ? r.pct : 0);
    }, 0) / DEFENSIVE.length;
    const cycAvg = CYCLICAL.reduce((s, sym) => {
      const r = rows.find(x => x.symbol === sym);
      return s + (r ? r.pct : 0);
    }, 0) / CYCLICAL.length;
    if(Math.abs(cycAvg - defAvg) > 0.15){
      const tilt = cycAvg > defAvg ? 'Cyclical' : 'Defensive';
      const tiltTone = cycAvg > defAvg ? 'bullish' : 'riskoff';
      chips.push({ label: `Tilt: ${tilt}`, tone: tiltTone });
    } else {
      chips.push({ label: 'Tilt: Balanced', tone: 'neutral' });
    }

    // Rates influence — from regime components if available
    const ratesScore = toNumber(regimePayload?.components?.rates?.score);
    if(ratesScore !== null){
      const ratesLabel = ratesScore >= 60 ? 'Supportive' : (ratesScore < 40 ? 'Pressuring' : 'Neutral');
      const ratesTone = ratesScore >= 60 ? 'bullish' : (ratesScore < 40 ? 'riskoff' : 'neutral');
      chips.push({ label: `Rates: ${ratesLabel}`, tone: ratesTone });
    }

    return chips;
  }

  function renderScannerOpportunities(ideas){
    if(!scannerOpportunitiesEl) return;
    latestOpportunities = Array.isArray(ideas) ? ideas.slice() : [];
    const tc = _card;              // BenTradeTradeCard building blocks
    const TOP = window.BenTradeScannerOrchestrator?.TOP_N || 9;

    /* ── Playbook-weighted re-sort (does NOT alter raw scanner scores) ── */
    const pbScorer = window.BenTradePlaybookScoring;
    let sortedIdeas = latestOpportunities;
    let pbNormalized = null;
    if(pbScorer && (_latestPlaybookPayload || _latestRegimePayload)){
      pbNormalized = pbScorer.normalizePlaybook(_latestPlaybookPayload, _latestRegimePayload);
      if(pbNormalized.primary.size > 0 || pbNormalized.avoid.size > 0){
        sortedIdeas = pbScorer.sortByPlaybook(latestOpportunities, pbNormalized);
      }
    }

    const top = sortedIdeas.slice(0, TOP).map((idea, idx) => {
      const normalized = normalizeOpportunity(idea, idea?.sourceType);
      logOpportunityInstrumentationOnce(normalized, idx);
      const key = opportunityKey(normalized, idx);
      /* Look up persisted model state from the shared store (keyed by tradeKey).
         We derive tradeKey the same way toScannerTrade does so the key is stable. */
      const rawTrade = idea.trade && typeof idea.trade === 'object' ? idea.trade : idea;
      const storeKey = String(rawTrade.trade_key || rawTrade.idea_key || '').trim();
      const storeEntry = storeKey && _modelStore ? _modelStore.get(storeKey) : null;
      if(storeEntry && storeEntry.status === 'success' && storeEntry.result){
        const r = storeEntry.result;
        normalized.model = {
          status: 'available',
          recommendation: String(r.recommendation || 'UNKNOWN').toUpperCase(),
          confidence: toNumber(r.confidence),
          summary: String(r.thesis || r.summary || '').trim(),
        };
      } else if(storeEntry && storeEntry.status === 'running'){
        normalized.model = { status: 'running', recommendation: 'RUNNING', confidence: null, summary: 'Running…' };
      }
      normalized._opKey = key;
      /* Carry playbook metadata (from sortByPlaybook's _pb annotation) for UI */
      if(idea._pb) normalized._pb = idea._pb;
      return normalized;
    });

    /* ── Empty state ── */
    if(!top.length){
      scannerOpportunitiesEl.innerHTML = `
        <div class="home-opp-empty">
          <div class="home-opp-empty-icon" aria-hidden="true">◇</div>
          <div class="home-opp-empty-text">No opportunities yet — run a scan to generate picks.</div>
          <button type="button" class="btn qtButton home-run-scan-btn" data-action="trigger-scan">Run Scan</button>
        </div>
      `;
      scannerOpportunitiesEl.querySelector('[data-action="trigger-scan"]')?.addEventListener('click', () => {
        runScanQueue().catch((err) => {
          setScanError(String(err?.message || err || 'Queue failed'));
          setScanStatus('');
        });
      });
      return;
    }

    /* ── Populated state — render scanner-style trade cards ── */
    /* Build scannerTrade objects (parallel to top[]) for mapper-based actions.
       Also enforce the tradeKey safety check here.
       
       ROOT CAUSE FIX: pbIndicator was appended as a sibling div AFTER the
       .trade-card div.  Both became separate CSS Grid children of
       .home-scanner-opportunities, doubling the grid item count and causing
       every other visual slot to be a tiny pb-indicator instead of a card
       (the "alternating missing cards" bug).  Fix: inject pbIndicator
       INSIDE the .trade-card wrapper by replacing its closing </div>. */
    _oeTradesForActions = [];
    _oeTopIdeas = [];
    const cardsHtml = [];
    const seenTradeKeys = new Set();

    top.forEach((idea, rawIdx) => {
      const scannerTrade = toScannerTrade(idea);
      const tradeKey = String(scannerTrade.trade_key || '').trim();

      /* Safety check: exclude cards with no tradeKey and log a warning */
      if(!tradeKey && idea.sourceType !== 'stock'){
        console.warn('[OE] Excluding opportunity without trade_key:', idea.symbol, idea.strategy);
        return;
      }

      /* Deduplicate by tradeKey — first occurrence wins (highest adjusted score) */
      const dedupeKey = tradeKey || `${idea.symbol}|${idea.strategy}|${rawIdx}`;
      if(seenTradeKeys.has(dedupeKey)){
        console.warn('[OE] Skipping duplicate trade_key:', dedupeKey);
        return;
      }
      seenTradeKeys.add(dedupeKey);

      const cardIdx = _oeTradesForActions.length;
      _oeTradesForActions.push(scannerTrade);
      _oeTopIdeas.push(idea);

      let cardHtml = tc.renderFullCard(scannerTrade, cardIdx, {
        strategyHint: String(idea.strategy || scannerTrade.strategy_id || '').toLowerCase(),
        rankOverride: _fmtLib.normalizeScore(idea.rank ?? idea.score) ?? null,
        modelStatus:  idea.model?.status === 'running' ? 'running' : null,
        expandState:  _oeExpandState,
      });

      /* Playbook lane indicator — injected INSIDE the .trade-card wrapper
         (before its closing </div>) so it stays a single CSS Grid child. */
      if(idea._pb && pbScorer){
        const pb = idea._pb;
        const summary = pbScorer.reasonSummary(pb);
        if(summary){
          const laneColors = {
            primary: 'rgba(0,220,120,0.85)',
            secondary: 'rgba(0,180,255,0.85)',
            avoid: 'rgba(255,90,90,0.85)',
            neutral: 'rgba(180,180,200,0.65)',
          };
          const color = laneColors[pb.lane] || laneColors.neutral;
          const laneLabel = (pb.lane || 'neutral').charAt(0).toUpperCase() + (pb.lane || 'neutral').slice(1);
          const dot = `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${color};margin-right:5px;vertical-align:middle;"></span>`;
          const pbIndicator = `<div class="pb-lane-indicator" style="font-size:10px;color:${color};padding:2px 10px 4px;line-height:1.3;" title="${_fmtLib.escapeHtml(summary)}">${dot}${_fmtLib.escapeHtml(laneLabel)}${pb.multiplier !== 1 ? ' \u00B7 Adj ' + pb.adjustedScore.toFixed(1) + '%' : ''}</div>`;
          /* Insert before the final </div> of .trade-card */
          cardHtml = cardHtml.replace(/<\/div>\s*$/, pbIndicator + '</div>');
        }
      }
      cardsHtml.push(cardHtml);
    });

    if(!cardsHtml.length){
      scannerOpportunitiesEl.innerHTML = `
        <div class="home-opp-empty">
          <div class="home-opp-empty-icon" aria-hidden="true">◇</div>
          <div class="home-opp-empty-text">No valid opportunities (all missing trade keys).</div>
        </div>`;
      return;
    }

    scannerOpportunitiesEl.innerHTML = `
      <div class="home-opp-count stock-note">${cardsHtml.length} Pick${cardsHtml.length !== 1 ? 's' : ''}</div>
      ${cardsHtml.join('')}
    `;

    /* ── Re-hydrate persisted model analysis results into freshly-created cards ── */
    if(_modelStore && typeof _modelStore.hydrateContainer === 'function'){
      _modelStore.hydrateContainer(scannerOpportunitiesEl);
    }

    /* ── Action wiring — mirrors strategy_dashboard_shell.js exactly ── */

    /* Collapse/expand persistence via <details> toggle */
    scannerOpportunitiesEl.querySelectorAll('details.trade-card-collapse').forEach((details) => {
      details.addEventListener('toggle', () => {
        const tk = details.dataset.tradeKey || details.closest('.trade-card')?.dataset?.tradeKey;
        if(tk) _oeExpandState[tk] = details.open;
      });
    });

    /* Copy trade key buttons */
    scannerOpportunitiesEl.querySelectorAll('[data-copy-trade-key]').forEach((copyBtn) => {
      copyBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if(_card?.copyTradeKey) _card.copyTradeKey(copyBtn.dataset.copyTradeKey, copyBtn);
      });
    });

    /* Action buttons — use mapper model + buildTradeActionPayload (identical to scanner shell) */
    scannerOpportunitiesEl.querySelectorAll('button[data-action]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();

        const action = String(btn.getAttribute('data-action') || '');
        const cardEl = btn.closest('.trade-card');
        const cardIdx = cardEl ? parseInt(cardEl.dataset.idx, 10) : -1;
        const trade = _oeTradesForActions[cardIdx];
        const idea = _oeTopIdeas[cardIdx];
        if(!trade || !idea) return;

        /* Map through canonical mapper — identical to scanner shell */
        const strategyHint = String(idea.strategy || trade.strategy_id || '').toLowerCase();
        const model = _mapper ? _mapper.map(trade, strategyHint) : null;
        const payload = (_mapper && model) ? _mapper.buildTradeActionPayload(model) : {};

        if(action === 'execute'){
          if(window.BenTradeExecutionModal && window.BenTradeExecutionModal.open){
            window.BenTradeExecutionModal.open(trade, payload);
          } else if(typeof window.executeTrade === 'function'){
            window.executeTrade(trade);
          }
          return;
        }

        if(action === 'reject'){
          const body = {
            trade_key: payload.tradeKey || '',
            symbol: payload.symbol || '',
            strategy: payload.strategyId || '',
            action: 'reject',
          };
          fetch('/api/decisions/reject', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          }).then(res => {
            if(res.ok){
              if(cardEl) cardEl.classList.add('manually-rejected');
              btn.disabled = true;
              btn.textContent = 'Rejected';
            }
          }).catch(() => {});
          return;
        }

        if(action === 'model-analysis'){
          /* Run model analysis inline on this card — uses shared store for persistence */
          console.info('[MODEL_TRACE:home] button clicked', { cardIdx, symbol: idea?.symbol, strategy: idea?.strategy });
          const modelBtn = btn;
          const modelOutputEl = cardEl?.querySelector('[data-model-output]');
          const tradeKey = String(trade.trade_key || '').trim();

          /* Mark running in store → triggers immediate UI update via hydration */
          if(tradeKey && _modelStore) _modelStore.setRunning(tradeKey);
          modelBtn.disabled = true;
          modelBtn.textContent = 'Running\u2026';
          if(modelOutputEl && _modelUI){
            modelOutputEl.style.display = 'block';
            modelOutputEl.innerHTML = _modelUI.render({ status: 'running' });
          }

          runModelForOpportunity(idea, (modelResult) => {
            console.info('[MODEL_TRACE:home] callback received', { status: modelResult?.status, recommendation: modelResult?.recommendation });

            /* Persist result in shared store (keyed by tradeKey) */
            if(tradeKey && _modelStore && _modelUI){
              if(modelResult && modelResult.status !== 'error' && modelResult.status !== 'not_run'){
                _modelStore.setSuccess(tradeKey, _modelUI.parse(modelResult));
              } else if(modelResult && modelResult.status === 'error'){
                _modelStore.setError(tradeKey, modelResult.summary || 'Model analysis failed');
              }
            }

            /* Update button — show re-run timestamp */
            modelBtn.disabled = false;
            const ts = new Date();
            const hhmm = String(ts.getHours()).padStart(2,'0') + ':' + String(ts.getMinutes()).padStart(2,'0');
            modelBtn.textContent = '\u21BB Re-run Analysis ' + hhmm;

            /* Render result in card */
            if(modelOutputEl){
              if(!modelResult || modelResult.status === 'not_run'){
                modelOutputEl.style.display = 'none';
              } else {
                modelOutputEl.style.display = 'block';
                modelOutputEl.innerHTML = _renderTradeModelOutput(modelResult);
              }
            }
          }, 'home_card_action');
          return;
        }

        if(action === 'workbench'){
          if(payload.tradeKey){
            window.location.hash = '#/admin/data-workbench?trade_key=' + encodeURIComponent(payload.tradeKey);
          } else if(tc.openDataWorkbenchByTrade){
            tc.openDataWorkbenchByTrade(trade);
          }
          return;
        }

        if(action === 'data-workbench'){
          if(tc.openDataWorkbenchByTrade){
            tc.openDataWorkbenchByTrade(trade);
          } else if(payload.tradeKey){
            window.location.hash = '#/admin/data-workbench?trade_key=' + encodeURIComponent(payload.tradeKey);
          }
          return;
        }
      });
    });
  }

  /* renderStrategyBoard — REMOVED: Strategy Leaderboard is global-only (index.html / sessionStats.js) */

  /* renderSignalHub — REMOVED: Signal Hub replaced by expanded Sector Performance panel */

  function renderRisk(snapshot, activeTradesPayload){
    const portfolio = snapshot?.portfolio || {};
    const risk = toNumber(portfolio?.risk);

    let capitalAtRisk = risk;
    let utilization = null;

    const activeTrades = Array.isArray(activeTradesPayload?.active_trades) ? activeTradesPayload.active_trades : [];
    if(capitalAtRisk === null){
      capitalAtRisk = activeTrades.reduce((sum, row) => {
        const comp = (row?.computed && typeof row.computed === 'object') ? row.computed : {};
        const candidate = toNumber(comp?.max_loss ?? row?.max_loss);
        return sum + (candidate || 0);
      }, 0);
    }
    if(capitalAtRisk !== null){
      const denom = 100000;
      utilization = denom > 0 ? capitalAtRisk / denom : null;
    }

    riskTilesEl.innerHTML = `
      <div class="statTile"><div class="statLabel" data-metric="delta">Net Delta</div><div class="statValue">${fmt(portfolio?.delta, 3)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="theta">Net Theta</div><div class="statValue">${fmt(portfolio?.theta, 3)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="vega">Net Vega</div><div class="statValue">${fmt(portfolio?.vega, 3)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="capital_at_risk">Capital at Risk</div><div class="statValue">$${fmt(capitalAtRisk, 0)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="risk_utilization">Risk Utilization</div><div class="statValue">${utilization === null ? '0.00%' : `${(utilization * 100).toFixed(2)}%`}</div></div>
    `;
  }

  /* ── Active Trades per-strategy bubble counts ── */
  function renderActiveTradesCount(activeTradesPayload){
    if(!activeTradesCountEl) return;
    const trades = Array.isArray(activeTradesPayload?.active_trades) ? activeTradesPayload.active_trades : [];
    const buckets = {
      credit_put: 0, credit_call: 0, debit_spreads: 0, iron_condor: 0,
      butterflies: 0, calendar: 0, income: 0, stock_scanner: 0,
    };
    trades.forEach(t => {
      const sid = (t?.strategy_id || t?.strategy || '').toLowerCase().replace(/[\s-]/g, '_');
      if(sid in buckets) buckets[sid]++;
      else if(sid.includes('put')) buckets.credit_put++;
      else if(sid.includes('call') && !sid.includes('iron')) buckets.credit_call++;
    });
    const labels = {
      credit_put: 'Credit Put', credit_call: 'Credit Call', debit_spreads: 'Debit Spreads',
      iron_condor: 'Iron Condor', butterflies: 'Butterflies', calendar: 'Calendar',
      income: 'Income', stock_scanner: 'Stocks',
    };
    const total = trades.length;
    activeTradesCountEl.innerHTML = `<div class="statTile"><div class="statLabel" data-metric="total_active_trades">Total</div><div class="statValue">${total}</div></div>`
      + Object.keys(buckets).map(k =>
        `<div class="statTile"><div class="statLabel" data-metric="strategy_bucket">${labels[k]}</div><div class="statValue">${buckets[k]}</div></div>`
      ).join('');
  }

  /* renderSessionStats — REMOVED: Session Stats is global-only (index.html / sessionStats.js) */

  /* ── Equity Curve ── */
  function renderEquityCurve(activeTradesPayload){
    if(!equityCurveEl) return;
    // Build a best-effort equity series from active trades sorted by open date
    const trades = Array.isArray(activeTradesPayload?.active_trades) ? activeTradesPayload.active_trades : [];
    let equitySeries = [];
    if(trades.length >= 2){
      // Sort by opened_at / created_at ascending, accumulate P&L
      const sorted = trades
        .map(t => {
          const comp = (t?.computed && typeof t.computed === 'object') ? t.computed : {};
          const pnl = toNumber(comp?.unrealized_pnl ?? comp?.pnl ?? t?.pnl) || 0;
          const dateStr = t?.opened_at || t?.created_at || '';
          return { date: dateStr, pnl };
        })
        .filter(r => r.date)
        .sort((a, b) => a.date.localeCompare(b.date));
      if(sorted.length >= 2){
        let cumulative = 0;
        equitySeries = sorted.map(r => {
          cumulative += r.pnl;
          return { close: cumulative };
        });
      }
    }
    if(equitySeries.length >= 2){
      if(equityCurveEmptyEl) equityCurveEmptyEl.style.display = 'none';
      equityCurveEl.style.display = '';
      renderChart(equityCurveEl, equitySeries, { stroke: 'rgba(126,247,184,0.92)' });
    } else {
      // Show empty state
      if(equityCurveEmptyEl) equityCurveEmptyEl.style.display = '';
      equityCurveEl.style.display = 'none';
    }
  }

  function _freshnessTag(freshness, key){
    if(!freshness || !freshness[key]) return '';
    var mc = window.BenTradeMarketContext;
    if(mc){
      var norm = mc.normalizeFromFlatMacro({ [key]: null, _freshness: freshness });
      if(norm && norm[key]) return mc.freshnessTag(norm[key]);
    }
    // Fallback to inline rendering
    const f = freshness[key];
    if(f.is_intraday) return '<span class="home-freshness-tag home-freshness-live" title="Intraday">live</span>';
    const obsDate = f.observation_date || '';
    const title = obsDate ? `EOD close (${obsDate})` : 'End-of-day close';
    return `<span class="home-freshness-tag home-freshness-eod" title="${title}">eod</span>`;
  }

  function renderMacro(macro, spySummary){
    const vix = toNumber(macro?.vix ?? spySummary?.options_context?.vix);
    const freshness = macro?._freshness || {};
    macroTilesEl.innerHTML = `
      <div class="statTile"><div class="statLabel" data-metric="ten_year_yield">10Y Yield ${_freshnessTag(freshness, 'ten_year_yield')}</div><div class="statValue">${fmt(macro?.ten_year_yield, 2)}%</div></div>
      <div class="statTile"><div class="statLabel" data-metric="fed_funds">Fed Funds ${_freshnessTag(freshness, 'fed_funds_rate')}</div><div class="statValue">${fmt(macro?.fed_funds_rate, 2)}%</div></div>
      <div class="statTile"><div class="statLabel" data-metric="cpi_yoy">CPI YoY ${_freshnessTag(freshness, 'cpi_yoy')}</div><div class="statValue">${fmt(macro?.cpi_yoy, 2)}%</div></div>
      <div class="statTile"><div class="statLabel" data-metric="vix_level">VIX ${_freshnessTag(freshness, 'vix')}</div><div class="statValue">${fmt(vix, 2)}</div></div>
    `;
    // Update shared market context store
    var mc = window.BenTradeMarketContext;
    if(mc){
      var norm = mc.normalizeFromFlatMacro(macro);
      if(norm) mc.setContext(norm);
    }
  }

  /* ── Playbook shaping: extract component score ── */
  function _compScore(components, key){
    const v = toNumber((components || {})[key]?.score);
    return v !== null ? Math.max(0, Math.min(100, v)) : 50;
  }

  /* ── Stock Strategy Playbook — equity tactical posture ── */
  function _shapeStockPlaybook(regimeState, regimeScore, components, vix){
    const trend = _compScore(components, 'trend');
    const breadth = _compScore(components, 'breadth');
    const momentum = _compScore(components, 'momentum');
    const vol = _compScore(components, 'volatility');
    const vixVal = toNumber(vix);

    let posture, summary;
    const strategies = [];
    let caution = null;

    if(regimeState === 'RISK_ON'){
      posture = regimeScore >= 75 ? 'aggressive' : 'constructive';
      summary = regimeScore >= 75
        ? 'Broad risk-on environment supports active equity positioning.'
        : 'Constructive tape favors selective long exposure with trend.';
      strategies.push({ name: 'Trend Continuation', conviction: trend >= 60 && momentum >= 55 ? 'high' : 'moderate', reason: 'Trend and momentum factors support sustained equity moves.' });
      if(breadth >= 60) strategies.push({ name: 'Broad Participation', conviction: breadth >= 70 ? 'high' : 'moderate', reason: 'Wide breadth supports broader selection beyond leaders.' });
      if(momentum >= 55) strategies.push({ name: 'Breakout Entries', conviction: momentum >= 70 ? 'high' : 'moderate', reason: 'Favorable momentum supports breakout continuation setups.' });
      strategies.push({ name: 'Dip Buying on Pullbacks', conviction: (vixVal !== null ? vixVal < 20 : vol > 55) ? 'moderate' : 'low', reason: 'Contained volatility supports reentry on dips to structure.' });
      if(momentum >= 75) caution = 'Avoid chasing extended names — look for consolidation entries.';
    } else if(regimeState === 'RISK_OFF'){
      posture = 'defensive';
      summary = 'Market structure is under pressure — prioritize capital preservation.';
      strategies.push({ name: 'Reduce Exposure / Raise Cash', conviction: 'high', reason: 'Risk-off conditions favor smaller, selective positioning.' });
      strategies.push({ name: 'Defensive Quality Names Only', conviction: trend < 40 ? 'high' : 'moderate', reason: 'Focus on low-beta, high-quality equities if adding exposure.' });
      if(momentum < 40) strategies.push({ name: 'Wait for Momentum Recovery', conviction: 'high', reason: 'Weak momentum signals suggest patience, not urgency.' });
      strategies.push({ name: 'Mean Reversion (Selective)', conviction: 'low', reason: 'Only with clear confirmation in oversold, high-quality names.' });
      caution = 'Avoid bottom-fishing in broad weakness — wait for breadth confirmation.';
    } else {
      posture = 'selective';
      summary = regimeScore >= 55
        ? 'Mixed-to-constructive conditions — be selective, favor quality setups.'
        : 'Range-bound environment limits directional confidence — reduce aggression.';
      strategies.push({ name: 'Selective Pullback Entries', conviction: trend >= 50 ? 'moderate' : 'low', reason: 'Favor entries at support with confirmation, not aggressive chasing.' });
      strategies.push({ name: 'Quality Over Quantity', conviction: 'moderate', reason: 'Prioritize strong relative-strength leaders over broad positioning.' });
      if(breadth >= 50) strategies.push({ name: 'Sector Rotation Themes', conviction: 'moderate', reason: 'Adequate breadth supports tactical sector-based entries.' });
      if(momentum < 45) strategies.push({ name: 'Reduce Position Sizing', conviction: 'moderate', reason: 'Low momentum reduces conviction — smaller bets until clarity.' });
      strategies.push({ name: 'Range / Mean Reversion', conviction: (vixVal !== null ? vixVal < 20 : vol > 55) ? 'moderate' : 'low', reason: 'Range-bound conditions suit mean-reversion setups at extremes.' });
      if(regimeScore < 45) caution = 'Avoid aggressive directional bets in low-conviction environment.';
    }
    return { posture, summary, strategies: strategies.slice(0, 5), caution };
  }

  /* ── Options Strategy Playbook — structure selection ── */
  function _shapeOptionsPlaybook(regimeState, regimeScore, components, vix, scoreboard){
    const vol = _compScore(components, 'volatility');
    const trend = _compScore(components, 'trend');
    const breadth = _compScore(components, 'breadth');
    const vixVal = toNumber(vix);
    const volHigh = vixVal !== null ? vixVal > 22 : vol < 40;
    const volLow  = vixVal !== null ? vixVal < 16 : vol > 65;
    const composite = (scoreboard && typeof scoreboard === 'object') ? (scoreboard.composite || {}) : {};
    const stability = composite.stability_state || '';

    let posture, summary;
    const strategies = [];
    let caution = null;

    if(regimeState === 'RISK_ON'){
      posture = regimeScore >= 75 ? 'aggressive' : 'constructive';
      summary = volHigh
        ? 'Risk-on with elevated vol — prime conditions for premium capture.'
        : 'Constructive tape supports both premium selling and directional structures.';
      strategies.push({ name: 'Put Credit Spreads', conviction: volHigh ? 'high' : 'moderate', reason: volHigh ? 'Elevated premium with bullish bias creates ideal credit conditions.' : 'Premium capture in supportive trend environment.' });
      strategies.push({ name: 'Covered Calls / Income', conviction: 'moderate', reason: 'Income overlay appropriate with risk-on drift and time decay.' });
      if(trend >= 60 && !volHigh) strategies.push({ name: 'Call Debit Spreads', conviction: 'moderate', reason: 'Strong trend supports directional upside with defined risk.' });
      if(stability === 'orderly' || !volHigh) strategies.push({ name: 'Iron Condors (Wide Wings)', conviction: 'moderate', reason: 'Orderly conditions support range-bound premium harvesting.' });
      caution = 'Avoid uncapped short calls — risk-on tapes can extend sharply.';
    } else if(regimeState === 'RISK_OFF'){
      posture = 'defensive';
      summary = volHigh
        ? 'Risk-off with elevated vol — protective structures and selective premium only.'
        : 'Risk-off bias — favor protective structures over premium selling.';
      strategies.push({ name: 'Put Debit Spreads / Protection', conviction: 'high', reason: 'Defined-risk bearish structures preferred in risk-off conditions.' });
      if(volHigh) strategies.push({ name: 'Cash-Secured Puts (Far OTM)', conviction: 'moderate', reason: 'Premium is rich — sell far OTM only with strict sizing.' });
      strategies.push({ name: 'Calendar Spreads', conviction: 'moderate', reason: 'Term-structure expression with reduced directional dependency.' });
      strategies.push({ name: 'Portfolio Hedges', conviction: 'high', reason: 'Protective structures reduce portfolio tail risk in stress.' });
      caution = 'Avoid selling premium near spot — gap and assignment risk elevated.';
    } else {
      posture = 'selective';
      summary = volHigh
        ? 'Neutral with elevated vol — favor defined-risk neutral structures.'
        : (volLow ? 'Neutral in low vol — debit structures and calendars gain edge.' : 'Mixed conditions favor balanced, risk-defined options structures.');
      strategies.push({ name: 'Iron Condors', conviction: !volLow ? 'high' : 'moderate', reason: 'Range-bound conditions favor neutral premium harvesting.' });
      strategies.push({ name: 'Credit Spreads (Wider Strikes)', conviction: 'moderate', reason: 'Wider risk bands maintain cushion in mixed tape.' });
      if(volLow) strategies.push({ name: 'Debit Spreads (Selective)', conviction: 'moderate', reason: 'Low vol makes debit structures more cost-efficient.' });
      strategies.push({ name: 'Calendars / Time Spreads', conviction: 'moderate', reason: 'Term-structure opportunities remain in range conditions.' });
      strategies.push({ name: 'Butterflies', conviction: breadth < 50 ? 'moderate' : 'low', reason: 'Defined-risk mean reversion for range-bound underlyings.' });
      if(regimeScore < 45) caution = 'Avoid aggressive directional debit spreads in low-conviction tape.';
    }
    return { posture, summary, strategies: strategies.slice(0, 5), caution };
  }

  /* ── Render a playbook panel — cyan/red color-coded format ── */
  function _renderPlaybookPanel(el, shaped){
    if(!el) return;
    if(!shaped){
      el.innerHTML = '<div class="home-pb-fallback">Playbook unavailable — insufficient market context.</div>';
      return;
    }
    const postureTone = shaped.posture === 'aggressive' || shaped.posture === 'constructive'
      ? 'bullish' : (shaped.posture === 'defensive' ? 'riskoff' : 'neutral');

    let html = `<div class="home-pb-header"><span class="home-pb-posture ${postureTone}">${shaped.posture}</span><span class="home-pb-summary stock-note">${shaped.summary}</span></div>`;

    // Split strategies: favor (high/moderate) vs avoid (low + caution)
    const favor = shaped.strategies.filter(function(s){ return s.conviction === 'high' || s.conviction === 'moderate'; });
    const avoid = shaped.strategies.filter(function(s){ return s.conviction === 'low'; });

    if(favor.length){
      html += `<div class="home-pb-lane home-pb-lane--favor"><div class="home-pb-lane-label">FAVOR</div><div class="home-pb-lane-items">`;
      favor.forEach(function(s){
        const isHigh = s.conviction === 'high';
        html += `<div class="home-pb-pill home-pb-pill--favor${isHigh ? ' home-pb-pill--strong' : ''}"><span class="home-pb-pill-name">${s.name}</span><span class="home-pb-pill-reason">${s.reason}</span></div>`;
      });
      html += '</div></div>';
    }

    if(avoid.length || shaped.caution){
      html += `<div class="home-pb-lane home-pb-lane--avoid"><div class="home-pb-lane-label">AVOID / REDUCE</div><div class="home-pb-lane-items">`;
      avoid.forEach(function(s){
        html += `<div class="home-pb-pill home-pb-pill--avoid"><span class="home-pb-pill-name">${s.name}</span><span class="home-pb-pill-reason">${s.reason}</span></div>`;
      });
      if(shaped.caution){
        html += `<div class="home-pb-pill home-pb-pill--avoid"><span class="home-pb-pill-name">⚠ Caution</span><span class="home-pb-pill-reason">${shaped.caution}</span></div>`;
      }
      html += '</div></div>';
    }

    el.innerHTML = html;
  }

  function emptySummary(symbol){
    return {
      symbol,
      price: { last: 0, change_pct: 0 },
      indicators: { rsi14: 0, ema20: 0 },
      history: [],
      options_context: { vix: 0 },
      source_health: {},
    };
  }

  function updateLastUpdated(iso){
    const parsed = iso ? new Date(iso) : null;
    const text = parsed && !Number.isNaN(parsed.getTime()) ? parsed.toLocaleTimeString() : '--';
    lastUpdatedEl.textContent = `Last updated: ${text}`;
  }

  /* ═══════════════════════════════════════════════════════════════
   * Market Picture History — 2-week engine-line chart
   * ═══════════════════════════════════════════════════════════════ */

  /**
   * ENGINE_HISTORY_SERIES — stable engine keys, display labels and line colors.
   * Keys MUST match ENGINE_DISPLAY in market_picture_contract.py (canonical source).
   */
  const ENGINE_HISTORY_SERIES = [
    { key: 'breadth_participation',   label: 'Breadth & Participation',      color: 'rgba(0,234,255,0.9)'   },
    { key: 'volatility_options',      label: 'Volatility & Options',         color: 'rgba(255,199,88,0.9)'  },
    { key: 'cross_asset_macro',       label: 'Cross-Asset Macro',            color: 'rgba(126,247,184,0.9)' },
    { key: 'flows_positioning',       label: 'Flows & Positioning',          color: 'rgba(255,79,102,0.9)'  },
    { key: 'liquidity_financial_conditions', label: 'Liquidity & Financial Conds', color: 'rgba(181,126,255,0.9)' },
    { key: 'news_sentiment',          label: 'News & Sentiment',             color: 'rgba(255,156,68,0.9)'  },
  ];

  /**
   * _shapeHistoryEngineSeries — transforms raw history snapshots into
   * per-engine time-series arrays suitable for charting.
   *
   * Plotted-score rule (documented per requirement):
   *   plotted_score = average(engine_score, model_score)  when BOTH are numbers
   *   plotted_score = engine_score                        when model_score is null/missing
   *   plotted_score = null                                when engine_score is also null
   *
   * @param {Array<Object>} entries — raw history snapshots from /api/market-picture/history
   * @param {number} [daysBack=14] — how many days of history to include
   * @returns {Object} { series: [{key, label, color, points: [{ts, plotted_score, engine_score, model_score, had_model}]}], regimeBands: [{tStart, tEnd, regime}], postureMarkers: [{ts, stock, options}], tooFew: boolean }
   */
  function _shapeHistoryEngineSeries(entries, daysBack){
    daysBack = daysBack || 14;
    var now = Date.now();
    var cutoff = now - daysBack * 86400000;

    // Filter to last N days and sort ascending by captured_at
    var filtered = [];
    for(var i = 0; i < entries.length; i++){
      var e = entries[i];
      var ts = e.captured_at ? new Date(e.captured_at).getTime() : 0;
      if(ts >= cutoff && ts <= now) filtered.push(e);
    }
    filtered.sort(function(a, b){
      return new Date(a.captured_at).getTime() - new Date(b.captured_at).getTime();
    });

    // Build per-engine series
    var series = [];
    for(var s = 0; s < ENGINE_HISTORY_SERIES.length; s++){
      var def = ENGINE_HISTORY_SERIES[s];
      var points = [];
      for(var j = 0; j < filtered.length; j++){
        var snap = filtered[j];
        var engines = snap.engines || [];
        var eng = null;
        for(var k = 0; k < engines.length; k++){
          if(engines[k].key === def.key){ eng = engines[k]; break; }
        }
        var eScore = eng ? (typeof eng.engine_score === 'number' ? eng.engine_score : null) : null;
        var mScore = eng ? (typeof eng.model_score === 'number' ? eng.model_score : null) : null;

        // Plotted-score rule:
        // avg(engine, model) if both present; engine_score alone if model missing; null if both null
        var plotted;
        if(eScore !== null && mScore !== null){
          plotted = (eScore + mScore) / 2;
        } else if(eScore !== null){
          plotted = eScore;
        } else {
          plotted = null;
        }

        points.push({
          ts: new Date(snap.captured_at).getTime(),
          plotted_score: plotted,
          engine_score: eScore,
          model_score: mScore,
          had_model: mScore !== null,
        });
      }
      series.push({
        key: def.key,
        label: def.label,
        color: def.color,
        points: points,
      });
    }

    // tooFew: need at least 2 data points to draw any line
    var hasEnoughPoints = filtered.length >= 2;

    // ── Regime bands: contiguous time spans sharing the same regime label ──
    var regimeBands = [];
    var curBand = null;
    for(var ri = 0; ri < filtered.length; ri++){
      var rSnap = filtered[ri];
      var rTs = new Date(rSnap.captured_at).getTime();
      var rLabel = String(rSnap.consumer_regime_label || rSnap.regime_state || 'NEUTRAL').toUpperCase();
      // Normalize to canonical three: RISK_ON, RISK_OFF, NEUTRAL
      if(rLabel !== 'RISK_ON' && rLabel !== 'RISK_OFF') rLabel = 'NEUTRAL';

      if(!curBand || curBand.regime !== rLabel){
        if(curBand) curBand.tEnd = rTs;
        curBand = { tStart: rTs, tEnd: rTs, regime: rLabel };
        regimeBands.push(curBand);
      } else {
        curBand.tEnd = rTs;
      }
    }

    // ── Posture change markers: derive posture from regime label + score ──
    // Posture derivation mirrors _shapeStockPlaybook / _shapeOptionsPlaybook logic:
    //   RISK_ON  + score >= 75 → aggressive, else constructive
    //   RISK_OFF → defensive
    //   NEUTRAL  → selective
    // Options posture follows same mapping.
    function _derivePosture(regimeLabel, regimeScore){
      if(regimeLabel === 'RISK_ON') return regimeScore >= 75 ? 'aggressive' : 'constructive';
      if(regimeLabel === 'RISK_OFF') return 'defensive';
      return 'selective';
    }
    var postureMarkers = [];
    var prevStockPosture = null;
    var prevOptionsPosture = null;
    for(var pi2 = 0; pi2 < filtered.length; pi2++){
      var pSnap = filtered[pi2];
      var pTs = new Date(pSnap.captured_at).getTime();
      var pLabel = String(pSnap.consumer_regime_label || pSnap.regime_state || 'NEUTRAL').toUpperCase();
      if(pLabel !== 'RISK_ON' && pLabel !== 'RISK_OFF') pLabel = 'NEUTRAL';
      var pScore = typeof pSnap.consumer_regime_score === 'number' ? pSnap.consumer_regime_score : 50;
      var stockP = _derivePosture(pLabel, pScore);
      var optionsP = _derivePosture(pLabel, pScore);
      if(stockP !== prevStockPosture || optionsP !== prevOptionsPosture){
        postureMarkers.push({ ts: pTs, stock: stockP, options: optionsP });
        prevStockPosture = stockP;
        prevOptionsPosture = optionsP;
      }
    }

    return { series: series, regimeBands: regimeBands, postureMarkers: postureMarkers, tooFew: !hasEnoughPoints };
  }

  /**
   * renderMarketPictureHistory — draw the 6-line engine chart via SVG.
   *
   * @param {Object} shaped — output of _shapeHistoryEngineSeries
   */
  function renderMarketPictureHistory(shaped){
    if(!mpHistorySvgEl || !mpHistoryEmptyEl || !mpHistoryChartEl || !mpHistoryLegendEl) return;

    if(!shaped || shaped.tooFew){
      mpHistoryEmptyEl.style.display = '';
      mpHistoryChartEl.style.display = 'none';
      return;
    }

    mpHistoryEmptyEl.style.display = 'none';
    mpHistoryChartEl.style.display = '';

    var series = shaped.series;
    var width = 900;
    var height = 300;
    var margin = { top: 14, right: 14, bottom: 38, left: 50 };
    var plotW = width - margin.left - margin.right;
    var plotH = height - margin.top - margin.bottom;

    // Determine global time range and score range (0–100 fixed for consistency)
    var tMin = Infinity, tMax = -Infinity;
    for(var s = 0; s < series.length; s++){
      var pts = series[s].points;
      for(var p = 0; p < pts.length; p++){
        if(pts[p].ts < tMin) tMin = pts[p].ts;
        if(pts[p].ts > tMax) tMax = pts[p].ts;
      }
    }
    if(tMax <= tMin) tMax = tMin + 1;
    var scoreMin = 0, scoreMax = 100;
    var scoreSpan = scoreMax - scoreMin;

    var xFor = function(ts){ return margin.left + ((ts - tMin) / (tMax - tMin)) * plotW; };
    var yFor = function(val){ return margin.top + (1 - ((val - scoreMin) / scoreSpan)) * plotH; };

    // Y-axis gridlines and labels (0, 25, 50, 75, 100)
    var yTicks = [0, 25, 50, 75, 100];
    var yGrid = '';
    var yLabels = '';
    for(var t = 0; t < yTicks.length; t++){
      var yy = yFor(yTicks[t]);
      yGrid += '<line x1="' + margin.left + '" y1="' + yy.toFixed(1) + '" x2="' + (width - margin.right) + '" y2="' + yy.toFixed(1) + '" stroke="rgba(0,234,255,0.10)" stroke-width="1" shape-rendering="crispEdges"></line>';
      yLabels += '<text x="' + (margin.left - 8) + '" y="' + (yy + 3).toFixed(1) + '" text-anchor="end" fill="rgba(215,251,255,0.7)" font-size="10" font-family="var(--font-body)">' + yTicks[t] + '</text>';
    }

    // X-axis date labels (daily ticks, skip to every 2 days if > 10)
    var xLabels = '';
    var xGrid = '';
    var dayMs = 86400000;
    var dayStart = new Date(tMin);
    dayStart.setHours(0,0,0,0);
    var dayStep = ((tMax - tMin) / dayMs) > 10 ? 2 : 1;
    var monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var cursor = new Date(dayStart.getTime() + dayMs);
    while(cursor.getTime() <= tMax){
      var dayNum = Math.round((cursor.getTime() - dayStart.getTime()) / dayMs);
      if(dayNum % dayStep === 0){
        var px = xFor(cursor.getTime());
        if(px >= margin.left && px <= width - margin.right){
          var yBottom = height - margin.bottom;
          xGrid += '<line x1="' + px.toFixed(1) + '" y1="' + margin.top + '" x2="' + px.toFixed(1) + '" y2="' + yBottom.toFixed(1) + '" stroke="rgba(0,234,255,0.06)" stroke-width="1" shape-rendering="crispEdges"></line>';
          xLabels += '<text x="' + px.toFixed(1) + '" y="' + (yBottom + 14).toFixed(1) + '" text-anchor="middle" fill="rgba(215,251,255,0.55)" font-size="9" font-family="var(--font-body)">' + monthNames[cursor.getMonth()] + ' ' + cursor.getDate() + '</text>';
        }
      }
      cursor = new Date(cursor.getTime() + dayMs);
    }

    // Draw paths for each engine series
    var paths = '';
    for(var si = 0; si < series.length; si++){
      var sr = series[si];
      var d = '';
      var started = false;
      for(var pi = 0; pi < sr.points.length; pi++){
        var pt = sr.points[pi];
        if(pt.plotted_score === null) continue;
        var xx = xFor(pt.ts);
        var yy2 = yFor(pt.plotted_score);
        d += (started ? ' L ' : 'M ') + xx.toFixed(2) + ' ' + yy2.toFixed(2);
        started = true;
      }
      if(d){
        paths += '<path d="' + d + '" fill="none" stroke="' + sr.color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" opacity="0.9"></path>';
      }
    }

    // Axes
    var axes = '<line x1="' + margin.left + '" y1="' + margin.top + '" x2="' + margin.left + '" y2="' + (height - margin.bottom) + '" stroke="rgba(0,234,255,0.35)" stroke-width="1" shape-rendering="crispEdges"></line>'
             + '<line x1="' + margin.left + '" y1="' + (height - margin.bottom) + '" x2="' + (width - margin.right) + '" y2="' + (height - margin.bottom) + '" stroke="rgba(0,234,255,0.35)" stroke-width="1" shape-rendering="crispEdges"></line>';

    mpHistorySvgEl.setAttribute('viewBox', '0 0 ' + width + ' ' + height);

    // ── Regime background bands (drawn first, behind everything) ──
    var REGIME_BAND_COLORS = {
      RISK_ON:  'rgba(126,247,184,0.07)',   // faint green
      RISK_OFF: 'rgba(255,79,102,0.07)',    // faint red
      NEUTRAL:  'rgba(255,199,88,0.04)',    // very faint gold
    };
    var bandsSvg = '';
    var bands = shaped.regimeBands || [];
    for(var bi = 0; bi < bands.length; bi++){
      var band = bands[bi];
      var bx1 = xFor(band.tStart);
      var bx2 = xFor(band.tEnd);
      // Extend first/last bands to plot edges for visual continuity
      if(bi === 0) bx1 = margin.left;
      if(bi === bands.length - 1) bx2 = width - margin.right;
      var bw = Math.max(bx2 - bx1, 1);
      var bColor = REGIME_BAND_COLORS[band.regime] || REGIME_BAND_COLORS.NEUTRAL;
      bandsSvg += '<rect x="' + bx1.toFixed(1) + '" y="' + margin.top + '" width="' + bw.toFixed(1) + '" height="' + plotH + '" fill="' + bColor + '"></rect>';
    }

    // ── Posture change markers (vertical dashed lines with label) ──
    var POSTURE_COLORS = {
      aggressive:   'rgba(126,247,184,0.55)',
      constructive: 'rgba(126,247,184,0.40)',
      selective:    'rgba(255,199,88,0.45)',
      defensive:    'rgba(255,79,102,0.50)',
    };
    var posturesSvg = '';
    var markers = shaped.postureMarkers || [];
    for(var mi = 0; mi < markers.length; mi++){
      var m = markers[mi];
      // Skip the very first marker (initial state, not a "change")
      if(mi === 0) continue;
      var mx = xFor(m.ts);
      if(mx < margin.left || mx > width - margin.right) continue;
      var mColor = POSTURE_COLORS[m.stock] || 'rgba(215,251,255,0.3)';
      posturesSvg += '<line x1="' + mx.toFixed(1) + '" y1="' + margin.top + '" x2="' + mx.toFixed(1) + '" y2="' + (height - margin.bottom) + '" stroke="' + mColor + '" stroke-width="1" stroke-dasharray="4,3" opacity="0.7" shape-rendering="crispEdges"></line>';
      // Small posture label at top
      posturesSvg += '<text x="' + (mx + 3).toFixed(1) + '" y="' + (margin.top + 10) + '" fill="' + mColor + '" font-size="8" letter-spacing="0.03em" font-family="var(--font-body)">' + m.stock.charAt(0).toUpperCase() + m.stock.slice(1) + '</text>';
    }

    mpHistorySvgEl.innerHTML = bandsSvg + yGrid + xGrid + axes + yLabels + xLabels + posturesSvg + paths;

    // Legend — engine lines + regime bands + posture markers
    var legendHtml = '';
    for(var li = 0; li < ENGINE_HISTORY_SERIES.length; li++){
      var ls = ENGINE_HISTORY_SERIES[li];
      legendHtml += '<span class="home-mp-legend-item"><span class="home-mp-legend-swatch" style="background:' + ls.color + ';"></span>' + ls.label + '</span>';
    }
    // Regime band legend
    legendHtml += '<span class="home-mp-legend-sep"></span>';
    legendHtml += '<span class="home-mp-legend-item"><span class="home-mp-legend-swatch home-mp-legend-band" style="background:rgba(126,247,184,0.35);"></span>Risk-On</span>';
    legendHtml += '<span class="home-mp-legend-item"><span class="home-mp-legend-swatch home-mp-legend-band" style="background:rgba(255,199,88,0.30);"></span>Neutral</span>';
    legendHtml += '<span class="home-mp-legend-item"><span class="home-mp-legend-swatch home-mp-legend-band" style="background:rgba(255,79,102,0.35);"></span>Risk-Off</span>';
    // Posture marker legend
    legendHtml += '<span class="home-mp-legend-item"><span class="home-mp-legend-swatch home-mp-legend-dash"></span>Posture Change</span>';
    mpHistoryLegendEl.innerHTML = legendHtml;
  }

  /**
   * loadAndRenderMarketPictureHistory — fetch + shape + render.
   * Called once per dashboard load, fire-and-forget.
   */
  function loadAndRenderMarketPictureHistory(){
    if(!mpHistorySvgEl) return;
    api.getMarketPictureHistory(2000).then(function(resp){
      var entries = (resp && Array.isArray(resp.entries)) ? resp.entries : [];
      var shaped = _shapeHistoryEngineSeries(entries, 14);
      renderMarketPictureHistory(shaped);
    }).catch(function(err){
      console.warn('[MarketPictureHistory] fetch failed:', err?.message || err);
      renderMarketPictureHistory(null);
    });
  }

  function renderSnapshot(snapshot){
    const payload = (snapshot && typeof snapshot === 'object') ? snapshot : {};
    const data = (payload.data && typeof payload.data === 'object') ? payload.data : {};
    const meta = (payload.meta && typeof payload.meta === 'object') ? payload.meta : {};

    const regimePayload = data.regime || {};
    const spySummary = data.spy || emptySummary('SPY');
    const vixSummary = data.vix || emptySummary('VIXY');
    const macro = data.macro || {};
    const signalUniversePayload = data.signalsUniverse || { items: [] };
    const playbookPayload = data.playbook || null;
    const riskSnapshot = data.portfolioRisk || { portfolio: {} };
    const activeTradesPayload = data.activeTrades || { active_trades: [] };
    const ideas = Array.isArray(data.opportunities) ? data.opportunities : [];
    const indexSummaries = data.indexSummaries || Object.fromEntries(INDEX_SYMBOLS.map((symbol) => [symbol, emptySummary(symbol)]));
    const sectorSummaries = data.sectors || {};
    const scoreboardPayload = data.scoreboard || {};

    // Stash regime + playbook for on-demand model analysis (auto-refresh safe)
    _latestRegimePayload = regimePayload;
    _latestPlaybookPayload = playbookPayload;
    // Enable chat button now that regime context is available
    _updateChatBtnState();

    renderRegime(regimePayload, spySummary, macro, indexSummaries);
    // Restore cached regime model analysis immediately (before auto-run updates it)
    _restoreRegimeModelResult(data);
    renderScoreboard(scoreboardPayload);
    renderIndexes(indexSummaries);
    renderSectors(sectorSummaries, regimePayload);
    renderScannerOpportunities(ideas);

    // Shape and render Stock + Options playbooks from market-picture context
    const _regimeLabelRaw = String(regimePayload?.regime_label || 'NEUTRAL').toUpperCase();
    const _regimeScoreNum = toNumber(regimePayload?.regime_score) ?? 50;
    const _regimeComps = regimePayload?.components || {};
    const _vixForPB = macro?.vix;
    _renderPlaybookPanel(stockStrategyPlaybookEl, _shapeStockPlaybook(_regimeLabelRaw, _regimeScoreNum, _regimeComps, _vixForPB));
    _renderPlaybookPanel(optionsStrategyPlaybookEl, _shapeOptionsPlaybook(_regimeLabelRaw, _regimeScoreNum, _regimeComps, _vixForPB, scoreboardPayload));
    /* Source Health / Session Stats / Strategy Leaderboard are global-only — not rendered here */
    renderRisk(riskSnapshot, activeTradesPayload);
    renderActiveTradesCount(activeTradesPayload);
    renderEquityCurve(activeTradesPayload);
    renderMacro(macro, spySummary);

    renderChart(spyChartEl, spySummary?.history || [], { stroke: 'rgba(0,234,255,0.95)' });
    renderChart(vixChartEl, vixSummary?.history || [], { stroke: 'rgba(255,199,88,0.95)' });
    /* Additional index charts */
    renderChart(diaChartEl, indexSummaries?.DIA?.history || [], { stroke: 'rgba(0,234,255,0.85)' });
    renderChart(qqqChartEl, indexSummaries?.QQQ?.history || [], { stroke: 'rgba(0,234,255,0.85)' });
    renderChart(iwmChartEl, indexSummaries?.IWM?.history || [], { stroke: 'rgba(0,234,255,0.85)' });
    renderChart(mdyChartEl, indexSummaries?.MDY?.history || [], { stroke: 'rgba(0,234,255,0.85)' });

    // Market Picture History — fire-and-forget async fetch + render
    loadAndRenderMarketPictureHistory();

    // Macro proxy charts — fire-and-forget async fetch + render
    loadAndRenderRegimeProxies(macro);

    // VIX canary: compare chart last price (VIXY ETF) with macro card VIX value
    var mc = window.BenTradeMarketContext;
    if(mc){
      var chartLast = vixSummary?.price?.last;
      var cardVix = macro?.vix;
      mc.vixCanaryCheck(chartLast, cardVix);
    }

    updateLastUpdated(meta.last_success_at);
    if(Array.isArray(meta.errors) && meta.errors.length){
      setError('Using cached data while refreshing.');
    } else {
      setError('');
    }

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(scope);
    }
    if(window.BenTradeBenTooltip?.bindAll){
      window.BenTradeBenTooltip.bindAll(scope);
    }
  }

  function renderFallbackBlank(){
    const snapshot = {
      data: {
        regime: {},
        spy: emptySummary('SPY'),
        vix: emptySummary('VIXY'),
        macro: {},
        signalsUniverse: { items: [] },
        playbook: null,
        portfolioRisk: { portfolio: {} },
        activeTrades: { active_trades: [] },
        opportunities: [],
        indexSummaries: Object.fromEntries(INDEX_SYMBOLS.map((symbol) => [symbol, emptySummary(symbol)])),
        sectors: {},
        scoreboard: {},
      },
      meta: { last_success_at: null, errors: [], partial: false },
    };
    renderSnapshot(snapshot);
  }

  function bindRetry(){
    // Legacy retry is no longer needed — playbooks render from available context
  }

  const LOG_HISTORY_LIMIT = 500;
  const logHistory = [];

  function stampLog(text){
    const ts = new Date().toLocaleTimeString();
    return `[${ts}] ${String(text || '')}`;
  }

  function pushLog(text){
    logHistory.push(stampLog(text));
    if(logHistory.length > LOG_HISTORY_LIMIT){
      logHistory.splice(0, logHistory.length - LOG_HISTORY_LIMIT);
    }
    if(overlay?.isOpen?.()){
      overlay.setLines(logHistory);
    }
  }

  let _holdRefreshBadge = false;

  function setRefreshingBadge(isVisible){
    if(!isVisible && _holdRefreshBadge) return; // held during data population boot
    refreshingBadgeEl.style.display = isVisible ? 'inline-flex' : 'none';
  }

  const QUEUE_LOG_LIMIT = 8;
  const queueLogLines = [];
  const queueState = {
    isRunning: false,
    stopRequested: false,
    runId: 0,
  };


  function renderQueueLog(){
    if(!queueLogEl) return;
    if(!queueLogLines.length){
      queueLogEl.style.display = 'none';
      queueLogEl.innerHTML = '';
      return;
    }
    queueLogEl.style.display = 'grid';
    queueLogEl.innerHTML = queueLogLines
      .map((entry) => `<div class="home-queue-log-line ${entry.kind === 'fail' ? 'fail' : ''}">${entry.text}</div>`)
      .join('');
  }

  function appendQueueLog(text, kind = 'info'){
    queueLogLines.push({ text: String(text || ''), kind: String(kind || 'info') });
    if(queueLogLines.length > QUEUE_LOG_LIMIT){
      queueLogLines.splice(0, queueLogLines.length - QUEUE_LOG_LIMIT);
    }
    renderQueueLog();
  }

  function setQueueProgress({ current, completed, total, running }){
    if(queueProgressEl) queueProgressEl.style.display = 'flex';
    if(queueCurrentEl) queueCurrentEl.textContent = String(current || 'Idle');
    if(queueCountEl) queueCountEl.textContent = `${Number(completed || 0)}/${Number(total || 0)}`;
    if(queueSpinnerEl) queueSpinnerEl.style.display = running ? 'inline-block' : 'none';
  }

  function resetQueueProgress(){
    if(queueProgressEl) queueProgressEl.style.display = 'none';
    if(queueCurrentEl) queueCurrentEl.textContent = 'Idle';
    if(queueCountEl) queueCountEl.textContent = '0/0';
    if(queueSpinnerEl) queueSpinnerEl.style.display = 'none';
    queueLogLines.splice(0, queueLogLines.length);
    renderQueueLog();
  }

  function withTimeout(promise, timeoutMs, label){
    const ms = Math.max(1000, Number(timeoutMs || 0));
    if(!ms) return promise;
    return Promise.race([
      promise,
      new Promise((_, reject) => {
        window.setTimeout(() => {
          const err = new Error(`${String(label || 'step')} timeout`);
          err.code = 'timeout';
          reject(err);
        }, ms);
      }),
    ]);
  }

  function isNotImplementedError(err){
    const status = Number(err?.status || err?.statusCode);
    if(status === 404 || status === 405 || status === 501) return true;
    const detail = String(err?.detail || err?.message || '').toLowerCase();
    return detail.includes('not implemented') || detail.includes('not found');
  }

  function readErrorMessageFromPayload(payload){
    const p = (payload && typeof payload === 'object') ? payload : {};
    return String(
      p?.error?.message
      || p?.detail
      || p?.message
      || p?.error
      || ''
    ).trim();
  }

  function describeRefreshError(err, step){
    const status = String(err?.status || err?.statusCode || err?.code || 'n/a');
    const endpoint = String(err?.endpoint || step?.endpoint || 'n/a');
    const payload = err?.payload && typeof err.payload === 'object' ? err.payload : null;
    const payloadMessage = readErrorMessageFromPayload(payload);
    const payloadDetail = String(payload?.error?.details?.message || payload?.error?.details?.detail || payload?.error?.details || '').trim();
    const detail = payloadMessage || payloadDetail || String(err?.detail || err?.message || '').trim() || 'n/a';
    const bodySnippet = String(err?.bodySnippet || '').trim();
    return {
      status,
      endpoint,
      detail,
      bodySnippet: bodySnippet ? bodySnippet.slice(0, 200) : '',
    };
  }

  function updateHomeSessionSnapshot(){
    try{
      const snap = cacheStore?.getSnapshot?.();
      if(!snap || typeof snap !== 'object') return;
      const data = (snap.data && typeof snap.data === 'object') ? { ...snap.data } : {};
      data.sessionStats = window.BenTradeSessionStatsStore?.getState?.() || data.sessionStats || { total_candidates: 0, accepted_trades: 0, by_module: {} };
      cacheStore.setSnapshot({ ...snap, data });
    }catch(_err){
    }
  }

  async function runScanQueue(){
    if(queueState.isRunning) return;

    const orchestrator = window.BenTradeScannerOrchestrator;
    if(!orchestrator){
      setScanError('Scanner orchestrator unavailable');
      return;
    }

    const preset = String((scanPresetEl && scanPresetEl.value) || 'balanced');
    const filterLevel = preset;   // dropdown now selects filter strictness level
    const scannerIds = orchestrator.presetToScannerIds(preset);
    const total = scannerIds.length;
    const runId = ++queueState.runId;
    let completed = 0;
    let warnings = 0;
    let criticalFail = null;

    queueState.isRunning = true;
    queueState.stopRequested = false;
    if(runQueueBtnEl) runQueueBtnEl.disabled = true;
    if(stopQueueBtnEl) stopQueueBtnEl.disabled = false;
    if(scanPresetEl) scanPresetEl.disabled = true;
    setScanError('');
    setScanStatus('');
    queueLogLines.splice(0, queueLogLines.length);
    renderQueueLog();

    appendQueueLog(`Queue level: ${preset} (${total} scanners)`);
    setQueueProgress({ current: 'Starting scanner suite...', completed: 0, total, running: true });

    try{
      /* Pass selected symbol subset if the user has narrowed the universe */
      const selectedSymbols = _homeSymbolSelector?.getSelected?.() || [];
      const result = await orchestrator.runScannerSuite({
        scannerIds,
        symbols: selectedSymbols.length ? selectedSymbols : undefined,
        filterLevel,
        logFn: (text) => {
          appendQueueLog(text);
          pushLog(text);
        },
        onStepComplete: ({ id, label, ok, error, tradeCount }) => {
          if(runId !== queueState.runId || queueState.stopRequested) return;
          if(ok){
            completed += 1;
            setQueueProgress({ current: `${label} (${tradeCount})`, completed, total, running: true });
          }else{
            // Check if this was an optional scanner
            const isDef = orchestrator.OPTION_SCANNER_DEFS.find((d) => d.id === id);
            const isOptional = isDef ? isDef.optional : false;
            if(isOptional){
              warnings += 1;
            }else{
              criticalFail = { id, label, error };
            }
            setQueueProgress({ current: `${label} failed`, completed, total, running: true });
          }
        },
      });

      if(runId !== queueState.runId) return;

      // Update home session snapshot after stats recording
      updateHomeSessionSnapshot();

      if(queueState.stopRequested){
        setQueueProgress({ current: 'Stopped', completed, total, running: false });
        setScanStatus('Stopped');
        appendQueueLog('Stopped: remaining steps cancelled');
      }else if(criticalFail){
        setQueueProgress({ current: 'Stopped on failure', completed, total, running: false });
        setScanError(`Queue failed at ${criticalFail.label}: ${criticalFail.error || 'n/a'}`);
        setScanStatus('Queue stopped');
      }else{
        const warnCount = result?.errors?.length || warnings;
        setQueueProgress({ current: 'Queue complete', completed: total, total, running: false });
        if(warnCount > 0){
          setScanStatus(`Queue complete with warnings (${warnCount}) • ${new Date().toLocaleTimeString()}`);
        }else{
          setScanStatus(`Queue complete • ${new Date().toLocaleTimeString()}`);
        }
      }

      await runLoadSequence({ force: true, showOverlay: false, homeOnly: false, reason: 'post_scan' }).catch(() => {});
      updateHomeScanCacheUI();
    }finally{
      if(runId === queueState.runId){
        queueState.isRunning = false;
        queueState.stopRequested = false;
        if(runQueueBtnEl) runQueueBtnEl.disabled = false;
        if(stopQueueBtnEl) stopQueueBtnEl.disabled = true;
        if(scanPresetEl) scanPresetEl.disabled = false;
      }
    }
  }

  function stopScanQueue(){
    if(!queueState.isRunning) return;
    queueState.stopRequested = true;
    setScanStatus('Stopping queue...');
    appendQueueLog('Stop requested');
    const countText = (queueCountEl && queueCountEl.textContent) || '0/0';
    setQueueProgress({ current: 'Stopping...', completed: Number(countText.split('/')[0] || 0), total: Number(countText.split('/')[1] || 0), running: true });
  }

  const cacheStore = window.BenTradeHomeCacheStore;
  let activeLoadToken = 0;
  let _loadInFlight = null;       // singleton guard — prevents overlapping load sequences
  const overlay = window.BenTradeHomeLoadingOverlay?.create?.(scope) || null;

  if(!cacheStore){
    renderFallbackBlank();
    setError('Home cache store unavailable');
    return;
  }

  cacheStore.setRenderer((snapshot) => {
    renderSnapshot(snapshot || {});
    bindRetry();
  });

  /**
   * @param {Object} opts
   * @param {boolean} [opts.force=false]
   * @param {boolean} [opts.showOverlay=false]
   * @param {boolean} [opts.homeOnly=true]
   * @param {string}  [opts.reason='unknown'] - Refresh reason for logging: 'bootstrap'|'manual'|'full_app_refresh'|'post_scan'
   */
  function runLoadSequence({ force = false, showOverlay = false, homeOnly = true, reason = 'unknown' } = {}){
    const _t0 = Date.now();
    console.log(`[HOME_REFRESH] ${reason} started (force=${force}, overlay=${showOverlay}, homeOnly=${homeOnly})`);

    /* ── Singleton guard: if a non-forced load is already running, reuse it ── */
    if(_loadInFlight && !force){
      console.log(`[HOME_REFRESH] ${reason} reusing in-flight load`);
      // If caller wants the overlay but it's not open yet, open it now
      if(showOverlay && overlay && !overlay.isOpen()){
        overlay.open({
          status: 'Loading...',
          logs: logHistory,
          onCancel: () => { overlay.close(); },
          onRetry: () => { runLoadSequence({ force: true, showOverlay: true, homeOnly, reason: 'manual_retry' }).catch(() => {}); },
        });
      }
      return _loadInFlight;
    }
    /* For forced reloads while in-flight, let refreshCore handle dedup internally.
       We still replace _loadInFlight so the new promise is the canonical one. */

    const loadToken = ++activeLoadToken;

    if(showOverlay && overlay){
      overlay.open({
        status: 'Starting...',
        logs: logHistory,
        onCancel: () => {
          overlay.close();
        },
        onRetry: () => {
          runLoadSequence({ force: true, showOverlay: true, homeOnly, reason: 'manual_retry' }).catch(() => {});
        },
      });
    }

    if(!showOverlay){
      setRefreshingBadge(true);
    }

    pushLog(homeOnly ? 'Starting home data load (home-only)...' : 'Starting home data load (full)...');

    const refreshPromise = force
      ? cacheStore.refreshNow({ logFn: pushLog, homeOnly })
      : cacheStore.refreshSilent({ force: false, logFn: pushLog, homeOnly });

    _loadInFlight = refreshPromise
      .then((snapshot) => {
        const elapsed = ((Date.now() - _t0) / 1000).toFixed(1);
        pushLog('Home ready.');
        console.log(`[HOME_REFRESH] ${reason} completed in ${elapsed}s`);
        setError('');
        window.BenTradeSessionStatsStore?.recordHomeRefresh?.();
        if(overlay && overlay.isOpen()){
          overlay.setStatus('Home ready.');
          setTimeout(() => { overlay.close(); }, 600);
        }
        return snapshot;
      })
      .catch((err) => {
        const elapsed = ((Date.now() - _t0) / 1000).toFixed(1);
        const message = String(err?.message || err || 'Refresh failed');
        pushLog(`Error: home n/a ${message}`);
        console.warn(`[HOME_REFRESH] ${reason} failed after ${elapsed}s: ${message}`);
        if(overlay && overlay.isOpen()){
          overlay.setStatus('Load finished with errors');
          // Leave overlay open so user can see the error, but make Cancel visible
        }
        setError(message);
        throw err;
      })
      .finally(() => {
        _loadInFlight = null;
        setRefreshingBadge(false);
      });

    return _loadInFlight;
  }

  /* ── Boot: welcome modal + sequential data pipeline ── */
  const bootModal = window.BenTradeBootChoiceModal;
  const hadCached = cacheStore.renderCachedImmediately();
  if(!hadCached){
    renderFallbackBlank();
  }

  /**
   * Poll backend data-population status until it completes or fails.
   * Updates the welcome modal phase indicators if provided.
   */
  async function pollDataPopulation(bootUI){
    const POLL_INTERVAL = 2000;
    const MAX_POLLS = 150; // 5 min max
    for(let i = 0; i < MAX_POLLS; i++){
      try{
        const status = await api.getDataPopulationStatus();
        const phase = status?.phase || 'idle';
        if(bootUI){
          if(phase === 'market_data'){
            bootUI.activatePhase('market_data');
          } else if(phase === 'model_analysis'){
            bootUI.setPhaseDone('market_data');
            bootUI.activatePhase('model_analysis');
            if(status.model_progress) bootUI.setModelProgress(status.model_progress);
          } else if(phase === 'completed'){
            bootUI.setPhaseDone('market_data');
            bootUI.setPhaseDone('model_analysis');
            if(status.model_progress) bootUI.setModelProgress(status.model_progress);
            return status;
          } else if(phase === 'failed'){
            bootUI.setPhaseDone('market_data');
            if(status.model_progress) bootUI.setModelProgress(status.model_progress);
            return status;
          }
        }
        if(phase === 'completed' || phase === 'failed') return status;
      }catch(_err){
        // Polling error — backend may not be ready yet, keep trying
      }
      await new Promise(r => setTimeout(r, POLL_INTERVAL));
    }
    return { phase: 'timeout' };
  }

  if(bootModal && !bootModal.alreadyChosen()){
    /* First visit this session — parallel startup orchestration.
     *
     * Branch A: backend data-population (market_data → model_analysis phases)
     *   Triggered immediately; progress tracked via polling.
     *
     * Branch B: home dashboard data load + auto regime model analysis
     *   Loads whatever data the backend already has, then auto-triggers the
     *   Market Regime model analysis to populate the right-side guidance area.
     *
     * The loading modal stays up until BOTH branches complete.
     */
    const bootUI = bootModal.create(scope);
    bootUI.show();

    // Hold the refreshing badge visible for the entire startup cycle
    _holdRefreshBadge = true;
    setRefreshingBadge(true);
    refreshingBadgeEl.innerHTML = '<span class="badge-spinner" aria-hidden="true"></span>Populating Data\u2026';

    // ── Trigger backend data-population pipeline ──
    api.triggerDataPopulation().catch(() => {});

    // ── Branch A: poll backend population status ──
    // Manages market_data + model_analysis boot-modal phases.
    bootUI.activatePhase('market_data');
    const populationDone = pollDataPopulation(bootUI).then((finalStatus) => {
      console.log('[STARTUP] Backend population complete:', finalStatus?.phase);
      return finalStatus;
    });

    // ── Branch B: load dashboard data + auto-run regime model analysis ──
    // Runs in parallel with data population.
    bootUI.activatePhase('dashboard');
    const dashboardDone = (async () => {
      try {
        await runLoadSequence({ force: true, showOverlay: false, homeOnly: true, reason: 'startup_parallel' });
      } catch(_e) {
        bindRetry();
      }
      bootUI.setPhaseDone('dashboard');
      // Auto-trigger Market Regime model analysis now that regime data is loaded.
      // Result populates the right-side Regime Guidance area (comparison table + model output).
      bootUI.activatePhase('dashboard_model');
      bootUI.setRegimeStatus('running');
      try {
        await runRegimeModelAnalysis();
        bootUI.setRegimeStatus('done');
      } catch(_e) {
        // Non-fatal — guidance area retains engine-derived chips as baseline
        bootUI.setRegimeStatus('failed');
      }
      bootUI.setPhaseDone('dashboard_model');
    })();

    // ── Wait for both branches, then dismiss modal ──
    Promise.allSettled([populationDone, dashboardDone]).then(() => {
      setTimeout(() => {
        bootUI.close();
        setTimeout(() => bootUI.destroy(), 500);
      }, 1500);
      _holdRefreshBadge = false;
      setRefreshingBadge(false);
    });

  } else {
    /* Already chose this session (SPA re-mount) — render cached data,
       then kick off a silent background refresh if the cache is stale.
       This ensures the dashboard is never blank after navigate-away/back. */
    console.log('[HOME_REFRESH] SPA re-mount — cache rendered: ' + hadCached);
    if(!hadCached){
      bindRetry();
    }
    // Background refresh if stale (non-blocking, preserves current UI)
    if(cacheStore.isStale(null, cacheStore.FRESH_TTL_MS)){
      console.log('[HOME_REFRESH] SPA re-mount — cache stale, starting silent background refresh');
      runLoadSequence({ force: false, showOverlay: false, homeOnly: true, reason: 'remount_stale' }).then(function(){
        // Auto-trigger model analysis after fresh data loads
        return runRegimeModelAnalysis();
      }).catch(function(err){
        console.warn('[HOME_REFRESH] SPA re-mount silent refresh failed:', err?.message || err);
      });
    }
  }

  refreshBtnEl.addEventListener('click', async () => {
    refreshBtnEl.classList.add('btn-refreshing');
    refreshBtnEl.innerHTML = '<span class="btn-spinner"></span>Refreshing\u2026';
    refreshBtnEl.disabled = true;
    try{
      await runLoadSequence({ force: true, showOverlay: false, reason: 'manual' });
      setError('');
      // Auto-trigger model analysis after manual refresh
      runRegimeModelAnalysis().catch(function(){});
    }catch(err){
      setError(String(err?.message || err || 'Refresh failed'));
    }finally{
      refreshBtnEl.disabled = false;
      refreshBtnEl.classList.remove('btn-refreshing');
      refreshBtnEl.innerHTML = 'Refresh';
    }
  });



  if(runQueueBtnEl){
    runQueueBtnEl.addEventListener('click', () => {
      runScanQueue().catch((err) => {
        setScanError(String(err?.message || err || 'Queue failed'));
        setScanStatus('');
      });
    });
  }

  if(stopQueueBtnEl){
    stopQueueBtnEl.addEventListener('click', () => {
      stopScanQueue();
    });
  }

  if(clearScanResultsBtnEl){
    clearScanResultsBtnEl.addEventListener('click', () => {
      clearHomeScanResults();
    });
  }

  resetQueueProgress();
  updateHomeScanCacheUI();

  return function cleanupHome(){
    queueState.stopRequested = true;
    queueState.isRunning = false;
    queueState.runId += 1;
    _loadInFlight = null;
    activeLoadToken += 1;
    if(overlay){
      overlay.destroy();
    }
    setRefreshingBadge(false);
    // NOTE: do NOT null the renderer here.
    // The renderer is harmlessly overwritten on next initHome().
    // Nulling it caused setSnapshot() calls (from in-flight refreshes completing
    // after navigate-away) to fire into nothing, and subsequent remounts
    // to lose the ability to render cached data.
  };
};
