window.BenTradePages = window.BenTradePages || {};

/**
 * Cross-Asset / Macro Confirmation dashboard controller.
 *
 * Fetches from /api/cross-asset-macro and populates dynamic elements.
 * Uses BenTradeDashboardCache for sessionStorage-backed caching.
 */
window.BenTradePages.initCrossAssetMacro = function initCrossAssetMacro(rootEl) {
  var doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  var scope = rootEl || doc;
  var API_URL = '/api/cross-asset-macro';
  var MODEL_URL = '/api/cross-asset-macro/model';
  var CACHE_KEY = 'crossAssetMacro';
  var MODEL_CACHE_KEY = 'crossAssetModel';
  var _cache = window.BenTradeDashboardCache;

  // ── DOM refs ──────────────────────────────────────────────────
  var refreshBtn       = scope.querySelector('#caRefreshBtn');
  var lastUpdatedEl    = scope.querySelector('#caLastUpdated');
  // Hero
  var heroLabel        = scope.querySelector('#caHeroLabel');
  var heroScore        = scope.querySelector('#caHeroScore');
  var labelChip        = scope.querySelector('#caLabelChip');
  var summaryEl        = scope.querySelector('#caSummary');
  // Signal quality
  var signalQualityEl  = scope.querySelector('#caSignalQuality');
  var confidenceChip   = scope.querySelector('#caConfidenceChip');
  // Drivers
  var confirmingDrivers  = scope.querySelector('#caConfirmingDrivers');
  var contradictingDrivers = scope.querySelector('#caContradictingDrivers');
  // Pillar bars
  var pillarBarsEl     = scope.querySelector('#caPillarBars');
  // Rates card
  var tenYieldEl       = scope.querySelector('#caTenYield');
  var twoYieldEl       = scope.querySelector('#caTwoYield');
  var yieldSpreadEl    = scope.querySelector('#caYieldSpread');
  var ratesBar         = scope.querySelector('#caRatesBar');
  var ratesScore       = scope.querySelector('#caRatesScore');
  // Dollar card
  var usdEl            = scope.querySelector('#caUSD');
  var dollarBar        = scope.querySelector('#caDollarBar');
  var dollarScore      = scope.querySelector('#caDollarScore');
  // Commodities
  var oilEl            = scope.querySelector('#caOil');
  var goldEl           = scope.querySelector('#caGold');
  var copperEl         = scope.querySelector('#caCopper');
  // Credit card
  var igSpreadEl       = scope.querySelector('#caIGSpread');
  var hySpreadEl       = scope.querySelector('#caHYSpread');
  var vixEl            = scope.querySelector('#caVIX');
  var creditBar        = scope.querySelector('#caCreditBar');
  var creditScore      = scope.querySelector('#caCreditScore');
  // Coherence
  var coherenceGaugeEl = scope.querySelector('#caCoherenceGauge');
  var coherenceLabel   = scope.querySelector('#caCoherenceLabel');
  // Data quality
  var confidenceScoreEl = scope.querySelector('#caConfidenceScore');
  var warningsEl       = scope.querySelector('#caWarnings');
  // Takeaway
  var takeawayEl       = scope.querySelector('#caTakeaway');
  // AI Model
  var modelLabel       = scope.querySelector('#caModelLabel');
  var modelScore       = scope.querySelector('#caModelScore');
  var modelSummary     = scope.querySelector('#caModelSummary');
  var modelCta         = scope.querySelector('#caModelCta');
  var runModelBtn      = scope.querySelector('#caRunModelBtn');
  var modelDetailsRow  = scope.querySelector('#caModelDetailsRow');
  var modelPillars     = scope.querySelector('#caModelPillars');

  var _destroyed = false;

  // ── Utilities ─────────────────────────────────────────────────

  function escapeHtml(val) {
    return String(val != null ? val : '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
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
    if (l.indexOf('strong confirm') >= 0 || l.indexOf('confirming') >= 0) return 'mod-chip-bullish';
    if (l.indexOf('contradiction') >= 0 || l.indexOf('contra') >= 0) return 'mod-chip-bearish';
    return 'mod-chip-neutral';
  }

  function signalChipClass(quality) {
    if (quality === 'high') return 'mod-signal-high';
    if (quality === 'medium') return 'mod-signal-medium';
    return 'mod-signal-low';
  }

  function fmtVal(val, suffix) {
    if (val == null) return '—';
    return Number(val).toFixed(2) + (suffix || '');
  }

  function fmtBp(val) {
    if (val == null) return '—';
    var bp = (val * 100).toFixed(0);
    return (val >= 0 ? '+' : '') + bp + 'bp';
  }

  // ── Render ────────────────────────────────────────────────────

  function render(payload) {
    if (_destroyed) return;
    var er = payload.engine_result || {};
    var dq = payload.data_quality || {};

    // ── Degraded-state banner ─────────────────────────────────
    var degradedBanner = scope.querySelector('#caDegradedBanner');
    var sourceErrors = (dq.source_errors && Object.keys(dq.source_errors).length > 0)
      ? dq.source_errors : null;
    if (degradedBanner) {
      if (sourceErrors) {
        var failedSources = Object.keys(sourceErrors).join(', ');
        degradedBanner.innerHTML = '&#9888; Partial data — failed sources: ' + escapeHtml(failedSources) +
          '. Scores may be degraded.';
        degradedBanner.style.display = '';
      } else if (er.error || (payload.error && !er.score && er.score !== 0)) {
        degradedBanner.innerHTML = '&#9888; Engine error — ' + escapeHtml(er.summary || payload.error || 'unknown');
        degradedBanner.style.display = '';
      } else {
        degradedBanner.style.display = 'none';
      }
    }

    // Hero card
    setText(heroLabel, er.label);
    setText(heroScore, er.score != null ? Math.round(er.score) : '—');
    if (heroLabel) heroLabel.style.color = scoreColor(er.score);
    if (heroScore) heroScore.style.color = scoreColor(er.score);
    if (labelChip) {
      labelChip.textContent = er.short_label || '—';
      labelChip.className = 'mod-chip ' + chipClass(er.label);
    }
    setText(summaryEl, er.summary);

    // Signal quality
    if (signalQualityEl) {
      signalQualityEl.textContent = (er.signal_quality || 'low').toUpperCase() + ' CONFIDENCE';
      signalQualityEl.className = 'mod-signal-chip ' + signalChipClass(er.signal_quality);
    }
    if (confidenceChip) {
      confidenceChip.textContent = er.confidence_score != null ? Math.round(er.confidence_score) + '/100' : '—';
    }

    // Drivers
    renderList(confirmingDrivers, er.confirming_signals, 'positive');
    renderList(contradictingDrivers, er.contradicting_signals, 'negative');

    // Pillar bars
    if (pillarBarsEl) {
      var ps = er.pillar_scores || {};
      var pw = er.pillar_weights || {};
      var names = {
        rates_yield_curve: 'Rates & Yield Curve',
        dollar_commodity: 'Dollar & Commodity',
        credit_risk_appetite: 'Credit & Risk Appetite',
        defensive_vs_growth: 'Defensive vs Growth',
        macro_coherence: 'Macro Coherence'
      };
      var html = '';
      Object.keys(names).forEach(function(key) {
        var s = ps[key];
        var w = pw[key] ? Math.round(pw[key] * 100) : 0;
        var pct = s != null ? Math.round(s) : 0;
        html += '<div class="mod-bar-row">' +
          '<span class="mod-bar-label">' + escapeHtml(names[key]) + ' (' + w + '%)</span>' +
          '<div class="mod-bar-track"><div class="mod-bar-fill" style="width:' + pct + '%;background:' + scoreColor(s) + ';"></div></div>' +
          '<span class="mod-bar-val">' + (s != null ? pct : '—') + '</span></div>';
      });
      pillarBarsEl.innerHTML = html;
    }

    // Rates card
    var rawRates = (er.raw_inputs || {}).rates || {};
    setText(tenYieldEl, fmtVal(rawRates.ten_year_yield, '%'));
    setText(twoYieldEl, fmtVal(rawRates.two_year_yield, '%'));
    setText(yieldSpreadEl, fmtBp(rawRates.yield_curve_spread));
    setBar(ratesBar, ratesScore, (er.pillar_scores || {}).rates_yield_curve);

    // Dollar card
    var rawDC = (er.raw_inputs || {}).dollar_commodity || {};
    setText(usdEl, fmtVal(rawDC.usd_index));
    setText(oilEl, rawDC.oil_wti != null ? '$' + Number(rawDC.oil_wti).toFixed(2) : '—');
    // Show oil ambiguity note if price is in $45-$85 range
    var oilAmbiguityNote = scope.querySelector('#caOilAmbiguityNote');
    if (oilAmbiguityNote) {
      var oilVal = rawDC.oil_wti;
      oilAmbiguityNote.style.display = (oilVal != null && oilVal >= 45 && oilVal <= 85) ? '' : 'none';
    }
    setText(goldEl, rawDC.gold_price != null ? '$' + Number(rawDC.gold_price).toFixed(0) : '—');
    setText(copperEl, rawDC.copper_price != null ? '$' + Number(rawDC.copper_price).toFixed(0) + '/mt' : '—');
    setBar(dollarBar, dollarScore, (er.pillar_scores || {}).dollar_commodity);

    // Credit card
    var rawCr = (er.raw_inputs || {}).credit || {};
    setText(igSpreadEl, rawCr.ig_spread != null ? rawCr.ig_spread.toFixed(2) + '%' : '—');
    setText(hySpreadEl, rawCr.hy_spread != null ? rawCr.hy_spread.toFixed(2) + '%' : '—');
    setText(vixEl, fmtVal(rawCr.vix));
    setBar(creditBar, creditScore, (er.pillar_scores || {}).credit_risk_appetite);

    // Coherence gauge
    var cohScore = (er.pillar_scores || {}).macro_coherence;
    if (coherenceGaugeEl) {
      coherenceGaugeEl.textContent = cohScore != null ? Math.round(cohScore) : '—';
      coherenceGaugeEl.style.color = scoreColor(cohScore);
    }
    if (coherenceLabel) {
      if (cohScore != null) {
        coherenceLabel.textContent = cohScore >= 70 ? 'HIGH' : (cohScore >= 45 ? 'MED' : 'LOW');
      } else {
        coherenceLabel.textContent = '—';
      }
    }

    // Data quality
    setText(confidenceScoreEl, er.confidence_score != null ? Math.round(er.confidence_score) + '/100' : '—');

    // Warnings
    if (warningsEl) {
      var warns = er.warnings || [];
      if (warns.length === 0) {
        warningsEl.innerHTML = '<div style="opacity:0.5;font-size:10px;">No warnings</div>';
      } else {
        warningsEl.innerHTML = warns.slice(0, 10).map(function(w) {
          return '<div class="mod-warning-item" style="font-size:10px;margin-bottom:2px;">⚠ ' + escapeHtml(w) + '</div>';
        }).join('');
      }
    }

    // Takeaway
    setText(takeawayEl, er.trader_takeaway);

    // Updated timestamp
    if (lastUpdatedEl) {
      var ts = er.as_of;
      if (ts) {
        try {
          var d = new Date(ts);
          lastUpdatedEl.textContent = 'Updated: ' + d.toLocaleTimeString();
        } catch (_) {
          lastUpdatedEl.textContent = 'Updated: ' + ts;
        }
      }
    }
  }

  // ── Model Analysis render ─────────────────────────────────────

  function renderModel(model) {
    if (!model) {
      if (modelCta) modelCta.style.display = '';
      if (modelDetailsRow) modelDetailsRow.style.display = 'none';
      return;
    }
    if (modelCta) modelCta.style.display = 'none';
    if (modelDetailsRow) modelDetailsRow.style.display = '';

    setText(modelLabel, model.label);
    if (modelLabel) modelLabel.style.color = scoreColor(model.score);
    setText(modelScore, model.score != null ? Math.round(model.score) + '/100' : '—');
    if (modelScore) modelScore.style.color = scoreColor(model.score);
    setText(modelSummary, model.summary);

    // Pillar analysis
    if (modelPillars) {
      var pa = model.pillar_analysis || {};
      var html = '';
      Object.keys(pa).forEach(function(key) {
        html += '<div style="margin-bottom:4px;"><span style="font-weight:600;font-size:10px;">' +
          escapeHtml(key.replace(/_/g, ' ').replace(/\b\w/g, function(l) { return l.toUpperCase(); })) +
          ':</span> <span style="font-size:10px;">' + escapeHtml(pa[key]) + '</span></div>';
      });
      modelPillars.innerHTML = html;
    }
  }

  // ── Fetch & Cache ─────────────────────────────────────────────

  function setLoading(btn, loading) {
    if (!btn) return;
    if (loading) {
      btn.disabled = true;
      btn.classList.add('btn-refreshing');
      btn.innerHTML = '<span class="btn-spinner"></span> Analyzing…';
    } else {
      btn.disabled = false;
      btn.classList.remove('btn-refreshing');
      btn.innerHTML = '↻ Analyze';
    }
  }

  function fetchData(force) {
    var url = API_URL + (force ? '?force=true' : '');
    setLoading(refreshBtn, true);

    fetch(url)
      .then(function(res) { return res.json(); })
      .then(function(payload) {
        if (_destroyed) return;
        render(payload);
        if (_cache) _cache.setCache(CACHE_KEY, payload);
        setLoading(refreshBtn, false);
      })
      .catch(function(err) {
        console.error('[BenTrade][CrossAsset] fetch error', err);
        setLoading(refreshBtn, false);
      });
  }

  function fetchModel(force) {
    if (runModelBtn) {
      runModelBtn.disabled = true;
      runModelBtn.textContent = 'Running…';
    }

    fetch(MODEL_URL, { method: 'POST' })
      .then(function(res) { return res.json(); })
      .then(function(result) {
        if (_destroyed) return;
        renderModel(result.model_analysis);
        if (_cache && result.model_analysis) {
          _cache.setCache(MODEL_CACHE_KEY, result);
        }
        if (runModelBtn) {
          runModelBtn.disabled = false;
          runModelBtn.textContent = 'Re-run Model';
        }
      })
      .catch(function(err) {
        console.error('[BenTrade][CrossAsset] model fetch error', err);
        if (runModelBtn) {
          runModelBtn.disabled = false;
          runModelBtn.textContent = 'Run AI Model';
        }
      });
  }

  // ── Init ──────────────────────────────────────────────────────

  // Try cache first
  if (_cache && _cache.hasCache(CACHE_KEY)) {
    console.log('[BenTrade][CrossAsset] cache_rehydrate');
    render(_cache.getCache(CACHE_KEY));
  }
  if (_cache && _cache.hasCache(MODEL_CACHE_KEY)) {
    var mc = _cache.getCache(MODEL_CACHE_KEY);
    if (mc && mc.model_analysis) renderModel(mc.model_analysis);
  }

  // Always fetch fresh data
  fetchData(false);

  // Wire buttons
  if (refreshBtn) {
    refreshBtn.addEventListener('click', function() { fetchData(true); });
  }
  if (runModelBtn) {
    runModelBtn.addEventListener('click', function() { fetchModel(true); });
  }

  return function cleanupCrossAssetMacro() {
    _destroyed = true;
    console.log('[BenTrade][CrossAsset] cleanup — DOM detached, cache preserved');
  };
};
