"""Remediation matrix for MoonLadderStudios/MoonMind#4189 verifier gaps.

Covers the still-open repo-executable deltas named in the authoritative
verifier report at ``af44c37`` without duplicating the landed serializer,
boundary, drain, and gap-closure suites:

- REQ-01/REQ-02 + ACC-02: arbitrary unknown workflow types stay
  non-executable through the real list/detail serializers and the
  registration/projection boundary, with no synthesized system ownership.
- REQ-03 + ACC-03: the full retired update-name matrix
  (``UpdateManifest``/``SetConcurrency``/``CancelNodes``/``RetryNodes``)
  rejects fail-before-effects at the service boundary, and rerun rejects
  missing/wrong-owner sources before any launch side effect.
- REQ-01 history layer: UserWorkflow-specific step-ledger reads reject a
  historical ``MoonMind.ManifestIngest`` row actionably over HTTP instead
  of reinterpreting it or contacting Temporal.
- REQ-07 + ACC-04: the versioned drain gate retains the old release while
  restart (open histories), retry (pending tasks), or cancel/schedule
  (existing schedules) work remains, and releases only on a fully
  observed zero count.

Hermetic: no Temporal server, database, deployment probe, compiler, or
retired parser is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _manifest_row() -> SimpleNamespace:
    """Historical old-release ManifestIngest row for HTTP-boundary tests."""
    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalWorkflowType,
    )

    owner = str(uuid4())
    return SimpleNamespace(
        namespace="moonmind",
        workflow_id=f"mm:historical-manifest:{uuid4().hex[:8]}",
        run_id=f"run-historical-{uuid4().hex[:8]}",
        workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
        owner_id=owner,
        owner_type=SimpleNamespace(value="user"),
        state=MoonMindWorkflowState.COMPLETED,
        close_status=None,
        search_attributes={
            "mm_owner_id": owner,
            "mm_owner_type": "user",
            "mm_entry": "manifest",
            "mm_state": "completed",
        },
        memo={"title": "Historical manifest ingest"},
        artifact_refs=["art_historical_1"],
        finish_outcome_code=None,
        finish_summary_json=None,
        input_ref=None,
        plan_ref="art_plan_historical",
        manifest_ref="artifact://manifest/historical",
        parameters={"task": {"instructions": "historical manifest work"}},
        entry="manifest",
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        closed_at=datetime.now(UTC),
    )


def _unknown_type_row() -> SimpleNamespace:
    """Row carrying an arbitrary unknown workflow type (never executable)."""
    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalExecutionCloseStatus,
    )

    now = datetime.now(UTC)
    owner = "user-123"
    return SimpleNamespace(
        namespace="moonmind",
        workflow_id="mm:unknown-type:4189",
        run_id="run-unknown-1",
        workflow_type=SimpleNamespace(value="MoonMind.SomethingElse"),
        owner_id=owner,
        owner_type=SimpleNamespace(value="user"),
        state=MoonMindWorkflowState.COMPLETED,
        close_status=TemporalExecutionCloseStatus.COMPLETED,
        search_attributes={
            "mm_owner_id": owner,
            "mm_owner_type": "user",
            "mm_state": "completed",
        },
        memo={"title": "Unknown type row"},
        artifact_refs=[],
        finish_outcome_code=None,
        finish_summary_json=None,
        input_ref=None,
        plan_ref=None,
        manifest_ref=None,
        parameters={},
        entry=None,
        paused=False,
        waiting_reason=None,
        attention_required=False,
        created_at=now,
        started_at=now,
        updated_at=now,
        closed_at=now,
        integration_state=None,
    )


def _override_user(app: FastAPI, *, user_id, is_superuser: bool):
    """Override the resolved strict principal dependency.

    Routes capture ``Depends(get_current_user())`` at import time, so the
    override must target the resolved dependency callable (the shared
    ``_strict_current_user`` in non-OIDC/header modes), not the factory.
    """
    from api_service import auth_providers as auth_mod

    user = SimpleNamespace(
        id=user_id,
        email="remediation-4189@example.com",
        is_active=True,
        is_superuser=is_superuser,
        roles=[],
    )
    app.dependency_overrides[auth_mod._strict_current_user] = lambda: user
    app.dependency_overrides[auth_mod._optional_current_user] = lambda: user
    return user


def _override_temporal_client(app: FastAPI) -> None:
    from api_service.api.routers.executions import get_temporal_client

    app.dependency_overrides[get_temporal_client] = lambda: SimpleNamespace()


# ---------------------------------------------------------------------------
# REQ-01/REQ-02 + ACC-02: unknown types stay closed, no system ownership
# ---------------------------------------------------------------------------


def test_unknown_workflow_type_resolves_to_non_executable_entry() -> None:
    """Arbitrary unknown types never resolve to the historical manifest entry."""
    from api_service.api.routers.executions import _resolve_execution_entry

    record = _unknown_type_row()
    assert _resolve_execution_entry(record, {}) == "user_workflow"
    assert (
        _resolve_execution_entry(_manifest_row(), {}) == "manifest"
    )


def test_product_projection_rejects_retired_and_unknown_types() -> None:
    """Neither the retired type nor arbitrary unknown types are launchable."""
    from moonmind.workflows.temporal.workflow_registry import (
        WorkflowProjectionExcluded,
        product_workflow_types,
        require_product_projection,
    )

    assert product_workflow_types() == ("MoonMind.UserWorkflow",)
    with pytest.raises(WorkflowProjectionExcluded):
        require_product_projection("MoonMind.ManifestIngest")
    with pytest.raises(WorkflowProjectionExcluded):
        require_product_projection("MoonMind.SomethingElse")


def test_unknown_type_serializers_preserve_owner_without_system_synthesis() -> None:
    """Unknown-type rows render generically; ownership is never synthesized."""
    from api_service.api.routers.executions import (
        _serialize_execution,
        _serialize_execution_list_item,
    )

    record = _unknown_type_row()
    item = _serialize_execution_list_item(record)
    assert item.workflow_id == "mm:unknown-type:4189"
    assert item.entry == "user_workflow"
    assert item.owner_id == "user-123"
    assert item.owner_type == "user"

    model = _serialize_execution(record)
    assert model.entry == "user_workflow"
    assert model.owner_id == "user-123"
    assert model.owner_type == "user"
    assert model.actions.can_rerun is False
    assert model.actions.can_edit_for_rerun is False
    assert model.actions.can_update_inputs is False


# ---------------------------------------------------------------------------
# REQ-03 + ACC-03: retired update-name matrix fails before effects
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update_name",
    ["UpdateManifest", "SetConcurrency", "CancelNodes", "RetryNodes"],
    ids=["update", "concurrency", "cancel_nodes", "retry_nodes"],
)
@pytest.mark.asyncio
async def test_retired_update_names_rejected_before_source_load(
    update_name: str,
) -> None:
    """Each retired node/update name rejects before any store side effect."""
    from moonmind.workflows.temporal.service import (
        TemporalExecutionService,
        TemporalExecutionValidationError,
    )
    from moonmind.workflows.temporal.service import RETIRED_MANIFEST_UPDATE_NAMES

    assert update_name in RETIRED_MANIFEST_UPDATE_NAMES
    session = AsyncMock()
    service = TemporalExecutionService(session)
    with pytest.raises(TemporalExecutionValidationError, match="was retired"):
        await service.update_execution(
            workflow_id=f"mm:historical-manifest:{uuid4().hex[:8]}",
            update_name=update_name,
        )
    session.execute.assert_not_awaited()
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_rerun_missing_manifest_source_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rerun of a missing workflow 404s before any launch side effect."""
    from api_service.api.routers.executions import _get_service, router
    from api_service.db.base import get_async_session
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal.service import TemporalExecutionNotFoundError

    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    service.describe_execution.side_effect = TemporalExecutionNotFoundError(
        "Workflow execution mm:missing was not found"
    )
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    _override_user(app, user_id=uuid4(), is_superuser=False)
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/executions/mm:missing/rerun")

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "execution_not_found"
    service.create_execution.assert_not_awaited()


@pytest.mark.asyncio
async def test_rerun_wrong_owner_manifest_source_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller who does not own the ManifestIngest row cannot rerun it."""
    from api_service.api.routers.executions import _get_service, router
    from api_service.db.base import get_async_session
    from moonmind.config.settings import settings

    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    service.describe_execution.return_value = _manifest_row()
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    _override_user(app, user_id=uuid4(), is_superuser=False)
    session = AsyncMock()
    session.get.return_value = None
    app.dependency_overrides[get_async_session] = lambda: session
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/executions/mm:historical-manifest:x/rerun")

    assert response.status_code == 404, response.text
    service.create_execution.assert_not_awaited()


# ---------------------------------------------------------------------------
# REQ-01 history layer: step-ledger reads reject ManifestIngest actionably
# ---------------------------------------------------------------------------


def test_manifest_step_ledger_read_rejected_without_temporal_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """History scoped to UserWorkflow never reinterprets a ManifestIngest row."""
    from api_service.api.routers.executions import _get_service, router
    from api_service.db.base import get_async_session
    from moonmind.config.settings import settings

    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    record = _manifest_row()
    service.describe_execution = AsyncMock(return_value=record)
    app.dependency_overrides[_get_service] = lambda: service
    temporal_client = SimpleNamespace()
    from api_service.api.routers.executions import get_temporal_client

    app.dependency_overrides[get_temporal_client] = lambda: temporal_client
    _override_user(app, user_id=record.owner_id, is_superuser=True)
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/api/executions/{record.workflow_id}/steps/logical-1/step-executions"
        )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_execution_query"


# ---------------------------------------------------------------------------
# REQ-07 + ACC-04: drain gate retains while restart/retry/cancel work remains
# ---------------------------------------------------------------------------


def test_drain_observations_retain_while_work_remains() -> None:
    """Open histories, pending tasks, or schedules each block removal."""
    from moonmind.gates.manifest_ingest_drain import (
        collect_manifest_ingest_drain_observations,
        evaluate_manifest_ingest_drain_observations,
    )

    for observations in (
        # Restart scope: an open ManifestIngest history still needs drain.
        collect_manifest_ingest_drain_observations(
            open_manifest_ingest_histories=1,
            pending_manifest_tasks=0,
            existing_manifest_schedules=0,
        ),
        # Retry scope: pending manifest activities still need drain.
        collect_manifest_ingest_drain_observations(
            open_manifest_ingest_histories=0,
            pending_manifest_tasks=2,
            existing_manifest_schedules=0,
        ),
        # Cancel/schedule scope: an enabled ManifestIngest-targeted
        # schedule still needs drain.
        collect_manifest_ingest_drain_observations(
            open_manifest_ingest_histories=0,
            pending_manifest_tasks=0,
            existing_manifest_schedules=1,
        ),
    ):
        decision = evaluate_manifest_ingest_drain_observations(observations)
        assert decision.may_deploy_removal is False
        assert decision.required_action == "retain_and_drain"

    drained = evaluate_manifest_ingest_drain_observations(
        collect_manifest_ingest_drain_observations(
            open_manifest_ingest_histories=0,
            pending_manifest_tasks=0,
            existing_manifest_schedules=0,
        )
    )
    assert drained.may_deploy_removal is True
    assert drained.required_action == "safe_to_remove"


def test_drain_observations_unobservable_stays_unknown_not_zero() -> None:
    """An unreachable probe dimension retains the old release (never zero)."""
    from moonmind.gates.manifest_ingest_drain import (
        collect_manifest_ingest_drain_observations,
        evaluate_manifest_ingest_drain_observations,
    )

    decision = evaluate_manifest_ingest_drain_observations(
        collect_manifest_ingest_drain_observations(
            open_manifest_ingest_histories=None,
            pending_manifest_tasks=0,
            existing_manifest_schedules=0,
        )
    )
    assert decision.may_deploy_removal is False
    assert "open_manifest_ingest_histories" in decision.blocking_dimensions
