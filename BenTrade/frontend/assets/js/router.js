// BenTrade SPA Router (no framework)
(function(){
  const routes = {
    "credit-spread": {
      view: "dashboards/credit-spread.view.html",
      init: () => window.BenTrade?.initCreditSpread?.(document.getElementById('view')),
      title: "Credit Spread Analysis"
    },
    "active-trade": {
      view: "dashboards/active-trade-dashboard.view.html",
      init: () => window.BenTrade?.initPlaceholderDashboard?.({
        title: "Active Trade Dashboard"
      }),
      title: "Active Trade Dashboard"
    },
    "trade-testing": {
      view: "dashboards/trade-testing-workbench.view.html",
      init: () => window.BenTrade?.initPlaceholderDashboard?.({
        title: "Trade Testing Workbench"
      }),
      title: "Trade Testing Workbench"
    },
    "stock-analysis": {
      view: "dashboards/stock-analysis-dashboard.view.html",
      init: () => window.BenTrade?.initPlaceholderDashboard?.({
        title: "Stock Analysis Dashboard"
      }),
      title: "Stock Analysis Dashboard"
    },
    "risk-capital": {
      view: "dashboards/risk-capital-management-dashboard.view.html",
      init: () => window.BenTrade?.initPlaceholderDashboard?.({
        title: "Risk & Capital Management Dashboard"
      }),
      title: "Risk & Capital Management Dashboard"
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
