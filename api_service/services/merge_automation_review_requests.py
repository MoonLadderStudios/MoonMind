"""Durable ledger for automated review requests owned by merge automation.

One logical request identity — parent workflow, repository, pull request, head
SHA, provider — maps to at most one posted GitHub comment.  The ledger exists
because GitHub comment creation has no idempotency key: without it, a lost
response after a successful POST would make a Temporal activity retry post a
second ``@codex review``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import MergeAutomationReviewRequestRecord
from moonmind.workflows.merge_automation_review import build_review_request_key

STATUS_PENDING = "pending"
STATUS_REQUESTED = "requested"
STATUS_FAILED = "failed"
STATUS_ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class ReviewRequestLedgerEntry:
    """Compact, non-sensitive view of one ledger row."""

    request_key: str
    status: str
    provider: str
    command: str
    head_sha: str
    repository: str
    pr_number: int
    request_comment_id: int | None
    request_comment_url: str | None
    requested_at: str | None
    actor: str | None
    reconciled: bool
    attempt_started_at: str
    failure_reason: str | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "requestKey": self.request_key,
            "status": self.status,
            "provider": self.provider,
            "command": self.command,
            "headSha": self.head_sha,
            "repository": self.repository,
            "prNumber": self.pr_number,
            "requestCommentId": self.request_comment_id,
            "requestCommentUrl": self.request_comment_url,
            "requestedAt": self.requested_at,
            "actor": self.actor,
            "reconciled": self.reconciled,
            "attemptStartedAt": self.attempt_started_at,
            "failureReason": self.failure_reason,
        }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _entry(record: MergeAutomationReviewRequestRecord) -> ReviewRequestLedgerEntry:
    return ReviewRequestLedgerEntry(
        request_key=record.request_key,
        status=record.status,
        provider=record.provider,
        command=record.command,
        head_sha=record.head_sha,
        repository=record.repository,
        pr_number=record.pr_number,
        request_comment_id=record.request_comment_id,
        request_comment_url=record.request_comment_url,
        requested_at=_iso(record.requested_at),
        actor=record.actor,
        reconciled=record.reconciled,
        attempt_started_at=_iso(record.attempt_started_at) or "",
        failure_reason=record.failure_reason,
    )


class MergeAutomationReviewRequestStore:
    """Read/claim/settle one automated review request identity."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, request_key: str) -> ReviewRequestLedgerEntry | None:
        record = await self._session.get(
            MergeAutomationReviewRequestRecord, request_key
        )
        return _entry(record) if record is not None else None

    async def claim(
        self,
        *,
        request_key: str,
        parent_workflow_id: str,
        repository: str,
        pr_number: int,
        head_sha: str,
        provider: str,
        command: str,
        now: datetime | None = None,
    ) -> ReviewRequestLedgerEntry:
        """Return the settled entry, or create/refresh a pending claim.

        A row that already reached ``requested`` is returned untouched so a
        Temporal retry or replay reuses the recorded comment instead of posting
        a new one.
        """

        existing = await self._session.get(
            MergeAutomationReviewRequestRecord, request_key
        )
        if existing is not None:
            if existing.status != STATUS_REQUESTED:
                # Keep the earliest attempt instant: reconciliation must be able
                # to see a comment posted by a previous ambiguous attempt.
                existing.status = STATUS_PENDING
                existing.failure_reason = None
            return _entry(existing)

        record = MergeAutomationReviewRequestRecord(
            request_key=request_key,
            parent_workflow_id=str(parent_workflow_id or "").strip(),
            repository=str(repository or "").strip(),
            pr_number=int(pr_number),
            head_sha=str(head_sha or "").strip(),
            provider=str(provider or "").strip().lower(),
            command=str(command or "").strip(),
            status=STATUS_PENDING,
            attempt_started_at=(now or datetime.now(UTC)),
        )
        self._session.add(record)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._session.get(
                MergeAutomationReviewRequestRecord, request_key
            )
            if existing is None:
                raise
            return _entry(existing)
        return _entry(record)

    async def settle(
        self,
        *,
        request_key: str,
        status: str,
        request_comment_id: int | None = None,
        request_comment_url: str | None = None,
        requested_at: datetime | None = None,
        actor: str | None = None,
        reconciled: bool = False,
        failure_reason: str | None = None,
    ) -> ReviewRequestLedgerEntry | None:
        record = await self._session.get(
            MergeAutomationReviewRequestRecord, request_key
        )
        if record is None:
            return None
        if record.status == STATUS_REQUESTED and status != STATUS_REQUESTED:
            # A settled request is authoritative; a later failure never erases
            # the proof that a comment was posted.
            return _entry(record)
        record.status = status
        if request_comment_id is not None:
            record.request_comment_id = int(request_comment_id)
        if request_comment_url is not None:
            record.request_comment_url = request_comment_url[:1024]
        if requested_at is not None:
            record.requested_at = requested_at
        if actor is not None:
            record.actor = actor[:255]
        record.reconciled = bool(reconciled)
        record.failure_reason = failure_reason[:500] if failure_reason else None
        await self._session.flush()
        return _entry(record)


__all__ = [
    "MergeAutomationReviewRequestStore",
    "ReviewRequestLedgerEntry",
    "STATUS_ABANDONED",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_REQUESTED",
    "build_review_request_key",
]
