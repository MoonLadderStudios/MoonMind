"""Durable Omnigent idempotency mapping service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import OmnigentExternalRun
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

FIRST_MESSAGE_NOT_PREPARED = "not_prepared"
FIRST_MESSAGE_PREPARED = "prepared"
FIRST_MESSAGE_POSTING = "posting"
FIRST_MESSAGE_POSTED = "posted"
FIRST_MESSAGE_TERMINAL = "terminal"


class OmnigentIdempotencyError(RuntimeError):
    """Base error for invalid durable retry state."""


class OmnigentDigestMismatchError(OmnigentIdempotencyError):
    """Raised when an idempotency key is reused for different first-message text."""


class OmnigentRunStore:
    """Persistence boundary for Omnigent external run rows."""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory

    async def get_or_create(
        self,
        *,
        request: AgentExecutionRequest,
        endpoint_ref: str,
        agent_id: str | None,
        agent_name: str | None,
        target_metadata: dict[str, Any],
    ) -> OmnigentExternalRun:
        async with self._session_factory() as session:
            row = await self._get(session, request.idempotency_key)
            if row is None:
                row = OmnigentExternalRun(
                    idempotency_key=request.idempotency_key,
                    moonmind_workflow_id=_workflow_id(request),
                    moonmind_agent_run_id=_agent_run_id(request),
                    correlation_id=request.correlation_id,
                    omnigent_endpoint_ref=endpoint_ref,
                    omnigent_agent_id=agent_id,
                    omnigent_agent_name=agent_name,
                    target_metadata=target_metadata,
                    status="active",
                    artifact_refs={},
                    terminal_refs={},
                )
                session.add(row)
                await session.commit()
            else:
                changed = False
                if row.omnigent_agent_id is None and agent_id is not None:
                    row.omnigent_agent_id = agent_id
                    changed = True
                if row.omnigent_agent_name is None and agent_name is not None:
                    row.omnigent_agent_name = agent_name
                    changed = True
                if not row.target_metadata:
                    row.target_metadata = target_metadata
                    changed = True
                if changed:
                    await session.commit()
            await session.refresh(row)
            return _detached(row)

    async def attach_session(self, idempotency_key: str, session_id: str) -> OmnigentExternalRun:
        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            if row.omnigent_session_id and row.omnigent_session_id != session_id:
                raise OmnigentIdempotencyError(
                    "idempotency key already maps to a different Omnigent session"
                )
            row.omnigent_session_id = session_id
            await session.commit()
            await session.refresh(row)
            return _detached(row)

    async def mark_prepared(
        self, idempotency_key: str, *, digest: str, marker: str
    ) -> OmnigentExternalRun:
        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            if row.first_message_digest and row.first_message_digest != digest:
                raise OmnigentDigestMismatchError(
                    "idempotencyKey reused with a different first-message digest"
                )
            if row.first_message_state == FIRST_MESSAGE_NOT_PREPARED:
                row.first_message_state = FIRST_MESSAGE_PREPARED
            row.first_message_digest = digest
            row.first_message_marker = marker
            await session.commit()
            await session.refresh(row)
            return _detached(row)

    async def mark_posting(self, idempotency_key: str) -> OmnigentExternalRun:
        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            row.first_message_state = FIRST_MESSAGE_POSTING
            row.first_message_post_attempted_at = datetime.now(tz=UTC)
            await session.commit()
            await session.refresh(row)
            return _detached(row)

    async def mark_posted(
        self,
        idempotency_key: str,
        *,
        response: dict[str, Any] | None = None,
        item_id: str | None = None,
    ) -> OmnigentExternalRun:
        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            row.first_message_state = FIRST_MESSAGE_POSTED
            row.first_message_posted_at = datetime.now(tz=UTC)
            response = response or {}
            row.first_message_pending_id = _string_or_none(response.get("pending_id"))
            row.first_message_item_id = item_id or _string_or_none(response.get("item_id"))
            await session.commit()
            await session.refresh(row)
            return _detached(row)

    async def mark_terminal(
        self,
        idempotency_key: str,
        *,
        status: str,
        terminal_refs: dict[str, Any] | None = None,
    ) -> OmnigentExternalRun:
        async with self._session_factory() as session:
            row = await self._require(session, idempotency_key)
            row.status = status
            row.first_message_state = FIRST_MESSAGE_TERMINAL
            if terminal_refs:
                row.terminal_refs = terminal_refs
            await session.commit()
            await session.refresh(row)
            return _detached(row)

    async def _get(
        self, session: AsyncSession, idempotency_key: str
    ) -> OmnigentExternalRun | None:
        result = await session.execute(
            select(OmnigentExternalRun).where(
                OmnigentExternalRun.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one_or_none()

    async def _require(self, session: AsyncSession, idempotency_key: str) -> OmnigentExternalRun:
        row = await self._get(session, idempotency_key)
        if row is None:
            raise OmnigentIdempotencyError("missing Omnigent idempotency row")
        return row


def _workflow_id(request: AgentExecutionRequest) -> str:
    if request.step_execution is not None:
        return request.step_execution.workflow_id
    return request.correlation_id


def _agent_run_id(request: AgentExecutionRequest) -> str:
    if request.step_execution is not None:
        return request.step_execution.run_id
    return request.correlation_id


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _detached(row: OmnigentExternalRun) -> OmnigentExternalRun:
    return row


__all__ = [
    "FIRST_MESSAGE_NOT_PREPARED",
    "FIRST_MESSAGE_POSTED",
    "FIRST_MESSAGE_POSTING",
    "FIRST_MESSAGE_PREPARED",
    "FIRST_MESSAGE_TERMINAL",
    "OmnigentDigestMismatchError",
    "OmnigentIdempotencyError",
    "OmnigentRunStore",
]
