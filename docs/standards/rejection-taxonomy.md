# Rejection Reason Taxonomy

> **Status:** Authoritative standard — reason codes are stable identifiers.

---

## Principles

- All reason codes are **snake_case**.
- **Never rename** an existing code. If a code becomes obsolete, deprecate it (stop emitting) but keep it in this list.
- To cover a new rejection scenario, **add a new code** — do not repurpose an existing one.
- Every rejected candidate in a scanner run must map to exactly one reason code from this list.
- If the UI currently displays a human-readable label for a rejection, map it to the corresponding code below. **Do not introduce new display strings that bypass this taxonomy.**

---

## Threshold Reasons (category: `threshold`)

These indicate the candidate had valid data but failed a quality/strategy gate.
The codes below are already used in the BenTrade UI and are the canonical set.

| Code | Definition | Controlled By |
|---|---|---|
| `ev_to_risk_below_floor` | EV / max-risk ratio is below the preset minimum. | `min_ev_to_risk` |
| `open_interest_below_min` | Open interest on a required leg is below the preset minimum. | `min_open_interest` |
| `spread_too_wide` | Bid-ask spread percentage exceeds the preset maximum. | `max_bid_ask_spread_pct` |
| `volume_below_min` | Volume on a required leg is below the preset minimum. | `min_volume` |
| `ror_below_floor` | Return on risk is below the minimum threshold. | `min_ror` |
| `non_positive_credit` | Net credit is ≤ 0 when a positive credit is required. | `min_credit` (must be > 0) |
| `ev_negative` | Expected value is below the negative tolerance (e.g., < −0.05). | — (hardcoded) |
| `pop_below_floor` | Probability of profit is below the preset minimum. | `min_pop` |
| `dte_out_of_range` | Days to expiration is outside the [dte_min, dte_max] window. | `dte_min`, `dte_max` |
| `distance_out_of_range` | OTM distance is outside the [distance_min, distance_max] window. | `distance_min`, `distance_max` |
| `width_out_of_range` | Spread width is outside the [width_min, width_max] window. | `width_min`, `width_max` |
| `debit_pct_too_high` | Debit as percentage of width exceeds the preset max (debit spreads). | `max_debit_pct_width` |
| `iv_rv_ratio_too_high` | IV/RV ratio exceeds the preset max (debit spreads — buying overpriced vol). | `max_iv_rv_ratio_for_buying` |
| `credit_below_floor` | Net credit per contract is below the preset minimum. | `min_credit` |
| `kelly_negative` | Kelly fraction is negative (negative edge). | — (hardcoded) |
| `credit_ge_width` | Net credit ≥ width (degenerate spread). | — (structural) |
| `invalid_width` | Spread width is zero or negative. | — (structural) |
| `v2_deep_itm_long_leg` | Long leg delta > 0.85 on a debit spread (deep ITM inflates POP). | — (hardcoded 0.85) |
| `v2_credit_spread_no_credit` | Credit strategy produces no actual credit after bid/ask recomputation (Phase E). | — (structural) |
| `v2_wide_spread_short_leg` | Short leg bid-ask spread > 20% on a credit strategy (unreliable fill). | — (hardcoded 0.20) |

---

## Data-Quality Reasons (category: `data_quality`)

These indicate the candidate was rejected because required market data was missing, stale, or structurally invalid. Candidates failing data-quality checks are rejected in the `validate_quotes` stage before any threshold gate runs.

**Composite rejection codes in filter trace:**
In the scanner's `rejection_reason_counts`, data-quality rejections for OI/volume use the composite form `DQ_MISSING:<field>` or `DQ_ZERO:<field>` (e.g., `DQ_MISSING:open_interest`, `DQ_ZERO:volume`). These map to the canonical codes below. The composite form preserves gate-level context (the "DQ_" prefix routes them to the `data_quality` gate in the gate breakdown).

| Code | Definition |
|---|---|
| `invalid_quote` | Bid or ask is structurally invalid (negative, null, or otherwise unusable) on a required leg. |
| `missing_quote` | No quote data returned at all for a required leg. |
| `inverted_market` | Ask price is less than bid price on a required leg. |
| `zero_mid` | Mid price computes to zero (both bid and ask are zero or missing). |
| `missing_open_interest` | Open interest is null/absent on a required leg. |
| `missing_volume` | Volume is null/absent on a required leg. |
| `zero_open_interest` | Open interest is explicitly 0 (exchange reported no activity). |
| `zero_volume` | Volume is explicitly 0 (exchange reported no activity). |
| `missing_bid_ask` | Both bid and ask are null/absent on a required leg. |
| `stale_quote` | Quote timestamp is older than the staleness threshold for the scan. |
| `missing_iv` | Implied volatility is null/absent when required. |
| `missing_delta` | Delta is null/absent when required for POP estimation. |
| `zero_bid_short_leg` | Short leg has bid=0 on a credit strategy — no premium collectible. |
| `wide_spread_short_leg` | Short leg bid-ask spread > 20% on a credit strategy — unreliable fill pricing. |

---

## Adding New Codes

1. Choose a descriptive snake_case name.
2. Add it to the appropriate section (`threshold` or `data_quality`) in this file with a 1-line definition.
3. Implement the code in the scanner plugin's evaluate/gate logic.
4. Ensure it appears in `rejection_reason_counts` in the filter trace.
5. If a UI label is needed, create a display mapping — never use the raw label as a reason code.

---

## Cross-References

- Scanner contract: [docs/standards/scanner-contract.md](scanner-contract.md)
- Data quality rules: [docs/standards/data-quality-rules.md](data-quality-rules.md)
- Preset knob → reason code mapping: [docs/standards/presets.md](presets.md)
