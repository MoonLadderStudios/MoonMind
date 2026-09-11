"""MoonLadderStudios/MoonMind#4226: incident replay for the 6h idle turn.

Replay shape from the 2026-09-10 05:00Z child
(``...:05:094d273f``, Omnigent session ``df02874f...``):
marker accepted, session snapshot ``status=idle`` with
``active_response_id=None``, no item after the marker, heartbeat frames
only (``eventsCaptured=1776``, ``turnEverActive=false``), and a stale
``turnTerminalResponseIds=[resp_4272f9...]`` entry from the previous
attempt. The turn-start watchdog must fire within
``turn_start_timeout_seconds`` (+ one poll interval) and report
``waited_seconds``; the Activity heartbeat must keep
``turnStartTimeoutSeconds`` populated while the marked turn is pending.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from moonmind.omnigent.execute import (
    OmnigentTurnNotStartedError,
    _MarkedTurnStartWatchdog,
    _await_marked_turn_terminal,
)

MARKER_4226 = (
    "MoonMind-Omnigent-Run:\n"
    "correlationId: mm:1d72e336-30cc-434e-9443-c3f833c151bb"
    "-2026-09-10T05:00:00Z\n"
    "idempotencyKey: mm:1d72e336-30cc-434e-9443-c3f833c151bb"
    "-2026-09-10T05:00:00Z:agent:tpl:github-issue-search-and-implement"
    ":05:094d273f:execution:1:agent_execute"
)

STALE_TERMINAL_RESPONSE_ID = "resp_4272f9stale-previous-attempt"


def _marked_user_item(marker: str) -> dict[str, Any]:
    return {
        "id": "item-user",
        "type": "message",
        "status": "completed",
        "data": {
            "role": "user",
            "content": [{"type": "input_text", "text": marker}],
        },
    }


def _idle_4226_snapshot(marker: str) -> dict[str, Any]:
    # Last item is the pre-existing `bash moonmind container python-tests`
    # call with no output; nothing follows this attempt's marker, matching
    # the incident's `updated_at=05:10:49Z` idle session.
    return {
        "status": "idle",
        "active_response_id": None,
        "items": [
            {
                "id": "bash-python-tests-no-output",
                "type": "function_call",
                "status": "completed",
                "data": {
                    "name": "bash",
                    "arguments": "moonmind container python-tests",
                    "output": None,
                },
            },
            _marked_user_item(marker),
        ],
    }


@pytest.mark.asyncio
async def test_4226_idle_heartbeat_replay_reports_waited_seconds() -> None:
    """Marker + idle snapshot + heartbeats only fails within budget."""

    snapshot = _idle_4226_snapshot(MARKER_4226)

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        async def get_session(self, _session_id: str) -> dict[str, Any]:
            self.calls += 1
            return snapshot

    client = Client()
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(OmnigentTurnNotStartedError) as excinfo:
        await _await_marked_turn_terminal(
            client=client,  # type: ignore[arg-type]
            session_id="df02874f959c492a8b03830780f800a9",
            marker=MARKER_4226,
            event_count=1776,
            terminal_status="completed",
            timeout_seconds=30.0,
            interval_seconds=0.001,
            turn_start_timeout_seconds=0.05,
        )
    elapsed = loop.time() - started
    assert elapsed < 5.0
    assert client.calls >= 2
    assert excinfo.value.code == "OMNIGENT_CURRENT_TURN_NOT_STARTED"
    assert excinfo.value.waited_seconds is not None
    assert excinfo.value.waited_seconds >= 0.05
    assert f"{excinfo.value.waited_seconds}s" in str(excinfo.value)


@pytest.mark.asyncio
async def test_4226_stale_terminal_id_does_not_defer_watchdog() -> None:
    """A pre-marker terminal id from attempt 1 must not defer attempt 2."""

    snapshot = _idle_4226_snapshot(MARKER_4226)
    loop = asyncio.get_running_loop()
    watchdog = _MarkedTurnStartWatchdog(
        loop=loop,
        timeout_seconds=0.05,
    )
    # Restore the stale id exactly as the post-dispatch path restores
    # `retry_state.turnTerminalResponseIds` from the durable heartbeat.
    watchdog.restore_terminal_response_ids([STALE_TERMINAL_RESPONSE_ID])

    class Client:
        async def get_session(self, _session_id: str) -> dict[str, Any]:
            return snapshot

    started = loop.time()
    with pytest.raises(OmnigentTurnNotStartedError) as excinfo:
        await _await_marked_turn_terminal(
            client=Client(),  # type: ignore[arg-type]
            session_id="df02874f959c492a8b03830780f800a9",
            marker=MARKER_4226,
            event_count=1776,
            terminal_status="completed",
            timeout_seconds=30.0,
            interval_seconds=0.001,
            turn_start_timeout_seconds=0.05,
            start_watchdog=watchdog,
        )
    assert loop.time() - started < 5.0
    assert excinfo.value.waited_seconds is not None


def test_4226_heartbeat_carries_start_budget_while_pending() -> None:
    """`turnStartTimeoutSeconds` is never null while the marked turn pends."""

    loop = asyncio.new_event_loop()
    try:
        watchdog = _MarkedTurnStartWatchdog(loop=loop, timeout_seconds=300.0)
        fields = watchdog.heartbeat_fields()
        assert fields["turnEverActive"] is False
        assert fields["turnStartTimeoutSeconds"] == 300.0
        assert fields["turnStartWaitSeconds"] is not None
    finally:
        loop.close()
