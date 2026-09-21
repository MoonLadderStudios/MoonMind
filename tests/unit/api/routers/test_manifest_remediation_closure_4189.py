"""Closure matrix for MoonLadderStudios/MoonMind#4189 verifier gaps.

Covers the repo-executable deltas still open at verifier HEAD ``49b39e6c``:

- REQ-01/REQ-02 + ACC-02: artifact layer stays inert/bounded for historical
  ``MoonMind.ManifestIngest`` rows (report hydration never contacts the
  artifact service, compiler, parser, readers, or a live host), and the UI
  response path (action capabilities) exposes a safe read-only generic with
  no synthesized system ownership.
- REQ-03 + ACC-03: rerun of an owned ManifestIngest source fails fast with
  an actionable 422 before any canonical load or launch side effect, and the
  retired update-name matrix rejects over HTTP (server authoritative over a
  cached UI).

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
    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalWorkflowType,
    )

    owner = str(uuid4())
    now = datetime.now(UTC)
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
        created_at=now,
        started_at=now,
        updated_at=now,
        closed_at=now,
    )


def _override_user(app: FastAPI, *, user_id, is_superuser: bool):
    from api_service import auth_providers as auth_mod

    user = SimpleNamespace(
        id=user_id,
        email="closure-4189@example.com",
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
# REQ-01/REQ-02 + ACC-02: artifact layer inert + UI read-only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manifest_artifact_report_hydration_is_inert() -> None:
    """Report hydration never contacts artifact storage for manifest rows."""
    from api_service.api.routers.executions import (
        _hydrate_execution_report_projection,
        _serialize_execution,
    )

    record = _manifest_row()
    execution = _serialize_execution(record)
    assert execution.entry == "manifest"
    # Identity/refs preserved without running the compiler or parser.
    assert execution.workflow_id == record.workflow_id
    assert execution.run_id == record.run_id

    hydrated = await _hydrate_execution_report_projection(
        execution, session=AsyncMock(), user=SimpleNamespace(id=record.owner_id)
    )
    # Early return path for non-user_workflow entries: same object, no reads.
    assert hydrated is execution


def test_manifest_ui_capabilities_are_read_only_without_system_synthesis() -> None:
    """UI action flags stay read-only; ownership is never synthesized."""
    from api_service.api.routers.executions import (
        _build_action_capabilities,
        _serialize_execution,
    )

    record = _manifest_row()
    capabilities = _build_action_capabilities(record)
    assert capabilities.can_rerun is False
    assert capabilities.can_edit_for_rerun is False
    assert capabilities.can_update_inputs is False
    assert capabilities.can_failed_step_resume is False
    assert capabilities.disabled_reasons.get("canRerun") == "unsupported_workflow_type"

    model = _serialize_execution(record)
    assert model.owner_id == record.owner_id
    assert model.owner_type == "user"
    assert model.actions.can_rerun is False


def test_manifest_unknown_state_degrades_to_read_only() -> None:
    """Blank/unknown lifecycle values degrade; unavailable stays unavailable."""
    from api_service.api.routers.executions import _build_action_capabilities

    record = _manifest_row()
    record.state = SimpleNamespace(value="some_future_state")
    record.search_attributes = dict(record.search_attributes, mm_state="some_future_state")
    capabilities = _build_action_capabilities(record)
    assert capabilities.can_rerun is False
    assert capabilities.can_edit_for_rerun is False
    assert capabilities.can_update_inputs is False


# ---------------------------------------------------------------------------
# REQ-03 + ACC-03: rerun fail-fast + retired update HTTP matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_owned_manifest_source_rejected_before_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owned ManifestIngest rerun 422s before canonical load or launch."""
    from api_service.api.routers.executions import _get_service, router
    from api_service.db.base import get_async_session
    from moonmind.config.settings import settings

    app = FastAPI()
    app.include_router(router)
    record = _manifest_row()
    service = AsyncMock()
    service.describe_execution.return_value = record
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    _override_user(app, user_id=record.owner_id, is_superuser=True)
    session = AsyncMock()
    app.dependency_overrides[get_async_session] = lambda: session
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(f"/api/executions/{record.workflow_id}/rerun")

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "manifest_retired"
    session.get.assert_not_awaited()
    service.create_execution.assert_not_awaited()


@pytest.mark.parametrize(
    "update_name",
    ["UpdateManifest", "SetConcurrency", "CancelNodes", "RetryNodes"],
    ids=["update", "concurrency", "cancel_nodes", "retry_nodes"],
)
def test_retired_update_names_rejected_over_http(
    update_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP update path is server-authoritative over a stale cached UI."""
    from api_service.api.routers.executions import _get_service, router
    from api_service.db.base import get_async_session
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal.service import TemporalExecutionValidationError

    app = FastAPI()
    app.include_router(router)
    record = _manifest_row()
    service = AsyncMock()
    service.describe_execution.return_value = record

    async def _reject_update(**kwargs):
        raise TemporalExecutionValidationError(
            f"Update {kwargs.get('update_name')} was retired with "
            "MoonMind.ManifestIngest (MoonLadderStudios/MoonMind#4192) "
            "and is no longer supported."
        )

    service.update_execution.side_effect = _reject_update
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    _override_user(app, user_id=record.owner_id, is_superuser=True)
    app.dependency_overrides[get_async_session] = lambda: AsyncMock()
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            f"/api/executions/{record.workflow_id}/update",
            json={"updateName": update_name},
        )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_update_request"
