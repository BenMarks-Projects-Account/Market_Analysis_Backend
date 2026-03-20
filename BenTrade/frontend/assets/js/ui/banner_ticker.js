/**
 * BenTrade — Banner Ticker Marquee  (v3 — cached snapshot, full-universe)
 *
 * Architecture:
 *   Universe      → /api/stock/ticker-universe   (full app universe, ~210 symbols)
 *   Quote feed    → /api/stock/ticker-snapshot    (5-min server-side cache, batched)
 *   Rotation      → WINDOW_SIZE symbols rendered per cycle; window advances
 *                   by ROTATE_STEP each ROTATION_INTERVAL_MS, cycling through
 *                   the entire universe over time.
 *   DOM           → Two copies of the window for seamless CSS scroll loop.
 *                   Only symbols with valid quote data are rendered.
 *   Styling       → CSS handles fog, glow, and smoked-glass layering.
 *
 * Data refresh cadence:
 *   - Snapshot fetch every 5 min (matches server cache TTL)
 *   - DOM rotation every 30s (rotates window across cached snapshot)
 *   - NO per-rotation API calls — all rendering from cached snapshot
 *
 * Color logic (change vs prevclose):
 *   positive  → green glow
 *   negative  → burnt-red / deep-crimson glow
 *   neutral   → cyan glow
 *
 * Exposed as  window.BenTradeBannerTicker
 */
window.BenTradeBannerTicker = (function(){
  'use strict';

  /* ── Config ─────────────────────────────────────────────────────── */
  var SNAPSHOT_INTERVAL_MS = 5 * 60 * 1000; // re-fetch snapshot every 5 min
  var ROTATION_INTERVAL_MS = 30 * 1000;     // rotate DOM window every 30s
  var SCROLL_DURATION_S    = 270;           // seconds for one full belt loop
  var WINDOW_SIZE          = 50;            // symbols rendered per cycle
  var ROTATE_STEP          = 12;            // advance window by N symbols each rotation

  /* Hardcoded fallback — used only when the universe API is unreachable. */
  var FALLBACK_SYMBOLS = [
    'SPY','QQQ','IWM','DIA',
    'XLF','XLK','XLE','XLY','XLP','XLV','XLI','XLU','XLB','XLRE','XLC',
    'AAPL','MSFT','NVDA','GOOGL','META','AMZN','TSLA','AMD','CRM','AVGO',
    'JPM','BAC','GS','V','MA','UNH','JNJ','LLY','HD','COST','WMT',
    'XOM','CVX','NFLX','DIS','CAT','BA','LMT','NEE','PG','KO'
  ];

  /* ── State ──────────────────────────────────────────────────────── */
  var _fullUniverse  = [];      // complete symbol list from /ticker-universe
  var _quotableSyms  = [];      // symbols that have valid quote data in snapshot
  var _windowOffset  = 0;       // current rotation offset into _quotableSyms
  var _snapshot      = {};      // full quote snapshot keyed by symbol
  var _trackEl       = null;
  var _beltEl        = null;
  var _snapshotTimer = null;
  var _rotationTimer = null;
  var _running       = false;

  /* ── Formatting helpers ─────────────────────────────────────────── */
  function _fmt(n, decimals){
    if(n == null || isNaN(n)) return '—';
    return Number(n).toFixed(decimals == null ? 2 : decimals);
  }
  function _sign(n){
    if(n == null || isNaN(n)) return '';
    return n > 0 ? '+' : '';
  }
  function _cls(n){
    if(n == null || isNaN(n) || n === 0) return 'neutral';
    return n > 0 ? 'positive' : 'negative';
  }

  /* ── API helpers ────────────────────────────────────────────────── */
  function _apiBase(){
    var api = window.BenTradeApi;
    return (api && api._baseUrl) ? api._baseUrl : '';
  }

  /** Fetch the full ticker universe symbol list (called once on init). */
  function _fetchUniverse(){
    return fetch(_apiBase() + '/api/stock/ticker-universe')
      .then(function(r){ return r.json(); })
      .then(function(data){
        if(data && Array.isArray(data.symbols) && data.symbols.length){
          _fullUniverse = data.symbols;
        } else {
          _fullUniverse = FALLBACK_SYMBOLS.slice();
        }
      })
      .catch(function(){
        _fullUniverse = FALLBACK_SYMBOLS.slice();
      });
  }

  /** Fetch the cached ticker snapshot (5-min server-side cache).
   *  Populates _snapshot and rebuilds _quotableSyms (symbols with data). */
  function _fetchSnapshot(){
    return fetch(_apiBase() + '/api/stock/ticker-snapshot')
      .then(function(r){ return r.json(); })
      .then(function(data){
        var quotes = (data && data.quotes) || {};
        _snapshot = quotes;
        _rebuildQuotableList();
      })
      .catch(function(err){
        console.warn('[BANNER_TICKER] snapshot fetch failed', err);
        // Keep existing snapshot — do not clear
      });
  }

  /** Rebuild the list of symbols that have valid, renderable data. */
  function _rebuildQuotableList(){
    var src = _fullUniverse.length ? _fullUniverse : FALLBACK_SYMBOLS;
    var valid = [];
    for(var i = 0; i < src.length; i++){
      var q = _snapshot[src[i]];
      if(q && q.last != null && !isNaN(q.last)){
        valid.push(src[i]);
      }
    }
    _quotableSyms = valid.length ? valid : src;
  }

  /* ── Windowed symbol selection ──────────────────────────────────── */
  function _getWindow(){
    var src = _quotableSyms.length ? _quotableSyms : FALLBACK_SYMBOLS;
    var len = src.length;
    if(len <= WINDOW_SIZE) return src.slice();

    var win = [];
    for(var i = 0; i < WINDOW_SIZE; i++){
      win.push(src[(_windowOffset + i) % len]);
    }
    return win;
  }

  function _advanceWindow(){
    var len = _quotableSyms.length || FALLBACK_SYMBOLS.length;
    _windowOffset = (_windowOffset + ROTATE_STEP) % len;
  }

  /* ── DOM builders ───────────────────────────────────────────────── */
  function _buildItem(sym, q){
    var item = document.createElement('span');
    item.className = 'banner-ticker-item';

    var symEl = document.createElement('span');
    symEl.className = 'banner-ticker-sym';
    symEl.textContent = sym;

    var priceEl = document.createElement('span');
    priceEl.className = 'banner-ticker-price';
    priceEl.textContent = q ? _fmt(q.last) : '—';

    var chgVal = q ? q.change : null;
    var pctVal = q ? q.change_pct : null;
    var direction = _cls(chgVal);

    var chgEl = document.createElement('span');
    chgEl.className = 'banner-ticker-chg ' + direction;
    item.setAttribute('data-dir', direction);

    if(chgVal != null && !isNaN(chgVal)){
      var txt = _sign(chgVal) + _fmt(chgVal);
      if(pctVal != null && !isNaN(pctVal)){
        txt += ' (' + _sign(pctVal) + _fmt(pctVal, 1) + '%)';
      }
      chgEl.textContent = txt;
    } else {
      chgEl.textContent = '';
    }

    item.appendChild(symEl);
    item.appendChild(priceEl);
    item.appendChild(chgEl);
    return item;
  }

  function _buildDot(){
    var d = document.createElement('span');
    d.className = 'banner-ticker-dot';
    return d;
  }

  /* ── Render belt (two copies for seamless CSS scroll loop) ──────── */
  function _render(){
    if(!_beltEl) return;
    var syms = _getWindow();
    if(!syms.length) return;

    var frag = document.createDocumentFragment();
    for(var copy = 0; copy < 2; copy++){
      for(var i = 0; i < syms.length; i++){
        frag.appendChild(_buildItem(syms[i], _snapshot[syms[i]] || null));
        frag.appendChild(_buildDot());
      }
    }
    _beltEl.textContent = '';
    _beltEl.appendChild(frag);

    _trackEl.style.setProperty('--ticker-duration', SCROLL_DURATION_S + 's');
  }

  /* ── Rotation tick (no API call — just advance window + re-render) ─ */
  function _rotate(){
    _advanceWindow();
    _render();
  }

  /* ── Snapshot refresh (fetches from 5-min cache, then re-renders) ── */
  function _refreshSnapshot(){
    _fetchSnapshot().then(function(){
      _render();
    });
  }

  /* ── Init / Destroy ─────────────────────────────────────────────── */
  function init(){
    if(_running) return;
    var titlebar = document.querySelector('.titlebar');
    if(!titlebar){
      console.warn('[BANNER_TICKER] .titlebar not found');
      return;
    }

    _trackEl = document.createElement('div');
    _trackEl.className = 'banner-ticker-track';

    _beltEl = document.createElement('div');
    _beltEl.className = 'banner-ticker-belt';
    _trackEl.appendChild(_beltEl);

    titlebar.insertBefore(_trackEl, titlebar.firstChild);
    _running = true;

    // Render immediately with fallback symbols (no quotes yet)
    _render();

    // 1. Fetch universe, then snapshot, then render with real data
    _fetchUniverse().then(function(){
      return _fetchSnapshot();
    }).then(function(){
      _render();
    });

    // 2. Snapshot refresh every 5 minutes (matches server cache TTL)
    _snapshotTimer = setInterval(_refreshSnapshot, SNAPSHOT_INTERVAL_MS);

    // 3. DOM rotation every 30 seconds (no API call — uses cached snapshot)
    _rotationTimer = setInterval(_rotate, ROTATION_INTERVAL_MS);
  }

  function destroy(){
    _running = false;
    if(_snapshotTimer){ clearInterval(_snapshotTimer); _snapshotTimer = null; }
    if(_rotationTimer){ clearInterval(_rotationTimer); _rotationTimer = null; }
    if(_trackEl && _trackEl.parentNode){
      _trackEl.parentNode.removeChild(_trackEl);
    }
    _trackEl = null;
    _beltEl  = null;
  }

  return { init: init, destroy: destroy, refresh: _refreshSnapshot };
})();

/* Auto-init when DOM is ready */
if(document.readyState === 'loading'){
  document.addEventListener('DOMContentLoaded', function(){ window.BenTradeBannerTicker.init(); });
} else {
  window.BenTradeBannerTicker.init();
}
