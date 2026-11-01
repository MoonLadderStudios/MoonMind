"""Controlled pre-upgrade plan production for retained lifecycle journeys."""

from contextlib import contextmanager


def historical_profile_bound_policy():
    from moonmind.omnigent.runtime_provider_rollout import (
        RolloutState,
        default_runtime_provider_rollout_policy,
    )

    policy = default_runtime_provider_rollout_policy(
        env={"MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED": "false"}
    )
    return policy.model_copy(
        update={
            "rules": tuple(
                (
                    rule.model_copy(update={"state": RolloutState.new_work_default})
                    if rule.target_id == "codex.legacy-profile-bound-omnigent"
                    else rule
                )
                for rule in policy.rules
            )
        }
    )


@contextmanager
def pre_upgrade_profile_bound_admission(monkeypatch):
    """Produce a historical plan; restore current admission before execution.

    Resource, policy, credential, workspace and artifact producers remain real.
    Current new-work boundaries are tested separately without this fixture.
    """
    from moonmind.omnigent import legacy_retirement
    from moonmind.omnigent.harness_platform import planner

    policy = historical_profile_bound_policy()
    with monkeypatch.context() as pre_upgrade:
        pre_upgrade.setattr(
            planner,
            "select_execution_realizer",
            lambda **_kwargs: "codex-profile-bound@1",
        )
        pre_upgrade.setattr(
            planner, "load_runtime_provider_rollout_policy", lambda: policy
        )
        ref = "realizer:codex-profile-bound@1"
        row = legacy_retirement._INVENTORY_BY_SURFACE[ref]
        historical_row = row.model_copy(
            update={
                "retirement_class": legacy_retirement.RetirementClass.ACTIVE_PRODUCT_PATH
            }
        )
        pre_upgrade.setitem(
            legacy_retirement._INVENTORY_BY_SURFACE, ref, historical_row
        )
        pre_upgrade.setitem(
            legacy_retirement._INVENTORY_BY_ID, row.path_id, historical_row
        )
        yield policy
