window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initHome = function initHome(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const api = window.BenTradeApi;

  /*
   * Do NOT add selectors for Source Health, Session Stats, or Strategy Leaderboard here.
   * Those are GLOBAL-ONLY panels rendered in the global right info bar (index.html / sessionStats.js).
   */
  const regimeStripEl = scope.querySelector('#homeRegimeStrip');
  const regimeComponentsEl = scope.querySelector('#homeRegimeComponents');
  const playbookChipsEl = scope.querySelector('#homePlaybookChips');
  const scanPresetEl = scope.querySelector('#homeScanPreset');
  const runQueueBtnEl = scope.querySelector('#homeRunQueueBtn');
  const stopQueueBtnEl = scope.querySelector('#homeStopQueueBtn');  // may be null (removed from DOM)
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
  const scannerOpportunitiesEl = scope.querySelector('#homeScannerOpportunities');
  const symbolUniverseEl = scope.querySelector('#homeSymbolUniverse');
  const riskTilesEl = scope.querySelector('#homeRiskTiles');
  const macroTilesEl = scope.querySelector('#homeMacroTiles');
  const strategyPlaybookEl = scope.querySelector('#homeStrategyPlaybook');
  const fullRefreshBtnEl = scope.querySelector('#homeFullRefreshBtn');
  const refreshBtnEl = scope.querySelector('#homeRefreshBtn');
  const pauseRefreshBtnEl = scope.querySelector('#homePauseRefreshBtn');
  const refreshingBadgeEl = scope.querySelector('#homeRefreshingBadge');
  const lastUpdatedEl = scope.querySelector('#homeLastUpdated');
  const vixChartEl = scope.querySelector('#homeVixChart');
  const errorEl = scope.querySelector('#homeError');
  const regimeModelBtnEl = scope.querySelector('#homeRegimeModelBtn');
  const regimeModelOutputEl = scope.querySelector('#homeRegimeModelOutput');
  const activeTradesCountEl = scope.querySelector('#homeActiveTradesCount');
  const equityCurveEl = scope.querySelector('#homeEquityCurve');
  const equityCurveEmptyEl = scope.querySelector('#homeEquityCurveEmpty');

  if(!regimeStripEl || !regimeComponentsEl || !playbookChipsEl || !scanPresetEl || !runQueueBtnEl || !queueProgressEl || !queueCurrentEl || !queueCountEl || !queueSpinnerEl || !queueLogEl || !scanStatusEl || !scanErrorEl || !signalHubEl || !indexTilesEl || !spyChartEl || !sectorBarsEl || !scannerOpportunitiesEl || !riskTilesEl || !macroTilesEl || !strategyPlaybookEl || !fullRefreshBtnEl || !refreshBtnEl || !refreshingBadgeEl || !lastUpdatedEl || !vixChartEl || !errorEl){
    return;
  }

  let latestOpportunities = [];
  const opportunityModelState = new Map();
  const devLoggedCards = new Set();

  /* ── OE card state (mirrors scanner shell's _expandState + currentTrades) ── */
  const _oeExpandState = {};
  let _oeTradesForActions = [];   // parallel array to top[] – raw scannerTrade objects
  let _oeTopIdeas = [];           // normalized ideas for action handlers
  const _mapper = window.BenTradeOptionTradeCardModel;

  /* ── Symbol Universe Selector (home scan queue) ── */
  let _homeSymbolSelector = null;
  if(symbolUniverseEl && window.BenTradeSymbolUniverseSelector){
    _homeSymbolSelector = window.BenTradeSymbolUniverseSelector.mount(symbolUniverseEl, {
      showFilter: true,
      onChange: () => {},  // passive — applied on next queue run
    });
  }

  /* ── Market Regime Model Analysis state ── */
  let _latestRegimePayload = null;
  let _latestPlaybookPayload = null;
  let _regimeModelInflight = null;   // Promise | null — guards duplicate clicks

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

  /* ── Market Regime Model Analysis ──────────────────────────────── */

  function _renderRegimeModelOutput(analysis){
    if(!regimeModelOutputEl) return;
    if(!analysis){
      regimeModelOutputEl.style.display = 'none';
      regimeModelOutputEl.innerHTML = '';
      return;
    }

    const sections = [];

    // Executive summary
    if(analysis.executive_summary){
      sections.push(`<div class="regime-model-section"><div class="regime-model-section-title">Executive Summary</div><div class="regime-model-section-body">${_esc(analysis.executive_summary)}</div></div>`);
    }

    // Regime breakdown by component
    if(analysis.regime_breakdown && typeof analysis.regime_breakdown === 'object'){
      const lines = ['trend', 'volatility', 'breadth', 'rates', 'momentum']
        .filter((k) => analysis.regime_breakdown[k])
        .map((k) => `<li><strong>${k.charAt(0).toUpperCase() + k.slice(1)}:</strong> ${_esc(String(analysis.regime_breakdown[k]))}</li>`)
        .join('');
      if(lines){
        sections.push(`<div class="regime-model-section"><div class="regime-model-section-title">Regime Breakdown</div><ul class="regime-model-list">${lines}</ul></div>`);
      }
    }

    // Primary fit
    if(analysis.primary_fit){
      sections.push(`<div class="regime-model-section"><div class="regime-model-section-title">Why Primary Strategies Fit</div><div class="regime-model-section-body">${_esc(analysis.primary_fit)}</div></div>`);
    }

    // Avoid rationale
    if(analysis.avoid_rationale){
      sections.push(`<div class="regime-model-section"><div class="regime-model-section-title">Why Avoid Strategies Are Riskier</div><div class="regime-model-section-body">${_esc(analysis.avoid_rationale)}</div></div>`);
    }

    // Change triggers
    const triggers = Array.isArray(analysis.change_triggers) ? analysis.change_triggers : [];
    if(triggers.length){
      const triggerLines = triggers.map((t) => `<li>${_esc(String(t))}</li>`).join('');
      sections.push(`<div class="regime-model-section"><div class="regime-model-section-title">What Would Change My Mind</div><ul class="regime-model-list">${triggerLines}</ul></div>`);
    }

    // Confidence + caveats
    if(analysis.confidence_caveats){
      const confPct = (analysis.confidence != null) ? ` (${(analysis.confidence * 100).toFixed(0)}%)` : '';
      sections.push(`<div class="regime-model-section"><div class="regime-model-section-title">Confidence &amp; Caveats${confPct}</div><div class="regime-model-section-body">${_esc(analysis.confidence_caveats)}</div></div>`);
    }

    regimeModelOutputEl.innerHTML = `<details class="regime-model-details" open><summary class="regime-model-summary">Model Analysis Output</summary><div class="regime-model-body">${sections.join('')}</div></details>`;
    regimeModelOutputEl.style.display = 'block';
  }

  function _esc(text){
    const el = document.createElement('span');
    el.textContent = String(text || '');
    return el.innerHTML;
  }

  async function runRegimeModelAnalysis(){
    if(_regimeModelInflight){
      return; // ignore duplicate clicks while in-flight
    }
    if(!_latestRegimePayload){
      _renderRegimeModelError('No regime data available. Load the dashboard first.');
      return;
    }

    // Show loading state
    if(regimeModelBtnEl){
      regimeModelBtnEl.disabled = true;
      regimeModelBtnEl.textContent = 'Analyzing…';
    }
    if(regimeModelOutputEl){
      regimeModelOutputEl.style.display = 'block';
      regimeModelOutputEl.innerHTML = '<div class="regime-model-loading"><span class="home-scan-spinner" aria-hidden="true"></span> Running model analysis…</div>';
    }

    const promise = api.modelAnalyzeRegime(_latestRegimePayload, _latestPlaybookPayload);
    _regimeModelInflight = promise;

    try{
      const result = await promise;
      if(_regimeModelInflight !== promise) return; // stale
      _renderRegimeModelOutput(result?.analysis || result);
    }catch(err){
      if(_regimeModelInflight !== promise) return;
      _renderRegimeModelError(err?.message || 'Model analysis failed');
    }finally{
      if(_regimeModelInflight === promise){
        _regimeModelInflight = null;
      }
      if(regimeModelBtnEl){
        regimeModelBtnEl.disabled = false;
        regimeModelBtnEl.textContent = 'Model Analysis';
      }
    }
  }

  function _renderRegimeModelError(message){
    if(!regimeModelOutputEl) return;
    regimeModelOutputEl.style.display = 'block';
    regimeModelOutputEl.innerHTML = `<div class="regime-model-error">${_esc(message)}</div>`;
  }

  /* ── End Regime Model Analysis ─────────────────────────────────── */

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
    put_credit_spread: '#/credit-spread',
    covered_call: '#/income',
    call_debit: '#/debit-spreads',
    iron_condor: '#/strategy-iron-condor',
    put_debit: '#/debit-spreads',
    csp_far_otm: '#/income',
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

  /* ── shared module delegates ── */
  const _fmtLib = window.BenTradeUtils.format;
  const _accessor = window.BenTradeUtils.tradeAccessor;
  const _card    = window.BenTradeTradeCard;
  const toNumber = _fmtLib.toNumber;
  const fmt      = _fmtLib.num;
  const fmtSigned = _fmtLib.signed;
  const fmtPct   = _fmtLib.signedPct;
  const toPctString = _fmtLib.pct;
  const metricMissingReason = _card.metricMissingReason;

  function normalizeSymbol(value){
    return String(value || '').trim().toUpperCase();
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
      || text.includes('csp');
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
    const comp = (trade?.computed && typeof trade.computed === 'object') ? trade.computed : {};
    const fields = {
      pop: comp?.pop,
      ev: comp?.expected_value,
      return_on_risk: comp?.return_on_risk ?? trade?.return_on_risk,
      max_profit: comp?.max_profit,
      max_loss: comp?.max_loss,
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
    const symbol = normalizeSymbol(row?.symbol);
    const score = _fmtLib.normalizeScore(row?.composite_score ?? row?.trade_quality_score ?? row?.score) ?? 0;
    const comp = (row?.computed && typeof row.computed === 'object') ? row.computed : {};
    const ev = toNumber(comp?.expected_value ?? row?.ev ?? row?.edge);
    const pop = toNumber(comp?.pop ?? row?.pop);
    const ror = toNumber(comp?.return_on_risk ?? row?.return_on_risk ?? row?.ror);
    const strategy = String(row?.strategy_id || row?.type || row?.recommended_strategy || source?.label || 'idea');
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
    const maxProfit = toNumber(comp?.max_profit ?? raw?.max_profit);
    const maxLoss = toNumber(comp?.max_loss ?? raw?.max_loss);
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
    const symbol = normalizeSymbol(row?.symbol || raw?.symbol) || 'N/A';
    const strategy = String(row?.strategy || raw?.strategy_id || raw?.type || raw?.recommended_strategy || 'idea');
    const strategySuggestsOptions = isLikelyOptionsStrategy(strategy);
    const isStock = !strategySuggestsOptions && (inferredSource === 'stock' || String(row?.source || '').toLowerCase().includes('stock scanner'));
    const rank = _fmtLib.normalizeScore(row?.rank ?? row?.score ?? row?.rank_score ?? raw?.rank_score ?? raw?.composite_score ?? raw?.trade_quality_score) ?? 0;

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
      // Prefer per-contract EV from computed (unified with scanner), then key_metrics
      const comp = (raw?.computed && typeof raw.computed === 'object') ? raw.computed : {};
      ev = toNumber(comp?.expected_value ?? row?.key_metrics?.ev_to_risk ?? row?.key_metrics?.ev ?? row?.ev);
      if(ev === null){
        ev = toNumber(raw?.ev ?? row?.edge ?? row?.expected_value);
      }

      pop = toNumber(comp?.pop ?? row?.key_metrics?.pop ?? row?.pop);
      if(pop === null){
        pop = toNumber(row?.pop ?? raw?.pop);
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
        symbol: String(raw?.symbol || symbol || '').toUpperCase(),
        strategy_id: String(raw?.strategy_id || strategy || ''),
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

  const escapeHtml = _fmtLib.escapeHtml;

  /**
   * toScannerTrade — Adapter: converts an Opportunity Engine idea into
   * the raw trade shape expected by BenTradeOptionTradeCardModel.map().
   * The mapper reads from .computed, .details, root-level keys, etc.
   * We shallow-copy to avoid mutating the source idea.
   *
   * For stock scanner candidates the raw object has a completely different
   * shape (no computed/details/legs/strikes).  We bridge it here so that
   * the 4-tier metric resolver in the card model picks up stock-specific
   * metrics just like option trades.
   */
  function toScannerTrade(idea){
    const raw = idea.trade && typeof idea.trade === 'object' ? { ...idea.trade } : {};
    if(!raw.symbol)      raw.symbol      = String(idea.symbol || '');
    if(!raw.strategy_id) raw.strategy_id = String(idea.strategy || raw.spread_type || raw.strategy || '');

    /* ── Stock candidate bridge ── */
    const isStock = idea.sourceType === 'stock' || raw.type === 'stock_buy';
    if(isStock){
      raw.strategy_id = raw.strategy_id || 'stock_buy';
      raw.trade_key   = raw.trade_key || raw.idea_key || `${raw.symbol}|STOCK|stock_scanner`;
      raw.underlying_price = raw.underlying_price ?? raw.price ?? null;
      if(!raw.trend) raw.trend = raw.trend || idea.trend || '';

      /* Surface stock scores into 'computed' so the 4-tier resolver
         finds them at tier-1 (same as option trades). */
      const m = raw.metrics && typeof raw.metrics === 'object' ? raw.metrics : {};
      raw.computed = Object.assign({}, raw.computed || {}, {
        rank_score:       raw.composite_score ?? null,
        trend_score:      raw.trend_score ?? null,
        momentum_score:   raw.momentum_score ?? null,
        volatility_score: raw.volatility_score ?? null,
        pullback_score:   raw.pullback_score ?? null,
        catalyst_score:   raw.catalyst_score ?? null,
        rsi14:            m.rsi14 ?? null,
        ema20:            m.ema20 ?? null,
        sma50:            m.sma50 ?? null,
        iv_rv_ratio:      m.iv_rv_ratio ?? null,
      });
    }

    return raw;
  }

  function opportunityKey(idea, idx){
    const symbol = normalizeSymbol(idea?.symbol || idea?.trade?.symbol || idea?.trade?.underlying || 'N/A');
    const strategy = String(idea?.strategy || idea?.trade?.strategy_id || idea?.trade?.spread_type || idea?.trade?.strategy || 'idea');
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

  /**
   * Render trade model analysis output as inline HTML for a card.
   * @param {object} model – { status, recommendation, confidence, summary }
   * @returns {string} HTML
   */
  function _renderTradeModelOutput(model){
    if(!model) return '';
    const esc = escapeHtml;

    if(model.status === 'running'){
      return '<div style="font-size:12px;color:var(--muted);padding:6px 10px;"><span class="home-scan-spinner" aria-hidden="true"></span> Running model analysis\u2026</div>';
    }

    if(model.status === 'error'){
      const msg = String(model.summary || 'Model analysis failed').trim();
      return `<div style="font-size:12px;color:#ff6b6b;padding:6px 10px;border:1px solid rgba(255,107,107,0.25);border-radius:6px;margin:4px 0;">\u26A0 ${esc(msg)}</div>`;
    }

    const rec = String(model.recommendation || 'UNKNOWN').toUpperCase();
    const confPct = toNumber(model.confidence) !== null ? ` (${(toNumber(model.confidence) * 100).toFixed(0)}%)` : '';
    const summary = String(model.summary || '').trim();

    const recColors = {
      'ACCEPT': 'rgba(0,220,120,0.9)',
      'REJECT': 'rgba(255,90,90,0.9)',
      'NEUTRAL': 'rgba(180,180,200,0.85)',
    };
    const color = recColors[rec] || recColors['NEUTRAL'];

    let html = `<div style="font-size:12px;padding:8px 10px;border:1px solid ${color.replace('0.9','0.3').replace('0.85','0.3')};border-radius:6px;margin:4px 0;">`;
    html += `<div style="font-weight:700;color:${color};margin-bottom:4px;">${esc(rec)}${esc(confPct)}</div>`;
    if(summary){
      html += `<div style="color:var(--text-secondary,#ccc);line-height:1.4;">${esc(summary)}</div>`;
    }
    html += '</div>';
    return html;
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
    const strategy = String(idea?.trade?.spread_type || idea?.trade?.strategy || idea.strategy || 'put_credit_spread');
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
    const symbol = String(src?.symbol || idea?.symbol || '').toUpperCase();
    const strategy = String(src?.strategy_id || idea?.strategy || '');
    return {
      ...src,
      symbol,
      strategy_id: strategy,
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
    const direct = String(idea?.report_file || idea?.trade?.report_file || idea?.trade?._source_report_file || '').trim();
    if(direct){
      console.info('[MODEL_TRACE] resolveModelSourceFile → direct:', direct);
      return direct;
    }

    const sessionSource = getModelSourceFromSession();
    if(sessionSource){
      console.info('[MODEL_TRACE] resolveModelSourceFile → session:', sessionSource);
      return sessionSource;
    }

    const strategyId = String(idea?.strategy_id || strategyIdFromValue(idea?.strategy || idea?.trade?.spread_type || idea?.trade?.strategy) || '').trim();
    if(strategyId && api?.listStrategyReports){
      try{
        const files = await api.listStrategyReports(strategyId);
        const candidate = Array.isArray(files) && files.length ? String(files[0] || '').trim() : '';
        if(candidate){
          console.info('[MODEL_TRACE] resolveModelSourceFile → listReports:', candidate);
          return candidate;
        }
      }catch(_err){
        console.warn('[MODEL_TRACE] resolveModelSourceFile → listReports error:', _err);
      }
    }

    /* Fallback: generate a synthetic source identifier so the backend can
       tag its output file.  The "source" param is an output label, not an
       input dependency — the model evaluates the trade payload directly. */
    const sym = String(idea?.symbol || idea?.trade?.underlying || idea?.trade?.symbol || 'unknown').toUpperCase();
    const strat = strategyId || 'unknown';
    const synthetic = `home_${strat}_${sym}`.replace(/[^a-zA-Z0-9_]/g, '_');
    console.info('[MODEL_TRACE] resolveModelSourceFile → synthetic fallback:', synthetic);
    return synthetic;
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

  /* ── Dedupe guard for Home model analysis (single-flight per opKey) ── */
  const _homeModelInFlight = new Set();

  async function runModelForOpportunity(idea, onModel, originTag = 'home_opportunities'){
    const _tag = `[MODEL_TRACE:home] runModelForOpportunity`;

    if(!idea){
      console.warn(_tag, 'called with null idea');
      if(typeof onModel === 'function') onModel({ status: 'error', recommendation: 'ERROR', confidence: null, summary: 'No trade selected.' });
      return false;
    }

    if(idea.sourceType === 'stock'){
      console.info(_tag, 'stock idea — skipping (not an options trade)');
      if(typeof onModel === 'function') onModel({ status: 'error', recommendation: 'N/A', confidence: null, summary: 'Model analysis is not available for stock ideas.' });
      return false;
    }

    /* Dedupe guard — only one request per opportunity at a time */
    const opKey = idea._opKey || opportunityKey(idea, -1);
    if(_homeModelInFlight.has(opKey)){
      console.info(_tag, 'dedupe guard — already in-flight for', opKey);
      return false;
    }
    _homeModelInFlight.add(opKey);
    console.info(_tag, 'start', { opKey, originTag, symbol: idea?.symbol, strategy: idea?.strategy });

    const resolvedIdea = resolveIdeaForModel(idea);
    let sourceFile;
    try{
      sourceFile = await resolveModelSourceFile(resolvedIdea);
    }catch(sfErr){
      console.warn(_tag, 'resolveModelSourceFile threw:', sfErr);
      sourceFile = null;
    }
    if(!sourceFile){
      const nextModel = {
        status: 'error',
        recommendation: 'ERROR',
        confidence: null,
        summary: 'No report source available for model analysis.',
      };
      if(typeof onModel === 'function') onModel(nextModel);
      _homeModelInFlight.delete(opKey);
      return false;
    }

    const tradePayload = {
      ...(resolvedIdea?.trade_payload && typeof resolvedIdea.trade_payload === 'object' ? resolvedIdea.trade_payload : {}),
      ...(resolvedIdea?.trade && typeof resolvedIdea.trade === 'object' ? resolvedIdea.trade : {}),
      symbol: String(resolvedIdea?.trade?.symbol || resolvedIdea?.symbol || '').toUpperCase(),
      strategy_id: String(resolvedIdea?.trade?.strategy_id || resolvedIdea?.strategy || ''),
      home_origin: String(originTag || 'home_opportunities'),
    };
    if(typeof onModel === 'function'){
      onModel({ status: 'running', recommendation: 'RUNNING', confidence: null, summary: 'Running...' });
    }

    console.info(_tag, 'calling api.modelAnalyze', { source: sourceFile, symbol: tradePayload.symbol, strategy_id: tradePayload.strategy_id });
    try{
      const result = await api.modelAnalyze(tradePayload, sourceFile);
      console.info(_tag, 'response OK', { recommendation: result?.evaluated_trade?.model_evaluation?.recommendation });
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
      console.warn(_tag, 'api.modelAnalyze error:', err?.detail || err?.message || err);
      const nextModel = {
        status: 'error',
        recommendation: 'ERROR',
        confidence: null,
        summary: String(err?.detail || err?.message || err || 'Model analysis failed'),
      };
      if(typeof onModel === 'function') onModel(nextModel);
      return false;
    }finally{
      _homeModelInFlight.delete(opKey);
    }
  }

  const metricValueOrMissing = _card.metricValueOrMissing;

  /* renderSourceHealth — REMOVED: Source Health is global-only (index.html / source_health.js) */

  function renderChart(svgEl, history, options){
    const rows = Array.isArray(history) ? history : [];
    const points = rows.map((row) => toNumber(row?.close)).filter((v) => v !== null);
    if(!points.length){
      svgEl.innerHTML = '';
      return;
    }

    /* ── Parse dates (if present) ── */
    const dates = rows.map((row) => {
      if(!row?.date) return null;
      const d = new Date(row.date + 'T00:00:00');
      return isNaN(d.getTime()) ? null : d;
    });
    const hasDates = dates.length === points.length && dates[0] !== null && dates[dates.length - 1] !== null;

    const width = 800;
    const height = 220;
    const margin = { top: 12, right: 12, bottom: hasDates ? 36 : 22, left: 52 };
    const plotW = width - margin.left - margin.right;
    const plotH = height - margin.top - margin.bottom;
    const min = Math.min(...points);
    const max = Math.max(...points);
    const span = Math.max(max - min, 0.0001);

    const yFor = (value) => margin.top + (1 - ((value - min) / span)) * plotH;

    /* ── X scale ── */
    let xFor;
    if(hasDates){
      const t0 = dates[0].getTime();
      const t1 = dates[dates.length - 1].getTime();
      const tSpan = Math.max(t1 - t0, 1);
      xFor = (index) => margin.left + ((dates[index].getTime() - t0) / tSpan) * plotW;
    } else {
      xFor = (index) => margin.left + (index / Math.max(points.length - 1, 1)) * plotW;
    }

    const path = points.map((value, index) => `${index === 0 ? 'M' : 'L'} ${xFor(index).toFixed(2)} ${yFor(value).toFixed(2)}`).join(' ');

    /* ── Y ticks / grid ── */
    const yTicks = Array.from({ length: 4 }, (_, idx) => {
      const ratio = idx / 3;
      const value = max - (span * ratio);
      return { value, y: yFor(value) };
    });

    const yGrid = yTicks.map((tick) => `<line x1="${margin.left}" y1="${tick.y.toFixed(2)}" x2="${(width - margin.right).toFixed(2)}" y2="${tick.y.toFixed(2)}" stroke="rgba(0,234,255,0.12)" stroke-width="1"></line>`).join('');
    const yLabels = yTicks.map((tick) => `<text x="${(margin.left - 8).toFixed(2)}" y="${(tick.y + 3).toFixed(2)}" text-anchor="end" fill="rgba(215,251,255,0.85)" font-size="10">${Number(tick.value).toFixed(2)}</text>`).join('');

    /* ── X ticks (weekly, only when dates are available) ── */
    let xGrid = '';
    let xLabels = '';
    if(hasDates){
      const t0 = dates[0].getTime();
      const t1 = dates[dates.length - 1].getTime();
      const tSpan = Math.max(t1 - t0, 1);
      const xPixel = (ms) => margin.left + ((ms - t0) / tSpan) * plotW;

      /* Find first Monday on or after the start date */
      const start = new Date(dates[0]);
      const dayOfWeek = start.getDay();          // 0=Sun … 6=Sat
      const daysToMon = dayOfWeek === 0 ? 1 : (dayOfWeek <= 1 ? (1 - dayOfWeek) : (8 - dayOfWeek));
      const firstMon = new Date(start);
      firstMon.setDate(firstMon.getDate() + daysToMon);

      /* Determine tick interval — keep ~6-12 visible labels */
      const totalWeeks = Math.round((t1 - t0) / (7 * 86400000));
      let weekStep = 1;
      if(totalWeeks > 36) weekStep = 4;
      else if(totalWeeks > 18) weekStep = 2;

      const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      const tickLines = [];
      const tickLabels = [];
      let cursor = new Date(firstMon);
      while(cursor.getTime() <= t1){
        const px = xPixel(cursor.getTime());
        if(px >= margin.left && px <= width - margin.right){
          const yBottom = height - margin.bottom;
          tickLines.push(`<line x1="${px.toFixed(2)}" y1="${margin.top}" x2="${px.toFixed(2)}" y2="${yBottom.toFixed(2)}" stroke="rgba(0,234,255,0.08)" stroke-width="1"></line>`);
          tickLabels.push(`<text x="${px.toFixed(2)}" y="${(yBottom + 14).toFixed(2)}" text-anchor="middle" fill="rgba(215,251,255,0.7)" font-size="9">${monthNames[cursor.getMonth()]} ${cursor.getDate()}</text>`);
        }
        cursor.setDate(cursor.getDate() + 7 * weekStep);
      }
      xGrid = tickLines.join('');
      xLabels = tickLabels.join('');
    }

    svgEl.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svgEl.innerHTML = `
      ${yGrid}
      ${xGrid}
      <line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${(height - margin.bottom).toFixed(2)}" stroke="rgba(0,234,255,0.45)" stroke-width="1"></line>
      <line x1="${margin.left}" y1="${(height - margin.bottom).toFixed(2)}" x2="${(width - margin.right).toFixed(2)}" y2="${(height - margin.bottom).toFixed(2)}" stroke="rgba(0,234,255,0.45)" stroke-width="1"></line>
      ${yLabels}
      ${xLabels}
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
    const _debugRegime = window.BENTRADE_DEBUG_REGIME;
    regimeComponentsEl.innerHTML = componentOrder.map((key) => {
      const item = components[key] || {};
      const rawScore = toNumber(item?.score);
      /* Score is already 0–100 from backend _normalize_component.
         Clamp to [0, 100] for both display and fill width. */
      const score = rawScore !== null ? Math.max(0, Math.min(100, rawScore)) : 0;
      const fillWidth = Math.max(2, Math.round(score));
      const signals = Array.isArray(item?.signals) ? item.signals : [];
      const label = key.charAt(0).toUpperCase() + key.slice(1);
      let detailHtml = '';

      if(_debugRegime){
        console.info(`[REGIME_BAR] ${key}: raw=${rawScore}, clamped=${score}, fill=${fillWidth}%`);
      }

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
          <div class="home-regime-track"><div class="home-regime-fill" style="width:${fillWidth}%;"></div></div>
          <div class="home-regime-score">${Math.round(score)}%</div>
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

  function renderScannerOpportunities(ideas){
    latestOpportunities = Array.isArray(ideas) ? ideas.slice() : [];
    const tc = _card;              // BenTradeTradeCard building blocks
    const TOP = window.BenTradeScannerOrchestrator?.TOP_N || 9;

    /* ── Playbook-weighted re-sort (does NOT alter raw scanner scores) ── */
    const pbScorer = window.BenTradePlaybookScoring;
    let sortedIdeas = latestOpportunities;
    let pbNormalized = null;
    if(pbScorer && (_latestPlaybookPayload || _latestRegimePayload)){
      pbNormalized = pbScorer.normalizePlaybook(_latestPlaybookPayload, _latestRegimePayload);
      if(pbNormalized.primary.size > 0 || pbNormalized.avoid.size > 0){
        sortedIdeas = pbScorer.sortByPlaybook(latestOpportunities, pbNormalized);
      }
    }

    const top = sortedIdeas.slice(0, TOP).map((idea, idx) => {
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
      /* Carry playbook metadata (from sortByPlaybook's _pb annotation) for UI */
      if(idea._pb) normalized._pb = idea._pb;
      return normalized;
    });

    /* ── Empty state ── */
    if(!top.length){
      scannerOpportunitiesEl.innerHTML = `
        <div class="home-opp-empty">
          <div class="home-opp-empty-icon" aria-hidden="true">📡</div>
          <div class="home-opp-empty-text">No opportunities yet — run a scan to generate picks.</div>
          <button type="button" class="btn qtButton home-run-scan-btn" data-action="trigger-scan">Run Scan</button>
        </div>
      `;
      scannerOpportunitiesEl.querySelector('[data-action="trigger-scan"]')?.addEventListener('click', () => {
        runScanQueue().catch((err) => {
          setScanError(String(err?.message || err || 'Queue failed'));
          setScanStatus('');
        });
      });
      return;
    }

    /* ── Populated state — render scanner-style trade cards ── */
    /* Build scannerTrade objects (parallel to top[]) for mapper-based actions.
       Also enforce the tradeKey safety check here.
       
       ROOT CAUSE FIX: pbIndicator was appended as a sibling div AFTER the
       .trade-card div.  Both became separate CSS Grid children of
       .home-scanner-opportunities, doubling the grid item count and causing
       every other visual slot to be a tiny pb-indicator instead of a card
       (the "alternating missing cards" bug).  Fix: inject pbIndicator
       INSIDE the .trade-card wrapper by replacing its closing </div>. */
    _oeTradesForActions = [];
    _oeTopIdeas = [];
    const cardsHtml = [];
    const seenTradeKeys = new Set();

    top.forEach((idea, rawIdx) => {
      const scannerTrade = toScannerTrade(idea);
      const tradeKey = String(scannerTrade.trade_key || '').trim();

      /* Safety check: exclude cards with no tradeKey and log a warning */
      if(!tradeKey && idea.sourceType !== 'stock'){
        console.warn('[OE] Excluding opportunity without trade_key:', idea.symbol, idea.strategy);
        return;
      }

      /* Deduplicate by tradeKey — first occurrence wins (highest adjusted score) */
      const dedupeKey = tradeKey || `${idea.symbol}|${idea.strategy}|${rawIdx}`;
      if(seenTradeKeys.has(dedupeKey)){
        console.warn('[OE] Skipping duplicate trade_key:', dedupeKey);
        return;
      }
      seenTradeKeys.add(dedupeKey);

      const cardIdx = _oeTradesForActions.length;
      _oeTradesForActions.push(scannerTrade);
      _oeTopIdeas.push(idea);

      let cardHtml = tc.renderFullCard(scannerTrade, cardIdx, {
        strategyHint: String(idea.strategy || scannerTrade.strategy_id || '').toLowerCase(),
        rankOverride: _fmtLib.normalizeScore(idea.rank ?? idea.score) ?? null,
        modelStatus:  idea.model?.status === 'running' ? 'running' : null,
        expandState:  _oeExpandState,
      });

      /* Playbook lane indicator — injected INSIDE the .trade-card wrapper
         (before its closing </div>) so it stays a single CSS Grid child. */
      if(idea._pb && pbScorer){
        const pb = idea._pb;
        const summary = pbScorer.reasonSummary(pb);
        if(summary){
          const laneColors = {
            primary: 'rgba(0,220,120,0.85)',
            secondary: 'rgba(0,180,255,0.85)',
            avoid: 'rgba(255,90,90,0.85)',
            neutral: 'rgba(180,180,200,0.65)',
          };
          const color = laneColors[pb.lane] || laneColors.neutral;
          const laneLabel = (pb.lane || 'neutral').charAt(0).toUpperCase() + (pb.lane || 'neutral').slice(1);
          const dot = `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${color};margin-right:5px;vertical-align:middle;"></span>`;
          const pbIndicator = `<div class="pb-lane-indicator" style="font-size:10px;color:${color};padding:2px 10px 4px;line-height:1.3;" title="${_fmtLib.escapeHtml(summary)}">${dot}${_fmtLib.escapeHtml(laneLabel)}${pb.multiplier !== 1 ? ' \u00B7 Adj ' + pb.adjustedScore.toFixed(1) + '%' : ''}</div>`;
          /* Insert before the final </div> of .trade-card */
          cardHtml = cardHtml.replace(/<\/div>\s*$/, pbIndicator + '</div>');
        }
      }
      cardsHtml.push(cardHtml);
    });

    if(!cardsHtml.length){
      scannerOpportunitiesEl.innerHTML = `
        <div class="home-opp-empty">
          <div class="home-opp-empty-icon" aria-hidden="true">📡</div>
          <div class="home-opp-empty-text">No valid opportunities (all missing trade keys).</div>
        </div>`;
      return;
    }

    scannerOpportunitiesEl.innerHTML = `
      <div class="home-opp-count stock-note">${cardsHtml.length} Pick${cardsHtml.length !== 1 ? 's' : ''}</div>
      ${cardsHtml.join('')}
    `;

    /* ── Action wiring — mirrors strategy_dashboard_shell.js exactly ── */

    /* Collapse/expand persistence via <details> toggle */
    scannerOpportunitiesEl.querySelectorAll('details.trade-card-collapse').forEach((details) => {
      details.addEventListener('toggle', () => {
        const tk = details.dataset.tradeKey || details.closest('.trade-card')?.dataset?.tradeKey;
        if(tk) _oeExpandState[tk] = details.open;
      });
    });

    /* Copy trade key buttons */
    scannerOpportunitiesEl.querySelectorAll('[data-copy-trade-key]').forEach((copyBtn) => {
      copyBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if(_card?.copyTradeKey) _card.copyTradeKey(copyBtn.dataset.copyTradeKey, copyBtn);
      });
    });

    /* Action buttons — use mapper model + buildTradeActionPayload (identical to scanner shell) */
    scannerOpportunitiesEl.querySelectorAll('button[data-action]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();

        const action = String(btn.getAttribute('data-action') || '');
        const cardEl = btn.closest('.trade-card');
        const cardIdx = cardEl ? parseInt(cardEl.dataset.idx, 10) : -1;
        const trade = _oeTradesForActions[cardIdx];
        const idea = _oeTopIdeas[cardIdx];
        if(!trade || !idea) return;

        /* Map through canonical mapper — identical to scanner shell */
        const strategyHint = String(idea.strategy || trade.strategy_id || '').toLowerCase();
        const model = _mapper ? _mapper.map(trade, strategyHint) : null;
        const payload = (_mapper && model) ? _mapper.buildTradeActionPayload(model) : {};

        if(action === 'execute'){
          if(window.BenTradeExecutionModal && window.BenTradeExecutionModal.open){
            window.BenTradeExecutionModal.open(trade, payload);
          } else if(typeof window.executeTrade === 'function'){
            window.executeTrade(trade);
          }
          return;
        }

        if(action === 'reject'){
          const body = {
            trade_key: payload.tradeKey || '',
            symbol: payload.symbol || '',
            strategy: payload.strategyId || '',
            action: 'reject',
          };
          fetch('/api/decisions/reject', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          }).then(res => {
            if(res.ok){
              if(cardEl) cardEl.classList.add('manually-rejected');
              btn.disabled = true;
              btn.textContent = 'Rejected';
            }
          }).catch(() => {});
          return;
        }

        if(action === 'model-analysis'){
          /* Run model analysis inline on this card — no navigation */
          console.info('[MODEL_TRACE:home] button clicked', { cardIdx, symbol: idea?.symbol, strategy: idea?.strategy });
          const modelBtn = btn;
          const modelOutputEl = cardEl?.querySelector('[data-model-output]');
          modelBtn.disabled = true;
          modelBtn.textContent = 'Running\u2026';
          if(modelOutputEl){
            modelOutputEl.style.display = 'block';
            modelOutputEl.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:6px 10px;"><span class="home-scan-spinner" aria-hidden="true"></span> Running model analysis\u2026</div>';
          }

          runModelForOpportunity(idea, (modelResult) => {
            console.info('[MODEL_TRACE:home] callback received', { status: modelResult?.status, recommendation: modelResult?.recommendation });
            /* Update per-card state */
            const opKey = idea._opKey || opportunityKey(idea, cardIdx);
            opportunityModelState.set(opKey, modelResult);

            /* Update button */
            modelBtn.disabled = false;
            modelBtn.textContent = 'Run Model Analysis';

            /* Render result in card */
            if(modelOutputEl){
              if(!modelResult || modelResult.status === 'not_run'){
                modelOutputEl.style.display = 'none';
              } else {
                modelOutputEl.style.display = 'block';
                modelOutputEl.innerHTML = _renderTradeModelOutput(modelResult);
              }
            }
          }, 'home_card_action');
          return;
        }

        if(action === 'workbench'){
          if(payload.tradeKey){
            window.location.hash = '#/admin/data-workbench?trade_key=' + encodeURIComponent(payload.tradeKey);
          } else if(tc.openDataWorkbenchByTrade){
            tc.openDataWorkbenchByTrade(trade);
          }
          return;
        }

        if(action === 'data-workbench'){
          if(tc.openDataWorkbenchByTrade){
            tc.openDataWorkbenchByTrade(trade);
          } else if(payload.tradeKey){
            window.location.hash = '#/admin/data-workbench?trade_key=' + encodeURIComponent(payload.tradeKey);
          }
          return;
        }
      });
    });
  }

  /* renderStrategyBoard — REMOVED: Strategy Leaderboard is global-only (index.html / sessionStats.js) */

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
      const score = _fmtLib.normalizeScore((row?.composite || {}).score) ?? 0;
      const label = String((row?.composite || {}).label || 'Neutral');
      const positives = (Array.isArray(row?.signals) ? row.signals : []).filter((item) => item?.value).slice(0, 4);
      return `
        <div class="home-signal-row">
          <div class="home-signal-head"><span class="qtPill">${symbol}</span> <span class="stock-note">${label} ${score.toFixed(1)}%</span></div>
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
        const comp = (row?.computed && typeof row.computed === 'object') ? row.computed : {};
        const candidate = toNumber(comp?.max_loss ?? row?.max_loss);
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

  /* ── Active Trades per-strategy bubble counts ── */
  function renderActiveTradesCount(activeTradesPayload){
    if(!activeTradesCountEl) return;
    const trades = Array.isArray(activeTradesPayload?.active_trades) ? activeTradesPayload.active_trades : [];
    const buckets = {
      credit_put: 0, credit_call: 0, debit_spreads: 0, iron_condor: 0,
      butterflies: 0, calendar: 0, income: 0, stock_scanner: 0,
    };
    trades.forEach(t => {
      const sid = (t?.strategy_id || t?.strategy || '').toLowerCase().replace(/[\s-]/g, '_');
      if(sid in buckets) buckets[sid]++;
      else if(sid.includes('put')) buckets.credit_put++;
      else if(sid.includes('call') && !sid.includes('iron')) buckets.credit_call++;
    });
    const labels = {
      credit_put: 'Credit Put', credit_call: 'Credit Call', debit_spreads: 'Debit Spreads',
      iron_condor: 'Iron Condor', butterflies: 'Butterflies', calendar: 'Calendar',
      income: 'Income', stock_scanner: 'Stocks',
    };
    const total = trades.length;
    activeTradesCountEl.innerHTML = `<div class="statTile"><div class="statLabel">Total</div><div class="statValue">${total}</div></div>`
      + Object.keys(buckets).map(k =>
        `<div class="statTile"><div class="statLabel">${labels[k]}</div><div class="statValue">${buckets[k]}</div></div>`
      ).join('');
  }

  /* renderSessionStats — REMOVED: Session Stats is global-only (index.html / sessionStats.js) */

  /* ── Equity Curve ── */
  function renderEquityCurve(activeTradesPayload){
    if(!equityCurveEl) return;
    // Build a best-effort equity series from active trades sorted by open date
    const trades = Array.isArray(activeTradesPayload?.active_trades) ? activeTradesPayload.active_trades : [];
    let equitySeries = [];
    if(trades.length >= 2){
      // Sort by opened_at / created_at ascending, accumulate P&L
      const sorted = trades
        .map(t => {
          const comp = (t?.computed && typeof t.computed === 'object') ? t.computed : {};
          const pnl = toNumber(comp?.unrealized_pnl ?? comp?.pnl ?? t?.pnl) || 0;
          const dateStr = t?.opened_at || t?.created_at || '';
          return { date: dateStr, pnl };
        })
        .filter(r => r.date)
        .sort((a, b) => a.date.localeCompare(b.date));
      if(sorted.length >= 2){
        let cumulative = 0;
        equitySeries = sorted.map(r => {
          cumulative += r.pnl;
          return { close: cumulative };
        });
      }
    }
    if(equitySeries.length >= 2){
      if(equityCurveEmptyEl) equityCurveEmptyEl.style.display = 'none';
      equityCurveEl.style.display = '';
      renderChart(equityCurveEl, equitySeries, { stroke: 'rgba(126,247,184,0.92)' });
    } else {
      // Show empty state
      if(equityCurveEmptyEl) equityCurveEmptyEl.style.display = '';
      equityCurveEl.style.display = 'none';
    }
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

  function renderSnapshot(snapshot){
    const payload = (snapshot && typeof snapshot === 'object') ? snapshot : {};
    const data = (payload.data && typeof payload.data === 'object') ? payload.data : {};
    const meta = (payload.meta && typeof payload.meta === 'object') ? payload.meta : {};

    const regimePayload = data.regime || {};
    const spySummary = data.spy || emptySummary('SPY');
    const vixSummary = data.vix || emptySummary('VIXY');
    const macro = data.macro || {};
    const signalUniversePayload = data.signalsUniverse || { items: [] };
    const playbookPayload = data.playbook || null;
    const riskSnapshot = data.portfolioRisk || { portfolio: {} };
    const activeTradesPayload = data.activeTrades || { active_trades: [] };
    const ideas = Array.isArray(data.opportunities) ? data.opportunities : [];
    const indexSummaries = data.indexSummaries || Object.fromEntries(INDEX_SYMBOLS.map((symbol) => [symbol, emptySummary(symbol)]));
    const sectorSummaries = data.sectors || {};

    // Stash regime + playbook for on-demand model analysis (auto-refresh safe)
    _latestRegimePayload = regimePayload;
    _latestPlaybookPayload = playbookPayload;

    renderRegime(regimePayload, spySummary, macro);
    renderIndexes(indexSummaries);
    renderSectors(sectorSummaries);
    renderScannerOpportunities(ideas);
    renderSignalHub(signalUniversePayload);
    if(playbookPayload){
      renderStrategyPlaybook(playbookPayload);
    } else {
      renderPlaybookFallback('Playbook unavailable');
    }
    /* Source Health / Session Stats / Strategy Leaderboard are global-only — not rendered here */
    renderRisk(riskSnapshot, activeTradesPayload);
    renderActiveTradesCount(activeTradesPayload);
    renderEquityCurve(activeTradesPayload);
    renderMacro(macro, spySummary);

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
        signalsUniverse: { items: [] },
        playbook: null,
        portfolioRisk: { portfolio: {} },
        activeTrades: { active_trades: [] },
        opportunities: [],
        indexSummaries: Object.fromEntries(INDEX_SYMBOLS.map((symbol) => [symbol, emptySummary(symbol)])),
        sectors: {},
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

  function isNotImplementedError(err){
    const status = Number(err?.status || err?.statusCode);
    if(status === 404 || status === 405 || status === 501) return true;
    const detail = String(err?.detail || err?.message || '').toLowerCase();
    return detail.includes('not implemented') || detail.includes('not found');
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

    /* ── Step 0: Home Dashboard data first (regime, playbook, SPY, VIX, sectors, risk) ── */
    steps.push({
      id: 'home_dashboard',
      label: 'Home Dashboard data',
      provider: 'internal',
      endpoint: 'homeCache.refreshCore',
      timeoutMs: 60000,
      critical: true,
      optional: false,
      fn: () => runLoadSequence({ force: true, showOverlay: false, homeOnly: true }),
    });

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

    /* ── Scanner Suite (replaces individual stock + strategy steps) ── */
    steps.push({
      id: 'scanner_suite',
      label: 'Scanner Suite (all scanners)',
      provider: 'internal',
      endpoint: 'orchestrator',
      timeoutMs: 300000,
      critical: true,
      optional: false,
      fn: () => {
        const orchestrator = window.BenTradeScannerOrchestrator;
        if(!orchestrator) return Promise.reject(new Error('Scanner orchestrator unavailable'));
        const currentLevel = String(scanPresetEl?.value || 'balanced');
        return orchestrator.runScannerSuite({
          filterLevel: currentLevel,
          logFn: pushLog,
          onStepComplete: ({ label, ok, tradeCount }) => {
            if(overlay?.isOpen?.()){
              overlay.setStatus(`Full App Refresh • Scanners • ${label}${ok ? ` (${tradeCount})` : ' failed'}`);
            }
          },
        });
      },
      afterSuccess: async () => {
        updateHomeSessionSnapshot();
      },
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
      id: 'source_health_refresh',
      label: 'Source health refresh',
      provider: 'internal',
      endpoint: '/api/health/sources',
      timeoutMs: 12000,
      critical: false,
      optional: false,
      fn: () => window.BenTradeSourceHealthStore?.fetchSourceHealth?.({ force: true }) || Promise.resolve({}),
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
        await runLoadSequence({ force: true, showOverlay: false, homeOnly: false }).catch(() => {});
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

  async function runScanQueue(){
    if(queueState.isRunning) return;

    const orchestrator = window.BenTradeScannerOrchestrator;
    if(!orchestrator){
      setScanError('Scanner orchestrator unavailable');
      return;
    }

    const preset = String(scanPresetEl.value || 'balanced');
    const filterLevel = preset;   // dropdown now selects filter strictness level
    const scannerIds = orchestrator.presetToScannerIds(preset);
    const total = scannerIds.length;
    const runId = ++queueState.runId;
    let completed = 0;
    let warnings = 0;
    let criticalFail = null;

    queueState.isRunning = true;
    queueState.stopRequested = false;
    runQueueBtnEl.disabled = true;
    if(stopQueueBtnEl) stopQueueBtnEl.disabled = false;
    scanPresetEl.disabled = true;
    setScanError('');
    setScanStatus('');
    queueLogLines.splice(0, queueLogLines.length);
    renderQueueLog();

    appendQueueLog(`Queue level: ${preset} (${total} scanners)`);
    setQueueProgress({ current: 'Starting scanner suite...', completed: 0, total, running: true });

    try{
      /* Pass selected symbol subset if the user has narrowed the universe */
      const selectedSymbols = _homeSymbolSelector?.getSelected?.() || [];
      const result = await orchestrator.runScannerSuite({
        scannerIds,
        symbols: selectedSymbols.length ? selectedSymbols : undefined,
        filterLevel,
        logFn: (text) => {
          appendQueueLog(text);
          pushLog(text);
        },
        onStepComplete: ({ id, label, ok, error, tradeCount }) => {
          if(runId !== queueState.runId || queueState.stopRequested) return;
          if(ok){
            completed += 1;
            setQueueProgress({ current: `${label} (${tradeCount})`, completed, total, running: true });
          }else{
            // Check if this was an optional scanner
            const isDef = orchestrator.OPTION_SCANNER_DEFS.find((d) => d.id === id);
            const isOptional = isDef ? isDef.optional : false;
            if(isOptional){
              warnings += 1;
            }else{
              criticalFail = { id, label, error };
            }
            setQueueProgress({ current: `${label} failed`, completed, total, running: true });
          }
        },
      });

      if(runId !== queueState.runId) return;

      // Update home session snapshot after stats recording
      updateHomeSessionSnapshot();

      if(queueState.stopRequested){
        setQueueProgress({ current: 'Stopped', completed, total, running: false });
        setScanStatus('Stopped');
        appendQueueLog('Stopped: remaining steps cancelled');
      }else if(criticalFail){
        setQueueProgress({ current: 'Stopped on failure', completed, total, running: false });
        setScanError(`Queue failed at ${criticalFail.label}: ${criticalFail.error || 'n/a'}`);
        setScanStatus('Queue stopped');
      }else{
        const warnCount = result?.errors?.length || warnings;
        setQueueProgress({ current: 'Queue complete', completed: total, total, running: false });
        if(warnCount > 0){
          setScanStatus(`Queue complete with warnings (${warnCount}) • ${new Date().toLocaleTimeString()}`);
        }else{
          setScanStatus(`Queue complete • ${new Date().toLocaleTimeString()}`);
        }
      }

      await runLoadSequence({ force: true, showOverlay: false, homeOnly: false }).catch(() => {});
    }finally{
      if(runId === queueState.runId){
        queueState.isRunning = false;
        queueState.stopRequested = false;
        runQueueBtnEl.disabled = false;
        if(stopQueueBtnEl) stopQueueBtnEl.disabled = true;
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
  let _loadInFlight = null;       // singleton guard — prevents overlapping load sequences
  const overlay = window.BenTradeHomeLoadingOverlay?.create?.(scope) || null;

  /* ── Auto-refresh pause/resume state (persisted in localStorage) ── */
  const PAUSE_STORAGE_KEY = 'bentrade_home_autorefresh_paused';
  let autoRefreshPaused = localStorage.getItem(PAUSE_STORAGE_KEY) === '1';

  function _clearRefreshInterval(){
    if(refreshInterval){
      window.clearInterval(refreshInterval);
      refreshInterval = null;
    }
  }

  function _startRefreshInterval(){
    _clearRefreshInterval();
    refreshInterval = window.setInterval(() => {
      if(autoRefreshPaused) return;
      runLoadSequence({ force: false, showOverlay: false }).catch(() => {});
    }, Number(cacheStore.REFRESH_INTERVAL_MS || 90000));
  }

  function _syncPauseButton(){
    if(!pauseRefreshBtnEl) return;
    pauseRefreshBtnEl.textContent = autoRefreshPaused ? 'Resume' : 'Pause';
    pauseRefreshBtnEl.title = autoRefreshPaused
      ? 'Resume scheduled auto-refresh'
      : 'Pause scheduled auto-refresh';
    pauseRefreshBtnEl.style.opacity = autoRefreshPaused ? '0.65' : '1';
  }

  function toggleAutoRefreshPause(){
    autoRefreshPaused = !autoRefreshPaused;
    localStorage.setItem(PAUSE_STORAGE_KEY, autoRefreshPaused ? '1' : '0');
    _syncPauseButton();
    if(autoRefreshPaused){
      _clearRefreshInterval();
    } else {
      _startRefreshInterval();
    }
  }

  _syncPauseButton();

  if(!cacheStore){
    renderFallbackBlank();
    setError('Home cache store unavailable');
    return;
  }

  cacheStore.setRenderer((snapshot) => {
    renderSnapshot(snapshot || {});
    bindRetry();
  });

  function runLoadSequence({ force = false, showOverlay = false, homeOnly = true } = {}){
    /* ── Singleton guard: if a non-forced load is already running, reuse it ── */
    if(_loadInFlight && !force){
      // If caller wants the overlay but it's not open yet, open it now
      if(showOverlay && overlay && !overlay.isOpen()){
        overlay.open({
          status: 'Loading...',
          logs: logHistory,
          onCancel: () => { overlay.close(); },
          onRetry: () => { runLoadSequence({ force: true, showOverlay: true, homeOnly }).catch(() => {}); },
        });
      }
      return _loadInFlight;
    }
    /* For forced reloads while in-flight, let refreshCore handle dedup internally.
       We still replace _loadInFlight so the new promise is the canonical one. */

    const loadToken = ++activeLoadToken;

    if(showOverlay && overlay){
      overlay.open({
        status: 'Starting...',
        logs: logHistory,
        onCancel: () => {
          overlay.close();
        },
        onRetry: () => {
          runLoadSequence({ force: true, showOverlay: true, homeOnly }).catch(() => {});
        },
      });
    }

    if(!showOverlay){
      setRefreshingBadge(true);
    }

    pushLog(homeOnly ? 'Starting home data load (home-only)...' : 'Starting home data load (full)...');

    const refreshPromise = force
      ? cacheStore.refreshNow({ logFn: pushLog, homeOnly })
      : cacheStore.refreshSilent({ force: false, logFn: pushLog, homeOnly });

    _loadInFlight = refreshPromise
      .then((snapshot) => {
        pushLog('Home ready.');
        setError('');
        window.BenTradeSessionStatsStore?.recordHomeRefresh?.();
        if(overlay && overlay.isOpen()){
          overlay.setStatus('Home ready.');
          setTimeout(() => { overlay.close(); }, 600);
        }
        return snapshot;
      })
      .catch((err) => {
        const message = String(err?.message || err || 'Refresh failed');
        pushLog(`Error: home n/a ${message}`);
        if(overlay && overlay.isOpen()){
          overlay.setStatus('Load finished with errors');
          // Leave overlay open so user can see the error, but make Cancel visible
        }
        setError(message);
        throw err;
      })
      .finally(() => {
        _loadInFlight = null;
        setRefreshingBadge(false);
      });

    return _loadInFlight;
  }

  /* ── Boot Choice: show modal on fresh session, otherwise use cached data ── */
  const bootModal = window.BenTradeBootChoiceModal;
  const hadCached = cacheStore.renderCachedImmediately();
  if(!hadCached){
    renderFallbackBlank();
  }

  if(bootModal && !bootModal.alreadyChosen()){
    /* First visit this session — present the boot choice before any loading */
    const bootUI = bootModal.create(scope);
    bootUI.show().then((choice) => {
      bootUI.destroy();
      if(choice === 'full'){
        /* Full App Refresh: home data + scanners + opportunities */
        runFullAppRefresh().catch((err) => {
          setScanError(String(err?.message || err || 'Full App Refresh failed'));
        });
      } else {
        /* Home Dashboard Refresh only — no scanners */
        runLoadSequence({ force: true, showOverlay: true, homeOnly: true }).catch(() => {
          bindRetry();
        });
      }
      if(!autoRefreshPaused){
        _startRefreshInterval();
      }
    });
  } else {
    /* Already chose this session (SPA re-mount) — silent refresh from cache */
    if(hadCached){
      runLoadSequence({ force: false, showOverlay: false }).catch(() => {
        bindRetry();
      });
    } else {
      runLoadSequence({ force: false, showOverlay: true }).catch(() => {
        bindRetry();
      });
    }
    if(!autoRefreshPaused){
      _startRefreshInterval();
    }
  }

  if(pauseRefreshBtnEl){
    pauseRefreshBtnEl.addEventListener('click', () => {
      toggleAutoRefreshPause();
    });
  }

  if(regimeModelBtnEl){
    regimeModelBtnEl.addEventListener('click', () => {
      runRegimeModelAnalysis();
    });
  }

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

  if(stopQueueBtnEl){
    stopQueueBtnEl.addEventListener('click', () => {
      stopScanQueue();
    });
  }

  resetQueueProgress();

  return function cleanupHome(){
    fullAppRefreshState.stopRequested = true;
    fullAppRefreshState.isRunning = false;
    fullAppRefreshState.runId += 1;
    queueState.stopRequested = true;
    queueState.isRunning = false;
    queueState.runId += 1;
    _clearRefreshInterval();
    _loadInFlight = null;
    activeLoadToken += 1;
    if(overlay){
      overlay.destroy();
    }
    setRefreshingBadge(false);
    cacheStore.setRenderer(null);
  };
};
