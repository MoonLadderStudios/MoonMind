"""New shared work cannot recreate the retained profile-bound coordinator."""

import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.planner import select_execution_realizer
from moonmind.omnigent.legacy_retirement import (
    LegacyAdmissionRejected,
    assert_surface_admits_new_work,
)
from moonmind.omnigent.realizers.codex_profile_bound import CodexProfileBoundRealizer
from moonmind.omnigent.realizers.registry import OmnigentExecutionRealizerRegistry
from moonmind.omnigent.runtime_provider_rollout import (
    RUNTIME_PROVIDER_ROLLBACK_ENV,
    RuntimeProviderPathClass,
    RolloutState,
    default_runtime_provider_rollout_policy,
    effective_rule_state,
)


@pytest.mark.parametrize(
    "environment",
    [
        {"MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED": "false"},
        {RUNTIME_PROVIDER_ROLLBACK_ENV: "stop_new_generic_codex_admission"},
    ],
)
def test_disabled_shared_codex_does_not_create_profile_bound_work(environment):
    policy = default_runtime_provider_rollout_policy(env=environment)
    with pytest.raises(HarnessPlatformError, match="no execution realizer"):
        select_execution_realizer(
            harness_id="codex-native", is_codex=True, rollout_policy=policy
        )


def test_restoring_a_default_does_not_restore_profile_bound_admission():
    policy = default_runtime_provider_rollout_policy(
        env={RUNTIME_PROVIDER_ROLLBACK_ENV: "restore_legacy_or_direct_default"}
    )
    assert (
        select_execution_realizer(
            harness_id="codex-native", is_codex=True, rollout_policy=policy
        )
        == "generic-omnigent-host@1"
    )
    for rule in policy.rules_for(
        harness_id="codex-native",
        path_class=RuntimeProviderPathClass.legacy_profile_bound_omnigent,
    ):
        assert (
            effective_rule_state(rule, policy)[0] is RolloutState.retired_for_new_work
        )


def test_retired_realizer_remains_available_to_recorded_plans():
    with pytest.raises(LegacyAdmissionRejected):
        assert_surface_admits_new_work("realizer:codex-profile-bound@1")
    recorded_owner = CodexProfileBoundRealizer()
    registry = OmnigentExecutionRealizerRegistry()
    registry.register(recorded_owner)
    assert registry.require("codex-profile-bound@1") is recorded_owner
