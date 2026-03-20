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
  var modelImplications = scope.querySelector('#lcModelImplications');

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
    console.log('[BenTrade][Liquidity] render_start', {
      score: er.score, label: er.short_label, confidence: er.confidence_score,
      pillar_count: er.pillar_scores ? Object.keys(er.pillar_scores).length : 0,
      warnings: (er.warnings || []).length, missing: (er.missing_inputs || []).length,
    });

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
    var fciProxy = submetricScore(pillarDetails, 'financial_conditions_tightness', 'fci_proxy');
    setText(lcFCI, fciProxy != null ? Math.round(fciProxy) + '/100' : '\u2014');
    var condTrend = submetricScore(pillarDetails, 'financial_conditions_tightness', 'conditions_supportiveness');
    setText(lcCondTrend, condTrend != null ? (condTrend >= 60 ? 'Supportive' : condTrend >= 40 ? 'Neutral' : 'Restrictive') : '\u2014');
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
    var dxyLevelSub = submetricScore(pillarDetails, 'dollar_global_liquidity', 'dxy_level');
    setText(lcDXYTrend, dxyLevelSub != null ? (dxyLevelSub >= 65 ? 'Weak $' : dxyLevelSub >= 35 ? 'Moderate' : 'Strong $') : '\u2014');
    var dollarPressSub = submetricScore(pillarDetails, 'dollar_global_liquidity', 'dollar_liquidity_pressure');
    setText(lcDollarPress, dollarPressSub != null ? Math.round(dollarPressSub) + '/100' : '\u2014');
    var globalHead = submetricScore(pillarDetails, 'dollar_global_liquidity', 'dollar_risk_asset_impact');
    setText(lcGlobalHead, globalHead != null ? (globalHead >= 60 ? 'Minimal' : globalHead >= 35 ? 'Moderate' : 'Significant') : '\u2014');
    setBar(p4Bar, p4Score, (er.pillar_scores || {}).dollar_global_liquidity);

    // Diagnostic: log resolved submetric values for key fields
    console.log('[BenTrade][Liquidity] submetric_resolution', {
      fci_proxy: fciProxy, conditions_trend: condTrend, broad_tightness: broadTight,
      dxy_level: dxyLevelSub, dollar_pressure: dollarPressSub, headwind: globalHead,
    });

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

  function setModelBtnState(loading) {
    runModelBtn = scope.querySelector('#lcRunModelBtn');
    if (!runModelBtn) return;
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
    if (modelSummary) {
      modelSummary.innerHTML =
        '<div class="mod-model-cta" id="lcModelCta">' +
        '<p style="opacity:0.6;font-size:12px;margin:0 0 10px;">Model analysis has not been run yet.</p>' +
        '<button class="mod-action-btn" id="lcRunModelBtn" type="button">Run Model Analysis</button>' +
        '</div>';
      var btn = modelSummary.querySelector('#lcRunModelBtn');
      if (btn) btn.addEventListener('click', function() { triggerModelAnalysis(); });
    }
    setText(modelLabel, '—');
    setText(modelScore, '—');
    if (modelDetailsRow) modelDetailsRow.style.display = 'none';
  }

  function renderModelError(errMsg) {
    console.error('[BenTrade][Liquidity] Model analysis error:', errMsg);
    if (modelSummary) {
      modelSummary.innerHTML =
        '<div style="color:rgba(255,79,102,0.9);font-size:12px;margin-bottom:8px;">' +
        escapeHtml(errMsg) + '</div>' +
        '<button class="mod-action-btn" id="lcRunModelBtn" type="button">Retry Model Analysis</button>';
      var btn = modelSummary.querySelector('#lcRunModelBtn');
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
    console.log('[BenTrade][Liquidity] Rendering model result:', model.label, model.score);

    // Label & score
    setText(modelLabel, (model.label || '—').toUpperCase());
    if (modelLabel) modelLabel.style.color = scoreColor(model.score);
    setText(modelScore, model.score != null ? Math.round(model.score) : '—');
    if (modelScore) modelScore.style.color = scoreColor(model.score);

    // Summary with confidence/drivers
    if (modelSummary) {
      var summaryText = model.summary || '';
      if (summaryText.charAt(0) === '{') {
        try { var sp = JSON.parse(summaryText); summaryText = sp.summary || sp.executive_summary || summaryText; } catch(_e) {
          var sm = summaryText.match(/"summary"\s*:\s*"((?:[^"\\]|\\.)*)"/);
          if (sm && sm[1]) summaryText = sm[1];
        }
      }
      var html = '<div style="font-size:12px;line-height:1.6;margin-bottom:10px;">' +
        escapeHtml(summaryText) + '</div>';

      html += '<div style="font-size:11px;opacity:0.7;margin-bottom:8px;">Confidence: ' +
        (model.confidence != null ? (model.confidence * 100).toFixed(0) + '%' : '—') + '</div>';

      var ld = model.liquidity_drivers || {};
      if (ld.constructive_factors && ld.constructive_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">CONSTRUCTIVE</span><ul class="mod-contrib-list">';
        ld.constructive_factors.forEach(function(f) {
          html += '<li class="mod-contrib-item"><span class="mod-contrib-dot positive"></span>' + escapeHtml(f) + '</li>';
        });
        html += '</ul></div>';
      }
      if (ld.warning_factors && ld.warning_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">WARNINGS</span><ul class="mod-contrib-list">';
        ld.warning_factors.forEach(function(f) {
          html += '<li class="mod-contrib-item"><span class="mod-contrib-dot negative"></span>' + escapeHtml(f) + '</li>';
        });
        html += '</ul></div>';
      }
      if (ld.conflicting_factors && ld.conflicting_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">CONFLICTING</span><ul class="mod-contrib-list">';
        ld.conflicting_factors.forEach(function(f) {
          html += '<li class="mod-contrib-item"><span class="mod-contrib-dot conflict"></span>' + escapeHtml(f) + '</li>';
        });
        html += '</ul></div>';
      }

      if (model.trader_takeaway) {
        html += '<div class="mod-divider"></div>' +
          '<div style="font-size:11px;font-weight:600;margin-bottom:4px;opacity:0.6;">TRADER TAKEAWAY</div>' +
          '<div style="font-size:12px;line-height:1.5;">' + escapeHtml(model.trader_takeaway) + '</div>';
      }

      var uf = model.uncertainty_flags || [];
      if (uf.length > 0) {
        html += '<div style="margin-top:8px;">';
        uf.forEach(function(f) {
          html += '<div style="font-size:10px;opacity:0.5;">⚠ ' + escapeHtml(f) + '</div>';
        });
        html += '</div>';
      }

      html += '<div style="margin-top:12px;">' +
        '<button class="mod-action-btn" id="lcRunModelBtn" type="button">Re-run Model Analysis</button></div>';

      modelSummary.innerHTML = html;
      var btn = modelSummary.querySelector('#lcRunModelBtn');
      if (btn) btn.addEventListener('click', function() { triggerModelAnalysis(); });
    }

    // Model detail row — pillar analysis
    if (modelDetailsRow) modelDetailsRow.style.display = '';
    if (modelPillars) {
      var pa = model.pillar_analysis || model.pillar_interpretation || {};
      var pillarKeys = ['rates_policy_pressure', 'financial_conditions_tightness', 'credit_funding_stress', 'dollar_global_liquidity', 'liquidity_stability_fragility'];
      var pillarLabels = {
        rates_policy_pressure: 'Rates & Policy',
        financial_conditions_tightness: 'Financial Conditions',
        credit_funding_stress: 'Credit & Funding',
        dollar_global_liquidity: 'Dollar / Global',
        liquidity_stability_fragility: 'Stability / Fragility'
      };
      var pillarsHtml = '';
      pillarKeys.forEach(function(k) {
        var val = pa[k];
        if (val) {
          pillarsHtml += '<div style="margin-bottom:8px;">' +
            '<div style="font-size:10px;font-weight:600;opacity:0.6;text-transform:uppercase;">' +
            escapeHtml(pillarLabels[k] || k) + '</div>' +
            '<div style="font-size:11px;line-height:1.5;">' + escapeHtml(val) + '</div></div>';
        }
      });
      modelPillars.innerHTML = pillarsHtml || '<div style="opacity:0.5;font-size:11px;">No pillar analysis available</div>';
    }

    // Model detail row — trading implications
    if (modelImplications) {
      var mi = model.market_implications || {};
      var implKeys = ['directional_bias', 'position_sizing', 'strategy_recommendation', 'risk_level', 'liquidity_tilt'];
      var implLabels = {
        directional_bias: 'Directional Bias', position_sizing: 'Position Sizing',
        strategy_recommendation: 'Strategy Recommendation', risk_level: 'Risk Level',
        liquidity_tilt: 'Liquidity Tilt'
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

  // ── Fetch Logic ───────────────────────────────────────────────

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

  function fetchData(force) {
    var url = API_URL + (force ? '?force=true' : '');
    console.log('[BenTrade][Liquidity] fetch', url);
    if (force) setRefreshBtnState(true);
    fetch(url)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (_destroyed) return;
        render(data);
        if (_cache) _cache.set(CACHE_KEY, data);
      })
      .catch(function(err) {
        console.error('[BenTrade][Liquidity] fetch_error', err);
        if (degradedBanner) {
          degradedBanner.innerHTML = '&#9888; Failed to load liquidity data.';
          degradedBanner.style.display = '';
        }
      })
      .finally(function() {
        setRefreshBtnState(false);
      });
  }

  function triggerModelAnalysis() {
    if (_destroyed) return;
    console.log('[BenTrade][Liquidity] Triggering model analysis…');
    if (modelSummary) {
      modelSummary.innerHTML =
        '<div style="text-align:center;padding:18px 0;">' +
        '<div style="display:inline-block;width:22px;height:22px;border-radius:50%;' +
        'border:2px solid rgba(0,234,255,0.15);border-top-color:rgba(0,234,255,0.9);' +
        'animation:btnInlineSpin 0.8s linear infinite;margin-bottom:8px;"></div>' +
        '<div style="font-size:11px;opacity:0.7;">Running model analysis… Interpreting liquidity conditions.</div></div>';
    }
    setModelBtnState(true);
    var CLIENT_TIMEOUT = (window.BenTradeApi && window.BenTradeApi.MODEL_TIMEOUT_MS) || 185000;
    var t0 = performance.now();
    console.log('[LIQ_MODEL] request_start', {
      endpoint: MODEL_URL, method: 'POST',
      timeout_ms: CLIENT_TIMEOUT,
      timestamp: new Date().toISOString(),
    });
    var controller = new AbortController();
    var timerFired = false;
    var timer = setTimeout(function() {
      timerFired = true;
      console.warn('[LIQ_MODEL] abort_timer_fired', {
        elapsed_ms: Math.round(performance.now() - t0),
        timeout_ms: CLIENT_TIMEOUT,
      });
      controller.abort();
    }, CLIENT_TIMEOUT);
    fetch(MODEL_URL, { method: 'POST', signal: controller.signal })
      .then(function(resp) {
        console.log('[LIQ_MODEL] response_headers', {
          status: resp.status, ok: resp.ok,
          elapsed_ms: Math.round(performance.now() - t0),
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .then(function(result) {
        if (_destroyed) return;
        console.log('[LIQ_MODEL] body_parsed', {
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
        console.error('[LIQ_MODEL] failure', { error: err.message, name: err.name, elapsed_ms: elapsed, timerFired: timerFired });
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
        console.log('[LIQ_MODEL] lifecycle_complete', { total_ms: Math.round(performance.now() - t0) });
      });
  }

  // ── Event Binding ─────────────────────────────────────────────

  if (refreshBtn) {
    refreshBtn.addEventListener('click', function() { fetchData(true); });
  }
  if (runModelBtn) {
    runModelBtn.addEventListener('click', function() { triggerModelAnalysis(); });
  }

  // ── Init ──────────────────────────────────────────────────────

  if (_cache && _cache.hasCache(CACHE_KEY)) {
    console.log('[BenTrade][Liquidity] cache_rehydrate route_entry');
    render(_cache.getData(CACHE_KEY));
    if (_cache.hasCache(MODEL_CACHE_KEY)) {
      renderModel(_cache.getData(MODEL_CACHE_KEY));
    }
  } else {
    fetchData(false);
  }

  return function cleanupLiquidityConditions() {
    _destroyed = true;
    console.log('[BenTrade][Liquidity] cleanup \u2014 DOM detached, cache preserved');
  };
};
