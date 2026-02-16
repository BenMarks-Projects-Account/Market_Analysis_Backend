window.BenTradeSourceHealth = (function(){
  const SOURCE_ORDER = ['Finnhub', 'Yahoo', 'Tradier', 'FRED'];

  function escapeHtml(value){
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function statusClass(status){
    const value = String(status || '').toLowerCase();
    if(value === 'ok' || value === 'green') return 'status-green';
    if(value === 'down' || value === 'red') return 'status-red';
    return 'status-yellow';
  }

  function formatAsOf(iso){
    if(!iso) return '';
    const date = new Date(String(iso));
    if(Number.isNaN(date.getTime())) return '';
    return date.toLocaleTimeString();
  }

  function setAsOf(iso){
    const title = document.getElementById('sourceHealthTitle');
    if(!title) return;
    const base = title.getAttribute('data-base-text') || 'SOURCE HEALTH';
    const at = formatAsOf(iso);
    title.textContent = at ? `${base} • ${at}` : base;
  }

  function renderRows(rows){
    return (rows || []).map((row) => {
      const label = row?.label || 'Unknown';
      const dotClass = row?.statusClass || 'status-yellow';
      const tooltip = row?.tooltip || '';
      return `
        <div class="diagnosticRow">
          <span class="diagnosticLabel">${escapeHtml(label)}</span>
          <span class="status-wrap" tabindex="0">
            <span class="status-dot ${dotClass}"></span>
            <span class="status-tooltip">${escapeHtml(tooltip)}</span>
          </span>
        </div>
      `;
    }).join('');
  }

  function normalizeCanonical(payload){
    const sources = Array.isArray(payload?.sources) ? payload.sources : [];
    const map = new Map();
    sources.forEach((item) => {
      const key = String(item?.name || '').trim();
      if(!key) return;
      map.set(key, item);
    });

    const ordered = SOURCE_ORDER.map((name) => map.get(name)).filter(Boolean);
    const extras = sources.filter((item) => !SOURCE_ORDER.includes(String(item?.name || '').trim()));
    const all = [...ordered, ...extras];

    const rows = all.map((item) => {
      const notes = Array.isArray(item?.notes) ? item.notes.filter(Boolean).map(String) : [];
      return {
        label: item?.name || 'Unknown',
        statusClass: statusClass(item?.status),
        tooltip: notes.length ? notes.join(' • ') : 'No notes',
      };
    });

    return {
      as_of: payload?.as_of || null,
      rows,
    };
  }

  function normalizeSnapshot(snapshot){
    const keyToName = { finnhub: 'Finnhub', yahoo: 'Yahoo', tradier: 'Tradier', fred: 'FRED' };
    const rows = Object.entries(snapshot || {}).map(([source, value]) => ({
      label: keyToName[String(source || '').toLowerCase()] || String(source || '').toUpperCase(),
      statusClass: statusClass(value?.status),
      tooltip: value?.message || 'No message',
    }));
    return { as_of: null, rows };
  }

  function renderFromCanonical(payload){
    const target = document.getElementById('sourceHealthRows');
    if(!target) return;
    const normalized = normalizeCanonical(payload || {});
    target.innerHTML = renderRows(normalized.rows);
    setAsOf(normalized.as_of);
  }

  function renderFromSnapshot(snapshot){
    const target = document.getElementById('sourceHealthRows');
    if(!target) return;
    const normalized = normalizeSnapshot(snapshot || {});
    target.innerHTML = renderRows(normalized.rows);
  }

  return {
    renderRows,
    renderFromSnapshot,
    renderFromCanonical,
  };
})();

window.BenTradeSourceHealthStore = (function(){
  const TTL_MS = 45000;
  const MIN_FORCE_GAP_MS = 5000;
  const state = {
    payload: null,
    fetchedAt: 0,
    inflight: null,
  };

  function isFresh(){
    return state.payload && (Date.now() - state.fetchedAt) < TTL_MS;
  }

  function canForceRefresh(){
    return (Date.now() - state.fetchedAt) >= MIN_FORCE_GAP_MS;
  }

  function render(payload){
    if(window.BenTradeSourceHealth?.renderFromCanonical){
      window.BenTradeSourceHealth.renderFromCanonical(payload || {});
    }
  }

  async function fetchSourceHealth(options){
    const opts = options || {};
    const force = Boolean(opts.force);

    if(!force && isFresh()){
      render(state.payload);
      return state.payload;
    }
    if(force && !canForceRefresh() && state.payload){
      render(state.payload);
      return state.payload;
    }
    if(state.inflight){
      const payload = await state.inflight;
      render(payload);
      return payload;
    }

    state.inflight = (async () => {
      const res = await fetch('/api/health/sources', { cache: 'no-store' });
      if(!res.ok){
        throw new Error(`Source health request failed (${res.status})`);
      }
      const payload = await res.json().catch(() => ({}));
      state.payload = payload;
      state.fetchedAt = Date.now();
      return payload;
    })();

    try{
      const payload = await state.inflight;
      render(payload);
      return payload;
    }catch(err){
      if(state.payload){
        render(state.payload);
        return state.payload;
      }
      throw err;
    }finally{
      state.inflight = null;
    }
  }

  async function refresh(){
    return fetchSourceHealth({ force: true });
  }

  return {
    fetchSourceHealth,
    refresh,
    render,
  };
})();
