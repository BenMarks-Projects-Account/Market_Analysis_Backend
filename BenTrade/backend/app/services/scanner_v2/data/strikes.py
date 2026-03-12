"""Strike-window narrowing — distance, moneyness, and option-type filters.

Operates on a list of V2OptionContract (already expiry-narrowed) and
produces V2StrikeEntry / V2ExpiryBucket structures grouped by
expiration.

Narrowing stages (applied in order)
────────────────────────────────────
1. **Option-type filter** — keep only puts, calls, or both.
2. **Moneyness filter** — OTM / ITM / ATM (within 0.5% of spot).
3. **Distance filter** — strike within [distance_min_pct, distance_max_pct]
   of underlying price.
4. **Deduplication** — one contract per (expiration, strike, option_type);
   keep highest open interest.

Reason codes
────────────
- ``wrong_type``         — option_type excluded by request
- ``wrong_moneyness``    — ITM when OTM requested, etc.
- ``distance_below_min`` — strike too close to underlying
- ``distance_above_max`` — strike too far from underlying
- ``duplicate_dropped``  — duplicate (strike, exp, type) removed
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from app.services.scanner_v2.data.contracts import (
    V2ExpiryBucket,
    V2NarrowingDiagnostics,
    V2NarrowingRequest,
    V2OptionContract,
    V2StrikeEntry,
)


# ═══════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════

def narrow_strikes(
    contracts: list[V2OptionContract],
    request: V2NarrowingRequest,
    underlying_price: float,
    diag: V2NarrowingDiagnostics | None = None,
) -> dict[str, V2ExpiryBucket]:
    """Apply strike-window narrowing and return expiry-bucketed results.

    Parameters
    ----------
    contracts
        Contracts already narrowed to valid expirations.
    request
        Narrowing parameters.
    underlying_price
        Spot price for distance / moneyness calculations.
    diag
        Diagnostics to populate (optional).

    Returns
    -------
    dict[str, V2ExpiryBucket]
        Keyed by ISO expiration date, sorted by strike within each bucket.
    """
    drop_reasons: dict[str, int] = defaultdict(int)
    kept: list[V2OptionContract] = []

    # Stage 1: option-type filter
    after_type = _filter_option_type(contracts, request, drop_reasons)

    if diag is not None:
        diag.contracts_after_type_filter = len(after_type)

    # Stage 2+3: moneyness + distance filter
    for c in after_type:
        reason = _check_strike_window(c, request, underlying_price)
        if reason is not None:
            drop_reasons[reason] += 1
            continue
        kept.append(c)

    if diag is not None:
        diag.contracts_after_strike_filter = len(kept)

    # Stage 4: Group by expiration + deduplicate
    buckets = _group_and_dedup(kept, underlying_price, drop_reasons)

    # Populate diagnostics
    if diag is not None:
        total_strikes = sum(b.strike_count for b in buckets.values())
        dropped_strikes = diag.total_unique_strikes - total_strikes if diag.total_unique_strikes else 0
        if dropped_strikes < 0:
            dropped_strikes = 0
        all_unique = set()
        for c in contracts:
            all_unique.add((c.expiration, c.strike, c.option_type))
        diag.total_unique_strikes = len(all_unique)
        diag.strikes_kept = total_strikes
        diag.strikes_dropped = diag.total_unique_strikes - total_strikes
        diag.strike_drop_reasons.update(drop_reasons)
        diag.duplicate_contracts_dropped = drop_reasons.get("duplicate_dropped", 0)
        diag.contracts_final = sum(b.strike_count for b in buckets.values())

    return buckets


# ═══════════════════════════════════════════════════════════════════
#  Internal stages
# ═══════════════════════════════════════════════════════════════════

def _filter_option_type(
    contracts: list[V2OptionContract],
    request: V2NarrowingRequest,
    drop_reasons: dict[str, int],
) -> list[V2OptionContract]:
    """Keep only contracts matching requested option types."""
    if not request.option_types:
        return contracts  # no filter — keep both

    wanted = {t.lower() for t in request.option_types}
    kept: list[V2OptionContract] = []
    for c in contracts:
        if c.option_type in wanted:
            kept.append(c)
        else:
            drop_reasons["wrong_type"] += 1
    return kept


def _check_strike_window(
    c: V2OptionContract,
    request: V2NarrowingRequest,
    underlying_price: float,
) -> str | None:
    """Return drop reason if contract fails moneyness or distance, else None."""
    # Moneyness filter
    if request.moneyness is not None:
        target = request.moneyness.lower()
        if target == "otm" and not c.is_otm(underlying_price):
            return "wrong_moneyness"
        if target == "itm" and not c.is_itm(underlying_price):
            return "wrong_moneyness"
        if target == "atm":
            dist = c.distance_pct(underlying_price)
            if dist is not None and dist > 0.005:  # >0.5% from spot
                return "wrong_moneyness"

    # Distance filter
    if request.distance_min_pct is not None or request.distance_max_pct is not None:
        dist = c.distance_pct(underlying_price)
        if dist is None:
            return "distance_unknown"
        if request.distance_min_pct is not None and dist < request.distance_min_pct:
            return "distance_below_min"
        if request.distance_max_pct is not None and dist > request.distance_max_pct:
            return "distance_above_max"

    return None


def _group_and_dedup(
    contracts: list[V2OptionContract],
    underlying_price: float,
    drop_reasons: dict[str, int],
) -> dict[str, V2ExpiryBucket]:
    """Group contracts into V2ExpiryBucket by expiration and deduplicate.

    For each (expiration, strike, option_type) key, keep the contract
    with the highest open_interest.
    """
    from datetime import date

    today = date.today()

    # Group by expiration
    by_exp: dict[str, list[V2OptionContract]] = defaultdict(list)
    for c in contracts:
        by_exp[c.expiration].append(c)

    buckets: dict[str, V2ExpiryBucket] = {}

    for exp in sorted(by_exp.keys()):
        exp_contracts = by_exp[exp]

        # Deduplicate by (strike, option_type)
        best: dict[tuple[float, str], V2OptionContract] = {}
        dupes = 0
        for c in exp_contracts:
            key = (c.strike, c.option_type)
            if key in best:
                # Keep higher OI
                existing_oi = best[key].open_interest or 0
                new_oi = c.open_interest or 0
                if new_oi > existing_oi:
                    best[key] = c
                dupes += 1
            else:
                best[key] = c

        if dupes:
            drop_reasons["duplicate_dropped"] += dupes

        # Build strike entries (sorted by strike)
        strike_entries: list[V2StrikeEntry] = []
        for (strike, _opt_type), contract in sorted(best.items()):
            strike_entries.append(
                V2StrikeEntry(
                    strike=strike,
                    contract=contract,
                ),
            )

        # Compute DTE
        try:
            exp_date = date.fromisoformat(exp)
            dte = (exp_date - today).days
        except (ValueError, TypeError):
            dte = 0

        # Determine option_type for bucket
        types_in_bucket = {c.option_type for c in best.values()}
        bucket_type = types_in_bucket.pop() if len(types_in_bucket) == 1 else None

        # Median IV
        ivs = [c.iv for c in best.values() if c.iv is not None]
        median_iv = round(statistics.median(ivs), 6) if ivs else None

        buckets[exp] = V2ExpiryBucket(
            expiration=exp,
            dte=dte,
            option_type=bucket_type,
            strikes=strike_entries,
            strike_count=len(strike_entries),
            median_iv=median_iv,
        )

    return buckets
