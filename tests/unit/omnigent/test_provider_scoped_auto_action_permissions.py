"""GitHub action admission must not narrow provider-neutral Auto publication."""

import pytest

from moonmind.omnigent.workspace_intent import (
    WorkspaceIntentCompilationError,
    authored_github_operations,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


def _request(source=None, *, skill_owned=False, **parameters):
    payload = {"publishMode": "auto", "requiredCapabilities": ["git"], **parameters}
    if skill_owned:
        payload["skill"] = {
            "name": "provider-publisher",
            "publish": {"mode": "auto", "owner": "agent", "requiresEvidence": True},
        }
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="codex",
        correlationId="provider-auto",
        idempotencyKey="provider-auto",
        workspaceSpec={"repository": source} if source else {},
        parameters=payload,
    )


@pytest.mark.parametrize("skill_owned", [False, True])
@pytest.mark.parametrize(
    "source",
    [
        None,
        "/authorized/local/repository",
        "file:///authorized/local/repository",
        "https://gitlab.example/owner/repository.git",
        "ssh://git@gitlab.example/owner/repository.git",
        "https://github.com.example/owner/repository.git",
    ],
)
def test_non_github_auto_compiles_without_github_actions(source, skill_owned):
    built = _request(source, skill_owned=skill_owned)
    assert authored_github_operations(built) == ("read",)


@pytest.mark.parametrize(
    "source, parameters",
    [
        ("owner/repository", {}),
        ("https://github.com/owner/repository.git", {}),
        ("https://GitHub.com/owner/repository.git", {}),
        (None, {"requiredCapabilities": ["gh"]}),
        (None, {"githubOperations": []}),
        (None, {"githubOperations": ["read"]}),
        (None, {"skill": {"publish": {"githubOperations": []}}}),
        (None, {"skill": {"sideEffect": {"kind": "merge_pull_request"}}}),
        (
            "/authorized/local/repository",
            {
                "skill": {
                    "sideEffect": {"kind": "merge_pull_request"},
                    "inputs": {"finishMode": "fix_only"},
                }
            },
        ),
    ],
)
def test_github_relevant_auto_still_rejects_undeclared_writes(source, parameters):
    with pytest.raises(WorkspaceIntentCompilationError) as rejected:
        authored_github_operations(_request(source, **parameters))
    assert rejected.value.code == "github_action_intent_invalid"
    assert "explicit githubOperations" in str(rejected.value)


@pytest.mark.parametrize(
    "parameters",
    [
        {"repositoryMutationRequired": "true"},
        {"repositoryOperation": "unexpected"},
        {"publishMode": False},
        {"githubOperations": "write"},
        {"skill": {"publish": {"mode": True}}},
    ],
)
def test_non_github_context_does_not_bypass_malformed_signal_checks(parameters):
    with pytest.raises(WorkspaceIntentCompilationError) as rejected:
        authored_github_operations(_request(**parameters))
    assert rejected.value.code == "github_action_intent_invalid"


def test_non_github_source_does_not_erase_explicit_github_actions():
    built = _request(
        "/authorized/local/repository", githubOperations=["write", "branch_write"]
    )
    assert authored_github_operations(built) == ("read", "write", "branch_write")


def test_provider_neutral_auto_does_not_become_read_only_workspace_intent():
    from moonmind.omnigent.workspace_intent import authored_repository_mutation_required

    built = _request("/authorized/local/repository", skill_owned=True)
    assert authored_repository_mutation_required(built) is True
