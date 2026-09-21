"""Guarded single-user upgrade eligibility + conversion entrypoint (#4346).

Source: docs/SingleUserApplicationDesign.md sections 9-10 (parent #4345).

Owns attribution, conversion admission, and the shared transaction/cutover
entrypoint. Subsystem issues own their concrete transforms; they integrate
here rather than creating another migrator.

Four dispositions:

* ``fresh_init`` — no retained application data; initialize without accounts.
* ``eligible_conversion`` — retained data attributable to one operator
  (proven aliases included); convert automatically.
* ``multi_person_refusal`` — retained data spans multiple people; block
  before any conversion-side mutation.
* ``unresolved_refusal`` — missing/conflicting/incomplete evidence
  (unknown principals, unowned rows without deployment evidence,
  settings collisions without explicit disposition, missing transform
  coverage); block on the same boundary.

Alias rule: same-person aliases require durable trustworthy evidence
passed explicitly as ``alias_groups``. Email, display name, admin flags,
a single active login, or a preferred-account argument are never
consulted and can never merge people. Multiple external provider accounts
are not multiple people (non-UUID machine principals are ignored).

Consistency: the conversion-wide advisory claim is acquired before the
authoritative inventory read and held through the single commit that
runs transforms and records completion, so a concurrent mutation cannot
slip between eligibility and publication. :func:`preflight` captures a
fingerprint of the source (owner sets, unowned rows, secret holders,
settings values, secret ciphertext hashes, and counts);
:func:`apply_conversion` recomputes it and raises
:class:`StaleAttributionError` when anything changed, so a change that
preceded the claim cannot publish on stale attribution either. The
fingerprint is a tripwire, not the enforcement: the claim plus the
transaction is. No new lock/identity service: mutual exclusion reuses
the ledger row plus a best-effort conversion-wide PostgreSQL advisory
claim (SQLite falls back to the ledger row alone). No ``in_progress``
row is ever committed: interruption before commit leaves nothing behind
and a retry converges; a stale ``in_progress`` row from an older
revision is reclaimed only while holding the conversion claim (which
proves no live writer exists) and otherwise fails closed.

Reports carry counts, reason codes, and truncated identifier prefixes
only — never emails, names, secret values, or resource content.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import String, UniqueConstraint, and_, cast, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from api_service.db.models import Base, Uuid, mutable_json_dict

logger = logging.getLogger(__name__)

SCHEMA_REVISION = "386_single_user_conversion_4346"

DEPLOYMENT_SUBJECT_ID = "00000000-0000-0000-0000-000000000000"


class ConversionAuthorizationError(PermissionError):
    """Apply invoked without explicit operator authorization."""


class StaleAttributionError(RuntimeError):
    """Source ownership/data changed since preflight; re-run preflight."""


class ConcurrentConversionError(RuntimeError):
    """Another conversion holds this preflight digest."""


# ---------------------------------------------------------------------------
# Ledger (durable release state; versioned migration 386 creates the table)
# ---------------------------------------------------------------------------


class SingleUserConversionRun(Base):
    """Durable ledger for the idempotent guarded conversion (#4346).

    One row per preflight digest. A rerun with the same digest replays the
    recorded result instead of duplicating work; a concurrent apply against
    an ``in_progress`` row fails closed. ``result_json`` carries only
    sanitized dispositions — never credentials or resource content.
    """

    __tablename__ = "single_user_conversion_runs"
    __table_args__ = (
        UniqueConstraint(
            "preflight_digest", name="uq_single_user_conversion_runs_digest"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    preflight_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="in_progress"
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(
        mutable_json_dict(), nullable=False, default=dict
    )


def _ledger_class() -> type[SingleUserConversionRun]:
    return SingleUserConversionRun


# ---------------------------------------------------------------------------
# Redaction helpers
# ---------------------------------------------------------------------------


def _redact_id(value: str, *, keep: int = 8) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    head = value[:keep] if len(value) > keep else ""
    return f"{head}…#{digest}" if head else f"#{digest}"


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _looks_like_uuid(value: Any) -> str | None:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Options + decision types
# ---------------------------------------------------------------------------


@dataclass
class ConversionOptions:
    alias_groups: tuple[tuple[str, ...], ...] = ()
    collision_resolutions: dict[str, Any] = field(default_factory=dict)
    deployment_owned_tables: frozenset[str] = frozenset()

    def canonical(self) -> dict[str, Any]:
        return {
            "alias_groups": [list(g) for g in self.alias_groups],
            # Both resolution keys and their sanitized values bind the
            # digest: two different operator choices for the same setting
            # must produce different digests, or a completed ledger row
            # would replay the first choice and erase the correction.
            "collision_resolutions": sorted(
                (str(k), _canonical(v))
                for k, v in self.collision_resolutions.items()
            ),
            "deployment_owned_tables": sorted(self.deployment_owned_tables),
        }


def normalize_options(
    alias_groups=None,
    collision_resolutions=None,
    deployment_owned_tables=None,
    *,
    operator_authorized: bool = False,
) -> ConversionOptions:
    # Alias groups, collision dispositions, and deployment-ownership claims
    # are operator-attested evidence about human data, not caller
    # convenience: they take effect only through an authorized boundary
    # (preflight/apply with explicit operator authorization). An
    # unauthorized caller supplying any of them is a misuse, not an
    # empty-evidence request, so fail closed instead of ignoring it.
    if (
        alias_groups or collision_resolutions or deployment_owned_tables
    ) and not operator_authorized:
        raise ConversionAuthorizationError(
            "alias, collision, and deployment-ownership evidence requires "
            "explicit operator authorization"
        )
    groups: list[tuple[str, ...]] = []
    seen: dict[str, int] = {}
    for group in alias_groups or []:
        members = sorted({_looks_like_uuid(m) or "" for m in group} - {""})
        if not members:
            raise ValueError("alias group must contain at least one UUID")
        for m in members:
            if m in seen:
                raise ValueError("alias groups must be disjoint")
            seen[m] = len(groups)
        groups.append(tuple(members))
    groups.sort()
    return ConversionOptions(
        alias_groups=tuple(groups),
        collision_resolutions=dict(collision_resolutions or {}),
        deployment_owned_tables=frozenset(
            str(t) for t in (deployment_owned_tables or set())
        ),
    )


@dataclass
class ConversionDecision:
    disposition: str
    eligible: bool
    reason_code: str
    person_count: int = 0
    principal_count: int = 0
    redacted_persons: list[str] = field(default_factory=list)
    detail: str = ""
    present_subsystems: list[str] = field(default_factory=list)

    def to_sanitized_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "eligible": self.eligible,
            "reason_code": self.reason_code,
            "person_count": self.person_count,
            "principal_count": self.principal_count,
            "persons": list(self.redacted_persons),
            "detail": self.detail,
            "present_subsystems": list(self.present_subsystems),
        }


@dataclass
class ConversionInventory:
    user_ids: list[str] = field(default_factory=list)
    principals: dict[str, list[str]] = field(default_factory=dict)
    unknown_principals: list[str] = field(default_factory=list)
    unowned: dict[str, int] = field(default_factory=dict)
    secret_holders: list[str] = field(default_factory=list)
    settings_collisions: list[str] = field(default_factory=list)
    present_subsystems: list[str] = field(default_factory=list)
    resource_counts: dict[str, int] = field(default_factory=dict)
    # Required reads that failed (surface names only, never values). Any
    # entry here means the inventory is partial: disposition must refuse
    # with ``inventory_unavailable`` rather than treat the unreadable
    # surface as empty or eligible.
    inventory_errors: list[str] = field(default_factory=list)
    # One-way hash over the settings values and legacy secret ciphertexts
    # observed during this read. Same-count content changes (a settings
    # value edit, a rotated legacy secret) alter this hash even though
    # owner sets and row counts are unchanged, so the preflight/apply
    # comparison detects them.
    source_content_hash: str = ""

    def fingerprint(self) -> str:
        return hashlib.sha256(
            _canonical(
                {
                    "users": sorted(self.user_ids),
                    "principals": {
                        k: sorted(v) for k, v in sorted(self.principals.items())
                    },
                    "unknown": sorted(self.unknown_principals),
                    "unowned": dict(sorted(self.unowned.items())),
                    "secrets": sorted(self.secret_holders),
                    "collisions": sorted(self.settings_collisions),
                    "subsystems": sorted(self.present_subsystems),
                    "resources": dict(sorted(self.resource_counts.items())),
                    "errors": sorted(self.inventory_errors),
                    "content": self.source_content_hash,
                }
            ).encode()
        ).hexdigest()


# Tables carrying a human principal. ``kind`` selects the read strategy:
# "uuid_fk" (UUID column), "uuid_text" (string column holding UUID text),
# "user_temporal" (string owner_id qualified by a USER owner_type: only
# rows explicitly marked human-owned count as principals, so machine
# owners never inflate attribution).
_PRINCIPAL_SURFACES: tuple[tuple[str, str, str], ...] = (
    ("user_profile", "user_id", "uuid_fk"),
    ("recurring_workflow_definitions", "owner_user_id", "uuid_fk"),
    ("omnigent_policies", "owner_user_id", "uuid_fk"),
    ("workflow_runs", "requested_by_user_id", "uuid_fk"),
    ("workflow_runs", "created_by", "uuid_fk"),
    ("preset_versions", "reviewed_by", "uuid_fk"),
    ("presets", "created_by", "uuid_fk"),
    ("preset_favorites", "user_id", "uuid_fk"),
    ("preset_recents", "user_id", "uuid_fk"),
    ("settings_overrides", "user_id", "uuid_fk"),
    ("settings_audit_events", "user_id", "uuid_fk"),
    ("settings_audit_events", "actor_user_id", "uuid_fk"),
    ("temporal_executions", "owner_id", "user_temporal"),
    ("temporal_execution_sources", "owner_id", "user_temporal"),
    ("workflow_execution_source_mappings", "owner_id", "user_temporal"),
    ("managed_agent_oauth_sessions", "requested_by_user_id", "uuid_text"),
    ("temporal_artifacts", "created_by_principal", "uuid_text"),
)

# Null-owner rows in these tables are ambiguous retained data, not
# deployment-owned by assumption. Each entry names a single owner
# column whose null (on a non-empty table) refuses conversion.
_UNOWNED_TRACKED: tuple[tuple[str, str], ...] = (
    ("recurring_workflow_definitions", "owner_user_id"),
    ("presets", "created_by"),
    ("temporal_executions", "owner_id"),
    ("temporal_artifacts", "created_by_principal"),
)

# Multi-column attribution: a row here is unattributed only when *every*
# listed user column is null. A workflow run with either owner set is
# attributed to that owner; only a both-null run (e.g. after
# ``ON DELETE SET NULL`` removed its user) is ambiguous retained data.
# Recorded under a ``table.col+col`` key so deployment-ownership checks
# (which split on the table prefix) keep working.
_UNOWNED_CONJUNCTIONS: dict[str, tuple[str, ...]] = {
    "workflow_runs": ("requested_by_user_id", "created_by"),
}

_SUBSYSTEM_BY_SURFACE: dict[str, str] = {
    "user_profile": "profile_secrets",
    "settings_overrides": "settings",
    "settings_audit_events": "settings",
    "presets": "presets",
    "preset_versions": "presets",
    "preset_favorites": "presets",
    "preset_recents": "presets",
    "recurring_workflow_definitions": "schedules",
    "temporal_artifacts": "artifacts",
    "workflow_artifacts": "artifacts",
    "temporal_executions": "temporal",
    "temporal_execution_sources": "temporal",
    "workflow_runs": "workflows",
}


def _record_inventory_error(inv: ConversionInventory, surface: str) -> None:
    """Record a failed required inventory read (surface name only).

    Unknown required evidence must never become an empty or eligible
    source: every recorded surface forces an ``inventory_unavailable``
    refusal in :func:`evaluate_disposition`.
    """
    if surface not in inv.inventory_errors:
        inv.inventory_errors.append(surface)
    logger.warning("single-user conversion inventory unreadable: %s", surface)


def _is_absent_table_error(exc: BaseException) -> bool:
    """True when a failed read proves the table itself is absent.

    An absent table provably holds no rows, so its surfaces are empty
    evidence rather than unknown evidence (partial schemas, such as the
    hermetic PostgreSQL fixture that creates only the tables under
    test, skip them exactly like a missing metadata entry). Any other
    failure -- a missing column, permissions, a transient outage --
    leaves attribution unknown and must be recorded.
    """
    cursor: BaseException | None = exc
    while cursor is not None:
        if type(cursor).__name__ in ("UndefinedTableError", "UndefinedTable"):
            return True
        if "no such table" in str(cursor).lower():
            return True
        cursor = cursor.__cause__ or cursor.__context__
    return False


async def _probe_count(
    session: AsyncSession, table, column, *, inv: ConversionInventory, surface: str
) -> int | None:
    probe = await session.begin_nested()
    try:
        count = (
            await session.execute(
                select(func.count()).select_from(table).where(column.is_not(None))
            )
        ).scalar() or 0
        await probe.commit()
        return int(count)
    except Exception as exc:
        # The savepoint alone is rolled back. An absent table holds no
        # rows (skip like a missing metadata entry); any other failure
        # is recorded so a missing count cannot hide retained data.
        try:
            await probe.rollback()
        except Exception:
            # Rollback of a failed savepoint is best effort: the probe is
            # already dead and the caller still classifies the error.
            pass
        if not _is_absent_table_error(exc):
            _record_inventory_error(inv, surface)
        return None


async def collect_inventory(session: AsyncSession) -> ConversionInventory:
    """Trace every retained user reference (SQL + serialized + in-flight).

    Never records emails, names, secret values, or resource content —
    only UUID principals, counts, and collision keys.
    """
    from api_service.db.models import User

    inv = ConversionInventory()
    users = (await session.execute(select(User.id))).scalars().all()
    inv.user_ids = sorted(str(u) for u in users)
    user_set = set(inv.user_ids)

    from api_service.db.models import User as _U

    metadata = _U.__table__.metadata
    principals: dict[str, set[str]] = {}
    unowned: dict[str, int] = {}
    resource_counts: dict[str, int] = {}

    for table_name, column_name, kind in _PRINCIPAL_SURFACES:
        table = metadata.tables.get(table_name)
        if table is None or column_name not in table.c:
            continue
        col = table.c[column_name]
        if kind == "user_temporal":
            owner_type_col = table.c.get("owner_type")
            try:
                if owner_type_col is not None:
                    rows = (
                        await session.execute(
                            select(col, owner_type_col).where(col.is_not(None))
                        )
                    ).all()
                    values = [
                        str(v)
                        for v, t in rows
                        if str(t or "").lower() == "user" and v is not None
                    ]
                else:
                    rows = (
                        await session.execute(
                            select(col).where(col.is_not(None))
                        )
                    ).all()
                    values = [str(r[0]) for r in rows if r[0] is not None]
            except Exception as exc:
                # A failed principal read leaves attribution unknown: roll
                # back only this surface, then classify. An absent table
                # holds no rows (skip); any other failure is recorded so
                # the disposition refuses instead of converting without
                # this evidence.
                try:
                    await session.rollback()
                except Exception:
                    # The surface read already failed; a failed rollback
                    # must not mask the classification below.
                    pass
                if not _is_absent_table_error(exc):
                    _record_inventory_error(inv, f"{table_name}.{column_name}")
                continue
            for raw in values:
                norm = _looks_like_uuid(raw)
                if norm:
                    principals.setdefault(f"{table_name}.{column_name}", set()).add(norm)
            total = await _probe_count(
                session, table, col,
                inv=inv, surface=f"count:{table_name}.{column_name}",
            )
            if total is not None:
                resource_counts[f"{table_name}.{column_name}"] = total
            continue
        if kind == "uuid_text":
            try:
                rows = (
                    await session.execute(select(col).where(col.is_not(None)))
                ).all()
            except Exception as exc:
                # Same classify-and-record rule as above: an absent table
                # is empty evidence, any other failure is unknown evidence.
                try:
                    await session.rollback()
                except Exception:
                    # The surface read already failed; a failed rollback
                    # must not mask the classification below.
                    pass
                if not _is_absent_table_error(exc):
                    _record_inventory_error(inv, f"{table_name}.{column_name}")
                continue
            for (raw,) in rows:
                if raw is None or str(raw).strip() == "":
                    continue
                norm = _looks_like_uuid(str(raw).strip())
                if norm:
                    principals.setdefault(f"{table_name}.{column_name}", set()).add(norm)
            total = await _probe_count(
                session, table, col,
                inv=inv, surface=f"count:{table_name}.{column_name}",
            )
            if total is not None:
                resource_counts[f"{table_name}.{column_name}"] = total
            continue
        # uuid_fk
        try:
            rows = (
                await session.execute(select(col).where(col.is_not(None)))
            ).all()
        except Exception as exc:
            # Same classify-and-record rule as above: an absent table is
            # empty evidence, any other failure is unknown evidence.
            try:
                await session.rollback()
            except Exception:
                # The surface read already failed; a failed rollback must
                # not mask the classification below.
                pass
            if not _is_absent_table_error(exc):
                _record_inventory_error(inv, f"{table_name}.{column_name}")
            continue
        for (raw,) in rows:
            if raw is None:
                continue
            norm = _looks_like_uuid(raw)
            if norm is None:
                continue
            if norm == DEPLOYMENT_SUBJECT_ID:
                continue
            principals.setdefault(f"{table_name}.{column_name}", set()).add(norm)
        total = await _probe_count(
            session, table, col,
            inv=inv, surface=f"count:{table_name}.{column_name}",
        )
        if total is not None:
            resource_counts[f"{table_name}.{column_name}"] = total

    inv.principals = {k: sorted(v) for k, v in principals.items()}
    all_principals = sorted({p for vs in principals.values() for p in vs})
    inv.unknown_principals = sorted(p for p in all_principals if p not in user_set)

    # Unowned rows: resource exists without attribution in tracked tables.
    for table_name, column_name in _UNOWNED_TRACKED:
        table = metadata.tables.get(table_name)
        if table is None or column_name not in table.c:
            continue
        col = table.c[column_name]
        probe = await session.begin_nested()
        try:
            if table_name == "temporal_executions":
                owner_type_col = table.c.get("owner_type")
                if owner_type_col is not None:
                    # ``owner_type`` is a native PostgreSQL enum, which
                    # ``lower()`` does not accept: cast to text first so
                    # the user-qualification works on every backend.
                    nulls = (
                        await session.execute(
                            select(func.count())
                            .select_from(table)
                            .where(
                                col.is_(None),
                                func.lower(cast(owner_type_col, String)) == "user",
                            )
                        )
                    ).scalar() or 0
                else:
                    nulls = (
                        await session.execute(
                            select(func.count())
                            .select_from(table)
                            .where(col.is_(None))
                        )
                    ).scalar() or 0
            else:
                # Deployment-seeded catalog presets carry trusted provenance
                # (``seed_source`` stamped by the seed sync), so only
                # ownerless rows *without* that provenance are ambiguous
                # retained data. Legacy user presets have no seed source and
                # are still counted here. Startup also evaluates eligibility
                # before seeding, so its own seeds never gate the cutover.
                if table_name == "presets" and "seed_source" in table.c:
                    seed_col = table.c["seed_source"]
                    nulls = (
                        await session.execute(
                            select(func.count())
                            .select_from(table)
                            .where(col.is_(None), seed_col.is_(None))
                        )
                    ).scalar() or 0
                else:
                    nulls = (
                        await session.execute(
                            select(func.count()).select_from(table).where(col.is_(None))
                        )
                    ).scalar() or 0
            # Only ambiguous when the table is non-empty.
            total = (
                await session.execute(select(func.count()).select_from(table))
            ).scalar() or 0
            await probe.commit()
        except Exception as exc:
            # Classify the probe failure: an absent table holds no rows
            # (skip); any other failure is unknown evidence, not proof of
            # attribution, so the disposition refuses.
            try:
                await probe.rollback()
            except Exception:
                # The probe already failed; a failed savepoint rollback
                # must not mask the classification below.
                pass
            if not _is_absent_table_error(exc):
                _record_inventory_error(inv, f"unowned:{table_name}.{column_name}")
            continue
        if int(total) > 0 and int(nulls) > 0:
            unowned[f"{table_name}.{column_name}"] = int(nulls)

    for table_name, column_names in _UNOWNED_CONJUNCTIONS.items():
        table = metadata.tables.get(table_name)
        if table is None or any(c not in table.c for c in column_names):
            continue
        cols = [table.c[c] for c in column_names]
        key = f"{table_name}.{'+'.join(column_names)}"
        probe = await session.begin_nested()
        try:
            nulls = (
                await session.execute(
                    select(func.count())
                    .select_from(table)
                    .where(and_(*(c.is_(None) for c in cols)))
                )
            ).scalar() or 0
            total = (
                await session.execute(select(func.count()).select_from(table))
            ).scalar() or 0
            await probe.commit()
        except Exception as exc:
            # Same classification as above: absent tables are empty
            # evidence, anything else is unknown evidence.
            try:
                await probe.rollback()
            except Exception:
                # The probe already failed; a failed savepoint rollback
                # must not mask the classification below.
                pass
            if not _is_absent_table_error(exc):
                _record_inventory_error(inv, f"unowned:{key}")
            continue
        if int(total) > 0 and int(nulls) > 0:
            unowned[key] = int(nulls)
    inv.unowned = unowned

    # Profile-held secrets (subsystem presence for coverage gating). The
    # ciphertext hashes below feed the content tripwire: a rotated legacy
    # secret changes the fingerprint even though holder sets and counts
    # are unchanged. Only hashes are retained, never plaintext.
    profile_content_entries: list[str] = []
    try:
        from api_service.db.models import UserProfile
        from api_service.services.profile_secret_migration import LEGACY_FIELDS

        secret_rows = (await session.execute(select(UserProfile))).scalars().all()
        holders = set()
        for p in secret_rows:
            for column, _short in LEGACY_FIELDS:
                try:
                    value = getattr(p, column, None)
                except Exception:
                    # An unreadable legacy ciphertext is unknown evidence:
                    # record which column failed and keep the holders found
                    # so far; the disposition still refuses on the error.
                    _record_inventory_error(inv, f"profile_secrets:{column}")
                    continue
                if isinstance(value, str) and value.strip():
                    uid = _looks_like_uuid(getattr(p, "user_id", None))
                    if uid:
                        holders.add(uid)
                    user_hex = str(getattr(p, "user_id", "") or "")
                    ciphertext_hash = hashlib.sha256(
                        value.encode()
                    ).hexdigest()
                    profile_content_entries.append(
                        f"{user_hex}\x00{column}\x00{ciphertext_hash}"
                    )
        inv.secret_holders = sorted(holders)
    except Exception as exc:
        # The whole profile-secret surface is unreadable. An absent table
        # holds no secrets (clear and continue); any other failure leaves
        # secret attribution unknown, so clear the holders and record it
        # rather than converting without secret attribution.
        inv.secret_holders = []
        try:
            await session.rollback()
        except Exception:
            # The surface read already failed; a failed rollback must not
            # mask the classification below.
            pass
        if not _is_absent_table_error(exc):
            _record_inventory_error(inv, "profile_secrets")

    # Same-person settings collisions: one effective value context, several
    # distinct values. A collision is about incompatible effective values,
    # not any two raw values in separate valid contexts: rows that differ
    # in (scope, workspace) resolve by override precedence, so only rows
    # sharing (key, scope, workspace) with distinct values collide. Every
    # row (including deployment-subject rows) still feeds the content
    # tripwire so a same-count value edit changes the fingerprint.
    settings_content_entries: list[str] = []
    try:
        from api_service.db.models import SettingsOverride

        rows = (await session.execute(select(SettingsOverride))).scalars().all()
        by_context: dict[tuple[str, str, str], dict[str, set[str]]] = {}
        for r in rows:
            uid = _looks_like_uuid(getattr(r, "user_id", None))
            key = str(getattr(r, "key", "") or "")
            scope = str(getattr(r, "scope", "") or "")
            workspace = str(getattr(r, "workspace_id", "") or "")
            canon = _canonical(getattr(r, "value_json", None))
            if key:
                settings_content_entries.append(
                    "\x00".join(
                        (
                            scope,
                            workspace,
                            str(getattr(r, "user_id", "") or ""),
                            key,
                            canon,
                        )
                    )
                )
            if uid is None or uid == DEPLOYMENT_SUBJECT_ID or not key:
                continue
            by_context.setdefault((key, scope, workspace), {}).setdefault(
                uid, set()
            ).add(canon)
        inv.settings_collisions = sorted(
            key
            for (key, _scope, _workspace), per_user in by_context.items()
            if len({v for vs in per_user.values() for v in vs}) > 1
        )
    except Exception as exc:
        # Same classification: an absent settings table holds no
        # overrides (clear and continue); any other failure is unknown
        # evidence, so clear collisions and record it rather than
        # converting blind.
        inv.settings_collisions = []
        try:
            await session.rollback()
        except Exception:
            # The surface read already failed; a failed rollback must not
            # mask the classification below.
            pass
        if not _is_absent_table_error(exc):
            _record_inventory_error(inv, "settings")

    inv.source_content_hash = hashlib.sha256(
        (
            hashlib.sha256(
                "\n".join(sorted(settings_content_entries)).encode()
            ).hexdigest()
            + "|"
            + hashlib.sha256(
                "\n".join(sorted(profile_content_entries)).encode()
            ).hexdigest()
        ).encode()
    ).hexdigest()

    # Present subsystems drive missing-coverage refusal.
    present: set[str] = set()
    for surface in list(principals) + [
        s for s in resource_counts if resource_counts[s] > 0
    ]:
        table = surface.split(".")[0]
        name = _SUBSYSTEM_BY_SURFACE.get(table)
        if name == "profile_secrets" and not inv.secret_holders:
            continue
        if name:
            present.add(name)
    if inv.secret_holders:
        present.add("profile_secrets")
    if inv.settings_collisions:
        present.add("settings")
    inv.present_subsystems = sorted(present)
    inv.resource_counts = resource_counts
    return inv


def _person_of(principal: str, user_index: dict[str, int], alias_of: dict[str, int]) -> str:
    if principal in alias_of:
        return f"alias:{alias_of[principal]}"
    return f"user:{user_index.get(principal, principal)}"


async def evaluate_disposition(
    session: AsyncSession,
    alias_groups=None,
    collision_resolutions=None,
    deployment_owned_tables=None,
    *,
    operator_authorized: bool = False,
) -> ConversionDecision:
    """Decide the conversion disposition for the current source state.

    Consults UUID principals and explicit ``alias_groups`` only. Email,
    display name, administrator flags, active-login counts, and any
    preferred-account hint are never read here and cannot merge people.
    Alias/collision/deployment evidence requires ``operator_authorized``:
    unattested caller lists must never merge people or clear refusals.

    Unknown required evidence (``inventory_errors``) refuses before any
    other check: a partial inventory must never read as empty or eligible.
    """
    options = normalize_options(alias_groups, collision_resolutions,
                                deployment_owned_tables,
                                operator_authorized=operator_authorized)
    inv = await collect_inventory(session)

    alias_of: dict[str, int] = {}
    for idx, group in enumerate(options.alias_groups):
        for member in group:
            alias_of[member] = idx
    user_index = {u: i for i, u in enumerate(sorted(inv.user_ids))}

    redacted = [_redact_id(u) for u in sorted(inv.user_ids)]
    base = dict(
        principal_count=sum(len(v) for v in inv.principals.values()),
        redacted_persons=redacted,
        present_subsystems=list(inv.present_subsystems),
    )

    if inv.inventory_errors:
        return ConversionDecision(
            disposition="unresolved_refusal",
            eligible=False,
            reason_code="inventory_unavailable",
            person_count=0,
            detail="required inventory reads failed ("
            + ",".join(sorted(inv.inventory_errors)[:5])
            + "); unknown evidence is not empty or eligible",
            **base,
        )

    has_resources = bool(
        inv.user_ids
        or any(inv.principals.values())
        or any(inv.unowned.values())
        or inv.secret_holders
        or any(c > 0 for c in inv.resource_counts.values())
    )
    if not has_resources:
        return ConversionDecision(
            disposition="fresh_init",
            eligible=True,
            reason_code="empty_source",
            person_count=0,
            **base,
        )

    if inv.unknown_principals:
        return ConversionDecision(
            disposition="unresolved_refusal",
            eligible=False,
            reason_code="unknown_principal",
            person_count=0,
            detail="retained references point at missing users; "
            "resolve attribution before conversion",
            **base,
        )

    unattested = {
        k: v
        for k, v in inv.unowned.items()
        if k.split(".")[0] not in options.deployment_owned_tables
    }
    if unattested:
        return ConversionDecision(
            disposition="unresolved_refusal",
            eligible=False,
            reason_code="unowned_rows",
            person_count=0,
            detail="unowned retained rows lack deployment-owned evidence",
            **base,
        )

    persons: dict[str, set[str]] = {}
    for u in inv.user_ids:
        persons.setdefault(_person_of(u, user_index, alias_of), set()).add(u)
    for values in inv.principals.values():
        for p in values:
            persons.setdefault(_person_of(p, user_index, alias_of), set()).add(p)
    for holder in inv.secret_holders:
        persons.setdefault(_person_of(holder, user_index, alias_of), set()).add(holder)

    if len(persons) > 1:
        return ConversionDecision(
            disposition="multi_person_refusal",
            eligible=False,
            reason_code="multi_person",
            person_count=len(persons),
            detail="retained data is attributable to more than one person; "
            "no owner is chosen, merged, or discarded",
            **base,
        )

    unresolved_collisions = [
        k
        for k in inv.settings_collisions
        if k not in options.collision_resolutions
    ]
    if unresolved_collisions:
        return ConversionDecision(
            disposition="unresolved_refusal",
            eligible=False,
            reason_code="settings_collision",
            person_count=1,
            detail="same-person settings keys collide without an explicit "
            "disposition: " + ",".join(unresolved_collisions[:5]),
            **base,
        )

    return ConversionDecision(
        disposition="eligible_conversion",
        eligible=True,
        reason_code="single_operator",
        person_count=1,
        **base,
    )


def compute_digest(
    fingerprint: str, options: ConversionOptions, schema_revision: str = SCHEMA_REVISION
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema_revision": schema_revision,
                "fingerprint": fingerprint,
                "options": options.canonical(),
            }
        ).encode()
    ).hexdigest()


@dataclass
class PreflightResult:
    digest: str
    fingerprint: str
    decision: ConversionDecision
    options: ConversionOptions
    inventory: ConversionInventory


@dataclass
class ApplyResult:
    digest: str
    published: bool
    decision: ConversionDecision
    transforms: dict[str, Any] = field(default_factory=dict)

    def to_sanitized_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "published": self.published,
            "decision": self.decision.to_sanitized_dict(),
            "transforms": {
                k: (v if isinstance(v, dict) else {"outcome": str(v)})
                for k, v in self.transforms.items()
            },
        }


async def preflight(
    session: AsyncSession,
    alias_groups=None,
    collision_resolutions=None,
    deployment_owned_tables=None,
    *,
    operator_authorized: bool = False,
) -> PreflightResult:
    """Inspect without writing; the digest binds a later apply."""
    options = normalize_options(alias_groups, collision_resolutions,
                                deployment_owned_tables,
                                operator_authorized=operator_authorized)
    inv = await collect_inventory(session)
    fingerprint = inv.fingerprint()
    decision = await evaluate_disposition(
        session,
        alias_groups=alias_groups,
        collision_resolutions=collision_resolutions,
        deployment_owned_tables=deployment_owned_tables,
        operator_authorized=operator_authorized,
    )
    digest = compute_digest(fingerprint, options)
    try:
        await session.rollback()
    except Exception:
        # Preflight is read-only: ending its transaction is cleanup, and a
        # failed cleanup must not fail the inspection itself.
        pass
    return PreflightResult(
        digest=digest,
        fingerprint=fingerprint,
        decision=decision,
        options=options,
        inventory=inv,
    )


def _conversion_advisory_key() -> int:
    """Conversion-wide PostgreSQL advisory-lock key (one per database).

    A per-proposal digest key cannot serialize two different conversion
    decisions for the same database, so mutual exclusion uses a single
    constant key for the whole conversion subsystem instead.
    """
    return int(
        hashlib.sha256(b"single-user-conversion").hexdigest()[:15], 16
    )


async def _try_conversion_claim(session: AsyncSession) -> bool | None:
    """Try a fail-fast conversion-wide claim for this database.

    Returns True when the claim was acquired, False when another
    conversion holds it, and None when the dialect has no advisory locks
    (SQLite and other hermetic test backends) so the caller falls back to
    the committed ledger-row path. A failed probe rolls back only its own
    savepoint, never the caller's work. The session-level lock survives
    the commit below and is released in the apply ``finally``.
    """
    probe = await session.begin_nested()
    try:
        claimed = (
            await session.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": _conversion_advisory_key()},
            )
        ).scalar()
        await probe.commit()
    except Exception:
        try:
            await probe.rollback()
        except Exception:
            # The probe is already dead; report "no advisory support" and
            # let the caller fall back to the ledger row.
            pass
        return None
    return bool(claimed)


async def _release_conversion_claim(session: AsyncSession) -> None:
    """Release a claim acquired by :func:`_try_conversion_claim`."""
    try:
        await session.execute(
            text("SELECT pg_advisory_unlock(:key)"),
            {"key": _conversion_advisory_key()},
        )
    except Exception:
        logger.debug("single-user conversion advisory unlock skipped", exc_info=True)
    finally:
        try:
            await session.rollback()
        except Exception:
            # Unlock acts immediately at the session level; ending the
            # statement's transaction is cleanup that must not fail loudly.
            pass


def _replay_stored_result(
    digest: str, decision: ConversionDecision, stored: dict[str, Any]
) -> ApplyResult:
    """Rebuild an :class:`ApplyResult` from a committed ledger row.

    A missing stored completion never defaults to success: ``published``
    defaults to False and the stored decision fields fall back to the
    freshly evaluated decision, never to an assumed success.
    """
    stored_decision = stored.get("decision") or decision.to_sanitized_dict()
    restored = ConversionDecision(
        disposition=str(stored_decision.get("disposition", decision.disposition)),
        eligible=bool(stored_decision.get("eligible", decision.eligible)),
        reason_code=str(stored_decision.get("reason_code", decision.reason_code)),
        person_count=int(stored_decision.get("person_count", 0)),
        principal_count=int(stored_decision.get("principal_count", 0)),
        redacted_persons=list(stored_decision.get("persons", [])),
        detail=str(stored_decision.get("detail", "")),
        present_subsystems=list(stored_decision.get("present_subsystems", [])),
    )
    return ApplyResult(
        digest=digest,
        published=bool(stored.get("published", False)),
        decision=restored,
        transforms=dict(stored.get("transforms", {})),
    )


TransformFn = Callable[..., Any]


async def _run_transform(fn: TransformFn, session: AsyncSession, eligible: list[str]):
    result = fn(session, eligible)
    if inspect.isawaitable(result):
        return await result
    return result


def _scrub_outcome_keys(mapping: dict[str, Any]) -> dict[str, Any]:
    """Drop secret-bearing keys from a stored transform outcome.

    The stored ledger summary is metadata only: any top-level or item
    key naming a secret, token, or value is stripped before persistence
    so a transform can never publish credential material alongside its
    outcome counts.
    """
    return {
        k: v
        for k, v in mapping.items()
        if "secret" not in str(k).lower()
        and "token" not in str(k).lower()
        and "value" not in str(k).lower()
    }


async def apply_conversion(
    session: AsyncSession,
    preflight_result: PreflightResult,
    *,
    operator_authorized: bool,
    transforms: dict[str, TransformFn] | None = None,
) -> ApplyResult:
    """Apply a preflight-bound conversion in one atomic transaction.

    The conversion-wide advisory claim is acquired *before* the
    authoritative inventory read and held through the single commit, so a
    concurrent writer cannot slip between eligibility and publication and
    two different conversion proposals for the same database serialize
    instead of racing. A changed source fingerprint still raises
    :class:`StaleAttributionError` for changes that preceded the claim.

    Refusals (ineligible disposition or missing transform coverage)
    perform no conversion-side writes. No ``in_progress`` row is ever
    committed: interruption before commit leaves nothing behind, so a
    retry converges instead of rejecting a self-made lock. A stale
    ``in_progress`` row from an older revision is reclaimed only while
    holding the conversion claim (which proves no live writer exists);
    without the claim it fails closed with
    :class:`ConcurrentConversionError` rather than stealing a live writer.
    Reruns with the same digest replay the recorded result, and a missing
    stored completion replays as unpublished, never as success.
    """
    if not operator_authorized:
        raise ConversionAuthorizationError(
            "single-user conversion apply requires explicit operator authorization"
        )
    transforms = dict(transforms or {})
    options = preflight_result.options

    claimed = await _try_conversion_claim(session)
    if claimed is False:
        raise ConcurrentConversionError(
            "another single-user conversion holds this database"
        )
    try:
        fresh = await collect_inventory(session)
        fresh_fingerprint = fresh.fingerprint()
        if fresh_fingerprint != preflight_result.fingerprint:
            raise StaleAttributionError(
                "source ownership or data changed since preflight; "
                "re-run preflight instead of applying stale attribution"
            )
        expected = compute_digest(fresh_fingerprint, options)
        if expected != preflight_result.digest:
            raise StaleAttributionError(
                "preflight digest no longer binds the current source and options"
            )

        decision = await evaluate_disposition(
            session,
            alias_groups=[set(g) for g in options.alias_groups],
            collision_resolutions=dict(options.collision_resolutions),
            deployment_owned_tables=set(options.deployment_owned_tables),
            operator_authorized=True,
        )
        if not decision.eligible:
            return ApplyResult(
                digest=preflight_result.digest, published=False, decision=decision
            )

        missing = [s for s in fresh.present_subsystems if s not in transforms]
        if missing:
            return ApplyResult(
                digest=preflight_result.digest,
                published=False,
                decision=ConversionDecision(
                    disposition="unresolved_refusal",
                    eligible=False,
                    reason_code="missing_transform_coverage",
                    person_count=decision.person_count,
                    principal_count=decision.principal_count,
                    redacted_persons=list(decision.redacted_persons),
                    detail="retained subsystems lack registered transforms: "
                    + ",".join(sorted(missing)),
                    present_subsystems=list(fresh.present_subsystems),
                ),
            )

        ledger_cls = _ledger_class()
        try:
            existing = (
                await session.execute(
                    select(ledger_cls).where(
                        ledger_cls.preflight_digest == preflight_result.digest
                    )
                )
            ).scalars().first()
            if existing is not None:
                if str(getattr(existing, "status", "")) == "complete":
                    stored = dict(getattr(existing, "result_json", None) or {})
                    return _replay_stored_result(
                        preflight_result.digest, decision, stored
                    )
                if claimed is True:
                    # We hold the conversion-wide claim, so no live writer
                    # can own this row: it is a stale remnant of an older
                    # revision that committed ``in_progress`` before
                    # transforming. Reclaim it by deleting and redoing the
                    # work below instead of orphaning the digest forever.
                    logger.warning(
                        "single-user conversion reclaiming stale in_progress row"
                    )
                    await session.delete(existing)
                    await session.flush()
                else:
                    raise ConcurrentConversionError(
                        "a conversion ledger row is in progress and liveness "
                        "cannot be proven without the conversion claim"
                    )

            eligible_ids = sorted(set(fresh.user_ids) | set(fresh.secret_holders))
            outcomes: dict[str, Any] = {}
            for name in sorted(transforms):
                outcome = await _run_transform(transforms[name], session, eligible_ids)
                if isinstance(outcome, dict):
                    outcomes[name] = _scrub_outcome_keys(dict(outcome))
                    # Items nested under a transform are scrubbed the same
                    # way; only outcome metadata is retained.
                    items = outcomes[name].get("items")
                    if isinstance(items, list):
                        outcomes[name]["items"] = [
                            _scrub_outcome_keys(dict(i))
                            if isinstance(i, dict)
                            else {}
                            for i in items
                        ]
                else:
                    outcomes[name] = {"outcome": str(outcome)}

            result = ApplyResult(
                digest=preflight_result.digest,
                published=True,
                decision=decision,
                transforms=outcomes,
            )
            try:
                session.add(
                    ledger_cls(
                        preflight_digest=preflight_result.digest,
                        status="complete",
                        result_json=result.to_sanitized_dict(),
                    )
                )
                await session.commit()
            except IntegrityError:
                # A concurrent writer committed a ledger row for this digest
                # between our check and our commit (possible only where the
                # advisory claim is unavailable). Reconcile instead of
                # repeating the write blindly.
                await session.rollback()
                raced = (
                    await session.execute(
                        select(ledger_cls).where(
                            ledger_cls.preflight_digest == preflight_result.digest
                        )
                    )
                ).scalars().first()
                if raced is not None and str(
                    getattr(raced, "status", "")
                ) == "complete":
                    stored = dict(getattr(raced, "result_json", None) or {})
                    return _replay_stored_result(
                        preflight_result.digest, decision, stored
                    )
                raise ConcurrentConversionError(
                    "conversion ledger conflicted without a completed winner"
                )
            return result
        except Exception:
            # Transforms and the completion record commit atomically: any
            # failure rolls back incomplete database work so a retry
            # converges. ConcurrentConversionError from the reconcile path
            # above is re-raised unchanged after the rollback. External
            # effects are reconciled from durable operation evidence
            # (idempotent migrate/reuse, no-op rewires) rather than
            # repeated blindly.
            await session.rollback()
            raise
    finally:
        if claimed:
            await _release_conversion_claim(session)


async def startup_guard_decision(
    session: AsyncSession,
    alias_groups=None,
    collision_resolutions=None,
    deployment_owned_tables=None,
) -> ConversionDecision:
    """Read-only guard for startup/upgrade routes: never writes, never seeds."""
    return await evaluate_disposition(
        session,
        alias_groups=alias_groups,
        collision_resolutions=collision_resolutions,
        deployment_owned_tables=deployment_owned_tables,
    )


async def _rewire_dangling_profile_refs(
    session: AsyncSession, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Point provider profiles at migrated ``db://`` secrets where safe.

    For each migrated legacy field, provider profiles that declare the
    same secret key are rewired only when their current reference cannot
    resolve: a missing/blank ref, or an ``env://VAR`` ref whose variable
    is unset in this deployment. A working ``env://`` ref (variable set)
    or an existing ``db://`` ref is left untouched so conversion never
    clobbers effective credentials with a stale legacy copy. Rewiring
    goes through :func:`rewire_provider_profile_secret_refs`, which
    verifies each secret exists ACTIVE before publishing the reference
    and is a no-op on identical refs (retry/interruption safe). Any
    lookup failure raises: publishing "complete" while credentials are
    unwired would report success for unusable provider access.
    Returns metadata only (profile ids, keys, outcomes, generations).
    """
    from sqlalchemy import select as _select

    from api_service.db.models import ManagedAgentProviderProfile
    from api_service.services.profile_secret_migration import (
        rewire_provider_profile_secret_refs,
    )
    from moonmind.auth.secret_refs import SecretBackend, parse_secret_ref

    targets: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field", "") or "")
        slug = str(item.get("slug", "") or "")
        if field and slug:
            targets[field] = slug
    if not targets:
        return []
    profiles = (
        await session.execute(_select(ManagedAgentProviderProfile))
    ).scalars().all()
    rewired: list[dict[str, Any]] = []
    for profile in profiles:
        refs = dict(getattr(profile, "secret_refs", None) or {})
        pending: dict[str, str] = {}
        for field, slug in sorted(targets.items()):
            if field not in refs:
                continue
            current = refs.get(field)
            if current is None or str(current).strip() == "":
                pending[field] = f"db://{slug}"
                continue
            try:
                parsed = parse_secret_ref(str(current))
            except Exception:
                # An unparseable existing ref resolves to nothing: treat
                # it as dangling and rewire to the migrated secret.
                pending[field] = f"db://{slug}"
                continue
            if parsed.backend == SecretBackend.DB_ENCRYPTED:
                continue
            if parsed.backend == SecretBackend.ENV and not os.environ.get(
                parsed.locator, ""
            ):
                # Dangling env ref: the variable is unset, so the profile
                # has no effective credential; the migrated copy restores it.
                pending[field] = f"db://{slug}"
        if not pending:
            continue
        outcome = await rewire_provider_profile_secret_refs(
            session,
            profile_id=str(getattr(profile, "profile_id")),
            refs=pending,
            commit=False,
        )
        rewired.append(
            {
                "profile_id": str(outcome.get("profile_id", "")),
                "keys": sorted(pending),
                "outcome": str(outcome.get("outcome", "")),
                "generation": int(outcome.get("credential_generation", 0) or 0),
            }
        )
    return rewired


async def profile_secrets_transform(
    session: AsyncSession, eligible_ids: list[str]
) -> dict[str, Any]:
    """Real ``profile_secrets`` subsystem transform (#4346 via #4349).

    Runs inside :func:`apply_conversion` after attribution is proven, so
    eligibility enforcement is bypassed here (the owning guarded migration
    already resolved it). Converts eligible legacy ``UserProfile``-held
    secrets into managed-secret references without publishing plaintext,
    then rewires provider profiles whose matching secret refs are
    dangling onto the migrated references. A published conversion
    therefore leaves provider credentials usable, not merely copied.
    ``eligible_ids`` is accepted for the shared transform signature and
    ignored beyond attribution already proven by the entrypoint.
    """
    from api_service.services.profile_secret_migration import (
        migrate_legacy_profile_secrets,
    )

    summary = await migrate_legacy_profile_secrets(
        session, commit=False, enforce_eligibility=False
    )
    raw_items = [i for i in summary.get("items", []) if isinstance(i, dict)]
    rewires = await _rewire_dangling_profile_refs(session, raw_items)
    return {
        "migrated": int(summary.get("migrated", 0)),
        "created": int(summary.get("created", 0)),
        "reused": int(summary.get("reused", 0)),
        "rewired_profiles": len(rewires),
        "rewires": rewires,
        "items": [
            {
                k: v
                for k, v in item.items()
                if "secret" not in k.lower()
                and "token" not in k.lower()
                and "value" not in k.lower()
            }
            for item in raw_items
        ],
    }


#: Production subsystem transforms registered into the shared entrypoint.
#: ``profile_secrets`` is the concrete #4349 conversion. Settings, presets,
#: schedules, artifacts, temporal, and workflow transforms are owned by
#: subsystem child issues; until they register here, an eligible source
#: holding those subsystems refuses with ``missing_transform_coverage``
#: rather than exposing a partially converted result.
DEFAULT_SUBSYSTEM_TRANSFORMS: dict[str, TransformFn] = {
    "profile_secrets": profile_secrets_transform,
}


async def run_guarded_upgrade(
    session: AsyncSession,
    *,
    operator_authorized: bool,
    alias_groups=None,
    collision_resolutions=None,
    deployment_owned_tables=None,
    transforms: dict[str, TransformFn] | None = None,
) -> ApplyResult:
    """Shared transaction/cutover entrypoint for every upgrade/startup route.

    This is the only supported way to publish a conversion candidate:
    capture a consistent preflight, then apply it with real subsystem
    transforms through versioned migration 386 + ledger idempotency.
    Alembic revision 386 creates only the ledger table and never applies a
    destructive conversion as a schema-upgrade side effect; CLI, worker, and
    HTTP startup routes must call this entrypoint instead of invoking
    subsystem conversions directly. Refusals perform no conversion-side
    writes; stale sources raise :class:`StaleAttributionError`.
    """
    selected = (
        dict(DEFAULT_SUBSYSTEM_TRANSFORMS) if transforms is None else dict(transforms)
    )
    bound = await preflight(
        session,
        alias_groups=alias_groups,
        collision_resolutions=collision_resolutions,
        deployment_owned_tables=deployment_owned_tables,
        operator_authorized=operator_authorized,
    )
    return await apply_conversion(
        session,
        bound,
        operator_authorized=operator_authorized,
        transforms=selected,
    )
