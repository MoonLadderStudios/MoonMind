"""Fan-out authority does not imply repository mutation authority."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.omnigent.workspace_intent import (
    authored_github_operations,
    authored_repository_mutation_required,
)
from moonmind.schemas.agent_runtime_models import AgentRunResult
from moonmind.security.execution_fanout_capabilities import (
    ExecutionFanoutCapabilityError,
    require_execution_fanout_authorization,
)
from moonmind.workflows.executions.repository_contract import DEFAULT_GIT_CONNECTION_REF
from tests.helpers.repository_connections import (
    github_pat_connection,
    github_repository_assignment,
    record_repository_connections,
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
    authorization = (
        OmnigentProfileBoundExecutionCoordinator._execution_fanout_authorization(built)
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


@pytest.mark.asyncio
async def test_batch_parent_runs_under_read_only_on_demand_workspace_policy(
    monkeypatch,
    tmp_path,
):
    from tests.unit.omnigent import test_oauth_profile_lifecycle as lifecycle

    built = await run_request(
        "batch-github-workflows", parameters={"publishMode": "none"}
    )
    document = lifecycle.policy_document()
    document["workspace"]["repositoryMutation"] = False
    monkeypatch.setattr(lifecycle, "policy_document", lambda: deepcopy(document))
    connection = github_pat_connection(
        DEFAULT_GIT_CONNECTION_REF, "ACTION_PAT"
    ).model_copy(update={"allowed_operations": ("read",)})
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        connection,
        assignments=[
            github_repository_assignment(
                connection.id, "owner/repo", operations=("read",)
            )
        ],
    )
    secret = AsyncMock(return_value="selected_token")
    monkeypatch.setattr("moonmind.auth.github_credentials._resolve_secret_ref", secret)
    credential = AsyncMock(
        side_effect=OmnigentProfileBoundExecutionCoordinator._github_token
    )
    execute = AsyncMock(return_value=AgentRunResult(summary="batch completed"))
    try:
        _ordered, evidence, _metadata, result = (
            await lifecycle._drive_authority_chain_coordinator(
                execute,
                request_parameters={
                    key: built.parameters[key]
                    for key in ("publishMode", "requiredCapabilities", "skill")
                },
                credential_resolver=credential,
                resolve_source_credential=True,
            )
        )

        assert result.failure_class is None
        assert result.summary == "batch completed"
        execute.assert_awaited_once()
        assert authored_github_operations(credential.await_args.args[0]) == ("read",)
        secret.assert_awaited_once()
        assert evidence[0]["runtime"]["hostMode"] == "on_demand_docker"
        assert evidence[0]["publication"]["repositoryMutationAuthorized"] is False
        assert (
            evidence[0]["publication"]["publicationState"] == "read_only_no_publication"
        )
    finally:
        await engine.dispose()
