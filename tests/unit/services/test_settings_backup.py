"""Unit tests for the settings backup/restore helpers.

Covers docs/Security/SettingsSystem.md §23 (Backup and Recovery):

* Exports of ``settings_overrides`` / ``settings_audit_events`` must exclude
  managed-secret plaintext while preserving SecretRef references, profile
  references, and audit history needed for forensic visibility.
* A partial-restore broken-reference scan surfaces overrides whose SecretRef
  or provider-profile target is missing or disabled so operators can repair
  the dependency before launches resume.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ManagedSecret,
    ProviderCredentialSource,
    RuntimeMaterializationMode,
    SecretStatus,
    SettingsAuditEvent,
    SettingsOverride,
)
from api_service.services.settings_backup import (
    SettingsBackupBundle,
    SettingsBackupViolation,
    SettingsBrokenReference,
    export_settings_backup,
    scan_broken_references,
)
from api_service.services.settings_catalog import (
    SettingAuditPolicy,
    SettingRegistryEntry,
    SettingsRegistry,
)


_DEFAULT_SUBJECT_ID = UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture
def settings_session_maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/settings_backup.db")

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    yield session_maker
    asyncio.run(engine.dispose())


def _backup_registry() -> SettingsRegistry:
    entries = (
        SettingRegistryEntry(
            key="workflow.default_task_runtime",
            title="Default Task Runtime",
            category="Workflow",
            section="user-workspace",
            value_type="enum",
            ui="select",
            scopes=("workspace",),
            default_value="codex",
            order=1,
        ),
        SettingRegistryEntry(
            key="workflow.default_provider_profile_ref",
            title="Default Provider Profile",
            category="Workflow",
            section="user-workspace",
            value_type="string",
            ui="select",
            scopes=("workspace",),
            default_value=None,
            order=2,
        ),
        SettingRegistryEntry(
            key="integrations.github.token_ref",
            title="GitHub Token",
            category="Integrations",
            section="providers-secrets",
            value_type="secret_ref",
            ui="secret_ref",
            scopes=("workspace",),
            default_value=None,
            sensitive=True,
            order=3,
            audit=SettingAuditPolicy(
                store_old_value=True, store_new_value=True, redact=True
            ),
        ),
        SettingRegistryEntry(
            key="integrations.smtp.password_inline",
            title="SMTP Password (Inline)",
            category="Integrations",
            section="providers-secrets",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value=None,
            sensitive=True,
            order=4,
            audit=SettingAuditPolicy(
                store_old_value=True, store_new_value=True, redact=True
            ),
        ),
    )
    return SettingsRegistry(entries=entries, stable_key_ledger=None)


def _seed_override(
    session,
    *,
    key: str,
    value: object,
    scope: str = "workspace",
    user_id: UUID = _DEFAULT_SUBJECT_ID,
    workspace_id: UUID = _DEFAULT_SUBJECT_ID,
    schema_version: int = 1,
    value_version: int = 1,
) -> SettingsOverride:
    row = SettingsOverride(
        scope=scope,
        workspace_id=workspace_id,
        user_id=user_id,
        key=key,
        value_json=value,
        schema_version=schema_version,
        value_version=value_version,
    )
    session.add(row)
    return row


def _seed_audit(
    session,
    *,
    key: str,
    event_type: str = "settings.override.updated",
    redacted: bool = False,
    old_value=None,
    new_value=None,
    scope: str = "workspace",
) -> SettingsAuditEvent:
    event = SettingsAuditEvent(
        event_type=event_type,
        key=key,
        scope=scope,
        workspace_id=_DEFAULT_SUBJECT_ID,
        user_id=_DEFAULT_SUBJECT_ID,
        actor_user_id=None,
        old_value_json=old_value,
        new_value_json=new_value,
        redacted=redacted,
    )
    session.add(event)
    return event


def _seed_provider_profile(
    session,
    *,
    profile_id: str,
    enabled: bool = True,
) -> ManagedAgentProviderProfile:
    row = ManagedAgentProviderProfile(
        profile_id=profile_id,
        runtime_id="codex",
        provider_id="codex",
        enabled=enabled,
        credential_source=ProviderCredentialSource.NONE,
        runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
    )
    session.add(row)
    return row


def _seed_managed_secret(
    session,
    *,
    slug: str,
    status: SecretStatus = SecretStatus.ACTIVE,
) -> ManagedSecret:
    row = ManagedSecret(
        slug=slug,
        ciphertext="dummy-ciphertext",
        status=status,
    )
    session.add(row)
    return row


@pytest.mark.asyncio
async def test_export_includes_nonsensitive_overrides_with_metadata(
    settings_session_maker,
):
    registry = _backup_registry()

    async with settings_session_maker() as session:
        _seed_override(
            session,
            key="workflow.default_task_runtime",
            value="codex_cli",
            schema_version=2,
            value_version=4,
        )
        await session.commit()

        bundle = await export_settings_backup(session=session, registry=registry)

    assert isinstance(bundle, SettingsBackupBundle)
    assert [record.key for record in bundle.overrides] == [
        "workflow.default_task_runtime",
    ]
    record = bundle.overrides[0]
    assert record.value_json == "codex_cli"
    assert record.schema_version == 2
    assert record.value_version == 4
    assert record.scope == "workspace"
    assert bundle.excluded_keys == []


@pytest.mark.asyncio
async def test_export_preserves_secret_ref_values_as_references(
    settings_session_maker,
):
    registry = _backup_registry()

    async with settings_session_maker() as session:
        _seed_override(
            session,
            key="integrations.github.token_ref",
            value="db://github-token",
        )
        await session.commit()

        bundle = await export_settings_backup(session=session, registry=registry)

    assert [record.key for record in bundle.overrides] == [
        "integrations.github.token_ref",
    ]
    record = bundle.overrides[0]
    # SecretRef strings are references, not plaintext, so they remain in
    # the bundle. Audit-level redaction only applies to audit-event payloads.
    assert record.value_json == "db://github-token"
    assert record.is_secret_ref is True
    assert bundle.excluded_keys == []


@pytest.mark.asyncio
async def test_export_excludes_sensitive_non_secret_ref_descriptors(
    settings_session_maker,
):
    """Sensitive but inline-typed descriptors are dropped from backups.

    A descriptor whose ``audit.redact`` is true but whose ``value_type`` is
    NOT ``secret_ref`` could carry inline credentials. The backup MUST drop
    that value so plaintext never reaches a restore artifact.
    """

    registry = _backup_registry()

    async with settings_session_maker() as session:
        _seed_override(
            session,
            key="integrations.smtp.password_inline",
            value="should-never-be-backed-up",
        )
        # A non-sensitive row is preserved alongside the dropped row.
        _seed_override(
            session,
            key="workflow.default_task_runtime",
            value="codex_cli",
        )
        await session.commit()

        bundle = await export_settings_backup(session=session, registry=registry)

    assert sorted(record.key for record in bundle.overrides) == [
        "workflow.default_task_runtime",
    ]
    assert sorted(bundle.excluded_keys) == ["integrations.smtp.password_inline"]


@pytest.mark.asyncio
async def test_export_blocks_when_secret_ref_descriptor_carries_plaintext(
    settings_session_maker,
):
    """Defense in depth: refuse to back up obvious plaintext secrets.

    A SecretRef descriptor whose persisted value does not look like a
    SecretRef (``env://`` / ``db://``) is a corruption indicator. The
    backup helper raises rather than emitting a bundle that might leak the
    plaintext into restore artifacts.
    """

    registry = _backup_registry()

    async with settings_session_maker() as session:
        _seed_override(
            session,
            key="integrations.github.token_ref",
            value="ghp_pretend_token_value_with_underscores",
        )
        await session.commit()

        with pytest.raises(SettingsBackupViolation):
            await export_settings_backup(session=session, registry=registry)


@pytest.mark.asyncio
async def test_export_includes_migration_audit_events_and_redacts_payloads(
    settings_session_maker,
):
    registry = _backup_registry()

    async with settings_session_maker() as session:
        _seed_audit(
            session,
            key="workflow.default_task_runtime",
            event_type="settings.migration.renamed",
            redacted=False,
            old_value="codex",
            new_value="codex",
        )
        _seed_audit(
            session,
            key="integrations.github.token_ref",
            event_type="settings.override.updated",
            redacted=True,
            old_value=None,
            new_value=None,
        )
        await session.commit()

        bundle = await export_settings_backup(session=session, registry=registry)

    audit_keys = sorted(record.event_type for record in bundle.audit_events)
    assert audit_keys == [
        "settings.migration.renamed",
        "settings.override.updated",
    ]
    redacted_record = next(
        record
        for record in bundle.audit_events
        if record.key == "integrations.github.token_ref"
    )
    assert redacted_record.redacted is True
    assert redacted_record.old_value_json is None
    assert redacted_record.new_value_json is None


@pytest.mark.asyncio
async def test_export_blocks_when_redacted_audit_row_still_carries_payload(
    settings_session_maker,
):
    """A row marked ``redacted=True`` MUST NOT carry plaintext values.

    If a corrupted row ever lands in the audit table with both
    ``redacted=True`` and a non-null value, the backup helper refuses to
    serialize it; otherwise the operator would silently restore plaintext.
    """

    registry = _backup_registry()

    async with settings_session_maker() as session:
        _seed_audit(
            session,
            key="integrations.github.token_ref",
            event_type="settings.override.updated",
            redacted=True,
            old_value="leaked-plaintext",
            new_value=None,
        )
        await session.commit()

        with pytest.raises(SettingsBackupViolation):
            await export_settings_backup(session=session, registry=registry)


@pytest.mark.asyncio
async def test_scan_flags_unresolved_db_secret_ref(settings_session_maker):
    registry = _backup_registry()

    async with settings_session_maker() as session:
        _seed_override(
            session,
            key="integrations.github.token_ref",
            value="db://missing-token",
        )
        await session.commit()

        broken = await scan_broken_references(session=session, registry=registry)

    assert [item.key for item in broken] == ["integrations.github.token_ref"]
    assert broken[0].code == "unresolved_secret_ref"
    assert broken[0].severity == "error"
    assert broken[0].details["ref_scheme"] == "db"


@pytest.mark.asyncio
async def test_scan_flags_disabled_managed_secret(settings_session_maker):
    registry = _backup_registry()

    async with settings_session_maker() as session:
        _seed_managed_secret(
            session, slug="github-token", status=SecretStatus.DISABLED
        )
        _seed_override(
            session,
            key="integrations.github.token_ref",
            value="db://github-token",
        )
        await session.commit()

        broken = await scan_broken_references(session=session, registry=registry)

    assert len(broken) == 1
    assert broken[0].code == "unresolved_secret_ref"
    assert broken[0].details["status"] == "disabled"


@pytest.mark.asyncio
async def test_scan_flags_unresolved_env_secret_ref(settings_session_maker):
    registry = _backup_registry()

    async with settings_session_maker() as session:
        _seed_override(
            session,
            key="integrations.github.token_ref",
            value="env://NOT_AVAILABLE",
        )
        await session.commit()

        broken = await scan_broken_references(
            session=session,
            registry=registry,
            env={},
        )

    assert len(broken) == 1
    assert broken[0].code == "unresolved_secret_ref"
    assert broken[0].details["ref_scheme"] == "env"


@pytest.mark.asyncio
async def test_scan_flags_missing_provider_profile(settings_session_maker):
    registry = _backup_registry()

    async with settings_session_maker() as session:
        _seed_override(
            session,
            key="workflow.default_provider_profile_ref",
            value="missing-profile",
        )
        await session.commit()

        broken = await scan_broken_references(session=session, registry=registry)

    assert len(broken) == 1
    item = broken[0]
    assert item.code == "provider_profile_not_found"
    assert item.details["profile_id"] == "missing-profile"


@pytest.mark.asyncio
async def test_scan_returns_empty_when_all_references_resolve(
    settings_session_maker,
):
    registry = _backup_registry()

    async with settings_session_maker() as session:
        _seed_managed_secret(session, slug="github-token")
        _seed_provider_profile(session, profile_id="default-codex")
        _seed_override(
            session,
            key="integrations.github.token_ref",
            value="db://github-token",
        )
        _seed_override(
            session,
            key="workflow.default_provider_profile_ref",
            value="default-codex",
        )
        _seed_override(
            session,
            key="integrations.github.token_ref",
            value="env://AVAILABLE_TOKEN",
            scope="user",
            user_id=uuid4(),
        )
        await session.commit()

        broken = await scan_broken_references(
            session=session,
            registry=registry,
            env={"AVAILABLE_TOKEN": "value"},
        )

    assert broken == []


@pytest.mark.asyncio
async def test_scan_returns_broken_references_as_pydantic_models(
    settings_session_maker,
):
    registry = _backup_registry()

    async with settings_session_maker() as session:
        _seed_override(
            session,
            key="integrations.github.token_ref",
            value="db://missing-token",
        )
        await session.commit()

        broken = await scan_broken_references(session=session, registry=registry)

    assert all(isinstance(item, SettingsBrokenReference) for item in broken)
    dump = broken[0].model_dump()
    # Stable shape for operator dashboards: must include the scope so the
    # diagnostic identifies the affected override row uniquely.
    assert set(dump) >= {"key", "scope", "code", "severity", "details", "message"}
