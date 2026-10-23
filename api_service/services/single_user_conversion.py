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

Consistency: the existing conversion advisory claim serializes converters;
transaction-held locks on the authoritative inventory tables exclude ordinary
writers until publication. SQLite reserves its writer transaction instead.
Eligible preflight binds ownership, counts, and content hashes (including retained
artifact children) to detect changes preceding those locks. Inspection errors
roll back only their savepoint, never the transaction holding authority locks.
No in-progress row is committed; interruption rolls back grants and completion
together. Historical completion from an older revision is not current readiness.

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

from sqlalchemy import String, UniqueConstraint, and_, cast, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from api_service.db.models import Base, Uuid, mutable_json_dict

logger = logging.getLogger(__name__)

SCHEMA_REVISION = "400_job_operator_access"

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
    ("presets", "scope_ref", "preset_scope"),
    ("preset_favorites", "user_id", "uuid_fk"),
    ("preset_recents", "user_id", "uuid_fk"),
    ("settings_overrides", "user_id", "uuid_fk"),
    ("settings_audit_events", "user_id", "uuid_fk"),
    # Audit actors record who attempted a change (including rejected writes),
    # not ownership of retained settings. Preserve them without attribution.
    ("temporal_executions", "owner_id", "user_temporal"),
    ("temporal_execution_sources", "owner_id", "user_temporal"),
    ("workflow_execution_source_mappings", "owner_id", "user_temporal"),
    ("container_jobs", "owner_id", "user_job"),
    ("managed_agent_oauth_sessions", "requested_by_user_id", "uuid_text"),
    ("temporal_artifacts", "created_by_principal", "artifact_principal"),
    ("temporal_artifact_use_claims", "owner_principal", "active_artifact_claim"),
)

# Schedules are already instance resources: their supported producer stores
# no human owner, and conversion preserves them. Non-null schedule references
# still participate in the principal inventory above.
# Null-owner rows in these tables are ambiguous retained data, not
# deployment-owned by assumption. Each entry names a single owner
# column whose null (on a non-empty table) refuses conversion.
_UNOWNED_TRACKED: tuple[tuple[str, str], ...] = (
    ("presets", "created_by"),
    ("temporal_artifacts", "created_by_principal"),
)
# Execution projections are already instance-visible and remain unchanged.
# Their populated human owners still participate in principal attribution above;
# a missing historical owner cannot authorize linked artifact recovery.

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
    "workflow_artifacts": "workflows",
    "temporal_artifact_links": "artifacts",
    "temporal_artifact_pins": "artifacts",
    "temporal_artifact_use_claims": "artifacts",
    "temporal_artifact_deletion_intents": "artifacts",
    "temporal_executions": "temporal",
    "temporal_execution_sources": "temporal",
    "workflow_execution_source_mappings": "temporal",
    "workflow_runs": "workflows",
    "container_jobs": "container_jobs",
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


_ARTIFACT_EVIDENCE_TABLES = (
    "temporal_artifacts", "temporal_artifact_links", "temporal_artifact_pins",
    "temporal_artifact_use_claims", "temporal_artifact_deletion_intents",
    "workflow_artifacts",
)


_ARTIFACT_MACHINE_PRINCIPALS = ("operator", "system", "agent_workflow", "agent_runtime")
_ARTIFACT_MACHINE_PREFIXES = ("service:", "workflow:", "system:")
# SQL's default space-only trim would mistake a whitespace-only suffix for a
# machine identity. Match Python str.strip across SQLite and PostgreSQL.
_PRINCIPAL_WHITESPACE = (
    "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


def _is_artifact_machine_principal(raw: Any) -> bool:
    value = str(raw or "").strip()
    return value in _ARTIFACT_MACHINE_PRINCIPALS or any(
        value.startswith(prefix) and bool(value[len(prefix):].strip())
        for prefix in _ARTIFACT_MACHINE_PREFIXES
    )


def _artifact_human_principal(raw: Any) -> str | None:
    # Job evidence uses the owner's serialized type prefix. Preserve that raw
    # namespace; only attribution inspection reads the UUID inside it.
    value = str(raw or "")
    return _looks_like_uuid(value[5:] if value.startswith("user:") else value)


def _uses_reserved_disabled_scope() -> bool:
    """Recognize predecessor local scope during conversion, never as admission."""
    return (
        os.environ.get("AUTH_PROVIDER", "disabled").strip().lower() == "disabled"
        and _looks_like_uuid(os.environ.get("DEFAULT_USER_ID") or DEPLOYMENT_SUBJECT_ID)
        == DEPLOYMENT_SUBJECT_ID
    )


async def _has_pending_artifact_recovery(session: AsyncSession) -> bool:
    """Probe for work without loading artifact rows or retained child content.

    This only selects at most one ID; a no-match query can still scan the
    table. Unknown, null and malformed owners require authoritative inspection.
    The probe never supplies attribution or grants recovery.
    """
    from api_service.db.models import TemporalArtifact

    def stripped(value):
        return func.ltrim(
            func.rtrim(value, _PRINCIPAL_WHITESPACE), _PRINCIPAL_WHITESPACE,
        )

    creator = func.coalesce(TemporalArtifact.created_by_principal, "")
    normalized = stripped(creator)
    no_recovery_needed = or_(
        normalized.in_(_ARTIFACT_MACHINE_PRINCIPALS),
        *(and_(
            # LIKE would treat uppercase prefixes as machines on SQLite.
            func.substr(normalized, 1, len(prefix)) == prefix,
            stripped(func.substr(normalized, len(prefix) + 1)) != "",
        ) for prefix in _ARTIFACT_MACHINE_PREFIXES),
    )
    if _uses_reserved_disabled_scope():
        default_owner = DEPLOYMENT_SUBJECT_ID
        if default_owner is not None:
            # The unchanged raw-owner equality already admits this exact
            # disabled request creator, even without a retained User row.
            no_recovery_needed = or_(
                no_recovery_needed,
                creator.in_((default_owner, f"user:{default_owner}")),
            )
    try:
        async with session.begin_nested():
            pending = (
                await session.execute(
                    select(TemporalArtifact.artifact_id)
                    .where(
                        TemporalArtifact.operator_recovery_granted.is_not(True),
                        ~no_recovery_needed,
                    )
                    .limit(1)
                )
            ).scalars().first()
    except Exception as exc:
        if _is_absent_table_error(exc):
            return False
        raise
    return pending is not None


async def _lock_conversion_evidence(session: AsyncSession) -> None:
    """Hold ordinary authority writers outside the inventory/commit interval.

    The converter advisory claim alone cannot exclude ordinary writers. Lock
    precisely the tables read by attribution, including empty tables where a
    second human could appear. Missing tables in partial schemas have no rows.
    """
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        # SQLite has no table locks. Acquire its writer reservation before any
        # savepoint so releasing an inspection savepoint cannot end protection.
        await session.execute(text("BEGIN IMMEDIATE"))
        return
    if dialect != "postgresql":
        raise ConcurrentConversionError("conversion requires an authority writer lock")
    from sqlalchemy import inspect as sa_inspect

    connection = await session.connection()
    present = set(await connection.run_sync(lambda c: sa_inspect(c).get_table_names()))
    names = {
        "user",
        "user_profile",
        "retired_user",
        "retired_user_profile",
        "settings_overrides",
        "single_user_conversion_runs",
    }
    names.update(t for t, _c, _k in _PRINCIPAL_SURFACES)
    names.update(t for t, _c in _UNOWNED_TRACKED)
    names.update(_UNOWNED_CONJUNCTIONS)
    names.update(_ARTIFACT_EVIDENCE_TABLES)
    quoted = ", ".join('"' + name + '"' for name in sorted(names & present))
    if quoted:
        await session.execute(text(f"LOCK TABLE {quoted} IN SHARE ROW EXCLUSIVE MODE NOWAIT"))


async def _table_content_digest(session: AsyncSession, table, columns) -> tuple[int, str]:
    """Row count plus an order-independent digest of ``columns`` in every row.

    PostgreSQL hashes rows in place and sums them as a multiset: materializing
    millions of retained artifact rows in Python held API startup for minutes.
    Table and column names come from ORM metadata, never from input.
    """
    if session.get_bind().dialect.name == "postgresql":
        names = ", ".join(f'"{column.name}"' for column in columns)
        count, low, high = (
            await session.execute(text(
                "SELECT count(*),"
                " coalesce(sum(('x' || substr(h, 1, 16))::bit(64)::bigint::numeric), 0),"
                " coalesce(sum(('x' || substr(h, 17, 16))::bit(64)::bigint::numeric), 0)"
                f" FROM (SELECT md5(json_build_array({names})::text) AS h"
                f' FROM "{table.name}") AS row_hashes'
            ))
        ).one()
        return int(count), f"{low}:{high}"
    rows = (await session.execute(select(*columns))).all()
    entries = sorted(_canonical(list(row)) for row in rows)
    return len(rows), hashlib.sha256("\n".join(entries).encode()).hexdigest()


async def collect_inventory(session: AsyncSession) -> ConversionInventory:
    """Trace every retained user reference (SQL + serialized + in-flight).

    Never records emails, names, secret values, or resource content —
    only UUID principals, counts, and collision keys.
    """
    from api_service.migrations.account_source import source_tables, source_user_ids

    inv = ConversionInventory()
    # Certificate-dependent exemptions are bound by the unowned-row counts in
    # the fingerprint. Hashing the ledger itself would invalidate our own
    # completed conversion's idempotent replay even when no row relies on it.
    instance_admitted = await _has_certified_instance_admission(session, inv)
    users = await source_user_ids(session)
    inv.user_ids = sorted(str(u) for u in users)
    user_set = set(inv.user_ids)
    preserve_deployment_scope = (
        _uses_reserved_disabled_scope()
        and DEPLOYMENT_SUBJECT_ID not in user_set
    )

    from api_service.db.models import Base

    tables = {**Base.metadata.tables, **await source_tables(session)}
    principals: dict[str, set[str]] = {}
    unowned: dict[str, int] = {}
    resource_counts: dict[str, int] = {}

    for table_name, column_name, kind in _PRINCIPAL_SURFACES:
        table = tables.get(table_name)
        if table is None or column_name not in table.c:
            continue
        col = table.c[column_name]
        # Attribution depends on identities, not the number of rows written by
        # each identity. Keep millions of repeated artifact creators in SQL;
        # the separate count below still binds retained row counts.
        stmt = select(col).where(col.is_not(None)).distinct()
        if table_name == "presets":
            # Creator UUID on an admitted instance preset is preserved history.
            stmt = stmt.where(table.c.scope_type.is_not(None))
        if kind == "preset_scope":
            stmt = stmt.where(cast(table.c.scope_type, String) == "personal")
        if kind == "active_artifact_claim":
            # Expired claims no longer protect retention or count quota. Their
            # owner, like pin/deletion actors, is preserved provenance only.
            stmt = stmt.where(table.c.expires_at > func.now())
        if kind in {"user_temporal", "user_job"} and "owner_type" in table.c:
            stmt = stmt.where(func.lower(cast(table.c.owner_type, String)) == "user")
        try:
            async with session.begin_nested():
                rows = (await session.execute(stmt)).all()
        except Exception as exc:
            if not _is_absent_table_error(exc):
                _record_inventory_error(inv, f"{table_name}.{column_name}")
            continue
        for (raw,) in rows:
            artifact_owner = (
                _artifact_human_principal(raw)
                if kind in {"artifact_principal", "active_artifact_claim"}
                else None
            )
            if (
                preserve_deployment_scope
                and kind
                in {
                    "artifact_principal",
                    "active_artifact_claim",
                    "user_job",
                    "user_temporal",
                }
                and (
                    raw == DEPLOYMENT_SUBJECT_ID
                    or (kind != "user_job" and raw == f"user:{DEPLOYMENT_SUBJECT_ID}")
                )
            ):
                # Built-in account-free scope is retained, not a person alias.
                # A persisted zero User disables this classification entirely.
                continue
            norm = artifact_owner or _looks_like_uuid(raw)
            if (
                kind == "preset_scope"
                and norm == DEPLOYMENT_SUBJECT_ID
                and norm not in user_set
            ):
                continue  # retained synthetic local scope, not a real User
            if norm and not (kind == "uuid_fk" and norm == DEPLOYMENT_SUBJECT_ID):
                principals.setdefault(f"{table_name}.{column_name}", set()).add(norm)
            elif kind == "preset_scope" and not norm:
                _record_inventory_error(inv, "principal:presets.scope_ref")
            elif kind == "user_job" or (
                kind in {"artifact_principal", "active_artifact_claim"}
                and not _is_artifact_machine_principal(raw)
            ):
                # Unknown text is not proof of a machine or a person.
                _record_inventory_error(inv, f"principal:{table_name}.{column_name}")
        total = await _probe_count(
            session, table, col,
            inv=inv, surface=f"count:{table_name}.{column_name}",
        )
        if total is not None:
            resource_counts[f"{table_name}.{column_name}"] = total

    artifact_content_entries: list[str] = []
    for table_name in _ARTIFACT_EVIDENCE_TABLES:
        if inv.inventory_errors:
            # Unknown principal evidence already forbids conversion. Hashing
            # retained content cannot repair it or authorize a mutation, and
            # can hold startup for minutes on a large refused source. Keep the
            # original error and leave source values untouched.
            break
        table = tables.get(table_name)
        if table is None:
            continue
        # Conversion-owned output is not source attribution. Everything else,
        # including child authority and quarantine, binds stale preflight checks.
        columns = [c for c in table.c if c.name != "operator_recovery_granted"]
        missing_creator = False
        try:
            async with session.begin_nested():
                count, digest = await _table_content_digest(session, table, columns)
                if table_name == "temporal_artifacts":
                    creator = table.c.created_by_principal
                    missing_creator = (
                        await session.execute(
                            select(creator).where(creator.is_(None)).limit(1)
                        )
                    ).first() is not None
        except Exception as exc:
            if not _is_absent_table_error(exc):
                _record_inventory_error(inv, f"artifact_evidence:{table_name}")
            continue
        resource_counts[f"{table_name}.rows"] = count
        artifact_content_entries.append(f"{table_name}:{digest}")
        if missing_creator:
            # Deployment attestation alone cannot reconstruct a lost human
            # raw owner. Refuse until that disposition has an owned design.
            _record_inventory_error(inv, "artifact_recovery:missing_creator")

    # Live parents need their owning transforms even when machine-owned and
    # their optional human owner columns are empty. Historical artifact links
    # alone do not invent a parent or an unrelated transform requirement.
    for table_name in (
        "temporal_executions",
        "temporal_execution_sources",
        "workflow_execution_source_mappings",
        "workflow_runs",
    ):
        table = tables.get(table_name)
        if table is not None:
            total = await _probe_count(
                session, table, next(iter(table.primary_key.columns)),
                inv=inv, surface=f"count:{table_name}.rows",
            )
            if total is not None:
                resource_counts[f"{table_name}.rows"] = total

    inv.principals = {k: sorted(v) for k, v in principals.items()}
    all_principals = sorted({p for vs in principals.values() for p in vs})
    inv.unknown_principals = sorted(p for p in all_principals if p not in user_set)

    # Unowned rows: resource exists without attribution in tracked tables.
    for table_name, column_name in _UNOWNED_TRACKED:
        table = tables.get(table_name)
        if table is None or column_name not in table.c:
            continue
        col = table.c[column_name]
        probe = await session.begin_nested()
        try:
            if table_name == "presets" and "seed_source" in table.c:
                predicates = [
                    col.is_(None),
                    table.c.seed_source.is_(None),
                    table.c.scope_type.is_not(None),
                    or_(
                        cast(table.c.scope_type, String) != "personal",
                        table.c.scope_ref.is_(None),
                    ),
                ]
                if instance_admitted:
                    # The predecessor OperatorAdmission has no User.id and
                    # saved custom presets as global with no scope_ref or
                    # seed_source. Only that exact shape inherits certified
                    # instance provenance; missing personal scopes do not.
                    predicates.append(
                        or_(
                            cast(table.c.scope_type, String) != "global",
                            table.c.scope_ref.is_not(None),
                        )
                    )
                nulls = (
                    await session.execute(
                        select(func.count()).select_from(table).where(*predicates)
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
        table = tables.get(table_name)
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
        from api_service.migrations.account_source import source_profiles
        from api_service.services.profile_secret_migration import LEGACY_FIELDS

        async with session.begin_nested():
            secret_rows = await source_profiles(session)
        holders = set()
        for p in secret_rows:
            for column, _short in LEGACY_FIELDS:
                try:
                    value = p.get(column)
                except Exception:
                    # An unreadable legacy ciphertext is unknown evidence:
                    # record which column failed and keep the holders found
                    # so far; the disposition still refuses on the error.
                    _record_inventory_error(inv, f"profile_secrets:{column}")
                    continue
                if isinstance(value, str) and value.strip():
                    uid = _looks_like_uuid(p.get("user_id"))
                    if uid:
                        holders.add(uid)
                    user_hex = str(p.get("user_id", "") or "")
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
        if not _is_absent_table_error(exc):
            _record_inventory_error(inv, "profile_secrets")

    # Settings collisions compare the explicit choices that were effective in
    # each retained context (instance, workspace, or personal), not raw row
    # counts. Every row still feeds the content tripwire so a same-count value
    # edit changes the fingerprint.
    settings_content_entries: list[str] = []
    try:
        from api_service.db.models import SettingsOverride

        async with session.begin_nested():
            rows = (await session.execute(select(SettingsOverride))).scalars().all()
        for r in rows:
            key = str(getattr(r, "key", "") or "")
            if key:
                settings_content_entries.append(
                    "\x00".join(
                        (
                            str(getattr(r, "scope", "") or ""),
                            str(getattr(r, "workspace_id", "") or ""),
                            str(getattr(r, "user_id", "") or ""),
                            key,
                            _canonical(getattr(r, "value_json", None)),
                        )
                    )
                )
        instance_edited = await _instance_edited_settings_keys(session)
        inv.settings_collisions = sorted(
            key
            for key, choices in _legacy_settings_choices(rows, instance_edited).items()
            if len(choices) > 1
        )
        # Retained predecessor rows need the settings transform whatever
        # subject columns they carry; instance rows are already converted.
        resource_counts["settings_overrides.scope"] = sum(
            1 for row in rows if row.scope is not None
        )
    except Exception as exc:
        # Same classification: an absent settings table holds no
        # overrides (clear and continue); any other failure is unknown
        # evidence, so clear collisions and record it rather than
        # converting blind.
        inv.settings_collisions = []
        if not _is_absent_table_error(exc):
            _record_inventory_error(inv, "settings")

    # Preset definitions, preference timestamps and future schedule targets
    # participate in stale-preflight fencing, not only their row counts.
    preset_content_entries: list[str] = []
    for table_name in (
        "presets",
        "preset_favorites",
        "preset_recents",
        "recurring_workflow_definitions",
    ):
        if inv.inventory_errors:
            # Refused attribution cannot be repaired by hashing more content.
            # Preserve the same bounded startup path as artifact inventory.
            break
        table = tables.get(table_name)
        if table is None:
            continue
        try:
            async with session.begin_nested():
                _count, digest = await _table_content_digest(session, table, list(table.c))
            preset_content_entries.append(f"{table_name}:{digest}")
        except SQLAlchemyError as exc:
            if not _is_absent_table_error(exc):
                _record_inventory_error(inv, f"preset_evidence:{table_name}")

    inv.source_content_hash = hashlib.sha256(
        (
            hashlib.sha256(
                "\n".join(sorted(settings_content_entries)).encode()
            ).hexdigest()
            + "|"
            + hashlib.sha256("\n".join(sorted(artifact_content_entries)).encode()).hexdigest()
            + "|"
            + hashlib.sha256(
                "\n".join(sorted(profile_content_entries + preset_content_entries)).encode()
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
    return _decision_from_inventory(inv, options)


def _decision_from_inventory(
    inv: ConversionInventory, options: ConversionOptions
) -> ConversionDecision:
    """Evaluate the same snapshot that binds preflight or locked apply."""
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
            "schema_revision": SCHEMA_REVISION,
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
    decision = _decision_from_inventory(inv, options)
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
    savepoint, never the caller's work. The transaction-level claim ends
    atomically with commit/rollback, independent of connection-pool checkout.
    """
    if session.get_bind().dialect.name == "sqlite":
        return None
    probe = await session.begin_nested()
    try:
        claimed = (
            await session.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
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
    """End the transaction holding a claim acquired by the helper above."""
    await session.rollback()


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
        published=(stored.get("schema_revision") == SCHEMA_REVISION
                   and bool(stored.get("published", False))),
        decision=restored,
        transforms=dict(stored.get("transforms", {})),
    )


def _is_production_completion(
    result: dict[str, Any],
    *,
    schema_revision: str = SCHEMA_REVISION,
    required_transforms: tuple[str, ...] | None = None,
) -> bool:
    """Require committed production proof, not truthy serialized data.

    Current readiness always uses the defaults. Explicit predecessor arguments
    qualify historical admission only, never current conversion completion.
    """
    if not isinstance(result, dict) or result.get("schema_revision") != schema_revision:
        return False
    decision = result.get("decision")
    if (
        result.get("production_transforms_completed") is not True
        or result.get("published") is not True
        or not isinstance(decision, dict)
        or decision.get("eligible") is not True
    ):
        return False
    outcomes = result.get("transforms")
    if not isinstance(outcomes, dict) or any(
        not isinstance(outcomes.get(name), dict)
        for name in (required_transforms if required_transforms is not None
                     else DEFAULT_SUBSYSTEM_TRANSFORMS)
    ):
        return False
    artifact_outcome = outcomes["artifacts"]
    # A serialized boolean is not a certified count.
    if any(
        type(artifact_outcome.get(name)) is not int  # noqa: E721
        for name in ("recovery_grants", "preserved")
    ):
        return False
    if decision.get("disposition") not in {"fresh_init", "eligible_conversion"}:
        return False
    return (
        0 <= artifact_outcome["recovery_grants"] <= artifact_outcome["preserved"]
    )


async def _has_certified_instance_admission(
    session: AsyncSession,
    inventory: ConversionInventory,
) -> bool:
    """Recognize account-free rows emitted after a proven instance cutover.

    Revisions 396 and 397 established operator admission before presets and
    then settings had their own transforms. Their production certificates can
    explain predecessor instance rows, but cannot publish or bypass the
    current conversion. Uncertified/custom completion and older unknown
    contracts grant nothing.
    """
    predecessors = {
        "399_retire_shared_accounts": (
            "accounts",
            "profile_secrets",
            "artifacts",
            "presets",
            "settings",
        ),
        "398_instance_settings": (
            "profile_secrets",
            "artifacts",
            "presets",
            "settings",
        ),
        "396_artifact_operator_recovery": ("profile_secrets", "artifacts"),
        "397_instance_preset_catalog": ("profile_secrets", "artifacts", "presets"),
    }
    ledger = _ledger_class()
    try:
        async with session.begin_nested():
            results = (
                await session.execute(
                    select(ledger.result_json).where(
                        ledger.status == "complete",
                        ledger.result_json["schema_revision"].as_string().in_(
                            (*predecessors, SCHEMA_REVISION)
                        ),
                    )
                )
            ).scalars().all()
    except Exception as exc:
        if not _is_absent_table_error(exc):
            _record_inventory_error(inventory, "instance_admission")
        return False
    return any(
        _is_production_completion(result)
        or any(
            _is_production_completion(
                result, schema_revision=revision, required_transforms=required
            )
            for revision, required in predecessors.items()
        )
        for result in results
    )


async def completed_guarded_upgrade(session: AsyncSession) -> ApplyResult | None:
    """Read one certified current cutover without materializing retained history.

    The marker is committed only after the exact production transforms run.
    Generic completion rows, custom transforms and older revisions cannot
    establish that artifact recovery was converted. Outstanding recovery
    work requires a new authoritative preflight and locked apply instead.
    """
    ledger_cls = _ledger_class()
    stored = ledger_cls.result_json
    try:
        async with session.begin_nested():
            row = (
                await session.execute(
                    select(ledger_cls)
                    .where(
                        ledger_cls.status == "complete",
                        stored["schema_revision"].as_string() == SCHEMA_REVISION,
                        stored["published"].as_boolean().is_(True),
                        stored["decision"]["eligible"].as_boolean().is_(True),
                        stored["decision"]["disposition"].as_string().in_(
                            ("fresh_init", "eligible_conversion")
                        ),
                        stored["production_transforms_completed"]
                        .as_boolean().is_(True),
                        stored["transforms"]["artifacts"]["recovery_grants"]
                        .as_integer() >= 0,
                        stored["transforms"]["artifacts"]["preserved"]
                        .as_integer() >= 0,
                    )
                    .limit(1)
                )
            ).scalars().first()
    except Exception as exc:
        if _is_absent_table_error(exc):
            return None
        raise
    if row is None:
        return None
    result = dict(row.result_json)
    if not _is_production_completion(result):
        return None
    if (await _has_pending_artifact_recovery(session)
            or await _has_pending_instance_conversion(session)):
        return None
    return _replay_stored_result(
        row.preflight_digest,
        ConversionDecision(
            disposition="unresolved_refusal", eligible=False,
            reason_code="missing_completion",
        ),
        result,
    )


TransformFn = Callable[..., Any]


def _uses_production_transforms(transforms: dict[str, TransformFn]) -> bool:
    return transforms.keys() == DEFAULT_SUBSYSTEM_TRANSFORMS.keys() and all(
        transforms[name] is fn
        for name, fn in DEFAULT_SUBSYSTEM_TRANSFORMS.items()
    )


async def _run_transform(
    fn: TransformFn,
    session: AsyncSession,
    eligible: list[str],
    options: ConversionOptions,
):
    # Only a transform that owns collision dispositions receives them.
    if "collision_resolutions" in inspect.signature(fn).parameters:
        result = fn(
            session,
            eligible,
            collision_resolutions=dict(options.collision_resolutions),
        )
    else:
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


def _preserved_instance_subsystems() -> set[str]:
    """Identify retained resources already consumed without an owner predicate.

    Schedule, execution, and read-only workflow history consumers already treat
    historical human fields as provenance. Rewriting those rows would change
    accepted work needlessly.
    Presets and settings have their own concrete transforms.
    """
    return {"schedules", "temporal", "workflows"}


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
    Custom reruns with the same digest replay the recorded result. The
    exact production transforms repair uncertified or pending recovery in
    place, atomically with their effects, rather than replaying an old cutover.
    """
    if not operator_authorized:
        raise ConversionAuthorizationError(
            "single-user conversion apply requires explicit operator authorization"
        )
    transforms = dict(transforms or {})
    production_transforms = _uses_production_transforms(transforms)
    options = preflight_result.options

    claimed = None
    try:
        claimed = await _try_conversion_claim(session)
        if claimed is False:
            raise ConcurrentConversionError(
                "another single-user conversion holds this database"
            )
        await _lock_conversion_evidence(session)
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

        decision_options = normalize_options(
            options.alias_groups,
            options.collision_resolutions,
            options.deployment_owned_tables,
            operator_authorized=True,
        )
        decision = _decision_from_inventory(fresh, decision_options)
        if not decision.eligible:
            return ApplyResult(
                digest=preflight_result.digest, published=False, decision=decision
            )

        preserved = (
            _preserved_instance_subsystems() if production_transforms else set()
        )
        missing = [
            s
            for s in fresh.present_subsystems
            if s not in transforms and s not in preserved
        ]
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
                    if not production_transforms or (
                        _is_production_completion(stored)
                        and not await _has_pending_artifact_recovery(session)
                        and not await _has_pending_instance_conversion(session)
                    ):
                        return _replay_stored_result(
                            preflight_result.digest, decision, stored
                        )
                    # Custom/no-op completion or a cleared recovery grant
                    # requires real defaults under current locked eligibility.
                    # Replace the old result only together with those effects.
                elif claimed is True:
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
                    existing = None
                else:
                    raise ConcurrentConversionError(
                        "a conversion ledger row is in progress and liveness "
                        "cannot be proven without the conversion claim"
                    )

            eligible_ids = sorted(set(fresh.user_ids) | set(fresh.secret_holders))
            outcomes: dict[str, Any] = {}
            # Archive only after every subsystem has preserved its data.
            ordered = sorted(name for name in transforms if name != "accounts")
            if "accounts" in transforms:
                ordered.append("accounts")
            for name in ordered:
                outcome = await _run_transform(
                    transforms[name], session, eligible_ids, options
                )
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
            stored_result = result.to_sanitized_dict()
            if production_transforms:
                # Persist proof of actual production execution, not merely a
                # complete row or caller-supplied transform outcome shape.
                stored_result["production_transforms_completed"] = True
            try:
                if existing is None:
                    session.add(
                        ledger_cls(
                            preflight_digest=preflight_result.digest,
                            status="complete",
                            result_json=stored_result,
                        )
                    )
                else:
                    # Repair and grants commit together; any failure retains
                    # the prior completion and rolls back the new authority.
                    existing.result_json = stored_result
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
                    if not production_transforms or (
                        _is_production_completion(stored)
                        and not await _has_pending_artifact_recovery(session)
                        and not await _has_pending_instance_conversion(session)
                    ):
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
        # Refusals and replays must release the authority locks too.
        await _release_conversion_claim(session)


async def startup_guard_decision(
    session: AsyncSession,
    alias_groups=None,
    collision_resolutions=None,
    deployment_owned_tables=None,
) -> ConversionDecision:
    """Read-only guard for startup/upgrade routes: never writes, never seeds."""
    if not (alias_groups or collision_resolutions or deployment_owned_tables):
        completed = await completed_guarded_upgrade(session)
        if completed is not None:
            return completed.decision
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
    probe = await session.begin_nested()
    try:
        profiles = (
            await session.execute(_select(ManagedAgentProviderProfile))
        ).scalars().all()
        await probe.commit()
    except Exception as exc:
        # An absent provider-profile table holds no refs to rewire (partial
        # schemas such as the hermetic PostgreSQL fixture); any other
        # lookup failure still raises so a published conversion never
        # reports success for unwired credentials. Only the savepoint
        # rolls back so already-migrated secrets in the outer transaction
        # are preserved.
        try:
            await probe.rollback()
        except Exception:
            # The rewire read already failed; a failed savepoint rollback
            # must not mask the classification below.
            pass
        if _is_absent_table_error(exc):
            return []
        raise
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


async def artifacts_transform(
    session: AsyncSession, eligible_ids: list[str]
) -> dict[str, Any]:
    """Grant proven historical human recovery without changing provenance.

    Only the guarded entrypoint owns this write. Caller metadata, schema
    migration, machine owners and ordinary artifact creation cannot grant it.
    Original creators continue to own quota and in-flight claim scopes.
    """
    from api_service.db.models import TemporalArtifact

    try:
        async with session.begin_nested():
            rows = (await session.execute(select(TemporalArtifact))).scalars().all()
    except Exception as exc:
        if _is_absent_table_error(exc):
            return {"recovery_grants": 0, "preserved": 0}
        raise
    preserve_deployment_scope = False
    if _uses_reserved_disabled_scope() and any(
        row.created_by_principal
        in {DEPLOYMENT_SUBJECT_ID, f"user:{DEPLOYMENT_SUBJECT_ID}"}
        for row in rows
    ):
        from api_service.migrations.account_source import source_user_ids

        # The owning apply holds the User-table lock. eligible_ids also carries
        # secret holders, so it cannot establish whether a real zero User exists.
        zero_user = (
            UUID(DEPLOYMENT_SUBJECT_ID)
            if UUID(DEPLOYMENT_SUBJECT_ID) in await source_user_ids(session)
            else None
        )
        preserve_deployment_scope = zero_user is None
    eligible = set(eligible_ids)
    granted = 0
    for row in rows:
        if preserve_deployment_scope and row.created_by_principal in {
            DEPLOYMENT_SUBJECT_ID,
            f"user:{DEPLOYMENT_SUBJECT_ID}",
        }:
            # Preserve creator, quota, claims and any existing grant verbatim.
            continue
        owner = _artifact_human_principal(row.created_by_principal)
        if owner:
            if owner not in eligible:
                raise StaleAttributionError("artifact creator is not proven eligible")
            if not row.operator_recovery_granted:
                row.operator_recovery_granted = True
                granted += 1
        elif not _is_artifact_machine_principal(row.created_by_principal):
            raise StaleAttributionError("artifact creator has no supported recovery disposition")
    await session.flush()
    return {"recovery_grants": granted, "preserved": len(rows)}


async def _preset_rows(session: AsyncSession, model):
    """Read an existing conversion surface, tolerating only absent tables."""
    try:
        async with session.begin_nested():
            return list((await session.execute(select(model))).scalars())
    except SQLAlchemyError as exc:
        if _is_absent_table_error(exc):
            return []
        raise


async def _has_pending_preset_conversion(session: AsyncSession) -> bool:
    from api_service.db.models import Preset, PresetFavorite, PresetRecent

    for model, column in (
        (Preset, Preset.scope_type),
        (PresetFavorite, PresetFavorite.user_id),
        (PresetRecent, PresetRecent.user_id),
    ):
        try:
            async with session.begin_nested():
                if (
                    await session.execute(
                        select(model.id).where(column.is_not(None)).limit(1)
                    )
                ).first() is not None:
                    return True
        except SQLAlchemyError as exc:
            if not _is_absent_table_error(exc):
                raise
    return False


async def presets_transform(
    session: AsyncSession, eligible_ids: list[str]
) -> dict[str, Any]:
    """Reconcile the existing catalog under the shared attribution/writer lock.

    Only actual slug collisions change names; stable preset identities preserve
    preferences and other ID references. Mutable future composition references
    are rebound by their original identity, never by newest-row wins. Recorded
    execution snapshots and historical receipts are deliberately not rewritten.
    """
    from copy import deepcopy
    from datetime import UTC

    from api_service.db.models import (
        Preset,
        PresetFavorite,
        PresetRecent,
        PresetScopeType,
        RecurringWorkflowDefinition,
    )

    rows = await _preset_rows(session, Preset)
    eligible = set(eligible_ids)
    # The old disabled local operator has no stored User. A real zero User
    # remains a person and must have passed the same attribution boundary.
    from api_service.migrations.account_source import source_user_ids

    zero_user = (
        UUID(DEPLOYMENT_SUBJECT_ID)
        if UUID(DEPLOYMENT_SUBJECT_ID) in await source_user_ids(session)
        else None
    )
    if zero_user is None:
        eligible.add(DEPLOYMENT_SUBJECT_ID)
    legacy = [row for row in rows if row.scope_type is not None]
    original = {}
    for row in rows:
        scope = row.scope_type.value if row.scope_type is not None else None
        key = (scope, row.scope_ref, row.slug)
        if key in original:
            raise StaleAttributionError("ambiguous legacy preset identity")
        original[key] = row
        if row.scope_type is not None:
            if row.created_by is not None and str(row.created_by) not in eligible:
                raise StaleAttributionError("preset creator is not proven eligible")
            if (
                row.scope_type is PresetScopeType.PERSONAL
                and _looks_like_uuid(row.scope_ref) not in eligible
            ):
                raise StaleAttributionError(
                    "personal preset attribution is not proven eligible"
                )

    # Retained activity inputs still carry the original scoped identity. Store
    # only its stable-ID mapping in the same production receipt as this rewrite;
    # scopeRef is historical evidence, never runtime ownership or admission.
    legacy_identities = [
        {
            "scope": row.scope_type.value,
            "scopeRef": row.scope_ref,
            "slug": row.slug,
            "presetId": str(row.id),
        }
        for row in sorted(legacy, key=lambda row: str(row.id))
    ]

    # Existing instance names win, then a managed seed's stable public name.
    # No content is merged, even when two definitions happen to be identical.
    by_slug: dict[str, list] = {}
    for row in rows:
        by_slug.setdefault(row.slug, []).append(row)
    occupied = set(by_slug)
    renamed: dict[UUID, str] = {}
    for slug, group in sorted(by_slug.items()):
        group.sort(
            key=lambda r: (
                r.scope_type is not None,
                not bool(r.seed_source and r.created_by is None),
                str(r.id),
            )
        )
        for row in group[1:]:
            suffix = row.id.hex
            # Full stable ID makes collisions bounded and deterministic. A
            # literal existing name can still conflict, so suffix only this
            # row again rather than renaming an unrelated catalog entry.
            candidate = f"{slug[:95]}-{suffix}"
            counter = 1
            while candidate in occupied:
                counter += 1
                tail = f"-{suffix}-{counter}"
                candidate = f"{slug[:128-len(tail)]}{tail}"
            occupied.add(candidate)
            renamed[row.id] = candidate

    def convert_ref(ref: dict, default_scope, default_ref):
        old_scope = ref.get("scope", default_scope)
        old_ref = ref.get("scopeRef", ref.get("scope_ref", default_ref))
        if old_scope in (None, "global"):
            old_ref = None
        key = (old_scope, old_ref, str(ref.get("slug", "")))
        target = original.get(key)
        # A known unavailable reference must remain an actionable failure;
        # never accidentally bind it to a different collision winner.
        if target is None:
            raise StaleAttributionError(
                "preset reference cannot be resolved without substitution"
            )
        ref["slug"] = renamed.get(target.id, target.slug)
        ref.pop("scope", None)
        ref.pop("scopeRef", None)
        ref.pop("scope_ref", None)

    def convert_steps(steps, scope, scope_ref):
        result = deepcopy(steps)
        for step in result:
            if not isinstance(step, dict):
                continue
            if step.get("kind") == "include":
                convert_ref(step, scope, scope_ref)
            elif step.get("type") == "preset" and isinstance(step.get("preset"), dict):
                convert_ref(step["preset"], scope, scope_ref)
        return result

    # Convert references before changing their lookup identities.
    for row in rows:
        scope = row.scope_type.value if row.scope_type is not None else None
        row.steps = convert_steps(row.steps or [], scope, row.scope_ref)
    for schedule in await _preset_rows(session, RecurringWorkflowDefinition):
        target = deepcopy(schedule.target or {})
        parameters = target.get("initialParameters", target.get("initial_parameters"))
        if not isinstance(parameters, dict):
            continue
        task = parameters.get("workflow", parameters.get("task"))
        if not isinstance(task, dict):
            continue
        owner_ref = str(schedule.owner_user_id) if schedule.owner_user_id else None

        def convert_saved(node, inherited_scope=None, inherited_ref=owner_ref):
            if not isinstance(node, dict):
                return
            scope = node.get("scope", inherited_scope)
            slug = str(node.get("slug", ""))
            if (
                scope is None
                and (None, None, slug) in original
                and ("global", None, slug) in original
            ):
                # Old omitted scopes meant global; new omitted scopes mean
                # instance. A mixed catalog cannot establish which was saved.
                raise StaleAttributionError("ambiguous omitted preset scope")
            if (
                scope is None
                and (None, None, str(node.get("slug", ""))) not in original
            ):
                scope = "global"  # legacy omitted-scope authoring default
            old_ref = node.get("scopeRef", node.get("scope_ref", inherited_ref))
            if node.get("slug"):
                convert_ref(node, scope, old_ref)
            for child in node.get("includes") or []:
                convert_saved(child, scope, old_ref)
            if isinstance(node.get("composition"), dict):
                convert_saved(node["composition"], scope, old_ref)

        for key in ("taskTemplate", "task_template"):
            convert_saved(task.get(key))
        for key in ("appliedStepTemplates", "applied_step_templates"):
            for applied in task.get(key) or []:
                convert_saved(applied)
        # Only provenance references changed. Saved inputs, capabilities and
        # expanded steps remain exactly as admitted; no re-expansion occurs.
        schedule.target = target
    for row in legacy:
        row.slug = renamed.get(row.id, row.slug)
        row.scope_type = None
        row.scope_ref = None
    await session.flush()

    merged = 0
    for model, timestamp in (
        (PresetFavorite, "created_at"),
        (PresetRecent, "applied_at"),
    ):
        groups: dict[UUID, list] = {}
        for item in await _preset_rows(session, model):
            if item.user_id is not None and str(item.user_id) not in eligible:
                raise StaleAttributionError(
                    "preset preference owner is not proven eligible"
                )
            if item.template_id not in {row.id for row in rows}:
                raise StaleAttributionError("preset preference target is missing")
            groups.setdefault(item.template_id, []).append(item)
        for group in groups.values():
            group.sort(key=lambda item: (item.user_id is not None, item.id))
            survivor = group[0]
            dates = [getattr(item, timestamp) for item in group]

            def aware(value):
                return value if value.tzinfo else value.replace(tzinfo=UTC)

            # A favorite keeps its earliest creation; recents retain last use.
            chosen = (max if model is PresetRecent else min)(dates, key=aware)
            for duplicate in group[1:]:
                await session.delete(duplicate)
                merged += 1
            await session.flush()
            survivor.user_id = None
            setattr(survivor, timestamp, chosen)
    await session.flush()
    return {
        "converted": len(legacy),
        "renamed_collisions": len(renamed),
        "deduplicated_preferences": merged,
        "legacy_identities": legacy_identities,
    }


async def _has_pending_settings_conversion(session: AsyncSession) -> bool:
    from api_service.db.models import SettingsOverride

    try:
        async with session.begin_nested():
            return (
                await session.execute(
                    select(SettingsOverride.id)
                    .where(SettingsOverride.scope.is_not(None))
                    .limit(1)
                )
            ).first() is not None
    except SQLAlchemyError as exc:
        if _is_absent_table_error(exc):
            return False
        raise


async def _has_pending_instance_conversion(session: AsyncSession) -> bool:
    return await _has_pending_preset_conversion(
        session
    ) or await _has_pending_settings_conversion(session)


#: Choice key for an instance reset made after revision 398. It is never a
#: canonical JSON value, so a disposition cannot name it.
_INSTANCE_RESET_CHOICE = "<instance reset>"


async def _instance_edited_settings_keys(session: AsyncSession) -> set[str]:
    """Keys the operator wrote or reset as instance configuration after 398.

    Revision 398 kept the scope label on deployment-subject history, so only
    instance writes made since then record override events with null scope.
    """
    from api_service.db.models import SettingsAuditEvent

    try:
        async with session.begin_nested():
            return set(
                (
                    await session.execute(
                        select(SettingsAuditEvent.key)
                        .where(
                            SettingsAuditEvent.scope.is_(None),
                            SettingsAuditEvent.event_type.in_(
                                ("settings.override.updated", "settings.override.reset")
                            ),
                        )
                        .distinct()
                    )
                ).scalars()
            )
    except SQLAlchemyError as exc:
        if _is_absent_table_error(exc):
            return set()
        raise


def _legacy_settings_choices(
    rows, instance_edited: set[str] = frozenset()
) -> dict[str, dict[str, Any]]:
    """The operator's previously effective choices for keys with retained rows.

    A personal row overrode its workspace in that person's sessions, so once
    one operator is proven it is that operator's effective choice. Shared
    (instance or workspace) rows decide only keys without a personal row.
    An instance write or reset made after revision 398 is a later explicit
    choice for that key, so it joins the deciding tier rather than losing to
    a retained row. Equal choices agree however many rows hold them; distinct
    choices within the deciding tier conflict, whichever row is newer.
    """
    pending = {row.key for row in rows if row.scope is not None}
    personal: dict[str, dict[str, Any]] = {}
    shared: dict[str, dict[str, Any]] = {}
    instance: dict[str, Any] = {}
    for row in rows:
        if row.key in pending:
            tier = personal if row.scope == "user" else shared
            tier.setdefault(row.key, {})[_canonical(row.value_json)] = row
            if row.scope is None:
                instance[row.key] = row
    choices: dict[str, dict[str, Any]] = {}
    for key in pending:
        choices[key] = dict(personal.get(key) or shared[key])
        if key in instance_edited:
            row = instance.get(key)
            if row is None:
                choices[key][_INSTANCE_RESET_CHOICE] = None
            else:
                choices[key].setdefault(_canonical(row.value_json), row)
    return choices


async def settings_transform(
    session: AsyncSession,
    eligible_ids: list[str],
    *,
    collision_resolutions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reconcile retained settings into the instance configuration.

    Runs under the shared attribution/writer lock after the disposition proved
    one operator. The effective choice becomes one instance override.
    Distinct choices need the operator's recorded disposition naming one of
    them. Superseded values stay recoverable in the conversion audit event. Stored
    values (SecretRefs and explicit nulls included) move unchanged, keys
    without rows stay inherited, and no newest-row or default is substituted.
    """
    from api_service.db.models import SettingsAuditEvent, SettingsOverride
    from api_service.services.settings_catalog import SettingsCatalogService

    rows = await _preset_rows(session, SettingsOverride)
    eligible = set(eligible_ids)
    from api_service.migrations.account_source import source_user_ids

    zero_user = (
        UUID(DEPLOYMENT_SUBJECT_ID)
        if UUID(DEPLOYMENT_SUBJECT_ID) in await source_user_ids(session)
        else None
    )
    if zero_user is None:
        eligible.add(DEPLOYMENT_SUBJECT_ID)
    for row in rows:
        if row.scope is not None and row.user_id is not None and (
            str(row.user_id) not in eligible
        ):
            raise StaleAttributionError("settings owner is not proven eligible")

    resolutions = collision_resolutions or {}
    catalog = SettingsCatalogService()
    converted = merged = resolved = 0
    instance_edited = await _instance_edited_settings_keys(session)
    for key, choices in sorted(_legacy_settings_choices(rows, instance_edited).items()):
        if len(choices) > 1:
            chosen = _canonical(resolutions.get(key)) if key in resolutions else None
            if chosen not in choices:
                raise StaleAttributionError(
                    "settings collision needs a disposition naming a retained choice"
                )
            resolved += 1
        else:
            chosen = next(iter(choices))
        source = choices[chosen]
        group = sorted(
            (row for row in rows if row.key == key),
            key=lambda row: (row.scope is not None, row is not source, str(row.id)),
        )
        superseded = {
            _canonical(row.value_json): row.value_json
            for row in group
            if _canonical(row.value_json) != chosen
        }
        survivor = group[0]
        if len(group) > 1:
            # A client holding any pre-conversion version must re-read.
            survivor.value_version = max(row.value_version for row in group) + 1
        survivor.value_json = source.value_json
        survivor.schema_version = source.schema_version
        for duplicate in group[1:]:
            await session.delete(duplicate)
            merged += 1
        await session.flush()
        survivor.scope = None
        survivor.workspace_id = None
        survivor.user_id = None
        discarded = [superseded[canon] for canon in sorted(superseded)] or None
        entry = catalog._entries_by_key.get(key)
        reason = "Reconciled retained settings into the instance configuration."
        session.add(
            catalog._audit_event(
                entry,
                event_type="settings.override.converted",
                old_value=discarded,
                new_value=source.value_json,
                reason=reason,
                request_id=None,
            )
            if entry is not None
            else SettingsAuditEvent(
                event_type="settings.override.converted",
                key=key,
                redacted=True,
                reason=reason,
            )
        )
        converted += 1
    await session.flush()
    return {
        "converted_keys": converted,
        "merged_rows": merged,
        "resolved_collisions": resolved,
    }


#: Production subsystem transforms registered into the shared entrypoint.
#: Only subsystems that mutate data need transforms. Owner-independent
#: schedules/executions are preserved by the guarded entrypoint. Retained
#: preset and settings rows use their real transforms; legacy workflow data
#: still requires its own conversion.
from api_service.migrations.account_source import archive_accounts


async def container_jobs_transform(
    session: AsyncSession, eligible_ids: list[str]
) -> dict:
    """Grant the admitted operator this job, preserving its durable owner.

    Eligibility and writer locks belong to the shared converter. This grant is
    resource authority, never an identity alias or permission for machine calls.
    """
    from api_service.db.models import ContainerJobRecord

    try:
        async with session.begin_nested():
            rows = (
                await session.scalars(
                    select(ContainerJobRecord).where(
                        ContainerJobRecord.owner_type == "user"
                    )
                )
            ).all()
    except SQLAlchemyError as exc:
        if _is_absent_table_error(exc):
            return {"operator_grants": 0}
        raise
    eligible = set(eligible_ids)
    granted = 0
    for row in rows:
        if (
            _looks_like_uuid(row.owner_id) in eligible
            and not row.operator_access_granted
        ):
            row.operator_access_granted = True
            granted += 1
    await session.flush()
    return {"operator_grants": granted}


DEFAULT_SUBSYSTEM_TRANSFORMS: dict[str, TransformFn] = {
    "accounts": archive_accounts,
    "container_jobs": container_jobs_transform,
    "profile_secrets": profile_secrets_transform,
    "artifacts": artifacts_transform,
    "presets": presets_transform,
    "settings": settings_transform,
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

    New candidates capture a consistent preflight, then apply it with real
    subsystem transforms through versioned migration 386 + ledger idempotency.
    Default restarts probe for outstanding artifact recovery before reusing
    certified completion without loading history. Explicit evidence or transforms take
    the authoritative preflight/apply path.
    Alembic revision 386 creates only the ledger table and never applies a
    destructive conversion as a schema-upgrade side effect; CLI, worker, and
    HTTP startup routes must call this entrypoint instead of invoking
    subsystem conversions directly. Refusals perform no conversion-side
    writes; stale sources raise :class:`StaleAttributionError`.
    """
    if not operator_authorized:
        raise ConversionAuthorizationError(
            "single-user conversion apply requires explicit operator authorization"
        )
    if transforms is None and not (
        alias_groups or collision_resolutions or deployment_owned_tables
    ):
        completed = await completed_guarded_upgrade(session)
        if completed is not None:
            await session.rollback()
            return completed
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
    if not bound.decision.eligible:
        # Apply would re-read the same fingerprint under writer locks only to
        # return this refusal; a refused source keeps writers unblocked.
        return ApplyResult(digest=bound.digest, published=False, decision=bound.decision)
    return await apply_conversion(
        session,
        bound,
        operator_authorized=operator_authorized,
        transforms=selected,
    )
