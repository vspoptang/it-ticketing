from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_user, template_context
from app.models.user import User
from app.services import attachment_service
from app.templates_setup import templates

router = APIRouter(prefix="/tickets", tags=["attachments"])


@router.get("/{ticket_id}/attachments", response_class=HTMLResponse)
async def list_attachments(
    request: Request,
    ticket_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    from app.services.ticket_service import get_ticket
    ticket = await get_ticket(db, ticket_id)
    ctx = template_context(request, current_user, ticket=ticket, attachments=ticket.attachments)
    return templates.TemplateResponse("tickets/_attachments.html", ctx)


@router.post("/{ticket_id}/attachments")
async def upload_attachment(
    request: Request,
    ticket_id: int,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    await attachment_service.save_upload(
        db, ticket_id, file, uploaded_by=current_user.display_name
    )
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=302)


@router.get("/{ticket_id}/attachments/{attachment_id}")
async def download_attachment(
    ticket_id: int,
    attachment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    att = await attachment_service.get_attachment(db, attachment_id)
    file_path = await attachment_service.get_attachment_path(att)
    return FileResponse(
        path=file_path,
        filename=att.original_filename,
        media_type=att.content_type or "application/octet-stream",
    )


@router.post("/{ticket_id}/attachments/{attachment_id}/delete")
async def delete_attachment(
    ticket_id: int,
    attachment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    await attachment_service.delete_attachment(db, attachment_id)
    return RedirectResponse(url=f"/tickets/{ticket_id}", status_code=302)
