"""The remediation link's target key follows the execution projection.

Schedule-started runs have only a ``temporal_executions`` projection row, so a
remediation link must reference that table to target them.
"""

from __future__ import annotations

import importlib
import io

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from api_service.db.models import TemporalExecutionRemediationLink

MIGRATION_MODULE = "api_service.migrations.versions.395_remediation_link_target"


def _postgres_sql(monkeypatch, step: str) -> str:
    migration = importlib.import_module(MIGRATION_MODULE)
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    monkeypatch.setattr(migration, "op", Operations(context))
    getattr(migration, step)()
    return " ".join(buffer.getvalue().split())


def test_migration_chains_to_current_head() -> None:
    migration = importlib.import_module(MIGRATION_MODULE)
    assert migration.down_revision == "394_default_conn_issues_grant"
    assert len(migration.revision) <= 32
    config = Config("api_service/migrations/alembic.ini")
    config.set_main_option("script_location", "api_service/migrations")
    script = ScriptDirectory.from_config(config)
    assert tuple(script.get_heads()) == ("396_artifact_operator_recovery",)
    assert script.get_revision("396_artifact_operator_recovery").down_revision == (
        migration.revision
    )


def test_upgrade_keys_the_target_to_the_projection_without_revalidating(
    monkeypatch,
) -> None:
    sql = _postgres_sql(monkeypatch, "upgrade")

    assert (
        "ALTER TABLE execution_remediation_links DROP CONSTRAINT "
        "execution_remediation_links_target_workflow_id_fkey"
    ) in sql
    assert (
        "FOREIGN KEY(target_workflow_id) REFERENCES temporal_executions "
        "(workflow_id) ON DELETE CASCADE NOT VALID"
    ) in sql


def test_downgrade_restores_the_source_key_without_deleting_links(
    monkeypatch,
) -> None:
    sql = _postgres_sql(monkeypatch, "downgrade")

    assert (
        "FOREIGN KEY(target_workflow_id) REFERENCES temporal_execution_sources "
        "(workflow_id) ON DELETE CASCADE NOT VALID"
    ) in sql
    assert "DELETE FROM" not in sql


def test_orm_link_target_references_the_projection() -> None:
    (foreign_key,) = TemporalExecutionRemediationLink.__table__.c[
        "target_workflow_id"
    ].foreign_keys
    assert foreign_key.target_fullname == "temporal_executions.workflow_id"
