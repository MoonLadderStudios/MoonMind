"""Hermetic product boundary for MoonLadderStudios/MoonMind#3705 and #3706."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from temporalio import activity
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.api.routers.executions import (
    _get_service,
    get_temporal_client,
    router,
)
from api_service.db.base import get_async_session
from moonmind.omnigent.reconciler import (
    CompiledSessionIntent,
    DesiredLifecycle,
    DurableSessionState,
    EventFrontierObservation,
    EvidenceObservation,
    HostObservation,
    LeaseObservation,
    LeaseState,
    ObservationSet,
    ProviderSessionObservation,
    SubmissionState,
    TerminalOutcome,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    OmnigentExecutionPlanBinding,
)
from moonmind.schemas.omnigent_session_models import OmnigentSessionWorkflowInput
from moonmind.schemas.resilience_policy_models import compile_resilience_policy
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_TASK_QUEUE,
    ARTIFACTS_TASK_QUEUE,
    LLM_TASK_QUEUE,
    build_default_activity_catalog,
    get_workflow_task_queue,
)
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows import (
    omnigent_session as session_module,
)
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.omnigent_session import (
    MoonMindOmnigentSessionWorkflow,
    canonical_omnigent_session_id,
    canonical_omnigent_turn_attempt_id,
    omnigent_session_workflow_id,
)
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.unit.api.routers.test_executions import (
    _build_execution_record,
    _override_user_dependencies,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.temporal_boundary,
]

_NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
CALLS: list[str] = []
STATE: dict[str, Any] = {}
PRODUCT_INPUTS: dict[str, Any] = {}
PAUSE_PHASE: str | None = None
PHASE_STARTED: asyncio.Event | None = None
PHASE_RELEASE: asyncio.Event | None = None


def _reset_state() -> None:
    CALLS.clear()
    STATE.clear()
    STATE.update(
        revision=1,
        profile=False,
        host=False,
        provider_session=False,
        observed_provider_session_id="provider-session-1",
        submitted=False,
        provider_status=None,
        event_read_count=0,
        snapshot_count=0,
        load_count=0,
        terminal=False,
        terminal_outcome="success",
        evidence=False,
        cleanup=False,
        cancel=False,
        admission=True,
        execution_realizer_ref="generic-omnigent-host@1",
        fail_activity=None,
        load_failures_remaining=0,
        reconciler_failed=False,
    )
    PRODUCT_INPUTS.clear()


def _trusted_search_attributes() -> TypedSearchAttributes:
    return TypedSearchAttributes(
        [
            SearchAttributePair(
                SearchAttributeKey.for_keyword("mm_owner_id"),
                "trusted-owner-3705",
            ),
            SearchAttributePair(
                SearchAttributeKey.for_keyword("mm_owner_type"),
                "user",
            ),
        ]
    )


@activity.defn(name="plan.generate")
async def _generate_product_plan(payload: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("plan_generate")
    PRODUCT_INPUTS.update(dict(payload.get("parameters") or {}))
    return {"plan_ref": "artifact://plan/omnigent-session-3705"}


@activity.defn(name="artifact.read")
async def _read_product_plan(payload: dict[str, Any]) -> bytes:
    CALLS.append("plan_read")
    artifact_ref = str(
        payload.get("artifact_ref") or payload.get("artifactRef") or ""
    )
    if artifact_ref == "artifact://registry/omnigent-session-3705":
        return json.dumps({"skills": []}).encode("utf-8")
    runtime = dict(PRODUCT_INPUTS.get("workflow", {}).get("runtime") or {})
    workflow_input = dict(PRODUCT_INPUTS.get("workflow") or {})
    plan = {
        "plan_version": "1.0",
        "metadata": {
            "title": "Omnigent session product path",
            "created_at": "2026-08-18T00:00:00Z",
            "registry_snapshot": {
                "digest": "reg:sha256:" + ("a" * 64),
                "artifact_ref": "artifact://registry/omnigent-session-3705",
            },
        },
        "policy": {"failure_mode": "FAIL_FAST"},
        "nodes": [
            {
                "id": "omnigent-session-step",
                "tool": {"type": "agent_runtime", "name": "omnigent"},
                "inputs": {
                    "targetRuntime": PRODUCT_INPUTS.get(
                        "targetRuntime", "omnigent"
                    ),
                    "instructions": workflow_input.get(
                        "instructions", "Apply the requested repository change."
                    ),
                    "runtime": runtime,
                    "repository": PRODUCT_INPUTS.get("repository"),
                },
            }
        ],
    }
    return json.dumps(plan).encode("utf-8")


@activity.defn(name="artifact.create")
async def _create_product_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    artifact_id = str(payload.get("artifact_id") or "omnigent-session-3705")
    return {"artifact_id": artifact_id, "artifact_ref": f"artifact://{artifact_id}"}


@activity.defn(name="artifact.write_complete")
async def _complete_product_artifact(_payload: dict[str, Any]) -> dict[str, Any]:
    return {"status": "complete"}


@activity.defn(name="provider_profile.list")
async def _list_product_profiles(_payload: dict[str, Any]) -> dict[str, Any]:
    return {"profiles": []}


@activity.defn(name="resilience.compile_policy")
async def _compile_product_policy(payload: dict[str, Any]) -> dict[str, Any]:
    return compile_resilience_policy(
        compiled_at=datetime.fromisoformat(payload["compiledAt"]),
        workflow_id=payload.get("workflowId"),
        run_id=payload.get("runId"),
        policy_version=int(payload.get("policyVersion") or 1),
        attempts={
            "stepMaxAttempts": 3,
            "stepNoProgressLimit": 2,
            "jobSelfHealMaxResets": 1,
        },
        timeouts={
            "stepTimeoutSeconds": 900,
            "stepIdleTimeoutSeconds": 300,
        },
        provider_cooldown={
            "cooldownAfter429Seconds": payload.get(
                "cooldownAfter429Seconds", 900
            ),
            "providerProfileId": payload.get("providerProfileId"),
            "rateLimitPolicy": payload.get("rateLimitPolicy") or {},
        },
        checkpoints={
            "checkpointRequired": True,
            "requiredBoundaries": [
                "after_prepare",
                "before_execution",
                "after_execution",
            ],
        },
        idempotency={
            "sideEffectIdempotencyRequired": True,
            "keyStrategy": "step_execution_operation",
        },
        outbound_scanning={
            "highSecurityMode": False,
            "blockOnFinding": False,
        },
        observability={
            "liveLogsTimelineEnabled": False,
            "structuredHistoryEnabled": True,
        },
        cost_attribution={"runtimeId": payload.get("runtimeId")},
    ).model_dump(by_alias=True, mode="json")


@activity.defn(name="execution.record_terminal_state")
async def _record_product_terminal_state(
    _payload: dict[str, Any],
) -> dict[str, bool]:
    return {"recorded": True}


@activity.defn(name="integration.resolve_adapter_metadata")
async def _resolve_adapter_metadata(agent_id: str) -> dict[str, Any]:
    CALLS.append("resolve_adapter")
    return {
        "agent_id": agent_id,
        "execution_style": "streaming_gateway",
        "supports_callbacks": False,
    }


@activity.defn(name="integration.omnigent.profile_bound_execute")
async def _legacy_profile_bound_execute(
    _request: AgentExecutionRequest,
) -> AgentRunResult:
    CALLS.append("legacy_profile_bound_execute")
    return AgentRunResult(summary="Legacy profile-bound execution completed")


@activity.defn(name="agent_skill.resolve")
async def _resolve_empty_skillset(*_args: Any) -> dict[str, Any]:
    return {"manifestRef": "art_skillset_omnigent_3705", "skills": []}


@activity.defn(name="omnigent.resolve_intent")
async def _resolve_intent(payload: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("resolve_intent")
    STATE["workflow_id"] = str(payload["workflowId"])
    STATE["step_execution_id"] = str(payload["stepExecutionId"])
    STATE["agent_run_id"] = str(payload["agentRunId"])
    assert "request" not in payload
    execution_plan = payload.get("omnigentExecutionPlan")
    STATE["execution_plan"] = execution_plan
    session_id = canonical_omnigent_session_id(
        workflow_id=str(payload["workflowId"]),
        step_execution_id=str(payload["stepExecutionId"]),
        agent_run_id=str(payload["agentRunId"]),
    )
    return OmnigentSessionWorkflowInput(
        sessionId=session_id,
        compiledExecutionIntentRef="art_intent_product_path",
        compiledExecutionIntentDigest="sha256:" + "a" * 64,
        workflowId=str(payload["workflowId"]),
        stepExecutionId=str(payload["stepExecutionId"]),
        agentRunId=str(payload["agentRunId"]),
        initialTurnAttemptId=canonical_omnigent_turn_attempt_id(session_id),
        admittedFeatureGeneration="omnigent-session-v1",
        compatibilityVersion="v1",
        omnigentExecutionPlan=execution_plan,
    ).model_dump(mode="json", by_alias=True)


@activity.defn(name="omnigent.evaluate_session_admission")
async def _evaluate_session_admission(payload: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("evaluate_session_admission")
    STATE["admission_plan"] = payload.get("omnigentExecutionPlan")
    decision = {
        "admitted": bool(STATE["admission"]),
        "reasonCode": (
            "enabled" if STATE["admission"] else "new_selection_disabled"
        ),
        "admissionMode": "enabled" if STATE["admission"] else "disabled",
        "admittedFeatureGeneration": "omnigent-session-v1",
    }
    if payload.get("omnigentExecutionPlan") is not None:
        decision["executionRealizerRef"] = STATE["execution_realizer_ref"]
    return decision


@activity.defn(name="integration.omnigent.execute")
async def _execute_recorded_plan_realizer(
    request: AgentExecutionRequest,
) -> AgentRunResult:
    CALLS.append("execute_recorded_plan_realizer")
    STATE["codex_dispatch_plan"] = request.omnigent_execution_plan.model_dump(
        mode="json", by_alias=True
    )
    return AgentRunResult(summary="Recorded Codex realizer completed")


@activity.defn(name="omnigent.load_reconciliation_inputs")
async def _load_reconciliation_inputs(
    payload: dict[str, Any],
) -> dict[str, Any]:
    CALLS.append("load")
    STATE["load_count"] = int(STATE["load_count"]) + 1
    if int(STATE["load_failures_remaining"]) > 0:
        STATE["load_failures_remaining"] = (
            int(STATE["load_failures_remaining"]) - 1
        )
        raise ApplicationError(
            "injected exhausted reconciliation input load",
            type="InjectedBoundedActivityFailure",
            non_retryable=True,
        )
    if STATE["load_count"] > 50:
        raise ApplicationError(
            f"supervisor did not converge: state={STATE}, calls={CALLS[-20:]}",
            non_retryable=True,
        )
    session_id = str(payload["sessionId"])
    leases_held = bool(STATE["profile"])
    durable = DurableSessionState(
        sessionId=session_id,
        revision=int(STATE["revision"]),
        ownerToken=f"omnigent-session:{session_id}",
        fencingGeneration=1,
        desired=(
            DesiredLifecycle.CANCEL
            if STATE["cancel"]
            else DesiredLifecycle.RUN
        ),
        providerSessionAttached=bool(STATE["provider_session"]),
        providerSessionId=(
            "provider-session-1" if STATE["provider_session"] else None
        ),
        attemptId=canonical_omnigent_turn_attempt_id(session_id),
        submission=(
            SubmissionState.ACCEPTED
            if STATE["submitted"]
            else SubmissionState.NOT_SUBMITTED
        ),
        turnAttempts=0,
        profileLease=LeaseState.HELD if leases_held else LeaseState.NONE,
        hostLease=(
            LeaseState.HELD if STATE["host"] else LeaseState.NONE
        ),
        terminalOutcome=(
            (
                TerminalOutcome.CANCELLED
                if STATE["cancel"]
                else (
                    TerminalOutcome.FAILURE
                    if STATE["terminal_outcome"] == "failure"
                    else TerminalOutcome.SUCCESS
                )
            )
            if STATE["terminal"]
            else None
        ),
        terminalEvidenceRef=(
            "art_terminal_result" if STATE["evidence"] else None
        ),
        evidenceHarvested=bool(STATE["evidence"]),
        cleanupStarted=bool(STATE["cleanup"]),
        cleanupComplete=bool(STATE["cleanup"]),
        failed=bool(STATE["reconciler_failed"]) and not bool(STATE["terminal"]),
    )
    observations = ObservationSet(
        providerSession=(
            ProviderSessionObservation(
                observedAt=_NOW,
                present=True,
                providerSessionId=str(STATE["observed_provider_session_id"]),
                rawStatus=str(STATE["provider_status"]),
                snapshotDigest=f"snapshot-{STATE['snapshot_count']}",
            )
            if STATE["provider_status"] is not None
            else None
        ),
        eventFrontier=(
            EventFrontierObservation(
                observedAt=_NOW,
                terminalEventSeen=False,
            )
            if STATE["provider_status"] == "completed"
            else None
        ),
        evidence=(
            EvidenceObservation(
                observedAt=_NOW,
                terminalEvidenceAvailable=True,
                artifactsAvailable=bool(STATE["evidence"]),
            )
            if STATE["terminal"]
            else None
        ),
        host=(
            HostObservation(
                observedAt=_NOW,
                registered=not bool(STATE["cleanup"]),
                runnerReady=not bool(STATE["cleanup"]),
            )
            if STATE["host"]
            else None
        ),
        profileLease=(
            LeaseObservation(
                observedAt=_NOW,
                held=leases_held,
                consumerActive=not bool(STATE["cleanup"]),
            )
            if leases_held
            else None
        ),
        hostLease=(
            LeaseObservation(
                observedAt=_NOW,
                held=bool(STATE["host"]),
                consumerActive=not bool(STATE["cleanup"]),
            )
            if STATE["host"]
            else None
        ),
    )
    response: dict[str, Any] = {
        "intent": CompiledSessionIntent(
            sessionId=session_id,
            provider="omnigent",
            maxTurnAttempts=1,
            reconcileIntervalSeconds=1,
            turnPromptDigest="sha256:prompt",
        ).model_dump(mode="json", by_alias=True),
        "durable": durable.model_dump(mode="json", by_alias=True),
        "observations": observations.model_dump(
            mode="json", by_alias=True, exclude_none=True
        ),
        "phase": "reconciling",
    }
    if STATE["evidence"] and not STATE["profile"] and not STATE["host"]:
        canceled = bool(STATE["cancel"])
        failure_status = str(STATE.get("failure_status") or "").strip()
        failed = bool(failure_status) or STATE["terminal_outcome"] == "failure"
        integration_failure = failure_status in {
            "integration_unavailable",
            "delivery_unknown",
        }
        response["terminalResult"] = {
            "status": (
                "canceled"
                if canceled
                else failure_status or "execution_failed"
                if failed
                else "completed"
            ),
            "resultRef": "art_terminal_result",
            "result": {
                "summary": (
                    "Omnigent session canceled through supervisor"
                    if canceled
                    else "Omnigent session failed through supervisor"
                    if failed
                    else "Omnigent session completed through supervisor"
                ),
                "failureClass": (
                    "canceled"
                    if canceled
                    else (
                        "integration_error"
                        if integration_failure
                        else "execution_error"
                    )
                    if failed
                    else None
                ),
                "metadata": {
                    "canonicalSessionId": session_id,
                    **(
                        {
                            "omnigentSessionStatus": (
                                failure_status or "execution_failed"
                            )
                        }
                        if failed
                        else {}
                    ),
                },
            },
        }
    return response


@activity.defn(name="omnigent.load_failure_authority")
async def _load_failure_authority(payload: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("load_failure_authority")
    return {
        "sessionId": str(payload["sessionId"]),
        "revision": int(STATE["revision"]),
        "fencingGeneration": 1,
    }


@activity.defn(name="omnigent.persist_decision")
async def _persist_decision(payload: dict[str, Any]) -> dict[str, Any]:
    decision = dict(payload["decision"])
    CALLS.append(
        f"decision:{decision['kind']}:{decision['reasonCode']}"
    )
    return {"decisionId": payload["decisionId"]}


@activity.defn(name="omnigent.persist_signal_intents")
async def _persist_signals(payload: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("persist_signals")
    if not STATE["terminal"]:
        for item in payload.get("signals") or []:
            if item.get("kind") in {
                "cancel_or_interrupt_requested",
                "cleanup_requested",
                "timeout_requested",
            }:
                STATE["cancel"] = True
                STATE["revision"] = int(STATE["revision"]) + 1
    return {"appliedIntentCount": len(payload.get("signals") or [])}


@activity.defn(name="omnigent.persist_failure")
async def _persist_failure(payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload["status"])
    CALLS.append(f"persist_failure:{status}:{payload['failedActivity']}")
    session_id = str(payload["sessionId"])
    if status == "cleanup_incomplete":
        STATE["cleanup_incomplete"] = True
        primary_status = str(
            STATE.get("failure_status")
            or (
                "execution_failed"
                if STATE["terminal_outcome"] == "failure"
                else "completed"
            )
        )
        primary_failure_class = (
            "integration_error"
            if primary_status
            in {
                "integration_unavailable",
                "delivery_unknown",
                "reconciliation_quarantined",
            }
            else "execution_error"
            if primary_status == "execution_failed"
            else None
        )
        result = AgentRunResult(
            summary=(
                "Omnigent session completed through supervisor"
                if primary_status == "completed"
                else f"Omnigent session {primary_status}"
            ),
            failureClass=primary_failure_class,
            metadata={
                "canonicalSessionId": session_id,
                "omnigentSessionStatus": "cleanup_incomplete",
                "primaryOmnigentSessionStatus": primary_status,
                "cleanupEvidenceRef": "art_cleanup_incomplete",
                "cleanupOwner": "integration.omnigent.oauth_host_janitor",
                "janitorRequired": True,
            },
            outputRefs=["art_terminal_result", "art_cleanup_incomplete"],
        )
        result_ref = "art_cleanup_incomplete"
    else:
        already_terminal = bool(STATE["terminal"])
        STATE["terminal"] = True
        if not already_terminal:
            STATE["terminal_outcome"] = "failure"
        STATE["failure_status"] = status
        STATE["evidence"] = True
        STATE["revision"] = int(STATE["revision"]) + 1
        result = AgentRunResult(
            summary=f"Omnigent session {status}",
            failureClass=(
                "integration_error"
                if status != "execution_failed"
                else "execution_error"
            ),
            metadata={
                "canonicalSessionId": session_id,
                "omnigentSessionStatus": status,
                "reasonCode": str(payload["reasonCode"]),
            },
            outputRefs=["art_failure_result"],
        )
        result_ref = "art_failure_result"
    return {
        "terminalResultRef": result_ref,
        "terminalResult": {
            "status": status,
            "resultRef": result_ref,
            "result": result.model_dump(mode="json", by_alias=True),
        },
    }


def _phase_activity(name: str, mutation: str | None = None):
    async def handler(_payload: dict[str, Any]) -> dict[str, Any]:
        CALLS.append(name)
        if (
            PAUSE_PHASE == name
            and PHASE_STARTED is not None
            and PHASE_RELEASE is not None
        ):
            PHASE_STARTED.set()
            await PHASE_RELEASE.wait()
        if STATE.get("fail_activity") == name:
            raise ApplicationError(
                f"injected exhausted {name}",
                type="InjectedBoundedActivityFailure",
                non_retryable=True,
            )
        if mutation is not None:
            STATE[mutation] = True
            STATE["revision"] = int(STATE["revision"]) + 1
        return {
            "outcome": "applied",
            **(
                {"terminalResultRef": "art_terminal_result"}
                if name == "harvest_evidence"
                else {}
            ),
        }

    return activity.defn(name=f"omnigent.{name}")(handler)


_ensure_profile = _phase_activity("ensure_provider_profile_lease", "profile")
_ensure_host = _phase_activity("ensure_host", "host")
_ensure_session = _phase_activity("ensure_provider_session", "provider_session")
_submit_turn = _phase_activity("submit_turn", "submitted")
_record_terminal = _phase_activity("record_terminal", "terminal")
_harvest_evidence = _phase_activity("harvest_evidence", "evidence")
_publish_workspace = _phase_activity("publish_workspace")
_stop_provider = _phase_activity("stop_provider_session")
_stop_host = _phase_activity("stop_host", "cleanup")


@activity.defn(name="omnigent.release_leases")
async def _release_leases(_payload: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("release_leases")
    STATE["profile"] = False
    STATE["host"] = False
    STATE["revision"] = int(STATE["revision"]) + 1
    return {"outcome": "applied"}


@activity.defn(name="omnigent.heartbeat_host_lease")
async def _heartbeat_host_lease(_payload: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("heartbeat_host_lease")
    return {
        "hostLeaseHeartbeat": "renewed" if STATE["host"] else "not_attached"
    }


@activity.defn(name="omnigent.read_event_batch")
async def _read_event_batch(_payload: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("read_event_batch")
    STATE["event_read_count"] = int(STATE["event_read_count"]) + 1
    if STATE["event_read_count"] == 1:
        return {"observationCount": 0, "readStatus": "unavailable"}
    return {"observationCount": 0}


@activity.defn(name="omnigent.observe_snapshot")
async def _observe_snapshot(_payload: dict[str, Any]) -> dict[str, Any]:
    CALLS.append("observe_snapshot")
    STATE["snapshot_count"] = int(STATE["snapshot_count"]) + 1
    if STATE["snapshot_count"] == 1:
        return {
            "observationCount": 0,
            "readStatus": "unavailable",
            "snapshotFrontier": None,
        }
    if STATE["snapshot_count"] == 2:
        STATE["provider_status"] = "new_provider_status"
    elif STATE["snapshot_count"] == 3:
        # A provider restart may briefly surface a snapshot from another
        # provider-session epoch. It must wake reconciliation but cannot
        # terminalize or re-submit this canonical session.
        STATE["provider_status"] = "running"
        STATE["observed_provider_session_id"] = "provider-session-after-restart"
    else:
        STATE["provider_status"] = "completed"
        STATE["observed_provider_session_id"] = "provider-session-1"
    return {
        "observationCount": 1,
        "snapshotFrontier": f"snapshot-{STATE['snapshot_count']}",
    }


@activity.defn(name="agent_runtime.publish_artifacts")
async def _publish_artifacts(result: AgentRunResult) -> AgentRunResult:
    CALLS.append("publish_artifacts")
    return result


async def _register_search_attributes(env: WorkflowEnvironment) -> None:
    await env.client.operator_service.add_search_attributes(
        AddSearchAttributesRequest(
            namespace=env.client.namespace,
            search_attributes={
                "mm_owner_id": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_owner_type": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_state": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_entry": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_updated_at": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
                "mm_started_at": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
                "mm_scheduled_for": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
                "mm_has_dependencies": IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
                "mm_dependency_count": IndexedValueType.INDEXED_VALUE_TYPE_INT,
                "mm_current_step_order": IndexedValueType.INDEXED_VALUE_TYPE_INT,
                "mm_repo": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_integration": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_target_runtime": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
                "mm_target_skill": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
                "mm_title": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
                "AgentRunId": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "SessionId": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "SessionStatus": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "IsDegraded": IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
            },
        )
    )


def _direct_session_input(session_id: str) -> OmnigentSessionWorkflowInput:
    return OmnigentSessionWorkflowInput(
        sessionId=session_id,
        compiledExecutionIntentRef=f"art_intent_{session_id}",
        compiledExecutionIntentDigest="sha256:" + "c" * 64,
        workflowId=f"workflow-{session_id}",
        stepExecutionId=f"step-{session_id}",
        agentRunId=f"agent-run-{session_id}",
        initialTurnAttemptId=canonical_omnigent_turn_attempt_id(session_id),
        admittedFeatureGeneration="omnigent-session-v1",
        compatibilityVersion="v1",
    )


def _direct_session_activities() -> list[Any]:
    return [
        _load_reconciliation_inputs,
        _load_failure_authority,
        _persist_decision,
        _persist_signals,
        _persist_failure,
        _ensure_profile,
        _ensure_host,
        _ensure_session,
        _submit_turn,
        _record_terminal,
        _harvest_evidence,
        _publish_workspace,
        _stop_provider,
        _stop_host,
        _release_leases,
        _heartbeat_host_lease,
        _read_event_batch,
        _observe_snapshot,
    ]


async def test_product_compiled_agent_run_converges_after_lost_terminal_event() -> None:
    """The #3706 product plan reaches admission and the session supervisor."""

    _reset_state()
    app = FastAPI()
    app.include_router(router)
    execution_service = AsyncMock()
    execution_service.create_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: execution_service
    app.dependency_overrides[get_temporal_client] = AsyncMock
    provider_profile = SimpleNamespace(
        profile_id="opencode-go-default", provider_id="opencode-go"
    )
    db_session = SimpleNamespace(
        get=AsyncMock(side_effect=[provider_profile, None]),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    app.dependency_overrides[get_async_session] = lambda: db_session
    _override_user_dependencies(app, is_superuser=False)
    profile_snapshot = {
        "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
        "profileId": "opencode-default",
        "version": 1,
        "digest": "sha256:" + "a" * 64,
        "providerProfileRef": "opencode-go-default",
        "executionProfileRef": "omnigent-opencode@1",
        "allowedLaunchPolicyRefs": ["opencode-on-demand@1"],
        "launchPolicyRef": "opencode-on-demand@1",
        "agentId": "opencode-agent",
        "policyRef": "omnigent-policy:sha256:" + "d" * 64,
        "document": {
            "endpointRef": "default",
            "model": {"model": "opencode/model", "settings": {}},
            "rag": {},
            "capture": {"stream": True},
            "workspace": {"mutation": "allowed"},
            "harness": "opencode-native",
        },
    }
    plan_binding = OmnigentExecutionPlanBinding(
        planRef="omnigent-execution-plan:sha256:" + "b" * 64,
        planDigest="sha256:" + "b" * 64,
        planArtifactRef="art_opencode_plan_3706",
        taskInputSnapshotRef="art_opencode_task_3706",
        taskInputSnapshotDigest="sha256:" + "c" * 64,
    )
    compiled_plan = SimpleNamespace(
        binding=plan_binding,
        artifact_refs=(
            "art_profile_3706",
            "art_skills_3706",
            "art_opencode_plan_3706",
        ),
        resolved_skillset_ref="art_skills_3706",
    )
    with (
        patch(
            "api_service.api.routers.executions.resolve_agent_profile_snapshot",
            new=AsyncMock(return_value=profile_snapshot),
        ),
        patch(
            "api_service.services.omnigent_execution_plan_service.persist_json_artifact",
            new=AsyncMock(
                return_value=("art_opencode_task_3706", "sha256:" + "c" * 64)
            ),
        ),
        patch(
            "api_service.services.omnigent_execution_plan_service.compile_and_persist_execution_plan",
            new=AsyncMock(return_value=compiled_plan),
        ),
        patch(
            "api_service.api.routers.executions.get_temporal_artifact_service",
            return_value=SimpleNamespace(),
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            "/api/executions",
            json={
                "type": "workflow",
                "payload": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "omnigent",
                    "agentProfile": {
                        "profileId": "opencode-default",
                        "providerProfileRef": "opencode-go-default",
                    },
                    "omnigent": {
                        "executionTargetRef": "omnigent-opencode@1",
                        "launchPolicyRef": "opencode-on-demand@1",
                    },
                    "workflow": {
                        "instructions": "Apply the requested repository change.",
                        "runtime": {"mode": "omnigent"},
                    },
                },
            },
        )
    assert response.status_code == 201
    authored = execution_service.create_execution.await_args.kwargs[
        "initial_parameters"
    ]
    assert authored["omnigentExecutionPlan"] == plan_binding.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )

    workflow_queue = get_workflow_task_queue()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _register_search_attributes(env)
        async with (
            Worker(
                env.client,
                task_queue=LLM_TASK_QUEUE,
                activities=[_generate_product_plan],
            ),
            Worker(
                env.client,
                task_queue=ARTIFACTS_TASK_QUEUE,
                activities=[
                    _read_product_plan,
                    _create_product_artifact,
                    _complete_product_artifact,
                    _list_product_profiles,
                    _compile_product_policy,
                    _record_product_terminal_state,
                ],
            ),
            Worker(
                env.client,
                task_queue=workflow_queue,
                workflows=[
                    MoonMindUserWorkflow,
                    MoonMindAgentRun,
                    MoonMindOmnigentSessionWorkflow,
                ],
                activities=[_resolve_adapter_metadata],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
                activities=[
                    _evaluate_session_admission,
                    _resolve_intent,
                    _load_reconciliation_inputs,
                    _persist_decision,
                    _persist_signals,
                    _persist_failure,
                    _ensure_profile,
                    _ensure_host,
                    _ensure_session,
                    _submit_turn,
                    _record_terminal,
                    _harvest_evidence,
                    _publish_workspace,
                    _stop_provider,
                    _stop_host,
                    _release_leases,
                    _heartbeat_host_lease,
                    _read_event_batch,
                    _observe_snapshot,
                    _publish_artifacts,
                    _resolve_empty_skillset,
                ],
            ),
        ):
            product_workflow_id = f"product-workflow-3705-{uuid4()}"
            handle = await env.client.start_workflow(
                MoonMindUserWorkflow.run,
                {
                    "workflowType": "MoonMind.UserWorkflow",
                    "title": "Omnigent session product path",
                    "initialParameters": authored,
                },
                id=product_workflow_id,
                task_queue=workflow_queue,
                search_attributes=_trusted_search_attributes(),
            )
            async def wait_for_agent_dispatch() -> None:
                while "agent_run_id" not in STATE:
                    await asyncio.sleep(0.05)

            dispatch_task = asyncio.create_task(wait_for_agent_dispatch())
            parent_result_task = asyncio.create_task(handle.result())
            done, _pending = await asyncio.wait(
                {dispatch_task, parent_result_task},
                timeout=20,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if dispatch_task not in done:
                if parent_result_task in done:
                    try:
                        parent_result = parent_result_task.result()
                    except Exception as exc:
                        workflow_cause = getattr(exc, "cause", None)
                        raise AssertionError(
                            "product workflow failed before AgentRun dispatch; "
                            f"calls={CALLS}, cause={workflow_cause!r}, "
                            "nestedCause="
                            f"{getattr(workflow_cause, 'cause', None)!r}"
                        ) from exc
                    raise AssertionError(
                        "product workflow completed before AgentRun dispatch; "
                        f"result={parent_result!r}, calls={CALLS}"
                    )
                raise AssertionError(
                    f"AgentRun dispatch timed out; calls={CALLS}"
                )
            parent_result_task.cancel()
            agent_run_workflow_id = str(STATE["agent_run_id"])
            agent_handle = env.client.get_workflow_handle(agent_run_workflow_id)
            session_id = canonical_omnigent_session_id(
                workflow_id=str(STATE["workflow_id"]),
                step_execution_id=str(STATE["step_execution_id"]),
                agent_run_id=agent_run_workflow_id,
            )
            session_handle = env.client.get_workflow_handle(
                omnigent_session_workflow_id(session_id)
            )
            try:
                await env.sleep(5)
            except Exception as exc:
                raise AssertionError(
                    "time-skipping stalled while the supervisor was active; "
                    f"state={STATE}, calls={CALLS[-40:]}"
                ) from exc
            try:
                result = AgentRunResult.model_validate(
                    await asyncio.wait_for(
                        agent_handle.result(), timeout=20
                    )
                )
            except TimeoutError as exc:
                await session_handle.terminate("supervisor test timeout")
                await agent_handle.terminate("supervisor test timeout")
                await handle.terminate("supervisor test timeout")
                raise AssertionError(
                    "AgentRun did not reach terminal state; "
                    f"state={STATE}, calls={CALLS}"
                ) from exc
            agent_history = await agent_handle.fetch_history()
            query_state = await session_handle.query("omnigent_session.state")
            session_history = await session_handle.fetch_history()
            # The changed AgentRun/session boundary is already terminal and
            # replayable. Stop the broader parent before unrelated post-run
            # publication/checkpoint gates in this intentionally minimal harness.
            await handle.terminate("supervisor boundary verified")

    await Replayer(
        workflows=[MoonMindAgentRun],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(agent_history)
    await Replayer(
        workflows=[MoonMindOmnigentSessionWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(session_history)

    assert result.summary == "Omnigent session completed through supervisor"
    assert query_state["terminalStatus"] == "completed"
    assert query_state["sessionId"] == session_id
    assert "compiledExecutionIntentRef" not in query_state
    assert "plan_generate" in CALLS
    assert "plan_read" in CALLS
    assert "resolve_intent" in CALLS
    assert STATE["execution_plan"] == authored["omnigentExecutionPlan"]
    assert STATE["admission_plan"] == authored["omnigentExecutionPlan"]
    assert "read_event_batch" in CALLS
    assert "observe_snapshot" in CALLS
    assert (
        "decision:await_observation:unknown_provider_status" in CALLS
    )
    assert (
        "decision:synthesize_terminal_from_snapshot:terminal_snapshot_synthesis"
        in CALLS
    )
    assert CALLS.index("harvest_evidence") < CALLS.index("stop_provider_session")
    assert CALLS.index("stop_host") < CALLS.index("release_leases")
    assert CALLS[-1] == "publish_artifacts"


async def test_plan_bound_codex_keeps_recorded_realizer_and_replays() -> None:
    """A new Codex plan never crosses into the generic session supervisor."""

    _reset_state()
    STATE["execution_realizer_ref"] = "codex-profile-bound@1"
    binding = OmnigentExecutionPlanBinding(
        planRef="omnigent-execution-plan:sha256:" + "d" * 64,
        planDigest="sha256:" + "d" * 64,
        planArtifactRef="art_codex_plan_3706",
        taskInputSnapshotRef="art_codex_task_3706",
        taskInputSnapshotDigest="sha256:" + "e" * 64,
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="provider-codex-native",
        omnigentExecutionPlan=binding,
        correlationId="workflow-codex-product",
        idempotencyKey="step-codex-product",
        instructionRef="art_codex_task_3706",
    )
    workflow_queue = get_workflow_task_queue()

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with (
            Worker(
                env.client,
                task_queue=workflow_queue,
                workflows=[MoonMindAgentRun],
                activities=[_resolve_adapter_metadata],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
                activities=[
                    _evaluate_session_admission,
                    _execute_recorded_plan_realizer,
                    _publish_artifacts,
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                request,
                id=f"agent-run-codex-plan-{uuid4()}",
                task_queue=workflow_queue,
            )
            result = AgentRunResult.model_validate(await handle.result())
            history = await handle.fetch_history()

    assert result.summary == "Recorded Codex realizer completed"
    assert STATE["codex_dispatch_plan"] == binding.model_dump(
        mode="json", by_alias=True
    )
    assert "execute_recorded_plan_realizer" in CALLS
    assert "resolve_intent" not in CALLS
    await Replayer(
        workflows=[MoonMindAgentRun],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


async def test_continue_as_new_preserves_active_provider_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """History rollover reloads one active provider session without redispatch."""

    _reset_state()
    # Four launch decisions establish an active provider turn. Roll over on the
    # first await-observation decision, then converge from durable fake state.
    monkeypatch.setattr(
        session_module,
        "CONTINUE_AS_NEW_DECISION_THRESHOLD",
        5,
    )
    workflow_queue = get_workflow_task_queue()
    session_id = "oms_continue_as_new_3705"
    session_input = OmnigentSessionWorkflowInput(
        sessionId=session_id,
        compiledExecutionIntentRef="art_intent_continue_as_new",
        compiledExecutionIntentDigest="sha256:" + "b" * 64,
        workflowId="workflow-continue-as-new",
        stepExecutionId="step-continue-as-new",
        agentRunId="agent-run-continue-as-new",
        initialTurnAttemptId=canonical_omnigent_turn_attempt_id(session_id),
        admittedFeatureGeneration="omnigent-session-v1",
        compatibilityVersion="v1",
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _register_search_attributes(env)
        async with (
            Worker(
                env.client,
                task_queue=workflow_queue,
                workflows=[MoonMindOmnigentSessionWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
                activities=[
                    _load_reconciliation_inputs,
                    _persist_decision,
                    _persist_signals,
                    _ensure_profile,
                    _ensure_host,
                    _ensure_session,
                    _submit_turn,
                    _record_terminal,
                    _harvest_evidence,
                    _publish_workspace,
                    _stop_provider,
                    _stop_host,
                    _release_leases,
                    _heartbeat_host_lease,
                    _read_event_batch,
                    _observe_snapshot,
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                MoonMindOmnigentSessionWorkflow.run,
                session_input,
                id=omnigent_session_workflow_id(session_id),
                task_queue=workflow_queue,
            )
            result = AgentRunResult.model_validate(
                await asyncio.wait_for(handle.result(), timeout=20)
            )
            query_state = await handle.query("omnigent_session.state")

    assert result.summary == "Omnigent session completed through supervisor"
    assert query_state["sessionId"] == session_id
    assert query_state["continueAsNewCount"] == 1
    assert CALLS.count("ensure_provider_profile_lease") == 1
    assert CALLS.count("ensure_host") == 1
    assert CALLS.count("ensure_provider_session") == 1
    assert CALLS.count("submit_turn") == 1


async def test_reconciliation_failure_persists_then_runs_ordered_cleanup() -> None:
    """Quarantine/fail decisions do not return before durable cleanup."""

    _reset_state()
    STATE.update(
        profile=True,
        host=True,
        provider_session=True,
        submitted=True,
        reconciler_failed=True,
    )
    workflow_queue = get_workflow_task_queue()
    session_id = "oms_reconciler_failure_3705"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _register_search_attributes(env)
        async with (
            Worker(
                env.client,
                task_queue=workflow_queue,
                workflows=[MoonMindOmnigentSessionWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
                activities=_direct_session_activities(),
            ),
        ):
            handle = await env.client.start_workflow(
                MoonMindOmnigentSessionWorkflow.run,
                _direct_session_input(session_id),
                id=omnigent_session_workflow_id(session_id),
                task_queue=workflow_queue,
            )
            result = AgentRunResult.model_validate(
                await asyncio.wait_for(handle.result(), timeout=20)
            )
            query_state = await handle.query("omnigent_session.state")

    assert result.failure_class == "execution_error"
    assert query_state["terminalStatus"] == "execution_failed"
    assert "persist_failure:execution_failed:omnigent.reconcile" in CALLS
    assert CALLS.index("stop_provider_session") < CALLS.index("stop_host")
    assert CALLS.index("stop_host") < CALLS.index("release_leases")


async def test_initial_input_failure_loads_minimal_authority_and_persists() -> None:
    """Failure before the first snapshot still uses the canonical DB fence."""

    _reset_state()
    STATE["load_failures_remaining"] = 1
    workflow_queue = get_workflow_task_queue()
    session_id = "oms_initial_load_failure_3705"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _register_search_attributes(env)
        async with (
            Worker(
                env.client,
                task_queue=workflow_queue,
                workflows=[MoonMindOmnigentSessionWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
                activities=_direct_session_activities(),
            ),
        ):
            handle = await env.client.start_workflow(
                MoonMindOmnigentSessionWorkflow.run,
                _direct_session_input(session_id),
                id=omnigent_session_workflow_id(session_id),
                task_queue=workflow_queue,
            )
            result = AgentRunResult.model_validate(
                await asyncio.wait_for(handle.result(), timeout=20)
            )

    assert result.failure_class == "integration_error"
    assert CALLS[:3] == [
        "load",
        "load_failure_authority",
        "persist_failure:integration_unavailable:omnigent.load_reconciliation_inputs",
    ]


async def test_persistent_input_failure_escalates_to_claimable_cleanup() -> None:
    """A terminalized loader outage cannot trap the child in a retry loop."""

    _reset_state()
    STATE["load_failures_remaining"] = 10
    workflow_queue = get_workflow_task_queue()
    session_id = "oms_persistent_input_failure_3705"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _register_search_attributes(env)
        async with (
            Worker(
                env.client,
                task_queue=workflow_queue,
                workflows=[MoonMindOmnigentSessionWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
                activities=_direct_session_activities(),
            ),
        ):
            handle = await env.client.start_workflow(
                MoonMindOmnigentSessionWorkflow.run,
                _direct_session_input(session_id),
                id=omnigent_session_workflow_id(session_id),
                task_queue=workflow_queue,
            )
            result = AgentRunResult.model_validate(
                await asyncio.wait_for(handle.result(), timeout=20)
            )
            query_state = await handle.query("omnigent_session.state")

    assert result.failure_class == "integration_error"
    assert result.metadata["omnigentSessionStatus"] == "cleanup_incomplete"
    assert result.metadata["primaryOmnigentSessionStatus"] == (
        "integration_unavailable"
    )
    assert result.metadata["janitorRequired"] is True
    assert query_state["terminalStatus"] == "cleanup_incomplete"
    assert CALLS.count("load_failure_authority") == 2
    assert (
        CALLS.count(
            "persist_failure:integration_unavailable:"
            "omnigent.load_reconciliation_inputs"
        )
        == 1
    )
    assert (
        CALLS.count(
            "persist_failure:cleanup_incomplete:"
            "omnigent.load_reconciliation_inputs"
        )
        == 1
    )
    assert "release_leases" not in CALLS


async def test_exhausted_turn_submission_records_delivery_unknown_then_cleans_up() -> None:
    """A lost submit response is never rewritten as a definite execution failure."""

    _reset_state()
    STATE["fail_activity"] = "submit_turn"
    workflow_queue = get_workflow_task_queue()
    session_id = "oms_delivery_unknown_3705"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _register_search_attributes(env)
        async with (
            Worker(
                env.client,
                task_queue=workflow_queue,
                workflows=[MoonMindOmnigentSessionWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
                activities=_direct_session_activities(),
            ),
        ):
            handle = await env.client.start_workflow(
                MoonMindOmnigentSessionWorkflow.run,
                _direct_session_input(session_id),
                id=omnigent_session_workflow_id(session_id),
                task_queue=workflow_queue,
            )
            result = AgentRunResult.model_validate(
                await asyncio.wait_for(handle.result(), timeout=20)
            )

    assert result.failure_class == "integration_error"
    assert result.metadata["omnigentSessionStatus"] == "delivery_unknown"
    assert "persist_failure:delivery_unknown:omnigent.submit_turn" in CALLS
    assert CALLS.index("stop_provider_session") < CALLS.index("stop_host")
    assert CALLS.index("stop_host") < CALLS.index("release_leases")


async def test_exhausted_cleanup_is_visible_and_janitor_owned() -> None:
    """Cleanup exhaustion preserves primary success and never releases early."""

    _reset_state()
    STATE["fail_activity"] = "stop_host"
    workflow_queue = get_workflow_task_queue()
    session_id = "oms_cleanup_incomplete_3705"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _register_search_attributes(env)
        async with (
            Worker(
                env.client,
                task_queue=workflow_queue,
                workflows=[MoonMindOmnigentSessionWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
                activities=_direct_session_activities(),
            ),
        ):
            handle = await env.client.start_workflow(
                MoonMindOmnigentSessionWorkflow.run,
                _direct_session_input(session_id),
                id=omnigent_session_workflow_id(session_id),
                task_queue=workflow_queue,
            )
            result = AgentRunResult.model_validate(
                await asyncio.wait_for(handle.result(), timeout=20)
            )
            query_state = await handle.query("omnigent_session.state")

    assert result.failure_class is None
    assert result.metadata["omnigentSessionStatus"] == "cleanup_incomplete"
    assert result.metadata["janitorRequired"] is True
    assert query_state["terminalStatus"] == "cleanup_incomplete"
    assert query_state["cleanupEvidenceRef"] == "art_cleanup_incomplete"
    assert "persist_failure:cleanup_incomplete:omnigent.stop_host" in CALLS
    assert "release_leases" not in CALLS


async def test_post_terminal_publication_failure_still_runs_cleanup() -> None:
    """Harvest success cannot hide a later exhausted publication phase."""

    _reset_state()
    STATE["fail_activity"] = "publish_workspace"
    workflow_queue = get_workflow_task_queue()
    session_id = "oms_publication_failure_3705"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _register_search_attributes(env)
        async with (
            Worker(
                env.client,
                task_queue=workflow_queue,
                workflows=[MoonMindOmnigentSessionWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
                activities=_direct_session_activities(),
            ),
        ):
            handle = await env.client.start_workflow(
                MoonMindOmnigentSessionWorkflow.run,
                _direct_session_input(session_id),
                id=omnigent_session_workflow_id(session_id),
                task_queue=workflow_queue,
            )
            result = AgentRunResult.model_validate(
                await asyncio.wait_for(handle.result(), timeout=20)
            )

    assert result.failure_class == "integration_error"
    assert (
        "persist_failure:integration_unavailable:omnigent.publish_workspace"
        in CALLS
    )
    assert CALLS.index("stop_provider_session") < CALLS.index("stop_host")
    assert CALLS.index("stop_host") < CALLS.index("release_leases")


async def test_legacy_heartbeat_crash_window_converges_after_disconnect_and_restart() -> None:
    """The #3698 edge is now a bounded wake plus authoritative snapshot."""

    replay_root = (
        Path(__file__).parents[2]
        / "reliability"
        / "replays"
        / "omnigent-profile-bound-heartbeat-timeout"
    )
    manifest = json.loads((replay_root / "manifest.json").read_text())
    assert manifest["activityType"] == "integration.omnigent.profile_bound_execute"
    assert manifest["heartbeatTimeoutSeconds"] == 120
    route = build_default_activity_catalog().resolve_activity(
        "omnigent.read_event_batch"
    )
    assert route.timeouts.heartbeat_timeout_seconds is None

    _reset_state()
    workflow_queue = get_workflow_task_queue()
    session_id = "oms_heartbeat_window_3705"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _register_search_attributes(env)
        async with (
            Worker(
                env.client,
                task_queue=workflow_queue,
                workflows=[MoonMindOmnigentSessionWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
                activities=_direct_session_activities(),
            ),
        ):
            handle = await env.client.start_workflow(
                MoonMindOmnigentSessionWorkflow.run,
                _direct_session_input(session_id),
                id=omnigent_session_workflow_id(session_id),
                task_queue=workflow_queue,
            )
            result = AgentRunResult.model_validate(
                await asyncio.wait_for(handle.result(), timeout=20)
            )

    assert result.failure_class is None
    assert int(STATE["event_read_count"]) >= 2
    assert int(STATE["snapshot_count"]) >= 4
    assert "decision:await_observation:unknown_provider_status" in CALLS
    assert (
        "decision:await_observation:awaiting_correlated_terminal_evidence"
        in CALLS
    )
    assert "decision:synthesize_terminal_from_snapshot:terminal_snapshot_synthesis" in CALLS
    assert CALLS.count("submit_turn") == 1


async def test_inflight_workflow_worker_restart_does_not_duplicate_provider_effects() -> None:
    """A worker restart while submit is in flight resumes the same command."""

    global PAUSE_PHASE, PHASE_STARTED, PHASE_RELEASE
    _reset_state()
    PAUSE_PHASE = "submit_turn"
    PHASE_STARTED = asyncio.Event()
    PHASE_RELEASE = asyncio.Event()
    workflow_queue = get_workflow_task_queue()
    session_id = "oms_worker_restart_3705"
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await _register_search_attributes(env)
            activity_worker = Worker(
                env.client,
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
                activities=_direct_session_activities(),
            )
            first_workflow_worker = Worker(
                env.client,
                task_queue=workflow_queue,
                workflows=[MoonMindOmnigentSessionWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
                sticky_queue_schedule_to_start_timeout=timedelta(seconds=1),
            )
            await activity_worker.__aenter__()
            await first_workflow_worker.__aenter__()
            try:
                handle = await env.client.start_workflow(
                    MoonMindOmnigentSessionWorkflow.run,
                    _direct_session_input(session_id),
                    id=omnigent_session_workflow_id(session_id),
                    task_queue=workflow_queue,
                )
                await asyncio.wait_for(PHASE_STARTED.wait(), timeout=20)
                await first_workflow_worker.__aexit__(None, None, None)
                PHASE_RELEASE.set()
                async with Worker(
                    env.client,
                    task_queue=workflow_queue,
                        workflows=[MoonMindOmnigentSessionWorkflow],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                        max_cached_workflows=0,
                    ):
                    await env.sleep(2)
                    result = AgentRunResult.model_validate(
                        await asyncio.wait_for(handle.result(), timeout=20)
                    )
            finally:
                if PHASE_RELEASE is not None:
                    PHASE_RELEASE.set()
                await activity_worker.__aexit__(None, None, None)

        assert result.failure_class is None
        assert CALLS.count("ensure_provider_session") == 1
        assert CALLS.count("submit_turn") == 1
    finally:
        if PHASE_RELEASE is not None:
            PHASE_RELEASE.set()
        PAUSE_PHASE = None
        PHASE_STARTED = None
        PHASE_RELEASE = None


async def test_pre_supervisor_agent_run_history_replays_on_legacy_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An AgentRun history without the admission patch never starts a child."""

    _reset_state()
    original_patched = agent_run_module.workflow.patched

    def patched_without_session_supervisor(patch_id: str) -> bool:
        if patch_id == agent_run_module.OMNIGENT_SESSION_SUPERVISOR_PATCH_ID:
            return False
        return original_patched(patch_id)

    monkeypatch.setattr(
        agent_run_module.workflow,
        "patched",
        patched_without_session_supervisor,
    )
    workflow_queue = get_workflow_task_queue()
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="omnigent-codex",
        correlationId="legacy-workflow-3705",
        idempotencyKey="legacy-step-3705",
        instructionRef="art_legacy_instruction_3705",
    )
    history = None
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with (
            Worker(
                env.client,
                task_queue=workflow_queue,
                workflows=[MoonMindAgentRun],
                activities=[_resolve_adapter_metadata],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ),
            Worker(
                env.client,
                task_queue=AGENT_RUNTIME_TASK_QUEUE,
                activities=[
                    _legacy_profile_bound_execute,
                    _publish_artifacts,
                ],
            ),
        ):
            handle = await env.client.start_workflow(
                MoonMindAgentRun.run,
                request,
                id=f"agent-run-legacy-3705-{uuid4()}",
                task_queue=workflow_queue,
            )
            result = AgentRunResult.model_validate(await handle.result())
            history = await handle.fetch_history()

    assert result.summary == "Legacy profile-bound execution completed"
    assert "legacy_profile_bound_execute" in CALLS
    assert "resolve_intent" not in CALLS

    monkeypatch.setattr(
        agent_run_module.workflow,
        "patched",
        original_patched,
    )
    assert history is not None
    await Replayer(
        workflows=[MoonMindAgentRun],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


async def test_disabling_new_admission_preserves_admitted_cleanup_and_history() -> None:
    """Policy changes route only new AgentRuns; the admitted child stays owned."""

    global PAUSE_PHASE, PHASE_STARTED, PHASE_RELEASE
    _reset_state()
    PAUSE_PHASE = "stop_host"
    PHASE_STARTED = asyncio.Event()
    PHASE_RELEASE = asyncio.Event()
    workflow_queue = get_workflow_task_queue()
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="omnigent-codex",
        correlationId="admission-history-3705",
        idempotencyKey="admission-history-3705:step",
        instructionRef="art_admission_history_instruction",
    )
    admitted_history = None
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await _register_search_attributes(env)
            async with (
                Worker(
                    env.client,
                    task_queue=workflow_queue,
                    workflows=[MoonMindAgentRun, MoonMindOmnigentSessionWorkflow],
                    activities=[_resolve_adapter_metadata],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
                Worker(
                    env.client,
                    task_queue=AGENT_RUNTIME_TASK_QUEUE,
                    activities=[
                        _evaluate_session_admission,
                        _resolve_intent,
                        *_direct_session_activities(),
                        _legacy_profile_bound_execute,
                        _publish_artifacts,
                    ],
                ),
            ):
                agent_run_id = f"agent-run-admitted-history-{uuid4()}"
                admitted_handle = await env.client.start_workflow(
                    MoonMindAgentRun.run,
                    request,
                    id=agent_run_id,
                    task_queue=workflow_queue,
                )
                await asyncio.wait_for(PHASE_STARTED.wait(), timeout=20)
                # This represents an operator disabling only new selections.
                # The child has already frozen its generation in history.
                STATE["admission"] = False
                PHASE_RELEASE.set()
                admitted_result = AgentRunResult.model_validate(
                    await asyncio.wait_for(admitted_handle.result(), timeout=20)
                )
                session_id = canonical_omnigent_session_id(
                    workflow_id=request.correlation_id,
                    step_execution_id=request.idempotency_key,
                    agent_run_id=agent_run_id,
                )
                session_handle = env.client.get_workflow_handle(
                    omnigent_session_workflow_id(session_id)
                )
                historical_state = await session_handle.query(
                    "omnigent_session.state"
                )
                admitted_history = await admitted_handle.fetch_history()

                legacy_handle = await env.client.start_workflow(
                    MoonMindAgentRun.run,
                    request.model_copy(
                        update={"idempotency_key": "disabled-new-selection:step"}
                    ),
                    id=f"agent-run-disabled-selection-{uuid4()}",
                    task_queue=workflow_queue,
                )
                legacy_result = AgentRunResult.model_validate(
                    await legacy_handle.result()
                )

        assert admitted_result.failure_class is None
        assert historical_state["terminalStatus"] == "completed"
        assert historical_state["featureGeneration"] == "omnigent-session-v1"
        assert CALLS.index("stop_host") < CALLS.index("release_leases")
        assert legacy_result.summary == "Legacy profile-bound execution completed"
        assert CALLS.count("resolve_intent") == 1
        assert CALLS.count("legacy_profile_bound_execute") == 1
        assert admitted_history is not None
        await Replayer(
            workflows=[MoonMindAgentRun],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(admitted_history)
    finally:
        if PHASE_RELEASE is not None:
            PHASE_RELEASE.set()
        PAUSE_PHASE = None
        PHASE_STARTED = None
        PHASE_RELEASE = None


@pytest.mark.parametrize(
    ("pause_phase", "expected_terminal_status"),
    [
        ("ensure_host", "canceled"),
        ("submit_turn", "canceled"),
        ("harvest_evidence", "completed"),
        ("stop_host", "completed"),
    ],
)
async def test_agent_run_cancellation_preserves_session_cleanup_owner(
    pause_phase: str,
    expected_terminal_status: str,
) -> None:
    """Cancellation never tears down the child before its ordered cleanup."""

    global PAUSE_PHASE, PHASE_STARTED, PHASE_RELEASE
    _reset_state()
    PAUSE_PHASE = pause_phase
    PHASE_STARTED = asyncio.Event()
    PHASE_RELEASE = asyncio.Event()
    workflow_queue = get_workflow_task_queue()
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="omnigent-codex",
        correlationId=f"cancel-{pause_phase}",
        idempotencyKey=f"cancel-{pause_phase}:step",
        instructionRef="art_cancel_instruction",
        timeoutPolicy={"timeout_seconds": 600},
    )

    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await _register_search_attributes(env)
            async with (
                Worker(
                    env.client,
                    task_queue=workflow_queue,
                    workflows=[
                        MoonMindAgentRun,
                        MoonMindOmnigentSessionWorkflow,
                    ],
                    activities=[_resolve_adapter_metadata],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
                Worker(
                    env.client,
                    task_queue=AGENT_RUNTIME_TASK_QUEUE,
                    activities=[
                        _evaluate_session_admission,
                        _resolve_intent,
                        _load_reconciliation_inputs,
                        _persist_decision,
                        _persist_signals,
                        _persist_failure,
                        _ensure_profile,
                        _ensure_host,
                        _ensure_session,
                        _submit_turn,
                        _record_terminal,
                        _harvest_evidence,
                        _publish_workspace,
                        _stop_provider,
                        _stop_host,
                        _release_leases,
                        _heartbeat_host_lease,
                        _read_event_batch,
                        _observe_snapshot,
                        _publish_artifacts,
                    ],
                ),
            ):
                agent_run_id = f"agent-run-cancel-{pause_phase}-{uuid4()}"
                handle = await env.client.start_workflow(
                    MoonMindAgentRun.run,
                    request,
                    id=agent_run_id,
                    task_queue=workflow_queue,
                )
                await asyncio.wait_for(PHASE_STARTED.wait(), timeout=20)
                await handle.cancel()
                # Let the bounded Activity acknowledge the cancellation race.
                # AgentRun keeps waiting for the signaled session owner until
                # the in-flight phase has stopped or settled and cleanup ends.
                PHASE_RELEASE.set()
                session_id = canonical_omnigent_session_id(
                    workflow_id=request.correlation_id,
                    step_execution_id=request.idempotency_key,
                    agent_run_id=agent_run_id,
                )
                session_handle = env.client.get_workflow_handle(
                    omnigent_session_workflow_id(session_id)
                )
                try:
                    agent_result = AgentRunResult.model_validate(
                        await asyncio.wait_for(handle.result(), timeout=20)
                    )
                except TimeoutError as exc:
                    await session_handle.terminate("cancellation test timeout")
                    await handle.terminate("cancellation test timeout")
                    raise AssertionError(
                        "AgentRun cancellation did not converge through cleanup; "
                        f"phase={pause_phase}, state={STATE}, calls={CALLS}"
                    ) from exc
                session_result = AgentRunResult.model_validate(
                    await session_handle.result()
                )
                query_state = await session_handle.query(
                    "omnigent_session.state"
                )

        assert query_state["terminalStatus"] == expected_terminal_status, {
            "calls": CALLS,
            "state": STATE,
            "query": query_state,
        }
        assert agent_result.failure_class == (
            "canceled" if expected_terminal_status == "canceled" else None
        )
        assert session_result.metadata["canonicalSessionId"] == session_id
        assert CALLS.index("stop_host") < CALLS.index("release_leases")
    finally:
        if PHASE_RELEASE is not None:
            PHASE_RELEASE.set()
        PAUSE_PHASE = None
        PHASE_STARTED = None
        PHASE_RELEASE = None
