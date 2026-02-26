from __future__ import annotations

import logging
from typing import Any

from app.services.ranking import compute_rank_score, safe_float
from app.utils.dates import dte_ceil
from common.quant_analysis import enrich_trades_batch

logger = logging.getLogger("bentrade.credit_spread")

# Tolerance: reject candidates whose net_credit is within this many dollars
# of the spread width.  Prevents near-zero-profit or floating-point-edge trades.
_EPSILON = 0.01

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
        target_widths = tuple(w for w in base_widths if width_min <= w <= width_max) or (2.0, 3.0, 5.0)

        candidates: list[dict[str, Any]] = []

        for snapshot in snapshots:
            contracts = snapshot.get("contracts") or []
            underlying_price = self._to_float(snapshot.get("underlying_price"))
            if underlying_price is None:
                continue

            puts = [
                c for c in contracts
                if str(getattr(c, "option_type", "")).lower() == "put"
                and self._to_float(getattr(c, "strike", None)) is not None
            ]
            if not puts:
                continue

            puts.sort(key=lambda c: float(c.strike), reverse=True)

            for short_leg in puts:
                short_strike = float(short_leg.strike)
                if short_strike >= underlying_price:
                    continue

                distance_pct = (underlying_price - short_strike) / underlying_price
                if distance_pct < distance_min or distance_pct > distance_max:
                    continue

                long_candidates = [
                    leg for leg in puts
                    if float(leg.strike) < short_strike
                ]
                if not long_candidates:
                    continue

                chosen_long = None
                for width in target_widths:
                    chosen_long = min(
                        long_candidates,
                        key=lambda leg: abs((short_strike - float(leg.strike)) - width),
                        default=None,
                    )
                    if chosen_long is not None:
                        break
                if chosen_long is None:
                    continue

                width = abs(short_strike - float(chosen_long.strike))
                if width <= 0:
                    continue

                candidates.append(
                    {
                        "short_leg": short_leg,
                        "long_leg": chosen_long,
                        "strategy": "put_credit_spread",
                        "width": width,
                        "snapshot": snapshot,
                    }
                )

                if len(candidates) >= 80:
                    break

            if len(candidates) >= 80:
                break

        return candidates

    def enrich(self, candidates: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
        if not candidates:
            return []

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

            # Legacy rejection codes kept for backward compatibility with
            # existing filter-trace gate groups.  The new QUOTE_INVALID:*
            # codes carry more detail; we map to legacy codes so both appear
            # in the trace breakdown.
            rejection: str | None = None
            if not quotes_ok:
                # Store the detailed code as primary rejection.
                rejection = quote_rejection
            elif short_bid is not None and short_bid <= 0:
                rejection = "MISSING_QUOTES:short_bid"
            elif long_ask is not None and long_ask <= 0:
                rejection = "MISSING_QUOTES:long_ask"

            if rejection:
                net_credit = None
            else:
                net_credit = short_bid - long_ask

            # Pre-validate net_credit versus width before sending to enrich_trade
            width = abs((short_strike or 0.0) - (long_strike or 0.0))
            if rejection is None and net_credit is not None:
                if net_credit <= 0:
                    rejection = "NON_POSITIVE_CREDIT"
                elif net_credit > width - _EPSILON:
                    rejection = "NET_CREDIT_GE_WIDTH"

            # Resolve delta safely: pass None (not 0.0) when missing
            raw_delta = self._to_float(getattr(short_leg, "delta", None))
            short_delta_abs = abs(raw_delta) if raw_delta is not None else None

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
                    "open_interest": getattr(short_leg, "open_interest", None),
                    "volume": getattr(short_leg, "volume", None),
                    "short_delta_abs": short_delta_abs,
                    "iv": self._to_float(getattr(short_leg, "iv", None)),
                    "implied_vol": self._to_float(getattr(short_leg, "iv", None)),
                    "width": width,
                    "net_credit": net_credit,
                    "contractsMultiplier": 100,
                    # -- enrichment debug fields (consumed by evaluate, not persisted) --
                    "_quote_rejection": rejection,
                    "_short_bid": short_bid,
                    "_short_ask": short_ask,
                    "_long_bid": long_bid,
                    "_long_ask": long_ask,
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

        # ── Gate 1: Pre-enrichment quote rejection (set during enrich()) ─────
        quote_rej = trade.get("_quote_rejection")
        if quote_rej:
            return False, [quote_rej]

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
        if pop is not None and pop < min_pop:
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
        oi_missing = oi_value is None
        vol_missing = vol_value is None

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
        else:
            # Both OI and volume present — apply threshold checks
            if int(oi_value) < min_oi:
                reasons.append("open_interest_below_min")
            if int(vol_value) < min_vol:
                reasons.append("volume_below_min")

        return len(reasons) == 0, reasons

    def score(self, trade: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        rank_score = float(compute_rank_score(trade))
        tie_breaks = {
            "edge": safe_float(trade.get("ev_to_risk")) or 0.0,
            "pop": safe_float(trade.get("p_win_used") or trade.get("pop_delta_approx")) or 0.0,
            "liq": -(safe_float(trade.get("bid_ask_spread_pct")) or 1.0),
        }
        return rank_score, tie_breaks
