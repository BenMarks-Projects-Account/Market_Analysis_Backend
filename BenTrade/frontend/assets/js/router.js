// BenTrade SPA Router (no framework)
(function(){
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

  initFullscreenToggle();

  const routeMeta = {
    "home": { title: "Home Dashboard", group: "Home", subgroup: "Market Overview", description: "Command center" },
    "credit-spread": { title: "Credit Spread Analysis", group: "Analysis", subgroup: "Options", description: "Credit Spreads" },
    "strategy-credit-put": { title: "Strategy Dashboard • Credit Put", group: "Analysis", subgroup: "Options → Credit Spreads", description: "Put wing" },
    "strategy-credit-call": { title: "Strategy Dashboard • Credit Call", group: "Analysis", subgroup: "Options → Credit Spreads", description: "Call wing" },
    "strategy-iron-condor": { title: "Strategy Dashboard • Iron Condor", group: "Analysis", subgroup: "Options → Premium Selling", description: "Iron Condor" },
    "iron-condor": { title: "Iron Condor Analysis", group: "Analysis", subgroup: "Options → Premium Selling", description: "Iron Condor" },
    "debit-spreads": { title: "Debit Spread Analysis", group: "Analysis", subgroup: "Options → Directional", description: "Long premium" },
    "butterflies": { title: "Butterfly Analysis", group: "Analysis", subgroup: "Options → Directional/Neutral", description: "Pin risk" },
    "calendar": { title: "Calendar Spread Analysis", group: "Analysis", subgroup: "Options", description: "Vol/term structure" },
    "income": { title: "Income Strategies", group: "Analysis", subgroup: "Options", description: "Income" },
    "active-trade": { title: "Active Trade Dashboard", group: "Trading", subgroup: "Execution & Monitoring", description: "Broker positions/orders" },
    "trade-testing": { title: "Trade Testing Workbench", group: "Trading", subgroup: "Execution & Monitoring", description: "What-if lab + scenarios" },
    "stock-analysis": { title: "Stock Analysis Dashboard", group: "Analysis", subgroup: "Equities", description: "Stock analysis" },
    "stock-scanner": { title: "Stock Scanner", group: "Analysis", subgroup: "Equities", description: "Auto-ranked stock ideas" },
    "risk-capital": { title: "Risk & Capital Management Dashboard", group: "Risk", subgroup: "Institutional controls", description: "Policies + limits" },
    "portfolio-risk": { title: "Portfolio Risk Matrix", group: "Risk", subgroup: "Institutional controls", description: "Greeks + scenarios" },
    "trade-lifecycle": { title: "Trade Lifecycle", group: "Lifecycle", subgroup: "Process & journaling", description: "States + history" },
    "strategy-analytics": { title: "Strategy Analytics", group: "Lifecycle", subgroup: "Process & journaling", description: "Performance + attribution" },
  };

  const routes = {
    "home": {
      view: "dashboards/home.html",
      init: () => window.BenTradePages?.initHome?.(document.getElementById('view')),
      title: routeMeta["home"].title
    },
    "credit-spread": {
      view: "dashboards/credit-spread.view.html",
      init: () => (window.BenTradePages?.initCreditSpread || window.BenTrade?.initCreditSpread)?.(document.getElementById('view')),
      title: routeMeta["credit-spread"].title
    },
    "strategy-credit-put": {
      view: "dashboards/credit-spread.view.html",
      init: () => window.BenTradePages?.initStrategyCreditPut?.(document.getElementById('view')),
      title: routeMeta["strategy-credit-put"].title
    },
    "strategy-credit-call": {
      view: "dashboards/credit-spread.view.html",
      init: () => window.BenTradePages?.initStrategyCreditCall?.(document.getElementById('view')),
      title: routeMeta["strategy-credit-call"].title
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
    setActive(routeKey in routes ? routeKey : "home");
  }

  function routeFromHash(){
    const hash = location.hash || "#/home";
    if(hash.startsWith('#/')){
      return (hash.split('/')[1] || 'home').trim();
    }
    return hash.replace(/^#/, '').trim() || 'home';
  }

  function navigate(){ loadView(routeFromHash()); }

  window.addEventListener("hashchange", navigate);

  document.addEventListener("click", (e)=>{
    const a = e.target.closest("[data-route]");
    if(!a) return;
    e.preventDefault();
    location.hash = "#/" + a.getAttribute("data-route");
  });

  navigate();
})();
