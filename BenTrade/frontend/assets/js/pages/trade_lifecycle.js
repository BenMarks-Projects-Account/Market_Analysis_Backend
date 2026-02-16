window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initTradeLifecycle = function initTradeLifecycle(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const api = window.BenTradeApi;

  const tabsEl = scope.querySelector('#lcTabs');
  const refreshBtn = scope.querySelector('#lcRefreshBtn');
  const errorEl = scope.querySelector('#lcError');
  const bodyEl = scope.querySelector('#lcTradesBody');
  const pageNotesMountEl = scope.querySelector('#lcPageNotesMount');
  const modalEl = scope.querySelector('#lcHistoryModal');
  const modalBody = scope.querySelector('#lcHistoryBody');
  const closeBtn = scope.querySelector('#lcHistoryCloseBtn');

  if(!tabsEl || !refreshBtn || !bodyEl || !pageNotesMountEl || !modalEl || !modalBody || !closeBtn){
    return;
  }

  window.BenTradeNotes?.attachNotes?.(pageNotesMountEl, 'notes:page:trade-lifecycle');

  let currentState = 'WATCHLIST';

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

  async function openHistory(tradeKey){
    try{
      const payload = await api.getLifecycleTradeDetail(tradeKey);
      const rows = Array.isArray(payload?.history) ? payload.history : [];
      modalBody.innerHTML = rows.length
        ? rows.map(row => `
          <div class="active-modal-row"><span>${row.ts || ''}</span><strong>${row.event || ''} ${row.meta?.reason ? '• ' + row.meta.reason : ''}</strong></div>
        `).join('')
        : '<div class="active-modal-note">No history found.</div>';
      modalEl.style.display = 'flex';
    }catch(err){
      setError(String(err?.message || err || 'Failed to load trade history'));
    }
  }

  async function refresh(){
    try{
      setError('');
      refreshBtn.disabled = true;
      const payload = await api.getLifecycleTrades(currentState);
      const rows = Array.isArray(payload?.trades) ? payload.trades : [];
      bodyEl.innerHTML = rows.length
        ? rows.map(row => `
          <tr>
            <td class="risk-key-cell">${row.trade_key || ''}</td>
            <td>${row.symbol || 'N/A'}</td>
            <td>${row.strategy || 'N/A'}</td>
            <td>${row.state || 'N/A'}</td>
            <td>${row.updated_at || 'N/A'}</td>
            <td><button class="btn" data-history-key="${row.trade_key || ''}">History</button></td>
          </tr>
        `).join('')
        : '<tr><td colspan="6" class="loading">No trades in this state.</td></tr>';

      bodyEl.querySelectorAll('[data-history-key]').forEach(btn => {
        btn.addEventListener('click', () => openHistory(btn.getAttribute('data-history-key')));
      });
    }catch(err){
      setError(String(err?.message || err || 'Failed to load lifecycle trades'));
    }finally{
      refreshBtn.disabled = false;
    }
  }

  tabsEl.querySelectorAll('[data-state]').forEach(btn => {
    btn.addEventListener('click', () => {
      currentState = String(btn.getAttribute('data-state') || 'WATCHLIST').toUpperCase();
      tabsEl.querySelectorAll('[data-state]').forEach(node => node.style.opacity = node === btn ? '1' : '0.8');
      refresh();
    });
  });

  refreshBtn.addEventListener('click', () => refresh());
  closeBtn.addEventListener('click', () => { modalEl.style.display = 'none'; });
  modalEl.addEventListener('click', (event) => {
    if(event.target === modalEl){
      modalEl.style.display = 'none';
    }
  });

  refresh();
};
