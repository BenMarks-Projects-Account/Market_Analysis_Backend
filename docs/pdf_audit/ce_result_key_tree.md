# CE result key tree

**Source:** Built from the frontend mock fixture in
`BenTrade/frontend/assets/js/pages/on_demand_evaluator.js` lines ~1980-2160
(function `_buildMockData`), cross-referenced against actual reading sites
in `renderPillars` / `renderQualityIndicators` / `renderEntryAndTargets`
which document divergences between mock and real-API shapes.

**Caveat:** A real API capture (via `scripts/dump_ce_result.py`) may show
additional keys not present in the mock fixture — particularly under
`metadata`, `raw_financials.sources`, and the per-pillar `metrics` /
`scores` dicts. The shapes documented below should be treated as a
floor, not a ceiling.

```
ce_result
├── company (dict)
│   ├── symbol: str = "MCY"
│   ├── name: str = "Mercury General Corp."
│   ├── company_name: str (alias of name in some responses)
│   ├── sector: str = "Financial Services"
│   ├── industry: str
│   ├── price: float | None
│   └── description: str | None
├── evaluation (dict)
│   ├── composite_score: float = 75.4         (0-100)
│   ├── completeness_pct: float = 92.0        (0-100)
│   ├── pillar_scores (dict)                  ← UI uses THIS for headline pillar score
│   │   ├── business_quality: float = 82.5
│   │   ├── operational_health: float = 78.1
│   │   ├── capital_allocation: float = 71.0
│   │   ├── growth_quality: float = 68.6
│   │   └── valuation: float = 76.8
│   └── pillar_breakdowns (dict)              ← per-pillar metric details
│       ├── business_quality (dict)
│       │   ├── score: float = 82.5           (mock only — NOT in real API)
│       │   ├── metrics (dict)                (real API)
│       │   │   ├── gross_margin: float = 0.55
│       │   │   ├── operating_margin: float = 0.28
│       │   │   ├── roic: float = 0.18
│       │   │   ├── fcf_margin: float = 0.22
│       │   │   └── rev_stability: float = 0.85
│       │   ├── scores (dict)                 (real API — per-metric 0-100)
│       │   │   └── <same keys as metrics> : int
│       │   └── components (dict)             (mock only — replaces metrics+scores)
│       │       └── gross_margin: {value: 0.55, score: 88, weight: 0.25}
│       ├── operational_health (dict)         (same shape; e.g. metrics.cash_conversion)
│       ├── capital_allocation (dict)         (e.g. metrics.roic_wacc_spread, metrics.insider_score)
│       ├── growth_quality (dict)
│       └── valuation (dict)
├── breakout (dict)
│   ├── score: float = 64.2                   (0-100)
│   ├── filter_status: str = "eligible"
│   └── components: dict
├── llm_recommendation (dict)                 ← rendered by PDF as "AI Investment Thesis"
│   ├── rating: str = "BUY"
│   ├── conviction: int = 75                  (0-100)
│   ├── summary: str
│   ├── thesis: str                           ← long-form body (PDF renders as paragraph)
│   ├── risks: list[str]
│   └── catalysts: list[str]
├── smart_money (dict)                        ← FETCHED SEPARATELY, NOT in main result
│   ├── insider_activity (dict)
│   │   ├── signal: str = "routine_selling"
│   │   ├── transaction_count: int
│   │   ├── buy_count: int
│   │   ├── sell_count: int
│   │   ├── buy_value: int
│   │   ├── sell_value: int
│   │   ├── net_value: int
│   │   ├── unique_buyers: int
│   │   ├── score: int = 60                   (0-100)
│   │   └── _lookback_days: int
│   ├── institutional_ownership (dict)
│   │   ├── current_pct: float | None
│   │   ├── current_holders: int | None
│   │   ├── trend: str = "no_data"
│   │   └── score: float | None
│   └── _source: str = "fmp"
├── piotroski_f_score (dict, sometimes absent)
│   ├── ok: bool
│   ├── score: int = 7                        (0-9)
│   ├── label: str = "STRONG"|"AVERAGE"|"WEAK"
│   ├── interpretation: str
│   └── error: str | None                     (when ok=False)
├── dcf (dict)
│   ├── ok: bool = True
│   ├── current_price: float = 145.30
│   ├── confidence: str = "HIGH"|"MEDIUM"|"LOW"
│   ├── valuation (dict)
│   │   ├── intrinsic_value_per_share: float
│   │   ├── upside_pct: float
│   │   ├── verdict: str = "UNDERVALUED"|"FAIR"|"OVERVALUED"
│   │   └── equity_value: float
│   ├── inputs (dict)                         (wacc, terminal_growth, …)
│   ├── projections: list[dict]               (per-year fcf projections)
│   ├── caveats: list[str]
│   └── llm_analysis: str | None              (rendered as paragraph by PDF)
├── eva (dict)
│   ├── ok: bool
│   ├── grade: str = "CREATING"|"DESTROYING"|…
│   ├── roic_analysis (dict)                  (roic, roic_pct)
│   ├── wacc (dict)                           (wacc, wacc_pct)
│   ├── eva (dict)                            (value_spread, value_spread_pct, eva_annual, …)
│   ├── implied_valuation (dict)              (per_share, upside_pct)
│   ├── verdict (dict)                        (status, summary)
│   ├── quality (dict)                        (signals: list[{signal, direction}])
│   └── llm_analysis: str | None
├── comps (dict)
│   ├── ok: bool
│   ├── subject (dict)                        (sector)
│   ├── peer_group (dict)
│   │   ├── count: int
│   │   ├── symbols: list[str]
│   │   └── details: list[dict]               (per-peer multiples)
│   ├── multiples_comparison: list
│   ├── fair_value (dict)                     (composite_fair_value, upside_pct)
│   ├── verdict (dict)                        (label, description)
│   ├── confidence (dict)                     (level)
│   └── llm_narrative: str | None
├── entry_analysis (dict)                     ← rendered as "Entry & Price Targets" by PDF
│   ├── ok: bool
│   ├── recommendation: str = "BUY"|"SELL"|"HOLD"
│   ├── conviction: float = 72                (0-100)
│   ├── summary: str
│   ├── composite_score: float = 72           (0-100)
│   ├── current_price: float
│   ├── components (dict)                     ← PDF currently DROPS this (nested)
│   │   ├── technical (dict)
│   │   │   ├── score: float
│   │   │   ├── rsi: float = 58.2
│   │   │   ├── rsi_signal: str = "neutral"|"overbought"|"oversold"
│   │   │   ├── sma_20: float
│   │   │   ├── sma_50: float                 ← UI shows
│   │   │   ├── sma_200: float                ← UI shows
│   │   │   ├── ma_position: str = "above_both"
│   │   │   ├── ma_signal: str = "bullish"    ← UI shows as "Trend"
│   │   │   ├── percentile_52w: float = 0.72  ← UI shows
│   │   │   ├── volume_signal: str
│   │   │   ├── support_level: float
│   │   │   └── resistance_level: float
│   │   ├── market_context (dict)             (regime, spy_rsi, vix)
│   │   └── catalyst (dict)                   (next_earnings, days_to_earnings)
│   ├── suggested_entry: float
│   ├── suggested_stop: float
│   ├── price_target: float
│   ├── risk_reward: str = "1.8:1"
│   ├── signals: list[dict]
│   └── llm_analysis: str | None
├── price_targets (dict)
│   ├── current: float
│   ├── analyst_consensus: float
│   ├── analyst_high: float
│   ├── analyst_low: float
│   ├── analyst_count: int
│   ├── implied_upside_pct: float
│   └── error: str | None
├── raw_financials (dict)
│   ├── fetched_at: str (ISO timestamp)
│   ├── evaluation_version: str
│   ├── sources (dict)
│   │   ├── profile: {provider, endpoint, fetched_at, ok}
│   │   ├── financials: {provider, endpoint, fetched_at, ok}
│   │   └── insider: {provider, endpoint, fetched_at, ok}
│   ├── company_data (dict)
│   │   ├── symbol: str
│   │   └── financials_annual (dict)          ← PDF reads from HERE for statement tables
│   │       ├── symbol: str
│   │       ├── timeframe: str = "annual"
│   │       ├── count: int
│   │       └── statements: list[dict]        ← see financials_shape.md
│   └── computed_inputs (dict)                (per-pillar raw inputs)
└── metadata (dict)
    ├── was_in_universe: bool
    ├── tier_assigned: str = "tier_1_large_mid"
    ├── data_quality: str = "full"|"partial"|"degraded"
    └── errors (dict)
        ├── fetch_errors: list
        ├── missing_data_warnings: list
        └── cross_validation_flags: list
```

## NOT present at top level (despite PDF expecting them)

| Key the PDF reads | Reality |
|-------------------|---------|
| `quality_signals` | **Does not exist.** UI synthesizes the panel from pillar metrics + smart_money + piotroski. |

## Endpoints that contribute to the rendered page (but not to the result dict)

| Endpoint                                              | Frontend usage                        | PDF usage                            |
|-------------------------------------------------------|---------------------------------------|--------------------------------------|
| `/api/company-evaluator/on-demand/jobs/{id}/result`   | Main render                           | Sole source                          |
| `/api/company-evaluator/smart-money/{symbol}`         | Quality Signals "Smart Money" card    | NOT fetched — see bug_list.md BUG #2 |
| `/api/company-evaluator/on-demand/research-prompt/{symbol}` | Research-prompt drawer          | NOT used                             |
