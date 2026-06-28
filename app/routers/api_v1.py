from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_user
from app.models.user import User
from app.schemas.ticket import (
    TicketCommentCreate,
    TicketCreate,
    TicketStatusUpdate,
    TicketUpdate,
)
from app.schemas.api import APIResponse, PaginatedResponse
from app.services import attachment_service, category_service, dashboard_service, ticket_service

router = APIRouter(prefix="/api/v1", tags=["api"])


# ── Helpers ──

async def get_api_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    from app.dependencies import get_current_user
    user = await get_current_user(request, db)
    if user is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="请先登录")
    return user


async def _check_ticket_api(ticket_id: int, db: AsyncSession, current_user: User):
    """Raise 403 if non-admin user tries to access a ticket not assigned to them."""
    if current_user.role == "admin":
        return
    ticket = await ticket_service.get_ticket(db, ticket_id)
    if ticket.assignee != current_user.display_name:
        raise HTTPException(status_code=403, detail="您只能访问指派给自己的工单")


async def _check_admin_for_status(
    current_user: User, status: str, db: AsyncSession,
):
    """Raise 403 if non-admin tries to use an admin-only transition."""
    if current_user.role == "admin":
        return
    from app.services.permission_service import get_admin_only_targets, get_transition_permissions
    perms = await get_transition_permissions(db)
    if status in get_admin_only_targets(perms):
        raise HTTPException(status_code=403, detail="仅管理员可执行此状态变更")


# ── Tickets ──

@router.get("/tickets")
async def api_list_tickets(
    request: Request,
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    assignee: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_api_user),
):
    result = await ticket_service.list_tickets(
        db, status=status, q=q, assignee=assignee,
        category=category, priority=priority, page=page, page_size=page_size,
        restrict_assignee=current_user.display_name if current_user.role != "admin" else None,
    )
    # Serialize tickets
    items = []
    for t in result["items"]:
        items.append({
            "id": t.id,
            "ticket_number": t.ticket_number,
            "title": t.title,
            "description": t.description,
            "status": t.status,
            "priority": t.priority,
            "assignee": t.assignee,
            "category": t.category,
            "creator_name": t.creator_name,
            "resolution_notes": t.resolution_notes,
            "sla_due_at": t.sla_due_at.isoformat() if t.sla_due_at else None,
            "first_response_at": t.first_response_at.isoformat() if t.first_response_at else None,
            "created_at": t.created_at.isoformat(),
            "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        })
    return PaginatedResponse(
        items=items, total=result["total"],
        page=result["page"], page_size=result["page_size"],
        total_pages=result["total_pages"],
    )


@router.post("/tickets")
async def api_create_ticket(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    priority: str = Form("中"),
    category: str = Form("其他"),
    creator_name: str = Form("匿名用户"),
    db: AsyncSession = Depends(get_db),
):
    # Public endpoint - anyone can create a ticket
    data = TicketCreate(title=title, description=description or None,
                        priority=priority, category=category or None,
                        creator_name=creator_name)
    ticket = await ticket_service.create_ticket(db, data)
    return APIResponse(success=True, data={"id": ticket.id, "ticket_number": ticket.ticket_number},
                       message="工单创建成功")


@router.get("/tickets/{ticket_id}")
async def api_get_ticket(
    ticket_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_api_user),
):
    await _check_ticket_api(ticket_id, db, current_user)
    t = await ticket_service.get_ticket(db, ticket_id)
    return APIResponse(success=True, data={
        "id": t.id, "ticket_number": t.ticket_number,
        "title": t.title, "description": t.description,
        "status": t.status, "priority": t.priority,
        "assignee": t.assignee, "category": t.category,
        "creator_name": t.creator_name,
        "resolution_notes": t.resolution_notes,
        "sla_due_at": t.sla_due_at.isoformat() if t.sla_due_at else None,
        "first_response_at": t.first_response_at.isoformat() if t.first_response_at else None,
        "created_at": t.created_at.isoformat(),
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "events": [{"event_type": e.event_type, "message": e.message,
                     "actor": e.actor, "created_at": e.created_at.isoformat()}
                    for e in t.events],
        "attachments": [{"id": a.id, "original_filename": a.original_filename,
                          "file_size": a.file_size, "content_type": a.content_type}
                         for a in t.attachments],
    })


@router.patch("/tickets/{ticket_id}")
async def api_update_ticket(
    ticket_id: int,
    data: TicketUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_api_user),
):
    await _check_ticket_api(ticket_id, db, current_user)
    ticket = await ticket_service.update_ticket(db, ticket_id, data)
    return APIResponse(success=True, data={"id": ticket.id}, message="工单已更新")


@router.post("/tickets/{ticket_id}/status")
async def api_update_status(
    ticket_id: int,
    data: TicketStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_api_user),
):
    await _check_ticket_api(ticket_id, db, current_user)
    await _check_admin_for_status(current_user, data.status, db)
    ticket = await ticket_service.update_ticket_status(
        db, ticket_id, data.status, data.resolution_notes,
        actor=current_user.display_name,
    )
    return APIResponse(success=True, data={"status": ticket.status}, message="状态已更新")


@router.post("/tickets/{ticket_id}/comments")
async def api_add_comment(
    ticket_id: int,
    data: TicketCommentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_api_user),
):
    await _check_ticket_api(ticket_id, db, current_user)
    event = await ticket_service.add_ticket_comment(
        db, ticket_id, data.comment, data.actor or current_user.display_name
    )
    return APIResponse(success=True, data={"id": event.id}, message="备注已添加")


@router.post("/tickets/{ticket_id}/attachments")
async def api_upload_attachment(
    ticket_id: int,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_api_user),
):
    await _check_ticket_api(ticket_id, db, current_user)
    att = await attachment_service.save_upload(
        db, ticket_id, file, uploaded_by=current_user.display_name
    )
    return APIResponse(success=True, data={"id": att.id, "filename": att.original_filename},
                       message="附件已上传")


# ── Categories ──

@router.get("/categories")
async def api_categories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_api_user),
):
    cats = await category_service.get_active_categories(db)
    return APIResponse(success=True, data=[
        {"id": c.id, "name": c.name, "description": c.description} for c in cats
    ])


# ── Dashboard ──

@router.get("/dashboard/stats")
async def api_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_api_user),
):
    from app.dependencies import require_staff
    summary = await dashboard_service.get_summary(db)
    sla = await dashboard_service.get_sla_metrics(db)
    trends = await dashboard_service.get_trends(db)
    status_dist = await dashboard_service.get_status_distribution(db)
    return APIResponse(success=True, data={
        "summary": summary, "sla": sla, "trends": trends, "status_distribution": status_dist,
    })


# ── Users ──

@router.post("/tickets/{ticket_id}/satisfaction")
async def api_set_satisfaction(
    ticket_id: int,
    satisfaction: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_api_user),
):
    from app.services.dashboard_service import set_satisfaction
    import logging
    _logger = logging.getLogger(__name__)
    try:
        ticket = await set_satisfaction(db, ticket_id, satisfaction)
        return APIResponse(success=True, data={"satisfaction": ticket.satisfaction}, message="评价已提交")
    except ValueError as e:
        return APIResponse(success=False, message=str(e))
    except Exception as e:
        _logger.error(f"Failed to set satisfaction for ticket {ticket_id}: {e}")
        return APIResponse(success=False, message="评价失败")


@router.get("/users/me")
async def api_me(current_user: User = Depends(get_api_user)):
    return APIResponse(success=True, data={
        "id": current_user.id, "username": current_user.username,
        "display_name": current_user.display_name, "role": current_user.role,
        "email": current_user.email,
    })
