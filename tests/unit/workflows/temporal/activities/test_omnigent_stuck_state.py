"""Activity-adapter coverage for MoonLadderStudios/MoonMind#3708."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from moonmind.omnigent.control_plane.records import SessionRecord
from moonmind.workflows.temporal.activities.omnigent_stuck_state import (
    TemporalOmnigentReconcileDispatcher,
    TemporalStuckStateDiagnosticPublisher,
)


@pytest.mark.asyncio
async def test_dispatcher_updates_the_canonical_session_supervisor_contract() -> None:
    class _Client:
        def __init__(self) -> None:
            self.calls = []

        async def update_workflow(self, *args) -> None:
            self.calls.append(args)

    client = _Client()
    dispatcher = TemporalOmnigentReconcileDispatcher(client)

    await dispatcher.request_reconcile(
        session_id="sess-1",
        workflow_id="wf-1",
        request_id="ocm-1",
        reason_code="live_conformance_evidence_stale",
        expected_revision="7",
        expected_fencing_generation="3",
    )

    assert client.calls == [
        (
            "omnigent-session:sess-1",
            "ReconcileOmnigentSession",
            {
                "sessionId": "sess-1",
                "requestId": "ocm-1",
                "reasonCode": "live_conformance_evidence_stale",
                "expectedRevision": 7,
                "expectedFencingGeneration": 3,
            },
        )
    ]


@pytest.mark.asyncio
async def test_diagnostic_publisher_uses_restricted_durable_artifact_authority() -> None:
    class _Artifacts:
        def __init__(self) -> None:
            self.created = []
            self.written = []

        async def create(self, **kwargs):
            self.created.append(kwargs)
            return SimpleNamespace(artifact_id="art-diagnostic"), object()

        async def write_complete(self, **kwargs) -> None:
            self.written.append(kwargs)

    artifacts = _Artifacts()
    publisher = TemporalStuckStateDiagnosticPublisher(artifacts)
    session = SessionRecord(
        session_id="sess-1",
        moonmind_workflow_id="wf-1",
        moonmind_run_id="run-1",
        provider="codex",
    )

    artifact_id = await publisher.publish(
        session=session,
        decision_id="odc-1",
        payload={
            "reason": "live_conformance_evidence_stale",
            "password": "must-redact",
        },
    )

    assert artifact_id == "art-diagnostic"
    assert artifacts.created[0]["link"]["workflow_id"] == "wf-1"
    assert artifacts.created[0]["metadata_json"]["issue"] == (
        "MoonLadderStudios/MoonMind#3708"
    )
    assert b"live_conformance_evidence_stale" in artifacts.written[0]["payload"]
    assert b"must-redact" not in artifacts.written[0]["payload"]
