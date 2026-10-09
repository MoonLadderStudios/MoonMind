"""Grant merge_request to the migrated default repository connection.

Revision 391 first recorded ``repository-connection:git-default`` with
``read``, ``write``, ``branch_write`` and ``review_request``. The legacy
deployment credential it maps also merged pull requests, and 391 was later
amended to grant ``merge_request`` too, but deployments that had already
applied it kept the four-operation row. Merging pr-resolver plans require
``merge_request`` on the collaboration slot, so on those deployments every
merging resolver launch failed with
``BOUND_DENIED: explicit connection does not admit merge_request``.

This revision adds ``merge_request`` only where the stale 391 mapping is
still in force: the row carries 391's ``connection.create`` audit identity,
is active, has never been updated (policy revision 1), and still allows
exactly the original four operations. An
assignment of that connection gains it only when it granted every one of
those four operations; an assignment the operator narrowed keeps its scope.
Connection and assignment revisions are unchanged because no admitted
snapshot relied on the missing operation, so in-flight runs do not go stale.

Downgrade keeps the grant: 391 already records it for new deployments.

Revision ID: 393_default_conn_merge_grant
Revises: 392_ephemeral_runtime_retention
"""

from __future__ import annotations

import json
import uuid
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "393_default_conn_merge_grant"
down_revision: Union[str, None] = "392_ephemeral_runtime_retention"
branch_labels = None
depends_on = None

DEFAULT_CONNECTION_REF = "repository-connection:git-default"
MIGRATION_REQUEST_ID = "migration:393:default-connection-merge-grant"
MIGRATION_ACTOR_REF = "system:migration-393"
# Frozen identity and operation set of revision 391's original mapping.
_MAPPING_REQUEST_ID = "migration:391:legacy-github-credential"
_PRE_MERGE_OPERATIONS = ("read", "write", "branch_write", "review_request")
_MERGE = "merge_request"

_records = sa.table(
    "repository_connection_records",
    sa.column("connection_id", sa.String),
    sa.column("allowed_operations", sa.JSON),
    sa.column("lifecycle", sa.String),
    sa.column("policy_revision", sa.Integer),
    sa.column("scope_type", sa.String),
    sa.column("scope_ref", sa.String),
    sa.column("tombstone", sa.Boolean),
)
_assignments = sa.table(
    "repository_connection_assignments",
    sa.column("id", sa.Uuid),
    sa.column("connection_id", sa.String),
    sa.column("operations", sa.JSON),
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


def _operations(value) -> list[str]:
    return list(json.loads(value) if isinstance(value, str) else value or [])


def grant_default_connection_merge(bind) -> bool:
    """Add merge_request to the stale 391 mapping; return whether it changed."""

    stored = bind.execute(
        sa.select(_records).where(_records.c.connection_id == DEFAULT_CONNECTION_REF)
    ).first()
    if (
        stored is None
        or stored.tombstone
        or stored.lifecycle != "active"
        # An operator update advances the policy revision; keeping the four
        # operations then is an explicit choice to leave merge disabled.
        or stored.policy_revision != 1
        or tuple(_operations(stored.allowed_operations)) != _PRE_MERGE_OPERATIONS
    ):
        return False
    mapped = bind.execute(
        sa.select(_audit.c.id).where(
            _audit.c.connection_id == DEFAULT_CONNECTION_REF,
            _audit.c.request_id == _MAPPING_REQUEST_ID,
            _audit.c.action == "connection.create",
        )
    ).first()
    if mapped is None:
        return False

    widened = []
    for assignment in bind.execute(
        sa.select(_assignments.c.id, _assignments.c.operations).where(
            _assignments.c.connection_id == DEFAULT_CONNECTION_REF
        )
    ).all():
        operations = _operations(assignment.operations)
        if _MERGE in operations or not set(_PRE_MERGE_OPERATIONS) <= set(operations):
            continue
        bind.execute(
            sa.update(_assignments)
            .where(_assignments.c.id == assignment.id)
            .values(operations=[*operations, _MERGE])
        )
        widened.append(str(assignment.id))
    bind.execute(
        sa.update(_records)
        .where(_records.c.connection_id == DEFAULT_CONNECTION_REF)
        .values(allowed_operations=[*_PRE_MERGE_OPERATIONS, _MERGE])
    )
    bind.execute(
        sa.insert(_audit).values(
            id=uuid.uuid4(),
            request_id=MIGRATION_REQUEST_ID,
            actor_ref=MIGRATION_ACTOR_REF,
            action="connection.update",
            connection_id=DEFAULT_CONNECTION_REF,
            scope_type=stored.scope_type,
            scope_ref=stored.scope_ref,
            policy_revision=stored.policy_revision,
            detail_json={
                "migration": revision,
                "grantedOperation": _MERGE,
                "widenedAssignments": widened,
            },
        )
    )
    return True


def upgrade() -> None:
    grant_default_connection_merge(op.get_bind())


def downgrade() -> None:
    pass
