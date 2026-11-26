"""A workflow step cannot wait indefinitely on an approval nobody answers.

mm:7baa29a9-4ad1-4578-a0be-b27af4042b65-2026-11-19T07:09:16Z held a Codex
command-approval prompt for six hours while its session projected ``running``
with the prompt in ``pending_elicitations``. The step now ends after its stall
window with a typed failure that names the prompt; an operator answering in
Workflow Chat within that window keeps the step running.

mm:60ed24ce-899d-45df-b045-7355974a23be-2026-11-23T17:40:00Z then held a Claude
prompt for four hours: the TUI repainted about once a second behind it, and
Omnigent sends heartbeats only on an idle stream, so the step never re-read the
snapshot that carried the prompt.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.execute import (
    OmnigentApprovalUnansweredError,
    OmnigentTurnUnrecoverableError,
    _approval_wait_timeout_seconds,
    _MarkedTurnStartWatchdog,
    run_omnigent_execution,
)
from moonmind.omnigent.harness_platform.failures import remediation_for
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    ExecutionBudgetContext,
    resolve_execution_budget,
)

_STATE = {"boundarySource": "marker", "progress": True, "signature": ["work"]}


def _snapshot(*elicitations):
    return {
        "status": "running",
        "active_response_id": "resp-1",
        "pending_elicitations": list(elicitations),
    }


_PROMPT = {
    "elicitation_id": "elicit-1",
    "params": {
        "message": "Codex wants to run **/bin/sh -lc 'cat SKILL.md'**",
        "phase": "codex_command_approval",
    },
    "reason": "May I read the skill outside the unavailable shell sandbox?",
}


def _watchdog(loop, **kwargs):
    return _MarkedTurnStartWatchdog(
        loop=loop,
        timeout_seconds=300,
        approval_wait_timeout_seconds=900,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_unanswered_approval_ends_the_step_after_its_window():
    loop = asyncio.get_running_loop()
    watchdog = _watchdog(loop)
    start = loop.time()
    watchdog.observe(_snapshot(_PROMPT), _STATE, observation_started_at=start)
    watchdog.observe(_snapshot(_PROMPT), _STATE, observation_started_at=start + 899)

    with pytest.raises(OmnigentApprovalUnansweredError) as failure:
        watchdog.observe(_snapshot(_PROMPT), _STATE, observation_started_at=start + 901)

    message = str(failure.value)
    assert "Codex wants to run" in message
    assert "unavailable shell sandbox" in message
    assert failure.value.code == "OMNIGENT_APPROVAL_UNANSWERED"
    # The execution boundary finalizes typed unrecoverable turns as terminal
    # failures; retrying would only reach the same prompt again.
    assert isinstance(failure.value, OmnigentTurnUnrecoverableError)
    assert remediation_for(failure.value.code) == "do_not_retry"
    assert failure.value.terminal_diagnostics()["approvalWaitSeconds"] >= 900


@pytest.mark.asyncio
async def test_answered_approval_restarts_the_window():
    loop = asyncio.get_running_loop()
    watchdog = _watchdog(loop)
    start = loop.time()
    watchdog.observe(_snapshot(_PROMPT), _STATE, observation_started_at=start)
    watchdog.observe(_snapshot(), _STATE, observation_started_at=start + 600)
    watchdog.observe(_snapshot(_PROMPT), _STATE, observation_started_at=start + 700)

    watchdog.observe(_snapshot(_PROMPT), _STATE, observation_started_at=start + 1500)
    with pytest.raises(OmnigentApprovalUnansweredError):
        watchdog.observe(
            _snapshot(_PROMPT), _STATE, observation_started_at=start + 1601
        )


@pytest.mark.asyncio
async def test_a_new_prompt_after_an_answer_gets_its_own_window():
    # Codex can ask again right after an answer, before any snapshot shows
    # an empty prompt list; the operator who answered must not be penalized.
    loop = asyncio.get_running_loop()
    watchdog = _watchdog(loop)
    start = loop.time()
    second = {**_PROMPT, "elicitation_id": "elicit-2"}
    watchdog.observe(_snapshot(_PROMPT), _STATE, observation_started_at=start)
    watchdog.observe(_snapshot(second), _STATE, observation_started_at=start + 800)

    watchdog.observe(_snapshot(second), _STATE, observation_started_at=start + 1600)
    with pytest.raises(OmnigentApprovalUnansweredError):
        watchdog.observe(_snapshot(second), _STATE, observation_started_at=start + 1701)


@pytest.mark.asyncio
async def test_quiet_work_without_a_prompt_is_not_an_approval_wait():
    loop = asyncio.get_running_loop()
    watchdog = _watchdog(loop)
    start = loop.time()
    watchdog.observe(_snapshot(), _STATE, observation_started_at=start)
    watchdog.observe(_snapshot(), _STATE, observation_started_at=start + 5000)


def _execution_request(**updates):
    return AgentExecutionRequest(
        **{
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": "mm:wf",
            "idempotencyKey": "mm:wf:node-1",
            **updates,
        }
    )


def test_approval_window_follows_the_admitted_step_budget():
    budget = resolve_execution_budget(
        timeout_policy={"timeout_seconds": 3600, "progress_stall_seconds": 420}
    )
    request = _execution_request(
        executionBudget=ExecutionBudgetContext(
            budget=budget,
            logicalStepId="node-1",
            attemptId="attempt-1",
            admittedAt=datetime.now(UTC),
        )
    )

    assert _approval_wait_timeout_seconds(request) == budget.progress_stall_seconds


def test_approval_window_without_an_admitted_budget_uses_the_resolved_default():
    request = _execution_request()
    default = resolve_execution_budget(agent_kind=request.agent_kind)

    assert _approval_wait_timeout_seconds(request) == default.progress_stall_seconds


@pytest.mark.asyncio
async def test_a_repainting_terminal_cannot_hide_an_unanswered_prompt(
    monkeypatch, tmp_path
):
    marker = (
        "MoonMind-Omnigent-Run:\n"
        "  correlationId: corr-busy-prompt\n"
        "  idempotencyKey: idem-busy-prompt"
    )
    prompt = {
        "elicitation_id": "elicit-claude-1",
        "params": {
            "message": "Claude wants to call **Bash**",
            "phase": "pre_tool_use",
            "permission_mode": "bypassPermissions",
        },
    }
    waiting = {
        "status": "running",
        "active_response_id": "resp-1",
        "pending_elicitations": [prompt],
        "items": [
            {"id": "prior-item", "type": "message"},
            {
                "id": "marked-user",
                "type": "message",
                "status": "completed",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": marker}],
                },
            },
            {
                "id": "call-1",
                "type": "function_call",
                "status": "completed",
                "data": {"call_id": "call-1"},
            },
        ],
    }

    class FakeClient:
        def __init__(self, **_: object) -> None:
            self.posted = asyncio.Event()

        async def list_agents(self):
            return {"items": [{"id": "agent-1", "name": "claude-native-ui"}]}

        async def create_session(self, payload):
            return {"id": "session-1"}

        async def post_event(self, session_id, payload):
            self.posted.set()
            return {"pending_id": "pending-1"}

        async def stream_events(self, session_id):
            await self.posted.wait()
            while True:
                # Frames arrive far faster than the idle tick, and Omnigent
                # sends no heartbeat while they do.
                yield {"type": "session.terminal.activity"}
                await asyncio.sleep(0.002)

        async def get_session(self, session_id):
            if not self.posted.is_set():
                return {
                    "status": "idle",
                    "active_response_id": None,
                    "items": [{"id": "prior-item", "type": "message"}],
                }
            return waiting

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    monkeypatch.setattr(
        "moonmind.omnigent.execute._TERMINAL_RECONCILIATION_INTERVAL_SECONDS", 0.2
    )
    monkeypatch.setattr(
        "moonmind.omnigent.execute._approval_wait_timeout_seconds", lambda _: 0.5
    )

    execution = run_omnigent_execution(
        _execution_request(
            correlationId="corr-busy-prompt",
            idempotencyKey="idem-busy-prompt",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "claude-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "do the work"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )
    # Before the fix this waited until the step's execution deadline.
    result = await asyncio.wait_for(execution, timeout=10)

    assert result.provider_error_code == "OMNIGENT_APPROVAL_UNANSWERED"
    assert "Claude wants to call **Bash**" in result.summary
