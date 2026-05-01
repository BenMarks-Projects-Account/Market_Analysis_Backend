# CE result variations — partial-result and error shapes

⚠ This is **best-effort from reading frontend guards**; real-data
confirmation requires capturing several CE results in different states
(success, partial, hard error, missing API key, no insider data, …) via
`scripts/dump_ce_result.py`.

## Top-level keys that can be missing or null

Based on guard patterns in `on_demand_evaluator.js`:

| Key                  | Can be missing? | Can be null? | Renderer guard                                                        |
|----------------------|-----------------|--------------|-----------------------------------------------------------------------|
| `company`            | unlikely        | yes          | `data.company \|\| {}`                                                |
| `evaluation`         | yes             | yes          | `data && data.evaluation` (renderPillars hides section if absent)      |
| `evaluation.pillar_scores` | yes       | yes          | `evaluation && evaluation.pillar_scores`                              |
| `evaluation.pillar_breakdowns` | yes   | yes          | `if (!breakdowns) { section.hidden = true; return; }` (L470-L473)     |
| `breakout`           | yes             | yes          | not always rendered                                                   |
| `llm_recommendation` | yes             | yes          | `if (!llmRec)` → "No LLM thesis available"                             |
| `smart_money`        | YES (always missing in main result) | n/a | fetched separately; UI shows loading state then either data or "unavailable" message |
| `piotroski_f_score`  | yes             | yes          | quality-signal card only added if present                             |
| `dcf`                | yes             | yes          | renderer guards on key existence                                      |
| `eva`                | yes             | yes          | guarded                                                               |
| `comps`              | yes             | yes          | guarded                                                               |
| `entry_analysis`     | yes             | yes          | `if (data.entry_analysis && data.entry_analysis.ok) { ... } else { "Not available" }` |
| `price_targets`      | yes             | yes          | `if (!pt \|\| pt.error) { "Not available" }`                          |
| `raw_financials`     | yes             | yes          | statements path is `?.company_data?.financials_annual?.statements`     |
| `metadata`           | unlikely        | yes          | not used by render path                                               |

## Sub-shape variations

### `entry_analysis`

- `ok: false` → UI shows "Not available". Other fields may still be
  present but garbled. **PDF should treat this as no-data and emit
  "Not available" via `fallback_text`.** Currently it would attempt to
  render the noise and likely produce a section with one or two scalar
  rows like `Ok: false`. Consider gating in `_build_document_model`:
  ```python
  ea = ce_result.get("entry_analysis") or {}
  if not ea.get("ok"):
      ea = None
  ```
- `components.technical` may be partially populated (e.g. `rsi` present
  but `sma_200` null because not enough history).

### `dcf` / `eva` / `comps`

- Each has an `ok: bool` flag. `ok: false` typically means upstream
  data was insufficient. The UI gracefully falls back; the PDF
  currently does not check `ok` and will render `Ok: false` as a kv row.
  Cosmetic, not a blocker.

### `price_targets`

- Common variant: `{"error": "FMP API limit"}` or `{"error": "..."}`.
  UI handles via `if (!pt || pt.error)` → "Not available". PDF would
  currently render the error string as a kv row labeled "Error".

### `piotroski_f_score`

- `{ok: true, score: 7, label: "STRONG", interpretation: "..."}`
- `{ok: false, error: "Insufficient historical data"}` — UI shows "N/A".

### `raw_financials.company_data.financials_annual.statements`

- May be empty list `[]` if the financials provider failed.
- May have `count: 0` and statements list missing entirely.
- Per-statement, individual line-item fields can be `null` (e.g.
  `inventory: null` is common for service businesses).

## Recommendation

Once a real result is captured, expand this doc with:

1. A "fully populated" sample (all sections OK).
2. A "degraded" sample where DCF or EVA failed.
3. A "no historical data" sample where `piotroski_f_score.ok = false`.

The PDF should be smoke-tested against each via
`scripts/test_pdf_render.py --from-file <each>.json`.
