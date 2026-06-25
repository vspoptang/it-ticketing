from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, template_context
from app.models.user import User
from app.services import category_service
from app.services.auth_service import list_users, toggle_user_active, update_user_role
from app.services.ticket_service import (
    STATUS_DISPLAY, VALID_TRANSITIONS,
)
from app.services.permission_service import get_permission, get_transition_permissions
from app.templates_setup import templates

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Categories ──

@router.get("/categories", response_class=HTMLResponse)
async def admin_categories(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    categories = await category_service.list_categories(db)
    ctx = template_context(request, current_user, categories=categories)
    return templates.TemplateResponse("admin/categories.html", ctx)


@router.post("/categories")
async def admin_create_category(
    name: str = Form(...),
    description: str = Form(""),
    sort_order: int = Form(0),
    complexity_weight: float = Form(1.0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    await category_service.create_category(db, name, description, sort_order, complexity_weight)
    return RedirectResponse(url="/admin/categories", status_code=302)


@router.post("/categories/{category_id}/edit")
async def admin_update_category(
    category_id: int,
    name: str = Form(...),
    description: str = Form(""),
    is_active: bool = Form(True),
    sort_order: int = Form(0),
    complexity_weight: float = Form(1.0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    await category_service.update_category(db, category_id, name, description, is_active, sort_order, complexity_weight)
    return RedirectResponse(url="/admin/categories", status_code=302)


@router.post("/categories/{category_id}/toggle")
async def admin_toggle_category(
    category_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    await category_service.toggle_category(db, category_id)
    return RedirectResponse(url="/admin/categories", status_code=302)


# ── Users ──

@router.get("/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    users = await list_users(db)
    ctx = template_context(request, current_user, users=users)
    return templates.TemplateResponse("admin/users.html", ctx)


@router.post("/users")
async def admin_create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(...),
    email: str = Form(""),
    role: str = Form("it_staff"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        from app.services.auth_service import create_user
        await create_user(db, username, password, display_name, email or None, role)
        return RedirectResponse(url="/admin/users", status_code=302)
    except Exception as e:
        users = await list_users(db)
        ctx = template_context(request, current_user, users=users,
                               error=str(e.detail) if hasattr(e, "detail") else str(e))
        return templates.TemplateResponse("admin/users.html", ctx, status_code=400)


@router.post("/users/{user_id}/toggle-active")
async def admin_toggle_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    await toggle_user_active(db, user_id)
    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/users/{user_id}/set-role")
async def admin_set_role(
    user_id: int,
    role: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    await update_user_role(db, user_id, role)
    return RedirectResponse(url="/admin/users", status_code=302)


# ── Permission reference ──

@router.get("/permissions", response_class=HTMLResponse)
async def admin_permissions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    perms = await get_transition_permissions(db)
    rows = []
    for src in VALID_TRANSITIONS:
        for tgt in VALID_TRANSITIONS[src]:
            perm = get_permission(perms, src, tgt)
            perm_label = {"any_staff": "IT人员（任何人）", "owner": "指派人", "admin_only": "管理员"}.get(perm, perm)
            rows.append({
                "from_label": STATUS_DISPLAY.get(src, src),
                "from": src,
                "to_label": STATUS_DISPLAY.get(tgt, tgt),
                "to": tgt,
                "perm": perm,
                "perm_label": perm_label,
            })
    ctx = template_context(request, current_user, rows=rows)
    return templates.TemplateResponse("admin/permissions.html", ctx)


@router.post("/permissions/save")
async def admin_save_permission(
    from_status: str = Form(...),
    to_status: str = Form(...),
    permission: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    from app.models.transition_permission import TransitionPermission
    from sqlalchemy import select as sa_select
    result = await db.execute(
        sa_select(TransitionPermission).where(
            TransitionPermission.from_status == from_status,
            TransitionPermission.to_status == to_status,
        )
    )
    row = result.scalar_one_or_none()
    if row:
        row.permission = permission
    else:
        db.add(TransitionPermission(from_status=from_status, to_status=to_status, permission=permission))
    await db.commit()
    return RedirectResponse(url="/admin/permissions", status_code=302)
