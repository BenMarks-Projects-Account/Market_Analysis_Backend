window.BenTradeHomeCacheStore = (function(){
  const STORAGE_KEY = 'bentrade_home_cache_v1';
  const FRESH_TTL_MS = 60 * 1000;
  const MAX_STALE_MS = 15 * 60 * 1000;
  const REFRESH_INTERVAL_MS = 90 * 1000;

  const INDEX_SYMBOLS = ['SPY', 'QQQ', 'IWM', 'DIA'];
  const SECTOR_SYMBOLS = ['XLF', 'XLK', 'XLE', 'XLY', 'XLP', 'XLV', 'XLI', 'XLB', 'XLRE', 'XLU', 'XLC'];

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

  async function refreshCore({ force, logFn, homeOnly } = {}){
    if(inFlight && !force) return inFlight;

    const api = window.BenTradeApi;
    const previous = getSnapshot();
    const previousData = (previous && typeof previous.data === 'object') ? previous.data : {};
    const skipScanners = !!homeOnly;

    const doRefresh = (async () => {
      const errors = [];
      const updates = {};
      let successfulStages = 0;

      /* ── Rate-limit aware stage runner ── */
      const STAGE_MIN_DELAY_MS = 350;
      const STAGE_MAX_RETRIES = 2;
      const STAGE_BACKOFF_BASE_MS = 2000;
      const STAGE_BACKOFF_CAP_MS = 15000;
      let lastStageFinishedAt = 0;

      function isRetryableError(err){
        const status = Number(err?.status || err?.statusCode || err?.response?.status);
        if(status === 429) return true;
        if(status >= 500 && status < 600) return true;
        const text = String(err?.message || err?.detail || '').toLowerCase();
        return text.includes('rate limit') || text.includes('too many requests');
      }

      function sleep(ms){ return new Promise(function(r){ window.setTimeout(r, Math.max(0,ms)); }); }

      async function runStage(displayName, key, fn){
        /* Enforce minimum gap between stage starts */
        const elapsed = Date.now() - lastStageFinishedAt;
        if(lastStageFinishedAt > 0 && elapsed < STAGE_MIN_DELAY_MS){
          await sleep(STAGE_MIN_DELAY_MS - elapsed);
        }

        logLine(logFn, `Fetching ${displayName}...`);
        let attempt = 0;
        while(true){
          try{
            const value = await fn();
            if(key){
              updates[key] = value;
            }
            successfulStages += 1;
            logLine(logFn, `Loaded: ${displayName}`);
            lastStageFinishedAt = Date.now();
            return value;
          }catch(err){
            if(isRetryableError(err) && attempt < STAGE_MAX_RETRIES){
              const backoff = Math.min(STAGE_BACKOFF_CAP_MS, STAGE_BACKOFF_BASE_MS * Math.pow(2, attempt));
              logLine(logFn, `Rate-limited on ${displayName}, retrying in ${(backoff/1000).toFixed(1)}s (attempt ${attempt + 1}/${STAGE_MAX_RETRIES})...`);
              await sleep(backoff);
              attempt += 1;
              continue;
            }
            const info = errorParts(err);
            errors.push(`${String(key || displayName)}: ${info.message}`);
            logLine(logFn, `Error: ${displayName} ${info.status} ${info.message}`);
            lastStageFinishedAt = Date.now();
            return null;
          }
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
          if(skipScanners){
            // Home-only mode: preserve existing scanner opportunities, don't fetch stale reports
            updates.opportunities = previousData?.opportunities || [];
            return;
          }
          // Full mode: read from Scanner Orchestrator (populated by Run Scan / Full App Refresh)
          const orchestrator = window.BenTradeScannerOrchestrator;
          const orchestratorResults = orchestrator?.getLatestResults?.();
          if(orchestratorResults && Array.isArray(orchestratorResults.opportunities) && orchestratorResults.opportunities.length){
            updates.opportunities = orchestratorResults.opportunities;
            logLine(logFn, `Loaded ${orchestratorResults.opportunities.length} opportunities from scanner orchestrator`);
            return;
          }
          // No orchestrator results yet — keep empty until scanners run this session
          updates.opportunities = [];
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
    return refreshCore({ force, homeOnly: true });
  }

  async function refreshNow(options = {}){
    const opts = options && typeof options === 'object' ? options : {};
    return refreshCore({ force: true, logFn: opts.logFn, homeOnly: !!opts.homeOnly });
  }

  async function refreshSilentWithLog(options = {}){
    const opts = options && typeof options === 'object' ? options : {};
    const snap = getSnapshot();
    if(!opts.force && snap && !isStale(snap, FRESH_TTL_MS)){
      return snap;
    }
    return refreshCore({ force: !!opts.force, logFn: opts.logFn, homeOnly: !!opts.homeOnly });
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
