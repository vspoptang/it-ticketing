from __future__ import annotations

import math
from datetime import datetime, timedelta
from app.timezone_helper import now

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.ticket import Ticket, TicketEvent
from app.schemas.ticket import TicketCreate, TicketUpdate

# ═══════════════════════════════════════════════════════════════
# Status machine
# ═══════════════════════════════════════════════════════════════

VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled", "escalated"}

VALID_TRANSITIONS: dict[str, list[str]] = {
    "pending":     ["in_progress", "cancelled"],
    "in_progress": ["completed", "cancelled", "escalated"],
    "escalated":   ["in_progress", "completed", "cancelled"],
    "completed":   ["in_progress"],              # 🆕 重开
    "cancelled":   ["pending"],                  # 🆕 恢复
}

STATUS_DISPLAY: dict[str, str] = {
    "pending":     "待处理",
    "in_progress": "处理中",
    "completed":   "已完成",
    "cancelled":   "已取消",
    "escalated":   "已升级",
}

STATUS_COLORS: dict[str, str] = {
    "pending":     "yellow",
    "in_progress": "blue",
    "completed":   "green",
    "cancelled":   "gray",
    "escalated":   "orange",
}

# ═══ Permission table: (from_status, to_status) → who can execute ═══
# "any_staff"  = admin or it_staff can do it
# "owner"      = only the ticket assignee (or admin) can do it
# "admin_only" = only admin

# Permissions are loaded from DB per request via permission_service.
from app.services.permission_service import get_permission, get_admin_only_targets

# Required fields per transition (from_status, to_status) → [field_names]
REQUIRED_FIELDS: dict[tuple[str, str], list[str]] = {}

SLA_HOURS_MAP = {
    "urgent": settings.SLA_HOURS_URGENT,
    "high":   settings.SLA_HOURS_HIGH,
    "medium": settings.SLA_HOURS_MEDIUM,
    "low":    settings.SLA_HOURS_LOW,
}


async def get_sla_map(db: AsyncSession | None = None) -> dict[str, float]:
    """Return priority→hours map, preferring DB config over env settings."""
    if db is not None:
        try:
            from app.services.priority_service import get_priority_hours
            return await get_priority_hours(db)
        except Exception:
            pass
    return dict(SLA_HOURS_MAP)


def business_hours_between(
    start: datetime, end: datetime,
    workdays: list[dict] | None = None,
) -> float:
    """Calculate business hours between two timestamps using workday config.
    workdays: [{day_of_week, is_workday, work_start, work_end}, ...]
    Defaults to Mon-Fri, 8:00-17:00 if not provided."""
    # Strip timezone for comparison
    if start.tzinfo is not None:
        start = start.replace(tzinfo=None)
    if end.tzinfo is not None:
        end = end.replace(tzinfo=None)
    if end <= start:
        return 0.0

    # Build lookup: day_of_week → (is_workday, start_hour, start_min, end_hour, end_min)
    if workdays is None:
        workdays = _default_workdays()

    wd_map = {}
    for w in workdays:
        sh, sm = _parse_time_str(w["work_start"])
        eh, em = _parse_time_str(w["work_end"])
        wd_map[w["day_of_week"]] = (w["is_workday"], sh, sm, eh, em)

    total = 0.0
    current = start.replace(second=0, microsecond=0)

    for _ in range(10000):
        dow = current.weekday()
        cfg = wd_map.get(dow, (False, 8, 0, 17, 0))
        is_wd, sh, sm, eh, em = cfg

        if not is_wd:
            current = current.replace(hour=0, minute=0) + timedelta(days=1)
            continue

        day_start = current.replace(hour=sh, minute=sm)
        day_end = current.replace(hour=eh, minute=em)

        if current < day_start:
            current = day_start

        if current >= day_end:
            current = day_start + timedelta(days=1)
            continue

        if current >= end:
            break

        if end <= day_end:
            total += (end - current).total_seconds() / 3600
            break
        else:
            total += (day_end - current).total_seconds() / 3600
            current = day_start + timedelta(days=1)

    return round(total, 1)


def _default_workdays() -> list[dict]:
    return [
        {"day_of_week": 0, "is_workday": True,  "work_start": "08:00", "work_end": "17:00"},
        {"day_of_week": 1, "is_workday": True,  "work_start": "08:00", "work_end": "17:00"},
        {"day_of_week": 2, "is_workday": True,  "work_start": "08:00", "work_end": "17:00"},
        {"day_of_week": 3, "is_workday": True,  "work_start": "08:00", "work_end": "17:00"},
        {"day_of_week": 4, "is_workday": True,  "work_start": "08:00", "work_end": "17:00"},
        {"day_of_week": 5, "is_workday": False, "work_start": "08:00", "work_end": "17:00"},
        {"day_of_week": 6, "is_workday": False, "work_start": "08:00", "work_end": "17:00"},
    ]


def _parse_time_str(t: str) -> tuple[int, int]:
    parts = t.split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0

# ── Helpers ──

def _build_tsquery(query: str) -> str | None:
    """Build a valid PostgreSQL tsquery string from user input.

    Converts user search terms to tsquery prefix-matching format:
    "打印机 网络" → "打印机:* & 网络:*"
    Returns None if nothing usable remains (fallback to ILIKE).
    """
    import re
    # Remove tsquery special characters but keep basic alphanumeric + CJK
    cleaned = re.sub(r'[!|&():*<>]', ' ', query)
    # Collapse whitespace
    terms = [t.strip() for t in cleaned.split() if t.strip()]
    if not terms:
        return None
    # Prefix match each term for substring-like behaviour
    return " & ".join(f"{term}:*" for term in terms)


def validate_transition(current_status: str, new_status: str, extra_data: dict | None = None) -> None:
    """Raise HTTPException(400) if the transition is invalid."""
    if new_status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"无效的状态值: {new_status}")

    allowed = VALID_TRANSITIONS.get(current_status, [])
    if new_status not in allowed:
        current_label = STATUS_DISPLAY.get(current_status, current_status)
        new_label = STATUS_DISPLAY.get(new_status, new_status)
        raise HTTPException(
            status_code=400,
            detail=f"不允许从「{current_label}」直接变更为「{new_label}」",
        )

    # Check required fields
    field_names = {"resolution_notes": "取消原因"}
    extra_data = extra_data or {}
    for (from_status, to_status), required in REQUIRED_FIELDS.items():
        if (from_status is None or from_status == current_status) and to_status == new_status:
            for field in required:
                value = extra_data.get(field, "").strip() if extra_data.get(field) else ""
                if not value:
                    label = field_names.get(field, field)
                    raise HTTPException(
                        status_code=400,
                        detail=f"变更到「{STATUS_DISPLAY[new_status]}」需要填写{label}",
                    )


def get_valid_transitions(
    current_status: str,
    user_role: str = "admin",
    is_assignee: bool = True,
    perms: dict | None = None,
) -> list[dict]:
    """Return allowed next statuses for this user on this ticket.

    Args:
        current_status: current ticket status
        user_role: 'admin' | 'it_staff' | 'end_user'
        is_assignee: True if this user is the ticket's current assignee
        perms: {(from, to): permission} dict from permission_service.
               If None, falls back to default (all transitions admin_only).
    """
    allowed = VALID_TRANSITIONS.get(current_status, [])
    result = []
    for target in allowed:
        if perms is not None:
            perm = get_permission(perms, current_status, target)
        else:
            perm = "admin_only"
        # Check permission
        if perm == "admin_only" and user_role != "admin":
            continue
        if perm == "owner" and user_role not in ("admin", "it_staff"):
            continue
        if perm == "owner" and user_role == "it_staff" and not is_assignee:
            continue
        result.append({
            "value": target,
            "label": STATUS_DISPLAY.get(target, target),
            "color": STATUS_COLORS.get(target, "gray"),
        })
    return result


def compute_sla_due_at(
    priority: str,
    start_time: datetime | None = None,
    sla_map: dict | None = None,
    workdays: list[dict] | None = None,
) -> datetime | None:
    """Calculate SLA deadline using workday config (defaults Mon-Fri 8-17)."""
    hours_map = sla_map or SLA_HOURS_MAP
    hours = hours_map.get(priority)
    if hours is None:
        return None

    current = (start_time or now()).replace(second=0, microsecond=0)
    orig_tz = current.tzinfo
    if current.tzinfo is not None:
        current = current.replace(tzinfo=None)
    remaining = float(hours)

    if workdays is None:
        workdays = _default_workdays()

    wd_map = {}
    for w in workdays:
        sh, sm = _parse_time_str(w["work_start"])
        eh, em = _parse_time_str(w["work_end"])
        wd_map[w["day_of_week"]] = (w["is_workday"], sh, sm, eh, em)

    for _ in range(10000):
        dow = current.weekday()
        cfg = wd_map.get(dow, (False, 8, 0, 17, 0))
        is_wd, sh, sm, eh, em = cfg

        if not is_wd:
            current = current.replace(hour=0, minute=0) + timedelta(days=1)
            continue

        day_start = current.replace(hour=sh, minute=sm)
        day_end = current.replace(hour=eh, minute=em)

        if current < day_start:
            current = day_start
        if current >= day_end:
            current = day_start + timedelta(days=1)
            continue

        time_left = (day_end - current).total_seconds() / 3600
        if time_left <= 0:
            current = day_start + timedelta(days=1)
            continue

        if remaining <= time_left:
            result = current + timedelta(hours=remaining)
            return result.replace(tzinfo=orig_tz) if orig_tz else result
        else:
            remaining -= time_left
            current = day_start + timedelta(days=1)

    # Fallback
    result = (start_time or now()) + timedelta(hours=hours)
    return result.replace(tzinfo=orig_tz) if orig_tz else result


async def _generate_ticket_number(db: AsyncSession) -> str:
    """Generate sequential ticket number IT-YYYY-NNNN. Idempotent with ON CONFLICT."""
    year = now().year

    # Ensure year row exists
    await db.execute(
        text(
            "INSERT INTO ticket_number_sequences (year, next_number) "
            "VALUES (:year, 1) "
            "ON CONFLICT (year) DO NOTHING"
        ),
        {"year": year},
    )
    await db.flush()

    # Read current value
    result = await db.execute(
        text("SELECT next_number FROM ticket_number_sequences WHERE year = :year"),
        {"year": year},
    )
    current = result.scalar_one()

    # Increment
    await db.execute(
        text("UPDATE ticket_number_sequences SET next_number = next_number + 1 WHERE year = :year"),
        {"year": year},
    )
    await db.flush()

    return f"IT-{year}-{current:04d}"


# ── Event recording ──

async def record_event(
    db: AsyncSession,
    ticket: Ticket,
    event_type: str,
    message: str,
    actor: str | None = None,
) -> TicketEvent:
    event = TicketEvent(ticket=ticket, event_type=event_type, message=message, actor=actor)
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


# ── CRUD ──

async def create_ticket(db: AsyncSession, data: TicketCreate) -> Ticket:
    ticket = Ticket(
        title=data.title,
        description=data.description,
        priority=data.priority,
        category=data.category,
        creator_name=data.creator_name,
    )
    # Generate ticket number and SLA
    ticket.ticket_number = await _generate_ticket_number(db)
    sla_map = await get_sla_map(db)
    ticket.sla_due_at = compute_sla_due_at(data.priority, sla_map=sla_map)

    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    await record_event(
        db, ticket,
        event_type="created",
        message=f"工单已创建 [{ticket.ticket_number}]",
        actor=data.creator_name,
    )
    return ticket


async def get_ticket(db: AsyncSession, ticket_id: int) -> Ticket:
    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.events), selectinload(Ticket.attachments))
        .where(Ticket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    return ticket


async def update_ticket(db: AsyncSession, ticket_id: int, data: TicketUpdate) -> Ticket:
    ticket = await get_ticket(db, ticket_id)
    update_data = data.model_dump(exclude_unset=True)
    changes = []
    assignee_changed = False
    old_assignee = ticket.assignee
    field_labels = {
        "title": "标题",
        "description": "描述",
        "priority": "优先级",
        "category": "分类",
        "assignee": "指派人",
        "resolution_notes": "处理结果",
    }
    for key, value in update_data.items():
        if getattr(ticket, key) != value:
            old_val = getattr(ticket, key) or "空"
            new_val = value or "空"
            label = field_labels.get(key, key)
            changes.append(f"「{label}」从「{old_val}」变更为「{new_val}」")
            if key == "assignee":
                assignee_changed = True
            setattr(ticket, key, value)
            # Recalculate SLA when priority changes (only for open tickets)
            if key == "priority" and ticket.status not in ("completed", "cancelled"):
                sla_map = await get_sla_map(db)
                ticket.sla_due_at = compute_sla_due_at(value, sla_map=sla_map)
    if changes:
        ticket.updated_at = now()
        await db.commit()
        await db.refresh(ticket)
        await record_event(
            db, ticket,
            event_type="updated",
            message=f"工单字段更新：{'；'.join(changes)}",
            actor=ticket.assignee or ticket.creator_name,
        )
        # Also record dedicated assignee_change event for proper audit trail
        if assignee_changed:
            action = "指派给" if ticket.assignee else "取消指派"
            target = ticket.assignee or old_assignee or ""
            await record_event(
                db, ticket,
                event_type="assignee_change",
                message=f"{action}「{target}」",
                actor=ticket.assignee or ticket.creator_name,
            )
    return ticket


async def update_ticket_status(
    db: AsyncSession,
    ticket_id: int,
    status: str,
    resolution_notes: str = "",
    actor: str | None = None,
) -> Ticket:
    ticket = await get_ticket(db, ticket_id)
    old_status = ticket.status

    # Validate
    validate_transition(old_status, status, {"resolution_notes": resolution_notes})

    ticket.status = status
    ticket.updated_at = now()

    # First response tracking
    if status == "in_progress" and ticket.first_response_at is None:
        ticket.first_response_at = now()

    # Reopen: completed → in_progress — reset SLA clock
    if old_status == "completed" and status == "in_progress":
        ticket.sla_due_at = compute_sla_due_at(ticket.priority)
        ticket.resolved_at = None

    # Escalation: in_progress → escalated — reset SLA clock
    if old_status == "in_progress" and status == "escalated":
        ticket.sla_due_at = compute_sla_due_at(ticket.priority)

    # Completion / terminal
    if status in ("completed", "cancelled"):
        ticket.resolved_at = now()
        if resolution_notes:
            ticket.resolution_notes = resolution_notes

    await db.commit()
    await db.refresh(ticket)

    await record_event(
        db, ticket,
        event_type="status_change",
        message=f"状态从「{STATUS_DISPLAY.get(old_status, old_status)}」改为「{STATUS_DISPLAY.get(status, status)}」",
        actor=actor or ticket.assignee or ticket.creator_name,
    )
    return ticket


async def update_ticket_assignee(
    db: AsyncSession, ticket_id: int, assignee: str | None, actor: str | None = None
) -> Ticket:
    ticket = await get_ticket(db, ticket_id)
    old_assignee = ticket.assignee
    ticket.assignee = assignee if assignee else None
    ticket.updated_at = now()
    await db.commit()
    await db.refresh(ticket)

    action = "指派给" if ticket.assignee else "取消指派"
    target = ticket.assignee or old_assignee or ""
    await record_event(
        db, ticket,
        event_type="assignee_change",
        message=f"{action}「{target}」",
        actor=actor or ticket.assignee or ticket.creator_name,
    )
    return ticket


async def add_ticket_comment(
    db: AsyncSession, ticket_id: int, comment: str, actor: str | None = None
) -> TicketEvent:
    ticket = await get_ticket(db, ticket_id)
    event = TicketEvent(ticket=ticket, event_type="comment", message=comment, actor=actor or "系统")
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


# ── List with FTS5 search ──

async def list_tickets(
    db: AsyncSession,
    status: str | None = None,
    q: str | None = None,
    assignee: str | None = None,
    category: str | None = None,
    priority: str | None = None,
    page: int = 1,
    page_size: int = 20,
    sla: str | None = None,
    year_month: str | None = None,
    restrict_assignee: str | None = None,
) -> dict:
    query = select(Ticket)

    # Role-based restriction: non-admin users only see their own tickets
    if restrict_assignee:
        query = query.where(func.lower(Ticket.assignee) == restrict_assignee.lower())

    if status and status in VALID_STATUSES:
        query = query.where(Ticket.status == status)

    if assignee:
        query = query.where(func.lower(Ticket.assignee) == assignee.lower())

    if category:
        query = query.where(func.lower(Ticket.category) == category.lower())

    if priority:
        query = query.where(func.lower(Ticket.priority) == priority.lower())

    # SLA filter
    if sla:
        china_now = now()
        if sla == "ontime":
            query = query.where(Ticket.status == "completed", Ticket.sla_due_at.isnot(None), Ticket.resolved_at <= Ticket.sla_due_at)
        elif sla == "breached":
            query = query.where(Ticket.status == "completed", Ticket.sla_due_at.isnot(None), Ticket.resolved_at > Ticket.sla_due_at)
        elif sla == "pending":
            query = query.where(Ticket.status.in_(["pending", "in_progress"]), Ticket.sla_due_at.isnot(None), Ticket.sla_due_at > china_now)
        elif sla == "overdue":
            query = query.where(Ticket.status.in_(["pending", "in_progress"]), Ticket.sla_due_at.isnot(None), Ticket.sla_due_at <= china_now)

    # Year-month filter
    if year_month and year_month != "all":
        from app.db_migrations import _is_pg
        if _is_pg():
            query = query.where(func.to_char(Ticket.created_at, 'YYYY-MM') == year_month)
        else:
            query = query.where(func.strftime('%Y-%m', Ticket.created_at) == year_month)

    # Search — use PostgreSQL tsvector or fallback to ILIKE
    if q:
        from app.db_migrations import _is_pg
        tsquery_str = _build_tsquery(q)
        if tsquery_str and _is_pg():
            from sqlalchemy import literal
            query = query.where(
                Ticket.search_vector.op("@@")(
                    func.to_tsquery(literal("simple"), tsquery_str)
                )
            )
        else:
            q_like = f"%{q}%"
            query = query.where(
                or_(
                    Ticket.ticket_number.ilike(q_like),
                    Ticket.title.ilike(q_like),
                    Ticket.description.ilike(q_like),
                    Ticket.creator_name.ilike(q_like),
                    Ticket.assignee.ilike(q_like),
                )
            )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    result = await db.execute(count_query)
    total = result.scalar_one()

    total_pages = max(1, math.ceil(total / page_size))

    # Paginate — smart sort: open/urgent/SLA-critical first
    sort_now = now()
    sort_24h = sort_now + timedelta(hours=24)

    from sqlalchemy import case
    priority_rank = case(
        (Ticket.priority == "urgent", 0),
        (Ticket.priority == "high",   1),
        (Ticket.priority == "medium", 2),
        (Ticket.priority == "low",    3),
        else_=9,
    )
    is_open = case(
        (Ticket.status.in_(["pending", "in_progress", "escalated"]), 0),
        else_=1,
    )
    sla_overdue = case(
        (Ticket.sla_due_at.isnot(None) & (Ticket.sla_due_at < sort_now), 0),
        else_=1,
    )
    sla_near = case(
        (Ticket.sla_due_at.isnot(None)
         & (Ticket.sla_due_at >= sort_now)
         & (Ticket.sla_due_at < sort_24h), 0),
        else_=1,
    )
    query = query.order_by(
        is_open,
        priority_rank,
        sla_overdue,
        sla_near,
        Ticket.sla_due_at.asc().nulls_last(),
        Ticket.updated_at.desc().nulls_last(),
        Ticket.created_at.desc(),
    ).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    tickets = result.scalars().all()

    return {
        "items": list(tickets),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }
