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
        qp.set('advanced_enabled', advancedEnabled ? 'true' : 'false');
        if(advancedEnabled){
          const filters = cfg.currentFilters || {};
          Object.entries(filters).forEach(([key, value]) => {
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
    controls.appendChild(btnDefaults);
    controls.appendChild(btnGenerateDefaults);
    controls.appendChild(btnReset);
    controls.appendChild(why);

    row.appendChild(fieldWrap);
    row.appendChild(controls);
    host.appendChild(row);

    const sticky = { ...(config.defaultFilters || {}) };

    const setAdvancedEnabled = (enabled) => {
      const on = !!enabled;
      state.activeConfig.advancedEnabled = on;
      fieldWrap.style.display = on ? 'flex' : 'none';
      btnDefaults.style.display = on ? '' : 'none';
      btnReset.style.display = on ? '' : 'none';
      why.style.display = on ? '' : 'none';
      const sharedToggle = document.getElementById('manualFiltersEnabled');
      if(sharedToggle){
        sharedToggle.checked = on;
      }
      const sharedPanel = document.getElementById('manualFiltersPanel');
      if(sharedPanel){
        sharedPanel.style.display = 'none';
      }
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
      const defaults = window.BenTradeStrategyDefaults?.getStrategyDefaults?.(strategyKey(config)) || {};
      fields.forEach((field) => {
        const input = row.querySelector(`#${CSS.escape(inputId(mode, field.key))}`);
        writeFieldValue(field, input, defaults[field.key]);
      });
      writeFiltersFromInputs();
      showToast('Defaults applied');
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

    const sharedAdvancedToggle = document.getElementById('manualFiltersEnabled');
    if(sharedAdvancedToggle){
      const label = sharedAdvancedToggle.closest('label');
      const textSpan = label ? label.querySelector('span') : null;
      if(textSpan) textSpan.textContent = 'Use Advanced Filters';
      sharedAdvancedToggle.checked = !!state.activeConfig.advancedEnabled;
      if(!sharedAdvancedToggle.dataset.shellBound){
        sharedAdvancedToggle.dataset.shellBound = '1';
        sharedAdvancedToggle.addEventListener('change', () => {
          advancedToggle.checked = !!sharedAdvancedToggle.checked;
          setAdvancedEnabled(advancedToggle.checked);
        });
      }
    }

    btnDefaults.addEventListener('click', applyDefaults);
    btnReset.addEventListener('click', clearInputs);
    btnGenerateDefaults.addEventListener('click', () => {
      applyDefaults();
      advancedToggle.checked = false;
      setAdvancedEnabled(false);
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
      metricColumns: Array.isArray(config.metricColumns) ? config.metricColumns : [],
      filterMode: String(config?.filterMode || ''),
      currentFilters: { ...(config.defaultFilters || {}) },
      advancedEnabled: isTruthy(config?.advancedEnabled),
    };

    rootEl.dataset.strategyId = state.activeConfig.strategyId;
    rootEl.dataset.strategyTitle = state.activeConfig.title;

    window.BenTrade?.initCreditSpread?.(rootEl);

    try{
      const host = rootEl.querySelector('.file-selector');
      buildForm(host, state.activeConfig);
    }catch(_err){
    }
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
    metricColumns: ['pop', 'ev', 'return_on_risk', 'max_loss', 'iv_rv_ratio', 'rank_score'],
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
    metricColumns: ['pop', 'ev', 'return_on_risk', 'max_loss', 'iv_rv_ratio', 'rank_score'],
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
    defaultFilters: { underlying: 'ALL', spread_type: 'credit_call_spread' },
    metricColumns: ['pop', 'ev', 'return_on_risk', 'max_loss', 'iv_rv_ratio', 'rank_score'],
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
    metricColumns: ['theta_capture', 'expected_move_ratio', 'symmetry_score', 'tail_risk_score', 'liquidity_score', 'rank_score'],
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
    metricColumns: ['ev_to_risk', 'return_on_risk', 'liquidity_score', 'conviction_score'],
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
    metricColumns: ['peak_profit_at_center', 'payoff_slope', 'probability_of_touch_center', 'cost_efficiency', 'gamma_peak_score', 'liquidity_score', 'rank_score'],
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
    metricColumns: ['iv_term_structure_score', 'move_risk_score', 'liquidity_score', 'vega_exposure', 'theta_structure', 'rank_score'],
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
    metricColumns: ['annualized_yield_on_collateral', 'premium_per_day', 'downside_buffer', 'assignment_risk_score', 'liquidity_score', 'iv_rv_ratio', 'rank_score'],
    filterMode: 'income',
  });
};
