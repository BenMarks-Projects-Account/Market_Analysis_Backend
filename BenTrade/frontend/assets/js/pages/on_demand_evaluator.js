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

  // === EVENT LISTENERS ===
  if (form) form.addEventListener('submit', handleSubmit);
  if (cancelBtn) cancelBtn.addEventListener('click', handleCancel);
  if (retryBtn) retryBtn.addEventListener('click', handleRetry);
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
    // Mount price chart below header
    if (window.BenTradeComponents && window.BenTradeComponents.mountPriceChart && data.symbol) {
      window.BenTradeComponents.mountPriceChart('ode-chart-container', data.symbol);
    }
    renderScoreCards(data);
    renderPillars(data.evaluation);
    renderSmartMoney(data.smart_money);
    renderValuationModels(data);
    renderEntryAndTargets(data);
    renderThesis(data.llm_recommendation);
    currentRawData = data.raw_financials || null;
    renderRawFinancials();
    renderMetadataFooter(data);
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

    container.innerHTML = pillarOrder.map(function(pair) {
      var key = pair[0], label = pair[1];
      var score = evaluation.pillar_scores[key];
      var breakdown = evaluation.pillar_breakdowns ? evaluation.pillar_breakdowns[key] : null;

      var componentsHtml = '';
      if (breakdown && breakdown.components) {
        componentsHtml = Object.keys(breakdown.components).map(function(name) {
          var comp = breakdown.components[name];
          return '<div class="ode-pillar-component">' +
            '<span>' + name.replace(/_/g, ' ') + '</span>' +
            '<span>' + (comp.score != null ? comp.score.toFixed(0) : '\u2014') + '</span></div>';
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

    container.innerHTML =
      '<div style="display:grid; grid-template-columns: repeat(3, 1fr); gap: 16px;">' + cards.join('') + '</div>';
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
  }

  function renderEntryAndTargets(data) {
    var entryCard = scope.querySelector('#ode-entry-card');
    var targetsCard = scope.querySelector('#ode-targets-card');

    if (entryCard) {
      if (data.entry_analysis && data.entry_analysis.ok) {
        var ea = data.entry_analysis;
        var tech = (ea.components ? ea.components.technical : null) || {};
        var rec = ea.recommendation;

        entryCard.innerHTML =
          '<div class="ode-valuation-title">Technical Entry</div>' +
          '<div class="ode-valuation-detail">' +
            (rec ? '<div style="margin-bottom: 8px;"><strong>Signal:</strong> ' + _esc(rec) + ' (' + (ea.conviction != null ? ea.conviction.toFixed(0) + '% conviction' : '\u2014') + ')</div>' : '') +
            '<div><strong>Trend:</strong> ' + _esc(tech.ma_signal || '\u2014') + '</div>' +
            '<div><strong>RSI:</strong> ' + (tech.rsi != null ? tech.rsi.toFixed(1) : '\u2014') + (tech.rsi_signal ? ' (' + _esc(tech.rsi_signal) + ')' : '') + '</div>' +
            '<div><strong>SMA 50:</strong> ' + (tech.sma_50 != null ? '$' + tech.sma_50.toFixed(2) : '\u2014') + '</div>' +
            '<div><strong>SMA 200:</strong> ' + (tech.sma_200 != null ? '$' + tech.sma_200.toFixed(2) : '\u2014') + '</div>' +
            (tech.percentile_52w != null ? '<div><strong>52w Range:</strong> ' + (tech.percentile_52w * 100).toFixed(0) + 'th percentile</div>' : '') +
            '<div style="margin-top: 12px;">' +
              (ea.suggested_entry != null ? '<div><strong>Suggested Entry:</strong> $' + ea.suggested_entry.toFixed(2) + '</div>' : '') +
              (ea.suggested_stop != null ? '<div style="color: #f08070;"><strong>Suggested Stop:</strong> $' + ea.suggested_stop.toFixed(2) + '</div>' : '') +
              (ea.price_target != null ? '<div><strong>Target:</strong> $' + ea.price_target.toFixed(2) + '</div>' : '') +
              (ea.risk_reward ? '<div><strong>R/R:</strong> ' + _esc(ea.risk_reward) + '</div>' : '') +
            '</div>' +
            (ea.summary ? '<div style="margin-top: 12px;">' + _esc(ea.summary) + '</div>' : '') +
          '</div>';
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
    if (value == null) return '\u2014';
    if (typeof value !== 'number') return String(value);

    // Percentages (decimals that look like ratios based on key name)
    var pctKeys = ['margin', 'yield', 'ratio', 'roic', 'roe', 'roa', 'growth', 'cagr', 'intensity', 'payout', 'spread', 'stability'];
    var lowerKey = key.toLowerCase();
    var isPct = false;
    for (var i = 0; i < pctKeys.length; i++) {
      if (lowerKey.indexOf(pctKeys[i]) !== -1) { isPct = true; break; }
    }
    if (isPct && Math.abs(value) < 10) {
      return (value * 100).toFixed(2) + '%';
    }

    // Large numbers
    if (Math.abs(value) >= 1e6) {
      return fmtCurrency(value);
    }

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

  // === CLEANUP (returned to router) ===
  return function cleanup() {
    stopPolling();
    window.removeEventListener('hashchange', _onHashChange);
    if (form) form.removeEventListener('submit', handleSubmit);
    if (cancelBtn) cancelBtn.removeEventListener('click', handleCancel);
    if (retryBtn) retryBtn.removeEventListener('click', handleRetry);
    if (window.BenTradeComponents && window.BenTradeComponents.destroyPriceChart) {
      window.BenTradeComponents.destroyPriceChart('ode-chart-container');
    }
    currentJobId = null;
    currentRawData = null;
    if (analyzeBtn) analyzeBtn.disabled = false;
  };
};
