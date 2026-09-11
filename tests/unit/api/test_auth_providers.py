import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

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
async def test_disabled_mode_missing_identity_fails_closed_without_stub(monkeypatch):
    """#4120/#4125: missing identity/DB data in local mode never mints a stub admin."""
    import moonmind.security.auth_modes_4120 as auth_modes
    from fastapi import Request

    user_id = str(uuid.uuid4())
    monkeypatch.setattr(auth_modes, "_ACTIVE_PRODUCTION_MODE", None)
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", user_id)

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.get.side_effect = ConnectionError("db down")
    scope = {"type": "http", "headers": [], "query_string": b""}
    request = Request(scope)

    dependency = get_current_user()
    with pytest.raises(HTTPException) as exc:
        await dependency(request, mock_session)
    assert exc.value.status_code == 503
    assert exc.value.detail in ("unavailable", "setup_required")
