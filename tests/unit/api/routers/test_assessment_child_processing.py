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
async def test_child_recovered_source_must_match_caller_workflow():
    admitted = source({"modelDelivery": "allowed"})
    admitted.workflow_id = "unrelated-workflow"
    service = SimpleNamespace(
        describe_execution=AsyncMock(return_value=source()),
        read_scheduled_execution_source=AsyncMock(return_value=admitted),
    )
    payload = {}
    with pytest.raises(HTTPException) as raised:
        await _inherit_assessment_processing_for_child(
            payload,
            principal=ExecutionPrincipal(workflow_id="source", run_id="source-run"),
            service=service,
            session=SimpleNamespace(get=AsyncMock(return_value=None)),
        )
    assert raised.value.status_code == 409
    assert payload == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "wrong-run", "invalid-policy"])
async def test_child_unavailable_source_never_becomes_unrestricted(monkeypatch, fault):
    admitted = source({"allowedProfileRefs": ["profile-a"]})
    if fault == "wrong-run":
        from moonmind.workflows.temporal.client import TemporalClientAdapter

        monkeypatch.setattr(
            TemporalClientAdapter,
            "verify_workflow_continuation",
            AsyncMock(side_effect=ValueError("unrelated run")),
        )
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


@pytest.mark.asyncio
async def test_child_continuation_keeps_original_processing_and_scope(monkeypatch):
    from tests.unit.workflows.temporal.test_assessment_processing_continuation import (
        continuation_history,
    )

    admitted = source({"allowedProfileRefs": ["approved-profile"]})
    continuation_history(monkeypatch)
    payload = {
        "parentWorkflowId": "untrusted-hint",
        "workflow": {"runtime": {"providerProfileRef": "other-profile"}},
    }
    await _inherit_assessment_processing_for_child(
        payload,
        principal=ExecutionPrincipal(workflow_id="source", run_id="current-run"),
        session=SimpleNamespace(get=AsyncMock(return_value=admitted)),
        service=None,
    )
    assert payload["assessmentProject"] == admitted.parameters["assessmentProject"]
    assert admitted.run_id == "source-run"
    from api_service.services.assessment_model_delivery import (
        require_execution_model_delivery,
    )
    from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError

    with pytest.raises(TemporalArtifactAuthorizationError):
        require_execution_model_delivery(payload, profile_ref="other-profile")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["wrong-successor", "failed", "temporary"])
async def test_child_unverified_continuation_preserves_payload(monkeypatch, change):
    from tests.unit.workflows.temporal.test_assessment_processing_continuation import (
        continuation_history,
    )

    continuation_history(monkeypatch, change)
    payload = {}
    with pytest.raises(HTTPException) as raised:
        await _inherit_assessment_processing_for_child(
            payload,
            principal=ExecutionPrincipal(workflow_id="source", run_id="current-run"),
            session=SimpleNamespace(
                get=AsyncMock(return_value=source({"modelDelivery": "disabled"}))
            ),
            service=None,
        )
    assert raised.value.status_code == (503 if change == "temporary" else 409)
    assert "private-transport-canary" not in str(raised.value.detail)
    assert payload == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "principal_run", [None, "continued-run"], ids=["scoped", "exact-run"]
)
async def test_scheduled_child_preserves_original_policy_after_continuation(
    monkeypatch, principal_run
):
    from tests.unit.workflows.temporal.test_assessment_processing_continuation import (
        scheduled_history,
    )

    workflow_id, admitted, reads = await scheduled_history(monkeypatch)
    projection = SimpleNamespace(run_id="original-run", parameters={})
    service = SimpleNamespace(
        describe_execution=AsyncMock(return_value=projection),
        read_scheduled_execution_source=AsyncMock(),
    )
    payload = {}
    await _inherit_assessment_processing_for_child(
        payload,
        principal=ExecutionPrincipal(workflow_id=workflow_id, run_id=principal_run),
        session=SimpleNamespace(get=AsyncMock(return_value=None)),
        service=service,
    )
    assert payload["assessmentProject"] == admitted["assessmentProject"]
    assert reads == (
        ["original-run"] if principal_run is None else ["continued-run", "original-run"]
    )
    service.read_scheduled_execution_source.assert_not_awaited()
