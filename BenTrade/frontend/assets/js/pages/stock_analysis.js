window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initStockAnalysis = function initStockAnalysis(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const api = window.BenTradeApi;
  const tradeKeyUtil = window.BenTradeUtils?.tradeKey;
  const sourceHealthUi = window.BenTradeSourceHealth;

  const symbolEl = scope.querySelector('#stockSymbol');
  const rangeEl = scope.querySelector('#stockRange');
  const refreshBtn = scope.querySelector('#stockRefreshBtn');
  const myStocksListEl = scope.querySelector('#stockMyStocksList');
  const errorEl = scope.querySelector('#stockError');
  const summaryGridEl = scope.querySelector('#stockSummaryGrid');
  const summaryPanelEl = scope.querySelector('#stockSummaryPanel');
  const sparklineEl = scope.querySelector('#stockSparkline');
  const recentClosesEl = scope.querySelector('#stockRecentCloses');
  const indicatorsEl = scope.querySelector('#stockIndicators');
  const optionsContextEl = scope.querySelector('#stockOptionsContext');
  const notesEl = scope.querySelector('#stockNotesSystem');
  const notesMountEl = scope.querySelector('#stockNotesMount');
  const debugBodyEl = scope.querySelector('#stockDebugBody');
  const summaryModeBtn = scope.querySelector('#stockSummaryModeBtn');
  const scannerModeBtn = scope.querySelector('#stockScannerModeBtn');
  const runScanBtn = scope.querySelector('#stockRunScanBtn');
  const scannerResultsEl = scope.querySelector('#stockScannerResults');
  const addSymbolEl = scope.querySelector('#stockAddSymbol');
  const addBtn = scope.querySelector('#stockAddBtn');

  const openScannerBtn = scope.querySelector('#stockOpenScannerBtn');
  const sendWorkbenchBtn = scope.querySelector('#stockSendWorkbenchBtn');
  const candidatePreviewEl = scope.querySelector('#stockCandidatePreview');

  if(!symbolEl || !rangeEl || !refreshBtn || !myStocksListEl || !summaryGridEl || !summaryPanelEl || !sparklineEl || !recentClosesEl || !indicatorsEl || !optionsContextEl || !notesEl || !notesMountEl || !debugBodyEl || !summaryModeBtn || !scannerModeBtn || !runScanBtn || !scannerResultsEl || !openScannerBtn || !sendWorkbenchBtn || !candidatePreviewEl || !addSymbolEl || !addBtn){
    return;
  }

  const notesController = window.BenTradeNotes?.attachNotes?.(notesMountEl, () => {
    const symbol = normalizeSymbol(symbolEl.value) || 'SPY';
    return `notes:stock:${symbol}`;
  });

  let currentPayload = null;
  let candidate = null;
  let scanPayload = null;
  let lastStatusCode = null;
  let lastRequestUrl = '';
  let currentMode = 'summary';
  const STOCK_LIST_KEY = 'bentrade_stock_list_v1';
  const PRESET_SYMBOLS = ['SPY', 'QQQ', 'IWM', 'AAPL', 'MSFT'];
  let watchlist = ['SPY', 'QQQ', 'IWM', 'AAPL', 'MSFT'];

  function uniqueSymbols(list){
    const out = [];
    const seen = new Set();
    (Array.isArray(list) ? list : []).forEach((item) => {
      const symbol = normalizeSymbol(item);
      if(!symbol || seen.has(symbol)) return;
      seen.add(symbol);
      out.push(symbol);
    });
    return out;
  }

  function persistStockList(){
    try{
      localStorage.setItem(STOCK_LIST_KEY, JSON.stringify(uniqueSymbols(watchlist)));
    }catch(_err){
    }
  }

  function readLocalStockList(){
    try{
      const raw = localStorage.getItem(STOCK_LIST_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      return uniqueSymbols(arr);
    }catch(_err){
      return [];
    }
  }

  function normalizeSymbol(raw){
    const symbol = String(raw || '').trim().toUpperCase();
    return symbol.replace(/[^A-Z0-9.-]/g, '').slice(0, 12);
  }

  try{
    const prefill = normalizeSymbol(localStorage.getItem('bentrade_selected_symbol'));
    if(prefill){
      symbolEl.value = prefill;
      localStorage.removeItem('bentrade_selected_symbol');
    }
  }catch(_err){
    localStorage.removeItem('bentrade_selected_symbol');
  }

  function bindMyStocksEvents(){
    if(!myStocksListEl) return;
    myStocksListEl.querySelectorAll('[data-symbol]').forEach(btn => {
      btn.addEventListener('click', () => {
        const sym = String(btn.getAttribute('data-symbol') || '').toUpperCase();
        if(!sym) return;
        symbolEl.value = sym;
        refresh();
      });
    });
  }

  function renderMyStocks(){
    if(!myStocksListEl) return;
    const activeSymbol = normalizeSymbol(symbolEl.value);
    const allSymbols = uniqueSymbols([...PRESET_SYMBOLS, ...watchlist]);
    watchlist = allSymbols;
    myStocksListEl.innerHTML = allSymbols
      .map((symbol) => `<button class="btn qtButton qtPill ${symbol === activeSymbol ? 'stock-chip-active' : ''}" data-symbol="${symbol}">${symbol}</button>`)
      .join('');
    bindMyStocksEvents();
    persistStockList();
  }

  async function loadWatchlist(){
    const localSymbols = readLocalStockList();
    watchlist = uniqueSymbols([...PRESET_SYMBOLS, ...localSymbols]);
    renderMyStocks();

    try{
      if(!api?.getStockWatchlist){
        renderMyStocks();
        return;
      }
      const payload = await api.getStockWatchlist();
      const symbols = Array.isArray(payload?.symbols) ? payload.symbols : [];
      const remoteSymbols = symbols.length ? symbols.map(normalizeSymbol).filter(Boolean) : [];
      watchlist = uniqueSymbols([...PRESET_SYMBOLS, ...watchlist, ...remoteSymbols]);
      renderMyStocks();
    }catch(err){
      renderMyStocks();
      console.warn('[stock-analysis] watchlist load failed', err);
    }
  }

  async function addWatchSymbol(){
    const symbol = normalizeSymbol(addSymbolEl.value);
    if(!symbol){
      setError('Enter a valid stock symbol.');
      return;
    }

    try{
      setError('');
      addBtn.disabled = true;
      if(api?.addStockWatchlist){
        const payload = await api.addStockWatchlist(symbol);
        const symbols = Array.isArray(payload?.symbols) ? payload.symbols : [];
        const remoteSymbols = symbols.length ? symbols.map(normalizeSymbol).filter(Boolean) : [];
        watchlist = uniqueSymbols([...PRESET_SYMBOLS, ...watchlist, ...remoteSymbols]);
      }else{
        watchlist = uniqueSymbols([...watchlist, symbol]);
      }
      addSymbolEl.value = '';
      renderMyStocks();
      symbolEl.value = symbol;
      notesController?.reload?.();
      await refresh();
    }catch(err){
      setError(String(err?.message || err || 'Failed to add stock'));
    }finally{
      addBtn.disabled = false;
    }
  }

  function strategySuggestion(result){
    const trend = String(result?.signals?.trend || 'range');
    const ivrv = Number(result?.signals?.iv_rv_ratio);
    const isIvRich = Number.isFinite(ivrv) && ivrv > 1.2;

    if(trend === 'up' && isIvRich) return 'put_credit_spread';
    if(trend === 'down' && isIvRich) return 'call_credit_spread';
    if(trend === 'up') return 'call_debit';
    if(trend === 'down') return 'put_debit';
    return isIvRich ? 'put_credit_spread' : 'call_debit';
  }

  function suggestionTradeKey(result){
    const symbol = String(result?.symbol || '').toUpperCase();
    const strategy = strategySuggestion(result);
    if(window.BenTradeUtils?.tradeKey?.tradeKey){
      return window.BenTradeUtils.tradeKey.tradeKey({
        underlying: symbol,
        expiration: 'NA',
        spread_type: strategy,
        short_strike: 'NA',
        long_strike: 'NA',
        dte: 'NA',
      });
    }
    return `${symbol}|NA|${strategy}|NA|NA|NA`;
  }

  function setMode(mode){
    currentMode = mode === 'scanner' ? 'scanner' : 'summary';
    const scannerVisible = currentMode === 'scanner';
    summaryPanelEl.style.display = scannerVisible ? 'none' : '';
    runScanBtn.closest('.stock-card').style.display = scannerVisible ? '' : 'none';
    summaryModeBtn.style.opacity = scannerVisible ? '0.75' : '1';
    scannerModeBtn.style.opacity = scannerVisible ? '1' : '0.75';
  }

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

  function fmt(value, decimals = 2){
    if(value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A';
    return Number(value).toFixed(decimals);
  }

  function fmtPct(value){
    if(value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A';
    const v = Number(value) * 100;
    const sign = v >= 0 ? '+' : '';
    return `${sign}${v.toFixed(2)}%`;
  }

  function normalizeStrike(value){
    if(tradeKeyUtil?.normalizeStrike){
      return tradeKeyUtil.normalizeStrike(value);
    }
    if(value === null || value === undefined || value === '') return 'NA';
    const n = Number(value);
    return Number.isFinite(n) ? String(n).replace(/\.0+$/, '') : String(value);
  }

  function roundToHalf(value){
    return Math.round(Number(value || 0) * 2) / 2;
  }

  function defaultFutureDate(days){
    const d = new Date();
    d.setDate(d.getDate() + days);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  function buildCandidate(payload){
    if(!payload) return null;
    const price = Number(payload?.price?.last);
    const em = Number(payload?.options_context?.expected_move);

    if(!Number.isFinite(price) || !Number.isFinite(em) || em <= 0){
      return null;
    }

    const expiration = String(payload?.options_context?.expiration || defaultFutureDate(14));
    const shortStrike = roundToHalf(price - (em * 0.6));
    const width = Math.max(1, roundToHalf(Math.max(1, em * 0.3)));
    const longStrike = roundToHalf(shortStrike - width);
    const dte = Number(payload?.options_context?.dte);

    const out = {
      symbol: String(payload.symbol || '').toUpperCase(),
      expiration,
      strategy: 'put_credit_spread',
      short_strike: shortStrike,
      long_strike: longStrike,
      contractsMultiplier: 100,
    };

    const previewKey = tradeKeyUtil?.tradeKey
      ? tradeKeyUtil.tradeKey({
          underlying: out.symbol,
          expiration: out.expiration,
          spread_type: out.strategy,
          short_strike: out.short_strike,
          long_strike: out.long_strike,
          dte: Number.isFinite(dte) ? dte : 'NA',
        })
      : `${out.symbol}|${out.expiration}|${out.strategy}|${normalizeStrike(out.short_strike)}|${normalizeStrike(out.long_strike)}|${Number.isFinite(dte) ? dte : 'NA'}`;

    return { ...out, preview_trade_key: previewKey };
  }

  function renderCandidate(){
    if(!candidate){
      candidatePreviewEl.innerHTML = '<div class="loading">No candidate suggestion available yet.</div>';
      return;
    }

    candidatePreviewEl.innerHTML = `
      <div><span class="qtPill">${candidate.symbol}</span> ${normalizeStrike(candidate.short_strike)} / ${normalizeStrike(candidate.long_strike)} • ${candidate.expiration}</div>
      <div class="workbench-key">${candidate.preview_trade_key}</div>
    `;
  }

  function renderSummary(payload){
    const p = payload?.price || {};
    const summarySymbol = String(payload?.symbol || 'N/A').toUpperCase();
    summaryGridEl.innerHTML = `
      <div class="statTile"><div class="statLabel">Symbol</div><div class="statValue"><span class="qtPill">${summarySymbol}</span></div></div>
      <div class="statTile"><div class="statLabel" data-metric="mark">Last</div><div class="statValue">$${fmt(p.last)}</div></div>
      <div class="statTile"><div class="statLabel">Change</div><div class="statValue">$${fmt(p.change)}</div></div>
      <div class="statTile"><div class="statLabel">Change %</div><div class="statValue">${fmtPct(p.change_pct)}</div></div>
      <div class="statTile"><div class="statLabel">Range High</div><div class="statValue">$${fmt(p.range_high)}</div></div>
      <div class="statTile"><div class="statLabel">Range Low</div><div class="statValue">$${fmt(p.range_low)}</div></div>
    `;
    if(window.attachMetricTooltips){
      window.attachMetricTooltips(summaryGridEl);
    }
  }

  function renderSparkline(history){
    const points = Array.isArray(history) ? history.map(row => Number(row?.close)).filter(v => Number.isFinite(v)) : [];
    if(!points.length){
      sparklineEl.innerHTML = '';
      recentClosesEl.innerHTML = '<div class="stock-note">Recent closes unavailable.</div>';
      return;
    }

    const width = 800;
    const height = 220;
    const margin = { top: 14, right: 16, bottom: 34, left: 62 };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;

    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = Math.max(max - min, 0.0001);

    const xFor = (index) => margin.left + (index / Math.max(points.length - 1, 1)) * plotWidth;
    const yFor = (value) => margin.top + (1 - ((value - min) / span)) * plotHeight;

    const path = points.map((value, index) => {
      const x = xFor(index);
      const y = yFor(value);
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    }).join(' ');

    const yTickCount = 5;
    const yTicks = Array.from({ length: yTickCount }, (_, idx) => {
      const ratio = idx / Math.max(yTickCount - 1, 1);
      const value = max - (span * ratio);
      return {
        value,
        y: yFor(value),
      };
    });

    const tickSet = new Set([
      0,
      Math.floor((points.length - 1) / 3),
      Math.floor(((points.length - 1) * 2) / 3),
      points.length - 1,
    ]);
    const xTicks = Array.from(tickSet).sort((a, b) => a - b);

    const rangeDaysByKey = {
      '1mo': 30,
      '3mo': 90,
      '6mo': 180,
      '1y': 365,
    };
    const selectedRange = String(rangeEl?.value || '6mo').toLowerCase();
    const rangeDays = rangeDaysByKey[selectedRange] || Math.max(points.length, 30);
    const endDate = new Date();
    const startDate = new Date(endDate.getTime() - (rangeDays * 24 * 60 * 60 * 1000));

    const dateForIndex = (index) => {
      const t = points.length <= 1 ? 0 : index / (points.length - 1);
      return new Date(startDate.getTime() + ((endDate.getTime() - startDate.getTime()) * t));
    };

    const fmtDate = (date) => {
      if(!(date instanceof Date) || Number.isNaN(date.getTime())) return '';
      const month = date.toLocaleString('en-US', { month: 'short' });
      const day = date.getDate();
      return day <= 3 ? month : `${month} ${day}`;
    };

    const yGrid = yTicks.map((tick) => `
      <line x1="${margin.left}" y1="${tick.y.toFixed(2)}" x2="${(width - margin.right).toFixed(2)}" y2="${tick.y.toFixed(2)}" stroke="rgba(0,234,255,0.12)" stroke-width="1"></line>
    `).join('');

    const yLabels = yTicks.map((tick) => `
      <text x="${(margin.left - 8).toFixed(2)}" y="${(tick.y + 3).toFixed(2)}" text-anchor="end" fill="rgba(215,251,255,0.85)" font-size="10">${Number(tick.value).toFixed(2)}</text>
    `).join('');

    const xTickLines = xTicks.map((index) => {
      const x = xFor(index);
      return `<line x1="${x.toFixed(2)}" y1="${margin.top}" x2="${x.toFixed(2)}" y2="${(height - margin.bottom).toFixed(2)}" stroke="rgba(0,234,255,0.08)" stroke-width="1"></line>`;
    }).join('');

    const xLabels = xTicks.map((index) => {
      const x = xFor(index);
      const dateLabel = fmtDate(dateForIndex(index));
      return `<text x="${x.toFixed(2)}" y="${(height - 10).toFixed(2)}" text-anchor="middle" fill="rgba(215,251,255,0.82)" font-size="10">${dateLabel}</text>`;
    }).join('');

    sparklineEl.innerHTML = `
      ${yGrid}
      ${xTickLines}
      <line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${(height - margin.bottom).toFixed(2)}" stroke="rgba(0,234,255,0.45)" stroke-width="1"></line>
      <line x1="${margin.left}" y1="${(height - margin.bottom).toFixed(2)}" x2="${(width - margin.right).toFixed(2)}" y2="${(height - margin.bottom).toFixed(2)}" stroke="rgba(0,234,255,0.45)" stroke-width="1"></line>
      ${yLabels}
      ${xLabels}
      <path d="${path}" fill="none" stroke="rgba(0,234,255,0.95)" stroke-width="3"></path>
    `;

    const recent = points.slice(-10).map(value => Number(value).toFixed(2));
    recentClosesEl.innerHTML = `<div class="stock-note">Recent closes: ${recent.join(', ')}</div>`;
  }

  function renderDebug(payload, errorText){
    const notes = Array.isArray(payload?.notes) ? payload.notes : [];
    const err = payload?.error?.message || payload?.detail || errorText || '';

    debugBodyEl.innerHTML = [
      `<div class="stock-note"><strong>Request:</strong> ${lastRequestUrl || 'N/A'}</div>`,
      `<div class="stock-note"><strong>Status:</strong> ${lastStatusCode ?? 'N/A'}</div>`,
      `<div class="stock-note"><strong>Notes:</strong> ${notes.length ? notes.map(item => String(item || '')).join(' | ') : 'None'}</div>`,
      `<div class="stock-note"><strong>Error:</strong> ${err || 'None'}</div>`,
    ].join('');
  }

  function renderScanResults(payload){
    const list = Array.isArray(payload?.results) ? payload.results : [];
    if(!list.length){
      scannerResultsEl.innerHTML = '<div class="stock-note">No scan results yet.</div>';
      return;
    }

    scannerResultsEl.innerHTML = list.map((item, idx) => {
      const symbol = String(item?.symbol || 'N/A').toUpperCase();
      const score = Number(item?.scanner_score || 0).toFixed(2);
      const trend = String(item?.signals?.trend || 'range');
      const ivrv = item?.signals?.iv_rv_ratio;
      const suggested = strategySuggestion(item);
      const keyPreview = suggestionTradeKey(item);
      const reasons = Array.isArray(item?.reasons) ? item.reasons : [];

      return `
        <div class="stock-card" data-scan-symbol="${symbol}" style="margin-bottom:8px;">
          <div class="stock-note"><strong>#${idx + 1}</strong> <span class="qtPill">${symbol}</span> • Score ${score}</div>
          <div class="stock-note">Trend: ${trend} • <span data-metric="rsi_14">RSI</span>: ${fmt(item?.signals?.rsi_14)} • <span data-metric="realized_vol_20d">RV20</span>: ${fmtPct(item?.signals?.rv_20d)} • <span data-metric="iv_rv_ratio">IV/RV</span>: ${ivrv === null || ivrv === undefined ? 'N/A' : Number(ivrv).toFixed(2)}</div>
          <div class="stock-note">Suggested: <strong>${suggested}</strong></div>
          <div class="workbench-key">${keyPreview}</div>
          <div class="stock-note">Why: ${reasons.join(' | ') || 'N/A'}</div>
          <div class="workbench-actions" style="margin-top:6px;">
            <button class="btn qtButton" data-action="view-summary" data-symbol="${symbol}">View Summary</button>
            <button class="btn qtButton" data-action="send-credit" data-symbol="${symbol}">Send to Credit Spread Analysis</button>
            <button class="btn qtButton" data-action="send-workbench" data-symbol="${symbol}" data-strategy="${suggested}" data-key="${keyPreview}">Send to Workbench</button>
          </div>
        </div>
      `;
    }).join('');

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(scannerResultsEl);
    }

    scannerResultsEl.querySelectorAll('button[data-action]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const action = String(btn.getAttribute('data-action') || '');
        const symbol = String(btn.getAttribute('data-symbol') || '').toUpperCase();
        if(!symbol) return;

        if(action === 'view-summary'){
          symbolEl.value = symbol;
          setMode('summary');
          await refresh();
          return;
        }

        if(action === 'send-credit'){
          localStorage.setItem('creditSpreadSelectedUnderlying', symbol);
          location.hash = '#/credit-spread';
          return;
        }

        if(action === 'send-workbench'){
          const strategy = String(btn.getAttribute('data-strategy') || 'put_credit_spread');
          const key = String(btn.getAttribute('data-key') || `${symbol}|NA|${strategy}|NA|NA|NA`);
          const payloadHandoff = {
            from: 'stock_scanner',
            ts: new Date().toISOString(),
            input: {
              symbol,
              expiration: 'NA',
              strategy,
              short_strike: null,
              long_strike: null,
              contractsMultiplier: 100,
            },
            trade_key: key,
            note: `Scanner suggestion from trend/IVRV for ${symbol}`,
          };
          localStorage.setItem('bentrade_workbench_handoff_v1', JSON.stringify(payloadHandoff));
          location.hash = '#/trade-testing';
        }
      });
    });
  }

  async function runScan(){
    const previousText = runScanBtn.textContent;
    try{
      setError('');
      runScanBtn.disabled = true;
      runScanBtn.textContent = 'Scanning...';
      lastRequestUrl = '/api/stock/scan?universe=default';

      const response = await fetch(lastRequestUrl, { method: 'GET' });
      lastStatusCode = response.status;
      const payload = await response.json().catch(() => ({}));

      if(!response.ok){
        const message = payload?.error?.message || payload?.detail || `Scan failed (${response.status})`;
        renderDebug(payload, message);
        throw new Error(message);
      }

      scanPayload = payload;
      renderScanResults(scanPayload);
      renderSourceHealth(scanPayload?.source_health || {});
      window.BenTradeSourceHealthStore?.fetchSourceHealth?.({ force: true }).catch(() => {});
      renderNotes(scanPayload?.notes || []);
      renderDebug(scanPayload, '');
    }catch(err){
      const message = String(err?.message || err || 'Failed to run scanner');
      setError(message);
      renderDebug(scanPayload || {}, message);
    }finally{
      runScanBtn.disabled = false;
      runScanBtn.textContent = previousText || 'Run Scan';
    }
  }

  function renderIndicators(indicators){
    const i = indicators || {};
    indicatorsEl.innerHTML = `
      <div class="statTile"><div class="statLabel" data-metric="rsi_14">RSI(14)</div><div class="statValue">${fmt(i.rsi14)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="sma_20">SMA20</div><div class="statValue">$${fmt(i.sma20)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="sma_50">SMA50</div><div class="statValue">$${fmt(i.sma50)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="ema_20">EMA20</div><div class="statValue">$${fmt(i.ema20)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="realized_vol_20d">Realized Vol</div><div class="statValue">${fmtPct(i.realized_vol)}</div></div>
    `;
    if(window.attachMetricTooltips){
      window.attachMetricTooltips(indicatorsEl);
    }
  }

  function renderOptionsContext(ctx){
    const c = ctx || {};
    optionsContextEl.innerHTML = `
      <div class="statTile"><div class="statLabel">Expiration</div><div class="statValue">${c.expiration || 'N/A'}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="dte">DTE</div><div class="statValue">${c.dte ?? 'N/A'}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="iv">ATM IV</div><div class="statValue">${fmtPct(c.iv)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="expected_move_1w">Expected Move</div><div class="statValue">$${fmt(c.expected_move)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="iv_rv_ratio">IV/RV</div><div class="statValue">${fmt(c.iv_rv, 3)}</div></div>
      <div class="statTile"><div class="statLabel">VIX</div><div class="statValue">${fmt(c.vix)}</div></div>
    `;
    if(window.attachMetricTooltips){
      window.attachMetricTooltips(optionsContextEl);
    }
  }

  function renderSourceHealth(snapshot){
    if(sourceHealthUi?.renderFromSnapshot){
      sourceHealthUi.renderFromSnapshot(snapshot || {});
    }
  }

  function renderNotes(notes){
    const list = Array.isArray(notes) ? notes : [];
    if(!list.length){
      notesEl.innerHTML = '<div class="loading">No system notes.</div>';
      return;
    }
    notesEl.innerHTML = list.map(item => `<div class="stock-note">• ${String(item || '')}</div>`).join('');
  }

  async function refresh(){
    const previousText = refreshBtn.textContent;
    try{
      setError('');
      refreshBtn.disabled = true;
      refreshBtn.textContent = 'Refreshing...';
      const symbol = String(symbolEl.value || '').trim().toUpperCase() || 'SPY';
      const range = String(rangeEl.value || '6mo');
      symbolEl.value = symbol;
      renderMyStocks();
      notesController?.reload?.();

      const sym = encodeURIComponent(symbol);
      const rng = encodeURIComponent(range);
      lastRequestUrl = `/api/stock/summary?symbol=${sym}&range=${rng}`;

      const response = await fetch(lastRequestUrl, { method: 'GET' });
      lastStatusCode = response.status;
      const payload = await response.json().catch(() => ({}));

      if(!response.ok){
        const message = payload?.error?.message || payload?.detail || `Request failed (${response.status})`;
        renderDebug(payload, message);
        throw new Error(message);
      }

      currentPayload = payload;
      candidate = buildCandidate(payload);

      renderSummary(payload);
      renderSparkline(payload?.history || []);
      renderIndicators(payload?.indicators || {});
      renderOptionsContext(payload?.options_context || {});
      renderSourceHealth(payload?.source_health || {});
      window.BenTradeSourceHealthStore?.fetchSourceHealth?.({ force: true }).catch(() => {});
      renderNotes(payload?.notes || []);
      renderCandidate();
      renderDebug(payload, '');
      renderMyStocks();
    }catch(err){
      const message = String(err?.message || err || 'Failed to load stock summary');
      setError(message);
      renderDebug(currentPayload || {}, message);
    }finally{
      refreshBtn.disabled = false;
      refreshBtn.textContent = previousText || 'Refresh';
    }
  }

  function openInScanner(){
    const symbol = String(symbolEl.value || '').trim().toUpperCase();
    if(!symbol) return;
    localStorage.setItem('creditSpreadSelectedUnderlying', symbol);
    location.hash = '#/credit-spread';
  }

  function sendCandidateToWorkbench(){
    if(!candidate){
      setError('No candidate available yet. Refresh summary first.');
      return;
    }

    const handoff = {
      symbol: candidate.symbol,
      expiration: candidate.expiration,
      strategy: candidate.strategy,
      short_strike: candidate.short_strike,
      long_strike: candidate.long_strike,
      contractsMultiplier: candidate.contractsMultiplier,
      preview_trade_key: candidate.preview_trade_key,
      created_at: new Date().toISOString(),
    };

    localStorage.setItem('workbenchPrefillCandidate', JSON.stringify(handoff));
    location.hash = '#/trade-testing';
  }

  refreshBtn.addEventListener('click', refresh);
  summaryModeBtn.addEventListener('click', () => setMode('summary'));
  scannerModeBtn.addEventListener('click', () => setMode('scanner'));
  runScanBtn.addEventListener('click', runScan);
  rangeEl.addEventListener('change', refresh);
  symbolEl.addEventListener('keydown', (event) => {
    if(event.key === 'Enter'){
      event.preventDefault();
      refresh();
    }
  });

  addBtn.addEventListener('click', () => addWatchSymbol());
  addSymbolEl.addEventListener('keydown', (event) => {
    if(event.key === 'Enter'){
      event.preventDefault();
      addWatchSymbol();
    }
  });

  openScannerBtn.addEventListener('click', openInScanner);
  sendWorkbenchBtn.addEventListener('click', sendCandidateToWorkbench);

  setMode('summary');
  renderScanResults({ results: [] });
  renderMyStocks();
  loadWatchlist().finally(() => refresh());
};
