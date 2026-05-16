from types import SimpleNamespace

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
    SettingDependency,
    SettingMigrationRule,
    SettingConstraints,
    SettingValidationIssue,
    SettingsCatalogBuilder,
    SettingsCatalogService,
    SettingsValidationError,
    SettingsWorkspacePolicy,
    SettingsRegistry,
    SettingRegistryEntry,
    _CATALOG_KEY_LEDGER,
    _REGISTRY,
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
    assert default_runtime.source == "config_file"
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


# ---------------------------------------------------------------------------
# MM-656: server-side validation and cross-setting policy enforcement
# ---------------------------------------------------------------------------


def _mm656_registry() -> tuple[SettingRegistryEntry, ...]:
    return (
        SettingRegistryEntry(
            key="test.boolean",
            title="Boolean",
            category="MM-656",
            section="user-workspace",
            value_type="boolean",
            ui="toggle",
            scopes=("workspace",),
            default_value=False,
            order=1,
        ),
        SettingRegistryEntry(
            key="test.string",
            title="String",
            category="MM-656",
            section="user-workspace",
            value_type="string",
            ui="input",
            scopes=("workspace",),
            default_value="safe",
            constraints=SettingConstraints(min_length=2, max_length=8, pattern=r"^[a-z]+$"),
            order=2,
        ),
        SettingRegistryEntry(
            key="test.integer",
            title="Integer",
            category="MM-656",
            section="user-workspace",
            value_type="integer",
            ui="number",
            scopes=("workspace",),
            default_value=1,
            constraints=SettingConstraints(minimum=0, maximum=10),
            order=3,
        ),
        SettingRegistryEntry(
            key="test.number",
            title="Number",
            category="MM-656",
            section="user-workspace",
            value_type="number",
            ui="number",
            scopes=("workspace",),
            default_value=1.5,
            constraints=SettingConstraints(minimum=0, maximum=10),
            order=4,
        ),
        SettingRegistryEntry(
            key="test.enum",
            title="Enum",
            category="MM-656",
            section="user-workspace",
            value_type="enum",
            ui="select",
            scopes=("workspace",),
            default_value="one",
            options=(("one", "One"), ("two", "Two")),
            order=5,
        ),
        SettingRegistryEntry(
            key="test.list",
            title="List",
            category="MM-656",
            section="user-workspace",
            value_type="list",
            ui="multi",
            scopes=("workspace",),
            default_value=[],
            constraints=SettingConstraints(min_items=1, max_items=2),
            order=6,
        ),
        SettingRegistryEntry(
            key="test.object",
            title="Object",
            category="MM-656",
            section="user-workspace",
            value_type="object",
            ui="json",
            scopes=("workspace",),
            default_value={"mode": "safe"},
            constraints=SettingConstraints(
                required_keys=["mode"],
                allowed_keys=["mode", "enabled"],
            ),
            order=7,
        ),
        SettingRegistryEntry(
            key="test.secret_ref",
            title="SecretRef",
            category="MM-656",
            section="user-workspace",
            value_type="secret_ref",
            ui="secret_ref_picker",
            scopes=("workspace",),
            default_value=None,
            order=8,
        ),
        SettingRegistryEntry(
            key="test.operation_mode",
            title="Operation Mode",
            category="MM-656",
            section="operations",
            value_type="enum",
            ui="select",
            scopes=("workspace",),
            default_value="idle",
            options=(("idle", "Idle"), ("repair", "Repair")),
            apply_mode="manual_operation",
            applies_to=("operations",),
            order=9,
        ),
    )


def _assert_validation_issue(
    exc: pytest.ExceptionInfo[SettingsValidationError],
    *,
    key: str,
    code: str,
    boundary: str = "write_request",
    blocks: str = "persistence",
) -> None:
    issue = exc.value.issues[0]
    assert issue.key == key
    assert issue.code == code
    assert issue.boundary == boundary
    assert blocks in issue.blocks
    assert issue.message
    dumped = issue.model_dump_json()
    assert ("gh" + "p_raw_plaintext") not in dumped
    assert "active-secret-plaintext" not in dumped


def _assert_preview_issue(
    response,
    *,
    key: str,
    code: str,
) -> None:
    assert response.accepted is False
    assert response.issues_by_key[key][0].code == code
    dumped = response.model_dump_json()
    assert ("gh" + "p_raw_plaintext") not in dumped
    assert "active-secret-plaintext" not in dumped


def _assert_redacted_diff(response, *, key: str) -> None:
    diff = next(item for item in response.diffs if item.key == key)
    assert diff.redacted is True
    assert diff.after.value is None
    dumped = diff.model_dump_json()
    assert "missing-token" not in dumped


def _assert_reload_requirement(response, *, key: str) -> None:
    requirement = next(item for item in response.reload_requirements if item.key == key)
    assert requirement.requires_reload is True
    assert requirement.apply_mode == "worker_reload"
    assert requirement.applies_to


@pytest.mark.asyncio
async def test_mm656_value_type_matrix_accepts_and_rejects_supported_categories(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        settings_session.add(
            ManagedSecret(
                slug="active-token",
                ciphertext="active-secret-plaintext",
                status=SecretStatus.ACTIVE,
                details={},
            )
        )
        await settings_session.commit()
        service = SettingsCatalogService(
            env={"ENV_TOKEN": "redacted"},
            registry=_mm656_registry(),
            session=settings_session,
        )

        accepted = await service.apply_overrides(
            scope="workspace",
            changes={
                "test.boolean": True,
                "test.string": "valid",
                "test.integer": 7,
                "test.number": 2.5,
                "test.enum": "two",
                "test.list": ["one"],
                "test.object": {"mode": "safe", "enabled": True},
                "test.secret_ref": "db://active-token",
            },
            expected_versions={
                "test.boolean": 1,
                "test.string": 1,
                "test.integer": 1,
                "test.number": 1,
                "test.enum": 1,
                "test.list": 1,
                "test.object": 1,
                "test.secret_ref": 1,
            },
        )

        assert accepted.values["test.boolean"].value is True
        assert accepted.values["test.number"].value == 2.5
        assert accepted.values["test.list"].value == ["one"]
        assert accepted.values["test.object"].value["mode"] == "safe"
        assert accepted.values["test.secret_ref"].diagnostics == []

        invalid_cases = [
            ("test.boolean", "yes", "type_mismatch"),
            ("test.string", "INVALID", "string_constraint_failed"),
            ("test.integer", True, "type_mismatch"),
            ("test.number", "2.5", "type_mismatch"),
            ("test.enum", "three", "enum_value_invalid"),
            ("test.list", [], "list_constraint_failed"),
            ("test.object", {"enabled": True}, "object_constraint_failed"),
            ("test.secret_ref", "gh" + "p_raw_plaintext", "unsafe_setting_payload"),
        ]
        for key, value, code in invalid_cases:
            with pytest.raises(SettingsValidationError) as exc:
                await service.apply_overrides(
                    scope="workspace",
                    changes={key: value},
                    expected_versions={key: accepted.values.get(key, SimpleNamespace(value_version=1)).value_version},
                )
            _assert_validation_issue(exc, key=key, code=code)


@pytest.mark.asyncio
async def test_mm656_references_policy_boundaries_and_atomicity(settings_session_maker):
    async with settings_session_maker() as settings_session:
        settings_session.add(
            ManagedSecret(
                slug="disabled-token",
                ciphertext="disabled-secret-plaintext",
                status=SecretStatus.DISABLED,
                details={},
            )
        )
        settings_session.add(
            ManagedAgentProviderProfile(
                profile_id="disabled-profile",
                runtime_id="codex",
                provider_id="openai",
                enabled=False,
            )
        )
        await settings_session.commit()
        service = SettingsCatalogService(
            env={},
            session=settings_session,
            workspace_policy=SettingsWorkspacePolicy(
                allowed_runtimes=("codex", "codex_cli"),
                skills_canary_enabled=False,
                allowed_publication_modes=("none", "pr"),
                allowed_secret_ref_backends=("db",),
                maintenance_mode=True,
                allowed_operation_modes_during_maintenance=("normal",),
            ),
        )
        await service.apply_overrides(
            scope="workspace",
            changes={"skills.canary_percent": 0},
            expected_versions={"skills.canary_percent": 1},
        )

        invalid_cases = [
            (
                {"integrations.github.token_ref": "env://MISSING"},
                {"integrations.github.token_ref": 1},
                "integrations.github.token_ref",
                "secret_ref_backend_policy_denied",
            ),
            (
                {"integrations.github.token_ref": "db://missing-token"},
                {"integrations.github.token_ref": 1},
                "integrations.github.token_ref",
                "secret_ref_unresolved",
            ),
            (
                {"integrations.github.token_ref": "db://disabled-token"},
                {"integrations.github.token_ref": 1},
                "integrations.github.token_ref",
                "secret_ref_unresolved",
            ),
            (
                {"workflow.default_provider_profile_ref": "missing-profile"},
                {"workflow.default_provider_profile_ref": 1},
                "workflow.default_provider_profile_ref",
                "provider_profile_not_found",
            ),
            (
                {"workflow.default_provider_profile_ref": "disabled-profile"},
                {"workflow.default_provider_profile_ref": 1},
                "workflow.default_provider_profile_ref",
                "provider_profile_disabled",
            ),
            (
                {"workflow.default_task_runtime": "jules"},
                {"workflow.default_task_runtime": 1},
                "workflow.default_task_runtime",
                "runtime_policy_denied",
            ),
            (
                {"skills.canary_percent": 50},
                {"skills.canary_percent": 1},
                "skills.canary_percent",
                "feature_disabled_canary_percent",
            ),
            (
                {"workflow.default_publish_mode": "branch"},
                {"workflow.default_publish_mode": 1},
                "workflow.default_publish_mode",
                "publication_mode_policy_denied",
            ),
            (
                {"workflow.operation_mode": "maintenance"},
                {"workflow.operation_mode": 1},
                "workflow.operation_mode",
                "maintenance_mode_conflict",
            ),
        ]
        for changes, versions, key, code in invalid_cases:
            with pytest.raises(SettingsValidationError) as exc:
                await service.apply_overrides(
                    scope="workspace",
                    changes=changes,
                    expected_versions=versions,
                )
            _assert_validation_issue(exc, key=key, code=code)

        valid = await service.apply_overrides(
            scope="workspace",
            changes={"workflow.default_publish_mode": "none"},
            expected_versions={"workflow.default_publish_mode": 1},
        )
        with pytest.raises(SettingsValidationError) as exc:
            await service.apply_overrides(
                scope="workspace",
                changes={
                    "workflow.default_publish_mode": "branch",
                    "skills.canary_percent": 25,
                },
                expected_versions={
                    "workflow.default_publish_mode": valid.values[
                        "workflow.default_publish_mode"
                    ].value_version,
                    "skills.canary_percent": 1,
                },
            )
        _assert_validation_issue(
            exc,
            key="workflow.default_publish_mode",
            code="publication_mode_policy_denied",
        )
        unchanged = await service.effective_value_async(
            "workflow.default_publish_mode", scope="workspace"
        )
        canary = await service.effective_value_async(
            "skills.canary_percent", scope="workspace"
        )
        assert unchanged.value == "none"
        assert canary.value != 25


@pytest.mark.asyncio
async def test_mm656_secret_ref_backend_policy_applies_to_all_secret_ref_settings(
    settings_session_maker,
):
    extra_secret_ref = SettingRegistryEntry(
        key="integrations.secondary_token_ref",
        title="Secondary Token Reference",
        category="Integrations",
        section="user-workspace",
        value_type="secret_ref",
        ui="secret_ref_picker",
        scopes=("workspace",),
        default_value=None,
        order=999,
    )
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(
            env={},
            registry=(*_REGISTRY, extra_secret_ref),
            session=settings_session,
            workspace_policy=SettingsWorkspacePolicy(
                allowed_secret_ref_backends=("db",),
            ),
        )

        with pytest.raises(SettingsValidationError) as exc:
            await service.apply_overrides(
                scope="workspace",
                changes={"integrations.secondary_token_ref": "env://TOKEN"},
                expected_versions={"integrations.secondary_token_ref": 1},
            )

    _assert_validation_issue(
        exc,
        key="integrations.secondary_token_ref",
        code="secret_ref_backend_policy_denied",
    )


@pytest.mark.asyncio
async def test_mm656_persisted_overrides_surface_policy_diagnostics_after_policy_change(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        enabled_service = SettingsCatalogService(env={}, session=settings_session)
        await enabled_service.apply_overrides(
            scope="workspace",
            changes={
                "workflow.default_publish_mode": "branch",
                "skills.canary_percent": 50,
            },
            expected_versions={
                "workflow.default_publish_mode": 1,
                "skills.canary_percent": 1,
            },
        )

        restricted_service = SettingsCatalogService(
            env={},
            session=settings_session,
            workspace_policy=SettingsWorkspacePolicy(
                skills_canary_enabled=False,
                allowed_publication_modes=("none",),
            ),
        )
        publish_mode = await restricted_service.effective_value_async(
            "workflow.default_publish_mode", scope="workspace"
        )
        canary = await restricted_service.effective_value_async(
            "skills.canary_percent", scope="workspace"
        )

    assert [diagnostic.code for diagnostic in publish_mode.diagnostics] == [
        "publication_mode_policy_denied"
    ]
    assert [diagnostic.code for diagnostic in canary.diagnostics] == [
        "feature_disabled_canary_percent"
    ]


def test_mm656_settings_error_details_omit_top_level_fields():
    issue = SettingValidationIssue(
        key="workflow.default_publish_mode",
        scope="workspace",
        code="publication_mode_policy_denied",
        message="workflow.default_publish_mode is not allowed by workspace policy.",
        boundary="write_request",
        rule="allowed_publication_modes",
        blocks=["persistence"],
        details={"allowed": ["none"]},
    )

    error = SettingsValidationError([issue]).to_settings_error()

    assert error.key == "workflow.default_publish_mode"
    assert error.scope == "workspace"
    assert error.message == (
        "workflow.default_publish_mode is not allowed by workspace policy."
    )
    assert "key" not in error.details
    assert "scope" not in error.details
    assert "message" not in error.details
    assert error.details["code"] == "publication_mode_policy_denied"
    assert error.details["details"] == {"allowed": ["none"]}


def test_mm656_boundary_validation_helpers_return_structured_issues():
    service = SettingsCatalogService(
        env={},
        workspace_policy=SettingsWorkspacePolicy(
            allowed_runtimes=("codex",),
            allowed_publication_modes=("none",),
        ),
    )

    descriptor_issues = service.validate_descriptor_generation()
    preview_issues = service.validate_effective_preview(
        "workflow.default_task_runtime",
        "jules",
        scope="workspace",
    )
    launch_issues = service.validate_launch_execution(
        {"workflow.default_publish_mode": "pr"},
        scope="workspace",
    )
    operation_issues = service.validate_operation_execution(
        {"workflow.default_publish_mode": "pr"},
        scope="workspace",
    )
    readiness = service.readiness_diagnostics(
        {"workflow.default_publish_mode": "pr"},
        scope="workspace",
    )

    assert descriptor_issues == []
    assert preview_issues[0].boundary == "effective_preview"
    assert preview_issues[0].blocks == ["preview"]
    assert launch_issues[0].boundary == "launch_execution"
    assert launch_issues[0].blocks == ["launch", "readiness"]
    assert operation_issues[0].boundary == "operation_execution"
    assert operation_issues[0].blocks == ["operation"]
    assert readiness["workflow.default_publish_mode"][0].boundary == (
        "readiness_diagnostics"
    )
    assert readiness["workflow.default_publish_mode"][0].blocks == ["readiness"]


@pytest.mark.asyncio
async def test_mm657_validate_changes_accepts_without_rows_or_audit(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(env={}, session=settings_session)

        response = await service.validate_changes(
            scope="workspace",
            changes={"workflow.default_publish_mode": "branch"},
            expected_versions={"workflow.default_publish_mode": 1},
        )
        rows = (
            await settings_session.execute(select(SettingsOverride))
        ).scalars().all()
        audit_count = await service.audit_event_count()

    assert response.accepted is True
    assert response.issues == []
    assert response.issues_by_key == {}
    assert rows == []
    assert audit_count == 0


@pytest.mark.asyncio
async def test_mm657_validate_changes_returns_version_and_value_issues(
    settings_session_maker,
):
    raw_secret_value = "gh" + "p_raw_plaintext"
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(env={}, session=settings_session)

        response = await service.validate_changes(
            scope="workspace",
            changes={
                "workflow.default_publish_mode": "not-supported",
                "integrations.github.token_ref": raw_secret_value,
            },
            expected_versions={
                "workflow.default_publish_mode": 99,
                "integrations.github.token_ref": 1,
            },
        )
        rows = (
            await settings_session.execute(select(SettingsOverride))
        ).scalars().all()

    assert response.accepted is False
    assert response.issues_by_key["workflow.default_publish_mode"][0].code == (
        "version_conflict"
    )
    assert any(
        issue.code == "enum_value_invalid"
        for issue in response.issues_by_key["workflow.default_publish_mode"]
    )
    assert response.issues_by_key["integrations.github.token_ref"][0].code == (
        "unsafe_setting_payload"
    )
    assert rows == []
    assert raw_secret_value not in response.model_dump_json()


@pytest.mark.asyncio
async def test_mm657_validate_changes_preserves_unsupported_requested_scope(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(env={}, session=settings_session)

        response = await service.validate_changes(
            scope="system",
            changes={"workflow.default_publish_mode": "branch"},
            expected_versions={"workflow.default_publish_mode": 1},
        )

    assert response.accepted is False
    assert response.issues[0].code == "unsupported_scope"
    assert response.issues[0].scope == "system"


@pytest.mark.asyncio
async def test_mm657_preview_changes_reports_diffs_reload_and_no_commit(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(env={}, session=settings_session)

        response = await service.preview_changes(
            scope="workspace",
            changes={"skills.policy_mode": "allowlist"},
            expected_versions={"skills.policy_mode": 1},
        )
        persisted = await service.effective_value_async(
            "skills.policy_mode", scope="workspace"
        )
        rows = (
            await settings_session.execute(select(SettingsOverride))
        ).scalars().all()
        audit_count = await service.audit_event_count()

    assert response.accepted is True
    assert response.issues == []
    assert response.diffs[0].key == "skills.policy_mode"
    assert response.diffs[0].before.value != response.diffs[0].after.value
    assert response.diffs[0].after.value == "allowlist"
    _assert_reload_requirement(response, key="skills.policy_mode")
    assert response.dependency_warnings == []
    assert persisted.value != "allowlist"
    assert rows == []
    assert audit_count == 0


@pytest.mark.asyncio
async def test_mm657_preview_changes_reports_dependency_warnings_and_redacted_refs(
    settings_session_maker,
):
    registry = (
        *_mm656_registry(),
        SettingRegistryEntry(
            key="test.dependent",
            title="Dependent",
            category="MM-657",
            section="user-workspace",
            value_type="string",
            ui="input",
            scopes=("workspace",),
            default_value="off",
            depends_on=(
                SettingDependency(
                    key="test.boolean",
                    required_value=True,
                    reason="dependent mode requires enablement",
                ),
            ),
            order=100,
        ),
    )
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(
            env={},
            registry=registry,
            session=settings_session,
        )

        dependency_response = await service.preview_changes(
            scope="workspace",
            changes={"test.dependent": "on"},
            expected_versions={"test.dependent": 1},
        )
        secret_response = await service.preview_changes(
            scope="workspace",
            changes={"test.secret_ref": "db://missing-token"},
            expected_versions={"test.secret_ref": 1},
        )

    assert dependency_response.accepted is False
    assert dependency_response.dependency_warnings[0].key == "test.dependent"
    assert dependency_response.dependency_warnings[0].dependency_key == "test.boolean"
    _assert_preview_issue(
        secret_response,
        key="test.secret_ref",
        code="secret_ref_unresolved",
    )
    _assert_redacted_diff(secret_response, key="test.secret_ref")


@pytest.mark.asyncio
async def test_mm656_dependency_validation_runs_at_all_boundaries(settings_session_maker):
    entry_type = SettingsCatalogService(env={})._registry[0].__class__
    registry = (
        entry_type(
            key="test.feature_enabled",
            title="Feature Enabled",
            category="MM-656",
            section="user-workspace",
            value_type="boolean",
            ui="toggle",
            scopes=("workspace",),
            default_value=False,
            order=1,
        ),
        entry_type(
            key="test.feature_mode",
            title="Feature Mode",
            category="MM-656",
            section="user-workspace",
            value_type="string",
            ui="input",
            scopes=("workspace",),
            default_value="off",
            depends_on=(
                SettingDependency(
                    key="test.feature_enabled",
                    required_value=True,
                    reason="feature mode requires enablement",
                ),
            ),
            order=2,
        ),
    )
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(
            env={},
            registry=registry,
            session=settings_session,
        )
        with pytest.raises(SettingsValidationError) as write_exc:
            await service.apply_overrides(
                scope="workspace",
                changes={"test.feature_mode": "on"},
                expected_versions={"test.feature_mode": 1},
            )
        _assert_validation_issue(
            write_exc,
            key="test.feature_mode",
            code="dependency_not_satisfied",
        )

        response = await service.apply_overrides(
            scope="workspace",
            changes={"test.feature_enabled": True, "test.feature_mode": "on"},
            expected_versions={"test.feature_enabled": 1, "test.feature_mode": 1},
        )

    assert response.values["test.feature_mode"].value == "on"

    helper_service = SettingsCatalogService(env={}, registry=registry)
    assert helper_service.validate_effective_preview(
        "test.feature_mode",
        "on",
        scope="workspace",
    )[0].code == "dependency_not_satisfied"
    assert helper_service.validate_launch_execution(
        {"test.feature_mode": "on"},
        scope="workspace",
    )[0].boundary == "launch_execution"
    assert helper_service.validate_operation_execution(
        {"test.feature_mode": "on"},
        scope="workspace",
    )[0].boundary == "operation_execution"
    assert helper_service.readiness_diagnostics(
        {"test.feature_mode": "on"},
        scope="workspace",
    )["test.feature_mode"][0].code == "dependency_not_satisfied"


@pytest.mark.asyncio
async def test_mm656_write_validates_unchanged_effective_companion(settings_session_maker):
    async with settings_session_maker() as settings_session:
        enabled_service = SettingsCatalogService(env={}, session=settings_session)
        await enabled_service.apply_overrides(
            scope="workspace",
            changes={"skills.canary_percent": 50},
            expected_versions={"skills.canary_percent": 1},
        )

        disabled_service = SettingsCatalogService(
            env={},
            session=settings_session,
            workspace_policy=SettingsWorkspacePolicy(skills_canary_enabled=False),
        )
        with pytest.raises(SettingsValidationError) as exc:
            await disabled_service.apply_overrides(
                scope="workspace",
                changes={"workflow.default_publish_mode": "branch"},
                expected_versions={"workflow.default_publish_mode": 1},
            )

    _assert_validation_issue(
        exc,
        key="skills.canary_percent",
        code="feature_disabled_canary_percent",
    )


@pytest.mark.asyncio
async def test_mm656_pre_persistence_boundary_blocks_rows_and_audit(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(env={}, session=settings_session)
        original_validate_values = service._validate_values

        def fail_pre_persistence(values, *, scope, boundary):
            if boundary == "pre_persistence":
                return [
                    service._validation_issue(
                        service._entries_by_key["workflow.default_publish_mode"],
                        scope,
                        code="pre_persistence_rejected",
                        message="Rejected before persistence.",
                        boundary=boundary,
                        rule="test_injected_boundary",
                    )
                ]
            return original_validate_values(values, scope=scope, boundary=boundary)

        service._validate_values = fail_pre_persistence  # type: ignore[method-assign]
        with pytest.raises(SettingsValidationError) as exc:
            await service.apply_overrides(
                scope="workspace",
                changes={"workflow.default_publish_mode": "branch"},
                expected_versions={"workflow.default_publish_mode": 1},
            )
        row_count = (
            await settings_session.execute(select(SettingsOverride))
        ).scalars().all()
        audit_count = await service.audit_event_count()

    _assert_validation_issue(
        exc,
        key="workflow.default_publish_mode",
        code="pre_persistence_rejected",
        boundary="pre_persistence",
    )
    assert row_count == []
    assert audit_count == 0


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


@pytest.mark.asyncio
async def test_catalog_async_rejects_descriptor_without_apply_mode():
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
        await service.catalog_async(section="user-workspace", scope="workspace")


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
    assert inherited.source == "config_file"
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
    assert effective.source == "workspace_override"
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
    assert effective.source == "workspace_override"
    assert effective.value_version == 4
    assert effective.diagnostics[0].code == "post_migration_invalid"
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
        with pytest.raises(SettingsValidationError) as missing:
            await service.apply_overrides(
                scope="workspace",
                changes={"workflow.default_provider_profile_ref": "missing-profile"},
                expected_versions={"workflow.default_provider_profile_ref": 1},
            )
        assert missing.value.issues[0].code == "provider_profile_not_found"

        with pytest.raises(SettingsValidationError) as disabled:
            await service.apply_overrides(
                scope="workspace",
                changes={"workflow.default_provider_profile_ref": "disabled-profile"},
                expected_versions={"workflow.default_provider_profile_ref": 1},
            )
        assert disabled.value.issues[0].code == "provider_profile_disabled"


@pytest.mark.asyncio
async def test_provider_profile_reference_rejects_enabled_not_ready_profile_without_plaintext(
    settings_session_maker,
) -> None:
    async with settings_session_maker() as settings_session:
        settings_session.add(
            ManagedSecret(
                slug="disabled-profile-secret",
                ciphertext="disabled-secret-plaintext",
                status=SecretStatus.DISABLED,
                details={},
            )
        )
        settings_session.add(
            ManagedAgentProviderProfile(
                profile_id="enabled-not-ready-profile",
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source=ProviderCredentialSource.SECRET_REF,
                runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                secret_refs={"provider_api_key": "db://disabled-profile-secret"},
                enabled=True,
            )
        )
        await settings_session.commit()

        service = SettingsCatalogService(env={}, session=settings_session)
        with pytest.raises(SettingsValidationError) as not_ready:
            await service.apply_overrides(
                scope="workspace",
                changes={
                    "workflow.default_provider_profile_ref": "enabled-not-ready-profile"
                },
                expected_versions={"workflow.default_provider_profile_ref": 1},
            )

    assert not_ready.value.issues[0].code == "provider_profile_not_ready"
    dumped = str([issue.model_dump() for issue in not_ready.value.issues])
    assert "disabled-secret-plaintext" not in dumped
    assert "disabled-profile-secret" in dumped


def test_provider_profile_readiness_treats_null_cooldown_as_blocker() -> None:
    service = SettingsCatalogService(env={})
    row = ManagedAgentProviderProfile(
        profile_id="null-cooldown-profile",
        runtime_id="codex_cli",
        provider_id="openai",
        credential_source=ProviderCredentialSource.NONE,
        runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
        max_parallel_runs=1,
        enabled=True,
    )
    row.cooldown_after_429_seconds = None

    diagnostic = service._provider_profile_readiness_diagnostic(
        row.profile_id,
        row,
    )

    assert diagnostic is not None
    assert diagnostic.code == "provider_profile_not_ready"
    assert "cooldown is invalid" in diagnostic.details["readiness_blockers"]


@pytest.mark.asyncio
async def test_provider_profile_override_preserves_resettable_source(
    settings_session_maker,
) -> None:
    async with settings_session_maker() as settings_session:
        settings_session.add(
            ManagedAgentProviderProfile(
                profile_id="codex-default",
                runtime_id="codex_cli",
                provider_id="openai",
                enabled=True,
            )
        )
        await settings_session.commit()
        service = SettingsCatalogService(env={}, session=settings_session)

        response = await service.apply_overrides(
            scope="workspace",
            changes={"workflow.default_provider_profile_ref": "codex-default"},
            expected_versions={"workflow.default_provider_profile_ref": 1},
        )
        effective = response.values["workflow.default_provider_profile_ref"]
        descriptor = next(
            item
            for item in (
                await service.catalog_async(section="user-workspace", scope="workspace")
            ).categories["Workflow"]
            if item.key == "workflow.default_provider_profile_ref"
        )

    assert effective.value == "codex-default"
    assert effective.source == "workspace_override"
    assert effective.diagnostics == []
    assert descriptor.effective_value == "codex-default"
    assert descriptor.source == "workspace_override"
    assert descriptor.diagnostics == []


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
        "blocks": ["launch", "readiness"],
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
    assert intentional_null.diagnostics[0].code == "intentional_null_override"


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
async def test_oversized_override_value_rejected_before_persistence(
    settings_session_maker,
):
    entry_type = SettingsCatalogService(env={})._registry[0].__class__
    registry = (
        entry_type(
            key="test.large_payload",
            title="Large Payload",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="default",
            order=1,
            apply_mode="immediate",
        ),
    )
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(
            env={},
            registry=registry,
            session=settings_session,
        )

        with pytest.raises(ValueError, match="invalid_setting_value"):
            await service.apply_overrides(
                scope="workspace",
                changes={"test.large_payload": "x" * 20000},
                expected_versions={"test.large_payload": 1},
            )

        rows = (await settings_session.execute(select(SettingsOverride))).scalars().all()
        effective = await service.effective_value_async(
            "test.large_payload", scope="workspace"
        )

    assert rows == []
    assert effective.value == "default"
    assert effective.source == "default"


@pytest.mark.asyncio
async def test_unsafe_payload_classes_rejected_before_persistence(
    settings_session_maker,
):
    entry_type = SettingsCatalogService(env={})._registry[0].__class__
    registry = (
        entry_type(
            key="test.payload_guard",
            title="Payload Guard",
            category="Test",
            section="user-workspace",
            value_type="string",
            ui="text",
            scopes=("workspace",),
            default_value="safe",
            order=1,
            apply_mode="immediate",
        ),
    )
    unsafe_values = [
        "oauth_session_blob={...}",
        "decrypted_credential_file=/tmp/credential.json",
        "generated_config password=plaintext",
        "large_artifact:" + ("x" * 20000),
        "workflow_payload={\"steps\": []}",
        "operational command_history: rm -rf /",
    ]
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(
            env={},
            registry=registry,
            session=settings_session,
        )

        for unsafe_value in unsafe_values:
            with pytest.raises(ValueError, match="invalid_setting_value"):
                await service.apply_overrides(
                    scope="workspace",
                    changes={"test.payload_guard": unsafe_value},
                    expected_versions={"test.payload_guard": 1},
                )

        rows = (await settings_session.execute(select(SettingsOverride))).scalars().all()
        effective = await service.effective_value_async(
            "test.payload_guard", scope="workspace"
        )

    assert rows == []
    assert effective.value == "safe"
    assert effective.source == "default"


@pytest.mark.asyncio
async def test_reset_edge_cases_preserve_sparse_scope_boundaries(
    settings_session_maker,
):
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(env={}, session=settings_session)
        await service.apply_overrides(
            scope="workspace",
            changes={"integrations.github.token_ref": "env://WORKSPACE_TOKEN"},
            expected_versions={"integrations.github.token_ref": 1},
        )
        await service.apply_overrides(
            scope="user",
            changes={"integrations.github.token_ref": "env://USER_TOKEN"},
            expected_versions={"integrations.github.token_ref": 1},
        )

        workspace_reset = await service.reset_override(
            "integrations.github.token_ref", scope="workspace"
        )
        user_after_workspace_reset = await service.effective_value_async(
            "integrations.github.token_ref", scope="user"
        )
        user_reset = await service.reset_override(
            "integrations.github.token_ref", scope="user"
        )
        absent_reset = await service.reset_override(
            "integrations.github.token_ref", scope="user"
        )

        with pytest.raises(KeyError):
            await service.reset_override("settings.unknown_key", scope="workspace")
        with pytest.raises(ValueError, match="invalid_scope"):
            await service.reset_override(
                "workflow.default_publish_mode", scope="user"
            )

    assert workspace_reset.source in {"config_file", "environment", "default"}
    assert user_after_workspace_reset.value == "env://USER_TOKEN"
    assert user_after_workspace_reset.source == "user_override"
    assert user_reset.source in {"config_file", "environment", "default"}
    assert absent_reset.source in {"config_file", "environment", "default"}


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

    assert reset.source == "config_file"
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
        settings_session.add(
            SettingsOverride(
                scope="workspace",
                workspace_id=UUID("00000000-0000-0000-0000-000000000000"),
                user_id=UUID("00000000-0000-0000-0000-000000000000"),
                key="integrations.github.token_ref",
                value_json="db://missing-token",
                value_version=1,
            )
        )
        settings_session.add(
            SettingsAuditEvent(
                event_type="settings.override.updated",
                key="integrations.github.token_ref",
                scope="workspace",
                workspace_id=UUID("00000000-0000-0000-0000-000000000000"),
                user_id=UUID("00000000-0000-0000-0000-000000000000"),
                new_value_json="db://missing-token",
                redacted=True,
                reason="wire github token",
            )
        )
        await settings_session.commit()
        service = SettingsCatalogService(env={}, session=settings_session)

        diagnostics = await service.diagnostics(scope="workspace")

    github = diagnostics.values["integrations.github.token_ref"]
    assert github.source == "secret_ref"
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


# ---------------------------------------------------------------------------
# MM-655: effective value resolver source, lock, metadata, diagnostics
# ---------------------------------------------------------------------------


def test_mm655_effective_value_metadata_and_canonical_source_vocabulary():
    configured_entry = SettingRegistryEntry(
        key="test.configured",
        title="Configured",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        scopes=("workspace",),
        default_value="built-in",
        settings_path=("settings", "configured"),
        apply_mode="worker_reload",
        requires_reload=True,
        applies_to=("worker", "task_creation"),
        order=1,
    )
    default_entry = SettingRegistryEntry(
        key="test.default_only",
        title="Default Only",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        scopes=("workspace",),
        default_value="catalog-default",
        order=2,
    )
    service = SettingsCatalogService(
        env={},
        settings=SimpleNamespace(settings=SimpleNamespace(configured="from-config")),
        registry=(configured_entry, default_entry),
    )

    configured = service.effective_value("test.configured", scope="workspace")
    default_only = service.effective_value("test.default_only", scope="workspace")

    assert configured.value == "from-config"
    assert configured.source == "config_file"
    assert configured.default_value == "built-in"
    assert configured.inheritance_state == "inherited"
    assert configured.requires_reload is True
    assert configured.applies_to == ["worker", "task_creation"]
    assert default_only.value == "catalog-default"
    assert default_only.source == "default"
    assert default_only.default_value == "catalog-default"
    assert default_only.inheritance_state == "inherited"


@pytest.mark.asyncio
async def test_mm655_operator_lock_wins_and_sets_read_only_reason(settings_session_maker):
    locked_entry = SettingRegistryEntry(
        key="test.locked",
        title="Locked",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        scopes=("workspace",),
        default_value="built-in",
        operator_locked_value="operator-value",
        operator_lock_reason="Controlled by operator policy.",
        order=1,
    )
    async with settings_session_maker() as settings_session:
        service = SettingsCatalogService(
            env={},
            registry=(locked_entry,),
            session=settings_session,
        )
        settings_session.add(
            SettingsOverride(
                scope="workspace",
                key="test.locked",
                value_json="workspace-value",
            )
        )
        await settings_session.commit()

        effective = await service.effective_value_async(
            "test.locked",
            scope="workspace",
        )
        descriptor = (
            await service.catalog_async(section="user-workspace", scope="workspace")
        ).categories["Test"][0]

    assert effective.value == "operator-value"
    assert effective.source == "operator_lock"
    assert effective.inheritance_state == "locked"
    assert effective.read_only is True
    assert effective.read_only_reason == "Controlled by operator policy."
    assert descriptor.effective_value == "operator-value"
    assert descriptor.source == "operator_lock"
    assert descriptor.read_only is True
    assert descriptor.read_only_reason == "Controlled by operator policy."


@pytest.mark.asyncio
async def test_mm655_distinct_resolution_diagnostics(settings_session_maker):
    no_default = SettingRegistryEntry(
        key="test.no_default",
        title="No Default",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        scopes=("workspace",),
        order=1,
    )
    inherited_null = SettingRegistryEntry(
        key="test.inherited_null",
        title="Inherited Null",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        scopes=("workspace",),
        default_value=None,
        settings_path=("missing", "path"),
        order=2,
    )
    intentional_null = SettingRegistryEntry(
        key="test.intentional_null",
        title="Intentional Null",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        scopes=("workspace",),
        default_value="default",
        order=3,
    )
    policy_blocked = SettingRegistryEntry(
        key="test.policy_blocked",
        title="Policy Blocked",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        scopes=("workspace",),
        default_value="blocked",
        policy_blocked_reason="Workspace policy blocks this value.",
        order=4,
    )
    migrated = SettingRegistryEntry(
        key="test.migrated",
        title="Migrated",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        scopes=("workspace",),
        default_value="default",
        order=5,
    )
    async with settings_session_maker() as settings_session:
        settings_session.add(
            SettingsOverride(
                scope="workspace",
                key="test.migrated",
                value_json={"old": "shape"},
                schema_version=1,
            )
        )
        await settings_session.commit()
        service = SettingsCatalogService(
            env={},
            registry=(
                no_default,
                inherited_null,
                intentional_null,
                policy_blocked,
                migrated,
            ),
            migration_rules=(
                SettingMigrationRule(
                    old_key="test.migrated",
                    state="type_changed",
                    message="Stored value is invalid after migration.",
                    expected_schema_version=2,
                ),
            ),
            session=settings_session,
        )
        await service.apply_overrides(
            scope="workspace",
            changes={"test.intentional_null": None},
            expected_versions={"test.intentional_null": 1},
        )

        values = {
            key: await service.effective_value_async(key, scope="workspace")
            for key in (
                "test.no_default",
                "test.inherited_null",
                "test.intentional_null",
                "test.policy_blocked",
                "test.migrated",
            )
        }

    assert values["test.no_default"].diagnostics[0].code == "no_default"
    assert values["test.inherited_null"].diagnostics[0].code == "inherited_null"
    assert (
        values["test.intentional_null"].diagnostics[0].code
        == "intentional_null_override"
    )
    assert values["test.policy_blocked"].diagnostics[0].code == "policy_blocked"
    assert values["test.migrated"].diagnostics[0].code == "post_migration_invalid"


@pytest.mark.asyncio
async def test_mm655_reference_sources_remain_secret_safe(settings_session_maker):
    async with settings_session_maker() as settings_session:
        settings_session.add(
            ManagedSecret(
                slug="active-token",
                ciphertext="active-secret-plaintext",
                status=SecretStatus.ACTIVE,
                details={},
            )
        )
        settings_session.add(
            ManagedAgentProviderProfile(
                profile_id="codex-default",
                runtime_id="codex",
                provider_id="openai",
                enabled=True,
            )
        )
        await settings_session.commit()

        service = SettingsCatalogService(env={}, session=settings_session)
        secret_ref = await service.apply_overrides(
            scope="workspace",
            changes={"integrations.github.token_ref": "db://active-token"},
            expected_versions={"integrations.github.token_ref": 1},
        )
        provider_ref = await service.apply_overrides(
            scope="workspace",
            changes={"workflow.default_provider_profile_ref": "codex-default"},
            expected_versions={"workflow.default_provider_profile_ref": 1},
        )

    github = secret_ref.values["integrations.github.token_ref"]
    provider = provider_ref.values["workflow.default_provider_profile_ref"]
    assert github.source == "secret_ref"
    assert github.diagnostics == []
    assert "active-secret-plaintext" not in github.model_dump_json()
    assert provider.source == "workspace_override"
    assert provider.diagnostics == []
    assert "secret_refs" not in provider.model_dump_json()


# ---------------------------------------------------------------------------
# MM-652: SettingsRegistry, SettingsCatalogBuilder, migration gate
# ---------------------------------------------------------------------------

def _minimal_entry(key: str = "workflow.default_task_runtime") -> SettingRegistryEntry:
    return SettingRegistryEntry(
        key=key,
        title="Test Setting",
        category="Test",
        section="user-workspace",
        value_type="string",
        ui="input",
        scopes=("workspace",),
        order=1,
        apply_mode="immediate",
        applies_to=("test",),
    )


def test_settings_registry_validates_unique_keys():
    entry = _minimal_entry()
    with pytest.raises(ValueError, match="duplicate_key"):
        SettingsRegistry((entry, entry), stable_key_ledger=None)


def test_settings_registry_validates_key_format():
    bad = _minimal_entry("BadKey")
    with pytest.raises(ValueError, match="invalid_key_format"):
        SettingsRegistry((bad,), stable_key_ledger=None)


def test_settings_registry_rejects_key_with_uppercase_segment():
    bad = _minimal_entry("workflow.BadSuffix")
    with pytest.raises(ValueError, match="invalid_key_format"):
        SettingsRegistry((bad,), stable_key_ledger=None)


def test_settings_registry_accepts_valid_dotted_key():
    entry = _minimal_entry("workflow.my_setting_1")
    registry = SettingsRegistry((entry,), stable_key_ledger=None)
    assert registry.get("workflow.my_setting_1") is entry


def test_settings_registry_orders_entries_at_initialization():
    first = _minimal_entry("workflow.first")
    second = _minimal_entry("workflow.second")
    first = first.__class__(**{**first.__dict__, "order": 1})
    second = second.__class__(**{**second.__dict__, "order": 2})

    registry = SettingsRegistry((second, first), stable_key_ledger=None)

    assert [entry.key for entry in registry.entries] == [
        "workflow.first",
        "workflow.second",
    ]


def test_settings_registry_migration_gate_raises_for_removed_key_without_rule():
    ledger = frozenset({"workflow.default_task_runtime", "workflow.old_key"})
    entry = _minimal_entry("workflow.default_task_runtime")
    with pytest.raises(ValueError, match="catalog_integrity_error"):
        SettingsRegistry((entry,), stable_key_ledger=ledger)


def test_settings_registry_migration_gate_passes_with_migration_rule():
    ledger = frozenset({"workflow.default_task_runtime", "workflow.old_key"})
    entry = _minimal_entry("workflow.default_task_runtime")
    rule = SettingMigrationRule(
        old_key="workflow.old_key",
        state="removed",
        message="removed in MM-652",
    )
    registry = SettingsRegistry((entry,), migration_rules=(rule,), stable_key_ledger=ledger)
    assert registry.get("workflow.default_task_runtime") is entry


def test_settings_registry_migration_gate_skipped_when_no_ledger():
    entry = _minimal_entry("workflow.default_task_runtime")
    registry = SettingsRegistry((entry,), stable_key_ledger=None)
    assert len(registry.entries) == 1


def test_settings_registry_default_uses_catalog_key_ledger():
    assert "workflow.default_task_runtime" in _CATALOG_KEY_LEDGER
    assert len(_CATALOG_KEY_LEDGER) == 8


def test_settings_registry_default_registry_passes_ledger_check():
    registry = SettingsRegistry(_REGISTRY)
    assert len(registry.entries) == 8


def test_settings_catalog_builder_filters_by_section():
    entry_uw = _minimal_entry("workflow.test_uw")
    entry_ops = SettingRegistryEntry(
        key="operations.test_ops",
        title="Ops Setting",
        category="Ops",
        section="operations",
        value_type="string",
        ui="input",
        scopes=("operator",),
        order=2,
        apply_mode="immediate",
        applies_to=("test",),
    )
    registry = SettingsRegistry((entry_uw, entry_ops), stable_key_ledger=None)
    builder = SettingsCatalogBuilder(registry)

    from api_service.services.settings_catalog import SettingDescriptor, SettingAuditPolicy

    def make_descriptor(entry):
        return SettingDescriptor(
            key=entry.key,
            title=entry.title,
            category=entry.category,
            section=entry.section,
            type=entry.value_type,
            ui=entry.ui,
            scopes=list(entry.scopes),
            default_value=None,
            effective_value=None,
            source="default",
            source_explanation="built-in default",
            apply_mode=entry.apply_mode,
            activation_state="active",
            active=True,
            order=entry.order,
            audit=SettingAuditPolicy(),
        )

    result = builder.build("user-workspace", descriptor_fn=make_descriptor)
    assert "Test" in result.categories
    assert "Ops" not in result.categories


def test_settings_catalog_builder_filters_by_scope():
    user_entry = SettingRegistryEntry(
        key="workflow.user_only",
        title="User Only",
        category="Workflow",
        section="user-workspace",
        value_type="string",
        ui="input",
        scopes=("user",),
        order=1,
        apply_mode="immediate",
        applies_to=("test",),
    )
    ws_entry = SettingRegistryEntry(
        key="workflow.workspace_only",
        title="Workspace Only",
        category="Workflow",
        section="user-workspace",
        value_type="string",
        ui="input",
        scopes=("workspace",),
        order=2,
        apply_mode="immediate",
        applies_to=("test",),
    )
    registry = SettingsRegistry((user_entry, ws_entry), stable_key_ledger=None)
    builder = SettingsCatalogBuilder(registry)

    from api_service.services.settings_catalog import SettingDescriptor, SettingAuditPolicy

    def make_descriptor(entry):
        return SettingDescriptor(
            key=entry.key,
            title=entry.title,
            category=entry.category,
            section=entry.section,
            type=entry.value_type,
            ui=entry.ui,
            scopes=list(entry.scopes),
            default_value=None,
            effective_value=None,
            source="default",
            source_explanation="built-in default",
            apply_mode=entry.apply_mode,
            activation_state="active",
            active=True,
            order=entry.order,
            audit=SettingAuditPolicy(),
        )

    ws_result = builder.build(scope="workspace", descriptor_fn=make_descriptor)
    ws_keys = [e.key for entries in ws_result.categories.values() for e in entries]
    assert "workflow.workspace_only" in ws_keys
    assert "workflow.user_only" not in ws_keys


def test_settings_registry_from_pydantic_model_extracts_exposed_field():
    from pydantic import Field
    from pydantic_settings import BaseSettings

    class SampleSettings(BaseSettings):
        my_flag: bool = Field(
            True,
            json_schema_extra={
                "moonmind": {
                    "expose": True,
                    "key": "test.my_flag",
                    "section": "user-workspace",
                    "category": "Test",
                    "scopes": ["workspace"],
                    "ui": "toggle",
                    "type": "boolean",
                    "requires_reload": False,
                    "apply_mode": "next_task",
                    "title": "My Flag",
                    "applies_to": ["test"],
                    "order": 1,
                }
            },
        )
        _hidden: str = "not_exposed"

    registry = SettingsRegistry.from_pydantic_model(SampleSettings, stable_key_ledger=None)
    assert registry.get("test.my_flag") is not None
    assert registry.get("test.my_flag").value_type == "boolean"


def test_settings_registry_from_pydantic_model_skips_unexposed_field():
    from pydantic import Field
    from pydantic_settings import BaseSettings

    class SampleSettings(BaseSettings):
        hidden_field: str = Field("hidden")
        exposed_field: str = Field(
            "exposed",
            json_schema_extra={
                "moonmind": {
                    "expose": True,
                    "key": "test.exposed_field",
                    "section": "user-workspace",
                    "category": "Test",
                    "scopes": ["workspace"],
                    "ui": "input",
                    "type": "string",
                    "requires_reload": False,
                    "apply_mode": "next_task",
                    "title": "Exposed Field",
                    "applies_to": ["test"],
                    "order": 1,
                }
            },
        )

    registry = SettingsRegistry.from_pydantic_model(SampleSettings, stable_key_ledger=None)
    assert registry.get("test.exposed_field") is not None
    assert len(registry.entries) == 1


def test_settings_registry_from_pydantic_model_does_not_expose_undefined_defaults():
    from pydantic import Field
    from pydantic_settings import BaseSettings

    def exposed(key: str) -> dict:
        return {
            "moonmind": {
                "expose": True,
                "key": key,
                "section": "user-workspace",
                "category": "Test",
                "scopes": ["workspace"],
                "ui": "input",
                "type": "string",
                "apply_mode": "next_task",
                "applies_to": ["test"],
                "order": 1,
            }
        }

    class SampleSettings(BaseSettings):
        required_value: str = Field(..., json_schema_extra=exposed("test.required_value"))
        generated_value: str = Field(
            default_factory=lambda: "generated",
            json_schema_extra=exposed("test.generated_value"),
        )
        literal_value: str = Field(
            "literal",
            json_schema_extra=exposed("test.literal_value"),
        )

    registry = SettingsRegistry.from_pydantic_model(SampleSettings, stable_key_ledger=None)

    assert registry.get("test.required_value").default_value is None
    assert registry.get("test.generated_value").default_value is None
    assert registry.get("test.literal_value").default_value == "literal"


def test_workflow_settings_has_moonmind_expose_metadata():
    from moonmind.config.settings import WorkflowSettings

    exposed_keys = set()
    for _name, field_info in WorkflowSettings.model_fields.items():
        extra = getattr(field_info, "json_schema_extra", None) or {}
        mm = extra.get("moonmind", {}) if isinstance(extra, dict) else {}
        if mm.get("expose"):
            exposed_keys.add(mm["key"])

    assert "workflow.default_task_runtime" in exposed_keys
    assert "workflow.default_publish_mode" in exposed_keys
    assert "skills.policy_mode" in exposed_keys
    assert "skills.canary_percent" in exposed_keys
    assert "live_sessions.default_enabled" in exposed_keys
