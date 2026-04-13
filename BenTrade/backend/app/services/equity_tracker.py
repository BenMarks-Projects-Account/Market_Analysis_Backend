"""Equity Tracker — daily account balance snapshots for equity curve.

Stores daily snapshots of account equity in a SQLite database.
Each snapshot records total_value, cash, positions_value, and day_pnl.
The equity curve endpoint reads from this local store.

Input fields:
  - total_value: from Tradier balances → equity or total_equity
  - cash: from Tradier balances → cash_available or total_cash
  - positions_value: total_value - cash (derived)
  - day_pnl: change from previous day's total_value (derived)
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_equity (
    date TEXT PRIMARY KEY,
    total_value REAL NOT NULL,
    cash REAL,
    positions_value REAL,
    day_pnl REAL,
    created_at TEXT NOT NULL
)
"""


class EquityTracker:
    """Manages daily equity curve snapshots stored in SQLite."""

    def __init__(self, data_dir: Path) -> None:
        self._db_path = data_dir / "equity_history.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    async def snapshot_from_balances(self, balances: dict[str, Any]) -> dict | None:
        """Take a snapshot from a Tradier balances payload.

        Returns the saved record dict, or None if balances are unusable.
        """
        if not balances:
            logger.warning("event=equity_snapshot_skip reason=no_balances")
            return None

        # Tradier balances can be nested under "balances" key
        bal = balances.get("balances", balances)
        if isinstance(bal, dict) and "balances" in bal:
            bal = bal["balances"]

        total = _safe_float(bal.get("equity") or bal.get("total_equity"))
        if total is None or total <= 0:
            logger.warning("event=equity_snapshot_skip reason=no_equity value=%s", bal.get("equity"))
            return None

        cash = _safe_float(bal.get("cash_available") or bal.get("total_cash") or bal.get("cash", {}).get("cash_available"))
        positions_value = round(total - cash, 2) if cash is not None else None

        today_str = date.today().isoformat()

        # Compute day P&L from previous snapshot
        prev = self._get_previous(today_str)
        day_pnl = round(total - prev["total_value"], 2) if prev else None

        now_utc = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO daily_equity
                   (date, total_value, cash, positions_value, day_pnl, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (today_str, round(total, 2), round(cash, 2) if cash is not None else None,
                 positions_value, day_pnl, now_utc),
            )
            conn.commit()
        finally:
            conn.close()

        record = {
            "date": today_str,
            "total_value": round(total, 2),
            "cash": round(cash, 2) if cash is not None else None,
            "positions_value": positions_value,
            "day_pnl": day_pnl,
        }
        logger.info(
            "event=equity_snapshot_saved date=%s total=%.2f cash=%s pnl=%s",
            today_str, total, cash, day_pnl,
        )
        return record

    def _get_previous(self, exclude_date: str) -> dict | None:
        conn = self._get_conn()
        try:
            row = conn.execute(
                """SELECT date, total_value FROM daily_equity
                   WHERE date < ? ORDER BY date DESC LIMIT 1""",
                (exclude_date,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_history(self, days: int = 90) -> list[dict]:
        """Return up to `days` most recent snapshots, oldest first."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT date, total_value, cash, positions_value, day_pnl
                   FROM daily_equity ORDER BY date DESC LIMIT ?""",
                (days,),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]
        finally:
            conn.close()


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None
