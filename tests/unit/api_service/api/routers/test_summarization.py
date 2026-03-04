from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_service.api.routers.summarization import (
    get_user_github_token,
    get_user_llm_api_key,
)
from api_service.db.models import User


@pytest.mark.asyncio
async def test_get_user_github_token():
    db = AsyncMock()
    user = User()
    user.id = "test_user_id"

    with patch("api_service.api.routers.summarization.ProfileService") as mock_ps_class:
        mock_ps = mock_ps_class.return_value
        profile = MagicMock()
        profile.github_token_encrypted = "test_github_token"
        mock_ps.get_profile_by_user_id = AsyncMock(return_value=profile)

        token = await get_user_github_token(user, db)
        assert token == "test_github_token"


@pytest.mark.asyncio
async def test_get_user_llm_api_key():
    db = AsyncMock()
    user = User()
    user.id = "test_user_id"

    with patch("api_service.api.routers.summarization.ProfileService") as mock_ps_class:
        mock_ps = mock_ps_class.return_value
        profile = MagicMock()
        profile.openai_api_key_encrypted = "test_openai_key"
        mock_ps.get_profile_by_user_id = AsyncMock(return_value=profile)

        token = await get_user_llm_api_key(user, "openai", db)
        assert token == "test_openai_key"


@pytest.mark.asyncio
async def test_get_user_llm_api_key_ollama():
    db = AsyncMock()
    user = User()
    user.id = "test_user_id"

    token = await get_user_llm_api_key(user, "ollama", db)
    assert token is None


@pytest.mark.asyncio
async def test_get_user_llm_api_key_no_profile():
    db = AsyncMock()
    user = User()
    user.id = "test_user_id"

    with patch("api_service.api.routers.summarization.ProfileService") as mock_ps_class:
        mock_ps = mock_ps_class.return_value
        mock_ps.get_profile_by_user_id = AsyncMock(return_value=None)

        token = await get_user_llm_api_key(user, "anthropic", db)
        assert token is None
