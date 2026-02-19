/**
 * Scanner Orchestrator — runs multiple scanners sequentially, normalizes
 * results, and returns the top N opportunities across all scanners.
 *
 * Exposed as  window.BenTradeScannerOrchestrator
 *
 * Usage:
 *   const result = await BenTradeScannerOrchestrator.runScannerSuite({
 *     scannerIds: ['stock_scanner', 'credit_put', 'credit_call'],
 *     logFn: console.log,
 *     onStepComplete: ({ id, label, ok, tradeCount }) => { ... },
 *   });
 *   // result.opportunities  — top 5 normalized picks
 *   // result.scanMeta       — run metadata (timestamp, duration, etc.)
 *   // result.errors         — per-scanner error strings
 */
window.BenTradeScannerOrchestrator = (function(){
  'use strict';

  /* ──────────────────── Scanner definitions ──────────────────── */

  /* Default per-scanner timeout (ms).  Override at runtime via
     BenTradeScannerOrchestrator.setTimeoutOverrides({ stock_scanner: 120000 }) */
  const DEFAULT_OPTION_TIMEOUT = 90000;   // was 45 000
  const DEFAULT_STOCK_TIMEOUT  = 180000;  // bumped from 60 000 — stock scanner is slow
  let _timeoutOverrides = {};             // id → ms

  /* ── Per-level timeout multipliers (wider levels analyse more data) ── */
  const LEVEL_TIMEOUT_MULT = { strict: 0.8, conservative: 1.0, balanced: 1.0, wide: 1.4 };

  /* ── Retry / back-off constants ── */
  const MAX_RETRIES       = 3;
  const RETRY_BASE_MS     = 2000;   // 2 s → 4 s → 8 s  (exponential)
  const RETRYABLE_STATUS  = new Set([429, 502, 503, 504]);

  const OPTION_SCANNER_DEFS = [
    { id: 'credit_put',    strategyId: 'credit_spread', moduleId: 'credit_put',    label: 'Credit Put Spread',  payload: { spread_type: 'put_credit_spread' }, route: '#/credit-spread', timeoutMs: DEFAULT_OPTION_TIMEOUT, optional: false },
    { id: 'credit_call',   strategyId: 'credit_spread', moduleId: 'credit_call',   label: 'Credit Call Spread', payload: { spread_type: 'call_credit_spread' }, route: '#/credit-spread', timeoutMs: DEFAULT_OPTION_TIMEOUT, optional: false },
    { id: 'iron_condor',   strategyId: 'iron_condor',   moduleId: 'iron_condor',   label: 'Iron Condor',        payload: {},                                   route: '#/iron-condor',   timeoutMs: DEFAULT_OPTION_TIMEOUT, optional: true  },
    { id: 'debit_spreads', strategyId: 'debit_spreads', moduleId: 'debit_spreads', label: 'Debit Spreads',      payload: {},                                   route: '#/debit-spreads', timeoutMs: DEFAULT_OPTION_TIMEOUT, optional: true  },
    { id: 'butterflies',   strategyId: 'butterflies',   moduleId: 'butterflies',   label: 'Butterflies',        payload: {},                                   route: '#/butterflies',   timeoutMs: DEFAULT_OPTION_TIMEOUT, optional: true  },
    { id: 'income',        strategyId: 'income',        moduleId: 'income',        label: 'Income',             payload: {},                                   route: '#/income',        timeoutMs: DEFAULT_OPTION_TIMEOUT, optional: true  },
    { id: 'calendar',      strategyId: 'calendars',     moduleId: 'calendar',      label: 'Calendar',           payload: {},                                   route: '#/calendar',      timeoutMs: DEFAULT_OPTION_TIMEOUT, optional: true  },
  ];

  const STOCK_SCANNER_DEF = {
    id: 'stock_scanner', moduleId: 'stock_scanner', label: 'Stock Scanner', route: '#/stock-scanner', timeoutMs: DEFAULT_STOCK_TIMEOUT, optional: false,
  };

  const TOP_N = 9;

  /** Latest scan results (persisted in memory). */
  let _latestResults = null;

  /* ──────────────────── Helpers ──────────────────── */

  const fmt = window.BenTradeUtils && window.BenTradeUtils.format ? window.BenTradeUtils.format : {};

  function toNumber(value){
    if(value === null || value === undefined || value === '') return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function deriveRor(raw){
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

  function deriveLiquidity(raw){
    const bidAskPct = toNumber(raw?.bid_ask_spread_pct);
    if(bidAskPct !== null){
      return Math.max(0, Math.min(100, 100 - (bidAskPct * 100)));
    }
    const volume = toNumber(raw?.volume);
    const oi = toNumber(raw?.open_interest);
    if(volume !== null || oi !== null){
      return Math.max(0, Math.min(100,
        ((volume || 0) / 1000) * 40 + ((oi || 0) / 3000) * 60));
    }
    return null;
  }

  function logLine(logFn, text){
    if(typeof logFn === 'function') logFn(String(text || ''));
  }

  /** Resolve effective timeout for a scanner id, factoring in level multiplier. */
  function getTimeoutMs(def, level){
    const override = _timeoutOverrides[def.id];
    const base = (typeof override === 'number' && override > 0) ? override : def.timeoutMs;
    const mult = LEVEL_TIMEOUT_MULT[level] || 1.0;
    return Math.round(base * mult);
  }

  /**
   * Wrap a promise with a timeout.  Rejects with a timeout error if the
   * promise does not settle within `ms` milliseconds.
   */
  function withTimeout(promise, ms, label){
    if(!ms || ms <= 0) return promise;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(`Timeout after ${ms}ms: ${label}`));
      }, ms);
      promise.then(
        (v) => { clearTimeout(timer); resolve(v); },
        (e) => { clearTimeout(timer); reject(e); },
      );
    });
  }

  /**
   * Determine whether an error is retryable (rate-limit or transient server error).
   * Looks for a `status` prop (fetch failures), or a "429" / "rate" mention in the message.
   */
  function isRetryable(err){
    if(err && typeof err.status === 'number' && RETRYABLE_STATUS.has(err.status)) return true;
    const msg = String(err?.message || err || '').toLowerCase();
    if(msg.includes('429') || msg.includes('rate limit') || msg.includes('too many requests')) return true;
    if(msg.includes('502') || msg.includes('503') || msg.includes('504') || msg.includes('bad gateway') || msg.includes('service unavailable')) return true;
    return false;
  }

  /**
   * Retry a promise-returning function with exponential back-off.
   * Only retries on retryable errors; other errors propagate immediately.
   *
   * @param {function} fn      — () => Promise<T>
   * @param {number}   ms      — per-attempt timeout
   * @param {string}   label   — human label for logging
   * @param {function} logFn   — optional log callback
   * @returns {Promise<T>}
   */
  async function withRetry(fn, ms, label, logFn){
    let lastErr;
    for(let attempt = 0; attempt <= MAX_RETRIES; attempt++){
      try{
        return await withTimeout(fn(), ms, label);
      }catch(err){
        lastErr = err;
        if(attempt < MAX_RETRIES && isRetryable(err)){
          const delay = RETRY_BASE_MS * Math.pow(2, attempt);
          logLine(logFn, `⚠ ${label} failed (attempt ${attempt + 1}/${MAX_RETRIES + 1}), retrying in ${delay}ms — ${err?.message || err}`);
          await new Promise(r => setTimeout(r, delay));
        }else{
          throw err;
        }
      }
    }
    throw lastErr;
  }

  /* ──────────────────── Normalize ──────────────────── */

  /**
   * Normalize a single trade / candidate from any scanner into the
   * canonical Scanner Opportunity shape consumed by the Opportunity Engine.
   *
   * @param {object} row   — raw trade or stock candidate
   * @param {object} def   — scanner definition (id, label, route, ...)
   * @param {string} type  — 'options' | 'stock'
   * @returns {object} normalized opportunity
   */
  function normalizeResult(row, def, type){
    const raw = (row && typeof row === 'object') ? row : {};
    const comp = (raw?.computed && typeof raw.computed === 'object') ? raw.computed : {};
    const symbol = String(raw?.symbol || '').trim().toUpperCase() || 'N/A';
    const score = fmt.normalizeScore(raw?.composite_score ?? raw?.trade_quality_score ?? raw?.score) ?? 0;

    const isStock = type === 'stock';
    const ev = isStock ? null : toNumber(comp?.expected_value ?? raw?.ev ?? raw?.edge);
    const pop = isStock ? null : toNumber(comp?.pop ?? raw?.pop);
    const ror = isStock ? null : deriveRor(raw);
    const liquidity = isStock ? null : deriveLiquidity(raw);

    const modelEvaluation = raw?.model_evaluation && typeof raw.model_evaluation === 'object'
      ? raw.model_evaluation : null;
    const strategy = String(raw?.strategy_id || raw?.type || raw?.recommended_strategy || def?.label || 'idea');

    return {
      symbol,
      strategy,
      score,
      ev,
      pop,
      ror,
      sourceScanner: def?.id || 'unknown',
      sourceType: isStock ? 'stock' : 'options',
      source: def?.label || 'Unknown',
      route: def?.route || '#/credit-spread',
      source_feed: 'scanner_orchestrator',
      model: modelEvaluation,
      key_metrics: {
        price: toNumber(raw?.price),
        rsi14: toNumber(raw?.metrics?.rsi14 ?? raw?.signals?.rsi_14 ?? raw?.rsi14),
        ema20: toNumber(raw?.metrics?.ema20 ?? raw?.ema20),
        iv_rv_ratio: toNumber(raw?.metrics?.iv_rv_ratio ?? raw?.signals?.iv_rv_ratio ?? raw?.iv_rv_ratio),
        trend: String(raw?.trend || '').trim().toLowerCase() || null,
        iv_rv_flag: null,
        liquidity,
      },
      trade: raw,
    };
  }

  /* ──────────────────── Sorting ──────────────────── */

  /**
   * Sort opportunities: primary by score descending (0-100),
   * then liquidity, then EV as tie-breakers.
   */
  function sortOpportunities(list){
    return list.slice().sort((a, b) => {
      // 1. Score (descending)
      const scoreDiff = (b.score || 0) - (a.score || 0);
      if(scoreDiff !== 0) return scoreDiff;

      // 2. Liquidity tie-breaker (descending; higher = tighter spreads)
      const liqA = toNumber(a.key_metrics?.liquidity) || 0;
      const liqB = toNumber(b.key_metrics?.liquidity) || 0;
      const liqDiff = liqB - liqA;
      if(liqDiff !== 0) return liqDiff;

      // 3. EV tie-breaker (descending; higher EV preferred)
      const evDiff = (toNumber(b.ev) || 0) - (toNumber(a.ev) || 0);
      return evDiff;
    });
  }

  /* ──────────────────── Core orchestrator ──────────────────── */

  /**
   * Return the full list of available scanner IDs.
   */
  function allScannerIds(){
    return [STOCK_SCANNER_DEF.id, ...OPTION_SCANNER_DEFS.map((d) => d.id)];
  }

  /**
   * Map preset name → array of scanner IDs.
   * All filter levels run every scanner.  Legacy quick/full_sweep preserved.
   */
  function presetToScannerIds(preset){
    const mode = String(preset || 'balanced').toLowerCase();
    if(mode === 'quick'){
      return ['stock_scanner'];
    }
    // All filter levels (strict, conservative, balanced, wide) and full_sweep
    // run every scanner.
    return allScannerIds();
  }

  /**
   * Run selected scanners sequentially, normalize results, and return
   * the top N opportunities.
   *
   * @param {object}   options
   * @param {string[]} [options.scannerIds]     — IDs to run (default: all)
   * @param {string[]} [options.symbols]        — symbol filter (default: store universe)
   * @param {function} [options.logFn]          — logging callback
   * @param {function} [options.onStepComplete] — called after each scanner
   *        with { id, label, ok, error, tradeCount, moduleId }
   * @returns {Promise<{opportunities: object[], scanMeta: object, errors: string[]}>}
   */
  async function runScannerSuite({ scannerIds, symbols, filterLevel, logFn, onStepComplete } = {}){
    const api = window.BenTradeApi;
    if(!api){
      throw new Error('BenTradeApi not available');
    }

    const idsToRun = Array.isArray(scannerIds) && scannerIds.length
      ? scannerIds
      : allScannerIds();

    const effectiveLevel = String(filterLevel || 'balanced').toLowerCase();

    const startTime = Date.now();
    const allCandidates = [];
    const errors = [];
    const scannersRun = [];
    const scannersFailed = [];

    /* Resolve symbol universe for option scanners */
    const resolvedSymbols = (Array.isArray(symbols) && symbols.length)
      ? symbols
      : (window.BenTradeSymbolUniverseStore?.getSymbols?.() || null);

    /* ── Stock scanner ── */
    if(idsToRun.includes(STOCK_SCANNER_DEF.id)){
      const def = STOCK_SCANNER_DEF;
      logLine(logFn, `Running: ${def.label}`);
      try{
        const response = await withRetry(
          () => api.getStockScanner(),
          getTimeoutMs(def, effectiveLevel),
          def.label,
          logFn,
        );
        const candidates = Array.isArray(response?.candidates) ? response.candidates : [];
        candidates.forEach((row) => {
          allCandidates.push(normalizeResult(row, def, 'stock'));
        });

        // Record session stats
        if(window.BenTradeSessionStatsStore?.recordRun && (Array.isArray(response?.candidates) || response?.report_stats)){
          window.BenTradeSessionStatsStore.recordRun(def.moduleId, response);
        }

        scannersRun.push(def.id);
        logLine(logFn, `Success: ${def.label} (${candidates.length} candidates)`);
        if(typeof onStepComplete === 'function'){
          onStepComplete({ id: def.id, label: def.label, ok: true, error: null, tradeCount: candidates.length, moduleId: def.moduleId });
        }
      }catch(err){
        const msg = String(err?.message || err || 'unknown error');
        errors.push(`${def.label}: ${msg}`);
        scannersFailed.push(def.id);
        logLine(logFn, `Failed: ${def.label} — ${msg}`);
        if(typeof onStepComplete === 'function'){
          onStepComplete({ id: def.id, label: def.label, ok: false, error: msg, tradeCount: 0, moduleId: def.moduleId });
        }
      }
    }

    /* ── Options scanners (sequential) ── */
    for(const def of OPTION_SCANNER_DEFS){
      if(!idsToRun.includes(def.id)) continue;

      logLine(logFn, `Running: ${def.label}`);
      try{
        /* Merge profile params → scanner payload.  Profile values serve as
           the baseline; def.payload overrides (e.g. spread_type for credit). */
        const profileParams = (window.BenTradeScannerProfiles?.getProfile)
          ? (window.BenTradeScannerProfiles.getProfile(def.strategyId, effectiveLevel) || {})
          : {};
        const scanPayload = Object.assign({}, profileParams, def.payload || {});
        if(resolvedSymbols) scanPayload.symbols = resolvedSymbols;
        const response = await withRetry(
          () => api.generateStrategyReport(def.strategyId, scanPayload),
          getTimeoutMs(def, effectiveLevel),
          def.label,
          logFn,
        );

        const trades = Array.isArray(response?.trades) ? response.trades : [];
        trades.forEach((row) => {
          allCandidates.push(normalizeResult(row, def, 'options'));
        });

        // Record session stats
        if(window.BenTradeSessionStatsStore?.recordRun){
          const hasCandidates = Array.isArray(response?.candidates);
          const hasTrades = Array.isArray(response?.trades);
          const hasStats = response?.report_stats && typeof response.report_stats === 'object';
          if(hasCandidates || hasTrades || hasStats){
            window.BenTradeSessionStatsStore.recordRun(def.moduleId, response);
          }
        }

        scannersRun.push(def.id);
        logLine(logFn, `Success: ${def.label} (${trades.length} trades)`);
        if(typeof onStepComplete === 'function'){
          onStepComplete({ id: def.id, label: def.label, ok: true, error: null, tradeCount: trades.length, moduleId: def.moduleId });
        }
      }catch(err){
        const msg = String(err?.message || err || 'unknown error');

        // Non-fatal for optional scanners
        if(def.optional){
          errors.push(`${def.label} (optional): ${msg}`);
          scannersFailed.push(def.id);
          logLine(logFn, `Failed (optional, continuing): ${def.label} — ${msg}`);
        }else{
          errors.push(`${def.label}: ${msg}`);
          scannersFailed.push(def.id);
          logLine(logFn, `Failed: ${def.label} — ${msg}`);
        }

        if(typeof onStepComplete === 'function'){
          onStepComplete({ id: def.id, label: def.label, ok: false, error: msg, tradeCount: 0, moduleId: def.moduleId });
        }
        // Continue — do NOT break; one scanner failing must not kill the run.
      }
    }

    /* ── Aggregate & sort ── */
    const sorted = sortOpportunities(allCandidates);
    const top = sorted.slice(0, TOP_N);
    const durationMs = Date.now() - startTime;

    _latestResults = {
      opportunities: top,
      allCandidates: sorted,
      scanMeta: {
        ran_at: new Date().toISOString(),
        duration_ms: durationMs,
        scanners_run: scannersRun,
        scanners_failed: scannersFailed,
        total_candidates: allCandidates.length,
        top_n: TOP_N,
      },
      errors,
    };

    logLine(logFn, `Scanner suite complete: ${scannersRun.length} scanners, ${allCandidates.length} candidates, top ${top.length} picked (${durationMs}ms)`);

    return _latestResults;
  }

  /* ──────────────────── Public API ──────────────────── */

  return {
    /** Run the scanner suite. See JSDoc above. */
    runScannerSuite,
    /** Get the latest results (or null if no scan has been run). */
    getLatestResults: function(){ return _latestResults; },
    /** Clear stored results. */
    clearResults: function(){ _latestResults = null; },
    /** Number of top picks returned. */
    TOP_N,
    /** All available scanner IDs. */
    allScannerIds,
    /** Map preset name to scanner IDs. */
    presetToScannerIds,
    /** Scanner definitions (read-only snapshot). */
    OPTION_SCANNER_DEFS: OPTION_SCANNER_DEFS.slice(),
    STOCK_SCANNER_DEF: Object.assign({}, STOCK_SCANNER_DEF),
    /** Override per-scanner timeout at runtime: { stock_scanner: 120000 } */
    setTimeoutOverrides: function(overrides){
      _timeoutOverrides = (overrides && typeof overrides === 'object') ? overrides : {};
    },
  };
})();
