from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user, require_user, template_context
from app.models.user import User
from app.services.auth_service import authenticate_user
from app.templates_setup import templates

router = APIRouter(prefix="/auth", tags=["auth"])

# Simple in-memory rate limiter for login
_login_attempts: dict[str, list[float]] = {}


def _check_login_rate(client_ip: str) -> bool:
    """Return True if rate limit not exceeded."""
    import time
    now_ts = time.time()
    window = 60  # 1 minute window
    attempts = _login_attempts.get(client_ip, [])
    # Purge old entries
    attempts = [t for t in attempts if now_ts - t < window]
    _login_attempts[client_ip] = attempts
    if len(attempts) >= settings.RATE_LIMIT_LOGIN_PER_MINUTE:
        return False
    attempts.append(now_ts)
    return True


# ---------- login ----------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, current_user: User | None = Depends(get_current_user)):
    if current_user:
        return RedirectResponse(url="/tickets", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "current_user": None, "messages": []})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    # Rate limit check
    client_ip = request.client.host if request.client else "unknown"
    if not _check_login_rate(client_ip):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "current_user": None,
                "error": f"登录尝试过于频繁，请稍后再试（每分钟最多 {settings.RATE_LIMIT_LOGIN_PER_MINUTE} 次）",
                "messages": [],
            },
            status_code=429,
        )

    user = await authenticate_user(db, username, password)
    if user is None:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "current_user": None,
                "error": "用户名或密码错误",
                "messages": [],
            },
            status_code=401,
        )

    from app.services.auth_service import create_access_token

    token = create_access_token(user)
    response = RedirectResponse(url="/tickets", status_code=302)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=settings.JWT_EXPIRATION_HOURS * 3600,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
    )
    return response


# ---------- logout ----------

@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/auth/login", status_code=302)
    response.delete_cookie(key="access_token")
    return response
