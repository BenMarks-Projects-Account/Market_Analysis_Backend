window.BenTradePages = window.BenTradePages || {};

/* ── In-memory session store (survives SPA navigation, clears on F5/close) ── */
window._BenTradeStockScannerStore = window._BenTradeStockScannerStore || { payload: null };

window.BenTradePages.initStockScanner = function initStockScanner(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const api = window.BenTradeApi;

  const refreshBtn = scope.querySelector('#stockScannerRefreshBtn');
  const errorEl = scope.querySelector('#stockScannerError');
  const metaEl = scope.querySelector('#stockScannerMeta');
  const listEl = scope.querySelector('#stockScannerList');
  const symbolsEl = scope.querySelector('#stockScannerSymbols');
  const countsBar = scope.querySelector('#tradeCountsBar');

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
  const collapsed = {};
  const modelResults = {};
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

  /* ── Session cache helpers (in-memory, no TTL, no auto-run) ── */
  function saveToSessionCache(payload){
    window._BenTradeStockScannerStore.payload = payload;
    console.debug('StockScanner: stored results in session cache (count=' + (Array.isArray(payload?.candidates) ? payload.candidates.length : 0) + ')');
  }

  function loadFromSessionCache(){
    const cached = window._BenTradeStockScannerStore.payload;
    if(cached && Array.isArray(cached.candidates) && cached.candidates.length > 0){
      console.debug('StockScanner: session cache hit (rendering cached results)');
      return cached;
    }
    console.debug('StockScanner: session cache miss (no scan yet; waiting for user)');
    return null;
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
  const fmtNum = fmt.num;
  const fmtPct = fmt.pct;
  const esc = fmt.escapeHtml;

  function ideaKey(row){
    const symbol = String(row?.symbol || '').toUpperCase();
    return String(row?.idea_key || `${symbol}|stock_scanner`);
  }

  function scoreToTone(score){
    const n = fmt.normalizeScore(score);
    if(n === null) return 'N/A';
    if(n >= 85) return 'Strong';
    if(n >= 70) return 'Constructive';
    if(n >= 55) return 'Neutral+';
    return 'Weak';
  }

  function pickStrategy(candidate){
    if(candidate?.type) return String(candidate.type);
    if(candidate?.recommended_strategy) return String(candidate.recommended_strategy);
    return 'stock_buy';
  }

  function renderMeta(payload){
    const asOf = payload?.as_of ? String(payload.as_of) : 'N/A';
    const count = Array.isArray(payload?.candidates) ? payload.candidates.length : 0;
    const srcStatus = payload?.source_status ? ` • Sources ${String(payload.source_status)}` : '';
    metaEl.textContent = `As of ${asOf} • ${count} candidates • Source ${lastEndpointUsed}${srcStatus}`;
    if(countsBar) countsBar.textContent = count ? `${count} candidate${count !== 1 ? 's' : ''} ranked` : '';
  }

  function renderModelIdeaRows(model, idx){
    const items = Array.isArray(model?.trade_ideas) ? model.trade_ideas : [];
    if(!items.length){
      return '<div class="detail-row"><span class="detail-label">Suggested Actions</span><span class="detail-value">None returned</span></div>';
    }

    return items.map((row, itemIdx) => {
      const type = String(row?.type || 'unknown');
      const action = String(row?.action || row?.strategy || '').trim();
      const reason = String(row?.reason || '').trim();
      const paramsText = (row?.params && typeof row.params === 'object') ? esc(JSON.stringify(row.params)) : '';
      const isOptions = type.toLowerCase() === 'options' && String(row?.strategy || '').trim();
      const suggestBtn = isOptions
        ? `<button class="btn" data-action="send-suggested-workbench" data-idx="${idx}" data-idea-idx="${itemIdx}" style="margin-top:8px;">Send suggested strategy to Workbench</button>`
        : '';

      return `
        <div class="detail-row" style="display:block;">
          <div><span class="detail-label">${esc(type)}</span> • <span class="detail-value">${esc(action || 'n/a')}</span></div>
          ${reason ? `<div class="stock-note" style="margin-top:4px;">${esc(reason)}</div>` : ''}
          ${paramsText ? `<div class="stock-note" style="margin-top:4px;">Params: ${paramsText}</div>` : ''}
          ${suggestBtn}
        </div>
      `;
    }).join('');
  }

  function modelSectionHtml(row, idx){
    const key = ideaKey(row);
    const model = modelResults[key];
    if(!model){
      return '';
    }

    const recommendation = String(model?.recommendation || 'WAIT').toUpperCase();
    const recClass = recommendation === 'BUY' ? 'rec-accept' : (recommendation === 'SELL' ? 'rec-reject' : 'rec-neutral');
    const confidence = Number(model?.confidence);
    const confidenceText = Number.isFinite(confidence) ? `${(confidence * 100).toFixed(1)}%` : 'N/A';

    const keyFactors = Array.isArray(model?.key_factors) ? model.key_factors : [];
    const risks = Array.isArray(model?.risks) ? model.risks : [];

    return `
      <div class="section section-model" style="display:block;">
        <div class="section-title">MODEL ANALYSIS</div>
        <div class="trade-details">
          <div class="detail-row"><span class="detail-label">Recommendation</span><span class="detail-value"><span class="model-value-pill ${recClass}">${esc(recommendation)}</span></span></div>
          <div class="detail-row"><span class="detail-label">Confidence</span><span class="detail-value">${esc(confidenceText)}</span></div>
          <div class="detail-row"><span class="detail-label">Time Horizon</span><span class="detail-value">${esc(model?.time_horizon || '1W')}</span></div>
          <div class="detail-row" style="display:block;"><span class="detail-label">Summary</span><div class="stock-note" style="margin-top:4px;">${esc(model?.summary || 'No summary.')}</div></div>
          <div class="detail-row" style="display:block;"><span class="detail-label">Key Factors</span><ul class="key-factors">${keyFactors.map(v => `<li>${esc(v)}</li>`).join('') || '<li>None</li>'}</ul></div>
          <div class="detail-row" style="display:block;"><span class="detail-label">Risks</span><ul class="key-factors">${risks.map(v => `<li>${esc(v)}</li>`).join('') || '<li>None</li>'}</ul></div>
          ${renderModelIdeaRows(model, idx)}
        </div>
      </div>
    `;
  }

  function sourceBadge(row){
    const state = String(row?.source_health?.status || '').toLowerCase();
    if(!state) return '';
    const label = state === 'ok' ? 'Source: OK' : (state === 'degraded' ? 'Source: Degraded' : 'Source: Down');
    return `<span class="data-warning-pill">${esc(label)}</span>`;
  }

  function renderCandidates(payload){
    const rows = Array.isArray(payload?.candidates) ? payload.candidates : [];
    renderedRows = rows.filter((row) => !rejectedIdeas.has(ideaKey(row)));

    Object.keys(collapsed).forEach((key) => {
      const idx = Number(key);
      if(!Number.isFinite(idx) || idx >= renderedRows.length){
        delete collapsed[key];
      }
    });

    for(let i = 0; i < renderedRows.length; i += 1){
      if(collapsed[i] === undefined) collapsed[i] = true;
    }

    const activeRows = renderedRows;
    if(!activeRows.length){
      listEl.innerHTML = '<div class="loading">No scanner candidates returned.</div>';
      return;
    }

    listEl.innerHTML = `<div class="trades-grid" style="width:100%">${activeRows.map((row, idx) => {
      const symbol = String(row?.symbol || '').toUpperCase();
      const signals = (Array.isArray(row?.signals) ? row.signals : []).map(s => String(s || '')).filter(Boolean);
      const signalsText = signals.length ? signals.join(', ') : 'none';
      const score = Number(row?.composite_score);
      const strategy = pickStrategy(row);
      const metrics = (row?.metrics && typeof row.metrics === 'object') ? row.metrics : {};
      const collapsedNow = !!collapsed[idx];
      const thesis = Array.isArray(row?.thesis) ? row.thesis : [];
      const sparklineText = Array.isArray(row?.sparkline) ? row.sparkline.map(v => fmtNum(v, 1)).join(' • ') : 'N/A';
      return `
        <div class="trade-card" data-idx="${idx}" data-symbol="${esc(symbol)}" data-strategy="${esc(strategy)}" data-score="${Number.isFinite(score) ? score : ''}">
          <div class="trade-header trade-header-click" data-action="toggle" data-idx="${idx}" role="button" aria-label="Toggle stock idea">
            <div class="trade-header-left"><span id="chev-${idx}" class="chev">${collapsedNow ? '▸' : '▾'}</span></div>
            <div class="trade-header-center">
              <div class="trade-type">#${idx + 1} ${esc(symbol)} • Stock Scanner Idea</div>
              <div class="trade-subtitle">
                <span class="trade-strikes-inline">${esc(strategy)}</span>
                <span class="underlying-price">(${fmtNum(row?.price, 2)})</span>
              </div>
              <div class="trade-rank-line">Rank Score: ${fmt.formatScore(row?.composite_score, 1)} (${scoreToTone(row?.composite_score)})</div>
              <span class="trade-key-wrap"><span class="trade-key-label" style="font-size:10px;color:rgba(230,251,255,0.5);font-family:monospace;word-break:break-all;">${esc(symbol)}|NA|${esc(strategy)}|NA|NA|NA</span>${card.copyTradeKeyButton(`${symbol}|NA|${strategy}|NA|NA|NA`)}</span>
            </div>
            <div class="trade-header-right">${sourceBadge(row)}</div>
          </div>

          <div id="tradeBody-${idx}" class="trade-collapsible ${collapsedNow ? 'is-collapsed' : ''}">
            <div class="trade-body">
              ${card.section('CORE METRICS', card.metricGrid([
                { label: 'Price', value: fmtNum(row?.price, 2), cssClass: 'neutral' },
                { label: 'Composite', value: fmt.formatScore(row?.composite_score, 1), cssClass: 'positive' },
                { label: 'Trend Score', value: fmt.formatScore(row?.trend_score, 1), cssClass: 'neutral' },
                { label: 'Momentum', value: fmt.formatScore(row?.momentum_score, 1), cssClass: 'neutral' },
                { label: 'Volatility', value: fmt.formatScore(row?.volatility_score, 1), cssClass: 'neutral' },
                { label: 'RSI14', value: fmtNum(metrics?.rsi14, 1), cssClass: 'neutral', dataMetric: 'rsi_14' },
                { label: 'RV20', value: fmtPct(metrics?.rv20, 1), cssClass: 'neutral', dataMetric: 'realized_vol_20d' },
                { label: 'IV/RV', value: fmtNum(metrics?.iv_rv_ratio, 2), cssClass: 'neutral', dataMetric: 'iv_rv_ratio' },
              ]), 'section-core')}

              ${card.section('IDEA DETAILS', card.detailRows([
                { label: 'Signals', value: esc(signalsText) },
                { label: 'Trend', value: esc(row?.trend || 'range') },
                { label: '1D Change', value: fmtPct(metrics?.price_change_1d, 2) },
                { label: '20D Change', value: fmtPct(metrics?.price_change_20d, 2) },
                { label: '52W Range', value: fmtNum(metrics?.low_52w, 2) + ' — ' + fmtNum(metrics?.high_52w, 2) },
              ]) + '<div class="detail-row" style="display:block;"><span class="detail-label">Thesis</span><ul class="key-factors">' + (thesis.map(v => `<li>${esc(v)}</li>`).join('') || '<li>No thesis notes</li>') + '</ul></div><div class="detail-row" style="display:block;"><span class="detail-label">Sparkline (24 bars, % from start)</span><div class="stock-note" style="margin-top:4px;">' + esc(sparklineText) + '</div></div>', 'section-details')}

              <div class="section section-details">
                <div class="section-title">NOTES</div>
                <div id="scannerIdeaNotes-${idx}"></div>
              </div>

              ${modelSectionHtml(row, idx)}
            </div>

            <div class="trade-actions">
              <div class="run-row"><button class="btn btn-run" id="runBtn-${idx}" data-action="run-model" data-idx="${idx}">Run Model Analysis</button></div>
              <div class="actions-row">
                <button class="btn btn-exec" data-action="execute" data-idx="${idx}">Execute Trade</button>
                <button class="btn btn-reject" data-action="reject" data-idx="${idx}">Reject</button>
              </div>
              <div class="actions-row">
                <button class="btn" data-action="open-analysis" data-symbol="${esc(symbol)}">Open in Stock Analysis</button>
                <button class="btn" data-action="send-workbench" data-idx="${idx}" data-symbol="${esc(symbol)}" data-strategy="${esc(strategy)}">Send to Testing Workbench</button>
              </div>
            </div>
          </div>
        </div>
      `;
    }).join('')}</div>`;

    activeRows.forEach((row, idx) => {
      const host = doc.getElementById(`scannerIdeaNotes-${idx}`);
      if(!host || !window.BenTradeNotes?.attachNotes) return;
      window.BenTradeNotes.attachNotes(host, `notes:idea:${ideaKey(row)}`);
    });
  }

  function openInStockAnalysis(symbol){
    const normalized = String(symbol || '').trim().toUpperCase();
    if(!normalized) return;
    localStorage.setItem('bentrade_selected_symbol', normalized);
    location.hash = '#stock-analysis';
  }

  function sendToWorkbench(symbol, strategy, score, params){
    const normalized = String(symbol || '').trim().toUpperCase();
    if(!normalized) return;

    const chosenStrategy = String(strategy || 'put_credit_spread').trim() || 'put_credit_spread';
    const payloadInput = {
      symbol: normalized,
      strategy: chosenStrategy,
      contractsMultiplier: 100,
    };
    if(params && typeof params === 'object'){
      Object.assign(payloadInput, params);
    }

    const payload = {
      from: 'stock_scanner',
      ts: new Date().toISOString(),
      input: payloadInput,
      trade_key: `${normalized}|NA|${chosenStrategy}|NA|NA|NA`,
      note: `Scanner composite score ${fmt.formatScore(score, 1)}`,
    };

    localStorage.setItem('bentrade_workbench_handoff_v1', JSON.stringify(payload));
    location.hash = '#/trade-testing';
  }

  function toggleCard(idx){
    const body = doc.getElementById(`tradeBody-${idx}`);
    const chev = doc.getElementById(`chev-${idx}`);
    if(!body) return;
    const isCollapsed = body.classList.toggle('is-collapsed');
    collapsed[idx] = isCollapsed;
    if(chev) chev.textContent = isCollapsed ? '▸' : '▾';
  }

  function executeStub(){
    const modal = doc.getElementById('modal');
    const modalMsg = doc.getElementById('modalMsg');
    if(modal && modalMsg){
      modalMsg.textContent = 'Trade capability off';
      modal.style.display = 'flex';
      return;
    }
    alert('Trade capability off');
  }

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

  async function runModelAnalysis(idx){
    const row = renderedRows[idx];
    if(!row) return;

    const runBtn = doc.getElementById(`runBtn-${idx}`);
    const key = ideaKey(row);
    if(runBtn){
      runBtn.disabled = true;
      runBtn.classList.add('is-loading');
      runBtn.textContent = 'Analyzing...';
    }

    try{
      const result = await api.modelAnalyzeStock(String(row?.symbol || ''), row, 'local_llm');
      modelResults[key] = result;
      renderCandidates(latestPayload || { candidates: [] });
      if(collapsed[idx]) toggleCard(idx);
    }catch(err){
      modelResults[key] = {
        recommendation: 'WAIT',
        confidence: 0.2,
        summary: String(err?.message || err || 'Model analysis failed'),
        key_factors: ['Model analysis request failed'],
        risks: ['Unable to fetch model output'],
        time_horizon: '1W',
        trade_ideas: [],
      };
      renderCandidates(latestPayload || { candidates: [] });
      if(collapsed[idx]) toggleCard(idx);
    }finally{
      const updatedBtn = doc.getElementById(`runBtn-${idx}`);
      if(updatedBtn){
        updatedBtn.disabled = false;
        updatedBtn.classList.remove('is-loading');
        updatedBtn.textContent = 'Run Model Analysis';
      }
    }
  }

  function sendSuggestedWorkbench(idx, ideaIdx){
    const row = renderedRows[idx];
    if(!row) return;
    const model = modelResults[ideaKey(row)] || {};
    const ideas = Array.isArray(model?.trade_ideas) ? model.trade_ideas : [];
    const suggestion = ideas[ideaIdx];
    if(!suggestion || String(suggestion?.type || '').toLowerCase() !== 'options') return;

    const strategy = String(suggestion?.strategy || row?.type || row?.recommended_strategy || pickStrategy(row));
    const params = (suggestion?.params && typeof suggestion.params === 'object') ? suggestion.params : {};
    sendToWorkbench(row?.symbol, strategy, Number(row?.composite_score), params);
  }

  listEl.addEventListener('click', (event) => {
    /* Copy trade key button */
    const copyBtn = event.target.closest('[data-copy-trade-key]');
    if(copyBtn){
      event.preventDefault();
      event.stopPropagation();
      card.copyTradeKey(copyBtn.dataset.copyTradeKey, copyBtn);
      return;
    }

    const button = event.target.closest('[data-action]');
    if(!button) return;
    const action = String(button.getAttribute('data-action') || '');
    const idx = Number(button.getAttribute('data-idx'));

    if(action === 'toggle'){
      if(Number.isFinite(idx)) toggleCard(idx);
      return;
    }

    if(action === 'run-model'){
      if(Number.isFinite(idx)) runModelAnalysis(idx);
      return;
    }

    if(action === 'execute'){
      executeStub();
      return;
    }

    if(action === 'reject'){
      if(Number.isFinite(idx)) rejectIdea(idx);
      return;
    }

    if(action === 'send-suggested-workbench'){
      const ideaIdx = Number(button.getAttribute('data-idea-idx'));
      if(Number.isFinite(idx) && Number.isFinite(ideaIdx)) sendSuggestedWorkbench(idx, ideaIdx);
      return;
    }

    const symbol = String(button.getAttribute('data-symbol') || '').toUpperCase();
    if(!symbol) return;

    if(action === 'open-analysis'){
      openInStockAnalysis(symbol);
      return;
    }

    if(action === 'send-workbench'){
      const strategy = String(button.getAttribute('data-strategy') || 'put_credit_spread');
      const card = button.closest('[data-score]');
      const score = card ? Number(card.getAttribute('data-score')) : NaN;
      sendToWorkbench(symbol, strategy, score);
    }
  });

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
};
