"""Versioned runtime-provider rollout policy.

Source issue: MoonLadderStudios/MoonMind#3833.
"""

from __future__ import annotations

import inspect
import json

import pytest

from moonmind.omnigent import runtime_provider_rollout as rollout
from moonmind.omnigent.runtime_provider_rollout import (
    RUNTIME_PROVIDER_ROLLBACK_ENV,
    RUNTIME_PROVIDER_ROLLOUT_ENV,
    RUNTIME_PROVIDER_ROLLOUT_POLICY_VERSION,
    RolloutReason,
    RolloutSelectionContext,
    RolloutState,
    RuntimeProviderCombination,
    RuntimeProviderPathClass,
    RuntimeProviderRollbackControl,
    RuntimeProviderRolloutPolicy,
    compute_runtime_provider_combination_key,
    default_runtime_provider_rollout_policy,
    freeze_rollout_record,
    load_runtime_provider_rollout_policy,
    native_interactive_chat_allowed,
    parse_rollback_controls,
    resolve_rollout_decision,
)

_CODEX_GATE = "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED"
_CLAUDE_GATE = "MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED"
_IMPL_REF = "omnigent-harness-implementation:sha256:" + "0" * 64


def _combination(**overrides) -> RuntimeProviderCombination:
    payload = {
        "harnessId": "codex-native",
        "harnessImplementationRef": _IMPL_REF,
        "agentProfileCompatibilityClass": "moonmind.omnigent-agent-profile.v2",
        "providerRuntimeId": "codex_cli",
        "providerClass": "codex-openai",
        "hostClassRef": "omnigent-codex@1",
        "runtimePackRef": "codex-native-pack@1",
        "credentialMaterializerRef": "codex-oauth-home@1",
        "launchPolicyRef": "omnigent-on-demand@2",
        "hostMode": "on-demand",
        "architecture": "linux/amd64",
        "modelConfigurationClass": "sha256:" + "1" * 64,
        "executionRealizerRef": "generic-omnigent-host@1",
        "pathClass": "generic_omnigent",
    }
    payload.update(overrides)
    return RuntimeProviderCombination.model_validate(payload)


def _policy(env: dict[str, str] | None = None) -> RuntimeProviderRolloutPolicy:
    return default_runtime_provider_rollout_policy(env=env or {})


# --- Exact dimensions -------------------------------------------------------


def test_combination_key_changes_with_every_exact_dimension():
    baseline = _combination()
    baseline_key = baseline.key()
    for dimension, replacement in (
        (
            "harnessImplementationRef",
            "omnigent-harness-implementation:sha256:" + "2" * 64,
        ),
        ("agentProfileCompatibilityClass", "moonmind.omnigent-agent-profile.v3"),
        ("providerRuntimeId", "claude_code"),
        ("providerClass", "codex-openai-alt"),
        ("hostClassRef", "omnigent-codex@2"),
        ("runtimePackRef", "codex-native-pack@2"),
        ("credentialMaterializerRef", "codex-oauth-home@2"),
        ("launchPolicyRef", "codex-static@1"),
        ("hostMode", "static-connected"),
        ("architecture", "linux/arm64"),
        ("modelConfigurationClass", "sha256:" + "3" * 64),
        ("executionRealizerRef", "codex-profile-bound@1"),
    ):
        assert _combination(**{dimension: replacement}).key() != baseline_key, dimension
    assert compute_runtime_provider_combination_key(baseline) == baseline_key


def test_combination_rejects_blank_and_wildcard_dimensions():
    with pytest.raises(Exception):
        _combination(hostClassRef="")
    with pytest.raises(Exception):
        _combination(hostClassRef="*")


def test_resolution_never_routes_by_display_name_or_substring():
    source = inspect.getsource(rollout.RolloutRule.matches)
    for forbidden in ("startswith", "endswith", " in expected", "lower()", "label"):
        assert forbidden not in source
    # A label change cannot move a combination between rules.
    policy = _policy({_CODEX_GATE: "true"})
    renamed = policy.model_copy(
        update={
            "rules": tuple(
                rule.model_copy(update={"label": "Totally Different Name"})
                for rule in policy.rules
            )
        }
    )
    assert (
        resolve_rollout_decision(policy=renamed, combination=_combination()).state
        is RolloutState.new_work_default
    )


def test_most_specific_rule_wins_deterministically():
    policy = _policy({_CODEX_GATE: "true"})
    decision = resolve_rollout_decision(policy=policy, combination=_combination())
    assert decision.target_id == "codex.generic-omnigent"
    legacy = resolve_rollout_decision(
        policy=policy,
        combination=_combination(
            executionRealizerRef="codex-profile-bound@1",
            pathClass="legacy_profile_bound_omnigent",
        ),
    )
    assert legacy.target_id == "codex.legacy-profile-bound-omnigent"
    assert legacy.state is RolloutState.retired_for_new_work


# --- Fail-closed default selection ------------------------------------------


def test_unregistered_combination_is_explicit_only_never_promoted():
    decision = resolve_rollout_decision(
        policy=_policy(),
        combination=_combination(
            harnessId="brand-new-native",
            runtimePackRef="brand-new-pack@1",
            providerRuntimeId="brand_new",
        ),
    )
    assert decision.state is RolloutState.explicit_only
    assert decision.reason_code is RolloutReason.combination_not_registered
    assert decision.default_eligible is False
    assert decision.explicit_selection_allowed is True


def test_codex_generic_is_disabled_until_the_deployment_qualifies_it():
    denied = resolve_rollout_decision(
        policy=_policy(), combination=_combination()
    )
    assert denied.state is RolloutState.disabled
    assert denied.explicit_selection_allowed is False
    assert RolloutReason.rollout_disabled in denied.unavailable_reasons

    promoted = resolve_rollout_decision(
        policy=_policy({_CODEX_GATE: "true"}), combination=_combination()
    )
    assert promoted.state is RolloutState.new_work_default
    assert promoted.default_eligible is True


def test_claude_and_opencode_rows_are_independent():
    policy = _policy({_CLAUDE_GATE: "true"})
    claude = resolve_rollout_decision(
        policy=policy,
        combination=_combination(
            harnessId="claude-native",
            runtimePackRef="claude-native-pack@1",
            hostClassRef="omnigent-claude@1",
            credentialMaterializerRef="claude-oauth-home@1",
            providerRuntimeId="claude_code",
        ),
    )
    assert claude.state is RolloutState.new_work_default
    # Promoting Claude does not promote Codex.
    codex = resolve_rollout_decision(policy=policy, combination=_combination())
    assert codex.state is RolloutState.disabled
    opencode = resolve_rollout_decision(
        policy=policy,
        combination=_combination(
            harnessId="opencode-native",
            runtimePackRef="opencode-native-pack@1",
            hostClassRef="omnigent-opencode@1",
            credentialMaterializerRef="opencode-auth-json@1",
            providerRuntimeId="opencode",
        ),
    )
    assert opencode.state is RolloutState.new_work_default


@pytest.mark.parametrize(
    "context_kwargs,expected_reason",
    [
        ({"launchReady": False}, RolloutReason.target_not_launch_ready),
        ({"modelQualified": False}, RolloutReason.model_not_qualified),
        (
            {"architectureSupported": False},
            RolloutReason.architecture_unsupported,
        ),
        ({"hostModeAvailable": False}, RolloutReason.host_mode_unavailable),
        (
            {"providerProfileAvailable": False},
            RolloutReason.provider_profile_unavailable,
        ),
    ],
)
def test_promoted_state_fails_closed_on_missing_readiness(
    context_kwargs, expected_reason
):
    policy = _policy({_CODEX_GATE: "true"})
    decision = resolve_rollout_decision(
        policy=policy,
        combination=_combination(),
        context=RolloutSelectionContext.model_validate(context_kwargs),
    )
    assert decision.default_eligible is False
    assert decision.state is RolloutState.explicit_only
    assert expected_reason in decision.unavailable_reasons


def test_missing_and_stale_support_evidence_deny_promotion():
    policy = _policy({_CODEX_GATE: "true"})
    evidence_required = policy.model_copy(
        update={
            "rules": tuple(
                rule.model_copy(
                    update={
                        "requires_support_evidence": True,
                        "evidence_max_age_seconds": 3600,
                    }
                )
                for rule in policy.rules
            )
        }
    )
    missing = resolve_rollout_decision(
        policy=evidence_required, combination=_combination()
    )
    assert RolloutReason.support_evidence_missing in missing.unavailable_reasons

    stale = resolve_rollout_decision(
        policy=evidence_required,
        combination=_combination(),
        context=RolloutSelectionContext(
            supportEvidenceRef="artifact:art_1", supportEvidenceAgeSeconds=7200
        ),
    )
    assert RolloutReason.support_evidence_stale in stale.unavailable_reasons

    expired = resolve_rollout_decision(
        policy=evidence_required,
        combination=_combination(),
        context=RolloutSelectionContext(
            supportEvidenceRef="artifact:art_1", supportEvidenceExpired=True
        ),
    )
    assert RolloutReason.support_evidence_stale in expired.unavailable_reasons

    fresh = resolve_rollout_decision(
        policy=evidence_required,
        combination=_combination(),
        context=RolloutSelectionContext(
            supportEvidenceRef="artifact:art_1", supportEvidenceAgeSeconds=60
        ),
    )
    assert fresh.state is RolloutState.new_work_default


# --- Canary allowlists ------------------------------------------------------


def _canary_policy(**cohort) -> RuntimeProviderRolloutPolicy:
    policy = _policy({_CODEX_GATE: "true"})
    rules = []
    for rule in policy.rules:
        if rule.target_id == "codex.generic-omnigent":
            rule = rule.model_copy(
                update={
                    "state": RolloutState.canary,
                    "canary": rollout.RolloutCohort.model_validate(cohort),
                }
            )
        rules.append(rule)
    return policy.model_copy(update={"rules": tuple(rules)})


@pytest.mark.parametrize(
    "cohort,context,admitted",
    [
        ({"ownerCohorts": ["platform"]}, {"ownerCohorts": ["platform"]}, True),
        ({"ownerCohorts": ["platform"]}, {"ownerCohorts": ["other"]}, False),
        ({"ownerCohorts": ["platform"]}, {}, False),
        (
            {"agentProfileRefs": ["codex-default@3"]},
            {"agentProfileRef": "codex-default@3"},
            True,
        ),
        (
            {"agentProfileRefs": ["codex-default@3"]},
            {"agentProfileRef": "codex-default@2"},
            False,
        ),
        (
            {"providerProfileRefs": ["pp-1"]},
            {"providerProfileRef": "pp-1"},
            True,
        ),
        (
            {"hostClassRefs": ["omnigent-codex@1"]},
            {"hostClassRef": "omnigent-codex@1"},
            True,
        ),
        (
            {"hostClassRefs": ["omnigent-codex@1"]},
            {"hostClassRef": "omnigent-codex@2"},
            False,
        ),
        (
            {"launchPolicyRefs": ["codex-static@1"]},
            {"launchPolicyRef": "codex-static@1"},
            True,
        ),
        ({"models": ["openai/gpt-5.5"]}, {"model": "openai/gpt-5.5"}, True),
        ({"models": ["openai/gpt-5.5"]}, {"model": "openai/gpt-5.3"}, False),
        ({"architectures": ["linux/amd64"]}, {"architecture": "linux/amd64"}, True),
        ({"architectures": ["linux/amd64"]}, {"architecture": "linux/arm64"}, False),
        ({"hostModes": ["on-demand"]}, {"hostMode": "on-demand"}, True),
        ({"hostModes": ["on-demand"]}, {"hostMode": "static-connected"}, False),
        (
            {"harnessImplementationRefs": [_IMPL_REF]},
            {"harnessImplementationRef": _IMPL_REF},
            True,
        ),
    ],
)
def test_canary_allowlists_are_exact_per_dimension(cohort, context, admitted):
    decision = resolve_rollout_decision(
        policy=_canary_policy(**cohort),
        combination=_combination(),
        context=RolloutSelectionContext.model_validate(context),
    )
    if admitted:
        assert decision.state is RolloutState.canary
        assert decision.explicit_selection_allowed is True
    else:
        assert decision.state is RolloutState.explicit_only
        assert (
            RolloutReason.rollout_canary_cohort_excluded
            in decision.unavailable_reasons
        )


# --- Rollback ---------------------------------------------------------------


def test_rollback_controls_are_independent_per_combination():
    policy = _policy(
        {
            _CODEX_GATE: "true",
            _CLAUDE_GATE: "true",
            RUNTIME_PROVIDER_ROLLBACK_ENV: "stop_new_generic_codex_admission",
        }
    )
    codex = resolve_rollout_decision(policy=policy, combination=_combination())
    assert codex.state is RolloutState.disabled
    assert (
        RuntimeProviderRollbackControl.stop_new_generic_codex_admission
        in codex.rollback_controls_applied
    )
    claude = resolve_rollout_decision(
        policy=policy,
        combination=_combination(
            harnessId="claude-native",
            runtimePackRef="claude-native-pack@1",
            hostClassRef="omnigent-claude@1",
            credentialMaterializerRef="claude-oauth-home@1",
            providerRuntimeId="claude_code",
        ),
    )
    assert claude.state is RolloutState.new_work_default
    assert claude.rollback_controls_applied == ()


def test_stop_all_new_omnigent_work_never_substitutes_a_direct_runtime():
    policy = _policy(
        {
            _CODEX_GATE: "true",
            RUNTIME_PROVIDER_ROLLBACK_ENV: "stop_all_new_omnigent_work",
        }
    )
    generic = resolve_rollout_decision(policy=policy, combination=_combination())
    assert generic.state is RolloutState.disabled
    assert (
        RolloutReason.rollback_all_omnigent_stopped in generic.unavailable_reasons
    )
    direct = resolve_rollout_decision(
        policy=policy,
        combination=_combination(
            harnessId="not-applicable",
            harnessImplementationRef="not-applicable",
            runtimePackRef="not-applicable",
            credentialMaterializerRef="not-applicable",
            executionRealizerRef="not-applicable",
            pathClass="direct_compatibility",
        ),
    )
    # Direct stays exactly a compatibility path; it is never promoted to cover
    # the stopped Omnigent work.
    assert direct.state is RolloutState.direct_compatibility_only
    assert direct.default_eligible is False


def test_restoring_a_legacy_default_is_not_reported_as_unavailability():
    """Demoting a generic row changes preference, not availability."""

    policy = _policy(
        {
            _CODEX_GATE: "true",
            RUNTIME_PROVIDER_ROLLBACK_ENV: "restore_legacy_or_direct_default",
        }
    )
    generic = resolve_rollout_decision(policy=policy, combination=_combination())
    assert generic.state is RolloutState.explicit_only
    assert generic.explicit_selection_allowed is True
    assert generic.unavailable_reasons == ()


def test_restore_legacy_default_only_promotes_a_supported_compatibility_row():
    policy = _policy(
        {
            _CODEX_GATE: "true",
            RUNTIME_PROVIDER_ROLLBACK_ENV: "restore_legacy_or_direct_default",
        }
    )
    generic = resolve_rollout_decision(policy=policy, combination=_combination())
    assert generic.state is RolloutState.explicit_only
    assert (
        RolloutReason.rollback_legacy_default_restored is generic.reason_code
    )
    direct = resolve_rollout_decision(
        policy=policy,
        combination=_combination(
            harnessId="not-applicable",
            harnessImplementationRef="not-applicable",
            runtimePackRef="not-applicable",
            credentialMaterializerRef="not-applicable",
            executionRealizerRef="not-applicable",
            pathClass="direct_compatibility",
        ),
    )
    assert direct.state is RolloutState.new_work_default

    unsupported = policy.model_copy(
        update={
            "rules": tuple(
                rule.model_copy(update={"legacy_default_restorable": False})
                for rule in policy.rules
            )
        }
    )
    still_compat = resolve_rollout_decision(
        policy=unsupported,
        combination=_combination(
            harnessId="not-applicable",
            harnessImplementationRef="not-applicable",
            runtimePackRef="not-applicable",
            credentialMaterializerRef="not-applicable",
            executionRealizerRef="not-applicable",
            pathClass="direct_compatibility",
        ),
    )
    assert still_compat.state is RolloutState.direct_compatibility_only


def test_native_interactive_chat_control_preserves_historical_reads():
    allowed = _policy({})
    assert native_interactive_chat_allowed(allowed) is True
    disabled = _policy(
        {RUNTIME_PROVIDER_ROLLBACK_ENV: "disable_native_interactive_chat"}
    )
    assert native_interactive_chat_allowed(disabled) is False
    # The control changes new interactive work only; it is not a rollout state
    # for any combination, so recorded rows keep executing.
    decision = resolve_rollout_decision(
        policy=disabled, combination=_combination()
    )
    assert decision.rollback_controls_applied == ()


def test_unknown_rollback_control_fails_fast():
    with pytest.raises(ValueError, match="rollback control"):
        parse_rollback_controls("stop_everything_immediately")
    assert parse_rollback_controls(None) == ()
    assert parse_rollback_controls("stop-all-new-omnigent-work") == (
        RuntimeProviderRollbackControl.stop_all_new_omnigent_work,
    )


# --- Frozen authority -------------------------------------------------------


def test_frozen_record_is_compact_versioned_and_identity_free():
    decision = resolve_rollout_decision(
        policy=_policy({_CODEX_GATE: "true"}), combination=_combination()
    )
    record = freeze_rollout_record(decision)
    assert record == {
        "policyVersion": RUNTIME_PROVIDER_ROLLOUT_POLICY_VERSION,
        "policyGeneration": 1,
        "combinationKey": decision.combination_key,
        "targetId": "codex.generic-omnigent",
        "pathClass": "generic_omnigent",
        "state": "new_work_default",
        "ruleGeneration": 1,
        "reasonCode": "rollout_new_work_default",
    }
    serialized = json.dumps(record)
    for forbidden in ("token", "secret", "/home/", "providerSession"):
        assert forbidden not in serialized


def test_changing_the_live_policy_does_not_reinterpret_a_frozen_record():
    promoted = resolve_rollout_decision(
        policy=_policy({_CODEX_GATE: "true"}), combination=_combination()
    )
    frozen = freeze_rollout_record(promoted)
    rolled_back = resolve_rollout_decision(
        policy=_policy({}), combination=_combination()
    )
    assert rolled_back.state is RolloutState.disabled
    # The already-frozen record is unchanged by the newer policy.
    assert frozen["state"] == "new_work_default"
    assert frozen["combinationKey"] == rolled_back.combination_key


# --- Deployment-owned configuration ----------------------------------------


def test_deployment_policy_document_overrides_the_built_in_rows():
    document = {
        "policyVersion": RUNTIME_PROVIDER_ROLLOUT_POLICY_VERSION,
        "generation": 7,
        "rules": [
            {
                "targetId": "codex.generic-omnigent",
                "label": "Codex via generic Omnigent",
                "selector": {
                    "harness_id": "codex-native",
                    "execution_realizer_ref": "generic-omnigent-host@1",
                    "path_class": "generic_omnigent",
                },
                "state": "preferred",
                "generation": 4,
            }
        ],
    }
    policy = load_runtime_provider_rollout_policy(
        env={RUNTIME_PROVIDER_ROLLOUT_ENV: json.dumps(document)}
    )
    assert policy.generation == 7
    # A deployment-authored rule requires support evidence by default, so an
    # authored promotion still fails closed without it.
    without_evidence = resolve_rollout_decision(
        policy=policy, combination=_combination()
    )
    assert without_evidence.state is RolloutState.explicit_only
    assert (
        RolloutReason.support_evidence_missing
        in without_evidence.unavailable_reasons
    )

    decision = resolve_rollout_decision(
        policy=policy,
        combination=_combination(),
        context=RolloutSelectionContext(supportEvidenceRef="artifact:art_1"),
    )
    assert decision.state is RolloutState.preferred
    assert decision.rule_generation == 4
    assert decision.policy_generation == 7


def test_invalid_deployment_policy_fails_fast_instead_of_reverting():
    with pytest.raises(ValueError, match="not valid JSON"):
        load_runtime_provider_rollout_policy(
            env={RUNTIME_PROVIDER_ROLLOUT_ENV: "{not json"}
        )
    with pytest.raises(ValueError, match="expected an object"):
        load_runtime_provider_rollout_policy(
            env={RUNTIME_PROVIDER_ROLLOUT_ENV: "[]"}
        )
    with pytest.raises(Exception):
        load_runtime_provider_rollout_policy(
            env={
                RUNTIME_PROVIDER_ROLLOUT_ENV: json.dumps(
                    {"policyVersion": "moonmind.something-else/v9", "rules": []}
                )
            }
        )


def test_policy_rejects_unknown_selector_dimensions_and_duplicate_targets():
    with pytest.raises(Exception, match="unknown selector dimensions"):
        rollout.RolloutRule.model_validate(
            {
                "targetId": "bogus",
                "label": "Bogus",
                "selector": {"display_name": "Codex"},
                "state": "preferred",
                "generation": 1,
            }
        )
    rule = {
        "targetId": "duplicate",
        "label": "Duplicate",
        "selector": {"harness_id": "codex-native"},
        "state": "preferred",
        "generation": 1,
    }
    with pytest.raises(Exception, match="duplicate rollout targetId"):
        RuntimeProviderRolloutPolicy.model_validate(
            {
                "policyVersion": RUNTIME_PROVIDER_ROLLOUT_POLICY_VERSION,
                "rules": [rule, dict(rule)],
            }
        )


def test_built_in_policy_registers_every_required_target_identity():
    policy = _policy({_CODEX_GATE: "true", _CLAUDE_GATE: "true"})
    assert {rule.target_id for rule in policy.rules} == {
        "codex.generic-omnigent",
        "codex.legacy-profile-bound-omnigent",
        "claude.generic-omnigent",
        "claude.direct",
        "codex.direct",
        "opencode.generic-omnigent",
    }
    generic = policy.rules_for(path_class=RuntimeProviderPathClass.generic_omnigent)
    assert {rule.harness_id for rule in generic} == {
        "codex-native",
        "claude-native",
        "opencode-native",
    }
