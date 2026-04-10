window.BenTradeUI = window.BenTradeUI || {};

window.BenTradeUI.Tooltip = (function(){
  const state = {
    el: null,
    activeTarget: null,
    observer: null,
  };

  const touchDevice = (typeof window !== 'undefined')
    ? (('ontouchstart' in window) || (navigator.maxTouchPoints > 0))
    : false;

  const LABEL_FALLBACK_MAP = {
    'ema20': 'ema_20',
    'ema 20': 'ema_20',
    'sma20': 'sma_20',
    'sma 20': 'sma_20',
    'sma50': 'sma_50',
    'sma 50': 'sma_50',
    'rsi14': 'rsi_14',
    'rsi(14)': 'rsi_14',
    'rsi 14': 'rsi_14',
    'realized vol': 'realized_vol_20d',
    'rv (20d)': 'realized_vol_20d',
    'rv20': 'realized_vol_20d',
    'atm iv': 'iv',
    'implied vol': 'iv',
    'expected move': 'expected_move_1w',
    'iv/rv': 'iv_rv_ratio',
    'ivrv': 'iv_rv_ratio',
    'pop': 'pop',
    'probability': 'pop',
    'ev': 'ev',
    'expected value': 'ev',
    'ev/r': 'ev_to_risk',
    'ev/risk': 'ev_to_risk',
    'return on risk': 'return_on_risk',
    'ror': 'return_on_risk',
    'break even': 'break_even',
    'max profit': 'max_profit',
    'max loss': 'max_loss',
    'credit': 'credit',
    'credit received': 'credit',
    'net credit': 'credit',
    'spread width': 'spread_width',
    'width': 'spread_width',
    'open interest': 'open_interest',
    'oi': 'open_interest',
    'volume': 'volume',
    'vol': 'volume',
    'delta': 'delta',
    'gamma': 'gamma',
    'theta': 'theta',
    'vega': 'vega',
    'net delta': 'delta',
    'net theta': 'theta',
    'net vega': 'vega',
    'dte': 'dte',
    'days to expiration': 'dte',
    'mark': 'mark',
    'unrealized p&l': 'unrealized_pnl',
    'p&l %': 'unrealized_pnl_pct',
    'kelly fraction': 'kelly_fraction',
    'trade quality score': 'trade_quality_score',
    'composite score': 'composite_score',
    'rank score': 'rank_score',
    'iv rank': 'iv_rank',
    'short strike z': 'short_strike_z',
    'bid-ask %': 'bid_ask_spread_pct',
    'bid/ask spread %': 'bid_ask_spread_pct',
    'strike dist %': 'strike_distance_pct',
    'strike distance %': 'strike_distance_pct',
    'risk remaining': 'risk_remaining',
    'estimated risk': 'estimated_risk',
    'win rate': 'win_rate',
    'total p&l': 'total_pnl',
    'avg p&l': 'avg_pnl',
    'max drawdown': 'max_drawdown',
    'trend': 'trend_score',
    'trend score': 'trend_score',
    'momentum': 'momentum_score',
    'momentum score': 'momentum_score',
    'pullback': 'pullback_score',
    'pullback score': 'pullback_score',
    'catalyst': 'catalyst_score',
    'catalyst score': 'catalyst_score',
    'volatility': 'volatility_score',
    'volatility score': 'volatility_score',
    'ema-20': 'ema_20',
    'sma-50': 'sma_50',
    // ── Home dashboard / Macro KPI labels ──
    'spy': 'spy_price',
    'spy price': 'spy_price',
    'vix': 'vix_level',
    'vix level': 'vix_level',
    '10y yield': 'ten_year_yield',
    '10y': 'ten_year_yield',
    'ten year yield': 'ten_year_yield',
    'fed funds': 'fed_funds',
    'cpi yoy': 'cpi_yoy',
    'cpi': 'cpi_yoy',
    'capital at risk': 'capital_at_risk',
    'risk utilization': 'risk_utilization',
    'total risk used': 'total_risk_used',
    'max trade %': 'max_trade_pct',
    'max symbol %': 'max_symbol_pct',
    'open trades': 'open_trades',
    'avg open': 'avg_open',
    'average open': 'avg_open',
    // ── Scanner KPIs ──
    'candidates': 'candidates',
    'universe': 'universe',
    'last scan': 'lastScan',
    'lastscan': 'lastScan',
    'data status': 'dataStatus',
    'datastatus': 'dataStatus',
    // ── Data Health / Admin ──
    'provider status': 'data_provider_status',
    'data staleness': 'data_staleness',
    'api latency': 'api_latency',
    // ── Active Trades ──
    'source': 'trade_source',
    'mode': 'trade_mode',
    // ── Stock Analysis ──
    'change': 'price_change',
    'change %': 'price_change_pct',
    'range high': 'range_high',
    'range low': 'range_low',
    // ── Trade Lifecycle ──
    'days held': 'days_held',
    'profit target %': 'profit_target_pct',
    'stop loss %': 'stop_loss_pct',
    // ── Strategy-specific labels (Iron Condor) ──
    'theta capture': 'theta_capture',
    'symmetry': 'symmetry_score',
    'em ratio': 'expected_move_ratio',
    'tail risk': 'tail_risk_score',
    // ── Strategy-specific labels (Butterfly) ──
    'peak profit': 'peak_profit_at_center',
    'prob touch center': 'probability_of_touch_center',
    'cost efficiency': 'cost_efficiency',
    'payoff slope': 'payoff_slope',
    'gamma peak': 'gamma_peak_score',
    // ── Strategy-specific labels (Calendar) ──
    'iv term structure': 'iv_term_structure_score',
    'vega exposure': 'vega_exposure',
    'theta structure': 'theta_structure',
    'move risk': 'move_risk_score',
    // ── Strategy-specific labels (Income / CSP) ──
    'annualised yield': 'annualized_yield_on_collateral',
    'annualized yield': 'annualized_yield_on_collateral',
    'premium / day': 'premium_per_day',
    'premium/day': 'premium_per_day',
    'downside buffer': 'downside_buffer',
    'assignment risk': 'assignment_risk_score',
    // ── Strategy-specific labels (Debit Spread) ──
    'conviction': 'conviction_score',
    // ── SHARED metrics ──
    'liquidity': 'liquidity_score',
    'iv / rv ratio': 'iv_rv_ratio',
    'ev / risk': 'ev_to_risk',
    // ── Stock scanner labels (pullback swing) ──
    'reset': 'reset_score',
    'pb from 20d high': 'pullback_from_20d_high',
    'dist to sma-20': 'distance_to_sma20',
    // ── Stock scanner labels (momentum breakout) ──
    'breakout': 'breakout_score',
    'base': 'base_quality_score',
    '55d high prox': 'breakout_proximity_55',
    'vol spike': 'vol_spike_ratio',
    'compression': 'compression_score',
    'dist sma-20': 'dist_sma20',
    // ── Stock scanner labels (mean reversion) ──
    'oversold': 'oversold_score',
    'stabilize': 'stabilization_score',
    'room': 'room_score',
    'rsi 2': 'rsi2',
    'z-score 20d': 'zscore_20',
    'dd from 20d hi': 'drawdown_20',
    // ── Stock scanner labels (volatility expansion) ──
    'expansion': 'expansion_score',
    'compress': 'compression_score',
    'confirm': 'confirmation_score',
    'risk': 'risk_score',
    'atr ratio': 'atr_ratio_10',
    'rv ratio': 'rv_ratio',
    'bb width %ile': 'bb_width_percentile_180',
    'atr %': 'atr_pct',
    // ── Contextual / Page labels ──
    'regime': 'regime',
    'total': 'total_active_trades',
    'expiration': 'expiration',
    'symbol': 'symbol',
    'composite': 'composite_score',
    'net credit / debit': 'net_credit',
    'thesis': 'thesis',
    // ── Session Stats ──
    'total candidates': 'total_candidates',
    'accepted trades/ideas': 'accepted_trades',
    'rejected': 'rejected_count',
    'acceptance rate': 'acceptance_rate',
    'best score': 'best_score',
    'avg quality score': 'avg_quality_score',
    'avg return on risk': 'avg_return_on_risk',
    'session runs': 'session_runs',
    // ── Dashboard table headers ──
    'strategy': 'strategy_name',
    'trades': 'trade_count',
    // ── Index ticker aliases ──
    'qqq': 'index_price',
    'iwm': 'index_price',
    'dia': 'index_price',
    'xsp': 'index_price',
    'rut': 'index_price',
    'ndx': 'index_price',
  };

  function ensureTooltipEl(){
    if(state.el) return state.el;
    const el = document.createElement('div');
    el.className = 'metric-tooltip';
    el.id = 'btMetricTooltip';
    el.setAttribute('role', 'tooltip');
    el.setAttribute('aria-hidden', 'true');
    // Mount inside overlay-root (inside .shell) so tooltip remains
    // visible when the app enters browser fullscreen.
    var root = (window.BenTradeOverlayRoot && window.BenTradeOverlayRoot.get)
      ? window.BenTradeOverlayRoot.get()
      : document.body;
    root.appendChild(el);
    state.el = el;
    return el;
  }

  function getGlossary(){
    return window.BenTradeMetrics?.glossary || {};
  }

  function normalizeText(s){
    return String(s || '').trim().toLowerCase().replace(/\s+/g, ' ');
  }

  function inferMetricFromLabel(el){
    const text = normalizeText(el?.textContent || '');
    if(!text) return '';
    if(LABEL_FALLBACK_MAP[text]) return LABEL_FALLBACK_MAP[text];
    const compressed = text.replace(/\s+/g, '');
    if(LABEL_FALLBACK_MAP[compressed]) return LABEL_FALLBACK_MAP[compressed];
    return '';
  }

  function buildTooltipHtml(metricId){
    const item = getGlossary()[metricId];
    if(!item) return '';
    const lines = [];
    lines.push(`<div class="metric-tooltip-title">${item.label || metricId}</div>`);
    if(item.short) lines.push(`<div class="metric-tooltip-line">${item.short}</div>`);
    if(item.formula) lines.push(`<div class="metric-tooltip-sub">Formula: ${item.formula}</div>`);
    if(item.why) lines.push(`<div class="metric-tooltip-sub">Why: ${item.why}</div>`);
    return lines.join('');
  }

  function positionTooltip(target){
    if(!state.el || !target) return;
    const rect = target.getBoundingClientRect();
    const ttRect = state.el.getBoundingClientRect();
    const gap = 10;

    let left = rect.left;
    let top = rect.bottom + gap;

    if(left + ttRect.width > window.innerWidth - 8){
      left = Math.max(8, window.innerWidth - ttRect.width - 8);
    }

    if(top + ttRect.height > window.innerHeight - 8){
      top = rect.top - ttRect.height - gap;
    }

    if(top < 8){
      top = Math.max(8, rect.bottom + gap);
    }

    state.el.style.left = `${Math.round(left)}px`;
    state.el.style.top = `${Math.round(top)}px`;
  }

  function hideTooltip(){
    if(!state.el) return;
    state.el.classList.remove('is-open');
    state.el.setAttribute('aria-hidden', 'true');
    // Move off-screen so hidden tooltip cannot block pointer events
    state.el.style.left = '-9999px';
    state.el.style.top = '-9999px';
    state.el.innerHTML = '';
    if(state.activeTarget){
      state.activeTarget.removeAttribute('aria-describedby');
    }
    state.activeTarget = null;
  }

  function showTooltip(target){
    if(!target) return;
    const metricId = String(target.getAttribute('data-metric') || '').trim();
    if(!metricId) return;

    const html = buildTooltipHtml(metricId);
    if(!html) return;

    const el = ensureTooltipEl();
    el.innerHTML = html;
    el.classList.add('is-open');
    el.setAttribute('aria-hidden', 'false');
    target.setAttribute('aria-describedby', 'btMetricTooltip');
    state.activeTarget = target;
    positionTooltip(target);

    if(window.__BEN_DEBUG_OVERLAYS){
      console.debug('[BenTrade:overlay] showTooltip', metricId,
        'parent:', el.parentElement?.id || el.parentElement?.tagName,
        'fullscreenElement:', document.fullscreenElement?.className || null);
    }
  }

  function isBound(el){
    return el?.dataset?.metricBound === '1';
  }

  function makeFocusable(el){
    if(!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
    if(!el.hasAttribute('role')) el.setAttribute('role', 'button');
    if(!el.hasAttribute('aria-label')){
      const txt = String(el.textContent || '').trim();
      if(txt) el.setAttribute('aria-label', `${txt} metric definition`);
    }
  }

  function injectTouchToggle(el){
    if(!touchDevice || !el || el.querySelector('.metric-tip-toggle')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'metric-tip-toggle';
    btn.setAttribute('aria-label', 'Show metric definition');
    btn.textContent = '?';
    btn.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const openForSame = state.activeTarget === el && state.el?.classList.contains('is-open');
      if(openForSame){
        hideTooltip();
      }else{
        showTooltip(el);
      }
    });
    el.appendChild(btn);
  }

  /* ── Dev-only missing-tooltip instrumentation ─────────────────── */
  // Enabled when ?debug or ?debugTooltips is in the URL.
  // Tracks every label element that the tooltip system skips
  // and logs the gap so developers can add the missing entry.
  var _debugTooltips = /[?&](debug|debugTooltips)\b/.test(location.search);
  var _missingSet = new Set();
  if(_debugTooltips){
    window.__BEN_TIPS_MISSING = _missingSet;
  }

  function _logMissing(el, reason){
    if(!_debugTooltips) return;
    var text = normalizeText(el?.textContent || '');
    if(!text || text.length > 60) return; // skip long content / not a label
    var metric = String(el.getAttribute('data-metric') || '').trim();
    var key = metric || text;
    if(_missingSet.has(key)) return;
    _missingSet.add(key);
    var route = location.hash || '/';
    var container = el.closest('[class]')?.className?.split(' ')[0] || '(root)';
    var suggested = (metric || text).replace(/[\s/]+/g, '_').replace(/[^a-z0-9_]/gi, '').toLowerCase();
    console.warn(
      '[BenTrade:tooltip] MISSING tooltip — label: "' + text + '"'
      + (metric ? ', data-metric: "' + metric + '"' : '')
      + ', route: ' + route
      + ', container: .' + container
      + ', suggested key: "' + suggested + '"'
    );
  }

  function bindTarget(el){
    if(!el || isBound(el)) return;

    if(!el.getAttribute('data-metric')){
      const inferred = inferMetricFromLabel(el);
      if(inferred) el.setAttribute('data-metric', inferred);
    }

    const metricId = String(el.getAttribute('data-metric') || '').trim();
    if(!metricId || !getGlossary()[metricId]){
      _logMissing(el, metricId ? 'no glossary entry for "' + metricId + '"' : 'no metric id resolved');
      return;
    }

    makeFocusable(el);
    el.dataset.metricBound = '1';

    el.addEventListener('mouseenter', () => showTooltip(el));
    el.addEventListener('mouseleave', () => hideTooltip());
    el.addEventListener('focus', () => showTooltip(el));
    el.addEventListener('blur', () => hideTooltip());
    el.addEventListener('keydown', (event) => {
      if(event.key === 'Escape'){
        hideTooltip();
        return;
      }
      if(event.key === 'Enter' || event.key === ' '){
        event.preventDefault();
        showTooltip(el);
      }
    });

    injectTouchToggle(el);
  }

  function bindAll(rootEl){
    const root = rootEl || document;
    root.querySelectorAll('[data-metric], .metric-label, .statLabel, .detail-label, th[data-metric]').forEach(el => {
      bindTarget(el);
    });
  }

  function attachMetricTooltips(rootEl){
    bindAll(rootEl || document);

    if(!state.observer){
      state.observer = new MutationObserver((mutations) => {
        for(const mutation of mutations){
          mutation.addedNodes.forEach((node) => {
            if(!(node instanceof Element)) return;
            if(node.matches('[data-metric], .metric-label, .statLabel, .detail-label, th')){
              bindTarget(node);
            }
            bindAll(node);
          });
        }
      });
      state.observer.observe(document.body, { childList: true, subtree: true });
    }

    if(!window.__btMetricTooltipGlobalBound){
      window.__btMetricTooltipGlobalBound = true;
      window.addEventListener('scroll', () => {
        if(state.activeTarget && state.el?.classList.contains('is-open')) positionTooltip(state.activeTarget);
      }, true);
      window.addEventListener('resize', () => {
        if(state.activeTarget && state.el?.classList.contains('is-open')) positionTooltip(state.activeTarget);
      });
      document.addEventListener('keydown', (event) => {
        if(event.key === 'Escape') hideTooltip();
      });
      document.addEventListener('click', (event) => {
        if(!state.el) return;
        const target = event.target;
        if(!(target instanceof Element)) return;
        if(state.el.contains(target)) return;
        if(target.closest('[data-metric], .metric-label, .statLabel, .detail-label, th')) return;
        hideTooltip();
      });
    }
  }

  return {
    attachMetricTooltips,
    hideTooltip,
  };
})();

window.attachMetricTooltips = function(rootEl){
  window.BenTradeUI?.Tooltip?.attachMetricTooltips?.(rootEl || document);
};
