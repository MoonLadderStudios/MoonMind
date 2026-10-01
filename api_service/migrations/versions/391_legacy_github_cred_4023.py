"""Map the legacy GitHub credential source to the default repository connection.

MoonLadderStudios/MoonMind#4023: before this revision every caller searched
environment tokens, SecretRef environment variables, the Settings reference,
and the ``GITHUB_TOKEN``/``GITHUB_PAT`` managed-secret slugs, and worker
startup reconciled ``repository-connection:git-default`` as an always-on
alias for that search. This revision records the one reference the legacy
precedence actually selected as a typed SecretRef on
``repository-connection:git-default``. Launches then read only that
connection's credential.

The decision uses configured references and managed-secret presence only. It
reads no token value, calls no external service, and never compares values.
A configured reference that cannot be used stops the decision (as it stopped
the legacy chain) and is reported with a correction instead of selecting the
next source. Nothing configured records nothing.

The write is idempotent and protected by the connection owner's constraints:
the row and its ``connection.create`` audit identity are inserted with
conflict-ignoring inserts, then re-read. A connection the operator already
recorded (including a deleted tombstone) is never overwritten, and a rerun
after a lost commit acknowledgment completes the audit record without a
duplicate mapping. Downgrade removes the row only while it is still exactly
the migrated mapping; otherwise newer work is kept for forward repair.

This module is the frozen historical reader of the legacy source names and
can be deleted with the rest of the squashed history once no supported
upgrade path starts below this revision.

Revision ID: 391_legacy_github_cred_4023
Revises: 390_preset_catalog_account_free
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple, Union

import sqlalchemy as sa
from alembic import op

revision: str = "391_legacy_github_cred_4023"
down_revision: Union[str, None] = "390_preset_catalog_account_free"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

DEFAULT_CONNECTION_REF = "repository-connection:git-default"
MIGRATION_REQUEST_ID = "migration:391:legacy-github-credential"
MIGRATION_ACTOR_REF = "system:migration-391"
_CREATE_ACTION = "connection.create"
_CORRECTION = (
    "Set it to a secret reference such as env://GITHUB_TOKEN or "
    "db://<managed secret slug>; while repository-connection:git-default is "
    "not recorded, launches read the corrected deployment value."
)

# Frozen legacy precedence (moonmind.auth.github_credentials and the launch
# fallback in managed_api_key_resolve at revision 390).
_DIRECT_TOKEN_ENVS = ("GITHUB_TOKEN", "GH_TOKEN", "WORKFLOW_GITHUB_TOKEN")
_SECRET_REF_ENVS = (
    "GITHUB_TOKEN_SECRET_REF",
    "WORKFLOW_GITHUB_TOKEN_SECRET_REF",
    "MOONMIND_GITHUB_TOKEN_REF",
)
_MANAGED_SECRET_SLUGS = ("GITHUB_TOKEN", "GITHUB_PAT")
_SUPPORTED_BACKENDS = frozenset({"env", "db", "exec", "vault"})
_TOKEN_SHAPE = re.compile(r"^(?:gh[opsur]_|github_pat_)", re.IGNORECASE)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_records = sa.table(
    "repository_connection_records",
    sa.column("connection_id", sa.String),
    sa.column("display_name", sa.String),
    sa.column("provider", sa.String),
    sa.column("hosting_service", sa.String),
    sa.column("endpoint_normalized", sa.String),
    sa.column("endpoint_ref", sa.String),
    sa.column("allowed_operations", sa.JSON),
    sa.column("client_policy", sa.JSON),
    sa.column("credential_config", sa.JSON),
    sa.column("lifecycle", sa.String),
    sa.column("policy_revision", sa.Integer),
    sa.column("credential_revision", sa.Integer),
    sa.column("owner_ref", sa.String),
    sa.column("scope_type", sa.String),
    sa.column("scope_ref", sa.String),
    sa.column("allowed_principal_refs", sa.JSON),
    sa.column("tombstone", sa.Boolean),
)
_audit = sa.table(
    "repository_connection_audit_events",
    sa.column("id", sa.Uuid),
    sa.column("request_id", sa.String),
    sa.column("actor_ref", sa.String),
    sa.column("action", sa.String),
    sa.column("connection_id", sa.String),
    sa.column("scope_type", sa.String),
    sa.column("scope_ref", sa.String),
    sa.column("policy_revision", sa.Integer),
    sa.column("detail_json", sa.JSON),
)
_assignments = sa.table(
    "repository_connection_assignments", sa.column("connection_id", sa.String)
)
_route_defaults = sa.table(
    "repository_route_defaults", sa.column("connection_id", sa.String)
)
_managed_secrets = sa.table(
    "managed_secrets", sa.column("slug", sa.String), sa.column("status", sa.String)
)


class LegacyGitHubMapping(NamedTuple):
    """Outcome of one run; every field is a safe reference, never a value."""

    outcome: str
    source: str | None = None
    credential_ref: str | None = None
    diagnostic: str | None = None


def _reference_for(source: str, configured: str) -> tuple[str, str] | str:
    """Return ``(provider, key)`` for a configured reference, or a diagnostic."""

    candidate = configured.strip()
    provider, _, key = candidate.partition("://")
    if not key:
        # The legacy resolver read a bare value as an environment name.
        provider, key = "env", candidate
    provider = provider.strip().lower()
    key = key.strip()
    if _TOKEN_SHAPE.match(key):
        return (
            f"{source} looks like a token value, not a secret reference. " + _CORRECTION
        )
    if provider not in _SUPPORTED_BACKENDS:
        return f"{source} does not name a supported secret backend. " + _CORRECTION
    if provider == "env" and not _ENV_NAME.fullmatch(key):
        return f"{source} does not name a valid environment variable. " + _CORRECTION
    from moonmind.auth.secret_refs import parse_secret_ref

    try:
        parse_secret_ref(f"{provider}://{key}")
    except Exception:
        return f"{source} is not a valid secret reference. " + _CORRECTION
    return provider, key


def legacy_github_reference(
    environ: Mapping[str, str], active_slugs: Iterable[str]
) -> tuple[LegacyGitHubMapping, tuple[str, str] | None]:
    """Apply the frozen legacy precedence to configured references only."""

    for name in _DIRECT_TOKEN_ENVS:
        if str(environ.get(name) or "").strip():
            return (
                LegacyGitHubMapping(
                    "mapped", source=name, credential_ref=f"env://{name}"
                ),
                ("env", name),
            )
    for name in _SECRET_REF_ENVS:
        configured = str(environ.get(name) or "").strip()
        if not configured:
            continue
        reference = _reference_for(name, configured)
        if isinstance(reference, str):
            return (
                LegacyGitHubMapping("unresolvable", source=name, diagnostic=reference),
                None,
            )
        provider, key = reference
        return (
            LegacyGitHubMapping(
                "mapped", source=name, credential_ref=f"{provider}://{key}"
            ),
            reference,
        )
    active = set(active_slugs)
    for slug in _MANAGED_SECRET_SLUGS:
        if slug in active:
            return (
                LegacyGitHubMapping(
                    "mapped",
                    source=f"managed secret {slug}",
                    credential_ref=f"db://{slug}",
                ),
                ("db", slug),
            )
    return LegacyGitHubMapping("absent"), None


def _deployment_client_policy() -> dict[str, Any]:
    """Record the deployment's observed Git client when it can be observed."""

    try:
        from moonmind.workflows.temporal.runtime.launcher import (
            resolve_deployment_git_client_policy,
        )

        return resolve_deployment_git_client_policy().model_dump(
            by_alias=True, mode="json"
        )
    except Exception:
        # Launches overlay the worker's own client policy; this is a record.
        return {
            "pinnedVersion": "unobserved",
            "toolBundleRef": "repository-client:git-system",
            "executableSha256": "unobserved",
        }


def _credential_config(reference: tuple[str, str]) -> dict[str, Any]:
    provider, key = reference
    return {
        "source": "secret_ref",
        "credentialRef": {"provider": provider, "key": key, "extra": {}},
    }


def _insert_ignoring_conflict(
    bind, table, values: Mapping[str, Any], *keys: str
) -> None:
    dialect = bind.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        insert = None
    if insert is not None:
        bind.execute(insert(table).values(**values).on_conflict_do_nothing())
        return
    conditions = [table.c[key] == values[key] for key in keys]
    if bind.execute(sa.select(sa.literal(1)).where(*conditions)).first() is None:
        bind.execute(sa.insert(table).values(**values))


def migrate_legacy_github_connection(
    bind, environ: Mapping[str, str]
) -> LegacyGitHubMapping:
    """Record the effective legacy reference once; never overwrite newer work."""

    active_slugs = [
        row.slug
        for row in bind.execute(
            sa.select(_managed_secrets.c.slug).where(
                _managed_secrets.c.slug.in_(_MANAGED_SECRET_SLUGS),
                _managed_secrets.c.status == "active",
            )
        )
    ]
    mapping, reference = legacy_github_reference(environ, active_slugs)
    if reference is None:
        if mapping.outcome == "unresolvable":
            logger.warning(
                "Legacy GitHub credential was not mapped to %s; authenticated "
                "repository work on it stays unavailable until corrected: %s",
                DEFAULT_CONNECTION_REF,
                mapping.diagnostic,
            )
        return mapping

    credential = _credential_config(reference)
    existed = _stored_default(bind) is not None
    if not existed:
        _insert_ignoring_conflict(
            bind,
            _records,
            {
                "connection_id": DEFAULT_CONNECTION_REF,
                "display_name": "Default GitHub connection",
                "provider": "git",
                "hosting_service": "github",
                "endpoint_normalized": "https://github.com",
                "endpoint_ref": "https://github.com",
                "allowed_operations": [
                    "read",
                    "write",
                    "branch_write",
                    "review_request",
                ],
                "client_policy": _deployment_client_policy(),
                "credential_config": credential,
                "lifecycle": "active",
                "policy_revision": 1,
                "credential_revision": 1,
                "owner_ref": "system:deployment",
                "scope_type": "system",
                "scope_ref": None,
                "allowed_principal_refs": [],
                "tombstone": False,
            },
            "connection_id",
        )
    stored = _stored_default(bind)
    if stored is None or not _is_migrated_mapping(stored, credential):
        # The operator (or a concurrent writer) owns a newer connection, or
        # deleted it; that work wins over the legacy mapping.
        logger.info(
            "Kept the recorded %s; the legacy %s mapping (%s) was not applied",
            DEFAULT_CONNECTION_REF,
            mapping.source,
            mapping.credential_ref,
        )
        return LegacyGitHubMapping(
            "kept_existing",
            source=mapping.source,
            credential_ref=mapping.credential_ref,
        )

    # Reconcile a lost acknowledgment through the owner's audit identity
    # instead of repeating the mapping.
    _insert_ignoring_conflict(
        bind,
        _audit,
        {
            "id": uuid.uuid4(),
            "request_id": MIGRATION_REQUEST_ID,
            "actor_ref": MIGRATION_ACTOR_REF,
            "action": _CREATE_ACTION,
            "connection_id": DEFAULT_CONNECTION_REF,
            "scope_type": "system",
            "scope_ref": None,
            "policy_revision": 1,
            "detail_json": {
                "migration": revision,
                "legacySource": mapping.source,
                "credentialRef": mapping.credential_ref,
            },
        },
        "request_id",
        "action",
    )
    if existed:
        return LegacyGitHubMapping(
            "already_mapped",
            source=mapping.source,
            credential_ref=mapping.credential_ref,
        )
    logger.info(
        "Recorded %s from the legacy %s as %s",
        DEFAULT_CONNECTION_REF,
        mapping.source,
        mapping.credential_ref,
    )
    return mapping


def _stored_default(bind):
    return bind.execute(
        sa.select(
            _records.c.credential_config,
            _records.c.policy_revision,
            _records.c.credential_revision,
            _records.c.lifecycle,
            _records.c.tombstone,
        ).where(_records.c.connection_id == DEFAULT_CONNECTION_REF)
    ).first()


def _is_migrated_mapping(stored, credential: Mapping[str, Any] | None = None) -> bool:
    config = stored.credential_config
    if isinstance(config, str):
        config = json.loads(config)
    if credential is not None and dict(config or {}) != dict(credential):
        return False
    return (
        not stored.tombstone
        and stored.lifecycle == "active"
        and (stored.policy_revision, stored.credential_revision) == (1, 1)
    )


def remove_untouched_legacy_github_connection(bind) -> bool:
    """Remove the migrated row only while nothing newer depends on it."""

    stored = _stored_default(bind)
    if stored is None or not _is_migrated_mapping(stored):
        return False
    migrated = bind.execute(
        sa.select(_audit.c.detail_json).where(
            _audit.c.request_id == MIGRATION_REQUEST_ID,
            _audit.c.action == _CREATE_ACTION,
        )
    ).first()
    later_writes = bind.execute(
        sa.select(sa.func.count())
        .select_from(_audit)
        .where(
            _audit.c.connection_id == DEFAULT_CONNECTION_REF,
            _audit.c.request_id != MIGRATION_REQUEST_ID,
        )
    ).scalar_one()
    bound = any(
        bind.execute(
            sa.select(sa.literal(1)).where(
                table.c.connection_id == DEFAULT_CONNECTION_REF
            )
        ).first()
        is not None
        for table in (_assignments, _route_defaults)
    )
    if migrated is None or later_writes or bound:
        return False
    bind.execute(
        sa.delete(_records).where(_records.c.connection_id == DEFAULT_CONNECTION_REF)
    )
    return True


def upgrade() -> None:
    migrate_legacy_github_connection(op.get_bind(), os.environ)


def downgrade() -> None:
    # Forward repair: a connection with newer writes stays; the append-only
    # audit record stays so a re-upgrade reuses the same identity.
    if not remove_untouched_legacy_github_connection(op.get_bind()):
        logger.info(
            "Kept %s on downgrade because it is not the untouched migrated mapping",
            DEFAULT_CONNECTION_REF,
        )
