"""Tests for auth_service: password hashing, token creation/validation, authentication."""

import pytest
import jwt
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.auth_service import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    authenticate_user,
    get_user_by_id,
    create_user,
)
from app.timezone_helper import now


class TestHashPassword:
    def test_hash_returns_bcrypt_string(self):
        result = hash_password("mypassword")
        assert result.startswith("$2b$") or result.startswith("$2a$")

    def test_hash_is_deterministic_verify(self):
        hashed = hash_password("secret123")
        assert verify_password("secret123", hashed) is True

    def test_wrong_password_fails(self):
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False


class TestToken:
    def test_create_token_contains_correct_payload(self, admin_user):
        token = create_access_token(admin_user)
        decoded = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        assert decoded["sub"] == str(admin_user.id)
        assert decoded["username"] == admin_user.username
        assert decoded["role"] == admin_user.role
        assert "exp" in decoded
        assert "iat" in decoded

    def test_decode_valid_token(self, admin_user):
        token = create_access_token(admin_user)
        payload = decode_access_token(token)
        assert payload["username"] == admin_user.username

    def test_decode_expired_token(self, admin_user):
        import jwt as pyjwt
        from app.timezone_helper import now

        expired_payload = {
            "sub": str(admin_user.id),
            "username": admin_user.username,
            "role": admin_user.role,
            "exp": now() - timedelta(hours=1),
            "iat": now() - timedelta(hours=25),
        }
        expired_token = pyjwt.encode(
            expired_payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
        )
        with pytest.raises(HTTPException) as exc:
            decode_access_token(expired_token)
        assert exc.value.status_code == 401
        assert "过期" in exc.value.detail

    def test_decode_invalid_token(self):
        with pytest.raises(HTTPException) as exc:
            decode_access_token("not.a.valid.token")
        assert exc.value.status_code == 401


class TestAuthenticateUser:
    async def test_valid_credentials(self, db: AsyncSession, admin_user):
        user = await authenticate_user(db, "testadmin", "testpass123")
        assert user is not None
        assert user.username == "testadmin"

    async def test_wrong_password(self, db: AsyncSession, admin_user):
        user = await authenticate_user(db, "testadmin", "wrongpass")
        assert user is None

    async def test_nonexistent_user(self, db: AsyncSession):
        user = await authenticate_user(db, "nobody", "whatever")
        assert user is None


class TestUserCRUD:
    async def test_get_user_by_id(self, db: AsyncSession, admin_user):
        user = await get_user_by_id(db, admin_user.id)
        assert user is not None
        assert user.username == "testadmin"

    async def test_get_nonexistent_user(self, db: AsyncSession):
        user = await get_user_by_id(db, 99999)
        assert user is None

    async def test_create_duplicate_user_fails(self, db: AsyncSession, admin_user):
        with pytest.raises(HTTPException) as exc:
            await create_user(db, "testadmin", "pass123", "Duplicate")
        assert exc.value.status_code == 400
        assert "已存在" in exc.value.detail
