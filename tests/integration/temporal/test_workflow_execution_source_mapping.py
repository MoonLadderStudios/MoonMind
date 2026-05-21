"""Integration checks for workflow execution source mapping persistence."""

from __future__ import annotations

import pytest

from api_service.db.models import Base, WorkflowExecutionSourceMapping

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


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
