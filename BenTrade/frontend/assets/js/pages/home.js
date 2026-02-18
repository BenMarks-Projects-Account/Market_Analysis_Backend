window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initHome = function initHome(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const api = window.BenTradeApi;

  const regimeStripEl = scope.querySelector('#homeRegimeStrip');
  const regimeComponentsEl = scope.querySelector('#homeRegimeComponents');
  const playbookChipsEl = scope.querySelector('#homePlaybookChips');
  const topPickEl = scope.querySelector('#homeTopPick');
  const scanPresetEl = scope.querySelector('#homeScanPreset');
  const runQueueBtnEl = scope.querySelector('#homeRunQueueBtn');
  const stopQueueBtnEl = scope.querySelector('#homeStopQueueBtn');
  const queueProgressEl = scope.querySelector('#homeQueueProgress');
  const queueCurrentEl = scope.querySelector('#homeQueueCurrent');
  const queueCountEl = scope.querySelector('#homeQueueCount');
  const queueSpinnerEl = scope.querySelector('#homeQueueSpinner');
  const queueLogEl = scope.querySelector('#homeQueueLog');
  const scanStatusEl = scope.querySelector('#homeScanStatus');
  const scanErrorEl = scope.querySelector('#homeScanError');
  const signalHubEl = scope.querySelector('#homeSignalHub');
  const indexTilesEl = scope.querySelector('#homeIndexTiles');
  const spyChartEl = scope.querySelector('#homeSpyChart');
  const sectorBarsEl = scope.querySelector('#homeSectorBars');
  const opportunitiesEl = scope.querySelector('#homeOpportunities');
  const strategyRowsEl = scope.querySelector('#homeStrategyRows');
  const strategyMiniEl = scope.querySelector('#homeStrategyMini');
  const riskTilesEl = scope.querySelector('#homeRiskTiles');
  const macroTilesEl = scope.querySelector('#homeMacroTiles');
  const strategyPlaybookEl = scope.querySelector('#homeStrategyPlaybook');
  const fullRefreshBtnEl = scope.querySelector('#homeFullRefreshBtn');
  const refreshBtnEl = scope.querySelector('#homeRefreshBtn');
  const refreshingBadgeEl = scope.querySelector('#homeRefreshingBadge');
  const lastUpdatedEl = scope.querySelector('#homeLastUpdated');
  const vixChartEl = scope.querySelector('#homeVixChart');
  const sourceHealthEl = scope.querySelector('#homeSourceHealth');
  const errorEl = scope.querySelector('#homeError');

  if(!regimeStripEl || !regimeComponentsEl || !playbookChipsEl || !topPickEl || !scanPresetEl || !runQueueBtnEl || !stopQueueBtnEl || !queueProgressEl || !queueCurrentEl || !queueCountEl || !queueSpinnerEl || !queueLogEl || !scanStatusEl || !scanErrorEl || !signalHubEl || !indexTilesEl || !spyChartEl || !sectorBarsEl || !opportunitiesEl || !strategyRowsEl || !strategyMiniEl || !riskTilesEl || !macroTilesEl || !strategyPlaybookEl || !fullRefreshBtnEl || !refreshBtnEl || !refreshingBadgeEl || !lastUpdatedEl || !vixChartEl || !errorEl){
    return;
  }

  let latestOpportunities = [];
  const opportunityModelState = new Map();
  const topPickModelState = { key: null, model: null };
  const devLoggedCards = new Set();
  let devLoggedTopPickSource = false;

  function setScanError(text){
    if(!text){
      scanErrorEl.style.display = 'none';
      scanErrorEl.textContent = '';
      return;
    }
    scanErrorEl.style.display = 'block';
    scanErrorEl.textContent = String(text);
  }

  function setScanStatus(text, isBusy = false){
    if(!text){
      scanStatusEl.style.display = 'none';
      scanStatusEl.innerHTML = '';
      return;
    }
    scanStatusEl.style.display = 'block';
    scanStatusEl.innerHTML = isBusy
      ? `<span class="home-scan-status"><span class="home-scan-spinner" aria-hidden="true"></span><span>${String(text)}</span></span>`
      : String(text);
  }

  const INDEX_SYMBOLS = ['SPY', 'QQQ', 'IWM', 'DIA'];
  const SECTOR_SYMBOLS = ['XLF', 'XLK', 'XLE', 'XLY', 'XLP', 'XLV', 'XLI', 'XLB', 'XLRE', 'XLU', 'XLC'];
  const SECTOR_META = {
    XLF: { name: 'Financials', description: 'Banks, insurers, and diversified financial services firms' },
    XLK: { name: 'Technology', description: 'Software, semiconductors, hardware, and IT services' },
    XLE: { name: 'Energy', description: 'Oil, gas, exploration, production, and energy equipment' },
    XLY: { name: 'Consumer Discretionary', description: 'Retail, autos, media, and optional consumer spending' },
    XLP: { name: 'Consumer Staples', description: 'Everyday household goods, food, and beverage producers' },
    XLV: { name: 'Health Care', description: 'Pharma, biotech, medical devices, and health providers' },
    XLI: { name: 'Industrials', description: 'Aerospace, machinery, transportation, and business services' },
    XLB: { name: 'Materials', description: 'Chemicals, metals, mining, and construction materials' },
    XLRE: { name: 'Real Estate', description: 'REITs and diversified real estate management firms' },
    XLU: { name: 'Utilities', description: 'Electric, gas, and water utility providers' },
    XLC: { name: 'Communication Services', description: 'Telecom, media, entertainment, and interactive platforms' },
  };
  const STRATEGY_SOURCES = [
    { id: 'credit_spread', label: 'Credit Spread', route: '#/credit-spread' },
    { id: 'debit_spreads', label: 'Debit Spreads', route: '#/debit-spreads' },
    { id: 'iron_condor', label: 'Iron Condor', route: '#/iron-condor' },
    { id: 'butterflies', label: 'Butterflies', route: '#/butterflies' },
  ];
  const PLAYBOOK_ROUTES = {
    credit_put_spread: '#/credit-spread',
    covered_call: '#/income',
    debit_call_spread: '#/debit-spreads',
    iron_condor: '#/strategy-iron-condor',
    debit_put_spread: '#/debit-spreads',
    cash_secured_put_far_otm: '#/income',
    calendar: '#/calendar',
    hedges: '#/portfolio-risk',
    short_put_spreads_near_spot: '#/credit-spread',
    iron_condor_tight: '#/strategy-iron-condor',
    credit_spreads_wider: '#/credit-spread',
    butterflies: '#/butterflies',
    aggressive_directional_debit_spreads: '#/debit-spreads',
    aggressive_short_calls: '#/income',
  };

  function setError(text){
    if(!text){
      errorEl.style.display = 'none';
      errorEl.textContent = '';
      return;
    }
    errorEl.style.display = 'block';
    errorEl.textContent = String(text);
  }

  function toNumber(value){
    if(value === null || value === undefined || value === '') return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function fmt(value, digits = 2){
    const n = toNumber(value);
    if(n === null) return '0.00';
    return n.toFixed(digits);
  }

  function fmtSigned(value, digits = 2){
    const n = toNumber(value);
    if(n === null) return '0.00';
    const text = n.toFixed(digits);
    return n > 0 ? `+${text}` : text;
  }

  function fmtPct(value, digits = 2){
    const n = toNumber(value);
    if(n === null) return '0.00%';
    return `${n >= 0 ? '+' : ''}${(n * 100).toFixed(digits)}%`;
  }

  function toPctString(value, digits = 1){
    const n = toNumber(value);
    if(n === null) return '0.0%';
    return `${n.toFixed(digits)}%`;
  }

  function normalizeSymbol(value){
    return String(value || '').trim().toUpperCase();
  }

  function metricMissingReason(sourceType, metric){
    const type = String(sourceType || '').toLowerCase();
    const key = String(metric || '').toLowerCase();
    if(type === 'stock'){
      if(key === 'ev') return 'EV not computed for equities';
      if(key === 'pop') return 'POP not computed for equities';
      if(key === 'ror') return 'RoR not computed for equities';
      return 'Not computed for equities';
    }
    return 'Missing from source payload';
  }

  function isLikelyOptionsStrategy(value){
    const text = String(value || '').toLowerCase();
    if(!text) return false;
    return text.includes('credit')
      || text.includes('debit')
      || text.includes('condor')
      || text.includes('butter')
      || text.includes('calendar')
      || text.includes('spread')
      || text.includes('covered_call')
      || text.includes('cash_secured_put');
  }

  function isDevInstrumentationEnabled(){
    try{
      const host = String(location.hostname || '').toLowerCase();
      const localHost = host === 'localhost' || host === '127.0.0.1' || host.endsWith('.local');
      if(localHost) return true;
      return localStorage.getItem('bentrade_debug_home_metrics') === '1';
    }catch(_err){
      return false;
    }
  }

  function logOpportunityInstrumentationOnce(idea, idx){
    if(!isDevInstrumentationEnabled()) return;
    const key = opportunityKey(idea, idx);
    if(devLoggedCards.has(key)) return;
    devLoggedCards.add(key);

    const trade = (idea?.trade && typeof idea.trade === 'object') ? idea.trade : {};
    const fields = {
      ev_to_risk: trade?.ev_to_risk,
      ev_per_share: trade?.ev_per_share,
      p_win_used: trade?.p_win_used,
      pop_delta_approx: trade?.pop_delta_approx,
      return_on_risk: trade?.return_on_risk,
      max_profit_per_share: trade?.max_profit_per_share,
      max_loss_per_share: trade?.max_loss_per_share,
    };
    console.debug('[HomeMetrics] card_source', {
      symbol: idea?.symbol,
      strategy: idea?.strategy,
      source_feed: idea?.source_feed || 'latest analysis_*.json trades',
      source: idea?.source,
      sourceType: idea?.sourceType,
      fields,
      normalized: {
        ev: idea?.ev,
        pop: idea?.pop,
        ror: idea?.ror,
      },
    });
  }

  function normalizeTradeIdea(row, source){
    const symbol = normalizeSymbol(row?.underlying || row?.underlying_symbol || row?.symbol);
    const score = toNumber(row?.composite_score ?? row?.trade_quality_score ?? row?.scanner_score ?? row?.score) ?? 0;
    const comp = (row?.computed && typeof row.computed === 'object') ? row.computed : {};
    const ev = toNumber(comp?.expected_value ?? row?.ev_per_contract ?? row?.expected_value ?? row?.ev_per_share ?? row?.ev ?? row?.edge);
    const pop = toNumber(comp?.pop ?? row?.p_win_used ?? row?.pop_delta_approx ?? row?.probability_of_profit ?? row?.pop);
    const ror = toNumber(comp?.return_on_risk ?? row?.return_on_risk ?? row?.ror);
    const strategy = String(row?.spread_type || row?.strategy || row?.recommended_strategy || source?.label || 'idea');
    const recommendation = String(row?.model_evaluation?.recommendation || row?.recommendation || 'N/A');

    return {
      symbol: symbol || 'N/A',
      strategy,
      score,
      ev,
      pop,
      ror,
      recommendation,
      route: source?.route || '#/credit-spread',
      source: source?.label || 'Unknown',
      trade: row,
    };
  }

  function computeRor(raw){
    const comp = (raw?.computed && typeof raw.computed === 'object') ? raw.computed : {};
    const direct = toNumber(comp?.return_on_risk ?? raw?.return_on_risk ?? raw?.ror);
    if(direct !== null) return direct;
    const maxProfit = toNumber(comp?.max_profit ?? raw?.max_profit_per_contract ?? raw?.max_profit_per_share ?? raw?.max_profit);
    const maxLoss = toNumber(comp?.max_loss ?? raw?.max_loss_per_contract ?? raw?.max_loss_per_share ?? raw?.max_loss);
    if(maxProfit !== null && maxLoss !== null && maxLoss > 0){
      return maxProfit / maxLoss;
    }
    return null;
  }

  function normalizeOpportunity(candidate, sourceType){
    const row = candidate && typeof candidate === 'object' ? candidate : {};
    const raw = row?.trade && typeof row.trade === 'object'
      ? row.trade
      : (row?.raw && typeof row.raw === 'object' ? row.raw : row);

    const inferredSource = String(sourceType || row?.sourceType || row?.type || '').toLowerCase();
    const symbol = normalizeSymbol(row?.symbol || raw?.symbol || raw?.underlying || raw?.underlying_symbol) || 'N/A';
    const strategy = String(row?.strategy || raw?.spread_type || raw?.strategy || raw?.recommended_strategy || 'idea');
    const strategySuggestsOptions = isLikelyOptionsStrategy(strategy);
    const isStock = !strategySuggestsOptions && (inferredSource === 'stock' || String(row?.source || '').toLowerCase().includes('stock scanner'));
    const rank = toNumber(row?.rank ?? row?.score ?? row?.rank_score ?? raw?.rank_score ?? raw?.composite_score ?? raw?.trade_quality_score) ?? 0;

    let ev = null;
    let pop = null;
    let ror = null;
    const notes = [];

    if(isStock){
      ev = null;
      pop = null;
      ror = null;
      notes.push('Not computed for equities ideas yet.');
    }else{
      // Prefer per-contract EV from computed (unified with scanner), then key_metrics, then legacy flat fields
      const comp = (raw?.computed && typeof raw.computed === 'object') ? raw.computed : {};
      ev = toNumber(comp?.expected_value ?? row?.key_metrics?.ev_to_risk ?? row?.key_metrics?.ev ?? row?.ev_to_risk ?? raw?.ev_to_risk ?? row?.ev);
      if(ev === null){
        ev = toNumber(raw?.ev_per_contract ?? raw?.expected_value ?? raw?.ev ?? row?.ev_per_share ?? raw?.ev_per_share ?? row?.edge ?? row?.expected_value);
      }

      pop = toNumber(comp?.pop ?? row?.p_win_used ?? raw?.p_win_used ?? row?.key_metrics?.pop ?? row?.pop);
      if(pop === null){
        pop = toNumber(row?.pop_delta_approx ?? raw?.pop_delta_approx ?? row?.probability_of_profit ?? row?.probability_of_profit ?? row?.pop ?? raw?.pop);
      }

      if(pop !== null && pop > 1.0){
        pop = pop / 100.0;
      }

      ror = computeRor(raw);
      if(ror === null){
        ror = computeRor(row);
      }
      if(ror === null){
        ror = toNumber(row?.key_metrics?.ror ?? row?.key_metrics?.return_on_risk);
      }
      if(ror !== null && ror > 1.0){
        ror = ror / 100.0;
      }
    }

    const modelPayload = row?.model && typeof row.model === 'object'
      ? row.model
      : (raw?.model_evaluation && typeof raw.model_evaluation === 'object' ? raw.model_evaluation : null);

    const model = modelPayload
      ? {
        status: 'available',
        recommendation: String(modelPayload?.recommendation || 'UNKNOWN').toUpperCase(),
        confidence: toNumber(modelPayload?.confidence),
        summary: String(modelPayload?.summary || '').trim(),
      }
      : {
        status: 'not_run',
        recommendation: 'Not run',
        confidence: null,
        summary: '',
      };

    const price = toNumber(raw?.price ?? row?.key_metrics?.price);
    const rsi14 = toNumber(raw?.metrics?.rsi14 ?? row?.key_metrics?.rsi14 ?? raw?.signals?.rsi_14 ?? raw?.rsi14);
    const ema20 = toNumber(raw?.metrics?.ema20 ?? row?.key_metrics?.ema20 ?? raw?.ema20);
    const ivrv = toNumber(raw?.metrics?.iv_rv_ratio ?? row?.key_metrics?.iv_rv_ratio ?? raw?.signals?.iv_rv_ratio ?? raw?.iv_rv_ratio);
    const trendRaw = String(raw?.trend || row?.key_metrics?.trend || raw?.signals?.trend || '').trim().toLowerCase();
    const trend = trendRaw || ((price !== null && ema20 !== null) ? (price >= ema20 ? 'up' : 'down') : null);
    const bidAskSpreadPct = toNumber(raw?.bid_ask_spread_pct ?? row?.key_metrics?.bid_ask_spread_pct);
    const volume = toNumber(raw?.volume ?? row?.key_metrics?.volume);
    const openInterest = toNumber(raw?.open_interest ?? row?.key_metrics?.open_interest);
    let liquidity = null;
    if(bidAskSpreadPct !== null){
      liquidity = Math.max(0, Math.min(100, 100 - (bidAskSpreadPct * 100)));
    } else if(volume !== null || openInterest !== null){
      liquidity = Math.max(0, Math.min(100, ((volume || 0) / 1000) * 40 + ((openInterest || 0) / 3000) * 60));
    }
    let ivrvFlag = null;
    if(ivrv !== null){
      if(ivrv > 1.2) ivrvFlag = 'rich';
      else if(ivrv < 0.9) ivrvFlag = 'cheap';
      else ivrvFlag = 'balanced';
    }

    return {
      symbol,
      strategy,
      rank,
      ev,
      pop,
      ror,
      model,
      why: Array.isArray(row?.why) ? row.why : [],
      key_metrics: {
        price,
        rsi14,
        ema20,
        trend,
        iv_rv_ratio: ivrv,
        iv_rv_flag: ivrvFlag,
        liquidity,
      },
      route: row?.route || row?.actions?.open_route || '#/credit-spread',
      source: row?.source || (isStock ? 'Stock Scanner' : 'Strategy'),
      source_feed: row?.source_feed || (isStock ? 'stock scanner' : 'latest analysis_*.json trades'),
      trade: raw,
      trade_payload: isStock ? null : {
        ...raw,
        underlying: String(raw?.underlying || raw?.underlying_symbol || symbol || '').toUpperCase(),
        underlying_symbol: String(raw?.underlying_symbol || raw?.underlying || symbol || '').toUpperCase(),
        spread_type: String(raw?.spread_type || raw?.strategy || strategy || ''),
      },
      equity_payload: isStock ? {
        symbol,
        idea: { ...raw, symbol },
      } : null,
      notes,
      sourceType: isStock ? 'stock' : 'options',
      actions: row?.actions || {},
    };
  }

  function escapeHtml(value){
    return String(value || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function opportunityKey(idea, idx){
    const symbol = normalizeSymbol(idea?.symbol || idea?.trade?.underlying || idea?.trade?.symbol || 'N/A');
    const strategy = String(idea?.strategy || idea?.trade?.spread_type || idea?.trade?.strategy || 'idea');
    const source = String(idea?.sourceType || idea?.source || 'unknown');
    return `${symbol}|${strategy}|${source}|${Number.isFinite(idx) ? idx : 0}`;
  }

  function formatModelSummary(model){
    if(!model || model.status === 'not_run') return 'Not run';
    if(model.status === 'running') return 'Running...';
    if(model.status === 'error'){
      const summary = String(model.summary || '').trim();
      return summary ? `Error • ${summary}` : 'Error • Model analysis failed';
    }
    const rec = String(model.recommendation || 'UNKNOWN').toUpperCase();
    const confText = toNumber(model.confidence) === null ? '' : ` (${(toNumber(model.confidence) * 100).toFixed(0)}%)`;
    const summary = String(model.summary || '').trim();
    if(summary){
      return `${rec}${confText} • ${summary}`;
    }
    return `${rec}${confText}`;
  }

  function routeForOpportunity(idea){
    if(!idea || idea.sourceType === 'stock') return '#/stock-analysis';
    const strategy = String(idea?.strategy || idea?.trade?.spread_type || idea?.trade?.strategy || '').toLowerCase();
    if(strategy.includes('credit_put')) return '#/credit-spread';
    if(strategy.includes('credit_call')) return '#/credit-spread';
    if(strategy.includes('credit_spread')) return '#/credit-spread';
    if(strategy.includes('iron_condor')) return '#/strategy-iron-condor';
    if(strategy.includes('debit')) return '#/debit-spreads';
    if(strategy.includes('butter')) return '#/butterflies';
    if(strategy.includes('calendar')) return '#/calendar';
    if(strategy.includes('income') || strategy.includes('covered_call')) return '#/income';
    const fromActions = String(idea?.actions?.open_route || idea?.route || '#/credit-spread');
    return fromActions.startsWith('#') ? fromActions : '#/credit-spread';
  }

  function persistSelectedOpportunity(idea){
    const symbol = String(idea?.symbol || '').toUpperCase();
    if(symbol){
      localStorage.setItem('bentrade_selected_symbol', symbol);
    }
    const candidateMinimal = {
      symbol,
      strategy: String(idea?.strategy || ''),
      sourceType: String(idea?.sourceType || ''),
      route: routeForOpportunity(idea),
      rank: toNumber(idea?.rank),
      trade: idea?.trade_payload || idea?.trade || null,
      equity: idea?.equity_payload || null,
    };
    localStorage.setItem('bentrade_selected_candidate', JSON.stringify(candidateMinimal));
  }

  function openAnalysisForOpportunity(idea){
    if(!idea) return;
    persistSelectedOpportunity(idea);
    location.hash = routeForOpportunity(idea);
  }

  function sendToWorkbenchForOpportunity(idea, destination = '#/trade-testing'){
    if(!idea) return;
    const strategy = String(idea?.trade?.spread_type || idea?.trade?.strategy || idea.strategy || 'credit_put_spread');
    const payload = {
      from: 'home_dashboard',
      ts: new Date().toISOString(),
      input: {
        symbol: String(idea?.symbol || ''),
        strategy,
        expiration: idea?.trade?.expiration || 'NA',
        short_strike: idea?.trade?.short_strike ?? null,
        long_strike: idea?.trade?.long_strike ?? null,
        contractsMultiplier: 100,
      },
      trade_key: `${String(idea?.symbol || 'N/A')}|NA|${strategy}|NA|NA|NA`,
      note: `Home opportunity ${String(idea?.source || 'Unknown')} rank ${fmt(idea?.rank, 1)}`,
    };
    localStorage.setItem('bentrade_workbench_handoff_v1', JSON.stringify(payload));
    location.hash = destination;
  }

  function buildExecutionTradeFromIdea(idea){
    const src = (idea?.trade_payload && typeof idea.trade_payload === 'object')
      ? idea.trade_payload
      : ((idea?.trade && typeof idea.trade === 'object') ? idea.trade : {});
    const symbol = String(src?.underlying || src?.underlying_symbol || src?.symbol || idea?.symbol || '').toUpperCase();
    const strategy = String(src?.spread_type || src?.strategy || idea?.strategy || '');
    return {
      ...src,
      underlying: symbol,
      underlying_symbol: symbol,
      spread_type: strategy,
      strategy,
      symbol,
    };
  }

  function openExecuteForOpportunity(idea){
    if(!idea || idea.sourceType === 'stock'){
      return;
    }
    const trade = buildExecutionTradeFromIdea(idea);
    if(typeof window.executeTrade === 'function'){
      window.executeTrade(trade);
      return;
    }
    window.BenTradeExecutionModal?.open?.(trade || {}, { primaryLabel: 'Execute (off)' });
  }

  function strategyIdFromValue(value){
    const text = String(value || '').toLowerCase();
    if(!text) return null;
    if(text.includes('credit') || text.includes('put_spread') || text.includes('call_spread')) return 'credit_spread';
    if(text.includes('debit')) return 'debit_spreads';
    if(text.includes('iron_condor') || text.includes('condor')) return 'iron_condor';
    if(text.includes('butter') || text.includes('fly')) return 'butterflies';
    return null;
  }

  function hasUsableTradePayload(value){
    return !!(value && typeof value === 'object' && (value.short_strike !== undefined || value.long_strike !== undefined || value.expiration || value.contracts || value.snapshot));
  }

  function getModelSourceFromSession(){
    const sessionSource = window.BenTradeSessionState?.getCurrentReportFile?.();
    if(sessionSource) return String(sessionSource);
    if(window.currentReportFile) return String(window.currentReportFile);
    return null;
  }

  async function resolveModelSourceFile(idea){
    const direct = String(idea?.report_file || idea?.trade?.report_file || '').trim();
    if(direct) return direct;

    const sessionSource = getModelSourceFromSession();
    if(sessionSource) return sessionSource;

    const strategyId = String(idea?.strategy_id || strategyIdFromValue(idea?.strategy || idea?.trade?.spread_type || idea?.trade?.strategy) || '').trim();
    if(strategyId && api?.listStrategyReports){
      try{
        const files = await api.listStrategyReports(strategyId);
        const candidate = Array.isArray(files) && files.length ? String(files[0] || '').trim() : '';
        if(candidate) return candidate;
      }catch(_err){
      }
    }

    return null;
  }

  function findMatchingOpportunityForModel(idea){
    const symbol = normalizeSymbol(idea?.symbol || idea?.trade?.underlying || idea?.trade?.symbol || '');
    if(!symbol) return null;
    const strategyText = String(idea?.strategy || idea?.trade?.spread_type || idea?.trade?.strategy || '').toLowerCase();
    const normalizedIdeas = Array.isArray(latestOpportunities)
      ? latestOpportunities.map((row) => normalizeOpportunity(row, row?.sourceType)).filter((row) => row && row.sourceType === 'options')
      : [];

    const strict = normalizedIdeas.find((row) => {
      const sameSymbol = normalizeSymbol(row?.symbol) === symbol;
      const sameStrategy = String(row?.strategy || '').toLowerCase() === strategyText;
      return sameSymbol && sameStrategy;
    });
    if(strict) return strict;

    const loose = normalizedIdeas.find((row) => normalizeSymbol(row?.symbol) === symbol);
    return loose || null;
  }

  function resolveIdeaForModel(idea){
    if(idea?.sourceType === 'stock') return idea;
    if(hasUsableTradePayload(idea?.trade_payload || idea?.trade)){
      return idea;
    }
    return findMatchingOpportunityForModel(idea) || idea;
  }

  async function runModelForOpportunity(idea, onModel, originTag = 'home_opportunities'){
    if(!idea || idea.sourceType === 'stock'){
      return false;
    }

    const resolvedIdea = resolveIdeaForModel(idea);
    const sourceFile = await resolveModelSourceFile(resolvedIdea);
    if(!sourceFile){
      const nextModel = {
        status: 'error',
        recommendation: 'ERROR',
        confidence: null,
        summary: 'No report source available for model analysis.',
      };
      if(typeof onModel === 'function') onModel(nextModel);
      return false;
    }

    const tradePayload = {
      ...(resolvedIdea?.trade_payload && typeof resolvedIdea.trade_payload === 'object' ? resolvedIdea.trade_payload : {}),
      ...(resolvedIdea?.trade && typeof resolvedIdea.trade === 'object' ? resolvedIdea.trade : {}),
      underlying: String(resolvedIdea?.trade?.underlying || resolvedIdea?.trade?.underlying_symbol || resolvedIdea?.symbol || '').toUpperCase(),
      underlying_symbol: String(resolvedIdea?.trade?.underlying_symbol || resolvedIdea?.trade?.underlying || resolvedIdea?.symbol || '').toUpperCase(),
      spread_type: String(resolvedIdea?.trade?.spread_type || resolvedIdea?.trade?.strategy || resolvedIdea?.strategy || ''),
      home_origin: String(originTag || 'home_opportunities'),
    };
    if(typeof onModel === 'function'){
      onModel({ status: 'running', recommendation: 'RUNNING', confidence: null, summary: 'Running...' });
    }

    try{
      const result = await api.modelAnalyze(tradePayload, sourceFile);
      const me = result?.evaluated_trade?.model_evaluation || {};
      const nextModel = {
        status: 'available',
        recommendation: String(me?.recommendation || 'NEUTRAL').toUpperCase(),
        confidence: toNumber(me?.confidence),
        summary: String(me?.summary || '').trim(),
      };
      if(typeof onModel === 'function') onModel(nextModel);
      return true;
    }catch(err){
      const nextModel = {
        status: 'error',
        recommendation: 'ERROR',
        confidence: null,
        summary: String(err?.detail || err?.message || err || 'Model analysis failed'),
      };
      if(typeof onModel === 'function') onModel(nextModel);
      return false;
    }
  }

  function metricValueOrMissing(value, formatter, reason){
    const hasValue = toNumber(value) !== null;
    if(hasValue){
      return formatter(value);
    }
    const why = String(reason || 'Metric unavailable for this pick');
    return `<span class="home-missing-wrap">— <span class="home-missing-hint" title="${why}">?</span></span>`;
  }

  function renderSourceHealth(snapshot){
    if(!sourceHealthEl){
      return;
    }
    const entries = Object.entries(snapshot || {});
    if(!entries.length){
      sourceHealthEl.innerHTML = '<div class="stock-note">No source snapshot available.</div>';
      return;
    }
    const nameMap = { finnhub: 'Finnhub', yahoo: 'Yahoo', tradier: 'Tradier', fred: 'FRED' };
    sourceHealthEl.innerHTML = entries.map(([key, value]) => {
      const status = String(value?.status || '').toLowerCase();
      const dotClass = status === 'ok' ? 'status-green' : (status === 'down' ? 'status-red' : 'status-yellow');
      const label = nameMap[String(key || '').toLowerCase()] || String(key || '').toUpperCase();
      const message = String(value?.message || 'No message');
      return `
        <div class="diagnosticRow">
          <span class="diagnosticLabel">${label}</span>
          <span class="status-wrap" tabindex="0">
            <span class="status-dot ${dotClass}"></span>
            <span class="status-tooltip">${message}</span>
          </span>
        </div>
      `;
    }).join('');
  }

  function renderChart(svgEl, history, options){
    const points = Array.isArray(history) ? history.map((row) => toNumber(row?.close)).filter((v) => v !== null) : [];
    if(!points.length){
      svgEl.innerHTML = '';
      return;
    }

    const width = 800;
    const height = 220;
    const margin = { top: 12, right: 12, bottom: 22, left: 52 };
    const plotW = width - margin.left - margin.right;
    const plotH = height - margin.top - margin.bottom;
    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = Math.max(max - min, 0.0001);

    const xFor = (index) => margin.left + (index / Math.max(points.length - 1, 1)) * plotW;
    const yFor = (value) => margin.top + (1 - ((value - min) / span)) * plotH;

    const path = points.map((value, index) => `${index === 0 ? 'M' : 'L'} ${xFor(index).toFixed(2)} ${yFor(value).toFixed(2)}`).join(' ');

    const yTicks = Array.from({ length: 4 }, (_, idx) => {
      const ratio = idx / 3;
      const value = max - (span * ratio);
      return { value, y: yFor(value) };
    });

    const yGrid = yTicks.map((tick) => `<line x1="${margin.left}" y1="${tick.y.toFixed(2)}" x2="${(width - margin.right).toFixed(2)}" y2="${tick.y.toFixed(2)}" stroke="rgba(0,234,255,0.12)" stroke-width="1"></line>`).join('');
    const yLabels = yTicks.map((tick) => `<text x="${(margin.left - 8).toFixed(2)}" y="${(tick.y + 3).toFixed(2)}" text-anchor="end" fill="rgba(215,251,255,0.85)" font-size="10">${Number(tick.value).toFixed(2)}</text>`).join('');

    svgEl.innerHTML = `
      ${yGrid}
      <line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${(height - margin.bottom).toFixed(2)}" stroke="rgba(0,234,255,0.45)" stroke-width="1"></line>
      <line x1="${margin.left}" y1="${(height - margin.bottom).toFixed(2)}" x2="${(width - margin.right).toFixed(2)}" y2="${(height - margin.bottom).toFixed(2)}" stroke="rgba(0,234,255,0.45)" stroke-width="1"></line>
      ${yLabels}
      <path d="${path}" fill="none" stroke="${options?.stroke || 'rgba(0,234,255,0.95)'}" stroke-width="3"></path>
    `;
  }

  function renderRegime(regimePayload, spySummary, macro){
    const spyLast = toNumber(spySummary?.price?.last);
    const vix = toNumber(spySummary?.options_context?.vix ?? macro?.vix);
    const tenYear = toNumber(macro?.ten_year_yield);
    const regimeScore = toNumber(regimePayload?.regime_score) ?? 50;
    const regimeLabelRaw = String(regimePayload?.regime_label || 'NEUTRAL').toUpperCase();
    const regimeLabelText = regimeLabelRaw === 'RISK_ON' ? 'Risk-On' : (regimeLabelRaw === 'RISK_OFF' ? 'Risk-Off' : 'Neutral');
    const tone = regimeLabelRaw === 'RISK_ON' ? 'bullish' : (regimeLabelRaw === 'RISK_OFF' ? 'riskoff' : 'neutral');

    regimeStripEl.innerHTML = `
      <div class="statTile"><div class="statLabel">SPY</div><div class="statValue">${fmt(spyLast)}</div><div class="stock-note">${fmtPct(spySummary?.price?.change_pct)}</div></div>
      <div class="statTile"><div class="statLabel">VIX</div><div class="statValue">${fmt(vix)}</div></div>
      <div class="statTile"><div class="statLabel">10Y Yield</div><div class="statValue">${fmt(tenYear, 2)}%</div></div>
      <div class="statTile home-regime-pill ${tone}"><div class="statLabel">Regime</div><div class="statValue">${regimeLabelText}</div><div class="stock-note">Score ${fmt(regimeScore, 1)}/100</div></div>
    `;

    const componentOrder = ['trend', 'volatility', 'breadth', 'rates', 'momentum'];
    const components = regimePayload?.components || {};
    regimeComponentsEl.innerHTML = componentOrder.map((key) => {
      const item = components[key] || {};
      const score = toNumber(item?.score) ?? 0;
      const width = Math.max(2, Math.round(Math.min(Math.max(score, 0), 100)));
      const signals = Array.isArray(item?.signals) ? item.signals : [];
      const label = key.charAt(0).toUpperCase() + key.slice(1);
      let detailHtml = '';

      if(key === 'trend'){
        if(signals.length){
          const detailLines = signals.slice(0, 3).map((line) => `<div class="stock-note home-regime-note-line">• ${String(line)}</div>`).join('');
          detailHtml = `<div class="home-regime-note-stack">${detailLines}</div>`;
        } else {
          detailHtml = '<span class="home-missing-wrap">Trend data unavailable <span class="home-missing-hint" title="Trend data unavailable">?</span></span>';
        }
      } else {
        detailHtml = signals[0] ? String(signals[0]) : 'No signal detail';
      }

      return `
        <div class="home-regime-row">
          <div class="home-regime-name">${label}</div>
          <div class="home-regime-track"><div class="home-regime-fill" style="width:${width}%;"></div></div>
          <div class="home-regime-score">${toPctString(score, 0)}</div>
          <div class="stock-note home-regime-note">${detailHtml}</div>
        </div>
      `;
    }).join('');

    const playbook = regimePayload?.suggested_playbook || {};
    const primary = Array.isArray(playbook?.primary) ? playbook.primary : [];
    const avoid = Array.isArray(playbook?.avoid) ? playbook.avoid : [];
    const notes = Array.isArray(playbook?.notes) ? playbook.notes.slice(0, 2) : [];
    playbookChipsEl.innerHTML = `
      <div class="home-chip-group">
        <span class="stock-note">Primary:</span>
        ${(primary.length ? primary : ['none']).map((item) => `<span class="qtPill">${String(item)}</span>`).join('')}
      </div>
      <div class="home-chip-group">
        <span class="stock-note">Avoid:</span>
        ${(avoid.length ? avoid : ['none']).map((item) => `<span class="qtPill qtPill-warn">${String(item)}</span>`).join('')}
      </div>
      <div class="home-playbook-notes">${notes.length ? notes.map((note) => `<div class="stock-note">• ${String(note)}</div>`).join('') : '<div class="stock-note">• No playbook notes.</div>'}</div>
    `;
  }

  function renderIndexes(indexSummaries){
    indexTilesEl.innerHTML = INDEX_SYMBOLS.map((symbol) => {
      const payload = indexSummaries[symbol] || {};
      const price = payload?.price || {};
      const indicators = payload?.indicators || {};
      const last = toNumber(price.last);
      const pct = toNumber(price.change_pct);
      const rsi = toNumber(indicators.rsi14);
      const ema20 = toNumber(indicators.ema20);
      const trend = (last !== null && ema20 !== null)
        ? (last >= ema20 ? 'Above EMA20' : 'Below EMA20')
        : 'N/A';
      return `
        <div class="statTile home-index-tile">
          <div class="statLabel">${symbol}</div>
          <div class="statValue">${fmt(last)}</div>
          <div class="stock-note">${fmtPct(pct)} • RSI ${fmt(rsi, 1)}</div>
          <div class="stock-note">${trend}</div>
        </div>
      `;
    }).join('');
  }

  function renderSectors(sectorSummaries){
    const rows = SECTOR_SYMBOLS.map((symbol) => {
      const pct = toNumber(sectorSummaries[symbol]?.price?.change_pct) ?? 0;
      const meta = SECTOR_META[symbol] || { name: symbol, description: symbol };
      return { symbol, pct, meta };
    });
    const maxAbs = Math.max(...rows.map((row) => Math.abs(row.pct)), 0.01);

    sectorBarsEl.innerHTML = rows.map((row) => {
      const width = Math.max(4, Math.round((Math.abs(row.pct) / maxAbs) * 100));
      const positive = row.pct >= 0;
      const label = `${row.symbol} — ${row.meta.name}`;
      const tooltip = `${row.symbol}: ${row.meta.description}`;
      return `
        <div class="home-sector-row">
          <div class="home-sector-label" title="${tooltip}">${label}</div>
          <div class="home-sector-track">
            <div class="home-sector-fill ${positive ? 'positive' : 'negative'}" style="width:${width}%;"></div>
          </div>
          <div class="home-sector-pct">${fmtPct(row.pct, 2)}</div>
        </div>
      `;
    }).join('');
  }

  function renderOpportunities(ideas){
    latestOpportunities = Array.isArray(ideas) ? ideas.slice() : [];
    const top = latestOpportunities.slice(0, 5).map((idea, idx) => {
      const normalized = normalizeOpportunity(idea, idea?.sourceType);
      logOpportunityInstrumentationOnce(normalized, idx);
      const key = opportunityKey(normalized, idx);
      const modelState = opportunityModelState.get(key);
      if(modelState && typeof modelState === 'object'){
        normalized.model = {
          status: String(modelState.status || 'available'),
          recommendation: String(modelState.recommendation || normalized.model?.recommendation || 'UNKNOWN').toUpperCase(),
          confidence: toNumber(modelState.confidence),
          summary: String(modelState.summary || '').trim(),
        };
      }
      normalized._opKey = key;
      return normalized;
    });
    if(!top.length){
      opportunitiesEl.innerHTML = '<div class="loading">No opportunities available.</div>';
      return;
    }

    opportunitiesEl.innerHTML = top.map((idea, idx) => `
      <div class="trade-card home-op-card" data-idx="${idx}">
        <div class="trade-header">
          <div class="trade-type"><span class="qtPill">${idea.symbol}</span> ${idea.strategy}</div>
          <div class="trade-strikes">${idea.source}</div>
        </div>
        <div class="trade-body">
          <div class="metric-grid">
            <div class="metric"><div class="metric-label">Rank</div><div class="metric-value positive">${fmt(idea.rank, 1)}</div></div>
            <div class="metric"><div class="metric-label">EV</div><div class="metric-value">${metricValueOrMissing(idea.ev, (v) => fmtSigned(v, 2), metricMissingReason(idea.sourceType, 'ev'))}</div></div>
            <div class="metric"><div class="metric-label">POP</div><div class="metric-value">${metricValueOrMissing(idea.pop, (v) => fmtPct(v, 1), metricMissingReason(idea.sourceType, 'pop'))}</div></div>
            <div class="metric"><div class="metric-label">RoR</div><div class="metric-value">${metricValueOrMissing(idea.ror, (v) => fmtPct(v, 1), metricMissingReason(idea.sourceType, 'ror'))}</div></div>
            <div class="metric"><div class="metric-label">Model</div><div class="metric-value">${idea.model?.status === 'running' ? 'Running...' : (idea.model?.status === 'available' ? `${idea.model.recommendation}${idea.model.confidence !== null ? ` (${(idea.model.confidence * 100).toFixed(0)}%)` : ''}` : 'Not run')}</div></div>
          </div>
          <div class="stock-note">MODEL: ${escapeHtml(formatModelSummary(idea.model || { status: 'not_run' }))}</div>
          ${idea.sourceType === 'stock' ? `<div class="stock-note">Price ${metricValueOrMissing(idea.key_metrics?.price, (v) => fmt(v, 2), 'Price unavailable')} • RSI ${metricValueOrMissing(idea.key_metrics?.rsi14, (v) => fmt(v, 1), 'RSI14 unavailable')} • Trend ${idea.key_metrics?.trend || '—'} • IV/RV ${metricValueOrMissing(idea.key_metrics?.iv_rv_ratio, (v) => fmt(v, 2), 'IV/RV unavailable')} ${idea.key_metrics?.iv_rv_flag ? `(${idea.key_metrics.iv_rv_flag})` : ''}</div>` : ''}
          <div class="home-op-actions">
            <button type="button" class="btn qtButton" data-action="execute" data-idx="${idx}" ${idea.sourceType === 'stock' ? 'disabled title="Execution supported for options trades only (for now)"' : ''}>Execute trade</button>
            <button type="button" class="btn qtButton" data-action="workbench" data-idx="${idx}">Send to workbench</button>
            <button type="button" class="btn qtButton" data-action="analysis" data-idx="${idx}">Open analysis</button>
            <button type="button" class="btn qtButton" data-action="run-model" data-idx="${idx}" ${idea.sourceType === 'stock' ? 'disabled title="Model analysis currently supports options trades only"' : ''}>${idea.model?.status === 'running' ? 'Running model...' : 'Run model analysis'}</button>
          </div>
        </div>
      </div>
    `).join('');

    opportunitiesEl.querySelectorAll('button[data-action]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const action = String(btn.getAttribute('data-action') || '');
        const idx = Number(btn.getAttribute('data-idx'));
        const idea = top[idx];
        if(!idea) return;

        if(action === 'analysis'){
          openAnalysisForOpportunity(idea);
          return;
        }

        if(action === 'run-model'){
          if(idea.sourceType === 'stock'){
            return;
          }
          const opKey = String(idea?._opKey || opportunityKey(idea, idx));
          runModelForOpportunity(idea, (modelState) => {
            opportunityModelState.set(opKey, modelState);
            renderOpportunities(latestOpportunities);
          }, 'home_opportunities').catch(() => {});
          return;
        }

        if(action === 'execute'){
          openExecuteForOpportunity(idea);
          return;
        }

        sendToWorkbenchForOpportunity(idea, '#/trade-testing');
      });
    });
  }

  function renderStrategyBoard(sessionState){
    const byModule = sessionState?.by_module || {};
    const rows = [
      ['Credit Put', byModule.credit_put],
      ['Credit Call', byModule.credit_call],
      ['Debit Spreads', byModule.debit_spreads],
      ['Iron Condor', byModule.iron_condor],
      ['Butterflies', byModule.butterflies],
      ['Calendar', byModule.calendar],
      ['Income', byModule.income],
      ['Stock Scanner', byModule.stock_scanner],
    ];

    strategyRowsEl.innerHTML = rows.map(([label, row]) => `
      <tr>
        <td>${label}</td>
        <td>${toNumber(row?.avg_quality_score) === null ? 'N/A' : fmtPct(row?.avg_quality_score, 1)}</td>
        <td>${toNumber(row?.avg_return_on_risk) === null ? 'N/A' : fmtPct(row?.avg_return_on_risk, 1)}</td>
        <td>${Number(row?.accepted_trades || 0)}</td>
      </tr>
    `).join('');

    const mini = rows.map(([label, row]) => {
      const score = toNumber(row?.avg_quality_score) ?? 0;
      return { label, score };
    });
    strategyMiniEl.innerHTML = mini.map((row) => {
      const width = Math.max(2, Math.round(Math.min(Math.max(row.score, 0), 1) * 100));
      return `<div class="home-mini-row"><span>${row.label}</span><div class="home-mini-track"><div class="home-mini-fill" style="width:${width}%;"></div></div></div>`;
    }).join('');
  }

  function renderTopPick(payload){
    const picks = Array.isArray(payload?.picks) ? payload.picks : [];
    const first = picks.length ? normalizeOpportunity(picks[0], picks[0]?.type) : null;
    const topNotes = Array.isArray(payload?.notes) ? payload.notes : [];
    const showFallbackLabel = topNotes.some((note) => String(note || '').toLowerCase().includes('fallback pick (recommendations offline)'));

    if(!first){
      topPickEl.innerHTML = '<div class="loading">Run a scan to generate picks.</div>';
      return;
    }

    const topKey = opportunityKey(first, 0);
    if(topPickModelState.key === topKey && topPickModelState.model){
      first.model = {
        status: String(topPickModelState.model.status || first.model?.status || 'not_run'),
        recommendation: String(topPickModelState.model.recommendation || first.model?.recommendation || 'UNKNOWN').toUpperCase(),
        confidence: toNumber(topPickModelState.model.confidence),
        summary: String(topPickModelState.model.summary || '').trim(),
      };
    }

    const metrics = first?.key_metrics || {};
    const why = Array.isArray(first?.why) ? first.why.slice(0, 3) : [];
    const actions = first?.actions || payload?.picks?.[0]?.actions || {};
    const route = String(first?.route || actions?.open_route || '#/stock-analysis');
    const workbenchPayload = actions?.send_to_workbench_payload || null;

    topPickEl.innerHTML = `
      <div class="home-top-pick-header">
        <div class="home-top-pick-title"><span class="qtPill">${String(first.symbol || 'N/A')}</span> ${String(first.strategy || 'idea')}</div>
        <div class="home-top-pick-score">Score ${fmt(first.rank, 1)}</div>
      </div>
      ${showFallbackLabel ? '<div class="stock-note">Fallback pick (recommendations offline)</div>' : ''}
      <div class="home-top-pick-why">
        ${why.length ? why.map((item) => `<div class="stock-note">• ${String(item)}</div>`).join('') : '<div class="stock-note">• No rationale available.</div>'}
      </div>
      <div class="home-top-pick-metrics">
        <span class="qtPill" data-metric="pop">POP ${metricValueOrMissing(first.pop, (v) => fmtPct(v, 1), metricMissingReason(first.sourceType, 'pop'))}</span>
        <span class="qtPill" data-metric="ev_to_risk">EV ${metricValueOrMissing(first.ev, (v) => fmtSigned(v, 2), metricMissingReason(first.sourceType, 'ev'))}</span>
        <span class="qtPill" data-metric="return_on_risk">RoR ${metricValueOrMissing(first.ror, (v) => fmtPct(v, 1), metricMissingReason(first.sourceType, 'ror'))}</span>
        <span class="qtPill" data-metric="iv_rv_ratio">IV/RV ${metricValueOrMissing(metrics?.iv_rv_ratio, (v) => fmt(v, 2), 'IV/RV unavailable')}</span>
      </div>
      <div class="stock-note">Model: ${formatModelSummary(first.model || { status: 'not_run' })}</div>
      <div class="home-top-pick-actions">
        <button type="button" class="btn qtButton" data-action="execute" ${first.sourceType === 'stock' ? 'disabled title="Execution supported for options trades only (for now)"' : ''}>Execute Trade</button>
        <button type="button" class="btn qtButton" data-action="open">Open Analysis</button>
        <button type="button" class="btn qtButton" data-action="workbench">Send to Workbench</button>
        <button type="button" class="btn qtButton" data-action="model" ${first.sourceType === 'stock' ? 'disabled title="Model analysis currently supports options trades only"' : ''}>Run Model</button>
      </div>
    `;

    topPickEl.querySelector('[data-action="execute"]')?.addEventListener('click', () => {
      openExecuteForOpportunity(first);
    });

    topPickEl.querySelector('[data-action="open"]')?.addEventListener('click', () => {
      openAnalysisForOpportunity(first);
    });

    topPickEl.querySelector('[data-action="workbench"]')?.addEventListener('click', () => {
      if(workbenchPayload){
        localStorage.setItem('bentrade_workbench_handoff_v1', JSON.stringify(workbenchPayload));
      } else {
        sendToWorkbenchForOpportunity(first);
        return;
      }
      location.hash = '#/trade-testing';
    });

    topPickEl.querySelector('[data-action="model"]')?.addEventListener('click', () => {
      if(first.sourceType === 'stock'){
        return;
      }
      runModelForOpportunity(first, (modelState) => {
        topPickModelState.key = topKey;
        topPickModelState.model = modelState;
        renderTopPick(payload);
      }, 'home_top_pick').catch(() => {});
    });
  }

  function renderSignalHub(universePayload){
    const items = Array.isArray(universePayload?.items) ? universePayload.items : [];
    if(!items.length){
      signalHubEl.innerHTML = '<div class="stock-note">Signal Hub unavailable.</div>';
      return;
    }

    const bySymbol = new Map(items.map((row) => [String(row?.symbol || '').toUpperCase(), row]));
    const sectorSymbols = ['XLK', 'XLF', 'XLE', 'XLV', 'XLY', 'XLP', 'XLI', 'XLB', 'XLRE', 'XLU', 'XLC'];
    const sectorRows = sectorSymbols
      .map((symbol) => bySymbol.get(symbol))
      .filter(Boolean)
      .sort((a, b) => Number((b?.composite || {}).score || 0) - Number((a?.composite || {}).score || 0))
      .slice(0, 4);

    const targetRows = [bySymbol.get('SPY'), ...sectorRows].filter(Boolean);
    signalHubEl.innerHTML = targetRows.map((row) => {
      const symbol = String(row?.symbol || 'N/A').toUpperCase();
      const score = Number((row?.composite || {}).score || 0);
      const label = String((row?.composite || {}).label || 'Neutral');
      const positives = (Array.isArray(row?.signals) ? row.signals : []).filter((item) => item?.value).slice(0, 4);
      return `
        <div class="home-signal-row">
          <div class="home-signal-head"><span class="qtPill">${symbol}</span> <span class="stock-note">${label} ${score.toFixed(1)}</span></div>
          <div class="home-signal-chips">
            ${positives.length ? positives.map((item) => `<span class="qtPill" data-metric="${String(item.id || '')}">${String(item.id || '').replaceAll('_', ' ')}</span>`).join('') : '<span class="stock-note">No active signals</span>'}
          </div>
        </div>
      `;
    }).join('');
  }

  function renderRisk(snapshot, activeTradesPayload){
    const portfolio = snapshot?.portfolio || {};
    const risk = toNumber(portfolio?.risk);

    let capitalAtRisk = risk;
    let utilization = null;

    const activeTrades = Array.isArray(activeTradesPayload?.active_trades) ? activeTradesPayload.active_trades : [];
    if(capitalAtRisk === null){
      capitalAtRisk = activeTrades.reduce((sum, row) => {
        const candidate = toNumber(row?.risk_amount ?? row?.max_loss ?? row?.estimated_risk);
        return sum + (candidate || 0);
      }, 0);
    }
    if(capitalAtRisk !== null){
      const denom = 100000;
      utilization = denom > 0 ? capitalAtRisk / denom : null;
    }

    riskTilesEl.innerHTML = `
      <div class="statTile"><div class="statLabel">Net Delta</div><div class="statValue">${fmt(portfolio?.delta, 3)}</div></div>
      <div class="statTile"><div class="statLabel">Net Theta</div><div class="statValue">${fmt(portfolio?.theta, 3)}</div></div>
      <div class="statTile"><div class="statLabel">Net Vega</div><div class="statValue">${fmt(portfolio?.vega, 3)}</div></div>
      <div class="statTile"><div class="statLabel">Capital at Risk</div><div class="statValue">$${fmt(capitalAtRisk, 0)}</div></div>
      <div class="statTile"><div class="statLabel">Risk Utilization</div><div class="statValue">${utilization === null ? '0.00%' : `${(utilization * 100).toFixed(2)}%`}</div></div>
    `;
  }

  function renderMacro(macro, spySummary){
    const vix = toNumber(macro?.vix ?? spySummary?.options_context?.vix);
    macroTilesEl.innerHTML = `
      <div class="statTile"><div class="statLabel">10Y Yield</div><div class="statValue">${fmt(macro?.ten_year_yield, 2)}%</div></div>
      <div class="statTile"><div class="statLabel">Fed Funds</div><div class="statValue">${fmt(macro?.fed_funds_rate, 2)}%</div></div>
      <div class="statTile"><div class="statLabel">CPI YoY</div><div class="statValue">${fmt(macro?.cpi_yoy, 2)}%</div></div>
      <div class="statTile"><div class="statLabel">VIX</div><div class="statValue">${fmt(vix, 2)}</div></div>
    `;
  }

  function renderStrategyPlaybook(payload){
    const regime = payload?.regime || {};
    const playbook = payload?.playbook || {};
    const regimeLabel = String(regime?.label || 'NEUTRAL').replaceAll('_', '-');
    const regimeScore = toNumber(regime?.score) ?? 50;

    const laneConfigs = [
      { key: 'primary', label: 'Primary', pillClass: 'qtPill' },
      { key: 'secondary', label: 'Secondary', pillClass: 'qtPill' },
      { key: 'avoid', label: 'Avoid', pillClass: 'qtPill qtPill-warn' },
    ];

    const laneHtml = laneConfigs.map((lane) => {
      const rows = Array.isArray(playbook?.[lane.key]) ? playbook[lane.key] : [];
      const list = rows.length ? rows.map((row) => {
        const strategy = String(row?.strategy || '').trim();
        const label = String(row?.label || strategy || 'N/A');
        const confidence = Math.max(0, Math.min(1, Number(row?.confidence || 0)));
        const width = Math.max(4, Math.round(confidence * 100));
        const why = Array.isArray(row?.why) ? row.why.slice(0, 3) : [];
        const route = PLAYBOOK_ROUTES[strategy] || '#/credit-spread';
        return `
          <div class="home-playbook-item">
            <div class="home-playbook-head">
              <button type="button" class="${lane.pillClass} home-playbook-link" data-route="${route}">${label}</button>
              <span class="stock-note">${(confidence * 100).toFixed(0)}%</span>
            </div>
            <div class="home-playbook-track"><div class="home-playbook-fill ${lane.key}" style="width:${width}%;"></div></div>
            <ul class="home-playbook-why home-playbook-why-list">${why.length ? why.map((item) => `<li>${String(item)}</li>`).join('') : '<li>No rationale available.</li>'}</ul>
          </div>
        `;
      }).join('') : '<div class="stock-note">No strategy recommendations.</div>';

      return `
        <div class="home-playbook-lane ${lane.key}">
          <div class="home-playbook-lane-title">${lane.label}</div>
          ${list}
        </div>
      `;
    }).join('');

    const notes = Array.isArray(playbook?.notes) ? playbook.notes.slice(0, 2) : [];
    strategyPlaybookEl.innerHTML = `
      <div class="home-playbook-summary">
        <span class="qtPill">Regime ${regimeLabel}</span>
        <span class="stock-note">Score ${fmt(regimeScore, 1)}/100</span>
      </div>
      <div class="home-playbook-grid">${laneHtml}</div>
      <div class="home-playbook-notes">
        ${notes.length ? notes.map((note) => `<div class="stock-note">• ${String(note)}</div>`).join('') : '<div class="stock-note">• No playbook notes.</div>'}
      </div>
    `;

    strategyPlaybookEl.querySelectorAll('button.home-playbook-link[data-route]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const route = String(btn.getAttribute('data-route') || '#/credit-spread');
        location.hash = route.startsWith('#/') ? route : '#/credit-spread';
      });
    });
  }

  function renderPlaybookFallback(message){
    strategyPlaybookEl.innerHTML = `
      <div class="home-playbook-fallback">
        <div class="stock-note">${String(message || 'Playbook unavailable')}</div>
        <button class="btn qtButton" type="button" data-action="retry-playbook">Retry</button>
      </div>
    `;
  }

  function emptySummary(symbol){
    return {
      symbol,
      price: { last: 0, change_pct: 0 },
      indicators: { rsi14: 0, ema20: 0 },
      history: [],
      options_context: { vix: 0 },
      source_health: {},
    };
  }

  function updateLastUpdated(iso){
    const parsed = iso ? new Date(iso) : null;
    const text = parsed && !Number.isNaN(parsed.getTime()) ? parsed.toLocaleTimeString() : '--';
    lastUpdatedEl.textContent = `Last updated: ${text}`;
  }

  function buildTopPickFallback(topPickPayload, ideas){
    const payload = (topPickPayload && typeof topPickPayload === 'object') ? topPickPayload : { picks: [] };
    const picks = Array.isArray(payload?.picks) ? payload.picks : [];
    const endpointError = payload?.error && typeof payload.error === 'object' ? payload.error : null;

    if(picks.length && !endpointError){
      return payload;
    }

    const sourceIdeas = Array.isArray(ideas) ? ideas.slice() : [];
    if(!sourceIdeas.length){
      return payload;
    }

    const normalizedIdeas = sourceIdeas
      .map((row) => normalizeOpportunity(row, row?.sourceType));

    const optionsFirst = normalizedIdeas
      .filter((row) => row?.sourceType === 'options')
      .sort((a, b) => (toNumber(b?.rank) ?? 0) - (toNumber(a?.rank) ?? 0));
    const equities = normalizedIdeas
      .filter((row) => row?.sourceType !== 'options')
      .sort((a, b) => (toNumber(b?.rank) ?? 0) - (toNumber(a?.rank) ?? 0));

    const sorted = optionsFirst.length ? [...optionsFirst, ...equities] : equities;

    const first = sorted[0];
    if(!first){
      return payload;
    }

    const pick = {
      symbol: first.symbol,
      strategy: first.strategy,
      rank_score: first.rank,
      type: first.sourceType,
      source: first.source,
      source_feed: first.source_feed,
      route: first.route,
      why: [
        `Derived from latest ${first.sourceType === 'stock' ? 'stock scanner' : 'strategy report'} candidates`,
      ],
      key_metrics: first.key_metrics,
      trade: first.trade,
      actions: first.actions,
    };

    const baseNotes = Array.isArray(payload?.notes) ? payload.notes.slice() : [];
    if(!baseNotes.some((note) => String(note || '').toLowerCase().includes('fallback pick (recommendations offline)'))){
      baseNotes.unshift('Fallback pick (recommendations offline)');
    }

    return {
      ...payload,
      picks: [pick],
      notes: baseNotes,
    };
  }

  function renderSnapshot(snapshot){
    const payload = (snapshot && typeof snapshot === 'object') ? snapshot : {};
    const data = (payload.data && typeof payload.data === 'object') ? payload.data : {};
    const meta = (payload.meta && typeof payload.meta === 'object') ? payload.meta : {};

    const sessionState = data.sessionStats || window.BenTradeSessionStatsStore?.getState?.() || {
      total_candidates: 0,
      accepted_trades: 0,
      by_module: {},
    };

    const regimePayload = data.regime || {};
    const spySummary = data.spy || emptySummary('SPY');
    const vixSummary = data.vix || emptySummary('VIXY');
    const macro = data.macro || {};
    const topPickPayloadRaw = data.topPicks || { picks: [] };
    const signalUniversePayload = data.signalsUniverse || { items: [] };
    const playbookPayload = data.playbook || null;
    const riskSnapshot = data.portfolioRisk || { portfolio: {} };
    const activeTradesPayload = data.activeTrades || { active_trades: [] };
    const ideas = Array.isArray(data.opportunities) ? data.opportunities : [];
    const indexSummaries = data.indexSummaries || Object.fromEntries(INDEX_SYMBOLS.map((symbol) => [symbol, emptySummary(symbol)]));
    const sectorSummaries = data.sectors || {};
    const sourceHealth = data.sourceHealth || regimePayload?.source_health || spySummary?.source_health || riskSnapshot?.source_health || {};

    renderRegime(regimePayload, spySummary, macro);
    renderIndexes(indexSummaries);
    renderSectors(sectorSummaries);
    renderOpportunities(ideas);
    const topPickPayload = buildTopPickFallback(topPickPayloadRaw, ideas);
    if(isDevInstrumentationEnabled() && !devLoggedTopPickSource){
      const topPickSource = Array.isArray(topPickPayload?.picks) && topPickPayload.picks[0]
        ? String(topPickPayload.picks[0]?.source_feed || 'recommendations/top')
        : 'none';
      console.debug('[HomeMetrics] top_pick_source', { source: topPickSource });
      devLoggedTopPickSource = true;
    }
    renderTopPick(topPickPayload);
    renderSignalHub(signalUniversePayload);
    if(playbookPayload){
      renderStrategyPlaybook(playbookPayload);
    } else {
      renderPlaybookFallback('Playbook unavailable');
    }
    renderStrategyBoard(sessionState);
    renderRisk(riskSnapshot, activeTradesPayload);
    renderMacro(macro, spySummary);
    renderSourceHealth(sourceHealth);

    renderChart(spyChartEl, spySummary?.history || [], { stroke: 'rgba(0,234,255,0.95)' });
    renderChart(vixChartEl, vixSummary?.history || [], { stroke: 'rgba(255,199,88,0.95)' });

    updateLastUpdated(meta.last_success_at);
    if(Array.isArray(meta.errors) && meta.errors.length){
      setError('Using cached data while refreshing.');
    } else {
      setError('');
    }

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(scope);
    }
  }

  function renderFallbackBlank(){
    const snapshot = {
      data: {
        regime: {},
        spy: emptySummary('SPY'),
        vix: emptySummary('VIXY'),
        macro: {},
        topPicks: { picks: [] },
        signalsUniverse: { items: [] },
        playbook: null,
        portfolioRisk: { portfolio: {} },
        activeTrades: { active_trades: [] },
        opportunities: [],
        indexSummaries: Object.fromEntries(INDEX_SYMBOLS.map((symbol) => [symbol, emptySummary(symbol)])),
        sectors: {},
        sessionStats: window.BenTradeSessionStatsStore?.getState?.() || { total_candidates: 0, accepted_trades: 0, by_module: {} },
        sourceHealth: {},
      },
      meta: { last_success_at: null, errors: [], partial: false },
    };
    renderSnapshot(snapshot);
  }

  function bindRetry(){
    strategyPlaybookEl.querySelector('[data-action="retry-playbook"]')?.addEventListener('click', () => {
      runLoadSequence({ force: true, showOverlay: true }).catch(() => {});
    });
  }

  const LOG_HISTORY_LIMIT = 500;
  const logHistory = [];

  function stampLog(text){
    const ts = new Date().toLocaleTimeString();
    return `[${ts}] ${String(text || '')}`;
  }

  function pushLog(text){
    logHistory.push(stampLog(text));
    if(logHistory.length > LOG_HISTORY_LIMIT){
      logHistory.splice(0, logHistory.length - LOG_HISTORY_LIMIT);
    }
    if(overlay?.isOpen?.()){
      overlay.setLines(logHistory);
    }
  }

  function setRefreshingBadge(isVisible){
    refreshingBadgeEl.style.display = isVisible ? 'inline-flex' : 'none';
  }

  const QUEUE_LOG_LIMIT = 8;
  const queueLogLines = [];
  const queueState = {
    isRunning: false,
    stopRequested: false,
    runId: 0,
  };
  const fullAppRefreshState = {
    isRunning: false,
    stopRequested: false,
    runId: 0,
  };

  function renderQueueLog(){
    if(!queueLogLines.length){
      queueLogEl.style.display = 'none';
      queueLogEl.innerHTML = '';
      return;
    }
    queueLogEl.style.display = 'grid';
    queueLogEl.innerHTML = queueLogLines
      .map((entry) => `<div class="home-queue-log-line ${entry.kind === 'fail' ? 'fail' : ''}">${entry.text}</div>`)
      .join('');
  }

  function appendQueueLog(text, kind = 'info'){
    queueLogLines.push({ text: String(text || ''), kind: String(kind || 'info') });
    if(queueLogLines.length > QUEUE_LOG_LIMIT){
      queueLogLines.splice(0, queueLogLines.length - QUEUE_LOG_LIMIT);
    }
    renderQueueLog();
  }

  function setQueueProgress({ current, completed, total, running }){
    queueProgressEl.style.display = 'flex';
    queueCurrentEl.textContent = String(current || 'Idle');
    queueCountEl.textContent = `${Number(completed || 0)}/${Number(total || 0)}`;
    queueSpinnerEl.style.display = running ? 'inline-block' : 'none';
  }

  function resetQueueProgress(){
    queueProgressEl.style.display = 'none';
    queueCurrentEl.textContent = 'Idle';
    queueCountEl.textContent = '0/0';
    queueSpinnerEl.style.display = 'none';
    queueLogLines.splice(0, queueLogLines.length);
    renderQueueLog();
  }

  function withTimeout(promise, timeoutMs, label){
    const ms = Math.max(1000, Number(timeoutMs || 0));
    if(!ms) return promise;
    return Promise.race([
      promise,
      new Promise((_, reject) => {
        window.setTimeout(() => {
          const err = new Error(`${String(label || 'step')} timeout`);
          err.code = 'timeout';
          reject(err);
        }, ms);
      }),
    ]);
  }

  function createStrategyStep({ id, label, strategyId, endpoint, moduleId, timeoutMs, optional = false, payload = {} }){
    return {
      id,
      label,
      endpoint,
      moduleId,
      timeoutMs,
      optional,
      fn: () => api.generateStrategyReport(strategyId, payload),
    };
  }

  function isNotImplementedError(err){
    const status = Number(err?.status || err?.statusCode);
    if(status === 404 || status === 405 || status === 501) return true;
    const detail = String(err?.detail || err?.message || '').toLowerCase();
    return detail.includes('not implemented') || detail.includes('not found');
  }

  function shouldRecordModuleRun(response){
    return Array.isArray(response?.candidates)
      || Array.isArray(response?.trades)
      || (response?.report_stats && typeof response.report_stats === 'object');
  }

  function readErrorMessageFromPayload(payload){
    const p = (payload && typeof payload === 'object') ? payload : {};
    return String(
      p?.error?.message
      || p?.detail
      || p?.message
      || p?.error
      || ''
    ).trim();
  }

  function describeRefreshError(err, step){
    const status = String(err?.status || err?.statusCode || err?.code || 'n/a');
    const endpoint = String(err?.endpoint || step?.endpoint || 'n/a');
    const payload = err?.payload && typeof err.payload === 'object' ? err.payload : null;
    const payloadMessage = readErrorMessageFromPayload(payload);
    const payloadDetail = String(payload?.error?.details?.message || payload?.error?.details?.detail || payload?.error?.details || '').trim();
    const detail = payloadMessage || payloadDetail || String(err?.detail || err?.message || '').trim() || 'n/a';
    const bodySnippet = String(err?.bodySnippet || '').trim();
    return {
      status,
      endpoint,
      detail,
      bodySnippet: bodySnippet ? bodySnippet.slice(0, 200) : '',
    };
  }

  function updateHomeSessionSnapshot(){
    try{
      const snap = cacheStore?.getSnapshot?.();
      if(!snap || typeof snap !== 'object') return;
      const data = (snap.data && typeof snap.data === 'object') ? { ...snap.data } : {};
      data.sessionStats = window.BenTradeSessionStatsStore?.getState?.() || data.sessionStats || { total_candidates: 0, accepted_trades: 0, by_module: {} };
      cacheStore.setSnapshot({ ...snap, data });
    }catch(_err){
    }
  }

  function buildFullAppRefreshSteps(){
    const steps = [];

    steps.push({
      id: 'broker_positions',
      label: 'Broker sync: Positions',
      provider: 'tradier',
      endpoint: '/api/trading/positions',
      timeoutMs: 20000,
      critical: false,
      optional: true,
      fn: () => api.getTradingPositions(),
      afterSuccess: async () => {
        await api.refreshActiveTrades().catch(() => {});
      },
    });

    steps.push({
      id: 'broker_orders',
      label: 'Broker sync: Open orders',
      provider: 'tradier',
      endpoint: '/api/trading/orders/open',
      timeoutMs: 20000,
      critical: false,
      optional: true,
      fn: () => api.getTradingOpenOrders(),
    });

    steps.push({
      id: 'broker_account',
      label: 'Broker sync: Account',
      provider: 'tradier',
      endpoint: '/api/trading/account',
      timeoutMs: 20000,
      critical: false,
      optional: true,
      fn: () => api.getTradingAccount(),
    });

    steps.push({
      id: 'stock_scanner',
      label: 'Stock Scanner',
      provider: 'internal',
      endpoint: '/api/stock/scanner',
      timeoutMs: 25000,
      critical: true,
      optional: false,
      moduleId: 'stock_scanner',
      fn: () => api.getStockScanner(),
      afterSuccess: async (response) => {
        if(window.BenTradeSessionStatsStore?.recordRun && shouldRecordModuleRun(response)){
          window.BenTradeSessionStatsStore.recordRun('stock_scanner', response);
          updateHomeSessionSnapshot();
        }
        await runLoadSequence({ force: true, showOverlay: false }).catch(() => {});
      },
    });

    const strategySteps = [
      { id: 'credit_put_generate', label: 'Credit Put Spread report generation', strategyId: 'credit_spread', moduleId: 'credit_put', payload: { spread_type: 'credit_put_spread' }, critical: true, optional: false },
      { id: 'credit_call_generate', label: 'Credit Call Spread report generation', strategyId: 'credit_spread', moduleId: 'credit_call', payload: { spread_type: 'credit_call_spread' }, critical: true, optional: false },
      { id: 'iron_condor_generate', label: 'Iron Condor report generation', strategyId: 'iron_condor', moduleId: 'iron_condor', payload: {}, critical: false, optional: true },
      { id: 'debit_spreads_generate', label: 'Debit Spreads report generation', strategyId: 'debit_spreads', moduleId: 'debit_spreads', payload: {}, critical: false, optional: true },
      { id: 'calendar_generate', label: 'Calendar report generation', strategyId: 'calendars', moduleId: 'calendar', payload: {}, critical: false, optional: true },
      { id: 'butterflies_generate', label: 'Butterflies report generation', strategyId: 'butterflies', moduleId: 'butterflies', payload: {}, critical: false, optional: true },
      { id: 'income_generate', label: 'Income report generation', strategyId: 'income', moduleId: 'income', payload: {}, critical: false, optional: true },
    ];

    strategySteps.forEach((row) => {
      steps.push({
        id: row.id,
        label: row.label,
        provider: 'internal',
        endpoint: `/api/strategies/${row.strategyId}/generate`,
        timeoutMs: 45000,
        critical: !!row.critical,
        optional: !!row.optional,
        moduleId: row.moduleId,
        fn: () => api.generateStrategyReport(row.strategyId, row.payload || {}),
        afterSuccess: async (response) => {
          if(window.BenTradeSessionStatsStore?.recordRun && row.moduleId && shouldRecordModuleRun(response)){
            window.BenTradeSessionStatsStore.recordRun(row.moduleId, response);
            updateHomeSessionSnapshot();
          }
          await runLoadSequence({ force: true, showOverlay: false }).catch(() => {});
        },
      });
    });

    steps.push({
      id: 'regime_refresh',
      label: 'Regime refresh',
      provider: 'internal',
      endpoint: '/api/regime',
      timeoutMs: 15000,
      critical: false,
      optional: false,
      fn: () => api.getRegime(),
      afterSuccess: async () => {
        await runLoadSequence({ force: true, showOverlay: false }).catch(() => {});
      },
    });

    steps.push({
      id: 'signals_refresh',
      label: 'Signals refresh',
      provider: 'internal',
      endpoint: '/api/signals',
      timeoutMs: 15000,
      critical: false,
      optional: true,
      fn: () => api.getSignals('SPY', '6mo'),
    });

    steps.push({
      id: 'top_picks_refresh',
      label: 'Top picks refresh',
      provider: 'internal',
      endpoint: '/api/recommendations/top',
      timeoutMs: 15000,
      critical: false,
      optional: true,
      fn: () => api.getTopRecommendations(),
      afterSuccess: async () => {
        await runLoadSequence({ force: true, showOverlay: false }).catch(() => {});
      },
    });

    steps.push({
      id: 'source_health_refresh',
      label: 'Source health refresh',
      provider: 'internal',
      endpoint: '/api/health/sources',
      timeoutMs: 12000,
      critical: false,
      optional: false,
      fn: () => window.BenTradeSourceHealthStore?.fetchSourceHealth?.({ force: true }) || Promise.resolve({}),
      afterSuccess: async () => {
        await runLoadSequence({ force: true, showOverlay: false }).catch(() => {});
      },
    });

    return steps;
  }

  async function runFullAppRefresh(){
    if(fullAppRefreshState.isRunning) return;
    const limiter = window.BenTradeRateLimiter?.create?.({
      minDelayMs: 750,
      maxRetries: 3,
      backoffBaseMs: 2000,
      backoffCapMs: 30000,
    });
    if(!limiter){
      setScanError('Rate limiter unavailable');
      return;
    }

    const steps = buildFullAppRefreshSteps();
    const total = steps.length;
    const runId = ++fullAppRefreshState.runId;
    let completed = 0;
    let warnings = 0;
    let fatalFailure = null;

    fullAppRefreshState.isRunning = true;
    fullAppRefreshState.stopRequested = false;
    fullRefreshBtnEl.disabled = true;
    setScanError('');
    setScanStatus('');
    pushLog('Full App Refresh started');

    if(overlay){
      overlay.open({
        status: `Full App Refresh • 0/${total}`,
        logs: logHistory,
        cancelLabel: 'Stop',
        showRetry: false,
        onCancel: () => {
          fullAppRefreshState.stopRequested = true;
          overlay.setStatus('Stopping Full App Refresh...');
          pushLog('Full App Refresh stop requested');
        },
        onRetry: null,
      });
    }

    try{
      for(let i = 0; i < steps.length; i += 1){
        const step = steps[i];
        if(runId !== fullAppRefreshState.runId || fullAppRefreshState.stopRequested){
          break;
        }

        const stepIndex = i + 1;
        const startText = `Starting: ${step.label}`;
        pushLog(startText);
        if(overlay?.isOpen?.()){
          overlay.setStatus(`Full App Refresh • ${completed}/${total} • ${step.label}`);
        }

        try{
          const response = await limiter.runStep({
            provider: step.provider,
            label: step.label,
            fn: () => withTimeout(Promise.resolve(step.fn()), step.timeoutMs, step.label),
          });

          if(runId !== fullAppRefreshState.runId || fullAppRefreshState.stopRequested){
            break;
          }

          const value = response?.value;
          if(value && typeof value === 'object' && value.ok === false){
            const detail = readErrorMessageFromPayload(value) || 'n/a';
            const nonFatalText = `Broker sync failed (non-fatal): ${step.label} endpoint=${step.endpoint || 'n/a'} detail=${detail}`;
            pushLog(nonFatalText);
            warnings += 1;
            continue;
          }

          pushLog(`Success: ${step.label}`);
          completed += 1;

          if(typeof step.afterSuccess === 'function'){
            await step.afterSuccess(value);
          }

          if(overlay?.isOpen?.()){
            overlay.setStatus(`Full App Refresh • ${completed}/${total} • Step ${stepIndex} complete`);
          }
        }catch(err){
          if(isNotImplementedError(err) && step.optional){
            warnings += 1;
            pushLog(`Not implemented: ${step.label}`);
            continue;
          }

          const parsed = describeRefreshError(err, step);
          const failLine = `Failed: ${step.label} (${parsed.status}) endpoint=${parsed.endpoint} detail=${parsed.detail}`;
          pushLog(failLine);
          if(parsed.bodySnippet){
            pushLog(`Body: ${parsed.bodySnippet}`);
          }

          if(step.optional){
            if(String(step.id || '').startsWith('broker_')){
              pushLog(`Broker sync failed (non-fatal): ${step.label}`);
            }
            warnings += 1;
            continue;
          }

          if(step.critical){
            fatalFailure = { label: step.label, detail: parsed.detail };
            break;
          }
          warnings += 1;
        }
      }

      if(runId !== fullAppRefreshState.runId){
        return;
      }

      if(fullAppRefreshState.stopRequested){
        setScanStatus('Full App Refresh stopped');
        pushLog('Full App Refresh stopped');
      }else if(fatalFailure){
        setScanError(`Full App Refresh failed at ${fatalFailure.label}: ${fatalFailure.detail}`);
        setScanStatus('Full App Refresh stopped on critical failure');
      }else{
        await runLoadSequence({ force: true, showOverlay: false }).catch(() => {});
        setScanStatus(`Full App Refresh complete${warnings ? ` (${warnings} warnings)` : ''} • ${new Date().toLocaleTimeString()}`);
        pushLog('Full App Refresh complete');
      }
    }finally{
      if(runId === fullAppRefreshState.runId){
        fullAppRefreshState.isRunning = false;
        fullAppRefreshState.stopRequested = false;
        fullRefreshBtnEl.disabled = false;
      }
      if(overlay?.isOpen?.()){
        overlay.setStatus('Full App Refresh finished');
        window.setTimeout(() => {
          if(overlay?.isOpen?.()){
            overlay.close();
          }
        }, 1200);
      }
    }
  }

  function buildScanPresetSteps(preset){
    const mode = String(preset || 'balanced').toLowerCase();
    const stockScanner = {
      id: 'stock_scanner',
      label: 'Stock Scanner',
      endpoint: '/api/stock/scanner',
      moduleId: 'stock_scanner',
      timeoutMs: 15000,
      optional: false,
      fn: () => api.getStockScanner(),
    };
    const regime = {
      id: 'regime_refresh',
      label: 'Regime refresh',
      endpoint: '/api/regime',
      moduleId: null,
      timeoutMs: 12000,
      optional: mode === 'quick',
      fn: () => api.getRegime(),
    };
    const topPicks = {
      id: 'top_picks_refresh',
      label: 'Top picks refresh',
      endpoint: '/api/recommendations/top',
      moduleId: null,
      timeoutMs: 12000,
      optional: mode !== 'quick',
      fn: () => api.getTopRecommendations(),
    };

    if(mode === 'quick'){
      return [stockScanner, regime, topPicks];
    }

    if(mode === 'full_sweep'){
      return [
        stockScanner,
        createStrategyStep({ id: 'credit_put_generate', label: 'Credit Put report generation', strategyId: 'credit_spread', endpoint: '/api/strategies/credit_spread/generate', moduleId: 'credit_put', timeoutMs: 35000, optional: false, payload: { spread_type: 'credit_put_spread' } }),
        createStrategyStep({ id: 'credit_call_generate', label: 'Credit Call report generation', strategyId: 'credit_spread', endpoint: '/api/strategies/credit_spread/generate', moduleId: 'credit_call', timeoutMs: 35000, optional: false, payload: { spread_type: 'credit_call_spread' } }),
        createStrategyStep({ id: 'iron_condor_generate', label: 'Iron Condor report generation', strategyId: 'iron_condor', endpoint: '/api/strategies/iron_condor/generate', moduleId: 'iron_condor', timeoutMs: 40000, optional: true }),
        createStrategyStep({ id: 'debit_spreads_generate', label: 'Debit Spreads report generation', strategyId: 'debit_spreads', endpoint: '/api/strategies/debit_spreads/generate', moduleId: 'debit_spreads', timeoutMs: 40000, optional: true }),
        createStrategyStep({ id: 'calendar_generate', label: 'Calendar report generation', strategyId: 'calendars', endpoint: '/api/strategies/calendars/generate', moduleId: 'calendar', timeoutMs: 40000, optional: true }),
        createStrategyStep({ id: 'butterflies_generate', label: 'Butterflies report generation', strategyId: 'butterflies', endpoint: '/api/strategies/butterflies/generate', moduleId: 'butterflies', timeoutMs: 40000, optional: true }),
        regime,
        topPicks,
      ];
    }

    return [
      stockScanner,
      createStrategyStep({ id: 'credit_spread_generate', label: 'Credit Spread report generation', strategyId: 'credit_spread', endpoint: '/api/strategies/credit_spread/generate', moduleId: 'credit_put', timeoutMs: 35000, optional: false }),
      regime,
      topPicks,
    ];
  }

  async function runQueueStep(step, runId){
    appendQueueLog(`Starting: ${step.label}`);
    pushLog(`Starting: ${step.label}`);
    try{
      const response = await withTimeout(Promise.resolve(step.fn()), step.timeoutMs, step.label);
      if(step.id === 'top_picks_refresh' && response?.error){
        const err = new Error(String(response?.error?.message || 'recommendations returned error'));
        err.status = 200;
        err.detail = String(response?.error?.message || 'recommendations payload includes error');
        err.endpoint = String(step.endpoint || '/api/recommendations/top');
        throw err;
      }
      if(runId !== queueState.runId || queueState.stopRequested){
        return { ok: false, stopped: true };
      }

      appendQueueLog(`Success: ${step.label}`);
      pushLog(`Success: ${step.label}`);

      if(step.moduleId && window.BenTradeSessionStatsStore?.recordRun){
        const hasCandidates = Array.isArray(response?.candidates);
        const hasTrades = Array.isArray(response?.trades);
        const hasReportStats = response?.report_stats && typeof response.report_stats === 'object';
        if(hasCandidates || hasTrades || hasReportStats){
          window.BenTradeSessionStatsStore.recordRun(step.moduleId, response);
        }
      }

      await runLoadSequence({ force: true, showOverlay: false }).catch(() => {});
      return { ok: true };
    }catch(err){
      const code = String(err?.status || err?.statusCode || err?.code || (String(err?.message || '').toLowerCase().includes('timeout') ? 'timeout' : 'n/a'));
      const endpoint = String(err?.endpoint || step?.endpoint || 'n/a');
      const detail = String(err?.detail || err?.message || 'n/a');
      const failureLine = `Failed: ${step.label} (${code}) endpoint=${endpoint} detail=${detail}`;
      appendQueueLog(failureLine, 'fail');
      pushLog(failureLine);
      return { ok: false, error: err, code, endpoint, detail };
    }
  }

  async function runScanQueue(){
    if(queueState.isRunning) return;

    const preset = String(scanPresetEl.value || 'balanced');
    const steps = buildScanPresetSteps(preset);
    const total = steps.length;
    const runId = ++queueState.runId;
    let completed = 0;
    let failed = null;
    let warnings = 0;

    queueState.isRunning = true;
    queueState.stopRequested = false;
    runQueueBtnEl.disabled = true;
    stopQueueBtnEl.disabled = false;
    scanPresetEl.disabled = true;
    setScanError('');
    setScanStatus('');
    queueLogLines.splice(0, queueLogLines.length);
    renderQueueLog();

    appendQueueLog(`Queue preset: ${preset.replaceAll('_', ' ')}`);
    setQueueProgress({ current: 'Starting queue...', completed: 0, total, running: true });

    try{
      for(let i = 0; i < steps.length; i += 1){
        const step = steps[i];
        if(runId !== queueState.runId || queueState.stopRequested){
          break;
        }

        setQueueProgress({ current: step.label, completed, total, running: true });
        const result = await runQueueStep(step, runId);

        if(runId !== queueState.runId || queueState.stopRequested || result?.stopped){
          break;
        }

        if(!result?.ok){
          const isOptionalFailure = !!step.optional;
          if(isOptionalFailure){
            warnings += 1;
            const warnText = `Optional step skipped: ${step.label}`;
            appendQueueLog(warnText);
            pushLog(warnText);
          } else {
            failed = { step, result };
          }
          const stopNow = (preset === 'quick') || !step.optional;
          if(stopNow){
            break;
          }
          continue;
        }

        completed += 1;
        setQueueProgress({ current: step.label, completed, total, running: true });
      }

      if(runId !== queueState.runId){
        return;
      }

      if(queueState.stopRequested){
        setQueueProgress({ current: 'Stopped', completed, total, running: false });
        setScanStatus('Stopped');
        appendQueueLog('Stopped: remaining steps cancelled');
      }else if(failed){
        setQueueProgress({ current: 'Stopped on failure', completed, total, running: false });
        const failMsg = String(failed?.result?.error?.message || failed?.result?.code || 'n/a');
        setScanError(`Queue failed at ${failed.step.label}: ${failMsg}`);
        setScanStatus('Queue stopped');
      }else{
        setQueueProgress({ current: 'Queue complete', completed: total, total, running: false });
        if(warnings > 0){
          setScanStatus(`Queue complete with warnings (${warnings}) • ${new Date().toLocaleTimeString()}`);
        } else {
          setScanStatus(`Queue complete • ${new Date().toLocaleTimeString()}`);
        }
      }

      await runLoadSequence({ force: true, showOverlay: false }).catch(() => {});
    }finally{
      if(runId === queueState.runId){
        queueState.isRunning = false;
        queueState.stopRequested = false;
        runQueueBtnEl.disabled = false;
        stopQueueBtnEl.disabled = true;
        scanPresetEl.disabled = false;
      }
    }
  }

  function stopScanQueue(){
    if(!queueState.isRunning) return;
    queueState.stopRequested = true;
    setScanStatus('Stopping queue...');
    appendQueueLog('Stop requested');
    setQueueProgress({ current: 'Stopping...', completed: Number((queueCountEl.textContent || '0/0').split('/')[0] || 0), total: Number((queueCountEl.textContent || '0/0').split('/')[1] || 0), running: true });
  }

  const cacheStore = window.BenTradeHomeCacheStore;
  let refreshInterval = null;
  let activeLoadToken = 0;
  const overlay = window.BenTradeHomeLoadingOverlay?.create?.(scope) || null;

  if(!cacheStore){
    renderFallbackBlank();
    setError('Home cache store unavailable');
    return;
  }

  cacheStore.setRenderer((snapshot) => {
    renderSnapshot(snapshot || {});
    bindRetry();
  });

  function runLoadSequence({ force = false, showOverlay = false } = {}){
    const loadToken = ++activeLoadToken;

    if(showOverlay && overlay){
      overlay.open({
        status: 'Starting...',
        logs: logHistory,
        onCancel: () => {
          overlay.close();
        },
        onRetry: () => {
          runLoadSequence({ force: true, showOverlay: true }).catch(() => {});
        },
      });
    }

    if(!showOverlay){
      setRefreshingBadge(true);
    }

    pushLog('Starting home data load...');

    const refreshPromise = force
      ? cacheStore.refreshNow({ logFn: pushLog })
      : cacheStore.refreshSilent({ force: false, logFn: pushLog });

    return refreshPromise
      .then((snapshot) => {
        pushLog('Home ready.');
        setError('');
        if(showOverlay && overlay && loadToken === activeLoadToken){
          overlay.setStatus('Home ready.');
          overlay.close();
        }
        return snapshot;
      })
      .catch((err) => {
        const message = String(err?.message || err || 'Refresh failed');
        pushLog(`Error: home n/a ${message}`);
        if(showOverlay && overlay && loadToken === activeLoadToken){
          overlay.setStatus('Load finished with errors');
        }
        setError(message);
        throw err;
      })
      .finally(() => {
        if(!showOverlay){
          setRefreshingBadge(false);
        }
      });
  }

  const hadCached = cacheStore.renderCachedImmediately();
  if(!hadCached){
    renderFallbackBlank();
    runLoadSequence({ force: false, showOverlay: true }).catch(() => {
      bindRetry();
    });
  } else {
    runLoadSequence({ force: false, showOverlay: false }).catch(() => {
      bindRetry();
    });
  }

  refreshInterval = window.setInterval(() => {
    runLoadSequence({ force: false, showOverlay: false }).catch(() => {});
  }, Number(cacheStore.REFRESH_INTERVAL_MS || 90000));

  refreshBtnEl.addEventListener('click', async () => {
    const oldText = refreshBtnEl.textContent;
    refreshBtnEl.disabled = true;
    refreshBtnEl.textContent = 'Refreshing...';
    try{
      await runLoadSequence({ force: true, showOverlay: true });
      setError('');
    }catch(err){
      setError(String(err?.message || err || 'Refresh failed'));
    }finally{
      refreshBtnEl.disabled = false;
      refreshBtnEl.textContent = oldText || 'Refresh';
    }
  });

  fullRefreshBtnEl.addEventListener('click', () => {
    runFullAppRefresh().catch((err) => {
      setScanError(String(err?.message || err || 'Full App Refresh failed'));
      setScanStatus('');
    });
  });

  runQueueBtnEl.addEventListener('click', () => {
    runScanQueue().catch((err) => {
      setScanError(String(err?.message || err || 'Queue failed'));
      setScanStatus('');
    });
  });

  stopQueueBtnEl.addEventListener('click', () => {
    stopScanQueue();
  });

  resetQueueProgress();

  return function cleanupHome(){
    fullAppRefreshState.stopRequested = true;
    fullAppRefreshState.isRunning = false;
    fullAppRefreshState.runId += 1;
    queueState.stopRequested = true;
    queueState.isRunning = false;
    queueState.runId += 1;
    if(refreshInterval){
      window.clearInterval(refreshInterval);
      refreshInterval = null;
    }
    if(overlay){
      overlay.destroy();
    }
    setRefreshingBadge(false);
    cacheStore.setRenderer(null);
  };
};
