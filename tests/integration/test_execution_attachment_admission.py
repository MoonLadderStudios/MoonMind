"""The canonical create winner owns attachments from concurrent API requests."""

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from temporalio.service import RPCError, RPCStatusCode

from api_service.db.models import TemporalArtifactLink, TemporalExecutionCanonicalRecord
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.artifact_access import access_for_test
from tests.integration import test_execution_input_admission_postgres as admission
from tests.integration.test_execution_start_admission import _submit
from tests.unit.omnigent.test_admitted_input_authority import _protect, _put
from tests.unit.omnigent.test_api_planning_input_authority import _api_context

sessions = admission.sessions
conversion_postgres_url = admission.conversion_postgres_url
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
_IMAGE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/p8AAAAASUVORK5CYII="
)


async def test_first_submissions_do_not_donate_losing_attachments(
    sessions, monkeypatch
):
    saved = await _api_context(sessions, monkeypatch)
    attachments = [
        await _put(
            sessions,
            _IMAGE,
            owner=saved.actor,
            workflow_id=None,
            link_type="",
            content_type="image/png",
        )
        for _ in range(3)
    ]
    starts = []

    async def start(**kwargs):
        artifact_id = kwargs["input_args"]["initial_parameters"]["workflow"][
            "inputAttachments"
        ][0]["artifactId"]
        async with sessions() as session:
            reader = TemporalArtifactService(TemporalArtifactRepository(session))
            _, body = await reader.read(
                artifact_id=artifact_id,
                access=access_for_test(f"workflow:{kwargs['workflow_id']}"),
            )
            assert body == _IMAGE
        starts.append(kwargs)
        return SimpleNamespace(run_id="actual-attachment-race-run")

    async def describe(_workflow_id):
        if starts:
            return SimpleNamespace(run_id="actual-attachment-race-run")
        raise RPCError("not started", RPCStatusCode.NOT_FOUND, b"")

    adapter = SimpleNamespace(
        start_workflow=AsyncMock(side_effect=start),
        describe_workflow=AsyncMock(side_effect=describe),
        namespace_retention_seconds=AsyncMock(return_value=86_400),
    )
    actual_create = TemporalExecutionService.create_execution
    both_admitted = asyncio.Event()
    admissions = 0

    async def overlap(self, **kwargs):
        nonlocal admissions
        admissions += 1
        if admissions == 2:
            both_admitted.set()
        # Both API requests must pass their initial missing-key lookup and
        # compile distinct snapshots before the canonical create picks one.
        await asyncio.wait_for(both_admitted.wait(), 10)
        return await actual_create(self, **kwargs)

    monkeypatch.setattr(TemporalExecutionService, "create_execution", overlap)
    requests = [
        {
            "type": "workflow",
            "payload": {
                "idempotencyKey": "two-first-attachment-submissions",
                "workflow": {
                    "instructions": f"Inspect image {index}",
                    "publish": {"mode": "none"},
                    "inputAttachments": [
                        {
                            "artifactId": artifact_id,
                            "filename": f"image-{index}.png",
                            "contentType": "image/png",
                            "sizeBytes": len(_IMAGE),
                        }
                    ],
                },
            },
        }
        for index, artifact_id in enumerate(attachments[:2])
    ]
    replies = await asyncio.wait_for(
        asyncio.gather(*(_submit(sessions, adapter, body) for body in requests)), 45
    )
    assert admissions == 2
    assert len(starts) == 1
    assert len({reply.workflow_id for reply in replies}) == 1
    winner_parameters = starts[0]["input_args"]["initial_parameters"]
    winner_id = winner_parameters["workflow"]["inputAttachments"][0]["artifactId"]
    loser_id = next(
        artifact_id for artifact_id in attachments[:2] if artifact_id != winner_id
    )
    async with sessions() as session:
        record = (
            await session.execute(select(TemporalExecutionCanonicalRecord))
        ).scalar_one()
        assert record.memo["task_input_snapshot_ref"] == (
            winner_parameters["omnigentExecutionPlan"]["taskInputSnapshotRef"]
        )
        assert loser_id not in record.artifact_refs
        attachment_links = (
            (
                await session.execute(
                    select(TemporalArtifactLink).where(
                        TemporalArtifactLink.workflow_id == record.workflow_id,
                        TemporalArtifactLink.link_type == "input.attachment",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(attachment_links) == 1
        assert attachment_links[0].artifact_id == winner_id
        assert attachment_links[0].run_id == record.run_id
        assert attachment_links[0].label == (
            winner_parameters["workflow"]["inputAttachments"][0]["filename"]
        )
        reader = TemporalArtifactService(TemporalArtifactRepository(session))
        _, body = await reader.read(
            artifact_id=winner_id,
            access=access_for_test(f"workflow:{record.workflow_id}"),
        )
        assert body == _IMAGE
        for denied_id in (loser_id, attachments[2]):
            assert denied_id not in record.artifact_refs
            with pytest.raises(TemporalArtifactAuthorizationError):
                await reader.read(
                    artifact_id=denied_id,
                    access=access_for_test(f"workflow:{record.workflow_id}"),
                )


async def test_quarantined_attachment_never_admits_or_starts(sessions, monkeypatch):
    saved = await _api_context(sessions, monkeypatch)
    artifact_id = await _put(
        sessions,
        _IMAGE,
        owner=saved.actor,
        workflow_id=None,
        link_type="",
        content_type="image/png",
    )
    await _protect(sessions, artifact_id, "quarantined")
    adapter = SimpleNamespace(
        start_workflow=AsyncMock(return_value=SimpleNamespace(run_id="must-not-start"))
    )
    with pytest.raises(HTTPException) as denied:
        await _submit(
            sessions,
            adapter,
            {
                "type": "workflow",
                "payload": {
                    "idempotencyKey": "quarantined-attachment",
                    "workflow": {
                        "instructions": "Inspect the admitted image",
                        "publish": {"mode": "none"},
                        "inputAttachments": [
                            {
                                "artifactId": artifact_id,
                                "filename": "quarantined.png",
                                "contentType": "image/png",
                                "sizeBytes": len(_IMAGE),
                            }
                        ],
                    },
                },
            },
        )
    assert denied.value.status_code == 422
    adapter.start_workflow.assert_not_awaited()
    async with sessions() as session:
        assert (
            await session.execute(select(TemporalExecutionCanonicalRecord))
        ).scalars().all() == []
        assert (
            await session.execute(select(TemporalArtifactLink))
        ).scalars().all() == []
