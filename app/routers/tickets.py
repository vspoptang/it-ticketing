from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import flash, get_current_user, require_user, require_admin, template_context
from app.models.user import User
from app.schemas.ticket import TicketCommentCreate, TicketCreate, TicketUpdate
from app.services import ticket_service, category_service
from app.services.auth_service import list_users
from app.services.permission_service import get_transition_permissions
from app.templates_setup import templates
from app.timezone_helper import now

router = APIRouter(prefix="/tickets", tags=["tickets"])


async def _get_staff(db: AsyncSession) -> list[User]:
    users = await list_users(db)
    return [u for u in users if u.role in ("admin", "it_staff") and u.is_active]


def _check_ticket_access(ticket, current_user: User) -> None:
    if current_user.role == "admin":
        return
    if ticket.assignee != current_user.display_name:
        raise HTTPException(status_code=403, detail="Only assigned tickets are accessible")


# ── List ──

@router.get("", response_class=HTMLResponse)
async def list_tickets(
    request: Request,
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    assignee: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    sla: Optional[str] = Query(None),
    year_month: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    perms = await get_transition_permissions(db)

    if year_month == "all":
        year_month = None

    result = await ticket_service.list_tickets(
        db, status=status, q=q, assignee=assignee,
        category=category, priority=priority, sla=sla,
        year_month=year_month,
        page=page, page_size=page_size,
        restrict_assignee=current_user.display_name if current_user.role != "admin" else None,
    )
    categories = await category_service.get_active_categories(db)
    staff = await _get_staff(db)

    is_htmx = request.headers.get("HX-Request") == "true"
    tmpl = "tickets/_list_table.html" if is_htmx else "tickets/list.html"
    for ticket in result["items"]:
        ticket._valid_transitions = ticket_service.get_valid_transitions(
            ticket.status,
            user_role=current_user.role,
            is_assignee=(ticket.assignee == current_user.display_name),
            perms=perms,
        )
    ctx = template_context(request, current_user, **{
        "tickets": result["items"], "total": result["total"],
        "page": result["page"], "page_size": result["page_size"],
        "total_pages": result["total_pages"],
        "status": status or "", "q": q or "",
        "assignee": assignee or "", "category": category or "",
        "priority": priority or "", "sla": sla or "",
        "year_month": year_month or "",
        "categories": categories,
        "staff": staff,
        "status_display": ticket_service.STATUS_DISPLAY,
        "status_colors": ticket_service.STATUS_COLORS,
        "now": now(),
    })
    return templates.TemplateResponse(tmpl, ctx)


@router.get("/new", response_class=HTMLResponse)
async def create_ticket_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    categories = await category_service.get_active_categories(db)
    ctx = template_context(request, current_user, categories=categories)
    return templates.TemplateResponse("tickets/form.html", ctx)


@router.post("/new")
async def create_ticket(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("medium"),
    category: str = Form("Other"),
    creator_name: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return RedirectResponse(url="/auth/login", status_code=302)
    data = TicketCreate(
        title=title, description=description,
        priority=priority, category=category,
        creator_name=creator_name or current_user.display_name,
    )
    ticket = await ticket_service.create_ticket(db, data)
    return RedirectResponse(url=f"/tickets/{ticket.id}", status_code=302)


@router.get("/{ticket_id}", response_class=HTMLResponse)
async def ticket_detail(
    request: Request, ticket_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    perms = await get_transition_permissions(db)
    ticket = await ticket_service.get_ticket(db, ticket_id)
    _check_ticket_access(ticket, current_user)
    valid_transitions = ticket_service.get_valid_transitions(
        ticket.status,
        user_role=current_user.role,
        is_assignee=(ticket.assignee == current_user.display_name),
        perms=perms,
    )
    staff = await _get_staff(db)
    ctx = template_context(request, current_user, **{
        "ticket": ticket, "valid_transitions": valid_transitions,
        "status_display": ticket_service.STATUS_DISPLAY,
        "status_colors": ticket_service.STATUS_COLORS,
        "staff": staff, "now": now().replace(tzinfo=None),
    })
    return templates.TemplateResponse("tickets/detail.html", ctx)


@router.get("/{ticket_id}/edit", response_class=HTMLResponse)
async def edit_ticket_form(
    request: Request, ticket_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    ticket = await ticket_service.get_ticket(db, ticket_id)
    _check_ticket_access(ticket, current_user)
    categories = await category_service.get_active_categories(db)
    staff = await _get_staff(db)
    ctx = template_context(request, current_user, ticket=ticket,
                           categories=categories, staff=staff)
    return templates.TemplateResponse("tickets/form.html", ctx)


@router.post("/{ticket_id}/edit")
async def update_ticket(
    request: Request, ticket_id: int,
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("medium"),
    category: str = Form("Other"),
    assignee: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    ticket = await ticket_service.get_ticket(db, ticket_id)
    _check_ticket_access(ticket, current_user)
    data = TicketUpdate(
        title=title, description=description,
        priority=priority, category=category,
        assignee=assignee or None,
    )
    ticket = await ticket_service.update_ticket(db, ticket_id, data)
    return RedirectResponse(url=f"/tickets/{ticket.id}", status_code=302)


@router.post("/{ticket_id}/comments")
async def add_comment(
    request: Request, ticket_id: int,
    comment: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    ticket = await ticket_service.get_ticket(db, ticket_id)
    _check_ticket_access(ticket, current_user)
    await ticket_service.add_ticket_comment(db, ticket_id, comment, actor=current_user.display_name)
    flash(request, "Comment added", "success")
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=302)


@router.post("/{ticket_id}/status")
async def update_status(
    request: Request, ticket_id: int,
    status: str = Form(...),
    resolution_notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    perms = await get_transition_permissions(db)
    ticket = await ticket_service.get_ticket(db, ticket_id)
    _check_ticket_access(ticket, current_user)
    allowed_targets = {t["value"] for t in ticket_service.get_valid_transitions(
        ticket.status,
        user_role=current_user.role,
        is_assignee=(ticket.assignee == current_user.display_name),
        perms=perms,
    )}
    if status not in allowed_targets:
        raise HTTPException(status_code=403, detail="Permission denied")

    escalation_assignee = None
    if status == "escalated" and resolution_notes:
        escalation_assignee = resolution_notes
        resolution_notes = ""

    ticket = await ticket_service.update_ticket_status(
        db, ticket_id, status, resolution_notes, actor=current_user.display_name,
    )
    if escalation_assignee:
        ticket = await ticket_service.update_ticket_assignee(
            db, ticket_id, escalation_assignee, actor=current_user.display_name,
        )
    valid_transitions = ticket_service.get_valid_transitions(
        ticket.status,
        user_role=current_user.role,
        is_assignee=(ticket.assignee == current_user.display_name),
        perms=perms,
    )
    ctx = template_context(request, current_user, ticket=ticket,
                           valid_transitions=valid_transitions,
                           status_display=ticket_service.STATUS_DISPLAY,
                           status_colors=ticket_service.STATUS_COLORS,
                           now=now().replace(tzinfo=None))
    return templates.TemplateResponse("tickets/_status_with_select.html", ctx)


@router.post("/{ticket_id}/assign")
async def assign_ticket(
    request: Request, ticket_id: int,
    assignee: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    _ = await ticket_service.get_ticket(db, ticket_id)
    ticket = await ticket_service.update_ticket_assignee(
        db, ticket_id, assignee, actor=current_user.display_name,
    )
    return RedirectResponse(url=f"/tickets/{ticket.id}", status_code=302)
