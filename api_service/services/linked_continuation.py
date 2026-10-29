"""Idempotent linked-continuation admission (MoonLadderStudios/MoonMind#3641).

Owns the durable, source-immutable reservation that backs the terminal
**Continue in a new workflow** action. One client idempotency key binds to
exactly one destination Workflow Execution; the source Workflow, its run, and its
evidence are only read, never mutated. The relational persistence lives in
``WorkflowLinkedContinuationRecord``; the ordinary create/compiler path (owned by
the executions router) is what actually starts the destination workflow. This
module deliberately does not start a workflow — it reserves and finalizes the
lineage around that create so a duplicate submission, or a retry after a failed
create, cannot fork two linked workflows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    TemporalArtifact,
    TemporalArtifactStatus,
    WorkflowLinkedContinuationRecord,
)

RELATIONSHIP_TYPE_LINKED_CONTINUATION = "linked_continuation"


class LinkedContinuationError(Exception):
    """Base class for linked-continuation admission failures."""


class LinkedContinuationConflict(LinkedContinuationError):
    """An idempotency key was reused for a materially different request.

    The changed request fails closed rather than being silently deduplicated to
    the first submission's destination, so a caller never has an edited
    continuation quietly dropped.
    """


def compute_request_digest(payload: dict[str, Any]) -> str:
    """Stable digest over the pinnable, authored continuation inputs.

    Two submissions with the same idempotency key must carry the same source
    identity, selected evidence, and authored intent to deduplicate; any
    difference is a conflict. Serialized with sorted keys so ordering never
    changes the digest.
    """

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


EVIDENCE_COPY_ADMISSION = "evidenceCopyAdmission"
CONTINUATION_EVIDENCE_MAX_BYTES = 10 * 1024 * 1024


def evidence_copy_admission(record, *, namespace: str) -> list[dict[str, Any]]:
    """Decode only the server-recorded copies, never authored workflow inputs."""
    pinned = record.pinned_source_refs or {}
    admission = pinned.get(EVIDENCE_COPY_ADMISSION)
    selected = pinned.get("selectedSourceArtifactRefs")
    if (
        not isinstance(admission, dict)
        or admission.get("schemaVersion") != 1
        or admission.get("namespace") != namespace
        or not isinstance(selected, list)
        or not isinstance(admission.get("copies"), list)
        or not admission["copies"]
    ):
        raise LinkedContinuationConflict(
            "Continuation evidence has no matching recorded copy admission; "
            "restore its existing input links or submit a new continuation."
        )
    seen = set()
    for entry in admission["copies"]:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("artifactId"), str)
            or not entry["artifactId"].startswith("art_")
            or entry["artifactId"] in seen
            or entry.get("sourceRef") not in selected
            or not isinstance(entry.get("sha256"), str)
            or len(entry["sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in entry["sha256"])
            or not isinstance(entry.get("sizeBytes"), int)
            or isinstance(entry.get("sizeBytes"), bool)
            or not 0 <= entry["sizeBytes"] <= CONTINUATION_EVIDENCE_MAX_BYTES
            or not isinstance(entry.get("contentType"), str)
            or not isinstance(entry.get("filename"), str)
        ):
            raise LinkedContinuationConflict(
                "Continuation evidence copy admission is invalid"
            )
        seen.add(entry["artifactId"])
    return admission["copies"]


def evidence_copy_attachments(copies):
    return [
        {
            key: entry[key]
            for key in ("artifactId", "filename", "contentType", "sizeBytes")
        }
        for entry in copies
    ]


@dataclass(frozen=True)
class LinkedContinuationReservation:
    """Outcome of admitting one linked-continuation request."""

    record: WorkflowLinkedContinuationRecord
    destination_workflow_id: str
    already_finalized: bool


class SqlLinkedContinuationRepository:
    """Durable reservation/finalization for the linked-continuation relationship."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _row_for_key(
        self,
        *,
        source_workflow_id: str,
        source_run_id: str,
        idempotency_key: str,
        for_update: bool,
    ) -> WorkflowLinkedContinuationRecord | None:
        stmt = select(WorkflowLinkedContinuationRecord).where(
            WorkflowLinkedContinuationRecord.source_workflow_id == source_workflow_id,
            WorkflowLinkedContinuationRecord.source_run_id == source_run_id,
            WorkflowLinkedContinuationRecord.idempotency_key == idempotency_key,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def reserve_or_get(
        self,
        *,
        source_workflow_id: str,
        source_run_id: str,
        idempotency_key: str,
        request_digest: str,
        pinned_source_refs: dict[str, Any],
        source_logical_step_id: str | None,
        source_step_execution_id: str | None,
        created_by: str | None,
        bounded_purpose: str | None,
    ) -> LinkedContinuationReservation:
        """Reserve one destination workflow id for a key, or return the existing one.

        The destination workflow id is pinned at reservation time so a duplicate
        or a retry after a failed create always drives the ordinary create path
        to the *same* id (idempotent). ``already_finalized`` distinguishes a
        completed reservation (return the same destination, do not create again)
        from a reserved-but-unfinalized one (create/retry the destination and
        finalize).
        """

        existing = await self._row_for_key(
            source_workflow_id=source_workflow_id,
            source_run_id=source_run_id,
            idempotency_key=idempotency_key,
            for_update=True,
        )
        if existing is not None:
            self._assert_digest(existing, request_digest)
            return LinkedContinuationReservation(
                record=existing,
                destination_workflow_id=str(existing.destination_workflow_id),
                already_finalized=existing.reserved_at is not None,
            )

        record = WorkflowLinkedContinuationRecord(
            source_workflow_id=source_workflow_id,
            source_run_id=source_run_id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            relationship_type=RELATIONSHIP_TYPE_LINKED_CONTINUATION,
            source_logical_step_id=source_logical_step_id,
            source_step_execution_id=source_step_execution_id,
            pinned_source_refs=dict(pinned_source_refs),
            created_by=created_by,
            bounded_purpose=bounded_purpose,
            destination_workflow_id=f"mm:{uuid4()}",
            reserved_at=None,
        )
        self._session.add(record)
        try:
            await self._session.flush()
        except IntegrityError:
            # A concurrent request inserted the same key first. Reconcile against
            # the committed row instead of forking a second destination.
            await self._session.rollback()
            reconciled = await self._row_for_key(
                source_workflow_id=source_workflow_id,
                source_run_id=source_run_id,
                idempotency_key=idempotency_key,
                for_update=True,
            )
            if reconciled is None:
                raise
            self._assert_digest(reconciled, request_digest)
            return LinkedContinuationReservation(
                record=reconciled,
                destination_workflow_id=str(reconciled.destination_workflow_id),
                already_finalized=reconciled.reserved_at is not None,
            )
        return LinkedContinuationReservation(
            record=record,
            destination_workflow_id=str(record.destination_workflow_id),
            already_finalized=False,
        )

    @staticmethod
    def _assert_digest(
        record: WorkflowLinkedContinuationRecord, request_digest: str
    ) -> None:
        if record.request_digest != request_digest:
            raise LinkedContinuationConflict(
                "The idempotency key was already used for a different continuation "
                "request."
            )

    async def record_evidence_copies(
        self,
        record: WorkflowLinkedContinuationRecord,
        *,
        namespace: str,
        attachments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Pin authorized copies before dispatch and preserve a concurrent winner."""
        result = await self._session.execute(
            select(WorkflowLinkedContinuationRecord)
            .where(WorkflowLinkedContinuationRecord.id == record.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        record = result.scalar_one()
        pinned = dict(record.pinned_source_refs or {})
        if EVIDENCE_COPY_ADMISSION not in pinned:
            copies = []
            for attachment in attachments:
                artifact = await self._session.get(
                    TemporalArtifact, attachment["artifactId"]
                )
                metadata = dict(getattr(artifact, "metadata_json", None) or {})
                if (
                    artifact is None
                    or artifact.status is not TemporalArtifactStatus.COMPLETE
                    or metadata.get("artifact_class")
                    != "linked_continuation_source_evidence"
                    or metadata.get("source_ref")
                    not in pinned.get("selectedSourceArtifactRefs", [])
                    or artifact.size_bytes != attachment["sizeBytes"]
                    or artifact.content_type != attachment["contentType"]
                ):
                    raise LinkedContinuationConflict(
                        "Continuation evidence copy is unavailable"
                    )
                copies.append(
                    {
                        **attachment,
                        "sha256": artifact.sha256,
                        "sourceRef": metadata["source_ref"],
                    }
                )
            pinned[EVIDENCE_COPY_ADMISSION] = {
                "schemaVersion": 1,
                "namespace": namespace,
                "copies": copies,
            }
            record.pinned_source_refs = pinned
        copies = evidence_copy_admission(record, namespace=namespace)
        await self._session.commit()
        return evidence_copy_attachments(copies)

    async def finalize(
        self,
        record: WorkflowLinkedContinuationRecord,
        *,
        destination_run_id: str | None,
    ) -> None:
        """Mark the reservation complete once the destination workflow exists."""

        record.destination_run_id = destination_run_id
        record.reserved_at = datetime.now(UTC)
        await self._session.flush()

    async def list_for_source(
        self, source_workflow_id: str
    ) -> list[WorkflowLinkedContinuationRecord]:
        """Finalized continuations that name ``source_workflow_id`` as the source."""

        stmt = (
            select(WorkflowLinkedContinuationRecord)
            .where(
                WorkflowLinkedContinuationRecord.source_workflow_id
                == source_workflow_id,
                WorkflowLinkedContinuationRecord.reserved_at.is_not(None),
            )
            .order_by(WorkflowLinkedContinuationRecord.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_destination(
        self, destination_workflow_id: str
    ) -> WorkflowLinkedContinuationRecord | None:
        """The linked-continuation record whose destination is this workflow."""

        stmt = select(WorkflowLinkedContinuationRecord).where(
            WorkflowLinkedContinuationRecord.destination_workflow_id
            == destination_workflow_id,
            WorkflowLinkedContinuationRecord.reserved_at.is_not(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


__all__ = [
    "RELATIONSHIP_TYPE_LINKED_CONTINUATION",
    "LinkedContinuationConflict",
    "LinkedContinuationError",
    "LinkedContinuationReservation",
    "SqlLinkedContinuationRepository",
    "compute_request_digest",
]
