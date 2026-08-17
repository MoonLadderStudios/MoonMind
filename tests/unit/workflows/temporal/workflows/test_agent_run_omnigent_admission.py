"""Unit tests for AgentRun -> MoonMind.OmnigentSession delegation (#3705)."""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows import agent_run as module
from moonmind.workflows.temporal.workflows.agent_run import (
    OMNIGENT_SESSION_SUPERVISOR_PATCH_ID,
    MoonMindAgentRun,
    RunStatus,
)


def _request(**overrides) -> AgentExecutionRequest:
    base = dict(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        instructionRef="artifact:instruction",
        executionProfileRef="profile:codex-oauth",
    )
    base.update(overrides)
    return AgentExecutionRequest(**base)


class _Info:
    workflow_id = "wf-agent-run-1"
    parent = None


def _install(monkeypatch, *, patched: bool):
    def _patched(patch_id: str) -> bool:
        if patch_id == OMNIGENT_SESSION_SUPERVISOR_PATCH_ID:
            return patched
        return False

    monkeypatch.setattr(module.workflow, "patched", _patched)
    monkeypatch.setattr(module.workflow, "info", lambda: _Info())


@pytest.mark.asyncio
async def test_not_patched_falls_through_to_legacy(monkeypatch):
    _install(monkeypatch, patched=False)
    wf = MoonMindAgentRun()

    async def _fail_activity(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("resolve_intent must not be called when unpatched")

    monkeypatch.setattr(wf, "_execute_routed_activity", _fail_activity)
    result = await wf._maybe_execute_omnigent_session(_request(), timeout_seconds=600)
    assert result is None


@pytest.mark.asyncio
async def test_admission_declined_falls_through_to_legacy(monkeypatch):
    _install(monkeypatch, patched=True)
    wf = MoonMindAgentRun()

    async def _resolve(name, payload, **kwargs):
        assert name == "omnigent.resolve_intent"
        return {"admitted": False, "intent": None}

    monkeypatch.setattr(wf, "_execute_routed_activity", _resolve)

    async def _no_child(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("child workflow must not start when declined")

    monkeypatch.setattr(module.workflow, "execute_child_workflow", _no_child)
    result = await wf._maybe_execute_omnigent_session(_request(), timeout_seconds=600)
    assert result is None


@pytest.mark.asyncio
async def test_admitted_starts_child_and_maps_result(monkeypatch):
    _install(monkeypatch, patched=True)
    wf = MoonMindAgentRun()

    intent_payload = {
        "canonicalSessionId": "wf-agent-run-1:omnigent",
        "executionIntentRef": "artifact:instruction",
        "executionIntentDigest": "digest",
        "owningWorkflowId": "wf-agent-run-1",
        "stepExecutionId": "corr-1",
        "agentRunId": "wf-agent-run-1",
        "executionProfileRef": "profile:codex-oauth",
        "initialTurnAttemptId": "wf-agent-run-1:omnigent:turn:1",
        "admittedFeatureGeneration": 1,
        "compatibilityVersion": 1,
    }

    async def _resolve(name, payload, **kwargs):
        assert name == "omnigent.resolve_intent"
        assert payload["canonicalSessionId"] == "wf-agent-run-1:omnigent"
        return {"admitted": True, "intent": intent_payload}

    monkeypatch.setattr(wf, "_execute_routed_activity", _resolve)

    started: dict[str, Any] = {}

    async def _child(name, arg, **kwargs):
        started["name"] = name
        started["id"] = kwargs.get("id")
        return {
            "status": "completed",
            "canonicalSessionId": "wf-agent-run-1:omnigent",
            "agentRunId": "wf-agent-run-1",
            "reasonCodes": ["cleanup_complete"],
            "terminalResultRef": "artifact:terminal",
            "diagnosticsRef": "artifact:diag",
            "summary": "Omnigent session completed",
            "failureClass": None,
            "turnAttempts": 1,
            "observationCount": 2,
            "decisionCount": 9,
        }

    monkeypatch.setattr(module.workflow, "execute_child_workflow", _child)

    result = await wf._maybe_execute_omnigent_session(_request(), timeout_seconds=600)

    assert result is not None
    assert started["name"] == "MoonMind.OmnigentSession"
    assert started["id"] == "wf-agent-run-1:omnigent:session"
    assert wf.run_status == RunStatus.completed
    assert result.output_refs == ["artifact:terminal"]
    assert result.metadata["omnigentSessionStatus"] == "completed"
    assert result.metadata["canonicalSessionId"] == "wf-agent-run-1:omnigent"


@pytest.mark.asyncio
async def test_admitted_failure_maps_run_status_failed(monkeypatch):
    _install(monkeypatch, patched=True)
    wf = MoonMindAgentRun()

    intent_payload = {
        "canonicalSessionId": "wf-agent-run-1:omnigent",
        "executionIntentRef": "artifact:instruction",
        "executionIntentDigest": "digest",
        "owningWorkflowId": "wf-agent-run-1",
        "stepExecutionId": "corr-1",
        "agentRunId": "wf-agent-run-1",
        "executionProfileRef": "profile:codex-oauth",
        "initialTurnAttemptId": "wf-agent-run-1:omnigent:turn:1",
        "admittedFeatureGeneration": 1,
        "compatibilityVersion": 1,
    }

    async def _resolve(name, payload, **kwargs):
        return {"admitted": True, "intent": intent_payload}

    async def _child(name, arg, **kwargs):
        return {
            "status": "execution_failed",
            "canonicalSessionId": "wf-agent-run-1:omnigent",
            "agentRunId": "wf-agent-run-1",
            "reasonCodes": ["max_turn_attempts_exhausted", "cleanup_complete"],
            "terminalResultRef": None,
            "diagnosticsRef": "artifact:diag",
            "summary": "Omnigent session execution_failed",
            "failureClass": "execution_failed",
            "turnAttempts": 3,
            "observationCount": 5,
            "decisionCount": 20,
        }

    monkeypatch.setattr(wf, "_execute_routed_activity", _resolve)
    monkeypatch.setattr(module.workflow, "execute_child_workflow", _child)

    result = await wf._maybe_execute_omnigent_session(_request(), timeout_seconds=600)
    assert result is not None
    assert wf.run_status == RunStatus.failed
    assert result.failure_class == "execution_error"
    assert result.metadata["omnigentSessionStatus"] == "execution_failed"


@pytest.mark.asyncio
async def test_non_omnigent_request_falls_through(monkeypatch):
    _install(monkeypatch, patched=True)
    wf = MoonMindAgentRun()
    result = await wf._maybe_execute_omnigent_session(
        _request(agentId="jules"), timeout_seconds=600
    )
    assert result is None
