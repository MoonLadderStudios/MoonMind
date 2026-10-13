"""Grant the issues collaboration operation to the publishing default connection.

Issue labels, comments and state now use the default repository connection
and require its ``issues`` operation instead of an ambient deployment token
(MoonLadderStudios/MoonMind#4003). Before this revision every publishing
default already performed issue collaboration through the deployment
credential, whether it is revision 391's mapping of that credential or a
default the operator edited or scoped in Source Control, and Source Control
publishing now includes ``issues``.

This revision therefore adds ``issues`` to the active
``repository-connection:git-default`` row when it grants publishing (``read``,
``write``, ``branch_write`` and ``review_request``), and to each of its
assignments that grants publishing. A default or assignment the operator kept
read-only or otherwise narrower keeps its scope, and other connections are
unchanged because issue collaboration uses only the default. A GitHub App
default keeps its scope too: its installation was enrolled for the operations
it already admits, so issue writes need an explicit permission upgrade rather
than an inferred grant. Connection and assignment revisions are unchanged
because no admitted snapshot relied on the missing operation, so in-flight runs
do not go stale.

Downgrade removes ``issues`` from every connection and assignment, because the
parent revision's application does not recognize that operation and could not
load a record that kept it.

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
# Source Control's publishing operations before issue collaboration had one.
_PUBLISH_OPERATIONS = frozenset({"read", "write", "branch_write", "review_request"})
_ISSUES = "issues"

_records = sa.table(
    "repository_connection_records",
    sa.column("connection_id", sa.String),
    sa.column("allowed_operations", sa.JSON),
    sa.column("credential_config", sa.JSON),
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


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _operations(value) -> list[str]:
    return list(_json(value) or [])


def _github_app(credential_config) -> bool:
    config = _json(credential_config)
    return isinstance(config, dict) and config.get("source") == "github_app"


def _grants_publishing(operations: list[str]) -> bool:
    return _ISSUES not in operations and _PUBLISH_OPERATIONS <= set(operations)


def grant_default_connection_issues(bind) -> bool:
    """Add issues to the publishing default; return whether it changed."""

    stored = bind.execute(
        sa.select(_records).where(_records.c.connection_id == DEFAULT_CONNECTION_REF)
    ).first()
    if (
        stored is None
        or stored.tombstone
        or stored.lifecycle != "active"
        or _github_app(stored.credential_config)
    ):
        return False
    operations = _operations(stored.allowed_operations)
    if not _grants_publishing(operations):
        return False

    widened = []
    for assignment in bind.execute(
        sa.select(_assignments.c.id, _assignments.c.operations).where(
            _assignments.c.connection_id == DEFAULT_CONNECTION_REF
        )
    ).all():
        assigned = _operations(assignment.operations)
        if not _grants_publishing(assigned):
            continue
        bind.execute(
            sa.update(_assignments)
            .where(_assignments.c.id == assignment.id)
            .values(operations=[*assigned, _ISSUES])
        )
        widened.append(str(assignment.id))
    bind.execute(
        sa.update(_records)
        .where(_records.c.connection_id == DEFAULT_CONNECTION_REF)
        .values(allowed_operations=[*operations, _ISSUES])
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


def revoke_issues_operation(bind) -> None:
    """Remove ``issues``, which the parent revision cannot load, everywhere."""

    for table, key, column in (
        (_records, _records.c.connection_id, _records.c.allowed_operations),
        (_assignments, _assignments.c.id, _assignments.c.operations),
    ):
        for row_key, value in bind.execute(sa.select(key, column)).all():
            operations = _operations(value)
            if _ISSUES in operations:
                bind.execute(
                    sa.update(table)
                    .where(key == row_key)
                    .values({column.name: [op for op in operations if op != _ISSUES]})
                )


def downgrade() -> None:
    revoke_issues_operation(op.get_bind())
