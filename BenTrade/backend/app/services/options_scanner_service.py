"""Options Scanner Service — V2 scanner orchestration adapter.

Thin service that implements the ``async scan(symbols, scanner_keys, context)``
interface required by ``OptionsOpportunityDeps`` in the options workflow runner.

Orchestrates V2 scanner families via the registry, fetching option chains
from ``base_data_service`` and running each (scanner_key × symbol) combination.

This is the data-provider boundary for the options workflow runner:
the runner never calls Tradier or any market-data API directly.

Input contract::

    await options_scanner_service.scan(
        symbols=["SPY", "QQQ"],
        scanner_keys=["put_credit_spread", "iron_condor", ...],
        context={"market_state_ref": ..., "consumer_summary": ...},
    )

Output contract::

    {
        "scan_results": [
            {
                "scanner_key": str,
                "strategy_id": str,
                "family_key": str,
                "symbol": str,
                "candidates": [V2Candidate.to_dict(), ...],
                "rejected": [V2Candidate.to_dict(), ...],
                "total_constructed": int,
                "total_passed": int,
                "total_rejected": int,
                "reject_reason_counts": dict,
                "warning_counts": dict,
                "phase_counts": list,
                "elapsed_ms": float,
            },
            ...
        ],
        "warnings": [str, ...],
        "scanners_total": int,
        "scanners_ok": int,
        "scanners_failed": int,
    }
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

_log = logging.getLogger("bentrade.options_scanner_service")

# Maximum DTE any scanner family uses (verticals & calendars = 90).
# Expirations beyond this are never candidates and can be skipped during
# prefetch to save Tradier API calls.
_MAX_PREFETCH_DTE = 90


class _PrefetchedSymbol:
    """Lightweight container for one symbol's pre-fetched chain data."""

    __slots__ = ("symbol", "merged_options", "underlying_price", "chain")

    def __init__(
        self,
        symbol: str,
        *,
        merged_options: list[dict[str, Any]] | None = None,
        underlying_price: float | None = None,
    ) -> None:
        self.symbol = symbol
        self.merged_options = merged_options or []
        self.underlying_price = underlying_price
        # Pre-build the chain dict shape V2 scanners expect
        self.chain: dict[str, Any] = (
            {"options": {"option": self.merged_options}}
            if self.merged_options
            else {}
        )

    @property
    def contract_count(self) -> int:
        return len(self.merged_options)


class OptionsScannerService:
    """V2 scanner orchestration adapter for the options workflow runner.

    Parameters
    ----------
    base_data_service
        Provides ``get_expirations(symbol)``, ``get_analysis_inputs(symbol, expiration)``,
        and ``get_underlying_price(symbol)`` via the Tradier chain source.
    """

    def __init__(self, *, base_data_service: Any) -> None:
        self._bds = base_data_service

    # ── public API ──────────────────────────────────────────────────────

    async def scan(
        self,
        symbols: list[str],
        scanner_keys: list[str],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run V2 scanners across symbols and scanner_keys.

        Phase 1 — Prefetch: fetch expirations, underlying prices, and
        option chains ONCE per symbol (DTE-filtered to 0-90).

        Phase 2 — Scan: iterate (scanner_key × symbol) and run each V2
        scanner on the prefetched merged chain.  No Tradier calls here.

        Returns the aggregate result dict matching the runner's expected shape.
        """
        from app.services.scanner_v2.registry import (
            get_v2_family,
            get_v2_scanner,
            is_v2_supported,
        )

        ctx = context or {}

        # ── Phase 1: prefetch chain data per symbol ──────────────────
        prefetch_start = time.monotonic()
        _log.info("event=scan_prefetch_start symbols=%d", len(symbols))

        prefetched: dict[str, _PrefetchedSymbol] = {}
        for symbol in symbols:
            prefetched[symbol] = await self._prefetch_symbol(symbol)

        prefetch_dur = time.monotonic() - prefetch_start
        total_chains = sum(p.contract_count for p in prefetched.values())
        _log.info(
            "event=scan_prefetch_complete duration_s=%.1f symbols=%d "
            "total_contracts=%d",
            prefetch_dur, len(prefetched), total_chains,
        )

        # ── Phase 2: run scanners on prefetched data ─────────────────
        scan_start = time.monotonic()
        all_results: list[dict[str, Any]] = []
        warnings: list[str] = []
        scanners_total = 0
        scanners_ok = 0
        scanners_failed = 0

        for scanner_key in scanner_keys:
            if not is_v2_supported(scanner_key):
                warnings.append(
                    f"Scanner key {scanner_key!r} has no V2 implementation — skipped"
                )
                continue

            family_meta = get_v2_family(scanner_key)
            if family_meta is None:
                continue

            scanner = get_v2_scanner(scanner_key)

            for symbol in symbols:
                scanners_total += 1
                pf = prefetched.get(symbol)
                if pf is None or not pf.merged_options:
                    all_results.append(
                        self._empty_result(
                            scanner_key, scanner_key,
                            family_meta.family_key, symbol,
                        )
                    )
                    scanners_ok += 1
                    continue

                try:
                    result = self._run_on_cached(
                        scanner=scanner,
                        scanner_key=scanner_key,
                        strategy_id=scanner_key,
                        family_key=family_meta.family_key,
                        symbol=symbol,
                        chain=pf.chain,
                        underlying_price=pf.underlying_price,
                        context=ctx,
                    )
                    all_results.append(result)
                    scanners_ok += 1
                except Exception as exc:
                    scanners_failed += 1
                    warnings.append(
                        f"Scanner {scanner_key} failed for {symbol}: {exc}"
                    )
                    _log.warning(
                        "event=scanner_failed scanner_key=%s symbol=%s error=%s",
                        scanner_key, symbol, exc,
                    )

        scan_dur = time.monotonic() - scan_start
        _log.info(
            "event=scan_phase2_complete duration_s=%.1f scanners=%d ok=%d "
            "failed=%d candidates=%d",
            scan_dur, scanners_total, scanners_ok, scanners_failed,
            sum(r.get("total_passed", 0) for r in all_results),
        )

        return {
            "scan_results": all_results,
            "warnings": warnings,
            "scanners_total": scanners_total,
            "scanners_ok": scanners_ok,
            "scanners_failed": scanners_failed,
        }

    # ── prefetch helpers ────────────────────────────────────────────────

    async def _prefetch_symbol(self, symbol: str) -> _PrefetchedSymbol:
        """Fetch expirations, underlying price, and merged chain for *symbol*.

        Only expirations within ``_MAX_PREFETCH_DTE`` days are fetched.
        Returns a lightweight container used by Phase 2.
        """
        try:
            expirations = await self._bds.tradier_client.get_expirations(symbol)
        except Exception as exc:
            _log.warning("event=prefetch_expirations_failed symbol=%s error=%s", symbol, exc)
            return _PrefetchedSymbol(symbol)

        if not expirations:
            _log.info("event=prefetch_no_expirations symbol=%s", symbol)
            return _PrefetchedSymbol(symbol)

        # DTE-filter: keep only expirations within the window any scanner uses
        today = date.today()
        max_exp_date = today + timedelta(days=_MAX_PREFETCH_DTE)
        relevant: list[str] = []
        for exp in expirations:
            try:
                exp_date = date.fromisoformat(str(exp))
            except (ValueError, TypeError):
                continue
            if today < exp_date <= max_exp_date:
                relevant.append(exp)

        if not relevant:
            _log.info(
                "event=prefetch_no_relevant_expirations symbol=%s total=%d max_dte=%d",
                symbol, len(expirations), _MAX_PREFETCH_DTE,
            )
            return _PrefetchedSymbol(symbol)

        underlying_price = await self._bds.get_underlying_price(symbol)

        # Fetch chains per expiration and merge.
        # get_analysis_inputs returns OptionContract (Pydantic) objects;
        # V2 scanners expect raw dicts → convert at this boundary.
        merged_options: list[dict[str, Any]] = []
        for exp in relevant:
            try:
                inputs = await self._bds.get_analysis_inputs(
                    symbol, exp, include_prices_history=False,
                )
                contracts = inputs.get("contracts") or []
                for c in contracts:
                    merged_options.append(
                        c.model_dump() if hasattr(c, "model_dump") else c
                    )
            except Exception as exc:
                _log.debug(
                    "event=prefetch_chain_skip symbol=%s exp=%s error=%s",
                    symbol, exp, exc,
                )

        chain_source = (
            type(self._bds.chain_source).__name__
            if hasattr(self._bds, "chain_source")
            else "unknown"
        )
        _log.info(
            "event=prefetch_symbol_done symbol=%s expirations=%d "
            "contracts=%d chain_source=%s",
            symbol, len(relevant), len(merged_options), chain_source,
        )
        return _PrefetchedSymbol(
            symbol,
            merged_options=merged_options,
            underlying_price=underlying_price,
        )

    # ── scanner execution (no I/O) ─────────────────────────────────────

    @staticmethod
    def _run_on_cached(
        *,
        scanner: Any,
        scanner_key: str,
        strategy_id: str,
        family_key: str,
        symbol: str,
        chain: dict[str, Any],
        underlying_price: float | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a V2 scanner on pre-fetched chain data (pure computation)."""
        scan_result = scanner.run(
            scanner_key=scanner_key,
            strategy_id=strategy_id,
            symbol=symbol,
            chain=chain,
            underlying_price=underlying_price,
            context=context,
        )

        result_dict = scan_result.to_dict()
        _log.info(
            "event=scanner_completed scanner_key=%s symbol=%s "
            "constructed=%d passed=%d rejected=%d",
            scanner_key, symbol,
            result_dict.get("total_constructed", 0),
            result_dict.get("total_passed", 0),
            result_dict.get("total_rejected", 0),
        )
        return result_dict

    @staticmethod
    def _empty_result(
        scanner_key: str,
        strategy_id: str,
        family_key: str,
        symbol: str,
    ) -> dict[str, Any]:
        return {
            "scanner_key": scanner_key,
            "strategy_id": strategy_id,
            "family_key": family_key,
            "symbol": symbol,
            "candidates": [],
            "rejected": [],
            "total_constructed": 0,
            "total_passed": 0,
            "total_rejected": 0,
            "reject_reason_counts": {},
            "warning_counts": {},
            "phase_counts": [],
            "elapsed_ms": 0.0,
        }
