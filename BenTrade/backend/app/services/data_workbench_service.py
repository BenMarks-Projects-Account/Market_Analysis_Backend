from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.validation_events import ValidationEventsService
from app.utils.computed_metrics import apply_metrics_contract
from app.utils.normalize import normalize_trade
from app.utils.trade_key import canonicalize_strategy_id, canonicalize_trade_key, normalize_strike, trade_key


_WORKBENCH_TYPE_ALIASES: dict[str, str] = {
    "credit_put_spread": "put_credit_spread",
    "put_credit_spread": "put_credit_spread",
    "put_credit": "put_credit_spread",
    "credit_call_spread": "call_credit_spread",
    "call_credit_spread": "call_credit_spread",
    "call_credit": "call_credit_spread",
}

_WORKBENCH_TYPE_VARIANTS: dict[str, tuple[str, ...]] = {
    "put_credit_spread": ("put_credit_spread", "put_credit", "credit_put_spread"),
    "call_credit_spread": ("call_credit_spread", "call_credit", "credit_call_spread"),
}


class DataWorkbenchService:
    def __init__(self, results_dir: Path, validation_events: ValidationEventsService | None = None) -> None:
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.validation_events = validation_events
        self.ledger_path = self.results_dir / "trade_ledger.jsonl"
        self.workbench_store_path = self.results_dir / "workbench_scenarios.json"
        self.records_path = self.results_dir / "data_workbench_records.jsonl"

    @staticmethod
    def _to_iso_timestamp(value: Any) -> str:
        text = str(value or "").strip()
        if text:
            return text
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_dte(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return "NA"
        try:
            num = float(raw)
            if num.is_integer():
                return str(int(num))
            return str(num)
        except Exception:
            return raw

    @staticmethod
    def _normalize_type(value: Any) -> str:
        raw = str(value or "").strip().lower()
        if not raw:
            return "NA"
        mapped = _WORKBENCH_TYPE_ALIASES.get(raw, raw)
        canonical, _alias_mapped, _provided = canonicalize_strategy_id(mapped)
        return canonical or mapped

    @classmethod
    def normalize_trade_key(cls, trade_key_value: str) -> str:
        raw = str(trade_key_value or "").strip()
        if not raw:
            return ""

        parts = [str(part).strip() for part in raw.split("|")]
        if len(parts) != 6:
            return canonicalize_trade_key(raw)

        symbol = str(parts[0] or "NA").upper()
        expiration = str(parts[1] or "NA")
        spread_type = cls._normalize_type(parts[2])
        short_strike = normalize_strike(parts[3])
        long_strike = normalize_strike(parts[4])
        dte = cls._normalize_dte(parts[5])
        return f"{symbol}|{expiration}|{spread_type}|{short_strike}|{long_strike}|{dte}"

    @classmethod
    def _type_variants(cls, spread_type: str) -> tuple[str, ...]:
        normalized = cls._normalize_type(spread_type)
        variants = _WORKBENCH_TYPE_VARIANTS.get(normalized)
        if variants:
            return variants
        return (normalized,)

    @classmethod
    def _attempted_keys(cls, trade_key_value: str) -> list[str]:
        original = str(trade_key_value or "").strip()
        normalized = cls.normalize_trade_key(original)

        attempted: list[str] = []

        def _push(value: str) -> None:
            text = str(value or "").strip()
            if text and text not in attempted:
                attempted.append(text)

        _push(normalized)
        _push(original)

        parts = normalized.split("|") if normalized else []
        if len(parts) == 6:
            symbol, expiration, spread_type, short_strike, long_strike, dte = parts
            for variant in cls._type_variants(spread_type):
                _push(f"{symbol}|{expiration}|{variant}|{short_strike}|{long_strike}|{dte}")

        return attempted

    @classmethod
    def _key_matches_attempted(cls, candidate_key: Any, attempted_keys: list[str]) -> bool:
        candidate = str(candidate_key or "").strip()
        if not candidate:
            return False
        normalized_candidate = cls.normalize_trade_key(candidate)
        return candidate in attempted_keys or normalized_candidate in attempted_keys

    @staticmethod
    def _normalize_trade_payload(trade_payload: dict[str, Any], *, expiration_hint: str | None = None) -> dict[str, Any]:
        """Normalize via the shared builder.  Produces the full canonical shape."""
        return normalize_trade(trade_payload, expiration=expiration_hint)

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _trade_side_label(cls, payload: dict[str, Any]) -> str | None:
        spread = str(payload.get("strategy_id") or payload.get("spread_type") or payload.get("strategy") or "").strip().lower()
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

    @classmethod
    def _trade_width(cls, payload: dict[str, Any]) -> float | None:
        width = cls._to_float(payload.get("width"))
        if width is not None:
            return abs(width)

        short_strike = cls._to_float(payload.get("short_strike"))
        long_strike = cls._to_float(payload.get("long_strike"))
        if short_strike is not None and long_strike is not None:
            return abs(short_strike - long_strike)

        put_short = cls._to_float(payload.get("put_short_strike"))
        put_long = cls._to_float(payload.get("put_long_strike"))
        call_short = cls._to_float(payload.get("call_short_strike"))
        call_long = cls._to_float(payload.get("call_long_strike"))
        values = []
        if put_short is not None and put_long is not None:
            values.append(abs(put_short - put_long))
        if call_short is not None and call_long is not None:
            values.append(abs(call_short - call_long))
        if values:
            return max(values)
        return None

    def _reconstruct_minimal_input_snapshot(self, raw_candidate: dict[str, Any], *, trade_key: str = "") -> dict[str, Any] | None:
        payload = raw_candidate if isinstance(raw_candidate, dict) else {}

        symbol = str(payload.get("underlying") or payload.get("underlying_symbol") or payload.get("symbol") or "").upper().strip()
        expiration = str(payload.get("expiration") or "").strip()

        if (not symbol or not expiration) and trade_key:
            parts = self.normalize_trade_key(trade_key).split("|")
            if len(parts) == 6:
                symbol = symbol or str(parts[0] or "").upper().strip()
                expiration = expiration or str(parts[1] or "").strip()

        if not symbol or not expiration:
            return None

        dte = self._to_float(payload.get("dte"))
        contracts_multiplier = self._to_float(payload.get("contractsMultiplier") or payload.get("contracts_multiplier"))

        return {
            "underlying_snapshot": {
                "symbol": symbol,
                "expiration": expiration,
                "underlying_price": self._to_float(payload.get("underlying_price") or payload.get("price")),
                "vix": self._to_float(payload.get("vix")),
                "dte": int(dte) if dte is not None and float(dte).is_integer() else dte,
            },
            "trade_context": {
                "side": self._trade_side_label(payload),
                "short_strike": self._to_float(payload.get("short_strike")),
                "long_strike": self._to_float(payload.get("long_strike")),
                "put_short_strike": self._to_float(payload.get("put_short_strike")),
                "put_long_strike": self._to_float(payload.get("put_long_strike")),
                "call_short_strike": self._to_float(payload.get("call_short_strike")),
                "call_long_strike": self._to_float(payload.get("call_long_strike")),
                "strike": self._to_float(payload.get("strike")),
                "center_strike": self._to_float(payload.get("center_strike")),
                "lower_strike": self._to_float(payload.get("lower_strike")),
                "upper_strike": self._to_float(payload.get("upper_strike")),
                "width": self._trade_width(payload),
                "net_credit": self._to_float(payload.get("net_credit")),
                "net_debit": self._to_float(payload.get("net_debit")),
                "contracts_multiplier": int(contracts_multiplier) if contracts_multiplier is not None and float(contracts_multiplier).is_integer() else contracts_multiplier,
            },
            "chain_metadata": {
                "contracts_count": None,
                "prices_history_points": None,
                "has_prices_history": False,
                "reconstructed": True,
            },
            "pricing_source": "reconstructed_from_raw_candidate",
            "timestamp": self._to_iso_timestamp(None),
        }

    @staticmethod
    def _collect_warnings(payload: dict[str, Any]) -> list[str]:
        warnings: list[str] = []
        for field in ("validation_warnings", "warnings"):
            value = payload.get(field)
            if isinstance(value, list):
                warnings.extend([str(item) for item in value if str(item).strip()])
            elif isinstance(value, str) and value.strip():
                warnings.append(value.strip())
        deduped: list[str] = []
        seen: set[str] = set()
        for item in warnings:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def _iter_scan_report_files(self) -> list[Path]:
        files: list[Path] = []
        files.extend(self.results_dir.glob("*_analysis_*.json"))
        files.extend(self.results_dir.glob("analysis_*.json"))

        deduped: dict[str, Path] = {}
        for path in files:
            deduped[path.name] = path

        return sorted(
            deduped.values(),
            key=lambda item: (
                item.name,
                item.stat().st_mtime if item.exists() else 0.0,
            ),
            reverse=True,
        )

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None

    def _read_workbench_items(self) -> list[dict[str, Any]]:
        if not self.workbench_store_path.exists():
            return []
        payload = self._read_json(self.workbench_store_path)
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _workbench_match(self, canonical_trade_key: str) -> dict[str, Any] | None:
        matched = [
            item for item in self._read_workbench_items()
            if canonicalize_trade_key(item.get("trade_key")) == canonical_trade_key
        ]
        if not matched:
            return None
        matched.sort(key=lambda row: str(row.get("created_at") or row.get("ts") or ""), reverse=True)
        latest = matched[0]
        return {
            "count": len(matched),
            "latest": latest,
        }

    def _find_in_latest_reports(self, attempted_keys: list[str]) -> dict[str, Any] | None:
        for report_path in self._iter_scan_report_files():
            payload = self._read_json(report_path)
            if payload is None:
                continue

            trades: list[dict[str, Any]] = []
            expiration_hint = None
            generated_at = None
            if isinstance(payload, dict):
                expiration_hint = str(payload.get("expiration") or "").strip() or None
                generated_at = str(payload.get("generated_at") or "").strip() or None
                rows = payload.get("trades")
                if isinstance(rows, list):
                    trades = [row for row in rows if isinstance(row, dict)]
            elif isinstance(payload, list):
                trades = [row for row in payload if isinstance(row, dict)]

            if not trades:
                continue

            for raw_trade in trades:
                normalized_trade = self._normalize_trade_payload(raw_trade, expiration_hint=expiration_hint)
                row_keys = [
                    raw_trade.get("trade_key"),
                    raw_trade.get("_trade_key"),
                    normalized_trade.get("trade_key"),
                ]
                if not any(self._key_matches_attempted(value, attempted_keys) for value in row_keys):
                    continue

                trade_json = {
                    "raw_candidate": dict(raw_trade),
                    "input_snapshot": raw_trade.get("input_snapshot") if isinstance(raw_trade.get("input_snapshot"), dict) else None,
                    "computed_metrics": normalized_trade.get("computed_metrics") or {},
                    "metrics_status": normalized_trade.get("metrics_status") or {"ready": False, "missing_fields": []},
                    "validation_warnings": self._collect_warnings(raw_trade),
                }

                return {
                    "trade_key": self.normalize_trade_key(normalized_trade.get("trade_key") or attempted_keys[0]),
                    "trade": normalized_trade,
                    "trade_json": trade_json,
                    "sources": {
                        "where_found": ["latest_scan_report"],
                        "report_id": report_path.name,
                    },
                    "timestamp": self._to_iso_timestamp(generated_at),
                }

        return None

    def _find_in_data_workbench_records(self, attempted_keys: list[str]) -> dict[str, Any] | None:
        if not self.records_path.exists():
            return None

        latest: dict[str, Any] | None = None
        try:
            with open(self.records_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except Exception:
                        continue
                    if not isinstance(row, dict):
                        continue
                    if not self._key_matches_attempted(row.get("trade_key"), attempted_keys):
                        continue
                    if latest is None or str(row.get("ts") or "") >= str(latest.get("ts") or ""):
                        latest = row
        except Exception:
            return None

        if latest is None:
            return None

        resolved_key = self.normalize_trade_key(latest.get("trade_key") or attempted_keys[0])
        trade_output = latest.get("trade_output") if isinstance(latest.get("trade_output"), dict) else {}
        normalized_trade = self._normalize_trade_payload(trade_output)
        normalized_trade["trade_key"] = resolved_key
        normalized_trade["trade_id"] = resolved_key

        input_snapshot = latest.get("input_snapshot") if isinstance(latest.get("input_snapshot"), dict) else None
        warnings = latest.get("validation_warnings") if isinstance(latest.get("validation_warnings"), list) else []

        return {
            "trade_key": resolved_key,
            "trade": normalized_trade,
            "trade_json": {
                "raw_candidate": trade_output,
                "input_snapshot": input_snapshot,
                "computed_metrics": normalized_trade.get("computed_metrics") or {},
                "metrics_status": normalized_trade.get("metrics_status") or {"ready": False, "missing_fields": []},
                "validation_warnings": warnings,
            },
            "sources": {
                "where_found": ["data_workbench_records"],
                "report_id": str(latest.get("report_id") or "") or None,
                "record_source": "data_workbench_records",
            },
            "timestamp": self._to_iso_timestamp(latest.get("ts")),
        }

    def _find_in_trade_ledger(self, attempted_keys: list[str]) -> dict[str, Any] | None:
        if not self.ledger_path.exists():
            return None

        latest: dict[str, Any] | None = None
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except Exception:
                        continue
                    if not isinstance(row, dict):
                        continue
                    if not self._key_matches_attempted(row.get("trade_key"), attempted_keys):
                        continue
                    if latest is None or str(row.get("ts") or "") >= str(latest.get("ts") or ""):
                        latest = row
        except Exception:
            return None

        if latest is None:
            return None

        resolved_key = self.normalize_trade_key(latest.get("trade_key") or attempted_keys[0])

        payload = latest.get("payload") if isinstance(latest.get("payload"), dict) else {}
        normalized_trade = self._normalize_trade_payload(payload)
        normalized_trade["trade_key"] = resolved_key
        normalized_trade["trade_id"] = resolved_key

        warnings = self._collect_warnings(payload)
        event_warnings = latest.get("warnings")
        if isinstance(event_warnings, list):
            warnings.extend([str(item) for item in event_warnings if str(item).strip()])
        deduped_warnings = list(dict.fromkeys(warnings))

        trade_json = {
            "raw_candidate": payload,
            "input_snapshot": payload.get("input_snapshot") if isinstance(payload.get("input_snapshot"), dict) else None,
            "computed_metrics": normalized_trade.get("computed_metrics") or {},
            "metrics_status": normalized_trade.get("metrics_status") or {"ready": False, "missing_fields": []},
            "validation_warnings": deduped_warnings,
            "ledger_event": {
                "event": latest.get("event"),
                "source": latest.get("source"),
                "ts": latest.get("ts"),
            },
        }

        return {
            "trade_key": resolved_key,
            "trade": normalized_trade,
            "trade_json": trade_json,
            "sources": {
                "where_found": ["trade_ledger"],
            },
            "timestamp": self._to_iso_timestamp(latest.get("ts")),
        }

    def _maybe_attach_workbench_source(self, record: dict[str, Any], *, canonical_trade_key: str) -> dict[str, Any]:
        workbench = self._workbench_match(canonical_trade_key)
        if not workbench:
            return record

        sources = record.get("sources") if isinstance(record.get("sources"), dict) else {}
        where_found = sources.get("where_found") if isinstance(sources.get("where_found"), list) else []
        if "workbench_store" not in where_found:
            where_found.append("workbench_store")
        sources["where_found"] = where_found
        sources["workbench_items"] = int(workbench.get("count") or 0)

        trade_json = record.get("trade_json") if isinstance(record.get("trade_json"), dict) else {}
        trade_json["workbench_artifact"] = workbench.get("latest") if isinstance(workbench.get("latest"), dict) else None

        record["sources"] = sources
        record["trade_json"] = trade_json
        return record

    def _handle_missing_input_snapshot(self, record: dict[str, Any]) -> dict[str, Any]:
        trade_json = record.get("trade_json") if isinstance(record.get("trade_json"), dict) else {}
        input_snapshot = trade_json.get("input_snapshot")
        if isinstance(input_snapshot, dict):
            return record

        raw_candidate = trade_json.get("raw_candidate") if isinstance(trade_json.get("raw_candidate"), dict) else {}
        reconstructed = self._reconstruct_minimal_input_snapshot(
            raw_candidate,
            trade_key=str(record.get("trade_key") or ""),
        )
        if reconstructed is None:
            fallback_trade = record.get("trade") if isinstance(record.get("trade"), dict) else {}
            reconstructed = self._reconstruct_minimal_input_snapshot(
                fallback_trade,
                trade_key=str(record.get("trade_key") or ""),
            )

        if isinstance(reconstructed, dict):
            trade_json["input_snapshot"] = reconstructed
            trade_json["input_snapshot_message"] = "input_snapshot reconstructed from raw candidate"
            record["trade_json"] = trade_json
            return record

        message = "input_snapshot is not available for this trade"
        trade_json["input_snapshot"] = None
        trade_json["input_snapshot_message"] = message

        warnings = trade_json.get("validation_warnings") if isinstance(trade_json.get("validation_warnings"), list) else []
        if "DATA_WORKBENCH_INPUT_SNAPSHOT_MISSING" not in warnings:
            warnings.append("DATA_WORKBENCH_INPUT_SNAPSHOT_MISSING")
        trade_json["validation_warnings"] = warnings

        if isinstance(self.validation_events, ValidationEventsService):
            try:
                self.validation_events.append_event(
                    severity="warn",
                    code="DATA_WORKBENCH_INPUT_SNAPSHOT_MISSING",
                    message="Data Workbench could not find input_snapshot for resolved trade",
                    context={
                        "trade_key": str(record.get("trade_key") or ""),
                        "sources": list((record.get("sources") or {}).get("where_found") or []),
                    },
                )
            except Exception:
                pass

        record["trade_json"] = trade_json
        return record

    def _closest_matches(self, normalized_key: str, limit: int = 10) -> list[str]:
        parts = normalized_key.split("|")
        if len(parts) != 6:
            return []
        symbol = parts[0]
        expiration = parts[1]
        prefix = f"{symbol}|{expiration}|"

        rows = self.search_recent(underlying=symbol, limit=200)
        matches: list[str] = []
        for row in rows:
            key = self.normalize_trade_key(row.get("trade_key"))
            if not key or not key.startswith(prefix):
                continue
            if key in matches:
                continue
            matches.append(key)
            if len(matches) >= limit:
                break
        return matches

    def resolve_trade_with_trace(self, trade_key_value: str) -> dict[str, Any]:
        original_key = str(trade_key_value or "").strip()
        normalized_key = self.normalize_trade_key(original_key)
        attempted_keys = self._attempted_keys(original_key)

        if original_key and normalized_key and original_key != normalized_key and isinstance(self.validation_events, ValidationEventsService):
            try:
                self.validation_events.append_event(
                    severity="warn",
                    code="TRADE_KEY_NON_CANONICAL",
                    message="Data Workbench request trade_key was normalized",
                    context={
                        "source": "admin_data_workbench",
                        "trade_key": normalized_key,
                        "provided_trade_key": original_key,
                    },
                )
            except Exception:
                pass

        record = self._find_in_data_workbench_records(attempted_keys)
        if record is None:
            record = self._find_in_latest_reports(attempted_keys)
        if record is None:
            record = self._find_in_trade_ledger(attempted_keys)

        if record is not None:
            canonical_trade_key = self.normalize_trade_key(record.get("trade_key") or normalized_key)
            record["trade_key"] = canonical_trade_key
            record = self._maybe_attach_workbench_source(record, canonical_trade_key=canonical_trade_key)
            record = self._handle_missing_input_snapshot(record)

        return {
            "original_key": original_key,
            "normalized_key": normalized_key,
            "attempted_keys": attempted_keys,
            "record": record,
            "closest_matches": self._closest_matches(normalized_key, limit=10),
        }

    def resolve_trade(self, trade_key_value: str) -> dict[str, Any] | None:
        return self.resolve_trade_with_trace(trade_key_value).get("record")

    def search_recent(self, *, underlying: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        target_underlying = str(underlying or "").strip().upper()
        safe_limit = max(1, min(int(limit), 200))

        entries: dict[str, dict[str, Any]] = {}

        for report_path in self._iter_scan_report_files():
            payload = self._read_json(report_path)
            if not isinstance(payload, dict):
                continue
            generated_at = self._to_iso_timestamp(payload.get("generated_at"))
            expiration_hint = str(payload.get("expiration") or "").strip() or None
            rows = payload.get("trades") if isinstance(payload.get("trades"), list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                trade = self._normalize_trade_payload(row, expiration_hint=expiration_hint)
                key = str(trade.get("trade_key") or "").strip()
                if not key:
                    continue
                if target_underlying and str(trade.get("underlying") or "").upper() != target_underlying:
                    continue
                existing = entries.get(key)
                candidate = {
                    "trade_key": key,
                    "timestamp": generated_at,
                    "source": "latest_scan_report",
                    "report_id": report_path.name,
                }
                if existing is None or str(candidate["timestamp"]) > str(existing.get("timestamp") or ""):
                    entries[key] = candidate

        if self.records_path.exists():
            try:
                with open(self.records_path, "r", encoding="utf-8") as handle:
                    for line in handle:
                        text = line.strip()
                        if not text:
                            continue
                        try:
                            row = json.loads(text)
                        except Exception:
                            continue
                        if not isinstance(row, dict):
                            continue
                        key = self.normalize_trade_key(row.get("trade_key"))
                        if not key:
                            continue
                        trade_output = row.get("trade_output") if isinstance(row.get("trade_output"), dict) else {}
                        symbol = str(
                            trade_output.get("underlying")
                            or trade_output.get("underlying_symbol")
                            or trade_output.get("symbol")
                            or ""
                        ).upper()
                        if target_underlying and symbol != target_underlying:
                            continue
                        candidate = {
                            "trade_key": key,
                            "timestamp": self._to_iso_timestamp(row.get("ts")),
                            "source": "data_workbench_records",
                            "report_id": str(row.get("report_id") or "") or None,
                        }
                        existing = entries.get(key)
                        if existing is None or str(candidate["timestamp"]) > str(existing.get("timestamp") or ""):
                            entries[key] = candidate
            except Exception:
                pass

        if self.ledger_path.exists():
            try:
                with open(self.ledger_path, "r", encoding="utf-8") as handle:
                    for line in handle:
                        text = line.strip()
                        if not text:
                            continue
                        try:
                            row = json.loads(text)
                        except Exception:
                            continue
                        if not isinstance(row, dict):
                            continue
                        key = canonicalize_trade_key(row.get("trade_key"))
                        if not key:
                            continue
                        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                        symbol = str(
                            payload.get("underlying")
                            or payload.get("underlying_symbol")
                            or payload.get("symbol")
                            or ""
                        ).upper()
                        if target_underlying and symbol != target_underlying:
                            continue
                        candidate = {
                            "trade_key": key,
                            "timestamp": self._to_iso_timestamp(row.get("ts")),
                            "source": "trade_ledger",
                        }
                        existing = entries.get(key)
                        if existing is None or str(candidate["timestamp"]) > str(existing.get("timestamp") or ""):
                            entries[key] = candidate
            except Exception:
                pass

        for item in self._read_workbench_items():
            key = canonicalize_trade_key(item.get("trade_key"))
            if not key:
                continue
            symbol = str(item.get("input", {}).get("symbol") or "").upper() if isinstance(item.get("input"), dict) else ""
            if target_underlying and symbol != target_underlying:
                continue
            candidate = {
                "trade_key": key,
                "timestamp": self._to_iso_timestamp(item.get("created_at") or item.get("ts")),
                "source": "workbench_store",
            }
            existing = entries.get(key)
            if existing is None or str(candidate["timestamp"]) > str(existing.get("timestamp") or ""):
                entries[key] = candidate

        rows = sorted(entries.values(), key=lambda row: str(row.get("timestamp") or ""), reverse=True)
        return rows[:safe_limit]
