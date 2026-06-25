import logging
import sys
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, engine
from app.db_migrations import ensure_db_schema
from app.routers.tickets import router as tickets_router
from app.routers.auth import router as auth_router
from app.routers.admin import router as admin_router
from app.routers.dashboard import router as dashboard_router
from app.routers.attachments import router as attachments_router
from app.routers.categories import router as categories_router
from app.routers.api_v1 import router as api_router

# ── Structured logging ──
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"
LOG_LEVEL = logging.DEBUG if settings.DEBUG else logging.INFO

logging.basicConfig(
    level=LOG_LEVEL,
    format=LOG_FORMAT,
    stream=sys.stdout,
)
# Silence noisy libraries
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Verify Origin/Referer for state-changing requests to prevent CSRF.

    Relies on SameSite=Lax cookies as primary defense; this is a defense-in-depth check.
    API endpoints (/api/) and GET/HEAD/OPTIONS are excluded.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)

        # Skip CSRF check for API endpoints (they use custom auth)
        if request.url.path.startswith("/api/"):
            return await call_next(request)

        # Verify Origin or Referer header matches the Host
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        host = request.headers.get("host", "")

        check_url = origin or referer
        if check_url:
            try:
                parsed = urlparse(check_url)
                check_host = parsed.netloc
                # Allow same host or localhost variants
                if check_host != host and check_host not in ("localhost:8000", "127.0.0.1:8000"):
                    return HTMLResponse(
                        content="<h1>CSRF validation failed</h1><p>Invalid request origin.</p>",
                        status_code=403,
                    )
            except Exception:
                return HTMLResponse(
                    content="<h1>CSRF validation failed</h1>",
                    status_code=403,
                )

        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(ensure_db_schema)
        await conn.run_sync(Base.metadata.create_all)
    import os
    os.makedirs("app/static/uploads", exist_ok=True)
    yield
    await engine.dispose()


app = FastAPI(title="IT工单系统", lifespan=lifespan)
app.add_middleware(CSRFMiddleware)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/health")
async def health():
    """Health check endpoint for Docker / load balancers."""
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(tickets_router)
app.include_router(admin_router)
app.include_router(dashboard_router)
app.include_router(attachments_router)
app.include_router(categories_router)
app.include_router(api_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/tickets", status_code=302)
