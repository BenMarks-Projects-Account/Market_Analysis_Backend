# BenTrade Audit Fix — Batch 8 Copilot Prompts
## Final Fix Soon Items (FS-3, FS-4, FS-1, FN-5)

> **Instructions**: Run in order. FS-3 first (market hours — foundation for FS-4), then FS-4 (unified freshness — uses FS-3), then FS-1 (data quality tags — uses FS-4's vocabulary), then FN-5 (composite ranking — independent but saved for last since the per-scanner-key budget already partially addresses it).

---

## Prompt 1 of 4: FS-3 — Market Hours Awareness

```
TASK: Create a shared market_hours utility and wire it into the MI Runner and Market Context Service so the system knows when the US market is open, avoids unnecessary API calls during off-hours, and correctly labels weekend/holiday data as stale instead of "intraday."

CONTEXT:
- app/trading/risk.py has a basic _is_market_open() that uses UTC hour approximation with no DST or holiday handling
- The MI Runner runs every 5 minutes 24/7 — hitting Tradier for quote data on weekends when nothing has changed since Friday
- Market Context Service labels Tradier quotes as "intraday" regardless of whether the market is open — Friday's VIX close shows as "intraday" on Monday morning
- Python 3.9+ has zoneinfo in the standard library (no pytz needed)

CREATE: app/utils/market_hours.py

```python
"""US equity market hours awareness — shared utility for all BenTrade services."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

# Regular session: 9:30 AM - 4:00 PM Eastern
_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)

# Extended hours (pre-market + after-hours): 4:00 AM - 8:00 PM Eastern
_EXTENDED_OPEN = time(4, 0)
_EXTENDED_CLOSE = time(20, 0)

# NYSE holidays for 2025-2027 (update annually)
# Format: set of (month, day) for fixed holidays + computed dates for floating ones
_FIXED_HOLIDAYS = {
    # 2026
    (2026, 1, 1),   # New Year's Day
    (2026, 1, 19),  # MLK Day
    (2026, 2, 16),  # Presidents' Day
    (2026, 4, 3),   # Good Friday
    (2026, 5, 25),  # Memorial Day
    (2026, 6, 19),  # Juneteenth
    (2026, 7, 3),   # Independence Day (observed)
    (2026, 9, 7),   # Labor Day
    (2026, 11, 26), # Thanksgiving
    (2026, 12, 25), # Christmas
}

def _is_holiday(d: date) -> bool:
    """Check if a date is an NYSE holiday."""
    return (d.year, d.month, d.day) in _FIXED_HOLIDAYS


def is_market_open(now: datetime | None = None) -> bool:
    """Check if the US equity market is currently in regular session.
    
    Returns True during regular trading hours (9:30 AM - 4:00 PM ET)
    on non-holiday weekdays.
    """
    now_et = (now or datetime.now(_ET)).astimezone(_ET) if now else datetime.now(_ET)
    
    # Weekend
    if now_et.weekday() >= 5:
        return False
    
    # Holiday
    if _is_holiday(now_et.date()):
        return False
    
    # Check time
    current_time = now_et.time()
    return _MARKET_OPEN <= current_time <= _MARKET_CLOSE


def is_extended_hours(now: datetime | None = None) -> bool:
    """Check if we're in extended hours (pre-market or after-hours).
    
    Returns True during 4:00 AM - 9:30 AM or 4:00 PM - 8:00 PM ET
    on non-holiday weekdays.
    """
    now_et = (now or datetime.now(_ET)).astimezone(_ET) if now else datetime.now(_ET)
    
    if now_et.weekday() >= 5 or _is_holiday(now_et.date()):
        return False
    
    current_time = now_et.time()
    pre_market = _EXTENDED_OPEN <= current_time < _MARKET_OPEN
    after_hours = _MARKET_CLOSE < current_time <= _EXTENDED_CLOSE
    return pre_market or after_hours


def is_trading_day(d: date | None = None) -> bool:
    """Check if a date is a trading day (weekday, not a holiday)."""
    d = d or date.today()
    return d.weekday() < 5 and not _is_holiday(d)


def market_status(now: datetime | None = None) -> str:
    """Return current market status string.
    
    Returns one of: "open", "extended", "closed"
    """
    if is_market_open(now):
        return "open"
    if is_extended_hours(now):
        return "extended"
    return "closed"


def last_close_date(now: datetime | None = None) -> date:
    """Return the date of the most recent market close.
    
    On a trading day after 4 PM ET: returns today.
    On a trading day before 4 PM ET: returns previous trading day.
    On weekends/holidays: returns the most recent Friday (or prior trading day).
    """
    now_et = (now or datetime.now(_ET)).astimezone(_ET) if now else datetime.now(_ET)
    d = now_et.date()
    
    # If today is a trading day and market has closed, today is the last close
    if is_trading_day(d) and now_et.time() >= _MARKET_CLOSE:
        return d
    
    # Walk backwards to find the most recent trading day
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d
```

WIRE INTO MI RUNNER:

Find app/services/data_population_service.py (or wherever the MI Runner loop is, approximately L118-131). The loop currently runs every ~5 minutes unconditionally.

Add market hours awareness:

```python
from app.utils.market_hours import market_status

# In the MI Runner loop, before running a cycle:
status = market_status()
if status == "closed":
    # Off-hours: extend interval to 30 minutes (no need to re-fetch stale data)
    _log.debug("event=mi_runner_offhours status=%s next_run_in=30m", status)
    await asyncio.sleep(30 * 60)  # 30 minutes
    continue
elif status == "extended":
    # Extended hours: run less frequently (10 minutes)
    interval = 10 * 60
else:
    # Market open: normal interval (5 minutes or whatever the current default is)
    interval = current_interval  # Preserve existing interval
```

WIRE INTO MARKET CONTEXT SERVICE:

Find app/services/market_context_service.py, specifically the _metric() function (approximately L57-68) where freshness is assigned.

```python
from app.utils.market_hours import market_status

# In _metric(), when assigning freshness for Tradier-sourced data:
if source == "tradier":
    status = market_status()
    if status == "open":
        freshness = "intraday"
    elif status == "extended":
        freshness = "extended"
    else:
        freshness = "prior_close"  # NOT "intraday" — data is from the last session
```

WIRE INTO risk.py:

Replace the inline _is_market_open() in app/trading/risk.py:

```python
from app.utils.market_hours import is_market_open
# Remove the local _is_market_open() function
# Update any callers to use the imported version
```

VERIFICATION:
- On a weekday during market hours: market_status() returns "open", MI Runner runs normally
- On a Saturday: market_status() returns "closed", MI Runner sleeps 30 minutes, Tradier data labeled "prior_close"
- On Christmas (if testing): same as weekend
- DST transition: verify 9:30 AM ET maps correctly in both EST and EDT

ACCEPTANCE CRITERIA:
- Shared market_hours.py with is_market_open(), is_extended_hours(), market_status(), last_close_date()
- DST-aware via zoneinfo (America/New_York)
- NYSE holiday calendar for 2026 (at minimum)
- MI Runner extends interval to 30min during off-hours
- Market Context Service labels Tradier data as "prior_close" when market is closed
- risk.py uses shared utility instead of inline implementation
```

---

## Prompt 2 of 4: FS-4 — Unify Freshness Vocabulary

```
TASK: Create a single compute_data_currency() function that replaces three incompatible freshness classification systems with one unified vocabulary tied to the confidence framework's penalty table.

CONTEXT:
Three systems exist:
1. Metric envelope: "intraday" / "eod" / "delayed" (describes source type, not age)
2. MI Runner _build_freshness_section(): "fresh" / "warning" / "stale" (from fetched_at — broken per FS-2)
3. Confidence framework: "live" / "recent" / "stale" / "very_stale" (has penalty table but isn't wired in)

We need ONE function that all three callers use, producing the confidence framework's vocabulary.

ADD TO: app/services/data_quality_utils.py (where days_stale() from FN-1 already lives)

```python
from app.utils.market_hours import market_status, is_trading_day

# Confidence penalty table (matches confidence_framework.py)
FRESHNESS_PENALTIES = {
    "live":       0.00,   # Real-time data during market hours
    "recent":     0.00,   # Within 1 trading day
    "delayed":    0.03,   # 2-3 calendar days
    "stale":      0.08,   # 4-7 calendar days
    "very_stale": 0.15,   # 8+ calendar days
    "unknown":    0.05,   # Can't determine freshness
}


def compute_data_currency(
    *,
    observation_date: str | None = None,
    source_type: str = "unknown",
    is_market_open: bool | None = None,
) -> dict:
    """Compute unified data freshness tier and confidence penalty.
    
    Args:
        observation_date: ISO date string of when the data was observed in the market
        source_type: "tradier", "fred", "finnhub", "polygon", "derived"
        is_market_open: Override for market status (None = auto-detect via market_hours)
    
    Returns:
        {"tier": str, "penalty": float, "age_days": int|None, "source_type": str}
    """
    if is_market_open is None:
        is_market_open = market_status() == "open"
    
    # Tradier intraday data: freshness depends on market hours
    if source_type == "tradier":
        if is_market_open:
            return {"tier": "live", "penalty": 0.00, "age_days": 0, "source_type": source_type}
        else:
            # Off-hours: Tradier data is from the last session
            return {"tier": "recent", "penalty": 0.00, "age_days": 0, "source_type": source_type}
    
    # FRED and other observation_date-based sources
    if observation_date:
        age = days_stale(observation_date)
        if age is None:
            return {"tier": "unknown", "penalty": 0.05, "age_days": None, "source_type": source_type}
        
        # Monthly series (like copper) tolerate more staleness
        is_monthly = source_type == "fred_monthly"
        
        if age <= 1:
            tier = "live" if is_market_open else "recent"
        elif age <= 3:
            tier = "recent"
        elif age <= 7:
            tier = "delayed" if is_monthly else "stale"
        elif age <= 14:
            tier = "stale"
        else:
            tier = "very_stale"
        
        return {
            "tier": tier,
            "penalty": FRESHNESS_PENALTIES.get(tier, 0.05),
            "age_days": age,
            "source_type": source_type,
        }
    
    # No observation_date available
    return {"tier": "unknown", "penalty": 0.05, "age_days": None, "source_type": source_type}
```

WIRE INTO MI Runner _build_freshness_section():

Replace the existing tier logic that uses fetched_at with compute_data_currency():

```python
from app.services.data_quality_utils import compute_data_currency

# For each metric in the freshness section:
currency = compute_data_currency(
    observation_date=metric.get("observation_date"),
    source_type="tradier" if metric.get("source") == "tradier" else "fred",
)
freshness_items[key] = {
    "tier": currency["tier"],
    "penalty": currency["penalty"],
    "age_days": currency["age_days"],
}
```

WIRE INTO Market Context Service _metric():

When building metric envelopes, include the unified tier:

```python
currency = compute_data_currency(
    observation_date=obs_date,
    source_type="tradier" if source == "tradier" else "fred",
)
metric["freshness_tier"] = currency["tier"]
metric["freshness_penalty"] = currency["penalty"]
```

VERIFICATION:
- FRED data from today during market hours → tier="live", penalty=0.00
- FRED data from 3 days ago → tier="recent", penalty=0.00
- FRED data from 5 days ago → tier="stale", penalty=0.08
- Tradier during market hours → tier="live", penalty=0.00
- Tradier during weekend → tier="recent", penalty=0.00
- Unknown source, no observation_date → tier="unknown", penalty=0.05

ACCEPTANCE CRITERIA:
- Single compute_data_currency() in data_quality_utils.py
- Returns: tier (from confidence framework vocabulary), penalty, age_days, source_type
- MI Runner uses this instead of its own tier logic
- Market Context Service includes unified tier in metric envelopes
- FRESHNESS_PENALTIES dict matches confidence_framework.py
- Market hours awareness integrated (Tradier during off-hours ≠ "intraday")
```

---

## Prompt 3 of 4: FS-1 — Data Quality Tags Through to Engines

```
TASK: Add a shared _extract_quality() function and wire it into all data providers so engines receive data quality metadata alongside their numeric inputs. This is the architectural fix that lets engines modulate confidence based on actual input freshness and proxy status.

CONTEXT:
- FN-1 (Batch 3) added days_stale() utility
- FS-4 (this batch, Prompt 2) added compute_data_currency() with unified tiers
- Currently all 5 data providers call _extract_value() which strips the metric envelope to a bare float
- Engines receive numbers with zero metadata about freshness, source, or proxy status
- We want to add a companion data_quality dict WITHOUT changing the engine input interface

ADD TO: app/services/data_quality_utils.py (alongside existing utilities)

```python
def extract_quality(metric: dict | None) -> dict:
    """Extract data-quality metadata from a metric envelope.
    
    Works alongside _extract_value() — call both on the same metric:
      value = _extract_value(metric)
      quality = extract_quality(metric)
    
    Returns:
        {"source": str, "tier": str, "penalty": float, "age_days": int|None,
         "is_proxy": bool, "observation_date": str|None}
    """
    if not isinstance(metric, dict):
        return {
            "source": "unknown",
            "tier": "unknown",
            "penalty": 0.05,
            "age_days": None,
            "is_proxy": False,
            "observation_date": None,
        }
    
    source = metric.get("source", "unknown")
    obs_date = metric.get("observation_date")
    
    # Use unified freshness computation
    source_type = "tradier" if source == "tradier" else "fred" if "fred" in source.lower() else "unknown"
    currency = compute_data_currency(observation_date=obs_date, source_type=source_type)
    
    return {
        "source": source,
        "tier": currency["tier"],
        "penalty": currency["penalty"],
        "age_days": currency["age_days"],
        "is_proxy": False,  # Callers override per-metric where applicable
        "observation_date": obs_date,
    }
```

ALSO consolidate _extract_value() into the shared module:

```python
def extract_value(metric):
    """Extract the numeric value from a metric envelope.
    
    Handles both dict envelopes ({"value": 25.1, "source": "tradier", ...})
    and bare values (25.1).
    """
    if isinstance(metric, dict):
        return metric.get("value")
    return metric
```

WIRE INTO EACH DATA PROVIDER:

For each of the 5 data providers, add quality extraction alongside value extraction. The pattern:

```python
from app.services.data_quality_utils import extract_value, extract_quality

# In each provider, where pillar data is assembled:
# BEFORE:
pillar_data["vix_spot"] = _extract_value(metrics.get("vix_spot"))

# AFTER (value extraction unchanged, quality added):
pillar_data["vix_spot"] = extract_value(metrics.get("vix_spot"))
# ... (extract_value is the same, just imported from shared location)

# Build quality companion dict for this provider's output:
data_quality = {}
for key, metric in metrics.items():
    data_quality[key] = extract_quality(metric)

# Maximum age across all inputs (for engine confidence modulation)
age_days_list = [q["age_days"] for q in data_quality.values() if q["age_days"] is not None]
max_age = max(age_days_list) if age_days_list else None
total_penalty = sum(q["penalty"] for q in data_quality.values())

data_quality["_summary"] = {
    "max_age_days": max_age,
    "total_freshness_penalty": round(total_penalty, 3),
    "stale_count": sum(1 for q in data_quality.values() if q.get("tier") in ("stale", "very_stale")),
    "metric_count": len(data_quality) - 1,  # Exclude _summary itself
}
```

PASS TO ENGINE via source_meta or a new data_quality key:

```python
# In the provider's return dict (or wherever engine input assembly happens):
result["data_quality"] = data_quality
# OR add to existing source_meta:
result["source_meta"]["data_quality"] = data_quality
```

THEN in each engine's _compute_confidence(), add age-based penalty:

```python
# In _compute_confidence(), after existing penalties:
dq = kwargs.get("data_quality") or kwargs.get("source_meta", {}).get("data_quality", {})
summary = dq.get("_summary", {})
max_age = summary.get("max_age_days")
if max_age is not None and max_age > 3:
    age_penalty = min(15, (max_age - 3) * 2)  # -2 pts per day over 3, cap at -15
    confidence -= age_penalty
    penalties.append(f"data_staleness: max_age={max_age}d → -{age_penalty}")
```

IMPORTANT: This is ADDITIVE — engine input dicts (pillar_data) are UNCHANGED. The data_quality dict travels alongside them. Engines can choose to use it or ignore it. No engine scoring logic changes — only confidence computation optionally incorporates it.

Apply to these 5 providers:
1. app/services/cross_asset_macro_data_provider.py
2. app/services/flows_positioning_data_provider.py
3. app/services/liquidity_conditions_data_provider.py
4. app/services/volatility_options_data_provider.py
5. app/services/news_sentiment_service.py

VERIFICATION:
- Run MI Runner. Check engine outputs for data_quality in source_meta.
- An engine receiving 5-day-old FRED data should have a staleness penalty in its confidence.
- An engine receiving all fresh Tradier data should have no staleness penalty.

ACCEPTANCE CRITERIA:
- extract_quality() and extract_value() in shared data_quality_utils.py
- All 5 data providers consolidated to use shared functions (no more per-provider _extract_value copies)
- data_quality dict produced by each provider with per-metric quality + _summary
- Engine confidence computation incorporates max_age_days penalty
- Existing engine scoring completely unchanged (quality is additive metadata)
- Stale FRED data now produces visible confidence reduction
```

---

## Prompt 4 of 4: FN-5 — Composite Rank Score for Options (Within Scanner-Key Budgets)

```
TASK: Replace raw EV as the within-key ranking metric with a composite score that includes capital efficiency (EV/max_loss), liquidity, and POP. The per-scanner-key budget system (from the recent fix) already distributes slots across strategies — this improves the WITHIN-KEY ranking so the best trade per strategy surfaces first.

CONTEXT:
- ranking.py already has a proper composite formula: edge(0.30) + ror(0.22) + pop(0.20) + liquidity(0.18) + tqs(0.10)
- The recent per-scanner-key budget fix distributes slots across scanner keys
- Within each key, candidates are still sorted by raw EV desc → RoR desc
- We want to replace that with the composite rank from ranking.py

FILE: app/workflows/options_opportunity_runner.py

Find where candidates are sorted WITHIN each scanner-key budget (the per-key sorting from the recent fix). It looks approximately like:

```python
# Current within-key sort:
key_candidates.sort(key=lambda c: (
    -_safe_float((c.get("math") or {}).get("ev")),
    -_safe_float((c.get("math") or {}).get("ror")),
))
```

OPTION A (preferred): Import ranking.py's compute_rank_score() and use it:

```python
from app.services.ranking import compute_rank_score  # Check actual function name and import path

# For each scanner key's candidates:
for cand in key_candidates:
    math = cand.get("math") or {}
    cand["_rank_score"] = compute_rank_score(
        ev=math.get("ev"),
        max_loss=math.get("max_loss"),
        ror=math.get("ror"),
        pop=math.get("pop"),
        # Liquidity inputs
        short_oi=cand.get("legs", [{}])[0].get("open_interest"),
        short_vol=cand.get("legs", [{}])[0].get("volume"),
        short_spread_pct=cand.get("legs", [{}])[0].get("spread_pct"),
    )

key_candidates.sort(key=lambda c: -(c.get("_rank_score") or 0))
```

NOTE: Check the actual signature of compute_rank_score() in ranking.py. It may take different parameter names or expect a trade dict. Adapt to match.

OPTION B (if ranking.py interface is too complex): Implement a simplified composite inline:

```python
def _compute_simple_rank(cand):
    """Simplified composite rank: capital efficiency + POP + RoR."""
    math = cand.get("math") or {}
    ev = _safe_float(math.get("ev")) or 0
    max_loss = _safe_float(math.get("max_loss")) or 1
    pop = _safe_float(math.get("pop")) or 0
    ror = _safe_float(math.get("ror")) or 0
    
    # Capital efficiency (EV per dollar risked) — most important
    ev_to_risk = ev / max(abs(max_loss), 1)
    
    # Normalize components to 0-1 range
    ev_to_risk_norm = min(max(ev_to_risk / 0.10, 0), 1)  # 0-10% range normalized
    pop_norm = min(max((pop - 0.50) / 0.45, 0), 1)        # 50-95% range normalized
    ror_norm = min(max(ror / 50, 0), 1)                    # 0-50% range normalized
    
    # Composite: capital efficiency weighted highest
    return ev_to_risk_norm * 0.40 + pop_norm * 0.35 + ror_norm * 0.25

# Sort within each key:
key_candidates.sort(key=lambda c: -_compute_simple_rank(c))
```

ALSO: Include the rank_score in the candidate output for transparency:
```python
cand["rank_score"] = round(cand.get("_rank_score", 0), 4)
```

VERIFICATION:
- A $5-wide spread with EV=$5 and max_loss=$400 (EV/risk=1.25%) should rank above a $50-wide spread with EV=$8 and max_loss=$4,200 (EV/risk=0.19%), even though the latter has higher raw EV.
- High-POP trades should rank above low-POP trades when EV/risk is similar.
- rank_score visible in candidate output for debugging.

ACCEPTANCE CRITERIA:
- Within-key ranking uses composite score (not raw EV)
- Capital efficiency (EV/max_loss) is the primary ranking factor
- POP and RoR contribute to ranking
- $5-wide with better EV/risk outranks $50-wide with higher raw EV
- rank_score included in candidate output
- Calendar candidates still use separate ranking (net_debit/max_loss from FS-20)
```

---

## After Batch 8

**All 22 Fix Soon items complete:**
- FS-1 through FS-22 ✓
- FN-1 through FN-14 ✓
- FN-5 (composite ranking) ✓

**Remaining: 29 Fix Later items** — these are foundation hardening (cross-series alignment, input validation, engine refactoring, client rate limiting, etc.). They improve robustness but aren't blocking trade quality or data integrity.

At this point you have a solid platform with:
- Correct data freshness tracking end-to-end
- Engine confidence reflecting actual input quality
- Market hours awareness reducing wasted API calls
- Composite ranking surfacing the best risk-adjusted trades
- All the scanner, pipeline, and portfolio work from Phases 1-4

The Fix Later items are good engineering but diminishing returns compared to what's been done. I'd suggest running the system for a few days, observing the outputs, and prioritizing Fix Later items based on what you actually see going wrong.
