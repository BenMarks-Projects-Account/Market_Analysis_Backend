// BenTrade — Shared execution modal (used by strategy dashboards)
window.BenTradeExecutionModal = window.BenTradeExecutionModal || (function(){
    const _accessor = window.BenTradeUtils.tradeAccessor;
    const fmtMoney  = window.BenTradeUtils.format.money;

    function tradeDetailsHtml(trade){
        const row = (trade && typeof trade === 'object') ? trade : {};
        const symbol = _accessor.resolveString(row, 'symbol') || 'N/A';
        const strategy = _accessor.resolveString(row, 'strategy') || 'N/A';
        const expiration = _accessor.resolveString(row, 'expiration') || 'N/A';
        const shortStrike = _accessor.resolveString(row, 'short_strike') || 'N/A';
        const longStrike = _accessor.resolveString(row, 'long_strike') || 'N/A';
        const maxLoss = fmtMoney(_accessor.resolve(row, 'max_loss'));
        const maxProfit = fmtMoney(_accessor.resolve(row, 'max_profit'));
        const creditDebitRaw = _accessor.resolve(row, 'net_credit') ?? _accessor.resolve(row, 'net_debit');
        const creditDebitLabel = creditDebitRaw === null ? 'Credit/Debit' : (creditDebitRaw >= 0 ? 'Credit' : 'Debit');
        const creditDebitValue = creditDebitRaw === null ? 'N/A' : `$${Math.abs(creditDebitRaw).toFixed(2)}`;

        return `
            <div style="display:grid;grid-template-columns:1fr;gap:6px;text-align:left;">
                <div><strong>Symbol:</strong> ${symbol}</div>
                <div><strong>Strategy:</strong> ${strategy}</div>
                <div><strong>Expiry:</strong> ${expiration}</div>
                <div><strong>Strikes:</strong> ${shortStrike} / ${longStrike}</div>
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

