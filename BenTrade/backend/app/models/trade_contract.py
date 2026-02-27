from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

_log = logging.getLogger("bentrade.trade_contract")

# Module-level regex for parsing legacy string-encoded condor strikes
# e.g. "P649.0|C702.0"  →  put=649.0, call=702.0
_CONDOR_STRIKE_RE = re.compile(r"P([\d.]+)\|C([\d.]+)", re.IGNORECASE)


class TradeContract(BaseModel):
    """Canonical trade structure (single source of truth).

    For 2-leg spreads  → ``short_strike`` / ``long_strike`` are numeric floats.
    For iron condors   → use the four explicit leg-strike fields:
        ``short_put_strike``, ``long_put_strike``,
        ``short_call_strike``, ``long_call_strike``.
    A ``legs`` list is the authoritative multi-leg representation.
    """

    model_config = ConfigDict(extra="allow")

    spread_type: str | None = None
    underlying: str | None = None

    # -- 2-leg strike fields (credit/debit spreads) --------------------------
    short_strike: float | None = None
    long_strike: float | None = None

    # -- 4-leg strike fields (iron condor) -----------------------------------
    short_put_strike: float | None = None
    long_put_strike: float | None = None
    short_call_strike: float | None = None
    long_call_strike: float | None = None

    # -- Multi-leg representation (authoritative for ≥3-leg strategies) ------
    legs: list[dict[str, Any]] | None = None

    dte: int | None = None
    net_credit: float | None = None
    width: float | None = None
    max_profit_per_share: float | None = None
    max_loss_per_share: float | None = None
    break_even: float | None = None
    return_on_risk: float | None = None
    pop_delta_approx: float | None = None
    p_win_used: float | None = None
    ev_per_share: float | None = None
    ev_to_risk: float | None = None
    kelly_fraction: float | None = None
    trade_quality_score: float | None = None
    iv: float | None = None
    realized_vol: float | None = None
    iv_rv_ratio: float | None = None
    expected_move: float | None = None
    short_strike_z: float | None = None
    bid_ask_spread_pct: float | None = None
    composite_score: float | None = None
    rank_score: float | None = None
    rank_in_report: int | None = None
    model_evaluation: dict[str, Any] | None = None

    expiration: str | None = None
    underlying_symbol: str | None = None

    # -- Iron-condor string-strike normalizer --------------------------------
    # If spread_type == iron_condor and short_strike/long_strike arrive as
    # encoded strings like "P649.0|C702.0", parse them into the 4 numeric
    # leg-strike fields and clear the string values.  This prevents Pydantic
    # float-validation failures on older payloads.  (Req §3)

    @model_validator(mode="before")
    @classmethod
    def _normalise_condor_strikes(cls, data: Any) -> Any:  # noqa: N805
        if not isinstance(data, dict):
            return data

        spread = str(
            data.get("spread_type")
            or data.get("strategy_id")
            or data.get("type")
            or ""
        ).lower()
        if "iron_condor" not in spread and "condor" not in spread:
            return data

        warned = False
        for key, put_field, call_field in (
            ("short_strike", "short_put_strike", "short_call_strike"),
            ("long_strike", "long_put_strike", "long_call_strike"),
        ):
            raw = data.get(key)
            if not isinstance(raw, str):
                continue
            m = _CONDOR_STRIKE_RE.search(raw)
            if m:
                put_val = float(m.group(1))
                call_val = float(m.group(2))
                # Only fill if the explicit fields are not already set
                if data.get(put_field) is None:
                    data[put_field] = put_val
                if data.get(call_field) is None:
                    data[call_field] = call_val
                # Clear the string so Pydantic doesn't try to coerce to float
                data[key] = None
                if not warned:
                    _log.warning(
                        "condor_strike_string_ignored: "
                        "parsed '%s' = P%.1f / C%.1f from legacy string encoding",
                        key, put_val, call_val,
                    )
                    warned = True
            else:
                # Unparseable string for a condor — clear to None
                _log.warning(
                    "condor_strike_string_ignored: "
                    "could not parse '%s' value '%s' — setting to None",
                    key, raw,
                )
                data[key] = None

        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TradeContract":
        return cls.model_validate(d or {})

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python", by_alias=False, exclude_none=False)
