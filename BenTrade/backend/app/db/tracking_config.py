"""Strategy-specific tracking window constants.

Used at decision-capture time to compute ``decisions.tracking_window_days``
and ``decisions.tracking_ends_utc``. Options strategies derive their window
from the candidate's expiration (computed inline as business days); stock
strategies use a fixed constant from this module.

These values are frozen for Phase 1. Revisit once real outcome data is
available.
"""

from __future__ import annotations

# Trading-days lookout window per stock strategy.
STOCK_TRACKING_WINDOWS: dict[str, int] = {
    "pullback_swing": 10,
    "momentum_breakout": 20,
    "mean_reversion": 5,
    "volatility_expansion": 10,
}

__all__ = ["STOCK_TRACKING_WINDOWS"]
