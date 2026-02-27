# Data Quality Rules

> **Status:** Authoritative standard — data integrity is the top priority.

---

## 1. Quote Integrity Rules

- **Bid must be ≥ 0.** A negative bid is invalid.
- **Ask must be > 0.** A zero or negative ask is invalid.
- **Ask must be ≥ Bid.** If ask < bid, the quote is invalid.
- **Mid = (bid + ask) / 2.** Mid is derived, never sourced independently. If either bid or ask is missing, mid is null.
- **Net credit (credit spreads) = short_bid − long_ask.** Must be > 0 and < width.
- **Net debit (debit spreads) = long_ask − short_bid.** Must be > 0 and < width.

---

## 2. Missing Fields Policy

- **Do NOT treat missing fields as 0** unless explicitly configured to do so per-field.
- Missing values must remain `null` / `None` and be tracked separately as **data-quality failures** in the filter trace.
- Specifically:
  - Missing OI → `missing_oi` (not OI = 0)
  - Missing volume → `missing_volume` (not volume = 0)
  - Missing IV → `missing_iv` (not IV = 0)
  - Missing delta → `missing_delta` (not delta = 0)
- `null` / `undefined` is always preferred over an incorrect number.

---

## 3. Source-of-Truth Policy

| Data Category | Authoritative Source | Notes |
|---|---|---|
| Option chains | **Tradier** | Strikes, expirations, greeks, OI, volume |
| Option quotes (bid/ask) | **Tradier** | Execution-critical pricing |
| Underlying price | **Tradier** | Real-time quote |
| Price history | Polygon | For IV/RV calculation, charting |
| Macro / rates | FRED | VIX term structure, risk-free rate |
| Company fundamentals | Finnhub | Sector, earnings calendar |

- If data from a **non-Tradier source** could change trade acceptance (e.g., would cause a candidate to be accepted or rejected), treat it as **non-authoritative** unless explicitly approved.
- Tradier data failures should be surfaced as errors, not silently backfilled from other sources.

---

## 4. Flagging Data-Quality Failures in Filter Trace

- Every data-quality rejection must appear in **both**:
  - `rejection_reason_counts` (with the appropriate reason code from [rejection-taxonomy.md](rejection-taxonomy.md))
  - `data_quality_counts` (the dedicated sub-object)
- The `data_quality_counts` object tracks these specific fields:
  - `missing_bid`, `missing_ask`, `missing_mid`
  - `missing_oi`, `missing_volume`
  - `missing_iv`, `missing_delta`
  - `invalid_credit`, `invalid_width`

---

## Cross-References

- Rejection taxonomy: [docs/standards/rejection-taxonomy.md](rejection-taxonomy.md)
- Scanner contract (trace schema): [docs/standards/scanner-contract.md](scanner-contract.md)
- Canonical trade shape: [docs/standards/canonical-contract.md](canonical-contract.md)
