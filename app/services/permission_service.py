"""Permission service — loads transition permissions from DB per request.

No module-level globals — each request gets fresh permissions via FastAPI Depends.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transition_permission import TransitionPermission

# Default permissions (fallback when DB is empty)
_DEFAULT_PERMISSIONS: dict[tuple[str, str], str] = {
    ("pending", "in_progress"): "any_staff",
    ("pending", "cancelled"): "admin_only",
    ("in_progress", "completed"): "owner",
    ("in_progress", "cancelled"): "admin_only",
    ("in_progress", "escalated"): "owner",
    ("escalated", "in_progress"): "owner",
    ("escalated", "completed"): "owner",
    ("escalated", "cancelled"): "admin_only",
    ("completed", "in_progress"): "any_staff",
    ("cancelled", "pending"): "admin_only",
}


async def get_transition_permissions(
    db: AsyncSession,
) -> dict[tuple[str, str], str]:
    """Load transition permissions from DB.

    Returns {(from_status, to_status): permission}.
    Falls back to defaults if DB is empty.
    """
    result = await db.execute(select(TransitionPermission))
    rows = result.scalars().all()
    if rows:
        return {(r.from_status, r.to_status): r.permission for r in rows}
    return dict(_DEFAULT_PERMISSIONS)


def get_permission(
    perms: dict[tuple[str, str], str],
    from_status: str,
    to_status: str,
) -> str:
    """Look up permission for a specific transition."""
    return perms.get((from_status, to_status), "admin_only")


def get_admin_only_targets(
    perms: dict[tuple[str, str], str],
) -> set[str]:
    """Return set of target statuses that are admin-only."""
    return {to_status for (_, to_status), perm in perms.items() if perm == "admin_only"}
