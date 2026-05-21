"""Source mapping persistence tests for workflow execution terminology."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from api_service.db import models


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


def test_workflow_execution_source_mapping_uses_workflow_table_and_primary_key() -> None:
    mapping_cls = getattr(models, "WorkflowExecutionSourceMapping")

    assert mapping_cls.__tablename__ == "workflow_execution_source_mappings"
    assert "workflow_id" in mapping_cls.__table__.columns
    assert "task_id" not in mapping_cls.__table__.columns
    assert {column.name for column in mapping_cls.__table__.primary_key} == {
        "workflow_id"
    }
    assert {index.name for index in mapping_cls.__table__.indexes} == {
        "ix_workflow_execution_source_mappings_source_entry",
        "ix_workflow_execution_source_mappings_source_record_id",
    }


def test_task_source_mapping_compatibility_model_is_removed() -> None:
    assert not hasattr(models, "TaskSourceMapping")
    assert "task_source_mappings" not in models.Base.metadata.tables


@pytest.mark.parametrize("workflow_id", ["", "   ", None])
def test_workflow_execution_source_mapping_rejects_blank_workflow_id(workflow_id) -> None:
    mapping_cls = getattr(models, "WorkflowExecutionSourceMapping")

    with pytest.raises(ValueError, match="workflow_id"):
        mapping_cls(
            workflow_id=workflow_id,
            source="jira",
            entry="user_workflow",
            source_record_id="MM-728",
        )


def test_legacy_task_source_rows_convert_to_workflow_execution_rows() -> None:
    migration = _load_source_mapping_migration_module()

    rows = [
        {
            "task_id": " mm:wf-1 ",
            "source": "jira",
            "entry": "user_workflow",
            "source_record_id": "MM-728",
            "owner_type": "user",
            "owner_id": "user-1",
            "created_at": "created",
            "updated_at": "updated",
        }
    ]

    assert migration._prepare_legacy_source_mapping_rows(rows) == [
        {
            "workflow_id": "mm:wf-1",
            "source": "jira",
            "entry": "user_workflow",
            "source_record_id": "MM-728",
            "owner_type": "user",
            "owner_id": "user-1",
            "created_at": "created",
            "updated_at": "updated",
        }
    ]


@pytest.mark.parametrize("task_id", ["", "   ", None])
def test_legacy_task_source_rows_fail_for_blank_workflow_identity(task_id) -> None:
    migration = _load_source_mapping_migration_module()

    with pytest.raises(RuntimeError, match="task_source_mappings contains 1 row"):
        migration._prepare_legacy_source_mapping_rows(
            [
                {
                    "task_id": task_id,
                    "source": "jira",
                    "entry": "user_workflow",
                    "source_record_id": "MM-728",
                }
            ]
        )


def test_legacy_task_source_rows_fail_for_conflicting_duplicate_workflow_identity() -> None:
    migration = _load_source_mapping_migration_module()

    with pytest.raises(RuntimeError, match="duplicate workflow_id"):
        migration._prepare_legacy_source_mapping_rows(
            [
                {
                    "task_id": "mm:wf-1",
                    "source": "jira",
                    "entry": "user_workflow",
                    "source_record_id": "MM-728",
                },
                {
                    "task_id": "mm:wf-1",
                    "source": "github",
                    "entry": "user_workflow",
                    "source_record_id": "123",
                },
            ]
        )


def test_legacy_task_source_mapping_migration_drops_legacy_table_after_conversion() -> None:
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
                "(task_id, source, entry, source_record_id, owner_type, owner_id, created_at, updated_at) "
                "VALUES "
                "('mm:wf-1', 'jira', 'user_workflow', 'MM-728', 'user', 'user-1', 'created', 'updated')"
            )
        )
        migration._migrate_legacy_task_source_mappings(conn)

        inspector = sa.inspect(conn)
        assert "task_source_mappings" not in inspector.get_table_names()
        assert "workflow_execution_source_mappings" in inspector.get_table_names()
        converted = conn.execute(
            sa.text(
                "SELECT workflow_id, source, entry, source_record_id, owner_type, owner_id "
                "FROM workflow_execution_source_mappings"
            )
        ).mappings().all()

    assert converted == [
        {
            "workflow_id": "mm:wf-1",
            "source": "jira",
            "entry": "user_workflow",
            "source_record_id": "MM-728",
            "owner_type": "user",
            "owner_id": "user-1",
        }
    ]
