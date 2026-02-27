from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any

from app.services.ranking import compute_rank_score, safe_float
from app.utils.dates import dte_ceil
from common.quant_analysis import enrich_trades_batch

logger = logging.getLogger("bentrade.credit_spread")

# Tolerance: reject candidates whose net_credit is within this many dollars
# of the spread width.  Prevents near-zero-profit or floating-point-edge trades.
_EPSILON = 0.01

# Supported price-basis options for credit calculation.
# "mid"     → (bid+ask)/2 for each leg  (default — less execution-pessimistic)
# "natural" → short_bid − long_ask      (worst-case fill for the seller)
_VALID_CREDIT_BASES = frozenset({"mid", "natural"})
_DEFAULT_CREDIT_BASIS = "mid"

# Valid dataQualityMode values — controls how missing OI/volume is handled.
#   "strict"   – missing OI/volume rejects  (distinct DQ code)
#   "balanced" – missing OI/volume rejects  (distinct DQ code, same outcome as strict)
#   "lenient"  – allow missing OI/volume when bid-ask is tight & credit >= min_credit
_DATA_QUALITY_MODES = frozenset({"strict", "balanced", "lenient"})
_DEFAULT_DATA_QUALITY_MODE = "balanced"

# Default minimum credit ($) required to keep a trade alive under lenient DQ mode.
_DEFAULT_MIN_CREDIT_FOR_DQ_WAIVER = 0.10


# ---------------------------------------------------------------------------
# Centralised quote validation
# ---------------------------------------------------------------------------

def validate_quote(bid: float | None, ask: float | None) -> tuple[bool, str | None]:
    """Check a single leg's quote validity.

    Rules:
    - bid must be >= 0 (None → invalid)
    - ask must be > 0    (None → invalid)
    - ask must be >= bid (inverted market)
    - mid = (bid + ask) / 2 must be > 0

    Returns (is_valid, reason_or_none).
    """
    if bid is None:
        return False, "missing_bid"
    if ask is None:
        return False, "missing_ask"
    if bid < 0:
        return False, "negative_bid"
    if ask <= 0:
        return False, "zero_or_negative_ask"
    if ask < bid:
        return False, "inverted_market"
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return False, "zero_mid"
    return True, None


def validate_spread_quotes(
    short_bid: float | None,
    short_ask: float | None,
    long_bid: float | None,
    long_ask: float | None,
) -> tuple[bool, str | None]:
    """Validate both legs of a credit spread.

    Returns (is_valid, rejection_code_or_none).
    Rejection codes use the ``QUOTE_INVALID:`` prefix for granular tracking.
    """
    ok_short, short_reason = validate_quote(short_bid, short_ask)
    if not ok_short:
        return False, f"QUOTE_INVALID:short_leg:{short_reason}"

    ok_long, long_reason = validate_quote(long_bid, long_ask)
    if not ok_long:
        return False, f"QUOTE_INVALID:long_leg:{long_reason}"

    return True, None


class CreditSpreadStrategyPlugin:
    id = "credit_spread"
    display_name = "Credit Spread"

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def build_candidates(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        snapshots = inputs.get("snapshots") or []
        if not snapshots:
            snapshots = [inputs]
        payload = inputs.get("request") or {}

        # Read distance / width params once (same for all snapshots)
        distance_min = self._to_float(payload.get("distance_min")) or 0.01
        distance_max = self._to_float(payload.get("distance_max")) or 0.12
        width_min = self._to_float(payload.get("width_min"))
        width_max = self._to_float(payload.get("width_max"))
        if width_min is None:
            width_min = 1.0
        if width_max is None:
            width_max = 5.0
        if width_max < width_min:
            width_min, width_max = width_max, width_min
        base_widths = [1.0, 2.0, 3.0, 5.0, 10.0]
        available_widths = [w for w in base_widths if width_min <= w <= width_max]
        if not available_widths:
            available_widths = [2.0, 3.0, 5.0]

        # ── Center-first width ordering ("normal distribution" feel) ─────
        # Geometric mean of the range endpoints favors a central width;
        # widths are then sorted by increasing distance from that center.
        # Ties broken by width value for determinism.
        center = math.sqrt(max(width_min, 0.5) * max(width_max, 0.5))
        available_widths.sort(key=lambda w: (abs(w - center), w))

        # Ensure the extremes (width_min, width_max) are always in the sequence.
        # They should be present from the base_widths filter, but guard anyway.
        for boundary in (width_min, width_max):
            if boundary not in available_widths and boundary in base_widths:
                available_widths.append(boundary)
        # Deduplicate while preserving order
        seen: set[float] = set()
        target_widths_list: list[float] = []
        for w in available_widths:
            if w not in seen:
                seen.add(w)
                target_widths_list.append(w)
        target_widths = tuple(target_widths_list)

        # ── Candidate cap: configurable via payload / preset ─────────────────
        # Defaults: wide=800, balanced=400, strict=200.  Old hardcoded value was 80.
        max_candidates = int(self._to_float(payload.get("max_candidates")) or 400)

        # ── Sub-stage instrumentation ────────────────────────────────────────
        # Tracks counts at each pruning point so the filter trace can show
        # exactly where candidates are lost during construction.
        sub_stages: dict[str, Any] = {
            "total_contracts": 0,
            "put_contracts": 0,
            "after_otm_filter": 0,       # puts with strike < underlying
            "after_distance_filter": 0,   # puts within distance window
            "after_width_match": 0,       # pairs with a valid long leg
            "after_positive_width": 0,    # pairs with width > 0
            "after_cap": 0,               # after max_candidates cap
            "by_symbol": {},              # {symbol: count}
            "by_expiration": {},          # {expiration: count}
            "max_candidates_setting": max_candidates,
            # Width-diversity trace metrics
            "width_order_used": list(target_widths),  # center-first order
            "width_center": round(center, 2),
            "candidates_before_cap": 0,
            "candidates_after_cap": 0,
            "width_distribution": {},     # {width_str: count} after cap
        }

        raw_candidates: list[dict[str, Any]] = []

        for snapshot in snapshots:
            contracts = snapshot.get("contracts") or []
            underlying_price = self._to_float(snapshot.get("underlying_price"))
            symbol = str(snapshot.get("symbol") or "").upper()
            expiration = str(snapshot.get("expiration") or "")

            sub_stages["total_contracts"] += len(contracts)

            if underlying_price is None:
                continue

            puts = [
                c for c in contracts
                if str(getattr(c, "option_type", "")).lower() == "put"
                and self._to_float(getattr(c, "strike", None)) is not None
            ]
            sub_stages["put_contracts"] += len(puts)
            if not puts:
                continue

            puts.sort(key=lambda c: float(c.strike), reverse=True)

            for short_leg in puts:
                short_strike = float(short_leg.strike)
                if short_strike >= underlying_price:
                    continue
                sub_stages["after_otm_filter"] += 1

                distance_pct = (underlying_price - short_strike) / underlying_price
                if distance_pct < distance_min or distance_pct > distance_max:
                    continue
                sub_stages["after_distance_filter"] += 1

                long_candidates = [
                    leg for leg in puts
                    if float(leg.strike) < short_strike
                ]
                if not long_candidates:
                    continue

                # Try each target width — build one candidate per matching width
                for tw in target_widths:
                    chosen_long = min(
                        long_candidates,
                        key=lambda leg, _tw=tw: abs((short_strike - float(leg.strike)) - _tw),
                        default=None,
                    )
                    if chosen_long is None:
                        continue

                    actual_width = abs(short_strike - float(chosen_long.strike))
                    # Reject if actual width deviates too far from target
                    if actual_width <= 0:
                        continue
                    tolerance = max(0.25, tw * 0.4)
                    if abs(actual_width - tw) > tolerance:
                        continue

                    sub_stages["after_width_match"] += 1
                    sub_stages["after_positive_width"] += 1

                    # ── Compute cheap basic metrics for smart pruning ────────
                    short_bid = self._to_float(getattr(short_leg, "bid", None))
                    long_ask = self._to_float(getattr(chosen_long, "ask", None))
                    basic_credit = None
                    basic_credit_pct = None
                    if short_bid is not None and long_ask is not None and short_bid > 0:
                        basic_credit = short_bid - long_ask
                        if actual_width > 0:
                            basic_credit_pct = basic_credit / actual_width

                    raw_candidates.append(
                        {
                            "short_leg": short_leg,
                            "long_leg": chosen_long,
                            "strategy": "put_credit_spread",
                            "width": actual_width,
                            "_target_width": tw,
                            "snapshot": snapshot,
                            "_symbol": symbol,
                            "_expiration": expiration,
                            "_basic_credit": basic_credit,
                            "_basic_credit_pct": basic_credit_pct,
                        }
                    )

        # ── Bucket-based cap: allocate slots evenly across width buckets ──
        # This guarantees width diversity — wider spreads aren't truncated
        # just because narrow ones were generated first.
        sub_stages["candidates_before_cap"] = len(raw_candidates)

        # Sort key: prefer positive credit, then highest credit/width ratio
        def _prune_key(c: dict) -> tuple:
            cr = c.get("_basic_credit")
            pct = c.get("_basic_credit_pct")
            has_credit = 0 if (cr is not None and cr > 0) else 1
            return (has_credit, -(pct or 0.0))

        if len(raw_candidates) > max_candidates:
            # Group by target width bucket (center-first order preserved)
            by_width: dict[float, list[dict]] = defaultdict(list)
            for c in raw_candidates:
                by_width[c.get("_target_width", c["width"])].append(c)

            # Sort each bucket by credit quality internally
            for bucket in by_width.values():
                bucket.sort(key=_prune_key)

            # Pass 1: allocate base quota per bucket
            width_order = [w for w in target_widths if w in by_width]
            quota = math.ceil(max_candidates / max(len(width_order), 1))
            capped: list[dict] = []
            remaining_slots = max_candidates
            bucket_used: dict[float, int] = {}

            for w in width_order:
                bucket = by_width[w]
                take = min(len(bucket), quota, remaining_slots)
                capped.extend(bucket[:take])
                bucket_used[w] = take
                remaining_slots -= take
                if remaining_slots <= 0:
                    break

            # Pass 2: if some buckets were small, fill remaining slots from
            # left-over candidates in center-first order.
            if remaining_slots > 0:
                used_ids = set(id(c) for c in capped)
                for w in width_order:
                    for c in by_width[w]:
                        if remaining_slots <= 0:
                            break
                        if id(c) not in used_ids:
                            capped.append(c)
                            used_ids.add(id(c))
                            remaining_slots -= 1
                            bucket_used[w] = bucket_used.get(w, 0) + 1

            raw_candidates = capped
            logger.info(
                "event=candidate_cap_applied max_candidates=%d "
                "total_before_cap=%d bucket_used=%s",
                max_candidates, sub_stages["candidates_before_cap"],
                {str(w): n for w, n in bucket_used.items()},
            )
        else:
            # No cap needed — still sort for deterministic ordering
            raw_candidates.sort(key=_prune_key)

        sub_stages["after_cap"] = len(raw_candidates)
        sub_stages["candidates_after_cap"] = len(raw_candidates)

        # ── Tally per-symbol, per-expiration, and per-width counts ───────
        for c in raw_candidates:
            sym = c.get("_symbol", "?")
            exp = c.get("_expiration", "?")
            sub_stages["by_symbol"][sym] = sub_stages["by_symbol"].get(sym, 0) + 1
            sub_stages["by_expiration"][exp] = sub_stages["by_expiration"].get(exp, 0) + 1

        # Width distribution trace: actual width → count (after cap)
        wd: dict[str, int] = {}
        for c in raw_candidates:
            wk = str(c["width"])
            wd[wk] = wd.get(wk, 0) + 1
        sub_stages["width_distribution"] = wd

        # Strip internal pruning keys before returning
        candidates = []
        for c in raw_candidates:
            c.pop("_symbol", None)
            c.pop("_expiration", None)
            c.pop("_basic_credit", None)
            c.pop("_basic_credit_pct", None)
            c.pop("_target_width", None)
            candidates.append(c)

        # Attach sub-stage counts to inputs so strategy_service can include
        # them in the filter trace.
        inputs["_build_sub_stages"] = sub_stages

        logger.info(
            "event=build_candidates_complete total_contracts=%d puts=%d "
            "otm=%d distance=%d width_match=%d after_cap=%d by_symbol=%s",
            sub_stages["total_contracts"],
            sub_stages["put_contracts"],
            sub_stages["after_otm_filter"],
            sub_stages["after_distance_filter"],
            sub_stages["after_width_match"],
            sub_stages["after_cap"],
            sub_stages["by_symbol"],
        )

        return candidates

    def enrich(self, candidates: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
        if not candidates:
            return []

        payload = inputs.get("request") or {}
        symbol = str(inputs.get("symbol") or "").upper()
        expiration = str(inputs.get("expiration") or "")
        underlying_price = self._to_float(inputs.get("underlying_price"))
        vix = self._to_float(inputs.get("vix"))
        prices_history = inputs.get("prices_history") or []

        base_trades: list[dict[str, Any]] = []
        for candidate in candidates:
            short_leg = candidate.get("short_leg")
            long_leg = candidate.get("long_leg")
            if short_leg is None or long_leg is None:
                continue

            snapshot = candidate.get("snapshot") if isinstance(candidate.get("snapshot"), dict) else inputs
            symbol = str(snapshot.get("symbol") or inputs.get("symbol") or "").upper()
            expiration = str(snapshot.get("expiration") or inputs.get("expiration") or "")
            underlying_price = self._to_float(snapshot.get("underlying_price") if isinstance(snapshot, dict) else inputs.get("underlying_price"))
            vix = self._to_float(snapshot.get("vix") if isinstance(snapshot, dict) else inputs.get("vix"))

            short_strike = self._to_float(getattr(short_leg, "strike", None))
            long_strike = self._to_float(getattr(long_leg, "strike", None))

            short_bid = self._to_float(getattr(short_leg, "bid", None))
            short_ask = self._to_float(getattr(short_leg, "ask", None))
            long_bid = self._to_float(getattr(long_leg, "bid", None))
            long_ask = self._to_float(getattr(long_leg, "ask", None))

            # -- Centralised quote validation ---------------------------------
            quotes_ok, quote_rejection = validate_spread_quotes(
                short_bid, short_ask, long_bid, long_ask,
            )

            # Collect ALL rejection codes that apply (quote + structural).
            # Previously only one code was recorded; now we keep both so
            # the filter trace can separate quote-validation from spread-
            # structure issues (requirement: do not conflate the two).
            rejection_codes: list[str] = []
            if not quotes_ok:
                rejection_codes.append(quote_rejection)
            elif short_bid is not None and short_bid <= 0:
                rejection_codes.append("MISSING_QUOTES:short_bid")
            elif long_ask is not None and long_ask <= 0:
                rejection_codes.append("MISSING_QUOTES:long_ask")

            # -- Credit calculation ------------------------------------------------
            # Formula depends on credit_price_basis (payload-configurable):
            #   "mid"     → short_mid − long_mid  (default)
            #   "natural" → short_bid − long_ask   (worst-case execution)
            credit_basis = str(payload.get("credit_price_basis") or _DEFAULT_CREDIT_BASIS).lower()
            if credit_basis not in _VALID_CREDIT_BASES:
                credit_basis = _DEFAULT_CREDIT_BASIS

            if rejection_codes:
                # Quotes failed — can't compute a reliable credit.
                net_credit = None
            elif credit_basis == "natural":
                # natural fill: credit = short_bid − long_ask
                net_credit = short_bid - long_ask
            else:
                # mid (default): credit = short_mid − long_mid
                short_mid = (short_bid + short_ask) / 2.0
                long_mid = (long_bid + long_ask) / 2.0
                net_credit = short_mid - long_mid

            # Pre-validate net_credit versus width before sending to enrich_trade.
            # These are spread-structure rejections, NOT quote-validation.
            width = abs((short_strike or 0.0) - (long_strike or 0.0))
            if net_credit is not None:
                if net_credit <= 0:
                    rejection_codes.append("non_positive_credit")
                elif net_credit > width - _EPSILON:
                    rejection_codes.append("credit_ge_width")

            # Backward-compat: _quote_rejection stores the FIRST code (or None).
            # _rejection_codes stores ALL codes for multi-reason tracing.
            rejection = rejection_codes[0] if rejection_codes else None

            # Resolve delta safely: pass None (not 0.0) when missing
            raw_delta = self._to_float(getattr(short_leg, "delta", None))
            short_delta_abs = abs(raw_delta) if raw_delta is not None else None

            # ── Volume / OI: map from both legs, keep per-leg raw values ────
            # Source: OptionContract (Pydantic model).  Fields are int|None;
            # None means Tradier returned null / field absent.  0 means the
            # exchange reported zero activity (distinct from missing).
            short_oi = getattr(short_leg, "open_interest", None)
            short_vol = getattr(short_leg, "volume", None)
            long_oi = getattr(long_leg, "open_interest", None)
            long_vol = getattr(long_leg, "volume", None)

            # Canonical trade-level OI/volume = short-leg values (the leg we
            # are selling, so its liquidity matters most).  Per-leg raw values
            # are stored separately for diagnostics.
            # Formula: open_interest = short_leg.open_interest
            #          volume       = short_leg.volume

            base_trades.append(
                {
                    "spread_type": "put_credit_spread",
                    "strategy": "put_credit_spread",
                    "underlying": symbol,
                    "underlying_symbol": symbol,
                    "expiration": expiration,
                    "dte": dte_ceil(expiration),
                    "short_strike": short_strike,
                    "long_strike": long_strike,
                    "underlying_price": underlying_price,
                    "price": underlying_price,
                    "vix": vix,
                    "bid": short_bid,
                    "ask": short_ask,
                    "open_interest": short_oi,
                    "volume": short_vol,
                    "short_delta_abs": short_delta_abs,
                    "iv": self._to_float(getattr(short_leg, "iv", None)),
                    "implied_vol": self._to_float(getattr(short_leg, "iv", None)),
                    "width": width,
                    "net_credit": net_credit,
                    "_credit_basis": credit_basis,
                    "contractsMultiplier": 100,
                    # -- enrichment debug fields (consumed by evaluate, not persisted) --
                    "_quote_rejection": rejection,
                    "_rejection_codes": rejection_codes,
                    "_short_bid": short_bid,
                    "_short_ask": short_ask,
                    "_long_bid": long_bid,
                    "_long_ask": long_ask,
                    # -- per-leg liquidity (for diagnostics / trace) --
                    "_short_oi": short_oi,
                    "_short_vol": short_vol,
                    "_long_oi": long_oi,
                    "_long_vol": long_vol,
                }
            )

        if not base_trades:
            return []

        enriched = enrich_trades_batch(
            base_trades,
            prices_history=[float(x) for x in prices_history if self._to_float(x) is not None],
            vix=vix,
            iv_low=None,
            iv_high=None,
        )
        return [row for row in (enriched or []) if isinstance(row, dict)]

    def evaluate(self, trade: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []

        # ── Gate 1: Pre-enrichment rejections (set during enrich()) ────────
        # _rejection_codes may contain MULTIPLE codes (e.g. a QUOTE_INVALID
        # code AND non_positive_credit).  Return all of them so the filter
        # trace captures every applicable reason.
        pre_rej_codes = trade.get("_rejection_codes") or []
        if not pre_rej_codes:
            # Fallback: legacy single-code field
            single = trade.get("_quote_rejection")
            if single:
                pre_rej_codes = [single]
        if pre_rej_codes:
            return False, list(pre_rej_codes)

        # ── Gate 2: CreditSpread metric failure (set during enrich_trade()) ──
        data_warn = trade.get("data_warning") or ""
        if "CreditSpread metrics unavailable" in data_warn:
            reasons.append("CREDIT_SPREAD_METRICS_FAILED")

        policy = trade.get("_policy") if isinstance(trade.get("_policy"), dict) else {}
        payload = trade.get("_request") if isinstance(trade.get("_request"), dict) else {}

        # ── Resolve dataQualityMode ──────────────────────────────────────────
        dq_mode = str(payload.get("data_quality_mode") or _DEFAULT_DATA_QUALITY_MODE).lower()
        if dq_mode not in _DATA_QUALITY_MODES:
            dq_mode = _DEFAULT_DATA_QUALITY_MODE

        # ── Read trade metrics ───────────────────────────────────────────────
        pop = safe_float(trade.get("p_win_used") or trade.get("pop_delta_approx"))
        ev = safe_float(trade.get("ev_per_share") or trade.get("expected_value"))
        ev_to_risk = safe_float(trade.get("ev_to_risk"))
        ror = safe_float(trade.get("return_on_risk"))
        width = safe_float(trade.get("width"))
        net_credit = safe_float(trade.get("net_credit"))
        spread_pct = safe_float(trade.get("bid_ask_spread_pct"))

        # ── Read OI / volume — preserve None for data-quality detection ──────
        raw_oi = trade.get("open_interest")
        raw_vol = trade.get("volume")
        oi_value = safe_float(raw_oi)   # None if missing / unparseable
        vol_value = safe_float(raw_vol)  # None if missing / unparseable

        # ── Gate 3: Spread structure (requires valid net_credit & width) ─────
        if width is None or width <= 0:
            reasons.append("invalid_width")
        if net_credit is None or net_credit <= 0:
            reasons.append("non_positive_credit")

        # ── Threshold resolution: prefer payload (preset-resolved), then policy, then safety fallback ──
        min_pop = safe_float(payload.get("min_pop"))
        if min_pop is None:
            min_pop = safe_float(policy.get("min_pop"))
        if min_pop is None:
            min_pop = 0.60  # balanced-level safety fallback

        min_ev_to_risk = safe_float(payload.get("min_ev_to_risk"))
        if min_ev_to_risk is None:
            min_ev_to_risk = safe_float(policy.get("min_ev_to_risk"))
        if min_ev_to_risk is None:
            min_ev_to_risk = 0.02  # balanced-level safety fallback

        min_ror = safe_float(payload.get("min_ror"))
        if min_ror is None:
            min_ror = safe_float(policy.get("min_ror"))
        if min_ror is None:
            min_ror = 0.01  # balanced-level safety fallback

        spread_pct_limit = safe_float(payload.get("max_bid_ask_spread_pct"))
        if spread_pct_limit is None:
            spread_pct_limit = safe_float(policy.get("max_bid_ask_spread_pct"))
        if spread_pct_limit is None:
            spread_pct_limit = 1.5  # balanced-level safety fallback

        min_oi = int(safe_float(payload.get("min_open_interest")) or 0)
        if min_oi <= 0:
            min_oi = max(int(safe_float(policy.get("min_open_interest")) or 0), 300)

        min_vol = int(safe_float(payload.get("min_volume")) or 0)
        if min_vol <= 0:
            min_vol = max(int(safe_float(policy.get("min_volume")) or 0), 20)

        # ── Gate 4: Probability & expected-value thresholds ──────────────────
        # Missing POP: BenTrade is probability-first.  A trade without POP
        # cannot be evaluated — reject under strict/balanced, waive only under
        # lenient (still tracked as data-quality issue).
        if pop is None:
            if dq_mode == "lenient":
                pass  # waived — dq_waived_count tracked at trace level
            else:
                reasons.append("DQ_MISSING:pop")
        elif pop < min_pop:
            reasons.append("pop_below_floor")

        if ev_to_risk is not None and ev_to_risk < min_ev_to_risk:
            reasons.append("ev_to_risk_below_floor")
        elif ev is not None and ev < -0.05:
            reasons.append("ev_negative")
        if ror is not None and ror < min_ror:
            reasons.append("ror_below_floor")

        # ── Gate 5: Bid-ask spread (only meaningful on validated quotes) ─────
        if spread_pct is not None and (spread_pct * 100.0) > spread_pct_limit:
            reasons.append("spread_too_wide")

        # ── Gate 6: Liquidity / data-quality for OI & volume ────────────────
        # Distinguishes three states per field:
        #   None  → missing (data-quality issue, not a threshold failure)
        #   0     → zero    (exchange reported no activity; treated as DQ in strict/balanced)
        #   > 0   → present → compare against threshold
        oi_missing = oi_value is None
        vol_missing = vol_value is None
        oi_zero = (not oi_missing) and int(oi_value) == 0
        vol_zero = (not vol_missing) and int(vol_value) == 0

        if oi_missing or vol_missing:
            # In lenient mode: waive missing OI/vol if pricing looks healthy
            if dq_mode == "lenient":
                spread_ok = (spread_pct is None) or ((spread_pct * 100.0) <= spread_pct_limit)
                min_credit = safe_float(payload.get("min_credit_for_dq_waiver"))
                if min_credit is None:
                    min_credit = _DEFAULT_MIN_CREDIT_FOR_DQ_WAIVER
                credit_ok = (net_credit is not None) and (net_credit >= min_credit)
                if not (spread_ok and credit_ok):
                    # Cannot waive — add DQ rejection
                    if oi_missing:
                        reasons.append("DQ_MISSING:open_interest")
                    if vol_missing:
                        reasons.append("DQ_MISSING:volume")
                # else: waived — trade keeps going
            else:
                # strict / balanced: missing data → distinct DQ rejection
                if oi_missing:
                    reasons.append("DQ_MISSING:open_interest")
                if vol_missing:
                    reasons.append("DQ_MISSING:volume")
        elif oi_zero or vol_zero:
            # Zero is not the same as missing — the exchange reported 0.
            # Strict/balanced: treat zero OI/vol as a data-quality concern.
            # Lenient: waive if pricing looks healthy.
            if dq_mode == "lenient":
                spread_ok = (spread_pct is None) or ((spread_pct * 100.0) <= spread_pct_limit)
                min_credit = safe_float(payload.get("min_credit_for_dq_waiver"))
                if min_credit is None:
                    min_credit = _DEFAULT_MIN_CREDIT_FOR_DQ_WAIVER
                credit_ok = (net_credit is not None) and (net_credit >= min_credit)
                if not (spread_ok and credit_ok):
                    if oi_zero:
                        reasons.append("DQ_ZERO:open_interest")
                    if vol_zero:
                        reasons.append("DQ_ZERO:volume")
                # else: waived
            else:
                if oi_zero:
                    reasons.append("DQ_ZERO:open_interest")
                if vol_zero:
                    reasons.append("DQ_ZERO:volume")
        else:
            # Both OI and volume present and > 0 — apply threshold checks
            if int(oi_value) < min_oi:
                reasons.append("open_interest_below_min")
            if int(vol_value) < min_vol:
                reasons.append("volume_below_min")

        return len(reasons) == 0, reasons

    def score(self, trade: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Return (rank_score, tie_breaks).  rank_score is 0–100."""
        rank_score = float(compute_rank_score(trade))
        tie_breaks = {
            "edge": safe_float(trade.get("ev_to_risk")) or 0.0,
            "pop": safe_float(trade.get("p_win_used") or trade.get("pop_delta_approx")) or 0.0,
            "liq": -(safe_float(trade.get("bid_ask_spread_pct")) or 1.0),
        }
        return rank_score, tie_breaks
