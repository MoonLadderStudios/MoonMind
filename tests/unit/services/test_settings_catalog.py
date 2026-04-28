import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from uuid import UUID, uuid4

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
from api_service.services.settings_catalog import (
    SETTINGS_PERMISSION_NAMES,
    SettingAuditPolicy,
    SettingMigrationRule,
    SettingsCatalogService,
)


@pytest.fixture
def settings_session_maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/settings.db")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio_run = __import__("asyncio").run
    asyncio_run(_setup())
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    yield session_maker
    asyncio_run(engine.dispose())


def test_catalog_returns_exposed_descriptor_metadata_and_omits_unexposed_setting():
    service = SettingsCatalogService(env={})

    catalog = service.catalog(section="user-workspace", scope="workspace")

    workflow = catalog.categories["Workflow"]
    default_runtime = next(
        item for item in workflow if item.key == "workflow.default_task_runtime"
    )
    assert default_runtime.type == "enum"
    assert default_runtime.section == "user-workspace"
    assert default_runtime.category == "Workflow"
    assert default_runtime.scopes == ["workspace"]
    assert default_runtime.constraints is None
    assert default_runtime.source == "config_or_default"
    assert default_runtime.apply_mode == "next_task"
    assert default_runtime.activation_state == "active"
    assert default_runtime.active is True
    assert default_runtime.pending_value is None
    assert default_runtime.completion_guidance == (
        "New tasks will use this value when they are created."
    )
    assert default_runtime.requires_reload is False
    assert default_runtime.requires_worker_restart is False
    assert default_runtime.requires_process_restart is False
    assert default_runtime.depends_on == []
    assert default_runtime.audit.store_old_value is True
    assert default_runtime.options
    assert {option.value for option in default_runtime.options} >= {
        "codex",
        "codex_cli",
    }

    all_keys = {
        descriptor.key
        for descriptors in catalog.categories.values()
        for descriptor in descriptors
    }
    assert "workflow.github_token" not in all_keys


def test_catalog_rejects_descriptor_without_apply_mode():
    broken_entry = SettingsCatalogService(env={})._registry[0].__class__(
        key="broken.apply_mode",
        title="Broken Apply Mode",
        category="Broken",
        section="user-workspace",
        value_type="string",
        ui="text",
        scopes=("workspace",),
        order=999,
        apply_mode="",  # type: ignore[arg-type]
        applies_to=("task_creation",),
    )
    service = SettingsCatalogService(env={}, registry=(broken_entry,))

    with pytest.raises(ValueError, match="broken\\.apply_mode"):
        service.catalog(section="user-workspace", scope="workspace")


def test_activation_metadata_covers_supported_apply_modes():
    entry_type = SettingsCatalogService(env={})._registry[0].__class__
    registry = (
        entry_type(
            key="test.immediate",
            title="Immediate",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="ready",
            order=1,
            apply_mode="immediate",
        ),
        entry_type(
            key="test.next_request",
            title="Next Request",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="queued",
            order=2,
            apply_mode="next_request",
            applies_to=("catalog",),
        ),
        entry_type(
            key="test.next_task",
            title="Next Task",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="queued",
            order=3,
            apply_mode="next_task",
            applies_to=("task_creation",),
        ),
        entry_type(
            key="test.next_launch",
            title="Next Launch",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="queued",
            order=4,
            apply_mode="next_launch",
            applies_to=("runtime_launch",),
        ),
        entry_type(
            key="test.worker_reload",
            title="Worker Reload",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="queued",
            order=5,
            apply_mode="worker_reload",
            requires_reload=True,
            applies_to=("worker",),
        ),
        entry_type(
            key="test.process_restart",
            title="Process Restart",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="queued",
            order=6,
            apply_mode="process_restart",
            requires_process_restart=True,
            applies_to=("api_service",),
        ),
        entry_type(
            key="test.manual_operation",
            title="Manual Operation",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="queued",
            order=7,
            apply_mode="manual_operation",
            applies_to=("operations",),
        ),
    )
    service = SettingsCatalogService(env={}, registry=registry)

    descriptors = service.catalog(section="user-workspace", scope="workspace").categories[
        "Test"
    ]
    by_key = {descriptor.key: descriptor for descriptor in descriptors}

    assert by_key["test.immediate"].activation_state == "active"
    assert by_key["test.immediate"].pending_value is None
    assert by_key["test.next_request"].activation_state == "active"
    assert by_key["test.next_task"].completion_guidance == (
        "New tasks will use this value when they are created."
    )
    assert by_key["test.next_launch"].completion_guidance == (
        "New launches will use this value the next time they start."
    )
    assert by_key["test.worker_reload"].activation_state == "active"
    assert by_key["test.process_restart"].activation_state == "active"
    assert by_key["test.manual_operation"].activation_state == "active"


def test_catalog_exposes_provider_profile_reference_without_launch_semantics():
    service = SettingsCatalogService(env={})

    catalog = service.catalog(section="user-workspace", scope="workspace")

    workflow = catalog.categories["Workflow"]
    provider_profile = next(
        item for item in workflow if item.key == "workflow.default_provider_profile_ref"
    )
    assert provider_profile.type == "string"
    assert provider_profile.ui == "provider_profile_picker"
    assert provider_profile.applies_to == [
        "task_creation",
        "workflow_runtime",
        "provider_profiles",
    ]
    assert provider_profile.effective_value is None
    assert provider_profile.diagnostics[0].code == "inherited_null"


def test_effective_value_reports_environment_source_and_explanation():
    service = SettingsCatalogService(
        env={"WORKFLOW_DEFAULT_PUBLISH_MODE": "branch"}
    )

    effective = service.effective_value(
        "workflow.default_publish_mode", scope="workspace"
    )

    assert effective.value == "branch"
    assert effective.source == "environment"
    assert "WORKFLOW_DEFAULT_PUBLISH_MODE" in effective.source_explanation
    assert effective.value_version == 1
    assert effective.diagnostics == []


def test_environment_values_are_parsed_to_declared_types():
    service = SettingsCatalogService(
        env={
            "WORKFLOW_SKILLS_CANARY_PERCENT": "42",
            "MOONMIND_LIVE_SESSION_ENABLED_DEFAULT": "false",
        }
    )

    canary = service.effective_value("skills.canary_percent", scope="workspace")
    live_sessions = service.effective_value(
        "live_sessions.default_enabled", scope="workspace"
    )

    assert canary.value == 42
    assert isinstance(canary.value, int)
    assert live_sessions.value is False


def test_secret_ref_diagnostic_does_not_expose_secret_plaintext():
    service = SettingsCatalogService(
        env={"MOONMIND_GITHUB_TOKEN_REF": "env://MISSING_GITHUB_TOKEN"}
    )

    effective = service.effective_value(
        "integrations.github.token_ref", scope="workspace"
    )

    assert effective.value == "env://MISSING_GITHUB_TOKEN"
    assert effective.source == "environment"
    assert len(effective.diagnostics) == 1
    diagnostic = effective.diagnostics[0]
    assert diagnostic.code == "unresolved_secret_ref"
    assert diagnostic.severity == "error"
    assert "MISSING_GITHUB_TOKEN" not in diagnostic.message
    assert "plaintext" not in diagnostic.model_dump_json().lower()


def test_invalid_secret_ref_environment_value_is_redacted():
    service = SettingsCatalogService(
        env={"MOONMIND_GITHUB_TOKEN_REF": "raw-github-token"}
    )

    effective = service.effective_value(
        "integrations.github.token_ref", scope="workspace"
    )

    assert effective.value is None
    assert [diagnostic.code for diagnostic in effective.diagnostics] == [
        "inherited_null",
        "invalid_secret_ref",
    ]
    assert "raw-github-token" not in effective.model_dump_json()


def test_null_secret_ref_reports_inherited_null_diagnostic():
    service = SettingsCatalogService(env={})

    effective = service.effective_value(
        "integrations.github.token_ref", scope="workspace"
    )

    assert effective.value is None
    assert effective.diagnostics[0].code == "inherited_null"


@pytest.mark.asyncio
async def test_workspace_override_persists_and_reports_version(settings_session_maker):
    async with settings_session_maker() as settings_session:
        await _workspace_override_persists_and_reports_version(settings_session)


async def _workspace_override_persists_and_reports_version(settings_session):
    service = SettingsCatalogService(env={}, session=settings_session)

    inherited = await service.effective_value_async(
        "workflow.default_publish_mode", scope="workspace"
    )
    assert inherited.source == "config_or_default"
    assert inherited.value_version == 1

    response = await service.apply_overrides(
        scope="workspace",
        changes={"workflow.default_publish_mode": "branch"},
        expected_versions={"workflow.default_publish_mode": 1},
        reason="Use branch publishing for workspace tasks.",
    )

    effective = response.values["workflow.default_publish_mode"]
    assert effective.value == "branch"
    assert effective.source == "workspace_override"
    assert effective.value_version == 1
    assert effective.activation_state == "pending_next_boundary"
    assert effective.active is False
    assert effective.pending_value == "branch"

    updated = await service.apply_overrides(
        scope="workspace",
        changes={"workflow.default_publish_mode": "none"},
        expected_versions={"workflow.default_publish_mode": 1},
    )
    assert updated.values["workflow.default_publish_mode"].value == "none"
    assert updated.values["workflow.default_publish_mode"].value_version == 2
    assert (
        updated.values["workflow.default_publish_mode"].activation_state
        == "pending_next_boundary"
    )


@pytest.mark.asyncio
async def test_rename_migration_preserves_old_workspace_override(settings_session_maker):
    entry_type = SettingsCatalogService(env={})._registry[0].__class__
    registry = (
        entry_type(
            key="test.new_runtime",
            title="New Runtime",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="default-runtime",
            order=1,
            apply_mode="next_task",
            applies_to=("task_creation",),
        ),
    )
    rules = (
        SettingMigrationRule(
            old_key="test.old_runtime",
            new_key="test.new_runtime",
            state="renamed",
            message="test.old_runtime was renamed to test.new_runtime.",
        ),
    )
    async with settings_session_maker() as settings_session:
        settings_session.add(
            SettingsOverride(
                scope="workspace",
                workspace_id=UUID("00000000-0000-0000-0000-000000000000"),
                user_id=UUID("00000000-0000-0000-0000-000000000000"),
                key="test.old_runtime",
                value_json="preserved-runtime",
                value_version=3,
            )
        )
        await settings_session.commit()

        service = SettingsCatalogService(
            env={},
            registry=registry,
            migration_rules=rules,
            session=settings_session,
        )
        effective = await service.effective_value_async(
            "test.new_runtime", scope="workspace"
        )

    assert effective.value == "preserved-runtime"
    assert effective.source == "migrated_workspace_override"
    assert effective.value_version == 3
    assert [diagnostic.code for diagnostic in effective.diagnostics] == [
        "setting_renamed_override"
    ]
    assert effective.diagnostics[0].details == {
        "old_key": "test.old_runtime",
        "new_key": "test.new_runtime",
        "state": "renamed",
    }


def test_duplicate_rename_migration_targets_are_rejected():
    entry_type = SettingsCatalogService(env={})._registry[0].__class__
    registry = (
        entry_type(
            key="test.new_runtime",
            title="New Runtime",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="default-runtime",
            order=1,
            apply_mode="next_task",
            applies_to=("task_creation",),
        ),
    )
    rules = (
        SettingMigrationRule(
            old_key="test.old_runtime_a",
            new_key="test.new_runtime",
            state="renamed",
            message="test.old_runtime_a was renamed.",
        ),
        SettingMigrationRule(
            old_key="test.old_runtime_b",
            new_key="test.new_runtime",
            state="renamed",
            message="test.old_runtime_b was renamed.",
        ),
    )
    service = SettingsCatalogService(env={}, registry=registry, migration_rules=rules)

    with pytest.raises(ValueError, match="duplicate rename migration target"):
        service.catalog(scope="workspace")


@pytest.mark.asyncio
async def test_migration_diagnostic_is_cleared_after_direct_override_resolution(
    settings_session_maker,
):
    entry_type = SettingsCatalogService(env={})._registry[0].__class__
    registry = (
        entry_type(
            key="test.new_runtime",
            title="New Runtime",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="default-runtime",
            order=1,
            apply_mode="next_task",
            applies_to=("task_creation",),
        ),
    )
    rules = (
        SettingMigrationRule(
            old_key="test.old_runtime",
            new_key="test.new_runtime",
            state="renamed",
            message="test.old_runtime was renamed to test.new_runtime.",
        ),
    )
    async with settings_session_maker() as settings_session:
        settings_session.add(
            SettingsOverride(
                scope="workspace",
                workspace_id=UUID("00000000-0000-0000-0000-000000000000"),
                user_id=UUID("00000000-0000-0000-0000-000000000000"),
                key="test.old_runtime",
                value_json="migrated-runtime",
                value_version=1,
            )
        )
        await settings_session.commit()

        service = SettingsCatalogService(
            env={},
            registry=registry,
            migration_rules=rules,
            session=settings_session,
        )
        migrated = await service.effective_value_async(
            "test.new_runtime", scope="workspace"
        )
        assert [diagnostic.code for diagnostic in migrated.diagnostics] == [
            "setting_renamed_override"
        ]

        settings_session.add(
            SettingsOverride(
                scope="workspace",
                workspace_id=UUID("00000000-0000-0000-0000-000000000000"),
                user_id=UUID("00000000-0000-0000-0000-000000000000"),
                key="test.new_runtime",
                value_json="direct-runtime",
                value_version=2,
            )
        )
        await settings_session.commit()

        direct = await service.effective_value_async(
            "test.new_runtime", scope="workspace"
        )

    assert direct.value == "direct-runtime"
    assert direct.source == "workspace_override"
    assert direct.diagnostics == []


@pytest.mark.asyncio
async def test_deprecated_override_diagnostics_do_not_expose_raw_value(
    settings_session_maker,
):
    rules = (
        SettingMigrationRule(
            old_key="test.removed_token",
            state="removed",
            message="test.removed_token was removed and requires migration.",
        ),
    )
    async with settings_session_maker() as settings_session:
        settings_session.add(
            SettingsOverride(
                scope="workspace",
                workspace_id=UUID("00000000-0000-0000-0000-000000000000"),
                user_id=UUID("00000000-0000-0000-0000-000000000000"),
                key="test.removed_token",
                value_json="ghp_should_never_leak",
                value_version=2,
            )
        )
        await settings_session.commit()

        service = SettingsCatalogService(
            env={},
            registry=(),
            migration_rules=rules,
            session=settings_session,
        )
        diagnostics = await service.diagnostics(scope="workspace")

    deprecated = diagnostics.values["test.removed_token"]
    assert deprecated.source == "deprecated_override"
    assert deprecated.diagnostics[0].code == "setting_deprecated_override"
    assert deprecated.diagnostics[0].details == {
        "old_key": "test.removed_token",
        "state": "removed",
        "value_version": 2,
        "schema_version": 1,
    }
    assert "ghp_should_never_leak" not in deprecated.model_dump_json()


@pytest.mark.asyncio
async def test_key_scoped_diagnostics_exclude_unrelated_deprecated_overrides(
    settings_session_maker,
):
    entry_type = SettingsCatalogService(env={})._registry[0].__class__
    registry = (
        entry_type(
            key="test.active_runtime",
            title="Active Runtime",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="default-runtime",
            order=1,
            apply_mode="next_task",
            applies_to=("task_creation",),
        ),
    )
    rules = (
        SettingMigrationRule(
            old_key="test.removed_token",
            state="removed",
            message="test.removed_token was removed and requires migration.",
        ),
    )
    async with settings_session_maker() as settings_session:
        settings_session.add(
            SettingsOverride(
                scope="workspace",
                workspace_id=UUID("00000000-0000-0000-0000-000000000000"),
                user_id=UUID("00000000-0000-0000-0000-000000000000"),
                key="test.removed_token",
                value_json="ghp_should_never_leak",
                value_version=2,
            )
        )
        await settings_session.commit()

        service = SettingsCatalogService(
            env={},
            registry=registry,
            migration_rules=rules,
            session=settings_session,
        )
        diagnostics = await service.diagnostics(
            scope="workspace", key="test.active_runtime"
        )

    assert list(diagnostics.values) == ["test.active_runtime"]


@pytest.mark.asyncio
async def test_schema_version_mismatch_requires_explicit_type_migration(
    settings_session_maker,
):
    entry_type = SettingsCatalogService(env={})._registry[0].__class__
    registry = (
        entry_type(
            key="test.threshold",
            title="Threshold",
            category="Test",
            section="user-workspace",
            value_type="integer",
            ui="number",
            scopes=("workspace",),
            default_value=10,
            order=1,
            apply_mode="next_task",
            applies_to=("task_creation",),
        ),
    )
    rules = (
        SettingMigrationRule(
            old_key="test.threshold",
            state="type_changed",
            expected_schema_version=2,
            message="test.threshold type changed and needs migration.",
        ),
    )
    async with settings_session_maker() as settings_session:
        settings_session.add(
            SettingsOverride(
                scope="workspace",
                workspace_id=UUID("00000000-0000-0000-0000-000000000000"),
                user_id=UUID("00000000-0000-0000-0000-000000000000"),
                key="test.threshold",
                value_json="10",
                schema_version=1,
                value_version=4,
            )
        )
        await settings_session.commit()

        service = SettingsCatalogService(
            env={},
            registry=registry,
            migration_rules=rules,
            session=settings_session,
        )
        effective = await service.effective_value_async(
            "test.threshold", scope="workspace"
        )

    assert effective.value is None
    assert effective.source == "type_migration_required"
    assert effective.value_version == 4
    assert effective.diagnostics[0].code == "setting_type_migration_required"
    assert effective.diagnostics[0].details == {
        "key": "test.threshold",
        "state": "type_changed",
        "schema_version": 1,
        "expected_schema_version": 2,
    }


def test_catalog_invariant_gate_for_future_integrations():
    service = SettingsCatalogService(env={})
    catalog = service.catalog(scope="workspace")
    descriptors = [
        descriptor
        for values in catalog.categories.values()
        for descriptor in values
    ]

    assert descriptors
    for descriptor in descriptors:
        assert descriptor.key
        assert "workspace" in descriptor.scopes
        assert descriptor.source_explanation
        assert descriptor.apply_mode
        assert descriptor.audit is not None
        assert descriptor.ui != "raw_secret"
        assert descriptor.type != "password"
        assert descriptor.key not in {"workflow.github_token"}

    github_ref = next(
        item for item in descriptors if item.key == "integrations.github.token_ref"
    )
    assert github_ref.ui == "secret_ref_picker"
    assert github_ref.secret_role == "github_token"
    assert github_ref.audit.redact is True


@pytest.mark.asyncio
async def test_provider_profile_reference_reports_missing_and_disabled_diagnostics(
    settings_session_maker,
) -> None:
    async with settings_session_maker() as settings_session:
        settings_session.add(
            ManagedAgentProviderProfile(
                profile_id="disabled-profile",
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.SECRET_REF,
                runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                enabled=False,
            )
        )
        await settings_session.commit()

        service = SettingsCatalogService(env={}, session=settings_session)
        missing = await service.apply_overrides(
            scope="workspace",
            changes={"workflow.default_provider_profile_ref": "missing-profile"},
            expected_versions={"workflow.default_provider_profile_ref": 1},
        )
        missing_value = missing.values["workflow.default_provider_profile_ref"]
        assert missing_value.diagnostics[0].code == "provider_profile_not_found"
        assert missing_value.diagnostics[0].details["launch_blocker"] is True

        disabled = await service.apply_overrides(
            scope="workspace",
            changes={"workflow.default_provider_profile_ref": "disabled-profile"},
            expected_versions={"workflow.default_provider_profile_ref": 1},
        )
        disabled_value = disabled.values["workflow.default_provider_profile_ref"]
        assert disabled_value.diagnostics[0].code == "provider_profile_disabled"
        assert disabled_value.diagnostics[0].details["launch_blocker"] is True


@pytest.mark.asyncio
async def test_late_diagnostics_report_restored_reference_gaps_without_plaintext(
    settings_session_maker,
) -> None:
    async with settings_session_maker() as settings_session:
        settings_session.add(
            ManagedSecret(
                slug="restored-github-token",
                ciphertext="restored-secret-plaintext",
                status=SecretStatus.ACTIVE,
                details={},
            )
        )
        await settings_session.commit()

        service = SettingsCatalogService(env={}, session=settings_session)
        await service.apply_overrides(
            scope="workspace",
            changes={"integrations.github.token_ref": "db://restored-github-token"},
            expected_versions={"integrations.github.token_ref": 1},
            reason="restore settings reference",
        )
        healthy = await service.diagnostics(
            scope="workspace",
            key="integrations.github.token_ref",
        )
        healthy_github = healthy.values["integrations.github.token_ref"]
        assert healthy_github.diagnostics == []
        assert healthy_github.pending_value is None

        row = (
            await settings_session.execute(
                select(ManagedSecret).where(
                    ManagedSecret.slug == "restored-github-token"
                )
            )
        ).scalar_one()
        row.status = SecretStatus.DISABLED
        await settings_session.commit()

        late_service = SettingsCatalogService(env={}, session=settings_session)
        broken = await late_service.diagnostics(
            scope="workspace",
            key="integrations.github.token_ref",
        )

    github = broken.values["integrations.github.token_ref"]
    assert github.diagnostics[0].code == "unresolved_secret_ref"
    assert github.diagnostics[0].details == {
        "ref_scheme": "db",
        "status": "disabled",
        "launch_blocker": True,
    }
    assert github.pending_value is None
    assert "restored-secret-plaintext" not in github.model_dump_json()


@pytest.mark.asyncio
async def test_async_list_and_batch_write_avoid_per_key_override_queries(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(env={}, session=settings_session)
        await service.apply_overrides(
            scope="workspace",
            changes={
                "workflow.default_publish_mode": "branch",
                "skills.canary_percent": 25,
            },
            expected_versions={
                "workflow.default_publish_mode": 1,
                "skills.canary_percent": 1,
            },
        )

        async def fail_per_key_lookup(*_args, **_kwargs):
            raise AssertionError("per-key override lookup should not be used")

        service._get_override = fail_per_key_lookup  # type: ignore[method-assign]

        listed = await service.effective_values_async(scope="workspace")
        updated = await service.apply_overrides(
            scope="workspace",
            changes={
                "workflow.default_publish_mode": "none",
                "skills.canary_percent": 50,
            },
            expected_versions={
                "workflow.default_publish_mode": 1,
                "skills.canary_percent": 1,
            },
        )

    assert listed.values["workflow.default_publish_mode"].value == "branch"
    assert listed.values["skills.canary_percent"].value == 25
    assert updated.values["workflow.default_publish_mode"].value == "none"
    assert updated.values["skills.canary_percent"].value == 50


@pytest.mark.asyncio
async def test_user_override_wins_and_null_override_is_intentional(settings_session_maker):
    async with settings_session_maker() as settings_session:
        await _user_override_wins_and_null_override_is_intentional(settings_session)


async def _user_override_wins_and_null_override_is_intentional(settings_session):
    service = SettingsCatalogService(env={}, session=settings_session)

    await service.apply_overrides(
        scope="workspace",
        changes={"integrations.github.token_ref": "env://WORKSPACE_GITHUB_TOKEN"},
        expected_versions={"integrations.github.token_ref": 1},
    )
    inherited_by_user = await service.effective_value_async(
        "integrations.github.token_ref", scope="user"
    )
    assert inherited_by_user.value == "env://WORKSPACE_GITHUB_TOKEN"
    assert inherited_by_user.source == "workspace_override"

    await service.apply_overrides(
        scope="user",
        changes={"integrations.github.token_ref": "env://USER_GITHUB_TOKEN"},
        expected_versions={"integrations.github.token_ref": 1},
    )
    user_effective = await service.effective_value_async(
        "integrations.github.token_ref", scope="user"
    )
    assert user_effective.value == "env://USER_GITHUB_TOKEN"
    assert user_effective.source == "user_override"

    await service.apply_overrides(
        scope="user",
        changes={"integrations.github.token_ref": None},
        expected_versions={"integrations.github.token_ref": 1},
    )
    intentional_null = await service.effective_value_async(
        "integrations.github.token_ref", scope="user"
    )
    assert intentional_null.value is None
    assert intentional_null.source == "user_override"
    assert intentional_null.diagnostics == []


@pytest.mark.asyncio
async def test_version_conflict_is_atomic(settings_session_maker):
    async with settings_session_maker() as settings_session:
        await _version_conflict_is_atomic(settings_session)


async def _version_conflict_is_atomic(settings_session):
    service = SettingsCatalogService(env={}, session=settings_session)
    await service.apply_overrides(
        scope="workspace",
        changes={
            "workflow.default_publish_mode": "branch",
            "skills.canary_percent": 25,
        },
        expected_versions={
            "workflow.default_publish_mode": 1,
            "skills.canary_percent": 1,
        },
    )

    with pytest.raises(ValueError, match="version_conflict"):
        await service.apply_overrides(
            scope="workspace",
            changes={
                "workflow.default_publish_mode": "none",
                "skills.canary_percent": 50,
            },
            expected_versions={
                "workflow.default_publish_mode": 99,
                "skills.canary_percent": 1,
            },
        )

    publish_mode = await service.effective_value_async(
        "workflow.default_publish_mode", scope="workspace"
    )
    canary = await service.effective_value_async(
        "skills.canary_percent", scope="workspace"
    )
    assert publish_mode.value == "branch"
    assert canary.value == 25


@pytest.mark.asyncio
async def test_unsafe_values_rejected_but_secret_refs_are_allowed(settings_session_maker):
    async with settings_session_maker() as settings_session:
        await _unsafe_values_rejected_but_secret_refs_are_allowed(settings_session)


async def _unsafe_values_rejected_but_secret_refs_are_allowed(settings_session):
    service = SettingsCatalogService(env={}, session=settings_session)

    with pytest.raises(ValueError, match="invalid_setting_value"):
        await service.apply_overrides(
            scope="workspace",
            changes={"integrations.github.token_ref": "ghp_raw_plaintext"},
            expected_versions={"integrations.github.token_ref": 1},
        )

    with pytest.raises(ValueError, match="invalid_setting_value"):
        await service.apply_overrides(
            scope="workspace",
            changes={"workflow.default_publish_mode": {"workflow_payload": {}}},
            expected_versions={"workflow.default_publish_mode": 1},
        )

    response = await service.apply_overrides(
        scope="workspace",
        changes={"integrations.github.token_ref": "env://GITHUB_TOKEN"},
        expected_versions={"integrations.github.token_ref": 1},
    )
    assert response.values["integrations.github.token_ref"].value == "env://GITHUB_TOKEN"
    assert (
        response.values["integrations.github.token_ref"].pending_value
        == "env://GITHUB_TOKEN"
    )


@pytest.mark.asyncio
async def test_pending_activation_redacts_sensitive_non_reference_values(
    settings_session_maker,
):
    entry_type = SettingsCatalogService(env={})._registry[0].__class__
    registry = (
        entry_type(
            key="test.sensitive_runtime_value",
            title="Sensitive Runtime Value",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            order=1,
            apply_mode="process_restart",
            requires_process_restart=True,
            applies_to=("api_service",),
            sensitive=True,
            audit=SettingAuditPolicy(redact=True),
        ),
    )
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(
            env={},
            registry=registry,
            session=settings_session,
        )

        response = await service.apply_overrides(
            scope="workspace",
            changes={"test.sensitive_runtime_value": "sensitive-runtime-secret"},
            expected_versions={"test.sensitive_runtime_value": 1},
        )

    value = response.values["test.sensitive_runtime_value"]
    assert value.activation_state == "pending_restart"
    assert value.pending_value == "[redacted]"


@pytest.mark.asyncio
async def test_reset_deletes_only_override_and_preserves_secret_and_audit(settings_session_maker):
    async with settings_session_maker() as settings_session:
        await _reset_deletes_only_override_and_preserves_secret_and_audit(settings_session)


async def _reset_deletes_only_override_and_preserves_secret_and_audit(settings_session):
    service = SettingsCatalogService(env={}, session=settings_session)
    settings_session.add(
        ManagedSecret(
            slug="github-token",
            ciphertext="encrypted",
            details={"label": "GitHub"},
        )
    )
    await settings_session.commit()

    await service.apply_overrides(
        scope="workspace",
        changes={"workflow.default_publish_mode": "branch"},
        expected_versions={"workflow.default_publish_mode": 1},
        reason="temporary override",
    )

    reset = await service.reset_override(
        "workflow.default_publish_mode", scope="workspace", reason="inherit again"
    )

    assert reset.source == "config_or_default"
    assert reset.value != "branch"
    secret_count = len((await settings_session.execute(select(ManagedSecret))).scalars().all())
    audit_count = await service.audit_event_count()
    assert secret_count == 1
    assert audit_count >= 2


def test_settings_permission_taxonomy_includes_least_privilege_actions():
    assert {
        "settings.catalog.read",
        "settings.effective.read",
        "settings.user.write",
        "settings.workspace.write",
        "settings.system.read",
        "settings.system.write",
        "secrets.metadata.read",
        "secrets.value.write",
        "secrets.rotate",
        "secrets.disable",
        "secrets.delete",
        "provider_profiles.read",
        "provider_profiles.write",
        "operations.read",
        "operations.invoke",
        "settings.audit.read",
    }.issubset(SETTINGS_PERMISSION_NAMES)


@pytest.mark.asyncio
async def test_audit_entries_redact_secret_ref_without_metadata_permission(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(env={}, session=settings_session)
        await service.apply_overrides(
            scope="workspace",
            changes={"integrations.github.token_ref": "env://GITHUB_TOKEN"},
            expected_versions={"integrations.github.token_ref": 1},
            reason="configure integration",
        )

        entries = await service.list_audit_events(
            permissions={"settings.audit.read"},
        )

    assert len(entries) == 1
    entry = entries[0]
    assert entry.key == "integrations.github.token_ref"
    assert entry.redacted is True
    assert entry.old_value is None
    assert entry.new_value is None
    assert "descriptor_policy" in entry.redaction_reasons
    assert "env://GITHUB_TOKEN" not in entry.model_dump_json()


@pytest.mark.asyncio
async def test_audit_entries_are_scoped_to_workspace_and_subject(
    settings_session_maker,
):
    workspace_id = uuid4()
    other_workspace_id = uuid4()
    user_id = uuid4()
    other_user_id = uuid4()
    async with settings_session_maker() as settings_session:
        settings_session.add_all(
            [
                SettingsAuditEvent(
                    event_type="settings.override.updated",
                    key="workflow.default_publish_mode",
                    scope="workspace",
                    workspace_id=workspace_id,
                    user_id=UUID("00000000-0000-0000-0000-000000000000"),
                    new_value_json="branch",
                    redacted=False,
                ),
                SettingsAuditEvent(
                    event_type="settings.override.updated",
                    key="workflow.default_task_runtime",
                    scope="user",
                    workspace_id=workspace_id,
                    user_id=user_id,
                    new_value_json="codex",
                    redacted=False,
                ),
                SettingsAuditEvent(
                    event_type="settings.override.updated",
                    key="workflow.default_publish_mode",
                    scope="workspace",
                    workspace_id=other_workspace_id,
                    user_id=UUID("00000000-0000-0000-0000-000000000000"),
                    new_value_json="none",
                    redacted=False,
                ),
                SettingsAuditEvent(
                    event_type="settings.override.updated",
                    key="workflow.default_task_runtime",
                    scope="user",
                    workspace_id=workspace_id,
                    user_id=other_user_id,
                    new_value_json="codex_cli",
                    redacted=False,
                ),
            ]
        )
        await settings_session.commit()

        service = SettingsCatalogService(
            env={},
            session=settings_session,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        all_entries = await service.list_audit_events(
            permissions={"settings.audit.read"},
        )
        user_entries = await service.list_audit_events(
            permissions={"settings.audit.read"},
            scope="user",
        )

    assert {entry.new_value for entry in all_entries} == {"branch", "codex"}
    assert [entry.new_value for entry in user_entries] == ["codex"]


@pytest.mark.asyncio
async def test_audit_entries_expose_secret_ref_metadata_with_permission(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(env={}, session=settings_session)
        await service.apply_overrides(
            scope="workspace",
            changes={"integrations.github.token_ref": "env://GITHUB_TOKEN"},
            expected_versions={"integrations.github.token_ref": 1},
        )

        entries = await service.list_audit_events(
            permissions={"settings.audit.read", "secrets.metadata.read"},
        )

    assert entries[0].new_value == "env://GITHUB_TOKEN"
    assert entries[0].redacted is False


@pytest.mark.asyncio
async def test_audit_entries_persist_actor_identity(settings_session_maker):
    user_id = uuid4()
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(
            env={},
            session=settings_session,
            user_id=user_id,
        )
        await service.apply_overrides(
            scope="workspace",
            changes={"workflow.default_publish_mode": "branch"},
            expected_versions={"workflow.default_publish_mode": 1},
        )

        entries = await service.list_audit_events(
            permissions={"settings.audit.read"},
        )

    assert entries[0].actor_user_id == user_id


@pytest.mark.asyncio
async def test_audit_entries_expose_apply_mode_and_affected_systems(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(env={}, session=settings_session)
        await service.apply_overrides(
            scope="workspace",
            changes={"workflow.default_publish_mode": "branch"},
            expected_versions={"workflow.default_publish_mode": 1},
            reason="configure publish mode",
        )

        entries = await service.list_audit_events(
            permissions={"settings.audit.read"},
        )

    assert entries[0].event_type == "settings.override.updated"
    assert entries[0].key == "workflow.default_publish_mode"
    assert entries[0].scope == "workspace"
    assert entries[0].apply_mode == "next_task"
    assert entries[0].affected_systems == ["task_creation", "publishing"]
    assert entries[0].validation_outcome == "accepted"
    assert entries[0].created_at is not None


@pytest.mark.asyncio
async def test_audit_redactor_blocks_secret_like_values_even_without_descriptor(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        settings_session.add(
            SettingsAuditEvent(
                event_type="settings.override.updated",
                key="workflow.default_publish_mode",
                scope="workspace",
                old_value_json="branch",
                new_value_json="github_pat_raw_token",
                redacted=False,
            )
        )
        await settings_session.commit()

        service = SettingsCatalogService(env={}, session=settings_session)
        entries = await service.list_audit_events(
            permissions={"settings.audit.read"},
        )

    assert entries[0].old_value == "branch"
    assert entries[0].new_value is None
    assert entries[0].redacted is True
    assert "secret_like_value" in entries[0].redaction_reasons
    assert "github_pat_raw_token" not in entries[0].model_dump_json()


@pytest.mark.asyncio
async def test_audit_redactor_blocks_embedded_secret_prefixes(settings_session_maker):
    async with settings_session_maker() as settings_session:
        settings_session.add(
            SettingsAuditEvent(
                event_type="settings.override.updated",
                key="workflow.default_publish_mode",
                scope="workspace",
                old_value_json="branch",
                new_value_json="prefix-ghp_embedded_suffix",
                redacted=False,
            )
        )
        await settings_session.commit()

        service = SettingsCatalogService(env={}, session=settings_session)
        entries = await service.list_audit_events(
            permissions={"settings.audit.read"},
        )

    assert entries[0].new_value is None
    assert "secret_like_value" in entries[0].redaction_reasons
    assert "prefix-ghp_embedded_suffix" not in entries[0].model_dump_json()


@pytest.mark.asyncio
async def test_audit_redactor_reports_stored_redacted_null_values(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        settings_session.add(
            SettingsAuditEvent(
                event_type="settings.override.updated",
                key="workflow.default_publish_mode",
                scope="workspace",
                new_value_json=None,
                redacted=True,
            )
        )
        await settings_session.commit()

        service = SettingsCatalogService(env={}, session=settings_session)
        entries = await service.list_audit_events(
            permissions={"settings.audit.read"},
        )

    assert entries[0].redacted is True
    assert "stored_redacted" in entries[0].redaction_reasons


@pytest.mark.asyncio
async def test_settings_diagnostics_include_source_restart_and_recent_change(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(env={}, session=settings_session)
        await service.apply_overrides(
            scope="workspace",
            changes={"integrations.github.token_ref": "db://missing-token"},
            expected_versions={"integrations.github.token_ref": 1},
            reason="wire github token",
        )

        diagnostics = await service.diagnostics(scope="workspace")

    github = diagnostics.values["integrations.github.token_ref"]
    assert github.source == "workspace_override"
    assert github.recent_change is not None
    assert github.recent_change.reason == "wire github token"
    assert github.apply_mode == "next_launch"
    assert github.activation_state == "active"
    assert github.active is True
    assert github.completion_guidance == (
        "New launches will use this value the next time they start."
    )
    assert github.affected_process_or_worker == "github, integrations"
    assert github.diagnostics[0].code == "unresolved_secret_ref"
    assert github.diagnostics[0].details["launch_blocker"] is True
    assert "missing-token" not in github.model_dump_json()


@pytest.mark.asyncio
async def test_settings_diagnostics_recent_change_is_scoped_to_workspace_and_subject(
    settings_session_maker,
):
    workspace_id = uuid4()
    async with settings_session_maker() as settings_session:
        settings_session.add(
            SettingsAuditEvent(
                event_type="settings.override.updated",
                key="workflow.default_publish_mode",
                scope="workspace",
                workspace_id=uuid4(),
                new_value_json="branch",
                reason="other workspace",
                redacted=False,
            )
        )
        await settings_session.commit()

        service = SettingsCatalogService(
            env={},
            session=settings_session,
            workspace_id=workspace_id,
        )
        diagnostics = await service.diagnostics(
            scope="workspace",
            key="workflow.default_publish_mode",
        )

    setting = diagnostics.values["workflow.default_publish_mode"]
    assert setting.recent_change is None


def test_invalid_secret_ref_diagnostic_does_not_fallback_to_sensitive_source(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should_not_be_used")
    service = SettingsCatalogService(
        env={
            "MOONMIND_GITHUB_TOKEN_REF": "env://MISSING_TOKEN",
            "GITHUB_TOKEN": "ghp_should_not_be_used",
        }
    )

    effective = service.effective_value(
        "integrations.github.token_ref", scope="workspace"
    )

    assert effective.diagnostics[0].code == "unresolved_secret_ref"
    assert "ghp_should_not_be_used" not in effective.model_dump_json()
