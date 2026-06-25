from __future__ import annotations

import os
import uuid

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.attachment import Attachment


async def save_upload(
    db: AsyncSession,
    ticket_id: int,
    file: UploadFile,
    uploaded_by: str | None = None,
) -> Attachment:
    # Validate file extension
    ext = os.path.splitext(file.filename or "file")[1].lower()
    allowed_extensions = {
        ext.strip().lower()
        for ext in settings.ALLOWED_UPLOAD_EXTENSIONS.split(",")
        if ext.strip()
    }
    if not allowed_extensions:
        allowed_extensions = {".pdf", ".doc", ".docx", ".xls", ".xlsx",
                              ".txt", ".png", ".jpg", ".jpeg", ".gif", ".zip"}
    if ext not in allowed_extensions and file.filename:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型「{ext}」。允许的类型: {', '.join(sorted(allowed_extensions))}",
        )

    # Validate size
    contents = await file.read()
    file_size = len(contents)
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if file_size > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"文件大小不能超过 {settings.MAX_UPLOAD_SIZE_MB}MB",
        )

    # Generate unique storage name
    storage_name = f"{uuid.uuid4().hex}{ext}"
    ticket_dir = os.path.join(settings.UPLOAD_DIR, str(ticket_id))
    os.makedirs(ticket_dir, exist_ok=True)
    file_path = os.path.join(ticket_dir, storage_name)

    # Write to disk
    with open(file_path, "wb") as f:
        f.write(contents)

    # Create DB record
    attachment = Attachment(
        ticket_id=ticket_id,
        filename=storage_name,
        original_filename=file.filename or "file",
        content_type=file.content_type,
        file_size=file_size,
        uploaded_by=uploaded_by,
    )
    db.add(attachment)
    await db.commit()
    await db.refresh(attachment)
    return attachment


async def get_attachment(db: AsyncSession, attachment_id: int) -> Attachment:
    result = await db.execute(select(Attachment).where(Attachment.id == attachment_id))
    att = result.scalar_one_or_none()
    if att is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    return att


async def get_attachment_path(attachment: Attachment) -> str:
    return os.path.join(settings.UPLOAD_DIR, str(attachment.ticket_id), attachment.filename)


async def delete_attachment(db: AsyncSession, attachment_id: int) -> None:
    att = await get_attachment(db, attachment_id)
    file_path = await get_attachment_path(att)
    if os.path.exists(file_path):
        os.remove(file_path)
    await db.delete(att)
    await db.commit()
