from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers import (
    execution_integrations as execution_integrations_router,
)
from api_service.auth_providers import get_current_user, get_current_user_optional
from api_service.db import base as db_base
from api_service.db.models import Base
from api_service.main import app
from moonmind.config.settings import settings
from moonmind.workflows.temporal.client import WorkflowStartResult

CURRENT_USER_DEP = get_current_user()
CURRENT_USER_OPTIONAL_DEP = get_current_user_optional()

# ---------------------------------------------------------------------------
# Module-scoped shared database fixture
# Creates the engine + schema ONCE per module instead of once per test.
# Each test uses a unique idempotency key to avoid cross-test data conflicts.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def _module_db(tmp_path_factory):
    """Create a single SQLite engine and schema for the entire module.

    Uses asyncio.run() because the project uses a custom pytest hook (not
    pytest-asyncio), which doesn't support module-scoped async fixtures.
    """
    import asyncio

    tmp = tmp_path_factory.mktemp("integration_db")
    db_url = f"sqlite+aiosqlite:///{tmp}/shared.db"

    async def _setup():
        engine = create_async_engine(db_url, future=True)
        session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, session_maker

    async def _teardown(engine):
        await engine.dispose()

    engine, session_maker = asyncio.run(_setup())

    _orig = (db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker)
    db_base.DATABASE_URL = db_url
    db_base.engine = engine
    db_base.async_session_maker = session_maker
    yield
    db_base.DATABASE_URL, db_base.engine, db_base.async_session_maker = _orig
    asyncio.run(_teardown(engine))

@pytest.fixture(autouse=True)
def _reset_dependency_overrides(_module_db):  # noqa: PT004 — depends on _module_db
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()

@pytest.fixture(autouse=True)
def _reset_callback_rate_limiter():
    execution_integrations_router._callback_rate_limiter = (
        execution_integrations_router._CallbackRateLimiter()
    )
    yield
    execution_integrations_router._callback_rate_limiter = (
        execution_integrations_router._CallbackRateLimiter()
    )

@pytest.fixture(autouse=True)
def _stub_temporal_adapter(monkeypatch: pytest.MonkeyPatch):
    # These callback/history tests stub the managed execution transport, not
    # Omnigent plan admission. Keep their fixture independent of the product
    # default and of runtime settings changed by other tests.
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")

    async def _start_workflow(self, **kwargs):
        workflow_id = str(kwargs["workflow_id"])
        return WorkflowStartResult(workflow_id=workflow_id, run_id=f"{workflow_id}-run")

    async def _describe_workflow(self, workflow_id: str):
        return None

    async def _signal_workflow(self, workflow_id: str, signal_name: str, arg=None):
        return None

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter.start_workflow",
        _start_workflow,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter.describe_workflow",
        _describe_workflow,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter.signal_workflow",
        _signal_workflow,
    )

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _create_monitored_execution(
    client: AsyncClient,
    *,
    execution_suffix: str = "default",
    integration_name: str = "jules",
) -> tuple[str, str]:
    create_response = await client.post(
        "/api/executions",
        json={
            "workflowType": "MoonMind.UserWorkflow",
            "title": "Integration callback test",
            "planArtifactRef": f"execution-integrations-plan-{execution_suffix}",
            "idempotencyKey": f"execution-integrations-create-{execution_suffix}",
        },
    )
    assert create_response.status_code == 201
    workflow_id = create_response.json()["workflowId"]

    configure_response = await client.post(
        f"/api/executions/{workflow_id}/integration",
        json={
            "integrationName": integration_name,
            "externalOperationId": f"task-{execution_suffix}",
            "normalizedStatus": "running",
            "providerStatus": "running",
            "callbackSupported": True,
        },
    )
    assert configure_response.status_code == 202
    callback_key = configure_response.json()["integration"]["callbackCorrelationKey"]
    return workflow_id, callback_key

def _shared_client(shared_user_id):
    """Return an async ASGI context manager with auth override applied."""
    user = SimpleNamespace(id=shared_user_id, is_superuser=False)
    # Create/execution routes resolve through the optional boundary; the
    # strict boundary covers the rest. Override both explicitly (#4125).
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: user
    app.dependency_overrides[CURRENT_USER_OPTIONAL_DEP] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_callback_discovery_describes_generic_contract():
    shared_user_id = uuid4()

    async with _shared_client(shared_user_id) as client:
        response = await client.get("/api/integrations/callbacks")

    assert response.status_code == 200
    payload = response.json()
    assert (
        payload["endpointTemplate"]
        == "/api/integrations/{integrationName}/callbacks/{callbackCorrelationKey}"
    )
    assert payload["payloadSchema"] == "IntegrationCallbackRequest"
    assert payload["defaults"]["maxPayloadBytes"] > 0

@pytest.mark.asyncio
async def test_callback_rejects_missing_configured_token(monkeypatch):
    shared_user_id = uuid4()
    monkeypatch.setattr(settings.jules, "jules_callback_token", "callback-secret")

    async with _shared_client(shared_user_id) as client:
        _workflow_id, callback_key = await _create_monitored_execution(
            client, execution_suffix=f"missing-token-{uuid4().hex[:8]}"
        )
        response = await client.post(
            f"/api/integrations/jules/callbacks/{callback_key}",
            json={
                "eventType": "completed",
                "normalizedStatus": "completed",
            },
        )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "integration_callback_unauthorized"

@pytest.mark.asyncio
async def test_callback_accepts_matching_bearer_token(monkeypatch):
    shared_user_id = uuid4()
    monkeypatch.setattr(settings.jules, "jules_callback_token", "callback-secret")

    async with _shared_client(shared_user_id) as client:
        _workflow_id, callback_key = await _create_monitored_execution(
            client, execution_suffix=f"auth-{uuid4().hex[:8]}"
        )
        response = await client.post(
            f"/api/integrations/jules/callbacks/{callback_key}",
            headers={"Authorization": "Bearer callback-secret"},
            json={
                "eventType": "completed",
                "providerEventId": "evt-1",
                "normalizedStatus": "completed",
            },
        )

    assert response.status_code == 202
    assert response.json()["integration"]["normalizedStatus"] == "completed"


@pytest.mark.asyncio
async def test_generic_callback_profile_accepts_matching_bearer_token(monkeypatch):
    shared_user_id = uuid4()
    monkeypatch.setattr(settings.integration_callbacks, "callback_token", "generic-secret")

    async with _shared_client(shared_user_id) as client:
        _workflow_id, callback_key = await _create_monitored_execution(
            client,
            execution_suffix=f"generic-auth-{uuid4().hex[:8]}",
            integration_name="openclaw",
        )
        unauthorized = await client.post(
            f"/api/integrations/openclaw/callbacks/{callback_key}",
            json={
                "eventType": "completed",
                "normalizedStatus": "completed",
            },
        )
        accepted = await client.post(
            f"/api/integrations/openclaw/callbacks/{callback_key}",
            headers={"Authorization": "Bearer generic-secret"},
            json={
                "eventType": "completed",
                "providerEventId": "evt-generic",
                "normalizedStatus": "completed",
            },
        )

    assert unauthorized.status_code == 401
    assert unauthorized.json()["detail"]["code"] == "integration_callback_unauthorized"
    assert accepted.status_code == 202
    assert accepted.json()["integration"]["integrationName"] == "openclaw"
    assert accepted.json()["integration"]["normalizedStatus"] == "completed"


@pytest.mark.asyncio
async def test_callback_rejects_payload_over_limit(monkeypatch):
    shared_user_id = uuid4()
    monkeypatch.setattr(settings.jules, "jules_callback_token", None)
    monkeypatch.setattr(settings.jules, "jules_callback_max_payload_bytes", 32)

    async with _shared_client(shared_user_id) as client:
        _workflow_id, callback_key = await _create_monitored_execution(
            client, execution_suffix=f"size-{uuid4().hex[:8]}"
        )
        response = await client.post(
            f"/api/integrations/jules/callbacks/{callback_key}",
            json={
                "eventType": "completed",
                "providerSummary": {"message": "x" * 128},
            },
        )

    assert response.status_code == 413
    assert (
        response.json()["detail"]["code"]
        == "integration_callback_payload_too_large"
    )

@pytest.mark.asyncio
async def test_callback_rejects_rate_limited_bursts(monkeypatch):
    shared_user_id = uuid4()
    monkeypatch.setattr(settings.jules, "jules_callback_token", None)
    monkeypatch.setattr(settings.jules, "jules_callback_rate_limit_per_window", 1)
    monkeypatch.setattr(settings.jules, "jules_callback_rate_limit_window_seconds", 60)

    async with _shared_client(shared_user_id) as client:
        _workflow_id, callback_key = await _create_monitored_execution(
            client, execution_suffix=f"rate-limit-{uuid4().hex[:8]}"
        )
        first = await client.post(
            f"/api/integrations/jules/callbacks/{callback_key}",
            json={
                "eventType": "progress",
                "providerEventId": "evt-rate-1",
                "normalizedStatus": "running",
            },
        )
        second = await client.post(
            f"/api/integrations/jules/callbacks/{callback_key}",
            json={
                "eventType": "progress",
                "providerEventId": "evt-rate-2",
                "normalizedStatus": "running",
            },
        )

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "integration_callback_rate_limited"

@pytest.mark.asyncio
async def test_callback_rate_limit_applies_across_callback_keys(monkeypatch):
    shared_user_id = uuid4()
    monkeypatch.setattr(settings.jules, "jules_callback_token", None)
    monkeypatch.setattr(settings.jules, "jules_callback_rate_limit_per_window", 1)
    monkeypatch.setattr(settings.jules, "jules_callback_rate_limit_window_seconds", 60)

    async with _shared_client(shared_user_id) as client:
        suffix = uuid4().hex[:8]
        _workflow_id, callback_key_one = await _create_monitored_execution(
            client,
            execution_suffix=f"shared-one-{suffix}",
        )
        _workflow_id, callback_key_two = await _create_monitored_execution(
            client,
            execution_suffix=f"shared-two-{suffix}",
        )
        first = await client.post(
            f"/api/integrations/jules/callbacks/{callback_key_one}",
            json={
                "eventType": "progress",
                "providerEventId": "evt-shared-1",
                "normalizedStatus": "running",
            },
        )
        second = await client.post(
            f"/api/integrations/jules/callbacks/{callback_key_two}",
            json={
                "eventType": "progress",
                "providerEventId": "evt-shared-2",
                "normalizedStatus": "running",
            },
        )

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "integration_callback_rate_limited"

def test_callback_rate_limiter_evicts_idle_buckets(monkeypatch):
    limiter = execution_integrations_router._CallbackRateLimiter()
    ticks = iter((10.0, 11.0, 80.0))
    monkeypatch.setattr(
        execution_integrations_router.time,
        "monotonic",
        lambda: next(ticks),
    )

    assert limiter.allow(key="jules:callback-1", limit=1, window_seconds=30) is True
    assert limiter.allow(key="jules:callback-2", limit=1, window_seconds=30) is True
    assert set(limiter._buckets) == {"jules:callback-1", "jules:callback-2"}

    assert limiter.allow(key="jules:callbacks", limit=1, window_seconds=30) is True
    assert set(limiter._buckets) == {"jules:callbacks"}

@pytest.mark.asyncio
async def test_callback_can_capture_raw_payload_artifact(tmp_path, monkeypatch):
    shared_user_id = uuid4()
    monkeypatch.setattr(settings.jules, "jules_callback_token", None)
    monkeypatch.setattr(settings.jules, "jules_callback_artifact_capture_enabled", True)
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow,
        "temporal_artifact_root",
        str(tmp_path / "artifacts"),
    )

    async with _shared_client(shared_user_id) as client:
        _workflow_id, callback_key = await _create_monitored_execution(
            client, execution_suffix=f"artifact-{uuid4().hex[:8]}"
        )
        response = await client.post(
            f"/api/integrations/jules/callbacks/{callback_key}",
            json={
                "eventType": "completed",
                "providerEventId": "evt-artifact",
                "normalizedStatus": "completed",
            },
        )

    assert response.status_code == 202
    assert any(
        str(ref).startswith("art_")
        for ref in response.json().get("artifactRefs", [])
    )


@pytest.mark.asyncio
async def test_removed_integration_binding_is_rejected_without_signalling(monkeypatch):
    """MoonLadderStudios/MoonMind#4644: a generic binding cannot re-enable Cloud."""
    signals = []

    async def signal(*args, **kwargs):
        signals.append((args, kwargs))

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter.signal_workflow",
        signal,
    )
    async with _shared_client(uuid4()) as client:
        created = await client.post(
            "/api/executions",
            json={
                "workflowType": "MoonMind.UserWorkflow",
                "title": "Preserve",
                "planArtifactRef": "old-cloud-plan",
                "idempotencyKey": uuid4().hex,
            },
        )
        assert created.status_code == 201
        bound = await client.post(
            f"/api/executions/{created.json()['workflowId']}/integration",
            json={
                "integrationName": "codex_cloud",
                "externalOperationId": "old-operation",
                "normalizedStatus": "running",
                "callbackSupported": True,
            },
        )
        assert bound.status_code == 422
        assert "unsupported" in bound.text.lower()
        assert signals == []


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [False, True])
async def test_historical_removed_integration_keeps_reads_but_cannot_advance(
    _module_db, monkeypatch, terminal, tmp_path
):
    from sqlalchemy import select
    from api_service.db.models import (
        TemporalExecutionRecord,
        TemporalExecutionCanonicalRecord,
        TemporalIntegrationCorrelationRecord,
    )
    from moonmind.workflows.temporal.service import MoonMindWorkflowState

    signals = []

    async def signal(*args, **kwargs):
        signals.append((args, kwargs))

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter.signal_workflow",
        signal,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter.update_workflow",
        signal,
    )
    monkeypatch.setattr(settings.integration_callbacks, "callback_token", None)
    monkeypatch.setattr(
        settings.integration_callbacks, "artifact_capture_enabled", False
    )
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "artifacts")
    )
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))
    async with _shared_client(uuid4()) as client:
        from api_service.api.routers.executions import get_temporal_client

        class HistoricalClient:
            def get_workflow_handle(self, workflow_id, **kwargs):
                raise RuntimeError("Fixture record no longer has a live provider")

        app.dependency_overrides[get_temporal_client] = HistoricalClient
        execution_suffix = uuid4().hex
        workflow_id, key = await _create_monitored_execution(
            client, execution_suffix=execution_suffix, integration_name="openclaw"
        )
        async with db_base.async_session_maker() as session:
            row = await session.scalar(
                select(TemporalExecutionRecord).where(
                    TemporalExecutionRecord.workflow_id == workflow_id
                )
            )
            snapshot = dict(row.integration_state)
            snapshot["integration_name"] = "codex_cloud"
            snapshot["result_refs"] = ["saved-result-original"]
            row.integration_state = snapshot
            canonical = await session.scalar(
                select(TemporalExecutionCanonicalRecord).where(
                    TemporalExecutionCanonicalRecord.workflow_id == workflow_id
                )
            )
            canonical.integration_state = dict(snapshot)
            if terminal:
                canonical.state = MoonMindWorkflowState.COMPLETED
            if terminal:
                row.state = MoonMindWorkflowState.COMPLETED
            correlation = await session.scalar(
                select(TemporalIntegrationCorrelationRecord).where(
                    TemporalIntegrationCorrelationRecord.workflow_id == workflow_id
                )
            )
            correlation.integration_name = "codex_cloud"
            await session.commit()
        read = await client.get(f"/api/executions/{workflow_id}")
        assert read.status_code == 200
        assert read.json()["integration"]["integrationName"] == "codex_cloud"
        assert read.json()["integration"]["resultRefs"] == ["saved-result-original"]
        signals.clear()
        existing = await client.post(
            "/api/executions",
            json={
                "workflowType": "MoonMind.UserWorkflow",
                "planArtifactRef": "old-plan",
                "idempotencyKey": f"execution-integrations-create-{execution_suffix}",
                "initialParameters": {"targetRuntime": "codex_cloud"},
            },
        )
        assert existing.status_code == 201, existing.text
        assert existing.json()["workflowId"] == workflow_id
        assert existing.json()["integration"] == read.json()["integration"]
        assert signals == []
        if not terminal:
            resumed = await client.post(
                f"/api/executions/{workflow_id}/signal",
                json={"signalName": "Resume", "payload": {}},
            )
            assert resumed.status_code == 409
            assert "unsupported integration" in resumed.text.lower()
            assert signals == []
        rerun = await client.post(
            f"/api/executions/{workflow_id}/update",
            json={"updateName": "RequestRerun"},
        )
        assert rerun.status_code == 422, rerun.text
        assert "unsupported integration" in rerun.text.lower()
        assert signals == []
        late = await client.post(
            f"/api/integrations/codex_cloud/callbacks/{key}",
            json={"eventType": "completed", "normalizedStatus": "completed"},
        )
        assert late.status_code == (202 if terminal else 409)
        assert signals == []
        after = await client.get(f"/api/executions/{workflow_id}")
        assert after.json()["integration"] == read.json()["integration"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_payload",
    [
        {
            "type": "workflow",
            "payload": {
                "workflow": {
                    "instructions": "Keep task",
                    "runtime": {
                        "mode": "codex_cloud",
                        "model": "saved-model",
                        "effort": "high",
                    },
                }
            },
        },
        {
            "type": "workflow",
            "payload": {
                "targetRuntime": "codex_cloud",
                "workflow": {
                    "instructions": "Keep task",
                    "runtime": {"mode": "codex_cli"},
                },
            },
        },
        {
            "workflowType": "MoonMind.UserWorkflow",
            "planArtifactRef": "saved-plan",
            "initialParameters": {
                "workflow": {
                    "instructions": "Keep task",
                    "steps": [
                        {
                            "runtime": {"mode": "codex_cloud"},
                            "instructions": "Keep this step",
                        }
                    ],
                }
            },
        },
    ],
)
async def test_removed_execution_targets_reject_before_temporal_start(
    monkeypatch, request_payload
):
    """#4644: stale canonical and legacy API envelopes never launch new work."""
    calls = []

    async def start(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Unsupported target reached Temporal start")

    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter.start_workflow", start
    )
    async with _shared_client(uuid4()) as client:
        response = await client.post(
            "/api/executions", json={**request_payload, "idempotencyKey": uuid4().hex}
        )
    assert response.status_code == 422, response.text
    assert "must be one of" in response.text.lower()
    assert calls == []
