"""Persist producer-verified artifact closures and current schedule root."""

import sqlalchemy as sa
from alembic import op

revision = "406_artifact_dependencies"
down_revision = "405_recurring_owner_terminal"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "temporal_artifacts",
        sa.Column("dependency_request_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "temporal_artifacts",
        sa.Column("dependency_closure_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "temporal_artifacts",
        sa.Column("dependency_closure_sha256", sa.String(64), nullable=True),
    )
    op.create_table(
        "temporal_artifact_dependencies",
        sa.Column(
            "root_artifact_id",
            sa.String(64),
            sa.ForeignKey("temporal_artifacts.artifact_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("dependency_artifact_id", sa.String(64), primary_key=True),
        sa.Column("role", sa.String(64), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_artifact_dependencies_required_child",
        "temporal_artifact_dependencies",
        ["dependency_artifact_id", "required"],
    )
    with op.batch_alter_table("recurring_workflow_definitions") as batch:
        batch.add_column(
            sa.Column("input_artifact_root_id", sa.String(64), nullable=True)
        )
        batch.create_foreign_key(
            "fk_recurring_input_artifact_root",
            "temporal_artifacts",
            ["input_artifact_root_id"],
            ["artifact_id"],
            ondelete="RESTRICT",
        )
        batch.create_index(
            "ix_recurring_workflow_definitions_input_artifact_root_id",
            ["input_artifact_root_id"],
        )


def downgrade():
    with op.batch_alter_table("recurring_workflow_definitions") as batch:
        batch.drop_index("ix_recurring_workflow_definitions_input_artifact_root_id")
        batch.drop_constraint("fk_recurring_input_artifact_root", type_="foreignkey")
        batch.drop_column("input_artifact_root_id")
    op.drop_table("temporal_artifact_dependencies")
    op.drop_column("temporal_artifacts", "dependency_closure_sha256")
    op.drop_column("temporal_artifacts", "dependency_closure_version")
    op.drop_column("temporal_artifacts", "dependency_request_sha256")
