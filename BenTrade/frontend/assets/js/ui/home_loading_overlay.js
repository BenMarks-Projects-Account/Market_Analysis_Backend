window.BenTradeHomeLoadingOverlay = (function(){
  function create(hostEl){
    const host = hostEl && hostEl.ownerDocument ? hostEl : document.body;
    const doc = host.ownerDocument || document;

    const root = doc.createElement('div');
    root.className = 'home-loading-overlay';
    root.innerHTML = `
      <div class="home-loading-modal" role="dialog" aria-modal="true" aria-label="Home loading">
        <div class="home-loading-head">
          <div class="home-loading-spinner" aria-hidden="true"></div>
          <div class="home-loading-status" id="homeLoadingStatus">Starting...</div>
        </div>
        <div class="home-loading-log" id="homeLoadingLog" aria-live="polite"></div>
        <div class="home-loading-actions">
          <button class="btn qtButton" type="button" id="homeLoadingCancel">Cancel</button>
          <button class="btn qtButton" type="button" id="homeLoadingRetry">Retry</button>
        </div>
      </div>
    `;

    host.appendChild(root);

    const statusEl = root.querySelector('#homeLoadingStatus');
    const logEl = root.querySelector('#homeLoadingLog');
    const cancelBtn = root.querySelector('#homeLoadingCancel');
    const retryBtn = root.querySelector('#homeLoadingRetry');

    let lines = [];
    let onCancel = null;
    let onRetry = null;

    function stampLine(text){
      const ts = new Date().toLocaleTimeString();
      return `[${ts}] ${String(text || '')}`;
    }

    function renderLog(){
      logEl.textContent = lines.join('\n');
      logEl.scrollTop = logEl.scrollHeight;
    }

    function setStatus(text){
      statusEl.textContent = String(text || 'Starting...');
    }

    function setLines(newLines){
      lines = Array.isArray(newLines) ? newLines.slice(-500) : [];
      renderLog();
    }

    function appendLog(text){
      lines.push(stampLine(text));
      if(lines.length > 500){
        lines = lines.slice(-500);
      }
      renderLog();
    }

    function open(options){
      const opts = options && typeof options === 'object' ? options : {};
      onCancel = typeof opts.onCancel === 'function' ? opts.onCancel : null;
      onRetry = typeof opts.onRetry === 'function' ? opts.onRetry : null;

      cancelBtn.textContent = String(opts.cancelLabel || 'Cancel');
      retryBtn.style.display = opts.showRetry === false ? 'none' : '';

      if(Array.isArray(opts.logs)){
        setLines(opts.logs);
      }
      setStatus(opts.status || 'Starting...');
      root.classList.add('is-open');
    }

    function close(){
      root.classList.remove('is-open');
    }

    function destroy(){
      root.remove();
    }

    cancelBtn.addEventListener('click', () => {
      if(typeof onCancel === 'function') onCancel();
    });

    retryBtn.addEventListener('click', () => {
      if(typeof onRetry === 'function') onRetry();
    });

    return {
      open,
      close,
      destroy,
      setStatus,
      setLines,
      appendLog,
      getLines: () => lines.slice(),
      isOpen: () => root.classList.contains('is-open'),
    };
  }

  return { create };
})();
