window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initRiskCapital = function initRiskCapital(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
  const api = window.BenTradeApi;

  const portfolioSizeEl = scope.querySelector('#riskPortfolioSize');
  const maxTotalPctEl = scope.querySelector('#riskMaxTotalPct');
  const maxSymbolPctEl = scope.querySelector('#riskMaxSymbolPct');
  const maxTradePctEl = scope.querySelector('#riskMaxTradePct');
  const maxDteEl = scope.querySelector('#riskMaxDte');
  const minCashReservePctEl = scope.querySelector('#riskMinCashReservePct');
  const maxPositionSizePctEl = scope.querySelector('#riskMaxPositionSizePct');
  const defaultContractsCapEl = scope.querySelector('#riskDefaultContractsCap');
  const maxRiskPerTradeEl = scope.querySelector('#riskMaxRiskPerTrade');
  const maxRiskTotalEl = scope.querySelector('#riskMaxRiskTotal');
  const maxConcurrentTradesEl = scope.querySelector('#riskMaxConcurrentTrades');
  const maxRiskPerUnderlyingEl = scope.querySelector('#riskMaxRiskPerUnderlying');
  const maxSameExpirationRiskEl = scope.querySelector('#riskMaxSameExpirationRisk');
  const maxShortStrikeDistanceSigmaEl = scope.querySelector('#riskMaxShortStrikeDistanceSigma');
  const minOpenInterestEl = scope.querySelector('#riskMinOpenInterest');
  const minVolumeEl = scope.querySelector('#riskMinVolume');
  const maxBidAskSpreadPctEl = scope.querySelector('#riskMaxBidAskSpreadPct');
  const minPopEl = scope.querySelector('#riskMinPop');
  const minEvToRiskEl = scope.querySelector('#riskMinEvToRisk');
  const minReturnOnRiskEl = scope.querySelector('#riskMinReturnOnRisk');
  const maxIvRvRatioForBuyingEl = scope.querySelector('#riskMaxIvRvRatioForBuying');
  const minIvRvRatioForSellingEl = scope.querySelector('#riskMinIvRvRatioForSelling');
  const notesEl = scope.querySelector('#riskPolicyNotes');

  const savePolicyBtn = scope.querySelector('#riskSavePolicyBtn');
  const recommendedDefaultsBtn = scope.querySelector('#riskRecommendedDefaultsBtn');
  const refreshBtn = scope.querySelector('#riskRefreshBtn');

  const errorEl = scope.querySelector('#riskError');
  const warningsHardEl = scope.querySelector('#riskWarningsHard');
  const warningsSoftEl = scope.querySelector('#riskWarningsSoft');
  const metaEl = scope.querySelector('#riskSnapshotMeta');
  const progressLabelEl = scope.querySelector('#riskProgressLabel');
  const progressFillEl = scope.querySelector('#riskProgressFill');
  const statsEl = scope.querySelector('#riskStats');
  const byUnderlyingEl = scope.querySelector('#riskByUnderlying');
  const tradesBodyEl = scope.querySelector('#riskTradesBody');

  const modalEl = scope.querySelector('#riskModal');
  const modalBodyEl = scope.querySelector('#riskModalBody');
  const modalCloseBtn = scope.querySelector('#riskModalCloseBtn');

  if(!portfolioSizeEl || !maxTotalPctEl || !maxSymbolPctEl || !maxTradePctEl || !maxDteEl || !minCashReservePctEl || !maxPositionSizePctEl || !defaultContractsCapEl || !maxRiskPerTradeEl || !maxRiskTotalEl || !maxConcurrentTradesEl || !maxRiskPerUnderlyingEl || !maxSameExpirationRiskEl || !maxShortStrikeDistanceSigmaEl || !minOpenInterestEl || !minVolumeEl || !maxBidAskSpreadPctEl || !minPopEl || !minEvToRiskEl || !minReturnOnRiskEl || !maxIvRvRatioForBuyingEl || !minIvRvRatioForSellingEl || !notesEl || !savePolicyBtn || !recommendedDefaultsBtn || !refreshBtn || !warningsHardEl || !warningsSoftEl || !metaEl || !progressLabelEl || !progressFillEl || !statsEl || !byUnderlyingEl || !tradesBodyEl || !modalEl || !modalBodyEl || !modalCloseBtn){
    return;
  }

  let currentSnapshot = null;

  function setError(text){
    if(!errorEl) return;
    if(!text){
      errorEl.style.display = 'none';
      errorEl.textContent = '';
      return;
    }
    errorEl.style.display = 'block';
    errorEl.textContent = text;
  }

  function fmtMoney(value){
    if(value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A';
    return `$${Number(value).toFixed(2)}`;
  }

  function fmtPct(value){
    if(value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A';
    return `${(Number(value) * 100).toFixed(2)}%`;
  }

  function renderPolicy(policy){
    const p = policy || {};
    portfolioSizeEl.value = p.portfolio_size ?? '';
    maxTotalPctEl.value = p.max_total_risk_pct ?? '';
    maxSymbolPctEl.value = p.max_symbol_risk_pct ?? '';
    maxTradePctEl.value = p.max_trade_risk_pct ?? '';
    maxDteEl.value = p.max_dte ?? '';
    minCashReservePctEl.value = p.min_cash_reserve_pct ?? '';
    maxPositionSizePctEl.value = p.max_position_size_pct ?? '';
    defaultContractsCapEl.value = p.default_contracts_cap ?? '';
    maxRiskPerTradeEl.value = p.max_risk_per_trade ?? '';
    maxRiskTotalEl.value = p.max_risk_total ?? '';
    maxConcurrentTradesEl.value = p.max_concurrent_trades ?? '';
    maxRiskPerUnderlyingEl.value = p.max_risk_per_underlying ?? '';
    maxSameExpirationRiskEl.value = p.max_same_expiration_risk ?? '';
    maxShortStrikeDistanceSigmaEl.value = p.max_short_strike_distance_sigma ?? '';
    minOpenInterestEl.value = p.min_open_interest ?? '';
    minVolumeEl.value = p.min_volume ?? '';
    maxBidAskSpreadPctEl.value = p.max_bid_ask_spread_pct ?? '';
    minPopEl.value = p.min_pop ?? '';
    minEvToRiskEl.value = p.min_ev_to_risk ?? '';
    minReturnOnRiskEl.value = p.min_return_on_risk ?? '';
    maxIvRvRatioForBuyingEl.value = p.max_iv_rv_ratio_for_buying ?? '';
    minIvRvRatioForSellingEl.value = p.min_iv_rv_ratio_for_selling ?? '';
    notesEl.value = p.notes || '';
  }

  function starterPackPolicy(){
    return {
      portfolio_size: 100000,
      max_total_risk_pct: 0.06,
      max_symbol_risk_pct: 0.02,
      max_trade_risk_pct: 0.01,
      max_dte: 45,
      min_cash_reserve_pct: 20,
      max_position_size_pct: 5,
      default_contracts_cap: 3,
      max_risk_per_trade: 1000,
      max_risk_total: 6000,
      max_concurrent_trades: 10,
      max_risk_per_underlying: 2000,
      max_same_expiration_risk: 500,
      max_short_strike_distance_sigma: 2.5,
      min_open_interest: 500,
      min_volume: 50,
      max_bid_ask_spread_pct: 1.5,
      min_pop: 0.60,
      min_ev_to_risk: 0.02,
      min_return_on_risk: 0.10,
      max_iv_rv_ratio_for_buying: 1.0,
      min_iv_rv_ratio_for_selling: 1.1,
      notes: '',
    };
  }

  function readPolicyForm(){
    return {
      portfolio_size: Number(portfolioSizeEl.value),
      max_total_risk_pct: Number(maxTotalPctEl.value),
      max_symbol_risk_pct: Number(maxSymbolPctEl.value),
      max_trade_risk_pct: Number(maxTradePctEl.value),
      max_dte: Number(maxDteEl.value),
      min_cash_reserve_pct: Number(minCashReservePctEl.value),
      max_position_size_pct: Number(maxPositionSizePctEl.value),
      default_contracts_cap: Number(defaultContractsCapEl.value),
      max_risk_per_trade: Number(maxRiskPerTradeEl.value),
      max_risk_total: Number(maxRiskTotalEl.value),
      max_concurrent_trades: Number(maxConcurrentTradesEl.value),
      max_risk_per_underlying: Number(maxRiskPerUnderlyingEl.value),
      max_same_expiration_risk: Number(maxSameExpirationRiskEl.value),
      max_short_strike_distance_sigma: Number(maxShortStrikeDistanceSigmaEl.value),
      min_open_interest: Number(minOpenInterestEl.value),
      min_volume: Number(minVolumeEl.value),
      max_bid_ask_spread_pct: Number(maxBidAskSpreadPctEl.value),
      min_pop: Number(minPopEl.value),
      min_ev_to_risk: Number(minEvToRiskEl.value),
      min_return_on_risk: Number(minReturnOnRiskEl.value),
      max_iv_rv_ratio_for_buying: Number(maxIvRvRatioForBuyingEl.value),
      min_iv_rv_ratio_for_selling: Number(minIvRvRatioForSellingEl.value),
      notes: String(notesEl.value || ''),
    };
  }

  function openModal(trade){
    const width = trade?.width;
    const credit = trade?.credit;
    const maxLoss = trade?.max_loss ?? (trade?.computed || {})?.max_loss ?? trade?.estimated_risk;
    const kelly = trade?.kelly_fraction;
    const note = trade?.notes || 'under construction';

    modalBodyEl.innerHTML = `
      <div class="active-modal-row"><span>Trade Key</span><strong>${trade?.trade_key || 'N/A'}</strong></div>
      <div class="active-modal-row"><span>Width</span><strong>${width === null || width === undefined ? 'under construction' : Number(width).toFixed(2)}</strong></div>
      <div class="active-modal-row"><span>Credit</span><strong>${credit === null || credit === undefined ? 'under construction' : Number(credit).toFixed(2)}</strong></div>
      <div class="active-modal-row"><span data-metric="max_loss">Max Loss</span><strong>${maxLoss === null || maxLoss === undefined ? 'under construction' : fmtMoney(maxLoss)}</strong></div>
      <div class="active-modal-row"><span data-metric="kelly_fraction">Kelly Fraction</span><strong>${kelly === null || kelly === undefined ? 'under construction' : Number(kelly).toFixed(4)}</strong></div>
      <div class="active-modal-row"><span>Notes</span><strong>${note}</strong></div>
    `;

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(modalBodyEl);
    }

    modalEl.style.display = 'flex';
  }

  function renderWarningList(targetEl, warnings){
    const list = Array.isArray(warnings) ? warnings : [];
    if(!list.length){
      targetEl.innerHTML = '<div class="loading">No warnings.</div>';
      return;
    }
    targetEl.innerHTML = list.map(item => `<div class="stock-note">• ${String(item || '')}</div>`).join('');
  }

  function renderWarnings(groups){
    const hard = Array.isArray(groups?.hard_limits) ? groups.hard_limits : [];
    const soft = Array.isArray(groups?.soft_gates) ? groups.soft_gates : [];
    renderWarningList(warningsHardEl, hard);
    renderWarningList(warningsSoftEl, soft);
  }

  function renderByUnderlying(rows){
    const list = Array.isArray(rows) ? rows : [];
    if(!list.length){
      byUnderlyingEl.innerHTML = '<div class="loading">No risk by underlying yet.</div>';
      return;
    }

    byUnderlyingEl.innerHTML = list.map(row => `
      <div class="diagnosticRow">
        <span class="diagnosticLabel">${row.symbol || 'N/A'}</span>
        <span class="detail-value">${fmtMoney(row.risk)}</span>
      </div>
    `).join('');
  }

  function renderTrades(trades){
    const list = Array.isArray(trades) ? trades : [];
    if(!list.length){
      tradesBodyEl.innerHTML = '<tr><td colspan="5" class="loading">No trades in exposure snapshot.</td></tr>';
      return;
    }

    tradesBodyEl.innerHTML = list.map((trade, idx) => {
      const key = String(trade.trade_key || `trade-${idx}`);
      return `
        <tr class="risk-row" data-trade-key="${key}">
          <td>${trade.symbol || 'N/A'}</td>
          <td class="risk-key-cell">${key}</td>
          <td data-metric="max_loss">${fmtMoney(trade.max_loss ?? (trade.computed || {}).max_loss ?? trade.estimated_risk)}</td>
          <td data-metric="dte">${trade.dte ?? 'N/A'}</td>
          <td>${trade.notes || ''}</td>
        </tr>
      `;
    }).join('');

    tradesBodyEl.querySelectorAll('.risk-row').forEach(row => {
      row.addEventListener('click', () => {
        const key = String(row.getAttribute('data-trade-key') || '');
        const trade = list.find(item => String(item.trade_key || '') === key);
        if(trade) openModal(trade);
      });
    });
  }

  function renderSnapshot(snapshot){
    currentSnapshot = snapshot || null;
    const exposure = snapshot?.exposure || {};
    const policy = snapshot?.policy || {};

    const portfolio = Number(policy.portfolio_size || 0);
    const totalCap = portfolio * Number(policy.max_total_risk_pct || 0);
    const used = exposure.total_risk_used;
    const remaining = exposure.risk_remaining;

    const pctUsed = (totalCap > 0 && used !== null && used !== undefined && Number.isFinite(Number(used)))
      ? Math.max(0, Math.min(100, (Number(used) / totalCap) * 100))
      : 0;

    metaEl.textContent = `as_of: ${snapshot?.as_of || 'N/A'} • source: ${snapshot?.exposure_source || 'none'}`;
    progressLabelEl.textContent = `Used ${fmtMoney(used)} of ${fmtMoney(totalCap)} (${pctUsed.toFixed(1)}%) • Remaining ${fmtMoney(remaining)}`;
    progressFillEl.style.width = `${pctUsed.toFixed(1)}%`;

    statsEl.innerHTML = `
      <div class="statTile"><div class="statLabel">Open Trades</div><div class="statValue">${exposure.open_trades ?? 0}</div></div>
      <div class="statTile"><div class="statLabel">Total Risk Used</div><div class="statValue">${fmtMoney(exposure.total_risk_used)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="risk_remaining">Risk Remaining</div><div class="statValue">${fmtMoney(exposure.risk_remaining)}</div></div>
      <div class="statTile"><div class="statLabel">Max Trade %</div><div class="statValue">${fmtPct(policy.max_trade_risk_pct)}</div></div>
      <div class="statTile"><div class="statLabel">Max Symbol %</div><div class="statValue">${fmtPct(policy.max_symbol_risk_pct)}</div></div>
      <div class="statTile"><div class="statLabel" data-metric="dte">Max DTE</div><div class="statValue">${policy.max_dte ?? 'N/A'}</div></div>
    `;

    if(window.attachMetricTooltips){
      window.attachMetricTooltips(statsEl);
      window.attachMetricTooltips(tradesBodyEl);
    }

    renderWarnings(exposure.warnings || {});
    renderByUnderlying(exposure.risk_by_underlying || []);
    renderTrades(exposure.trades || []);
  }

  async function loadPolicy(){
    const payload = await api.getRiskPolicy();
    renderPolicy(payload?.policy || {});
  }

  async function loadSnapshot(){
    const payload = await api.getRiskSnapshot();
    renderSnapshot(payload || {});
  }

  async function savePolicy(){
    try{
      setError('');
      savePolicyBtn.disabled = true;
      const payload = await api.updateRiskPolicy(readPolicyForm());
      renderPolicy(payload?.policy || {});
      await loadSnapshot();
    }catch(err){
      setError(String(err?.message || err || 'Failed to save policy'));
    }finally{
      savePolicyBtn.disabled = false;
    }
  }

  function applyRecommendedDefaults(){
    renderPolicy(starterPackPolicy());
  }

  async function refreshAll(){
    try{
      setError('');
      refreshBtn.disabled = true;
      await loadPolicy();
      await loadSnapshot();
    }catch(err){
      setError(String(err?.message || err || 'Failed to refresh risk dashboard'));
    }finally{
      refreshBtn.disabled = false;
    }
  }

  savePolicyBtn.addEventListener('click', () => savePolicy());
  recommendedDefaultsBtn.addEventListener('click', () => applyRecommendedDefaults());
  refreshBtn.addEventListener('click', () => refreshAll());

  modalCloseBtn.addEventListener('click', () => { modalEl.style.display = 'none'; });
  modalEl.addEventListener('click', (event) => {
    if(event.target === modalEl){
      modalEl.style.display = 'none';
    }
  });

  refreshAll();
};
