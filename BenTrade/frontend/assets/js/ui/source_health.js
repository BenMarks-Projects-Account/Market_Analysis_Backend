window.BenTradeSourceHealth = (function(){
  const SOURCE_ORDER = ['Finnhub', 'Yahoo', 'Tradier', 'FRED'];
  const AI_MODEL_LABEL = 'AI Model';

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
    // AI Model entry is always listed last
    const modelEntry = sources.find((item) => String(item?.name || '').trim() === AI_MODEL_LABEL);
    const extras = sources.filter((item) => {
      const name = String(item?.name || '').trim();
      return !SOURCE_ORDER.includes(name) && name !== AI_MODEL_LABEL;
    });
    const all = [...ordered, ...extras, ...(modelEntry ? [modelEntry] : [])];

    const rows = all.map((item) => {
      const notes = Array.isArray(item?.notes) ? item.notes.filter(Boolean).map(String) : [];
      const name = String(item?.name || 'Unknown');
      const isModel = name === AI_MODEL_LABEL;

      // For model entries, build a richer tooltip with latency and model name
      let tooltip = '';
      if(isModel){
        const latency = item?.latency_ms;
        const parts = [];
        if(item?.status === 'ok' || item?.status === 'green'){
          parts.push('PASS');
        }else{
          parts.push('FAIL');
        }
        if(latency != null) parts.push(latency + ' ms');
        // First note is usually the model name
        if(notes.length) parts.push(notes[0]);
        tooltip = parts.join(' · ');
      }else{
        tooltip = notes.length ? notes.join(' • ') : 'No notes';
      }

      return {
        label: name,
        statusClass: statusClass(item?.status),
        tooltip,
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
  const TTL_MS = 15000;       // reduced from 45s — model health backend cache is 10s
  const MIN_FORCE_GAP_MS = 3000;  // reduced from 5s
  const STALE_LIMIT_MS = 30000;   // discard cached payload older than this
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

  function isStale(){
    return !state.payload || (Date.now() - state.fetchedAt) > STALE_LIMIT_MS;
  }

  function render(payload){
    if(window.BenTradeSourceHealth?.renderFromCanonical){
      window.BenTradeSourceHealth.renderFromCanonical(payload || {});
    }
  }

  /** Render all sources as unknown/red when no data is available. */
  function renderUnknown(){
    var target = document.getElementById('sourceHealthRows');
    if(!target) return;
    target.innerHTML = '<div class="diagnosticRow"><span class="diagnosticLabel" style="opacity:.6">Health check pending…</span></div>';
  }

  function resetCache(){
    state.payload = null;
    state.fetchedAt = 0;
    state.inflight = null;
    if(window.BenTradeDebug) console.log('[source-health-store] cache reset');
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
      if(window.BenTradeDebug){
        var modelEntry = (payload?.sources || []).find(function(s){ return s?.name === 'AI Model'; });
        console.log('[source-health-store] fetched', {
          as_of: payload?.as_of,
          model_status: modelEntry?.status || 'missing',
          model_notes: modelEntry?.notes,
        });
      }
      render(payload);
      return payload;
    }catch(err){
      // Fail CLOSED: do NOT fall back to stale data — show unknown state.
      // Old behaviour returned last cached payload which could be an old GREEN.
      if(window.BenTradeDebug) console.warn('[source-health-store] fetch failed, clearing stale state', err);
      if(isStale()){
        state.payload = null;
        state.fetchedAt = 0;
        renderUnknown();
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
    resetCache,
  };
})();
