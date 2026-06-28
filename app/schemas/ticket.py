from pydantic import BaseModel, Field, field_validator
from typing import Optional

VALID_PRIORITIES = {"紧急", "高", "中", "低"}

class TicketCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = ""
    priority: str = "中"
    category: Optional[str] = None
    creator_name: str = "匿名用户"

    @field_validator("priority")
    @classmethod
    def check_priority(cls, v: str) -> str:
        if v not in VALID_PRIORITIES:
            raise ValueError(f"无效优先级: {v}，可选值: {VALID_PRIORITIES}")
        return v


class TicketUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    category: Optional[str] = None
    assignee: Optional[str] = None
    resolution_notes: Optional[str] = None

    @field_validator("priority")
    @classmethod
    def check_priority(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_PRIORITIES:
            raise ValueError(f"无效优先级: {v}，可选值: {VALID_PRIORITIES}")
        return v


class TicketStatusUpdate(BaseModel):
    status: str
    resolution_notes: str = ""


class TicketCommentCreate(BaseModel):
    comment: str
    actor: Optional[str] = None
