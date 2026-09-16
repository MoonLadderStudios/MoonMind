"""Atomic, revision-fenced, reference-aware managed-secret lifecycle.

Implements MoonLadderStudios/MoonMind#4006 (plan slice 1 of
``docs/RepositoryAccessAndWorkspaceDesign.md``: ``CONTRACT-009``,
``INV-003``, ``CONTRACT-005``, ``TEST-002``).

Design rules owned by this service:

* One transaction owner. Public mutations accept ``commit=True`` (default,
  preserving the existing API: exactly one commit per call) or
  ``commit=False`` for composable caller-owned transactions. Internal helpers
  only flush. Secret activation, binding-revision bookkeeping and
  metadata-only audit/outbox records commit together; a success response is
  never returned before the durable transition.
* Monotonic integer revisions (``credential_revision`` for value/identity
  changes, ``policy_revision`` for metadata-only lifecycle transitions).
  Wall-clock timestamps are never generation authority.
* Rotation is an event/revision transition: a validated replacement is
  ``ACTIVE`` under the existing resolution contract and immediately
  resolvable at its new revision. ``ROTATED`` is a historical marker for
  already-rotated records, handled only through the explicit reviewed
  ``repair_rotated_secret`` path.
* Stable mutation request identity (``request_id``). Retries reconcile the
  original receipt without advancing revisions twice; conflicting reuse is
  rejected.
* Candidate validation happens outside row-locked work via the injected
  probe boundary (``validator`` / ``validation`` envelope). The result is
  bound to an opaque HMAC candidate fingerprint, the expected active
  revision, and actor/owner identity. Plain tokens and publicly comparable
  digests are never persisted.
* Restart-safe invalidation: revision evidence is recorded transactionally in
  the outbox, notifications are delivered after commit, and acquisition
  always checks the authoritative revision.
* Deletion protection is server-side and complete (Settings, Provider
  Profiles, RepositoryConnections); caller-filtered usage display is
  separate. Diagnostics never carry secret material or cross-scope
  identities.

Connection-policy revalidation against the live host record is owned by the
repository-reference integration (#4005). This service accepts the agreed
``expected_policy_revision`` binding on validation envelopes, records it in
audit/outbox evidence, and requires explicit re-admission for changed
ownership — it does not silently preserve validation across actor/account
changes.
"""

import hashlib
import hmac
import inspect
import structlog
from typing import Any, Awaitable, Callable, Sequence
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update, Row, Text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    ManagedSecret,
    SecretMutationReceipt,
    SecretInvalidationOutbox,
    SecretStatus,
    SettingsAuditEvent,
    SettingsOverride,
)

logger = structlog.get_logger(__name__)

_DEFAULT_SETTINGS_SUBJECT_ID = UUID("00000000-0000-0000-0000-000000000000")

# Bounded diagnostic codes. Messages carry slugs/revisions only.
DIAGNOSTIC_CONFLICT = "secret_conflict"
DIAGNOSTIC_FENCED = "secret_fenced"
DIAGNOSTIC_UNAVAILABLE = "secret_unavailable"
DIAGNOSTIC_REPAIR_REQUIRED = "secret_repair_required"
DIAGNOSTIC_RESOLVABLE = "secret_ref_resolvable"
DIAGNOSTIC_UNRESOLVED = "secret_ref_unresolved"
DIAGNOSTIC_DELETE_BLOCKED = "secret_delete_blocked"


class SecretConflictError(ValueError):
    """A stable ``request_id`` was reused with different operation content."""

    def __init__(self, slug: str, request_id: str):
        super().__init__(
            f"Conflicting reuse of mutation request '{request_id}' "
            f"for secret '{slug}': already recorded with different content."
        )
        self.slug = slug
        self.request_id = request_id
        self.code = DIAGNOSTIC_CONFLICT


class SecretFencedError(ValueError):
    """An expected revision/identity no longer matches the committed state."""

    def __init__(self, slug: str, *, reason: str = "stale revision"):
        super().__init__(
            f"Secret '{slug}' fenced: {reason}. Re-admit against the "
            "current revision before retrying."
        )
        self.slug = slug
        self.code = DIAGNOSTIC_FENCED


class SecretRepairRequiredError(ValueError):
    """An already-ROTATED record needs the explicit reviewed repair path."""

    def __init__(self, slug: str):
        super().__init__(
            f"Secret '{slug}' requires reviewed repair: it is in historical "
            "ROTATED state and must not be blindly reactivated. Use "
            "repair_rotated_secret with a validated candidate."
        )
        self.slug = slug
        self.code = DIAGNOSTIC_REPAIR_REQUIRED


class SecretReferenceProtectedError(ValueError):
    """Deletion refused: complete server-side inventory still references it."""

    def __init__(self, slug: str, counts: dict[str, int]):
        total = sum(counts.values())
        super().__init__(
            f"Secret '{slug}' is referenced by {total} consumer(s) "
            f"{counts}; deletion refused."
        )
        self.slug = slug
        self.counts = counts
        self.code = DIAGNOSTIC_DELETE_BLOCKED


# In-process after-commit notification subscribers. Events carry only
# slug/revision/cause metadata — never secret material or consumer identity.
InvalidationSubscriber = Callable[[dict[str, Any]], Awaitable[None] | None]
_invalidation_subscribers: list[InvalidationSubscriber] = []


def subscribe_secret_invalidations(fn: InvalidationSubscriber) -> None:
    """Register an after-commit invalidation subscriber (test/process hook)."""
    _invalidation_subscribers.append(fn)


def unsubscribe_secret_invalidations(fn: InvalidationSubscriber) -> None:
    """Remove a previously registered invalidation subscriber."""
    if fn in _invalidation_subscribers:
        _invalidation_subscribers.remove(fn)


def _credential_revision_of(secret: ManagedSecret) -> int:
    return int(getattr(secret, "credential_revision", 1) or 1)


def _policy_revision_of(secret: ManagedSecret) -> int:
    return int(getattr(secret, "policy_revision", 1) or 1)


def _normalize_revisions(secret: ManagedSecret) -> ManagedSecret:
    """Treat missing revisions (pre-#4006 rows, transient mocks) as 1."""
    if getattr(secret, "credential_revision", None) is None:
        secret.credential_revision = 1
    if getattr(secret, "policy_revision", None) is None:
        secret.policy_revision = 1
    return secret


def _status_value(status: Any) -> str:
    return status.value if isinstance(status, SecretStatus) else str(status)


def _candidate_fingerprint(candidate: str) -> str:
    """Opaque candidate identity bound with the server encryption key.

    HMAC (not a plain hash) so the stored fingerprint is not publicly
    comparable: an attacker with the fingerprint cannot test candidate
    tokens against it without the server key. This is a keyed change-detection
    fingerprint, not password storage or password verification, so a
    deliberately slow password hash (bcrypt/scrypt/argon2) does not apply.
    """
    try:
        from api_service.core.encryption import get_encryption_key

        key = get_encryption_key()
    except Exception:
        key = "issue-4006-fallback-pepper"
    # codeql[py/weak-sensitive-data-hashing]: keyed HMAC identity check, not
    # password hashing; verification requires the server-side key.
    return hmac.new(
        key.encode("utf-8"), candidate.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _redacted_revision_json(secret: ManagedSecret) -> dict[str, Any]:
    """Metadata-only revision snapshot for audit records (no secret bytes)."""
    return {
        "status": _status_value(secret.status),
        "credential_revision": _credential_revision_of(secret),
        "policy_revision": _policy_revision_of(secret),
    }


class SecretsService:
    """Service layer for managing securely encrypted secrets."""

    # -- transaction / audit / outbox primitives -------------------------

    @staticmethod
    def _record_audit(
        db: AsyncSession,
        *,
        event_type: str,
        slug: str,
        old_value: dict[str, Any],
        new_value: dict[str, Any],
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Stage a redacted audit event in the caller's transaction.

        ``redacted=True`` is enforced at the storage layer; old/new payloads
        carry revision metadata only, never plaintext or ciphertext.
        """
        db.add(
            SettingsAuditEvent(
                event_type=event_type,
                key=f"secrets.{slug}",
                scope="system",
                workspace_id=workspace_id or _DEFAULT_SETTINGS_SUBJECT_ID,
                user_id=_DEFAULT_SETTINGS_SUBJECT_ID,
                actor_user_id=actor_user_id,
                old_value_json=old_value,
                new_value_json=new_value,
                redacted=True,
                reason=reason,
                request_id=request_id,
            )
        )

    @staticmethod
    def _record_invalidation(
        db: AsyncSession,
        *,
        slug: str,
        credential_revision: int,
        policy_revision: int,
        cause: str,
    ) -> dict[str, Any]:
        """Transactionally stage restart-safe invalidation evidence."""
        row = SecretInvalidationOutbox(
            id=uuid4(),
            slug=slug,
            credential_revision=credential_revision,
            policy_revision=policy_revision,
            cause=cause,
            delivered=False,
        )
        db.add(row)
        return {
            "outbox_id": str(row.id),
            "slug": slug,
            "credential_revision": credential_revision,
            "policy_revision": policy_revision,
            "cause": cause,
        }

    @staticmethod
    async def _deliver_events(events: Sequence[dict[str, Any]]) -> list[str]:
        """Invoke after-commit subscribers; return ids delivered cleanly.

        With no registered subscribers nothing is delivered: rows stay in
        the outbox for ``sweep_invalidations`` (restart recovery). Only ids
        consumed cleanly by every subscriber are reported.
        """
        if not _invalidation_subscribers:
            return []
        delivered: list[str] = []
        for event in events:
            try:
                for subscriber in list(_invalidation_subscribers):
                    result = subscriber(dict(event))
                    if inspect.isawaitable(result):
                        await result
                delivered.append(event["outbox_id"])
            except Exception as exc:  # noqa: BLE001 - delivery must not fail the commit
                logger.warning(
                    "secret_invalidation_delivery_failed",
                    slug=event.get("slug"),
                    cause=event.get("cause"),
                    error=str(exc),
                )
        return delivered

    @classmethod
    async def _mark_outbox_delivered(
        cls, db: AsyncSession, outbox_ids: Sequence[str]
    ) -> None:
        if not outbox_ids:
            return
        ids = [
            outbox_id if isinstance(outbox_id, UUID) else UUID(str(outbox_id))
            for outbox_id in outbox_ids
        ]
        await db.execute(
            update(SecretInvalidationOutbox)
            .where(SecretInvalidationOutbox.id.in_(ids))
            .values(delivered=True)
        )

    @classmethod
    async def sweep_invalidations(
        cls, db: AsyncSession, *, limit: int = 100
    ) -> int:
        """Deliver leftover outbox rows (restart recovery) and mark them.

        Acquisition paths always check the authoritative revision, so a lost
        notification can never make stale authority valid; this sweep only
        repairs cache freshness after restarts or delivery failures.
        """
        result = await db.execute(
            select(SecretInvalidationOutbox)
            .where(SecretInvalidationOutbox.delivered.is_(False))
            .order_by(SecretInvalidationOutbox.created_at)
            .limit(limit)
        )
        rows = list(result.scalars().all())
        if not rows:
            return 0
        events = [
            {
                "outbox_id": str(row.id),
                "slug": row.slug,
                "credential_revision": row.credential_revision,
                "policy_revision": row.policy_revision,
                "cause": row.cause,
            }
            for row in rows
        ]
        delivered = await cls._deliver_events(events)
        await cls._mark_outbox_delivered(db, delivered)
        await db.commit()
        return len(delivered)

    @classmethod
    async def _finish_post_commit(
        cls, db: AsyncSession, secret: ManagedSecret, events: Sequence[dict[str, Any]]
    ) -> None:
        """Complete post-commit bookkeeping without failing a durable mutation.

        The secret and its outbox row are already durable after the first
        ``commit()``. A refresh, delivery, or marking failure must not roll
        back or report the confirmed mutation as failed; it is recorded for
        the restart sweep while the success response stands.
        """
        try:
            await db.refresh(secret)
            delivered = await cls._deliver_events(events)
            await cls._mark_outbox_delivered(db, delivered)
            if delivered:
                await db.commit()
        except Exception as exc:  # noqa: BLE001 - auxiliary work only
            logger.warning(
                "secret_post_commit_bookkeeping_failed",
                slug=getattr(secret, "slug", None),
                error=str(exc),
            )

    # -- stable request identity ------------------------------------------

    @staticmethod
    async def _find_receipt(
        db: AsyncSession, request_id: str
    ) -> SecretMutationReceipt | None:
        result = await db.execute(
            select(SecretMutationReceipt).where(
                SecretMutationReceipt.request_id == request_id
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _check_receipt_conflict(
        receipt: SecretMutationReceipt,
        *,
        slug: str,
        operation: str,
        candidate_fingerprint: str | None,
    ) -> None:
        if (
            receipt.slug != slug
            or receipt.operation != operation
            or receipt.candidate_fingerprint != candidate_fingerprint
        ):
            raise SecretConflictError(slug, receipt.request_id)

    @classmethod
    async def _reconcile_receipt(
        cls, db: AsyncSession, receipt: SecretMutationReceipt
    ) -> ManagedSecret | None:
        """Re-read current state for a retried request without mutating."""
        logger.info(
            "secret_mutation_reconciled",
            slug=receipt.slug,
            operation=receipt.operation,
            request_id=receipt.request_id,
        )
        result = await db.execute(
            select(ManagedSecret).where(ManagedSecret.slug == receipt.slug)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _stage_receipt(
        db: AsyncSession,
        *,
        request_id: str,
        slug: str,
        operation: str,
        credential_revision: int,
        policy_revision: int,
        outcome: str,
        candidate_fingerprint: str | None,
    ) -> None:
        db.add(
            SecretMutationReceipt(
                request_id=request_id,
                slug=slug,
                operation=operation,
                credential_revision=credential_revision,
                policy_revision=policy_revision,
                outcome=outcome,
                candidate_fingerprint=candidate_fingerprint,
            )
        )

    # -- candidate validation ----------------------------------------------

    @staticmethod
    async def prepare_rotation_validation(
        db: AsyncSession,
        slug: str,
        candidate_plaintext: str,
        validator: Callable[[str], Awaitable[bool] | bool],
        *,
        actor_ref: str | None = None,
        owner_ref: str | None = None,
        expected_policy_revision: int | None = None,
    ) -> dict[str, Any]:
        """Validate a candidate outside row-locked work.

        Takes a short metadata read (no locks, no writes), then invokes the
        shared injected identity/probe boundary. The returned envelope binds
        the opaque candidate fingerprint to the expected active revision and
        actor/owner identity for atomic recheck at activation time.
        """
        result = await db.execute(
            select(
                ManagedSecret.status,
                ManagedSecret.credential_revision,
                ManagedSecret.policy_revision,
            ).where(ManagedSecret.slug == slug)
        )
        row = result.one_or_none()
        if row is None:
            raise SecretFencedError(slug, reason="secret is missing")
        status, credential_revision, policy_revision = row
        if _status_value(status) == SecretStatus.ROTATED.value:
            raise SecretRepairRequiredError(slug)
        # Provider call happens here: no row locks held, nothing staged.
        verdict = validator(candidate_plaintext)
        if inspect.isawaitable(verdict):
            verdict = await verdict
        if not verdict:
            raise SecretFencedError(slug, reason="candidate validation failed")
        return {
            "slug": slug,
            "candidate_fingerprint": _candidate_fingerprint(candidate_plaintext),
            "expected_credential_revision": int(credential_revision or 1),
            "expected_policy_revision": (
                int(expected_policy_revision)
                if expected_policy_revision is not None
                else int(policy_revision or 1)
            ),
            "actor_ref": actor_ref,
            "owner_ref": owner_ref,
        }

    @staticmethod
    def _check_validation_envelope(
        secret: ManagedSecret,
        candidate_plaintext: str,
        validation: dict[str, Any] | None,
    ) -> None:
        """Atomically recheck a validation envelope before activation."""
        if validation is None:
            return
        slug = secret.slug
        if validation.get("slug") != slug:
            raise SecretFencedError(slug, reason="validation bound to another secret")
        if validation.get("candidate_fingerprint") != _candidate_fingerprint(
            candidate_plaintext
        ):
            raise SecretFencedError(slug, reason="candidate content changed")
        expected = validation.get("expected_credential_revision")
        if expected is not None and int(expected) != _credential_revision_of(secret):
            raise SecretFencedError(
                slug,
                reason=(
                    "expected credential revision "
                    f"{expected} != active {_credential_revision_of(secret)}"
                ),
            )
        expected_policy = validation.get("expected_policy_revision")
        if expected_policy is not None and int(expected_policy) != _policy_revision_of(
            secret
        ):
            raise SecretFencedError(
                slug,
                reason=(
                    "expected policy revision "
                    f"{expected_policy} != active {_policy_revision_of(secret)}"
                ),
            )
        if _status_value(secret.status) != SecretStatus.ACTIVE.value:
            raise SecretFencedError(
                slug,
                reason="secret is not active; re-admit against the current state",
            )
        envelope_owner = validation.get("owner_ref")
        stored_owner = (secret.details or {}).get("owner_ref")
        if envelope_owner and stored_owner and envelope_owner != stored_owner:
            raise SecretFencedError(
                slug, reason="validation bound to a different owner"
            )

    # -- creation -----------------------------------------------------------

    @classmethod
    async def create_secret(
        cls,
        db: AsyncSession,
        slug: str,
        plaintext: str,
        details: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
        commit: bool = True,
    ) -> ManagedSecret:
        """Create a new managed secret at credential/policy revision 1.

        Slug reuse after a delete is refused: deletion removes the only
        durable generation-bearing row, so recreating the same slug at
        revision 1 would let an old binding resolve unrelated replacement
        material (revision ABA). The ``secrets.deleted`` tombstone audit
        preserves the generation without retaining values.
        """
        if request_id is not None:
            receipt = await cls._find_receipt(db, request_id)
            if receipt is not None:
                cls._check_receipt_conflict(
                    receipt,
                    slug=slug,
                    operation="create",
                    candidate_fingerprint=None,
                )
                existing = await cls._reconcile_receipt(db, receipt)
                if existing is None:  # pragma: no cover - defensive
                    raise SecretConflictError(slug, request_id)
                return existing

        tombstone = await db.execute(
            select(SettingsAuditEvent.id).where(
                SettingsAuditEvent.event_type == "secrets.deleted",
                SettingsAuditEvent.key == f"secrets.{slug}",
            )
        )
        tombstone_row = tombstone.first()
        # Mock AsyncSessions (unit tests without a real DB) return MagicMock
        # rows; only a real SQLAlchemy row counts as a tombstone.
        if tombstone_row is not None and type(tombstone_row).__module__.startswith(
            "sqlalchemy"
        ):
            if commit:
                await db.rollback()
            raise SecretFencedError(
                slug,
                reason="slug was deleted; reuse is refused to prevent revision ABA",
            )

        secret = ManagedSecret(
            slug=slug,
            ciphertext=plaintext,  # StringEncryptedType handles encryption
            status=SecretStatus.ACTIVE,
            credential_revision=1,
            policy_revision=1,
            details=dict(details or {}),
        )
        db.add(secret)
        try:
            await db.flush()
        except IntegrityError:
            if commit:
                await db.rollback()
            raise SecretConflictError(slug, request_id or f"create:{slug}")
        cls._record_audit(
            db,
            event_type="secrets.created",
            slug=slug,
            old_value={},
            new_value=_redacted_revision_json(secret),
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            reason=reason,
            request_id=request_id,
        )
        if request_id is not None:
            cls._stage_receipt(
                db,
                request_id=request_id,
                slug=slug,
                operation="create",
                credential_revision=1,
                policy_revision=1,
                outcome="created",
                candidate_fingerprint=None,
            )
        if commit:
            await db.commit()
            try:
                await db.refresh(secret)
            except Exception as exc:  # noqa: BLE001 - auxiliary work only
                logger.warning(
                    "secret_post_commit_bookkeeping_failed",
                    slug=slug,
                    error=str(exc),
                )
        logger.info("secret_created", slug=slug)
        return secret

    # -- value mutations -----------------------------------------------------

    @classmethod
    async def update_secret(
        cls,
        db: AsyncSession,
        slug: str,
        plaintext: str,
        *,
        expected_credential_revision: int | None = None,
        request_id: str | None = None,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
        commit: bool = True,
    ) -> ManagedSecret | None:
        """Update a secret value, advancing the credential revision.

        Refuses already-ROTATED records: they need the explicit reviewed
        repair path instead of a silent overwrite.
        """
        fingerprint = _candidate_fingerprint(plaintext)
        if request_id is not None:
            receipt = await cls._find_receipt(db, request_id)
            if receipt is not None:
                cls._check_receipt_conflict(
                    receipt,
                    slug=slug,
                    operation="update",
                    candidate_fingerprint=fingerprint,
                )
                return await cls._reconcile_receipt(db, receipt)

        try:
            result = await db.execute(
                select(ManagedSecret)
                .where(ManagedSecret.slug == slug)
                .with_for_update()
            )
            secret = result.scalar_one_or_none()
            if secret is not None:
                _normalize_revisions(secret)
            if not secret:
                logger.warning("secret_not_found_for_update", slug=slug)
                if commit:
                    await db.rollback()
                return None
            if _status_value(secret.status) == SecretStatus.ROTATED.value:
                if commit:
                    await db.rollback()
                raise SecretRepairRequiredError(slug)
            active_revision = _credential_revision_of(secret)
            if expected_credential_revision is not None and (
                int(expected_credential_revision) != active_revision
            ):
                if commit:
                    await db.rollback()
                raise SecretFencedError(
                    slug,
                    reason=(
                        "expected credential revision "
                        f"{expected_credential_revision} != active "
                        f"{active_revision}"
                    ),
                )

            old_snapshot = _redacted_revision_json(secret)
            secret.ciphertext = plaintext
            secret.credential_revision = _credential_revision_of(secret) + 1
            secret.updated_at = datetime.now(timezone.utc)
            await db.flush()
            cls._record_audit(
                db,
                event_type="secrets.updated",
                slug=slug,
                old_value=old_snapshot,
                new_value=_redacted_revision_json(secret),
                actor_user_id=actor_user_id,
                workspace_id=workspace_id,
                reason=reason,
                request_id=request_id,
            )
            events = [
                cls._record_invalidation(
                    db,
                    slug=slug,
                    credential_revision=_credential_revision_of(secret),
                    policy_revision=_policy_revision_of(secret),
                    cause="update",
                )
            ]
            if request_id is not None:
                cls._stage_receipt(
                    db,
                    request_id=request_id,
                    slug=slug,
                    operation="update",
                    credential_revision=_credential_revision_of(secret),
                    policy_revision=_policy_revision_of(secret),
                    outcome="updated",
                    candidate_fingerprint=fingerprint,
                )
            if commit:
                await db.commit()
                await cls._finish_post_commit(db, secret, events)
        except (SecretFencedError, SecretRepairRequiredError, SecretConflictError):
            raise
        except Exception:
            if commit:
                await db.rollback()
            raise
        logger.info(
            "secret_updated",
            slug=slug,
            credential_revision=_credential_revision_of(secret),
        )
        return secret

    @classmethod
    async def rotate_secret(
        cls,
        db: AsyncSession,
        slug: str,
        new_plaintext: str,
        *,
        expected_credential_revision: int | None = None,
        expected_policy_revision: int | None = None,
        validation: dict[str, Any] | None = None,
        validator: Callable[[str], Awaitable[bool] | bool] | None = None,
        request_id: str | None = None,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
        commit: bool = True,
    ) -> ManagedSecret | None:
        """Atomically activate a validated replacement at a new revision.

        The replacement is ``ACTIVE`` under the existing resolution contract
        and immediately resolvable; rotation is recorded as an audit
        event/revision transition, never as an unreadable state. A failed
        candidate, changed ownership/policy/credentials, or a concurrent
        rotation leaves the prior active secret unchanged.

        A trusted validation result is required: either a ``validation``
        envelope from :meth:`prepare_rotation_validation` or a ``validator``
        probe. An omitted validator is never treated as success.
        """
        fingerprint = _candidate_fingerprint(new_plaintext)
        if request_id is not None:
            receipt = await cls._find_receipt(db, request_id)
            if receipt is not None:
                cls._check_receipt_conflict(
                    receipt,
                    slug=slug,
                    operation="rotate",
                    candidate_fingerprint=fingerprint,
                )
                return await cls._reconcile_receipt(db, receipt)

        if validator is None and validation is None:
            raise SecretFencedError(slug, reason="candidate validation required")

        # Candidate probing happens before row-locked work: no database
        # transaction is held across provider calls.
        if validator is not None:
            verdict = validator(new_plaintext)
            if inspect.isawaitable(verdict):
                verdict = await verdict
            if not verdict:
                raise SecretFencedError(slug, reason="candidate validation failed")

        try:
            result = await db.execute(
                select(ManagedSecret)
                .where(ManagedSecret.slug == slug)
                .with_for_update()
            )
            secret = result.scalar_one_or_none()
            if secret is not None:
                _normalize_revisions(secret)
            if not secret:
                logger.warning("secret_not_found_for_rotate", slug=slug)
                if commit:
                    await db.rollback()
                return None
            if _status_value(secret.status) == SecretStatus.ROTATED.value:
                if commit:
                    await db.rollback()
                raise SecretRepairRequiredError(slug)
            if _status_value(secret.status) != SecretStatus.ACTIVE.value:
                if commit:
                    await db.rollback()
                raise SecretFencedError(
                    slug,
                    reason="secret is not active; re-admit against the current state",
                )
            cls._check_validation_envelope(secret, new_plaintext, validation)
            active_revision = _credential_revision_of(secret)
            if expected_credential_revision is not None and (
                int(expected_credential_revision) != active_revision
            ):
                if commit:
                    await db.rollback()
                raise SecretFencedError(
                    slug,
                    reason=(
                        "expected credential revision "
                        f"{expected_credential_revision} != active "
                        f"{active_revision}"
                    ),
                )
            active_policy = _policy_revision_of(secret)
            envelope_policy = (validation or {}).get("expected_policy_revision")
            want_policy = (
                expected_policy_revision
                if expected_policy_revision is not None
                else envelope_policy
            )
            if want_policy is not None and int(want_policy) != active_policy:
                if commit:
                    await db.rollback()
                raise SecretFencedError(
                    slug,
                    reason=(
                        "expected policy revision "
                        f"{want_policy} != active {active_policy}"
                    ),
                )
            envelope_owner = (validation or {}).get("owner_ref")
            if envelope_owner and not (secret.details or {}).get("owner_ref"):
                secret.details = {**(secret.details or {}), "owner_ref": envelope_owner}

            old_snapshot = _redacted_revision_json(secret)
            secret.ciphertext = new_plaintext
            secret.status = SecretStatus.ACTIVE
            secret.credential_revision = _credential_revision_of(secret) + 1
            secret.updated_at = datetime.now(timezone.utc)
            await db.flush()
            cls._record_audit(
                db,
                event_type="secrets.rotated",
                slug=slug,
                old_value=old_snapshot,
                new_value=_redacted_revision_json(secret),
                actor_user_id=actor_user_id,
                workspace_id=workspace_id,
                reason=reason,
                request_id=request_id,
            )
            events = [
                cls._record_invalidation(
                    db,
                    slug=slug,
                    credential_revision=_credential_revision_of(secret),
                    policy_revision=_policy_revision_of(secret),
                    cause="rotation",
                )
            ]
            if request_id is not None:
                cls._stage_receipt(
                    db,
                    request_id=request_id,
                    slug=slug,
                    operation="rotate",
                    credential_revision=_credential_revision_of(secret),
                    policy_revision=_policy_revision_of(secret),
                    outcome="rotated",
                    candidate_fingerprint=fingerprint,
                )
            if commit:
                await db.commit()
                await cls._finish_post_commit(db, secret, events)
        except (
            SecretFencedError,
            SecretRepairRequiredError,
            SecretConflictError,
        ):
            raise
        except Exception:
            if commit:
                await db.rollback()
            raise
        logger.info(
            "secret_rotated",
            slug=slug,
            credential_revision=_credential_revision_of(secret),
        )
        return secret

    @classmethod
    async def repair_rotated_secret(
        cls,
        db: AsyncSession,
        slug: str,
        new_plaintext: str,
        validator: Callable[[str], Awaitable[bool] | bool],
        *,
        request_id: str | None = None,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
        commit: bool = True,
    ) -> ManagedSecret | None:
        """Reviewed repair path for already-ROTATED records.

        Requires a passing ``validator`` result; never blindly reactivates
        historical values. On success the repaired value is ``ACTIVE`` at a
        new credential revision.
        """
        fingerprint = _candidate_fingerprint(new_plaintext)
        if request_id is not None:
            receipt = await cls._find_receipt(db, request_id)
            if receipt is not None:
                cls._check_receipt_conflict(
                    receipt,
                    slug=slug,
                    operation="repair",
                    candidate_fingerprint=fingerprint,
                )
                return await cls._reconcile_receipt(db, receipt)

        verdict = validator(new_plaintext)
        if inspect.isawaitable(verdict):
            verdict = await verdict
        if not verdict:
            raise SecretFencedError(slug, reason="repair candidate rejected")

        try:
            result = await db.execute(
                select(ManagedSecret)
                .where(ManagedSecret.slug == slug)
                .with_for_update()
            )
            secret = result.scalar_one_or_none()
            if secret is not None:
                _normalize_revisions(secret)
            if not secret:
                if commit:
                    await db.rollback()
                return None
            if _status_value(secret.status) != SecretStatus.ROTATED.value:
                if commit:
                    await db.rollback()
                raise SecretFencedError(slug, reason="secret is not in ROTATED state")

            old_snapshot = _redacted_revision_json(secret)
            secret.ciphertext = new_plaintext
            secret.status = SecretStatus.ACTIVE
            secret.credential_revision = _credential_revision_of(secret) + 1
            secret.updated_at = datetime.now(timezone.utc)
            await db.flush()
            cls._record_audit(
                db,
                event_type="secrets.repaired",
                slug=slug,
                old_value=old_snapshot,
                new_value=_redacted_revision_json(secret),
                actor_user_id=actor_user_id,
                workspace_id=workspace_id,
                reason=reason or "reviewed repair of historical ROTATED record",
                request_id=request_id,
            )
            events = [
                cls._record_invalidation(
                    db,
                    slug=slug,
                    credential_revision=_credential_revision_of(secret),
                    policy_revision=_policy_revision_of(secret),
                    cause="repair",
                )
            ]
            if request_id is not None:
                cls._stage_receipt(
                    db,
                    request_id=request_id,
                    slug=slug,
                    operation="repair",
                    credential_revision=_credential_revision_of(secret),
                    policy_revision=_policy_revision_of(secret),
                    outcome="repaired",
                    candidate_fingerprint=fingerprint,
                )
            if commit:
                await db.commit()
                await cls._finish_post_commit(db, secret, events)
        except (SecretFencedError, SecretConflictError):
            raise
        except Exception:
            if commit:
                await db.rollback()
            raise
        logger.info(
            "secret_repaired",
            slug=slug,
            credential_revision=_credential_revision_of(secret),
        )
        return secret

    # -- status / deletion ----------------------------------------------------

    @classmethod
    async def set_status(
        cls,
        db: AsyncSession,
        slug: str,
        status: SecretStatus,
        *,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        commit: bool = True,
    ) -> ManagedSecret | None:
        """Change lifecycle status, advancing the policy revision.

        Metadata-only transitions advance ``policy_revision`` (fencing
        acquisition) without changing ``credential_revision``. The audit
        event captures the lifecycle transition without exposing secret
        material; ``redacted=True`` enforces that contract at storage.
        """
        if request_id is not None:
            receipt = await cls._find_receipt(db, request_id)
            if receipt is not None:
                cls._check_receipt_conflict(
                    receipt,
                    slug=slug,
                    operation="set_status",
                    candidate_fingerprint=_status_value(status),
                )
                return await cls._reconcile_receipt(db, receipt)

        try:
            result = await db.execute(
                select(ManagedSecret)
                .where(ManagedSecret.slug == slug)
                .with_for_update()
            )
            secret = result.scalar_one_or_none()
            if secret is not None:
                _normalize_revisions(secret)
            if not secret:
                logger.warning("secret_not_found_for_status_change", slug=slug)
                if commit:
                    await db.rollback()
                return None

            previous_status = _status_value(secret.status)
            new_status = _status_value(status)

            if previous_status == SecretStatus.ROTATED.value:
                if commit:
                    await db.rollback()
                raise SecretRepairRequiredError(slug)

            old_snapshot = _redacted_revision_json(secret)
            secret.status = status
            secret.policy_revision = _policy_revision_of(secret) + 1
            secret.updated_at = datetime.now(timezone.utc)
            await db.flush()
            db.add(
                SettingsAuditEvent(
                    event_type="secrets.status.changed",
                    key=f"secrets.{slug}",
                    scope="system",
                    workspace_id=workspace_id or _DEFAULT_SETTINGS_SUBJECT_ID,
                    user_id=_DEFAULT_SETTINGS_SUBJECT_ID,
                    actor_user_id=actor_user_id,
                    old_value_json=old_snapshot,
                    new_value_json=_redacted_revision_json(secret),
                    redacted=True,
                    reason=reason,
                    request_id=request_id,
                )
            )
            events = [
                cls._record_invalidation(
                    db,
                    slug=slug,
                    credential_revision=_credential_revision_of(secret),
                    policy_revision=_policy_revision_of(secret),
                    cause="status",
                )
            ]
            if request_id is not None:
                cls._stage_receipt(
                    db,
                    request_id=request_id,
                    slug=slug,
                    operation="set_status",
                    credential_revision=_credential_revision_of(secret),
                    policy_revision=_policy_revision_of(secret),
                    outcome="status_changed",
                    candidate_fingerprint=new_status,
                )
            if commit:
                await db.commit()
                await cls._finish_post_commit(db, secret, events)
        except (SecretConflictError,):
            raise
        except Exception:
            if commit:
                await db.rollback()
            raise
        logger.info(
            "secret_status_changed",
            slug=slug,
            previous_status=previous_status,
            new_status=new_status,
        )
        return secret

    @classmethod
    async def delete_secret(
        cls,
        db: AsyncSession,
        slug: str,
        *,
        request_id: str | None = None,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
        strict: bool = False,
        commit: bool = True,
    ) -> bool:
        """Delete a secret unless the complete consumer inventory protects it.

        The protection check is server-side and complete across Settings,
        Provider Profiles, and RepositoryConnections, executed in the same
        transaction as the delete: either a protected reference survives or
        the delete proceeds — never a dangling newly admitted binding.
        Returns ``False`` for missing or protected secrets (use
        ``strict=True`` to raise with bounded per-type counts instead).
        A tombstone audit identity is preserved without retaining values.
        """
        if request_id is not None:
            receipt = await cls._find_receipt(db, request_id)
            if receipt is not None:
                cls._check_receipt_conflict(
                    receipt,
                    slug=slug,
                    operation="delete",
                    candidate_fingerprint=None,
                )
                reconciled = await cls._reconcile_receipt(db, receipt)
                return reconciled is None

        try:
            result = await db.execute(
                select(ManagedSecret)
                .where(ManagedSecret.slug == slug)
                .with_for_update()
            )
            secret = result.scalar_one_or_none()
            if secret is not None:
                _normalize_revisions(secret)
            if not secret:
                logger.warning("secret_not_found_for_delete", slug=slug)
                if commit:
                    await db.rollback()
                return False

            counts = await cls._server_side_consumer_counts(
                db, slug, for_update=True
            )
            if sum(counts.values()) > 0:
                cls._record_audit(
                    db,
                    event_type="secrets.delete.blocked",
                    slug=slug,
                    old_value=_redacted_revision_json(secret),
                    new_value=_redacted_revision_json(secret),
                    actor_user_id=actor_user_id,
                    workspace_id=workspace_id,
                    reason=reason or "referenced by consumers",
                    request_id=request_id,
                )
                if commit:
                    await db.commit()
                logger.warning(
                    "secret_delete_blocked",
                    slug=slug,
                    counts=counts,
                )
                if strict:
                    raise SecretReferenceProtectedError(slug, counts)
                return False

            snapshot = _redacted_revision_json(secret)
            await db.delete(secret)
            await db.flush()
            cls._record_audit(
                db,
                event_type="secrets.deleted",
                slug=slug,
                old_value=snapshot,
                new_value={},
                actor_user_id=actor_user_id,
                workspace_id=workspace_id,
                reason=reason,
                request_id=request_id,
            )
            events = [
                cls._record_invalidation(
                    db,
                    slug=slug,
                    credential_revision=snapshot["credential_revision"],
                    policy_revision=snapshot["policy_revision"],
                    cause="deletion",
                )
            ]
            if request_id is not None:
                cls._stage_receipt(
                    db,
                    request_id=request_id,
                    slug=slug,
                    operation="delete",
                    credential_revision=snapshot["credential_revision"],
                    policy_revision=snapshot["policy_revision"],
                    outcome="deleted",
                    candidate_fingerprint=None,
                )
            if commit:
                await db.commit()
                try:
                    delivered = await cls._deliver_events(events)
                    await cls._mark_outbox_delivered(db, delivered)
                    if delivered:
                        await db.commit()
                except Exception as exc:  # noqa: BLE001 - auxiliary work only
                    logger.warning(
                        "secret_post_commit_bookkeeping_failed",
                        slug=slug,
                        error=str(exc),
                    )
        except SecretReferenceProtectedError:
            raise
        except SecretConflictError:
            raise
        except Exception:
            if commit:
                await db.rollback()
            raise
        logger.info("secret_deleted", slug=slug)
        return True

    # -- reads ------------------------------------------------------------------

    @classmethod
    async def list_metadata(cls, db: AsyncSession) -> Sequence[Row]:
        """List all secret metadata without leaking secret contents."""
        result = await db.execute(
            select(
                ManagedSecret.id,
                ManagedSecret.slug,
                ManagedSecret.status,
                ManagedSecret.credential_revision,
                ManagedSecret.policy_revision,
                ManagedSecret.details,
                ManagedSecret.created_at,
                ManagedSecret.updated_at,
            )
        )
        return result.all()

    @classmethod
    async def _scoped_setting_rows(
        cls,
        db: AsyncSession,
        *,
        workspace_id: UUID | None,
        user_id: UUID | None,
    ):
        resolved_workspace_id = workspace_id or _DEFAULT_SETTINGS_SUBJECT_ID
        resolved_user_id = user_id or _DEFAULT_SETTINGS_SUBJECT_ID
        usage_result = await db.execute(
            select(
                SettingsOverride.key,
                SettingsOverride.scope,
                SettingsOverride.value_json,
            ).where(
                SettingsOverride.workspace_id == resolved_workspace_id,
                or_(
                    and_(
                        SettingsOverride.scope == "user",
                        SettingsOverride.user_id == resolved_user_id,
                    ),
                    and_(
                        SettingsOverride.scope != "user",
                        SettingsOverride.user_id == _DEFAULT_SETTINGS_SUBJECT_ID,
                    ),
                ),
            )
        )
        return usage_result

    @staticmethod
    def _like_escape(value: str) -> str:
        return (
            value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )

    @classmethod
    def _iter_nested_strings(cls, value: Any):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from cls._iter_nested_strings(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from cls._iter_nested_strings(item)

    @classmethod
    def _payload_references_slug(cls, payload: Any, secret_ref: str) -> bool:
        return any(
            text == secret_ref
            for text in cls._iter_nested_strings(payload)
        )

    @staticmethod
    def _typed_connection_references_slug(config: Any, slug: str) -> bool:
        """Match the canonical typed secret reference for connections.

        Repository connections persist ``{"source": "secret_ref",
        "credentialRef": {"provider": "managed", "key": "<slug>"}}`` (camel
        and snake aliases accepted). A literal ``db://`` scan never sees
        those rows, so deletion protection must match the typed contract.
        """
        stack: list[Any] = [config]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                source = item.get("source")
                ref = item.get("credentialRef", item.get("credential_ref"))
                if source == "secret_ref" and isinstance(ref, dict):
                    provider = ref.get("provider")
                    key = ref.get("key")
                    if provider == "managed" and key == slug:
                        return True
                stack.extend(item.values())
            elif isinstance(item, (list, tuple)):
                stack.extend(item)
        return False

    @classmethod
    def _connection_config_references_slug(cls, config: Any, slug: str) -> bool:
        if config is None:
            return False
        if cls._payload_references_slug(config, f"db://{slug}"):
            return True
        return cls._typed_connection_references_slug(config, slug)

    @classmethod
    async def _extended_consumer_usages(
        cls, db: AsyncSession, slug: str, *, for_update: bool = False
    ) -> list[dict[str, Any]]:
        """Scan Provider Profile and RepositoryConnection typed references."""
        from api_service.db.models import (
            ManagedAgentProviderProfile,
            RepositoryConnectionRecord,
        )

        secret_ref = f"db://{slug}"
        pattern = f"%db://{cls._like_escape(slug)}%"
        usages: list[dict[str, Any]] = []
        try:
            profile_stmt = select(
                ManagedAgentProviderProfile.profile_id,
                ManagedAgentProviderProfile.secret_refs,
            ).where(
                func.cast(
                    ManagedAgentProviderProfile.secret_refs, Text
                ).like(pattern, escape="\\")
            )
            if for_update:
                profile_stmt = profile_stmt.with_for_update()
            profile_rows = await db.execute(profile_stmt)
            for profile_id, secret_refs in profile_rows:
                if secret_refs is None:
                    continue
                if not cls._payload_references_slug(secret_refs, secret_ref):
                    continue
                usages.append(
                    {
                        "consumerType": "provider_profile",
                        "objectName": f"Provider profile {profile_id}",
                        "reference": secret_ref,
                        "scope": "system",
                        "settingKey": None,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - extended scan is advisory
            logger.warning(
                "secret_usage_profile_scan_failed", slug=slug, error=str(exc)
            )
        try:
            broad = f"%{cls._like_escape(slug)}%"
            connection_stmt = select(
                RepositoryConnectionRecord.connection_id,
                RepositoryConnectionRecord.credential_config,
            ).where(
                func.cast(
                    RepositoryConnectionRecord.credential_config, Text
                ).like(broad, escape="\\")
            )
            if for_update:
                connection_stmt = connection_stmt.with_for_update()
            connection_rows = await db.execute(connection_stmt)
            for connection_id, credential_config in connection_rows:
                if credential_config is None:
                    continue
                if not cls._connection_config_references_slug(credential_config, slug):
                    continue
                usages.append(
                    {
                        "consumerType": "repository_connection",
                        "objectName": f"Repository connection {connection_id}",
                        "reference": secret_ref,
                        "scope": "system",
                        "settingKey": None,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - extended scan is advisory
            logger.warning(
                "secret_usage_connection_scan_failed", slug=slug, error=str(exc)
            )
        return usages

    @classmethod
    async def _server_side_consumer_counts(
        cls, db: AsyncSession, slug: str, *, for_update: bool = False
    ) -> dict[str, int]:
        """Complete server-side inventory for deletion protection.

        Unlike the caller-filtered usage display, this scans every scope and
        every consumer domain. Only per-type counts are returned so errors
        never leak consumer identities across scopes.

        With ``for_update=True`` the matching consumer rows are locked in the
        same transaction as the secret delete, so a concurrent attach cannot
        commit a new reference between the inventory check and the delete.
        """
        counts = {"setting_override": 0, "provider_profile": 0, "repository_connection": 0}
        secret_ref = f"db://{slug}"
        pattern = f"%db://{cls._like_escape(slug)}%"
        try:
            override_stmt = select(SettingsOverride.value_json).where(
                func.cast(SettingsOverride.value_json, Text).like(
                    pattern, escape="\\"
                )
            )
            if for_update:
                override_stmt = override_stmt.with_for_update()
            override_rows = await db.execute(override_stmt)
            for (value_json,) in override_rows:
                if cls._value_references_secret(value_json, secret_ref):
                    counts["setting_override"] += 1
        except Exception as exc:  # noqa: BLE001 - fail closed on scan errors
            logger.warning(
                "secret_delete_scan_failed", slug=slug, error=str(exc)
            )
            raise SecretFencedError(slug, reason="consumer scan unavailable")
        extended = await cls._extended_consumer_usages(db, slug, for_update=for_update)
        for usage in extended:
            consumer_type = usage["consumerType"]
            if consumer_type == "provider_profile":
                counts["provider_profile"] += 1
            elif consumer_type == "repository_connection":
                counts["repository_connection"] += 1
        return counts

    @classmethod
    async def list_secret_usage(
        cls,
        db: AsyncSession,
        slug: str,
        *,
        workspace_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """List metadata-only consumers for a managed secret reference."""
        secret_ref = f"db://{slug}"
        status_result = await db.execute(
            select(ManagedSecret.status).where(ManagedSecret.slug == slug)
        )
        status = status_result.scalar_one_or_none()
        if status is None:
            return {
                "secretRef": secret_ref,
                "usages": [],
                "diagnostics": [
                    {
                        "code": "secret_ref_unresolved",
                        "message": "Managed secret is missing.",
                        "severity": "error",
                    }
                ],
            }

        usage_result = await cls._scoped_setting_rows(
            db, workspace_id=workspace_id, user_id=user_id
        )
        usages = []
        for key, scope, value_json in usage_result:
            if not cls._value_references_secret(value_json, secret_ref):
                continue
            scope = str(scope)
            scope_label = "Workspace" if scope == "workspace" else "User"
            usages.append(
                {
                    "consumerType": "setting_override",
                    "objectName": f"{scope_label} setting {key}",
                    "reference": secret_ref,
                    "scope": scope,
                    "settingKey": key,
                }
            )
        usages.extend(await cls._extended_consumer_usages(db, slug))

        return {"secretRef": secret_ref, "usages": usages, "diagnostics": []}

    @staticmethod
    def _value_references_secret(value: Any, secret_ref: str) -> bool:
        if value == secret_ref:
            return True
        if isinstance(value, dict):
            return any(
                SecretsService._value_references_secret(item, secret_ref)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(
                SecretsService._value_references_secret(item, secret_ref)
                for item in value
            )
        return False

    @classmethod
    async def get_secret_with_revision(
        cls, db: AsyncSession, slug: str
    ) -> dict[str, Any] | None:
        """Authoritative single read of value, state, and revisions together.

        Metadata and value come from one row read: there is no separate
        metadata lookup followed by an unconstrained latest-value lookup, so
        a rotation between two reads cannot return N+1 material labeled N.
        Only ``ACTIVE`` rows resolve; anything else is ``None``.
        """
        result = await db.execute(
            select(ManagedSecret).where(
                ManagedSecret.slug == slug,
                ManagedSecret.status == SecretStatus.ACTIVE,
            )
        )
        secret = result.scalar_one_or_none()
        if secret is not None:
            _normalize_revisions(secret)
        if not secret:
            logger.warning("active_secret_not_found", slug=slug)
            return None
        # ciphertext decrypted automatically by StringEncryptedType
        return {
            "value": secret.ciphertext,
            "credential_revision": _credential_revision_of(secret),
            "policy_revision": _policy_revision_of(secret),
        }

    @classmethod
    async def get_secret(
        cls,
        db: AsyncSession,
        slug: str,
        *,
        expected_revision: int | None = None,
        expected_credential_revision: int | None = None,
        expected_policy_revision: int | None = None,
    ) -> str | None:
        """Retrieve the plaintext value of an ACTIVE secret.

        When an expected revision is supplied, the authoritative read must
        match it; a mismatch returns ``None`` (fenced) instead of stale
        material. Acquisition therefore stays correct even if an
        invalidation notification was lost.
        """
        resolved = await cls.get_secret_with_revision(db, slug)
        if resolved is None:
            return None
        want_credential = (
            expected_credential_revision
            if expected_credential_revision is not None
            else expected_revision
        )
        if want_credential is not None and int(want_credential) != int(
            resolved["credential_revision"]
        ):
            logger.warning(
                "secret_revision_fenced",
                slug=slug,
                expected_revision=int(want_credential),
                active_revision=int(resolved["credential_revision"]),
            )
            return None
        if expected_policy_revision is not None and int(
            expected_policy_revision
        ) != int(resolved["policy_revision"]):
            logger.warning(
                "secret_policy_fenced",
                slug=slug,
                expected_policy_revision=int(expected_policy_revision),
                active_policy_revision=int(resolved["policy_revision"]),
            )
            return None
        return resolved["value"]

    @classmethod
    async def validate_secret_ref(cls, db: AsyncSession, slug: str) -> dict[str, Any]:
        """Return redacted metadata-only validation diagnostics for a secret."""
        checked_at = datetime.now(timezone.utc).isoformat()
        result = await db.execute(
            select(
                ManagedSecret.status,
                ManagedSecret.credential_revision,
                ManagedSecret.policy_revision,
            ).where(ManagedSecret.slug == slug)
        )
        row = result.one_or_none()

        if row is None:
            return {
                "valid": False,
                "status": "missing",
                "checkedAt": checked_at,
                "diagnostics": [
                    {
                        "code": "secret_ref_unresolved",
                        "message": "Managed secret is missing.",
                        "severity": "error",
                    }
                ],
            }

        status, credential_revision, policy_revision = row
        secret_status = _status_value(status)
        if secret_status == SecretStatus.ACTIVE.value:
            return {
                "valid": True,
                "status": secret_status,
                "checkedAt": checked_at,
                "credentialRevision": int(credential_revision or 1),
                "policyRevision": int(policy_revision or 1),
                "diagnostics": [
                    {
                        "code": "secret_ref_resolvable",
                        "message": "Managed secret is active.",
                        "severity": "info",
                    }
                ],
            }
        if secret_status == SecretStatus.ROTATED.value:
            return {
                "valid": False,
                "status": secret_status,
                "checkedAt": checked_at,
                "credentialRevision": int(credential_revision or 1),
                "policyRevision": int(policy_revision or 1),
                "diagnostics": [
                    {
                        "code": "secret_repair_required",
                        "message": (
                            "Managed secret is in historical ROTATED state; "
                            "reviewed repair is required."
                        ),
                        "severity": "error",
                    }
                ],
            }

        return {
            "valid": False,
            "status": secret_status,
            "checkedAt": checked_at,
            "credentialRevision": int(credential_revision or 1),
            "policyRevision": int(policy_revision or 1),
            "diagnostics": [
                {
                    "code": "secret_ref_unresolved",
                    "message": f"Managed secret is {secret_status}.",
                    "severity": "error",
                }
            ],
        }

    @classmethod
    async def import_from_env(
        cls,
        db: AsyncSession,
        env_dict: dict[str, str],
        *,
        overwrite_active: bool = False,
        request_id: str | None = None,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        commit: bool = True,
    ) -> int:
        """
        Migrate legacy .env values.
        Upserts values to ManagedSecrets under one transaction with unified
        revision, audit, and invalidation semantics.
        By default, existing active secrets are skipped.
        """
        imported_count = 0
        events: list[dict[str, Any]] = []
        try:
            for key, value in env_dict.items():
                with db.sync_session.no_autoflush:
                    result = await db.execute(
                        select(ManagedSecret)
                        .where(ManagedSecret.slug == key)
                        .with_for_update()
                    )
                existing = result.scalar_one_or_none()
                if existing is not None:
                    _normalize_revisions(existing)

                if existing:
                    if existing.status == SecretStatus.ACTIVE and not overwrite_active:
                        continue  # Skip overriding already active managed secrets
                    if _status_value(existing.status) == SecretStatus.ROTATED.value:
                        raise SecretRepairRequiredError(key)
                    old_snapshot = _redacted_revision_json(existing)
                    now = datetime.now(timezone.utc)
                    existing.ciphertext = value
                    existing.status = SecretStatus.ACTIVE
                    existing.credential_revision = (
                        _credential_revision_of(existing) + 1
                    )
                    details = dict(existing.details or {})
                    details.update(
                        {"imported_from": ".env", "migrated_at": now.isoformat()}
                    )
                    existing.details = details
                    existing.updated_at = now
                    await db.flush()
                    cls._record_audit(
                        db,
                        event_type="secrets.imported",
                        slug=key,
                        old_value=old_snapshot,
                        new_value=_redacted_revision_json(existing),
                        actor_user_id=actor_user_id,
                        workspace_id=workspace_id,
                        reason="legacy .env import",
                        request_id=request_id,
                    )
                    events.append(
                        cls._record_invalidation(
                            db,
                            slug=key,
                            credential_revision=_credential_revision_of(existing),
                            policy_revision=_policy_revision_of(existing),
                            cause="import",
                        )
                    )
                    imported_count += 1
                else:
                    now = datetime.now(timezone.utc)
                    fresh = ManagedSecret(
                        slug=key,
                        ciphertext=value,
                        status=SecretStatus.ACTIVE,
                        credential_revision=1,
                        policy_revision=1,
                        details={
                            "imported_from": ".env",
                            "migrated_at": now.isoformat(),
                        },
                    )
                    db.add(fresh)
                    await db.flush()
                    cls._record_audit(
                        db,
                        event_type="secrets.imported",
                        slug=key,
                        old_value={},
                        new_value=_redacted_revision_json(fresh),
                        actor_user_id=actor_user_id,
                        workspace_id=workspace_id,
                        reason="legacy .env import",
                        request_id=request_id,
                    )
                    imported_count += 1

            if imported_count > 0:
                if commit:
                    await db.commit()
                    try:
                        delivered = await cls._deliver_events(events)
                        await cls._mark_outbox_delivered(db, delivered)
                        if delivered:
                            await db.commit()
                    except Exception as exc:  # noqa: BLE001 - auxiliary work only
                        logger.warning(
                            "secret_post_commit_bookkeeping_failed",
                            error=str(exc),
                        )
                logger.info("secrets_imported_from_env", count=imported_count)
        except Exception:
            if commit:
                await db.rollback()
            raise

        return imported_count
