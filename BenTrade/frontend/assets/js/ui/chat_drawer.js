/**
 * BenTrade — Contextual Chat Drawer
 * ===================================
 *
 * Reusable right-side chat drawer/panel for context-grounded AI conversations.
 * The first consumer is Market Regime; the architecture is generic so any
 * dashboard panel can open this same drawer with a different context.
 *
 * Public API (window.BenTradeChat):
 *   open(contextContract)  — open drawer with new session (or restore minimized)
 *   minimize()             — slide drawer away, keep session alive, show bubble
 *   endChat()              — destroy session, remove bubble, fully close
 *   isOpen()               — boolean
 *   hasSession()           — boolean (active or minimized session exists)
 *
 * Context contract shape:
 * {
 *   context_type:    string   e.g. "market_regime"
 *   context_title:   string   e.g. "Market Regime"
 *   context_summary: string   short description for display
 *   context_payload: object   curated data for the model
 *   source_panel:    string   e.g. "home.regime"
 *   generated_at:    string   ISO timestamp
 *   quick_starters:  string[] (optional) context-specific starter prompts
 * }
 */
window.BenTradeChat = (function () {
  'use strict';

  const api = window.BenTradeApi;

  // ── DOM references (created once, reused) ────────────────────────
  let _drawerEl = null;
  let _backdropEl = null;
  let _bubbleEl = null;      // minimized chat bubble
  let _headerTitleEl = null;
  let _closeBtnEl = null;
  let _endBtnEl = null;
  let _resetBtnEl = null;
  let _messagesEl = null;
  let _inputEl = null;
  let _sendBtnEl = null;
  let _errorEl = null;
  let _startersEl = null;

  // ── Session state (in-memory only, no persistence) ───────────────
  let _session = null;  // { context, messages: [{role, content, timestamp}] }
  let _inflight = false;
  let _lastFailedText = null;

  // ── Default quick starters per context type (frontend fallback) ──
  var _FALLBACK_STARTERS = {
    market_regime: [
      'What strategies fit this regime?',
      'What are the biggest risks today?',
      'Explain structural vs tape vs tactical',
      'How should I size risk right now?',
      'What would flip this regime?',
      'Why is the tape narrow?',
    ],
  };

  // ══════════════════════════════════════════════════════════════════
  // DOM construction (one-time)
  // ══════════════════════════════════════════════════════════════════

  function _ensureDOM() {
    if (_drawerEl) return;

    // Backdrop — clicking it minimizes (does NOT end) the chat
    _backdropEl = document.createElement('div');
    _backdropEl.className = 'bt-chat-backdrop';
    _backdropEl.addEventListener('click', minimize);

    // Drawer
    _drawerEl = document.createElement('aside');
    _drawerEl.className = 'bt-chat-drawer';
    _drawerEl.setAttribute('role', 'complementary');
    _drawerEl.setAttribute('aria-label', 'AI Chat');

    _drawerEl.innerHTML = [
      '<div class="bt-chat-header">',
      '  <div class="bt-chat-header-left">',
      '    <div class="bt-chat-header-icon">',
      '      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
      '    </div>',
      '    <div class="bt-chat-header-text">',
      '      <div class="bt-chat-header-title">AI Analysis Chat</div>',
      '      <div class="bt-chat-header-subtitle"></div>',
      '    </div>',
      '  </div>',
      '  <div class="bt-chat-header-actions">',
      '    <button class="bt-chat-reset-btn" type="button" aria-label="New chat" title="New chat">',
      '      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>',
      '    </button>',
      '    <button class="bt-chat-close-btn" type="button" aria-label="Minimize chat" title="Minimize">',
      '      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/></svg>',
      '    </button>',
      '    <button class="bt-chat-end-btn" type="button" aria-label="End chat" title="End chat">&times;</button>',
      '  </div>',
      '</div>',
      '<div class="bt-chat-context-banner"></div>',
      '<div class="bt-chat-starters"></div>',
      '<div class="bt-chat-messages" role="log" aria-live="polite"></div>',
      '<div class="bt-chat-error" style="display:none;"></div>',
      '<div class="bt-chat-input-bar">',
      '  <textarea class="bt-chat-input" placeholder="Ask about this context\u2026" rows="1"></textarea>',
      '  <button class="bt-chat-send-btn" type="button" aria-label="Send message" title="Send">',
      '    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
      '  </button>',
      '</div>',
    ].join('\n');

    _headerTitleEl = _drawerEl.querySelector('.bt-chat-header-title');
    _closeBtnEl = _drawerEl.querySelector('.bt-chat-close-btn');
    _endBtnEl = _drawerEl.querySelector('.bt-chat-end-btn');
    _resetBtnEl = _drawerEl.querySelector('.bt-chat-reset-btn');
    _messagesEl = _drawerEl.querySelector('.bt-chat-messages');
    _inputEl = _drawerEl.querySelector('.bt-chat-input');
    _sendBtnEl = _drawerEl.querySelector('.bt-chat-send-btn');
    _errorEl = _drawerEl.querySelector('.bt-chat-error');
    _startersEl = _drawerEl.querySelector('.bt-chat-starters');

    // Events
    _closeBtnEl.addEventListener('click', minimize);
    _endBtnEl.addEventListener('click', endChat);
    _resetBtnEl.addEventListener('click', _onReset);
    _sendBtnEl.addEventListener('click', _onSend);
    _inputEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        _onSend();
      }
    });
    // Auto-resize textarea
    _inputEl.addEventListener('input', function () {
      _inputEl.style.height = 'auto';
      _inputEl.style.height = Math.min(_inputEl.scrollHeight, 120) + 'px';
    });

    // Minimized bubble (persistent, hidden until needed)
    _bubbleEl = document.createElement('button');
    _bubbleEl.className = 'bt-chat-bubble';
    _bubbleEl.setAttribute('aria-label', 'Reopen chat');
    _bubbleEl.setAttribute('title', 'Reopen chat');
    _bubbleEl.innerHTML = [
      '<svg class="bt-chat-bubble-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
      '<button class="bt-chat-bubble-end" type="button" aria-label="End chat" title="End chat">&times;</button>',
    ].join('');
    _bubbleEl.addEventListener('click', function (e) {
      if (e.target.closest('.bt-chat-bubble-end')) {
        e.stopPropagation();
        endChat();
        return;
      }
      _reopenFromBubble();
    });

    document.body.appendChild(_backdropEl);
    document.body.appendChild(_drawerEl);
    document.body.appendChild(_bubbleEl);

    // Re-parent into overlay-root on fullscreen changes
    document.addEventListener('fullscreenchange', _rehomeDrawer);
  }

  /** Move drawer + backdrop + bubble into overlay-root (fullscreen) or back to body. */
  function _rehomeDrawer() {
    var root = (window.BenTradeOverlayRoot && window.BenTradeOverlayRoot.get)
      ? window.BenTradeOverlayRoot.get()
      : document.body;
    if (_backdropEl && _backdropEl.parentElement !== root) root.appendChild(_backdropEl);
    if (_drawerEl  && _drawerEl.parentElement  !== root) root.appendChild(_drawerEl);
    if (_bubbleEl  && _bubbleEl.parentElement  !== root) root.appendChild(_bubbleEl);
  }

  // ══════════════════════════════════════════════════════════════════
  // Public API
  // ══════════════════════════════════════════════════════════════════

  /**
   * Open the chat drawer.
   * - If no active session: starts a new one, seeds it, shows starters.
   * - If a minimized session exists for the same context: restores it.
   * - If a minimized session exists for a DIFFERENT context: ends old, starts new.
   */
  function open(contextContract) {
    _ensureDOM();

    // Collapse expanded panel if needed
    if (window.BenTradeExpand && window.BenTradeExpand.isExpanded()) {
      window.BenTradeExpand.collapse();
    }

    _rehomeDrawer();

    // Validate
    var ctxErrors = _validateContextContract(contextContract);
    if (ctxErrors.length) {
      _showDrawer();
      _messagesEl.innerHTML = '';
      _startersEl.innerHTML = '';
      _startersEl.style.display = 'none';
      _showError('Cannot start chat: ' + ctxErrors.join('; '));
      _setSending(true);
      return;
    }

    // If we already have a minimized session for the same context, just reopen it
    if (_session && _session.context &&
        _session.context.context_type === contextContract.context_type) {
      _hideBubble();
      _showDrawer();
      _inputEl.focus();
      return;
    }

    // Different context or no session — start fresh
    _startNewSession(contextContract);
  }

  /** Minimize: hide drawer, show bubble, keep session alive. */
  function minimize() {
    if (!_drawerEl) return;
    _drawerEl.classList.remove('bt-chat-drawer--open');
    _backdropEl.classList.remove('bt-chat-backdrop--visible');
    document.body.classList.remove('bt-chat-body-lock');
    if (_session) _showBubble();
  }

  /** End chat: destroy session, remove bubble, fully close. */
  function endChat() {
    _session = null;
    _inflight = false;
    _lastFailedText = null;
    if (_drawerEl) {
      _drawerEl.classList.remove('bt-chat-drawer--open');
    }
    if (_backdropEl) {
      _backdropEl.classList.remove('bt-chat-backdrop--visible');
    }
    document.body.classList.remove('bt-chat-body-lock');
    _hideBubble();
  }

  function isOpen() {
    return _drawerEl ? _drawerEl.classList.contains('bt-chat-drawer--open') : false;
  }

  function hasSession() {
    return !!_session;
  }

  // ══════════════════════════════════════════════════════════════════
  // Internal: Session management
  // ══════════════════════════════════════════════════════════════════

  function _startNewSession(contextContract) {
    _session = {
      context: contextContract,
      messages: [],
    };
    _lastFailedText = null;

    // Update header
    var title = contextContract.context_title || contextContract.context_type || 'AI Chat';
    _headerTitleEl.textContent = 'AI Analysis Chat';
    _drawerEl.querySelector('.bt-chat-header-subtitle').textContent = title;

    // Context banner
    _updateContextBanner(contextContract);

    // Clear UI
    _messagesEl.innerHTML = '';
    _inputEl.value = '';
    _inputEl.style.height = 'auto';
    _hideError();

    // Show quick starters
    _renderStarters(contextContract);

    // Hide bubble, show drawer
    _hideBubble();
    _showDrawer();

    // Auto-send seeded request
    _sendSeeded(contextContract);
  }

  function _onReset() {
    if (!_session) return;
    _startNewSession(_session.context);
  }

  function _reopenFromBubble() {
    if (!_session) return;
    _hideBubble();
    _showDrawer();
    _scrollToBottom();
    _inputEl.focus();
  }

  function _showDrawer() {
    _drawerEl.classList.add('bt-chat-drawer--open');
    _backdropEl.classList.add('bt-chat-backdrop--visible');
    document.body.classList.add('bt-chat-body-lock');
  }

  function _showBubble() {
    if (!_bubbleEl) return;
    _bubbleEl.classList.add('bt-chat-bubble--visible');
  }

  function _hideBubble() {
    if (!_bubbleEl) return;
    _bubbleEl.classList.remove('bt-chat-bubble--visible');
  }

  // ══════════════════════════════════════════════════════════════════
  // Internal: Quick starters
  // ══════════════════════════════════════════════════════════════════

  function _renderStarters(ctx) {
    if (!_startersEl) return;
    var ctxType = ctx.context_type || '';
    // Allow context contract to supply custom starters, fall back to registry
    var starters = ctx.quick_starters || _FALLBACK_STARTERS[ctxType] || [];
    if (!starters.length) {
      _startersEl.style.display = 'none';
      return;
    }
    _startersEl.innerHTML = '';
    _startersEl.style.display = '';
    var label = document.createElement('div');
    label.className = 'bt-chat-starters-label';
    label.textContent = 'Quick starters';
    _startersEl.appendChild(label);
    var grid = document.createElement('div');
    grid.className = 'bt-chat-starters-grid';
    starters.forEach(function (text) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'bt-chat-starter-btn';
      btn.textContent = text;
      btn.addEventListener('click', function () {
        _hideStarters();
        _appendMessage('user', text);
        _sendToModel(text);
      });
      grid.appendChild(btn);
    });
    _startersEl.appendChild(grid);
  }

  function _hideStarters() {
    if (_startersEl) _startersEl.style.display = 'none';
  }

  // ══════════════════════════════════════════════════════════════════
  // Internal: Seeded initial request
  // ══════════════════════════════════════════════════════════════════

  function _sendSeeded(ctx) {
    var seedMessage = _buildSeedMessage(ctx);
    _sendToModel(seedMessage);
  }

  function _buildSeedMessage(ctx) {
    var summary = ctx.context_summary || '';
    var label = (ctx.context_payload || {}).regime_label || '';
    return (
      'Analyze the current ' + (ctx.context_title || ctx.context_type || 'market') + ' conditions. ' +
      (label ? 'The regime is currently ' + label + '. ' : '') +
      (summary ? summary + ' ' : '') +
      'Provide a concise, structured opening read covering: ' +
      '(1) the current regime posture and what is driving it, ' +
      '(2) key risks or notable conditions, and ' +
      '(3) what this means for options strategy selection. ' +
      'Keep it tight and actionable.'
    );
  }

  // ══════════════════════════════════════════════════════════════════
  // Internal: Send / receive
  // ══════════════════════════════════════════════════════════════════

  function _onSend() {
    if (_inflight) return;
    var text = (_inputEl.value || '').trim();
    if (!text) return;

    _inputEl.value = '';
    _inputEl.style.height = 'auto';
    _hideError();
    _hideStarters();
    _removeFollowups();

    _appendMessage('user', text);
    _sendToModel(text);
  }

  function _sendToModel(userText) {
    if (!_session) return;
    _inflight = true;
    _setSending(true);
    _hideError();

    var history = _session.messages
      .filter(function (m) { return m.role === 'user' || m.role === 'assistant'; })
      .map(function (m) { return { role: m.role, content: m.content }; });

    _session.messages.push({ role: 'user', content: userText, timestamp: Date.now() });

    var thinkingEl = _appendThinking();

    api.contextualChat(_session.context, userText, history)
      .then(function (result) {
        _removeThinking(thinkingEl);
        _lastFailedText = null;
        var assistantMsg = result.assistant_message || 'No response received.';
        _session.messages.push({ role: 'assistant', content: assistantMsg, timestamp: Date.now() });
        _appendMessage('assistant', assistantMsg);

        // Show suggested follow-ups if provided
        var followups = result.suggested_followups;
        if (followups && followups.length) {
          _renderFollowups(followups);
        }

        // Hide starters once we have a real conversation going
        _hideStarters();
      })
      .catch(function (err) {
        _removeThinking(thinkingEl);
        _lastFailedText = userText;
        _showError(_formatChatError(err));
      })
      .finally(function () {
        _inflight = false;
        _setSending(false);
        _inputEl.focus();
      });
  }

  // ══════════════════════════════════════════════════════════════════
  // Internal: Suggested follow-ups
  // ══════════════════════════════════════════════════════════════════

  function _renderFollowups(suggestions) {
    _removeFollowups(); // clear any existing
    var container = document.createElement('div');
    container.className = 'bt-chat-followups';
    suggestions.forEach(function (text) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'bt-chat-followup-btn';
      btn.textContent = text;
      btn.addEventListener('click', function () {
        _removeFollowups();
        _appendMessage('user', text);
        _sendToModel(text);
      });
      container.appendChild(btn);
    });
    _messagesEl.appendChild(container);
    _scrollToBottom();
  }

  function _removeFollowups() {
    if (!_messagesEl) return;
    var existing = _messagesEl.querySelectorAll('.bt-chat-followups');
    existing.forEach(function (el) { el.parentNode.removeChild(el); });
  }

  // ══════════════════════════════════════════════════════════════════
  // Internal: DOM helpers
  // ══════════════════════════════════════════════════════════════════

  function _appendMessage(role, content) {
    var bubble = document.createElement('div');
    bubble.className = 'bt-chat-msg bt-chat-msg--' + role;

    var bodyEl = document.createElement('div');
    bodyEl.className = 'bt-chat-msg-body';

    if (role === 'assistant') {
      bodyEl.innerHTML = _renderMarkdown(content);
    } else if (role === 'system') {
      bodyEl.innerHTML = '<em>' + _esc(content) + '</em>';
    } else {
      bodyEl.textContent = content;
    }

    bubble.appendChild(bodyEl);

    var ts = document.createElement('div');
    ts.className = 'bt-chat-msg-ts';
    var d = new Date();
    ts.textContent = d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
    bubble.appendChild(ts);

    _messagesEl.appendChild(bubble);
    _scrollToBottom();
    return bubble;
  }

  function _appendThinking() {
    var el = document.createElement('div');
    el.className = 'bt-chat-msg bt-chat-msg--assistant bt-chat-thinking';
    el.innerHTML = '<div class="bt-chat-msg-body"><span class="bt-chat-dots"><span></span><span></span><span></span></span></div>';
    _messagesEl.appendChild(el);
    _scrollToBottom();
    return el;
  }

  function _removeThinking(el) {
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function _scrollToBottom() {
    requestAnimationFrame(function () {
      _messagesEl.scrollTop = _messagesEl.scrollHeight;
    });
  }

  function _setSending(active) {
    _sendBtnEl.disabled = active;
    _inputEl.disabled = active;
    if (active) {
      _sendBtnEl.classList.add('bt-chat-send-btn--sending');
    } else {
      _sendBtnEl.classList.remove('bt-chat-send-btn--sending');
    }
  }

  function _showError(msg) {
    _errorEl.innerHTML = '';
    var textNode = document.createElement('span');
    textNode.textContent = msg;
    _errorEl.appendChild(textNode);

    if (_lastFailedText) {
      var retryBtn = document.createElement('button');
      retryBtn.type = 'button';
      retryBtn.className = 'bt-chat-retry-btn';
      retryBtn.textContent = 'Retry';
      retryBtn.addEventListener('click', function () {
        var text = _lastFailedText;
        _lastFailedText = null;
        _hideError();
        if (_session && _session.messages.length && _session.messages[_session.messages.length - 1].role === 'user') {
          _session.messages.pop();
        }
        _sendToModel(text);
      });
      _errorEl.appendChild(retryBtn);
    }

    _errorEl.style.display = 'flex';
    _scrollToBottom();
  }

  function _hideError() {
    _errorEl.style.display = 'none';
    _errorEl.innerHTML = '';
  }

  function _validateContextContract(ctx) {
    var errors = [];
    if (!ctx || typeof ctx !== 'object') {
      return ['context contract is missing or invalid'];
    }
    if (!ctx.context_type) errors.push('context_type is required');
    if (!ctx.context_payload || typeof ctx.context_payload !== 'object') {
      errors.push('context_payload is missing');
    }
    return errors;
  }

  function _formatChatError(err) {
    if (!err) return 'Chat request failed. Please try again.';
    var status = err.status;
    if (status === 422 && err.detail) {
      var detail = err.detail;
      if (detail.errors && Array.isArray(detail.errors)) {
        return 'Validation error: ' + detail.errors.join('; ');
      }
      if (detail.message) return detail.message;
    }
    if (status === 405) {
      return 'Request failed (method not allowed). The server may have dropped the connection. Please retry.';
    }
    if (status === 502) {
      return 'The AI model is currently unavailable. Please try again in a moment.';
    }
    if (status >= 500) {
      return 'Server error (' + status + '). Please try again in a moment.';
    }
    if (err.name === 'AbortError') {
      return 'Request timed out. The model may be overloaded \u2014 please try again.';
    }
    if (!status && err.message && err.message.toLowerCase().indexOf('fetch') !== -1) {
      return 'Network error \u2014 could not reach the server. Check your connection.';
    }
    return err.message || 'Chat request failed. Please try again.';
  }

  function _esc(text) {
    var el = document.createElement('span');
    el.textContent = String(text || '');
    return el.innerHTML;
  }

  function _renderMarkdown(text) {
    if (!text) return '';
    var html = _esc(text);
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/((?:^[\-\*]\s+.+$\n?)+)/gm, function(block) {
      var items = block.trim().split('\n').map(function(line) {
        return '<li>' + line.replace(/^[\-\*]\s+/, '') + '</li>';
      }).join('');
      return '<ul>' + items + '</ul>';
    });
    html = html.replace(/((?:^\d+\.\s+.+$\n?)+)/gm, function(block) {
      var items = block.trim().split('\n').map(function(line) {
        return '<li>' + line.replace(/^\d+\.\s+/, '') + '</li>';
      }).join('');
      return '<ol>' + items + '</ol>';
    });
    html = html.replace(/\n\n+/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    return '<p>' + html + '</p>';
  }

  function _updateContextBanner(ctx) {
    var banner = _drawerEl.querySelector('.bt-chat-context-banner');
    if (!banner) return;
    var payload = ctx.context_payload || {};
    var label = payload.regime_label || 'Unknown';
    var score = payload.regime_score != null ? payload.regime_score : '\u2014';
    var conf = payload.confidence != null ? (payload.confidence * 100).toFixed(0) + '%' : '\u2014';
    var asOf = ctx.generated_at ? _formatTimeAgo(ctx.generated_at) : 'just now';

    banner.innerHTML = [
      '<div class="bt-chat-banner-row">',
      '  <span class="bt-chat-banner-dot"></span>',
      '  <span class="bt-chat-banner-label">Grounded in: <strong>' + _esc(ctx.context_title || ctx.context_type) + '</strong></span>',
      '</div>',
      '<div class="bt-chat-banner-detail">',
      '  ' + _esc(label) + ' &middot; Score ' + _esc(String(score)) + ' &middot; Confidence ' + _esc(String(conf)) + ' &middot; ' + _esc(asOf),
      '</div>',
    ].join('');
  }

  function _formatTimeAgo(iso) {
    try {
      var diff = Date.now() - new Date(iso).getTime();
      if (diff < 60000) return 'just now';
      if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
      if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
      return new Date(iso).toLocaleDateString();
    } catch (e) {
      return 'just now';
    }
  }

  // ── Public surface ───────────────────────────────────────────────
  return {
    open: open,
    minimize: minimize,
    endChat: endChat,
    isOpen: isOpen,
    hasSession: hasSession,
  };
})();
