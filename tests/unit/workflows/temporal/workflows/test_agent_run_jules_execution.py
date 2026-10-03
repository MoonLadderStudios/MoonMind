from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
)
from moonmind.schemas.temporal_activity_models import (
    AgentRuntimeFetchResultInput,
    ExternalAgentRunInput,
)
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
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

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "integration.resolve_adapter_metadata":
            return {"agent_id": payload, "execution_style": "polling"}
        if activity_name == "integration.jules.start":
            return {"external_id": "new-session-1", "status": "queued"}
        if activity_name == "integration.jules.status":
            return {"normalized_status": "completed"}
        if activity_name == "integration.jules.fetch_result":
            return {"summary": "Done", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        _request(
            parameters={
                "publishMode": "none",
                "jules_session_id": "legacy-session-42",
            }
        )
    )

    assert routed_calls[0][0] == "integration.resolve_adapter_metadata"
    assert routed_calls[1][0] == "integration.jules.start"
    assert all(name != "integration.jules.send_message" for name, _ in routed_calls)
    assert run.run_id == "new-session-1"
    assert result.failure_class is None
    assert result.metadata["childWorkflowId"] == "wf-agent-run-1"
    assert result.metadata["childRunId"] == "run-1"


async def test_agent_run_provisions_external_callback_url_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "integration.resolve_adapter_metadata":
            return {
                "agent_id": "jules",
                "execution_style": "polling",
                "supports_callbacks": True,
                "callback_base_url": "https://moonmind.example.test",
            }
        if activity_name == "integration.jules.start":
            assert isinstance(payload, AgentExecutionRequest)
            assert payload.callback_correlation_key
            assert payload.callback_url == (
                "https://moonmind.example.test/api/integrations/jules/callbacks/"
                f"{payload.callback_correlation_key}"
            )
            assert payload.callback_policy["callbackUrl"] == payload.callback_url
            assert payload.callback_policy["callbackCorrelationKey"] == (
                payload.callback_correlation_key
            )
            return {
                "external_id": "new-session-1",
                "status": "queued",
                "callback_supported": True,
                "callback_correlation_key": payload.callback_correlation_key,
            }
        if activity_name == "integration.jules.status":
            return {"normalized_status": "completed"}
        if activity_name == "integration.jules.fetch_result":
            return {"summary": "Done", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(_request())

    assert routed_calls[1][0] == "integration.jules.start"
    assert result.failure_class is None


async def test_external_callback_ingress_strips_candidates_before_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    _configure_workflow_runtime(monkeypatch)

    request = _request(
        callbackPolicy={
            "callbackUrl": "   ",
            "url": " https://moonmind.example.test/explicit-callback ",
            "callbackCorrelationKey": "   ",
        },
        callbackCorrelationKey=" request-key ",
    )

    updated = run._with_external_callback_ingress(
        request,
        integration_name="jules",
        supports_callbacks=True,
        callback_base_url="https://moonmind.example.test",
    )

    assert updated.callback_url == "https://moonmind.example.test/explicit-callback"
    assert updated.callback_correlation_key == "request-key"
    assert updated.callback_policy["callbackUrl"] == updated.callback_url
    assert updated.callback_policy["callbackCorrelationKey"] == "request-key"


@pytest.mark.parametrize("bound_merge", [False, True])
@pytest.mark.parametrize(
    "repository_spec",
    [
        {"repository": "org/repo"},
        {"repo": "org/repo"},
        {"repositoryTarget": {
            "provider": "git",
            "connectionRef": "repository-connection:git-default",
            "repository": {"name": "org/repo"},
            "branch": {"name": "feature-branch"},
        }},
    ],
    ids=["legacy-repository", "legacy-repo", "canonical-target"],
)
async def test_agent_run_jules_branch_publish_failure_maps_to_non_success(
    monkeypatch: pytest.MonkeyPatch,
    bound_merge: bool,
    repository_spec: dict[str, Any],
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "patched",
        lambda patch: bound_merge or patch != "jules-merge-target-authority-v1",
    )

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "integration.resolve_adapter_metadata":
            return {"agent_id": payload, "execution_style": "polling"}
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

    monkeypatch.setattr(
        agent_run_module.workflow, "wait_condition", fake_wait_condition
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        _request(
            workspaceSpec={
                **repository_spec,
                "startingBranch": "feature-branch",
                "targetBranch": "main",
            },
            parameters={"publishMode": "branch", "targetBranch": "main"},
        )
    )

    assert any(name == "repo.merge_pr" for name, _ in routed_calls)
    merge_payload = next(
        payload for name, payload in routed_calls if name == "repo.merge_pr"
    )
    expected = {
        "pr_url": "https://github.com/org/repo/pull/123",
        "target_branch": "main",
    }
    if bound_merge:
        expected["expected_repository"] = "org/repo"
    assert merge_payload == expected
    assert result.failure_class == "execution_error"
    assert result.provider_error_code == "branch_publish_failed"
    assert result.metadata["publishOutcome"] == "publish_failed"
    assert result.metadata["providerNativePullRequest"] == {
        "url": "https://github.com/org/repo/pull/123",
        "readinessState": "pending",
        "source": "jules",
        "headBranch": "feature-branch",
        "baseBranch": "main",
    }
    assert result.metadata["pullRequestUrl"] == "https://github.com/org/repo/pull/123"
    assert result.metadata["headBranch"] == "feature-branch"
    assert result.metadata["baseBranch"] == "main"
    assert result.metadata["readinessState"] == "pending"


async def test_agent_run_does_not_treat_generic_external_url_as_native_pr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    _configure_workflow_runtime(monkeypatch)

    result = run._enrich_result_metadata(
        request=_request(),
        result=AgentRunResult(
            summary="Provider task complete.",
            metadata={"externalUrl": "https://jules.example.test/tasks/task-123"},
        ),
    )

    assert result is not None
    assert "providerNativePullRequest" not in result.metadata
    assert "pullRequestUrl" not in result.metadata
    assert result.metadata["externalUrl"] == "https://jules.example.test/tasks/task-123"


@pytest.mark.parametrize("old_scheduled_call", [False, True])
@pytest.mark.parametrize(
    "workspace_spec",
    [
        {"repository": "org/repo", "startingBranch": "release"},
        {"repo": "org/repo", "startingBranch": "release"},
        {"repositoryTarget": {
            "provider": "git",
            "connectionRef": "repository-connection:git-default",
            "repository": {"name": "org/repo"},
            "branch": {"name": "release"},
        }},
    ],
    ids=["legacy-repository", "legacy-repo", "canonical-target-only"],
)
async def test_agent_run_jules_pins_head_and_recovers_old_authority(
    monkeypatch: pytest.MonkeyPatch, old_scheduled_call: bool,
    workspace_spec: dict[str, Any],
) -> None:
    run = MoonMindAgentRun()
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        agent_run_module.workflow, "patched",
        lambda name: not (old_scheduled_call and name == "jules-merge-target-authority-v1"),
    )
    calls = []
    url = "https://github.com/org/repo/pull/123"

    async def wait(_condition, timeout):
        raise asyncio.TimeoutError()

    async def execute(name, payload, **kwargs):
        if name == "integration.resolve_adapter_metadata":
            return {"agent_id": payload, "execution_style": "polling"}
        if name == "integration.jules.start":
            return {"external_id": "session-1", "status": "queued"}
        if name == "integration.jules.status":
            return {"normalized_status": "completed"}
        if name == "integration.jules.fetch_result":
            return {"summary": "Done", "metadata": {"pullRequestUrl": url}}
        if name == "repo.merge_pr":
            calls.append(dict(payload))
            if not payload.get("expected_repository"):
                return {"merged": False, "reasonCode": "merge_authority_required"}
            if not payload.get("expected_head_sha"):
                return {"merged": False, "reasonCode": "merge_head_resolved", "expectedHeadSha": "a" * 40}
            return {"merged": True, "mergeSha": "c" * 40}
        if name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(name)

    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", wait)
    monkeypatch.setattr(run, "_execute_routed_activity", execute)
    result = await run.run(_request(
        workspaceSpec=workspace_spec,
        parameters={"publishMode": "branch"},
    ))

    assert result.failure_class is None
    assert result.metadata["publishOutcome"] == "branch_merged"
    assert len(calls) == (3 if old_scheduled_call else 2)
    assert calls[-1] == {
        "pr_url": url, "expected_repository": "org/repo",
        "target_branch": "release", "expected_head_sha": "a" * 40,
    }


@pytest.mark.parametrize(
    "legacy_patches",
    [
        (),
        ("jules-merge-canonical-authority-v1",),
        ("jules-merge-canonical-authority-v1", "jules-merge-target-authority-v1"),
    ],
    ids=["canonical", "retained-bound", "retained-unbound"],
)
@pytest.mark.parametrize("legacy_aliases", [False, True])
async def test_agent_run_jules_repository_authority_history_replays(
    monkeypatch: pytest.MonkeyPatch,
    legacy_patches: tuple[str, ...],
    legacy_aliases: bool,
) -> None:
    """Retain old repository/branch payloads and replay canonical-only publication."""
    calls = []
    emitted_merge_payloads = []
    url = "https://github.com/org/repo/pull/123"

    @activity.defn(name="integration.resolve_adapter_metadata")
    async def resolve_metadata(agent_id: str) -> dict:
        return {"agent_id": agent_id, "execution_style": "polling"}

    @activity.defn(name="integration.jules.start")
    async def start(request: AgentExecutionRequest) -> dict:
        return {"external_id": "session-1", "status": "queued"}

    @activity.defn(name="integration.jules.status")
    async def status(request: ExternalAgentRunInput) -> dict:
        return {"normalized_status": "completed"}

    @activity.defn(name="integration.jules.fetch_result")
    async def fetch(request: ExternalAgentRunInput) -> dict:
        return {"summary": "Done", "metadata": {"pullRequestUrl": url}}

    @activity.defn(name="repo.merge_pr")
    async def merge(payload: dict) -> dict:
        calls.append(dict(payload))
        if not payload.get("expected_repository"):
            return {"merged": False, "reasonCode": "merge_authority_required"}
        if not payload.get("expected_head_sha"):
            return {
                "merged": False,
                "reasonCode": "merge_head_resolved",
                "expectedHeadSha": "a" * 40,
            }
        return {"merged": True, "mergeSha": "c" * 40}

    @activity.defn(name="agent_runtime.publish_artifacts")
    async def publish(result: AgentRunResult) -> AgentRunResult:
        return result

    async def routed(self, name, payload, **_kwargs):
        if name == "repo.merge_pr":
            emitted_merge_payloads.append(dict(payload))
        return await agent_run_module.workflow.execute_activity(
            name,
            payload,
            task_queue=agent_run_module.workflow.info().task_queue,
            start_to_close_timeout=timedelta(seconds=10),
        )

    original_patched = agent_run_module.workflow.patched
    monkeypatch.setattr(
        agent_run_module.workflow,
        "patched",
        lambda patch: False if patch in legacy_patches else original_patched(patch),
    )
    monkeypatch.setattr(MoonMindAgentRun, "_execute_routed_activity", routed)
    queue = f"jules-canonical-authority-{len(legacy_patches)}-{legacy_aliases}"
    workspace_spec = {
        "repositoryTarget": {
            "provider": "git",
            "repository": {"name": "org/repo"},
            "branch": {"name": "release"},
        },
    }
    if legacy_aliases:
        workspace_spec.update(repository="legacy/repo", startingBranch="main")
    # Bound startup and teardown as well as execution; a broken ephemeral
    # server must release the managed test slot rather than stall the resolver.
    async with asyncio.timeout(60):
        async with (
            await WorkflowEnvironment.start_time_skipping(
                data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
            ) as env,
            Worker(
                env.client,
                task_queue=queue,
                workflows=[MoonMindAgentRun],
                workflow_runner=UnsandboxedWorkflowRunner(),
                activities=[resolve_metadata, start, status, fetch, merge, publish],
                graceful_shutdown_timeout=timedelta(seconds=1),
            ),
        ):
            handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                _request(
                    workspaceSpec=workspace_spec,
                    parameters={"publishMode": "branch"},
                ),
                id=queue,
                task_queue=queue,
            )
            result = await asyncio.wait_for(handle.result(), 30)
            history = await handle.fetch_history()

    expected_repository = (
        ("legacy/repo" if legacy_aliases else "") if legacy_patches else "org/repo"
    )
    authored = {
        "pr_url": url,
        "expected_repository": expected_repository,
        "target_branch": "main" if legacy_patches else "release",
    }
    expected_calls = (
        [{"pr_url": url}, authored] if len(legacy_patches) == 2 else [authored]
    )
    if expected_repository:
        expected_calls.append({**authored, "expected_head_sha": "a" * 40})
    elif len(legacy_patches) != 2:
        expected_calls.append(authored)
    assert calls == expected_calls
    assert result.failure_class == (None if expected_repository else "execution_error")

    monkeypatch.setattr(agent_run_module.workflow, "patched", original_patched)
    emitted_merge_payloads.clear()
    await asyncio.wait_for(
        Replayer(
            workflows=[MoonMindAgentRun],
            workflow_runner=UnsandboxedWorkflowRunner(),
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
        ).replay_workflow(history),
        30,
    )
    # Temporal's determinism checker does not compare every argument value;
    # verify the actual replayed payloads match their retained activity inputs.
    assert emitted_merge_payloads == calls


async def test_agent_run_external_poll_and_fetch_use_typed_activity_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    wait_calls = 0

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        nonlocal wait_calls
        wait_calls += 1
        raise asyncio.TimeoutError()

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "integration.resolve_adapter_metadata":
            return {"agent_id": payload, "execution_style": "polling"}
        if activity_name == "integration.jules.start":
            return {"external_id": "session-typed-1", "status": "queued"}
        if activity_name == "integration.jules.status":
            assert isinstance(payload, ExternalAgentRunInput)
            return AgentRunStatus(
                runId=payload.run_id,
                agentKind="external",
                agentId="jules",
                status="completed",
            )
        if activity_name == "integration.jules.fetch_result":
            assert isinstance(payload, ExternalAgentRunInput)
            return AgentRunResult(summary="Done", metadata={})
        if activity_name == "agent_runtime.publish_artifacts":
            assert isinstance(payload, AgentRunResult)
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(_request())

    assert wait_calls == 1
    assert result.summary == "Done"
    status_payload = next(
        payload for name, payload in routed_calls if name == "integration.jules.status"
    )
    fetch_payload = next(
        payload
        for name, payload in routed_calls
        if name == "integration.jules.fetch_result"
    )
    assert isinstance(status_payload, ExternalAgentRunInput)
    assert status_payload.run_id == "session-typed-1"
    assert isinstance(fetch_payload, ExternalAgentRunInput)
    assert fetch_payload.run_id == "session-typed-1"

async def test_agent_run_jules_feedback_with_auto_answer_disabled_signals_intervention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []
    parent_signals: list[tuple[str, str]] = []

    _configure_workflow_runtime(monkeypatch)

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        raise asyncio.TimeoutError()

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "integration.resolve_adapter_metadata":
            return {"agent_id": "jules", "execution_style": "polling"}
        if activity_name == "integration.jules.start":
            return {"external_id": "jules-session-1", "status": "running"}
        if activity_name == "integration.jules.status":
            return AgentRunStatus(
                runId="jules-session-1",
                agentKind="external",
                agentId="jules",
                status="awaiting_feedback",
                metadata={"activityId": "activity-1"},
            )
        if activity_name == "integration.jules.list_activities":
            return {
                "activityId": "activity-1",
                "latestAgentQuestion": "Which branch should I use?",
            }
        if activity_name == "integration.jules.get_auto_answer_config":
            return {"enabled": False, "max_answers": 3}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    async def fake_signal_parent_child_state_changed(
        parent_info: Any,
        state: str,
        reason: str,
    ) -> None:
        parent_signals.append((state, reason))

    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)
    monkeypatch.setattr(
        run,
        "_signal_parent_child_state_changed",
        fake_signal_parent_child_state_changed,
    )

    result = await run.run(_request())

    assert result.failure_class == "user_error"
    assert result.provider_error_code == "intervention_requested"
    assert result.metadata["status"] == "intervention_requested"
    assert result.metadata["reason"] == "agent_requested_feedback"
    assert result.metadata["julesAutoAnswerReason"] == "jules_auto_answer_disabled"
    assert result.metadata["lastStatus"]["status"] == "awaiting_feedback"
    assert ("intervention_requested", "Jules requested human feedback.") in parent_signals
    assert all(name != "integration.jules.fetch_result" for name, _ in routed_calls)


async def test_resolve_adapter_metadata_normalizes_case_for_activity_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.workflows.adapters.external_adapter_registry import (
        ExternalAdapterRegistry,
    )
    from moonmind.workflows.adapters.openclaw_agent_adapter import (
        OpenClawExternalAdapter,
    )

    registry = ExternalAdapterRegistry()
    registry.register("openclaw", OpenClawExternalAdapter)
    monkeypatch.setattr(
        agent_run_module,
        "build_default_registry",
        lambda: registry,
    )

    metadata = await agent_run_module.resolve_adapter_metadata("OpenClaw")

    assert metadata == {
        "agent_id": "openclaw",
        "execution_style": "streaming_gateway",
        "supports_callbacks": False,
        "callback_base_url": None,
    }

async def test_resolve_adapter_metadata_exposes_gated_omnigent_streaming_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.workflows.adapters.external_adapter_registry import (
        build_default_registry,
    )

    registry = build_default_registry(
        env={
            "OMNIGENT_ENABLED": "1",
            "OMNIGENT_SERVER_URL": "https://omnigent.example.test",
            "OMNIGENT_API_TOKEN": "activity-boundary-only",
        }
    )
    monkeypatch.setattr(
        agent_run_module,
        "build_default_registry",
        lambda: registry,
    )

    metadata = await agent_run_module.resolve_adapter_metadata("omnigent")

    assert metadata == {
        "agent_id": "omnigent",
        "execution_style": "streaming_gateway",
        "supports_callbacks": False,
        "callback_base_url": None,
    }

@pytest.mark.parametrize(
    "alias",
    ["omnigent_session", "omnigent_claude", "omnigent_codex", "omnigent_polly"],
)
async def test_resolve_adapter_metadata_rejects_omnigent_top_level_aliases(
    monkeypatch: pytest.MonkeyPatch,
    alias: str,
) -> None:
    def unexpected_registry() -> None:
        pytest.fail("Unsupported aliases must be rejected before adapter lookup")

    monkeypatch.setattr(
        agent_run_module,
        "build_default_registry",
        unexpected_registry,
    )

    with pytest.raises(
        ApplicationError, match=f"unknown agent runtime capability '{alias}'"
    ) as error:
        await agent_run_module.resolve_adapter_metadata(alias)
    assert error.value.non_retryable is True

async def test_agent_run_streaming_gateway_uses_validated_provider_execute_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any, dict[str, Any]]] = []

    _configure_workflow_runtime(monkeypatch)

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload, kwargs))
        if activity_name == "integration.resolve_adapter_metadata":
            return {"agent_id": "omnigent", "execution_style": "streaming_gateway"}
        if activity_name == "integration.omnigent.execute":
            assert isinstance(payload, AgentExecutionRequest)
            assert payload.agent_id == "omnigent"
            return AgentRunResult(summary="Stream complete", metadata={})
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        _request(
            agentId="omnigent",
            executionProfileRef=None,
        )
    )

    execute_call = next(
        call for call in routed_calls if call[0] == "integration.omnigent.execute"
    )
    assert execute_call[0] == "integration.omnigent.execute"
    assert (
        execute_call[2]["heartbeat_timeout"]
        == agent_run_module.STREAMING_EXTERNAL_HEARTBEAT_TIMEOUT
    )
    assert all(name != "integration.openclaw.execute" for name, _, _ in routed_calls)
    assert result.summary == "Stream complete"
    assert result.metadata["childWorkflowId"] == "wf-agent-run-1"

async def test_agent_run_managed_passes_commit_message_override_to_fetch_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId="managed-run-1",
                agentKind="managed",
                agentId=request.agent_id,
                status="running",
                startedAt=agent_run_module.workflow.now(),
            )

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        run.completion_event.set()

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> _FakeManagerHandle:
        run.slot_assigned_event.set()
        run._assigned_profile_id = execution_profile_ref or "default-managed"
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "agent_runtime.fetch_result":
            return {"summary": "Managed success", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(
        agent_run_module,
        "ManagedAgentAdapter",
        _FakeManagedAgentAdapter,
    )
    monkeypatch.setattr(
        run,
        "_ensure_manager_and_signal",
        fake_ensure_manager_and_signal,
    )
    monkeypatch.setattr(
        run,
        "_sync_manager_profiles",
        fake_sync_manager_profiles,
    )
    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        AgentExecutionRequest(
            agentKind="managed",
            agentId="codex_cli",
            correlationId="corr-managed-1",
            idempotencyKey="idem-managed-1",
            parameters={
                "publishMode": "pr",
                "commitMessage": "Use producer commit text",
            },
            workspaceSpec={"startingBranch": "main"},
        )
    )

    assert result.summary == "Managed success"
    fetch_payload = next(
        payload for name, payload in routed_calls if name == "agent_runtime.fetch_result"
    )
    assert isinstance(fetch_payload, AgentRuntimeFetchResultInput)
    assert fetch_payload.publish_mode == "pr"
    assert fetch_payload.commit_message == "Use producer commit text"
    assert fetch_payload.target_branch == "main"

async def test_agent_run_managed_preserves_workflow_scoped_session_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = MoonMindAgentRun()
    routed_calls: list[tuple[str, Any]] = []

    _configure_workflow_runtime(monkeypatch)

    class _FakeManagedAgentAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("ManagedAgentAdapter should not be used for managedSession requests")

    class _FakeCodexSessionAdapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def start(self, request: AgentExecutionRequest) -> AgentRunHandle:
            return AgentRunHandle(
                runId="managed-session-run-2",
                agentKind="managed",
                agentId=request.agent_id,
                status="completed",
                startedAt=agent_run_module.workflow.now(),
            )

        async def status(self, run_id: str) -> AgentRunStatus:
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="codex_cli",
                status="completed",
            )

        async def fetch_result(self, run_id: str) -> AgentRunResult:
            raise AssertionError(
                "Terminal managed-session runs should fetch results through "
                "agent_runtime.fetch_result"
            )

    async def fake_wait_condition(_condition: Any, timeout: timedelta) -> None:
        run.completion_event.set()

    class _FakeManagerHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

    class _FakeSessionWorkflowHandle:
        async def signal(self, signal_name: str, payload: Any) -> None:
            return None

        async def query(self, query_name: str) -> dict[str, Any]:
            return {
                "binding": {
                    "workflowId": "wf-run-1:session:codex_cli",
                    "agentRunId": "wf-run-1",
                    "sessionId": "sess:wf-run-1:codex_cli",
                    "sessionEpoch": 1,
                    "runtimeId": "codex_cli",
                    "executionProfileRef": "codex-default",
                },
                "status": "active",
                "containerId": None,
                "threadId": None,
                "activeTurnId": None,
                "terminationRequested": False,
            }

    async def fake_ensure_manager_and_signal(
        manager_id: str,
        runtime_id: str,
        *,
        request_slot: bool,
        execution_profile_ref: str | None,
        profile_selector: dict[str, Any],
        request_priority: int | None = None,
        request_queue_metadata: dict[str, Any] | None = None,
    ) -> _FakeManagerHandle:
        run.slot_assigned_event.set()
        run._assigned_profile_id = execution_profile_ref or "default-managed"
        return _FakeManagerHandle()

    async def fake_sync_manager_profiles(
        *,
        manager_id: str,
        manager_handle: object,
        runtime_id: str,
    ) -> int:
        return 1

    async def fake_execute_routed_activity(
        activity_name: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        routed_calls.append((activity_name, payload))
        if activity_name == "agent_runtime.fetch_result":
            return {"summary": "Managed success", "metadata": {}}
        if activity_name == "agent_runtime.publish_artifacts":
            return payload
        raise AssertionError(f"Unexpected routed activity: {activity_name}")

    monkeypatch.setattr(
        agent_run_module,
        "ManagedAgentAdapter",
        _FakeManagedAgentAdapter,
    )
    monkeypatch.setattr(
        agent_run_module,
        "CodexSessionAdapter",
        _FakeCodexSessionAdapter,
    )
    monkeypatch.setattr(
        run,
        "_ensure_manager_and_signal",
        fake_ensure_manager_and_signal,
    )
    monkeypatch.setattr(
        run,
        "_sync_manager_profiles",
        fake_sync_manager_profiles,
    )
    monkeypatch.setattr(agent_run_module.workflow, "wait_condition", fake_wait_condition)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "get_external_workflow_handle",
        lambda *_args, **_kwargs: _FakeSessionWorkflowHandle(),
    )
    monkeypatch.setattr(run, "_execute_routed_activity", fake_execute_routed_activity)

    result = await run.run(
        AgentExecutionRequest(
            agentKind="managed",
            agentId="codex_cli",
            correlationId="corr-managed-2",
            idempotencyKey="idem-managed-2",
            executionProfileRef="codex-default",
            managedSession={
                "workflowId": "wf-run-1:session:codex_cli",
                "agentRunId": "wf-run-1",
                "sessionId": "sess:wf-run-1:codex_cli",
                "sessionEpoch": 1,
                "runtimeId": "codex_cli",
            },
        )
    )

    fetch_payload = next(
        payload for name, payload in routed_calls if name == "agent_runtime.fetch_result"
    )
    assert isinstance(fetch_payload, AgentRuntimeFetchResultInput)
    assert fetch_payload.run_id == "wf-run-1"
    assert fetch_payload.agent_id == "codex_cli"
    assert result.metadata["managedSession"] == {
        "workflowId": "wf-run-1:session:codex_cli",
        "agentRunId": "wf-run-1",
        "sessionId": "sess:wf-run-1:codex_cli",
        "sessionEpoch": 1,
        "runtimeId": "codex_cli",
        "executionProfileRef": None,
    }
