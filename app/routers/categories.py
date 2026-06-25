from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_staff, template_context
from app.services import category_service

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("/options", response_class=HTMLResponse)
async def category_options(
    request: Request,
    selected: str = "",
    db: AsyncSession = Depends(get_db),
):
    """HTMX partial: render <option> list for dropdowns."""
    categories = await category_service.get_active_categories(db)
    options_html = ""
    for cat in categories:
        sel = " selected" if cat.name == selected else ""
        options_html += f'<option value="{cat.name}"{sel}>{cat.name}</option>\n'
    return HTMLResponse(content=options_html)
