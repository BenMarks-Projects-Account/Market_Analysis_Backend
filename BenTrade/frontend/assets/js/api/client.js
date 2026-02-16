window.BenTradeApi = (function(){
  async function jsonFetch(url, options){
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if(!response.ok){
      const message = payload?.error?.message || payload?.detail || `Request failed (${response.status})`;
      throw new Error(message);
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

  function addStockWatchlist(symbol){
    return jsonFetch('/api/stock/watchlist', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ symbol: String(symbol || '') }),
    });
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

  return {
    listReports,
    getReport,
    modelAnalyze,
    persistRejectDecision,
    getRejectDecisions,
    getActiveTrades,
    refreshActiveTrades,
    workbenchAnalyze,
    listWorkbenchScenarios,
    saveWorkbenchScenario,
    deleteWorkbenchScenario,
    getStockSummary,
    getStockWatchlist,
    addStockWatchlist,
    getPortfolioRiskMatrix,
    postLifecycleEvent,
    getLifecycleTrades,
    getLifecycleTradeDetail,
    getStrategyAnalyticsSummary,
    getRiskPolicy,
    updateRiskPolicy,
    getRiskSnapshot,
  };
})();
