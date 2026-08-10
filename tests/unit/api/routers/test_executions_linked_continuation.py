"""Router-boundary tests for terminal captured evidence + linked continuation.

MoonLadderStudios/MoonMind#3641. Exercises the real ``continue_in_new_workflow``,
``get_workflow_captured_evidence``, and ``list_execution_continuations`` handlers
with a real in-memory session and the linked-continuation repository, proving the
terminal gate, evidence authorization, source pinning, idempotency, and
bidirectional relationship display without mutating the source.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import executions as ex
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalExecutionRecord,
    TemporalWorkflowType,
    WorkflowLinkedContinuationRecord,
)


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: TemporalExecutionCanonicalRecord.__table__.create(sync)
        )
        await connection.run_sync(
            lambda sync: WorkflowLinkedContinuationRecord.__table__.create(sync)
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_canonical(sessions, owner_id: str) -> None:
    async with sessions() as session:
        session.add(
            TemporalExecutionCanonicalRecord(
                workflow_id="mm:source",
                run_id="run-1",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                entry="user_workflow",
                owner_id=owner_id,
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.COMPLETED,
                parameters={"workflow": {"instructions": "original intent"}},
                memo={"title": "Source workflow"},
            )
        )
        await session.commit()


class _FakeService:
    """Minimal stand-in that records the ordinary create-path invocation."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.create_calls: list[dict] = []
        self._fail_times = fail_times

    @staticmethod
    def _full_rerun_parameters(parameters):
        return dict(parameters or {})

    async def create_execution(self, **kwargs):
        self.create_calls.append(kwargs)
        if len(self.create_calls) <= self._fail_times:
            from moonmind.workflows.temporal import (
                TemporalExecutionValidationError,
            )

            raise TemporalExecutionValidationError("transient create failure")
        return SimpleNamespace(
            workflow_id=kwargs["_workflow_id"],
            run_id="dest-run",
        )


def _evidence(**overrides) -> ex._SourceCapturedEvidence:
    base = dict(
        available=True,
        named_refs={
            "finalSnapshotRef": "art_final",
            "captureManifestRef": "art_manifest",
        },
        additional_refs=["art_output"],
        finish_summary_ref="art_summary",
        final_snapshot_ref="art_final",
        capture_manifest_ref="art_manifest",
        summary="did the thing",
        logical_step_id=None,
        step_execution_id="step-9",
        incomplete_reason=None,
    )
    base.update(overrides)
    return ex._SourceCapturedEvidence(**base)


def _patch_collaborators(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_status: str = "completed",
    evidence: ex._SourceCapturedEvidence | None = None,
    materialized_attachments: list[dict] | None = None,
    attach_calls: list[list[dict]] | None = None,
) -> None:
    async def _owned(*, service, workflow_id, user):  # noqa: ANN001
        return SimpleNamespace(
            workflow_id="mm:source", run_id="run-1", status=source_status
        )

    async def _resolve(*, workflow_id, run_id):  # noqa: ANN001
        return evidence if evidence is not None else _evidence()

    async def _snapshot(**kwargs):  # noqa: ANN001
        return "art_snapshot"

    async def _materialize(*, session, user, refs):  # noqa: ANN001
        return list(materialized_attachments or [])

    async def _attach(*, session, record, attachment_refs):  # noqa: ANN001
        if attach_calls is not None:
            attach_calls.append(list(attachment_refs))

    monkeypatch.setattr(ex, "_get_owned_execution", _owned)
    monkeypatch.setattr(ex, "_resolve_source_captured_evidence", _resolve)
    monkeypatch.setattr(
        ex,
        "_persist_original_workflow_input_snapshot_from_parameters",
        _snapshot,
    )
    # Keep the artifact-store-backed materialization out of the hermetic router
    # tests by default; the materialization logic is covered separately.
    monkeypatch.setattr(
        ex, "_materialize_continuation_source_attachments", _materialize
    )
    monkeypatch.setattr(
        ex, "_attach_input_attachment_artifacts_to_execution", _attach
    )


def _payload(**overrides) -> ex.ContinueInNewWorkflowRequest:
    data = {"idempotencyKey": "ck-1"}
    data.update(overrides)
    return ex.ContinueInNewWorkflowRequest.model_validate(data)


@pytest.mark.asyncio
async def test_continue_creates_linked_workflow_and_pins_source(monkeypatch) -> None:
    engine, sessions = await _database()
    user = SimpleNamespace(id=uuid4())
    await _seed_canonical(sessions, owner_id=str(user.id))
    _patch_collaborators(monkeypatch)
    telemetry: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ex,
        "record_native_chat_request",
        lambda stage, outcome: telemetry.append((stage, outcome)),
    )
    service = _FakeService()

    async with sessions() as session:
        result = await ex.continue_in_new_workflow(
            workflow_id="mm:source",
            payload=_payload(
                title="Follow-up",
                instructions="continue the work",
                selectedSourceArtifactRefs=["art_final", "art_output"],
                boundedPurpose="ship the follow-up",
            ),
            service=service,  # type: ignore[arg-type]
            session=session,
            user=user,
            _submit_enabled=None,
        )

    assert result.created is True
    assert result.relationship_type == "linked_continuation"
    assert result.destination_workflow_id.startswith("mm:")
    assert result.pinned_source_refs["sourceWorkflowId"] == "mm:source"
    assert result.pinned_source_refs["sourceFinalSnapshotRef"] == "art_final"
    assert result.pinned_source_refs["sourceCaptureManifestRef"] == "art_manifest"
    assert result.pinned_source_refs["sourceFinishSummaryRef"] == "art_summary"
    assert result.pinned_source_refs["selectedSourceArtifactRefs"] == [
        "art_final",
        "art_output",
    ]
    assert telemetry == [("continuation", "success")]

    # Ordinary create path was reused with the reserved destination id, the pinned
    # source lineage, and the authored new intent.
    assert len(service.create_calls) == 1
    call = service.create_calls[0]
    assert call["_workflow_id"] == result.destination_workflow_id
    # The create idempotency key is scoped to (source_workflow_id, source_run_id,
    # idempotency_key) like the relationship reservation, and derived as a bounded
    # digest so it fits the String(128) column even for a 512-char client key.
    expected_create_key = "continue:" + ex.compute_request_digest(
        {
            "sourceWorkflowId": "mm:source",
            "sourceRunId": "run-1",
            "idempotencyKey": "ck-1",
        }
    )
    assert call["idempotency_key"] == expected_create_key
    assert len(call["idempotency_key"]) <= 128
    # The source plan/manifest are regenerated for the authored continuation
    # intent rather than pinned (a pinned plan short-circuits compilation, so the
    # continuation would run the source's old nodes).
    assert call["plan_artifact_ref"] is None
    assert call["manifest_artifact_ref"] is None
    params = call["initial_parameters"]
    assert params["continuationSource"]["relationshipType"] == "linked_continuation"
    assert params["workflow"]["instructions"] == "continue the work"

    # The relationship is durably finalized for bidirectional display.
    async with sessions() as session:
        repo = ex.SqlLinkedContinuationRepository(session)
        outbound = await repo.list_for_source("mm:source")
        assert [r.destination_workflow_id for r in outbound] == [
            result.destination_workflow_id
        ]
        assert outbound[0].created_by == str(user.id)
        assert outbound[0].bounded_purpose == "ship the follow-up"
    await engine.dispose()


@pytest.mark.asyncio
async def test_continue_is_idempotent_on_repeat_key(monkeypatch) -> None:
    engine, sessions = await _database()
    user = SimpleNamespace(id=uuid4())
    await _seed_canonical(sessions, owner_id=str(user.id))
    _patch_collaborators(monkeypatch)
    service = _FakeService()

    async def _run():
        async with sessions() as session:
            return await ex.continue_in_new_workflow(
                workflow_id="mm:source",
                payload=_payload(title="Follow-up"),
                service=service,  # type: ignore[arg-type]
                session=session,
                user=user,
                _submit_enabled=None,
            )

    first = await _run()
    second = await _run()

    assert first.created is True
    assert second.created is False
    assert first.destination_workflow_id == second.destination_workflow_id
    # The ordinary create path ran exactly once; the duplicate reconciled.
    assert len(service.create_calls) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_continue_retries_to_same_destination_after_failed_create(
    monkeypatch,
) -> None:
    engine, sessions = await _database()
    user = SimpleNamespace(id=uuid4())
    await _seed_canonical(sessions, owner_id=str(user.id))
    _patch_collaborators(monkeypatch)
    # The first create attempt fails after the reservation is persisted.
    service = _FakeService(fail_times=1)

    async def _run():
        async with sessions() as session:
            return await ex.continue_in_new_workflow(
                workflow_id="mm:source",
                payload=_payload(title="Follow-up"),
                service=service,  # type: ignore[arg-type]
                session=session,
                user=user,
                _submit_enabled=None,
            )

    with pytest.raises(HTTPException) as excinfo:
        await _run()
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["code"] == "continuation_validation_failed"

    # The reservation persisted (unfinalized); the retry re-drives the create to
    # the same reserved destination id and finalizes it.
    async with sessions() as session:
        repo = ex.SqlLinkedContinuationRepository(session)
        assert await repo.list_for_source("mm:source") == []  # not yet finalized

    result = await _run()
    assert result.created is True
    reserved_ids = {call["_workflow_id"] for call in service.create_calls}
    assert len(reserved_ids) == 1
    assert result.destination_workflow_id in reserved_ids

    async with sessions() as session:
        repo = ex.SqlLinkedContinuationRepository(session)
        outbound = await repo.list_for_source("mm:source")
        assert [r.destination_workflow_id for r in outbound] == [
            result.destination_workflow_id
        ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_continue_rejects_non_terminal_source(monkeypatch) -> None:
    engine, sessions = await _database()
    user = SimpleNamespace(id=uuid4())
    await _seed_canonical(sessions, owner_id=str(user.id))
    _patch_collaborators(monkeypatch, source_status="executing")
    telemetry: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ex,
        "record_native_chat_request",
        lambda stage, outcome: telemetry.append((stage, outcome)),
    )
    service = _FakeService()

    with pytest.raises(HTTPException) as excinfo:
        async with sessions() as session:
            await ex.continue_in_new_workflow(
                workflow_id="mm:source",
                payload=_payload(),
                service=service,  # type: ignore[arg-type]
                session=session,
                user=user,
                _submit_enabled=None,
            )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "continuation_source_not_terminal"
    assert service.create_calls == []
    assert telemetry == [("continuation", "stale_rejected")]
    await engine.dispose()


@pytest.mark.asyncio
async def test_continue_rejects_unauthorized_evidence(monkeypatch) -> None:
    engine, sessions = await _database()
    user = SimpleNamespace(id=uuid4())
    await _seed_canonical(sessions, owner_id=str(user.id))
    _patch_collaborators(monkeypatch)
    telemetry: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ex,
        "record_native_chat_request",
        lambda stage, outcome: telemetry.append((stage, outcome)),
    )
    service = _FakeService()

    with pytest.raises(HTTPException) as excinfo:
        async with sessions() as session:
            await ex.continue_in_new_workflow(
                workflow_id="mm:source",
                payload=_payload(
                    selectedSourceArtifactRefs=["art_final", "art_not_authorized"]
                ),
                service=service,  # type: ignore[arg-type]
                session=session,
                user=user,
                _submit_enabled=None,
            )
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["code"] == "continuation_evidence_unauthorized"
    # Non-enumerating: the count is reported, never which refs.
    assert excinfo.value.detail["unauthorizedCount"] == 1
    assert service.create_calls == []
    assert telemetry == [("continuation", "denied")]
    await engine.dispose()


@pytest.mark.asyncio
async def test_continue_conflicts_on_reused_key_with_changed_request(
    monkeypatch,
) -> None:
    engine, sessions = await _database()
    user = SimpleNamespace(id=uuid4())
    await _seed_canonical(sessions, owner_id=str(user.id))
    _patch_collaborators(monkeypatch)
    service = _FakeService()

    async with sessions() as session:
        await ex.continue_in_new_workflow(
            workflow_id="mm:source",
            payload=_payload(title="First"),
            service=service,  # type: ignore[arg-type]
            session=session,
            user=user,
            _submit_enabled=None,
        )

    with pytest.raises(HTTPException) as excinfo:
        async with sessions() as session:
            await ex.continue_in_new_workflow(
                workflow_id="mm:source",
                payload=_payload(title="Edited intent"),
                service=service,  # type: ignore[arg-type]
                session=session,
                user=user,
                _submit_enabled=None,
            )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "continuation_idempotency_conflict"
    await engine.dispose()


@pytest.mark.asyncio
async def test_captured_evidence_projects_authorized_refs(monkeypatch) -> None:
    async def _owned(*, service, workflow_id, user):  # noqa: ANN001
        return SimpleNamespace(workflow_id="mm:source", run_id="run-1")

    async def _resolve(*, workflow_id, run_id):  # noqa: ANN001
        return _evidence()

    monkeypatch.setattr(ex, "_get_owned_execution", _owned)
    monkeypatch.setattr(ex, "_resolve_source_captured_evidence", _resolve)

    result = await ex.get_workflow_captured_evidence(
        workflow_id="mm:source",
        service=SimpleNamespace(),  # type: ignore[arg-type]
        user=SimpleNamespace(id=uuid4()),
    )
    assert result.available is True
    kinds = {item.kind for item in result.items}
    assert {"final_snapshot", "capture_manifest", "finish_summary", "output_artifact"} <= kinds
    refs = {item.artifact_ref for item in result.items}
    assert "art_final" in refs and "art_output" in refs


@pytest.mark.asyncio
async def test_list_continuations_outbound_and_inbound(monkeypatch) -> None:
    engine, sessions = await _database()
    user = SimpleNamespace(id=uuid4())
    await _seed_canonical(sessions, owner_id=str(user.id))
    _patch_collaborators(monkeypatch)
    service = _FakeService()

    async with sessions() as session:
        created = await ex.continue_in_new_workflow(
            workflow_id="mm:source",
            payload=_payload(),
            service=service,  # type: ignore[arg-type]
            session=session,
            user=user,
            _submit_enabled=None,
        )
    destination_id = created.destination_workflow_id

    async def _owned(*, service, workflow_id, user):  # noqa: ANN001
        return SimpleNamespace(workflow_id=workflow_id, run_id="r", status="completed")

    monkeypatch.setattr(ex, "_get_owned_execution", _owned)

    async with sessions() as session:
        outbound = await ex.list_execution_continuations(
            workflow_id="mm:source",
            direction="outbound",
            service=service,  # type: ignore[arg-type]
            session=session,
            user=user,
        )
    assert outbound.direction == "outbound"
    assert [i.destination_workflow_id for i in outbound.items] == [destination_id]
    assert outbound.items[0].relationship_type == "linked_continuation"

    async with sessions() as session:
        inbound = await ex.list_execution_continuations(
            workflow_id=destination_id,
            direction="inbound",
            service=service,  # type: ignore[arg-type]
            session=session,
            user=user,
        )
    assert inbound.direction == "inbound"
    assert [i.source_workflow_id for i in inbound.items] == ["mm:source"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_append_linked_continuations_surfaces_source_side_view(
    monkeypatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: WorkflowLinkedContinuationRecord.__table__.create(sync)
        )
        await connection.run_sync(
            lambda sync: TemporalExecutionRecord.__table__.create(sync)
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        record = WorkflowLinkedContinuationRecord(
            source_workflow_id="mm:source",
            source_run_id="run-1",
            idempotency_key="ck-1",
            request_digest="d",
            pinned_source_refs={},
            destination_workflow_id="mm:dest",
            destination_run_id="dest-run",
        )
        session.add(record)
        await session.flush()
        await session.refresh(record)
        record.reserved_at = record.created_at  # finalized
        session.add(
            TemporalExecutionRecord(
                workflow_id="mm:dest",
                run_id="dest-run",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                entry="user_workflow",
            )
        )
        await session.commit()

    monkeypatch.setattr(ex, "_is_execution_admin", lambda user: True)
    monkeypatch.setattr(
        ex, "_execution_related_run_metadata", lambda record: {"status": "completed"}
    )

    hydrated: list = []
    async with sessions() as session:
        await ex._append_linked_continuations(
            SimpleNamespace(workflow_id="mm:source"),  # type: ignore[arg-type]
            hydrated,
            session=session,
            user=SimpleNamespace(id=uuid4()),
        )

    assert len(hydrated) == 1
    assert hydrated[0].workflow_id == "mm:dest"
    assert hydrated[0].relationship == "Continued in a new workflow"
    assert hydrated[0].status == "completed"
    await engine.dispose()


@pytest.mark.asyncio
async def test_continue_conflicts_on_changed_bounded_purpose(monkeypatch) -> None:
    engine, sessions = await _database()
    user = SimpleNamespace(id=uuid4())
    await _seed_canonical(sessions, owner_id=str(user.id))
    _patch_collaborators(monkeypatch)
    service = _FakeService()

    async with sessions() as session:
        await ex.continue_in_new_workflow(
            workflow_id="mm:source",
            payload=_payload(boundedPurpose="first purpose"),
            service=service,  # type: ignore[arg-type]
            session=session,
            user=user,
            _submit_enabled=None,
        )

    # Reusing the key with an edited boundedPurpose is a materially changed
    # request — it must conflict, not silently return the first destination.
    with pytest.raises(HTTPException) as excinfo:
        async with sessions() as session:
            await ex.continue_in_new_workflow(
                workflow_id="mm:source",
                payload=_payload(boundedPurpose="edited purpose"),
                service=service,  # type: ignore[arg-type]
                session=session,
                user=user,
                _submit_enabled=None,
            )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "continuation_idempotency_conflict"
    await engine.dispose()


@pytest.mark.asyncio
async def test_continue_create_key_is_bounded_for_long_client_key(monkeypatch) -> None:
    engine, sessions = await _database()
    user = SimpleNamespace(id=uuid4())
    await _seed_canonical(sessions, owner_id=str(user.id))
    _patch_collaborators(monkeypatch)
    service = _FakeService()

    long_key = "k" * 512
    async with sessions() as session:
        await ex.continue_in_new_workflow(
            workflow_id="mm:source",
            payload=_payload(idempotencyKey=long_key),
            service=service,  # type: ignore[arg-type]
            session=session,
            user=user,
            _submit_enabled=None,
        )

    # A 512-char client key must not overflow the String(128) create key column.
    call = service.create_calls[0]
    assert call["idempotency_key"].startswith("continue:")
    assert len(call["idempotency_key"]) <= 128
    await engine.dispose()


@pytest.mark.asyncio
async def test_continue_injects_and_links_materialized_attachments(monkeypatch) -> None:
    engine, sessions = await _database()
    user = SimpleNamespace(id=uuid4())
    await _seed_canonical(sessions, owner_id=str(user.id))
    attach_calls: list[list[dict]] = []
    attachment = {
        "artifactId": "art_copy_1",
        "filename": "final.json",
        "contentType": "application/json",
        "sizeBytes": 12,
    }
    _patch_collaborators(
        monkeypatch,
        materialized_attachments=[attachment],
        attach_calls=attach_calls,
    )
    service = _FakeService()

    async with sessions() as session:
        result = await ex.continue_in_new_workflow(
            workflow_id="mm:source",
            payload=_payload(
                instructions="continue",
                selectedSourceArtifactRefs=["art_final"],
            ),
            service=service,  # type: ignore[arg-type]
            session=session,
            user=user,
            _submit_enabled=None,
        )

    assert result.created is True
    # The materialized evidence is attached under the workflow payload's
    # inputAttachments (the ordinary prepared-input shape) ...
    call = service.create_calls[0]
    attachments = call["initial_parameters"]["workflow"]["inputAttachments"]
    assert attachment in attachments
    # ... and linked to the destination workflow after it is created.
    assert attach_calls == [[attachment]]
    await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_source_evidence_populates_logical_step(monkeypatch) -> None:
    row = SimpleNamespace(
        metadata_={"logicalStepId": "step-42"},
        terminal_refs={"outputRefs": [], "summary": "done"},
        step_execution_id="se-1",
        final_snapshot_ref="art_final",
        initial_snapshot_ref=None,
        capture_manifest_ref="art_manifest",
        diagnostics_ref=None,
        raw_events_ref=None,
        normalized_events_ref=None,
        external_state_ref=None,
    )

    class _FakeStore:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        async def resolve_projection_session(self, *, workflow_id, run_id):  # noqa: ANN001
            return row

    monkeypatch.setattr(ex, "OmnigentBridgeSessionStore", _FakeStore)

    evidence = await ex._resolve_source_captured_evidence(
        workflow_id="mm:source", run_id="run-1"
    )
    # The logical step id is read from the bridge projection metadata rather than
    # hard-coded to None, so the continuation preserves the Step lineage.
    assert evidence.logical_step_id == "step-42"
    assert evidence.step_execution_id == "se-1"


class _FakeArtifactService:
    def __init__(self) -> None:
        self.writes: list[tuple[str, int]] = []
        self._counter = 0

    async def read(self, *, artifact_id, principal, allow_restricted_raw=False):  # noqa: ANN001
        return SimpleNamespace(artifact_id=artifact_id), b'{"k":"v"}'

    async def create(self, *, principal, content_type, size_bytes, retention_class, metadata_json=None, **kwargs):  # noqa: ANN001
        self._counter += 1
        art = SimpleNamespace(artifact_id=f"art_new_{self._counter}")
        return art, SimpleNamespace()

    async def write_complete(self, *, artifact_id, principal, payload, content_type):  # noqa: ANN001
        self.writes.append((artifact_id, len(payload)))
        return SimpleNamespace(artifact_id=artifact_id)


@pytest.mark.asyncio
async def test_materialize_source_attachments_copies_durable_refs(monkeypatch) -> None:
    fake = _FakeArtifactService()
    monkeypatch.setattr(ex, "get_temporal_artifact_service", lambda session: fake)

    result = await ex._materialize_continuation_source_attachments(
        session=SimpleNamespace(),
        user=SimpleNamespace(id=uuid4()),
        refs=["art_final", "art_manifest"],
    )

    assert [a["artifactId"] for a in result] == ["art_new_1", "art_new_2"]
    assert all(
        {"artifactId", "filename", "contentType", "sizeBytes"} <= set(a)
        for a in result
    )
    assert len(fake.writes) == 2


@pytest.mark.asyncio
async def test_materialize_source_attachments_reads_omnigent_and_skips_oversized(
    monkeypatch,
) -> None:
    fake = _FakeArtifactService()
    monkeypatch.setattr(ex, "get_temporal_artifact_service", lambda session: fake)

    import moonmind.omnigent.bridge_artifacts as bridge_artifacts

    class _FakeGateway:
        async def read_bytes(self, ref):  # noqa: ANN001
            return b"x" * 50

    monkeypatch.setattr(bridge_artifacts, "LocalOmnigentArtifactGateway", _FakeGateway)
    # Force the size guard: the omnigent ref's 50 bytes exceeds the cap and is
    # skipped rather than failing the continuation.
    monkeypatch.setattr(ex, "_CONTINUATION_EVIDENCE_MAX_BYTES", 10)

    result = await ex._materialize_continuation_source_attachments(
        session=SimpleNamespace(),
        user=SimpleNamespace(id=uuid4()),
        refs=["artifact://omnigent/corr/final.json"],
    )

    assert result == []
    assert fake.writes == []


def test_build_related_runs_surfaces_continuation_source() -> None:
    params = {
        "continuationSource": {
            "relationshipType": "linked_continuation",
            "sourceWorkflowId": "mm:source",
            "sourceRunId": "run-1",
        }
    }
    related = ex._build_related_runs(SimpleNamespace(parameters=params), params=params)
    continued = [r for r in related if r.relationship == "Continued from"]
    assert len(continued) == 1
    assert continued[0].workflow_id == "mm:source"
    assert continued[0].run_id == "run-1"
