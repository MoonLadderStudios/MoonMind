"""Grant the issues collaboration operation to the migrated default connection.

Issue labels, comments and state now use the default repository connection
and require its ``issues`` operation instead of an ambient deployment token
(MoonLadderStudios/MoonMind#4003). Revision 391 mapped the deployment's
pre-#4023 credential to ``repository-connection:git-default``, and that
credential already performed issue collaboration, so the unedited mapping
gains ``issues``.

This revision adds ``issues`` only where 391's mapping is still in force: the
row carries 391's ``connection.create`` audit identity, is active, has never
been updated (policy revision 1), and allows exactly 391's operations (with
or without the ``merge_request`` grant of revision 393). An assignment of
that connection gains it only when it granted every one of those
operations; an assignment the operator narrowed keeps its scope. Connection
and assignment revisions are unchanged because no admitted snapshot relied on
the missing operation, so in-flight runs do not go stale.

Downgrade keeps the grant: revision 391's credential already had this use.

Revision ID: 394_default_conn_issues_grant
Revises: 393_default_conn_merge_grant
"""

from __future__ import annotations

import json
import uuid
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "394_default_conn_issues_grant"
down_revision: Union[str, None] = "393_default_conn_merge_grant"
branch_labels = None
depends_on = None

DEFAULT_CONNECTION_REF = "repository-connection:git-default"
MIGRATION_REQUEST_ID = "migration:394:default-connection-issues-grant"
MIGRATION_ACTOR_REF = "system:migration-394"
# Frozen identity and operation sets of revision 391's mapping.
_MAPPING_REQUEST_ID = "migration:391:legacy-github-credential"
_MAPPED_OPERATION_SETS = (
    ("read", "write", "branch_write", "review_request"),
    ("read", "write", "branch_write", "review_request", "merge_request"),
)
_ISSUES = "issues"

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


def grant_default_connection_issues(bind) -> bool:
    """Add issues to the unedited 391 mapping; return whether it changed."""

    stored = bind.execute(
        sa.select(_records).where(_records.c.connection_id == DEFAULT_CONNECTION_REF)
    ).first()
    if stored is None or stored.tombstone or stored.lifecycle != "active":
        return False
    mapped_operations = tuple(_operations(stored.allowed_operations))
    # An operator update advances the policy revision; its operations are an
    # explicit choice, not the mapping.
    if stored.policy_revision != 1 or mapped_operations not in _MAPPED_OPERATION_SETS:
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
        if _ISSUES in operations or not set(mapped_operations) <= set(operations):
            continue
        bind.execute(
            sa.update(_assignments)
            .where(_assignments.c.id == assignment.id)
            .values(operations=[*operations, _ISSUES])
        )
        widened.append(str(assignment.id))
    bind.execute(
        sa.update(_records)
        .where(_records.c.connection_id == DEFAULT_CONNECTION_REF)
        .values(allowed_operations=[*mapped_operations, _ISSUES])
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
                "grantedOperation": _ISSUES,
                "widenedAssignments": widened,
            },
        )
    )
    return True


def upgrade() -> None:
    grant_default_connection_issues(op.get_bind())


def downgrade() -> None:
    pass
