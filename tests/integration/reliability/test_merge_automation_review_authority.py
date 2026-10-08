"""Direct review Activities consume their owning execution's admitted connection."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.db import base, models
from moonmind.workflows.adapters.github_service import (
    AutomatedReviewRequestResult,
    GitHubService,
    PullRequestReadinessResult,
)
from moonmind.workflows.temporal import activity_runtime
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalIntegrationActivities,
)
from moonmind.omnigent.host_services.github_credentials import OmnigentGithubCredentialService
from tests.integration.reliability.test_repository_access_consumers_4676 import (
    _REPOSITORY,
    _WORKFLOW,
    repository_consumers as _repository_consumers,
)
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ready_opencode_image_pair,  # noqa: F401
)

repository_consumers = _repository_consumers

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_HEAD = "a" * 40
_GATE = f"merge-automation:{_WORKFLOW}:{_REPOSITORY}:4719:{_HEAD}"
_CONFIG = {
    "enabled": True,
    "finishMode": "review_only",
    "reviewLoop": {
        "enabled": True,
        "automatedReviewProvider": "codex",
        "requireFreshReviewForEveryHead": True,
    },
}


async def _admit(context, monkeypatch, *, owner_id="user-1"):
    compiled = await context.compile(
        requiredCapabilities=["gh"],
        workflow={"publish": {"mode": "none", "mergeAutomation": _CONFIG}},
    )
    async with context.sessions() as session:
        session.add(models.TemporalExecutionCanonicalRecord(
            workflow_id=_WORKFLOW, run_id="run-1", owner_id=owner_id,
            workflow_type=models.TemporalWorkflowType.USER_WORKFLOW, entry="run",
            parameters={"omnigentExecutionPlan": compiled.binding.model_dump(by_alias=True)},
        ))
        await session.commit()

    @asynccontextmanager
    async def session_context():
        async with context.sessions() as session:
            yield session

    monkeypatch.setattr(base, "get_async_session_context", session_context)
    monkeypatch.setattr(activity_runtime.temporal_activity, "info", lambda: SimpleNamespace(
        activity_id="review-activity", workflow_id=_GATE, workflow_run_id="gate-run-1",
    ))
    return {
        "finishMode": "review_only",
        "parentWorkflowId": _GATE,
        "admittedParentWorkflowId": _WORKFLOW,
        "parentRunId": "run-1",
        "principal": owner_id,
        "parentExecutionPlan": compiled.binding.model_dump(by_alias=True),
        "repository": _REPOSITORY,
        "prNumber": 4719,
        "expectedHeadSha": _HEAD,
        "provider": "codex",
    }


def _install_github(monkeypatch):
    post = AsyncMock(return_value=AutomatedReviewRequestResult(
        status="requested", provider="codex", command="@codex review",
        headSha=_HEAD, requestCommentId=1234,
        requestedAt="2026-10-07T09:00:00Z", summary="requested",
    ))
    read = AsyncMock(return_value=PullRequestReadinessResult(headSha=_HEAD))
    monkeypatch.setattr(GitHubService, "request_automated_review", post)
    monkeypatch.setattr(GitHubService, "evaluate_pull_request_readiness", read)
    return post, read


@pytest.mark.parametrize("owner_id", ["user-1", "system"])
async def test_direct_review_request_and_readiness_use_admitted_connection(
    repository_consumers, monkeypatch, owner_id,
):
    context = repository_consumers
    payload = await _admit(context, monkeypatch, owner_id=owner_id)
    post, read = _install_github(monkeypatch)
    activities = TemporalIntegrationActivities()
    acquired = []
    acquire = OmnigentGithubCredentialService.acquire_repository_use

    async def capture(self, **kwargs):
        value = await acquire(self, **kwargs)
        acquired.append((kwargs["operation"], value))
        return value

    monkeypatch.setattr(OmnigentGithubCredentialService, "acquire_repository_use", capture)

    result = await activities.merge_automation_request_automated_review(payload)
    assert result["status"] == "requested"
    assert post.await_args.kwargs["github_token"] == "selected-credential-canary"
    assert "credential-canary" not in str(result)

    readiness = await activities.merge_automation_evaluate_readiness({
        **payload, "parentWorkflowId": _WORKFLOW,
        "pullRequest": {"repo": _REPOSITORY, "number": 4719, "headSha": _HEAD},
        "mergeAutomationConfig": _CONFIG,
    })
    assert read.await_args.kwargs["github_token"] == "selected-credential-canary"
    assert "credential-canary" not in str(readiness)
    assert [operation for operation, _value in acquired] == ["review_request", "read"]
    assert all(value.credential.cleared for _operation, value in acquired)
    assert all(value.binding.execution_owner == _GATE for _operation, value in acquired)

    # Reconciled ledger success still validates the immutable parent authority.
    recorded = await activities.merge_automation_request_automated_review(payload)
    assert recorded["status"] == "recorded"
    assert post.await_count == 1
    forged = {**payload, "parentExecutionPlan": {
        **payload["parentExecutionPlan"], "planArtifactRef": "artifact:unrelated",
    }}
    with pytest.raises((TemporalActivityRuntimeError, ValueError)):
        await activities.merge_automation_request_automated_review(forged)
    assert post.await_count == 1


@pytest.mark.parametrize("mismatch,operation", [
    ("actual_gate", "request"), ("actual_gate", "readiness"),
    ("ledger_owner", "request"),
])
async def test_review_authority_is_bound_to_actual_activity_gate(
    repository_consumers, monkeypatch, mismatch, operation,
):
    context = repository_consumers
    payload = await _admit(context, monkeypatch)
    post, read = _install_github(monkeypatch)
    if mismatch == "actual_gate":
        monkeypatch.setattr(activity_runtime.temporal_activity, "info", lambda: SimpleNamespace(
            activity_id="review-activity", workflow_id="unrelated-gate", workflow_run_id="gate-run-1",
        ))
    else:
        payload["parentWorkflowId"] = "unrelated-ledger-owner"
    activities = TemporalIntegrationActivities()
    with pytest.raises((TemporalActivityRuntimeError, ValueError)):
        if operation == "request":
            await activities.merge_automation_request_automated_review(payload)
        else:
            await activities.merge_automation_evaluate_readiness({
                **payload, "pullRequest": {"repo": _REPOSITORY, "number": 4719, "headSha": _HEAD},
                "mergeAutomationConfig": _CONFIG,
            })
    post.assert_not_awaited()
    read.assert_not_awaited()
    async with context.sessions() as session:
        from sqlalchemy import select
        assert not (await session.execute(select(models.MergeAutomationReviewRequestRecord))).scalars().all()


@pytest.mark.parametrize("field,value", [
    ("parentExecutionPlan", None),
    ("principal", "another-owner"),
    ("repository", "another/repository"),
    ("parentRunId", "another-run"),
])
async def test_review_request_rejects_unadmitted_authority_before_ledger(
    repository_consumers, monkeypatch, field, value,
):
    context = repository_consumers
    payload = await _admit(context, monkeypatch)
    post, _read = _install_github(monkeypatch)
    with pytest.raises((TemporalActivityRuntimeError, ValueError)):
        await TemporalIntegrationActivities().merge_automation_request_automated_review(
            {**payload, field: value, "githubToken": "caller-token-canary"},
        )
    post.assert_not_awaited()
    bad = {**payload, field: value, "githubToken": "caller-token-canary"}
    with pytest.raises((TemporalActivityRuntimeError, ValueError)):
        await TemporalIntegrationActivities().merge_automation_evaluate_readiness({
            **bad, "pullRequest": {"repo": bad["repository"], "number": 4719, "headSha": _HEAD},
            "mergeAutomationConfig": _CONFIG,
        })
    _read.assert_not_awaited()
    async with context.sessions() as session:
        from sqlalchemy import select
        assert not (await session.execute(select(models.MergeAutomationReviewRequestRecord))).scalars().all()


@pytest.mark.parametrize("repository_consumers", ["https://github.example.com"], indirect=True)
async def test_review_activity_rejects_enterprise_binding_before_public_api(
    repository_consumers, monkeypatch,
):
    payload = await _admit(repository_consumers, monkeypatch)
    post, read = _install_github(monkeypatch)
    with pytest.raises((TemporalActivityRuntimeError, ValueError)):
        await TemporalIntegrationActivities().merge_automation_request_automated_review(payload)
    post.assert_not_awaited()
    read.assert_not_awaited()


async def test_recorded_review_request_rechecks_selected_connection_revocation(
    repository_consumers, monkeypatch,
):
    context = repository_consumers
    payload = await _admit(context, monkeypatch)
    post, _read = _install_github(monkeypatch)
    activities = TemporalIntegrationActivities()
    await activities.merge_automation_request_automated_review(payload)
    async with context.sessions() as session:
        connection = await session.get(models.RepositoryConnectionRecord, "selected-repository")
        connection.lifecycle = "disabled"
        await session.commit()
    with pytest.raises((TemporalActivityRuntimeError, ValueError)):
        await activities.merge_automation_request_automated_review(payload)
    assert post.await_count == 1
