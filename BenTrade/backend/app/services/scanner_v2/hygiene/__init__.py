"""V2 Scanner — candidate trust hygiene layer.

Shared modules for quote sanity, liquidity sanity, and duplicate
suppression.  These sit after structural/quote-presence validation
(Phase D) and before normalization (Phase F), ensuring that only
trustworthy, unique candidates reach downstream consumers.

Modules
-------
quote_sanity      Detect broken quote conditions beyond mere presence.
liquidity_sanity  Detect unusable liquidity and warn on marginal quality.
dedup             Detect and suppress duplicate / near-duplicate candidates.
"""

from app.services.scanner_v2.hygiene.quote_sanity import run_quote_sanity
from app.services.scanner_v2.hygiene.liquidity_sanity import run_liquidity_sanity
from app.services.scanner_v2.hygiene.dedup import (
    run_dedup,
    candidate_dedup_key,
    DedupResult,
)

__all__ = [
    "run_quote_sanity",
    "run_liquidity_sanity",
    "run_dedup",
    "candidate_dedup_key",
    "DedupResult",
]
