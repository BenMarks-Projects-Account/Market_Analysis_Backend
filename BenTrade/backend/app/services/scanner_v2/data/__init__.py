"""Options Scanner V2 — shared data-narrowing framework.

Provides the shared upstream data layer that all V2 family builders
consume.  Centralizes chain loading, expiry narrowing, strike-window
narrowing, and underlying normalization so family implementations
operate on clean, narrowed data rather than raw chain chaos.

Modules
-------
contracts       Data shapes for narrowed data (V2OptionContract,
                V2ExpiryBucket, V2NarrowedUniverse, etc.)
chain           Chain normalization: raw Tradier dict → typed contracts.
expiry          Expiry narrowing: DTE windows, nearest-N, multi-expiry.
strikes         Strike-window narrowing: distance, delta, moneyness.
narrow          Orchestrator: runs the full narrowing pipeline and
                produces a V2NarrowedUniverse ready for family builders.

Usage
-----
    from app.services.scanner_v2.data import narrow_chain

    universe = narrow_chain(
        chain=raw_chain,
        symbol="SPY",
        underlying_price=595.50,
        dte_min=7,
        dte_max=45,
        option_types=["put"],
    )
    # universe.expiry_buckets  → dict[str, V2ExpiryBucket]
    # universe.diagnostics     → V2NarrowingDiagnostics
"""

from app.services.scanner_v2.data.contracts import (  # noqa: F401
    V2NarrowingRequest,
    V2OptionContract,
    V2ExpiryBucket,
    V2StrikeEntry,
    V2UnderlyingSnapshot,
    V2NarrowedUniverse,
    V2NarrowingDiagnostics,
)
from app.services.scanner_v2.data.narrow import (  # noqa: F401
    narrow_chain,
)
