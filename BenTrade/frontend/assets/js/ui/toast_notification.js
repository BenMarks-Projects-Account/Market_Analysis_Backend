/**
 * BenTrade toast notification system.
 * Shows pop-up notifications in the lower-right corner.
 * Mounts inside BenTradeOverlayRoot for fullscreen support.
 */
(function(global) {
  var _container = null;
  var TOAST_DURATION = 10000; // 10 seconds

  function _esc(s) {
    if (!s) return '';
    var el = document.createElement('span');
    el.textContent = s;
    return el.innerHTML;
  }

  function _getContainer() {
    if (_container && _container.parentNode) return _container;

    _container = document.createElement('div');
    _container.id = 'bentrade-toast-container';
    _container.style.cssText =
      'position:fixed; bottom:20px; right:20px; z-index:9999; ' +
      'display:flex; flex-direction:column-reverse; gap:8px; max-width:400px; pointer-events:none;';

    var root = global.BenTradeOverlayRoot ? global.BenTradeOverlayRoot.get() : document.body;
    root.appendChild(_container);

    return _container;
  }

  function showTradeSignal(notification) {
    var container = _getContainer();

    var borderColor = notification.type === 'stock_buy' ? '#00c853' : '#00e0c3';
    var typeLabel = notification.type === 'stock_buy' ? 'STOCK BUY' : 'OPTIONS EXECUTE';

    var toast = document.createElement('div');
    toast.className = 'bt-toast-signal';
    toast.style.cssText =
      'pointer-events:auto; background:rgba(13,17,23,0.95); ' +
      'border:1px solid rgba(255,255,255,0.1); border-left:4px solid ' + borderColor + '; ' +
      'border-radius:8px; padding:12px 16px; min-width:300px; ' +
      'box-shadow:0 4px 20px rgba(0,0,0,0.5); ' +
      'animation:btToastSlideIn 0.3s ease-out; cursor:pointer;';

    // Build content safely
    var header = document.createElement('div');
    header.style.cssText = 'display:flex; justify-content:space-between; align-items:start;';

    var left = document.createElement('div');
    left.innerHTML =
      '<span style="background:' + borderColor + '20; color:' + borderColor +
      '; padding:2px 6px; border-radius:3px; font-size:0.7rem; font-weight:600;">' +
      _esc(typeLabel) + '</span>' +
      '<strong style="margin-left:8px; font-size:1rem; color:#e0e0e0;">' +
      _esc(notification.symbol || '') + '</strong>' +
      '<span style="color:rgba(224,224,224,0.5); margin-left:4px;">' +
      _esc(notification.strategy || '') + '</span>';

    var closeBtn = document.createElement('button');
    closeBtn.textContent = '\u00d7';
    closeBtn.style.cssText =
      'background:none; border:none; color:rgba(224,224,224,0.4); cursor:pointer; ' +
      'font-size:1.2rem; padding:0 0 0 8px; line-height:1;';
    closeBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      _dismiss(toast);
    });

    header.appendChild(left);
    header.appendChild(closeBtn);
    toast.appendChild(header);

    if (notification.headline) {
      var hl = document.createElement('div');
      hl.style.cssText = 'margin-top:6px; font-size:0.85rem; color:rgba(224,224,224,0.8);';
      hl.textContent = notification.headline;
      toast.appendChild(hl);
    }

    var meta = document.createElement('div');
    meta.style.cssText = 'margin-top:4px; font-size:0.75rem; color:rgba(224,224,224,0.5);';
    var parts = [];
    parts.push('Score: ' + (notification.score != null ? notification.score : '--'));
    parts.push('Conviction: ' + (notification.conviction != null ? notification.conviction + '%' : '--'));
    if (notification.strikes) parts.push(notification.strikes);
    meta.textContent = parts.join(' | ');
    toast.appendChild(meta);

    // Click to navigate to notifications page
    toast.addEventListener('click', function() {
      location.hash = '#/notifications';
      _dismiss(toast);
    });

    container.appendChild(toast);

    // Auto-dismiss
    var timer = setTimeout(function() { _dismiss(toast); }, TOAST_DURATION);
    toast._dismissTimer = timer;
  }

  function _dismiss(toast) {
    if (!toast || !toast.parentNode) return;
    if (toast._dismissTimer) clearTimeout(toast._dismissTimer);
    toast.style.animation = 'btToastSlideOut 0.3s ease-in forwards';
    setTimeout(function() {
      if (toast.parentNode) toast.remove();
    }, 300);
  }

  global.BenTradeToastNotification = {
    showTradeSignal: showTradeSignal,
  };

})(window);
