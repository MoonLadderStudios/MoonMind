"""Unit tests for MM-1030 Omnigent terminal execution normalization."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from moonmind.omnigent.bridge_artifacts import (
    LocalOmnigentArtifactGateway,
    OmnigentArtifactError,
    OmnigentCaptureBundle,
    build_omnigent_result,
    build_omnigent_terminal_refs,
)
from moonmind.omnigent.bridge_store import (
    FIRST_MESSAGE_ITEM_FRONTIER_KEY,
    OmnigentDigestMismatchError,
)
from moonmind.omnigent.execute import (
    OmnigentContractError,
    OmnigentSessionStillRunningError,
    _agent_items,
    _await_marked_turn_terminal,
    _build_omnigent_first_message,
    _first_message_text,
    _enqueue_stream_events,
    _resolve_agent_id,
    _resolve_initial_context_message,
    _restore_active_journals,
    _session_authority_observation,
    _snapshot_confirms_current_turn_terminal,
    _snapshot_contains_current_turn_progress,
    normalize_omnigent_observation,
    run_omnigent_execution,
)
from moonmind.rag.context_injection import PromptContextResolution
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile:test",
        correlationId="corr-1",
        idempotencyKey="idem-1",
    )


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (None, (None, None)),
        ({}, (None, None)),
        ({"capabilities": {}, "status": ""}, ({}, None)),
        (
            {
                "interventionCapabilities": {
                    "sendMessage": True,
                    "unknown": "not-a-bool",
                },
                "status": "idle",
            },
            ({"sendMessage": True}, "idle"),
        ),
    ],
)
def test_session_authority_observation_preserves_absent_fields(
    snapshot: dict[str, Any] | None,
    expected: tuple[dict[str, bool] | None, str | None],
) -> None:
    assert _session_authority_observation(snapshot) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "inline_instruction", "artifact_text", "expected_base"),
    [
        ({"text": "Explicit prompt"}, "Inline prompt", None, "Explicit prompt"),
        (
            {"instructionRef": "artifact://prompt"},
            "Inline prompt",
            "Artifact prompt",
            "Artifact prompt",
        ),
        ({}, "Inline prompt", None, "Inline prompt"),
    ],
)
async def test_first_message_includes_terminal_continuation_authority(
    prompt: dict[str, Any],
    inline_instruction: str,
    artifact_text: str | None,
    expected_base: str,
) -> None:
    authority_instruction = "Execution continuation authority: none."
    request = _request()
    request.instruction_ref = inline_instruction
    request.parameters = {
        "metadata": {
            "moonmind": {
                "terminalContinuationAuthorityInstruction": authority_instruction
            }
        }
    }

    class Gateway:
        async def read_text(self, ref: str) -> str:
            assert ref == "artifact://prompt"
            assert artifact_text is not None
            return artifact_text

    message = await _build_omnigent_first_message(
        request=request,
        prompt=prompt,
        artifact_gateway=Gateway(),
    )
    text = _first_message_text(message)

    assert expected_base in text
    assert text.count(authority_instruction) == 1


@pytest.mark.asyncio
async def test_profile_bound_first_message_activates_resolved_skill_snapshot() -> None:
    request = _request()
    request.resolved_skillset_ref = "art-resolved-fix-comments"
    request.instruction_ref = "Inline task instructions"
    request.parameters = {
        "metadata": {"moonmind": {"selectedSkill": "fix-comments"}},
        "omnigent": {
            "_moonmindProfileAuthorization": {
                "providerProfileId": "codex",
                "hostLeaseRef": "lease-1",
            }
        },
    }

    class Gateway:
        async def read_text(self, _ref: str) -> str:
            raise AssertionError("the explicit prompt should not read an artifact")

    message = await _build_omnigent_first_message(
        request=request,
        prompt={"text": "Execute the selected skill"},
        artifact_gateway=Gateway(),
    )
    text = _first_message_text(message)

    assert text.startswith("Active MoonMind skill snapshot:")
    assert "Selected skill: fix-comments" in text
    assert (
        "Read `/opt/moonmind-skills/fix-comments/SKILL.md` first" in text
    )
    assert "set `MOONMIND_ACTIVE_SKILLS_DIR`" in text
    assert text.endswith("Execute the selected skill")


def test_current_turn_progress_ignores_prior_terminal_work() -> None:
    marker = """MoonMind-Omnigent-Run:
  correlationId: workflow-1
  idempotencyKey: continuation-2"""
    prior_only = {
        "items": [
            {"type": "message", "data": {"role": "assistant", "content": []}},
            {"type": "function_call_output", "data": {}},
            {
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": marker}],
                },
            },
        ]
    }
    current_progress = {
        "items": [*prior_only["items"], {"type": "function_call", "data": {}}]
    }

    assert not _snapshot_contains_current_turn_progress(prior_only, marker=marker)
    assert _snapshot_contains_current_turn_progress(current_progress, marker=marker)


def test_marked_tool_progress_requires_terminal_assistant_evidence() -> None:
    marker = """MoonMind-Omnigent-Run:
  correlationId: workflow-1
  idempotencyKey: continuation-2"""
    items = [
        {
            "type": "message",
            "data": {
                "role": "user",
                "content": [{"type": "input_text", "text": marker}],
            },
        },
        {"type": "function_call_output", "data": {}},
    ]

    assert not _snapshot_confirms_current_turn_terminal(
        {
            "status": "running",
            "active_response_id": "response-1",
            "items": items,
        },
        marker=marker,
    )
    assert not _snapshot_confirms_current_turn_terminal(
        {"status": "idle", "active_response_id": None, "items": items},
        marker=marker,
    )
    completed_items = [
        *items,
        {
            "type": "message",
            "data": {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Done"}],
            },
        },
    ]
    assert _snapshot_confirms_current_turn_terminal(
        {"status": "running", "active_response_id": None, "items": completed_items},
        marker=marker,
    )
    assert not _snapshot_confirms_current_turn_terminal(
        {"status": "running", "items": items},
        marker=marker,
    )


def test_terminal_assistant_closes_stale_call_before_turn_diff() -> None:
    """Regression for the stalled implementation turn in workflow db2c38f9."""

    marker = """MoonMind-Omnigent-Run:
  correlationId: workflow-1
  idempotencyKey: implementation-1"""
    snapshot = {
        "status": "idle",
        "active_response_id": None,
        "items": [
            {
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": marker}],
                },
            },
            {
                "type": "function_call",
                "data": {"name": "shell", "call_id": "npm-without-output"},
            },
            {
                "type": "message",
                "data": {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Completed."}],
                },
            },
            {
                "type": "function_call",
                "data": {"name": "turn_diff", "call_id": "turn-diff-1"},
            },
            {
                "type": "function_call_output",
                "data": {"call_id": "turn-diff-1", "output": "diff"},
            },
        ],
    }

    assert _snapshot_confirms_current_turn_terminal(snapshot, marker=marker)


@pytest.mark.asyncio
async def test_stream_queue_marks_pre_post_terminal_as_stale() -> None:
    release_current_event = asyncio.Event()

    class Client:
        async def stream_events(self, _session_id):
            yield {"type": "session.terminal", "status": "completed"}
            await release_current_event.wait()
            yield {"type": "session.terminal", "status": "completed"}

    queue = asyncio.Queue()
    message_posted = asyncio.Event()
    task = asyncio.create_task(
        _enqueue_stream_events(
            client=Client(),
            session_id="session-1",
            queue=queue,
            message_posted=message_posted,
        )
    )

    stale_event, stale_after_post = await queue.get()
    message_posted.set()
    release_current_event.set()
    current_event, current_after_post = await queue.get()
    assert await task is None

    assert stale_event["type"] == "session.terminal"
    assert stale_after_post is False
    assert current_event["type"] == "session.terminal"
    assert current_after_post is True


@pytest.mark.asyncio
async def test_snapshot_polling_waits_for_marked_turn_running_transition() -> None:
    marker = "MoonMind-Omnigent-Run: continuation-2"
    prior_terminal = {"status": "completed", "items": []}
    current_items = [
        {
            "type": "message",
            "data": {
                "role": "user",
                "content": [{"type": "input_text", "text": marker}],
            },
        },
        {"type": "function_call_output", "data": {}},
    ]

    class Client:
        def __init__(self) -> None:
            self.snapshots = [
                prior_terminal,
                {"status": "running", "items": current_items},
                {
                    "status": "running",
                    "active_response_id": None,
                    "items": current_items,
                },
            ]
            self.calls = 0

        async def get_session(self, _session_id: str) -> dict[str, Any]:
            index = min(self.calls, len(self.snapshots) - 1)
            self.calls += 1
            return self.snapshots[index]

    status, snapshot = await _await_marked_turn_terminal(
        client=Client(),
        session_id="session-1",
        marker=marker,
        event_count=4,
        terminal_status="completed",
        interval_seconds=0.001,
        quiet_period_seconds=0.002,
        tool_only_quiet_period_seconds=0.002,
    )

    assert status == "completed"
    assert snapshot["items"] == current_items


@pytest.mark.asyncio
async def test_snapshot_polling_accepts_marked_progress_while_session_is_idle() -> None:
    marker = "MoonMind-Omnigent-Run: continuation-2"
    terminal = {
        "status": "idle",
        "items": [
            {
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": marker}],
                },
            },
            {
                "type": "message",
                "data": {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            },
        ],
    }

    class Client:
        async def get_session(self, _session_id: str) -> dict[str, Any]:
            return terminal

    status, snapshot = await _await_marked_turn_terminal(
        client=Client(),
        session_id="session-1",
        marker=marker,
        event_count=4,
        terminal_status="completed",
        interval_seconds=0.001,
        quiet_period_seconds=0.002,
    )

    assert status == "completed"
    assert snapshot is terminal


@pytest.mark.asyncio
async def test_snapshot_polling_uses_pre_dispatch_item_ids_when_marker_is_evicted() -> None:
    """A capped stock snapshot must not erase current-turn terminal evidence."""

    marker = "MoonMind-Omnigent-Run: continuation-with-many-tools"
    baseline_item_ids = frozenset({"prior-1", "prior-2"})
    terminal = {
        "status": "idle",
        "active_response_id": None,
        "items": [
            {
                "id": "call-current",
                "type": "function_call",
                "data": {"call_id": "call-current"},
            },
            {
                "id": "output-current",
                "type": "function_call_output",
                "data": {"call_id": "call-current"},
            },
            {
                "id": "assistant-current",
                "type": "message",
                "data": {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            },
        ],
    }

    class Client:
        async def get_session(self, _session_id: str) -> dict[str, Any]:
            return terminal

    assert not _snapshot_contains_current_turn_progress(terminal, marker=marker)
    assert _snapshot_contains_current_turn_progress(
        terminal,
        marker=marker,
        baseline_item_ids=baseline_item_ids,
    )
    assert not _snapshot_contains_current_turn_progress(
        {
            "status": "idle",
            "items": [{"id": "prior-1", "type": "function_call_output"}],
        },
        marker=marker,
        baseline_item_ids=baseline_item_ids,
    )
    status, snapshot = await _await_marked_turn_terminal(
        client=Client(),
        session_id="session-1",
        marker=marker,
        baseline_item_ids=baseline_item_ids,
        event_count=9,
        terminal_status="completed",
        interval_seconds=0.001,
        quiet_period_seconds=0.002,
    )

    assert status == "completed"
    assert snapshot is terminal


@pytest.mark.asyncio
async def test_snapshot_polling_preserves_marked_provider_failure() -> None:
    marker = "MoonMind-Omnigent-Run: failed-turn"
    failed = {
        "status": "failed",
        "items": [
            {
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": marker}],
                },
            },
            {"type": "function_call_output", "data": {}},
        ],
    }

    class Client:
        async def get_session(self, _session_id: str) -> dict[str, Any]:
            return failed

    status, snapshot = await _await_marked_turn_terminal(
        client=Client(),
        session_id="session-1",
        marker=marker,
        event_count=2,
        terminal_status="completed",
        interval_seconds=0.001,
    )

    assert status == "failed"
    assert snapshot is failed


@pytest.mark.asyncio
async def test_snapshot_polling_waits_for_quiet_after_stale_idle_tool_output() -> None:
    marker = "MoonMind-Omnigent-Run: continuation-2"
    marked_user = {
        "id": "item-user",
        "type": "message",
        "status": "completed",
        "data": {
            "role": "user",
            "content": [{"type": "input_text", "text": marker}],
        },
    }
    snapshots = [
        {
            "status": "idle",
            "items": [
                marked_user,
                {
                    "id": "assistant-preamble",
                    "type": "message",
                    "status": "completed",
                    "data": {
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "I will inspect it."}
                        ],
                    },
                },
            ],
        },
        {
            "status": "idle",
            "items": [
                marked_user,
                {
                    "id": "assistant-preamble",
                    "type": "message",
                    "status": "completed",
                    "data": {
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "I will inspect it."}
                        ],
                    },
                },
                {
                    "id": "call-1",
                    "type": "function_call",
                    "status": "completed",
                    "data": {"call_id": "call-1"},
                },
            ],
        },
        {
            "status": "idle",
            "items": [
                marked_user,
                {
                    "id": "assistant-preamble",
                    "type": "message",
                    "status": "completed",
                    "data": {
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "I will inspect it."}
                        ],
                    },
                },
                {
                    "id": "call-1",
                    "type": "function_call",
                    "status": "completed",
                    "data": {"call_id": "call-1"},
                },
                {
                    "id": "output-1",
                    "type": "function_call_output",
                    "status": "completed",
                    "data": {"call_id": "call-1"},
                },
            ],
        },
        {
            "status": "idle",
            "items": [
                marked_user,
                {
                    "id": "assistant-preamble",
                    "type": "message",
                    "status": "completed",
                    "data": {
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "I will inspect it."}
                        ],
                    },
                },
                {
                    "id": "call-1",
                    "type": "function_call",
                    "status": "completed",
                    "data": {"call_id": "call-1"},
                },
                {
                    "id": "output-1",
                    "type": "function_call_output",
                    "status": "completed",
                    "data": {"call_id": "call-1"},
                },
                {
                    "id": "call-2",
                    "type": "function_call",
                    "status": "completed",
                    "data": {"call_id": "call-2"},
                },
            ],
        },
        {
            "status": "idle",
            "items": [
                marked_user,
                {
                    "id": "assistant-preamble",
                    "type": "message",
                    "status": "completed",
                    "data": {
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "I will inspect it."}
                        ],
                    },
                },
                {
                    "id": "call-1",
                    "type": "function_call",
                    "status": "completed",
                    "data": {"call_id": "call-1"},
                },
                {
                    "id": "output-1",
                    "type": "function_call_output",
                    "status": "completed",
                    "data": {"call_id": "call-1"},
                },
                {
                    "id": "call-2",
                    "type": "function_call",
                    "status": "completed",
                    "data": {"call_id": "call-2"},
                },
                {
                    "id": "output-2",
                    "type": "function_call_output",
                    "status": "completed",
                    "data": {"call_id": "call-2"},
                },
            ],
        },
    ]

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        async def get_session(self, _session_id: str) -> dict[str, Any]:
            index = min(self.calls, len(snapshots) - 1)
            self.calls += 1
            return snapshots[index]

    client = Client()
    status, snapshot = await _await_marked_turn_terminal(
        client=client,
        session_id="session-1",
        marker=marker,
        event_count=4,
        terminal_status="completed",
        interval_seconds=0.001,
        quiet_period_seconds=0.02,
        tool_only_quiet_period_seconds=0.02,
    )

    assert status == "completed"
    assert snapshot["items"][-1]["id"] == "output-2"
    assert client.calls >= 6


@pytest.mark.asyncio
async def test_snapshot_polling_does_not_count_slow_stale_read_as_quiet() -> None:
    """Provider read latency cannot hide tools produced during that read."""

    marker = "MoonMind-Omnigent-Run: slow-snapshot-race"
    marked_user = {
        "id": "item-user",
        "type": "message",
        "status": "completed",
        "data": {
            "role": "user",
            "content": [{"type": "input_text", "text": marker}],
        },
    }
    assistant_snapshot = {
        "status": "idle",
        "items": [
            marked_user,
            {
                "id": "assistant-preamble",
                "type": "message",
                "status": "completed",
                "data": {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Inspecting"}],
                },
            },
        ],
    }
    tool_snapshot = {
        "status": "idle",
        "items": [
            *assistant_snapshot["items"],
            {
                "id": "call-late",
                "type": "function_call",
                "status": "completed",
                "data": {"call_id": "call-late"},
            },
            {
                "id": "output-late",
                "type": "function_call_output",
                "status": "completed",
                "data": {"call_id": "call-late"},
            },
        ],
    }

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        async def get_session(self, _session_id: str) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 2:
                # This stale response arrives after the entire quiet period.
                await asyncio.sleep(0.03)
                return assistant_snapshot
            return assistant_snapshot if self.calls == 1 else tool_snapshot

    client = Client()
    status, snapshot = await _await_marked_turn_terminal(
        client=client,
        session_id="session-1",
        marker=marker,
        event_count=4,
        terminal_status="completed",
        interval_seconds=0.001,
        quiet_period_seconds=0.02,
        tool_only_quiet_period_seconds=0.02,
    )

    assert status == "completed"
    assert snapshot["items"][-1]["id"] == "output-late"
    assert client.calls >= 4


@pytest.mark.asyncio
async def test_initial_context_is_persisted_before_first_message_digest(
    monkeypatch, tmp_path
) -> None:
    request = _request()
    request.parameters = {"metadata": {}}
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)
    recorded: list[dict[str, Any]] = []

    async def inject_context(self, *, request, workspace_path):
        request.parameters["metadata"]["moonmind"] = {
            "latestContextPackRef": "artifact://context/pack.json",
            "retrievedContextDigest": "sha256:pack",
            "retrievalQueryDigest": "sha256:query",
            "retrievalQueryPreview": "Do work",
            "retrievedContextTransport": "gateway",
            "retrievedContextItemCount": 2,
            "retrievedContextSources": ["docs/a.md", "docs/b.md"],
            "retrievalCollections": ["canonical"],
            "retrievalScope": {"repository": "org/repo", "run": "corr-1"},
            "retrievalBudgets": {"tokens": 500, "latency_ms": 1000},
            "retrievalUsage": {"tokens": 20, "latency_ms": 12},
            "retrievalOverlay": {"policy": "include", "freshness": "fresh"},
            "retrievalEmbeddingConfigRef": "sha256:embedding",
            "retrievalFailureClass": None,
            "retrievalMode": "semantic",
            "retrievalContextTruncated": True,
            "retrievalDurabilityAuthority": "artifact_ref",
        }
        framed = "SYSTEM SAFETY NOTICE:\nBEGIN_RETRIEVED_CONTEXT\nuntrusted\nEND_RETRIEVED_CONTEXT\n\nDo work"
        request.instruction_ref = framed
        return PromptContextResolution(instruction=framed, items_count=2)

    class Store:
        async def record_initial_context(self, key, *, evidence):
            assert key == "idem-1"
            recorded.append(dict(evidence))

    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService.inject_context",
        inject_context,
    )
    message, evidence = await _resolve_initial_context_message(
        request=request,
        first_message={
            "type": "message",
            "data": {
                "role": "user",
                "content": [{"type": "input_text", "text": "Do work"}],
            },
        },
        artifact_gateway=gateway,
        run_store=Store(),
        durable_row=None,
        workspace=str(tmp_path),
    )

    assert "SYSTEM SAFETY NOTICE" in _first_message_text(message)
    assert evidence["contextPackRef"] == "artifact://context/pack.json"
    assert evidence["state"] == "completed"
    assert evidence["truncated"] is True
    assert evidence["contextPackDigest"] == "sha256:pack"
    assert evidence["queryDigest"] == "sha256:query"
    assert evidence["collections"] == ["canonical"]
    assert evidence["scope"] == {"repository": "org/repo", "run": "corr-1"}
    assert evidence["sources"] == ["docs/a.md", "docs/b.md"]
    assert evidence["budgets"]["tokens"] == 500
    assert evidence["embeddingConfigRef"] == "sha256:embedding"
    assert evidence["firstMessageConsumedContextRef"] is True
    assert evidence["preparedMessageRef"].startswith("artifact://omnigent/")
    prepared_payload = json.dumps(
        message, sort_keys=True, separators=(",", ":")
    ).encode()
    assert evidence["preparedMessageDigest"] == hashlib.sha256(
        prepared_payload
    ).hexdigest()
    assert message["metadata"]["moonmindIdempotencyKey"] == "idem-1"
    assert "MoonMind-Omnigent-Run:" in _first_message_text(message)
    assert recorded == [evidence]


@pytest.mark.asyncio
async def test_initial_context_pack_is_published_through_artifact_gateway(
    monkeypatch, tmp_path
) -> None:
    request = _request()
    request.parameters = {"metadata": {}}
    context_path = tmp_path / "workspace-context.json"
    context_path.write_text('{"items":[]}\n', encoding="utf-8")
    gateway = LocalOmnigentArtifactGateway(root=tmp_path / "published")

    async def inject_context(self, *, request, workspace_path):
        request.parameters["metadata"]["moonmind"] = {
            "latestContextPackRef": "artifacts/context/workspace-context.json",
            "retrievedContextDigest": "sha256:pack",
            "retrievedContextItemCount": 0,
            "retrievalMode": "semantic",
        }
        return PromptContextResolution(
            instruction="Do work", artifact_path=context_path
        )

    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService.inject_context",
        inject_context,
    )
    _, evidence = await _resolve_initial_context_message(
        request=request,
        first_message={
            "type": "message",
            "data": {
                "role": "user",
                "content": [{"type": "input_text", "text": "Do work"}],
            },
        },
        artifact_gateway=gateway,
        run_store=None,
        durable_row=None,
        workspace=str(tmp_path),
    )

    assert evidence["contextPackRef"].startswith("artifact://omnigent/")
    assert (
        request.parameters["metadata"]["moonmind"]["retrievalDurabilityAuthority"]
        == "artifact_gateway"
    )


@pytest.mark.asyncio
async def test_required_context_artifact_publication_failure_fails_before_commit(
    monkeypatch, tmp_path
) -> None:
    request = _request()
    request.parameters = {"metadata": {}, "rag": {"required": True}}
    context_path = tmp_path / "workspace-context.json"
    context_path.write_text('{"items":[]}\n', encoding="utf-8")

    async def inject_context(self, *, request, workspace_path):
        request.parameters["metadata"]["moonmind"] = {
            "latestContextPackRef": "artifacts/context/workspace-context.json",
            "retrievedContextDigest": "sha256:pack",
            "retrievalMode": "semantic",
        }
        return PromptContextResolution(
            instruction="Do work", artifact_path=context_path
        )

    class FailingGateway(LocalOmnigentArtifactGateway):
        async def write_text(self, **kwargs):
            if kwargs.get("link_type") == "input.context-pack":
                raise OmnigentArtifactError("artifact service unavailable")
            return await super().write_text(**kwargs)

    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService.inject_context",
        inject_context,
    )
    with pytest.raises(
        OmnigentContractError,
        match="required initial context artifact publication failed",
    ):
        await _resolve_initial_context_message(
            request=request,
            first_message={
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Do work"}],
                },
            },
            artifact_gateway=FailingGateway(root=tmp_path / "published"),
            run_store=None,
            durable_row=None,
            workspace=str(tmp_path),
        )


@pytest.mark.asyncio
async def test_initial_context_retry_reuses_exact_prepared_message(
    monkeypatch, tmp_path
) -> None:
    request = _request()
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)
    prepared = {
        "type": "message",
        "data": {
            "role": "user",
            "content": [{"type": "input_text", "text": "persisted context message"}],
        },
    }
    prepared_ref = await gateway.write_json(
        request=request,
        name="input.omnigent.first_message.prepared.json",
        payload=prepared,
        link_type="input.omnigent.first_message.prepared",
    )

    class Row:
        metadata_ = {
            "initialRetrieval": {
                "state": "completed",
                "contextPackRef": "artifact://context/pack.json",
                "preparedMessageRef": prepared_ref,
            }
        }

    async def unexpected_injection(*args, **kwargs):
        raise AssertionError("retry must not rerun retrieval")

    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService.inject_context",
        unexpected_injection,
    )
    message, evidence = await _resolve_initial_context_message(
        request=request,
        first_message={
            "type": "message",
            "data": {
                "role": "user",
                "content": [{"type": "input_text", "text": "new message"}],
            },
        },
        artifact_gateway=gateway,
        run_store=None,
        durable_row=Row(),
        workspace=str(tmp_path),
    )

    assert message == prepared
    assert evidence == Row.metadata_["initialRetrieval"]


@pytest.mark.asyncio
@pytest.mark.parametrize("artifact_payload", ["[]", "{not-json"])
async def test_initial_context_retry_rejects_corrupt_prepared_message(
    tmp_path, artifact_payload: str
) -> None:
    request = _request()
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)
    prepared_ref = await gateway.write_text(
        request=request,
        name="input.omnigent.first_message.prepared.json",
        payload=artifact_payload,
        link_type="input.omnigent.first_message.prepared",
        content_type="application/json",
    )

    class Row:
        metadata_ = {"initialRetrieval": {"preparedMessageRef": prepared_ref}}

    with pytest.raises((OmnigentContractError, json.JSONDecodeError)):
        await _resolve_initial_context_message(
            request=request,
            first_message={
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Do work"}],
                },
            },
            artifact_gateway=gateway,
            run_store=None,
            durable_row=Row(),
            workspace=str(tmp_path),
        )


@pytest.mark.asyncio
async def test_initial_context_retry_rejects_missing_prepared_message(tmp_path) -> None:
    class Row:
        metadata_ = {
            "initialRetrieval": {
                "preparedMessageRef": "artifact://omnigent/missing/prepared.json"
            }
        }

    with pytest.raises(OmnigentArtifactError):
        await _resolve_initial_context_message(
            request=_request(),
            first_message={
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Do work"}],
                },
            },
            artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
            run_store=None,
            durable_row=Row(),
            workspace=str(tmp_path),
        )


@pytest.mark.asyncio
async def test_required_initial_context_fails_before_message_commit(
    monkeypatch, tmp_path
) -> None:
    request = _request()
    request.parameters = {"rag": {"required": True}, "metadata": {}}

    async def disabled_context(self, *, request, workspace_path):
        request.parameters["metadata"]["moonmind"] = {
            "retrievalMode": "disabled",
            "retrievalDisabledReason": "retrieval_gateway_unavailable",
        }
        return PromptContextResolution(instruction=request.instruction_ref or "")

    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService.inject_context",
        disabled_context,
    )
    with pytest.raises(OmnigentContractError, match="required initial context"):
        await _resolve_initial_context_message(
            request=request,
            first_message={
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Do work"}],
                },
            },
            artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
            run_store=None,
            durable_row=None,
            workspace=str(tmp_path),
        )


@pytest.mark.asyncio
async def test_restore_active_journals_preserves_committed_retry_prefix(tmp_path) -> None:
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)
    request = _request()
    raw_ref = await gateway.write_text(
        request=request,
        name="runtime.omnigent.sse.raw.jsonl",
        payload='{"type":"response.delta","token":"[REDACTED]"}\n',
        link_type="runtime.omnigent.sse.raw",
        content_type="application/x-ndjson",
    )
    normalized_ref = await gateway.write_text(
        request=request,
        name="runtime.omnigent.sse.normalized.jsonl",
        payload='{"eventType":"response.delta","sequence":1}\n',
        link_type="runtime.omnigent.sse.normalized",
        content_type="application/x-ndjson",
    )

    class DurableRow:
        raw_events_ref = raw_ref
        normalized_events_ref = normalized_ref

    raw, normalized = await _restore_active_journals(
        artifact_gateway=gateway, durable_row=DurableRow()
    )

    assert raw == [{"type": "response.delta", "token": "[REDACTED]"}]
    assert normalized == [{"eventType": "response.delta", "sequence": 1}]


def _bundle(**overrides: Any) -> OmnigentCaptureBundle:
    payload = {
        "output_refs": ["artifact://omnigent/corr-1/output.omnigent.snapshot.final.json"],
        "diagnostics_ref": "artifact://omnigent/corr-1/diagnostics.omnigent.json",
        "capture_manifest_ref": "artifact://omnigent/corr-1/output.omnigent.capture_manifest.json",
        "metadata_refs": {
            "captureManifestRef": (
                "artifact://omnigent/corr-1/output.omnigent.capture_manifest.json"
            )
        },
    }
    payload.update(overrides)
    return OmnigentCaptureBundle(**payload)


def test_build_terminal_refs_persists_router_terminal_metadata() -> None:
    refs = build_omnigent_terminal_refs(
        _bundle(),
        terminal_status="failed",
        final_snapshot={"summary": "provider failed", "failureCode": "provider_error"},
    )

    assert refs["failureClass"] == "execution_error"
    assert refs["failureCode"] == "provider_error"
    assert refs["summary"] == "provider failed"


def test_normalize_waiting_with_elicitation_is_internal_awaiting_approval() -> None:
    assert (
        normalize_omnigent_observation(
            {"session": {"status": "waiting"}, "pending_inputs": [{"id": "el_1"}]}
        )
        == "awaiting_approval"
    )


def test_normalize_unknown_status_raises_contract_error() -> None:
    with pytest.raises(OmnigentContractError, match="Unsupported Omnigent status"):
        normalize_omnigent_observation({"session": {"status": "mystery"}})


def test_normalize_nested_response_terminal_status() -> None:
    assert (
        normalize_omnigent_observation(
            {"type": "response.output_item.done", "response": {"status": "completed"}}
        )
        == "completed"
    )
    assert (
        normalize_omnigent_observation(
            {"data": {"response": {"status": "failed"}}}
        )
        == "failed"
    )


def test_build_omnigent_result_is_compact_terminal_success() -> None:
    result = build_omnigent_result(
        request=_request(),
        terminal_status="completed",
        session_id="sess-1",
        agent_id="agent-1",
        final_snapshot={
            "summary": "finished",
            "outputRefs": ["artifact://transcript", "artifact://snapshot"],
            "diagnosticsRef": "artifact://diagnostics",
            "captureManifestRef": "artifact://capture",
        },
        event_count=12,
        capture_bundle=_bundle(
            output_refs=["artifact://transcript", "artifact://snapshot"],
            diagnostics_ref="artifact://diagnostics",
            capture_manifest_ref="artifact://capture",
            metadata_refs={"captureManifestRef": "artifact://capture"},
        ),
    )

    assert result.failure_class is None


def test_build_omnigent_result_exposes_initial_context_pack_ref() -> None:
    request = _request()
    request.parameters = {
        "metadata": {
            "moonmind": {
                "latestContextPackRef": "artifact://context/input.context-pack.json"
            }
        }
    }

    result = build_omnigent_result(
        request=request,
        terminal_status="completed",
        session_id="session-1",
        agent_id="agent-1",
        final_snapshot={"summary": "done"},
        event_count=1,
        capture_bundle=_bundle(
            output_refs=["artifact://transcript"],
            diagnostics_ref="artifact://diagnostics",
        ),
    )

    assert result.metadata["initialContextPackRef"] == (
        "artifact://context/input.context-pack.json"
    )
    assert result.provider_error_code is None
    assert result.output_refs == ["artifact://transcript"]
    assert result.diagnostics_ref == "artifact://diagnostics"
    assert result.metadata["providerName"] == "omnigent"
    assert result.metadata["normalizedStatus"] == "completed"
    assert result.metadata["captureManifestRef"] == (
        "artifact://omnigent/corr-1/output.omnigent.capture_manifest.json"
    )


def test_build_omnigent_result_maps_snake_case_metadata() -> None:
    result = build_omnigent_result(
        request=_request(),
        terminal_status="completed",
        session_id="sess-1",
        agent_id="agent-1",
        final_snapshot={
            "summary": "finished",
            "host_type": "external",
            "capture_manifest_ref": "artifact://capture",
            "github_pr_url": "https://github.example/pr/1",
        },
        event_count=1,
        capture_bundle=_bundle(),
    )

    assert result.metadata["hostType"] == "external"
    assert result.metadata["captureManifestRef"].startswith("artifact://omnigent/")
    assert result.metadata["githubPrUrl"] == "https://github.example/pr/1"


def test_agent_items_ignores_unexpected_payload_shape() -> None:
    assert _agent_items({"items": "unexpected"}) == []


def test_resolve_agent_id_rejects_unknown_requested_name() -> None:
    with pytest.raises(OmnigentContractError, match="could not be resolved"):
        _resolve_agent_id(
            agents_payload={"items": [{"id": "agent-1", "name": "known"}]},
            requested_name="missing",
        )


def test_build_omnigent_result_is_terminal_failure_with_provider_error() -> None:
    result = build_omnigent_result(
        request=_request(),
        terminal_status="failed",
        session_id="sess-1",
        agent_id="agent-1",
        final_snapshot={"summary": "provider failed"},
        event_count=2,
        capture_bundle=_bundle(),
        provider_error_code="omnigent_failed",
    )

    assert result.failure_class == "execution_error"
    assert result.provider_error_code == "omnigent_failed"
    assert all(not ref.startswith("omnigent://") for ref in result.output_refs)
    assert not result.diagnostics_ref.startswith("omnigent://")
    assert result.metadata["normalizedStatus"] == "failed"


def test_build_omnigent_result_uses_valid_failure_class_for_timeout() -> None:
    result = build_omnigent_result(
        request=_request(),
        terminal_status="timed_out",
        session_id="sess-1",
        agent_id=None,
        final_snapshot={"summary": "timed out"},
        event_count=1,
        capture_bundle=_bundle(),
    )

    assert result.failure_class == "system_error"
    assert result.metadata["normalizedStatus"] == "timed_out"


@pytest.mark.asyncio
async def test_run_omnigent_execution_waits_for_terminal_result(
    monkeypatch,
    tmp_path,
) -> None:
    created_clients: list[object] = []
    heartbeats: list[dict[str, Any]] = []
    large_provider_state = "large-provider-state:" + ("x" * 12_000)

    class FakeClient:
        def __init__(self, **_: object) -> None:
            self.posted_events: list[dict[str, object]] = []
            self.stream_started = False
            created_clients.append(self)

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(
            self, payload: dict[str, object]
        ) -> dict[str, object]:
            assert payload["agent_id"] == "agent-1"
            assert payload["labels"]["moonmind.issue"] == "MM-1059"
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            assert session_id == "session-1"
            assert self.stream_started is True
            self.posted_events.append(payload)
            return {"pending_id": "pending-1"}

        async def stream_events(self, session_id: str):
            assert session_id == "session-1"
            self.stream_started = True
            yield {"session": {"status": "running"}}
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            assert session_id == "session-1"
            return {
                "status": "completed",
                "summary": "done",
                "outputRefs": ["artifact://final"],
                "diagnosticsRef": "artifact://diagnostics",
                "transcript": large_provider_state,
            }

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv(
        "OMNIGENT_SERVER_URL",
        "https://operator@omnigent.test:443/api?debug=1",
    )
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    monkeypatch.setattr(
        "moonmind.omnigent.execute._safe_heartbeat",
        lambda details: heartbeats.append(details),
    )

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "title": "Execute Omnigent",
                "omnigent": {
                    "endpointRef": "endpoint:test",
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    assert result.summary == "done"
    assert result.output_refs


@pytest.mark.asyncio
async def test_run_omnigent_execution_rejects_canonical_delivery_without_authority(
    monkeypatch,
    tmp_path,
) -> None:
    provider_mutations: list[str] = []

    class Row:
        bridge_session_id = "bridge-1"
        moonmind_workflow_id = "corr-canonical"
        moonmind_agent_run_id = None
        omnigent_session_id = None
        status = "declared"
        first_message_posted_at = None

    class Store:
        async def get_binding(self, *_args, **_kwargs):
            return None

        async def get_or_create(self, **_kwargs):
            return Row()

        async def get_canonical_session(self, _session_id):
            return None

        async def record_canonical_turn_delivery(self, *_args, **_kwargs):
            raise AssertionError("delivery cannot precede canonical authority")

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def list_agents(self):
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, _payload):
            provider_mutations.append("create_session")
            return {"id": "session-1"}

        async def post_event(self, _session_id, _payload):
            provider_mutations.append("post_event")
            return {}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-canonical",
            idempotencyKey="idem-canonical",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "canonical turn first"},
                }
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
        run_store=Store(),
    )

    assert result.provider_error_code == "omnigent_contract_error"
    assert "canonical session/turn persistence" in result.summary
    assert provider_mutations == []
    assert all(ref.startswith("artifact://omnigent/") for ref in result.output_refs)
    assert result.diagnostics_ref.startswith("artifact://omnigent/")
    assert result.metadata["externalStateRef"].startswith("artifact://omnigent/")
    assert "workspaceRootRef" not in result.metadata
    assert result.metadata["checkpointKind"] == "external_state_ref"
    assert result.metadata["idempotencyKey"] == "idem-1"
    assert result.metadata["normalizedStatus"] == "completed"
    result_payload = result.model_dump(by_alias=True, mode="json")
    assert large_provider_state not in json.dumps(result_payload)
    external_state = json.loads(
        (tmp_path / "corr-1" / "checkpoint.omnigent.external_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert external_state["sourceIssue"] == "MM-1077"
    assert external_state["endpoint"] == {
        "endpointRef": "endpoint:test",
        "serverUrl": "https://omnigent.test:443/api",
    }
    assert external_state["correlation"] == {
        "correlationId": "corr-1",
        "idempotencyKey": "idem-1",
        "omnigentSessionId": "session-1",
        "omnigentAgentId": "agent-1",
    }
    assert external_state["firstMessage"]["digest"]
    assert external_state["firstMessage"]["requestRef"].startswith(
        "artifact://omnigent/"
    )
    assert external_state["firstMessage"]["responseRef"].startswith(
        "artifact://omnigent/"
    )
    assert external_state["firstMessage"]["posted"] is True
    assert external_state["reattachState"]["initialSnapshotRef"].startswith(
        "artifact://omnigent/"
    )
    assert external_state["streamRefs"]["rawSseStreamRef"].startswith(
        "artifact://omnigent/"
    )
    assert external_state["snapshotRefs"]["finalSnapshotRef"].startswith(
        "artifact://omnigent/"
    )
    assert external_state["terminalResultRefs"]["diagnosticsRef"] == result.diagnostics_ref
    assert external_state["patchEvidence"]["patchUnavailable"] is True
    assert external_state["patchEvidence"]["diagnostics"][0]["code"] == (
        "omnigent_patch_unavailable"
    )
    assert large_provider_state not in json.dumps(external_state)
    assert created_clients
    assert heartbeats
    assert all("normalizedStatus" in heartbeat for heartbeat in heartbeats)
    assert all("eventsCaptured" in heartbeat for heartbeat in heartbeats)
    event_types = [
        heartbeat.get("eventType")
        for heartbeat in heartbeats
        if "eventType" in heartbeat
    ]
    assert event_types == ["", "response.completed"]


@pytest.mark.asyncio
async def test_run_omnigent_execution_accepts_native_idle_turn_edge(
    monkeypatch,
    tmp_path,
) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base, OmnigentBridgeSession
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore

    awaited: dict[str, object] = {}
    terminal_snapshot = {
        "status": "idle",
        "active_response_id": None,
        "summary": "done after native idle edge",
        "items": [
            {
                "id": "current-assistant",
                "type": "message",
                "data": {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            }
        ],
    }

    class FakeClient:
        def __init__(self, **_: object) -> None:
            self.posted = asyncio.Event()

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            self.posted.set()
            return {}

        async def stream_events(self, session_id: str):
            await self.posted.wait()
            yield {"type": "session.status", "status": "idle"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return (
                terminal_snapshot
                if self.posted.is_set()
                else {"status": "idle", "items": []}
            )

    async def capture_terminal_wait(**kwargs: object):
        awaited.update(kwargs)
        return "completed", terminal_snapshot

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    monkeypatch.setattr(
        "moonmind.omnigent.execute._await_marked_turn_terminal",
        capture_terminal_wait,
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = OmnigentBridgeSessionStore(session_maker)

    try:
        result = await run_omnigent_execution(
            AgentExecutionRequest(
                agentKind="external",
                agentId="omnigent",
                correlationId="corr-idle-edge",
                idempotencyKey="idem-idle-edge",
                parameters={
                    "omnigent": {
                        "agent": {"agentName": "codex-native-ui"},
                        "session": {"allowEmptyWorkspace": True},
                        "prompt": {"text": "Do the task"},
                    },
                },
            ),
            artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
            run_store=store,
        )

        async with session_maker() as session:
            row = (
                await session.execute(
                    select(OmnigentBridgeSession).where(
                        OmnigentBridgeSession.idempotency_key == "idem-idle-edge"
                    )
                )
            ).scalar_one()
            bridge_session_id = row.bridge_session_id
        indexed_events = await store.list_events(bridge_session_id)
    finally:
        await engine.dispose()

    assert result.summary == "done after native idle edge"
    assert result.metadata["normalizedStatus"] == "completed"
    assert awaited["terminal_status"] == "completed"
    assert awaited["baseline_item_ids"] == frozenset()
    assert indexed_events[-1].event_type == "session.final_snapshot"
    assert indexed_events[-1].normalized_status == "completed"

    normalized_path = tmp_path / "corr-idle-edge" / "runtime.omnigent.sse.normalized.jsonl"
    normalized_events = [
        json.loads(line)
        for line in normalized_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert normalized_events[-1]["type"] == "session.final_snapshot"
    assert normalized_events[-1]["normalizedStatus"] == "completed"


@pytest.mark.asyncio
async def test_run_omnigent_execution_reports_httpx_transport_errors(
    monkeypatch,
) -> None:
    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            raise httpx.ConnectError("connection failed")

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        )
    )

    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "omnigent_http_error"
    assert result.metadata["normalizedStatus"] == "failed"
    assert result.metadata["externalStateRef"].startswith("artifact://omnigent/")
    assert result.metadata["checkpointKind"] == "external_state_ref"
    assert "workspaceRootRef" not in result.metadata


@pytest.mark.asyncio
async def test_run_omnigent_execution_uses_nested_session_parameters(
    monkeypatch,
) -> None:
    captured_session_payloads: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            raise AssertionError("agentId should avoid list_agents lookup")

        async def create_session(
            self, payload: dict[str, object]
        ) -> dict[str, object]:
            captured_session_payloads.append(payload)
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            return {}

        async def stream_events(self, session_id: str):
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "status": "completed",
                "summary": "done",
                "hostType": "external",
                "workspace": "/workspace/repo",
            }

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentId": "agent-1"},
                    "session": {
                        "hostType": "external",
                        "hostId": "host-1",
                        "workspace": "/workspace/repo",
                        "modelOverride": "codex-special",
                        "reasoningEffort": "high",
                    },
                },
            },
        )
    )

    assert captured_session_payloads == [
        {
            "agent_id": "agent-1",
            "title": "MoonMind Agent Task",
            "idempotency_key": "idem-1",
            "labels": {
                "moonmind.correlation_id": "corr-1",
                "moonmind.idempotency_key": "idem-1",
                "moonmind.issue": "MM-1059",
            },
            "host_type": "external",
            "workspace": "/workspace/repo",
            "host_id": "host-1",
            "model_override": "codex-special",
            "reasoning_effort": "high",
            "terminal_launch_args": [],
        }
    ]
    assert result.metadata["hostType"] == "external"
    assert result.metadata["workspace"] == "/workspace/repo"


@pytest.mark.asyncio
async def test_run_omnigent_execution_derives_managed_workspace_from_workspace_spec(
    monkeypatch,
) -> None:
    captured_session_payloads: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            raise AssertionError("agentId should avoid list_agents lookup")

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            captured_session_payloads.append(payload)
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            return {}

        async def stream_events(self, session_id: str):
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "completed", "summary": "done"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            workspaceSpec={
                "repository": "https://github.com/org/repo",
                "branch": "feature-branch",
            },
            parameters={
                "omnigent": {
                    "agent": {"agentId": "agent-1"},
                    "session": {"hostType": "managed"},
                },
            },
        )
    )

    assert result.failure_class is None
    assert captured_session_payloads[0]["workspace"] == (
        "https://github.com/org/repo#feature-branch"
    )


@pytest.mark.asyncio
async def test_run_omnigent_execution_preserves_session_after_transport_error(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            calls.append(("create_session", payload))
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            raise httpx.ConnectError("provider write failed")

        async def stream_events(self, session_id: str):
            if False:
                yield {}

        async def delete_session(
            self,
            session_id: str,
            *,
            delete_branch: bool = False,
        ) -> dict[str, object]:
            calls.append(
                (
                    "delete_session",
                    {"session_id": session_id, "delete_branch": delete_branch},
                )
            )
            return {}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    artifact_gateway = LocalOmnigentArtifactGateway(root=tmp_path)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        ),
        artifact_gateway=artifact_gateway,
    )

    assert result.failure_class == "integration_error"
    assert result.diagnostics_ref.startswith("artifact://omnigent/")
    assert result.metadata["externalStateRef"].startswith("artifact://omnigent/")
    external_state = json.loads(
        await artifact_gateway.read_text(result.metadata["externalStateRef"])
    )
    assert external_state["retry"]["sessionResolution"] == "created"
    assert external_state["retry"]["firstMessageOutcome"] == "pending"
    assert external_state["firstMessage"]["digest"]
    assert external_state["firstMessage"]["idempotencyMarkerPresent"] is True
    assert external_state["artifactRefs"]["diagnosticsRef"] == result.diagnostics_ref
    assert all(call[0] != "delete_session" for call in calls)


@pytest.mark.asyncio
async def test_run_omnigent_execution_harvests_before_delete_on_cancellation(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[tuple[str, object]] = []
    httpx_clients: list[object] = []
    cleanup_harvest_client_ids: list[int] = []

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.httpx_client = kwargs["client"]
            httpx_clients.append(self.httpx_client)

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            calls.append(("create_session", payload))
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            calls.append(("post_event", payload))
            return {}

        async def stream_events(self, session_id: str):
            if False:
                yield {}

        async def get_session(self, session_id: str) -> dict[str, object]:
            calls.append(("get_session", session_id))
            return {"status": "running"}

        async def interrupt(self, session_id: str) -> dict[str, object]:
            calls.append(("interrupt", session_id))
            return {}

        async def stop_session(self, session_id: str) -> dict[str, object]:
            calls.append(("stop_session", session_id))
            return {}

        async def list_changed_files(self, session_id: str) -> dict[str, object]:
            calls.append(("list_changed_files", session_id))
            cleanup_harvest_client_ids.append(id(self.httpx_client))
            return {"items": [{"path": "src/app.py"}]}

        async def list_workspace_files(self, session_id: str) -> dict[str, object]:
            calls.append(("list_workspace_files", session_id))
            return {"items": [{"path": "src/app.py", "type": "file"}]}

        async def get_workspace_file(self, session_id: str, path: str) -> bytes:
            calls.append(("get_workspace_file", path))
            return b"print('cancelled')\n"

        async def get_workspace_diff(self, session_id: str, path: str) -> bytes:
            calls.append(("get_workspace_diff", path))
            return b"diff --git a/src/app.py b/src/app.py\n"

        async def list_session_files(self, session_id: str) -> dict[str, object]:
            calls.append(("list_session_files", session_id))
            return {"items": []}

        async def delete_session(
            self,
            session_id: str,
            *,
            delete_branch: bool = False,
        ) -> dict[str, object]:
            calls.append(("delete_session", session_id))
            return {}

    async def cancel_immediately(_delay: float) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    monkeypatch.setattr("moonmind.omnigent.execute.asyncio.sleep", cancel_immediately)
    monkeypatch.setattr(
        "moonmind.omnigent.execute._heartbeat_state",
        lambda: {"omnigentSessionId": "session-1"},
    )

    with pytest.raises(asyncio.CancelledError):
        await run_omnigent_execution(
            AgentExecutionRequest(
                agentKind="external",
                agentId="omnigent",
                correlationId="corr-1",
                idempotencyKey="idem-1",
                parameters={
                    "omnigent": {
                        "endpointRef": "omnigent:endpoint:retry",
                        "agent": {"agentName": "codex-native-ui"},
                        "session": {"allowEmptyWorkspace": True},
                        "prompt": {"text": "Do the task"},
                        "capture": {"deleteOmnigentSessionAfterHarvest": True},
                    },
                },
            ),
            artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
        )

    assert ("interrupt", "session-1") in calls
    assert ("stop_session", "session-1") in calls
    assert ("list_changed_files", "session-1") in calls
    assert ("delete_session", "session-1") in calls
    assert len(httpx_clients) == 2
    assert cleanup_harvest_client_ids == [id(httpx_clients[1])]
    assert calls.index(("list_changed_files", "session-1")) < calls.index(
        ("delete_session", "session-1")
    )
    manifest_path = tmp_path / "corr-1" / "output.omnigent.capture_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == "moonmind.omnigent.capture_manifest.v1"
    assert manifest["terminalStatus"] == "canceled"
    assert manifest["patchUnavailable"] is False
    assert manifest["evidenceCompleteness"]["status"] == "complete"
    assert manifest["capturePolicy"]["limits"]["maxListEntries"] == 100
    assert manifest["capturePolicy"]["limits"]["maxContentBytes"] == 10 * 1024 * 1024
    assert manifest["capturePolicy"]["limits"]["maxPreviewBytes"] == 256 * 1024
    assert manifest["capturePolicy"]["binaryHandling"].startswith("metadata_")
    assert manifest["capturePolicy"]["timeoutSeconds"] == 30
    assert manifest["capturePolicy"]["retry"] == {"maxAttempts": 3}
    assert [group["groupKey"] for group in manifest["resourceGroups"]] == [
        "changed_files",
        "diffs",
        "workspace_files",
        "session_files",
        "snapshots",
        "logs_and_journals",
        "diagnostics",
        "manifests",
    ]
    external_state_path = tmp_path / "corr-1" / "checkpoint.omnigent.external_state.json"
    external_state = json.loads(external_state_path.read_text(encoding="utf-8"))
    assert external_state["endpointRef"] == "omnigent:endpoint:retry"
    assert external_state["retry"]["sessionResolution"] == "attached"
    assert external_state["retry"]["attached"] is True
    assert external_state["retry"]["attachSource"] == "activity_heartbeat"
    assert external_state["retry"]["firstMessageOutcome"] == "pending"
    assert external_state["firstMessage"]["idempotencyMarkerPresent"] is True
    assert external_state["firstMessage"]["posted"] is False


@pytest.mark.asyncio
async def test_run_omnigent_execution_dereferences_instruction_ref_when_prompt_text_is_absent(
    monkeypatch,
) -> None:
    posted_events: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            assert session_id == "session-1"
            posted_events.append(payload)
            return {}

        async def stream_events(self, session_id: str):
            assert session_id == "session-1"
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            assert session_id == "session-1"
            return {"status": "completed", "summary": "done"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"instructionRef": "artifact://instruction"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(
            readable_refs={"artifact://instruction": "Dereferenced instruction body"}
        ),
    )

    assert result.failure_class is None
    assert result.summary == "done"
    text = posted_events[0]["data"]["content"][0]["text"]
    assert "Dereferenced instruction body" in text
    assert "artifact://instruction" not in text


@pytest.mark.asyncio
async def test_run_omnigent_execution_preserves_inline_instruction_ref(
    monkeypatch,
) -> None:
    posted_events: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            posted_events.append(payload)
            return {}

        async def stream_events(self, session_id: str):
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "completed", "summary": "done"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            instructionRef="Implement the requested change",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(),
    )

    assert result.failure_class is None
    text = posted_events[0]["data"]["content"][0]["text"]
    assert "Implement the requested change" in text


@pytest.mark.asyncio
async def test_local_omnigent_artifact_gateway_rejects_traversal_refs(tmp_path) -> None:
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)

    with pytest.raises(OmnigentArtifactError, match="escapes artifact root"):
        await gateway.read_text("artifact://omnigent/../../secret.txt")

    ref = await gateway.write_bytes(
        request=_request(),
        name="../session.log",
        payload=b"evidence",
        link_type="output.omnigent.session_file",
    )

    assert ref == "artifact://omnigent/corr-1/segment/session.log"
    assert (tmp_path / "corr-1" / "segment" / "session.log").read_bytes() == b"evidence"
    assert not (tmp_path.parent / "session.log").exists()


@pytest.mark.asyncio
async def test_local_omnigent_artifact_gateway_wraps_os_errors(
    tmp_path, monkeypatch
) -> None:
    # §17: a filesystem persistence failure (disk full, permission, missing
    # directory) must surface as OmnigentArtifactError so the required
    # artifact-persistence handler classifies it instead of letting a raw
    # OSError escape the activity.
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(Path, "write_bytes", _boom)

    with pytest.raises(OmnigentArtifactError):
        await gateway.write_bytes(
            request=_request(),
            name="output.omnigent.snapshot.final.json",
            payload=b"evidence",
            link_type="output.omnigent.snapshot",
        )


@pytest.mark.asyncio
async def test_run_omnigent_execution_raises_when_stream_ends_still_running(
    monkeypatch,
) -> None:
    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            return {}

        async def stream_events(self, session_id: str):
            assert session_id == "session-1"
            if False:
                yield {}

        async def get_session(self, session_id: str) -> dict[str, object]:
            assert session_id == "session-1"
            return {"status": "running"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    with pytest.raises(OmnigentSessionStillRunningError):
        await run_omnigent_execution(
            AgentExecutionRequest(
                agentKind="external",
                agentId="omnigent",
                correlationId="corr-1",
                idempotencyKey="idem-1",
                parameters={
                    "omnigent": {
                        "agent": {"agentName": "codex-native-ui"},
                        "session": {"allowEmptyWorkspace": True},
                        "prompt": {"text": "Do the task"},
                    },
                },
            )
        )


@pytest.mark.asyncio
async def test_stream_end_polls_unfinished_current_turn_before_completion(
    monkeypatch,
) -> None:
    snapshots = [
        {
            "status": "idle",
            "active_response_id": None,
            "items": [{"id": "prior-item", "type": "function_call_output"}],
        },
        {
            "status": "completed",
            "active_response_id": None,
            "items": [
                {
                    "id": "current-call",
                    "type": "function_call",
                    "data": {"call_id": "current-call"},
                }
            ],
        },
    ]
    awaited: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **_: object) -> None:
            self.snapshot_index = 0

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            return {}

        async def stream_events(self, session_id: str):
            if False:
                yield {}

        async def get_session(self, session_id: str) -> dict[str, object]:
            snapshot = snapshots[min(self.snapshot_index, len(snapshots) - 1)]
            self.snapshot_index += 1
            return snapshot

    async def reject_unfinished_turn(**kwargs: object):
        awaited.update(kwargs)
        raise OmnigentSessionStillRunningError("unfinished current tool call")

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    monkeypatch.setattr(
        "moonmind.omnigent.execute._await_marked_turn_terminal",
        reject_unfinished_turn,
    )

    with pytest.raises(OmnigentSessionStillRunningError):
        await run_omnigent_execution(
            AgentExecutionRequest(
                agentKind="external",
                agentId="omnigent",
                correlationId="corr-1",
                idempotencyKey="idem-unfinished-current-turn",
                parameters={
                    "omnigent": {
                        "agent": {"agentName": "codex-native-ui"},
                        "session": {"allowEmptyWorkspace": True},
                        "prompt": {"text": "Do the task"},
                    },
                },
            )
        )

    assert awaited["baseline_item_ids"] == frozenset({"prior-item"})
    assert awaited["terminal_status"] == "completed"


@pytest.mark.asyncio
async def test_run_omnigent_execution_reuses_heartbeat_session_on_retry(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[str] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            calls.append("create_session")
            return {"id": "new-session"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            calls.append("post_event")
            return {}

        async def stream_events(self, session_id: str):
            assert session_id == "existing-session"
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "completed", "summary": "reattached"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    monkeypatch.setattr(
        "moonmind.omnigent.execute._heartbeat_state",
        lambda: {
            "omnigentSessionId": "existing-session",
            "firstMessagePosted": True,
        },
    )

    artifact_gateway = LocalOmnigentArtifactGateway(root=tmp_path)
    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "continue"},
                },
            },
        ),
        artifact_gateway=artifact_gateway,
    )

    external_state = json.loads(
        await artifact_gateway.read_text(result.metadata["externalStateRef"])
    )
    assert result.summary == "reattached"
    assert calls == []
    assert external_state["firstMessage"]["state"] == "posted"
    assert "firstMessageResponseRef" not in external_state["artifactRefs"]


@pytest.mark.asyncio
async def test_run_omnigent_execution_reconciles_idle_snapshot_before_retry_stream(
    monkeypatch,
    tmp_path,
) -> None:
    awaited: dict[str, object] = {}
    terminal_snapshot = {
        "status": "idle",
        "active_response_id": None,
        "summary": "completed before retry attached",
        "items": [
            {
                "id": "current-call",
                "type": "function_call",
                "data": {"call_id": "current-call"},
            },
            {
                "id": "current-output",
                "type": "function_call_output",
                "data": {"call_id": "current-call"},
            },
            {
                "id": "current-assistant",
                "type": "message",
                "data": {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            },
        ],
    }

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            raise AssertionError("retry must reuse the heartbeat session")

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            raise AssertionError("retry must not repost the first message")

        async def stream_events(self, session_id: str):
            raise AssertionError(
                "an already-terminal retry must not wait on a heartbeat-only stream"
            )
            if False:
                yield {}

        async def get_session(self, session_id: str) -> dict[str, object]:
            assert session_id == "existing-session"
            return terminal_snapshot

    async def capture_terminal_wait(**kwargs: object):
        awaited.update(kwargs)
        return "completed", terminal_snapshot

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    monkeypatch.setattr(
        "moonmind.omnigent.execute._heartbeat_state",
        lambda: {
            "omnigentSessionId": "existing-session",
            "firstMessagePosted": True,
            "preDispatchItemIds": ["prior-item"],
        },
    )
    monkeypatch.setattr(
        "moonmind.omnigent.execute._await_marked_turn_terminal",
        capture_terminal_wait,
    )

    artifact_gateway = LocalOmnigentArtifactGateway(root=tmp_path)
    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-missed-terminal-edge",
            idempotencyKey="idem-missed-terminal-edge",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "continue"},
                },
            },
        ),
        artifact_gateway=artifact_gateway,
    )

    external_state = json.loads(
        await artifact_gateway.read_text(result.metadata["externalStateRef"])
    )
    assert result.summary == "completed before retry attached"
    assert awaited["baseline_item_ids"] == frozenset({"prior-item"})
    assert external_state["terminalReconciliation"] == {
        "source": "reattached_inactive_snapshot",
        "status": "completed",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("heartbeat_type", "reconciliation_source"),
    [
        ("session.heartbeat", "session_heartbeat_snapshot"),
        ("response.heartbeat", "response_heartbeat_snapshot"),
    ],
)
async def test_run_omnigent_execution_reconciles_idle_snapshot_from_heartbeat(
    monkeypatch,
    tmp_path,
    heartbeat_type: str,
    reconciliation_source: str,
) -> None:
    awaited: dict[str, object] = {}
    terminal_snapshot = {
        "status": "idle",
        "active_response_id": None,
        "summary": "completed without terminal event",
        "items": [
            {
                "id": "current-call",
                "type": "function_call",
                "data": {"call_id": "current-call"},
            },
            {
                "id": "current-output",
                "type": "function_call_output",
                "data": {"call_id": "current-call"},
            },
        ],
    }

    class FakeClient:
        def __init__(self, **_: object) -> None:
            self.posted = asyncio.Event()

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            self.posted.set()
            return {"pending_id": "pending-1"}

        async def stream_events(self, session_id: str):
            await self.posted.wait()
            yield {"type": heartbeat_type, "status": "running"}
            raise AssertionError(
                "heartbeat reconciliation must finish without another SSE edge"
            )

        async def get_session(self, session_id: str) -> dict[str, object]:
            assert session_id == "session-1"
            if not self.posted.is_set():
                return {
                    "status": "idle",
                    "active_response_id": None,
                    "items": [{"id": "prior-item", "type": "message"}],
                }
            return terminal_snapshot

    async def capture_terminal_wait(**kwargs: object):
        awaited.update(kwargs)
        return "completed", terminal_snapshot

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    monkeypatch.setattr(
        "moonmind.omnigent.execute._await_marked_turn_terminal",
        capture_terminal_wait,
    )

    artifact_gateway = LocalOmnigentArtifactGateway(root=tmp_path)
    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-heartbeat-terminal-reconcile",
            idempotencyKey="idem-heartbeat-terminal-reconcile",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "do the work"},
                },
            },
        ),
        artifact_gateway=artifact_gateway,
    )

    external_state = json.loads(
        await artifact_gateway.read_text(result.metadata["externalStateRef"])
    )
    assert result.summary == "completed without terminal event"
    assert awaited["baseline_item_ids"] == frozenset({"prior-item"})
    assert external_state["terminalReconciliation"] == {
        "source": reconciliation_source,
        "status": "completed",
    }
    normalized_path = (
        tmp_path
        / "corr-heartbeat-terminal-reconcile"
        / "runtime.omnigent.sse.normalized.jsonl"
    )
    normalized_events = [
        json.loads(line)
        for line in normalized_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    reconciled = normalized_events[-1]
    assert reconciled["metadata"]["moonmind"] == {
        "source": "omnigent_terminal_reconciliation",
        "terminalReconciliationSource": reconciliation_source,
        "workflowChatVisible": True,
    }
    marker = (
        "MoonMind-Omnigent-Run:\n"
        "  correlationId: corr-heartbeat-terminal-reconcile\n"
        "  idempotencyKey: idem-heartbeat-terminal-reconcile"
    )
    marker_digest = hashlib.sha256(f"session-1\n{marker}".encode()).hexdigest()
    assert reconciled["deduplicationKey"] == (
        f"terminal-reconciliation:{marker_digest}"
    )


@pytest.mark.asyncio
async def test_run_omnigent_execution_preserves_durable_failed_terminal_on_retry(
    monkeypatch,
    tmp_path,
) -> None:
    terminal_calls: list[str] = []

    class Row:
        bridge_session_id = ""
        omnigent_session_id = "existing-session"
        status = "failed"
        first_message_state = "terminal"
        first_message_posted_at = object()
        first_message_pending_id = "pending-1"
        first_message_item_id = "item-1"
        terminal_refs = {
            "summary": "durable provider failure",
            "failureClass": "execution_error",
        }
        metadata_: dict[str, object] = {}

    row = Row()

    class Store:
        async def get_binding(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def get_or_create(self, **_kwargs: object) -> Row:
            return row

        async def mark_prepared(self, *_args: object, **_kwargs: object) -> Row:
            return row

        async def mark_terminal(
            self, *_args: object, status: str, **_kwargs: object
        ) -> Row:
            terminal_calls.append(status)
            return row

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            raise AssertionError("retry must reuse the durable session")

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            raise AssertionError("retry must not repost a terminal turn")

        async def stream_events(self, session_id: str):
            raise AssertionError("retry must not stream after a durable terminal")
            if False:
                yield {}

        async def get_session(self, session_id: str) -> dict[str, object]:
            assert session_id == "existing-session"
            return {"status": "idle", "summary": "provider has returned to idle"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-durable-terminal",
            idempotencyKey="idem-durable-terminal",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                }
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
        run_store=Store(),
        first_message_text="continue",
    )

    assert result.metadata["normalizedStatus"] == "failed"
    assert result.failure_class == "execution_error"
    assert result.summary == "durable provider failure"
    assert terminal_calls == ["failed"]


@pytest.mark.asyncio
async def test_run_omnigent_execution_continues_existing_session_with_new_message(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            self.stream_started = False

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            raise AssertionError("same-session continuation must not create a session")

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            assert self.stream_started is True
            text = str(payload["data"]["content"][0]["text"])
            calls.append((session_id, text))
            return {"pending_id": "pending-continuation-1"}

        async def stream_events(self, session_id: str):
            assert session_id == "existing-session"
            self.stream_started = True
            yield {"type": "turn.started"}
            yield {"type": "turn.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            assert session_id == "existing-session"
            return {"status": "completed", "summary": "continuation complete"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-continuation-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                }
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
        resume_session_id="existing-session",
        first_message_text="Continue the current task.",
        defer_bridge_terminal=True,
    )

    assert calls and calls[0][0] == "existing-session"
    assert calls[0][1].startswith("Continue the current task.")
    assert "idem-continuation-1" in calls[0][1]
    assert result.summary == "continuation complete"
    assert result.metadata["deferredBridgeTerminal"]["status"] == "completed"
    assert result.metadata["deferredBridgeTerminal"]["idempotencyKey"] == (
        "idem-continuation-1"
    )


@pytest.mark.asyncio
async def test_run_omnigent_execution_reuses_persisted_session_on_retry(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[str] = []
    recorded_sessions: list[dict[str, object]] = []

    class Row:
        omnigent_session_id = "persisted-session"
        first_message_state = "posted"
        first_message_pending_id = "pending-1"
        first_message_item_id = "item-1"

    class Store:
        async def get_or_create(self, **_: object) -> Row:
            return Row()

        async def get_binding(self, *_a: object, **_k: object) -> None:
            return None

        async def mark_prepared(self, *_: object, **__: object) -> Row:
            return Row()

        async def mark_terminal(self, *_: object, **__: object) -> Row:
            return Row()

        async def record_session_created(
            self, idempotency_key: str, **kwargs: object
        ) -> Row:
            recorded_sessions.append({"idempotency_key": idempotency_key, **kwargs})
            return Row()

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            calls.append("create_session")
            return {"id": "new-session"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            calls.append("post_event")
            return {}

        async def stream_events(self, session_id: str):
            assert session_id == "persisted-session"
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "completed", "summary": "durably reattached"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    artifact_gateway = LocalOmnigentArtifactGateway(root=tmp_path)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "continue"},
                },
            },
        ),
        run_store=Store(),
        artifact_gateway=artifact_gateway,
    )

    assert result.summary == "durably reattached"
    assert calls == []
    assert recorded_sessions == [
        {
            "idempotency_key": "idem-1",
            "session_id": "persisted-session",
            "agent_id": "agent-1",
            "endpoint_ref": "default",
            "capabilities": None,
            "session_status": "completed",
        }
    ]
    assert result.metadata["checkpointKind"] == "external_state_ref"
    assert result.metadata["stateCheckpointRef"] == result.metadata["externalStateRef"]
    assert "workspaceRootRef" not in result.metadata
    assert result.metadata["externalStateRef"].startswith("artifact://omnigent/")
    external_state = json.loads(
        await artifact_gateway.read_text(result.metadata["externalStateRef"])
    )
    assert external_state["retry"]["sessionResolution"] == "attached"
    assert external_state["retry"]["attached"] is True
    assert external_state["retry"]["attachSource"] == "bridge_session_store"
    assert external_state["retry"]["firstMessageOutcome"] == "already_posted"
    assert external_state["firstMessage"]["state"] == "posted"
    assert external_state["firstMessage"]["posted"] is True
    assert external_state["firstMessage"]["responseIdentifiers"] == {
        "pendingId": "pending-1",
        "itemId": "item-1",
    }
    assert external_state["artifactRefs"]["diagnosticsRef"] == result.diagnostics_ref


@pytest.mark.asyncio
async def test_run_omnigent_execution_reuses_persisted_item_frontier_on_retry(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[str] = []
    awaited: dict[str, object] = {}
    terminal_snapshot = {
        "status": "idle",
        "active_response_id": None,
        "summary": "durably reattached after marker eviction",
        "items": [
            {
                "id": "current-call",
                "type": "function_call",
                "data": {"call_id": "current-call"},
            },
            {
                "id": "current-output",
                "type": "function_call_output",
                "data": {"call_id": "current-call"},
            },
            {
                "id": "current-assistant",
                "type": "message",
                "data": {
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            },
        ],
    }

    class Row:
        omnigent_session_id = "persisted-session"
        first_message_state = "posted"
        first_message_pending_id = "pending-1"
        first_message_item_id = "marked-item"
        metadata_ = {FIRST_MESSAGE_ITEM_FRONTIER_KEY: ["prior-item"]}

    class Store:
        async def get_or_create(self, **_: object) -> Row:
            return Row()

        async def get_binding(self, *_a: object, **_k: object) -> None:
            return None

        async def mark_prepared(self, *_: object, **__: object) -> Row:
            return Row()

        async def mark_terminal(self, *_: object, **__: object) -> Row:
            return Row()

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            calls.append("create_session")
            return {"id": "new-session"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            calls.append("post_event")
            return {}

        async def stream_events(self, session_id: str):
            assert session_id == "persisted-session"
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            assert session_id == "persisted-session"
            return terminal_snapshot

    async def capture_terminal_wait(**kwargs: object):
        awaited.update(kwargs)
        return "completed", terminal_snapshot

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    monkeypatch.setattr(
        "moonmind.omnigent.execute._await_marked_turn_terminal",
        capture_terminal_wait,
    )
    artifact_gateway = LocalOmnigentArtifactGateway(root=tmp_path)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-persisted-frontier",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "continue"},
                },
            },
        ),
        run_store=Store(),
        artifact_gateway=artifact_gateway,
    )

    assert result.summary == "durably reattached after marker eviction"
    assert calls == []
    assert awaited["baseline_item_ids"] == frozenset({"prior-item"})


@pytest.mark.asyncio
async def test_run_omnigent_execution_reports_disabled_idempotency_marker(
    monkeypatch,
    tmp_path,
) -> None:
    posted_events: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            posted_events.append(payload)
            return {}

        async def stream_events(self, session_id: str):
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "completed", "summary": "done"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    artifact_gateway = LocalOmnigentArtifactGateway(root=tmp_path)
    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {
                        "text": "continue",
                        "includeIdempotencyMarker": False,
                    },
                },
            },
        ),
        artifact_gateway=artifact_gateway,
    )

    assert result.failure_class is None
    assert result.metadata["stateCheckpointRef"] == result.metadata["externalStateRef"]
    text = posted_events[0]["data"]["content"][0]["text"]
    assert "moonmind:first-message" not in text
    external_state = json.loads(
        await artifact_gateway.read_text(result.metadata["externalStateRef"])
    )
    assert external_state["firstMessage"]["idempotencyMarkerPresent"] is False


@pytest.mark.asyncio
async def test_run_omnigent_execution_reconciles_posting_state_without_duplicate_prompt(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[str] = []
    marker: dict[str, str] = {}

    class Row:
        omnigent_session_id = "persisted-session"
        first_message_state = "posting"

    class PostedRow:
        omnigent_session_id = "persisted-session"
        first_message_state = "posted"

    class Store:
        async def get_or_create(self, **_: object) -> Row:
            return Row()

        async def get_binding(self, *_a: object, **_k: object) -> None:
            return None

        async def mark_prepared(self, *_: object, **__: object) -> Row:
            marker["value"] = str(__["marker"])
            return Row()

        async def mark_posted(self, *_: object, **__: object) -> PostedRow:
            calls.append("mark_posted")
            return PostedRow()

        async def mark_terminal(self, *_: object, **__: object) -> PostedRow:
            return PostedRow()

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            calls.append("create_session")
            return {"id": "new-session"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            calls.append("post_event")
            return {}

        async def stream_events(self, session_id: str):
            assert session_id == "persisted-session"
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            if session_id == "persisted-session":
                return {
                    "status": "completed",
                    "summary": "reconciled",
                    "events": [{"text": marker.get("value", "")}],
                }
            return {"status": "completed", "summary": "child"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    artifact_gateway = LocalOmnigentArtifactGateway(root=tmp_path)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "continue"},
                },
            },
        ),
        run_store=Store(),
        artifact_gateway=artifact_gateway,
    )

    assert result.summary == "reconciled"
    assert "mark_posted" in calls
    assert "create_session" not in calls
    assert "post_event" not in calls
    external_state = json.loads(
        await artifact_gateway.read_text(result.metadata["externalStateRef"])
    )
    assert external_state["retry"]["sessionResolution"] == "attached"
    assert external_state["retry"]["firstMessageOutcome"] == "reconciled"
    assert external_state["retry"]["reconciliationChecked"] is True
    assert external_state["retry"]["markerFound"] is True


@pytest.mark.asyncio
async def test_run_omnigent_execution_fails_closed_when_posting_state_cannot_reconcile(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[str] = []

    class Row:
        omnigent_session_id = "persisted-session"
        first_message_state = "posting"

    class Store:
        async def get_or_create(self, **_: object) -> Row:
            return Row()

        async def get_binding(self, *_a: object, **_k: object) -> None:
            return None

        async def mark_prepared(self, *_: object, **__: object) -> Row:
            return Row()

        async def mark_posted(self, *_: object, **__: object) -> Row:
            calls.append("mark_posted")
            return Row()

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            calls.append("post_event")
            return {}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "running", "summary": "no marker present"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    artifact_gateway = LocalOmnigentArtifactGateway(root=tmp_path)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "continue"},
                },
            },
        ),
        run_store=Store(),
        artifact_gateway=artifact_gateway,
    )

    # §17: ambiguous `posting` reconciliation fails closed as integration_error.
    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "omnigent_first_message_reconcile_failed"
    assert result.diagnostics_ref.startswith("artifact://omnigent/")
    assert "post_event" not in calls
    assert "mark_posted" not in calls
    external_state = json.loads(
        await artifact_gateway.read_text(result.metadata["externalStateRef"])
    )
    assert external_state["retry"]["sessionResolution"] == "attached"
    assert external_state["retry"]["firstMessageOutcome"] == "unrecoverable_mismatch"
    assert external_state["retry"]["mismatchReason"] == "reconcile_failed"
    assert external_state["retry"]["markerFound"] is False
    assert external_state["artifactRefs"]["diagnosticsRef"] == result.diagnostics_ref


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "durable_state",
    [
        "not_prepared",
        "prepared",
        "posting",
        "response_before_posted_persistence",
        "posted",
        "terminal",
    ],
)
async def test_run_omnigent_execution_digest_mismatch_is_non_retryable_with_diagnostics(
    monkeypatch,
    tmp_path,
    durable_state: str,
) -> None:
    class Row:
        omnigent_session_id = "persisted-session"
        first_message_state = (
            "posting"
            if durable_state == "response_before_posted_persistence"
            else durable_state
        )

    class Store:
        async def get_or_create(self, **_: object) -> Row:
            return Row()

        async def get_binding(self, *_a: object, **_k: object) -> None:
            return None

        async def mark_prepared(self, *_: object, **__: object) -> Row:
            raise OmnigentDigestMismatchError("digest changed")

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            raise AssertionError("digest mismatch must not post")

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "running", "summary": "existing session"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    artifact_gateway = LocalOmnigentArtifactGateway(root=tmp_path)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "changed prompt"},
                },
            },
        ),
        run_store=Store(),
        artifact_gateway=artifact_gateway,
    )

    # §17: first-message digest mismatch is a conflicting replay under the same
    # idempotency key and maps to user_error, not execution_error.
    assert result.failure_class == "user_error"
    assert result.provider_error_code == "omnigent_first_message_digest_mismatch"
    assert result.diagnostics_ref.startswith("artifact://omnigent/")
    external_state = json.loads(
        await artifact_gateway.read_text(result.metadata["externalStateRef"])
    )
    assert external_state["retry"]["firstMessageOutcome"] == "unrecoverable_mismatch"
    assert external_state["retry"]["mismatchReason"] == "digest_mismatch"
    assert external_state["artifactRefs"]["diagnosticsRef"] == result.diagnostics_ref


@pytest.mark.asyncio
async def test_run_omnigent_execution_harvests_changed_and_session_files(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            return {"pending_id": "pending-1"}

        async def stream_events(self, session_id: str):
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "status": "completed",
                "summary": "done",
                "githubPrUrl": "https://github.example/org/repo/pull/1",
            }

        async def list_changed_files(self, session_id: str) -> dict[str, object]:
            return {"items": [{"path": "src/app.py"}]}

        async def get_workspace_file(self, session_id: str, path: str) -> bytes:
            return {
                "README.md": b"# Project\n",
                "src/app.py": b"print('changed')\n",
            }[path]

        async def list_workspace_files(self, session_id: str) -> dict[str, object]:
            return {
                "items": [
                    {"path": "README.md", "type": "file"},
                    {"path": "src", "type": "directory"},
                ]
            }

        async def get_workspace_diff(self, session_id: str, path: str) -> bytes:
            assert path == "src/app.py"
            return b"diff --git a/src/app.py b/src/app.py\n"

        async def list_session_files(self, session_id: str) -> dict[str, object]:
            return {"items": [{"id": "file-1", "filename": "session.log"}]}

        async def get_session_file_content(self, session_id: str, file_id: str) -> bytes:
            assert file_id == "file-1"
            return b"session evidence\n"

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    assert result.failure_class is None
    assert result.metadata["changedFilesIndexRef"].startswith("artifact://omnigent/")
    assert result.metadata["workspaceFilesIndexRef"].startswith("artifact://omnigent/")
    assert result.metadata["sessionFilesIndexRef"].startswith("artifact://omnigent/")
    assert result.metadata["githubPrUrl"] == "https://github.example/org/repo/pull/1"
    manifest_path = tmp_path / "corr-1" / "output.omnigent.capture_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["workspaceFiles"][0]["path"] == "README.md"
    assert manifest["workspaceFiles"][0]["contentType"] == "text/markdown"
    assert manifest["workspaceFiles"][0]["sizeBytes"] == len(b"# Project\n")
    assert manifest["workspaceDiffs"][0]["path"] == "src/app.py"
    assert manifest["patchUnavailable"] is False
    manifest_resources = next(
        group["items"] for group in manifest["resourceGroups"]
        if group["groupKey"] == "manifests"
    )
    assert {resource["label"] for resource in manifest_resources} >= {
        "Changed-file index", "Workspace-file index", "Session-file index"
    }
    external_state_path = tmp_path / "corr-1" / "checkpoint.omnigent.external_state.json"
    external_state = json.loads(external_state_path.read_text(encoding="utf-8"))
    assert external_state["patchEvidence"] == {
        "diffRefs": [
            {
                "path": "src/app.py",
                "artifactRef": manifest["workspaceDiffs"][0]["artifactRef"],
            }
        ],
        "patchUnavailable": False,
    }


@pytest.mark.asyncio
async def test_run_omnigent_execution_honors_workspace_files_capture_opt_out(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            return {"pending_id": "pending-1"}

        async def stream_events(self, session_id: str):
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "completed", "summary": "done"}

        async def list_changed_files(self, session_id: str) -> dict[str, object]:
            return {"items": [{"path": "src/app.py"}]}

        async def get_workspace_file(self, session_id: str, path: str) -> bytes:
            assert path == "src/app.py"
            return b"print('changed')\n"

        async def list_workspace_files(self, session_id: str) -> dict[str, object]:
            raise AssertionError("workspaceFiles=false must skip workspace file harvest")

        async def get_workspace_diff(self, session_id: str, path: str) -> bytes:
            assert path == "src/app.py"
            return b"diff --git a/src/app.py b/src/app.py\n"

        async def list_session_files(self, session_id: str) -> dict[str, object]:
            return {"items": []}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                    "capture": {"workspaceFiles": False},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    assert result.failure_class is None
    assert "workspaceFilesIndexRef" not in result.metadata
    manifest_path = tmp_path / "corr-1" / "output.omnigent.capture_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "workspaceFilesIndexRef" not in manifest
    assert "workspaceFiles" not in manifest
    assert manifest["changedFiles"][0]["path"] == "src/app.py"
    assert manifest["workspaceDiffs"][0]["path"] == "src/app.py"


@pytest.mark.asyncio
async def test_run_omnigent_execution_caps_resource_harvest(
    monkeypatch,
    tmp_path,
) -> None:
    changed_fetches: list[str] = []
    session_fetches: list[str] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            return {"pending_id": "pending-1"}

        async def stream_events(self, session_id: str):
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "completed", "summary": "done"}

        async def list_changed_files(self, session_id: str) -> dict[str, object]:
            return {"items": [{"path": f"src/file_{index}.py"} for index in range(101)]}

        async def get_workspace_file(self, session_id: str, path: str) -> bytes:
            changed_fetches.append(path)
            return b"changed\n"

        async def list_session_files(self, session_id: str) -> dict[str, object]:
            return {
                "items": [
                    {"id": f"file-{index}", "filename": f"session-{index}.log"}
                    for index in range(101)
                ]
            }

        async def get_session_file_content(self, session_id: str, file_id: str) -> bytes:
            session_fetches.append(file_id)
            return b"session evidence\n"

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    assert result.failure_class is None
    assert len(changed_fetches) == 100
    assert changed_fetches[-1] == "src/file_99.py"
    assert len(session_fetches) == 100
    assert session_fetches[-1] == "file-99"


@pytest.mark.asyncio
async def test_run_omnigent_execution_records_missing_resource_harvest_and_child_evidence(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            return {"pending_id": "pending-1"}

        async def stream_events(self, session_id: str):
            yield {
                "type": "session.child.created",
                "data": {"childSessionId": "child-1"},
            }
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            if session_id == "child-1":
                return {"status": "completed", "summary": "child done"}
            return {"status": "completed", "summary": "done"}

        async def list_changed_files(self, session_id: str) -> dict[str, object]:
            raise RuntimeError("diff endpoint missing")

        async def list_session_files(self, session_id: str) -> dict[str, object]:
            raise RuntimeError("session file endpoint missing")

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    assert result.failure_class is None
    assert result.metadata["childSessionsRef"].startswith("artifact://omnigent/")
    manifest_path = tmp_path / "corr-1" / "output.omnigent.capture_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["patchUnavailable"] is True
    assert manifest["childSessions"] == 1
    assert manifest["childSessionEvidence"][0]["childSessionId"] == "child-1"
    assert "changedFilesUnavailable" in manifest
    assert "workspaceFilesUnavailable" in manifest
    assert "sessionFilesUnavailable" in manifest
    # §17: optional resource-harvest failure resolves to completed-with-diagnostics
    # (no failure class) when policy does not require full evidence.
    assert manifest["optionalResourceHarvest"]["outcome"] == "completed_with_diagnostics"
    assert manifest["optionalResourceHarvest"]["failureClass"] is None


class _HarvestFailureClient:
    """Fake client that completes a session but fails every resource harvest."""

    def __init__(self, **_: object) -> None:
        pass

    async def list_agents(self) -> dict[str, object]:
        return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

    async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
        return {"id": "session-1"}

    async def post_event(
        self,
        session_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        return {"pending_id": "pending-1"}

    async def stream_events(self, session_id: str):
        yield {"type": "response.completed"}

    async def get_session(self, session_id: str) -> dict[str, object]:
        return {"status": "completed", "summary": "done"}

    async def list_changed_files(self, session_id: str) -> dict[str, object]:
        raise RuntimeError("changed-file endpoint missing")

    async def list_workspace_files(self, session_id: str) -> dict[str, object]:
        raise RuntimeError("workspace-file endpoint missing")

    async def list_session_files(self, session_id: str) -> dict[str, object]:
        raise RuntimeError("session-file endpoint missing")


@pytest.mark.asyncio
async def test_run_omnigent_execution_escalates_harvest_failure_when_full_evidence(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr(
        "moonmind.omnigent.execute.OmnigentHttpClient", _HarvestFailureClient
    )

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                    "capture": {"requireFullEvidence": True},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    # §17: required full evidence turns an optional harvest failure into a
    # system_error rather than completed-with-diagnostics.
    assert result.failure_class == "system_error"
    assert result.provider_error_code == "omnigent_required_resource_evidence_missing"
    # §17: the escalated failure result must not inherit the provider's
    # success snapshot summary ("done") and must describe the missing evidence.
    assert result.summary != "done"
    assert "evidence" in result.summary.lower()
    manifest_path = tmp_path / "corr-1" / "output.omnigent.capture_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["optionalResourceHarvest"]["outcome"] == "required_evidence_missing"
    assert manifest["optionalResourceHarvest"]["failureClass"] == "system_error"


@pytest.mark.asyncio
async def test_run_omnigent_execution_harvest_failure_completes_without_policy(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr(
        "moonmind.omnigent.execute.OmnigentHttpClient", _HarvestFailureClient
    )

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    # §17: without a full-evidence policy an optional harvest failure stays a
    # completed run (no failure class).
    assert result.failure_class is None
    assert result.provider_error_code is None


@pytest.mark.asyncio
async def test_run_omnigent_execution_required_artifact_persistence_is_system_error(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "running"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    # Unresolvable MoonMind artifact ref -> artifact-authority read
                    # failure raised as OmnigentArtifactError.
                    "prompt": {"instructionRef": "artifact://omnigent/missing-ref"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    # §17: required artifact-persistence/authority failure -> system_error.
    assert result.failure_class == "system_error"
    assert result.provider_error_code == "omnigent_artifact_persistence_failed"


@pytest.mark.asyncio
async def test_run_omnigent_execution_invalid_session_payload_is_user_error(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "session": {
                        "hostType": "managed",
                        "hostId": "host-1",
                        "workspace": "https://github.com/o/r",
                    },
                    "prompt": {"text": "Do the task"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    # §17: invalid session-create payload (managed hostId) -> user_error.
    assert result.failure_class == "user_error"
    assert result.provider_error_code == "omnigent_invalid_session_payload"


@pytest.mark.asyncio
async def test_snapshot_derived_terminal_status_is_indexed(
    monkeypatch,
    tmp_path,
) -> None:
    """A stream that ends non-terminal but a terminal final snapshot must still
    record a terminal event in the durable event index (OmnigentBridge §7.2)."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base
    from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            return {"pending_id": "pending-1"}

        async def stream_events(self, session_id: str):
            # Stream ends without ever emitting a terminal normalized status.
            yield {"session": {"status": "running"}}
            yield {"session": {"status": "running"}}

        async def get_session(self, session_id: str) -> dict[str, object]:
            # The provider's final snapshot is terminal.
            return {"status": "completed", "summary": "done"}

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = OmnigentBridgeSessionStore(session_maker)

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    try:
        result = await run_omnigent_execution(
            AgentExecutionRequest(
                agentKind="external",
                agentId="omnigent",
                correlationId="corr-1",
                idempotencyKey="idem-1",
                parameters={
                    "omnigent": {
                        "agent": {"agentName": "codex-native-ui"},
                        "session": {"allowEmptyWorkspace": True},
                        "prompt": {"text": "Do the task"},
                    },
                },
            ),
            run_store=store,
            artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
        )
        assert result.metadata["normalizedStatus"] == "completed"

        from sqlalchemy import select

        from api_service.db.models import OmnigentBridgeSession

        async with session_maker() as session:
            row = (
                await session.execute(
                    select(OmnigentBridgeSession).where(
                        OmnigentBridgeSession.idempotency_key == "idem-1"
                    )
                )
            ).scalar_one()
            bridge_session_id = row.bridge_session_id

        events = await store.list_events(bridge_session_id)
    finally:
        await engine.dispose()

    # The event index must contain a terminal completion event even though the
    # stream itself never emitted one, and sequences must stay unique/monotonic.
    sequences = [e.sequence for e in events]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert events[-1].normalized_status == "completed"
    assert events[-1].event_type == "session.final_snapshot"


@pytest.mark.asyncio
async def test_run_omnigent_execution_rejects_cross_owner_bridge_session(
    monkeypatch,
) -> None:
    """§16 rule 1: authorize the bridge session before any provider call."""

    from moonmind.omnigent.bridge_security import BridgeSessionBinding

    class Store:
        async def get_binding(self, *_a: object, **_k: object) -> BridgeSessionBinding:
            return BridgeSessionBinding(
                workflow_id="other-workflow", agent_run_id="other-run"
            )

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            raise AssertionError("provider call must not happen before authorization")

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        ),
        run_store=Store(),
    )

    assert result.failure_class == "user_error"
    assert result.provider_error_code == "omnigent_authorization_denied"
    assert result.metadata["authorizationDenied"] is True


@pytest.mark.asyncio
async def test_run_omnigent_execution_rechecks_owner_after_get_or_create(
    monkeypatch,
) -> None:
    """§16 rule 1: reject a concurrently created cross-owner durable row."""

    class Row:
        moonmind_workflow_id = "other-workflow"
        moonmind_agent_run_id = "other-run"

    class Store:
        async def get_binding(self, *_a: object, **_k: object) -> None:
            return None

        async def get_or_create(self, *_a: object, **_k: object) -> Row:
            return Row()

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, *_a: object, **_k: object) -> dict[str, object]:
            raise AssertionError("session attach/create must not use cross-owner row")

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        ),
        run_store=Store(),
    )

    assert result.failure_class == "user_error"
    assert result.provider_error_code == "omnigent_authorization_denied"
    assert result.metadata["authorizationDenied"] is True


@pytest.mark.asyncio
async def test_run_omnigent_execution_redacts_raw_events_before_persistence(
    monkeypatch,
    tmp_path,
) -> None:
    """§16 rule 5: raw provider events are redacted before artifact write."""

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            return {}

        async def stream_events(self, session_id: str):
            yield {"type": "host.capabilities", "api_token": "sk-should-not-persist"}
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "completed", "summary": "done"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    assert result.failure_class is None
    raw_path = tmp_path / "corr-1" / "runtime.omnigent.sse.raw.jsonl"
    raw_text = raw_path.read_text(encoding="utf-8")
    assert "sk-should-not-persist" not in raw_text
    assert "[REDACTED]" in raw_text
    normalized_path = tmp_path / "corr-1" / "runtime.omnigent.sse.normalized.jsonl"
    normalized_events = [
        json.loads(line)
        for line in normalized_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert normalized_events[0]["schemaVersion"] == "moonmind.omnigent_bridge.event.v1"
    assert normalized_events[0]["sequence"] == 1
    assert normalized_events[0]["type"] == "host.capabilities"
    assert normalized_events[0]["normalizedStatus"] == "running"
    assert normalized_events[0]["data"] == {}
    assert normalized_events[0]["metadata"]["moonmind"] == {
        "workflowChatVisible": False,
        "source": "omnigent_stream",
    }
    assert any(
        event["type"] == "response.completed"
        and event["normalizedStatus"] == "completed"
        for event in normalized_events
    )
    # This fake emits completion before post_event returns, so the queue marks
    # it as pre-post replay. The corroborating terminal snapshot is therefore
    # appended as the authoritative terminal index entry.
    assert normalized_events[-1]["type"] == "session.final_snapshot"
    assert normalized_events[-1]["normalizedStatus"] == "completed"

    # Scan the actual durable artifact bodies emitted by the execution, rather
    # than reconstructed projections. This includes the raw and normalized
    # journals, diagnostics, manifest, snapshots, and external-state evidence.
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(tmp_path.rglob("*"))
        if path.is_file()
    )
    assert "sk-should-not-persist" not in persisted
    assert "[REDACTED]" in persisted
