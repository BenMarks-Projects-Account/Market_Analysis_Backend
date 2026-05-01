# Scoring System — where do scores live in the CE result?

Source-of-truth references:
- Frontend `on_demand_evaluator.js` L820-L888 (`renderPillars`)
- Frontend mock fixture `on_demand_evaluator.js` L1980-L2010 (real-API-shaped)
- Frontend `_getMetric` helper L478-L484 (handles both real + mock shapes)

## 1. Overall composite score

```
ce_result.evaluation.composite_score      type: float, scale: 0–100
```

UI displays it as the big number at the top of the report, formatted
with one decimal (e.g. "75.4"). PDF currently reads this correctly
(`_render_pillars` L640-L644).

## 2. Completeness percentage

```
ce_result.evaluation.completeness_pct     type: float, scale: 0–100
```

PDF currently reads this correctly.

## 3. Per-pillar overall scores

```
ce_result.evaluation.pillar_scores : dict[str, float]   scale: 0–100
```

This is the **dict the UI uses** for the headline pillar number
(`evaluation.pillar_scores[key]`, `on_demand_evaluator.js` L834). Always
present in real API responses. **PDF currently does NOT read this** — it
looks at `breakdown.score` which only exists in the mock fixture.

Pillar key names that exist in real API (per the UI's hardcoded
`pillarOrder` array, L824-L830):

| Key                  | UI label             |
|----------------------|----------------------|
| `business_quality`   | Business Quality     |
| `operational_health` | Operational Health   |
| `capital_allocation` | Capital Allocation   |
| `growth_quality`     | Growth Quality       |
| `valuation`          | Valuation            |

## 4. Per-pillar metric breakdowns

```
ce_result.evaluation.pillar_breakdowns : dict[str, PillarBreakdown]
```

Each `PillarBreakdown` has TWO different shapes, handled by the UI's
`_getMetric` helper:

### Real API shape

```
pillar_breakdowns.business_quality = {
  "metrics": {                          # raw values
    "gross_margin": 0.55,
    "operating_margin": 0.28,
    "roic": 0.18,
    "fcf_margin": 0.22,
    "rev_stability": 0.85
  },
  "scores": {                           # 0-100 per metric
    "gross_margin": 88,
    "operating_margin": 85,
    "roic": 82,
    "fcf_margin": 75,
    "rev_stability": 78
  }
}
```

UI reads `breakdown.metrics[name]` for the value and
`breakdown.scores[name]` for the per-metric 0-100 score (used in
tooltips: `title="Score: 82/100"`).

PDF currently reads `pdata.get("metrics")` correctly but ignores
`pdata.get("scores")` entirely. That's not necessarily a bug — the PDF
chooses to show only values, not per-metric scores.

### Mock / legacy shape

```
pillar_breakdowns.business_quality = {
  "score": 82.5,                        # ← the headline pillar number
  "components": {
    "gross_margin":   { "value": 0.55, "score": 88, "weight": 0.25 },
    "operating_margin": { ... }
  }
}
```

PDF currently relies on `pdata.get("score")` from this shape, which is why
the headline pillar number works on the mock fixture but vanishes on real
API data. See [`bug_list.md` BUG #1](./bug_list.md).

## 5. Other top-level scores in the CE result

| Path                                  | Type / scale                | Notes                                       |
|---------------------------------------|-----------------------------|---------------------------------------------|
| `breakout.score`                      | float 0–100                 | Not currently rendered in PDF.              |
| `entry_analysis.composite_score`      | float 0–100                 | PDF currently passes through filter.        |
| `entry_analysis.conviction`           | float 0–100                 | PDF renders.                                |
| `entry_analysis.components.technical.score` | float 0–100           | Nested → currently dropped by PDF filter.   |
| `dcf.confidence`                      | str ("HIGH"/"MEDIUM"/"LOW") | PDF renders as scalar.                      |
| `eva.grade`                           | str ("CREATING"/"DESTROYING"/…) | PDF renders.                            |
| `comps.confidence.level`              | str                          | Nested → dropped by PDF filter.             |
| `llm_recommendation.conviction`       | float 0–100                 | PDF renders via `_render_ai_thesis`.         |
| `piotroski_f_score.score`             | int 0–9                     | NOT in PDF — see [`bug_list.md` BUG #2](./bug_list.md). |
| `smart_money.insider_activity.score`  | float 0–100                 | NOT in CE result; fetched separately.       |

## 6. Verdict on the user's question

The user asked: *"What is the pillar's own score called? `score`? `weighted_score`? `composite`? `total`?"*

**Answer:** None of the above as a field on the breakdown dict in real API.
The pillar's overall score is in a SIBLING dict at
`evaluation.pillar_scores[<pillar_key>]`. The PDF must read from there,
not from `breakdown.score`. Mock data has `breakdown.score` for backwards
compatibility but the UI's `renderPillars` ignores it and uses
`pillar_scores` exclusively.
