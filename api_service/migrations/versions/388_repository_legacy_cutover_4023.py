"""Legacy credential cutover marker (MoonLadderStudios/MoonMind#4023).

Revision ID: 388_repository_legacy_cutover_4023
Revises: 387_merge_386_heads_4461

One short, idempotent migration sequence reusing the existing #4005
owners. The transactional writer
(``api_service.services.repository_connections`` with expected-revision
conflicts and stable request-identity audit replay) already provides
the persistence, concurrency, and lost-acknowledgment semantics; this
revision therefore performs no schema change and creates no new
migration ledger/lease table. Ordinary post-migration startup consults
only the completed Alembic head, never a census of deleted credential
sources.
"""

from __future__ import annotations

from typing import Sequence, Union

__all__ = [
    "revision",
    "down_revision",
    "branch_labels",
    "depends_on",
    "upgrade",
    "downgrade",
]

revision: str = "388_repository_legacy_cutover_4023"
down_revision: Union[str, None, Sequence[str]] = "387_merge_386_heads_4461"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Reuses the #4005 repository-connection tables and the audit
    # (request_id, action) uniqueness constraint for idempotent cutover
    # mappings. No new tables, no ledger/lease, safe to rerun.
    pass


def downgrade() -> None:
    # Marker only: nothing to undo.
    pass
