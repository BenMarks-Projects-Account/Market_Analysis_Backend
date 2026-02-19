/**
 * BenTrade — Symbol Universe Selector (reusable component)
 *
 * Renders a chip bar showing the global symbol list with add/remove,
 * plus an optional multi-select filter (scan only subset).
 *
 * Depends on:
 *   - BenTradeSymbolUniverseStore   (global store)
 *   - BenTradeUtils.format          (escapeHtml)
 *
 * Exposed as  window.BenTradeSymbolUniverseSelector
 *
 * Usage:
 *   const sel = BenTradeSymbolUniverseSelector.mount(containerEl, {
 *     showFilter: true,      // show multi-select filter (default false)
 *     onChange: (selected) => { ... },  // called when filter selection changes
 *   });
 *   sel.getSelected();     // current selected symbols ([] = all)
 *   sel.destroy();         // clean up listeners
 */
window.BenTradeSymbolUniverseSelector = (function(){
  'use strict';

  const esc = window.BenTradeUtils?.format?.escapeHtml || function(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); };

  function mount(container, options){
    if(!container) return null;
    const opts = options || {};
    const store = window.BenTradeSymbolUniverseStore;
    if(!store) return null;

    const showFilter = !!opts.showFilter;
    let _selected = new Set();       // empty = all
    let _unsub = null;

    /* ── helpers ── */
    function _doAdd(input){
      const val = input.value.trim().toUpperCase();
      if(val && store.addSymbol(val)){
        input.value = '';
      }else if(val){
        input.classList.add('shake');
        setTimeout(() => input.classList.remove('shake'), 400);
      }
    }

    /* ── Render ── */
    function render(){
      const symbols = store.getSymbols();
      const chipsHtml = symbols.map(sym => {
        const isActive = _selected.size === 0 || _selected.has(sym);
        const activeCls = showFilter ? (isActive ? ' symbol-chip-active' : ' symbol-chip-inactive') : '';
        return `<span class="symbol-chip${activeCls}" data-symbol="${esc(sym)}">`
          + `<span class="symbol-chip-text">${esc(sym)}</span>`
          + `<span class="symbol-chip-remove" data-remove-symbol="${esc(sym)}" title="Remove ${esc(sym)}">\u00D7</span>`
          + `</span>`;
      }).join('');

      const filterLabel = showFilter
        ? `<span class="symbol-filter-label stock-note" style="font-size:10px;margin-right:4px;">${_selected.size === 0 ? 'All symbols' : _selected.size + ' selected'}:</span>`
        : '';

      const emptyHint = symbols.length === 0
        ? '<span class="symbol-empty-hint">No symbols — scanning all defaults</span>'
        : '';

      container.innerHTML = `
        <div class="symbol-add-row">
          <input type="text" class="symbol-add-input" maxlength="6" placeholder="Ticker" aria-label="Add symbol" />
          <button type="button" class="symbol-add-btn" title="Add symbol">Add</button>
        </div>
        <div class="symbol-chip-box">
          ${filterLabel}${chipsHtml}${emptyHint}
        </div>
      `;

      /* Wire add-row events */
      const input = container.querySelector('.symbol-add-input');
      const addBtn = container.querySelector('.symbol-add-btn');
      if(input){
        input.addEventListener('keydown', (e) => {
          if(e.key === 'Enter'){
            e.preventDefault();
            _doAdd(input);
          }
        });
      }
      if(addBtn && input){
        addBtn.addEventListener('click', () => _doAdd(input));
      }

      container.querySelectorAll('[data-remove-symbol]').forEach(el => {
        el.addEventListener('click', (e) => {
          e.stopPropagation();
          const sym = el.dataset.removeSymbol;
          store.removeSymbol(sym);
          _selected.delete(sym);
        });
      });

      if(showFilter){
        container.querySelectorAll('.symbol-chip').forEach(chip => {
          chip.addEventListener('click', (e) => {
            if(e.target.closest('[data-remove-symbol]')) return;
            const sym = chip.dataset.symbol;
            if(_selected.has(sym)){
              _selected.delete(sym);
            }else{
              _selected.add(sym);
            }
            render();
            if(typeof opts.onChange === 'function') opts.onChange(getSelected());
          });
        });
      }
    }

    function getSelected(){
      if(_selected.size === 0) return store.getSymbols();
      return store.getSymbols().filter(s => _selected.has(s));
    }

    function destroy(){
      if(_unsub) _unsub();
      container.innerHTML = '';
    }

    /* Subscribe to store changes */
    _unsub = store.subscribe(() => render());
    render();

    return { getSelected, destroy, render };
  }

  return { mount };
})();
