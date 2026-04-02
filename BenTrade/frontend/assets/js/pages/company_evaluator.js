/* ── Company Evaluator Page ── */
window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initCompanyEvaluator = function initCompanyEvaluator(rootEl) {
  var doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  var scope = rootEl || doc;
  var _statusTimer = null;
  var _ceCompanies = [];
  var _cePositions = {};   // { SYMBOL: { qty, avg_price } } from Tradier
  var _sortCol = 'composite_score';
  var _sortAsc = false;

  // ── Helpers ──
  function _esc(s) {
    if (!s) return '';
    var el = document.createElement('span');
    el.textContent = s;
    return el.innerHTML;
  }

  var _thStyle = 'padding:8px 10px; text-align:left; color:rgba(224,224,224,0.5); font-size:0.75rem; font-weight:600; letter-spacing:0.03em; text-transform:uppercase;';
  var _tdStyle = 'padding:8px 10px;';

  function _scoreColor(score) {
    if (score == null) return 'rgba(224,224,224,0.3)';
    if (score >= 75) return '#00c853';
    if (score >= 55) return '#ffd600';
    if (score >= 35) return '#ff9800';
    return '#ff1744';
  }

  var _recColors = {
    'STRONG_BUY': '#00c853', 'BUY': '#4caf50',
    'HOLD': '#ffd600',
    'SELL': '#ff9800', 'STRONG_SELL': '#ff1744'
  };

  function _recBadge(rec) {
    if (!rec) return '<span style="color:rgba(224,224,224,0.2);">--</span>';
    var color = _recColors[rec] || 'rgba(224,224,224,0.5)';
    var label = _esc(rec.replace(/_/g, ' '));
    return '<span style="background:' + color + '15; color:' + color + '; padding:2px 6px; border-radius:3px; font-size:0.7rem; font-weight:600;">' + label + '</span>';
  }

  function _timeAgo(isoString) {
    if (!isoString) return '--';
    var d = new Date(isoString);
    var now = new Date();
    var diff = (now - d) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return Math.floor(diff / 86400) + 'd ago';
  }

  function _isStale(isoString) {
    if (!isoString) return true;
    var d = new Date(isoString);
    return (new Date() - d) > 7 * 86400 * 1000;
  }

  function _pillarCell(score) {
    if (score == null) return '<td style="' + _tdStyle + 'text-align:center; color:rgba(224,224,224,0.2);">--</td>';
    var color = _scoreColor(score);
    return '<td style="' + _tdStyle + 'text-align:center; color:' + color + '; font-size:0.85rem;">' + score.toFixed(0) + '</td>';
  }

  function _formatMarketCap(val) {
    if (val == null || val === 0) return '--';
    if (val >= 1e12) return '$' + (val / 1e12).toFixed(1) + 'T';
    if (val >= 1e9) return '$' + (val / 1e9).toFixed(1) + 'B';
    if (val >= 1e6) return '$' + (val / 1e6).toFixed(0) + 'M';
    return '$' + val.toLocaleString();
  }

  function _classifyMarketCap(val) {
    if (val == null || val === 0) return '';
    if (val >= 200e9) return 'mega';
    if (val >= 10e9) return 'large';
    if (val >= 2e9) return 'mid';
    if (val >= 300e6) return 'small';
    return 'micro';
  }

  // ── Filter logic ──
  function _getFilterState() {
    return {
      sector: (scope.querySelector('#ce-sector-filter') || {}).value || '',
      mcap: (scope.querySelector('#ce-mcap-filter') || {}).value || '',
      rating: (scope.querySelector('#ce-rating-filter') || {}).value || '',
      tier: (scope.querySelector('#ce-tier-filter') || {}).value || '',
      score: (scope.querySelector('#ce-score-filter') || {}).value || '',
      search: ((scope.querySelector('#ce-search-input') || {}).value || '').trim().toLowerCase(),
    };
  }

  function _matchesFilters(c, f) {
    if (f.sector && c.sector !== f.sector) return false;
    if (f.mcap && _classifyMarketCap(c.market_cap) !== f.mcap) return false;
    if (f.rating && c.llm_recommendation !== f.rating) return false;
    if (f.tier && (c.source || c.universe_tier || '') !== f.tier) return false;
    if (f.score) {
      var s = c.composite_score;
      var sv = parseInt(f.score, 10);
      if (sv === 80 && (s == null || s < 80)) return false;
      if (sv === 70 && (s == null || s < 70 || s >= 80)) return false;
      if (sv === 60 && (s == null || s < 60 || s >= 70)) return false;
      if (sv === 0 && (s != null && s >= 60)) return false;
    }
    if (f.search) {
      var sym = (c.symbol || '').toLowerCase();
      var name = (c.company_name || '').toLowerCase();
      if (sym.indexOf(f.search) === -1 && name.indexOf(f.search) === -1) return false;
    }
    return true;
  }

  function applyFilters() {
    // Remove all analysis panels/accordion rows and reset panel state
    var allPanels = scope.querySelectorAll('.ce-analysis-panel, .ce-analysis-header, .ce-analysis-content');
    for (var pi = 0; pi < allPanels.length; pi++) allPanels[pi].parentNode.removeChild(allPanels[pi]);
    _entryPanelOpen = {};
    _compsPanelOpen = {};
    _dcfPanelOpen = {};
    _evaPanelOpen = {};
    var f = _getFilterState();
    var filtered = [];
    for (var i = 0; i < _ceCompanies.length; i++) {
      if (_matchesFilters(_ceCompanies[i], f)) filtered.push(_ceCompanies[i]);
    }
    _sortList(filtered);
    renderTable(filtered);
    _updateFilterCount(filtered.length, _ceCompanies.length);
  }

  function _updateFilterCount(shown, total) {
    var el = scope.querySelector('#ce-filter-count');
    if (el) el.textContent = 'Showing ' + shown + ' of ' + total + ' companies';
  }

  function resetFilters() {
    var ids = ['#ce-sector-filter', '#ce-mcap-filter', '#ce-rating-filter', '#ce-tier-filter', '#ce-score-filter'];
    for (var i = 0; i < ids.length; i++) {
      var el = scope.querySelector(ids[i]);
      if (el) el.value = '';
    }
    var search = scope.querySelector('#ce-search-input');
    if (search) search.value = '';
    applyFilters();
  }

  // ── Sort logic ──
  function _sortList(arr) {
    var col = _sortCol;
    var asc = _sortAsc;
    arr.sort(function(a, b) {
      var va = _getSortValue(a, col);
      var vb = _getSortValue(b, col);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (va < vb) return asc ? -1 : 1;
      if (va > vb) return asc ? 1 : -1;
      return 0;
    });
  }

  function _getSortValue(c, col) {
    if (col === 'composite_score') return c.composite_score;
    if (col === 'market_cap') return c.market_cap;
    if (col === 'llm_recommendation') {
      var order = { 'STRONG_BUY': 5, 'BUY': 4, 'HOLD': 3, 'SELL': 2, 'STRONG_SELL': 1 };
      return order[c.llm_recommendation] || 0;
    }
    var ps = c.pillar_scores || {};
    if (col === 'business_quality') return ps.business_quality;
    if (col === 'operational_health') return ps.operational_health;
    if (col === 'capital_allocation') return ps.capital_allocation;
    if (col === 'growth_quality') return ps.growth_quality;
    if (col === 'valuation') return ps.valuation;
    return null;
  }

  function _onSortClick(col) {
    if (_sortCol === col) {
      _sortAsc = !_sortAsc;
    } else {
      _sortCol = col;
      _sortAsc = false;
    }
    applyFilters();
  }

  function _sortIndicator(col) {
    if (_sortCol !== col) return '';
    return _sortAsc ? ' &#9650;' : ' &#9660;';
  }

  // ── Populate dynamic filter dropdowns ──
  function populateFilters(companies) {
    // Sectors
    var sectorSeen = {};
    var sectors = [];
    var tierSeen = {};
    var tiers = [];
    for (var i = 0; i < companies.length; i++) {
      var s = companies[i].sector;
      if (s && !sectorSeen[s]) { sectorSeen[s] = true; sectors.push(s); }
      var t = companies[i].source || companies[i].universe_tier;
      if (t && !tierSeen[t]) { tierSeen[t] = true; tiers.push(t); }
    }
    sectors.sort();
    tiers.sort();

    var sectorSelect = scope.querySelector('#ce-sector-filter');
    if (sectorSelect) {
      sectorSelect.innerHTML = '<option value="">All Sectors (' + companies.length + ')</option>';
      for (var j = 0; j < sectors.length; j++) {
        var cnt = 0;
        for (var k = 0; k < companies.length; k++) {
          if (companies[k].sector === sectors[j]) cnt++;
        }
        sectorSelect.innerHTML += '<option value="' + _esc(sectors[j]) + '">' + _esc(sectors[j]) + ' (' + cnt + ')</option>';
      }
    }

    var tierSelect = scope.querySelector('#ce-tier-filter');
    if (tierSelect) {
      tierSelect.innerHTML = '<option value="">All Tiers</option>';
      for (var ti = 0; ti < tiers.length; ti++) {
        tierSelect.innerHTML += '<option value="' + _esc(tiers[ti]) + '">' + _esc(tiers[ti]) + '</option>';
      }
    }
  }

  // ── Position cross-reference ──
  function loadPositions() {
    var api = window.BenTradeApi;
    if (!api || !api.getTradingPositions) return;
    api.getTradingPositions('paper')
      .then(function(data) {
        var pos = {};
        var items = (data && data.positions) || data || [];
        if (!Array.isArray(items)) items = [];
        for (var i = 0; i < items.length; i++) {
          var p = items[i];
          // Equity positions only (no option_type field)
          var sym = (p.symbol || '').toUpperCase();
          if (!sym || p.option_type) continue;
          var qty = parseFloat(p.quantity) || 0;
          if (qty <= 0) continue;
          pos[sym] = { qty: qty, avg_price: parseFloat(p.cost_basis) / qty || null };
        }
        _cePositions = pos;
        applyFilters();  // re-render to show position badges
      })
      .catch(function() { /* positions unavailable — no badge, no problem */ });
  }

  function _positionBadge(symbol) {
    var p = _cePositions[symbol];
    if (!p) return '';
    return '<span style="display:inline-flex; align-items:center; gap:2px; padding:1px 5px; border-radius:3px; '
      + 'font-size:0.68rem; font-weight:600; background:rgba(0,200,200,0.1); color:#00c8c8; border:1px solid rgba(0,200,200,0.2);">'
      + '\uD83D\uDCCB ' + p.qty + ' share' + (p.qty !== 1 ? 's' : '') + '</span>';
  }

  // ── Buy flow ──
  function _buyStock(symbol, company) {
    // Fetch quote then open the existing stock execute modal
    var api = window.BenTradeApi;
    if (!api || !api.getBatchQuotes) {
      alert('Trading API not available.');
      return;
    }

    // Show loading state on the button
    var btn = scope.querySelector('.ce-buy-btn[data-symbol="' + symbol + '"]');
    if (btn) { btn.disabled = true; btn.textContent = '...'; }

    api.getBatchQuotes([symbol])
      .then(function(data) {
        var q = (data && data.quotes && data.quotes[symbol]) || {};
        var last = q.last || q.bid || null;
        var bid = q.bid || null;
        var ask = q.ask || null;

        // Build a candidate object that matches what the stock execute modal expects
        var candidate = {
          symbol: symbol,
          company_name: company.company_name || '',
          price: last,
          bid: bid,
          ask: ask,
          composite_score: company.composite_score,
          llm_recommendation: company.llm_recommendation,
        };

        var tradeKey = 'ce-' + symbol + '-' + Date.now().toString(36);
        var modal = window.BenTradeStockExecuteModal;
        if (!modal || !modal.open) {
          alert('Stock execution modal not loaded. Please access via TMC.');
          return;
        }

        modal.open(candidate, 'company_evaluator_buy', tradeKey);
      })
      .catch(function(err) {
        alert('Failed to fetch quote for ' + symbol + ': ' + (err.message || err));
      })
      .finally(function() {
        if (btn) { btn.disabled = false; btn.textContent = 'Buy \u25B6'; }
      });
  }

  function _buyBtnStyle(rec) {
    // Prominent green for BUY/STRONG_BUY, muted for others
    if (rec === 'STRONG_BUY' || rec === 'BUY') {
      return 'background:rgba(0,200,83,0.15); color:#00c853; border:1px solid rgba(0,200,83,0.3);';
    }
    return 'background:rgba(255,255,255,0.04); color:rgba(224,224,224,0.4); border:1px solid rgba(255,255,255,0.08);';
  }

  // ── Buy at a specific limit price (from entry analysis) ──
  function _buyStockAtPrice(symbol, limitPrice, company) {
    var api = window.BenTradeApi;
    if (!api || !api.getBatchQuotes) { alert('Trading API not available.'); return; }

    var candidate = {
      symbol: symbol,
      company_name: (company && company.company_name) || '',
      price: limitPrice,
      composite_score: (company && company.composite_score) || null,
      llm_recommendation: (company && company.llm_recommendation) || null,
    };

    var tradeKey = 'ce-entry-' + symbol + '-' + Date.now().toString(36);
    var modal = window.BenTradeStockExecuteModal;
    if (!modal || !modal.open) { alert('Stock execution modal not loaded.'); return; }
    modal.open(candidate, 'company_evaluator_buy', tradeKey);
  }

  // ── Entry Point Analysis ──
  // ── Analysis cache & panel state ──
  // { SYMBOL: { data, analyzedAt } } — survives re-renders, cleared on page exit
  var _entryCache = {};
  var _compsCache = {};
  var _dcfCache = {};
  var _evaCache = {};
  // Track which panels are expanded (all can be open simultaneously)
  var _entryPanelOpen = {};   // { SYMBOL: true }
  var _compsPanelOpen = {};   // { SYMBOL: true }
  var _dcfPanelOpen = {};     // { SYMBOL: true }
  var _evaPanelOpen = {};     // { SYMBOL: true }
  // Track which symbols have server-side cached analyses (from status endpoint)
  var _analysisStatus = {};   // { SYMBOL: { entry: bool, comps: bool, dcf: bool, eva: bool } }

  function _entryAnalysis(symbol, company) {
    // If header already exists, just toggle
    var hdr = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="entry"]');
    if (hdr) {
      _togglePanel(symbol, 'entry');
      return;
    }
    // No panel yet — fetch then create
    _entryPanelOpen[symbol] = true;
    _updateBtnState(symbol, 'entry');

    // Show spinner in a temporary row
    var actionRow = scope.querySelector('.ce-action-row[data-symbol="' + _esc(symbol) + '"]');
    if (!actionRow) return;
    var colCount = _getColCount();

    var spinTr = document.createElement('tr');
    spinTr.className = 'ce-analysis-panel ce-analysis-spinner';
    spinTr.setAttribute('data-symbol', symbol);
    spinTr.setAttribute('data-type', 'entry');
    var spinTd = document.createElement('td');
    spinTd.colSpan = colCount;
    spinTd.innerHTML = '<div style="padding:16px 24px; text-align:center; color:rgba(224,224,224,0.5);">'
      + '<div class="home-scan-spinner" style="width:20px; height:20px; margin:0 auto 8px;"></div>'
      + 'Analyzing entry point for ' + _esc(symbol) + '\u2026</div>';
    spinTr.appendChild(spinTd);
    actionRow.parentNode.insertBefore(spinTr, actionRow.nextSibling);

    _fetchEntryAnalysis(symbol, company, null, false);
  }

  function _fetchEntryAnalysis(symbol, company, _unused, forceFresh) {
    var fetchPromise;
    if (!forceFresh) {
      fetchPromise = fetch('/api/company-evaluator/entry-point/analysis/' + encodeURIComponent(symbol))
        .then(function(res) {
          if (res.ok) return res.json();
          return _postEntryAnalysis(symbol);
        });
    } else {
      fetchPromise = _postEntryAnalysis(symbol);
    }

    fetchPromise
      .then(function(data) {
        _entryCache[symbol] = { data: data, analyzedAt: new Date().toISOString() };
        _updateBtnState(symbol, 'entry');
        _buildAccordionPanel(symbol, company, 'entry');
      })
      .catch(function(err) {
        // Remove spinner, show error inline
        _removeSpinner(symbol, 'entry');
        _entryPanelOpen[symbol] = false;
        _updateBtnState(symbol, 'entry');
        var actionRow = scope.querySelector('.ce-action-row[data-symbol="' + _esc(symbol) + '"]');
        if (!actionRow) return;
        var errTr = document.createElement('tr');
        errTr.className = 'ce-analysis-panel';
        errTr.setAttribute('data-symbol', symbol);
        errTr.setAttribute('data-type', 'entry');
        var errTd = document.createElement('td');
        errTd.colSpan = _getColCount();
        errTd.innerHTML = '<div style="padding:12px 24px; color:#ff5a5a; font-size:13px;">'
          + '\u26A0 Entry analysis failed: ' + _esc(err.message)
          + ' <button class="ce-panel-dismiss" style="margin-left:12px; padding:2px 8px; border-radius:4px; font-size:11px; '
          + 'background:none; color:rgba(224,224,224,0.4); border:1px solid rgba(255,255,255,0.08); cursor:pointer;">Dismiss</button></div>';
        errTr.appendChild(errTd);
        actionRow.parentNode.insertBefore(errTr, actionRow.nextSibling);
        var dismiss = errTd.querySelector('.ce-panel-dismiss');
        if (dismiss) dismiss.addEventListener('click', function() { errTr.parentNode.removeChild(errTr); });
      });
  }

  function _postEntryAnalysis(symbol) {
    return fetch('/api/company-evaluator/entry-point/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: symbol }),
    }).then(function(res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    });
  }

  function _closeEntryPanel() {
    var panels = scope.querySelectorAll('.ce-analysis-panel[data-type="entry"], .ce-analysis-header[data-type="entry"], .ce-analysis-content[data-type="entry"]');
    for (var i = 0; i < panels.length; i++) panels[i].parentNode.removeChild(panels[i]);
    _entryPanelOpen = {};
    var btns = scope.querySelectorAll('.ce-entry-btn');
    for (var b = 0; b < btns.length; b++) _updateBtnState(btns[b].getAttribute('data-symbol'), 'entry');
  }

  // ── Comps Analysis ──
  function _closeCompsPanel() {
    var panels = scope.querySelectorAll('.ce-analysis-panel[data-type="comps"], .ce-analysis-header[data-type="comps"], .ce-analysis-content[data-type="comps"]');
    for (var i = 0; i < panels.length; i++) panels[i].parentNode.removeChild(panels[i]);
    _compsPanelOpen = {};
    var btns = scope.querySelectorAll('.ce-comps-btn');
    for (var b = 0; b < btns.length; b++) _updateBtnState(btns[b].getAttribute('data-symbol'), 'comps');
  }

  // ── DCF Analysis ──
  function _closeDcfPanel() {
    var panels = scope.querySelectorAll('.ce-analysis-panel[data-type="dcf"], .ce-analysis-header[data-type="dcf"], .ce-analysis-content[data-type="dcf"]');
    for (var i = 0; i < panels.length; i++) panels[i].parentNode.removeChild(panels[i]);
    _dcfPanelOpen = {};
    var btns = scope.querySelectorAll('.ce-dcf-btn');
    for (var b = 0; b < btns.length; b++) _updateBtnState(btns[b].getAttribute('data-symbol'), 'dcf');
  }

  function _handleDcfAnalysis(symbol, company) {
    var hdr = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="dcf"]');
    if (hdr) {
      _togglePanel(symbol, 'dcf');
      return;
    }
    _dcfPanelOpen[symbol] = true;
    _updateBtnState(symbol, 'dcf');

    var actionRow = scope.querySelector('.ce-action-row[data-symbol="' + _esc(symbol) + '"]');
    if (!actionRow) return;
    var colCount = _getColCount();

    var spinTr = document.createElement('tr');
    spinTr.className = 'ce-analysis-panel ce-analysis-spinner';
    spinTr.setAttribute('data-symbol', symbol);
    spinTr.setAttribute('data-type', 'dcf');
    var spinTd = document.createElement('td');
    spinTd.colSpan = colCount;
    spinTd.innerHTML = '<div style="padding:16px 24px; text-align:center; color:rgba(224,224,224,0.5);">'
      + '<div class="home-scan-spinner" style="width:20px; height:20px; margin:0 auto 8px;"></div>'
      + 'Running DCF analysis for ' + _esc(symbol) + '\u2026</div>';
    spinTr.appendChild(spinTd);

    // Insert after comps content, comps header, entry content, entry header, or action row
    var compsContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="comps"]');
    var compsHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="comps"]');
    var entryContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="entry"]');
    var entryHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="entry"]');
    var insertAfter = compsContent || compsHeader || entryContent || entryHeader || actionRow;
    insertAfter.parentNode.insertBefore(spinTr, insertAfter.nextSibling);

    _fetchDcfAnalysis(symbol, company, false);
  }

  function _fetchDcfAnalysis(symbol, company, forceFresh) {
    var fetchPromise;
    if (!forceFresh) {
      fetchPromise = fetch('http://localhost:8100/api/valuation/dcf/' + encodeURIComponent(symbol))
        .then(function(res) {
          if (res.ok) return res.json();
          return _postDcfAnalysis(symbol);
        });
    } else {
      fetchPromise = _postDcfAnalysis(symbol);
    }

    fetchPromise
      .then(function(raw) {
        if (raw.ok === false) throw new Error(raw.error || 'DCF analysis failed');
        _dcfCache[symbol] = { data: raw, analyzedAt: new Date().toISOString() };
        _updateBtnState(symbol, 'dcf');
        _buildAccordionPanel(symbol, company, 'dcf');
      })
      .catch(function(err) {
        _removeSpinner(symbol, 'dcf');
        _dcfPanelOpen[symbol] = false;
        _updateBtnState(symbol, 'dcf');
        var actionRow = scope.querySelector('.ce-action-row[data-symbol="' + _esc(symbol) + '"]');
        if (!actionRow) return;
        var errTr = document.createElement('tr');
        errTr.className = 'ce-analysis-panel';
        errTr.setAttribute('data-symbol', symbol);
        errTr.setAttribute('data-type', 'dcf');
        var errTd = document.createElement('td');
        errTd.colSpan = _getColCount();
        errTd.innerHTML = '<div style="padding:12px 24px; color:#ff5a5a; font-size:13px;">'
          + '\u26A0 DCF analysis failed: ' + _esc(err.message)
          + ' <button class="ce-panel-dismiss" style="margin-left:12px; padding:2px 8px; border-radius:4px; font-size:11px; '
          + 'background:none; color:rgba(224,224,224,0.4); border:1px solid rgba(255,255,255,0.08); cursor:pointer;">Dismiss</button></div>';
        errTr.appendChild(errTd);
        var compsContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="comps"]');
        var compsHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="comps"]');
        var entryContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="entry"]');
        var entryHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="entry"]');
        var ia = compsContent || compsHeader || entryContent || entryHeader || actionRow;
        ia.parentNode.insertBefore(errTr, ia.nextSibling);
        var dismiss = errTd.querySelector('.ce-panel-dismiss');
        if (dismiss) dismiss.addEventListener('click', function() { errTr.parentNode.removeChild(errTr); });
      });
  }

  function _postDcfAnalysis(symbol) {
    return fetch('http://localhost:8100/api/valuation/dcf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: symbol }),
    }).then(function(res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    });
  }

  // ── EVA/ROIC Analysis ──
  function _closeEvaPanel() {
    var panels = scope.querySelectorAll('.ce-analysis-panel[data-type="eva"], .ce-analysis-header[data-type="eva"], .ce-analysis-content[data-type="eva"]');
    for (var i = 0; i < panels.length; i++) panels[i].parentNode.removeChild(panels[i]);
    _evaPanelOpen = {};
    var btns = scope.querySelectorAll('.ce-eva-btn');
    for (var b = 0; b < btns.length; b++) _updateBtnState(btns[b].getAttribute('data-symbol'), 'eva');
  }

  function _handleEvaAnalysis(symbol, company) {
    var hdr = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="eva"]');
    if (hdr) {
      _togglePanel(symbol, 'eva');
      return;
    }
    _evaPanelOpen[symbol] = true;
    _updateBtnState(symbol, 'eva');

    var actionRow = scope.querySelector('.ce-action-row[data-symbol="' + _esc(symbol) + '"]');
    if (!actionRow) return;
    var colCount = _getColCount();

    var spinTr = document.createElement('tr');
    spinTr.className = 'ce-analysis-panel ce-analysis-spinner';
    spinTr.setAttribute('data-symbol', symbol);
    spinTr.setAttribute('data-type', 'eva');
    var spinTd = document.createElement('td');
    spinTd.colSpan = colCount;
    spinTd.innerHTML = '<div style="padding:16px 24px; text-align:center; color:rgba(224,224,224,0.5);">'
      + '<div class="home-scan-spinner" style="width:20px; height:20px; margin:0 auto 8px;"></div>'
      + 'Running EVA/ROIC analysis for ' + _esc(symbol) + '\u2026</div>';
    spinTr.appendChild(spinTd);

    // Insert after dcf > comps > entry > action row
    var dcfContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="dcf"]');
    var dcfHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="dcf"]');
    var compsContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="comps"]');
    var compsHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="comps"]');
    var entryContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="entry"]');
    var entryHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="entry"]');
    var insertAfter = dcfContent || dcfHeader || compsContent || compsHeader || entryContent || entryHeader || actionRow;
    insertAfter.parentNode.insertBefore(spinTr, insertAfter.nextSibling);

    _fetchEvaAnalysis(symbol, company, false);
  }

  function _fetchEvaAnalysis(symbol, company, forceFresh) {
    var fetchPromise;
    if (!forceFresh) {
      fetchPromise = fetch('http://localhost:8100/api/valuation/eva/' + encodeURIComponent(symbol))
        .then(function(res) {
          if (res.ok) return res.json();
          return _postEvaAnalysis(symbol);
        });
    } else {
      fetchPromise = _postEvaAnalysis(symbol);
    }

    fetchPromise
      .then(function(raw) {
        if (raw.ok === false) throw new Error(raw.error || 'EVA analysis failed');
        _evaCache[symbol] = { data: raw, analyzedAt: new Date().toISOString() };
        _updateBtnState(symbol, 'eva');
        _buildAccordionPanel(symbol, company, 'eva');
      })
      .catch(function(err) {
        _removeSpinner(symbol, 'eva');
        _evaPanelOpen[symbol] = false;
        _updateBtnState(symbol, 'eva');
        var actionRow = scope.querySelector('.ce-action-row[data-symbol="' + _esc(symbol) + '"]');
        if (!actionRow) return;
        var errTr = document.createElement('tr');
        errTr.className = 'ce-analysis-panel';
        errTr.setAttribute('data-symbol', symbol);
        errTr.setAttribute('data-type', 'eva');
        var errTd = document.createElement('td');
        errTd.colSpan = _getColCount();
        errTd.innerHTML = '<div style="padding:12px 24px; color:#ff5a5a; font-size:13px;">'
          + '\u26A0 EVA analysis failed: ' + _esc(err.message)
          + ' <button class="ce-panel-dismiss" style="margin-left:12px; padding:2px 8px; border-radius:4px; font-size:11px; '
          + 'background:none; color:rgba(224,224,224,0.4); border:1px solid rgba(255,255,255,0.08); cursor:pointer;">Dismiss</button></div>';
        errTr.appendChild(errTd);
        var dcfContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="dcf"]');
        var dcfHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="dcf"]');
        var compsContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="comps"]');
        var compsHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="comps"]');
        var entryContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="entry"]');
        var entryHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="entry"]');
        var ia = dcfContent || dcfHeader || compsContent || compsHeader || entryContent || entryHeader || actionRow;
        ia.parentNode.insertBefore(errTr, ia.nextSibling);
        var dismiss = errTd.querySelector('.ce-panel-dismiss');
        if (dismiss) dismiss.addEventListener('click', function() { errTr.parentNode.removeChild(errTr); });
      });
  }

  function _postEvaAnalysis(symbol) {
    return fetch('http://localhost:8100/api/valuation/eva', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: symbol }),
    }).then(function(res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    });
  }

  function _handleCompsAnalysis(symbol, company) {
    var hdr = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="comps"]');
    if (hdr) {
      _togglePanel(symbol, 'comps');
      return;
    }
    _compsPanelOpen[symbol] = true;
    _updateBtnState(symbol, 'comps');

    var actionRow = scope.querySelector('.ce-action-row[data-symbol="' + _esc(symbol) + '"]');
    if (!actionRow) return;
    var colCount = _getColCount();

    var spinTr = document.createElement('tr');
    spinTr.className = 'ce-analysis-panel ce-analysis-spinner';
    spinTr.setAttribute('data-symbol', symbol);
    spinTr.setAttribute('data-type', 'comps');
    var spinTd = document.createElement('td');
    spinTd.colSpan = colCount;
    spinTd.innerHTML = '<div style="padding:16px 24px; text-align:center; color:rgba(224,224,224,0.5);">'
      + '<div class="home-scan-spinner" style="width:20px; height:20px; margin:0 auto 8px;"></div>'
      + 'Running comparable company analysis for ' + _esc(symbol) + '\u2026</div>';
    spinTr.appendChild(spinTd);

    // Insert after entry content row if exists, else after action row
    var entryContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="entry"]');
    var entryHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="entry"]');
    var insertAfter = entryContent || entryHeader || actionRow;
    insertAfter.parentNode.insertBefore(spinTr, insertAfter.nextSibling);

    _fetchCompsAnalysis(symbol, company, null, false);
  }

  function _fetchCompsAnalysis(symbol, company, _unused, forceFresh) {
    var fetchPromise;
    if (!forceFresh) {
      fetchPromise = fetch('http://localhost:8100/api/valuation/comps/' + encodeURIComponent(symbol))
        .then(function(res) {
          if (res.ok) return res.json();
          return _postCompsAnalysis(symbol);
        });
    } else {
      fetchPromise = _postCompsAnalysis(symbol);
    }

    fetchPromise
      .then(function(raw) {
        if (raw.ok === false) throw new Error(raw.error || 'Analysis failed');
        var data = _normalizeCompsData(raw);
        _compsCache[symbol] = { data: data, analyzedAt: new Date().toISOString() };
        _updateBtnState(symbol, 'comps');
        _buildAccordionPanel(symbol, company, 'comps');
      })
      .catch(function(err) {
        _removeSpinner(symbol, 'comps');
        _compsPanelOpen[symbol] = false;
        _updateBtnState(symbol, 'comps');
        var actionRow = scope.querySelector('.ce-action-row[data-symbol="' + _esc(symbol) + '"]');
        if (!actionRow) return;
        var errTr = document.createElement('tr');
        errTr.className = 'ce-analysis-panel';
        errTr.setAttribute('data-symbol', symbol);
        errTr.setAttribute('data-type', 'comps');
        var errTd = document.createElement('td');
        errTd.colSpan = _getColCount();
        errTd.innerHTML = '<div style="padding:12px 24px; color:#ff5a5a; font-size:13px;">'
          + '\u26A0 Comps analysis failed: ' + _esc(err.message)
          + ' <button class="ce-panel-dismiss" style="margin-left:12px; padding:2px 8px; border-radius:4px; font-size:11px; '
          + 'background:none; color:rgba(224,224,224,0.4); border:1px solid rgba(255,255,255,0.08); cursor:pointer;">Dismiss</button></div>';
        errTr.appendChild(errTd);
        // Insert after entry panels or action row
        var entryContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="entry"]');
        var entryHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="entry"]');
        var ia = entryContent || entryHeader || actionRow;
        ia.parentNode.insertBefore(errTr, ia.nextSibling);
        var dismiss = errTd.querySelector('.ce-panel-dismiss');
        if (dismiss) dismiss.addEventListener('click', function() { errTr.parentNode.removeChild(errTr); });
      });
  }

  function _postCompsAnalysis(symbol) {
    return fetch('http://localhost:8100/api/valuation/comps', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: symbol }),
    }).then(function(res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    });
  }

  // ── Shared accordion helpers ──

  function _getColCount() {
    var headerRow = scope.querySelector('#ce-table-container thead tr');
    return headerRow ? headerRow.querySelectorAll('th').length : 13;
  }

  function _removeSpinner(symbol, type) {
    var spin = scope.querySelector('.ce-analysis-spinner[data-symbol="' + symbol + '"][data-type="' + type + '"]');
    if (spin) spin.parentNode.removeChild(spin);
  }

  function _getRecIcon(rec) {
    if (rec === 'ENTER_NOW') return '\u2705';
    if (rec === 'WAIT') return '\u23F3';
    if (rec === 'AVOID') return '\u26D4';
    return '\u2753';
  }

  function _buildAccordionPanel(symbol, company, type) {
    _removeSpinner(symbol, type);

    // Remove any existing header+content for this symbol+type
    var old1 = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="' + type + '"]');
    var old2 = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="' + type + '"]');
    if (old1) old1.parentNode.removeChild(old1);
    if (old2) old2.parentNode.removeChild(old2);

    var cacheMap = { entry: _entryCache, comps: _compsCache, dcf: _dcfCache, eva: _evaCache };
    var cache = cacheMap[type] ? cacheMap[type][symbol] : null;
    if (!cache) return;
    var data = cache.data;
    var analyzedAt = cache.analyzedAt;
    var ago = _timeAgo(analyzedAt);
    var colCount = _getColCount();
    var openMap = { entry: _entryPanelOpen, comps: _compsPanelOpen, dcf: _dcfPanelOpen, eva: _evaPanelOpen };
    var isOpen = openMap[type] ? !!openMap[type][symbol] : false;

    // Build summary text for the header row
    var summaryHtml = '';
    if (type === 'entry') {
      var rec = data.recommendation || '\u2014';
      var conv = data.conviction != null ? data.conviction : '\u2014';
      var entryPrice = data.suggested_entry;
      summaryHtml = '<span class="ce-rec-badge ' + _recBadgeClass(rec) + '" style="padding:2px 8px; font-size:11px;">'
        + _getRecIcon(rec) + ' ' + _esc(rec.replace(/_/g, ' ')) + ' ' + conv + '/100</span>';
      if (entryPrice != null) summaryHtml += '&nbsp;&nbsp;&nbsp;$' + Number(entryPrice).toFixed(2) + ' entry';
    } else if (type === 'comps') {
      var val = data.valuation || {};
      var verdict = val.verdict || '';
      var upside = val.upside_pct;
      var fv = val.fair_value_composite;
      summaryHtml = '<span style="color:' + _verdictColor(verdict) + '; font-weight:500;">' + _esc(verdict.replace(/_/g, ' ')) + '</span>';
      if (upside != null) summaryHtml += ' ' + (upside >= 0 ? '+' : '') + Number(upside).toFixed(0) + '%';
      if (fv != null) summaryHtml += '&nbsp;&nbsp;&nbsp;FV $' + Number(fv).toFixed(0);
    } else if (type === 'dcf') {
      // DCF summary
      var dcfVal = data.valuation || {};
      var dcfVerdict = dcfVal.verdict || '';
      var dcfUpside = dcfVal.upside_pct;
      var dcfIv = dcfVal.intrinsic_value_per_share;
      summaryHtml = '<span style="color:' + _verdictColor(dcfVerdict) + '; font-weight:500;">' + _esc(dcfVerdict.replace(/_/g, ' ')) + '</span>';
      if (dcfUpside != null) summaryHtml += ' ' + (dcfUpside >= 0 ? '+' : '') + Number(dcfUpside).toFixed(0) + '%';
      if (dcfIv != null) summaryHtml += '&nbsp;&nbsp;&nbsp;IV $' + Number(dcfIv).toFixed(2);
    } else {
      // EVA summary
      var evaGrade = data.grade || (data.roic_analysis || {}).grade || '';
      var evaRoic = (data.roic_analysis || {}).roic;
      var evaWacc = (data.wacc || {}).wacc;
      var evaSpread = (evaRoic != null && evaWacc != null) ? evaRoic - evaWacc : null;
      summaryHtml = _renderGradeBadge(evaGrade);
      if (evaRoic != null) summaryHtml += ' ' + (evaRoic * 100).toFixed(1) + '% ROIC';
      if (evaSpread != null) summaryHtml += '&nbsp;&nbsp;&nbsp;' + (evaSpread >= 0 ? '+' : '') + (evaSpread * 100).toFixed(1) + '% spread';
    }

    var labelMap = { entry: 'Entry Point Analysis', comps: 'Comps Analysis', dcf: 'DCF Analysis', eva: 'EVA/ROIC Analysis' };
    var label = labelMap[type] || type;

    // Header row
    var hdrTr = document.createElement('tr');
    hdrTr.className = 'ce-analysis-header';
    hdrTr.setAttribute('data-symbol', symbol);
    hdrTr.setAttribute('data-type', type);
    hdrTr.style.cssText = 'border-bottom:1px solid rgba(255,255,255,0.04);';
    var hdrTd = document.createElement('td');
    hdrTd.colSpan = colCount;
    hdrTd.innerHTML = '<div class="ce-ah-inner">'
      + '<span class="ce-ah-chevron' + (isOpen ? ' open' : '') + '">\u25B6</span>'
      + '<span class="ce-ah-label">' + _esc(label) + '</span>'
      + '<span class="ce-ah-summary">' + summaryHtml + '</span>'
      + '<span class="ce-ah-age">' + _esc(ago) + '</span>'
      + '</div>';
    hdrTr.appendChild(hdrTd);

    // Content row
    var cntTr = document.createElement('tr');
    cntTr.className = 'ce-analysis-content';
    cntTr.setAttribute('data-symbol', symbol);
    cntTr.setAttribute('data-type', type);
    var cntTd = document.createElement('td');
    cntTd.colSpan = colCount;
    var rendererMap = { entry: _renderEntryAnalysis, comps: _renderCompsAnalysis, dcf: _renderDcfAnalysis, eva: _renderEvaAnalysis };
    var renderer = rendererMap[type] || _renderEntryAnalysis;
    var innerHtml = renderer(data, symbol, company);
    cntTd.innerHTML = '<div class="ce-ac-wrapper' + (isOpen ? ' open' : '') + '">'
      + '<div class="ce-ac-inner">' + innerHtml + '</div></div>';
    cntTr.appendChild(cntTd);

    // Find insertion point
    var actionRow = scope.querySelector('.ce-action-row[data-symbol="' + _esc(symbol) + '"]');
    if (!actionRow) return;

    if (type === 'entry') {
      // Entry goes right after action row
      actionRow.parentNode.insertBefore(cntTr, actionRow.nextSibling);
      actionRow.parentNode.insertBefore(hdrTr, actionRow.nextSibling);
    } else if (type === 'comps') {
      // Comps goes after entry panels if they exist
      var entryContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="entry"]');
      var entryHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="entry"]');
      var ia = entryContent || entryHeader || actionRow;
      ia.parentNode.insertBefore(cntTr, ia.nextSibling);
      ia.parentNode.insertBefore(hdrTr, ia.nextSibling);
    } else if (type === 'dcf') {
      // DCF goes after comps > entry > action row
      var compsContent2 = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="comps"]');
      var compsHeader2 = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="comps"]');
      var entryContent2 = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="entry"]');
      var entryHeader2 = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="entry"]');
      var ia2 = compsContent2 || compsHeader2 || entryContent2 || entryHeader2 || actionRow;
      ia2.parentNode.insertBefore(cntTr, ia2.nextSibling);
      ia2.parentNode.insertBefore(hdrTr, ia2.nextSibling);
    } else {
      // EVA goes after dcf > comps > entry > action row
      var dcfContent3 = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="dcf"]');
      var dcfHeader3 = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="dcf"]');
      var compsContent3 = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="comps"]');
      var compsHeader3 = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="comps"]');
      var entryContent3 = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="entry"]');
      var entryHeader3 = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="entry"]');
      var ia3 = dcfContent3 || dcfHeader3 || compsContent3 || compsHeader3 || entryContent3 || entryHeader3 || actionRow;
      ia3.parentNode.insertBefore(cntTr, ia3.nextSibling);
      ia3.parentNode.insertBefore(hdrTr, ia3.nextSibling);
    }

    // Wire header click
    hdrTr.addEventListener('click', function(e) {
      e.stopPropagation();
      _togglePanel(symbol, type);
    });

    // Wire content buttons
    _wireAccordionButtons(symbol, company, type, data, cntTr);

    // If opened, animate in
    if (isOpen) {
      var wrapper = cntTd.querySelector('.ce-ac-wrapper');
      if (wrapper) {
        wrapper.classList.remove('open');
        requestAnimationFrame(function() {
          wrapper.classList.add('open');
        });
      }
    }
  }

  function _togglePanel(symbol, type) {
    var hdr = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="' + type + '"]');
    var cnt = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="' + type + '"]');
    if (!hdr || !cnt) return;

    var wrapper = cnt.querySelector('.ce-ac-wrapper');
    var chevron = hdr.querySelector('.ce-ah-chevron');
    var isOpen = wrapper && wrapper.classList.contains('open');

    if (isOpen) {
      // Collapse
      if (wrapper) wrapper.classList.remove('open');
      if (chevron) chevron.classList.remove('open');
      if (type === 'entry') { delete _entryPanelOpen[symbol]; }
      else if (type === 'comps') { delete _compsPanelOpen[symbol]; }
      else if (type === 'dcf') { delete _dcfPanelOpen[symbol]; }
      else { delete _evaPanelOpen[symbol]; }
    } else {
      // Expand
      if (wrapper) wrapper.classList.add('open');
      if (chevron) chevron.classList.add('open');
      if (type === 'entry') { _entryPanelOpen[symbol] = true; }
      else if (type === 'comps') { _compsPanelOpen[symbol] = true; }
      else if (type === 'dcf') { _dcfPanelOpen[symbol] = true; }
      else { _evaPanelOpen[symbol] = true; }
    }
    _updateBtnState(symbol, type);
  }

  function _wireAccordionButtons(symbol, company, type, data, cntTr) {
    // Refresh
    var refreshBtns = cntTr.querySelectorAll('.ce-panel-refresh');
    for (var r = 0; r < refreshBtns.length; r++) {
      refreshBtns[r].addEventListener('click', function(e) {
        e.stopPropagation();
        _refreshAnalysis(symbol, company, type);
      });
    }

    // Buy buttons
    if (type === 'entry') {
      var entryBuyBtn = cntTr.querySelector('.ce-entry-buy');
      if (entryBuyBtn && data && data.suggested_entry != null) {
        entryBuyBtn.addEventListener('click', function(e) {
          e.stopPropagation();
          _buyStockAtPrice(symbol, data.suggested_entry, company);
        });
      }
    } else if (type === 'comps') {
      var compsBuyBtn = cntTr.querySelector('.ce-comps-buy');
      if (compsBuyBtn) {
        compsBuyBtn.addEventListener('click', function(e) {
          e.stopPropagation();
          _buyStock(symbol, company);
        });
      }
      var compsEntryBtn = cntTr.querySelector('.ce-comps-entry');
      if (compsEntryBtn) {
        compsEntryBtn.addEventListener('click', function(e) {
          e.stopPropagation();
          _entryAnalysis(symbol, company);
        });
      }
    } else if (type === 'dcf') {
      // DCF buy button
      var dcfBuyBtn = cntTr.querySelector('.ce-dcf-buy');
      if (dcfBuyBtn) {
        dcfBuyBtn.addEventListener('click', function(e) {
          e.stopPropagation();
          _buyStock(symbol, company);
        });
      }
    } else {
      // EVA buy button
      var evaBuyBtn = cntTr.querySelector('.ce-eva-buy');
      if (evaBuyBtn) {
        evaBuyBtn.addEventListener('click', function(e) {
          e.stopPropagation();
          _buyStock(symbol, company);
        });
      }
    }
  }

  function _recBadgeClass(rec) {
    if (rec === 'ENTER_NOW') return 'ce-rec-enter';
    if (rec === 'WAIT') return 'ce-rec-wait';
    if (rec === 'AVOID') return 'ce-rec-avoid';
    return '';
  }

  function _refreshAnalysis(symbol, company, type) {
    // Remove existing accordion and rebuild with fresh data
    var hdr = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="' + type + '"]');
    var cnt = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="' + type + '"]');
    if (hdr) hdr.parentNode.removeChild(hdr);
    if (cnt) cnt.parentNode.removeChild(cnt);

    // Set as open and show spinner
    if (type === 'entry') { _entryPanelOpen[symbol] = true; }
    else if (type === 'comps') { _compsPanelOpen[symbol] = true; }
    else if (type === 'dcf') { _dcfPanelOpen[symbol] = true; }
    else { _evaPanelOpen[symbol] = true; }

    var actionRow = scope.querySelector('.ce-action-row[data-symbol="' + _esc(symbol) + '"]');
    if (!actionRow) return;
    var colCount = _getColCount();

    var spinTr = document.createElement('tr');
    spinTr.className = 'ce-analysis-panel ce-analysis-spinner';
    spinTr.setAttribute('data-symbol', symbol);
    spinTr.setAttribute('data-type', type);
    var spinTd = document.createElement('td');
    spinTd.colSpan = colCount;
    spinTd.innerHTML = '<div style="padding:16px 24px; text-align:center; color:rgba(224,224,224,0.5);">'
      + '<div class="home-scan-spinner" style="width:20px; height:20px; margin:0 auto 8px;"></div>'
      + 'Re-running analysis\u2026</div>';
    spinTr.appendChild(spinTd);

    // Insert after the right anchor
    var entryContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="entry"]');
    var entryHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="entry"]');
    var compsContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="comps"]');
    var compsHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="comps"]');
    var dcfContent = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="dcf"]');
    var dcfHeader = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="dcf"]');
    var insertAfter;
    if (type === 'entry') {
      insertAfter = actionRow;
    } else if (type === 'comps') {
      insertAfter = entryContent || entryHeader || actionRow;
    } else if (type === 'dcf') {
      insertAfter = compsContent || compsHeader || entryContent || entryHeader || actionRow;
    } else {
      insertAfter = dcfContent || dcfHeader || compsContent || compsHeader || entryContent || entryHeader || actionRow;
    }
    insertAfter.parentNode.insertBefore(spinTr, insertAfter.nextSibling);

    if (type === 'entry') {
      _fetchEntryAnalysis(symbol, company, null, true);
    } else if (type === 'comps') {
      _fetchCompsAnalysis(symbol, company, null, true);
    } else if (type === 'dcf') {
      _fetchDcfAnalysis(symbol, company, true);
    } else {
      _fetchEvaAnalysis(symbol, company, true);
    }
  }

  function _getPanelTd(symbol, type) {
    var panel = scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="' + type + '"]');
    return panel ? panel.querySelector('td') : null;
  }

  function _collapsePanel(symbol, type) {
    _togglePanel(symbol, type);
  }

  function _updateBtnState(symbol, type) {
    var selectorMap = { entry: '.ce-entry-btn', comps: '.ce-comps-btn', dcf: '.ce-dcf-btn', eva: '.ce-eva-btn' };
    var selector = selectorMap[type] || '.ce-entry-btn';
    var btn = scope.querySelector(selector + '[data-symbol="' + symbol + '"]');
    if (!btn) return;
    var openMap = { entry: _entryPanelOpen, comps: _compsPanelOpen, dcf: _dcfPanelOpen, eva: _evaPanelOpen };
    var cacheMap = { entry: _entryCache, comps: _compsCache, dcf: _dcfCache, eva: _evaCache };
    var labelMap = { entry: 'Entry \uD83D\uDD0D', comps: 'Comps \uD83D\uDCCA', dcf: 'DCF \uD83D\uDCC8', eva: 'EVA \uD83C\uDFDB' };
    var shortMap = { entry: 'Entry', comps: 'Comps', dcf: 'DCF', eva: 'EVA' };
    var isOpen = openMap[type] && openMap[type][symbol];
    var hasCache = cacheMap[type] && !!cacheMap[type][symbol];
    var hasServerCache = _analysisStatus[symbol] && _analysisStatus[symbol][type];

    if (isOpen) {
      btn.innerHTML = shortMap[type] + ' \u25BC';
    } else if (hasCache || hasServerCache) {
      btn.innerHTML = labelMap[type]
        + ' <span style="color:#00c853; font-size:0.6rem;">\u25CF</span>';
    } else {
      btn.innerHTML = labelMap[type];
    }
  }

  function _loadAnalysisStatus() {
    fetch('http://localhost:8100/api/analyses/status')
      .then(function(res) { if (res.ok) return res.json(); return null; })
      .then(function(data) {
        if (!data) return;
        var syms = data.symbols || data;
        if (typeof syms !== 'object') return;
        _analysisStatus = syms;
        var types = ['entry', 'comps', 'dcf', 'eva'];
        var selectors = ['.ce-entry-btn', '.ce-comps-btn', '.ce-dcf-btn', '.ce-eva-btn'];
        for (var t = 0; t < types.length; t++) {
          var btns = scope.querySelectorAll(selectors[t]);
          for (var b = 0; b < btns.length; b++) {
            _updateBtnState(btns[b].getAttribute('data-symbol'), types[t]);
          }
        }
      })
      .catch(function() { /* status unavailable */ });
  }

  function _normalizeCompsData(raw) {
    var v = (raw.verdict && typeof raw.verdict === 'object') ? raw.verdict : {};
    var fv = raw.fair_value || {};
    var subj = raw.subject || {};
    var pg = raw.peer_group || {};
    var conf = (raw.confidence && typeof raw.confidence === 'object') ? raw.confidence : {};

    // Build implied-price lookup from fair_value
    var impliedMap = {};
    var impliedList = fv.implied_by_multiple || [];
    for (var ii = 0; ii < impliedList.length; ii++) {
      impliedMap[impliedList[ii].multiple] = impliedList[ii].implied_price;
    }

    // comparison_table from multiples_comparison
    raw.comparison_table = (raw.multiples_comparison || [])
      .filter(function(m) { return m.usable; })
      .map(function(m) {
        return {
          name: m.name,
          subject: m.subject,
          peer_median: m.peer_median,
          vs_peers_pct: m.premium_pct,
          implied_price: impliedMap[m.name] || null
        };
      });

    // peers from peer_group.details (market_cap_m → raw dollars for _fmtMcap)
    raw.peers = (pg.details || []).map(function(p) {
      return {
        symbol: p.symbol,
        market_cap: p.market_cap_m ? p.market_cap_m * 1e6 : null,
        pe: p.pe,
        ev_ebitda: p.ev_ebitda,
        ps: p.ps
      };
    });

    // llm
    raw.llm_analysis = raw.llm_narrative || null;

    // valuation wrapper the renderer reads
    raw.valuation = {
      verdict: v.label || '',
      fair_value_composite: fv.composite_fair_value || null,
      current_price: subj.current_price || fv.current_price || null,
      upside_pct: fv.upside_pct != null ? fv.upside_pct : null,
      sector: subj.sector || '',
      confidence: conf.level || ''
    };

    raw.current_price = subj.current_price || fv.current_price || null;
    return raw;
  }

  function _verdictColor(verdict) {
    if (!verdict) return 'rgba(224,224,224,0.6)';
    var v = String(verdict).toUpperCase();
    if (v.indexOf('UNDERVALUED') !== -1) return '#00c853';
    if (v.indexOf('OVERVALUED') !== -1) return '#ff1744';
    return '#ffd600';
  }

  function _signalIcon(pctDiff) {
    if (pctDiff == null) return { icon: '\u25CF', color: '#9ca3af', label: 'fair' };
    if (pctDiff < -3) return { icon: '\u25B2', color: '#4ade80', label: 'under' };
    if (pctDiff > 3) return { icon: '\u25BC', color: '#f87171', label: 'over' };
    return { icon: '\u25CF', color: '#9ca3af', label: 'fair' };
  }

  function _fmtMcap(val) {
    if (val == null || val === 0) return '--';
    if (val >= 1e12) return '$' + (val / 1e12).toFixed(1) + 'T';
    if (val >= 1e9) return '$' + (val / 1e9).toFixed(1) + 'B';
    if (val >= 1e6) return '$' + (val / 1e6).toFixed(0) + 'M';
    return '$' + val.toLocaleString();
  }

  function _fmtNum(val, decimals) {
    if (val == null) return '--';
    return Number(val).toFixed(decimals != null ? decimals : 1);
  }

  function _fmtPct(val) {
    if (val == null) return '--';
    var s = (val >= 0 ? '+' : '') + Number(val).toFixed(1) + '%';
    return s;
  }

  function _renderCompsAnalysis(data, symbol, company) {
    var val = data.valuation || {};
    var verdict = val.verdict || data.verdict || '';
    var fairValue = val.fair_value_composite || val.fair_value || null;
    var rangeLow = val.fair_value_low || (val.fair_value_range && val.fair_value_range[0]) || null;
    var rangeHigh = val.fair_value_high || (val.fair_value_range && val.fair_value_range[1]) || null;
    var currentPrice = val.current_price || data.current_price || null;
    var upside = val.upside_pct || null;
    var peerCount = (data.peers && data.peers.length) || 0;
    var sector = val.sector || (company && company.sector) || '';
    var mcapRange = val.market_cap_range || '';
    var confidence = val.confidence || data.confidence || '';
    var compTable = data.comparison_table || val.comparison_table || [];
    var peers = data.peers || [];
    var llmAnalysis = data.llm_analysis || null;
    var llmAvailable = data.llm_available !== false;

    var h = '';

    // VERDICT
    h += '<div style="font-size:0.68rem; letter-spacing:1px; color:rgba(255,255,255,0.4); margin-bottom:6px; text-transform:uppercase;">Verdict</div>';
    h += '<div style="background:rgba(255,255,255,0.03); border-radius:8px; padding:12px; border:1px solid rgba(255,255,255,0.06); margin-bottom:14px;">';
    h += '<div style="font-size:0.92rem; font-weight:700; color:' + _verdictColor(verdict) + ';">'
       + _esc(verdict.replace(/_/g, ' '));
    if (upside != null) h += ' \u2014 ' + (upside >= 0 ? '+' : '') + Number(upside).toFixed(1) + '% upside';
    h += '</div>';
    h += '<div style="font-size:0.82rem; color:rgba(224,224,224,0.7); margin-top:4px;">';
    if (fairValue != null) {
      h += 'Fair value: <span style="font-weight:600; color:#e0e0e0;">$' + Number(fairValue).toFixed(2) + '</span>';
      if (rangeLow != null && rangeHigh != null) h += ' (range $' + Math.round(rangeLow) + ' \u2013 $' + Math.round(rangeHigh) + ')';
    }
    if (currentPrice != null) h += '&nbsp;&nbsp;Current: <span style="font-weight:600; color:#e0e0e0;">$' + Number(currentPrice).toFixed(2) + '</span>';
    h += '</div>';
    h += '<div style="font-size:0.75rem; color:rgba(224,224,224,0.45); margin-top:2px;">';
    var meta = [];
    if (peerCount > 0) meta.push(peerCount + ' peers');
    if (sector) meta.push('in ' + _esc(sector));
    if (mcapRange) meta.push(_esc(mcapRange));
    if (confidence) meta.push('Confidence: ' + _esc(confidence));
    h += meta.join(' \u00B7 ');
    h += '</div>';
    h += '</div>';

    // MULTIPLES + PEERS side-by-side
    h += '<div class="ce-comps-grid">';

    // LEFT: MULTIPLES COMPARISON
    if (compTable.length > 0) {
      h += '<div class="ce-col">';
      h += '<div class="ce-col-header">Multiples Comparison</div>';
      h += '<div style="overflow-x:auto;">';
      h += '<table style="width:100%; border-collapse:collapse; font-size:0.8rem;">';
      h += '<thead><tr style="border-bottom:1px solid rgba(255,255,255,0.08);">';
      h += '<th style="' + _thStyle + '">Multiple</th>';
      h += '<th style="' + _thStyle + 'text-align:right;">' + _esc(symbol) + '</th>';
      h += '<th style="' + _thStyle + 'text-align:right;">Peers</th>';
      h += '<th style="' + _thStyle + 'text-align:right;">vs Peers</th>';
      h += '<th style="' + _thStyle + 'text-align:right;">Implied</th>';
      h += '<th style="' + _thStyle + 'text-align:center;">Signal</th>';
      h += '</tr></thead><tbody>';

      for (var mi = 0; mi < compTable.length; mi++) {
        var m = compTable[mi];
        var mName = m.multiple || m.metric || m.name || '';
        var mCompany = m.company_value || m.subject || null;
        var mPeers = m.peer_median || m.peers || null;
        var mVsPeers = m.vs_peers_pct || m.premium_discount_pct || null;
        var mImplied = m.implied_price || null;
        var sig = _signalIcon(mVsPeers);
        var vsPeerColor = mVsPeers == null ? 'rgba(224,224,224,0.5)' : (mVsPeers < -3 ? '#4ade80' : mVsPeers > 3 ? '#f87171' : '#9ca3af');

        h += '<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">';
        h += '<td style="' + _tdStyle + 'color:rgba(224,224,224,0.7);">' + _esc(mName) + '</td>';
        h += '<td style="' + _tdStyle + 'text-align:right; color:#e0e0e0;">' + _fmtNum(mCompany) + '</td>';
        h += '<td style="' + _tdStyle + 'text-align:right; color:rgba(224,224,224,0.6);">' + _fmtNum(mPeers) + '</td>';
        h += '<td style="' + _tdStyle + 'text-align:right; color:' + vsPeerColor + ';">' + _fmtPct(mVsPeers) + '</td>';
        h += '<td style="' + _tdStyle + 'text-align:right; color:rgba(224,224,224,0.6);">' + (mImplied != null ? '$' + _fmtNum(mImplied, 2) : '--') + '</td>';
        h += '<td style="' + _tdStyle + 'text-align:center; color:' + sig.color + ';">' + sig.icon + ' ' + sig.label + '</td>';
        h += '</tr>';
      }

      h += '</tbody></table></div>';
      h += '</div>'; // close multiples column
    }

    // RIGHT: PEER GROUP
    if (peers.length > 0) {
      h += '<div class="ce-col">';
      h += '<div class="ce-col-header">Peer Group</div>';
      h += '<div style="overflow-x:auto;">';
      h += '<table style="width:100%; border-collapse:collapse; font-size:0.8rem;">';
      h += '<thead><tr style="border-bottom:1px solid rgba(255,255,255,0.08);">';
      h += '<th style="' + _thStyle + '">Symbol</th>';
      h += '<th style="' + _thStyle + '">Name</th>';
      h += '<th style="' + _thStyle + 'text-align:right;">Mkt Cap</th>';
      h += '<th style="' + _thStyle + 'text-align:right;">P/E</th>';
      h += '<th style="' + _thStyle + 'text-align:right;">EV/EBITDA</th>';
      h += '<th style="' + _thStyle + 'text-align:right;">P/S</th>';
      h += '</tr></thead><tbody>';

      for (var pi = 0; pi < peers.length; pi++) {
        var p = peers[pi];
        h += '<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">';
        h += '<td style="' + _tdStyle + 'font-weight:600; color:#e0e0e0;">' + _esc(p.symbol || p.ticker || '') + '</td>';
        h += '<td style="' + _tdStyle + 'color:rgba(224,224,224,0.7); max-width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">' + _esc(p.name || p.company_name || '') + '</td>';
        h += '<td style="' + _tdStyle + 'text-align:right; color:rgba(224,224,224,0.6);">' + _fmtMcap(p.market_cap) + '</td>';
        h += '<td style="' + _tdStyle + 'text-align:right; color:rgba(224,224,224,0.6);">' + _fmtNum(p.pe || p.pe_ratio, 1) + '</td>';
        h += '<td style="' + _tdStyle + 'text-align:right; color:rgba(224,224,224,0.6);">' + _fmtNum(p.ev_ebitda, 1) + '</td>';
        h += '<td style="' + _tdStyle + 'text-align:right; color:rgba(224,224,224,0.6);">' + _fmtNum(p.ps || p.ps_ratio, 1) + '</td>';
        h += '</tr>';
      }

      // Median row
      var medians = data.peer_medians || val.peer_medians || null;
      if (medians) {
        h += '<tr style="border-top:2px solid rgba(255,255,255,0.1);">';
        h += '<td style="' + _tdStyle + 'font-weight:600; color:rgba(224,224,224,0.5);" colspan="3">Median</td>';
        h += '<td style="' + _tdStyle + 'text-align:right; font-weight:600; color:rgba(224,224,224,0.7);">' + _fmtNum(medians.pe || medians.pe_ratio, 1) + '</td>';
        h += '<td style="' + _tdStyle + 'text-align:right; font-weight:600; color:rgba(224,224,224,0.7);">' + _fmtNum(medians.ev_ebitda, 1) + '</td>';
        h += '<td style="' + _tdStyle + 'text-align:right; font-weight:600; color:rgba(224,224,224,0.7);">' + _fmtNum(medians.ps || medians.ps_ratio, 1) + '</td>';
        h += '</tr>';
      }

      h += '</tbody></table></div>';
      h += '</div>'; // close peers column
    }

    h += '</div>'; // close ce-comps-grid

    // AI ANALYSIS
    if (llmAvailable && llmAnalysis) {
      var narrative = typeof llmAnalysis === 'string' ? llmAnalysis : (llmAnalysis.narrative || llmAnalysis.summary || llmAnalysis.text || '');
      var mostRelevant = typeof llmAnalysis === 'object' ? (llmAnalysis.most_relevant_multiple || '') : '';
      var peerQuality = typeof llmAnalysis === 'object' ? (llmAnalysis.peer_quality || '') : '';

      if (narrative) {
        h += '<div style="font-size:0.68rem; letter-spacing:1px; color:rgba(255,255,255,0.4); margin-bottom:6px; text-transform:uppercase;">AI Analysis</div>';
        h += '<div style="background:rgba(255,255,255,0.03); border-radius:8px; padding:12px; border:1px solid rgba(255,255,255,0.06); margin-bottom:14px;">';
        h += '<div style="font-size:0.82rem; line-height:1.6; color:rgba(255,255,255,0.8);">' + _esc(narrative) + '</div>';
        if (mostRelevant || peerQuality) {
          h += '<div style="margin-top:8px; font-size:0.75rem; color:rgba(224,224,224,0.5);">';
          var parts = [];
          if (mostRelevant) parts.push('Most relevant: <span style="color:rgba(224,224,224,0.7);">' + _esc(mostRelevant) + '</span>');
          if (peerQuality) parts.push('Peer quality: <span style="color:rgba(224,224,224,0.7);">' + _esc(peerQuality) + '</span>');
          h += parts.join(' \u00B7 ');
          h += '</div>';
        }
        h += '</div>';
      }
    }

    // Footer actions
    h += '<div class="ce-panel-actions">';
    if (currentPrice != null) {
      h += '<button class="ce-comps-buy ce-btn-buy">Buy at $' + Number(currentPrice).toFixed(2) + ' \u25B6</button>';
    }
    h += '<button class="ce-comps-entry ce-btn-refresh">Entry Analysis \uD83D\uDD0D</button>';
    h += '<button class="ce-panel-refresh ce-btn-refresh">\uD83D\uDD04 Refresh</button>';
    h += '</div>';

    return h;
  }

  // ── DCF Analysis renderer ──
  function _renderDcfAnalysis(data, symbol, company) {
    var val = data.valuation || {};
    var assumptions = data.assumptions || {};
    var projections = data.projections || [];
    var sensitivity = data.sensitivity || [];
    var currentPrice = data.current_price || val.current_price || null;
    var intrinsicValue = val.intrinsic_value_per_share || null;
    var verdict = val.verdict || '';
    var upsidePct = val.upside_pct;
    var confidence = data.confidence || val.confidence || '';
    var pvFcfs = val.pv_of_fcfs || null;
    var pvTerminal = val.pv_of_terminal || null;
    var enterpriseValue = val.enterprise_value || null;
    var llmAnalysis = data.llm_analysis || data.llm_narrative || null;
    var llmAvailable = data.llm_available !== false;
    var caveats = data.caveats || [];

    var verdictColor = upsidePct != null
      ? (upsidePct > 5 ? '#4ade80' : (upsidePct > -5 ? '#fbbf24' : '#f87171'))
      : 'rgba(255,255,255,0.6)';

    var h = '';

    // ── INTRINSIC VALUE section ──
    h += '<div class="ce-col" style="margin-bottom:16px;">';
    h += '<div class="ce-col-header">Intrinsic Value</div>';
    h += '<div class="dcf-headline">';
    h += '<span class="dcf-iv">Intrinsic Value: <strong style="color:' + verdictColor + ';">'
       + (intrinsicValue != null ? '$' + Number(intrinsicValue).toFixed(2) : '--') + '</strong></span>';
    h += '<span class="dcf-current" style="color:rgba(255,255,255,0.5);">Current: '
       + (currentPrice != null ? '$' + Number(currentPrice).toFixed(2) : '--') + '</span>';
    h += '</div>';
    h += '<div class="dcf-verdict" style="color:' + verdictColor + ';">'
       + _esc(verdict.replace(/_/g, ' '));
    if (upsidePct != null) h += ' \u2014 ' + (upsidePct >= 0 ? '+' : '') + Number(upsidePct).toFixed(1) + '%';
    h += '</div>';
    if (confidence) h += '<div class="dcf-confidence">Confidence: ' + _esc(confidence) + '</div>';

    // EV Breakdown bars
    if (pvFcfs != null && pvTerminal != null && enterpriseValue) {
      var pvFcfPct = Math.round(pvFcfs / enterpriseValue * 100);
      var pvTvPct = 100 - pvFcfPct;
      h += '<div class="dcf-ev-breakdown">';
      h += '<div style="font-size:11px; color:rgba(255,255,255,0.4); margin-bottom:6px; text-transform:uppercase; letter-spacing:1px;">EV Breakdown</div>';
      h += '<div class="ev-bar-row">';
      h += '<span class="ev-bar-label">PV of FCFs</span>';
      h += '<div class="ev-bar-track"><div class="ev-bar-fill" style="width:' + pvFcfPct + '%; background:#60a5fa;"></div></div>';
      h += '<span class="ev-bar-value">' + _fmtMcap(pvFcfs) + ' (' + pvFcfPct + '%)</span>';
      h += '</div>';
      h += '<div class="ev-bar-row">';
      h += '<span class="ev-bar-label">PV of Terminal</span>';
      h += '<div class="ev-bar-track"><div class="ev-bar-fill" style="width:' + pvTvPct + '%; background:#a78bfa;"></div></div>';
      h += '<span class="ev-bar-value">' + _fmtMcap(pvTerminal) + ' (' + pvTvPct + '%)</span>';
      h += '</div>';
      h += '</div>';
    }
    h += '</div>'; // close intrinsic value section

    // ── ASSUMPTIONS + PROJECTIONS side by side ──
    h += '<div class="ce-dual-col" style="margin-bottom:16px;">';

    // LEFT: Assumptions
    h += '<div class="ce-col">';
    h += '<div class="ce-col-header">Assumptions</div>';
    h += '<div class="dcf-assumptions-grid">';
    var assKeys = [
      { key: 'wacc', label: 'WACC', fmt: 'pct' },
      { key: 'terminal_growth', label: 'Terminal g', fmt: 'pct' },
      { key: 'risk_free_rate', label: 'Risk-free', fmt: 'pct' },
      { key: 'beta', label: 'Beta', fmt: 'num2' },
      { key: 'tax_rate', label: 'Tax rate', fmt: 'pct' },
      { key: 'fcf_margin', label: 'FCF margin', fmt: 'pct' },
      { key: 'net_debt', label: 'Net debt', fmt: 'money' },
      { key: 'shares_outstanding', label: 'Shares', fmt: 'money' },
    ];
    for (var ai = 0; ai < assKeys.length; ai++) {
      var ak = assKeys[ai];
      var av = assumptions[ak.key];
      if (av == null) continue;
      var avStr;
      if (ak.fmt === 'pct') avStr = (Number(av) * 100).toFixed(1) + '%';
      else if (ak.fmt === 'num2') avStr = Number(av).toFixed(2);
      else if (ak.fmt === 'money') avStr = _fmtMcap(av);
      else avStr = String(av);
      h += '<div class="dcf-assum-row"><span class="dcf-assum-lbl">' + _esc(ak.label) + '</span><span class="dcf-assum-val">' + _esc(avStr) + '</span></div>';
    }
    h += '</div>';
    h += '</div>'; // close assumptions

    // RIGHT: Projections
    h += '<div class="ce-col">';
    h += '<div class="ce-col-header">Projections</div>';
    if (projections.length > 0) {
      h += '<table class="projections-table"><thead><tr>';
      h += '<th>Year</th><th>Revenue</th><th>FCF</th><th>Growth</th><th>PV</th>';
      h += '</tr></thead><tbody>';
      for (var pi = 0; pi < projections.length; pi++) {
        var p = projections[pi];
        var growthVal = p.growth != null ? (Number(p.growth) * 100).toFixed(1) + '%' : '--';
        h += '<tr>';
        h += '<td>' + _esc(String(p.year || (pi + 1))) + '</td>';
        h += '<td>' + _fmtMcap(p.revenue) + '</td>';
        h += '<td>' + _fmtMcap(p.fcf) + '</td>';
        h += '<td>' + growthVal + '</td>';
        h += '<td>' + _fmtMcap(p.pv || p.present_value) + '</td>';
        h += '</tr>';
      }
      h += '</tbody></table>';
    } else {
      h += '<div style="color:rgba(255,255,255,0.3); font-size:13px; padding:12px 0;">No projection data available</div>';
    }
    h += '</div>'; // close projections
    h += '</div>'; // close dual-col

    // ── SENSITIVITY TABLE ──
    if (sensitivity.length > 0) {
      h += '<div class="ce-col" style="margin-bottom:16px;">';
      h += '<div class="ce-col-header">Sensitivity Analysis</div>';
      h += _renderSensitivityTable(sensitivity, currentPrice);
      h += '</div>';
    }

    // ── AI ANALYSIS ──
    if (llmAvailable && llmAnalysis) {
      var narrative = typeof llmAnalysis === 'string' ? llmAnalysis : (llmAnalysis.narrative || llmAnalysis.summary || llmAnalysis.text || '');
      if (narrative) {
        h += '<div class="ce-col" style="margin-bottom:16px;">';
        h += '<div class="ce-col-header">AI Analysis</div>';
        h += '<div style="font-size:13px; line-height:1.6; color:rgba(255,255,255,0.8);">' + _esc(narrative) + '</div>';
        h += '</div>';
      }
    }

    // ── CAVEATS ──
    if (caveats.length > 0) {
      h += '<div style="margin-bottom:12px;">';
      h += '<div style="font-size:11px; letter-spacing:1px; color:rgba(255,255,255,0.3); text-transform:uppercase; margin-bottom:4px;">Caveats</div>';
      h += '<ul class="dcf-caveats" style="margin:0; padding-left:16px;">';
      for (var ci = 0; ci < caveats.length; ci++) {
        h += '<li>' + _esc(caveats[ci]) + '</li>';
      }
      h += '</ul></div>';
    }

    // ── Footer actions ──
    h += '<div class="ce-panel-actions">';
    if (currentPrice != null) {
      h += '<button class="ce-dcf-buy ce-btn-buy">Buy at $' + Number(currentPrice).toFixed(2) + ' \u25B6</button>';
    }
    h += '<button class="ce-panel-refresh ce-btn-refresh">\uD83D\uDD04 Refresh</button>';
    h += '</div>';

    return h;
  }

  function _renderSensitivityTable(sensitivity, currentPrice) {
    // Detect growth rate columns from the data
    var growthRates = [];
    if (sensitivity.length > 0 && sensitivity[0].values) {
      var keys = Object.keys(sensitivity[0].values);
      keys.sort(function(a, b) { return parseFloat(a) - parseFloat(b); });
      growthRates = keys;
    }
    if (growthRates.length === 0) return '';

    // Find base row (middle row)
    var baseIdx = Math.floor(sensitivity.length / 2);

    var h = '<table class="sensitivity-table">';
    h += '<thead><tr><th>WACC \\ Term g</th>';
    for (var gi = 0; gi < growthRates.length; gi++) {
      h += '<th>' + _esc(growthRates[gi]) + '</th>';
    }
    h += '</tr></thead><tbody>';

    for (var ri = 0; ri < sensitivity.length; ri++) {
      var row = sensitivity[ri];
      var isBase = ri === baseIdx;
      var wacc = row.wacc != null ? (typeof row.wacc === 'number' && row.wacc < 1 ? (row.wacc * 100).toFixed(1) + '%' : row.wacc + '%') : '--';
      h += '<tr class="' + (isBase ? 'sensitivity-base-row' : '') + '">';
      h += '<td style="font-weight:500; color:rgba(255,255,255,0.6);">' + wacc + (isBase ? ' \u2190' : '') + '</td>';

      for (var gj = 0; gj < growthRates.length; gj++) {
        var cellVal = row.values[growthRates[gj]];
        if (cellVal == null) {
          h += '<td>\u2014</td>';
          continue;
        }
        var isBaseCell = isBase && gj === Math.floor(growthRates.length / 2);
        var cellColor = 'rgba(255,255,255,0.6)';
        if (currentPrice != null) {
          if (cellVal > currentPrice * 1.05) cellColor = '#4ade80';
          else if (cellVal < currentPrice * 0.95) cellColor = '#f87171';
          else cellColor = '#fbbf24';
        }
        h += '<td style="color:' + cellColor + ';'
           + (isBaseCell ? ' font-weight:600; text-decoration:underline;' : '')
           + '">$' + Math.round(cellVal) + '</td>';
      }
      h += '</tr>';
    }
    h += '</tbody></table>';

    if (currentPrice != null) {
      h += '<div class="sensitivity-legend">'
         + 'Current price: $' + Number(currentPrice).toFixed(2) + ' \u00B7 '
         + '<span style="color:#4ade80;">Green</span> = undervalued \u00B7 '
         + '<span style="color:#f87171;">Red</span> = overvalued'
         + '</div>';
    }
    return h;
  }

  // ── EVA/ROIC Analysis renderer ──
  function _renderEvaAnalysis(data, symbol, company) {
    var roicAnalysis = data.roic_analysis || {};
    var waccData = data.wacc || {};
    var comparison = data.comparison || {};
    var capitalStructure = data.capital_structure || {};
    var evaData = data.eva || {};
    var qualitySignals = data.quality_signals || [];
    var llmAnalysis = data.llm_analysis || data.llm_narrative || null;
    var llmAvailable = data.llm_available !== false;
    var currentPrice = data.current_price || null;

    var roic = roicAnalysis.roic || 0;
    var wacc = waccData.wacc || 0;
    var spread = roic - wacc;
    var grade = data.grade || roicAnalysis.grade || '';
    var moat = data.moat || roicAnalysis.moat || '';
    var sustainability = data.sustainability || roicAnalysis.sustainability || '';

    var h = '';

    // ── VALUE CREATION section ──
    h += '<div class="ce-col" style="margin-bottom:16px;">';
    h += '<div class="ce-col-header">Value Creation</div>';
    h += _renderSpreadVisual(roic, wacc);
    h += '<div style="display:flex; align-items:center; gap:10px; margin:8px 0;">';
    h += '<span style="font-size:12px; color:rgba(255,255,255,0.5);">Grade:</span> ';
    h += _renderGradeBadge(grade);
    var gradeDesc = {
      'ELITE': 'Exceptional value creator',
      'STRONG': 'Consistent value creator',
      'GOOD': 'Solid value creator',
      'MARGINAL': 'Marginal value creation',
      'DESTROYING': 'Destroying shareholder value'
    };
    if (gradeDesc[grade]) h += '<span style="font-size:12px; color:rgba(255,255,255,0.45); margin-left:4px;">\u2014 ' + gradeDesc[grade] + '</span>';
    h += '</div>';

    // Annual EVA
    var annualEva = evaData.annual_eva || evaData.eva;
    var evaPerShare = evaData.eva_per_share;
    if (annualEva != null) {
      h += '<div class="eva-annual">Annual EVA: <strong style="color:' + (annualEva >= 0 ? '#4ade80' : '#f87171') + ';">'
         + _fmtMcap(annualEva) + '</strong>';
      if (evaPerShare != null) h += ' ($' + Number(evaPerShare).toFixed(2) + '/share)';
      h += '</div>';
    }
    h += '</div>'; // close value creation

    // ── CAPITAL RETURNS + QUALITY SIGNALS side by side ──
    h += '<div class="ce-dual-col" style="margin-bottom:16px;">';

    // LEFT: Capital Returns
    h += '<div class="ce-col">';
    h += '<div class="ce-col-header">Capital Returns</div>';
    h += _renderReturnBars(data);
    h += '</div>';

    // RIGHT: Quality Signals
    h += '<div class="ce-col">';
    h += '<div class="ce-col-header">Quality Signals</div>';
    if (qualitySignals.length > 0) {
      for (var qi = 0; qi < qualitySignals.length; qi++) {
        var qs = qualitySignals[qi];
        var qIcon, qColor;
        if (typeof qs === 'string') {
          qIcon = '\u25CF'; qColor = '#9ca3af';
          h += '<div class="eva-quality-signal" style="color:' + qColor + ';">' + qIcon + ' ' + _esc(qs) + '</div>';
        } else {
          var dir = qs.direction || qs.signal_type || 'neutral';
          if (dir === 'positive' || dir === 'bullish') { qIcon = '\u25B2'; qColor = '#4ade80'; }
          else if (dir === 'negative' || dir === 'bearish') { qIcon = '\u25BC'; qColor = '#f87171'; }
          else { qIcon = '\u25CF'; qColor = '#9ca3af'; }
          h += '<div class="eva-quality-signal" style="color:' + qColor + ';">' + qIcon + ' ' + _esc(qs.text || qs.label || qs.description || '') + '</div>';
        }
      }
    } else {
      // Build from comparison data
      var compMetrics = [
        { label: 'ROE', value: comparison.roe, threshold: 0.15, suffix: '%' },
        { label: 'ROA', value: comparison.roa, threshold: 0.08, suffix: '%' },
        { label: 'Op margin', value: comparison.operating_margin, threshold: 0.20, suffix: '%' },
        { label: 'Reinvestment rate', value: comparison.reinvestment_rate, threshold: null, suffix: '%' },
      ];
      for (var ci = 0; ci < compMetrics.length; ci++) {
        var cm = compMetrics[ci];
        if (cm.value == null) continue;
        var cmVal = cm.value > 1 ? cm.value : cm.value * 100;
        var cmIcon, cmColor, cmNote;
        if (cm.threshold != null) {
          if (cm.value >= cm.threshold) { cmIcon = '\u25B2'; cmColor = '#4ade80'; cmNote = 'strong'; }
          else if (cm.value >= cm.threshold * 0.5) { cmIcon = '\u25CF'; cmColor = '#fbbf24'; cmNote = 'moderate'; }
          else { cmIcon = '\u25BC'; cmColor = '#f87171'; cmNote = 'weak'; }
        } else { cmIcon = '\u25CF'; cmColor = '#9ca3af'; cmNote = ''; }
        h += '<div class="eva-quality-signal" style="color:' + cmColor + ';">' + cmIcon + ' '
           + _esc(cm.label) + ' ' + cmVal.toFixed(0) + '%'
           + (cmNote ? ' \u2014 ' + cmNote : '') + '</div>';
      }
    }
    if (moat) h += '<div style="margin-top:8px; font-size:13px; color:rgba(255,255,255,0.6);">Moat: <strong>' + _esc(moat) + '</strong></div>';
    if (sustainability) h += '<div style="font-size:13px; color:rgba(255,255,255,0.6);">ROIC sustainability: <strong>' + _esc(sustainability) + '</strong></div>';
    h += '</div>'; // close quality signals
    h += '</div>'; // close dual-col

    // ── CAPITAL STRUCTURE ──
    h += '<div class="ce-col" style="margin-bottom:16px;">';
    h += '<div class="ce-col-header">Capital Structure</div>';
    var capRows = [
      { label: 'Invested Capital', value: capitalStructure.invested_capital, indent: 0 },
      { label: 'Equity', value: capitalStructure.equity, indent: 1, pct: capitalStructure.equity_pct },
      { label: 'Debt', value: capitalStructure.debt || capitalStructure.total_debt, indent: 1 },
      { label: 'Cash', value: capitalStructure.cash ? -Math.abs(capitalStructure.cash) : null, indent: 1 },
    ];
    for (var cri = 0; cri < capRows.length; cri++) {
      var cr = capRows[cri];
      if (cr.value == null) continue;
      var prefix = cr.indent ? '\u251C\u2500 ' : '';
      if (cr.indent && cri === capRows.length - 1) prefix = '\u2514\u2500 ';
      h += '<div class="eva-cap-structure-row">';
      h += '<span class="cap-label">' + (cr.indent ? '&nbsp;&nbsp;&nbsp;' : '') + prefix + _esc(cr.label) + '</span>';
      h += '<span class="cap-val">' + _fmtMcap(cr.value);
      if (cr.pct != null) h += ' (' + (cr.pct * 100).toFixed(1) + '%)';
      h += '</span></div>';
    }
    // NOPAT, Operating Income, Tax Rate
    var nopat = data.nopat || capitalStructure.nopat;
    var opIncome = data.operating_income || capitalStructure.operating_income;
    var taxRate = data.tax_rate || capitalStructure.tax_rate;
    if (nopat != null) h += '<div class="eva-cap-structure-row" style="margin-top:8px;"><span class="cap-label">NOPAT</span><span class="cap-val">' + _fmtMcap(nopat) + '</span></div>';
    if (opIncome != null) h += '<div class="eva-cap-structure-row"><span class="cap-label">Operating Income</span><span class="cap-val">' + _fmtMcap(opIncome) + '</span></div>';
    if (taxRate != null) h += '<div class="eva-cap-structure-row"><span class="cap-label">Tax Rate</span><span class="cap-val">' + (taxRate > 1 ? taxRate.toFixed(1) : (taxRate * 100).toFixed(1)) + '%</span></div>';
    h += '</div>'; // close capital structure

    // ── EVA VALUATION ──
    var evaImplied = evaData.implied_value || evaData.eva_implied_value;
    if (evaImplied != null || currentPrice != null) {
      h += '<div class="ce-col" style="margin-bottom:16px;">';
      h += '<div class="ce-col-header">EVA Valuation</div>';
      var valLine = '';
      if (evaImplied != null) valLine += 'EVA-implied value: <strong style="color:' + (evaImplied >= (currentPrice || 0) ? '#4ade80' : '#f87171') + ';">$' + Number(evaImplied).toFixed(2) + '</strong>';
      if (currentPrice != null) valLine += '&nbsp;&nbsp;&nbsp;Current: <strong style="color:rgba(255,255,255,0.8);">$' + Number(currentPrice).toFixed(2) + '</strong>';
      h += '<div style="font-size:14px; color:rgba(255,255,255,0.7);">' + valLine + '</div>';
      h += '<div class="eva-valuation-note">Note: EVA perpetuity is conservative \u2014 assumes current EVA is sustainable but doesn\'t grow. Market prices in growth.</div>';
      h += '</div>';
    }

    // ── AI ANALYSIS ──
    if (llmAvailable && llmAnalysis) {
      var narrative = typeof llmAnalysis === 'string' ? llmAnalysis : (llmAnalysis.narrative || llmAnalysis.summary || llmAnalysis.text || '');
      if (narrative) {
        h += '<div class="ce-col" style="margin-bottom:16px;">';
        h += '<div class="ce-col-header">AI Analysis</div>';
        h += '<div style="font-size:13px; line-height:1.6; color:rgba(255,255,255,0.8);">' + _esc(narrative) + '</div>';
        h += '</div>';
      }
    }

    // ── Footer actions ──
    h += '<div class="ce-panel-actions">';
    if (currentPrice != null) {
      h += '<button class="ce-eva-buy ce-btn-buy">Buy at $' + Number(currentPrice).toFixed(2) + ' \u25B6</button>';
    }
    h += '<button class="ce-panel-refresh ce-btn-refresh">\uD83D\uDD04 Refresh</button>';
    h += '</div>';

    return h;
  }

  function _renderSpreadVisual(roic, wacc) {
    var spread = roic - wacc;
    var spreadColor = spread > 0.10 ? '#4ade80' :
                      spread > 0.03 ? '#86efac' :
                      spread > 0 ? '#fbbf24' : '#f87171';
    var maxVal = Math.max(roic, wacc, 0.20);
    var roicWidth = Math.min(100, (roic / maxVal) * 100);
    var waccWidth = Math.min(100, (wacc / maxVal) * 100);

    return '<div class="spread-visual">'
      + '<div class="spread-row">'
      + '<span class="spread-label">ROIC</span>'
      + '<div class="spread-bar-track"><div class="spread-bar-fill" style="width:' + roicWidth + '%; background:#4ade80;"></div></div>'
      + '<span class="spread-value" style="color:#4ade80">' + (roic * 100).toFixed(1) + '%</span>'
      + '</div>'
      + '<div class="spread-center">'
      + '<span class="spread-badge" style="color:' + spreadColor + '; border-color:' + spreadColor + ';">'
      + 'SPREAD ' + (spread > 0 ? '+' : '') + (spread * 100).toFixed(1) + '%'
      + '</span>'
      + '</div>'
      + '<div class="spread-row">'
      + '<span class="spread-label">WACC</span>'
      + '<div class="spread-bar-track"><div class="spread-bar-fill" style="width:' + waccWidth + '%; background:#f87171;"></div></div>'
      + '<span class="spread-value" style="color:#f87171">' + (wacc * 100).toFixed(1) + '%</span>'
      + '</div>'
      + '</div>';
  }

  function _renderGradeBadge(grade) {
    var styles = {
      'ELITE':      { bg: '#064e3b', border: '#059669', color: '#34d399' },
      'STRONG':     { bg: '#065f46', border: '#10b981', color: '#6ee7b7' },
      'GOOD':       { bg: '#1c4532', border: '#38a169', color: '#68d391' },
      'MARGINAL':   { bg: '#78350f', border: '#d97706', color: '#fbbf24' },
      'DESTROYING': { bg: '#7f1d1d', border: '#dc2626', color: '#f87171' }
    };
    var s = styles[grade] || styles['MARGINAL'] || { bg: '#78350f', border: '#d97706', color: '#fbbf24' };
    return '<span class="eva-grade-badge" style="background:' + s.bg + '; border:1px solid ' + s.border + '; color:' + s.color + ';">' + _esc(grade || '--') + '</span>';
  }

  function _renderReturnBars(data) {
    var roicAnalysis = data.roic_analysis || {};
    var comparison = data.comparison || {};
    var waccData = data.wacc || {};
    var metrics = [
      { label: 'ROIC', value: roicAnalysis.roic, max: 0.40 },
      { label: 'ROE', value: comparison.roe, max: 0.50 },
      { label: 'ROA', value: comparison.roa, max: 0.30 },
      { label: 'WACC', value: waccData.wacc, max: 0.20, color: '#f87171' }
    ];

    var h = '';
    for (var mi = 0; mi < metrics.length; mi++) {
      var m = metrics[mi];
      if (m.value == null) continue;
      var pct = Math.min(100, (Math.abs(m.value) / m.max) * 100);
      var color = m.color || (m.value > 0.15 ? '#4ade80' : m.value > 0.08 ? '#fbbf24' : '#f87171');
      var display = Math.abs(m.value) > 1 ? (m.value * 100).toFixed(0) + '%' : (m.value * 100).toFixed(1) + '%';
      h += '<div class="return-bar-row">'
         + '<span class="return-label">' + _esc(m.label) + '</span>'
         + '<span class="return-value" style="color:' + color + ';">' + display + '</span>'
         + '<div class="return-bar-track"><div class="return-bar-fill" style="width:' + pct + '%; background:' + color + ';"></div></div>'
         + '</div>';
    }
    return h;
  }

  function _renderEntryAnalysis(data, symbol, company) {
    var rec = data.recommendation || 'UNKNOWN';
    var conviction = data.conviction != null ? data.conviction : null;
    var summary = data.summary || '';
    var entry = data.suggested_entry;
    var stop = data.suggested_stop;
    var current = data.current_price;
    var rr = data.risk_reward;
    var target = data.price_target;
    var targetSrc = data.price_target_source || '';
    var comp = data.components || {};
    var signals = data.signals || [];

    var h = '';

    // ── Two-column layout ──
    h += '<div class="ce-dual-col">';

    // ── LEFT: Engine column ──
    h += '<div class="ce-col">';
    h += '<div class="ce-col-header">Engine</div>';
    h += _renderRecBadge(rec, conviction);
    if (summary) h += '<div style="margin-top:6px; font-size:0.78rem; color:rgba(224,224,224,0.6); line-height:1.5;">' + _esc(summary) + '</div>';

    // Score bars
    h += '<div style="display:grid; gap:6px; margin:12px 0 10px;">';
    var pillars = [
      { key: 'technical', label: 'Technical' },
      { key: 'market_context', label: 'Market' },
      { key: 'valuation_timing', label: 'Valuation' },
      { key: 'catalyst', label: 'Catalyst' },
    ];
    for (var pi = 0; pi < pillars.length; pi++) {
      var pdata = comp[pillars[pi].key] || {};
      var pscore = pdata.score != null ? pdata.score : null;
      h += _renderScoreBar(pillars[pi].label, pscore);
    }
    h += '</div>';

    // Price levels grid (Entry, Current, Target, Stop, R/R)
    h += '<div class="ce-price-grid">';
    h += _metricCell('Entry', entry != null ? '$' + Number(entry).toFixed(2) : '--', '#00e0c3');
    h += _metricCell('Current', current != null ? '$' + Number(current).toFixed(2) : '--', 'rgba(224,224,224,0.7)');
    h += _metricCellWithSub('Target',
           target != null ? '$' + Number(target).toFixed(2) : '--',
           targetSrc ? targetSrc.replace(/_/g, ' ') : '',
           '#a78bfa');
    h += _metricCell('Stop', stop != null ? '$' + Number(stop).toFixed(2) : '--', '#ff5252');
    h += _metricCell('R/R', _formatRR(rr), '#ffd600');
    h += '</div>';

    // Signals (properly formatted)
    if (signals.length > 0) {
      h += '<div style="font-size:0.68rem; color:rgba(224,224,224,0.4); text-transform:uppercase; letter-spacing:0.04em; margin-bottom:4px;">Signals</div>';
      for (var si = 0; si < signals.length && si < 8; si++) {
        h += _renderSignal(signals[si]);
      }
    }
    h += '</div>'; // close engine column

    // ── RIGHT: AI Analysis column ──
    h += '<div class="ce-col">';
    h += '<div class="ce-col-header">AI Analysis</div>';

    if (data.llm_available) {
      h += _renderRecBadge(data.llm_recommendation || 'WAIT', data.llm_conviction);

      // LLM narrative
      if (data.llm_analysis) {
        h += '<div style="margin-top:8px; font-size:0.8rem; line-height:1.6; color:rgba(255,255,255,0.8);">' + _esc(data.llm_analysis) + '</div>';
      }

      // Key levels from LLM
      if (data.llm_key_levels) {
        var kl = data.llm_key_levels;
        h += '<div style="margin-top:10px; font-size:0.72rem; color:rgba(224,224,224,0.4); text-transform:uppercase; letter-spacing:0.04em;">Key Levels</div>';
        if (kl.buy_below != null) h += '<div style="font-size:0.78rem; color:#4ade80; padding:2px 0;">\u25B6 Buy below $' + Number(kl.buy_below).toFixed(2) + '</div>';
        if (kl.take_profit != null) h += '<div style="font-size:0.78rem; color:#a78bfa; padding:2px 0;">\u2B06 Take profit $' + Number(kl.take_profit).toFixed(2) + '</div>';
        if (kl.stop_loss != null) h += '<div style="font-size:0.78rem; color:#ff5252; padding:2px 0;">\u26D4 Stop loss $' + Number(kl.stop_loss).toFixed(2) + '</div>';
      }

      // Agreement indicator
      h += '<div style="margin-top:12px; font-size:0.78rem; padding:6px 10px; border-radius:4px; '
         + 'background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05);">';
      if (data.llm_agrees_with_engine) {
        h += '\u2705 Agrees with engine';
      } else {
        h += '\u26A0\uFE0F Disagrees with engine';
      }
      h += '</div>';

    } else {
      // LLM unavailable
      h += '<div style="text-align:center; padding:30px 10px; color:rgba(224,224,224,0.3); font-size:0.82rem;">';
      h += 'Model analysis unavailable';
      if (data.llm_error) {
        h += '<br><span style="font-size:0.7rem; opacity:0.5;">' + _esc(data.llm_error) + '</span>';
      }
      h += '</div>';
    }
    h += '</div>'; // close AI column
    h += '</div>'; // close grid

    // ── Footer: Buy + Refresh buttons ──
    h += '<div class="ce-panel-actions">';
    if (entry != null && rec === 'ENTER_NOW') {
      h += '<button class="ce-entry-buy ce-btn-buy">Buy at $' + Number(entry).toFixed(2) + ' \u25B6</button>';
    } else if (entry != null) {
      h += '<button class="ce-entry-buy ce-btn-refresh">Buy at $' + Number(entry).toFixed(2) + ' \u25B6</button>';
    }
    h += '<button class="ce-panel-refresh ce-btn-refresh">\uD83D\uDD04 Refresh</button>';
    h += '</div>';

    return h;
  }

  // ── Recommendation badge (shared for engine + LLM) ──
  function _renderRecBadge(rec, conviction) {
    var recColors = {
      'ENTER_NOW': { bg: 'rgba(0,200,83,0.08)', border: 'rgba(0,200,83,0.25)', text: '#00c853', icon: '\u2705' },
      'WAIT':      { bg: 'rgba(255,214,0,0.06)', border: 'rgba(255,214,0,0.25)', text: '#ffd600', icon: '\u23F3' },
      'AVOID':     { bg: 'rgba(255,23,68,0.06)', border: 'rgba(255,23,68,0.25)', text: '#ff1744', icon: '\u26D4' },
    };
    var rc = recColors[rec] || { bg: 'rgba(255,255,255,0.03)', border: 'rgba(255,255,255,0.1)', text: 'rgba(224,224,224,0.6)', icon: '\u2753' };
    var h = '<div style="margin-top:4px; padding:4px 10px; border-radius:4px; display:inline-block; '
       + 'background:' + rc.bg + '; border:1px solid ' + rc.border + '; color:' + rc.text + '; font-size:0.82rem; font-weight:600;">'
       + rc.icon + ' ' + _esc((rec || '').replace(/_/g, ' '));
    if (conviction != null) h += ' \u2014 ' + conviction + '/100';
    h += '</div>';
    return h;
  }

  // ── Signal rendering (handles object or string signals) ──
  function _renderSignal(sig) {
    var icons = { bullish: '\u25B2', bearish: '\u25BC', neutral: '\u25CF' };
    var colors = { bullish: '#4ade80', bearish: '#f87171', neutral: '#9ca3af' };

    if (typeof sig === 'string') {
      return '<div style="font-size:0.78rem; color:rgba(224,224,224,0.5); padding:1px 0;">\u25CF ' + _esc(sig) + '</div>';
    }

    var direction = sig.direction || 'neutral';
    var icon = icons[direction] || '\u25CF';
    var color = colors[direction] || '#9ca3af';
    var text = sig.signal || sig.text || sig.description || sig.label || JSON.stringify(sig);
    var weight = sig.weight || '';

    return '<div style="color:' + color + '; margin:2px 0; font-size:0.78rem;">'
         + icon + ' ' + _esc(text)
         + (weight ? ' <span style="opacity:0.45; font-size:0.68rem; margin-left:4px;">(' + _esc(weight) + ')</span>' : '')
         + '</div>';
  }

  // ── Format R/R ratio ──
  function _formatRR(rr) {
    if (rr == null) return '--';
    if (typeof rr === 'number') return rr.toFixed(1) + ':1';
    var s = String(rr);
    if (s === '--' || s === 'N/A' || s === '') return '--';
    return s;
  }

  // ── Metric cell with sub-label (for price target source) ──
  function _metricCellWithSub(label, value, sub, color) {
    return '<div style="text-align:center;">'
      + '<div style="font-size:0.68rem; color:rgba(224,224,224,0.4); text-transform:uppercase; letter-spacing:0.03em;">' + _esc(label) + '</div>'
      + '<div style="font-size:0.92rem; font-weight:700; color:' + color + ';">' + _esc(value) + '</div>'
      + (sub ? '<div style="font-size:0.6rem; color:rgba(224,224,224,0.3); margin-top:1px;">(' + _esc(sub) + ')</div>' : '')
      + '</div>';
  }

  function _renderScoreBar(label, score) {
    var pct = score != null ? Math.max(0, Math.min(100, Math.round(score))) : 0;
    var color = score == null ? 'rgba(224,224,224,0.2)' :
                score >= 75 ? '#00c853' :
                score >= 55 ? '#ffd600' :
                score >= 35 ? '#ff9800' : '#ff1744';
    var barBg = score != null ? color + '30' : 'rgba(224,224,224,0.05)';

    var h = '<div style="display:flex; align-items:center; gap:8px;">';
    h += '<span style="font-size:0.72rem; color:rgba(224,224,224,0.5); min-width:65px;">' + _esc(label) + '</span>';
    h += '<div style="flex:1; height:8px; background:' + barBg + '; border-radius:4px; overflow:hidden;">';
    if (score != null) {
      h += '<div style="width:' + pct + '%; height:100%; background:' + color + '; border-radius:4px;"></div>';
    }
    h += '</div>';
    h += '<span style="font-size:0.78rem; font-weight:600; color:' + color + '; min-width:24px; text-align:right;">'
       + (score != null ? Math.round(score) : '--') + '</span>';
    h += '</div>';
    return h;
  }

  function _metricCell(label, value, color) {
    return '<div style="text-align:center;">'
      + '<div style="font-size:0.68rem; color:rgba(224,224,224,0.4); text-transform:uppercase; letter-spacing:0.03em;">' + _esc(label) + '</div>'
      + '<div style="font-size:0.92rem; font-weight:700; color:' + color + ';">' + _esc(value) + '</div>'
      + '</div>';
  }

  // ── Ranked list ──
  function loadRankedList() {
    var url = '/api/company-evaluator/ranked?limit=500';

    fetch(url)
      .then(function(res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function(data) {
        _ceCompanies = data.companies || [];
        populateFilters(_ceCompanies);
        applyFilters();
      })
      .catch(function(err) {
        var el = scope.querySelector('#ce-table-container');
        if (el) {
          el.innerHTML =
            '<div style="text-align:center; padding:40px; color:rgba(224,224,224,0.4);">' +
            '<div style="font-size:1.5rem; margin-bottom:8px;">\u26A0</div>' +
            'Failed to load company evaluations: ' + _esc(err.message) + '<br>' +
            '<small>Is the Company Evaluator service running on Machine 2?</small></div>';
        }
      });
  }

  function renderTable(companies) {
    var container = scope.querySelector('#ce-table-container');
    if (!container) return;

    if (!companies || companies.length === 0) {
      container.innerHTML =
        '<div style="text-align:center; padding:60px; color:rgba(224,224,224,0.3);">' +
        '<div style="font-size:2rem; margin-bottom:12px;">\uD83D\uDCCA</div>' +
        'No companies match current filters.<br>' +
        '<small>Try widening your filters or click "Run Crawler" to evaluate the universe.</small></div>';
      return;
    }

    var hasMcap = false;
    for (var ci = 0; ci < companies.length; ci++) {
      if (companies[ci].market_cap) { hasMcap = true; break; }
    }

    var sortableStyle = _thStyle + 'cursor:pointer; user-select:none;';
    var colCount = 13 + (hasMcap ? 1 : 0); // total columns (no Actions column in header)
    var html = '<table style="width:100%; border-collapse:collapse; font-size:0.85rem;">';

    // Header — NO Actions column
    html += '<thead><tr style="border-bottom:1px solid rgba(255,255,255,0.1);">';
    html += '<th style="' + _thStyle + 'width:40px;">#</th>';
    html += '<th style="' + _thStyle + '">Symbol</th>';
    html += '<th style="' + _thStyle + '">Company</th>';
    html += '<th style="' + _thStyle + '">Sector</th>';
    if (hasMcap) {
      html += '<th style="' + sortableStyle + 'text-align:right;" data-sort="market_cap">Mkt Cap' + _sortIndicator('market_cap') + '</th>';
    }
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="composite_score">Score' + _sortIndicator('composite_score') + '</th>';
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="business_quality">Biz Qual' + _sortIndicator('business_quality') + '</th>';
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="operational_health">Ops Health' + _sortIndicator('operational_health') + '</th>';
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="capital_allocation">Cap Alloc' + _sortIndicator('capital_allocation') + '</th>';
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="growth_quality">Growth' + _sortIndicator('growth_quality') + '</th>';
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="valuation">Valuation' + _sortIndicator('valuation') + '</th>';
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="llm_recommendation">LLM' + _sortIndicator('llm_recommendation') + '</th>';
    html += '<th style="' + _thStyle + '">Updated</th>';
    html += '</tr></thead><tbody>';

    for (var i = 0; i < companies.length; i++) {
      var c = companies[i];
      var scoreColor = _scoreColor(c.composite_score);
      var recBadge = _recBadge(c.llm_recommendation);
      var ps = c.pillar_scores || {};
      var updated = _timeAgo(c.evaluated_at);
      var stale = _isStale(c.evaluated_at);
      var sym = _esc(c.symbol || '');

      var rowStyle = 'border-bottom:none; cursor:pointer;';
      if (stale) rowStyle += ' opacity:0.65;';

      // Row 1: Data row
      html += '<tr class="ce-row" data-symbol="' + sym + '" style="' + rowStyle + '">';
      html += '<td style="' + _tdStyle + 'color:rgba(224,224,224,0.4);">' + (i + 1) + '</td>';
      html += '<td style="' + _tdStyle + 'font-weight:700; color:#e0e0e0;">' + sym + '</td>';
      html += '<td style="' + _tdStyle + 'color:rgba(224,224,224,0.7); max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">' + _esc(c.company_name || '') + '</td>';
      html += '<td style="' + _tdStyle + 'color:rgba(224,224,224,0.5); font-size:0.78rem;">' + _esc(c.sector || '--') + '</td>';
      if (hasMcap) {
        html += '<td style="' + _tdStyle + 'text-align:right; color:rgba(224,224,224,0.6); font-size:0.8rem;">' + _formatMarketCap(c.market_cap) + '</td>';
      }
      html += '<td style="' + _tdStyle + 'text-align:center;"><span style="color:' + scoreColor + '; font-weight:700; font-size:1rem;">' + (c.composite_score != null ? c.composite_score.toFixed(1) : '--') + '</span></td>';
      html += _pillarCell(ps.business_quality);
      html += _pillarCell(ps.operational_health);
      html += _pillarCell(ps.capital_allocation);
      html += _pillarCell(ps.growth_quality);
      html += _pillarCell(ps.valuation);
      html += '<td style="' + _tdStyle + 'text-align:center;">' + recBadge + '</td>';

      var updatedStyle = _tdStyle + 'font-size:0.75rem;';
      if (stale) {
        updatedStyle += ' color:#ff9800;';
        updated = '\u26A0 ' + updated;
      } else {
        updatedStyle += ' color:rgba(224,224,224,0.3);';
      }
      html += '<td style="' + updatedStyle + '">' + _esc(updated) + '</td>';
      html += '</tr>';

      // Row 2: Action row
      html += '<tr class="ce-action-row" data-symbol="' + sym + '" style="border-bottom:1px solid rgba(255,255,255,0.05); background:rgba(255,255,255,0.015);">';
      html += '<td colspan="' + colCount + '" style="padding:4px 10px; white-space:nowrap;">';
      html += _positionBadge(c.symbol || '');
      html += ' <button class="ce-buy-btn" data-symbol="' + sym + '" data-idx="' + i + '" '
        + 'style="padding:2px 8px; border-radius:3px; font-size:0.72rem; font-weight:600; cursor:pointer; '
        + _buyBtnStyle(c.llm_recommendation) + '">Buy \u25B6</button>';
      html += ' <button class="ce-entry-btn" data-symbol="' + sym + '" data-idx="' + i + '" '
        + 'style="padding:2px 8px; border-radius:3px; font-size:0.72rem; font-weight:500; cursor:pointer; '
        + 'background:rgba(255,255,255,0.04); color:rgba(224,224,224,0.35); border:1px solid rgba(255,255,255,0.06);" '
        + 'title="Entry Analysis">Entry \uD83D\uDD0D</button>';
      html += ' <button class="ce-comps-btn" data-symbol="' + sym + '" data-idx="' + i + '" '
        + 'style="padding:2px 8px; border-radius:3px; font-size:0.72rem; font-weight:500; cursor:pointer; '
        + 'background:rgba(255,255,255,0.04); color:rgba(224,224,224,0.35); border:1px solid rgba(255,255,255,0.06);" '
        + 'title="Comparable Company Analysis">Comps \uD83D\uDCCA</button>';
      html += ' <button class="ce-dcf-btn" data-symbol="' + sym + '" data-idx="' + i + '" '
        + 'style="padding:2px 8px; border-radius:3px; font-size:0.72rem; font-weight:500; cursor:pointer; '
        + 'background:rgba(255,255,255,0.04); color:rgba(224,224,224,0.35); border:1px solid rgba(255,255,255,0.06);" '
        + 'title="DCF Valuation">DCF \uD83D\uDCC8</button>';
      html += ' <button class="ce-eva-btn" data-symbol="' + sym + '" data-idx="' + i + '" '
        + 'style="padding:2px 8px; border-radius:3px; font-size:0.72rem; font-weight:500; cursor:pointer; '
        + 'background:rgba(255,255,255,0.04); color:rgba(224,224,224,0.35); border:1px solid rgba(255,255,255,0.06);" '
        + 'title="EVA/ROIC Analysis">EVA \uD83C\uDFDB</button>';
      html += '</td>';
      html += '</tr>';
    }

    html += '</tbody></table>';
    container.innerHTML = html;

    // Bind data row clicks (opens detail drawer)
    var rows = container.querySelectorAll('.ce-row');
    for (var r = 0; r < rows.length; r++) {
      rows[r].addEventListener('click', _onRowClick);
      rows[r].addEventListener('mouseover', function() { this.style.background = 'rgba(255,255,255,0.04)'; });
      rows[r].addEventListener('mouseout', function() { this.style.background = 'transparent'; });
    }

    // Bind sort header clicks
    var sortHeaders = container.querySelectorAll('th[data-sort]');
    for (var si = 0; si < sortHeaders.length; si++) {
      sortHeaders[si].addEventListener('click', (function(col) {
        return function() { _onSortClick(col); };
      })(sortHeaders[si].getAttribute('data-sort')));
      sortHeaders[si].addEventListener('mouseover', function() { this.style.color = '#e0e0e0'; });
      sortHeaders[si].addEventListener('mouseout', function() { this.style.color = 'rgba(224,224,224,0.5)'; });
    }

    // Bind Buy buttons
    var buyBtns = container.querySelectorAll('.ce-buy-btn');
    for (var bi = 0; bi < buyBtns.length; bi++) {
      buyBtns[bi].addEventListener('click', (function(sym, idx) {
        return function(e) {
          e.stopPropagation();
          _buyStock(sym, companies[idx]);
        };
      })(buyBtns[bi].getAttribute('data-symbol'), parseInt(buyBtns[bi].getAttribute('data-idx'), 10)));
    }

    // Bind Entry Analysis buttons
    var entryBtns = container.querySelectorAll('.ce-entry-btn');
    for (var ei = 0; ei < entryBtns.length; ei++) {
      entryBtns[ei].addEventListener('click', (function(sym, idx) {
        return function(e) {
          e.stopPropagation();
          _entryAnalysis(sym, companies[idx]);
        };
      })(entryBtns[ei].getAttribute('data-symbol'), parseInt(entryBtns[ei].getAttribute('data-idx'), 10)));
    }

    // Bind Comps buttons
    var compsBtns = container.querySelectorAll('.ce-comps-btn');
    for (var cbi = 0; cbi < compsBtns.length; cbi++) {
      compsBtns[cbi].addEventListener('click', (function(sym, idx) {
        return function(e) {
          e.stopPropagation();
          _handleCompsAnalysis(sym, companies[idx]);
        };
      })(compsBtns[cbi].getAttribute('data-symbol'), parseInt(compsBtns[cbi].getAttribute('data-idx'), 10)));
    }

    // Bind DCF buttons
    var dcfBtns = container.querySelectorAll('.ce-dcf-btn');
    for (var di = 0; di < dcfBtns.length; di++) {
      dcfBtns[di].addEventListener('click', (function(sym, idx) {
        return function(e) {
          e.stopPropagation();
          _handleDcfAnalysis(sym, companies[idx]);
        };
      })(dcfBtns[di].getAttribute('data-symbol'), parseInt(dcfBtns[di].getAttribute('data-idx'), 10)));
    }

    // Bind EVA buttons
    var evaBtns = container.querySelectorAll('.ce-eva-btn');
    for (var evi = 0; evi < evaBtns.length; evi++) {
      evaBtns[evi].addEventListener('click', (function(sym, idx) {
        return function(e) {
          e.stopPropagation();
          _handleEvaAnalysis(sym, companies[idx]);
        };
      })(evaBtns[evi].getAttribute('data-symbol'), parseInt(evaBtns[evi].getAttribute('data-idx'), 10)));
    }

    // Update button states for cached analyses
    _loadAnalysisStatus();
  }

  function _onRowClick() {
    var sym = this.getAttribute('data-symbol');
    if (sym) openDetail(sym);
  }

  // ── Detail drawer ──
  function openDetail(symbol) {
    var drawer = scope.querySelector('#ce-detail-drawer');
    if (!drawer) return;

    drawer.style.display = 'block';
    drawer.innerHTML = '<div style="text-align:center; padding:40px; color:rgba(224,224,224,0.4);">Loading ' + _esc(symbol) + '...</div>';

    fetch('/api/company-evaluator/company/' + encodeURIComponent(symbol))
      .then(function(res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function(data) {
        renderDetail(drawer, data);
      })
      .catch(function(err) {
        drawer.innerHTML = '<div style="padding:20px; color:#ff1744;">Failed to load: ' + _esc(err.message) + '</div>';
      });
  }

  function closeDetail() {
    var drawer = scope.querySelector('#ce-detail-drawer');
    if (drawer) drawer.style.display = 'none';
  }

  function renderDetail(drawer, data) {
    var ps = data.pillar_scores || {};
    var llm = data.llm_analysis || {};
    var pd = data.pillar_details || {};

    var recColors = { 'STRONG_BUY': '#00c853', 'BUY': '#00c853', 'HOLD': '#ffd600', 'SELL': '#ff9800', 'STRONG_SELL': '#ff1744' };
    var recColor = recColors[llm.recommendation] || '#888';

    var html = '';

    // Close button + header
    html += '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">';
    html += '<div><h5 style="color:var(--accent-primary, #00e0c3); margin:0;">' + _esc(data.symbol) + '</h5>';
    html += '<small style="color:rgba(224,224,224,0.5);">' + _esc(data.company_name || '') + ' \u00B7 ' + _esc(data.sector || '') + '</small></div>';
    html += '<button id="ce-close-drawer-btn" style="background:none; border:none; color:rgba(224,224,224,0.5); font-size:1.5rem; cursor:pointer;">\u00D7</button>';
    html += '</div>';

    // Composite score hero
    html += '<div style="text-align:center; padding:16px; background:rgba(255,255,255,0.03); border-radius:8px; margin-bottom:16px;">';
    html += '<div style="font-size:2.5rem; font-weight:700; color:' + _scoreColor(data.composite_score) + ';">' +
      (data.composite_score != null ? data.composite_score.toFixed(1) : '--') + '</div>';
    html += '<div style="color:rgba(224,224,224,0.5); font-size:0.85rem;">Composite Score</div>';
    if (llm.recommendation) {
      html += '<div style="margin-top:8px;"><span style="background:' + recColor + '20; color:' + recColor +
        '; padding:4px 12px; border-radius:4px; font-weight:600;">' + _esc(llm.recommendation.replace(/_/g, ' ')) + '</span></div>';
    }
    if (llm.conviction) {
      html += '<div style="margin-top:4px; color:rgba(224,224,224,0.4); font-size:0.8rem;">Conviction: ' + _esc(String(llm.conviction)) + '%</div>';
    }
    html += '</div>';

    // Pillar scores bar chart
    html += '<div style="margin-bottom:16px;">';
    html += '<h6 style="color:rgba(224,224,224,0.6); font-size:0.75rem; text-transform:uppercase; letter-spacing:0.04em; margin-bottom:8px;">Pillar Scores</h6>';

    var pillars = [
      ['Business Quality', ps.business_quality, '30%'],
      ['Ops & Health', ps.operational_health, '15%'],
      ['Capital Allocation', ps.capital_allocation, '20%'],
      ['Growth Quality', ps.growth_quality, '20%'],
      ['Valuation', ps.valuation, '15%']
    ];

    for (var pi = 0; pi < pillars.length; pi++) {
      var p = pillars[pi];
      var val = p[1];
      var barWidth = val != null ? Math.max(val, 2) : 0;
      var barColor = _scoreColor(val);
      html += '<div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">';
      html += '<div style="width:120px; font-size:0.78rem; color:rgba(224,224,224,0.6);">' + _esc(p[0]) + ' <span style="color:rgba(224,224,224,0.3);">(' + p[2] + ')</span></div>';
      html += '<div style="flex:1; background:rgba(255,255,255,0.05); border-radius:3px; height:16px; overflow:hidden;">';
      html += '<div style="width:' + barWidth + '%; height:100%; background:' + barColor + '30; border-right:2px solid ' + barColor + ';"></div>';
      html += '</div>';
      html += '<div style="width:32px; text-align:right; font-size:0.85rem; color:' + barColor + '; font-weight:600;">' + (val != null ? val.toFixed(0) : '--') + '</div>';
      html += '</div>';
    }
    html += '</div>';

    // LLM Analysis
    if (llm.summary || llm.thesis) {
      html += '<div style="margin-bottom:16px; background:rgba(255,255,255,0.03); border-radius:6px; padding:12px;">';
      html += '<h6 style="color:var(--accent-primary, #00e0c3); font-size:0.75rem; text-transform:uppercase; margin-bottom:8px;">Investment Analysis</h6>';
      if (llm.summary) html += '<div style="color:rgba(224,224,224,0.8); font-size:0.85rem; margin-bottom:8px;">' + _esc(llm.summary) + '</div>';
      if (llm.thesis) html += '<div style="color:rgba(224,224,224,0.6); font-size:0.82rem; font-style:italic;">' + _esc(llm.thesis) + '</div>';
      html += '</div>';
    }

    // Risks & Catalysts
    var hasRisks = llm.risks && llm.risks.length;
    var hasCatalysts = llm.catalysts && llm.catalysts.length;
    if (hasRisks || hasCatalysts) {
      html += '<div style="display:flex; gap:12px; margin-bottom:16px;">';

      if (hasRisks) {
        html += '<div style="flex:1; background:rgba(255,23,68,0.05); border:1px solid rgba(255,23,68,0.15); border-radius:6px; padding:10px;">';
        html += '<div style="color:#ff1744; font-size:0.7rem; font-weight:600; text-transform:uppercase; margin-bottom:6px;">Risks</div>';
        for (var ri = 0; ri < llm.risks.length; ri++) {
          html += '<div style="color:rgba(224,224,224,0.7); font-size:0.78rem; margin-bottom:3px;">\u2022 ' + _esc(llm.risks[ri]) + '</div>';
        }
        html += '</div>';
      }

      if (hasCatalysts) {
        html += '<div style="flex:1; background:rgba(0,200,83,0.05); border:1px solid rgba(0,200,83,0.15); border-radius:6px; padding:10px;">';
        html += '<div style="color:#00c853; font-size:0.7rem; font-weight:600; text-transform:uppercase; margin-bottom:6px;">Catalysts</div>';
        for (var ci = 0; ci < llm.catalysts.length; ci++) {
          html += '<div style="color:rgba(224,224,224,0.7); font-size:0.78rem; margin-bottom:3px;">\u2022 ' + _esc(llm.catalysts[ci]) + '</div>';
        }
        html += '</div>';
      }

      html += '</div>';
    }

    // Pillar detail sections (expandable)
    var pillarNames = {
      'business_quality': 'Business Quality Metrics',
      'operational_health': 'Operational & Financial Health',
      'capital_allocation': 'Capital Allocation Quality',
      'growth_quality': 'Growth Quality',
      'valuation': 'Valuation & Expectations'
    };

    var pillarKeys = ['business_quality', 'operational_health', 'capital_allocation', 'growth_quality', 'valuation'];
    for (var pdi = 0; pdi < pillarKeys.length; pdi++) {
      var pKey = pillarKeys[pdi];
      var detail = pd[pKey];
      if (!detail || !detail.metrics) continue;

      html += '<details style="margin-bottom:8px; border:1px solid rgba(255,255,255,0.06); border-radius:6px;">';
      html += '<summary style="padding:8px 12px; cursor:pointer; color:rgba(224,224,224,0.6); font-size:0.78rem; font-weight:600;">' +
        _esc(pillarNames[pKey]) + ' \u2014 <span style="color:' + _scoreColor(detail.pillar_score) + ';">' +
        (detail.pillar_score != null ? detail.pillar_score.toFixed(0) : '--') + '</span></summary>';
      html += '<div style="padding:8px 12px;">';

      var metricKeys = Object.keys(detail.metrics);
      for (var mi = 0; mi < metricKeys.length; mi++) {
        var mKey = metricKeys[mi];
        var mVal = detail.metrics[mKey];
        if (mVal == null) continue;
        var displayVal;
        if (typeof mVal === 'number') {
          displayVal = Math.abs(mVal) > 1000000 ? '$' + (mVal / 1000000000).toFixed(1) + 'B' : mVal.toFixed(2);
        } else {
          displayVal = String(mVal);
        }
        var mScore = detail.scores ? detail.scores[mKey] : null;

        html += '<div style="display:flex; justify-content:space-between; padding:2px 0; font-size:0.78rem;">';
        html += '<span style="color:rgba(224,224,224,0.5);">' + _esc(mKey.replace(/_/g, ' ')) + '</span>';
        html += '<span style="color:rgba(224,224,224,0.8);">' + _esc(displayVal);
        if (mScore != null) html += ' <span style="color:' + _scoreColor(mScore) + '; font-size:0.7rem;">(' + mScore.toFixed(0) + ')</span>';
        html += '</span></div>';
      }

      html += '</div></details>';
    }

    // Metadata
    html += '<div style="margin-top:16px; padding-top:12px; border-top:1px solid rgba(255,255,255,0.06); color:rgba(224,224,224,0.3); font-size:0.7rem;">';
    html += 'Last evaluated: ' + (data.evaluated_at ? _esc(new Date(data.evaluated_at).toLocaleString()) : '--');
    html += ' \u00B7 Data quality: ' + _esc(data.data_freshness || '--');
    html += '</div>';

    drawer.innerHTML = html;

    // Bind close button
    var closeBtn = drawer.querySelector('#ce-close-drawer-btn');
    if (closeBtn) closeBtn.addEventListener('click', closeDetail);
  }

  // ── Status indicator ──
  function loadStatus() {
    fetch('/api/company-evaluator/status')
      .then(function(res) { return res.json(); })
      .then(function(data) {
        var el = scope.querySelector('#ce-status');
        if (!el) return;

        if (data.service_healthy) {
          var pipeline = data.pipeline || {};
          if (pipeline.running) {
            el.innerHTML = '<span style="color:#ffd600;">\u25CF Crawler running: ' + _esc(pipeline.current_symbol || '...') +
              ' (' + _esc(String((pipeline.progress && pipeline.progress.pct) || 0)) + '%)</span>';
          } else {
            el.innerHTML = '<span style="color:#00c853;">\u25CF Service connected</span>';
          }
        } else {
          el.innerHTML = '<span style="color:#ff1744;">\u25CF Service unavailable</span>';
        }
      })
      .catch(function() {
        var el = scope.querySelector('#ce-status');
        if (el) el.innerHTML = '<span style="color:#ff1744;">\u25CF Cannot reach evaluator service</span>';
      });
  }

  // ── Action handlers ──
  function refresh() {
    loadRankedList();
    loadStatus();
  }

  function triggerCrawl() {
    if (!confirm('Start crawling the full company universe? This will take 2-3 hours.')) return;

    fetch('/api/company-evaluator/crawl', { method: 'POST' })
      .then(function(res) { return res.json(); })
      .then(function(data) {
        if (data.status === 'started') {
          alert('Crawler started \u2014 ' + (data.symbols || '?') + ' companies queued.');
          loadStatus();
        } else if (data.status === 'already_running') {
          alert('Crawler is already running.');
        } else {
          alert('Response: ' + JSON.stringify(data));
        }
      })
      .catch(function(err) {
        alert('Failed to start crawler: ' + err.message);
      });
  }

  // ── Keyboard handler ──
  function _onKeydown(e) {
    if (e.key === 'Escape') closeDetail();
  }

  // ── Bind events ──
  var _filterIds = ['#ce-sector-filter', '#ce-mcap-filter', '#ce-rating-filter', '#ce-tier-filter', '#ce-score-filter'];
  for (var fi = 0; fi < _filterIds.length; fi++) {
    var fEl = scope.querySelector(_filterIds[fi]);
    if (fEl) fEl.addEventListener('change', applyFilters);
  }

  var searchInput = scope.querySelector('#ce-search-input');
  if (searchInput) {
    var _searchDebounce = null;
    searchInput.addEventListener('input', function() {
      clearTimeout(_searchDebounce);
      _searchDebounce = setTimeout(applyFilters, 200);
    });
  }

  var resetBtn = scope.querySelector('#ce-reset-filters');
  if (resetBtn) resetBtn.addEventListener('click', resetFilters);

  var refreshBtn = scope.querySelector('#ce-refresh-btn');
  if (refreshBtn) refreshBtn.addEventListener('click', refresh);

  var crawlBtn = scope.querySelector('#ce-crawl-btn');
  if (crawlBtn) crawlBtn.addEventListener('click', triggerCrawl);

  document.addEventListener('keydown', _onKeydown);

  // ── Connection toggle ──
  var _connRadios = scope.querySelectorAll('input[name="ce-conn-mode"]');
  var _connUrlEl = scope.querySelector('#ce-conn-url');

  function setConnRadioState(mode) {
    _connRadios.forEach(function(r) { r.checked = (r.value === mode); });
  }

  function showConnUrl(url, healthy) {
    if (!_connUrlEl) return;
    var dot = healthy ? '\u25CF' : '\u25CB';
    var color = healthy ? '#00c853' : '#ff1744';
    _connUrlEl.innerHTML = '<span style="color:' + color + ';">' + dot + '</span> ' + _esc(url);
    _connUrlEl.title = healthy ? 'Connected' : 'Cannot reach evaluator at ' + url;
  }

  function showConnWarning(url) {
    if (!_connUrlEl) return;
    _connUrlEl.innerHTML = '<span style="color:#ff1744;">\u25CB</span> ' + _esc(url) +
      ' <span style="color:#ff9800; font-size:0.68rem;">\u2014 not reachable</span>';
  }

  async function loadConnectionState() {
    try {
      var res = await fetch('/api/company-evaluator/connection');
      if (!res.ok) return;
      var data = await res.json();
      setConnRadioState(data.mode);
      showConnUrl(data.url, null);
      // Verify reachability through our proxy status endpoint
      checkEvaluatorHealth(data.url);
    } catch (_e) { /* ignore */ }
  }

  async function checkEvaluatorHealth(url) {
    try {
      var res = await fetch('/api/company-evaluator/status');
      if (!res.ok) { showConnWarning(url); return; }
      var data = await res.json();
      if (data.service_healthy) {
        showConnUrl(url, true);
      } else {
        showConnWarning(url);
      }
    } catch (_e) {
      showConnWarning(url);
    }
  }

  async function switchConnectionMode(mode) {
    _connRadios.forEach(function(r) { r.disabled = true; });
    try {
      var res = await fetch('/api/company-evaluator/connection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: mode }),
      });
      if (!res.ok) {
        var err = await res.json().catch(function() { return {}; });
        alert('Failed to switch mode: ' + (err.detail || 'unknown error'));
        loadConnectionState();
        return;
      }
      var data = await res.json();
      setConnRadioState(data.mode);
      showConnUrl(data.url, null);
      // Health check the new target
      await checkEvaluatorHealth(data.url);
      // Reload data with new connection
      refresh();
    } catch (e) {
      alert('Failed to switch connection: ' + e.message);
      loadConnectionState();
    } finally {
      _connRadios.forEach(function(r) { r.disabled = false; });
    }
  }

  _connRadios.forEach(function(radio) {
    radio.addEventListener('change', function() {
      if (this.checked) switchConnectionMode(this.value);
    });
  });

  // ── Init ──
  loadConnectionState();
  loadRankedList();
  loadPositions();
  loadStatus();
  _statusTimer = setInterval(loadStatus, 15000);

  // ── Cleanup (returned to router) ──
  return function cleanup() {
    if (_statusTimer) { clearInterval(_statusTimer); _statusTimer = null; }
    document.removeEventListener('keydown', _onKeydown);
    closeDetail();
  };
};
