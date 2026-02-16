window.BenTradePages = window.BenTradePages || {};

window.BenTradePages.initCreditSpread = function initCreditSpreadPage(rootEl){
  // Keep behavior unchanged while progressively moving logic out of app.js.
  // TODO(architecture): migrate remaining credit spread logic from app.js into this page module.
  return window.BenTrade?.initCreditSpread?.(rootEl);
};
