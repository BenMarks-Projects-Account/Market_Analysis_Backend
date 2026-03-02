window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initDataHealth = function initDataHealth(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const DATA_HEALTH_URL = '/api/admin/data-health';
  const DATA_SOURCE_URL = '/api/admin/platform/data-source';

  const refreshBtn = scope.querySelector('#dhRefreshBtn');
  const errorEl = scope.querySelector('#dhError');
  const providerTilesEl = scope.querySelector('#dhProviderTiles');
  const eventsBodyEl = scope.querySelector('#dhEventsBody');
  const topCodesEl = scope.querySelector('#dhTopCodes');
  const severityCountsEl = scope.querySelector('#dhSeverityCounts');
  const dataSourceToggleEl = scope.querySelector('#dhDataSourceToggle');
  const dataSourceMetaEl = scope.querySelector('#dhDataSourceMeta');
  const dataSourceWarningEl = scope.querySelector('#dhDataSourceWarning');

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

    const buttons = dataSourceToggleEl.querySelectorAll('.ds-toggle-btn');
    buttons.forEach((btn) => {
      const btnMode = btn.getAttribute('data-mode');
      const isActive = btnMode === mode;
      btn.setAttribute('aria-pressed', String(isActive));
      btn.classList.toggle('ds-toggle-active', isActive);
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
    const buttons = dataSourceToggleEl.querySelectorAll('.ds-toggle-btn');
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
      const btn = e.target.closest('.ds-toggle-btn');
      if(!btn) return;
      const mode = btn.getAttribute('data-mode');
      if(mode && mode !== _currentMode){
        setDataSourceMode(mode);
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
          <div class="statLabel">${escapeHtml(name.toUpperCase())}</div>
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
  }

  refreshBtn.addEventListener('click', () => {
    loadDataHealth();
  });

  loadDataHealth();
};
