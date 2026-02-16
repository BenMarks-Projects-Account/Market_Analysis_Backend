window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initStrategyAnalytics = function initStrategyAnalytics(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const api = window.BenTradeApi;

  const rangeEl = scope.querySelector('#saRange');
  const refreshBtn = scope.querySelector('#saRefreshBtn');
  const errorEl = scope.querySelector('#saError');
  const kpisEl = scope.querySelector('#saKpis');
  const curveEl = scope.querySelector('#saEquityCurve');
  const strategyBody = scope.querySelector('#saStrategyBody');
  const underlyingBody = scope.querySelector('#saUnderlyingBody');
  const evListEl = scope.querySelector('#saEvList');
  const notesEl = scope.querySelector('#saNotesSystem');
  const notesMountEl = scope.querySelector('#saNotesMount');

  if(!rangeEl || !refreshBtn || !kpisEl || !curveEl || !strategyBody || !underlyingBody || !evListEl || !notesEl || !notesMountEl){
    return;
  }

  window.BenTradeNotes?.attachNotes?.(notesMountEl, 'notes:page:strategy-analytics');

  let payload = null;
  let selectedStrategy = null;

  function fmt(value, d=2){
    if(value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A';
    return Number(value).toFixed(d);
  }

  function setError(text){
    if(!errorEl) return;
    if(!text){
      errorEl.style.display = 'none';
      errorEl.textContent = '';
      return;
    }
    errorEl.style.display = 'block';
    errorEl.textContent = text;
  }

  function renderKpis(){
    const byStrategy = Array.isArray(payload?.by_strategy) ? payload.by_strategy : [];
    const totalTrades = byStrategy.reduce((acc, row) => acc + Number(row.trades || 0), 0);
    const totalPnl = byStrategy.reduce((acc, row) => acc + Number(row.total_pnl || 0), 0);
    const weightedWinKnown = byStrategy.filter(r => r.win_rate !== null && r.win_rate !== undefined);
    const winRate = weightedWinKnown.length
      ? weightedWinKnown.reduce((acc, row) => acc + Number(row.win_rate || 0), 0) / weightedWinKnown.length
      : null;
    const avgPnl = totalTrades > 0 ? totalPnl / totalTrades : null;

    const curve = Array.isArray(payload?.equity_curve) ? payload.equity_curve : [];
    let peak = Number.NEGATIVE_INFINITY;
    let maxDrawdown = 0;
    curve.forEach(point => {
      const cum = Number(point.cum_pnl || 0);
      if(cum > peak) peak = cum;
      if(Number.isFinite(peak)){
        maxDrawdown = Math.max(maxDrawdown, peak - cum);
      }
    });

    kpisEl.innerHTML = `
      <div class="statTile"><div class="statLabel" data-metric="total_pnl">Total P&L</div><div class="statValue">${fmt(totalPnl)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="win_rate">Win Rate</div><div class="statValue">${winRate === null ? 'N/A' : (Number(winRate) * 100).toFixed(1) + '%'}</div></div>
      <div class="statTile"><div class="statLabel">Trades</div><div class="statValue">${totalTrades}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="avg_pnl">Avg P&L</div><div class="statValue">${fmt(avgPnl)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="max_drawdown">Max Drawdown</div><div class="statValue">${fmt(maxDrawdown)}</div></div>
    `;

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(kpisEl);
    }
  }

  function renderCurve(){
    const curve = Array.isArray(payload?.equity_curve) ? payload.equity_curve : [];
    if(!curve.length){
      curveEl.innerHTML = '';
      return;
    }

    const width = 800;
    const height = 220;
    const margin = { top: 14, right: 12, bottom: 24, left: 50 };
    const pw = width - margin.left - margin.right;
    const ph = height - margin.top - margin.bottom;

    const values = curve.map(point => Number(point.cum_pnl || 0));
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = Math.max(max - min, 0.001);

    const path = values.map((val, i) => {
      const x = margin.left + (i / Math.max(values.length - 1, 1)) * pw;
      const y = margin.top + (1 - ((val - min) / span)) * ph;
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    }).join(' ');

    curveEl.innerHTML = `
      <line x1="${margin.left}" y1="${margin.top + ph}" x2="${width - margin.right}" y2="${margin.top + ph}" stroke="rgba(0,234,255,0.35)"></line>
      <line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${margin.top + ph}" stroke="rgba(0,234,255,0.35)"></line>
      <path d="${path}" fill="none" stroke="rgba(0,234,255,0.95)" stroke-width="3"></path>
    `;
  }

  function renderTables(){
    const strategyRows = Array.isArray(payload?.by_strategy) ? payload.by_strategy : [];
    strategyBody.innerHTML = strategyRows.length
      ? strategyRows.map(row => `
        <tr data-strategy="${row.strategy || ''}" class="risk-row">
          <td>${row.strategy || 'N/A'}</td>
          <td>${row.trades ?? 0}</td>
          <td data-metric="win_rate">${row.win_rate === null || row.win_rate === undefined ? 'N/A' : (Number(row.win_rate) * 100).toFixed(1) + '%'}</td>
          <td data-metric="avg_pnl">${fmt(row.avg_pnl)}</td>
          <td data-metric="total_pnl">${fmt(row.total_pnl)}</td>
        </tr>
      `).join('')
      : '<tr><td colspan="5" class="loading">No strategy rows.</td></tr>';

    strategyBody.querySelectorAll('[data-strategy]').forEach(row => {
      row.addEventListener('click', () => {
        selectedStrategy = String(row.getAttribute('data-strategy') || '');
        renderEvList();
      });
    });

    const underRows = Array.isArray(payload?.by_underlying) ? payload.by_underlying : [];
    underlyingBody.innerHTML = underRows.length
      ? underRows.map(row => `
        <tr>
          <td>${row.symbol || 'N/A'}</td>
          <td>${row.trades ?? 0}</td>
          <td data-metric="avg_pnl">${fmt(row.avg_pnl)}</td>
          <td data-metric="total_pnl">${fmt(row.total_pnl)}</td>
        </tr>
      `).join('')
      : '<tr><td colspan="4" class="loading">No underlying rows.</td></tr>';

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(strategyBody);
      window.attachMetricTooltips(underlyingBody);
    }
  }

  function renderEvList(){
    const data = payload?.ev_vs_realized || {};
    const points = Array.isArray(data.points) ? data.points : [];
    const filtered = selectedStrategy ? points.filter(p => String(p.strategy || '') === selectedStrategy) : points;
    evListEl.innerHTML = filtered.length
      ? filtered.map(p => `<div class="stock-note">${p.trade_key} • ${p.strategy || 'N/A'} • <span data-metric="ev_to_risk">EV/R</span> ${fmt(p.ev_to_risk,3)} • <span data-metric="unrealized_pnl">Realized</span> ${fmt(p.realized_pnl)}</div>`).join('')
      : '<div class="loading">No EV vs realized points.</div>';

    const notes = Array.isArray(data.notes) ? data.notes : [];
    const general = Array.isArray(payload?.notes) ? payload.notes : [];
    notesEl.innerHTML = [...general, ...notes].map(msg => `<div class="stock-note">• ${msg}</div>`).join('') || '<div class="loading">No notes.</div>';

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(evListEl);
    }
  }

  async function refresh(){
    try{
      setError('');
      refreshBtn.disabled = true;
      payload = await api.getStrategyAnalyticsSummary(rangeEl.value || '90d');
      renderKpis();
      renderCurve();
      renderTables();
      renderEvList();
    }catch(err){
      setError(String(err?.message || err || 'Failed to load strategy analytics'));
    }finally{
      refreshBtn.disabled = false;
    }
  }

  refreshBtn.addEventListener('click', () => refresh());
  rangeEl.addEventListener('change', () => refresh());

  refresh();
};
