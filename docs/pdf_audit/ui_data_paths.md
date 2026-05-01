# UI data paths

For each section the user sees in the on-demand evaluator page, this
table records the JS function that renders it and the exact paths it
reads from the `result` object returned by the CE on-demand `/result`
endpoint.

All file:line refs are against
`BenTrade/frontend/assets/js/pages/on_demand_evaluator.js`.

---

## Pillar Breakdown / composite score / per-pillar scores

Renderer: `renderPillars(evaluation)` — L820-L888.

| UI element                   | Source path                                                                                    |
|------------------------------|------------------------------------------------------------------------------------------------|
| Composite score (top of card)| `result.evaluation.composite_score`                                                            |
| Completeness %               | `result.evaluation.completeness_pct`                                                           |
| Per-pillar headline score    | `result.evaluation.pillar_scores[<key>]`     ← real API                                         |
| Pillar metric VALUES         | `result.evaluation.pillar_breakdowns[<key>].metrics[<metric>]`     ← real API                   |
| Pillar metric SCORES (tooltip) | `result.evaluation.pillar_breakdowns[<key>].scores[<metric>]`    ← real API                    |
| Mock fallback for VALUES     | `result.evaluation.pillar_breakdowns[<key>].components[<metric>].value`                         |
| Mock fallback for SCORES     | `result.evaluation.pillar_breakdowns[<key>].components[<metric>].score`                         |

Helper: `_getMetric(pillar, metric)` (L478-L484, also used by quality
indicators) handles the metrics-vs-components shape divergence.

---

## Quality Signals

Renderer: `renderQualityIndicators(data)` — L465-L630.

⚠ **Synthesized client-side; no `quality_signals` key in the CE result.**

| Card               | Source path                                                                            |
|--------------------|----------------------------------------------------------------------------------------|
| Capital Quality    | `result.evaluation.pillar_breakdowns.capital_allocation.metrics.roic_wacc_spread`       |
| Smart Money        | `result.smart_money.insider_activity.score`  (separately fetched) — falls back to `result.evaluation.pillar_breakdowns.capital_allocation.metrics.insider_score` |
| Predictability     | `result.evaluation.pillar_breakdowns.business_quality.metrics.rev_stability`            |
| Cash Quality       | `result.evaluation.pillar_breakdowns.operational_health.metrics.cash_conversion`        |
| Piotroski F-Score  | `result.piotroski_f_score.{ok, score, label, interpretation, error}`                    |

`smart_money` arrives via a separate call to
`/api/company-evaluator/smart-money/{symbol}` — see `fetchAndRenderSmartMoney`
at L897-L920.

---

## Entry & Price Targets

Renderer: `renderEntryAndTargets(data)` — L1531-L1629.

### Technical Entry card

| UI element                  | Source path                                                                  |
|-----------------------------|------------------------------------------------------------------------------|
| Signal badge (BUY/SELL/HOLD)| `result.entry_analysis.recommendation` + `result.entry_analysis.conviction`   |
| Suggested Entry callout     | `result.entry_analysis.suggested_entry`                                       |
| Stop                        | `result.entry_analysis.suggested_stop`                                        |
| Target                      | `result.entry_analysis.price_target`                                          |
| R / R                       | `result.entry_analysis.risk_reward`                                           |
| Trend                       | `result.entry_analysis.components.technical.ma_signal`                        |
| RSI                         | `result.entry_analysis.components.technical.rsi` + `.rsi_signal`              |
| SMA50                       | `result.entry_analysis.components.technical.sma_50`                           |
| SMA200                      | `result.entry_analysis.components.technical.sma_200`                          |
| 52w percentile              | `result.entry_analysis.components.technical.percentile_52w`                   |
| Summary narrative           | `result.entry_analysis.summary`                                               |

Guard: `data.entry_analysis && data.entry_analysis.ok` (L1537). Falsy
ok → "Not available" path.

### Analyst Price Targets card

| UI element        | Source path                                  |
|-------------------|----------------------------------------------|
| Big consensus #   | `result.price_targets.analyst_consensus`     |
| Current           | `result.price_targets.current`               |
| High              | `result.price_targets.analyst_high`          |
| Low               | `result.price_targets.analyst_low`           |
| Analysts count    | `result.price_targets.analyst_count`         |
| Implied Upside    | `result.price_targets.implied_upside_pct`    |
| Error fallback    | `result.price_targets.error`                 |

---

## DCF Valuation

Renderer: `renderDcf(dcf)` — search the file for the section but the
read paths used are:

| UI element                       | Source path                                          |
|----------------------------------|------------------------------------------------------|
| Verdict                          | `result.dcf.valuation.verdict`                        |
| Intrinsic value / share          | `result.dcf.valuation.intrinsic_value_per_share`      |
| Upside %                         | `result.dcf.valuation.upside_pct`                     |
| Confidence                       | `result.dcf.confidence`                               |
| WACC / terminal growth / inputs  | `result.dcf.inputs.{wacc, terminal_growth, …}`        |
| Caveats list                     | `result.dcf.caveats`                                  |
| Model analysis paragraph         | `result.dcf.llm_analysis`                             |

---

## EVA Valuation

| UI element        | Source path                                |
|-------------------|--------------------------------------------|
| Grade             | `result.eva.grade`                          |
| ROIC %            | `result.eva.roic_analysis.roic_pct`         |
| WACC %            | `result.eva.wacc.wacc_pct`                  |
| Value spread %    | `result.eva.eva.value_spread_pct`           |
| EVA annual        | `result.eva.eva.eva_annual`                 |
| Per-share implied | `result.eva.implied_valuation.per_share`    |
| Verdict summary   | `result.eva.verdict.summary`                |
| Quality signals   | `result.eva.quality.signals[]`              |
| Model analysis    | `result.eva.llm_analysis`                   |

---

## Comparable Companies

| UI element            | Source path                                  |
|-----------------------|----------------------------------------------|
| Peer count + symbols  | `result.comps.peer_group.{count, symbols}`   |
| Per-peer multiples    | `result.comps.peer_group.details[*]`         |
| Fair value composite  | `result.comps.fair_value.composite_fair_value` |
| Upside %              | `result.comps.fair_value.upside_pct`         |
| Verdict               | `result.comps.verdict.{label, description}`  |
| Confidence            | `result.comps.confidence.level`              |
| LLM narrative         | `result.comps.llm_narrative`                 |

---

## AI Investment Thesis / LLM Recommendation

Renderer: `renderThesis(llmRec)` — L1640-L1700.

| UI element  | Source path                                   |
|-------------|-----------------------------------------------|
| Summary     | `result.llm_recommendation.summary`           |
| Full Thesis | `result.llm_recommendation.thesis`            |
| Catalysts   | `result.llm_recommendation.catalysts[]`       |
| Risks       | `result.llm_recommendation.risks[]`           |

Header chips:

| UI element  | Source path                                   |
|-------------|-----------------------------------------------|
| Rating chip | `result.llm_recommendation.rating`            |
| Conviction  | `result.llm_recommendation.conviction`        |

---

## Financial Statements

The UI reads the same flat per-period rows the PDF reads:

`result.raw_financials.company_data.financials_annual.statements[]`

Each statement has both period metadata (`fiscal_year`, `fiscal_period`,
`period`, `end_date`) and the line-item fields. See
[`financials_shape.md`](./financials_shape.md).
