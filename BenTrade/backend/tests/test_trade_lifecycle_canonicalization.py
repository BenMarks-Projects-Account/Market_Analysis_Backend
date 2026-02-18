from __future__ import annotations

import json

from app.services.trade_lifecycle_service import TradeLifecycleService


def test_lifecycle_canonicalizes_alias_strategy_and_trade_key_and_emits_events(tmp_path):
    svc = TradeLifecycleService(results_dir=tmp_path)

    svc.append_event(
        event="WATCHLIST",
        trade_key_value="QQQ|2026-02-23|credit_put_spread|565|560|7",
        source="scanner",
        payload={
            "underlying": "QQQ",
            "expiration": "2026-02-23",
            "spread_type": "credit_put_spread",
            "short_strike": 565,
            "long_strike": 560,
            "dte": 7,
        },
    )

    rows = svc.list_events()
    assert len(rows) == 1
    row = rows[0]
    assert row["trade_key"] == "QQQ|2026-02-23|put_credit_spread|565|560|7"
    assert row["payload"]["spread_type"] == "put_credit_spread"
    assert row["payload"]["strategy"] == "put_credit_spread"
    assert "TRADE_KEY_NON_CANONICAL" in (row.get("warnings") or [])
    assert "TRADE_STRATEGY_ALIAS_MAPPED" in (row.get("warnings") or [])

    events_path = tmp_path / "validation_events.jsonl"
    assert events_path.exists()

    codes = []
    with open(events_path, "r", encoding="utf-8") as handle:
        for raw in handle:
            text = raw.strip()
            if not text:
                continue
            payload = json.loads(text)
            if isinstance(payload, dict):
                codes.append(str(payload.get("code") or ""))

    assert "TRADE_KEY_NON_CANONICAL" in codes
    assert "TRADE_STRATEGY_ALIAS_MAPPED" in codes
