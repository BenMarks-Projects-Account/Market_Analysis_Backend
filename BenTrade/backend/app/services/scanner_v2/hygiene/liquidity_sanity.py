"""V2 Scanner — shared liquidity sanity checks.

Goes beyond Phase D's liquidity-presence checks to detect candidates
with technically present but effectively unusable liquidity.

Philosophy
----------
- REJECT clearly dead liquidity: both OI=0 AND volume=0 on any leg.
  This means no market exists — the candidate is untradeable.
- WARN on marginal liquidity that might still be usable:
  - Very low OI (present but thin).
  - Very low volume (present but thin).
  - Wide composite bid-ask spread % across the spread.
- Do NOT hard-reject merely low (but non-zero) OI or volume.
  Leave that to downstream ranking/selection.

Checks performed
----------------
Per-leg:
  - Dead leg: OI=0 AND volume=0 → reject.
  - Low OI: OI < threshold → warn.
  - Low volume: volume < threshold → warn.

Candidate-level:
  - Min-leg OI across all legs → warn if below threshold.
  - Composite spread bid-ask width % → warn if excessively wide.

These run on candidates that PASSED Phase D (liquidity presence
already confirmed, so OI/volume are not None).
"""

from __future__ import annotations

from typing import Any

from app.services.scanner_v2.contracts import V2Candidate, V2CheckResult
from app.services.scanner_v2.diagnostics.builder import DiagnosticsBuilder
from app.services.scanner_v2.diagnostics.reason_codes import (
    REJECT_DEAD_LEG,
    WARN_LOW_OI,
    WARN_LOW_VOLUME,
    WARN_WIDE_COMPOSITE_SPREAD,
)

# ── Default thresholds (conservative — catch obvious junk only) ──

# OI below this on any leg → warning (not rejection).
_LOW_OI_WARN_THRESHOLD = 10

# Volume below this on any leg → warning (not rejection).
_LOW_VOLUME_WARN_THRESHOLD = 5

# Composite bid-ask spread % above this → warning.
# Computed as: sum(ask - bid) / sum(mid) across all legs.
_WIDE_COMPOSITE_SPREAD_WARN_PCT = 0.50  # 50%


def run_liquidity_sanity(
    candidates: list[V2Candidate],
    *,
    low_oi_warn: int = _LOW_OI_WARN_THRESHOLD,
    low_volume_warn: int = _LOW_VOLUME_WARN_THRESHOLD,
    wide_spread_warn_pct: float = _WIDE_COMPOSITE_SPREAD_WARN_PCT,
) -> list[V2Candidate]:
    """Run candidate-level liquidity sanity checks.

    Skips candidates already rejected by prior phases.

    Parameters
    ----------
    candidates
        List of V2Candidate objects (mutated in place).
    low_oi_warn
        Warn if any leg's OI is below this value.
    low_volume_warn
        Warn if any leg's volume is below this value.
    wide_spread_warn_pct
        Warn if composite bid-ask spread % exceeds this.

    Returns
    -------
    The same list, with diagnostics updated on each candidate.
    """
    for cand in candidates:
        if cand.diagnostics.reject_reasons:
            continue

        builder = DiagnosticsBuilder(source_phase="D2")
        checks: list[V2CheckResult] = []

        min_oi: int | None = None
        total_spread = 0.0
        total_mid = 0.0

        for leg in cand.legs:
            prefix = f"leg[{leg.index}] {leg.side} {leg.option_type} {leg.strike}"

            oi = leg.open_interest
            vol = leg.volume

            # ── Dead leg: OI=0 AND volume=0 ──────────────────────
            if oi is not None and vol is not None and oi == 0 and vol == 0:
                checks.append(V2CheckResult(
                    "leg_alive", False,
                    f"{prefix}: OI=0 volume=0",
                ))
                builder.add_reject(
                    REJECT_DEAD_LEG,
                    source_check="leg_alive",
                    message=f"{prefix}: OI=0 AND volume=0 — no market",
                    leg_index=leg.index,
                )
                continue

            checks.append(V2CheckResult("leg_alive", True, prefix))

            # ── Low OI warning ───────────────────────────────────
            if oi is not None:
                if min_oi is None or oi < min_oi:
                    min_oi = oi
                if oi < low_oi_warn:
                    checks.append(V2CheckResult(
                        "oi_reasonable", False,
                        f"{prefix}: OI={oi} < {low_oi_warn}",
                    ))
                    builder.add_warning(
                        WARN_LOW_OI,
                        source_check="oi_reasonable",
                        message=f"{prefix}: OI={oi} < {low_oi_warn}",
                        leg_index=leg.index,
                        oi=oi,
                        threshold=low_oi_warn,
                    )
                else:
                    checks.append(V2CheckResult(
                        "oi_reasonable", True, prefix,
                    ))

            # ── Low volume warning ───────────────────────────────
            if vol is not None:
                if vol < low_volume_warn:
                    checks.append(V2CheckResult(
                        "volume_reasonable", False,
                        f"{prefix}: volume={vol} < {low_volume_warn}",
                    ))
                    builder.add_warning(
                        WARN_LOW_VOLUME,
                        source_check="volume_reasonable",
                        message=f"{prefix}: volume={vol} < {low_volume_warn}",
                        leg_index=leg.index,
                        volume=vol,
                        threshold=low_volume_warn,
                    )
                else:
                    checks.append(V2CheckResult(
                        "volume_reasonable", True, prefix,
                    ))

            # Accumulate for composite spread calculation
            if leg.bid is not None and leg.ask is not None and leg.ask >= leg.bid:
                spread = leg.ask - leg.bid
                mid = (leg.bid + leg.ask) / 2.0
                total_spread += spread
                total_mid += mid

        # ── Composite bid-ask spread % ───────────────────────────
        if total_mid > 0:
            composite_pct = total_spread / total_mid
            if composite_pct > wide_spread_warn_pct:
                checks.append(V2CheckResult(
                    "composite_spread_reasonable", False,
                    f"composite bid-ask spread = {composite_pct:.2%} > "
                    f"{wide_spread_warn_pct:.0%}",
                ))
                builder.add_warning(
                    WARN_WIDE_COMPOSITE_SPREAD,
                    source_check="composite_spread_reasonable",
                    message=(
                        f"Composite bid-ask spread {composite_pct:.2%} "
                        f"> {wide_spread_warn_pct:.0%} threshold"
                    ),
                    composite_spread_pct=round(composite_pct, 4),
                    threshold=wide_spread_warn_pct,
                )
            else:
                checks.append(V2CheckResult(
                    "composite_spread_reasonable", True,
                    f"composite={composite_pct:.2%}",
                ))

        builder.set_check_results("liquidity_sanity", checks)
        builder.apply(cand.diagnostics)

    return candidates
