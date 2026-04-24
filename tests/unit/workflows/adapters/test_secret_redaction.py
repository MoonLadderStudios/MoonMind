import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from moonmind.workflows.adapters.secret_boundary import DatabaseSecretResolver

@pytest.mark.asyncio
async def test_database_secret_resolver():
    db_session_mock = AsyncMock()
    
    # Mock the SQLAlchemy select result
    result_mock = MagicMock()
    # Assume secret_val inside result.scalars().first()
    secret_mock = MagicMock()
    secret_mock.ciphertext = "encrypted_super_secret_key"
    
    result_mock.scalars.return_value.first.return_value = secret_mock
    db_session_mock.execute.return_value = result_mock
    
    resolver = DatabaseSecretResolver(db_session=db_session_mock)
    
    ref_id = str(uuid.uuid4())
    secret_refs = {"ANTHROPIC_API_KEY": ref_id}
    
    resolved = await resolver.resolve_secrets(secret_refs)
    
    assert "ANTHROPIC_API_KEY" in resolved
    assert resolved["ANTHROPIC_API_KEY"] == "encrypted_super_secret_key"

@pytest.mark.asyncio
async def test_database_secret_resolver_invalid_uuid():
    db_session_mock = AsyncMock()
    resolver = DatabaseSecretResolver(db_session=db_session_mock)
    
    secret_refs = {"ANTHROPIC_API_KEY": "not-a-uuid"}
    resolved = await resolver.resolve_secrets(secret_refs)

    assert resolved == {}
    db_session_mock.execute.assert_not_called()

@pytest.mark.asyncio
async def test_database_secret_resolver_supports_db_slug_refs():
    db_session_mock = AsyncMock()

    result_mock = MagicMock()
    secret_mock = MagicMock()
    secret_mock.ciphertext = "decrypted_claude_token"
    result_mock.scalars.return_value.first.return_value = secret_mock
    db_session_mock.execute.return_value = result_mock

    resolver = DatabaseSecretResolver(db_session=db_session_mock)

    resolved = await resolver.resolve_secrets(
        {"ANTHROPIC_API_KEY": "db://claude-anthropic-token"}
    )

    assert resolved == {"ANTHROPIC_API_KEY": "decrypted_claude_token"}
    db_session_mock.execute.assert_called_once()
