/* ===================================================================
   On-Demand Company Evaluator — Page Controller
   Pattern: window.BenTradePages.initOnDemandEvaluator(rootEl)
   =================================================================== */
window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initOnDemandEvaluator = function initOnDemandEvaluator(rootEl) {
  var doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  var scope = rootEl || doc;

  // === API CONFIG ===
  var API_BASE = '/api/company-evaluator/on-demand';
  var POLL_INTERVAL_MS = 2000;

  // Set to true to use mock data before backend exists
  var MOCK_MODE = false;

  // === STATE ===
  var currentJobId = null;
  var pollTimer = null;
  var currentRawData = null;

  // === DOM REFS ===
  var form = scope.querySelector('#ode-form');
  var symbolInput = scope.querySelector('#ode-symbol-input');
  var analyzeBtn = scope.querySelector('#ode-analyze-btn');
  var loadingEl = scope.querySelector('#ode-loading');
  var loadingSymbol = scope.querySelector('#ode-loading-symbol');
  var loadingStep = scope.querySelector('#ode-loading-step');
  var loadingFill = scope.querySelector('#ode-loading-progress-fill');
  var loadingPercent = scope.querySelector('#ode-loading-percent');
  var loadingCompleted = scope.querySelector('#ode-loading-completed');
  var cancelBtn = scope.querySelector('#ode-loading-cancel');
  var errorEl = scope.querySelector('#ode-error');
  var errorTitle = scope.querySelector('#ode-error-title');
  var errorMessage = scope.querySelector('#ode-error-message');
  var retryBtn = scope.querySelector('#ode-error-retry');
  var resultsEl = scope.querySelector('#ode-results');
  var rawTabs = scope.querySelectorAll('.ode-raw-tab');
  var deepResearchBtn = scope.querySelector('#ode-deep-research-btn');
  var _currentResearchSymbol = null;

  // === EVENT LISTENERS ===
  if (form) form.addEventListener('submit', handleSubmit);
  if (cancelBtn) cancelBtn.addEventListener('click', handleCancel);
  if (retryBtn) retryBtn.addEventListener('click', handleRetry);
  _initDeepResearchButton();
  rawTabs.forEach(function(tab) {
    tab.addEventListener('click', function() { switchRawTab(tab); });
  });

  // === URL PARAM AUTO-RUN ===
  var _lastAutoSymbol = null;

  function _getSymbolFromUrl() {
    var hash = window.location.hash || '';
    var qIdx = hash.indexOf('?');
    if (qIdx === -1) return null;
    var qs = hash.substring(qIdx + 1);
    var params = new URLSearchParams(qs);
    var sym = params.get('symbol');
    if (!sym) return null;
    var normalized = sym.toUpperCase().trim();
    if (!/^[A-Z]{1,6}$/.test(normalized)) return null;
    return normalized;
  }

  function _autoRunAnalysis(symbol) {
    if (!symbolInput) return;
    symbolInput.value = symbol;
    // Clear URL param to prevent re-run on refresh
    var baseHash = (window.location.hash || '').split('?')[0];
    if (history.replaceState) {
      history.replaceState(null, '', window.location.pathname + window.location.search + baseHash);
    }
    // Trigger the form submit handler
    if (form && typeof form.requestSubmit === 'function') {
      form.requestSubmit();
    } else if (form) {
      form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
    }
  }

  function _onHashChange() {
    var sym = _getSymbolFromUrl();
    if (sym && sym !== _lastAutoSymbol) {
      _lastAutoSymbol = sym;
      _autoRunAnalysis(sym);
    }
  }

  window.addEventListener('hashchange', _onHashChange);

  // Initial check on page load
  var _initSymbol = _getSymbolFromUrl();
  if (_initSymbol) {
    _lastAutoSymbol = _initSymbol;
    _autoRunAnalysis(_initSymbol);
  }

  // === API FUNCTIONS ===
  function apiStartAnalysis(symbol) {
    return fetch(API_BASE + '/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: symbol.toUpperCase().trim() })
    }).then(function(resp) {
      if (!resp.ok) {
        return resp.json().catch(function() { return {}; }).then(function(body) {
          throw new Error(body.detail || body.error || 'HTTP ' + resp.status);
        });
      }
      return resp.json();
    });
  }

  function apiGetJobStatus(jobId) {
    return fetch(API_BASE + '/jobs/' + encodeURIComponent(jobId)).then(function(resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.json();
    });
  }

  function apiGetJobResult(jobId) {
    return fetch(API_BASE + '/jobs/' + encodeURIComponent(jobId) + '/result').then(function(resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.json();
    });
  }

  function apiCancelJob(jobId) {
    return fetch(API_BASE + '/jobs/' + encodeURIComponent(jobId), { method: 'DELETE' })
      .catch(function(err) { console.warn('Cancel request failed:', err); });
  }

  // === SUBMIT ===
  function handleSubmit(event) {
    event.preventDefault();
    var symbol = (symbolInput ? symbolInput.value : '').toUpperCase().trim();
    if (!symbol) return;

    if (!/^[A-Z]{1,6}$/.test(symbol)) {
      showError('Invalid Symbol', 'Symbol must be 1-6 uppercase letters (e.g., QCOM).');
      return;
    }

    _disableDeepResearchButton();
    showLoading(symbol);

    if (MOCK_MODE) {
      runMockAnalysis(symbol);
      return;
    }

    apiStartAnalysis(symbol).then(function(job) {
      currentJobId = job.job_id;
      startPolling(job.job_id);
    }).catch(function(err) {
      showError('Failed to Start Analysis', err.message);
    });
  }

  // === POLLING ===
  function startPolling(jobId) {
    stopPolling();
    pollTimer = setInterval(function() {
      apiGetJobStatus(jobId).then(function(status) {
        updateLoadingState(status);
        if (status.status === 'complete') {
          stopPolling();
          apiGetJobResult(jobId).then(function(result) {
            showResults(result);
          }).catch(function(err) {
            showError('Failed to Load Results', err.message);
          });
        } else if (status.status === 'failed') {
          stopPolling();
          showError('Analysis Failed', status.error || 'The analysis job failed for an unknown reason.');
        }
      }).catch(function(err) {
        console.error('Polling error:', err);
        stopPolling();
        showError('Connection Lost', 'Could not get job status. Please try again.');
      });
    }, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // === CANCEL / RETRY ===
  function handleCancel() {
    if (currentJobId) {
      apiCancelJob(currentJobId);
      currentJobId = null;
    }
    stopPolling();
    hideAllStates();
  }

  function handleRetry() {
    hideAllStates();
    if (symbolInput) symbolInput.focus();
  }

  // === STATE MANAGEMENT ===
  function hideAllStates() {
    if (loadingEl) loadingEl.hidden = true;
    if (errorEl) errorEl.hidden = true;
    if (resultsEl) resultsEl.hidden = true;
    var bp = scope.querySelector('#ode-business-profile');
    if (bp) bp.hidden = true;
  }

  function showLoading(symbol) {
    hideAllStates();
    if (loadingEl) loadingEl.hidden = false;
    if (loadingSymbol) loadingSymbol.textContent = symbol;
    if (loadingStep) loadingStep.textContent = 'Initializing analysis...';
    if (loadingFill) loadingFill.style.width = '0%';
    if (loadingPercent) loadingPercent.textContent = '0%';
    if (loadingCompleted) loadingCompleted.innerHTML = '';
    if (analyzeBtn) analyzeBtn.disabled = true;
  }

  function updateLoadingState(status) {
    if (!status || !status.progress) return;
    var progress = status.progress;

    if (loadingStep) loadingStep.textContent = progress.current_step || 'Working...';
    if (loadingFill) loadingFill.style.width = (progress.percent || 0) + '%';
    if (loadingPercent) loadingPercent.textContent = (progress.percent || 0) + '%';

    if (loadingCompleted && status.completed_steps) {
      loadingCompleted.innerHTML = status.completed_steps.map(function(step) {
        return '<div class="ode-loading-completed-item">' +
          '<span class="ode-loading-completed-check">\u2713</span> ' +
          _esc(step) + '</div>';
      }).join('');
    }
  }

  function showError(title, message) {
    hideAllStates();
    if (errorEl) errorEl.hidden = false;
    if (errorTitle) errorTitle.textContent = title;
    if (errorMessage) errorMessage.textContent = message;
    if (analyzeBtn) analyzeBtn.disabled = false;
  }

  // === RESULTS RENDERING ===
  function showResults(data) {
    hideAllStates();
    if (resultsEl) resultsEl.hidden = false;
    if (analyzeBtn) analyzeBtn.disabled = false;

    renderCompanyHeader(data);
    renderDistressBadge(data);
    // Mount price chart below header
    if (window.BenTradeComponents && window.BenTradeComponents.mountPriceChart && data.symbol) {
      window.BenTradeComponents.mountPriceChart('ode-chart-container', data.symbol);
    }
    renderBusinessProfile(data);
    renderQualityIndicators(data);
    renderScoreCards(data);
    renderPillars(data.evaluation);
    renderSmartMoney(data.smart_money);
    renderValuationModels(data);
    renderEntryAndTargets(data);
    renderThesis(data.llm_recommendation);
    currentRawData = data.raw_financials || null;
    renderRawFinancials();
    renderMetadataFooter(data);

    // Enable deep research button now that we have data
    var sym = (data.company && data.company.symbol) || data.symbol;
    if (sym) _enableDeepResearchButton(sym);
  }

  function renderCompanyHeader(data) {
    var container = scope.querySelector('#ode-company-header');
    if (!container) return;
    var company = data.company || {};

    // Price fallback chain: company.price → dcf.current_price → entry_analysis.current_price
    var price = company.price;
    if (price == null && data.dcf) price = data.dcf.current_price;
    if (price == null && data.entry_analysis) price = data.entry_analysis.current_price;

    // Sector fallback: prefer clean sector from comps over raw SIC description
    var sector = (data.comps && data.comps.subject ? data.comps.subject.sector : null) || company.sector || '\u2014';

    container.innerHTML =
      '<div><div class="ode-company-symbol">' + _esc(company.symbol || '\u2014') + '</div></div>' +
      '<div class="ode-company-info">' +
        '<div class="ode-company-name">' + _esc(company.name || '') + '</div>' +
        '<div class="ode-company-meta">' +
          '<span>' + _esc(sector) + '</span>' +
          '<span>' + _esc(company.exchange || '\u2014') + '</span>' +
          (company.employees ? '<span>' + fmtNum(company.employees) + ' employees</span>' : '') +
          (company.ceo ? '<span>CEO: ' + _esc(company.ceo) + '</span>' : '') +
        '</div>' +
      '</div>' +
      '<div class="ode-company-price">' +
        '<div class="ode-company-price-value">' + (price != null ? '$' + price.toFixed(2) : '\u2014') + '</div>' +
        '<div class="ode-company-market-cap">Mkt Cap: $' + fmtLarge(company.market_cap) + '</div>' +
      '</div>';
  }

  // === DISTRESS RISK BADGE ===
  function renderDistressBadge(data) {
    var badge = scope.querySelector('#ode-distress-badge');
    if (!badge) return;

    // Try real API structure first, then mock/legacy
    var altmanZ = null;
    var breakdowns = data && data.evaluation ? data.evaluation.pillar_breakdowns : null;
    if (breakdowns && breakdowns.operational_health) {
      var oh = breakdowns.operational_health;
      if (oh.metrics && oh.metrics.altman_z != null) {
        altmanZ = oh.metrics.altman_z;
      } else if (oh.components && oh.components.altman_z) {
        altmanZ = oh.components.altman_z.value;
      }
    }

    if (altmanZ === null || altmanZ === undefined) {
      badge.className = 'ode-distress-badge unknown';
      badge.innerHTML = '<span class="ode-distress-badge-icon">\u25CF</span>Distress: N/A';
      return;
    }

    var z = Number(altmanZ);
    var level, label;
    if (z >= 2.99) {
      level = 'safe';
      label = 'Safe (Z=' + z.toFixed(2) + ')';
    } else if (z >= 1.81) {
      level = 'watch';
      label = 'Watch (Z=' + z.toFixed(2) + ')';
    } else {
      level = 'distress';
      label = 'Distress (Z=' + z.toFixed(2) + ')';
    }

    badge.className = 'ode-distress-badge ' + level;
    badge.innerHTML = '<span class="ode-distress-badge-icon">\u25CF</span>' + _esc(label);
  }

  // === QUALITY INDICATORS PANEL ===
  function renderQualityIndicators(data) {
    var section = scope.querySelector('#ode-quality-indicators');
    var grid = scope.querySelector('#ode-quality-grid');
    if (!section || !grid) return;

    var breakdowns = data && data.evaluation ? data.evaluation.pillar_breakdowns : null;
    if (!breakdowns) {
      section.hidden = true;
      return;
    }

    // Helper to get metric value from real or mock API structure
    function _getMetric(pillar, metric) {
      var bd = breakdowns[pillar];
      if (!bd) return null;
      if (bd.metrics && bd.metrics[metric] != null) return bd.metrics[metric];
      if (bd.components && bd.components[metric]) return bd.components[metric].value;
      return null;
    }

    var cards = [];

    // Capital Quality from ROIC vs WACC spread
    var spread = _getMetric('capital_allocation', 'roic_wacc_spread');
    if (spread !== null && spread !== undefined) {
      var pct = spread * 100;
      var level, label;
      if (pct >= 10) { level = 'excellent'; label = 'Excellent'; }
      else if (pct >= 5) { level = 'good'; label = 'Good'; }
      else if (pct >= 0) { level = 'adequate'; label = 'Adequate'; }
      else { level = 'poor'; label = 'Destroying Value'; }

      cards.push({
        label: 'Capital Quality',
        value: label,
        detail: 'ROIC ' + (pct >= 0 ? 'beats' : 'lags') + ' WACC by ' + Math.abs(pct).toFixed(1) + ' pts',
        level: level
      });
    }

    // Smart Money from insider activity (try smart_money first, fallback to capital_allocation metrics)
    var sm = data.smart_money;
    var ia = sm ? sm.insider_activity : null;
    var insiderScore = (ia && ia.score != null) ? Number(ia.score) : null;
    // Fallback: capital_allocation pillar sometimes has insider_score
    if (insiderScore === null) {
      var capInsider = _getMetric('capital_allocation', 'insider_score');
      if (capInsider != null) insiderScore = Number(capInsider);
    }
    if (insiderScore !== null && !isNaN(insiderScore)) {
      var smLevel, smLabel;
      if (insiderScore >= 70) { smLevel = 'excellent'; smLabel = 'Strong Buying'; }
      else if (insiderScore >= 55) { smLevel = 'good'; smLabel = 'Net Buying'; }
      else if (insiderScore >= 45) { smLevel = 'adequate'; smLabel = 'Neutral'; }
      else { smLevel = 'poor'; smLabel = 'Net Selling'; }

      cards.push({
        label: 'Smart Money',
        value: smLabel,
        detail: 'Insider score ' + insiderScore.toFixed(0) + '/100',
        level: smLevel
      });
    }

    // Predictability from revenue stability
    var stability = _getMetric('business_quality', 'rev_stability');
    if (stability !== null && stability !== undefined) {
      var stabPct = stability * 100;
      var stabLevel, stabLabel;
      if (stabPct >= 80) { stabLevel = 'excellent'; stabLabel = 'High'; }
      else if (stabPct >= 60) { stabLevel = 'good'; stabLabel = 'Moderate'; }
      else if (stabPct >= 40) { stabLevel = 'adequate'; stabLabel = 'Variable'; }
      else { stabLevel = 'poor'; stabLabel = 'Volatile'; }

      cards.push({
        label: 'Predictability',
        value: stabLabel,
        detail: 'Revenue stability ' + stabPct.toFixed(0) + '%',
        level: stabLevel
      });
    }

    // Cash Quality from cash conversion
    var cashConv = _getMetric('operational_health', 'cash_conversion');
    if (cashConv !== null && cashConv !== undefined) {
      var ratio = Number(cashConv);
      var cashLevel, cashLabel;
      if (ratio >= 1.0) { cashLevel = 'excellent'; cashLabel = 'Strong'; }
      else if (ratio >= 0.7) { cashLevel = 'good'; cashLabel = 'Adequate'; }
      else if (ratio >= 0.4) { cashLevel = 'adequate'; cashLabel = 'Weak'; }
      else { cashLevel = 'poor'; cashLabel = 'Poor'; }

      cards.push({
        label: 'Cash Quality',
        value: cashLabel,
        detail: 'OCF/NI ratio ' + ratio.toFixed(2),
        level: cashLevel
      });
    }

    // Piotroski F-Score
    var piotroski = data && data.piotroski_f_score ? data.piotroski_f_score : null;
    if (piotroski) {
      if (piotroski.ok) {
        var pScore = piotroski.score;
        var pLabel = piotroski.label;
        var pLevel;
        if (pLabel === 'STRONG') pLevel = 'excellent';
        else if (pLabel === 'AVERAGE') pLevel = 'good';
        else pLevel = 'poor';

        cards.push({
          label: 'Piotroski F-Score',
          value: pScore + '/9 ' + _capitalize(pLabel),
          detail: piotroski.interpretation || (pScore + ' of 9 quality checks passed'),
          level: pLevel,
          expandable: true,
          expandKey: 'piotroski',
          piotroskiData: piotroski
        });
      } else {
        cards.push({
          label: 'Piotroski F-Score',
          value: 'N/A',
          detail: piotroski.error || 'Insufficient historical data',
          level: 'unknown',
          expandable: false
        });
      }
    }

    if (cards.length === 0) {
      section.hidden = true;
      return;
    }

    section.hidden = false;
    grid.innerHTML = cards.map(function(c, idx) {
      var expandCls = c.expandable ? ' expandable' : '';
      var expandAttrs = c.expandable
        ? ' data-expand-key="' + c.expandKey + '" data-card-idx="' + idx + '"'
        : '';
      var chevron = c.expandable ? '<span class="ode-quality-card-chevron">\u25BE</span>' : '';

      return '<div class="ode-quality-card ' + c.level + expandCls + '"' + expandAttrs + '>' +
        '<div class="ode-quality-card-header">' +
          '<div>' +
            '<div class="ode-quality-card-label">' + _esc(c.label) + '</div>' +
            '<div class="ode-quality-card-value">' + _esc(c.value) + '</div>' +
            '<div class="ode-quality-card-detail">' + _esc(c.detail) + '</div>' +
          '</div>' +
          chevron +
        '</div>' +
      '</div>';
    }).join('');

    // Wire up click handlers for expandable cards
    grid.querySelectorAll('.ode-quality-card.expandable').forEach(function(el) {
      el.addEventListener('click', function() {
        var expandKey = el.dataset.expandKey;
        if (expandKey === 'piotroski') {
          var cardIdx = parseInt(el.dataset.cardIdx, 10);
          var cardData = cards[cardIdx];
          if (cardData && cardData.piotroskiData) {
            _showPiotroskiBreakdown(cardData.piotroskiData);
          }
        }
      });
    });
  }

  // === PIOTROSKI BREAKDOWN MODAL ===
  function _showPiotroskiBreakdown(piotroski) {
    if (!piotroski || !piotroski.checks) return;

    // Remove existing modal if any
    var existing = document.getElementById('ode-piotroski-modal');
    if (existing) existing.remove();

    var overlay = document.createElement('div');
    overlay.className = 'ode-modal-overlay';
    overlay.id = 'ode-piotroski-modal';

    var checksHtml = Object.values(piotroski.checks).map(function(check) {
      var cls = check.passed ? 'passed' : 'failed';
      var icon = check.passed ? '\u2713' : '\u2717';
      return '<div class="ode-piotroski-check ' + cls + '">' +
        '<span class="ode-piotroski-check-icon">' + icon + '</span>' +
        '<div class="ode-piotroski-check-body">' +
          '<div class="ode-piotroski-check-label">' + _esc(check.label) + '</div>' +
          '<div class="ode-piotroski-check-details">' + _esc(check.details || '') + '</div>' +
        '</div>' +
      '</div>';
    }).join('');

    overlay.innerHTML =
      '<div class="ode-modal" role="dialog" aria-labelledby="piotroski-modal-title">' +
        '<div class="ode-modal-header">' +
          '<div class="ode-modal-title" id="piotroski-modal-title">' +
            'Piotroski F-Score: ' + piotroski.score + '/9 (' + _esc(piotroski.label) + ')' +
          '</div>' +
          '<button class="ode-modal-close" aria-label="Close">\u00D7</button>' +
        '</div>' +
        '<div class="ode-modal-body">' +
          '<div class="ode-piotroski-interpretation">' + _esc(piotroski.interpretation || '') + '</div>' +
          '<div class="ode-piotroski-checks">' + checksHtml + '</div>' +
          '<div class="ode-piotroski-footer">' +
            '<a href="https://en.wikipedia.org/wiki/Piotroski_F-score" target="_blank" rel="noopener">' +
              'About the Piotroski F-Score \u2192' +
            '</a>' +
          '</div>' +
        '</div>' +
      '</div>';

    document.body.appendChild(overlay);

    var close = function() { overlay.remove(); document.removeEventListener('keydown', escHandler); };
    overlay.querySelector('.ode-modal-close').addEventListener('click', close);
    overlay.addEventListener('click', function(e) {
      if (e.target === overlay) close();
    });

    var escHandler = function(e) {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('keydown', escHandler);
  }

  function _capitalize(s) {
    if (!s) return '';
    return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
  }

  function renderBusinessProfile(data) {
    var section = scope.querySelector('#ode-business-profile');
    var body = scope.querySelector('#ode-business-profile-body');
    if (!section || !body) return;

    var profile = data.business_profile;

    // Not generated / not available case
    if (!profile || !profile.ok) {
      section.hidden = false;
      var errorMsg = (profile && profile.error) ? profile.error : 'Business profile not available';
      body.innerHTML =
        '<div class="ode-bp-unavailable">' +
          _esc(errorMsg) +
          (profile && profile.llm_available === false ? '<div style="margin-top: 8px; font-size: 11px;">LM Studio may not be running on the model machine.</div>' : '') +
        '</div>';
      return;
    }

    section.hidden = false;

    var pitch = profile.elevator_pitch || '';
    var bm = profile.business_model || {};
    var moat = profile.moat || {};
    var comp = profile.competitive_landscape || {};
    var risks = Array.isArray(profile.key_risks) ? profile.key_risks : [];
    var confidence = profile.confidence || '\u2014';
    var generatedAt = profile.generated_at || '';

    var moatStrength = (moat.strength || 'NONE').toUpperCase();
    var moatBadgeMap = { 'STRONG': 'ode-bp-badge-strong', 'MODERATE': 'ode-bp-badge-moderate', 'WEAK': 'ode-bp-badge-weak', 'NONE': 'ode-bp-badge-none' };
    var moatBadgeClass = moatBadgeMap[moatStrength] || 'ode-bp-badge-none';

    var positionLabel = (comp.market_position || '\u2014').toUpperCase();
    var positionMap = { 'LEADER': 'ode-bp-position-leader', 'CHALLENGER': 'ode-bp-position-challenger', 'NICHE': 'ode-bp-position-niche', 'FOLLOWER': 'ode-bp-position-follower' };
    var positionClass = positionMap[positionLabel] || '';

    // Revenue streams as tags
    var revenueStreams = Array.isArray(bm.revenue_streams) ? bm.revenue_streams : [];
    var revenueTags = revenueStreams.length
      ? '<div class="ode-bp-tags">' + revenueStreams.map(function(s) { return '<span class="ode-bp-tag">' + _esc(s) + '</span>'; }).join('') + '</div>'
      : '<div class="ode-bp-field-value">\u2014</div>';

    // Competitors as tags
    var competitors = Array.isArray(comp.direct_competitors) ? comp.direct_competitors : [];
    var competitorTags = competitors.length
      ? '<div class="ode-bp-tags">' + competitors.map(function(c) { return '<span class="ode-bp-tag">' + _esc(c) + '</span>'; }).join('') + '</div>'
      : '<div class="ode-bp-field-value">\u2014</div>';

    // Moat signals as list
    var moatSignals = Array.isArray(moat.signals) ? moat.signals : [];
    var moatSignalsList = moatSignals.length
      ? '<ul class="ode-bp-list">' + moatSignals.map(function(s) { return '<li>' + _esc(s) + '</li>'; }).join('') + '</ul>'
      : '<div class="ode-bp-field-value">\u2014</div>';

    // Risks as list
    var risksList = risks.length
      ? '<ul class="ode-bp-list">' + risks.map(function(r) { return '<li>' + _esc(r) + '</li>'; }).join('') + '</ul>'
      : '<div class="ode-bp-field-value">No specific risks identified</div>';

    // Format the generated timestamp
    var generatedStr = '';
    if (generatedAt) {
      try {
        var d = new Date(generatedAt);
        generatedStr = d.toLocaleString();
      } catch (e) {
        generatedStr = generatedAt;
      }
    }

    body.innerHTML =
      (pitch ? '<div class="ode-bp-pitch">' + _esc(pitch) + '</div>' : '') +

      '<div class="ode-bp-block">' +
        '<div class="ode-bp-block-title">Business Model</div>' +
        '<div class="ode-bp-field">' +
          '<span class="ode-bp-field-label">Revenue Streams</span>' +
          revenueTags +
        '</div>' +
        '<div class="ode-bp-field">' +
          '<span class="ode-bp-field-label">Customer Type</span>' +
          '<span class="ode-bp-field-value">' + _esc(bm.customer_type || '\u2014') + '</span>' +
        '</div>' +
        '<div class="ode-bp-field">' +
          '<span class="ode-bp-field-label">Pricing Model</span>' +
          '<span class="ode-bp-field-value">' + _esc(bm.pricing_model || '\u2014') + '</span>' +
        '</div>' +
        '<div class="ode-bp-field">' +
          '<span class="ode-bp-field-label">Contract Type</span>' +
          '<span class="ode-bp-field-value">' + _esc(bm.contract_type || '\u2014') + '</span>' +
        '</div>' +
      '</div>' +

      '<div class="ode-bp-block">' +
        '<div class="ode-bp-block-title">' +
          'Moat' +
          '<span class="ode-bp-badge ' + moatBadgeClass + '">' + _esc(moatStrength) + '</span>' +
        '</div>' +
        '<div class="ode-bp-field">' +
          '<span class="ode-bp-field-label">Primary Advantage</span>' +
          '<span class="ode-bp-field-value">' + _esc(moat.primary || '\u2014') + '</span>' +
        '</div>' +
        '<div class="ode-bp-field">' +
          '<span class="ode-bp-field-label">Supporting Evidence</span>' +
          moatSignalsList +
        '</div>' +
      '</div>' +

      '<div class="ode-bp-block">' +
        '<div class="ode-bp-block-title">Competitive Landscape</div>' +
        '<div class="ode-bp-field">' +
          '<span class="ode-bp-field-label">Market Position</span>' +
          '<span class="ode-bp-field-value ' + positionClass + '" style="font-weight: 700;">' + _esc(positionLabel) + '</span>' +
        '</div>' +
        '<div class="ode-bp-field">' +
          '<span class="ode-bp-field-label">Direct Competitors</span>' +
          competitorTags +
        '</div>' +
        '<div class="ode-bp-field">' +
          '<span class="ode-bp-field-label">Differentiation</span>' +
          '<span class="ode-bp-field-value">' + _esc(comp.differentiation || '\u2014') + '</span>' +
        '</div>' +
      '</div>' +

      '<div class="ode-bp-block ode-bp-risks">' +
        '<div class="ode-bp-block-title">Key Business Risks</div>' +
        risksList +
      '</div>' +

      '<div class="ode-bp-footer">' +
        'Confidence: ' + _esc(confidence) + (generatedStr ? ' \u00b7 Generated ' + _esc(generatedStr) : '') +
      '</div>';
  }

  function renderScoreCards(data) {
    var compositeCard = scope.querySelector('#ode-composite-card');
    var breakoutCard = scope.querySelector('#ode-breakout-card');
    var llmCard = scope.querySelector('#ode-llm-card');

    if (compositeCard) {
      var score = data.evaluation ? data.evaluation.composite_score : null;
      var completeness = data.evaluation ? data.evaluation.completeness_pct : null;
      compositeCard.innerHTML =
        '<div class="ode-score-card-title">Composite Score</div>' +
        '<div class="ode-score-card-value">' + (score != null ? score.toFixed(1) : '\u2014') + '</div>' +
        '<div class="ode-score-card-subtitle">' + (completeness != null ? completeness.toFixed(0) + '% data completeness' : '') + '</div>';
    }

    if (breakoutCard) {
      var bScore = data.breakout ? data.breakout.score : null;
      var bStatus = data.breakout ? data.breakout.filter_status : '';
      breakoutCard.innerHTML =
        '<div class="ode-score-card-title">Breakout Score</div>' +
        '<div class="ode-score-card-value">' + (bScore != null ? bScore.toFixed(1) : '\u2014') + '</div>' +
        '<div class="ode-score-card-subtitle">' + (bStatus === 'eligible' ? 'Eligible for breakout' : _esc(bStatus)) + '</div>';
    }

    if (llmCard) {
      var rec = data.llm_recommendation || {};
      var rating = (rec.rating || 'hold').toLowerCase().replace(/_/g, '-');
      llmCard.innerHTML =
        '<div class="ode-score-card-title">LLM Recommendation</div>' +
        '<div style="margin: 12px 0;">' +
          '<span class="ode-llm-rating ode-llm-rating-' + rating + '">' + _esc(rec.rating || '\u2014') + '</span>' +
        '</div>' +
        '<div class="ode-score-card-subtitle">Conviction: ' + (rec.conviction != null ? rec.conviction + '%' : '\u2014') + '</div>';
    }
  }

  function renderPillars(evaluation) {
    var container = scope.querySelector('#ode-pillars-grid');
    if (!container || !evaluation || !evaluation.pillar_scores) return;

    var pillarOrder = [
      ['business_quality', 'Business Quality'],
      ['operational_health', 'Operational Health'],
      ['capital_allocation', 'Capital Allocation'],
      ['growth_quality', 'Growth Quality'],
      ['valuation', 'Valuation']
    ];

    var fmt = window.BenTradeComponents;

    container.innerHTML = pillarOrder.map(function(pair) {
      var key = pair[0], label = pair[1];
      var score = evaluation.pillar_scores[key];
      var breakdown = evaluation.pillar_breakdowns ? evaluation.pillar_breakdowns[key] : null;

      var componentsHtml = '';
      if (breakdown && breakdown.metrics) {
        // Real API: separate metrics and scores dicts
        var metricNames = Object.keys(breakdown.metrics);
        componentsHtml = metricNames.map(function(name) {
          var metricVal = breakdown.metrics[name];
          var metricScore = breakdown.scores ? breakdown.scores[name] : null;
          var fmtVal = fmt ? fmt.formatMetric(name, metricVal) : (metricVal != null ? String(metricVal) : '\u2014');
          var fmtLabel = fmt ? fmt.formatMetricLabel(name) : name.replace(/_/g, ' ');
          return '<div class="ode-pillar-component">' +
            '<span>' + _esc(fmtLabel) + '</span>' +
            '<span title="Score: ' + (metricScore != null ? metricScore.toFixed(0) : 'n/a') + '/100">' + _esc(fmtVal) + '</span></div>';
        }).join('');
      } else if (breakdown && breakdown.components) {
        // Mock / legacy: components with {value, score, weight}
        componentsHtml = Object.keys(breakdown.components).map(function(name) {
          var comp = breakdown.components[name];
          var fmtVal = fmt ? fmt.formatMetric(name, comp.value) : (comp.score != null ? comp.score.toFixed(0) : '\u2014');
          var fmtLabel = fmt ? fmt.formatMetricLabel(name) : name.replace(/_/g, ' ');
          return '<div class="ode-pillar-component">' +
            '<span>' + _esc(fmtLabel) + '</span>' +
            '<span>' + _esc(fmtVal) + '</span></div>';
        }).join('');
      }

      return '<div class="ode-pillar-card">' +
        '<div class="ode-pillar-name">' + label + '</div>' +
        '<div class="ode-pillar-score">' + (score != null ? score.toFixed(1) : '\u2014') + '</div>' +
        '<div class="ode-pillar-components">' + componentsHtml + '</div></div>';
    }).join('');
  }

  function renderSmartMoney(sm) {
    var container = scope.querySelector('#ode-smart-money');
    if (!container) return;

    if (!sm) {
      container.innerHTML = '<div style="color: #506878;">No smart money data available</div>';
      return;
    }

    var cards = [];

    // Insider activity
    var ia = sm.insider_activity;
    if (ia) {
      var signal = ia.signal || 'unknown';
      var signalLabel = signal.replace(/_/g, ' ');
      var netValue = ia.net_value || 0;

      cards.push(
        '<div class="ode-sm-card">' +
          '<div class="ode-pillar-name">Insider Activity</div>' +
          '<div class="ode-pillar-score">' + (ia.score != null ? ia.score.toFixed(0) : '\u2014') + '</div>' +
          '<div style="color: ' + (netValue < 0 ? '#f08070' : '#60d890') + '; font-size: 11px; margin-bottom: 8px; text-transform: capitalize;">' + _esc(signalLabel) + '</div>' +
          '<div class="ode-pillar-components">' +
            '<div class="ode-pillar-component"><span>Transactions (' + (ia._lookback_days || 180) + 'd)</span><span>' + (ia.transaction_count || 0) + '</span></div>' +
            '<div class="ode-pillar-component"><span>Buys</span><span>' + (ia.buy_count || 0) + '</span></div>' +
            '<div class="ode-pillar-component"><span>Sells</span><span>' + (ia.sell_count || 0) + '</span></div>' +
            '<div class="ode-pillar-component"><span>Buy Value</span><span>' + fmtCurrency(ia.buy_value) + '</span></div>' +
            '<div class="ode-pillar-component"><span>Sell Value</span><span>' + fmtCurrency(ia.sell_value) + '</span></div>' +
            '<div class="ode-pillar-component"><span>Net Value</span><span style="color: ' + (netValue < 0 ? '#f08070' : '#60d890') + ';">' + fmtCurrency(Math.abs(netValue)) + ' ' + (netValue < 0 ? '(net sell)' : '(net buy)') + '</span></div>' +
          '</div>' +
        '</div>'
      );
    }

    // Institutional
    var inst = sm.institutional_ownership;
    cards.push(
      '<div class="ode-sm-card">' +
        '<div class="ode-pillar-name">Institutional</div>' +
        '<div class="ode-pillar-score">' + (inst && inst.score != null ? inst.score.toFixed(0) : '\u2014') + '</div>' +
        '<div class="ode-pillar-components">' +
          (inst && inst.current_pct != null
            ? '<div class="ode-pillar-component"><span>Ownership</span><span>' + (inst.current_pct * 100).toFixed(1) + '%</span></div>'
            : '<div style="color: #506878;">Data not available on current plan</div>') +
        '</div>' +
      '</div>'
    );

    // Congressional
    cards.push(
      '<div class="ode-sm-card">' +
        '<div class="ode-pillar-name">Congressional</div>' +
        '<div class="ode-pillar-score">\u2014</div>' +
        '<div class="ode-pillar-components">' +
          '<div style="color: #506878;">No recent disclosures</div>' +
        '</div>' +
      '</div>'
    );

    container.innerHTML = cards.join('');
  }

  function renderValuationModels(data) {
    var dcfCard = scope.querySelector('#ode-dcf-card');
    var evaCard = scope.querySelector('#ode-eva-card');
    var compsCard = scope.querySelector('#ode-comps-card');

    // DCF
    if (dcfCard) {
      if (data.dcf && data.dcf.ok) {
        var d = data.dcf;
        var intrinsic = d.valuation ? d.valuation.intrinsic_value_per_share : null;
        var upside = d.valuation ? d.valuation.upside_pct : null;
        var currentPrice = d.current_price;
        var wacc = d.inputs ? d.inputs.wacc : null;
        var terminal = d.inputs ? d.inputs.terminal_growth : null;
        var verdict = d.valuation ? d.valuation.verdict : null;
        var verdictColor = verdict === 'UNDERVALUED' ? '#60d890' : verdict === 'OVERVALUED' ? '#f08070' : '#c0d8e8';

        dcfCard.innerHTML =
          '<div class="ode-valuation-title">DCF Intrinsic Value</div>' +
          '<div class="ode-valuation-value">' + (intrinsic != null ? '$' + intrinsic.toFixed(2) : '\u2014') + '</div>' +
          '<div class="ode-valuation-detail">' +
            '<div>Current: ' + (currentPrice != null ? '$' + currentPrice.toFixed(2) : '\u2014') + '</div>' +
            '<div>Upside: ' + (upside != null ? (upside >= 0 ? '+' : '') + upside.toFixed(1) + '%' : '\u2014') + '</div>' +
            (verdict ? '<div style="margin-top: 4px; color: ' + verdictColor + ';">' + verdict + '</div>' : '') +
            '<div style="margin-top: 8px; color: #506878;">' +
              'WACC ' + (wacc != null ? (wacc * 100).toFixed(1) + '%' : '\u2014') + ' \u00b7 ' +
              'Terminal ' + (terminal != null ? (terminal * 100).toFixed(1) + '%' : '\u2014') +
            '</div>' +
            (d.confidence ? '<div style="margin-top: 4px; color: #506878;">Confidence: ' + d.confidence + '</div>' : '') +
          '</div>';
      } else {
        dcfCard.innerHTML = '<div class="ode-valuation-title">DCF</div><div class="ode-valuation-detail">Not available</div>';
      }
    }

    // EVA
    if (evaCard) {
      if (data.eva && data.eva.ok) {
        var ev = data.eva;
        var roic = ev.roic_analysis ? ev.roic_analysis.roic : null;
        var evaWacc = ev.wacc ? ev.wacc.wacc : null;
        var spread = ev.eva ? ev.eva.value_spread : null;
        var evaAnnual = ev.eva ? ev.eva.eva_annual : null;
        var createsValue = ev.eva ? ev.eva.creates_value : false;
        var grade = ev.grade;

        evaCard.innerHTML =
          '<div class="ode-valuation-title">Economic Value Added</div>' +
          '<div class="ode-valuation-value">' + fmtCurrency(evaAnnual) + '</div>' +
          '<div class="ode-valuation-detail">' +
            '<div>ROIC: ' + (roic != null ? (roic * 100).toFixed(1) + '%' : '\u2014') + '</div>' +
            '<div>WACC: ' + (evaWacc != null ? (evaWacc * 100).toFixed(1) + '%' : '\u2014') + '</div>' +
            '<div>Spread: ' + (spread != null ? (spread >= 0 ? '+' : '') + (spread * 100).toFixed(1) + '%' : '\u2014') + '</div>' +
            '<div style="margin-top: 8px; color: ' + (createsValue ? '#60d890' : '#f08070') + ';">' +
              (createsValue ? '\u2713 Value Creating' : '\u2717 Value Destroying') +
            '</div>' +
            (grade ? '<div style="margin-top: 4px; color: #506878;">Grade: ' + grade + '</div>' : '') +
          '</div>';
      } else {
        evaCard.innerHTML = '<div class="ode-valuation-title">EVA</div><div class="ode-valuation-detail">Not available</div>';
      }
    }

    // Comps
    if (compsCard) {
      if (data.comps && data.comps.ok) {
        var comps = data.comps;
        var peers = (comps.peer_group ? comps.peer_group.details : []) || [];
        var fairValue = comps.fair_value ? comps.fair_value.composite_fair_value : null;
        var upsidePct = comps.fair_value ? comps.fair_value.upside_pct : null;
        var verdictLabel = comps.verdict ? comps.verdict.label : null;
        var verdictDesc = comps.verdict ? comps.verdict.description : null;
        var vlColor = verdictLabel === 'UNDERVALUED' ? '#60d890' : verdictLabel === 'OVERVALUED' ? '#f08070' : '#c0d8e8';

        var peerRows = peers.slice(0, 5).map(function(p) {
          var multiple = p.pe != null ? 'P/E ' + p.pe.toFixed(1) : (p.ps != null ? 'P/S ' + p.ps.toFixed(1) : '\u2014');
          return '<div class="ode-pillar-component"><span>' + _esc(p.symbol) + '</span><span>' + multiple + '</span></div>';
        }).join('');

        compsCard.innerHTML =
          '<div class="ode-valuation-title">Peer Comparison</div>' +
          (fairValue != null ? '<div class="ode-valuation-value">$' + fairValue.toFixed(2) + '</div>' : '') +
          '<div class="ode-valuation-detail">' +
            (upsidePct != null ? '<div>Upside: ' + (upsidePct >= 0 ? '+' : '') + upsidePct.toFixed(1) + '%</div>' : '') +
            (verdictLabel ? '<div style="margin-top: 4px; color: ' + vlColor + ';">' + verdictLabel + '</div>' : '') +
          '</div>' +
          '<div class="ode-pillar-components" style="margin-top: 12px;">' +
            '<div style="color: #607890; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px;">Peers (' + peers.length + ')</div>' +
            peerRows +
          '</div>' +
          (verdictDesc ? '<div class="ode-valuation-detail" style="margin-top: 12px;">' + _esc(verdictDesc) + '</div>' : '');
      } else {
        compsCard.innerHTML = '<div class="ode-valuation-title">Comps</div><div class="ode-valuation-detail">Not available</div>';
      }
    }

    // EPV (Greenwald) — supports both flat (single EPV) and dual (trailing/normalized) formats
    var epvCard = scope.querySelector('#ode-epv-card');
    if (epvCard) {
      var epv = data.epv;
      if (epv && epv.ok) {
        var isDual = !!(epv.trailing && epv.normalized);
        var currentPrice = isDual ? (epv.shared_inputs || {}).current_price : epv.current_price;
        var emergence = isDual ? (epv.emergence || {}) : null;

        epvCard.className = 'ode-valuation-card epv-card expandable';

        if (isDual) {
          // Dual format: trailing + normalized side by side
          var trailing = epv.trailing;
          var normalized = epv.normalized;
          var trailingFv = trailing.fair_value_per_share;
          var normalizedFv = normalized.fair_value_per_share;
          var trailingCls = (trailingFv != null && currentPrice != null && trailingFv > currentPrice) ? 'above-price' : 'below-price';
          var normalizedCls = (normalizedFv != null && currentPrice != null && normalizedFv > currentPrice) ? 'above-price' : 'below-price';
          var emergLvl = emergence ? _epvEmergenceBadgeLevel(emergence.signal) : 'unknown';

          epvCard.innerHTML =
            '<div class="ode-valuation-title">EPV (Greenwald)</div>' +
            '<div class="ode-valuation-card-subtitle">Earnings Power Value</div>' +
            '<div class="epv-dual-grid">' +
              '<div class="epv-value-block">' +
                '<div class="epv-value-label">Trailing (' + (trailing.period_years || 1) + 'y)</div>' +
                '<div class="epv-value-amount ' + trailingCls + '">' + _epvFmtPrice(trailingFv) + '</div>' +
                '<div class="epv-value-premium">' + _epvFmtGrowthPremium(trailing) + '</div>' +
              '</div>' +
              '<div class="epv-value-block">' +
                '<div class="epv-value-label">Normalized (' + (normalized.period_years || 5) + 'y)</div>' +
                '<div class="epv-value-amount ' + normalizedCls + '">' + _epvFmtPrice(normalizedFv) + '</div>' +
                '<div class="epv-value-premium">' + _epvFmtGrowthPremium(normalized) + '</div>' +
              '</div>' +
            '</div>' +
            (currentPrice != null ? '<div class="epv-current-price">Current: $' + currentPrice.toFixed(2) + '</div>' : '') +
            '<div class="ode-valuation-card-footer">' +
              '<span class="epv-emergence-badge ' + emergLvl + '">Emergence: ' + _esc(_epvFmtSignal(emergence ? emergence.signal : null)) + '</span>' +
            '</div>';
        } else {
          // Flat format: single EPV value with growth premium
          var fv = epv.fair_value_per_share;
          var fvCls = (fv != null && currentPrice != null && fv > currentPrice) ? 'above-price' : 'below-price';
          var inputs = epv.inputs || {};
          var periodYears = inputs.normalization_period_years || 5;

          epvCard.innerHTML =
            '<div class="ode-valuation-title">EPV (Greenwald)</div>' +
            '<div class="ode-valuation-card-subtitle">Earnings Power Value</div>' +
            '<div class="epv-value-block" style="margin-bottom:8px">' +
              '<div class="epv-value-label">Normalized (' + periodYears + 'y EBIT)</div>' +
              '<div class="epv-value-amount ' + fvCls + '" style="font-size:22px">' + _epvFmtPrice(fv) + '</div>' +
              '<div class="epv-value-premium">' + _epvFmtGrowthPremium(epv) + '</div>' +
            '</div>' +
            (currentPrice != null ? '<div class="epv-current-price">Current: $' + currentPrice.toFixed(2) + '</div>' : '') +
            (epv.interpretation ? '<div class="ode-valuation-detail" style="margin-top:8px;font-size:11px">' + _esc(epv.interpretation) + '</div>' : '');
        }

        epvCard.addEventListener('click', function() { _showEpvBreakdown(epv); });
      } else if (epv && !epv.ok) {
        epvCard.innerHTML =
          '<div class="ode-valuation-title">EPV (Greenwald)</div>' +
          '<div class="ode-valuation-card-error">' + _esc(epv.error || 'Insufficient data') + '</div>';
      } else {
        epvCard.innerHTML = '<div class="ode-valuation-title">EPV</div><div class="ode-valuation-detail">Not available</div>';
      }
    }
  }

  // === EPV HELPERS ===
  function _epvFmtPrice(v) {
    if (v == null) return '\u2014';
    var n = Number(v);
    if (isNaN(n)) return '\u2014';
    return '$' + n.toFixed(2);
  }

  function _epvFmtGrowthPremium(sub) {
    if (!sub) return '';
    var label = sub.growth_premium_label;
    var pct = sub.growth_premium_pct;
    if (label === 'NEGATIVE_EPV') {
      return '<span class="epv-premium-label negative">Negative EPV</span>';
    }
    if (pct == null) return '';
    var cls = _epvPremiumClass(label);
    var display = _epvFmtLabel(label);
    var sign = pct >= 0 ? '+' : '';
    return '<span class="epv-premium-label ' + cls + '">' + sign + pct.toFixed(0) + '% ' + display + '</span>';
  }

  function _epvFmtLabel(label) {
    if (!label) return '';
    return label.split('_').map(function(w) {
      return w.charAt(0) + w.slice(1).toLowerCase();
    }).join(' ');
  }

  function _epvPremiumClass(label) {
    var map = {
      'DEEP_DISCOUNT': 'positive',
      'DISCOUNTED': 'positive',
      'MODEST_GROWTH': 'neutral',
      'SIGNIFICANT_GROWTH': 'neutral',
      'HIGH_GROWTH': 'caution',
      'VERY_HIGH_GROWTH': 'caution',
      'SPECULATIVE': 'warning',
      'NEGATIVE_EPV': 'warning'
    };
    return map[label] || 'neutral';
  }

  function _epvFmtSignal(signal) {
    if (!signal) return 'N/A';
    return signal.split('_').map(function(w) {
      return w.charAt(0) + w.slice(1).toLowerCase();
    }).join(' ');
  }

  function _epvEmergenceBadgeLevel(signal) {
    var map = {
      'EMERGING': 'excellent',
      'EXPANDING': 'good',
      'RECOVERING': 'good',
      'STABLE': 'neutral',
      'DECLINING': 'poor',
      'POSSIBLE_ONE_TIME': 'caution',
      'INSUFFICIENT_DATA': 'unknown'
    };
    return map[signal] || 'unknown';
  }

  function _epvFmtMoneyShort(v) {
    if (v == null) return '\u2014';
    var n = Math.abs(Number(v));
    var sign = v < 0 ? '-' : '';
    if (n >= 1e12) return sign + (n / 1e12).toFixed(2) + 'T';
    if (n >= 1e9) return sign + (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6) return sign + (n / 1e6).toFixed(2) + 'M';
    return sign + n.toFixed(0);
  }

  function _epvFmtShares(v) {
    if (v == null) return '\u2014';
    var n = Number(v);
    if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    return n.toLocaleString();
  }

  function _epvCalcBarHeight(value, all) {
    var max = 0;
    for (var i = 0; i < all.length; i++) {
      if (all[i] != null && all[i] > max) max = all[i];
    }
    if (!max || max <= 0) return 20;
    var pct = Math.max(0.1, (value || 0) / max);
    return Math.round(20 + pct * 80);
  }

  // === EPV BREAKDOWN MODAL ===
  function _showEpvBreakdown(epv) {
    if (!epv || !epv.ok) return;

    var existing = document.getElementById('ode-epv-modal');
    if (existing) existing.remove();

    var isDual = !!(epv.trailing && epv.normalized);

    // Resolve inputs from either format
    var inputs = isDual ? (epv.shared_inputs || {}) : (epv.inputs || {});
    var emergence = isDual ? (epv.emergence || {}) : null;

    // Build emergence block (only for dual format)
    var emergenceHtml = '';
    if (emergence && emergence.signal) {
      var emergLvl = _epvEmergenceBadgeLevel(emergence.signal);
      emergenceHtml =
        '<div class="ode-epv-emergence-block ' + emergLvl + '">' +
          '<div class="ode-epv-emergence-title">Emergence Signal: ' + _esc(_epvFmtSignal(emergence.signal)) + '</div>' +
          '<div class="ode-epv-emergence-text">' + _esc(emergence.interpretation || '') + '</div>' +
        '</div>';
    }

    // Build EBIT history bars (dual format has ebit_history in emergence)
    var history = emergence ? (emergence.ebit_history || []) : [];
    var historyHtml = '';
    if (history.length > 0) {
      historyHtml =
        '<div class="ode-epv-section-title">Operating Income History (oldest \u2192 newest)</div>' +
        '<div class="ode-epv-history-chart">' +
          history.map(function(v, i) {
            var h = _epvCalcBarHeight(v, history);
            return '<div class="ode-epv-history-bar" style="height:' + h + 'px" title="Year ' + (i + 1) + ': $' + _epvFmtMoneyShort(v) + '">' +
              '<span class="ode-epv-history-label">' + _epvFmtMoneyShort(v) + '</span>' +
            '</div>';
          }).join('') +
        '</div>';
    }

    // Build inputs section
    var inputsHtml = '';
    var taxRate = inputs.tax_rate;
    var wacc = inputs.wacc;
    var shares = inputs.diluted_shares;
    var mktCap = isDual ? inputs.market_cap : epv.market_cap;
    var nopat = inputs.nopat;
    var ebit = inputs.normalized_ebit;

    if (ebit != null) {
      inputsHtml += '<div class="ode-epv-input-row"><span class="ode-epv-input-label">Normalized EBIT</span>' +
        '<span class="ode-epv-input-value">$' + _epvFmtMoneyShort(ebit) +
        (inputs.normalization_period_years ? ' <span class="ode-epv-input-source">(' + inputs.normalization_period_years + 'y avg)</span>' : '') +
        '</span></div>';
    }
    if (taxRate != null) {
      inputsHtml += '<div class="ode-epv-input-row"><span class="ode-epv-input-label">Tax Rate</span>' +
        '<span class="ode-epv-input-value">' + (taxRate * 100).toFixed(1) + '%' +
        (inputs.tax_rate_source ? ' <span class="ode-epv-input-source">(' + _esc(inputs.tax_rate_source) + ')</span>' : '') +
        '</span></div>';
    }
    if (nopat != null) {
      inputsHtml += '<div class="ode-epv-input-row"><span class="ode-epv-input-label">NOPAT</span>' +
        '<span class="ode-epv-input-value">$' + _epvFmtMoneyShort(nopat) + '</span></div>';
    }
    if (wacc != null) {
      inputsHtml += '<div class="ode-epv-input-row"><span class="ode-epv-input-label">WACC</span>' +
        '<span class="ode-epv-input-value">' + (wacc * 100).toFixed(2) + '%' +
        (inputs.wacc_source ? ' <span class="ode-epv-input-source">(' + _esc(inputs.wacc_source) + ')</span>' : '') +
        '</span></div>';
    }
    if (shares != null) {
      inputsHtml += '<div class="ode-epv-input-row"><span class="ode-epv-input-label">Diluted Shares</span>' +
        '<span class="ode-epv-input-value">' + _epvFmtShares(shares) +
        (inputs.shares_source ? ' <span class="ode-epv-input-source">(' + _esc(inputs.shares_source) + ')</span>' : '') +
        '</span></div>';
    }
    if (mktCap != null) {
      inputsHtml += '<div class="ode-epv-input-row"><span class="ode-epv-input-label">Market Cap</span>' +
        '<span class="ode-epv-input-value">$' + _epvFmtMoneyShort(mktCap) + '</span></div>';
    }

    // Build valuation summary
    var valuationHtml = '';
    if (isDual) {
      var trailing = epv.trailing;
      var normalized = epv.normalized;
      valuationHtml =
        '<div class="ode-epv-section-title">Valuation Comparison</div>' +
        '<div class="ode-epv-comparison-grid">' +
          '<div class="ode-epv-comparison-col">' +
            '<div class="ode-epv-col-header">Trailing (' + (trailing.period_years || 1) + 'y)</div>' +
            '<div class="ode-epv-col-row">EBIT: $' + _epvFmtMoneyShort(trailing.ebit) + '</div>' +
            '<div class="ode-epv-col-row">EPV Total: $' + _epvFmtMoneyShort(trailing.epv_total) + '</div>' +
            '<div class="ode-epv-col-row">Per Share: ' + _epvFmtPrice(trailing.fair_value_per_share) + '</div>' +
            '<div class="ode-epv-col-row">Premium: ' + (trailing.growth_premium_pct != null ? trailing.growth_premium_pct.toFixed(1) + '%' : '\u2014') + '</div>' +
          '</div>' +
          '<div class="ode-epv-comparison-col">' +
            '<div class="ode-epv-col-header">Normalized (' + (normalized.period_years || 5) + 'y)</div>' +
            '<div class="ode-epv-col-row">EBIT: $' + _epvFmtMoneyShort(normalized.ebit) + '</div>' +
            '<div class="ode-epv-col-row">EPV Total: $' + _epvFmtMoneyShort(normalized.epv_total) + '</div>' +
            '<div class="ode-epv-col-row">Per Share: ' + _epvFmtPrice(normalized.fair_value_per_share) + '</div>' +
            '<div class="ode-epv-col-row">Premium: ' + (normalized.growth_premium_pct != null ? normalized.growth_premium_pct.toFixed(1) + '%' : '\u2014') + '</div>' +
          '</div>' +
        '</div>';
    } else {
      // Flat format: single summary
      var currentPrice = epv.current_price;
      valuationHtml =
        '<div class="ode-epv-section-title">Valuation Summary</div>' +
        '<div class="ode-epv-inputs-grid">' +
          '<div class="ode-epv-input-row"><span class="ode-epv-input-label">EPV Total</span>' +
            '<span class="ode-epv-input-value">$' + _epvFmtMoneyShort(epv.epv_total) + '</span></div>' +
          '<div class="ode-epv-input-row"><span class="ode-epv-input-label">Fair Value / Share</span>' +
            '<span class="ode-epv-input-value">' + _epvFmtPrice(epv.fair_value_per_share) + '</span></div>' +
          (currentPrice != null ? '<div class="ode-epv-input-row"><span class="ode-epv-input-label">Current Price</span>' +
            '<span class="ode-epv-input-value">$' + currentPrice.toFixed(2) + '</span></div>' : '') +
          '<div class="ode-epv-input-row"><span class="ode-epv-input-label">Growth Premium</span>' +
            '<span class="ode-epv-input-value">' + (epv.growth_premium_pct != null ? (epv.growth_premium_pct >= 0 ? '+' : '') + epv.growth_premium_pct.toFixed(1) + '%' : '\u2014') +
            (epv.growth_premium_label ? ' <span class="ode-epv-input-source">(' + _esc(_epvFmtLabel(epv.growth_premium_label)) + ')</span>' : '') +
            '</span></div>' +
        '</div>';
    }

    // Interpretation block
    var interpHtml = '';
    if (epv.interpretation) {
      interpHtml =
        '<div class="ode-piotroski-interpretation" style="margin-bottom:16px">' +
          _esc(epv.interpretation) +
        '</div>';
    }

    var overlay = document.createElement('div');
    overlay.className = 'ode-modal-overlay';
    overlay.id = 'ode-epv-modal';

    overlay.innerHTML =
      '<div class="ode-modal" role="dialog" aria-labelledby="epv-modal-title">' +
        '<div class="ode-modal-header">' +
          '<div class="ode-modal-title" id="epv-modal-title">Earnings Power Value Analysis</div>' +
          '<button class="ode-modal-close" aria-label="Close">\u00D7</button>' +
        '</div>' +
        '<div class="ode-modal-body">' +
          emergenceHtml +
          interpHtml +
          historyHtml +
          (inputsHtml ? '<div class="ode-epv-section-title">Calculation Inputs</div><div class="ode-epv-inputs-grid">' + inputsHtml + '</div>' : '') +
          valuationHtml +
          '<div class="ode-modal-footer-link">' +
            '<a href="https://en.wikipedia.org/wiki/Earnings_Power_Value" target="_blank" rel="noopener">' +
              'About Earnings Power Value (Greenwald) \u2192' +
            '</a>' +
          '</div>' +
        '</div>' +
      '</div>';

    document.body.appendChild(overlay);

    var close = function() { overlay.remove(); document.removeEventListener('keydown', escHandler); };
    overlay.querySelector('.ode-modal-close').addEventListener('click', close);
    overlay.addEventListener('click', function(e) {
      if (e.target === overlay) close();
    });
    var escHandler = function(e) {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('keydown', escHandler);
  }

  function renderEntryAndTargets(data) {
    var entryCard = scope.querySelector('#ode-entry-card');
    var targetsCard = scope.querySelector('#ode-targets-card');

    if (entryCard) {
      if (data.entry_analysis && data.entry_analysis.ok) {
        var ea = data.entry_analysis;
        var tech = (ea.components ? ea.components.technical : null) || {};
        var rec = ea.recommendation;

        // Signal badge
        var signalClass = 'neutral';
        if (rec) {
          var recLower = rec.toLowerCase();
          if (recLower === 'buy' || recLower === 'strong buy') signalClass = 'bullish';
          else if (recLower === 'sell' || recLower === 'strong sell') signalClass = 'bearish';
        }
        var signalHtml = rec
          ? '<div class="ode-entry-signal ode-entry-signal-' + signalClass + '">' +
              _esc(rec) + (ea.conviction != null ? ' \u00b7 ' + ea.conviction.toFixed(0) + '% conviction' : '') +
            '</div>'
          : '';

        // Suggested entry callout
        var entryCallout = ea.suggested_entry != null
          ? '<div class="ode-entry-callout">' +
              '<div class="ode-entry-callout-label">Suggested Entry</div>' +
              '<div class="ode-entry-callout-price">$' + ea.suggested_entry.toFixed(2) + '</div>' +
            '</div>'
          : '';

        // Key levels row: Stop / Target / R:R
        var levels = [];
        if (ea.suggested_stop != null) {
          levels.push('<div class="ode-entry-level"><span class="ode-entry-level-label">Stop</span><span class="ode-entry-level-value" style="color: #f08070;">$' + ea.suggested_stop.toFixed(2) + '</span></div>');
        }
        if (ea.price_target != null) {
          levels.push('<div class="ode-entry-level"><span class="ode-entry-level-label">Target</span><span class="ode-entry-level-value" style="color: #60d890;">$' + ea.price_target.toFixed(2) + '</span></div>');
        }
        if (ea.risk_reward) {
          levels.push('<div class="ode-entry-level"><span class="ode-entry-level-label">R / R</span><span class="ode-entry-level-value">' + _esc(ea.risk_reward) + '</span></div>');
        }
        var levelsHtml = levels.length
          ? '<div class="ode-entry-levels">' + levels.join('') + '</div>'
          : '';

        // Technical context
        var techItems = [];
        techItems.push('<span>Trend: ' + _esc(tech.ma_signal || '\u2014') + '</span>');
        techItems.push('<span>RSI: ' + (tech.rsi != null ? tech.rsi.toFixed(1) : '\u2014') + (tech.rsi_signal ? ' (' + _esc(tech.rsi_signal) + ')' : '') + '</span>');
        if (tech.sma_50 != null) techItems.push('<span>SMA50: $' + tech.sma_50.toFixed(2) + '</span>');
        if (tech.sma_200 != null) techItems.push('<span>SMA200: $' + tech.sma_200.toFixed(2) + '</span>');
        if (tech.percentile_52w != null) techItems.push('<span>52w: ' + (tech.percentile_52w * 100).toFixed(0) + 'th pctl</span>');
        var techHtml = '<div class="ode-entry-tech">' + techItems.join('') + '</div>';

        // Summary
        var summaryHtml = ea.summary
          ? '<div class="ode-entry-summary">' + _esc(ea.summary) + '</div>'
          : '';

        entryCard.innerHTML =
          '<div class="ode-valuation-title">Technical Entry</div>' +
          signalHtml + entryCallout + levelsHtml + techHtml + summaryHtml;
      } else {
        entryCard.innerHTML = '<div class="ode-valuation-title">Entry Analysis</div><div class="ode-valuation-detail">Not available</div>';
      }
    }

    if (targetsCard) {
      var pt = data.price_targets;
      if (!pt || pt.error) {
        targetsCard.innerHTML =
          '<div class="ode-valuation-title">Analyst Price Targets</div>' +
          '<div class="ode-valuation-detail">' +
            '<div>Not available</div>' +
            (pt && pt.error ? '<div style="margin-top: 4px; color: #506878;">' + _esc(pt.error) + '</div>' : '') +
          '</div>';
      } else {
        targetsCard.innerHTML =
          '<div class="ode-valuation-title">Analyst Price Targets</div>' +
          '<div class="ode-valuation-value">' + (pt.analyst_consensus != null ? '$' + pt.analyst_consensus.toFixed(2) : '\u2014') + '</div>' +
          '<div class="ode-valuation-detail">' +
            '<div>Current: ' + (pt.current != null ? '$' + pt.current.toFixed(2) : '\u2014') + '</div>' +
            '<div>High: ' + (pt.analyst_high != null ? '$' + pt.analyst_high.toFixed(2) : '\u2014') + '</div>' +
            '<div>Low: ' + (pt.analyst_low != null ? '$' + pt.analyst_low.toFixed(2) : '\u2014') + '</div>' +
            '<div>Analysts: ' + (pt.analyst_count || 0) + '</div>' +
            '<div style="margin-top: 8px;">' +
              'Implied Upside: ' + (pt.implied_upside_pct != null ? (pt.implied_upside_pct >= 0 ? '+' : '') + pt.implied_upside_pct.toFixed(1) + '%' : '\u2014') +
            '</div>' +
          '</div>';
      }
    }
  }

  function renderThesis(llmRec) {
    var container = scope.querySelector('#ode-thesis');
    if (!container) return;

    if (!llmRec) {
      container.innerHTML = '<div style="color: #506878;">No LLM thesis available</div>';
      return;
    }

    var html = '';

    if (llmRec.summary) {
      html += '<div class="ode-thesis-section">' +
        '<div class="ode-thesis-section-title">Summary</div>' +
        '<div>' + _esc(llmRec.summary) + '</div></div>';
    }

    if (llmRec.thesis) {
      html += '<div class="ode-thesis-section">' +
        '<div class="ode-thesis-section-title">Full Thesis</div>' +
        '<div>' + _esc(llmRec.thesis) + '</div></div>';
    }

    if (llmRec.catalysts && llmRec.catalysts.length) {
      html += '<div class="ode-thesis-section">' +
        '<div class="ode-thesis-section-title">Catalysts</div>' +
        '<ul class="ode-thesis-list">' +
        llmRec.catalysts.map(function(c) { return '<li>' + _esc(c) + '</li>'; }).join('') +
        '</ul></div>';
    }

    if (llmRec.risks && llmRec.risks.length) {
      html += '<div class="ode-thesis-section">' +
        '<div class="ode-thesis-section-title">Risks</div>' +
        '<ul class="ode-thesis-list">' +
        llmRec.risks.map(function(r) { return '<li>' + _esc(r) + '</li>'; }).join('') +
        '</ul></div>';
    }

    container.innerHTML = html;
  }

  // === RAW FINANCIALS ===

  // Field groupings for raw financial statement tabs
  var INCOME_STATEMENT_FIELDS = [
    'revenue', 'cost_of_revenue', 'gross_profit', 'operating_expenses',
    'research_and_development', 'selling_general_administrative',
    'operating_income', 'income_before_tax', 'income_tax', 'net_income',
    'eps_basic', 'eps_diluted', 'basic_avg_shares', 'diluted_avg_shares'
  ];

  var BALANCE_SHEET_FIELDS = [
    'total_assets', 'current_assets', 'noncurrent_assets', 'fixed_assets',
    'inventory', 'accounts_payable', 'total_liabilities', 'current_liabilities',
    'noncurrent_liabilities', 'long_term_debt', 'total_equity', 'equity_parent'
  ];

  var CASH_FLOW_FIELDS = [
    'operating_cash_flow', 'investing_cash_flow', 'financing_cash_flow',
    'net_cash_flow', 'free_cash_flow'
  ];

  function renderRawFinancials() {
    // Reset active tab state in the DOM
    rawTabs.forEach(function(t) { t.classList.remove('ode-raw-tab-active'); });
    var incomeTab = scope.querySelector('.ode-raw-tab[data-tab="income"]');
    if (incomeTab) incomeTab.classList.add('ode-raw-tab-active');
    switchRawTabByName('income');
  }

  function switchRawTab(tabElement) {
    rawTabs.forEach(function(t) { t.classList.remove('ode-raw-tab-active'); });
    tabElement.classList.add('ode-raw-tab-active');
    switchRawTabByName(tabElement.dataset.tab);
  }

  function switchRawTabByName(tabName) {
    var container = scope.querySelector('#ode-raw-content');
    if (!container) return;
    if (!currentRawData) {
      container.innerHTML = '<div style="color: #506878; padding: 20px; text-align: center;">No data available</div>';
      return;
    }

    // Update active tab visual
    rawTabs.forEach(function(t) {
      if (t.dataset.tab === tabName) {
        t.classList.add('ode-raw-tab-active');
      } else {
        t.classList.remove('ode-raw-tab-active');
      }
    });

    switch (tabName) {
      case 'income':
        container.innerHTML = renderStatementTab(INCOME_STATEMENT_FIELDS);
        break;
      case 'balance':
        container.innerHTML = renderStatementTab(BALANCE_SHEET_FIELDS);
        break;
      case 'cashflow':
        container.innerHTML = renderStatementTab(CASH_FLOW_FIELDS);
        break;
      case 'metrics':
        container.innerHTML = renderComputedMetrics(currentRawData.computed_inputs || {});
        break;
      case 'sources':
        container.innerHTML = renderDataSources(currentRawData.sources || {});
        break;
    }
  }

  function renderStatementTab(fields) {
    var companyData = currentRawData.company_data || {};
    var annual = companyData.financials_annual || {};
    // Handle both {statements: [...]} dict and plain array shapes
    var statements = Array.isArray(annual) ? annual
      : (Array.isArray(annual.statements) ? annual.statements : []);

    if (statements.length === 0) {
      return '<div style="color: #506878; padding: 20px; text-align: center;">No annual financial data available</div>';
    }

    // Sort periods newest-first by fiscal_year then period date
    var sorted = statements.slice().sort(function(a, b) {
      var yearA = parseInt(a.fiscal_year, 10) || 0;
      var yearB = parseInt(b.fiscal_year, 10) || 0;
      if (yearA !== yearB) return yearB - yearA;
      var dateA = a.period || a.start_date || '';
      var dateB = b.period || b.start_date || '';
      return dateB > dateA ? 1 : dateB < dateA ? -1 : 0;
    });

    var shown = sorted.slice(0, 6);

    // Header row
    var headerCells = '';
    for (var h = 0; h < shown.length; h++) {
      var yr = shown[h].fiscal_year || '?';
      var fp = shown[h].fiscal_period || '';
      var colLabel = (fp === 'FY' || !fp) ? 'FY' + yr : fp + ' ' + yr;
      headerCells += '<th>' + _esc(String(colLabel)) + '</th>';
    }

    // Body rows — one per known field
    var rows = '';
    for (var f = 0; f < fields.length; f++) {
      var field = fields[f];
      var fieldLabel = field.replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); }).replace(/Eps/g, 'EPS');
      rows += '<tr><td>' + _esc(fieldLabel) + '</td>';
      for (var p = 0; p < shown.length; p++) {
        rows += '<td>' + fmtFinancial(shown[p][field], field) + '</td>';
      }
      rows += '</tr>';
    }

    return '<table class="ode-raw-table"><thead><tr><th>Line Item</th>' +
      headerCells + '</tr></thead><tbody>' + rows + '</tbody></table>';
  }

  function renderComputedMetrics(computedInputs) {
    var groups = {
      biz_quality: 'Business Quality',
      ops_health: 'Operational Health',
      cap_allocation: 'Capital Allocation',
      growth: 'Growth',
      valuation: 'Valuation'
    };

    var html = '';
    var keys = Object.keys(groups);
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      var label = groups[key];
      var group = computedInputs[key];
      if (!group || Object.keys(group).length === 0) continue;

      html += '<div style="margin-bottom: 24px;">' +
        '<div style="color: #80c8e0; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; font-weight: 600; margin-bottom: 8px;">' + label + '</div>' +
        '<table class="ode-raw-table"><tbody>';

      var metricKeys = Object.keys(group);
      for (var j = 0; j < metricKeys.length; j++) {
        var mKey = metricKeys[j];
        var mLabel = mKey.replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
        html += '<tr><td>' + _esc(mLabel) + '</td><td>' + fmtMetric(group[mKey], mKey) + '</td></tr>';
      }

      html += '</tbody></table></div>';
    }

    if (!html) {
      html = '<div style="color: #506878; padding: 20px; text-align: center;">No computed metrics available</div>';
    }
    return html;
  }

  function renderDataSources(sources) {
    if (!sources || Object.keys(sources).length === 0) {
      return '<div style="color: #506878; padding: 20px; text-align: center;">No source metadata available</div>';
    }

    var html = '<table class="ode-raw-table"><thead><tr>' +
      '<th>Endpoint</th><th>Provider</th><th>Status</th><th>Fetched</th>' +
      '</tr></thead><tbody>';

    var names = Object.keys(sources);
    for (var i = 0; i < names.length; i++) {
      var name = names[i];
      var info = sources[name] || {};
      var provider = info.provider || '\u2014';
      var ok = info.ok;
      var fetched = info.fetched_at ? info.fetched_at.substring(0, 19).replace('T', ' ') : '\u2014';
      var statusColor = ok === true ? '#60d890' : ok === false ? '#f08070' : '#506878';
      var statusLabel = ok === true ? '\u2713 OK' : ok === false ? '\u2717 Failed' : '\u2014';

      html += '<tr>' +
        '<td>' + _esc(name) + '</td>' +
        '<td>' + _esc(provider) + '</td>' +
        '<td style="color: ' + statusColor + ';">' + statusLabel + '</td>' +
        '<td>' + _esc(fetched) + '</td>' +
        '</tr>';
    }

    html += '</tbody></table>';
    return html;
  }

  // === METADATA FOOTER ===
  function renderMetadataFooter(data) {
    var container = scope.querySelector('#ode-metadata-footer');
    if (!container) return;

    var meta = data.metadata || {};
    var errors = meta.errors || {};
    var warnings = errors.missing_data_warnings || [];

    container.innerHTML =
      (meta.was_in_universe ? 'Refreshed existing entry' : 'New entry added to universe') + ' \u00b7 ' +
      'Tier: ' + _esc(meta.tier_assigned || '\u2014') + ' \u00b7 ' +
      'Data quality: ' + _esc(meta.data_quality || '\u2014') +
      (warnings.length > 0 ? ' \u00b7 ' + warnings.length + ' warning(s)' : '');
  }

  // === FORMATTERS ===
  function fmtLarge(num) {
    if (num == null) return '\u2014';
    if (num >= 1e12) return (num / 1e12).toFixed(2) + 'T';
    if (num >= 1e9) return (num / 1e9).toFixed(2) + 'B';
    if (num >= 1e6) return (num / 1e6).toFixed(2) + 'M';
    return num.toFixed(0);
  }

  function fmtNum(num) {
    if (num == null) return '\u2014';
    return num.toLocaleString('en-US');
  }

  function fmtCurrency(num) {
    if (num == null) return '\u2014';
    if (num >= 1e9) return '$' + (num / 1e9).toFixed(2) + 'B';
    if (num >= 1e6) return '$' + (num / 1e6).toFixed(2) + 'M';
    if (num >= 1e3) return '$' + (num / 1e3).toFixed(0) + 'K';
    return '$' + num.toFixed(0);
  }

  function fmtFinancial(value, fieldName) {
    if (value == null) return '\u2014';
    if (typeof value !== 'number') return _esc(String(value));
    // EPS and per-share values
    if (fieldName.indexOf('eps') !== -1 || fieldName.indexOf('per_share') !== -1) return '$' + value.toFixed(2);
    // Share counts — show in millions
    if (fieldName.indexOf('shares') !== -1 || fieldName.indexOf('avg_shares') !== -1) {
      if (Math.abs(value) >= 1e6) return (value / 1e6).toFixed(1) + 'M';
      return value.toFixed(0);
    }
    if (fieldName.indexOf('ratio') !== -1 || fieldName.indexOf('margin') !== -1) return value.toFixed(2);
    return fmtCurrency(value);
  }

  function fmtMetric(value, key) {
    // Delegate to the per-metric format registry when available
    if (window.BenTradeComponents && window.BenTradeComponents.formatMetric) {
      return window.BenTradeComponents.formatMetric(key, value);
    }
    // Fallback for cases where metric_formatter.js hasn't loaded
    if (value == null) return '\u2014';
    if (typeof value !== 'number') return String(value);
    if (Math.abs(value) >= 1e6) return fmtCurrency(value);
    return value.toFixed(2);
  }

  function _esc(text) {
    if (text == null) return '';
    var div = doc.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
  }

  // === MOCK MODE ===
  function runMockAnalysis(symbol) {
    var steps = [
      'Fetching universe registration',
      'Pulling fundamentals from Polygon',
      'Cross-validating with FMP',
      'Computing composite score',
      'Computing DCF valuation',
      'Running EVA analysis',
      'Fetching peer comps',
      'Generating LLM thesis'
    ];
    var completed = [];
    var stepIndex = 0;

    function nextStep() {
      if (stepIndex >= steps.length) {
        showResults(getMockResultData(symbol));
        return;
      }

      updateLoadingState({
        progress: {
          current_step: steps[stepIndex],
          current_step_index: stepIndex + 1,
          total_steps: steps.length,
          percent: Math.round(((stepIndex + 1) / steps.length) * 100)
        },
        completed_steps: completed.slice()
      });

      completed.push(steps[stepIndex]);
      stepIndex++;
      pollTimer = setTimeout(nextStep, 1500);
    }

    nextStep();
  }

  function getMockResultData(symbol) {
    return {
      job_id: 'mock_job',
      symbol: symbol,
      completed_at: new Date().toISOString(),
      company: {
        symbol: symbol,
        name: symbol + ' Inc (Mock)',
        sector: 'Technology',
        industry: 'Software',
        exchange: 'XNAS',
        market_cap: 50000000000,
        price: 145.30,
        ceo: 'John Doe',
        employees: 25000,
        description: 'Mock company for UI testing'
      },
      evaluation: {
        composite_score: 75.4,
        completeness_pct: 92.0,
        pillar_scores: {
          business_quality: 82.5,
          operational_health: 78.1,
          capital_allocation: 71.0,
          growth_quality: 68.6,
          valuation: 76.8
        },
        pillar_breakdowns: {
          business_quality: {
            score: 82.5,
            components: {
              gross_margin: { value: 0.55, score: 88, weight: 0.25 },
              operating_margin: { value: 0.28, score: 85, weight: 0.25 },
              roic: { value: 0.18, score: 82, weight: 0.25 },
              fcf_margin: { value: 0.22, score: 75, weight: 0.25 }
            }
          }
        }
      },
      breakout: {
        score: 64.2,
        filter_status: 'eligible',
        components: {}
      },
      llm_recommendation: {
        rating: 'BUY',
        conviction: 75,
        summary: 'Mock recommendation summary text for UI testing.',
        thesis: 'Mock thesis with longer text. This company demonstrates sustained competitive advantage.',
        risks: ['Competitive pressure from larger players', 'Regulatory uncertainty'],
        catalysts: ['New product launch in Q3', 'International expansion']
      },
      smart_money: {
        insider_activity: {
          signal: 'routine_selling',
          transaction_count: 8,
          buy_count: 3,
          sell_count: 5,
          buy_value: 1500000,
          sell_value: 2300000,
          net_value: -800000,
          unique_buyers: 2,
          score: 60,
          _lookback_days: 180
        },
        institutional_ownership: {
          current_pct: null,
          current_holders: null,
          trend: 'no_data',
          score: null
        },
        _source: 'fmp'
      },
      dcf: {
        ok: true,
        current_price: 145.30,
        confidence: 'HIGH',
        valuation: {
          intrinsic_value_per_share: 162.50,
          upside_pct: 11.8,
          verdict: 'UNDERVALUED',
          equity_value: 180000000000
        },
        inputs: {
          wacc: 0.092,
          terminal_growth: 0.025,
          revenue_growth_used: 0.08,
          fcf_margin: 0.25,
          shares_outstanding_m: 1100
        },
        projections: [
          { year: 1, revenue: 40000000000, fcf: 10000000000, growth: 0.08, pv: 9200000000 }
        ],
        caveats: ['Assumes stable margin expansion'],
        llm_analysis: null
      },
      eva: {
        ok: true,
        grade: 'CREATING',
        roic_analysis: { roic: 0.18, roic_pct: '18.0%' },
        wacc: { wacc: 0.092, wacc_pct: '9.2%' },
        eva: {
          value_spread: 0.088,
          value_spread_pct: '8.8%',
          eva_annual: 2300000000,
          eva_per_share: 2.09,
          creates_value: true
        },
        implied_valuation: { per_share: 175.00, upside_pct: 20.4 },
        verdict: { status: 'Creating economic value', summary: 'ROIC exceeds WACC by 8.8pp' },
        quality: { signals: [{ signal: 'Strong ROIC', direction: 'positive' }] },
        llm_analysis: null
      },
      comps: {
        ok: true,
        subject: { sector: 'Technology' },
        peer_group: {
          count: 4,
          symbols: ['MSFT', 'AAPL', 'GOOG', 'META'],
          details: [
            { symbol: 'MSFT', pe: 32.5, ev_ebitda: 22.1, ps: 12.0, pfcf: null, pb: 10.5, ev_revenue: 11.0, peg: 2.1, market_cap_m: 2800000 },
            { symbol: 'AAPL', pe: 28.5, ev_ebitda: 19.5, ps: 7.5, pfcf: null, pb: 40.0, ev_revenue: 7.2, peg: 2.8, market_cap_m: 2700000 },
            { symbol: 'GOOG', pe: 22.1, ev_ebitda: 16.8, ps: 6.0, pfcf: null, pb: 5.5, ev_revenue: 5.8, peg: 1.5, market_cap_m: 1900000 },
            { symbol: 'META', pe: 24.3, ev_ebitda: 14.2, ps: 9.0, pfcf: null, pb: 7.0, ev_revenue: 8.0, peg: 1.2, market_cap_m: 1200000 }
          ]
        },
        multiples_comparison: [],
        fair_value: { composite_fair_value: 158.00, upside_pct: 8.7 },
        verdict: { label: 'UNDERVALUED', description: 'Trading at 35% discount to peer average P/E' },
        confidence: { level: 'MEDIUM' },
        llm_narrative: null
      },
      entry_analysis: {
        ok: true,
        recommendation: 'BUY',
        conviction: 72,
        summary: 'Stock is in a bullish trend above both SMAs with RSI in neutral territory.',
        composite_score: 72,
        current_price: 145.30,
        components: {
          technical: {
            score: 70,
            rsi: 58.2,
            rsi_signal: 'neutral',
            sma_20: 143.50,
            sma_50: 142.10,
            sma_200: 135.80,
            ma_position: 'above_both',
            ma_signal: 'bullish',
            percentile_52w: 0.72,
            volume_signal: 'normal',
            support_level: 140.50,
            resistance_level: 148.00
          },
          market_context: { regime: 'NEUTRAL', spy_rsi: 55, vix: 18.5 },
          catalyst: { next_earnings: '2026-05-15', days_to_earnings: 33 }
        },
        suggested_entry: 143.00,
        suggested_stop: 132.00,
        price_target: 162.50,
        risk_reward: '1.8:1',
        signals: [{ signal: 'Above both SMAs', direction: 'bullish', weight: 0.3 }],
        llm_analysis: null
      },
      price_targets: {
        current: 145.30,
        analyst_consensus: 158.00,
        analyst_high: 175.00,
        analyst_low: 130.00,
        analyst_count: 18,
        implied_upside_pct: 8.7
      },
      raw_financials: {
        fetched_at: new Date().toISOString(),
        evaluation_version: 'mock',
        sources: {
          profile: { provider: 'fmp', endpoint: '/api/v3/profile', fetched_at: new Date().toISOString(), ok: true },
          financials: { provider: 'fmp', endpoint: '/api/v3/income-statement', fetched_at: new Date().toISOString(), ok: true },
          insider: { provider: 'fmp', endpoint: '/api/v4/insider-trading', fetched_at: new Date().toISOString(), ok: true }
        },
        company_data: {
          symbol: symbol,
          financials_annual: {
            symbol: symbol,
            timeframe: 'annual',
            count: 2,
            statements: [
              {
                period: '2024-12-31', fiscal_year: 2024, fiscal_period: 'FY',
                revenue: 38000000000, cost_of_revenue: 17100000000, gross_profit: 20900000000,
                operating_expenses: 10400000000, research_and_development: 5200000000,
                selling_general_administrative: 5200000000, operating_income: 10500000000,
                income_before_tax: 9950000000, income_tax: 1950000000, net_income: 8000000000,
                eps_basic: 7.18, eps_diluted: 7.05, basic_avg_shares: 1114000000, diluted_avg_shares: 1134000000,
                total_assets: 55000000000, current_assets: 22000000000, noncurrent_assets: 33000000000,
                fixed_assets: 4000000000, inventory: null, accounts_payable: 3500000000,
                total_liabilities: 30000000000, current_liabilities: 11000000000, noncurrent_liabilities: 19000000000,
                long_term_debt: 14000000000, total_equity: 25000000000, equity_parent: 25000000000,
                operating_cash_flow: 11000000000, investing_cash_flow: -1500000000,
                financing_cash_flow: -8500000000, net_cash_flow: 1000000000, free_cash_flow: 9500000000
              },
              {
                period: '2023-12-31', fiscal_year: 2023, fiscal_period: 'FY',
                revenue: 35200000000, cost_of_revenue: 16300000000, gross_profit: 18900000000,
                operating_expenses: 9800000000, research_and_development: 4800000000,
                selling_general_administrative: 5000000000, operating_income: 9100000000,
                income_before_tax: 8580000000, income_tax: 1700000000, net_income: 6880000000,
                eps_basic: 6.15, eps_diluted: 6.08, basic_avg_shares: 1118000000, diluted_avg_shares: 1131000000,
                total_assets: 50000000000, current_assets: 20000000000, noncurrent_assets: 30000000000,
                fixed_assets: 3500000000, inventory: null, accounts_payable: 3200000000,
                total_liabilities: 27000000000, current_liabilities: 10000000000, noncurrent_liabilities: 17000000000,
                long_term_debt: 12000000000, total_equity: 23000000000, equity_parent: 23000000000,
                operating_cash_flow: 9800000000, investing_cash_flow: -1200000000,
                financing_cash_flow: -7500000000, net_cash_flow: 1100000000, free_cash_flow: 8600000000
              }
            ]
          }
        },
        computed_inputs: {
          biz_quality: { gross_margin: 0.55, operating_margin: 0.28, roic: 0.18, fcf_margin: 0.22 },
          ops_health: { revenue_growth_1y: 0.08, margin_stability: 0.92 },
          cap_allocation: { buyback_yield: 0.02, dividend_growth: 0.08 },
          growth: { revenue_cagr_3y: 0.09, earnings_cagr_3y: 0.11 },
          valuation: { pe_ratio: 17.4, ev_ebitda: 11.2, fcf_yield: 0.065 }
        }
      },
      metadata: {
        was_in_universe: true,
        tier_assigned: 'tier_1_large_mid',
        data_quality: 'full',
        errors: { fetch_errors: [], missing_data_warnings: [], cross_validation_flags: [] }
      }
    };
  }

  // === DEEP RESEARCH PROMPT ===

  function _enableDeepResearchButton(symbol) {
    if (deepResearchBtn) {
      deepResearchBtn.disabled = false;
      _currentResearchSymbol = symbol;
    }
  }

  function _disableDeepResearchButton() {
    if (deepResearchBtn) {
      deepResearchBtn.disabled = true;
      _currentResearchSymbol = null;
    }
  }

  function _handleDeepResearchClick() {
    if (!_currentResearchSymbol) return;

    var modal = scope.querySelector('#ode-research-prompt-modal');
    var loading = scope.querySelector('#ode-research-prompt-loading');
    var errorEl = scope.querySelector('#ode-research-prompt-error');
    var errorMsg = scope.querySelector('#ode-research-prompt-error-message');
    var content = scope.querySelector('#ode-research-prompt-content');
    var textarea = scope.querySelector('#ode-research-prompt-textarea');
    var charcount = scope.querySelector('#ode-research-prompt-charcount');
    var meta = scope.querySelector('#ode-research-prompt-meta');
    var copyBtn = scope.querySelector('#ode-research-prompt-copy');

    // Show modal in loading state
    modal.hidden = false;
    loading.hidden = false;
    errorEl.hidden = true;
    content.hidden = true;
    copyBtn.disabled = true;
    copyBtn.classList.remove('copied');
    copyBtn.textContent = 'Copy to Clipboard';
    meta.textContent = '';

    fetch('/api/company-evaluator/on-demand/research-prompt/' + encodeURIComponent(_currentResearchSymbol))
      .then(function(resp) { return resp.json(); })
      .then(function(data) {
        loading.hidden = true;

        if (!data.ok) {
          errorEl.hidden = false;
          errorMsg.textContent = data.error || 'Failed to generate research prompt.';
          return;
        }

        // Success — show content
        content.hidden = false;
        textarea.value = data.prompt;
        charcount.textContent = data.prompt.length.toLocaleString() + ' characters';
        copyBtn.disabled = false;

        // Meta line
        var ageStr = _formatPromptAge(data.evaluation_age_seconds);
        meta.textContent = (data.company_name || data.symbol) + (ageStr ? ' \u00b7 ' + ageStr : '');
      })
      .catch(function(err) {
        loading.hidden = true;
        errorEl.hidden = false;
        errorMsg.textContent = 'Network error: ' + err.message;
      });
  }

  function _formatPromptAge(seconds) {
    if (seconds == null) return '';
    if (seconds < 60) return 'evaluated ' + seconds + 's ago';
    if (seconds < 3600) return 'evaluated ' + Math.round(seconds / 60) + 'm ago';
    if (seconds < 86400) return 'evaluated ' + Math.round(seconds / 3600) + 'h ago';
    return 'evaluated ' + Math.round(seconds / 86400) + 'd ago';
  }

  function _closeResearchPromptModal() {
    var modal = scope.querySelector('#ode-research-prompt-modal');
    if (modal) modal.hidden = true;
  }

  function _copyResearchPromptToClipboard() {
    var textarea = scope.querySelector('#ode-research-prompt-textarea');
    var copyBtn = scope.querySelector('#ode-research-prompt-copy');
    if (!textarea || !textarea.value) return;

    var onCopied = function() {
      copyBtn.textContent = '\u2713 Copied';
      copyBtn.classList.add('copied');
      setTimeout(function() {
        copyBtn.textContent = 'Copy to Clipboard';
        copyBtn.classList.remove('copied');
      }, 2000);
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(textarea.value).then(onCopied).catch(function() {
        textarea.select();
        document.execCommand('copy');
        onCopied();
      });
    } else {
      textarea.select();
      document.execCommand('copy');
      onCopied();
    }
  }

  function _onResearchEscKey(e) {
    if (e.key === 'Escape') {
      var modal = scope.querySelector('#ode-research-prompt-modal');
      if (modal && !modal.hidden) _closeResearchPromptModal();
    }
  }

  function _onResearchOverlayClick(e) {
    var modal = scope.querySelector('#ode-research-prompt-modal');
    if (e.target === modal) _closeResearchPromptModal();
  }

  function _initDeepResearchButton() {
    if (deepResearchBtn) {
      deepResearchBtn.addEventListener('click', _handleDeepResearchClick);
    }

    var closeBtn = scope.querySelector('#ode-research-prompt-close');
    if (closeBtn) closeBtn.addEventListener('click', _closeResearchPromptModal);

    var cancelModalBtn = scope.querySelector('#ode-research-prompt-cancel');
    if (cancelModalBtn) cancelModalBtn.addEventListener('click', _closeResearchPromptModal);

    var copyBtn = scope.querySelector('#ode-research-prompt-copy');
    if (copyBtn) copyBtn.addEventListener('click', _copyResearchPromptToClipboard);

    document.addEventListener('keydown', _onResearchEscKey);

    var modal = scope.querySelector('#ode-research-prompt-modal');
    if (modal) modal.addEventListener('click', _onResearchOverlayClick);
  }

  // === CLEANUP (returned to router) ===
  return function cleanup() {
    stopPolling();
    window.removeEventListener('hashchange', _onHashChange);
    if (form) form.removeEventListener('submit', handleSubmit);
    if (cancelBtn) cancelBtn.removeEventListener('click', handleCancel);
    if (retryBtn) retryBtn.removeEventListener('click', handleRetry);
    document.removeEventListener('keydown', _onResearchEscKey);
    if (window.BenTradeComponents && window.BenTradeComponents.destroyPriceChart) {
      window.BenTradeComponents.destroyPriceChart('ode-chart-container');
    }
    currentJobId = null;
    currentRawData = null;
    if (analyzeBtn) analyzeBtn.disabled = false;
  };
};
