/**
 * Pre-Market Intelligence Display — standalone module.
 *
 * Used by the home dashboard.  Has ZERO dependencies on
 * trade_management_center.js or any other page module.
 *
 * Exports:  window.BenTradePreMarket = { init, refresh, destroy }
 */
(function(global){
  'use strict';

  /* ── state ── */
  var _indexTimer  = null;
  var _slowTimer   = null;
  var _intelTimer  = null;

  /* ── cached DOM refs (set in init) ── */
  var _intelEl     = null;
  var _chartsEl    = null;
  var _emptyEl     = null;
  var _indexSvg    = null;
  var _vixSvg      = null;
  var _macroSvg    = null;
  var _gapSvg      = null;
  var _refreshBtn  = null;

  /* ── HTML-escape helper ── */
  function _esc(text){
    if(text == null) return '';
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* ══════════════════════════════════════════════════════════════════
   *  PUBLIC API
   * ══════════════════════════════════════════════════════════════════ */

  /**
   * Initialise the pre-market panels on a page.
   *
   * @param {Object}  cfg
   * @param {Element} cfg.scope          Root element to querySelector within (default: document)
   * @param {string}  cfg.intelId        DOM ID for briefing panel  (default 'home-pre-market-intel')
   * @param {string}  cfg.chartsId       DOM ID for charts grid     (default 'homePreMarketCharts')
   * @param {string}  cfg.emptyId        DOM ID for empty state     (default 'homePreMarketEmpty')
   * @param {string}  cfg.indexSvgId     DOM ID for index futures   (default 'homePreMarketIndexFutures')
   * @param {string}  cfg.vixSvgId       DOM ID for VIX chart       (default 'homePreMarketVix')
   * @param {string}  cfg.macroSvgId     DOM ID for macro chart     (default 'homePreMarketMacro')
   * @param {string}  cfg.gapSvgId       DOM ID for gap chart       (default 'homePreMarketGap')
   * @param {string}  cfg.refreshBtnId   DOM ID for refresh button  (default 'homePreMarketRefreshBtn')
   */
  function init(cfg){
    cfg = cfg || {};
    var scope = cfg.scope || document;

    _intelEl    = scope.querySelector('#' + (cfg.intelId      || 'home-pre-market-intel'));
    _chartsEl   = scope.querySelector('#' + (cfg.chartsId     || 'homePreMarketCharts'));
    _emptyEl    = scope.querySelector('#' + (cfg.emptyId      || 'homePreMarketEmpty'));
    _indexSvg   = scope.querySelector('#' + (cfg.indexSvgId   || 'homePreMarketIndexFutures'));
    _vixSvg     = scope.querySelector('#' + (cfg.vixSvgId     || 'homePreMarketVix'));
    _macroSvg   = scope.querySelector('#' + (cfg.macroSvgId   || 'homePreMarketMacro'));
    _gapSvg     = scope.querySelector('#' + (cfg.gapSvgId     || 'homePreMarketGap'));
    _refreshBtn = scope.querySelector('#' + (cfg.refreshBtnId || 'homePreMarketRefreshBtn'));

    // Diagnostic logging — surface exactly what was found/missing
    console.log('[PreMarket] init — intelEl:', !!_intelEl,
      'chartsEl:', !!_chartsEl, 'indexSvg:', !!_indexSvg,
      'vixSvg:', !!_vixSvg, 'macroSvg:', !!_macroSvg,
      'gapSvg:', !!_gapSvg, 'scope:', scope.tagName || 'document');

    if(!_intelEl && !_chartsEl){
      console.warn('[PreMarket] init — no containers found in scope; aborting');
      return;
    }

    // Load once immediately
    _loadBriefing();
    _loadAndRenderCharts();

    // Set up auto-refresh timers
    _startTimers();

    // Manual refresh button
    if(_refreshBtn){
      _refreshBtn.addEventListener('click', _onRefreshClick);
    }
  }

  /** Force-refresh both briefing + charts. */
  function refresh(){
    _loadBriefing();
    _loadAndRenderCharts();
  }

  /** Tear down timers (call when navigating away). */
  function destroy(){
    _stopTimers();
    if(_refreshBtn){
      _refreshBtn.removeEventListener('click', _onRefreshClick);
    }
    _intelEl = _chartsEl = _emptyEl = null;
    _indexSvg = _vixSvg = _macroSvg = _gapSvg = null;
    _refreshBtn = null;
  }

  /* ══════════════════════════════════════════════════════════════════
   *  TIMERS
   * ══════════════════════════════════════════════════════════════════ */

  function _startTimers(){
    _stopTimers();
    // Briefing + index futures: every 60 s
    _intelTimer = setInterval(_loadBriefing, 60000);
    _indexTimer = setInterval(function(){
      if(!_indexSvg) return;
      Promise.all(['es','nq','rty','ym'].map(function(sym){ return _fetchBars(sym, '5m', 2); }))
        .then(function(indexResults){
          var labels = ['ES','NQ','RTY','YM'];
          var colors = [_CT.index.es, _CT.index.nq, _CT.index.rty, _CT.index.ym];
          var lines = indexResults.map(function(res, idx){ return { label: labels[idx], color: colors[idx], data: _normalizePct(res.bars, res.prior_close) }; }).filter(function(l){ return l.data.length > 0; });
          _renderLineChart(_indexSvg, lines, { yLabel: '% Change', showMarketOpen: true });
        }).catch(function(){});
    }, 60000);
    // Slow charts (VIX, Macro, Gap): every 5 min
    _slowTimer = setInterval(function(){ _loadAndRenderCharts(); }, 300000);
  }

  function _stopTimers(){
    if(_indexTimer){ clearInterval(_indexTimer); _indexTimer = null; }
    if(_slowTimer){ clearInterval(_slowTimer); _slowTimer = null; }
    if(_intelTimer){ clearInterval(_intelTimer); _intelTimer = null; }
  }

  function _onRefreshClick(){
    if(!_refreshBtn) return;
    _refreshBtn.disabled = true;
    _refreshBtn.textContent = 'Refreshing\u2026';
    _loadAndRenderCharts().finally(function(){
      if(_refreshBtn){
        _refreshBtn.disabled = false;
        _refreshBtn.textContent = '\u21bb Refresh';
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════
   *  BRIEFING — intelligence panel
   * ══════════════════════════════════════════════════════════════════ */

  function _loadBriefing(){
    if(!_intelEl){
      console.warn('[PreMarket] _loadBriefing skipped — _intelEl is null');
      return;
    }
    console.log('[PreMarket] fetching /api/pre-market/briefing ...');
    fetch('/api/pre-market/briefing')
      .then(function(res){
        console.log('[PreMarket] briefing response:', res.status, res.statusText);
        return res.ok ? res.json() : null;
      })
      .then(function(briefing){
        if(briefing){
          console.log('[PreMarket] briefing received — signal:', briefing.overnight_signal?.signal, 'snapshots:', Object.keys(briefing.snapshots || {}).length);
          _renderBriefing(briefing);
        } else {
          console.warn('[PreMarket] briefing returned null/empty — panel stays hidden');
        }
      })
      .catch(function(e){
        console.warn('[PreMarket] briefing fetch failed:', e && e.message || e);
      });
  }

  function _renderBriefing(briefing){
    if(!_intelEl) return;

    var signal    = briefing.overnight_signal || {};
    var snapshots = briefing.snapshots || {};
    var cross     = briefing.cross_asset || {};
    var vix       = briefing.vix_term_structure || {};
    var alerts    = briefing.position_alerts || [];

    var signalColor = signal.signal === 'BULLISH' ? '#00c853'
                    : signal.signal === 'BEARISH' ? '#ff1744'
                    : '#ffd600';
    var signalIcon  = signal.signal === 'BULLISH' ? '\u25B2'
                    : signal.signal === 'BEARISH' ? '\u25BC'
                    : '\u25C6';

    var html = '<div class="tmc-pre-market-panel" style="border-left:4px solid ' + signalColor + ';">';

    // header
    html += '<div class="tmc-pm-header">'
          + '<span class="tmc-pm-title">PRE-MARKET INTELLIGENCE</span>'
          + '<span class="tmc-pm-time">'
          +   (briefing.timestamp ? new Date(briefing.timestamp).toLocaleTimeString() : '')
          + '</span>'
          + '</div>';

    // signal row
    html += '<div class="tmc-pm-signal-row">'
          + '<span class="tmc-pm-signal-icon" style="color:' + signalColor + ';">' + _esc(signalIcon) + '</span>'
          + '<strong style="color:' + signalColor + ';font-size:1.1rem;">' + _esc(signal.signal || 'NEUTRAL') + '</strong>'
          + '<span class="tmc-pm-conviction">' + _esc(signal.conviction || '') + ' conviction</span>'
          + '</div>';

    // futures grid
    html += '<div class="tmc-pm-futures-grid">';
    var indices = [['es','ES (SPY)'], ['nq','NQ (QQQ)'], ['rty','RTY (IWM)'], ['ym','YM (DIA)']];
    for(var i = 0; i < indices.length; i++){
      var key = indices[i][0], label = indices[i][1];
      var snap = snapshots[key];
      if(!snap) continue;
      var chg = snap.change_pct || 0;
      var color = chg >= 0 ? '#00c853' : '#ff1744';
      var arrow = chg >= 0 ? '\u25B2' : '\u25BC';
      html += '<div class="tmc-pm-future-card">'
            + '<small class="tmc-pm-future-label">' + _esc(label) + '</small><br>'
            + '<strong>' + (snap.last != null ? Number(snap.last).toLocaleString() : '--') + '</strong> '
            + '<span style="color:' + color + ';font-size:0.85rem;">'
            +   arrow + ' ' + (chg * 100).toFixed(2) + '%'
            + '</span>'
            + '</div>';
    }
    html += '</div>';

    // VIX term structure
    if(vix.structure){
      var vixColor = vix.structure === 'backwardation' ? '#ff1744'
                   : vix.structure === 'contango' ? '#00c853'
                   : '#ffd600';
      html += '<div class="tmc-pm-meta-row">'
            + '<small>VIX: <strong>' + (vix.spot != null ? Number(vix.spot).toFixed(2) : '--') + '</strong></small>'
            + '<small>Term Structure: <strong style="color:' + vixColor + ';">' + _esc((vix.structure || '').toUpperCase()) + '</strong></small>'
            + '<small>Spread: ' + (vix.contango_pct != null ? Number(vix.contango_pct).toFixed(2) + '%' : '--') + '</small>'
            + '</div>';
    }

    // cross-asset
    html += '<div class="tmc-pm-meta-row">'
          + '<small>Oil: <strong>' + (cross.oil_change_pct != null ? (cross.oil_change_pct * 100).toFixed(1) + '%' : '--') + '</strong></small>'
          + '<small>Dollar: <strong>' + (cross.dollar_change_pct != null ? (cross.dollar_change_pct * 100).toFixed(1) + '%' : '--') + '</strong></small>'
          + '<small>10Y: <strong>' + (cross.bond_change_pct != null ? (cross.bond_change_pct * 100).toFixed(1) + '%' : '--') + '</strong></small>'
          + '<small>Cross-Asset: <strong>' + _esc(signal.cross_asset_confirmation || '--') + '</strong></small>'
          + '</div>';

    // position alerts
    if(alerts.length > 0){
      html += '<div class="tmc-pm-alerts">'
            + '<small class="tmc-pm-alerts-title">\u26A0 POSITION ALERTS</small>';
      for(var a = 0; a < alerts.length; a++){
        html += '<div class="tmc-pm-alert-item">'
              + _esc(alerts[a].symbol) + ' ' + _esc(alerts[a].strategy) + ': ' + _esc(alerts[a].impact)
              + '</div>';
      }
      html += '</div>';
    }

    html += '</div>';

    _intelEl.innerHTML = html;
    _intelEl.style.display = 'block';
  }

  /* ══════════════════════════════════════════════════════════════════
   *  DATA FETCHING
   * ══════════════════════════════════════════════════════════════════ */

  function _fetchBars(instrument, timeframe, days){
    return fetch('/api/pre-market/bars/' + encodeURIComponent(instrument) + '?timeframe=' + encodeURIComponent(timeframe) + '&days=' + days)
      .then(function(res){
        if(!res.ok) return { bars: [], prior_close: null };
        return res.json().then(function(data){
          return { bars: Array.isArray(data.bars) ? data.bars : [], prior_close: data.prior_close || null };
        });
      })
      .catch(function(e){
        console.warn('[PreMarket] bars fetch failed for ' + instrument + ':', e && e.message || e);
        return { bars: [], prior_close: null };
      });
  }

  function _normalizePct(bars, priorClose){
    if(!bars.length) return [];
    // Use prior session close as baseline when available; fall back to first bar
    var baseline = (priorClose && priorClose !== 0) ? priorClose : bars[0].close;
    if(!baseline || baseline === 0) return [];
    return bars.map(function(b){
      return { timestamp: b.timestamp || b.date, value: ((b.close - baseline) / baseline) * 100 };
    });
  }

  function _computeGaps(bars){
    var gaps = [];
    for(var i = 1; i < bars.length; i++){
      var prevClose = bars[i - 1].close;
      var todayOpen = bars[i].open;
      if(!prevClose || !todayOpen) continue;
      gaps.push({
        date: bars[i].timestamp || bars[i].date,
        gap_pct: ((todayOpen - prevClose) / prevClose) * 100,
      });
    }
    return gaps;
  }

  /* ══════════════════════════════════════════════════════════════════
   *  CHART THEME
   * ══════════════════════════════════════════════════════════════════ */

  var _CT = {
    grid:       'rgba(255,255,255,0.04)',
    axis:       'rgba(255,255,255,0.08)',
    tickLabel:  'rgba(224,224,224,0.50)',
    xLabel:     'rgba(224,224,224,0.45)',
    legendText: 'rgba(224,224,224,0.70)',
    zeroLine:   'rgba(255,255,255,0.15)',
    emptyText:  'rgba(147,167,182,0.50)',
    positive:   '#00c853',
    negative:   '#ff1744',
    index: { es: '#00e0c3', nq: '#64ffda', rty: '#ff9800', ym: '#b388ff' },
    macro: { cl: '#ff9800', dx: '#b388ff', zn: '#64ffda' },
    vix:        '#ff6b6b',
  };

  /* ══════════════════════════════════════════════════════════════════
   *  SVG CHARTS
   * ══════════════════════════════════════════════════════════════════ */

  /**
   * Generate subtle SVG vertical lines at day boundaries for multi-day charts.
   * @param {Array} bars - array of objects with .timestamp or .date
   * @param {{left:number,right:number,top:number,bottom:number}} margin
   * @param {number} plotW - plot area width
   * @param {number} height - total SVG height
   * @returns {string} SVG line elements
   */
  function _dayBoundaryLines(bars, margin, plotW, height){
    if(!bars || bars.length < 2) return '';
    var svg = '';
    var lastDateStr = null;
    for(var i = 0; i < bars.length; i++){
      var ts = bars[i].timestamp || bars[i].date || bars[i].t;
      if(!ts) continue;
      var d = new Date(ts);
      if(isNaN(d.getTime())) continue;
      var dateStr = d.getUTCFullYear() + '-' + (d.getUTCMonth() + 1) + '-' + d.getUTCDate();
      if(lastDateStr && dateStr !== lastDateStr){
        var bx = margin.left + (i / Math.max(bars.length - 1, 1)) * plotW;
        svg += '<line x1="' + bx.toFixed(1) + '" y1="' + margin.top + '" x2="' + bx.toFixed(1) + '" y2="' + (height - margin.bottom) + '" stroke="rgba(255,255,255,0.08)" stroke-width="1" stroke-dasharray="2,4" shape-rendering="crispEdges"/>';
      }
      lastDateStr = dateStr;
    }
    return svg;
  }

  /* ── Multi-line chart (index futures % change) ── */

  function _renderLineChart(svgEl, lines, opts){
    if(!svgEl) return;
    var allData = lines.flatMap(function(l){ return l.data; });
    if(!allData.length){
      svgEl.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="' + _CT.emptyText + '" font-size="12">No data available</text>';
      return;
    }

    var width = 800, height = 240;
    var margin = { top: 24, right: 14, bottom: 36, left: 56 };
    var plotW = width - margin.left - margin.right;
    var plotH = height - margin.top - margin.bottom;

    var allVals = allData.map(function(d){ return d.value; });
    var minV = Math.min.apply(null, allVals);
    var maxV = Math.max.apply(null, allVals);
    var span = Math.max(maxV - minV, 0.0001);
    minV -= span * 0.05;
    maxV += span * 0.05;
    span = maxV - minV;

    var yFor = function(v){ return margin.top + (1 - ((v - minV) / span)) * plotH; };

    // Global time axis — position all data by timestamp, not array index
    var allTimes = allData.map(function(d){ return new Date(d.timestamp).getTime(); }).filter(function(t){ return !isNaN(t); });
    var minTime = Math.min.apply(null, allTimes);
    var maxTime = Math.max.apply(null, allTimes);
    var timeSpan = Math.max(maxTime - minTime, 1);
    var xForTime = function(ts){
      var t = typeof ts === 'number' ? ts : new Date(ts).getTime();
      return margin.left + ((t - minTime) / timeSpan) * plotW;
    };

    // Y grid + labels
    var yTicks = [0, 0.25, 0.5, 0.75, 1].map(function(r){
      var v = maxV - span * r;
      return { v: v, y: yFor(v) };
    });
    var yGrid = yTicks.map(function(t){
      return '<line x1="' + margin.left + '" y1="' + t.y.toFixed(1) + '" x2="' + (width - margin.right) + '" y2="' + t.y.toFixed(1) + '" stroke="' + _CT.grid + '" stroke-width="0.5" shape-rendering="crispEdges"/>';
    }).join('');
    var yLabels = yTicks.map(function(t){
      return '<text x="' + (margin.left - 6) + '" y="' + (t.y + 3).toFixed(1) + '" text-anchor="end" fill="' + _CT.tickLabel + '" font-size="9" font-family="var(--font-body)">' + t.v.toFixed(2) + '</text>';
    }).join('');

    // Zero line
    var zeroLine = '';
    if(minV <= 0 && maxV >= 0){
      var zy = yFor(0);
      zeroLine = '<line x1="' + margin.left + '" y1="' + zy.toFixed(1) + '" x2="' + (width - margin.right) + '" y2="' + zy.toFixed(1) + '" stroke="' + _CT.zeroLine + '" stroke-width="0.8" stroke-dasharray="4,3" shape-rendering="crispEdges"/>';
    }

    // Build line paths per series — x positioned by timestamp
    var linesSvg = '';
    var legendItems = [];
    lines.forEach(function(series){
      if(!series.data.length) return;
      var pts = series.data.map(function(d){ return { x: xForTime(d.timestamp), y: yFor(d.value) }; });

      // Catmull-Rom smooth curve
      var path;
      if(pts.length <= 2){
        path = pts.map(function(p, i){ return (i === 0 ? 'M' : 'L') + ' ' + p.x.toFixed(1) + ' ' + p.y.toFixed(1); }).join(' ');
      }else{
        path = 'M ' + pts[0].x.toFixed(1) + ' ' + pts[0].y.toFixed(1);
        for(var i = 0; i < pts.length - 1; i++){
          var p0 = pts[Math.max(i - 1, 0)];
          var p1 = pts[i];
          var p2 = pts[i + 1];
          var p3 = pts[Math.min(i + 2, pts.length - 1)];
          var cp1x = p1.x + (p2.x - p0.x) / 10;
          var cp1y = p1.y + (p2.y - p0.y) / 10;
          var cp2x = p2.x - (p3.x - p1.x) / 10;
          var cp2y = p2.y - (p3.y - p1.y) / 10;
          path += ' C ' + cp1x.toFixed(1) + ' ' + cp1y.toFixed(1) + ', ' + cp2x.toFixed(1) + ' ' + cp2y.toFixed(1) + ', ' + p2.x.toFixed(1) + ' ' + p2.y.toFixed(1);
        }
      }

      linesSvg += '<path d="' + path + '" fill="none" stroke="' + series.color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>';

      var last = pts[pts.length - 1];
      var lastVal = series.data[series.data.length - 1].value;
      linesSvg += '<text x="' + (last.x + 4).toFixed(1) + '" y="' + (last.y + 3).toFixed(1) + '" fill="' + series.color + '" font-size="8" font-family="var(--font-body)">' + lastVal.toFixed(2) + '</text>';

      legendItems.push(series);
    });

    // X labels — evenly spaced from time range (~6-hour intervals for 48h)
    var xLabels = '';
    if(timeSpan > 1){
      var fmtTime = function(ms){
        var d = new Date(ms);
        return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
      };
      var yBase = height - margin.bottom + 14;
      // Choose step: ~6h for 48h, ~3h for 24h, ~1h for <12h
      var hoursInWindow = timeSpan / 3600000;
      var stepHours = hoursInWindow > 36 ? 6 : hoursInWindow > 16 ? 3 : 1;
      var stepMs = stepHours * 3600000;
      // Snap first label to the nearest stepHours boundary
      var firstLabel = Math.ceil(minTime / stepMs) * stepMs;
      for(var labelTime = firstLabel; labelTime <= maxTime; labelTime += stepMs){
        var lx = xForTime(labelTime);
        if(lx < margin.left + 10 || lx > width - margin.right - 10) continue;
        xLabels += '<text x="' + lx.toFixed(1) + '" y="' + yBase + '" text-anchor="middle" fill="' + _CT.xLabel + '" font-size="8.5" font-family="var(--font-body)">' + fmtTime(labelTime) + '</text>';
      }
    }

    // Legend row at top
    var legendSvg = '';
    legendItems.forEach(function(s, idx){
      var lx = margin.left + idx * 110;
      legendSvg += '<line x1="' + lx + '" y1="12" x2="' + (lx + 14) + '" y2="12" stroke="' + s.color + '" stroke-width="2.5" stroke-linecap="round"/>';
      legendSvg += '<text x="' + (lx + 18) + '" y="15" fill="' + _CT.legendText + '" font-size="9" font-family="var(--font-body)">' + _esc(s.label) + '</text>';
    });

    // Market open + close vertical lines — positioned by exact timestamp
    var marketLines = '';
    if(opts && opts.showMarketOpen){
      // Determine ET offset: EDT (Mar-Nov) = UTC-4, EST (Nov-Mar) = UTC-5
      // Check DST for the midpoint of the visible window
      var midDate = new Date((minTime + maxTime) / 2);
      var jan = new Date(midDate.getFullYear(), 0, 1);
      var jul = new Date(midDate.getFullYear(), 6, 1);
      // getTimezoneOffset is inverted: more negative = further ahead of UTC
      // US Eastern: EST=-5 (offset=300), EDT=-4 (offset=240)
      // We compute based on the data, not the browser timezone
      // April is EDT (UTC-4), so market open = 13:30 UTC, close = 20:00 UTC
      var etOffsetHours = (midDate.getMonth() >= 2 && midDate.getMonth() <= 10) ? 4 : 5;

      // Iterate calendar days in the visible window
      var dayStart = new Date(minTime);
      dayStart.setUTCHours(0, 0, 0, 0);
      var dayEnd = new Date(maxTime);
      dayEnd.setUTCHours(23, 59, 59, 999);

      for(var dd = new Date(dayStart); dd <= dayEnd; dd.setUTCDate(dd.getUTCDate() + 1)){
        var dow = dd.getUTCDay();
        if(dow === 0 || dow === 6) continue; // skip weekends

        // Market open: 9:30 AM ET in UTC
        var openMs = Date.UTC(dd.getUTCFullYear(), dd.getUTCMonth(), dd.getUTCDate(), 9 + etOffsetHours, 30);
        // Market close: 4:00 PM ET in UTC
        var closeMs = Date.UTC(dd.getUTCFullYear(), dd.getUTCMonth(), dd.getUTCDate(), 16 + etOffsetHours, 0);

        if(openMs >= minTime && openMs <= maxTime){
          var ox = xForTime(openMs);
          marketLines += '<line x1="' + ox.toFixed(1) + '" y1="' + margin.top + '" x2="' + ox.toFixed(1) + '" y2="' + (height - margin.bottom) + '" stroke="rgba(255,199,88,0.4)" stroke-width="1" stroke-dasharray="5,3"/>';
          marketLines += '<text x="' + (ox + 3).toFixed(1) + '" y="' + (margin.top + 10) + '" fill="rgba(255,199,88,0.6)" font-size="8" font-family="var(--font-body)">Open</text>';
        }
        if(closeMs >= minTime && closeMs <= maxTime){
          var cx = xForTime(closeMs);
          marketLines += '<line x1="' + cx.toFixed(1) + '" y1="' + margin.top + '" x2="' + cx.toFixed(1) + '" y2="' + (height - margin.bottom) + '" stroke="rgba(255,23,68,0.4)" stroke-width="1" stroke-dasharray="5,3"/>';
          marketLines += '<text x="' + (cx + 3).toFixed(1) + '" y="' + (margin.top + 10) + '" fill="rgba(255,23,68,0.6)" font-size="8" font-family="var(--font-body)">Close</text>';
        }
      }
    }

    svgEl.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    svgEl.innerHTML = yGrid + zeroLine + marketLines +
      '<line x1="' + margin.left + '" y1="' + margin.top + '" x2="' + margin.left + '" y2="' + (height - margin.bottom) + '" stroke="' + _CT.axis + '" stroke-width="1" shape-rendering="crispEdges"/>' +
      '<line x1="' + margin.left + '" y1="' + (height - margin.bottom) + '" x2="' + (width - margin.right) + '" y2="' + (height - margin.bottom) + '" stroke="' + _CT.axis + '" stroke-width="1" shape-rendering="crispEdges"/>' +
      yLabels + xLabels + legendSvg + linesSvg;
  }

  /* ── Gap history bar chart ── */

  function _renderGapChart(svgEl, gaps){
    if(!svgEl) return;
    if(!gaps.length){
      svgEl.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="' + _CT.emptyText + '" font-size="12">No gap data available</text>';
      return;
    }

    var width = 800, height = 240;
    var margin = { top: 20, right: 14, bottom: 36, left: 56 };
    var plotW = width - margin.left - margin.right;
    var plotH = height - margin.top - margin.bottom;

    var vals = gaps.map(function(g){ return g.gap_pct; });
    var absMax = Math.max.apply(null, vals.map(function(v){ return Math.abs(v); }));
    absMax = Math.max(absMax, 0.1);
    var maxV = absMax * 1.15;
    var minV = -maxV;
    var span = maxV - minV;
    var yFor = function(v){ return margin.top + (1 - ((v - minV) / span)) * plotH; };
    var zeroY = yFor(0);

    var barGap = plotW / gaps.length;
    var barWidth = Math.min(Math.max(barGap * 0.65, 4), 28);

    var labelStep = gaps.length <= 10 ? 1 : gaps.length <= 20 ? 2 : 3;

    var barsSvg = '';
    var xLabelsSvg = '';
    gaps.forEach(function(g, i){
      var cx = margin.left + i * barGap + barGap / 2;
      var bx = cx - barWidth / 2;
      var isUp = g.gap_pct >= 0;
      var color = isUp ? _CT.positive : _CT.negative;
      var barY = isUp ? yFor(g.gap_pct) : zeroY;
      var barH = Math.abs(yFor(g.gap_pct) - zeroY);
      barH = Math.max(barH, 1);

      var tooltip = (isUp ? '+' : '') + g.gap_pct.toFixed(2) + '%';
      barsSvg += '<rect x="' + bx.toFixed(1) + '" y="' + barY.toFixed(1) + '" width="' + barWidth.toFixed(1) + '" height="' + barH.toFixed(1) + '" rx="3" fill="' + color + '" opacity="0.85"><title>' + tooltip + '</title></rect>';

      if(i % labelStep === 0){
        var dateStr = '';
        if(g.date){
          var d = new Date(g.date);
          dateStr = isNaN(d.getTime()) ? String(g.date).slice(5, 10) : (d.getMonth() + 1) + '/' + d.getDate();
        }
        xLabelsSvg += '<text x="' + cx.toFixed(1) + '" y="' + (height - margin.bottom + 13) + '" text-anchor="middle" fill="' + _CT.xLabel + '" font-size="8.5" font-family="var(--font-body)">' + dateStr + '</text>';
      }
    });

    var yTicks = [-absMax, -absMax / 2, 0, absMax / 2, absMax].map(function(v){ return { v: v, y: yFor(v) }; });
    var yGrid = yTicks.map(function(t){
      return '<line x1="' + margin.left + '" y1="' + t.y.toFixed(1) + '" x2="' + (width - margin.right) + '" y2="' + t.y.toFixed(1) + '" stroke="' + _CT.grid + '" stroke-width="0.5" shape-rendering="crispEdges"/>';
    }).join('');
    var yLabels = yTicks.map(function(t){
      return '<text x="' + (margin.left - 6) + '" y="' + (t.y + 3).toFixed(1) + '" text-anchor="end" fill="' + _CT.tickLabel + '" font-size="9" font-family="var(--font-body)">' + (t.v >= 0 ? '+' : '') + t.v.toFixed(2) + '%</text>';
    }).join('');

    var zeroLineSvg = '<line x1="' + margin.left + '" y1="' + zeroY.toFixed(1) + '" x2="' + (width - margin.right) + '" y2="' + zeroY.toFixed(1) + '" stroke="' + _CT.zeroLine + '" stroke-width="1" shape-rendering="crispEdges"/>';

    svgEl.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    svgEl.innerHTML = yGrid + zeroLineSvg +
      '<line x1="' + margin.left + '" y1="' + margin.top + '" x2="' + margin.left + '" y2="' + (height - margin.bottom) + '" stroke="' + _CT.axis + '" stroke-width="1" shape-rendering="crispEdges"/>' +
      '<line x1="' + margin.left + '" y1="' + (height - margin.bottom) + '" x2="' + (width - margin.right) + '" y2="' + (height - margin.bottom) + '" stroke="' + _CT.axis + '" stroke-width="1" shape-rendering="crispEdges"/>' +
      yLabels + xLabelsSvg + barsSvg;
  }

  /* ── VIX chart with term structure badge ── */

  function _renderVixChart(svgEl, vixBars, termStructure){
    if(!svgEl) return;
    if(!vixBars.length){
      svgEl.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="' + _CT.emptyText + '" font-size="12">No VIX data available</text>';
      return;
    }

    var width = 800, height = 240;
    var margin = { top: 28, right: 14, bottom: 36, left: 56 };
    var plotW = width - margin.left - margin.right;
    var plotH = height - margin.top - margin.bottom;

    var closes = vixBars.map(function(b){ return b.close; }).filter(function(v){ return v != null; });
    if(!closes.length){
      svgEl.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="' + _CT.emptyText + '" font-size="12">No VIX close data</text>';
      return;
    }

    var minV = Math.min.apply(null, closes) * 0.95;
    var maxV = Math.max.apply(null, closes) * 1.05;
    var span = Math.max(maxV - minV, 0.1);
    var yFor = function(v){ return margin.top + (1 - ((v - minV) / span)) * plotH; };
    var xFor = function(i){ return margin.left + (i / Math.max(vixBars.length - 1, 1)) * plotW; };

    // VIX line (Catmull-Rom)
    var pts = vixBars.map(function(b, i){ return { x: xFor(i), y: yFor(b.close) }; });
    var linePath;
    if(pts.length <= 2){
      linePath = pts.map(function(p, i){ return (i === 0 ? 'M' : 'L') + ' ' + p.x.toFixed(1) + ' ' + p.y.toFixed(1); }).join(' ');
    }else{
      linePath = 'M ' + pts[0].x.toFixed(1) + ' ' + pts[0].y.toFixed(1);
      for(var i = 0; i < pts.length - 1; i++){
        var p0 = pts[Math.max(i - 1, 0)];
        var p1 = pts[i];
        var p2 = pts[i + 1];
        var p3 = pts[Math.min(i + 2, pts.length - 1)];
        linePath += ' C ' + (p1.x + (p2.x - p0.x) / 10).toFixed(1) + ' ' + (p1.y + (p2.y - p0.y) / 10).toFixed(1) + ', ' + (p2.x - (p3.x - p1.x) / 10).toFixed(1) + ' ' + (p2.y - (p3.y - p1.y) / 10).toFixed(1) + ', ' + p2.x.toFixed(1) + ' ' + p2.y.toFixed(1);
      }
    }

    // Gradient fill under VIX line
    var fillPath = linePath + ' L ' + pts[pts.length - 1].x.toFixed(1) + ' ' + (height - margin.bottom) + ' L ' + pts[0].x.toFixed(1) + ' ' + (height - margin.bottom) + ' Z';

    // Y grid
    var yTicks = [0, 0.25, 0.5, 0.75, 1].map(function(r){
      var v = maxV - span * r;
      return { v: v, y: yFor(v) };
    });
    var yGrid = yTicks.map(function(t){
      return '<line x1="' + margin.left + '" y1="' + t.y.toFixed(1) + '" x2="' + (width - margin.right) + '" y2="' + t.y.toFixed(1) + '" stroke="' + _CT.grid + '" stroke-width="0.5" shape-rendering="crispEdges"/>';
    }).join('');
    var yLbls = yTicks.map(function(t){
      return '<text x="' + (margin.left - 6) + '" y="' + (t.y + 3).toFixed(1) + '" text-anchor="end" fill="' + _CT.tickLabel + '" font-size="9" font-family="var(--font-body)">' + t.v.toFixed(1) + '</text>';
    }).join('');

    // X labels
    var xLbls = '';
    if(vixBars.length > 1){
      var fTs = vixBars[0].timestamp || vixBars[0].date;
      var lTs = vixBars[vixBars.length - 1].timestamp || vixBars[vixBars.length - 1].date;
      var fmtD = function(ts){
        if(!ts) return '';
        var d = new Date(ts);
        return isNaN(d.getTime()) ? String(ts).slice(0, 10) : (d.getMonth() + 1) + '/' + d.getDate();
      };
      var yBase = height - margin.bottom + 14;
      xLbls = '<text x="' + margin.left + '" y="' + yBase + '" fill="' + _CT.xLabel + '" font-size="8.5" font-family="var(--font-body)">' + fmtD(fTs) + '</text>';
      xLbls += '<text x="' + (width - margin.right) + '" y="' + yBase + '" text-anchor="end" fill="' + _CT.xLabel + '" font-size="8.5" font-family="var(--font-body)">' + fmtD(lTs) + '</text>';
    }

    // Legend
    var legendSvg = '<line x1="' + margin.left + '" y1="14" x2="' + (margin.left + 14) + '" y2="14" stroke="' + _CT.vix + '" stroke-width="2.5" stroke-linecap="round"/>';
    legendSvg += '<text x="' + (margin.left + 18) + '" y="17" fill="' + _CT.legendText + '" font-size="9" font-family="var(--font-body)">VIX Level</text>';

    // Term structure badge (upper right corner)
    var badgeSvg = '';
    if(termStructure && termStructure.structure){
      var tsLabel = termStructure.structure.toUpperCase();
      var tsPct = termStructure.contango_pct != null ? ' (' + (termStructure.contango_pct >= 0 ? '+' : '') + termStructure.contango_pct.toFixed(1) + '%)' : '';
      var isContango = termStructure.structure === 'contango';
      var isBackward = termStructure.structure === 'backwardation';
      var badgeColor = isContango ? 'rgba(0,200,83,0.15)' : isBackward ? 'rgba(255,23,68,0.15)' : 'rgba(147,167,182,0.10)';
      var textColor = isContango ? 'rgba(0,200,83,0.95)' : isBackward ? 'rgba(255,23,68,0.95)' : 'rgba(215,251,255,0.70)';
      var badgeText = tsLabel + tsPct;
      var badgeW = badgeText.length * 6.5 + 16;
      var badgeX = width - margin.right - badgeW;
      badgeSvg = '<rect x="' + badgeX + '" y="4" width="' + badgeW + '" height="18" rx="4" fill="' + badgeColor + '"/>';
      badgeSvg += '<text x="' + (badgeX + badgeW / 2) + '" y="16" text-anchor="middle" fill="' + textColor + '" font-size="9" font-weight="600" font-family="var(--font-body)">' + badgeText + '</text>';
    }

    // Last VIX value annotation
    var lastClose = closes[closes.length - 1];
    var lastPt = pts[pts.length - 1];
    var vixAnnot = '<text x="' + (lastPt.x + 4).toFixed(1) + '" y="' + (lastPt.y + 3).toFixed(1) + '" fill="' + _CT.vix + '" font-size="9" font-weight="600" font-family="var(--font-body)">' + lastClose.toFixed(1) + '</text>';

    // Defs for gradient fill
    var defsSvg = '<defs><linearGradient id="vixFillGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="rgba(255,107,107,0.12)"/><stop offset="100%" stop-color="rgba(255,107,107,0.01)"/></linearGradient></defs>';

    // Day boundary vertical lines
    var vixDayLines = _dayBoundaryLines(vixBars, margin, plotW, height);

    svgEl.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    svgEl.innerHTML = defsSvg + yGrid + vixDayLines +
      '<path d="' + fillPath + '" fill="url(#vixFillGrad)"/>' +
      '<line x1="' + margin.left + '" y1="' + margin.top + '" x2="' + margin.left + '" y2="' + (height - margin.bottom) + '" stroke="' + _CT.axis + '" stroke-width="1" shape-rendering="crispEdges"/>' +
      '<line x1="' + margin.left + '" y1="' + (height - margin.bottom) + '" x2="' + (width - margin.right) + '" y2="' + (height - margin.bottom) + '" stroke="' + _CT.axis + '" stroke-width="1" shape-rendering="crispEdges"/>' +
      yLbls + xLbls + legendSvg + badgeSvg +
      '<path d="' + linePath + '" fill="none" stroke="' + _CT.vix + '" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>' +
      vixAnnot;
  }

  /* ── Macro dual-Y-axis chart ── */

  function _renderMacroChart(svgEl, macroBars){
    if(!svgEl) return;
    var clBars = macroBars[0] || [];
    var dxBars = macroBars[1] || [];
    var znBars = macroBars[2] || [];
    if(!clBars.length && !dxBars.length && !znBars.length){
      svgEl.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="' + _CT.emptyText + '" font-size="12">No macro data available</text>';
      return;
    }

    var width = 800, height = 240;
    var margin = { top: 24, right: 52, bottom: 36, left: 56 };
    var plotW = width - margin.left - margin.right;
    var plotH = height - margin.top - margin.bottom;

    // Left axis: Oil raw prices
    var clVals = clBars.map(function(d){ return d.close; });
    var clMin, clMax, clSpan;
    if(clVals.length){
      clMin = Math.min.apply(null, clVals);
      clMax = Math.max.apply(null, clVals);
      clSpan = Math.max(clMax - clMin, 0.01);
      clMin -= clSpan * 0.05; clMax += clSpan * 0.05; clSpan = clMax - clMin;
    } else {
      clMin = 0; clMax = 1; clSpan = 1;
    }
    var yLeft = function(v){ return margin.top + (1 - ((v - clMin) / clSpan)) * plotH; };

    // Right axis: DX + ZN as % change
    var dxPct = _normalizePct(dxBars);
    var znPct = _normalizePct(znBars);
    var rightVals = dxPct.concat(znPct).map(function(d){ return d.value; });
    var rMin, rMax, rSpan;
    if(rightVals.length){
      rMin = Math.min.apply(null, rightVals);
      rMax = Math.max.apply(null, rightVals);
      rSpan = Math.max(rMax - rMin, 0.01);
      rMin -= rSpan * 0.05; rMax += rSpan * 0.05; rSpan = rMax - rMin;
    } else {
      rMin = -1; rMax = 1; rSpan = 2;
    }
    var yRight = function(v){ return margin.top + (1 - ((v - rMin) / rSpan)) * plotH; };

    // Grid + Y ticks
    var yGrid = '', yLeftLbls = '', yRightLbls = '';
    for(var i = 0; i <= 4; i++){
      var ratio = i / 4;
      var py = margin.top + ratio * plotH;
      yGrid += '<line x1="' + margin.left + '" y1="' + py + '" x2="' + (width - margin.right) + '" y2="' + py + '" stroke="' + _CT.grid + '" stroke-width="1" shape-rendering="crispEdges"/>';
      var leftVal = clMax - ratio * clSpan;
      yLeftLbls += '<text x="' + (margin.left - 6) + '" y="' + (py + 3) + '" text-anchor="end" fill="' + _CT.tickLabel + '" font-size="9" font-family="var(--font-body)">' + leftVal.toFixed(1) + '</text>';
      var rightVal = rMax - ratio * rSpan;
      yRightLbls += '<text x="' + (width - margin.right + 6) + '" y="' + (py + 3) + '" text-anchor="start" fill="' + _CT.tickLabel + '" font-size="9" font-family="var(--font-body)">' + rightVal.toFixed(2) + '%</text>';
    }

    // Zero line for right axis
    var zeroSvg = '';
    if(rMin < 0 && rMax > 0){
      var zy = yRight(0);
      zeroSvg = '<line x1="' + margin.left + '" y1="' + zy + '" x2="' + (width - margin.right) + '" y2="' + zy + '" stroke="' + _CT.zeroLine + '" stroke-width="1" stroke-dasharray="4,3" shape-rendering="crispEdges"/>';
    }

    // X labels
    var xRef = [clBars, dxBars, znBars].reduce(function(a, b){ return a.length >= b.length ? a : b; }, []);
    var xLbls = '';
    var labelStep = Math.max(1, Math.floor(xRef.length / 6));
    for(var xi = 0; xi < xRef.length; xi += labelStep){
      var px = margin.left + (xi / Math.max(xRef.length - 1, 1)) * plotW;
      var dt = new Date(xRef[xi].timestamp || xRef[xi].t);
      var lbl = (dt.getMonth() + 1) + '/' + dt.getDate();
      xLbls += '<text x="' + px + '" y="' + (height - margin.bottom + 14) + '" text-anchor="middle" fill="' + _CT.xLabel + '" font-size="9" font-family="var(--font-body)">' + lbl + '</text>';
    }

    // Catmull-Rom spline builder
    function buildPath(data, yFn){
      if(data.length < 2) return '';
      var pts = data.map(function(d, i){ return { x: margin.left + (i / (data.length - 1)) * plotW, y: yFn(d.value !== undefined ? d.value : d.close) }; });
      var path = 'M' + pts[0].x + ',' + pts[0].y;
      for(var j = 0; j < pts.length - 1; j++){
        var p0 = pts[Math.max(j - 1, 0)];
        var p1 = pts[j];
        var p2 = pts[j + 1];
        var p3 = pts[Math.min(j + 2, pts.length - 1)];
        var cp1x = p1.x + (p2.x - p0.x) / 6;
        var cp1y = p1.y + (p2.y - p0.y) / 6;
        var cp2x = p2.x - (p3.x - p1.x) / 6;
        var cp2y = p2.y - (p3.y - p1.y) / 6;
        path += ' C' + cp1x + ',' + cp1y + ' ' + cp2x + ',' + cp2y + ' ' + p2.x + ',' + p2.y;
      }
      return path;
    }

    var clData = clBars.map(function(d){ return { value: d.close, close: d.close }; });
    var clPath = buildPath(clData, function(v){ return yLeft(v); });
    var dxPath = buildPath(dxPct, function(v){ return yRight(v); });
    var znPath = buildPath(znPct, function(v){ return yRight(v); });

    // Legend
    var series = [
      { label: 'Oil (CL)', color: _CT.macro.cl, show: clBars.length > 0 },
      { label: 'Dollar (DX)', color: _CT.macro.dx, show: dxPct.length > 0 },
      { label: '10Y (ZN)', color: _CT.macro.zn, show: znPct.length > 0 },
    ];
    var legendSvg = '';
    var lx = margin.left + 4;
    series.forEach(function(s){
      if(!s.show) return;
      legendSvg += '<rect x="' + lx + '" y="' + (margin.top - 14) + '" width="8" height="8" rx="2" fill="' + s.color + '"/>';
      legendSvg += '<text x="' + (lx + 11) + '" y="' + (margin.top - 6) + '" fill="' + _CT.legendText + '" font-size="9" font-family="var(--font-body)">' + s.label + '</text>';
      lx += (s.label.length * 6) + 22;
    });

    // Axis labels
    var leftAxisLabel = '<text x="' + 12 + '" y="' + (margin.top + plotH / 2) + '" text-anchor="middle" fill="' + _CT.tickLabel + '" font-size="8" font-family="var(--font-body)" transform="rotate(-90,' + 12 + ',' + (margin.top + plotH / 2) + ')">Oil ($)</text>';
    var rightAxisLabel = '<text x="' + (width - 4) + '" y="' + (margin.top + plotH / 2) + '" text-anchor="middle" fill="' + _CT.tickLabel + '" font-size="8" font-family="var(--font-body)" transform="rotate(90,' + (width - 4) + ',' + (margin.top + plotH / 2) + ')">% Change</text>';

    // Day boundary vertical lines (use longest series for reference)
    var macroDayLines = _dayBoundaryLines(xRef, margin, plotW, height);

    svgEl.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    svgEl.innerHTML = yGrid + zeroSvg + macroDayLines +
      '<line x1="' + margin.left + '" y1="' + margin.top + '" x2="' + margin.left + '" y2="' + (height - margin.bottom) + '" stroke="' + _CT.axis + '" stroke-width="1" shape-rendering="crispEdges"/>' +
      '<line x1="' + (width - margin.right) + '" y1="' + margin.top + '" x2="' + (width - margin.right) + '" y2="' + (height - margin.bottom) + '" stroke="' + _CT.axis + '" stroke-width="1" shape-rendering="crispEdges"/>' +
      '<line x1="' + margin.left + '" y1="' + (height - margin.bottom) + '" x2="' + (width - margin.right) + '" y2="' + (height - margin.bottom) + '" stroke="' + _CT.axis + '" stroke-width="1" shape-rendering="crispEdges"/>' +
      yLeftLbls + yRightLbls + xLbls + legendSvg + leftAxisLabel + rightAxisLabel +
      (clPath ? '<path d="' + clPath + '" fill="none" stroke="' + _CT.macro.cl + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' : '') +
      (dxPath ? '<path d="' + dxPath + '" fill="none" stroke="' + _CT.macro.dx + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' : '') +
      (znPath ? '<path d="' + znPath + '" fill="none" stroke="' + _CT.macro.zn + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' : '');
  }

  /* ══════════════════════════════════════════════════════════════════
   *  CHART ORCHESTRATION
   * ══════════════════════════════════════════════════════════════════ */

  function _loadAndRenderCharts(){
    if(!_chartsEl) return Promise.resolve();

    return Promise.all([
      Promise.all(['es','nq','rty','ym'].map(function(sym){ return _fetchBars(sym, '5m', 2); })),
      _fetchBars('vix', '1h', 14),
      Promise.all(['cl','dx','zn'].map(function(sym){ return _fetchBars(sym, '1d', 14); })),
      _fetchBars('es', '1d', 14),
      fetch('/api/pre-market/vix-term-structure').then(function(r){ return r.ok ? r.json() : null; }).catch(function(){ return null; }),
    ]).then(function(results){
      var indexResults = results[0];
      var vixResult    = results[1];
      var macroResults = results[2];
      var gapResult    = results[3];
      var termStruct   = results[4];

      // Unwrap bars from {bars, prior_close} envelopes for non-index charts
      var vixBars   = vixResult.bars;
      var macroBars = macroResults.map(function(r){ return r.bars; });
      var gapBars   = gapResult.bars;

      var hasAnyData = [indexResults.map(function(r){ return r.bars; }), [vixBars], macroBars, [gapBars]].some(function(arr){
        return arr.some(function(b){ return b && b.length > 0; });
      });

      if(!hasAnyData){
        if(_chartsEl) _chartsEl.style.display = 'none';
        if(_emptyEl) _emptyEl.style.display = 'block';
        return;
      }
      if(_chartsEl) _chartsEl.style.display = '';
      if(_emptyEl) _emptyEl.style.display = 'none';

      // Chart 1: Index Futures normalized to % change (48h continuous)
      var indexLabels = ['ES','NQ','RTY','YM'];
      var indexColors = [_CT.index.es, _CT.index.nq, _CT.index.rty, _CT.index.ym];
      var indexLines = indexResults.map(function(res, idx){
        return { label: indexLabels[idx], color: indexColors[idx], data: _normalizePct(res.bars, res.prior_close) };
      }).filter(function(l){ return l.data.length > 0; });
      _renderLineChart(_indexSvg, indexLines, { yLabel: '% Change', showMarketOpen: true });

      // Chart 2: VIX & Term Structure
      _renderVixChart(_vixSvg, vixBars, termStruct);

      // Chart 3: Macro Cross-Asset with dual Y-axes
      _renderMacroChart(_macroSvg, macroBars);

      // Chart 4: Gap History
      var gaps = _computeGaps(gapBars);
      _renderGapChart(_gapSvg, gaps);
    }).catch(function(err){
      console.warn('[PreMarket] chart render failed:', err && err.message || err);
      if(_chartsEl) _chartsEl.style.display = 'none';
      if(_emptyEl) _emptyEl.style.display = 'block';
    });
  }

  /* ── Export ── */
  global.BenTradePreMarket = {
    init:    init,
    refresh: refresh,
    destroy: destroy,
  };

})(window);
