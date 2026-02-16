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

  return {
    listReports,
    getReport,
    modelAnalyze,
    persistRejectDecision,
    getRejectDecisions,
  };
})();
