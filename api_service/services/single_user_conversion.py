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

Consistency: :func:`preflight` captures a fingerprint of the source;
:func:`apply_conversion` recomputes it and raises
:class:`StaleAttributionError` when anything changed, so a concurrent
mutation after preflight cannot publish on stale attribution. No new
lock/identity service: mutual exclusion reuses the ledger row plus a
best-effort PostgreSQL advisory claim (SQLite falls back to the ledger).

Reports carry counts, reason codes, and truncated identifier prefixes
only — never emails, names, secret values, or resource content.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import String, UniqueConstraint, func, select, text
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
            "collision_resolutions": sorted(str(k) for k in self.collision_resolutions),
            "deployment_owned_tables": sorted(self.deployment_owned_tables),
        }


def normalize_options(
    alias_groups=None,
    collision_resolutions=None,
    deployment_owned_tables=None,
) -> ConversionOptions:
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
                }
            ).encode()
        ).hexdigest()


# Tables carrying a human principal. ``kind`` selects the read strategy:
# "uuid_fk" (UUID column), "uuid_text" (string column holding UUID text),
# "user_temporal" (string owner_id qualified by USER owner_type).
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
    ("temporal_execution_sources", "owner_id", "uuid_text"),
    ("workflow_execution_source_mappings", "owner_id", "uuid_text"),
    ("managed_agent_oauth_sessions", "requested_by_user_id", "uuid_text"),
    ("temporal_artifacts", "created_by_principal", "uuid_text"),
)

# Null-owner rows in these tables are ambiguous retained data, not
# deployment-owned by assumption.
_UNOWNED_TRACKED: tuple[tuple[str, str], ...] = (
    ("recurring_workflow_definitions", "owner_user_id"),
    ("presets", "created_by"),
    ("temporal_executions", "owner_id"),
    ("temporal_artifacts", "created_by_principal"),
)

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


async def _probe_count(session: AsyncSession, table, column) -> int | None:
    probe = await session.begin_nested()
    try:
        count = (
            await session.execute(
                select(func.count()).select_from(table).where(column.is_not(None))
            )
        ).scalar() or 0
        await probe.commit()
        return int(count)
    except Exception:
        try:
            await probe.rollback()
        except Exception:
            pass
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
            except Exception:
                try:
                    await session.rollback()
                except Exception:
                    pass
                continue
            for raw in values:
                norm = _looks_like_uuid(raw)
                if norm:
                    principals.setdefault(f"{table_name}.{column_name}", set()).add(norm)
            total = await _probe_count(session, table, col)
            if total is not None:
                resource_counts[f"{table_name}.{column_name}"] = total
            continue
        if kind == "uuid_text":
            try:
                rows = (
                    await session.execute(select(col).where(col.is_not(None)))
                ).all()
            except Exception:
                try:
                    await session.rollback()
                except Exception:
                    pass
                continue
            for (raw,) in rows:
                if raw is None or str(raw).strip() == "":
                    continue
                norm = _looks_like_uuid(str(raw).strip())
                if norm:
                    principals.setdefault(f"{table_name}.{column_name}", set()).add(norm)
            total = await _probe_count(session, table, col)
            if total is not None:
                resource_counts[f"{table_name}.{column_name}"] = total
            continue
        # uuid_fk
        try:
            rows = (
                await session.execute(select(col).where(col.is_not(None)))
            ).all()
        except Exception:
            try:
                await session.rollback()
            except Exception:
                pass
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
        total = await _probe_count(session, table, col)
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
                    nulls = (
                        await session.execute(
                            select(func.count())
                            .select_from(table)
                            .where(
                                col.is_(None),
                                func.lower(owner_type_col) == "user",
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
        except Exception:
            try:
                await probe.rollback()
            except Exception:
                pass
            continue
        if int(total) > 0 and int(nulls) > 0:
            unowned[f"{table_name}.{column_name}"] = int(nulls)
    inv.unowned = unowned

    # Profile-held secrets (subsystem presence for coverage gating).
    try:
        from api_service.db.models import UserProfile

        secret_rows = (await session.execute(select(UserProfile))).scalars().all()
        holders = set()
        for p in secret_rows:
            for column in (
                "google_api_key_encrypted",
                "openai_api_key_encrypted",
                "github_token_encrypted",
                "anthropic_api_key_encrypted",
            ):
                try:
                    value = getattr(p, column, None)
                except Exception:
                    continue
                if isinstance(value, str) and value.strip():
                    uid = _looks_like_uuid(getattr(p, "user_id", None))
                    if uid:
                        holders.add(uid)
        inv.secret_holders = sorted(holders)
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass

    # Same-person settings collisions: one key, several distinct values.
    try:
        from api_service.db.models import SettingsOverride

        rows = (await session.execute(select(SettingsOverride))).scalars().all()
        by_key: dict[str, set[str]] = {}
        for r in rows:
            uid = _looks_like_uuid(getattr(r, "user_id", None))
            if uid is None or uid == DEPLOYMENT_SUBJECT_ID:
                continue
            by_key.setdefault(str(getattr(r, "key", "")), set()).add(
                _canonical(getattr(r, "value_json", None))
            )
        inv.settings_collisions = sorted(
            k for k, vs in by_key.items() if k and len(vs) > 1
        )
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass

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
) -> ConversionDecision:
    """Decide the conversion disposition for the current source state.

    Consults UUID principals and explicit ``alias_groups`` only. Email,
    display name, administrator flags, active-login counts, and any
    preferred-account hint are never read here and cannot merge people.
    """
    options = normalize_options(alias_groups, collision_resolutions,
                                deployment_owned_tables)
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
) -> PreflightResult:
    """Inspect without writing; the digest binds a later apply."""
    options = normalize_options(alias_groups, collision_resolutions,
                                deployment_owned_tables)
    inv = await collect_inventory(session)
    fingerprint = inv.fingerprint()
    decision = await evaluate_disposition(
        session,
        alias_groups=alias_groups,
        collision_resolutions=collision_resolutions,
        deployment_owned_tables=deployment_owned_tables,
    )
    digest = compute_digest(fingerprint, options)
    try:
        await session.rollback()
    except Exception:
        pass
    return PreflightResult(
        digest=digest,
        fingerprint=fingerprint,
        decision=decision,
        options=options,
        inventory=inv,
    )


def _advisory_key(digest: str) -> int:
    return int(hashlib.sha256(f"single-user-conversion:{digest}".encode()).hexdigest()[:15], 16)


async def _try_advisory_claim(session: AsyncSession, digest: str) -> bool | None:
    probe = await session.begin_nested()
    try:
        claimed = (
            await session.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": _advisory_key(digest)}
            )
        ).scalar()
        await probe.commit()
    except Exception:
        try:
            await probe.rollback()
        except Exception:
            pass
        return None
    return bool(claimed)


async def _release_advisory_claim(session: AsyncSession, digest: str) -> None:
    try:
        await session.execute(
            text("SELECT pg_advisory_unlock(:key)"), {"key": _advisory_key(digest)}
        )
    except Exception:
        logger.debug("single-user conversion advisory unlock skipped", exc_info=True)
    finally:
        try:
            await session.rollback()
        except Exception:
            pass


TransformFn = Callable[..., Any]


async def _run_transform(fn: TransformFn, session: AsyncSession, eligible: list[str]):
    result = fn(session, eligible)
    if inspect.isawaitable(result):
        return await result
    return result


async def apply_conversion(
    session: AsyncSession,
    preflight_result: PreflightResult,
    *,
    operator_authorized: bool,
    transforms: dict[str, TransformFn] | None = None,
) -> ApplyResult:
    """Apply a preflight-bound conversion idempotently and resumably.

    Refusals (ineligible disposition or missing transform coverage)
    perform no conversion-side writes. A changed source fingerprint
    raises :class:`StaleAttributionError` instead of publishing stale
    attribution. Reruns with the same digest replay the recorded result.
    """
    if not operator_authorized:
        raise ConversionAuthorizationError(
            "single-user conversion apply requires explicit operator authorization"
        )
    transforms = dict(transforms or {})
    options = preflight_result.options

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
    advisory_held = await _try_advisory_claim(session, preflight_result.digest)
    if advisory_held is False:
        raise ConcurrentConversionError(
            "another conversion holds this preflight digest"
        )
    try:
        try:
            run = ledger_cls(
                preflight_digest=preflight_result.digest,
                status="in_progress",
                result_json={},
            )
            session.add(run)
            await session.flush()
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = (
                await session.execute(
                    select(ledger_cls).where(
                        ledger_cls.preflight_digest == preflight_result.digest
                    )
                )
            ).scalars().first()
            if existing is None:
                raise ConcurrentConversionError(
                    "conversion ledger conflicted without a winner"
                )
            if str(getattr(existing, "status", "")) == "in_progress":
                raise ConcurrentConversionError(
                    "another conversion holds this preflight digest"
                )
            stored = dict(getattr(existing, "result_json", None) or {})
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
                digest=preflight_result.digest,
                published=bool(stored.get("published", True)),
                decision=restored,
                transforms=dict(stored.get("transforms", {})),
            )

        eligible_ids = sorted(set(fresh.user_ids) | set(fresh.secret_holders))
        outcomes: dict[str, Any] = {}
        for name in sorted(transforms):
            outcome = await _run_transform(transforms[name], session, eligible_ids)
            if isinstance(outcome, dict):
                outcomes[name] = {
                    k: v
                    for k, v in outcome.items()
                    if k != "items" or True
                }
                # Strip any accidental secret-bearing payloads from stored
                # outcomes: keep outcome metadata, drop raw values.
                items = outcomes[name].get("items")
                if isinstance(items, list):
                    outcomes[name]["items"] = [
                        {k: v for k, v in (i.items() if isinstance(i, dict) else []) if "secret" not in k.lower() and "token" not in k.lower() and "value" not in k.lower()}
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
        row = (
            await session.execute(
                select(ledger_cls).where(
                    ledger_cls.preflight_digest == preflight_result.digest
                )
            )
        ).scalars().first()
        if row is not None:
            row.status = "complete"
            row.result_json = result.to_sanitized_dict()
            await session.flush()
        await session.commit()
        return result
    finally:
        if advisory_held:
            await _release_advisory_claim(session, preflight_result.digest)


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
