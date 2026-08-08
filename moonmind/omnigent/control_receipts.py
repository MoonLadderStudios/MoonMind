"""Durable per-mutation control-receipt store for native Omnigent chat.

Source design: ``docs/Omnigent/OmnigentBridge.md`` §7.3 and
``docs/UI/WorkflowChatPanel.md`` §7 (MoonLadderStudios/MoonMind#3636).

Every mutating native chat/control/approval/terminal/workspace request records a
versioned receipt *before* the provider side effect, keyed by the MoonMind
idempotency key. This module owns that persistence boundary:

* :meth:`OmnigentControlReceiptStore.begin` persists pending intent and reports
  whether the caller is the *first* writer for the idempotency key. A duplicate
  request receives the existing receipt and must NOT re-run the side effect —
  returning the prior normalized result instead (issue #3636 AC8).
* :meth:`OmnigentControlReceiptStore.mark_dispatched` records the dispatch time
  just before crossing the provider boundary.
* :meth:`OmnigentControlReceiptStore.finalize` records the normalized outcome,
  stable reason code, upstream correlation, audit ref, and completion time.
* :meth:`OmnigentControlReceiptStore.reconcile` folds a ``delivery_unknown``
  outcome to a terminal one without duplicating the mutation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api_service.db.models import OmnigentControlReceipt

CONTROL_RECEIPT_SCHEMA_VERSION = 1

# Normalized outcome vocabulary, aligned with ArtifactSessionControlResponse.
RECEIPT_STATUS_PENDING = "pending"
RECEIPT_STATUS_ACCEPTED = "accepted"
RECEIPT_STATUS_COMPLETED = "completed"
RECEIPT_STATUS_REJECTED = "rejected"
RECEIPT_STATUS_FAILED = "failed"
RECEIPT_STATUS_DELIVERY_UNKNOWN = "delivery_unknown"

# Terminal outcomes: a duplicate request returns the stored result and the
# provider side effect must not run again.
TERMINAL_RECEIPT_STATUSES = frozenset(
    {RECEIPT_STATUS_COMPLETED, RECEIPT_STATUS_REJECTED, RECEIPT_STATUS_FAILED}
)


class ControlReceiptConflict(RuntimeError):
    """Raised when a stored receipt disagrees with a duplicate request's identity.

    An idempotency key that is reused for a *different* control operation is a
    caller error, not a safe retry, and must fail fast rather than reuse an
    unrelated receipt.
    """


@dataclass(frozen=True)
class ControlReceiptIntent:
    """Immutable inputs for one durable control receipt."""

    actor_principal: str
    control_type: str
    request_id: str
    idempotency_key: str
    workflow_id: str | None = None
    run_id: str | None = None
    step_execution_id: str | None = None
    agent_run_id: str | None = None
    bridge_session_id: str | None = None
    provider_session_id: str | None = None
    expected_session_epoch: int | None = None
    expected_turn_id: str | None = None
    expected_elicitation_id: str | None = None
    agent_profile_digest: str | None = None
    provider_profile_generation: int | None = None
    launch_policy_ref: str | None = None
    launch_snapshot_ref: str | None = None
    policy_digest: str | None = None


@dataclass(frozen=True)
class ControlReceiptView:
    """Detached, safe snapshot of a persisted receipt for callers."""

    receipt_id: str
    schema_version: int
    control_type: str
    request_id: str
    idempotency_key: str
    status: str
    stable_reason_code: str | None
    upstream_correlation: str | None
    result: dict[str, Any]
    completed_at: datetime | None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RECEIPT_STATUSES


def _view(row: OmnigentControlReceipt) -> ControlReceiptView:
    return ControlReceiptView(
        receipt_id=row.receipt_id,
        schema_version=row.schema_version,
        control_type=row.control_type,
        request_id=row.request_id,
        idempotency_key=row.idempotency_key,
        status=row.status,
        stable_reason_code=row.stable_reason_code,
        upstream_correlation=row.upstream_correlation,
        result=dict(row.result_json or {}),
        completed_at=row.completed_at,
    )


class OmnigentControlReceiptStore:
    """Persistence boundary for durable Omnigent control receipts."""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory

    async def begin(
        self, intent: ControlReceiptIntent
    ) -> tuple[ControlReceiptView, bool]:
        """Persist pending intent, returning ``(receipt, is_new)``.

        ``is_new`` is ``True`` only for the first writer of the idempotency key;
        a duplicate returns the stored receipt with ``is_new=False`` so the
        caller returns the prior result and does not repeat the side effect. A
        reused key with a *different* control type raises
        :class:`ControlReceiptConflict`.
        """

        key = (intent.idempotency_key or "").strip()
        if not key:
            raise ValueError("control receipt requires a non-blank idempotency key")

        async with self._session_factory() as session:
            existing = await self._get_by_key(session, key)
            if existing is not None:
                if existing.control_type != intent.control_type:
                    raise ControlReceiptConflict(
                        "idempotency key reused for a different control type"
                    )
                return _view(existing), False

            now = datetime.now(tz=UTC)
            row = OmnigentControlReceipt(
                receipt_id=f"ocr_{uuid4().hex}",
                schema_version=CONTROL_RECEIPT_SCHEMA_VERSION,
                actor_principal=intent.actor_principal,
                control_type=intent.control_type,
                request_id=intent.request_id,
                idempotency_key=key,
                workflow_id=intent.workflow_id,
                run_id=intent.run_id,
                step_execution_id=intent.step_execution_id,
                agent_run_id=intent.agent_run_id,
                bridge_session_id=intent.bridge_session_id,
                provider_session_id=intent.provider_session_id,
                expected_session_epoch=intent.expected_session_epoch,
                expected_turn_id=intent.expected_turn_id,
                expected_elicitation_id=intent.expected_elicitation_id,
                agent_profile_digest=intent.agent_profile_digest,
                provider_profile_generation=intent.provider_profile_generation,
                launch_policy_ref=intent.launch_policy_ref,
                launch_snapshot_ref=intent.launch_snapshot_ref,
                policy_digest=intent.policy_digest,
                status=RECEIPT_STATUS_PENDING,
                result_json={},
                requested_at=now,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # A concurrent first writer won the unique idempotency key race.
                await session.rollback()
                existing = await self._get_by_key(session, key)
                if existing is None:  # pragma: no cover - defensive
                    raise
                if existing.control_type != intent.control_type:
                    raise ControlReceiptConflict(
                        "idempotency key reused for a different control type"
                    )
                return _view(existing), False
            await session.refresh(row)
            return _view(row), True

    async def mark_dispatched(self, receipt_id: str) -> ControlReceiptView:
        """Record the dispatch time just before the provider side effect."""

        async with self._session_factory() as session:
            row = await self._require(session, receipt_id)
            if row.dispatched_at is None:
                row.dispatched_at = datetime.now(tz=UTC)
                await session.commit()
                await session.refresh(row)
            return _view(row)

    async def finalize(
        self,
        receipt_id: str,
        *,
        status: str,
        stable_reason_code: str | None = None,
        upstream_correlation: str | None = None,
        result: dict[str, Any] | None = None,
        audit_artifact_ref: str | None = None,
        completed_at: datetime | None = None,
    ) -> ControlReceiptView:
        """Record the normalized outcome for a receipt.

        Terminal outcomes are immutable: once a receipt is
        completed/rejected/failed, a subsequent finalize is a no-op that returns
        the stored result (so a duplicate cannot rewrite an authoritative
        outcome).
        """

        async with self._session_factory() as session:
            row = await self._require(session, receipt_id)
            if row.status in TERMINAL_RECEIPT_STATUSES:
                return _view(row)
            row.status = status
            row.stable_reason_code = stable_reason_code
            if upstream_correlation is not None:
                row.upstream_correlation = upstream_correlation
            if result is not None:
                row.result_json = dict(result)
            if audit_artifact_ref is not None:
                row.audit_artifact_ref = audit_artifact_ref
            if status in TERMINAL_RECEIPT_STATUSES:
                row.completed_at = completed_at or datetime.now(tz=UTC)
            await session.commit()
            await session.refresh(row)
            return _view(row)

    async def reconcile(
        self,
        receipt_id: str,
        *,
        status: str,
        stable_reason_code: str | None = None,
        upstream_correlation: str | None = None,
        result: dict[str, Any] | None = None,
        audit_artifact_ref: str | None = None,
    ) -> ControlReceiptView:
        """Fold a ``delivery_unknown`` receipt to a terminal outcome.

        Reconciliation only advances an ambiguous (``delivery_unknown`` or
        ``pending``) receipt; an already terminal receipt is returned unchanged.
        It performs no provider mutation of its own — the caller has verified the
        real outcome — so the mutation is never duplicated (issue #3636 §5).
        """

        async with self._session_factory() as session:
            row = await self._require(session, receipt_id)
            if row.status in TERMINAL_RECEIPT_STATUSES:
                return _view(row)
            row.status = status
            row.stable_reason_code = stable_reason_code
            if upstream_correlation is not None:
                row.upstream_correlation = upstream_correlation
            if result is not None:
                row.result_json = dict(result)
            if audit_artifact_ref is not None:
                row.audit_artifact_ref = audit_artifact_ref
            if status in TERMINAL_RECEIPT_STATUSES:
                row.completed_at = datetime.now(tz=UTC)
            await session.commit()
            await session.refresh(row)
            return _view(row)

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> ControlReceiptView | None:
        key = (idempotency_key or "").strip()
        if not key:
            return None
        async with self._session_factory() as session:
            row = await self._get_by_key(session, key)
            return _view(row) if row is not None else None

    async def _get_by_key(
        self, session: Any, idempotency_key: str
    ) -> OmnigentControlReceipt | None:
        result = await session.execute(
            select(OmnigentControlReceipt).where(
                OmnigentControlReceipt.idempotency_key == idempotency_key
            )
        )
        return result.scalars().first()

    async def _require(
        self, session: Any, receipt_id: str
    ) -> OmnigentControlReceipt:
        row = await session.get(OmnigentControlReceipt, receipt_id)
        if row is None:
            raise ControlReceiptConflict(f"unknown control receipt: {receipt_id}")
        return row


__all__ = [
    "CONTROL_RECEIPT_SCHEMA_VERSION",
    "RECEIPT_STATUS_PENDING",
    "RECEIPT_STATUS_ACCEPTED",
    "RECEIPT_STATUS_COMPLETED",
    "RECEIPT_STATUS_REJECTED",
    "RECEIPT_STATUS_FAILED",
    "RECEIPT_STATUS_DELIVERY_UNKNOWN",
    "TERMINAL_RECEIPT_STATUSES",
    "ControlReceiptConflict",
    "ControlReceiptIntent",
    "ControlReceiptView",
    "OmnigentControlReceiptStore",
]
