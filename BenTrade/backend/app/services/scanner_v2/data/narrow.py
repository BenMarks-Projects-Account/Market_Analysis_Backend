"""Narrowing orchestrator — runs the full pipeline and produces a V2NarrowedUniverse.

Pipeline stages
───────────────
1. **Normalize** — raw Tradier chain → V2OptionContract list.
2. **Expiry narrow** — filter contracts by DTE window.
3. **Strike narrow** — filter by distance / moneyness / option-type
   and group into V2ExpiryBucket structures.
4. **Package** — assemble V2NarrowedUniverse with diagnostics.

For multi-expiry strategies (calendars/diagonals), the orchestrator
uses ``narrow_expirations_multi()`` to produce separate near/far
contract lists, then runs strike narrowing on each independently.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from app.services.scanner_v2.data.chain import normalize_chain
from app.services.scanner_v2.data.contracts import (
    V2NarrowedUniverse,
    V2NarrowingDiagnostics,
    V2NarrowingRequest,
    V2UnderlyingSnapshot,
)
from app.services.scanner_v2.data.expiry import (
    narrow_expirations,
    narrow_expirations_multi,
)
from app.services.scanner_v2.data.strikes import narrow_strikes


def narrow_chain(
    chain: dict | list,
    symbol: str,
    underlying_price: float,
    *,
    request: V2NarrowingRequest | None = None,
    # Convenience kwargs override request fields
    dte_min: int | None = None,
    dte_max: int | None = None,
    option_types: list[str] | None = None,
    distance_min_pct: float | None = None,
    distance_max_pct: float | None = None,
    moneyness: str | None = None,
    multi_expiry: bool | None = None,
    near_dte_min: int | None = None,
    near_dte_max: int | None = None,
    far_dte_min: int | None = None,
    far_dte_max: int | None = None,
    today: object = None,
) -> V2NarrowedUniverse:
    """Run the full narrowing pipeline.

    Parameters
    ----------
    chain
        Raw Tradier chain (dict or list).
    symbol
        Underlying symbol (e.g. ``"SPY"``).
    underlying_price
        Spot price for distance / moneyness calculations.
    request
        Full narrowing request.  If None, one is built from kwargs.
    dte_min, dte_max, option_types, ...
        Convenience overrides — applied on top of ``request``.
    today
        Override for today's date (testing convenience).

    Returns
    -------
    V2NarrowedUniverse
        Fully narrowed and structured data ready for family builders.
    """
    # ── Build / merge request ───────────────────────────────────
    req = _build_request(
        request,
        dte_min=dte_min,
        dte_max=dte_max,
        option_types=option_types,
        distance_min_pct=distance_min_pct,
        distance_max_pct=distance_max_pct,
        moneyness=moneyness,
        multi_expiry=multi_expiry,
        near_dte_min=near_dte_min,
        near_dte_max=near_dte_max,
        far_dte_min=far_dte_min,
        far_dte_max=far_dte_max,
    )

    # ── Diagnostics container ───────────────────────────────────
    diag = V2NarrowingDiagnostics()

    # ── DIAGNOSTIC: narrow_chain entry trace (TEMPORARY — remove after debugging) ──
    _narrow_log = logging.getLogger("bentrade.narrow_chain")
    _narrow_diag: dict[str, object] = {
        "timestamp": datetime.now().isoformat(),
        "symbol": symbol,
        "underlying_price": underlying_price,
        "dte_min": req.dte_min,
        "dte_max": req.dte_max,
        "option_types": req.option_types,
        "multi_expiry": req.multi_expiry,
    }
    # Count raw contracts in input chain
    if isinstance(chain, dict):
        _raw_opts = (chain.get("options") or {})
        if isinstance(_raw_opts, dict):
            _inner = _raw_opts.get("option", [])
            _narrow_diag["input_contract_count"] = len(_inner) if isinstance(_inner, list) else 1
        elif isinstance(_raw_opts, list):
            _narrow_diag["input_contract_count"] = len(_raw_opts)
        else:
            _narrow_diag["input_contract_count"] = f"unexpected: {type(_raw_opts).__name__}"
    elif isinstance(chain, list):
        _narrow_diag["input_contract_count"] = len(chain)
    else:
        _narrow_diag["input_contract_type"] = type(chain).__name__
    # ── END DIAGNOSTIC HEADER ──

    # ── Build underlying snapshot ───────────────────────────────
    underlying = V2UnderlyingSnapshot(
        symbol=symbol,
        price=underlying_price,
        price_source="provided",
    )

    # ── Stage 1: Normalize chain ────────────────────────────────
    contracts = normalize_chain(chain, diag=diag)

    # ── Chain completeness check ────────────────────────────────
    _MIN_CONTRACTS_WARNING = {
        "SPY": 200, "QQQ": 200, "IWM": 150, "DIA": 100,
    }
    _DEFAULT_MIN_CONTRACTS = 50
    _sym_upper = symbol.upper() if symbol else ""
    _min_expected = _MIN_CONTRACTS_WARNING.get(_sym_upper, _DEFAULT_MIN_CONTRACTS)
    _contract_count = len(contracts) if contracts else 0
    if _contract_count < _min_expected:
        _narrow_log.warning(
            "event=chain_possibly_incomplete symbol=%s contracts=%d expected_min=%d",
            symbol, _contract_count, _min_expected,
        )
        diag.chain_completeness_warning = True
        diag.chain_contract_count = _contract_count
        diag.chain_expected_min = _min_expected
        diag.warnings.append(
            f"Chain possibly incomplete: {_contract_count} contracts "
            f"(expected ≥{_min_expected} for {_sym_upper})"
        )

    # ── DIAGNOSTIC: after normalize ──
    _narrow_diag["after_normalize_count"] = len(contracts) if contracts else 0
    if contracts:
        # Sample first 5 expirations seen
        _exp_set = sorted({c.expiration for c in contracts})
        _narrow_diag["unique_expirations_after_normalize"] = len(_exp_set)
        _narrow_diag["sample_expirations"] = _exp_set[:10]
    # ── END DIAGNOSTIC ──

    if not contracts:
        return V2NarrowedUniverse(
            underlying=underlying,
            diagnostics=diag,
            request=req,
        )

    # ── Stage 2: Expiry narrowing ───────────────────────────────
    from datetime import date as _date
    _today = today if isinstance(today, _date) else None

    if req.multi_expiry:
        near_contracts, far_contracts = narrow_expirations_multi(
            contracts, req, diag=diag, today=_today,
        )
        # Run strike narrowing independently on near + far
        near_buckets = narrow_strikes(
            near_contracts, req, underlying_price, diag=None,
        )
        far_buckets = narrow_strikes(
            far_contracts, req, underlying_price, diag=None,
        )
        # Merge buckets (keys may overlap if DTE windows overlap —
        # far_buckets values take precedence for shared keys since
        # far-leg pricing matters more for multi-expiry strategies).
        all_buckets = {**near_buckets, **far_buckets}

        # Update diagnostics with merged counts
        diag.contracts_after_strike_filter = sum(
            b.strike_count for b in all_buckets.values()
        )
        diag.contracts_final = diag.contracts_after_strike_filter
    else:
        expiry_contracts = narrow_expirations(
            contracts, req, diag=diag, today=_today,
        )

        # ── Stage 3: Strike narrowing ──────────────────────────
        all_buckets = narrow_strikes(
            expiry_contracts, req, underlying_price, diag=diag,
        )

    # ── Stage 4: Package ────────────────────────────────────────
    # ── DIAGNOSTIC: finalize narrow_chain trace ──
    _narrow_diag["after_expiry_filter_count"] = diag.contracts_after_expiry_filter
    _narrow_diag["expirations_kept"] = diag.expirations_kept
    _narrow_diag["expirations_dropped"] = diag.expirations_dropped
    _narrow_diag["expirations_kept_list"] = diag.expirations_kept_list[:10] if diag.expirations_kept_list else []
    _narrow_diag["expiry_drop_reasons"] = dict(diag.expiry_drop_reasons) if hasattr(diag, "expiry_drop_reasons") and diag.expiry_drop_reasons else {}
    _narrow_diag["after_strike_filter_count"] = diag.contracts_after_strike_filter
    _narrow_diag["contracts_final"] = diag.contracts_final
    _narrow_diag["strike_drop_reasons"] = dict(diag.strike_drop_reasons) if hasattr(diag, "strike_drop_reasons") and diag.strike_drop_reasons else {}
    _narrow_diag["data_quality"] = {
        "missing_bid": diag.contracts_missing_bid,
        "missing_ask": diag.contracts_missing_ask,
        "inverted_quote": diag.contracts_inverted_quote,
        "missing_delta": diag.contracts_missing_delta,
        "missing_iv": diag.contracts_missing_iv,
    }
    _narrow_diag["warnings"] = diag.warnings[:10] if diag.warnings else []
    _narrow_diag["final_bucket_expirations"] = sorted(all_buckets.keys()) if all_buckets else []
    _narrow_diag["final_bucket_strike_counts"] = {
        k: len(b.strikes) if hasattr(b, "strikes") else 0
        for k, b in (all_buckets or {}).items()
    }
    # Only write diag file in production (skip when underlying_price looks synthetic)
    import os as _os
    if not _os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            _diag_dir = Path("results/diagnostics")
            _diag_dir.mkdir(parents=True, exist_ok=True)
            _diag_file = _diag_dir / f"narrow_diag_{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
            with open(_diag_file, "w") as _f:
                json.dump(_narrow_diag, _f, indent=2, default=str)
        except Exception:
            pass
    _narrow_log.info(
        "NARROW_DIAG %s: input=%s normalize=%d expiry_kept=%d strikes_final=%d buckets=%d",
        symbol,
        _narrow_diag.get("input_contract_count", "?"),
        _narrow_diag.get("after_normalize_count", 0),
        _narrow_diag.get("after_expiry_filter_count", 0),
        _narrow_diag.get("contracts_final", 0),
        len(all_buckets) if all_buckets else 0,
    )
    # ── END DIAGNOSTIC ──
    return V2NarrowedUniverse(
        underlying=underlying,
        expiry_buckets=all_buckets,
        diagnostics=diag,
        request=req,
    )


def _build_request(
    base: V2NarrowingRequest | None,
    **overrides: object,
) -> V2NarrowingRequest:
    """Create a V2NarrowingRequest, applying any non-None overrides."""
    if base is None:
        base = V2NarrowingRequest()

    for key, val in overrides.items():
        if val is not None and hasattr(base, key):
            object.__setattr__(base, key, val)

    return base
