from api_service.services.settings_catalog import SettingsCatalogService


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
