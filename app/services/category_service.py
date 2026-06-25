from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category


async def list_categories(db: AsyncSession) -> list[Category]:
    result = await db.execute(
        select(Category).order_by(Category.sort_order, Category.name)
    )
    return list(result.scalars().all())


async def get_active_categories(db: AsyncSession) -> list[Category]:
    result = await db.execute(
        select(Category)
        .where(Category.is_active == True)
        .order_by(Category.sort_order, Category.name)
    )
    return list(result.scalars().all())


async def get_category_by_id(db: AsyncSession, category_id: int) -> Category:
    result = await db.execute(select(Category).where(Category.id == category_id))
    cat = result.scalar_one_or_none()
    if cat is None:
        raise HTTPException(status_code=404, detail="分类不存在")
    return cat


async def create_category(
    db: AsyncSession, name: str, description: str = "", sort_order: int = 0, complexity_weight: float = 1.0
) -> Category:
    result = await db.execute(select(Category).where(Category.name == name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"分类「{name}」已存在")
    cat = Category(name=name, description=description or None, sort_order=sort_order, complexity_weight=complexity_weight)
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


async def update_category(
    db: AsyncSession, category_id: int, name: str = "", description: str = "",
    is_active: bool = True, sort_order: int | None = None, complexity_weight: float | None = None,
) -> Category:
    cat = await get_category_by_id(db, category_id)
    if name:
        cat.name = name
    if description:
        cat.description = description
    cat.is_active = is_active
    if sort_order is not None:
        cat.sort_order = sort_order
    if complexity_weight is not None:
        cat.complexity_weight = complexity_weight
    await db.commit()
    await db.refresh(cat)
    return cat


async def get_category_weight_map(db: AsyncSession) -> dict[str, float]:
    """Return {category_name: complexity_weight} for all active categories."""
    cats = await get_active_categories(db)
    return {c.name: c.complexity_weight for c in cats}


async def toggle_category(db: AsyncSession, category_id: int) -> Category:
    """Toggle category active status."""
    cat = await get_category_by_id(db, category_id)
    cat.is_active = not cat.is_active
    await db.commit()
    await db.refresh(cat)
    return cat
