"""Unified trade normalizer – single source of truth for trade output shape.

Every consumer (scanner reports, homepage recommendations, workbench trade
lookup, admin data workbench) calls ``normalize_trade()`` to guarantee:

- canonical symbol / strategy / trade-key identities
- per-contract monetary values as primary (per-share × multiplier)
- ``computed``, ``details``, ``pills`` sub-dicts for UI consumption
- ``computed_metrics`` + ``metrics_status`` via shared contract
- spread pricing context (spread_mid, spread_natural) for execution
- breakeven derivation for vertical spreads
- OCC symbol validation on legs
- validation warnings for missing key metrics

Use ``strip_legacy_fields()`` at the API boundary to remove legacy flat
fields from trade dicts before returning to the frontend.
"""

from __future__ import annotations

import logging
from typing import Any

from app.utils.computed_metrics import apply_metrics_contract
from app.utils.strategy_id_resolver import resolve_strategy_id_or_none
from app.utils.trade_key import canonicalize_strategy_id, canonicalize_trade_key, trade_key

_log = logging.getLogger("bentrade.normalize")


# ── helpers ───────────────────────────────────────────────────────────


def _to_float(value: Any) -> float | None:
    """Convert *value* to float, returning ``None`` for blanks / junk."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(row: dict[str, Any], *keys: str) -> float | None:
    """Return the first non-``None`` float found in *row* for *keys*."""
    for key in keys:
        value = _to_float(row.get(key))
        if value is not None:
            return value
    return None


def _upsert_warning(row: dict[str, Any], code: str) -> None:
    """Append *code* to ``validation_warnings`` if not already present."""
    warnings = row.get("validation_warnings") if isinstance(row.get("validation_warnings"), list) else []
    if code not in warnings:
        warnings.append(code)
    row["validation_warnings"] = warnings


# ── legacy-field stripping (API boundary) ─────────────────────────────

_LEGACY_FLAT_FIELDS: frozenset[str] = frozenset({
    # Per-share / per-contract duplicates (now in computed.*)
    "ev_per_share",
    "ev_per_contract",
    "max_profit_per_share",
    "max_loss_per_share",
    "max_profit_per_contract",
    "max_loss_per_contract",
    # Legacy metric names (now in computed / details)
    "p_win_used",
    "pop_delta_approx",
    "pop_approx",
    "probability_of_profit",
    "implied_prob_profit",
    "ev_to_risk",
    "bid_ask_spread_pct",
    "strike_distance_pct",
    "realized_vol_20d",
    "estimated_risk",
    "risk_amount",
    "estimated_max_profit",
    "premium_received",
    "premium_paid",
    "scanner_score",
    "expiration_date",
    # Duplicate identity fields (use strategy_id / symbol instead)
    "spread_type",
    "strategy",
    "underlying",
    "underlying_symbol",
})


def strip_legacy_fields(trade: dict[str, Any]) -> dict[str, Any]:
    """Remove legacy flat fields from a trade dict for API responses.

    Call this at every outbound API boundary so the frontend never
    receives deprecated / ambiguous root-level fields.  The canonical
    data lives in ``computed``, ``details``, ``pills``, and the root
    identity keys (``symbol``, ``strategy_id``, ``trade_key``, etc.).
    """
    return {k: v for k, v in trade.items() if k not in _LEGACY_FLAT_FIELDS}


# ── strategy display labels ──────────────────────────────────────────

_STRATEGY_LABELS: dict[str, str] = {
    "put_credit_spread": "Put Credit Spread",
    "call_credit_spread": "Call Credit Spread",
    "put_debit": "Put Debit Spread",
    "call_debit": "Call Debit Spread",
    "iron_condor": "Iron Condor",
    "butterfly_debit": "Debit Butterfly",
    "calendar_spread": "Calendar Spread",
    "calendar_call_spread": "Call Calendar Spread",
    "calendar_put_spread": "Put Calendar Spread",
    "csp": "Cash Secured Put",
    "covered_call": "Covered Call",
    "income": "Income Strategy",
    "single": "Single Option",
    "long_call": "Long Call",
    "long_put": "Long Put",
}


def strategy_label(strategy_id: str) -> str:
    """Human-readable label for a canonical strategy ID."""
    key = str(strategy_id or "").strip().lower()
    return _STRATEGY_LABELS.get(key, key.replace("_", " ").title() or "Trade")


# ── composite-strike derivation ──────────────────────────────────────


def _derive_key_strikes(row: dict[str, Any], spread_type: str) -> tuple[Any, Any]:
    """Derive short/long strike values for trade-key generation.

    Handles composite strategies (iron condor, butterfly) that represent
    strikes differently from simple two-leg spreads.
    """
    short = row.get("short_strike")
    long = row.get("long_strike")
    if short not in (None, "") or long not in (None, ""):
        return short, long

    if spread_type == "iron_condor":
        return (
            f"P{row.get('put_short_strike') or 'NA'}|C{row.get('call_short_strike') or 'NA'}",
            f"P{row.get('put_long_strike') or 'NA'}|C{row.get('call_long_strike') or 'NA'}",
        )

    if spread_type == "butterfly_debit":
        center = row.get("center_strike") or row.get("short_strike") or "NA"
        lower = row.get("lower_strike") or "NA"
        upper = row.get("upper_strike") or "NA"
        return center, f"L{lower}|U{upper}"

    # Generic fallback: use "strike" for short_strike if available.
    strike = row.get("strike")
    if strike not in (None, ""):
        return strike, "NA"

    return short, long


# ── spread pricing helpers ────────────────────────────────────────────


def _compute_spread_pricing(
    normalized: dict[str, Any],
    spread_type: str,
) -> dict[str, float | None]:
    """Derive spread_mid, spread_natural, spread_mark from legs.

    For CREDIT spreads (selling):
      spread_mid     = short_leg.mid − long_leg.mid
      spread_natural = short_leg.bid − long_leg.ask   (worst-case fill for seller)

    For DEBIT spreads (buying):
      spread_mid     = long_leg.mid − short_leg.mid
      spread_natural = long_leg.ask − short_leg.bid   (worst-case fill for buyer)

    Inputs: legs[].bid, legs[].ask, legs[].mid
    Outputs: spread_mid, spread_natural, spread_mark (avg of mid+natural)
    """
    from app.utils.computed_metrics import is_credit_strategy, is_debit_strategy

    result: dict[str, float | None] = {
        "spread_mid": None,
        "spread_natural": None,
        "spread_mark": None,
    }

    # Use upstream value if already computed
    existing_mid = _to_float(normalized.get("spread_mid"))
    if existing_mid is not None:
        result["spread_mid"] = existing_mid

    legs = normalized.get("legs")
    if not isinstance(legs, list) or len(legs) < 2:
        return result

    # Identify short and long legs
    short_leg = None
    long_leg = None
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        side = str(leg.get("side") or "").lower()
        if side in ("sell", "sell_to_open") and short_leg is None:
            short_leg = leg
        elif side in ("buy", "buy_to_open") and long_leg is None:
            long_leg = leg

    if short_leg is None or long_leg is None:
        return result

    s_bid = _to_float(short_leg.get("bid"))
    s_ask = _to_float(short_leg.get("ask"))
    s_mid = _to_float(short_leg.get("mid"))
    l_bid = _to_float(long_leg.get("bid"))
    l_ask = _to_float(long_leg.get("ask"))
    l_mid = _to_float(long_leg.get("mid"))

    # Derive mid from bid+ask if not explicitly provided
    if s_mid is None and s_bid is not None and s_ask is not None:
        s_mid = (s_bid + s_ask) / 2.0
    if l_mid is None and l_bid is not None and l_ask is not None:
        l_mid = (l_bid + l_ask) / 2.0

    is_credit = is_credit_strategy(spread_type)

    # Spread mid
    if s_mid is not None and l_mid is not None and result["spread_mid"] is None:
        if is_credit:
            result["spread_mid"] = round(s_mid - l_mid, 4)
        else:
            result["spread_mid"] = round(l_mid - s_mid, 4)

    # Spread natural (worst-case fill)
    if is_credit:
        # Selling: natural = short_bid − long_ask
        if s_bid is not None and l_ask is not None:
            nat = round(s_bid - l_ask, 4)
            if nat < 0:
                # Negative natural means the market is inverted — null + warning.
                # Input fields: short_leg.bid, long_leg.ask
                # Formula: spread_natural = short_bid − long_ask
                result["spread_natural"] = None
                _upsert_warning(
                    normalized,
                    "NEGATIVE_NATURAL_PRICE",
                )
                _log.warning(
                    "[normalize] NEGATIVE_NATURAL_PRICE: short_bid=%.4f long_ask=%.4f nat=%.4f spread_type=%s",
                    s_bid, l_ask, nat, spread_type,
                )
            else:
                result["spread_natural"] = nat
    else:
        # Buying: natural = long_ask − short_bid
        if l_ask is not None and s_bid is not None:
            result["spread_natural"] = round(l_ask - s_bid, 4)

    # Spread mark = average of mid and natural when both available
    mid_val = result["spread_mid"]
    nat_val = result["spread_natural"]
    if mid_val is not None and nat_val is not None:
        result["spread_mark"] = round((mid_val + nat_val) / 2.0, 4)

    return result


def _compute_breakeven(
    normalized: dict[str, Any],
    spread_type: str,
) -> float | None:
    """Derive breakeven for vertical spreads.

    For put credit spread:
      breakeven = short_put_strike − net_credit

    For call credit spread:
      breakeven = short_call_strike + net_credit

    For put debit spread:
      breakeven = long_put_strike + net_debit

    For call debit spread:
      breakeven = long_call_strike − net_debit

    Inputs: short_strike/long_strike, net_credit/net_debit
    Output: breakeven price (float)
    """
    # Prefer upstream value if already computed
    existing = _to_float(normalized.get("break_even")) or _to_float(normalized.get("break_even_low"))
    if existing is not None:
        return existing

    sid = (spread_type or "").strip().lower()
    short_strike = _to_float(normalized.get("short_strike"))
    long_strike = _to_float(normalized.get("long_strike"))
    net_credit = _to_float(normalized.get("net_credit"))
    net_debit = _to_float(normalized.get("net_debit"))

    if sid in ("put_credit_spread", "credit_spread"):
        if short_strike is not None and net_credit is not None:
            return round(short_strike - net_credit, 4)
    elif sid == "call_credit_spread":
        if short_strike is not None and net_credit is not None:
            return round(short_strike + net_credit, 4)
    elif sid in ("put_debit", "put_debit_spread"):
        if long_strike is not None and net_debit is not None:
            return round(long_strike + net_debit, 4)
    elif sid in ("call_debit", "call_debit_spread"):
        if long_strike is not None and net_debit is not None:
            return round(long_strike - net_debit, 4)
    elif sid == "iron_condor":
        # Iron condor has two breakevens — compute lower (put-side)
        # and return the more conservative one
        sp = _to_float(normalized.get("short_put_strike"))
        sc = _to_float(normalized.get("short_call_strike"))
        if sp is not None and net_credit is not None:
            return round(sp - net_credit, 4)
        if short_strike is not None and net_credit is not None:
            return round(short_strike - net_credit, 4)

    return None


def _validate_legs_occ(normalized: dict[str, Any]) -> None:
    """Warn if any leg is missing its OCC symbol.

    Checks each leg in legs[] for occ_symbol presence.
    Appends MISSING_OCC_SYMBOL validation warning if any are absent.
    Sets execution_invalid=True on the trade if OCC is missing.
    """
    legs = normalized.get("legs")
    if not isinstance(legs, list) or len(legs) == 0:
        return

    missing_count = 0
    for i, leg in enumerate(legs):
        if not isinstance(leg, dict):
            continue
        occ = leg.get("occ_symbol") or leg.get("option_symbol")
        if not occ or not str(occ).strip():
            missing_count += 1
            _log.warning(
                "leg[%d] missing OCC symbol: strike=%s side=%s",
                i, leg.get("strike"), leg.get("side"),
            )

    if missing_count > 0:
        _upsert_warning(normalized, "MISSING_OCC_SYMBOL")
        normalized["execution_invalid"] = True
        normalized["execution_invalid_reason"] = (
            f"{missing_count} leg(s) missing OCC symbol — execution blocked"
        )


# ── main entry point ─────────────────────────────────────────────────


def normalize_trade(
    trade: dict[str, Any],
    *,
    strategy_id: str | None = None,
    expiration: str | None = None,
    derive_dte: bool = False,
) -> dict[str, Any]:
    """Normalize a raw trade dict into the canonical output shape.

    Parameters
    ----------
    trade:
        Raw trade dict from a scanner plugin, persisted report, or workbench
        record.
    strategy_id:
        Optional strategy hint (e.g. ``"butterflies"``).  Falls back to the
        trade's own ``spread_type`` / ``strategy`` / ``strategy_id`` fields.
    expiration:
        Optional expiration hint.  Falls back to the trade's own
        ``expiration`` field.
    derive_dte:
        When ``True`` and no ``dte`` field is present, attempt to derive DTE
        from the expiration date using ``dte_ceil()``.

    Returns
    -------
    dict
        Canonical fields: ``underlying``, ``underlying_symbol``, ``symbol``,
        ``strategy_id``, ``spread_type``, ``strategy``, ``trade_key``,
        ``computed``, ``details``, ``pills``, ``computed_metrics``,
        ``metrics_status``, and legacy back-fill aliases.
    """
    normalized = dict(trade or {})

    # ── 0. Seed root from existing sub-dicts (safe re-normalization) ──
    # When a trade was previously normalized and saved with values
    # only in computed/details/computed_metrics sub-dicts (not at root),
    # re-normalization must be able to find them.
    # GUARD: Never propagate the *wrong* cashflow field from an old
    # sub-dict.  E.g. a stored report with a corrupted computed_metrics
    # that has net_debit on a credit strategy must not seed root.net_debit.
    from app.utils.computed_metrics import is_credit_strategy, is_debit_strategy
    _step0_strat = (
        str(normalized.get("spread_type")
            or normalized.get("strategy")
            or normalized.get("strategy_id")
            or strategy_id
            or "").strip().lower()
    )
    _step0_skip: set[str] = set()
    if is_credit_strategy(_step0_strat):
        _step0_skip.add("net_debit")       # never seed wrong cashflow
    elif is_debit_strategy(_step0_strat):
        _step0_skip.add("net_credit")
    for _subkey in ("computed", "computed_metrics", "details"):
        _sub = normalized.get(_subkey)
        if isinstance(_sub, dict):
            for _k, _v in _sub.items():
                if _k in _step0_skip:
                    continue
                if normalized.get(_k) is None and _v is not None:
                    normalized[_k] = _v

    # ── 1. Symbol triple-write ────────────────────────────────────────
    symbol = str(
        normalized.get("underlying")
        or normalized.get("underlying_symbol")
        or normalized.get("symbol")
        or ""
    ).upper()
    if symbol:
        normalized["underlying"] = symbol
        normalized["underlying_symbol"] = symbol
        normalized["symbol"] = symbol

    # ── 2. Strategy canonicalization + triple-write ───────────────────
    raw_spread_type = (
        normalized.get("spread_type")
        or normalized.get("strategy")
        or normalized.get("strategy_id")
        or strategy_id
    )
    # Use the single resolver (emits STRATEGY_ALIAS_USED for aliases).
    spread_type = resolve_strategy_id_or_none(raw_spread_type)
    # Keep old canonicalize call for alias_mapped metadata (cheap, no side-effects).
    _, alias_mapped, provided_strategy = canonicalize_strategy_id(raw_spread_type)
    spread_type = spread_type or str(strategy_id or raw_spread_type or "NA").strip().lower() or "NA"
    normalized["spread_type"] = spread_type
    normalized["strategy"] = spread_type
    normalized["strategy_id"] = spread_type

    if strategy_id is not None:
        normalized["strategyId"] = strategy_id

    # ── 3. Expiration ────────────────────────────────────────────────
    exp = str(normalized.get("expiration") or expiration or "").strip() or "NA"
    normalized["expiration"] = exp

    # ── 4. DTE derivation ────────────────────────────────────────────
    dte_value = normalized.get("dte")
    if dte_value in (None, "") and derive_dte and exp not in ("", "NA"):
        try:
            from app.utils.dates import dte_ceil

            dte_value = dte_ceil(exp)
        except Exception:
            dte_value = None
    normalized["dte"] = dte_value

    # ── 5. Composite-strike derivation + key generation ──────────────
    key_short_strike, key_long_strike = _derive_key_strikes(normalized, spread_type)
    if normalized.get("short_strike") in (None, ""):
        normalized["short_strike"] = key_short_strike
    if normalized.get("long_strike") in (None, ""):
        normalized["long_strike"] = key_long_strike

    provided_key = str(normalized.get("trade_key") or "").strip()
    generated_key = trade_key(
        underlying=symbol,
        expiration=exp,
        spread_type=spread_type,
        short_strike=key_short_strike,
        long_strike=key_long_strike,
        dte=dte_value,
    )
    tkey = canonicalize_trade_key(provided_key) if provided_key else generated_key
    normalized["trade_key"] = tkey
    normalized["trade_id"] = tkey
    normalized.pop("_trade_key", None)

    # ── 6. composite_score ↔ rank_score alias ────────────────────────
    if normalized.get("composite_score") is None and normalized.get("rank_score") is not None:
        normalized["composite_score"] = normalized.get("rank_score")

    # ── 7. Per-share → per-contract scaling ──────────────────────────
    multiplier = _to_float(normalized.get("contractsMultiplier") or normalized.get("contracts_multiplier")) or 100.0

    expected_value_contract = _first_number(normalized, "ev_per_contract", "expected_value", "ev")
    if expected_value_contract is None:
        ev_share = _first_number(normalized, "ev_per_share")
        if ev_share is not None:
            expected_value_contract = ev_share * multiplier

    max_profit_contract = _first_number(normalized, "max_profit_per_contract")
    if max_profit_contract is None:
        mp_share = _first_number(normalized, "max_profit_per_share")
        if mp_share is not None:
            max_profit_contract = mp_share * multiplier
        else:
            max_profit_contract = _first_number(normalized, "max_profit")

    max_loss_contract = _first_number(normalized, "max_loss_per_contract")
    if max_loss_contract is None:
        ml_share = _first_number(normalized, "max_loss_per_share")
        if ml_share is not None:
            max_loss_contract = ml_share * multiplier
        else:
            max_loss_contract = _first_number(normalized, "max_loss")

    # ── 7b. Spread pricing context (mid + natural) ─────────────────
    # Derived from legs[].bid/ask/mid.  Upstream values preserved if present.
    pricing = _compute_spread_pricing(normalized, spread_type)
    if pricing["spread_mid"] is not None:
        normalized["spread_mid"] = pricing["spread_mid"]
    if pricing["spread_natural"] is not None:
        normalized["spread_natural"] = pricing["spread_natural"]
    if pricing["spread_mark"] is not None:
        normalized["spread_mark"] = pricing["spread_mark"]

    # ── 7c. Breakeven derivation ─────────────────────────────────────
    # Derived from short_strike/long_strike + net_credit/net_debit.
    # Prefer upstream value if already present.
    breakeven = _compute_breakeven(normalized, spread_type)
    if breakeven is not None:
        normalized["break_even"] = breakeven

    # ── 7d. OCC symbol validation on legs ────────────────────────────
    _validate_legs_occ(normalized)

    # ── 8. Build computed / details / pills ──────────────────────────
    computed: dict[str, Any] = {
        "max_profit": max_profit_contract,
        "max_loss": max_loss_contract,
        "pop": _first_number(
            normalized,
            "p_win_used",
            "pop_delta_approx",
            "pop_approx",
            "probability_of_touch_center",
            "implied_prob_profit",
            "pop",
        ),
        "return_on_risk": _first_number(normalized, "return_on_risk", "ror"),
        "expected_value": expected_value_contract,
        "kelly_fraction": _first_number(normalized, "kelly_fraction"),
        "iv_rank": _first_number(normalized, "iv_rank"),
        "short_strike_z": _first_number(normalized, "short_strike_z"),
        "bid_ask_pct": _first_number(normalized, "bid_ask_spread_pct"),
        "strike_dist_pct": _first_number(
            normalized,
            "strike_distance_pct",
            "strike_distance_vs_expected_move",
            "expected_move_ratio",
        ),
        "rsi14": _first_number(normalized, "rsi14", "rsi_14"),
        "rv_20d": _first_number(normalized, "realized_vol_20d", "rv_20d"),
        "open_interest": _first_number(normalized, "open_interest"),
        "volume": _first_number(normalized, "volume"),
        "ev_to_risk": (
            _first_number(normalized, "ev_to_risk")
            if _first_number(normalized, "ev_to_risk") is not None
            else (
                round(expected_value_contract / abs(max_loss_contract), 4)
                if expected_value_contract is not None and max_loss_contract and abs(max_loss_contract) > 0
                else None
            )
        ),
    }

    details: dict[str, Any] = {
        "break_even": _first_number(normalized, "break_even", "break_even_low"),
        "dte": _first_number(normalized, "dte"),
        "expected_move": _first_number(normalized, "expected_move", "expected_move_near"),
        "iv_rv_ratio": _first_number(normalized, "iv_rv_ratio"),
        "trade_quality_score": _first_number(normalized, "trade_quality_score"),
        "market_regime": str(normalized.get("market_regime") or normalized.get("regime") or "").strip() or None,
    }

    # ── 8b. Pricing context sub-dict ─────────────────────────────────
    # Surfaces spread_mid, spread_natural, spread_mark for UI / ticket.
    normalized["pricing"] = {
        "spread_mid": _to_float(normalized.get("spread_mid")),
        "spread_natural": _to_float(normalized.get("spread_natural")),
        "spread_mark": _to_float(normalized.get("spread_mark")),
    }

    dte_front = _first_number(normalized, "dte_near")
    dte_back = _first_number(normalized, "dte_far")
    pills: dict[str, Any] = {
        "strategy_label": strategy_label(spread_type),
        "dte": details["dte"],
        "pop": computed["pop"],
        "oi": computed["open_interest"],
        "vol": computed["volume"],
        "regime_label": details["market_regime"],
    }
    if dte_front is not None and dte_back is not None:
        pills["dte_front"] = dte_front
        pills["dte_back"] = dte_back
        pills["dte_label"] = (
            f"DTE {int(dte_front) if float(dte_front).is_integer() else dte_front}"
            f"/{int(dte_back) if float(dte_back).is_integer() else dte_back}"
        )

    normalized["computed"] = computed
    normalized["details"] = details
    normalized["pills"] = pills

    # ── 9. apply_metrics_contract (computed_metrics + metrics_status) ─
    normalized = apply_metrics_contract(normalized)

    # ── 9b. Cashflow schema invariant check & correction ───────────────
    # Credit strategies must have net_credit, not net_debit.
    # Debit strategies must have net_debit, not net_credit.
    # Old stored reports may have swapped values — detect and FIX, not
    # just warn.  Correction: move the wrong-side value to the correct
    # side if the correct side is empty, then null the wrong side.
    from app.utils.computed_metrics import is_credit_strategy, is_debit_strategy
    _cm = normalized.get("computed_metrics") or {}
    if is_credit_strategy(spread_type):
        if _cm.get("net_debit") is not None:
            _upsert_warning(normalized, "SCHEMA_MISMATCH_NET_DEBIT_FOR_CREDIT")
            # Correct: if net_credit is missing, the net_debit is likely
            # the swapped net_credit value.  Move it to the right field.
            if _cm.get("net_credit") is None:
                _cm["net_credit"] = _cm["net_debit"]
            _cm["net_debit"] = None
            normalized["computed_metrics"] = _cm
        if _cm.get("net_credit") is None and normalized.get("net_credit") is not None:
            _cm["net_credit"] = normalized["net_credit"]
            normalized["computed_metrics"] = _cm
            _upsert_warning(normalized, "SCHEMA_MISMATCH_NET_CREDIT_MISSING_IN_METRICS")
        # Also ensure root is clean
        normalized.pop("net_debit", None)
    elif is_debit_strategy(spread_type):
        if _cm.get("net_credit") is not None:
            _upsert_warning(normalized, "SCHEMA_MISMATCH_NET_CREDIT_FOR_DEBIT")
            if _cm.get("net_debit") is None:
                _cm["net_debit"] = _cm["net_credit"]
            _cm["net_credit"] = None
            normalized["computed_metrics"] = _cm
        if _cm.get("net_debit") is None and normalized.get("net_debit") is not None:
            _cm["net_debit"] = normalized["net_debit"]
            normalized["computed_metrics"] = _cm
            _upsert_warning(normalized, "SCHEMA_MISMATCH_NET_DEBIT_MISSING_IN_METRICS")
        # Also ensure root is clean
        normalized.pop("net_credit", None)

    # ── 9c. Engine gate status (for UI alignment with model analysis) ─
    # Accepted trades carry selection_reasons=[] from evaluate().
    # Expose a structured engine_gate_status so the frontend can
    # display whether the engine accepted or rejected the trade,
    # independently of the LLM model recommendation.
    _sel_reasons = normalized.get("selection_reasons")
    if isinstance(_sel_reasons, list):
        normalized["engine_gate_status"] = {
            "passed": len(_sel_reasons) == 0,
            "failed_reasons": list(_sel_reasons),
        }

    # ── 10. (removed) Legacy root-level back-fill aliases ────────────
    # Formerly wrote p_win_used, ev_per_contract, bid_ask_spread_pct,
    # etc. back to root from computed/details.  Consumers should now
    # read from computed.* / details.* instead.  Use
    # strip_legacy_fields() at the API boundary to ensure these fields
    # never reach the frontend.

    # ── 11. Validation warnings ──────────────────────────────────────
    if computed["pop"] is None:
        _upsert_warning(normalized, "POP_NOT_IMPLEMENTED_FOR_STRATEGY")
    if pills["regime_label"] is None:
        _upsert_warning(normalized, "REGIME_UNAVAILABLE")
    if computed["max_profit"] is None:
        _upsert_warning(normalized, "MAX_PROFIT_UNAVAILABLE")
    if computed["max_loss"] is None:
        _upsert_warning(normalized, "MAX_LOSS_UNAVAILABLE")
    if computed["expected_value"] is None:
        _upsert_warning(normalized, "EXPECTED_VALUE_UNAVAILABLE")
    if computed["return_on_risk"] is None:
        _upsert_warning(normalized, "RETURN_ON_RISK_UNAVAILABLE")
    if details["break_even"] is None:
        _upsert_warning(normalized, "BREAKEVEN_UNAVAILABLE")
    if normalized.get("pricing", {}).get("spread_mid") is None:
        _upsert_warning(normalized, "SPREAD_MID_UNAVAILABLE")
    if normalized.get("pricing", {}).get("spread_natural") is None:
        _upsert_warning(normalized, "SPREAD_NATURAL_UNAVAILABLE")

    return normalized
