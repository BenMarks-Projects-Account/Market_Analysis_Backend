window.BenTradePages = window.BenTradePages || {};

/**
 * Volatility & Options Structure dashboard controller.
 *
 * Fetches from /api/volatility-options and populates all dynamic elements.
 * Uses BenTradeDashboardCache for sessionStorage-backed caching.
 */
window.BenTradePages.initVolatilityOptions = function initVolatilityOptions(rootEl) {
  var doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  var scope = rootEl || doc;
  var API_URL = '/api/volatility-options';
  var MODEL_URL = '/api/volatility-options/model';
  var CACHE_KEY = 'volatilityOptions';
  var MODEL_CACHE_KEY = 'volatilityOptionsModel';
  var REQUIRED_FIELDS = ['engine_result', 'engine_result.score'];
  var _cache = window.BenTradeDashboardCache;
  var _destroyed = false;

  // ── DOM refs ──────────────────────────────────────────────────
  var refreshBtn      = scope.querySelector('#volRefreshBtn');
  var lastUpdatedEl   = scope.querySelector('#volLastUpdated');
  var refreshOverlay  = scope.querySelector('#volRefreshOverlay');
  var refreshError    = scope.querySelector('#volRefreshError');
  // Hero
  var heroScore       = scope.querySelector('#volHeroScore');
  var heroLabel       = scope.querySelector('#volHeroLabel');
  var labelChip       = scope.querySelector('#volLabelChip');
  var summaryEl       = scope.querySelector('#volSummary');
  var premiumChip     = scope.querySelector('#volPremiumChip');
  var directionalChip = scope.querySelector('#volDirectionalChip');
  // Vol context
  var vixSpotEl       = scope.querySelector('#volVixSpot');
  var vixAvgEl        = scope.querySelector('#volVixAvg');
  var vixTrendEl      = scope.querySelector('#volVixTrend');
  var vvixEl          = scope.querySelector('#volVvix');
  var ivRankEl        = scope.querySelector('#volIVRank');
  var ivPctlEl        = scope.querySelector('#volIVPctl');
  var regimeExplEl    = scope.querySelector('#volRegimeExplanation');
  // Options posture
  var postureGauge    = scope.querySelector('#volPostureGauge');
  var postureBias     = scope.querySelector('#volPostureBias');
  var postureSummary  = scope.querySelector('#volPostureSummary');
  var signalChip      = scope.querySelector('#volSignalChip');
  var confidenceEl    = scope.querySelector('#volConfidence');
  // Component grid
  var frontEl         = scope.querySelector('#volFront');
  var nd2El           = scope.querySelector('#vol2nd');
  var rd3El           = scope.querySelector('#vol3rd');
  var termShapeEl     = scope.querySelector('#volTermShape');
  var structBar       = scope.querySelector('#volStructBar');
  var structScore     = scope.querySelector('#volStructScore');
  var iv30El          = scope.querySelector('#volIV30');
  var rv30El          = scope.querySelector('#volRV30');
  var ivrvSpreadEl    = scope.querySelector('#volIVRVSpread');
  var vrpEl           = scope.querySelector('#volVRP');
  var skewEl          = scope.querySelector('#volSkew');
  var putSkewEl       = scope.querySelector('#volPutSkew');
  var tailRiskEl      = scope.querySelector('#volTailRisk');
  var skewBar         = scope.querySelector('#volSkewBar');
  var skewScore       = scope.querySelector('#volSkewScore');
  var eqPCEl          = scope.querySelector('#volEqPC');
  var idxPCEl         = scope.querySelector('#volIdxPC');
  var richnessEl      = scope.querySelector('#volRichness');
  var posBar          = scope.querySelector('#volPosBar');
  var posScore        = scope.querySelector('#volPosScore');
  // Pillar bars
  var p1Bar = scope.querySelector('#volP1Bar'), p1Val = scope.querySelector('#volP1Val');
  var p2Bar = scope.querySelector('#volP2Bar'), p2Val = scope.querySelector('#volP2Val');
  var p3Bar = scope.querySelector('#volP3Bar'), p3Val = scope.querySelector('#volP3Val');
  var p4Bar = scope.querySelector('#volP4Bar'), p4Val = scope.querySelector('#volP4Val');
  var p5Bar = scope.querySelector('#volP5Bar'), p5Val = scope.querySelector('#volP5Val');
  // Strategy bars
  var s1Bar = scope.querySelector('#volS1Bar'), s1Val = scope.querySelector('#volS1Val');
  var s2Bar = scope.querySelector('#volS2Bar'), s2Val = scope.querySelector('#volS2Val');
  var s3Bar = scope.querySelector('#volS3Bar'), s3Val = scope.querySelector('#volS3Val');
  var s4Bar = scope.querySelector('#volS4Bar'), s4Val = scope.querySelector('#volS4Val');
  // Detail
  var detailPositive  = scope.querySelector('#volDetailPositive');
  var detailNegative  = scope.querySelector('#volDetailNegative');
  var detailConflicts = scope.querySelector('#volDetailConflicts');
  var takeawayEl      = scope.querySelector('#volTakeaway');
  var diagPre         = scope.querySelector('#volDiagPre');
  // Model
  var modelLabel      = scope.querySelector('#volModelLabel');
  var modelScore      = scope.querySelector('#volModelScore');
  var modelSummary    = scope.querySelector('#volModelSummary');
  var modelCta        = scope.querySelector('#volModelCta');
  var runModelBtn     = scope.querySelector('#volRunModelBtn');
  var modelDetailsRow = scope.querySelector('#volModelDetailsRow');
  var modelPillars    = scope.querySelector('#volModelPillars');
  var modelImplications = scope.querySelector('#volModelImplications');

  // ── Utilities ─────────────────────────────────────────────────

  function escapeHtml(val) {
    return String(val != null ? val : '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function setText(el, val) {
    if (el) el.textContent = (val != null ? val : '—');
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
    if (score == null) { barEl.style.width = '0%'; valEl.textContent = '—'; return; }
    var pct = Math.min(100, Math.max(0, Math.round(score)));
    barEl.style.width = pct + '%';
    barEl.style.background = scoreColor(score);
    valEl.textContent = pct;
  }

  function chipClass(label) {
    var l = (label || '').toLowerCase();
    if (l.indexOf('favored') >= 0 || l.indexOf('favorable') >= 0 || l.indexOf('constructive') >= 0) return 'mod-chip-bullish';
    if (l.indexOf('stress') >= 0 || l.indexOf('defensive') >= 0 || l.indexOf('elevated') >= 0) return 'mod-chip-bearish';
    if (l.indexOf('mixed') >= 0 || l.indexOf('fragile') >= 0) return 'mod-chip-neutral';
    return 'mod-chip-calm';
  }

  function signalChipClass(quality) {
    if (quality === 'high') return 'mod-signal-high';
    if (quality === 'medium') return 'mod-signal-medium';
    return 'mod-signal-low';
  }

  function fmtFixed(val, decimals) {
    if (val == null) return '—';
    return Number(val).toFixed(decimals != null ? decimals : 1);
  }

  function fmtPct(val) {
    if (val == null) return '—';
    return fmtFixed(val, 1) + '%';
  }

  /**
   * Show a metric value, or "—" with a title tooltip explaining why unavailable.
   * @param {Element} el - DOM element to update
   * @param {string} text - display text (or "—")
   * @param {Object} avail - metric_availability entry {status, reason}
   */
  function setWithAvailability(el, text, avail) {
    if (!el) return;
    el.textContent = text;
    if (text === '—' && avail && avail.status === 'unavailable' && avail.reason) {
      el.title = avail.reason;
      el.style.cursor = 'help';
    } else {
      el.title = '';
      el.style.cursor = '';
    }
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

  function renderWarningList(el, items) {
    if (!el) return;
    if (!items || items.length === 0) {
      el.innerHTML = '<li class="mod-warning-item" style="opacity:0.5;">None</li>';
      return;
    }
    el.innerHTML = items.map(function(item) {
      return '<li class="mod-warning-item"><span class="mod-warning-icon">⚠</span>' +
        escapeHtml(item) + '</li>';
    }).join('');
  }

  // ── Render ────────────────────────────────────────────────────

  function render(payload) {
    if (_destroyed) return;
    var eng = payload.engine_result || {};
    var raw = eng.raw_inputs || {};
    var pillars = eng.pillar_scores || {};
    var strategies = eng.strategy_scores || {};
    var regime = raw.regime || {};
    var structure = raw.structure || {};
    var skew = raw.skew || {};
    var positioning = raw.positioning || {};
    var dq = payload.data_quality || {};
    var ma = dq.metric_availability || {};

    // Hero
    setText(heroScore, eng.score != null ? Math.round(eng.score) : '—');
    setText(heroLabel, (eng.label || '—').toUpperCase());
    if (heroLabel) heroLabel.style.color = scoreColor(eng.score);
    setText(summaryEl, eng.summary);
    if (labelChip) {
      labelChip.textContent = eng.short_label || '—';
      labelChip.className = 'mod-chip ' + chipClass(eng.label);
    }

    // Strategy chips
    var psScore = strategies.premium_selling ? strategies.premium_selling.score : null;
    var dirScore = strategies.directional ? strategies.directional.score : null;
    if (premiumChip) {
      premiumChip.textContent = 'Premium Selling ' + (psScore != null ? Math.round(psScore) : '—');
      premiumChip.className = 'mod-chip ' + (psScore != null && psScore >= 65 ? 'mod-chip-bullish' : psScore != null && psScore >= 45 ? 'mod-chip-neutral' : 'mod-chip-bearish');
    }
    if (directionalChip) {
      directionalChip.textContent = 'Directional ' + (dirScore != null ? Math.round(dirScore) : '—');
      directionalChip.className = 'mod-chip ' + (dirScore != null && dirScore >= 65 ? 'mod-chip-bullish' : dirScore != null && dirScore >= 45 ? 'mod-chip-neutral' : 'mod-chip-bearish');
    }

    // Vol context card
    setWithAvailability(vixSpotEl, fmtFixed(regime.vix_spot, 1), ma.vix_spot);
    setWithAvailability(vixAvgEl, fmtFixed(regime.vix_avg_20d, 1), ma.vix_avg_20d);
    if (vixTrendEl && regime.vix_spot != null && regime.vix_avg_20d != null) {
      var pctChg = ((regime.vix_spot - regime.vix_avg_20d) / regime.vix_avg_20d * 100);
      vixTrendEl.textContent = (pctChg <= 0 ? '▼ ' : '▲ ') + Math.abs(pctChg).toFixed(1) + '% vs avg';
      vixTrendEl.style.color = pctChg <= 0 ? '#00e676' : 'rgba(255,79,102,0.9)';
      vixTrendEl.title = '';
    } else {
      setWithAvailability(vixTrendEl, '—', ma.vix_avg_20d);
    }
    setWithAvailability(vvixEl, fmtFixed(regime.vvix, 1), ma.vvix);
    
    // VIX Rank (PROXY: VIX used as index-level IV proxy)
    if (ivRankEl && regime.vix_rank_30d != null) {
      var vr = regime.vix_rank_30d;
      ivRankEl.textContent = fmtFixed(vr, 0) + '%';
      if (!ivRankEl.title) ivRankEl.title = 'VIX Rank (30d) PROXY: VIX futures-free index used as approximation of implied volatility rank. Not true option IV rank.';
    } else {
      setWithAvailability(ivRankEl, '—', ma.vix_rank_30d);
    }
    
    // VIX Percentile (PROXY: VIX used as index-level IV proxy)
    if (ivPctlEl && regime.vix_percentile_1y != null) {
      var vp = regime.vix_percentile_1y;
      ivPctlEl.textContent = fmtFixed(vp, 0) + '%';
      if (!ivPctlEl.title) ivPctlEl.title = 'VIX Percentile (1Y) PROXY: VIX futures-free index used as approximation of implied volatility percentile. Not true option IV percentile.';
    } else {
      setWithAvailability(ivPctlEl, '—', ma.vix_percentile_1y);
    }
    setText(regimeExplEl, eng.pillar_explanations ? eng.pillar_explanations.volatility_regime : '—');

    // Options posture card
    var posBias = positioning.premium_bias;
    if (postureBias) {
      var biasLabel = posBias != null ? (posBias > 20 ? 'SELL' : posBias < -20 ? 'BUY' : 'NEUTRAL') : '—';
      postureBias.textContent = biasLabel;
      postureBias.style.color = biasLabel === 'SELL' ? '#00e676' : biasLabel === 'BUY' ? 'rgba(255,79,102,0.9)' : '#888';
    }
    if (postureGauge) {
      var gc = posBias != null ? (posBias > 20 ? 'rgba(0,230,118,0.3)' : posBias < -20 ? 'rgba(255,79,102,0.3)' : 'rgba(136,136,136,0.3)') : 'rgba(136,136,136,0.3)';
      postureGauge.style.borderColor = gc;
    }
    setText(postureSummary, eng.pillar_explanations ? eng.pillar_explanations.positioning_options_posture : '—');
    if (signalChip) {
      var sq = eng.signal_quality || 'low';
      signalChip.textContent = sq.toUpperCase() + ' SUITABILITY';
      signalChip.className = 'mod-signal-chip ' + signalChipClass(sq);
    }
    setText(confidenceEl, eng.confidence_score != null ? Math.round(eng.confidence_score) + '/100' : '—');

    // Component grid — term structure
    setText(frontEl, fmtFixed(structure.vix_front_month, 1));
    setText(nd2El, fmtFixed(structure.vix_2nd_month, 1));
    setText(rd3El, fmtFixed(structure.vix_3rd_month, 1));
    if (termShapeEl && structure.vix_front_month != null && structure.vix_2nd_month != null) {
      var isContango = structure.vix_2nd_month >= structure.vix_front_month;
      termShapeEl.textContent = isContango ? 'Contango' : 'Backwardation';
      termShapeEl.style.color = isContango ? '#00e676' : 'rgba(255,79,102,0.9)';
    } else {
      setText(termShapeEl, '—');
    }
    setBar(structBar, structScore, pillars.volatility_structure);

    // IV vs RV
    setWithAvailability(iv30El, structure.iv_30d != null ? fmtPct(structure.iv_30d) : '—', ma.iv_30d);
    setWithAvailability(rv30El, structure.rv_30d != null ? fmtPct(structure.rv_30d) : '—', ma.rv_30d);
    if (ivrvSpreadEl && structure.iv_30d != null && structure.rv_30d != null) {
      var spread = structure.iv_30d - structure.rv_30d;
      ivrvSpreadEl.textContent = (spread >= 0 ? '+' : '') + fmtPct(spread);
      ivrvSpreadEl.style.color = spread >= 0 ? '#00e676' : 'rgba(255,79,102,0.9)';
    } else {
      setText(ivrvSpreadEl, '—');
    }
    if (vrpEl && structure.iv_30d != null && structure.rv_30d != null) {
      vrpEl.textContent = structure.iv_30d > structure.rv_30d ? 'Positive' : 'Negative';
      vrpEl.style.color = structure.iv_30d > structure.rv_30d ? '#00e676' : 'rgba(255,79,102,0.9)';
    } else {
      setText(vrpEl, '—');
    }

    // Skew
    setWithAvailability(skewEl, fmtFixed(skew.cboe_skew, 0), ma.cboe_skew);
    setWithAvailability(putSkewEl, skew.put_skew_25d != null ? fmtPct(skew.put_skew_25d) : '—', ma.put_skew_25d);
    
    // Tail Risk Signal (now includes label: "Low", "Moderate", "Elevated", "High")
    if (tailRiskEl) {
      if (skew.tail_risk_signal != null) {
        // tail_risk_signal is now a label: "Low"|"Moderate"|"Elevated"|"High"
        // tail_risk_numeric is the 0-100 value for gradient coloring
        var tLabel = skew.tail_risk_signal;
        var tNumeric = skew.tail_risk_numeric;
        tailRiskEl.textContent = tLabel;
        tailRiskEl.style.color = !tNumeric ? '#888' :
          tNumeric <= 30 ? '#00e676' :
          tNumeric <= 60 ? 'var(--warn, #ffab40)' :
          'rgba(255,79,102,0.9)';
        tailRiskEl.title = 'Tail Risk: ' + tLabel + ' (based on put skew and CBOE SKEW)';
      } else {
        setWithAvailability(tailRiskEl, '—', ma.tail_risk_signal);
      }
    }
    setBar(skewBar, skewScore, pillars.tail_risk_skew);

    // P/C Ratios
    setWithAvailability(eqPCEl, fmtFixed(positioning.equity_pc_ratio, 2), ma.equity_pc_ratio);
    
    // SPY P/C Proxy (PROXY: SPY options used as index proxy due to lack of broader index feed)
    if (idxPCEl) {
      var spy_pc = positioning.spy_pc_ratio_proxy;
      if (spy_pc != null) {
        idxPCEl.textContent = fmtFixed(spy_pc, 2);
        idxPCEl.title = 'SPY P/C Proxy: Using SPY options as index-level proxy due to lack of dedicated index options feed.';
      } else {
        setWithAvailability(idxPCEl, '—', ma.spy_pc_ratio_proxy);
      }
    }
    
    // Option Richness (blended logic: Rich if vix_rank>60 AND iv>rv, Cheap if vix_rank<30 OR iv≤rv, else Fair)
    if (richnessEl) {
      var label = positioning.option_richness_label;  // "Rich"|"Fair"|"Cheap" from provider
      var numeric = positioning.option_richness;       // 0-100 for backward compat
      if (label || numeric != null) {
        richnessEl.textContent = label || (numeric < 30 ? 'Cheap' : numeric < 60 ? 'Fair' : 'Rich');
        richnessEl.title = 'Option Richness (blended): Combines VIX rank context with IV-RV spread for comprehensive richness assessment.';
      } else {
        setWithAvailability(richnessEl, '—', ma.option_richness);
      }
    }
    setBar(posBar, posScore, pillars.positioning_options_posture);

    // Pillar bars
    setBar(p1Bar, p1Val, pillars.volatility_regime);
    setBar(p2Bar, p2Val, pillars.volatility_structure);
    setBar(p3Bar, p3Val, pillars.tail_risk_skew);
    setBar(p4Bar, p4Val, pillars.positioning_options_posture);
    setBar(p5Bar, p5Val, pillars.strategy_suitability);

    // Strategy bars
    setBar(s1Bar, s1Val, strategies.premium_selling ? strategies.premium_selling.score : null);
    setBar(s2Bar, s2Val, strategies.directional ? strategies.directional.score : null);
    setBar(s3Bar, s3Val, strategies.vol_structure_plays ? strategies.vol_structure_plays.score : null);
    setBar(s4Bar, s4Val, strategies.hedging ? strategies.hedging.score : null);

    // Detail analysis
    renderList(detailPositive, eng.positive_contributors, 'positive');
    renderWarningList(detailNegative, eng.negative_contributors);
    renderList(detailConflicts, eng.conflicting_signals, 'conflict');
    setText(takeawayEl, eng.trader_takeaway);

    // Diagnostics
    if (diagPre && eng.diagnostics) {
      diagPre.textContent = JSON.stringify(eng.diagnostics, null, 2);
    }

    // Timestamp
    if (lastUpdatedEl) {
      var ts = payload.as_of || eng.as_of;
      lastUpdatedEl.textContent = ts ? 'Updated: ' + new Date(ts).toLocaleTimeString() : 'Updated: —';
    }
  }

  // ── Model Analysis ────────────────────────────────────────────

  function setModelBtnState(loading) {
    runModelBtn = scope.querySelector('#volRunModelBtn');
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
        '<div class="mod-model-cta" id="volModelCta">' +
        '<p style="opacity:0.6;font-size:12px;margin:0 0 10px;">Model analysis has not been run yet.</p>' +
        '<button class="mod-action-btn" id="volRunModelBtn" type="button">Run Model Analysis</button>' +
        '</div>';
      var btn = modelSummary.querySelector('#volRunModelBtn');
      if (btn) btn.addEventListener('click', function() { triggerModelAnalysis(); });
    }
    setText(modelLabel, '—');
    setText(modelScore, '—');
    if (modelDetailsRow) modelDetailsRow.style.display = 'none';
  }

  function renderModelError(errMsg) {
    if (modelSummary) {
      modelSummary.innerHTML =
        '<div style="color:rgba(255,79,102,0.9);font-size:12px;margin-bottom:8px;">' +
        escapeHtml(errMsg) + '</div>' +
        '<button class="mod-action-btn" id="volRunModelBtn" type="button">Retry Model Analysis</button>';
      var btn = modelSummary.querySelector('#volRunModelBtn');
      if (btn) btn.addEventListener('click', function() { triggerModelAnalysis(); });
    }
    setText(modelLabel, 'Error');
    if (modelLabel) modelLabel.style.color = 'rgba(255,79,102,0.9)';
    setText(modelScore, '—');
    if (modelDetailsRow) modelDetailsRow.style.display = 'none';
  }

  function renderModel(model) {
    if (!model) { renderModelNotRun(); return; }
    console.log('[BenTrade][Volatility] Rendering model result:', model.label, model.score);

    // Label & score
    setText(modelLabel, (model.label || '—').toUpperCase());
    if (modelLabel) modelLabel.style.color = scoreColor(model.score);
    setText(modelScore, model.score != null ? Math.round(model.score) : '—');
    if (modelScore) modelScore.style.color = scoreColor(model.score);

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

      var vd = model.vol_drivers || {};
      if (vd.favorable_factors && vd.favorable_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">FAVORABLE</span><ul class="mod-contrib-list">';
        vd.favorable_factors.forEach(function(f) {
          html += '<li class="mod-contrib-item"><span class="mod-contrib-dot positive"></span>' + escapeHtml(f) + '</li>';
        });
        html += '</ul></div>';
      }
      if (vd.warning_factors && vd.warning_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">WARNINGS</span><ul class="mod-contrib-list">';
        vd.warning_factors.forEach(function(f) {
          html += '<li class="mod-contrib-item"><span class="mod-contrib-dot negative"></span>' + escapeHtml(f) + '</li>';
        });
        html += '</ul></div>';
      }
      if (vd.conflicting_factors && vd.conflicting_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">CONFLICTING</span><ul class="mod-contrib-list">';
        vd.conflicting_factors.forEach(function(f) {
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
        '<button class="mod-action-btn" id="volRunModelBtn" type="button">Re-run Model Analysis</button></div>';

      modelSummary.innerHTML = html;
      var btn = modelSummary.querySelector('#volRunModelBtn');
      if (btn) btn.addEventListener('click', function() { triggerModelAnalysis(); });
    }

    if (modelDetailsRow) modelDetailsRow.style.display = '';
    if (modelPillars) {
      var pa = model.pillar_analysis || {};
      var pillarKeys = ['volatility_regime', 'volatility_structure', 'tail_risk_skew', 'positioning', 'strategy_suitability'];
      var pillarLabels = {
        volatility_regime: 'Volatility Regime', volatility_structure: 'Vol Structure',
        tail_risk_skew: 'Tail Risk / Skew', positioning: 'Positioning',
        strategy_suitability: 'Strategy Suitability'
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

    if (modelImplications) {
      var si = model.strategy_implications || {};
      var implKeys = ['premium_selling', 'directional', 'vol_structure', 'hedging', 'position_sizing', 'risk_level'];
      var implLabels = {
        premium_selling: 'Premium Selling', directional: 'Directional',
        vol_structure: 'Vol Structure Plays', hedging: 'Hedging',
        position_sizing: 'Position Sizing', risk_level: 'Risk Level'
      };
      var implHtml = '';
      implKeys.forEach(function(k) {
        var val = si[k];
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
    console.log('[BenTrade][Volatility] Triggering model analysis…');
    if (modelSummary) {
      modelSummary.innerHTML =
        '<div style="text-align:center;padding:18px 0;">' +
        '<div style="display:inline-block;width:22px;height:22px;border-radius:50%;' +
        'border:2px solid rgba(0,234,255,0.15);border-top-color:rgba(0,234,255,0.9);' +
        'animation:btnInlineSpin 0.8s linear infinite;margin-bottom:8px;"></div>' +
        '<div style="font-size:11px;opacity:0.7;">Running model analysis… Interpreting volatility conditions.</div></div>';
    }
    setModelBtnState(true);
    var CLIENT_TIMEOUT = (window.BenTradeApi && window.BenTradeApi.MODEL_TIMEOUT_MS) || 185000;
    var t0 = performance.now();
    var controller = new AbortController();
    var timerFired = false;
    var timer = setTimeout(function() {
      timerFired = true;
      controller.abort();
    }, CLIENT_TIMEOUT);
    fetch(MODEL_URL, { method: 'POST', signal: controller.signal })
      .then(function(resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .then(function(result) {
        if (_destroyed) return;
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
        var msg;
        if (err.name === 'AbortError') {
          msg = 'Model request timed out after ' + Math.round(CLIENT_TIMEOUT / 1000) + 's. Is the local LLM running?';
        } else {
          msg = String(err.message || 'Model analysis failed');
        }
        renderModelError(msg);
      })
      .finally(function() {
        clearTimeout(timer);
        setModelBtnState(false);
      });
  }

  // ── Error state ───────────────────────────────────────────────

  function renderErrorState(errMsg) {
    setText(heroLabel, 'ERROR');
    if (heroLabel) heroLabel.style.color = 'rgba(255,79,102,0.9)';
    setText(heroScore, '—');
    setText(summaryEl, errMsg);
    if (labelChip) { labelChip.textContent = 'Error'; labelChip.className = 'mod-chip mod-chip-bearish'; }
    if (lastUpdatedEl) lastUpdatedEl.textContent = 'Error: ' + errMsg;
  }

  // ── Refresh overlay / error ───────────────────────────────────

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

  function showRefreshOverlay(show) {
    if (refreshOverlay) refreshOverlay.style.display = show ? '' : 'none';
  }

  function showRefreshError(errMsg) {
    if (!refreshError) return;
    if (!errMsg) { refreshError.style.display = 'none'; return; }
    refreshError.style.display = '';
    refreshError.innerHTML = '<span>⚠ Refresh failed: ' + escapeHtml(errMsg) + ' — showing cached data</span>' +
      '<button type="button" style="margin-left:auto;background:none;border:none;' +
      'color:rgba(255,79,102,0.7);cursor:pointer;font-size:14px;" onclick="this.parentElement.style.display=\'none\'">✕</button>';
  }

  function restoreModelState() {
    var modelData = _cache ? _cache.getData(MODEL_CACHE_KEY) : null;
    if (modelData) {
      renderModel(modelData);
    } else {
      renderModelNotRun();
    }
  }

  // ── Fetch ─────────────────────────────────────────────────────

  function fetchData(force) {
    if (_destroyed) return;

    var hasCached = _cache && _cache.hasCache(CACHE_KEY);

    if (!force && hasCached) {
      var cachedData = _cache.getData(CACHE_KEY);
      console.log('[BenTrade][Volatility] cache_rehydrate score=%s',
        cachedData && cachedData.engine_result ? cachedData.engine_result.score : '?');
      render(cachedData);
      restoreModelState();
      return;
    }

    if (force && hasCached) {
      showRefreshOverlay(true);
      showRefreshError(null);
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
        if (_cache) {
          var wrote = _cache.setSafe(CACHE_KEY, data, REQUIRED_FIELDS);
          if (!wrote) {
            if (hasCached) { showRefreshError('Invalid response from server'); }
            else { renderErrorState('Invalid response (missing required fields)'); }
            return;
          }
        }
        console.log('[BenTrade][Volatility] %s success score=%s',
          force ? 'refresh' : 'first_load',
          data.engine_result ? data.engine_result.score : '?');
        render(data);
        restoreModelState();
        showRefreshError(null);
      })
      .catch(function(err) {
        if (_destroyed) return;
        var msg = err.message || 'Failed to load volatility data';
        if (hasCached) {
          showRefreshError(msg);
          if (_cache) _cache.setError(CACHE_KEY, msg);
        } else {
          renderErrorState(msg);
          renderModelNotRun();
        }
      })
      .finally(function() {
        if (_cache) _cache.setRefreshing(CACHE_KEY, false);
        setRefreshBtnState(false);
        showRefreshOverlay(false);
      });
  }

  // ── Init ──────────────────────────────────────────────────────
  if (refreshBtn) refreshBtn.addEventListener('click', function() { fetchData(true); });
  if (runModelBtn) runModelBtn.addEventListener('click', function() { triggerModelAnalysis(); });

  fetchData(false);

  // ── Cleanup ───────────────────────────────────────────────────
  return function cleanupVolatilityOptions() {
    _destroyed = true;
    showRefreshOverlay(false);
    console.log('[BenTrade][Volatility] cleanup — DOM detached, cache preserved');
  };
};
