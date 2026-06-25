from datetime import datetime, timezone, timedelta

# China Standard Time (UTC+8)
CST = timezone(timedelta(hours=8))


def now() -> datetime:
    """Return current datetime in China timezone."""
    return datetime.now(CST)
