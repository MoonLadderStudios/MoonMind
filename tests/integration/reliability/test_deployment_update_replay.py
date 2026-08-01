from __future__ import annotations

import pytest

from moonmind.workflows.skills.deployment_execution import (
    ComposeCommandPlan,
    _command_plan_targeting_stack_services,
)
from tests.integration.reliability.helpers import load_replay

pytestmark = [
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.reliability_journey,
]


def test_deployment_update_reconciles_non_image_infrastructure() -> None:
    replay_id = "deployment-update-infrastructure-reconciliation"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    source_plan = manifest["commandPlan"]

    plan = _command_plan_targeting_stack_services(
        ComposeCommandPlan(
            runner_mode=source_plan["runnerMode"],
            pull_args=tuple(source_plan["pullArgs"]),
            up_args=tuple(source_plan["upArgs"]),
        ),
        before_state=manifest["beforeState"],
        requested_repository=manifest["requestedRepository"],
        excluded_services=manifest["excludedServices"],
    )

    expected_pull_args = (
        *tuple(source_plan["pullArgs"]),
        *tuple(expected["pullServices"]),
    )
    expected_up_args = (
        *tuple(source_plan["upArgs"]),
        "--no-deps",
        *tuple(expected["reconciliationServices"]),
    )
    assert plan.pull_args == expected_pull_args
    assert plan.up_args == expected_up_args
    assert all(
        service not in plan.pull_args and service not in plan.up_args
        for service in expected["excludedServices"]
    )
