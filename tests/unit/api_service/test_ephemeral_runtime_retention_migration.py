"""Unit coverage for applying ephemeral retention to existing runtime evidence.

Runtime logs, diagnostics and Omnigent SSE journal prefixes became ephemeral
(7 days) for new artifacts, but rows written earlier kept the 30-day standard
class. Hundreds of thousands of superseded journal prefixes therefore held
the object store full. The migration reclassifies only rows whose every link
is runtime evidence; the lifecycle sweep still enforces pins, use claims and
active-journal protection before deleting anything.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from api_service.db.models import (
    TemporalArtifact,
    TemporalArtifactLink,
    TemporalArtifactRetentionClass,
    TemporalArtifactStatus,
)

MIGRATION_MODULE = (
    "api_service.migrations.versions.392_ephemeral_runtime_retention"
)
CREATED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _migration():
    return importlib.import_module(MIGRATION_MODULE)


def test_migration_chains_off_current_head() -> None:
    migration = _migration()
    assert migration.down_revision == "391_legacy_github_cred_4023"
    assert len(migration.revision) <= 32

    config = Config("api_service/migrations/alembic.ini")
    config.set_main_option("script_location", "api_service/migrations")
    script = ScriptDirectory.from_config(config)
    assert tuple(script.get_heads()) == (migration.revision,)


def _artifact(
    artifact_id: str,
    *,
    retention: TemporalArtifactRetentionClass = TemporalArtifactRetentionClass.STANDARD,
    status: TemporalArtifactStatus = TemporalArtifactStatus.COMPLETE,
) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "created_at": CREATED_AT,
        "storage_key": f"default/artifacts/{artifact_id}",
        "status": status,
        "retention_class": retention,
        "expires_at": (
            None
            if retention is TemporalArtifactRetentionClass.PINNED
            else CREATED_AT + timedelta(days=30)
        ),
        "metadata_json": {},
    }


def test_upgrade_reclassifies_only_rows_whose_links_are_all_runtime_evidence(
    monkeypatch,
) -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        TemporalArtifact.metadata.create_all(
            connection,
            tables=[TemporalArtifact.__table__, TemporalArtifactLink.__table__],
        )
        connection.execute(
            sa.insert(TemporalArtifact),
            [
                _artifact("art_journal"),
                _artifact("art_stdout"),
                _artifact("art_shared"),
                _artifact("art_unlinked"),
                _artifact("art_report"),
                _artifact("art_long", retention=TemporalArtifactRetentionClass.LONG),
                _artifact(
                    "art_pinned", retention=TemporalArtifactRetentionClass.PINNED
                ),
                _artifact("art_deleted", status=TemporalArtifactStatus.DELETED),
            ],
        )
        links = [
            ("art_journal", "runtime.omnigent.sse.normalized"),
            ("art_journal", "runtime.omnigent.sse.raw"),
            ("art_stdout", "runtime.stdout"),
            ("art_shared", "runtime.omnigent.sse.raw"),
            ("art_shared", "output.primary"),
            ("art_report", "report.primary"),
            ("art_long", "runtime.stdout"),
            ("art_pinned", "runtime.omnigent.sse.raw"),
            ("art_deleted", "runtime.omnigent.sse.raw"),
        ]
        for index, (artifact_id, link_type) in enumerate(links):
            connection.execute(
                sa.insert(TemporalArtifactLink).values(
                    id=uuid.UUID(int=index + 1),
                    artifact_id=artifact_id,
                    namespace="default",
                    workflow_id="mm:workflow",
                    run_id="run",
                    link_type=link_type,
                    created_at=CREATED_AT,
                )
            )

        migration = _migration()
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()

        rows = {
            row.artifact_id: row
            for row in connection.execute(
                sa.select(
                    TemporalArtifact.artifact_id,
                    TemporalArtifact.retention_class,
                    TemporalArtifact.expires_at,
                )
            )
        }

    for reclassified in ("art_journal", "art_stdout"):
        assert rows[reclassified].retention_class is (
            TemporalArtifactRetentionClass.EPHEMERAL
        )
        assert rows[reclassified].expires_at.replace(tzinfo=UTC) == (
            CREATED_AT + timedelta(days=7)
        )
    for kept in ("art_shared", "art_unlinked", "art_report", "art_deleted"):
        assert rows[kept].retention_class is TemporalArtifactRetentionClass.STANDARD
        assert rows[kept].expires_at.replace(tzinfo=UTC) == (
            CREATED_AT + timedelta(days=30)
        )
    assert rows["art_long"].retention_class is TemporalArtifactRetentionClass.LONG
    assert rows["art_pinned"].retention_class is TemporalArtifactRetentionClass.PINNED
    assert rows["art_pinned"].expires_at is None


def test_migration_policy_matches_runtime_retention_derivation() -> None:
    """The frozen link list must equal what new artifacts are assigned."""
    from moonmind.workflows.temporal.artifacts import _derive_retention

    for link_type in _migration().EPHEMERAL_LINK_TYPES:
        assert (
            _derive_retention(None, link_type)
            is TemporalArtifactRetentionClass.EPHEMERAL
        )
