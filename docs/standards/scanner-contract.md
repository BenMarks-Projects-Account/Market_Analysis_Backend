# Scanner Contract

> **Status:** Authoritative standard — all scanner work must conform.

---

## 1. Required Scanner Output Fields

Every scanner run MUST return an object containing at minimum:

| Field | Type | Description |
|---|---|---|
| `accepted_trades` | `list[TradeDTO]` | Trades that passed all filter stages |
| `candidate_count` | `int` | Total candidates constructed before filtering |
| `accepted_count` | `int` | Number of trades that survived all gates |
| `preset_used` | `string` | Profile level applied (`strict`, `balanced`, `wide`, etc.) |
| `timestamp` | `ISO 8601 string` | When the scan completed |
| `filter_trace` | `object` | Full trace object (see §2) |

---

## 2. Required Filter Trace Schema

The `filter_trace` object is **mandatory**. It provides full explainability for every scan.

**Reason codes MUST come from [docs/standards/rejection-taxonomy.md](rejection-taxonomy.md). Do not invent new codes without updating the taxonomy first.**

```jsonc
{
  "preset_name": "balanced",              // profile level used
  "resolved_thresholds": {                // final numeric values after preset resolution
    "min_pop": 0.60,
    "min_ev_to_risk": 0.02,
    "max_bid_ask_spread_pct": 1.5,
    "min_open_interest": 300,
    "min_volume": 20,
    "min_credit": null,                   // null if not applicable
    "min_ror": 0.01,
    "dte_min": 7,
    "dte_max": 45,
    "width_min": 1,
    "width_max": 5,
    "distance_min": 0.01,
    "distance_max": 0.12
  },
  "stage_counts": [                       // ordered — one entry per filter stage
    { "stage": "candidates_built",      "remaining": 420 },
    { "stage": "validate_quotes",       "remaining": 390 },  // ← data-quality gate
    { "stage": "liquidity_gate",        "remaining": 310 },
    { "stage": "spread_gate",           "remaining": 280 },
    { "stage": "ev_pop_gate",           "remaining": 95 },
    { "stage": "final_scoring",         "remaining": 40 }
  ],
  "rejection_reason_counts": {            // keyed by canonical codes from rejection-taxonomy.md
    // data_quality codes (from validate_quotes stage)
    "invalid_quote": 10,
    "missing_quote": 3,
    "inverted_market": 2,
    "zero_mid": 1,
    "missing_open_interest": 8,
    "missing_volume": 6,
    // threshold codes (from liquidity/spread/ev gates)
    "open_interest_below_min": 50,
    "volume_below_min": 30,
    "spread_too_wide": 30,
    "non_positive_credit": 5,
    "ev_to_risk_below_floor": 120,
    "ror_below_floor": 15,
    "pop_below_floor": 70
    // ... every rejected candidate maps to exactly one code
  },
  "data_quality_counts": {                // aggregated data-quality outcomes
    "invalid_quote": 10,
    "missing_quote": 3,
    "inverted_market": 2,
    "zero_mid": 1,
    "missing_open_interest": 8,
    "missing_volume": 6,
    "missing_bid_ask": 2,
    "stale_quote": 0,
    "missing_iv": 1,
    "missing_delta": 0,
    "total_invalid": 33                   // sum of all data-quality rejections
  }
}
```

---

## 3. Filter Stage Ordering

Stages MUST execute in this order:

1. **`candidates_built`** — build pairs/combos from the chain.
2. **`validate_quotes`** — reject candidates with missing/invalid quotes, inverted markets, zero mid, stale data. This stage quantifies all `data_quality` reason codes. It runs _before_ any threshold gate.
3. **`liquidity_gate`** — OI and volume minimums (`open_interest_below_min`, `volume_below_min`).
4. **`spread_gate`** — bid-ask spread percentage (`spread_too_wide`).
5. **`ev_pop_gate`** — EV/risk, POP, RoR, credit thresholds (`ev_to_risk_below_floor`, `pop_below_floor`, `ror_below_floor`, `non_positive_credit`, etc.).
6. **`final_scoring`** — composite score, top-N selection.

> **Rationale:** Quote integrity must be validated _before_ liquidity and EV gates, because those gates assume valid pricing inputs.

---

## 4. "No Silent Drops" Rule

- Every candidate that is rejected MUST be counted under a reason code in `rejection_reason_counts`.
- The sum of all rejection counts + `accepted_count` MUST equal `candidate_count`.
- If a candidate fails multiple gates, attribute it to the **first** gate it fails (per the ordered stages above).
- Reason codes must come from the stable taxonomy in [rejection-taxonomy.md](rejection-taxonomy.md). Never invent ad-hoc reason strings.

---

## 5. Cross-References

- Rejection reason codes: [docs/standards/rejection-taxonomy.md](rejection-taxonomy.md)
- Preset definitions: [docs/standards/presets.md](presets.md)
- Data quality rules: [docs/standards/data-quality-rules.md](data-quality-rules.md)
- Canonical trade shape: [docs/standards/canonical-contract.md](canonical-contract.md)
