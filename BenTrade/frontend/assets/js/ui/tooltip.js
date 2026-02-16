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
    'delta': 'delta',
    'gamma': 'gamma',
    'theta': 'theta',
    'vega': 'vega',
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
    'strike dist %': 'strike_distance_pct',
    'risk remaining': 'risk_remaining',
    'estimated risk': 'estimated_risk',
    'win rate': 'win_rate',
    'total p&l': 'total_pnl',
    'avg p&l': 'avg_pnl',
    'max drawdown': 'max_drawdown'
  };

  function ensureTooltipEl(){
    if(state.el) return state.el;
    const el = document.createElement('div');
    el.className = 'metric-tooltip';
    el.id = 'btMetricTooltip';
    el.setAttribute('role', 'tooltip');
    el.setAttribute('aria-hidden', 'true');
    document.body.appendChild(el);
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

  function bindTarget(el){
    if(!el || isBound(el)) return;

    if(!el.getAttribute('data-metric')){
      const inferred = inferMetricFromLabel(el);
      if(inferred) el.setAttribute('data-metric', inferred);
    }

    const metricId = String(el.getAttribute('data-metric') || '').trim();
    if(!metricId || !getGlossary()[metricId]) return;

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
    root.querySelectorAll('[data-metric], .metric-label, .statLabel, .detail-label, th').forEach(bindTarget);
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
