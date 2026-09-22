"""MoonLadderStudios/MoonMind#3933: reuse the harness registry.

Focused registration-level coverage: a test harness registered only via
``register_harness_product`` must reach the actual registration/schema
consumers without editing a second product list and without adding a core
lifecycle branch, a required selector, or a readiness registry.

Reuses the landed #4033 Profile-first authoring fixture for the
form-to-admission handoff; adds only the missing reorder/delayed-response
identity preservation.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError


def _isolate_registry(monkeypatch: pytest.MonkeyPatch):
    from moonmind.omnigent.harness_platform import harness_registry

    monkeypatch.setattr(
        harness_registry, "_REGISTRATIONS", dict(harness_registry._REGISTRATIONS)
    )
    monkeypatch.setattr(harness_registry, "_ALIASES", dict(harness_registry._ALIASES))
    return harness_registry


def _register_test_oauth_harness(harness_registry):
    harness_registry.register_harness_product(
        harness_registry.HarnessProductRegistration.model_validate(
            {
                "harnessId": "test-oauth-native",
                "aliases": ["test-oauth"],
                "executionTargetRef": "omnigent-test-oauth@1",
                "hostClassRef": "omnigent-test-oauth@1",
                "materializerRef": "codex-oauth-home@1",
                "authModel": "oauth_volume",
            }
        )
    )


def test_focused_registration_reaches_schema_consumers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry-only test harness flows to schema validators (AC-01/REQ-01)."""
    harness_registry = _isolate_registry(monkeypatch)
    _register_test_oauth_harness(harness_registry)

    assert "test-oauth-native" in harness_registry.approved_harness_ids()
    assert (
        harness_registry.canonical_harness_id("test-oauth") == "test-oauth-native"
    )

    from moonmind.omnigent.execution_profiles import OmnigentExecutionProfile

    profile = OmnigentExecutionProfile.model_validate(
        {
            "profileId": "omnigent-test-oauth",
            "version": 1,
            "displayName": "Test OAuth",
            "endpointRef": "default",
            "agentName": "test-agent",
            "harness": "test-oauth-native",
            "defaultPolicyRef": "codex-on-demand@1",
            "providerRuntime": "codex_cli",
            "captureDefaults": {"required": True, "retentionDays": 30},
            "readiness": {"requiresProviderLaunchReady": True},
        }
    )
    assert profile.harness == "test-oauth-native"

    from moonmind.schemas.agent_runtime_models import OmnigentOAuthHostBinding

    binding = OmnigentOAuthHostBinding.model_validate(
        {
            "bindingRef": "omnigent-oauth:test",
            "providerProfileId": "test-profile",
            "endpointRef": "default",
            "harness": "test-oauth-native",
            "credentialMountRef": {
                "authVolumeRef": {
                    "runtimeId": "codex_cli",
                    "providerId": "openai",
                    "providerProfileId": "test-profile",
                    "volumeRef": "vol-test",
                    "credentialGeneration": 1,
                    "ownerUserId": "profile:test-profile",
                },
                "targetPath": "/home/app/.codex",
                "accessMode": "read_write",
                "runtimeUid": 1000,
                "runtimeGid": 1000,
            },
            "maxHosts": 1,
            "maxSessionsPerHost": 1,
        }
    )
    assert binding.harness == "test-oauth-native"

    from api_service.api.routers.omnigent_catalog import (
        OmnigentCodexCatalogReadiness,
    )

    default_harnesses = (
        OmnigentCodexCatalogReadiness.model_fields["harnesses"].default_factory()
    )
    assert "test-oauth-native" in default_harnesses
    assert "codex-native" in default_harnesses
    assert "claude-native" in default_harnesses


def test_unknown_harness_cannot_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown/incompatible input is rejected before effects (AC-03/REQ-04)."""
    _isolate_registry(monkeypatch)

    from moonmind.omnigent.execution_profiles import OmnigentExecutionProfile

    with pytest.raises(ValidationError):
        OmnigentExecutionProfile.model_validate(
            {
                "profileId": "omnigent-unknown",
                "version": 1,
                "displayName": "Unknown",
                "endpointRef": "default",
                "agentName": "test-agent",
                "harness": "unknown-native",
                "defaultPolicyRef": "codex-on-demand@1",
                "providerRuntime": "codex_cli",
                "captureDefaults": {"required": True, "retentionDays": 30},
                "readiness": {"requiresProviderLaunchReady": True},
            }
        )

    from moonmind.schemas.agent_runtime_models import OmnigentOAuthHostBinding

    with pytest.raises(ValidationError):
        OmnigentOAuthHostBinding.model_validate(
            {
                "bindingRef": "omnigent-oauth:unknown",
                "providerProfileId": "unknown-profile",
                "endpointRef": "default",
                "harness": "unknown-native",
                "credentialMountRef": {
                    "authVolumeRef": {
                        "runtimeId": "codex_cli",
                        "providerId": "openai",
                        "providerProfileId": "unknown-profile",
                        "volumeRef": "vol-unknown",
                        "credentialGeneration": 1,
                        "ownerUserId": "profile:unknown-profile",
                    },
                    "targetPath": "/home/app/.codex",
                    "accessMode": "read_write",
                    "runtimeUid": 1000,
                    "runtimeGid": 1000,
                },
                "maxHosts": 1,
                "maxSessionsPerHost": 1,
            }
        )


def test_oauth_binding_rejects_approved_non_oauth_harness() -> None:
    """opencode-native is registered but never an OAuth host binding (REQ-03)."""
    from moonmind.schemas.agent_runtime_models import OmnigentOAuthHostBinding

    with pytest.raises(ValidationError):
        OmnigentOAuthHostBinding.model_validate(
            {
                "bindingRef": "omnigent-oauth:opencode",
                "providerProfileId": "opencode-profile",
                "endpointRef": "default",
                "harness": "opencode-native",
                "credentialMountRef": {
                    "authVolumeRef": {
                        "runtimeId": "codex_cli",
                        "providerId": "openai",
                        "providerProfileId": "opencode-profile",
                        "volumeRef": "vol-opencode",
                        "credentialGeneration": 1,
                        "ownerUserId": "profile:opencode-profile",
                    },
                    "targetPath": "/home/app/.codex",
                    "accessMode": "read_write",
                    "runtimeUid": 1000,
                    "runtimeGid": 1000,
                },
                "maxHosts": 1,
                "maxSessionsPerHost": 1,
            }
        )


def test_no_additional_required_selector_introduced() -> None:
    """Changed consumers keep ordinary Runtime + one Profile shape (REQ-02)."""
    from moonmind.omnigent.execution_profiles import OmnigentExecutionProfile
    from moonmind.schemas.agent_runtime_models import OmnigentOAuthHostBinding
    from api_service.api.routers.omnigent_catalog import (
        OmnigentCodexCatalogReadiness,
    )

    assert set(OmnigentExecutionProfile.model_fields) == {
        "profile_id",
        "version",
        "display_name",
        "enabled",
        "endpoint_ref",
        "agent_name",
        "harness",
        "default_policy_ref",
        "provider_runtime",
        "provider_auth",
        "capture_defaults",
        "model",
        "reasoning",
        "readiness",
    }
    assert "materializer" not in OmnigentExecutionProfile.model_fields
    assert "host_class" not in OmnigentExecutionProfile.model_fields
    assert "execution_configuration" not in OmnigentExecutionProfile.model_fields

    assert set(OmnigentOAuthHostBinding.model_fields) == {
        "binding_ref",
        "provider_profile_id",
        "endpoint_ref",
        "harness",
        "credential_mount_ref",
        "max_hosts",
        "max_sessions_per_host",
        "static_host_id",
        "host_launch_profile_ref",
        "execution_profile_ref",
        "launch_policy_ref",
        "effective_launch_snapshot",
    }
    assert "readiness" not in OmnigentOAuthHostBinding.model_fields

    assert "readinessRegistry" not in OmnigentCodexCatalogReadiness.model_fields
    assert "profileType" not in OmnigentCodexCatalogReadiness.model_fields


def _load_profile_first_fixture():
    fixture_path = (
        Path(__file__).resolve().parents[3]
        / "frontend/src/runtime/fixtures/profile-first-authoring.json"
    )
    return json.loads(fixture_path.read_text())


def _session_pair(fixture, digest_suffix="9"):
    from types import SimpleNamespace as Row

    reference = fixture["provider"]["execution_selection"]
    profile = Row(
        profile_id=reference["profileId"],
        default_for_runtime=True,
        active_version=reference["version"],
    )
    version = Row(
        version=reference["version"],
        digest=reference["digest"],
        validation_result={"ready": True},
        document=copy.deepcopy(fixture["configuration"]["versions"][0]["document"]),
    )
    provider = Row(
        profile_id=fixture["provider"]["profile_id"],
        runtime_id=fixture["provider"]["runtime_id"],
        provider_id=fixture["provider"]["provider_id"],
        credential_source="secret_ref",
        runtime_materialization_mode="config_bundle",
        execution_configuration=None,
    )
    return provider, [(profile, version)], reference


def test_pinned_profile_choice_wins_over_catalog_order() -> None:
    """Catalog reorder and delayed duplicate rows keep Profile identity (AC-02)."""
    from api_service.services.profile_execution_selection import (
        select_execution_configuration,
    )

    fixture = _load_profile_first_fixture()
    provider, pairs, reference = _session_pair(fixture)

    first = select_execution_configuration(provider, pairs)
    assert first == reference

    # Reordered rows must not replace the pinned choice. A delayed duplicate
    # delivery of an incompatible row must also keep the same identity.
    from types import SimpleNamespace as Row

    incompatible = (
        Row(
            profile_id="profile-stale",
            default_for_runtime=False,
            active_version=9,
        ),
        Row(
            version=9,
            digest="sha256:" + "b" * 64,
            validation_result={"ready": False},
            document=copy.deepcopy(pairs[0][1].document),
        ),
    )
    ordered = [pairs[0], incompatible]
    reordered = [incompatible, pairs[0]]
    first = select_execution_configuration(provider, ordered)
    second = select_execution_configuration(provider, reordered)
    assert first == reference
    assert second == reference
    assert second["harnessId"] == "opencode-native"
    assert second["providerProfileRef"] == fixture["provider"]["profile_id"]


def test_registered_unavailable_vs_busy_states_stay_distinct() -> None:
    """Unavailable config fails; busy capacity never rewrites identity (AC-02)."""
    from fastapi import HTTPException

    from api_service.services.profile_execution_selection import (
        select_execution_configuration,
        validate_execution_configuration_expectation,
    )

    fixture = _load_profile_first_fixture()
    provider, pairs, reference = _session_pair(fixture)

    # Registered-but-unavailable: validation not ready -> 409 required.
    pairs[0][1].validation_result = {"ready": False}
    with pytest.raises(HTTPException) as exc:
        select_execution_configuration(provider, pairs)
    assert exc.value.status_code == 409
    assert (
        exc.value.detail["code"] == "profile_execution_configuration_required"
    )

    # Temporarily-busy capacity is not part of the immutable selection identity:
    # the same pinned selection resolves even when the caller observes busy.
    provider, pairs, reference = _session_pair(fixture)
    busy_provider = copy.copy(provider)
    busy_provider_busy = dict(vars(busy_provider))
    busy_provider_busy["busy"] = True
    from types import SimpleNamespace as Row

    busy_row = Row(**busy_provider_busy)
    resolved = select_execution_configuration(busy_row, pairs)
    assert resolved == reference

    # Stale form identity is rejected without selecting another account.
    stale = dict(reference)
    stale["digest"] = "sha256:" + "0" * 64
    with pytest.raises(HTTPException) as stale_exc:
        validate_execution_configuration_expectation(
            {
                "profileId": stale["profileId"],
                "version": stale["version"],
                "digest": stale["digest"],
            },
            resolved,
        )
    assert stale_exc.value.status_code == 409
    assert (
        stale_exc.value.detail["code"]
        == "profile_execution_configuration_changed"
    )
