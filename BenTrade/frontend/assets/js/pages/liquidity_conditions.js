window.BenTradePages = window.BenTradePages || {};

/**
 * Liquidity & Financial Conditions dashboard controller.
 *
 * Fetches from /api/liquidity-conditions and populates dynamic elements.
 * Uses BenTradeDashboardCache for sessionStorage-backed caching.
 */
window.BenTradePages.initLiquidityConditions = function initLiquidityConditions(rootEl) {
  var doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  var scope = rootEl || doc;
  var API_URL = '/api/liquidity-conditions';
  var MODEL_URL = '/api/liquidity-conditions/model';
  var CACHE_KEY = 'liquidityConditions';
  var MODEL_CACHE_KEY = 'liquidityConditionsModel';
  var _cache = window.BenTradeDashboardCache;
  var _destroyed = false;

  // ── DOM refs ──────────────────────────────────────────────────
  var refreshBtn       = scope.querySelector('#lcRefreshBtn');
  var lastUpdatedEl    = scope.querySelector('#lcLastUpdated');
  var degradedBanner   = scope.querySelector('#lcDegradedBanner');
  // Hero
  var heroLabel        = scope.querySelector('#lcHeroLabel');
  var heroScore        = scope.querySelector('#lcHeroScore');
  var labelChip        = scope.querySelector('#lcLabelChip');
  var signalQualityEl  = scope.querySelector('#lcSignalQuality');
  var confidenceChip   = scope.querySelector('#lcConfidenceChip');
  var summaryEl        = scope.querySelector('#lcSummary');
  // Drivers
  var positiveDrivers  = scope.querySelector('#lcPositiveDrivers');
  var negativeDrivers  = scope.querySelector('#lcNegativeDrivers');
  var conflictingSignals = scope.querySelector('#lcConflictingSignals');
  // Support vs Stress bars
  var supportBar       = scope.querySelector('#lcSupportBar');
  var supportVal       = scope.querySelector('#lcSupportVal');
  var tightenBar       = scope.querySelector('#lcTightenBar');
  var tightenVal       = scope.querySelector('#lcTightenVal');
  var stressBar        = scope.querySelector('#lcStressBar');
  var stressVal        = scope.querySelector('#lcStressVal');
  var fragilityBarEl   = scope.querySelector('#lcFragilityBar');
  var fragilityValEl   = scope.querySelector('#lcFragilityVal');
  // Pillar 1 — Rates & Policy
  var lcTwoYear        = scope.querySelector('#lcTwoYear');
  var lcTenYear        = scope.querySelector('#lcTenYear');
  var lcFedFunds       = scope.querySelector('#lcFedFunds');
  var lcCurveSpread    = scope.querySelector('#lcCurveSpread');
  var p1Bar            = scope.querySelector('#lcP1Bar');
  var p1Score          = scope.querySelector('#lcP1Score');
  // Pillar 2 — Financial Conditions
  var lcVIX            = scope.querySelector('#lcVIX');
  var lcFCI            = scope.querySelector('#lcFCI');
  var lcCondTrend      = scope.querySelector('#lcCondTrend');
  var lcBroadTight     = scope.querySelector('#lcBroadTight');
  var p2Bar            = scope.querySelector('#lcP2Bar');
  var p2Score          = scope.querySelector('#lcP2Score');
  // Pillar 3 — Credit & Funding
  var lcIGSpread       = scope.querySelector('#lcIGSpread');
  var lcHYSpread       = scope.querySelector('#lcHYSpread');
  var lcCreditStress   = scope.querySelector('#lcCreditStress');
  var lcFundingStress  = scope.querySelector('#lcFundingStress');
  var p3Bar            = scope.querySelector('#lcP3Bar');
  var p3Score          = scope.querySelector('#lcP3Score');
  // Pillar 4 — Dollar & Global
  var lcDXY            = scope.querySelector('#lcDXY');
  var lcDXYTrend       = scope.querySelector('#lcDXYTrend');
  var lcDollarPress    = scope.querySelector('#lcDollarPress');
  var lcGlobalHead     = scope.querySelector('#lcGlobalHead');
  var p4Bar            = scope.querySelector('#lcP4Bar');
  var p4Score          = scope.querySelector('#lcP4Score');
  // Pillar 5 — Stability & Fragility
  var lcStability      = scope.querySelector('#lcStability');
  var lcContradiction  = scope.querySelector('#lcContradiction');
  var lcFragility      = scope.querySelector('#lcFragility');
  var lcSuddenStress   = scope.querySelector('#lcSuddenStress');
  var p5Bar            = scope.querySelector('#lcP5Bar');
  var p5Score          = scope.querySelector('#lcP5Score');
  // Pillar summary bars
  var pillarBarsEl     = scope.querySelector('#lcPillarBars');
  // Data quality
  var confidenceScoreEl = scope.querySelector('#lcConfidenceScore');
  var sigQualityEl     = scope.querySelector('#lcSigQuality');
  var missingCountEl   = scope.querySelector('#lcMissingCount');
  var warningCountEl   = scope.querySelector('#lcWarningCount');
  var warningsEl       = scope.querySelector('#lcWarnings');
  // Takeaway
  var takeawayEl       = scope.querySelector('#lcTakeaway');
  // AI Model
  var runModelBtn      = scope.querySelector('#lcRunModelBtn');
  var modelCta         = scope.querySelector('#lcModelCta');
  var modelDetailsRow  = scope.querySelector('#lcModelDetailsRow');
  var modelLabel       = scope.querySelector('#lcModelLabel');
  var modelScore       = scope.querySelector('#lcModelScore');
  var modelSummary     = scope.querySelector('#lcModelSummary');
  var modelPillars     = scope.querySelector('#lcModelPillars');

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

  function stressColor(score) {
    // For stress/tightening metrics — inverted: higher = more concern
    if (score == null) return '#888';
    if (score >= 60) return 'rgba(255,79,102,0.9)';
    if (score >= 40) return 'var(--warn, #ffab40)';
    if (score >= 20) return 'var(--cyan, #00eaff)';
    return '#00e676';
  }

  function setBar(barEl, valEl, score, invertColor) {
    if (!barEl || !valEl) return;
    if (score == null) {
      barEl.style.width = '0%';
      valEl.textContent = '\u2014';
      return;
    }
    var pct = Math.min(100, Math.max(0, Math.round(score)));
    barEl.style.width = pct + '%';
    barEl.style.background = invertColor ? stressColor(score) : scoreColor(score);
    valEl.textContent = pct;
  }

  function setText(el, val) {
    if (el) el.textContent = (val != null ? val : '\u2014');
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
    if (l.indexOf('supportive') >= 0 || l.indexOf('strongly') >= 0) return 'mod-chip-bullish';
    if (l.indexOf('restrictive') >= 0 || l.indexOf('stress') >= 0) return 'mod-chip-bearish';
    if (l.indexOf('tightening') >= 0) return 'mod-chip-bearish';
    return 'mod-chip-neutral';
  }

  function signalChipClass(quality) {
    if (quality === 'high') return 'mod-signal-high';
    if (quality === 'medium') return 'mod-signal-medium';
    return 'mod-signal-low';
  }

  function fmtVal(val, suffix) {
    if (val == null) return '\u2014';
    return Number(val).toFixed(2) + (suffix || '');
  }

  function fmtPct(val) {
    if (val == null) return '\u2014';
    return Number(val).toFixed(2) + '%';
  }

  function submetricScore(pillarDetails, pillarName, subName) {
    var pd = pillarDetails || {};
    var pillar = pd[pillarName] || {};
    var subs = pillar.submetrics || [];
    for (var i = 0; i < subs.length; i++) {
      if (subs[i].name === subName) return subs[i].score;
    }
    return null;
  }

  // ── Render Engine Data ────────────────────────────────────────

  function render(payload) {
    if (_destroyed) return;
    var er = payload.engine_result || {};
    var dq = payload.data_quality || {};

    // Degraded-state banner
    var sourceErrors = (dq.source_errors && Object.keys(dq.source_errors).length > 0)
      ? dq.source_errors : null;
    if (degradedBanner) {
      if (sourceErrors) {
        var failedSources = Object.keys(sourceErrors).join(', ');
        degradedBanner.innerHTML = '&#9888; Partial data \u2014 failed sources: ' + escapeHtml(failedSources) +
          '. Scores may be degraded.';
        degradedBanner.style.display = '';
      } else if (er.error || (payload.error && !er.score && er.score !== 0)) {
        degradedBanner.innerHTML = '&#9888; Engine error \u2014 ' + escapeHtml(er.summary || payload.error || 'unknown');
        degradedBanner.style.display = '';
      } else {
        degradedBanner.style.display = 'none';
      }
    }

    // Hero card
    setText(heroLabel, er.label);
    setText(heroScore, er.score != null ? Math.round(er.score) : '\u2014');
    if (heroLabel) heroLabel.style.color = scoreColor(er.score);
    if (heroScore) heroScore.style.color = scoreColor(er.score);
    if (labelChip) {
      labelChip.textContent = er.short_label || '\u2014';
      labelChip.className = 'mod-chip ' + chipClass(er.label);
    }
    setText(summaryEl, er.summary);

    // Signal quality
    if (signalQualityEl) {
      signalQualityEl.textContent = (er.signal_quality || 'low').toUpperCase() + ' CONFIDENCE';
      signalQualityEl.className = 'mod-signal-chip ' + signalChipClass(er.signal_quality);
    }
    if (confidenceChip) {
      confidenceChip.textContent = er.confidence_score != null ? Math.round(er.confidence_score) + '/100' : '\u2014';
    }

    // Drivers
    renderList(positiveDrivers, er.positive_contributors, 'positive');
    renderList(negativeDrivers, er.negative_contributors, 'negative');
    renderList(conflictingSignals, er.conflicting_signals, 'warning');

    // Support vs stress bars
    var svs = er.support_vs_stress || {};
    setBar(supportBar, supportVal, svs.supportive_for_risk_assets);
    setBar(tightenBar, tightenVal, svs.tightening_pressure, true);
    setBar(stressBar, stressVal, svs.stress_risk, true);
    setBar(fragilityBarEl, fragilityValEl, svs.fragility, true);

    // Raw inputs
    var rawRates = (er.raw_inputs || {}).rates || {};
    var rawCond = (er.raw_inputs || {}).conditions || {};
    var rawCredit = (er.raw_inputs || {}).credit || {};
    var rawDollar = (er.raw_inputs || {}).dollar || {};

    // Diagnostics for submetric scores
    var diag = er.diagnostics || {};
    var pillarDetails = diag.pillar_details || {};

    // Pillar 1 — Rates & Policy
    setText(lcTwoYear, fmtPct(rawRates.two_year_yield));
    setText(lcTenYear, fmtPct(rawRates.ten_year_yield));
    setText(lcFedFunds, fmtPct(rawRates.fed_funds_rate));
    setText(lcCurveSpread, rawRates.yield_curve_spread != null ? (rawRates.yield_curve_spread >= 0 ? '+' : '') + rawRates.yield_curve_spread.toFixed(3) + '%' : '\u2014');
    setBar(p1Bar, p1Score, (er.pillar_scores || {}).rates_policy_pressure);

    // Pillar 2 — Financial Conditions
    setText(lcVIX, fmtVal(rawCond.vix));
    var fciProxy = submetricScore(pillarDetails, 'financial_conditions_tightness', 'financial_conditions_index');
    setText(lcFCI, fciProxy != null ? Math.round(fciProxy) + '/100' : '\u2014');
    var condTrend = submetricScore(pillarDetails, 'financial_conditions_tightness', 'financial_conditions_trend');
    setText(lcCondTrend, condTrend != null ? (condTrend >= 60 ? 'Easing' : condTrend >= 40 ? 'Stable' : 'Tightening') : '\u2014');
    var broadTight = submetricScore(pillarDetails, 'financial_conditions_tightness', 'broad_tightness_score');
    setText(lcBroadTight, broadTight != null ? Math.round(broadTight) + '/100' : '\u2014');
    setBar(p2Bar, p2Score, (er.pillar_scores || {}).financial_conditions_tightness);

    // Pillar 3 — Credit & Funding
    setText(lcIGSpread, rawCredit.ig_spread != null ? rawCredit.ig_spread.toFixed(2) + '%' : '\u2014');
    setText(lcHYSpread, rawCredit.hy_spread != null ? rawCredit.hy_spread.toFixed(2) + '%' : '\u2014');
    var creditStressSub = submetricScore(pillarDetails, 'credit_funding_stress', 'credit_stress_signal');
    setText(lcCreditStress, creditStressSub != null ? Math.round(creditStressSub) + '/100' : '\u2014');
    var fundingSub = submetricScore(pillarDetails, 'credit_funding_stress', 'funding_stress_proxy');
    setText(lcFundingStress, fundingSub != null ? Math.round(fundingSub) + '/100' : '\u2014');
    setBar(p3Bar, p3Score, (er.pillar_scores || {}).credit_funding_stress);

    // Pillar 4 — Dollar & Global
    setText(lcDXY, rawDollar.dxy_level != null ? rawDollar.dxy_level.toFixed(1) : '\u2014');
    var dxyTrendSub = submetricScore(pillarDetails, 'dollar_global_liquidity', 'dxy_trend');
    setText(lcDXYTrend, dxyTrendSub != null ? (dxyTrendSub >= 60 ? 'Weakening' : dxyTrendSub >= 40 ? 'Stable' : 'Strengthening') : '\u2014');
    var dollarPressSub = submetricScore(pillarDetails, 'dollar_global_liquidity', 'dollar_liquidity_pressure');
    setText(lcDollarPress, dollarPressSub != null ? Math.round(dollarPressSub) + '/100' : '\u2014');
    var globalHead = submetricScore(pillarDetails, 'dollar_global_liquidity', 'global_liquidity_headwind');
    setText(lcGlobalHead, globalHead != null ? (globalHead >= 60 ? 'Minimal' : globalHead >= 35 ? 'Moderate' : 'Significant') : '\u2014');
    setBar(p4Bar, p4Score, (er.pillar_scores || {}).dollar_global_liquidity);

    // Pillar 5 — Stability & Fragility
    var stabSub = submetricScore(pillarDetails, 'liquidity_stability_fragility', 'stability_of_conditions');
    setText(lcStability, stabSub != null ? Math.round(stabSub) + '/100' : '\u2014');
    var contraSub = submetricScore(pillarDetails, 'liquidity_stability_fragility', 'contradiction_between_pillars');
    setText(lcContradiction, contraSub != null ? Math.round(contraSub) + '/100' : '\u2014');
    var fragSub = submetricScore(pillarDetails, 'liquidity_stability_fragility', 'fragility_penalty');
    setText(lcFragility, fragSub != null ? Math.round(fragSub) + '/100' : '\u2014');
    var sudSub = submetricScore(pillarDetails, 'liquidity_stability_fragility', 'sudden_stress_risk');
    setText(lcSuddenStress, sudSub != null ? Math.round(sudSub) + '/100' : '\u2014');
    setBar(p5Bar, p5Score, (er.pillar_scores || {}).liquidity_stability_fragility);

    // Pillar summary bars
    if (pillarBarsEl) {
      var ps = er.pillar_scores || {};
      var pw = er.pillar_weights || {};
      var names = {
        rates_policy_pressure: 'Rates & Policy Pressure',
        financial_conditions_tightness: 'Financial Conditions',
        credit_funding_stress: 'Credit & Funding Stress',
        dollar_global_liquidity: 'Dollar / Global Liquidity',
        liquidity_stability_fragility: 'Stability / Fragility'
      };
      var html = '';
      Object.keys(names).forEach(function(key) {
        var s = ps[key];
        var w = pw[key] ? Math.round(pw[key] * 100) : 0;
        var pct = s != null ? Math.round(s) : 0;
        html += '<div class="mod-bar-row">' +
          '<span class="mod-bar-label">' + escapeHtml(names[key]) + ' (' + w + '%)</span>' +
          '<div class="mod-bar-track"><div class="mod-bar-fill" style="width:' + pct + '%;background:' + scoreColor(s) + ';"></div></div>' +
          '<span class="mod-bar-val">' + (s != null ? pct : '\u2014') + '</span></div>';
      });
      pillarBarsEl.innerHTML = html;
    }

    // Data quality
    setText(confidenceScoreEl, er.confidence_score != null ? Math.round(er.confidence_score) + '/100' : '\u2014');
    setText(sigQualityEl, er.signal_quality || '\u2014');
    setText(missingCountEl, (er.missing_inputs || []).length);
    setText(warningCountEl, (er.warnings || []).length);

    // Warnings
    if (warningsEl) {
      var warns = er.warnings || [];
      if (warns.length === 0) {
        warningsEl.innerHTML = '<li class="mod-warning-item" style="opacity:0.5;">No warnings</li>';
      } else {
        warningsEl.innerHTML = warns.map(function(w) {
          return '<li class="mod-warning-item"><span class="mod-warning-icon">\u26A0</span>' + escapeHtml(w) + '</li>';
        }).join('');
      }
    }

    // Takeaway
    setText(takeawayEl, er.trader_takeaway);

    // Last updated
    if (lastUpdatedEl) {
      var asOf = er.as_of || payload.as_of;
      if (asOf) {
        try {
          lastUpdatedEl.textContent = 'Updated: ' + new Date(asOf).toLocaleTimeString();
        } catch (e) {
          lastUpdatedEl.textContent = 'Updated: ' + asOf;
        }
      }
    }
  }

  // ── Render Model Analysis ─────────────────────────────────────

  function renderModel(data) {
    if (_destroyed) return;
    var ma = data.model_analysis;
    if (!ma) {
      if (modelCta) { modelCta.style.display = ''; modelCta.textContent = data.error ? data.error.message : 'Model analysis unavailable.'; }
      if (modelDetailsRow) modelDetailsRow.style.display = 'none';
      return;
    }
    if (modelCta) modelCta.style.display = 'none';
    if (modelDetailsRow) modelDetailsRow.style.display = '';
    if (modelLabel) { modelLabel.textContent = ma.label || '\u2014'; modelLabel.className = 'mod-chip ' + chipClass(ma.label); }
    if (modelScore) { modelScore.textContent = ma.score != null ? Math.round(ma.score) : '\u2014'; modelScore.style.color = scoreColor(ma.score); }
    setText(modelSummary, ma.summary);

    if (modelPillars && ma.pillar_interpretation) {
      var html = '<div class="mod-section-title" style="margin-top:8px;">Pillar Interpretation</div>';
      var names = {
        rates_policy_pressure: 'Rates & Policy',
        financial_conditions_tightness: 'Financial Conditions',
        credit_funding_stress: 'Credit & Funding',
        dollar_global_liquidity: 'Dollar / Global',
        liquidity_stability_fragility: 'Stability / Fragility'
      };
      Object.keys(names).forEach(function(key) {
        var v = ma.pillar_interpretation[key];
        if (v) html += '<div style="margin-bottom:4px;"><strong>' + escapeHtml(names[key]) + ':</strong> ' + escapeHtml(v) + '</div>';
      });
      if (ma.trader_takeaway) {
        html += '<div class="mod-divider"></div><div class="mod-section-title">AI Takeaway</div><div>' + escapeHtml(ma.trader_takeaway) + '</div>';
      }
      modelPillars.innerHTML = html;
    }
  }

  // ── Fetch Logic ───────────────────────────────────────────────

  function fetchData(force) {
    var url = API_URL + (force ? '?force=true' : '');
    console.log('[BenTrade][Liquidity] fetch', url);
    fetch(url)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (_destroyed) return;
        render(data);
        if (_cache) _cache.setCache(CACHE_KEY, data);
      })
      .catch(function(err) {
        console.error('[BenTrade][Liquidity] fetch_error', err);
        if (degradedBanner) {
          degradedBanner.innerHTML = '&#9888; Failed to load liquidity data.';
          degradedBanner.style.display = '';
        }
      });
  }

  function fetchModel(force) {
    if (runModelBtn) runModelBtn.disabled = true;
    if (modelCta) { modelCta.style.display = ''; modelCta.textContent = 'Running model analysis\u2026'; }
    fetch(MODEL_URL + (force ? '?force=true' : ''), { method: 'POST' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (_destroyed) return;
        renderModel(data);
        if (_cache) _cache.setCache(MODEL_CACHE_KEY, data);
        if (runModelBtn) runModelBtn.disabled = false;
      })
      .catch(function(err) {
        console.error('[BenTrade][Liquidity] model_error', err);
        if (modelCta) { modelCta.style.display = ''; modelCta.textContent = 'Model analysis failed.'; }
        if (runModelBtn) runModelBtn.disabled = false;
      });
  }

  // ── Event Binding ─────────────────────────────────────────────

  if (refreshBtn) {
    refreshBtn.addEventListener('click', function() { fetchData(true); });
  }
  if (runModelBtn) {
    runModelBtn.addEventListener('click', function() { fetchModel(true); });
  }

  // ── Init ──────────────────────────────────────────────────────

  if (_cache && _cache.hasCache(CACHE_KEY)) {
    console.log('[BenTrade][Liquidity] cache_rehydrate route_entry');
    render(_cache.getCache(CACHE_KEY));
    if (_cache.hasCache(MODEL_CACHE_KEY)) {
      renderModel(_cache.getCache(MODEL_CACHE_KEY));
    }
  } else {
    fetchData(false);
  }

  return function cleanupLiquidityConditions() {
    _destroyed = true;
    console.log('[BenTrade][Liquidity] cleanup \u2014 DOM detached, cache preserved');
  };
};
