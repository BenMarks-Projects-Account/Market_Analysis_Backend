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
   * User preferences (debug flags, watchlist, notes, auto-refresh pause) are preserved.
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
      <div class="boot-choice-modal" role="dialog" aria-modal="true" aria-label="Choose startup mode">
        <div class="boot-choice-header">
          <div class="brand-badge" aria-hidden="true">
            <span class="bt">BT</span>
            <span class="brand-name">BenTrade</span>
          </div>
        </div>
        <div class="boot-choice-subtitle">Choose how to start your session</div>
        <div class="boot-choice-cards">
          <button class="boot-choice-card boot-choice-card--home" type="button" data-choice="home">
            <span class="boot-choice-card-label">Home Dashboard Refresh</span>
            <span class="boot-choice-card-desc">
              Load regime, playbook, market data, and portfolio risk.<br>
              Scanners will <strong>not</strong> run — faster startup.
            </span>
          </button>
          <button class="boot-choice-card boot-choice-card--full" type="button" data-choice="full">
            <span class="boot-choice-card-label">Full App Refresh</span>
            <span class="boot-choice-card-desc">
              Home data <strong>+</strong> all scanners &amp; strategy analysis.<br>
              Populates Opportunity Engine with top 5 picks.
            </span>
          </button>
        </div>
        <div class="boot-choice-footer">
          <span class="boot-choice-hint">This choice is only shown once per session.</span>
        </div>
      </div>
    `;

    host.appendChild(root);

    let _resolve = null;

    function handleClick(e){
      const btn = e.target.closest('[data-choice]');
      if(!btn) return;
      const choice = btn.getAttribute('data-choice');
      if(choice === 'home' || choice === 'full'){
        markChosen();
        close();
        if(typeof _resolve === 'function'){
          _resolve(choice);
          _resolve = null;
        }
      }
    }

    root.addEventListener('click', handleClick);

    function show(){
      root.classList.add('is-open');
      return new Promise(function(resolve){
        _resolve = resolve;
      });
    }

    function close(){
      root.classList.remove('is-open');
    }

    function destroy(){
      root.removeEventListener('click', handleClick);
      root.remove();
    }

    return {
      show,
      close,
      destroy,
      isOpen: () => root.classList.contains('is-open'),
    };
  }

  return { create, alreadyChosen, markChosen, getSessionId, ensureSessionBoundary, clearSessionData };
})();
