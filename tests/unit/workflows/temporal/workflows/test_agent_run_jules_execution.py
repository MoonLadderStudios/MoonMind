from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


pytestmark = [pytest.mark.asyncio]


def _request(**overrides: Any) -> AgentExecutionRequest:
    payload = {
        "agentKind": "external",
        "agentId": "jules",
        "executionProfileRef": "profile:jules-default",
        "correlationId": "corr-1",
        "idempotencyKey": "idem-1",
        "instructionRef": "Implement the requested change.",
        "workspaceSpec": {"startingBranch": "feature-branch"},
        "parameters": {"publishMode": "none"},
    }
    payload.update(overrides)
    return AgentExecutionRequest(**payload)


def _configure_workflow_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-agent-run-1",
            "run_id": "run-1",
            "search_attributes": {},
            "parent": None,
        },
    )
    logger = type(
        "Logger",
        (),
        {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
    )
    monkeypatch.setattr(agent_run_module.workflow, "info", workflow_info)
    monkeypatch.setattr(agent_run_module.workflow, "logger", logger)
    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )


async def test_agent_run_jules_starts_new_run_instead_of_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    async def fake_execute_activity(
        activity: object,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity == agent_run_module.resolve_external_adapter:
            return "jules"
        if activity == agent_run_module.external_adapter_execution_style:
            return "polling"
        raise AssertionError(f"Unexpected workflow.execute_activity call: {activity!r}")

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    async def fake_execute_activity_with_routing(
        activity_name: str,
        payload: Any,
        *_args: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "integration.jules.start":
            return {"external_id": "new-session-1", "status": "queued"}
        if activity_name == "integration.jules.status":
            return {"normalized_status": "completed"}
        if activity_name == "integration.jules.fetch_result":
            return {"summary": "Done", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run, "_execute_activity_with_routing", fake_execute_activity_with_routing)
    monkeypatch.setattr(
        run,
        "_get_route_info",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=(
                "test-queue",
                timedelta(seconds=30),
                timedelta(seconds=30),
                None,
                None,
            ),
        ),
    )

    result = await run.run(
        _request(
            parameters={
                "publishMode": "none",
                "jules_session_id": "legacy-session-42",
            }
        )
    )

    assert routed_calls[0][0] == "integration.jules.start"
    assert all(name != "integration.jules.send_message" for name, _ in routed_calls)
    assert run.run_id == "new-session-1"
    assert result.failure_class is None


async def test_agent_run_jules_branch_publish_failure_maps_to_non_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    async def fake_execute_activity(
        activity: object,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity == agent_run_module.resolve_external_adapter:
            return "jules"
        if activity == agent_run_module.external_adapter_execution_style:
            return "polling"
        raise AssertionError(f"Unexpected workflow.execute_activity call: {activity!r}")

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    async def fake_execute_activity_with_routing(
        activity_name: str,
        payload: Any,
        *_args: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "integration.jules.start":
            return {"external_id": "session-1", "status": "queued"}
        if activity_name == "integration.jules.status":
            return {"normalized_status": "completed"}
        if activity_name == "integration.jules.fetch_result":
            return {
                "summary": "Provider reported success.",
                "metadata": {
                    "pullRequestUrl": "https://github.com/org/repo/pull/123",
                },
            }
        if activity_name == "repo.merge_pr":
            return {"merged": False, "summary": "Merge rejected"}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run, "_execute_activity_with_routing", fake_execute_activity_with_routing)
    monkeypatch.setattr(
        run,
        "_get_route_info",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=(
                "test-queue",
                timedelta(seconds=30),
                timedelta(seconds=30),
                None,
                None,
            ),
        ),
    )

    result = await run.run(
        _request(
            workspaceSpec={"startingBranch": "feature-branch", "targetBranch": "main"},
            parameters={"publishMode": "branch", "targetBranch": "main"},
        )
    )

    assert any(name == "repo.merge_pr" for name, _ in routed_calls)
    merge_payload = next(payload for name, payload in routed_calls if name == "repo.merge_pr")
    assert merge_payload == {
        "pr_url": "https://github.com/org/repo/pull/123",
        "target_branch": "main",
    }
    assert result.failure_class == "execution_error"
    assert result.provider_error_code == "branch_publish_failed"
    assert result.metadata["publishOutcome"] == "publish_failed"
