// BenTrade SPA Router (no framework)
(function(){
  const ROUTE_HISTORY_KEY = 'bentrade_route_history_v1';
  const routeHistoryState = {
    stack: [],
    index: -1,
    pendingTarget: null,
  };

  function normalizeHashForHistory(hash){
    const raw = String(hash || '').trim();
    if(!raw) return '#/home';
    if(raw.startsWith('#/')) return raw;
    if(raw.startsWith('#')) return `#/${raw.slice(1)}`;
    return `#/${raw}`;
  }

  function loadRouteHistory(){
    try{
      const raw = sessionStorage.getItem(ROUTE_HISTORY_KEY);
      const parsed = raw ? JSON.parse(raw) : null;
      const stack = Array.isArray(parsed?.stack)
        ? parsed.stack.map((row) => normalizeHashForHistory(row)).filter(Boolean)
        : [];
      const index = Number(parsed?.index);
      routeHistoryState.stack = stack;
      routeHistoryState.index = Number.isFinite(index) ? Math.max(-1, Math.min(index, stack.length - 1)) : (stack.length - 1);
    }catch(_err){
      routeHistoryState.stack = [];
      routeHistoryState.index = -1;
    }
  }

  function persistRouteHistory(){
    try{
      sessionStorage.setItem(ROUTE_HISTORY_KEY, JSON.stringify({
        stack: routeHistoryState.stack,
        index: routeHistoryState.index,
        last_route: routeHistoryState.stack[routeHistoryState.index] || null,
      }));
    }catch(_err){
    }
  }

  function updateHeaderNavButtons(){
    const backBtn = document.getElementById('headerBackBtn');
    const forwardBtn = document.getElementById('headerForwardBtn');
    if(backBtn){
      const hasSpaBack = routeHistoryState.index > 0;
      const hasBrowserBack = window.history.length > 1;
      backBtn.disabled = !(hasSpaBack || hasBrowserBack);
    }
    if(forwardBtn){
      const hasSpaForward = routeHistoryState.index >= 0 && routeHistoryState.index < (routeHistoryState.stack.length - 1);
      forwardBtn.disabled = !hasSpaForward;
    }
  }

  function commitRouteInHistory(hash){
    const normalized = normalizeHashForHistory(hash);
    if(routeHistoryState.pendingTarget && routeHistoryState.pendingTarget === normalized){
      routeHistoryState.pendingTarget = null;
      persistRouteHistory();
      updateHeaderNavButtons();
      return;
    }

    const current = routeHistoryState.stack[routeHistoryState.index] || null;
    if(current === normalized){
      persistRouteHistory();
      updateHeaderNavButtons();
      return;
    }

    const nextStack = routeHistoryState.stack.slice(0, Math.max(0, routeHistoryState.index + 1));
    nextStack.push(normalized);
    routeHistoryState.stack = nextStack;
    routeHistoryState.index = nextStack.length - 1;
    persistRouteHistory();
    updateHeaderNavButtons();
  }

  function goBack(){
    if(routeHistoryState.index > 0){
      routeHistoryState.index -= 1;
      const target = routeHistoryState.stack[routeHistoryState.index];
      routeHistoryState.pendingTarget = target;
      persistRouteHistory();
      updateHeaderNavButtons();
      location.hash = target;
      return;
    }
    window.history.back();
    updateHeaderNavButtons();
  }

  function goForward(){
    if(routeHistoryState.index >= 0 && routeHistoryState.index < (routeHistoryState.stack.length - 1)){
      routeHistoryState.index += 1;
      const target = routeHistoryState.stack[routeHistoryState.index];
      routeHistoryState.pendingTarget = target;
      persistRouteHistory();
      updateHeaderNavButtons();
      location.hash = target;
      return;
    }
    window.history.forward();
    updateHeaderNavButtons();
  }

  function goHome(){
    location.hash = '#/home';
  }

  function initHeaderNavControls(){
    const backBtn = document.getElementById('headerBackBtn');
    const forwardBtn = document.getElementById('headerForwardBtn');
    const homeBtn = document.getElementById('headerHomeBtn');

    if(backBtn && backBtn.dataset.bound !== '1'){
      backBtn.dataset.bound = '1';
      backBtn.addEventListener('click', goBack);
    }
    if(forwardBtn && forwardBtn.dataset.bound !== '1'){
      forwardBtn.dataset.bound = '1';
      forwardBtn.addEventListener('click', goForward);
    }
    if(homeBtn && homeBtn.dataset.bound !== '1'){
      homeBtn.dataset.bound = '1';
      homeBtn.addEventListener('click', goHome);
    }

    window.BenTradeRouterHistory = {
      canGoBack: () => routeHistoryState.index > 0 || window.history.length > 1,
      canGoForward: () => routeHistoryState.index >= 0 && routeHistoryState.index < (routeHistoryState.stack.length - 1),
      goBack,
      goForward,
      goHome,
      getState: () => ({ stack: routeHistoryState.stack.slice(), index: routeHistoryState.index }),
    };

    updateHeaderNavButtons();
  }

  function initFullscreenToggle(){
    const btn = document.getElementById('fullscreenToggleBtn');
    if(!btn || btn.dataset.bound === '1') return;

    const target = document.querySelector('.shell') || document.documentElement;

    const setLabel = () => {
      btn.textContent = document.fullscreenElement ? 'Exit Fullscreen' : 'Fullscreen';
    };

    btn.dataset.bound = '1';
    btn.addEventListener('click', async () => {
      try{
        if(!document.fullscreenElement){
          await target.requestFullscreen();
        } else {
          await document.exitFullscreen();
        }
      }catch(_err){
      }
    });

    document.addEventListener('fullscreenchange', setLabel);
    setLabel();
  }

  loadRouteHistory();
  initHeaderNavControls();
  initFullscreenToggle();

  const routeMeta = {
    "home": { title: "Home Dashboard", group: "Home", subgroup: "Market Overview", description: "Command center" },
    "news-sentiment": { title: "News & Sentiment", group: "Home", subgroup: "Market Intelligence", description: "Macro & headline intelligence" },
    "trade-building-pipeline": { title: "Trade Building Pipeline", group: "Home", subgroup: "Workflow", description: "Execution-focused pipeline DAG" },
    "credit-spread": { title: "Credit Spread Analysis", group: "Analysis", subgroup: "Options", description: "Credit Spreads" },
    "strategy-iron-condor": { title: "Strategy Dashboard • Iron Condor", group: "Analysis", subgroup: "Options → Premium Selling", description: "Iron Condor" },
    "iron-condor": { title: "Iron Condor Analysis", group: "Analysis", subgroup: "Options → Premium Selling", description: "Iron Condor" },
    "debit-spreads": { title: "Debit Spread Analysis", group: "Analysis", subgroup: "Options → Directional", description: "Long premium" },
    "butterflies": { title: "Butterfly Analysis", group: "Analysis", subgroup: "Options → Directional/Neutral", description: "Pin risk" },
    "calendar": { title: "Calendar Spread Analysis", group: "Analysis", subgroup: "Options", description: "Vol/term structure" },
    "income": { title: "Income Strategies", group: "Analysis", subgroup: "Options", description: "Income" },
    "active-trade": { title: "Active Trade Dashboard", group: "Trading", subgroup: "Execution & Monitoring", description: "Broker positions/orders" },
    "trade-testing": { title: "Trade Testing Workbench", group: "Trading", subgroup: "Execution & Monitoring", description: "What-if lab + scenarios" },
    "trade-management": { title: "Trade Management Center", group: "Trading", subgroup: "Execution & Monitoring", description: "Candidate review + execution" },
    "stock-analysis": { title: "Stock Analysis Dashboard", group: "Analysis", subgroup: "Equities", description: "Stock analysis" },
    "stock-scanner": { title: "Stock Scanner (Deprecated)", group: "Analysis", subgroup: "Equities", description: "Legacy generic scanner" },
    "stocks/pullback-swing": { title: "Pullback Swing — Stock Scanner", group: "Stock Strategies", subgroup: "Equities → Swing", description: "Dip buys in trend" },
    "stocks/momentum-breakout": { title: "Momentum Breakout — Stock Scanner", group: "Stock Strategies", subgroup: "Equities → Momentum", description: "Breakout entries" },
    "stocks/mean-reversion": { title: "Mean Reversion — Stock Scanner", group: "Stock Strategies", subgroup: "Equities → Reversion", description: "Oversold bounces" },
    "stocks/volatility-expansion": { title: "Volatility Expansion — Stock Scanner", group: "Stock Strategies", subgroup: "Equities → Volatility", description: "IV spike entries" },
    "risk-capital": { title: "Risk & Capital Management Dashboard", group: "Risk", subgroup: "Institutional controls", description: "Policies + limits" },
    "portfolio-risk": { title: "Portfolio Risk Matrix", group: "Risk", subgroup: "Institutional controls", description: "Greeks + scenarios" },
    "trade-lifecycle": { title: "Trade Lifecycle", group: "Lifecycle", subgroup: "Process & journaling", description: "States + history" },
    "strategy-analytics": { title: "Strategy Analytics", group: "Lifecycle", subgroup: "Process & journaling", description: "Performance + attribution" },
    "admin-data-health": { title: "Data Health", group: "Admin", subgroup: "Operations", description: "Provider + validation health" },
    "admin/data-workbench": { title: "Data Workbench", group: "Admin", subgroup: "Operations", description: "Trade JSON + card inspection" },
    "admin/tooltip-test": { title: "Tooltip Test", group: "Admin", subgroup: "Dev", description: "Tooltip regression sandbox" },
    "admin/pipeline-monitor": { title: "Pipeline Monitor", group: "Admin", subgroup: "Operations", description: "Pipeline run inspector" },
    "market/breadth": { title: "Breadth & Participation", group: "Market Picture", subgroup: "Internals", description: "Market breadth & participation depth" },
    "market/volatility": { title: "Volatility & Options Structure", group: "Market Picture", subgroup: "Volatility", description: "Vol regime & options posture" },
    "market/cross-asset": { title: "Cross-Asset / Macro", group: "Market Picture", subgroup: "Macro", description: "Cross-asset confirmation signals" },
    "market/flows": { title: "Flows & Positioning", group: "Market Picture", subgroup: "Flows", description: "Positioning & flow dynamics" },
    "market/liquidity": { title: "Liquidity & Financial Conditions", group: "Market Picture", subgroup: "Liquidity", description: "Financial conditions & policy" },
  };

  const routes = {
    "home": {
      view: "dashboards/home.html",
      init: () => window.BenTradePages?.initHome?.(document.getElementById('view')),
      title: routeMeta["home"].title
    },
    "news-sentiment": {
      view: "dashboards/news_sentiment.html",
      init: () => window.BenTradePages?.initNewsSentiment?.(document.getElementById('view')),
      title: routeMeta["news-sentiment"].title
    },
    "trade-building-pipeline": {
      view: "dashboards/trade_building_pipeline.html",
      init: () => window.BenTradePages?.initTradeBuildingPipeline?.(document.getElementById('view')),
      title: routeMeta["trade-building-pipeline"].title
    },
    "credit-spread": {
      view: "dashboards/credit-spread.view.html",
      init: () => window.BenTradePages?.initCreditSpreads?.(document.getElementById('view')),
      title: routeMeta["credit-spread"].title
    },
    "strategy-iron-condor": {
      view: "dashboards/credit-spread.view.html",
      init: () => window.BenTradePages?.initStrategyIronCondor?.(document.getElementById('view')),
      title: routeMeta["strategy-iron-condor"].title
    },
    "iron-condor": {
      view: "dashboards/credit-spread.view.html",
      init: () => window.BenTradePages?.initStrategyIronCondor?.(document.getElementById('view')),
      title: routeMeta["iron-condor"].title
    },
    "debit-spreads": {
      view: "dashboards/credit-spread.view.html",
      init: () => window.BenTradePages?.initDebitSpreads?.(document.getElementById('view')),
      title: routeMeta["debit-spreads"].title
    },
    "butterflies": {
      view: "dashboards/credit-spread.view.html",
      init: () => window.BenTradePages?.initButterflies?.(document.getElementById('view')),
      title: routeMeta["butterflies"].title
    },
    "calendar": {
      view: "dashboards/credit-spread.view.html",
      init: () => window.BenTradePages?.initCalendar?.(document.getElementById('view')),
      title: routeMeta["calendar"].title
    },
    "income": {
      view: "dashboards/credit-spread.view.html",
      init: () => window.BenTradePages?.initIncome?.(document.getElementById('view')),
      title: routeMeta["income"].title
    },
    "active-trade": {
      view: "dashboards/active_trades.html",
      init: () => window.BenTradePages?.initActiveTrades?.(document.getElementById('view')),
      title: routeMeta["active-trade"].title
    },
    "trade-testing": {
      view: "dashboards/trade_workbench.html",
      init: () => window.BenTradePages?.initTradeWorkbench?.(document.getElementById('view')),
      title: routeMeta["trade-testing"].title
    },
    "trade-management": {
      view: "dashboards/trade_management_center.html",
      init: () => window.BenTradePages?.initTradeManagementCenter?.(document.getElementById('view')),
      title: routeMeta["trade-management"].title
    },
    "stock-analysis": {
      view: "dashboards/stock_analysis.html",
      init: () => window.BenTradePages?.initStockAnalysis?.(document.getElementById('view')),
      title: routeMeta["stock-analysis"].title
    },
    "stock-scanner": {
      view: "dashboards/stock_scanner.html",
      init: () => window.BenTradePages?.initStockScanner?.(document.getElementById('view')),
      title: routeMeta["stock-scanner"].title
    },
    "stocks/pullback-swing": {
      view: "dashboards/stock_strategy.html",
      init: () => window.BenTradePages?.initStockPullbackSwing?.(document.getElementById('view')),
      title: routeMeta["stocks/pullback-swing"].title
    },
    "stocks/momentum-breakout": {
      view: "dashboards/stock_strategy.html",
      init: () => window.BenTradePages?.initStockMomentumBreakout?.(document.getElementById('view')),
      title: routeMeta["stocks/momentum-breakout"].title
    },
    "stocks/mean-reversion": {
      view: "dashboards/stock_strategy.html",
      init: () => window.BenTradePages?.initStockMeanReversion?.(document.getElementById('view')),
      title: routeMeta["stocks/mean-reversion"].title
    },
    "stocks/volatility-expansion": {
      view: "dashboards/stock_strategy.html",
      init: () => window.BenTradePages?.initStockVolatilityExpansion?.(document.getElementById('view')),
      title: routeMeta["stocks/volatility-expansion"].title
    },
    "risk-capital": {
      view: "dashboards/risk_capital.html",
      init: () => window.BenTradePages?.initRiskCapital?.(document.getElementById('view')),
      title: routeMeta["risk-capital"].title
    },
    "portfolio-risk": {
      view: "dashboards/portfolio_risk.html",
      init: () => window.BenTradePages?.initPortfolioRisk?.(document.getElementById('view')),
      title: routeMeta["portfolio-risk"].title
    },
    "trade-lifecycle": {
      view: "dashboards/trade_lifecycle.html",
      init: () => window.BenTradePages?.initTradeLifecycle?.(document.getElementById('view')),
      title: routeMeta["trade-lifecycle"].title
    },
    "strategy-analytics": {
      view: "dashboards/strategy_analytics.html",
      init: () => window.BenTradePages?.initStrategyAnalytics?.(document.getElementById('view')),
      title: routeMeta["strategy-analytics"].title
    },
    "admin-data-health": {
      view: "dashboards/data_health.html",
      init: () => window.BenTradePages?.initDataHealth?.(document.getElementById('view')),
      title: routeMeta["admin-data-health"].title
    },
    "admin/data-workbench": {
      view: "dashboards/admin_data_workbench.html",
      init: () => window.BenTradePages?.initAdminDataWorkbench?.(document.getElementById('view')),
      title: routeMeta["admin/data-workbench"].title
    },
    "admin/tooltip-test": {
      view: "dashboards/tooltip_test.html",
      init: () => {},
      title: routeMeta["admin/tooltip-test"].title
    },
    "admin/pipeline-monitor": {
      view: "dashboards/pipeline_monitor.html",
      init: () => window.BenTradePages?.initPipelineMonitor?.(document.getElementById('view')),
      title: routeMeta["admin/pipeline-monitor"].title
    },
    "market/breadth": {
      view: "dashboards/breadth_participation.html",
      init: () => window.BenTradePages?.initBreadthParticipation?.(document.getElementById('view')),
      title: routeMeta["market/breadth"].title
    },
    "market/volatility": {
      view: "dashboards/volatility_options.html",
      init: () => window.BenTradePages?.initVolatilityOptions?.(document.getElementById('view')),
      title: routeMeta["market/volatility"].title
    },
    "market/cross-asset": {
      view: "dashboards/cross_asset_macro.html",
      init: () => window.BenTradePages?.initCrossAssetMacro?.(document.getElementById('view')),
      title: routeMeta["market/cross-asset"].title
    },
    "market/flows": {
      view: "dashboards/flows_positioning.html",
      init: () => window.BenTradePages?.initFlowsPositioning?.(document.getElementById('view')),
      title: routeMeta["market/flows"].title
    },
    "market/liquidity": {
      view: "dashboards/liquidity_conditions.html",
      init: () => window.BenTradePages?.initLiquidityConditions?.(document.getElementById('view')),
      title: routeMeta["market/liquidity"].title
    }
  };

  function setHeroSubtitle(text){
    const subtitleEl = document.querySelector('.hero-subtitle');
    if(subtitleEl) subtitleEl.textContent = text || 'BenTrade Dashboard';
  }

  function setHeroContext(meta){
    const contextEl = document.getElementById('heroContext');
    if(!contextEl) return;
    if(!meta){
      contextEl.textContent = 'Analysis → Options → Credit Spreads';
      return;
    }
    const group = String(meta.group || '').trim();
    const subgroup = String(meta.subgroup || '').trim();
    const description = String(meta.description || '').trim();
    const parts = [group, subgroup, description].filter(Boolean);
    contextEl.textContent = parts.join(' → ');
  }

  function setActive(route){
    document.querySelectorAll("[data-route]").forEach(a=>{
      a.classList.toggle("active", a.getAttribute("data-route")===route);
    });
  }

  async function loadView(routeKey){
    const r = routes[routeKey] || routes["home"];
    const meta = routeMeta[routeKey] || routeMeta["home"];
    const viewEl = document.getElementById("view");
    if(!viewEl) return;

    // Dismiss any open tooltips before tearing down the old view
    try{ window.BenTradeUI?.Tooltip?.hideTooltip?.(); } catch(_){}
    try{ window.BenTradeBenTooltip?.hide?.(); } catch(_){}

    try{
      if(typeof window.BenTradeActiveViewCleanup === 'function'){
        window.BenTradeActiveViewCleanup();
      }
    }catch(e){
      console.error(e);
    }finally{
      window.BenTradeActiveViewCleanup = null;
    }

    // reset view
    viewEl.innerHTML = '<div class="loading">Loading…</div>';

    const res = await fetch(r.view, { cache: "no-store" });
    const html = await res.text();
    viewEl.innerHTML = html;

    setHeroSubtitle(meta?.title || r.title);
    setHeroContext(meta);
    try{
      const cleanup = r.init && r.init();
      window.BenTradeActiveViewCleanup = (typeof cleanup === 'function') ? cleanup : null;
    } catch(e){ console.error(e); }
    try{
      await window.BenTradeSourceHealthStore?.fetchSourceHealth?.({ force: true });
    } catch(e){
      console.error(e);
    }
    try{ window.attachMetricTooltips && window.attachMetricTooltips(viewEl); } catch(e){ console.error(e); }
    try{ window.BenTradeBenTooltip && window.BenTradeBenTooltip.bindAll(viewEl); } catch(e){ console.error(e); }
    setActive(routeKey in routes ? routeKey : "home");
  }

  function routeFromHash(){
    const hash = location.hash || "#/home";
    if(hash.startsWith('#/')){
      const raw = hash.slice(2);
      const pathOnly = raw.split('?')[0] || 'home';
      return pathOnly.trim() || 'home';
    }
    return (hash.replace(/^#/, '').split('?')[0] || '').trim() || 'home';
  }

  function navigate(){
    const hash = normalizeHashForHistory(location.hash || '#/home');
    commitRouteInHistory(hash);
    loadView(routeFromHash());
  }

  window.addEventListener("hashchange", navigate);

  document.addEventListener("click", (e)=>{
    const a = e.target.closest("[data-route]");
    if(!a) return;
    e.preventDefault();
    location.hash = "#/" + a.getAttribute("data-route");
  });

  navigate();
})();
