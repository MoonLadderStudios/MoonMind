"""MM-1209 regression for the real MergeAutomation/UserWorkflow/AgentRun chain."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from temporalio import activity
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.config.settings import settings
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_TASK_QUEUE,
    ARTIFACTS_TASK_QUEUE,
    INTEGRATIONS_TASK_QUEUE,
    LLM_TASK_QUEUE,
)
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow,
)
from moonmind.workflows.temporal.workflows.merge_gate import (
    deterministic_resolver_idempotency_key,
)
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.integration.services.temporal.workflows.test_agent_run import (
    _COMMON_AGENT_RUN_ACTIVITIES,
    MockProviderProfileManager,
    mock_agent_runtime_fetch_result,
    mock_agent_runtime_status,
    mock_provider_profile_list,
)
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _mock_resilience_policy_envelope,
)


pytestmark = [pytest.mark.integration]


@activity.defn(name="provider_profile.list")
async def _ready_profiles(payload: dict[str, Any]) -> dict[str, Any]:
    from api_service.services.provider_profile_readiness import (
        provider_profile_launch_ready_from_payload,
    )
    from moonmind.provider_profiles.isolation_policy import derive_isolation_policy

    if (payload.get("runtime_id") or payload.get("runtimeId")) != "claude_code":
        return {"profiles": []}
    result = await mock_provider_profile_list(payload)
    for profile in result["profiles"]:
        profile.update(
            {
                "provider_id": "anthropic",
                "credential_source": "secret_ref",
                "runtime_materialization_mode": "api_key_env",
                "auth_state": "connected",
                "secret_refs": {"anthropic_api_key": "env://ANTHROPIC_API_KEY"},
            }
        )
        isolation = derive_isolation_policy(
            runtime_id="claude_code",
            provider_id="anthropic",
            authentication_method="api_key",
            credential_source="secret_ref",
            runtime_materialization_mode="api_key_env",
        )
        profile["clear_env_keys"] = list(isolation.keys)
        assert provider_profile_launch_ready_from_payload(profile)
    return result


@activity.defn(name="agent_runtime.status")
async def _running_status(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "runId": payload.get("runId") or payload.get("run_id"),
        "agentKind": "managed",
        "agentId": "claude_code",
        "status": "running",
    }


@activity.defn(name="agent_runtime.fetch_result")
async def _completed_result(_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": "Harness completed; resolver evidence must determine its outcome."
    }


@activity.defn(name="plan.generate")
async def _plan_generate(_payload: dict[str, Any]) -> dict[str, str]:
    return {"plan_ref": "artifact://mm1209/plan"}


@activity.defn(name="artifact.read")
async def _artifact_read(_payload: dict[str, Any]) -> bytes:
    artifact_ref = str(
        _payload.get("artifact_ref") or _payload.get("artifactRef") or ""
    )
    if artifact_ref == "artifact://mm1209/registry":
        return json.dumps({"skills": []}).encode("utf-8")
    return json.dumps(
        {
            "plan_version": "1.0",
            "metadata": {
                "title": "Resolve PR",
                "created_at": "2026-07-12T00:00:00Z",
                "registry_snapshot": {
                    "digest": "reg:sha256:" + ("a" * 64),
                    "artifact_ref": "artifact://mm1209/registry",
                },
            },
            "policy": {"failure_mode": "FAIL_FAST"},
            "nodes": [
                {
                    "id": "resolver",
                    "tool": {"type": "agent_runtime", "name": "claude_code"},
                    "inputs": {
                        "targetRuntime": "claude_code",
                        "instructions": "Run the resolved pr-resolver Skill.",
                        "selectedSkill": "pr-resolver",
                        "skill": {
                            "name": "pr-resolver",
                            "sideEffect": {
                                "terminalContractId": "pr_resolver_terminal.v1",
                                "outcomeArtifact": "var/pr_resolver/result.json",
                                "terminalSchemaVersion": "2",
                            },
                        },
                    },
                }
            ],
        }
    ).encode("utf-8")


@activity.defn(name="artifact.create")
async def _artifact_create(_payload: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    return {"artifact_id": "art-mm1209"}, {"upload_url": "memory://mm1209"}


@activity.defn(name="artifact.write_complete")
async def _artifact_write(_payload: Any) -> dict[str, str]:
    return {"artifact_id": "art-mm1209"}


@activity.defn(name="resilience.compile_policy")
async def _compile_policy(_payload: Any) -> dict[str, Any]:
    return _mock_resilience_policy_envelope(_payload)


@activity.defn(name="execution.record_terminal_state")
async def _record_terminal_state(_payload: Any) -> dict[str, bool]:
    return {"recorded": True}


@activity.defn(name="merge_automation.evaluate_readiness")
async def _readiness(_payload: dict[str, Any]) -> dict[str, Any]:
    global _ci_wait_observed
    if _terminal_evidence_calls >= 2:
        return {
            "headSha": "abcdef1",
            "ready": False,
            "pullRequestOpen": False,
            "pullRequestMerged": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
        }
    if _scenario == "ci_failure_queued" and _terminal_evidence_calls == 0:
        return {
            "actionableCiFailuresVersion": "v1",
            "readinessObservationId": activity.info().activity_id,
            "headSha": "abcdef1",
            "ready": False,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": False,
            "checksPassing": False,
            "blockers": [
                {
                    "kind": "checks_failed",
                    "summary": "Required test job failed",
                    "retryable": True,
                    "source": "github",
                },
                {
                    "kind": "checks_running",
                    "summary": "Downstream workflow is queued",
                    "retryable": True,
                    "source": "github",
                },
            ],
        }
    if (
        _scenario == "ci_wait"
        and _terminal_evidence_calls == 1
        and not _ci_wait_observed
    ):
        _ci_wait_observed = True
        return {
            "headSha": "abcdef1",
            "ready": False,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": False,
            "checksPassing": False,
            "blockers": [
                {
                    "kind": "checks_running",
                    "summary": "Self-hosted CI is queued",
                    "retryable": True,
                    "source": "github",
                }
            ],
        }
    return {
        "headSha": "abcdef1",
        "ready": True,
        "pullRequestOpen": True,
        "policyAllowed": True,
        "checksComplete": True,
        "checksPassing": True,
        "automatedReviewComplete": True,
        "jiraStatusAllowed": True,
    }


@activity.defn(name="merge_automation.request_automated_review")
async def _request_automated_review(_payload: dict[str, Any]) -> dict[str, Any]:
    assert _scenario == "review", "batch adoption must not request a fresh review"
    return {
        "status": "requested",
        "requestCommentId": 98765,
        "requestedAt": "2026-08-26T23:59:00Z",
    }


_terminal_evidence_calls = 0
_scenario = "review"
_ci_wait_observed = False


@activity.defn(name="agent_runtime.evaluate_terminal_evidence")
async def _terminal_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    global _terminal_evidence_calls
    _terminal_evidence_calls += 1
    contract = payload["terminalContract"]
    if _terminal_evidence_calls == 1:
        if _scenario in {"ci_wait", "ci_failure_queued"}:
            return {
                "summary": "CI wait returned to durable owner",
                "failureClass": "execution_error",
                "providerErrorCode": "PR_RESOLVER_REENTER_GATE",
                "metadata": {
                    "terminalContractOutcome": "continuation_requested",
                    "terminalContractExecutionRef": contract["executionRef"],
                    "mergeAutomationDisposition": "reenter_gate",
                    "gatedContinuation": {
                        "schemaVersion": "gated-continuation/v1",
                        "gateType": "merge_automation",
                        "action": "reenter_gate",
                        "executionRef": contract["executionRef"],
                        "headSha": "abcdef1",
                        "retryAfterSeconds": 1,
                        "reason": "ci_running",
                    },
                },
            }
        return {
            "summary": "durable continuation requested",
            "failureClass": "execution_error",
            "providerErrorCode": "PR_RESOLVER_REENTER_GATE",
            "metadata": {
                "terminalContractOutcome": "continuation_requested",
                "terminalContractEvidencePath": "var/pr_resolver/result.json",
                "terminalContractExecutionRef": contract["executionRef"],
                "mergeAutomationDisposition": "request_review",
                "gatedContinuation": {
                    "schemaVersion": "gated-continuation/v2",
                    "gateType": "merge_automation",
                    "action": "request_review",
                    "provider": "codex",
                    "reason": "fresh_review_required_after_remediation",
                    "executionRef": contract["executionRef"],
                    "headSha": "abcdef1",
                    "progressSignature": "abcdef1||",
                },
            },
        }
    return {
        "summary": "merged",
        "metadata": {
            "terminalContractOutcome": "terminal_success",
            "terminalContractSatisfied": True,
            "mergeAutomationDisposition": "merged",
            "push_status": "pushed",
            "push_branch": "feature",
            "pull_request_url": (
                "https://github.com/MoonLadderStudios/MoonMind/pull/1209"
            ),
        },
    }


@activity.defn(name="agent_skill.resolve")
async def _resolve_skill(*_args: Any) -> dict[str, Any]:
    return {
        "manifestRef": "art-skill-mm1209",
        "skills": [{"name": "pr-resolver"}],
    }


async def _register_search_attributes(env: WorkflowEnvironment) -> None:
    await env.client.operator_service.add_search_attributes(
        AddSearchAttributesRequest(
            namespace=env.client.namespace,
            search_attributes={
                "mm_owner_id": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_owner_type": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_entry": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_repo": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_state": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_updated_at": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
                "mm_started_at": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
                "mm_scheduled_for": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
                "mm_has_dependencies": IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
                "mm_dependency_count": IndexedValueType.INDEXED_VALUE_TYPE_INT,
                "mm_current_step_order": IndexedValueType.INDEXED_VALUE_TYPE_INT,
                "mm_integration": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_target_runtime": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
                "mm_target_skill": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
                "mm_title": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
                "mm_provider_profile": IndexedValueType.INDEXED_VALUE_TYPE_TEXT,
            },
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["review", "ci_wait", "ci_failure_queued"])
async def test_real_three_workflow_topology_requests_review_then_merges(
    scenario,
) -> None:
    global _terminal_evidence_calls, _scenario, _ci_wait_observed
    _scenario = scenario
    _ci_wait_observed = False
    _terminal_evidence_calls = 0
    parent_id = "mm1209-full-topology"
    child_queue = "mm.workflow.user.v2"
    resolver_base = deterministic_resolver_idempotency_key(
        parent_workflow_id="user-parent",
        repo="MoonLadderStudios/MoonMind",
        pr_number=1209,
        head_sha="abcdef1",
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _register_search_attributes(env)
        common = [
            *(
                item
                for item in _COMMON_AGENT_RUN_ACTIVITIES
                if item
                not in {
                    mock_provider_profile_list,
                    mock_agent_runtime_status,
                    mock_agent_runtime_fetch_result,
                }
            ),
            _ready_profiles,
            _running_status,
            _completed_result,
            _terminal_evidence,
            _resolve_skill,
        ]
        async with (
            Worker(env.client, task_queue=LLM_TASK_QUEUE, activities=[_plan_generate]),
            Worker(
                env.client,
                task_queue=ARTIFACTS_TASK_QUEUE,
                activities=[
                    _artifact_read,
                    _artifact_create,
                    _artifact_write,
                    _compile_policy,
                    _record_terminal_state,
                    *common,
                ],
            ),
            Worker(env.client, task_queue=AGENT_RUNTIME_TASK_QUEUE, activities=common),
            Worker(
                env.client,
                task_queue=(
                    settings.temporal.activity_agent_runtime_control_task_queue
                ),
                activities=[_terminal_evidence],
            ),
            Worker(
                env.client,
                task_queue=INTEGRATIONS_TASK_QUEUE,
                activities=[_readiness, _request_automated_review],
            ),
            Worker(
                env.client,
                task_queue=child_queue,
                workflows=[
                    MoonMindUserWorkflow,
                    MoonMindAgentRun,
                    MockProviderProfileManager,
                ],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue="mm1209-parent",
                workflows=[MoonMindMergeAutomationWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
        ):
            await env.client.start_workflow(
                MockProviderProfileManager.run,
                {"runtime_id": "claude_code", "default_profile_id": "claude-managed"},
                id="provider-profile-manager:claude_code",
                task_queue=child_queue,
            )
            handle = await env.client.start_workflow(
                MoonMindMergeAutomationWorkflow.run,
                {
                    "workflowType": "MoonMind.MergeAutomation",
                    "parentWorkflowId": "user-parent",
                    "publishContextRef": "artifact://publish-context",
                    "pullRequest": {
                        "repo": "MoonLadderStudios/MoonMind",
                        "number": 1209,
                        "url": "https://github.com/MoonLadderStudios/MoonMind/pull/1209",
                        "headSha": "abcdef1",
                        "headBranch": "feature",
                        "baseBranch": "main",
                    },
                    "mergeAutomationConfig": {
                        "gate": {
                            "github": {
                                "automatedReview": "required" if scenario == "review" else "disabled"
                            }
                        },
                        "timeouts": {"fallbackPollSeconds": 2},
                        "reviewLoop": {
                            "enabled": scenario == "review",
                            "provider": "codex",
                            "maxCycles": 2,
                        },
                    },
                    "resolverTemplate": {
                        "targetRuntime": "claude_code",
                        **(
                            {"inputs": {"maxIterations": 7, "returnToGate": True}}
                            if scenario in {"ci_wait", "ci_failure_queued"}
                            else {}
                        ),
                    },
                },
                id=parent_id,
                task_queue="mm1209-parent",
            )

            async def complete_agent(cycle: int) -> None:
                agent_id = f"{resolver_base}:{cycle}:agent:resolver"
                agent = env.client.get_workflow_handle(agent_id)
                for _ in range(100):
                    try:
                        await agent.signal(
                            MoonMindAgentRun.completion_signal,
                            {"summary": f"resolver cycle {cycle} completed"},
                        )
                        return
                    except Exception:
                        await asyncio.sleep(0.05)
                try:
                    parent_result = await asyncio.wait_for(handle.result(), timeout=2)
                except asyncio.TimeoutError:
                    parent_result = "Timeout (still running)"
                resolver_id = f"{resolver_base}:{cycle}"
                try:
                    resolver_result = await env.client.get_workflow_handle(
                        resolver_id
                    ).result()
                except Exception as exc:
                    messages = []
                    current: BaseException | None = exc
                    while current is not None:
                        messages.append(f"{type(current).__name__}: {current}")
                        current = getattr(current, "cause", None) or current.__cause__
                    resolver_result = " <- ".join(messages)
                try:
                    first_resolver = await env.client.get_workflow_handle(
                        f"{resolver_base}:1"
                    ).result()
                except Exception as exc:
                    first_messages = []
                    current = exc
                    while current is not None:
                        first_messages.append(f"{type(current).__name__}: {current}")
                        current = getattr(current, "cause", None) or current.__cause__
                    first_resolver = " <- ".join(first_messages)
                raise AssertionError(
                    f"AgentRun child did not start: {agent_id}; "
                    f"resolver={resolver_result}; first={first_resolver}; "
                    f"parent={parent_result}"
                )

            await complete_agent(1)
            await complete_agent(2)
            result = await asyncio.wait_for(handle.result(), timeout=30)
            try:
                second_result = await env.client.get_workflow_handle(
                    f"{resolver_base}:2"
                ).result()
            except Exception as exc:
                second_messages = []
                current: BaseException | None = exc
                while current is not None:
                    second_messages.append(f"{type(current).__name__}: {current}")
                    current = getattr(current, "cause", None) or current.__cause__
                second_result = " <- ".join(second_messages)
            first_description = await env.client.get_workflow_handle(
                f"{resolver_base}:1"
            ).describe()
            second_description = await env.client.get_workflow_handle(
                f"{resolver_base}:2"
            ).describe()
            first_memo = await first_description.memo()
            second_memo = await second_description.memo()
            history = await handle.fetch_history()

    assert result["status"] == "merged", (
        result.get("summary"),
        result.get("blockers"),
        second_result,
    )
    assert result["cycles"] == 2
    assert result["continuationCounters"]["continuation_cycle_completed"] == 1
    if scenario == "review":
        assert result["reviewLoop"]["cycles"] == 1
    else:
        assert "reviewLoop" not in result
    assert _terminal_evidence_calls == 2
    assert first_memo["title"] == "Resolve PR #1209 (Attempt 1)"
    assert second_memo["title"] == "Resolve PR #1209 (Attempt 2)"
    if scenario == "ci_wait":
        assert _ci_wait_observed
        assert any(
            event.HasField("timer_started_event_attributes") for event in history.events
        )
    elif scenario == "ci_failure_queued":
        first_child_started = next(
            event.event_id
            for event in history.events
            if event.HasField("start_child_workflow_execution_initiated_event_attributes")
        )
        assert not any(
            event.HasField("timer_started_event_attributes")
            and event.event_id < first_child_started
            for event in history.events
        ), "Failed CI must dispatch the resolver before waiting for queued downstream checks"
    await Replayer(
        workflows=[MoonMindMergeAutomationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)
