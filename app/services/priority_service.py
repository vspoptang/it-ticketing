"""Priority SLA config service — read/write priority SLA hours from DB."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.priority_config import PriorityConfig

# Fallback defaults (used before DB is populated)
_DEFAULTS = {
    "紧急": 4.0,
    "高": 8.0,
    "中": 24.0,
    "低": 48.0,
}


async def get_priority_hours(db: AsyncSession) -> dict[str, float]:
    """Return {priority: hours} map from DB. Falls back to defaults."""
    result = await db.execute(select(PriorityConfig).order_by(PriorityConfig.sort_order))
    rows = result.scalars().all()
    if rows:
        return {r.priority: r.hours for r in rows}
    return dict(_DEFAULTS)


async def get_all_priorities(db: AsyncSession) -> list[PriorityConfig]:
    """Return all priority configs, ordered by sort_order."""
    result = await db.execute(select(PriorityConfig).order_by(PriorityConfig.sort_order))
    return list(result.scalars().all())


async def update_priority_hours(
    db: AsyncSession, priority: str, hours: float
) -> PriorityConfig:
    """Update hours for a priority. Returns the updated config."""
    result = await db.execute(
        select(PriorityConfig).where(PriorityConfig.priority == priority)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Unknown priority: {priority}")
    row.hours = hours
    await db.commit()
    await db.refresh(row)
    return row


async def recalculate_all_sla(db: AsyncSession) -> int:
    """Recalculate sla_due_at for all non-terminal tickets using current priority config.
    Returns number of tickets updated."""
    from app.models.ticket import Ticket
    from app.services.ticket_service import compute_sla_due_at

    hours_map = await get_priority_hours(db)

    result = await db.execute(
        select(Ticket).where(
            Ticket.status.notin_(["completed", "cancelled"])
        )
    )
    tickets = result.scalars().all()

    count = 0
    for ticket in tickets:
        new_sla = compute_sla_due_at(ticket.priority, start_time=ticket.created_at, sla_map=hours_map)
        if new_sla and new_sla != ticket.sla_due_at:
            ticket.sla_due_at = new_sla
            count += 1

    if count > 0:
        await db.commit()

    return count
