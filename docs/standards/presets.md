# Scanner Presets

> **Status:** Authoritative standard — preset philosophy and expectations.

---

## Philosophy

BenTrade scanners support tiered filter profiles so users can control the trade-off between **quality** and **candidate volume**.

| Preset | Intent |
|---|---|
| **Strict** | Tightest filters. Fewest results, highest average quality. Suitable for low-volatility, high-confidence environments. |
| **Balanced** | Reasonable volume with conservative quality floors. The default for most scans. |
| **Wide** | Maximum discovery. Relaxed filters to surface candidates that tighter presets would exclude. Useful for research or volatile markets. |

> Additional levels (e.g., `Conservative`) may exist between Strict and Balanced. The three above are the minimum required set.

---

## Knobs That Must Differ Across Presets

Each preset MUST resolve to **meaningfully different numeric thresholds** for at least these parameters:

| Knob | Key(s) | What Changes |
|---|---|---|
| EV / Risk floor | `min_ev_to_risk` | Strict is highest, Wide is lowest. |
| Spread max % | `max_bid_ask_spread_pct` | Strict is tightest, Wide allows wider spreads. |
| Min open interest | `min_open_interest` | Strict requires deepest liquidity. |
| Min volume | `min_volume` | Same direction as OI. |
| Min credit | `min_credit` (if applicable) | Strict may set a higher floor. |
| DTE window | `dte_min`, `dte_max` | Strict is narrower, Wide is broader. |
| Width | `width_min`, `width_max` | May vary by strategy. |
| OTM distance | `distance_min`, `distance_max` | Strict typically tighter band. |
| Min POP | `min_pop` | Strict is highest, Wide is lowest. |
| Max candidates | `max_candidates` | Controls candidate construction cap. Wide is largest (800), Strict is smallest (200). Prevents over-pruning before evaluate gates run. |

---

## Verification Rule

- Presets MUST be **verifiable via filter trace**. After a scan, the `resolved_thresholds` in the trace must show the actual numeric values used, and they must match the preset definition.
- If two presets produce identical `resolved_thresholds`, that is a bug.
- Preset resolution MUST be centralized in one function/module — no scattered defaults across files.

---

## Preset Resolution Flow

```
User selects level (e.g., "Balanced")
       │
       ▼
Profile lookup (profiles.js or equivalent)
       │
       ▼
Payload merge (profile values → scanner request)
       │
       ▼
_apply_request_defaults() uses setdefault()
       │  (profile keys in payload always win)
       ▼
Plugin reads final values → recorded in filter_trace.resolved_thresholds
```

---

## Knob → Rejection Reason Code Mapping

Each preset knob directly controls a specific rejection reason code from the [rejection taxonomy](rejection-taxonomy.md). When a candidate fails a threshold gate, the reason code emitted must be the one listed here.

| Knob | Rejection Reason Code | Rule |
|---|---|---|
| `min_open_interest` | `open_interest_below_min` | OI on required leg < threshold |
| `min_volume` | `volume_below_min` | Volume on required leg < threshold |
| `max_bid_ask_spread_pct` | `spread_too_wide` | Bid-ask spread % > threshold |
| `min_ev_to_risk` | `ev_to_risk_below_floor` | EV/risk ratio < threshold |
| `min_ror` | `ror_below_floor` | Return on risk < threshold |
| `min_credit` (must be > 0) | `non_positive_credit` | Net credit ≤ 0 |
| `min_pop` | `pop_below_floor` | POP < threshold |

> **Differentiation rule:** Strict, Balanced, and Wide MUST produce different `resolved_thresholds` values for **at least 3** of the knobs above. If two presets share the same value for all listed knobs, that is a bug.

---

## Cross-References

- Profile tables with exact values: [docs/scanners/global-profiles.md](../scanners/global-profiles.md)
- Filter parameter inventory: [docs/scanners/filter-parameter-inventory.md](../scanners/filter-parameter-inventory.md)
- Scanner contract (trace schema): [docs/standards/scanner-contract.md](scanner-contract.md)
- Rejection taxonomy: [docs/standards/rejection-taxonomy.md](rejection-taxonomy.md)
