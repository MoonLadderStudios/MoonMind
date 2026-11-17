"""Fan-out authority does not imply repository mutation authority."""

import pytest

from moonmind.omnigent.host_services.runtime_environment import (
    OmnigentRuntimeEnvironmentService,
)
from moonmind.omnigent.workspace_intent import (
    authored_github_operations,
    authored_repository_mutation_required,
)
from moonmind.security.execution_fanout_capabilities import (
    ExecutionFanoutCapabilityError,
    require_execution_fanout_authorization,
)
from tests.unit.omnigent.test_github_action_permissions import request, run_request


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skill_name",
    [
        "batch-pr-resolver",
        "batch-dependabot-resolver",
        "batch-workflows",
        "batch-github-workflows",
    ],
)
async def test_resolved_batch_parent_retains_fanout_without_repository_mutation(
    skill_name,
):
    built = await run_request(skill_name, parameters={"publishMode": "none"})

    assert built.parameters["skill"]["sideEffect"]["kind"] == "enqueue_children"
    capabilities = built.parameters["requiredCapabilities"]
    assert "execution.fanout" in capabilities
    authorization = OmnigentRuntimeEnvironmentService._execution_fanout_authorization(
        built
    )
    assert authorization["selectedSkill"] == skill_name
    assert require_execution_fanout_authorization(capabilities, authorization)
    assert authored_github_operations(built) == ("read",)
    assert not authored_repository_mutation_required(built)

    # Classification cannot authorize an untrusted or unresolved fan-out Skill.
    for denied in ({}, {**authorization, "authorized": False}):
        with pytest.raises(ExecutionFanoutCapabilityError):
            require_execution_fanout_authorization(capabilities, denied)


@pytest.mark.parametrize("projection", ["skill", "parameters"])
@pytest.mark.parametrize(
    "action, mutation",
    [
        ({"sideEffect": {"kind": "enqueue_children"}}, False),
        (
            {"sideEffect": {"kind": "enqueue_children", "githubOperations": ["write"]}},
            True,
        ),
        ({"sideEffect": {"kind": "merge_pull_request"}}, True),
        ({"publish": {"githubOperations": ["write", "branch_write"]}}, True),
    ],
)
def test_repository_mutation_uses_composed_skill_actions(projection, action, mutation):
    built = request(skill=action if projection == "parameters" else {})
    if projection == "skill":
        built = built.model_copy(update={"skill": action})

    assert authored_repository_mutation_required(built) is mutation


@pytest.mark.parametrize(
    "parameters",
    [
        {"repositoryMutationRequired": True},
        {"repositoryOperation": "write"},
        {"publishMode": "branch"},
        {"publishMode": "pr"},
        {"publishMode": "auto", "githubOperations": ["write", "branch_write"]},
        {"githubOperations": ["merge_request"]},
    ],
)
def test_fanout_does_not_erase_explicit_repository_mutation(parameters):
    built = request(skill={"sideEffect": {"kind": "enqueue_children"}}, **parameters)

    assert authored_repository_mutation_required(built)


def test_non_repository_side_effect_does_not_imply_mutation():
    built = request(
        requiredCapabilities=["jira"],
        skill={"sideEffect": {"kind": "tracker_notification"}},
    )

    assert not authored_repository_mutation_required(built)
