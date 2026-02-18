window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initAdminDataWorkbench = function initAdminDataWorkbench(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;

  const tradeKeyInput = scope.querySelector('#adminWorkbenchTradeKeyInput');
  const loadBtn = scope.querySelector('#adminWorkbenchLoadBtn');
  const recentBtn = scope.querySelector('#adminWorkbenchRecentBtn');
  const errorEl = scope.querySelector('#adminWorkbenchError');
  const metaEl = scope.querySelector('#adminWorkbenchMeta');
  const jsonEl = scope.querySelector('#adminWorkbenchJson');
  const jsonCopyBtn = scope.querySelector('#adminWorkbenchCopyJsonBtn');
  const rawToggle = scope.querySelector('#adminWorkbenchRawToggle');
  const tradeCardHost = scope.querySelector('#adminWorkbenchTradeCard');
  const recentListEl = scope.querySelector('#adminWorkbenchRecent');
  const emptyEl = scope.querySelector('#adminWorkbenchEmpty');
  const layoutEl = scope.querySelector('#adminWorkbenchLayout');

  if(!tradeKeyInput || !loadBtn || !errorEl || !metaEl || !jsonEl || !jsonCopyBtn || !rawToggle || !tradeCardHost || !recentListEl || !emptyEl || !layoutEl){
    return;
  }

  let latestPayload = null;

  function escapeHtml(value){
    return String(value || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function fmtNum(value, digits = 2, prefix = '', suffix = ''){
    const n = toFiniteNumber(value);
    if(!Number.isFinite(n)) return 'N/A';
    return `${prefix}${n.toFixed(digits)}${suffix}`;
  }

  function fmtPct(value, digits = 1){
    const n = toFiniteNumber(value);
    if(!Number.isFinite(n)) return 'N/A';
    return `${(n * 100).toFixed(digits)}%`;
  }

  function toFiniteNumber(value){
    if(value === null || value === undefined || typeof value === 'boolean') return null;
    if(typeof value === 'string' && value.trim() === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function metricNumber(trade, computedKey, ...legacyKeys){
    const computed = (trade && typeof trade.computed === 'object') ? trade.computed : {};
    const fromComputed = toFiniteNumber(computed?.[computedKey]);
    if(fromComputed !== null) return fromComputed;
    for(const key of legacyKeys){
      const value = toFiniteNumber(trade?.[key]);
      if(value !== null) return value;
    }
    return null;
  }

  function metricDollars(trade, computedKey, ...legacyKeys){
    const value = metricNumber(trade, computedKey, ...legacyKeys);
    return value;
  }

  function formatTradeType(value){
    const text = String(value || 'trade').replaceAll('_', ' ').trim();
    return text.replace(/\b\w/g, (ch) => ch.toUpperCase());
  }

  function parseHashRouteAndQuery(){
    const hash = String(window.location.hash || '#/home');
    const hashBody = hash.startsWith('#/') ? hash.slice(2) : hash.replace(/^#/, '');
    const [pathRaw, queryRaw = ''] = hashBody.split('?');
    const params = new URLSearchParams(queryRaw || '');
    return {
      path: String(pathRaw || '').trim(),
      params,
    };
  }

  function setRouteTradeKey(tradeKey){
    const parsed = parseHashRouteAndQuery();
    const params = new URLSearchParams(parsed.params.toString());
    if(tradeKey){
      params.set('trade_key', tradeKey);
    }else{
      params.delete('trade_key');
    }
    const q = params.toString();
    const nextHash = `#/${parsed.path || 'admin/data-workbench'}${q ? `?${q}` : ''}`;
    if(window.location.hash !== nextHash){
      try{
        window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${nextHash}`);
      }catch(_err){
        window.location.hash = nextHash;
      }
    }
  }

  function setError(text){
    if(!text){
      errorEl.style.display = 'none';
      errorEl.textContent = '';
      return;
    }
    errorEl.style.display = 'block';
    errorEl.textContent = String(text);
  }

  function setEmptyState(on){
    emptyEl.style.display = on ? 'block' : 'none';
    layoutEl.style.display = on ? 'none' : 'grid';
  }

  function renderTradeCard(trade, payload){
    if(!trade || typeof trade !== 'object'){
      tradeCardHost.innerHTML = '<div class="loading">No trade loaded yet.</div>';
      return;
    }

    const warnings = Array.isArray(payload?.trade_json?.validation_warnings) ? payload.trade_json.validation_warnings : [];
    const warningPill = warnings.length ? `<span class="data-warning-pill">${escapeHtml(`${warnings.length} warning${warnings.length === 1 ? '' : 's'}`)}</span>` : '';

    tradeCardHost.innerHTML = `
      <div class="trade-card" data-idx="0">
        <div class="trade-header">
          <div class="trade-header-center">
            <div class="trade-type">${escapeHtml(formatTradeType(trade.spread_type || trade.strategy || trade.strategy_id))}</div>
            <div class="trade-subtitle">
              <span class="underlying-symbol">${escapeHtml(trade.underlying || trade.underlying_symbol || trade.symbol || '')}</span>
              <span class="trade-strikes-inline">${escapeHtml(String(trade.short_strike ?? 'NA'))}/${escapeHtml(String(trade.long_strike ?? 'NA'))}</span>
              <span class="underlying-price">(${fmtNum(metricNumber(trade, 'underlying_price', 'underlying_price'), 2)})</span>
            </div>
            <div class="trade-rank-line">Rank Score: ${fmtPct(metricNumber(trade, 'rank_score', 'rank_score', 'composite_score'), 1)}</div>
            <div style="margin-top:4px;display:flex;align-items:center;gap:6px;opacity:0.86;font-size:11px;">
              <span>ID: ${escapeHtml(String(trade.trade_key || 'N/A'))}</span>
            </div>
          </div>
          <div class="trade-header-right">${warningPill}</div>
        </div>

        <div class="trade-collapsible">
          <div class="trade-body">
            <div class="section section-core">
              <div class="section-title">CORE METRICS</div>
              <div class="metric-grid">
                <div class="metric"><div class="metric-label">Max Profit</div><div class="metric-value positive">${fmtNum(metricDollars(trade, 'max_profit', 'max_profit_per_contract', 'max_profit_per_share', 'max_profit'), 2, '$')}</div></div>
                <div class="metric"><div class="metric-label">Max Loss</div><div class="metric-value negative">${fmtNum(metricDollars(trade, 'max_loss', 'max_loss_per_contract', 'max_loss_per_share', 'max_loss'), 2, '$')}</div></div>
                <div class="metric"><div class="metric-label">Probability</div><div class="metric-value neutral">${fmtPct(metricNumber(trade, 'pop', 'p_win_used', 'pop_delta_approx', 'pop'), 1)}</div></div>
                <div class="metric"><div class="metric-label">Return on Risk</div><div class="metric-value neutral">${fmtPct(metricNumber(trade, 'return_on_risk', 'return_on_risk'), 1)}</div></div>
                <div class="metric"><div class="metric-label">Expected Value</div><div class="metric-value neutral">${fmtNum(metricDollars(trade, 'expected_value', 'ev_per_contract', 'ev_per_share', 'expected_value', 'ev'), 2, '$')}</div></div>
                <div class="metric"><div class="metric-label">Composite</div><div class="metric-value neutral">${fmtNum(metricNumber(trade, 'trade_quality_score', 'composite_score', 'trade_quality_score'), 2)}</div></div>
              </div>
            </div>

            <div class="section section-details">
              <div class="section-title">TRADE DETAILS</div>
              <div class="trade-details">
                <div class="detail-row"><span class="detail-label">Expiration</span><span class="detail-value">${escapeHtml(String(trade.expiration || 'N/A'))}</span></div>
                <div class="detail-row"><span class="detail-label">DTE</span><span class="detail-value">${escapeHtml(String(trade.dte ?? 'N/A'))}</span></div>
                <div class="detail-row"><span class="detail-label">Break Even</span><span class="detail-value">${fmtNum(metricNumber(trade, 'break_even', 'break_even'), 2, '$')}</span></div>
                <div class="detail-row"><span class="detail-label">Net Credit / Debit</span><span class="detail-value">${fmtNum(metricNumber(trade, 'net_credit', 'net_credit'), 2, '$')} / ${fmtNum(metricNumber(trade, 'net_debit', 'net_debit'), 2, '$')}</span></div>
                <div class="detail-row"><span class="detail-label">IV/RV Ratio</span><span class="detail-value">${fmtNum(metricNumber(trade, 'iv_rv_ratio', 'iv_rv_ratio'), 2)}</span></div>
                <div class="detail-row"><span class="detail-label">Bid/Ask Spread %</span><span class="detail-value">${fmtPct(metricNumber(trade, 'bid_ask_pct', 'bid_ask_spread_pct'), 2)}</span></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function runMetricFormattingSanityCheck(){
    if(window.__benTradeAdminMetricNullSanityChecked) return;
    window.__benTradeAdminMetricNullSanityChecked = true;

    const emptyTrade = { computed: {}, details: {} };
    const checks = [
      fmtNum(metricDollars(emptyTrade, 'max_profit', 'max_profit_per_contract', 'max_profit_per_share', 'max_profit'), 2, '$') === 'N/A',
      fmtNum(metricDollars(emptyTrade, 'max_loss', 'max_loss_per_contract', 'max_loss_per_share', 'max_loss'), 2, '$') === 'N/A',
      fmtPct(metricNumber(emptyTrade, 'pop', 'p_win_used', 'pop_delta_approx', 'pop'), 1) === 'N/A',
      fmtPct(metricNumber(emptyTrade, 'return_on_risk', 'return_on_risk'), 1) === 'N/A',
      fmtNum(metricDollars(emptyTrade, 'expected_value', 'ev_per_contract', 'ev_per_share', 'expected_value', 'ev'), 2, '$') === 'N/A',
    ];

    if(!checks.every(Boolean)){
      console.warn('[dev-sanity] Admin Data Workbench metrics should render N/A for missing values, not 0.');
    }
  }
  runMetricFormattingSanityCheck();

  function getJsonText(){
    if(!latestPayload) return '{}';
    if(rawToggle.checked){
      return JSON.stringify(latestPayload.trade_json || {}, null, 0);
    }
    return JSON.stringify(latestPayload.trade_json || {}, null, 2);
  }

  function renderJson(){
    jsonEl.textContent = getJsonText();
  }

  async function copyJson(){
    const text = getJsonText();
    try{
      await navigator.clipboard.writeText(text);
      metaEl.textContent = 'JSON copied to clipboard.';
    }catch(_err){
      const ta = doc.createElement('textarea');
      ta.value = text;
      doc.body.appendChild(ta);
      ta.select();
      try{ doc.execCommand('copy'); }catch(_e){}
      doc.body.removeChild(ta);
      metaEl.textContent = 'JSON copied.';
    }
  }

  function endpointForTrade(tradeKey){
    return `/api/admin/data-workbench/trade?trade_key=${encodeURIComponent(tradeKey)}`;
  }

  async function fetchTrade(tradeKey){
    const response = await fetch(endpointForTrade(tradeKey), { method: 'GET' });
    const body = await response.json().catch(() => ({}));

    if(!response.ok){
      const status = response.status;
      const message = body?.error?.message || body?.detail || 'Request failed';
      const err = new Error(message);
      err.status = status;
      throw err;
    }

    return body;
  }

  async function fetchRecent(){
    try{
      const response = await fetch('/api/admin/data-workbench/search?limit=50', { method: 'GET' });
      if(!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json().catch(() => ({}));
      return Array.isArray(payload?.items) ? payload.items : [];
    }catch(_err){
      return [];
    }
  }

  function renderRecent(items){
    if(!Array.isArray(items) || !items.length){
      recentListEl.innerHTML = '<div class="stock-note">No recent trade keys found.</div>';
      return;
    }

    recentListEl.innerHTML = items.map((item) => {
      const tradeKey = String(item?.trade_key || '').trim();
      const ts = String(item?.timestamp || 'n/a');
      const source = String(item?.source || 'unknown');
      return `<button type="button" data-action="recent-load" data-trade-key="${escapeHtml(tradeKey)}">${escapeHtml(tradeKey)} • ${escapeHtml(source)} • ${escapeHtml(ts)}</button>`;
    }).join('');
  }

  async function loadTradeByKey(tradeKey, opts = {}){
    const key = String(tradeKey || '').trim();
    tradeKeyInput.value = key;

    if(!key){
      latestPayload = null;
      setError('');
      metaEl.textContent = '';
      tradeCardHost.innerHTML = '<div class="loading">No trade loaded yet.</div>';
      jsonEl.textContent = '{}';
      setEmptyState(true);
      const recent = await fetchRecent();
      renderRecent(recent);
      return;
    }

    setError('');
    metaEl.textContent = 'Loading trade...';
    tradeCardHost.innerHTML = '<div class="loading">Loading trade details...</div>';

    try{
      const payload = await fetchTrade(key);
      latestPayload = payload;
      renderJson();
      renderTradeCard(payload.trade || {}, payload);
      const whereFound = Array.isArray(payload?.sources?.where_found) ? payload.sources.where_found.join(', ') : 'unknown';
      metaEl.textContent = `Loaded ${payload.trade_key || key} • source: ${whereFound}`;
      setEmptyState(false);
      if(opts.updateHash !== false){
        setRouteTradeKey(payload.trade_key || key);
      }
    }catch(err){
      latestPayload = null;
      renderJson();
      tradeCardHost.innerHTML = '<div class="loading">Unable to render trade details.</div>';
      if(Number(err?.status) === 404){
        setError(`Trade not found for trade_key: ${key}`);
      }else{
        setError(`Failed to load trade: ${String(err?.message || 'request failed')}`);
      }
      setEmptyState(true);
      const recent = await fetchRecent();
      renderRecent(recent);
    }
  }

  loadBtn.addEventListener('click', () => {
    loadTradeByKey(tradeKeyInput.value);
  });

  tradeKeyInput.addEventListener('keydown', (event) => {
    if(event.key !== 'Enter') return;
    event.preventDefault();
    loadTradeByKey(tradeKeyInput.value);
  });

  rawToggle.addEventListener('change', () => {
    renderJson();
  });

  jsonCopyBtn.addEventListener('click', () => {
    copyJson();
  });

  recentBtn.addEventListener('click', async () => {
    const rows = await fetchRecent();
    renderRecent(rows);
    metaEl.textContent = rows.length ? `Loaded ${rows.length} recent trade keys` : 'No recent trade keys available';
  });

  recentListEl.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action="recent-load"]');
    if(!button) return;
    const tradeKey = button.getAttribute('data-trade-key') || '';
    loadTradeByKey(tradeKey);
  });

  const initQuery = parseHashRouteAndQuery().params.get('trade_key') || '';
  if(initQuery){
    loadTradeByKey(initQuery, { updateHash: false });
  }else{
    loadTradeByKey('', { updateHash: false });
  }
};
