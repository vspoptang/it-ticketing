from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.timezone_helper import now


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    assignee: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    creator_name: Mapped[str] = mapped_column(String(100), nullable=False)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: now()
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=lambda: now()
    )

    # P1: SLA + ticket number
    ticket_number: Mapped[Optional[str]] = mapped_column(
        String(15), unique=True, nullable=True, index=True
    )
    sla_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_response_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    satisfaction: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )  # satisfied / neutral / unsatisfied

    events: Mapped[List["TicketEvent"]] = relationship(
        "TicketEvent",
        back_populates="ticket",
        order_by="TicketEvent.created_at",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    attachments: Mapped[List["Attachment"]] = relationship(
        "Attachment",
        back_populates="ticket",
        order_by="Attachment.uploaded_at",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TicketEvent(Base):
    __tablename__ = "ticket_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actor: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: now()
    )

    ticket: Mapped["Ticket"] = relationship(back_populates="events")
