"""Workday config service — read cached workday/hours config from DB."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workday_config import WorkdayConfig

# Defaults (before DB is populated)
_DEFAULTS = [
    (0, True, 8, 17),   # Mon
    (1, True, 8, 17),   # Tue
    (2, True, 8, 17),   # Wed
    (3, True, 8, 17),   # Thu
    (4, True, 8, 17),   # Fri
    (5, False, 8, 17),  # Sat
    (6, False, 8, 17),  # Sun
]


async def get_workday_config(db: AsyncSession) -> list[dict]:
    """Return [{day_of_week, label, is_workday, work_start, work_end}, ...]."""
    result = await db.execute(
        select(WorkdayConfig).order_by(WorkdayConfig.day_of_week)
    )
    rows = result.scalars().all()
    if rows:
        return [
            {
                "day_of_week": r.day_of_week,
                "label": r.label,
                "is_workday": r.is_workday,
                "work_start": r.work_start,
                "work_end": r.work_end,
            }
            for r in rows
        ]
    return [
        {"day_of_week": d, "label": ["周一","周二","周三","周四","周五","周六","周日"][d],
         "is_workday": wd, "work_start": f"{ws:02d}:00", "work_end": f"{we:02d}:00"}
        for d, wd, ws, we in _DEFAULTS
    ]


async def update_workday(
    db: AsyncSession, day_of_week: int, is_workday: bool,
    work_start: str | None = None, work_end: str | None = None,
) -> None:
    """Update a single workday config row."""
    result = await db.execute(
        select(WorkdayConfig).where(WorkdayConfig.day_of_week == day_of_week)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return
    row.is_workday = is_workday
    if work_start is not None:
        row.work_start = work_start
    if work_end is not None:
        row.work_end = work_end
    await db.commit()


def _parse_time(t: str) -> tuple[int, int]:
    """Parse '08:00' or '17:30' → (hour, minute)."""
    parts = t.split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
