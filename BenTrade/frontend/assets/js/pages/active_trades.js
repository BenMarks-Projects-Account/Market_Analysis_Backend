window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initActiveTrades = function initActiveTrades(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const api = window.BenTradeApi;
  const sourceHealthUi = window.BenTradeSourceHealth;
  const tradeKeyUtil = window.BenTradeUtils?.tradeKey;

  const listEl = scope.querySelector('#activeList');
  const errorEl = scope.querySelector('#activeError');
  const refreshBtn = scope.querySelector('#activeRefreshBtn');
  const autoRefreshEl = scope.querySelector('#activeAutoRefresh');
  const underlyingFilterEl = scope.querySelector('#activeUnderlyingFilter');
  const statusFilterEl = scope.querySelector('#activeStatusFilter');
  const searchEl = scope.querySelector('#activeSearch');
  const liveBadgeEl = scope.querySelector('#activeLiveBadge');

  const modalEl = scope.querySelector('#activeCloseModal');
  const modalBodyEl = scope.querySelector('#activeModalBody');
  const modalCloseBtn = scope.querySelector('#activeCloseModalBtn');

  if(!listEl || !refreshBtn || !underlyingFilterEl || !statusFilterEl || !searchEl){
    return;
  }

  let autoTimer = null;
  let trades = [];
  let payload = null;
  const expanded = new Set();

  function stableKeyForTrade(trade, index){
    if(tradeKeyUtil?.tradeKey){
      return tradeKeyUtil.tradeKey({
        underlying: trade?.symbol,
        expiration: trade?.expiration,
        spread_type: trade?.spread_type || trade?.strategy,
        short_strike: trade?.short_strike,
        long_strike: trade?.long_strike,
        dte: trade?.dte,
      });
    }
    return String(trade?.trade_key || trade?.trade_id || index);
  }

  function fmtNumber(value, decimals = 2){
    if(value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A';
    return Number(value).toFixed(decimals);
  }

  function fmtMoney(value){
    if(value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A';
    const n = Number(value);
    const sign = n >= 0 ? '+' : '-';
    return `${sign}$${Math.abs(n).toFixed(2)}`;
  }

  function fmtPct(value){
    if(value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A';
    const n = Number(value) * 100;
    const sign = n >= 0 ? '+' : '-';
    return `${sign}${Math.abs(n).toFixed(1)}%`;
  }

  function setLiveBadge(asOf){
    if(!liveBadgeEl) return;
    if(!asOf){
      liveBadgeEl.textContent = 'STALE';
      liveBadgeEl.classList.remove('is-live');
      return;
    }
    const ageMs = Date.now() - new Date(asOf).getTime();
    const isLive = Number.isFinite(ageMs) && ageMs >= 0 && ageMs <= 90000;
    liveBadgeEl.textContent = isLive ? 'LIVE' : 'STALE';
    liveBadgeEl.classList.toggle('is-live', isLive);
  }

  function renderSourceHealth(sourceHealth){
    if(!sourceHealthUi || !sourceHealthUi.renderFromSnapshot) return;
    sourceHealthUi.renderFromSnapshot(sourceHealth || {});
  }

  function renderStats(tradeCount){
    const statsEl = document.getElementById('reportStatsGrid');
    if(!statsEl) return;
    statsEl.innerHTML = `
      <div class="statTile"><div class="statLabel">Dashboard</div><div class="statValue">Active Trades</div></div>
      <div class="statTile"><div class="statLabel">Source</div><div class="statValue">Tradier</div></div>
      <div class="statTile"><div class="statLabel">Open Trades</div><div class="statValue">${tradeCount}</div></div>
      <div class="statTile"><div class="statLabel">Mode</div><div class="statValue">Read-Only</div></div>
    `;
  }

  function hydrateUnderlyingFilter(allTrades){
    const current = underlyingFilterEl.value || 'ALL';
    const symbols = [...new Set((allTrades || []).map(tr => String(tr.symbol || '').toUpperCase()).filter(Boolean))].sort();
    underlyingFilterEl.innerHTML = '<option value="ALL">All underlyings</option>';
    symbols.forEach(symbol => {
      const option = document.createElement('option');
      option.value = symbol;
      option.textContent = symbol;
      underlyingFilterEl.appendChild(option);
    });
    underlyingFilterEl.value = symbols.includes(current) ? current : 'ALL';
  }

  function currentFilteredTrades(){
    const symbol = (underlyingFilterEl.value || 'ALL').toUpperCase();
    const status = (statusFilterEl.value || 'ALL').toUpperCase();
    const search = (searchEl.value || '').trim().toLowerCase();

    return (trades || []).filter(trade => {
      const sym = String(trade.symbol || '').toUpperCase();
      const st = String(trade.status || '').toUpperCase();
      const strategy = String(trade.strategy || '').toLowerCase();

      if(symbol !== 'ALL' && sym !== symbol) return false;
      if(status !== 'ALL' && st !== status) return false;
      if(search && !(sym.toLowerCase().includes(search) || strategy.includes(search))) return false;
      return true;
    });
  }

  function openSimulateClose(trade){
    if(!modalEl || !modalBodyEl) return;
    const mark = trade?.mark_price;
    const quantity = Number(trade?.quantity || 0);

    if(mark === null || mark === undefined || Number.isNaN(Number(mark))){
      modalBodyEl.innerHTML = '<div class="active-modal-note">mark unavailable; under construction</div>';
    } else {
      const estClose = Number(mark) * quantity * 100;
      const pnl = (trade?.unrealized_pnl !== null && trade?.unrealized_pnl !== undefined)
        ? Number(trade.unrealized_pnl)
        : null;
      modalBodyEl.innerHTML = `
        <div class="active-modal-row"><span>Trade</span><strong>${trade.symbol} • ${trade.strategy}</strong></div>
        <div class="active-modal-row"><span data-metric="mark">Estimated close debit/credit</span><strong>$${estClose.toFixed(2)}</strong></div>
        <div class="active-modal-row"><span data-metric="unrealized_pnl">Estimated P&L if closed now</span><strong>${pnl === null || Number.isNaN(pnl) ? 'N/A' : fmtMoney(pnl)}</strong></div>
      `;
    }

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(modalBodyEl);
    }

    modalEl.style.display = 'flex';
  }

  function renderCards(){
    const filtered = currentFilteredTrades();

    if(!filtered.length){
      listEl.innerHTML = `
        <div class="active-empty-tron">
          <div class="active-empty-title">NO OPEN TRADES</div>
          <div class="active-empty-sub">Quantum lane is clear. New opportunities will appear here.</div>
        </div>
      `;
      return;
    }

    listEl.innerHTML = filtered.map((trade, idx) => {
      const key = stableKeyForTrade(trade, idx);
      const isExpanded = expanded.has(key);
      const legs = Array.isArray(trade.legs) ? trade.legs : [];
      const pnlClass = Number(trade.unrealized_pnl || 0) >= 0 ? 'positive' : 'negative';

      return `
        <div class="trade-card active-trade-card" data-trade-key="${key}">
          <div class="trade-header active-trade-header">
            <div class="trade-type">${trade.symbol || 'N/A'} • ${trade.strategy || 'single'}</div>
            <div class="trade-strikes">Qty ${trade.quantity ?? 'N/A'} • <span data-metric="dte">DTE</span> ${trade.dte ?? 'N/A'} • ${trade.status || 'OPEN'}</div>
          </div>
          <div class="trade-body">
            <div class="metric-grid">
              <div class="metric"><div class="metric-label">Avg Open</div><div class="metric-value">${fmtNumber(trade.avg_open_price)}</div></div>
              <div class="metric"><div class="metric-label" data-metric="mark">Mark</div><div class="metric-value">${fmtNumber(trade.mark_price)}</div></div>
              <div class="metric"><div class="metric-label" data-metric="unrealized_pnl">Unrealized P&L</div><div class="metric-value ${pnlClass}">${fmtMoney(trade.unrealized_pnl)}</div></div>
              <div class="metric"><div class="metric-label" data-metric="unrealized_pnl_pct">P&L %</div><div class="metric-value ${pnlClass}">${fmtPct(trade.unrealized_pnl_pct)}</div></div>
            </div>

            <div class="active-trade-actions">
              <button class="btn active-toggle-legs" data-key="${key}">${isExpanded ? 'Hide Legs' : 'Show Legs'}</button>
              <button class="btn" data-simulate-key="${key}">Simulate Close</button>
            </div>

            <div class="active-legs ${isExpanded ? '' : 'is-collapsed'}">
              ${legs.length ? legs.map(leg => `
                <div class="detail-row">
                  <span class="detail-label">${leg.symbol || 'LEG'} (${leg.side || '-'})</span>
                  <span class="detail-value">Qty ${leg.qty ?? '-'} @ ${fmtNumber(leg.price)}</span>
                </div>
              `).join('') : '<div class="detail-row"><span class="detail-label">No legs</span><span class="detail-value">N/A</span></div>'}
            </div>
          </div>
        </div>
      `;
    }).join('');

    listEl.querySelectorAll('[data-key]').forEach(btn => {
      btn.addEventListener('click', () => {
        const key = btn.getAttribute('data-key');
        if(!key) return;
        if(expanded.has(key)) expanded.delete(key); else expanded.add(key);
        renderCards();
      });
    });

    listEl.querySelectorAll('[data-simulate-key]').forEach(btn => {
      btn.addEventListener('click', () => {
        const key = btn.getAttribute('data-simulate-key');
        const trade = filtered.find((item, idx) => stableKeyForTrade(item, idx) === String(key));
        if(trade) openSimulateClose(trade);
      });
    });

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(listEl);
    }
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

  async function refresh(){
    try{
      setError('');
      refreshBtn.disabled = true;
      refreshBtn.textContent = 'Refreshing...';

      payload = await api.getActiveTrades();

      if(payload?.error){
        setError(payload.error === 'Tradier not configured' ? 'Tradier not configured' : payload.error);
      }

      trades = Array.isArray(payload?.active_trades) ? payload.active_trades : [];
      hydrateUnderlyingFilter(trades);
      renderSourceHealth(payload?.source_health || {});
      renderStats(trades.length);
      setLiveBadge(payload?.as_of);
      renderCards();
    }catch(err){
      console.error('[active-trades] refresh failed', err);
      setError(String(err?.message || err || 'Failed to load active trades'));
      trades = [];
      renderCards();
    }finally{
      refreshBtn.disabled = false;
      refreshBtn.textContent = 'Refresh';
    }
  }

  function setupAutoRefresh(){
    if(autoTimer){
      clearInterval(autoTimer);
      autoTimer = null;
    }
    if(autoRefreshEl?.checked){
      autoTimer = setInterval(() => {
        refresh();
      }, 30000);
    }
  }

  refreshBtn.addEventListener('click', () => refresh());
  underlyingFilterEl.addEventListener('change', renderCards);
  statusFilterEl.addEventListener('change', renderCards);
  searchEl.addEventListener('input', renderCards);

  if(autoRefreshEl){
    autoRefreshEl.addEventListener('change', setupAutoRefresh);
  }

  if(modalCloseBtn && modalEl){
    modalCloseBtn.addEventListener('click', () => { modalEl.style.display = 'none'; });
    modalEl.addEventListener('click', (event) => {
      if(event.target === modalEl){
        modalEl.style.display = 'none';
      }
    });
  }

  refresh();
};
