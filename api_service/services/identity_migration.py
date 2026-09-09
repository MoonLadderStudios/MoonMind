"""Operator-only identity migration dry-run/apply (K3, #4119).

Additive migration tooling for the Keycloak-to-target identity cutover. Reads
reviewed source-provider-to-issuer mappings plus verified target enrollment
evidence, binds every apply to the inspected database/schema/configuration and
mapping digest, and stays idempotent and transactionally resumable with
explicit partial-result reporting.

Safety properties:

* Dry-run performs no writes (read-only transaction, rolled back).
* Apply is bound to a preflight digest over (schema revision, provider
  mapping, enrollment evidence, database fingerprint). Changed input or
  concurrent identity edits invalidate the preflight instead of applying stale
  assumptions (:class:`StalePreflightError`).
* Apply is idempotent and resumable: each row commits durably as it
  completes with a progressive ledger checkpoint, already-migrated rows
  are skipped, and reruns converge on one safe result.
* Concurrent applies fail fast on the ``identity_migration_runs`` ledger:
  a second apply with the same digest while one is ``in_progress`` fails
  closed with :class:`ConcurrentApplyError` (PostgreSQL advisory claim
  when available, committed ledger row otherwise); a rerun after
  completion replays the recorded result.
* Ambiguous rows (duplicates, missing mappings, duplicate emails, orphan
  profiles, default-ID mismatches, unresolved ownership) block the affected
  rows only — never automatic email linking — and unrelated owners and data
  are preserved. Reports are sanitized (no identity exports, password hashes,
  or tokens).

Rollback compatibility: the migration is additive (new tables only; legacy
``User.oidc_*`` columns and historical Alembic revisions are retained). The
last known-good application keeps reading the database until cutover is
accepted; downgrade drops only the new tables after asserting they hold no
migrated authority still referenced by the resolver.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    IdentityMigrationRun,
    User,
    UserExternalIdentity,
)
from api_service.services.identity_service import (
    ControlledEnrollmentRequiredError,
    IdentityReport,
    IdentityValidationError,
    collect_identity_report,
    ensure_profile_transactional,
    ownership_snapshot,
    resolve_user_id_for_identity,
    validate_external_identity,
)

logger = logging.getLogger(__name__)

SCHEMA_REVISION = "374_identity_mapping_k3"


class MigrationAuthorizationError(PermissionError):
    """Operator-only command invoked without explicit operator authorization."""


class StalePreflightError(RuntimeError):
    """Inspected database/mapping changed since the preflight digest."""


class ConcurrentApplyError(RuntimeError):
    """Another apply holds the same preflight digest."""


def _advisory_lock_key(digest: str) -> int:
    """Derive a signed-64-bit PostgreSQL advisory-lock key for a digest."""
    return int(
        hashlib.sha256(f"identity-migration:{digest}".encode()).hexdigest()[:15], 16
    )


async def _try_nonblocking_ledger_claim(
    session: AsyncSession, digest: str
) -> bool | None:
    """Try a fail-fast PostgreSQL advisory claim for the preflight digest.

    Returns True when the claim was acquired, False when another apply
    holds it, and None when the dialect has no advisory locks (SQLite and
    other hermetic test backends) so the caller falls back to the
    committed ledger-row path. A failed probe rolls back only its own
    savepoint, never the caller's work. The session-level lock survives
    the per-row commits below and is released in the apply ``finally``.
    """
    probe = await session.begin_nested()
    try:
        claimed = (
            await session.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": _advisory_lock_key(digest)},
            )
        ).scalar()
        await probe.commit()
    except Exception:
        await probe.rollback()
        return None
    return bool(claimed)


async def _release_ledger_claim(session: AsyncSession, digest: str) -> None:
    """Release a claim acquired by :func:`_try_nonblocking_ledger_claim`."""
    try:
        await session.execute(
            text("SELECT pg_advisory_unlock(:key)"),
            {"key": _advisory_lock_key(digest)},
        )
    except Exception:
        logger.debug("identity migration advisory unlock skipped", exc_info=True)
    finally:
        # End the unlock statement's transaction without committing caller
        # state: session-level advisory locks act immediately, so a rollback
        # cannot re-acquire the released claim.
        try:
            await session.rollback()
        except Exception:
            logger.debug("identity migration unlock rollback skipped", exc_info=True)


@dataclass
class MigrationRow:
    """One reviewed source-provider identity targeted at one MoonMind UUID."""

    user_id: UUID
    source_provider: str
    issuer: str
    subject: str


@dataclass
class PreflightResult:
    """Read-only preflight outcome; the digest binds a later apply."""

    digest: str
    schema_revision: str
    db_fingerprint: str
    rows: list[MigrationRow] = field(default_factory=list)
    report: IdentityReport | None = None
    before_snapshot: dict[str, Any] | None = None


@dataclass
class ApplyResult:
    """Per-row apply dispositions plus the after-reconciliation snapshot."""

    digest: str
    migrated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    blocked: list[dict[str, Any]] = field(default_factory=list)
    after_snapshot: dict[str, Any] | None = None

    def to_sanitized_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "migrated": list(self.migrated),
            "skipped": list(self.skipped),
            "blocked": [dict(b) for b in self.blocked],
            "after_snapshot": dict(self.after_snapshot or {}),
        }


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


async def database_fingerprint(session: AsyncSession) -> str:
    """Fingerprint the inspected identity state (counts plus sorted UUIDs)."""
    user_ids = sorted(
        str(u)
        for u in (await session.execute(select(User.id))).scalars().all()
    )
    pairs = sorted(
        f"{m.issuer}\x00{m.subject}\x00{m.user_id}"
        for m in (await session.execute(select(UserExternalIdentity))).scalars().all()
    )
    profile_count = (
        await session.execute(select(func.count()).select_from(User.__table__.metadata.tables["user_profile"]))
    ).scalar() or 0
    canonical = _canonical_json(
        {"users": user_ids, "identities": pairs, "profiles": int(profile_count)}
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def compute_preflight_digest(
    *,
    schema_revision: str,
    provider_to_issuer: dict[str, str],
    enrollment_evidence: dict[str, Any] | None,
    db_fingerprint: str,
) -> str:
    """Bind schema, mapping, enrollment evidence, and DB state into one digest."""
    canonical = _canonical_json(
        {
            "schema_revision": schema_revision,
            "provider_to_issuer": dict(sorted((provider_to_issuer or {}).items())),
            "enrollment_evidence": enrollment_evidence or {},
            "db_fingerprint": db_fingerprint,
        }
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def parse_migration_rows(
    *,
    provider_to_issuer: dict[str, str],
    enrollment_evidence: dict[str, Any] | None,
) -> list[MigrationRow]:
    """Parse verified target enrollment evidence into migration rows.

    ``enrollment_evidence`` maps ``str(user_id)`` to ``{"issuer": ...,
    "subject": ..., "source_provider": ...}``. Entries whose source provider
    has no reviewed issuer target are refused here (unresolved mapping) rather
    than guessed. Identities keep exact case-sensitive subject semantics and
    full issuer URIs — never truncated, never derived from email.
    """
    rows: list[MigrationRow] = []
    for user_id_text, entry in (enrollment_evidence or {}).items():
        try:
            user_id = UUID(str(user_id_text))
        except (ValueError, TypeError, AttributeError) as exc:
            raise IdentityValidationError(f"invalid user id {user_id_text!r}") from exc
        if not isinstance(entry, dict):
            raise IdentityValidationError(f"invalid enrollment entry for {user_id}")
        issuer = entry.get("issuer") or provider_to_issuer.get(
            str(entry.get("source_provider") or ""), ""
        )
        subject = entry.get("subject", "")
        source_provider = str(entry.get("source_provider") or "")
        if not issuer:
            raise ControlledEnrollmentRequiredError(
                "unresolved_mapping",
                f"user {user_id} source provider {source_provider!r} has no "
                "reviewed issuer target; explicit mapping required",
            )
        issuer, subject = validate_external_identity(str(issuer), str(subject))
        rows.append(
            MigrationRow(
                user_id=user_id,
                source_provider=source_provider,
                issuer=issuer,
                subject=subject,
            )
        )
    return rows


async def preflight(
    session: AsyncSession,
    *,
    provider_to_issuer: dict[str, str],
    enrollment_evidence: dict[str, Any] | None = None,
    default_email: str | None = None,
) -> PreflightResult:
    """Inspect without writing; the digest binds a later apply.

    Runs read-only: all inspected state is loaded in a transaction that the
    caller rolls back (or never commits). Raises on invalid enrollment
    evidence; reports ambiguous rows as blockers without linking by email.
    """
    fingerprint = await database_fingerprint(session)
    rows = parse_migration_rows(
        provider_to_issuer=provider_to_issuer,
        enrollment_evidence=enrollment_evidence,
    )
    report = await collect_identity_report(
        session,
        provider_to_issuer=provider_to_issuer,
        default_email=default_email,
        planned_user_ids={row.user_id for row in rows},
    )
    before = await ownership_snapshot(session)
    digest = compute_preflight_digest(
        schema_revision=SCHEMA_REVISION,
        provider_to_issuer=provider_to_issuer,
        enrollment_evidence=enrollment_evidence,
        db_fingerprint=fingerprint,
    )
    await session.rollback()
    return PreflightResult(
        digest=digest,
        schema_revision=SCHEMA_REVISION,
        db_fingerprint=fingerprint,
        rows=rows,
        report=report,
        before_snapshot=before,
    )


async def _apply_one_row(
    session: AsyncSession, row: MigrationRow
) -> tuple[str, dict[str, Any] | None]:
    """Apply one row in a savepoint; returns (disposition, blocked_detail)."""
    nested = await session.begin_nested()
    try:
        user = await session.get(User, row.user_id)
        if user is None:
            await nested.rollback()
            return "blocked", {
                "code": "unresolved_ownership",
                "user_id": str(row.user_id),
                "detail": "target user does not exist; row blocked, data preserved",
            }
        winner = await resolve_user_id_for_identity(session, row.issuer, row.subject)
        if winner is not None:
            if winner != row.user_id:
                await nested.rollback()
                return "blocked", {
                    "code": "duplicate_mapping",
                    "user_id": str(row.user_id),
                    "detail": "identity already bound to a different user; "
                    "manual enrollment required, no merge",
                }
            # Already bound to the requested user: converge like the
            # new-binding path by ensuring the required profile before
            # reporting success, so reruns over partially prepared data
            # cannot report success while the user still has no profile.
            await ensure_profile_transactional(session, row.user_id)
            await nested.commit()
            return "skipped", None
        session.add(
            UserExternalIdentity(
                id=uuid4(), user_id=row.user_id, issuer=row.issuer, subject=row.subject
            )
        )
        try:
            await session.flush()
        except IntegrityError:
            await nested.rollback()
            winner = await resolve_user_id_for_identity(session, row.issuer, row.subject)
            if winner == row.user_id:
                return "skipped", None
            return "blocked", {
                "code": "duplicate_mapping",
                "user_id": str(row.user_id),
                "detail": "concurrent binding won the race for a different "
                "user; manual enrollment required",
            }
        await ensure_profile_transactional(session, row.user_id)
        await nested.commit()
        return "migrated", None
    except Exception:
        await nested.rollback()
        raise


async def apply_migration(
    session: AsyncSession,
    preflight_result: PreflightResult,
    *,
    operator_authorized: bool,
    provider_to_issuer: dict[str, str],
    enrollment_evidence: dict[str, Any] | None = None,
) -> ApplyResult:
    """Apply a preflight-bound migration idempotently and resumably.

    Requires ``operator_authorized=True`` (operator-only command). Re-inspects
    the database and refuses with :class:`StalePreflightError` when the
    fingerprint, schema revision, mapping, or enrollment evidence changed
    since preflight. Interruption-safe: each row commits durably as it
    completes with a progressive ledger checkpoint, and already-migrated
    rows are skipped, so a rerun with a fresh preflight resumes from
    validated progress and converges on one safe result with explicit
    partial-result reporting.
    """
    if not operator_authorized:
        raise MigrationAuthorizationError(
            "identity migration apply requires explicit operator authorization"
        )
    fresh_fingerprint = await database_fingerprint(session)
    expected = compute_preflight_digest(
        schema_revision=preflight_result.schema_revision,
        provider_to_issuer=provider_to_issuer,
        enrollment_evidence=enrollment_evidence,
        db_fingerprint=fresh_fingerprint,
    )
    if preflight_result.schema_revision != SCHEMA_REVISION:
        raise StalePreflightError(
            f"schema revision changed since preflight "
            f"({preflight_result.schema_revision} != {SCHEMA_REVISION})"
        )
    if expected != preflight_result.digest:
        raise StalePreflightError(
            "database, mapping, or enrollment evidence changed since preflight; "
            "re-run dry-run and review the new report instead of applying "
            "stale assumptions"
        )

    # Fail fast on a truly concurrent apply instead of blocking on the
    # ledger unique index until the holder finishes or a DB timeout fires.
    advisory_held = await _try_nonblocking_ledger_claim(
        session, preflight_result.digest
    )
    if advisory_held is False:
        raise ConcurrentApplyError(
            "another apply holds this preflight digest; refusing concurrent apply"
        )
    try:
        try:
            run = IdentityMigrationRun(
                preflight_digest=preflight_result.digest,
                status="in_progress",
                result_json={},
            )
            session.add(run)
            await session.flush()
            # Durably publish the in_progress claim before touching rows so
            # a concurrent apply observes it instead of duplicating work.
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = (
                await session.execute(
                    select(IdentityMigrationRun).where(
                        IdentityMigrationRun.preflight_digest
                        == preflight_result.digest
                    )
                )
            ).scalars().first()
            if existing is None:  # pragma: no cover - ledger race guard
                raise ConcurrentApplyError(
                    "migration ledger conflicted without a winner"
                )
            if existing.status == "in_progress":
                raise ConcurrentApplyError(
                    "another apply holds this preflight digest; "
                    "refusing concurrent apply"
                )
            stored = dict(existing.result_json or {})
            return ApplyResult(
                digest=preflight_result.digest,
                migrated=list(stored.get("migrated", [])),
                skipped=list(stored.get("skipped", [])),
                blocked=list(stored.get("blocked", [])),
                after_snapshot=dict(stored.get("after_snapshot", {})),
            )

        result = ApplyResult(digest=preflight_result.digest)
        for row in preflight_result.rows:
            disposition, blocked = await _apply_one_row(session, row)
            if disposition == "migrated":
                result.migrated.append(str(row.user_id))
            elif disposition == "skipped":
                result.skipped.append(str(row.user_id))
            else:
                result.blocked.append(
                    {"user_id": str(row.user_id), **(blocked or {"code": "blocked"})}
                )
            # Durable per-row progress: checkpoint the partial result into
            # the ledger and commit before the next row, so an interruption
            # or a later unexpected row failure preserves validated work
            # instead of rolling every earlier mapping back to zero.
            checkpoint = (
                await session.execute(
                    select(IdentityMigrationRun).where(
                        IdentityMigrationRun.preflight_digest
                        == preflight_result.digest
                    )
                )
            ).scalars().first()
            if checkpoint is not None:
                checkpoint.result_json = result.to_sanitized_dict()
                await session.flush()
            await session.commit()
        result.after_snapshot = await ownership_snapshot(session)
        if preflight_result.before_snapshot is not None:
            before_ids = set((preflight_result.before_snapshot.get("user_ids") or []))
            after_ids = set((result.after_snapshot.get("user_ids") or []))
            if before_ids != after_ids:
                raise RuntimeError(
                    "migration changed the retained user set; refusing to record success"
                )
        run_row = (
            await session.execute(
                select(IdentityMigrationRun).where(
                    IdentityMigrationRun.preflight_digest == preflight_result.digest
                )
            )
        ).scalars().first()
        if run_row is not None:
            run_row.status = "complete"
            run_row.result_json = result.to_sanitized_dict()
            await session.flush()
        await session.commit()
        return result
    finally:
        if advisory_held:
            await _release_ledger_claim(session, preflight_result.digest)


SessionFactory = Callable[[], Awaitable[AsyncSession]]
