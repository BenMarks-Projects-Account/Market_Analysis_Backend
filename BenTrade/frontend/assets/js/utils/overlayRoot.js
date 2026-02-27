/**
 * BenTrade — Overlay Root Utility.
 *
 * Provides a single function to resolve the correct DOM mount point for
 * tooltips, modals, popovers and toasts.
 *
 * Problem:
 *   When the app enters fullscreen via `.shell.requestFullscreen()`, only
 *   DOM children of `.shell` are visible.  Elements appended to
 *   `document.body` (outside the fullscreen subtree) become invisible.
 *
 * Solution:
 *   An `<div id="overlay-root">` lives inside `.shell` in index.html.
 *   All overlay components mount there instead of document.body.
 *   A `fullscreenchange` listener automatically re-parents any stray
 *   overlays that were accidentally appended to body.
 *
 * Usage:
 *   const root = window.BenTradeOverlayRoot.get();
 *   root.appendChild(myOverlayEl);
 *
 * No dependencies — load before tooltip.js, app.js, data_workbench_modal.js.
 */
window.BenTradeOverlayRoot = (function () {
  'use strict';

  var OVERLAY_ROOT_ID = 'overlay-root';

  /**
   * Return the correct DOM element to append overlay content to.
   *
   * Priority:
   *   1. #overlay-root (lives inside .shell — always in fullscreen subtree)
   *   2. document.fullscreenElement (fallback if overlay-root missing)
   *   3. document.body (non-fullscreen fallback)
   *
   * @returns {HTMLElement}
   */
  function get() {
    var el = document.getElementById(OVERLAY_ROOT_ID);
    if (el) return el;
    return document.fullscreenElement || document.body;
  }

  /**
   * Re-parent known body-level overlays into the overlay-root when
   * fullscreen is entered.  This catches any overlays that were created
   * before this utility loaded or that bypassed get().
   */
  function _rehome() {
    var root = document.getElementById(OVERLAY_ROOT_ID);
    if (!root) return;

    // Known overlay selectors that must live inside the fullscreen subtree
    var selectors = [
      '#btMetricTooltip',
      '#btBenTooltip',
      '#modal',
      '#tradeTicketOverlay',
      '.dwb-modal-overlay',
      '#strategyDefaultsToast',
    ];

    selectors.forEach(function (sel) {
      var el = document.querySelector(sel);
      if (el && el.parentElement === document.body) {
        root.appendChild(el);
        if (window.__BEN_DEBUG_OVERLAYS) {
          console.debug('[BenTrade:overlay]', 'Re-parented', sel, 'into overlay-root');
        }
      }
    });
  }

  // Listen for fullscreen changes to auto-rehome stray overlays
  document.addEventListener('fullscreenchange', function () {
    if (document.fullscreenElement) {
      _rehome();
    }
    if (window.__BEN_DEBUG_OVERLAYS) {
      console.debug('[BenTrade:overlay]', 'fullscreenchange →',
        document.fullscreenElement ? 'ENTERED fullscreen' : 'EXITED fullscreen',
        'fullscreenElement:', document.fullscreenElement);
    }
  });

  return {
    get: get,
  };
})();
