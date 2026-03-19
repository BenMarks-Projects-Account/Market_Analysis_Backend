/**
 * BenTrade — Dashboard Expand / Collapse
 * ========================================
 *
 * Allows the center content panel to expand to full viewport,
 * overlaying banner, left nav, and right sidebar, with a zoom transition.
 *
 * Public API (window.BenTradeExpand):
 *   toggle()    — expand if collapsed, collapse if expanded
 *   expand()    — go fullscreen
 *   collapse()  — return to normal
 *   isExpanded() — boolean
 */
window.BenTradeExpand = (function () {
  'use strict';

  var SHELL_CLASS = 'bt-view-expanded';
  var _shellEl = null;
  var _expanded = false;

  function _getShell() {
    if (!_shellEl) _shellEl = document.querySelector('.shell');
    return _shellEl;
  }

  function expand() {
    var shell = _getShell();
    if (!shell || _expanded) return;
    _expanded = true;
    shell.classList.add(SHELL_CLASS);
  }

  function collapse() {
    var shell = _getShell();
    if (!shell || !_expanded) return;
    _expanded = false;
    shell.classList.remove(SHELL_CLASS);
  }

  function toggle() {
    _expanded ? collapse() : expand();
  }

  function isExpanded() {
    return _expanded;
  }

  /** Collapse on Escape key */
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && _expanded) {
      collapse();
    }
  });

  /** Auto-collapse on route change — return to normal layout */
  window.addEventListener('hashchange', function () {
    if (_expanded) collapse();
  });

  /**
   * Double-click anywhere inside #view toggles expand/collapse.
   * Ignored on interactive elements (inputs, buttons, links, textareas,
   * select) and elements with contenteditable so normal interactions
   * aren't hijacked.
   */
  document.addEventListener('dblclick', function (e) {
    var viewEl = document.getElementById('view');
    if (!viewEl || !viewEl.contains(e.target)) return;
    var tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' ||
        tag === 'BUTTON' || tag === 'A' || e.target.isContentEditable ||
        e.target.closest('button') || e.target.closest('a')) return;
    e.preventDefault();
    toggle();
  });

  return {
    expand: expand,
    collapse: collapse,
    toggle: toggle,
    isExpanded: isExpanded,
  };
})();
