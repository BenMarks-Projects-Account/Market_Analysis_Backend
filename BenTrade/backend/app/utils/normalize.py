"""Unified trade normalizer – single source of truth for trade output shape.

Every consumer (scanner reports, homepage recommendations, workbench trade
lookup, admin data workbench) calls ``normalize_trade()`` to guarantee:

- canonical symbol / strategy / trade-key identities
- per-contract monetary values as primary (per-share × multiplier)
- ``computed``, ``details``, ``pills`` sub-dicts for UI consumption
- ``computed_metrics`` + ``metrics_status`` via shared contract
- validation warnings for missing key metrics

Use ``strip_legacy_fields()`` at the API boundary to remove legacy flat
fields from trade dicts before returning to the frontend.
"""

from __future__ import annotations

from typing import Any

from app.utils.computed_metrics import apply_metrics_contract
from app.utils.strategy_id_resolver import resolve_strategy_id_or_none
from app.utils.trade_key import canonicalize_strategy_id, canonicalize_trade_key, trade_key


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
    for _subkey in ("computed", "computed_metrics", "details"):
        _sub = normalized.get(_subkey)
        if isinstance(_sub, dict):
            for _k, _v in _sub.items():
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

    return normalized
