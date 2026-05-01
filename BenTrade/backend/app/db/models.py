"""BenTrade decision history — SQLAlchemy ORM models (9 tables).

Schema overview
---------------
1.  ``market_state_snapshots``       — one row per Market Intelligence publish
2.  ``decisions``                    — one row per LLM-evaluated candidate
3.  ``scanner_runs``                 — one row per scanner invocation
4.  ``scanner_candidates``           — per-candidate rows tied to a scanner_run
5.  ``executions``                   — one row per broker order leg (Tradier)
6.  ``position_events``              — timeline of position state changes
7.  ``decision_outcomes``            — strategy-aware realized outcome per horizon
8.  ``decision_daily_tracking``      — per-decision per-day shadow snapshot
9.  ``underlying_price_history``     — daily OHLC for every symbol we track

Design notes
------------
* Every LLM-evaluated candidate becomes a ``decisions`` row regardless of
  verdict (EXECUTE or PASS). Post-LLM filtering (``model_filter``) is a
  UI-presentation concern, not a data-capture concern. This supports
  computing two calibration win rates:
    - all-evaluated  → calibrates scanner + deterministic filters
    - execute-only   → calibrates the LLM final-decision layer
* Real executions layer on top as a reality check on the shadow data.

Conventions
-----------
* All timestamp columns carry the ``_utc`` suffix (ISO 8601 UTC TEXT).
* JSON-shaped data is stored as ``TEXT`` (use ``json_extract``).
* ``regime`` stores lowercase underscore form from MI composite.
* ``regime_confidence`` stored 0–100 (composite.confidence × 100 at write).
* ``*_confidence`` columns read from ``engines.<name>.confidence`` (0–100).

Migration strategy
------------------
Raw DDL via ``Base.metadata.create_all`` on startup (no Alembic), mirroring
the Company Evaluator pattern.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all BenTrade history ORM models."""


# ─────────────────────────────────────────────────────────────────────
# 1) market_state_snapshots  (unchanged from Step 1)
# ─────────────────────────────────────────────────────────────────────
class MarketStateSnapshot(Base):
    """One row per Market Intelligence publish."""

    __tablename__ = "market_state_snapshots"

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    captured_at_utc: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    regime: Mapped[str | None] = mapped_column(Text, nullable=True)
    regime_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    breadth_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    breadth_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    cross_asset_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cross_asset_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    flows_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    flows_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    news_sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    news_sentiment_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    vix: Mapped[float | None] = mapped_column(Float, nullable=True)
    us10y: Mapped[float | None] = mapped_column(Float, nullable=True)
    spy_close: Mapped[float | None] = mapped_column(Float, nullable=True)

    flows_pillar_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    artifact_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_utc: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_snapshots_regime_captured", "regime", "captured_at_utc"),
    )


# ─────────────────────────────────────────────────────────────────────
# 2) decisions  (EXPANDED in Step 1.5 with tracking + hypothetical cols)
# ─────────────────────────────────────────────────────────────────────
class Decision(Base):
    """One row per LLM-evaluated candidate.

    Captured for BOTH EXECUTE and PASS verdicts. Captured before any
    post-LLM UI filter (``model_filter``). Every decision is shadow-tracked
    over a strategy-specific window.

    ``recommendation`` values:
      * options/stock: ``'EXECUTE'`` | ``'PASS'``
      * active-trade:  ``'HOLD'`` | ``'REDUCE'`` | ``'CLOSE'`` | ``'URGENT_REVIEW'``
    """

    __tablename__ = "decisions"

    decision_id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("market_state_snapshots.snapshot_id"),
        nullable=True,
        index=True,
    )
    timestamp_utc: Mapped[str] = mapped_column(Text, nullable=False)

    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    scanner_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    model_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    deterministic_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expiration: Mapped[str | None] = mapped_column(Text, nullable=True)

    candidate_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Truncated to 500 chars at write time
    llm_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Step 1.5: hypothetical entry/exit for shadow P&L ─────────────
    # For options: net credit (credit spreads) or net debit (debit spreads).
    # For stocks: close price at decision time.
    hypothetical_entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Options spreads: known upfront (credit collected / width-debit paid).
    # Stocks: None — open-ended.
    hypothetical_max_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Options spreads: width - credit (credit) or debit paid (debit).
    # Stocks: derived from candidate stop-loss if present, else None.
    hypothetical_max_loss: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Step 1.5: tracking window ────────────────────────────────────
    # Required: every decision has a window (see tracking_config.py +
    # strategy-specific computation in history_recorder.py).
    tracking_window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    # ISO datetime. decision_date + tracking_window_days (trading days).
    # Indexed because daily tracking job queries "WHERE tracking_ends_utc > now()".
    tracking_ends_utc: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # "active" | "completed" | "stopped_early"
    tracking_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="active",
        index=True,
    )

    created_at_utc: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_decisions_symbol_timestamp", "symbol", "timestamp_utc"),
        Index("idx_decisions_workflow_timestamp", "workflow_id", "timestamp_utc"),
    )


# ─────────────────────────────────────────────────────────────────────
# 3) scanner_runs  (unchanged from Step 1)
# ─────────────────────────────────────────────────────────────────────
class ScannerRun(Base):
    """One row per scanner invocation — stores the filter trace."""

    __tablename__ = "scanner_runs"

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(Text, nullable=False)
    scanner_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    preset: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at_utc: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at_utc: Mapped[str | None] = mapped_column(Text, nullable=True)

    stage_counts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_counts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_quality_counts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    thresholds_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    total_constructed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_passed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_rejected: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at_utc: Mapped[str] = mapped_column(Text, nullable=False)


# ─────────────────────────────────────────────────────────────────────
# 4) scanner_candidates  (unchanged from Step 1)
# ─────────────────────────────────────────────────────────────────────
class ScannerCandidate(Base):
    """Per-candidate row inside a scanner_run. Records passers AND rejects."""

    __tablename__ = "scanner_candidates"

    candidate_id: Mapped[str] = mapped_column(Text, primary_key=True)
    scanner_run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("scanner_runs.run_id"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    scanner_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    deterministic_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    candidate_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_utc: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_scanner_candidates_run_symbol", "scanner_run_id", "symbol"),
    )


# ─────────────────────────────────────────────────────────────────────
# 5) executions  (EXPANDED in Step 1.5 with slippage + delay cols)
# ─────────────────────────────────────────────────────────────────────
class Execution(Base):
    """One row per broker order leg placement/fill event."""

    __tablename__ = "executions"

    execution_id: Mapped[str] = mapped_column(Text, primary_key=True)
    decision_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("decisions.decision_id"),
        nullable=True,
        index=True,
    )
    broker_order_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    account_mode: Mapped[str] = mapped_column(Text, nullable=False)  # 'live' | 'paper'

    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    option_symbol: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)

    submitted_at_utc: Mapped[str] = mapped_column(Text, nullable=False)
    filled_at_utc: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Step 1.5: reality-check metrics ──────────────────────────────
    # actual_fill_price - decision.hypothetical_entry_price.
    # Positive = paid more than hypothetical (bad).
    slippage_vs_hypothetical: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Hours between decision.timestamp_utc and submitted_at_utc.
    delay_hours: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw_response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_utc: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_executions_symbol_submitted", "symbol", "submitted_at_utc"),
    )


# ─────────────────────────────────────────────────────────────────────
# 6) position_events  (unchanged from Step 1)
# ─────────────────────────────────────────────────────────────────────
class PositionEvent(Base):
    """Timeline of position state changes.

    ``position_key`` is a stable synthetic id for a multi-leg strategy.
    Events with the same ``position_key`` form the life-cycle of one trade
    and persist across multiple executions (open + close).

    ``execution_id`` is an optional FK to the specific execution that caused
    this event. Nullable because some events (expire/assign) may not map to
    a broker execution.
    """

    __tablename__ = "position_events"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    position_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    decision_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("decisions.decision_id"),
        nullable=True,
    )
    execution_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("executions.execution_id"),
        nullable=True,
    )

    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at_utc: Mapped[str] = mapped_column(Text, nullable=False)

    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_credit_debit: Mapped[float | None] = mapped_column(Float, nullable=True)
    greeks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_utc: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index(
            "idx_position_events_position_occurred",
            "position_key",
            "occurred_at_utc",
        ),
    )


# ─────────────────────────────────────────────────────────────────────
# 7) decision_outcomes  (EXPANDED in Step 1.5 — strategy-aware)
# ─────────────────────────────────────────────────────────────────────
class DecisionOutcome(Base):
    """Realized outcome of a decision at a specific evaluation horizon.

    Compound UNIQUE on ``(decision_id, horizon)``. One decision produces
    multiple outcome rows (one per horizon).

    Horizons for options decisions:
      ``entry`` | ``t_plus_1`` | ``t_plus_5`` | ``t_plus_10`` | ``at_expiry``
    Horizons for stock decisions:
      ``entry`` | ``t_plus_1`` | ``t_plus_5`` | ``t_plus_10`` | ``at_window_end``

    Phase 1 ships with the schema only — all columns are NULL until the
    future outcome-scorer project populates them.
    """

    __tablename__ = "decision_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("decisions.decision_id"),
        nullable=False,
        index=True,
    )
    horizon: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    scored_at_utc: Mapped[str] = mapped_column(Text, nullable=False)
    # Lets future scorers coexist with current data
    scorer_version: Mapped[str] = mapped_column(Text, nullable=False, default="1.0")

    # Parallel shadow / real P&L
    shadow_pnl_at_horizon: Mapped[float | None] = mapped_column(Float, nullable=True)
    real_pnl_at_horizon: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Market context
    underlying_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Strategy-agnostic outcome metrics
    pct_of_max_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_of_max_loss: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Strategy-agnostic booleans (NULL when not applicable)
    hit_profit_target: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    breached_short_strike: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # options-only
    stopped_out: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # stocks with stops
    expired_worthless: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # options-only

    # Escape hatch for strategy-specific fields that don't fit above
    strategy_specific_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    evaluation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("decision_id", "horizon", name="uq_outcomes_decision_horizon"),
        Index("ix_outcomes_horizon_scored", "horizon", "scored_at_utc"),
    )


# ─────────────────────────────────────────────────────────────────────
# 8) decision_daily_tracking  (NEW in Step 1.5)
# ─────────────────────────────────────────────────────────────────────
class DecisionDailyTracking(Base):
    """Per-decision per-day shadow snapshot.

    Written by a daily tracking job (ships in a later Phase — table exists
    in Phase 1 but no code writes to it yet).

    Volume estimate: ~40 decisions/day × ~15d avg window ≈ 600 rows/day,
    ~150K rows/year. Well within SQLite comfort zone.
    """

    __tablename__ = "decision_daily_tracking"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("decisions.decision_id"),
        nullable=False,
        index=True,
    )
    # ISO date (YYYY-MM-DD), not datetime
    tracking_date: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # Trading days, not calendar days
    days_since_decision: Mapped[int] = mapped_column(Integer, nullable=False)

    # Current marks
    current_mark: Mapped[float | None] = mapped_column(Float, nullable=True)
    underlying_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Shadow P&L
    shadow_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    shadow_pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Running extrema
    mae_to_date: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe_to_date: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Greeks (options-only; NULL for stocks)
    delta_now: Mapped[float | None] = mapped_column(Float, nullable=True)
    theta_now: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("decision_id", "tracking_date", name="uq_tracking_decision_date"),
        Index("ix_tracking_date_decision", "tracking_date", "decision_id"),
    )


# ─────────────────────────────────────────────────────────────────────
# 9) underlying_price_history  (NEW in Step 1.5)
# ─────────────────────────────────────────────────────────────────────
class UnderlyingPriceHistory(Base):
    """Daily OHLC for every symbol we track.

    CE's database has no price history table (CE stores fundamentals
    analyses only: company_evaluations, evaluation_history, entry_point_analyses,
    comps_analyses, dcf_analyses, eva_analyses, universe_symbols, on_demand_jobs,
    crawler_cycle_metrics). No reuse possible — BenTrade owns this table.

    Populated lazily: when a decision references a symbol we don't have
    history for, the daily tracking job fetches and writes here.
    """

    __tablename__ = "underlying_price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    trade_date: Mapped[str] = mapped_column(Text, nullable=False, index=True)  # ISO date

    open_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Only truly required field
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "fmp" | "polygon" | "tradier"
    source: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", name="uq_price_symbol_date"),
    )


__all__ = [
    "Base",
    "MarketStateSnapshot",
    "Decision",
    "ScannerRun",
    "ScannerCandidate",
    "Execution",
    "PositionEvent",
    "DecisionOutcome",
    "DecisionDailyTracking",
    "UnderlyingPriceHistory",
]
