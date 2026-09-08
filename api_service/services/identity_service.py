"""K3 production identity authority (MoonLadderStudios/MoonMind#4119).

Single active resolver mapping a verified OIDC ``(issuer, subject)`` pair —
or an explicitly namespaced trusted-proxy identity — to exactly one
MoonMind ``User.id``. Consumed by account enrollment/session work (#4117
configuration, follow-up session issues); it owns no session, credential, or
provider-profile behavior.

Contract (mirrors the K2 qualification semantics in
``moonmind/security/omnigent_auth_qualification.py`` against the real tables):

* ``issuer`` is the full verified issuer URI (never truncated into the legacy
  32-character provider field); trusted-proxy identities use the explicit
  ``proxy:<namespace>:<stable-id>`` form.
* ``subject`` matches exactly and case-sensitively; identity is never derived
  from email, display name, or login name.
* Same subject across issuers resolves to distinct users; same email across
  users never merges accounts; email/login renames keep the UUID and the
  current local ``is_active``/``is_superuser`` flags.
* Account creation plus external-identity binding plus profile creation is one
  uniqueness-protected transaction; concurrent first logins produce one
  mapping and one profile and never promote an inactive or ordinary user.
* The legacy ``User.oidc_provider/oidc_subject`` columns are frozen read-only
  evidence and are never consulted here.

Ownership trace (tables/fields carrying a user principal, preserved by
migration — UUIDs are never rewritten and Temporal histories are never
rewritten or remapped to the first administrator):

* SQL foreign keys to ``user.id``: ``user_profile.user_id``,
  ``recurring_workflow_definitions.owner_user_id``,
  ``omnigent_policies.owner_user_id``, ``workflow_runs.requested_by_user_id``,
  ``workflow_runs.created_by``, ``preset_versions.reviewed_by``,
  ``presets.created_by``, ``preset_favorites.user_id``,
  ``preset_recents.user_id``.
* Serialized ownership references (string principals, matched by exact UUID
  text): ``temporal_executions.owner_id``,
  ``temporal_execution_sources.owner_id``, ``task_source_mappings.owner_id``,
  ``managed_agent_oauth_sessions.requested_by_user_id``,
  ``temporal_artifacts.created_by_principal``,
  ``settings_overrides``/``settings_audit_events`` ``user_id``/``actor`` UUIDs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    User,
    UserExternalIdentity,
    UserProfile,
)

logger = logging.getLogger(__name__)

DEFAULT_USER_UUID = UUID("00000000-0000-0000-0000-000000000000")

RESERVED_SUBJECTS = frozenset({"local", "__public__"})

MAX_ISSUER_LENGTH = 2048
MAX_SUBJECT_LENGTH = 1024

TRUSTED_PROXY_PREFIX = "proxy:"

# Foreign-key ownership surfaces verified against ``api_service/db/models.py``.
# Used by migration reconciliation to assert every retained UUID and
# foreign-key owner is preserved.
USER_FK_OWNERSHIP: tuple[tuple[str, str], ...] = (
    ("user_profile", "user_id"),
    ("recurring_workflow_definitions", "owner_user_id"),
    ("omnigent_policies", "owner_user_id"),
    ("workflow_runs", "requested_by_user_id"),
    ("workflow_runs", "created_by"),
    ("preset_versions", "reviewed_by"),
    ("presets", "created_by"),
    ("preset_favorites", "user_id"),
    ("preset_recents", "user_id"),
)

# Serialized (string) ownership references matched by exact UUID text.
SERIALIZED_OWNERSHIP_REFERENCES: tuple[tuple[str, str], ...] = (
    ("temporal_executions", "owner_id"),
    ("temporal_execution_sources", "owner_id"),
    ("task_source_mappings", "owner_id"),
    ("managed_agent_oauth_sessions", "requested_by_user_id"),
    ("temporal_artifacts", "created_by_principal"),
)


class IdentityValidationError(ValueError):
    """Fail-closed validation error for an unverified identity."""


class IdentityConflictError(RuntimeError):
    """A uniqueness-protected binding collided; caller must surface it."""


class ControlledEnrollmentRequiredError(RuntimeError):
    """Identity/credential state needs explicit operator enrollment, not a guess.

    Raised for default-UUID mismatches, email-taken-by-another-user,
    unported credentials/MFA, and any ambiguous row — never resolved by
    automatic email linking.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_external_identity(issuer: str, subject: str) -> tuple[str, str]:
    """Validate an ``(issuer, subject)`` pair without normalizing identity.

    Returns the pair unchanged (exact case-sensitive semantics preserved).
    Raises :class:`IdentityValidationError` on empty, reserved, or oversized
    input — never truncates.
    """
    if not isinstance(issuer, str) or not issuer:
        raise IdentityValidationError("issuer must be a non-empty string")
    if not isinstance(subject, str) or not subject:
        raise IdentityValidationError("subject must be a non-empty string")
    if subject in RESERVED_SUBJECTS:
        raise IdentityValidationError(f"reserved identity {subject!r}")
    if len(issuer) > MAX_ISSUER_LENGTH:
        raise IdentityValidationError(
            f"issuer exceeds {MAX_ISSUER_LENGTH} characters; refusing to truncate"
        )
    if len(subject) > MAX_SUBJECT_LENGTH:
        raise IdentityValidationError(
            f"subject exceeds {MAX_SUBJECT_LENGTH} characters; refusing to truncate"
        )
    return issuer, subject


def is_trusted_proxy_issuer(issuer: str) -> bool:
    """Whether an issuer uses the explicit trusted-proxy namespace."""
    return issuer.startswith(TRUSTED_PROXY_PREFIX)


def credential_enrollment_required(hashed_password: str | None) -> bool:
    """Whether a login must take the controlled enrollment/reset path.

    Identity migration never ports credentials: unknown, missing, or
    non-argon2 hashes are never assumed compatible (see also #4118). The
    caller must require controlled enrollment or reset instead of reporting a
    migrated credential status.
    """
    if not hashed_password:
        return True
    return not hashed_password.startswith("$argon2")


async def resolve_user_id_for_identity(
    session: AsyncSession, issuer: str, subject: str
) -> UUID | None:
    """Resolve a verified ``(issuer, subject)`` pair to one MoonMind UUID.

    Exact, case-sensitive match against the single active relation. Returns
    ``None`` when no mapping exists (enrollment required). Never consults
    email, display name, or the legacy ``oidc_*`` columns.
    """
    issuer, subject = validate_external_identity(issuer, subject)
    result = await session.execute(
        select(UserExternalIdentity.user_id).where(
            UserExternalIdentity.issuer == issuer,
            UserExternalIdentity.subject == subject,
        )
    )
    return result.scalars().first()


async def bind_external_identity(
    session: AsyncSession, user_id: UUID, issuer: str, subject: str
) -> UserExternalIdentity:
    """Bind ``(issuer, subject)`` to ``user_id`` with uniqueness protection.

    Low-level primitive: flushes inside the caller's transaction and lets a
    concurrent duplicate insert raise :class:`sqlalchemy.exc.IntegrityError`.
    Prefer :func:`bind_external_identity_convergent` (converges on the race
    winner) or :func:`get_or_create_user_for_identity` (full provisioning)
    unless the caller owns the conflict policy.
    """
    issuer, subject = validate_external_identity(issuer, subject)
    mapping = UserExternalIdentity(user_id=user_id, issuer=issuer, subject=subject)
    session.add(mapping)
    await session.flush()
    return mapping


async def bind_external_identity_convergent(
    session: AsyncSession, user_id: UUID, issuer: str, subject: str
) -> tuple[UserExternalIdentity, bool]:
    """Bind, converging on the winner when a concurrent insert wins the race.

    Returns ``(mapping, created)``. Uses a nested savepoint so a duplicate
    insert rolls back only the contested row, preserving the caller's outer
    transaction.
    """
    issuer, subject = validate_external_identity(issuer, subject)
    nested = await session.begin_nested()
    try:
        mapping = UserExternalIdentity(user_id=user_id, issuer=issuer, subject=subject)
        session.add(mapping)
        await session.flush()
        await nested.commit()
        return mapping, True
    except IntegrityError:
        await nested.rollback()
        result = await session.execute(
            select(UserExternalIdentity).where(
                UserExternalIdentity.issuer == issuer,
                UserExternalIdentity.subject == subject,
            )
        )
        existing = result.scalars().first()
        if existing is None:
            raise IdentityConflictError(
                "external identity binding conflicted but no winner is visible"
            )
        return existing, False


async def ensure_profile_transactional(session: AsyncSession, user_id: UUID) -> bool:
    """Create the one profile for ``user_id``; ``True`` when newly created.

    Converges on the existing row when a concurrent creator wins the race
    instead of failing the transaction.
    """
    nested = await session.begin_nested()
    try:
        session.add(UserProfile(user_id=user_id))
        await session.flush()
        await nested.commit()
        return True
    except IntegrityError:
        await nested.rollback()
        return False


async def get_or_create_user_for_identity(
    session: AsyncSession,
    issuer: str,
    subject: str,
    *,
    email: str | None = None,
    is_active: bool = True,
) -> tuple[User, bool]:
    """Transactionally resolve-or-provision the user for a verified identity.

    * Returning users keep their UUID and current local ``is_active`` /
      ``is_superuser`` flags; only a changed email is updated.
    * New users are created with ``is_superuser=False`` (never promoted) and
      one profile in the same transaction.
    * Email is informational only: it is never used to look up or merge
      accounts. When the email is already taken by a *different* user, a
      :class:`ControlledEnrollmentRequiredError` is raised instead of merging
      or transferring ownership (covers same-email-across-issuers and recycled
      emails).
    * Caller retry contract (for session wiring): on
      :class:`IdentityConflictError` or ``email_taken``
      :class:`ControlledEnrollmentRequiredError`, roll back and retry with
      bounded backoff, then re-resolve the identity — the race winner owns
      the mapping and the loser converges on it.
    """
    issuer, subject = validate_external_identity(issuer, subject)
    existing_id = await resolve_user_id_for_identity(session, issuer, subject)
    if existing_id is not None:
        user = await session.get(User, existing_id)
        if user is None:  # pragma: no cover - ledger inconsistency guard
            raise IdentityConflictError("external identity points at a missing user")
        if email is not None and user.email != email:
            user.email = email
            await session.flush()
        await ensure_profile_transactional(session, user.id)
        return user, False

    # Provision inside a savepoint so a lost mapping race discards the loser
    # user row instead of stranding an unmapped duplicate account.
    outer = await session.begin_nested()
    try:
        if email is not None:
            taken = await session.execute(select(User.id).where(User.email == email))
            if taken.scalars().first() is not None:
                raise ControlledEnrollmentRequiredError(
                    "email_taken",
                    "email is already owned by a different user; explicit operator "
                    "enrollment is required, automatic linking is refused",
                )
        user = User(
            id=uuid.uuid4(),
            email=email or f"enrolled-{uuid.uuid4().hex}@example.invalid",
            hashed_password=None,
            is_active=is_active,
            is_superuser=False,
            is_verified=False,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            # Lost an email-uniqueness race that the pre-check could not see;
            # the winner is resolved by retrying, never by merging.
            raise ControlledEnrollmentRequiredError(
                "email_taken",
                "email was claimed concurrently by a different user; explicit "
                "operator enrollment is required, automatic linking is refused",
            ) from exc
        mapping, created = await bind_external_identity_convergent(
            session, user.id, issuer, subject
        )
        if created or mapping.user_id == user.id:
            await outer.commit()
        else:
            await outer.rollback()
            winner = await session.get(User, mapping.user_id)
            if winner is None:
                raise IdentityConflictError(
                    "binding race winner is not yet visible; retry with backoff"
                )
            await ensure_profile_transactional(session, winner.id)
            return winner, False
    except (ControlledEnrollmentRequiredError, IdentityConflictError):
        if outer.is_active:
            await outer.rollback()
        raise
    await ensure_profile_transactional(session, user.id)
    return user, True


# ---------------------------------------------------------------------------
# Safe reporting and reconciliation (sanitized: no hashes, tokens, exports)
# ---------------------------------------------------------------------------


@dataclass
class IdentityIssue:
    """One actionable migration blocker for a single row or pair of rows."""

    code: str
    detail: str
    user_ids: list[str] = field(default_factory=list)


@dataclass
class IdentityReport:
    """Sanitized migration report: counts plus non-sensitive identifiers."""

    total_users: int = 0
    mapped_users: int = 0
    unmapped_users: int = 0
    orphan_profiles: int = 0
    issues: list[IdentityIssue] = field(default_factory=list)

    def to_sanitized_dict(self) -> dict[str, Any]:
        return {
            "total_users": self.total_users,
            "mapped_users": self.mapped_users,
            "unmapped_users": self.unmapped_users,
            "orphan_profiles": self.orphan_profiles,
            "issues": [
                {"code": i.code, "detail": i.detail, "user_ids": list(i.user_ids)}
                for i in self.issues
            ],
        }

    def has_blockers(self) -> bool:
        return bool(self.issues)


def _redact_for_report(value: str, *, keep: int = 8) -> str:
    """Redact an identifier for operator display (prefix plus length only)."""
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    return f"{value[:keep]}…#{digest}" if len(value) > keep else f"#{digest}"


async def collect_identity_report(
    session: AsyncSession,
    *,
    provider_to_issuer: dict[str, str] | None = None,
    default_user_id: UUID = DEFAULT_USER_UUID,
    default_email: str | None = None,
) -> IdentityReport:
    """Collect duplicate/missing/orphan/mismatch findings without secrets.

    Ambiguous rows are reported as blockers; nothing here links accounts by
    email. The report contains UUIDs, issue codes, and redacted issuer
    prefixes only — never password hashes, tokens, or identity exports.
    """
    provider_to_issuer = provider_to_issuer or {}
    report = IdentityReport()

    users = (await session.execute(select(User))).scalars().all()
    report.total_users = len(users)
    identities = (await session.execute(select(UserExternalIdentity))).scalars().all()
    mapped: set[UUID] = {m.user_id for m in identities}
    report.mapped_users = len([u for u in users if u.id in mapped])
    report.unmapped_users = report.total_users - report.mapped_users

    seen_pairs: dict[tuple[str, str], list[UUID]] = {}
    for m in identities:
        seen_pairs.setdefault((m.issuer, m.subject), []).append(m.user_id)
    for (issuer, subject), owners in seen_pairs.items():
        if len(owners) > 1:  # pragma: no cover - unique constraint guards this
            report.issues.append(
                IdentityIssue(
                    code="duplicate_mapping",
                    detail=f"identity {_redact_for_report(issuer)} is bound twice",
                    user_ids=[str(o) for o in owners],
                )
            )

    emails: dict[str, list[UUID]] = {}
    for u in users:
        emails.setdefault((u.email or "").strip().lower(), []).append(u.id)
    for email, owners in emails.items():
        if email and len(owners) > 1:
            report.issues.append(
                IdentityIssue(
                    code="duplicate_email",
                    detail="email is owned by more than one user; "
                    "manual enrollment required, no automatic merge",
                    user_ids=[str(o) for o in owners],
                )
            )

    profiles = (await session.execute(select(UserProfile))).scalars().all()
    user_ids = {u.id for u in users}
    orphans = [p for p in profiles if p.user_id not in user_ids]
    report.orphan_profiles = len(orphans)
    for p in orphans:
        report.issues.append(
            IdentityIssue(
                code="orphan_profile",
                detail=f"profile {p.id} references a missing user",
                user_ids=[str(p.user_id)],
            )
        )

    for u in users:
        if u.id not in mapped and (u.oidc_provider or u.oidc_subject):
            report.issues.append(
                IdentityIssue(
                    code="missing_mapping",
                    detail="legacy Keycloak-era identity has no verified "
                    "(issuer, subject) target mapping",
                    user_ids=[str(u.id)],
                )
            )

    if default_email is not None:
        for u in users:
            if (
                u.email == default_email
                and u.id != default_user_id
                and u.id in {x for x in user_ids}
            ):
                report.issues.append(
                    IdentityIssue(
                        code="default_id_mismatch",
                        detail="default email is owned by a non-default UUID; "
                        "protected operator claim required, never silent replacement",
                        user_ids=[str(u.id)],
                    )
                )
                break

    for legacy_provider, issuer in provider_to_issuer.items():
        if not issuer:
            report.issues.append(
                IdentityIssue(
                    code="unresolved_mapping",
                    detail=f"source provider {legacy_provider!r} has no reviewed "
                    "issuer target",
                )
            )
    void_subjects = [
        m for m in identities if not m.issuer or not m.subject
    ]
    for m in void_subjects:  # pragma: no cover - validation guards this
        report.issues.append(
            IdentityIssue(
                code="unresolved_ownership",
                detail="external identity row has an empty issuer or subject",
                user_ids=[str(m.user_id)],
            )
        )
    return report


async def ownership_snapshot(session: AsyncSession) -> dict[str, Any]:
    """Capture a sanitized before/after ownership snapshot for reconciliation.

    Records every retained ``User.id`` plus the foreign-key owners that must
    survive migration unchanged. UUIDs are preserved verbatim here (they are
    the reconciliation keys, not secrets); no hashes, tokens, or exports.
    """
    users = (await session.execute(select(User.id))).scalars().all()
    snapshot: dict[str, Any] = {"user_ids": sorted(str(u) for u in users)}
    fk_counts: dict[str, int] = {}
    for table_name, column in USER_FK_OWNERSHIP:
        table = User.__table__.metadata.tables.get(table_name)
        if table is None:  # pragma: no cover - registry drift guard
            fk_counts[f"{table_name}.{column}"] = -1
            continue
        col = table.c[column]
        count = (
            await session.execute(
                select(func.count()).select_from(table).where(col.is_not(None))
            )
        ).scalar() or 0
        fk_counts[f"{table_name}.{column}"] = int(count)
    snapshot["fk_owner_counts"] = fk_counts
    return snapshot


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    """Stable digest of an ownership snapshot for before/after comparison."""
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
