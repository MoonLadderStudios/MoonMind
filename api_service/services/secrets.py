"""Atomic, revision-fenced, reference-aware managed-secret lifecycle.

Implements MoonLadderStudios/MoonMind#4006 (plan slice 1 of
``docs/RepositoryAccessAndWorkspaceDesign.md`` CONTRACT-009, INV-003,
CONTRACT-005, TEST-002).

Design rules enforced here:

* **One transaction owner.** Every mutating helper accepts ``auto_commit``.
  The default (``True``) preserves the historical single-call behavior.
  Callers that need one atomic connection/secret/revision/audit change pass
  ``auto_commit=False``, compose several helpers on the same ``AsyncSession``,
  and commit once themselves. Helpers never roll back caller work and never
  report success before the durable transition.
* **Monotonic revisions in metadata.** ``ManagedSecret.details`` carries
  ``credential_revision`` (advances only on credential replacement) and
  ``policy_revision`` (advances on metadata-only changes). No schema change
  is required, the values stay inside the existing metadata-only API shape,
  and they are never secret material. Legacy rows without these keys read
  as revision 1.
* **Rotation is an event/revision transition.** A successful rotation leaves
  the replacement ``ACTIVE`` under the existing resolution contract and
  records a durable ``secrets.rotated`` audit event. Historical ``ROTATED``
  rows are *not* blindly reactivated; they require the explicit reviewed
  :meth:`SecretsService.repair_rotated_secret` path.
* **Validation happens outside the write transaction.** Candidates are
  admitted to an opaque in-memory :class:`ValidationBinding` and probed
  through the injected :class:`SecretCandidateValidator` boundary without
  holding a database transaction. Activation rechecks the expected active
  revision, actor/resource owner, and policy revision atomically; any drift
  invalidates the binding and the prior secret stays usable. Plaintext
  tokens and publicly comparable digests are never persisted.
* **Idempotent mutations.** Every mutation accepts a stable ``request_id``.
  A retry after a lost commit acknowledgment reconciles against the durable
  audit record instead of rotating again; conflicting reuse of a request id
  is rejected.
* **Restart-safe invalidation.** The revision advance and the invalidation
  evidence commit transactionally (inside the audit event). Cache
  notifications fan out through post-commit hooks only after commit, and
  acquisition always rechecks the authoritative revision, so a lost
  notification can never validate stale authority.
* **Secret safety.** Plaintext, ciphertext, bearer handles, and raw
  validation responses never enter logs, audit JSON, exception models, or
  return values outside the explicit secret-value getters.
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, Sequence
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, Row
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    ManagedSecret,
    SecretStatus,
    SettingsAuditEvent,
    SettingsOverride,
)

logger = structlog.get_logger(__name__)

_DEFAULT_SETTINGS_SUBJECT_ID = UUID("00000000-0000-0000-0000-000000000000")

# Metadata-only detail keys. These are safe to expose through the existing
# SecretMetadataResponse.details shape: they are counters/identifiers, never
# secret material.
CREDENTIAL_REVISION_KEY = "credential_revision"
POLICY_REVISION_KEY = "policy_revision"
LAST_REQUEST_ID_KEY = "last_request_id"
VALIDATED_ACTOR_KEY = "validated_actor_ref"
REPAIR_OF_KEY = "repair_of_status"

# Bounded machine-readable diagnostics. Callers and logs use these codes;
# human prose stays out of the contract.
DIAG_CONFLICT = "secret_mutation_conflict"
DIAG_FENCED = "secret_revision_fenced"
DIAG_UNAVAILABLE = "secret_unavailable"
DIAG_REPAIR_REQUIRED = "secret_repair_required"
DIAG_IN_USE = "secret_ref_in_use"
DIAG_UNRESOLVED = "secret_ref_unresolved"
DIAG_RESOLVABLE = "secret_ref_resolvable"


class SecretMutationConflict(Exception):
    """Stable request identity reused with conflicting operation/payload."""

    code = DIAG_CONFLICT


class SecretFencedError(Exception):
    """Expected revision/state no longer matches the authoritative row."""

    code = DIAG_FENCED


class SecretRepairRequiredError(Exception):
    """Historical ROTATED row needs the explicit reviewed repair path."""

    code = DIAG_REPAIR_REQUIRED


class SecretInUseError(Exception):
    """Deletion blocked by a complete server-side consumer inventory."""

    code = DIAG_IN_USE


@dataclass(frozen=True)
class SecretMutationOutcome:
    """Safe, metadata-only summary of a committed secret transition."""

    slug: str
    status: str
    credential_revision: int
    policy_revision: int
    request_id: str | None = None
    outcome: str = "committed"


@dataclass(frozen=True)
class SecretValidationRequest:
    """In-memory candidate probe issued outside any database transaction."""

    slug: str
    candidate_id: str
    expected_credential_revision: int
    actor_ref: str | None
    policy_revision: int


@dataclass(frozen=True)
class SecretValidationResult:
    ok: bool
    reason_code: str = "candidate_valid"


class SecretCandidateValidator(Protocol):
    """Injected identity/probe boundary for candidate validation.

    Implemented by the host integration (see #4005); tests inject fakes.
    The candidate plaintext is passed in memory only and must never be
    persisted by an implementation.
    """

    async def __call__(
        self, request: SecretValidationRequest, candidate_plaintext: str
    ) -> SecretValidationResult: ...


@dataclass
class ValidationBinding:
    """Opaque in-memory admission for one candidate activation attempt."""

    binding_id: str
    slug: str
    expected_credential_revision: int
    actor_ref: str | None
    policy_revision: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    consumed: bool = False


PostCommitHook = Callable[[SecretMutationOutcome], None]

_POST_COMMIT_HOOKS: list[PostCommitHook] = []


def register_secret_post_commit_hook(hook: PostCommitHook) -> None:
    """Register a cache-notification fan-out invoked only after commit."""
    _POST_COMMIT_HOOKS.append(hook)


def unregister_secret_post_commit_hook(hook: PostCommitHook) -> None:
    """Remove a previously registered post-commit hook (tests/cleanup)."""
    try:
        _POST_COMMIT_HOOKS.remove(hook)
    except ValueError:
        pass


def _drain_post_commit_hooks(outcome: SecretMutationOutcome) -> None:
    for hook in list(_POST_COMMIT_HOOKS):
        try:
            hook(outcome)
        except Exception as exc:  # noqa: BLE001 - notifications never fail commits
            logger.warning(
                "secret_post_commit_hook_failed",
                slug=outcome.slug,
                error=str(exc),
            )


def _credential_revision_of(details: Any) -> int:
    try:
        rev = int((details or {}).get(CREDENTIAL_REVISION_KEY, 1))
    except (TypeError, ValueError):
        return 1
    return rev if rev >= 1 else 1


def _policy_revision_of(details: Any) -> int:
    try:
        rev = int((details or {}).get(POLICY_REVISION_KEY, 1))
    except (TypeError, ValueError):
        return 1
    return rev if rev >= 1 else 1


def _status_value(status: Any) -> str:
    if isinstance(status, SecretStatus):
        return status.value
    return str(status)


def _secret_ref_for(slug: str) -> str:
    return f"db://{slug}"


def _supports_row_lock(db: AsyncSession) -> bool:
    try:
        bind = db.sync_session.bind  # type: ignore[union-attr]
        return getattr(getattr(bind, "dialect", None), "name", "") == "postgresql"
    except Exception:  # noqa: BLE001 - conservative fallback to plain read
        return False


async def _load_secret(db: AsyncSession, slug: str, *, for_update: bool = False) -> ManagedSecret | None:
    stmt = select(ManagedSecret).where(ManagedSecret.slug == slug)
    if for_update and _supports_row_lock(db):
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _commit_or_flush(
    db: AsyncSession, instance: ManagedSecret | None, *, auto_commit: bool
) -> None:
    if auto_commit:
        await db.commit()
        if instance is not None:
            await db.refresh(instance)
    else:
        await db.flush()


def _record_secret_audit(
    db: AsyncSession,
    *,
    event_type: str,
    slug: str,
    old_status: str | None,
    new_status: str,
    credential_revision: int,
    policy_revision: int,
    actor_user_id: UUID | None = None,
    workspace_id: UUID | None = None,
    reason: str | None = None,
    request_id: str | None = None,
) -> SettingsAuditEvent:
    """Append a redacted metadata-only audit event (never secret material)."""
    event = SettingsAuditEvent(
        event_type=event_type,
        key=f"secrets.{slug}",
        scope="system",
        workspace_id=workspace_id or _DEFAULT_SETTINGS_SUBJECT_ID,
        user_id=_DEFAULT_SETTINGS_SUBJECT_ID,
        actor_user_id=actor_user_id,
        old_value_json={"status": old_status} if old_status is not None else None,
        new_value_json={
            "status": new_status,
            "credential_revision": credential_revision,
            "policy_revision": policy_revision,
            "invalidated": True,
        },
        redacted=True,
        reason=reason,
        request_id=request_id,
    )
    db.add(event)
    return event


async def _reconcile_request_id(
    db: AsyncSession, *, slug: str, event_type: str, request_id: str | None
) -> ManagedSecret | None:
    """Return the current row when ``request_id`` already committed.

    Retrying after a lost commit acknowledgment must reconcile the original
    operation instead of mutating again. Reuse of one request id for a
    *different* operation on the same key is a conflict.
    """
    if not request_id:
        return None
    existing = await db.execute(
        select(SettingsAuditEvent.event_type).where(
            SettingsAuditEvent.key == f"secrets.{slug}",
            SettingsAuditEvent.request_id == request_id,
        )
    )
    prior_types = {row[0] for row in existing.all()}
    if not prior_types:
        return None
    if event_type not in prior_types:
        raise SecretMutationConflict(
            f"Request id {request_id!r} was already used for a different "
            f"secret operation on {slug!r}."
        )
    logger.info("secret_request_reconciled", slug=slug, event_type=event_type)
    return await _load_secret(db, slug)


class SecretsService:
    """Service layer for managing securely encrypted secrets."""

    # -- lifecycle helpers -------------------------------------------------

    @staticmethod
    def credential_revision(secret: ManagedSecret) -> int:
        """Return the monotonic credential revision for a secret row."""
        return _credential_revision_of(secret.details)

    @staticmethod
    def policy_revision(secret: ManagedSecret) -> int:
        """Return the monotonic policy revision for a secret row."""
        return _policy_revision_of(secret.details)

    @classmethod
    async def admit_rotation_candidate(
        cls,
        db: AsyncSession,
        slug: str,
        *,
        actor_ref: str | None = None,
    ) -> ValidationBinding:
        """Admit a candidate outside any long-running database transaction.

        Performs one short metadata-only read to bind the expected active
        credential revision, actor/resource owner, and policy revision into
        an opaque in-memory binding. The caller holds the candidate
        plaintext in memory, probes it through the injected validator, then
        calls :meth:`activate_admitted_candidate`, which rechecks every
        bound version atomically before activation.
        """
        row = await _load_secret(db, slug)
        return ValidationBinding(
            binding_id=uuid4().hex,
            slug=slug,
            expected_credential_revision=_credential_revision_of(
                row.details if row is not None else None
            ),
            actor_ref=actor_ref,
            policy_revision=_policy_revision_of(
                row.details if row is not None else None
            ),
        )

    @classmethod
    async def create_secret(
        cls,
        db: AsyncSession,
        slug: str,
        plaintext: str,
        details: dict[str, Any] | None = None,
        *,
        auto_commit: bool = True,
        request_id: str | None = None,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
    ) -> ManagedSecret:
        """Create a new managed secret (credential revision starts at 1)."""
        reconciled = await _reconcile_request_id(
            db, slug=slug, event_type="secrets.created", request_id=request_id
        )
        if reconciled is not None:
            return reconciled

        merged = dict(details or {})
        merged.setdefault(CREDENTIAL_REVISION_KEY, 1)
        merged.setdefault(POLICY_REVISION_KEY, 1)
        if request_id is not None:
            merged[LAST_REQUEST_ID_KEY] = request_id

        secret = ManagedSecret(
            slug=slug,
            ciphertext=plaintext,  # StringEncryptedType handles encryption
            status=SecretStatus.ACTIVE,
            details=merged,
        )
        db.add(secret)
        _record_secret_audit(
            db,
            event_type="secrets.created",
            slug=slug,
            old_status=None,
            new_status=SecretStatus.ACTIVE.value,
            credential_revision=1,
            policy_revision=1,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            reason=reason,
            request_id=request_id,
        )
        await _commit_or_flush(db, secret, auto_commit=auto_commit)
        outcome = SecretMutationOutcome(
            slug=slug,
            status=SecretStatus.ACTIVE.value,
            credential_revision=1,
            policy_revision=1,
            request_id=request_id,
        )
        if auto_commit:
            _drain_post_commit_hooks(outcome)
        logger.info("secret_created", slug=slug)
        return secret

    @classmethod
    async def _apply_credential_change(
        cls,
        db: AsyncSession,
        slug: str,
        new_plaintext: str,
        *,
        event_type: str,
        auto_commit: bool,
        request_id: str | None,
        actor_user_id: UUID | None,
        workspace_id: UUID | None,
        reason: str | None,
        expected_credential_revision: int | None,
        actor_ref: str | None,
        validation_binding: ValidationBinding | None,
        validator: SecretCandidateValidator | None,
        validator_result: SecretValidationResult | None,
        activate: bool,
    ) -> ManagedSecret:
        # Idempotent retry reconciles without mutating twice.
        reconciled = await _reconcile_request_id(
            db, slug=slug, event_type=event_type, request_id=request_id
        )
        if reconciled is not None:
            return reconciled

        # Validate the candidate outside the write lock: no database
        # transaction is held across the provider probe.
        if validator is not None or validation_binding is not None:
            if validator is None or validation_binding is None:
                raise SecretFencedError(
                    "Candidate activation requires both a validation binding "
                    "and a validator."
                )
            if validation_binding.consumed:
                raise SecretFencedError("Validation binding was already consumed.")
            if validation_binding.slug != slug:
                raise SecretFencedError("Validation binding targets another secret.")
            probe_request = SecretValidationRequest(
                slug=slug,
                candidate_id=validation_binding.binding_id,
                expected_credential_revision=(
                    validation_binding.expected_credential_revision
                ),
                actor_ref=validation_binding.actor_ref,
                policy_revision=validation_binding.policy_revision,
            )
            probe = (
                validator_result
                if validator_result is not None
                else await validator(probe_request, new_plaintext)
            )
            if not probe.ok:
                logger.warning(
                    "secret_candidate_rejected", slug=slug, reason=probe.reason_code
                )
                raise SecretFencedError(
                    f"Candidate validation failed: {probe.reason_code}."
                )

        # Atomic section: lock, recheck versions, compare-and-swap.
        secret = await _load_secret(db, slug, for_update=True)
        if secret is None:
            logger.warning("secret_not_found_for_credential_change", slug=slug)
            raise SecretFencedError(f"Managed secret {slug!r} is missing.")

        current_status = _status_value(secret.status)
        if current_status == SecretStatus.ROTATED.value:
            raise SecretRepairRequiredError(
                f"Managed secret {slug!r} is ROTATED and needs the explicit "
                "reviewed repair path."
            )
        if current_status == SecretStatus.DELETED.value:
            raise SecretFencedError(
                f"Managed secret {slug!r} is deleted and cannot be mutated."
            )
        # Direct edits preserve a non-active lifecycle state (historical
        # behavior); rotation always activates the validated replacement
        # under the existing resolution contract.
        final_status = SecretStatus.ACTIVE if activate else secret.status

        current_cred = _credential_revision_of(secret.details)
        current_policy = _policy_revision_of(secret.details)
        if expected_credential_revision is not None and (
            expected_credential_revision != current_cred
        ):
            raise SecretFencedError(
                f"Expected credential revision {expected_credential_revision} "
                f"but the authoritative revision is {current_cred}."
            )
        if validation_binding is not None:
            if (
                validation_binding.expected_credential_revision != current_cred
                or validation_binding.policy_revision != current_policy
            ):
                raise SecretFencedError(
                    "Validation binding is stale: ownership, policy, "
                    "credentials, or candidate context changed."
                )
            if validation_binding.actor_ref != actor_ref and (
                validation_binding.actor_ref is not None or actor_ref is not None
            ):
                raise SecretFencedError(
                    "Validation binding actor does not match this activation."
                )
            validation_binding.consumed = True

        new_cred = current_cred + 1
        previous_status = current_status
        secret.ciphertext = new_plaintext
        secret.status = final_status
        secret.updated_at = datetime.now(timezone.utc)
        details = dict(secret.details or {})
        details[CREDENTIAL_REVISION_KEY] = new_cred
        if actor_ref is not None:
            details[VALIDATED_ACTOR_KEY] = actor_ref
        if request_id is not None:
            details[LAST_REQUEST_ID_KEY] = request_id
        secret.details = details
        final_status_value = _status_value(final_status)
        _record_secret_audit(
            db,
            event_type=event_type,
            slug=slug,
            old_status=previous_status,
            new_status=final_status_value,
            credential_revision=new_cred,
            policy_revision=current_policy,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            reason=reason,
            request_id=request_id,
        )
        await _commit_or_flush(db, secret, auto_commit=auto_commit)
        if auto_commit:
            _drain_post_commit_hooks(
                SecretMutationOutcome(
                    slug=slug,
                    status=final_status_value,
                    credential_revision=new_cred,
                    policy_revision=current_policy,
                    request_id=request_id,
                    outcome="committed",
                )
            )
        logger.info(
            "secret_credential_changed", slug=slug, credential_revision=new_cred
        )
        return secret

    @classmethod
    async def update_secret(
        cls,
        db: AsyncSession,
        slug: str,
        plaintext: str,
        *,
        auto_commit: bool = True,
        request_id: str | None = None,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
        expected_credential_revision: int | None = None,
        actor_ref: str | None = None,
    ) -> ManagedSecret | None:
        """Replace a secret value, advancing the credential revision.

        Returns ``None`` only when the slug does not exist (historical
        behavior). Stale revisions, failed validation, and ROTATED rows
        raise bounded diagnostics instead of mutating.
        """
        try:
            return await cls._apply_credential_change(
                db,
                slug,
                plaintext,
                event_type="secrets.updated",
                auto_commit=auto_commit,
                request_id=request_id,
                actor_user_id=actor_user_id,
                workspace_id=workspace_id,
                reason=reason,
                expected_credential_revision=expected_credential_revision,
                actor_ref=actor_ref,
                validation_binding=None,
                validator=None,
                validator_result=None,
                activate=False,
            )
        except SecretFencedError as exc:
            if "is missing" in str(exc):
                return None
            raise

    @classmethod
    async def rotate_secret(
        cls,
        db: AsyncSession,
        slug: str,
        new_plaintext: str,
        *,
        auto_commit: bool = True,
        request_id: str | None = None,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
        expected_credential_revision: int | None = None,
        actor_ref: str | None = None,
        validation_binding: ValidationBinding | None = None,
        validator: SecretCandidateValidator | None = None,
        validator_result: SecretValidationResult | None = None,
    ) -> ManagedSecret | None:
        """Rotate to a validated replacement, keeping it ``ACTIVE``.

        The replacement commits at ``credential_revision + 1`` together with
        a durable ``secrets.rotated`` audit/invalidation record, so the new
        value is immediately resolvable at its new revision. A failed
        validation or commit leaves the prior active secret unchanged.
        Returns ``None`` only when the slug does not exist.
        """
        try:
            return await cls._apply_credential_change(
                db,
                slug,
                new_plaintext,
                event_type="secrets.rotated",
                auto_commit=auto_commit,
                request_id=request_id,
                actor_user_id=actor_user_id,
                workspace_id=workspace_id,
                reason=reason,
                expected_credential_revision=expected_credential_revision,
                actor_ref=actor_ref,
                validation_binding=validation_binding,
                validator=validator,
                validator_result=validator_result,
                activate=True,
            )
        except SecretFencedError as exc:
            if "is missing" in str(exc):
                return None
            raise

    @classmethod
    async def activate_admitted_candidate(
        cls,
        db: AsyncSession,
        slug: str,
        candidate_plaintext: str,
        binding: ValidationBinding,
        validator: SecretCandidateValidator,
        *,
        auto_commit: bool = True,
        request_id: str | None = None,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
    ) -> ManagedSecret:
        """Validate outside the transaction, then atomically activate."""
        result = await cls._apply_credential_change(
            db,
            slug,
            candidate_plaintext,
            event_type="secrets.rotated",
            auto_commit=auto_commit,
            request_id=request_id,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            reason=reason or "admitted candidate activation",
            expected_credential_revision=binding.expected_credential_revision,
            actor_ref=binding.actor_ref,
            validation_binding=binding,
            validator=validator,
            validator_result=None,
            activate=True,
        )
        return result

    @classmethod
    async def repair_rotated_secret(
        cls,
        db: AsyncSession,
        slug: str,
        new_plaintext: str,
        validator: SecretCandidateValidator,
        *,
        auto_commit: bool = True,
        request_id: str | None = None,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
        actor_ref: str | None = None,
    ) -> ManagedSecret | None:
        """Explicit reviewed repair path for historical ``ROTATED`` rows.

        Revalidates the replacement through the injected probe boundary,
        then transitions the row to ``ACTIVE`` at the next credential
        revision with a ``secrets.repaired`` audit event. Never blindly
        reactivates: failed validation leaves the row untouched.
        """
        reconciled = await _reconcile_request_id(
            db, slug=slug, event_type="secrets.repaired", request_id=request_id
        )
        if reconciled is not None:
            return reconciled

        # Short metadata-only read with no row lock held.
        snapshot = await _load_secret(db, slug)
        if snapshot is None:
            logger.warning("secret_not_found_for_repair", slug=slug)
            return None
        if _status_value(snapshot.status) != SecretStatus.ROTATED.value:
            raise SecretFencedError(
                f"Managed secret {slug!r} is not ROTATED; repair is not required."
            )
        snapshot_cred = _credential_revision_of(snapshot.details)
        snapshot_policy = _policy_revision_of(snapshot.details)

        # Validate the replacement outside any write lock: no database
        # transaction is held across the provider probe.
        probe = await validator(
            SecretValidationRequest(
                slug=slug,
                candidate_id=f"repair-{uuid4().hex}",
                expected_credential_revision=snapshot_cred,
                actor_ref=actor_ref,
                policy_revision=snapshot_policy,
            ),
            new_plaintext,
        )
        if not probe.ok:
            logger.warning(
                "secret_repair_rejected", slug=slug, reason=probe.reason_code
            )
            raise SecretFencedError(f"Repair validation failed: {probe.reason_code}.")

        # Atomic section: lock, recheck versions, then mutate.
        secret = await _load_secret(db, slug, for_update=True)
        if secret is None:
            logger.warning("secret_not_found_for_repair", slug=slug)
            return None
        if _status_value(secret.status) != SecretStatus.ROTATED.value:
            raise SecretFencedError(
                f"Managed secret {slug!r} is not ROTATED; repair is not required."
            )
        current_cred = _credential_revision_of(secret.details)
        current_policy = _policy_revision_of(secret.details)
        if current_cred != snapshot_cred or current_policy != snapshot_policy:
            raise SecretFencedError(
                "Repair validation is stale: ownership, policy, credentials, "
                "or candidate context changed."
            )

        new_cred = current_cred + 1
        secret.ciphertext = new_plaintext
        secret.status = SecretStatus.ACTIVE
        secret.updated_at = datetime.now(timezone.utc)
        details = dict(secret.details or {})
        details[CREDENTIAL_REVISION_KEY] = new_cred
        details[REPAIR_OF_KEY] = SecretStatus.ROTATED.value
        if actor_ref is not None:
            details[VALIDATED_ACTOR_KEY] = actor_ref
        if request_id is not None:
            details[LAST_REQUEST_ID_KEY] = request_id
        secret.details = details
        _record_secret_audit(
            db,
            event_type="secrets.repaired",
            slug=slug,
            old_status=SecretStatus.ROTATED.value,
            new_status=SecretStatus.ACTIVE.value,
            credential_revision=new_cred,
            policy_revision=current_policy,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            reason=reason or "reviewed repair of historical ROTATED row",
            request_id=request_id,
        )
        await _commit_or_flush(db, secret, auto_commit=auto_commit)
        if auto_commit:
            _drain_post_commit_hooks(
                SecretMutationOutcome(
                    slug=slug,
                    status=SecretStatus.ACTIVE.value,
                    credential_revision=new_cred,
                    policy_revision=current_policy,
                    request_id=request_id,
                    outcome="repaired",
                )
            )
        logger.info("secret_repaired", slug=slug, credential_revision=new_cred)
        return secret

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
        auto_commit: bool = True,
    ) -> ManagedSecret | None:
        """Change lifecycle status and record a redacted audit event.

        Metadata-only changes advance the policy revision, never the
        credential revision. Direct reactivation of a ``ROTATED`` row is
        rejected here so historical values flow through the reviewed repair
        path instead of being blindly reactivated.
        """
        reconciled = await _reconcile_request_id(
            db, slug=slug, event_type="secrets.status.changed", request_id=request_id
        )
        if reconciled is not None:
            return reconciled

        secret = await _load_secret(db, slug, for_update=True)

        if not secret:
            logger.warning("secret_not_found_for_status_change", slug=slug)
            return None

        previous_status = _status_value(secret.status)
        new_status = status.value if isinstance(status, SecretStatus) else str(status)

        if (
            previous_status == SecretStatus.ROTATED.value
            and new_status == SecretStatus.ACTIVE.value
        ):
            raise SecretRepairRequiredError(
                f"Managed secret {slug!r} is ROTATED; use the explicit reviewed "
                "repair path instead of a direct status change."
            )

        current_cred = _credential_revision_of(secret.details)
        new_policy = _policy_revision_of(secret.details) + 1
        secret.status = status
        secret.updated_at = datetime.now(timezone.utc)
        details = dict(secret.details or {})
        details[POLICY_REVISION_KEY] = new_policy
        if request_id is not None:
            details[LAST_REQUEST_ID_KEY] = request_id
        secret.details = details
        _record_secret_audit(
            db,
            event_type="secrets.status.changed",
            slug=slug,
            old_status=previous_status,
            new_status=new_status,
            credential_revision=current_cred,
            policy_revision=new_policy,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            reason=reason,
            request_id=request_id,
        )
        await _commit_or_flush(db, secret, auto_commit=auto_commit)
        if auto_commit:
            _drain_post_commit_hooks(
                SecretMutationOutcome(
                    slug=slug,
                    status=new_status,
                    credential_revision=current_cred,
                    policy_revision=new_policy,
                    request_id=request_id,
                )
            )
        logger.info("secret_status_changed", slug=slug, new_status=new_status)
        return secret

    @classmethod
    async def delete_secret(
        cls,
        db: AsyncSession,
        slug: str,
        *,
        auto_commit: bool = True,
        request_id: str | None = None,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
        reason: str | None = None,
    ) -> bool:
        """Delete a secret only when no consumer references it.

        The complete server-side inventory (all scopes and all supported
        consumer types) gates deletion; a caller-filtered usage display is
        never sufficient. The row is locked transactionally so a concurrent
        attachment either survives (deletion blocked) or fails against the
        deleted row -- never a dangling newly admitted binding. The durable
        audit event preserves tombstone identity; decrypted values are never
        retained in audit rows.
        """
        secret = await _load_secret(db, slug, for_update=True)

        if not secret:
            logger.warning("secret_not_found_for_delete", slug=slug)
            return False

        consumers = await cls.collect_secret_consumers(db, slug)
        if consumers:
            logger.warning(
                "secret_delete_blocked_by_consumers",
                slug=slug,
                consumer_count=len(consumers),
            )
            return False

        status_before = _status_value(secret.status)
        cred = _credential_revision_of(secret.details)
        policy = _policy_revision_of(secret.details)
        _record_secret_audit(
            db,
            event_type="secrets.deleted",
            slug=slug,
            old_status=status_before,
            new_status=SecretStatus.DELETED.value,
            credential_revision=cred,
            policy_revision=policy,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
            reason=reason,
            request_id=request_id,
        )
        await db.delete(secret)
        if auto_commit:
            await db.commit()
            _drain_post_commit_hooks(
                SecretMutationOutcome(
                    slug=slug,
                    status=SecretStatus.DELETED.value,
                    credential_revision=cred,
                    policy_revision=policy,
                    request_id=request_id,
                    outcome="deleted",
                )
            )
        else:
            await db.flush()
        logger.info("secret_deleted", slug=slug)
        return True

    @classmethod
    async def assert_secret_deletable(cls, db: AsyncSession, slug: str) -> None:
        """Raise :class:`SecretInUseError` when deletion would be blocked."""
        consumers = await cls.collect_secret_consumers(db, slug)
        if consumers:
            raise SecretInUseError(
                f"Managed secret {slug!r} has {len(consumers)} consumer(s)."
            )

    @classmethod
    async def run_in_secret_attachment(
        cls,
        db: AsyncSession,
        slug: str,
        attach: Callable[[], Awaitable[Any]],
        *,
        expected_credential_revision: int | None = None,
    ) -> Any:
        """Attach a consumer reference transactionally against deletion.

        Locks the secret row, verifies it is ``ACTIVE`` at the expected
        revision, then runs ``attach`` on the same session so either the
        protected reference survives or attachment fails -- never a dangling
        newly admitted binding.
        """
        secret = await _load_secret(db, slug, for_update=True)
        if secret is None or _status_value(secret.status) != SecretStatus.ACTIVE.value:
            raise SecretFencedError(
                f"Managed secret {slug!r} is not available for attachment."
            )
        if expected_credential_revision is not None and (
            _credential_revision_of(secret.details) != expected_credential_revision
        ):
            raise SecretFencedError(
                f"Managed secret {slug!r} changed revision during attachment."
            )
        return await attach()

    @classmethod
    async def list_metadata(cls, db: AsyncSession) -> Sequence[Row]:
        """List all secret metadata without leaking plaintext."""
        # Using deferred column loading or just avoiding `ciphertext` access
        # Since `ciphertext` is encrypted at rest, accessing it returns the decrypt,
        # so we MUST avoid reading it or just not returning it in a Pydantic model.
        # Returning SQLAlchemy model is safe as long as the caller doesn't read `ciphertext`.
        # Even safer:
        # We can yield dicts or specific metadata objects.
        result = await db.execute(
            select(
                ManagedSecret.id,
                ManagedSecret.slug,
                ManagedSecret.status,
                ManagedSecret.details,
                ManagedSecret.created_at,
                ManagedSecret.updated_at
            )
        )
        return result.all()

    @classmethod
    def _iter_nested_refs(cls, value: Any) -> Sequence[str]:
        found: list[str] = []
        stack: list[Any] = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                if item.startswith("db://") and len(item) > 5:
                    found.append(item)
            elif isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, (list, tuple)):
                stack.extend(item)
        return found

    @classmethod
    async def collect_secret_consumers(
        cls, db: AsyncSession, slug: str
    ) -> list[dict[str, Any]]:
        """Complete server-side consumer inventory for deletion protection.

        Covers every supported typed/nested reference: Settings overrides
        (all scopes), Provider Profile ``secret_refs``, and repository
        connection ``credential_config``. Metadata only: consumer identity
        is scoped to type/name, never secret contents.
        """
        secret_ref = _secret_ref_for(slug)
        consumers: list[dict[str, Any]] = []

        override_rows = await db.execute(
            select(
                SettingsOverride.key,
                SettingsOverride.scope,
                SettingsOverride.value_json,
            )
        )
        for key, scope, value_json in override_rows:
            if secret_ref not in cls._iter_nested_refs(value_json):
                continue
            scope_label = "Workspace" if str(scope) == "workspace" else "User"
            consumers.append(
                {
                    "consumerType": "setting_override",
                    "objectName": f"{scope_label} setting {key}",
                    "reference": secret_ref,
                    "scope": str(scope),
                    "settingKey": key,
                }
            )

        try:
            from api_service.db.models import (  # noqa: PLC0415 - avoid import cycle
                ManagedAgentProviderProfile,
                RepositoryConnectionRecord,
            )
        except Exception:  # noqa: BLE001 - inventory degrades to overrides only
            logger.warning("secret_consumer_inventory_partial", slug=slug)
            return consumers

        try:
            profile_rows = await db.execute(
                select(
                    ManagedAgentProviderProfile.profile_id,
                    ManagedAgentProviderProfile.secret_refs,
                )
            )
            for profile_id, secret_refs in profile_rows:
                refs: list[str] = []
                if isinstance(secret_refs, dict):
                    for candidate in secret_refs.values():
                        refs.extend(cls._iter_nested_refs(candidate))
                if secret_ref not in refs:
                    continue
                consumers.append(
                    {
                        "consumerType": "provider_profile",
                        "objectName": f"Provider profile {profile_id}",
                        "reference": secret_ref,
                        "scope": "system",
                        "profileId": profile_id,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - table may not exist yet
            logger.warning(
                "secret_consumer_inventory_profile_skipped",
                slug=slug,
                error=str(exc),
            )

        try:
            connection_rows = await db.execute(
                select(
                    RepositoryConnectionRecord.connection_id,
                    RepositoryConnectionRecord.credential_config,
                )
            )
            for connection_id, credential_config in connection_rows:
                if secret_ref not in cls._iter_nested_refs(credential_config):
                    continue
                consumers.append(
                    {
                        "consumerType": "repository_connection",
                        "objectName": f"Repository connection {connection_id}",
                        "reference": secret_ref,
                        "scope": "system",
                        "connectionId": connection_id,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - table may not exist yet
            logger.warning(
                "secret_consumer_inventory_connection_skipped",
                slug=slug,
                error=str(exc),
            )

        return consumers

    @classmethod
    async def list_secret_usage(
        cls,
        db: AsyncSession,
        slug: str,
        *,
        workspace_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """List caller-filtered metadata-only consumers for display.

        Display filtering is a presentation concern only: deletion
        protection always uses :meth:`collect_secret_consumers`.
        """
        secret_ref = f"db://{slug}"
        resolved_workspace_id = workspace_id or _DEFAULT_SETTINGS_SUBJECT_ID
        resolved_user_id = user_id or _DEFAULT_SETTINGS_SUBJECT_ID
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
    async def resolve_secret(
        cls, db: AsyncSession, slug: str
    ) -> dict[str, Any] | None:
        """Authoritative single-read resolution: value plus fenced revisions."""
        result = await db.execute(select(ManagedSecret).where(ManagedSecret.slug == slug))
        secret = result.scalar_one_or_none()
        if secret is None or _status_value(secret.status) != SecretStatus.ACTIVE.value:
            logger.warning("active_secret_not_found", slug=slug)
            return None
        # Single authoritative read: ciphertext, status, and revisions come
        # from the same row. Callers must echo credential_revision back via
        # expected_credential_revision on fenced paths.
        return {
            "value": secret.ciphertext,
            "status": _status_value(secret.status),
            "credential_revision": _credential_revision_of(secret.details),
            "policy_revision": _policy_revision_of(secret.details),
        }

    @classmethod
    async def get_secret(
        cls,
        db: AsyncSession,
        slug: str,
        *,
        expected_credential_revision: int | None = None,
    ) -> str | None:
        """Retrieve the plaintext value of an ACTIVE secret.

        When ``expected_credential_revision`` is given, the value resolves
        only when the authoritative row is ACTIVE *at that revision* in the
        same read; a metadata lookup followed by an unconstrained
        latest-value lookup is never sufficient.
        """
        resolved = await cls.resolve_secret(db, slug)
        if resolved is None:
            return None
        if expected_credential_revision is not None and (
            resolved["credential_revision"] != expected_credential_revision
        ):
            logger.warning(
                "secret_revision_fenced", slug=slug, code=DIAG_FENCED
            )
            return None
        return resolved["value"]

    @classmethod
    async def validate_secret_ref(
        cls,
        db: AsyncSession,
        slug: str,
        *,
        expected_credential_revision: int | None = None,
    ) -> dict[str, Any]:
        """Return redacted metadata-only validation diagnostics for a managed secret."""
        checked_at = datetime.now(timezone.utc).isoformat()
        # Metadata-only authoritative read: status plus revision counters,
        # never ciphertext. Revision fencing rides on the same row.
        result = await db.execute(
            select(ManagedSecret.status, ManagedSecret.details).where(
                ManagedSecret.slug == slug
            )
        )
        found = result.one_or_none()

        if found is None:
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

        status, details = found
        secret_status = _status_value(status)
        credential_revision = _credential_revision_of(details)
        if expected_credential_revision is not None and (
            expected_credential_revision != credential_revision
        ):
            return {
                "valid": False,
                "status": secret_status,
                "checkedAt": checked_at,
                "credentialRevision": credential_revision,
                "diagnostics": [
                    {
                        "code": DIAG_FENCED,
                        "message": "Managed secret changed revision.",
                        "severity": "error",
                    }
                ],
            }
        if secret_status == SecretStatus.ACTIVE.value:
            payload: dict[str, Any] = {
                "valid": True,
                "status": secret_status,
                "checkedAt": checked_at,
                "credentialRevision": credential_revision,
                "diagnostics": [
                    {
                        "code": "secret_ref_resolvable",
                        "message": "Managed secret is active.",
                        "severity": "info",
                    }
                ],
            }
            return payload

        code = (
            DIAG_REPAIR_REQUIRED
            if secret_status == SecretStatus.ROTATED.value
            else DIAG_UNRESOLVED
        )
        return {
            "valid": False,
            "status": secret_status,
            "checkedAt": checked_at,
            "credentialRevision": credential_revision,
            "diagnostics": [
                {
                    "code": code,
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
        auto_commit: bool = True,
        request_id: str | None = None,
        actor_user_id: UUID | None = None,
        workspace_id: UUID | None = None,
    ) -> int:
        """
        Migrate legacy .env values under the unified revision/audit contract.

        All rows commit together in one transaction: new keys are created at
        credential revision 1, re-admitted non-active keys rotate through the
        same revision advance and audit shape as any other rotation, and
        metadata-only touch-ups advance the policy revision. By default,
        existing active secrets are skipped.
        """
        imported_count = 0
        for key, value in env_dict.items():
            with db.sync_session.no_autoflush:
                result = await db.execute(
                    select(ManagedSecret).where(ManagedSecret.slug == key)
                )
            existing = result.scalar_one_or_none()

            if existing:
                if existing.status == SecretStatus.ACTIVE and not overwrite_active:
                    continue  # Skip overriding already active managed secrets
                if _status_value(existing.status) == SecretStatus.ACTIVE.value:
                    await cls._apply_credential_change(
                        db,
                        key,
                        value,
                        event_type="secrets.imported",
                        auto_commit=False,
                        request_id=request_id,
                        actor_user_id=actor_user_id,
                        workspace_id=workspace_id,
                        reason="legacy .env import",
                        expected_credential_revision=None,
                        actor_ref=None,
                        validation_binding=None,
                        validator=None,
                        validator_result=None,
                        activate=True,
                    )
                    imported_count += 1
                    continue
                # Non-active rows are re-admitted through creation-equivalent
                # semantics: activate at the next credential revision with a
                # durable import audit event. ROTATED rows stay on the repair
                # path and are never silently reactivated here.
                if _status_value(existing.status) == SecretStatus.ROTATED.value:
                    logger.warning("secret_import_skipped_rotated", slug=key)
                    continue
                # Per-key idempotency: a retried import with the same
                # request_id reconciles instead of advancing twice.
                reconciled = await _reconcile_request_id(
                    db,
                    slug=key,
                    event_type="secrets.imported",
                    request_id=request_id,
                )
                if reconciled is not None:
                    continue
                now = datetime.now(timezone.utc)
                old_status = _status_value(existing.status)
                details = dict(existing.details or {})
                details[CREDENTIAL_REVISION_KEY] = (
                    _credential_revision_of(details) + 1
                )
                details.update(
                    {"imported_from": ".env", "migrated_at": now.isoformat()}
                )
                existing.ciphertext = value
                existing.status = SecretStatus.ACTIVE
                existing.details = details
                existing.updated_at = now
                _record_secret_audit(
                    db,
                    event_type="secrets.imported",
                    slug=key,
                    old_status=old_status,
                    new_status=SecretStatus.ACTIVE.value,
                    credential_revision=details[CREDENTIAL_REVISION_KEY],
                    policy_revision=_policy_revision_of(details),
                    actor_user_id=actor_user_id,
                    workspace_id=workspace_id,
                    reason="legacy .env import",
                    request_id=request_id,
                )
                imported_count += 1
            else:
                now = datetime.now(timezone.utc)
                db.add(
                    ManagedSecret(
                        slug=key,
                        ciphertext=value,
                        status=SecretStatus.ACTIVE,
                        details={
                            "imported_from": ".env",
                            "migrated_at": now.isoformat(),
                            CREDENTIAL_REVISION_KEY: 1,
                            POLICY_REVISION_KEY: 1,
                        },
                    )
                )
                _record_secret_audit(
                    db,
                    event_type="secrets.imported",
                    slug=key,
                    old_status=None,
                    new_status=SecretStatus.ACTIVE.value,
                    credential_revision=1,
                    policy_revision=1,
                    actor_user_id=actor_user_id,
                    workspace_id=workspace_id,
                    reason="legacy .env import",
                    request_id=request_id,
                )
                imported_count += 1

        if imported_count > 0:
            if auto_commit:
                await db.commit()
                _drain_post_commit_hooks(
                    SecretMutationOutcome(
                        slug=f"{imported_count} keys",
                        status=SecretStatus.ACTIVE.value,
                        credential_revision=0,
                        policy_revision=0,
                        request_id=request_id,
                        outcome="imported",
                    )
                )
                logger.info("secrets_imported_from_env", count=imported_count)
            else:
                await db.flush()

        return imported_count
