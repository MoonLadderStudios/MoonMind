"""Unit tests for the versioned runtime-provider rollout policy.

Source issue: MoonLadderStudios/MoonMind#3833.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.runtime_provider_rollout import (
    ROLLOUT_POLICY_VERSION,
    AdmittedAuthority,
    AuthoringSurface,
    RolloutAdmissionError,
    RolloutCombination,
    RolloutEntry,
    RolloutPolicy,
    RolloutState,
    admit_authoring_selection,
    apply_rollback,
    assert_history_not_reinterpreted,
    canonical_runtime_id_for,
    combination_key,
    empty_rollout_policy,
    get_rollout_telemetry,
    is_compatibility_path,
    load_rollout_policy,
    migration_status_view,
    preserve_or_upgrade_target,
    record_rollout_decision,
    reset_rollout_telemetry,
    resolve_default_target,
    rollout_state_for,
    schedule_revision_for_default_change,
    select_authoring_target,
    target_identity_label,
)


def _combination(**overrides) -> RolloutCombination:
    base = {
        "harnessImplementation": "codex-native@1",
        "agentProfileClass": "codex-default@3",
        "providerRuntime": "codex_cli",
        "providerClass": "codex-oauth@1",
        "hostClass": "omnigent-codex@1",
        "runtimePack": "codex-native-pack@1",
        "credentialMaterializer": "codex-oauth-home@1",
        "launchPolicy": "static-connected@1",
        "hostMode": "static-connected",
        "architecture": "linux/amd64",
        "modelConfigClass": "codex-gpt-5.5@1",
        "executionRealizer": "generic-omnigent-host@1",
        "supportEvidenceRef": "artifact:evidence/codex-generic",
    }
    base.update(overrides)
    return RolloutCombination.model_validate(base)


def _policy(state: RolloutState, generation: int = 7, **entry_overrides) -> RolloutPolicy:
    entry_kwargs: dict = {
        "combination": _combination(),
        "state": state,
        "evidenceFresh": True,
        "launchReady": True,
    }
    entry_kwargs.update(entry_overrides)
    entry = RolloutEntry.model_validate(entry_kwargs)
    return RolloutPolicy.model_validate(
        {
            "schemaVersion": ROLLOUT_POLICY_VERSION,
            "generation": generation,
            "entries": [entry.model_dump(by_alias=True, mode="json")],
        }
    )


def test_combination_key_is_exact_and_stable():
    first = combination_key(_combination())
    second = combination_key(_combination())
    assert first == second
    assert first.startswith("omnigent-rollout-combination:sha256:")
    other = combination_key(_combination(host_class="omnigent-claude@1"))
    assert other != first


def test_unknown_combination_is_disabled_fail_closed():
    policy = empty_rollout_policy()
    state, generation, entry = rollout_state_for(policy, _combination())
    assert state is RolloutState.DISABLED
    assert generation == 1
    assert entry is None
    with pytest.raises(RolloutAdmissionError, match="combination_unknown"):
        select_authoring_target(
            policy=policy,
            surface=AuthoringSurface.WORKFLOW_CREATE,
            combination=_combination(),
            explicit=True,
        )


def test_promoted_default_preselects_omnigent_with_canonical_identity():
    policy = _policy(RolloutState.NEW_WORK_DEFAULT)
    admitted = resolve_default_target(
        policy=policy,
        product_intention="codex",
        surface=AuthoringSurface.WORKFLOW_CREATE,
        combination_template=_combination().model_dump(by_alias=True, mode="json"),
    )
    assert admitted.canonical_runtime_id == "external/omnigent"
    assert admitted.default_selection is True
    assert admitted.rollout_generation == 7
    assert admitted.rollout_state is RolloutState.NEW_WORK_DEFAULT
    assert admitted.target_label == "Codex via generic Omnigent"


def test_no_display_name_or_substring_routing():
    policy = _policy(RolloutState.NEW_WORK_DEFAULT)
    with pytest.raises(RolloutAdmissionError, match="display name or runtime substring"):
        resolve_default_target(
            policy=policy,
            product_intention="Codex (Direct)",
            surface=AuthoringSurface.WORKFLOW_CREATE,
            combination_template=_combination().model_dump(by_alias=True, mode="json"),
        )


def test_missing_or_stale_evidence_fails_closed_with_reason():
    policy = _policy(
        RolloutState.PREFERRED,
        evidenceFresh=False,
        evidenceReason="protected evidence expired 2026-01-01",
    )
    with pytest.raises(RolloutAdmissionError, match="protected evidence expired"):
        select_authoring_target(
            policy=policy,
            surface=AuthoringSurface.PRESET,
            combination=_combination(),
            explicit=True,
        )


def test_explicit_selection_never_falls_back_on_denial():
    policy = _policy(RolloutState.DISABLED)
    with pytest.raises(RolloutAdmissionError, match="rollout_disabled"):
        select_authoring_target(
            policy=policy,
            surface=AuthoringSurface.API,
            combination=_combination(),
            explicit=True,
        )


def test_every_authoring_surface_shares_one_boundary():
    policy = _policy(RolloutState.PREFERRED)
    for surface in AuthoringSurface:
        admitted = select_authoring_target(
            policy=policy,
            surface=surface,
            combination=_combination(),
            explicit=True,
        )
        assert isinstance(admitted, AdmittedAuthority)


def test_compatibility_labels_preserve_truthful_identity():
    assert (
        target_identity_label("codex-native", "generic-omnigent-host@1")
        == "Codex via generic Omnigent"
    )
    assert (
        target_identity_label("codex-native", "codex-profile-bound@1")
        == "Codex via legacy profile-bound Omnigent"
    )
    assert (
        target_identity_label("codex-native", "direct") == "Direct Codex compatibility"
    )
    assert canonical_runtime_id_for("codex-native", "generic-omnigent-host@1") == (
        "external/omnigent"
    )
    assert canonical_runtime_id_for("codex-native", "direct") == "codex_cli"
    assert is_compatibility_path("codex-native", "direct") is True
    assert (
        is_compatibility_path("codex-native", "generic-omnigent-host@1") is False
    )


def test_direct_compatibility_only_rejects_generic():
    policy = _policy(
        RolloutState.DIRECT_COMPATIBILITY_ONLY,
        combination=_combination(execution_realizer="generic-omnigent-host@1"),
    )
    with pytest.raises(RolloutAdmissionError, match="direct_compatibility_only"):
        select_authoring_target(
            policy=policy,
            surface=AuthoringSurface.WORKFLOW_CREATE,
            combination=_combination(execution_realizer="generic-omnigent-host@1"),
            explicit=True,
        )
    direct = _combination(execution_realizer="direct")
    direct_policy = _policy(
        RolloutState.DIRECT_COMPATIBILITY_ONLY, combination=direct
    )
    admitted = select_authoring_target(
        policy=direct_policy,
        surface=AuthoringSurface.WORKFLOW_CREATE,
        combination=direct,
        explicit=True,
    )
    assert admitted.canonical_runtime_id == "codex_cli"


def test_canary_requires_allowlisted_cohort_per_combination():
    policy = _policy(RolloutState.CANARY, canaryCohorts=("owner-alpha",))
    with pytest.raises(RolloutAdmissionError, match="canary_cohort_not_allowlisted"):
        select_authoring_target(
            policy=policy,
            surface=AuthoringSurface.WORKFLOW_CREATE,
            combination=_combination(),
            explicit=True,
            owner_cohort="owner-beta",
        )
    admitted = select_authoring_target(
        policy=policy,
        surface=AuthoringSurface.WORKFLOW_CREATE,
        combination=_combination(),
        explicit=True,
        owner_cohort="owner-alpha",
    )
    assert admitted.rollout_state is RolloutState.CANARY


def test_rerun_preserves_recorded_target_without_silent_replacement():
    policy = _policy(RolloutState.PREFERRED)
    recorded = select_authoring_target(
        policy=policy,
        surface=AuthoringSurface.WORKFLOW_CREATE,
        combination=_combination(),
        explicit=True,
    )
    kept = preserve_or_upgrade_target(
        recorded=recorded,
        policy=policy,
        combination=_combination(),
        upgrade_requested=False,
    )
    assert kept == recorded
    changed = _combination(model_config_class="codex-gpt-5.6@1")
    with pytest.raises(RolloutAdmissionError, match="without_explicit_upgrade"):
        preserve_or_upgrade_target(
            recorded=recorded,
            policy=policy,
            combination=changed,
            upgrade_requested=False,
        )


def test_schedule_default_change_creates_new_revision():
    assert schedule_revision_for_default_change(current_revision=3, default_changed=True) == 4
    assert schedule_revision_for_default_change(current_revision=3, default_changed=False) == 3


def test_rollback_bumps_generation_without_reinterpreting_history():
    policy = _policy(RolloutState.NEW_WORK_DEFAULT)
    recorded = select_authoring_target(
        policy=policy,
        surface=AuthoringSurface.WORKFLOW_CREATE,
        combination=_combination(),
        explicit=True,
    )
    rolled = apply_rollback(
        policy=policy,
        combination_key_value=recorded.rollout_combination_key,
        target_state=RolloutState.EXPLICIT_ONLY,
    )
    assert rolled.generation == policy.generation + 1
    # Recorded authority is unchanged by the rollback.
    assert recorded.rollout_generation == policy.generation
    assert_history_not_reinterpreted(
        recorded={
            "rolloutGeneration": recorded.rollout_generation,
            "rolloutState": recorded.rollout_state.value,
        },
        current_policy=rolled,
    )
    with pytest.raises(RolloutAdmissionError, match="must not promote"):
        apply_rollback(
            policy=policy,
            combination_key_value=recorded.rollout_combination_key,
            target_state=RolloutState.NEW_WORK_DEFAULT,
        )


def test_migration_status_view_has_no_secret_material():
    policy = _policy(RolloutState.PREFERRED)
    view = migration_status_view(policy=policy)
    assert len(view) == 1
    row = view[0]
    assert row["rolloutState"] == "preferred"
    assert row["isDefault"] is True
    assert row["executionRealizer"] == "generic-omnigent-host@1"
    blob = str(row)
    for forbidden in ("token", "secret", "session", "credential-body"):
        assert forbidden not in blob.lower()


def test_telemetry_labels_stay_low_cardinality():
    reset_rollout_telemetry()
    record_rollout_decision(
        harness="codex-native",
        realizer_ref="generic-omnigent-host@1",
        decision="admitted_default",
        surface=AuthoringSurface.WORKFLOW_CREATE,
    )
    record_rollout_decision(
        harness="codex-native",
        realizer_ref="generic-omnigent-host@1",
        decision="admitted_default",
        surface="workflow-evil-user-123",
    )
    counters = get_rollout_telemetry()
    assert counters["codex-native|generic|admitted_default|workflow_create"] == 1
    # Unknown surfaces collapse to a bounded bucket; no per-user labels exist.
    assert counters["codex-native|generic|admitted_default|api"] == 1
    reset_rollout_telemetry()


def test_load_rollout_policy_defaults_fail_closed(tmp_path):
    assert empty_rollout_policy().entries == ()
    with pytest.raises(RolloutAdmissionError, match="unavailable"):
        load_rollout_policy(path=str(tmp_path / "missing.json"))
    doc = tmp_path / "rollout.json"
    doc.write_text('{"schemaVersion": "wrong"}', encoding="utf-8")
    with pytest.raises(RolloutAdmissionError, match="invalid"):
        load_rollout_policy(path=str(doc))


def test_plan_persists_rollout_generation_atomically():
    from moonmind.omnigent.harness_platform.execution_plan import (
        OmnigentExecutionPlanPayload,
    )

    payload = OmnigentExecutionPlanPayload.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-execution-plan-payload.v1",
            "endpointRef": "endpoint@1",
            "agentProfileSnapshotRef": "profile@1",
            "harnessCatalogRef": "catalog@1",
            "harnessId": "opencode-native",
            "harnessImplementationRef": "opencode-native@1",
            "agentSource": {"kind": "stock"},
            "credentialBindingSetRef": "bindings@1",
            "credentialBindings": {},
            "hostClassRef": "omnigent-opencode@1",
            "launchPolicyRef": "policy@1",
            "executionRealizerRef": "generic-omnigent-host@1",
            "model": {
                "qualifiedId": "m",
                "modelConfigDigest": "sha256:" + "0" * 64,
            },
            "resolvedSkills": {},
            "classAdmissionDecision": {},
            "runtimeValidationRequirements": [],
            "workspaceIntentRef": "workspace@1",
            "policySnapshotRef": "policy-snap@1",
            "supportCombinationKey": "key",
            "rolloutPolicyVersion": ROLLOUT_POLICY_VERSION,
            "rolloutGeneration": 7,
            "rolloutState": "new_work_default",
        }
    )
    assert payload.rollout_generation == 7
    with pytest.raises(Exception, match="atomically|rollout"):
        OmnigentExecutionPlanPayload.model_validate(
            {
                **payload.model_dump(by_alias=True, mode="json"),
                "rolloutGeneration": None,
            }
        )


def test_admit_authoring_selection_rejects_unknown_surface():
    policy = _policy(RolloutState.PREFERRED)
    with pytest.raises(Exception):
        admit_authoring_selection(
            policy=policy,
            selection={
                "surface": "not-a-surface",
                "combination": _combination().model_dump(by_alias=True, mode="json"),
                "explicit": True,
            },  # type: ignore[arg-type]
        )
