"""Join the published pending-intent and conversion-lookup branches.

Keep both parents so databases that have applied either index migration can
converge without rewriting their recorded migration history.
"""

revision = "404_pending_lookup_merge"
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
