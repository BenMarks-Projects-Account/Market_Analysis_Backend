/**
 * Boot Choice Modal — shown ONCE per session before any loading begins.
 * Also manages session boundary: generates a sessionId and clears stale
 * session data from localStorage on fresh boot.
 *
 * Usage:
 *   const modal = window.BenTradeBootChoiceModal.create(document.body);
 *   const choice = await modal.show();   // 'home' | 'full'
 *   modal.destroy();
 */
window.BenTradeBootChoiceModal = (function(){
  const SESSION_KEY = 'bentrade_boot_choice_done';
  const SESSION_ID_KEY = 'bentrade_session_id';

  /* ── localStorage keys that hold session-scoped data (must be cleared on fresh boot) ── */
  const SESSION_DATA_KEYS = [
    'bentrade_home_cache_v1',
    'bentrade_session_stats_v1',
    'creditSpreadSelectedReport',
    'creditSpreadSelectedUnderlying',
    'bentrade_scanner_rejected_v1',
    'bentrade_selected_symbol',
    'bentrade_selected_candidate',
    'bentrade_workbench_handoff_v1',
    'workbenchPrefillCandidate',
    'creditSpreadSelectedUnderlying',
  ];

  /** Generate a unique session identifier (timestamp + random suffix). */
  function generateSessionId(){
    const ts = Date.now();
    const rand = Math.random().toString(36).slice(2, 8);
    return `${ts}_${rand}`;
  }

  /** Get the current sessionId (created once per browser session). */
  function getSessionId(){
    let id = sessionStorage.getItem(SESSION_ID_KEY);
    if(!id){
      id = generateSessionId();
      sessionStorage.setItem(SESSION_ID_KEY, id);
    }
    return id;
  }

  /** Returns true if the boot choice was already made this browser session. */
  function alreadyChosen(){
    return sessionStorage.getItem(SESSION_KEY) === '1';
  }

  /** Mark boot choice as done for the current browser session. */
  function markChosen(){
    sessionStorage.setItem(SESSION_KEY, '1');
  }

  /**
   * Clear all session-scoped data from localStorage.
   * Called ONCE on fresh app boot (before any loading begins).
   * User preferences (debug flags, watchlist, notes) are preserved.
   */
  function clearSessionData(){
    SESSION_DATA_KEYS.forEach(function(key){
      try{ localStorage.removeItem(key); }catch(_e){}
    });
  }

  /**
   * Initialise the session boundary. If this is a fresh browser session
   * (no sessionId in sessionStorage), generate one and wipe stale localStorage
   * session data. Returns the sessionId.
   */
  function ensureSessionBoundary(){
    const existing = sessionStorage.getItem(SESSION_ID_KEY);
    if(existing){
      return existing;          // SPA re-mount — session already initialised
    }
    const id = generateSessionId();
    sessionStorage.setItem(SESSION_ID_KEY, id);
    clearSessionData();         // Wipe stale session data from previous run
    return id;
  }

  /* ── Run session boundary immediately on script load ── */
  /* This executes before homeCache, sessionStats, or any other store IIFE,
     because boot_choice_modal.js is loaded first in index.html.
     On a fresh browser session (no sessionId in sessionStorage), this
     clears all stale session data from localStorage so subsequent stores
     initialise from a blank slate. */
  const _sessionId = ensureSessionBoundary();

  function create(hostEl){
    const host = hostEl && hostEl.ownerDocument ? hostEl : document.body;
    const doc = host.ownerDocument || document;

    const root = doc.createElement('div');
    root.className = 'boot-choice-overlay';
    root.innerHTML = `
      <div class="boot-choice-modal" role="dialog" aria-modal="true" aria-label="Welcome to BenTrade">
        <div class="boot-choice-header">
          <div class="brand-badge" aria-hidden="true">
            <span class="bt">BT</span>
            <span class="brand-name">BenTrade</span>
          </div>
        </div>
        <div class="boot-choice-subtitle">Welcome to BenTrade</div>
        <div class="boot-welcome-body">
          <div class="boot-welcome-status">
            <span class="boot-welcome-spinner" aria-hidden="true"></span>
            <span class="boot-welcome-text">BenTrade Data Population Has Begun</span>
          </div>
          <div class="boot-welcome-phases">
            <div class="boot-phase" data-phase="market_data">
              <span class="boot-phase-icon">&#9711;</span>
              <span class="boot-phase-label">Market Data</span>
            </div>
            <div class="boot-phase" data-phase="model_analysis">
              <div class="boot-phase-label">
                <span class="boot-phase-icon">&#9711;</span>
                Model Analysis
              </div>
              <div class="boot-model-engines" id="bootModelEngines"></div>
            </div>
            <div class="boot-phase" data-phase="dashboard">
              <span class="boot-phase-icon">&#9711;</span>
              <span class="boot-phase-label">Dashboard</span>
            </div>
          </div>
        </div>
        <div class="boot-choice-footer">
          <span class="boot-choice-hint">Loading will continue in the background.</span>
        </div>
      </div>
    `;

    host.appendChild(root);

    const phaseEls = {
      market_data: root.querySelector('[data-phase="market_data"]'),
      model_analysis: root.querySelector('[data-phase="model_analysis"]'),
      dashboard: root.querySelector('[data-phase="dashboard"]'),
    };
    const modelEnginesEl = root.querySelector('#bootModelEngines');

    function setPhaseActive(phase){
      Object.entries(phaseEls).forEach(([key, el]) => {
        if(!el) return;
        el.classList.remove('active', 'done');
        const icon = el.querySelector('.boot-phase-icon');
        if(key === phase){
          el.classList.add('active');
          if(icon) icon.textContent = '\u25F7'; // spinning indicator
        }
      });
    }

    function setPhaseDone(phase){
      const el = phaseEls[phase];
      if(!el) return;
      el.classList.remove('active');
      el.classList.add('done');
      const icon = el.querySelector('.boot-phase-icon');
      if(icon) icon.textContent = '\u2713'; // checkmark
    }

    /** Update the per-engine model analysis sub-progress list.
     *  @param {Object<string,string>} progress — e.g. {breadth_participation: "running", ...}
     */
    function setModelProgress(progress){
      if(!modelEnginesEl || !progress) return;
      const ENGINE_LABELS = {
        breadth_participation: 'Breadth',
        volatility_options: 'Volatility',
        cross_asset_macro: 'Cross-Asset',
        flows_positioning: 'Flows',
        liquidity_conditions: 'Liquidity',
        news_sentiment: 'News',
      };
      let html = '';
      for(const [key, label] of Object.entries(ENGINE_LABELS)){
        const st = progress[key] || 'pending';
        const icon = st === 'done' ? '\u2713' : st === 'running' ? '\u25F7' : st === 'failed' ? '\u2717' : '\u00B7';
        html += `<span class="boot-engine-item boot-engine-${st}"><span class="boot-engine-icon">${icon}</span>${label}</span>`;
      }
      modelEnginesEl.innerHTML = html;
    }

    function show(){
      markChosen();
      root.classList.add('is-open');
    }

    function close(){
      root.classList.remove('is-open');
    }

    function destroy(){
      root.remove();
    }

    return {
      show,
      close,
      destroy,
      setPhaseActive,
      setPhaseDone,
      setModelProgress,
      isOpen: () => root.classList.contains('is-open'),
    };
  }

  return { create, alreadyChosen, markChosen, getSessionId, ensureSessionBoundary, clearSessionData };
})();
