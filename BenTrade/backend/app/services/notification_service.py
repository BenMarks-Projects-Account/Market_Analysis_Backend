"""Notification service — monitors workflow results for actionable signals."""

import logging
import threading
from datetime import datetime, timezone
from collections import deque

_log = logging.getLogger("bentrade.notifications")


class NotificationService:
    """Monitors TMC results for BUY/EXECUTE signals and maintains notification history."""

    MAX_NOTIFICATIONS = 200  # Ring buffer size

    def __init__(self):
        self._lock = threading.Lock()
        self._notifications = deque(maxlen=self.MAX_NOTIFICATIONS)
        self._unread_count = 0
        self._listeners = []  # For real-time push (future SSE support)

    def check_stock_results(self, stock_result: dict):
        """Check stock workflow results for BUY recommendations."""
        if not stock_result:
            return

        candidates = stock_result.get("candidates") or stock_result.get("recommendations") or []
        for cand in candidates:
            rec = (cand.get("model_recommendation") or cand.get("recommendation") or "").upper()
            if rec in ("BUY", "EXECUTE"):
                self._add_notification(
                    notification_type="stock_buy",
                    symbol=cand.get("symbol"),
                    strategy=cand.get("scanner_key") or cand.get("strategy"),
                    score=cand.get("model_score") or cand.get("score"),
                    conviction=cand.get("model_confidence") or cand.get("conviction"),
                    headline=cand.get("model_review_summary") or cand.get("headline"),
                    price=cand.get("underlying_price") or cand.get("price"),
                    source="stock_workflow",
                    candidate_data=cand,
                )

    def check_options_results(self, options_result: dict):
        """Check options workflow results for EXECUTE recommendations."""
        if not options_result:
            return

        candidates = options_result.get("candidates") or options_result.get("selected") or []
        for cand in candidates:
            rec = (cand.get("model_recommendation") or "").upper()
            if rec in ("BUY", "EXECUTE"):
                math = cand.get("math", {})
                legs = cand.get("legs", [])
                strikes = "/".join(str(l.get("strike", "")) for l in legs)

                self._add_notification(
                    notification_type="options_execute",
                    symbol=cand.get("symbol"),
                    strategy=cand.get("scanner_key") or cand.get("strategy_id"),
                    score=cand.get("model_score"),
                    conviction=cand.get("model_confidence"),
                    headline=cand.get("model_review_summary"),
                    price=cand.get("underlying_price"),
                    strikes=strikes,
                    expiration=cand.get("expiration"),
                    dte=cand.get("dte"),
                    pop=math.get("pop"),
                    ev=math.get("ev"),
                    credit=math.get("net_credit"),
                    debit=math.get("net_debit"),
                    source="options_workflow",
                    candidate_data=cand,
                )

    def _add_notification(self, *, notification_type, symbol, strategy, **kwargs):
        """Add a notification to the list."""
        notification = {
            "id": f"notif_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{symbol}_{strategy}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": notification_type,
            "symbol": symbol,
            "strategy": strategy,
            "read": False,
            **{k: v for k, v in kwargs.items() if k != "candidate_data"},
        }

        # Store candidate data separately (larger, for drill-down)
        candidate_data = kwargs.get("candidate_data")
        if candidate_data:
            notification["has_detail"] = True

        with self._lock:
            self._notifications.appendleft(notification)
            self._unread_count += 1

        _log.info(
            "event=notification_added type=%s symbol=%s strategy=%s score=%s",
            notification_type, symbol, strategy, kwargs.get("score"),
        )

        # Notify real-time listeners
        for listener in self._listeners:
            try:
                listener(notification)
            except Exception:
                pass

    def get_notifications(self, *, limit: int = 50, unread_only: bool = False) -> list:
        """Get recent notifications."""
        with self._lock:
            notifications = list(self._notifications)

        if unread_only:
            notifications = [n for n in notifications if not n.get("read")]

        return notifications[:limit]

    def get_unread_count(self) -> int:
        with self._lock:
            return self._unread_count

    def mark_read(self, notification_id: str = None):
        """Mark one or all notifications as read."""
        with self._lock:
            if notification_id:
                for n in self._notifications:
                    if n["id"] == notification_id:
                        if not n["read"]:
                            n["read"] = True
                            self._unread_count = max(0, self._unread_count - 1)
                        break
            else:
                # Mark all as read
                for n in self._notifications:
                    n["read"] = True
                self._unread_count = 0

    def clear(self):
        """Clear all notifications."""
        with self._lock:
            self._notifications.clear()
            self._unread_count = 0


# Singleton
_service: NotificationService | None = None


def get_notification_service() -> NotificationService:
    global _service
    if _service is None:
        _service = NotificationService()
    return _service
