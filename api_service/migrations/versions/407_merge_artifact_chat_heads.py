"""Join verified artifact dependencies and Omnigent chat ownership.

Preserve both revision identities so installations on either branch can
upgrade without replaying or discarding the other branch's migration.
"""

revision = "407_merge_artifact_chat_heads"
down_revision = (
    "406_artifact_dependencies",
    "406_omnigent_chat_ownership",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
