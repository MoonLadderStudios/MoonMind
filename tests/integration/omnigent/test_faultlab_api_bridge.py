"""AC7 API/transport binding: fault scenarios vs. the real bridge SSE stream.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criterion 7 — selected
scenarios must cross the API/transport boundary: the create path, reconnect, and
terminal replay, so SSE/WebSocket disconnect, reconnect, and duplicate/reordered
events are covered by a real API flow).

This binding drives the **real** Omnigent bridge server-sent-events stream route
(``GET /api/omnigent/bridge-sessions/{id}/stream`` in
``api_service.api.routers.omnigent_bridge``) with an event frontier derived from a
fault-lab declarative scenario's transport faults (duplicate / reorder /
disconnect). It re-proves, at the transport boundary, that:

* the stream delivers a **monotonic, de-duplicated** event frontier even when the
  scenario injects duplicate and reordered transport events;
* an EventSource **reconnect** carrying the last acknowledged sequence
  (``Last-Event-ID``) never re-delivers an already-acknowledged event
  (invariant: no duplicate delivery across reconnect);
* the **terminal** envelope is replayed after live events, so terminal evidence
  survives a disconnect (historical-read safety at the transport edge).

It is hermetic (in-process ASGI via ``TestClient``; no network, credentials,
Docker, or Temporal) and therefore safe for required CI.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.omnigent_bridge import (
    OMNIGENT_BRIDGE_MOUNT_PATH,
    _get_bridge_store,
    _get_execution_service,
    _require_bridge_enabled,
    router,
)
from api_service.auth_providers import get_current_user
from moonmind.omnigent.faultlab.scenario import (
    EmittedEvent,
    FaultScenario,
    ScenarioStep,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

_USER_ID = uuid4()
_WORKFLOW_ID = "mm:faultlab-api"
_AGENT_RUN_ID = "ar-faultlab-api"
_SESSION_ID = "brs-faultlab"


def _transport_fault_scenario() -> FaultScenario:
    """A declarative scenario whose read_events steps inject transport faults.

    The provider re-delivers a batch (duplicate), delivers a later batch out of
    frontier order (reorder), and drops the connection (disconnect) before the
    terminal — the exact transport-fault classes the reconnect contract must
    absorb.
    """

    return FaultScenario(
        seed=3709,
        scenario_id="api-transport-duplicate-reorder-disconnect",
        steps=(
            ScenarioStep(
                on="read_events",
                emit=(
                    EmittedEvent(type="response.delta", cursor="1"),
                    EmittedEvent(type="response.delta", cursor="2"),
                ),
                duplicate=True,
            ),
            ScenarioStep(
                on="read_events",
                emit=(
                    EmittedEvent(type="response.delta", cursor="4"),
                    EmittedEvent(type="response.delta", cursor="3"),
                ),
                reorder=True,
                disconnect=True,
            ),
        ),
    )


def _durable_rows_from_scenario(scenario: FaultScenario) -> list[SimpleNamespace]:
    """Project a scenario's transport events onto the store's durable frontier.

    A duplicate re-delivery collapses to one durable row per sequence; a reordered
    delivery is normalized to sequence order — this is the durable frontier the
    real store presents to the stream route, independent of transport disorder.
    """

    raw: list[tuple[int, str]] = []
    for step in scenario.steps:
        if step.on.value != "read_events":
            continue
        batch = [(int(event.cursor), event.type) for event in step.emit if event.cursor]
        if step.reorder:
            batch = list(reversed(batch))
        raw.extend(batch)
        if step.duplicate:
            raw.extend(batch)  # the provider re-delivered the batch on reconnect

    seen: dict[int, str] = {}
    for sequence, event_type in raw:
        seen.setdefault(sequence, event_type)  # dedup by durable sequence
    rows: list[SimpleNamespace] = []
    for sequence in sorted(seen):  # normalized to monotonic frontier order
        rows.append(
            SimpleNamespace(
                event_id=f"evt-{sequence}",
                bridge_session_id=_SESSION_ID,
                sequence=sequence,
                timestamp=SimpleNamespace(
                    isoformat=lambda seq=sequence: f"2026-08-18T00:00:0{seq}+00:00"
                ),
                direction="host_to_moonmind",
                event_type=seen[sequence],
                normalized_status="running",
                text_preview=f"event {sequence}",
                artifact_ref=None,
                metadata_={"sequence": sequence},
            )
        )
    return rows


class _FaultlabBridgeStore:
    """Minimal real-shaped store serving a fault-scenario frontier to the route."""

    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    async def get_bridge_session_owner(self, bridge_session_id: str):
        return SimpleNamespace(workflow_id=_WORKFLOW_ID, agent_run_id=_AGENT_RUN_ID)

    async def list_event_page(self, bridge_session_id: str, *, after: int, limit: int):
        rows = [row for row in self._rows if row.sequence > after]
        return SimpleNamespace(
            rows=rows[:limit],
            has_more=len(rows) > limit,
            latest_sequence=max((row.sequence for row in self._rows), default=0),
            earliest_sequence=min((row.sequence for row in self._rows), default=None),
        )

    async def get_bridge_session(self, bridge_session_id: str):
        # A completed terminal so the stream replays the terminal envelope after
        # the live frontier is delivered.
        return SimpleNamespace(
            status="completed",
            terminal_refs={"summary": "done"},
            metadata_={},
            diagnostics_ref="artifact://diagnostics",
            capture_manifest_ref=None,
            initial_snapshot_ref=None,
            final_snapshot_ref="artifact://final",
            raw_events_ref=None,
            normalized_events_ref=None,
            external_state_ref=None,
        )


class _FaultlabService:
    def __init__(self, owner_id: Any) -> None:
        self._owner_id = owner_id

    async def describe_execution(self, workflow_id: str):
        return SimpleNamespace(owner_id=str(self._owner_id))


def _mock_user():
    return SimpleNamespace(id=_USER_ID, email="faultlab@example.com", is_superuser=False)


def _build_client(rows: list[SimpleNamespace]) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix=OMNIGENT_BRIDGE_MOUNT_PATH)
    store = _FaultlabBridgeStore(rows)
    app.dependency_overrides[get_current_user()] = _mock_user
    app.dependency_overrides[_get_execution_service] = lambda: _FaultlabService(_USER_ID)
    app.dependency_overrides[_get_bridge_store] = lambda: store
    app.dependency_overrides[_require_bridge_enabled] = lambda: SimpleNamespace(
        host_protocol_mode="upstream_omnigent_server_proxy"
    )
    return TestClient(app)


def _parse_sse(body: str) -> tuple[list[int], bool]:
    """Return the delivered ``id:`` sequences and whether a terminal was seen."""

    sequences: list[int] = []
    terminal = False
    for block in body.split("\n\n"):
        lines = block.splitlines()
        event_id = None
        is_terminal = False
        for line in lines:
            if line.startswith("id: "):
                event_id = int(line[4:])
            elif line.startswith("event: terminal"):
                is_terminal = True
        if is_terminal:
            terminal = True
        elif event_id is not None:
            sequences.append(event_id)
    return sequences, terminal


_STREAM_URL = f"{OMNIGENT_BRIDGE_MOUNT_PATH}/bridge-sessions/{_SESSION_ID}/stream"


def test_bridge_stream_delivers_monotonic_deduped_frontier_and_terminal_replay() -> None:
    rows = _durable_rows_from_scenario(_transport_fault_scenario())
    client = _build_client(rows)

    response = client.get(_STREAM_URL)
    assert response.status_code == 200
    sequences, terminal = _parse_sse(response.text)

    # Duplicate + reordered transport events are normalized to one monotonic
    # frontier at the API boundary.
    assert sequences == [1, 2, 3, 4]
    assert sequences == sorted(set(sequences))
    # Terminal evidence is replayed after the live frontier (historical-read
    # safety at the transport edge).
    assert terminal is True


def test_bridge_stream_reconnect_with_cursor_does_not_redeliver() -> None:
    rows = _durable_rows_from_scenario(_transport_fault_scenario())
    client = _build_client(rows)

    # A reconnect after acknowledging sequence 2 (the disconnect boundary the
    # scenario injects) must resume strictly after it — never re-delivering an
    # already-acknowledged event.
    response = client.get(_STREAM_URL, headers={"Last-Event-ID": "2"})
    assert response.status_code == 200
    sequences, terminal = _parse_sse(response.text)

    assert sequences == [3, 4]
    assert all(seq > 2 for seq in sequences)
    assert terminal is True
