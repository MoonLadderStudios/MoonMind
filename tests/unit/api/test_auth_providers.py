import uuid
import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_default_user_from_db
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
