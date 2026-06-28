"""Password management service: change, reset, forgot-password."""

import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.models.password_reset import PasswordResetToken
from app.services.auth_service import hash_password, verify_password
from app.timezone_helper import now


async def change_password(
    db: AsyncSession,
    user: User,
    old_password: str,
    new_password: str,
) -> None:
    """Change password for an authenticated user."""
    if not verify_password(old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")

    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码长度不能少于8位")

    user.password_hash = hash_password(new_password)
    user.updated_at = now()
    await db.commit()


async def create_reset_token(db: AsyncSession, username: str) -> str | None:
    """Create a password reset token. Returns the token string, or None if user not found."""
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None

    # Generate token
    token = secrets.token_urlsafe(32)
    expires_at = now() + timedelta(hours=1)

    reset = PasswordResetToken(
        user_id=user.id,
        token=token,
        expires_at=expires_at,
    )
    db.add(reset)
    await db.commit()

    return token


async def reset_password(db: AsyncSession, token: str, new_password: str) -> User:
    """Reset password using a valid reset token."""
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码长度不能少于8位")

    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token == token,
            PasswordResetToken.used == False,
        )
    )
    reset = result.scalar_one_or_none()

    if reset is None:
        raise HTTPException(status_code=400, detail="无效的重置链接")
    if reset.expires_at < now():
        raise HTTPException(status_code=400, detail="重置链接已过期，请重新申请")

    # Get user
    result = await db.execute(select(User).where(User.id == reset.user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=400, detail="用户不存在")

    # Update password
    user.password_hash = hash_password(new_password)
    user.updated_at = now()
    reset.used = True
    await db.commit()

    return user


async def admin_reset_password(db: AsyncSession, user_id: int, new_password: str) -> User:
    """Admin resets a user's password."""
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码长度不能少于8位")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.password_hash = hash_password(new_password)
    user.updated_at = now()
    await db.commit()

    return user
