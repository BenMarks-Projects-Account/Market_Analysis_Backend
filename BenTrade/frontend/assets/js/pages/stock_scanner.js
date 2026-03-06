/**
 * @deprecated This generic stock scanner page is deprecated.
 * Use the dedicated strategy dashboards instead:
 *   - #/stocks/pullback-swing      (stock_pullback_swing.js)
 *   - #/stocks/momentum-breakout   (stock_momentum_breakout.js)
 *   - #/stocks/mean-reversion      (stock_mean_reversion.js)
 *   - #/stocks/volatility-expansion (stock_volatility_expansion.js)
 *
 * This file is retained for backward compatibility and will be
 * removed in a future release.
 */
window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initStockScanner = function initStockScanner(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const api = window.BenTradeApi;

  const refreshBtn = scope.querySelector('#stockScannerRefreshBtn');
  const clearBtn = scope.querySelector('#stockScannerClearBtn');
  const lastRunEl = scope.querySelector('#stockScannerLastRun');
  const errorEl = scope.querySelector('#stockScannerError');
  const metaEl = scope.querySelector('#stockScannerMeta');
  const listEl = scope.querySelector('#stockScannerList');
  const symbolsEl = scope.querySelector('#stockScannerSymbols');
  const countsBar = scope.querySelector('#tradeCountsBar');

  const CACHE_ID = 'stockScanner';
  const cache = window.BenTradeScanResultsCache;

  if(!refreshBtn || !errorEl || !metaEl || !listEl){
    return;
  }

  /* Mount symbol universe selector (add/remove chips + filter) */
  let _symbolSelector = null;
  if(symbolsEl && window.BenTradeSymbolUniverseSelector){
    _symbolSelector = window.BenTradeSymbolUniverseSelector.mount(symbolsEl, {
      showFilter: true,
      onChange: () => {},  // filter change is passive — applied on next scan
    });
  }

  let latestPayload = null;
  let renderedRows = [];
  let lastEndpointUsed = '/api/stock/scanner';
  const modelResults = {};  // legacy compat — also mirrored to shared store
  const _modelStore = window.BenTradeModelAnalysisStore;
  const _modelUI    = window.BenTradeModelAnalysis;
  const REJECTED_KEY = 'bentrade_scanner_rejected_v1';

  function loadRejected(){
    try{
      const raw = localStorage.getItem(REJECTED_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      if(Array.isArray(arr)) return new Set(arr.map(v => String(v || '')).filter(Boolean));
    }catch(_err){
    }
    return new Set();
  }

  const rejectedIdeas = loadRejected();

  function saveRejected(){
    try{
      localStorage.setItem(REJECTED_KEY, JSON.stringify(Array.from(rejectedIdeas)));
    }catch(_err){
    }
  }

  /* ── Session cache helpers (shared sessionStorage via BenTradeScanResultsCache) ── */
  function saveToSessionCache(payload){
    if(cache){
      cache.save(CACHE_ID, payload, { endpoint: lastEndpointUsed });
    }
    console.debug('StockScanner: stored results in session cache (count=' + (Array.isArray(payload?.candidates) ? payload.candidates.length : 0) + ')');
  }

  function loadFromSessionCache(){
    if(!cache) return null;
    const entry = cache.load(CACHE_ID);
    if(entry && entry.payload && Array.isArray(entry.payload.candidates) && entry.payload.candidates.length > 0){
      console.debug('StockScanner: session cache hit (rendering cached results)');
      return entry.payload;
    }
    console.debug('StockScanner: session cache miss (no scan yet; waiting for user)');
    return null;
  }

  function clearSessionCache(){
    if(cache) cache.clear(CACHE_ID);
    latestPayload = null;
    renderedRows = [];
    renderMeta({ as_of: null, candidates: [] });
    listEl.innerHTML = '<div class="loading">No scan yet. Click <b>Run Scan</b> to start.</div>';
    updateLastRunDisplay();
    updateClearBtnVisibility();
    setError('');
    console.debug('StockScanner: cleared session cache');
  }

  function updateLastRunDisplay(){
    if(!lastRunEl) return;
    if(!cache) { lastRunEl.textContent = ''; return; }
    const ts = cache.formatTimestamp(CACHE_ID);
    lastRunEl.textContent = ts !== 'N/A' ? 'Last run: ' + ts : '';
  }

  function updateClearBtnVisibility(){
    if(!clearBtn) return;
    const hasResults = cache && cache.load(CACHE_ID) !== null;
    clearBtn.style.display = hasResults ? 'inline-block' : 'none';
  }

  function setError(text){
    if(!text){
      errorEl.style.display = 'none';
      errorEl.textContent = '';
      return;
    }
    errorEl.style.display = 'block';
    errorEl.textContent = text;
  }

  function normalizeScannerPayload(payload){
    if(Array.isArray(payload?.candidates)){
      return {
        as_of: payload?.as_of || null,
        candidates: payload.candidates,
        notes: Array.isArray(payload?.notes) ? payload.notes : [],
        source_status: payload?.source_status || null,
      };
    }

    const legacyRows = Array.isArray(payload?.results) ? payload.results : [];
    const mapped = legacyRows.map((row) => ({
      symbol: row?.symbol,
      price: row?.price ?? null,
      trend_score: null,
      momentum_score: null,
      volatility_score: null,
      composite_score: row?.scanner_score,
      signals: Array.isArray(row?.signals)
        ? row.signals
        : (row?.signals && typeof row.signals === 'object' ? Object.keys(row.signals).filter(Boolean) : []),
    }));

    return {
      as_of: payload?.as_of || null,
      candidates: mapped,
      notes: Array.isArray(payload?.notes) ? payload.notes : [],
      source_status: null,
    };
  }

  async function fetchScannerPayload(){
    const endpoints = ['/api/stock/scanner', '/api/stock/scan?universe=default'];
    let lastError = null;

    for(const endpoint of endpoints){
      let response;
      try{
        response = await fetch(endpoint, { method: 'GET' });
      }catch(err){
        lastError = new Error(`Request failed for ${endpoint}: ${String(err?.message || err || 'network error')}`);
        continue;
      }

      const payload = await response.json().catch(() => ({}));
      if(response.ok){
        lastEndpointUsed = endpoint;
        return normalizeScannerPayload(payload);
      }

      const backendMessage = payload?.error?.message || payload?.detail || 'request failed';
      lastError = new Error(`HTTP ${response.status} (${endpoint}): ${backendMessage}`);
      lastError.status = response.status;

      if(response.status !== 404){
        throw lastError;
      }
    }

    throw (lastError || new Error('Scanner endpoint unavailable'));
  }

  // Shared utilities
  const fmt = window.BenTradeUtils.format;
  const card = window.BenTradeTradeCard;
  const _mapper = window.BenTradeOptionTradeCardModel;
  const fmtNum = fmt.num;
  const fmtPct = fmt.pct;
  const esc = fmt.escapeHtml;

  /* ── Collapse/expand state (persists across re-renders via tradeKey) ── */
  const _expandState = {};

  function ideaKey(row){
    const symbol = String(row?.symbol || '').toUpperCase();
    return String(row?.idea_key || `${symbol}|stock_scanner`);
  }

  function renderMeta(payload){
    const asOf = payload?.as_of ? String(payload.as_of) : 'N/A';
    const count = Array.isArray(payload?.candidates) ? payload.candidates.length : 0;
    const srcStatus = payload?.source_status ? ` • Sources ${String(payload.source_status)}` : '';
    metaEl.textContent = `As of ${asOf} • ${count} candidates • Source ${lastEndpointUsed}${srcStatus}`;
    if(countsBar) countsBar.textContent = count ? `${count} candidate${count !== 1 ? 's' : ''} ranked` : '';
  }

  /* ── Transform scanner candidate → mapper-compatible trade shape ── */
  function candidateToTradeShape(row){
    const symbol = String(row?.symbol || '').toUpperCase();
    const metrics = (row?.metrics && typeof row.metrics === 'object') ? row.metrics : {};
    return {
      symbol: symbol,
      strategy_id: 'stock_buy',
      trade_key: row?.trade_key || `${symbol}|NA|stock_buy|NA|NA|NA`,
      price: row?.price ?? null,
      underlying_price: row?.price ?? null,
      composite_score: row?.composite_score ?? null,
      trend_score: row?.trend_score ?? null,
      momentum_score: row?.momentum_score ?? null,
      volatility_score: row?.volatility_score ?? null,
      pullback_score: row?.pullback_score ?? null,
      catalyst_score: row?.catalyst_score ?? null,
      rsi14: metrics?.rsi14 ?? row?.rsi14 ?? null,
      iv_rv_ratio: metrics?.iv_rv_ratio ?? row?.iv_rv_ratio ?? null,
      ema20: metrics?.ema20 ?? row?.ema20 ?? null,
      sma50: metrics?.sma50 ?? row?.sma50 ?? null,
      trend: row?.trend || '',
      /* preserve raw candidate for extra sections */
      _scanner_candidate: row,
    };
  }

  /* ── Build stock-specific extra sections for the expandable body ── */
  function buildExtraSections(row, idx){
    const signals = (Array.isArray(row?.signals) ? row.signals : []).map(s => String(s || '')).filter(Boolean);
    const signalsText = signals.length ? signals.join(', ') : 'none';
    const thesis = Array.isArray(row?.thesis) ? row.thesis : [];
    const sparklineText = Array.isArray(row?.sparkline) ? row.sparkline.map(v => fmtNum(v, 1)).join(' \u2022 ') : '';
    const metrics = (row?.metrics && typeof row.metrics === 'object') ? row.metrics : {};

    const detailItems = [
      { label: 'Signals', value: esc(signalsText) },
      { label: 'Trend', value: esc(row?.trend || 'range') },
      { label: 'Price', value: '$' + fmtNum(row?.price, 2) },
    ];
    if(metrics?.price_change_1d != null) detailItems.push({ label: '1D Change', value: fmtPct(metrics.price_change_1d, 2) });
    if(metrics?.price_change_20d != null) detailItems.push({ label: '20D Change', value: fmtPct(metrics.price_change_20d, 2) });
    if(metrics?.low_52w != null || metrics?.high_52w != null) detailItems.push({ label: '52W Range', value: fmtNum(metrics?.low_52w, 2) + ' \u2014 ' + fmtNum(metrics?.high_52w, 2) });
    if(metrics?.rv20 != null) detailItems.push({ label: 'RV20', value: fmtPct(metrics.rv20, 1), dataMetric: 'realized_vol_20d' });

    let html = card.section('SCANNER DETAILS', card.detailRows(detailItems)
      + (thesis.length
        ? '<div class="detail-row" style="display:block;"><span class="detail-label" data-metric="thesis">Thesis</span><ul class="key-factors">' + thesis.map(v => '<li>' + esc(v) + '</li>').join('') + '</ul></div>'
        : '')
      + (sparklineText
        ? '<div class="detail-row" style="display:block;"><span class="detail-label">Sparkline</span><div class="stock-note" style="margin-top:4px;">' + esc(sparklineText) + '</div></div>'
        : ''),
    'section-details');

    html += '<div class="section section-details"><div class="section-title">NOTES</div><div id="scannerIdeaNotes-' + idx + '"></div></div>';

    return html;
  }

  /* ── Model output renderer (delegates to shared module) ── */
  function _renderModelOutputHtml(result){
    if(!result) return '';
    if(_modelUI){
      const parsed = _modelUI.parse(result);
      return _modelUI.render(parsed);
    }
    /* Fallback if shared module not loaded */
    const me = result.model_evaluation || result;
    const rec = String(me.recommendation || 'UNKNOWN').toUpperCase();
    return '<div style="font-size:12px;padding:8px;color:var(--text-secondary,#ccc);">' + esc(rec) + ' \u2014 ' + esc(String(me.summary || '')) + '</div>';
  }

  /* ── Render candidates using canonical TradeCard ── */
  function renderCandidates(payload){
    const rows = Array.isArray(payload?.candidates) ? payload.candidates : [];
    renderedRows = rows.filter((row) => !rejectedIdeas.has(ideaKey(row)));

    if(!renderedRows.length){
      listEl.innerHTML = '<div class="loading">No scanner candidates returned.</div>';
      return;
    }

    const cardsHtml = renderedRows.map((row, idx) => {
      const tradeObj = candidateToTradeShape(row);
      const extras = buildExtraSections(row, idx);

      /* Render via canonical card component */
      let html = card.renderFullCard(tradeObj, idx, {
        strategyHint: 'stock_buy',
        expandState: _expandState,
        rankOverride: fmt.normalizeScore(row?.composite_score) ?? null,
      });

      /* Inject stock-specific sections into trade-body (before </details>) */
      html = html.replace('</div></details>', extras + '</div></details>');

      return html;
    }).join('');

    listEl.innerHTML = '<div class="trades-grid" style="width:100%">' + cardsHtml + '</div>';

    /* Re-hydrate persisted model analysis results into freshly-created cards */
    if(_modelStore && typeof _modelStore.hydrateContainer === 'function'){
      _modelStore.hydrateContainer(listEl);
    }

    /* ── Post-render DOM enhancements ── */
    renderedRows.forEach((row, idx) => {
      const cardEl = listEl.querySelector('.trade-card[data-idx="' + idx + '"]');
      if(!cardEl) return;

      /* Add "Open in Stock Analysis" button to actions */
      const actionsEl = cardEl.querySelector('.trade-actions');
      if(actionsEl){
        const extraRow = document.createElement('div');
        extraRow.className = 'actions-row';
        const symbol = String(row?.symbol || '').toUpperCase();
        extraRow.innerHTML = '<button type="button" class="btn btn-action" data-action="open-analysis" data-symbol="' + esc(symbol) + '" title="Open in Stock Analysis">Open in Stock Analysis</button>';
        actionsEl.appendChild(extraRow);
      }

      /* Attach notes widget */
      const host = doc.getElementById('scannerIdeaNotes-' + idx);
      if(host && window.BenTradeNotes?.attachNotes){
        window.BenTradeNotes.attachNotes(host, 'notes:idea:' + ideaKey(row));
      }

      /* Hydrate cached model results into model-output slot (shared store) */
      const tradeKeyForStore = String(candidateToTradeShape(row).trade_key || '').trim();
      const storeEntry = tradeKeyForStore && _modelStore ? _modelStore.get(tradeKeyForStore) : null;
      if(storeEntry && storeEntry.status === 'success' && storeEntry.result){
        const outputEl = cardEl.querySelector('[data-model-output]');
        if(outputEl){
          outputEl.style.display = 'block';
          outputEl.innerHTML = _modelUI ? _modelUI.render(storeEntry.result) : '';
        }
      } else {
        /* Legacy fallback: hydrate from local modelResults */
        const key = ideaKey(row);
        if(modelResults[key] && !modelResults[key]._inflight){
          const outputEl = cardEl.querySelector('[data-model-output]');
          if(outputEl){
            outputEl.style.display = 'block';
            outputEl.innerHTML = _renderModelOutputHtml({ status: 'success', model_evaluation: modelResults[key] });
          }
        }
      }
    });

    /* Wire <details> toggle for expand state persistence */
    listEl.querySelectorAll('details.trade-card-collapse').forEach((details) => {
      details.addEventListener('toggle', () => {
        const tk = details.dataset.tradeKey || '';
        if(tk) _expandState[tk] = details.open;
      });
    });

    /* Attach metric tooltips to the freshly-rendered cards */
    if(window.attachMetricTooltips){
      window.attachMetricTooltips(listEl);
    }
  }

  /* ── Navigation helpers ── */
  function openInStockAnalysis(symbol){
    const normalized = String(symbol || '').trim().toUpperCase();
    if(!normalized) return;
    localStorage.setItem('bentrade_selected_symbol', normalized);
    location.hash = '#stock-analysis';
  }

  /* ── Reject idea ── */
  async function rejectIdea(idx){
    const row = renderedRows[idx];
    if(!row) return;
    const key = ideaKey(row);
    rejectedIdeas.add(key);
    saveRejected();
    window.BenTradeSessionStatsStore?.recordReject?.('stock_scanner', 1);

    if(api?.postLifecycleEvent){
      try{
        await api.postLifecycleEvent({
          event: 'REJECT',
          trade_key: key,
          source: 'stock_scanner',
          reason: 'manual_reject',
          trade: row,
        });
      }catch(_err){
      }
    }

    renderMeta(latestPayload || { as_of: null, candidates: [] });
    renderCandidates(latestPayload || { candidates: [] });
  }

  /* ── Build derived data for Data Workbench modal ── */
  function _buildDerivedData(rawRow, tradeObj, model, key){
    const metrics = (rawRow?.metrics && typeof rawRow.metrics === 'object') ? rawRow.metrics : {};
    const scoringInputs = {
      rsi14: metrics.rsi14 ?? null,
      rv20: metrics.rv20 ?? null,
      iv_rv_ratio: metrics.iv_rv_ratio ?? null,
      price_change_1d: metrics.price_change_1d ?? null,
      price_change_20d: metrics.price_change_20d ?? null,
      low_52w: metrics.low_52w ?? null,
      high_52w: metrics.high_52w ?? null,
      ema20: metrics.ema20 ?? null,
      sma50: metrics.sma50 ?? null,
    };
    const scoringOutputs = {
      composite_score: rawRow?.composite_score ?? null,
      trend_score: rawRow?.trend_score ?? null,
      momentum_score: rawRow?.momentum_score ?? null,
      volatility_score: rawRow?.volatility_score ?? null,
      pullback_score: rawRow?.pullback_score ?? null,
      catalyst_score: rawRow?.catalyst_score ?? null,
    };
    const mapperDiag = model ? {
      missingKeys: model.missingKeys || [],
      hasAllRequired: model.hasAllRequired,
      coreResolved: (model.coreMetrics || []).filter(m => m.value !== null).map(m => m.key),
      coreMissing: (model.coreMetrics || []).filter(m => m.value === null).map(m => m.key),
      detailResolved: (model.detailFields || []).filter(m => m.value !== null).map(m => m.key),
      detailMissing: (model.detailFields || []).filter(m => m.value === null).map(m => m.key),
    } : null;
    const modelAnalysis = modelResults[key] && !modelResults[key]._inflight ? modelResults[key] : null;
    return {
      scoring_inputs: scoringInputs,
      scoring_outputs: scoringOutputs,
      mapper_diagnostics: mapperDiag,
      model_analysis: modelAnalysis,
      signals: rawRow?.signals || [],
      trend: rawRow?.trend || null,
      thesis: rawRow?.thesis || [],
    };
  }

  /* ── Run model analysis on a card ── */
  async function runModelAnalysis(btn, cardEl, idx){
    const row = renderedRows[idx];
    if(!row) return;

    const key = ideaKey(row);
    const tradeObj = candidateToTradeShape(row);
    const tradeKey = String(tradeObj.trade_key || '').trim();
    const outputEl = cardEl?.querySelector('[data-model-output]');

    /* Guard against duplicate clicks */
    if(modelResults[key]?._inflight) return;
    modelResults[key] = { _inflight: true };
    if(tradeKey && _modelStore) _modelStore.setRunning(tradeKey);

    if(btn){
      btn.disabled = true;
      btn.textContent = 'Analyzing\u2026';
    }
    if(outputEl){
      outputEl.style.display = 'block';
      outputEl.innerHTML = _renderModelOutputHtml({ status: 'running' });
    }

    try{
      const result = await api.modelAnalyzeStock(String(row?.symbol || ''), row, 'local_llm');
      modelResults[key] = result;

      /* Persist in shared store */
      if(tradeKey && _modelStore && _modelUI){
        _modelStore.setSuccess(tradeKey, _modelUI.parse({ status: 'success', model_evaluation: result }));
      }

      if(outputEl){
        outputEl.style.display = 'block';
        outputEl.innerHTML = _renderModelOutputHtml({ status: 'success', model_evaluation: result });
      }
    }catch(err){
      const errMsg = String(err?.message || err || 'Model analysis failed');
      const errResult = {
        recommendation: 'WAIT',
        confidence: 0.2,
        summary: errMsg,
        key_factors: ['Model analysis request failed'],
        risks: ['Unable to fetch model output'],
        time_horizon: '1W',
        trade_ideas: [],
      };
      modelResults[key] = errResult;
      if(tradeKey && _modelStore) _modelStore.setError(tradeKey, errMsg);
      if(outputEl){
        outputEl.style.display = 'block';
        outputEl.innerHTML = _renderModelOutputHtml({ status: 'error', summary: errMsg });
      }
    }finally{
      if(btn){
        btn.disabled = false;
        const ts = new Date();
        const hhmm = String(ts.getHours()).padStart(2,'0') + ':' + String(ts.getMinutes()).padStart(2,'0');
        btn.textContent = '\u21BB Re-run Analysis ' + hhmm;
      }
    }
  }

  /* ── Action button delegation (matches strategy_dashboard_shell pattern) ── */
  listEl.addEventListener('click', (event) => {
    /* Copy trade key button */
    const copyBtn = event.target.closest('[data-copy-trade-key]');
    if(copyBtn){
      event.preventDefault();
      event.stopPropagation();
      card.copyTradeKey(copyBtn.dataset.copyTradeKey, copyBtn);
      return;
    }

    const btn = event.target.closest('[data-action]');
    if(!btn) return;
    event.preventDefault();
    event.stopPropagation();

    const action = String(btn.getAttribute('data-action') || '');
    const cardEl = btn.closest('.trade-card');
    const idx = cardEl ? parseInt(cardEl.dataset.idx, 10) : -1;
    const row = renderedRows[idx];
    if(!row && action !== 'open-analysis') return;

    if(action === 'model-analysis'){
      runModelAnalysis(btn, cardEl, idx);
      return;
    }

    if(action === 'execute'){
      const tradeObj = candidateToTradeShape(row);
      if(window.BenTradeExecutionModal && window.BenTradeExecutionModal.open){
        const model = _mapper ? _mapper.map(tradeObj, 'stock_buy') : null;
        const payload = (_mapper && model) ? _mapper.buildTradeActionPayload(model) : {};
        window.BenTradeExecutionModal.open(tradeObj, payload);
      } else {
        const modal = doc.getElementById('modal');
        const modalMsg = doc.getElementById('modalMsg');
        if(modal && modalMsg){
          modalMsg.textContent = 'Trade capability off';
          modal.style.display = 'flex';
        }
      }
      return;
    }

    if(action === 'reject'){
      if(Number.isFinite(idx)){
        /* Visual immediate feedback on the card */
        if(cardEl) cardEl.classList.add('manually-rejected');
        btn.disabled = true;
        btn.textContent = 'Rejected';
        rejectIdea(idx);
      }
      return;
    }

    if(action === 'open-analysis'){
      const symbol = String(btn.getAttribute('data-symbol') || '').toUpperCase();
      if(symbol) openInStockAnalysis(symbol);
      return;
    }

    if(action === 'data-workbench'){
      const tradeObj = row ? candidateToTradeShape(row) : null;
      if(tradeObj && window.BenTradeDataWorkbenchModal){
        const rawCandidate = row || null;
        const model = _mapper ? _mapper.map(tradeObj, 'stock_buy') : null;
        const derived = _buildDerivedData(row, tradeObj, model, ideaKey(row));
        window.BenTradeDataWorkbenchModal.open({
          symbol: tradeObj.symbol || '?',
          normalized: tradeObj,
          rawSource: rawCandidate,
          derived: derived,
        });
      } else if(tradeObj && card.openDataWorkbenchByTrade){
        card.openDataWorkbenchByTrade(tradeObj);
      }
      return;
    }

    if(action === 'workbench'){
      const tradeObj = row ? candidateToTradeShape(row) : null;
      if(tradeObj && card.openDataWorkbenchByTrade){
        card.openDataWorkbenchByTrade(tradeObj);
      } else if(tradeObj?.trade_key){
        window.location.hash = '#/admin/data-workbench?trade_key=' + encodeURIComponent(tradeObj.trade_key);
      }
      return;
    }
  });

  /* Wire <details> toggle at the container level (useCapture for toggle) */
  listEl.addEventListener('toggle', (e) => {
    const details = e.target;
    if(details.tagName !== 'DETAILS') return;
    const tk = details.dataset.tradeKey || '';
    if(tk) _expandState[tk] = details.open;
  }, true);

  async function runScan(){
    console.debug('StockScanner: user triggered scan -> fetching');
    const previousLabel = refreshBtn.textContent;
    try{
      setError('');
      refreshBtn.disabled = true;
      refreshBtn.textContent = 'Scanning…';
      const payload = await fetchScannerPayload();
      latestPayload = payload;
      saveToSessionCache(payload);
      renderMeta(payload);
      renderCandidates(payload);
      updateLastRunDisplay();
      updateClearBtnVisibility();
      window.BenTradeSessionStatsStore?.recordRun?.('stock_scanner', payload);
      window.BenTradeSourceHealthStore?.fetchSourceHealth?.({ force: true }).catch(() => {});
    }catch(err){
      const status = Number(err?.status);
      const prefix = Number.isFinite(status) ? `HTTP ${status}: ` : '';
      setError(`${prefix}${String(err?.message || err || 'Failed to run stock scan')}`);
      if(!latestPayload){
        metaEl.textContent = `As of N/A • 0 candidates • Source ${lastEndpointUsed}`;
        listEl.innerHTML = '<div class="loading">No scan yet. Click <b>Run Scan</b> to start.</div>';
      }
    }finally{
      refreshBtn.disabled = false;
      refreshBtn.textContent = previousLabel || 'Run Scan';
    }
  }

  refreshBtn.addEventListener('click', runScan);

  if(clearBtn){
    clearBtn.addEventListener('click', clearSessionCache);
  }

  /* ── Init: restore from session cache — NEVER auto-run ── */
  const _cached = loadFromSessionCache();
  if(_cached){
    latestPayload = _cached;
    renderMeta(_cached);
    renderCandidates(_cached);
  }else{
    renderMeta({ as_of: null, candidates: [] });
    listEl.innerHTML = '<div class="loading">No scan yet. Click <b>Run Scan</b> to start.</div>';
  }
  updateLastRunDisplay();
  updateClearBtnVisibility();
};
