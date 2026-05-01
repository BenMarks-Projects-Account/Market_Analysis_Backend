"""Institutional 13F Service — orchestrator for the 13F pillar.

Coordinates: tier list loading → FMP data fetch → engine compute → cache.
Follows the same pattern as BreadthService/VolatilityOptionsService.

Data flow:
  1. Load tier-1 filer list (JSON config) + derive tier-2 from FMP AUM list
  2. For each symbol in the scanner universe:
       - Fetch institutional holders (13F data)
       - Fetch shares float (for normalization)
       - Fetch company profile (for sector mapping)
  3. Invoke ``institutional_13f_engine.compute_13f_scores()``
  4. Cache result (24-hour TTL — data is quarterly)
  5. Return structured payload matching engine output contract

New-filing detection:
  - On each call, check if new 13F filings have appeared since last compute
  - If no new filings, return cached result
  - Configurable via ``PILLAR_13F_RECOMPUTE_CHECK_INTERVAL_SECONDS``
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.engine_output_contract import normalize_engine_output
from app.services.institutional_13f_engine import compute_13f_scores
from app.utils.cache import TTLCache

logger = logging.getLogger(__name__)

# ── Default config ──────────────────────────────────────────────────

_DEFAULT_CACHE_TTL = 86400  # 24 hours
_DEFAULT_RECOMPUTE_CHECK_INTERVAL = 3600  # 1 hour
_DEFAULT_TIER1_WEIGHT = 3.0
_DEFAULT_TIER2_WEIGHT = 1.0
_DEFAULT_TIER2_SIZE = 100


def _current_13f_quarter() -> tuple[int, int]:
    """Return the most recent quarter with likely-complete 13F data.

    13F filings are due 45 days after quarter end.  We pick the quarter
    whose filing deadline has passed (with a small buffer).
    """
    now = datetime.now(timezone.utc)
    year = now.year
    month = now.month
    day = now.day

    if month >= 6 or (month == 5 and day >= 20):
        return (year, 1)
    if month >= 9 or (month == 8 and day >= 20):
        return (year, 2)
    if month >= 12 or (month == 11 and day >= 20):
        return (year, 3)
    if month >= 3 or (month == 2 and day >= 20):
        return (year - 1, 4)
    return (year - 1, 3)


def _prev_quarter(year: int, quarter: int) -> tuple[int, int]:
    """Return the quarter before the given one."""
    if quarter == 1:
        return (year - 1, 4)
    return (year, quarter - 1)


class Institutional13FService:
    """Service layer for Institutional 13F pillar.

    Orchestrates data fetch → engine computation → cache.
    """

    def __init__(
        self,
        fmp_client: Any,
        cache: TTLCache,
        settings: Any,
    ) -> None:
        self.fmp_client = fmp_client
        self.cache = cache
        self.settings = settings

        # Tier/weight config
        self._tier1_weight = float(
            getattr(settings, "SMART_MONEY_TIER1_WEIGHT", _DEFAULT_TIER1_WEIGHT),
        )
        self._tier2_weight = float(
            getattr(settings, "SMART_MONEY_TIER2_WEIGHT", _DEFAULT_TIER2_WEIGHT),
        )
        self._tier2_size = int(
            getattr(settings, "SMART_MONEY_TIER2_SIZE", _DEFAULT_TIER2_SIZE),
        )
        self._cache_ttl = int(
            getattr(settings, "PILLAR_13F_CACHE_TTL_SECONDS", _DEFAULT_CACHE_TTL),
        )
        self._recompute_check_interval = int(
            getattr(
                settings,
                "PILLAR_13F_RECOMPUTE_CHECK_INTERVAL_SECONDS",
                _DEFAULT_RECOMPUTE_CHECK_INTERVAL,
            ),
        )

        # State
        self._tier1_filers: list[dict[str, Any]] = []
        self._tier2_filers: list[dict[str, Any]] = []
        self._filer_weights: dict[str, float] = {}
        self._tier1_ciks: set[str] = set()
        self._last_recompute_at: str | None = None
        self._last_new_filing_check: float = 0.0
        self._initialized = False

    # ── Initialization ──────────────────────────────────────────

    def _load_tier1_filers(self) -> list[dict[str, Any]]:
        """Load tier-1 filers from JSON config file."""
        config_path_str = getattr(
            self.settings, "SMART_MONEY_TIER1_FILERS_PATH",
            "config/smart_money_tier1.json",
        )
        # Resolve relative to app directory
        config_path = Path(__file__).parent.parent / config_path_str
        if not config_path.exists():
            logger.warning("Tier-1 filer config not found: %s", config_path)
            return []

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            filers = data.get("filers", [])
            logger.info("event=tier1_filers_loaded count=%d", len(filers))
            return filers
        except Exception as exc:
            logger.error("Failed to load tier-1 filers: %s", exc)
            return []

    async def _load_tier2_filers(self) -> list[dict[str, Any]]:
        """Derive tier-2 filers from FMP institutional ownership list (top N by AUM)."""
        raw = await self.fmp_client.get_institutional_ownership_list(
            limit=self._tier2_size,
        )
        if not raw:
            logger.warning("event=tier2_filers_unavailable source=fmp")
            return []

        filers: list[dict[str, Any]] = []
        for entry in raw:
            cik = str(entry.get("cik", "")).zfill(10)
            if cik in self._tier1_ciks:
                continue  # Already tier-1, skip
            filers.append({
                "name": entry.get("investorName", "Unknown"),
                "cik": cik,
                "short_name": entry.get("investorName", "")[:40],
            })

        logger.info("event=tier2_filers_loaded count=%d", len(filers))
        return filers

    async def _initialize(self) -> None:
        """Load filer lists and build weight map. Runs once."""
        if self._initialized:
            return

        # Load tier 1
        self._tier1_filers = self._load_tier1_filers()
        self._tier1_ciks = {
            f["cik"].zfill(10) for f in self._tier1_filers if f.get("cik")
        }

        # Build tier 1 weights
        for filer in self._tier1_filers:
            cik = filer["cik"].zfill(10)
            self._filer_weights[cik] = self._tier1_weight

        # Load tier 2 (async FMP call)
        self._tier2_filers = await self._load_tier2_filers()
        for filer in self._tier2_filers:
            cik = filer["cik"].zfill(10)
            if cik not in self._filer_weights:
                self._filer_weights[cik] = self._tier2_weight

        self._initialized = True
        logger.info(
            "event=13f_service_initialized tier1=%d tier2=%d total_weighted=%d",
            len(self._tier1_filers),
            len(self._tier2_filers),
            len(self._filer_weights),
        )

    # ── New-filing detection ────────────────────────────────────

    async def check_for_new_filings(self) -> bool:
        """Check if new 13F filings exist since last recompute.

        Queries FMP for filing dates of a sentinel tier-1 filer.
        Returns True if the latest filing period is newer than what
        we last computed.
        """
        import time
        now = time.monotonic()
        if now - self._last_new_filing_check < self._recompute_check_interval:
            return False  # Rate-limit the check itself
        self._last_new_filing_check = now

        if not self._tier1_filers:
            return True  # Haven't initialized yet, force a compute

        # Check a well-known filer (Berkshire Hathaway) for new filing dates
        sentinel_cik = self._tier1_filers[0].get("cik", "")
        if not sentinel_cik:
            return True

        dates = await self.fmp_client.get_13f_filing_dates(sentinel_cik)
        if not dates:
            return False  # Can't check, assume no new filings

        latest_period = max(
            (d.get("date", "") for d in dates if d.get("date")),
            default="",
        )
        if not latest_period:
            return False

        # Compare with what we last computed
        if self._last_recompute_at and latest_period <= self._last_recompute_at:
            logger.debug("event=13f_no_new_filings latest=%s", latest_period)
            return False

        logger.info(
            "event=13f_new_filings_detected latest=%s previous=%s",
            latest_period,
            self._last_recompute_at,
        )
        return True

    # ── Data fetch ──────────────────────────────────────────────

    async def _fetch_universe_data(
        self,
        universe: list[str],
        year: int,
        quarter: int,
    ) -> tuple[
        dict[str, list[dict[str, Any]]],
        dict[str, dict[str, Any]],
        dict[str, str],
        dict[str, float],
    ]:
        """Fetch 13F holders, float data, sector map, and market caps for universe.

        Returns (holdings_data, float_data, sector_map, market_caps)
        """
        # Batch fetch with concurrency control
        sem = asyncio.Semaphore(10)  # Limit concurrent FMP calls

        async def _get_holders(symbol: str) -> tuple[str, list[dict[str, Any]]]:
            async with sem:
                data = await self.fmp_client.get_institutional_holders(
                    symbol, year, quarter, limit=100,
                )
                return (symbol, data or [])

        async def _get_float(symbol: str) -> tuple[str, dict[str, Any]]:
            async with sem:
                data = await self.fmp_client.get_shares_float(symbol)
                if data and isinstance(data, list) and data:
                    return (symbol, data[0])
                return (symbol, {})

        async def _get_profile(symbol: str) -> tuple[str, dict[str, Any]]:
            async with sem:
                data = await self.fmp_client.get_company_profile(symbol)
                return (symbol, data or {})

        # Run all fetches concurrently
        holders_results, float_results, profile_results = await asyncio.gather(
            asyncio.gather(*[_get_holders(s) for s in universe]),
            asyncio.gather(*[_get_float(s) for s in universe]),
            asyncio.gather(*[_get_profile(s) for s in universe]),
        )

        holdings_data = dict(holders_results)
        float_data = dict(float_results)

        sector_map: dict[str, str] = {}
        market_caps: dict[str, float] = {}
        for symbol, profile in profile_results:
            sector_map[symbol] = profile.get("sector", "Unknown") or "Unknown"
            market_caps[symbol] = float(profile.get("mktCap", 0) or 0)

        logger.info(
            "event=13f_data_fetched symbols=%d holders_found=%d sectors=%d",
            len(universe),
            sum(1 for v in holdings_data.values() if v),
            len(set(sector_map.values()) - {"Unknown"}),
        )
        return holdings_data, float_data, sector_map, market_caps

    # ── Main entry point ────────────────────────────────────────

    async def get_13f_analysis(
        self,
        *,
        force: bool = False,
        universe: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return full 13F institutional analysis.

        Parameters
        ----------
        force : bool
            If True, bypass cache and recompute.
        universe : list[str] | None
            Override the symbol universe. If None, uses config symbols.

        Returns
        -------
        dict with: engine_result, data_quality, as_of, compute_duration_s
        """
        cache_key = "institutional_13f"

        if not force:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                # Check for new filings before returning stale cache
                has_new = await self.check_for_new_filings()
                if not has_new:
                    logger.info("event=13f_cache_hit")
                    return cached
                logger.info("event=13f_cache_stale new_filings_detected")

        # Check if FMP is available
        if not self.fmp_client.is_available():
            return self._degraded_result("FMP client unavailable")

        logger.info("event=13f_compute_start force=%s", force)
        start = datetime.now(timezone.utc)

        try:
            await self._initialize()

            if not self._filer_weights:
                return self._degraded_result("No filer weights available (tier config empty)")

            # Resolve universe
            if universe is None:
                universe = self._get_default_universe()

            year, quarter = _current_13f_quarter()
            prev_year, prev_quarter = _prev_quarter(year, quarter)

            # Fetch data
            holdings_data, float_data, sector_map, market_caps = (
                await self._fetch_universe_data(universe, year, quarter)
            )

            # Compute engine result
            engine_result = compute_13f_scores(
                universe=universe,
                holdings_data=holdings_data,
                float_data=float_data,
                sector_map=sector_map,
                market_caps=market_caps,
                filer_weights=self._filer_weights,
                tier1_ciks=self._tier1_ciks,
                prior_sector_scores=None,  # TODO: load from prior cached result
            )

            duration = (datetime.now(timezone.utc) - start).total_seconds()
            self._last_recompute_at = f"{year}-Q{quarter}"

            payload = {
                "engine_result": engine_result,
                "data_quality": {
                    "coverage_pct": engine_result.get("diagnostics", {}).get("coverage_pct", 0),
                    "sectors_covered": engine_result.get("diagnostics", {}).get("sectors_covered", 0),
                    "universe_size": len(universe),
                    "data_stale": False,
                    "quarter": f"{year}-Q{quarter}",
                },
                "as_of": datetime.now(timezone.utc).isoformat(),
                "compute_duration_s": round(duration, 2),
                "cache_info": {
                    "cached": False,
                    "ttl_seconds": self._cache_ttl,
                },
            }

            # Cache the result
            await self.cache.set(cache_key, payload, ttl=self._cache_ttl)
            logger.info(
                "event=13f_compute_complete duration=%.2fs score=%.1f classification=%s",
                duration,
                engine_result.get("score", 0),
                engine_result.get("classification", "unknown"),
            )
            return payload

        except Exception as exc:
            logger.error("13F pillar compute failed: %s", exc, exc_info=True)
            return self._degraded_result(f"Compute error: {exc}")

    def _get_default_universe(self) -> list[str]:
        """Get the default scanner universe from config."""
        symbols_str = getattr(self.settings, "OPTIONS_SCAN_SYMBOLS", "")
        if not symbols_str:
            return []
        return [s.strip() for s in symbols_str.split(",") if s.strip()]

    def _degraded_result(self, reason: str) -> dict[str, Any]:
        """Return a neutral/degraded result when data is unavailable."""
        logger.warning("event=13f_degraded reason=%s", reason)
        return {
            "engine_result": {
                "score": 50.0,
                "label": "Neutral Institutional Flow",
                "short_label": "Neutral",
                "confidence_score": 0.0,
                "classification": "neutral",
                "pillars": {},
                "sector_heatmap": {},
                "notable_moves": {
                    "top_new_positions": [],
                    "top_exits": [],
                    "top_increased_stakes": [],
                    "top_decreased_stakes": [],
                    "consensus_buys": [],
                    "consensus_sells": [],
                },
                "top_stocks": [],
                "summary": f"13F data unavailable: {reason}",
                "trader_takeaway": "No institutional signal available.",
                "warnings": [reason],
                "diagnostics": {
                    "universe_size": 0,
                    "symbols_with_data": 0,
                    "coverage_pct": 0,
                    "sectors_covered": 0,
                    "tier1_filers_matched": 0,
                    "total_filers_tracked": 0,
                },
            },
            "data_quality": {
                "coverage_pct": 0,
                "sectors_covered": 0,
                "universe_size": 0,
                "data_stale": True,
                "quarter": "unavailable",
            },
            "as_of": datetime.now(timezone.utc).isoformat(),
            "compute_duration_s": 0,
            "cache_info": {"cached": False, "degraded": True},
        }

    # ── Force recompute (admin) ─────────────────────────────────

    async def force_recompute(self) -> dict[str, Any]:
        """Admin endpoint: force a full recompute bypassing cache and filing check."""
        self._initialized = False  # Force re-initialization of filer lists
        self._last_new_filing_check = 0.0  # Reset check timer
        return await self.get_13f_analysis(force=True)
