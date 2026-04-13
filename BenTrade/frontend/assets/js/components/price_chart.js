/**
 * price_chart.js — Reusable Chart.js price chart component
 * Renders close + SMA50 + SMA200 line chart with key levels.
 * Fetches from GET /api/company-evaluator/charts/{symbol}?timeframe={6M|1Y|3Y|5Y}
 *
 * Namespace: window.BenTradeComponents.mountPriceChart / destroyPriceChart
 */
(function () {
  'use strict';

  // ── Active chart instances keyed by containerId ──
  var _instances = {};

  // ── Color palette (dark theme) ──
  var COLORS = {
    close:       '#80e0e0',
    sma50:       '#f0c060',
    sma200:      '#c080f0',
    support:     '#60d890',
    resistance:  '#f08070',
    week52:      '#506878',
    grid:        'rgba(80,120,150,0.08)',
    text:        '#80a0b8',
    tooltipBg:   'rgba(8,20,32,0.92)',
    tooltipText: '#d0e0f0'
  };

  var TIMEFRAMES = ['6M', '1Y', '3Y', '5Y'];
  var DEFAULT_TF = '1Y';

  // ── Public API ──

  /**
   * Mount a price chart into the given container.
   * @param {string} containerId  - DOM id of the container div
   * @param {string} symbol       - Ticker symbol
   * @param {object} [opts]       - { timeframe, showTimeframeButtons }
   */
  function mountPriceChart(containerId, symbol, opts) {
    opts = opts || {};
    var timeframe = opts.timeframe || DEFAULT_TF;
    var showTimeframeButtons = opts.showTimeframeButtons !== false;

    var container = document.getElementById(containerId);
    if (!container) return;

    // Tear down any existing instance
    _destroyInstance(containerId);

    // Build DOM skeleton
    container.innerHTML = '';
    container.classList.add('bt-price-chart-wrap');

    // Timeframe bar
    if (showTimeframeButtons) {
      var tfBar = document.createElement('div');
      tfBar.className = 'bt-pc-timeframe-bar';
      for (var t = 0; t < TIMEFRAMES.length; t++) {
        var btn = document.createElement('button');
        btn.className = 'bt-pc-tf-btn' + (TIMEFRAMES[t] === timeframe ? ' active' : '');
        btn.setAttribute('data-tf', TIMEFRAMES[t]);
        btn.textContent = TIMEFRAMES[t];
        tfBar.appendChild(btn);
      }
      container.appendChild(tfBar);

      // Bind timeframe clicks
      tfBar.addEventListener('click', function (e) {
        var tfVal = e.target.getAttribute('data-tf');
        if (!tfVal) return;
        var inst = _instances[containerId];
        if (!inst) return;
        // Update active class
        var allBtns = tfBar.querySelectorAll('.bt-pc-tf-btn');
        for (var i = 0; i < allBtns.length; i++) allBtns[i].classList.remove('active');
        e.target.classList.add('active');
        inst.timeframe = tfVal;
        _loadAndRender(containerId);
      });
    }

    // Canvas wrapper
    var canvasWrap = document.createElement('div');
    canvasWrap.className = 'bt-pc-canvas-wrap';
    container.appendChild(canvasWrap);

    // Status area (loading / error / meta)
    var statusEl = document.createElement('div');
    statusEl.className = 'bt-pc-status';
    container.appendChild(statusEl);

    // Store instance
    _instances[containerId] = {
      symbol: symbol,
      timeframe: timeframe,
      chart: null,
      container: container,
      canvasWrap: canvasWrap,
      statusEl: statusEl
    };

    _loadAndRender(containerId);
  }

  /**
   * Destroy a chart instance and clean up.
   * @param {string} containerId
   */
  function destroyPriceChart(containerId) {
    _destroyInstance(containerId);
    var container = document.getElementById(containerId);
    if (container) {
      container.innerHTML = '';
      container.classList.remove('bt-price-chart-wrap');
    }
  }

  // ── Internal ──

  function _destroyInstance(containerId) {
    var inst = _instances[containerId];
    if (!inst) return;
    if (inst.chart) {
      inst.chart.destroy();
      inst.chart = null;
    }
    delete _instances[containerId];
  }

  function _loadAndRender(containerId) {
    var inst = _instances[containerId];
    if (!inst) return;

    // Show loading state
    inst.canvasWrap.innerHTML =
      '<div class="bt-pc-loading">'
        + '<div class="bt-pc-spinner"></div>'
        + '<span>Loading chart\u2026</span>'
      + '</div>';
    inst.statusEl.textContent = '';

    var url = '/api/company-evaluator/charts/'
      + encodeURIComponent(inst.symbol)
      + '?timeframe=' + encodeURIComponent(inst.timeframe);

    fetch(url)
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        // Verify instance still alive
        if (!_instances[containerId]) return;
        _renderChart(containerId, data);
      })
      .catch(function (err) {
        if (!_instances[containerId]) return;
        inst.canvasWrap.innerHTML =
          '<div class="bt-pc-error">Chart unavailable: ' + _esc(err.message) + '</div>';
      });
  }

  function _renderChart(containerId, data) {
    var inst = _instances[containerId];
    if (!inst) return;

    var prices = data.prices || [];
    var sma50  = data.sma_50 || [];
    var sma200 = data.sma_200 || [];
    var levels = data.levels || {};
    var meta   = data.metadata || {};

    if (!prices.length) {
      inst.canvasWrap.innerHTML =
        '<div class="bt-pc-error">No price data available</div>';
      return;
    }

    // Parse dates and close values
    var labels = [];
    var closes = [];
    for (var i = 0; i < prices.length; i++) {
      labels.push(prices[i].date || prices[i].d || '');
      closes.push(prices[i].close != null ? prices[i].close : (prices[i].c != null ? prices[i].c : null));
    }

    // SMA arrays — match to prices length (backend may send shorter arrays)
    var sma50Data  = _alignSeries(sma50, prices);
    var sma200Data = _alignSeries(sma200, prices);

    // Destroy old chart if any
    if (inst.chart) { inst.chart.destroy(); inst.chart = null; }

    // Create fresh canvas
    inst.canvasWrap.innerHTML = '';
    var canvas = document.createElement('canvas');
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    inst.canvasWrap.appendChild(canvas);

    // Build datasets
    var datasets = [
      {
        label: 'Close',
        data: closes,
        borderColor: COLORS.close,
        backgroundColor: 'rgba(128,224,224,0.06)',
        borderWidth: 1.5,
        pointRadius: 0,
        pointHitRadius: 6,
        fill: true,
        tension: 0.15,
        order: 1
      },
      {
        label: 'SMA 50',
        data: sma50Data,
        borderColor: COLORS.sma50,
        borderWidth: 1.2,
        borderDash: [4, 3],
        pointRadius: 0,
        fill: false,
        tension: 0.15,
        spanGaps: true,
        order: 2
      },
      {
        label: 'SMA 200',
        data: sma200Data,
        borderColor: COLORS.sma200,
        borderWidth: 1.2,
        borderDash: [6, 4],
        pointRadius: 0,
        fill: false,
        tension: 0.15,
        spanGaps: true,
        order: 3
      }
    ];

    // Add horizontal level lines as flat datasets
    var levelEntries = [
      { key: 'support',       label: 'Support',       color: COLORS.support },
      { key: 'resistance',    label: 'Resistance',    color: COLORS.resistance },
      { key: 'week_52_high',  label: '52w High',      color: COLORS.week52 },
      { key: 'week_52_low',   label: '52w Low',       color: COLORS.week52 }
    ];
    for (var li = 0; li < levelEntries.length; li++) {
      var lv = levelEntries[li];
      var val = levels[lv.key];
      if (val == null) continue;
      var flatArr = [];
      for (var fi = 0; fi < labels.length; fi++) flatArr.push(val);
      datasets.push({
        label: lv.label,
        data: flatArr,
        borderColor: lv.color,
        borderWidth: 1,
        borderDash: [2, 4],
        pointRadius: 0,
        fill: false,
        tension: 0,
        order: 10
      });
    }

    // Render with Chart.js
    var ctx = canvas.getContext('2d');
    inst.chart = new Chart(ctx, {
      type: 'line',
      data: { labels: labels, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: 'index',
          intersect: false
        },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: {
              color: COLORS.text,
              font: { size: 11 },
              boxWidth: 14,
              padding: 10,
              usePointStyle: true
            }
          },
          tooltip: {
            backgroundColor: COLORS.tooltipBg,
            titleColor: COLORS.tooltipText,
            bodyColor: COLORS.tooltipText,
            borderColor: 'rgba(0,234,255,0.18)',
            borderWidth: 1,
            padding: 10,
            callbacks: {
              label: function (ctx) {
                var v = ctx.parsed.y;
                if (v == null) return '';
                return ctx.dataset.label + ': $' + v.toFixed(2);
              }
            }
          }
        },
        scales: {
          x: {
            ticks: {
              color: COLORS.text,
              font: { size: 10 },
              maxTicksLimit: 12,
              maxRotation: 0
            },
            grid: { color: COLORS.grid }
          },
          y: {
            position: 'right',
            ticks: {
              color: COLORS.text,
              font: { size: 10 },
              callback: function (v) { return '$' + v.toFixed(0); }
            },
            grid: { color: COLORS.grid }
          }
        }
      }
    });

    // Show metadata notes
    var notes = meta.notes || [];
    if (notes.length) {
      inst.statusEl.innerHTML =
        '<div class="bt-pc-meta">' + notes.map(_esc).join(' &middot; ') + '</div>';
    } else {
      inst.statusEl.textContent = '';
    }
  }

  /**
   * Align an SMA series to the prices array length.
   * SMA arrays from the backend may be shorter (leading nulls).
   */
  function _alignSeries(smaArr, prices) {
    var result = [];
    var offset = prices.length - smaArr.length;
    for (var i = 0; i < prices.length; i++) {
      if (i < offset || !smaArr[i - offset]) {
        result.push(null);
      } else {
        var item = smaArr[i - offset];
        result.push(item.value != null ? item.value : (item.v != null ? item.v : null));
      }
    }
    return result;
  }

  function _esc(s) {
    if (s == null) return '';
    var d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  // ── Expose on namespace ──
  window.BenTradeComponents = window.BenTradeComponents || {};
  window.BenTradeComponents.mountPriceChart = mountPriceChart;
  window.BenTradeComponents.destroyPriceChart = destroyPriceChart;
})();
