"""Unit tests for the shared rollout admission boundary.

Source issue: MoonLadderStudios/MoonMind#3833.

Covers ``moonmind/omnigent/harness_platform/rollout_admission.py``: surface
resolution, pre-promotion passthrough, fail-closed admission with the frozen
rollout triple, default resolution, and low-cardinality telemetry. No Docker,
database, or provider credentials are required.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.rollout_admission import (
    admit_rollout_for_plan,
    build_rollout_combination,
    owner_cohort_from_parameters,
    resolve_rollout_default_for_intention,
    rollout_policy_configured,
    rollout_surface_from_parameters,
)
from moonmind.omnigent.runtime_provider_rollout import (
    ROLLOUT_POLICY_ENV,
    ROLLOUT_POLICY_VERSION,
    AuthoringSurface,
    RolloutCombination,
    RolloutEntry,
    RolloutPolicy,
    RolloutState,
    get_rollout_telemetry,
    reset_rollout_telemetry,
)


def _combination(**overrides) -> RolloutCombination:
    base = {
        "harnessImplementation": "codex-native@1",
        "agentProfileClass": "codex-default@v3#sha256:abc",
        "providerRuntime": "codex_cli",
        "providerClass": "codex-oauth@1",
        "hostClass": "omnigent-codex@1",
        "runtimePack": "image@sha256:def",
        "credentialMaterializer": "codex-oauth-home@1",
        "launchPolicy": "static-connected@1",
        "hostMode": "static-connected",
        "architecture": "linux/amd64",
        "modelConfigClass": "codex",
        "executionRealizer": "generic-omnigent-host@1",
        "supportEvidenceRef": "catalog@1#impl@1",
    }
    # Overrides use exact alias (camelCase) keys, matching RolloutCombination.
    base.update(overrides)
    return RolloutCombination.model_validate(base)


def _policy(state: RolloutState, generation: int = 7) -> RolloutPolicy:
    entry = RolloutEntry.model_validate(
        {
            "combination": _combination().model_dump(by_alias=True, mode="json"),
            "state": state,
            "evidenceFresh": True,
            "launchReady": True,
        }
    )
    return RolloutPolicy.model_validate(
        {
            "schemaVersion": ROLLOUT_POLICY_VERSION,
            "generation": generation,
            "entries": [entry.model_dump(by_alias=True, mode="json")],
        }
    )


def _canary_policy() -> RolloutPolicy:
    entry = RolloutEntry.model_validate(
        {
            "combination": _combination(ownerCohort="team-a").model_dump(
                by_alias=True, mode="json"
            ),
            "state": RolloutState.CANARY,
            "evidenceFresh": True,
            "launchReady": True,
            "canaryCohorts": ["team-a"],
        }
    )
    return RolloutPolicy.model_validate(
        {
            "schemaVersion": ROLLOUT_POLICY_VERSION,
            "generation": 7,
            "entries": [entry.model_dump(by_alias=True, mode="json")],
        }
    )


def test_surface_defaults_to_api_without_hint():
    assert rollout_surface_from_parameters(None) is AuthoringSurface.API
    assert rollout_surface_from_parameters({}) is AuthoringSurface.API
    assert rollout_surface_from_parameters({"omnigent": {}}) is AuthoringSurface.API
    assert (
        rollout_surface_from_parameters({"omnigent": {"authoringSurface": "bogus"}})
        is AuthoringSurface.API
    )


def test_surface_honors_exact_surface_hint():
    assert (
        rollout_surface_from_parameters(
            {"omnigent": {"authoringSurface": "schedule"}}
        )
        is AuthoringSurface.SCHEDULE
    )
    assert (
        rollout_surface_from_parameters({"omnigent": {"authoringSurface": "rerun"}})
        is AuthoringSurface.RERUN
    )


def test_owner_cohort_parsing():
    assert owner_cohort_from_parameters(None) is None
    assert owner_cohort_from_parameters({"omnigent": {"ownerCohort": " team-a "}}) == "team-a"
    assert owner_cohort_from_parameters({"omnigent": {}}) is None


def test_policy_configured_reads_deployment_ref():
    assert rollout_policy_configured(env={}) is False
    assert rollout_policy_configured(env={ROLLOUT_POLICY_ENV: "  "}) is False
    assert (
        rollout_policy_configured(env={ROLLOUT_POLICY_ENV: "/etc/mm/rollout.json"})
        is True
    )


def test_pre_promotion_persists_null_authority(monkeypatch):
    monkeypatch.delenv(ROLLOUT_POLICY_ENV, raising=False)
    assert admit_rollout_for_plan(
        parameters={"omnigent": {"authoringSurface": "workflow_create"}},
        combination=_combination(),
    ) == (None, None, None)


def test_configured_policy_admits_and_freezes_triple():
    reset_rollout_telemetry()
    policy = _policy(RolloutState.PREFERRED)
    version, generation, state = admit_rollout_for_plan(
        parameters={"omnigent": {"authoringSurface": "schedule"}},
        combination=_combination(),
        policy=policy,
    )
    assert version == ROLLOUT_POLICY_VERSION
    assert generation == 7
    assert state == RolloutState.PREFERRED.value
    telemetry = get_rollout_telemetry()
    assert telemetry.get("codex-native|generic|admitted_explicit|schedule") == 1


def test_unknown_combination_denies_without_fallback():
    reset_rollout_telemetry()
    policy = _policy(RolloutState.PREFERRED)
    with pytest.raises(HarnessPlatformError, match="combination_unknown"):
        admit_rollout_for_plan(
            parameters={},
            combination=_combination(hostClass="other@9"),
            policy=policy,
        )
    telemetry = get_rollout_telemetry()
    assert telemetry.get("codex-native|generic|denied|api") == 1


def test_stale_evidence_denies_with_exact_reason():
    entry = RolloutEntry.model_validate(
        {
            "combination": _combination().model_dump(by_alias=True, mode="json"),
            "state": RolloutState.PREFERRED,
            "evidenceFresh": False,
            "evidenceReason": "conformance older than 24h",
            "launchReady": True,
        }
    )
    policy = RolloutPolicy.model_validate(
        {
            "schemaVersion": ROLLOUT_POLICY_VERSION,
            "generation": 3,
            "entries": [entry.model_dump(by_alias=True, mode="json")],
        }
    )
    with pytest.raises(HarnessPlatformError, match="conformance older than 24h"):
        admit_rollout_for_plan(parameters={}, combination=_combination(), policy=policy)


def test_default_resolution_promotes_qualified_target():
    policy = _policy(RolloutState.NEW_WORK_DEFAULT)
    admitted = resolve_rollout_default_for_intention(
        product_intention="codex",
        surface=AuthoringSurface.WORKFLOW_CREATE,
        combination_template=_combination().model_dump(by_alias=True, mode="json"),
        policy=policy,
    )
    assert admitted.canonical_runtime_id == "external/omnigent"
    assert admitted.default_selection is True


def test_default_resolution_denies_without_promotion():
    policy = _policy(RolloutState.EXPLICIT_ONLY)
    # EXPLICIT_ONLY denies the implicit default with its own exact reason;
    # DISABLED denies with no-promoted-default. Both fail closed.
    with pytest.raises(HarnessPlatformError, match="rollout default unavailable"):
        resolve_rollout_default_for_intention(
            product_intention="codex",
            surface="workflow_create",
            combination_template=_combination().model_dump(by_alias=True, mode="json"),
            policy=policy,
        )
    with pytest.raises(HarnessPlatformError, match="no promoted default"):
        resolve_rollout_default_for_intention(
            product_intention="codex",
            surface="workflow_create",
            combination_template=_combination(ownerCohort="team-a").model_dump(
                by_alias=True, mode="json"
            ),
            owner_cohort="team-a",
            policy=_canary_policy(),
        )


def test_combination_builder_uses_exact_refs():
    combination = build_rollout_combination(
        harness_implementation="codex-native@1",
        agent_profile_ref="p@v1#d",
        provider_runtime="codex_cli",
        provider_class="codex-oauth@1",
        host_class_ref="hc@1",
        runtime_pack="img@1",
        credential_materializer="mat@1",
        launch_policy_ref="lp@1",
        host_mode="static-connected",
        architecture="linux/amd64",
        model_config_class="codex",
        execution_realizer="generic-omnigent-host@1",
        support_evidence_ref="cat#impl",
    )
    assert combination.harness_implementation == "codex-native@1"
    assert combination.execution_realizer == "generic-omnigent-host@1"
