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
  var modelImplications = scope.querySelector('#fpModelImplications');

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

  function fmtProxy(val, suffix) {
    if (val == null) return '—';
    return Number(val).toFixed(2) + (suffix || '') + ' \u207F'; // superscript-n as proxy marker
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
    setText(fpPutCall, fmtProxy(rawPos.put_call_ratio));
    setText(fpVIX, fmtVal(rawPos.vix));
    setText(fpSystematic, fmtProxy(rawPos.systematic_allocation, '%'));
    setText(fpFuturesNet, fmtProxy(rawPos.futures_net_long_pct, '%'));
    setBar(fpP1Bar, fpP1Score, (er.pillar_scores || {}).positioning_pressure);

    // Pillar 2 — Crowding / Stretch
    setText(fpCrowding, fmtProxy(rawCrowd.crowding_level));
    setText(fpRetailBull, rawCrowd.retail_bull_pct != null ? Math.round(rawCrowd.retail_bull_pct) + '% \u207F' : '—');
    setText(fpRetailBear, rawCrowd.retail_bear_pct != null ? Math.round(rawCrowd.retail_bear_pct) + '% \u207F' : '—');
    setText(fpShortInterest, fmtProxy(rawCrowd.short_interest_pct));
    setBar(fpP2Bar, fpP2Score, (er.pillar_scores || {}).crowding_stretch);

    // Pillar 3 — Squeeze / Unwind
    setText(fpVIXTerm, fmtVal(rawSqueeze.vix_term_structure));
    setText(fpAsymmetry, fmtProxy(rawSqueeze.positioning_asymmetry));
    setBar(fpP3Bar, fpP3Score, (er.pillar_scores || {}).squeeze_unwind_risk);
    // Squeeze note from pillar explanation
    if (fpSqueezeNote) {
      var sqExpl = (er.pillar_explanations || {}).squeeze_unwind_risk;
      setText(fpSqueezeNote, sqExpl || '—');
    }

    // Pillar 4 — Flow Direction & Persistence
    setText(fpFlowDir, fmtProxy(rawFlow.flow_direction_score));
    setText(fpPersist5d, fmtProxy(rawFlow.flow_persistence_5d));
    setText(fpPersist20d, fmtProxy(rawFlow.flow_persistence_20d));
    setText(fpFollowThru, fmtProxy(rawFlow.follow_through_score));
    setBar(fpP4Bar, fpP4Score, (er.pillar_scores || {}).flow_direction_persistence);

    // Pillar 5 — Positioning Stability
    setBar(fpP5Bar, fpP5Score, (er.pillar_scores || {}).positioning_stability);
    if (fpStabilityNote) {
      var stabExpl = (er.pillar_explanations || {}).positioning_stability;
      setText(fpStabilityNote, stabExpl || '—');
    }

    // Data quality
    setText(confidenceScoreEl, er.confidence_score != null ? Math.round(er.confidence_score) + '/100' : '—');

    // Data quality footnote — proxy indicator
    var dqMeta = er.data_quality;
    if (warningsEl && dqMeta) {
      var proxyNote = '<div style="opacity:0.6;font-size:9px;margin-top:4px;border-top:1px solid rgba(255,255,255,0.1);padding-top:4px;">' +
        '\u207F = VIX-derived proxy (' + (dqMeta.proxy_count || 0) + ' of ' +
        ((dqMeta.proxy_count || 0) + (dqMeta.direct_count || 0)) + ' signals) &mdash; ' +
        escapeHtml(dqMeta.phase || '') + '</div>';
    }

    // Warnings
    if (warningsEl) {
      var warns = er.warnings || [];
      if (warns.length === 0) {
        warningsEl.innerHTML = '<div style="opacity:0.5;font-size:10px;">No warnings</div>' + (proxyNote || '');
      } else {
        warningsEl.innerHTML = warns.slice(0, 10).map(function(w) {
          return '<div class="mod-warning-item" style="font-size:10px;margin-bottom:2px;">⚠ ' + escapeHtml(w) + '</div>';
        }).join('') + (proxyNote || '');
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

  function setModelBtnState(loading) {
    runModelBtn = scope.querySelector('#fpRunModelBtn');
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
        '<div class="mod-model-cta" id="fpModelCta">' +
        '<p style="opacity:0.6;font-size:12px;margin:0 0 10px;">Model analysis has not been run yet.</p>' +
        '<button class="mod-action-btn" id="fpRunModelBtn" type="button">Run Model Analysis</button>' +
        '</div>';
      var btn = modelSummary.querySelector('#fpRunModelBtn');
      if (btn) btn.addEventListener('click', function() { triggerModelAnalysis(); });
    }
    setText(modelLabel, '—');
    setText(modelScore, '—');
    if (modelDetailsRow) modelDetailsRow.style.display = 'none';
  }

  function renderModelError(errMsg) {
    console.error('[BenTrade][Flows] Model analysis error:', errMsg);
    if (modelSummary) {
      modelSummary.innerHTML =
        '<div style="color:rgba(255,79,102,0.9);font-size:12px;margin-bottom:8px;">' +
        escapeHtml(errMsg) + '</div>' +
        '<button class="mod-action-btn" id="fpRunModelBtn" type="button">Retry Model Analysis</button>';
      var btn = modelSummary.querySelector('#fpRunModelBtn');
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
    console.log('[BenTrade][Flows] Rendering model result:', model.label, model.score);

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

      var fd = model.flows_drivers || {};
      if (fd.constructive_factors && fd.constructive_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">CONSTRUCTIVE</span><ul class="mod-contrib-list">';
        fd.constructive_factors.forEach(function(f) {
          html += '<li class="mod-contrib-item"><span class="mod-contrib-dot positive"></span>' + escapeHtml(f) + '</li>';
        });
        html += '</ul></div>';
      }
      if (fd.warning_factors && fd.warning_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">WARNINGS</span><ul class="mod-contrib-list">';
        fd.warning_factors.forEach(function(f) {
          html += '<li class="mod-contrib-item"><span class="mod-contrib-dot negative"></span>' + escapeHtml(f) + '</li>';
        });
        html += '</ul></div>';
      }
      if (fd.conflicting_factors && fd.conflicting_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">CONFLICTING</span><ul class="mod-contrib-list">';
        fd.conflicting_factors.forEach(function(f) {
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
        '<button class="mod-action-btn" id="fpRunModelBtn" type="button">Re-run Model Analysis</button></div>';

      modelSummary.innerHTML = html;
      var btn = modelSummary.querySelector('#fpRunModelBtn');
      if (btn) btn.addEventListener('click', function() { triggerModelAnalysis(); });
    }

    // Model detail row — pillar analysis
    if (modelDetailsRow) modelDetailsRow.style.display = '';
    if (modelPillars) {
      var pa = model.pillar_analysis || {};
      var pillarKeys = ['positioning_pressure', 'crowding_stretch', 'squeeze_unwind', 'flow_persistence', 'positioning_stability'];
      var pillarLabels = {
        positioning_pressure: 'Positioning Pressure', crowding_stretch: 'Crowding / Stretch',
        squeeze_unwind: 'Squeeze / Unwind', flow_persistence: 'Flow Persistence',
        positioning_stability: 'Positioning Stability'
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
      if (!pillarsHtml) {
        Object.keys(pa).forEach(function(k) {
          pillarsHtml += '<div style="margin-bottom:8px;">' +
            '<div style="font-size:10px;font-weight:600;opacity:0.6;text-transform:uppercase;">' +
            escapeHtml(k.replace(/_/g, ' ')) + '</div>' +
            '<div style="font-size:11px;line-height:1.5;">' + escapeHtml(pa[k]) + '</div></div>';
        });
      }
      modelPillars.innerHTML = pillarsHtml || '<div style="opacity:0.5;font-size:11px;">No pillar analysis available</div>';
    }

    // Model detail row — trading implications
    if (modelImplications) {
      var mi = model.market_implications || {};
      var implKeys = ['directional_bias', 'position_sizing', 'strategy_recommendation', 'risk_level', 'flow_tilt'];
      var implLabels = {
        directional_bias: 'Directional Bias', position_sizing: 'Position Sizing',
        strategy_recommendation: 'Strategy Recommendation', risk_level: 'Risk Level',
        flow_tilt: 'Flow Tilt'
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

  // ── Fetch & Cache ─────────────────────────────────────────────

  function setLoading(btn, loading) {
    if (!btn) return;
    if (loading) {
      btn.disabled = true;
      btn.classList.add('btn-refreshing');
      btn.innerHTML = '<span class="btn-spinner"></span> Refreshing\u2026';
    } else {
      btn.disabled = false;
      btn.classList.remove('btn-refreshing');
      btn.innerHTML = 'Refresh';
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
        if (_cache) _cache.set(CACHE_KEY, payload);
        setLoading(refreshBtn, false);
      })
      .catch(function(err) {
        console.error('[BenTrade][Flows] fetch error', err);
        setLoading(refreshBtn, false);
      });
  }

  function triggerModelAnalysis() {
    if (_destroyed) return;
    console.log('[BenTrade][Flows] Triggering model analysis…');
    if (modelSummary) {
      modelSummary.innerHTML =
        '<div style="text-align:center;padding:18px 0;">' +
        '<div style="display:inline-block;width:22px;height:22px;border-radius:50%;' +
        'border:2px solid rgba(0,234,255,0.15);border-top-color:rgba(0,234,255,0.9);' +
        'animation:btnInlineSpin 0.8s linear infinite;margin-bottom:8px;"></div>' +
        '<div style="font-size:11px;opacity:0.7;">Running model analysis… Interpreting flows &amp; positioning.</div></div>';
    }
    setModelBtnState(true);
    var CLIENT_TIMEOUT = (window.BenTradeApi && window.BenTradeApi.MODEL_TIMEOUT_MS) || 185000;
    var t0 = performance.now();
    console.log('[FP_MODEL] request_start', {
      endpoint: MODEL_URL, method: 'POST',
      timeout_ms: CLIENT_TIMEOUT,
      timestamp: new Date().toISOString(),
    });
    var controller = new AbortController();
    var timerFired = false;
    var timer = setTimeout(function() {
      timerFired = true;
      console.warn('[FP_MODEL] abort_timer_fired', {
        elapsed_ms: Math.round(performance.now() - t0),
        timeout_ms: CLIENT_TIMEOUT,
      });
      controller.abort();
    }, CLIENT_TIMEOUT);
    fetch(MODEL_URL, { method: 'POST', signal: controller.signal })
      .then(function(resp) {
        console.log('[FP_MODEL] response_headers', {
          status: resp.status, ok: resp.ok,
          elapsed_ms: Math.round(performance.now() - t0),
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .then(function(result) {
        if (_destroyed) return;
        console.log('[FP_MODEL] body_parsed', {
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
        console.error('[FP_MODEL] failure', { error: err.message, name: err.name, elapsed_ms: elapsed, timerFired: timerFired });
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
        console.log('[FP_MODEL] lifecycle_complete', { total_ms: Math.round(performance.now() - t0) });
      });
  }

  // ── Init ──────────────────────────────────────────────────────

  // Try cache first
  if (_cache && _cache.hasCache(CACHE_KEY)) {
    console.log('[BenTrade][Flows] cache_rehydrate');
    render(_cache.getData(CACHE_KEY));
  }
  if (_cache && _cache.hasCache(MODEL_CACHE_KEY)) {
    var mc = _cache.getData(MODEL_CACHE_KEY);
    if (mc) renderModel(mc);
  }

  // Always fetch fresh data
  fetchData(false);

  // Wire buttons
  if (refreshBtn) {
    refreshBtn.addEventListener('click', function() { fetchData(true); });
  }
  if (runModelBtn) {
    runModelBtn.addEventListener('click', function() { triggerModelAnalysis(); });
  }

  return function cleanupFlowsPositioning() {
    _destroyed = true;
    console.log('[BenTrade][Flows] cleanup — DOM detached, cache preserved');
  };
};
