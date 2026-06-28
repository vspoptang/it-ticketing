from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_staff, template_context
from app.models.user import User
from app.services import dashboard_service
from app.services.auth_service import list_users
from app.services.workday_service import get_workday_config
from app.services.period_helper import compute_period, month_range
from app.templates_setup import templates

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    assignee: Optional[str] = Query(None),
    period: str = Query("this_month"),
    month_offset: int = Query(0, ge=-24, le=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    assignee_filter = assignee if assignee else None
    date_start, date_end, period_label = compute_period(period)

    # Sequential queries to avoid SQLAlchemy async session concurrency issues
    summary = await dashboard_service.get_summary(db, assignee_filter, date_start, date_end)
    status_dist = await dashboard_service.get_status_distribution(db, assignee_filter, date_start, date_end)
    priority_dist = await dashboard_service.get_priority_distribution(db, assignee_filter, date_start, date_end)
    category_dist = await dashboard_service.get_category_distribution(db, assignee_filter, date_start, date_end)
    workload = await dashboard_service.get_assignee_workload(db, assignee_filter)
    sla = await dashboard_service.get_sla_metrics(db, assignee_filter, date_start, date_end)
    trends = await dashboard_service.get_trends(db, 30, assignee_filter, date_start, date_end)
    priority_time = await dashboard_service.get_priority_avg_time(db, assignee_filter, date_start, date_end)
    backlog_trend = await dashboard_service.get_backlog_trend(db, 30, assignee_filter)
    first_response = await dashboard_service.get_first_response_time(db, assignee_filter, date_start, date_end)
    expertise = await dashboard_service.get_category_expertise(db)
    leaderboard = await dashboard_service.get_monthly_leaderboard(db, month_offset)
    mom = await dashboard_service.get_mom_comparison(db, assignee_filter)
    active_tickets = await dashboard_service.get_active_tickets(db, assignee_filter)
    monthly_sla = await dashboard_service.get_monthly_sla_trend(db, 6, assignee_filter)
    overdue_tickets = await dashboard_service.get_overdue_tickets_detail(db, assignee_filter)
    response_dist = await dashboard_service.get_response_time_distribution(db, assignee_filter, date_start, date_end)
    workload_balance = await dashboard_service.get_workload_balance(db, assignee_filter)
    cat_eff = await dashboard_service.get_category_efficiency_comparison(db, assignee_filter, date_start=date_start, date_end=date_end)
    heatmap_data = await dashboard_service.get_personal_heatmap(db, assignee_filter, 18)
    sat_stats = await dashboard_service.get_satisfaction_stats(db, assignee_filter)
    workday_config = await get_workday_config(db)

    lb_start, lb_end, lb_label = month_range(month_offset)
    leaderboard_month = lb_label

    users = await list_users(db)
    staff = [u for u in users if u.role == "it_staff" and u.is_active]

    status_map = {"pending": "待处理", "in_progress": "处理中", "completed": "已完成", "cancelled": "已取消", "escalated": "已升级"}
    status_colors = {"pending": "#EAB308", "in_progress": "#3B82F6", "completed": "#22C55E", "cancelled": "#9CA3AF", "escalated": "#F97316"}
    priority_map = {"低": "低", "中": "中", "高": "高", "紧急": "紧急"}

    ctx = template_context(request, current_user, **{
        "summary": summary, "status_dist": status_dist, "priority_dist": priority_dist,
        "category_dist": category_dist, "workload": workload, "sla": sla,
        "trends": trends, "priority_time": priority_time, "backlog_trend": backlog_trend,
        "first_response": first_response, "expertise": expertise,
        "leaderboard": leaderboard, "leaderboard_month": leaderboard_month,
        "month_offset": month_offset, "mom": mom, "active_tickets": active_tickets,
        "monthly_sla": monthly_sla,
        "status_map": status_map, "status_colors": status_colors,
        "priority_map": priority_map, "staff": staff, "assignee": assignee or "",
        "period": period, "period_label": period_label,
        "overdue_tickets": overdue_tickets,
        "response_dist": response_dist,
        "workload_balance": workload_balance, "cat_eff": cat_eff,
        "heatmap_data": heatmap_data, "sat_stats": sat_stats,
    })
    return templates.TemplateResponse("dashboard.html", ctx)


@router.get("/staff/{name}/returns", response_class=HTMLResponse)
async def staff_returns_detail(
    request: Request, name: str,
    month_offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    from app.services.period_helper import month_range
    month_start, month_end, _ = month_range(month_offset)
    tickets = await dashboard_service.get_return_tickets(db, name, month_start, month_end)
    return templates.TemplateResponse("dashboard/_detail_modal.html", {
        "request": request, "staff_name": name, "tickets": tickets,
        "type": "退回", "month_label": month_start.strftime("%Y年%m月"),
    })


@router.get("/staff/{name}/receives", response_class=HTMLResponse)
async def staff_receives_detail(
    request: Request, name: str,
    month_offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    from app.services.period_helper import month_range
    month_start, month_end, _ = month_range(month_offset)
    tickets = await dashboard_service.get_receive_tickets(db, name, month_start, month_end)
    return templates.TemplateResponse("dashboard/_detail_modal.html", {
        "request": request, "staff_name": name, "tickets": tickets,
        "type": "接收升级", "month_label": month_start.strftime("%Y年%m月"),
    })
