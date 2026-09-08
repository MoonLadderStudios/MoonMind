import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import api_service.auth_providers as auth_providers
from api_service.auth_providers import get_current_user, get_default_user_from_db
from api_service.db.models import User
from moonmind.config.settings import settings

@pytest.mark.asyncio
async def test_get_default_user_happy_path(monkeypatch):
    user_id = str(uuid.uuid4())
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", user_id)
    default_user = User(
        id=uuid.UUID(user_id),
        email="default@example.com",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        hashed_password="x",
    )
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.get.return_value = default_user

    result = await get_default_user_from_db(mock_session)

    assert result.id == uuid.UUID(user_id)
    mock_session.get.assert_called_once_with(User, uuid.UUID(user_id))

@pytest.mark.asyncio
async def test_get_default_user_invalid_id(monkeypatch):
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", None)
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.get.return_value = None
    with pytest.raises(HTTPException) as exc:
        await get_default_user_from_db(mock_session)
    assert exc.value.status_code == 500
    assert exc.value.detail == "Default user not found"


@pytest.mark.asyncio
async def test_disabled_auth_missing_user_fails_closed_without_stub(monkeypatch):
    """#4120 R5: missing identity data fails closed (503), never a stub admin."""
    user_id = str(uuid.uuid4())
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", user_id)
    monkeypatch.setattr(settings.workflow, "test_mode", False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)

    def _ctx_factory():
        class _Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, *args, **kwargs):
                return None

        return _Ctx()

    import api_service.db.base as db_base

    monkeypatch.setattr(db_base, "get_async_session_context", _ctx_factory)

    dependency = get_current_user()
    with pytest.raises(HTTPException) as exc:
        await dependency()
    assert exc.value.status_code == 503
    monkeypatch.setattr(auth_providers, "_cached_current_user_dependency", None)
