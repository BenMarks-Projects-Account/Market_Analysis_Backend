window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initDataHealth = function initDataHealth(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const DATA_HEALTH_URL = '/api/admin/data-health';
  const DATA_SOURCE_URL = '/api/admin/platform/data-source';
  const MODEL_SOURCE_URL = '/api/admin/platform/model-source';

  const refreshBtn = scope.querySelector('#dhRefreshBtn');
  const errorEl = scope.querySelector('#dhError');
  const providerTilesEl = scope.querySelector('#dhProviderTiles');
  const eventsBodyEl = scope.querySelector('#dhEventsBody');
  const topCodesEl = scope.querySelector('#dhTopCodes');
  const severityCountsEl = scope.querySelector('#dhSeverityCounts');
  const dataSourceToggleEl = scope.querySelector('#dhDataSourceToggle');
  const dataSourceMetaEl = scope.querySelector('#dhDataSourceMeta');
  const dataSourceWarningEl = scope.querySelector('#dhDataSourceWarning');
  const captureBtn = scope.querySelector('#dhCaptureBtn');
  const captureStatusEl = scope.querySelector('#dhCaptureStatus');

  // Scanner symbols cached from the data-source response
  let _scannerSymbols = ['SPY', 'QQQ', 'IWM', 'DIA', 'XSP', 'RUT', 'NDX'];

  if(!refreshBtn || !providerTilesEl || !eventsBodyEl || !topCodesEl || !severityCountsEl){
    return;
  }

  function escapeHtml(value){
    return String(value || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function setError(text){
    if(!errorEl) return;
    if(!text){
      errorEl.style.display = 'none';
      errorEl.innerHTML = '';
      return;
    }
    errorEl.style.display = 'block';
    errorEl.innerHTML = String(text || '');
  }

  function buildErrorCard(url, status, snippet){
    const safeUrl = escapeHtml(url || DATA_HEALTH_URL);
    const safeStatus = (status === null || status === undefined) ? 'network_error' : escapeHtml(String(status));
    const safeSnippet = escapeHtml(String(snippet || '').trim() || 'No response body');
    return `
      <div><strong>Failed to load Data Health</strong></div>
      <div style="margin-top:6px;">URL: ${safeUrl}</div>
      <div>Status: ${safeStatus}</div>
      <div style="margin-top:6px;">Response:</div>
      <pre style="white-space:pre-wrap; word-break:break-word; margin:6px 0 0;">${safeSnippet}</pre>
    `;
  }

  // ── Platform Data Source toggle ──────────────────────────────────────

  let _currentMode = 'live';

  function formatTimestamp(isoStr){
    if(!isoStr) return '';
    try{
      const d = new Date(isoStr);
      return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'medium' });
    }catch(_e){
      return String(isoStr);
    }
  }

  function renderDataSourceState(state){
    if(!dataSourceToggleEl) return;
    const mode = (state && state.data_source_mode) || 'live';
    _currentMode = mode;

    // Cache scanner symbols from backend
    if(state && Array.isArray(state.scanner_symbols) && state.scanner_symbols.length){
      _scannerSymbols = state.scanner_symbols;
    }

    const buttons = dataSourceToggleEl.querySelectorAll('.dh-ds-btn');
    buttons.forEach((btn) => {
      const btnMode = btn.getAttribute('data-mode');
      const isActive = btnMode === mode;
      btn.setAttribute('aria-pressed', String(isActive));
      btn.classList.toggle('dh-ds-btn--active', isActive);
    });

    if(dataSourceMetaEl){
      let meta = '';
      if(state && state.updated_at){
        meta = 'Last changed: ' + formatTimestamp(state.updated_at);
      }
      if(state && mode === 'snapshot' && !state.has_snapshots){
        meta += (meta ? ' · ' : '') + 'No snapshots on disk';
      }
      dataSourceMetaEl.textContent = meta;
    }

    if(dataSourceWarningEl){
      if(mode === 'snapshot' && state && !state.has_snapshots){
        dataSourceWarningEl.style.display = 'block';
        dataSourceWarningEl.textContent = 'Warning: No snapshot files found on disk. Scans in Offline mode will produce no results. Capture snapshots first.';
      }else{
        dataSourceWarningEl.style.display = 'none';
        dataSourceWarningEl.textContent = '';
      }
    }
  }

  async function fetchDataSourceState(){
    try{
      const res = await fetch(DATA_SOURCE_URL, { method: 'GET' });
      if(!res.ok) return null;
      return await res.json();
    }catch(_e){
      return null;
    }
  }

  async function setDataSourceMode(mode){
    if(!dataSourceToggleEl) return;
    const buttons = dataSourceToggleEl.querySelectorAll('.dh-ds-btn');
    buttons.forEach((btn) => { btn.disabled = true; });

    try{
      const res = await fetch(DATA_SOURCE_URL, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data_source_mode: mode }),
      });

      if(!res.ok){
        const body = await res.text().catch(() => '');
        if(dataSourceWarningEl){
          dataSourceWarningEl.style.display = 'block';
          dataSourceWarningEl.textContent = 'Failed to update data source: ' + (body || res.statusText);
        }
        return;
      }

      const result = await res.json();
      renderDataSourceState(result);

      // Refresh the full data health panel
      loadDataHealth();
    }catch(err){
      if(dataSourceWarningEl){
        dataSourceWarningEl.style.display = 'block';
        dataSourceWarningEl.textContent = 'Failed to update data source: ' + (err.message || 'Network error');
      }
    }finally{
      buttons.forEach((btn) => { btn.disabled = false; });
    }
  }

  if(dataSourceToggleEl){
    dataSourceToggleEl.addEventListener('click', (e) => {
      const btn = e.target.closest('.dh-ds-btn');
      if(!btn) return;
      const mode = btn.getAttribute('data-mode');
      if(mode && mode !== _currentMode){
        setDataSourceMode(mode);
      }
    });
  }

  // ── Snapshot Capture button ─────────────────────────────────────────

  async function captureSnapshot(){
    if(!captureBtn || !captureStatusEl) return;
    captureBtn.disabled = true;
    captureBtn.classList.add('dh-capture-btn--loading');
    captureStatusEl.style.display = 'block';
    captureStatusEl.className = 'dh-capture-status dh-capture-status--progress';
    captureStatusEl.textContent = 'Capturing snapshot for ' + _scannerSymbols.join(', ') + '…';

    try{
      const res = await fetch('/api/admin/snapshots/capture', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          strategy_id: 'credit_spread',
          symbols: _scannerSymbols,
          preset_name: 'balanced',
          dte_min: 3,
          dte_max: 45,
        }),
      });

      if(!res.ok){
        const body = await res.text().catch(() => '');
        let msg = 'Capture failed';
        try{
          const err = JSON.parse(body);
          msg = (err.error && err.error.message) || msg;
        }catch(_e){ /* ignore */ }
        captureStatusEl.className = 'dh-capture-status dh-capture-status--error';
        captureStatusEl.textContent = msg;
        return;
      }

      const result = await res.json();
      const dur = result.capture_duration_seconds
        ? result.capture_duration_seconds.toFixed(1) + 's'
        : '';
      const complete = result.completeness && result.completeness.complete;
      captureStatusEl.className = 'dh-capture-status ' + (complete ? 'dh-capture-status--ok' : 'dh-capture-status--warn');
      captureStatusEl.textContent =
        'Captured ' + result.trace_id +
        ' — ' + (result.chains_captured || 0) + ' chains, ' +
        (result.symbols || []).length + ' symbols' +
        (dur ? ' in ' + dur : '') +
        (complete ? '' : ' (incomplete — check manifest)');

      // Refresh data source state to pick up the new snapshot
      const dsState = await fetchDataSourceState();
      if(dsState) renderDataSourceState(dsState);
    }catch(err){
      captureStatusEl.className = 'dh-capture-status dh-capture-status--error';
      captureStatusEl.textContent = 'Capture failed: ' + (err.message || 'Network error');
    }finally{
      captureBtn.disabled = false;
      captureBtn.classList.remove('dh-capture-btn--loading');
    }
  }

  if(captureBtn){
    captureBtn.addEventListener('click', () => captureSnapshot());
  }

  // ── Model Source toggle ──────────────────────────────────────────────

  const modelSourceToggleEl = scope.querySelector('#dhModelSourceToggle');
  const modelSourceMetaEl = scope.querySelector('#dhModelSourceMeta');
  let _activeModelSource = 'local';

  function renderModelSourceState(state){
    if(!modelSourceToggleEl) return;
    const sources = (state && state.sources) || {};
    const active = (state && state.active_source) || 'local';
    _activeModelSource = active;

    // Build buttons dynamically from source list
    modelSourceToggleEl.innerHTML = '';
    const keys = Object.keys(sources);
    keys.forEach((key) => {
      const src = sources[key];
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'dh-ds-btn';
      btn.setAttribute('data-source', key);
      btn.setAttribute('aria-pressed', String(key === active));
      if(key === active) btn.classList.add('dh-ds-btn--active');
      if(!src.enabled) btn.disabled = true;

      // Dot color: green for local, blue for model_machine, grey for disabled
      const dot = document.createElement('span');
      dot.className = 'dh-ds-dot';
      if(!src.enabled){
        dot.style.background = '#666';
      }else if(key === 'local'){
        dot.className = 'dh-ds-dot dh-ds-dot--live';
      }else{
        dot.style.background = 'var(--cyan, #00eaff)';
        dot.style.boxShadow = '0 0 6px rgba(0,234,255,0.45)';
      }
      btn.appendChild(dot);
      btn.appendChild(document.createTextNode(' ' + escapeHtml(src.name)));
      modelSourceToggleEl.appendChild(btn);
    });

    if(modelSourceMetaEl){
      const activeName = (sources[active] && sources[active].name) || active;
      const endpoint = (state && state.active_endpoint) || '';
      const displayEndpoint = endpoint ? endpoint.replace(/^https?:\/\//, '').replace(/\/v1\/chat\/completions$/, '') : 'N/A';
      modelSourceMetaEl.textContent = 'Active: ' + activeName + ' · Endpoint: ' + displayEndpoint;
    }
  }

  async function fetchModelSourceState(){
    try{
      const res = await fetch(MODEL_SOURCE_URL, { method: 'GET' });
      if(!res.ok) return null;
      return await res.json();
    }catch(_e){
      return null;
    }
  }

  async function setModelSource(source){
    if(!modelSourceToggleEl) return;
    const buttons = modelSourceToggleEl.querySelectorAll('.dh-ds-btn');
    buttons.forEach((btn) => { btn.disabled = true; });

    try{
      const res = await fetch(MODEL_SOURCE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: source }),
      });
      if(res.ok){
        // Re-fetch full state so buttons and meta update correctly
        const updated = await fetchModelSourceState();
        if(updated) renderModelSourceState(updated);

        // Reset + refresh the global source health store so the sidebar
        // indicator re-probes the NEW source instead of showing stale GREEN
        // from the old source.
        if(window.BenTradeSourceHealthStore){
          if(window.BenTradeSourceHealthStore.resetCache) window.BenTradeSourceHealthStore.resetCache();
          window.BenTradeSourceHealthStore.fetchSourceHealth({ force: true }).catch(function(){});
        }
      }
    }catch(_err){
      // silent
    }finally{
      const refreshed = modelSourceToggleEl.querySelectorAll('.dh-ds-btn');
      refreshed.forEach((btn) => {
        const src = btn.getAttribute('data-source');
        // Re-enable only if the source is enabled (not premium_online)
        btn.disabled = false;
      });
    }
  }

  if(modelSourceToggleEl){
    modelSourceToggleEl.addEventListener('click', (e) => {
      const btn = e.target.closest('.dh-ds-btn');
      if(!btn || btn.disabled) return;
      const source = btn.getAttribute('data-source');
      if(source && source !== _activeModelSource){
        setModelSource(source);
      }
    });
  }

  // ── Data Health fetching ─────────────────────────────────────────────

  async function fetchDataHealth(){
    const response = await fetch(DATA_HEALTH_URL, { method: 'GET' });
    const responseText = await response.text();
    let payload = {};
    if(responseText){
      try{
        payload = JSON.parse(responseText);
      }catch(_err){
        payload = {};
      }
    }
    if(!response.ok){
      const err = new Error(`Request failed (${response.status})`);
      err.status = response.status;
      err.url = DATA_HEALTH_URL;
      err.bodySnippet = String(responseText || '').slice(0, 400);
      throw err;
    }
    return payload;
  }

  function statusPill(status){
    const normalized = String(status || '').toLowerCase();
    if(normalized === 'green') return 'diag-pill-green';
    if(normalized === 'yellow') return 'diag-pill-yellow';
    return 'diag-pill-red';
  }

  function severityLabel(severity){
    const normalized = String(severity || 'warn').toLowerCase();
    return normalized === 'error' ? 'ERROR' : 'WARN';
  }

  function renderProviders(sourceHealth){
    const providers = (sourceHealth && typeof sourceHealth === 'object') ? sourceHealth : {};
    const keys = Object.keys(providers);
    if(!keys.length){
      providerTilesEl.innerHTML = '<div class="stock-note">No provider health available.</div>';
      return;
    }

    providerTilesEl.innerHTML = keys.map((name) => {
      const row = providers[name] || {};
      const status = String(row.status || 'red').toLowerCase();
      const message = String(row.message || 'unavailable');
      const pillClass = statusPill(status);
      return `
        <div class="statTile">
          <div class="statLabel" data-metric="data_provider_status">${escapeHtml(name.toUpperCase())}</div>
          <div class="diag-pill ${pillClass}">${escapeHtml(status.toUpperCase())}</div>
          <div class="stock-note" style="margin-top:6px;">${escapeHtml(message)}</div>
        </div>
      `;
    }).join('');
  }

  function renderEvents(events){
    const rows = Array.isArray(events) ? events : [];
    if(!rows.length){
      eventsBodyEl.innerHTML = '<tr><td colspan="5" class="loading">No validation events yet.</td></tr>';
      return;
    }

    eventsBodyEl.innerHTML = rows.slice().reverse().map((event) => {
      const context = (event && typeof event.context === 'object') ? event.context : {};
      const symbolOrTrade = context.symbol || context.trade_key || '—';
      return `
        <tr>
          <td>${escapeHtml(String(event.ts || ''))}</td>
          <td>${escapeHtml(severityLabel(event.severity))}</td>
          <td>${escapeHtml(String(event.code || 'UNKNOWN'))}</td>
          <td>${escapeHtml(String(symbolOrTrade || '—'))}</td>
          <td>${escapeHtml(String(event.message || ''))}</td>
        </tr>
      `;
    }).join('');
  }

  function renderRollups(rollups){
    const topCodes = Array.isArray(rollups?.top_codes) ? rollups.top_codes : [];
    if(!topCodes.length){
      topCodesEl.innerHTML = '<div class="stock-note">No code rollups yet.</div>';
    }else{
      topCodesEl.innerHTML = topCodes.map((item) => {
        const code = String(item?.code || 'UNKNOWN');
        const count = Number(item?.count || 0);
        return `<div class="stock-note">• ${escapeHtml(code)}: ${count}</div>`;
      }).join('');
    }

    const bySeverity = (rollups && typeof rollups.counts_by_severity === 'object')
      ? rollups.counts_by_severity
      : {};
    const warnCount = Number(bySeverity.warn || 0);
    const errorCount = Number(bySeverity.error || 0);
    severityCountsEl.innerHTML = `
      <div class="stock-note">• WARN: ${warnCount}</div>
      <div class="stock-note">• ERROR: ${errorCount}</div>
    `;
  }

  async function loadDataHealth(){
    setError('');
    providerTilesEl.innerHTML = '<div class="loading">Loading provider health…</div>';
    eventsBodyEl.innerHTML = '<tr><td colspan="5" class="loading">Loading validation events…</td></tr>';
    topCodesEl.innerHTML = '<div class="loading">Loading rollups…</div>';
    severityCountsEl.innerHTML = '<div class="loading">Loading rollups…</div>';

    // Load data source state in parallel
    const dataSourcePromise = fetchDataSourceState();

    try{
      const payload = await fetchDataHealth();
      renderProviders(payload?.source_health || {});
      renderEvents(payload?.validation_events || []);
      renderRollups(payload?.rollups || {});
    }catch(err){
      const message = buildErrorCard(
        err?.url || DATA_HEALTH_URL,
        err?.status,
        err?.bodySnippet || err?.message || 'Request failed',
      );
      setError(message);
      providerTilesEl.innerHTML = '<div class="stock-note">Provider health unavailable.</div>';
      eventsBodyEl.innerHTML = '<tr><td colspan="5" class="loading">Validation events unavailable.</td></tr>';
      topCodesEl.innerHTML = '<div class="stock-note">Rollups unavailable.</div>';
      severityCountsEl.innerHTML = '<div class="stock-note">Rollups unavailable.</div>';
    }

    // Render data source toggle state
    try{
      const dsState = await dataSourcePromise;
      if(dsState){
        renderDataSourceState(dsState);
      }
    }catch(_e){
      // non-critical
    }

    // Render model source toggle state
    try{
      const msState = await fetchModelSourceState();
      if(msState){
        renderModelSourceState(msState);
      }
    }catch(_e){
      // non-critical
    }
  }

  refreshBtn.addEventListener('click', () => {
    loadDataHealth();
  });

  loadDataHealth();
};
