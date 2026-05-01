"""Unit tests for ``app.services.history_recorder``.

Focus: in-memory SQLite round-trip + null-session-maker no-op + the
strategy-aware helpers (tracking window math, hypothetical extraction,
id generation, reasoning truncation).

The end-to-end live test against the NAS database lives in
``scripts/`` / manual validation — these unit tests are hermetic.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Decision, MarketStateSnapshot
from app.services.history_recorder import (
    LLM_REASONING_MAX_CHARS,
    _build_flows_pillar_json,
    _business_days_between,
    _compute_tracking_ends_utc,
    _compute_tracking_window_days,
    _extract_hypothetical_options,
    _extract_hypothetical_stock,
    _extract_snapshot_fields,
    _truncate_reasoning,
    log_decision,
    log_market_snapshot,
    make_decision_id,
)


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def session_maker():
    """In-memory SQLite with full schema, for round-trip tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture
def sample_market_state() -> dict:
    return {
        "composite": {"market_state": "risk_on", "confidence": 0.72},
        "engines": {
            "breadth_participation": {"score": 61.0, "confidence": 80.0},
            "volatility_options": {"score": 55.0, "confidence": 75.0},
            "cross_asset_macro": {"score": 48.0, "confidence": 60.0},
            "flows_positioning": {
                "score": 65.0,
                "confidence": 70.0,
                "pillar_scores": {"etf_flows": 58.0, "options_positioning": 62.0},
                "detail_sections": {"strategy_bias": "bullish"},
                "supporting_metrics": [{"name": "put_call_ratio", "value": 0.82}],
                "contradiction_flags": [],
                "source_status": "ok",
            },
            "liquidity_financial_conditions": {"score": 52.0, "confidence": 65.0},
            "news_sentiment": {"score": 57.0, "confidence": 55.0},
        },
        "market_snapshot": {
            "metrics": {
                "vix": {"value": 14.2},
                "ten_year_yield": {"value": 4.31},
                "spy_close": {"value": 588.12},
            }
        },
    }


@pytest.fixture
def options_candidate() -> dict:
    return {
        "symbol": "SPY",
        "strategy_id": "put_credit_spread",
        "scanner_key": "vertical_spread",
        "expiration": "2026-01-16",
        "short_strike": 570,
        "long_strike": 565,
        "net_credit": 1.35,
        "width": 5.0,
        "max_profit_per_share": 1.35,
        "max_loss_per_share": 3.65,
    }


@pytest.fixture
def stock_candidate() -> dict:
    return {
        "symbol": "NVDA",
        "strategy_id": "pullback_swing",
        "scanner_key": "pullback_swing",
        "current_price": 142.5,
        "stop_loss": 138.0,
    }


# ─── Small helpers ────────────────────────────────────────────────────


def test_truncate_reasoning_none():
    assert _truncate_reasoning(None) is None


def test_truncate_reasoning_short():
    assert _truncate_reasoning("short text") == "short text"


def test_truncate_reasoning_collapses_whitespace():
    assert _truncate_reasoning("a  \n\t b") == "a b"


def test_truncate_reasoning_clips_to_limit():
    long = "x" * (LLM_REASONING_MAX_CHARS + 100)
    out = _truncate_reasoning(long)
    assert out is not None
    assert len(out) == LLM_REASONING_MAX_CHARS
    assert out.endswith("…")


def test_make_decision_id_stable_and_unique():
    a = make_decision_id("options", "2026-01-15T18:00:00+00:00", "SPY:put_credit:570:565")
    b = make_decision_id("options", "2026-01-15T18:00:00+00:00", "SPY:put_credit:570:565")
    c = make_decision_id("options", "2026-01-15T18:00:00+00:00", "SPY:put_credit:575:570")
    assert a == b
    assert a != c
    assert a.startswith("options_20260115180000_spy_put_credit_570_565_")


# ─── Business-day math ────────────────────────────────────────────────


def test_business_days_between_skips_weekend():
    # Fri 2025-11-07 → Mon 2025-11-10 = 1 business day (Fri→Mon, exclusive end becomes
    # [Fri, Mon) = 1 day counted for Fri)
    days = _business_days_between(date(2025, 11, 7), date(2025, 11, 10))
    assert days == 1


def test_business_days_between_zero_for_past_end():
    assert _business_days_between(date(2025, 11, 10), date(2025, 11, 7)) == 0


def test_business_days_skips_holiday():
    # Thanksgiving 2025 = Thu Nov 27. Wed 26 → Fri 28 = 1 biz day (only Wed counted,
    # Thu is holiday, Fri is end-exclusive).
    days = _business_days_between(date(2025, 11, 26), date(2025, 11, 28))
    assert days == 1


def test_compute_tracking_window_options(options_candidate):
    w = _compute_tracking_window_days(options_candidate, "options")
    assert w > 0


def test_compute_tracking_window_options_missing_exp():
    w = _compute_tracking_window_days({"symbol": "SPY"}, "options")
    assert w == 10  # fallback


def test_compute_tracking_window_stock_known():
    w = _compute_tracking_window_days(
        {"symbol": "AAPL", "scanner_key": "pullback_swing"}, "stock"
    )
    assert w == 10  # from STOCK_TRACKING_WINDOWS


def test_compute_tracking_window_stock_momentum():
    w = _compute_tracking_window_days(
        {"symbol": "NVDA", "scanner_key": "momentum_breakout"}, "stock"
    )
    assert w == 20


def test_compute_tracking_window_stock_unknown_key():
    w = _compute_tracking_window_days({"symbol": "X", "scanner_key": "bogus"}, "stock")
    assert w == 10  # fallback


def test_compute_tracking_ends_utc_shifts_by_business_days():
    # 2025-11-07 Fri, +3 biz days → 2025-11-12 Wed
    out = _compute_tracking_ends_utc("2025-11-07T18:00:00+00:00", 3)
    assert out.startswith("2025-11-12T18:00:00")


# ─── Snapshot field extraction ────────────────────────────────────────


def test_extract_snapshot_fields_regime_and_confidence(sample_market_state):
    f = _extract_snapshot_fields(sample_market_state)
    assert f["regime"] == "risk_on"
    # 0.72 * 100 = 72
    assert f["regime_confidence"] == pytest.approx(72.0)
    assert f["breadth_score"] == 61.0
    assert f["flows_confidence"] == 70.0
    assert f["vix"] == 14.2
    assert f["us10y"] == 4.31
    assert f["spy_close"] == 588.12
    assert f["flows_pillar_json"] is not None
    assert "etf_flows" in f["flows_pillar_json"]


def test_extract_snapshot_fields_leaves_0to100_confidence_unchanged():
    # If composite.confidence is already 85 (0-100), don't multiply.
    ms = {"composite": {"market_state": "neutral", "confidence": 85.0}, "engines": {}}
    f = _extract_snapshot_fields(ms)
    assert f["regime_confidence"] == 85.0


def test_build_flows_pillar_json_handles_missing():
    assert _build_flows_pillar_json(None) is None
    assert _build_flows_pillar_json({}) is not None


# ─── Hypothetical extraction ──────────────────────────────────────────


def test_hypothetical_options_credit_spread(options_candidate):
    h = _extract_hypothetical_options(options_candidate)
    assert h["hypothetical_entry_price"] == 1.35  # net credit per share
    assert h["hypothetical_max_profit"] == pytest.approx(135.0)  # 1.35 * 100
    assert h["hypothetical_max_loss"] == pytest.approx(365.0)  # 3.65 * 100


def test_hypothetical_options_credit_spread_derived_from_width():
    # Only credit + width given — reconstruct max_loss.
    c = {
        "strategy_id": "call_credit_spread",
        "net_credit": 0.80,
        "width": 5.0,
    }
    h = _extract_hypothetical_options(c)
    assert h["hypothetical_entry_price"] == 0.80
    assert h["hypothetical_max_profit"] == pytest.approx(80.0)
    assert h["hypothetical_max_loss"] == pytest.approx((5.0 - 0.80) * 100.0)


def test_hypothetical_options_debit_spread():
    c = {
        "strategy_id": "call_debit",
        "net_debit": 1.50,
        "width": 5.0,
    }
    h = _extract_hypothetical_options(c)
    assert h["hypothetical_entry_price"] == 1.50
    assert h["hypothetical_max_profit"] == pytest.approx((5.0 - 1.50) * 100.0)
    assert h["hypothetical_max_loss"] == pytest.approx(150.0)


def test_hypothetical_options_iron_condor():
    c = {
        "strategy_id": "iron_condor",
        "net_credit": 2.10,
        "width": 5.0,
        "max_profit": 210.0,
        "max_loss": 290.0,
    }
    h = _extract_hypothetical_options(c)
    assert h["hypothetical_entry_price"] == 2.10
    assert h["hypothetical_max_profit"] == 210.0
    assert h["hypothetical_max_loss"] == 290.0


def test_hypothetical_stock_uses_current_price(stock_candidate):
    h = _extract_hypothetical_stock(stock_candidate)
    assert h["hypothetical_entry_price"] == 142.5
    assert h["hypothetical_max_profit"] is None
    assert h["hypothetical_max_loss"] == pytest.approx(4.5)


def test_hypothetical_stock_without_stop():
    c = {"symbol": "AAPL", "close": 200.0}
    h = _extract_hypothetical_stock(c)
    assert h["hypothetical_entry_price"] == 200.0
    assert h["hypothetical_max_loss"] is None


# ─── Null-session-maker no-op (REQUIRED) ──────────────────────────────


@pytest.mark.asyncio
async def test_log_market_snapshot_null_session_noop(sample_market_state):
    # Must return silently (no exception, no log.error spam per-call)
    result = await log_market_snapshot(
        session_maker=None,
        snapshot_id="snap_test_1",
        market_state=sample_market_state,
        captured_at_utc="2026-01-15T18:00:00+00:00",
    )
    assert result is None


@pytest.mark.asyncio
async def test_log_decision_null_session_noop(options_candidate):
    decision_id = await log_decision(
        session_maker=None,
        decision_type="options",
        candidate=options_candidate,
        recommendation="EXECUTE",
        run_id="run_test_1",
        workflow_id="options_opportunity",
    )
    assert decision_id is None


# ─── Round-trip: snapshot + two decisions ─────────────────────────────


@pytest.mark.asyncio
async def test_roundtrip_snapshot_and_decisions(
    session_maker, sample_market_state, options_candidate, stock_candidate
):
    snap_id = "snap_roundtrip_1"
    now_utc = "2026-01-15T18:00:00+00:00"

    await log_market_snapshot(
        session_maker=session_maker,
        snapshot_id=snap_id,
        market_state=sample_market_state,
        captured_at_utc=now_utc,
        artifact_filename="market_state_20260115_180000.json",
    )

    opt_id = await log_decision(
        session_maker=session_maker,
        decision_type="options",
        candidate=options_candidate,
        recommendation="EXECUTE",
        run_id="run_opt_1",
        workflow_id="options_opportunity",
        snapshot_id=snap_id,
        model_score=78.5,
        deterministic_rank=3,
        rank=1,
        llm_reasoning=" trim  me   ",
        timestamp_utc=now_utc,
    )
    assert opt_id is not None

    stk_id = await log_decision(
        session_maker=session_maker,
        decision_type="stock",
        candidate=stock_candidate,
        recommendation="PASS",
        run_id="run_stk_1",
        workflow_id="stock_opportunity",
        snapshot_id=snap_id,
        market_state=sample_market_state,
        model_score=42.0,
        timestamp_utc=now_utc,
    )
    assert stk_id is not None

    # Read back
    async with session_maker() as s:
        snap = await s.get(MarketStateSnapshot, snap_id)
        assert snap is not None
        assert snap.regime == "risk_on"
        assert snap.regime_confidence == pytest.approx(72.0)
        assert snap.vix == 14.2

        opt = await s.get(Decision, opt_id)
        assert opt is not None
        assert opt.symbol == "SPY"
        assert opt.strategy_id == "put_credit_spread"
        assert opt.snapshot_id == snap_id  # FK preserved
        assert opt.recommendation == "EXECUTE"
        assert opt.hypothetical_entry_price == 1.35
        assert opt.hypothetical_max_profit == pytest.approx(135.0)
        assert opt.hypothetical_max_loss == pytest.approx(365.0)
        assert opt.tracking_window_days > 0
        assert opt.tracking_ends_utc >= now_utc
        assert opt.tracking_status == "active"
        assert opt.llm_reasoning == "trim me"

        stk = await s.get(Decision, stk_id)
        assert stk is not None
        assert stk.symbol == "NVDA"
        assert stk.tracking_window_days == 10  # pullback_swing window
        assert stk.hypothetical_entry_price == 142.5
        assert stk.hypothetical_max_loss == pytest.approx(4.5)
        assert stk.recommendation == "PASS"
