"""Priority SLA configuration model."""

from sqlalchemy import String, Integer, Float
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PriorityConfig(Base):
    __tablename__ = "priority_config"

    priority: Mapped[str] = mapped_column(
        String(20), primary_key=True
    )  # urgent, high, medium, low
    label: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # 紧急, 高, 中, 低
    hours: Mapped[float] = mapped_column(
        Float, nullable=False, default=4.0
    )  # SLA hours
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0
    )
