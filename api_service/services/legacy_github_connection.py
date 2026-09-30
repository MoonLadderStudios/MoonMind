"""One recoverable legacy GitHub credential migration (MoonLadderStudios/MoonMind#4023).

Maps the proven effective legacy GitHub credential reference onto
``repository-connection:git-default`` through the existing #4005 connection
writer, so new authenticated work uses that recorded identity instead of
searching ambient credentials at every call.

* The recorded connection row is the migration outcome; there is no separate
  ledger or lease. Once it exists (active, disabled, or deleted) startup
  never rereads legacy sources and never remaps, overwrites, or recreates it.
* Only references are recorded (``env://GITHUB_TOKEN``, ``db://slug``, ...);
  token values are never read, compared, or probed.
* The write carries a stable request identity derived from the reference, so
  a lost commit acknowledgment or a concurrent creator replays or reconciles
  the committed row instead of adding another.
* Persisted ``integrations.github.token_ref`` overrides are fenced by their
  ``value_version``: an operator edit that lands between classification and
  the write aborts this run, and the next run maps the edited value.
* Absent, unreadable, or conflicting evidence records nothing and returns a
  bounded correction. Only authenticated ``git-default`` work is affected;
  scratch and explicitly anonymous work never consult this connection.

Removal condition: delete this module (and its startup call) once the
connection form owned by MoonLadderStudios/MoonMind#4019 creates the default
connection directly and supported deployments no longer bootstrap it from
``.env``/Settings GitHub token references.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import RepositoryConnectionRecord, SettingsOverride
from api_service.services.repository_connections import (
    RepositoryConnectionService,
    _record_to_connection,
)
from moonmind.auth.github_credentials import (
    LegacyGitHubIdentity,
    LegacyGitHubIdentityOutcome,
    classify_legacy_github_credential,
)
from moonmind.workflows.executions.repository_contract import (
    DEFAULT_GIT_CONNECTION_REF,
    RepositoryClientPolicy,
    RepositoryConnection,
    RepositoryRouteError,
)

OPERATOR_OWNER_REF = "owner:operator"
MIGRATION_ACTOR_REF = "system:legacy-github-migration-4023"
MIGRATION_REQUEST_PREFIX = "legacy-github-connection-4023"
AFFECTED_ACTION = "authenticated operations using repository-connection:git-default"
OPERATOR_GITHUB_TOKEN_SETTING_KEY = "integrations.github.token_ref"
#: The legacy default connection's operations, preserved rather than widened.
LEGACY_DEFAULT_GIT_OPERATIONS = ("read", "write", "branch_write", "review_request")


class LegacyGitHubMigrationOutcome(StrEnum):
    MIGRATED = "migrated"
    ALREADY_PRESENT = "already_present"
    ABSENT = "absent"
    UNREADABLE = "unreadable"
    CONFLICTING = "conflicting"
    CONFIGURATION_CHANGED = "configuration_changed"


class LegacyGitHubMigrationResult(BaseModel):
    """Reference-only migration outcome; never carries token material."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    outcome: LegacyGitHubMigrationOutcome
    credential_ref: str | None = Field(None, alias="credentialRef")
    source_name: str | None = Field(None, alias="sourceName")
    correction: str | None = None

    def safe_diagnostic(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "connectionRef": DEFAULT_GIT_CONNECTION_REF,
            "credentialRef": self.credential_ref,
            "sourceName": self.source_name,
            "correction": self.correction,
            "affectedAction": AFFECTED_ACTION,
        }


def _credential_ref_of(record: RepositoryConnectionRecord) -> str | None:
    config = dict(record.credential_config or {})
    ref = config.get("credentialRef")
    if config.get("source") != "secret_ref" or not isinstance(ref, Mapping):
        return None
    return f"{ref.get('provider')}://{ref.get('key')}"


async def _recorded_default_connection(
    session: AsyncSession,
) -> RepositoryConnectionRecord | None:
    return (
        await session.execute(
            select(RepositoryConnectionRecord).where(
                RepositoryConnectionRecord.connection_id == DEFAULT_GIT_CONNECTION_REF
            )
        )
    ).scalar_one_or_none()


async def _operator_setting_fence(
    session: AsyncSession, *, lock: bool
) -> tuple[tuple[str, int, str], ...]:
    """Read persisted Settings GitHub token refs with their value versions.

    ``lock`` holds the rows until the migration commits where the dialect
    supports it (PostgreSQL); SQLite serializes writers at the database.
    """

    stmt = select(SettingsOverride).where(
        SettingsOverride.key == OPERATOR_GITHUB_TOKEN_SETTING_KEY
    )
    bind = session.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "") or ""
    if lock and dialect_name not in {"", "sqlite"}:
        stmt = stmt.with_for_update()
    rows = (await session.execute(stmt)).scalars().all()
    return tuple(
        sorted(
            (str(row.id), int(row.value_version), str(row.value_json or "").strip())
            for row in rows
        )
    )


def _default_connection_for(
    identity: LegacyGitHubIdentity, *, client_policy: RepositoryClientPolicy
) -> RepositoryConnection:
    provider, key = identity.secret_ref_parts()
    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": DEFAULT_GIT_CONNECTION_REF,
            "provider": "git",
            "displayName": "Default GitHub connection",
            "endpointRef": "https://github.com",
            "allowedOperations": list(LEGACY_DEFAULT_GIT_OPERATIONS),
            "clientPolicy": client_policy.model_dump(by_alias=True, mode="json"),
            "credential": {
                "source": "secret_ref",
                "credentialRef": {"provider": provider, "key": key},
            },
            "lifecycle": "active",
            "policyRevision": 1,
            "credentialRevision": 1,
            "ownership": {
                "ownerRef": OPERATOR_OWNER_REF,
                "scopeType": "system",
                "allowedPrincipalRefs": [],
            },
            "hostingService": "github",
        }
    )


def _request_id_for(credential_ref: str) -> str:
    digest = hashlib.sha256(credential_ref.encode("utf-8")).hexdigest()[:32]
    return f"{MIGRATION_REQUEST_PREFIX}:{digest}"


def _reconciled(
    record: RepositoryConnectionRecord, *, proposed_ref: str | None
) -> LegacyGitHubMigrationResult:
    recorded_ref = _credential_ref_of(record)
    if proposed_ref is None or recorded_ref == proposed_ref:
        return LegacyGitHubMigrationResult(
            outcome=LegacyGitHubMigrationOutcome.ALREADY_PRESENT,
            credentialRef=recorded_ref,
        )
    return LegacyGitHubMigrationResult(
        outcome=LegacyGitHubMigrationOutcome.CONFLICTING,
        credentialRef=recorded_ref,
        correction=(
            f"{DEFAULT_GIT_CONNECTION_REF} was recorded concurrently with "
            f"{recorded_ref}; the legacy configuration names {proposed_ref}. "
            "The recorded connection is kept; remove the legacy reference that "
            "should not be used."
        ),
    )


async def migrate_legacy_github_connection(
    session: AsyncSession,
    *,
    environ: Mapping[str, str],
    settings_ref: str | None,
    client_policy_factory: Callable[[], RepositoryClientPolicy],
) -> LegacyGitHubMigrationResult:
    """Map the proven legacy GitHub identity once; idempotent and fenced.

    ``client_policy_factory`` is only called when a mapping is written, so a
    post-migration startup inspects neither legacy sources nor the host.
    """

    existing = await _recorded_default_connection(session)
    if existing is not None:
        return _reconciled(existing, proposed_ref=None)

    fence = await _operator_setting_fence(session, lock=False)
    identity = classify_legacy_github_credential(
        environ,
        settings_ref=settings_ref,
        operator_setting_refs=tuple(value for _id, _version, value in fence),
    )
    if identity.outcome is not LegacyGitHubIdentityOutcome.PROVEN:
        return LegacyGitHubMigrationResult(
            outcome=LegacyGitHubMigrationOutcome(identity.outcome.value),
            sourceName=identity.source_name,
            correction=identity.correction,
        )
    proposed_ref = identity.credential_ref
    connection = _default_connection_for(
        identity, client_policy=client_policy_factory()
    )

    if await _operator_setting_fence(session, lock=True) != fence:
        await session.rollback()
        return LegacyGitHubMigrationResult(
            outcome=LegacyGitHubMigrationOutcome.CONFIGURATION_CHANGED,
            sourceName=identity.source_name,
            correction=(
                "Settings integrations.github.token_ref changed while the "
                "default GitHub connection was being migrated; the next "
                "startup maps the saved value."
            ),
        )
    try:
        await RepositoryConnectionService(session).create_connection(
            connection,
            actor_ref=MIGRATION_ACTOR_REF,
            request_id=_request_id_for(proposed_ref or ""),
            principal_ref=OPERATOR_OWNER_REF,
            principal_scope=("system", None),
        )
    except RepositoryRouteError:
        # A concurrent creator committed first: reconcile against its row
        # instead of repeating or overwriting the effect.
        await session.rollback()
        committed = await _recorded_default_connection(session)
        if committed is None:
            raise
        return _reconciled(committed, proposed_ref=proposed_ref)
    return LegacyGitHubMigrationResult(
        outcome=LegacyGitHubMigrationOutcome.MIGRATED,
        credentialRef=proposed_ref,
        sourceName=identity.source_name,
    )


async def load_default_git_connection(
    session: AsyncSession,
) -> RepositoryConnection | None:
    """Return the recorded default connection, or ``None`` when absent/deleted.

    Consumers still authorize current use (lifecycle, operations) at their
    own boundary; history never grants reacquisition of a revoked credential.
    """

    record = await _recorded_default_connection(session)
    if record is None or record.tombstone:
        return None
    return _record_to_connection(record)


__all__ = [
    "AFFECTED_ACTION",
    "LEGACY_DEFAULT_GIT_OPERATIONS",
    "LegacyGitHubMigrationOutcome",
    "LegacyGitHubMigrationResult",
    "OPERATOR_OWNER_REF",
    "load_default_git_connection",
    "migrate_legacy_github_connection",
]
