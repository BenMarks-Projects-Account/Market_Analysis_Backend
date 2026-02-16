from __future__ import annotations

from typing import Any

from app.models.trade_contract import TradeContract


def analyze_trade(
    trade: TradeContract,
    source: str,
    model_url: str = "http://localhost:1234/v1/chat/completions",
    retries: int = 2,
    timeout: int = 30,
) -> dict[str, Any] | None:
    # Keep the legacy JSON contract exactly the same by delegating to the legacy implementation.
    # TODO(architecture): migrate implementation from common.utils into this module and delete legacy shim.
    from common import utils as legacy_utils

    return legacy_utils._analyze_trade_with_model_legacy(
        trade.to_dict(),
        source,
        model_url=model_url,
        retries=retries,
        timeout=timeout,
    )
