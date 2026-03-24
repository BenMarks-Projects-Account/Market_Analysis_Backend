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

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("bentrade.options_scanner_service")


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

    async def scan(
        self,
        symbols: list[str],
        scanner_keys: list[str],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run V2 scanners across symbols and scanner_keys.

        For each (scanner_key, symbol) pair:
        1. Resolve expirations via Tradier
        2. Fetch the full chain for each expiration
        3. Run the V2 scanner family
        4. Collect results

        Returns the aggregate result dict matching the runner's expected shape.
        """
        from app.services.scanner_v2.registry import (
            get_v2_family,
            get_v2_scanner,
            is_v2_supported,
        )

        ctx = context or {}
        all_results: list[dict[str, Any]] = []
        warnings: list[str] = []
        scanners_total = 0
        scanners_ok = 0
        scanners_failed = 0

        for scanner_key in scanner_keys:
            if not is_v2_supported(scanner_key):
                warnings.append(f"Scanner key {scanner_key!r} has no V2 implementation — skipped")
                continue

            family_meta = get_v2_family(scanner_key)
            if family_meta is None:
                continue

            for symbol in symbols:
                scanners_total += 1
                try:
                    result = await self._run_one(
                        scanner_key=scanner_key,
                        strategy_id=scanner_key,
                        family_key=family_meta.family_key,
                        symbol=symbol,
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

        return {
            "scan_results": all_results,
            "warnings": warnings,
            "scanners_total": scanners_total,
            "scanners_ok": scanners_ok,
            "scanners_failed": scanners_failed,
        }

    async def _run_one(
        self,
        *,
        scanner_key: str,
        strategy_id: str,
        family_key: str,
        symbol: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a single V2 scanner for one symbol.

        Fetches chains from base_data_service, then delegates to the
        synchronous V2 scanner.run() method.
        """
        from app.services.scanner_v2.registry import get_v2_scanner

        scanner = get_v2_scanner(scanner_key)

        # Get expirations for this symbol
        expirations = await self._bds.tradier_client.get_expirations(symbol)
        if not expirations:
            _log.info(
                "event=no_expirations scanner_key=%s symbol=%s", scanner_key, symbol,
            )
            return self._empty_result(scanner_key, strategy_id, family_key, symbol)

        # Get underlying price
        underlying_price = await self._bds.get_underlying_price(symbol)

        # ── DIAGNOSTIC: chain fetch tracing (TEMPORARY — remove after debugging) ──
        _chain_diag: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "scanner_key": scanner_key,
            "expirations_returned": len(expirations),
            "expiration_list": list(expirations)[:20],
            "underlying_price": underlying_price,
            "data_source_class": type(self._bds.chain_source).__name__
            if hasattr(self._bds, "chain_source") else "unknown",
        }
        _per_exp_counts: dict[str, Any] = {}
        # ── END DIAGNOSTIC HEADER ──

        # Fetch chains for available expirations and merge into one chain dict.
        # base_data_service.get_analysis_inputs returns OptionContract (Pydantic)
        # objects via normalize_chain, but V2 scanners expect raw dicts.
        # Convert via .model_dump() at this boundary.
        merged_options: list[dict[str, Any]] = []
        for exp in expirations:
            try:
                inputs = await self._bds.get_analysis_inputs(symbol, exp, include_prices_history=False)
                contracts = inputs.get("contracts") or []
                _exp_count = len(contracts) if hasattr(contracts, "__len__") else 0
                _per_exp_counts[str(exp)] = _exp_count  # DIAGNOSTIC
                for c in contracts:
                    merged_options.append(
                        c.model_dump() if hasattr(c, "model_dump") else c
                    )
            except Exception as exc:
                _per_exp_counts[str(exp)] = f"ERROR: {exc}"  # DIAGNOSTIC
                _log.debug(
                    "event=chain_fetch_skip scanner_key=%s symbol=%s exp=%s error=%s",
                    scanner_key, symbol, exp, exc,
                )
                continue

        # ── DIAGNOSTIC: finalize chain fetch trace ──
        _chain_diag["per_expiration_contract_counts"] = _per_exp_counts
        _chain_diag["total_contracts_fetched"] = len(merged_options)
        _chain_diag["expirations_with_contracts"] = sum(
            1 for v in _per_exp_counts.values() if isinstance(v, int) and v > 0
        )
        if merged_options:
            _chain_diag["sample_contracts"] = merged_options[:3]
        # Skip file write during test runs (MagicMock data source)
        _ds_class = _chain_diag.get("data_source_class", "")
        if _ds_class not in ("MagicMock", "Mock", "unknown"):
            try:
                _diag_dir = Path("results/diagnostics")
                _diag_dir.mkdir(parents=True, exist_ok=True)
                _diag_file = _diag_dir / f"chain_diag_{symbol}_{scanner_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                with open(_diag_file, "w") as _f:
                    json.dump(_chain_diag, _f, indent=2, default=str)
            except Exception:
                pass
        # ── END DIAGNOSTIC ──

        if not merged_options:
            _log.info(
                "event=no_chain_data scanner_key=%s symbol=%s", scanner_key, symbol,
            )
            return self._empty_result(scanner_key, strategy_id, family_key, symbol)

        _log.info(
            "event=chain_merged scanner_key=%s symbol=%s expirations=%d contracts=%d chain_source=%s",
            scanner_key, symbol, len(expirations), len(merged_options),
            _chain_diag.get("data_source_class", "unknown"),
        )

        # Build chain dict in the shape V2 scanners expect
        chain = {"options": {"option": merged_options}}

        # Run the V2 scanner (synchronous)
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
