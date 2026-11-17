from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def month_windows(now_ts: int, timezone: str, count: int = 12) -> list[tuple[str, int, int]]:
    """Return (label, start_ts, end_ts) for the last `count` calendar months, oldest first.

    Months follow the configured time zone; the current month ends at `now_ts`.
    """
    tz = ZoneInfo(timezone)
    now = datetime.fromtimestamp(now_ts, tz)
    year, month = now.year, now.month
    windows: list[tuple[str, int, int]] = []
    for _ in range(count):
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        start = int(datetime(year, month, 1, tzinfo=tz).timestamp())
        end = min(int(datetime(next_year, next_month, 1, tzinfo=tz).timestamp()), now_ts)
        windows.append((f"{year}-{month:02d}", start, end))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    windows.reverse()
    return windows
