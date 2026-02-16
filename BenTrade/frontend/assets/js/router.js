// BenTrade SPA Router (no framework)
(function(){
  const routes = {
    "credit-spread": {
      view: "dashboards/credit-spread.view.html",
      init: () => (window.BenTradePages?.initCreditSpread || window.BenTrade?.initCreditSpread)?.(document.getElementById('view')),
      title: "Credit Spread Analysis"
    },
    "active-trade": {
      view: "dashboards/active_trades.html",
      init: () => window.BenTradePages?.initActiveTrades?.(document.getElementById('view')),
      title: "Active Trade Dashboard"
    },
    "trade-testing": {
      view: "dashboards/trade_workbench.html",
      init: () => window.BenTradePages?.initTradeWorkbench?.(document.getElementById('view')),
      title: "Trade Testing Workbench"
    },
    "stock-analysis": {
      view: "dashboards/stock_analysis.html",
      init: () => window.BenTradePages?.initStockAnalysis?.(document.getElementById('view')),
      title: "Stock Analysis Dashboard"
    },
    "risk-capital": {
      view: "dashboards/risk_capital.html",
      init: () => window.BenTradePages?.initRiskCapital?.(document.getElementById('view')),
      title: "Risk & Capital Management Dashboard"
    },
    "portfolio-risk": {
      view: "dashboards/portfolio_risk.html",
      init: () => window.BenTradePages?.initPortfolioRisk?.(document.getElementById('view')),
      title: "Portfolio Risk Matrix"
    },
    "trade-lifecycle": {
      view: "dashboards/trade_lifecycle.html",
      init: () => window.BenTradePages?.initTradeLifecycle?.(document.getElementById('view')),
      title: "Trade Lifecycle"
    },
    "strategy-analytics": {
      view: "dashboards/strategy_analytics.html",
      init: () => window.BenTradePages?.initStrategyAnalytics?.(document.getElementById('view')),
      title: "Strategy Analytics"
    }
  };

  function setHeroSubtitle(text){
    const subtitleEl = document.querySelector('.hero-subtitle');
    if(subtitleEl) subtitleEl.textContent = text || 'BenTrade Dashboard';
  }

  function setActive(route){
    document.querySelectorAll("[data-route]").forEach(a=>{
      a.classList.toggle("active", a.getAttribute("data-route")===route);
    });
  }

  async function loadView(routeKey){
    const r = routes[routeKey] || routes["credit-spread"];
    const viewEl = document.getElementById("view");
    if(!viewEl) return;

    // reset view
    viewEl.innerHTML = '<div class="loading">Loading…</div>';

    const res = await fetch(r.view, { cache: "no-store" });
    const html = await res.text();
    viewEl.innerHTML = html;

    setHeroSubtitle(r.title);
    try{ r.init && r.init(); } catch(e){ console.error(e); }
    try{ window.attachMetricTooltips && window.attachMetricTooltips(viewEl); } catch(e){ console.error(e); }
    setActive(routeKey in routes ? routeKey : "credit-spread");
  }

  function routeFromHash(){
    const hash = location.hash || "#/credit-spread";
    return (hash.split("/")[1] || "credit-spread").trim();
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
