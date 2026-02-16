window.BenTradeSession = (function(){
  const REPORT_KEY = 'creditSpreadSelectedReport';
  const UNDERLYING_KEY = 'creditSpreadSelectedUnderlying';

  function getSelectedReport(){
    return localStorage.getItem(REPORT_KEY) || '';
  }

  function setSelectedReport(report){
    localStorage.setItem(REPORT_KEY, report || '');
  }

  function getSelectedUnderlying(){
    return localStorage.getItem(UNDERLYING_KEY) || 'ALL';
  }

  function setSelectedUnderlying(symbol){
    localStorage.setItem(UNDERLYING_KEY, symbol || 'ALL');
  }

  function setCurrentTrades(trades){
    window.currentTrades = Array.isArray(trades) ? trades : [];
  }

  function getCurrentTrades(){
    return Array.isArray(window.currentTrades) ? window.currentTrades : [];
  }

  function setCurrentReportFile(reportFile){
    window.currentReportFile = reportFile || null;
  }

  function getCurrentReportFile(){
    return window.currentReportFile || null;
  }

  return {
    getSelectedReport,
    setSelectedReport,
    getSelectedUnderlying,
    setSelectedUnderlying,
    setCurrentTrades,
    getCurrentTrades,
    setCurrentReportFile,
    getCurrentReportFile,
  };
})();
