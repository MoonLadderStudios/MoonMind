"""GitHub action admission must not narrow provider-neutral Auto publication."""

from unittest.mock import AsyncMock, Mock

import pytest

from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.omnigent.workspace_intent import authored_github_operations
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


@pytest.mark.asyncio
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
async def test_non_github_auto_reaches_plan_validation_without_github_or_ownership_effects(
    monkeypatch, source, skill_owned
):
    built = _request(source, skill_owned=skill_owned)
    lookup = AsyncMock(side_effect=AssertionError("unexpected GitHub authority lookup"))
    credential = AsyncMock(side_effect=AssertionError("unexpected GitHub credential"))
    secret = AsyncMock(side_effect=AssertionError("unexpected secret read"))
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.validate_default_github_connection_operations",
        lookup,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_default_github_connection_credential",
        credential,
    )
    monkeypatch.setattr("moonmind.auth.github_credentials._resolve_secret_ref", secret)

    class ReachedPlanValidation(Exception):
        pass

    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    coordinator._require_recorded_plan_request = Mock(side_effect=ReachedPlanValidation)
    coordinator._run_store = AsyncMock()
    coordinator._hosts = AsyncMock()
    coordinator._lease_client = AsyncMock()
    # Workspace authorization and materialization have their own later owner.
    # This boundary must preserve the request and reach immutable plan checks.
    with pytest.raises(ReachedPlanValidation):
        await coordinator.execute(built)
    coordinator._require_recorded_plan_request.assert_called_once_with(built)
    assert coordinator._run_store.mock_calls == []
    assert coordinator._hosts.mock_calls == []
    assert coordinator._lease_client.mock_calls == []
    assert await coordinator._github_token(built) is None
    lookup.assert_not_awaited()
    credential.assert_not_awaited()
    secret.assert_not_awaited()


@pytest.mark.asyncio
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
async def test_github_relevant_auto_still_rejects_undeclared_writes_before_effects(
    source, parameters
):
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    result = await coordinator.execute(_request(source, **parameters))
    assert result.provider_error_code == "github_action_intent_invalid"
    assert "explicit githubOperations" in result.summary


@pytest.mark.asyncio
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
async def test_non_github_context_does_not_bypass_malformed_signal_checks(parameters):
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    result = await coordinator.execute(_request(**parameters))
    assert result.provider_error_code == "github_action_intent_invalid"


def test_non_github_source_does_not_erase_explicit_github_actions():
    built = _request(
        "/authorized/local/repository", githubOperations=["write", "branch_write"]
    )
    assert authored_github_operations(built) == ("read", "write", "branch_write")


def test_provider_neutral_auto_does_not_become_read_only_workspace_intent():
    from moonmind.omnigent.workspace_intent import authored_repository_mutation_required

    built = _request("/authorized/local/repository", skill_owned=True)
    assert authored_repository_mutation_required(built) is True
