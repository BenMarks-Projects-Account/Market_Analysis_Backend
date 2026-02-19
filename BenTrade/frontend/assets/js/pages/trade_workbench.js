window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initTradeWorkbench = function initTradeWorkbench(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const api = window.BenTradeApi;
  const sourceHealthUi = window.BenTradeSourceHealth;
  const tradeKeyUtil = window.BenTradeUtils?.tradeKey;

  const symbolEl = scope.querySelector('#wbSymbol');
  const expirationEl = scope.querySelector('#wbExpiration');
  const strategyEl = scope.querySelector('#wbStrategy');
  const shortStrikeEl = scope.querySelector('#wbShortStrike');
  const longStrikeEl = scope.querySelector('#wbLongStrike');
  const strikeEl = scope.querySelector('#wbStrike');
  const putShortEl = scope.querySelector('#wbPutShortStrike');
  const putLongEl = scope.querySelector('#wbPutLongStrike');
  const callShortEl = scope.querySelector('#wbCallShortStrike');
  const callLongEl = scope.querySelector('#wbCallLongStrike');
  const centerStrikeEl = scope.querySelector('#wbCenterStrike');
  const multiplierEl = scope.querySelector('#wbMultiplier');

  const spreadFieldsEl = scope.querySelector('#wbSpreadFields');
  const singleFieldsEl = scope.querySelector('#wbSingleFields');
  const condorFieldsEl = scope.querySelector('#wbCondorFields');
  const flyFieldsEl = scope.querySelector('#wbFlyFields');

  const analyzeBtn = scope.querySelector('#wbAnalyzeBtn');
  const mutateBtn = scope.querySelector('#wbMutateBtn');
  const saveScenarioBtn = scope.querySelector('#wbSaveScenarioBtn');

  const scenarioNameEl = scope.querySelector('#wbScenarioName');
  const scenarioNotesEl = scope.querySelector('#wbScenarioNotes');

  const resultEl = scope.querySelector('#wbResult');
  const suggestionsEl = scope.querySelector('#wbSuggestions');
  const sourceHealthEl = scope.querySelector('#wbSourceHealth');
  const scenarioListEl = scope.querySelector('#wbScenarioList');
  const pageNotesMountEl = scope.querySelector('#wbPageNotesMount');
  const legPreviewEl = scope.querySelector('#wbLegPreview');
  const keyPreviewEl = scope.querySelector('#wbKeyPreview');
  const analysisStatusEl = scope.querySelector('#wbAnalysisStatus');
  const errorEl = scope.querySelector('#wbError');
  const importNoticeEl = scope.querySelector('#wbImportNotice');

  if(!symbolEl || !expirationEl || !strategyEl || !multiplierEl || !analyzeBtn || !resultEl || !suggestionsEl || !scenarioListEl || !sourceHealthEl || !pageNotesMountEl || !legPreviewEl || !keyPreviewEl || !analysisStatusEl){
    return;
  }

  window.BenTradeNotes?.attachNotes?.(pageNotesMountEl, 'notes:page:trade-testing');

  const ANALYZABLE_STRATEGIES = new Set([
    'put_credit_spread',
    'call_credit_spread',
    'put_debit',
    'call_debit',
  ]);

  let selectedTrade = null;
  let selectedSuggestionKey = null;
  let scenarios = [];
  let importedHandoff = null;

  async function postLifecycle(eventName, trade, reason){
    if(!api?.postLifecycleEvent || !trade) return;
    const event = String(eventName || '').toUpperCase();
    if(!event) return;

    const tradeKey = String(trade.trade_key || '').trim();
    if(!tradeKey) return;

    const payloadTrade = { ...trade };
    if(event === 'CLOSE'){
      const text = window.prompt('Optional realized P&L (number):', '');
      if(text !== null && String(text).trim() !== ''){
        const value = Number(text);
        if(Number.isFinite(value)) payloadTrade.realized_pnl = value;
      }
    }

    try{
      await api.postLifecycleEvent({
        event,
        trade_key: tradeKey,
        source: 'workbench',
        trade: payloadTrade,
        reason: String(reason || '').trim(),
      });
    }catch(_err){
    }
  }

  function normalizeStrike(value){
    if(tradeKeyUtil?.normalizeStrike){
      return tradeKeyUtil.normalizeStrike(value);
    }
    if(value === null || value === undefined || value === '') return 'NA';
    const n = Number(value);
    return Number.isFinite(n) ? String(n).replace(/\.0+$/, '') : String(value);
  }

  const toNumber = window.BenTradeUtils.format.toNumber;

  function setError(text){
    if(!errorEl) return;
    if(!text){
      errorEl.style.display = 'none';
      errorEl.textContent = '';
      return;
    }
    errorEl.style.display = 'block';
    errorEl.textContent = text;
  }

  function setAnalysisStatus(text){
    if(!analysisStatusEl) return;
    analysisStatusEl.textContent = String(text || '');
  }

  function setImportNotice(payload){
    if(!importNoticeEl) return;
    if(!payload){
      importNoticeEl.style.display = 'none';
      importNoticeEl.textContent = '';
      importNoticeEl.innerHTML = '';
      return;
    }

    const key = String(payload.trade_key || 'N/A');
    importNoticeEl.style.display = 'block';
    importNoticeEl.innerHTML = `Imported trade • ${key} <button id="wbReimportBtn" class="btn" style="margin-left:8px;">Re-import</button>`;

    const reimportBtn = importNoticeEl.querySelector('#wbReimportBtn');
    if(reimportBtn){
      reimportBtn.addEventListener('click', async () => {
        if(!importedHandoff?.input) return;
        hydrateFromInput(importedHandoff.input);
        await analyze(importedHandoff.input);
      });
    }
  }

  function defaultExpiration(){
    const d = new Date();
    d.setDate(d.getDate() + 14);
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
  }

  function showFieldGroup(el, show){
    if(!el) return;
    el.style.display = show ? '' : 'none';
  }

  function currentStrategy(){
    return String(strategyEl.value || 'put_credit_spread').trim();
  }

  function updateStrategyFields(){
    const strategy = currentStrategy();
    const isSpread = strategy.includes('_put_spread') || strategy.includes('_call_spread');
    const isSingle = strategy === 'long_call' || strategy === 'long_put' || strategy === 'covered_call' || strategy === 'csp';
    const isCondor = strategy === 'iron_condor' || strategy === 'iron_butterfly';
    const isFly = strategy === 'iron_butterfly';

    showFieldGroup(spreadFieldsEl, isSpread);
    showFieldGroup(singleFieldsEl, isSingle);
    showFieldGroup(condorFieldsEl, isCondor);
    showFieldGroup(flyFieldsEl, isFly);

    mutateBtn.disabled = !isSpread;
    renderLegPreviewAndKey();
  }

  function estimateDte(expiration){
    const value = String(expiration || '').trim();
    if(!value || value.toUpperCase() === 'NA') return 'NA';
    const exp = new Date(value + 'T00:00:00');
    if(Number.isNaN(exp.getTime())) return 'NA';
    const now = new Date();
    const ms = exp.getTime() - now.getTime();
    return Math.round(ms / 86400000);
  }

  function buildKeyParts(payload){
    const strategy = String(payload.strategy || '').trim();

    if(strategy.includes('_put_spread') || strategy.includes('_call_spread')){
      return {
        short_strike: payload.short_strike,
        long_strike: payload.long_strike,
      };
    }

    if(strategy === 'iron_condor'){
      return {
        short_strike: `P${normalizeStrike(payload.put_short_strike)}|C${normalizeStrike(payload.call_short_strike)}`,
        long_strike: `P${normalizeStrike(payload.put_long_strike)}|C${normalizeStrike(payload.call_long_strike)}`,
      };
    }

    if(strategy === 'iron_butterfly'){
      const center = payload.center_strike ?? payload.put_short_strike ?? payload.call_short_strike;
      return {
        short_strike: `P${normalizeStrike(center)}|C${normalizeStrike(center)}`,
        long_strike: `P${normalizeStrike(payload.put_long_strike)}|C${normalizeStrike(payload.call_long_strike)}`,
      };
    }

    if(strategy === 'long_call' || strategy === 'long_put'){
      return {
        short_strike: payload.strike,
        long_strike: 'NA',
      };
    }

    if(strategy === 'covered_call'){
      return {
        short_strike: payload.strike,
        long_strike: 'STOCK',
      };
    }

    if(strategy === 'csp'){
      return {
        short_strike: payload.strike,
        long_strike: 'CASH',
      };
    }

    return {
      short_strike: 'NA',
      long_strike: 'NA',
    };
  }

  function asPayload(overrides){
    const strategy = currentStrategy();
    const base = {
      symbol: String(symbolEl.value || '').trim().toUpperCase(),
      expiration: String(expirationEl.value || '').trim() || 'NA',
      strategy,
      contractsMultiplier: Number(multiplierEl.value || 100) || 100,
      short_strike: toNumber(shortStrikeEl?.value),
      long_strike: toNumber(longStrikeEl?.value),
      strike: toNumber(strikeEl?.value),
      put_short_strike: toNumber(putShortEl?.value),
      put_long_strike: toNumber(putLongEl?.value),
      call_short_strike: toNumber(callShortEl?.value),
      call_long_strike: toNumber(callLongEl?.value),
      center_strike: toNumber(centerStrikeEl?.value),
    };

    if(strategy === 'iron_butterfly' && base.center_strike !== null){
      if(base.put_short_strike === null) base.put_short_strike = base.center_strike;
      if(base.call_short_strike === null) base.call_short_strike = base.center_strike;
    }

    const payload = { ...base, ...(overrides || {}) };
    payload.workbench_key_parts = buildKeyParts(payload);
    return payload;
  }

  function computeTradeKey(payload){
    const p = payload || {};
    const parts = p.workbench_key_parts || buildKeyParts(p);
    const dte = estimateDte(p.expiration);

    if(tradeKeyUtil?.tradeKey){
      return tradeKeyUtil.tradeKey({
        underlying: p.symbol,
        expiration: p.expiration,
        spread_type: p.strategy,
        short_strike: parts.short_strike,
        long_strike: parts.long_strike,
        dte,
      });
    }

    return `${p.symbol}|${p.expiration}|${p.strategy}|${normalizeStrike(parts.short_strike)}|${normalizeStrike(parts.long_strike)}|${dte}`;
  }

  function buildLegs(payload){
    const p = payload || {};
    const s = p.strategy;
    const qty = p.contractsMultiplier || 100;

    if(s === 'put_credit_spread'){
      return [
        { side: 'SELL', type: 'PUT', strike: p.short_strike, qty },
        { side: 'BUY', type: 'PUT', strike: p.long_strike, qty },
      ];
    }
    if(s === 'call_credit_spread'){
      return [
        { side: 'SELL', type: 'CALL', strike: p.short_strike, qty },
        { side: 'BUY', type: 'CALL', strike: p.long_strike, qty },
      ];
    }
    if(s === 'put_debit'){
      return [
        { side: 'BUY', type: 'PUT', strike: p.long_strike, qty },
        { side: 'SELL', type: 'PUT', strike: p.short_strike, qty },
      ];
    }
    if(s === 'call_debit'){
      return [
        { side: 'BUY', type: 'CALL', strike: p.long_strike, qty },
        { side: 'SELL', type: 'CALL', strike: p.short_strike, qty },
      ];
    }
    if(s === 'iron_condor'){
      return [
        { side: 'SELL', type: 'PUT', strike: p.put_short_strike, qty },
        { side: 'BUY', type: 'PUT', strike: p.put_long_strike, qty },
        { side: 'SELL', type: 'CALL', strike: p.call_short_strike, qty },
        { side: 'BUY', type: 'CALL', strike: p.call_long_strike, qty },
      ];
    }
    if(s === 'iron_butterfly'){
      const center = p.center_strike ?? p.put_short_strike ?? p.call_short_strike;
      return [
        { side: 'SELL', type: 'PUT', strike: center, qty },
        { side: 'SELL', type: 'CALL', strike: center, qty },
        { side: 'BUY', type: 'PUT', strike: p.put_long_strike, qty },
        { side: 'BUY', type: 'CALL', strike: p.call_long_strike, qty },
      ];
    }
    if(s === 'long_call'){
      return [{ side: 'BUY', type: 'CALL', strike: p.strike, qty }];
    }
    if(s === 'long_put'){
      return [{ side: 'BUY', type: 'PUT', strike: p.strike, qty }];
    }
    if(s === 'covered_call'){
      return [
        { side: 'LONG', type: 'STOCK', strike: 'NA', qty: qty },
        { side: 'SELL', type: 'CALL', strike: p.strike, qty },
      ];
    }
    if(s === 'csp'){
      return [
        { side: 'SELL', type: 'PUT', strike: p.strike, qty },
        { side: 'RESERVE', type: 'CASH', strike: p.strike, qty },
      ];
    }
    return [];
  }

  function renderLegPreviewAndKey(){
    const payload = asPayload();
    const legs = buildLegs(payload);
    const key = computeTradeKey(payload);

    keyPreviewEl.textContent = key || 'N/A';

    if(!legs.length){
      legPreviewEl.innerHTML = '<div class="loading">No leg preview yet.</div>';
      return;
    }

    legPreviewEl.innerHTML = legs.map(leg => `
      <div class="diagnosticRow">
        <span class="diagnosticLabel">${leg.side} ${leg.type} ${normalizeStrike(leg.strike)}</span>
        <span class="detail-value">x${leg.qty ?? 100}</span>
      </div>
    `).join('');
  }

  const fmt    = window.BenTradeUtils.format.num;
  const fmtPct  = window.BenTradeUtils.format.pct;

  function renderTradeCard(trade){
    if(!trade){
      resultEl.innerHTML = '<div class="loading">Analyze or build a template to view strategy output.</div>';
      return;
    }

    const key = String(trade.trade_key || computeTradeKey(asPayload()));
    const strategy = String(trade.strategy_id || trade.strategy || trade.spread_type || currentStrategy());

    if(trade.analysis_state === 'under_construction'){
      resultEl.innerHTML = `
        <div class="trade-card" data-trade-key="${key}">
          <div class="trade-header">
            <div class="trade-type">${trade.symbol || trade.underlying || trade.underlying_symbol || 'N/A'} • ${strategy}</div>
            <div class="trade-strikes">Analysis: under construction</div>
            <div class="trade-strikes">${key}</div>
          </div>
        </div>
      `;
      return;
    }

    const rorClass = Number((trade.computed || {}).return_on_risk || trade.return_on_risk || 0) >= 0.2 ? 'positive' : 'neutral';
    const evRaw = (trade.computed || {}).expected_value ?? trade.expected_value;
    const evClass = Number(evRaw || 0) >= 0 ? 'positive' : 'negative';

    resultEl.innerHTML = `
      <div class="trade-card" data-trade-key="${key}">
        <div class="trade-header">
          <div class="trade-type">${trade.symbol || trade.underlying || trade.underlying_symbol || 'N/A'} • ${strategy}</div>
          <div class="trade-strikes">${normalizeStrike(trade.short_strike)} / ${normalizeStrike(trade.long_strike)} • DTE ${trade.dte ?? 'N/A'}</div>
          <div class="trade-strikes">${key}</div>
        </div>
        <div class="trade-body">
          <div class="metric-grid">
            <div class="metric"><div class="metric-label">Net Credit</div><div class="metric-value">$${fmt(trade.net_credit)}</div></div>
            <div class="metric"><div class="metric-label" data-metric="return_on_risk">Return on Risk</div><div class="metric-value ${rorClass}">${fmtPct(trade.return_on_risk)}</div></div>
            <div class="metric"><div class="metric-label" data-metric="pop">POP</div><div class="metric-value">${fmtPct((trade.computed || {}).pop)}</div></div>
            <div class="metric"><div class="metric-label" data-metric="ev">Expected Value</div><div class="metric-value ${evClass}">$${fmt((trade.computed || {}).expected_value || trade.expected_value)}</div></div>
          </div>
          <div class="trade-actions-row" style="margin-top:12px; gap:8px; display:flex; flex-wrap:wrap;">
            <button class="btn" data-lifecycle="WATCHLIST">Add to Watchlist</button>
            <button class="btn" data-lifecycle="OPEN">Mark Open</button>
            <button class="btn" data-lifecycle="CLOSE">Close</button>
            <button class="btn" data-lifecycle="REJECT">Reject</button>
          </div>
        </div>
      </div>
    `;

    resultEl.querySelectorAll('[data-lifecycle]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const event = String(btn.getAttribute('data-lifecycle') || '').toUpperCase();
        await postLifecycle(event, trade, `workbench_${event.toLowerCase()}`);
      });
    });

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(resultEl);
    }
  }

  function renderHealth(snapshot){
    const rows = Object.entries(snapshot || {}).map(([source, value]) => {
      const status = String(value?.status || 'yellow').toLowerCase();
      const dot = status === 'green' ? 'status-green' : (status === 'red' ? 'status-red' : 'status-yellow');
      const message = value?.message || 'No message';
      return `
        <div class="diagnosticRow">
          <span class="diagnosticLabel">${String(source).toUpperCase()}</span>
          <span class="status-wrap" tabindex="0">
            <span class="status-dot ${dot}"></span>
            <span class="status-tooltip">${message}</span>
          </span>
        </div>
      `;
    }).join('');
    sourceHealthEl.innerHTML = rows || '<div class="loading">No source health yet</div>';
    if(sourceHealthUi?.renderFromSnapshot){
      sourceHealthUi.renderFromSnapshot(snapshot || {});
    }
  }

  function renderSuggestions(items){
    const list = Array.isArray(items) ? items : [];
    if(!list.length){
      suggestionsEl.innerHTML = '<div class="loading">Use Mutate to generate nearby candidates.</div>';
      return;
    }

    suggestionsEl.innerHTML = list.map(item => {
      const trade = item?.trade || {};
      const key = String(trade.trade_key || item.trade_key || '');
      const active = selectedSuggestionKey === key ? 'active' : '';
      return `
        <div class="workbench-suggestion ${active}" data-suggestion-key="${key}">
          <div><strong>${trade.underlying || trade.underlying_symbol || 'N/A'}</strong> ${normalizeStrike(trade.short_strike)} / ${normalizeStrike(trade.long_strike)}</div>
          <div class="workbench-key">${key}</div>
          <div class="workbench-suggestion-actions">
            <button class="btn" data-select-key="${key}">Select</button>
          </div>
        </div>
      `;
    }).join('');

    suggestionsEl.querySelectorAll('[data-select-key]').forEach(btn => {
      btn.addEventListener('click', () => {
        const key = String(btn.getAttribute('data-select-key') || '');
        const selected = list.find(item => String(item?.trade?.trade_key || item?.trade_key) === key);
        if(!selected) return;
        selectedSuggestionKey = key;
        selectedTrade = selected.trade;
        renderTradeCard(selectedTrade);
        renderSuggestions(list);
      });
    });
  }

  function renderScenarioLibrary(){
    if(!Array.isArray(scenarios) || !scenarios.length){
      scenarioListEl.innerHTML = '<div class="loading">No saved scenarios yet.</div>';
      return;
    }

    scenarioListEl.innerHTML = scenarios.map(item => {
      const input = item.input || {};
      const payload = { ...input, workbench_key_parts: input.workbench_key_parts || buildKeyParts(input) };
      const key = String(item.trade_key || computeTradeKey(payload));
      return `
        <div class="workbench-scenario" data-trade-key="${key}">
          <div class="workbench-scenario-name">${item.name || 'Untitled'}</div>
          <div class="workbench-key">${key}</div>
          <div class="workbench-scenario-notes">${item.notes || ''}</div>
          <div class="workbench-suggestion-actions">
            <button class="btn" data-load-id="${item.id}">Load</button>
            <button class="btn" data-delete-id="${item.id}">Delete</button>
          </div>
        </div>
      `;
    }).join('');

    scenarioListEl.querySelectorAll('[data-load-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = String(btn.getAttribute('data-load-id') || '');
        const scenario = scenarios.find(item => String(item.id) === id);
        if(!scenario) return;
        hydrateFromInput(scenario.input || {});
        scenarioNameEl.value = String(scenario.name || '');
        scenarioNotesEl.value = String(scenario.notes || '');
        await analyze();
      });
    });

    scenarioListEl.querySelectorAll('[data-delete-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        try{
          const id = String(btn.getAttribute('data-delete-id') || '');
          if(!id) return;
          await api.deleteWorkbenchScenario(id);
          await loadScenarios();
        }catch(err){
          setError(String(err?.message || err || 'Failed to delete scenario'));
        }
      });
    });
  }

  async function loadScenarios(){
    try{
      const payload = await api.listWorkbenchScenarios();
      scenarios = Array.isArray(payload?.scenarios) ? payload.scenarios : [];
      renderScenarioLibrary();
    }catch(err){
      scenarios = [];
      renderScenarioLibrary();
      setError(String(err?.message || err || 'Failed to load scenarios'));
    }
  }

  function validatePayload(payload){
    const p = payload || {};
    if(!p.symbol) return 'symbol is required';
    if(!p.strategy) return 'strategy is required';
    if(!p.expiration) return 'expiration is required';

    if(p.strategy.includes('_put_spread') || p.strategy.includes('_call_spread')){
      if(!Number.isFinite(Number(p.short_strike))) return 'short strike must be numeric';
      if(!Number.isFinite(Number(p.long_strike))) return 'long strike must be numeric';
      if(Number(p.short_strike) === Number(p.long_strike)) return 'short and long strikes must differ';
      return '';
    }

    if(p.strategy === 'long_call' || p.strategy === 'long_put' || p.strategy === 'covered_call' || p.strategy === 'csp'){
      if(!Number.isFinite(Number(p.strike))) return 'strike must be numeric';
      return '';
    }

    if(p.strategy === 'iron_condor' || p.strategy === 'iron_butterfly'){
      if(!Number.isFinite(Number(p.put_short_strike))) return 'put short strike must be numeric';
      if(!Number.isFinite(Number(p.put_long_strike))) return 'put long strike must be numeric';
      if(!Number.isFinite(Number(p.call_short_strike))) return 'call short strike must be numeric';
      if(!Number.isFinite(Number(p.call_long_strike))) return 'call long strike must be numeric';
      return '';
    }

    return '';
  }

  function canAnalyze(payload){
    const p = payload || {};
    return ANALYZABLE_STRATEGIES.has(String(p.strategy || ''));
  }

  function placeholderTrade(payload){
    const p = payload || asPayload();
    return {
      underlying: p.symbol,
      underlying_symbol: p.symbol,
      strategy: p.strategy,
      spread_type: p.strategy,
      short_strike: p.workbench_key_parts?.short_strike,
      long_strike: p.workbench_key_parts?.long_strike,
      dte: estimateDte(p.expiration),
      trade_key: computeTradeKey(p),
      analysis_state: 'under_construction',
    };
  }

  async function analyze(overrides){
    const payload = asPayload(overrides);
    renderLegPreviewAndKey();

    const validationError = validatePayload(payload);
    if(validationError){
      setError(validationError);
      return null;
    }

    if(!canAnalyze(payload)){
      setError('');
      setAnalysisStatus('Analysis: under construction for this strategy template.');
      selectedTrade = placeholderTrade(payload);
      renderTradeCard(selectedTrade);
      return selectedTrade;
    }

    try{
      setError('');
      setAnalysisStatus('');
      analyzeBtn.disabled = true;
      const response = await api.workbenchAnalyze(payload);
      selectedTrade = response?.trade || null;
      if(selectedTrade && !selectedTrade.trade_key){
        selectedTrade.trade_key = computeTradeKey(payload);
      }
      renderTradeCard(selectedTrade);
      renderHealth(response?.source_health || {});
      return selectedTrade;
    }catch(err){
      setError(String(err?.message || err || 'Analyze failed'));
      return null;
    }finally{
      analyzeBtn.disabled = false;
    }
  }

  async function mutate(){
    const payload = asPayload();
    const validationError = validatePayload(payload);
    if(validationError){
      setError(validationError);
      return;
    }

    if(!canAnalyze(payload)){
      setError('Mutate is only available for spread templates currently.');
      return;
    }

    const strategy = String(payload.strategy || '');
    const step = strategy.includes('_put_') ? -1 : 1;
    const width = Math.abs(Number(payload.short_strike) - Number(payload.long_strike));

    const candidates = [1, 2, 3].map(offset => {
      const shortStrike = Number((Number(payload.short_strike) + (offset * step)).toFixed(2));
      const longStrike = strategy.includes('_put_')
        ? Number((shortStrike - width).toFixed(2))
        : Number((shortStrike + width).toFixed(2));
      return asPayload({ ...payload, short_strike: shortStrike, long_strike: longStrike });
    });

    try{
      setError('');
      mutateBtn.disabled = true;
      const analyzed = await Promise.all(candidates.map(async candidate => {
        const response = await api.workbenchAnalyze(candidate);
        const trade = response?.trade || null;
        if(!trade) return null;
        if(!trade.trade_key){
          trade.trade_key = computeTradeKey(candidate);
        }
        return { trade_key: trade.trade_key, trade };
      }));

      const list = analyzed.filter(Boolean);
      renderSuggestions(list);
      if(list.length && !selectedTrade){
        selectedTrade = list[0].trade;
        selectedSuggestionKey = list[0].trade_key;
        renderTradeCard(selectedTrade);
      }
    }catch(err){
      setError(String(err?.message || err || 'Mutate failed'));
    }finally{
      mutateBtn.disabled = false;
    }
  }

  async function saveScenario(){
    const payload = asPayload();
    const validationError = validatePayload(payload);
    if(validationError){
      setError(validationError);
      return;
    }

    const name = String(scenarioNameEl?.value || '').trim();
    if(!name){
      setError('Scenario name is required');
      return;
    }

    try{
      setError('');
      saveScenarioBtn.disabled = true;
      const scenarioPayload = {
        name,
        input: payload,
        notes: String(scenarioNotesEl?.value || '').trim(),
      };
      await api.saveWorkbenchScenario(scenarioPayload);
      await loadScenarios();
    }catch(err){
      setError(String(err?.message || err || 'Failed to save scenario'));
    }finally{
      saveScenarioBtn.disabled = false;
    }
  }

  function hydrateFromInput(input){
    const payload = input || {};
    symbolEl.value = String(payload.symbol || symbolEl.value || '').toUpperCase();
    const expirationRaw = String(payload.expiration || '').trim();
    expirationEl.value = expirationRaw && expirationRaw !== 'NA' ? expirationRaw : (expirationEl.value || '');
    strategyEl.value = String(payload.strategy || strategyEl.value || 'put_credit_spread');

    if(shortStrikeEl) shortStrikeEl.value = payload.short_strike ?? '';
    if(longStrikeEl) longStrikeEl.value = payload.long_strike ?? '';
    if(strikeEl) strikeEl.value = payload.strike ?? '';
    if(putShortEl) putShortEl.value = payload.put_short_strike ?? '';
    if(putLongEl) putLongEl.value = payload.put_long_strike ?? '';
    if(callShortEl) callShortEl.value = payload.call_short_strike ?? '';
    if(callLongEl) callLongEl.value = payload.call_long_strike ?? '';
    if(centerStrikeEl) centerStrikeEl.value = payload.center_strike ?? '';

    multiplierEl.value = String(payload.contractsMultiplier ?? payload.contracts_multiplier ?? multiplierEl.value ?? 100);
    updateStrategyFields();
  }

  function applyHandoffPrefill(){
    let imported = null;

    try{
      const raw = localStorage.getItem('bentrade_workbench_handoff_v1');
      if(raw){
        const payload = JSON.parse(raw);
        if(payload && typeof payload === 'object' && payload.input && typeof payload.input === 'object'){
          hydrateFromInput(payload.input);
          localStorage.removeItem('bentrade_workbench_handoff_v1');
          imported = payload;
        }
      }
    }catch(_err){
      localStorage.removeItem('bentrade_workbench_handoff_v1');
    }

    if(!imported){
      try{
        const rawLegacy = localStorage.getItem('workbenchPrefillCandidate');
        if(rawLegacy){
          const payloadLegacy = JSON.parse(rawLegacy);
          if(payloadLegacy && typeof payloadLegacy === 'object'){
            const legacyHandoff = {
              from: 'legacy_prefill',
              ts: new Date().toISOString(),
              input: {
                symbol: payloadLegacy.symbol,
                expiration: payloadLegacy.expiration,
                strategy: payloadLegacy.strategy,
                short_strike: payloadLegacy.short_strike,
                long_strike: payloadLegacy.long_strike,
                contractsMultiplier: payloadLegacy.contractsMultiplier,
              },
              trade_key: payloadLegacy.trade_key || payloadLegacy.preview_trade_key || '',
              note: '',
            };
            hydrateFromInput(legacyHandoff.input);
            localStorage.removeItem('workbenchPrefillCandidate');
            imported = legacyHandoff;
          }
        }
      }catch(_legacyErr){
        localStorage.removeItem('workbenchPrefillCandidate');
      }
    }

    importedHandoff = imported;
    setImportNotice(importedHandoff);
    return importedHandoff;
  }

  analyzeBtn.addEventListener('click', () => analyze());
  mutateBtn.addEventListener('click', () => mutate());
  if(saveScenarioBtn){
    saveScenarioBtn.addEventListener('click', () => saveScenario());
  }

  strategyEl.addEventListener('change', updateStrategyFields);
  [symbolEl, expirationEl, shortStrikeEl, longStrikeEl, strikeEl, putShortEl, putLongEl, callShortEl, callLongEl, centerStrikeEl, multiplierEl].forEach(input => {
    if(!input) return;
    input.addEventListener('input', renderLegPreviewAndKey);
  });

  expirationEl.value = expirationEl.value || defaultExpiration();
  updateStrategyFields();

  const imported = applyHandoffPrefill();
  renderTradeCard(null);
  renderSuggestions([]);
  renderHealth({});
  renderLegPreviewAndKey();
  loadScenarios();

  if(imported?.input){
    const importedExpiration = String(imported.input.expiration || '').trim().toUpperCase();
    if(importedExpiration && importedExpiration !== 'NA'){
      analyze(imported.input);
    } else {
      setAnalysisStatus('Imported trade ready: select/confirm expiration, then Analyze.');
    }
  }
};
