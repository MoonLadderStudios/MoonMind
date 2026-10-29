"""Concurrent continuation materializers preserve one durable evidence winner."""

import asyncio
import hashlib
from copy import deepcopy

import pytest
from sqlalchemy import select, text

from api_service.db import models
from api_service.services.linked_continuation import (
    SqlLinkedContinuationRepository,
    evidence_copy_admission,
    evidence_copy_attachments,
)
from tests.support.isolated_postgres import isolated_postgres

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


async def test_concurrent_materializers_preserve_first_committed_copy_admission():
    tables = [
        models.TemporalArtifact.__table__,
        models.WorkflowLinkedContinuationRecord.__table__,
    ]
    source = "artifact://art_authorized_source"
    payload = b"authorized source evidence"
    digest = hashlib.sha256(payload).hexdigest()
    attachments = [
        {
            "artifactId": f"art_copy_{name}",
            "filename": f"{name}.txt",
            "contentType": "text/plain",
            "sizeBytes": len(payload),
        }
        for name in ("left", "right")
    ]
    async with isolated_postgres(tables) as sessions:
        async with sessions() as seed:
            # Each request has independently finished an authorized copy of the
            # selected source. This repository boundary receives completed rows,
            # not the source bytes or untrusted request metadata.
            for attachment in attachments:
                seed.add(
                    models.TemporalArtifact(
                        artifact_id=attachment["artifactId"],
                        storage_key=attachment["artifactId"],
                        status=models.TemporalArtifactStatus.COMPLETE,
                        content_type=attachment["contentType"],
                        size_bytes=attachment["sizeBytes"],
                        sha256=digest,
                        metadata_json={
                            "artifact_class": "linked_continuation_source_evidence",
                            "source_ref": source,
                        },
                    )
                )
            reservation = await SqlLinkedContinuationRepository(seed).reserve_or_get(
                source_workflow_id="source",
                source_run_id="source-run",
                idempotency_key="same-request",
                request_digest="same-digest",
                pinned_source_refs={"selectedSourceArtifactRefs": [source]},
                source_logical_step_id=None,
                source_step_execution_id=None,
                created_by="operator",
                bounded_purpose="continue",
            )
            record_id = reservation.record.id
            await seed.commit()

        async with sessions() as left, sessions() as right, sessions() as blocker:
            records = [
                await session.get(models.WorkflowLinkedContinuationRecord, record_id)
                for session in (left, right)
            ]
            assert all(
                "evidenceCopyAdmission" not in r.pinned_source_refs for r in records
            )
            pids = [
                await session.scalar(text("SELECT pg_backend_pid()"))
                for session in (left, right)
            ]
            await blocker.execute(
                select(models.WorkflowLinkedContinuationRecord)
                .where(models.WorkflowLinkedContinuationRecord.id == record_id)
                .with_for_update()
            )
            tasks = [
                asyncio.create_task(
                    SqlLinkedContinuationRepository(session).record_evidence_copies(
                        record, namespace="default", attachments=[attachment]
                    )
                )
                for session, record, attachment in zip(
                    (left, right), records, attachments, strict=True
                )
            ]
            try:
                # Prove both live transactions reached the row lock, rather
                # than relying on sleeps or accidentally testing serial calls.
                async with asyncio.timeout(10), sessions() as observer:
                    while True:
                        blocked = [
                            await observer.scalar(
                                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                                {"pid": pid},
                            )
                            for pid in pids
                        ]
                        if all(blocked):
                            break
                        assert not any(task.done() for task in tasks)
                        await asyncio.sleep(0.01)
                await blocker.commit()
                async with asyncio.timeout(10):
                    results = await asyncio.gather(*tasks)
            finally:
                await blocker.rollback()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            assert results[0] == results[1]
            assert results[0] in [[attachment] for attachment in attachments]
            assert records[0].pinned_source_refs == records[1].pinned_source_refs
            winner = deepcopy(records[0].pinned_source_refs)

        async with sessions() as verify:
            record = await verify.get(
                models.WorkflowLinkedContinuationRecord, record_id
            )
            assert record.pinned_source_refs == winner
            copies = evidence_copy_admission(record, namespace="default")
            assert evidence_copy_attachments(copies) == results[0]
            assert copies[0]["sha256"] == digest
            assert copies[0]["sourceRef"] == source
            assert record.pinned_source_refs["selectedSourceArtifactRefs"] == [source]
