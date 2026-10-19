"""Add protected historical artifact recovery disposition.

Schema installation grants no access. The guarded conversion transaction sets
this field only after retained human attribution is proven.
"""
from alembic import op
import sqlalchemy as sa

revision = "396_artifact_operator_recovery"
down_revision = "395_remediation_link_target"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("temporal_artifacts", sa.Column(
        "operator_recovery_granted", sa.Boolean(), nullable=False,
        server_default=sa.false(),
    ))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Serialize the check and DDL with a converter publishing a grant.
        bind.execute(sa.text("LOCK TABLE temporal_artifacts IN ACCESS EXCLUSIVE MODE"))
    remaining = bind.execute(sa.text(
        "SELECT COUNT(*) FROM temporal_artifacts WHERE operator_recovery_granted"
    )).scalar()
    if remaining:
        raise RuntimeError(
            "Cannot discard published artifact recovery grants; repair forward "
            "so retained restricted data stays recoverable."
        )
    op.drop_column("temporal_artifacts", "operator_recovery_granted")
