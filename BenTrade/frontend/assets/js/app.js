// BenTrade — Execution modal adapter
// Delegates to the new TradeTicket modal (trade_ticket.js).
// Maintains backward-compatible open(trade, options) signature so
// existing call sites (strategy_dashboard_shell, home, stock_scanner) keep working.
window.BenTradeExecutionModal = window.BenTradeExecutionModal || (function(){

    function open(trade, options){
        // The new TradeTicket handles normalization, validation, preview, and submission.
        if(window.BenTradeTradeTicket && window.BenTradeTradeTicket.open){
            window.BenTradeTradeTicket.open(trade, { rawTrade: trade });
        } else {
            console.error('[BenTradeExecutionModal] TradeTicket module not loaded');
            alert('Trade ticket module unavailable');
        }
    }

    return { open: open };
})();

