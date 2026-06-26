"""Remove active agent skill semantic version storage for MM-901.

Revision ID: 329_mm901_name_only_agent_skills
Revises: 328_strip_preset_step_versions
Create Date: 2026-06-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "329_mm901_name_only_agent_skills"
down_revision: Union[str, None] = "328_strip_preset_step_versions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_skill_definitions",
        sa.Column(
            "format",
            sa.Enum("markdown", "bundle", name="agentskillformat"),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_skill_definitions",
        sa.Column("artifact_ref", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "agent_skill_definitions",
        sa.Column("content_digest", sa.String(length=128), nullable=True),
    )

    op.execute(
        """
        update agent_skill_definitions d
           set format = v.format,
               artifact_ref = v.artifact_ref,
               content_digest = v.content_digest
         from agent_skill_versions v
         where v.skill_id = d.id
           and v.id = (
               select v2.id
                 from agent_skill_versions v2
                where v2.skill_id = d.id
                order by v2.created_at desc, v2.id desc
                limit 1
           )
        """
    )
    op.execute("update agent_skill_definitions set format = 'markdown' where format is null")

    with op.batch_alter_table("agent_skill_definitions") as batch_op:
        batch_op.alter_column(
            "format",
            existing_type=sa.Enum("markdown", "bundle", name="agentskillformat"),
            nullable=False,
            server_default="markdown",
        )

    with op.batch_alter_table("skill_set_entries") as batch_op:
        batch_op.drop_column("version_constraint")

    op.drop_index("ix_agent_skill_versions_skill", table_name="agent_skill_versions")
    op.drop_table("agent_skill_versions")


def downgrade() -> None:
    raise RuntimeError(
        "MM-901 removes active agent skill semantic versions; downgrade is unsupported."
    )
