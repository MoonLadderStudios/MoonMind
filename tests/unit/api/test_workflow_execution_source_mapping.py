"""Source mapping persistence tests for workflow execution terminology."""

from __future__ import annotations

import pytest

from api_service.db import models


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
