"""Persistence helpers for task proposal entities."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from moonmind.workflows.task_proposals import models


class TaskProposalNotFoundError(RuntimeError):
    """Raised when a proposal cannot be found for the requested id."""


class TaskProposalRepository:
    """Repository wrapper for creating and retrieving task proposals."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_proposal(
        self,
        *,
        title: str,
        summary: str,
        category: str | None,
        tags: list[str],
        repository: str,
        task_create_request: dict[str, object],
        proposed_by_worker_id: str | None,
        proposed_by_user_id: UUID | None,
        origin_source: models.TaskProposalOriginSource,
        origin_id: UUID | None,
        origin_metadata: dict[str, object],
        dedup_key: str,
        dedup_hash: str,
        review_priority: models.TaskProposalReviewPriority,
    ) -> models.TaskProposal:
        entity = models.TaskProposal(
            title=title,
            summary=summary,
            category=category,
            tags=tags,
            repository=repository,
            task_create_request=task_create_request,
            proposed_by_worker_id=proposed_by_worker_id,
            proposed_by_user_id=proposed_by_user_id,
            origin_source=origin_source,
            origin_id=origin_id,
            origin_metadata=origin_metadata,
            dedup_key=dedup_key,
            dedup_hash=dedup_hash,
            review_priority=review_priority,
        )
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def list_proposals(
        self,
        *,
        status: models.TaskProposalStatus | None = None,
        category: str | None = None,
        repository: str | None = None,
        origin_source: models.TaskProposalOriginSource | None = None,
        cursor: tuple[datetime, UUID] | None = None,
        limit: int = 50,
        now: datetime | None = None,
        include_snoozed: bool = False,
        only_snoozed: bool = False,
    ) -> tuple[list[models.TaskProposal], bool]:
        now = now or datetime.now(UTC)
        stmt: Select[tuple[models.TaskProposal]] = (
            select(models.TaskProposal)
            .order_by(
                models.TaskProposal.created_at.desc(),
                models.TaskProposal.id.desc(),
            )
            .limit(limit + 1)
        )
        if status is not None:
            stmt = stmt.where(models.TaskProposal.status == status)
        if category:
            stmt = stmt.where(models.TaskProposal.category == category)
        if repository:
            stmt = stmt.where(models.TaskProposal.repository == repository)
        if origin_source is not None:
            stmt = stmt.where(models.TaskProposal.origin_source == origin_source)
        if cursor is not None:
            cursor_time, cursor_id = cursor
            stmt = stmt.where(
                or_(
                    models.TaskProposal.created_at < cursor_time,
                    and_(
                        models.TaskProposal.created_at == cursor_time,
                        models.TaskProposal.id < cursor_id,
                    ),
                )
            )
        if only_snoozed:
            stmt = stmt.where(
                models.TaskProposal.snoozed_until.is_not(None),
                models.TaskProposal.snoozed_until > now,
            )
        elif not include_snoozed:
            stmt = stmt.where(
                or_(
                    models.TaskProposal.snoozed_until.is_(None),
                    models.TaskProposal.snoozed_until <= now,
                )
            )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        has_more = len(rows) > limit
        return rows[:limit], has_more

    async def get_proposal(self, proposal_id: UUID) -> models.TaskProposal | None:
        return await self._session.get(models.TaskProposal, proposal_id)

    async def get_proposal_for_update(self, proposal_id: UUID) -> models.TaskProposal:
        stmt: Select[tuple[models.TaskProposal]] = (
            select(models.TaskProposal)
            .where(models.TaskProposal.id == proposal_id)
            .with_for_update()
        )
        result = await self._session.execute(stmt)
        proposal = result.scalar_one_or_none()
        if proposal is None:
            raise TaskProposalNotFoundError(str(proposal_id))
        return proposal

    async def list_similar(
        self, *, proposal: models.TaskProposal, limit: int = 10
    ) -> list[models.TaskProposal]:
        stmt: Select[tuple[models.TaskProposal]] = (
            select(models.TaskProposal)
            .where(
                models.TaskProposal.dedup_hash == proposal.dedup_hash,
                models.TaskProposal.id != proposal.id,
                models.TaskProposal.status == models.TaskProposalStatus.OPEN,
            )
            .order_by(models.TaskProposal.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def expire_snoozed(self, *, now: datetime | None = None) -> int:
        """Clear snoozed flags for proposals whose snooze has elapsed."""

        now = now or datetime.now(UTC)
        stmt = (
            update(models.TaskProposal)
            .where(
                models.TaskProposal.snoozed_until.is_not(None),
                models.TaskProposal.snoozed_until <= now,
            )
            .values(snoozed_until=None, snoozed_by_user_id=None, snooze_note=None)
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)

    async def update_priority(
        self,
        *,
        proposal: models.TaskProposal,
        priority: models.TaskProposalReviewPriority,
        user_id: UUID,
    ) -> models.TaskProposal:
        proposal.review_priority = priority
        proposal.decided_by_user_id = user_id
        await self._session.flush()
        return proposal

    async def snooze(
        self,
        *,
        proposal: models.TaskProposal,
        until: datetime,
        user_id: UUID,
        note: str | None,
    ) -> models.TaskProposal:
        proposal.snoozed_until = until
        proposal.snoozed_by_user_id = user_id
        proposal.snooze_note = note
        history = list(proposal.snooze_history or [])
        history.append(
            {
                "until": until.isoformat(),
                "note": note,
                "snoozedBy": str(user_id),
            }
        )
        proposal.snooze_history = history[-20:]
        await self._session.flush()
        return proposal

    async def unsnooze(
        self,
        *,
        proposal: models.TaskProposal,
        user_id: UUID,
    ) -> models.TaskProposal:
        proposal.snoozed_until = None
        proposal.snoozed_by_user_id = user_id
        proposal.snooze_note = None
        await self._session.flush()
        return proposal

    async def log_notification(
        self,
        *,
        proposal_id: UUID,
        category: str,
        target: str,
        status: str,
        error: str | None = None,
    ) -> models.TaskProposalNotification:
        record = models.TaskProposalNotification(
            proposal_id=proposal_id,
            category=category,
            target=target,
            status=status,
            error=error,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def has_notification(self, *, proposal_id: UUID, target: str) -> bool:
        stmt = select(models.TaskProposalNotification.id).where(
            models.TaskProposalNotification.proposal_id == proposal_id,
            models.TaskProposalNotification.target == target,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def commit(self) -> None:
        await self._session.commit()

    async def refresh(self, entity: models.TaskProposal) -> models.TaskProposal:
        await self._session.refresh(entity)
        return entity


__all__ = [
    "TaskProposalRepository",
    "TaskProposalNotFoundError",
]
