window.BenTradePages = window.BenTradePages || {};

/**
 * Flows & Positioning dashboard controller.
 *
 * Fetches from /api/flows-positioning and populates dynamic elements.
 * Uses BenTradeDashboardCache for sessionStorage-backed caching.
 */
window.BenTradePages.initFlowsPositioning = function initFlowsPositioning(rootEl) {
  var doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  var scope = rootEl || doc;
  var API_URL = '/api/flows-positioning';
  var MODEL_URL = '/api/flows-positioning/model';
  var CACHE_KEY = 'flowsPositioning';
  var MODEL_CACHE_KEY = 'flowsPositioningModel';
  var _cache = window.BenTradeDashboardCache;
  var _destroyed = false;

  // ── DOM refs ──────────────────────────────────────────────────
  var refreshBtn       = scope.querySelector('#fpRefreshBtn');
  var lastUpdatedEl    = scope.querySelector('#fpLastUpdated');
  var degradedBanner   = scope.querySelector('#fpDegradedBanner');
  // Hero
  var heroLabel        = scope.querySelector('#fpHeroLabel');
  var heroScore        = scope.querySelector('#fpHeroScore');
  var labelChip        = scope.querySelector('#fpLabelChip');
  var signalQualityEl  = scope.querySelector('#fpSignalQuality');
  var confidenceChip   = scope.querySelector('#fpConfidenceChip');
  var summaryEl        = scope.querySelector('#fpSummary');
  // Flow drivers
  var positiveDrivers  = scope.querySelector('#fpPositiveDrivers');
  var negativeDrivers  = scope.querySelector('#fpNegativeDrivers');
  // Strategy bias bars
  var biasContinuationBar = scope.querySelector('#fpBiasContinuationBar');
  var biasContinuationVal = scope.querySelector('#fpBiasContinuationVal');
  var biasReversalBar     = scope.querySelector('#fpBiasReversalBar');
  var biasReversalVal     = scope.querySelector('#fpBiasReversalVal');
  var biasSqueezeBar      = scope.querySelector('#fpBiasSqueezeBar');
  var biasSqueezeVal      = scope.querySelector('#fpBiasSqueezeVal');
  var biasFragilityBar    = scope.querySelector('#fpBiasFragilityBar');
  var biasFragilityVal    = scope.querySelector('#fpBiasFragilityVal');
  // Pillar bars container
  var pillarBarsEl     = scope.querySelector('#fpPillarBars');
  // Pillar 1 — Positioning Pressure
  var fpPutCall        = scope.querySelector('#fpPutCall');
  var fpVIX            = scope.querySelector('#fpVIX');
  var fpSystematic     = scope.querySelector('#fpSystematic');
  var fpFuturesNet     = scope.querySelector('#fpFuturesNet');
  var fpP1Bar          = scope.querySelector('#fpP1Bar');
  var fpP1Score        = scope.querySelector('#fpP1Score');
  // Pillar 2 — Crowding / Stretch
  var fpCrowding       = scope.querySelector('#fpCrowding');
  var fpRetailBull     = scope.querySelector('#fpRetailBull');
  var fpRetailBear     = scope.querySelector('#fpRetailBear');
  var fpShortInterest  = scope.querySelector('#fpShortInterest');
  var fpP2Bar          = scope.querySelector('#fpP2Bar');
  var fpP2Score        = scope.querySelector('#fpP2Score');
  // Pillar 3 — Squeeze / Unwind
  var fpVIXTerm        = scope.querySelector('#fpVIXTerm');
  var fpAsymmetry      = scope.querySelector('#fpAsymmetry');
  var fpP3Bar          = scope.querySelector('#fpP3Bar');
  var fpP3Score        = scope.querySelector('#fpP3Score');
  var fpSqueezeNote    = scope.querySelector('#fpSqueezeNote');
  // Pillar 4 — Flow Direction & Persistence
  var fpFlowDir        = scope.querySelector('#fpFlowDir');
  var fpPersist5d      = scope.querySelector('#fpPersist5d');
  var fpPersist20d     = scope.querySelector('#fpPersist20d');
  var fpFollowThru     = scope.querySelector('#fpFollowThru');
  var fpP4Bar          = scope.querySelector('#fpP4Bar');
  var fpP4Score        = scope.querySelector('#fpP4Score');
  // Pillar 5 — Positioning Stability
  var fpP5Bar          = scope.querySelector('#fpP5Bar');
  var fpP5Score        = scope.querySelector('#fpP5Score');
  var fpStabilityNote  = scope.querySelector('#fpStabilityNote');
  // Data quality
  var confidenceScoreEl = scope.querySelector('#fpConfidenceScore');
  var warningsEl       = scope.querySelector('#fpWarnings');
  // Takeaway
  var takeawayEl       = scope.querySelector('#fpTakeaway');
  // AI Model
  var runModelBtn      = scope.querySelector('#fpRunModelBtn');
  var modelCta         = scope.querySelector('#fpModelCta');
  var modelDetailsRow  = scope.querySelector('#fpModelDetailsRow');
  var modelLabel       = scope.querySelector('#fpModelLabel');
  var modelScore       = scope.querySelector('#fpModelScore');
  var modelSummary     = scope.querySelector('#fpModelSummary');
  var modelPillars     = scope.querySelector('#fpModelPillars');

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
    if (l.indexOf('supportive') >= 0 || l.indexOf('strongly') >= 0) return 'mod-chip-bullish';
    if (l.indexOf('reversal') >= 0 || l.indexOf('unstable') >= 0) return 'mod-chip-bearish';
    if (l.indexOf('fragile') >= 0 || l.indexOf('crowded') >= 0) return 'mod-chip-bearish';
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

  // ── Render ────────────────────────────────────────────────────

  function render(payload) {
    if (_destroyed) return;
    var er = payload.engine_result || {};
    var dq = payload.data_quality || {};

    // ── Degraded-state banner ─────────────────────────────────
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
    renderList(positiveDrivers, er.positive_contributors, 'positive');
    renderList(negativeDrivers, er.negative_contributors, 'negative');

    // Strategy bias bars
    var bias = er.strategy_bias || {};
    setBar(biasContinuationBar, biasContinuationVal, bias.continuation_support);
    setBar(biasReversalBar, biasReversalVal, bias.reversal_risk);
    setBar(biasSqueezeBar, biasSqueezeVal, bias.squeeze_potential);
    setBar(biasFragilityBar, biasFragilityVal, bias.fragility);

    // Pillar bars (summary row)
    if (pillarBarsEl) {
      var ps = er.pillar_scores || {};
      var pw = er.pillar_weights || {};
      var names = {
        positioning_pressure: 'Positioning Pressure',
        crowding_stretch: 'Crowding / Stretch',
        squeeze_unwind_risk: 'Squeeze / Unwind Risk',
        flow_direction_persistence: 'Flow Direction & Persistence',
        positioning_stability: 'Positioning Stability'
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

    // ── Component cards ───────────────────────────────────────
    var rawPos = (er.raw_inputs || {}).positioning || {};
    var rawCrowd = (er.raw_inputs || {}).crowding || {};
    var rawSqueeze = (er.raw_inputs || {}).squeeze || {};
    var rawFlow = (er.raw_inputs || {}).flow || {};

    // Pillar 1 — Positioning Pressure
    setText(fpPutCall, fmtVal(rawPos.put_call_ratio));
    setText(fpVIX, fmtVal(rawPos.vix_level));
    setText(fpSystematic, fmtVal(rawPos.systematic_allocation, '%'));
    setText(fpFuturesNet, fmtVal(rawPos.futures_net_long, '%'));
    setBar(fpP1Bar, fpP1Score, (er.pillar_scores || {}).positioning_pressure);

    // Pillar 2 — Crowding / Stretch
    setText(fpCrowding, fmtVal(rawCrowd.crowding_level));
    setText(fpRetailBull, rawCrowd.retail_bull_pct != null ? Math.round(rawCrowd.retail_bull_pct) + '%' : '—');
    setText(fpRetailBear, rawCrowd.retail_bear_pct != null ? Math.round(rawCrowd.retail_bear_pct) + '%' : '—');
    setText(fpShortInterest, fmtVal(rawCrowd.short_interest));
    setBar(fpP2Bar, fpP2Score, (er.pillar_scores || {}).crowding_stretch);

    // Pillar 3 — Squeeze / Unwind
    setText(fpVIXTerm, fmtVal(rawSqueeze.vix_term_structure));
    setText(fpAsymmetry, fmtVal(rawSqueeze.positioning_asymmetry));
    setBar(fpP3Bar, fpP3Score, (er.pillar_scores || {}).squeeze_unwind_risk);
    // Squeeze note from pillar explanation
    if (fpSqueezeNote) {
      var sqExpl = (er.pillar_explanations || {}).squeeze_unwind_risk;
      setText(fpSqueezeNote, sqExpl || '—');
    }

    // Pillar 4 — Flow Direction & Persistence
    setText(fpFlowDir, fmtVal(rawFlow.flow_direction));
    setText(fpPersist5d, fmtVal(rawFlow.persistence_5d));
    setText(fpPersist20d, fmtVal(rawFlow.persistence_20d));
    setText(fpFollowThru, fmtVal(rawFlow.follow_through));
    setBar(fpP4Bar, fpP4Score, (er.pillar_scores || {}).flow_direction_persistence);

    // Pillar 5 — Positioning Stability
    setBar(fpP5Bar, fpP5Score, (er.pillar_scores || {}).positioning_stability);
    if (fpStabilityNote) {
      var stabExpl = (er.pillar_explanations || {}).positioning_stability;
      setText(fpStabilityNote, stabExpl || '—');
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
        console.error('[BenTrade][Flows] fetch error', err);
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
        console.error('[BenTrade][Flows] model fetch error', err);
        if (runModelBtn) {
          runModelBtn.disabled = false;
          runModelBtn.textContent = 'Run AI Model';
        }
      });
  }

  // ── Init ──────────────────────────────────────────────────────

  // Try cache first
  if (_cache && _cache.hasCache(CACHE_KEY)) {
    console.log('[BenTrade][Flows] cache_rehydrate');
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

  return function cleanupFlowsPositioning() {
    _destroyed = true;
    console.log('[BenTrade][Flows] cleanup — DOM detached, cache preserved');
  };
};
