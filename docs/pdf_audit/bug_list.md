# BUG LIST — PDF renderer vs. CE result shape

Each bug is keyed by user-visible symptom and lists the exact fix call site.
Line numbers are against `BenTrade/backend/app/services/on_demand_pdf_service.py`
as of the audit (post Phase 2.1).

---

## BUG #1 — Pillar overall score missing from PDF

**Symptom:** PDF "Pillar Breakdown" shows the metric rows (ROIC, Gross Margin,
…) under each pillar heading but no "Business Quality: 76.0" headline number.

**UI reads** (`on_demand_evaluator.js` L834, L850-L851):

```js
var score = evaluation.pillar_scores[key];                    // ← THIS is the headline number
var breakdown = evaluation.pillar_breakdowns[key];
// uses breakdown.metrics  (real API)
// or   breakdown.components (mock / legacy)
```

**PDF reads** (`on_demand_pdf_service.py` L658-L660 in `_render_pillars`):

```python
score = pdata.get("score")        # ← only present in MOCK fixture, NOT real API
if score is not None:
    rows.append(("Score", _fmt_num(score)))
```

**Why it fails on real data:** The real CE API returns scores in a sibling
dict `evaluation.pillar_scores` (a flat `{key: float}` map). The per-pillar
breakdown dict does NOT contain a `score` key in real data; only the mock
fixture happens to put one there. So `pdata.get("score")` is `None` and the
"Score" row is silently skipped.

**Fix:**
- In `_render_pillars`, before the `for pname, pdata in breakdowns.items()` loop:
  ```python
  pillar_scores = evaluation.get("pillar_scores") or {}
  ```
- Replace the existing `score = pdata.get("score")` with:
  ```python
  score = pillar_scores.get(pname)
  if score is None:
      score = pdata.get("score")        # mock-fixture fallback
  ```

**Location:** `on_demand_pdf_service.py` lines **635-666** (function
`_render_pillars`, the inner-loop block right after `_h3(pdf, …)`).

---

## BUG #2 — Quality Signals always "Not available"

**Symptom:** PDF "Quality Signals" section is always literally "Not available"
even when the UI shows a populated panel (Capital Quality / Smart Money /
Predictability / Cash Quality / Piotroski F-Score).

**UI reads** (`on_demand_evaluator.js` L465-L630, function
`renderQualityIndicators`): there is **no `quality_signals` key in the CE
result.** The UI synthesizes the panel client-side from:

| Card label         | Source path                                                                          |
|--------------------|--------------------------------------------------------------------------------------|
| Capital Quality    | `evaluation.pillar_breakdowns.capital_allocation.metrics.roic_wacc_spread`            |
| Smart Money        | `smart_money.insider_activity.score` (fetched separately via `/smart-money/{symbol}`) — fallback `evaluation.pillar_breakdowns.capital_allocation.metrics.insider_score` |
| Predictability     | `evaluation.pillar_breakdowns.business_quality.metrics.rev_stability`                 |
| Cash Quality       | `evaluation.pillar_breakdowns.operational_health.metrics.cash_conversion`             |
| Piotroski F-Score  | `piotroski_f_score` (top-level dict with `ok`, `score`, `label`, `interpretation`)    |

**PDF reads** (`on_demand_pdf_service.py` L319 / L922):

```python
quality_signals=ce_result.get("quality_signals"),    # ← KEY DOES NOT EXIST
…
_render_dict_section(pdf, "Quality Signals", doc.quality_signals)  # → "Not available"
```

**Fix:** Two options. Pick one and stick with it (these are the structural
choices; pick whichever fits Phase 3's scope):

**Option A — match the UI by synthesizing in `_build_document_model`.**
Build a small dict from the same sources the UI uses and stash it in
`doc.quality_signals`:

```python
def _synthesize_quality_signals(ce_result: dict) -> Optional[dict]:
    bk = (ce_result.get("evaluation") or {}).get("pillar_breakdowns") or {}
    def metric(pillar: str, name: str):
        b = bk.get(pillar) or {}
        m = b.get("metrics") or {}
        return m.get(name)
    out: dict[str, Any] = {}
    spread = metric("capital_allocation", "roic_wacc_spread")
    if spread is not None:
        out["capital_quality_spread_pct"] = spread * 100
    stab = metric("business_quality", "rev_stability")
    if stab is not None:
        out["revenue_stability_pct"] = stab * 100
    cc = metric("operational_health", "cash_conversion")
    if cc is not None:
        out["cash_conversion_ratio"] = cc
    sm = (ce_result.get("smart_money") or {}).get("insider_activity") or {}
    if sm.get("score") is not None:
        out["insider_score"] = sm["score"]
    p = ce_result.get("piotroski_f_score")
    if isinstance(p, dict) and p.get("ok") and p.get("score") is not None:
        out["piotroski_f_score"] = f"{p['score']}/9 ({p.get('label','')})"
    return out or None
```
Then: `quality_signals=_synthesize_quality_signals(ce_result),`

**Option B — render the underlying pillar metrics directly under "Quality
Signals" without the UI's level/label rollups.** Smaller code change but
the PDF will look different from the UI.

**Location:**
- Reading: `on_demand_pdf_service.py` line **319** (`_build_document_model`).
- Rendering: line **922** (`_render_pdf`).

⚠ Note: `smart_money` is fetched via a SEPARATE endpoint
(`/api/company-evaluator/smart-money/{symbol}`) on the frontend AFTER the
main result loads. It is NOT included in the CE on-demand job result. If
you want the Smart Money card in the PDF, the PDF service must also fetch
that endpoint or the CE proxy must merge it in. Document this dependency
explicitly when picking Option A.

---

## BUG #3 — Entry & Price Targets renders skeletal scalars only

**Symptom:** PDF "Entry & Price Targets" shows recommendation / conviction /
suggested entry / stop / target / R:R, but is missing the trend / RSI / SMA /
52-week-percentile context the UI shows in the same card.

**UI reads** (`on_demand_evaluator.js` L1537-L1599, function
`renderEntryAndTargets`):

```js
var ea = data.entry_analysis;                      // top-level dict
var tech = ea.components.technical || {};          // ← nested dict
// reads: ea.recommendation, ea.conviction, ea.summary,
//        ea.suggested_entry, ea.suggested_stop, ea.price_target, ea.risk_reward
//        tech.ma_signal, tech.rsi, tech.rsi_signal,
//        tech.sma_50, tech.sma_200, tech.percentile_52w
```

**PDF reads** (`on_demand_pdf_service.py` L928-L932 + `_filter_valuation_fields`
L699-L723):

```python
_render_valuation_section(pdf, "Entry & Price Targets",
    (doc.entry_price_targets or {}).get("entry_analysis") if doc.entry_price_targets else None,
    fallback_text="Not available",
)
# _filter_valuation_fields skips any value that is dict or list:
#     if isinstance(v, (dict, list)): continue
# So `entry_analysis.components` (dict) is dropped entirely.
```

**Why it shows "Not available" in some cases:** if `entry_analysis.ok` is
`False` or the only non-noise scalar fields are absent, the filtered row
list is empty and the `fallback_text="Not available"` path fires.

**Fix:** Flatten `entry_analysis.components.technical` into the section
before filtering. In `_build_document_model`, replace the current
`entry_price_targets` construction with a flattened dict:

```python
ea = ce_result.get("entry_analysis") or {}
ea_tech = (ea.get("components") or {}).get("technical") or {}
flattened_ea = {
    # core scalars (UI shows these prominently)
    "recommendation":   ea.get("recommendation"),
    "conviction":       ea.get("conviction"),
    "summary":          ea.get("summary"),
    "current_price":    ea.get("current_price"),
    "suggested_entry":  ea.get("suggested_entry"),
    "suggested_stop":   ea.get("suggested_stop"),
    "price_target":     ea.get("price_target"),
    "risk_reward":      ea.get("risk_reward"),
    # technical context flattened from components.technical
    "trend":            ea_tech.get("ma_signal"),
    "rsi":              ea_tech.get("rsi"),
    "rsi_signal":       ea_tech.get("rsi_signal"),
    "sma_50":           ea_tech.get("sma_50"),
    "sma_200":          ea_tech.get("sma_200"),
    "percentile_52w":   ea_tech.get("percentile_52w"),
}
entry_price_targets = {
    "entry_analysis": {k: v for k, v in flattened_ea.items() if v is not None} or None,
    "price_targets":  ce_result.get("price_targets"),
}
```

**Location:** `on_demand_pdf_service.py` lines **300-304** (in
`_build_document_model`).

---

## BUG #4 — `_render_dict_section` will still render "Not available"
##           for Quality Signals after Option B above

If you go with Option B for BUG #2 (don't synthesize, just feed pillar
metrics in), be aware that `_render_dict_section` (L606-L632) skips ANY
nested-dict value and renders "(No scalar fields)" if all values were
nested dicts. The pillar `metrics` sub-dicts ARE flat dicts of scalars,
so feeding `evaluation.pillar_breakdowns.business_quality.metrics`
directly will work — but feeding `evaluation.pillar_breakdowns` itself
will not (each value is a dict).

No code change required to fix this; it's a constraint to respect when
choosing what to pass.

---

## BUG #5 (Already fixed but verify) — Statement period headers

**Symptom:** Per Ben, header rows now appear on Income / Balance / Cash
Flow after the recent header-callback commit.

**Verification:** spot-check the post-fix `_statement_table` keeps emitting
the year header on each `add_page` triggered by the auto-page-break inside
the rows loop. The current `_emit_header_row` callback registered as the
page-header looks correct. No bug here per audit; flagged only because
Ben's prompt asked to confirm.

---

## Order to apply

1. **BUG #3** (Entry & Price Targets) — small, isolated, fixes a major
   visible gap.
2. **BUG #1** (Pillar score) — small, isolated.
3. **BUG #2** (Quality Signals) — bigger decision (Option A vs B) and a
   dependency question (Smart Money endpoint). Defer until 1+3 land.

Each fix is independent and can be tested standalone via
`scripts/test_pdf_render.py --from-file docs/pdf_audit/ce_result_sample.json`.
