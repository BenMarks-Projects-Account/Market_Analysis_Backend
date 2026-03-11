window.BenTradePages = window.BenTradePages || {};

/**
 * Breadth & Participation dashboard controller.
 *
 * Fetches from /api/breadth-participation and populates all dynamic elements.
 * Uses BenTradeDashboardCache for sessionStorage-backed caching.
 */
window.BenTradePages.initBreadthParticipation = function initBreadthParticipation(rootEl) {
  var doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  var scope = rootEl || doc;
  var API_URL = '/api/breadth-participation';
  var MODEL_URL = '/api/breadth-participation/model';
  var CACHE_KEY = 'breadthParticipation';
  var MODEL_CACHE_KEY = 'breadthModel';
  var _cache = window.BenTradeDashboardCache;

  // ── DOM refs ──────────────────────────────────────────────────
  var refreshBtn       = scope.querySelector('#breadthRefreshBtn');
  var lastUpdatedEl    = scope.querySelector('#breadthLastUpdated');
  // Hero
  var heroLabel        = scope.querySelector('#breadthHeroLabel');
  var heroScore        = scope.querySelector('#breadthHeroScore');
  var labelChip        = scope.querySelector('#breadthLabelChip');
  var summaryEl        = scope.querySelector('#breadthSummary');
  // Signal quality
  var signalQualityEl  = scope.querySelector('#breadthSignalQuality');
  var confidenceNote   = scope.querySelector('#breadthConfidenceNote');
  var confidenceChip   = scope.querySelector('#breadthConfidenceChip');
  // Drivers
  var positiveDrivers  = scope.querySelector('#breadthPositiveDrivers');
  var negativeDrivers  = scope.querySelector('#breadthNegativeDrivers');
  // A/D card
  var advancingEl      = scope.querySelector('#breadthAdvancing');
  var decliningEl      = scope.querySelector('#breadthDeclining');
  var adRatioEl        = scope.querySelector('#breadthADRatio');
  var pctUpEl          = scope.querySelector('#breadthPctUp');
  var partBar          = scope.querySelector('#breadthPartBar');
  var partScore        = scope.querySelector('#breadthPartScore');
  // MA breadth
  var ma200Bar         = scope.querySelector('#breadthMA200Bar');
  var ma200Val         = scope.querySelector('#breadthMA200Val');
  var ma50Bar          = scope.querySelector('#breadthMA50Bar');
  var ma50Val          = scope.querySelector('#breadthMA50Val');
  var ma20Bar          = scope.querySelector('#breadthMA20Bar');
  var ma20Val          = scope.querySelector('#breadthMA20Val');
  var trendBar         = scope.querySelector('#breadthTrendBar');
  var trendScore       = scope.querySelector('#breadthTrendScore');
  // New Highs/Lows
  var newHighsEl       = scope.querySelector('#breadthNewHighs');
  var newLowsEl        = scope.querySelector('#breadthNewLows');
  var hlRatioEl        = scope.querySelector('#breadthHLRatio');
  var hlHighSeg        = scope.querySelector('#breadthHLHighSeg');
  var hlLowSeg         = scope.querySelector('#breadthHLLowSeg');
  // Volume
  var upVolEl          = scope.querySelector('#breadthUpVol');
  var downVolEl        = scope.querySelector('#breadthDownVol');
  var volRatioEl       = scope.querySelector('#breadthVolRatio');
  var volBar           = scope.querySelector('#breadthVolBar');
  var volScoreEl       = scope.querySelector('#breadthVolScore');
  // EW vs CW
  var cwReturnEl       = scope.querySelector('#breadthCWReturn');
  var ewReturnEl       = scope.querySelector('#breadthEWReturn');
  var ewcwGapEl        = scope.querySelector('#breadthEWCWGap');
  var leaderBar        = scope.querySelector('#breadthLeaderBar');
  var leaderScoreEl    = scope.querySelector('#breadthLeaderScore');
  // Leadership quality metrics
  var pctOutperfEl     = scope.querySelector('#breadthPctOutperf');
  var medVsIdxEl       = scope.querySelector('#breadthMedVsIdx');
  var stabBar          = scope.querySelector('#breadthStabBar');
  var stabScoreEl      = scope.querySelector('#breadthStabScore');
  // Sector
  var sectorBarsEl     = scope.querySelector('#breadthSectorBars');
  var heatmapEl        = scope.querySelector('#breadthHeatmap');
  // Data quality
  var coverageEl       = scope.querySelector('#breadthCoverage');
  var confidenceEl     = scope.querySelector('#breadthConfidence');
  var dataQualityEl    = scope.querySelector('#breadthDataQuality');
  var histValidityEl   = scope.querySelector('#breadthHistValidity');
  var pitStatusEl      = scope.querySelector('#breadthPITStatus');
  var warningsEl       = scope.querySelector('#breadthWarnings');
  // Detail analysis
  var detailPositive   = scope.querySelector('#breadthDetailPositive');
  var detailNegative   = scope.querySelector('#breadthDetailNegative');
  var detailConflicts  = scope.querySelector('#breadthDetailConflicts');
  var takeawayEl       = scope.querySelector('#breadthTakeaway');
  // Pillar bars
  var pillarBarsEl     = scope.querySelector('#breadthPillarBars');
  // AI Model Analysis
  var modelLabel       = scope.querySelector('#breadthModelLabel');
  var modelScore       = scope.querySelector('#breadthModelScore');
  var modelSummary     = scope.querySelector('#breadthModelSummary');
  var modelCta         = scope.querySelector('#breadthModelCta');
  var runModelBtn      = scope.querySelector('#breadthRunModelBtn');
  var modelDetailsRow  = scope.querySelector('#breadthModelDetailsRow');
  var modelPillars     = scope.querySelector('#breadthModelPillars');
  var modelImplications = scope.querySelector('#breadthModelImplications');
  // Participation Trend chart container
  var trendChartWrap   = scope.querySelector('#breadthTrendChartWrap');

  var _destroyed = false;

  // ── Utilities ─────────────────────────────────────────────────

  function escapeHtml(val) {
    return String(val != null ? val : '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function fmtPct(val) {
    if (val == null) return '—';
    return (val * 100).toFixed(1) + '%';
  }

  function fmtReturnPct(val) {
    if (val == null) return '—';
    var pct = (val * 100).toFixed(2);
    return (val >= 0 ? '+' : '') + pct + '%';
  }

  function fmtVol(val) {
    if (val == null) return '—';
    if (val >= 1e9) return (val / 1e9).toFixed(1) + 'B';
    if (val >= 1e6) return (val / 1e6).toFixed(1) + 'M';
    return val.toLocaleString();
  }

  function fmtNum(val) {
    if (val == null) return '—';
    return Number(val).toLocaleString();
  }

  function scoreColor(score) {
    if (score == null) return '#888';
    if (score >= 70) return '#00e676';
    if (score >= 55) return 'var(--cyan, #00eaff)';
    if (score >= 40) return 'var(--warn, #ffab40)';
    return 'rgba(255,79,102,0.9)';
  }

  function setBar(barEl, valEl, score) {
    if (!barEl || !valEl) return;
    if (score == null) {
      barEl.style.width = '0%';
      valEl.textContent = '—';
      return;
    }
    var pct = Math.min(100, Math.max(0, Math.round(score)));
    barEl.style.width = pct + '%';
    barEl.style.background = scoreColor(score);
    valEl.textContent = pct;
  }

  function setText(el, val) {
    if (el) el.textContent = (val != null ? val : '—');
  }

  function renderList(el, items, dotClass) {
    if (!el) return;
    if (!items || items.length === 0) {
      el.innerHTML = '<li class="mod-contrib-item" style="opacity:0.5;">None</li>';
      return;
    }
    el.innerHTML = items.map(function(item) {
      return '<li class="mod-contrib-item"><span class="mod-contrib-dot ' +
        escapeHtml(dotClass) + '"></span>' + escapeHtml(item) + '</li>';
    }).join('');
  }

  function chipClass(label) {
    var l = (label || '').toLowerCase();
    if (l.indexOf('strong') >= 0 || l.indexOf('constructive') >= 0) return 'mod-chip-bullish';
    if (l.indexOf('weak') >= 0 || l.indexOf('deteriorat') >= 0) return 'mod-chip-bearish';
    return 'mod-chip-neutral';
  }

  function signalChipClass(quality) {
    if (quality === 'high') return 'mod-signal-high';
    if (quality === 'medium') return 'mod-signal-medium';
    return 'mod-signal-low';
  }

  // ── Heatmap builder ───────────────────────────────────────────

  var SEVERITY_COLORS = {
    critical: 'rgba(255,79,102,0.95)',
    high: 'rgba(255,152,0,0.9)',
    medium: 'rgba(244,200,95,0.85)',
    low: 'rgba(170,210,224,0.6)',
    info: 'rgba(170,210,224,0.35)'
  };

  var SEVERITY_ICONS = {
    critical: '🔴',
    high: '🟠',
    medium: '🟡',
    low: 'ℹ️',
    info: '📋'
  };

  function renderWarningGroup(title, warnings, level) {
    if (!warnings || warnings.length === 0) return '';
    var color = SEVERITY_COLORS[level] || SEVERITY_COLORS.medium;
    var icon = SEVERITY_ICONS[level] || '⚠';
    var html = '<div style="margin-top:8px;">';
    html += '<div style="font-size:9px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;' +
      'color:' + color + ';margin-bottom:4px;">' + escapeHtml(title) + '</div>';
    warnings.forEach(function(w) {
      html += '<div class="mod-warning-item" style="display:flex;align-items:flex-start;gap:4px;margin-bottom:3px;">' +
        '<span style="flex-shrink:0;font-size:9px;">' + icon + '</span>' +
        '<span style="font-size:10px;color:' + color + ';">' + escapeHtml(w.message || '') + '</span></div>';
    });
    html += '</div>';
    return html;
  }

  function renderDeferredGroup(items) {
    if (!items || items.length === 0) return '';
    var html = '<details class="mod-details" style="margin-top:8px;">';
    html += '<summary style="font-size:9px;color:rgba(170,210,224,0.5);">Deferred Enhancements (' + items.length + ')</summary>';
    html += '<div class="mod-details-body" style="padding-top:4px;">';
    items.forEach(function(w) {
      html += '<div style="font-size:9px;opacity:0.4;margin-bottom:2px;">📋 ' + escapeHtml(w.message || '') + '</div>';
    });
    html += '</div></details>';
    return html;
  }

  function heatClass(ret) {
    if (ret == null) return 'mod-heat-neutral';
    if (ret > 0.01) return 'mod-heat-green-3';
    if (ret > 0.005) return 'mod-heat-green-2';
    if (ret > 0.001) return 'mod-heat-green-1';
    if (ret >= -0.001) return 'mod-heat-neutral';
    if (ret >= -0.005) return 'mod-heat-red-1';
    if (ret >= -0.01) return 'mod-heat-red-2';
    return 'mod-heat-red-3';
  }

  var SECTOR_ABBR = {
    'Technology': 'Tech', 'Healthcare': 'HC', 'Financials': 'Fins',
    'Consumer Discretionary': 'Disc', 'Consumer Staples': 'Stpl',
    'Energy': 'Enrgy', 'Industrials': 'Ind', 'Materials': 'Matl',
    'Utilities': 'Utils', 'REITs': 'RE', 'Communication Services': 'Comm'
  };

  // ── Render ────────────────────────────────────────────────────

  function render(payload) {
    if (_destroyed) return;
    var eng = payload.engine_result || {};
    var raw = eng.raw_inputs || {};
    var pillars = eng.pillar_scores || {};
    var dq = payload.data_quality || {};
    var diag = eng.diagnostics || {};

    // Hero
    setText(heroScore, eng.score != null ? Math.round(eng.score) : '—');
    setText(heroLabel, (eng.label || '—').toUpperCase());
    if (heroLabel) heroLabel.style.color = scoreColor(eng.score);
    setText(summaryEl, eng.summary);
    if (labelChip) {
      labelChip.textContent = eng.short_label || '—';
      labelChip.className = 'mod-chip ' + chipClass(eng.label);
    }

    // Signal quality
    var sq = eng.signal_quality || 'low';
    setText(signalQualityEl, sq.toUpperCase());
    if (signalQualityEl) signalQualityEl.style.color = sq === 'high' ? '#00e676' : sq === 'medium' ? 'var(--cyan)' : 'var(--warn)';
    setText(confidenceNote, 'Confidence score: ' + (eng.confidence_score != null ? Math.round(eng.confidence_score) : '—') + '/100');
    if (confidenceChip) {
      confidenceChip.textContent = sq.toUpperCase() + ' CONFIDENCE';
      confidenceChip.className = 'mod-signal-chip ' + signalChipClass(sq);
    }

    // Drivers
    renderList(positiveDrivers, eng.positive_contributors, 'positive');
    renderList(negativeDrivers, eng.negative_contributors, 'negative');

    // A/D card
    var partRaw = raw.participation || {};
    setText(advancingEl, fmtNum(partRaw.advancing));
    setText(decliningEl, fmtNum(partRaw.declining));
    if (partRaw.advancing != null && partRaw.declining != null) {
      var adR = partRaw.advancing / Math.max(partRaw.declining, 1);
      setText(adRatioEl, adR.toFixed(2) + 'x');
    }
    if (partRaw.advancing != null && partRaw.total_valid) {
      setText(pctUpEl, fmtPct(partRaw.advancing / partRaw.total_valid));
    }
    setBar(partBar, partScore, pillars.participation_breadth);

    // MA breadth
    var trendRaw = raw.trend || {};
    setBar(ma200Bar, ma200Val, trendRaw.pct_above_200dma != null ? trendRaw.pct_above_200dma * 100 : null);
    if (ma200Val && trendRaw.pct_above_200dma != null) ma200Val.textContent = fmtPct(trendRaw.pct_above_200dma);
    setBar(ma50Bar, ma50Val, trendRaw.pct_above_50dma != null ? trendRaw.pct_above_50dma * 100 : null);
    if (ma50Val && trendRaw.pct_above_50dma != null) ma50Val.textContent = fmtPct(trendRaw.pct_above_50dma);
    setBar(ma20Bar, ma20Val, trendRaw.pct_above_20dma != null ? trendRaw.pct_above_20dma * 100 : null);
    if (ma20Val && trendRaw.pct_above_20dma != null) ma20Val.textContent = fmtPct(trendRaw.pct_above_20dma);
    setBar(trendBar, trendScore, pillars.trend_breadth);

    // New highs / lows
    setText(newHighsEl, fmtNum(partRaw.new_highs));
    setText(newLowsEl, fmtNum(partRaw.new_lows));
    if (partRaw.new_highs != null && partRaw.new_lows != null) {
      var hlTotal = partRaw.new_highs + partRaw.new_lows;
      setText(hlRatioEl, hlTotal > 0 ? (partRaw.new_highs / Math.max(partRaw.new_lows, 1)).toFixed(1) + 'x' : '—');
      if (hlHighSeg && hlLowSeg && hlTotal > 0) {
        hlHighSeg.style.width = Math.round(partRaw.new_highs / hlTotal * 100) + '%';
        hlLowSeg.style.width = Math.round(partRaw.new_lows / hlTotal * 100) + '%';
      }
    }

    // Volume
    var volRaw = raw.volume || {};
    setText(upVolEl, fmtVol(volRaw.up_volume));
    setText(downVolEl, fmtVol(volRaw.down_volume));
    if (volRaw.up_volume != null && volRaw.down_volume != null) {
      setText(volRatioEl, (volRaw.up_volume / Math.max(volRaw.down_volume, 1)).toFixed(2) + 'x');
    }
    setBar(volBar, volScoreEl, pillars.volume_breadth);

    // EW vs CW
    var leaderRaw = raw.leadership || {};
    setText(cwReturnEl, fmtReturnPct(leaderRaw.cw_return));
    setText(ewReturnEl, fmtReturnPct(leaderRaw.ew_return));
    if (leaderRaw.ew_return != null && leaderRaw.cw_return != null) {
      setText(ewcwGapEl, fmtReturnPct(leaderRaw.ew_return - leaderRaw.cw_return));
    }
    setBar(leaderBar, leaderScoreEl, pillars.leadership_quality);

    // Leadership quality metrics
    setText(pctOutperfEl, fmtPct(leaderRaw.pct_outperforming_index));
    if (leaderRaw.median_return != null && leaderRaw.index_return != null) {
      setText(medVsIdxEl, fmtReturnPct(leaderRaw.median_return - leaderRaw.index_return));
    }
    setBar(stabBar, stabScoreEl, pillars.participation_stability);

    // Sector bars
    var sectorReturns = leaderRaw.sector_returns || {};
    if (sectorBarsEl && Object.keys(sectorReturns).length > 0) {
      var sorted = Object.entries(sectorReturns)
        .sort(function(a, b) { return (b[1] || 0) - (a[1] || 0); });
      sectorBarsEl.innerHTML = sorted.map(function(pair) {
        var name = pair[0], ret = pair[1];
        var pct = ret != null ? Math.abs(ret * 100) : 0;
        var barW = Math.min(100, pct * 20); // scale: 5% -> 100%
        var color = ret >= 0 ? '#00e676' : 'rgba(255,79,102,0.7)';
        return '<div class="mod-bar-row"><span class="mod-bar-label">' +
          escapeHtml(SECTOR_ABBR[name] || name) +
          '</span><div class="mod-bar-track"><div class="mod-bar-fill" style="width:' +
          barW + '%;background:' + color + ';"></div></div><span class="mod-bar-val">' +
          fmtReturnPct(ret) + '</span></div>';
      }).join('');
    }

    // Heatmap
    if (heatmapEl && Object.keys(sectorReturns).length > 0) {
      heatmapEl.innerHTML = Object.entries(sectorReturns).map(function(pair) {
        var name = pair[0], ret = pair[1];
        return '<div class="mod-heatmap-cell ' + heatClass(ret) + '">' +
          escapeHtml(SECTOR_ABBR[name] || name) + '<br>' + fmtReturnPct(ret) + '</div>';
      }).join('');
    }

    // Data quality
    var universe = eng.universe || {};
    var diag = eng.diagnostics || {};
    var qualityScores = diag.quality_scores || {};
    var grouped = diag.grouped_warnings || {};
    setText(coverageEl, (universe.coverage_pct != null ? Math.round(universe.coverage_pct) + '%' : '—'));
    setText(confidenceEl, eng.confidence_score != null ? Math.round(eng.confidence_score) + '/100' : '—');
    setText(dataQualityEl, qualityScores.data_quality_score != null ? Math.round(qualityScores.data_quality_score) + '/100' : '—');
    setText(histValidityEl, qualityScores.historical_validity_score != null ? Math.round(qualityScores.historical_validity_score) + '/100' : '—');

    // Color code the quality scores
    if (dataQualityEl) dataQualityEl.style.color = scoreColor(qualityScores.data_quality_score);
    if (histValidityEl) histValidityEl.style.color = scoreColor(qualityScores.historical_validity_score);
    if (confidenceEl) confidenceEl.style.color = scoreColor(eng.confidence_score);

    // PIT status chip
    if (pitStatusEl) {
      var pitAvail = eng.point_in_time_constituents_available;
      if (pitAvail) {
        pitStatusEl.innerHTML = '<span class="mod-chip mod-chip-bullish" style="font-size:9px;padding:2px 6px;">PIT Constituents ✓</span>';
      } else {
        pitStatusEl.innerHTML = '<span class="mod-chip mod-chip-bearish" style="font-size:9px;padding:2px 6px;">No PIT Constituents</span>' +
          '<span style="font-size:10px;opacity:0.5;margin-left:6px;">Survivorship bias risk — historical validity may be reduced</span>';
      }
    }

    // Grouped warnings
    if (warningsEl) {
      var html = '';
      html += renderWarningGroup('Structural Risks', grouped.structural_risks, 'critical');
      html += renderWarningGroup('Completeness Issues', grouped.completeness_issues, 'high');
      html += renderWarningGroup('Signal Interpretation', grouped.signal_notes, 'medium');
      html += renderDeferredGroup(grouped.deferred_enhancements);
      warningsEl.innerHTML = html || '<div style="opacity:0.5;">No warnings</div>';
    }

    // Detail analysis
    renderList(detailPositive, eng.positive_contributors, 'positive');
    renderList(detailNegative, eng.negative_contributors, 'negative');
    renderList(detailConflicts, eng.conflicting_signals, 'conflict');
    setText(takeawayEl, eng.trader_takeaway);

    // Participation Trend sparkline
    renderTrendSparkline(trendRaw);

    // Pillar bars
    if (pillarBarsEl) {
      var pillarDefs = [
        { key: 'participation_breadth', label: 'Participation', weight: 25 },
        { key: 'trend_breadth', label: 'Trend', weight: 25 },
        { key: 'volume_breadth', label: 'Volume', weight: 20 },
        { key: 'leadership_quality', label: 'Leadership', weight: 20 },
        { key: 'participation_stability', label: 'Stability', weight: 10 },
      ];
      pillarBarsEl.innerHTML = pillarDefs.map(function(p) {
        var s = pillars[p.key];
        var pct = s != null ? Math.round(s) : 0;
        var color = scoreColor(s);
        return '<div class="mod-bar-row"><span class="mod-bar-label">' +
          escapeHtml(p.label) + ' (' + p.weight + '%)</span>' +
          '<div class="mod-bar-track"><div class="mod-bar-fill" style="width:' +
          pct + '%;background:' + color + ';"></div></div>' +
          '<span class="mod-bar-val">' + (s != null ? pct : '—') + '</span></div>';
      }).join('');
    }

    // Timestamp
    if (lastUpdatedEl) {
      var ts = payload.as_of || eng.as_of;
      lastUpdatedEl.textContent = ts ? 'Updated: ' + new Date(ts).toLocaleTimeString() : 'Updated: —';
    }
  }

  // ── Participation Trend Sparkline ─────────────────────────────

  /**
   * Renders an SVG sparkline from MA-breadth data (pct_above_200/50/20dma).
   * Input: raw.trend object with numeric 0–1 pct values.
   * Falls back to explicit "no data" message if values are missing.
   */
  function renderTrendSparkline(trendRaw) {
    if (!trendChartWrap) return;
    var series = [];
    var labels = [];
    var colors = { '200 DMA': '#00e676', '50 DMA': 'rgba(0,234,255,0.9)', '20 DMA': 'rgba(244,200,95,0.85)' };

    // Build single-point series from current MA breadth percentages
    if (trendRaw && trendRaw.pct_above_200dma != null) {
      series.push({ label: '200 DMA', value: trendRaw.pct_above_200dma * 100 });
    }
    if (trendRaw && trendRaw.pct_above_50dma != null) {
      series.push({ label: '50 DMA', value: trendRaw.pct_above_50dma * 100 });
    }
    if (trendRaw && trendRaw.pct_above_20dma != null) {
      series.push({ label: '20 DMA', value: trendRaw.pct_above_20dma * 100 });
    }

    if (series.length === 0) {
      console.warn('[BenTrade][Breadth] No trend data available for sparkline');
      trendChartWrap.innerHTML =
        '<div style="text-align:center;padding:20px 0;font-size:11px;opacity:0.5;">' +
        'No MA breadth data available for trend chart</div>';
      return;
    }

    console.log('[BenTrade][Breadth] Rendering trend sparkline with', series.length, 'MA series');

    // SVG horizontal bar chart showing % above each moving average
    var W = 400, H = 140;
    var barH = 22, gap = 14, padL = 62, padR = 50, padT = 18;
    var maxVal = 100;
    var barW = W - padL - padR;

    var svg = '<svg class="mod-trend-chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet">';

    // Title
    svg += '<text class="spark-label" x="' + (W / 2) + '" y="12" text-anchor="middle" style="font-size:9px;fill:rgba(0,234,255,0.5);">% ABOVE MOVING AVERAGE</text>';

    // Grid line at 50%
    var x50 = padL + (50 / maxVal) * barW;
    svg += '<line class="spark-axis" x1="' + x50 + '" y1="' + padT + '" x2="' + x50 + '" y2="' + (padT + series.length * (barH + gap) - gap) + '" stroke-dasharray="3,3"/>';
    svg += '<text class="spark-val" x="' + x50 + '" y="' + (padT + series.length * (barH + gap) + 6) + '" text-anchor="middle">50%</text>';

    series.forEach(function(s, i) {
      var y = padT + i * (barH + gap);
      var w = Math.max(2, (s.value / maxVal) * barW);
      var color = colors[s.label] || '#00eaff';

      // Label
      svg += '<text class="spark-label" x="' + (padL - 4) + '" y="' + (y + barH / 2 + 3) + '" text-anchor="end">' + escapeHtml(s.label) + '</text>';

      // Track
      svg += '<rect x="' + padL + '" y="' + y + '" width="' + barW + '" height="' + barH + '" rx="3" fill="rgba(0,234,255,0.03)" stroke="rgba(0,234,255,0.06)" stroke-width="0.5"/>';

      // Fill bar
      svg += '<rect x="' + padL + '" y="' + y + '" width="' + w + '" height="' + barH + '" rx="3" fill="' + color + '" opacity="0.35"/>';
      svg += '<rect x="' + padL + '" y="' + y + '" width="' + w + '" height="' + barH + '" rx="3" fill="url(#trendGrad' + i + ')" />';

      // Gradient
      svg += '<defs><linearGradient id="trendGrad' + i + '" x1="0" y1="0" x2="1" y2="0">' +
        '<stop offset="0%" stop-color="' + color + '" stop-opacity="0.5"/>' +
        '<stop offset="100%" stop-color="' + color + '" stop-opacity="0.15"/>' +
        '</linearGradient></defs>';

      // Value text
      svg += '<text class="spark-val" x="' + (padL + w + 5) + '" y="' + (y + barH / 2 + 3) + '" style="font-size:9px;fill:' + color + ';">' + s.value.toFixed(1) + '%</text>';
    });

    svg += '</svg>';
    trendChartWrap.innerHTML = svg;
  }

  // ── Model Analysis Rendering ────────────────────────────────

  function setModelBtnState(loading) {
    // Re-acquire button ref (may have been recreated by render functions)
    runModelBtn = scope.querySelector('#breadthRunModelBtn');
    if (!runModelBtn) return;
    console.log('[BenTrade][Breadth] Model button state →', loading ? 'loading' : 'idle');
    runModelBtn.disabled = loading;
    if (loading) {
      runModelBtn.classList.add('btn-refreshing');
      runModelBtn.innerHTML = '<span class="btn-spinner"></span>Analyzing…';
    } else {
      runModelBtn.classList.remove('btn-refreshing');
      runModelBtn.textContent = 'Run Model Analysis';
    }
  }

  function renderModelNotRun() {
    console.log('[BenTrade][Breadth] Rendering model-not-run state');
    if (modelSummary) {
      modelSummary.innerHTML =
        '<div class="mod-model-cta" id="breadthModelCta">' +
        '<p style="opacity:0.6;font-size:12px;margin:0 0 10px;">Model analysis has not been run yet.</p>' +
        '<button class="mod-action-btn" id="breadthRunModelBtn" type="button">Run Model Analysis</button>' +
        '</div>';
      var btn = modelSummary.querySelector('#breadthRunModelBtn');
      if (btn) btn.addEventListener('click', function() { triggerModelAnalysis(); });
    }
    setText(modelLabel, '—');
    setText(modelScore, '—');
    if (modelDetailsRow) modelDetailsRow.style.display = 'none';
  }

  function renderModelError(errMsg) {
    console.error('[BenTrade][Breadth] Model analysis error:', errMsg);
    if (modelSummary) {
      modelSummary.innerHTML =
        '<div style="color:rgba(255,79,102,0.9);font-size:12px;margin-bottom:8px;">' +
        escapeHtml(errMsg) + '</div>' +
        '<button class="mod-action-btn" id="breadthRunModelBtn" type="button">Retry Model Analysis</button>';
      var btn = modelSummary.querySelector('#breadthRunModelBtn');
      if (btn) btn.addEventListener('click', function() { triggerModelAnalysis(); });
    }
    setText(modelLabel, 'Error');
    if (modelLabel) modelLabel.style.color = 'rgba(255,79,102,0.9)';
    setText(modelScore, '—');
    if (modelDetailsRow) modelDetailsRow.style.display = 'none';
  }

  function renderModel(model) {
    if (!model) {
      renderModelNotRun();
      return;
    }
    console.log('[BenTrade][Breadth] Rendering model result:', model.label, model.score);

    // Label & score
    setText(modelLabel, (model.label || '—').toUpperCase());
    if (modelLabel) modelLabel.style.color = scoreColor(model.score);
    setText(modelScore, model.score != null ? Math.round(model.score) : '—');
    if (modelScore) modelScore.style.color = scoreColor(model.score);

    // Summary with confidence/drivers
    if (modelSummary) {
      var html = '<div style="font-size:12px;line-height:1.6;margin-bottom:10px;">' +
        escapeHtml(model.summary || '') + '</div>';

      // Confidence
      html += '<div style="font-size:11px;opacity:0.7;margin-bottom:8px;">Confidence: ' +
        (model.confidence != null ? (model.confidence * 100).toFixed(0) + '%' : '—') + '</div>';

      // Breadth drivers
      var bd = model.breadth_drivers || {};
      if (bd.constructive_factors && bd.constructive_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">CONSTRUCTIVE</span><ul class="mod-contrib-list">';
        bd.constructive_factors.forEach(function(f) {
          html += '<li class="mod-contrib-item"><span class="mod-contrib-dot positive"></span>' + escapeHtml(f) + '</li>';
        });
        html += '</ul></div>';
      }
      if (bd.warning_factors && bd.warning_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">WARNINGS</span><ul class="mod-contrib-list">';
        bd.warning_factors.forEach(function(f) {
          html += '<li class="mod-contrib-item"><span class="mod-contrib-dot negative"></span>' + escapeHtml(f) + '</li>';
        });
        html += '</ul></div>';
      }
      if (bd.conflicting_factors && bd.conflicting_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">CONFLICTING</span><ul class="mod-contrib-list">';
        bd.conflicting_factors.forEach(function(f) {
          html += '<li class="mod-contrib-item"><span class="mod-contrib-dot conflict"></span>' + escapeHtml(f) + '</li>';
        });
        html += '</ul></div>';
      }

      // Trader takeaway
      if (model.trader_takeaway) {
        html += '<div class="mod-divider"></div>' +
          '<div style="font-size:11px;font-weight:600;margin-bottom:4px;opacity:0.6;">TRADER TAKEAWAY</div>' +
          '<div style="font-size:12px;line-height:1.5;">' + escapeHtml(model.trader_takeaway) + '</div>';
      }

      // Uncertainty flags
      var uf = model.uncertainty_flags || [];
      if (uf.length > 0) {
        html += '<div style="margin-top:8px;">';
        uf.forEach(function(f) {
          html += '<div style="font-size:10px;opacity:0.5;">⚠ ' + escapeHtml(f) + '</div>';
        });
        html += '</div>';
      }

      // Re-run button
      html += '<div style="margin-top:12px;">' +
        '<button class="mod-action-btn" id="breadthRunModelBtn" type="button">Re-run Model Analysis</button></div>';

      modelSummary.innerHTML = html;
      var btn = modelSummary.querySelector('#breadthRunModelBtn');
      if (btn) btn.addEventListener('click', function() { triggerModelAnalysis(); });
    }

    // Model detail row — pillar interpretations
    if (modelDetailsRow) modelDetailsRow.style.display = '';
    if (modelPillars) {
      var pa = model.pillar_analysis || {};
      var pillarKeys = ['participation', 'trend', 'volume', 'leadership', 'stability'];
      var pillarsHtml = '';
      pillarKeys.forEach(function(k) {
        var val = pa[k];
        if (val) {
          pillarsHtml += '<div style="margin-bottom:8px;">' +
            '<div style="font-size:10px;font-weight:600;opacity:0.6;text-transform:uppercase;">' +
            escapeHtml(k) + '</div>' +
            '<div style="font-size:11px;line-height:1.5;">' + escapeHtml(val) + '</div></div>';
        }
      });
      modelPillars.innerHTML = pillarsHtml || '<div style="opacity:0.5;font-size:11px;">No pillar analysis available</div>';
    }

    // Model detail row — market implications
    if (modelImplications) {
      var mi = model.market_implications || {};
      var implKeys = ['directional_bias', 'position_sizing', 'strategy_recommendation', 'risk_level', 'sector_tilt'];
      var implLabels = {
        directional_bias: 'Directional Bias', position_sizing: 'Position Sizing',
        strategy_recommendation: 'Strategy Recommendation', risk_level: 'Risk Level',
        sector_tilt: 'Sector Tilt'
      };
      var implHtml = '';
      implKeys.forEach(function(k) {
        var val = mi[k];
        if (val) {
          implHtml += '<div style="margin-bottom:6px;">' +
            '<span style="font-size:10px;opacity:0.6;">' + escapeHtml(implLabels[k] || k) + ':</span> ' +
            '<span style="font-size:11px;">' + escapeHtml(val) + '</span></div>';
        }
      });
      modelImplications.innerHTML = implHtml || '<div style="opacity:0.5;font-size:11px;">No implications available</div>';
    }
  }

  function triggerModelAnalysis() {
    if (_destroyed) return;
    console.log('[BenTrade][Breadth] Triggering model analysis…');
    // Show loading overlay in model summary while LLM runs
    if (modelSummary) {
      var existingSummary = modelSummary.innerHTML;
      var overlay = document.createElement('div');
      overlay.id = 'breadthModelLoadingOverlay';
      overlay.style.cssText = 'text-align:center;padding:18px 0;';
      overlay.innerHTML =
        '<div style="display:inline-block;width:22px;height:22px;border-radius:50%;' +
        'border:2px solid rgba(0,234,255,0.15);border-top-color:rgba(0,234,255,0.9);' +
        'animation:btnInlineSpin 0.8s linear infinite;margin-bottom:8px;"></div>' +
        '<div style="font-size:11px;opacity:0.7;">Running model analysis… Interpreting breadth inputs.</div>';
      modelSummary.innerHTML = '';
      modelSummary.appendChild(overlay);
    }
    setModelBtnState(true);
    var CLIENT_TIMEOUT = (window.BenTradeApi && window.BenTradeApi.MODEL_TIMEOUT_MS) || 185000;
    var t0 = performance.now();
    console.log('[BREADTH_MODEL] request_start', {
      endpoint: MODEL_URL, method: 'POST',
      timeout_ms: CLIENT_TIMEOUT,
      timestamp: new Date().toISOString(),
    });
    var controller = new AbortController();
    var timerFired = false;
    var timer = setTimeout(function() {
      timerFired = true;
      console.warn('[BREADTH_MODEL] abort_timer_fired', {
        elapsed_ms: Math.round(performance.now() - t0),
        timeout_ms: CLIENT_TIMEOUT,
      });
      controller.abort();
    }, CLIENT_TIMEOUT);
    fetch(MODEL_URL, { method: 'POST', signal: controller.signal })
      .then(function(resp) {
        console.log('[BREADTH_MODEL] response_headers', {
          status: resp.status, ok: resp.ok,
          elapsed_ms: Math.round(performance.now() - t0),
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .then(function(result) {
        if (_destroyed) return;
        console.log('[BREADTH_MODEL] body_parsed', {
          hasModel: !!result.model_analysis,
          elapsed_ms: Math.round(performance.now() - t0),
        });
        var modelData = result.model_analysis || null;
        var errorInfo = result.error || null;
        if (_cache && modelData) _cache.set(MODEL_CACHE_KEY, modelData);
        renderModel(modelData);
        if (!modelData) {
          var errMsg = (errorInfo && errorInfo.message)
            ? errorInfo.message
            : 'Model returned no result — is the local LLM running?';
          var errKind = (errorInfo && errorInfo.kind) ? ' (' + errorInfo.kind + ')' : '';
          renderModelError(errMsg + errKind);
        }
      })
      .catch(function(err) {
        if (_destroyed) return;
        var elapsed = Math.round(performance.now() - t0);
        console.error('[BREADTH_MODEL] failure', { error: err.message, name: err.name, elapsed_ms: elapsed, timerFired: timerFired });
        var msg;
        if (err.name === 'AbortError') {
          var timeoutSec = Math.round(CLIENT_TIMEOUT / 1000);
          msg = 'Model request timed out after ' + timeoutSec + 's. Is the local LLM running?';
        } else {
          msg = String(err.message || 'Model analysis failed');
        }
        renderModelError(msg);
      })
      .finally(function() {
        clearTimeout(timer);
        setModelBtnState(false);
        console.log('[BREADTH_MODEL] lifecycle_complete', { total_ms: Math.round(performance.now() - t0) });
      });
  }

  // ── Error state rendering ─────────────────────────────────

  function renderErrorState(errMsg) {
    console.error('[BenTrade][Breadth] Engine error:', errMsg);
    setText(heroLabel, 'ERROR');
    if (heroLabel) heroLabel.style.color = 'rgba(255,79,102,0.9)';
    setText(heroScore, '—');
    setText(summaryEl, errMsg);
    if (labelChip) {
      labelChip.textContent = 'Error';
      labelChip.className = 'mod-chip mod-chip-bearish';
    }
    setText(signalQualityEl, '—');
    setText(confidenceNote, 'Engine data unavailable');
    if (lastUpdatedEl) lastUpdatedEl.textContent = 'Error: ' + errMsg;
  }

  // ── Fetch ─────────────────────────────────────────────────────

  function setRefreshBtnState(refreshing) {
    if (!refreshBtn) return;
    if (refreshing) {
      refreshBtn.classList.add('btn-refreshing');
      refreshBtn.innerHTML = '<span class="btn-spinner"></span>Refreshing\u2026';
      refreshBtn.disabled = true;
    } else {
      refreshBtn.classList.remove('btn-refreshing');
      refreshBtn.innerHTML = 'Refresh';
      refreshBtn.disabled = false;
    }
  }

  function renderRefreshOverlay(show) {
    var existing = scope.querySelector('#breadthRefreshOverlay');
    if (!show) {
      if (existing) existing.remove();
      return;
    }
    if (existing) return; // already showing
    var overlay = document.createElement('div');
    overlay.id = 'breadthRefreshOverlay';
    overlay.style.cssText =
      'position:fixed;top:64px;right:24px;z-index:100;padding:6px 14px;' +
      'background:rgba(0,30,40,0.85);border:1px solid rgba(0,234,255,0.15);' +
      'border-radius:6px;font-size:11px;color:rgba(0,234,255,0.7);' +
      'display:flex;align-items:center;gap:6px;pointer-events:none;';
    overlay.innerHTML =
      '<span style="display:inline-block;width:12px;height:12px;border-radius:50%;' +
      'border:2px solid rgba(0,234,255,0.15);border-top-color:rgba(0,234,255,0.8);' +
      'animation:btnInlineSpin 0.8s linear infinite;"></span>Refreshing…';
    (rootEl || document.body).appendChild(overlay);
  }

  function renderRefreshError(errMsg) {
    var existing = scope.querySelector('#breadthRefreshError');
    if (existing) existing.remove();
    if (!errMsg) return;
    var banner = document.createElement('div');
    banner.id = 'breadthRefreshError';
    banner.style.cssText =
      'margin:8px 0;padding:8px 12px;background:rgba(255,79,102,0.08);' +
      'border:1px solid rgba(255,79,102,0.25);border-radius:6px;font-size:11px;' +
      'color:rgba(255,79,102,0.9);display:flex;align-items:center;gap:8px;';
    banner.innerHTML =
      '<span>⚠ Refresh failed: ' + escapeHtml(errMsg) + ' — showing cached data</span>' +
      '<button type="button" style="margin-left:auto;background:none;border:none;' +
      'color:rgba(255,79,102,0.7);cursor:pointer;font-size:14px;" ' +
      'onclick="this.parentElement.remove()">✕</button>';
    var hero = scope.querySelector('.mod-hero-card');
    if (hero && hero.parentElement) {
      hero.parentElement.insertBefore(banner, hero.nextSibling);
    }
  }

  function renderCacheSourceBadge(source) {
    if (lastUpdatedEl && source === 'cache') {
      var ts = _cache ? _cache.getLastUpdated(CACHE_KEY) : null;
      lastUpdatedEl.textContent = ts
        ? 'Cached: ' + new Date(ts).toLocaleTimeString()
        : 'Showing cached data';
    }
  }

  /** Restore model analysis panel from cache or show not-run state. */
  function restoreModelState() {
    var modelData = _cache ? _cache.getData(MODEL_CACHE_KEY) : null;
    if (modelData) {
      console.log('[BenTrade][Breadth] cache_rehydrate model_data=present');
      renderModel(modelData);
    } else {
      renderModelNotRun();
    }
  }

  // ── Fetch ─────────────────────────────────────────────────────

  var REQUIRED_FIELDS = ['engine_result', 'engine_result.score'];

  function fetchData(force) {
    if (_destroyed) return;

    var hasCached = _cache && _cache.hasCache(CACHE_KEY);
    console.log('[BenTrade][Breadth] fetchData force=%s hasCached=%s status=%s',
      force, hasCached, _cache ? _cache.getStatus(CACHE_KEY) : 'no-cache');

    // ── Route re-entry with existing cache: render immediately, skip fetch ──
    if (!force && hasCached) {
      var cachedData = _cache.getData(CACHE_KEY);
      console.log('[BenTrade][Breadth] cache_rehydrate route_entry score=%s',
        cachedData && cachedData.engine_result ? cachedData.engine_result.score : '?');
      render(cachedData);
      restoreModelState();
      renderCacheSourceBadge('cache');
      return;
    }

    // ── Refresh with cached data visible ──
    if (force && hasCached) {
      renderRefreshOverlay(true);
      renderRefreshError(null); // clear prior error
      console.log('[BenTrade][Breadth] refresh_start preserving_cache=true');
    }

    // ── No cache, first load ──
    if (!hasCached) {
      console.log('[BenTrade][Breadth] first_load_start');
    }

    if (force) setRefreshBtnState(true);

    var url = API_URL + (force ? '?force=true' : '');
    if (_cache) _cache.setRefreshing(CACHE_KEY, true);

    fetch(url)
      .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function(data) {
        if (_destroyed) return;
        // Validate before cache write
        if (_cache) {
          var wrote = _cache.setSafe(CACHE_KEY, data, REQUIRED_FIELDS);
          if (!wrote) {
            console.warn('[BenTrade][Breadth] payload_validation_failed — preserving prior cache');
            if (hasCached) {
              renderRefreshError('Invalid response from server');
            } else {
              renderErrorState('Invalid response from server (missing required fields)');
            }
            return;
          }
        }
        console.log('[BenTrade][Breadth] %s success score=%s',
          force ? 'refresh' : 'first_load',
          data.engine_result ? data.engine_result.score : '?');
        render(data);
        restoreModelState();
        renderRefreshError(null);
      })
      .catch(function(err) {
        if (_destroyed) return;
        var msg = err.message || 'Failed to load breadth data';
        console.error('[BenTrade][Breadth] %s failure: %s',
          force ? 'refresh' : 'first_load', msg);

        if (hasCached) {
          // Keep cached data visible, show error banner
          console.log('[BenTrade][Breadth] refresh_failed preserving_cache=true');
          renderRefreshError(msg);
          if (_cache) _cache.setError(CACHE_KEY, msg);
        } else {
          // No cache — show full error state with retry
          renderErrorState(msg);
          renderModelNotRun();
        }
      })
      .finally(function() {
        if (_cache) _cache.setRefreshing(CACHE_KEY, false);
        setRefreshBtnState(false);
        renderRefreshOverlay(false);
      });
  }

  // ── Init ──────────────────────────────────────────────────────
  if (refreshBtn) {
    refreshBtn.addEventListener('click', function() { fetchData(true); });
  }
  if (runModelBtn) {
    runModelBtn.addEventListener('click', function() { triggerModelAnalysis(); });
  }

  fetchData(false);

  // ── Cleanup (returned to router) ─────────────────────────────
  return function cleanupBreadthParticipation() {
    _destroyed = true;
    renderRefreshOverlay(false);
    console.log('[BenTrade][Breadth] cleanup — DOM detached, cache preserved');
  };
};
