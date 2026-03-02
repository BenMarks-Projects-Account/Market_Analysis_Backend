from __future__ import annotations

import inspect
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

logger = logging.getLogger(__name__)

from app.services.strategies.base import StrategyPlugin
from app.services.strategies.butterflies import ButterfliesStrategyPlugin
from app.services.strategies.calendars import CalendarsStrategyPlugin
from app.services.strategies.credit_spread import CreditSpreadStrategyPlugin
from app.services.strategies.debit_spreads import DebitSpreadsStrategyPlugin
from app.services.strategies.income import IncomeStrategyPlugin
from app.services.strategies.iron_condor import IronCondorStrategyPlugin
from app.services.validation_events import ValidationEventsService
from app.utils.computed_metrics import apply_metrics_contract
from app.utils.dates import dte_ceil
from app.utils.expected_fill import build_fill_trace
from app.utils.normalize import normalize_trade
from app.utils.report_conformance import validate_report_file
from app.utils.snapshot import SnapshotChainSource
from app.utils.strategy_id_resolver import resolve_strategy_id_or_none
from app.utils.trade_key import canonicalize_strategy_id, canonicalize_trade_key, trade_key

# Central symbol universe for all strategy scanners.
# Every generate() call that does not specify explicit symbols falls back here.
DEFAULT_SCANNER_SYMBOLS: list[str] = ["SPY", "QQQ", "IWM", "DIA", "XSP", "RUT", "NDX"]


class StrategyService:
    def __init__(
        self,
        base_data_service: Any,
        results_dir: Path,
        risk_policy_service: Any | None = None,
        signal_service: Any | None = None,
        regime_service: Any | None = None,
        platform_settings: Any | None = None,
        snapshot_dir: Path | None = None,
    ) -> None:
        self.base_data_service = base_data_service
        self.results_dir = results_dir
        self.risk_policy_service = risk_policy_service
        self.signal_service = signal_service
        self.regime_service = regime_service
        self.platform_settings = platform_settings
        self._snapshot_dir = snapshot_dir
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.validation_events = ValidationEventsService(results_dir=self.results_dir)
        self.data_workbench_records_path = self.results_dir / "data_workbench_records.jsonl"
        self._records_lock = RLock()

        self._plugins: dict[str, StrategyPlugin] = {}
        self.register(CreditSpreadStrategyPlugin())
        self.register(DebitSpreadsStrategyPlugin())
        self.register(IronCondorStrategyPlugin())
        self.register(ButterfliesStrategyPlugin())
        self.register(CalendarsStrategyPlugin())
        self.register(IncomeStrategyPlugin())

    def register(self, plugin: StrategyPlugin) -> None:
        key = str(getattr(plugin, "id", "")).strip().lower()
        if not key:
            raise ValueError("plugin.id is required")
        self._plugins[key] = plugin

    def get_plugin(self, strategy_id: str) -> StrategyPlugin:
        key = str(strategy_id or "").strip().lower()
        plugin = self._plugins.get(key)
        if plugin is None:
            raise KeyError(f"Unknown strategy: {strategy_id}")
        return plugin

    def list_strategy_ids(self) -> list[str]:
        return sorted(self._plugins.keys())

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _upsert_warning(row: dict[str, Any], code: str) -> None:
        warnings = row.get("validation_warnings") if isinstance(row.get("validation_warnings"), list) else []
        if code not in warnings:
            warnings.append(code)
        row["validation_warnings"] = warnings

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _build_input_snapshot(
        snapshot: dict[str, Any] | None,
        *,
        data_source_mode: str = "live",
    ) -> dict[str, Any] | None:
        data = snapshot if isinstance(snapshot, dict) else None
        if not data:
            return None

        symbol = str(data.get("symbol") or "").upper()
        expiration = str(data.get("expiration") or "").strip()
        dte = StrategyService._to_float(data.get("dte"))
        contracts = data.get("contracts") if isinstance(data.get("contracts"), list) else []
        closes = data.get("prices_history") if isinstance(data.get("prices_history"), list) else []

        # pricing_source reflects actual data source
        # Derived: data_source_mode → pricing_source label
        if data_source_mode == "snapshot":
            pricing_source = "snapshot+fred"
        else:
            pricing_source = "tradier+fred+polygon"

        return {
            "underlying_snapshot": {
                "symbol": symbol or None,
                "expiration": expiration or None,
                "underlying_price": StrategyService._to_float(data.get("underlying_price")),
                "vix": StrategyService._to_float(data.get("vix")),
                "dte": int(dte) if dte is not None and float(dte).is_integer() else dte,
            },
            "chain_metadata": {
                "contracts_count": len(contracts),
                "prices_history_points": len(closes),
                "has_prices_history": bool(closes),
            },
            "pricing_source": pricing_source,
            "data_source_mode": data_source_mode,
            "timestamp": StrategyService._utc_now_iso(),
        }

    @staticmethod
    def _snapshot_index(snapshots: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        index: dict[tuple[str, str], dict[str, Any]] = {}
        for snap in snapshots:
            if not isinstance(snap, dict):
                continue
            symbol = str(snap.get("symbol") or "").upper().strip()
            expiration = str(snap.get("expiration") or "").strip()
            if not symbol or not expiration:
                continue
            index[(symbol, expiration)] = snap
        return index

    @staticmethod
    def _trade_side_label(trade: dict[str, Any]) -> str | None:
        spread = str(trade.get("strategy_id") or trade.get("spread_type") or trade.get("strategy") or "").strip().lower()
        if not spread:
            return None
        if spread in {"put_credit_spread", "put_debit", "calendar_put_spread", "csp", "long_put"}:
            return "put"
        if spread in {"call_credit_spread", "call_debit", "calendar_call_spread", "covered_call", "long_call"}:
            return "call"
        if spread in {"iron_condor", "butterfly_debit", "calendar_spread", "income"}:
            return "multi"
        if "put" in spread:
            return "put"
        if "call" in spread:
            return "call"
        return None

    @staticmethod
    def _trade_width(trade: dict[str, Any]) -> float | None:
        width = StrategyService._to_float(trade.get("width"))
        if width is not None:
            return abs(width)

        short_strike = StrategyService._to_float(trade.get("short_strike"))
        long_strike = StrategyService._to_float(trade.get("long_strike"))
        if short_strike is not None and long_strike is not None:
            return abs(short_strike - long_strike)

        put_short = StrategyService._to_float(trade.get("put_short_strike"))
        put_long = StrategyService._to_float(trade.get("put_long_strike"))
        call_short = StrategyService._to_float(trade.get("call_short_strike"))
        call_long = StrategyService._to_float(trade.get("call_long_strike"))
        values = []
        if put_short is not None and put_long is not None:
            values.append(abs(put_short - put_long))
        if call_short is not None and call_long is not None:
            values.append(abs(call_short - call_long))
        if values:
            return max(values)

        return None

    def _build_minimal_snapshot_from_trade(self, trade: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(trade, dict):
            return None

        symbol = str(trade.get("underlying") or trade.get("underlying_symbol") or trade.get("symbol") or "").upper().strip()
        expiration = str(trade.get("expiration") or "").strip()
        if not symbol or not expiration:
            return None

        dte = self._to_float(trade.get("dte"))
        contracts_multiplier = self._to_float(trade.get("contractsMultiplier") or trade.get("contracts_multiplier"))
        side = self._trade_side_label(trade)
        width = self._trade_width(trade)

        return {
            "underlying_snapshot": {
                "symbol": symbol,
                "expiration": expiration,
                "underlying_price": self._to_float(trade.get("underlying_price") or trade.get("price")),
                "vix": self._to_float(trade.get("vix")),
                "dte": int(dte) if dte is not None and float(dte).is_integer() else dte,
            },
            "trade_context": {
                "side": side,
                "short_strike": self._to_float(trade.get("short_strike")),
                "long_strike": self._to_float(trade.get("long_strike")),
                "put_short_strike": self._to_float(trade.get("put_short_strike")),
                "put_long_strike": self._to_float(trade.get("put_long_strike")),
                "call_short_strike": self._to_float(trade.get("call_short_strike")),
                "call_long_strike": self._to_float(trade.get("call_long_strike")),
                "strike": self._to_float(trade.get("strike")),
                "center_strike": self._to_float(trade.get("center_strike")),
                "lower_strike": self._to_float(trade.get("lower_strike")),
                "upper_strike": self._to_float(trade.get("upper_strike")),
                "width": width,
                "net_credit": self._to_float(trade.get("net_credit")),
                "net_debit": self._to_float(trade.get("net_debit")),
                "contracts_multiplier": int(contracts_multiplier) if contracts_multiplier is not None and float(contracts_multiplier).is_integer() else contracts_multiplier,
            },
            "chain_metadata": {
                "contracts_count": None,
                "prices_history_points": None,
                "has_prices_history": False,
                "reconstructed": True,
            },
            "pricing_source": "reconstructed_from_trade_output",
            "timestamp": self._utc_now_iso(),
        }

    def _resolve_trade_input_snapshot(
        self,
        trade: dict[str, Any],
        snapshot_index: dict[tuple[str, str], dict[str, Any]],
        *,
        data_source_mode: str = "live",
    ) -> tuple[dict[str, Any] | None, str | None]:
        if isinstance(trade.get("input_snapshot"), dict):
            return trade.get("input_snapshot"), "embedded"

        symbol = str(trade.get("underlying") or trade.get("underlying_symbol") or trade.get("symbol") or "").upper().strip()
        expiration = str(trade.get("expiration") or "").strip()
        if symbol and expiration:
            snap = snapshot_index.get((symbol, expiration))
            built = self._build_input_snapshot(snap, data_source_mode=data_source_mode)
            if built is not None:
                return built, "analysis_inputs"

        fallback = self._build_minimal_snapshot_from_trade(trade)
        if fallback is not None:
            return fallback, "reconstructed"

        return None, None

    def _attach_input_snapshots_to_trades(
        self,
        accepted: list[dict[str, Any]],
        snapshots: list[dict[str, Any]],
        *,
        data_source_mode: str = "live",
    ) -> None:
        if not accepted:
            return
        snapshot_index = self._snapshot_index(snapshots)
        for trade in accepted:
            if not isinstance(trade, dict):
                continue
            snapshot, source = self._resolve_trade_input_snapshot(
                trade, snapshot_index, data_source_mode=data_source_mode,
            )
            if snapshot is not None:
                trade["input_snapshot"] = snapshot
                trade["input_snapshot_source"] = source

    def _persist_data_workbench_records(
        self,
        *,
        report_id: str,
        strategy_id: str,
        accepted: list[dict[str, Any]],
        snapshots: list[dict[str, Any]],
    ) -> None:
        if not accepted:
            return

        snapshot_index = self._snapshot_index(snapshots)

        lines: list[str] = []
        for trade in accepted:
            if not isinstance(trade, dict):
                continue
            key = canonicalize_trade_key(trade.get("trade_key"))
            if not key:
                continue
            snapshot, snapshot_source = self._resolve_trade_input_snapshot(trade, snapshot_index)
            record = {
                "ts": self._utc_now_iso(),
                "record_type": "data_workbench_trade_record_v1",
                "report_id": report_id,
                "trade_key": key,
                "strategy_id": resolve_strategy_id_or_none(
                    trade.get("strategy_id") or trade.get("spread_type") or strategy_id
                ) or str(strategy_id or "").strip().lower(),
                "input_snapshot": snapshot,
                "input_snapshot_source": snapshot_source,
                "trade_output": trade,
                "validation_warnings": list(trade.get("validation_warnings") or []) if isinstance(trade.get("validation_warnings"), list) else [],
            }
            lines.append(json.dumps(record, ensure_ascii=False))

        if not lines:
            return

        self.data_workbench_records_path.parent.mkdir(parents=True, exist_ok=True)
        with self._records_lock:
            with open(self.data_workbench_records_path, "a", encoding="utf-8") as handle:
                for line in lines:
                    handle.write(line + "\n")

    # -- Scan-parameter presets (Strict / Conservative / Balanced / Wide) --
    # Gate groups for filter-trace categorisation.
    # Keys match rejection-reason strings emitted by strategy plugins.
    _GATE_GROUPS: dict[str, list[str]] = {
        "quote_validation": [
            "MISSING_QUOTES:short_bid", "MISSING_QUOTES:long_ask",
            "ASK_LT_BID:short_leg", "ASK_LT_BID:long_leg",
            # Centralised quote-validation codes (finer-grained)
            "QUOTE_INVALID:short_leg:missing_bid", "QUOTE_INVALID:short_leg:missing_ask",
            "QUOTE_INVALID:short_leg:negative_bid", "QUOTE_INVALID:short_leg:zero_or_negative_ask",
            "QUOTE_INVALID:short_leg:inverted_market", "QUOTE_INVALID:short_leg:zero_mid",
            "QUOTE_INVALID:long_leg:missing_bid", "QUOTE_INVALID:long_leg:missing_ask",
            "QUOTE_INVALID:long_leg:negative_bid", "QUOTE_INVALID:long_leg:zero_or_negative_ask",
            "QUOTE_INVALID:long_leg:inverted_market", "QUOTE_INVALID:long_leg:zero_mid",
            # Debit-spread quote-quality codes (pricing derived from suspect quotes)
            "QUOTE_REJECTED:debit_exceeds_width",
            # Iron condor: any leg quote missing/invalid → readiness=false
            "LEG_QUOTE_INCOMPLETE",
        ],
        "metrics_computation": ["CREDIT_SPREAD_METRICS_FAILED"],
        "probability": ["pop_below_floor", "DQ_MISSING:pop"],
        "expected_value": ["ev_to_risk_below_floor", "ev_negative"],
        "return_on_risk": ["ror_below_floor"],
        "spread_structure": [
            "invalid_width", "non_positive_credit", "credit_ge_width", "spread_too_wide",
            # debit-spread-specific structural codes
            "non_positive_debit", "debit_ge_width", "debit_too_close_to_width",
            # iron condor structural codes
            "credit_below_min", "condor_too_skewed", "distance_below_min_sigma",
            "invalid_condor_penny_wing", "short_leg_no_bid",
        ],
        "liquidity": ["open_interest_below_min", "volume_below_min"],
        "data_quality": [
            "DQ_MISSING:open_interest", "DQ_MISSING:volume",
            "DQ_ZERO:open_interest", "DQ_ZERO:volume",
        ],
    }

    # Payload keys that are NOT numeric filter thresholds.
    _FILTER_TRACE_SKIP_KEYS: frozenset[str] = frozenset({
        "symbols", "symbol", "expiration", "max_expirations_per_symbol",
        "preset", "direction", "moneyness", "center_mode", "butterfly_type",
        "option_side", "distance_mode", "allow_skewed",
        "prefer_term_structure", "data_quality_mode",
        "spread_type", "min_credit_for_dq_waiver", "min_debit_for_dq_waiver", "credit_price_basis",
    })

    # Per-level presets for each strategy.  All numeric evaluate thresholds
    # (min_pop, min_ev_to_risk, min_ror, liquidity floors, etc.) are set per
    # level so that strict is materially tighter than balanced in ≥3 dimensions.
    _PRESETS: dict[str, dict[str, dict[str, Any]]] = {
        "credit_spread": {
            "strict": {
                "dte_min": 14,
                "dte_max": 30,
                "expected_move_multiple": 1.2,
                "width_min": 3.0,
                "width_max": 5.0,
                "distance_min": 0.03,
                "distance_max": 0.08,
                "max_candidates": 200,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_pop": 0.70,
                "min_ev_to_risk": 0.03,
                "min_ror": 0.03,
                "max_bid_ask_spread_pct": 1.0,
                "min_open_interest": 1000,
                "min_volume": 100,
                "data_quality_mode": "strict",
            },
            "conservative": {
                "dte_min": 14,
                "dte_max": 30,
                "expected_move_multiple": 1.0,
                "width_min": 3.0,
                "width_max": 5.0,
                "distance_min": 0.03,
                "distance_max": 0.08,
                "max_candidates": 300,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_pop": 0.60,
                "min_ev_to_risk": 0.012,
                "min_ror": 0.01,
                "max_bid_ask_spread_pct": 1.5,
                "min_open_interest": 200,
                "min_volume": 10,
                "data_quality_mode": "balanced",
            },
            "balanced": {
                "dte_min": 7,
                "dte_max": 45,
                "expected_move_multiple": 1.0,
                "width_min": 1.0,
                "width_max": 5.0,
                "distance_min": 0.01,
                "distance_max": 0.12,
                "max_candidates": 400,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_pop": 0.55,
                "min_ev_to_risk": 0.008,
                "min_ror": 0.005,
                "max_bid_ask_spread_pct": 2.0,
                "min_open_interest": 100,
                "min_volume": 5,
                "data_quality_mode": "balanced",
            },
            "wide": {
                "dte_min": 3,
                "dte_max": 60,
                "expected_move_multiple": 0.8,
                "width_min": 1.0,
                "width_max": 10.0,
                "distance_min": 0.01,
                "distance_max": 0.15,
                "max_candidates": 800,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_pop": 0.45,
                "min_ev_to_risk": 0.005,
                "min_ror": 0.002,
                "max_bid_ask_spread_pct": 3.0,
                "min_open_interest": 25,
                "min_volume": 1,
                "data_quality_mode": "lenient",
            },
        },
        "debit_spreads": {
            "strict": {
                "dte_min": 14,
                "dte_max": 30,
                "width_min": 2.0,
                "width_max": 5.0,
                "max_candidates": 200,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "max_debit_pct_width": 0.40,
                "max_iv_rv_ratio_for_buying": 0.90,
                "min_pop": 0.65,
                "min_ev_to_risk": 0.03,
                "max_bid_ask_spread_pct": 1.0,
                "min_open_interest": 1000,
                "min_volume": 100,
                "data_quality_mode": "strict",
            },
            "conservative": {
                "dte_min": 14,
                "dte_max": 45,
                "width_min": 2.0,
                "width_max": 5.0,
                "max_candidates": 300,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "max_debit_pct_width": 0.45,
                "max_iv_rv_ratio_for_buying": 1.0,
                "min_pop": 0.55,
                "min_ev_to_risk": 0.015,
                "max_bid_ask_spread_pct": 1.5,
                "min_open_interest": 300,
                "min_volume": 20,
                "data_quality_mode": "balanced",
            },
            "balanced": {
                "dte_min": 7,
                "dte_max": 45,
                "width_min": 1.0,
                "width_max": 10.0,
                "max_candidates": 400,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "max_debit_pct_width": 0.50,
                "max_iv_rv_ratio_for_buying": 1.10,
                "min_pop": 0.50,
                "min_ev_to_risk": 0.01,
                "max_bid_ask_spread_pct": 2.0,
                "min_open_interest": 100,
                "min_volume": 5,
                "data_quality_mode": "balanced",
            },
            "wide": {
                "dte_min": 3,
                "dte_max": 60,
                "width_min": 0.5,
                "width_max": 10.0,
                "max_candidates": 800,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "max_debit_pct_width": 0.65,
                "max_iv_rv_ratio_for_buying": 1.30,
                "min_pop": 0.40,
                "min_ev_to_risk": 0.005,
                "max_bid_ask_spread_pct": 3.0,
                "min_open_interest": 25,
                "min_volume": 1,
                "data_quality_mode": "lenient",
            },
        },
        "iron_condor": {
            "strict": {
                "dte_min": 21,
                "dte_max": 45,
                "distance_mode": "expected_move",
                "distance_target": 1.2,
                "min_sigma_distance": 1.2,
                "wing_width_put": 5.0,
                "wing_width_call": 5.0,
                "wing_width_max": 10.0,
                "allow_skewed": False,
                "symmetry_target": 0.80,
                "min_ror": 0.15,
                "min_credit": 0.15,
                "min_ev_to_risk": 0.05,
                "min_pop": 0.55,
                "max_candidates": 220,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_open_interest": 1000,
                "min_volume": 100,
                "data_quality_mode": "strict",
                # Penny-wing prechecks (candidate construction)
                "min_short_leg_mid": 0.10,
                "min_side_credit": 0.10,
            },
            "conservative": {
                "dte_min": 21,
                "dte_max": 45,
                "distance_mode": "expected_move",
                "distance_target": 1.1,
                "min_sigma_distance": 1.1,
                "wing_width_put": 5.0,
                "wing_width_call": 5.0,
                "wing_width_max": 10.0,
                "allow_skewed": False,
                "symmetry_target": 0.70,
                "min_ror": 0.12,
                "min_credit": 0.10,
                "min_ev_to_risk": 0.02,
                "min_pop": 0.50,
                "max_candidates": 220,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_open_interest": 500,
                "min_volume": 50,
                "data_quality_mode": "balanced",
                # Penny-wing prechecks (candidate construction)
                "min_short_leg_mid": 0.08,
                "min_side_credit": 0.08,
            },
            "balanced": {
                "dte_min": 14,
                "dte_max": 45,
                "distance_mode": "expected_move",
                "distance_target": 1.0,
                "min_sigma_distance": 1.0,
                "wing_width_put": 5.0,
                "wing_width_call": 5.0,
                "wing_width_max": 10.0,
                "allow_skewed": False,
                "symmetry_target": 0.55,
                "min_ror": 0.08,
                "min_credit": 0.10,
                "min_ev_to_risk": 0.00,
                "min_pop": 0.45,
                "max_candidates": 300,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                # Volume is optional in balanced — OI + quote quality
                # are the primary liquidity gates instead.
                "min_open_interest": 300,
                "min_volume": 0,
                "data_quality_mode": "balanced",
                # Penny-wing prechecks (candidate construction)
                "min_short_leg_mid": 0.05,
                "min_side_credit": 0.05,
            },
            "wide": {
                "dte_min": 14,
                "dte_max": 60,
                "distance_mode": "expected_move",
                "distance_target": 0.9,
                "min_sigma_distance": 0.9,
                "wing_width_put": 5.0,
                "wing_width_call": 5.0,
                "wing_width_max": 15.0,
                "allow_skewed": True,
                "symmetry_target": 0.40,
                "min_ror": 0.05,
                "min_credit": 0.05,
                "min_ev_to_risk": -0.05,
                "min_pop": 0.35,
                "max_candidates": 500,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_open_interest": 100,
                "min_volume": 0,
                "data_quality_mode": "lenient",
                "max_bid_ask_spread_pct": 2.0,
                # Penny-wing prechecks (candidate construction)
                # Wide is lenient but still rejects zero-value wings
                "min_short_leg_mid": 0.05,
                "min_side_credit": 0.03,
            },
        },
        # ── Butterfly presets ─────────────────────────────────────────
        # Covers debit_call_butterfly, debit_put_butterfly, iron_butterfly.
        # Iron butterflies are credit structures; debit butterflies are debit.
        "butterflies": {
            "strict": {
                "dte_min": 7,
                "dte_max": 21,
                "width_min": 2.0,
                "width_max": 10.0,
                "max_candidates": 200,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_open_interest": 1000,
                "min_volume": 100,
                "max_bid_ask_spread_pct": 1.0,
                "min_pop": 0.08,
                "min_ev_to_risk": 0.01,
                "min_expected_value": 0.0,
                "min_cost_efficiency": 2.0,
                "max_debit_pct_width": 0.35,
                "data_quality_mode": "strict",
            },
            "conservative": {
                "dte_min": 7,
                "dte_max": 30,
                "width_min": 2.0,
                "width_max": 10.0,
                "max_candidates": 260,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_open_interest": 500,
                "min_volume": 50,
                "max_bid_ask_spread_pct": 1.5,
                "min_pop": 0.06,
                "min_ev_to_risk": 0.005,
                "min_expected_value": 0.0,
                "min_cost_efficiency": 1.5,
                "max_debit_pct_width": 0.45,
                "data_quality_mode": "balanced",
            },
            "balanced": {
                "dte_min": 7,
                "dte_max": 45,
                "width_min": 1.0,
                "width_max": 15.0,
                "max_candidates": 400,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_open_interest": 300,
                "min_volume": 20,
                "max_bid_ask_spread_pct": 2.0,
                "min_pop": 0.04,
                "min_ev_to_risk": -0.01,
                "min_expected_value": -10.0,
                "min_cost_efficiency": 1.0,
                "max_debit_pct_width": 0.60,
                "data_quality_mode": "balanced",
            },
            "wide": {
                "dte_min": 3,
                "dte_max": 60,
                "width_min": 0.5,
                "width_max": 20.0,
                "max_candidates": 800,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_open_interest": 50,
                "min_volume": 5,
                "max_bid_ask_spread_pct": 3.0,
                "min_pop": 0.02,
                "min_ev_to_risk": -0.05,
                "min_expected_value": -50.0,
                "min_cost_efficiency": 0.5,
                "max_debit_pct_width": 0.80,
                "data_quality_mode": "lenient",
            },
        },
        # ── Calendar spread presets ──────────────────────────────────
        # Calendars currently lack full POP/EV modelling.
        # All presets set required_metrics_complete=True so that the
        # METRICS_NOT_IMPLEMENTED gate in evaluate() rejects every
        # candidate until a proper pricing model is built.
        "calendars": {
            "strict": {
                "near_dte_min": 7,
                "near_dte_max": 14,
                "far_dte_min": 30,
                "far_dte_max": 60,
                "dte_min": 7,
                "dte_max": 60,
                "max_candidates": 120,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_open_interest": 1000,
                "min_volume": 100,
                "max_bid_ask_spread_pct": 1.0,
                "required_metrics_complete": True,
                "data_quality_mode": "strict",
            },
            "conservative": {
                "near_dte_min": 7,
                "near_dte_max": 14,
                "far_dte_min": 28,
                "far_dte_max": 60,
                "dte_min": 7,
                "dte_max": 60,
                "max_candidates": 180,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_open_interest": 500,
                "min_volume": 50,
                "max_bid_ask_spread_pct": 1.5,
                "required_metrics_complete": True,
                "data_quality_mode": "balanced",
            },
            "balanced": {
                "near_dte_min": 7,
                "near_dte_max": 21,
                "far_dte_min": 21,
                "far_dte_max": 60,
                "dte_min": 7,
                "dte_max": 60,
                "max_candidates": 240,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_open_interest": 300,
                "min_volume": 20,
                "max_bid_ask_spread_pct": 2.0,
                "required_metrics_complete": True,
                "data_quality_mode": "balanced",
            },
            "wide": {
                "near_dte_min": 5,
                "near_dte_max": 28,
                "far_dte_min": 21,
                "far_dte_max": 90,
                "dte_min": 5,
                "dte_max": 90,
                "max_candidates": 400,
                "symbols": list(DEFAULT_SCANNER_SYMBOLS),
                "min_open_interest": 100,
                "min_volume": 5,
                "max_bid_ask_spread_pct": 3.0,
                "required_metrics_complete": True,
                "data_quality_mode": "lenient",
            },
        },
    }
    _DEFAULT_PRESET = "balanced"

    # ── Preset-based data requirements ──────────────────────────────
    # Wide preset relaxes price-history requirement so scans can proceed
    # when candles are missing.  All other presets treat missing price
    # history as fatal at snapshot-validation time.
    _PRICE_HISTORY_OPTIONAL_PRESETS: frozenset[str] = frozenset({"wide"})

    @classmethod
    def is_price_history_required(cls, preset_name: str) -> bool:
        """Whether missing price history should reject a snapshot.

        Returns ``False`` for the *wide* preset (scans proceed without
        candles); ``True`` for all other presets.
        """
        return str(preset_name).lower() not in cls._PRICE_HISTORY_OPTIONAL_PRESETS

    # ── Safety ceiling for raw candidate generation ───────────────────
    # Prevents runaway combinatorial explosions in build_candidates().
    # This is NOT the quality-based cap — that is resolved_thresholds.max_candidates,
    # applied centrally by select_top_n AFTER generation completes.
    # The generation cap is strategy-agnostic and must be >> any preset max_candidates.
    DEFAULT_MAX_GENERATED_CANDIDATES: int = 20_000

    @classmethod
    def resolve_thresholds(cls, strategy_id: str, preset_name: str | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return the complete resolved threshold dict for a strategy + preset.

        This is the **single source of truth** for filter thresholds.
        ``_apply_request_defaults`` delegates here for credit_spread presets.

        Parameters
        ----------
        strategy_id : str
        preset_name : str | None
            One of strict / conservative / balanced / wide.
            Falls back to ``_DEFAULT_PRESET`` if ``None`` or unknown.
        overrides : dict | None
            Explicit overrides that win over preset values.

        Returns
        -------
        dict[str, Any]
            Flat dict of threshold keys → resolved values.
        """
        presets = cls._PRESETS.get(strategy_id, {})
        name = str(preset_name or cls._DEFAULT_PRESET).lower()
        if name not in presets:
            logger.warning(
                "Unknown preset '%s' for strategy '%s'; falling back to '%s'",
                preset_name, strategy_id, cls._DEFAULT_PRESET,
            )
            name = cls._DEFAULT_PRESET
        base = dict(presets.get(name, {}))
        if overrides:
            base.update({k: v for k, v in overrides.items() if v is not None})
        return base

    def _apply_request_defaults(self, strategy_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = dict(payload or {})

        if strategy_id == "credit_spread":
            raw_preset = req.pop("preset", None)
            requested_preset = str(raw_preset).lower() if raw_preset else None
            # "manual" = user submitted custom advanced filters; don't resolve to a named preset
            if requested_preset == "manual":
                preset_name = "manual"
                logger.info("Preset requested='manual' — using caller-supplied thresholds, no preset overlay")
            else:
                preset_name = str(raw_preset or self._DEFAULT_PRESET).lower()
                # Validate preset name
                presets = self._PRESETS.get(strategy_id, {})
                if preset_name not in presets:
                    logger.warning(
                        "Unknown preset '%s' for strategy '%s'; falling back to '%s'",
                        preset_name, strategy_id, self._DEFAULT_PRESET,
                    )
                    preset_name = self._DEFAULT_PRESET

                preset_values = presets.get(preset_name, {})
                # If user passed singular "symbol", don't apply preset "symbols"
                user_specified_symbol = "symbol" in (payload or {}) or "symbols" in (payload or {})
                for k, v in preset_values.items():
                    if k == "symbols" and user_specified_symbol:
                        continue
                    req.setdefault(k, v)

            req["_preset_name"] = preset_name  # stamp resolved name for filter trace
            req["_requested_preset_name"] = requested_preset  # stamp raw requested name
            req["_requested_data_quality_mode"] = str(payload.get("data_quality_mode") or "").lower() or None

        elif strategy_id == "debit_spreads":
            raw_preset = req.pop("preset", None)
            requested_preset = str(raw_preset).lower() if raw_preset else None
            if requested_preset == "manual":
                preset_name = "manual"
                logger.info("Preset requested='manual' for debit_spreads — using caller-supplied thresholds")
            else:
                preset_name = str(raw_preset or self._DEFAULT_PRESET).lower()
                presets = self._PRESETS.get(strategy_id, {})
                if preset_name not in presets:
                    logger.warning(
                        "Unknown preset '%s' for strategy '%s'; falling back to '%s'",
                        preset_name, strategy_id, self._DEFAULT_PRESET,
                    )
                    preset_name = self._DEFAULT_PRESET

                preset_values = presets.get(preset_name, {})
                user_specified_symbol = "symbol" in (payload or {}) or "symbols" in (payload or {})
                for k, v in preset_values.items():
                    if k == "symbols" and user_specified_symbol:
                        continue
                    req.setdefault(k, v)

            req["_preset_name"] = preset_name
            req["_requested_preset_name"] = requested_preset
            req["_requested_data_quality_mode"] = str(payload.get("data_quality_mode") or "").lower() or None

        elif strategy_id == "iron_condor":
            raw_preset = req.pop("preset", None)
            requested_preset = str(raw_preset).lower() if raw_preset else None
            if requested_preset == "manual":
                preset_name = "manual"
                logger.info("Preset requested='manual' for iron_condor — using caller-supplied thresholds")
            else:
                preset_name = str(raw_preset or self._DEFAULT_PRESET).lower()
                presets = self._PRESETS.get(strategy_id, {})
                if preset_name not in presets:
                    logger.warning(
                        "Unknown preset '%s' for strategy '%s'; falling back to '%s'",
                        preset_name, strategy_id, self._DEFAULT_PRESET,
                    )
                    preset_name = self._DEFAULT_PRESET

                preset_values = presets.get(preset_name, {})
                user_specified_symbol = "symbol" in (payload or {}) or "symbols" in (payload or {})
                for k, v in preset_values.items():
                    if k == "symbols" and user_specified_symbol:
                        continue
                    req.setdefault(k, v)

            req["_preset_name"] = preset_name
            req["_requested_preset_name"] = requested_preset
            req["_requested_data_quality_mode"] = str(payload.get("data_quality_mode") or "").lower() or None

        elif strategy_id == "butterflies":
            raw_preset = req.pop("preset", None)
            requested_preset = str(raw_preset).lower() if raw_preset else None
            if requested_preset == "manual":
                preset_name = "manual"
                logger.info("Preset requested='manual' for butterflies — using caller-supplied thresholds")
            else:
                preset_name = str(raw_preset or self._DEFAULT_PRESET).lower()
                presets = self._PRESETS.get(strategy_id, {})
                if preset_name not in presets:
                    logger.warning(
                        "Unknown preset '%s' for strategy '%s'; falling back to '%s'",
                        preset_name, strategy_id, self._DEFAULT_PRESET,
                    )
                    preset_name = self._DEFAULT_PRESET

                preset_values = presets.get(preset_name, {})
                user_specified_symbol = "symbol" in (payload or {}) or "symbols" in (payload or {})
                for k, v in preset_values.items():
                    if k == "symbols" and user_specified_symbol:
                        continue
                    req.setdefault(k, v)

            req["_preset_name"] = preset_name
            req["_requested_preset_name"] = requested_preset
            req["_requested_data_quality_mode"] = str(payload.get("data_quality_mode") or "").lower() or None

        elif strategy_id == "calendars":
            raw_preset = req.pop("preset", None)
            requested_preset = str(raw_preset).lower() if raw_preset else None
            if requested_preset == "manual":
                preset_name = "manual"
                logger.info("Preset requested='manual' for calendars — using caller-supplied thresholds")
            else:
                preset_name = str(raw_preset or self._DEFAULT_PRESET).lower()
                presets = self._PRESETS.get(strategy_id, {})
                if preset_name not in presets:
                    logger.warning(
                        "Unknown preset '%s' for strategy '%s'; falling back to '%s'",
                        preset_name, strategy_id, self._DEFAULT_PRESET,
                    )
                    preset_name = self._DEFAULT_PRESET

                preset_values = presets.get(preset_name, {})
                user_specified_symbol = "symbol" in (payload or {}) or "symbols" in (payload or {})
                for k, v in preset_values.items():
                    if k == "symbols" and user_specified_symbol:
                        continue
                    req.setdefault(k, v)

            req["_preset_name"] = preset_name
            req["_requested_preset_name"] = requested_preset
            req["_requested_data_quality_mode"] = str(payload.get("data_quality_mode") or "").lower() or None

        elif strategy_id == "income":
            req.setdefault("dte_min", 14)
            req.setdefault("dte_max", 45)
            req.setdefault("delta_min", 0.20)
            req.setdefault("delta_max", 0.30)
            req.setdefault("min_annualized_yield", 0.10)
            req.setdefault("min_open_interest", 500)
            req.setdefault("min_volume", 50)

        return req

    async def _resolve_expirations(self, symbol: str, request_payload: dict[str, Any], strategy_id: str) -> list[str]:
        requested_expiration = str(request_payload.get("expiration") or "").strip()
        if requested_expiration:
            return [requested_expiration]

        if strategy_id == "credit_spread":
            default_min, default_max = 14, 30
        elif strategy_id == "debit_spreads":
            default_min, default_max = 14, 45
        elif strategy_id == "iron_condor":
            default_min, default_max = 21, 45
        elif strategy_id == "butterflies":
            default_min, default_max = 7, 21
        elif strategy_id == "calendars":
            default_min, default_max = 7, 60
        elif strategy_id == "income":
            default_min, default_max = 14, 45
        else:
            default_min, default_max = 7, 45
        dte_min = int(request_payload.get("dte_min") or default_min)
        dte_max = int(request_payload.get("dte_max") or default_max)
        if dte_max < dte_min:
            dte_min, dte_max = dte_max, dte_min

        # ── Snapshot mode: derive expirations from saved files ─────
        _chain_source = self.base_data_service.chain_source
        if isinstance(_chain_source, SnapshotChainSource):
            expirations = _chain_source.get_available_expirations(symbol)
            if not expirations:
                raise ValueError(f"No snapshot expirations for {symbol}")
            filtered = []
            for exp in expirations:
                try:
                    d = dte_ceil(exp)
                except Exception:
                    continue
                if dte_min <= d <= dte_max:
                    filtered.append((d, exp))
            filtered.sort(key=lambda r: r[0])
            default_max_exp = 12 if strategy_id == "calendars" else (8 if strategy_id == "income" else 4)
            max_exp = int(request_payload.get("max_expirations_per_symbol") or default_max_exp)
            values = [e for _, e in filtered[:max(1, max_exp)]]
            return values if values else [expirations[0]]

        # ── Live mode: fetch from Tradier ──────────────────────────
        expirations = await self.base_data_service.tradier_client.get_expirations(symbol)
        filtered: list[tuple[int, str]] = []
        for exp in expirations:
            text = str(exp).strip()
            if not text:
                continue
            try:
                dte = dte_ceil(text)
            except Exception:
                continue
            if dte_min <= dte <= dte_max:
                filtered.append((dte, text))

        filtered.sort(key=lambda row: row[0])
        default_max_exp = 12 if strategy_id == "calendars" else (8 if strategy_id == "income" else 4)
        max_exp = int(request_payload.get("max_expirations_per_symbol") or default_max_exp)
        values = [exp for _, exp in filtered[: max(1, max_exp)]]
        if values:
            return values

        clean = [str(x) for x in expirations if str(x).strip()]
        if not clean:
            raise ValueError(f"No expirations available for {symbol}")
        return [clean[0]]

    def _normalize_trade(self, strategy_id: str, expiration: str, trade: dict[str, Any]) -> dict[str, Any]:
        """Thin wrapper around the shared ``normalize_trade`` builder.

        Adds validation-event logging for alias mapping and non-canonical
        trade keys — side-effects that belong to the scanner service, not
        the shared normalizer.
        """
        # Capture pre-normalization state for validation-event logging.
        raw_spread_type = (
            trade.get("spread_type") or trade.get("strategy") or strategy_id
        )
        _, alias_mapped, provided_strategy = canonicalize_strategy_id(raw_spread_type)
        provided_key = str(trade.get("trade_key") or "").strip()

        # normalize_trade() now delegates to resolve_strategy_id_or_none
        # which emits STRATEGY_ALIAS_USED for aliases.
        normalized = normalize_trade(
            trade,
            strategy_id=strategy_id,
            expiration=expiration,
            derive_dte=True,
        )

        # Log legacy validation event for backward compat.
        spread_type = normalized.get("strategy_id") or strategy_id
        if alias_mapped:
            try:
                self.validation_events.append_event(
                    severity="warn",
                    code="TRADE_STRATEGY_ALIAS_MAPPED",
                    message="Scanner output strategy alias mapped to canonical strategy_id",
                    context={
                        "strategy_id": spread_type,
                        "provided_strategy": provided_strategy,
                    },
                )
            except Exception:
                pass

        tkey = normalized.get("trade_key") or ""
        if provided_key and tkey != provided_key:
            try:
                self.validation_events.append_event(
                    severity="warn",
                    code="TRADE_KEY_NON_CANONICAL",
                    message="Scanner trade_key was rewritten to canonical format",
                    context={
                        "strategy_id": spread_type,
                        "trade_key": tkey,
                        "provided_trade_key": provided_key,
                    },
                )
            except Exception:
                pass

        return normalized

    # ------------------------------------------------------------------
    # Near-miss analysis
    # ------------------------------------------------------------------

    # Reason codes that indicate truly broken data (not threshold failures).
    # Candidates with ONLY these codes are "garbage" — they score far from
    # passing and will sort to the bottom of the near-miss list.
    _STRUCTURAL_REASONS: frozenset[str] = frozenset({
        "invalid_width",
        # credit spread structural
        "non_positive_credit", "credit_ge_width",
        "CREDIT_SPREAD_METRICS_FAILED",
        # debit spread structural
        "non_positive_debit", "debit_ge_width", "debit_too_close_to_width",
    })

    def _build_near_miss(
        self,
        rejected_rows: list[tuple[dict[str, Any], list[str]]],
        payload: dict[str, Any],
        policy: dict[str, Any],
        limit: int = 20,
        plugin: StrategyPlugin | None = None,
    ) -> list[dict[str, Any]]:
        """Score rejected candidates by proximity to passing, return top *limit*.

        Scoring: for each threshold gate, compute ``(actual - threshold) / |threshold|``
        (positive = passing, negative = failing).  The "nearness score" is the
        *sum* of per-gate normalised shortfalls — higher is closer to passing.
        Quote-validation / structural rejections get a heavy penalty.

        Returns a list of dicts with full trade data + ``gate_deltas`` showing
        exactly how far each metric was from the threshold.
        """
        from app.services.ranking import safe_float

        # Resolve thresholds once (same logic as evaluate).
        def _resolve(key: str, fallback: float) -> float:
            v = safe_float(payload.get(key))
            if v is None:
                v = safe_float(policy.get(key))
            return v if v is not None else fallback

        min_pop = _resolve("min_pop", 0.60)
        min_ev_to_risk = _resolve("min_ev_to_risk", 0.02)
        min_ror = _resolve("min_ror", 0.01)
        max_spread_pct = _resolve("max_bid_ask_spread_pct", 1.5)
        min_oi = int(_resolve("min_open_interest", 300))
        min_vol = int(_resolve("min_volume", 20))

        scored: list[tuple[float, dict[str, Any]]] = []

        for row, reasons in rejected_rows:
            # -- Extract metrics -------------------------------------------------
            # Use `is not None` pattern to preserve valid 0.0 values.
            _pw = row.get("p_win_used")
            pop = safe_float(_pw if _pw is not None else row.get("pop_delta_approx"))
            _ev_s = row.get("ev_per_share")
            ev = safe_float(_ev_s if _ev_s is not None else row.get("expected_value"))
            ev_to_risk = safe_float(row.get("ev_to_risk"))
            ror = safe_float(row.get("return_on_risk"))
            net_credit = safe_float(row.get("net_credit"))
            spread_pct = safe_float(row.get("bid_ask_spread_pct"))
            width = safe_float(row.get("width"))
            oi_val = safe_float(row.get("open_interest"))
            vol_val = safe_float(row.get("volume"))
            _ml_s = row.get("max_loss_per_share")
            max_loss = safe_float(_ml_s if _ml_s is not None else row.get("max_loss"))

            # -- Compute per-gate deltas (actual - threshold) --------------------
            # Positive delta = passing; negative = failing by that amount.
            gate_deltas: dict[str, dict[str, Any]] = {}

            def _delta(name: str, actual: float | None, threshold: float, higher_is_better: bool = True) -> None:
                if actual is None:
                    gate_deltas[name] = {"actual": None, "threshold": threshold, "delta": None}
                    return
                d = (actual - threshold) if higher_is_better else (threshold - actual)
                gate_deltas[name] = {
                    "actual": round(actual, 6),
                    "threshold": round(threshold, 6),
                    "delta": round(d, 6),
                }

            _delta("ev_to_risk", ev_to_risk, min_ev_to_risk)
            _delta("ror", ror, min_ror)
            _delta("pop", pop, min_pop)
            # For spread_pct: actual is multiplied by 100 in evaluate, threshold is already %
            spread_pct_100 = (spread_pct * 100.0) if spread_pct is not None else None
            _delta("spread_pct", spread_pct_100, max_spread_pct, higher_is_better=False)
            _delta("open_interest", float(oi_val) if oi_val is not None else None, float(min_oi))
            _delta("volume", float(vol_val) if vol_val is not None else None, float(min_vol))

            # -- Nearness score: sum of normalised deltas -------------------------
            # Each gate contributes (delta / |threshold|) clipped to [-2, 1].
            # Structural / quote rejections get a heavy penalty (-10 each).
            nearness = 0.0
            for gname, gd in gate_deltas.items():
                d = gd["delta"]
                t = gd["threshold"]
                if d is None:
                    nearness -= 1.0  # missing metric = moderate penalty
                elif t != 0:
                    nearness += max(-2.0, min(1.0, d / abs(t)))
                else:
                    nearness += 1.0 if d >= 0 else -1.0

            # Penalty for quote/structural problems
            _quote_reasons = [r for r in reasons if r.startswith("QUOTE_INVALID:")
                              or r.startswith("MISSING_QUOTES:")
                              or r in self._STRUCTURAL_REASONS]
            nearness -= 10.0 * len(_quote_reasons)

            # Pre-extract per-leg quotes (preserve 0.0 — do NOT use `or`).
            # For strategies with legs[], extract bid/ask from the first two
            # legs generically (avoid hardcoded IC-specific field names).
            _sb_raw = row.get("_short_bid")
            _sa_raw = row.get("_short_ask")
            _lb_raw = row.get("_long_bid")
            _la_raw = row.get("_long_ask")

            # Fallback: if transient fields are absent, try canonical legs[]
            _nm_legs = row.get("legs")
            if isinstance(_nm_legs, list) and len(_nm_legs) >= 2:
                # Find sell/buy legs for bid/ask mapping
                for _nml in _nm_legs:
                    if not isinstance(_nml, dict):
                        continue
                    if _nml.get("side") == "sell" and _sb_raw is None:
                        _sb_raw = _nml.get("bid")
                        _sa_raw = _nml.get("ask")
                    elif _nml.get("side") == "buy" and _lb_raw is None:
                        _lb_raw = _nml.get("bid")
                        _la_raw = _nml.get("ask")

            # -- Build candidate entry -------------------------------------------
            entry: dict[str, Any] = {
                "symbol": str(row.get("underlying") or row.get("symbol") or ""),
                "expiration": str(row.get("expiration") or ""),
                "dte": row.get("dte"),
                "short_strike": row.get("short_strike"),
                "long_strike": row.get("long_strike"),
                "width": width,
                "spread_type": str(row.get("spread_type") or row.get("strategy") or ""),
                # Per-leg quotes — use `is not None` to preserve valid 0.0
                "short_bid": safe_float(_sb_raw if _sb_raw is not None else row.get("bid")),
                "short_ask": safe_float(_sa_raw if _sa_raw is not None else row.get("ask")),
                "long_bid": safe_float(_lb_raw),
                "long_ask": safe_float(_la_raw),
                "short_mid": None,
                "long_mid": None,
                # Credit & risk (credit-spread) / Debit & risk (debit-spread)
                "net_credit": net_credit,
                "credit_basis": row.get("_credit_basis"),
                "net_debit": safe_float(row.get("net_debit")),
                "debit_as_pct_of_width": safe_float(row.get("debit_as_pct_of_width")),
                "max_loss": max_loss,
                "spread_pct": spread_pct_100,
                # Key metrics
                "ev": ev,
                "ev_to_risk": ev_to_risk,
                "ror": ror,
                "pop": pop,
                # Liquidity
                "open_interest": row.get("open_interest"),
                "volume": row.get("volume"),
                # Scoring
                "nearness_score": round(nearness, 4),
                "gate_deltas": gate_deltas,
                "reasons": reasons,
                "reason_count": len(reasons),
                "primary_rejection_reason": reasons[0] if reasons else None,
            }

            # Compute mids if quotes available
            sb = entry["short_bid"]
            sa = entry["short_ask"]
            lb = entry["long_bid"]
            la = entry["long_ask"]
            if sb is not None and sa is not None:
                entry["short_mid"] = round((sb + sa) / 2.0, 4)
            if lb is not None and la is not None:
                entry["long_mid"] = round((lb + la) / 2.0, 4)

            # ── Strategy-specific near-miss fields ─────────────────────
            # Delegate to plugin.build_near_miss_entry() so that IC-specific
            # fields, sigma distances, etc. are added by the plugin — the
            # orchestrator has no hardcoded knowledge of any strategy's
            # internal diagnostic fields.
            if plugin is not None:
                entry = plugin.build_near_miss_entry(row, reasons, entry)

            scored.append((nearness, entry))

        # Sort by nearness (highest = closest to passing), take top N
        scored.sort(key=lambda x: x[0], reverse=True)
        result = [entry for _, entry in scored[:limit]]

        # ── Debug snapshot: top near-miss trades ──────────────────────────
        _nm_log_limit = min(3, len(result))
        for _nmi in range(_nm_log_limit):
            _nm = result[_nmi]
            logger.debug(
                "event=near_miss_snapshot rank=%d spread_type=%s symbol=%s "
                "expiration=%s short_bid=%s short_ask=%s long_bid=%s long_ask=%s "
                "short_mid=%s long_mid=%s net_credit=%s nearness=%s reasons=%s",
                _nmi + 1, _nm.get("spread_type"),
                _nm.get("symbol"), _nm.get("expiration"),
                _nm.get("short_bid"), _nm.get("short_ask"),
                _nm.get("long_bid"), _nm.get("long_ask"),
                _nm.get("short_mid"), _nm.get("long_mid"),
                _nm.get("net_credit"),
                _nm.get("nearness_score"),
                _nm.get("reasons"),
            )

        return result

    def _build_report_stats(self, trades: list[dict[str, Any]]) -> dict[str, Any]:
        scores = [self._to_float(t.get("rank_score") or t.get("composite_score")) for t in trades]
        scores = [s for s in scores if s is not None]
        pops = [self._to_float(t.get("p_win_used")) if t.get("p_win_used") is not None
                else self._to_float(t.get("pop_delta_approx")) for t in trades]
        pops = [p for p in pops if p is not None]
        rors = [self._to_float(t.get("return_on_risk")) for t in trades]
        rors = [r for r in rors if r is not None]

        def _avg(values: list[float]) -> float | None:
            return (sum(values) / len(values)) if values else None

        return {
            "total_candidates": len(trades),
            "accepted_trades": len(trades),
            "rejected_trades": 0,
            "acceptance_rate": 1.0 if trades else 0.0,
            "best_trade_score": max(scores) if scores else None,
            "worst_accepted_score": min(scores) if scores else None,
            "avg_trade_score": _avg(scores),
            "avg_probability": _avg(pops),
            "avg_return_on_risk": _avg(rors),
            "best_underlying": str((trades[0].get("underlying") if trades else "") or "").upper() or None,
        }

    @staticmethod
    def _normalize_rank_100(value: Any) -> float:
        """Clamp *value* to [0, 100].

        rank_score is canonical 0–100 (produced by compute_rank_score).
        External scores (e.g. signal composite) may still arrive as 0–1;
        the <=1.0 heuristic handles that legacy path.
        """
        try:
            n = float(value)
        except (TypeError, ValueError):
            return 0.0
        if n <= 1.0:
            n *= 100.0
        return max(0.0, min(100.0, n))

    @staticmethod
    def _regime_fit_from_playbook(strategy: str, regime_payload: dict[str, Any] | None) -> float:
        payload = regime_payload if isinstance(regime_payload, dict) else {}
        playbook = payload.get("suggested_playbook") if isinstance(payload.get("suggested_playbook"), dict) else {}
        primary = {str(x).lower() for x in (playbook.get("primary") or [])}
        avoid = {str(x).lower() for x in (playbook.get("avoid") or [])}
        key = str(strategy or "").lower()
        if key in primary:
            return 100.0
        if key in avoid:
            return 10.0

        label = str(payload.get("regime_label") or "NEUTRAL").upper()
        if label == "RISK_OFF" and ("credit_put" in key or "short_put" in key):
            return 15.0
        if label == "RISK_ON" and ("credit_put" in key or "covered_call" in key):
            return 90.0
        return 55.0

    async def _apply_context_scores(self, accepted: list[dict[str, Any]]) -> None:
        if not accepted:
            return

        regime_payload = None
        if self.regime_service and hasattr(self.regime_service, "get_regime"):
            try:
                regime_payload = await self.regime_service.get_regime()
            except Exception:
                regime_payload = None

        signal_scores: dict[str, float] = {}
        if self.signal_service and hasattr(self.signal_service, "get_symbol_signals"):
            symbols = sorted({str(t.get("underlying") or t.get("symbol") or "").upper() for t in accepted if str(t.get("underlying") or t.get("symbol") or "").strip()})
            for symbol in symbols:
                try:
                    payload = await self.signal_service.get_symbol_signals(symbol=symbol, range_key="6mo")
                    score = self._normalize_rank_100((payload.get("composite") or {}).get("score"))
                    signal_scores[symbol] = score
                except Exception:
                    signal_scores[symbol] = 50.0

        for trade in accepted:
            strategy = str(trade.get("spread_type") or trade.get("strategy") or "")
            symbol = str(trade.get("underlying") or trade.get("symbol") or "").upper()
            structure_score = self._normalize_rank_100(trade.get("rank_score") or trade.get("composite_score"))
            underlying_score = signal_scores.get(symbol, 50.0)
            regime_fit = self._regime_fit_from_playbook(strategy, regime_payload)

            blended = (0.60 * structure_score) + (0.20 * underlying_score) + (0.20 * regime_fit)
            trade["rank_score_raw"] = structure_score
            trade["rank_score"] = round(blended, 3)
            trade["rank_components"] = {
                "structure": round(structure_score, 3),
                "underlying_composite": round(underlying_score, 3),
                "regime_fit": round(regime_fit, 3),
                "blended": round(blended, 3),
            }

    async def _emit_progress(self, callback: Any, stage: str, message: str, details: dict[str, Any] | None = None) -> None:
        if callback is None:
            return
        payload = {
            "stage": stage,
            "message": message,
        }
        if isinstance(details, dict) and details:
            payload.update(details)
        try:
            result = callback(payload)
            if inspect.isawaitable(result):
                await result
        except Exception:
            pass

    def _build_report_blob(
        self,
        strategy_id: str,
        payload: dict[str, Any],
        symbol_list: list[str],
        primary: dict[str, Any] | None,
        candidates: list[dict[str, Any]],
        enriched: list[dict[str, Any]],
        accepted: list[dict[str, Any]],
        notes: list[str],
        *,
        generation_diagnostics: dict[str, Any] | None = None,
        filter_trace: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_health = self.base_data_service.get_source_health_snapshot()
        report_stats = self._build_report_stats(accepted)

        # -- report_status / report_warnings --
        report_warnings: list[str] = []
        if not accepted:
            report_warnings.append("No trades generated (all candidates filtered out or invalid quotes).")
        diag = generation_diagnostics if isinstance(generation_diagnostics, dict) else {}
        rej_bk = diag.get("rejection_breakdown") if isinstance(diag.get("rejection_breakdown"), dict) else {}
        if diag.get("closes_count", -1) == 0:
            report_warnings.append("Price history unavailable (closes=0). SMA/RSI/RV computations may be missing.")
        if diag.get("invalid_quote_count", 0) > 0:
            report_warnings.append(f"{diag['invalid_quote_count']} chain row(s) had ask < bid (invalid quotes).")
        if diag.get("invalid_spread_count", 0) > 0:
            report_warnings.append(f"{diag['invalid_spread_count']} candidate(s) failed net_credit vs width validation.")

        report_status = "ok" if accepted else "empty"

        # Augment report_stats with candidate-level totals
        report_stats["total_candidates"] = len(candidates)
        report_stats["rejected_trades"] = len(enriched) - len(accepted)
        report_stats["acceptance_rate"] = (len(accepted) / len(enriched)) if enriched else 0.0
        report_stats["rejection_breakdown"] = rej_bk

        return {
            "strategyId": strategy_id,
            "generated_at": self._utc_now_iso(),
            "data_source_mode": (filter_trace or {}).get("data_source_mode", "live"),
            "report_status": report_status,
            "report_warnings": report_warnings,
            "symbol": (primary or {}).get("symbol") if isinstance(primary, dict) else (symbol_list[0] if symbol_list else None),
            "expiration": (primary or {}).get("expiration") if isinstance(primary, dict) else None,
            "symbols": symbol_list,
            "report_stats": report_stats,
            "source_health": source_health,
            "trades": accepted,
            "diagnostics": {
                "candidate_count": len(candidates),
                "enriched_count": len(enriched),
                "accepted_count": len(accepted),
                "closes_count": diag.get("closes_count"),
                "invalid_quote_count": diag.get("invalid_quote_count", 0),
                "invalid_spread_count": diag.get("invalid_spread_count", 0),
                "rejection_breakdown": rej_bk,
                "notes": list(dict.fromkeys([str(n) for n in notes if str(n).strip()])),
            },
            "filter_trace": filter_trace,
        }

    async def _quote_smoke_test(
        self,
        snapshots: list[dict[str, Any]],
        strategy_id: str,
    ) -> dict[str, Any]:
        """Run a single-contract quote smoke test against chain data.

        After snapshot collection and before candidate construction, this
        picks ONE known contract from the chain, inspects its chain-embedded
        bid/ask, then makes a DIRECT Tradier /markets/quotes call using the
        OCC option symbol.  The result is a structured diagnostic dict that
        is attached to the filter trace so we can pinpoint where quote data
        is lost.

        Returns a dict with:
          provider, quote_endpoint, request_params,
          contract_from_chain, chain_bid, chain_ask,
          quote_http_status, quote_error_message,
          response_body_snippet, parsed_bid, parsed_ask, parsed_last,
          chain_quote_summary, diagnosis, timestamp
        """
        from datetime import datetime, timezone

        result: dict[str, Any] = {
            "provider": "tradier",
            "quote_endpoint": "/markets/quotes",
            "strategy_id": strategy_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if not snapshots:
            result["diagnosis"] = "NO_SNAPSHOTS"
            result["diagnosis_detail"] = "No snapshots available to test"
            logger.warning("event=quote_smoke_test result=NO_SNAPSHOTS")
            return result

        # ── Pick a snapshot (prefer SPY, fall back to first) ───────────
        chosen_snap = snapshots[0]
        for snap in snapshots:
            if str(snap.get("symbol") or "").upper() == "SPY":
                chosen_snap = snap
                break

        contracts = chosen_snap.get("contracts") or []
        symbol = str(chosen_snap.get("symbol") or "").upper()
        expiration = str(chosen_snap.get("expiration") or "")

        result["snapshot_symbol"] = symbol
        result["snapshot_expiration"] = expiration
        result["snapshot_contracts_total"] = len(contracts)

        if not contracts:
            result["diagnosis"] = "EMPTY_CHAIN"
            result["diagnosis_detail"] = f"Chain for {symbol} {expiration} returned 0 contracts"
            logger.warning(
                "event=quote_smoke_test result=EMPTY_CHAIN symbol=%s expiration=%s",
                symbol, expiration,
            )
            return result

        # ── Chain-level bid/ask census ─────────────────────────────────
        _has_bid = 0
        _has_ask = 0
        _has_both = 0
        _has_occ = 0
        _zero_bid = 0
        _zero_ask = 0
        for c in contracts:
            bid = getattr(c, "bid", None)
            ask = getattr(c, "ask", None)
            occ = getattr(c, "symbol", None)
            if bid is not None:
                _has_bid += 1
                if bid == 0:
                    _zero_bid += 1
            if ask is not None:
                _has_ask += 1
                if ask == 0:
                    _zero_ask += 1
            if bid is not None and ask is not None:
                _has_both += 1
            if occ and len(str(occ)) > 10:
                _has_occ += 1

        chain_summary = {
            "total": len(contracts),
            "has_bid": _has_bid,
            "has_ask": _has_ask,
            "has_both": _has_both,
            "has_occ_symbol": _has_occ,
            "zero_bid": _zero_bid,
            "zero_ask": _zero_ask,
            "missing_bid": len(contracts) - _has_bid,
            "missing_ask": len(contracts) - _has_ask,
        }
        result["chain_quote_summary"] = chain_summary

        logger.info(
            "event=quote_smoke_test_chain_census symbol=%s expiration=%s "
            "total=%d has_both=%d missing_bid=%d missing_ask=%d has_occ=%d",
            symbol, expiration, len(contracts), _has_both,
            len(contracts) - _has_bid, len(contracts) - _has_ask, _has_occ,
        )

        # ── Pick two test contracts (first put, first call with OCC) ───
        test_put = None
        test_call = None
        for c in contracts:
            occ = getattr(c, "symbol", None)
            if not occ or len(str(occ)) <= 10:
                continue
            opt_type = str(getattr(c, "option_type", "")).lower()
            if opt_type == "put" and test_put is None:
                test_put = c
            elif opt_type == "call" and test_call is None:
                test_call = c
            if test_put and test_call:
                break

        probes = [x for x in [test_put, test_call] if x is not None]
        if not probes:
            result["diagnosis"] = "NO_OCC_SYMBOLS_IN_CHAIN"
            result["diagnosis_detail"] = (
                "Chain contracts exist but none have an OCC-format symbol. "
                "Cannot perform direct quote lookup."
            )
            logger.warning(
                "event=quote_smoke_test result=NO_OCC_SYMBOLS_IN_CHAIN symbol=%s",
                symbol,
            )
            return result

        # ── Log chain-embedded data for probe contracts ────────────────
        probe_details: list[dict[str, Any]] = []
        occ_symbols: list[str] = []
        for probe in probes:
            occ = str(getattr(probe, "symbol", ""))
            detail = {
                "occ_symbol": occ,
                "option_type": str(getattr(probe, "option_type", "")),
                "strike": getattr(probe, "strike", None),
                "expiration": str(getattr(probe, "expiration", "")),
                "chain_bid": getattr(probe, "bid", None),
                "chain_ask": getattr(probe, "ask", None),
                "chain_delta": getattr(probe, "delta", None),
                "chain_iv": getattr(probe, "iv", None),
                "chain_oi": getattr(probe, "open_interest", None),
                "chain_volume": getattr(probe, "volume", None),
            }
            probe_details.append(detail)
            occ_symbols.append(occ)
            logger.info(
                "event=quote_smoke_test_probe occ=%s type=%s strike=%s "
                "chain_bid=%s chain_ask=%s chain_delta=%s",
                occ, detail["option_type"], detail["strike"],
                detail["chain_bid"], detail["chain_ask"], detail["chain_delta"],
            )

        result["contract_probes"] = probe_details
        result["request_params"] = {"symbols": ",".join(occ_symbols)}

        # ── Direct Tradier quote lookup for OCC symbols ────────────────
        tradier_client = getattr(self.base_data_service, "tradier_client", None)
        if tradier_client is None:
            result["diagnosis"] = "NO_TRADIER_CLIENT"
            result["diagnosis_detail"] = "base_data_service has no tradier_client attribute"
            logger.error("event=quote_smoke_test result=NO_TRADIER_CLIENT")
            return result

        try:
            # Use the new option-aware quote method
            direct_quotes = await tradier_client.get_option_quotes(occ_symbols)
            result["quote_http_status"] = 200
            result["quote_error_message"] = None
        except Exception as exc:
            result["quote_http_status"] = getattr(exc, "status_code", None)
            result["quote_error_message"] = str(exc)
            result["diagnosis"] = "QUOTE_API_ERROR"
            result["diagnosis_detail"] = f"Tradier /markets/quotes failed: {exc}"
            result["response_body_snippet"] = str(getattr(exc, "details", {}))[:300]
            logger.error(
                "event=quote_smoke_test result=QUOTE_API_ERROR error=%s", exc,
            )
            return result

        result["response_body_snippet"] = str(direct_quotes)[:300]

        # ── Compare chain vs direct quote for each probe ───────────────
        quote_results: list[dict[str, Any]] = []
        any_success = False
        for detail in probe_details:
            occ = detail["occ_symbol"]
            dq = direct_quotes.get(occ) or {}
            qr: dict[str, Any] = {
                "occ_symbol": occ,
                "direct_bid": dq.get("bid"),
                "direct_ask": dq.get("ask"),
                "direct_last": dq.get("last"),
                "direct_quote_found": bool(dq),
                "chain_bid": detail["chain_bid"],
                "chain_ask": detail["chain_ask"],
            }

            # Determine per-contract diagnosis
            if dq and dq.get("bid") is not None and dq.get("ask") is not None:
                qr["match_status"] = "QUOTE_OK"
                any_success = True
            elif dq and (dq.get("bid") is None or dq.get("ask") is None):
                qr["match_status"] = "QUOTE_PARTIAL"
                qr["issue"] = (
                    "Direct quote returned but bid/ask partially null. "
                    f"bid={dq.get('bid')}, ask={dq.get('ask')}"
                )
            elif not dq:
                qr["match_status"] = "QUOTE_EMPTY"
                qr["issue"] = (
                    "Direct quote endpoint returned no data for this OCC symbol. "
                    "Possible causes: symbol format mismatch, contract not tradeable, "
                    "market closed, or sandbox limitations."
                )
            else:
                qr["match_status"] = "QUOTE_UNKNOWN"

            # Check chain data independently
            if detail["chain_bid"] is None and detail["chain_ask"] is None:
                qr["chain_status"] = "CHAIN_MISSING_BOTH"
            elif detail["chain_bid"] is None:
                qr["chain_status"] = "CHAIN_MISSING_BID"
            elif detail["chain_ask"] is None:
                qr["chain_status"] = "CHAIN_MISSING_ASK"
            else:
                qr["chain_status"] = "CHAIN_OK"

            quote_results.append(qr)

        result["direct_quote_results"] = quote_results

        # ── Overall diagnosis ──────────────────────────────────────────
        chain_ok = chain_summary["has_both"] > 0
        direct_ok = any_success

        if chain_ok and direct_ok:
            result["diagnosis"] = "PIPELINE_OK"
            result["diagnosis_detail"] = (
                f"Chain has {chain_summary['has_both']}/{chain_summary['total']} "
                f"contracts with bid+ask. Direct quote confirms data available."
            )
        elif chain_ok and not direct_ok:
            result["diagnosis"] = "CHAIN_OK_BUT_DIRECT_QUOTE_FAILED"
            result["diagnosis_detail"] = (
                "Chain data has bid/ask but direct /markets/quotes returned empty. "
                "This is expected — chain data IS the quote source for options. "
                "The scan pipeline should use chain-embedded bid/ask, not separate quote calls."
            )
        elif not chain_ok and direct_ok:
            result["diagnosis"] = "CHAIN_MISSING_QUOTES_BUT_DIRECT_OK"
            result["diagnosis_detail"] = (
                f"Chain has 0/{chain_summary['total']} contracts with bid+ask, "
                "but direct quote API has data. Possible normalize_chain bug."
            )
        else:
            result["diagnosis"] = "NO_QUOTE_DATA_ANYWHERE"
            result["diagnosis_detail"] = (
                f"Chain has 0/{chain_summary['total']} contracts with bid+ask "
                "AND direct quote API returned empty. Tradier may not have "
                "pricing data (market closed? sandbox? contract delisted?)."
            )

        logger.info(
            "event=quote_smoke_test result=%s chain_has_both=%d direct_ok=%s "
            "symbol=%s expiration=%s",
            result["diagnosis"], chain_summary["has_both"], direct_ok,
            symbol, expiration,
        )

        return result

    async def generate(self, strategy_id: str, request_payload: dict[str, Any] | None = None, progress_callback: Any | None = None) -> dict[str, Any]:
        plugin = self.get_plugin(strategy_id)
        payload = self._apply_request_defaults(strategy_id, request_payload or {})
        notes: list[str] = []

        # ── Resolve runtime data source mode ──────────────────────────
        # Priority: platform_settings (runtime toggle) > env OPTION_CHAIN_SOURCE
        # "stock" scanner is excluded — always uses live.
        _data_source_mode = "live"
        _snapshot_chain_source: SnapshotChainSource | None = None
        _snapshot_staleness_info: list[dict[str, Any]] = []

        if self.platform_settings is not None:
            _data_source_mode = self.platform_settings.data_source_mode
        elif isinstance(self.base_data_service.chain_source, SnapshotChainSource):
            _data_source_mode = "snapshot"

        # Stock scanner is excluded from snapshot mode
        if strategy_id == "stock":
            _data_source_mode = "live"

        if _data_source_mode == "snapshot" and self._snapshot_dir:
            from app.config import get_settings as _get_settings
            _snap_settings = _get_settings()
            _max_age = int(getattr(_snap_settings, "SNAPSHOT_MAX_AGE_HOURS", 48))
            _snapshot_chain_source = SnapshotChainSource(
                self._snapshot_dir,
                provider="tradier",
                max_age_hours=_max_age,
            )

        logger.info(
            "event=generate_data_source_mode strategy=%s mode=%s",
            strategy_id, _data_source_mode,
        )

        # ── Temporarily swap chain source for this run if snapshot mode ──
        _original_chain_source = self.base_data_service.chain_source
        if _snapshot_chain_source is not None:
            self.base_data_service.chain_source = _snapshot_chain_source

        try:
            return await self._generate_inner(
                strategy_id=strategy_id,
                plugin=plugin,
                payload=payload,
                notes=notes,
                progress_callback=progress_callback,
                data_source_mode=_data_source_mode,
                snapshot_chain_source=_snapshot_chain_source,
                snapshot_staleness_info=_snapshot_staleness_info,
            )
        finally:
            # Always restore original chain source
            self.base_data_service.chain_source = _original_chain_source

    async def _generate_inner(
        self,
        *,
        strategy_id: str,
        plugin: Any,
        payload: dict[str, Any],
        notes: list[str],
        progress_callback: Any | None,
        data_source_mode: str,
        snapshot_chain_source: SnapshotChainSource | None,
        snapshot_staleness_info: list[dict[str, Any]],
    ) -> dict[str, Any]:

        # Reset snapshot recorder for this run (fresh trace_id + counters)
        _recorder = self.base_data_service.snapshot_recorder
        if _recorder and _recorder.enabled:
            _recorder.reset_run()

        await self._emit_progress(progress_callback, "prepare", f"Preparing {strategy_id} inputs")

        symbols = payload.get("symbols") if isinstance(payload.get("symbols"), list) else None
        symbol_list = [str(x).upper() for x in (symbols or []) if str(x).strip()] or [str(payload.get("symbol") or "").upper().strip()]
        # If no symbols resolved from payload at all, use the full scanner universe
        if not symbol_list or symbol_list == [""]:
            symbol_list = list(DEFAULT_SCANNER_SYMBOLS)
        logger.info("[SCANNER] %s generate — symbols=%s mode=%s", strategy_id, symbol_list, data_source_mode)

        snapshots: list[dict[str, Any]] = []
        _captured_history_symbols: set[str] = set()

        # ── Preset-based price history requirement ────────────────────
        # Resolved once before the loop so the rule is centralized.
        _resolved_preset = str(payload.get("_preset_name") or self._DEFAULT_PRESET).lower()
        _price_history_required = self.is_price_history_required(_resolved_preset)

        # Per-symbol snapshot status for trace instrumentation
        _snapshot_symbol_details: list[str] = []

        for symbol in symbol_list:
            try:
                expirations = await self._resolve_expirations(symbol, payload, strategy_id)
            except Exception as exc:
                notes.append(f"{symbol}: expirations unavailable ({exc})")
                _snapshot_symbol_details.append(f"{symbol}: REJECTED (EXPIRATIONS_UNAVAILABLE)")
                continue

            for expiration in expirations:
                # ── Staleness check in snapshot mode ──────────────────
                if data_source_mode == "snapshot" and snapshot_chain_source is not None:
                    staleness = snapshot_chain_source.check_staleness(symbol, expiration)
                    if staleness.get("stale"):
                        warning_msg = staleness.get("warning") or f"Stale snapshot: {symbol} {expiration}"
                        notes.append(f"SNAPSHOT_STALE: {warning_msg}")
                        snapshot_staleness_info.append({
                            "symbol": symbol,
                            "expiration": expiration,
                            **staleness,
                        })
                        logger.warning(
                            "event=snapshot_stale symbol=%s expiration=%s age_seconds=%s max_hours=%s",
                            symbol, expiration,
                            staleness.get("age_seconds"),
                            staleness.get("max_age_hours"),
                        )
                        # In offline mode, do NOT fall back to live — skip
                        _snapshot_symbol_details.append(f"{symbol}/{expiration}: REJECTED (SNAPSHOT_STALE)")
                        continue
                    snapshot_staleness_info.append({
                        "symbol": symbol,
                        "expiration": expiration,
                        **staleness,
                    })

                try:
                    snapshot_inputs = await self.base_data_service.get_analysis_inputs(symbol, expiration)
                except Exception as exc:
                    notes.append(f"{symbol} {expiration}: analysis inputs unavailable ({exc})")
                    _snapshot_symbol_details.append(f"{symbol}/{expiration}: REJECTED (ANALYSIS_INPUTS_UNAVAILABLE)")
                    continue

                # ── In snapshot mode: load prices_history from disk ───
                if data_source_mode == "snapshot" and snapshot_chain_source is not None:
                    closes = snapshot_chain_source.get_prices_history(symbol)
                    if closes:
                        snapshot_inputs["prices_history"] = closes
                    elif not snapshot_inputs.get("prices_history"):
                        notes.append(f"{symbol}: prices_history unavailable in snapshot mode")

                    # ── Underlying price fallback ─────────────────────
                    if snapshot_inputs.get("underlying_price") is None:
                        fallback_price = snapshot_chain_source.get_underlying_price(symbol)
                        if fallback_price is not None:
                            snapshot_inputs["underlying_price"] = fallback_price
                            notes.append(f"{symbol}: underlying_price derived from snapshot chain data")
                        else:
                            notes.append(f"{symbol} {expiration}: underlying_price missing in snapshot — skipping")
                            logger.warning(
                                "event=snapshot_underlying_price_missing symbol=%s expiration=%s",
                                symbol, expiration,
                            )
                            _snapshot_symbol_details.append(f"{symbol}/{expiration}: REJECTED (MISSING_UNDERLYING_PRICE)")
                            continue

                contracts = snapshot_inputs.get("contracts") or []
                if not contracts:
                    notes.append(f"{symbol} {expiration}: no_chain")
                    _snapshot_symbol_details.append(f"{symbol}/{expiration}: REJECTED (NO_CHAIN)")
                    continue

                # ── Price-history validation (preset-dependent) ───────
                # Tier-1 checks above (underlying price, chain) are always
                # required.  Price history is Tier-2: required for all
                # presets EXCEPT wide.
                _closes = snapshot_inputs.get("prices_history") or []
                _has_valid_history = bool(_closes) and any(float(c) > 0 for c in _closes if c is not None)
                _history_flag = None
                if not _has_valid_history:
                    if _price_history_required:
                        notes.append(f"{symbol} {expiration}: prices_history missing — rejected (preset={_resolved_preset})")
                        logger.info(
                            "event=snapshot_rejected_missing_history symbol=%s expiration=%s preset=%s",
                            symbol, expiration, _resolved_preset,
                        )
                        _snapshot_symbol_details.append(f"{symbol}/{expiration}: REJECTED (MISSING_PRICE_HISTORY)")
                        continue
                    else:
                        # Wide preset: flag but accept
                        _history_flag = "MISSING_PRICE_HISTORY"
                        logger.info(
                            "event=snapshot_accepted_missing_history symbol=%s expiration=%s preset=%s",
                            symbol, expiration, _resolved_preset,
                        )

                # ── Capture prices_history alongside chain (live mode) ──
                if (
                    data_source_mode == "live"
                    and _recorder
                    and _recorder.enabled
                    and symbol not in _captured_history_symbols
                ):
                    closes_to_save = snapshot_inputs.get("prices_history") or []
                    if closes_to_save:
                        _recorder.save_prices_history(
                            closes_to_save,
                            provider="tradier",
                            symbol=symbol,
                        )
                        _captured_history_symbols.add(symbol)

                _snap_entry = {
                    **snapshot_inputs,
                    "symbol": symbol,
                    "expiration": expiration,
                    "dte": dte_ceil(expiration),
                }
                if _history_flag:
                    _snap_entry.setdefault("data_quality_flags", []).append(_history_flag)
                snapshots.append(_snap_entry)

                # Trace detail
                if _history_flag:
                    _snapshot_symbol_details.append(f"{symbol}/{expiration}: VALID ({_history_flag})")
                else:
                    _snapshot_symbol_details.append(f"{symbol}/{expiration}: VALID")

        await self._emit_progress(progress_callback, "snapshots", "Snapshots collected", {"count": len(snapshots)})

        # ── Quote smoke test (diagnostic: chain bid/ask + direct quote) ─
        # Runs once per generate() call to verify the quote pipeline is
        # functional.  Result is attached to the filter_trace.
        _quote_smoke: dict[str, Any] = {}
        if snapshots:
            try:
                _quote_smoke = await self._quote_smoke_test(snapshots, strategy_id)
            except Exception as exc:
                _quote_smoke = {
                    "diagnosis": "SMOKE_TEST_EXCEPTION",
                    "diagnosis_detail": str(exc),
                    "timestamp": self._utc_now_iso(),
                }
                logger.error("event=quote_smoke_test_error error=%s", exc)

            # ── Abort if pipeline is fundamentally broken ──────────────
            _diag = _quote_smoke.get("diagnosis") or ""
            if _diag in ("NO_QUOTE_DATA_ANYWHERE", "EMPTY_CHAIN", "QUOTE_API_ERROR"):
                notes.append(
                    f"QUOTE_PIPELINE_BROKEN: {_quote_smoke.get('diagnosis_detail', _diag)}. "
                    "Unable to fetch bid/ask for known chain contracts."
                )
                logger.error(
                    "event=quote_pipeline_broken diagnosis=%s detail=%s",
                    _diag, _quote_smoke.get("diagnosis_detail"),
                )

        candidates: list[dict[str, Any]] = []
        enriched: list[dict[str, Any]] = []
        accepted: list[dict[str, Any]] = []
        rejection_breakdown: dict[str, int] = {}
        _cap_summary: dict[str, Any] | None = None
        _capture_examples = bool(payload.get("_capture_trace_examples"))
        _rejected_examples: list[dict[str, Any]] = []
        _MAX_EXAMPLES = 3
        # For near-miss: always collect rejected rows (lightweight refs)
        # when we might need them.  Actual near-miss scoring happens after
        # the loop, only when accepted==0.
        _rejected_rows: list[tuple[dict[str, Any], list[str]]] = []
        _NEAR_MISS_MAX = 20

        if snapshots:
            primary = snapshots[0]
        else:
            primary = {
                "symbol": symbol_list[0] if symbol_list else None,
                "expiration": str(payload.get("expiration") or ""),
            }
            notes.append("No analysis snapshots available for requested symbols/expirations")

        policy = self.risk_policy_service.get_policy() if self.risk_policy_service else {}
        inputs = {
            **primary,
            "symbol": primary.get("symbol"),
            "expiration": primary.get("expiration"),
            "snapshots": snapshots,
            "symbols": symbol_list,
            "policy": policy,
        }
        inputs["request"] = payload

        # ── Initialize experiment/diagnostic vars (used in filter_trace) ──
        _bypass_soft_cap = False
        _bypass_reason: str | None = None
        _enrichment_cap = 0
        _enrich_elapsed = 0.0
        _gate_pass_counts: dict[str, int] = {}

        if snapshots:
            await self._emit_progress(progress_callback, "build_candidates", "Building candidates")

            # ── Pass generation_cap into inputs for plugin builders ────
            # Plugins use this as a safety ceiling, NOT the preset max_candidates.
            _generation_cap = int(
                payload.get("max_generated_candidates")
                or self.DEFAULT_MAX_GENERATED_CANDIDATES
            )
            inputs["_generation_cap"] = _generation_cap

            try:
                candidates = plugin.build_candidates(inputs)
            except Exception as exc:
                notes.append(f"build_candidates failed: {exc}")
                raise

            # ── Track how many the builder produced before any capping ──
            _generated_total = len(candidates)
            _generation_cap_reached = _generated_total >= _generation_cap

            # ── Soft cap: quality-based candidate selection ────────────
            # resolved_thresholds.max_candidates is the ONLY cap that
            # determines how many candidates reach enrichment.
            # Uses heap-based top-N by pre_score (chain-level data, no API calls).
            from app.utils.candidate_sampler import select_top_n

            _enrichment_cap = int(payload.get("max_candidates") or 400)

            # ── Experiment: bypass soft-cap for credit_spread + wide ──
            # Hard rule: credit_spread with preset "wide" bypasses the
            # enrichment cap so ALL candidates reach enrichment + gates.
            # Also activatable via payload flag or env var as fallback.
            _resolved_preset_for_bypass = str(payload.get("_preset_name") or "").lower()
            _bypass_soft_cap = False
            _bypass_reason: str | None = None
            if strategy_id == "credit_spread":
                if _resolved_preset_for_bypass == "wide":
                    _bypass_soft_cap = True
                    _bypass_reason = "credit_spread_wide_experiment"
                elif payload.get("bypass_soft_cap_for_enrichment"):
                    _bypass_soft_cap = True
                    _bypass_reason = "payload_flag"
                else:
                    from app.config import get_settings as _get_settings
                    if _get_settings().CREDIT_SPREAD_BYPASS_SOFT_CAP:
                        _bypass_soft_cap = True
                        _bypass_reason = "env_var"

            candidates, _cap_summary = select_top_n(
                candidates,
                _enrichment_cap,
                generation_cap=_generation_cap,
                bypass_enrichment_cap=_bypass_soft_cap,
                bypass_reason=_bypass_reason,
            )

            # ── Enrichment timing ─────────────────────────────────────
            _enrich_start = time.monotonic()

            await self._emit_progress(progress_callback, "enrich", "Enriching candidates", {"count": len(candidates)})
            try:
                enriched = plugin.enrich(candidates, inputs)
            except Exception as exc:
                notes.append(f"enrich failed: {exc}")
                raise

            _enrich_elapsed = time.monotonic() - _enrich_start

            await self._emit_progress(progress_callback, "evaluate", "Evaluating and scoring candidates", {"count": len(enriched)})
            _MAX_REJECTION_LOGS = 20
            _rejection_log_count = 0
            for row in enriched:
                try:
                    row = dict(row)
                    row["_policy"] = policy
                    row["_request"] = payload

                    # ── Readiness guardrail (iron condor / multi-leg) ──────
                    # If enrichment flagged readiness=False (any leg quote
                    # missing/invalid), short-circuit with a clear rejection
                    # reason instead of letting evaluate() produce confusing
                    # gate failures like credit_below_min on None values.
                    if row.get("readiness") is False:
                        ok = False
                        reasons = ["LEG_QUOTE_INCOMPLETE"]
                    else:
                        _gate_pass_counts["readiness_check"] = _gate_pass_counts.get("readiness_check", 0) + 1
                        ok, reasons = plugin.evaluate(row)
                    if not ok:
                        # -- Aggregate rejection reason counters --
                        for r in reasons:
                            rejection_breakdown[r] = rejection_breakdown.get(r, 0) + 1
                        # -- Capture rejected examples for filter trace --
                        if _capture_examples and len(_rejected_examples) < _MAX_EXAMPLES:
                            from app.services.ranking import safe_float as _sf
                            _rejected_examples.append({
                                "symbol": str(row.get("underlying") or row.get("symbol") or ""),
                                "expiration": str(row.get("expiration") or ""),
                                "short_strike": row.get("short_strike"),
                                "long_strike": row.get("long_strike"),
                                "width": row.get("width"),
                                "net_credit": row.get("net_credit"),
                                "pop": _sf(row.get("p_win_used") or row.get("pop_delta_approx")),
                                "ev_to_risk": _sf(row.get("ev_to_risk")),
                                "ror": _sf(row.get("return_on_risk")),
                                "open_interest": row.get("open_interest"),
                                "volume": row.get("volume"),
                                "reasons": reasons,
                                "primary_rejection_reason": reasons[0] if reasons else None,
                            })
                        # -- Collect for near-miss analysis (always) --
                        _rejected_rows.append((row, reasons))
                        # -- Structured debug log (first N only) --
                        if _rejection_log_count < _MAX_REJECTION_LOGS:
                            _rejection_log_count += 1
                            _is_ic_row = str(row.get("spread_type") or row.get("strategy") or "") == "iron_condor"
                            if _is_ic_row:
                                logger.info(
                                    "event=candidate_rejected strategy=%s symbol=%s expiration=%s "
                                    "short_put=%s long_put=%s short_call=%s long_call=%s "
                                    "width=%s net_credit=%s readiness=%s "
                                    "sp_bid=%s sp_ask=%s lp_bid=%s lp_ask=%s "
                                    "sc_bid=%s sc_ask=%s lc_bid=%s lc_ask=%s "
                                    "reasons=%s",
                                    strategy_id,
                                    row.get("underlying") or row.get("symbol"),
                                    row.get("expiration"),
                                    row.get("short_put_strike") or row.get("put_short_strike"),
                                    row.get("long_put_strike") or row.get("put_long_strike"),
                                    row.get("short_call_strike") or row.get("call_short_strike"),
                                    row.get("long_call_strike") or row.get("call_long_strike"),
                                    row.get("width"),
                                    row.get("net_credit"),
                                    row.get("readiness"),
                                    row.get("_short_put_bid"),
                                    row.get("_short_put_ask"),
                                    row.get("_long_put_bid"),
                                    row.get("_long_put_ask"),
                                    row.get("_short_call_bid"),
                                    row.get("_short_call_ask"),
                                    row.get("_long_call_bid"),
                                    row.get("_long_call_ask"),
                                    reasons,
                                )
                            else:
                                logger.info(
                                    "event=candidate_rejected strategy=%s symbol=%s expiration=%s "
                                    "short_strike=%s long_strike=%s width=%s net_credit=%s "
                                    "short_bid=%s short_ask=%s long_bid=%s long_ask=%s "
                                    "delta=%s reasons=%s",
                                    strategy_id,
                                    row.get("underlying") or row.get("symbol"),
                                    row.get("expiration"),
                                    row.get("short_strike"),
                                    row.get("long_strike"),
                                    row.get("width"),
                                    row.get("net_credit"),
                                    row.get("_short_bid") or row.get("bid"),
                                    row.get("_short_ask") or row.get("ask"),
                                    row.get("_long_bid"),
                                    row.get("_long_ask"),
                                    row.get("short_delta_abs"),
                                    reasons,
                                )
                        continue
                    # ── Track gate passes (all gates passed for this row) ──
                    _gate_pass_counts["all_gates_passed"] = _gate_pass_counts.get("all_gates_passed", 0) + 1
                    rank_score, tie_breaks = plugin.score(row)
                    # ── POP attribution invariant check ────────────────────
                    _pop_err = plugin.validate_pop_attribution(row)
                    if _pop_err:
                        logger.warning(
                            "event=pop_attribution_violation strategy=%s %s",
                            strategy_id, _pop_err,
                        )
                    row.pop("_policy", None)
                    row.pop("_request", None)
                    # Remove transient debug fields before persisting.
                    # Uses plugin.TRANSIENT_FIELDS so each strategy declares
                    # its own transient set — strategy_service has no hardcoded
                    # knowledge of IC-specific or debit-specific transient fields.
                    for _k in plugin.TRANSIENT_FIELDS:
                        row.pop(_k, None)
                    row["rank_score"] = rank_score
                    row["tie_breaks"] = tie_breaks
                    row["strategyId"] = strategy_id
                    row["selection_reasons"] = reasons
                    accepted.append(self._normalize_trade(strategy_id, str(row.get("expiration") or primary.get("expiration") or "NA"), row))
                except Exception as exc:
                    notes.append(f"candidate skipped: {exc}")
                    continue

            # ── Per-symbol candidate / accepted summary ──────────────
            _sym_candidates: dict[str, int] = {}
            _sym_accepted: dict[str, int] = {}
            for c in candidates:
                sym = str(c.get("underlying") or c.get("symbol") or "?").upper()
                _sym_candidates[sym] = _sym_candidates.get(sym, 0) + 1
            for a in accepted:
                sym = str(a.get("symbol") or a.get("underlying") or a.get("underlying_symbol") or "?").upper()
                _sym_accepted[sym] = _sym_accepted.get(sym, 0) + 1
            for sym in sorted(set(list(_sym_candidates.keys()) + list(_sym_accepted.keys()))):
                logger.info(
                    "[SCANNER] %s: %d candidates, %d accepted",
                    sym,
                    _sym_candidates.get(sym, 0),
                    _sym_accepted.get(sym, 0),
                )

            if rejection_breakdown:
                logger.info(
                    "event=rejection_summary strategy=%s total_rejected=%d breakdown=%s",
                    strategy_id,
                    sum(rejection_breakdown.values()),
                    rejection_breakdown,
                )

        # ── Near-miss analysis (only when accepted==0) ───────────────────────
        # Scores every rejected candidate by how close it was to passing all
        # gates, then returns the top _NEAR_MISS_MAX with full diagnostics so
        # the user can answer: "credit/EV wrong?  liquidity missing?  garbage?"
        _near_miss: list[dict[str, Any]] = []
        if not accepted and _rejected_rows:
            _near_miss = self._build_near_miss(
                _rejected_rows, payload, policy, _NEAR_MISS_MAX, plugin,
            )

        await self._apply_context_scores(accepted)

        accepted_pre_dedup = len(accepted)

        deduped: dict[str, dict[str, Any]] = {}
        for trade in accepted:
            key = str(trade.get("trade_key") or "").strip()
            if not key:
                continue
            current = deduped.get(key)
            if current is None or float(trade.get("rank_score") or 0.0) > float(current.get("rank_score") or 0.0):
                deduped[key] = trade
        accepted = list(deduped.values())

        accepted.sort(
            key=lambda tr: (
                float(tr.get("rank_score") or 0.0),
                float((tr.get("tie_breaks") or {}).get("edge") or (tr.get("tie_breaks") or {}).get("ev_to_risk") or 0.0),
                float((tr.get("tie_breaks") or {}).get("pop") or (tr.get("tie_breaks") or {}).get("liquidity") or 0.0),
                float((tr.get("tie_breaks") or {}).get("liq") or (tr.get("tie_breaks") or {}).get("conviction") or 0.0),
            ),
            reverse=True,
        )

        self._attach_input_snapshots_to_trades(
            accepted=accepted, snapshots=snapshots, data_source_mode=data_source_mode,
        )

        await self._emit_progress(progress_callback, "write_report", "Writing report", {"accepted_count": len(accepted)})

        # -- collect lightweight generation diagnostics --
        total_closes = sum(len(s.get("prices_history") or []) for s in snapshots)
        invalid_spread_notes = [n for n in notes if "net_credit" in n.lower() or "width" in n.lower()]
        generation_diagnostics: dict[str, Any] = {
            "closes_count": total_closes,
            "invalid_quote_count": sum(
                1 for s in snapshots
                for w in (s.get("warnings") or [])
                if "ask" in str(w).lower() and "bid" in str(w).lower()
            ),
            "invalid_spread_count": len(invalid_spread_notes),
            "rejection_breakdown": rejection_breakdown,
        }

        ts_name = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{strategy_id}_analysis_{ts_name}.json"
        path = self.results_dir / filename

        # ── Build filter trace ───────────────────────────────────
        total_contracts = sum(len(s.get("contracts") or []) for s in snapshots)

        # Resolve thresholds: extract numeric filter params from payload
        preset_name = str(payload.get("_preset_name") or self._DEFAULT_PRESET)
        resolved_thresholds: dict[str, Any] = {}
        for k, v in payload.items():
            if k.startswith("_") or k in self._FILTER_TRACE_SKIP_KEYS:
                continue
            if isinstance(v, (int, float)):
                resolved_thresholds[k] = v

        # Gate breakdown: categorize rejection reasons
        gate_breakdown: dict[str, int] = {}
        all_categorized: set[str] = set()
        for gate_name, reason_keys in self._GATE_GROUPS.items():
            count = sum(rejection_breakdown.get(k, 0) for k in reason_keys)
            if count > 0:
                gate_breakdown[gate_name] = count
            all_categorized.update(reason_keys)
        # Also categorize DQ_ZERO codes under data_quality gate
        for reason, cnt in rejection_breakdown.items():
            if reason.startswith("DQ_ZERO:") and reason not in all_categorized:
                gate_breakdown["data_quality"] = gate_breakdown.get("data_quality", 0) + cnt
                all_categorized.add(reason)
            # QUOTE_REJECTED:* codes → quote_validation (catch any not in the
            # explicit list above — future-proofs new quote-quality codes).
            if reason.startswith("QUOTE_REJECTED:") and reason not in all_categorized:
                gate_breakdown["quote_validation"] = gate_breakdown.get("quote_validation", 0) + cnt
                all_categorized.add(reason)
        uncategorized = sum(
            cnt for reason, cnt in rejection_breakdown.items()
            if reason not in all_categorized and cnt > 0
        )
        if uncategorized:
            gate_breakdown["other"] = uncategorized

        # Clarify semantics: counts are "trades that failed this gate at
        # least once" (a trade may appear in multiple groups).  Total may
        # exceed input_count because multiple gates can reject the same trade.
        gate_breakdown["_semantics"] = "trades_failed_at_least_once_per_gate"

        # Data quality flags
        data_quality_flags: list[str] = []
        if generation_diagnostics.get("closes_count", -1) == 0:
            data_quality_flags.append("MISSING_PRICE_HISTORY")
        iq = generation_diagnostics.get("invalid_quote_count", 0)
        if iq > 0:
            data_quality_flags.append(f"INVALID_QUOTES:{iq}")
        no_chain_count = sum(1 for n in notes if "no_chain" in n)
        if no_chain_count:
            data_quality_flags.append(f"NO_CHAIN_SYMBOLS:{no_chain_count}")
        # OI/volume data quality
        _dq_oi_total = rejection_breakdown.get("DQ_MISSING:open_interest", 0) + rejection_breakdown.get("DQ_ZERO:open_interest", 0)
        _dq_vol_total = rejection_breakdown.get("DQ_MISSING:volume", 0) + rejection_breakdown.get("DQ_ZERO:volume", 0)
        if _dq_oi_total > 0:
            data_quality_flags.append(f"MISSING_OR_ZERO_OI:{_dq_oi_total}")
        if _dq_vol_total > 0:
            data_quality_flags.append(f"MISSING_OR_ZERO_VOLUME:{_dq_vol_total}")
        # Quote data quality — aggregate from per-candidate QUOTE_INVALID rejections
        _dq_quote_total = sum(
            v for k, v in rejection_breakdown.items()
            if k.startswith("QUOTE_INVALID:")
        )
        if _dq_quote_total > 0:
            data_quality_flags.append(f"MISSING_OR_INVALID_QUOTES:{_dq_quote_total}")

        # ── Missing-field counts ──────────────────────────────────────────
        # Scan enriched rows for None bid/ask/OI/volume/POP/delta.
        # Distinguishes missing (None) from zero (0) for OI and volume.
        #
        # CRITICAL: For iron-condor (multi-leg) rows that carry a canonical
        # `legs[]` array, ALL bid/ask/delta counters are derived DIRECTLY
        # from legs[].bid / legs[].ask / legs[].delta.
        # This is the single source of truth — it cannot be affected by
        # transient-field stripping or by readiness-boolean inconsistencies.
        # bid=0 is VALID (not missing); only None counts as missing.
        _mfc_oi = 0          # open_interest is None
        _mfc_oi_zero = 0     # open_interest == 0
        _mfc_vol = 0         # volume is None
        _mfc_vol_zero = 0    # volume == 0
        _mfc_bid = 0         # any leg has bid==None
        _mfc_ask = 0         # any leg has ask==None
        _mfc_any_leg_quote_missing = 0   # any leg bid or ask is None
        _mfc_pop = 0         # p_win_used / pop_delta_approx is None
        _mfc_delta = 0       # any leg delta is None
        _mfc_quote_rejected = 0
        _mfc_dq_waived = 0
        for _row in enriched:
            if not isinstance(_row, dict):
                continue
            # OI / volume: distinguish None vs 0
            _raw_oi = _row.get("open_interest")
            _raw_vol = _row.get("volume")
            if _raw_oi is None:
                _mfc_oi += 1
            elif self._to_float(_raw_oi) == 0:
                _mfc_oi_zero += 1
            if _raw_vol is None:
                _mfc_vol += 1
            elif self._to_float(_raw_vol) == 0:
                _mfc_vol_zero += 1

            # ── Bid / Ask / Delta — canonical legs[] path ──────────────
            _ic_legs = _row.get("legs")
            if isinstance(_ic_legs, list) and len(_ic_legs) >= 2:
                # Multi-leg strategy (IC): read directly from legs[]
                _any_bid_missing = any(
                    isinstance(lg, dict) and lg.get("bid") is None
                    for lg in _ic_legs
                )
                _any_ask_missing = any(
                    isinstance(lg, dict) and lg.get("ask") is None
                    for lg in _ic_legs
                )
                if _any_bid_missing:
                    _mfc_bid += 1
                if _any_ask_missing:
                    _mfc_ask += 1
                if _any_bid_missing or _any_ask_missing:
                    _mfc_any_leg_quote_missing += 1
                # Delta: any leg missing delta
                _any_delta_missing = any(
                    isinstance(lg, dict) and lg.get("delta") is None
                    for lg in _ic_legs
                )
                if _any_delta_missing:
                    _mfc_delta += 1
            else:
                # 2-leg / legacy strategies: use transient fields
                _sb = _row.get("_short_bid")
                _sa = _row.get("_short_ask")
                _lb = _row.get("_long_bid")
                _la = _row.get("_long_ask")
                if _sb is None and _row.get("bid") is None:
                    _mfc_bid += 1
                if _la is None and _row.get("ask") is None:
                    _mfc_ask += 1
                if _sb is None or _sa is None or _lb is None or _la is None:
                    _mfc_any_leg_quote_missing += 1
                # Delta: check top-level delta fields
                _has_delta = (_row.get("delta") is not None
                              or _row.get("short_delta") is not None
                              or _row.get("short_delta_abs") is not None)
                if not _has_delta:
                    _mfc_delta += 1

            # POP: p_win_used or pop_delta_approx
            if _row.get("p_win_used") is None and _row.get("pop_delta_approx") is None:
                _mfc_pop += 1
            # Quote validation failure
            if _row.get("_quote_rejection"):
                _mfc_quote_rejected += 1
            elif any(
                str(c).startswith("QUOTE_REJECTED:")
                for c in (_row.get("_rejection_codes") or [])
            ):
                _mfc_quote_rejected += 1

        # Count DQ waived trades (in lenient mode, DQ_MISSING codes absent for
        # trades that would have been flagged but were waived).
        _dq_oi_rejected = rejection_breakdown.get("DQ_MISSING:open_interest", 0)
        _dq_vol_rejected = rejection_breakdown.get("DQ_MISSING:volume", 0)
        _dq_oi_zero_rejected = rejection_breakdown.get("DQ_ZERO:open_interest", 0)
        _dq_vol_zero_rejected = rejection_breakdown.get("DQ_ZERO:volume", 0)
        _dq_pop_rejected = rejection_breakdown.get("DQ_MISSING:pop", 0)
        _dq_mode_used = str(payload.get("data_quality_mode") or "").lower()
        if _dq_mode_used == "lenient":
            # Waived = had missing/zero fields but not rejected
            _mfc_dq_waived = (
                max(0, _mfc_oi - _dq_oi_rejected)
                + max(0, _mfc_vol - _dq_vol_rejected)
                + max(0, _mfc_oi_zero - _dq_oi_zero_rejected)
                + max(0, _mfc_vol_zero - _dq_vol_zero_rejected)
                + max(0, _mfc_pop - _dq_pop_rejected)
            )

        missing_field_counts: dict[str, int] = {
            "missing_open_interest": _mfc_oi,
            "zero_open_interest": _mfc_oi_zero,
            "missing_volume": _mfc_vol,
            "zero_volume": _mfc_vol_zero,
            "missing_bid": _mfc_bid,
            "missing_ask": _mfc_ask,
            "any_leg_quote_missing": _mfc_any_leg_quote_missing,
            "missing_pop": _mfc_pop,
            "missing_delta": _mfc_delta,
            "quote_rejected": _mfc_quote_rejected,
            "dq_waived": _mfc_dq_waived,
            "total_enriched": len(enriched),
        }

        # ── Counter-consistency guardrail ──────────────────────────────────
        # INVARIANT: if any_leg_quote_missing == 0, then missing_bid and
        # missing_ask must also be 0.  Log a WARNING if this is violated.
        if _mfc_any_leg_quote_missing == 0 and (_mfc_bid > 0 or _mfc_ask > 0):
            logger.warning(
                "event=counter_invariant_violation strategy=%s "
                "any_leg_quote_missing=0 but missing_bid=%d missing_ask=%d — "
                "this indicates a counter bug",
                strategy_id, _mfc_bid, _mfc_ask,
            )
        logger.info(
            "event=missing_field_counts strategy=%s total=%d "
            "missing_bid=%d missing_ask=%d any_leg_quote_missing=%d "
            "missing_delta=%d quote_rejected=%d",
            strategy_id,
            len(enriched), _mfc_bid, _mfc_ask,
            _mfc_any_leg_quote_missing, _mfc_delta, _mfc_quote_rejected,
        )
        # Debug snapshot: first enriched row's canonical legs[] for audit trail
        if enriched and isinstance(enriched[0], dict):
            _dbg = enriched[0]
            _dbg_legs = _dbg.get("legs") or []
            _dbg_leg_summary = [
                {k: lg.get(k) for k in ("name", "bid", "ask", "mid", "delta", "iv", "occ_symbol")}
                for lg in _dbg_legs if isinstance(lg, dict)
            ]
            logger.info(
                "event=enriched_row_snapshot strategy=%s "
                "readiness=%s net_credit=%s spread_bid=%s spread_ask=%s "
                "delta=%s short_delta_abs=%s "
                "leg_count=%d legs=%s",
                strategy_id,
                _dbg.get("readiness"),
                _dbg.get("net_credit"),
                _dbg.get("spread_bid"), _dbg.get("spread_ask"),
                _dbg.get("delta"), _dbg.get("short_delta_abs"),
                len(_dbg_legs),
                _dbg_leg_summary,
            )

        # ── Explicit DQ summary — answers "why 0 trades?" at a glance ──────
        # Scan pop_model_used for model breakdown
        _pop_model_counts: dict[str, int] = {}
        for _row in enriched:
            if not isinstance(_row, dict):
                continue
            _pm = _row.get("pop_model_used") or "NONE"
            _pop_model_counts[_pm] = _pop_model_counts.get(_pm, 0) + 1

        # Diagnostic trace counters (Task 5)
        _pop_fallback_count = sum(
            1 for _r in enriched if isinstance(_r, dict)
            and any("POP_FALLBACK_DELTA" in f for f in (_r.get("_dq_flags") or []))
        )
        _kelly_computed = sum(
            1 for _r in enriched if isinstance(_r, dict)
            and _r.get("kelly_fraction") is not None
        )
        _kelly_missing_count = sum(
            1 for _r in enriched if isinstance(_r, dict)
            and _r.get("kelly_fraction") is None
        )
        _iv_rank_missing_count = sum(
            1 for _r in enriched if isinstance(_r, dict)
            and _r.get("iv_rank") is None
        )
        # Readiness counters — from enriched rows' `readiness` field.
        # For IC this is set by the plugin; for 2-leg strategies it may not
        # exist (treat absent as "unknown", do not count).
        _readiness_true = sum(
            1 for _r in enriched if isinstance(_r, dict)
            and _r.get("readiness") is True
        )
        _readiness_false = sum(
            1 for _r in enriched if isinstance(_r, dict)
            and _r.get("readiness") is False
        )

        dq_summary: dict[str, Any] = {
            "missing_pop_count": _mfc_pop,
            "missing_delta_count": _mfc_delta,
            "zero_open_interest_count": _mfc_oi_zero,
            "zero_volume_count": _mfc_vol_zero,
            "quote_rejected_count": _mfc_quote_rejected,
            "any_leg_quote_missing_count": _mfc_any_leg_quote_missing,
            "dq_waived_count": _mfc_dq_waived,
            "pop_model_breakdown": _pop_model_counts,
            "pop_fallback_count": _pop_fallback_count,
            "kelly_computed": _kelly_computed,
            "kelly_missing_count": _kelly_missing_count,
            "iv_rank_missing_count": _iv_rank_missing_count,
            "readiness_true_count": _readiness_true,
            "readiness_false_count": _readiness_false,
        }

        # ── Quote/OI enrichment counters ────────────────────────────────────
        # Delegates to plugin.compute_enrichment_counters() so each strategy
        # declares how to derive counters from its own enriched output.
        # This eliminates strategy-specific field knowledge from the orchestrator.
        enrichment_counters: dict[str, int] = plugin.compute_enrichment_counters(enriched)
        # Supplement with OI/volume counters (universal, not strategy-specific)
        _eq_total = len(enriched)
        _eq_quote_rejected = sum(
            1 for _r in enriched if isinstance(_r, dict)
            and _r.get("_quote_rejection")
        )
        _eq_has_oi = sum(
            1 for _r in enriched if isinstance(_r, dict)
            and _r.get("open_interest") is not None
        )
        _eq_has_vol = sum(
            1 for _r in enriched if isinstance(_r, dict)
            and _r.get("volume") is not None
        )
        enrichment_counters.update({
            "quote_validation_rejected": _eq_quote_rejected,
            # Legacy aliases (backward compat)
            "quote_lookup_attempted": _eq_total,
            "quote_lookup_success": enrichment_counters.get("leg_quote_lookup_success", 0),
            "quote_lookup_failed": enrichment_counters.get("leg_quote_lookup_failed", 0),
            # OI counters (trade-level)
            "oi_lookup_attempted": _eq_total,
            "oi_lookup_success": _eq_has_oi,
            "oi_lookup_failed": _mfc_oi,
            # Volume counters
            "volume_lookup_attempted": _eq_total,
            "volume_lookup_success": _eq_has_vol,
            "volume_lookup_failed": _mfc_vol,
        })

        # Log enrichment counters summary
        logger.info(
            "event=enrichment_counters strategy=%s total=%d "
            "quote_success=%d quote_partial=%d quote_missing=%d "
            "spread_derived_success=%d spread_derived_failed=%d",
            strategy_id, _eq_total,
            enrichment_counters.get("leg_quote_lookup_success", 0),
            enrichment_counters.get("quote_lookup_partial", 0),
            enrichment_counters.get("quote_lookup_missing", 0),
            enrichment_counters.get("spread_quote_derived_success", 0),
            enrichment_counters.get("spread_quote_derived_failed", 0),
        )

        # ── Assertion guard: smoke-test vs counters consistency ────────────
        # If the quote_smoke_test's chain_quote_summary says all contracts
        # have bid+ask, but missing_field_counts says otherwise, flag it.
        # This catches normalisation bugs or stale-cache regressions.
        if _quote_smoke and isinstance(_quote_smoke, dict):
            _chain_summary = _quote_smoke.get("chain_quote_summary")
            if isinstance(_chain_summary, dict):
                _smoke_missing_bid = _chain_summary.get("missing_bid", -1)
                _smoke_missing_ask = _chain_summary.get("missing_ask", -1)
                # Smoke says all contracts have bid+ask BUT counters say
                # enriched candidates are missing them → data-pipeline gap.
                if (_smoke_missing_bid == 0 and _smoke_missing_ask == 0
                        and (_mfc_bid > 0 or _mfc_ask > 0)):
                    logger.warning(
                        "event=smoke_vs_counter_mismatch strategy=%s "
                        "smoke_missing_bid=%d smoke_missing_ask=%d "
                        "counter_missing_bid=%d counter_missing_ask=%d "
                        "counter_any_leg_quote_missing=%d — "
                        "chain_quote_summary says all contracts have bid+ask "
                        "but enriched-trade counters report missing. "
                        "Possible normalisation or __pycache__ issue.",
                        strategy_id, _smoke_missing_bid, _smoke_missing_ask,
                        _mfc_bid, _mfc_ask, _mfc_any_leg_quote_missing,
                    )
                # Smoke says all contracts have bid+ask AND counters ALSO
                # say no missing → but spread_quote_derived still 0?
                _eq_has_all_quotes = enrichment_counters.get("leg_quote_lookup_success", 0)
                _eq_spread_derived = enrichment_counters.get("spread_quote_derived_success", 0)
                if (_smoke_missing_bid == 0 and _smoke_missing_ask == 0
                        and _mfc_any_leg_quote_missing == 0
                        and _eq_has_all_quotes == _eq_total
                        and _eq_spread_derived == 0 and _eq_total > 0):
                    logger.warning(
                        "event=spread_derived_mismatch strategy=%s "
                        "all_legs_have_quotes=%d but spread_derived=%d — "
                        "likely net_credit not computed despite valid quotes. "
                        "Check readiness logic or __pycache__.",
                        strategy_id, _eq_has_all_quotes, _eq_spread_derived,
                    )

        filter_trace: dict[str, Any] = {
            "trace_id": f"{strategy_id}_{ts_name}_{uuid.uuid4().hex[:8]}",
            "timestamp": self._utc_now_iso(),
            "strategy_id": strategy_id,
            "data_source_mode": data_source_mode,
            "preset_name": preset_name,
            "requested_preset_name": payload.get("_requested_preset_name"),
            "data_quality_mode": _dq_mode_used or "balanced",
            "requested_data_quality_mode": payload.get("_requested_data_quality_mode"),
            "resolved_thresholds": resolved_thresholds,
            "stages": [
                {
                    "name": "snapshot_collection",
                    "label": "Snapshot Collection",
                    "input_count": len(symbol_list),
                    "output_count": len(snapshots),
                    "detail": f"{len(symbol_list)} symbols \u2192 {len(snapshots)} valid snapshots",
                    "price_history_required": _price_history_required,
                    "preset_name": _resolved_preset,
                    "per_symbol": _snapshot_symbol_details,
                },
                {
                    "name": "candidate_construction",
                    "label": "Candidate Construction",
                    "input_count": total_contracts,
                    "output_count": (_cap_summary or {}).get("generated_total", len(candidates)),
                    "detail": f"{total_contracts} contracts \u2192 {(_cap_summary or {}).get('generated_total', len(candidates))} spread candidates",
                    "sub_stages": inputs.get("_build_sub_stages"),
                },
                {
                    "name": "soft_cap",
                    "label": "Soft Cap (top-N by pre_score)",
                    "input_count": (_cap_summary or {}).get("generated_total", len(candidates)),
                    "output_count": len(candidates),
                    "detail": (
                        f"{(_cap_summary or {}).get('generated_total', len(candidates))} candidates "
                        f"\u2192 {len(candidates)} after soft cap"
                        + (" (BYPASSED)" if _bypass_soft_cap else "")
                    ),
                    "bypassed": _bypass_soft_cap,
                    "bypass_reason": _bypass_reason if _bypass_soft_cap else None,
                    "original_enrichment_cap": (_cap_summary or {}).get("original_enrichment_cap", _enrichment_cap),
                    "effective_enrichment_cap": (_cap_summary or {}).get("effective_enrichment_cap", len(candidates)),
                    "cap_summary": _cap_summary,
                },
                {
                    "name": "enrichment",
                    "label": "Enrichment",
                    "input_count": len(candidates),
                    "output_count": len(enriched),
                    "detail": f"{len(candidates)} candidates \u2192 {len(enriched)} enriched trades",
                    "elapsed_seconds": round(_enrich_elapsed, 3),
                    "elapsed_ms": round(_enrich_elapsed * 1000, 1),
                    "total_enriched": len(enriched),
                    "enrichment_mode": "cpu_local",
                    "batching": {
                        "note": "Enrichment is CPU-only (no external API calls); "
                                 "quotes come from pre-fetched chain data.",
                        "batch_count": 1,
                        "batch_size": len(enriched),
                        "max_in_flight": 1,
                    },
                },
                {
                    "name": "evaluate_gates",
                    "label": "Quality Gates",
                    "input_count": len(enriched),
                    "output_count": accepted_pre_dedup,
                    "detail": f"{len(enriched)} enriched \u2192 {accepted_pre_dedup} passed all gates",
                    "gate_pass_counts": _gate_pass_counts,
                },
                {
                    "name": "dedup_ranking",
                    "label": "Dedup & Ranking",
                    "input_count": accepted_pre_dedup,
                    "output_count": len(accepted),
                    "detail": f"{accepted_pre_dedup} \u2192 {len(accepted)} unique trades",
                },
            ],
            "gate_breakdown": gate_breakdown,
            "rejection_reasons": dict(rejection_breakdown),
            "data_quality_flags": data_quality_flags,
            "missing_field_counts": missing_field_counts,
            "enrichment_counters": enrichment_counters,
            "dq_summary": dq_summary,
            "experiment_flags": {
                "credit_spread_wide_bypass_soft_cap": _bypass_soft_cap,
                "bypass_reason": _bypass_reason,
            },
        }

        # ── Expected Fill trace ─────────────────────────────────────────────
        # Builds fill_model_summary, fill_impact, fill_samples from enriched
        # and accepted trades.  Additive-only — no existing fields affected.
        try:
            _fill_trace = build_fill_trace(enriched, accepted)
            filter_trace.update(_fill_trace)
        except Exception:
            logger.warning("event=fill_trace_build_failed", exc_info=True)

        if _capture_examples and _rejected_examples:
            filter_trace["rejected_examples"] = _rejected_examples
        if _near_miss:
            filter_trace["near_miss"] = _near_miss
        # Iron-condor leg-quote DQ fail samples (produced by IC enrich)
        _ic_dq = inputs.get("_ic_dq_fail_samples")
        if _ic_dq:
            filter_trace["dq_fail_samples"] = _ic_dq

        # Quote smoke test diagnostic (always attached when available)
        if _quote_smoke:
            filter_trace["quote_smoke_test"] = _quote_smoke

        # Snapshot-mode metadata
        if data_source_mode == "snapshot":
            filter_trace["pricing_source"] = "snapshot+fred"
            if snapshot_staleness_info:
                filter_trace["snapshot_staleness"] = snapshot_staleness_info
        else:
            filter_trace["pricing_source"] = "tradier+fred+polygon"

        blob = self._build_report_blob(
            strategy_id=strategy_id,
            payload=payload,
            symbol_list=symbol_list,
            primary=primary,
            candidates=candidates,
            enriched=enriched,
            accepted=accepted,
            notes=notes,
            generation_diagnostics=generation_diagnostics,
            filter_trace=filter_trace,
        )

        path.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        try:
            self._persist_data_workbench_records(
                report_id=filename,
                strategy_id=strategy_id,
                accepted=accepted,
                snapshots=snapshots,
            )
        except Exception:
            pass

        # Write snapshot index if capture is active
        if _recorder and _recorder.enabled:
            try:
                _recorder.write_index()
            except Exception:
                logger.warning("event=snapshot_index_write_failed", exc_info=True)

        await self._emit_progress(progress_callback, "completed", "Report generation completed", {"filename": filename})
        return {"filename": filename, **blob}

    def list_reports(self, strategy_id: str) -> list[str]:
        prefix = f"{strategy_id}_analysis_"
        files = [p.name for p in self.results_dir.glob(f"{strategy_id}_analysis_*.json") if p.name.startswith(prefix)]
        files.sort(reverse=True)
        return files

    def get_report(self, strategy_id: str, filename: str) -> dict[str, Any]:
        if not filename.startswith(f"{strategy_id}_analysis_") or not filename.endswith(".json"):
            raise ValueError("Invalid report filename")

        path = self.results_dir / filename
        if not path.exists():
            raise FileNotFoundError(filename)

        payload = validate_report_file(path, validation_events=self.validation_events, auto_delete=True)
        if payload is None:
            raise FileNotFoundError(filename)

        trades = payload.get("trades") if isinstance(payload, dict) else []
        if not isinstance(trades, list):
            trades = []

        normalized_trades = [self._normalize_trade(strategy_id, str(payload.get("expiration") or "NA"), t) for t in trades if isinstance(t, dict)]
        payload["trades"] = normalized_trades
        payload["report_stats"] = payload.get("report_stats") if isinstance(payload.get("report_stats"), dict) else self._build_report_stats(normalized_trades)
        payload["strategyId"] = strategy_id
        # Ensure report_status is always present
        if not payload.get("report_status"):
            payload["report_status"] = "ok" if normalized_trades else "empty"
        return payload
