# BenTrade Foundation Audit — Pass 1 Findings Report
## Data Integrity Layer: Consolidated Analysis

**Date**: 2026-03-20
**Auditor**: Claude (synthesis of 6 Copilot-generated audit documents)
**Scope**: Every data path from external API → engine/scanner input

---

## Executive Assessment

Your data layer is **structurally sound but operationally porous**. The architecture makes the right choices — single source of truth (Tradier), metric envelopes with freshness metadata, None-over-zero policy, immutable rejection codes. But there are systematic gaps in how freshness, proxy status, and temporal alignment are enforced as data flows through the pipeline. The result is that **engines and scanners operate on data they believe is current and direct, when it may be stale and proxy-derived**.

The good news: these are fixable without architectural rework. The patterns are correct — you just need to close the enforcement gaps.

---

## Severity 1: Systemic Issues (Affect Every Trade Decision)

### S1. The Freshness Pipeline Is Structurally Broken

**What happens**: `fetched_at` is set when the metric envelope is *constructed*, not when the API was *called*. Since the Market Context Service rebuilds envelopes on every cache miss (30s cycle), `fetched_at` always shows approximately "now" — even when the underlying FRED data is served from a 300-second cache that may itself contain day-old observations.

**Downstream impact**: The MI Runner's `_build_freshness_section()` computes staleness from `fetched_at`, so its tiers (fresh/warning/stale) almost always show "fresh" during a normal run. This makes the entire freshness monitoring system a no-op in practice.

**The real fix**: Staleness should be computed from `observation_date` (the date the data was observed in the market), not `fetched_at`. For Tradier intraday data that lacks `observation_date`, freshness should be gated by market-hours awareness — a Friday close served on Monday isn't "intraday."

**Files affected**: `market_context_service.py` (_metric function), `market_intelligence_runner.py` (_build_freshness_section), all 5 data providers.

### S2. Engines Receive Bare Values With No Metadata

**What happens**: Every data provider calls `_extract_value()` which strips the metric envelope down to a bare float. Source, freshness, observation_date, proxy status — all discarded. The engine receives a dict of numbers with no way to know which are current, which are stale, and which are proxied.

**Downstream impact**: An engine scores today's live VIX alongside last month's copper price and a fabricated VIX term structure estimate, treating all three as equally trustworthy current observations. Pillar scores, composite scores, and confidence values are all computed without awareness of input quality.

**The real fix**: Either pass a lightweight quality tag alongside each value (e.g., `{"value": 25.1, "age_days": 0, "is_proxy": False}`), or compute a per-pillar data-quality multiplier in the data provider that the engine uses to weight its confidence.

### S3. Three Incompatible Freshness Vocabularies

**What exists**:
- Metric envelope: `"intraday"` / `"eod"` / `"delayed"` (describes source type, not age)
- MI Runner tiers: `"fresh"` / `"warning"` / `"stale"` (computed from flawed `fetched_at`)
- Confidence framework: `"live"` / `"recent"` / `"stale"` / `"very_stale"` (has correct penalty tables but isn't wired in)

**Impact**: The confidence framework defines the right penalty structure but is disconnected from the actual data flow. The MI Runner uses a broken freshness check. The metric envelope's labels describe source type, not data currency. No single component has both the right data AND the right logic to enforce freshness.

---

## Severity 2: Engine-Specific Data Quality Issues

### E1. Flows & Positioning: 12 Proxy Metrics From 1 VIX Input

This was already known but the audit confirms the full scope: every metric in the Flows engine is a clamped linear function of VIX. The 5-pillar structure creates an illusion of independent analysis, but mathematically the engine has 1 degree of freedom. The SIGNAL_PROVENANCE system correctly labels these as proxies, and confidence is penalized — but the engine still produces a score (0-100) with pillar breakdowns that look like real analysis to downstream consumers.

**What to do**: Short-term, reduce this engine's weight in the composite and cap its confidence lower (the current ~55 cap is reasonable but the weight should reflect the single-source reality). Medium-term, replace even one metric with real data (CBOE put/call ratio is free and daily) to break the single-source dependency.

### E2. Volatility Engine: Proxy Laundering (No SIGNAL_PROVENANCE)

The volatility engine uses 5+ proxy metrics (`vix_rank_30d`, `vix_percentile_1y`, `vix_2nd_month`, `vix_3rd_month`, `option_richness`, `premium_bias`) but has **no SIGNAL_PROVENANCE dict**. The `engine_output_contract.py` normalizer reports `proxy_count=0` for this engine — factually incorrect.

The term structure metrics (`vix_2nd_month`, `vix_3rd_month`) are the most concerning: they're fabricated heuristics (not market data) that feed Pillar 2 (25% weight) as if they were real VIX futures prices. The code has a comment acknowledging this, but no downstream consumer knows.

**What to do**: Add SIGNAL_PROVENANCE to the volatility engine. At minimum, tag `vix_rank_30d`, `vix_percentile_1y`, `vix_2nd_month`, `vix_3rd_month` as proxies. Consider moving option_richness and premium_bias computation INTO the engine (from the data provider) so the engine works from raw components.

### E3. News Engine: No Confidence Mechanism At All

The news sentiment engine has no SIGNAL_PROVENANCE, no proxy labeling, and no confidence computation. Its headline_sentiment component (30% weight) is keyword-matching — a crude proxy for real NLP sentiment. Four of its six components are PROXY or PROXY-OF-PROXY by classification. The engine reports whatever score it computes with no quality metadata.

### E4. Cross-Series Date Mismatch (Yield Curve Spread)

The yield curve spread (DGS10 - DGS2) is computed without verifying both series share the same observation date. If DGS10 publishes Monday's value but DGS2 is delayed, the spread silently mixes data from different days. Same risk applies to any derived metric combining multiple FRED series.

---

## Severity 3: Scanner & Options Data Issues

### O1. POP Depends Entirely on Tradier Delta (No Fallback)

All options POP calculations use `1 - abs(short.delta)` or variants. If Tradier returns missing deltas (after hours, illiquid contracts), POP is None, and EV is None. Phase D doesn't gate on delta presence — contracts pass hygiene checks and enter ranking with no EV. No fallback POP calculation exists (e.g., Black-Scholes with IV).

### O2. Butterfly POP Overestimates Profit Probability

The butterfly POP formula measures P(stock finishes between outer strikes), but max profit only occurs near the center. For wide butterflies, the overestimation can be 2-3x. EV inherits this bias, potentially ranking butterflies too favorably.

### O3. No Chain Completeness Check

If Tradier returns a chain with 3 contracts instead of 300, no alarm fires. The scanner proceeds with whatever it has. For index ETFs this is unlikely but not impossible during API issues.

### O4. Bid=0 Passes Validation

A contract with bid=0 and ask=0.05 is considered valid. This produces $0 credit in spread calculations. The credibility gate catches penny premiums (net credit < $0.05) but individual legs with bid=0 can still participate in multi-leg constructions.

---

## Severity 4: Operational Issues

### P1. No Market Hours Awareness in Data Pipeline

`_is_market_open()` exists only in the risk module. The MI Runner, Market Context Service, and scanners have no hours gate. This means:
- Aggressive re-fetching during weekends/holidays (Tradier gets hit every 10s for quote data that hasn't changed since Friday)
- Friday's VIX close labeled "intraday" on Monday morning
- No TTL adjustment (30s cache is the same 24/7)

### P2. No Rate Limiting on TradierClient

The breadth engine fires ~150 bar requests per MI cycle with only a Semaphore(10) in the provider. No client-level rate limiter exists. HTTP 429 is detected but NOT retried — data simply goes missing for that cycle.

### P3. FRED Data Has No Staleness Tolerance (Except Copper)

Only copper (PCOPPUSDM) has an observation_date staleness check. All other FRED series — yields, credit spreads, oil, USD index — could be days stale with zero detection, zero confidence penalty, and zero downstream signal.

### P4. Regime Service Bypasses FredClient Cache

The regime service calls FRED directly via `request_json()`, bypassing the 300s TTL cache. This creates redundant API calls and means the regime service may see different "latest" values than the Market Context Service for the same FRED series.

---

## What's Working Well (Keep These Patterns)

1. **None-over-zero policy**: Universally enforced. Missing data is None, never fabricated. This is the single most important data integrity practice and you've got it right everywhere.

2. **Immutable rejection taxonomy**: Options V2 pipeline tracks every rejection with stable codes. No silent drops. This is excellent for debugging and auditing.

3. **Metric envelope design**: The `_metric()` envelope structure (value, source, freshness, observation_date, fetched_at) is the right abstraction. The problem isn't the design — it's that the metadata gets stripped before engines see it.

4. **SIGNAL_PROVENANCE** (where it exists): The flows and cross-asset engines honestly label their proxies. This pattern just needs to be extended to volatility and news.

5. **Chain validation**: The options chain normalization is thorough — bid/ask validation, delta clamping, IV normalization, inverted quote rejection. This is production-grade.

6. **Scanner formula correctness**: All technical indicators (RSI, SMA, ATR, RV, Z-score) use standard textbook formulas. No computational errors detected. Options math (vertical, iron condor, butterfly, calendar) is correct for each structure type.

7. **Anti-anchoring exclusions**: Model analysis correctly strips composite scores, labels, and narratives before the LLM sees engine data. The pattern is well-implemented (though pillar_scores being included is a partial anchor — noted as medium concern).

---

## Recommended Fix Priority

### Fix Now (Before Expanding Any Features)

**FN-1: Observation-date-based staleness for all FRED series**
Add `_days_stale()` check (already exists for copper) to every FRED metric. Apply a confidence penalty when staleness exceeds 2 business days. This is a small code change with high impact — it prevents stale macro data from silently corrupting engine scores.

**FN-2: Add SIGNAL_PROVENANCE to volatility engine**
Tag `vix_rank_30d`, `vix_percentile_1y`, `vix_2nd_month`, `vix_3rd_month`, `option_richness`, `premium_bias` as proxies. This fixes the `proxy_count=0` misreporting and makes the dashboard honest about data quality.

**FN-3: Delta-presence gate in options Phase D**
Add a check: if the short leg(s) have delta=None, reject the candidate with a specific code (`v2_missing_delta`). This prevents POP=None candidates from entering ranking and ensures every ranked candidate has computable EV.

### Fix Soon (Next 2-3 Sprints)

**FS-1: Pass data-quality tags through to engines**
Either add a lightweight `_quality` companion to each pillar value, or compute a per-pillar `data_freshness_score` in the data provider that the engine can use to modulate confidence. This is the architectural fix for S2 (bare values).

**FS-2: Fix `fetched_at` semantics**
Either (a) set `fetched_at` at the actual API call time (inside the client, before caching), or (b) stop using `fetched_at` for staleness and switch all freshness checks to `observation_date` comparison. Option (b) is cleaner.

**FS-3: Market hours awareness**
Add an `is_market_hours()` check to the MI Runner. During off-hours: extend cache TTLs (no point re-fetching every 5 min on weekends), downgrade "intraday" freshness labels to "eod" or "closed", and optionally skip MI runs entirely on weekends.

**FS-4: Unify freshness vocabulary**
Map between the three systems: metric envelope labels → MI runner tiers → confidence framework penalties. A single `compute_data_currency()` function that takes `observation_date` + `source_type` + `current_time` and returns a standardized freshness tier with the correct confidence penalty.

### Fix Later (Solid Foundation Work)

**FL-1: Cross-series date alignment for derived metrics**
Before computing yield_curve_spread, verify DGS10 and DGS2 share the same observation_date. If they don't, use the older date's value for both or flag the mismatch.

**FL-2: Add input validation to engine entry points**
Lightweight schema check before each engine runs: assert expected keys present, assert numeric types, assert values within plausible ranges. This catches upstream changes that would otherwise produce silent garbage.

**FL-3: Move pre-computed signals from vol data provider into engine**
Let the engine receive raw components (put_skew_25d, cboe_skew, vix_rank_30d, iv_30d, rv_30d) and compute tail_risk, option_richness, premium_bias internally. This gives the engine full control over threshold logic.

**FL-4: Client-level rate limiter for Tradier**
Add an `asyncio.Semaphore` or token bucket at the TradierClient level to prevent burst API calls. Add 429 retry with backoff.

---

## What This Means for Pass 2

The data integrity layer is usable but has known leaks. For the Computation pass (Pass 2), I'll need to evaluate whether the scoring formulas produce sensible outputs given these input quality issues. Specifically:

- Do pillar scores respond correctly when inputs are at extremes?
- Do composite weights make sense given that some pillars are proxy-heavy?
- Does the confidence framework correctly penalize the known data quality issues?
- Are there scoring interactions where stale data produces systematically biased scores?

The key question for Pass 2: **given what we now know about data quality, are the scores calibrated to reflect reality, or are they producing confident-looking numbers from uncertain inputs?**
