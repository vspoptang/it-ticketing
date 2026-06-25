"""Shared fixtures for IT Ticketing tests."""
import asyncio, os, sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["JWT_SECRET_KEY"]="test...testkey123456789012345678901234"
os.environ["ADMIN_DEFAULT_PASSWORD"]="test...secretsabove" * 2

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import get_db
from app.main import app as fastapi_app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        from app.db_migrations import ensure_db_schema
        await conn.run_sync(ensure_db_schema)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as session:
        yield session


@pytest_asyncio.fixture
async def client(db):
    async def override_get_db():
        yield db
    fastapi_app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_user(db: AsyncSession):
    from app.services.auth_service import create_user
    return await create_user(db, "testadmin", "testpass123", "Test Admin", role="admin")


@pytest_asyncio.fixture
async def staff_user(db: AsyncSession):
    from app.services.auth_service import create_user
    return await create_user(db, "teststaff", "testpass123", "Test Staff", role="it_staff")


@pytest_asyncio.fixture
async def end_user(db: AsyncSession):
    from app.services.auth_service import create_user
    return await create_user(db, "testuser", "testpass123", "Test User")


@pytest_asyncio.fixture
async def sample_ticket(db: AsyncSession, admin_user):
    from app.services import ticket_service
    from app.schemas.ticket import TicketCreate
    data = TicketCreate(
        title="Test Ticket", description="Test description",
        priority="medium", category="Software",
        creator_name=admin_user.display_name,
    )
    return await ticket_service.create_ticket(db, data)
