"""CLI entrypoint for snapshot capture.

Usage:
    python -m app.tools.capture_snapshot \
        --strategy credit_spread \
        --preset balanced \
        --symbols SPY,QQQ \
        --dte-min 3 --dte-max 45

Run from the backend/ directory with the virtual environment active.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure backend/ is on sys.path for relative imports
_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture a complete offline snapshot dataset for BenTrade scanners.",
    )
    parser.add_argument(
        "--strategy",
        required=True,
        help="Strategy ID (credit_spread, iron_condor, butterflies, etc.)",
    )
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated symbols (e.g. SPY,QQQ,IWM)",
    )
    parser.add_argument(
        "--preset",
        default="balanced",
        help="Scanner preset name (default: balanced)",
    )
    parser.add_argument(
        "--dte-min",
        type=int,
        default=3,
        help="Minimum DTE for expirations (default: 3)",
    )
    parser.add_argument(
        "--dte-max",
        type=int,
        default=60,
        help="Maximum DTE for expirations (default: 60)",
    )
    parser.add_argument(
        "--max-expirations",
        type=int,
        default=6,
        help="Max expirations per symbol (default: 6)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=365,
        help="Days of price history to capture (default: 365)",
    )
    parser.add_argument(
        "--provider",
        default="tradier",
        help="Data provider (default: tradier)",
    )
    parser.add_argument(
        "--data-quality-mode",
        default="standard",
        help="Data quality mode (default: standard)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose/debug logging",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    import httpx
    from app.config import get_settings
    from app.clients.tradier_client import TradierClient
    from app.clients.finnhub_client import FinnhubClient
    from app.clients.fred_client import FredClient
    from app.clients.polygon_client import PolygonClient
    from app.services.base_data_service import BaseDataService
    from app.services.snapshot_capture_service import SnapshotCaptureService
    from app.utils.cache import TTLCache
    from app.utils.snapshot import TradierChainSource

    logger = logging.getLogger("capture_snapshot")

    settings = get_settings()
    snapshot_dir = Path(settings.SNAPSHOT_DIR) if settings.SNAPSHOT_DIR else _BACKEND_DIR / "data" / "snapshots"

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        logger.error("No symbols specified.")
        sys.exit(1)

    logger.info(
        "Starting snapshot capture: strategy=%s symbols=%s preset=%s "
        "dte=%d-%d max_exp=%d lookback=%d",
        args.strategy, symbols, args.preset,
        args.dte_min, args.dte_max, args.max_expirations, args.lookback_days,
    )

    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS) as http_client:
        cache = TTLCache()

        tradier_client = TradierClient(settings=settings, http_client=http_client, cache=cache)
        finnhub_client = FinnhubClient(settings=settings, http_client=http_client, cache=cache)
        polygon_client = PolygonClient(settings=settings, http_client=http_client, cache=cache)
        fred_client = FredClient(settings=settings, http_client=http_client, cache=cache)

        chain_source = TradierChainSource(tradier_client)

        base_data_service = BaseDataService(
            tradier_client=tradier_client,
            finnhub_client=finnhub_client,
            fred_client=fred_client,
            polygon_client=polygon_client,
            chain_source=chain_source,
        )

        capture_service = SnapshotCaptureService(
            base_data_service=base_data_service,
            tradier_client=tradier_client,
            fred_client=fred_client,
            snapshot_dir=snapshot_dir,
        )

        manifest = await capture_service.capture(
            strategy_id=args.strategy,
            symbols=symbols,
            preset_name=args.preset,
            data_quality_mode=args.data_quality_mode,
            dte_min=args.dte_min,
            dte_max=args.dte_max,
            max_expirations_per_symbol=args.max_expirations,
            provider=args.provider,
            lookback_days=args.lookback_days,
        )

    # Print summary
    print("\n" + "=" * 60)
    print("SNAPSHOT CAPTURE COMPLETE")
    print("=" * 60)
    print(f"  trace_id:    {manifest.trace_id}")
    print(f"  strategy:    {manifest.strategy_id}")
    print(f"  preset:      {manifest.preset_name}")
    print(f"  symbols:     {manifest.symbols}")
    print(f"  chains:      {manifest.chains_captured}")
    print(f"  expirations: {manifest.expirations_captured}")
    print(f"  duration:    {manifest.capture_duration_seconds}s")
    print(f"  complete:    {manifest.completeness.required_artifacts_present}")
    if manifest.completeness.missing_artifacts:
        print(f"  MISSING:     {manifest.completeness.missing_artifacts}")

    # Find output path
    from app.services.snapshot_capture_service import SnapshotCaptureService as SCS
    run_dir = SCS.find_snapshot_by_trace_id(
        snapshot_dir, manifest.trace_id, provider=args.provider,
    )
    print(f"  output:      {run_dir}")
    print("=" * 60)

    if not manifest.completeness.required_artifacts_present:
        logger.warning(
            "Snapshot is INCOMPLETE — missing: %s",
            manifest.completeness.missing_artifacts,
        )
        sys.exit(1)


def main() -> None:
    args = _parse_args()
    _setup_logging(verbose=args.verbose)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
