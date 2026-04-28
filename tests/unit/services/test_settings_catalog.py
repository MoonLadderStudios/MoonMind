import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base, ManagedSecret
from api_service.services.settings_catalog import SettingsCatalogService


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

    updated = await service.apply_overrides(
        scope="workspace",
        changes={"workflow.default_publish_mode": "none"},
        expected_versions={"workflow.default_publish_mode": 1},
    )
    assert updated.values["workflow.default_publish_mode"].value == "none"
    assert updated.values["workflow.default_publish_mode"].value_version == 2


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
