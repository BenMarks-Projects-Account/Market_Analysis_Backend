// BenTrade Dashboard Logic: Credit Spread Analysis
window.BenTradeExecutionModal = window.BenTradeExecutionModal || (function(){
    function toNumber(value){
        if(value === null || value === undefined || value === '') return null;
        const n = Number(value);
        return Number.isFinite(n) ? n : null;
    }

    function fmtMoney(value){
        const n = toNumber(value);
        if(n === null) return 'N/A';
        return `${n >= 0 ? '+' : '-'}$${Math.abs(n).toFixed(2)}`;
    }

    function tradeDetailsHtml(trade){
        const row = (trade && typeof trade === 'object') ? trade : {};
        const symbol = String(row.underlying || row.underlying_symbol || row.symbol || 'N/A').toUpperCase();
        const strategy = String(row.spread_type || row.strategy || 'N/A');
        const expiration = String(row.expiration || row.expiration_date || 'N/A');
        const shortStrike = row.short_strike ?? row.put_short_strike ?? row.call_short_strike;
        const longStrike = row.long_strike ?? row.put_long_strike ?? row.call_long_strike;
        const maxLoss = fmtMoney(row.max_loss_per_share ?? row.max_loss ?? row.estimated_risk ?? row.risk_amount);
        const maxProfit = fmtMoney(row.max_profit_per_share ?? row.max_profit ?? row.estimated_max_profit);
        const creditDebitRaw = toNumber(row.net_credit ?? row.net_debit ?? row.credit ?? row.debit ?? row.premium_received ?? row.premium_paid);
        const creditDebitLabel = creditDebitRaw === null ? 'Credit/Debit' : (creditDebitRaw >= 0 ? 'Credit' : 'Debit');
        const creditDebitValue = creditDebitRaw === null ? 'N/A' : `$${Math.abs(creditDebitRaw).toFixed(2)}`;

        return `
            <div style="display:grid;grid-template-columns:1fr;gap:6px;text-align:left;">
                <div><strong>Symbol:</strong> ${symbol}</div>
                <div><strong>Strategy:</strong> ${strategy}</div>
                <div><strong>Expiry:</strong> ${expiration}</div>
                <div><strong>Strikes:</strong> ${shortStrike ?? 'N/A'} / ${longStrike ?? 'N/A'}</div>
                <div><strong>Max Loss:</strong> ${maxLoss}</div>
                <div><strong>Max Profit:</strong> ${maxProfit}</div>
                <div><strong>${creditDebitLabel}:</strong> ${creditDebitValue}</div>
            </div>
        `;
    }

    function ensureModal(doc){
        let modal = doc.getElementById('modal');
        if(modal) return modal;

        modal = doc.createElement('div');
        modal.id = 'modal';
        modal.style.display = 'none';
        modal.innerHTML = `
            <div id="modalCard" onclick="event.stopPropagation()">
                <div id="modalTitle">Trade Action</div>
                <div id="modalMsg">Trade capability off</div>
                <button id="modalPrimary" class="btn" type="button" style="margin-bottom:8px;">Execute (off)</button>
                <button id="modalClose" class="btn" type="button">Close</button>
            </div>
        `;
        modal.addEventListener('click', () => {
            modal.style.display = 'none';
        });
        doc.body.appendChild(modal);

        const closeBtn = doc.getElementById('modalClose');
        if(closeBtn){
            closeBtn.addEventListener('click', () => {
                modal.style.display = 'none';
            });
        }
        return modal;
    }

    function open(trade, options = {}){
        const doc = document;
        const modal = ensureModal(doc);
        const modalTitle = doc.getElementById('modalTitle');
        const modalMsg = doc.getElementById('modalMsg');
        const modalPrimary = doc.getElementById('modalPrimary');
        const modalClose = doc.getElementById('modalClose');
        if(!modal || !modalMsg){
            alert('Trade capability off');
            return;
        }

        if(modalTitle){
            modalTitle.textContent = 'Trade Action';
        }
        modalMsg.innerHTML = `${tradeDetailsHtml(trade)}<div style="margin-top:10px;">Trade capability off</div>`;

        const primaryLabel = String(options?.primaryLabel || 'Execute (off)');
        if(modalPrimary){
            modalPrimary.textContent = primaryLabel;
            modalPrimary.style.display = '';
            modalPrimary.onclick = () => {
                modal.style.display = 'none';
            };
        }

        if(modalClose){
            modalClose.onclick = () => {
                modal.style.display = 'none';
            };
        }

        modal.style.display = 'flex';
    }

    return { open };
})();

window.BenTrade = window.BenTrade || {};
window.BenTrade.initCreditSpread = function initCreditSpread(rootEl){
  const doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  const scope = rootEl || doc;
    const session = window.BenTradeSession || null;
    const api = window.BenTradeApi || null;
    const tradeCardUi = window.BenTradeTradeCard || null;

    const reportSelect = scope.querySelector('#reportSelect');
  const fileSelect = scope.querySelector('#fileSelect');
  const content = scope.querySelector('#content');
        const manualFiltersEnabled = scope.querySelector('#manualFiltersEnabled');
        const manualFiltersPanel = scope.querySelector('#manualFiltersPanel');
        const tradeCountsBar = scope.querySelector('#tradeCountsBar');
        const manualFilterPill = scope.querySelector('#manualFilterPill');
        const mfUseDefaultsBtn = scope.querySelector('#mfUseDefaultsBtn');
        const mfApplyBtn = scope.querySelector('#mfApplyBtn');
        const mfResetBtn = scope.querySelector('#mfResetBtn');
    if(!reportSelect || !fileSelect || !content){
        console.warn('[BenTrade] CreditSpread view not mounted (missing #reportSelect/#fileSelect/#content).');
    return;
  }

                const REPORT_KEY = 'creditSpreadSelectedReport';
        const UNDERLYING_KEY = 'creditSpreadSelectedUnderlying';
                function getSelectedReport(){
                        if(session?.getSelectedReport){
                            return session.getSelectedReport();
                        }
                        return localStorage.getItem(REPORT_KEY) || '';
                }
                function setSelectedReport(report){
                        if(session?.setSelectedReport){
                            session.setSelectedReport(report || '');
                            return;
                        }
                        localStorage.setItem(REPORT_KEY, report || '');
                }
        function getSelectedUnderlying(){
            if(session?.getSelectedUnderlying){
                return session.getSelectedUnderlying();
            }
            return localStorage.getItem(UNDERLYING_KEY) || 'ALL';
        }
        function setSelectedUnderlying(symbol){
            if(session?.setSelectedUnderlying){
                session.setSelectedUnderlying(symbol || 'ALL');
                return;
            }
            localStorage.setItem(UNDERLYING_KEY, symbol || 'ALL');
        }

        const manualFilterDefaults = window.BenTradeStrategyDefaults?.getStrategyDefaults?.('credit_spread') || {
            dte_min: 7,
            dte_max: 21,
            expected_move_multiple: 1.0,
            width_min: 1,
            width_max: 5,
            min_pop: 0.65,
            min_ev_to_risk: 0.02,
            max_bid_ask_spread_pct: 1.5,
            min_open_interest: 500,
            min_volume: 50,
        };

        const manualFilterFieldMap = {
            dte_min: scope.querySelector('#mfDteMin'),
            dte_max: scope.querySelector('#mfDteMax'),
            expected_move_multiple: scope.querySelector('#mfExpMoveX'),
            width_min: scope.querySelector('#mfWidthMin'),
            width_max: scope.querySelector('#mfWidthMax'),
            min_pop: scope.querySelector('#mfMinPop'),
            min_ev_to_risk: scope.querySelector('#mfMinEvRisk'),
            max_bid_ask_spread_pct: scope.querySelector('#mfMaxSpreadPct'),
            min_open_interest: scope.querySelector('#mfMinOi'),
            min_volume: scope.querySelector('#mfMinVol'),
        };

        let fullTradeList = [];
        let filteredTradeList = [];
        let manualFiltersApplied = false;
        let appliedManualFilters = null;

        function toFilterNumber(value){
            if(value === null || value === undefined || value === '') return null;
            const parsed = Number(value);
            return Number.isFinite(parsed) ? parsed : null;
        }

        function readManualFilterInputs(){
            const values = {};
            Object.keys(manualFilterFieldMap).forEach((key) => {
                const input = manualFilterFieldMap[key];
                values[key] = toFilterNumber(input?.value);
            });
            return values;
        }

        function setManualFilterInputs(values){
            const source = (values && typeof values === 'object') ? values : {};
            Object.entries(manualFilterFieldMap).forEach(([key, input]) => {
                if(!input) return;
                const value = source[key];
                input.value = (value === null || value === undefined) ? '' : String(value);
            });
        }

        function clearManualFilterInputs(){
            Object.values(manualFilterFieldMap).forEach((input) => {
                if(input) input.value = '';
            });
        }

        function setManualFilterPanelVisible(enabled){
            if(!manualFiltersPanel) return;
            manualFiltersPanel.style.display = enabled ? 'block' : 'none';
        }

        function computeTradeWidth(trade){
            const directWidth = toFilterNumber(trade?.width);
            if(directWidth !== null) return directWidth;
            const shortStrike = toFilterNumber(trade?.short_strike);
            const longStrike = toFilterNumber(trade?.long_strike);
            if(shortStrike !== null && longStrike !== null) return Math.abs(shortStrike - longStrike);
            return null;
        }

        function composeFilterStatusText(filters){
            if(!filters || typeof filters !== 'object') return 'Filters active';
            const parts = [];
            if(filters.dte_min !== null || filters.dte_max !== null){
                parts.push(`DTE ${filters.dte_min ?? '—'}–${filters.dte_max ?? '—'}`);
            }
            if(filters.min_pop !== null) parts.push(`POP ≥ ${filters.min_pop}`);
            if(filters.min_ev_to_risk !== null) parts.push(`EV/Risk ≥ ${filters.min_ev_to_risk}`);
            if(filters.min_open_interest !== null) parts.push(`OI ≥ ${filters.min_open_interest}`);
            if(filters.min_volume !== null) parts.push(`Vol ≥ ${filters.min_volume}`);
            return parts.length ? `Filters active: ${parts.join(', ')}` : 'Filters active';
        }

        function passesManualFilters(trade, filters){
            const dte = toFilterNumber(trade?.dte);
            if(filters.dte_min !== null && (dte === null || dte < filters.dte_min)) return false;
            if(filters.dte_max !== null && (dte === null || dte > filters.dte_max)) return false;

            const shortStrikeZ = toFilterNumber(trade?.short_strike_z);
            if(filters.expected_move_multiple !== null && (shortStrikeZ === null || Math.abs(shortStrikeZ) < filters.expected_move_multiple)) return false;

            const width = computeTradeWidth(trade);
            if(filters.width_min !== null && (width === null || width < filters.width_min)) return false;
            if(filters.width_max !== null && (width === null || width > filters.width_max)) return false;

            const pop = toFilterNumber(trade?.p_win_used ?? trade?.pop_delta_approx);
            if(filters.min_pop !== null && (pop === null || pop < filters.min_pop)) return false;

            const evToRisk = toFilterNumber(trade?.ev_to_risk);
            if(filters.min_ev_to_risk !== null && (evToRisk === null || evToRisk < filters.min_ev_to_risk)) return false;

            const spreadPct = toFilterNumber(trade?.bid_ask_spread_pct);
            if(filters.max_bid_ask_spread_pct !== null && (spreadPct === null || (spreadPct * 100) > filters.max_bid_ask_spread_pct)) return false;

            const openInterest = toFilterNumber(trade?.open_interest);
            if(filters.min_open_interest !== null && (openInterest === null || openInterest < filters.min_open_interest)) return false;

            const volume = toFilterNumber(trade?.volume);
            if(filters.min_volume !== null && (volume === null || volume < filters.min_volume)) return false;

            return true;
        }

        function isManualFilterEnabled(){
            return !!manualFiltersEnabled?.checked;
        }

        function renderCountsAndFilterStatus(totalCount, displayedCount){
            const total = Number.isFinite(Number(totalCount)) ? Number(totalCount) : 0;
            const shown = Number.isFinite(Number(displayedCount)) ? Number(displayedCount) : 0;

            if(tradeCountsBar){
                const lines = [`Trades loaded: ${total}`];
                if(isManualFilterEnabled() && manualFiltersApplied){
                    lines.push(`Trades after filters: ${shown}`);
                }
                tradeCountsBar.textContent = lines.join(' • ');
            }

            if(!manualFilterPill) return;
            if(!(isManualFilterEnabled() && manualFiltersApplied && appliedManualFilters)){
                manualFilterPill.style.display = 'none';
                manualFilterPill.innerHTML = '';
                return;
            }

            const text = composeFilterStatusText(appliedManualFilters);
            manualFilterPill.style.display = 'block';
            manualFilterPill.innerHTML = `
                <div style="display:inline-flex;align-items:center;gap:10px;padding:6px 10px;border-radius:999px;border:1px solid rgba(0,234,255,0.34);background:rgba(5,18,26,0.75);box-shadow:0 0 10px rgba(0,234,255,0.14);color:rgba(210,248,255,0.96);font-size:12px;">
                    <span>${text}</span>
                    <button id="manualFilterClearBtn" class="btn" type="button" style="padding:4px 8px;font-size:11px;">Clear</button>
                </div>
            `;
            const clearBtn = manualFilterPill.querySelector('#manualFilterClearBtn');
            if(clearBtn){
                clearBtn.addEventListener('click', () => {
                    manualFiltersApplied = false;
                    appliedManualFilters = null;
                    clearManualFilterInputs();
                    applyUnderlyingFilter();
                });
            }
        }

        function applyManualFiltersToTrades(trades){
            const rows = Array.isArray(trades) ? trades : [];
            if(!(isManualFilterEnabled() && manualFiltersApplied && appliedManualFilters)) return rows;
            return rows.filter((trade) => passesManualFilters(trade, appliedManualFilters));
        }

        let pendingGeneratedReport = null;

        function currentRouteKey(){
            const hash = String(location.hash || '').trim().toLowerCase();
            if(hash.startsWith('#/')) return String(hash.split('/')[1] || '').trim().toLowerCase();
            return hash.replace(/^#/, '').trim().toLowerCase();
        }

        function tradeSpreadType(trade){
            return String(trade?.spread_type || trade?.strategy || '').trim().toLowerCase();
        }

        function moduleIdFromRoute(trades){
            const route = currentRouteKey();
            if(route === 'debit-spreads') return 'debit_spreads';
            if(route === 'iron-condor' || route === 'strategy-iron-condor') return 'iron_condor';
            if(route === 'butterflies') return 'butterflies';
            if(route === 'calendar') return 'calendar';
            if(route === 'income') return 'income';
            if(route !== 'credit-spread') return null;

            const rows = Array.isArray(trades) ? trades : [];
            const hasPut = rows.some((trade) => {
                const spread = tradeSpreadType(trade);
                return spread === 'put_credit' || spread === 'credit_put_spread';
            });
            const hasCall = rows.some((trade) => {
                const spread = tradeSpreadType(trade);
                return spread === 'call_credit' || spread === 'credit_call_spread';
            });
            if(hasPut && !hasCall) return 'credit_put';
            if(hasCall && !hasPut) return 'credit_call';
            return null;
        }

        function recordSessionRunFromPayload(filename, payload, trades){
            const store = window.BenTradeSessionStatsStore;
            if(!store?.recordRun) return;
            if(!pendingGeneratedReport) return;
            if(pendingGeneratedReport !== '*' && pendingGeneratedReport !== String(filename || '')) return;

            const route = currentRouteKey();
            if(route === 'credit-spread'){
                const rows = Array.isArray(trades) ? trades : [];
                const putRows = rows.filter((trade) => {
                    const spread = tradeSpreadType(trade);
                    return spread === 'put_credit' || spread === 'credit_put_spread';
                });
                const callRows = rows.filter((trade) => {
                    const spread = tradeSpreadType(trade);
                    return spread === 'call_credit' || spread === 'credit_call_spread';
                });

                if(putRows.length) store.recordRun('credit_put', { trades: putRows });
                if(callRows.length) store.recordRun('credit_call', { trades: callRows });
                if(!putRows.length && !callRows.length){
                    const fallback = moduleIdFromRoute(rows) || 'credit_put';
                    store.recordRun(fallback, payload);
                }
                pendingGeneratedReport = null;
                return;
            }

            const moduleId = moduleIdFromRoute(trades);
            if(moduleId){
                store.recordRun(moduleId, payload);
            }
            pendingGeneratedReport = null;
        }

        function moduleIdForManualReject(trade){
            const route = currentRouteKey();
            if(route === 'debit-spreads') return 'debit_spreads';
            if(route === 'iron-condor' || route === 'strategy-iron-condor') return 'iron_condor';
            if(route === 'butterflies') return 'butterflies';
            if(route === 'calendar') return 'calendar';
            if(route === 'income') return 'income';

            const spread = tradeSpreadType(trade);
            if(spread === 'put_credit' || spread === 'credit_put_spread') return 'credit_put';
            if(spread === 'call_credit' || spread === 'credit_call_spread') return 'credit_call';
            return 'credit_put';
        }

        function getTradeUnderlying(trade){
            return String(trade?.underlying || trade?.underlying_symbol || '').trim().toUpperCase();
        }

        function populateUnderlyingOptions(trades){
            const symbols = Array.from(new Set((Array.isArray(trades) ? trades : []).map(getTradeUnderlying).filter(Boolean))).sort();
            const selected = getSelectedUnderlying();
            fileSelect.innerHTML = '<option value="ALL">All underlyings</option>';
            symbols.forEach(symbol => {
                const option = document.createElement('option');
                option.value = symbol;
                option.textContent = symbol;
                fileSelect.appendChild(option);
            });
            if(symbols.includes(selected)){
                fileSelect.value = selected;
            } else {
                fileSelect.value = 'ALL';
                setSelectedUnderlying('ALL');
            }
        }

        function applyUnderlyingFilter(){
            if(!Array.isArray(fullTradeList)){
                displayTrades([]);
                renderCountsAndFilterStatus(0, 0);
                return;
            }
            const selected = fileSelect.value || 'ALL';
            setSelectedUnderlying(selected);

            let tradesByUnderlying = fullTradeList;
            if(selected !== 'ALL'){
                tradesByUnderlying = fullTradeList.filter(trade => getTradeUnderlying(trade) === selected);
            }

            filteredTradeList = applyManualFiltersToTrades(tradesByUnderlying);
            displayTrades(filteredTradeList);
            renderCountsAndFilterStatus(fullTradeList.length, filteredTradeList.length);
        }

        function setReportsLoading(){
            reportSelect.innerHTML = '<option value="">Loading reports...</option>';
        }

        // Load available analysis files
        async function loadFiles(preferredReport = null) {
            try {
                const response = await fetch('/api/reports');
                const files = await response.json();

                if(!Array.isArray(files) || files.length === 0){
                    reportSelect.innerHTML = '<option value="">No reports yet</option>';
                    reportSelect.value = '';
                    fileSelect.innerHTML = '<option value="ALL">All underlyings</option>';
                    content.innerHTML = '<div class="loading">No analysis reports available yet</div>';
                    window.currentTrades = [];
                    window.currentReportFile = null;
                    renderDiagnosticPanel(null);
                    return;
                }

                reportSelect.innerHTML = '<option value="">Select a report...</option>';
                files.forEach(file => {
                    const option = document.createElement('option');
                    option.value = file;
                    option.textContent = formatReportName(file);
                    reportSelect.appendChild(option);
                });

                const saved = getSelectedReport();
                const current = window.currentReportFile;
                const selectedReport = [preferredReport, saved, current, files[0]].find(candidate => candidate && files.includes(candidate)) || files[0];
                reportSelect.value = selectedReport;
                setSelectedReport(selectedReport);
                await loadAnalysis(selectedReport);
            } catch (error) {
                console.error('Error loading files:', error);
                reportSelect.innerHTML = '<option value="">Error loading reports</option>';
                fileSelect.innerHTML = '<option value="ALL">All underlyings</option>';
                content.innerHTML = '<div class="error">Error loading reports</div>';
                renderDiagnosticPanel(null);
            }
        }

        function buildStatsFromTrades(trades){
            const safeTrades = Array.isArray(trades) ? trades : [];
            const scores = safeTrades.map(t => toNumeric(t.composite_score ?? t.trade_quality_score)).filter(v => v !== null);
            const pops = safeTrades.map(t => toNumeric(t.p_win_used ?? t.pop_delta_approx)).filter(v => v !== null);
            const rors = safeTrades.map(t => toNumeric(t.return_on_risk)).filter(v => v !== null);
            const avg = (values) => values.length ? (values.reduce((sum, value) => sum + value, 0) / values.length) : null;
            const bestUnderlying = safeTrades.length
                ? String((safeTrades.reduce((best, trade) => {
                    const score = toNumeric(trade.composite_score ?? trade.trade_quality_score) ?? -1;
                    const bestScore = toNumeric(best.composite_score ?? best.trade_quality_score) ?? -1;
                    return score > bestScore ? trade : best;
                }, safeTrades[0])?.underlying || safeTrades[0]?.underlying_symbol || '')).toUpperCase() || null
                : null;

            return {
                total_candidates: safeTrades.length,
                accepted_trades: safeTrades.length,
                rejected_trades: 0,
                acceptance_rate: safeTrades.length > 0 ? 1 : 0,
                best_trade_score: scores.length ? Math.max(...scores) : null,
                worst_accepted_score: scores.length ? Math.min(...scores) : null,
                avg_trade_score: avg(scores),
                avg_probability: avg(pops),
                avg_return_on_risk: avg(rors),
                best_underlying: bestUnderlying,
            };
        }

        function normalizeReportPayload(payload){
            if(Array.isArray(payload)){
                return {
                    trades: payload,
                    reportStats: buildStatsFromTrades(payload),
                    diagnostics: {},
                    sourceHealth: {},
                    debugStageCounts: {},
                    validationWarnings: [],
                };
            }
            if(payload && typeof payload === 'object'){
                if(Array.isArray(payload.trades)){
                    const reportStats = (payload.report_stats && typeof payload.report_stats === 'object')
                        ? payload.report_stats
                        : buildStatsFromTrades(payload.trades);
                    const diagnostics = (payload.diagnostics && typeof payload.diagnostics === 'object')
                        ? payload.diagnostics
                        : {};
                    const sourceHealth = (payload.source_health && typeof payload.source_health === 'object')
                        ? payload.source_health
                        : ((diagnostics.source_health && typeof diagnostics.source_health === 'object') ? diagnostics.source_health : {});

                    const debugStageCounts = (payload.debug_stage_counts && typeof payload.debug_stage_counts === 'object')
                        ? payload.debug_stage_counts
                        : ((diagnostics.debug_stage_counts && typeof diagnostics.debug_stage_counts === 'object') ? diagnostics.debug_stage_counts : {});
                    const validationWarnings = Array.isArray(payload.validation_warnings)
                        ? payload.validation_warnings
                        : (Array.isArray(diagnostics.validation_warnings) ? diagnostics.validation_warnings : []);

                    return { trades: payload.trades, reportStats, diagnostics, sourceHealth, debugStageCounts, validationWarnings };
                }
                return { trades: [], reportStats: {}, diagnostics: payload, sourceHealth: {}, debugStageCounts: {}, validationWarnings: [] };
            }
            return { trades: [], reportStats: {}, diagnostics: {}, sourceHealth: {}, debugStageCounts: {}, validationWarnings: [] };
        }

        function formatReportName(filename) {
            const dateTimeStr = filename.replace('analysis_', '').replace('.json', '');
            const [dateStr, timeStr] = dateTimeStr.split('_');

            if (!dateStr || !timeStr || dateStr.length !== 8 || timeStr.length !== 6) {
                return dateTimeStr;
            }

            const year = dateStr.substring(0, 4);
            const month = parseInt(dateStr.substring(4, 6)) - 1;
            const day = parseInt(dateStr.substring(6, 8));

            const hour = timeStr.substring(0, 2);
            const minute = timeStr.substring(2, 4);
            const second = timeStr.substring(4, 6);

            const date = new Date(year, month, day);
            const monthName = date.toLocaleString('en-US', { month: 'long' });
            const dayWithSuffix = getDayWithSuffix(day);
            const timeFormatted = `${hour}:${minute}:${second}`;

            return `${monthName} ${dayWithSuffix} at ${timeFormatted}`;
        }

        function getDayWithSuffix(day) {
            if (day > 3 && day < 21) return day + 'th';
            switch (day % 10) {
                case 1: return day + 'st';
                case 2: return day + 'nd';
                case 3: return day + 'rd';
                default: return day + 'th';
            }
        }

        async function loadAnalysis(filename) {
            if (!filename) {
                content.innerHTML = '<div class="loading">Select an analysis report to view trade details</div>';
                renderDiagnosticPanel(null);
                return;
            }

            try {
                console.log('[ui] loadAnalysis:', filename);
                content.innerHTML = '<div class="loading">Loading analysis...</div>';

                const response = await fetch(`/api/reports/${filename}`);
                const payload = await response.json();
                const { trades, reportStats, diagnostics, sourceHealth, debugStageCounts, validationWarnings } = normalizeReportPayload(payload);
                console.log('[ui] loaded trades count:', Array.isArray(trades) ? trades.length : 'N/A', trades);

                function toFloat(x){
                    if(x === null || x === undefined) return null;
                    if(typeof x === 'number') return x;
                    try{
                        let s = String(x).trim();
                        if(s.endsWith('%')){ s = s.slice(0,-1); return parseFloat(s)/100; }
                        return parseFloat(s);
                    }catch(e){ return null; }
                }

                function passesQualityGate(tr){
                    try{
                        if(tr.model_evaluation && tr.model_evaluation.recommendation === 'REJECT') return false;
                    }catch(e){}

                    const ev = toFloat(tr.ev_per_share ?? tr.expected_value);
                    const kelly = toFloat(tr.kelly_fraction);
                    const ror = toFloat(tr.return_on_risk);
                    const max_profit = toFloat(tr.max_profit_per_share ?? tr.max_profit);
                    const max_loss = toFloat(tr.max_loss_per_share ?? tr.max_loss);

                    if(ev !== null && !Number.isNaN(ev) && ev < 0) return false;
                    if(kelly !== null && !Number.isNaN(kelly) && kelly < 0) return false;
                    if(ror !== null && !Number.isNaN(ror) && ror < 0.10) return false;
                    if(max_profit && max_loss && max_profit > 0 && (max_loss / max_profit) > 8) return false;

                    return true;
                }

                window.allTrades = trades;
                const filtered = Array.isArray(trades) ? trades.filter(passesQualityGate) : trades;
                const keyedFiltered = Array.isArray(filtered)
                    ? filtered.map((trade) => {
                        const tradeKey = String(trade?.trade_key || '').trim();
                        return tradeKey ? { ...trade, trade_key: tradeKey } : trade;
                    })
                    : filtered;

                const persistedRejected = new Set();
                if(Array.isArray(keyedFiltered) && filename && api?.getRejectDecisions){
                    try{
                        const decisionPayload = await api.getRejectDecisions(filename);
                        const list = Array.isArray(decisionPayload?.decisions) ? decisionPayload.decisions : [];
                        list.forEach(item => {
                            if(item && item.type === 'reject' && item.trade_key){
                                persistedRejected.add(String(item.trade_key));
                            }
                        });
                    }catch(e){
                        console.warn('[BenTrade] Failed to load decision file', e);
                    }
                }

                const persistedFiltered = Array.isArray(keyedFiltered)
                    ? keyedFiltered.filter(trade => !persistedRejected.has(String(trade.trade_key || '')))
                    : keyedFiltered;

                fullTradeList = Array.isArray(persistedFiltered) ? [...persistedFiltered] : [];
                filteredTradeList = [...fullTradeList];
                window.currentTrades = filteredTradeList;
                window.currentReportFile = filename;
                if(session?.setCurrentTrades) session.setCurrentTrades(fullTradeList);
                if(session?.setCurrentReportFile) session.setCurrentReportFile(filename);
                setSelectedReport(filename);

                manualFiltersApplied = false;
                appliedManualFilters = null;
                setManualFilterPanelVisible(false);
                clearManualFilterInputs();

                if(Array.isArray(trades) && filtered.length !== trades.length){
                    console.log(`[ui] filtered out ${trades.length - filtered.length} rejected trade(s)`);
                }

                populateUnderlyingOptions(fullTradeList);
                applyUnderlyingFilter();
                renderDiagnosticPanel({ reportStats, diagnostics, sourceHealth, trades, debugStageCounts, validationWarnings });
                recordSessionRunFromPayload(filename, payload, trades);
                window.BenTradeSourceHealthStore?.fetchSourceHealth?.({ force: true }).catch(() => {});
            } catch (error) {
                console.error('Error loading analysis:', error);
                content.innerHTML = '<div class="error">Error loading analysis data</div>';
                renderDiagnosticPanel(null);
            }
        }

        function escapeHtml(s){
            if(s === null || s === undefined) return '';
            return String(s)
                .replaceAll('&','&amp;')
                .replaceAll('<','&lt;')
                .replaceAll('>','&gt;')
                .replaceAll('"','&quot;')
                .replaceAll("'",'&#039;')
                .replaceAll('\n','<br/>');
        }

        // --------- Formatters ----------
        function fmtNumber(v, decimals = 2, prefix = '$', suffix = ''){
            const numeric = toNumeric(v);
            if (numeric === null) return 'N/A';
            return (prefix || '') + numeric.toFixed(decimals) + (suffix || '');
        }

        function fmtPercent(v, decimals = 1){
            const numeric = toNumeric(v);
            if (numeric === null) return 'N/A';
            return (numeric * 100).toFixed(decimals) + '%';
        }

        function toNumeric(value){
            if(value === null || value === undefined || typeof value === 'boolean') return null;
            if(typeof value === 'string' && value.trim() === '') return null;
            const num = Number(value);
            return Number.isFinite(num) ? num : null;
        }

        function avgMetric(trades, selector){
            if(!Array.isArray(trades) || trades.length === 0) return null;
            const values = trades
                .map(selector)
                .map(toNumeric)
                .filter(v => v !== null);
            if(values.length === 0) return null;
            return values.reduce((sum, val) => sum + val, 0) / values.length;
        }

        function normalizeSourceStatus(sourceKey, status){
            window._sourceHealthEvidence = window._sourceHealthEvidence || {};
            const raw = (status && typeof status === 'object') ? status.status : status;
            const normalized = String(raw || '').trim().toLowerCase();
            if(normalized === 'green' || normalized === 'yellow' || normalized === 'red') return normalized;

            const httpCode = (status && typeof status === 'object') ? Number(status.last_http) : NaN;
            if(Number.isFinite(httpCode) && httpCode >= 200 && httpCode < 300){
                window._sourceHealthEvidence[sourceKey] = true;
                return 'green';
            }

            if(window._sourceHealthEvidence[sourceKey]) return 'green';
            return 'yellow';
        }

        function statValue(value, formatter){
            const normalized = toNumeric(value);
            if(normalized === null) return '--';
            return formatter ? formatter(normalized) : String(normalized);
        }

        function statTextValue(value){
            if(value === null || value === undefined) return '--';
            const text = String(value).trim();
            return text !== '' ? text : '--';
        }

        function sourceTooltipText(sourceInfo, status){
            const info = (sourceInfo && typeof sourceInfo === 'object') ? sourceInfo : {};
            const httpCode = Number(info.last_http);
            const hasHttp = Number.isFinite(httpCode);
            const message = (info.message != null && String(info.message).trim() !== '') ? String(info.message).trim() : '';

            if(status === 'green'){
                if(hasHttp) return `Healthy. API response HTTP ${httpCode}.`;
                return 'Healthy. API response HTTP 200.';
            }

            if(status === 'yellow'){
                if(hasHttp && message) return `Degraded: ${message} (HTTP ${httpCode}).`;
                if(hasHttp) return `Degraded. Last upstream response HTTP ${httpCode}.`;
                if(message) return `Degraded: ${message}.`;
                return 'Degraded: intermittent, timeout, or rate-limited upstream behavior.';
            }

            if(hasHttp && message) return `Down/blocked: ${message} (HTTP ${httpCode}).`;
            if(hasHttp) return `Down/blocked. Last upstream response HTTP ${httpCode}.`;
            if(message) return `Down/blocked: ${message}.`;
            return 'Down/blocked: authentication, repeated failures, or sustained network outage.';
        }

        function renderDiagnosticPanel(reportData){
            const sourceHealthRows = doc.getElementById('sourceHealthRows');
            const reportStatsGrid = doc.getElementById('reportStatsGrid');
            if(!sourceHealthRows || !reportStatsGrid) return;

            const payload = (reportData && typeof reportData === 'object' && !Array.isArray(reportData)) ? reportData : {};
            const payloadTrades = Array.isArray(reportData)
                ? reportData
                : (Array.isArray(payload.trades) ? payload.trades : (Array.isArray(window.allTrades) ? window.allTrades : []));
            const diagnostics = (payload.diagnostics && typeof payload.diagnostics === 'object') ? payload.diagnostics : {};

            const sourceHealth = (payload.sourceHealth && typeof payload.sourceHealth === 'object')
                ? payload.sourceHealth
                : ((payload.source_health && typeof payload.source_health === 'object') ? payload.source_health : {});
            const sources = [
                { key: 'finnhub', label: 'Finnhub' },
                { key: 'yahoo', label: 'Yahoo' },
                { key: 'tradier', label: 'Tradier' },
                { key: 'fred', label: 'FRED' },
            ];

            sourceHealthRows.innerHTML = sources.map(source => {
                const sourceInfo = sourceHealth[source.key];
                const status = normalizeSourceStatus(source.key, sourceInfo);
                const tooltipText = sourceTooltipText(sourceInfo, status);
                return `
                    <div class="diagnosticRow">
                        <span class="diagnosticLabel">${source.label}</span>
                        <span class="status-wrap" role="img" aria-label="${source.label} status ${status}">
                            <span class="status-dot status-${status}"></span>
                            <span class="status-tooltip">${escapeHtml(tooltipText)}</span>
                        </span>
                    </div>
                `;
            }).join('');

            const snapshot = window.BenTradeSessionStatsStore?.getState?.() || {
                runs: 0,
                total_candidates: 0,
                accepted_trades: 0,
                rejected_trades: 0,
                acceptance_rate: 0,
                best_score: null,
                avg_quality_score: null,
                avg_return_on_risk: null,
            };

            const stats = [
                { label: 'Total candidates', value: String(Math.round(Number(snapshot.total_candidates || 0))) },
                { label: 'Accepted trades/ideas', value: String(Math.round(Number(snapshot.accepted_trades || 0))) },
                { label: 'Rejected', value: String(Math.round(Number(snapshot.rejected_trades || 0))) },
                { label: 'Acceptance rate', value: fmtPercent(Number(snapshot.acceptance_rate || 0), 1) },
                { label: 'Best score', value: (toNumeric(snapshot.best_score) === null ? 'N/A' : fmtPercent(snapshot.best_score, 1)) },
                { label: 'Avg quality score', value: (toNumeric(snapshot.avg_quality_score) === null ? 'N/A' : fmtPercent(snapshot.avg_quality_score, 1)) },
                { label: 'Avg return on risk', value: (toNumeric(snapshot.avg_return_on_risk) === null ? 'N/A' : fmtPercent(snapshot.avg_return_on_risk, 1)) },
                { label: 'Session runs', value: String(Math.round(Number(snapshot.runs || 0))) },
            ];

            reportStatsGrid.innerHTML = stats.map(stat => `
                <div class="statTile">
                    <div class="statLabel">${stat.label}</div>
                    <div class="statValue">${stat.value}</div>
                </div>
            `).join('');

            renderScanDiagnosticsPanel(payload);
        }

        function ensureScanDiagnosticsHost(){
            const fileSelector = scope.querySelector('.file-selector');
            if(!fileSelector) return null;

            let panel = doc.getElementById('scanDiagnosticsPanel');
            if(panel) return panel;

            panel = doc.createElement('details');
            panel.id = 'scanDiagnosticsPanel';
            panel.style.marginTop = '10px';
            panel.style.border = '1px solid rgba(0,220,255,0.18)';
            panel.style.borderRadius = '10px';
            panel.style.background = 'rgba(3,10,18,0.45)';
            panel.style.padding = '8px 10px';

            const summary = doc.createElement('summary');
            summary.textContent = 'Scan Diagnostics';
            summary.style.cursor = 'pointer';
            summary.style.fontSize = '12px';
            summary.style.color = 'rgba(210,248,255,0.96)';

            const body = doc.createElement('div');
            body.id = 'scanDiagnosticsBody';
            body.style.marginTop = '8px';
            body.style.fontSize = '12px';
            body.style.color = 'rgba(190,236,244,0.94)';

            panel.appendChild(summary);
            panel.appendChild(body);
            fileSelector.appendChild(panel);
            return panel;
        }

        function renderScanDiagnosticsPanel(payload){
            const panel = ensureScanDiagnosticsHost();
            if(!panel) return;
            const body = doc.getElementById('scanDiagnosticsBody');
            if(!body) return;

            const stageCounts = (payload.debugStageCounts && typeof payload.debugStageCounts === 'object')
                ? payload.debugStageCounts
                : ((payload.diagnostics && typeof payload.diagnostics.debug_stage_counts === 'object') ? payload.diagnostics.debug_stage_counts : {});
            const warnings = Array.isArray(payload.validationWarnings)
                ? payload.validationWarnings
                : (Array.isArray(payload.diagnostics?.validation_warnings) ? payload.diagnostics.validation_warnings : []);

            const stageEntries = Object.entries(stageCounts || {});
            const warningRows = warnings.slice(-8);

            const stagesHtml = stageEntries.length
                ? `<div style="display:flex;flex-wrap:wrap;gap:6px;">${stageEntries.map(([stage, count]) => `
                    <span style="padding:3px 8px;border-radius:999px;border:1px solid rgba(0,234,255,0.28);background:rgba(5,18,26,0.68);color:rgba(210,248,255,0.95);">
                        ${escapeHtml(stage)}: ${escapeHtml(String(count))}
                    </span>
                `).join('')}</div>`
                : '<div style="opacity:0.8;">No stage counts reported.</div>';

            const warningsHtml = warningRows.length
                ? `<div style="margin-top:8px;display:grid;gap:6px;">${warningRows.map((row) => {
                    const code = escapeHtml(String(row?.code || row?.warning_code || 'WARN'));
                    const message = escapeHtml(String(row?.message || row?.detail || 'Validation warning'));
                    return `<div style="padding:6px 8px;border:1px solid rgba(255,215,0,0.35);border-radius:8px;background:rgba(46,36,10,0.45);color:rgba(255,235,160,0.96);"><strong>${code}</strong> — ${message}</div>`;
                }).join('')}</div>`
                : '<div style="margin-top:8px;opacity:0.8;">No validation warnings.</div>';

            body.innerHTML = `
                <div style="font-size:11px;opacity:0.85;margin-bottom:4px;">Stage counts</div>
                ${stagesHtml}
                <div style="font-size:11px;opacity:0.85;margin-top:10px;">Last validation warnings</div>
                ${warningsHtml}
            `;
        }

        function contractDollars(trade, contractField, shareField, fallbackField){
            const computed = (trade && typeof trade.computed === 'object') ? trade.computed : {};
            if(contractField === 'max_profit_per_contract'){
                const n = toNumeric(computed.max_profit);
                if(n !== null) return n;
            }
            if(contractField === 'max_loss_per_contract'){
                const n = toNumeric(computed.max_loss);
                if(n !== null) return n;
            }
            if(fallbackField === 'expected_value'){
                const n = toNumeric(computed.expected_value);
                if(n !== null) return n;
            }

            const contractValue = toNumeric(trade?.[contractField]);
            if(contractValue !== null) return contractValue;

            const shareValue = toNumeric(trade?.[shareField]);
            if(shareValue !== null) return shareValue * 100;

            const fallbackValue = toNumeric(trade?.[fallbackField]);
            if(fallbackValue !== null) return fallbackValue;

            return null;
        }

        function metricNumber(trade, computedKey, ...legacyKeys){
            const computed = (trade && typeof trade.computed === 'object') ? trade.computed : {};
            const fromComputed = toNumeric(computed?.[computedKey]);
            if(fromComputed !== null) return fromComputed;
            for(const key of legacyKeys){
                const value = toNumeric(trade?.[key]);
                if(value !== null) return value;
            }
            return null;
        }

        function runMetricFormattingSanityCheck(){
            if(window.__benTradeMetricNullSanityChecked) return;
            window.__benTradeMetricNullSanityChecked = true;

            const emptyTrade = { computed: {}, details: {} };
            const checks = [
                fmtNumber(contractDollars(emptyTrade, 'max_profit_per_contract', 'max_profit_per_share', 'max_profit'), 2, '$') === 'N/A',
                fmtNumber(contractDollars(emptyTrade, 'max_loss_per_contract', 'max_loss_per_share', 'max_loss'), 2, '$') === 'N/A',
                fmtPercent(metricNumber(emptyTrade, 'pop', 'p_win_used', 'pop_delta_approx', 'pop_approx'), 1) === 'N/A',
                fmtPercent(metricNumber(emptyTrade, 'return_on_risk', 'return_on_risk'), 1) === 'N/A',
                fmtNumber(contractDollars(emptyTrade, 'ev_per_contract', 'ev_per_share', 'expected_value'), 2, '$') === 'N/A',
                fmtPercent(metricNumber(emptyTrade, 'kelly_fraction', 'kelly_fraction'), 1) === 'N/A',
                fmtPercent(metricNumber(emptyTrade, 'iv_rank', 'iv_rank'), 1) === 'N/A',
                fmtPercent(metricNumber(emptyTrade, 'strike_dist_pct', 'strike_distance_pct', 'strike_distance_vs_expected_move', 'expected_move_ratio'), 2) === 'N/A',
            ];

            if(!checks.every(Boolean)){
                console.warn('[dev-sanity] Missing computed metrics should render as N/A, not 0.');
            }
        }
        runMetricFormattingSanityCheck();

        function detailValue(trade, detailsKey, ...legacyKeys){
            const details = (trade && typeof trade.details === 'object') ? trade.details : {};
            const fromDetails = details?.[detailsKey];
            if(fromDetails !== null && fromDetails !== undefined && String(fromDetails).trim() !== '') return fromDetails;
            for(const key of legacyKeys){
                const value = trade?.[key];
                if(value !== null && value !== undefined && String(value).trim() !== '') return value;
            }
            return null;
        }

        function formatTradeType(type) {
            const key = String(type || '').toLowerCase();
            if(key === 'put_credit' || key === 'credit_put_spread') return '📉 Put Credit Spread';
            if(key === 'call_credit' || key === 'credit_call_spread') return '📈 Call Credit Spread';
            if(key === 'debit_call_spread') return '📈 Call Debit Spread';
            if(key === 'debit_put_spread') return '📉 Put Debit Spread';
            if(key === 'call_debit') return '📈 Call Debit Spread';
            if(key === 'put_debit') return '📉 Put Debit Spread';
            if(key === 'iron_condor') return '🦅 Iron Condor';
            if(key === 'butterfly_debit' || key === 'debit_butterfly') return '🦋 Debit Butterfly';
            if(key === 'debit_call_butterfly') return '🦋 Call Debit Butterfly';
            if(key === 'debit_put_butterfly') return '🦋 Put Debit Butterfly';
            if(key === 'iron_butterfly') return '🦋 Iron Butterfly';
            if(key === 'calendar_call_spread') return '🗓️ Call Calendar Spread';
            if(key === 'calendar_put_spread') return '🗓️ Put Calendar Spread';
            if(key === 'cash_secured_put' || key === 'csp') return '💵 Cash Secured Put';
            if(key === 'covered_call') return '📞 Covered Call';
            return String(type || 'Spread');
        }

        function extractTradeWarning(trade){
            if(trade?.data_warning) return 'WARN';
            const warnings = trade?.validation_warnings;
            if(Array.isArray(warnings) && warnings.length) return 'WARN';
            if(Array.isArray(trade?.warnings) && trade.warnings.length) return 'WARN';
            return '';
        }

        function renderTradePills(trade){
            const pillsPayload = (trade && typeof trade.pills === 'object') ? trade.pills : {};
            const strategy = String(pillsPayload.strategy_label || '').trim() || 'Strategy';
            const dteLabel = String(pillsPayload.dte_label || '').trim();
            const dteFront = toNumeric(pillsPayload.dte_front);
            const dteBack = toNumeric(pillsPayload.dte_back);
            const dte = toNumeric(pillsPayload.dte);
            const dteText = dteLabel
                ? dteLabel
                : (dteFront !== null && dteBack !== null)
                    ? `DTE ${Math.round(dteFront)}/${Math.round(dteBack)}`
                    : (dte === null ? 'DTE —' : `DTE ${Math.round(dte)}`);
            const pop = toNumeric(pillsPayload.pop);
            const popText = pop === null ? 'POP N/A' : `POP ${pop.toFixed(2)}`;
            const oi = toNumeric(pillsPayload.oi);
            const vol = toNumeric(pillsPayload.vol);
            const liqText = `${oi === null ? 'OI —' : `OI ${Math.round(oi)}`}/${vol === null ? 'Vol —' : `Vol ${Math.round(vol)}`}`;
            const regime = String(pillsPayload.regime_label || '').trim();
            const regimeText = regime || 'Regime N/A';
            const warn = extractTradeWarning(trade);

            const mk = (text, warnPill = false) => `<span style="padding:3px 8px;border-radius:999px;border:1px solid ${warnPill ? 'rgba(255,215,0,0.40)' : 'rgba(0,234,255,0.28)'};background:${warnPill ? 'rgba(46,36,10,0.50)' : 'rgba(5,18,26,0.70)'};color:${warnPill ? 'rgba(255,235,160,0.96)' : 'rgba(210,248,255,0.95)'};font-size:11px;line-height:1;">${escapeHtml(text)}</span>`;

            const pills = [
                mk(strategy),
                mk(dteText),
                mk(popText),
                mk(liqText),
                mk(regimeText),
            ];
            if(warn) pills.push(mk(warn, true));
            return `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;">${pills.join('')}</div>`;
        }

        
                // Display trades in a nice grid
        function displayTrades(trades) {
                    const safeTrades = Array.isArray(trades) ? trades : [];
                    window.currentTrades = safeTrades;
                    console.log('[ui] displayTrades, trades[0] keys:', safeTrades && safeTrades[0] ? Object.keys(safeTrades[0]) : 'none');

            
            window._collapsed = window._collapsed || {};
            // Default ALL trades to collapsed on first render
                    safeTrades.forEach((_, i) => { if (window._collapsed[i] === undefined) window._collapsed[i] = true; });
const html = `
                <div class="trades-grid">
                            ${safeTrades.map((trade, idx) => `
                        <div class="trade-card" data-idx="${idx}">
                            <div class="trade-header trade-header-click" onclick="toggleTrade(${idx})" role="button" aria-label="Toggle trade">
                                <div class="trade-header-left"><span id="chev-${idx}" class="chev">${window._collapsed && window._collapsed[idx] === false ? "▾" : "▸"}</span></div>
                                <div class="trade-header-center">
                                    <div class="trade-type">${formatTradeType(trade.spread_type)}</div>
                                    <div class="trade-subtitle">
                                        <span class="underlying-symbol">${trade.underlying || trade.underlying_symbol || ''}</span>
                                        ${(function(){
                                            const spreadType = String(trade.spread_type || trade.strategy || '').toLowerCase();
                                            if(spreadType === 'iron_condor'){
                                                return `<span class="trade-strikes-inline">P ${trade.put_short_strike ?? 'NA'}/${trade.put_long_strike ?? 'NA'} • C ${trade.call_short_strike ?? 'NA'}/${trade.call_long_strike ?? 'NA'}</span>`;
                                            }
                                            if(spreadType === 'debit_call_butterfly' || spreadType === 'debit_put_butterfly' || spreadType === 'iron_butterfly'){
                                                return `<span class="trade-strikes-inline">${trade.lower_strike ?? 'NA'} / ${trade.center_strike ?? trade.short_strike ?? 'NA'} / ${trade.upper_strike ?? 'NA'}</span>`;
                                            }
                                            if(spreadType === 'calendar_call_spread' || spreadType === 'calendar_put_spread'){
                                                return `<span class="trade-strikes-inline">K ${trade.strike ?? trade.short_strike ?? 'NA'} • ${trade.expiration_near ?? 'NA'} → ${trade.expiration_far ?? trade.expiration ?? 'NA'}</span>`;
                                            }
                                            if(spreadType === 'cash_secured_put' || spreadType === 'covered_call'){
                                                return `<span class="trade-strikes-inline">K ${trade.short_strike ?? trade.strike ?? 'NA'} • ${trade.expiration ?? 'NA'}</span>`;
                                            }
                                            return `<span class="trade-strikes-inline">${trade.short_strike}/${trade.long_strike}</span>`;
                                        })()}
                                        <span class="underlying-price">(${fmtNumber(trade.underlying_price,2,'','')})</span>
                                    </div>
                                    ${renderTradePills(trade)}
                                    <div style="margin-top:4px;display:flex;align-items:center;gap:6px;opacity:0.76;font-size:10.5px;">
                                        <span>ID: ${escapeHtml(String(trade.trade_key || trade._trade_key || 'N/A'))}</span>
                                        <button class="btn" style="padding:1px 5px;font-size:10px;min-height:20px;line-height:1;" onclick="copyTradeId(${idx}); event.stopPropagation();" title="Copy trade ID" aria-label="Copy trade ID">⧉</button>
                                    </div>
                                    <div class="trade-rank-line">Rank Score: ${fmtPercent((trade.rank_score ?? trade.composite_score), 1)}</div>
                                </div>
                                <div class="trade-header-right">
                                    ${trade.data_warning ? `<span class="data-warning-pill">Data Warning</span>` : ``}
                                </div>
                            </div>

                            <div id="tradeBody-${idx}" class="trade-collapsible ${window._collapsed && window._collapsed[idx] === false ? "" : "is-collapsed"}">
                                <div class="trade-body">
                                    <div class="section section-core">
                                        <div class="section-title">CORE METRICS</div>
                                        <div class="metric-grid">
                                            <div class="metric">
                                                <div class="metric-label" data-metric="max_profit">Max Profit</div>
                                                <div class="metric-value positive">${fmtNumber(contractDollars(trade, 'max_profit_per_contract', 'max_profit_per_share', 'max_profit'),2,'$')}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="max_loss">Max Loss</div>
                                                <div class="metric-value negative">${fmtNumber(contractDollars(trade, 'max_loss_per_contract', 'max_loss_per_share', 'max_loss'),2,'$')}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="pop">Probability</div>
                                                <div class="metric-value neutral">${fmtPercent(metricNumber(trade, 'pop', 'p_win_used', 'pop_delta_approx', 'pop_approx'),1)}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="return_on_risk">Return on Risk</div>
                                                <div class="metric-value ${metricNumber(trade, 'return_on_risk', 'return_on_risk') != null && metricNumber(trade, 'return_on_risk', 'return_on_risk') > 0.2 ? 'positive' : 'neutral'}">${fmtPercent(metricNumber(trade, 'return_on_risk', 'return_on_risk'),1)}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="ev">Expected Value</div>
                                                <div class="metric-value ${contractDollars(trade, 'ev_per_contract', 'ev_per_share', 'expected_value') != null ? (contractDollars(trade, 'ev_per_contract', 'ev_per_share', 'expected_value') > 0 ? 'positive' : 'negative') : 'neutral'}">${fmtNumber(contractDollars(trade, 'ev_per_contract', 'ev_per_share', 'expected_value'),2,'$')}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="kelly_fraction">Kelly Fraction</div>
                                                <div class="metric-value ${metricNumber(trade, 'kelly_fraction', 'kelly_fraction') != null ? (metricNumber(trade, 'kelly_fraction', 'kelly_fraction') > 0 ? 'positive' : 'negative') : 'neutral'}">${fmtPercent(metricNumber(trade, 'kelly_fraction', 'kelly_fraction'),1)}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="iv_rank">IV Rank</div>
                                                <div class="metric-value ${metricNumber(trade, 'iv_rank', 'iv_rank') != null && metricNumber(trade, 'iv_rank', 'iv_rank') > 0.5 ? 'positive' : 'neutral'}">${fmtPercent(metricNumber(trade, 'iv_rank', 'iv_rank'),1)}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="short_strike_z">Short Strike Z</div>
                                                <div class="metric-value ${metricNumber(trade, 'short_strike_z', 'short_strike_z') != null && metricNumber(trade, 'short_strike_z', 'short_strike_z') > 1 ? 'positive' : 'neutral'}">${fmtNumber(metricNumber(trade, 'short_strike_z', 'short_strike_z'),2,'','')}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="bid_ask_spread_pct">Bid-Ask %</div>
                                                <div class="metric-value ${metricNumber(trade, 'bid_ask_pct', 'bid_ask_spread_pct') != null && metricNumber(trade, 'bid_ask_pct', 'bid_ask_spread_pct') < 0.1 ? 'positive' : 'neutral'}">${fmtPercent(metricNumber(trade, 'bid_ask_pct', 'bid_ask_spread_pct'),2)}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="strike_distance_pct">Strike Dist %</div>
                                                <div class="metric-value">${fmtPercent(metricNumber(trade, 'strike_dist_pct', 'strike_distance_pct', 'strike_distance_vs_expected_move', 'expected_move_ratio'),2)}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="rsi_14">RSI14</div>
                                                <div class="metric-value ${metricNumber(trade, 'rsi14', 'rsi14', 'rsi_14') != null && metricNumber(trade, 'rsi14', 'rsi14', 'rsi_14') > 60 ? 'negative' : 'neutral'}">${fmtNumber(metricNumber(trade, 'rsi14', 'rsi14', 'rsi_14'),1,'','')}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="realized_vol_20d">RV (20d)</div>
                                                <div class="metric-value">${fmtPercent(metricNumber(trade, 'rv_20d', 'realized_vol_20d', 'rv_20d'),2)}</div>
                                            </div>
                                        </div>
                                    </div>

                                    <div class="section section-details">
                                        <div class="section-title">TRADE DETAILS</div>
                                        <div class="trade-details">
                                            <div class="detail-row">
                                                <span class="detail-label" data-metric="break_even">Break Even</span>
                                                <span class="detail-value">${(function(){
                                                    const spreadType = String(trade.spread_type || trade.strategy || '').toLowerCase();
                                                    if(spreadType === 'iron_condor' || spreadType === 'debit_call_butterfly' || spreadType === 'debit_put_butterfly' || spreadType === 'iron_butterfly'){
                                                        return `${fmtNumber(trade.break_even_low,2,'$')} / ${fmtNumber(trade.break_even_high,2,'$')}`;
                                                    }
                                                    return fmtNumber(detailValue(trade, 'break_even', 'break_even'),2,'$');
                                                })()}</span>
                                            </div>
                                            ${String(trade.spread_type || trade.strategy || '').toLowerCase() === 'iron_condor' ? `
                                            <div class="detail-row">
                                                <span class="detail-label">Put / Call Width</span>
                                                <span class="detail-value">${fmtNumber(trade.width_put,2,'','')} / ${fmtNumber(trade.width_call,2,'','')}</span>
                                            </div>
                                            ` : ''}
                                            ${['debit_call_butterfly','debit_put_butterfly','iron_butterfly'].includes(String(trade.spread_type || trade.strategy || '').toLowerCase()) ? `
                                            <div class="detail-row">
                                                <span class="detail-label">Payoff Summary</span>
                                                <span class="detail-value">Center ${fmtNumber(trade.center_strike,2,'$')} • Peak ${fmtNumber(trade.peak_profit_at_center,2,'$')} • Slope ${fmtNumber(trade.payoff_slope,2,'$','/$')}</span>
                                            </div>
                                            ` : ''}
                                            ${['calendar_call_spread','calendar_put_spread'].includes(String(trade.spread_type || trade.strategy || '').toLowerCase()) ? `
                                            <div class="detail-row">
                                                <span class="detail-label">Why Score</span>
                                                <span class="detail-value">Term ${fmtPercent(trade.why_term_structure,0)} • Move ${fmtPercent(trade.why_move_risk,0)} • Liquidity ${fmtPercent(trade.why_liquidity,0)}</span>
                                            </div>
                                            <div class="detail-row">
                                                <span class="detail-label">Calendar Metrics</span>
                                                <span class="detail-value">Debit ${fmtNumber(trade.net_debit,2,'$')} • Theta ${fmtNumber(trade.theta_structure,4,'','')} • Vega ${fmtNumber(trade.vega_exposure,4,'','')}</span>
                                            </div>
                                            ` : ''}
                                            ${['cash_secured_put','covered_call'].includes(String(trade.spread_type || trade.strategy || '').toLowerCase()) ? `
                                            <div class="detail-row">
                                                <span class="detail-label">Income Why Score</span>
                                                <span class="detail-value">Yield ${fmtPercent(trade.why_yield,0)} • Buffer ${fmtPercent(trade.why_buffer,0)} • Liquidity ${fmtPercent(trade.why_liquidity,0)} • IV ${fmtPercent(trade.why_iv_rich,0)}</span>
                                            </div>
                                            <div class="detail-row">
                                                <span class="detail-label">Income Metrics</span>
                                                <span class="detail-value">Ann Yield ${fmtPercent(trade.annualized_yield_on_collateral,1)} • Prem/Day ${fmtNumber(trade.premium_per_day,2,'$')} • Buffer ${fmtPercent(trade.downside_buffer,1)} • Assign ${fmtPercent(trade.assignment_risk_score,1)}</span>
                                            </div>
                                            ` : ''}
                                            <div class="detail-row">
                                                <span class="detail-label" data-metric="dte">Days to Expiration</span>
                                                <span class="detail-value">${detailValue(trade, 'dte', 'dte') ?? 'N/A'}</span>
                                            </div>
                                            <div class="detail-row">
                                                <span class="detail-label" data-metric="expected_move_1w">Expected Move</span>
                                                <span class="detail-value">${fmtNumber(detailValue(trade, 'expected_move', 'expected_move', 'expected_move_near'),2,'','')}</span>
                                            </div>
                                            <div class="detail-row">
                                                <span class="detail-label" data-metric="iv_rv_ratio">IV/RV Ratio</span>
                                                <span class="detail-value">${fmtNumber(detailValue(trade, 'iv_rv_ratio', 'iv_rv_ratio'),2,'','')}</span>
                                            </div>
                                            <div class="detail-row">
                                                <span class="detail-label" data-metric="trade_quality_score">Trade Quality Score</span>
                                                <span class="detail-value">${fmtPercent(detailValue(trade, 'trade_quality_score', 'trade_quality_score'),1)}</span>
                                            </div>
                                            <div class="detail-row">
                                                <span class="detail-label" data-metric="market_regime">Market Regime</span>
                                                <span class="detail-value">${detailValue(trade, 'market_regime', 'market_regime', 'regime') || 'N/A'}</span>
                                            </div>
                                        </div>
                                    </div>

                                    <div id="modelArea-${idx}" class="section section-model" style="display:none;"></div>
                                    <div class="section section-details">
                                        <div class="section-title">NOTES</div>
                                        <div id="tradeNotes-${idx}"></div>
                                    </div>
                                </div>
                            </div>

                            <div class="trade-actionbar">
                                <button id="runBtn-${idx}" id="runBtn-${idx}" class="btn btn-run" style="${window._collapsed && window._collapsed[idx] === false ? "" : "display:none;"}" onclick="analyzeTrade(${idx}); event.stopPropagation();">Run qwen2.5 model analysis</button>
                                <div class="trade-actions-row">
                                    <button class="btn btn-exec" onclick="executeTrade(${idx}); event.stopPropagation();">Execute trade</button>
                                    <button class="btn btn-reject" onclick="manualReject(${idx}); event.stopPropagation();">Reject</button>
                                    <button class="btn" onclick="sendToWorkbench(${idx}); event.stopPropagation();">Send to Workbench</button>
                                    <button class="btn" onclick="openDataWorkbench(${idx}); event.stopPropagation();">Data Workbench</button>
                                    <button class="btn" onclick="lifecycleAction(${idx}, 'WATCHLIST'); event.stopPropagation();">Add to Watchlist</button>
                                    <button class="btn" onclick="lifecycleAction(${idx}, 'OPEN'); event.stopPropagation();">Mark Open</button>
                                    <button class="btn" onclick="lifecycleAction(${idx}, 'CLOSE'); event.stopPropagation();">Close</button>
                                </div>
                            </div>
                        </div>
                    `).join('')}
                </div>
            `;

            content.innerHTML = html;

            if(window.attachMetricTooltips){
                window.attachMetricTooltips(content);
            }

            safeTrades.forEach((trade, idx) => {
                const host = doc.getElementById(`tradeNotes-${idx}`);
                if(!host || !window.BenTradeNotes?.attachNotes) return;

                const symbol = String(trade.underlying || trade.underlying_symbol || trade.symbol || '').trim().toUpperCase();
                const expiration = String(trade.expiration || '').trim() || 'NA';
                const strategy = String(trade.spread_type || trade.strategy || '').trim() || 'NA';
                const tradeKey = String(trade.trade_key || '').trim() || (
                    helper?.tradeKey
                        ? helper.tradeKey({
                            underlying: symbol,
                            expiration,
                            spread_type: strategy,
                            short_strike: trade.short_strike,
                            long_strike: trade.long_strike,
                            dte: trade.dte ?? 'NA',
                        })
                        : ''
                );

                if(!tradeKey) return;

                window.BenTradeNotes.attachNotes(host, `notes:trade:${tradeKey}`);
            });

            window.toggleTrade = function(idx){
                const body = doc.getElementById(`tradeBody-${idx}`);
                const chev = doc.getElementById(`chev-${idx}`);
                if(!body) return;
                const collapsed = body.classList.toggle('is-collapsed');
                const runBtn = doc.getElementById(`runBtn-${idx}`);
                if(runBtn) runBtn.style.display = collapsed ? 'none' : '';

                window._collapsed[idx] = collapsed;
                if(chev) chev.textContent = collapsed ? '▸' : '▾';
            };

            window.executeTrade = function(idxOrTrade){
                const trade = (typeof idxOrTrade === 'number')
                    ? (window.currentTrades && window.currentTrades[idxOrTrade] ? window.currentTrades[idxOrTrade] : null)
                    : ((idxOrTrade && typeof idxOrTrade === 'object') ? idxOrTrade : null);
                window.BenTradeExecutionModal?.open?.(trade || {}, { primaryLabel: 'Execute (off)' });
            };

            window.sendToWorkbench = function(idx){
                const trade = window.currentTrades && window.currentTrades[idx] ? window.currentTrades[idx] : null;
                if(!trade) return;

                const tradeKey = String(trade.trade_key || '').trim();
                if(!tradeKey){
                    showToast('Missing trade ID for workbench handoff');
                    return;
                }

                const helper = window.BenTradeUtils?.tradeKey;
                const symbol = String(trade.underlying || trade.underlying_symbol || trade.symbol || '').trim().toUpperCase();
                const expirationRaw = trade.expiration;
                const expiration = expirationRaw === null || expirationRaw === undefined || String(expirationRaw).trim() === '' ? 'NA' : String(expirationRaw).trim();
                const spread = String(trade.spread_type || trade.strategy || '').trim().toLowerCase();
                let strategy = 'credit_put_spread';
                if(spread === 'call_credit' || spread === 'credit_call_spread') strategy = 'credit_call_spread';
                else if(spread === 'debit_call_spread') strategy = 'debit_call_spread';
                else if(spread === 'debit_put_spread') strategy = 'debit_put_spread';
                else if(spread === 'call_debit') strategy = 'debit_call_spread';
                else if(spread === 'put_debit') strategy = 'debit_put_spread';
                else if(spread === 'iron_condor') strategy = 'iron_condor';
                else if(spread === 'butterfly_debit' || spread === 'debit_butterfly') strategy = 'debit_call_butterfly';
                else if(spread === 'debit_call_butterfly') strategy = 'debit_call_butterfly';
                else if(spread === 'debit_put_butterfly') strategy = 'debit_put_butterfly';
                else if(spread === 'iron_butterfly') strategy = 'iron_butterfly';
                else if(spread === 'calendar_call_spread') strategy = 'calendar_call_spread';
                else if(spread === 'calendar_put_spread') strategy = 'calendar_put_spread';
                else if(spread === 'cash_secured_put' || spread === 'csp') strategy = 'cash_secured_put';
                else if(spread === 'covered_call') strategy = 'covered_call';

                const shortStrike = Number(trade.short_strike);
                const longStrike = Number(trade.long_strike);
                const input = {
                    symbol,
                    expiration,
                    strategy,
                    short_strike: Number.isFinite(shortStrike) ? shortStrike : trade.short_strike,
                    long_strike: Number.isFinite(longStrike) ? longStrike : trade.long_strike,
                    put_short_strike: trade.put_short_strike,
                    put_long_strike: trade.put_long_strike,
                    call_short_strike: trade.call_short_strike,
                    call_long_strike: trade.call_long_strike,
                    center_strike: trade.center_strike,
                    lower_strike: trade.lower_strike,
                    upper_strike: trade.upper_strike,
                    wing_width: trade.wing_width,
                    butterfly_type: trade.butterfly_type,
                    strike: trade.strike,
                    expiration_near: trade.expiration_near,
                    expiration_far: trade.expiration_far,
                    contractsMultiplier: Number(trade.contractsMultiplier || 100) || 100,
                };

                const payload = {
                    from: 'credit_spread_analysis',
                    ts: new Date().toISOString(),
                    input,
                    trade_key: tradeKey,
                    note: trade.model_evaluation?.summary || '',
                };

                if(api?.postLifecycleEvent){
                    api.postLifecycleEvent({
                        event: 'WATCHLIST',
                        trade_key: tradeKey,
                        source: 'scanner',
                        trade,
                        reason: 'send_to_workbench',
                    }).catch(() => {});
                }

                localStorage.setItem('bentrade_workbench_handoff_v1', JSON.stringify(payload));
                location.hash = '#/trade-testing';
            };

            window.openDataWorkbench = function(idx){
                const trade = window.currentTrades && window.currentTrades[idx] ? window.currentTrades[idx] : null;
                if(!trade) return;

                const navigate = tradeCardUi?.openDataWorkbenchByTrade;
                const ok = typeof navigate === 'function'
                    ? navigate(trade, {
                        onMissingTradeKey: () => {
                            showToast('Trade ID unavailable');
                            console.warn('[validation][TRADE_ID_UNAVAILABLE] Data Workbench navigation requires trade.trade_key', {
                                context: 'scanner_trade_card',
                                trade,
                            });
                        },
                    })
                    : false;

                if(ok) return;

                const key = String(trade.trade_key || '').trim();
                if(!key){
                    showToast('Trade ID unavailable');
                    console.warn('[validation][TRADE_ID_UNAVAILABLE] Data Workbench navigation requires trade.trade_key', {
                        context: 'scanner_trade_card',
                        trade,
                    });
                    return;
                }

                location.hash = `#/admin/data-workbench?trade_key=${encodeURIComponent(key)}`;
            };

            window.lifecycleAction = async function(idx, eventName){
                const trade = window.currentTrades && window.currentTrades[idx] ? window.currentTrades[idx] : null;
                if(!trade || !api?.postLifecycleEvent) return;

                const tradeKey = String(trade.trade_key || '').trim();
                if(!tradeKey) return;

                let reason = '';
                const payload = { ...trade };
                if(String(eventName || '').toUpperCase() === 'CLOSE'){
                    const input = window.prompt('Optional realized P&L (number):', '');
                    if(input !== null && String(input).trim() !== ''){
                        const value = Number(input);
                        if(Number.isFinite(value)) payload.realized_pnl = value;
                    }
                    reason = 'manual_close';
                }

                try{
                    await api.postLifecycleEvent({
                        event: String(eventName || '').toUpperCase(),
                        trade_key: tradeKey,
                        source: 'scanner',
                        trade: payload,
                        reason,
                    });
                }catch(_err){
                }
            };

            window.manualReject = async function(idx){
                const trade = window.currentTrades && window.currentTrades[idx] ? window.currentTrades[idx] : null;
                if(trade && window.currentReportFile && api?.persistRejectDecision){
                    try{
                        const tradeKey = String(trade.trade_key || '').trim();
                        if(tradeKey){
                            await api.persistRejectDecision({
                                report_file: window.currentReportFile,
                                trade_key: tradeKey,
                                reason: 'manual_reject',
                            });
                        }
                    }catch(err){
                        console.warn('[BenTrade] Failed to persist manual reject', err);
                    }
                }

                if(trade && api?.postLifecycleEvent){
                    try{
                        const tradeKey = String(trade.trade_key || '').trim();
                        if(!tradeKey) return;

                        await api.postLifecycleEvent({
                            event: 'REJECT',
                            trade_key: tradeKey,
                            source: 'scanner',
                            trade,
                            reason: 'manual_reject',
                        });
                    }catch(_err){
                    }
                }

                const card = document.querySelector(`.trade-card[data-idx="${idx}"]`);
                if(card) card.remove();
                if(window.currentTrades && window.currentTrades[idx]){
                    window.currentTrades[idx].manual_reject = true;
                }

                const moduleId = moduleIdForManualReject(trade || {});
                window.BenTradeSessionStatsStore?.recordReject?.(moduleId, 1);
            };

            window.copyTradeId = async function(idx){
                const trade = window.currentTrades && window.currentTrades[idx] ? window.currentTrades[idx] : null;
                const key = String(trade?.trade_key || trade?._trade_key || '').trim();
                if(!key) return;
                try{
                    await navigator.clipboard.writeText(key);
                    showToast('Trade ID copied');
                }catch(_err){
                    showToast('Copy failed');
                }
            };

            window.analyzeTrade = async function(idx){
                const btn = doc.getElementById(`runBtn-${idx}`);
                if(!window.currentTrades || !window.currentReportFile) return;
                if(!btn) return;

                try{
                    btn.disabled = true;
                    btn.classList.add('is-loading');
                    btn.textContent = 'Analyzing…';

                    const trade = window.currentTrades[idx];
                    const resp = await fetch('/api/model/analyze',{
                        method:'POST',
                        headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({trade: trade, source: window.currentReportFile})
                    });
                    const data = await resp.json();

                    if(resp.ok && data && data.ok){
                        const evaluated = data.evaluated_trade;
                        window.currentTrades[idx] = evaluated;

                        const modelArea = doc.getElementById(`modelArea-${idx}`);
                        if(modelArea){
                            const me = evaluated.model_evaluation || {};
                            const rec = me.recommendation || 'N/A';
                            const conf = me.confidence;
                            const risk = me.risk_level || 'N/A';
                            const kfs = me.key_factors || [];
                            const summary = me.summary || '';

                            const recClass = rec === 'ACCEPT' ? 'rec-accept' : (rec === 'REJECT' ? 'rec-reject' : 'rec-neutral');
                            const confClass = (conf != null) ? (conf > 0.75 ? 'confidence-high' : (conf > 0.4 ? 'confidence-mid' : 'confidence-low')) : '';
                            const riskClass = (risk === 'Low') ? 'risk-low' : (risk === 'High' ? 'risk-high' : 'risk-moderate');

                            modelArea.innerHTML = `
                                <div class="section-title">QWEN MODEL ANALYSIS</div>
                                <div class="model-grid">
                                    <div class="model-row"><span class="detail-label">Recommendation</span><span class="detail-value"><span class="model-value-pill ${recClass}">${rec}</span></span></div>
                                    <div class="model-row"><span class="detail-label">Confidence</span><span class="detail-value"><span class="model-value-pill ${confClass}">${(conf != null) ? (Number(conf)*100).toFixed(1) + '%' : 'N/A'}</span></span></div>
                                    <div class="model-row"><span class="detail-label">Risk Level</span><span class="detail-value"><span class="model-value-pill ${riskClass}">${risk}</span></span></div>
                                </div>
                                <div class="model-kf">
                                    <div class="detail-label" style="margin-bottom:6px;">Key Factors</div>
                                    <ul class="key-factors">${kfs.map(k=>`<li>${k}</li>`).join('')}</ul>
                                </div>
                                <div class="model-summary">
                                    <div class="detail-label" style="margin-bottom:6px;">Summary</div>
                                    <div>${summary}</div>
                                </div>
                            `;
                            modelArea.style.display = 'block';
                        }

                        btn.classList.remove('is-loading');
                        btn.textContent = 'Analyzed';
                        btn.disabled = true;
                    }else{
                        btn.classList.remove('is-loading');
                        btn.textContent = 'Error';
                        setTimeout(()=>{ btn.disabled=false; btn.textContent='Run qwen2.5 model analysis'; }, 1800);
                        console.error('Model analyze failed', data);
                    }
                }catch(err){
                    console.error('analyzeTrade error', err);
                    if(btn){
                        btn.classList.remove('is-loading');
                        btn.textContent='Error';
                        setTimeout(()=>{ btn.disabled=false; btn.textContent='Run qwen2.5 model analysis'; },1800);
                    }
                }
            };

            Object.keys(window._collapsed || {}).forEach(k=>{
                const idx = Number(k);
                const body = doc.getElementById(`tradeBody-${idx}`);
                const chev = doc.getElementById(`chev-${idx}`);
                if(!body || !chev) return;
                const isCollapsed = body.classList.contains('is-collapsed');
                chev.textContent = isCollapsed ? '▸' : '▾';
            });
}

        fileSelect.addEventListener('change', applyUnderlyingFilter);

        const manualFilteringAvailable = false;
        if(manualFiltersPanel) manualFiltersPanel.style.display = 'none';
        if(manualFilterPill) manualFilterPill.style.display = 'none';

        if(manualFiltersEnabled && manualFilteringAvailable){
            manualFiltersEnabled.checked = false;
            setManualFilterPanelVisible(false);
            manualFiltersEnabled.addEventListener('change', () => {
                const enabled = isManualFilterEnabled();
                setManualFilterPanelVisible(enabled);
                if(!enabled){
                    manualFiltersApplied = false;
                    appliedManualFilters = null;
                    clearManualFilterInputs();
                }
                applyUnderlyingFilter();
            });
        }

        if(mfUseDefaultsBtn && manualFilteringAvailable){
            mfUseDefaultsBtn.addEventListener('click', () => {
                setManualFilterInputs(manualFilterDefaults);
            });
        }

        if(mfApplyBtn && manualFilteringAvailable){
            mfApplyBtn.addEventListener('click', () => {
                if(!isManualFilterEnabled()) return;
                appliedManualFilters = readManualFilterInputs();
                manualFiltersApplied = true;
                applyUnderlyingFilter();
            });
        }

        if(mfResetBtn && manualFilteringAvailable){
            mfResetBtn.addEventListener('click', () => {
                clearManualFilterInputs();
                manualFiltersApplied = false;
                appliedManualFilters = null;
                applyUnderlyingFilter();
            });
        }

        reportSelect.addEventListener('change', (e) => {
            const filename = e.target.value;
            if(!filename){
                content.innerHTML = '<div class="loading">Select an analysis report to view trade details</div>';
                return;
            }
            setReportsLoading();
            loadFiles(filename);
        });

        doc.getElementById('genBtn').addEventListener('click', ()=>{
            const genBtn = doc.getElementById('genBtn');

            // Keep a real spinner element inside the button whenever `is-loading` is present.
            const ensureSpinner = (btn) => {
                if (!btn) return;
                if (btn.classList.contains('is-loading')) {
                    if (!btn.querySelector('.gen-spinner')) {
                        const sp = document.createElement('span');
                        sp.className = 'gen-spinner';
                        btn.appendChild(sp);
                    }
                } else {
                    const sp = btn.querySelector('.gen-spinner');
                    if (sp) sp.remove();
                }
            };

            // Observe class changes to add/remove spinner automatically.
            try {
                const mo = new MutationObserver(() => ensureSpinner(genBtn));
                mo.observe(genBtn, { attributes: true, attributeFilter: ['class'] });
            } catch (e) {
                // MutationObserver may not be available in some contexts; fall back to manual calls.
            }
            ensureSpinner(genBtn);
            const overlay = doc.getElementById('genOverlay');
            const status = doc.getElementById('genStatus');
            const statusLog = doc.getElementById('genStatusLog');
            overlay.style.display = 'flex';
            status.textContent = 'Starting...';
            if(statusLog) statusLog.innerHTML = '';
            genBtn.classList.add('is-loading');
            let latestStage = 'starting';
            let latestErrorPayload = null;

            const appendStatusLog = (text) => {
                if(!statusLog) return;
                const timestamp = new Date().toLocaleTimeString();
                const line = document.createElement('div');
                line.textContent = `[${timestamp}] ${text}`;
                statusLog.appendChild(line);
                while(statusLog.childNodes.length > 120){
                    statusLog.removeChild(statusLog.firstChild);
                }
                statusLog.scrollTop = statusLog.scrollHeight;
            };

            const ensureCopyDetailsButton = (payload) => {
                if(!statusLog || !payload) return;
                let btn = statusLog.querySelector('#genCopyErrorBtn');
                if(!btn){
                    btn = document.createElement('button');
                    btn.id = 'genCopyErrorBtn';
                    btn.className = 'btn';
                    btn.type = 'button';
                    btn.textContent = 'Copy details';
                    btn.style.marginTop = '8px';
                    statusLog.appendChild(btn);
                }
                btn.onclick = async () => {
                    try{
                        await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
                        appendStatusLog('Copied error details to clipboard');
                    }catch(_err){
                        appendStatusLog('Copy failed');
                    }
                };
            };

            const readErrorPayload = (evt) => {
                try{
                    if(evt && evt.data){
                        const parsed = JSON.parse(evt.data);
                        if(parsed && typeof parsed === 'object') return parsed;
                    }
                }catch(_err){}
                return null;
            };
            appendStatusLog('Starting report generation...');

            const evt = new EventSource('/api/generate');
            let finalized = false;
            const finishGeneration = (finalText, filename) => {
                if(finalized) return;
                finalized = true;
                if(finalText){
                    status.textContent = finalText;
                    appendStatusLog(finalText);
                }
                setTimeout(()=>{
                    overlay.style.display = 'none';
                    try{ evt.close(); }catch(_err){}
                    genBtn.classList.remove('is-loading');
                    if(filename){
                        pendingGeneratedReport = String(filename);
                        loadFiles(filename);
                        window.BenTradeSourceHealthStore?.fetchSourceHealth?.({ force: true }).catch(() => {});
                    }
                }, 900);
            };

            evt.addEventListener('progress', (e)=>{
                try{
                    const data = JSON.parse(e.data);
                    if(data?.stage) latestStage = String(data.stage);
                    const msg = data.message || data.step || 'Working...';
                    status.textContent = msg;
                    appendStatusLog(msg);
                }catch(err){ status.textContent = e.data }
            });
            evt.addEventListener('status', (e)=>{
                try{
                    const data = JSON.parse(e.data || '{}');
                    if(data?.stage) latestStage = String(data.stage);
                    const stageText = data?.stage ? ` [${data.stage}]` : '';
                    const msg = `${data?.message || 'Working'}${stageText}`;
                    status.textContent = msg;
                    appendStatusLog(msg);
                }catch(_err){
                    status.textContent = 'Working...';
                }
            });
            evt.addEventListener('completed', (e)=>{
                let fn = null;
                try{
                    const data = JSON.parse(e.data || '{}');
                    fn = data.filename || null;
                }catch(_err){}
                const msg = fn ? ('Completed — ' + fn) : 'Completed';
                status.textContent = msg;
                appendStatusLog(msg);
            });
            evt.addEventListener('done', (e)=>{
                let fn = null;
                try{
                    const data = JSON.parse(e.data || '{}');
                    fn = data.filename || null;
                }catch(_err){}
                finishGeneration(fn ? ('Completed — ' + fn) : 'Completed', fn);
            });
            evt.addEventListener('error', (e)=>{
                if(finalized) return;
                const parsed = readErrorPayload(e);
                if(parsed){
                    latestErrorPayload = parsed;
                }

                const stage = String(parsed?.stage || latestStage || 'unknown');
                const errorMessage = String(parsed?.error_message || parsed?.message || 'Generation failed');
                const traceId = String(parsed?.trace_id || 'n/a');
                const hint = String(parsed?.hint || 'Check backend logs for trace details');
                const detailsPayload = parsed || {
                    stage,
                    error_type: 'EventSourceError',
                    error_message: errorMessage,
                    trace_id: traceId,
                    hint,
                };

                appendStatusLog(`Error stage: ${stage}`);
                appendStatusLog(`Error message: ${errorMessage}`);
                appendStatusLog(`Trace ID: ${traceId}`);
                appendStatusLog(`Hint: ${hint}`);
                ensureCopyDetailsButton(detailsPayload);
                finishGeneration(`Error [${stage}]: ${errorMessage} (trace: ${traceId})`);
            });
        });

        loadFiles();

};

window.BenTrade = window.BenTrade || {};
window.BenTrade._underConstructionTronMarkup = null;

window.BenTrade.ensureUnderConstructionTron = async function ensureUnderConstructionTron(hostEl){
    if(!hostEl) return;
    if(!window.BenTrade._underConstructionTronMarkup){
        const res = await fetch('dashboards/partials/under-construction-tron.view.html', { cache: 'no-store' });
        window.BenTrade._underConstructionTronMarkup = await res.text();
    }
    hostEl.innerHTML = window.BenTrade._underConstructionTronMarkup;
};

window.BenTrade.renderSourceHealthPlaceholder = function renderSourceHealthPlaceholder(){
    const container = document.getElementById('sourceHealthRows');
    if(!container) return;
    container.innerHTML = [
        ['Market Data Feed', 'status-yellow', 'Placeholder source status while dashboard is under construction.'],
        ['Options Chain Feed', 'status-yellow', 'Placeholder source status while dashboard is under construction.'],
        ['Risk Engine', 'status-green', 'Core services available. Dashboard metrics not wired yet.'],
        ['Automation Jobs', 'status-red', 'No jobs configured yet for this dashboard.']
    ].map(([label, statusClass, tip]) => `
        <div class="diagnosticRow">
            <span class="diagnosticLabel">${label}</span>
            <span class="status-wrap" tabindex="0">
                <span class="status-dot ${statusClass}"></span>
                <span class="status-tooltip">${tip}</span>
            </span>
        </div>
    `).join('');
};

window.BenTrade.renderStatsPlaceholder = function renderStatsPlaceholder(title){
    if(window.BenTradeSessionStatsStore?.renderPanel){
        window.BenTradeSessionStatsStore.renderPanel();
        return;
    }
    const stats = document.getElementById('reportStatsGrid');
    if(!stats) return;
    stats.innerHTML = [
        ['Dashboard', title],
        ['Status', 'Placeholder'],
        ['Cards', 'Coming Soon'],
        ['Automation', 'Planned']
    ].map(([label, value]) => `
        <div class="statTile">
            <div class="statLabel">${label}</div>
            <div class="statValue">${value}</div>
        </div>
    `).join('');
};

window.BenTrade.initPlaceholderDashboard = async function initPlaceholderDashboard(config){
    const title = config?.title || 'Dashboard';
    const host = document.querySelector('[data-under-construction-host]');
    await window.BenTrade.ensureUnderConstructionTron(host);
    window.BenTradeSourceHealthStore?.fetchSourceHealth?.({ force: true }).catch(() => {});
    window.BenTrade.renderStatsPlaceholder(title);
};
