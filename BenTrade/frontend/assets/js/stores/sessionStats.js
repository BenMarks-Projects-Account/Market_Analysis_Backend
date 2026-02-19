window.BenTradeSessionStatsStore = (function(){
  const STORAGE_KEY = 'bentrade_session_stats_v1';
  const MODULE_IDS = [
    'credit_put',
    'credit_call',
    'debit_spreads',
    'iron_condor',
    'butterflies',
    'calendar',
    'income',
    'stock_scanner',
  ];

  let state = null;
  const listeners = new Set();

  /** Get the current sessionId from the boot-choice module (set on boot). */
  function currentSessionId(){
    return window.BenTradeBootChoiceModal?.getSessionId?.() || null;
  }

  function nowIso(){
    return new Date().toISOString();
  }

  function toNumber(value){
    if(value === null || value === undefined || value === '') return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function avgFromList(items, selector){
    if(!Array.isArray(items) || !items.length) return null;
    const values = items.map(selector).map(toNumber).filter(v => v !== null);
    if(!values.length) return null;
    return values.reduce((sum, value) => sum + value, 0) / values.length;
  }

  function maxFromList(items, selector){
    if(!Array.isArray(items) || !items.length) return null;
    const values = items.map(selector).map(toNumber).filter(v => v !== null);
    if(!values.length) return null;
    return Math.max(...values);
  }

  function zeroCounters(){
    return {
      runs: 0,
      total_candidates: 0,
      accepted_trades: 0,
      rejected_trades: 0,
      best_score: null,
      _sum_quality_score: 0,
      _quality_samples: 0,
      _sum_return_on_risk: 0,
      _ror_samples: 0,
    };
  }

  function buildModuleMap(){
    const out = {};
    MODULE_IDS.forEach((moduleId) => {
      out[moduleId] = zeroCounters();
    });
    return out;
  }

  function createInitialState(){
    const ts = nowIso();
    return {
      sessionId: currentSessionId(),
      session_started_at: ts,
      last_updated_at: ts,
      last_home_refresh_at: null,
      home_refresh_count: 0,
      ...zeroCounters(),
      by_module: buildModuleMap(),
    };
  }

  function clampNonNegative(value){
    const n = Math.round(Number(value) || 0);
    return n < 0 ? 0 : n;
  }

  function ensureModule(stateObj, moduleId){
    if(!stateObj.by_module || typeof stateObj.by_module !== 'object'){
      stateObj.by_module = {};
    }
    if(!stateObj.by_module[moduleId]){
      stateObj.by_module[moduleId] = zeroCounters();
    }
  }

  function hydrateState(raw){
    const base = createInitialState();
    const incoming = (raw && typeof raw === 'object') ? raw : {};

    /* ── Session boundary guard: reject data from a different session ── */
    const activeId = currentSessionId();
    if(activeId && incoming.sessionId && incoming.sessionId !== activeId){
      /* Stale data from a prior session — start fresh */
      return base;
    }

    base.sessionId = activeId || incoming.sessionId || base.sessionId;
    base.session_started_at = String(incoming.session_started_at || base.session_started_at);
    base.last_updated_at = String(incoming.last_updated_at || base.last_updated_at);
    base.last_home_refresh_at = incoming.last_home_refresh_at || null;
    base.home_refresh_count = clampNonNegative(incoming.home_refresh_count);

    const mergeCounters = (target, source) => {
      target.runs = clampNonNegative(source?.runs);
      target.total_candidates = clampNonNegative(source?.total_candidates);
      target.accepted_trades = clampNonNegative(source?.accepted_trades);
      target.rejected_trades = clampNonNegative(source?.rejected_trades);
      const best = toNumber(source?.best_score);
      target.best_score = best !== null ? best : null;
      target._sum_quality_score = toNumber(source?._sum_quality_score) ?? 0;
      target._quality_samples = clampNonNegative(source?._quality_samples);
      target._sum_return_on_risk = toNumber(source?._sum_return_on_risk) ?? 0;
      target._ror_samples = clampNonNegative(source?._ror_samples);
    };

    mergeCounters(base, incoming);

    MODULE_IDS.forEach((moduleId) => {
      ensureModule(base, moduleId);
      mergeCounters(base.by_module[moduleId], incoming?.by_module?.[moduleId]);
    });

    return base;
  }

  function computeDerived(node){
    const candidates = clampNonNegative(node.total_candidates);
    const accepted = clampNonNegative(node.accepted_trades);
    const qualitySamples = clampNonNegative(node._quality_samples);
    const rorSamples = clampNonNegative(node._ror_samples);
    return {
      runs: clampNonNegative(node.runs),
      total_candidates: candidates,
      accepted_trades: accepted,
      rejected_trades: clampNonNegative(node.rejected_trades),
      acceptance_rate: candidates > 0 ? (accepted / candidates) : 0,
      best_score: toNumber(node.best_score),
      avg_quality_score: qualitySamples > 0 ? (Number(node._sum_quality_score) / qualitySamples) : null,
      avg_return_on_risk: rorSamples > 0 ? (Number(node._sum_return_on_risk) / rorSamples) : null,
    };
  }

  function computeViewState(){
    const root = state || createInitialState();
    const byModule = {};
    MODULE_IDS.forEach((moduleId) => {
      byModule[moduleId] = computeDerived(root.by_module[moduleId] || zeroCounters());
    });

    return {
      sessionId: root.sessionId || null,
      session_started_at: root.session_started_at,
      last_updated_at: root.last_updated_at,
      last_home_refresh_at: root.last_home_refresh_at || null,
      home_refresh_count: clampNonNegative(root.home_refresh_count),
      ...computeDerived(root),
      by_module: byModule,
    };
  }

  function persist(){
    try{
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    }catch(_err){
    }
  }

  function fmtPercent(value, digits){
    const n = toNumber(value);
    if(n === null) return 'N/A';
    return `${(n * 100).toFixed(digits)}%`;
  }

  function fmtNumber(value, digits){
    const n = toNumber(value);
    if(n === null) return 'N/A';
    return n.toFixed(digits);
  }

  /** Delegate to the shared format lib for canonical score display. */
  function fmtScore(raw){
    const lib = window.BenTradeUtils && window.BenTradeUtils.format;
    if(lib && lib.formatScore) return lib.formatScore(raw, 1);
    const n = toNumber(raw);
    return n === null ? 'N/A' : `${n.toFixed(1)}%`;
  }

  function renderPanel(){
    const grid = document.getElementById('reportStatsGrid');
    const title = document.getElementById('sessionStatsTitle');
    const meta = document.getElementById('sessionStatsMeta');
    if(!grid) return;

    const snapshot = computeViewState();

    if(title){
      title.textContent = 'SESSION STATS';
    }

    if(meta){
      const startedText = snapshot.session_started_at ? new Date(snapshot.session_started_at).toLocaleString() : 'N/A';
      const updatedText = snapshot.last_updated_at ? new Date(snapshot.last_updated_at).toLocaleString() : 'N/A';
      const homeRefreshText = snapshot.last_home_refresh_at ? new Date(snapshot.last_home_refresh_at).toLocaleTimeString() : '--';
      meta.textContent = `Runs: ${snapshot.runs} • Home refreshes: ${snapshot.home_refresh_count} (${homeRefreshText}) • Started: ${startedText} • Updated: ${updatedText}`;
    }

    const stats = [
      ['Total candidates', String(snapshot.total_candidates)],
      ['Accepted trades/ideas', String(snapshot.accepted_trades)],
      ['Rejected', String(snapshot.rejected_trades)],
      ['Acceptance rate', fmtPercent(snapshot.acceptance_rate, 1)],
      ['Best score', snapshot.best_score === null ? 'N/A' : fmtScore(snapshot.best_score)],
      ['Avg quality score', snapshot.avg_quality_score === null ? 'N/A' : fmtScore(snapshot.avg_quality_score)],
      ['Avg return on risk', snapshot.avg_return_on_risk === null ? 'N/A' : fmtPercent(snapshot.avg_return_on_risk, 1)],
      ['Session runs', String(snapshot.runs)],
    ];

    grid.innerHTML = stats.map(([label, value]) => `
      <div class="statTile">
        <div class="statLabel">${label}</div>
        <div class="statValue">${value}</div>
      </div>
    `).join('');

    const resetBtn = document.getElementById('sessionStatsResetBtn');
    if(resetBtn && !resetBtn.dataset.bound){
      resetBtn.dataset.bound = '1';
      resetBtn.addEventListener('click', () => {
        const ok = window.confirm('Reset session stats? This clears accumulated totals.');
        if(ok) reset();
      });
    }
  }

  /* ── Strategy Leaderboard (global right info bar) ── */
  const LEADERBOARD_LABELS = {
    credit_put: 'Credit Put', credit_call: 'Credit Call', debit_spreads: 'Debit Spreads',
    iron_condor: 'Iron Condor', butterflies: 'Butterflies', calendar: 'Calendar',
    income: 'Income', stock_scanner: 'Stock Scanner',
  };

  function renderLeaderboard(){
    const rowsEl = document.getElementById('globalStrategyRows');
    const miniEl = document.getElementById('globalStrategyMini');
    if(!rowsEl) return;
    const snapshot = computeViewState();
    const byModule = snapshot.by_module || {};
    const rows = MODULE_IDS.map(id => [LEADERBOARD_LABELS[id] || id, byModule[id]]);

    rowsEl.innerHTML = rows.map(([label, row]) => {
      const quality = toNumber(row?.avg_quality_score);
      const qualityText = quality === null ? 'N/A' : fmtScore(quality);
      const rorVal = toNumber(row?.avg_return_on_risk);
      const rorText = rorVal === null ? 'N/A' : fmtPercent(rorVal, 1);
      return `<tr><td>${label}</td><td>${qualityText}</td><td>${rorText}</td><td>${clampNonNegative(row?.accepted_trades || 0)}</td></tr>`;
    }).join('');

    if(miniEl){
      miniEl.innerHTML = rows.map(([label, row]) => {
        const score = toNumber(row?.avg_quality_score) ?? 0;
        const width = Math.max(2, Math.round(Math.min(Math.max(score, 0), 100)));
        return `<div class="home-mini-row"><span>${label}</span><div class="home-mini-track"><div class="home-mini-fill" style="width:${width}%;"></div></div></div>`;
      }).join('');
    }
  }

  function notify(){
    renderPanel();
    renderLeaderboard();
    const snapshot = computeViewState();
    listeners.forEach((listener) => {
      try{ listener(snapshot); }catch(_err){}
    });
  }

  function weightedMerge(target, normalized){
    target.runs += clampNonNegative(normalized.runs);
    target.total_candidates += clampNonNegative(normalized.total_candidates);
    target.accepted_trades += clampNonNegative(normalized.accepted_trades);
    target.rejected_trades += clampNonNegative(normalized.rejected_trades);

    const best = toNumber(normalized.best_score);
    if(best !== null){
      const currentBest = toNumber(target.best_score);
      target.best_score = currentBest === null ? best : Math.max(currentBest, best);
    }

    const quality = toNumber(normalized.avg_quality_score);
    const qualitySamples = clampNonNegative(normalized._quality_samples ?? normalized.accepted_trades ?? normalized.total_candidates);
    if(quality !== null && qualitySamples > 0){
      target._sum_quality_score += (quality * qualitySamples);
      target._quality_samples += qualitySamples;
    }

    const avgRor = toNumber(normalized.avg_return_on_risk);
    const rorSamples = clampNonNegative(normalized._ror_samples ?? normalized.accepted_trades);
    if(avgRor !== null && rorSamples > 0){
      target._sum_return_on_risk += (avgRor * rorSamples);
      target._ror_samples += rorSamples;
    }
  }

  function normalizeStats(moduleId, apiResponse){
    const payload = (apiResponse && typeof apiResponse === 'object') ? apiResponse : {};
    const reportStats = (payload.report_stats && typeof payload.report_stats === 'object')
      ? payload.report_stats
      : ((payload.reportStats && typeof payload.reportStats === 'object') ? payload.reportStats : {});

    const trades = Array.isArray(payload.trades) ? payload.trades : [];
    const candidates = Array.isArray(payload.candidates) ? payload.candidates : [];

    const notes = [];

    let totalCandidates = toNumber(reportStats.total_candidates);
    if(totalCandidates === null){
      totalCandidates = moduleId === 'stock_scanner' ? candidates.length : trades.length;
      notes.push('total_candidates missing; defaulted from list length');
    }

    let acceptedTrades = toNumber(reportStats.accepted_trades);
    if(acceptedTrades === null){
      if(moduleId === 'stock_scanner'){
        acceptedTrades = totalCandidates;
        notes.push('accepted_trades missing for stock_scanner; defaulted to candidates count');
      }else{
        acceptedTrades = trades.length || totalCandidates;
        notes.push('accepted_trades missing; defaulted from trades/candidates count');
      }
    }

    let rejectedTrades = toNumber(reportStats.rejected_trades);
    if(rejectedTrades === null){
      rejectedTrades = Math.max((totalCandidates || 0) - (acceptedTrades || 0), 0);
      notes.push('rejected_trades missing; computed from candidates - accepted');
    }

    let bestScore = toNumber(reportStats.best_trade_score ?? reportStats.best_quality_score ?? reportStats.best_score);
    if(bestScore === null){
      bestScore = maxFromList(trades, (trade) => trade?.composite_score ?? trade?.trade_quality_score);
      if(bestScore === null){
        bestScore = maxFromList(candidates, (idea) => idea?.composite_score ?? idea?.trade_quality_score);
      }
      if(bestScore === null){
        notes.push('best_score missing; defaulted to null');
      }
    }

    let avgQuality = toNumber(reportStats.avg_trade_score ?? reportStats.avg_quality_score);
    let qualitySamples = clampNonNegative(acceptedTrades || totalCandidates || 0);
    if(avgQuality === null){
      avgQuality = avgFromList(trades, (trade) => trade?.composite_score ?? trade?.trade_quality_score);
      if(avgQuality === null){
        avgQuality = avgFromList(candidates, (idea) => idea?.composite_score ?? idea?.trade_quality_score);
      }
      if(avgQuality === null){
        qualitySamples = 0;
        notes.push('avg_quality_score missing; defaulted to null');
      }else{
        notes.push('avg_quality_score missing; derived from candidate scores');
      }
    }

    let avgRor = toNumber(reportStats.avg_return_on_risk ?? reportStats.avg_ror);
    let rorSamples = clampNonNegative(acceptedTrades || 0);
    if(avgRor === null){
      avgRor = avgFromList(trades, (trade) => trade?.return_on_risk);
      if(avgRor === null){
        rorSamples = 0;
        notes.push('avg_return_on_risk missing; defaulted to null');
      }else{
        notes.push('avg_return_on_risk missing; derived from trades list');
      }
    }

    if(notes.length){
      console.info(`[session-stats] ${moduleId}: ${notes.join(' | ')}`);
    }

    return {
      runs: 1,
      total_candidates: clampNonNegative(totalCandidates),
      accepted_trades: clampNonNegative(acceptedTrades),
      rejected_trades: clampNonNegative(rejectedTrades),
      best_score: bestScore,
      avg_quality_score: avgQuality,
      avg_return_on_risk: avgRor,
      _quality_samples: qualitySamples,
      _ror_samples: rorSamples,
    };
  }

  function recordRun(moduleId, payload){
    const id = MODULE_IDS.includes(moduleId) ? moduleId : null;
    if(!id){
      console.info(`[session-stats] unsupported module id ignored: ${moduleId}`);
      return computeViewState();
    }

    const normalized = normalizeStats(id, payload);
    ensureModule(state, id);
    weightedMerge(state, normalized);
    weightedMerge(state.by_module[id], normalized);
    state.last_updated_at = nowIso();
    persist();
    notify();
    return computeViewState();
  }

  function recordReject(moduleId, count){
    const id = MODULE_IDS.includes(moduleId) ? moduleId : null;
    if(!id) return;

    const delta = clampNonNegative(count ?? 1);
    if(delta <= 0) return;

    ensureModule(state, id);
    state.rejected_trades += delta;
    state.by_module[id].rejected_trades += delta;

    if(state.accepted_trades >= delta) state.accepted_trades -= delta;
    else state.accepted_trades = 0;

    if(state.by_module[id].accepted_trades >= delta) state.by_module[id].accepted_trades -= delta;
    else state.by_module[id].accepted_trades = 0;

    state.last_updated_at = nowIso();
    persist();
    notify();
  }

  function recordHomeRefresh(){
    if(!state) init();
    state.home_refresh_count = (state.home_refresh_count || 0) + 1;
    state.last_home_refresh_at = nowIso();
    state.last_updated_at = nowIso();
    persist();
    notify();
    return computeViewState();
  }

  function reset(){
    state = createInitialState();
    persist();
    notify();
    return computeViewState();
  }

  function init(){
    if(state) return computeViewState();
    let parsed = null;
    try{
      const raw = localStorage.getItem(STORAGE_KEY);
      parsed = raw ? JSON.parse(raw) : null;
    }catch(_err){
      parsed = null;
    }
    state = hydrateState(parsed);
    persist();
    notify();
    window.addEventListener('storage', (event) => {
      if(event.key !== STORAGE_KEY) return;
      try{
        const next = event.newValue ? JSON.parse(event.newValue) : null;
        state = hydrateState(next);
        notify();
      }catch(_err){
      }
    });
    return computeViewState();
  }

  function getState(){
    if(!state) init();
    return computeViewState();
  }

  function subscribe(listener){
    if(typeof listener !== 'function') return () => {};
    listeners.add(listener);
    try{ listener(getState()); }catch(_err){}
    return () => listeners.delete(listener);
  }

  init();

  return {
    moduleIds: MODULE_IDS.slice(),
    init,
    getState,
    subscribe,
    reset,
    normalizeStats,
    recordRun,
    recordReject,
    recordHomeRefresh,
    renderPanel,
    renderLeaderboard,
  };
})();
