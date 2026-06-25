from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.auth_service import decode_access_token, get_user_by_id
from app.models.user import User


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """从 cookie 中提取当前用户；未登录返回 None"""
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        user = await get_user_by_id(db, int(payload["sub"]))
        if user is None or not user.is_active:
            return None
        return user
    except HTTPException:
        return None


async def require_user(user: User | None = Depends(get_current_user)) -> User:
    """要求已登录，否则返回 401"""
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


async def require_admin(user: User = Depends(require_user)) -> User:
    """要求管理员角色"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


async def require_staff(user: User = Depends(require_user)) -> User:
    """要求 IT 人员或管理员角色"""
    if user.role not in ("admin", "it_staff"):
        raise HTTPException(status_code=403, detail="需要IT人员权限")
    return user


def flash(request: Request, text: str, msg_type: str = "info") -> None:
    """Add a flash message to be displayed on the next rendered page."""
    if not hasattr(request.state, "_messages"):
        request.state._messages = []
    request.state._messages.append({"text": text, "type": msg_type})


def template_context(request: Request, current_user: User | None, **kwargs) -> dict:
    """注入模板公共上下文：request、current_user、messages"""
    messages = getattr(request.state, "_messages", [])
    return {"request": request, "current_user": current_user, "messages": messages, **kwargs}
