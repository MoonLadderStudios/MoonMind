"""add provider profile runtime defaults

Revision ID: e6f7a8b9c0d1
Revises: d5c6f7a8b9c0
Create Date: 2026-04-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql import column, table


revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5c6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


__all__ = ["revision", "down_revision", "upgrade", "downgrade"]


profiles_table = table(
    "managed_agent_provider_profiles",
    column("profile_id", sa.String),
    column("runtime_id", sa.String),
    column("enabled", sa.Boolean),
    column("priority", sa.Integer),
    column("is_default", sa.Boolean),
)


def upgrade() -> None:
    op.add_column(
        "managed_agent_provider_profiles",
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.select(
                profiles_table.c.profile_id,
                profiles_table.c.runtime_id,
                profiles_table.c.enabled,
                profiles_table.c.priority,
            )
        )
    )

    runtime_rows: dict[str, list[sa.Row]] = {}
    for row in rows:
        runtime_rows.setdefault(str(row.runtime_id), []).append(row)

    for runtime_id, runtime_profiles in runtime_rows.items():
        runtime_profiles.sort(
            key=lambda row: (
                0 if row.enabled else 1,
                -(row.priority if row.priority is not None else 100),
                str(row.profile_id),
            ),
        )
        default_profile_id = runtime_profiles[0].profile_id
        bind.execute(
            profiles_table.update()
            .where(profiles_table.c.runtime_id == runtime_id)
            .values(is_default=False)
        )
        bind.execute(
            profiles_table.update()
            .where(profiles_table.c.profile_id == default_profile_id)
            .values(is_default=True)
        )

    op.create_index(
        "ux_provider_profiles_runtime_default",
        "managed_agent_provider_profiles",
        ["runtime_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
        sqlite_where=sa.text("is_default = 1"),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_provider_profiles_runtime_default",
        table_name="managed_agent_provider_profiles",
    )
    op.drop_column("managed_agent_provider_profiles", "is_default")
