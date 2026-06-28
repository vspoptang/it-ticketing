"""Tests for ticket_service: CRUD, transitions, SLA, search, permissions."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ticket import TicketCreate
from app.services import ticket_service
from app.services.ticket_service import (
    STATUS_DISPLAY,
    VALID_TRANSITIONS,
    compute_sla_due_at,
    validate_transition,
)
from app.models.ticket import Ticket


class TestCreateTicket:
    async def test_create_ticket_success(self, db: AsyncSession, admin_user):
        data = TicketCreate(
            title="网络故障",
            description="无法连接VPN",
            priority="high",
            category="网络",
            creator_name=admin_user.display_name,
        )
        ticket = await ticket_service.create_ticket(db, data)
        assert ticket.id is not None
        assert ticket.title == "网络故障"
        assert ticket.status == "pending"
        assert ticket.ticket_number is not None
        assert ticket.ticket_number.startswith("IT-")

    async def test_create_ticket_generates_sla(self, db: AsyncSession, admin_user):
        data = TicketCreate(
            title="紧急问题",
            priority="urgent",
            category="硬件",
            creator_name=admin_user.display_name,
        )
        ticket = await ticket_service.create_ticket(db, data)
        assert ticket.sla_due_at is not None

    async def test_create_ticket_events_recorded(self, db: AsyncSession, admin_user):
        data = TicketCreate(
            title="测试事件",
            priority="low",
            category="其他",
            creator_name=admin_user.display_name,
        )
        ticket = await ticket_service.create_ticket(db, data)
        assert len(ticket.events) >= 1
        assert ticket.events[0].event_type == "created"


class TestGetTicket:
    async def test_get_existing_ticket(self, db: AsyncSession, sample_ticket):
        ticket = await ticket_service.get_ticket(db, sample_ticket.id)
        assert ticket.id == sample_ticket.id

    async def test_get_nonexistent_ticket(self, db: AsyncSession):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await ticket_service.get_ticket(db, 99999)
        assert exc.value.status_code == 404


class TestStatusTransitions:
    async def test_valid_transition_pending_to_in_progress(
        self, db: AsyncSession, sample_ticket, staff_user
    ):
        ticket = await ticket_service.update_ticket_status(
            db, sample_ticket.id, "in_progress",
            actor=staff_user.display_name,
        )
        assert ticket.status == "in_progress"
        assert ticket.first_response_at is not None

    async def test_invalid_transition_raises(self, db: AsyncSession, sample_ticket):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            # pending -> completed is not allowed directly
            await ticket_service.update_ticket_status(
                db, sample_ticket.id, "completed",
                actor="someone",
            )
        assert exc.value.status_code == 400

    async def test_complete_sets_resolved_at(self, db: AsyncSession, sample_ticket, staff_user):
        # First move to in_progress
        await ticket_service.update_ticket_status(
            db, sample_ticket.id, "in_progress", actor=staff_user.display_name,
        )
        # Then complete
        ticket = await ticket_service.update_ticket_status(
            db, sample_ticket.id, "completed", actor=staff_user.display_name,
        )
        assert ticket.status == "completed"
        assert ticket.resolved_at is not None


class TestListTickets:
    async def test_list_returns_paginated(self, db: AsyncSession, sample_ticket):
        result = await ticket_service.list_tickets(db, page=1, page_size=10)
        assert result["total"] >= 1
        assert result["page"] == 1
        assert len(result["items"]) >= 1

    async def test_list_filter_by_status(self, db: AsyncSession, sample_ticket):
        result = await ticket_service.list_tickets(db, status="pending")
        for t in result["items"]:
            assert t.status == "pending"

    async def test_list_search_by_title(self, db: AsyncSession, sample_ticket):
        result = await ticket_service.list_tickets(db, q="Test")
        assert result["total"] >= 1

    async def test_list_search_no_results(self, db: AsyncSession, sample_ticket):
        result = await ticket_service.list_tickets(db, q="不存在的关键词12345xyz")
        assert result["total"] == 0


class TestAddComment:
    async def test_add_comment_creates_event(self, db: AsyncSession, sample_ticket, admin_user):
        event = await ticket_service.add_ticket_comment(
            db, sample_ticket.id, "这是一条备注", actor=admin_user.display_name,
        )
        assert event.event_type == "comment"
        assert event.message == "这是一条备注"
        assert event.actor == admin_user.display_name


class TestSlaComputation:
    def test_sla_urgent_returns_future(self):
        due = compute_sla_due_at("urgent")
        from app.timezone_helper import now
        assert due is not None
        assert due > now()

    def test_sla_invalid_priority_returns_none(self):
        assert compute_sla_due_at("nonexistent") is None

    def test_sla_low_is_longer_than_urgent(self):
        from app.timezone_helper import now
        urgent_due = compute_sla_due_at("urgent")
        low_due = compute_sla_due_at("low", start_time=now())
        # Low SLA (48h) should be later than urgent (4h)
        assert low_due > urgent_due
