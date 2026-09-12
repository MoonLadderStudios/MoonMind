"""Activity-owned, durable issue claim receipts; GitHub labels remain advisory.

One logical workflow owns one pinned issue. The database serializes retries of
that owner, while remote comments expose competing deployments. No timestamp
or caller-supplied attempt identifier grants ownership.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from temporalio import activity

from api_service.db.models import GitHubIssueClaim
from moonmind.workflows.temporal.github_issue_attempts import (
    parse_attempt_comment,
    stable_attempt_marker,
)


class ActiveIssueClaimConflict(ValueError):
    """Another durable owner reserved this candidate before any local write."""


def claim_owner(context: Mapping[str, Any] | None = None) -> str:
    """The owning Activity supplies identity, never a tool's authored inputs."""
    if activity.in_activity():
        info = activity.info()
        return f"{info.namespace}/{info.workflow_id}"
    # Direct trusted executors and isolated tests supply their durable owner.
    owner = str((context or {}).get("execution_owner") or "").strip()
    if not owner:
        raise ValueError("Issue mutation requires a durable execution owner")
    return owner


@dataclass(frozen=True)
class ClaimReceipt:
    owner: str
    repository: str
    issue_number: int
    attempt_id: str
    actor_id: str
    comment_body: str
    comment_id: str | None
    confirmed: bool
    pending_comment_body: str | None
    released: bool
    announcement_started: bool = False
    finalization_json: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row):
        return cls(**{name: getattr(row, name) for name in cls.__dataclass_fields__})

    def handoff(self) -> dict[str, Any]:
        parsed = parse_attempt_comment(self.comment_body)
        return {
            "attemptId": self.attempt_id,
            "existingAttemptCommentId": self.comment_id,
            "admittedIdentity": {
                "repository": self.repository,
                "issueNumber": self.issue_number,
                "predecessorAttemptId": (
                    parsed.handoff.predecessor_attempt_id if parsed.handoff else ""
                ),
            },
        }


class IssueClaimStore:
    def __init__(self, session_factory=None):
        if session_factory is None:
            from api_service.db.base import async_session_maker

            session_factory = async_session_maker
        self.sessions = session_factory

    async def get(self, owner: str) -> ClaimReceipt | None:
        async with self.sessions() as session:
            row = await session.get(GitHubIssueClaim, owner)
            return ClaimReceipt.from_row(row) if row else None

    async def active_for_issue(
        self, repository: str, issue_number: int
    ) -> ClaimReceipt | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(GitHubIssueClaim).where(
                        GitHubIssueClaim.repository == repository.casefold(),
                        GitHubIssueClaim.issue_number == issue_number,
                        GitHubIssueClaim.released.is_(False),
                    )
                )
            ).scalar_one_or_none()
            return ClaimReceipt.from_row(row) if row else None

    async def for_execution(self, owner: str) -> ClaimReceipt | None:
        """Resolve a controlling parent only from Temporal's recorded ancestry."""
        own = await self.get(owner)
        if own is not None or not activity.in_activity():
            return own
        from temporalio.api.common.v1 import WorkflowExecution
        from temporalio.api.workflowservice.v1 import (
            DescribeNamespaceRequest,
            DescribeWorkflowExecutionRequest,
        )

        from moonmind.config.settings import settings
        from moonmind.workflows.temporal.client import get_temporal_client

        info = activity.info()
        client = await get_temporal_client(settings.temporal.address, info.namespace)
        execution = WorkflowExecution(
            workflow_id=info.workflow_id, run_id=info.workflow_run_id
        )
        namespace_id = None
        for _ in range(16):
            described = await client.workflow_service.describe_workflow_execution(
                DescribeWorkflowExecutionRequest(
                    namespace=info.namespace, execution=execution
                )
            )
            parent = described.workflow_execution_info
            if not parent.HasField("parent_execution"):
                return None
            if parent.parent_namespace_id:
                if namespace_id is None:
                    namespace_id = (
                        await client.workflow_service.describe_namespace(
                            DescribeNamespaceRequest(namespace=info.namespace)
                        )
                    ).namespace_info.id
                if parent.parent_namespace_id != namespace_id:
                    raise ValueError(
                        "claim_namespace_changed: cross-namespace child requires explicit ownership transfer"
                    )
            execution = parent.parent_execution
            receipt = await self.get(f"{info.namespace}/{execution.workflow_id}")
            if receipt is not None:
                return receipt
        raise ValueError(
            "claim_lineage_limit: controlling workflow ancestry exceeds the bounded lookup"
        )

    async def prepare_comment_update(self, owner: str, body: str) -> None:
        async with self.locked(owner) as row:
            if row.pending_comment_body and row.pending_comment_body != body:
                raise ValueError(
                    "claim_update_pending: reconcile the previous comment update first"
                )
            row.pending_comment_body = body

    async def start_announcement(self, owner: str, attempt_id: str) -> None:
        """Commit POST intent before crossing GitHub's acknowledgement boundary."""
        async with self.locked(owner) as row:
            if row.attempt_id != attempt_id:
                raise ActiveIssueClaimConflict(
                    "claim_changed: stale reservation cannot announce"
                )
            row.announcement_started = True

    async def abandon_unannounced(self, owner: str, attempt_id: str) -> bool:
        """Release a reservation only while no external mutation was authorized."""
        async with self.sessions() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(GitHubIssueClaim)
                        .where(GitHubIssueClaim.owner == owner)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None:
                    return True
                if (
                    row.attempt_id != attempt_id
                    or row.announcement_started
                    or row.comment_id
                    or row.confirmed
                    or row.pending_comment_body
                ):
                    return False
                await session.delete(row)
                return True

    async def record_finalization(
        self, owner: str, phase: str, value: dict[str, Any]
    ) -> None:
        """Pin the release plan before mutation and its observed result afterwards."""
        if phase not in {"plan", "result"}:
            raise ValueError("Unknown claim finalization phase")
        async with self.locked(owner) as row:
            receipts = dict(row.finalization_json or {})
            if phase in receipts and receipts[phase] != value:
                raise ValueError(
                    "claim_finalization_conflict: release evidence is immutable"
                )
            receipts[phase] = value
            row.finalization_json = receipts

    async def confirm_comment_update(self, owner: str, body: str) -> None:
        async with self.locked(owner) as row:
            if row.pending_comment_body == body:
                row.comment_body = body
                row.pending_comment_body = None
                parsed = parse_attempt_comment(body)
                if parsed.handoff and parsed.handoff.activity == "released":
                    row.released = True

    async def prepare(
        self,
        *,
        owner: str,
        repository: str,
        issue_number: int,
        attempt_id: str,
        actor_id: str,
        comment_body: str,
    ) -> ClaimReceipt:
        async with self.sessions() as session:
            row = GitHubIssueClaim(
                owner=owner,
                repository=repository.casefold(),
                issue_number=issue_number,
                attempt_id=attempt_id,
                actor_id=actor_id,
                comment_body=comment_body,
                confirmed=False,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
            row = await session.get(GitHubIssueClaim, owner)
            if row is None:
                raise ActiveIssueClaimConflict(
                    "active_attempt_conflict: another durable owner selected this issue"
                )
            if (row.repository, row.issue_number) != (
                repository.casefold(),
                issue_number,
            ):
                raise ValueError(
                    "substitution_denied: execution already selected a different issue"
                )
            return ClaimReceipt.from_row(row)

    @asynccontextmanager
    async def locked(self, owner: str):
        async with self.sessions() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(GitHubIssueClaim)
                        .where(GitHubIssueClaim.owner == owner)
                        .with_for_update()
                    )
                ).scalar_one()
                yield row


def inspect_claim_comments(
    receipt: ClaimReceipt, comments: list[dict[str, Any]]
) -> str | None:
    """Validate own provenance and reject active contenders or conflicting copies."""
    own = []
    contenders = False
    observed_release = receipt.released
    marker = stable_attempt_marker(receipt.attempt_id)
    for comment in comments:
        body = str(comment.get("body") or "")
        if marker in body:
            if str(
                (comment.get("user") or {}).get("id") or ""
            ) != receipt.actor_id or body not in {
                receipt.comment_body,
                receipt.pending_comment_body,
            }:
                raise ValueError(
                    "claim_evidence_conflict: own comment provenance/content changed"
                )
            own.append(str(comment.get("id") or ""))
            parsed = parse_attempt_comment(body)
            observed_release = bool(
                parsed.handoff and parsed.handoff.activity == "released"
            )
            continue
        parsed = parse_attempt_comment(body)
        if parsed.status == "no_marker":
            continue
        if parsed.handoff is None or parsed.handoff.activity in {
            "preparing",
            "active",
            "awaiting-review",
            "releasing",
            "attention",
        }:
            contenders = True
    own = list(dict.fromkeys(own))
    if len(own) > 1:
        from moonmind.observability.metrics import increment_counter

        increment_counter(
            "moonmind_duplicate_effect_observations", labels={"component": "worker"}
        )
    if len(own) > 1 or (receipt.comment_id and own != [receipt.comment_id]):
        raise ValueError(
            "claim_evidence_conflict: own receipt is missing or duplicated"
        )
    if contenders and not observed_release:
        raise ActiveIssueClaimConflict(
            "active_attempt_conflict: another unresolved attempt is present"
        )
    return own[0] if own else None


async def verify_claim(receipt: ClaimReceipt, service) -> bool:
    listed = await service.list_issue_comments(
        repo=receipt.repository, issue_number=receipt.issue_number
    )
    if not listed.get("ok") or not isinstance(listed.get("comments"), list):
        raise ValueError("read_failure: complete claim comment evidence is unavailable")
    return bool(inspect_claim_comments(receipt, listed["comments"]))


async def reconcile_claim_comment(
    store: IssueClaimStore, receipt: ClaimReceipt, service
) -> ClaimReceipt:
    """Read back a pending write without issuing another external effect."""
    listed = await service.list_issue_comments(
        repo=receipt.repository, issue_number=receipt.issue_number
    )
    if not listed.get("ok") or not isinstance(listed.get("comments"), list):
        raise ValueError("read_failure: claim comment update cannot be reconciled")
    comment_id = inspect_claim_comments(receipt, listed["comments"])
    if not comment_id:
        raise ValueError("claim_evidence_conflict: announced comment disappeared")
    observed = next(
        item for item in listed["comments"] if str(item.get("id")) == comment_id
    )
    await store.confirm_comment_update(receipt.owner, str(observed.get("body") or ""))
    return await store.get(receipt.owner)


async def publish_claim_comment(
    store: IssueClaimStore, receipt: ClaimReceipt, service, body: str
) -> str:
    """Persist an update intent, reconcile its response, then commit its receipt."""
    # Finish any acknowledged-by-GitHub update before accepting a new intent.
    listed = await service.list_issue_comments(
        repo=receipt.repository, issue_number=receipt.issue_number
    )
    if not listed.get("ok") or not isinstance(listed.get("comments"), list):
        raise ValueError("read_failure: claim comment update cannot be reconciled")
    comment_id = inspect_claim_comments(receipt, listed["comments"])
    if not comment_id:
        raise ValueError("claim_evidence_conflict: announced comment disappeared")
    observed = next(
        item for item in listed["comments"] if str(item.get("id")) == comment_id
    )
    await store.confirm_comment_update(receipt.owner, str(observed.get("body") or ""))
    await store.prepare_comment_update(receipt.owner, body)
    if observed.get("body") != body:
        result = await service.update_issue_comment(
            repo=receipt.repository, comment_id=int(comment_id), body=body
        )
        if not result.get("ok") and result.get("reasonCode") != "outcome_unknown":
            raise ValueError(
                "claim_update_failed: GitHub rejected the owned comment update"
            )
    current = await store.get(receipt.owner)
    listed = await service.list_issue_comments(
        repo=receipt.repository, issue_number=receipt.issue_number
    )
    if not listed.get("ok") or not isinstance(listed.get("comments"), list):
        raise ValueError("read_failure: claim comment update is unconfirmed")
    inspect_claim_comments(current, listed["comments"])
    if not any(
        str(item.get("id")) == comment_id and item.get("body") == body
        for item in listed["comments"]
    ):
        raise ValueError(
            "claim_update_pending: GitHub has not confirmed the intended comment"
        )
    await store.confirm_comment_update(receipt.owner, body)
    return comment_id
