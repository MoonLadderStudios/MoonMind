"""Rename claude_default profile_id to claude_anthropic.

This migration renames the well-known Anthropic Claude Code auth profile.
The new name makes it unambiguous from other claude_code profiles (e.g.
claude_minimax) that may coexist under the same runtime family.
"""

from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE managed_agent_auth_profiles
        SET profile_id = 'claude_anthropic'
        WHERE profile_id = 'claude_default'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE managed_agent_auth_profiles
        SET profile_id = 'claude_default'
        WHERE profile_id = 'claude_anthropic'
        """
    )
