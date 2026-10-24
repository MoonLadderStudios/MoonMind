"""Index live recovery admission and bridge journal reference ownership.

Only indexes change; retained owners, recovery grants and journal references
remain untouched. The owner classification is frozen here so future policy
changes cannot silently change what an already-installed index represents.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.elements import Grouping

revision = "401_artifact_recovery_indexes"
down_revision = "400_job_operator_access"
branch_labels = None
depends_on = None


def _recovery_owner_class():
    whitespace = (
        "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
        "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
        "\u2028\u2029\u202f\u205f\u3000"
    )

    def stripped(value):
        return sa.func.ltrim(sa.func.rtrim(value, whitespace), whitespace)

    creator = sa.func.coalesce(sa.column("created_by_principal"), "")
    normalized = stripped(creator)
    machine = sa.or_(
        normalized.in_(("operator", "system", "agent_workflow", "agent_runtime")),
        *(
            sa.and_(
                sa.func.substr(normalized, 1, len(prefix)) == prefix,
                stripped(sa.func.substr(normalized, len(prefix) + 1)) != "",
            )
            for prefix in ("service:", "workflow:", "system:")
        ),
    )
    return Grouping(
        sa.case(
            (machine, 0),
            (
                creator.in_(
                    (
                        "00000000-0000-0000-0000-000000000000",
                        "user:00000000-0000-0000-0000-000000000000",
                    )
                ),
                1,
            ),
            else_=2,
        )
    )


def upgrade() -> None:
    op.create_index(
        "ix_temporal_artifacts_recovery_owner",
        "temporal_artifacts",
        [_recovery_owner_class()],
        postgresql_where=sa.text("operator_recovery_granted IS NOT TRUE"),
        sqlite_where=sa.text("operator_recovery_granted IS NOT 1"),
    )
    op.create_index(
        "ix_omnigent_bridge_session_events_artifact_ref",
        "omnigent_bridge_session_events",
        ["artifact_ref"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_omnigent_bridge_session_events_artifact_ref",
        table_name="omnigent_bridge_session_events",
    )
    op.drop_index(
        "ix_temporal_artifacts_recovery_owner", table_name="temporal_artifacts"
    )
