from __future__ import annotations

from datetime import datetime, timedelta
from app.timezone_helper import now

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import Ticket, TicketEvent

OPEN_STATUSES = ["pending", "in_progress", "escalated"]
CLOSED_STATUS = "completed"


def _apply_filter(query, assignee: str | None):
    if assignee:
        return query.where(Ticket.assignee == assignee)
    return query


async def get_status_distribution(db: AsyncSession, assignee: str | None = None) -> list[dict]:
    query = select(Ticket.status, func.count(Ticket.id)).where(Ticket.status != "cancelled").group_by(Ticket.status)
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    return [{"status": row[0], "count": row[1]} for row in result.all()]


async def get_priority_distribution(db: AsyncSession, assignee: str | None = None) -> list[dict]:
    query = select(Ticket.priority, func.count(Ticket.id)).where(Ticket.status != "cancelled").group_by(Ticket.priority)
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    return [{"priority": row[0], "count": row[1]} for row in result.all()]


async def get_category_distribution(db: AsyncSession, assignee: str | None = None) -> list[dict]:
    query = (
        select(Ticket.category, func.count(Ticket.id))
        .where(Ticket.category.isnot(None), Ticket.status != "cancelled")
        .group_by(Ticket.category)
        .order_by(func.count(Ticket.id).desc())
    )
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    return [{"category": row[0], "count": row[1]} for row in result.all()]


async def get_assignee_workload(db: AsyncSession, assignee: str | None = None, limit: int = 10) -> list[dict]:
    query = (
        select(Ticket.assignee, func.count(Ticket.id))
        .where(Ticket.assignee.isnot(None), Ticket.status.in_(OPEN_STATUSES))
        .group_by(Ticket.assignee)
        .order_by(func.count(Ticket.id).desc())
        .limit(limit)
    )
    if assignee:
        query = query.where(Ticket.assignee == assignee)
    result = await db.execute(query)
    return [{"assignee": row[0], "count": row[1]} for row in result.all()]


async def get_sla_metrics(db: AsyncSession, assignee: str | None = None) -> dict:
    current_time = now().replace(tzinfo=None)

    query = select(func.count(Ticket.id)).where(Ticket.status == CLOSED_STATUS)
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    total_completed = result.scalar_one()

    query = select(func.count(Ticket.id)).where(
        Ticket.status == CLOSED_STATUS,
        Ticket.sla_due_at.isnot(None),
        Ticket.resolved_at <= Ticket.sla_due_at,
    )
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    within_sla = result.scalar_one()

    query = select(func.count(Ticket.id)).where(
        Ticket.status.in_(OPEN_STATUSES),
        Ticket.sla_due_at.isnot(None),
        Ticket.sla_due_at < current_time,
    )
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    breaching_now = result.scalar_one()

    compliance_rate = round(within_sla / total_completed * 100, 1) if total_completed > 0 else 100.0

    return {
        "total_completed": total_completed,
        "within_sla": within_sla,
        "overdue": total_completed - within_sla,
        "breaching_now": breaching_now,
        "compliance_rate": compliance_rate,
    }


async def get_trends(db: AsyncSession, days: int = 30, assignee: str | None = None) -> dict:
    start_date = now() - timedelta(days=days)

    query = select(func.date(Ticket.created_at), func.count(Ticket.id)).where(
        Ticket.created_at >= start_date, Ticket.status != "cancelled"
    ).group_by(func.date(Ticket.created_at)).order_by(func.date(Ticket.created_at))
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    created = {row[0]: row[1] for row in result.all()}

    query = select(func.date(Ticket.resolved_at), func.count(Ticket.id)).where(
        Ticket.resolved_at >= start_date, Ticket.status == CLOSED_STATUS
    ).group_by(func.date(Ticket.resolved_at)).order_by(func.date(Ticket.resolved_at))
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    resolved = {row[0]: row[1] for row in result.all()}

    labels = []
    created_data = []
    resolved_data = []
    for i in range(days - 1, -1, -1):
        d = (now() - timedelta(days=i)).strftime("%Y-%m-%d")
        labels.append(d)
        created_data.append(created.get(d, 0))
        resolved_data.append(resolved.get(d, 0))

    return {"labels": labels, "created": created_data, "resolved": resolved_data}


async def get_summary(db: AsyncSession, assignee: str | None = None) -> dict:
    query = select(func.count(Ticket.id)).where(Ticket.status.in_(OPEN_STATUSES))
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    open_count = result.scalar_one()

    today_str = now().strftime("%Y-%m-%d")

    # Today created
    query = select(func.count(Ticket.id)).where(
        func.date(Ticket.created_at) == today_str, Ticket.status != "cancelled",
    )
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    today_created = result.scalar_one()
    query = select(func.count(Ticket.id)).where(
        func.date(Ticket.resolved_at) == today_str, Ticket.status == CLOSED_STATUS
    )
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    resolved_today = result.scalar_one()

    query = select(func.count(Ticket.id)).where(
        Ticket.status.in_(OPEN_STATUSES),
        Ticket.sla_due_at.isnot(None),
        Ticket.sla_due_at < now().replace(tzinfo=None),
    )
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    breached = result.scalar_one()

    query = select(
        func.avg(func.julianday(Ticket.resolved_at) - func.julianday(Ticket.created_at))
    ).where(Ticket.status == CLOSED_STATUS, Ticket.resolved_at.isnot(None))
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    avg_hours_raw = result.scalar_one()
    avg_hours = round(avg_hours_raw * 24, 1) if avg_hours_raw else 0

    # Total tickets for this person (exclude cancelled)
    query = select(func.count(Ticket.id)).where(Ticket.status != "cancelled")
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    total_count = result.scalar_one()

    return {
        "open_count": open_count,
        "resolved_today": resolved_today,
        "today_created": today_created,
        "net_digest": resolved_today - today_created,
        "breached": breached,
        "avg_resolution_hours": avg_hours,
        "total_count": total_count,
    }


async def get_monthly_sla_trend(db: AsyncSession, months: int = 6, assignee: str | None = None) -> list[dict]:
    """Monthly SLA compliance rate for the past N months."""
    trend = []
    # Walk back one month at a time (avoids 31-day skip bugs)
    cursor = now().replace(day=1)
    month_starts = []
    for _ in range(months):
        month_starts.append(cursor)
        # Go to first day of previous month
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    month_starts.reverse()
    for ms in month_starts:
        me = (ms.replace(day=28) + timedelta(days=4)).replace(day=1)

        query = select(func.count(Ticket.id)).where(
            Ticket.status == CLOSED_STATUS, Ticket.resolved_at >= ms, Ticket.resolved_at < me,
        )
        query = _apply_filter(query, assignee)
        r = await db.execute(query)
        total = r.scalar_one()

        query = select(func.count(Ticket.id)).where(
            Ticket.status == CLOSED_STATUS, Ticket.resolved_at >= ms, Ticket.resolved_at < me,
            Ticket.sla_due_at.isnot(None), Ticket.resolved_at <= Ticket.sla_due_at,
        )
        query = _apply_filter(query, assignee)
        r = await db.execute(query)
        on_time = r.scalar_one()

        rate = round(on_time / total * 100, 1) if total > 0 else 0
        trend.append({"label": ms.strftime("%m月"), "rate": rate, "total": total, "on_time": on_time})
    return trend


async def get_weekly_sla_trend(db: AsyncSession, weeks: int = 12, assignee: str | None = None) -> list[dict]:
    """Weekly SLA compliance rate over the past N weeks."""
    trend = []
    for i in range(weeks - 1, -1, -1):
        week_end = now() - timedelta(weeks=i)
        week_start = week_end - timedelta(days=7)

        query = select(func.count(Ticket.id)).where(
            Ticket.status == CLOSED_STATUS,
            Ticket.resolved_at >= week_start,
            Ticket.resolved_at < week_end,
        )
        query = _apply_filter(query, assignee)
        result = await db.execute(query)
        total = result.scalar_one()

        query = select(func.count(Ticket.id)).where(
            Ticket.status == CLOSED_STATUS,
            Ticket.resolved_at >= week_start,
            Ticket.resolved_at < week_end,
            Ticket.sla_due_at.isnot(None),
            Ticket.resolved_at <= Ticket.sla_due_at,
        )
        query = _apply_filter(query, assignee)
        result = await db.execute(query)
        on_time = result.scalar_one()

        rate = round(on_time / total * 100, 1) if total > 0 else 0
        trend.append({
            "label": week_start.strftime("%m/%d"),
            "total": total,
            "on_time": on_time,
            "rate": rate,
        })
    return trend


async def get_priority_avg_time(db: AsyncSession, assignee: str | None = None) -> list[dict]:
    """Average resolution time by priority."""
    result_list = []
    for p in ["urgent", "high", "medium", "low"]:
        query = select(
            func.avg(func.julianday(Ticket.resolved_at) - func.julianday(Ticket.created_at))
        ).where(Ticket.status == CLOSED_STATUS, Ticket.resolved_at.isnot(None), Ticket.priority == p)
        query = _apply_filter(query, assignee)
        result = await db.execute(query)
        raw = result.scalar_one()
        result_list.append({"priority": p, "avg_hours": round(raw * 24, 1) if raw else 0})
    return result_list


async def get_backlog_trend(db: AsyncSession, days: int = 30, assignee: str | None = None) -> list[dict]:
    """Daily backlog count for same period as trends."""
    trend = []
    for i in range(days - 1, -1, -1):
        day_end = now() - timedelta(days=i)
        query = select(func.count(Ticket.id)).where(
            Ticket.status.in_(OPEN_STATUSES), Ticket.created_at < day_end,
        )
        query = _apply_filter(query, assignee)
        r = await db.execute(query)
        trend.append({"backlog": r.scalar_one()})
    return trend


async def get_first_response_time(db: AsyncSession, assignee: str | None = None) -> float:
    """Average hours from creation to first in_progress (for tickets that reached in_progress)."""
    query = select(
        func.avg(func.julianday(Ticket.first_response_at) - func.julianday(Ticket.created_at))
    ).where(Ticket.first_response_at.isnot(None), Ticket.status != "cancelled")
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    raw = result.scalar_one()
    return round(raw * 24, 1) if raw else 0


async def get_category_expertise(db: AsyncSession, assignee: str | None = None) -> list[dict]:
    """Per-assignee category distribution (for radar chart)."""
    from app.models.user import User
    from app.models.category import Category

    result = await db.execute(
        select(User.display_name).where(User.role.in_(["admin", "it_staff"]), User.is_active == True)
    )
    staff = [row[0] for row in result.all()]
    # Fetch categories from DB, not hardcoded
    r = await db.execute(
        select(Category.name).where(Category.is_active == True).order_by(Category.sort_order)
    )
    categories = [row[0] for row in r.all()]
    if not categories:
        categories = ["硬件", "软件", "网络", "账号", "其他"]
    result_list = []
    for s in staff:
        row_data = {"assignee": s}
        for cat in categories:
            query = select(func.count(Ticket.id)).where(
                Ticket.assignee == s, Ticket.category == cat, Ticket.status != "cancelled"
            )
            r = await db.execute(query)
            row_data[cat] = r.scalar_one()
        result_list.append(row_data)
    return result_list


async def get_monthly_leaderboard(db: AsyncSession, offset: int = 0) -> list[dict]:
    """Monthly composite score ranking with offset (0=current, -1=last, etc.)."""
    from app.models.user import User
    result = await db.execute(
        select(User.display_name).where(User.role.in_(["admin", "it_staff"]), User.is_active == True)
    )
    staff = [row[0] for row in result.all()]
    if not staff:
        return []
    current = now()
    # Calculate target month
    y, m = current.year, current.month
    m += offset
    while m < 1: y -= 1; m += 12
    while m > 12: y += 1; m -= 12
    month_start = datetime(y, m, 1, tzinfo=current.tzinfo)
    month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    # Get category weight map for weighted scoring
    from app.models.category import Category
    weight_r = await db.execute(select(Category.name, Category.complexity_weight))
    cat_weights = {row[0]: row[1] for row in weight_r.all()}

    raw_data = []
    for s in staff:
        query = select(func.count(Ticket.id)).where(
            Ticket.assignee == s, Ticket.status == CLOSED_STATUS, Ticket.resolved_at >= month_start, Ticket.resolved_at < month_end
        )
        r = await db.execute(query)
        completed = r.scalar_one()

        # Weighted completion: sum category weights for completed tickets
        weighted_r = await db.execute(
            select(func.coalesce(func.sum(Category.complexity_weight), 0)).where(
                Ticket.assignee == s, Ticket.status == CLOSED_STATUS,
                Ticket.resolved_at >= month_start, Ticket.resolved_at < month_end,
                Ticket.category == Category.name,
            )
        )
        weighted_completed = round(weighted_r.scalar_one(), 1)

        query = select(func.count(Ticket.id)).where(
            Ticket.assignee == s, Ticket.status == CLOSED_STATUS,
            Ticket.resolved_at >= month_start, Ticket.resolved_at < month_end, Ticket.sla_due_at.isnot(None),
            Ticket.resolved_at <= Ticket.sla_due_at,
        )
        r = await db.execute(query)
        on_time = r.scalar_one()
        sla_rate = round(on_time / completed * 100, 1) if completed > 0 else 0

        query = select(
            func.avg(func.julianday(Ticket.resolved_at) - func.julianday(Ticket.created_at))
        ).where(
            Ticket.assignee == s, Ticket.status == CLOSED_STATUS,
            Ticket.resolved_at >= month_start, Ticket.resolved_at < month_end, Ticket.resolved_at.isnot(None),
        )
        r = await db.execute(query)
        raw = r.scalar_one()
        avg_hours = round(raw * 24, 1) if raw else 0

        raw_data.append({
            "assignee": s, "completed": completed, "weighted": weighted_completed,
            "sla_rate": sla_rate, "avg_hours": avg_hours,
        })

    # Normalize and compute composite score (use weighted for completion dimension)
    if raw_data:
        max_weighted = max(d["weighted"] for d in raw_data) or 1
        max_avg = max(d["avg_hours"] for d in raw_data) or 1
        for d in raw_data:
            completed_score = (d["weighted"] / max_weighted) * 40 if max_weighted else 0
            sla_score = (d["sla_rate"] / 100) * 35
            speed_score = ((1 - d["avg_hours"] / max_avg) * 25) if max_avg else 25
            d["score"] = round(completed_score + sla_score + speed_score, 1)

    raw_data.sort(key=lambda x: x["score"], reverse=True)
    return raw_data


async def get_active_tickets(db: AsyncSession, assignee: str | None = None) -> list[dict]:
    """Currently in-progress tickets per assignee."""
    query = select(Ticket.assignee, Ticket.ticket_number, Ticket.title, Ticket.priority, Ticket.sla_due_at).where(
        Ticket.status == "in_progress"
    ).order_by(Ticket.created_at.desc())
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    tickets = []
    for row in result.all():
        tickets.append({
            "assignee": row[0], "ticket_number": row[1], "title": row[2],
            "priority": row[3], "sla_due_at": row[4],
        })
    return tickets


async def get_mom_comparison(db: AsyncSession, assignee: str | None = None) -> dict:
    """Month-over-month comparison."""
    this_start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_start = (this_start - timedelta(days=1)).replace(day=1)
    last_end = this_start

    def _count(start, end, status=None, sla=False):
        q = select(func.count(Ticket.id)).where(Ticket.created_at >= start, Ticket.created_at < end)
        q = _apply_filter(q, assignee)
        if status:
            q = q.where(Ticket.status == CLOSED_STATUS)
        if sla:
            q = q.where(Ticket.status == CLOSED_STATUS, Ticket.sla_due_at.isnot(None),
                        Ticket.resolved_at <= Ticket.sla_due_at)
        return q

    def _avg_time(start, end, field):
        q = select(func.avg(func.julianday(field) - func.julianday(Ticket.created_at))).where(
            Ticket.created_at >= start, Ticket.created_at < end,
            Ticket.status == CLOSED_STATUS, field.isnot(None),
        )
        q = _apply_filter(q, assignee)
        return q

    # This month
    r = await db.execute(_count(this_start, now()))
    tm_created = r.scalar_one()
    r = await db.execute(_count(this_start, now(), True))
    tm_completed = r.scalar_one()
    r = await db.execute(_count(this_start, now(), True, True))
    tm_sla_ok = r.scalar_one()
    r = await db.execute(_avg_time(this_start, now(), Ticket.first_response_at))
    raw = r.scalar_one()
    tm_resp = round(raw * 24, 1) if raw else 0
    r = await db.execute(_avg_time(this_start, now(), Ticket.resolved_at))
    raw = r.scalar_one()
    tm_avg = round(raw * 24, 1) if raw else 0

    # Last month
    r = await db.execute(_count(last_start, last_end))
    lm_created = r.scalar_one()
    r = await db.execute(_count(last_start, last_end, True))
    lm_completed = r.scalar_one()
    r = await db.execute(_count(last_start, last_end, True, True))
    lm_sla_ok = r.scalar_one()
    r = await db.execute(_avg_time(last_start, last_end, Ticket.first_response_at))
    raw = r.scalar_one()
    lm_resp = round(raw * 24, 1) if raw else 0
    r = await db.execute(_avg_time(last_start, last_end, Ticket.resolved_at))
    raw = r.scalar_one()
    lm_avg = round(raw * 24, 1) if raw else 0

    def pct(new, old):
        if old == 0: return 100 if new > 0 else 0
        return round((new - old) / old * 100, 1)

    def delta(new, old):
        d = round(new - old, 1)
        return f"+{d}" if d > 0 else str(d)

    return {
        "this_month": {"created": tm_created, "completed": tm_completed,
                       "sla_rate": round(tm_sla_ok/tm_completed*100,1) if tm_completed>0 else 0,
                       "first_response": tm_resp, "avg_resolution": tm_avg},
        "last_month": {"created": lm_created, "completed": lm_completed,
                       "sla_rate": round(lm_sla_ok/lm_completed*100,1) if lm_completed>0 else 0,
                       "first_response": lm_resp, "avg_resolution": lm_avg},
        "change": {
            "created": pct(tm_created, lm_created),
            "completed": pct(tm_completed, lm_completed),
            "first_response": delta(tm_resp, lm_resp),
            "avg_resolution": delta(tm_avg, lm_avg),
        },
        "sla_improved": (tm_sla_ok/tm_completed if tm_completed>0 else 0) >= (lm_sla_ok/lm_completed if lm_completed>0 else 0),
        "sla_unchanged": abs(
            (round(tm_sla_ok/tm_completed*100,1) if tm_completed>0 else 0) -
            (round(lm_sla_ok/lm_completed*100,1) if lm_completed>0 else 0)
        ) < 0.01,
        "resp_improved": tm_resp <= lm_resp if lm_resp > 0 else True,
        "resp_unchanged": abs(tm_resp - lm_resp) < 0.01,
        "avg_improved": tm_avg <= lm_avg if lm_avg > 0 else True,
        "avg_unchanged": abs(tm_avg - lm_avg) < 0.01,
        "this_label": this_start.strftime("%Y年%m月"),
        "last_label": last_start.strftime("%Y年%m月"),
    }


# ═══════════════════════════════════════════════════════════════
# P0+P1 新增: 个人KPI · 超期明细 · 活动流 · 响应分布 · 负载均衡
# ═══════════════════════════════════════════════════════════════


async def get_personal_kpi_detail(
    db: AsyncSession, assignee: str, year: int | None = None, month: int | None = None
) -> dict:
    """Detailed KPIs for one IT staff member."""
    current = now()
    if year is None:
        year = current.year
    if month is None:
        month = current.month
    month_start = datetime(year, month, 1, tzinfo=current.tzinfo)
    month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)

    # Total completed this month
    r = await db.execute(
        select(func.count(Ticket.id)).where(
            Ticket.assignee == assignee,
            Ticket.status == CLOSED_STATUS,
            Ticket.resolved_at >= month_start,
            Ticket.resolved_at < month_end,
        )
    )
    completed = r.scalar_one()

    # SLA on-time
    r = await db.execute(
        select(func.count(Ticket.id)).where(
            Ticket.assignee == assignee,
            Ticket.status == CLOSED_STATUS,
            Ticket.resolved_at >= month_start,
            Ticket.resolved_at < month_end,
            Ticket.sla_due_at.isnot(None),
            Ticket.resolved_at <= Ticket.sla_due_at,
        )
    )
    on_time = r.scalar_one()
    sla_rate = round(on_time / completed * 100, 1) if completed > 0 else 0

    # Average resolution hours
    r = await db.execute(
        select(func.avg(func.julianday(Ticket.resolved_at) - func.julianday(Ticket.created_at))).where(
            Ticket.assignee == assignee,
            Ticket.status == CLOSED_STATUS,
            Ticket.resolved_at >= month_start,
            Ticket.resolved_at < month_end,
            Ticket.resolved_at.isnot(None),
        )
    )
    raw = r.scalar_one()
    avg_hours = round(raw * 24, 1) if raw else 0

    # First response time
    r = await db.execute(
        select(func.avg(func.julianday(Ticket.first_response_at) - func.julianday(Ticket.created_at))).where(
            Ticket.assignee == assignee,
            Ticket.first_response_at.isnot(None),
            Ticket.first_response_at >= month_start,
            Ticket.first_response_at < month_end,
        )
    )
    raw = r.scalar_one()
    first_resp = round(raw * 24, 1) if raw else 0

    # 退回次数: escalated/completed → in_progress (the assignee took it back)
    r2 = await db.execute(
        select(func.count(TicketEvent.id)).where(
            TicketEvent.event_type == "status_change",
            TicketEvent.message.contains("改为「处理中」"),
            TicketEvent.actor == assignee,
            TicketEvent.created_at >= month_start,
            TicketEvent.created_at < month_end,
        )
    )
    returns_count = r2.scalar_one()

    # Escalation rate — count status_change events to escalated
    r = await db.execute(
        select(func.count(TicketEvent.id)).where(
            TicketEvent.event_type == "status_change",
            TicketEvent.message.contains("改为「已升级」"),
            TicketEvent.actor == assignee,
            TicketEvent.created_at >= month_start,
            TicketEvent.created_at < month_end,
        )
    )
    escalations_out = r.scalar_one()

    # Daily completion breakdown within this month
    daily = []
    day = month_start
    end = month_end
    while day < end:
        next_day = min(day + timedelta(days=1), end)
        r = await db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.assignee == assignee,
                Ticket.status == CLOSED_STATUS,
                Ticket.resolved_at >= day,
                Ticket.resolved_at < next_day,
            )
        )
        daily.append({"date": day.strftime("%m/%d"), "count": r.scalar_one()})
        day = next_day

    # Per-priority breakdown
    priority_breakdown = []
    for p in ["urgent", "high", "medium", "low"]:
        r = await db.execute(
            select(
                func.count(Ticket.id),
                func.avg(func.julianday(Ticket.resolved_at) - func.julianday(Ticket.created_at)),
            ).where(
                Ticket.assignee == assignee,
                Ticket.status == CLOSED_STATUS,
                Ticket.priority == p,
                Ticket.resolved_at >= month_start,
                Ticket.resolved_at < month_end,
                Ticket.resolved_at.isnot(None),
            )
        )
        row = r.one()
        raw_avg = row[1]
        priority_breakdown.append({
            "priority": p,
            "count": row[0],
            "avg_hours": round(raw_avg * 24, 1) if raw_avg else 0,
        })

    return {
        "assignee": assignee,
        "month_label": month_start.strftime("%Y年%m月"),
        "completed": completed,
        "on_time": on_time,
        "sla_rate": sla_rate,
        "avg_hours": avg_hours,
        "first_response": first_resp,
        "returns_": returns_count,
        "escalations_out": escalations_out,
        "daily_completion": daily,
        "priority_breakdown": priority_breakdown,
    }


async def get_all_staff_kpi(
    db: AsyncSession, year: int | None = None, month: int | None = None
) -> list[dict]:
    """Get KPI detail for all active staff members."""
    from app.models.user import User
    r = await db.execute(
        select(User.display_name).where(User.role.in_(["admin", "it_staff"]), User.is_active == True)
    )
    staff = [row[0] for row in r.all()]
    results = []
    for s in staff:
        kpi = await get_personal_kpi_detail(db, s, year, month)
        results.append(kpi)
    return results


async def get_overdue_tickets_detail(
    db: AsyncSession, assignee: str | None = None
) -> list[dict]:
    """Overdue tickets with full detail."""
    current = now().replace(tzinfo=None)
    query = (
        select(Ticket)
        .where(
            Ticket.status.in_(OPEN_STATUSES),
            Ticket.sla_due_at.isnot(None),
            Ticket.sla_due_at < current,
        )
        .order_by(Ticket.sla_due_at.asc())
    )
    if assignee:
        query = query.where(Ticket.assignee == assignee)
    r = await db.execute(query)
    tickets = r.scalars().all()

    def _to_dt(val):
        """Ensure timezone-aware datetime."""
        if val is None:
            return None
        if val.tzinfo is None:
            return val.replace(tzinfo=current.tzinfo)
        return val

    return [
        {
            "id": t.id,
            "ticket_number": t.ticket_number,
            "title": t.title,
            "assignee": t.assignee,
            "priority": t.priority,
            "status": t.status,
            "sla_due_at": t.sla_due_at.strftime("%Y-%m-%d %H:%M") if t.sla_due_at else "",
            "created_at": t.created_at.strftime("%Y-%m-%d %H:%M"),
            "overdue_hours": round((current - _to_dt(t.sla_due_at)).total_seconds() / 3600, 1) if t.sla_due_at else 0,
        }
        for t in tickets
    ]


async def get_recent_activity(
    db: AsyncSession, limit: int = 50, assignee: str | None = None
) -> list[dict]:
    """Recent ticket events as activity feed."""
    current = now()
    query = (
        select(TicketEvent, Ticket.ticket_number, Ticket.title)
        .join(Ticket, TicketEvent.ticket_id == Ticket.id)
        .where(TicketEvent.created_at >= current - timedelta(days=7))
        .order_by(TicketEvent.created_at.desc())
        .limit(limit)
    )
    r = await db.execute(query)
    rows = r.all()
    activities = []
    for evt, tn, title in rows:
        if assignee and evt.actor != assignee:
            continue
        activities.append({
            "actor": evt.actor,
            "event_type": evt.event_type,
            "message": evt.message,
            "ticket_number": tn,
            "ticket_title": title,
            "created_at": evt.created_at.strftime("%m/%d %H:%M"),
            "created_at_iso": evt.created_at.isoformat(),
        })
    return activities


async def get_response_time_distribution(
    db: AsyncSession, assignee: str | None = None
) -> dict:
    """Response time distribution in buckets (hours)."""
    buckets = [
        ("≤1h", 0, 1),
        ("≤4h", 1, 4),
        ("≤8h", 4, 8),
        ("≤24h", 8, 24),
        (">24h", 24, 9999),
    ]
    total = 0
    dist = []
    for label, lo, hi in buckets:
        query = select(func.count(Ticket.id)).where(
            Ticket.first_response_at.isnot(None),
            Ticket.status != "cancelled",
            func.julianday(Ticket.first_response_at) - func.julianday(Ticket.created_at) > lo / 24.0,
            func.julianday(Ticket.first_response_at) - func.julianday(Ticket.created_at) <= hi / 24.0,
        )
        if assignee:
            query = query.where(Ticket.assignee == assignee)
        r = await db.execute(query)
        count = r.scalar_one()
        total += count
        dist.append({"label": label, "count": count, "lo": lo, "hi": hi})
    # Add percentages
    for d in dist:
        d["pct"] = round(d["count"] / total * 100, 1) if total > 0 else 0
    return {"buckets": dist, "total": total}


async def get_workload_balance(
    db: AsyncSession, assignee: str | None = None
) -> list[dict]:
    """Current workload per staff member with open ticket details."""
    from app.models.user import User
    r = await db.execute(
        select(User.display_name).where(User.role.in_(["admin", "it_staff"]), User.is_active == True)
    )
    staff = [row[0] for row in r.all()]
    if not staff:
        return []

    workloads = []
    for s in staff:
        if assignee and s != assignee:
            continue
        # Open tickets
        r = await db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.assignee == s, Ticket.status.in_(OPEN_STATUSES)
            )
        )
        open_count = r.scalar_one()
        # Overdue
        r = await db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.assignee == s,
                Ticket.status.in_(OPEN_STATUSES),
                Ticket.sla_due_at.isnot(None),
                Ticket.sla_due_at < now().replace(tzinfo=None),
            )
        )
        overdue = r.scalar_one()
        # Urgent open
        r = await db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.assignee == s,
                Ticket.status.in_(OPEN_STATUSES),
                Ticket.priority == "urgent",
            )
        )
        urgent = r.scalar_one()
        # Completed this week
        week_start = now() - timedelta(days=now().weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        r = await db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.assignee == s,
                Ticket.status == CLOSED_STATUS,
                Ticket.resolved_at >= week_start,
            )
        )
        week_completed = r.scalar_one()

        workloads.append({
            "assignee": s,
            "open_count": open_count,
            "overdue": overdue,
            "urgent": urgent,
            "week_completed": week_completed,
        })

    max_open = max(w["open_count"] for w in workloads) or 1
    for w in workloads:
        w["load_pct"] = round(w["open_count"] / max_open * 100) if max_open else 0
        # Load level
        if w["load_pct"] >= 80:
            w["load_level"] = "high"
            w["load_color"] = "red"
        elif w["load_pct"] >= 50:
            w["load_level"] = "normal"
            w["load_color"] = "blue"
        else:
            w["load_level"] = "low"
            w["load_color"] = "green"

    workloads.sort(key=lambda x: x["open_count"], reverse=True)
    return workloads


# ═══════════════════════════════════════════════════════════════
# P3: 分类效率对比
# ═══════════════════════════════════════════════════════════════


async def get_category_efficiency_comparison(
    db: AsyncSession, assignee: str | None = None, months: int = 3
) -> dict:
    """Per-category average resolution time by staff member (recent N months only).

    Returns data suitable for a grouped bar chart.
    """
    from app.models.user import User
    from app.models.category import Category

    # Time window
    cutoff = now() - timedelta(days=months * 30)
    cutoff = cutoff.replace(tzinfo=None)

    # Active staff
    r = await db.execute(
        select(User.display_name).where(User.role.in_(["admin", "it_staff"]), User.is_active == True)
    )
    staff = [row[0] for row in r.all()]
    if assignee:
        staff = [s for s in staff if s == assignee]
    if not staff:
        return {"categories": [], "staff": [], "matrix": {}}

    # Active categories
    r = await db.execute(
        select(Category.name).where(Category.is_active == True).order_by(Category.sort_order)
    )
    categories = [row[0] for row in r.all()]
    if not categories:
        return {"categories": [], "staff": [], "matrix": {}}

    # Build matrix: {category: {staff_name: avg_hours}}
    matrix: dict[str, dict[str, float]] = {cat: {} for cat in categories}
    for cat in categories:
        for s in staff:
            r = await db.execute(
                select(
                    func.avg(func.julianday(Ticket.resolved_at) - func.julianday(Ticket.created_at))
                ).where(
                    Ticket.category == cat,
                    Ticket.assignee == s,
                    Ticket.status == CLOSED_STATUS,
                    Ticket.resolved_at.isnot(None),
                    Ticket.resolved_at >= cutoff,
                )
            )
            raw = r.scalar_one()
            # Cap at 72h (3 business days) to filter unrealistic imported data
            avg_hours = min(round(raw * 24, 1) if raw else 0, 72)
            matrix[cat][s] = avg_hours

    return {
        "categories": categories,
        "staff": staff,
        "matrix": matrix,
    }


# ═══════════════════════════════════════════════════════════════
# P2: 个人日历热力图 (GitHub-style contribution grid)
# ═══════════════════════════════════════════════════════════════


async def get_personal_heatmap(
    db: AsyncSession, assignee: str | None = None, months: int = 3
) -> dict:
    """Daily completion counts for heatmap. Returns weeks × day_of_week grid."""
    current = now()
    end_date = current.replace(hour=23, minute=59, second=59, microsecond=999999)
    start_date = (end_date - timedelta(days=months * 31)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    query = select(
        func.date(Ticket.resolved_at),
        func.count(Ticket.id),
    ).where(
        Ticket.status == CLOSED_STATUS,
        Ticket.resolved_at >= start_date,
        Ticket.resolved_at <= end_date,
    )
    if assignee:
        query = query.where(Ticket.assignee == assignee)
    query = query.group_by(func.date(Ticket.resolved_at))
    r = await db.execute(query)
    daily = {row[0]: row[1] for row in r.all()}

    # Build week grid (like GitHub: columns=weeks, rows=day of week)
    # Align to Monday=0 ... Sunday=6
    weeks = []
    cursor = start_date
    current_week = [None] * 7  # Mon-Sun
    # Shift start to previous Monday
    weekday = cursor.weekday()
    if weekday > 0:
        cursor = cursor - timedelta(days=weekday)
    end = end_date

    while cursor <= end:
        day_of_week = cursor.weekday()
        date_str = cursor.strftime("%Y-%m-%d")
        count = daily.get(date_str, 0)
        current_week[day_of_week] = {"date": date_str, "count": count, "day": cursor.day}
        if day_of_week == 6:  # Sunday = end of week
            weeks.append(list(current_week))
            current_week = [None] * 7
        cursor += timedelta(days=1)
    if any(current_week):
        weeks.append(list(current_week))

    # Max count for color scaling
    max_count = max(daily.values()) if daily else 0

    return {
        "weeks": weeks,
        "max_count": max_count,
        "month_labels": _build_month_labels(weeks),
    }


def _build_month_labels(weeks: list) -> list[dict]:
    """Return month label positions for the heatmap header."""
    labels = []
    for wi, week in enumerate(weeks):
        for day_data in week:
            if day_data and day_data["day"] == 1:
                labels.append({"week_index": wi, "label": day_data["date"][:7]})
                break
    return labels


# ═══════════════════════════════════════════════════════════════
# P3: 客户满意度
# ═══════════════════════════════════════════════════════════════


async def get_satisfaction_stats(
    db: AsyncSession, assignee: str | None = None
) -> dict:
    """Per-person satisfaction distribution from completed tickets."""
    from app.models.user import User

    r = await db.execute(
        select(User.display_name).where(User.role.in_(["admin", "it_staff"]), User.is_active == True)
    )
    staff = [row[0] for row in r.all()]

    stats = []
    for s in staff:
        if assignee and s != assignee:
            continue

        r = await db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.assignee == s, Ticket.satisfaction.isnot(None)
            )
        )
        total = r.scalar_one()

        r = await db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.assignee == s, Ticket.satisfaction == "satisfied"
            )
        )
        satisfied = r.scalar_one()

        r = await db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.assignee == s, Ticket.satisfaction == "neutral"
            )
        )
        neutral = r.scalar_one()

        r = await db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.assignee == s, Ticket.satisfaction == "unsatisfied"
            )
        )
        unsatisfied = r.scalar_one()

        rate = round(satisfied / total * 100, 1) if total > 0 else 0
        stats.append({
            "assignee": s,
            "total_rated": total,
            "satisfied": satisfied,
            "neutral": neutral,
            "unsatisfied": unsatisfied,
            "rate": rate,
        })

    stats.sort(key=lambda x: x["rate"], reverse=True)
    return {
        "staff_stats": stats,
    }


async def set_satisfaction(
    db: AsyncSession, ticket_id: int, satisfaction: str
) -> Ticket:
    """Record satisfaction rating for a completed ticket."""
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise ValueError("工单不存在")
    if satisfaction not in ("satisfied", "neutral", "unsatisfied"):
        raise ValueError("无效的满意度值")
    ticket.satisfaction = satisfaction
    await db.commit()
    await db.refresh(ticket)
    return ticket