"""Snapshot manifest schema — single source of truth for offline datasets.

A manifest describes everything captured during a snapshot run and is the
authoritative record the offline replay loader consults.

Storage layout:
    snapshots/{provider}/{YYYYMMDD}/{strategy_id}/{trace_id}/
        snapshot_manifest.json
        {SYMBOL}/
            underlying_quote.json
            prices_history.json
            option_chain_{expiration}.json   (one per expiration)
        market_context.json                  (VIX, risk-free rate, regime)
        scan_config.json                     (resolved thresholds, preset)
        filter_trace.json                    (optional, if scan ran)
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SymbolArtifacts(BaseModel):
    """Paths to captured artifacts for a single symbol."""

    symbol: str
    underlying_quote: str | None = None
    prices_history: str | None = None
    option_chains: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of expiration date → relative path to chain file",
    )


class CompletenessInfo(BaseModel):
    """Tracks whether all required artifacts are present."""

    required_artifacts_present: bool = False
    missing_artifacts: list[str] = Field(default_factory=list)


class ScanConfig(BaseModel):
    """Resolved scan configuration stored alongside the snapshot."""

    strategy_id: str
    preset_name: str = "balanced"
    data_quality_mode: str = "standard"
    symbols: list[str] = Field(default_factory=list)
    dte_min: int = 3
    dte_max: int = 60
    max_expirations_per_symbol: int = 6
    resolved_thresholds: dict[str, Any] = Field(default_factory=dict)
    request_payload: dict[str, Any] = Field(default_factory=dict)


class MarketContext(BaseModel):
    """Global / derived market data that scanners might reference."""

    vix: float | None = None
    risk_free_rate: float | None = None
    regime_label: str | None = None
    regime_score: float | None = None
    provider: str = "mixed"
    captured_at: str | None = None


class SnapshotManifest(BaseModel):
    """Root manifest for a snapshot capture run.

    Written to ``snapshot_manifest.json`` at the root of each capture
    directory.  The offline replay loader reads this to locate all data.
    """

    trace_id: str
    created_at: str
    provider: str = "tradier"
    strategy_id: str
    preset_name: str = "balanced"
    data_quality_mode: str = "standard"
    symbols: list[str] = Field(default_factory=list)
    symbol_artifacts: dict[str, SymbolArtifacts] = Field(
        default_factory=dict,
        description="Keyed by uppercase symbol",
    )
    market_context_path: str | None = None
    scan_config_path: str | None = None
    filter_trace_path: str | None = None
    completeness: CompletenessInfo = Field(default_factory=CompletenessInfo)

    # Metadata
    capture_duration_seconds: float | None = None
    expirations_captured: int = 0
    chains_captured: int = 0
    history_bars_captured: int = 0

    def validate_completeness(self) -> CompletenessInfo:
        """Check that all required artifacts are present and listed."""
        missing: list[str] = []

        for sym in self.symbols:
            arts = self.symbol_artifacts.get(sym)
            if arts is None:
                missing.append(f"{sym}: no artifacts recorded")
                continue
            if arts.underlying_quote is None:
                missing.append(f"{sym}: underlying_quote missing")
            if arts.prices_history is None:
                missing.append(f"{sym}: prices_history missing")
            if not arts.option_chains:
                missing.append(f"{sym}: no option chains captured")

        if self.market_context_path is None:
            missing.append("market_context.json missing")

        self.completeness = CompletenessInfo(
            required_artifacts_present=len(missing) == 0,
            missing_artifacts=missing,
        )
        return self.completeness
