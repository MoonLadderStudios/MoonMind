"""Integration checks for workflow execution source mapping persistence."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from api_service.db.models import Base, WorkflowExecutionSourceMapping

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _load_source_mapping_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "migrations"
        / "versions"
        / "312_workflow_execution_source_mapping_cutover.py"
    )
    spec = importlib.util.spec_from_file_location(
        "workflow_execution_source_mapping_cutover",
        migration_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metadata_contains_workflow_execution_source_mapping_only() -> None:
    assert "workflow_execution_source_mappings" in Base.metadata.tables
    assert "task_source_mappings" not in Base.metadata.tables


def test_workflow_execution_source_mapping_has_no_task_compatibility_columns() -> None:
    table = WorkflowExecutionSourceMapping.__table__

    assert table.name == "workflow_execution_source_mappings"
    assert set(table.columns.keys()) >= {
        "workflow_id",
        "source",
        "entry",
        "source_record_id",
        "owner_type",
        "owner_id",
        "created_at",
        "updated_at",
    }
    assert "task_id" not in table.columns


def test_legacy_task_source_mapping_cutover_converts_then_removes_legacy_table() -> None:
    migration = _load_source_mapping_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "task_source_mappings",
        metadata,
        sa.Column("task_id", sa.String(128), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("entry", sa.String(32), nullable=True),
        sa.Column("source_record_id", sa.String(128), nullable=False),
        sa.Column("owner_type", sa.String(32), nullable=True),
        sa.Column("owner_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=True),
        sa.Column("updated_at", sa.String(32), nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO task_source_mappings "
                "(task_id, source, entry, source_record_id) "
                "VALUES ('mm:wf-1', 'jira', 'user_workflow', 'MM-728')"
            )
        )

        migration._migrate_legacy_task_source_mappings(conn)

        inspector = sa.inspect(conn)
        assert "task_source_mappings" not in inspector.get_table_names()
        converted = conn.execute(
            sa.text(
                "SELECT workflow_id, source, entry, source_record_id "
                "FROM workflow_execution_source_mappings"
            )
        ).mappings().all()

    assert converted == [
        {
            "workflow_id": "mm:wf-1",
            "source": "jira",
            "entry": "user_workflow",
            "source_record_id": "MM-728",
        }
    ]
