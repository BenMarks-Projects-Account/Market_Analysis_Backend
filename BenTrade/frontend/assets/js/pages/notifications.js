/* ── Notifications Page ── */
window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initNotifications = function initNotifications(rootEl) {
  var doc = (rootEl && rootEl.ownerDocument) ? rootEl.ownerDocument : document;
  var scope = rootEl || doc;
  var _pollTimer = null;

  // ── Helpers ──
  function _esc(s) {
    if (!s) return '';
    var el = document.createElement('span');
    el.textContent = s;
    return el.innerHTML;
  }

  function _formatTs(iso) {
    if (!iso) return '';
    try { return new Date(iso).toLocaleString(); }
    catch (e) { return iso; }
  }

  function _cleanStrategy(s) {
    if (!s) return '';
    return s
      .replace(/^stock_/, '')
      .replace(/_/g, ' ')
      .replace(/\b\w/g, function(c) { return c.toUpperCase(); });
  }

  // ── Card renderer ──
  function renderCard(n) {
    var isUnread = !n.read;
    var isStock = n.type === 'stock_buy';
    var borderColor = isStock ? '#00c853' : '#00e0c3';
    var typeLabel = isStock ? 'STOCK BUY' : 'OPTIONS EXECUTE';
    var strategyDisplay = _cleanStrategy(n.strategy);
    var timeStr = _formatTs(n.timestamp);

    var scoreDisplay = (n.score != null && n.score !== '' && n.score !== 0)
      ? n.score : null;
    var convictionDisplay = (n.conviction != null && n.conviction !== '' && n.conviction !== 0)
      ? n.conviction + '%' : null;
    var bgColor = isUnread ? 'rgba(255,255,255,0.05)' : 'rgba(255,255,255,0.02)';

    var html = '';
    html += '<div class="notif-card" data-notif-id="' + _esc(n.id) + '" style="';
    html += 'background:' + bgColor + '; border:1px solid rgba(255,255,255,0.08); ';
    html += 'border-left:3px solid ' + borderColor + '; border-radius:6px; ';
    html += 'padding:12px 16px; margin-bottom:8px; cursor:pointer; transition:background 0.2s;">';

    // Row 1: type badge, symbol, strategy, score
    html += '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">';
    html += '<div style="display:flex; align-items:center; gap:10px;">';
    html += '<span style="background:' + borderColor + '15; color:' + borderColor + '; ';
    html += 'padding:3px 8px; border-radius:4px; font-size:0.7rem; font-weight:600; ';
    html += 'letter-spacing:0.03em; white-space:nowrap;">' + typeLabel + '</span>';
    if (isUnread) {
      html += '<span style="background:#ff1744; color:white; padding:2px 6px; border-radius:3px; ';
      html += 'font-size:0.65rem; font-weight:600;">NEW</span>';
    }
    html += '<span style="color:#e0e0e0; font-size:1.1rem; font-weight:700;">' + _esc(n.symbol || '--') + '</span>';
    html += '<span style="color:rgba(224,224,224,0.5); font-size:0.85rem;">' + _esc(strategyDisplay) + '</span>';
    html += '</div>';
    html += '<div style="text-align:right;">';
    if (scoreDisplay) {
      html += '<span style="color:#00c853; font-weight:600; font-size:0.95rem;">Score: ' + scoreDisplay + '</span>';
    }
    if (convictionDisplay) {
      html += '<span style="color:rgba(224,224,224,0.5); font-size:0.8rem; margin-left:8px;">Conv: ' + convictionDisplay + '</span>';
    }
    html += '</div>';
    html += '</div>';

    // Row 2: headline
    if (n.headline) {
      html += '<div style="color:rgba(224,224,224,0.8); font-size:0.85rem; margin-bottom:4px; line-height:1.4;">';
      html += _esc(n.headline) + '</div>';
    }

    // Row 3: options details
    if (n.strikes) {
      html += '<div style="color:rgba(224,224,224,0.4); font-size:0.78rem; margin-bottom:4px;">';
      html += 'Strikes: ' + _esc(n.strikes) + ' · Exp: ' + _esc(n.expiration || '--') + ' · ' + (n.dte != null ? n.dte : '--') + 'd';
      if (n.pop != null) html += ' · POP: ' + (n.pop * 100).toFixed(0) + '%';
      if (n.ev != null) html += ' · EV: $' + Number(n.ev).toFixed(0);
      if (n.credit != null) html += ' · Credit: $' + Number(n.credit).toFixed(2);
      if (n.debit != null) html += ' · Debit: $' + Number(n.debit).toFixed(2);
      html += '</div>';
    }

    // Row 4: timestamp + price
    html += '<div style="display:flex; justify-content:space-between; align-items:center;">';
    html += '<span style="color:rgba(224,224,224,0.3); font-size:0.75rem;">' + timeStr + '</span>';
    if (n.price != null) {
      html += '<span style="color:rgba(224,224,224,0.4); font-size:0.78rem;">$' + Number(n.price).toFixed(2) + '</span>';
    }
    html += '</div>';

    html += '</div>';
    return html;
  }

  // ── Load notifications ──
  function loadNotifications() {
    fetch('/api/notifications?limit=100')
      .then(function(res) { return res.ok ? res.json() : null; })
      .then(function(data) {
        if (!data) return;
        renderList(data.notifications || []);
      })
      .catch(function(e) { console.warn('[NOTIFICATIONS] load failed:', e); });
  }

  function renderList(notifications) {
    var listEl = scope.querySelector('#notifications-list');
    if (!listEl) return;

    if (!notifications || notifications.length === 0) {
      listEl.innerHTML =
        '<div style="text-align:center; padding:60px 20px; color:rgba(224,224,224,0.3);">' +
        '<div style="font-size:2rem; margin-bottom:12px;">🔔</div>' +
        '<div>No notifications yet</div>' +
        '<div style="font-size:0.85rem; margin-top:4px;">BUY and EXECUTE signals will appear here when detected</div>' +
        '</div>';
      return;
    }

    var html = '';
    for (var i = 0; i < notifications.length; i++) {
      html += renderCard(notifications[i]);
    }
    listEl.innerHTML = html;

    // Click handlers for mark-read
    var cards = listEl.querySelectorAll('.notif-card');
    for (var j = 0; j < cards.length; j++) {
      cards[j].addEventListener('click', _handleCardClick);
    }
  }

  function _handleCardClick(e) {
    var card = e.currentTarget;
    var id = card.getAttribute('data-notif-id');
    if (!id) return;
    fetch('/api/notifications/read?notification_id=' + encodeURIComponent(id), { method: 'POST' })
      .then(function() {
        var badge = card.querySelector('[style*="background:#ff1744"]');
        if (badge) badge.remove();
        card.style.background = 'rgba(255,255,255,0.02)';
      })
      .catch(function(e) { console.warn('[NOTIFICATIONS] mark read failed:', e); });
  }

  // ── Mark all ──
  var markAllBtn = scope.querySelector('#notifMarkAllBtn');
  if (markAllBtn) {
    markAllBtn.addEventListener('click', function() {
      fetch('/api/notifications/read', { method: 'POST' })
        .then(function() { loadNotifications(); })
        .catch(function(e) { console.warn('[NOTIFICATIONS] mark all failed:', e); });
    });
  }

  // ── Clear all ──
  var clearBtn = scope.querySelector('#notifClearBtn');
  if (clearBtn) {
    clearBtn.addEventListener('click', function() {
      if (!confirm('Clear all notifications?')) return;
      fetch('/api/notifications/clear', { method: 'POST' })
        .then(function() { loadNotifications(); })
        .catch(function(e) { console.warn('[NOTIFICATIONS] clear failed:', e); });
    });
  }

  // ── Initial load + auto-refresh ──
  loadNotifications();
  _pollTimer = setInterval(loadNotifications, 15000);

  // ── Cleanup ──
  return function cleanupNotifications() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  };
};
