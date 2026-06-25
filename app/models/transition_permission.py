from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TransitionPermission(Base):
    __tablename__ = "transition_permissions"

    from_status: Mapped[str] = mapped_column(String(20), primary_key=True)
    to_status: Mapped[str] = mapped_column(String(20), primary_key=True)
    permission: Mapped[str] = mapped_column(
        String(20), nullable=False, default="admin_only"
    )  # "any_staff" | "owner" | "admin_only"
