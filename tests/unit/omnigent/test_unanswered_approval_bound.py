"""A workflow step cannot wait indefinitely on an approval nobody answers.

mm:7baa29a9-4ad1-4578-a0be-b27af4042b65-2026-11-19T07:09:16Z held a Codex
command-approval prompt for six hours while its session projected ``running``
with the prompt in ``pending_elicitations``. The step now ends after its stall
window with a typed failure that names the prompt; an operator answering in
Workflow Chat within that window keeps the step running.
"""

import asyncio

import pytest

from moonmind.omnigent.execute import (
    OmnigentApprovalUnansweredError,
    OmnigentTurnUnrecoverableError,
    _MarkedTurnStartWatchdog,
)
from moonmind.omnigent.harness_platform.failures import remediation_for

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
