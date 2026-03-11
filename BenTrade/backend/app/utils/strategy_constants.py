"""
Shared Strategy & Cluster Constants
=====================================

Thin constants layer for mappings genuinely shared across
portfolio risk, decision policy, and related modules.

Only values that are imported by multiple consumers belong here.
One-off constants stay in their home module.
"""

from __future__ import annotations

# ── Correlated-asset clusters ────────────────────────────────────────
# ETFs and index products that track similar market segments.
# Used by:
#   - portfolio_risk_engine  (correlation exposure grouping)
#   - decision_policy        (correlated-cluster policy check)

CORRELATION_CLUSTERS: dict[str, list[str]] = {
    "sp500":       ["SPY", "SPX", "XSP", "ES", "VOO", "IVV"],
    "nasdaq":      ["QQQ", "NDX", "NQ", "TQQQ", "SQQQ"],
    "russell":     ["IWM", "RUT", "TNA", "TZA"],
    "dow":         ["DIA", "DJX", "YM"],
    "volatility":  ["VIX", "VXX", "UVXY", "SVXY", "VIXY"],
    "bonds":       ["TLT", "IEF", "SHY", "AGG", "BND", "HYG", "LQD"],
    "gold":        ["GLD", "GDX", "IAU", "SLV"],
    "energy":      ["XLE", "USO", "OIH", "XOP"],
    "tech":        ["XLK", "SMH", "SOXX", "ARKK"],
    "financials":  ["XLF", "KRE", "KBE"],
}

# Reverse lookup: symbol → cluster name
SYMBOL_TO_CLUSTER: dict[str, str] = {}
for _cluster, _symbols in CORRELATION_CLUSTERS.items():
    for _sym in _symbols:
        SYMBOL_TO_CLUSTER[_sym] = _cluster
