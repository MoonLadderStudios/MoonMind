"""Correlated native completion avoids a heuristic wait without weakening it."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from moonmind.omnigent import execute

MARKER = "MoonMind-Omnigent-Run: current-turn"
RESPONSE_ID = "codex_turn_current"


def _message(role, text, *, phase=None):
    data = {"role": role, "content": [{"type": "output_text", "text": text}]}
    if phase is not None:
        data["phase"] = phase
    return {
        "id": f"{role}-{text}",
        "type": "message",
        "status": "completed",
        "response_id": RESPONSE_ID,
        "data": data,
    }


def _terminal():
    return {
        "id": "session-1",
        "harness": "codex-native",
        "status": "idle",
        "active_response_id": None,
        "pending_inputs": [],
        "pending_elicitations": [],
        "terminal_pending": False,
        "items": [
            _message("user", MARKER),
            _message("assistant", "Done", phase="final_answer"),
        ],
    }


async def _poll(monkeypatch, snapshots, *, read_latencies=(), **kwargs):
    """Run the actual polling decision with deterministic elapsed time."""
    clock = SimpleNamespace(now=0.0, time=lambda: clock.now)
    calls = []
    heartbeats = []

    class Client:
        async def list_session_child_sessions(self, session_id):
            return {"data": [], "has_more": False}

        async def get_session(self, session_id):
            assert session_id == "session-1"
            snapshot = snapshots[min(len(calls), len(snapshots) - 1)]
            if len(calls) < len(read_latencies):
                clock.now += read_latencies[len(calls)]
            calls.append(snapshot)
            return copy.deepcopy(snapshot)

    async def sleep(seconds):
        clock.now += seconds

    async def wait_for(awaitable, **kwargs):
        return await awaitable

    with monkeypatch.context() as scoped:
        scoped.setattr(execute.asyncio, "get_running_loop", lambda: clock)
        scoped.setattr(execute.asyncio, "sleep", sleep)
        scoped.setattr(execute.asyncio, "wait_for", wait_for)
        scoped.setattr(execute, "_safe_heartbeat", heartbeats.append)
        result = await execute._await_marked_turn_terminal(
            client=Client(),
            session_id="session-1",
            marker=MARKER,
            event_count=3,
            terminal_status=kwargs.pop("terminal_status", "completed"),
            **kwargs,
        )
    return result, clock.now, calls, heartbeats


@pytest.mark.asyncio
async def test_correlated_native_final_answer_skips_sixty_second_wait(monkeypatch):
    result, elapsed, calls, _ = await _poll(
        monkeypatch, [_terminal()], native_terminal_response_id=RESPONSE_ID
    )
    assert result[0] == "completed"
    assert elapsed == 0
    assert len(calls) == 2


def _tool(name="shell", call_id="tool-1", *, output=False):
    return {
        "id": f"{call_id}-{'output' if output else 'call'}",
        "type": "function_call_output" if output else "function_call",
        "status": "completed",
        "response_id": RESPONSE_ID,
        "data": {"name": name, "call_id": call_id},
    }


@pytest.mark.parametrize(
    "scenario",
    [
        "missing-phase",
        "commentary",
        "partial-answer",
        "empty-answer",
        "streaming-answer",
        "interrupted-answer",
        "child-return",
        "unknown-harness",
        "claude",
        "wrong-session",
        "running",
        "waiting",
        "active-response",
        "missing-response-field",
        "pending-input",
        "pending-elicitation",
        "missing-pending-inputs",
        "terminal-pending",
        "background-count",
        "background-task",
        "provider-error",
        "plan-mode",
        "newer-user",
        "prior-final",
        "marker-evicted",
        "wrong-response",
        "marker-wrong-response",
        "unmatched-before-final",
        "unmatched-after-final",
        "matched-tool-after-final",
        "unfinished-diff",
        "streaming-diff",
        "anonymous-call",
        "foreign-turn-item",
        "unknown-item",
        "nonmapping-item",
    ],
)
def test_fast_path_rejects_ambiguous_or_unfinished_snapshots(scenario):
    snapshot = _terminal()
    answer = snapshot["items"][-1]
    if scenario == "missing-phase":
        answer["data"].pop("phase")
    elif scenario in {"commentary", "partial-answer"}:
        answer["data"]["phase"] = scenario.replace("-", "_")
    elif scenario == "empty-answer":
        answer["data"]["content"] = []
    elif scenario == "streaming-answer":
        answer["status"] = "in_progress"
    elif scenario == "interrupted-answer":
        answer["data"]["interrupted"] = True
    elif scenario == "child-return":
        answer["data"]["subagent_return_id"] = "child-1"
    elif scenario in {"unknown-harness", "claude"}:
        snapshot["harness"] = "claude-native" if scenario == "claude" else None
    elif scenario == "wrong-session":
        snapshot["id"] = "another-session"
    elif scenario in {"running", "waiting"}:
        snapshot["status"] = scenario
    elif scenario == "active-response":
        snapshot["active_response_id"] = RESPONSE_ID
    elif scenario == "missing-response-field":
        snapshot.pop("active_response_id")
    elif scenario == "pending-input":
        snapshot["pending_inputs"] = [{"content": "new work"}]
    elif scenario == "pending-elicitation":
        snapshot["pending_elicitations"] = [{"id": "approval-1"}]
    elif scenario == "missing-pending-inputs":
        snapshot.pop("pending_inputs")
    elif scenario == "terminal-pending":
        snapshot["terminal_pending"] = True
    elif scenario == "background-count":
        snapshot["background_task_count"] = 1
    elif scenario == "background-task":
        snapshot["background_tasks"] = [{"id": "task-1"}]
    elif scenario == "provider-error":
        snapshot["last_task_error"] = {"message": "failed"}
    elif scenario == "plan-mode":
        snapshot["labels"] = {"omnigent.codex_native.collaboration_mode": "plan"}
    elif scenario == "newer-user":
        snapshot["items"].append(_message("user", "Continue"))
    elif scenario == "prior-final":
        snapshot["items"].reverse()
    elif scenario == "marker-evicted":
        snapshot["items"].pop(0)
    elif scenario == "wrong-response":
        answer["response_id"] = "codex_turn_prior"
    elif scenario == "marker-wrong-response":
        snapshot["items"][0]["response_id"] = "injection-response"
    elif scenario == "unmatched-before-final":
        snapshot["items"].insert(1, _tool())
    elif scenario == "unmatched-after-final":
        snapshot["items"].append(_tool())
    elif scenario == "matched-tool-after-final":
        snapshot["items"].extend([_tool(), _tool(output=True)])
    elif scenario == "unfinished-diff":
        snapshot["items"].append(_tool("turn_diff"))
    elif scenario == "streaming-diff":
        snapshot["items"].extend([_tool("turn_diff"), _tool(output=True)])
        snapshot["items"][-1]["status"] = "in_progress"
    elif scenario == "anonymous-call":
        snapshot["items"].insert(1, _tool(call_id=None))
    elif scenario == "foreign-turn-item":
        snapshot["items"].insert(1, _tool())
        snapshot["items"][1]["response_id"] = "codex_turn_other"
    elif scenario == "unknown-item":
        snapshot["items"].append({"type": "unknown", "status": "completed"})
    elif scenario == "nonmapping-item":
        snapshot["items"].append(None)
    else:
        raise AssertionError(scenario)
    state = execute._marked_turn_item_state(
        snapshot, marker=MARKER, baseline_item_ids=frozenset({"old-item"})
    )
    assert not execute._snapshot_confirms_native_final_answer(
        snapshot, session_id="session-1", response_id=RESPONSE_ID, turn_state=state
    )


@pytest.mark.asyncio
async def test_matched_work_and_paired_turn_diff_preserve_fast_path(monkeypatch):
    snapshot = _terminal()
    snapshot["items"][1:1] = [_tool(), _tool(output=True)]
    snapshot["items"].extend(
        [_tool("turn_diff", "diff-1"), _tool(call_id="diff-1", output=True)]
    )
    result, elapsed, _, heartbeats = await _poll(
        monkeypatch, [snapshot], native_terminal_response_id=RESPONSE_ID
    )
    assert result[0] == "completed"
    assert elapsed == 0
    assert heartbeats[-1]["markedTurnTerminalEvidence"] == "codex_native_final_answer"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["phase", "correlated-event"])
async def test_legacy_projection_keeps_sixty_second_fallback(monkeypatch, missing):
    snapshot = _terminal()
    if missing == "phase":
        snapshot["items"][-1]["data"].pop("phase")
    result, elapsed, calls, _ = await _poll(
        monkeypatch,
        [snapshot],
        native_terminal_response_id=RESPONSE_ID if missing == "phase" else None,
    )
    assert result[0] == "completed"
    assert elapsed == 60
    assert len(calls) == 31


@pytest.mark.asyncio
async def test_tool_only_projection_keeps_three_hundred_second_fallback(monkeypatch):
    snapshot = _terminal()
    snapshot["items"][1:] = [_tool(), _tool(output=True)]
    result, elapsed, _, _ = await _poll(
        monkeypatch, [snapshot], native_terminal_response_id=RESPONSE_ID
    )
    assert result[0] == "completed"
    assert elapsed == 300


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "canceled", "timed_out"])
async def test_final_answer_never_overrides_nonsuccess_terminal(monkeypatch, status):
    result, elapsed, _, _ = await _poll(
        monkeypatch,
        [_terminal()],
        native_terminal_response_id=RESPONSE_ID,
        terminal_status=status,
    )
    assert result[0] == status
    assert elapsed == 60


@pytest.mark.asyncio
async def test_native_finalization_must_settle_before_fast_path(monkeypatch):
    active = _terminal()
    active["status"] = "running"
    active["active_response_id"] = RESPONSE_ID
    incomplete = _terminal()
    incomplete["items"].append(_tool("turn_diff", "diff-1"))
    completed = copy.deepcopy(incomplete)
    completed["items"].append(_tool(call_id="diff-1", output=True))
    result, elapsed, calls, _ = await _poll(
        monkeypatch,
        [active, incomplete, completed],
        native_terminal_response_id=RESPONSE_ID,
    )
    assert result[0] == "completed"
    assert elapsed == 4
    assert len(calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,expected_status,fast",
    [
        ("correlated", "completed", True),
        ("no-running", "completed", False),
        ("pre-dispatch-running", "completed", False),
        ("wrong-running-response", "completed", False),
        ("wrong-idle-response", "completed", False),
        ("child-running", "completed", False),
        ("child-idle", "completed", False),
        ("injection-completed", "completed", False),
        ("interrupted-then-idle", "canceled", False),
        ("stream-closed", "ambiguous", False),
        ("active-child", "completed", False),
        ("interrupted-child", "completed", False),
        ("completed-child", "completed", False),
        ("child-lookup-failed", "completed", False),
        ("child-lookup-timeout", "completed", False),
        ("malformed-children", "completed", False),
        ("observation-gap-during-child-read", "completed", False),
        ("new-input-during-child-read", "completed", False),
        ("failure-during-child-read", "failed", False),
        ("archived-child", "completed", False),
        ("pre-dispatch-child", "completed", False),
        ("child-update", "completed", False),
        ("resume-gap", "completed", False),
        ("historical-items", "completed", False),
        ("attached-session", "completed", False),
        ("stream-read-failed", "completed", False),
    ],
)
async def test_execution_requires_live_same_turn_lifecycle_edges(
    monkeypatch, tmp_path, scenario, expected_status, fast
):
    import asyncio

    import httpx

    from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.workflows.adapters.omnigent_client import OmnigentClientError

    terminal = _terminal()
    heartbeats = []
    awaited = []
    diagnostics = []

    def edge(status, *, response_id=RESPONSE_ID, conversation_id="session-1"):
        return {
            "type": "session.status",
            "status": status,
            "response_id": response_id,
            "conversation_id": conversation_id,
        }

    class Client:
        def __init__(self, **kwargs):
            self.posted = asyncio.Event()

        async def list_agents(self):
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload):
            return {"id": "session-1"}

        async def post_event(self, session_id, payload):
            terminal["items"][0] = _message(
                "user", execute._first_message_text(payload)
            )
            self.posted.set()
            return {}

        async def list_session_child_sessions(self, session_id):
            if scenario == "child-lookup-failed":
                raise OmnigentClientError("child list unavailable")
            if scenario == "child-lookup-timeout":
                raise TimeoutError()
            if scenario == "malformed-children":
                return {"data": []}
            if scenario == "observation-gap-during-child-read":
                diagnostics.append({"code": "omnigent_stream_read_interrupted"})
            if scenario == "new-input-during-child-read":
                terminal["pending_inputs"] = [{"content": "new work"}]
            if scenario == "failure-during-child-read":
                terminal["status"] = "failed"
            if scenario in {"active-child", "interrupted-child", "completed-child"}:
                return {
                    "data": [{"id": "child-1", "status": scenario}],
                    "has_more": False,
                }
            return {"data": [], "has_more": False}

        async def get_session(self, session_id):
            return (
                copy.deepcopy(terminal)
                if self.posted.is_set()
                else {
                    "id": "session-1",
                    "items": (
                        [_message("assistant", "Earlier work")]
                        if scenario == "historical-items"
                        else []
                    ),
                }
            )

        async def stream_events(self, session_id):
            child_event = {
                "type": "session.created",
                "conversation_id": "session-1",
                "parent_session_id": "session-1",
                "child_session_id": "child-1",
            }
            if scenario == "pre-dispatch-child":
                yield child_event
            if scenario == "pre-dispatch-running":
                yield edge("running")
            await self.posted.wait()
            if scenario not in {"no-running", "pre-dispatch-running"}:
                yield edge(
                    "running",
                    response_id=(
                        "codex_turn_other"
                        if scenario == "wrong-running-response"
                        else RESPONSE_ID
                    ),
                    conversation_id=(
                        "child-1" if scenario == "child-running" else "session-1"
                    ),
                )
            if scenario == "archived-child":
                # Archiving empties the child catalog but not this evidence.
                yield child_event
            if scenario == "child-update":
                yield {**child_event, "type": "session.child_session.updated"}
            if scenario == "resume-gap":
                yield {"type": "stream.resume_gap", "conversation_id": "session-1"}
            if scenario == "stream-closed":
                return
            if scenario == "stream-read-failed":
                raise OmnigentClientError("stream interrupted") from httpx.ReadError(
                    "closed"
                )
            if scenario == "interrupted-then-idle":
                yield {
                    "type": "session.interrupted",
                    "conversation_id": "session-1",
                    "response_id": RESPONSE_ID,
                }
            if scenario == "injection-completed":
                yield {
                    "type": "response.completed",
                    "response_id": "injection-response",
                }
            else:
                yield edge(
                    "idle",
                    response_id=(
                        "codex_turn_other"
                        if scenario == "wrong-idle-response"
                        else RESPONSE_ID
                    ),
                    conversation_id=(
                        "child-1" if scenario == "child-idle" else "session-1"
                    ),
                )

    real_wait = execute._await_marked_turn_terminal

    async def bounded_wait(**kwargs):
        awaited.append(kwargs.get("native_terminal_response_id"))
        if scenario == "observation-gap-during-child-read":
            kwargs["observation_diagnostics"] = diagnostics
        kwargs.update(quiet_period_seconds=0, tool_only_quiet_period_seconds=0)
        return await real_wait(**kwargs)

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr(execute, "OmnigentHttpClient", Client)
    monkeypatch.setattr(execute, "_await_marked_turn_terminal", bounded_wait)
    monkeypatch.setattr(execute, "_safe_heartbeat", heartbeats.append)
    monkeypatch.setattr(execute, "_TERMINAL_RECONCILIATION_INTERVAL_SECONDS", 0.01)
    execution = execute.run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-native-final",
            idempotencyKey="idem-native-final",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                }
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
        resume_session_id="session-1" if scenario == "attached-session" else None,
    )
    if expected_status == "ambiguous":
        with pytest.raises(execute.OmnigentSessionStillRunningError):
            await execution
        assert not awaited
        return
    result = await execution
    assert result.metadata["normalizedStatus"] == expected_status
    assert (
        any(
            h.get("markedTurnTerminalEvidence") == "codex_native_final_answer"
            for h in heartbeats
        )
        is fast
    )
    if fast:
        assert RESPONSE_ID in awaited


@pytest.mark.asyncio
async def test_slow_snapshot_requires_another_fresh_observation(monkeypatch):
    result, elapsed, calls, _ = await _poll(
        monkeypatch,
        [_terminal()],
        read_latencies=[9, 0],
        native_terminal_response_id=RESPONSE_ID,
    )
    assert result[0] == "completed"
    assert elapsed == 11
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_child_catalog_read_uses_encoded_session_and_one_row_limit():
    import httpx

    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

    async def handler(request):
        assert request.method == "GET"
        assert (
            request.url.raw_path == b"/v1/sessions/session%2F1/child_sessions?limit=1"
        )
        return httpx.Response(200, json={"data": [], "has_more": False})

    client = OmnigentHttpClient(
        base_url="https://omnigent.test", transport=httpx.MockTransport(handler)
    )
    assert await client.list_session_child_sessions("session/1") == {
        "data": [],
        "has_more": False,
    }
