"""Child delivery authority is canonical, not an authored parent hint."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api_service.api.routers.executions import _inherit_assessment_processing_for_child
from moonmind.workflows.executions.runtime_inheritance import ExecutionPrincipal
from tests.unit.services.test_assessment_model_delivery import source


@pytest.mark.asyncio
async def test_child_reads_verified_scheduled_source_not_projection_policy():
    admitted = source({"modelDelivery": "local_only"})
    projection = source({"modelDelivery": "allowed"})
    session = SimpleNamespace(get=AsyncMock(return_value=None))
    service = SimpleNamespace(
        describe_execution=AsyncMock(return_value=projection),
        read_scheduled_execution_source=AsyncMock(return_value=admitted),
    )
    payload = {"parentWorkflowId": "untrusted-hint"}
    await _inherit_assessment_processing_for_child(
        payload,
        principal=ExecutionPrincipal(workflow_id="source", run_id="source-run"),
        service=service,
        session=session,
    )
    assert payload["assessmentProject"] == admitted.parameters["assessmentProject"]
    service.describe_execution.assert_awaited_once_with("source")
    service.read_scheduled_execution_source.assert_awaited_once_with(projection)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "wrong-run", "invalid-policy"])
async def test_child_unavailable_source_never_becomes_unrestricted(fault):
    admitted = source({"allowedProfileRefs": ["profile-a"]})
    if fault == "wrong-run":
        admitted.run_id = "other-run"
    if fault == "invalid-policy":
        admitted.parameters["assessmentProject"]["configuration"][
            "evidenceProcessing"
        ] = {"modelDelivery": "synthetic-private-source"}
    session = SimpleNamespace(
        get=AsyncMock(return_value=None if fault == "missing" else admitted)
    )
    service = SimpleNamespace(
        describe_execution=AsyncMock(side_effect=ValueError("synthetic-private-source"))
    )
    payload = {}
    with pytest.raises(HTTPException) as caught:
        await _inherit_assessment_processing_for_child(
            payload,
            principal=ExecutionPrincipal(workflow_id="source", run_id="source-run"),
            service=service,
            session=session,
        )
    assert caught.value.status_code == 409
    assert "synthetic-private-source" not in str(caught.value.detail)
    assert caught.value.__suppress_context__
    assert not payload


@pytest.mark.asyncio
async def test_independent_operator_create_does_not_inherit_an_authored_parent():
    payload = {"parentWorkflowId": "untrusted-hint"}
    await _inherit_assessment_processing_for_child(
        payload,
        principal=ExecutionPrincipal(is_instance_operator=True),
        service=None,
        session=None,
    )
    assert payload == {"parentWorkflowId": "untrusted-hint"}
