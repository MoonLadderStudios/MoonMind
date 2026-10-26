"""Join the independent delivery-intent and conversion-lookup index branches.

Both revisions are published, so retain their upgrade paths without changing
either index or rewriting migration history.
"""

revision = "404_merge_index_heads"
down_revision = (
    "402_pending_execution_intents",
    "403_conversion_completion_lookup",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
