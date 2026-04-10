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
  var _currentPage = 0;
  var _PAGE_SIZE = 50;
  var _lastFiltered = [];

  // ── Helpers ──
  function _esc(s) {
    if (!s) return '';
    var el = document.createElement('span');
    el.textContent = s;
    return el.innerHTML;
  }

  var _thStyle = 'padding:8px 10px; text-align:left; color:#00eaff; font-size:0.75rem; font-weight:600; letter-spacing:0.03em; text-transform:uppercase;';
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

  // ── Breakout score helpers ──
  function _getBreakoutColorClass(score) {
    if (score >= 70) return 'ce-breakout-high';
    if (score >= 50) return 'ce-breakout-medium';
    if (score >= 30) return 'ce-breakout-low';
    return 'ce-breakout-minimal';
  }

  function _renderBreakoutScore(score) {
    if (score == null) {
      return '<span class="ce-breakout-na" title="Not eligible for breakout screening (outside $500M\u2013$50B range or insufficient data)">\u2014</span>';
    }
    var colorClass = _getBreakoutColorClass(score);
    var tierLabel = score >= 70 ? 'Strong candidate'
                  : score >= 50 ? 'Moderate potential'
                  : score >= 30 ? 'Weak signals'
                  : 'Minimal potential';
    return '<span class="ce-breakout-score ' + colorClass + '" title="Breakout Potential: ' + score.toFixed(1) + '/100 \u2014 ' + tierLabel + '. Click Raw \uD83D\uDD2C for full breakdown.">' + score.toFixed(1) + '</span>';
  }

  // ── Completeness badge helpers ──
  function _completenessBadgeClass(pct) {
    if (pct == null) return 'ce-completeness-unknown';
    if (pct >= 80) return 'ce-completeness-high';
    if (pct >= 50) return 'ce-completeness-medium';
    return 'ce-completeness-low';
  }

  function _completenessTooltip(pct, missingPillarCount) {
    if (pct == null) return 'Data completeness: unknown';
    var tier;
    if (pct >= 80) tier = 'high confidence';
    else if (pct >= 50) tier = 'medium confidence';
    else tier = 'low confidence \u2014 sparse data';
    var tip = 'Data completeness: ' + pct.toFixed(0) + '% (' + tier + ')';
    if (missingPillarCount > 0) tip += ' \u2022 ' + missingPillarCount + ' pillar(s) with missing data';
    return tip;
  }

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
    if (score == null) return '<td style="' + _tdStyle + 'text-align:center; color:#3a4a58;">--</td>';
    var color = _scoreColor(score);
    return '<td style="' + _tdStyle + 'text-align:center; color:' + color + '; font-size:0.85rem; font-weight:500;">' + score.toFixed(0) + '</td>';
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
      completeness: (scope.querySelector('#ce-completeness-filter') || {}).value || '',
      breakout: (scope.querySelector('#ce-breakout-filter') || {}).value || '',
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
    if (f.completeness) {
      var cpct = c.completeness_pct;
      if (f.completeness === 'high' && (cpct == null || cpct < 80)) return false;
      if (f.completeness === 'medium' && (cpct == null || cpct < 50 || cpct >= 80)) return false;
      if (f.completeness === 'low' && (cpct != null && cpct >= 50)) return false;
    }
    if (f.breakout) {
      var bs = c.breakout_score;
      if (f.breakout === 'eligible' && bs == null) return false;
      if (f.breakout === 'high' && (bs == null || bs < 70)) return false;
      if (f.breakout === 'medium' && (bs == null || bs < 50 || bs >= 70)) return false;
      if (f.breakout === 'low' && (bs == null || bs < 30 || bs >= 50)) return false;
      if (f.breakout === 'any' && (bs == null || bs <= 0)) return false;
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
    var allPanels = scope.querySelectorAll('.ce-analysis-panel, .ce-analysis-header, .ce-analysis-content, .ce-price-row');
    for (var pi = 0; pi < allPanels.length; pi++) allPanels[pi].parentNode.removeChild(allPanels[pi]);
    _entryPanelOpen = {};
    _compsPanelOpen = {};
    _dcfPanelOpen = {};
    _evaPanelOpen = {};
    _smartMoneyPanelOpen = {};
    var f = _getFilterState();
    var filtered = [];
    for (var i = 0; i < _ceCompanies.length; i++) {
      if (_matchesFilters(_ceCompanies[i], f)) filtered.push(_ceCompanies[i]);
    }
    _sortList(filtered);
    _lastFiltered = filtered;
    _currentPage = 0;
    renderTable(filtered);
    _updateFilterCount(filtered.length, _ceCompanies.length);
  }

  function _updateFilterCount(shown, total) {
    var el = scope.querySelector('#ce-filter-count');
    if (!el) return;
    var breakoutEligible = 0;
    var breakoutHigh = 0;
    for (var i = 0; i < _ceCompanies.length; i++) {
      var bs = _ceCompanies[i].breakout_score;
      if (bs != null) { breakoutEligible++; if (bs >= 70) breakoutHigh++; }
    }
    var html = '<span>' + shown + '</span> of ' + total;
    if (breakoutEligible > 0) {
      html += '  \u2022  ' + breakoutEligible + ' breakout';
      if (breakoutHigh > 0) html += '  \u2022  ' + breakoutHigh + ' high';
    }
    el.innerHTML = html;
  }

  function resetFilters() {
    var ids = ['#ce-sector-filter', '#ce-mcap-filter', '#ce-rating-filter', '#ce-tier-filter', '#ce-score-filter', '#ce-completeness-filter', '#ce-breakout-filter'];
    for (var i = 0; i < ids.length; i++) {
      var el = scope.querySelector(ids[i]);
      if (el) { el.value = ''; el.classList.remove('ce-filter-active'); }
    }
    var search = scope.querySelector('#ce-search-input');
    if (search) search.value = '';
    _updateClearButtonVisibility();
    applyFilters();
  }

  // ── Active filter state UI ──
  function _updateFilterActiveStates() {
    var selects = scope.querySelectorAll('.ce-filter-compact');
    for (var i = 0; i < selects.length; i++) {
      if (selects[i].value && selects[i].value !== '') {
        selects[i].classList.add('ce-filter-active');
      } else {
        selects[i].classList.remove('ce-filter-active');
      }
    }
    _updateClearButtonVisibility();
  }

  function _updateClearButtonVisibility() {
    var anyActive = scope.querySelector('.ce-filter-active');
    var searchEl = scope.querySelector('#ce-search-input');
    var searchHasValue = searchEl && searchEl.value.trim().length > 0;
    var clearBtn = scope.querySelector('#ce-clear-filters');
    if (clearBtn) {
      clearBtn.style.display = (anyActive || searchHasValue) ? 'inline-block' : 'none';
    }
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
    if (col === 'completeness_pct') return c.completeness_pct;
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
    if (col === 'breakout_score') return c.breakout_score;
    return null;
  }

  function _onSortClick(col) {
    if (_sortCol === col) {
      _sortAsc = !_sortAsc;
    } else {
      _sortCol = col;
      _sortAsc = false;
    }
    _currentPage = 0;
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

  // ── Price inline display ──
  function _handlePrice(symbol) {
    var existing = scope.querySelector('.ce-price-row[data-symbol="' + symbol + '"]');
    if (existing) {
      existing.parentNode.removeChild(existing);
      return;
    }

    var actionRow = scope.querySelector('.ce-action-row[data-symbol="' + _esc(symbol) + '"]');
    if (!actionRow) return;
    var colCount = _getColCount();

    var row = document.createElement('tr');
    row.className = 'ce-price-row';
    row.setAttribute('data-symbol', symbol);
    var td = document.createElement('td');
    td.colSpan = colCount;
    td.innerHTML = '<div class="ce-price-display"><span class="ce-price-label">Fetching price\u2026</span></div>';
    row.appendChild(td);
    actionRow.parentNode.insertBefore(row, actionRow.nextSibling);

    fetch('/api/company-evaluator/quote/' + encodeURIComponent(symbol))
      .then(function(res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function(data) {
        if (data.ok === false) throw new Error(data.error || 'Quote unavailable');
        var price = data.price || data.last || data.close;
        var change = data.change != null ? data.change : null;
        var changePct = data.change_pct != null ? data.change_pct : (data.change_percentage != null ? data.change_percentage : null);
        var volume = data.volume || null;

        var changeColor = (change != null && change >= 0) ? '#4ade80' : '#f87171';
        var changeIcon = (change != null && change >= 0) ? '\u25B2' : '\u25BC';
        var changeSign = (change != null && change >= 0) ? '+' : '';

        var h = '<div class="ce-price-display">';
        h += '<span class="ce-price-label">Current Price:</span>';
        h += '<span class="ce-price-val">' + (price != null ? '$' + Number(price).toFixed(2) : '--') + '</span>';
        if (change != null) {
          h += '<span class="ce-price-change" style="color:' + changeColor + ';">';
          h += changeIcon + ' ' + changeSign + '$' + Math.abs(change).toFixed(2);
          if (changePct != null) h += ' (' + changeSign + Math.abs(changePct).toFixed(2) + '%)';
          h += '</span>';
        }
        if (volume) {
          h += '<span class="ce-price-volume">Vol: ' + _fmtVolume(volume) + '</span>';
        }
        h += '</div>';
        td.innerHTML = h;
      })
      .catch(function(err) {
        td.innerHTML = '<div class="ce-price-display ce-price-error">Price unavailable: ' + _esc(err.message) + '</div>';
      });
  }

  function _fmtVolume(vol) {
    if (vol >= 1e6) return (vol / 1e6).toFixed(1) + 'M';
    if (vol >= 1e3) return (vol / 1e3).toFixed(0) + 'K';
    return String(vol);
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
  var _smartMoneyPanelOpen = {}; // { SYMBOL: true }
  var _smartMoneyCache = {};  // { SYMBOL: { data, analyzedAt } }
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
    var anchor = _getInsertAnchor(symbol, 'entry');
    anchor.parentNode.insertBefore(spinTr, anchor.nextSibling);

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
        var anchor = _getInsertAnchor(symbol, 'entry');
        anchor.parentNode.insertBefore(errTr, anchor.nextSibling);
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

    // Insert after eva > entry > price > action row
    var anchor = _getInsertAnchor(symbol, 'dcf');
    anchor.parentNode.insertBefore(spinTr, anchor.nextSibling);

    _fetchDcfAnalysis(symbol, company, false);
  }

  function _fetchDcfAnalysis(symbol, company, forceFresh) {
    var fetchPromise;
    if (!forceFresh) {
      fetchPromise = fetch('/api/company-evaluator/valuation/dcf/' + encodeURIComponent(symbol))
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
        var anchor = _getInsertAnchor(symbol, 'dcf');
        anchor.parentNode.insertBefore(errTr, anchor.nextSibling);
        var dismiss = errTd.querySelector('.ce-panel-dismiss');
        if (dismiss) dismiss.addEventListener('click', function() { errTr.parentNode.removeChild(errTr); });
      });
  }

  function _postDcfAnalysis(symbol) {
    return fetch('/api/company-evaluator/valuation/dcf', {
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

    // Insert after entry > price > action row
    var anchor = _getInsertAnchor(symbol, 'eva');
    anchor.parentNode.insertBefore(spinTr, anchor.nextSibling);

    _fetchEvaAnalysis(symbol, company, false);
  }

  function _fetchEvaAnalysis(symbol, company, forceFresh) {
    var fetchPromise;
    if (!forceFresh) {
      fetchPromise = fetch('/api/company-evaluator/valuation/eva/' + encodeURIComponent(symbol))
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
        var anchor = _getInsertAnchor(symbol, 'eva');
        anchor.parentNode.insertBefore(errTr, anchor.nextSibling);
        var dismiss = errTd.querySelector('.ce-panel-dismiss');
        if (dismiss) dismiss.addEventListener('click', function() { errTr.parentNode.removeChild(errTr); });
      });
  }

  function _postEvaAnalysis(symbol) {
    return fetch('/api/company-evaluator/valuation/eva', {
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

    // Insert after dcf > eva > entry > price > action row
    var anchor = _getInsertAnchor(symbol, 'comps');
    anchor.parentNode.insertBefore(spinTr, anchor.nextSibling);

    _fetchCompsAnalysis(symbol, company, null, false);
  }

  function _fetchCompsAnalysis(symbol, company, _unused, forceFresh) {
    var fetchPromise;
    if (!forceFresh) {
      fetchPromise = fetch('/api/company-evaluator/valuation/comps/' + encodeURIComponent(symbol))
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
        // Insert after dcf > eva > entry > price > action row
        var anchor = _getInsertAnchor(symbol, 'comps');
        anchor.parentNode.insertBefore(errTr, anchor.nextSibling);
        var dismiss = errTd.querySelector('.ce-panel-dismiss');
        if (dismiss) dismiss.addEventListener('click', function() { errTr.parentNode.removeChild(errTr); });
      });
  }

  function _postCompsAnalysis(symbol) {
    return fetch('/api/company-evaluator/valuation/comps', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: symbol }),
    }).then(function(res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    });
  }

  // ── Smart Money Analysis ──
  function _closeSmartMoneyPanel() {
    var panels = scope.querySelectorAll('.ce-analysis-panel[data-type="smart-money"], .ce-analysis-header[data-type="smart-money"], .ce-analysis-content[data-type="smart-money"]');
    for (var i = 0; i < panels.length; i++) panels[i].parentNode.removeChild(panels[i]);
    _smartMoneyPanelOpen = {};
    var btns = scope.querySelectorAll('.ce-sm-btn');
    for (var b = 0; b < btns.length; b++) _updateBtnState(btns[b].getAttribute('data-symbol'), 'smart-money');
  }

  function _handleSmartMoney(symbol) {
    var hdr = scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="smart-money"]');
    if (hdr) {
      _togglePanel(symbol, 'smart-money');
      return;
    }
    _smartMoneyPanelOpen[symbol] = true;
    _updateBtnState(symbol, 'smart-money');

    var actionRow = scope.querySelector('.ce-action-row[data-symbol="' + _esc(symbol) + '"]');
    if (!actionRow) return;
    var colCount = _getColCount();

    var spinTr = document.createElement('tr');
    spinTr.className = 'ce-analysis-panel ce-analysis-spinner';
    spinTr.setAttribute('data-symbol', symbol);
    spinTr.setAttribute('data-type', 'smart-money');
    var spinTd = document.createElement('td');
    spinTd.colSpan = colCount;
    spinTd.innerHTML = '<div style="padding:16px 24px; text-align:center; color:rgba(224,224,224,0.5);">'
      + '<div class="home-scan-spinner" style="width:20px; height:20px; margin:0 auto 8px;"></div>'
      + 'Loading smart money data for ' + _esc(symbol) + '\u2026</div>';
    spinTr.appendChild(spinTd);

    var anchor = _getInsertAnchor(symbol, 'smart-money');
    if (anchor) anchor.parentNode.insertBefore(spinTr, anchor.nextSibling);

    fetch('/api/company-evaluator/companies/' + encodeURIComponent(symbol) + '/raw', { signal: AbortSignal.timeout(15000) })
      .then(function(res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function(rawData) {
        var sm = (rawData.raw_financials || {}).smart_money;
        if (!sm) sm = {};
        _smartMoneyCache[symbol] = { data: sm, analyzedAt: new Date().toISOString() };
        _updateBtnState(symbol, 'smart-money');
        _buildAccordionPanel(symbol, null, 'smart-money');
      })
      .catch(function(err) {
        _removeSpinner(symbol, 'smart-money');
        _smartMoneyPanelOpen[symbol] = false;
        _updateBtnState(symbol, 'smart-money');
        var errTr = document.createElement('tr');
        errTr.className = 'ce-analysis-panel';
        errTr.setAttribute('data-symbol', symbol);
        errTr.setAttribute('data-type', 'smart-money');
        var errTd = document.createElement('td');
        errTd.colSpan = _getColCount();
        errTd.innerHTML = '<div style="padding:12px 24px; color:#ff5a5a; font-size:13px;">'
          + '\u26A0 Smart money data failed: ' + _esc(err.message)
          + ' <button class="ce-panel-dismiss" style="margin-left:12px; padding:2px 8px; border-radius:4px; font-size:11px; '
          + 'background:none; color:rgba(224,224,224,0.4); border:1px solid rgba(255,255,255,0.08); cursor:pointer;">Dismiss</button></div>';
        errTr.appendChild(errTd);
        var anchor2 = _getInsertAnchor(symbol, 'smart-money');
        if (anchor2) anchor2.parentNode.insertBefore(errTr, anchor2.nextSibling);
        var dismiss = errTd.querySelector('.ce-panel-dismiss');
        if (dismiss) dismiss.addEventListener('click', function() { errTr.parentNode.removeChild(errTr); });
      });
  }

  // ── Smart Money verdict matrix ──
  var _smVerdictMatrix = {
    'strong_buying|accumulating': { text: 'STRONG BULLISH ALIGNMENT', detail: 'Multiple insiders buying + Institutions accumulating', tier: 'bullish-strong', css: '#80e8a0' },
    'strong_buying|stable':       { text: 'INSIDER CONVICTION', detail: 'Multiple insiders buying with stable institutional base', tier: 'bullish', css: '#70d090' },
    'strong_buying|distributing': { text: 'MIXED \u2014 INSIDERS BUYING', detail: 'Insiders buying aggressively but institutions reducing', tier: 'mixed', css: '#f0c060' },
    'buying|accumulating':        { text: 'BULLISH ALIGNMENT', detail: 'Insiders buying + Institutions adding positions', tier: 'bullish', css: '#70d090' },
    'buying|stable':              { text: 'MILD BULLISH', detail: 'Insider buying with stable institutional base', tier: 'bullish', css: '#90d8a0' },
    'buying|distributing':        { text: 'MIXED \u2014 INSIDERS BUYING', detail: 'Insiders buying but institutions reducing', tier: 'mixed', css: '#f0c060' },
    'neutral|accumulating':       { text: 'INSTITUTIONAL ACCUMULATION', detail: 'No insider signal but institutions adding', tier: 'bullish', css: '#90d8a0' },
    'neutral|stable':             { text: 'NEUTRAL', detail: 'No significant smart money activity', tier: 'neutral', css: '#a0b8c8' },
    'neutral|distributing':       { text: 'INSTITUTIONAL EXIT', detail: 'Institutions reducing positions', tier: 'bearish', css: '#f0a070' },
    'selling|accumulating':       { text: 'MIXED \u2014 INSIDERS SELLING', detail: 'Insiders selling but institutions still adding', tier: 'mixed', css: '#f0c060' },
    'selling|stable':             { text: 'MILD BEARISH', detail: 'Insider selling with stable institutional base', tier: 'bearish', css: '#f0a070' },
    'selling|distributing':       { text: 'BEARISH ALIGNMENT', detail: 'Insiders selling + Institutions reducing', tier: 'bearish-strong', css: '#f08070' },
    'strong_selling|accumulating':{ text: 'MIXED \u2014 INSIDERS DUMPING', detail: 'Heavy insider selling despite institutional buying', tier: 'mixed', css: '#f0c060' },
    'strong_selling|stable':      { text: 'INSIDER EXIT', detail: 'Heavy insider selling', tier: 'bearish-strong', css: '#ff8070' },
    'strong_selling|distributing':{ text: 'STRONG BEARISH ALIGNMENT', detail: 'Heavy insider selling + Institutions distributing', tier: 'bearish-strong', css: '#ff8070' },
    'no_activity|accumulating':   { text: 'INSTITUTIONAL ACCUMULATION', detail: 'No insider activity but institutions adding', tier: 'bullish', css: '#90d8a0' },
    'no_activity|stable':         { text: 'NEUTRAL', detail: 'No significant smart money activity', tier: 'neutral', css: '#a0b8c8' },
    'no_activity|distributing':   { text: 'INSTITUTIONAL EXIT', detail: 'Institutions reducing positions', tier: 'bearish', css: '#f0a070' },
  };

  function _smComputeVerdict(insiderSignal, instTrend) {
    var key = (insiderSignal || 'no_data') + '|' + (instTrend || 'no_data');
    return _smVerdictMatrix[key] || { text: 'INSUFFICIENT DATA', detail: 'Smart money data not available', tier: 'neutral', css: '#a0b8c8' };
  }

  // ── Smart Money formatters ──
  function _smFmtSignal(signal) {
    var map = { strong_buying: 'STRONG BUYING', buying: 'BUYING', neutral: 'NEUTRAL', selling: 'SELLING', strong_selling: 'STRONG SELLING', no_activity: 'NO ACTIVITY', no_data: 'NO DATA' };
    return map[signal] || (signal || 'NO DATA').toUpperCase().replace(/_/g, ' ');
  }

  function _smSignalIcon(signal) {
    var map = { strong_buying: '\u2B06\uFE0F\u2B06\uFE0F', buying: '\u2B06\uFE0F', neutral: '\u27A1\uFE0F', selling: '\u2B07\uFE0F', strong_selling: '\u2B07\uFE0F\u2B07\uFE0F', no_activity: '\u2014', no_data: '\u2014' };
    return map[signal] || '';
  }

  function _smSignalColor(signal) {
    if (signal === 'strong_buying' || signal === 'buying') return '#50c878';
    if (signal === 'selling' || signal === 'strong_selling') return '#f06060';
    return '#a0b8c8';
  }

  function _smFmtTrend(trend) {
    var map = { accumulating: 'ACCUMULATING', stable: 'STABLE', distributing: 'DISTRIBUTING', no_data: 'NO DATA' };
    return map[trend] || (trend || 'NO DATA').toUpperCase().replace(/_/g, ' ');
  }

  function _smTrendIcon(trend) {
    var map = { accumulating: '\uD83D\uDCC8', stable: '\u27A1\uFE0F', distributing: '\uD83D\uDCC9', no_data: '\u2014' };
    return map[trend] || '';
  }

  function _smTrendColor(trend) {
    if (trend === 'accumulating') return '#50c878';
    if (trend === 'distributing') return '#f06060';
    return '#a0b8c8';
  }

  function _smFmtCurrency(val) {
    if (val == null) return '\u2014';
    var sign = val >= 0 ? '+' : '-';
    var abs = Math.abs(val);
    if (abs >= 1e9) return sign + '$' + (abs / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(0);
  }

  function _smFmtShares(val) {
    if (val == null) return '\u2014';
    var sign = val >= 0 ? '+' : '';
    return sign + val.toLocaleString();
  }

  function _smFmtChange(val, type) {
    if (val == null) return '';
    var arrow = val >= 0 ? '\u25B2' : '\u25BC';
    var cls = val >= 0 ? 'color:#50c878;' : 'color:#f06060;';
    if (type === 'pct') return '<span style="' + cls + ' font-size:0.75rem; margin-left:4px;">' + arrow + ' ' + (val >= 0 ? '+' : '') + val.toFixed(1) + '%</span>';
    return '<span style="' + cls + ' font-size:0.75rem; margin-left:4px;">' + arrow + ' ' + (val >= 0 ? '+' : '') + val.toLocaleString() + '</span>';
  }

  function _smInterpretPutCall(ratio) {
    if (ratio == null) return '';
    if (ratio < 0.7) return '<span style="color:#50c878; font-size:0.72rem; margin-left:4px;">(bullish)</span>';
    if (ratio > 1.3) return '<span style="color:#f06060; font-size:0.72rem; margin-left:4px;">(bearish)</span>';
    return '<span style="color:#a0b8c8; font-size:0.72rem; margin-left:4px;">(neutral)</span>';
  }

  // ── Smart Money renderer ──
  function _renderSmartMoneyPanel(data, symbol) {
    var insider = data.insider_activity || {};
    var inst = data.institutional_ownership || {};
    var noInsider = !insider.signal || insider.signal === 'no_data';
    var noInst = !inst.trend || inst.trend === 'no_data';

    var verdict = _smComputeVerdict(insider.signal || 'no_data', inst.trend || 'no_data');

    var html = '';

    // ── Section 1: Verdict Banner ──
    var tierBg = { 'bullish-strong': 'rgba(80,200,120,0.15)', 'bullish': 'rgba(80,200,120,0.10)', 'mixed': 'rgba(240,192,80,0.10)', 'bearish': 'rgba(240,96,96,0.10)', 'bearish-strong': 'rgba(240,96,96,0.15)', 'neutral': 'rgba(128,144,160,0.10)' };
    var tierBorder = { 'bullish-strong': '#50c878', 'bullish': '#50c878', 'mixed': '#f0c050', 'bearish': '#f06060', 'bearish-strong': '#f06060', 'neutral': '#809eb0' };
    var bg = tierBg[verdict.tier] || tierBg.neutral;
    var bord = tierBorder[verdict.tier] || tierBorder.neutral;

    html += '<div style="padding:16px 20px; border-radius:6px; margin-bottom:16px; background:' + bg + '; border-left:4px solid ' + bord + ';">';
    html += '<div style="font-size:11px; text-transform:uppercase; letter-spacing:1px; color:' + verdict.css + '; opacity:0.7; margin-bottom:4px;">Smart Money Verdict</div>';
    html += '<div style="font-size:20px; font-weight:700; color:' + verdict.css + '; margin-bottom:6px;">' + _esc(verdict.text) + '</div>';
    html += '<div style="font-size:13px; color:' + verdict.css + '; opacity:0.85;">' + _esc(verdict.detail) + '</div>';
    html += '</div>';

    // ── Section 2: Two-column cards ──
    html += '<div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:16px;">';

    // Insider card
    html += '<div style="background:#0d2030; border:1px solid #1a3a4a; border-radius:6px; padding:16px;">';
    html += '<div style="font-size:11px; text-transform:uppercase; letter-spacing:1px; color:#708090; margin-bottom:8px;">Insider Activity (180d)</div>';
    if (noInsider) {
      html += '<div style="color:rgba(224,224,224,0.3); font-size:0.85rem; padding:12px 0;">No insider data available</div>';
    } else {
      var sigColor = _smSignalColor(insider.signal);
      html += '<div style="font-size:18px; font-weight:600; color:' + sigColor + '; margin-bottom:6px;">' + _smSignalIcon(insider.signal) + ' ' + _smFmtSignal(insider.signal) + '</div>';
      html += '<div style="font-size:12px; color:#708090; margin-bottom:14px;">Score: <span style="color:' + _scoreColor(insider.score) + '; font-weight:600;">' + (insider.score != null ? insider.score.toFixed(0) : '\u2014') + '</span>/100</div>';

      var totalTx = insider.transaction_count || (insider.buy_count || 0) + (insider.sell_count || 0);
      var buys = insider.buy_count || 0;
      var sells = insider.sell_count || 0;
      var total = buys + sells;
      var buyPct = total > 0 ? (buys / total * 100) : 50;

      html += _smStatRow('Total transactions', totalTx);
      // ratio bar
      html += '<div style="display:flex; height:6px; background:#1a3a4a; border-radius:3px; overflow:hidden; margin:6px 0;">';
      html += '<div style="width:' + buyPct + '%; background:#50c878;"></div>';
      html += '<div style="width:' + (100 - buyPct) + '%; background:#f06060;"></div>';
      html += '</div>';
      html += '<div style="display:flex; justify-content:space-between; font-size:13px; margin-bottom:10px;">';
      html += '<span style="color:#50c878;">\u2191 ' + buys + ' buys</span>';
      html += '<span style="color:#f06060;">' + sells + ' sells \u2193</span>';
      html += '</div>';

      html += _smStatRow('Unique buyers', insider.unique_buyers != null ? insider.unique_buyers : '\u2014');

      // Buyer mix
      html += '<div style="font-size:11px; text-transform:uppercase; color:#708090; margin:10px 0 4px; letter-spacing:0.5px;">Buyer Mix</div>';
      html += _smStatRow('\uD83D\uDC54 Officers', insider.officer_buys || 0);
      html += _smStatRow('\uD83C\uDFA9 Directors', insider.director_buys || 0);
      html += _smStatRow('\uD83D\uDCBC 10% Owners', insider.ten_pct_owner_buys || 0);

      // Net value
      var netShares = insider.net_shares;
      var netValue = insider.net_value;
      if (netShares != null || netValue != null) {
        html += '<div style="margin-top:10px; padding-top:8px; border-top:1px solid rgba(255,255,255,0.06); font-size:13px; color:#d0e0e8; font-weight:500;">';
        html += 'Net: ' + _smFmtShares(netShares) + ' shares';
        if (netValue != null) html += ' (' + _smFmtCurrency(netValue) + ')';
        html += '</div>';
      }
    }
    html += '</div>';

    // Institutional card
    html += '<div style="background:#0d2030; border:1px solid #1a3a4a; border-radius:6px; padding:16px;">';
    html += '<div style="font-size:11px; text-transform:uppercase; letter-spacing:1px; color:#708090; margin-bottom:8px;">Institutional Ownership</div>';
    if (noInst) {
      html += '<div style="color:rgba(224,224,224,0.3); font-size:0.85rem; padding:12px 0;">No institutional data available</div>';
    } else {
      var trendColor = _smTrendColor(inst.trend);
      html += '<div style="font-size:18px; font-weight:600; color:' + trendColor + '; margin-bottom:6px;">' + _smTrendIcon(inst.trend) + ' ' + _smFmtTrend(inst.trend) + '</div>';
      html += '<div style="font-size:12px; color:#708090; margin-bottom:14px;">Score: <span style="color:' + _scoreColor(inst.score) + '; font-weight:600;">' + (inst.score != null ? inst.score.toFixed(0) : '\u2014') + '</span>/100</div>';

      // Current ownership
      var cpct = inst.current_pct != null ? inst.current_pct.toFixed(1) + '%' : '\u2014';
      html += '<div style="display:flex; justify-content:space-between; padding:4px 0; font-size:13px;">';
      html += '<span style="color:#809eb0;">Current ownership</span>';
      html += '<span style="color:#d0e0e8; font-weight:500;">' + cpct + _smFmtChange(inst.pct_change_qoq, 'pct') + '</span>';
      html += '</div>';

      var holders = inst.current_holders != null ? inst.current_holders.toLocaleString() : '\u2014';
      html += '<div style="display:flex; justify-content:space-between; padding:4px 0; font-size:13px;">';
      html += '<span style="color:#809eb0;">Holder count</span>';
      html += '<span style="color:#d0e0e8; font-weight:500;">' + holders + _smFmtChange(inst.holder_change_qoq, 'count') + '</span>';
      html += '</div>';

      // Quarterly activity
      html += '<div style="font-size:11px; text-transform:uppercase; color:#708090; margin:10px 0 4px; letter-spacing:0.5px;">Quarterly Activity</div>';
      html += _smStatRow('\uD83C\uDD95 New positions', inst.new_positions_qoq || 0);
      html += _smStatRow('\u2B06\uFE0F Increased', inst.increased_positions_qoq || 0);
      html += _smStatRow('\u2B07\uFE0F Reduced', inst.reduced_positions_qoq || 0);
      html += _smStatRow('\u274C Closed', inst.closed_positions_qoq || 0);

      // Put/call
      if (inst.put_call_ratio != null) {
        html += '<div style="font-size:11px; text-transform:uppercase; color:#708090; margin:10px 0 4px; letter-spacing:0.5px;">Hedge Sentiment</div>';
        html += '<div style="display:flex; justify-content:space-between; padding:4px 0; font-size:13px;">';
        html += '<span style="color:#809eb0;">Put/Call ratio</span>';
        html += '<span style="color:#d0e0e8; font-weight:500;">' + inst.put_call_ratio.toFixed(2) + _smInterpretPutCall(inst.put_call_ratio) + '</span>';
        html += '</div>';
      }
    }
    html += '</div>';

    html += '</div>'; // close grid

    // ── Section 3: Detail tables (collapsible) ──
    var txList = insider.recent_transactions || insider.transactions || [];
    var topHolders = inst.top_holders || [];
    if (txList.length || topHolders.length) {
      html += '<details style="margin-bottom:8px; border:1px solid rgba(255,255,255,0.06); border-radius:6px;">';
      html += '<summary style="padding:8px 12px; cursor:pointer; color:rgba(224,224,224,0.5); font-size:0.78rem; font-weight:600;">Show Details</summary>';
      html += '<div style="padding:12px 16px;">';

      if (txList.length) {
        html += '<div style="font-size:11px; text-transform:uppercase; color:#708090; margin-bottom:6px; letter-spacing:0.5px;">Recent Insider Transactions</div>';
        html += '<div style="overflow-x:auto; max-height:250px; overflow-y:auto; margin-bottom:12px;">';
        html += '<table style="width:100%; border-collapse:collapse; font-size:0.72rem;">';
        html += '<thead><tr style="border-bottom:1px solid rgba(255,255,255,0.06);">';
        html += '<th style="padding:3px 6px; text-align:left; color:rgba(224,224,224,0.3);">Date</th>';
        html += '<th style="padding:3px 6px; text-align:left; color:rgba(224,224,224,0.3);">Insider</th>';
        html += '<th style="padding:3px 6px; text-align:left; color:rgba(224,224,224,0.3);">Role</th>';
        html += '<th style="padding:3px 6px; text-align:left; color:rgba(224,224,224,0.3);">Action</th>';
        html += '<th style="padding:3px 6px; text-align:right; color:rgba(224,224,224,0.3);">Shares</th>';
        html += '<th style="padding:3px 6px; text-align:right; color:rgba(224,224,224,0.3);">Price</th>';
        html += '<th style="padding:3px 6px; text-align:right; color:rgba(224,224,224,0.3);">Value</th>';
        html += '</tr></thead><tbody>';
        for (var ti = 0; ti < Math.min(txList.length, 15); ti++) {
          var tx = txList[ti];
          var actionColor = (tx.action || '').toLowerCase().indexOf('buy') >= 0 ? '#50c878' : (tx.action || '').toLowerCase().indexOf('sell') >= 0 ? '#f06060' : '#d0e0e8';
          html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.03);">';
          html += '<td style="padding:3px 6px; color:rgba(224,224,224,0.5); white-space:nowrap;">' + _esc(tx.date || tx.filing_date || '\u2014') + '</td>';
          html += '<td style="padding:3px 6px; color:rgba(224,224,224,0.7);">' + _esc(tx.name || tx.insider || '\u2014') + '</td>';
          html += '<td style="padding:3px 6px; color:rgba(224,224,224,0.4); font-size:0.68rem;">' + _esc(tx.role || tx.title || '\u2014') + '</td>';
          html += '<td style="padding:3px 6px; color:' + actionColor + '; font-weight:600;">' + _esc((tx.action || tx.transaction_type || '\u2014').toUpperCase()) + '</td>';
          html += '<td style="padding:3px 6px; text-align:right; color:rgba(224,224,224,0.6);">' + (tx.shares != null ? Math.abs(tx.shares).toLocaleString() : '\u2014') + '</td>';
          html += '<td style="padding:3px 6px; text-align:right; color:rgba(224,224,224,0.5);">' + (tx.price != null ? '$' + Number(tx.price).toFixed(2) : '\u2014') + '</td>';
          html += '<td style="padding:3px 6px; text-align:right; color:rgba(224,224,224,0.6);">' + (tx.value != null ? _smFmtCurrency(Math.abs(tx.value)) : '\u2014') + '</td>';
          html += '</tr>';
        }
        html += '</tbody></table></div>';
      }

      if (topHolders.length) {
        html += '<div style="font-size:11px; text-transform:uppercase; color:#708090; margin-bottom:6px; letter-spacing:0.5px;">Top Institutional Holders</div>';
        html += '<div style="overflow-x:auto; max-height:250px; overflow-y:auto;">';
        html += '<table style="width:100%; border-collapse:collapse; font-size:0.72rem;">';
        html += '<thead><tr style="border-bottom:1px solid rgba(255,255,255,0.06);">';
        html += '<th style="padding:3px 6px; text-align:left; color:rgba(224,224,224,0.3);">#</th>';
        html += '<th style="padding:3px 6px; text-align:left; color:rgba(224,224,224,0.3);">Holder</th>';
        html += '<th style="padding:3px 6px; text-align:right; color:rgba(224,224,224,0.3);">Shares</th>';
        html += '<th style="padding:3px 6px; text-align:right; color:rgba(224,224,224,0.3);">% of Float</th>';
        html += '<th style="padding:3px 6px; text-align:right; color:rgba(224,224,224,0.3);">QoQ Change</th>';
        html += '</tr></thead><tbody>';
        for (var hi = 0; hi < Math.min(topHolders.length, 15); hi++) {
          var h = topHolders[hi];
          html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.03);">';
          html += '<td style="padding:3px 6px; color:rgba(224,224,224,0.4);">' + (hi + 1) + '</td>';
          html += '<td style="padding:3px 6px; color:rgba(224,224,224,0.7);">' + _esc(h.name || h.holder || '\u2014') + '</td>';
          html += '<td style="padding:3px 6px; text-align:right; color:rgba(224,224,224,0.6);">' + (h.shares != null ? h.shares.toLocaleString() : '\u2014') + '</td>';
          html += '<td style="padding:3px 6px; text-align:right; color:rgba(224,224,224,0.5);">' + (h.pct_of_float != null ? h.pct_of_float.toFixed(1) + '%' : (h.ownership_pct != null ? h.ownership_pct.toFixed(1) + '%' : '\u2014')) + '</td>';
          html += '<td style="padding:3px 6px; text-align:right;">' + _smFmtChange(h.change_qoq || h.shares_change, 'count') + '</td>';
          html += '</tr>';
        }
        html += '</tbody></table></div>';
      }

      html += '</div></details>';
    }

    return html;
  }

  function _smStatRow(label, value) {
    return '<div style="display:flex; justify-content:space-between; padding:4px 0; font-size:13px;">'
      + '<span style="color:#809eb0;">' + label + '</span>'
      + '<span style="color:#d0e0e8; font-weight:500;">' + value + '</span>'
      + '</div>';
  }

  // ── Shared accordion helpers ──

  function _getColCount() {
    var headerRow = scope.querySelector('#ce-table-container thead tr');
    return headerRow ? headerRow.querySelectorAll('th').length : 14;
  }

  function _removeSpinner(symbol, type) {
    var spin = scope.querySelector('.ce-analysis-spinner[data-symbol="' + symbol + '"][data-type="' + type + '"]');
    if (spin) spin.parentNode.removeChild(spin);
  }

  // Returns the element to insertBefore(newNode, anchor.nextSibling) for correct panel ordering.
  // Desired visual order: actionRow → priceRow → entry → eva → dcf → comps
  function _getInsertAnchor(symbol, type) {
    var actionRow = scope.querySelector('.ce-action-row[data-symbol="' + _esc(symbol) + '"]');
    if (!actionRow) return null;
    var priceRow = scope.querySelector('.ce-price-row[data-symbol="' + symbol + '"]');
    var lastOfType = function(t) {
      return scope.querySelector('.ce-analysis-content[data-symbol="' + symbol + '"][data-type="' + t + '"]')
        || scope.querySelector('.ce-analysis-header[data-symbol="' + symbol + '"][data-type="' + t + '"]')
        || scope.querySelector('.ce-analysis-panel[data-symbol="' + symbol + '"][data-type="' + t + '"]');
    };
    if (type === 'entry') {
      return priceRow || actionRow;
    }
    if (type === 'eva') {
      return lastOfType('entry') || priceRow || actionRow;
    }
    if (type === 'dcf') {
      return lastOfType('eva') || lastOfType('entry') || priceRow || actionRow;
    }
    if (type === 'comps') {
      return lastOfType('dcf') || lastOfType('eva') || lastOfType('entry') || priceRow || actionRow;
    }
    // smart-money is last (after comps)
    return lastOfType('comps') || lastOfType('dcf') || lastOfType('eva') || lastOfType('entry') || priceRow || actionRow;
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

    var cacheMap = { entry: _entryCache, comps: _compsCache, dcf: _dcfCache, eva: _evaCache, 'smart-money': _smartMoneyCache };
    var cache = cacheMap[type] ? cacheMap[type][symbol] : null;
    if (!cache) return;
    var data = cache.data;
    var analyzedAt = cache.analyzedAt;
    var ago = _timeAgo(analyzedAt);
    var colCount = _getColCount();
    var openMap = { entry: _entryPanelOpen, comps: _compsPanelOpen, dcf: _dcfPanelOpen, eva: _evaPanelOpen, 'smart-money': _smartMoneyPanelOpen };
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
    } else if (type === 'eva') {
      // EVA summary
      var evaGrade = _getEvaGrade(data);
      var evaRoic = (data.roic_analysis || {}).roic;
      var evaWacc = (data.wacc || {}).wacc;
      var evaSpread = (evaRoic != null && evaWacc != null) ? evaRoic - evaWacc : null;
      summaryHtml = _renderGradeBadge(evaGrade);
      if (evaRoic != null) summaryHtml += ' ' + (evaRoic * 100).toFixed(1) + '% ROIC';
      if (evaSpread != null) summaryHtml += '&nbsp;&nbsp;&nbsp;' + (evaSpread >= 0 ? '+' : '') + (evaSpread * 100).toFixed(1) + '% spread';
    } else if (type === 'smart-money') {
      var smVerdict = _smComputeVerdict(
        ((data.insider_activity || {}).signal || 'no_data'),
        ((data.institutional_ownership || {}).trend || 'no_data')
      );
      summaryHtml = '<span style="color:' + smVerdict.css + '; font-weight:600;">' + _esc(smVerdict.text) + '</span>';
    }

    var labelMap = { entry: 'Entry Point Analysis', comps: 'Comps Analysis', dcf: 'DCF Analysis', eva: 'EVA/ROIC Analysis', 'smart-money': 'Smart Money Analysis' };
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
    var rendererMap = { entry: _renderEntryAnalysis, comps: _renderCompsAnalysis, dcf: _renderDcfAnalysis, eva: _renderEvaAnalysis, 'smart-money': _renderSmartMoneyPanel };
    var renderer = rendererMap[type] || _renderEntryAnalysis;
    var innerHtml = renderer(data, symbol, company);
    cntTd.innerHTML = '<div class="ce-ac-wrapper' + (isOpen ? ' open' : '') + '">'
      + '<div class="ce-ac-inner">' + innerHtml + '</div></div>';
    cntTr.appendChild(cntTd);

    // Find insertion point — order: price → entry → eva → dcf → comps
    var anchor = _getInsertAnchor(symbol, type);
    if (!anchor) return;
    anchor.parentNode.insertBefore(cntTr, anchor.nextSibling);
    anchor.parentNode.insertBefore(hdrTr, anchor.nextSibling);

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
      else if (type === 'smart-money') { delete _smartMoneyPanelOpen[symbol]; }
      else { delete _evaPanelOpen[symbol]; }
    } else {
      // Expand
      if (wrapper) wrapper.classList.add('open');
      if (chevron) chevron.classList.add('open');
      if (type === 'entry') { _entryPanelOpen[symbol] = true; }
      else if (type === 'comps') { _compsPanelOpen[symbol] = true; }
      else if (type === 'dcf') { _dcfPanelOpen[symbol] = true; }
      else if (type === 'smart-money') { _smartMoneyPanelOpen[symbol] = true; }
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

    // Insert after the right anchor — order: price → entry → eva → dcf → comps
    var anchor = _getInsertAnchor(symbol, type);
    if (anchor) anchor.parentNode.insertBefore(spinTr, anchor.nextSibling);

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
    var selectorMap = { entry: '.ce-entry-btn', comps: '.ce-comps-btn', dcf: '.ce-dcf-btn', eva: '.ce-eva-btn', 'smart-money': '.ce-sm-btn' };
    var selector = selectorMap[type] || '.ce-entry-btn';
    var btn = scope.querySelector(selector + '[data-symbol="' + symbol + '"]');
    if (!btn) return;
    var openMap = { entry: _entryPanelOpen, comps: _compsPanelOpen, dcf: _dcfPanelOpen, eva: _evaPanelOpen, 'smart-money': _smartMoneyPanelOpen };
    var cacheMap = { entry: _entryCache, comps: _compsCache, dcf: _dcfCache, eva: _evaCache, 'smart-money': _smartMoneyCache };
    var labelMap = { entry: 'Entry \uD83D\uDD0D', comps: 'Comps \uD83D\uDCCA', dcf: 'DCF \uD83D\uDCC8', eva: 'EVA \uD83C\uDFDB', 'smart-money': 'Smart $ \uD83D\uDCB0' };
    var shortMap = { entry: 'Entry', comps: 'Comps', dcf: 'DCF', eva: 'EVA', 'smart-money': 'Smart $' };
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
    fetch('/api/company-evaluator/analyses/status')
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

  // ── EVA grade lookup (checks multiple paths) ──
  function _getEvaGrade(data) {
    return data.grade
      || (data.quality && data.quality.grade)
      || (data.roic_analysis && data.roic_analysis.grade)
      || (data.eva && data.eva.grade)
      || (data.verdict && data.verdict.grade)
      || '';
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
    var grade = _getEvaGrade(data);
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
        if (!res.ok) {
          return res.json().catch(function() { return {}; }).then(function(body) {
            throw new Error(body.detail || 'HTTP ' + res.status);
          });
        }
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

    // Pagination slice
    var totalItems = companies.length;
    var totalPages = Math.ceil(totalItems / _PAGE_SIZE);
    if (_currentPage >= totalPages) _currentPage = totalPages - 1;
    if (_currentPage < 0) _currentPage = 0;
    var startIdx = _currentPage * _PAGE_SIZE;
    var endIdx = Math.min(startIdx + _PAGE_SIZE, totalItems);
    var pageCompanies = companies.slice(startIdx, endIdx);

    var hasMcap = false;
    for (var ci = 0; ci < pageCompanies.length; ci++) {
      if (pageCompanies[ci].market_cap) { hasMcap = true; break; }
    }

    var sortableStyle = _thStyle + 'cursor:pointer; user-select:none;';
    var colCount = 14 + (hasMcap ? 1 : 0); // total columns (no Actions column in header) — includes breakout column
    var html = '<table style="width:100%; border-collapse:collapse; font-size:0.85rem;">';

    // Header — NO Actions column
    html += '<thead><tr style="border-bottom:2px solid rgba(80,120,150,0.2);">';
    html += '<th style="' + _thStyle + 'width:40px;">#</th>';
    html += '<th style="' + _thStyle + '">Symbol</th>';
    html += '<th style="' + _thStyle + '">Company</th>';
    html += '<th style="' + _thStyle + '">Sector</th>';
    if (hasMcap) {
      html += '<th style="' + sortableStyle + 'text-align:right;" data-sort="market_cap">Mkt Cap' + _sortIndicator('market_cap') + '</th>';
    }
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="composite_score">Score ' + _sortIndicator('composite_score') + '</th>';
    html += '<th class="ce-breakout-col" style="' + sortableStyle + 'text-align:center; width:85px; min-width:70px;" data-sort="breakout_score">Breakout ' + _sortIndicator('breakout_score') + '</th>';
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="business_quality">Biz Qual' + _sortIndicator('business_quality') + '</th>';
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="operational_health">Ops Health' + _sortIndicator('operational_health') + '</th>';
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="capital_allocation">Cap Alloc' + _sortIndicator('capital_allocation') + '</th>';
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="growth_quality">Growth' + _sortIndicator('growth_quality') + '</th>';
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="valuation">Valuation' + _sortIndicator('valuation') + '</th>';
    html += '<th style="' + sortableStyle + 'text-align:center;" data-sort="llm_recommendation">LLM' + _sortIndicator('llm_recommendation') + '</th>';
    html += '<th style="' + _thStyle + '">Updated</th>';
    html += '</tr></thead><tbody>';

    for (var i = 0; i < pageCompanies.length; i++) {
      var c = pageCompanies[i];
      var globalIdx = startIdx + i;
      var scoreColor = _scoreColor(c.composite_score);
      var recBadge = _recBadge(c.llm_recommendation);
      var ps = c.pillar_scores || {};
      var updated = _timeAgo(c.evaluated_at);
      var stale = _isStale(c.evaluated_at);
      var sym = _esc(c.symbol || '');

      var rowStyle = 'border-bottom:none; cursor:pointer;';
      if (stale) rowStyle += ' opacity:0.65;';
      // Zebra striping
      if (i % 2 === 0) rowStyle += ' background:rgba(255,255,255,0.018);';

      // Row 1: Data row
      html += '<tr class="ce-row" data-symbol="' + sym + '" style="' + rowStyle + '">';
      html += '<td style="' + _tdStyle + 'color:#506878; font-weight:600; font-size:0.75rem; text-align:center; min-width:30px;">' + (globalIdx + 1) + '</td>';
      html += '<td style="' + _tdStyle + 'font-weight:700; color:#ffffff; font-size:0.95rem; letter-spacing:0.5px;">' + sym + '</td>';
      html += '<td style="' + _tdStyle + 'color:#b8c8d8; max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:0.82rem;">' + _esc(c.company_name || '') + '</td>';
      html += '<td style="' + _tdStyle + 'color:#7090a8; font-size:0.75rem; text-transform:uppercase; letter-spacing:0.3px;">' + _esc(c.sector || '--') + '</td>';
      if (hasMcap) {
        html += '<td style="' + _tdStyle + 'text-align:right; color:#90a8b8; font-size:0.82rem; font-weight:500; font-variant-numeric:tabular-nums;">' + _formatMarketCap(c.market_cap) + '</td>';
      }
      var _cBadgeCls = _completenessBadgeClass(c.completeness_pct);
      var _cTooltip = _completenessTooltip(c.completeness_pct, c.missing_pillar_count);
      var _scoreGlow = c.composite_score != null && c.composite_score >= 75 ? 'text-shadow:0 0 12px ' + scoreColor + '40;' : c.composite_score != null && c.composite_score >= 55 ? 'text-shadow:0 0 10px ' + scoreColor + '25;' : '';
      html += '<td style="' + _tdStyle + 'text-align:center;"><span style="display:inline-flex; align-items:center; gap:5px; justify-content:center;"><span style="color:' + scoreColor + '; font-weight:700; font-size:1.05rem; ' + _scoreGlow + '">' + (c.composite_score != null ? c.composite_score.toFixed(1) : '--') + '</span><span class="' + _cBadgeCls + '" title="' + _esc(_cTooltip) + '" style="display:inline-block; width:8px; height:8px; border-radius:50%; flex-shrink:0;"></span></span></td>';
      html += '<td class="ce-breakout-cell" style="' + _tdStyle + 'text-align:center;">' + _renderBreakoutScore(c.breakout_score) + '</td>';
      html += _pillarCell(ps.business_quality);
      html += _pillarCell(ps.operational_health);
      html += _pillarCell(ps.capital_allocation);
      html += _pillarCell(ps.growth_quality);
      html += _pillarCell(ps.valuation);
      html += '<td style="' + _tdStyle + 'text-align:center;">' + recBadge + '</td>';

      var updatedStyle = _tdStyle + 'font-size:0.72rem;';
      if (stale) {
        updatedStyle += ' color:#ff9800;';
        updated = '\u26A0 ' + updated;
      } else {
        updatedStyle += ' color:#506878;';
      }
      html += '<td style="' + updatedStyle + '">' + _esc(updated) + '</td>';
      html += '</tr>';

      // Row 2: Action row
      var actionBg = i % 2 === 0 ? 'background:rgba(255,255,255,0.018);' : '';
      html += '<tr class="ce-action-row" data-symbol="' + sym + '" style="border-bottom:1px solid rgba(80,120,150,0.08); ' + actionBg + '">';
      html += '<td colspan="' + colCount + '" style="padding:4px 10px; white-space:nowrap;">';
      var _abtnBase = 'padding:3px 10px; border-radius:4px; font-size:0.72rem; font-weight:500; cursor:pointer; '
        + 'background:rgba(20,40,55,0.8); color:#90b0c8; border:1px solid rgba(80,120,150,0.25); letter-spacing:0.2px; transition:all 0.15s ease;';
      html += ' <button class="ce-buy-btn ce-action-btn" data-symbol="' + sym + '" data-idx="' + i + '" '
        + 'style="padding:3px 10px; border-radius:4px; font-size:0.72rem; font-weight:600; cursor:pointer; letter-spacing:0.2px; transition:all 0.15s ease; '
        + _buyBtnStyle(c.llm_recommendation) + '">Buy \u25B6</button>';
      html += ' <button class="ce-price-btn ce-action-btn" data-symbol="' + sym + '" data-idx="' + i + '" '
        + 'style="' + _abtnBase + '"'
        + '>Price \uD83D\uDCB2</button>';
      html += ' <button class="ce-entry-btn ce-action-btn" data-symbol="' + sym + '" data-idx="' + i + '" '
        + 'style="' + _abtnBase + '"'
        + '>Entry \uD83D\uDD0D</button>';
      html += ' <button class="ce-comps-btn ce-action-btn" data-symbol="' + sym + '" data-idx="' + i + '" '
        + 'style="' + _abtnBase + '"'
        + '>Comps \uD83D\uDCCA</button>';
      html += ' <button class="ce-dcf-btn ce-action-btn" data-symbol="' + sym + '" data-idx="' + i + '" '
        + 'style="' + _abtnBase + '"'
        + '>DCF \uD83D\uDCC8</button>';
      html += ' <button class="ce-eva-btn ce-action-btn" data-symbol="' + sym + '" data-idx="' + i + '" '
        + 'style="' + _abtnBase + '"'
        + '>EVA \uD83C\uDFDB</button>';
      html += ' <button class="ce-sm-btn ce-action-btn" data-symbol="' + sym + '" data-idx="' + i + '" '
        + 'style="' + _abtnBase + '"'
        + '>Smart $ \uD83D\uDCB0</button>';
      html += ' <button class="ce-raw-btn ce-action-btn" data-symbol="' + sym + '" data-idx="' + i + '" '
        + 'style="' + _abtnBase + '"'
        + '>Raw \uD83D\uDD2C</button>';
      html += '</td>';
      html += '</tr>';
    }

    html += '</tbody></table>';

    // Paging controls
    if (totalPages > 1) {
      html += '<div class="ce-paging">';
      html += '<button class="ce-paging-btn ce-paging-prev"' + (_currentPage <= 0 ? ' disabled' : '') + '>&laquo; Prev</button>';
      // Page number buttons — show window around current page
      var windowSize = 5;
      var pageStart = Math.max(0, _currentPage - Math.floor(windowSize / 2));
      var pageEnd = Math.min(totalPages, pageStart + windowSize);
      if (pageEnd - pageStart < windowSize) pageStart = Math.max(0, pageEnd - windowSize);
      if (pageStart > 0) {
        html += '<button class="ce-paging-btn ce-paging-num" data-page="0">1</button>';
        if (pageStart > 1) html += '<span class="ce-paging-ellipsis">&hellip;</span>';
      }
      for (var pi = pageStart; pi < pageEnd; pi++) {
        html += '<button class="ce-paging-btn ce-paging-num' + (pi === _currentPage ? ' active' : '') + '" data-page="' + pi + '">' + (pi + 1) + '</button>';
      }
      if (pageEnd < totalPages) {
        if (pageEnd < totalPages - 1) html += '<span class="ce-paging-ellipsis">&hellip;</span>';
        html += '<button class="ce-paging-btn ce-paging-num" data-page="' + (totalPages - 1) + '">' + totalPages + '</button>';
      }
      html += '<button class="ce-paging-btn ce-paging-next"' + (_currentPage >= totalPages - 1 ? ' disabled' : '') + '>Next &raquo;</button>';
      html += '<span class="ce-paging-info">' + (startIdx + 1) + '\u2013' + endIdx + ' of ' + totalItems + '</span>';
      html += '</div>';
    }

    container.innerHTML = html;

    // Bind data row clicks (opens detail drawer)
    var rows = container.querySelectorAll('.ce-row');
    for (var r = 0; r < rows.length; r++) {
      rows[r].addEventListener('click', _onRowClick);
      (function(row) {
        var baseColor = row.style.background || '';
        row.addEventListener('mouseover', function() { this.style.background = 'rgba(80,140,180,0.08)'; });
        row.addEventListener('mouseout', function() { this.style.background = baseColor; });
      })(rows[r]);
    }

    // Bind sort header clicks
    var sortHeaders = container.querySelectorAll('th[data-sort]');
    for (var si = 0; si < sortHeaders.length; si++) {
      sortHeaders[si].addEventListener('click', (function(col) {
        return function() { _onSortClick(col); };
      })(sortHeaders[si].getAttribute('data-sort')));
      sortHeaders[si].addEventListener('mouseover', function() { this.style.color = '#00eaff'; this.style.opacity = '1'; });
      sortHeaders[si].addEventListener('mouseout', function() { this.style.color = '#00eaff'; this.style.opacity = ''; });
    }

    // Bind Buy buttons
    var buyBtns = container.querySelectorAll('.ce-buy-btn');
    for (var bi = 0; bi < buyBtns.length; bi++) {
      buyBtns[bi].addEventListener('click', (function(sym, idx) {
        return function(e) {
          e.stopPropagation();
          _buyStock(sym, pageCompanies[idx]);
        };
      })(buyBtns[bi].getAttribute('data-symbol'), parseInt(buyBtns[bi].getAttribute('data-idx'), 10)));
    }

    // Bind Price buttons
    var priceBtns = container.querySelectorAll('.ce-price-btn');
    for (var pri = 0; pri < priceBtns.length; pri++) {
      priceBtns[pri].addEventListener('click', (function(sym) {
        return function(e) {
          e.stopPropagation();
          _handlePrice(sym);
        };
      })(priceBtns[pri].getAttribute('data-symbol')));
    }

    // Bind Entry Analysis buttons
    var entryBtns = container.querySelectorAll('.ce-entry-btn');
    for (var ei = 0; ei < entryBtns.length; ei++) {
      entryBtns[ei].addEventListener('click', (function(sym, idx) {
        return function(e) {
          e.stopPropagation();
          _entryAnalysis(sym, pageCompanies[idx]);
        };
      })(entryBtns[ei].getAttribute('data-symbol'), parseInt(entryBtns[ei].getAttribute('data-idx'), 10)));
    }

    // Bind Comps buttons
    var compsBtns = container.querySelectorAll('.ce-comps-btn');
    for (var cbi = 0; cbi < compsBtns.length; cbi++) {
      compsBtns[cbi].addEventListener('click', (function(sym, idx) {
        return function(e) {
          e.stopPropagation();
          _handleCompsAnalysis(sym, pageCompanies[idx]);
        };
      })(compsBtns[cbi].getAttribute('data-symbol'), parseInt(compsBtns[cbi].getAttribute('data-idx'), 10)));
    }

    // Bind DCF buttons
    var dcfBtns = container.querySelectorAll('.ce-dcf-btn');
    for (var di = 0; di < dcfBtns.length; di++) {
      dcfBtns[di].addEventListener('click', (function(sym, idx) {
        return function(e) {
          e.stopPropagation();
          _handleDcfAnalysis(sym, pageCompanies[idx]);
        };
      })(dcfBtns[di].getAttribute('data-symbol'), parseInt(dcfBtns[di].getAttribute('data-idx'), 10)));
    }

    // Bind EVA buttons
    var evaBtns = container.querySelectorAll('.ce-eva-btn');
    for (var evi = 0; evi < evaBtns.length; evi++) {
      evaBtns[evi].addEventListener('click', (function(sym, idx) {
        return function(e) {
          e.stopPropagation();
          _handleEvaAnalysis(sym, pageCompanies[idx]);
        };
      })(evaBtns[evi].getAttribute('data-symbol'), parseInt(evaBtns[evi].getAttribute('data-idx'), 10)));
    }

    // Bind Raw Data Inspector buttons
    var rawBtns = container.querySelectorAll('.ce-raw-btn');
    for (var rbi = 0; rbi < rawBtns.length; rbi++) {
      rawBtns[rbi].addEventListener('click', (function(sym) {
        return function(e) {
          e.stopPropagation();
          _handleRawDataClick(sym);
        };
      })(rawBtns[rbi].getAttribute('data-symbol')));
    }

    // Bind Smart Money buttons
    var smBtns = container.querySelectorAll('.ce-sm-btn');
    for (var smi = 0; smi < smBtns.length; smi++) {
      smBtns[smi].addEventListener('click', (function(sym) {
        return function(e) {
          e.stopPropagation();
          _handleSmartMoney(sym);
        };
      })(smBtns[smi].getAttribute('data-symbol')));
    }

    // Bind paging controls
    var prevBtn = container.querySelector('.ce-paging-prev');
    if (prevBtn) prevBtn.addEventListener('click', function() {
      if (_currentPage > 0) { _currentPage--; renderTable(_lastFiltered); container.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    });
    var nextBtn = container.querySelector('.ce-paging-next');
    if (nextBtn) nextBtn.addEventListener('click', function() {
      _currentPage++; renderTable(_lastFiltered); container.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    var pageNums = container.querySelectorAll('.ce-paging-num');
    for (var pni = 0; pni < pageNums.length; pni++) {
      pageNums[pni].addEventListener('click', (function(pg) {
        return function() { _currentPage = pg; renderTable(_lastFiltered); container.scrollIntoView({ behavior: 'smooth', block: 'start' }); };
      })(parseInt(pageNums[pni].getAttribute('data-page'), 10)));
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

  // ── Add Stock handler ──
  function _handleAddStock() {
    var input = scope.querySelector('#ce-add-stock-input');
    var statusEl = scope.querySelector('#ce-add-stock-status');
    var btn = scope.querySelector('#ce-add-stock-btn');
    if (!input) return;
    var symbol = input.value.trim().toUpperCase().replace(/[^A-Z.]/g, '');
    if (!symbol) { _showAddStatus(statusEl, 'Enter a symbol', 'info'); return; }

    btn.disabled = true;
    btn.textContent = '…';
    _showAddStatus(statusEl, 'Adding ' + symbol + '…', 'info');

    fetch('/api/company-evaluator/universe/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: symbol })
    })
      .then(function(res) { return res.json(); })
      .then(function(data) {
        if (!data.ok) {
          _showAddStatus(statusEl, data.error || 'Failed', 'error');
          return;
        }
        if (data.action === 'exists') {
          _showAddStatus(statusEl, data.message, 'info');
          // Auto-fill search box so user can find it
          var searchEl = scope.querySelector('#ce-search-input');
          if (searchEl) {
            searchEl.value = symbol;
            searchEl.dispatchEvent(new Event('input'));
          }
        } else {
          // added or reactivated
          _showAddStatus(statusEl, '\u2713 ' + (data.message || symbol + ' added'), 'success');
          input.value = '';
          setTimeout(refresh, 2000);
        }
      })
      .catch(function(err) {
        _showAddStatus(statusEl, 'Connection failed: ' + err.message, 'error');
      })
      .finally(function() {
        btn.disabled = false;
        btn.textContent = 'Add';
      });
  }

  function _showAddStatus(el, msg, type) {
    if (!el) return;
    el.textContent = msg;
    el.className = 'ce-add-status ' + type;
    clearTimeout(el._timer);
    el._timer = setTimeout(function() { el.textContent = ''; el.className = 'ce-add-status'; }, 5000);
  }

  // ── Keyboard handler ──
  function _onKeydown(e) {
    if (e.key === 'Escape') closeDetail();
  }

  // ── Bind events ──
  var _filterIds = ['#ce-sector-filter', '#ce-mcap-filter', '#ce-rating-filter', '#ce-tier-filter', '#ce-score-filter', '#ce-completeness-filter'];
  for (var fi = 0; fi < _filterIds.length; fi++) {
    var fEl = scope.querySelector(_filterIds[fi]);
    if (fEl) fEl.addEventListener('change', function() { _updateFilterActiveStates(); applyFilters(); });
  }

  // Breakout filter with auto-sort: when user selects a breakout filter, auto-sort by breakout_score desc
  var _breakoutFilterEl = scope.querySelector('#ce-breakout-filter');
  if (_breakoutFilterEl) {
    _breakoutFilterEl.addEventListener('change', function() {
      if (_breakoutFilterEl.value) {
        _sortCol = 'breakout_score';
        _sortAsc = false;
      }
      _updateFilterActiveStates();
      applyFilters();
    });
  }

  var searchInput = scope.querySelector('#ce-search-input');
  if (searchInput) {
    var _searchDebounce = null;
    searchInput.addEventListener('input', function() {
      clearTimeout(_searchDebounce);
      _searchDebounce = setTimeout(function() { _updateClearButtonVisibility(); applyFilters(); }, 200);
    });
  }

  // Clear all filters button (replaces old reset button)
  var clearBtn = scope.querySelector('#ce-clear-filters');
  if (clearBtn) clearBtn.addEventListener('click', resetFilters);

  var refreshBtn = scope.querySelector('#ce-refresh-btn');
  if (refreshBtn) refreshBtn.addEventListener('click', refresh);

  var crawlBtn = scope.querySelector('#ce-crawl-btn');
  if (crawlBtn) crawlBtn.addEventListener('click', triggerCrawl);

  var addStockBtn = scope.querySelector('#ce-add-stock-btn');
  if (addStockBtn) addStockBtn.addEventListener('click', _handleAddStock);
  var addStockInput = scope.querySelector('#ce-add-stock-input');
  if (addStockInput) addStockInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { e.preventDefault(); _handleAddStock(); }
  });

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

  // ── Raw Data Inspector Modal ──────────────────────────────────────────

  function _rawCreateModal() {
    if (document.getElementById('ce-raw-modal')) return;
    var div = document.createElement('div');
    div.id = 'ce-raw-modal';
    div.style.cssText = 'display:none; position:fixed; top:0; left:0; right:0; bottom:0; z-index:2147483647; align-items:center; justify-content:center;';
    div.innerHTML =
      '<div id="ce-raw-backdrop" style="position:absolute; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.7); z-index:2147483646;"></div>' +
      '<div id="ce-raw-content" style="position:relative; z-index:2147483647; background:#0a1920; border:1px solid #1a3a4a; border-radius:8px; width:90%; max-width:1100px; max-height:90vh; overflow-y:auto; padding:24px; color:#d0e0e8; font-family:inherit;">' +
        '<div id="ce-raw-header" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; padding-bottom:16px; border-bottom:1px solid #1a3a4a;">' +
          '<h2 id="ce-raw-title" style="margin:0; font-size:1.15rem; font-weight:600; color:#e0e0e0;">Raw Data Inspector</h2>' +
          '<button id="ce-raw-close" style="background:none; border:none; color:rgba(224,224,224,0.5); font-size:1.4rem; cursor:pointer; padding:4px 8px; line-height:1;" title="Close">\u2715</button>' +
        '</div>' +
        '<div id="ce-raw-body"></div>' +
      '</div>';
    // Append inside .shell so the modal is visible in Fullscreen API mode
    // (.shell is the requestFullscreen() target — children of body outside it are hidden)
    var container = document.querySelector('.shell') || document.body;
    container.appendChild(div);

    document.getElementById('ce-raw-backdrop').addEventListener('click', _rawClose);
    document.getElementById('ce-raw-close').addEventListener('click', _rawClose);
  }

  function _rawClose() {
    var modal = document.getElementById('ce-raw-modal');
    if (modal) {
      modal.style.display = 'none';
      var body = document.getElementById('ce-raw-body');
      if (body) body.innerHTML = '';
    }
  }

  function _rawOnKeydown(e) {
    if (e.key === 'Escape') _rawClose();
  }
  document.addEventListener('keydown', _rawOnKeydown);

  async function _handleRawDataClick(symbol) {
    _rawCreateModal();
    var modal = document.getElementById('ce-raw-modal');
    modal.style.display = 'flex';
    var title = document.getElementById('ce-raw-title');
    title.textContent = 'Raw Data Inspector \u2014 ' + symbol;
    var body = document.getElementById('ce-raw-body');
    body.innerHTML = '<div style="text-align:center; padding:40px; color:rgba(224,224,224,0.4);"><div style="font-size:1.5rem; margin-bottom:12px;">&#9203;</div>Loading raw data for <strong>' + _esc(symbol) + '</strong>...</div>';

    try {
      var resp = await fetch('/api/company-evaluator/companies/' + encodeURIComponent(symbol) + '/raw', { signal: AbortSignal.timeout(15000) });
      if (!resp.ok) {
        var errData = {};
        try { errData = await resp.json(); } catch(_) {}
        _rawRenderError(errData.detail || ('HTTP ' + resp.status));
        return;
      }
      var data = await resp.json();
      _rawRender(data);
    } catch (err) {
      _rawRenderError(err.message || 'Request failed');
    }
  }

  function _rawRenderError(msg) {
    var body = document.getElementById('ce-raw-body');
    if (!body) return;
    body.innerHTML =
      '<div style="background:rgba(255,80,80,0.1); border:1px solid rgba(255,80,80,0.4); border-left:4px solid #ff5050; padding:16px 20px; border-radius:4px;">' +
        '<div style="color:#ff7070; font-weight:600; margin-bottom:6px;">\u26A0  Failed to load raw data</div>' +
        '<div style="color:rgba(224,224,224,0.6); font-size:0.85rem;">' + _esc(msg) + '</div>' +
      '</div>';
  }

  // ── Number formatting helpers ──

  function _rawFmtMarketCap(val) {
    if (val == null) return '\u2014';
    if (val >= 1e12) return '$' + (val / 1e12).toFixed(1) + 'T';
    if (val >= 1e9) return '$' + (val / 1e9).toFixed(1) + 'B';
    if (val >= 1e6) return '$' + (val / 1e6).toFixed(0) + 'M';
    if (val >= 1e3) return '$' + (val / 1e3).toFixed(0) + 'K';
    return '$' + val;
  }

  function _rawFmtNumber(val) {
    if (val == null) return '\u2014';
    if (typeof val !== 'number') return _esc(String(val));
    return val.toLocaleString();
  }

  function _rawFmtPct(val) {
    if (val == null) return '\u2014';
    if (typeof val !== 'number') return _esc(String(val));
    // If value looks like a ratio (< 2 absolute), show as pct
    if (Math.abs(val) <= 2) return (val * 100).toFixed(1) + '%';
    return val.toFixed(1) + '%';
  }

  function _rawScoreColor(score) {
    if (score == null) return 'rgba(224,224,224,0.3)';
    if (score >= 70) return '#00c853';
    if (score >= 40) return '#ffd600';
    return '#ff1744';
  }

  function _rawCompleteColor(pct) {
    if (pct == null) return 'rgba(224,224,224,0.3)';
    if (pct >= 80) return '#00c853';
    if (pct >= 50) return '#ffd600';
    return '#ff1744';
  }

  // ── Main render orchestrator ──

  function _rawRender(data) {
    var body = document.getElementById('ce-raw-body');
    if (!body) return;
    var html = '';

    html += _rawRenderHeader(data);
    html += _rawRenderDiagnostics(data.diagnostics, data.composite);
    html += _rawRenderDataSources(data.data_sources);
    html += _rawRenderProfile(data.profile);
    html += _rawRenderPillars(data.pillars);
    html += _rawRenderRawFinancials(data.raw_financials, data.profile);
    html += _rawRenderLLM(data.llm_analysis);

    body.innerHTML = html;

    // Wire collapsible sections
    body.querySelectorAll('[data-raw-toggle]').forEach(function(btn) {
      btn.addEventListener('click', function() {
        var target = document.getElementById(btn.getAttribute('data-raw-toggle'));
        if (!target) return;
        var isOpen = target.style.display !== 'none';
        target.style.display = isOpen ? 'none' : 'block';
        btn.querySelector('.ce-raw-chevron').textContent = isOpen ? '\u25B6' : '\u25BC';
      });
    });
  }

  // ── Section 1: Header Summary ──

  function _rawRenderHeader(data) {
    var comp = data.composite || {};
    var prof = data.profile || {};
    var score = comp.composite_score;
    var rank = comp.rank;
    var rating = comp.rating || '\u2014';
    var completeness = comp.overall_completeness_pct;
    var evalAt = data.evaluated_at || comp.evaluated_at;

    return '<div style="display:flex; flex-wrap:wrap; gap:16px; align-items:center; margin-bottom:20px; padding:14px 18px; background:#0d2030; border:1px solid #1a3a4a; border-radius:6px;">' +
      '<div style="font-size:1.4rem; font-weight:700; color:' + _rawScoreColor(score) + ';">' + (score != null ? score.toFixed(1) : '--') + '</div>' +
      '<div style="font-size:0.8rem; color:rgba(224,224,224,0.5);">Rank: <strong style="color:#e0e0e0;">' + (rank != null ? '#' + rank : '--') + '</strong></div>' +
      '<div>' + _recBadge(rating) + '</div>' +
      '<div style="font-size:0.8rem; color:rgba(224,224,224,0.5);">Completeness: <strong style="color:' + _rawCompleteColor(completeness) + ';">' + (completeness != null ? completeness.toFixed(0) + '%' : '--') + '</strong></div>' +
      (evalAt ? '<div style="font-size:0.75rem; color:rgba(224,224,224,0.3); margin-left:auto;">Evaluated: ' + _timeAgo(evalAt) + '</div>' : '') +
    '</div>';
  }

  // ── Section 2: Diagnostics ──

  function _rawRenderDiagnostics(diag, composite) {
    if (!diag) return '';
    var fetchErrs = diag.fetch_errors || [];
    var dqFlags = diag.data_quality_flags || [];
    var missingWarns = diag.missing_data_warnings || [];
    var lowCompleteness = composite && composite.overall_completeness_pct != null && composite.overall_completeness_pct < 50;

    if (!fetchErrs.length && !dqFlags.length && !missingWarns.length && !lowCompleteness) return '';

    var html = '<div style="background:rgba(255,80,80,0.08); border:1px solid rgba(255,80,80,0.3); border-left:4px solid #ff5050; padding:14px 18px; margin-bottom:16px; border-radius:4px;">';
    html += '<div style="color:#ff7070; font-weight:700; font-size:0.85rem; margin-bottom:10px;">\u26A0\uFE0F  DATA QUALITY ISSUES DETECTED</div>';

    if (fetchErrs.length) {
      html += '<div style="margin-bottom:8px;"><div style="color:rgba(224,224,224,0.5); font-size:0.75rem; font-weight:600; text-transform:uppercase; margin-bottom:4px;">Fetch Errors</div>';
      for (var i = 0; i < fetchErrs.length; i++) {
        var fe = fetchErrs[i];
        var feText = typeof fe === 'string' ? fe : (fe.source || '') + ': ' + (fe.error || fe.message || JSON.stringify(fe));
        html += '<div style="color:rgba(224,224,224,0.6); font-size:0.8rem; margin-left:12px;">\u2022 ' + _esc(feText) + '</div>';
      }
      html += '</div>';
    }

    if (dqFlags.length) {
      html += '<div style="margin-bottom:8px;"><div style="color:rgba(224,224,224,0.5); font-size:0.75rem; font-weight:600; text-transform:uppercase; margin-bottom:4px;">Data Quality Flags (' + dqFlags.length + ' rejected inputs)</div>';
      for (var j = 0; j < dqFlags.length; j++) {
        var dq = dqFlags[j];
        var dqText = typeof dq === 'string' ? dq : (dq.pillar ? dq.pillar + '.' : '') + (dq.metric || '') + ' = ' + (dq.value != null ? dq.value : '?') + (dq.reason ? ' (' + dq.reason + ')' : '');
        html += '<div style="color:#ff7070; font-size:0.8rem; margin-left:12px;">\u2022 ' + _esc(dqText) + '</div>';
      }
      html += '</div>';
    }

    if (missingWarns.length) {
      html += '<div style="margin-bottom:8px;"><div style="color:rgba(224,224,224,0.5); font-size:0.75rem; font-weight:600; text-transform:uppercase; margin-bottom:4px;">Missing Data Warnings</div>';
      for (var k = 0; k < missingWarns.length; k++) {
        html += '<div style="color:rgba(224,224,224,0.6); font-size:0.8rem; margin-left:12px;">\u2022 ' + _esc(missingWarns[k]) + '</div>';
      }
      html += '</div>';
    }

    if (lowCompleteness) {
      html += '<div style="color:#ff9800; font-size:0.8rem; margin-top:6px;">\u26A0 Overall completeness is below 50% \u2014 scores may be unreliable.</div>';
    }

    html += '</div>';
    return html;
  }

  // ── Section 3: Data Sources ──

  function _rawRenderDataSources(sources) {
    if (!sources || !sources.length) return '';
    var html = '<div style="margin-bottom:16px;">';
    html += '<div style="color:rgba(224,224,224,0.5); font-size:0.75rem; font-weight:600; text-transform:uppercase; margin-bottom:8px;">Data Sources</div>';
    html += '<table style="width:100%; border-collapse:collapse; font-size:0.8rem;">';
    html += '<thead><tr style="border-bottom:1px solid rgba(255,255,255,0.08);">';
    html += '<th style="padding:6px 10px; text-align:left; color:rgba(224,224,224,0.4); font-weight:600;">Source</th>';
    html += '<th style="padding:6px 10px; text-align:left; color:rgba(224,224,224,0.4); font-weight:600;">Provider</th>';
    html += '<th style="padding:6px 10px; text-align:left; color:rgba(224,224,224,0.4); font-weight:600;">Status</th>';
    html += '<th style="padding:6px 10px; text-align:left; color:rgba(224,224,224,0.4); font-weight:600;">Fetched</th>';
    html += '<th style="padding:6px 10px; text-align:left; color:rgba(224,224,224,0.4); font-weight:600;">Error</th>';
    html += '</tr></thead><tbody>';

    for (var i = 0; i < sources.length; i++) {
      var s = sources[i];
      var ok = s.success || s.status === 'ok' || s.status === 'success';
      var statusIcon = ok ? '<span style="color:#00c853;">\u2705 OK</span>' : '<span style="color:#ff5050;">\u274C FAILED</span>';
      html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.04);">';
      html += '<td style="padding:6px 10px; color:rgba(224,224,224,0.7);">' + _esc(s.name || s.source || '\u2014') + '</td>';
      html += '<td style="padding:6px 10px; color:rgba(224,224,224,0.5);">' + _esc(s.provider || '\u2014') + '</td>';
      html += '<td style="padding:6px 10px;">' + statusIcon + '</td>';
      html += '<td style="padding:6px 10px; color:rgba(224,224,224,0.4);">' + (s.fetched_at ? _timeAgo(s.fetched_at) : '\u2014') + '</td>';
      html += '<td style="padding:6px 10px; color:#ff7070; font-size:0.75rem;">' + _esc(s.error || '\u2014') + '</td>';
      html += '</tr>';
    }

    html += '</tbody></table></div>';
    return html;
  }

  // ── Section 4: Profile ──

  function _rawRenderProfile(profile) {
    if (!profile) return '';
    var fields = [
      ['Symbol', profile.symbol],
      ['Name', profile.name || profile.company_name],
      ['Sector', profile.sector],
      ['Industry', profile.industry],
      ['Market Cap', _rawFmtMarketCap(profile.market_cap)],
      ['Current Price', profile.current_price != null ? '$' + Number(profile.current_price).toFixed(2) : null],
      ['Shares Outstanding', profile.shares_outstanding != null ? _rawFmtNumber(profile.shares_outstanding) : null],
      ['Country', profile.country],
      ['Exchange', profile.exchange],
      ['Employees', profile.employees != null ? _rawFmtNumber(profile.employees) : null],
    ];

    var html = '<div style="margin-bottom:16px;">';
    html += '<div style="color:rgba(224,224,224,0.5); font-size:0.75rem; font-weight:600; text-transform:uppercase; margin-bottom:8px;">Company Profile</div>';
    html += '<div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:4px 24px; background:#0d2030; border:1px solid #1a3a4a; border-radius:6px; padding:12px 16px;">';

    for (var i = 0; i < fields.length; i++) {
      var label = fields[i][0];
      var value = fields[i][1];
      html += '<div style="display:flex; justify-content:space-between; padding:3px 0; border-bottom:1px solid rgba(255,255,255,0.03);">';
      html += '<span style="color:rgba(224,224,224,0.4); font-size:0.78rem;">' + label + '</span>';
      html += '<span style="color:rgba(224,224,224,0.8); font-size:0.78rem; font-weight:500;">' + _esc(value != null ? String(value) : '\u2014') + '</span>';
      html += '</div>';
    }

    html += '</div></div>';
    return html;
  }

  // ── Section 5: Pillar Breakdown ──

  var _rawPillarNames = {
    'business_quality': 'Business Quality',
    'operational_health': 'Operational & Financial Health',
    'capital_allocation': 'Capital Allocation',
    'growth_quality': 'Growth Quality',
    'valuation_expectations': 'Valuation & Expectations',
    'valuation': 'Valuation & Expectations'
  };

  function _rawRenderPillars(pillars) {
    if (!pillars) return '';
    var html = '<div style="margin-bottom:16px;">';
    html += '<div style="color:rgba(224,224,224,0.5); font-size:0.75rem; font-weight:600; text-transform:uppercase; margin-bottom:10px;">Pillar Breakdown</div>';

    var keys = Object.keys(pillars);
    for (var i = 0; i < keys.length; i++) {
      var pKey = keys[i];
      var p = pillars[pKey];
      if (!p) continue;
      html += _rawRenderOnePillar(pKey, p);
    }

    html += '</div>';
    return html;
  }

  function _rawRenderOnePillar(key, p) {
    var name = _rawPillarNames[key] || key.replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
    var score = p.score != null ? p.score : p.pillar_score;
    var rawScore = p.raw_score;
    var capApplied = p.cap_applied;
    var completeness = p.completeness_pct;
    var metrics = p.metrics || [];
    var missing = p.missing || [];
    var rejected = p.rejected || [];

    // Metric count
    var totalMetrics = (metrics ? metrics.length : 0) + (missing ? missing.length : 0) + (rejected ? rejected.length : 0);
    var validMetrics = metrics ? metrics.length : 0;

    var html = '<div style="background:#0d2030; border:1px solid #1a3a4a; border-radius:6px; padding:14px 18px; margin-bottom:10px;">';
    // Pillar header
    html += '<div style="display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin-bottom:10px;">';
    html += '<span style="font-weight:600; font-size:0.9rem; color:rgba(224,224,224,0.85);">' + _esc(name) + '</span>';
    html += '<span style="font-size:1.05rem; font-weight:700; color:' + _rawScoreColor(score) + ';">' + (score != null ? score.toFixed(1) : '--') + '</span>';

    if (capApplied && rawScore != null) {
      html += '<span style="font-size:0.75rem; color:#ff9800;">(capped from ' + rawScore.toFixed(1) + ')</span>';
    }

    if (completeness != null) {
      html += '<span style="font-size:0.75rem; color:' + _rawCompleteColor(completeness) + ';">' + completeness.toFixed(0) + '% complete</span>';
    } else if (totalMetrics > 0) {
      html += '<span style="font-size:0.75rem; color:rgba(224,224,224,0.4);">(' + validMetrics + ' of ' + totalMetrics + ' metrics)</span>';
    }

    html += '</div>';

    // Metrics table
    if (metrics.length || missing.length || rejected.length) {
      html += '<table style="width:100%; border-collapse:collapse; font-size:0.78rem;">';
      html += '<thead><tr style="border-bottom:1px solid rgba(255,255,255,0.06);">';
      html += '<th style="padding:4px 8px; text-align:left; color:rgba(224,224,224,0.3); font-weight:600; width:20px;"></th>';
      html += '<th style="padding:4px 8px; text-align:left; color:rgba(224,224,224,0.3); font-weight:600;">Metric</th>';
      html += '<th style="padding:4px 8px; text-align:right; color:rgba(224,224,224,0.3); font-weight:600;">Raw Value</th>';
      html += '<th style="padding:4px 8px; text-align:right; color:rgba(224,224,224,0.3); font-weight:600;">Sub-Score</th>';
      html += '</tr></thead><tbody>';

      // Valid metrics
      for (var mi = 0; mi < metrics.length; mi++) {
        var m = metrics[mi];
        var mName = m.name || m.metric || 'unknown';
        var mRaw = m.raw_value != null ? m.raw_value : m.value;
        var mScore = m.sub_score != null ? m.sub_score : m.score;
        html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.03);">';
        html += '<td style="padding:4px 8px; color:#50c0a0;">\u2713</td>';
        html += '<td style="padding:4px 8px; color:rgba(224,224,224,0.7);">' + _esc(mName) + '</td>';
        html += '<td style="padding:4px 8px; text-align:right; color:rgba(224,224,224,0.6);">' + (mRaw != null ? (typeof mRaw === 'number' ? mRaw.toFixed(4) : _esc(String(mRaw))) : '\u2014') + '</td>';
        html += '<td style="padding:4px 8px; text-align:right; color:' + _rawScoreColor(mScore) + '; font-weight:600;">' + (mScore != null ? mScore.toFixed(1) : '\u2014') + '</td>';
        html += '</tr>';
      }

      // Missing metrics
      for (var xi = 0; xi < missing.length; xi++) {
        var xm = missing[xi];
        var xName = typeof xm === 'string' ? xm : (xm.name || xm.metric || 'unknown');
        html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.03);">';
        html += '<td style="padding:4px 8px; color:#708090;">\u2717</td>';
        html += '<td style="padding:4px 8px; color:#708090;">' + _esc(xName) + '</td>';
        html += '<td style="padding:4px 8px; text-align:right; color:#708090;">\u2014</td>';
        html += '<td style="padding:4px 8px; text-align:right; color:#708090;">missing</td>';
        html += '</tr>';
      }

      // Rejected outliers
      for (var ri = 0; ri < rejected.length; ri++) {
        var rm = rejected[ri];
        var rName = rm.name || rm.metric || 'unknown';
        var rVal = rm.raw_value != null ? rm.raw_value : rm.value;
        var rReason = rm.reason || 'out of range';
        html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.03);">';
        html += '<td style="padding:4px 8px; color:#ff7070;">\u26A0</td>';
        html += '<td style="padding:4px 8px; color:#ff7070;">' + _esc(rName) + '</td>';
        html += '<td style="padding:4px 8px; text-align:right; color:#ff7070;">' + (rVal != null ? (typeof rVal === 'number' ? rVal.toFixed(4) : _esc(String(rVal))) : '?') + '</td>';
        html += '<td style="padding:4px 8px; text-align:right; color:#ff7070; font-size:0.72rem;">REJECTED (' + _esc(rReason) + ')</td>';
        html += '</tr>';
      }

      html += '</tbody></table>';
    }

    if (capApplied) {
      html += '<div style="margin-top:8px; font-size:0.75rem; color:#ff9800;">Cap Applied: raw ' + (rawScore != null ? rawScore.toFixed(1) : '?') + ' \u2192 capped to ' + (score != null ? score.toFixed(1) : '?') + '</div>';
    }

    html += '</div>';
    return html;
  }

  // ── Section 6: Raw Financials (collapsible) ──

  function _rawRenderRawFinancials(rawFin, profile) {
    var sym = (profile && profile.symbol) || '';
    var sectionId = 'ce-raw-financials-body';

    var html = '<div style="margin-bottom:16px;">';
    html += '<div data-raw-toggle="' + sectionId + '" style="display:flex; align-items:center; gap:8px; cursor:pointer; padding:8px 0; user-select:none;">';
    html += '<span class="ce-raw-chevron" style="color:rgba(224,224,224,0.4); font-size:0.7rem;">\u25B6</span>';
    html += '<span style="color:rgba(224,224,224,0.5); font-size:0.75rem; font-weight:600; text-transform:uppercase;">Raw Financials</span>';
    html += '</div>';
    html += '<div id="' + sectionId + '" style="display:none;">';

    if (!rawFin) {
      html += '<div style="background:#0d2030; border:1px solid #1a3a4a; border-radius:6px; padding:16px 20px;">';
      html += '<div style="color:rgba(224,224,224,0.6); font-size:0.85rem; margin-bottom:12px;">Raw financials not yet persisted for this evaluation.</div>';
      html += '<div style="color:rgba(224,224,224,0.4); font-size:0.78rem; margin-bottom:12px;">This row was scored before the persistence layer was added. The next crawler cycle will populate it, or you can trigger an immediate refresh.</div>';
      if (sym) {
        html += '<button onclick="fetch(\'/api/company-evaluator/evaluate/' + _esc(sym) + '\', {method:\'POST\'}).then(function(r){if(r.ok)alert(\'Backfill triggered for ' + _esc(sym) + '.\');else alert(\'Backfill failed.\');}).catch(function(){alert(\'Backfill failed.\');})" style="padding:6px 14px; border-radius:4px; font-size:0.78rem; font-weight:600; cursor:pointer; background:rgba(0,224,195,0.12); color:#00e0c3; border:1px solid rgba(0,224,195,0.3);">Backfill Now</button>';
      }
      html += '</div>';
    } else {
      // Render each section of raw financials
      var finSections = [
        ['income_statement', 'Income Statement'],
        ['balance_sheet', 'Balance Sheet'],
        ['cash_flow', 'Cash Flow'],
        ['finnhub_metrics', 'Finnhub Metrics'],
        ['analyst_recommendations', 'Analyst Recommendations'],
        ['insider_transactions', 'Insider Transactions'],
      ];

      for (var si = 0; si < finSections.length; si++) {
        var fKey = finSections[si][0];
        var fLabel = finSections[si][1];
        var fData = rawFin[fKey];
        if (!fData) continue;

        html += '<div style="margin-bottom:10px;">';
        html += '<div style="color:rgba(224,224,224,0.5); font-size:0.72rem; font-weight:600; text-transform:uppercase; margin-bottom:4px;">' + fLabel + '</div>';

        if (Array.isArray(fData)) {
          if (fData.length === 0) {
            html += '<div style="color:rgba(224,224,224,0.3); font-size:0.78rem;">No data</div>';
          } else if (typeof fData[0] === 'object') {
            html += _rawRenderObjectTable(fData.slice(0, 10));
          } else {
            html += '<div style="color:rgba(224,224,224,0.6); font-size:0.78rem;">' + _esc(JSON.stringify(fData.slice(0, 10))) + '</div>';
          }
        } else if (typeof fData === 'object') {
          // Could be { annual: [...], quarterly: [...] } or flat key-value
          if (fData.annual || fData.quarterly) {
            if (fData.annual && fData.annual.length) {
              html += '<div style="color:rgba(224,224,224,0.4); font-size:0.7rem; margin-bottom:2px;">Annual</div>';
              html += _rawRenderObjectTable(fData.annual.slice(0, 5));
            }
            if (fData.quarterly && fData.quarterly.length) {
              html += '<div style="color:rgba(224,224,224,0.4); font-size:0.7rem; margin:6px 0 2px;">Quarterly</div>';
              html += _rawRenderObjectTable(fData.quarterly.slice(0, 5));
            }
          } else {
            html += _rawRenderKVTable(fData);
          }
        }

        html += '</div>';
      }
    }

    html += '</div></div>';
    return html;
  }

  function _rawRenderObjectTable(arr) {
    if (!arr || !arr.length) return '<div style="color:rgba(224,224,224,0.3); font-size:0.78rem;">No data</div>';
    var keys = Object.keys(arr[0]);
    var html = '<div style="overflow-x:auto; max-height:300px; overflow-y:auto;">';
    html += '<table style="width:100%; border-collapse:collapse; font-size:0.72rem;">';
    html += '<thead><tr>';
    for (var h = 0; h < keys.length; h++) {
      html += '<th style="padding:3px 6px; text-align:left; color:rgba(224,224,224,0.3); font-weight:600; white-space:nowrap; border-bottom:1px solid rgba(255,255,255,0.06);">' + _esc(keys[h]) + '</th>';
    }
    html += '</tr></thead><tbody>';
    for (var r = 0; r < arr.length; r++) {
      html += '<tr style="border-bottom:1px solid rgba(255,255,255,0.03);">';
      for (var c = 0; c < keys.length; c++) {
        var val = arr[r][keys[c]];
        var display = val == null ? '\u2014' : (typeof val === 'number' ? val.toLocaleString() : _esc(String(val)));
        html += '<td style="padding:3px 6px; color:rgba(224,224,224,0.5); white-space:nowrap;">' + display + '</td>';
      }
      html += '</tr>';
    }
    html += '</tbody></table></div>';
    return html;
  }

  function _rawRenderKVTable(obj) {
    var keys = Object.keys(obj);
    if (!keys.length) return '<div style="color:rgba(224,224,224,0.3); font-size:0.78rem;">No data</div>';
    var html = '<div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:2px 20px;">';
    for (var i = 0; i < keys.length; i++) {
      var val = obj[keys[i]];
      var display = val == null ? '\u2014' : (typeof val === 'number' ? val.toLocaleString() : _esc(String(val)));
      html += '<div style="display:flex; justify-content:space-between; padding:2px 0; border-bottom:1px solid rgba(255,255,255,0.02);">';
      html += '<span style="color:rgba(224,224,224,0.4); font-size:0.72rem;">' + _esc(keys[i]) + '</span>';
      html += '<span style="color:rgba(224,224,224,0.6); font-size:0.72rem;">' + display + '</span>';
      html += '</div>';
    }
    html += '</div>';
    return html;
  }

  // ── Section 7: LLM Analysis (collapsible) ──

  function _rawRenderLLM(llm) {
    if (!llm) return '';
    var sectionId = 'ce-raw-llm-body';

    var html = '<div style="margin-bottom:16px;">';
    html += '<div data-raw-toggle="' + sectionId + '" style="display:flex; align-items:center; gap:8px; cursor:pointer; padding:8px 0; user-select:none;">';
    html += '<span class="ce-raw-chevron" style="color:rgba(224,224,224,0.4); font-size:0.7rem;">\u25B6</span>';
    html += '<span style="color:rgba(224,224,224,0.5); font-size:0.75rem; font-weight:600; text-transform:uppercase;">LLM Analysis</span>';
    html += '</div>';
    html += '<div id="' + sectionId + '" style="display:none;">';

    html += '<div style="background:#0d2030; border:1px solid #1a3a4a; border-radius:6px; padding:14px 18px;">';

    // Recommendation + Conviction
    var rec = llm.recommendation || llm.rating;
    var conviction = llm.conviction;
    html += '<div style="display:flex; align-items:center; gap:12px; margin-bottom:12px;">';
    if (rec) html += _recBadge(rec);
    if (conviction != null) {
      html += '<span style="font-size:0.8rem; color:rgba(224,224,224,0.5);">Conviction: <strong style="color:' + _rawScoreColor(conviction) + ';">' + conviction + '</strong></span>';
    }
    html += '</div>';

    // Summary
    if (llm.summary) {
      html += '<div style="color:rgba(224,224,224,0.7); font-size:0.82rem; margin-bottom:10px; line-height:1.5;">' + _esc(llm.summary) + '</div>';
    }

    // Thesis
    if (llm.thesis) {
      html += '<div style="margin-bottom:10px;">';
      html += '<div style="color:rgba(224,224,224,0.4); font-size:0.72rem; font-weight:600; text-transform:uppercase; margin-bottom:4px;">Thesis</div>';
      html += '<div style="color:rgba(224,224,224,0.6); font-size:0.8rem; line-height:1.5;">' + _esc(llm.thesis) + '</div>';
      html += '</div>';
    }

    // Risks + Catalysts
    var risks = llm.risks || [];
    var catalysts = llm.catalysts || [];
    if (risks.length || catalysts.length) {
      html += '<div style="display:flex; gap:12px; margin-top:8px;">';
      if (risks.length) {
        html += '<div style="flex:1; background:rgba(255,23,68,0.05); border:1px solid rgba(255,23,68,0.15); border-radius:6px; padding:10px;">';
        html += '<div style="color:#ff1744; font-size:0.7rem; font-weight:600; text-transform:uppercase; margin-bottom:6px;">Risks</div>';
        for (var ri = 0; ri < risks.length; ri++) {
          html += '<div style="color:rgba(224,224,224,0.7); font-size:0.78rem; margin-bottom:3px;">\u2022 ' + _esc(risks[ri]) + '</div>';
        }
        html += '</div>';
      }
      if (catalysts.length) {
        html += '<div style="flex:1; background:rgba(0,200,83,0.05); border:1px solid rgba(0,200,83,0.15); border-radius:6px; padding:10px;">';
        html += '<div style="color:#00c853; font-size:0.7rem; font-weight:600; text-transform:uppercase; margin-bottom:6px;">Catalysts</div>';
        for (var ci = 0; ci < catalysts.length; ci++) {
          html += '<div style="color:rgba(224,224,224,0.7); font-size:0.78rem; margin-bottom:3px;">\u2022 ' + _esc(catalysts[ci]) + '</div>';
        }
        html += '</div>';
      }
      html += '</div>';
    }

    html += '</div>';
    html += '</div></div>';
    return html;
  }

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
    document.removeEventListener('keydown', _rawOnKeydown);
    _rawClose();
    closeDetail();
  };
};
