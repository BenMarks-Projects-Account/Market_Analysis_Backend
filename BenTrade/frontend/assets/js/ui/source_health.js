window.BenTradeSourceHealth = (function(){
  function statusClass(status){
    const value = String(status || '').toLowerCase();
    if(value === 'green') return 'status-green';
    if(value === 'red') return 'status-red';
    return 'status-yellow';
  }

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

  function renderFromSnapshot(snapshot){
    const target = document.getElementById('sourceHealthRows');
    if(!target) return;

    const rows = Object.entries(snapshot || {}).map(([source, value]) => ({
      label: String(source || '').toUpperCase(),
      statusClass: statusClass(value?.status),
      tooltip: value?.message || 'No message',
    }));

    target.innerHTML = renderRows(rows);
  }

  return {
    renderRows,
    renderFromSnapshot,
  };
})();
