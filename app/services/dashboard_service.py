from __future__ import annotations

from datetime import datetime, timedelta
from app.timezone_helper import now

from sqlalchemy import func, select, text, exists, or_, and_
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ticket import Ticket, TicketEvent

OPEN_STATUSES = ["pending", "in_progress", "escalated"]
CLOSED_STATUS = "completed"


def _apply_filter(query, assignee: str | None):
    if assignee:
        return query.where(Ticket.assignee == assignee)
    return query


def _apply_date_filter(query, date_start, date_end, field=None):
    """Add date range filter to query. field defaults to Ticket.created_at."""
    if field is None:
        field = Ticket.created_at
    if date_start:
        query = query.where(field >= date_start)
    if date_end:
        query = query.where(field <= date_end)
    return query


async def get_status_distribution(db: AsyncSession, assignee: str | None = None,
                                   date_start=None, date_end=None) -> list[dict]:
    query = select(Ticket.status, func.count(Ticket.id)).where(Ticket.status != "cancelled").group_by(Ticket.status)
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    return [{"status": row[0], "count": row[1]} for row in result.all()]


async def get_priority_distribution(db: AsyncSession, assignee: str | None = None,
                                     date_start=None, date_end=None) -> list[dict]:
    query = select(Ticket.priority, func.count(Ticket.id)).where(Ticket.status != "cancelled").group_by(Ticket.priority)
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    return [{"priority": row[0], "count": row[1]} for row in result.all()]


async def get_category_distribution(db: AsyncSession, assignee: str | None = None,
                                     date_start=None, date_end=None) -> list[dict]:
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


async def get_sla_metrics(db: AsyncSession, assignee: str | None = None,
                         date_start=None, date_end=None) -> dict:
    current_time = now().replace(tzinfo=None)

    query = select(func.count(Ticket.id)).where(Ticket.status == CLOSED_STATUS)
    query = _apply_filter(query, assignee)
    query = _apply_date_filter(query, date_start, date_end, Ticket.resolved_at)
    result = await db.execute(query)
    total_completed = result.scalar_one()

    query = select(func.count(Ticket.id)).where(
        Ticket.status == CLOSED_STATUS,
        Ticket.sla_due_at.isnot(None),
        Ticket.resolved_at <= Ticket.sla_due_at,
    )
    query = _apply_filter(query, assignee)
    query = _apply_date_filter(query, date_start, date_end, Ticket.resolved_at)
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


async def get_trends(db: AsyncSession, days: int = 30, assignee: str | None = None,
                    date_start=None, date_end=None) -> dict:
    start_date = now() - timedelta(days=days)

    query = select(func.date(Ticket.created_at), func.count(Ticket.id)).where(
        Ticket.created_at >= start_date, Ticket.status != "cancelled"
    ).group_by(func.date(Ticket.created_at)).order_by(func.date(Ticket.created_at))
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    created = {str(row[0]): row[1] for row in result.all()}

    query = select(func.date(Ticket.resolved_at), func.count(Ticket.id)).where(
        Ticket.resolved_at >= start_date, Ticket.status == CLOSED_STATUS
    ).group_by(func.date(Ticket.resolved_at)).order_by(func.date(Ticket.resolved_at))
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    resolved = {str(row[0]): row[1] for row in result.all()}

    labels = []
    created_data = []
    resolved_data = []
    for i in range(days - 1, -1, -1):
        d = (now() - timedelta(days=i)).strftime("%Y-%m-%d")
        labels.append(d)
        created_data.append(created.get(d, 0))
        resolved_data.append(resolved.get(d, 0))

    return {"labels": labels, "created": created_data, "resolved": resolved_data}


async def get_summary(db: AsyncSession, assignee: str | None = None,
                     date_start=None, date_end=None) -> dict:
    query = select(func.count(Ticket.id)).where(Ticket.status.in_(OPEN_STATUSES))
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    open_count = result.scalar_one()

    today_date = now().date()

    # Today created
    query = select(func.count(Ticket.id)).where(
        func.date(Ticket.created_at) == today_date, Ticket.status != "cancelled",
    )
    query = _apply_filter(query, assignee)
    result = await db.execute(query)
    today_created = result.scalar_one()
    query = select(func.count(Ticket.id)).where(
        func.date(Ticket.resolved_at) == today_date, Ticket.status == CLOSED_STATUS
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

    q = select(Ticket.created_at, Ticket.resolved_at).where(
        Ticket.status == CLOSED_STATUS, Ticket.resolved_at.isnot(None),
    )
    if assignee:
        q = q.where(Ticket.assignee == assignee)
    r = await db.execute(q)
    rows = r.all()
    from app.services.ticket_service import business_hours_between
    bh_list = [business_hours_between(row[0], row[1]) for row in rows if row[0] and row[1]]
    avg_hours = round(sum(bh_list) / len(bh_list), 1) if bh_list else 0

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


async def get_priority_avg_time(db: AsyncSession, assignee: str | None = None,
                                date_start=None, date_end=None) -> list[dict]:
    """Average resolution time by priority."""
    result_list = []
    for p in ["紧急", "高", "中", "低"]:
        query = select(
            Ticket.created_at, Ticket.resolved_at
        ).where(Ticket.status == CLOSED_STATUS, Ticket.resolved_at.isnot(None), Ticket.priority == p)
        query = _apply_filter(query, assignee)
        query = _apply_date_filter(query, date_start, date_end, Ticket.resolved_at)
        result = await db.execute(query)
        rows = result.all()
        from app.services.ticket_service import business_hours_between
        bh_list = [business_hours_between(row[0], row[1]) for row in rows if row[0] and row[1]]
        result_list.append({"priority": p, "avg_hours": round(sum(bh_list) / len(bh_list), 1) if bh_list else 0})
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


async def get_first_response_time(db: AsyncSession, assignee: str | None = None,
                                  date_start=None, date_end=None) -> float:
    """Average business hours from creation to first response."""
    q = select(Ticket.created_at, Ticket.first_response_at).where(
        Ticket.first_response_at.isnot(None), Ticket.status != "cancelled",
    )
    q = _apply_filter(q, assignee)
    q = _apply_date_filter(q, date_start, date_end, Ticket.first_response_at)
    r = await db.execute(q)
    rows = r.all()
    from app.services.ticket_service import business_hours_between
    bh_list = [business_hours_between(row[0], row[1]) for row in rows if row[0] and row[1]]
    return round(sum(bh_list) / len(bh_list), 1) if bh_list else 0


async def get_category_expertise(db: AsyncSession, assignee: str | None = None) -> list[dict]:
    """Per-assignee category distribution (for radar chart)."""
    from app.models.user import User
    from app.models.category import Category

    result = await db.execute(
        select(User.display_name).where(User.role == "it_staff", User.is_active == True)
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
    from app.models.category import Category
    from app.services.ticket_service import business_hours_between
    r = await db.execute(
        select(User.display_name).where(User.role == "it_staff", User.is_active == True)
    )
    staff = [row[0] for row in r.all()]
    if not staff:
        return []
    current = now()
    y, m = current.year, current.month
    m += offset
    while m < 1: y -= 1; m += 12
    while m > 12: y += 1; m -= 12
    month_start = datetime(y, m, 1, tzinfo=current.tzinfo)
    month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    weight_r = await db.execute(select(Category.name, Category.complexity_weight))
    cat_weights = {row[0]: row[1] for row in weight_r.all()}

    raw_data = []
    for s in staff:
        # completed
        r = await db.execute(select(func.count(Ticket.id)).where(
            Ticket.assignee == s, Ticket.status == CLOSED_STATUS,
            Ticket.resolved_at >= month_start, Ticket.resolved_at < month_end))
        completed = r.scalar_one()

        # weighted
        weighted_r = await db.execute(select(func.coalesce(func.sum(Category.complexity_weight), 0)).where(
            Ticket.assignee == s, Ticket.status == CLOSED_STATUS,
            Ticket.resolved_at >= month_start, Ticket.resolved_at < month_end,
            Ticket.category == Category.name))
        weighted_completed = round(weighted_r.scalar_one(), 1)

        # sla
        r = await db.execute(select(func.count(Ticket.id)).where(
            Ticket.assignee == s, Ticket.status == CLOSED_STATUS,
            Ticket.resolved_at >= month_start, Ticket.resolved_at < month_end,
            Ticket.sla_due_at.isnot(None), Ticket.resolved_at <= Ticket.sla_due_at))
        on_time = r.scalar_one()
        sla_rate = round(on_time / completed * 100, 1) if completed > 0 else 0

        # avg hours (business hours)
        r = await db.execute(select(Ticket.created_at, Ticket.resolved_at).where(
            Ticket.assignee == s, Ticket.status == CLOSED_STATUS,
            Ticket.resolved_at >= month_start, Ticket.resolved_at < month_end,
            Ticket.resolved_at.isnot(None)))
        bh_list = [business_hours_between(row[0], row[1]) for row in r.all() if row[0] and row[1]]
        avg_hours = round(sum(bh_list) / len(bh_list), 1) if bh_list else 0

        # first_response
        fr_r = await db.execute(select(Ticket.created_at, Ticket.first_response_at).where(
            Ticket.assignee == s, Ticket.status == CLOSED_STATUS,
            Ticket.resolved_at >= month_start, Ticket.resolved_at < month_end,
            Ticket.first_response_at.isnot(None)))
        fr_rows = [(row[0], row[1]) for row in fr_r.all()]
        fr_hours = [business_hours_between(c, f) for c, f in fr_rows if c and f]
        first_response = round(sum(fr_hours) / len(fr_hours), 1) if fr_hours else 0

        # returns_
        ret_r = await db.execute(
            select(func.count(func.distinct(Ticket.id))).join(
                TicketEvent, TicketEvent.ticket_id == Ticket.id
            ).where(
                Ticket.assignee == s,
                TicketEvent.event_type == "status_change",
                TicketEvent.message.like("%已完成%处理中%"),
                TicketEvent.created_at >= month_start,
                TicketEvent.created_at < month_end,
                TicketEvent.created_at > func.coalesce(
                    select(func.max(TicketEvent.created_at)).where(
                        TicketEvent.ticket_id == Ticket.id,
                        TicketEvent.event_type == "assignee_change",
                        TicketEvent.message.like(f"%{s}%"),
                    ).correlate(Ticket).scalar_subquery(),
                    datetime(2000, 1, 1),
                ),
                ~exists().where(
                    TicketEvent.ticket_id == Ticket.id,
                    TicketEvent.event_type == "assignee_change",
                    TicketEvent.message.like(f"%{s}%"),
                    TicketEvent.created_at > TicketEvent.created_at,
                ).correlate(Ticket),
            )
        )
        returns_ = ret_r.scalar_one()

        # escalations_out
        EscEvent = aliased(TicketEvent)
        prev_owner_subq = (
            select(TicketEvent.message).where(
                TicketEvent.ticket_id == Ticket.id,
                TicketEvent.event_type == "assignee_change",
                TicketEvent.created_at < EscEvent.created_at,
            ).order_by(TicketEvent.created_at.desc()).limit(1)
            .correlate(Ticket).scalar_subquery()
        )
        esc_r = await db.execute(
            select(func.count(func.distinct(Ticket.id))).join(
                EscEvent, EscEvent.ticket_id == Ticket.id
            ).where(
                EscEvent.event_type == "status_change",
                EscEvent.message.like("%改为「已升级」%"),
                EscEvent.created_at >= month_start,
                EscEvent.created_at < month_end,
                or_(
                    prev_owner_subq.like(f"%{s}%"),
                    and_(
                        prev_owner_subq == None,
                        Ticket.assignee == s,
                        ~exists().where(
                            TicketEvent.ticket_id == Ticket.id,
                            TicketEvent.event_type == "assignee_change",
                            TicketEvent.created_at > EscEvent.created_at,
                        ).correlate(Ticket),
                    ),
                ),
            )
        )
        escalations_out = esc_r.scalar_one()

        # escalations_in
        esc_in_r = await db.execute(
            select(func.count(func.distinct(Ticket.id))).where(
                Ticket.assignee == s,
                exists().where(
                    TicketEvent.ticket_id == Ticket.id,
                    TicketEvent.event_type == "status_change",
                    TicketEvent.message.like("%改为「已升级」%"),
                    TicketEvent.created_at >= month_start,
                    TicketEvent.created_at < month_end,
                    TicketEvent.actor != s,
                ).correlate(Ticket),
                exists().where(
                    TicketEvent.ticket_id == Ticket.id,
                    TicketEvent.event_type == "assignee_change",
                    TicketEvent.message.like(f"%{s}%"),
                    TicketEvent.created_at >= month_start,
                    TicketEvent.created_at < month_end,
                    TicketEvent.created_at > select(TicketEvent.created_at).where(
                        TicketEvent.ticket_id == Ticket.id,
                        TicketEvent.event_type == "status_change",
                        TicketEvent.message.like("%改为「已升级」%"),
                        TicketEvent.created_at >= month_start,
                        TicketEvent.created_at < month_end,
                    ).order_by(TicketEvent.created_at.asc()).limit(1)
                    .correlate(Ticket).scalar_subquery(),
                ).correlate(Ticket),
            )
        )
        escalations_in = esc_in_r.scalar_one()

        raw_data.append({
            "assignee": s, "completed": completed, "weighted": weighted_completed,
            "sla_rate": sla_rate, "avg_hours": avg_hours,
            "first_response": first_response,
            "returns_": returns_,
            "escalations_out": escalations_out,
            "escalations_in": escalations_in,
        })

    if raw_data:
        max_weighted = max(d["weighted"] for d in raw_data) or 1
        max_avg = max(d["avg_hours"] for d in raw_data) or 1
        max_fr = max(d["first_response"] for d in raw_data) or 1
        for d in raw_data:
            completed_score = round((d["weighted"] / max_weighted) * 35, 1) if max_weighted else 0
            sla_score = round((d["sla_rate"] / 100) * 25, 1)
            speed_score = round(((1 - d["avg_hours"] / max_avg) * 20), 1) if max_avg else 20
            response_score = round(((1 - d["first_response"] / max_fr) * 5), 1) if max_fr else 5
            returns_score = 5 - min(d["returns_"], 5)
            receive_score = min(d["escalations_in"] * 2, 10)
            d["score"] = round(completed_score + sla_score + speed_score + response_score + returns_score + receive_score, 1)
            d["score_completion"] = completed_score
            d["score_sla"] = sla_score
            d["score_speed"] = speed_score
            d["score_response"] = response_score
            d["score_returns"] = returns_score
            d["score_receive"] = receive_score
            d["_max_weighted"] = round(max_weighted, 1)
            d["_max_avg"] = max_avg
            d["_max_fr"] = max_fr

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

    async def _avg_time(start, end, field):
        q = select(Ticket.created_at, field).where(
            Ticket.created_at >= start, Ticket.created_at < end,
            Ticket.status == CLOSED_STATUS, field.isnot(None),
        )
        q = _apply_filter(q, assignee)
        r = await db.execute(q)
        rows = r.all()
        from app.services.ticket_service import business_hours_between
        bh_list = [business_hours_between(row[0], row[1]) for row in rows if row[0] and row[1]]
        return round(sum(bh_list) / len(bh_list), 1) if bh_list else 0

    # This month
    r = await db.execute(_count(this_start, now()))
    tm_created = r.scalar_one()
    r = await db.execute(_count(this_start, now(), True))
    tm_completed = r.scalar_one()
    r = await db.execute(_count(this_start, now(), True, True))
    tm_sla_ok = r.scalar_one()
    tm_resp = await _avg_time(this_start, now(), Ticket.first_response_at)
    tm_avg = await _avg_time(this_start, now(), Ticket.resolved_at)

    # Last month
    r = await db.execute(_count(last_start, last_end))
    lm_created = r.scalar_one()
    r = await db.execute(_count(last_start, last_end, True))
    lm_completed = r.scalar_one()
    r = await db.execute(_count(last_start, last_end, True, True))
    lm_sla_ok = r.scalar_one()
    lm_resp = await _avg_time(last_start, last_end, Ticket.first_response_at)
    lm_avg = await _avg_time(last_start, last_end, Ticket.resolved_at)

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
    for p in ["紧急", "高", "中", "低"]:
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
        select(User.display_name).where(User.role == "it_staff", User.is_active == True)
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
    db: AsyncSession, limit: int = 50, assignee: str | None = None,
    date_start=None, date_end=None,
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
    db: AsyncSession, assignee: str | None = None,
    date_start=None, date_end=None,
) -> dict:
    """Response time distribution in buckets (business hours)."""
    from app.services.ticket_service import business_hours_between
    q = select(Ticket.created_at, Ticket.first_response_at).where(
        Ticket.first_response_at.isnot(None), Ticket.status != "cancelled",
    )
    if assignee:
        q = q.where(Ticket.assignee == assignee)
    r = await db.execute(q)
    rows = r.all()
    hours_list = [business_hours_between(row[0], row[1]) for row in rows if row[0] and row[1]]

    buckets = [
        ("≤1h", 0, 1),
        ("≤4h", 1, 4),
        ("≤8h", 4, 8),
        ("≤24h", 8, 24),
        (">24h", 24, 9999),
    ]
    total = len(hours_list)
    dist = []
    for label, lo, hi in buckets:
        count = sum(1 for h in hours_list if lo < h <= hi)
        dist.append({"label": label, "count": count, "lo": lo, "hi": hi})
    # Add percentages
    for d in dist:
        d["pct"] = round(d["count"] / total * 100, 1) if total > 0 else 0
    return {"buckets": dist, "total": total}


async def get_workload_balance(
    db: AsyncSession, assignee: str | None = None
) -> list[dict]:
    """Current workload per staff member with stacked-bar priority breakdown."""
    from app.models.user import User
    r = await db.execute(
        select(User.display_name).where(User.role == "it_staff", User.is_active == True)
    )
    staff = [row[0] for row in r.all()]
    if not staff:
        return []

    CAPACITY = 8
    PRIORITY_ORDER = ["紧急", "高", "中", "低"]
    PRIORITY_COLORS = {"紧急": "bg-red-500", "高": "bg-orange-400", "中": "bg-blue-400", "低": "bg-gray-300"}

    # Batch query: per-person per-priority count of open tickets
    q = select(Ticket.assignee, Ticket.priority, func.count(Ticket.id)).where(
        Ticket.assignee.in_(staff), Ticket.status.in_(OPEN_STATUSES),
    ).group_by(Ticket.assignee, Ticket.priority)
    r = await db.execute(q)
    from collections import defaultdict
    pmap: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for name, pri, cnt in r.all():
        pmap[name][pri] = cnt

    results = []
    for s in staff:
        pri_counts = pmap.get(s, {})
        total = sum(pri_counts.values())
        load_level = "high" if total >= CAPACITY else ("normal" if total >= 4 else "low")
        load_pct = min(total / CAPACITY * 100, 100)

        priorities = []
        cumul = 0
        for p in PRIORITY_ORDER:
            c = pri_counts.get(p, 0)
            w = round(c / CAPACITY * 100, 1) if CAPACITY else 0
            priorities.append({
                "priority": p, "count": c,
                "width_pct": w, "left_pct": round(cumul, 1),
                "color": PRIORITY_COLORS[p],
            })
            cumul += w

        results.append({
            "assignee": s, "total": total, "capacity": CAPACITY,
            "load_level": load_level, "load_pct": round(load_pct, 1),
            "priorities": priorities,
        })

    results.sort(key=lambda x: x["total"], reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════
# P3: 分类效率对比
# ═══════════════════════════════════════════════════════════════


async def get_category_efficiency_comparison(
    db: AsyncSession, assignee: str | None = None, months: int = 3,
    date_start=None, date_end=None,
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
        select(User.display_name).where(User.role == "it_staff", User.is_active == True)
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
    from app.services.ticket_service import business_hours_between
    matrix: dict[str, dict[str, float]] = {cat: {} for cat in categories}
    for cat in categories:
        for s in staff:
            r = await db.execute(
                select(Ticket.created_at, Ticket.resolved_at).where(
                    Ticket.category == cat,
                    Ticket.assignee == s,
                    Ticket.status == CLOSED_STATUS,
                    Ticket.resolved_at.isnot(None),
                    Ticket.resolved_at >= cutoff,
                )
            )
            rows = r.all()
            bh_list = [business_hours_between(row[0], row[1]) for row in rows if row[0] and row[1]]
            matrix[cat][s] = round(sum(bh_list) / len(bh_list), 1) if bh_list else 0

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
    """Daily completion counts for heatmap. Returns staff_weeks × day_of_week grid."""
    from app.models.user import User
    current = now()
    end_date = current.replace(hour=23, minute=59, second=59, microsecond=999999)
    start_date = (end_date - timedelta(days=months * 31)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Get active staff list — only those with resolved tickets in the range
    r = await db.execute(select(User.display_name).where(User.is_active == True))
    all_names = [row[0] for row in r.all()]
    staff_list = ["全部"]
    for name in all_names:
        q = select(func.count(Ticket.id)).where(
            Ticket.assignee == name, Ticket.status == CLOSED_STATUS,
            Ticket.resolved_at >= start_date, Ticket.resolved_at <= end_date,
        )
        cnt = (await db.execute(q)).scalar_one()
        if cnt > 0:
            staff_list.append(name)

    # Build week grid helper
    def build_weeks(daily_counts: dict):
        weeks = []
        cursor = start_date
        current_week = [None] * 7
        weekday = cursor.weekday()
        if weekday > 0:
            cursor = cursor - timedelta(days=weekday)
        while cursor <= end_date:
            dow = cursor.weekday()
            date_str = cursor.strftime("%Y-%m-%d")
            count = daily_counts.get(date_str, 0)
            current_week[dow] = {"date": date_str, "count": count, "day": cursor.day}
            if dow == 6:
                weeks.append(list(current_week))
                current_week = [None] * 7
            cursor += timedelta(days=1)
        if any(current_week):
            weeks.append(list(current_week))
        return weeks

    # Query all resolved dates (no assignee filter for "全部")
    q = select(func.date(Ticket.resolved_at), func.count(Ticket.id)).where(
        Ticket.status == CLOSED_STATUS,
        Ticket.resolved_at >= start_date,
        Ticket.resolved_at <= end_date,
    ).group_by(func.date(Ticket.resolved_at))
    if assignee:
        q = q.where(Ticket.assignee == assignee)
    r = await db.execute(q)
    daily_all = {str(row[0]): row[1] for row in r.all()}

    max_count = max(daily_all.values()) if daily_all else 0
    staff_weeks = {"全部": build_weeks(daily_all)}

    # Per-staff weeks
    for name in staff_list[1:]:  # skip "全部"
        q = select(func.date(Ticket.resolved_at), func.count(Ticket.id)).where(
            Ticket.status == CLOSED_STATUS,
            Ticket.assignee == name,
            Ticket.resolved_at >= start_date,
            Ticket.resolved_at <= end_date,
        ).group_by(func.date(Ticket.resolved_at))
        r = await db.execute(q)
        daily_person = {str(row[0]): row[1] for row in r.all()}
        staff_weeks[name] = build_weeks(daily_person)

    return {
        "max_count": max_count,
        "staff_list": staff_list,
        "staff_weeks": staff_weeks,
    }


# ═══════════════════════════════════════════════════════════════
# P3: 客户满意度
# ═══════════════════════════════════════════════════════════════


async def get_satisfaction_stats(
    db: AsyncSession, assignee: str | None = None
) -> dict:
    """Per-person satisfaction distribution from completed tickets."""
    from app.models.user import User

    r = await db.execute(
        select(User.display_name).where(User.role == "it_staff", User.is_active == True)
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


async def get_return_tickets(db: AsyncSession, name: str, month_start, month_end):
    """Get tickets returned (已完成→处理中) to a specific staff member in a month."""
    q = (
        select(Ticket.id, Ticket.ticket_number, Ticket.title, Ticket.priority,
               TicketEvent.created_at)
        .join(TicketEvent, TicketEvent.ticket_id == Ticket.id)
        .where(
            Ticket.assignee == name,
            TicketEvent.event_type == "status_change",
            TicketEvent.message.like("%已完成%处理中%"),
            TicketEvent.created_at >= month_start,
            TicketEvent.created_at < month_end,
        )
        .order_by(TicketEvent.created_at.desc())
    )
    r = await db.execute(q)
    return [
        {"id": row[0], "ticket_number": row[1], "title": row[2],
         "priority": row[3], "created_at": row[4]}
        for row in r.all()
    ]


async def get_receive_tickets(db: AsyncSession, name: str, month_start, month_end):
    """Get tickets this person received via escalation in a month."""
    q = (
        select(Ticket.id, Ticket.ticket_number, Ticket.title, Ticket.priority,
               TicketEvent.created_at)
        .join(TicketEvent, TicketEvent.ticket_id == Ticket.id)
        .where(
            Ticket.assignee == name,
            TicketEvent.event_type == "assignee_change",
            TicketEvent.message.like(f"%{name}%"),
            TicketEvent.created_at >= month_start,
            TicketEvent.created_at < month_end,
            exists().where(
                TicketEvent.ticket_id == Ticket.id,
                TicketEvent.event_type == "status_change",
                TicketEvent.message.like("%改为「已升级」%"),
                TicketEvent.created_at >= month_start,
                TicketEvent.created_at < month_end,
                TicketEvent.actor != name,
            ).correlate(Ticket),
            TicketEvent.created_at > select(TicketEvent.created_at).where(
                TicketEvent.ticket_id == Ticket.id,
                TicketEvent.event_type == "status_change",
                TicketEvent.message.like("%改为「已升级」%"),
                TicketEvent.created_at >= month_start,
                TicketEvent.created_at < month_end,
            ).order_by(TicketEvent.created_at.asc()).limit(1)
            .correlate(Ticket).scalar_subquery(),
        )
        .order_by(TicketEvent.created_at.desc())
    )
    r = await db.execute(q)
    return [
        {"id": row[0], "ticket_number": row[1], "title": row[2],
         "priority": row[3], "created_at": row[4]}
        for row in r.all()
    ]