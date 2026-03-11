window.BenTradePages = window.BenTradePages || {};

/**
 * News & Sentiment dashboard — layered architecture.
 *
 * Layers:
 *   1. Base payload  (items + macro + source_freshness) — always available from /api/news-sentiment
 *   2. Internal Engine (deterministic) — returned with base payload, additive overlay
 *   3. Model Analysis (LLM) — manual trigger only via POST /api/news-sentiment/model
 *
 * Caching:
 *   - Base + engine cached under 'newsSentiment'
 *   - Model analysis cached under 'newsSentimentModel'
 *   - Both via BenTradeDashboardCache (sessionStorage-backed)
 */
window.BenTradePages.initNewsSentiment = function initNewsSentiment(rootEl) {
  var doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  var scope = rootEl || doc;
  var API_URL = '/api/news-sentiment';
  var MODEL_URL = '/api/news-sentiment/model';
  var CACHE_KEY = 'newsSentiment';
  var MODEL_CACHE_KEY = 'newsSentimentModel';
  var _cache = window.BenTradeDashboardCache;

  // ── DOM refs ──────────────────────────────────────────────────
  var refreshBtn       = scope.querySelector('#nsRefreshBtn');
  var errorEl          = scope.querySelector('#nsError');
  var lastUpdatedEl    = scope.querySelector('#nsLastUpdated');
  // Engine track
  var engineLabel      = scope.querySelector('#nsEngineLabel');
  var engineScore      = scope.querySelector('#nsEngineScore');
  var engineComponents = scope.querySelector('#nsEngineComponents');
  // Model track
  var modelLabel       = scope.querySelector('#nsModelLabel');
  var modelScore       = scope.querySelector('#nsModelScore');
  var modelSummary     = scope.querySelector('#nsModelSummary');
  var runModelBtn      = scope.querySelector('#nsRunModelBtn');
  var modelCta         = scope.querySelector('#nsModelCta');
  // Model insights row
  var insightsRow      = scope.querySelector('#nsInsightsRow');
  var modelNarratives  = scope.querySelector('#nsModelNarratives');
  var modelRisks       = scope.querySelector('#nsModelRisks');
  var modelTriggers    = scope.querySelector('#nsModelTriggers');
  // Macro
  var macroVix         = scope.querySelector('#nsMacroVix');
  var macro10y         = scope.querySelector('#nsMacro10y');
  var macro2y          = scope.querySelector('#nsMacro2y');
  var macroSpread      = scope.querySelector('#nsMacroSpread');
  var macroOil         = scope.querySelector('#nsMacroOil');
  var macroFed         = scope.querySelector('#nsMacroFed');
  var macroStress      = scope.querySelector('#nsMacroStress');
  // Freshness badge slots
  var freshVix         = scope.querySelector('#nsFreshVix');
  var fresh10y         = scope.querySelector('#nsFresh10y');
  var fresh2y          = scope.querySelector('#nsFresh2y');
  var freshOil         = scope.querySelector('#nsFreshOil');
  var freshFed         = scope.querySelector('#nsFreshFed');
  // Headlines
  var headlineFeed     = scope.querySelector('#nsHeadlineFeed');
  var itemCountEl      = scope.querySelector('#nsItemCount');
  // Component breakdown
  var breakdownEl      = scope.querySelector('#nsComponentBreakdown');
  // Summary metrics
  var metricTotal      = scope.querySelector('#nsMetricTotal');
  var metricBullish    = scope.querySelector('#nsMetricBullish');
  var metricBearish    = scope.querySelector('#nsMetricBearish');
  var metricNeutral    = scope.querySelector('#nsMetricNeutral');
  var metricMixed      = scope.querySelector('#nsMetricMixed');
  var metricRecent     = scope.querySelector('#nsMetricRecent');
  var categoryBarEl    = scope.querySelector('#nsCategoryBar');
  // Source health
  var sourceHealthEl   = scope.querySelector('#nsSourceHealth');

  var _destroyed = false;
  var _autoRefreshTimer = null;

  // ── Utilities ─────────────────────────────────────────────────

  function escapeHtml(val) {
    return String(val ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function formatTime(iso) {
    if (!iso) return '';
    var d = new Date(String(iso));
    if (Number.isNaN(d.getTime())) return '';
    var now = new Date();
    var diffMs = now - d;
    var diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return diffMin + 'm ago';
    var diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return diffHr + 'h ago';
    return d.toLocaleDateString();
  }

  function setError(text) {
    if (!errorEl) return;
    if (!text) { errorEl.style.display = 'none'; errorEl.innerHTML = ''; return; }
    errorEl.style.display = 'block';
    errorEl.innerHTML = escapeHtml(text);
  }

  function regimeClass(label) {
    return String(label || '').toLowerCase().replace(/\s+/g, '-');
  }

  function scoreColor100(score) {
    if (score == null) return 'var(--muted)';
    if (score >= 65) return '#00e676';
    if (score >= 40) return 'var(--cyan)';
    if (score >= 25) return 'var(--warn)';
    return '#ff4f66';
  }

  function barColor(score) {
    if (score >= 65) return 'linear-gradient(90deg, rgba(0,230,118,0.5), #00e676)';
    if (score >= 40) return 'linear-gradient(90deg, rgba(0,234,255,0.4), var(--cyan))';
    if (score >= 25) return 'linear-gradient(90deg, rgba(244,200,95,0.4), var(--warn))';
    return 'linear-gradient(90deg, rgba(255,79,102,0.5), #ff4f66)';
  }

  function updateLastUpdated() {
    if (!lastUpdatedEl) return;
    var cached = _cache && _cache.get(CACHE_KEY);
    var ts = cached ? cached.lastUpdated : null;
    if (ts) {
      lastUpdatedEl.textContent = 'Updated ' + new Date(ts).toLocaleTimeString();
    }
  }

  // ── Render: Engine card ───────────────────────────────────────

  function renderEngine(engine) {    console.log('[NEWS_ENGINE] renderEngine called', {
      hasEngine: !!engine,
      score: engine ? engine.score : null,
      label: engine ? engine.regime_label : null,
      componentKeys: engine && engine.components ? Object.keys(engine.components) : [],
    });    if (!engine) {
      if (engineLabel) { engineLabel.textContent = '—'; engineLabel.className = 'ns-regime-label'; }
      if (engineScore) { engineScore.textContent = '—'; engineScore.style.color = 'var(--muted)'; }
      if (engineComponents) engineComponents.innerHTML = '<div class="ns-loading">Engine data unavailable</div>';
      return;
    }
    var label = engine.regime_label || '—';
    var score = engine.score;
    var expl = engine.explanation || {};

    if (engineLabel) {
      engineLabel.textContent = expl.label || label;
      engineLabel.className = 'ns-regime-label ' + regimeClass(expl.label || label);
    }
    if (engineScore) {
      engineScore.textContent = score != null ? score.toFixed(1) : '—';
      engineScore.style.color = scoreColor100(score);
    }

    if (engineComponents && engine.components) {
      var names = [
        'headline_sentiment', 'negative_pressure', 'narrative_severity',
        'source_agreement', 'macro_stress', 'recency_pressure'
      ];
      var html = '';

      // ── Summary section ──────────────────────────────────────
      if (expl.summary) {
        html += '<div class="ns-engine-summary">' + escapeHtml(expl.summary) + '</div>';
      }

      // ── Component bars with interpretation ───────────────────
      var compAnalysis = {};
      if (expl.component_analysis) {
        for (var a = 0; a < expl.component_analysis.length; a++) {
          compAnalysis[expl.component_analysis[a].component] = expl.component_analysis[a];
        }
      }

      for (var i = 0; i < names.length; i++) {
        var name = names[i];
        var comp = engine.components[name];
        if (!comp) continue;
        var s = comp.score != null ? comp.score : 0;
        var ca = compAnalysis[name];
        var displayName = ca ? ca.display_name : name;
        var tooltip = ca ? ca.tooltip : '';
        var interpretation = ca ? ca.interpretation : '';
        var contribution = ca ? ca.contribution : 'neutral';
        var contribCls = 'ns-contrib-' + contribution;

        html +=
          '<div class="ns-comp-row-enhanced">' +
            '<div class="ns-comp-row">' +
              '<span class="ns-comp-name" title="' + escapeHtml(tooltip) + '">' +
                escapeHtml(displayName) +
                '<span class="ns-comp-contrib ' + contribCls + '"></span>' +
              '</span>' +
              '<div class="ns-comp-bar-track">' +
                '<div class="ns-comp-bar-fill" style="width:' + s + '%;background:' + barColor(s) + '"></div>' +
              '</div>' +
              '<span class="ns-comp-val">' + s.toFixed(0) + '</span>' +
            '</div>' +
            (interpretation ? '<div class="ns-comp-interp">' + escapeHtml(interpretation) + '</div>' : '') +
          '</div>';
      }

      // ── Trader takeaway ──────────────────────────────────────
      if (expl.trader_takeaway) {
        html += '<div class="ns-engine-takeaway">' +
                '<div class="ns-engine-section-title">TRADER TAKEAWAY</div>' +
                '<div class="ns-engine-takeaway-text">' + escapeHtml(expl.trader_takeaway) + '</div>' +
                '</div>';
      }

      engineComponents.innerHTML = html || '<div class="ns-loading">No components</div>';
    }
  }

  // ── Render: Model card ────────────────────────────────────────

  function renderModelNotRun() {
    if (modelLabel) { modelLabel.textContent = '—'; modelLabel.className = 'ns-regime-label'; }
    if (modelScore) { modelScore.textContent = '—'; modelScore.style.color = 'var(--muted)'; }
    if (modelSummary) {
      modelSummary.innerHTML =
        '<div class="ns-model-cta" id="nsModelCta">' +
          '<p class="ns-model-cta-text">Model analysis has not been run yet.</p>' +
          '<button class="mod-action-btn" id="nsRunModelBtn" type="button">Run Model Analysis</button>' +
        '</div>';
      // Rebind button after innerHTML replace
      runModelBtn = modelSummary.querySelector('#nsRunModelBtn');
      if (runModelBtn) runModelBtn.addEventListener('click', triggerModelAnalysis);
    }
    if (insightsRow) insightsRow.style.display = 'none';
  }

  function renderModelError(errMsg) {
    if (modelLabel) {
      modelLabel.textContent = 'Error';
      modelLabel.className = 'ns-regime-label unavailable';
    }
    if (modelScore) { modelScore.textContent = '—'; modelScore.style.color = 'var(--muted)'; }
    if (modelSummary) {
      modelSummary.innerHTML =
        '<div class="ns-model-cta">' +
          '<p class="ns-model-cta-text" style="color:var(--danger);">' + escapeHtml(errMsg) + '</p>' +
          '<button class="mod-action-btn" id="nsRunModelBtn" type="button">Retry Model Analysis</button>' +
        '</div>';
      runModelBtn = modelSummary.querySelector('#nsRunModelBtn');
      if (runModelBtn) runModelBtn.addEventListener('click', triggerModelAnalysis);
    }
  }

  function renderModel(model) {
    if (!model) { renderModelNotRun(); return; }

    // Use new "label" field, fall back to legacy "regime_label"
    var label = model.label || model.regime_label || '—';
    var score = model.score;

    if (modelLabel) {
      modelLabel.textContent = label;
      // Map label to CSS class
      var labelCls = label.toLowerCase().replace(/[\s_]+/g, '-');
      modelLabel.className = 'ns-regime-label ' + regimeClass(labelCls);
    }
    if (modelScore) {
      modelScore.textContent = score != null ? score.toFixed(1) : '—';
      modelScore.style.color = scoreColor100(score);
    }
    if (modelSummary) {
      var html = '';

      // ── Confidence + Tone row ────────────────────────────────
      var metaHtml = '';
      if (model.confidence != null) {
        metaHtml += '<span class="ns-model-meta-item">Confidence: <strong>' +
                    (model.confidence * 100).toFixed(0) + '%</strong></span>';
      }
      var tone = model.tone || model.headline_tone;
      if (tone) {
        metaHtml += '<span class="ns-model-meta-item">Tone: <strong>' +
                    escapeHtml(tone) + '</strong></span>';
      }
      if (metaHtml) {
        html += '<div class="ns-model-meta-row">' + metaHtml + '</div>';
      }

      // ── Summary ──────────────────────────────────────────────
      var summary = model.summary || model.executive_summary;
      if (summary) {
        html += '<div class="ns-model-section ns-model-summary-text">' +
                escapeHtml(summary) + '</div>';
      }

      // ── Headline Drivers ─────────────────────────────────────
      if (model.headline_drivers && model.headline_drivers.length) {
        html += '<div class="ns-model-section">' +
                '<div class="ns-model-section-title">HEADLINE DRIVERS</div>' +
                '<div class="ns-driver-list">';
        for (var d = 0; d < model.headline_drivers.length; d++) {
          var drv = model.headline_drivers[d];
          var impactCls = (drv.impact || 'neutral').toLowerCase();
          html += '<div class="ns-driver-item">' +
                  '<span class="ns-driver-theme">' + escapeHtml(drv.theme || '') + '</span>' +
                  '<span class="ns-driver-impact ns-impact-' + escapeHtml(impactCls) + '">' +
                  escapeHtml(drv.impact || '') + '</span>' +
                  (drv.explanation ? '<div class="ns-driver-explain">' + escapeHtml(drv.explanation) + '</div>' : '') +
                  '</div>';
        }
        html += '</div></div>';
      }

      // ── Score Drivers (bullish / bearish / offsetting) ───────
      if (model.score_drivers) {
        var sd = model.score_drivers;
        html += '<div class="ns-model-section ns-score-drivers-grid">';
        if (sd.bullish_factors && sd.bullish_factors.length) {
          html += '<div class="ns-factor-col ns-factor-bullish">' +
                  '<div class="ns-factor-title">BULLISH</div>' +
                  renderFactorList(sd.bullish_factors, 'bullish') + '</div>';
        }
        if (sd.bearish_factors && sd.bearish_factors.length) {
          html += '<div class="ns-factor-col ns-factor-bearish">' +
                  '<div class="ns-factor-title">BEARISH</div>' +
                  renderFactorList(sd.bearish_factors, 'bearish') + '</div>';
        }
        if (sd.offsetting_factors && sd.offsetting_factors.length) {
          html += '<div class="ns-factor-col ns-factor-offsetting">' +
                  '<div class="ns-factor-title">OFFSETTING</div>' +
                  renderFactorList(sd.offsetting_factors, 'mixed') + '</div>';
        }
        html += '</div>';
      }

      // ── Trader Takeaway ──────────────────────────────────────
      if (model.trader_takeaway) {
        html += '<div class="ns-model-section ns-trader-takeaway">' +
                '<div class="ns-model-section-title">TRADER TAKEAWAY</div>' +
                '<div class="ns-takeaway-text">' + escapeHtml(model.trader_takeaway) + '</div>' +
                '</div>';
      }

      // ── Collapsible: Headlines Detail + Uncertainty + Market Implications ──
      var detailHtml = '';
      if (model.major_headlines && model.major_headlines.length) {
        detailHtml += '<div class="ns-model-section">' +
                      '<div class="ns-model-section-title">MAJOR HEADLINES</div>';
        for (var h = 0; h < model.major_headlines.length; h++) {
          var mh = model.major_headlines[h];
          var mhImpact = (mh.market_impact || 'neutral').toLowerCase();
          detailHtml += '<div class="ns-major-headline">' +
                        '<div class="ns-mh-top">' +
                        '<span class="ns-mh-headline">' + escapeHtml(mh.headline || '') + '</span>' +
                        '<span class="ns-badge ns-badge-sentiment ' + escapeHtml(mhImpact) + '">' +
                        escapeHtml(mh.market_impact || '') + '</span>' +
                        '</div>' +
                        (mh.why_it_matters ? '<div class="ns-mh-why">' + escapeHtml(mh.why_it_matters) + '</div>' : '') +
                        '</div>';
        }
        detailHtml += '</div>';
      }
      if (model.market_implications) {
        var mi = model.market_implications;
        var miKeys = ['equities', 'volatility', 'rates', 'energy_or_commodities', 'sector_rotation'];
        var miLabels = { equities: 'Equities', volatility: 'Volatility', rates: 'Rates',
                         energy_or_commodities: 'Energy/Commodities', sector_rotation: 'Sector Rotation' };
        var miHtml = '';
        for (var m = 0; m < miKeys.length; m++) {
          if (mi[miKeys[m]]) {
            miHtml += '<div class="ns-mi-item"><span class="ns-mi-key">' +
                      miLabels[miKeys[m]] + '</span><span class="ns-mi-val">' +
                      escapeHtml(mi[miKeys[m]]) + '</span></div>';
          }
        }
        if (miHtml) {
          detailHtml += '<div class="ns-model-section">' +
                        '<div class="ns-model-section-title">MARKET IMPLICATIONS</div>' +
                        '<div class="ns-mi-grid">' + miHtml + '</div></div>';
        }
      }
      if (model.uncertainty_flags && model.uncertainty_flags.length) {
        detailHtml += '<div class="ns-model-section">' +
                      '<div class="ns-model-section-title">UNCERTAINTY FLAGS</div>' +
                      '<ul class="ns-insight-list ns-uncertainty-list">';
        for (var u = 0; u < model.uncertainty_flags.length; u++) {
          detailHtml += '<li>' + escapeHtml(model.uncertainty_flags[u]) + '</li>';
        }
        detailHtml += '</ul></div>';
      }

      if (detailHtml) {
        html += '<details class="ns-model-details">' +
                '<summary class="ns-model-details-toggle">Show Full Analysis</summary>' +
                '<div class="ns-model-details-body">' + detailHtml + '</div>' +
                '</details>';
      }

      // Always provide re-run option after model completes
      html += '<div style="margin-top:10px;text-align:center;">' +
              '<button class="mod-action-btn" id="nsRunModelBtn" type="button" ' +
              'style="font-size:10px;padding:3px 10px;">Re-run Model Analysis</button></div>';
      modelSummary.innerHTML = html || '<div class="ns-model-unavailable">No summary</div>';
      runModelBtn = modelSummary.querySelector('#nsRunModelBtn');
      if (runModelBtn) runModelBtn.addEventListener('click', triggerModelAnalysis);
    }

    // Insights row — legacy fields still supported
    if (insightsRow) {
      var hasInsights = (model.dominant_narratives && model.dominant_narratives.length) ||
                        (model.underpriced_risks && model.underpriced_risks.length) ||
                        (model.change_triggers && model.change_triggers.length);
      insightsRow.style.display = hasInsights ? '' : 'none';
    }

    renderInsightList(modelNarratives, model.dominant_narratives);
    renderInsightList(modelRisks, model.underpriced_risks);
    renderInsightList(modelTriggers, model.change_triggers);
  }

  function renderFactorList(items, type) {
    if (!items || !items.length) return '';
    var cls = type === 'bullish' ? 'ns-factor-bull' : (type === 'bearish' ? 'ns-factor-bear' : 'ns-factor-offset');
    var html = '<ul class="ns-factor-list ' + cls + '">';
    for (var i = 0; i < items.length; i++) {
      html += '<li>' + escapeHtml(items[i]) + '</li>';
    }
    html += '</ul>';
    return html;
  }

  function renderInsightList(el, items) {
    if (!el) return;
    if (!items || items.length === 0) {
      el.innerHTML = '<div class="ns-loading">None available</div>';
      return;
    }
    el.innerHTML = '<ul class="ns-insight-list">' +
      items.map(function(i) { return '<li>' + escapeHtml(i) + '</li>'; }).join('') +
      '</ul>';
  }

  // ── Render: Macro card ────────────────────────────────────────

  function renderMacro(macro) {
    if (!macro) return;
    var fmt = function(v, decimals) {
      return v != null ? Number(v).toFixed(decimals != null ? decimals : 2) : '—';
    };
    if (macroVix) macroVix.textContent = fmt(macro.vix, 1);
    if (macro10y) macro10y.textContent = fmt(macro.us_10y_yield) + '%';
    if (macro2y) macro2y.textContent = fmt(macro.us_2y_yield) + '%';
    if (macroSpread) {
      macroSpread.textContent = fmt(macro.yield_curve_spread, 3);
      if (macro.yield_curve_spread != null && macro.yield_curve_spread < 0) {
        macroSpread.style.color = 'var(--danger)';
      }
    }
    if (macroOil) macroOil.textContent = '$' + fmt(macro.oil_wti, 1);
    if (macroFed) macroFed.textContent = fmt(macro.fed_funds_rate) + '%';
    if (macroStress) {
      var level = (macro.stress_level || 'unknown').toLowerCase();
      macroStress.textContent = 'Stress: ' + level.charAt(0).toUpperCase() + level.slice(1);
      macroStress.className = 'ns-stress-badge ' + level;
    }

    // Freshness badges via shared market context
    var mc = window.BenTradeMarketContext;
    if (mc && macro._freshness) {
      var norm = mc.normalizeFromNsMacro(macro);
      if (norm) {
        mc.setContext(norm);
        if (freshVix) freshVix.innerHTML = mc.freshnessTag(norm.vix);
        if (fresh10y) fresh10y.innerHTML = mc.freshnessTag(norm.ten_year_yield);
        if (fresh2y) fresh2y.innerHTML = mc.freshnessTag(norm.two_year_yield);
        if (freshOil) freshOil.innerHTML = mc.freshnessTag(norm.oil_wti);
        if (freshFed) freshFed.innerHTML = mc.freshnessTag(norm.fed_funds_rate);
      }
    }
  }

  // ── Render: Summary Metrics (derived from items) ──────────────

  function renderSummaryMetrics(items) {
    if (!items || !items.length) return;
    var total = items.length;
    var bullish = 0, bearish = 0, neutral = 0, mixed = 0;
    var categoryMap = {};
    var now = Date.now();
    var recent24h = 0;

    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var sl = (it.sentiment_label || '').toLowerCase();
      if (sl === 'bullish') bullish++;
      else if (sl === 'bearish') bearish++;
      else if (sl === 'neutral') neutral++;
      else mixed++;

      var cat = it.category || 'other';
      categoryMap[cat] = (categoryMap[cat] || 0) + 1;

      if (it.published_at) {
        var pubTime = new Date(it.published_at).getTime();
        if (!isNaN(pubTime) && (now - pubTime) < 86400000) recent24h++;
      }
    }

    if (metricTotal) metricTotal.textContent = total;
    if (metricBullish) metricBullish.textContent = bullish;
    if (metricBearish) metricBearish.textContent = bearish;
    if (metricNeutral) metricNeutral.textContent = neutral;
    if (metricMixed) metricMixed.textContent = mixed;
    if (metricRecent) metricRecent.textContent = recent24h;

    // Category distribution pills
    if (categoryBarEl) {
      var cats = Object.keys(categoryMap).sort(function(a, b) { return categoryMap[b] - categoryMap[a]; });
      categoryBarEl.innerHTML = cats.map(function(c) {
        return '<span class="ns-cat-pill">' + escapeHtml(c) +
               '<span class="ns-cat-count">' + categoryMap[c] + '</span></span>';
      }).join('');
    }
  }

  // ── Render: Source Health ──────────────────────────────────────

  function renderSourceHealth(sources) {
    if (!sourceHealthEl) return;
    if (!sources || !sources.length) {
      sourceHealthEl.innerHTML = '<div class="ns-loading">No source info</div>';
      return;
    }

    var html = '<table class="ns-source-table">' +
      '<thead><tr><th>Source</th><th>Status</th><th>Items</th><th>Fetched</th></tr></thead>' +
      '<tbody>';
    for (var i = 0; i < sources.length; i++) {
      var s = sources[i];
      var status = (s.status || 'unknown').toLowerCase();
      html += '<tr>' +
        '<td>' + escapeHtml(s.source) + '</td>' +
        '<td><span class="ns-src-status ' + escapeHtml(status) + '">' + escapeHtml(status) + '</span></td>' +
        '<td>' + (s.item_count != null ? s.item_count : '—') + '</td>' +
        '<td>' + (s.last_fetched ? formatTime(s.last_fetched) : '—') + '</td>' +
      '</tr>';
    }
    html += '</tbody></table>';
    sourceHealthEl.innerHTML = html;
  }

  // ── Render: Headline feed ─────────────────────────────────────

  function renderHeadlines(items) {
    if (!headlineFeed) return;
    if (!items || items.length === 0) {
      headlineFeed.innerHTML = '<div class="ns-loading">No headlines available</div>';
      if (itemCountEl) itemCountEl.textContent = '';
      return;
    }
    if (itemCountEl) itemCountEl.textContent = items.length + ' headlines';

    headlineFeed.innerHTML = items
      .map(function(item) {
        var headlineHtml = item.url
          ? '<a href="' + escapeHtml(item.url) + '" target="_blank" rel="noopener noreferrer">' +
            escapeHtml(item.headline) + '</a>'
          : escapeHtml(item.headline);

        var symbolTags = (item.symbols || [])
          .slice(0, 5)
          .map(function(s) { return '<span class="ns-symbol-tag">' + escapeHtml(s) + '</span>'; })
          .join('');

        return '<div class="ns-headline-item">' +
          '<div class="ns-headline-top">' +
            '<div class="ns-headline-text">' + headlineHtml + '</div>' +
          '</div>' +
          '<div class="ns-headline-meta">' +
            '<span class="ns-badge ns-badge-source">' + escapeHtml(item.source) + '</span>' +
            '<span class="ns-badge ns-badge-category">' + escapeHtml(item.category) + '</span>' +
            '<span class="ns-badge ns-badge-sentiment ' + escapeHtml(item.sentiment_label) + '">' +
              escapeHtml(item.sentiment_label) + '</span>' +
            '<span class="ns-headline-time">' + formatTime(item.published_at) + '</span>' +
            (symbolTags ? '<span class="ns-headline-symbols">' + symbolTags + '</span>' : '') +
          '</div>' +
        '</div>';
      })
      .join('');
  }

  // ── Render: Component breakdown ───────────────────────────────

  function renderComponentBreakdown(engine, items) {
    if (!breakdownEl) return;

    // If engine components are available, render them
    if (engine && engine.components) {
      var expl = engine.explanation || {};
      var names = [
        'headline_sentiment', 'negative_pressure', 'narrative_severity',
        'source_agreement', 'macro_stress', 'recency_pressure'
      ];

      // Build lookup from explanation component_analysis
      var compAnalysis = {};
      if (expl.component_analysis) {
        for (var a = 0; a < expl.component_analysis.length; a++) {
          compAnalysis[expl.component_analysis[a].component] = expl.component_analysis[a];
        }
      }

      var html = '';
      for (var i = 0; i < names.length; i++) {
        var name = names[i];
        var comp = engine.components[name];
        if (!comp) continue;
        var s = comp.score != null ? comp.score : 0;
        var weight = engine.weights ? engine.weights[name] : null;
        var signals = (comp.signals || []).join(' \u00b7 ');
        var ca = compAnalysis[name];
        var displayName = ca ? ca.display_name : name;
        var tooltip = ca ? ca.tooltip : '';
        var interpretation = ca ? ca.interpretation : '';
        var contribution = ca ? ca.contribution : 'neutral';
        var contribCls = 'ns-contrib-' + contribution;

        html += '<div class="ns-breakdown-comp">' +
          '<div class="ns-breakdown-comp-header">' +
            '<span class="ns-breakdown-comp-name" title="' + escapeHtml(tooltip) + '">' +
              escapeHtml(displayName) +
              '<span class="ns-comp-contrib ' + contribCls + '"></span>' +
              (weight != null ? ' <span style="font-weight:400;color:var(--muted);font-size:10px;">(w:' + weight + ')</span>' : '') +
            '</span>' +
            '<span class="ns-breakdown-comp-score" style="color:' + scoreColor100(s) + '">' + s.toFixed(1) + '</span>' +
          '</div>' +
          '<div class="ns-breakdown-comp-bar">' +
            '<div class="ns-breakdown-comp-fill" style="width:' + s + '%;background:' + barColor(s) + '"></div>' +
          '</div>' +
          (interpretation ? '<div class="ns-comp-interp">' + escapeHtml(interpretation) + '</div>' : '') +
          (signals ? '<div class="ns-breakdown-signals">' + escapeHtml(signals) + '</div>' : '') +
        '</div>';
      }

      // ── Score logic section ────────────────────────────────
      if (expl.score_logic) {
        var sl = expl.score_logic;
        html += '<div class="ns-score-logic">';
        html += '<div class="ns-engine-section-title">SCORE CONTRIBUTORS</div>';
        if (sl.positive_contributors && sl.positive_contributors.length) {
          html += '<div class="ns-contrib-group"><span class="ns-contrib-label ns-contrib-positive">\u25B2 Positive</span> ' +
                  escapeHtml(sl.positive_contributors.join(', ')) + '</div>';
        }
        if (sl.negative_contributors && sl.negative_contributors.length) {
          html += '<div class="ns-contrib-group"><span class="ns-contrib-label ns-contrib-negative">\u25BC Negative</span> ' +
                  escapeHtml(sl.negative_contributors.join(', ')) + '</div>';
        }
        if (sl.balancing_contributors && sl.balancing_contributors.length) {
          html += '<div class="ns-contrib-group"><span class="ns-contrib-label ns-contrib-balancing">\u25C6 Balancing</span> ' +
                  escapeHtml(sl.balancing_contributors.join(', ')) + '</div>';
        }
        html += '</div>';
      }

      // ── Signal quality ─────────────────────────────────────
      if (expl.signal_quality) {
        html += '<div class="ns-signal-quality">' +
                '<span class="ns-signal-quality-badge ns-sq-' + (expl.signal_quality.strength || 'moderate').toLowerCase() + '">' +
                  escapeHtml(expl.signal_quality.strength || '') + '</span> ' +
                '<span class="ns-signal-quality-text">' + escapeHtml(expl.signal_quality.explanation || '') + '</span>' +
                '</div>';
      }

      // ── Trader takeaway ────────────────────────────────────
      if (expl.trader_takeaway) {
        html += '<div class="ns-engine-takeaway">' +
                '<div class="ns-engine-section-title">TRADER TAKEAWAY</div>' +
                '<div class="ns-engine-takeaway-text">' + escapeHtml(expl.trader_takeaway) + '</div>' +
                '</div>';
      }

      breakdownEl.innerHTML = html || '<div class="ns-loading">No components</div>';
      return;
    }

    // Fallback: show source-derived summary when engine is unavailable
    if (items && items.length) {
      var sentimentCounts = { bullish: 0, bearish: 0, neutral: 0, mixed: 0 };
      for (var j = 0; j < items.length; j++) {
        var sl = (items[j].sentiment_label || 'neutral').toLowerCase();
        sentimentCounts[sl] = (sentimentCounts[sl] || 0) + 1;
      }
      var total = items.length || 1;
      var fallbackHtml = '<div style="padding:4px 0;font-size:11px;color:var(--muted);margin-bottom:8px;">' +
        'Engine unavailable — showing source-derived summary</div>';
      var labels = ['bullish', 'bearish', 'neutral', 'mixed'];
      var colors = { bullish: '#00e676', bearish: '#ff4f66', neutral: 'var(--cyan)', mixed: 'var(--warn)' };
      for (var k = 0; k < labels.length; k++) {
        var pct = (sentimentCounts[labels[k]] / total * 100);
        fallbackHtml += '<div class="ns-breakdown-comp">' +
          '<div class="ns-breakdown-comp-header">' +
            '<span class="ns-breakdown-comp-name">' + labels[k].charAt(0).toUpperCase() + labels[k].slice(1) + '</span>' +
            '<span class="ns-breakdown-comp-score" style="color:' + colors[labels[k]] + '">' +
              sentimentCounts[labels[k]] + ' (' + pct.toFixed(0) + '%)</span>' +
          '</div>' +
          '<div class="ns-breakdown-comp-bar">' +
            '<div class="ns-breakdown-comp-fill" style="width:' + pct + '%;background:' + colors[labels[k]] + '"></div>' +
          '</div>' +
        '</div>';
      }
      breakdownEl.innerHTML = fallbackHtml;
    } else {
      breakdownEl.innerHTML = '<div class="ns-loading">No data available</div>';
    }
  }

  // ── Orchestration: render all base data ────────────────────────

  function renderBaseData(data) {
    if (!data) return;    console.log('[NEWS_ENGINE] renderBaseData', {
      keys: Object.keys(data),
      hasEngine: !!data.internal_engine,
      engineScore: data.internal_engine ? data.internal_engine.score : null,
      itemCount: data.items ? data.items.length : 0,
      hasMacro: !!data.macro_context,
    });    renderEngine(data.internal_engine);
    renderMacro(data.macro_context);
    renderHeadlines(data.items);
    renderSummaryMetrics(data.items);
    renderSourceHealth(data.source_freshness);
    renderComponentBreakdown(data.internal_engine, data.items);
    updateLastUpdated();
  }

  // ── Refresh button state ──────────────────────────────────────

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

  // ── Base data fetch (no model) ────────────────────────────────

  async function fetchBaseData(force) {
    var url = force ? API_URL + '?force=true' : API_URL;
    var resp = await fetch(url);
    if (!resp.ok) {
      var text = await resp.text();
      throw new Error('HTTP ' + resp.status + ': ' + (text || 'Request failed').slice(0, 300));
    }
    return resp.json();
  }

  function loadData(force) {
    if (_destroyed) return;
    setError(null);

    var showSpinner = !!force || !(_cache && _cache.get(CACHE_KEY) && _cache.get(CACHE_KEY).isLoaded);

    if (_cache) {
      if (showSpinner) setRefreshBtnState(true);
      _cache.fetchWithCache(
        CACHE_KEY,
        function() { return fetchBaseData(!!force); },
        {
          onCached: function(data) { renderBaseData(data); },
          onSuccess: function(data) {
            renderBaseData(data);
            setRefreshBtnState(false);
          },
          onError: function(err) {
            console.error('[news-sentiment] refresh error:', err);
            setError(String(err.message || err));
            setRefreshBtnState(false);
          }
        },
        !!force
      );
    } else {
      if (showSpinner) setRefreshBtnState(true);
      fetchBaseData(!!force)
        .then(function(data) { renderBaseData(data); })
        .catch(function(err) {
          console.error('[news-sentiment] load error:', err);
          setError(String(err.message || err));
        })
        .finally(function() { setRefreshBtnState(false); });
    }
  }

  // ── Model analysis (manual trigger) ───────────────────────────

  function setModelBtnState(running) {
    // Find the current model button (may have been replaced by innerHTML)
    var btn = scope.querySelector('#nsRunModelBtn');
    if (!btn) return;
    if (running) {
      btn.classList.add('btn-refreshing');
      btn.innerHTML = '<span class="btn-spinner"></span>Running\u2026';
      btn.disabled = true;
    } else {
      btn.classList.remove('btn-refreshing');
      btn.disabled = false;
    }
  }

  async function triggerModelAnalysis() {
    if (_destroyed) return;
    setModelBtnState(true);

    // Use centralized model timeout from API client (single source of truth)
    var CLIENT_TIMEOUT = (window.BenTradeApi && window.BenTradeApi.MODEL_TIMEOUT_MS) || 185000;
    var t0 = performance.now();
    console.log('[NEWS_MODEL] request_start', {
      endpoint: MODEL_URL, method: 'POST',
      timeout_ms: CLIENT_TIMEOUT,
      timestamp: new Date().toISOString(),
    });

    var controller = new AbortController();
    var timerFired = false;
    var timer = setTimeout(function() {
      timerFired = true;
      console.warn('[NEWS_MODEL] abort_timer_fired', {
        elapsed_ms: Math.round(performance.now() - t0),
        timeout_ms: CLIENT_TIMEOUT,
      });
      controller.abort();
    }, CLIENT_TIMEOUT);

    try {
      var resp = await fetch(MODEL_URL, { method: 'POST', signal: controller.signal });
      var tHeaders = performance.now();
      console.log('[NEWS_MODEL] response_headers', {
        status: resp.status, ok: resp.ok,
        elapsed_ms: Math.round(tHeaders - t0),
      });

      if (!resp.ok) {
        var text = await resp.text();
        console.error('[NEWS_MODEL] http_error', { status: resp.status, body: text.slice(0, 300) });
        throw new Error('HTTP ' + resp.status + ': ' + (text || 'Model request failed').slice(0, 300));
      }

      var result = await resp.json();
      var tParsed = performance.now();
      console.log('[NEWS_MODEL] body_parsed', { elapsed_ms: Math.round(tParsed - t0) });

      var modelData = result.model_analysis || null;
      var errorInfo = result.error || null;
      console.log('[NEWS_MODEL] result', {
        hasModel: !!modelData,
        score: modelData ? modelData.score : null,
        errorKind: errorInfo ? errorInfo.kind : null,
        total_ms: Math.round(performance.now() - t0),
      });

      // Cache model result separately (only if successful)
      if (_cache && modelData) {
        _cache.set(MODEL_CACHE_KEY, modelData);
      }

      renderModel(modelData);
      if (!modelData) {
        // Use specific error message from backend if available
        var errMsg = (errorInfo && errorInfo.message)
          ? errorInfo.message
          : 'Model returned no result. The LLM may be unavailable.';
        var errKind = (errorInfo && errorInfo.kind) ? ' (' + errorInfo.kind + ')' : '';
        renderModelError(errMsg + errKind);
      }
    } catch (err) {
      var elapsed = Math.round(performance.now() - t0);
      console.error('[NEWS_MODEL] failure', { error: err.message, name: err.name, elapsed_ms: elapsed, timerFired: timerFired });

      var msg;
      if (err.name === 'AbortError') {
        var timeoutSec = Math.round(CLIENT_TIMEOUT / 1000);
        msg = 'Model request timed out after ' + timeoutSec + 's. Is the local LLM running?';
      } else {
        msg = String(err.message || 'Model analysis failed');
      }
      renderModelError(msg);
    } finally {
      clearTimeout(timer);
      setModelBtnState(false);
      console.log('[NEWS_MODEL] lifecycle_complete', { total_ms: Math.round(performance.now() - t0) });
    }
  }

  // ── Init & cleanup ────────────────────────────────────────────

  // 1. Render cached base data immediately if available
  var _hadCached = false;
  if (_cache) {
    var cachedBase = _cache.get(CACHE_KEY);
    console.log('[NEWS_ENGINE] init cache check', {
      hasCacheStore: true,
      hasCachedEntry: !!cachedBase,
      isLoaded: cachedBase ? cachedBase.isLoaded : false,
      hasData: cachedBase ? cachedBase.data != null : false,
      hasEngine: cachedBase && cachedBase.data ? !!cachedBase.data.internal_engine : false,
      cacheKeys: cachedBase && cachedBase.data ? Object.keys(cachedBase.data) : [],
    });
    if (cachedBase && cachedBase.isLoaded && cachedBase.data) {
      renderBaseData(cachedBase.data);
      _hadCached = true;
    }

    // Also restore cached model result if available
    var cachedModel = _cache.get(MODEL_CACHE_KEY);
    if (cachedModel && cachedModel.isLoaded && cachedModel.data) {
      renderModel(cachedModel.data);
    } else {
      renderModelNotRun();
    }
  } else {
    renderModelNotRun();
  }

  // 2. Bind refresh button
  if (refreshBtn) {
    refreshBtn.addEventListener('click', function() { loadData(true); });
  }

  // 3. Bind model analysis button
  if (runModelBtn) {
    runModelBtn.addEventListener('click', triggerModelAnalysis);
  }

  // 4. Fetch fresh base data (background if cache was rendered)
  loadData(false);

  // 5. Auto-refresh every 5 minutes (background, base data only — no model)
  _autoRefreshTimer = setInterval(function() {
    if (!_destroyed) loadData(false);
  }, 300000);

  // Return cleanup function for router
  return function cleanup() {
    _destroyed = true;
    if (_autoRefreshTimer) {
      clearInterval(_autoRefreshTimer);
      _autoRefreshTimer = null;
    }
  };
};
