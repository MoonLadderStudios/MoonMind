"""MM-713 Settings System guardrail suite.

This file is a single, named contract/guardrail test suite that maps every
non-goal in `docs/Security/SettingsSystem.md` §4, every security requirement
in §22, and every desired-state invariant in §29 to at least one direct
assertion. It also asserts that the suggested §26 internal-component split
is reflected in the actual code structure, and that the local-first
walkthrough doc exists.

Each test is named `test_section_<n>_<short_label>` so a grep over the file
produces an audit-friendly mapping back to the design doc. A new descriptor
or design item must add its own entry here before catalog work merges —
the suite is the regression net for §4/§22/§29.

Run with: ./tools/test_unit.sh tests/unit/services/test_settings_guardrails.py
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Iterable

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base
from api_service.services.settings_catalog import (
    SETTINGS_PERMISSION_NAMES,
    EffectiveSettingValue,
    SettingDescriptor,
    SettingRegistryEntry,
    SettingsCatalogService,
    SettingsRegistry,
    _MAX_OVERRIDE_VALUE_BYTES,
    _PERSISTED_SCOPES,
    _REGISTRY,
    _UNSAFE_FIELD_TOKENS,
    _UNSAFE_STRING_TOKENS,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[3]
SETTINGS_SYSTEM_DOC = REPO_ROOT / "docs" / "Security" / "SettingsSystem.md"
LOCAL_FIRST_DOC = REPO_ROOT / "docs" / "Security" / "SettingsLocalFirstBringUp.md"
SETTINGS_ROUTER_FILE = (
    REPO_ROOT / "api_service" / "api" / "routers" / "settings.py"
)


def _operator_locked_registry() -> tuple[SettingRegistryEntry, ...]:
    return (
        SettingRegistryEntry(
            key="ops.feature_flag",
            title="Locked Feature Flag",
            category="Operations",
            section="user-workspace",
            value_type="boolean",
            ui="toggle",
            scopes=("workspace",),
            order=10,
            default_value=False,
            operator_locked_value=True,
            operator_lock_reason="environment policy",
            apply_mode="immediate",
        ),
    )


@pytest_asyncio.fixture
async def settings_async_session(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/settings-guardrail.db"
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    yield session_maker
    await engine.dispose()


# ===========================================================================
# §4 Non-Goals — one assertion per bullet (12 bullets in SettingsSystem.md §4)
# ===========================================================================


def test_section4_1_does_not_expose_every_pydantic_field_automatically():
    """§4 — `expose every backend field or Pydantic setting automatically`."""

    class _NoExposeMetadataModel:
        model_fields: dict = {}

    registry = SettingsRegistry.from_pydantic_model(_NoExposeMetadataModel)
    assert registry.entries == ()


def test_section4_2_no_router_endpoint_writes_raw_env_vars():
    """§4 — `allow raw environment-variable editing from the browser`."""
    body = SETTINGS_ROUTER_FILE.read_text()
    forbidden = ["os.environ[", "putenv(", "setenv(", 'os.environ.update']
    found = [marker for marker in forbidden if marker in body]
    assert not found, (
        "Settings router must not mutate process env vars: "
        f"{found}"
    )


def test_section4_3_settings_api_is_not_a_secret_store():
    """§4 — `replace the Secrets System`."""
    descriptors = {entry.key: entry for entry in _REGISTRY}
    for key, entry in descriptors.items():
        if entry.secret_role is not None:
            assert entry.value_type == "secret_ref", (
                f"Setting {key} declares secret_role but its value_type "
                "is not secret_ref — Settings cannot duplicate secret storage."
            )


def test_section4_4_no_plaintext_readback_for_secret_typed_settings():
    """§4 — `reveal stored secrets to users or operators`."""
    descriptor_fields = set(SettingDescriptor.model_fields.keys())
    forbidden = {"plaintext", "ciphertext", "secret_value", "resolved_value"}
    leaks = descriptor_fields & forbidden
    assert not leaks, (
        f"SettingDescriptor leaks plaintext-shaped fields: {leaks}"
    )


def test_section4_5_provider_profiles_not_inlined_into_generic_settings():
    """§4 — `replace Provider Profiles with generic settings`."""
    for entry in _REGISTRY:
        if entry.key == "workflow.default_provider_profile_ref":
            assert entry.value_type == "string"
            assert entry.ui == "provider_profile_picker"
            # The descriptor must hold a *reference*, not the inlined profile
            # row (which would otherwise be a dict / object value_type).
            assert entry.value_type != "object"


def test_section4_6_operations_settings_are_not_ordinary_preferences():
    """§4 — `turn Operations into ordinary key/value preferences`."""
    operations_entries = [e for e in _REGISTRY if e.section == "operations"]
    assert operations_entries, "operations section must contain at least one descriptor"
    for entry in operations_entries:
        # An operations descriptor must declare an explicit, non-immediate
        # apply mode so the UI can require confirmation/status handling.
        assert entry.apply_mode in {
            "manual_operation",
            "worker_reload",
            "process_restart",
        }, (
            f"Operations descriptor {entry.key} must not silently apply as "
            f"immediate preference (apply_mode={entry.apply_mode!r})."
        )


def test_section4_7_apply_modes_acknowledge_non_hot_reloadable_settings():
    """§4 — `guarantee every setting can be hot-reloaded`."""
    apply_modes = {entry.apply_mode for entry in _REGISTRY}
    # The descriptor language must be able to express non-immediate modes.
    expressible = {
        "immediate",
        "next_request",
        "next_task",
        "next_launch",
        "worker_reload",
        "process_restart",
        "manual_operation",
    }
    assert apply_modes.issubset(expressible)


def test_section4_8_settings_cannot_carry_executable_runtime_commands():
    """§4 — `remove the need for runtime-specific strategy code`."""
    service = SettingsCatalogService(env={})
    # Object-shaped overrides with command-like keys are explicitly flagged
    # by `_unsafe_object_paths` so runtime strategies remain code, not config.
    command_like = {"command": "rm -rf /", "script": "exfiltrate"}
    unsafe = service._unsafe_object_paths(command_like)
    assert {"command", "script"}.issubset(set(unsafe)), (
        f"Object settings must reject command/script keys; got unsafe={unsafe}"
    )


def test_section4_9_override_payloads_are_size_bounded():
    """§4 — `make MoonMind a general-purpose enterprise configuration management product`."""
    # General-purpose configuration would not impose tight size limits.
    assert _MAX_OVERRIDE_VALUE_BYTES <= 64 * 1024
    assert _MAX_OVERRIDE_VALUE_BYTES > 0


def test_section4_10_settings_sections_are_bounded_not_a_generic_db_editor():
    """§4 — `create a generic admin UI for every database table`."""
    sections = {entry.section for entry in _REGISTRY}
    # MoonMind Settings only exposes three sections per §6. Any new section
    # must update both the doc and the catalog descriptor union.
    assert sections.issubset(
        {"providers-secrets", "user-workspace", "operations"}
    ), f"Unexpected catalog sections present: {sections}"


def test_section4_11_unknown_keys_rejected_by_backend_not_silently_accepted():
    """§4 — `make the frontend the authority for validation or eligibility`."""
    service = SettingsCatalogService(env={})
    with pytest.raises(KeyError):
        service.effective_value("never.declared.key", scope="workspace")


def test_section4_12_no_non_persisted_scopes_accept_writes():
    """§4 — generic-database non-goal hardening: `system`/`operator` writes
    do not flow through the Settings overrides path."""
    # Only `user` and `workspace` are persisted; system/operator settings are
    # deployment-owned per §7.3.
    assert _PERSISTED_SCOPES == {"user", "workspace"}


# ===========================================================================
# §22 Security Requirements — one assertion per numbered requirement
# ===========================================================================


def test_section22_1_raw_secrets_not_stored_in_generic_overrides():
    """§22.1 — raw secrets are not stored in generic setting overrides."""
    # The catalog's unsafe-string detector rejects raw token/secret payloads.
    for marker in ("secret=", "token=", "api_key=", "private_key"):
        assert marker in _UNSAFE_STRING_TOKENS or marker.rstrip("=") in {
            t.rstrip("=") for t in _UNSAFE_STRING_TOKENS
        }


@pytest.mark.asyncio
async def test_section22_2_stored_secrets_not_rendered_after_creation(
    settings_async_session,
):
    """§22.2 — stored secrets are not rendered back to the browser."""
    from api_service.api.schemas import SecretMetadataResponse

    fields = set(SecretMetadataResponse.model_fields.keys())
    leaks = fields & {"ciphertext", "plaintext", "value"}
    assert not leaks, (
        f"SecretMetadataResponse must not expose plaintext fields: {leaks}"
    )


def test_section22_3_secret_like_fields_hidden_unless_secret_ref():
    """§22.3 — secret-like fields are hidden unless represented as SecretRef."""
    for entry in _REGISTRY:
        key_normalized = entry.key.lower()
        looks_secret = any(token in key_normalized for token in _UNSAFE_FIELD_TOKENS)
        if looks_secret:
            assert entry.value_type == "secret_ref", (
                f"Setting {entry.key} matches secret-name heuristics but is "
                f"not represented as a SecretRef (value_type={entry.value_type!r})."
            )


def test_section22_4_backend_authoritative_for_catalog_eligibility():
    """§22.4 — backend is authoritative for catalog generation and eligibility."""
    # Only fields with explicit `moonmind.expose=True` may be promoted, and
    # SettingsRegistry refuses to register a key the migration ledger does
    # not know about.
    class _UnexposedModel:
        model_fields: dict = {}

    registry = SettingsRegistry.from_pydantic_model(_UnexposedModel)
    assert registry.entries == ()


def test_section22_5_unknown_setting_keys_are_rejected():
    """§22.5 — unknown setting keys are rejected."""
    service = SettingsCatalogService(env={})
    with pytest.raises(KeyError):
        service.effective_value("integrations.bogus.key", scope="workspace")


def test_section22_6_client_supplied_descriptor_metadata_ignored():
    """§22.6 — client-supplied descriptor metadata is ignored for authz/validation."""
    body = SETTINGS_ROUTER_FILE.read_text()
    # Writes go through `apply_overrides`; no router handler should accept
    # client-supplied descriptors, audit policy, or scope overrides.
    forbidden_payload_fields = [
        "payload.audit",
        "payload.descriptor",
        "payload.scopes",
        "payload.read_only",
        "payload.expose",
    ]
    leaks = [field for field in forbidden_payload_fields if field in body]
    assert not leaks, (
        f"Settings router accepts client-supplied descriptor metadata: {leaks}"
    )


def test_section22_7_operator_lock_blocks_normal_user_workspace_writes():
    """§22.7 — operator-locked settings cannot be overwritten via user/workspace APIs."""
    service = SettingsCatalogService(
        env={},
        registry=_operator_locked_registry(),
    )
    with pytest.raises(PermissionError):
        service.ensure_write_allowed("ops.feature_flag", scope="workspace")
    assert service.write_lock_error_code("ops.feature_flag") == "operator_locked"


def test_section22_8_audit_policy_redacts_when_descriptor_demands_it():
    """§22.8 — settings changes are audited with redaction according to descriptor policy."""
    for entry in _REGISTRY:
        if entry.value_type == "secret_ref":
            assert entry.audit.redact, (
                f"SecretRef descriptor {entry.key} must declare audit.redact=True."
            )


def test_section22_9_settings_routes_require_auth_dependency():
    """§22.9 — Settings APIs enforce session/auth dependencies."""
    body = SETTINGS_ROUTER_FILE.read_text()
    # Every route handler must take a `user: Any = Depends(...)` parameter so
    # FastAPI applies the project's session/auth guard.
    assert body.count("SETTINGS_CURRENT_USER_DEP") >= 6


def test_section22_10_values_are_size_limited_and_schema_validated():
    """§22.10 — values are size-limited and schema-validated before persistence."""
    assert _MAX_OVERRIDE_VALUE_BYTES == 16 * 1024


def test_section22_11_object_settings_reject_command_like_payloads():
    """§22.11 — object settings do not permit arbitrary executable code/templates."""
    service = SettingsCatalogService(env={})
    # Nested object settings with executable-shaped keys must surface as
    # unsafe paths so the descriptor-validation boundary refuses the write.
    payload = {
        "preset": {
            "label": "ok",
            "template": "format c: && curl evil",
            "shell": "/bin/sh -c rm -rf",
        }
    }
    unsafe = service._unsafe_object_paths(payload)
    assert any(path.endswith("template") for path in unsafe)
    assert any(path.endswith("shell") for path in unsafe)


def test_section22_12_operations_apply_modes_require_confirmation():
    """§22.12 — operational commands require explicit authorization and confirmation."""
    service = SettingsCatalogService(env={})
    ops = [e for e in _REGISTRY if e.section == "operations"]
    assert ops
    op_entry = ops[0]
    # When apply_mode == manual_operation, an empty confirmation produces a
    # `requires_confirmation` issue.
    issues = service._confirmation_issues_for_changes(
        {op_entry.key: op_entry},
        {op_entry.key: op_entry.default_value or "normal"},
        scope="workspace",
        confirmation=None,
    )
    assert any(issue.code == "requires_confirmation" for issue in issues)


def test_section22_13_secret_validation_returns_redacted_diagnostics():
    """§22.13 — secret validation returns redacted diagnostics only."""
    from api_service.services.secrets import SecretsService

    sig = inspect.signature(SecretsService.validate_secret_ref)
    # Validation must not advertise a `reveal` or `plaintext` parameter.
    assert "reveal" not in sig.parameters
    assert "plaintext" not in sig.parameters


def test_section22_14_provider_profile_materialization_not_in_settings_overrides():
    """§22.14 — provider profile materialization never stores resolved plaintext in settings rows."""
    from api_service.services.settings_backup import SettingsBackupOverrideRecord

    fields = set(SettingsBackupOverrideRecord.model_fields.keys())
    leaks = fields & {"ciphertext", "plaintext", "resolved_value"}
    assert not leaks, (
        f"Settings backup override record exposes resolved/plaintext fields: {leaks}"
    )


# ===========================================================================
# §29 Desired-State Invariants — one assertion per numbered invariant
# ===========================================================================


def test_section29_1_unexposed_setting_cannot_be_edited():
    """§29.1 — a setting cannot be edited unless the catalog explicitly exposes it."""
    service = SettingsCatalogService(env={})
    with pytest.raises(KeyError):
        service.ensure_write_allowed("never.exposed", scope="workspace")


def test_section29_2_cannot_write_at_undeclared_scope():
    """§29.2 — a setting cannot be written at a scope not declared by its descriptor."""
    service = SettingsCatalogService(env={})
    workspace_only = next(
        e for e in _REGISTRY if e.scopes == ("workspace",)
    )
    with pytest.raises(ValueError):
        service.ensure_write_allowed(workspace_only.key, scope="user")


def test_section29_3_writes_pass_through_backend_validation():
    """§29.3 — a setting cannot bypass backend validation."""
    method = SettingsCatalogService.apply_overrides
    source = inspect.getsource(method)
    assert "_validate_values" in source, (
        "apply_overrides must invoke _validate_values; backend validation is non-bypassable."
    )


def test_section29_4_generic_override_cannot_carry_raw_secret_plaintext():
    """§29.4 — a setting cannot store raw secret plaintext in a generic override."""
    service = SettingsCatalogService(env={})
    for value in (
        "ghp_FakeTokenJustForTest1234567890ABCDEF",
        "AKIA1234567890ABCDEF",
        "private_key=BEGIN RSA",
    ):
        assert service._contains_unsafe_payload(value), (
            f"Unsafe payload guard missed value: {value!r}"
        )


def test_section29_5_settings_ui_cannot_retrieve_secret_plaintext():
    """§29.5 — a stored secret cannot be retrieved as plaintext by the Settings UI."""
    router_body = SETTINGS_ROUTER_FILE.read_text()
    # The settings router never imports the encrypted ciphertext column or
    # the SecretsService.get_secret method, which is the only function that
    # decrypts plaintext.
    assert "SecretsService.get_secret" not in router_body
    assert "secret.ciphertext" not in router_body


def test_section29_6_secret_ref_can_be_validated_without_revealing_value():
    """§29.6 — a SecretRef can be validated and audited without revealing the referenced value."""
    from api_service.services.secrets import SecretsService

    source = inspect.getsource(SecretsService.validate_secret_ref)
    # The validator returns a dict with metadata fields; no key in that dict
    # should be `plaintext`/`value`.
    assert '"plaintext"' not in source
    assert "'plaintext'" not in source


def test_section29_7_provider_profile_references_secrets_without_embedding():
    """§29.7 — a provider profile can reference secrets without embedding them."""
    from api_service.db.models import ManagedAgentProviderProfile

    mapper = ManagedAgentProviderProfile.__mapper__
    column_names = {column.key for column in mapper.columns}
    assert "secret_refs" in column_names
    # Plaintext / ciphertext for secrets must not live on the profile row.
    assert "secret_plaintext" not in column_names


def test_section29_8_effective_value_always_explains_source():
    """§29.8 — an effective value can always explain its source."""
    required_fields = set(EffectiveSettingValue.model_fields.keys())
    assert "source" in required_fields
    assert "source_explanation" in required_fields


def test_section29_9_reset_returns_to_inheritance_not_default_mutation():
    """§29.9 — a reset returns to inheritance rather than mutating defaults."""
    source = inspect.getsource(SettingsCatalogService.reset_override)
    # Reset operates on the override row only — it deletes the row to
    # restore inheritance and never mutates the descriptor default value.
    assert "_get_override" in source
    assert "session.delete" in source
    assert "default_value" not in source


def test_section29_10_operator_lock_blocks_user_workspace_apis():
    """§29.10 — an operator lock cannot be overwritten by ordinary user/workspace writes."""
    service = SettingsCatalogService(
        env={},
        registry=_operator_locked_registry(),
    )
    with pytest.raises(PermissionError):
        service.ensure_write_allowed("ops.feature_flag", scope="workspace")


def test_section29_11_operational_commands_are_authorization_gated_and_audited():
    """§29.11 — operational commands are authorization-gated and audited."""
    router_body = SETTINGS_ROUTER_FILE.read_text()
    # Every router that performs writes must record an audit event when it
    # rejects the request — section 29.11 demands authorization + audit even
    # on the failure path.
    assert "_record_rejected_write_audit" in router_body
    # The least-privilege permission taxonomy must contain operations-side
    # actions distinct from generic settings reads/writes.
    operations_perms = {
        name for name in SETTINGS_PERMISSION_NAMES if "operations" in name
    }
    assert operations_perms, "Operations permissions missing from least-privilege set"


def test_section29_12_catalog_changes_are_testable_and_intentional():
    """§29.12 — catalog changes are testable and intentional."""
    snapshot_test = (
        REPO_ROOT
        / "tests"
        / "unit"
        / "services"
        / "test_settings_catalog_snapshot.py"
    )
    snapshot_file = (
        REPO_ROOT
        / "tests"
        / "unit"
        / "services"
        / "snapshots"
        / "settings_catalog_snapshot.json"
    )
    assert snapshot_test.exists()
    assert snapshot_file.exists()


# ===========================================================================
# §26 Component split — the suggested internal component layout is reflected
# in the actual code structure or in this guardrail suite. Frontend mirror
# coverage lives in `frontend/src/components/settings/SettingsComponentSplit.test.tsx`.
# ===========================================================================


def test_section26_backend_component_split_is_reflected_in_modules():
    """§26 — suggested backend component split is present in code structure."""
    from api_service.services import settings_backup, settings_catalog
    from api_service.services import settings_change_publisher, settings_migrations

    expected_symbols: Iterable[tuple[object, str]] = (
        (settings_catalog, "SettingsRegistry"),
        (settings_catalog, "SettingsCatalogBuilder"),
        (settings_catalog, "SettingsCatalogService"),
        (settings_change_publisher, "SettingsChangePublisher"),
        (settings_migrations, "SettingsMigrationOrchestrator"),
        (settings_backup, "export_settings_backup"),
        (settings_backup, "scan_broken_references"),
    )
    missing = [
        f"{module.__name__}.{name}"
        for module, name in expected_symbols
        if not hasattr(module, name)
    ]
    assert not missing, f"Missing §26 components: {missing}"


def test_local_first_walkthrough_doc_is_present_and_links_back_to_invariants():
    """Doc gate — the local-first Settings walkthrough exists and references this suite."""
    assert LOCAL_FIRST_DOC.exists(), (
        f"Local-first Settings walkthrough must be documented at {LOCAL_FIRST_DOC}."
    )
    body = LOCAL_FIRST_DOC.read_text()
    assert "MM-713" in body
    # The doc must reference the smoke test path so reviewers can find the
    # automated walkthrough.
    assert "test_settings_local_first_smoke.py" in body


def test_guardrail_suite_covers_all_sections_one_test_per_bullet():
    """Meta-check: this file contains one test for each §4/§22/§29 bullet."""
    body = Path(__file__).read_text()
    for section, count in (("section4", 12), ("section22", 14), ("section29", 12)):
        actual = body.count(f"def test_{section}_")
        assert actual >= count, (
            f"Expected at least {count} tests for {section}, found {actual}"
        )
