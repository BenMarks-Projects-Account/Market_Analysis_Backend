/**
 * BenTrade — Home Dashboard Note Sections Registry (v1)
 *
 * Hand-curated map of {section_id: display_name} for clickable note-enabled
 * sections on the home dashboard. Adding a section is a two-line change:
 *   1) add an entry here
 *   2) add ``data-note-section="<id>"`` to the matching heading
 *
 * Keep in lockstep with the server allow-list:
 *   BenTrade/backend/app/services/notes_service.py :: ALLOWED_HOME_SECTIONS
 * (parity is enforced by tests/test_routes_notes.py).
 */
window.BenTradeHomeNoteSections = Object.freeze({
  pre_market_intelligence: "PRE-MARKET INTELLIGENCE",
  pre_market_indicators: "Pre-Market Indicators",
  index_futures_continuous_48h: "Index Futures — Continuous (48h)",
  market_regime: "Market Regime",
  macro_market_proxies: "Macro Market Proxies",
});
