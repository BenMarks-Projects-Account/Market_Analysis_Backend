window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initPortfolioRisk = function initPortfolioRisk(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const api = window.BenTradeApi;
  const sourceHealthUi = window.BenTradeSourceHealth;

  const refreshBtn = scope.querySelector('#prRefreshBtn');
  const runScenarioBtn = scope.querySelector('#prRunScenarioBtn');
  const errorEl = scope.querySelector('#prError');
  const warningsEl = scope.querySelector('#prWarnings');
  const totalsEl = scope.querySelector('#prPortfolioTotals');
  const underlyingBody = scope.querySelector('#prUnderlyingBody');
  const bucketBody = scope.querySelector('#prBucketBody');
  const scenariosEl = scope.querySelector('#prScenarios');
  const notesEl = scope.querySelector('#prNotesSystem');
  const notesMountEl = scope.querySelector('#prNotesMount');
  const modalEl = scope.querySelector('#prDetailModal');
  const modalBody = scope.querySelector('#prDetailBody');
  const closeBtn = scope.querySelector('#prDetailCloseBtn');

  if(!refreshBtn || !runScenarioBtn || !totalsEl || !underlyingBody || !bucketBody || !scenariosEl || !notesEl || !notesMountEl || !modalEl || !modalBody || !closeBtn){
    return;
  }

  window.BenTradeNotes?.attachNotes?.(notesMountEl, 'notes:page:portfolio-risk');

  let payload = null;

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

  function renderTotals(){
    const p = payload?.portfolio || {};
    totalsEl.innerHTML = `
      <div class="statTile"><div class="statLabel">As Of</div><div class="statValue">${payload?.as_of || 'N/A'}</div></div>
      <div class="statTile"><div class="statLabel">Source</div><div class="statValue">${payload?.source || 'none'}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="delta">Delta</div><div class="statValue">${fmt(p.delta,3)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="gamma">Gamma</div><div class="statValue">${fmt(p.gamma,3)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="theta">Theta</div><div class="statValue">${fmt(p.theta,3)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="vega">Vega</div><div class="statValue">${fmt(p.vega,3)}</div></div>
    `;

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(totalsEl);
    }
  }

  function openUnderlyingDetail(symbol){
    const list = Array.isArray(payload?.by_underlying) ? payload.by_underlying : [];
    const item = list.find(row => String(row.symbol || '').toUpperCase() === String(symbol || '').toUpperCase());
    const trades = Array.isArray(item?.trades) ? item.trades : [];
    modalBody.innerHTML = trades.length
      ? trades.map(tr => `
        <div class="active-modal-row"><span>${tr.trade_key || 'N/A'}</span><strong>${tr.strategy || 'N/A'} • DTE ${tr.dte ?? 'N/A'} • Risk ${fmt(tr.risk)}</strong></div>
      `).join('')
      : '<div class="active-modal-note">No trade detail for this underlying.</div>';
    modalEl.style.display = 'flex';
  }

  function renderUnderlying(){
    const rows = Array.isArray(payload?.by_underlying) ? payload.by_underlying : [];
    if(!rows.length){
      underlyingBody.innerHTML = '<tr><td colspan="7" class="loading">No rows</td></tr>';
      return;
    }
    underlyingBody.innerHTML = rows.map(row => `
      <tr class="risk-row" data-symbol="${row.symbol || ''}">
        <td>${row.symbol || 'N/A'}</td>
        <td data-metric="delta">${fmt(row.delta,3)}</td>
        <td data-metric="gamma">${fmt(row.gamma,3)}</td>
        <td data-metric="theta">${fmt(row.theta,3)}</td>
        <td data-metric="vega">${fmt(row.vega,3)}</td>
        <td data-metric="estimated_risk">${fmt(row.risk)}</td>
        <td>${row.trade_count ?? 0}</td>
      </tr>
    `).join('');

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(underlyingBody);
    }

    underlyingBody.querySelectorAll('[data-symbol]').forEach(row => {
      row.addEventListener('click', () => openUnderlyingDetail(row.getAttribute('data-symbol')));
    });
  }

  function renderBuckets(){
    const rows = Array.isArray(payload?.by_expiration_bucket) ? payload.by_expiration_bucket : [];
    if(!rows.length){
      bucketBody.innerHTML = '<tr><td colspan="3" class="loading">No rows</td></tr>';
      return;
    }
    bucketBody.innerHTML = rows.map(row => `
      <tr>
        <td>${row.bucket || 'N/A'}</td>
        <td data-metric="estimated_risk">${fmt(row.risk)}</td>
        <td>${row.trade_count ?? 0}</td>
      </tr>
    `).join('');

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(bucketBody);
    }
  }

  function renderScenarios(){
    const rows = Array.isArray(payload?.scenarios) ? payload.scenarios : [];
    scenariosEl.innerHTML = rows.length
      ? rows.map(row => `<div class="stock-note">${row.name || 'Scenario'} → ${fmt(row.pnl_estimate)}</div>`).join('')
      : '<div class="loading">No scenarios</div>';
  }

  function renderWarningsAndNotes(){
    const warnings = Array.isArray(payload?.warnings) ? payload.warnings : [];
    warningsEl.innerHTML = warnings.length
      ? warnings.map(msg => `<div class="stock-note">• ${msg}</div>`).join('')
      : '<div class="stock-note">No warnings.</div>';

    const notes = ['Risk matrix includes best-effort Greek approximations where needed.'];
    notesEl.innerHTML = notes.map(msg => `<div class="stock-note">• ${msg}</div>`).join('');
  }

  async function refresh(){
    try{
      setError('');
      refreshBtn.disabled = true;
      payload = await api.getPortfolioRiskMatrix();
      renderTotals();
      renderUnderlying();
      renderBuckets();
      renderScenarios();
      renderWarningsAndNotes();
      if(sourceHealthUi?.renderFromSnapshot){
        sourceHealthUi.renderFromSnapshot(payload?.source_health || {});
      }
    }catch(err){
      setError(String(err?.message || err || 'Failed to load portfolio risk matrix'));
    }finally{
      refreshBtn.disabled = false;
    }
  }

  refreshBtn.addEventListener('click', () => refresh());
  runScenarioBtn.addEventListener('click', () => renderScenarios());
  closeBtn.addEventListener('click', () => { modalEl.style.display = 'none'; });
  modalEl.addEventListener('click', (event) => {
    if(event.target === modalEl){
      modalEl.style.display = 'none';
    }
  });

  refresh();
};
