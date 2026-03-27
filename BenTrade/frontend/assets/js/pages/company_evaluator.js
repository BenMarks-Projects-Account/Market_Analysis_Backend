/* ── Company Evaluator Page ── */
window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initCompanyEvaluator = function initCompanyEvaluator(rootEl) {
  var doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  var scope = rootEl || doc;
  var _statusTimer = null;
  var _ceCompanies = [];

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

  function _recBadge(rec) {
    if (!rec) return '<span style="color:rgba(224,224,224,0.2);">--</span>';
    var colors = {
      'STRONG_BUY': '#00c853', 'BUY': '#00c853',
      'HOLD': '#ffd600',
      'SELL': '#ff9800', 'STRONG_SELL': '#ff1744'
    };
    var color = colors[rec] || 'rgba(224,224,224,0.5)';
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

  function _pillarCell(score) {
    if (score == null) return '<td style="' + _tdStyle + 'text-align:center; color:rgba(224,224,224,0.2);">--</td>';
    var color = _scoreColor(score);
    return '<td style="' + _tdStyle + 'text-align:center; color:' + color + '; font-size:0.85rem;">' + score.toFixed(0) + '</td>';
  }

  // ── Ranked list ──
  function loadRankedList(sector) {
    var url = '/api/company-evaluator/ranked?limit=100';
    if (sector) url += '&sector=' + encodeURIComponent(sector);

    fetch(url)
      .then(function(res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function(data) {
        _ceCompanies = data.companies || [];
        renderTable(_ceCompanies);
        populateSectorFilter(_ceCompanies);
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
        'No companies evaluated yet.<br>' +
        '<small>Click "Run Crawler" to evaluate the universe, or the service needs to complete its first run.</small></div>';
      return;
    }

    var html = '<table style="width:100%; border-collapse:collapse; font-size:0.85rem;">';

    // Header
    html += '<thead><tr style="border-bottom:1px solid rgba(255,255,255,0.1);">';
    html += '<th style="' + _thStyle + 'width:40px;">#</th>';
    html += '<th style="' + _thStyle + '">Symbol</th>';
    html += '<th style="' + _thStyle + '">Company</th>';
    html += '<th style="' + _thStyle + '">Sector</th>';
    html += '<th style="' + _thStyle + 'text-align:center;">Score</th>';
    html += '<th style="' + _thStyle + 'text-align:center;">Biz Quality</th>';
    html += '<th style="' + _thStyle + 'text-align:center;">Ops Health</th>';
    html += '<th style="' + _thStyle + 'text-align:center;">Cap Alloc</th>';
    html += '<th style="' + _thStyle + 'text-align:center;">Growth</th>';
    html += '<th style="' + _thStyle + 'text-align:center;">Valuation</th>';
    html += '<th style="' + _thStyle + 'text-align:center;">LLM</th>';
    html += '<th style="' + _thStyle + '">Updated</th>';
    html += '</tr></thead><tbody>';

    for (var i = 0; i < companies.length; i++) {
      var c = companies[i];
      var scoreColor = _scoreColor(c.composite_score);
      var recBadge = _recBadge(c.llm_recommendation);
      var ps = c.pillar_scores || {};
      var updated = _timeAgo(c.evaluated_at);
      var sym = _esc(c.symbol || '');

      html += '<tr class="ce-row" data-symbol="' + sym + '" style="border-bottom:1px solid rgba(255,255,255,0.05); cursor:pointer;">';
      html += '<td style="' + _tdStyle + 'color:rgba(224,224,224,0.4);">' + _esc(String(c.rank || '--')) + '</td>';
      html += '<td style="' + _tdStyle + 'font-weight:700; color:#e0e0e0;">' + sym + '</td>';
      html += '<td style="' + _tdStyle + 'color:rgba(224,224,224,0.7); max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">' + _esc(c.company_name || '') + '</td>';
      html += '<td style="' + _tdStyle + 'color:rgba(224,224,224,0.5); font-size:0.78rem;">' + _esc(c.sector || '--') + '</td>';
      html += '<td style="' + _tdStyle + 'text-align:center;"><span style="color:' + scoreColor + '; font-weight:700; font-size:1rem;">' + (c.composite_score != null ? c.composite_score.toFixed(1) : '--') + '</span></td>';
      html += _pillarCell(ps.business_quality);
      html += _pillarCell(ps.operational_health);
      html += _pillarCell(ps.capital_allocation);
      html += _pillarCell(ps.growth_quality);
      html += _pillarCell(ps.valuation);
      html += '<td style="' + _tdStyle + 'text-align:center;">' + recBadge + '</td>';
      html += '<td style="' + _tdStyle + 'color:rgba(224,224,224,0.3); font-size:0.75rem;">' + _esc(updated) + '</td>';
      html += '</tr>';
    }

    html += '</tbody></table>';
    container.innerHTML = html;

    // Bind row clicks
    var rows = container.querySelectorAll('.ce-row');
    for (var r = 0; r < rows.length; r++) {
      rows[r].addEventListener('click', _onRowClick);
      rows[r].addEventListener('mouseover', function() { this.style.background = 'rgba(255,255,255,0.04)'; });
      rows[r].addEventListener('mouseout', function() { this.style.background = 'transparent'; });
    }
  }

  function _onRowClick() {
    var sym = this.getAttribute('data-symbol');
    if (sym) openDetail(sym);
  }

  // ── Sector filter ──
  function populateSectorFilter(companies) {
    var seen = {};
    var sectors = [];
    for (var i = 0; i < companies.length; i++) {
      var s = companies[i].sector;
      if (s && !seen[s]) {
        seen[s] = true;
        sectors.push(s);
      }
    }
    sectors.sort();

    var select = scope.querySelector('#ce-sector-filter');
    if (!select) return;

    select.innerHTML = '<option value="">All Sectors (' + companies.length + ')</option>';
    for (var j = 0; j < sectors.length; j++) {
      var count = 0;
      for (var k = 0; k < companies.length; k++) {
        if (companies[k].sector === sectors[j]) count++;
      }
      select.innerHTML += '<option value="' + _esc(sectors[j]) + '">' + _esc(sectors[j]) + ' (' + count + ')</option>';
    }
  }

  function filterBySector(sector) {
    if (sector) {
      var filtered = [];
      for (var i = 0; i < _ceCompanies.length; i++) {
        if (_ceCompanies[i].sector === sector) filtered.push(_ceCompanies[i]);
      }
      renderTable(filtered);
    } else {
      renderTable(_ceCompanies);
    }
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
    var sectorEl = scope.querySelector('#ce-sector-filter');
    loadRankedList(sectorEl ? sectorEl.value : '');
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
  var sectorFilter = scope.querySelector('#ce-sector-filter');
  if (sectorFilter) sectorFilter.addEventListener('change', function() { filterBySector(this.value); });

  var refreshBtn = scope.querySelector('#ce-refresh-btn');
  if (refreshBtn) refreshBtn.addEventListener('click', refresh);

  var crawlBtn = scope.querySelector('#ce-crawl-btn');
  if (crawlBtn) crawlBtn.addEventListener('click', triggerCrawl);

  document.addEventListener('keydown', _onKeydown);

  // ── Init ──
  loadRankedList();
  loadStatus();
  _statusTimer = setInterval(loadStatus, 15000);

  // ── Cleanup (returned to router) ──
  return function cleanup() {
    if (_statusTimer) { clearInterval(_statusTimer); _statusTimer = null; }
    document.removeEventListener('keydown', _onKeydown);
    closeDetail();
  };
};
