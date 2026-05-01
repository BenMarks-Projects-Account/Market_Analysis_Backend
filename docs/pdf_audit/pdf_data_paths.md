# PDF data paths (current, pre-fix)

Mirrors the layout of [`ui_data_paths.md`](./ui_data_paths.md). All
file:line refs are against
`BenTrade/backend/app/services/on_demand_pdf_service.py`.

Renderer entrypoint: `_render_pdf(doc)` — L919-L946 — invokes section
renderers in fixed order: header → chart → quality_signals → dcf → eva →
comps → pillars → entry & price targets → financials → ai_thesis → appended → notes.

DocumentModel construction: `_build_document_model(ce_result, payload)` —
L244-L312.

---

## Pillar Breakdown

Renderer: `_render_pillars(pdf, evaluation)` — L635-L666.

| DocumentModel field / row    | Python read path                                                              | Issue?                                                              |
|------------------------------|-------------------------------------------------------------------------------|---------------------------------------------------------------------|
| `doc.pillar_breakdown`       | `ce_result.get("evaluation")` (L319)                                          | OK                                                                  |
| Composite Score              | `evaluation.get("composite_score")`                                           | OK                                                                  |
| Completeness %               | `evaluation.get("completeness_pct")`                                          | OK                                                                  |
| Per-pillar Score row         | `pdata.get("score")` (L658)                                                   | **BUG #1** — real API stores it at `evaluation["pillar_scores"][pname]` |
| Per-pillar metric rows       | `pdata.get("metrics")` (L661) → iter scalars                                  | OK for real API; misses `pdata["components"][m]["value"]` for mock  |

---

## Quality Signals

Renderer: `_render_dict_section(pdf, "Quality Signals", doc.quality_signals)` — L606-L632, called at L922.

| DocumentModel field    | Python read path                          | Issue?                                                                 |
|------------------------|-------------------------------------------|------------------------------------------------------------------------|
| `doc.quality_signals`  | `ce_result.get("quality_signals")` (L319) | **BUG #2** — key does not exist; UI synthesizes from pillar metrics + smart_money + piotroski. Always renders "Not available". |

---

## Entry & Price Targets

Renderer: `_render_valuation_section(..., (doc.entry_price_targets or {}).get("entry_analysis"), fallback_text="Not available")` — L928-L932.

| DocumentModel field             | Python read path                                                                              | Issue?                                                       |
|---------------------------------|-----------------------------------------------------------------------------------------------|--------------------------------------------------------------|
| `doc.entry_price_targets`       | `{"entry_analysis": ce_result.get("entry_analysis"), "price_targets": ce_result.get("price_targets")}` (L300-L304) | OK structurally                                              |
| Entry recommendation, conviction, summary, current_price, suggested_entry, suggested_stop, price_target, risk_reward | top-level scalars on `entry_analysis` → survive `_filter_valuation_fields` (L699-L723) | OK                                                           |
| Entry composite_score, signals  | `entry_analysis.composite_score` (scalar) survives; `signals` (list) skipped                  | OK                                                           |
| **Trend / RSI / SMA50 / SMA200 / 52w pctl** | `entry_analysis.components.technical.*` — `components` is a dict → `_filter_valuation_fields` skips it (L713) | **BUG #3** — the rich technical context never appears in the PDF |
| Price Targets sub-table         | `doc.entry_price_targets["price_targets"].items()` filtered to scalars (L935-L941)            | OK                                                           |

---

## DCF Valuation

Renderer: `_render_valuation_section(pdf, "DCF Valuation", doc.dcf)` — L923.

| DocumentModel field | Python read path                  | Issue?                                                              |
|---------------------|-----------------------------------|---------------------------------------------------------------------|
| `doc.dcf`           | `ce_result.get("dcf")` (L321)     | OK                                                                  |
| Top-level scalars   | iterated via `_filter_valuation_fields`; non-noise scalars rendered | OK — `current_price`, `confidence` survive                |
| **Nested dicts** (`valuation`, `inputs`, `projections`, `caveats`) | dropped by `isinstance(v, (dict, list))` skip | KNOWN LIMITATION — not flagged by Ben so not a bug for this pass    |
| Model analysis para | `dcf.llm_analysis` if non-empty   | OK (rendered as "Model Analysis" h3 below the kv table)             |

The PDF therefore shows DCF only as: `current_price`, `confidence`, plus
the LLM analysis paragraph. The intrinsic value / upside / verdict /
inputs are silently dropped because they live in nested dicts.

This may itself be a bug worth filing in a future pass — but Ben's
prompt only flagged Pillar Breakdown / Quality Signals / Entry & Price
Targets, so it's noted here as a constraint, not in the bug list.

---

## EVA Valuation

Same renderer / same constraint as DCF. Visible scalars: `ok`, `grade`.
Nested `roic_analysis`, `wacc`, `eva`, `implied_valuation`, `verdict`,
`quality` all dropped.

---

## Comparable Companies

Same renderer / same constraint. Visible scalars: `ok`. All nested
sub-dicts (`subject`, `peer_group`, `fair_value`, `verdict`,
`confidence`) dropped.

---

## AI Investment Thesis

Renderer: `_render_ai_thesis(pdf, doc.ai_thesis)` — L859-L915, called at L943.

| DocumentModel field   | Python read path                              | Issue?                                                                    |
|-----------------------|-----------------------------------------------|---------------------------------------------------------------------------|
| `doc.ai_thesis`       | `ce_result.get("llm_recommendation")` (L327)  | OK                                                                        |
| Long body             | tries `thesis`, `thesis_text`, `narrative`, `rationale` in order; first non-empty string wins | OK                                                                        |
| KV scalars            | iterated, skipping the body keys + `VALUATION_NOISE_FIELDS` + nested types | OK                                                                        |
| Catalysts / Risks lists | `thesis.get("catalysts")` / `risks` — both are lists, dropped | The UI shows these — could be a follow-up bug, not in Ben's list this pass |

---

## Financial Statements

Renderer: `_render_financials(pdf, fs)` — L771-L791, called at L942.

| DocumentModel field        | Python read path                                                                       | Issue? |
|----------------------------|----------------------------------------------------------------------------------------|--------|
| `fs.income_statement.rows` | `_partition_statements(statements, INCOME_STATEMENT_FIELDS)` (L283)                    | OK     |
| `fs.balance_sheet.rows`    | `_partition_statements(statements, BALANCE_SHEET_FIELDS)` (L284)                       | OK     |
| `fs.cash_flow.rows`        | `_partition_statements(statements, CASH_FLOW_FIELDS)` (L285)                           | OK     |
| `statements` source        | `ce_result["raw_financials"]["company_data"]["financials_annual"]["statements"]` (L271-L274) | OK     |
| Period header label        | `fiscal_year` → parsed-year fallback → raw period/end_date → "—" (post Phase 2.1)      | OK (recently fixed) |

See [`financials_shape.md`](./financials_shape.md) for the per-statement
key-by-key audit.
