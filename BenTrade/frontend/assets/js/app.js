// BenTrade — Shared execution modal (used by strategy dashboards)
window.BenTradeExecutionModal = window.BenTradeExecutionModal || (function(){
    const toNumber = window.BenTradeUtils.format.toNumber;
    const fmtMoney  = window.BenTradeUtils.format.money;

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

