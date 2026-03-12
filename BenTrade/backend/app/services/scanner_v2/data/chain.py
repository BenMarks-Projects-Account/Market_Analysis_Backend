"""Chain normalization — raw Tradier dict → V2OptionContract list.

This module converts the Tradier chain format into normalized
V2OptionContract instances.  It handles the Tradier nesting
(``{"options": {"option": [...]}}``, flat list, or single dict),
normalizes field names / types, computes ``mid``, and flags
data-quality issues.

Input format (Tradier)
──────────────────────
Each contract dict may contain:
    symbol, root_symbol, option_type, expiration_date, strike,
    bid, ask, last, volume, open_interest, greeks.delta,
    greeks.gamma, greeks.theta, greeks.vega, greeks.mid_iv

Greeks may live in a nested ``greeks`` dict or be top-level keys.
"""

from __future__ import annotations

from app.services.scanner_v2.data.contracts import (
    V2NarrowingDiagnostics,
    V2OptionContract,
)


def extract_options_list(chain: dict | list) -> list[dict]:
    """Extract flat list of contract dicts from Tradier chain.

    Handles:
    - ``{"options": {"option": [...]}}``   (standard Tradier)
    - ``{"options": {"option": {...}}}``   (single contract)
    - ``{"options": [...]}``               (pre-flattened)
    - ``[...]``                            (already flat)
    """
    if isinstance(chain, list):
        return chain
    if not isinstance(chain, dict):
        return []
    options_wrapper = chain.get("options")
    if isinstance(options_wrapper, dict):
        inner = options_wrapper.get("option")
        if isinstance(inner, list):
            return inner
        if isinstance(inner, dict):
            return [inner]
    if isinstance(options_wrapper, list):
        return options_wrapper
    return []


def _safe_float(val: object) -> float | None:
    """Convert value to float or None.  Never use 0 as sentinel."""
    if val is None:
        return None
    try:
        f = float(val)
        return f
    except (ValueError, TypeError):
        return None


def _safe_int(val: object) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _resolve_greek(raw: dict, key: str) -> float | None:
    """Get a greek from nested ``greeks`` dict or top-level key."""
    greeks_dict = raw.get("greeks")
    if isinstance(greeks_dict, dict):
        val = _safe_float(greeks_dict.get(key))
        if val is not None:
            return val
    return _safe_float(raw.get(key))


def normalize_contract(raw: dict) -> V2OptionContract | None:
    """Convert one raw Tradier contract dict → V2OptionContract.

    Returns None if the contract cannot be parsed (missing
    required fields: symbol, strike, option_type, expiration).
    """
    symbol = raw.get("symbol") or ""
    root = raw.get("root_symbol") or raw.get("underlying") or ""
    option_type = (raw.get("option_type") or "").lower()
    expiration = raw.get("expiration_date") or raw.get("expiration") or ""
    strike = _safe_float(raw.get("strike"))

    if not symbol or strike is None or not option_type or not expiration:
        return None

    bid = _safe_float(raw.get("bid"))
    ask = _safe_float(raw.get("ask"))

    # Derive mid
    mid: float | None = None
    if bid is not None and ask is not None:
        mid = round((bid + ask) / 2, 4)

    # Quote validity: missing or inverted
    quote_valid = True
    if bid is None or ask is None:
        quote_valid = False
    elif bid < 0 or ask < 0:
        quote_valid = False
    elif bid > ask:
        quote_valid = False

    return V2OptionContract(
        symbol=symbol,
        root_symbol=root,
        strike=strike,
        option_type=option_type,
        expiration=expiration,
        bid=bid,
        ask=ask,
        mid=mid,
        delta=_resolve_greek(raw, "delta"),
        gamma=_resolve_greek(raw, "gamma"),
        theta=_resolve_greek(raw, "theta"),
        vega=_resolve_greek(raw, "vega"),
        iv=_resolve_greek(raw, "mid_iv") or _resolve_greek(raw, "iv"),
        open_interest=_safe_int(raw.get("open_interest")),
        volume=_safe_int(raw.get("volume")),
        quote_valid=quote_valid,
    )


def normalize_chain(
    chain: dict | list,
    diag: V2NarrowingDiagnostics | None = None,
) -> list[V2OptionContract]:
    """Normalize raw Tradier chain → list of V2OptionContract.

    Populates data-quality counters on ``diag`` if provided.

    Unparseable contracts are silently dropped but counted.
    """
    raw_list = extract_options_list(chain)
    contracts: list[V2OptionContract] = []
    unparseable = 0

    for raw in raw_list:
        c = normalize_contract(raw)
        if c is None:
            unparseable += 1
            continue
        contracts.append(c)

    if diag is not None:
        diag.total_contracts_loaded = len(raw_list)
        _tally_quality(contracts, diag)
        if unparseable:
            diag.warnings.append(
                f"{unparseable} contract(s) could not be parsed from chain",
            )

    return contracts


def _tally_quality(
    contracts: list[V2OptionContract],
    diag: V2NarrowingDiagnostics,
) -> None:
    """Count data-quality flags across normalized contracts."""
    for c in contracts:
        if c.bid is None:
            diag.contracts_missing_bid += 1
        if c.ask is None:
            diag.contracts_missing_ask += 1
        if c.bid is not None and c.ask is not None and c.bid > c.ask:
            diag.contracts_inverted_quote += 1
        if c.delta is None:
            diag.contracts_missing_delta += 1
        if c.iv is None:
            diag.contracts_missing_iv += 1
        if c.open_interest is None:
            diag.contracts_missing_oi += 1
        if c.volume is None:
            diag.contracts_missing_volume += 1
