from __future__ import annotations

import logging
from datetime import datetime
from app.timezone_helper import now

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ticket import Ticket
from app.models.notification import NotificationEvent

logger = logging.getLogger(__name__)


async def send_ticket_notifications(
    db: AsyncSession,
    ticket: Ticket,
    event_type: str,
    actor: str | None = None,
    bg_tasks: BackgroundTasks | None = None,
) -> None:
    """Queue notifications for ticket events.

    NOTE: Background tasks receive ticket metadata (not DB session) because the
    request-scoped session is closed before the background task runs.
    Each background task creates its own session.
    """
    subject = f"[IT工单] #{ticket.id} {ticket.title}"
    recipients: list[str] = []

    if event_type == "assigned" and ticket.assignee:
        subject = f"[IT工单] 您被指派了工单 #{ticket.id}"
        recipients.append(ticket.assignee)
    elif event_type == "completed":
        subject = f"[IT工单] 工单 #{ticket.id} 已完成"
        recipients.append(ticket.creator_name)
    elif event_type == "comment":
        subject = f"[IT工单] 工单 #{ticket.id} 有新备注"

    # Build ticket summary for background tasks (avoid passing ORM objects)
    ticket_summary = {
        "id": ticket.id,
        "ticket_number": ticket.ticket_number,
        "title": ticket.title,
        "status": ticket.status,
        "priority": ticket.priority,
    }

    # Queue email notifications
    if settings.SMTP_HOST and recipients:
        for recipient in recipients:
            body = _build_email_body(ticket, event_type, actor)
            if bg_tasks:
                bg_tasks.add_task(_send_email_async, ticket_summary["id"], recipient, subject, body)

    # Queue webhook notifications
    webhook_urls = [u.strip() for u in settings.WEBHOOK_URLS.split(",") if u.strip()]
    if webhook_urls:
        payload = {
            "ticket_id": ticket.id,
            "ticket_number": ticket.ticket_number,
            "title": ticket.title,
            "status": ticket.status,
            "event_type": event_type,
            "actor": actor,
        }
        for url in webhook_urls:
            if bg_tasks:
                bg_tasks.add_task(_send_webhook_async, ticket_summary["id"], url, payload)


def _build_email_body(ticket: Ticket, event_type: str, actor: str | None) -> str:
    lines = [
        f"工单: {ticket.ticket_number or ('#' + str(ticket.id))}",
        f"标题: {ticket.title}",
        f"状态: {ticket.status}",
        f"优先级: {ticket.priority}",
        "",
        f"事件: {event_type}",
    ]
    if actor:
        lines.append(f"操作人: {actor}")
    return "\n".join(lines)


async def _send_email_async(
    ticket_id: int | None,
    recipient: str,
    subject: str,
    body: str,
) -> None:
    """Send email via SMTP and record result.

    Creates its own DB session — safe for background tasks.
    """
    from app.database import async_session

    async with async_session() as db:
        notif = NotificationEvent(
            ticket_id=ticket_id,
            event_type="email",
            recipient=recipient,
            subject=subject,
            message=body,
            status="pending",
        )
        db.add(notif)
        await db.commit()

        try:
            import aiosmtplib
            from email.mime.text import MIMEText

            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = settings.NOTIFICATION_FROM_EMAIL
            msg["To"] = recipient

            await aiosmtplib.send(
                msg,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER or None,
                password=settings.SMTP_PASSWORD or None,
                use_tls=settings.SMTP_USE_TLS,
            )
            notif.status = "sent"
            notif.sent_at = now()
        except Exception as e:
            notif.status = "failed"
            notif.error_message = str(e)
            logger.error(f"Email notification failed: {e}")

        await db.commit()


async def _send_webhook_async(
    ticket_id: int | None,
    url: str,
    payload: dict,
) -> None:
    """Send webhook and record result.

    Creates its own DB session — safe for background tasks.
    """
    from app.database import async_session

    async with async_session() as db:
        notif = NotificationEvent(
            ticket_id=ticket_id,
            event_type="webhook",
            recipient=url,
            message=str(payload),
            status="pending",
        )
        db.add(notif)
        await db.commit()

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            notif.status = "sent"
            notif.sent_at = now()
        except Exception as e:
            notif.status = "failed"
            notif.error_message = str(e)
            logger.error(f"Webhook notification failed: {e}")

        await db.commit()
