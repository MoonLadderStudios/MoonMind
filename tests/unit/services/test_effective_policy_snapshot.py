"""Admission-snapshot binding for execution-sensitive effective settings.

Source issue: MoonLadderStudios/MoonMind#3941 (acceptance REQ-04).

Settings edits affect newly admitted work or an explicitly supported
operation, never the replay of an old history. These tests pin the immutable
admission snapshot contract in ``moonmind/config/effective_policy_snapshot``:

* binding freezes every execution-sensitive key with its provenance;
* a later UI save / reload / restart changes current effective state but
  leaves the recorded snapshot untouched (active executions keep running
  under the recorded policy);
* the snapshot names a truthful change class per key (live, next admission,
  worker reload, process restart, manual operation, credential lifecycle);
* diagnostics projections never carry secret values.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from api_service.services.settings_catalog import SettingsCatalogService
from moonmind.config.effective_policy_snapshot import (
    EXECUTION_SENSITIVE_KEYS,
    REDACTED_DIAGNOSTIC_KEYS,
    bind_effective_policy_snapshot,
    snapshot_diff,
    snapshot_matches,
)


def _stub_settings() -> SimpleNamespace:
    return SimpleNamespace(
        workflow=SimpleNamespace(
            default_runtime="codex_cli",
            default_publish_mode="pr",
            moonspec_environment_blocked_publish_action="fail",
            skill_policy_mode="permissive",
            skills_canary_percent=100,
            default_provider_profile_ref=None,
        )
    )


def _response(env: dict[str, str] | None = None):
    service = SettingsCatalogService(settings=_stub_settings(), env=env or {})
    return service.effective_values(scope="workspace")


def test_binding_freezes_every_execution_sensitive_key_with_provenance():
    snapshot = bind_effective_policy_snapshot(_response())

    assert {entry.key for entry in snapshot.entries} == set(
        EXECUTION_SENSITIVE_KEYS
    )
    assert snapshot.scope == "workspace"
    assert snapshot.schema_version == 1
    assert snapshot.policy_hash
    by_key = {entry.key: entry for entry in snapshot.entries}
    assert by_key["skills.canary_percent"].value == 100
    assert by_key["skills.canary_percent"].source in {
        "default",
        "config_file",
        "environment",
    }
    # A second bind of identical state yields the same policy hash.
    assert (
        bind_effective_policy_snapshot(_response()).policy_hash
        == snapshot.policy_hash
    )


def test_active_execution_retains_recorded_policy_after_a_ui_save():
    before = _response()
    snapshot = bind_effective_policy_snapshot(before)

    after = _response(env={"WORKFLOW_SKILLS_CANARY_PERCENT": "25"})
    assert snapshot_matches(snapshot, after) is False
    assert snapshot_diff(snapshot, after) == ["skills.canary_percent"]

    # The recorded snapshot is untouched by the later save.
    by_key = {entry.key: entry for entry in snapshot.entries}
    assert by_key["skills.canary_percent"].value == 100
    assert (
        bind_effective_policy_snapshot(after).policy_hash != snapshot.policy_hash
    )


def test_binding_is_partial_snapshot_proof():
    response = _response()
    redacted = SimpleNamespace(
        scope=response.scope,
        values={
            key: value
            for key, value in response.values.items()
            if key != "skills.canary_percent"
        },
    )
    with pytest.raises(ValueError, match="skills.canary_percent"):
        bind_effective_policy_snapshot(redacted)


def test_change_classes_are_truthful_per_key():
    snapshot = bind_effective_policy_snapshot(_response())
    by_key = {entry.key: entry for entry in snapshot.entries}
    assert by_key["workflow.default_runtime"].change_class == "next_admission"
    assert by_key["workflow.default_publish_mode"].change_class == "next_admission"
    assert (
        by_key[
            "workflow.moonspec_environment_blocked_publish_action"
        ].change_class
        == "next_admission"
    )
    assert by_key["skills.policy_mode"].change_class == "worker_reload"
    assert by_key["skills.canary_percent"].change_class == "next_admission"
    assert (
        by_key["integrations.github.token_ref"].change_class
        == "credential_lifecycle"
    )
    assert (
        by_key["workflow.default_provider_profile_ref"].change_class
        == "credential_lifecycle"
    )
    assert by_key["workflow.operation_mode"].change_class == "manual_operation"
    assert all(entry.requires_drain is False for entry in snapshot.entries)


def test_snapshot_change_classes_match_catalog_apply_modes():
    service = SettingsCatalogService(settings=_stub_settings(), env={})
    catalog = service.catalog()
    descriptors = {
        descriptor.key: descriptor
        for descriptors in catalog.categories.values()
        for descriptor in descriptors
    }
    # worker_reload classification is only truthful if the catalog actually
    # requires a reload for that key.
    assert descriptors["skills.policy_mode"].requires_reload is True
    assert descriptors["skills.policy_mode"].apply_mode == "worker_reload"
    assert descriptors["workflow.default_runtime"].apply_mode == "next_workflow"
    assert descriptors["workflow.operation_mode"].apply_mode == "manual_operation"


def test_snapshot_is_immutable():
    snapshot = bind_effective_policy_snapshot(_response())
    with pytest.raises(ValidationError):
        snapshot.scope = "user"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        snapshot.entries[0].value = "tampered"  # type: ignore[misc]


def test_diagnostic_projection_excludes_secret_values():
    response = _response(env={"MOONMIND_GITHUB_TOKEN_REF": "db://test-secret-xyz"})
    snapshot = bind_effective_policy_snapshot(response)
    assert "integrations.github.token_ref" in REDACTED_DIAGNOSTIC_KEYS

    projected = snapshot.to_diagnostic_dict()
    serialized = str(projected)
    assert "test-secret-xyz" not in serialized
    token_entry = projected["entries"]["integrations.github.token_ref"]
    assert token_entry["present"] is True
    assert token_entry["source"] == "environment"
    # Non-secret execution policy stays visible for operability.
    by_key = {entry.key: entry for entry in snapshot.entries}
    assert (
        projected["entries"]["skills.canary_percent"]["value"]
        == by_key["skills.canary_percent"].value
    )
