"""Dashboard time period helpers."""

from datetime import datetime, timedelta

from app.timezone_helper import now


def compute_period(period: str) -> tuple[datetime | None, datetime | None, str]:
    """Compute (date_start, date_end, label) from period selector.
    
    period values: this_month, 30d, 90d, all
    Returns (None, None, label) for 'all' (no filter).
    """
    current = now()

    if period == "30d":
        start = current - timedelta(days=30)
        return start.replace(hour=0, minute=0, second=0, microsecond=0), current, "近 30 天"

    elif period == "90d":
        start = current - timedelta(days=90)
        return start.replace(hour=0, minute=0, second=0, microsecond=0), current, "近 3 个月"

    elif period == "all":
        return None, None, "全部"

    else:  # this_month (default)
        start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, current, f"{current.year}年{current.month}月"


def month_range(offset: int) -> tuple[datetime, datetime, str]:
    """Compute month range with offset from current month.
    offset=0 → this month, offset=-1 → last month, etc.
    """
    current = now()
    y, m = current.year, current.month
    m += offset
    while m < 1:
        y -= 1
        m += 12
    while m > 12:
        y += 1
        m -= 12

    start = datetime(y, m, 1, tzinfo=current.tzinfo)
    if m == 12:
        end = datetime(y + 1, 1, 1, tzinfo=current.tzinfo)
    else:
        end = datetime(y, m + 1, 1, tzinfo=current.tzinfo)

    return start, end, f"{y}年{m}月"
