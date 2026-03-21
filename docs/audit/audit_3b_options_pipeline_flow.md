# Audit 3B — Options Pipeline End-to-End Flow

**Scope**: Complete trace of the options opportunity pipeline — from symbol universe through chain fetching, V2 scanner 6-phase execution, credibility gate, EV-based ranking, and final output packaging. Five workflow stages mapped with candidate counts, data transformations, and failure modes at each boundary.

**Date**: 2025-07-19
**Auditor**: Copilot (automated deep-read)

---

## Source Files

| Component | File | Key Lines |
|-----------|------|-----------|
| Runner (pipeline orchestrator) | `app/workflows/options_opportunity_runner.py` | L87–140 (constants/config), L455–660 (orchestrator), L662–795 (stages 1-2), L796–918 (stage 3), L918–1040 (stage 4), L1040–1200 (stage 5) |
| Scanner service (dispatch) | `app/services/options_scanner_service.py` | L63–139 (scan), L141–220 (_run_one) |
| V2 Base Scanner | `app/services/scanner_v2/base_scanner.py` | L78–260 (6-phase run), L263–410 (hooks) |
| V2 Narrowing | `app/services/scanner_v2/data/narrow.py` | L46–152 (narrow_chain) |
| V2 Expiry filtering | `app/services/scanner_v2/data/expiry.py` | L41–181 (DTE window) |
| V2 Strike filtering | `app/services/scanner_v2/data/strikes.py` | L43–159 (distance/moneyness) |
| V2 Contracts (dataclasses) | `app/services/scanner_v2/contracts.py` | L47–322 (V2Leg, V2RecomputedMath, V2Candidate) |
| Vertical Spreads family | `app/services/scanner_v2/families/vertical_spreads.py` | L65–345 |
| Iron Condors family | `app/services/scanner_v2/families/iron_condors.py` | L63–410 |
| Butterflies family | `app/services/scanner_v2/families/butterflies.py` | L73–300 |
| Calendars/Diagonals family | `app/services/scanner_v2/families/calendars.py` | L80–220 |
| Phase implementations | `app/services/scanner_v2/phases.py` | C/D/D2/E/F implementations |
| Quote sanity | `app/services/scanner_v2/hygiene/quote_sanity.py` | L44–116 |
| Dedup | `app/services/scanner_v2/hygiene/dedup.py` | L1–80 |
| Registry | `app/services/scanner_v2/registry.py` | L74–148 |

---

## Pipeline Constants

| Constant | Value | Location | Purpose |
|----------|-------|----------|---------|
| `DEFAULT_TOP_N` | 30 | runner L98 | Final output cap |
| `DEFAULT_SYMBOLS` | `("SPY", "QQQ", "IWM", "DIA")` | runner L101 | Index ETF universe |
| `ALL_V2_SCANNER_KEYS` | 11 keys | runner L104–128 | All scanner variants |
| `MIN_PREMIUM` | $0.05 | runner L968 | Credibility gate: minimum per-share premium |
| `MAX_POP_THRESHOLD` | 0.995 | runner L969 | Credibility gate: reject delta ≈ 0 |
| `_DEFAULT_GENERATION_CAP` | 50,000 | vertical_spreads L51 | Per-symbol candidate explosion safety |
| `_DEFAULT_MAX_WIDTH` | $50 | vertical_spreads L66 | Maximum strike-distance per spread |

### DTE Windows Per Family

| Family | dte_min | dte_max | Multi-expiry? |
|--------|---------|---------|---------------|
| Vertical Spreads | 1 | 90 | No |
| Iron Condors | 7 | 60 | No |
| Butterflies | 7 | 60 | No |
| Calendars/Diagonals | 7 | 90 | Yes |

---

## 1. Stage Inventory & Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    OPTIONS OPPORTUNITY PIPELINE                        │
│                    (options_opportunity_runner.py)                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  Stage 1: load_market_state                                           │
│    └─ Load latest market state (regime, VIX, tags)                    │
│         Status: degradable (enrichment-only)                          │
│         Failed → abort                                                │
│                                                                       │
│  Stage 2: scan                                                        │
│    └─ OptionsScannerService.scan(symbols, scanner_keys, context)      │
│    └─ For each (scanner_key × symbol):                                │
│         └─ Fetch expirations from Tradier                             │
│         └─ Fetch chain per expiration                                 │
│         └─ V2 Scanner 6-phase pipeline (A→B→C→D→D2→E→F)              │
│    └─ Collect passed + rejected candidates                            │
│         Status: failed → abort                                        │
│                                                                       │
│  Stage 3: validate_math                                               │
│    └─ Filter: keep only downstream_usable=True candidates             │
│    └─ Surface V2 validation results as workflow artifacts             │
│         Status: failed → abort                                        │
│                                                                       │
│  Stage 4: enrich_evaluate                                             │
│    └─ Enrich: attach market_regime, risk_environment                  │
│    └─ Credibility gate (3 checks)                                     │
│    └─ Sort: EV DESC → RoR DESC → symbol ASC                          │
│    └─ Assign rank (1-based)                                           │
│         Status: failed → abort                                        │
│                                                                       │
│  Stage 5: select_package              ← ALWAYS EXECUTES              │
│    └─ Select top-N (default 30)                                       │
│    └─ Write output.json, summary.json, manifest.json, latest.json    │
│                                                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

### V2 Scanner Internal Pipeline (runs inside Stage 2 per scanner_key×symbol)

```
┌─────────────────────────────────────────────────────────────────┐
│  V2 SCANNER 6-PHASE PIPELINE (base_scanner.py)                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Phase A: Narrowing                                             │
│    └─ Filter chain by DTE window [dte_min, dte_max]             │
│    └─ Filter strikes by option_type, moneyness, distance        │
│    └─ Dedup by (expiration, strike, option_type) → keep max OI  │
│    └─ Output: V2NarrowedUniverse with expiry buckets            │
│                                                                 │
│  Phase B: Candidate Construction (family-specific)              │
│    └─ Enumerate all valid leg combinations per expiry bucket    │
│    └─ Safety cap at 50,000 candidates per (scanner, symbol)     │
│    └─ Output: list[V2Candidate] with preliminary math           │
│                                                                 │
│  Phase C: Structural Validation                                 │
│    └─ Shared: leg count, option_type consistency, expiry match  │
│    └─ Family hooks: IC geometry, butterfly symmetry, etc.       │
│    └─ Reject codes: v2_malformed_legs, v2_ic_invalid_geometry   │
│                                                                 │
│  Phase D: Quote & Liquidity Sanity                              │
│    └─ Per-leg: bid/ask present, not inverted, not negative      │
│    └─ Per-leg: OI > 0, volume > 0 (presence checks)            │
│    └─ Reject codes: v2_negative_bid/ask, v2_missing_quote       │
│                                                                 │
│  Phase D2: Trust Hygiene                                        │
│    └─ Re-run quote & liquidity sanity (safety layer)            │
│    └─ Wide spread warning: spread_ratio > 100% of mid           │
│    └─ Dedup: suppress structural duplicates                     │
│    └─ Reject code: v2_dedup_duplicate_suppress                  │
│                                                                 │
│  Phase E: Recomputed Math                                       │
│    └─ Default (verticals): net_credit, width, max_profit,       │
│       max_loss, POP, EV, RoR, Kelly, breakeven                  │
│    └─ Family overrides: IC (both-side credit, dual breakeven),  │
│       butterfly, calendar (EV=None)                             │
│    └─ Math verification: downstream_usable set to False if fail │
│                                                                 │
│  Phase F: Normalization                                         │
│    └─ passed = (no reject_reasons)                              │
│    └─ downstream_usable = passed (default)                      │
│    └─ Set generated_at, scanner_version                         │
│                                                                 │
│  → Return: V2ScanResult { passed[], rejected[],                 │
│              phase_counts, reject_reason_counts }               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Symbol → Chain → Candidate Flow

### Symbol Universe

4 index ETFs: `SPY`, `QQQ`, `IWM`, `DIA` (hardcoded default, user-overridable via `RunnerConfig.symbols`).

### Dispatch Model

11 scanner_keys × 4 symbols = **44 (scanner_key, symbol) pairs** per run.

Each pair runs the full 6-phase V2 pipeline independently:

```
Scanner dispatch:
  For each scanner_key in ALL_V2_SCANNER_KEYS:            (11 keys)
    For each symbol in DEFAULT_SYMBOLS:                    (4 symbols)
      1. Fetch expirations from Tradier
      2. Fetch chain contracts per expiration
      3. Run BaseV2Scanner.run() → 6 phases
      4. Collect passed + rejected V2Candidates
```

### Chain Data Flow

```
Tradier API
  ↓
base_data_service.get_expirations(symbol) → [ISO date list]
  ↓
For each expiration:
  base_data_service.get_analysis_inputs(symbol, exp) → OptionContract(Pydantic)
  .model_dump() → dict
  ↓
Merge all → chain = {"options": {"option": [dict, ...]}}
  ↓
BaseV2Scanner.run(chain, underlying_price, context)
```

### Candidate Count Funnel (estimated per symbol)

```
Expirations fetched:   ~20-40 per symbol (weeklies + monthlies, 0-90 DTE)
                         ↓
Phase A (narrow):      ~10-25 expirations survive DTE window
                       ~100-500 contracts per expiration
                         ↓
Phase B (construct):   Verticals: O(n²) per expiry — potentially thousands
                       IC: O(n²×n²) cross-product — can explode to 50K cap
                       Butterfly: O(n²) symmetric triplets — hundreds
                       Calendar: O(exp²×strikes) — hundreds
                       Safety cap: 50,000 per (scanner, symbol)
                         ↓
Phase C (structural):  ~95-99% survive (rejects malformed legs only)
                         ↓
Phase D (quote/liq):   ~70-90% survive (rejects missing/inverted quotes)
                         ↓
Phase D2 (hygiene):    ~60-85% survive (dedup suppresses exact duplicates)
                         ↓
Phase E (math):        ~95-99% survive (rejects math failures: width=0, etc.)
                         ↓
Phase F (normalize):   passed = no reject_reasons → downstream_usable=True
                         ↓
Typical yield:         ~hundreds to few thousand passed per (scanner, symbol)
```

### Aggregate Across All 44 Scanner Runs

```
44 scanner runs → raw_candidates (passed only, aggregated)
                  + rejected_candidates (for diagnostics)
                    ↓
Stage 3 (validate_math):  Filter downstream_usable=False
                          Typically ~0-5% filtered here (Phase F already filtered)
                    ↓
Stage 4 (credibility gate): 3 checks → reject penny, zero-delta, unfillable
                             Typically rejects 30-60% of candidates
                    ↓
Sort by EV DESC → RoR DESC → symbol ASC
                    ↓
Stage 5 (select):  Top 30 → output.json
```

---

## 3. Cross-Family Candidate Distribution

### Scanner Key → Family Mapping

| Family | Scanner Keys | Leg Count | EV Computed? |
|--------|-------------|-----------|--------------|
| Vertical Spreads | put_credit_spread, call_credit_spread, put_debit, call_debit | 2 | Yes |
| Iron Condors | iron_condor | 4 | Yes |
| Butterflies | butterfly_debit, iron_butterfly | 3 or 4 | Yes |
| Calendars/Diagonals | calendar_call_spread, calendar_put_spread, diagonal_call_spread, diagonal_put_spread | 2 | **No** (EV=None) |

### Volume Distribution

Vertical spreads dominate raw candidate volume because:
1. Four variants (4 scanner_keys vs 1 for IC, 2 for butterfly, 4 for calendar)
2. O(n²) construction per expiration with widest DTE window (1-90)
3. Simpler structure means fewer Phase C/D rejections

**Estimated distribution of raw_candidates**:
- Verticals: ~60-70%
- Iron Condors: ~10-15%
- Butterflies: ~5-10%
- Calendars/Diagonals: ~10-20%

### Family Competition for Top-30

All families compete for the same top-30 slots via the EV-based sort. This creates significant bias:

- **Verticals and IC** have computed EV → compete directly
- **Calendars and diagonals have EV=None** → `_safe_float(None) = 0.0` → sorted to **bottom**
- Calendar/diagonal candidates can only appear in top-30 if fewer than 30 candidates with positive EV exist

---

## 4. Credibility Gate Details

### Location

`_stage_enrich_evaluate()` in runner (L956–1000).

### Three Checks (applied in order, first failure rejects)

| # | Check | Condition | Reject If | Reason Code | Rationale |
|---|-------|-----------|-----------|-------------|-----------|
| 1 | Minimum premium | `max(net_credit, net_debit)` | < $0.05 per-share | `penny_premium` | Rejects deep-OTM with no real premium |
| 2 | Delta sanity | POP from delta | ≥ 0.995 | `zero_delta_short` | Rejects shorts with delta ≈ 0 (worthless) |
| 3 | Fillability | Any leg bid | All legs bid ≤ 0 | `all_legs_zero_bid` | Rejects unfillable positions |

### Check Ordering & Impact

The checks are sequential with `continue` on first failure — a candidate failing check 1 is never tested for checks 2 or 3. This means rejection counts per reason are NOT independent.

**Expected rejection distribution** (estimated):
- `penny_premium`: Highest rejector — deep-OTM spreads with <$0.05 credit are very common, especially for wide-width verticals and far-OTM condor sides
- `zero_delta_short`: Moderate — catches ultra-far-OTM shorts where delta rounds to zero
- `all_legs_zero_bid`: Lowest — most legs have at least some market-maker bid

### Credibility Gate Gaps

The gate checks basic viability but does NOT check:
- Minimum width (a $0.50 wide spread may pass with $0.06 premium)
- Minimum RoR (a positive-EV trade with 2% RoR is not practically useful)
- Bid-ask spread as percentage of mid (a $0.10 credit where bid-ask spread is $0.15 is not fillable at quoted price)
- Minimum OI/volume thresholds per leg (presence is checked in Phase D, but no minimum threshold)

---

## 5. Ranking and Selection

### Sort Key

```python
# Runner L1013-1018
credible.sort(
    key=lambda c: (
        -_safe_float((c.get("math") or {}).get("ev")),      # Primary: EV DESC
        -_safe_float((c.get("math") or {}).get("ror")),      # Secondary: RoR DESC
        c.get("symbol", ""),                                  # Tertiary: symbol ASC
    ),
)
```

### Sort Behavior with None/Missing Values

`_safe_float(None)` returns `0.0`:
- Calendar/diagonal EV=None → sorts as EV=0.0 → **bottom of list**
- Any candidate with positive EV ranks above all calendars
- Among calendars, RoR (also None→0.0) is the tiebreaker → falls to symbol alphabetical

### Selection

```python
selected = enriched[:config.top_n]  # default=30
```

No additional filtering after sort — pure rank cutoff.

### Ranking Bias

1. **EV-first ranking favors credit strategies** — put credit spreads and iron condors naturally have higher EV than debit strategies because credit strategies have higher POP
2. **Calendar/diagonal exclusion** — with EV=None (coerced to 0.0), these families are effectively excluded from top-30 unless fewer than 30 other candidates pass the credibility gate
3. **No EV normalization** — EV is per-contract absolute dollars; a 50-point-wide SPY condor with $500 EV always outranks a 5-point-wide QQQ vertical with $50 EV, regardless of capital efficiency (RoR)

---

## 6. Market State Integration

### Where Used

| Stage | Field | Source | Role |
|-------|-------|--------|------|
| Stage 1 | market_state_ref, consumer_summary, composite | `load_market_state_for_consumer()` | Load from disk |
| Stage 2 | market_state_ref, consumer_summary | Passed as `context` to scanner service | Available to scanners (NOT used for filtering) |
| Stage 4 | market_regime, risk_environment | Attached to each candidate from consumer_summary | Enrichment only |

### How It's Used (and Not Used)

- **Enrichment only** — market regime and risk environment are **attached** to candidates for display but **never used for filtering, scoring, or ranking**
- **No regime-aware gating** — if market_state is "risk_off", premium-selling strategies (put credit spreads, iron condors) are NOT blocked or penalized
- **No VIX-aware thresholds** — min_premium, DTE windows, and credibility checks are static constants regardless of VIX level
- **Scanner context** — the consumer_summary is passed to V2 scanners as `context`, but no family scanner reads or acts on it

### Degradation

Market state failure → Stage 1 returns "degraded" status → pipeline continues with empty enrichment fields. No candidates are rejected due to missing market state.

---

## 7. Missing: Model Analysis Layer

### Confirmation: NO Model Analysis Stage

The options pipeline has **no LLM/model analysis stage**. Unlike the stock pipeline which has:
- Stage 7: `run_final_model_analysis` (LLM review per candidate)
- Stage 7b: `model_filter_rank` (PASS removal, model_score ranking)

The options pipeline is **pure quantitative** — selection is entirely via V2 scanner math (EV, RoR, POP) and the credibility gate.

### Fields Missing for Model Analysis

If model analysis were added, candidates would need:
- `model_recommendation` (BUY/PASS)
- `model_confidence` (0-100)
- `model_score` (0-100)
- `model_review_summary` (text)
- `model_key_factors` (list of factor assessments)
- `model_caution_notes` (risk flags)
- `model_review` (full analysis dict)

### Where It Would Slot In

Between Stage 4 (enrich_evaluate) and Stage 5 (select_package):
- After credibility gate filtering and EV-based ranking
- Before top-N selection
- Would review the top ~30-50 candidates by EV
- Could override ranking via model_score or hybrid score

---

## 8. Pipeline Failure Modes

### Stage Failure Matrix

| Stage | On Failure | Pipeline Impact |
|-------|-----------|-----------------|
| 1 — load_market_state | Failed → abort, Degraded → continue | Hard failure aborts; stale/degraded data acceptable |
| 2 — scan | Failed → abort | Total scanner service failure aborts |
| 2 (per-scanner) | Exception → warning, skip | Individual (scanner_key, symbol) failure is tolerated |
| 2 (per-expiration) | Exception → debug log, skip `continue` | Individual chain fetch failure is tolerated |
| 3 — validate_math | Failed → abort | Runtime error in validation aborts |
| 4 — enrich_evaluate | Failed → abort | Runtime error in enrichment/credibility aborts |
| 5 — select_package | Always runs | Packages whatever candidates are available |

### Tradier Chain Failure

Per-expiration failure handling in `_run_one()`:

```python
for exp in expirations:
    try:
        inputs = await self._bds.get_analysis_inputs(symbol, exp, ...)
        contracts = inputs.get("contracts") or []
        merged_options.extend([c.model_dump() for c in contracts])
    except Exception as exc:
        _log.debug("event=chain_fetch_skip ... error=%s", exc)
        continue  # Skip this expiration, continue with others

if not merged_options:
    return _empty_result(...)  # 0 candidates, stage still succeeds
```

- **One expiration fails**: Skip it, use remaining expirations → partial data
- **All expirations fail**: Return empty result (0 candidates), scan stage still succeeds
- **All symbols fail**: raw_candidates = [], pipeline continues to Stage 5 with 0 candidates → output.json written with empty list, quality_level = "no_candidates"

### Phase E Math Failure

- If recomputation throws → candidate gets reject_reason → marked `downstream_usable=False` in Phase F
- Examples: width=0 (division error), max_loss ≤ 0, missing leg data
- NOT escalated to stage failure — candidate rejected, pipeline continues

### CancelledError / Unexpected Error

Same pattern as stock pipeline:
```python
except asyncio.CancelledError:
    warnings.append("[pipeline] Run interrupted — packaging partial output")
except Exception as exc:
    warnings.append(f"[pipeline] Unexpected error — packaging partial output: {exc}")

# Stage 5 ALWAYS executes
outcome = _stage_select_package(...)
```

---

## Pipeline Flow Diagram with Candidate Counts

```
SYMBOLS:       SPY, QQQ, IWM, DIA (4 index ETFs)
                  ↓
DISPATCH:      11 scanner_keys × 4 symbols = 44 (scanner, symbol) runs
                  ↓
TRADIER:       ~20-40 expirations per symbol fetched
               ~100-500 contracts per expiration
                  ↓
╔══════════════════════════════════════════════════════════════╗
║              V2 SCANNER 6-PHASE (per scanner × symbol)      ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Phase A: Narrow by DTE + strikes                            ║
║    ~10-25 expirations survive                                ║
║                                                              ║
║  Phase B: Construct candidates (family-specific)             ║
║    Verticals: ~1,000-50,000 (O(n²) per expiry)              ║
║    IC: ~500-50,000 (cross-product of sides)                  ║
║    Butterfly: ~100-1,000 (symmetric triplets)                ║
║    Calendar: ~50-500 (same-strike cross-expiry)              ║
║                                                              ║
║  Phase C: Structural validation                              ║
║    ~95-99% survive                                           ║
║                                                              ║
║  Phase D: Quote/liquidity sanity                             ║
║    ~70-90% survive                                           ║
║                                                              ║
║  Phase D2: Trust hygiene + dedup                             ║
║    ~60-85% survive (dedup is major filter)                   ║
║                                                              ║
║  Phase E: Recomputed math                                    ║
║    ~95-99% survive                                           ║
║                                                              ║
║  Phase F: Normalize → passed / rejected                      ║
╚══════════════════════════════════════════════════════════════╝
                  ↓
Stage 2 output:  raw_candidates (passed) + rejected_candidates
                 Estimated: ~hundreds to thousands passed per run
                  ↓
Stage 3:         Filter downstream_usable=False
                 ~0-5% filtered (Phase F already handled this)
                  ↓
Stage 4:         Credibility gate (3 checks)
                 penny_premium:    rejects ~20-40% (deep-OTM noise)
                 zero_delta_short: rejects ~5-15%
                 all_legs_zero_bid: rejects ~1-5%
                 Total: ~30-60% rejected
                  ↓
                 Sort by EV DESC → RoR DESC → symbol ASC
                 Assign rank (1-based)
                  ↓
Stage 5:         Top 30 selected → output.json
                  ↓
OUTPUT:          ≤30 ranked options candidates
```

---

## Findings

### F-3B-01 — HIGH: Calendar/Diagonal Candidates Systematically Excluded from Top-30

**Evidence**: Calendar and diagonal families have `EV=None` (correctly — time-spread EV requires forward-looking assumptions). The sort key uses `_safe_float(None) = 0.0`, so all calendar/diagonal candidates sort to the bottom. With verticals and condors generating hundreds of positive-EV candidates, calendars **never appear in the top-30**.

**Impact**: An entire strategy family is effectively invisible in the output despite being a core part of BenTrade's options philosophy. Calendar spreads are valuable income strategies, especially in range-bound markets.

**Root cause**: The ranking system uses a single sort key (EV) that is structurally undefined for one family. There is no multi-track ranking or family-reserved slots.

**Recommendation**: Either:
1. Reserve top-N slots per family (e.g., top 10 verticals + top 5 IC + top 5 butterfly + top 5 calendar + top 5 diagonal)
2. Compute a proxy EV for calendars (e.g., based on theta differential and IV term structure)
3. Use a normalized composite score that accounts for family differences

---

### F-3B-02 — HIGH: No Model Analysis Layer — Pure Quantitative Selection

**Evidence**: The options pipeline has 5 stages with no LLM review. The stock pipeline has 8 stages including `run_final_model_analysis` (Stage 7) and `model_filter_rank` (Stage 7b). Options candidates are selected purely by EV/RoR ranking after a basic credibility gate.

**Impact**:
- No qualitative review of trade thesis, market context appropriateness, or risk/reward judgment
- A mechanically high-EV trade that is inappropriate for current market conditions (e.g., selling premium into a volatility spike) will rank at the top
- The stock pipeline can PASS unsuitable candidates; the options pipeline cannot
- Pass 2 noted this asymmetry — options pipeline has no model-based quality gate

**Recommendation**: Add a model analysis stage between Stage 4 and Stage 5. Start with the top-N by EV (e.g., top 50) to avoid LLM cost on thousands of candidates. Model can assess:
- Regime appropriateness (credit strategy in risk-off?)
- Greeks quality (theta/vega ratio for income trades)
- Event risk proximity
- IV rank context

---

### F-3B-03 — HIGH: No Regime-Aware Gating in Options Pipeline

**Evidence**: Market regime (`market_state`) and risk environment (`stability_state`) are loaded in Stage 1 and attached to candidates in Stage 4, but are **never used for filtering, scoring adjustment, or ranking modification**. The credibility gate's 3 checks are regime-agnostic.

**Impact**: In a "risk_off" regime with elevated VIX:
- Put credit spreads (short premium) still rank by EV as if conditions were normal
- Iron condors (short premium on both sides) are not penalized
- The delta-approximated POP may be misleadingly high if realized vol exceeds implied vol
- BenTrade's philosophy emphasizes risk-defined strategies, but the pipeline doesn't adapt strategy selection to the regime

**Recommendation**: Add regime-conditional logic:
- In risk_off: penalize or block premium-selling strategies, or require wider spreads / lower delta
- In elevated VIX: adjust POP estimates or require higher minimum POP
- Pass regime context to the credibility gate or as a scoring multiplier

---

### F-3B-04 — MEDIUM: EV Ranking Favors Absolute Dollars Over Capital Efficiency

**Evidence**: The sort key is `(-EV, -RoR, symbol)`. EV is per-contract absolute dollars. A 50-point-wide SPY condor with $500 EV always ranks above a 5-point-wide QQQ vertical with $50 EV, even though the QQQ trade may be more capital-efficient (higher RoR).

**Impact**: Wider spreads on higher-priced underlyings dominate rankings because they have higher absolute EV. Narrower, more capital-efficient trades that might be better for smaller accounts are systematically disadvantaged.

**Recommendation**: Consider `ev_per_day` or a normalized metric like `ev / max_loss` (which is just RoR) or a blended score. At minimum, offer a ranking mode toggle between "absolute EV" and "capital efficiency (RoR)" for different account sizes.

---

### F-3B-05 — MEDIUM: Credibility Gate Checks Are Sequential — Rejection Counts Not Independent

**Evidence**: The three credibility checks use `continue` on first failure:

```python
if max_premium < MIN_PREMIUM:
    credibility_reasons["penny_premium"] += 1
    continue                            # ← skip checks 2 and 3

if pop >= MAX_POP_THRESHOLD:
    credibility_reasons["zero_delta_short"] += 1
    continue                            # ← skip check 3

if not has_fillable_leg:
    credibility_reasons["all_legs_zero_bid"] += 1
    continue
```

**Impact**: A candidate with both penny premium AND zero delta is only counted as `penny_premium`. The `zero_delta_short` and `all_legs_zero_bid` counts are understated because they only count candidates that passed earlier checks. The diagnostic counts are therefore not a complete picture of data quality.

**Risk**: Low. The candidates are correctly rejected either way. But for diagnostic analysis of data quality, the counts are misleading.

**Recommendation**: Collect all applicable reasons per candidate (don't `continue` after first failure), then reject. This gives accurate per-reason counts for diagnostics.

---

### F-3B-06 — MEDIUM: Credibility Gate Missing Width and Spread Sanity Checks

**Evidence**: The credibility gate checks premium, delta, and fillability but does NOT check:
1. **Minimum width** — a $0.50-wide spread with $0.06 credit passes all 3 checks but offers $6 profit / $44 max loss (13.6% RoR) — barely viable
2. **Bid-ask spread ratio** — a $0.10 credit where the bid-ask spread is $0.15 is unfillable at quoted price
3. **Minimum RoR** — a 2% RoR trade is not practically useful for income strategies
4. **Minimum OI/volume per leg** — Phase D checks for presence (>0) but not minimum thresholds

**Impact**: Low-quality candidates that pass the credibility gate can occupy top-30 slots, displacing genuinely actionable trades.

**Recommendation**: Add to credibility gate:
- `min_width >= $1.00` (or $2.00 for index ETFs)
- `bid_ask_spread_ratio < 0.50` (spread should be < 50% of mid)
- `min_ror >= 0.05` (5% minimum return on risk)
- `min_oi_per_leg >= 10` (minimum open interest)

---

### F-3B-07 — MEDIUM: Phase D and D2 Overlap — Double Quote/Liquidity Check

**Evidence**: Phase D runs `phase_d_quote_liquidity_sanity()` and Phase D2 runs `run_quote_sanity()` + `run_liquidity_sanity()` again as part of trust hygiene. The Phase D2 quote sanity checks are a superset of Phase D's checks.

**Impact**: Candidates rejected in Phase D are not re-checked in D2 (they already have reject_reasons), so there's no double-counting. But Phase D rejects are redundant with D2's more thorough checks. Processing cost is wasted on candidates that Phase D would have caught.

**Risk**: Low — correctness is maintained. This is a code-hygiene issue.

**Recommendation**: Consider merging Phase D into D2 or having Phase D be a fast pre-filter with Phase D2 as the thorough pass.

---

### F-3B-08 — MEDIUM: No Cross-Family Deduplication

**Evidence**: Dedup in Phase D2 operates per (scanner_key, symbol) run. A put credit spread on SPY 400/395 and an iron condor that includes the same put side (SPY 400/395) are never deduped against each other because they run in separate scanner executions.

**Impact**: The top-30 could contain a standalone vertical and a condor that includes the same vertical as one side. This isn't necessarily wrong (they are different strategies), but the portfolio exposure overlap is not tracked.

**Recommendation**: Add a post-aggregation dedup or overlap detection step in Stage 3 or 4 that flags when a condor's component side is also present as a standalone vertical in the ranked list.

---

### F-3B-09 — LOW: Generation Cap (50K) May Still Allow Memory Pressure

**Evidence**: Vertical spreads are O(n²) per expiration. With ~20 expirations × ~200 strikes per expiration, a single (scanner_key, symbol) pair could generate ~20 × C(200,2) = ~400K candidates before the cap. The 50K cap catches this, but the loop iterates until it hits the cap, doing work that is thrown away.

**Impact**: Performance — unnecessary computation before hitting the cap. For SPY with many strike points and weekly expirations, this could be significant.

**Recommendation**: Consider pre-filtering strikes more aggressively in Phase A (tighter distance windows) or computing an estimated combination count before entering the construction loop.

---

### F-3B-10 — LOW: _safe_float Coercion Hides Data Quality Issues in Sorting

**Evidence**: `_safe_float(None)` returns `0.0`. This means:
- A candidate with `EV=None` (no EV computed, e.g., calendar) sorts identically to `EV=0.0` (computed EV that happens to be zero)
- A candidate with `RoR=None` sorts identically to `RoR=0.0`
- There is no way to distinguish "not computed" from "computed as zero" in the ranking

**Impact**: Low — calendars are the main case and they are systematically at the bottom regardless. But if a vertical had a math error resulting in EV=None, it would silently sort to the bottom rather than being flagged as a data quality issue.

**Recommendation**: Use `float('-inf')` for None values in the sort key, or filter out EV=None candidates before ranking and handle them separately.

---

### F-3B-11 — LOW: Scanner Count (44 runs) Includes Redundant Calendar/Diagonal Work

**Evidence**: 4 calendar/diagonal scanner_keys × 4 symbols = 16 runs that produce candidates with EV=None. These candidates will never reach the top-30 in practice (see F-3B-01). This is ~36% of all scanner runs producing no actionable output.

**Impact**: Wasted API calls to Tradier and wasted computation. Each run fetches the full chain and runs all 6 phases.

**Risk**: Low — correctness is not affected. This is a performance/cost issue.

**Recommendation**: Either fix the ranking to include calendars (F-3B-01) or conditionally skip calendar/diagonal scanners until their ranking issue is resolved.

---

## Summary

| Severity | Count | Key Theme |
|----------|-------|-----------|
| HIGH | 3 | Calendar exclusion from ranking, no model analysis, no regime gating |
| MEDIUM | 5 | EV ranking bias, credibility gate gaps, phase overlap, no cross-family dedup, sequential check counts |
| LOW | 3 | Generation cap perf, _safe_float hiding data quality, redundant calendar scanner runs |
| **Total** | **11** | |

### Pipeline Health Assessment

The options pipeline is **well-architected** with clear phase separation, comprehensive diagnostics, and honest handling of unknowns (calendar EV=None rather than fabricated). The 6-phase V2 scanner design is a significant improvement over V1, with structured reject reasons and phase counts at every boundary.

**Primary concerns**:
1. Calendar/diagonal strategies are functionally invisible due to EV=None → 0.0 coercion in ranking — an entire strategy family is dead on arrival
2. No model analysis means no qualitative judgment — regime-inappropriate trades rank purely on math
3. The credibility gate is too thin — it catches worthless options but misses low-quality-but-technically-valid trades (tiny widths, illiquid, unfavorable bid-ask spreads)

### Comparison to Stock Pipeline (3A)

| Aspect | Stock Pipeline | Options Pipeline |
|--------|---------------|-----------------|
| Stages | 8 (incl. model analysis) | 5 (no model analysis) |
| Universe | ~196 symbols, 4 scanners | 4 symbols, 11 scanner variants |
| Filtering depth | Scanner filters + quality gate + model PASS/BUY | Phase C/D/D2/E + credibility gate (3 checks) |
| Ranking | setup_quality (scanner composite) → model_score | EV → RoR → symbol |
| Regime awareness | Enrichment only (no gating) | Enrichment only (no gating) |
| Model analysis | Yes (LLM review, BUY/PASS, model_score) | No |
| Quality floor | MIN_SETUP_QUALITY=30 | MIN_PREMIUM=$0.05 + POP<0.995 |
| Top-N cap | 20 (Stage 5) → 10 (Stage 7b) | 30 (Stage 5) |
