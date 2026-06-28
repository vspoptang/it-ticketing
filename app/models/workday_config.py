"""Workday & work-hours configuration model."""

from sqlalchemy import Boolean, Integer, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WorkdayConfig(Base):
    __tablename__ = "workday_config"

    day_of_week: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )  # 0=Mon, 1=Tue, ..., 6=Sun
    label: Mapped[str] = mapped_column(String(10), nullable=False)
    is_workday: Mapped[bool] = mapped_column(Boolean, default=True)
    work_start: Mapped[str] = mapped_column(String(5), default="08:00")
    work_end: Mapped[str] = mapped_column(String(5), default="17:00")
