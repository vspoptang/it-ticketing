from pydantic import BaseModel
from typing import Any, Optional


class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int
    total_pages: int


class APIResponse(BaseModel):
    success: bool = True
    data: Any = None
    message: str = ""
