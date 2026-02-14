import math
from datetime import datetime, timezone, timedelta


def dte_ceil(expiration_yyyy_mm_dd: str, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    expiration_dt = datetime.strptime(expiration_yyyy_mm_dd, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    delta = expiration_dt - current
    return max(0, math.ceil(delta.total_seconds() / 86400))


def unix_days_ago(days: int) -> int:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return int(dt.timestamp())


def unix_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())
