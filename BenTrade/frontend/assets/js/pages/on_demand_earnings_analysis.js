/**
 * On Demand Earnings Analysis dashboard.
 * Single-ticker analysis backed by EVA. Uses the shared rendering
 * helpers exposed by earnings_analysis.js (window.BenTradeEAShared).
 *
 * Init:  window.BenTradePages.initOnDemandEarningsAnalysis(rootEl)
 */
(function () {
  'use strict';

  window.BenTradePages = window.BenTradePages || {};

  window.BenTradePages.initOnDemandEarningsAnalysis = function initOnDemandEarningsAnalysis(rootEl) {
    var scope = rootEl || document;
    var H = (window.BenTradeEAShared && window.BenTradeEAShared.helpers) || {};
    var renderSidePanel = window.BenTradeEAShared && window.BenTradeEAShared.renderSidePanel;

    var elTicker  = scope.querySelector('#oea-ticker');
    var elAnalyze = scope.querySelector('#oea-analyze');
    var elAdd     = scope.querySelector('#oea-add');
    var elStatus  = scope.querySelector('#oea-status');
    var elBanner  = scope.querySelector('#oea-banner-slot');
    var elBody    = scope.querySelector('#oea-body');

    var state = {
      ticker: '',
      currentTab: 'overview',
      currentCtx: null,
      pollTimer: null,
      pollDeadline: 0,
    };

    function _esc(s) {
      if (s == null) return '';
      var d = document.createElement('span'); d.textContent = String(s); return d.innerHTML;
    }

    function _setBanner(html) { elBanner.innerHTML = html || ''; }
    function _setStatus(text) { elStatus.textContent = text || ''; }

    function _bindTooltips(container) {
      try { window.BenTradeUI && window.BenTradeUI.Tooltip && window.BenTradeUI.Tooltip.bindMetricsInContainer && window.BenTradeUI.Tooltip.bindMetricsInContainer(container); } catch (_) {}
      try { window.attachMetricTooltips && window.attachMetricTooltips(container); } catch (_) {}
    }

    function _stopPoll() {
      if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
    }

    function _startPoll(ticker) {
      _stopPoll();
      state.pollDeadline = Date.now() + 2 * 60 * 1000; // 2 minutes
      state.pollTimer = setInterval(function () {
        if (Date.now() > state.pollDeadline) { _stopPoll(); return; }
        _analyze(ticker, /*silent*/ true);
      }, 10000);
    }

    function _renderEmpty(msg) {
      elBody.innerHTML = '<div class="ea-od-empty">' + _esc(msg || 'Enter a ticker to see earnings vol analysis.') + '</div>';
    }

    function _renderError(msg) {
      elBody.innerHTML = '<div class="ea-state ea-state-error">' + _esc(msg) + '</div>';
    }

    function _renderShell(rendered) {
      elBody.innerHTML =
        '<div class="ea-panel">' +
          '<div class="ea-panel-header"><span>' + _esc(rendered.title) + '</span></div>' +
          '<div class="ea-panel-tabs" id="oea-tabs">' +
            '<button class="ea-tab active" data-tab="overview">Overview</button>' +
            '<button class="ea-tab" data-tab="history">History</button>' +
            '<button class="ea-tab" data-tab="timeline">Timeline</button>' +
            '<button class="ea-tab" data-tab="macro">Macro</button>' +
            '<button class="ea-tab" data-tab="raw">Raw</button>' +
          '</div>' +
          '<div class="ea-panel-body" id="oea-tab-body"></div>' +
        '</div>';
      var tabs = elBody.querySelectorAll('#oea-tabs .ea-tab');
      Array.prototype.forEach.call(tabs, function (b) {
        b.addEventListener('click', function () {
          state.currentTab = b.getAttribute('data-tab');
          Array.prototype.forEach.call(tabs, function (x) { x.classList.toggle('active', x === b); });
          _renderTab(rendered);
        });
      });
      _renderTab(rendered);
    }

    function _renderTab(rendered) {
      var body = elBody.querySelector('#oea-tab-body');
      if (!body) return;
      body.innerHTML = rendered.tabs[state.currentTab] || '';
      _bindTooltips(body);
    }

    function _pickLatestSnapshot(snaps) {
      if (!Array.isArray(snaps) || !snaps.length) return null;
      return snaps.slice().sort(function (a, b) {
        return String(b.snapshot_date || '').localeCompare(String(a.snapshot_date || ''));
      })[0];
    }

    function _analyze(rawTicker, silent) {
      var ticker = (rawTicker || '').trim().toUpperCase();
      if (!ticker) { _renderEmpty(); return; }
      state.ticker = ticker;
      if (!silent) { _setStatus('Loading…'); _setBanner(''); }

      var pProfile = window.BenTradeApi.getEvaTicker(ticker)
        .then(function (r) { return { ok: true, data: r }; })
        .catch(function (err) {
          console.error('[OnDemandEarnings] getEvaTicker failed', ticker, err);
          return { ok: false, err: err };
        });

      var pLatest = window.BenTradeApi.getEvaTickerLatestFeatures(ticker)
        .then(function (r) { return { ok: true, data: r }; })
        .catch(function (err) {
          console.error('[OnDemandEarnings] getEvaTickerLatestFeatures failed', ticker, err);
          return { ok: false, err: err };
        });

      Promise.all([pProfile, pLatest]).then(function (parts) {
        var prof = parts[0];
        var latest = parts[1];

        // Service unreachable → hard error
        if (!prof.ok && prof.err && prof.err.status === 503) {
          _setStatus('');
          _renderError('Could not reach Earnings Vol Analyzer. Check the service at 192.168.1.143:8200.');
          return;
        }

        // Profile 404 → ticker doesn't exist on EVA at all
        var profileExists = prof.ok && prof.data;
        var profile = profileExists ? (prof.data.profile || prof.data) : null;
        var inUniverse = profile && (profile.in_universe === true || profile.is_active === true || profile.universe_active === true);
        var backfillStatus = profile ? (profile.backfill_status || (prof.data && prof.data.backfill_status)) : null;

        if (!profileExists && (!latest.ok || !latest.data)) {
          _setStatus('');
          _renderError('Ticker "' + _esc(ticker) + '" not found on EVA. It may not be optionable, not a valid symbol, or EVA has no record yet.');
          elAdd.style.display = 'inline-block';
          elAdd.dataset.ticker = ticker;
          return;
        }

        // Build context for shared renderer
        var latestData = latest.ok ? latest.data : null;
        var snap = (latestData && (latestData.features || latestData.latest_features || latestData.snapshot)) || latestData || {};
        var snaps = (latestData && (latestData.snapshots || latestData.all_features)) || (snap ? [snap] : []);
        var event = (latestData && (latestData.event || latestData.next_event)) ||
                    (profile && profile.next_event) || {};
        var history = (prof.ok && prof.data && (prof.data.earnings_history || prof.data.history)) || [];

        var ctx = {
          event: Object.assign({ ticker: ticker }, event),
          ticker_profile: profile || { ticker: ticker },
          snapshot: snap,
          snapshots_all: snaps,
          history: history,
        };
        state.currentCtx = ctx;

        // Banners
        var banners = '';
        if (!inUniverse) {
          banners += '<div class="ea-banner ea-banner-warn">' +
            'This ticker is not in your universe. Limited historical data available. ' +
            'Click <b>Add to Universe</b> to trigger full backfill.</div>';
          elAdd.style.display = 'inline-block';
          elAdd.dataset.ticker = ticker;
        } else {
          elAdd.style.display = 'none';
        }
        if (backfillStatus === 'running' || backfillStatus === 'pending') {
          banners += '<div class="ea-banner ea-banner-info">Backfill ' + _esc(backfillStatus) +
            ' — data will populate over the next ~30 seconds. Auto-refreshing…</div>';
          if (!state.pollTimer) _startPoll(ticker);
        } else if (backfillStatus === 'complete' || backfillStatus === 'completed') {
          _stopPoll();
        }
        _setBanner(banners);
        _setStatus('Loaded ' + new Date().toLocaleTimeString());

        if (renderSidePanel) {
          var rendered = renderSidePanel(ctx);
          _renderShell(rendered);
        } else {
          _renderError('Internal: shared renderer missing (earnings_analysis.js not loaded).');
        }
      }).catch(function (err) {
        console.error('[OnDemandEarnings] _analyze pipeline failed', err);
        _setStatus('');
        _renderError('Failed to render: ' + (err && err.message ? err.message : err));
      });
    }

    // ── Bindings ──
    elAnalyze.addEventListener('click', function () { _stopPoll(); _analyze(elTicker.value, false); });
    elTicker.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { _stopPoll(); _analyze(elTicker.value, false); }
    });
    elAdd.addEventListener('click', function () {
      var ticker = elAdd.dataset.ticker || elTicker.value;
      if (!ticker) return;
      var notes = window.prompt('Optional notes for adding ' + ticker + ' to the universe:', '') || '';
      window.BenTradeApi.addEvaTicker(ticker, notes || null)
        .then(function () {
          if (window.BenTradeToast && window.BenTradeToast.show) {
            window.BenTradeToast.show('Added ' + ticker + ' to universe. Backfill running. Data will populate over the next ~30 seconds.');
          } else {
            window.alert('Added ' + ticker + ' to universe. Backfill running.');
          }
          elAdd.style.display = 'none';
          _startPoll(ticker);
          _analyze(ticker, false);
        })
        .catch(function (err) {
          console.error('[OnDemandEarnings] addEvaTicker failed', ticker, err);
          var msg = (err && err.message) || 'Failed to add ticker.';
          if (window.BenTradeToast && window.BenTradeToast.error) window.BenTradeToast.error(msg);
          else window.alert(msg);
        });
    });

    _renderEmpty();

    // ── Connection toggle (mirrors Company Evaluator pattern) ──
    var _connRadios = scope.querySelectorAll('input[name="oea-conn-mode"]');
    var _connUrlEl  = scope.querySelector('#oea-conn-url');

    function _setConnRadioState(mode) {
      Array.prototype.forEach.call(_connRadios, function (r) { r.checked = (r.value === mode); });
    }
    function _showConnUrl(url, healthy) {
      if (!_connUrlEl) return;
      var dot = healthy ? '\u25CF' : '\u25CB';
      var color = healthy ? '#00c853' : '#ff1744';
      _connUrlEl.innerHTML = '<span style="color:' + color + ';">' + dot + '</span> ' + _esc(url);
      _connUrlEl.title = healthy ? 'Connected' : 'Cannot reach EVA at ' + url;
    }
    function _showConnWarning(url) {
      if (!_connUrlEl) return;
      _connUrlEl.innerHTML = '<span style="color:#ff1744;">\u25CB</span> ' + _esc(url) +
        ' <span style="color:#ff9800; font-size:0.68rem;">\u2014 not reachable</span>';
    }
    async function _checkEvaHealth(url) {
      try {
        var res = await fetch('/api/eva/status');
        if (!res.ok) { _showConnWarning(url); return; }
        var data = await res.json();
        if (data.service_healthy) _showConnUrl(url, true); else _showConnWarning(url);
      } catch (_e) { _showConnWarning(url); }
    }
    async function _loadConnectionState() {
      try {
        var res = await fetch('/api/eva/connection');
        if (!res.ok) return;
        var data = await res.json();
        _setConnRadioState(data.mode);
        _showConnUrl(data.url, null);
        _checkEvaHealth(data.url);
      } catch (_e) { /* ignore */ }
    }
    async function _switchConnectionMode(mode) {
      Array.prototype.forEach.call(_connRadios, function (r) { r.disabled = true; });
      try {
        var res = await fetch('/api/eva/connection', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: mode }),
        });
        if (!res.ok) {
          var err = await res.json().catch(function () { return {}; });
          alert('Failed to switch EVA mode: ' + (err.detail || 'unknown error'));
          _loadConnectionState();
          return;
        }
        var data = await res.json();
        _setConnRadioState(data.mode);
        _showConnUrl(data.url, null);
        // Cache invalidation: clear EA cache (shared store) and any in-progress poll
        try { window.BenTradeEarningsAnalysisCache && window.BenTradeEarningsAnalysisCache.clearAll && window.BenTradeEarningsAnalysisCache.clearAll(); } catch (_) {}
        _stopPoll();
        await _checkEvaHealth(data.url);
        if (state.ticker) {
          _analyze(state.ticker, false);
        } else {
          _renderEmpty();
        }
      } catch (e) {
        alert('Failed to switch connection: ' + e.message);
        _loadConnectionState();
      } finally {
        Array.prototype.forEach.call(_connRadios, function (r) { r.disabled = false; });
      }
    }
    Array.prototype.forEach.call(_connRadios, function (radio) {
      radio.addEventListener('change', function () {
        if (this.checked) _switchConnectionMode(this.value);
      });
    });
    _loadConnectionState();

    return function cleanup() { _stopPoll(); };
  };
})();
