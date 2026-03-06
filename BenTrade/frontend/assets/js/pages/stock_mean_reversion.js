/**
 * BenTrade — Mean Reversion Bounce Stock Strategy Dashboard
 * strategy_id: stock_mean_reversion
 *
 * Uses the shared BenTradeStockTradeCardMapper to render canonical TradeCards
 * (identical layout to options scanners) via renderFullCard().
 *
 * Flow: fetch → candidateToTradeShape → renderFullCard → action delegation.
 */
window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initStockMeanReversion = function initStockMeanReversion(rootEl) {
  var STRATEGY_ID = 'stock_mean_reversion';
  var registry    = window.BenTradeStockStrategies;
  var meta        = registry ? registry.getById(STRATEGY_ID) : null;
  var doc         = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  var scope       = rootEl || doc;

  // Shared mapper
  var stockMapper = window.BenTradeStockTradeCardMapper;
  var cache       = window.BenTradeScanResultsCache;
  var fmt         = window.BenTradeUtils ? window.BenTradeUtils.format : null;
  var CACHE_ID    = 'meanReversion';

  // DOM references
  var titleEl       = scope.querySelector('#stockStrategyTitle');
  var subtitleEl    = scope.querySelector('#stockStrategySubtitle');
  var badgeEl       = scope.querySelector('#stockStrategyBadge');
  var iconEl        = scope.querySelector('#stockStrategyIcon');
  var runBtn        = scope.querySelector('#stockStrategyRunBtn');
  var emptyStateEl  = scope.querySelector('#stockStrategyEmptyState');
  var candidatesEl  = scope.querySelector('#stockStrategyCandidates');
  var metricsRow    = scope.querySelector('#stockStrategyMetrics');

  // Metric tiles
  var tileCandidates = metricsRow ? metricsRow.querySelector('[data-metric="candidates"] .metric-tile-value') : null;
  var tileUniverse   = metricsRow ? metricsRow.querySelector('[data-metric="universe"] .metric-tile-value')   : null;
  var tileLastScan   = metricsRow ? metricsRow.querySelector('[data-metric="lastScan"] .metric-tile-value')   : null;
  var tileDataStatus = metricsRow ? metricsRow.querySelector('[data-metric="dataStatus"] .metric-tile-value') : null;

  // Hydrate header
  if (titleEl)    titleEl.textContent   = (meta ? meta.name : 'Mean Reversion') + ' — Stock Scanner';
  if (subtitleEl) subtitleEl.textContent = meta ? meta.description : 'Bounce plays on oversold names reverting to mean';
  if (badgeEl)    badgeEl.textContent    = 'BETA';
  if (iconEl)     iconEl.textContent     = meta ? meta.icon : '⟲';

  /* ── Strategy Info Banner (once per session) ─────────────────── */
  var BANNER_SESSION_KEY = 'bentrade_banner_dismissed_' + STRATEGY_ID;
  var STRATEGY_DESCRIPTION = 'Mean Reversion finds stocks that have been pushed to statistical extremes — oversold on RSI, trading below lower Bollinger Bands, or stretched far from their moving average — and are primed for a snap-back toward fair value. The scanner prioritizes names with strong long-term trends where the dip is likely temporary.';
  (function initBanner() {
    var bannerEl     = scope.querySelector('#stockStrategyBanner');
    var bannerTitle  = scope.querySelector('#stockBannerTitle');
    var bannerDesc   = scope.querySelector('#stockBannerDesc');
    var bannerDismiss = scope.querySelector('#stockBannerDismiss');
    if (!bannerEl) return;
    if (sessionStorage.getItem(BANNER_SESSION_KEY)) return;
    if (bannerTitle) bannerTitle.textContent = (meta ? meta.name : 'Mean Reversion') + ' Strategy';
    if (bannerDesc)  bannerDesc.textContent  = STRATEGY_DESCRIPTION;
    bannerEl.style.display = 'block';
    if (bannerDismiss) {
      bannerDismiss.addEventListener('click', function () {
        bannerEl.style.display = 'none';
        sessionStorage.setItem(BANNER_SESSION_KEY, '1');
      });
    }
  })();

  var latestPayload = null;
  var renderedRows  = [];
  var _expandState  = {};

  // ── Helpers ─────────────────────────────────────────────────────
  function esc(s) { return fmt && fmt.escapeHtml ? fmt.escapeHtml(s) : String(s || ''); }

  // ── Session cache ──────────────────────────────────────────────
  function saveToCache(payload) {
    if (cache) cache.save(CACHE_ID, payload, { endpoint: meta ? meta.endpoint : '' });
  }
  function loadFromCache() {
    if (!cache) return null;
    var entry = cache.load(CACHE_ID);
    return entry ? entry.payload : null;
  }

  // ── Update metric tiles ────────────────────────────────────────
  function updateTiles(payload) {
    var count = Array.isArray(payload.candidates) ? payload.candidates.length : 0;
    if (tileCandidates) tileCandidates.textContent = count;
    if (tileUniverse)   tileUniverse.textContent   = payload.universe ? payload.universe.symbols_count : '—';
    if (tileLastScan)   tileLastScan.textContent    = payload.as_of ? new Date(payload.as_of).toLocaleTimeString() : '—';
    var status = payload.status || 'unknown';
    if (tileDataStatus) {
      tileDataStatus.textContent = status.toUpperCase();
      tileDataStatus.style.color = status === 'ok' ? 'rgba(0,234,255,0.95)' : 'rgba(255,180,60,0.95)';
    }
  }

  // ── Render candidates ──────────────────────────────────────────
  function renderCandidates(payload) {
    if (!candidatesEl) return;
    var candidates = Array.isArray(payload.candidates) ? payload.candidates : [];

    if (candidates.length === 0) {
      candidatesEl.innerHTML = '<div style="text-align:center;padding:32px;color:rgba(190,236,244,0.5);font-size:13px;grid-column:1/-1;">No candidates found. Try running a scan.</div>';
      return;
    }

    renderedRows = candidates;
    var html = '';
    var renderErrors = [];

    candidates.forEach(function (row, idx) {
      try {
        html += stockMapper.renderStockCard(row, idx, STRATEGY_ID, _expandState);
      } catch (cardErr) {
        renderErrors.push({ idx: idx, symbol: row && row.symbol, error: cardErr.message });
        console.warn('MeanReversion: card render error for candidate ' + idx, cardErr);
        html += '<div class="trade-card" style="margin-bottom:12px;padding:10px;border:1px solid rgba(255,120,100,0.3);border-radius:10px;background:rgba(8,18,26,0.9);color:rgba(255,180,160,0.8);font-size:12px;">\u26A0 Render error for ' + esc(row && row.symbol || '#' + idx) + '</div>';
      }
    });

    if (renderErrors.length) console.warn('MeanReversion: ' + renderErrors.length + ' card render errors', renderErrors);
    candidatesEl.innerHTML = html;

    if (emptyStateEl) emptyStateEl.style.display = 'none';

    // Wire action delegation
    candidatesEl.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-action]');
      if (!btn) return;
      var action   = btn.dataset.action;
      var tradeKey = btn.dataset.tradeKey || '';
      var symbol   = btn.dataset.symbol || '';
      var row      = _findRowByTradeKey(tradeKey);

      if (action === 'data-workbench' && row) {
        stockMapper.openDataWorkbenchForStock(row, STRATEGY_ID);
      } else if (action === 'stock-analysis') {
        stockMapper.openStockAnalysis(symbol || (row && row.symbol));
      } else if (action === 'reject' && tradeKey) {
        _handleReject(btn, tradeKey);
      } else if (action === 'execute') {
        if (row) stockMapper.executeStockTrade(btn, tradeKey, row, STRATEGY_ID);
      } else if (action === 'workbench') {
        console.log('[MeanReversion] Testing Workbench stub for:', tradeKey);
      } else if (action === 'model-analysis') {
        if (row) {
          stockMapper.runModelAnalysisForStock(btn, tradeKey, row, STRATEGY_ID);
        }
      }
    });

    // Wire expand state persistence
    candidatesEl.querySelectorAll('details.trade-card-collapse').forEach(function (details) {
      details.addEventListener('toggle', function () {
        var tk = details.dataset.tradeKey || '';
        if (tk) _expandState[tk] = details.open;
      });
    });

    if (window.attachMetricTooltips) window.attachMetricTooltips(candidatesEl);

    // Hydrate cached model analysis results into rendered cards
    if (window.BenTradeModelAnalysisStore && window.BenTradeModelAnalysisStore.hydrateContainer) {
      window.BenTradeModelAnalysisStore.hydrateContainer(candidatesEl);
    }
  }

  // ── Reject handler ─────────────────────────────────────────────
  function _handleReject(btn, tradeKey) {
    var cardEl = btn.closest('.trade-card');
    if (cardEl) {
      cardEl.style.opacity = '0.35';
      cardEl.style.pointerEvents = 'none';
    }
    console.log('[MeanReversion] Rejected:', tradeKey);
  }

  // ── Find row by trade key ──────────────────────────────────────
  function _findRowByTradeKey(tradeKey) {
    if (!tradeKey) return null;
    for (var i = 0; i < renderedRows.length; i++) {
      var row = renderedRows[i];
      var rk = row.trade_key || stockMapper.buildStockTradeKey(row.symbol, STRATEGY_ID);
      if (rk === tradeKey) return row;
    }
    return null;
  }

  // ── Fetch scan results ─────────────────────────────────────────
  async function runScan() {
    if (!runBtn) return;
    runBtn.disabled = true;
    runBtn.textContent = '⟳ Scanning…';
    if (candidatesEl) candidatesEl.innerHTML = '<div style="text-align:center;padding:32px;color:rgba(190,236,244,0.5);font-size:13px;grid-column:1/-1;">Scanning universe\u2026 this may take 30-60 seconds.</div>';

    try {
      var endpoint = meta ? meta.endpoint : '/api/stocks/mean-reversion';
      var resp = await fetch(endpoint);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      var payload = await resp.json();
      latestPayload = payload;

      updateTiles(payload);
      renderCandidates(payload);
      saveToCache(payload);
    } catch (err) {
      console.error('Mean Reversion scan error:', err);
      if (candidatesEl) candidatesEl.innerHTML = '<div style="text-align:center;padding:32px;color:rgba(255,120,100,0.9);font-size:13px;grid-column:1/-1;">Scan failed: ' + esc(err.message) + '</div>';
      if (tileDataStatus) {
        tileDataStatus.textContent = 'ERROR';
        tileDataStatus.style.color = 'rgba(255,120,100,0.95)';
      }
    } finally {
      if (runBtn) {
        runBtn.disabled = false;
        runBtn.textContent = '▶ Run Scan';
      }
    }
  }

  // ── Boot: restore cache or show empty state ────────────────────
  var cached = loadFromCache();
  if (cached && Array.isArray(cached.candidates) && cached.candidates.length > 0) {
    latestPayload = cached;
    updateTiles(cached);
    renderCandidates(cached);
    if (emptyStateEl) emptyStateEl.style.display = 'none';
  }

  if (runBtn) {
    runBtn.addEventListener('click', function () { runScan(); });
  }
};
