# Options Metrics Validation — Deep ITM Debit Spread Analysis

**Date:** 2026-03-25  
**Trigger:** IWM Put Debit 240/262 surfaced in debit spreads top-10 with POP 96.5%, EV $1,111, RoR 117%  
**Status:** Math formulas verified correct for inputs used. **POP estimate is fundamentally misleading for deep ITM debit spreads** — the V2 pipeline uses delta approximation which dramatically overestimates probability of profit when the long leg is deep ITM.

---

## 1. Flagged Trade Summary

| Field | Value |
|-------|-------|
| Strategy | `put_debit` |
| Underlying | IWM @ $251.82 |
| Long leg | 262 PUT (bid 9.13 / ask 10.25, delta −0.96) |
| Short leg | 240 PUT (bid 0.13 / ask 0.15, delta −0.05) |
| Expiration | 2026-03-27 (2 DTE) |
| Width | $22 |
| Net debit | $10.12 (long.ask − short.bid = 10.25 − 0.13) |
| Breakeven | $251.88 (long_strike − net_debit = 262 − 10.12) |

---

## 2. Math Verification — Formulas Correct

All formulas from `scanner_v2/phases.py` (`_recompute_vertical_math`) verified:

| Metric | Formula | Calculation | Result | Reported | Match |
|--------|---------|-------------|--------|----------|-------|
| Width | \|short − long\| | \|240 − 262\| | 22 | 22 | ✓ |
| Net debit | long.ask − short.bid | 10.25 − 0.13 | 10.12 | 10.12 | ✓ |
| Max profit | (width − debit) × 100 | (22 − 10.12) × 100 | $1,188 | $1,188 | ✓ |
| Max loss | debit × 100 | 10.12 × 100 | $1,012 | $1,012 | ✓ |
| RoR | max_profit / max_loss | 1188 / 1012 | 1.174 (117%) | 117% | ✓ |
| POP | \|long.delta\| | \|−0.96\| | 0.965 | 96.5% | ✓ |
| EV | pop × max_profit − (1−pop) × max_loss | 0.965×1188 − 0.035×1012 | $1,111 | $1,111 | ✓ |

**Verdict:** The arithmetic is correct given the inputs. The problem is the POP formula itself.

---

## 3. ROOT CAUSE: POP Formula Overestimates Probability of Profit for Debit Spreads

### 3.1 What V2 computes

**File:** `scanner_v2/phases.py` lines 413–419

```python
# Debit spread: POP = |long_leg.delta|
m.pop = round(abs(long.delta), 4)
m.pop_source = "delta_approx"
```

This measures **P(long leg finishes ITM)** = P(stock < 262 at expiry) = 96.5%.

### 3.2 What POP should be

For a put debit spread, profit requires: spread value at expiry > debit paid.

- Stock < $240 → spread = $22 → profit = $1,188 ✓
- $240 < stock < $251.88 → spread = (262 − stock) > 10.12 → profitable ✓
- Stock > $251.88 → spread < 10.12 → **loss** ✗

**True POP = P(stock < breakeven) = P(stock < $251.88)**

With IWM at $251.82, the breakeven is just $0.06 above the current price. The true probability of profit is approximately **50–51%**, not 96.5%.

### 3.3 Breakeven lognormal verification

The legacy pipeline (`strategies/debit_spreads.py` line 47) computes this correctly:

```
P(S_T < breakeven) = N(-d2)
d2 = [ln(S/K) - σ²T/2] / (σ√T)
```

With S=251.82, K=251.88, σ≈0.22, T=2/365:
- d2 ≈ −0.023
- P(profit) = N(0.023) ≈ **50.9%**

### 3.4 Impact quantification

| POP estimate | Source | EV | Rank effect |
|-------------|--------|-----|-------------|
| 96.5% | V2 delta_approx | **$1,111** | Saturates edge + ror + pop → top rank |
| ~51% | Breakeven lognormal | **~$109** | Normal debit-spread range → mid-rank |

The delta approximation inflates POP by **~46 percentage points** and EV by **~10×** for this trade.

### 3.5 Why this doesn't affect credit spreads

For credit spreads, POP = 1 − |short.delta| ≈ P(short expires OTM) ≈ P(profit). The approximation is accurate because the net credit is small relative to the width, so breakeven ≈ short strike.

For debit spreads, the gap between "long leg finishes ITM" and "spread exceeds debit" varies dramatically — and grows as the long leg goes deeper ITM.

---

## 4. Spot-Check: Accepted Trades from March 11 Output

3 trades verified from `debit_spreads_analysis_20260311_145641.json`:

### Trade 1: SPY Put Debit 665/675, 9 DTE (SPY @ $677.76)

| Metric | Computed | File value | Match |
|--------|----------|------------|-------|
| Width | \|665−675\| = 10 | 10.0 | ✓ |
| Debit | 8.45−5.51 = 2.94 | 2.94 | ✓ |
| Max profit | (10−2.94)×100 = 706 | 706.0 | ✓ |
| Max loss | 2.94×100 = 294 | 294.0 | ✓ |
| POP delta | \|−0.4617\| = 0.4617 | 0.4617 | ✓ |
| POP refined | breakeven lognormal | 0.4166 | ✓ (p_win_used) |
| EV | 0.4166×706 − 0.5834×294 = 122.61 | 122.61 | ✓ |
| RoR | 706/294 = 2.401 | 2.401 | ✓ |

**Note:** This trade correctly uses `p_win_used = 0.4166` (breakeven lognormal), not the raw delta of 0.4617. The V2 pipeline would use 0.4617 — a 10% overestimation for near-ATM spreads.

### Trade 2: IWM Put Debit 245/255, 9 DTE (IWM @ $253.62)

| Metric | Computed | File value | Match |
|--------|----------|------------|-------|
| Debit | 5.96−2.52 = 3.44 | 3.44 | ✓ |
| Max profit | (10−3.44)×100 = 656 | 656.0 | ✓ |
| Max loss | 3.44×100 = 344 | 344.0 | ✓ |
| POP delta | 0.617 | 0.617 | ✓ |
| POP refined | breakeven lognormal | 0.4493 | ✓ (p_win_used) |
| EV | 0.4493×656 − 0.5507×344 = 105.32 | 105.32 | ✓ |

**Note:** Delta overestimates by 0.168 (37% relative) even for a slightly ITM long leg.

### Trade 3: SPY Put Debit 667/677, 9 DTE

| Metric | Computed | File value | Match |
|--------|----------|------------|-------|
| Debit | 320/100 = 3.20 | confirmed | ✓ |
| EV | 0.4437×680 − 0.5563×320 = 123.73 | 123.74 | ✓ (rounding) |

**Spot-check verdict:** All 3 trades math is correct. The March 11 file uses breakeven lognormal POP via the legacy enrichment pipeline, which gives more accurate estimates.

---

## 5. Filter Gap Analysis

### 5.1 DTE minimum: 1 (too low)

**File:** `scanner_v2/families/vertical_spreads.py` line 111

```python
dte_min = 1
dte_max = 90
```

| Family | dte_min | dte_max |
|--------|---------|---------|
| Vertical spreads | **1** | 90 |
| Iron condors | 7 | 60 |
| Butterflies | 7 | 60 |
| Calendars | 7 | 90 |

Options with 1–2 DTE are gamma-dominated, illiquid, and have aggressive theta decay. They are gamma scalps, not income plays. Iron condors and butterflies already set 7 DTE minimum — verticals should match.

**Recommendation:** Raise `dte_min` to **5** for vertical spreads (both credit and debit).

### 5.2 No ITM/moneyness filter

The delta filter on the **short leg** (0.05–0.40) is the only moneyness-related gate. No filter exists for:

- Long leg delta / moneyness
- Spread-level intrinsic value concentration
- Distance between underlying price and long strike

The user's trade: short delta = 0.05 (just barely passes the 0.05 floor). The long leg's delta of 0.96 is unrestricted.

**Recommendation:** Add a **long leg delta cap** of 0.85 for debit spreads, rejecting candidates where the long leg is deep ITM. Alternatively, cap `debit_as_pct_of_width` to 0.40 (currently 0.50).

### 5.3 Credibility gate gap

The credibility gate in `options_opportunity_runner.py` (line ~1175) checks:

| Check | Threshold | User's trade | Result |
|-------|-----------|-------------|--------|
| Net premium ≥ $0.05 | MIN_PREMIUM = 0.05 | net_debit = 10.12 | PASSES |
| POP < 0.995 | MAX_POP_THRESHOLD = 0.995 | pop = 0.965 | PASSES |
| At least one fillable leg | bid > 0 | both have bid > 0 | PASSES |

Credibility gate only catches "worthless deep-OTM" trades, not "pure intrinsic deep-ITM" trades.

**Recommendation:** Add an **intrinsic value credibility check** — reject debit spreads where `debit_as_pct_of_width > 0.45 AND long_leg |delta| > 0.85`.

### 5.4 Ranking normalization miscalibration

**File:** `ranking.py` lines 76–84

| Component | Weight | Norm range | User's trade | Score |
|-----------|--------|-----------|-------------|-------|
| edge (EV/risk) | 0.30 | [0.00, 0.05] | 1.098 | **1.0** (maxed) |
| ror | 0.22 | [0.05, 0.50] | 1.174 | **1.0** (maxed) |
| pop | 0.20 | [0.50, 0.95] | 0.965 | **1.0** (maxed) |
| liquidity | 0.18 | computed | low (OTM short leg) | ~0.1 |

Three of four components saturate at 1.0. The only differentiator is liquidity (18% weight). This guarantees deep ITM debit spreads outrank healthy near-ATM spreads.

For comparison, a healthy SPY put debit (from March 11):

| Component | Norm range | Value | Score |
|-----------|-----------|-------|-------|
| edge | [0.00, 0.05] | 0.417 | 1.0 (also maxed) |
| ror | [0.05, 0.50] | 2.40 | 1.0 (also maxed) |
| pop | [0.50, 0.95] | 0.4166 | **0.0** (below range!) |
| liquidity | computed | high | ~0.7 |

The normalization range [0.50, 0.95] for POP is calibrated for credit spreads (POP 55–85%), not debit spreads (POP 30–55%). Healthy debit spreads score 0.0 on POP while deep ITM anomalies score 1.0.

---

## 6. Recommendations

### Immediate fixes (high impact, low risk)

1. **Raise DTE floor for verticals to 5** — matches the philosophy of other families; prevents gamma-dominated near-expiry trades from surfacing.

2. **Add long-leg delta cap of 0.85 for debit spreads** — rejects deep ITM long legs where POP delta approximation is wildly inaccurate.

3. **Lower `max_debit_pct_width` from 0.50 to 0.40** — catches trades where most of the spread cost is intrinsic value.

### Architectural improvements (medium effort)

4. **Implement breakeven POP in V2 Phase E for debit spreads** — the legacy `_compute_pop_breakeven_lognormal()` already exists in `strategies/debit_spreads.py`. Port it to `phases.py` and use it as `pop` (replacing or refining the delta approximation). This is the ROOT FIX.

5. **Add debit-specific normalization ranges to ranking.py** — the current `minmax_norm` ranges are credit-spread-calibrated. Either detect strategy type and use different ranges, or widen the ranges:
   - `pop`: [0.25, 0.95] (covers both credit and debit spread POP ranges)
   - `edge`: [0.00, 0.50] (debit spreads routinely have ev_to_risk > 0.05)
   - `ror`: [0.05, 3.00] (debit spreads have RoR 1.5–3.5)

6. **Add "deep ITM" credibility check** — reject debit candidates where `|long.delta| > 0.85 AND dte < 7` (intrinsic-dominated near-expiry).

### Monitoring

7. **Log POP source in scanner output** — track how many candidates use delta_approx vs breakeven_lognormal to quantify exposure to this issue across all runs.

---

## 7. Answer to Original Questions

**Q: Should this trade be in the top-10?**  
No. The math is arithmetically correct but the POP of 96.5% is a delta approximation that means "probability long leg finishes ITM" — not "probability of profit." True POP via breakeven lognormal is ~51%. With corrected POP, EV drops from $1,111 to ~$109, making this a mid-tier candidate at best.

**Q: Is a 2 DTE deep ITM spread a good income trade?**  
No. The breakeven ($251.88) is virtually at the current price ($251.82). The position has a coin-flip probability of profit, pays commission relative to notional, and has no time for directional thesis to play out. This is a gamma scalp, not an income trade.

**Q: What's missing in the scanner filters?**  
Three gaps: (1) DTE floor is 1, should be 5+; (2) no long-leg moneyness filter; (3) V2 POP uses delta approximation instead of breakeven probability for debit spreads. The combination allows deep ITM near-expiry trades to appear with artificially inflated metrics that dominate ranking.

---

## Files Referenced

| File | Relevance |
|------|-----------|
| `scanner_v2/phases.py:375–440` | V2 Phase E math (POP, EV, RoR formulas) |
| `scanner_v2/families/vertical_spreads.py:63–112` | Delta range, DTE limits, variant config |
| `strategies/debit_spreads.py:47–96` | Breakeven lognormal POP (legacy, correct) |
| `strategies/debit_spreads.py:1079–1280` | Legacy evaluate() gates |
| `services/ranking.py:76–130` | Ranking normalization and scoring |
| `workflows/options_opportunity_runner.py:1170–1200` | Credibility gate |
| `workflows/options_opportunity_runner.py:1220–1295` | Strategy-diverse ranking |
