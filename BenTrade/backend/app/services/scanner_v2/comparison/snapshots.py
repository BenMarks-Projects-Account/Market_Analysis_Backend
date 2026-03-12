"""Comparison harness — snapshot loading and building utilities.

Snapshots are frozen market-data fixtures that both legacy and V2
scanners receive identically.  This ensures comparison results are
not polluted by data-timing differences.

Snapshot storage
────────────────
Snapshots can be:
1. Built in-memory from dicts (``build_snapshot``).
2. Loaded from JSON files (``load_snapshot``).
3. Saved to JSON for regression archives (``save_snapshot``).

JSON files live under ``tests/fixtures/scanner_snapshots/`` by convention,
but any path is accepted.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.scanner_v2.comparison.contracts import ComparisonSnapshot

_log = logging.getLogger("bentrade.scanner_v2.comparison.snapshots")


# ── Build from raw data ─────────────────────────────────────────────

def build_snapshot(
    *,
    snapshot_id: str,
    symbol: str,
    underlying_price: float,
    chain: dict[str, Any],
    expirations: list[str] | None = None,
    description: str = "",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ComparisonSnapshot:
    """Create a ``ComparisonSnapshot`` from raw input data.

    This is the primary way to build snapshots in tests and scripts.
    If ``expirations`` is not provided, they are extracted from the
    chain data.
    """
    if expirations is None:
        expirations = _extract_expirations(chain)

    return ComparisonSnapshot(
        snapshot_id=snapshot_id,
        symbol=symbol.upper(),
        underlying_price=underlying_price,
        chain=chain,
        expirations=expirations,
        captured_at=datetime.now(timezone.utc).isoformat(),
        description=description,
        tags=tags or [],
        metadata=metadata or {},
    )


def _extract_expirations(chain: dict[str, Any]) -> list[str]:
    """Pull unique expiration dates from a Tradier-shaped chain."""
    options = chain.get("options", {})
    if isinstance(options, dict):
        option_list = options.get("option", [])
    elif isinstance(options, list):
        option_list = options
    else:
        return []

    expirations = sorted({
        opt.get("expiration_date", opt.get("expiration", ""))
        for opt in option_list
        if opt.get("expiration_date") or opt.get("expiration")
    })
    return [e for e in expirations if e]


# ── Load / save JSON ────────────────────────────────────────────────

def load_snapshot(path: str | Path) -> ComparisonSnapshot:
    """Load a snapshot from a JSON file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Snapshot file not found: {p}")

    data = json.loads(p.read_text(encoding="utf-8"))
    return ComparisonSnapshot.from_dict(data)


def save_snapshot(snapshot: ComparisonSnapshot, path: str | Path) -> Path:
    """Save a snapshot to a JSON file.  Creates parent dirs if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(snapshot.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )
    _log.info("Snapshot saved: %s", p)
    return p


# ── Synthetic chain builders (for test fixtures) ────────────────────

def build_synthetic_chain(
    *,
    symbol: str = "SPY",
    underlying_price: float = 595.50,
    expiration: str = "2026-03-20",
    put_strikes: list[dict[str, Any]] | None = None,
    call_strikes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a minimal Tradier-shaped option chain for testing.

    Each entry in ``put_strikes`` / ``call_strikes`` should be a dict::

        {
            "strike": 590.0,
            "bid": 1.50,
            "ask": 1.65,
            "delta": -0.30,   # optional
            "iv": 0.22,       # optional
            "oi": 5000,       # optional
            "volume": 800,    # optional
        }

    Missing fields default to ``None`` in the generated contracts.
    """
    option_list: list[dict[str, Any]] = []

    for entries, opt_type in [(put_strikes or [], "put"), (call_strikes or [], "call")]:
        for entry in entries:
            option_list.append(_build_option_contract(
                symbol=symbol,
                underlying=underlying_price,
                expiration=expiration,
                option_type=opt_type,
                **entry,
            ))

    return {
        "options": {
            "option": option_list,
        },
    }


def _build_option_contract(
    *,
    symbol: str,
    underlying: float,
    expiration: str,
    option_type: str,
    strike: float,
    bid: float | None = None,
    ask: float | None = None,
    delta: float | None = None,
    gamma: float | None = None,
    theta: float | None = None,
    vega: float | None = None,
    iv: float | None = None,
    oi: int | None = None,
    volume: int | None = None,
    last: float | None = None,
) -> dict[str, Any]:
    """Build a single Tradier-compatible option contract dict."""
    # Tradier convention: option symbols like SPY260320P00590000
    exp_short = expiration.replace("-", "")[2:]   # "260320"
    opt_char = "P" if option_type == "put" else "C"
    strike_int = int(strike * 1000)
    occ_symbol = f"{symbol}{exp_short}{opt_char}{strike_int:08d}"

    greeks: dict[str, Any] | None = None
    if any(v is not None for v in [delta, gamma, theta, vega, iv]):
        greeks = {}
        if delta is not None:
            greeks["delta"] = delta
        if gamma is not None:
            greeks["gamma"] = gamma
        if theta is not None:
            greeks["theta"] = theta
        if vega is not None:
            greeks["vega"] = vega
        if iv is not None:
            greeks["mid_iv"] = iv

    contract: dict[str, Any] = {
        "symbol": occ_symbol,
        "root_symbol": symbol,
        "underlying": symbol,
        "strike": strike,
        "option_type": option_type,
        "expiration_date": expiration,
        "bid": bid,
        "ask": ask,
        "last": last,
        "open_interest": oi,
        "volume": volume,
    }
    if greeks:
        contract["greeks"] = greeks

    return contract
