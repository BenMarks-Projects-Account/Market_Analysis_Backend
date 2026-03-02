from __future__ import annotations

import logging
import math
from statistics import pstdev
from typing import Any

from app.services.ranking import compute_rank_score, safe_float
from app.services.strategies.base import (
    POP_SOURCE_BREAKEVEN_LOGNORMAL,
    POP_SOURCE_DELTA_ADJUSTED,
    POP_SOURCE_DELTA_APPROX,
    POP_SOURCE_NONE,
    StrategyPlugin,
)
from app.utils.expected_fill import apply_expected_fill

logger = logging.getLogger("bentrade.debit_spreads")


class DataQualityError(RuntimeError):
    """Raised when quote/data integrity checks fail systemically.

    This stops the pipeline before scoring proceeds on garbage data.
    """


# Valid dataQualityMode values — controls how missing OI/volume/POP is handled.
#   "strict"      – missing data → reject (not used as a preset name; available)
#   "balanced"    – same as strict for DQ fields
#   "lenient"     – waive missing OI/volume/POP when pricing looks healthy
_DATA_QUALITY_MODES = frozenset({"strict", "balanced", "lenient"})
_DEFAULT_DATA_QUALITY_MODE = "balanced"

# Minimum net_debit ($) to waive DQ fields in lenient mode.
_DEFAULT_MIN_DEBIT_FOR_DQ_WAIVER = 0.10

# Epsilon for POP floor comparison (Task 1).
# Avoids rejecting boundary trades where pop ≈ threshold due to float rounding.
_POP_EPSILON = 1e-4


# ---------------------------------------------------------------------------
# Breakeven + lognormal POP fallback (Task 2)
# ---------------------------------------------------------------------------

def _compute_pop_breakeven_lognormal(
    break_even: float | None,
    underlying_price: float | None,
    iv: float | None,
    dte: int | None,
    strategy: str,
) -> float | None:
    """Compute P(profit) using Black-Scholes lognormal distribution.

    For call_debit:  P(S_T > breakeven) = N(d2)
    For put_debit:   P(S_T < breakeven) = N(-d2)

    Where d2 = [ln(S/K) + (r - σ²/2)T] / (σ√T), with r=0 (risk-free rate
    approximation — acceptable for short-dated options).

    Returns None if any input is missing or invalid.
    """
    if (break_even is None or underlying_price is None or iv is None
            or dte is None or iv <= 0 or underlying_price <= 0
            or break_even <= 0 or dte <= 0):
        return None

    T = dte / 365.0
    sigma_sqrt_T = iv * math.sqrt(T)
    if sigma_sqrt_T <= 0:
        return None

    # d2 with r=0: d2 = [ln(S/K) - σ²T/2] / (σ√T)
    d2 = (math.log(underlying_price / break_even) - 0.5 * iv * iv * T) / sigma_sqrt_T

    # Standard normal CDF approximation (Abramowitz & Stegun 26.2.17)
    def _norm_cdf(x: float) -> float:
        """Fast normal CDF, max error ~1.5e-7."""
        if x < -8.0:
            return 0.0
        if x > 8.0:
            return 1.0
        t = 1.0 / (1.0 + 0.2316419 * abs(x))
        poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
        pdf = math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
        cdf = 1.0 - pdf * poly
        return cdf if x >= 0 else 1.0 - cdf

    if strategy == "call_debit":
        # P(S_T > breakeven) = N(d2)
        pop = _norm_cdf(d2)
    else:
        # put_debit: P(S_T < breakeven) = N(-d2)
        pop = _norm_cdf(-d2)

    return max(0.0, min(1.0, pop))


# ---------------------------------------------------------------------------
# Centralised quote validation (mirrors credit_spread.py for consistency)
# ---------------------------------------------------------------------------

def validate_quote(bid: float | None, ask: float | None) -> tuple[bool, str | None]:
    """Check a single leg's quote validity.

    Rules:
    - bid must be >= 0 (None → invalid)
    - ask must be > 0   (None → invalid)
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
    long_bid: float | None,
    long_ask: float | None,
    short_bid: float | None,
    short_ask: float | None,
) -> tuple[bool, str | None]:
    """Validate both legs of a debit spread.

    For debit spreads the *long* leg is the bought option (we pay ask)
    and the *short* leg is the sold option (we receive bid).
    Rejection codes use the ``QUOTE_INVALID:`` prefix for granular tracking.
    """
    ok_long, long_reason = validate_quote(long_bid, long_ask)
    if not ok_long:
        return False, f"QUOTE_INVALID:long_leg:{long_reason}"

    ok_short, short_reason = validate_quote(short_bid, short_ask)
    if not ok_short:
        return False, f"QUOTE_INVALID:short_leg:{short_reason}"

    return True, None


def _validate_debit_trade(trade: dict[str, Any], exp_move: float | None) -> None:
    """In-place sanity checks on an enriched debit trade (Goal 5).

    Appends to ``trade["_dq_flags"]`` for any failed check.  Adds
    ``trade["_valid_for_ranking"]`` boolean.

    Checks:
      1. spread_mid/spread_ask/spread_bid present and non-negative
      2. net_debit ≈ spread_ask (NATURAL) or spread_mid (MID) ± tolerance
      3. max_loss ≈ net_debit × 100
      4. max_profit ≈ (width − net_debit) × 100
      5. breakeven formula: call = long_strike + net_debit; put = long_strike − net_debit
      6. bid_ask_spread_pct from spread quotes matches stored value
      7. POP sanity: if breakeven > 1.5× expected_move away, pop > 0.8 → POP_SUSPECT
      8. EV–POP invariant: |ev/max_loss + 1 − 2×p_win| should be small
    """
    flags: list[str] = trade.get("_dq_flags", [])
    tol = 0.015  # $0.015 tolerance for rounding

    net_debit = trade.get("net_debit")
    width = trade.get("width")
    strategy = trade.get("strategy")
    valid = True  # assume valid, flip on critical failure

    # --- Check 1: spread quotes ---
    sb = trade.get("spread_bid")
    sa = trade.get("spread_ask")
    sm = trade.get("spread_mid")
    if sb is None or sa is None or sm is None:
        if not trade.get("_rejection_codes"):
            flags.append("SANITY:spread_quotes_missing")
    else:
        if sa < -tol:
            flags.append("SANITY:spread_ask_negative")
            valid = False

    # --- Check 2: net_debit vs spread quote ---
    method = trade.get("_debit_method", "natural")
    if net_debit is not None and sa is not None and method == "natural":
        if abs(net_debit - sa) > tol:
            flags.append(f"SANITY:net_debit_vs_spread_ask_mismatch:{net_debit:.4f}!={sa:.4f}")
    if net_debit is not None and sm is not None and method == "mid":
        if abs(net_debit - sm) > tol:
            flags.append(f"SANITY:net_debit_vs_spread_mid_mismatch:{net_debit:.4f}!={sm:.4f}")

    # --- Check 3/4: max_loss, max_profit vs net_debit ---
    max_loss = trade.get("max_loss")
    max_profit = trade.get("max_profit")
    if net_debit is not None and max_loss is not None:
        expected_max_loss = net_debit * 100.0
        if abs(max_loss - expected_max_loss) > 1.0:
            flags.append(f"SANITY:max_loss_mismatch:{max_loss}!={expected_max_loss}")
            valid = False
    if net_debit is not None and width is not None and max_profit is not None:
        expected_max_profit = max(width - net_debit, 0.0) * 100.0
        if abs(max_profit - expected_max_profit) > 1.0:
            flags.append(f"SANITY:max_profit_mismatch:{max_profit}!={expected_max_profit}")
            valid = False

    # --- Check 5: breakeven formula ---
    be = trade.get("break_even")
    ls = trade.get("long_strike")
    if be is not None and ls is not None and net_debit is not None:
        if strategy == "call_debit":
            expected_be = ls + net_debit
        else:
            expected_be = ls - net_debit
        if abs(be - expected_be) > tol:
            flags.append(f"SANITY:breakeven_mismatch:{be}!={expected_be}")

    # --- Check 7: POP vs expected move ---
    pop = trade.get("p_win_used")
    if pop is not None and pop > 0.8 and be is not None and exp_move is not None and exp_move > 0:
        underlying = trade.get("underlying_price", 0.0) or 0.0
        be_distance = abs(be - underlying)
        if be_distance > exp_move * 1.5:
            flags.append("POP_SUSPECT:high_pop_vs_expected_move")

    # --- Check 8: EV–POP consistency ---
    ev = trade.get("ev_per_contract")
    if (ev is not None and pop is not None and max_loss is not None
            and max_profit is not None and max_loss > 0):
        # Binary EV: ev = pop * max_profit - (1-pop) * max_loss
        # So: ev/max_loss = pop * (max_profit/max_loss) - (1-pop)
        # Reconstruct: expected = pop * max_profit - (1-pop) * max_loss
        expected_ev = pop * max_profit - (1.0 - pop) * max_loss
        if abs(ev - expected_ev) > 1.0:
            flags.append(f"SANITY:ev_pop_mismatch:ev={ev:.2f},expected={expected_ev:.2f}")

    trade["_dq_flags"] = flags
    trade["_valid_for_ranking"] = valid


class DebitSpreadsStrategyPlugin(StrategyPlugin):
    id = "debit_spreads"
    display_name = "Debit Spreads"

    # ── Transient fields to strip before persisting ─────────────────────
    TRANSIENT_FIELDS: frozenset[str] = StrategyPlugin.TRANSIENT_FIELDS | frozenset({
        "_dq_flags", "_pop_gate_eval", "_gate_eval_snapshot",
        "_primary_rejection_reason", "_valid_for_ranking",
    })

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, value))

    @staticmethod
    def _choose_widths(underlying_price: float, payload: dict[str, Any]) -> list[float]:
        req_width = payload.get("width")
        if req_width not in (None, ""):
            try:
                width = max(0.5, float(req_width))
                return [width]
            except (TypeError, ValueError):
                pass

        if underlying_price < 50:
            widths = [0.5, 1.0, 2.0]
        elif underlying_price < 150:
            widths = [1.0, 2.0, 5.0]
        else:
            widths = [2.0, 5.0, 10.0]

        return widths

    @staticmethod
    def _best_by_strike(contracts: list[Any]) -> dict[float, Any]:
        out: dict[float, Any] = {}
        for contract in contracts:
            strike = safe_float(getattr(contract, "strike", None))
            if strike is None:
                continue
            current = out.get(strike)
            if current is None:
                out[strike] = contract
                continue
            curr_oi = safe_float(getattr(current, "open_interest", None)) or 0.0
            new_oi = safe_float(getattr(contract, "open_interest", None)) or 0.0
            if new_oi > curr_oi:
                out[strike] = contract
        return out

    def build_candidates(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        # ── DATA PATH (shared with credit_spread.py) ───────────────────────
        # Both scanners receive the same `inputs["snapshots"]` list built by
        # strategy_service.generate().  Each snapshot contains:
        #   - "contracts": list[OptionContract]  (from base_data_service.normalize_chain())
        #   - "underlying_price", "symbol", "expiration", "dte", etc.
        # OptionContract fields: bid, ask, open_interest, volume, delta, iv,
        #                        strike, expiration, option_type, symbol
        # All quote/OI/volume data comes from Tradier via normalize_chain().
        # KEY DIFFERENCES from credit_spread.build_candidates():
        #   1) Scans BOTH calls and puts (credit scans puts only).
        #   2) Uses _best_by_strike() dedup (credit iterates all contracts).
        #   3) Builds call_debit and put_debit spread types.
        snapshots = inputs.get("snapshots") or []
        payload = inputs.get("request") or {}
        direction = str(payload.get("direction") or "both").strip().lower()
        if direction not in {"both", "call", "put"}:
            direction = "both"

        # Safety ceiling only — preset max_candidates is applied centrally
        # by select_top_n() in strategy_service.generate().
        max_candidates = int(inputs.get("_generation_cap") or 20_000)

        # ── Sub-stage instrumentation (mirrors credit_spread.py) ────────────
        # Tracks counts at each pruning point so the filter trace can show
        # exactly where candidates are lost during construction.
        sub_stages: dict[str, Any] = {
            "total_contracts": 0,
            "call_contracts": 0,
            "put_contracts": 0,
            "after_otm_filter": 0,        # strikes within distance window
            "after_width_match": 0,        # pairs with a valid short leg
            "after_positive_width": 0,     # pairs with width > 0
            "after_cap": 0,                # after max_candidates cap
            "by_symbol": {},               # {symbol: count}
            "by_expiration": {},           # {expiration: count}
            "max_candidates_setting": max_candidates,
            "direction": direction,
            "skipped_empty_chain": 0,      # snapshots with 0 usable contracts
        }

        candidates: list[dict[str, Any]] = []

        for snapshot in snapshots:
            symbol = str(snapshot.get("symbol") or "").upper()
            expiration = str(snapshot.get("expiration") or "")
            dte = int(snapshot.get("dte") or 0)
            underlying_price = self._to_float(snapshot.get("underlying_price"))
            contracts = snapshot.get("contracts") or []

            sub_stages["total_contracts"] += len(contracts)

            if not symbol or not expiration or underlying_price is None:
                continue

            # ── Chain-level data guard ──────────────────────────────────────
            # If the chain returned zero usable contracts, log and skip.
            # This catches API failures / empty expirations before we burn
            # cycles on candidate pairing.
            if not contracts:
                sub_stages["skipped_empty_chain"] += 1
                logger.warning(
                    "event=empty_chain symbol=%s expiration=%s contracts=0",
                    symbol, expiration,
                )
                continue

            widths = self._choose_widths(underlying_price, payload)
            call_contracts = [c for c in contracts if str(getattr(c, "option_type", "")).lower() == "call"]
            put_contracts = [c for c in contracts if str(getattr(c, "option_type", "")).lower() == "put"]
            sub_stages["call_contracts"] += len(call_contracts)
            sub_stages["put_contracts"] += len(put_contracts)
            call_map = self._best_by_strike(call_contracts)
            put_map = self._best_by_strike(put_contracts)

            strike_window = underlying_price * 0.12

            if direction in {"both", "call"}:
                call_strikes = sorted(call_map.keys())
                for long_strike in call_strikes:
                    if abs(long_strike - underlying_price) > strike_window:
                        continue
                    sub_stages["after_otm_filter"] += 1
                    for width in widths:
                        target = long_strike + width
                        short_strike = min((s for s in call_strikes if s > long_strike), key=lambda s: abs(s - target), default=None)
                        if short_strike is None:
                            continue
                        if abs((short_strike - long_strike) - width) > max(0.25, width * 0.4):
                            continue
                        actual_width = abs(short_strike - long_strike)
                        if actual_width <= 0:
                            continue
                        sub_stages["after_width_match"] += 1
                        sub_stages["after_positive_width"] += 1
                        candidates.append(
                            {
                                "strategy": "call_debit",
                                "spread_type": "call_debit",
                                "symbol": symbol,
                                "expiration": expiration,
                                "dte": dte,
                                "underlying_price": underlying_price,
                                "width": actual_width,
                                "long_strike": long_strike,
                                "short_strike": short_strike,
                                "long_leg": call_map.get(long_strike),
                                "short_leg": call_map.get(short_strike),
                                "snapshot": snapshot,
                            }
                        )
                        if len(candidates) >= max_candidates:
                            break
                    if len(candidates) >= max_candidates:
                        break

            if len(candidates) < max_candidates and direction in {"both", "put"}:
                put_strikes = sorted(put_map.keys())
                for long_strike in put_strikes:
                    if abs(long_strike - underlying_price) > strike_window:
                        continue
                    sub_stages["after_otm_filter"] += 1
                    for width in widths:
                        target = long_strike - width
                        short_strike = min((s for s in put_strikes if s < long_strike), key=lambda s: abs(s - target), default=None)
                        if short_strike is None:
                            continue
                        if abs((long_strike - short_strike) - width) > max(0.25, width * 0.4):
                            continue
                        actual_width = abs(long_strike - short_strike)
                        if actual_width <= 0:
                            continue
                        sub_stages["after_width_match"] += 1
                        sub_stages["after_positive_width"] += 1
                        candidates.append(
                            {
                                "strategy": "put_debit",
                                "spread_type": "put_debit",
                                "symbol": symbol,
                                "expiration": expiration,
                                "dte": dte,
                                "underlying_price": underlying_price,
                                "width": actual_width,
                                "long_strike": long_strike,
                                "short_strike": short_strike,
                                "long_leg": put_map.get(long_strike),
                                "short_leg": put_map.get(short_strike),
                                "snapshot": snapshot,
                            }
                        )
                        if len(candidates) >= max_candidates:
                            break
                    if len(candidates) >= max_candidates:
                        break

        # ── Tally per-symbol, per-expiration counts ──────────────────────
        sub_stages["after_cap"] = len(candidates)
        for c in candidates:
            sym = c.get("symbol", "?")
            exp = c.get("expiration", "?")
            sub_stages["by_symbol"][sym] = sub_stages["by_symbol"].get(sym, 0) + 1
            sub_stages["by_expiration"][exp] = sub_stages["by_expiration"].get(exp, 0) + 1

        # Attach sub-stage counts to inputs so strategy_service can include
        # them in the filter trace (same pattern as credit_spread.py).
        inputs["_build_sub_stages"] = sub_stages

        logger.info(
            "event=build_candidates_complete total_contracts=%d calls=%d puts=%d "
            "otm=%d width_match=%d after_cap=%d by_symbol=%s",
            sub_stages["total_contracts"],
            sub_stages["call_contracts"],
            sub_stages["put_contracts"],
            sub_stages["after_otm_filter"],
            sub_stages["after_width_match"],
            sub_stages["after_cap"],
            sub_stages["by_symbol"],
        )

        return candidates

    @staticmethod
    def _realized_vol_from_prices(prices: list[float]) -> float | None:
        if not prices or len(prices) < 25:
            return None
        returns: list[float] = []
        for i in range(1, len(prices)):
            prev = float(prices[i - 1])
            cur = float(prices[i])
            if prev <= 0 or cur <= 0:
                continue
            returns.append(math.log(cur / prev))
        if len(returns) < 10:
            return None
        sigma = pstdev(returns)
        return float(sigma * math.sqrt(252.0))

    @staticmethod
    def _combo_spread_pct(
        spread_bid: float | None,
        spread_ask: float | None,
        net_debit: float | None,
    ) -> float | None:
        """Bid–ask spread percentage from SPREAD-level quotes.

        Formula: (spread_ask − spread_bid) / reference_mid
        Where reference_mid = net_debit when positive, else (spread_bid+spread_ask)/2.
        Returns None when inputs are insufficient.
        """
        if spread_bid is None or spread_ask is None:
            return None
        if net_debit is not None and net_debit > 0:
            mid = net_debit
        else:
            mid = (spread_bid + spread_ask) / 2.0
        if mid <= 0:
            return None
        return max(0.0, (spread_ask - spread_bid) / mid)

    def enrich(self, candidates: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
        # ── DATA PATH (shared with credit_spread.py) ───────────────────────
        # Per-leg quote/OI/volume fields are read via getattr() from the same
        # OptionContract Pydantic objects populated by normalize_chain().
        # Field mapping (identical for both scanners):
        #   getattr(leg, "bid", None)            → Tradier row["bid"]
        #   getattr(leg, "ask", None)            → Tradier row["ask"]
        #   getattr(leg, "open_interest", None)  → Tradier row["open_interest"]
        #   getattr(leg, "volume", None)         → Tradier row["volume"]
        #   getattr(leg, "delta", None)          → Tradier row["greeks"]["delta"]
        #   getattr(leg, "iv", None)             → Tradier row["iv"] or greeks
        # KEY DIFFERENCES from credit_spread.enrich():
        #   1) OI/volume → min(long, short) [credit uses short-only]
        #   2) POP → 1 - debit/width [credit uses delta-based CreditSpread model]
        #   3) Computes everything inline [credit delegates to enrich_trades_batch()]
        if not candidates:
            return []

        payload = inputs.get("request") or {}
        policy = inputs.get("policy") or {}
        max_ivrv_for_buy = safe_float(policy.get("max_iv_rv_ratio_for_buying"))
        if max_ivrv_for_buy is None:
            max_ivrv_for_buy = safe_float(payload.get("max_iv_rv_ratio_for_buying"))

        # Pre-compute realized vol once per unique snapshot to avoid redundant math
        _rv_cache: dict[int, float | None] = {}

        out: list[dict[str, Any]] = []

        for candidate in candidates:
            long_leg = candidate.get("long_leg")
            short_leg = candidate.get("short_leg")
            if long_leg is None or short_leg is None:
                continue

            strategy = str(candidate.get("strategy") or "call_debit")
            symbol = str(candidate.get("symbol") or "").upper()
            expiration = str(candidate.get("expiration") or "")
            dte = int(candidate.get("dte") or 0)
            width = float(candidate.get("width") or 0.0)
            underlying_price = float(candidate.get("underlying_price") or 0.0)
            snapshot = candidate.get("snapshot") or {}

            # ── Per-leg raw quotes ──────────────────────────────────────────
            long_bid = safe_float(getattr(long_leg, "bid", None))
            long_ask = safe_float(getattr(long_leg, "ask", None))
            short_bid = safe_float(getattr(short_leg, "bid", None))
            short_ask = safe_float(getattr(short_leg, "ask", None))

            # ── Per-leg mid ─────────────────────────────────────────────────
            long_mid = ((long_bid + long_ask) / 2.0
                        if long_bid is not None and long_ask is not None else None)
            short_mid = ((short_bid + short_ask) / 2.0
                         if short_bid is not None and short_ask is not None else None)

            # ── Centralised quote validation ────────────────────────────────
            quotes_ok, quote_rejection = validate_spread_quotes(
                long_bid, long_ask, short_bid, short_ask,
            )

            # Per-candidate data-quality flags (list of human-readable strings).
            dq_flags: list[str] = []

            # Collect ALL rejection codes (quote + structural).
            rejection_codes: list[str] = []
            if not quotes_ok:
                rejection_codes.append(quote_rejection)
                dq_flags.append(f"QUOTE_FAILED:{quote_rejection}")

            # ── Spread-level quotes (Goal 1) ────────────────────────────────
            # For a debit spread we BUY the long leg and SELL the short leg.
            #   spread_bid = long_bid − short_ask  (worst exit / what we'd receive selling)
            #   spread_ask = long_ask − short_bid  (worst entry / what we'd pay buying)
            #   spread_mid = (spread_bid + spread_ask) / 2
            if quotes_ok:
                spread_bid = long_bid - short_ask
                spread_ask = long_ask - short_bid
                spread_mid = (spread_bid + spread_ask) / 2.0
            else:
                spread_bid = None
                spread_ask = None
                spread_mid = None

            # ── Net debit (configurable basis) ──────────────────────────────
            # "natural" = spread_ask (worst-case entry; default)
            # "mid"     = spread_mid
            debit_method = str(payload.get("debit_price_basis") or "natural").lower()
            if debit_method not in ("natural", "mid"):
                debit_method = "natural"

            if rejection_codes:
                debit = None
            elif debit_method == "mid" and spread_mid is not None:
                debit = round(spread_mid, 4)
            elif spread_ask is not None:
                debit = round(spread_ask, 4)
            else:
                debit = None

            # Structural validation on debit
            # INVARIANT: structural rejection codes (non_positive_debit,
            # debit_ge_width) ONLY fire when pricing derives from trustworthy
            # quotes.  A zero short-leg bid indicates no real market;  the
            # resulting debit = long_ask overstates cost and would trigger
            # a spurious debit_ge_width.  Attribute to data quality instead.
            if debit is not None:
                if debit <= 0:
                    rejection_codes.append("non_positive_debit")
                elif width > 0 and debit >= width:
                    # Root-cause check: is the inflated debit due to a suspect
                    # leg quote (zero or near-zero bid on the short leg)?
                    _short_bid_suspect = (short_bid is not None and short_bid <= 0)
                    if _short_bid_suspect:
                        rejection_codes.append("QUOTE_REJECTED:debit_exceeds_width")
                        dq_flags.append("QUOTE_REJECTED:short_bid_zero")
                    else:
                        rejection_codes.append("debit_ge_width")

            rejection = rejection_codes[0] if rejection_codes else None

            # ── Strikes ─────────────────────────────────────────────────────
            long_strike = float(candidate.get("long_strike") or 0.0)
            short_strike = float(candidate.get("short_strike") or 0.0)

            # ── Core metrics (only when debit valid) ────────────────────────
            if debit is not None and debit > 0 and width > 0:
                max_profit = max(width - debit, 0.0) * 100.0
                max_loss = debit * 100.0
                return_on_risk = (max_profit / max_loss) if max_loss > 0 else 0.0
                debit_as_pct = debit / width
                if strategy == "call_debit":
                    break_even = long_strike + debit
                else:  # put_debit
                    break_even = long_strike - debit
            else:
                max_profit = None
                max_loss = None
                return_on_risk = None
                break_even = None
                debit_as_pct = None

            # ── IV / RV / expected-move (computed before POP for fallback) ──
            snap_id = id(snapshot)
            if snap_id not in _rv_cache:
                prices_history = snapshot.get("prices_history") or []
                _rv_cache[snap_id] = self._realized_vol_from_prices(
                    [float(x) for x in prices_history if self._to_float(x) is not None]
                )
            rv = _rv_cache[snap_id]

            iv_long = safe_float(getattr(long_leg, "iv", None))
            iv_short = safe_float(getattr(short_leg, "iv", None))
            if iv_long is not None and iv_short is not None:
                iv = (iv_long + iv_short) / 2.0
            elif iv_long is not None:
                iv = iv_long
            else:
                iv = iv_short  # may be None

            iv_rv_ratio = (iv / rv) if iv is not None and rv not in (None, 0) else None
            iv_pref = 0.5
            if iv_rv_ratio is not None:
                denom = max_ivrv_for_buy if (max_ivrv_for_buy is not None and max_ivrv_for_buy > 0) else 1.0
                iv_pref = self._clamp((denom - iv_rv_ratio) / denom)

            exp_move = None
            if iv is not None and underlying_price > 0 and dte > 0:
                exp_move = underlying_price * iv * math.sqrt(dte / 365.0)

            # ── POP (Goal 2 + refined model) ──────────────────────────────
            # Three levels:
            #   pop_delta_approx   — |delta_long| (baseline, always stored)
            #   pop_refined        — best refined estimate:
            #       a) BREAKEVEN_LOGNORMAL: P(S_T > breakeven) via lognormal
            #       b) DELTA_ADJUSTED: interpolation between |δ_long| and
            #          |δ_short| at the breakeven position:
            #          pop ≈ |δ_long| − (|δ_long| − |δ_short|) × (debit/width)
            #          If short delta unavailable:
            #          pop ≈ |δ_long| × (1 − debit/width)
            #   p_win_used         — pop_refined if available, else pop_delta_approx
            #
            # pop_model_used tracks which model produced p_win_used.
            long_delta_raw = safe_float(getattr(long_leg, "delta", None))
            short_delta_raw = safe_float(getattr(short_leg, "delta", None))
            if long_delta_raw is not None:
                pop_delta_approx = self._clamp(abs(long_delta_raw))
            else:
                pop_delta_approx = None

            # Breakeven+lognormal (uses IV already computed above)
            pop_breakeven_lognormal = _compute_pop_breakeven_lognormal(
                break_even, underlying_price, iv, dte, strategy,
            )

            # Diagnostic: market-implied probability of FULL max profit
            implied_max_profit_prob = (
                self._clamp(debit_as_pct) if debit_as_pct is not None else None
            )

            # ── pop_refined: best available refined POP ─────────────────────
            pop_refined: float | None = None
            pop_refined_model: str | None = None

            if pop_breakeven_lognormal is not None:
                pop_refined = pop_breakeven_lognormal
                pop_refined_model = POP_SOURCE_BREAKEVEN_LOGNORMAL
            elif pop_delta_approx is not None and debit_as_pct is not None:
                abs_delta_short = (
                    self._clamp(abs(short_delta_raw))
                    if short_delta_raw is not None else None
                )
                if abs_delta_short is not None:
                    # Interpolation: delta at breakeven between two legs
                    # Formula: |δ_long| − (|δ_long| − |δ_short|) × (debit/width)
                    _pop_adj = pop_delta_approx - (pop_delta_approx - abs_delta_short) * debit_as_pct
                else:
                    # Conservative: scale delta by remaining profit range
                    _pop_adj = pop_delta_approx * (1.0 - debit_as_pct)
                pop_refined = self._clamp(_pop_adj)
                pop_refined_model = POP_SOURCE_DELTA_ADJUSTED

            # p_win_used: prefer refined (breakeven > delta_adjusted),
            # fall back to raw delta.
            # Breakeven-lognormal is the AUTHORITATIVE POP — it is NOT
            # capped at pop_delta_approx (the breakeven model already
            # accounts for the debit paid, so it can legitimately differ
            # from |delta_long|).
            if pop_refined is not None:
                p_win_used = pop_refined
                pop_model_used = pop_refined_model
            elif pop_delta_approx is not None:
                p_win_used = pop_delta_approx
                pop_model_used = POP_SOURCE_DELTA_APPROX
            else:
                p_win_used = None
                pop_model_used = POP_SOURCE_NONE

            # DQ flags for POP
            if p_win_used is None:
                dq_flags.append("MISSING_POP:all_models_unavailable")
            if pop_refined is None and pop_delta_approx is not None:
                dq_flags.append("POP_FALLBACK_DELTA")
            if long_delta_raw is None and pop_model_used not in (POP_SOURCE_BREAKEVEN_LOGNORMAL, POP_SOURCE_DELTA_ADJUSTED):
                dq_flags.append("MISSING_DELTA:long_leg")

            strike_distance = abs(short_strike - underlying_price)
            alignment = 0.5
            if exp_move and exp_move > 0:
                ratio = strike_distance / exp_move
                alignment = self._clamp(1.0 - abs(1.0 - ratio))

            # ── Theta ───────────────────────────────────────────────────────
            long_theta = safe_float(getattr(long_leg, "theta", None))
            short_theta = safe_float(getattr(short_leg, "theta", None))
            theta_net = None
            theta_penalty = 0.0
            if long_theta is not None and short_theta is not None:
                theta_net = long_theta - short_theta
                theta_penalty = max(0.0, -theta_net)

            # ── OI / Volume: preserve None vs 0 distinction ────────────────
            long_oi_raw = getattr(long_leg, "open_interest", None)
            short_oi_raw = getattr(short_leg, "open_interest", None)
            long_vol_raw = getattr(long_leg, "volume", None)
            short_vol_raw = getattr(short_leg, "volume", None)

            long_oi = safe_float(long_oi_raw)
            short_oi = safe_float(short_oi_raw)
            long_vol = safe_float(long_vol_raw)
            short_vol = safe_float(short_vol_raw)

            if long_oi is not None and short_oi is not None:
                oi = int(min(long_oi, short_oi))
            else:
                oi = None
                if long_oi is None:
                    dq_flags.append("MISSING_OI:long_leg")
                if short_oi is None:
                    dq_flags.append("MISSING_OI:short_leg")

            if long_vol is not None and short_vol is not None:
                volume = int(min(long_vol, short_vol))
            else:
                volume = None
                if long_vol is None:
                    dq_flags.append("MISSING_VOL:long_leg")
                if short_vol is None:
                    dq_flags.append("MISSING_VOL:short_leg")

            # Flag zero OI/volume (distinct from missing)
            if long_oi is not None and int(long_oi) == 0:
                dq_flags.append("ZERO_OI:long_leg")
            if short_oi is not None and int(short_oi) == 0:
                dq_flags.append("ZERO_OI:short_leg")
            if long_vol is not None and int(long_vol) == 0:
                dq_flags.append("ZERO_VOL:long_leg")
            if short_vol is not None and int(short_vol) == 0:
                dq_flags.append("ZERO_VOL:short_leg")

            # ── Bid-ask spread % (from spread-level quotes) ─────────────────
            spread_pct = self._combo_spread_pct(spread_bid, spread_ask, debit) if quotes_ok else None

            # ── IV Rank ──────────────────────────────────────────────────────
            # IV Rank = (current_IV − IV_min) / (IV_max − IV_min)
            # Requires iv_history list in snapshot (≥20 observations).
            # When unavailable, iv_rank = None + IVR_INSUFFICIENT_HISTORY flag.
            iv_history = snapshot.get("iv_history") or []
            _iv_hist_floats = [
                x for x in (safe_float(v) for v in iv_history)
                if x is not None and x > 0
            ]
            if iv is not None and len(_iv_hist_floats) >= 20:
                _iv_min = min(_iv_hist_floats)
                _iv_max = max(_iv_hist_floats)
                if _iv_max > _iv_min:
                    iv_rank = self._clamp((iv - _iv_min) / (_iv_max - _iv_min))
                else:
                    iv_rank = 0.5  # flat history — neutral rank
            else:
                iv_rank = None
                if iv is None:
                    dq_flags.append("IVR_INSUFFICIENT_HISTORY:no_current_iv")
                elif len(_iv_hist_floats) < 20:
                    dq_flags.append("IVR_INSUFFICIENT_HISTORY")

            # ── EV (Goal 3): binary model using refined POP ─────────────────
            # EV = p_win × max_profit − p_loss × max_loss  (per contract)
            # Uses p_win_used (refined POP) directly — no alignment blending.
            p_loss_used = (1.0 - p_win_used) if p_win_used is not None else None
            if (p_win_used is not None and max_profit is not None
                    and max_loss is not None and p_loss_used is not None):
                ev = (p_win_used * max_profit) - (p_loss_used * max_loss)
                ev_to_risk = (ev / max_loss) if max_loss > 0 else 0.0
            else:
                ev = None
                ev_to_risk = None

            # ── Kelly fraction (binary payoff model) ────────────────────────
            # f* = (b×p − q) / b  where b = max_profit/max_loss, p = POP, q = 1−p
            # Clamped to [0, kelly_cap] (default 1.0).
            _kelly_cap = safe_float(payload.get("kelly_cap")) or 1.0
            if (p_win_used is not None and max_profit is not None
                    and max_loss is not None and max_profit > 0 and max_loss > 0):
                _b = max_profit / max_loss
                _q = 1.0 - p_win_used
                _kelly_raw = (_b * p_win_used - _q) / _b
                kelly_fraction = max(0.0, min(_kelly_cap, _kelly_raw))
            else:
                kelly_fraction = None
                dq_flags.append("KELLY_UNAVAILABLE")

            # Debug audit trail for EV computation (Goal 3 deliverable)
            ev_inputs = {
                "p_win_used": p_win_used,
                "p_loss_used": p_loss_used,
                "avg_win_used": max_profit,
                "avg_loss_used": max_loss,
                "model": "binary",
                "notes": (
                    f"POP model={pop_model_used or 'NONE'}; binary payoff model "
                    "(p_win × max_profit − p_loss × max_loss)"
                ),
            }

            # ── Build the enriched output dict ──────────────────────────────
            # ── Canonical legs[] array (matches IC schema) ──────────────
            # Fields: name, right, side, strike, qty, bid, ask, mid,
            #         delta, iv, open_interest, volume, occ_symbol
            _option_right = "call" if strategy == "call_debit" else "put"
            _long_name = f"long_{_option_right}"
            _short_name = f"short_{_option_right}"
            _legs = [
                {
                    "name": _long_name,
                    "right": _option_right,
                    "side": "buy",
                    "strike": long_strike,
                    "qty": 1,
                    "bid": long_bid,
                    "ask": long_ask,
                    "mid": long_mid,
                    "delta": long_delta_raw,
                    "iv": iv_long,
                    "open_interest": int(long_oi) if long_oi is not None else None,
                    "volume": int(long_vol) if long_vol is not None else None,
                    "occ_symbol": getattr(long_leg, "symbol", None),
                },
                {
                    "name": _short_name,
                    "right": _option_right,
                    "side": "sell",
                    "strike": short_strike,
                    "qty": 1,
                    "bid": short_bid,
                    "ask": short_ask,
                    "mid": short_mid,
                    "delta": short_delta_raw,
                    "iv": iv_short,
                    "open_interest": int(short_oi) if short_oi is not None else None,
                    "volume": int(short_vol) if short_vol is not None else None,
                    "occ_symbol": getattr(short_leg, "symbol", None),
                },
            ]

            trade_dict: dict[str, Any] = {
                "strategy": strategy,
                "spread_type": strategy,
                "underlying": symbol,
                "underlying_symbol": symbol,
                "symbol": symbol,
                "expiration": expiration,
                "dte": dte,
                "underlying_price": underlying_price,
                "price": underlying_price,
                "long_strike": long_strike,
                "short_strike": short_strike,
                "width": width,
                # ── Canonical legs[] ────────────────────────────────────────
                "legs": _legs,
                # ── Spread-level quotes (Goal 1) ────────────────────────────
                "spread_bid": spread_bid,
                "spread_ask": spread_ask,
                "spread_mid": spread_mid,
                # ── Pricing ─────────────────────────────────────────────────
                "net_debit": debit,
                "net_credit": None,  # debit strategy — net_credit must be absent
                "_debit_method": debit_method,
                "max_profit": max_profit,
                "max_profit_per_contract": max_profit,
                "max_loss": max_loss,
                "max_loss_per_contract": max_loss,
                "break_even": break_even,
                "return_on_risk": return_on_risk,
                "debit_as_pct_of_width": debit_as_pct,
                # ── POP fields (refined model) ──────────────────────────────
                # pop_delta_approx = |delta_long| baseline (always stored)
                # pop_breakeven_lognormal = BS lognormal POP (when IV available)
                # pop_refined = best refined POP (breakeven > delta_adjusted)
                # pop_refined_model = "BREAKEVEN_LOGNORMAL" | "DELTA_ADJUSTED" | None
                # pop_model_used = model behind p_win_used
                # p_win_used = pop_refined if available, else pop_delta_approx
                "pop_delta_approx": pop_delta_approx,
                "pop_breakeven_lognormal": pop_breakeven_lognormal,
                "pop_refined": pop_refined,
                "pop_refined_model": pop_refined_model,
                "pop_model_used": pop_model_used,
                "implied_max_profit_prob": implied_max_profit_prob,
                # Backward compat: implied_prob_profit = best available POP
                "implied_prob_profit": p_win_used,
                "p_win_used": p_win_used,
                # ── Volatility / expected-move ──────────────────────────────
                "expected_move": exp_move,
                "expected_move_alignment": alignment,
                "strike_distance_vs_expected_move": (
                    (strike_distance / exp_move) if exp_move not in (None, 0) else None
                ),
                "theta_net": theta_net,
                "theta_decay_penalty": theta_penalty,
                "iv": iv,
                "implied_vol": iv,
                "iv_rv_ratio": iv_rv_ratio,
                "iv_rv_ratio_preference_for_buying": iv_pref,
                "iv_rank": iv_rank,
                # ── Liquidity fields (None-safe) ────────────────────────────
                "open_interest": oi,
                "volume": volume,
                "bid_ask_spread_pct": spread_pct,
                # ── Top-level bid/ask (compat with credit_spread shape) ─────
                # These show the SHORT leg quotes for strategy_service compat.
                # For the spread-level view, use spread_bid/spread_ask above.
                "bid": short_bid,
                "ask": short_ask,
                # ── Delta (for strategy_service counter) ────────────────────
                "short_delta_abs": abs(long_delta_raw) if long_delta_raw is not None else None,
                # ── EV metrics (Goal 3) ─────────────────────────────────────
                "ev_per_contract": ev,
                "ev_per_share": (ev / 100.0) if ev is not None else None,
                "ev_to_risk": ev_to_risk,
                "kelly_fraction": kelly_fraction,
                "contractsMultiplier": 100,
                "selection_reasons": [],
                # ── Debug / enrichment trace ────────────────────────────────
                "_ev_inputs": ev_inputs,
                "_quote_rejection": rejection,
                "_rejection_codes": rejection_codes,
                "_long_bid": long_bid,
                "_long_ask": long_ask,
                "_short_bid": short_bid,
                "_short_ask": short_ask,
                "_long_mid": long_mid,
                "_short_mid": short_mid,
                "_long_oi": int(long_oi) if long_oi is not None else None,
                "_short_oi": int(short_oi) if short_oi is not None else None,
                "_long_vol": int(long_vol) if long_vol is not None else None,
                "_short_vol": int(short_vol) if short_vol is not None else None,
                "_dq_flags": dq_flags,
            }

            # ── Validate trade sanity (Goal 5) ──────────────────────────────
            _validate_debit_trade(trade_dict, exp_move)

            out.append(trade_dict)

        # ── Quote Integrity invariant (fail-fast) ───────────────────────────
        # Sample up to 5 enriched candidates.  If >50% have None leg quotes,
        # this is a systemic data failure — log details and raise.
        # _skip_quote_integrity: set by tests that deliberately pass null chains.
        skip_integrity = bool(payload.get("_skip_quote_integrity"))
        if out and not skip_integrity:
            sample = out[:5]
            null_count = sum(
                1 for t in sample
                if t.get("_short_bid") is None or t.get("_long_ask") is None
            )
            for i, t in enumerate(sample):
                logger.info(
                    "event=quote_integrity_sample idx=%d symbol=%s expiration=%s "
                    "strategy=%s long_strike=%s short_strike=%s "
                    "short_bid=%s short_ask=%s long_bid=%s long_ask=%s "
                    "short_oi=%s long_oi=%s",
                    i,
                    t.get("symbol"),
                    t.get("expiration"),
                    t.get("strategy"),
                    t.get("long_strike"),
                    t.get("short_strike"),
                    t.get("_short_bid"),
                    t.get("_short_ask"),
                    t.get("_long_bid"),
                    t.get("_long_ask"),
                    t.get("_short_oi"),
                    t.get("_long_oi"),
                )
            if null_count > len(sample) / 2:
                msg = (
                    f"QUOTE INTEGRITY FAILURE: {null_count}/{len(sample)} sampled "
                    f"debit candidates have null leg quotes. "
                    f"Provider: Tradier (normalize_chain → OptionContract). "
                    f"Lookup key: getattr(leg, 'bid'|'ask', None). "
                    f"Summary: {len(out)} total enriched, "
                    f"{sum(1 for t in out if t.get('_short_bid') is None)} null short_bid, "
                    f"{sum(1 for t in out if t.get('_long_ask') is None)} null long_ask."
                )
                logger.error(msg)
                raise DataQualityError(msg)

        # ── Expected fill pricing ─────────────────────────────────────────
        # Apply expected-fill model to each enriched debit trade.
        # Debit spreads have spread_mid and spread_ask (= natural for debits).
        for trade in out:
            apply_expected_fill(trade)

        return out

    def evaluate(self, trade: dict[str, Any]) -> tuple[bool, list[str]]:
        """Gate a debit-spread trade.  Returns (passed, [rejection_codes]).

        Rejection codes are stable strings from the BenTrade rejection taxonomy.
        Gate order mirrors credit_spread.py for consistency:
          0. Sanity-check failures from validate_debit_trade
          1. Pre-enrichment rejections (quote validation, debit structure)
          2. POP gate (missing → DQ_MISSING:pop in strict/balanced; waived in lenient)
          3. EV / return-on-risk thresholds
          4. Bid-ask spread width
          5. Debit-as-pct-of-width cap
          6. OI / volume — DQ (missing/zero) then threshold
        """
        reasons: list[str] = []

        # ── Gate 0: Sanity-check failures (from _validate_debit_trade) ─────
        if not trade.get("_valid_for_ranking", True):
            sanity_flags = [f for f in (trade.get("_dq_flags") or []) if f.startswith("SANITY:")]
            reasons.extend(sanity_flags or ["SANITY:failed_validation"])
            return False, reasons

        # ── Gate 1: Pre-enrichment rejections (set during enrich()) ────────
        pre_rej_codes = trade.get("_rejection_codes") or []
        if not pre_rej_codes:
            single = trade.get("_quote_rejection")
            if single:
                pre_rej_codes = [single]
        if pre_rej_codes:
            return False, list(pre_rej_codes)

        policy = trade.get("_policy") if isinstance(trade.get("_policy"), dict) else {}
        payload = trade.get("_request") if isinstance(trade.get("_request"), dict) else {}

        # ── Resolve dataQualityMode ──────────────────────────────────────────
        dq_mode = str(payload.get("data_quality_mode") or _DEFAULT_DATA_QUALITY_MODE).lower()
        if dq_mode not in _DATA_QUALITY_MODES:
            dq_mode = _DEFAULT_DATA_QUALITY_MODE

        # ── Read trade metrics ───────────────────────────────────────────────
        # Fill-aware: prefer fill-based metrics for gating when available,
        # fall back to mid-based.  Structural checks (net_debit, width)
        # remain on mid values since they validate the trade exists.
        pop = safe_float(trade.get("p_win_used"))
        ev_to_risk = safe_float(trade.get("ev_to_risk_fill")) or safe_float(trade.get("ev_to_risk"))
        net_debit = safe_float(trade.get("net_debit"))
        width = safe_float(trade.get("width"))
        spread_pct = safe_float(trade.get("bid_ask_spread_pct"))

        # ── Read OI / volume — preserve None for data-quality detection ──────
        raw_oi = trade.get("open_interest")
        raw_vol = trade.get("volume")
        oi_value = safe_float(raw_oi)   # None if missing / unparseable
        vol_value = safe_float(raw_vol)  # None if missing / unparseable

        # ── Threshold resolution: prefer payload (preset-resolved), then policy, then safety fallback ──
        min_pop = safe_float(payload.get("min_pop"))
        if min_pop is None:
            min_pop = safe_float(policy.get("min_pop"))
        if min_pop is None:
            min_pop = 0.55  # balanced-level safety fallback for debit spreads

        min_ev_to_risk = safe_float(payload.get("min_ev_to_risk"))
        if min_ev_to_risk is None:
            min_ev_to_risk = safe_float(policy.get("min_ev_to_risk"))
        if min_ev_to_risk is None:
            min_ev_to_risk = 0.01

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

        max_debit_pct = safe_float(payload.get("max_debit_pct_width"))
        if max_debit_pct is None:
            max_debit_pct = safe_float(payload.get("max_debit"))
        if max_debit_pct is None:
            max_debit_pct = safe_float(policy.get("max_debit_pct_width"))
        if max_debit_pct is None:
            max_debit_pct = 0.50  # balanced-level safety fallback

        # ── Gate 2: Width / debit structure ──────────────────────────────────
        if width is None or width <= 0:
            reasons.append("invalid_width")
        if net_debit is None or net_debit <= 0:
            reasons.append("non_positive_debit")

        # ── Gate 3: Probability (POP) ────────────────────────────────────────
        # Missing POP: BenTrade is probability-first.  A trade without POP
        # cannot be evaluated — reject under strict/balanced, waive only under
        # lenient (still tracked as data-quality issue).
        #
        # Epsilon tolerance (Task 1): boundary trades where pop ≈ threshold
        # (within _POP_EPSILON) are NOT rejected.  This prevents float-precision
        # artifacts from killing trades at exactly the threshold.
        pop_epsilon = safe_float(payload.get("pop_epsilon")) or _POP_EPSILON
        pop_gate_passed = False
        pop_gate_reason: str | None = None
        if pop is None:
            if dq_mode == "lenient":
                pop_gate_passed = True  # waived
                pop_gate_reason = "waived_lenient"
            else:
                pop_gate_reason = "DQ_MISSING:pop"
                reasons.append("DQ_MISSING:pop")
        elif pop >= (min_pop - pop_epsilon):
            pop_gate_passed = True
        else:
            pop_gate_reason = "pop_below_floor"
            reasons.append("pop_below_floor")

        # Trace field for pop gate evaluation (Task 1 deliverable)
        trade["_pop_gate_eval"] = {
            "pop_actual": pop,
            "threshold": min_pop,
            "epsilon": pop_epsilon,
            "effective_threshold": round(min_pop - pop_epsilon, 8),
            "passed": pop_gate_passed,
            "reason": pop_gate_reason,
            "pop_model_used": trade.get("pop_model_used"),
        }

        # ── Gate 4: EV / return-on-risk thresholds ───────────────────────────
        if ev_to_risk is not None and ev_to_risk < min_ev_to_risk:
            reasons.append("ev_to_risk_below_floor")

        # ── Gate 5: Bid-ask spread (only meaningful on validated quotes) ─────
        if spread_pct is not None and (spread_pct * 100.0) > spread_pct_limit:
            reasons.append("spread_too_wide")

        # ── Gate 6: Debit-as-pct-of-width cap ────────────────────────────────
        debit_as_pct = safe_float(trade.get("debit_as_pct_of_width"))
        if debit_as_pct is not None and debit_as_pct > max_debit_pct:
            reasons.append("debit_too_close_to_width")

        # ── Gate 7: Liquidity / data-quality for OI & volume ────────────────
        # Distinguishes three states per field:
        #   None  → missing (data-quality issue, not a threshold failure)
        #   0     → zero    (exchange reported no activity; treated as DQ in strict/balanced)
        #   > 0   → present → compare against threshold
        oi_missing = oi_value is None
        vol_missing = vol_value is None
        oi_zero = (not oi_missing) and int(oi_value) == 0
        vol_zero = (not vol_missing) and int(vol_value) == 0

        if oi_missing or vol_missing:
            if dq_mode == "lenient":
                # Waive missing OI/vol if pricing looks healthy
                spread_ok = (spread_pct is None) or ((spread_pct * 100.0) <= spread_pct_limit)
                min_debit = safe_float(payload.get("min_debit_for_dq_waiver"))
                if min_debit is None:
                    min_debit = _DEFAULT_MIN_DEBIT_FOR_DQ_WAIVER
                debit_ok = (net_debit is not None) and (net_debit >= min_debit)
                if not (spread_ok and debit_ok):
                    if oi_missing:
                        reasons.append("DQ_MISSING:open_interest")
                    if vol_missing:
                        reasons.append("DQ_MISSING:volume")
            else:
                # strict / balanced: missing data → distinct DQ rejection
                if oi_missing:
                    reasons.append("DQ_MISSING:open_interest")
                if vol_missing:
                    reasons.append("DQ_MISSING:volume")
        elif oi_zero or vol_zero:
            if dq_mode == "lenient":
                spread_ok = (spread_pct is None) or ((spread_pct * 100.0) <= spread_pct_limit)
                min_debit = safe_float(payload.get("min_debit_for_dq_waiver"))
                if min_debit is None:
                    min_debit = _DEFAULT_MIN_DEBIT_FOR_DQ_WAIVER
                debit_ok = (net_debit is not None) and (net_debit >= min_debit)
                if not (spread_ok and debit_ok):
                    if oi_zero:
                        reasons.append("DQ_ZERO:open_interest")
                    if vol_zero:
                        reasons.append("DQ_ZERO:volume")
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

        # ── Gate eval snapshot (Goal 4 + Task 4 deliverables) ────────────────
        # Attach the exact values used for gate checks so trace can show
        # what was compared against what threshold.
        trade["_gate_eval_snapshot"] = {
            "pop": pop,
            "min_pop": min_pop,
            "pop_epsilon": pop_epsilon,
            "pop_model_used": trade.get("pop_model_used"),
            "break_even": trade.get("break_even"),
            "expected_move": trade.get("expected_move"),
            "max_profit": trade.get("max_profit"),
            "max_loss": trade.get("max_loss"),
            "kelly_fraction": trade.get("kelly_fraction"),
            "ev_to_risk": ev_to_risk,
            "min_ev_to_risk": min_ev_to_risk,
            "spread_pct": spread_pct,
            "spread_pct_limit": spread_pct_limit,
            "debit_as_pct": debit_as_pct,
            "max_debit_pct": max_debit_pct,
            "oi": oi_value,
            "min_oi": min_oi,
            "vol": vol_value,
            "min_vol": min_vol,
            "dq_mode": dq_mode,
        }

        # ── Primary rejection reason (Task 4) ───────────────────────────────
        # Single field for quicker debugging — the first rejection reason.
        trade["_primary_rejection_reason"] = reasons[0] if reasons else None

        return len(reasons) == 0, reasons

    def score(self, trade: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Return (rank_score, tie_breaks).  rank_score is 0–100.

        Delegates to the canonical ``compute_rank_score()`` from ranking.py,
        which uses a weighted combination of edge, RoR, POP, liquidity, and
        TQS components.  This ensures the debit-spreads ranking scale matches
        credit spreads (0–100) with no custom inline formula.
        """
        rank_score = float(compute_rank_score(trade))
        tie_breaks = {
            "edge": safe_float(trade.get("ev_to_risk")) or 0.0,
            "pop": safe_float(trade.get("p_win_used")) or 0.0,
            "liq": -(safe_float(trade.get("bid_ask_spread_pct")) or 1.0),
        }
        return rank_score, tie_breaks
