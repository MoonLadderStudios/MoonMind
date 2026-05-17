"""Integration tests for settings backup, restore, and migration cutover.

Exercises the FRs in docs/Security/SettingsSystem.md §23 (Backup and
Recovery) and §24 (Migration and Deprecation) end-to-end against the real
registry, the migration orchestrator, and the backup/broken-reference
helpers — without requiring external services.

Tests cover the three acceptance criteria that previously had no explicit
end-to-end coverage:

1. The rename workflow preserves effective values across the migration
   cutover when applied through the real registry catalog service.
2. The backup helper excludes managed-secret plaintext and preserves
   SecretRef references plus migration audit events.
3. The broken-reference scan surfaces SecretRef and provider-profile
   targets that go missing after a partial restore.
"""

from __future__ import annotations

from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select
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
    SettingsBackupViolation,
    export_settings_backup,
    scan_broken_references,
)
from api_service.services.settings_catalog import (
    SettingMigrationRule,
    SettingsCatalogService,
)
from api_service.services.settings_migrations import (
    SettingsMigrationOrchestrator,
    TypeChangeCoercion,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
]


_DEFAULT_SUBJECT_ID = UUID("00000000-0000-0000-0000-000000000000")


@pytest_asyncio.fixture
async def settings_backup_session(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/settings-backup-it.db"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session
    await engine.dispose()


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


def _seed_managed_secret(
    session,
    *,
    slug: str,
    status: SecretStatus = SecretStatus.ACTIVE,
) -> ManagedSecret:
    row = ManagedSecret(
        slug=slug,
        ciphertext="ciphertext-placeholder",
        status=status,
    )
    session.add(row)
    return row


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


async def test_rename_cutover_preserves_effective_value_through_real_registry(
    settings_backup_session,
):
    """The rename orchestrator preserves effective values when wired through
    :class:`SettingsCatalogService` using a synthetic descriptor + rule pair.

    This is an end-to-end rehearsal of the §24.1 cutover: read the value
    via the catalog at the new key, run the orchestrator, re-read, and
    confirm the effective value is unchanged while the renamed-override
    diagnostic clears.
    """

    from api_service.services.settings_catalog import (
        SettingAuditPolicy,
        SettingRegistryEntry,
    )

    registry = (
        SettingRegistryEntry(
            key="contract.legacy_default_runtime",
            title="Legacy Default Runtime",
            category="Workflow",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value=None,
            order=1,
        ),
        SettingRegistryEntry(
            key="contract.default_runtime",
            title="Default Runtime",
            category="Workflow",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value=None,
            order=2,
            audit=SettingAuditPolicy(
                store_old_value=True,
                store_new_value=True,
                redact=False,
            ),
        ),
    )
    rule = SettingMigrationRule(
        old_key="contract.legacy_default_runtime",
        new_key="contract.default_runtime",
        state="renamed",
        message="contract.legacy_default_runtime was renamed to contract.default_runtime.",
    )

    _seed_override(
        settings_backup_session,
        key="contract.legacy_default_runtime",
        value="codex_cli",
    )
    await settings_backup_session.commit()

    catalog_before = SettingsCatalogService(
        env={},
        registry=registry,
        migration_rules=(rule,),
        session=settings_backup_session,
    )
    before = await catalog_before.effective_value_async(
        "contract.default_runtime", scope="workspace"
    )
    assert before.value == "codex_cli"
    assert any(
        diag.code == "setting_renamed_override" for diag in before.diagnostics
    )

    orchestrator = SettingsMigrationOrchestrator(
        session=settings_backup_session,
        migration_rules=(rule,),
    )
    outcomes = await orchestrator.apply_rename(rule)
    await settings_backup_session.commit()

    assert len(outcomes) == 1
    assert outcomes[0].applied is True

    catalog_after = SettingsCatalogService(
        env={},
        registry=registry,
        migration_rules=(rule,),
        session=settings_backup_session,
    )
    after = await catalog_after.effective_value_async(
        "contract.default_runtime", scope="workspace"
    )
    assert after.value == "codex_cli"
    assert all(
        diag.code != "setting_renamed_override" for diag in after.diagnostics
    )

    audit_events = (
        await settings_backup_session.execute(
            select(SettingsAuditEvent).where(
                SettingsAuditEvent.event_type == "settings.migration.renamed"
            )
        )
    ).scalars().all()
    assert len(audit_events) == 1


async def test_backup_export_against_real_registry_redacts_audit_payloads(
    settings_backup_session,
):
    """End-to-end backup snapshot against the production catalog descriptors.

    Uses the default registry so the descriptor for
    ``integrations.github.token_ref`` (a real SecretRef-typed descriptor)
    is the one applied to backup-redaction policy. The bundle MUST include
    the SecretRef value as a reference, include the migration audit event,
    and surface no plaintext payload.
    """

    _seed_managed_secret(settings_backup_session, slug="github-token")
    _seed_override(
        settings_backup_session,
        key="integrations.github.token_ref",
        value="db://github-token",
    )
    _seed_override(
        settings_backup_session,
        key="workflow.default_task_runtime",
        value="codex_cli",
    )

    # An audit event flagged as redacted MUST NOT carry payload values.
    settings_backup_session.add(
        SettingsAuditEvent(
            event_type="settings.override.updated",
            key="integrations.github.token_ref",
            scope="workspace",
            workspace_id=_DEFAULT_SUBJECT_ID,
            user_id=_DEFAULT_SUBJECT_ID,
            redacted=True,
            old_value_json=None,
            new_value_json=None,
        )
    )
    settings_backup_session.add(
        SettingsAuditEvent(
            event_type="settings.migration.renamed",
            key="workflow.legacy_default_runtime",
            scope="workspace",
            workspace_id=_DEFAULT_SUBJECT_ID,
            user_id=_DEFAULT_SUBJECT_ID,
            redacted=False,
            old_value_json="codex",
            new_value_json="codex",
        )
    )
    await settings_backup_session.commit()

    from api_service.services.settings_catalog import _REGISTRY  # type: ignore

    bundle = await export_settings_backup(
        session=settings_backup_session, registry=_REGISTRY
    )

    keys = sorted(record.key for record in bundle.overrides)
    assert "integrations.github.token_ref" in keys
    assert "workflow.default_task_runtime" in keys

    token_record = next(
        record
        for record in bundle.overrides
        if record.key == "integrations.github.token_ref"
    )
    assert token_record.value_json == "db://github-token"
    assert token_record.is_secret_ref is True

    audit_event_types = sorted(record.event_type for record in bundle.audit_events)
    assert "settings.migration.renamed" in audit_event_types
    assert "settings.override.updated" in audit_event_types

    redacted_event = next(
        record
        for record in bundle.audit_events
        if record.key == "integrations.github.token_ref"
    )
    assert redacted_event.redacted is True
    assert redacted_event.old_value_json is None
    assert redacted_event.new_value_json is None


async def test_partial_restore_broken_references_surface_for_operator(
    settings_backup_session,
):
    """After restoring overrides without their dependencies, the scan flags
    every persisted override whose SecretRef or provider-profile target is
    missing or in a non-launchable state.
    """

    _seed_override(
        settings_backup_session,
        key="integrations.github.token_ref",
        value="db://github-token",
    )
    _seed_override(
        settings_backup_session,
        key="workflow.default_provider_profile_ref",
        value="codex-default",
    )
    _seed_override(
        settings_backup_session,
        key="workflow.default_task_runtime",
        value="codex_cli",
    )
    await settings_backup_session.commit()

    from api_service.services.settings_catalog import _REGISTRY  # type: ignore

    broken = await scan_broken_references(
        session=settings_backup_session, registry=_REGISTRY, env={}
    )

    codes = {(item.key, item.code) for item in broken}
    assert ("integrations.github.token_ref", "unresolved_secret_ref") in codes
    assert ("workflow.default_provider_profile_ref", "provider_profile_not_found") in codes
    # The unrelated, non-reference override does NOT appear in the scan.
    assert all(item.key != "workflow.default_task_runtime" for item in broken)


async def test_partial_restore_clears_diagnostics_once_dependencies_return(
    settings_backup_session,
):
    """Restoring the missing managed secret + provider profile clears the
    broken-reference findings without operator intervention.
    """

    _seed_override(
        settings_backup_session,
        key="integrations.github.token_ref",
        value="db://github-token",
    )
    _seed_override(
        settings_backup_session,
        key="workflow.default_provider_profile_ref",
        value="codex-default",
    )
    await settings_backup_session.commit()

    from api_service.services.settings_catalog import _REGISTRY  # type: ignore

    broken = await scan_broken_references(
        session=settings_backup_session, registry=_REGISTRY, env={}
    )
    assert len(broken) == 2

    _seed_managed_secret(settings_backup_session, slug="github-token")
    _seed_provider_profile(settings_backup_session, profile_id="codex-default")
    await settings_backup_session.commit()

    broken_after_restore = await scan_broken_references(
        session=settings_backup_session, registry=_REGISTRY, env={}
    )
    assert broken_after_restore == []


async def test_type_change_cutover_records_audit_and_survives_backup(
    settings_backup_session,
):
    """A type-change migration produces an audit event that is preserved by
    the backup snapshot and does not corrupt the override value.
    """

    from api_service.services.settings_catalog import (
        SettingAuditPolicy,
        SettingRegistryEntry,
    )

    registry = (
        SettingRegistryEntry(
            key="contract.retry_limit",
            title="Retry Limit",
            category="Workflow",
            section="user-workspace",
            value_type="integer",
            ui="number",
            scopes=("workspace",),
            default_value=0,
            order=1,
            audit=SettingAuditPolicy(
                store_old_value=True,
                store_new_value=True,
                redact=False,
            ),
        ),
    )
    rule = SettingMigrationRule(
        old_key="contract.retry_limit",
        state="type_changed",
        message="contract.retry_limit type changed from string to integer.",
        expected_schema_version=2,
    )

    _seed_override(
        settings_backup_session,
        key="contract.retry_limit",
        value="3",
        schema_version=1,
    )
    await settings_backup_session.commit()

    orchestrator = SettingsMigrationOrchestrator(
        session=settings_backup_session,
        migration_rules=(rule,),
    )
    outcomes = await orchestrator.apply_type_change(
        rule,
        TypeChangeCoercion(coerce=int, target_schema_version=2),
    )
    await settings_backup_session.commit()
    assert outcomes[0].new_value == 3

    bundle = await export_settings_backup(
        session=settings_backup_session, registry=registry
    )
    retry_record = next(
        record for record in bundle.overrides if record.key == "contract.retry_limit"
    )
    assert retry_record.value_json == 3
    assert retry_record.schema_version == 2

    migration_events = [
        record
        for record in bundle.audit_events
        if record.event_type == "settings.migration.type_changed"
    ]
    assert len(migration_events) == 1


async def test_backup_refuses_to_serialize_plaintext_in_secret_ref_slot(
    settings_backup_session,
):
    """A SecretRef-typed override storing a value that is not a SecretRef is
    a data-corruption indicator. The backup helper raises rather than
    emitting a bundle that could leak the plaintext into restore artifacts.
    """

    _seed_override(
        settings_backup_session,
        key="integrations.github.token_ref",
        value="ghp_definitely_not_a_secret_ref",
    )
    await settings_backup_session.commit()

    from api_service.services.settings_catalog import _REGISTRY  # type: ignore

    with pytest.raises(SettingsBackupViolation):
        await export_settings_backup(
            session=settings_backup_session, registry=_REGISTRY
        )
