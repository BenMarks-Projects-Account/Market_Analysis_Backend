window.BenTradeApi = (function(){
  // Model/LLM requests get a 185s client-side timeout (slightly > backend 180s
  // so the backend timeout fires first and returns a proper error).
  var MODEL_TIMEOUT_MS = 185000;

  async function jsonFetch(url, options){
    const response = await fetch(url, options);
    const responseText = await response.text();
    let payload = {};
    if(responseText){
      try{
        payload = JSON.parse(responseText);
      }catch(_err){
        payload = {};
      }
    }
    if(!response.ok){
      const rawDetail = payload?.detail;
      const errObj = payload?.error;

      // Extract message — handle Pydantic 422 array format, string detail,
      // structured detail object, and error envelope
      var message;
      if (errObj?.message) {
        message = errObj.message;
      } else if (typeof rawDetail === 'string') {
        message = rawDetail;
      } else if (Array.isArray(rawDetail)) {
        // Pydantic 422 validation errors — extract all messages
        message = rawDetail.map(function(e) {
          var loc = (e.loc || []).join(' → ');
          return (loc ? loc + ': ' : '') + (e.msg || 'validation error');
        }).join('; ') || 'Validation error (' + response.status + ')';
      } else if (rawDetail?.message) {
        message = rawDetail.message;
      } else {
        message = 'Request failed (' + response.status + ')';
      }

      const err = new Error(message);
      err.status = response.status;
      // Merge structured details from error envelope with raw detail
      const structured = errObj?.details && Object.keys(errObj.details).length > 0
        ? Object.assign({message: errObj.message}, errObj.details)
        : null;
      err.detail = structured || rawDetail || errObj?.message || null;
      err.payload = payload;
      err.endpoint = String(url || '');
      err.bodySnippet = responseText ? String(responseText).slice(0, 2000) : '';
      throw err;
    }

    try{
      const textUrl = String(url || '');
      const isHealthEndpoint = textUrl.startsWith('/api/health');
      if(!isHealthEndpoint && window.BenTradeSourceHealthStore?.fetchSourceHealth){
        window.BenTradeSourceHealthStore.fetchSourceHealth({ force: true }).catch(() => {});
      }
    }catch(_err){
    }

    return payload;
  }

  /** jsonFetch with an AbortController timeout for model/LLM calls. */
  function modelFetch(url, options) {
    var controller = new AbortController();
    var timer = setTimeout(function() { controller.abort(); }, MODEL_TIMEOUT_MS);
    var opts = Object.assign({}, options || {}, { signal: controller.signal });
    return jsonFetch(url, opts).finally(function() { clearTimeout(timer); });
  }

  function listReports(){
    return jsonFetch('/api/reports');
  }

  function getReport(filename){
    return jsonFetch(`/api/reports/${filename}`);
  }

  /* ── Shared trade sanitizer for model analysis (multi-leg aware) ─── */
  /**
   * Ensure iron-condor trades carry the 4 numeric strike fields and legs[],
   * and never send short_strike/long_strike as "P...|C..." strings.
   * 2-leg spreads pass through unchanged.
   */
  function _sanitizeTradeForModel(trade) {
    if (!trade || typeof trade !== 'object') return trade || {};
    var out = Object.assign({}, trade);
    var sid = String(out.spread_type || out.strategy_id || out.type || '').toLowerCase();

    if (sid.indexOf('iron_condor') !== -1 || sid.indexOf('condor') !== -1) {
      /* ── Iron condor: populate 4 numeric strikes, strip string encoding ── */
      var parseStrikePair = function(val) {
        if (typeof val !== 'string') return null;
        var m = val.match(/P([\d.]+)\|C([\d.]+)/i);
        return m ? { put: parseFloat(m[1]), call: parseFloat(m[2]) } : null;
      };

      /* Parse string-encoded strikes into numeric fields if needed */
      var shortParsed = parseStrikePair(out.short_strike);
      var longParsed  = parseStrikePair(out.long_strike);

      if (shortParsed) {
        if (out.short_put_strike  == null) out.short_put_strike  = shortParsed.put;
        if (out.short_call_strike == null) out.short_call_strike = shortParsed.call;
        out.short_strike = null;  // clear string — server expects float or null
      }
      if (longParsed) {
        if (out.long_put_strike  == null) out.long_put_strike  = longParsed.put;
        if (out.long_call_strike == null) out.long_call_strike = longParsed.call;
        out.long_strike = null;
      }

      /* If short_strike/long_strike are still strings (but not parseable), null them */
      if (typeof out.short_strike === 'string') out.short_strike = null;
      if (typeof out.long_strike  === 'string') out.long_strike  = null;
    }
    return out;
  }

  function modelAnalyze(trade, source){
    return modelFetch('/api/model/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ trade: _sanitizeTradeForModel(trade), source }),
    });
  }

  function modelAnalyzeStock(symbol, idea, source){
    return modelFetch('/api/model/analyze_stock', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        symbol: String(symbol || ''),
        idea: (idea && typeof idea === 'object') ? idea : {},
        source: String(source || 'local_llm'),
      }),
    });
  }

  function modelAnalyzeStockStrategy(strategyId, candidate){
    return modelFetch('/api/model/analyze_stock_strategy', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        strategy_id: String(strategyId || ''),
        candidate: (candidate && typeof candidate === 'object') ? candidate : {},
      }),
    });
  }

  function tmcFinalDecision(candidate, strategyId){
    return modelFetch('/api/tmc/workflows/model/final-decision', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        candidate: (candidate && typeof candidate === 'object') ? candidate : {},
        strategy_id: strategyId || null,
      }),
    });
  }

  function modelAnalyzeRegime(regime, playbook){
    return modelFetch('/api/model/analyze_regime', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        regime: (regime && typeof regime === 'object') ? regime : {},
        playbook: (playbook && typeof playbook === 'object') ? playbook : null,
      }),
    });
  }

  function persistRejectDecision(payload){
    return jsonFetch('/api/decisions/reject', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {}),
    });
  }

  function getRejectDecisions(reportFile){
    return jsonFetch(`/api/decisions/${encodeURIComponent(reportFile)}`);
  }

  function getActiveTrades(accountMode){
    const mode = accountMode || 'live';
    return jsonFetch(`/api/trading/active?account_mode=${encodeURIComponent(mode)}`);
  }

  function refreshActiveTrades(accountMode){
    const mode = accountMode || 'live';
    return jsonFetch(`/api/trading/active/refresh?account_mode=${encodeURIComponent(mode)}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
    });
  }

  function closePosition(payload){
    return jsonFetch('/api/trading/close-position', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
  }

  function getMonitorResults(accountMode){
    const mode = accountMode || 'live';
    return jsonFetch(`/api/trading/monitor?account_mode=${encodeURIComponent(mode)}`);
  }

  function getMonitorNarrative(symbol, position, monitorResult){
    return modelFetch('/api/trading/monitor/narrative', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ symbol: symbol, position: position, monitor_result: monitorResult }),
    });
  }

  function analyzeActiveTrade(symbol, position, accountMode){
    return modelFetch('/api/model/active-trade-analysis', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ symbol: symbol, position: position, account_mode: accountMode || 'live' }),
    });
  }

  function getTradingPositions(accountMode){
    const mode = accountMode || 'live';
    return jsonFetch(`/api/trading/positions?account_mode=${encodeURIComponent(mode)}`);
  }

  function getTradingOpenOrders(){
    return jsonFetch('/api/trading/orders/open');
  }

  function getTradingAccount(){
    return jsonFetch('/api/trading/account');
  }

  function workbenchAnalyze(payload){
    return jsonFetch('/api/workbench/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {}),
    });
  }

  function listWorkbenchScenarios(){
    return jsonFetch('/api/workbench/scenarios');
  }

  function saveWorkbenchScenario(payload){
    return jsonFetch('/api/workbench/scenarios', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {}),
    });
  }

  function deleteWorkbenchScenario(id){
    return jsonFetch(`/api/workbench/scenarios/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });
  }

  function getStockSummary(symbol, range){
    const sym = encodeURIComponent(String(symbol || 'SPY').toUpperCase());
    const rng = encodeURIComponent(String(range || '6mo'));
    return jsonFetch(`/api/stock/summary?symbol=${sym}&range=${rng}`);
  }

  function getStockWatchlist(){
    return jsonFetch('/api/stock/watchlist');
  }

  function getStockScanner(){
    return jsonFetch('/api/stock/scanner');
  }

  function addStockWatchlist(symbol){
    return jsonFetch('/api/stock/watchlist', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ symbol: String(symbol || '') }),
    });
  }

  function getMacroIndicators(){
    return jsonFetch('/api/stock/macro');
  }

  function getRegime(){
    return jsonFetch('/api/regime');
  }

  function getRegimeProxies(){
    return jsonFetch('/api/regime/proxies');
  }

  function getTopRecommendations(){
    return jsonFetch('/api/recommendations/top');
  }

  function getPlaybook(){
    return jsonFetch('/api/playbook');
  }

  function getSignals(symbol, range){
    const sym = encodeURIComponent(String(symbol || 'SPY').toUpperCase());
    const rng = encodeURIComponent(String(range || '6mo'));
    return jsonFetch(`/api/signals?symbol=${sym}&range=${rng}`);
  }

  function getSignalsUniverse(universe, range){
    const uni = encodeURIComponent(String(universe || 'default'));
    const rng = encodeURIComponent(String(range || '6mo'));
    return jsonFetch(`/api/signals/universe?universe=${uni}&range=${rng}`);
  }

  function getPortfolioRiskMatrix(){
    return jsonFetch('/api/portfolio/risk/matrix');
  }

  function getMarketPictureScoreboard(){
    return jsonFetch('/api/market-picture/scoreboard');
  }

  function getMarketPictureModelScores(){
    return jsonFetch('/api/market-picture/model-scores');
  }

  function getMarketPictureHistory(limit){
    var qs = limit ? '?limit=' + encodeURIComponent(limit) : '';
    return jsonFetch('/api/market-picture/history' + qs);
  }

  function postLifecycleEvent(payload){
    return jsonFetch('/api/lifecycle/event', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {}),
    });
  }

  function getLifecycleTrades(state){
    const query = state ? `?state=${encodeURIComponent(String(state))}` : '';
    return jsonFetch(`/api/lifecycle/trades${query}`);
  }

  function getLifecycleTradeDetail(tradeKey){
    return jsonFetch(`/api/lifecycle/trades/${encodeURIComponent(String(tradeKey || ''))}`);
  }

  function getStrategyAnalyticsSummary(range){
    const key = encodeURIComponent(String(range || '90d'));
    return jsonFetch(`/api/analytics/strategy/summary?range=${key}`);
  }

  function getRiskPolicy(){
    return jsonFetch('/api/risk/policy');
  }

  function updateRiskPolicy(payload){
    return jsonFetch('/api/risk/policy', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {}),
    });
  }

  function getRiskSnapshot(){
    return jsonFetch('/api/risk/snapshot');
  }

  function listStrategyReports(strategyId){
    const key = encodeURIComponent(String(strategyId || ''));
    return jsonFetch(`/api/strategies/${key}/reports`);
  }

  function getStrategyReport(strategyId, filename){
    const key = encodeURIComponent(String(strategyId || ''));
    const file = encodeURIComponent(String(filename || ''));
    return jsonFetch(`/api/strategies/${key}/reports/${file}`);
  }

  function generateStrategyReport(strategyId, payload){
    const key = encodeURIComponent(String(strategyId || ''));
    return jsonFetch(`/api/strategies/${key}/generate`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {}),
    });
  }

  function getAdminDataHealth(){
    return jsonFetch('/api/admin/data-health');
  }

  /* ── Trading execution ───────────────────────────────────── */

  function getTradingStatus(){
    return jsonFetch('/api/trading/status');
  }

  function tradingTestConnection(){
    return jsonFetch('/api/trading/test-connection');
  }

  function tradingPreview(payload){
    var endpoint = '/api/trading/preview';
    console.log('Tradier preview endpoint:', endpoint);
    console.log('Tradier preview payload:', payload);
    return jsonFetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {}),
    });
  }

  function tradingSubmit(payload){
    return jsonFetch('/api/trading/submit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {}),
    });
  }

  function tradingKillSwitchOn(){
    return jsonFetch('/api/trading/runtime-config', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ tradier_execution_enabled: true }),
    });
  }

  function tradingKillSwitchOff(){
    return jsonFetch('/api/trading/runtime-config', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ tradier_execution_enabled: false }),
    });
  }

  /* ── Stock execution ───────────────────────────────────── */
  function stockExecute(payload){
    return jsonFetch('/api/stocks/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }
  function getStockExecutionStatus(){
    return jsonFetch('/api/stocks/execute/status');
  }

  /* ── Stock Engine — run all stock scanners, return top 9 ── */
  function getStockEngine(){
    return jsonFetch('/api/stocks/engine');
  }

  /* ── Order reconciliation — check Tradier status ─────── */
  function getTradierOrderStatus(orderId){
    return jsonFetch('/api/trading/orders/' + encodeURIComponent(orderId) + '/tradier-status');
  }

  /* ── Active Trade Pipeline ────────────────────────────────── */
  function runActiveTradesPipeline(opts){
    var params = [];
    if (opts && opts.skip_model) params.push('skip_model=true');
    if (opts && opts.account_mode) params.push('account_mode=' + encodeURIComponent(opts.account_mode));
    var qs = params.length ? '?' + params.join('&') : '';
    return modelFetch('/api/active-trade-pipeline/run' + qs, { method: 'POST' });
  }
  function getLatestActiveTradeResults(){
    return jsonFetch('/api/active-trade-pipeline/results');
  }
  function getActiveTradeRunDetail(runId){
    return jsonFetch('/api/active-trade-pipeline/results/' + encodeURIComponent(runId));
  }
  function listActiveTradeRuns(){
    return jsonFetch('/api/active-trade-pipeline/runs');
  }

  /* ── TMC Workflow Endpoints ───────────────────────────────── */
  function tmcRunStock(opts){
    var body = {};
    if (opts && opts.top_n != null) body.top_n = opts.top_n;
    return jsonFetch('/api/tmc/workflows/stock/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }
  function tmcRunOptions(opts){
    var body = {};
    if (opts && opts.top_n != null) body.top_n = opts.top_n;
    if (opts && opts.symbols) body.symbols = opts.symbols;
    return jsonFetch('/api/tmc/workflows/options/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }
  function tmcGetLatestStock(){
    return jsonFetch('/api/tmc/workflows/stock/latest');
  }
  function tmcGetLatestOptions(){
    return jsonFetch('/api/tmc/workflows/options/latest');
  }
  function tmcGetStockSummary(){
    return jsonFetch('/api/tmc/workflows/stock/summary');
  }
  function tmcGetOptionsSummary(){
    return jsonFetch('/api/tmc/workflows/options/summary');
  }

  /* ── Data Population ── */
  function getDataPopulationStatus(){
    return jsonFetch('/api/data-population/status');
  }
  function triggerDataPopulation(){
    return jsonFetch('/api/data-population/trigger', { method: 'POST' });
  }

  /* ── Contextual Chat ── */
  function contextualChat(context, message, history){
    return modelFetch('/api/chat/contextual', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        context: context || {},
        message: message || '',
        history: Array.isArray(history) ? history : [],
      }),
    });
  }

  return {
    listReports,
    getReport,
    modelAnalyze,
    modelAnalyzeStock,
    modelAnalyzeStockStrategy,
    modelAnalyzeRegime,
    persistRejectDecision,
    getRejectDecisions,
    getActiveTrades,
    refreshActiveTrades,
    closePosition,
    getTradingPositions,
    getTradingOpenOrders,
    getTradingAccount,
    workbenchAnalyze,
    listWorkbenchScenarios,
    saveWorkbenchScenario,
    deleteWorkbenchScenario,
    getStockSummary,
    getStockWatchlist,
    getStockScanner,
    addStockWatchlist,
    getMacroIndicators,
    getRegime,
    getRegimeProxies,
    getTopRecommendations,
    getPlaybook,
    getSignals,
    getSignalsUniverse,
    getPortfolioRiskMatrix,
    getMarketPictureScoreboard,
    getMarketPictureModelScores,
    getMarketPictureHistory,
    postLifecycleEvent,
    getLifecycleTrades,
    getLifecycleTradeDetail,
    getStrategyAnalyticsSummary,
    getRiskPolicy,
    updateRiskPolicy,
    getRiskSnapshot,
    listStrategyReports,
    getStrategyReport,
    generateStrategyReport,
    getAdminDataHealth,
    getTradingStatus,
    tradingTestConnection,
    tradingPreview,
    tradingSubmit,
    tradingKillSwitchOn,
    tradingKillSwitchOff,
    getTradierOrderStatus,
    stockExecute,
    getStockExecutionStatus,
    getStockEngine,
    getMonitorResults,
    getMonitorNarrative,
    analyzeActiveTrade,
    runActiveTradesPipeline,
    getLatestActiveTradeResults,
    getActiveTradeRunDetail,
    listActiveTradeRuns,
    tmcRunStock,
    tmcRunOptions,
    tmcGetLatestStock,
    tmcGetLatestOptions,
    tmcGetStockSummary,
    tmcGetOptionsSummary,
    tmcFinalDecision,
    getDataPopulationStatus,
    triggerDataPopulation,
    contextualChat,
    MODEL_TIMEOUT_MS: MODEL_TIMEOUT_MS,
    modelFetch: modelFetch,
  };
})();
