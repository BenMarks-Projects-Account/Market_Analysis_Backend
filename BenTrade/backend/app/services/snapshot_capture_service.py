"""Snapshot capture service — produces complete replayable offline datasets.

Fetches ALL data a scanner run needs (option chains, underlying quotes,
price history, VIX, regime context) from live providers, persists them
into a structured snapshot directory, and writes a ``snapshot_manifest.json``
describing every artifact.

Usage:
    service = SnapshotCaptureService(base_data_service, tradier_client, ...)
    manifest = await service.capture(
        strategy_id="credit_spread",
        symbols=["SPY", "QQQ"],
        preset_name="balanced",
        dte_min=3, dte_max=45,
    )

Storage layout:
    {snapshot_dir}/{provider}/{YYYYMMDD}/{strategy_id}/{trace_id}/
        snapshot_manifest.json
        market_context.json
        scan_config.json
        {SYMBOL}/
            underlying_quote.json
            prices_history.json
            option_chain_{expiration}.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.models.snapshot_manifest import (
    CompletenessInfo,
    MarketContext,
    ScanConfig,
    SnapshotManifest,
    SymbolArtifacts,
)

logger = logging.getLogger(__name__)


class SnapshotCaptureService:
    """Captures a complete offline dataset for scanner replay.

    Parameters
    ----------
    base_data_service : BaseDataService
        Central data-fetching hub (provides underlying price, history, VIX).
    tradier_client : TradierClient
        Source of truth for option chains and expirations.
    fred_client : FredClient
        Macro data (VIX series).
    snapshot_dir : Path
        Root directory for snapshot storage.
    regime_service : RegimeService | None
        Optional — captures regime label if available.
    """

    def __init__(
        self,
        base_data_service: Any,
        tradier_client: Any,
        fred_client: Any,
        snapshot_dir: Path,
        regime_service: Any | None = None,
    ) -> None:
        self._bds = base_data_service
        self._tradier = tradier_client
        self._fred = fred_client
        self._snapshot_dir = snapshot_dir
        self._regime = regime_service

    async def capture(
        self,
        *,
        strategy_id: str,
        symbols: list[str],
        preset_name: str = "balanced",
        data_quality_mode: str = "standard",
        dte_min: int = 3,
        dte_max: int = 60,
        max_expirations_per_symbol: int = 6,
        provider: str = "tradier",
        lookback_days: int = 365,
        request_payload: dict[str, Any] | None = None,
    ) -> SnapshotManifest:
        """Run the full capture pipeline and return the manifest.

        Raises no exceptions for individual symbol failures — errors are
        logged and reflected in the manifest's completeness section.
        """
        t0 = time.monotonic()
        trace_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y%m%d")

        # Build output directory
        run_dir = (
            self._snapshot_dir / provider / date_str / strategy_id / trace_id
        )
        run_dir.mkdir(parents=True, exist_ok=True)

        symbols_upper = [s.strip().upper() for s in symbols if s.strip()]

        manifest = SnapshotManifest(
            trace_id=trace_id,
            created_at=now.isoformat(),
            provider=provider,
            strategy_id=strategy_id,
            preset_name=preset_name,
            data_quality_mode=data_quality_mode,
            symbols=symbols_upper,
        )

        # ── 1. Capture per-symbol data ─────────────────────────────────
        total_chains = 0
        total_expirations = 0
        total_bars = 0

        for symbol in symbols_upper:
            arts = await self._capture_symbol(
                symbol=symbol,
                run_dir=run_dir,
                provider=provider,
                dte_min=dte_min,
                dte_max=dte_max,
                max_expirations=max_expirations_per_symbol,
                lookback_days=lookback_days,
            )
            manifest.symbol_artifacts[symbol] = arts
            total_expirations += len(arts.option_chains)
            total_chains += len(arts.option_chains)

        # ── 2. Capture market context (VIX, regime) ────────────────────
        market_ctx = await self._capture_market_context(run_dir)
        manifest.market_context_path = "market_context.json"

        # ── 3. Write scan config ───────────────────────────────────────
        scan_config = ScanConfig(
            strategy_id=strategy_id,
            preset_name=preset_name,
            data_quality_mode=data_quality_mode,
            symbols=symbols_upper,
            dte_min=dte_min,
            dte_max=dte_max,
            max_expirations_per_symbol=max_expirations_per_symbol,
            request_payload=request_payload or {},
        )
        config_path = run_dir / "scan_config.json"
        config_path.write_text(
            json.dumps(scan_config.model_dump(), indent=2, default=str),
            encoding="utf-8",
        )
        manifest.scan_config_path = "scan_config.json"

        # ── 4. Populate stats & validate completeness ──────────────────
        elapsed = time.monotonic() - t0
        manifest.capture_duration_seconds = round(elapsed, 2)
        manifest.expirations_captured = total_expirations
        manifest.chains_captured = total_chains
        manifest.history_bars_captured = total_bars
        manifest.validate_completeness()

        # ── 5. Write manifest ──────────────────────────────────────────
        manifest_path = run_dir / "snapshot_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest.model_dump(), indent=2, default=str),
            encoding="utf-8",
        )

        logger.info(
            "event=snapshot_capture_complete trace_id=%s strategy=%s "
            "symbols=%s expirations=%d chains=%d duration=%.2fs "
            "complete=%s missing=%s output=%s",
            trace_id,
            strategy_id,
            symbols_upper,
            total_expirations,
            total_chains,
            elapsed,
            manifest.completeness.required_artifacts_present,
            manifest.completeness.missing_artifacts,
            run_dir,
        )

        return manifest

    # ── Per-symbol capture ─────────────────────────────────────────────

    async def _capture_symbol(
        self,
        *,
        symbol: str,
        run_dir: Path,
        provider: str,
        dte_min: int,
        dte_max: int,
        max_expirations: int,
        lookback_days: int,
    ) -> SymbolArtifacts:
        """Capture all data for a single symbol."""
        sym_dir = run_dir / symbol
        sym_dir.mkdir(parents=True, exist_ok=True)

        arts = SymbolArtifacts(symbol=symbol)

        # ── A. Underlying quote ────────────────────────────────────────
        try:
            quote = await self._tradier.get_quote(symbol)
            if quote:
                quote_path = sym_dir / "underlying_quote.json"
                quote_data = {
                    "meta": {
                        "provider": provider,
                        "symbol": symbol,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    "quote": quote,
                }
                quote_path.write_text(
                    json.dumps(quote_data, indent=2, default=str),
                    encoding="utf-8",
                )
                arts.underlying_quote = f"{symbol}/underlying_quote.json"
                logger.info(
                    "event=snapshot_quote_captured symbol=%s", symbol,
                )
        except Exception as exc:
            logger.warning(
                "event=snapshot_quote_error symbol=%s error=%s", symbol, exc,
            )

        # ── B. Price history ───────────────────────────────────────────
        try:
            # Fetch dated history (with date + close for full fidelity)
            dated_bars = await self._bds.get_prices_history_dated(
                symbol, lookback_days=lookback_days,
            )
            if dated_bars:
                history_path = sym_dir / "prices_history.json"
                closes = [
                    float(bar["close"]) for bar in dated_bars
                    if isinstance(bar, dict) and bar.get("close") is not None
                ]
                history_data = {
                    "meta": {
                        "provider": provider,
                        "symbol": symbol,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "timeframe": "daily",
                        "lookback_days": lookback_days,
                        "bar_count": len(dated_bars),
                        "start": dated_bars[0].get("date") if dated_bars else None,
                        "end": dated_bars[-1].get("date") if dated_bars else None,
                    },
                    "bars": dated_bars,
                    "closes": closes,
                }
                history_path.write_text(
                    json.dumps(history_data, indent=2, default=str),
                    encoding="utf-8",
                )
                arts.prices_history = f"{symbol}/prices_history.json"
                logger.info(
                    "event=snapshot_history_captured symbol=%s bars=%d "
                    "range=%s..%s",
                    symbol,
                    len(dated_bars),
                    dated_bars[0].get("date") if dated_bars else "?",
                    dated_bars[-1].get("date") if dated_bars else "?",
                )
            else:
                logger.warning(
                    "event=snapshot_history_empty symbol=%s", symbol,
                )
        except Exception as exc:
            logger.warning(
                "event=snapshot_history_error symbol=%s error=%s", symbol, exc,
            )

        # ── C. Option chains per expiration ────────────────────────────
        try:
            all_expirations = await self._tradier.get_expirations(symbol)
        except Exception as exc:
            logger.warning(
                "event=snapshot_expirations_error symbol=%s error=%s",
                symbol, exc,
            )
            all_expirations = []

        # Filter expirations by DTE range
        today = datetime.now(timezone.utc).date()
        valid_expirations: list[str] = []
        for exp_str in all_expirations:
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                if dte_min <= dte <= dte_max:
                    valid_expirations.append(exp_str)
            except ValueError:
                continue

        # Cap to max_expirations
        valid_expirations = sorted(valid_expirations)[:max_expirations]

        # Fetch underlying price for chain metadata
        underlying_price: float | None = None
        try:
            underlying_price = await self._bds.get_underlying_price(symbol)
        except Exception:
            pass

        for expiration in valid_expirations:
            try:
                # Use raw payload (bypasses cache, full envelope)
                raw_payload = await self._tradier.fetch_chain_raw_payload(
                    symbol, expiration, greeks=True,
                )
                chain_filename = f"option_chain_{expiration}.json"
                chain_path = sym_dir / chain_filename
                chain_data = {
                    "meta": {
                        "provider": provider,
                        "symbol": symbol,
                        "expiration": expiration,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "underlying_price": underlying_price,
                        "endpoint": "/markets/options/chains",
                        "request_params": {
                            "symbol": symbol,
                            "expiration": expiration,
                            "greeks": "true",
                        },
                    },
                    "raw": raw_payload,
                }
                chain_path.write_text(
                    json.dumps(chain_data, indent=2, default=str),
                    encoding="utf-8",
                )
                arts.option_chains[expiration] = f"{symbol}/{chain_filename}"
                logger.info(
                    "event=snapshot_chain_captured symbol=%s expiration=%s",
                    symbol, expiration,
                )
            except Exception as exc:
                logger.warning(
                    "event=snapshot_chain_error symbol=%s expiration=%s "
                    "error=%s",
                    symbol, expiration, exc,
                )

        return arts

    # ── Market context capture ─────────────────────────────────────────

    async def _capture_market_context(self, run_dir: Path) -> MarketContext:
        """Capture VIX + regime data."""
        ctx = MarketContext(
            captured_at=datetime.now(timezone.utc).isoformat(),
        )

        # VIX
        try:
            vix = await self._fred.get_latest_series_value()
            ctx.vix = vix
        except Exception as exc:
            logger.warning("event=snapshot_vix_error error=%s", exc)

        # Regime (optional)
        if self._regime is not None:
            try:
                regime_data = await self._regime.get_regime()
                ctx.regime_label = regime_data.get("regime")
                ctx.regime_score = regime_data.get("composite_score")
            except Exception as exc:
                logger.warning("event=snapshot_regime_error error=%s", exc)

        # Write to disk
        ctx_path = run_dir / "market_context.json"
        ctx_path.write_text(
            json.dumps(ctx.model_dump(), indent=2, default=str),
            encoding="utf-8",
        )
        return ctx

    # ── Listing available snapshots ────────────────────────────────────

    @staticmethod
    def list_snapshots(
        snapshot_dir: Path,
        provider: str = "tradier",
        strategy_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List available snapshot runs from disk.

        Returns list of dicts with trace_id, created_at, strategy_id,
        symbols, completeness, and path.
        """
        results: list[dict[str, Any]] = []
        provider_dir = snapshot_dir / provider
        if not provider_dir.is_dir():
            return results

        for date_dir in sorted(provider_dir.iterdir(), reverse=True):
            if not date_dir.is_dir() or not date_dir.name.isdigit():
                continue
            # Look for strategy subdirs
            for strat_dir in sorted(date_dir.iterdir()):
                if not strat_dir.is_dir():
                    continue
                if strategy_id and strat_dir.name != strategy_id:
                    continue
                for trace_dir in sorted(strat_dir.iterdir(), reverse=True):
                    if not trace_dir.is_dir():
                        continue
                    manifest_path = trace_dir / "snapshot_manifest.json"
                    if not manifest_path.is_file():
                        continue
                    try:
                        raw = json.loads(
                            manifest_path.read_text(encoding="utf-8"),
                        )
                        results.append({
                            "trace_id": raw.get("trace_id"),
                            "created_at": raw.get("created_at"),
                            "strategy_id": raw.get("strategy_id"),
                            "preset_name": raw.get("preset_name"),
                            "symbols": raw.get("symbols", []),
                            "completeness": raw.get("completeness", {}),
                            "chains_captured": raw.get("chains_captured", 0),
                            "expirations_captured": raw.get(
                                "expirations_captured", 0,
                            ),
                            "path": str(trace_dir),
                        })
                    except (json.JSONDecodeError, OSError):
                        continue

        return results

    @staticmethod
    def find_snapshot_by_trace_id(
        snapshot_dir: Path,
        trace_id: str,
        provider: str = "tradier",
    ) -> Path | None:
        """Locate a snapshot run directory by trace_id."""
        provider_dir = snapshot_dir / provider
        if not provider_dir.is_dir():
            return None
        for date_dir in sorted(provider_dir.iterdir(), reverse=True):
            if not date_dir.is_dir() or not date_dir.name.isdigit():
                continue
            for strat_dir in date_dir.iterdir():
                if not strat_dir.is_dir():
                    continue
                trace_dir = strat_dir / trace_id
                if trace_dir.is_dir() and (
                    trace_dir / "snapshot_manifest.json"
                ).is_file():
                    return trace_dir
        return None
