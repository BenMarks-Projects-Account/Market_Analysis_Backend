# PDF Renderer Data-Shape Audit

Diagnostic-only audit of `BenTrade/backend/app/services/on_demand_pdf_service.py`
versus the actual CE on-demand result shape consumed by
`BenTrade/frontend/assets/js/pages/on_demand_evaluator.js`.

**No fixes were applied in this pass.** The output is reference material for
a follow-up surgical fix prompt.

## Status of each task

| Task | Deliverable | Status |
|------|-------------|--------|
| 1. Capture real CE result | [`ce_result_sample.json`](./ce_result_sample.json) | **PARTIAL** — backend was not running at audit time. Used the frontend mock fixture (`on_demand_evaluator.js` ~L1980) as the structural reference and shipped [`scripts/dump_ce_result.py`](../../scripts/dump_ce_result.py) for you to run when backend is up. |
| 2. Key-tree summary       | [`ce_result_key_tree.md`](./ce_result_key_tree.md) | Complete (built from mock fixture + real-API hints in `renderPillars()` + `renderQualityIndicators()`) |
| 3. UI data paths          | [`ui_data_paths.md`](./ui_data_paths.md) | Complete |
| 4. PDF data paths         | [`pdf_data_paths.md`](./pdf_data_paths.md) | Complete |
| 5. Bug list (the actionable output) | [`bug_list.md`](./bug_list.md) | **Complete — start here** |
| 6. Financials shape       | [`financials_shape.md`](./financials_shape.md) | Complete |
| 7. Scoring system         | [`scoring_system.md`](./scoring_system.md) | Complete |
| 8. CE result variations   | [`ce_result_variations.md`](./ce_result_variations.md) | Partial (best-effort from code; needs real samples to confirm) |
| 9. Test render harness    | [`scripts/test_pdf_render.py`](../../scripts/test_pdf_render.py) | Complete |

## How to capture a real CE result and validate fixes

```powershell
# 1. Start the BenTrade backend (Flask on :5000) however you normally do.
# 2. From the repo root:

# Capture a real result for an existing job:
python scripts/dump_ce_result.py --job-id ondemand_2026-04-15T00:11:53_MSFT_88a4 `
    --out docs/pdf_audit/ce_result_sample.json

# Render a PDF from a saved snapshot (no Flask hop required):
python scripts/test_pdf_render.py --job-id MSFT --from-file docs/pdf_audit/ce_result_sample.json `
    --pdf-out C:/tmp/test_render.pdf --model-out C:/tmp/test_render_model.json

# The DocumentModel JSON shows exactly what the renderer "saw" — diff it
# against the source CE JSON to confirm a fix actually changed the model.
```

## Most important finding (TL;DR)

The Quality Signals section is rendering "Not available" because **`quality_signals`
is not a top-level key in the CE result at all.** The frontend
`renderQualityIndicators()` (`on_demand_evaluator.js` L465-L630) **synthesizes**
that panel client-side from:

- `evaluation.pillar_breakdowns.capital_allocation.metrics.roic_wacc_spread`
- `evaluation.pillar_breakdowns.business_quality.metrics.rev_stability`
- `evaluation.pillar_breakdowns.operational_health.metrics.cash_conversion`
- `smart_money.insider_activity.score` (fetched from a separate endpoint)
- `piotroski_f_score` (top-level, sometimes present)

The PDF reads `ce_result.get("quality_signals")` → always `None` → "Not
available". See [`bug_list.md` BUG #2](./bug_list.md).

The other big findings:

- **Pillar overall score** (e.g. "Business Quality: 76.0") lives at
  `evaluation.pillar_scores[<key>]`, NOT `pillar_breakdowns[<key>].score`.
  Real-API breakdowns may not have a top-level `score` field at all
  (mock has it for convenience but `renderPillars` never reads it).
- **Entry & Price Targets** PDF reads scalar fields fine, but the rich
  technical context the UI shows (RSI, SMA50, SMA200, 52w pctl, trend)
  lives in `entry_analysis.components.technical.*` — a nested dict that
  `_filter_valuation_fields` deliberately skips.
- **Financial statements** field names + period keys match between the
  fixture and the PDF constants. The recent header-fix commit is correct.
