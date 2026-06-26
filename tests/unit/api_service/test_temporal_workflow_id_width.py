from __future__ import annotations

from api_service.db.models import (
    TemporalExecutionCanonicalRecord,
    TemporalExecutionDependency,
    TemporalExecutionRecord,
    TemporalIntegrationCorrelationRecord,
)

def test_temporal_projection_workflow_ids_accept_child_workflow_ids() -> None:
    assert TemporalExecutionCanonicalRecord.__table__.c.workflow_id.type.length == 255
    assert TemporalExecutionRecord.__table__.c.workflow_id.type.length == 255
    assert (
        TemporalExecutionDependency.__table__.c.dependent_workflow_id.type.length == 255
    )
    assert (
        TemporalExecutionDependency.__table__.c.prerequisite_workflow_id.type.length
        == 255
    )
    assert (
        TemporalIntegrationCorrelationRecord.__table__.c.workflow_id.type.length == 255
    )
