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
  var modelImplications = scope.querySelector('#caModelImplications');

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

  function setModelBtnState(loading) {
    runModelBtn = scope.querySelector('#caRunModelBtn');
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
        '<div class="mod-model-cta" id="caModelCta">' +
        '<p style="opacity:0.6;font-size:12px;margin:0 0 10px;">Model analysis has not been run yet.</p>' +
        '<button class="mod-action-btn" id="caRunModelBtn" type="button">Run Model Analysis</button>' +
        '</div>';
      var btn = modelSummary.querySelector('#caRunModelBtn');
      if (btn) btn.addEventListener('click', function() { triggerModelAnalysis(); });
    }
    setText(modelLabel, '—');
    setText(modelScore, '—');
    if (modelDetailsRow) modelDetailsRow.style.display = 'none';
  }

  function renderModelError(errMsg) {
    console.error('[BenTrade][CrossAsset] Model analysis error:', errMsg);
    if (modelSummary) {
      modelSummary.innerHTML =
        '<div style="color:rgba(255,79,102,0.9);font-size:12px;margin-bottom:8px;">' +
        escapeHtml(errMsg) + '</div>' +
        '<button class="mod-action-btn" id="caRunModelBtn" type="button">Retry Model Analysis</button>';
      var btn = modelSummary.querySelector('#caRunModelBtn');
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
    console.log('[BenTrade][CrossAsset] Rendering model result:', model.label, model.score);

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

      var cd = model.cross_asset_drivers || {};
      if (cd.constructive_factors && cd.constructive_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">CONSTRUCTIVE</span><ul class="mod-contrib-list">';
        cd.constructive_factors.forEach(function(f) {
          html += '<li class="mod-contrib-item"><span class="mod-contrib-dot positive"></span>' + escapeHtml(f) + '</li>';
        });
        html += '</ul></div>';
      }
      if (cd.warning_factors && cd.warning_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">WARNINGS</span><ul class="mod-contrib-list">';
        cd.warning_factors.forEach(function(f) {
          html += '<li class="mod-contrib-item"><span class="mod-contrib-dot negative"></span>' + escapeHtml(f) + '</li>';
        });
        html += '</ul></div>';
      }
      if (cd.conflicting_factors && cd.conflicting_factors.length > 0) {
        html += '<div style="margin-top:6px;"><span style="font-size:10px;opacity:0.6;">CONFLICTING</span><ul class="mod-contrib-list">';
        cd.conflicting_factors.forEach(function(f) {
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
        '<button class="mod-action-btn" id="caRunModelBtn" type="button">Re-run Model Analysis</button></div>';

      modelSummary.innerHTML = html;
      var btn = modelSummary.querySelector('#caRunModelBtn');
      if (btn) btn.addEventListener('click', function() { triggerModelAnalysis(); });
    }

    // Model detail row — pillar analysis
    if (modelDetailsRow) modelDetailsRow.style.display = '';
    if (modelPillars) {
      var pa = model.pillar_analysis || {};
      var pillarKeys = ['rates_bonds', 'equity_internals', 'currencies', 'commodities', 'cross_asset_coherence'];
      var pillarLabels = {
        rates_bonds: 'Rates & Bonds', equity_internals: 'Equity Internals',
        currencies: 'Currencies', commodities: 'Commodities',
        cross_asset_coherence: 'Cross-Asset Coherence'
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
      // Fallback: render any extra keys the LLM included
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
        console.error('[BenTrade][CrossAsset] fetch error', err);
        setLoading(refreshBtn, false);
      });
  }

  function triggerModelAnalysis() {
    if (_destroyed) return;
    console.log('[BenTrade][CrossAsset] Triggering model analysis…');
    if (modelSummary) {
      modelSummary.innerHTML =
        '<div style="text-align:center;padding:18px 0;">' +
        '<div style="display:inline-block;width:22px;height:22px;border-radius:50%;' +
        'border:2px solid rgba(0,234,255,0.15);border-top-color:rgba(0,234,255,0.9);' +
        'animation:btnInlineSpin 0.8s linear infinite;margin-bottom:8px;"></div>' +
        '<div style="font-size:11px;opacity:0.7;">Running model analysis… Interpreting cross-asset signals.</div></div>';
    }
    setModelBtnState(true);
    var CLIENT_TIMEOUT = (window.BenTradeApi && window.BenTradeApi.MODEL_TIMEOUT_MS) || 185000;
    var t0 = performance.now();
    console.log('[CA_MODEL] request_start', {
      endpoint: MODEL_URL, method: 'POST',
      timeout_ms: CLIENT_TIMEOUT,
      timestamp: new Date().toISOString(),
    });
    var controller = new AbortController();
    var timerFired = false;
    var timer = setTimeout(function() {
      timerFired = true;
      console.warn('[CA_MODEL] abort_timer_fired', {
        elapsed_ms: Math.round(performance.now() - t0),
        timeout_ms: CLIENT_TIMEOUT,
      });
      controller.abort();
    }, CLIENT_TIMEOUT);
    fetch(MODEL_URL, { method: 'POST', signal: controller.signal })
      .then(function(resp) {
        console.log('[CA_MODEL] response_headers', {
          status: resp.status, ok: resp.ok,
          elapsed_ms: Math.round(performance.now() - t0),
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .then(function(result) {
        if (_destroyed) return;
        console.log('[CA_MODEL] body_parsed', {
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
        console.error('[CA_MODEL] failure', { error: err.message, name: err.name, elapsed_ms: elapsed, timerFired: timerFired });
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
        console.log('[CA_MODEL] lifecycle_complete', { total_ms: Math.round(performance.now() - t0) });
      });
  }

  // ── Init ──────────────────────────────────────────────────────

  // Try cache first
  if (_cache && _cache.hasCache(CACHE_KEY)) {
    console.log('[BenTrade][CrossAsset] cache_rehydrate');
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

  return function cleanupCrossAssetMacro() {
    _destroyed = true;
    console.log('[BenTrade][CrossAsset] cleanup — DOM detached, cache preserved');
  };
};
