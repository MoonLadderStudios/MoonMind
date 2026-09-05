"""Bind reusable execution behavior to the existing Profile identity.

Null preserves automatic resolution. Existing immutable consumer snapshots
remain authoritative; migration never guesses among customized combinations.
"""

import sqlalchemy as sa
from alembic import op

revision = "371_profile_execution_config"
down_revision = "370_provider_default_authority"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column(
            "execution_configuration",
            sa.JSON(),
            nullable=True,
        ),
    )
    profiles = sa.table(
        "managed_agent_provider_profiles",
        sa.column("profile_id", sa.String()),
        sa.column("auth_state", sa.String()),
        sa.column("command_behavior", sa.JSON()),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(profiles)).mappings():
        behavior = dict(row["command_behavior"] or {})
        readiness = dict(behavior.get("auth_readiness") or {})
        # One-time cutover of the two discovery-owned flags written by the
        # previous reconciler. Credential failures and explicit disables remain.
        if str(row["auth_state"]).lower() == "connected" and readiness.get(
            "failure_reason"
        ) in {
            "Pinned OpenCode runtime validation failed.",
            "The selected model was not observed by the pinned OpenCode runtime.",
        }:
            readiness.pop("failure_reason", None)
            readiness.pop("launch_ready", None)
            behavior["auth_readiness"] = readiness
            connection.execute(
                profiles.update()
                .where(
                    profiles.c.profile_id == row["profile_id"],
                )
                .values(command_behavior=behavior)
            )


def downgrade():
    op.drop_column("managed_agent_provider_profiles", "execution_configuration")
