window.BenTradeHomeCacheStore = (function(){
  const STORAGE_KEY = 'bentrade_home_cache_v1';
  const FRESH_TTL_MS = 60 * 1000;
  const MAX_STALE_MS = 15 * 60 * 1000;
  const REFRESH_INTERVAL_MS = 90 * 1000;

  const INDEX_SYMBOLS = ['SPY', 'QQQ', 'IWM', 'DIA'];
  const SECTOR_SYMBOLS = ['XLF', 'XLK', 'XLE', 'XLY', 'XLP', 'XLV', 'XLI', 'XLB', 'XLRE', 'XLU', 'XLC'];
  const STRATEGY_SOURCES = [
    { id: 'credit_spread', label: 'Credit Spread', route: '#/credit-spread' },
    { id: 'debit_spreads', label: 'Debit Spreads', route: '#/debit-spreads' },
    { id: 'iron_condor', label: 'Iron Condor', route: '#/iron-condor' },
    { id: 'butterflies', label: 'Butterflies', route: '#/butterflies' },
  ];

  let renderer = null;
  let inMemory = null;
  let inFlight = null;

  function nowIso(){
    return new Date().toISOString();
  }

  function safeParse(raw){
    try{
      return JSON.parse(raw);
    }catch(_err){
      return null;
    }
  }

  function loadFromStorage(){
    const parsed = safeParse(localStorage.getItem(STORAGE_KEY) || '');
    if(parsed && typeof parsed === 'object') return parsed;
    return null;
  }

  function persist(snapshot){
    try{
      localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
    }catch(_err){
    }
  }

  function toNumber(value){
    if(value === null || value === undefined || value === '') return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function deriveRor(raw){
    const direct = toNumber(raw?.return_on_risk ?? raw?.ror);
    if(direct !== null) return direct;

    const maxProfit = toNumber(raw?.max_profit_per_share ?? raw?.max_profit ?? raw?.max_profit_per_contract);
    const maxLoss = toNumber(raw?.max_loss_per_share ?? raw?.max_loss ?? raw?.max_loss_per_contract);
    if(maxProfit !== null && maxLoss !== null && maxLoss > 0){
      return maxProfit / maxLoss;
    }
    return null;
  }

  function normalizeTradeIdea(row, source){
    const raw = (row && typeof row === 'object') ? row : {};
    const symbol = String(row?.underlying || row?.underlying_symbol || row?.symbol || '').trim().toUpperCase();
    const score = toNumber(row?.composite_score ?? row?.trade_quality_score ?? row?.scanner_score ?? row?.score) ?? 0;
    const ev = toNumber(raw?.ev_per_share ?? raw?.expected_value ?? raw?.ev ?? raw?.edge ?? raw?.ev_to_risk);
    const pop = toNumber(raw?.p_win_used ?? raw?.pop_delta_approx ?? raw?.probability_of_profit ?? raw?.pop);
    const ror = deriveRor(raw);
    const sourceType = String(source?.type || '').toLowerCase() === 'stock' ? 'stock' : 'options';
    const modelEvaluation = raw?.model_evaluation && typeof raw.model_evaluation === 'object' ? raw.model_evaluation : null;
    const strategy = String(row?.spread_type || row?.strategy || row?.recommended_strategy || source?.label || 'idea');
    const recommendation = modelEvaluation ? String(modelEvaluation?.recommendation || 'Not run') : 'Not run';

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
      sourceType,
      model: modelEvaluation,
      key_metrics: {
        price: toNumber(raw?.price),
        rsi14: toNumber(raw?.metrics?.rsi14 ?? raw?.signals?.rsi_14 ?? raw?.rsi14),
        ema20: toNumber(raw?.metrics?.ema20 ?? raw?.ema20),
        iv_rv_ratio: toNumber(raw?.metrics?.iv_rv_ratio ?? raw?.signals?.iv_rv_ratio ?? raw?.iv_rv_ratio),
        trend: String(raw?.trend || '').trim().toLowerCase() || null,
      },
      trade: row,
    };
  }

  async function fetchLatestStrategyIdeas(api){
    const allIdeas = [];

    await Promise.all(STRATEGY_SOURCES.map(async (source) => {
      try{
        const files = await api.listStrategyReports(source.id);
        const report = Array.isArray(files) && files.length ? String(files[0]) : null;
        if(!report) return;
        const payload = await api.getStrategyReport(source.id, report);
        const trades = Array.isArray(payload?.trades) ? payload.trades : [];
        trades.slice(0, 8).forEach((trade) => allIdeas.push(normalizeTradeIdea(trade, source)));
      }catch(_err){
      }
    }));

    try{
      const scannerPayload = await api.getStockScanner();
      const candidates = Array.isArray(scannerPayload?.candidates) ? scannerPayload.candidates : [];
      candidates.slice(0, 8).forEach((idea) => {
        allIdeas.push(normalizeTradeIdea(idea, { label: 'Stock Scanner', route: '#/stock-scanner', type: 'stock' }));
      });
    }catch(_err){
    }

    allIdeas.sort((a, b) => (b.score || 0) - (a.score || 0));
    return allIdeas;
  }

  function mergeData(previousData, nextData){
    return {
      ...(previousData || {}),
      ...(nextData || {}),
    };
  }

  function errorParts(err){
    const status = Number(err?.status ?? err?.statusCode ?? err?.response?.status);
    const message = String(err?.message || err?.detail || err || 'request failed');
    return {
      status: Number.isFinite(status) ? status : 'n/a',
      message,
    };
  }

  function logLine(logFn, text){
    if(typeof logFn === 'function'){
      logFn(String(text || ''));
    }
  }

  async function refreshCore({ force, logFn } = {}){
    if(inFlight && !force) return inFlight;

    const api = window.BenTradeApi;
    const previous = getSnapshot();
    const previousData = (previous && typeof previous.data === 'object') ? previous.data : {};

    const doRefresh = (async () => {
      const errors = [];
      const updates = {};
      let successfulStages = 0;

      async function runStage(displayName, key, fn){
        logLine(logFn, `Fetching ${displayName}...`);
        try{
          const value = await fn();
          if(key){
            updates[key] = value;
          }
          successfulStages += 1;
          logLine(logFn, `Loaded: ${displayName}`);
          return value;
        }catch(err){
          const info = errorParts(err);
          errors.push(`${String(key || displayName)}: ${info.message}`);
          logLine(logFn, `Error: ${displayName} ${info.status} ${info.message}`);
          return null;
        }
      }

      await runStage('regime', 'regime', () => api.getRegime());
      await runStage('playbook', 'playbook', () => api.getPlaybook());
      await runStage('SPY summary', 'spy', () => api.getStockSummary('SPY', '6mo'));
      await runStage('VIX', 'vix', () => api.getStockSummary('VIXY', '3mo'));

      await runStage('sectors', null, async () => {
        const symbolList = [...INDEX_SYMBOLS, ...SECTOR_SYMBOLS];
        const summaryEntries = await Promise.allSettled(symbolList.map(async (symbol) => {
          const payload = await api.getStockSummary(symbol, '6mo');
          return [symbol, payload];
        }));

        const summaryBySymbol = {};
        summaryEntries.forEach((item) => {
          if(item.status === 'fulfilled'){
            const [symbol, payload] = item.value;
            summaryBySymbol[symbol] = payload;
            return;
          }
          const info = errorParts(item.reason);
          errors.push(`summary: ${info.message}`);
        });

        updates.indexSummaries = Object.fromEntries(INDEX_SYMBOLS.map((symbol) => [
          symbol,
          summaryBySymbol[symbol] || previousData?.indexSummaries?.[symbol] || null,
        ]));
        updates.sectors = Object.fromEntries(SECTOR_SYMBOLS.map((symbol) => [
          symbol,
          summaryBySymbol[symbol] || previousData?.sectors?.[symbol] || null,
        ]));
      });

      await runStage('top picks', 'topPicks', () => api.getTopRecommendations());
      await runStage('portfolio risk', 'portfolioRisk', () => api.getPortfolioRiskMatrix());

      await runStage('source health', 'sourceHealth', async () => {
        const response = await fetch('/api/health/sources', { method: 'GET' });
        const payload = await response.json().catch(() => ({}));
        if(!response.ok){
          const message = String(payload?.detail || payload?.message || `HTTP ${response.status}`);
          const err = new Error(message);
          err.status = response.status;
          throw err;
        }
        return payload;
      });

      await Promise.allSettled([
        (async () => {
          try{
            updates.signalsUniverse = await api.getSignalsUniverse('default', '6mo');
          }catch(_err){
          }
        })(),
        (async () => {
          try{
            updates.macro = await api.getMacroIndicators();
          }catch(_err){
          }
        })(),
        (async () => {
          try{
            updates.activeTrades = await api.getActiveTrades();
          }catch(_err){
          }
        })(),
        (async () => {
          try{
            updates.opportunities = await fetchLatestStrategyIdeas(api);
          }catch(_err){
          }
        })(),
      ]);

      updates.sessionStats = window.BenTradeSessionStatsStore?.getState?.() || previousData?.sessionStats || {
        total_candidates: 0,
        accepted_trades: 0,
        by_module: {},
      };

      const sourceHealthFromEndpoint = Array.isArray(updates.sourceHealth?.sources)
        ? Object.fromEntries(
          updates.sourceHealth.sources.map((row) => {
            const name = String(row?.name || '').trim().toLowerCase();
            const status = String(row?.status || '').toLowerCase();
            const message = Array.isArray(row?.notes) ? String(row.notes[0] || '') : '';
            return [name || 'source', {
              status,
              message,
              last_ok_ts: row?.last_ok || null,
            }];
          })
        )
        : null;

      const derivedSourceHealth = sourceHealthFromEndpoint
        || updates.regime?.source_health
        || updates.spy?.source_health
        || updates.portfolioRisk?.source_health
        || previousData?.sourceHealth
        || {};
      updates.sourceHealth = derivedSourceHealth;

      const mergedData = mergeData(previousData, updates);
      const ts = nowIso();
      const fullFailure = successfulStages <= 0;
      const nextSnapshot = {
        cached_at: ts,
        expires_at: new Date(Date.now() + FRESH_TTL_MS).toISOString(),
        data: mergedData,
        meta: {
          errors,
          partial: errors.length > 0,
          last_success_at: fullFailure ? (previous?.meta?.last_success_at || ts) : ts,
        },
      };

      setSnapshot(nextSnapshot);
      return nextSnapshot;
    })().finally(() => {
      inFlight = null;
    });

    inFlight = doRefresh;
    return doRefresh;
  }

  function getSnapshot(){
    if(!inMemory){
      inMemory = loadFromStorage();
    }
    return inMemory;
  }

  function setSnapshot(snapshot){
    inMemory = snapshot;
    persist(snapshot);
    if(typeof renderer === 'function'){
      renderer(snapshot);
    }
  }

  function isStale(snapshot, ttlMs){
    const snap = snapshot || getSnapshot();
    if(!snap || !snap.cached_at) return true;
    const ageMs = Date.now() - new Date(snap.cached_at).getTime();
    return ageMs > Number(ttlMs || FRESH_TTL_MS);
  }

  function isUsable(snapshot){
    const snap = snapshot || getSnapshot();
    if(!snap || !snap.cached_at) return false;
    const ageMs = Date.now() - new Date(snap.cached_at).getTime();
    return ageMs <= MAX_STALE_MS;
  }

  function setRenderer(nextRenderer){
    renderer = (typeof nextRenderer === 'function') ? nextRenderer : null;
  }

  function renderCachedImmediately(){
    const snap = getSnapshot();
    if(snap && isUsable(snap) && typeof renderer === 'function'){
      renderer(snap);
      return true;
    }
    return false;
  }

  async function refreshSilent({ force = false } = {}){
    const snap = getSnapshot();
    if(!force && snap && !isStale(snap, FRESH_TTL_MS)){
      return snap;
    }
    return refreshCore({ force });
  }

  async function refreshNow(options = {}){
    const opts = options && typeof options === 'object' ? options : {};
    return refreshCore({ force: true, logFn: opts.logFn });
  }

  async function refreshSilentWithLog(options = {}){
    const opts = options && typeof options === 'object' ? options : {};
    const snap = getSnapshot();
    if(!opts.force && snap && !isStale(snap, FRESH_TTL_MS)){
      return snap;
    }
    return refreshCore({ force: !!opts.force, logFn: opts.logFn });
  }

  return {
    getSnapshot,
    setSnapshot,
    isStale,
    renderCachedImmediately,
    refreshSilent: refreshSilentWithLog,
    refreshNow,
    setRenderer,
    FRESH_TTL_MS,
    MAX_STALE_MS,
    REFRESH_INTERVAL_MS,
  };
})();
