from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import ManagedSecret, SecretStatus
from api_service.services.secrets import (
    SecretFencedError,
    SecretRepairRequiredError,
    SecretsService,
)

@pytest.fixture
def mock_db_session():
    """Mock an AsyncSession for testing."""
    session = MagicMock(spec=AsyncSession)
    session.sync_session = MagicMock()

    # Mock commit, refresh, add to return awaitable objects
    async def mock_commit(): pass
    async def mock_refresh(instance): pass
    async def mock_flush(*args, **kwargs): pass
    async def mock_rollback(): pass
    session.commit.side_effect = mock_commit
    session.refresh.side_effect = mock_refresh
    session.flush.side_effect = mock_flush
    session.rollback.side_effect = mock_rollback

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
    assert secret.credential_revision == 1
    assert secret.policy_revision == 1

    mock_db_session.add.assert_any_call(secret)
    # Unified audit semantics: creation records a redacted audit event plus
    # restart-safe invalidation evidence in the same transaction.
    from api_service.db.models import SecretInvalidationOutbox, SettingsAuditEvent

    added_types = [type(call.args[0]) for call in mock_db_session.add.call_args_list]
    assert SettingsAuditEvent in added_types
    assert SecretInvalidationOutbox not in added_types  # create needs no invalidation
    audit = next(
        call.args[0]
        for call in mock_db_session.add.call_args_list
        if isinstance(call.args[0], SettingsAuditEvent)
    )
    assert audit.event_type == "secrets.created"
    assert audit.redacted is True
    assert audit.new_value_json["credential_revision"] == 1

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
    assert updated.credential_revision == 2
    assert updated.policy_revision == 1
    mock_db_session.commit.assert_called_once()
    mock_db_session.refresh.assert_called_once_with(updated)

@pytest.mark.asyncio
async def test_update_secret_rejects_stale_revision(mock_db_session):
    existing_secret = ManagedSecret(
        slug="test-secret",
        ciphertext="old",
        status=SecretStatus.ACTIVE,
        credential_revision=5,
        policy_revision=2,
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_secret

    async def mock_execute(*args, **kwargs):
        return mock_result

    mock_db_session.execute.side_effect = mock_execute

    with pytest.raises(SecretFencedError):
        await SecretsService.update_secret(
            mock_db_session, "test-secret", "new-value", expected_credential_revision=4
        )
    assert existing_secret.ciphertext == "old"
    assert existing_secret.credential_revision == 5

@pytest.mark.asyncio
async def test_update_secret_refuses_rotated_without_repair(mock_db_session):
    existing_secret = ManagedSecret(
        slug="test-secret", ciphertext="old", status=SecretStatus.ROTATED
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_secret

    async def mock_execute(*args, **kwargs):
        return mock_result

    mock_db_session.execute.side_effect = mock_execute

    with pytest.raises(SecretRepairRequiredError):
        await SecretsService.update_secret(mock_db_session, "test-secret", "new-value")

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

    # Rotation is an event/revision transition: the replacement stays ACTIVE
    # under the existing resolution contract at a new credential revision.
    assert rotated.ciphertext == "new-value"
    assert rotated.status == SecretStatus.ACTIVE
    assert rotated.credential_revision == 2

    from api_service.db.models import SettingsAuditEvent

    audit_calls = [
        call.args[0]
        for call in mock_db_session.add.call_args_list
        if isinstance(call.args[0], SettingsAuditEvent)
    ]
    assert audit_calls and audit_calls[0].event_type == "secrets.rotated"
    assert audit_calls[0].redacted is True

@pytest.mark.asyncio
async def test_rotate_secret_refuses_stale_expected_revision(mock_db_session):
    existing_secret = ManagedSecret(
        slug="test-secret",
        ciphertext="old",
        status=SecretStatus.ACTIVE,
        credential_revision=7,
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_secret

    async def mock_execute(*args, **kwargs):
        return mock_result

    mock_db_session.execute.side_effect = mock_execute

    with pytest.raises(SecretFencedError):
        await SecretsService.rotate_secret(
            mock_db_session,
            "test-secret",
            "new-value",
            expected_credential_revision=6,
        )
    assert existing_secret.ciphertext == "old"
    assert existing_secret.credential_revision == 7

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
    # Metadata-only transitions advance the policy revision, not the value.
    assert disabled.credential_revision == 1
    assert disabled.policy_revision == 2


@pytest.mark.asyncio
async def test_set_status_records_audit_event(mock_db_session):
    from api_service.db.models import SettingsAuditEvent

    slug = "test-secret"
    existing_secret = ManagedSecret(slug=slug, ciphertext="old", status=SecretStatus.ACTIVE)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_secret

    async def mock_execute(*args, **kwargs):
        return mock_result

    mock_db_session.execute.side_effect = mock_execute

    actor_id = uuid4()
    workspace_id = uuid4()

    await SecretsService.set_status(
        mock_db_session,
        slug,
        SecretStatus.DISABLED,
        actor_user_id=actor_id,
        workspace_id=workspace_id,
        reason="rotation cadence",
    )

    audit_calls = [
        call.args[0]
        for call in mock_db_session.add.call_args_list
        if call.args and isinstance(call.args[0], SettingsAuditEvent)
    ]
    assert audit_calls, "Expected SettingsAuditEvent to be persisted for status change"
    event = audit_calls[0]
    assert event.event_type == "secrets.status.changed"
    assert event.actor_user_id == actor_id
    assert event.workspace_id == workspace_id
    assert event.redacted is True
    assert event.old_value_json == {
        "status": "active",
        "credential_revision": 1,
        "policy_revision": 1,
    }
    assert event.new_value_json == {
        "status": "disabled",
        "credential_revision": 1,
        "policy_revision": 2,
    }
    assert event.reason == "rotation cadence"
    assert event.key == f"secrets.{slug}"

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
async def test_get_secret_fences_stale_revision(mock_db_session):
    existing_secret = ManagedSecret(
        slug="test-secret",
        ciphertext="plntxt",
        status=SecretStatus.ACTIVE,
        credential_revision=4,
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_secret

    async def mock_execute(*args, **kwargs):
        return mock_result

    mock_db_session.execute.side_effect = mock_execute

    assert (
        await SecretsService.get_secret(
            mock_db_session, "test-secret", expected_revision=3
        )
        is None
    )
    assert (
        await SecretsService.get_secret(
            mock_db_session, "test-secret", expected_revision=4
        )
        == "plntxt"
    )


@pytest.mark.asyncio
async def test_validate_secret_ref_returns_redacted_active_diagnostic(mock_db_session):
    slug = "test-secret"
    mock_result = MagicMock()
    mock_result.one_or_none.return_value = (SecretStatus.ACTIVE, 3, 2)

    async def mock_execute(*args, **kwargs):
        return mock_result

    mock_db_session.execute.side_effect = mock_execute

    result = await SecretsService.validate_secret_ref(mock_db_session, slug)

    assert result["valid"] is True
    assert result["status"] == "active"
    assert result["credentialRevision"] == 3
    assert result["policyRevision"] == 2
    assert result["diagnostics"][0]["code"] == "secret_ref_resolvable"
    execute_statement = mock_db_session.execute.call_args.args[0]
    assert "managed_secrets.status" in str(execute_statement)
    assert "managed_secrets.ciphertext" not in str(execute_statement)


@pytest.mark.asyncio
async def test_validate_secret_ref_reports_missing_without_plaintext(mock_db_session):
    mock_result = MagicMock()
    mock_result.one_or_none.return_value = None

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
async def test_validate_secret_ref_reports_repair_for_rotated(mock_db_session):
    mock_result = MagicMock()
    mock_result.one_or_none.return_value = (SecretStatus.ROTATED, 2, 1)

    async def mock_execute(*args, **kwargs):
        return mock_result

    mock_db_session.execute.side_effect = mock_execute

    result = await SecretsService.validate_secret_ref(mock_db_session, "old-secret")

    assert result["valid"] is False
    assert result["status"] == "rotated"
    assert result["diagnostics"][0]["code"] == "secret_repair_required"


def _usage_execute(mock_db_session, status_value, scoped_rows):
    status_result = MagicMock()
    status_result.scalar_one_or_none.return_value = status_value

    async def mock_execute(*args, **kwargs):
        call = mock_db_session.execute.call_count
        if call == 1:
            return status_result
        if call == 2:
            return scoped_rows
        return []

    mock_db_session.execute.side_effect = mock_execute


@pytest.mark.asyncio
async def test_list_secret_usage_reports_settings_consumers_without_plaintext(
    mock_db_session,
):
    raw_secret = "ghp_usage_plaintext"
    scoped_rows = [
        ("integrations.github.token_ref", "workspace", "db://github-pat-main")
    ]
    _usage_execute(mock_db_session, SecretStatus.ACTIVE, scoped_rows)

    result = await SecretsService.list_secret_usage(
        mock_db_session,
        "github-pat-main",
        workspace_id=uuid4(),
        user_id=uuid4(),
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
    usage_statement = mock_db_session.execute.call_args_list[1].args[0]
    assert "settings_overrides.key" in str(usage_statement)
    assert "settings_overrides.value_json" in str(usage_statement)
    assert "settings_overrides.workspace_id" in str(usage_statement)


@pytest.mark.asyncio
async def test_list_secret_usage_reports_provider_and_connection_consumers(
    mock_db_session,
):
    scoped_rows = [("some.key", "workspace", "db://shared-pat")]
    profile_result = [("codex-default", {"api_key": "db://shared-pat"})]
    connection_result = [("conn-1", {"pat": {"ref": "db://shared-pat"}})]
    status_result = MagicMock()
    status_result.scalar_one_or_none.return_value = SecretStatus.ACTIVE

    async def mock_execute(*args, **kwargs):
        call = mock_db_session.execute.call_count
        if call == 1:
            return status_result
        if call == 2:
            return scoped_rows
        if call == 3:
            return profile_result
        return connection_result

    mock_db_session.execute.side_effect = mock_execute

    result = await SecretsService.list_secret_usage(mock_db_session, "shared-pat")

    consumer_types = {usage["consumerType"] for usage in result["usages"]}
    assert consumer_types == {
        "setting_override",
        "provider_profile",
        "repository_connection",
    }


@pytest.mark.asyncio
async def test_list_secret_usage_reports_empty_and_missing_without_plaintext(
    mock_db_session,
):
    _usage_execute(mock_db_session, SecretStatus.ACTIVE, [])

    empty = await SecretsService.list_secret_usage(mock_db_session, "unused-secret")

    assert empty == {
        "secretRef": "db://unused-secret",
        "usages": [],
        "diagnostics": [],
    }

    mock_db_session.execute.reset_mock()
    missing_result = MagicMock()
    missing_result.scalar_one_or_none.return_value = None

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
async def test_list_secret_usage_restricts_query_to_caller_scope(mock_db_session):
    workspace_id = uuid4()
    user_id = uuid4()
    _usage_execute(mock_db_session, SecretStatus.ACTIVE, [])

    await SecretsService.list_secret_usage(
        mock_db_session,
        "github-pat-main",
        workspace_id=workspace_id,
        user_id=user_id,
    )

    usage_statement = mock_db_session.execute.call_args_list[1].args[0]
    compiled = usage_statement.compile(compile_kwargs={"literal_binds": True})
    statement = str(compiled)

    assert workspace_id.hex in statement
    assert user_id.hex in statement
    assert UUID("00000000-0000-0000-0000-000000000000").hex in statement

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
    # Two secret rows plus one redacted audit event per imported key.
    from api_service.db.models import SettingsAuditEvent

    audit_calls = [
        call.args[0]
        for call in mock_db_session.add.call_args_list
        if isinstance(call.args[0], SettingsAuditEvent)
    ]
    assert len(audit_calls) == 2
    assert all(event.event_type == "secrets.imported" for event in audit_calls)
    assert all(event.redacted is True for event in audit_calls)
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
    assert active_secret.credential_revision == 2
