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
  const captureBtn = scope.querySelector('#dhCaptureBtn');
  const captureStatusEl = scope.querySelector('#dhCaptureStatus');
  const routingSystemEl = scope.querySelector('#dhRoutingSystemContent');
  const routingProviderTilesEl = scope.querySelector('#dhRoutingProviderTiles');
  const routingRecentBodyEl = scope.querySelector('#dhRoutingRecentBody');
  const routingRefreshConfigBtn = scope.querySelector('#dhRefreshConfigBtn');
  const routingRefreshProvidersBtn = scope.querySelector('#dhRefreshProvidersBtn');
  const routingRefreshRuntimeBtn = scope.querySelector('#dhRefreshRuntimeBtn');
  const routingActionFeedbackEl = scope.querySelector('#dhRoutingActionFeedback');

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

  // ── Unified Model & Routing Mode selector (Step 18) ─────────────────

  const EXECUTION_MODE_URL = '/api/admin/routing/execution-mode';
  const modelSourceToggleEl = scope.querySelector('#dhModelSourceToggle');
  const modelSourceMetaEl = scope.querySelector('#dhModelSourceMeta');
  const modelSourceFeedbackEl = scope.querySelector('#dhModelSourceFeedback');
  let _activeExecutionMode = '';
  let _execModeInFlight = false;

  function renderModelSourceState(state){
    if(!modelSourceToggleEl) return;
    const options = (state && Array.isArray(state.options)) ? state.options : [];
    const active = (state && state.selected_mode) || '';
    _activeExecutionMode = active;

    modelSourceToggleEl.innerHTML = '';

    // Render primary group first, then direct group
    const primaryModes = options.filter(function(o){ return o.group === 'primary'; });
    const directModes = options.filter(function(o){ return o.group === 'direct'; });

    function createModeButton(opt){
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'dh-exec-mode-btn';
      btn.setAttribute('data-mode', opt.mode);
      btn.setAttribute('aria-pressed', String(opt.mode === active));
      btn.setAttribute('title', opt.description || '');
      if(opt.mode === active) btn.classList.add('dh-exec-mode-btn--active');

      var dot = document.createElement('span');
      dot.className = 'dh-ds-dot';
      if(opt.group === 'primary'){
        dot.classList.add('dh-ds-dot--live');
      }else{
        dot.style.background = 'var(--cyan, #00eaff)';
        dot.style.boxShadow = '0 0 6px rgba(0,234,255,0.45)';
      }
      btn.appendChild(dot);
      btn.appendChild(document.createTextNode(' ' + escapeHtml(opt.label)));
      return btn;
    }

    if(primaryModes.length){
      var primaryLabel = document.createElement('span');
      primaryLabel.className = 'dh-exec-mode-group-label';
      primaryLabel.textContent = 'Distributed';
      modelSourceToggleEl.appendChild(primaryLabel);
      primaryModes.forEach(function(opt){ modelSourceToggleEl.appendChild(createModeButton(opt)); });
    }

    if(directModes.length){
      var directLabel = document.createElement('span');
      directLabel.className = 'dh-exec-mode-group-label';
      directLabel.textContent = 'Direct';
      modelSourceToggleEl.appendChild(directLabel);
      directModes.forEach(function(opt){ modelSourceToggleEl.appendChild(createModeButton(opt)); });
    }

    if(modelSourceMetaEl){
      var label = (state && state.display_label) || active;
      var desc = (state && state.description) || '';
      modelSourceMetaEl.textContent = 'Active: ' + label + (desc ? ' — ' + desc : '');
    }
  }

  async function fetchModelSourceState(){
    try{
      var res = await fetch(EXECUTION_MODE_URL, { method: 'GET' });
      if(!res.ok) return null;
      return await res.json();
    }catch(_e){
      return null;
    }
  }

  function showModelSourceFeedback(type, message){
    if(!modelSourceFeedbackEl) return;
    modelSourceFeedbackEl.style.display = 'block';
    modelSourceFeedbackEl.className = 'dh-routing-feedback dh-routing-feedback--' + type;
    modelSourceFeedbackEl.textContent = message;
    clearTimeout(modelSourceFeedbackEl._hideTimer);
    modelSourceFeedbackEl._hideTimer = setTimeout(function(){
      modelSourceFeedbackEl.style.display = 'none';
    }, 6000);
  }

  async function setModelSource(mode){
    if(_execModeInFlight) return;
    _execModeInFlight = true;
    var buttons = modelSourceToggleEl ? modelSourceToggleEl.querySelectorAll('.dh-exec-mode-btn') : [];
    buttons.forEach(function(btn){ btn.disabled = true; });

    try{
      var res = await fetch(EXECUTION_MODE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: mode }),
      });

      if(res.status === 429){
        var body429 = await res.json().catch(function(){ return {}; });
        showModelSourceFeedback('warn', body429.message || 'Rate limited — try again shortly.');
        return;
      }

      if(!res.ok){
        var errBody = await res.json().catch(function(){ return {}; });
        showModelSourceFeedback('error', errBody.message || 'Failed to update routing mode.');
        return;
      }

      // Re-fetch full state so all buttons and meta update correctly
      var updated = await fetchModelSourceState();
      if(updated) renderModelSourceState(updated);

      showModelSourceFeedback('ok', 'Routing mode changed to ' + (updated && updated.display_label || mode) + '.');

      // Refresh routing dashboard to reflect new mode in system status
      try{
        var dashRes = await fetch(ROUTING_CONTROL_URL + '/dashboard?refresh=false&recent_limit=10', { method: 'GET' });
        if(dashRes.ok){
          var dashData = await dashRes.json();
          renderRoutingDashboard(dashData);
        }
      }catch(_e){ /* non-critical */ }
    }catch(err){
      showModelSourceFeedback('error', 'Failed: ' + (err.message || 'Network error'));
    }finally{
      _execModeInFlight = false;
      var refreshedBtns = modelSourceToggleEl ? modelSourceToggleEl.querySelectorAll('.dh-exec-mode-btn') : [];
      refreshedBtns.forEach(function(btn){ btn.disabled = false; });
    }
  }

  if(modelSourceToggleEl){
    modelSourceToggleEl.addEventListener('click', function(e){
      var btn = e.target.closest('.dh-exec-mode-btn');
      if(!btn || btn.disabled) return;
      var mode = btn.getAttribute('data-mode');
      if(mode && mode !== _activeExecutionMode){
        setModelSource(mode);
      }
    });
  }

  // ── Routing operator controls (Step 15) ───────────────────────────────

  const ROUTING_CONTROL_URL = '/api/admin/routing';
  const _routingCtrlBtns = [routingRefreshConfigBtn, routingRefreshProvidersBtn, routingRefreshRuntimeBtn].filter(Boolean);
  let _routingCtrlInFlight = false;

  function setRoutingCtrlButtonsDisabled(disabled){
    _routingCtrlBtns.forEach((btn) => {
      btn.disabled = disabled;
      btn.classList.toggle('dh-routing-ctrl-btn--loading', disabled);
    });
  }

  function showRoutingFeedback(type, message){
    if(!routingActionFeedbackEl) return;
    routingActionFeedbackEl.style.display = 'block';
    routingActionFeedbackEl.className = 'dh-routing-feedback dh-routing-feedback--' + type;
    routingActionFeedbackEl.textContent = message;
    // Auto-hide after 8 seconds
    clearTimeout(routingActionFeedbackEl._hideTimer);
    routingActionFeedbackEl._hideTimer = setTimeout(() => {
      routingActionFeedbackEl.style.display = 'none';
    }, 8000);
  }

  async function routingControlAction(endpoint, label){
    if(_routingCtrlInFlight) return;
    _routingCtrlInFlight = true;
    setRoutingCtrlButtonsDisabled(true);

    try{
      const res = await fetch(ROUTING_CONTROL_URL + '/' + endpoint, { method: 'POST' });
      if(res.status === 429){
        const body = await res.json().catch(() => ({}));
        showRoutingFeedback('warn', label + ': ' + (body.message || 'Rate limited — try again shortly.'));
        return;
      }
      if(!res.ok){
        const body = await res.text().catch(() => '');
        showRoutingFeedback('error', label + ' failed (HTTP ' + res.status + '): ' + (body || res.statusText));
        return;
      }
      const result = await res.json();
      const ts = new Date().toLocaleTimeString();
      showRoutingFeedback('ok', label + ' completed at ' + ts + '.');

      // Re-fetch routing dashboard sections to reflect new state
      try{
        const dashRes = await fetch(ROUTING_CONTROL_URL + '/dashboard?refresh=true&recent_limit=10', { method: 'GET' });
        if(dashRes.ok){
          const dashData = await dashRes.json();
          renderRoutingDashboard(dashData);
        }
      }catch(_e){ /* non-critical */ }
    }catch(err){
      showRoutingFeedback('error', label + ' failed: ' + (err.message || 'Network error'));
    }finally{
      _routingCtrlInFlight = false;
      setRoutingCtrlButtonsDisabled(false);
    }
  }

  if(routingRefreshConfigBtn){
    routingRefreshConfigBtn.addEventListener('click', () => routingControlAction('refresh-config', 'Config refresh'));
  }
  if(routingRefreshProvidersBtn){
    routingRefreshProvidersBtn.addEventListener('click', () => routingControlAction('refresh-providers', 'Provider refresh'));
  }
  if(routingRefreshRuntimeBtn){
    routingRefreshRuntimeBtn.addEventListener('click', () => routingControlAction('refresh-runtime', 'Runtime refresh'));
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

  // ── Routing dashboard rendering (Step 13) ────────────────────────────

  const SEVERITY_PILL_MAP = {
    healthy: 'diag-pill-green',
    warning: 'diag-pill-yellow',
    caution: 'diag-pill-yellow',
    offline: 'diag-pill-grey',
    error: 'diag-pill-red',
  };

  const EXEC_STATUS_PILL = {
    success: 'diag-pill-green',
    failed: 'diag-pill-red',
    timeout: 'diag-pill-red',
    skipped: 'diag-pill-grey',
    not_attempted: 'diag-pill-grey',
  };

  function severityPill(severity){
    return SEVERITY_PILL_MAP[severity] || 'diag-pill-grey';
  }

  function renderRoutingSystem(system){
    if(!routingSystemEl) return;
    if(!system || typeof system !== 'object'){
      routingSystemEl.innerHTML = '<div class="stock-note">Routing data unavailable.</div>';
      return;
    }

    const enabled = system.routing_enabled;
    const enabledPill = enabled ? 'diag-pill-green' : 'diag-pill-red';
    const enabledText = enabled ? 'ENABLED' : 'DISABLED';

    const bedrockEnabled = system.bedrock_enabled;
    const bedrockPill = bedrockEnabled ? 'diag-pill-green' : 'diag-pill-grey';
    const bedrockText = bedrockEnabled ? 'ENABLED' : 'DISABLED';

    const configLoadedAt = system.config_loaded_at
      ? new Date(system.config_loaded_at).toLocaleString()
      : 'unknown';

    routingSystemEl.innerHTML = `
      <div class="dh-routing-meta-grid">
        <div class="dh-routing-meta-row">
          <span class="dh-routing-meta-label">Execution Mode</span>
          <span>${escapeHtml(String(system.execution_mode_label || system.selected_execution_mode || '—'))}</span>
        </div>
        <div class="dh-routing-meta-row">
          <span class="dh-routing-meta-label">Routing</span>
          <span class="diag-pill ${enabledPill}">${enabledText}</span>
        </div>
        <div class="dh-routing-meta-row">
          <span class="dh-routing-meta-label">Bedrock</span>
          <span class="diag-pill ${bedrockPill}">${bedrockText}</span>
        </div>
        <div class="dh-routing-meta-row">
          <span class="dh-routing-meta-label">Providers</span>
          <span>${escapeHtml(String(system.provider_count || 0))}</span>
        </div>
        <div class="dh-routing-meta-row">
          <span class="dh-routing-meta-label">Default Concurrency</span>
          <span>${escapeHtml(String(system.default_max_concurrency || 1))}</span>
        </div>
        <div class="dh-routing-meta-row">
          <span class="dh-routing-meta-label">Config Source</span>
          <span>${escapeHtml(String(system.config_source || 'defaults'))}</span>
        </div>
        <div class="dh-routing-meta-row">
          <span class="dh-routing-meta-label">Config Loaded</span>
          <span>${escapeHtml(configLoadedAt)}</span>
        </div>
        <div class="dh-routing-meta-row">
          <span class="dh-routing-meta-label">Degraded Threshold</span>
          <span class="dh-routing-threshold-hint">${escapeHtml(String(system.probe_degraded_threshold_ms || 2000))} ms</span>
        </div>
      </div>
    `;
  }

  function renderRoutingProviders(providers){
    if(!routingProviderTilesEl) return;
    if(!Array.isArray(providers) || !providers.length){
      routingProviderTilesEl.innerHTML = '<div class="stock-note">No routing providers registered.</div>';
      return;
    }

    routingProviderTilesEl.innerHTML = providers.map((p) => {
      const pill = severityPill(p.severity || 'offline');
      // Use state_display_label from backend (Step 16) with fallback
      const stateText = escapeHtml(p.state_display_label || String(p.current_state || 'unknown').toUpperCase());
      const timingStr = p.timing_ms != null ? p.timing_ms.toFixed(0) + ' ms' : '—';
      const capacityStr = p.in_flight_count + '/' + p.max_concurrency;
      const configStr = p.configured ? 'Configured' : 'Not configured';
      const probeTypeStr = p.probe_type === 'config_only' ? 'Config-only probe' : (p.probe_type === 'cached' ? 'Cached' : 'Live probe');
      const detailText = p.status_detail_text || '';
      const checkedStr = p.last_checked_at ? new Date(p.last_checked_at).toLocaleTimeString() : '';

      return `
        <div class="statTile">
          <div class="statLabel" data-metric="routing_provider">${escapeHtml(p.display_label || p.provider)}</div>
          <div class="diag-pill ${pill}">${stateText}</div>
          ${detailText ? '<div class="dh-routing-detail-text">' + escapeHtml(detailText) + '</div>' : ''}
          <div class="dh-routing-tile-meta">
            <span title="In-flight / Max concurrency">Slots: ${escapeHtml(capacityStr)}</span>
            <span title="Probe latency">Latency: ${escapeHtml(timingStr)}</span>
            <span title="Probe method">${escapeHtml(probeTypeStr)}</span>
            <span>${escapeHtml(configStr)}</span>
            ${checkedStr ? '<span title="Last probe time">Checked: ' + escapeHtml(checkedStr) + '</span>' : ''}
          </div>
        </div>
      `;
    }).join('');
  }

  function renderRoutingRecent(traces){
    if(!routingRecentBodyEl) return;
    if(!Array.isArray(traces) || !traces.length){
      routingRecentBodyEl.innerHTML = '<tr><td colspan="6" class="loading">No recent routing activity.</td></tr>';
      return;
    }

    routingRecentBodyEl.innerHTML = traces.map((t) => {
      const statusPillClass = EXEC_STATUS_PILL[t.execution_status] || 'diag-pill-grey';
      const modeStr = t.requested_mode === t.resolved_mode
        ? escapeHtml(t.requested_mode || '—')
        : escapeHtml((t.requested_mode || '?') + ' → ' + (t.resolved_mode || '?'));
      const providerStr = t.selected_provider
        ? escapeHtml(t.selected_provider)
        : '—';
      const fallbackStr = t.fallback_used ? 'Yes' : '—';
      const timingStr = t.timing_ms != null ? t.timing_ms.toFixed(0) + ' ms' : '—';

      // Build skip detail as tooltip
      const skipEntries = t.skip_summary && typeof t.skip_summary === 'object'
        ? Object.entries(t.skip_summary)
        : [];
      const skipTitle = skipEntries.length
        ? 'Skipped: ' + skipEntries.map(([r, c]) => r + '(' + c + ')').join(', ')
        : '';

      return `
        <tr${skipTitle ? ' title="' + escapeHtml(skipTitle) + '"' : ''}>
          <td>${escapeHtml(t.task_type || '—')}</td>
          <td>${modeStr}</td>
          <td>${providerStr}</td>
          <td><span class="diag-pill ${statusPillClass}">${escapeHtml((t.execution_status || '—').toUpperCase())}</span></td>
          <td>${escapeHtml(fallbackStr)}</td>
          <td>${escapeHtml(timingStr)}</td>
        </tr>
      `;
    }).join('');
  }

  function renderRoutingDashboard(routing){
    if(!routing || typeof routing !== 'object'){
      if(routingSystemEl) routingSystemEl.innerHTML = '<div class="stock-note">Routing data unavailable.</div>';
      if(routingProviderTilesEl) routingProviderTilesEl.innerHTML = '<div class="stock-note">No routing providers.</div>';
      if(routingRecentBodyEl) routingRecentBodyEl.innerHTML = '<tr><td colspan="6" class="loading">No routing data.</td></tr>';
      return;
    }
    renderRoutingSystem(routing.system || {});
    renderRoutingProviders(routing.providers || []);
    renderRoutingRecent(routing.recent_traces || []);
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
      renderRoutingDashboard(payload?.routing || null);
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

    // Render model & routing mode selector state (Step 18 unified)
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
