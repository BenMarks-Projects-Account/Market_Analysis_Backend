window.BenTradePages = window.BenTradePages || {};

window.BenTradeStrategyShell = (function(){
  const state = {
    installed: false,
    activeConfig: null,
    nativeFetch: null,
    nativeEventSource: null,
  };

  function isTruthy(value){
    if(typeof value === 'boolean') return value;
    const text = String(value || '').trim().toLowerCase();
    return text === '1' || text === 'true' || text === 'yes' || text === 'on';
  }

  function isStrategyRouteActive(){
    const hash = String(window.location.hash || '').toLowerCase();
    return hash.startsWith('#/strategy-')
      || hash.startsWith('#/credit-spread')
      || hash.startsWith('#credit-spread')
      || hash.startsWith('#/debit-spreads')
      || hash.startsWith('#debit-spreads')
      || hash.startsWith('#/iron-condor')
      || hash.startsWith('#iron-condor')
      || hash.startsWith('#/butterflies')
      || hash.startsWith('#butterflies')
      || hash.startsWith('#/calendar')
      || hash.startsWith('#calendar')
      || hash.startsWith('#/income')
      || hash.startsWith('#income');
  }

  function activeConfig(){
    if(!isStrategyRouteActive()) return null;
    return state.activeConfig;
  }

  function strategyKey(config){
    const mode = String(config?.filterMode || '').trim().toLowerCase();
    if(mode === 'credit-spread') return 'credit_spread';
    return String(config?.strategyId || '').trim().toLowerCase();
  }

  function showToast(message){
    const id = 'strategyDefaultsToast';
    let el = document.getElementById(id);
    if(!el){
      el = document.createElement('div');
      el.id = id;
      el.style.position = 'fixed';
      el.style.right = '16px';
      el.style.bottom = '16px';
      el.style.zIndex = '9999';
      el.style.padding = '8px 12px';
      el.style.borderRadius = '10px';
      el.style.background = 'rgba(8,18,26,0.92)';
      el.style.border = '1px solid rgba(0,234,255,0.35)';
      el.style.color = 'rgba(215,251,255,0.96)';
      el.style.fontSize = '12px';
      el.style.boxShadow = '0 10px 24px rgba(0,0,0,0.45)';
      el.style.opacity = '0';
      el.style.transition = 'opacity 0.16s ease';
      document.body.appendChild(el);
    }
    el.textContent = String(message || 'Updated');
    el.style.opacity = '1';
    setTimeout(() => { el.style.opacity = '0'; }, 1100);
  }

  function ensureInstalled(){
    if(state.installed) return;
    state.installed = true;

    state.nativeFetch = window.fetch.bind(window);
    state.nativeEventSource = window.EventSource;

    window.fetch = function(input, init){
      const cfg = activeConfig();
      if(!cfg){
        return state.nativeFetch(input, init);
      }

      const endpoint = cfg.endpoint || {};
      const reportListUrl = endpoint.reports || `/api/strategies/${encodeURIComponent(cfg.strategyId)}/reports`;
      const reportGetPrefix = `${reportListUrl}/`;

      const rawUrl = (typeof input === 'string')
        ? input
        : (input instanceof Request ? input.url : String(input || ''));

      let mappedUrl = rawUrl;
      if(rawUrl === '/api/reports'){
        mappedUrl = reportListUrl;
      }else if(rawUrl.startsWith('/api/reports/')){
        mappedUrl = `${reportGetPrefix}${rawUrl.substring('/api/reports/'.length)}`;
      }

      if(mappedUrl === rawUrl){
        return state.nativeFetch(input, init);
      }

      if(typeof input === 'string'){
        return state.nativeFetch(mappedUrl, init);
      }
      if(input instanceof Request){
        const req = new Request(mappedUrl, input);
        return state.nativeFetch(req, init);
      }
      return state.nativeFetch(mappedUrl, init);
    };

    function PatchedEventSource(url, eventSourceInitDict){
      const cfg = activeConfig();
      let mapped = String(url || '');
      if(cfg && mapped === '/api/generate'){
        const endpoint = cfg.endpoint || {};
        mapped = endpoint.generateSse || `/api/strategies/${encodeURIComponent(cfg.strategyId)}/generate`;
        const qp = new URLSearchParams();
        const advancedEnabled = !!cfg.advancedEnabled;
        const filters = cfg.currentFilters || {};
        qp.set('advanced_enabled', advancedEnabled ? 'true' : 'false');
        // Always send preset and symbols regardless of advanced toggle
        if(filters.preset) qp.set('preset', String(filters.preset));
        if(Array.isArray(filters.symbols) && filters.symbols.length){
          qp.set('symbols', filters.symbols.join(','));
        }
        if(advancedEnabled){
          Object.entries(filters).forEach(([key, value]) => {
            if(key === 'preset' || key === 'symbols') return; // already handled
            if(value === null || value === undefined || value === '') return;
            qp.set(String(key), String(value));
          });
        }
        const suffix = qp.toString();
        if(suffix){
          mapped = `${mapped}${mapped.includes('?') ? '&' : '?'}${suffix}`;
        }
      }
      return new state.nativeEventSource(mapped, eventSourceInitDict);
    }
    PatchedEventSource.prototype = state.nativeEventSource.prototype;
    Object.defineProperty(PatchedEventSource, 'name', { value: 'EventSource' });
    window.EventSource = PatchedEventSource;
  }

  function formDefinition(mode){
    const key = String(mode || '').trim().toLowerCase();
    if(key === 'credit-spread'){
      return [
        { key: 'dte_min', label: 'DTE min', type: 'int', min: 1, width: '90px' },
        { key: 'dte_max', label: 'DTE max', type: 'int', min: 1, width: '90px' },
        { key: 'expected_move_multiple', label: 'Exp Move×', type: 'float', min: 0.1, step: 0.1, width: '105px' },
        { key: 'width_min', label: 'Width min', type: 'float', min: 0.5, step: 0.5, width: '95px' },
        { key: 'width_max', label: 'Width max', type: 'float', min: 0.5, step: 0.5, width: '95px' },
        { key: 'distance_min', label: 'OTM min', type: 'float', min: 0.01, max: 0.30, step: 0.01, width: '95px' },
        { key: 'distance_max', label: 'OTM max', type: 'float', min: 0.01, max: 0.30, step: 0.01, width: '95px' },
        { key: 'min_pop', label: 'Min POP', type: 'float', min: 0, max: 1, step: 0.01, width: '100px' },
        { key: 'min_ev_to_risk', label: 'Min EV/Risk', type: 'float', min: 0, step: 0.01, width: '115px' },
        { key: 'max_bid_ask_spread_pct', label: 'Max spread %', type: 'float', min: 0.1, step: 0.1, width: '115px' },
        { key: 'min_open_interest', label: 'Min OI', type: 'int', min: 1, width: '90px' },
        { key: 'min_volume', label: 'Min Vol', type: 'int', min: 1, width: '90px' },
      ];
    }
    if(key === 'debit-spreads'){
      return [
        { key: 'direction', label: 'Direction', type: 'select', width: '130px', options: [
          { value: 'both', label: 'Both' },
          { value: 'call', label: 'Call' },
          { value: 'put', label: 'Put' },
        ] },
        { key: 'dte_min', label: 'DTE min', type: 'int', min: 1, width: '90px' },
        { key: 'dte_max', label: 'DTE max', type: 'int', min: 1, width: '90px' },
        { key: 'width_min', label: 'Width min', type: 'float', min: 0.5, step: 0.5, width: '95px' },
        { key: 'width_max', label: 'Width max', type: 'float', min: 0.5, step: 0.5, width: '95px' },
        { key: 'max_debit_pct_width', label: 'Max debit % width', type: 'float', min: 0.05, step: 0.01, width: '140px' },
        { key: 'max_iv_rv_ratio_for_buying', label: 'IV/RV ≤', type: 'float', min: 0.1, step: 0.01, width: '95px' },
        { key: 'min_open_interest', label: 'Min OI', type: 'int', min: 1, width: '90px' },
        { key: 'min_volume', label: 'Min Vol', type: 'int', min: 1, width: '90px' },
      ];
    }
    if(key === 'iron-condor'){
      return [
        { key: 'dte_min', label: 'DTE min', type: 'int', min: 1, width: '90px' },
        { key: 'dte_max', label: 'DTE max', type: 'int', min: 1, width: '90px' },
        { key: 'distance_mode', label: 'Distance mode', type: 'select', width: '160px', options: [
          { value: 'expected_move', label: 'Expected Move' },
          { value: 'delta', label: 'Delta' },
        ] },
        { key: 'distance_target', label: 'Distance target', type: 'float', min: 0.05, step: 0.05, width: '120px' },
        { key: 'wing_width_put', label: 'Put wing', type: 'float', min: 0.5, step: 0.5, width: '90px' },
        { key: 'wing_width_call', label: 'Call wing', type: 'float', min: 0.5, step: 0.5, width: '90px' },
        { key: 'wing_width_max', label: 'Wing max', type: 'float', min: 0.5, step: 0.5, width: '90px' },
        { key: 'min_ror', label: 'Min RoR', type: 'float', min: 0.01, step: 0.01, width: '95px' },
        { key: 'symmetry_target', label: 'Symmetry', type: 'float', min: 0.1, max: 1, step: 0.01, width: '95px' },
        { key: 'min_open_interest', label: 'Min OI', type: 'int', min: 1, width: '90px' },
        { key: 'min_volume', label: 'Min Vol', type: 'int', min: 1, width: '90px' },
      ];
    }
    if(key === 'butterflies'){
      return [
        { key: 'dte_min', label: 'DTE min', type: 'int', min: 1, width: '90px' },
        { key: 'dte_max', label: 'DTE max', type: 'int', min: 1, width: '90px' },
        { key: 'butterfly_type', label: 'Type', type: 'select', width: '120px', options: [
          { value: 'debit', label: 'Debit' },
          { value: 'iron', label: 'Iron' },
          { value: 'both', label: 'Both' },
        ] },
        { key: 'option_side', label: 'Side', type: 'select', width: '120px', options: [
          { value: 'call', label: 'Call' },
          { value: 'put', label: 'Put' },
          { value: 'both', label: 'Both' },
        ] },
        { key: 'center_mode', label: 'Center', type: 'select', width: '140px', options: [
          { value: 'spot', label: 'Spot' },
          { value: 'forecast', label: 'Forecast' },
          { value: 'expected_move', label: 'Expected Move' },
        ] },
        { key: 'width_min', label: 'Wing min', type: 'float', min: 0.5, step: 0.5, width: '95px' },
        { key: 'width_max', label: 'Wing max', type: 'float', min: 0.5, step: 0.5, width: '95px' },
        { key: 'min_cost_efficiency', label: 'Min efficiency', type: 'float', min: 0.1, step: 0.1, width: '120px' },
        { key: 'min_open_interest', label: 'Min OI', type: 'int', min: 1, width: '90px' },
        { key: 'min_volume', label: 'Min Vol', type: 'int', min: 1, width: '90px' },
      ];
    }
    if(key === 'calendar'){
      return [
        { key: 'near_dte_min', label: 'Near DTE min', type: 'int', min: 1, width: '105px' },
        { key: 'near_dte_max', label: 'Near DTE max', type: 'int', min: 1, width: '105px' },
        { key: 'far_dte_min', label: 'Far DTE min', type: 'int', min: 1, width: '105px' },
        { key: 'far_dte_max', label: 'Far DTE max', type: 'int', min: 1, width: '105px' },
        { key: 'moneyness', label: 'Strike', type: 'select', width: '110px', options: [
          { value: 'atm', label: 'ATM' },
          { value: 'itm', label: 'ITM' },
          { value: 'otm', label: 'OTM' },
        ] },
        { key: 'prefer_term_structure', label: 'Far IV≥Near', type: 'select', width: '120px', options: [
          { value: '1', label: 'Prefer' },
          { value: '0', label: 'Ignore' },
        ] },
        { key: 'max_bid_ask_spread_pct', label: 'Max spread %', type: 'float', min: 0.1, step: 0.1, width: '115px' },
        { key: 'min_open_interest', label: 'Min OI', type: 'int', min: 1, width: '90px' },
        { key: 'min_volume', label: 'Min Vol', type: 'int', min: 1, width: '90px' },
      ];
    }
    if(key === 'income'){
      return [
        { key: 'dte_min', label: 'DTE min', type: 'int', min: 1, width: '90px' },
        { key: 'dte_max', label: 'DTE max', type: 'int', min: 1, width: '90px' },
        { key: 'delta_min', label: 'Delta min', type: 'float', min: 0.01, max: 0.95, step: 0.01, width: '95px' },
        { key: 'delta_max', label: 'Delta max', type: 'float', min: 0.01, max: 0.95, step: 0.01, width: '95px' },
        { key: 'min_annualized_yield', label: 'Min ann. yield', type: 'float', min: 0, step: 0.01, width: '120px' },
        { key: 'min_buffer', label: 'Min buffer', type: 'float', min: 0, step: 0.01, width: '95px', placeholder: 'auto' },
        { key: 'min_open_interest', label: 'Min OI', type: 'int', min: 1, width: '90px' },
        { key: 'min_volume', label: 'Min Vol', type: 'int', min: 1, width: '90px' },
      ];
    }
    return [];
  }

  function inputId(mode, key){
    return `strategy-${String(mode || '').replace(/[^a-z0-9]+/gi, '-')}-${String(key || '').replace(/[^a-z0-9_]+/gi, '')}`;
  }

  function readFieldValue(field, input){
    if(!input) return undefined;
    const raw = String(input.value ?? '').trim();
    if(raw === '') return undefined;

    if(field.type === 'int'){
      const n = Number(raw);
      return Number.isFinite(n) ? Math.round(n) : undefined;
    }
    if(field.type === 'float'){
      const n = Number(raw);
      return Number.isFinite(n) ? n : undefined;
    }
    return raw;
  }

  function writeFieldValue(field, input, value){
    if(!input) return;
    if(value === null || value === undefined || value === ''){
      input.value = '';
      return;
    }
    input.value = String(value);
  }

  function buildForm(host, config){
    const mode = config.filterMode || strategyKey(config);
    const fields = formDefinition(mode);
    if(!host || !fields.length) return;

    const rowId = `strategyForm-${mode}`;
    if(host.querySelector(`#${CSS.escape(rowId)}`)) return;

    const row = document.createElement('div');
    row.id = rowId;
    row.style.display = 'flex';
    row.style.flexDirection = 'column';
    row.style.gap = '8px';
    row.style.marginTop = '10px';

    const fieldWrap = document.createElement('div');
    fieldWrap.style.display = 'flex';
      const advancedToggleWrap = document.createElement('label');
      advancedToggleWrap.style.display = 'inline-flex';
      advancedToggleWrap.style.alignItems = 'center';
      advancedToggleWrap.style.gap = '8px';
      advancedToggleWrap.style.fontSize = '13px';
      advancedToggleWrap.style.color = 'rgba(215,251,255,0.96)';

      const advancedToggle = document.createElement('input');
      advancedToggle.type = 'checkbox';
      advancedToggle.checked = !!config.advancedEnabled;

      const advancedText = document.createElement('span');
      advancedText.textContent = 'Use Advanced Filters';
      advancedToggleWrap.appendChild(advancedToggle);
      advancedToggleWrap.appendChild(advancedText);

    fieldWrap.style.gap = '8px';
    fieldWrap.style.flexWrap = 'wrap';

    fields.forEach((field) => {
      const wrap = document.createElement('label');
      wrap.style.display = 'flex';
      wrap.style.flexDirection = 'column';
      wrap.style.gap = '2px';

      const title = document.createElement('span');
      title.textContent = field.label;
      title.style.fontSize = '11px';
      title.style.opacity = '0.86';

      let control;
      if(field.type === 'select'){
        const select = document.createElement('select');
        (field.options || []).forEach((opt) => {
          const option = document.createElement('option');
          option.value = String(opt.value);
          option.textContent = String(opt.label);
          select.appendChild(option);
        });
        control = select;
      }else{
        const input = document.createElement('input');
        input.type = 'number';
        if(field.min != null) input.min = String(field.min);
        if(field.max != null) input.max = String(field.max);
        if(field.step != null) input.step = String(field.step);
        if(field.placeholder) input.placeholder = String(field.placeholder);
        control = input;
      }

      control.id = inputId(mode, field.key);
      control.setAttribute('data-filter-key', field.key);
      control.style.width = field.width || '110px';
      wrap.appendChild(title);
      wrap.appendChild(control);
      fieldWrap.appendChild(wrap);
    });

    const controls = document.createElement('div');
    controls.className = 'strategy-default-controls';
    controls.style.display = 'flex';
    controls.style.flexWrap = 'wrap';
    controls.style.gap = '8px';
    controls.style.alignItems = 'center';

    const btnDefaults = document.createElement('button');
    btnDefaults.type = 'button';
    btnDefaults.className = 'btn';
    btnDefaults.textContent = 'Use Defaults';

    const btnGenerateDefaults = document.createElement('button');
    btnGenerateDefaults.type = 'button';
    btnGenerateDefaults.className = 'btn';
    btnGenerateDefaults.textContent = 'Generate With Defaults';

    const btnReset = document.createElement('button');
    btnReset.type = 'button';
    btnReset.className = 'btn';
    btnReset.textContent = 'Reset';

    const why = document.createElement('details');
    why.style.marginLeft = '4px';
    const summary = document.createElement('summary');
    summary.textContent = 'Why these defaults?';
    summary.style.cursor = 'pointer';
    summary.style.fontSize = '12px';
    const list = document.createElement('ul');
    list.style.margin = '6px 0 0 16px';
    list.style.padding = '0';
    list.style.fontSize = '12px';
    const reasons = window.BenTradeStrategyDefaults?.getStrategyWhy?.(strategyKey(config)) || [];
    reasons.slice(0, 3).forEach((item) => {
      const li = document.createElement('li');
      li.textContent = String(item);
      list.appendChild(li);
    });
    why.appendChild(summary);
    why.appendChild(list);

    controls.appendChild(advancedToggleWrap);

    // -- Preset toggle (Conservative / Strict) --
    const sKey = strategyKey(config);
    const presetNames = window.BenTradeStrategyDefaults?.getPresetNames?.(sKey) || [];
    let presetSelect = null;
    if(presetNames.length > 1){
      const presetWrap = document.createElement('label');
      presetWrap.style.display = 'inline-flex';
      presetWrap.style.alignItems = 'center';
      presetWrap.style.gap = '6px';
      presetWrap.style.fontSize = '12px';
      presetWrap.style.color = 'rgba(215,251,255,0.96)';
      presetWrap.style.marginRight = '4px';
      const presetLabel = document.createElement('span');
      presetLabel.textContent = 'Preset:';
      presetSelect = document.createElement('select');
      presetSelect.style.fontSize = '12px';
      presetSelect.style.padding = '3px 6px';
      presetNames.forEach((name) => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name.charAt(0).toUpperCase() + name.slice(1);
        presetSelect.appendChild(opt);
      });
      presetSelect.value = presetNames[0]; // default to first (conservative)
      presetWrap.appendChild(presetLabel);
      presetWrap.appendChild(presetSelect);
      controls.appendChild(presetWrap);
    }

    controls.appendChild(btnDefaults);
    controls.appendChild(btnGenerateDefaults);
    controls.appendChild(btnReset);
    controls.appendChild(why);

    row.appendChild(fieldWrap);
    row.appendChild(controls);
    host.appendChild(row);

    const sticky = { ...(config.defaultFilters || {}) };
    let currentPreset = presetNames.length ? presetNames[0] : '';

    const setAdvancedEnabled = (enabled) => {
      const on = !!enabled;
      state.activeConfig.advancedEnabled = on;
      fieldWrap.style.display = on ? 'flex' : 'none';
      btnDefaults.style.display = on ? '' : 'none';
      btnReset.style.display = on ? '' : 'none';
      why.style.display = on ? '' : 'none';
      /* Template manual-filter controls removed — shell owns its own. */
    };

    const writeFiltersFromInputs = () => {
      const next = { ...sticky };
      fields.forEach((field) => {
        const input = row.querySelector(`#${CSS.escape(inputId(mode, field.key))}`);
        const value = readFieldValue(field, input);
        if(value !== undefined){
          next[field.key] = value;
        }
      });
      state.activeConfig.currentFilters = next;
    };

    const applyDefaults = () => {
      const defaults = window.BenTradeStrategyDefaults?.getStrategyDefaults?.(strategyKey(config), currentPreset) || {};
      fields.forEach((field) => {
        const input = row.querySelector(`#${CSS.escape(inputId(mode, field.key))}`);
        writeFieldValue(field, input, defaults[field.key]);
      });
      writeFiltersFromInputs();
      // Stash preset into currentFilters so SSE URL includes it
      state.activeConfig.currentFilters.preset = currentPreset;
      showToast(`${currentPreset ? currentPreset.charAt(0).toUpperCase() + currentPreset.slice(1) : 'Defaults'} applied`);
    };

    const clearInputs = () => {
      fields.forEach((field) => {
        const input = row.querySelector(`#${CSS.escape(inputId(mode, field.key))}`);
        writeFieldValue(field, input, '');
      });
      state.activeConfig.currentFilters = { ...sticky };
      showToast('Inputs reset');
    };

    row.querySelectorAll('input,select').forEach((el) => {
      if(el === advancedToggle) return;
      el.addEventListener('change', writeFiltersFromInputs);
      el.addEventListener('input', writeFiltersFromInputs);
    });

    advancedToggle.addEventListener('change', () => {
      setAdvancedEnabled(advancedToggle.checked);
      if(!advancedToggle.checked){
        state.activeConfig.currentFilters = { ...(config.defaultFilters || {}) };
      }
    });

    /* Template manual-filter toggle removed — shell owns its own. */

    // -- Preset select change handler --
    if(presetSelect){
      presetSelect.addEventListener('change', () => {
        currentPreset = presetSelect.value;
        applyDefaults();
      });
    }

    btnDefaults.addEventListener('click', applyDefaults);
    btnReset.addEventListener('click', clearInputs);
    btnGenerateDefaults.addEventListener('click', () => {
      applyDefaults();
      advancedToggle.checked = false;
      setAdvancedEnabled(false);
      // Ensure preset + symbols travel through the generate call
      const defaults = window.BenTradeStrategyDefaults?.getStrategyDefaults?.(strategyKey(config), currentPreset) || {};
      if(defaults.symbols){
        state.activeConfig.currentFilters.symbols = defaults.symbols;
      }
      state.activeConfig.currentFilters.preset = currentPreset;
      const genBtn = document.getElementById('genBtn');
      if(genBtn){
        genBtn.click();
      }
    });

    applyDefaults();
    setAdvancedEnabled(!!config.advancedEnabled);
  }

  function mount(rootEl, config){
    if(!rootEl || !config || !config.strategyId) return;
    ensureInstalled();

    state.activeConfig = {
      strategyId: String(config.strategyId),
      title: String(config.title || config.strategyId),
      endpoint: {
        reports: String(config?.endpoint?.reports || `/api/strategies/${encodeURIComponent(config.strategyId)}/reports`),
        generateSse: String(config?.endpoint?.generateSse || `/api/strategies/${encodeURIComponent(config.strategyId)}/generate`),
      },
      defaultFilters: config.defaultFilters || {},
      filterMode: String(config?.filterMode || ''),
      currentFilters: { ...(config.defaultFilters || {}) },
      advancedEnabled: isTruthy(config?.advancedEnabled),
    };

    rootEl.dataset.strategyId = state.activeConfig.strategyId;
    rootEl.dataset.strategyTitle = state.activeConfig.title;

    try{
      const host = rootEl.querySelector('.file-selector');
      buildForm(host, state.activeConfig);
    }catch(_err){
    }

    /* ── Dashboard lifecycle driver ──────────────────────────────── */
    const INFO = (...args) => console.info(`[StrategyShell:${state.activeConfig.strategyId}]`, ...args);

    const api = window.BenTradeApi;
    const fmt = window.BenTradeUtils?.format || {};
    const tc  = window.BenTradeTradeCard || {};
    const toNumber = fmt.toNumber || ((v) => { const n = Number(v); return Number.isFinite(n) ? n : null; });
    const escapeHtml = fmt.escapeHtml || ((v) => String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'));

    const reportSelect = rootEl.querySelector('#reportSelect');
    const fileSelect   = rootEl.querySelector('#fileSelect');
    const genBtn       = rootEl.querySelector('#genBtn');
    const contentEl    = rootEl.querySelector('#content');
    const countsBar    = rootEl.querySelector('#tradeCountsBar');
    const overlay      = rootEl.querySelector('#genOverlay');
    const genStatus    = rootEl.querySelector('#genStatus');
    const genStatusLog = rootEl.querySelector('#genStatusLog');
    const symUniverseEl = rootEl.querySelector('#strategySymbolUniverse');

    /* Mount symbol universe selector (add/remove + filter for this strategy) */
    let _strategySymbolSelector = null;
    if(symUniverseEl && window.BenTradeSymbolUniverseSelector){
      _strategySymbolSelector = window.BenTradeSymbolUniverseSelector.mount(symUniverseEl, {
        showFilter: true,
        onChange: () => {},  // passive — applied on next generate
      });
    }

    let   currentTrades   = [];
    let   currentFilename = '';

    /* ---------- collapse state (persists across re-renders) ---------- */
    const _expandState = {};  // { [tradeKey]: true/false } — true = expanded

    /* ---------- helpers ---------- */

    function setDropdownError(select, message){
      select.innerHTML = '';
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = message;
      select.appendChild(opt);
    }

    function formatTradeType(val){
      return String(val || 'trade').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    /* ── Debug flag: set window.BENTRADE_DEBUG_TRADES=true or URL ?debug_trades=1 ── */
    const _debugTrades = (function(){
      if(window.BENTRADE_DEBUG_TRADES) return true;
      try{ return new URLSearchParams(window.location.search).get('debug_trades') === '1'; }catch(_){ return false; }
    })();

    /* ── Mapper + config references ───────────────────────────── */
    const _mapper = window.BenTradeOptionTradeCardModel;

    function renderTradeCard(trade, idx){
      /* 1. Map raw API trade → clean view model via config + mapper */
      const model = _mapper.map(trade, state.activeConfig.strategyId);
      const h = model.header;

      /* 2. Resolve rank score for prominent header display */
      const rankDesc = { key: 'rank_score', computedKey: 'rank_score', rootFallbacks: ['composite_score'] };
      const rankVal  = _mapper.resolveMetric(trade, rankDesc);
      const rankBadge = rankVal !== null
        ? `<span class="trade-rank-badge" style="font-size:14px;font-weight:700;color:var(--accent-cyan);background:rgba(0,220,255,0.08);border:1px solid rgba(0,220,255,0.24);border-radius:8px;padding:3px 10px;white-space:nowrap;">Score ${fmt.formatScore(rankVal, 1)}</span>`
        : '';

      /* 3. Header badges — symbol · DTE */
      const symbolBadge  = model.symbol  ? tc.pill(model.symbol)                                    : '';
      const dteBadge     = model.dte !== null ? tc.pill(model.dte + ' DTE')                         : '';

      const strikes = [
        model.shortStrike !== null ? `Short ${model.shortStrike}` : null,
        model.longStrike  !== null ? `Long ${model.longStrike}`   : null,
        model.width       !== null ? `Width ${model.width}`       : null,
      ].filter(Boolean).join(' · ');

      // Net premium line (strategy-aware)
      const premiumText = model.netPremium !== null
        ? `${model.netPremiumLabel}: $${fmt.num(model.netPremium, 2)}`
        : '';

      // Trade key (always visible in header) + copy button
      const tradeKeyDisplay = model.tradeKey
        ? `<span class="trade-key-wrap"><span class="trade-key-label" style="font-size:10px;color:rgba(230,251,255,0.5);font-family:monospace;word-break:break-all;">${escapeHtml(model.tradeKey)}</span>${tc.copyTradeKeyButton(model.tradeKey)}</span>`
        : '';

      /* 4. (Removed — mini summary metrics row no longer rendered) */

      /* 5. Core metrics block (expandable) — only render metrics with a value */
      const resolvedCore = model.coreMetrics.filter(m => m.value !== null);
      const coreGridItems = resolvedCore.map(m => ({
        label: m.label, value: m.display, cssClass: m.tone, dataMetric: m.dataMetric,
      }));
      const coreHtml = resolvedCore.length > 0
        ? tc.section('CORE METRICS', tc.metricGrid(coreGridItems), 'section-core')
        : '';

      /* 6. Detail fields block (expandable, if any resolved) */
      let detailHtml = '';
      const resolvedDetails = model.detailFields.filter(m => m.value !== null);
      if(resolvedDetails.length > 0){
        const detailItems = resolvedDetails.map(m => ({
          label: m.label, value: m.display, dataMetric: m.dataMetric,
        }));
        detailHtml = tc.section('TRADE DETAILS', tc.detailRows(detailItems), 'section-details');
      }

      /* 7. Action buttons (always visible — 3 rows)
       *    Row 1: [Run Model Analysis] — full width
       *    Row 2: [Execute Trade] [Reject]
       *    Row 3: [Send to Workbench] [Send to Data Workbench]
       */
      const tradeKeyAttr = model.tradeKey ? ` data-trade-key="${escapeHtml(model.tradeKey)}"` : '';
      const actionsHtml = `
        <div class="trade-actions">
          <div class="run-row">
            <button class="btn btn-run btn-action" data-action="model-analysis"${tradeKeyAttr} title="Run model analysis on this trade">Run Model Analysis</button>
          </div>
          <div class="actions-row">
            <button class="btn btn-exec btn-action" data-action="execute"${tradeKeyAttr} title="Open execution modal">Execute Trade</button>
            <button class="btn btn-reject btn-action" data-action="reject"${tradeKeyAttr} title="Reject this trade">Reject</button>
          </div>
          <div class="actions-row">
            <button class="btn btn-action" data-action="workbench"${tradeKeyAttr} title="Send to Testing Workbench">Send to Testing Workbench</button>
            <button class="btn btn-action" data-action="data-workbench"${tradeKeyAttr} title="Send to Data Workbench">Send to Data Workbench</button>
          </div>
        </div>`;

      /* 8. Missing-keys warning (visible only in debug) */
      let warnHtml = '';
      if(_debugTrades && model.missingKeys.length > 0){
        warnHtml = `<div class="trade-debug-warn" style="font-size:10px;color:#ffbb33;margin-top:4px;opacity:0.8;">Missing: ${escapeHtml(model.missingKeys.join(', '))}</div>`;
      }

      /* Debug audit */
      if(_debugTrades && idx < 3){
        console.info(
          `[DEBUG_TRADES:PRE_RENDER] trade[${idx}] ${model.symbol} ${model.strategyId}`,
          '\n  model:', model,
        );
      }

      /* Collapse state — default collapsed, persist per tradeKey */
      const isExpanded = model.tradeKey ? (_expandState[model.tradeKey] === true) : false;
      const openAttr = isExpanded ? ' open' : '';

      /* Chevron SVG */
      const chevronSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>';

      return `
        <div class="trade-card" data-idx="${idx}"${tradeKeyAttr} style="margin-bottom:14px;display:flex;flex-direction:column;">
          <details class="trade-card-collapse"${tradeKeyAttr}${openAttr}>
            <summary class="trade-summary">
              <div class="trade-header trade-header-click">
                <div class="trade-header-left">
                  <span class="chev">${chevronSvg}</span>
                </div>
                <div class="trade-header-center">
                  <div class="trade-type" style="display:flex;align-items:center;gap:8px;justify-content:center;">
                    ${symbolBadge} ${dteBadge} ${escapeHtml(model.strategyLabel)}
                  </div>
                  <div class="trade-subtitle">${escapeHtml(h.expiration)}${model.dte !== null ? ` (${model.dte} DTE)` : ''} · ${escapeHtml(strikes)}${premiumText ? ' · ' + escapeHtml(premiumText) : ''}</div>
                  ${tradeKeyDisplay ? `<div style="text-align:center;">${tradeKeyDisplay}</div>` : ''}
                </div>
                <div class="trade-header-right">
                  ${rankBadge}
                </div>
              </div>
            </summary>
            <div class="trade-body" style="flex:1 1 auto;">
              ${coreHtml}
              ${detailHtml}
              ${warnHtml}
            </div>
          </details>
          ${actionsHtml}
        </div>`;
    }

    function renderTrades(trades){
      if(!contentEl) return;
      if(!trades || !trades.length){
        contentEl.innerHTML = '<div class="loading">No trades found in this report.</div>';
        if(countsBar) countsBar.textContent = '0 trades';
        return;
      }
      contentEl.innerHTML = trades.map((t, i) => renderTradeCard(t, i)).join('');
      if(countsBar) countsBar.textContent = `${trades.length} trade${trades.length !== 1 ? 's' : ''}`;
    }

    function filterAndRender(){
      const sym = fileSelect ? String(fileSelect.value || 'ALL') : 'ALL';
      const filtered = sym === 'ALL'
        ? currentTrades
        : currentTrades.filter(t => {
            const s = String(t.symbol || t.underlying || t.underlying_symbol || '').toUpperCase();
            return s === sym.toUpperCase();
          });
      INFO('filterAndRender', { symbol: sym, total: currentTrades.length, filtered: filtered.length });
      renderTrades(filtered);
    }

    function populateSymbols(trades){
      if(!fileSelect) return;
      const symbols = [...new Set(
        (trades || []).map(t => String(t.symbol || t.underlying || t.underlying_symbol || '').toUpperCase()).filter(Boolean)
      )].sort();
      fileSelect.innerHTML = '';
      const allOpt = document.createElement('option');
      allOpt.value = 'ALL';
      allOpt.textContent = `All symbols (${symbols.length})`;
      fileSelect.appendChild(allOpt);
      symbols.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s;
        opt.textContent = s;
        fileSelect.appendChild(opt);
      });
      INFO('populateSymbols', symbols);
    }

    /* ---------- load a single report ---------- */

    async function loadReport(filename){
      if(!filename) return;
      INFO('loadReport', filename);
      currentFilename = filename;
      if(contentEl) contentEl.innerHTML = '<div class="loading">Loading report…</div>';
      try{
        const resp = await fetch(`/api/reports/${encodeURIComponent(filename)}`);
        if(resp.status === 404){
          // Report was deleted or not found — show friendly message
          INFO('loadReport 404 (report deleted or missing)', filename);
          currentTrades = [];
          populateSymbols([]);
          if(contentEl) contentEl.innerHTML = '<div class="loading" style="color:#ffbb33;">Report generated but the file was removed (0 trades). Try generating a new report.</div>';
          if(countsBar) countsBar.textContent = '0 trades';
          return;
        }
        if(!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        const trades = Array.isArray(data?.trades) ? data.trades : (Array.isArray(data) ? data : []);
        const reportStatus = String(data?.report_status || (trades.length ? 'ok' : 'empty'));
        const reportWarnings = Array.isArray(data?.report_warnings) ? data.report_warnings : [];
        const diagnostics = data?.diagnostics || {};

        /* Stamp source metadata onto each trade so the model can resolve it */
        const generatedAt = data?.generated_at || data?.metadata?.generated_at || null;
        for(let ti = 0; ti < trades.length; ti++){
          trades[ti]._source_report_file  = trades[ti]._source_report_file  || filename;
          trades[ti]._source_generated_at = trades[ti]._source_generated_at || generatedAt;
        }

        currentTrades = trades;
        populateSymbols(trades);
        INFO('loadReport OK', { filename, tradeCount: trades.length, report_status: reportStatus, diagnostics });

        /* Debug: log the raw API payload before any rendering */
        if(_debugTrades && trades.length > 0){
          console.info(
            `[DEBUG_TRADES:API_RESPONSE] ${filename} — ${trades.length} trades`,
            '\n  cardConfig:', (window.BenTradeStrategyCardConfig?.forStrategy?.(state.activeConfig.strategyId) || {}).coreMetrics?.map(m => m.key),
            '\n  sample trade[0] keys:', Object.keys(trades[0]),
            '\n  trade[0].computed:', trades[0].computed,
            '\n  trade[0].computed_metrics:', trades[0].computed_metrics,
            '\n  trade[0].details:', trades[0].details,
            '\n  trade[0].pills:', trades[0].pills,
          );
        }

        if(trades.length === 0){
          // Empty report — styled explanation box with warnings, diagnostics, rejection breakdown
          const stats = data?.report_stats || {};
          const symbols = Array.isArray(data?.symbols) ? data.symbols : [];

          let html = '<div style="border:1px solid rgba(255,187,51,0.35);border-radius:10px;padding:16px 18px;margin-top:6px;background:rgba(255,187,51,0.06);">';
          html += '<div style="font-size:14px;font-weight:700;color:#ffbb33;margin-bottom:8px;">No Trades Passed Filters</div>';
          html += '<div style="font-size:12px;color:rgba(230,251,255,0.85);line-height:1.55;">';
          html += 'All candidates were filtered out by the evaluate thresholds. ';
          html += 'This is normal when market conditions or scan parameters produce candidates ';
          html += 'that don\'t meet the quality gates (EV/risk, liquidity, spread width).';
          html += '</div>';

          if(reportWarnings.length){
            html += '<div style="margin-top:10px;font-size:12px;font-weight:600;color:#9fefff;">Warnings</div>';
            html += '<ul style="margin:4px 0 0 18px;padding:0;color:#9fefff;font-size:12px;line-height:1.5;">';
            reportWarnings.forEach(w => { html += `<li>${escapeHtml(w)}</li>`; });
            html += '</ul>';
          }

          // Diagnostics summary line
          const parts = [];
          if(symbols.length) parts.push(`Symbols: ${symbols.join(', ')}`);
          if(diagnostics.candidate_count != null) parts.push(`Candidates: ${diagnostics.candidate_count}`);
          if(diagnostics.enriched_count != null) parts.push(`Enriched: ${diagnostics.enriched_count}`);
          if(diagnostics.accepted_count != null) parts.push(`Accepted: ${diagnostics.accepted_count}`);
          if(diagnostics.closes_count != null) parts.push(`Closes: ${diagnostics.closes_count}`);
          if(stats.total_candidates != null) parts.push(`Total scanned: ${stats.total_candidates}`);
          if(stats.acceptance_rate != null) parts.push(`Pass rate: ${(stats.acceptance_rate * 100).toFixed(1)}%`);
          if(diagnostics.invalid_quote_count) parts.push(`Invalid quotes: ${diagnostics.invalid_quote_count}`);
          if(parts.length){
            html += `<div style="margin-top:10px;color:rgba(159,239,255,0.7);font-size:11px;">${escapeHtml(parts.join(' · '))}</div>`;
          }

          // Rejection breakdown (from report_stats or diagnostics)
          const rejBk = stats.rejection_breakdown || diagnostics.rejection_breakdown || {};
          const rejEntries = Object.entries(rejBk).filter(([,v]) => v > 0);
          if(rejEntries.length){
            html += '<div style="margin-top:12px;font-size:12px;font-weight:600;color:#ffbb33;">Rejection Breakdown</div>';
            html += '<table style="margin-top:4px;font-size:11px;color:#ddd;border-collapse:collapse;width:100%;max-width:360px;">';
            html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.1);"><th style="text-align:left;padding:4px 10px 4px 0;font-weight:600;color:#9fefff;">Reason</th><th style="text-align:right;padding:4px 0;font-weight:600;color:#9fefff;">Count</th></tr>';
            rejEntries.sort((a,b) => b[1] - a[1]);
            rejEntries.forEach(([reason, count]) => {
              const label = reason.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
              html += `<tr><td style="padding:3px 10px 3px 0;">${escapeHtml(label)}</td><td style="text-align:right;padding:3px 0;">${count}</td></tr>`;
            });
            html += '</table>';
          }

          // Suggestion
          html += '<div style="margin-top:14px;font-size:11px;color:rgba(159,239,255,0.6);line-height:1.5;">';
          html += '<strong>Suggestions:</strong> Try the Conservative preset for wider scan parameters, ';
          html += 'scan multiple symbols (SPY + QQQ + IWM), or enable Advanced Filters to tune DTE / width / OTM distance.';
          html += '</div>';

          html += '</div>';  // close explanation box
          if(contentEl) contentEl.innerHTML = html;
          if(countsBar) countsBar.textContent = '0 trades';
          return;
        }

        filterAndRender();
      }catch(err){
        INFO('loadReport ERROR', err);
        currentTrades = [];
        if(contentEl) contentEl.innerHTML = `<div class="loading" style="color:#ff5e5e;">Failed to load report: ${escapeHtml(err.message)}</div>`;
        if(countsBar) countsBar.textContent = '';
        setDropdownError(fileSelect, 'Error loading symbols');
      }
    }

    /* ---------- load report list ---------- */

    async function loadReportList(){
      INFO('loadReportList');
      if(reportSelect){
        reportSelect.innerHTML = '';
        const loadOpt = document.createElement('option');
        loadOpt.value = '';
        loadOpt.textContent = 'Loading reports…';
        reportSelect.appendChild(loadOpt);
      }
      try{
        const list = await fetch('/api/reports').then(r => {
          if(!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        });
        const files = Array.isArray(list) ? list : [];
        INFO('loadReportList OK', { count: files.length });
        if(!reportSelect) return;
        reportSelect.innerHTML = '';
        if(!files.length){
          const opt = document.createElement('option');
          opt.value = '';
          opt.textContent = 'No reports available';
          reportSelect.appendChild(opt);
          if(fileSelect) setDropdownError(fileSelect, 'No symbols');
          if(contentEl) contentEl.innerHTML = '<div class="loading">No reports yet. Click "Generate New Report" to create one.</div>';
          return;
        }
        files.forEach((f, i) => {
          const opt = document.createElement('option');
          opt.value = String(f);
          opt.textContent = String(f);
          reportSelect.appendChild(opt);
        });
        // Auto-load the first (most recent) report
        await loadReport(files[0]);
      }catch(err){
        INFO('loadReportList ERROR', err);
        if(reportSelect) setDropdownError(reportSelect, 'Error loading reports');
        if(fileSelect) setDropdownError(fileSelect, 'Error');
        if(contentEl) contentEl.innerHTML = `<div class="loading" style="color:#ff5e5e;">Failed to load reports: ${escapeHtml(err.message)}</div>`;
      }
    }

    /* ---------- generate new report (SSE) ---------- */

    function startGeneration(){
      INFO('startGeneration');
      if(genBtn){
        genBtn.disabled = true;
        genBtn.textContent = 'Generating…';
      }
      if(overlay) overlay.style.display = 'flex';
      if(genStatus) genStatus.textContent = 'Starting…';
      if(genStatusLog) genStatusLog.textContent = '';

      const es = new EventSource('/api/generate');

      function appendLog(msg){
        if(!genStatusLog) return;
        genStatusLog.textContent += msg + '\n';
        genStatusLog.scrollTop = genStatusLog.scrollHeight;
      }

      es.addEventListener('status', (e) => {
        try{
          const d = JSON.parse(e.data);
          const msg = String(d.message || d.stage || 'Working…');
          if(genStatus) genStatus.textContent = msg;
          appendLog(msg);
          INFO('generate:status', msg);
        }catch(_){}
      });

      es.addEventListener('completed', (e) => {
        try{
          const d = JSON.parse(e.data);
          const msg = String(d.message || 'Completed');
          if(genStatus) genStatus.textContent = msg;
          appendLog('✓ ' + msg);
          INFO('generate:completed', d);
        }catch(_){}
      });

      es.addEventListener('error', (e) => {
        // SSE spec: browser fires generic error on close
        if(es.readyState === EventSource.CLOSED) return;
        try{
          const d = JSON.parse(e.data);
          const msg = String(d.message || d.error_message || 'Error');
          if(genStatus) genStatus.textContent = 'Error: ' + msg;
          appendLog('✗ ' + msg);
          if(d.hint) appendLog('  Hint: ' + d.hint);
          INFO('generate:error', d);
        }catch(_){
          if(genStatus) genStatus.textContent = 'Connection error';
          appendLog('✗ Connection lost');
          INFO('generate:error', 'connection lost');
        }
      });

      es.addEventListener('done', () => {
        INFO('generate:done');
        es.close();
        finishGeneration();
      });

      // Safety: if the stream doesn't emit 'done', close after 3 min
      setTimeout(() => {
        if(es.readyState !== EventSource.CLOSED){
          INFO('generate:timeout – closing SSE');
          es.close();
          finishGeneration();
        }
      }, 180000);
    }

    function finishGeneration(){
      if(genBtn){
        genBtn.disabled = false;
        genBtn.textContent = 'Generate New Report';
      }
      // Hide overlay after brief delay so user can read final status
      setTimeout(() => {
        if(overlay) overlay.style.display = 'none';
      }, 1200);
      // Refresh the report list
      loadReportList();
    }

    /* ---------- wire up DOM events ---------- */

    if(reportSelect){
      reportSelect.addEventListener('change', () => {
        const val = reportSelect.value;
        if(val) loadReport(val);
      });
    }

    if(fileSelect){
      fileSelect.addEventListener('change', filterAndRender);
    }

    if(genBtn){
      genBtn.addEventListener('click', (e) => {
        e.preventDefault();
        if(genBtn.disabled) return;
        startGeneration();
      });
    }

    /* ---------- action button delegation ---------- */
    if(contentEl){
      /* Track expand/collapse state via native <details> toggle event */
      contentEl.addEventListener('toggle', (e) => {
        const details = e.target;
        if(details.tagName !== 'DETAILS') return;
        const tk = details.dataset.tradeKey || (details.closest && details.closest('.trade-card')?.dataset.tradeKey);
        if(tk) _expandState[tk] = details.open;
      }, true); /* useCapture — toggle doesn't bubble */

      contentEl.addEventListener('click', (e) => {
        /* Copy trade key button */
        const copyBtn = e.target.closest('[data-copy-trade-key]');
        if(copyBtn){
          e.preventDefault();
          e.stopPropagation();
          tc.copyTradeKey(copyBtn.dataset.copyTradeKey, copyBtn);
          return;
        }

        /* Buttons inside <summary> must NOT trigger expand/collapse */
        const btn = e.target.closest('[data-action]');
        if(btn){
          /* Stop the click from bubbling up to <summary> toggle behavior */
          e.preventDefault();
          e.stopPropagation();
          const action = btn.dataset.action;
          const cardEl = btn.closest('.trade-card');
          const tradeIdx = cardEl ? parseInt(cardEl.dataset.idx, 10) : -1;
          const trade = currentTrades[tradeIdx];
          if(!trade) return;

          // Build a clean model + action payload (no raw JSON in handlers)
          const model   = _mapper.map(trade, state.activeConfig.strategyId);
          const payload = _mapper.buildTradeActionPayload(model);

          if(action === 'execute'){
            if(window.BenTradeExecutionModal && window.BenTradeExecutionModal.open){
              window.BenTradeExecutionModal.open(trade, payload);
            }else{
              INFO('No execution modal available');
            }
          }else if(action === 'reject'){
            /* Reject this trade — POST to decisions endpoint */
            const body = { trade_key: payload.tradeKey, symbol: payload.symbol, strategy: payload.strategyId, action: 'reject' };
            fetch('/api/decisions/reject', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
              .then(res => {
                if(res.ok){
                  if(cardEl) cardEl.classList.add('manually-rejected');
                  btn.disabled = true;
                  btn.textContent = 'Rejected';
                  INFO(`Rejected trade ${payload.tradeKey}`);
                }else{
                  INFO(`Reject failed (${res.status})`);
                }
              })
              .catch(err => INFO('Reject error: ' + err.message));
          }else if(action === 'model-analysis'){
            /* Run model analysis — navigate to model analysis page with trade context */
            const qs = new URLSearchParams({ symbol: payload.symbol, strategy: payload.strategyId, trade_key: payload.tradeKey || '' });
            window.location.hash = '#/admin/model-analysis?' + qs.toString();
          }else if(action === 'workbench'){
            if(payload.tradeKey){
              window.location.hash = '#/admin/data-workbench?trade_key=' + encodeURIComponent(payload.tradeKey);
            }else if(tc.openDataWorkbenchByTrade){
              tc.openDataWorkbenchByTrade(trade);
            }
          }else if(action === 'data-workbench'){
            /* Send to Data Workbench — ingest the full trade */
            if(tc.openDataWorkbenchByTrade){
              tc.openDataWorkbenchByTrade(trade);
            }else if(payload.tradeKey){
              window.location.hash = '#/admin/data-workbench?trade_key=' + encodeURIComponent(payload.tradeKey);
            }
          }
          return;
        }
      });
    }

    /* ---------- initial load ---------- */
    loadReportList();
  }

  return {
    mount,
  };
})();

window.BenTradePages.initStrategyDashboard = function initStrategyDashboard(rootEl, config){
  return window.BenTradeStrategyShell?.mount?.(rootEl, config || {});
};

window.BenTradePages.initCreditSpreads = function initCreditSpreads(rootEl){
  return window.BenTradePages.initStrategyDashboard(rootEl, {
    strategyId: 'credit_spread',
    title: 'Credit Spread Analysis',
    endpoint: {
      reports: '/api/strategies/credit_spread/reports',
      generateSse: '/api/strategies/credit_spread/generate',
    },
    defaultFilters: {},
    filterMode: 'credit-spread',
    advancedEnabled: false,
  });
};

window.BenTradePages.initStrategyCreditPut = function initStrategyCreditPut(rootEl){
  return window.BenTradePages.initStrategyDashboard(rootEl, {
    strategyId: 'credit_spread',
    title: 'Strategy: Credit Put Spread',
    endpoint: {
      reports: '/api/strategies/credit_spread/reports',
      generateSse: '/api/strategies/credit_spread/generate',
    },
    defaultFilters: { underlying: 'ALL' },
    filterMode: 'credit-spread',
  });
};

window.BenTradePages.initStrategyCreditCall = function initStrategyCreditCall(rootEl){
  return window.BenTradePages.initStrategyDashboard(rootEl, {
    strategyId: 'credit_spread',
    title: 'Strategy: Credit Call Spread',
    endpoint: {
      reports: '/api/strategies/credit_spread/reports',
      generateSse: '/api/strategies/credit_spread/generate',
    },
    defaultFilters: { underlying: 'ALL', spread_type: 'call_credit_spread' },
    filterMode: 'credit-spread',
  });
};

window.BenTradePages.initStrategyIronCondor = function initStrategyIronCondor(rootEl){
  return window.BenTradePages.initStrategyDashboard(rootEl, {
    strategyId: 'iron_condor',
    title: 'Iron Condor Analysis',
    endpoint: {
      reports: '/api/strategies/iron_condor/reports',
      generateSse: '/api/strategies/iron_condor/generate',
    },
    defaultFilters: {},
    filterMode: 'iron-condor',
  });
};

window.BenTradePages.initDebitSpreads = function initDebitSpreads(rootEl){
  return window.BenTradePages.initStrategyDashboard(rootEl, {
    strategyId: 'debit_spreads',
    title: 'Debit Spread Analysis',
    endpoint: {
      reports: '/api/strategies/debit_spreads/reports',
      generateSse: '/api/strategies/debit_spreads/generate',
    },
    defaultFilters: {},
    filterMode: 'debit-spreads',
  });
};

window.BenTradePages.initButterflies = function initButterflies(rootEl){
  return window.BenTradePages.initStrategyDashboard(rootEl, {
    strategyId: 'butterflies',
    title: 'Butterfly Analysis',
    endpoint: {
      reports: '/api/strategies/butterflies/reports',
      generateSse: '/api/strategies/butterflies/generate',
    },
    defaultFilters: {},
    filterMode: 'butterflies',
  });
};

window.BenTradePages.initCalendar = function initCalendar(rootEl){
  return window.BenTradePages.initStrategyDashboard(rootEl, {
    strategyId: 'calendars',
    title: 'Calendar Spread Analysis',
    endpoint: {
      reports: '/api/strategies/calendars/reports',
      generateSse: '/api/strategies/calendars/generate',
    },
    defaultFilters: {},
    filterMode: 'calendar',
  });
};

window.BenTradePages.initIncome = function initIncome(rootEl){
  return window.BenTradePages.initStrategyDashboard(rootEl, {
    strategyId: 'income',
    title: 'Income Strategies',
    endpoint: {
      reports: '/api/strategies/income/reports',
      generateSse: '/api/strategies/income/generate',
    },
    defaultFilters: {},
    filterMode: 'income',
  });
};
