window.BenTradeApi = (function(){
  async function jsonFetch(url, options){
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if(!response.ok){
      const message = payload?.error?.message || payload?.detail || `Request failed (${response.status})`;
      const err = new Error(message);
      err.status = response.status;
      err.detail = payload?.detail || payload?.error?.message || null;
      err.payload = payload;
      err.endpoint = String(url || '');
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

  function listReports(){
    return jsonFetch('/api/reports');
  }

  function getReport(filename){
    return jsonFetch(`/api/reports/${filename}`);
  }

  function modelAnalyze(trade, source){
    return jsonFetch('/api/model/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ trade, source }),
    });
  }

  function modelAnalyzeStock(symbol, idea, source){
    return jsonFetch('/api/model/analyze_stock', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        symbol: String(symbol || ''),
        idea: (idea && typeof idea === 'object') ? idea : {},
        source: String(source || 'local_llm'),
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

  function getActiveTrades(){
    return jsonFetch('/api/trading/active');
  }

  function refreshActiveTrades(){
    return jsonFetch('/api/trading/active/refresh', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
    });
  }

  function getTradingPositions(){
    return jsonFetch('/api/trading/positions');
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

  return {
    listReports,
    getReport,
    modelAnalyze,
    modelAnalyzeStock,
    persistRejectDecision,
    getRejectDecisions,
    getActiveTrades,
    refreshActiveTrades,
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
    getTopRecommendations,
    getPlaybook,
    getSignals,
    getSignalsUniverse,
    getPortfolioRiskMatrix,
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
  };
})();
