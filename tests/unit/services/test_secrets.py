from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import ManagedSecret, SecretStatus
from api_service.db.models import SettingsOverride
from api_service.services.secrets import SecretsService

@pytest.fixture
def mock_db_session():
    """Mock an AsyncSession for testing."""
    session = MagicMock(spec=AsyncSession)
    session.sync_session = MagicMock()
    
    # Mock commit, refresh, add to return awaitable objects
    async def mock_commit(): pass
    async def mock_refresh(instance): pass
    session.commit.side_effect = mock_commit
    session.refresh.side_effect = mock_refresh
    
    return session

@pytest.mark.asyncio
async def test_create_secret(mock_db_session):
    slug = "test-secret"
    plaintext = "super-secret-value"
    
    secret = await SecretsService.create_secret(mock_db_session, slug, plaintext, details={"test": True})
    
    assert secret.slug == slug
    assert secret.ciphertext == plaintext
    assert secret.status == SecretStatus.ACTIVE
    assert secret.details == {"test": True}
    
    mock_db_session.add.assert_called_once_with(secret)
    mock_db_session.commit.assert_called_once()
    mock_db_session.refresh.assert_called_once_with(secret)

@pytest.mark.asyncio
async def test_update_secret(mock_db_session):
    slug = "test-secret"
    existing_secret = ManagedSecret(slug=slug, ciphertext="old", status=SecretStatus.ACTIVE)
    
    # Setup mock query execution
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_secret
    
    async def mock_execute(*args, **kwargs):
        return mock_result
        
    mock_db_session.execute.side_effect = mock_execute
    
    updated = await SecretsService.update_secret(mock_db_session, slug, "new-value")
    
    assert updated.slug == slug
    assert updated.ciphertext == "new-value"
    mock_db_session.commit.assert_called_once()
    mock_db_session.refresh.assert_called_once_with(updated)

@pytest.mark.asyncio
async def test_rotate_secret(mock_db_session):
    slug = "test-secret"
    existing_secret = ManagedSecret(slug=slug, ciphertext="old", status=SecretStatus.ACTIVE)
    
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_secret
    
    async def mock_execute(*args, **kwargs):
        return mock_result
        
    mock_db_session.execute.side_effect = mock_execute
    
    rotated = await SecretsService.rotate_secret(mock_db_session, slug, "new-value")
    
    assert rotated.ciphertext == "new-value"
    assert rotated.status == SecretStatus.ROTATED

@pytest.mark.asyncio
async def test_set_status_secret(mock_db_session):
    slug = "test-secret"
    existing_secret = ManagedSecret(slug=slug, ciphertext="old", status=SecretStatus.ACTIVE)
    
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_secret
    
    async def mock_execute(*args, **kwargs):
        return mock_result
        
    mock_db_session.execute.side_effect = mock_execute
    
    disabled = await SecretsService.set_status(mock_db_session, slug, SecretStatus.DISABLED)
    assert disabled.status == SecretStatus.DISABLED

@pytest.mark.asyncio
async def test_get_secret(mock_db_session):
    slug = "test-secret"
    existing_secret = ManagedSecret(slug=slug, ciphertext="plntxt", status=SecretStatus.ACTIVE)
    
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_secret
    
    async def mock_execute(*args, **kwargs):
        return mock_result
        
    mock_db_session.execute.side_effect = mock_execute
    
    fetched = await SecretsService.get_secret(mock_db_session, slug)
    assert fetched == "plntxt"


@pytest.mark.asyncio
async def test_validate_secret_ref_returns_redacted_active_diagnostic(mock_db_session):
    slug = "test-secret"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = SecretStatus.ACTIVE

    async def mock_execute(*args, **kwargs):
        return mock_result

    mock_db_session.execute.side_effect = mock_execute

    result = await SecretsService.validate_secret_ref(mock_db_session, slug)

    assert result["valid"] is True
    assert result["status"] == "active"
    assert result["diagnostics"][0]["code"] == "secret_ref_resolvable"
    execute_statement = mock_db_session.execute.call_args.args[0]
    assert "managed_secrets.status" in str(execute_statement)
    assert "managed_secrets.ciphertext" not in str(execute_statement)


@pytest.mark.asyncio
async def test_validate_secret_ref_reports_missing_without_plaintext(mock_db_session):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    async def mock_execute(*args, **kwargs):
        return mock_result

    mock_db_session.execute.side_effect = mock_execute

    result = await SecretsService.validate_secret_ref(mock_db_session, "missing-secret")

    assert result["valid"] is False
    assert result["status"] == "missing"
    assert result["diagnostics"][0] == {
        "code": "secret_ref_unresolved",
        "message": "Managed secret is missing.",
        "severity": "error",
    }


@pytest.mark.asyncio
async def test_list_secret_usage_reports_settings_consumers_without_plaintext(
    mock_db_session,
):
    raw_secret = "ghp_usage_plaintext"
    secret_status_result = MagicMock()
    secret_status_result.scalar_one_or_none.return_value = SecretStatus.ACTIVE
    usage_result = MagicMock()
    usage_result.scalars.return_value.all.return_value = [
        SettingsOverride(
            scope="workspace",
            key="integrations.github.token_ref",
            value_json="db://github-pat-main",
        )
    ]

    async def mock_execute(*args, **kwargs):
        return secret_status_result if mock_db_session.execute.call_count == 1 else usage_result

    mock_db_session.execute.side_effect = mock_execute

    result = await SecretsService.list_secret_usage(
        mock_db_session,
        "github-pat-main",
    )

    assert result["secretRef"] == "db://github-pat-main"
    assert result["usages"] == [
        {
            "consumerType": "setting_override",
            "objectName": "Workspace setting integrations.github.token_ref",
            "reference": "db://github-pat-main",
            "scope": "workspace",
            "settingKey": "integrations.github.token_ref",
        }
    ]
    assert raw_secret not in repr(result)


@pytest.mark.asyncio
async def test_list_secret_usage_reports_empty_and_missing_without_plaintext(
    mock_db_session,
):
    active_result = MagicMock()
    active_result.scalar_one_or_none.return_value = SecretStatus.ACTIVE
    empty_usage_result = MagicMock()
    empty_usage_result.scalars.return_value.all.return_value = []
    missing_result = MagicMock()
    missing_result.scalar_one_or_none.return_value = None

    async def active_execute(*args, **kwargs):
        return active_result if mock_db_session.execute.call_count == 1 else empty_usage_result

    mock_db_session.execute.side_effect = active_execute

    empty = await SecretsService.list_secret_usage(mock_db_session, "unused-secret")

    assert empty == {
        "secretRef": "db://unused-secret",
        "usages": [],
        "diagnostics": [],
    }

    mock_db_session.execute.reset_mock()
    mock_db_session.execute.side_effect = None

    async def missing_execute(*args, **kwargs):
        return missing_result

    mock_db_session.execute.side_effect = missing_execute

    missing = await SecretsService.list_secret_usage(mock_db_session, "missing-secret")

    assert missing["secretRef"] == "db://missing-secret"
    assert missing["usages"] == []
    assert missing["diagnostics"] == [
        {
            "code": "secret_ref_unresolved",
            "message": "Managed secret is missing.",
            "severity": "error",
        }
    ]
    assert "plaintext" not in repr(missing)

@pytest.mark.asyncio
async def test_import_from_env(mock_db_session):
    env_dict = {
        "KEY_1": "val1",
        "KEY_2": "val2",
    }
    
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None  # Mock that no secrets exist yet
    
    async def mock_execute(*args, **kwargs):
        return mock_result
        
    mock_db_session.execute.side_effect = mock_execute
    
    count = await SecretsService.import_from_env(mock_db_session, env_dict)

    assert count == 2
    assert mock_db_session.add.call_count == 2
    mock_db_session.commit.assert_called_once()

@pytest.mark.asyncio
async def test_import_from_env_does_not_overwrite_active_by_default(mock_db_session):
    active_secret = ManagedSecret(
        slug="KEY_1",
        ciphertext="old-value",
        status=SecretStatus.ACTIVE,
        details={"imported_from": ".env"},
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = active_secret

    async def mock_execute(*args, **kwargs):
        return mock_result

    mock_db_session.execute.side_effect = mock_execute

    count = await SecretsService.import_from_env(
        mock_db_session,
        {"KEY_1": "new-value"},
    )

    assert count == 0
    assert active_secret.ciphertext == "old-value"

@pytest.mark.asyncio
async def test_import_from_env_can_overwrite_active(mock_db_session):
    active_secret = ManagedSecret(
        slug="KEY_1",
        ciphertext="old-value",
        status=SecretStatus.ACTIVE,
        details={"imported_from": ".env", "migrated_at": "earlier"},
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = active_secret

    async def mock_execute(*args, **kwargs):
        return mock_result

    mock_db_session.execute.side_effect = mock_execute

    count = await SecretsService.import_from_env(
        mock_db_session,
        {"KEY_1": "new-value"},
        overwrite_active=True,
    )

    assert count == 1
    assert active_secret.ciphertext == "new-value"
