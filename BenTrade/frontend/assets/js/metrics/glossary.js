/**
 * BenTrade — Metric Glossary (backward-compatible façade).
 *
 * Delegates to the centralized BenTradeTooltipDictionary.
 * Existing code that reads window.BenTradeMetrics.glossary continues
 * to work without changes.
 */
window.BenTradeMetrics = window.BenTradeMetrics || {};

window.BenTradeMetrics.glossary = (
  window.BenTradeTooltipDictionary
    ? window.BenTradeTooltipDictionary.allMetrics()
    : {}
);

