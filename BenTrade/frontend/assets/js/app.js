// BenTrade Dashboard Logic: Credit Spread Analysis
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
            if(!Array.isArray(window.currentTrades)){
                displayTrades([]);
                return;
            }
            const selected = fileSelect.value || 'ALL';
            setSelectedUnderlying(selected);
            if(selected === 'ALL'){
                displayTrades(window.currentTrades);
                return;
            }
            const filtered = window.currentTrades.filter(trade => getTradeUnderlying(trade) === selected);
            displayTrades(filtered);
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
                return { trades: payload, reportStats: buildStatsFromTrades(payload), diagnostics: {}, sourceHealth: {} };
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
                    return { trades: payload.trades, reportStats, diagnostics, sourceHealth };
                }
                return { trades: [], reportStats: {}, diagnostics: payload, sourceHealth: {} };
            }
            return { trades: [], reportStats: {}, diagnostics: {}, sourceHealth: {} };
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
                const { trades, reportStats, diagnostics, sourceHealth } = normalizeReportPayload(payload);
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
                        const tradeKey = tradeCardUi?.buildTradeKey
                            ? tradeCardUi.buildTradeKey(trade)
                            : `${String(trade?.underlying || trade?.underlying_symbol || '').toUpperCase()}|${trade?.expiration || ''}|${trade?.spread_type || ''}|${trade?.short_strike || ''}|${trade?.long_strike || ''}|${trade?.dte || ''}`;
                        return { ...trade, _trade_key: tradeKey };
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
                    ? keyedFiltered.filter(trade => !persistedRejected.has(String(trade._trade_key || '')))
                    : keyedFiltered;

                window.currentTrades = persistedFiltered;
                window.currentReportFile = filename;
                if(session?.setCurrentTrades) session.setCurrentTrades(persistedFiltered);
                if(session?.setCurrentReportFile) session.setCurrentReportFile(filename);
                setSelectedReport(filename);

                if(Array.isArray(trades) && filtered.length !== trades.length){
                    console.log(`[ui] filtered out ${trades.length - filtered.length} rejected trade(s)`);
                }

                populateUnderlyingOptions(persistedFiltered);
                applyUnderlyingFilter();
                renderDiagnosticPanel({ reportStats, diagnostics, sourceHealth, trades });
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
            if (v === null || v === undefined || Number.isNaN(Number(v))) return 'N/A';
            return (prefix || '') + Number(v).toFixed(decimals) + (suffix || '');
        }

        function fmtPercent(v, decimals = 1){
            if (v === null || v === undefined || Number.isNaN(Number(v))) return 'N/A';
            return (Number(v) * 100).toFixed(decimals) + '%';
        }

        function toNumeric(value){
            if(value === null || value === undefined || value === '') return null;
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
            const acceptedTrades = payloadTrades;
            const reportStats = (payload.reportStats && typeof payload.reportStats === 'object')
                ? payload.reportStats
                : ((payload.report_stats && typeof payload.report_stats === 'object') ? payload.report_stats : {});
            const diagnostics = (payload.diagnostics && typeof payload.diagnostics === 'object') ? payload.diagnostics : {};

            const totalCandidates = toNumeric(reportStats.total_candidates ?? diagnostics.total_candidates) ?? (payloadTrades.length || null);
            const acceptedTradesCount = toNumeric(reportStats.accepted_trades ?? diagnostics.accepted_trades) ?? (acceptedTrades.length || null);
            const rejectedTradesCount = toNumeric(reportStats.rejected_trades ?? diagnostics.rejected_trades) ?? ((totalCandidates !== null && acceptedTradesCount !== null) ? Math.max(totalCandidates - acceptedTradesCount, 0) : null);
            const acceptanceRate = toNumeric(reportStats.acceptance_rate ?? diagnostics.acceptance_rate) ?? ((totalCandidates && acceptedTradesCount !== null) ? (acceptedTradesCount / totalCandidates) : null);

            const avgQuality = toNumeric(reportStats.avg_trade_score ?? diagnostics.avg_trade_score ?? diagnostics.avg_quality_score) ?? avgMetric(acceptedTrades, t => t.composite_score ?? t.trade_quality_score);
            const bestQuality = toNumeric(reportStats.best_trade_score ?? diagnostics.best_trade_score ?? diagnostics.best_quality_score) ?? (() => {
                const scores = acceptedTrades.map(t => toNumeric(t.composite_score ?? t.trade_quality_score)).filter(v => v !== null);
                return scores.length ? Math.max(...scores) : null;
            })();
            const worstQuality = toNumeric(reportStats.worst_accepted_score ?? diagnostics.worst_accepted_score ?? diagnostics.worst_quality_score) ?? (() => {
                const scores = acceptedTrades.map(t => toNumeric(t.composite_score ?? t.trade_quality_score)).filter(v => v !== null);
                return scores.length ? Math.min(...scores) : null;
            })();
            const avgPop = toNumeric(reportStats.avg_probability ?? diagnostics.avg_probability ?? diagnostics.avg_pop) ?? avgMetric(acceptedTrades, t => t.p_win_used ?? t.pop_delta_approx);
            const avgRor = toNumeric(reportStats.avg_return_on_risk ?? diagnostics.avg_return_on_risk ?? diagnostics.avg_ror) ?? avgMetric(acceptedTrades, t => t.return_on_risk);
            const bestUnderlying = reportStats.best_underlying ?? diagnostics.best_underlying ?? null;

            const dteBuckets = (reportStats.dte_bucket_counts && typeof reportStats.dte_bucket_counts === 'object')
                ? reportStats.dte_bucket_counts
                : ((diagnostics.dte_bucket_counts && typeof diagnostics.dte_bucket_counts === 'object') ? diagnostics.dte_bucket_counts : {});

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

            const stats = [
                { label: 'Total candidates', value: statValue(totalCandidates, v => String(Math.round(v))) },
                { label: 'Accepted trades', value: statValue(acceptedTradesCount, v => String(Math.round(v))) },
                { label: 'Rejected trades', value: statValue(rejectedTradesCount, v => String(Math.round(v))) },
                { label: 'Acceptance rate', value: statValue(acceptanceRate, v => fmtPercent(v, 1)) },
                { label: 'Avg quality score', value: statValue(avgQuality, v => fmtPercent(v, 1)) },
                { label: 'Best trade score', value: statValue(bestQuality, v => fmtPercent(v, 1)) },
                { label: 'Worst accepted score', value: statValue(worstQuality, v => fmtPercent(v, 1)) },
                { label: 'Avg probability', value: statValue(avgPop, v => fmtPercent(v, 1)) },
                { label: 'Avg return on risk', value: statValue(avgRor, v => fmtPercent(v, 1)) },
                { label: 'Best underlying', value: statTextValue(bestUnderlying) },
                { label: 'DTE 3-5 candidates', value: statValue(dteBuckets['3-5'], v => String(Math.round(v))) },
                { label: 'DTE 6-10 candidates', value: statValue(dteBuckets['6-10'], v => String(Math.round(v))) },
                { label: 'DTE 11-14 candidates', value: statValue(dteBuckets['11-14'], v => String(Math.round(v))) },
            ];

            reportStatsGrid.innerHTML = stats.map(stat => `
                <div class="statTile">
                    <div class="statLabel">${stat.label}</div>
                    <div class="statValue">${stat.value}</div>
                </div>
            `).join('');
        }

        function contractDollars(trade, contractField, shareField, fallbackField){
            const contractValue = Number(trade?.[contractField]);
            if(Number.isFinite(contractValue)) return contractValue;

            const shareValue = Number(trade?.[shareField]);
            if(Number.isFinite(shareValue)) return shareValue * 100;

            const fallbackValue = Number(trade?.[fallbackField]);
            if(Number.isFinite(fallbackValue)) return fallbackValue;

            return null;
        }

        function getProb(trade){
            // priority: p_win_used -> pop_delta_approx -> null
            const v = (trade.p_win_used != null) ? trade.p_win_used :
                      (trade.pop_delta_approx != null) ? trade.pop_delta_approx :
                      null;
            return v;
        }

        function formatTradeType(type) {
            return type === 'put_credit' ? '📉 Put Credit Spread' : '📈 Call Credit Spread';
        }

        
                // Display trades in a nice grid
        function displayTrades(trades) {
            console.log('[ui] displayTrades, trades[0] keys:', trades && trades[0] ? Object.keys(trades[0]) : 'none');

            
            window._collapsed = window._collapsed || {};
            // Default ALL trades to collapsed on first render
            trades.forEach((_, i) => { if (window._collapsed[i] === undefined) window._collapsed[i] = true; });
const html = `
                <div class="trades-grid">
                    ${trades.map((trade, idx) => `
                        <div class="trade-card" data-idx="${idx}">
                            <div class="trade-header trade-header-click" onclick="toggleTrade(${idx})" role="button" aria-label="Toggle trade">
                                <div class="trade-header-left"><span id="chev-${idx}" class="chev">${window._collapsed && window._collapsed[idx] === false ? "▾" : "▸"}</span></div>
                                <div class="trade-header-center">
                                    <div class="trade-type">${formatTradeType(trade.spread_type)}</div>
                                    <div class="trade-subtitle">
                                        <span class="underlying-symbol">${trade.underlying || trade.underlying_symbol || ''}</span>
                                        <span class="trade-strikes-inline">${trade.short_strike}/${trade.long_strike}</span>
                                        <span class="underlying-price">(${fmtNumber(trade.underlying_price,2,'','')})</span>
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
                                                <div class="metric-value neutral">${fmtPercent(trade.p_win_used,1)}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="return_on_risk">Return on Risk</div>
                                                <div class="metric-value ${trade.return_on_risk != null && trade.return_on_risk > 0.2 ? 'positive' : 'neutral'}">${fmtPercent(trade.return_on_risk,1)}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="ev">Expected Value</div>
                                                <div class="metric-value ${contractDollars(trade, 'ev_per_contract', 'ev_per_share', 'expected_value') != null ? (contractDollars(trade, 'ev_per_contract', 'ev_per_share', 'expected_value') > 0 ? 'positive' : 'negative') : 'neutral'}">${fmtNumber(contractDollars(trade, 'ev_per_contract', 'ev_per_share', 'expected_value'),2,'$')}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="kelly_fraction">Kelly Fraction</div>
                                                <div class="metric-value ${trade.kelly_fraction != null ? (trade.kelly_fraction > 0 ? 'positive' : 'negative') : 'neutral'}">${fmtPercent(trade.kelly_fraction,1)}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="iv_rank">IV Rank</div>
                                                <div class="metric-value ${trade.iv_rank != null && trade.iv_rank > 0.5 ? 'positive' : 'neutral'}">${fmtPercent(trade.iv_rank,1)}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="short_strike_z">Short Strike Z</div>
                                                <div class="metric-value ${trade.short_strike_z != null && trade.short_strike_z > 1 ? 'positive' : 'neutral'}">${fmtNumber(trade.short_strike_z,2,'','')}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="bid_ask_spread_pct">Bid-Ask %</div>
                                                <div class="metric-value ${trade.bid_ask_spread_pct != null && trade.bid_ask_spread_pct < 0.1 ? 'positive' : 'neutral'}">${fmtPercent(trade.bid_ask_spread_pct,2)}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="strike_distance_pct">Strike Dist %</div>
                                                <div class="metric-value">${fmtPercent(trade.strike_distance_pct,2)}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="rsi_14">RSI14</div>
                                                <div class="metric-value ${trade.rsi14 != null && trade.rsi14 > 60 ? 'negative' : 'neutral'}">${fmtNumber(trade.rsi14,1,'','')}</div>
                                            </div>
                                            <div class="metric">
                                                <div class="metric-label" data-metric="realized_vol_20d">RV (20d)</div>
                                                <div class="metric-value">${fmtPercent(trade.realized_vol_20d,2)}</div>
                                            </div>
                                        </div>
                                    </div>

                                    <div class="section section-details">
                                        <div class="section-title">TRADE DETAILS</div>
                                        <div class="trade-details">
                                            <div class="detail-row">
                                                <span class="detail-label" data-metric="break_even">Break Even</span>
                                                <span class="detail-value">${fmtNumber(trade.break_even,2,'$')}</span>
                                            </div>
                                            <div class="detail-row">
                                                <span class="detail-label" data-metric="dte">Days to Expiration</span>
                                                <span class="detail-value">${trade.dte ?? 'N/A'}</span>
                                            </div>
                                            <div class="detail-row">
                                                <span class="detail-label" data-metric="expected_move_1w">Expected Move</span>
                                                <span class="detail-value">${fmtNumber(trade.expected_move,2,'','')}</span>
                                            </div>
                                            <div class="detail-row">
                                                <span class="detail-label" data-metric="iv_rv_ratio">IV/RV Ratio</span>
                                                <span class="detail-value">${fmtNumber(trade.iv_rv_ratio,2,'','')}</span>
                                            </div>
                                            <div class="detail-row">
                                                <span class="detail-label" data-metric="trade_quality_score">Trade Quality Score</span>
                                                <span class="detail-value">${fmtPercent(trade.trade_quality_score,1)}</span>
                                            </div>
                                            <div class="detail-row">
                                                <span class="detail-label" data-metric="market_regime">Market Regime</span>
                                                <span class="detail-value">${trade.market_regime || 'N/A'}</span>
                                            </div>
                                        </div>
                                    </div>

                                    <div id="modelArea-${idx}" class="section section-model" style="display:none;"></div>
                                </div>
                            </div>

                            <div class="trade-actionbar">
                                <button id="runBtn-${idx}" id="runBtn-${idx}" class="btn btn-run" style="${window._collapsed && window._collapsed[idx] === false ? "" : "display:none;"}" onclick="analyzeTrade(${idx}); event.stopPropagation();">Run qwen2.5 model analysis</button>
                                <div class="trade-actions-row">
                                    <button class="btn btn-exec" onclick="executeTrade(${idx}); event.stopPropagation();">Execute trade</button>
                                    <button class="btn btn-reject" onclick="manualReject(${idx}); event.stopPropagation();">Reject</button>
                                    <button class="btn" onclick="sendToWorkbench(${idx}); event.stopPropagation();">Send to Workbench</button>
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

            window.executeTrade = function(idx){
                const modal = doc.getElementById('modal');
                const modalMsg = doc.getElementById('modalMsg');
                if(modal && modalMsg){
                    modalMsg.textContent = 'Trade capability off';
                    modal.style.display = 'flex';
                } else {
                    alert('Trade capability off');
                }
            };

            window.sendToWorkbench = function(idx){
                const trade = window.currentTrades && window.currentTrades[idx] ? window.currentTrades[idx] : null;
                if(!trade) return;

                const helper = window.BenTradeUtils?.tradeKey;
                const symbol = String(trade.underlying || trade.underlying_symbol || trade.symbol || '').trim().toUpperCase();
                const expirationRaw = trade.expiration;
                const expiration = expirationRaw === null || expirationRaw === undefined || String(expirationRaw).trim() === '' ? 'NA' : String(expirationRaw).trim();
                const spread = String(trade.spread_type || trade.strategy || '').trim().toLowerCase();
                const strategy = spread === 'call_credit' || spread === 'credit_call_spread' ? 'credit_call_spread' : 'credit_put_spread';

                const shortStrike = Number(trade.short_strike);
                const longStrike = Number(trade.long_strike);
                const input = {
                    symbol,
                    expiration,
                    strategy,
                    short_strike: Number.isFinite(shortStrike) ? shortStrike : trade.short_strike,
                    long_strike: Number.isFinite(longStrike) ? longStrike : trade.long_strike,
                    contractsMultiplier: Number(trade.contractsMultiplier || 100) || 100,
                };

                const computedKey = helper?.tradeKey
                    ? helper.tradeKey({
                        underlying: input.symbol,
                        expiration: input.expiration,
                        spread_type: input.strategy,
                        short_strike: input.short_strike,
                        long_strike: input.long_strike,
                        dte: trade.dte ?? 'NA',
                    })
                    : String(trade._trade_key || trade.trade_key || `${input.symbol}|${input.expiration}|${input.strategy}|${input.short_strike}|${input.long_strike}|${trade.dte ?? 'NA'}`);

                const payload = {
                    from: 'credit_spread_analysis',
                    ts: new Date().toISOString(),
                    input,
                    trade_key: computedKey,
                    note: trade.model_evaluation?.summary || '',
                };

                if(api?.postLifecycleEvent){
                    api.postLifecycleEvent({
                        event: 'WATCHLIST',
                        trade_key: computedKey,
                        source: 'scanner',
                        trade,
                        reason: 'send_to_workbench',
                    }).catch(() => {});
                }

                localStorage.setItem('bentrade_workbench_handoff_v1', JSON.stringify(payload));
                location.hash = '#/trade-testing';
            };

            window.lifecycleAction = async function(idx, eventName){
                const trade = window.currentTrades && window.currentTrades[idx] ? window.currentTrades[idx] : null;
                if(!trade || !api?.postLifecycleEvent) return;

                const helper = window.BenTradeUtils?.tradeKey;
                const symbol = String(trade.underlying || trade.underlying_symbol || trade.symbol || '').trim().toUpperCase();
                const expiration = String(trade.expiration || '').trim() || 'NA';
                const strategy = String(trade.spread_type || trade.strategy || '').trim() || 'NA';

                const computedKey = helper?.tradeKey
                    ? helper.tradeKey({
                        underlying: symbol,
                        expiration,
                        spread_type: strategy,
                        short_strike: trade.short_strike,
                        long_strike: trade.long_strike,
                        dte: trade.dte ?? 'NA',
                    })
                    : String(trade._trade_key || trade.trade_key || `${symbol}|${expiration}|${strategy}|${trade.short_strike}|${trade.long_strike}|${trade.dte ?? 'NA'}`);

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
                        trade_key: computedKey,
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
                        const tradeKey = trade._trade_key || (tradeCardUi?.buildTradeKey ? tradeCardUi.buildTradeKey(trade) : null);
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
                        const helper = window.BenTradeUtils?.tradeKey;
                        const symbol = String(trade.underlying || trade.underlying_symbol || trade.symbol || '').trim().toUpperCase();
                        const expiration = String(trade.expiration || '').trim() || 'NA';
                        const strategy = String(trade.spread_type || trade.strategy || '').trim() || 'NA';
                        const computedKey = helper?.tradeKey
                            ? helper.tradeKey({
                                underlying: symbol,
                                expiration,
                                spread_type: strategy,
                                short_strike: trade.short_strike,
                                long_strike: trade.long_strike,
                                dte: trade.dte ?? 'NA',
                            })
                            : String(trade._trade_key || trade.trade_key || `${symbol}|${expiration}|${strategy}|${trade.short_strike}|${trade.long_strike}|${trade.dte ?? 'NA'}`);

                        await api.postLifecycleEvent({
                            event: 'REJECT',
                            trade_key: computedKey,
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
            appendStatusLog('Starting report generation...');

            const evt = new EventSource('/api/generate');
            evt.addEventListener('progress', (e)=>{
                try{
                    const data = JSON.parse(e.data);
                    const msg = data.message || data.step || 'Working...';
                    status.textContent = msg;
                    appendStatusLog(msg);
                }catch(err){ status.textContent = e.data }
            });
            evt.addEventListener('done', (e)=>{
                const data = JSON.parse(e.data);
                const fn = data.filename;
                status.textContent = 'Completed — ' + fn;
                appendStatusLog('Completed — ' + fn);
                setTimeout(()=>{
                    overlay.style.display = 'none';
                    evt.close();
                    genBtn.classList.remove('is-loading');
                    loadFiles(fn);
                }, 900);
            });
            evt.addEventListener('error', (e)=>{
                try{
                    if(e && e.data){
                        const d = JSON.parse(e.data);
                        if(d && d.message){
                            status.textContent = 'Error: ' + d.message;
                            appendStatusLog('Error: ' + d.message);
                        }
                    }
                }catch(err){}
                if(evt.readyState === EventSource.CLOSED){
                    setTimeout(()=>{ overlay.style.display='none'; evt.close(); genBtn.classList.remove('is-loading'); }, 900);
                } else {
                    // if not closed, still remove loading (user can retry)
                    genBtn.classList.remove('is-loading');
                }
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
    window.BenTrade.renderSourceHealthPlaceholder();
    window.BenTrade.renderStatsPlaceholder(title);
};
