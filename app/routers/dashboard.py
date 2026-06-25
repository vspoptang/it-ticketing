import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_staff, template_context
from app.models.user import User
from app.services import dashboard_service
from app.services.auth_service import list_users
from app.templates_setup import templates

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    assignee: Optional[str] = Query(None),
    month_offset: int = Query(0, ge=-12, le=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    assignee_filter = assignee if assignee else None

    # Parallelize independent queries (original + new P0/P1 data)
    (
        summary, status_dist, priority_dist, category_dist,
        workload, sla, trends, priority_time, backlog_trend,
        first_response, expertise, leaderboard,
        mom, active_tickets, monthly_sla,
        # ── P0+P1 new data ──
        all_kpi, overdue_tickets, recent_activity,
        response_dist, workload_balance,
        cat_eff, heatmap_data,
        sat_stats,
    ) = await asyncio.gather(
        dashboard_service.get_summary(db, assignee_filter),
        dashboard_service.get_status_distribution(db, assignee_filter),
        dashboard_service.get_priority_distribution(db, assignee_filter),
        dashboard_service.get_category_distribution(db, assignee_filter),
        dashboard_service.get_assignee_workload(db, assignee_filter),
        dashboard_service.get_sla_metrics(db, assignee_filter),
        dashboard_service.get_trends(db, 30, assignee_filter),
        dashboard_service.get_priority_avg_time(db, assignee_filter),
        dashboard_service.get_backlog_trend(db, 30, assignee_filter),
        dashboard_service.get_first_response_time(db, assignee_filter),
        dashboard_service.get_category_expertise(db),
        dashboard_service.get_monthly_leaderboard(db, month_offset),
        dashboard_service.get_mom_comparison(db, assignee_filter),
        dashboard_service.get_active_tickets(db, assignee_filter),
        dashboard_service.get_monthly_sla_trend(db, 6, assignee_filter),
        # ── P0+P1 ──
        dashboard_service.get_all_staff_kpi(db),
        dashboard_service.get_overdue_tickets_detail(db, assignee_filter),
        dashboard_service.get_recent_activity(db, 40, assignee_filter),
        dashboard_service.get_response_time_distribution(db, assignee_filter),
        dashboard_service.get_workload_balance(db, assignee_filter),
        dashboard_service.get_category_efficiency_comparison(db, assignee_filter),
        dashboard_service.get_personal_heatmap(db, assignee_filter, 3),
        dashboard_service.get_satisfaction_stats(db, assignee_filter),
    )
    # Month label
    from datetime import datetime
    now_dt = datetime.now()
    y, m = now_dt.year, now_dt.month
    m += month_offset
    while m < 1: y -= 1; m += 12
    while m > 12: y += 1; m -= 12
    leaderboard_month = f"{y}年{m}月"

    # Staff list for filter
    users = await list_users(db)
    staff = [u for u in users if u.role in ("admin", "it_staff") and u.is_active]

    status_map = {
        "pending": "待处理", "in_progress": "处理中", "completed": "已完成",
        "cancelled": "已取消", "escalated": "已升级",
    }
    status_colors = {
        "pending": "#EAB308", "in_progress": "#3B82F6",
        "completed": "#22C55E", "cancelled": "#9CA3AF",
        "escalated": "#F97316",
    }
    priority_map = {"low": "低", "medium": "中", "high": "高", "urgent": "紧急"}

    ctx = template_context(request, current_user, **{
        "summary": summary,
        "status_dist": status_dist,
        "priority_dist": priority_dist,
        "category_dist": category_dist,
        "workload": workload,
        "sla": sla,
        "trends": trends,
        "priority_time": priority_time,
        "backlog_trend": backlog_trend,
        "first_response": first_response,
        "expertise": expertise,
        "leaderboard": leaderboard,
        "leaderboard_month": leaderboard_month,
        "month_offset": month_offset,
        "mom": mom,
        "active_tickets": active_tickets,
        "monthly_sla": monthly_sla,
        "status_map": status_map,
        "status_colors": status_colors,
        "priority_map": priority_map,
        "staff": staff,
        "assignee": assignee or "",
        # ── P0+P1 new data ──
        "all_kpi": all_kpi,
        "overdue_tickets": overdue_tickets,
        "recent_activity": recent_activity,
        "response_dist": response_dist,
        "workload_balance": workload_balance,
        "cat_eff": cat_eff,
        "heatmap_data": heatmap_data,
        "sat_stats": sat_stats,
    })
    return templates.TemplateResponse("dashboard.html", ctx)
