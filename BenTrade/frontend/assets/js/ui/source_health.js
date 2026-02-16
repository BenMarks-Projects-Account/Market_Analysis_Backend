window.BenTradeSourceHealth = (function(){
  function renderRows(rows){
    return (rows || []).map((row) => {
      const label = row?.label || 'Unknown';
      const statusClass = row?.statusClass || 'status-yellow';
      const tooltip = row?.tooltip || '';
      return `
        <div class="diagnosticRow">
          <span class="diagnosticLabel">${label}</span>
          <span class="status-wrap" tabindex="0">
            <span class="status-dot ${statusClass}"></span>
            <span class="status-tooltip">${tooltip}</span>
          </span>
        </div>
      `;
    }).join('');
  }

  return {
    renderRows,
  };
})();
